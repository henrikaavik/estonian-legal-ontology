"""Offline sub-corpus aggregate rebuild (eurlex / curia / eelnoud).

Regression for the drift where ``*_combined.jsonld`` lost its schema graph
(eurlex: 24 missing schema IDs; curia: stale ``curiaLink``; eelnoud: missing
``Phase_*`` / ``enactedAs`` and drifting ``@type``) because the only producers
were the network-fetching generator mains.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import rebuild_subcorpus_combined as rsc
from estleg import run_all_integration
from estleg.validate_all import SUBCORPUS_COMBINED_TARGETS

REPO_ROOT = Path(__file__).resolve().parents[1]
CTX = {"estleg": "https://w3id.org/estleg/", "owl": "http://www.w3.org/2002/07/owl#"}


def _write(path: Path, graph: list[dict], context: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"@context": context or CTX, "@graph": graph}), encoding="utf-8"
    )


@pytest.fixture
def eurlex_tree(tmp_path: Path) -> Path:
    _write(
        tmp_path / "eurlex" / "eurlex_schema.json",
        [
            {"@id": "estleg:EURlex_Schema_2026", "@type": ["owl:Ontology"]},
            {"@id": "estleg:EULegislation", "@type": ["owl:Class"]},
            {"@id": "estleg:EUDocType_Directive", "@type": ["owl:NamedIndividual"]},
        ],
    )
    # Written out of name order on purpose: output follows sorted filenames.
    _write(
        tmp_path / "eurlex" / "eurlex_regulations_peep.json",
        [
            {"@id": "estleg:EURlex_Regulations_Map", "@type": ["owl:Ontology"]},
            {"@id": "estleg:EU_32016R0679", "@type": ["estleg:EULegislation"]},
            # Same @id as the directive peep, different content: first wins.
            {"@id": "estleg:EU_SHARED", "estleg:celexNumber": "from-regulations"},
        ],
        context={**CTX, "eli": "http://data.europa.eu/eli/ontology#"},
    )
    _write(
        tmp_path / "eurlex" / "eurlex_directives_peep.json",
        [
            {"@id": "estleg:EURlex_Directives_Map", "@type": ["owl:Ontology"]},
            {"@id": "estleg:EU_32000L0060", "@type": ["estleg:EULegislation"]},
            {"@id": "estleg:EU_SHARED", "estleg:celexNumber": "from-directives"},
            # Identical re-assertion of a schema node: a silent duplicate.
            {"@id": "estleg:EULegislation", "@type": ["owl:Class"]},
        ],
    )
    return tmp_path


def _graph(result: rsc.RebuildResult) -> list[dict]:
    return json.loads(result.path.read_text(encoding="utf-8"))["@graph"]


def test_head_first_then_schema_then_sorted_peeps(eurlex_tree: Path) -> None:
    result = rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree)
    ids = [n["@id"] for n in _graph(result)]
    assert ids == [
        "estleg:EURlex_Combined_Map",
        "estleg:EURlex_Schema_2026",
        "estleg:EULegislation",
        "estleg:EUDocType_Directive",
        "estleg:EU_32000L0060",  # directives peep sorts before regulations
        "estleg:EU_SHARED",
        "estleg:EU_32016R0679",
    ]
    assert result.schema_nodes == 3
    assert result.peep_nodes == 3


def test_per_peep_heads_are_folded_into_the_combined_head(eurlex_tree: Path) -> None:
    ids = {n["@id"] for n in _graph(rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree))}
    assert "estleg:EURlex_Directives_Map" not in ids
    assert "estleg:EURlex_Regulations_Map" not in ids


def test_head_is_stamped_dataset_and_keeps_resolvable_import(eurlex_tree: Path) -> None:
    head = _graph(rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree))[0]
    assert head["@type"] == ["owl:Ontology", "void:Dataset", "dcat:Dataset"]
    assert head["rdfs:label"] == "Estonian Legal Ontology — EUR-Lex combined"
    assert head["owl:imports"] == {"@id": "estleg:EURlex_Schema_2026"}
    # No wall-clock stamp on the head (#295).
    assert not any("date" in key.lower() or "modified" in key.lower() for key in head)


def test_dangling_import_is_dropped(eurlex_tree: Path) -> None:
    schema = eurlex_tree / "eurlex" / "eurlex_schema.json"
    doc = json.loads(schema.read_text(encoding="utf-8"))
    doc["@graph"] = [n for n in doc["@graph"] if n["@id"] != "estleg:EURlex_Schema_2026"]
    schema.write_text(json.dumps(doc), encoding="utf-8")
    result = rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree)
    assert "owl:imports" not in _graph(result)[0]
    assert result.dropped_imports == ["estleg:EURlex_Schema_2026"]


def test_first_wins_and_divergent_duplicate_is_reported(
    eurlex_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree)
    shared = [n for n in _graph(result) if n["@id"] == "estleg:EU_SHARED"]
    assert shared == [{"@id": "estleg:EU_SHARED", "estleg:celexNumber": "from-directives"}]
    assert result.duplicate_ids == 2
    assert result.divergent_duplicates == ["estleg:EU_SHARED"]
    out = capsys.readouterr().out
    assert "divergent duplicate estleg:EU_SHARED" in out
    assert "estleg:EULegislation" not in out  # identical duplicate is silent


def test_context_is_the_union_of_source_contexts(eurlex_tree: Path) -> None:
    result = rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree)
    ctx = json.loads(result.path.read_text(encoding="utf-8"))["@context"]
    assert ctx["eli"] == "http://data.europa.eu/eli/ontology#"
    for prefix in ("void", "dcat", "dcterms"):
        assert prefix in ctx


def test_conflicting_context_prefix_fails(eurlex_tree: Path) -> None:
    _write(
        eurlex_tree / "eurlex" / "eurlex_zz_peep.json",
        [{"@id": "estleg:EU_X"}],
        context={**CTX, "owl": "http://example.org/not-owl#"},
    )
    with pytest.raises(rsc.RebuildError, match="owl"):
        rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree)


def test_lfs_pointer_source_fails_instead_of_writing_partial(eurlex_tree: Path) -> None:
    (eurlex_tree / "eurlex" / "eurlex_regulations_peep.json").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n",
        encoding="utf-8",
    )
    with pytest.raises(rsc.RebuildError, match="LFS"):
        rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree)
    assert not (eurlex_tree / "eurlex" / "eurlex_combined.jsonld").exists()


def test_rebuild_is_byte_idempotent_and_check_mode_detects_drift(eurlex_tree: Path) -> None:
    first = rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree)
    assert first.written
    before = first.path.read_bytes()
    second = rsc.rebuild_subcorpus_combined("eurlex", eurlex_tree)
    assert second.up_to_date and not second.written
    assert first.path.read_bytes() == before

    _write(
        eurlex_tree / "eurlex" / "eurlex_zz_peep.json", [{"@id": "estleg:EU_NEW"}]
    )
    assert rsc.main(["--subcorpus", "eurlex", "--krr-dir", str(eurlex_tree), "--check"]) == 1
    assert first.path.read_bytes() == before  # --check never writes
    assert rsc.main(["--subcorpus", "eurlex", "--krr-dir", str(eurlex_tree)]) == 0
    assert rsc.main(["--subcorpus", "eurlex", "--krr-dir", str(eurlex_tree), "--check"]) == 0


def test_curia_and_eelnoud_use_their_own_heads(tmp_path: Path) -> None:
    for name, head in (
        ("curia", "estleg:CURIA_Combined_Map"),
        ("eelnoud", "estleg:Eelnoud_Combined_Map"),
    ):
        _write(tmp_path / name / f"{name}_schema.json", [{"@id": f"estleg:{name}_Class"}])
        _write(tmp_path / name / f"{name}_a_peep.json", [{"@id": f"estleg:{name}_1"}])
        graph = _graph(rsc.rebuild_subcorpus_combined(name, tmp_path))
        assert [n["@id"] for n in graph] == [head, f"estleg:{name}_Class", f"estleg:{name}_1"]
        assert "owl:imports" not in graph[0]


def test_sources_come_from_the_parity_gate_definition() -> None:
    assert rsc.subcorpus_names() == tuple(s.name for s in SUBCORPUS_COMBINED_TARGETS)
    for spec in SUBCORPUS_COMBINED_TARGETS:
        assert rsc.head_id(spec) in spec.expected_extras
        assert rsc.dataset_label(spec).startswith("Estonian Legal Ontology — ")
        assert spec.name in rsc.HEAD_FIELDS


def test_draft_generator_flag_delegates_to_rebuild(tmp_path: Path, monkeypatch) -> None:
    from estleg import generate_draft_legislation as gdl

    _write(tmp_path / "eelnoud" / "eelnoud_schema.json", [{"@id": "estleg:Phase_Enacted"}])
    _write(tmp_path / "eelnoud" / "eelnoud_review_peep.json", [{"@id": "estleg:Draft_X"}])
    monkeypatch.setattr(gdl, "EELNOUD_DIR", tmp_path / "eelnoud")
    monkeypatch.setattr(gdl, "fetch_rss", lambda _url: pytest.fail("must not fetch"))
    assert gdl.main(["--rebuild-combined-from-peeps"]) == 0
    graph = json.loads(
        (tmp_path / "eelnoud" / "eelnoud_combined.jsonld").read_text(encoding="utf-8")
    )["@graph"]
    assert [n["@id"] for n in graph] == [
        "estleg:Eelnoud_Combined_Map",
        "estleg:Phase_Enacted",
        "estleg:Draft_X",
    ]


def test_dag_rebuilds_every_subcorpus_aggregate_before_release_build() -> None:
    steps = {s["name"]: s for s in run_all_integration.STEPS}
    writers = {
        w: name for name, s in steps.items() for w in s["writes"] if w.endswith("_combined.jsonld")
    }
    for spec in SUBCORPUS_COMBINED_TARGETS:
        assert spec.combined_path_rel in writers, spec.combined_path_rel
        assert writers[spec.combined_path_rel] in steps["build_release_artifacts.py"]["depends_on"]
    assert "link_curia_eu_legislation.py" in steps["rebuild_curia_combined"]["depends_on"]
    # rebuild_curia_combined, not link_curia, must be the curia aggregate's last writer.
    topo = run_all_integration.validate_dag(
        run_all_integration.STEPS, run_all_integration.COMMITTED_INPUTS
    )
    assert topo.index("rebuild_curia_combined") > topo.index("link_curia_eu_legislation.py")


# Parity invariant on the committed corpus (LFS artifacts; `-m corpus` job).
@pytest.mark.corpus
@pytest.mark.parametrize("name", [s.name for s in SUBCORPUS_COMBINED_TARGETS])
def test_committed_aggregate_equals_fresh_rebuild(name: str) -> None:
    krr = REPO_ROOT / "krr_outputs"
    spec = rsc.subcorpus_spec(name)
    committed = krr / spec.combined_path_rel
    assert not committed.read_text(encoding="utf-8").startswith("version https://git-lfs"), (
        f"{spec.combined_path_rel} is an un-materialised LFS pointer; run git lfs pull"
    )
    doc, _ = rsc.build_subcorpus_combined(name, krr)
    assert committed.read_text(encoding="utf-8") == rsc.serialize(doc), (
        f"{spec.combined_path_rel} drifted from its sources; run "
        f"`python3 scripts/rebuild_subcorpus_combined.py --subcorpus {name}`"
    )


def test_subcorpus_dir_override_reads_and_writes_inside_that_dir(tmp_path: Path) -> None:
    odd = tmp_path / "drafts_elsewhere"
    _write(odd / "eelnoud_schema.json", [{"@id": "estleg:Phase_Enacted"}])
    _write(odd / "eelnoud_review_peep.json", [{"@id": "estleg:Draft_X"}])
    result = rsc.rebuild_subcorpus_combined("eelnoud", tmp_path, subcorpus_dir=odd)
    assert result.path == odd / "eelnoud_combined.jsonld"
    assert [n["@id"] for n in _graph(result)][1:] == ["estleg:Phase_Enacted", "estleg:Draft_X"]
    assert not (tmp_path / "eelnoud").exists()
