import json

import pytest

from estleg import fetch_eurovoc_official as eurovoc
from estleg.extract_institutional_competence import apply_institution_identity, bind_institutions
from estleg.heuristic_overrides import parse_overrides_text
from estleg import serialize_tabular as tabular


@pytest.mark.parametrize("action", ["set", "remove"])
def test_official_refresh_applies_current_human_override(tmp_path, action):
    path = tmp_path / "eurlex_directives_peep.json"
    node = {"@id": "estleg:EU_C1", "@type": ["estleg:EULegislation"],
            "estleg:celexNumber": "C1"}
    eurovoc.stamp_official_subjects(node, ["10"])
    path.write_text(json.dumps({"@context": {}, "@graph": [node]}))
    record = {"node": node["@id"], "predicate": "dcterms:subject", "action": action,
              "reviewer": "Reviewer", "date": "2026-10-09", "basis": "Reviewed source"}
    if action == "set":
        record["value"] = [{"@id": "http://eurovoc.europa.eu/20"}]
    store = parse_overrides_text(json.dumps(record))
    eurovoc.stamp_eurlex_peeps({"C1": ["10"]}, tmp_path, overrides=store)
    result = json.loads(path.read_text())["@graph"][0]
    assert result.get("dcterms:subject", []) == record.get("value", [])
    assert result.get("eli:is_about", []) == record.get("value", [])
    assert result["prov:wasAttributedTo"] == "Reviewer"
    assert "estleg:subjectSource" not in result
    eurovoc.stamp_eurlex_peeps({"C1": ["10"]}, tmp_path,
                             overrides=parse_overrides_text(""))
    result = json.loads(path.read_text())["@graph"][0]
    assert result["dcterms:subject"] == [{"@id": "http://eurovoc.europa.eu/10"}]
    assert "prov:wasAttributedTo" not in result
    assert result.get("estleg:assertionConfidence", {}).get("@value") != "1.0"


@pytest.mark.parametrize("ids", [["20"], []])
def test_official_refresh_preserves_subjects_from_other_vocabularies(ids):
    external = {"@id": "https://example.org/subject"}
    node = {"dcterms:subject": [external, {"@id": "http://eurovoc.europa.eu/10"}],
            "eli:is_about": [external, {"@id": "http://eurovoc.europa.eu/10"}],
            "estleg:subjectSource": "cellar"}
    eurovoc.stamp_official_subjects(node, ids)
    assert node["dcterms:subject"] == [external] + eurovoc.official_subject_refs(ids)
    assert node["eli:is_about"] == node["dcterms:subject"]


@pytest.mark.parametrize("text,expected", [
    ("Terviseamet kontrollib nõudeid ja Keskkonnaamet annab loa.",
     {"terviseamet": "enforcement", "keskkonnaamet": "licensing"}),
    ("Keskkonnaamet annab loa ja Terviseamet kontrollib nõudeid.",
     {"terviseamet": "enforcement", "keskkonnaamet": "licensing"}),
    ("Linnavolikogu kehtestab korra ja Terviseamet kontrollib nõudeid.",
     {"linnavolikogu": "regulation", "terviseamet": "enforcement"}),
    ("Terviseamet teostab järelevalvet ja Riigikohus otsustab vaidluse.",
     {"terviseamet": "supervision", "riigikohus": "general"}),
    ("Keskkonnaamet kontrollib nõudeid ja annab loa.", {"keskkonnaamet": "licensing"}),
    ("Keskkonnaamet ja Terviseamet kontrollivad nõudeid ja annavad loa.",
     {"keskkonnaamet": "licensing", "terviseamet": "licensing"}),
])
def test_competence_types_do_not_cross_adjacent_authorities(text, expected):
    assert {b.suffix: b.competence_type for b in bind_institutions(text)} == expected


def test_identity_refresh_preserves_unrelated_see_also():
    node = {"rdfs:seeAlso": [{"@id": "https://example.org/registry"},
                              {"@id": "http://www.wikidata.org/entity/Q1"}]}
    apply_institution_identity(node, "test", identity={}, wd_map={})
    assert node["rdfs:seeAlso"] == {"@id": "https://example.org/registry"}


@pytest.mark.parametrize("content", [None, "{bad", '{"@graph": null}',
                                      "version https://git-lfs.github.com/spec/v1\n"])
def test_kov_export_rejects_incomplete_inputs_before_overwriting(tmp_path, content):
    if content is not None:
        (tmp_path / "act_peep.json").write_text(content)
    out = tmp_path / "export"
    out.mkdir()
    previous = out / "kov_legality.csv"
    previous.write_text("previous export")
    with pytest.raises(ValueError):
        tabular.serialize_kov_legality(
            krr_dir=tmp_path, out_dir=out, kov_globs=["*_peep.json"],
            versions=object(), context=tabular.KovContext({}, {}, {}))
    assert previous.read_text() == "previous export"
