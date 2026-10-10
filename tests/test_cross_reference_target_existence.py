"""Cross-reference target existence (2026-10-09 RT refresh).

A repealed § keeps its node (``estleg:provisionRepealed``) but loses its
``_Lg_<n>`` subsection nodes. Citations must cite the finest EXISTING
granularity: lõige if present, else its §, else no edge (a target-less
``estleg:Citation`` node, per #696) — never a dangling @id. Typed #513
edges left over from an earlier run must not survive a re-run.
"""
from __future__ import annotations

import json

import pytest

from estleg import estleg_common
from estleg import extract_cross_references as xr

CTX = {"estleg": "https://w3id.org/estleg/", "owl": "http://www.w3.org/2002/07/owl#"}


def _prov(iri: str, source_act: str, text: str | None = None, **extra) -> dict:
    node = {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:sourceAct": source_act,
    }
    if text:
        node["estleg:legalText"] = {"@value": text, "@language": "et"}
    node.update(extra)
    return node


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """Two fake law peeps: RPKS (the cited law) and SRC (the citing law)."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    tgt = {
        "@context": CTX,
        "@graph": [
            {"@id": "estleg:RPKS_Map", "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
             "estleg:sourceAct": "Sihtseadus"},
            _prov("estleg:RPKS_Par_5", "Sihtseadus"),
            {"@id": "estleg:RPKS_Par_5_Lg_2", "@type": ["estleg:Subsection"],
             "estleg:parentProvision": {"@id": "estleg:RPKS_Par_5"}},
            # Repealed § 21: § node kept, its lõiked gone after the refresh.
            _prov("estleg:RPKS_Par_21", "Sihtseadus", **{"estleg:provisionRepealed": True}),
        ],
    }
    src = {
        "@context": CTX,
        "@graph": [
            {"@id": "estleg:SRC_Map", "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
             "estleg:sourceAct": "Lähteseadus"},
            _prov("estleg:SRC_Par_1", "Lähteseadus", "RPKS § 5 lõike 2 alusel kehtestatakse kord."),
            _prov("estleg:SRC_Par_2", "Lähteseadus", "RPKS § 21 lõike 1 alusel määratud pension."),
            _prov("estleg:SRC_Par_3", "Lähteseadus", "RPKS § 99 lõike 1 alusel antud luba."),
            # Stale typed edge from an earlier run, no citation in the text.
            _prov("estleg:SRC_Par_4", "Lähteseadus", "Ilma viideteta säte.",
                  **{"estleg:isLegalBasisFor": [{"@id": "estleg:RPKS_Par_21_Lg_1"}]}),
        ],
    }
    (krr / "sihtseadus_peep.json").write_text(json.dumps(tgt), encoding="utf-8")
    src_path = krr / "lahteseadus_peep.json"
    src_path.write_text(json.dumps(src), encoding="utf-8")
    monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
    monkeypatch.setattr(xr, "KRR_DIR", krr)
    monkeypatch.setattr(xr, "DATA_DIR", tmp_path / "no_xml")
    return src_path


def _run(src_path, *, guard: bool = True):
    prefix_to_provisions, *_rest, act_iri_to_prefix = xr.build_provision_index()
    known = xr.build_known_target_ids(prefix_to_provisions) if guard else None
    stats = xr.process_law_file(
        src_path,
        {"RPKS": ["RPKS"]},
        prefix_to_provisions,
        {},
        act_iri_to_prefix=act_iri_to_prefix,
        known_target_ids=known,
    )
    graph = json.loads(src_path.read_text(encoding="utf-8"))["@graph"]
    return stats, {n["@id"]: n for n in graph}, known


def _ids(node: dict, prop: str) -> list[str]:
    return [v["@id"] for v in node.get(prop, [])]


def test_existing_lg_resolves_to_lg_iri(corpus):
    _stats, nodes, _known = _run(corpus)
    assert _ids(nodes["estleg:SRC_Par_1"], "estleg:references") == ["estleg:RPKS_Par_5_Lg_2"]
    assert _ids(nodes["estleg:SRC_Par_1"], "estleg:isLegalBasisFor") == ["estleg:RPKS_Par_5_Lg_2"]


def test_missing_lg_under_existing_par_resolves_to_par(corpus):
    _stats, nodes, _known = _run(corpus)
    assert _ids(nodes["estleg:SRC_Par_2"], "estleg:references") == ["estleg:RPKS_Par_21"]
    assert _ids(nodes["estleg:SRC_Par_2"], "estleg:isLegalBasisFor") == ["estleg:RPKS_Par_21"]


def test_missing_par_emits_no_dangling_iri(corpus):
    _stats, nodes, known = _run(corpus)
    assert "estleg:references" not in nodes["estleg:SRC_Par_3"]
    assert "estleg:isLegalBasisFor" not in nodes["estleg:SRC_Par_3"]
    citations = [
        n for n in nodes.values()
        if n.get("estleg:citationSource") == {"@id": "estleg:SRC_Par_3"}
    ]
    assert len(citations) == 1 and "estleg:citationTarget" not in citations[0]
    # Graph closure: every provision-level object IRI exists in the corpus.
    for node in nodes.values():
        for prop in ("estleg:references", *xr.TYPED_REFERENCE_PROPS):
            for iri in _ids(node, prop):
                assert iri in known, (node["@id"], prop, iri)


def test_stale_typed_edge_is_cleared_on_rerun(corpus):
    stats, nodes, _known = _run(corpus)
    assert "estleg:isLegalBasisFor" not in nodes["estleg:SRC_Par_4"]
    assert stats["staleTypedEdgesCleared"] == 1


def test_clear_existing_references_also_clears_typed_edges(corpus):
    xr.clear_existing_references()
    graph = json.loads(corpus.read_text(encoding="utf-8"))["@graph"]
    assert not any("estleg:isLegalBasisFor" in n for n in graph)


def test_guard_folds_stale_index_entry_and_counts(corpus):
    """Even when the resolver index is stale (lists a lõige that no longer
    exists), the guard folds to the § and reports it."""
    prefix_to_provisions, *_ = xr.build_provision_index()
    known = xr.build_known_target_ids(prefix_to_provisions)
    prefix_to_provisions["RPKS"]["21_Lg_1"] = "estleg:RPKS_Par_21_Lg_1"
    prefix_to_provisions["RPKS"]["99_Lg_1"] = "estleg:RPKS_Par_99_Lg_1"
    stats = xr.process_law_file(
        corpus, {"RPKS": ["RPKS"]}, prefix_to_provisions, {},
        known_target_ids=known,
    )
    nodes = {n["@id"]: n for n in json.loads(corpus.read_text(encoding="utf-8"))["@graph"]}
    assert _ids(nodes["estleg:SRC_Par_2"], "estleg:references") == ["estleg:RPKS_Par_21"]
    assert "estleg:references" not in nodes["estleg:SRC_Par_3"]
    assert stats["subsectionTargetsFoldedToSection"] == 1
    assert stats["targetsDroppedMissing"] == 1


@pytest.mark.parametrize(
    "iri,expected",
    [
        ("estleg:A_Par_1_Lg_2", "estleg:A_Par_1_Lg_2"),
        ("estleg:A_Par_1_Lg_3", "estleg:A_Par_1"),
        ("estleg:A_Par_1_Lg_3_1", "estleg:A_Par_1"),
        ("estleg:A_Par_9_Lg_1", None),
        ("estleg:A_Par_9", None),
        ("estleg:A_Map", "estleg:A_Map"),  # act-level IRIs pass through
    ],
)
def test_guard_provision_target(iri, expected):
    known = frozenset({"estleg:A_Par_1", "estleg:A_Par_1_Lg_2"})
    assert xr.guard_provision_target(iri, known) == expected
    assert xr.guard_provision_target(iri, None) == iri
