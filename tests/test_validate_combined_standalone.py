"""Tests for the combined-only SHACL regression gate (#489, #568).

The gate validates ``krr_outputs/combined_ontology.jsonld`` ALONE (no sibling
sub-corpora) and must:

  * FAIL on a shaped stub node missing its SHACL-required semantic edges
    (the pre-#488 state of the aggregate artifact), and
  * PASS once those edges are present (the post-#488 builder output), and
  * SKIP enum-like ``graphClosureExempt`` controlled-vocabulary predicates so a
    combined-only load is not asked to inline controlled vocabularies, and
  * FAIL when a bulk class (e.g. ``estleg:ProvisionVersion``) is present below
    its minimum instance-count sanity floor, which SHACL min-count alone cannot
    catch — a vanished class yields zero focus nodes and would pass clean (#568).
"""

from __future__ import annotations

import json
from pathlib import Path

from estleg import validate_combined_standalone as gate

CONTEXT = {
    "estleg": "https://w3id.org/estleg/",
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
    # class_floors={} isolates the shaped-stub violation from the #568 floors.
    summary = gate.evaluate(combined, SHAPES, class_floors={})

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
        assert row["examples"] == ["https://w3id.org/estleg/Reg_1001517_Map_2026"]


def test_gate_passes_on_complete_shaped_stub(tmp_path):
    """The same stub passes once it carries its required edges and the
    sh:class targets (Issuer / Municipality) resolve in the same file."""
    combined = write_combined(
        tmp_path, [_complete_municipal_stub(), ISSUER, MUNICIPALITY]
    )
    # class_floors={} disables the #568 bulk-class floor check: this tiny
    # synthetic fixture intentionally carries no ProvisionVersion/AmendmentEvent.
    summary = gate.evaluate(combined, SHAPES, class_floors={})
    assert summary["status"] == "ok", summary["groups"]
    assert summary["class_floor_failures"] == [], summary


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
    # class_floors={} keeps this focused on the exempt-skip behaviour, not the
    # #568 bulk-class floors (the fixture has no ProvisionVersion/AmendmentEvent).
    summary = gate.evaluate(combined, SHAPES, class_floors={})
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
    # class_floors={} isolates the malformed-value finding from the #568 floors.
    summary = gate.evaluate(combined, SHAPES, class_floors={})

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
    # class_floors={} isolates the missing-classifier finding from the #568 floors.
    summary = gate.evaluate(combined, SHAPES, class_floors={})

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
    # --no-floor disables the #568 bulk-class floors so this tiny well-formed
    # fixture (no ProvisionVersion/AmendmentEvent) exercises the clean exit path.
    assert (
        gate.main(
            ["--combined", str(good), "--shapes-dir", str(SHAPES), "--no-floor"]
        )
        == 0
    )


# --- #568: bulk-class minimum instance-count floor ---------------------------


def _provision_version_nodes(count: int) -> list[dict]:
    """`count` distinct, minimally-typed estleg:ProvisionVersion nodes.

    Only the type + id matter here — the #568 floor counts distinct typed
    subjects, independent of whether they satisfy ProvisionVersionShape.
    """
    return [
        {
            "@id": f"estleg:PV_test_{i}",
            "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
        }
        for i in range(count)
    ]


def test_floor_failure_when_bulk_class_near_absent(tmp_path):
    """A combined with only 2 ProvisionVersion nodes fails the gate when the
    floor demands >= 1000 — the exact false-PASS #568 guards against. The
    failure is recorded in class_floor_failures (not the SHACL groups), the
    status flips to "violations", and the report names the class + floor."""
    combined = write_combined(tmp_path, _provision_version_nodes(2))
    summary = gate.evaluate(
        combined, SHAPES, class_floors={"estleg:ProvisionVersion": 1000}
    )

    assert summary["status"] == "violations", summary
    assert summary["class_floor_failures"] == [
        {"class": "estleg:ProvisionVersion", "count": 2, "floor": 1000}
    ], summary["class_floor_failures"]

    # main() (driven through evaluate with the same floors) must exit non-zero;
    # status == "violations" is the exit-1 signal main() returns.
    report = "\n".join(gate.format_report(summary))
    assert "estleg:ProvisionVersion present 2x but expected >= 1000" in report, report
    assert "missing this class" in report, report


def test_floor_met_when_enough_instances_present(tmp_path):
    """With a small floor (2) and 3 ProvisionVersion nodes present, the floor is
    cleared: class_floor_failures is empty. Asserted directly on the floor list
    (not overall status) so any unrelated ProvisionVersionShape SHACL findings
    on these minimal nodes do not mask the floor logic under test."""
    combined = write_combined(tmp_path, _provision_version_nodes(3))
    summary = gate.evaluate(
        combined, SHAPES, class_floors={"estleg:ProvisionVersion": 2}
    )
    assert summary["class_floor_failures"] == [], summary["class_floor_failures"]


def test_floor_reports_each_missing_bulk_class(tmp_path):
    """Both default bulk classes absent (empty combined) => one floor-failure row
    per expected class, using the module-default EXPECTED_CLASS_FLOORS."""
    combined = write_combined(tmp_path, [])
    summary = gate.evaluate(combined, SHAPES)  # default floors

    assert summary["status"] == "violations", summary
    failed_classes = {f["class"] for f in summary["class_floor_failures"]}
    assert failed_classes == set(gate.EXPECTED_CLASS_FLOORS), summary
    for failure in summary["class_floor_failures"]:
        assert failure["count"] == 0, failure
        assert failure["floor"] == gate.EXPECTED_CLASS_FLOORS[failure["class"]], failure
