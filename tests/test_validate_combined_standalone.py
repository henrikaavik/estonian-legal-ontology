"""Tests for the combined-only SHACL regression gate (#489).

The gate validates ``krr_outputs/combined_ontology.jsonld`` ALONE (no sibling
sub-corpora) and must:

  * FAIL on a shaped stub node missing its SHACL-required semantic edges
    (the pre-#488 state of the aggregate artifact), and
  * PASS once those edges are present (the post-#488 builder output), and
  * SKIP enum-like ``graphClosureExempt`` controlled-vocabulary predicates so a
    combined-only load is not asked to inline controlled vocabularies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_combined_standalone as gate  # noqa: E402

CONTEXT = {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}

SHAPES = Path(__file__).resolve().parent.parent / "shacl"


def write_combined(tmp_path: Path, nodes: list[dict]) -> Path:
    path = tmp_path / "combined_ontology.jsonld"
    path.write_text(
        json.dumps({"@context": CONTEXT, "@graph": nodes}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# --- reusable fixture nodes -------------------------------------------------

ISSUER = {
    "@id": "estleg:Issuer_saku_vallavolikogu",
    "@type": ["owl:NamedIndividual", "estleg:Issuer"],
    "rdfs:label": "Saku Vallavolikogu",
    "estleg:bodyType": "volikogu",
    "estleg:currentMunicipality": {"@id": "estleg:Municipality_EHAK_0681"},
    "estleg:mappingSource": "haldusreform-2017",
}

MUNICIPALITY = {
    "@id": "estleg:Municipality_EHAK_0681",
    "@type": ["owl:NamedIndividual", "estleg:Municipality"],
    "rdfs:label": "Saku vald",
    "estleg:ehakCode": "0681",
    "estleg:county": "Harju maakond",
}


def _incomplete_municipal_stub() -> dict:
    """The pre-#488 shape: a typed MunicipalRegulation stub stripped of edges."""
    return {
        "@id": "estleg:Reg_1001517_Map_2026",
        "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
        "rdfs:label": "Kaugküttepiirkonna määramine Saku vallas (määrus)",
        "estleg:isStubNode": True,
    }


def _complete_municipal_stub() -> dict:
    """The post-#488 shape: the same stub carrying its required edges."""
    return {
        "@id": "estleg:Reg_1001517_Map_2026",
        "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
        "rdfs:label": "Kaugküttepiirkonna määramine Saku vallas (määrus)",
        "estleg:documentType": "määrus",
        "estleg:terviktekstId": "1001517",
        "estleg:titleNormalized": "kaugkuttepiirkonna maaramine saku vallas",
        "estleg:enactedBy": {"@id": "estleg:Issuer_saku_vallavolikogu"},
        "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0681"},
        "estleg:isStubNode": True,
    }


def test_gate_fails_on_incomplete_shaped_stub(tmp_path):
    """A combined carrying a thin MunicipalRegulation stub fails the gate."""
    combined = write_combined(tmp_path, [_incomplete_municipal_stub()])
    summary = gate.evaluate(combined, SHAPES)

    assert summary["status"] == "violations", summary
    paths = {row["path"] for row in summary["groups"]}
    assert paths == {
        "estleg:documentType",
        "estleg:terviktekstId",
        "estleg:enactedBy",
        "estleg:enactedByMunicipality",
        "estleg:titleNormalized",
    }, paths
    # every finding is grouped by focus type + the isStubNode flag (#489)
    for row in summary["groups"]:
        assert "estleg:MunicipalRegulation" in row["focus_type"], row
        assert row["is_stub"] is True, row
        assert row["examples"] == ["https://data.riik.ee/ontology/estleg#Reg_1001517_Map_2026"]


def test_gate_passes_on_complete_shaped_stub(tmp_path):
    """The same stub passes once it carries its required edges and the
    sh:class targets (Issuer / Municipality) resolve in the same file."""
    combined = write_combined(
        tmp_path, [_complete_municipal_stub(), ISSUER, MUNICIPALITY]
    )
    summary = gate.evaluate(combined, SHAPES)
    assert summary["status"] == "ok", summary["groups"]


