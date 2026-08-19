"""Issue #417 — eurlex_combined must carry peep transposition edges."""

from __future__ import annotations

import json
from pathlib import Path

from estleg import run_all_integration
from estleg.generate_eu_legislation import rebuild_eurlex_combined_from_peeps


def _count_pred(graph: list, pred: str) -> int:
    n = 0
    for node in graph:
        if isinstance(node, dict) and node.get(pred):
            n += 1
    return n


def test_rebuild_copies_transposition_edges(tmp_path: Path):
    eurlex = tmp_path / "eurlex"
    eurlex.mkdir()
    (eurlex / "eurlex_directives_peep.json").write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:EURlex_Directives_Map",
                        "@type": ["owl:Ontology"],
                    },
                    {
                        "@id": "estleg:EU_32000L0060",
                        "@type": ["estleg:EULegislation"],
                        "estleg:transposedBy": {"@id": "estleg:VES_Map"},
                        "estleg:transpositionDeadline": {
                            "@value": "2003-12-22",
                            "@type": "xsd:date",
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    stats = rebuild_eurlex_combined_from_peeps(eurlex)
    doc = json.loads(stats["path"].read_text(encoding="utf-8"))
    graph = doc["@graph"]
    ids = {n.get("@id") for n in graph if isinstance(n, dict)}
    assert "estleg:EU_32000L0060" in ids
    assert "estleg:EURlex_Directives_Map" not in ids
    assert "estleg:EURlex_Combined_Map" in ids
    assert _count_pred(graph, "estleg:transposedBy") == 1
    assert _count_pred(graph, "estleg:transpositionDeadline") == 1


def test_dag_rebuilds_eurlex_combined_after_transposition():
    names = [s["name"] for s in run_all_integration.STEPS]
    assert "rebuild_eurlex_combined" in names
    step = next(s for s in run_all_integration.STEPS if s["name"] == "rebuild_eurlex_combined")
    assert "generate_transposition_mapping.py" in step["depends_on"]
    assert "eurlex/eurlex_combined.jsonld" in step["writes"]


def test_committed_eurlex_combined_matches_directive_peep_transposition():
    root = Path(__file__).resolve().parents[1] / "krr_outputs" / "eurlex"
    peep = json.loads((root / "eurlex_directives_peep.json").read_text(encoding="utf-8"))
    combined = json.loads((root / "eurlex_combined.jsonld").read_text(encoding="utf-8"))
    assert _count_pred(combined["@graph"], "estleg:transposedBy") == _count_pred(
        peep["@graph"], "estleg:transposedBy"
    )
    assert _count_pred(combined["@graph"], "estleg:transpositionDeadline") == _count_pred(
        peep["@graph"], "estleg:transpositionDeadline"
    )
