"""False-pass and override-lifecycle regressions from PR #745 review."""

import json

import pytest

from estleg import check_text_fidelity as ctf
from estleg import heuristic_overrides as ho


@pytest.mark.parametrize(("left", "right"), [
    ("Vt § 7².", "Vt § 72."),
    ("1) nimi; 2) aadress", "2) nimi; 1) aadress"),
])
def test_fidelity_preserves_legally_significant_numbers(left, right):
    assert ctf.normalise_text(left) != ctf.normalise_text(right)


@pytest.mark.parametrize("content", [None, "not json", "{}"])
def test_coverage_cannot_pass_missing_or_unreadable_inputs(tmp_path, content):
    if content is not None:
        (tmp_path / "law_peep.json").write_text(content)
    baseline = tmp_path / "baseline.json"
    ctf.write_baseline({key: [] for key in ctf.COVERAGE_RULES}, baseline)
    assert ctf.run_coverage(tmp_path, baseline) == ctf.EXIT_FAIL


def test_update_baseline_cannot_accept_new_defects(tmp_path):
    baseline = tmp_path / "baseline.json"
    ctf.write_baseline({key: [] for key in ctf.COVERAGE_RULES}, baseline)
    before = baseline.read_bytes()
    (tmp_path / "law_peep.json").write_text(json.dumps({"@graph": [
        {"@id": "estleg:X_Map", "@type": ["estleg:Law", "estleg:Act"]},
    ]}))
    assert ctf.run_coverage(tmp_path, baseline, update=True) == ctf.EXIT_FAIL
    assert baseline.read_bytes() == before


def test_wrong_source_redaction_cannot_pass_fidelity():
    record = ctf.ProvisionRecord("estleg:X_Par_1", "§ 1", "x", "1", "x_peep.json")
    report = ctf.SampleReport([ctf.SampleResult(record, ctf.STALE_SOURCE_ID)])
    assert report.exit_code == ctf.EXIT_FAIL


def test_explicit_missing_override_store_is_an_error(tmp_path):
    with pytest.raises(ho.OverrideError, match="missing"):
        ho.load_overrides(tmp_path / "missing.jsonl")


def test_target_classifier_removes_stale_human_confidence_off_provision_path(tmp_path):
    from estleg.classify_target_group import classify_files
    path = tmp_path / "law.json"
    path.write_text(json.dumps({"@graph": [{"@id": "estleg:X_Map",
        "prov:wasAttributedTo": "Reviewer",
        "estleg:assertionConfidence": {"@value": "1.0", "@type": "xsd:decimal"},
    }]}))
    classify_files([path], report_path=tmp_path / "report.json", overrides=ho.OverrideStore())
    node = json.loads(path.read_text())["@graph"][0]
    assert "estleg:assertionConfidence" not in node


def test_court_partial_resolution_preserves_unresolved_sections():
    from estleg.extract_court_provision_links import resolve_citations
    missing = []
    found = resolve_citations(
        [{"law_ref": "KarS", "paragraphs": ["1", "999"], "citationText": "KarS §§ 1, 999"}],
        {"KarS": {"K"}}, {"K": {"1": "estleg:K_Par_1"}}, unresolved=missing,
    )
    assert found == ["estleg:K_Par_1"]
    assert len(missing) == 1
    assert missing[0]["paragraphs"] == ["999"]


def test_court_cleanup_does_not_delete_unrelated_targetless_citation():
    from estleg.extract_court_provision_links import is_court_pass_unresolved_citation
    node = {"@id": "estleg:Citation_Curated_1", "@type": "estleg:Citation",
            "estleg:citationText": "Reviewed reference"}
    assert not is_court_pass_unresolved_citation(node)


def test_unresolved_court_citation_does_not_reuse_existing_id():
    from estleg.extract_court_provision_links import build_unresolved_court_citations
    from estleg.extract_cross_references import build_citation_iri
    taken = {build_citation_iri("estleg:RK_1", 1)}
    [node] = build_unresolved_court_citations("estleg:RK_1", [
        {"law_ref": "KarS", "citationText": "KarS § 999"},
    ], taken)
    assert node["@id"] == build_citation_iri("estleg:RK_1", 2)