def test_gate_skips_graph_closure_exempt_enum_predicates(tmp_path):
    """A CourtDecision stub missing only the enum-like estleg:caseType
    (graphClosureExempt) is NOT reported — that classifier lives in the
    controlled vocabulary, not on the combined-only node."""
    court_stub = {
        "@id": "estleg:RK_3_21_2176_52",
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "rdfs:label": "RK 3-21-2176/52",
        "estleg:caseNumber": "3-21-2176/52",
        # estleg:caseType deliberately absent — exempt, must be skipped
        "estleg:isStubNode": True,
    }
    combined = write_combined(tmp_path, [court_stub])
    summary = gate.evaluate(combined, SHAPES)
    assert summary["status"] == "ok", summary["groups"]
    assert summary["skipped_exempt"] >= 1, summary


def test_gate_reports_malformed_present_exempt_value(tmp_path):
    """A *present but malformed* graphClosureExempt value is a real defect, not
    an exemption. A caseType that is a literal (not the enum IRI) trips
    sh:nodeKind and MUST be reported — even on a stub — because the exempt skip
    covers only a *missing* classifier (sh:minCount), never a present one. The
    pre-fix filter swallowed this and produced a false PASS (#491 review)."""
    court_stub = {
        "@id": "estleg:RK_3_21_2176_52",
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "rdfs:label": "RK 3-21-2176/52",
        "estleg:caseNumber": "3-21-2176/52",
        # caseType is PRESENT but a bare literal, not the enum IRI -> nodeKind
        "estleg:caseType": "not-an-iri",
        "estleg:isStubNode": True,
    }
    combined = write_combined(tmp_path, [court_stub])
    summary = gate.evaluate(combined, SHAPES)

    assert summary["status"] == "violations", summary
    case_type_rows = [r for r in summary["groups"] if r["path"] == "estleg:caseType"]
    assert len(case_type_rows) == 1, summary["groups"]
    assert case_type_rows[0]["count"] == 1, case_type_rows
    # the malformed value was reported, NOT swallowed by the exempt filter
    assert summary["skipped_exempt"] == 0, summary


def test_gate_reports_missing_exempt_classifier_on_non_stub(tmp_path):
    """The exempt skip is for closure STUBS only. A non-stub full node genuinely
    missing its enum classifier is a builder regression (combined dropped an edge
    it should carry inline) and IS reported, not skipped (#491 review)."""
    full_court = {
        "@id": "estleg:RK_3_21_2176_52",
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "rdfs:label": "RK 3-21-2176/52",
        "estleg:caseNumber": "3-21-2176/52",
        # caseType absent AND this is not a stub -> reported, not exempt
    }
    combined = write_combined(tmp_path, [full_court])
    summary = gate.evaluate(combined, SHAPES)

    assert summary["status"] == "violations", summary
    paths = {r["path"] for r in summary["groups"]}
    assert "estleg:caseType" in paths, summary["groups"]
    assert summary["skipped_exempt"] == 0, summary


def test_gate_errors_when_combined_missing(tmp_path):
    summary = gate.evaluate(tmp_path / "combined_ontology.jsonld", SHAPES)
    assert summary["status"] == "error"
    assert any("not found" in e for e in summary["errors"])


def test_main_exit_codes(tmp_path):
    """main() returns 1 on violations, 0 when clean."""
    bad = write_combined(tmp_path, [_incomplete_municipal_stub()])
    assert gate.main(["--combined", str(bad), "--shapes-dir", str(SHAPES)]) == 1

    good_dir = tmp_path / "good"
    good_dir.mkdir()
    good = write_combined(
        good_dir, [_complete_municipal_stub(), ISSUER, MUNICIPALITY]
    )
    assert gate.main(["--combined", str(good), "--shapes-dir", str(SHAPES)]) == 0
