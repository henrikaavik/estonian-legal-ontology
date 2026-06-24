"""Tests for the fitness-for-purpose eval harness (#617).

Builds a tiny synthetic corpus with KNOWN metrics and asserts the harness
computes them — so the precision/recall + coverage + co-occurrence math is
verified independently of the real corpus.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import eval_harness  # noqa: E402


def _write(path: Path, graph: list[dict]) -> None:
    path.write_text(json.dumps({"@context": {}, "@graph": graph}), encoding="utf-8")


def _build_corpus(tmp: Path) -> Path:
    krr = tmp / "krr_outputs"
    krr.mkdir()
    (krr / "provision_versions").mkdir()
    # law_a: has all five verticals as edges + a version sidecar
    _write(
        krr / "law_a_peep.json",
        [
            {
                "@id": "estleg:A",
                "@type": ["estleg:Act", "estleg:Law"],
                # temporalStatus is act-level (#128), not on provisions.
                "estleg:temporalStatus": "inForce",
                "estleg:transposesDirective": [{"@id": "estleg:EU_X"}],
            },
            {
                "@id": "estleg:A_Par_1",
                "@type": ["estleg:LegalProvision_law_a"],
                "estleg:targetGroup": ["citizen"],
                "estleg:normativeType": "Obligation",
                "estleg:competentAuthority": [{"@id": "estleg:Institution_kohus"}],
                "estleg:interpretedBy": [{"@id": "estleg:RK_1"}],
                "estleg:hasSanction": [{"@id": "estleg:Sanction_A_1"}],
                "estleg:references": [{"@id": "estleg:A_Par_2"}],
            },
            {
                "@id": "estleg:A_Par_2",
                "@type": ["estleg:LegalProvision_law_a"],
                # references an out-of-corpus node -> unresolved
                "estleg:references": [{"@id": "estleg:NOT_PRESENT"}],
            },
        ],
    )
    # version sidecar named for the law -> pointInTime present
    (krr / "provision_versions" / "law_a.jsonld").write_text("{}", encoding="utf-8")
    # law_b: a bare provision, no verticals
    _write(
        krr / "law_b_peep.json",
        [
            {"@id": "estleg:B", "@type": ["estleg:Act", "estleg:Law"]},
            {"@id": "estleg:B_Par_1", "@type": ["estleg:LegalProvision_law_b"]},
        ],
    )
    (krr / "INDEX.json").write_text(
        json.dumps(
            {
                "total_laws": 2,
                "total_files": 2,
                "laws": [
                    {"name": "law_a", "files": ["law_a_peep.json"]},
                    {"name": "law_b", "files": ["law_b_peep.json"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return krr


def test_evaluate_computes_known_metrics(tmp_path):
    krr = _build_corpus(tmp_path)
    report = eval_harness.evaluate(krr)

    assert report["population"] == {"indexed_laws": 2, "law_files_loaded": 2, "provisions": 3}

    # law_a has all 5 verticals as edges; law_b has none.
    cooc = report["vertical_cooccurrence"]
    assert cooc["laws_with_all_verticals_referenced"] == 1
    assert cooc["histogram"]["5"] == 1
    assert cooc["histogram"]["0"] == 1
    assert cooc["most_complete_laws"][0]["law"] == "law_a"

    # 2 provisions (Par_1, Par_2); 1 carries targetGroup.
    assert report["provision_coverage"]["estleg:targetGroup"]["count"] == 1
    # Act-level temporalStatus known on law_a (its Act node is inForce), not law_b.
    assert report["point_in_time"]["laws_with_known_act_temporalStatus"] == 1
    assert report["point_in_time"]["act_status_known_pct"] == 50.0
    # law_a has a (stub) version sidecar -> provision-layer point-in-time present.
    assert report["point_in_time"]["laws_with_version_sidecar"] == 1

    # 2 cross-ref edges; 1 resolves in-corpus (A_Par_2), 1 does not (NOT_PRESENT).
    cr = report["crossref_edge_resolution"]
    assert cr["edges"] == 2
    assert cr["resolved_in_corpus"] == 1
    assert cr["pct"] == 50.0

    # No Sanction node is DEFINED inline (only a hasSanction edge): sanctions live
    # in the sidecar / combined overlay, not inline in the peep.
    assert report["inline_sanctions"]["inline"] is False
    assert report["retrievability_gap"]["sanction_definitions_inline_in_peeps"] == 0


def test_render_markdown_runs(tmp_path):
    report = eval_harness.evaluate(_build_corpus(tmp_path))
    md = eval_harness.render_markdown(report)
    assert "Fitness-for-purpose evaluation" in md
    assert "Retrievability gap" in md


def test_gold_set_precision_recall(tmp_path):
    krr = _build_corpus(tmp_path)
    gold = tmp_path / "gold.json"
    gold.write_text(
        json.dumps(
            {
                "layer": "targetGroup",
                "property": "estleg:targetGroup",
                "items": [
                    {"node": "estleg:A_Par_1", "gold": ["citizen"]},  # matches -> TP
                    {"node": "estleg:A_Par_2", "gold": ["business"]},  # predicted None -> FN
                ],
            }
        ),
        encoding="utf-8",
    )
    acc = eval_harness.evaluate_gold_set(gold, krr)
    assert acc["true_positives"] == 1
    assert acc["false_negatives"] == 1
    assert acc["precision"] == 1.0  # 1 TP, 0 FP
    assert acc["recall"] == 0.5  # 1 TP, 1 FN


def test_gold_set_confident_wrong_prediction_is_fp_and_fn(tmp_path):
    # A confident-but-WRONG prediction must count as BOTH a false positive and a
    # false negative — otherwise recall ignores it and reports None instead of 0.
    # (A_Par_1 is tagged ["citizen"] in the synthetic corpus; gold says ["business"].)
    krr = _build_corpus(tmp_path)
    gold = tmp_path / "gold.json"
    gold.write_text(
        json.dumps(
            {
                "layer": "targetGroup",
                "property": "estleg:targetGroup",
                "items": [{"node": "estleg:A_Par_1", "gold": ["business"]}],  # predicted ["citizen"]
            }
        ),
        encoding="utf-8",
    )
    acc = eval_harness.evaluate_gold_set(gold, krr)
    assert acc["true_positives"] == 0
    assert acc["false_positives"] == 1
    assert acc["false_negatives"] == 1
    assert acc["precision"] == 0.0
    assert acc["recall"] == 0.0  # NOT None
