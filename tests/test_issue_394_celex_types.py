"""#394 — non-Regulation CELEX must not stay typed as Regulation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_eu_legislation as gel

KRR = Path(__file__).resolve().parent.parent / "krr_outputs"


def test_retarget_rewrites_sector4_x_and_leaves_real_regulations():
    graph = [
        {
            "@id": "estleg:EU_42024X0211",
            "@type": ["estleg:EULegislation"],
            "estleg:celexNumber": "42024X0211",
            "estleg:euDocumentType": {"@id": "estleg:EUDocType_Regulation"},
        },
        {
            "@id": "estleg:EU_32016R0679",
            "@type": ["estleg:EULegislation"],
            "estleg:celexNumber": "32016R0679",
            "estleg:euDocumentType": {"@id": "estleg:EUDocType_Regulation"},
        },
        {
            "@id": "estleg:EU_52025AP0047",
            "@type": ["estleg:EULegislation"],
            "estleg:celexNumber": "52025AP0047",
            "estleg:euDocumentType": {"@id": "estleg:EUDocType_ParliamentPosition"},
        },
    ]
    assert gel.retarget_non_regulation_types(graph) == 1
    assert graph[0]["estleg:euDocumentType"]["@id"] == (
        "estleg:EUDocType_InternationalAgreement"
    )
    assert graph[1]["estleg:euDocumentType"]["@id"] == (
        "estleg:EUDocType_Regulation"
    )
    assert graph[2]["estleg:euDocumentType"]["@id"] == (
        "estleg:EUDocType_ParliamentPosition"
    )
    assert gel.retarget_non_regulation_types(graph) == 0


def test_committed_regulations_peep_has_no_mistyped_sector45():
    path = KRR / "eurlex" / "eurlex_regulations_peep.json"
    graph = json.loads(path.read_text(encoding="utf-8"))["@graph"]
    bad = []
    for node in graph:
        if not isinstance(node, dict):
            continue
        types = node.get("@type")
        tlist = types if isinstance(types, list) else [types]
        if "estleg:EULegislation" not in tlist:
            continue
        celex = node.get("estleg:celexNumber")
        if not isinstance(celex, str):
            continue
        doc = node.get("estleg:euDocumentType")
        did = doc.get("@id") if isinstance(doc, dict) else doc
        if celex.startswith(("4", "5")) and did == "estleg:EUDocType_Regulation":
            bad.append((node.get("@id"), celex, did))
    assert bad == [], f"still typed Regulation: {bad[:5]}"


def test_ticket_sample_42024x0211_is_international_agreement():
    path = KRR / "eurlex" / "eurlex_regulations_peep.json"
    graph = json.loads(path.read_text(encoding="utf-8"))["@graph"]
    node = next(n for n in graph if n.get("estleg:celexNumber") == "42024X0211")
    assert node["estleg:euDocumentType"]["@id"] == (
        "estleg:EUDocType_InternationalAgreement"
    )


def test_coverage_report_processed_plus_skipped_equals_input():
    path = KRR / "reports" / "kov" / "classify_eurovoc_coverage.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["files_processed"] + doc["files_skipped"] == doc["input_files_total"]
    assert "unclassified" not in doc.get("skip_reasons", {})
    assert doc["files_with_no_output"] == 2310
    assert doc["files_skipped"] == 4


def test_eurlex_index_splits_override_types():
    path = KRR / "eurlex" / "EURLEX_INDEX.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    by_type = doc["by_type"]
    assert by_type["Regulation"]["total"] == 18753
    assert by_type["InternationalAgreement"]["total"] == 26
    assert by_type["ParliamentPosition"]["total"] == 5
    assert (
        by_type["Regulation"]["total"]
        + by_type["InternationalAgreement"]["total"]
        + by_type["ParliamentPosition"]["total"]
        == 18784
    )


def test_draft_impact_unresolved_count_matches_list():
    path = KRR / "draft_impact_report.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    names = doc["unresolved_law_names"]
    assert doc["summary"]["affected_law_names_unresolved"] == len(names)
    assert doc["summary"]["affected_law_names_unresolved"] == len(set(names))
