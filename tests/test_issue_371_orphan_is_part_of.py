"""Issue #371 — structuredBody provisions must get an isPartOf container."""

from __future__ import annotations

from pathlib import Path

from backfill_orphan_is_part_of import _is_provision, _types, backfill_graph


def test_preamble_orphans_get_preamble_chapter():
    graph = [
        {
            "@id": "estleg:REÕS_Map_2026",
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
            "estleg:contentStatus": "structuredBody",
        },
        {
            "@id": "estleg:Chapter_REÕS_1",
            "@type": ["owl:NamedIndividual", "estleg:Chapter"],
            "estleg:hasPart": [{"@id": "estleg:REÕS_Par_30"}],
        },
        {"@id": "estleg:REÕS_Par_1", "@type": ["owl:NamedIndividual", "estleg:LegalProvision_x"]},
        {
            "@id": "estleg:REÕS_Par_30",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision_x"],
            "estleg:isPartOf": {"@id": "estleg:Chapter_REÕS_1"},
        },
    ]
    assert backfill_graph(graph) == 1
    p1 = next(n for n in graph if n["@id"] == "estleg:REÕS_Par_1")
    assert p1["estleg:isPartOf"]["@id"] == "estleg:Chapter_REÕS_Preamble"
    chapter = next(n for n in graph if n["@id"] == "estleg:Chapter_REÕS_Preamble")
    assert {"@id": "estleg:REÕS_Par_1"} in chapter["estleg:hasPart"]


def test_unnamed_jagu_orphan_inherits_previous_chapter():
    graph = [
        {
            "@id": "estleg:ES_Map_2026",
            "@type": ["estleg:Act"],
            "estleg:contentStatus": "structuredBody",
        },
        {"@id": "estleg:Chapter_ES_1", "@type": ["owl:NamedIndividual", "estleg:Chapter"]},
        {
            "@id": "estleg:ES_Par_5",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision_x"],
            "estleg:isPartOf": {"@id": "estleg:Chapter_ES_1"},
        },
        {"@id": "estleg:ES_Par_6", "@type": ["owl:NamedIndividual", "estleg:LegalProvision_x"]},
    ]
    assert backfill_graph(graph) == 1
    p6 = next(n for n in graph if n["@id"] == "estleg:ES_Par_6")
    assert p6["estleg:isPartOf"]["@id"] == "estleg:Chapter_ES_1"
    chapter = next(n for n in graph if n["@id"] == "estleg:Chapter_ES_1")
    assert {"@id": "estleg:ES_Par_6"} in chapter["estleg:hasPart"]


def test_committed_structured_body_peeps_have_no_orphan_provisions():
    import json

    krr = Path(__file__).resolve().parents[1] / "krr_outputs"
    leftovers = []
    for path in krr.glob("*_peep.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        graph = doc.get("@graph") or []
        act = next(
            (n for n in graph if isinstance(n, dict) and "estleg:Act" in _types(n)),
            None,
        )
        if not act or act.get("estleg:contentStatus") != "structuredBody":
            continue
        if not any(
            isinstance(n, dict) and str(n.get("@id", "")).startswith("estleg:Chapter_")
            for n in graph
        ):
            continue
        for node in graph:
            if isinstance(node, dict) and _is_provision(node) and "estleg:isPartOf" not in node:
                leftovers.append((path.name, node.get("@id")))
    assert leftovers == [], leftovers[:10]
