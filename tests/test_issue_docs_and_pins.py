"""Structural tests for docs/pins landed while closing review tickets."""

from __future__ import annotations

from pathlib import Path

import kov_pipeline_coverage as kpc
from estleg_common import BUILD_EVALUATION_DATE, ONTOLOGY_VERSION

REPO = Path(__file__).resolve().parent.parent


def test_architecture_doc_exists_and_names_surfaces():
    text = (REPO / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "Load surfaces" in text
    assert "combined_ontology.jsonld" in text
    assert "mcp_server" in text


def test_citation_cff_version_matches_ontology():
    text = (REPO / "CITATION.cff").read_text(encoding="utf-8")
    assert f"version: {ONTOLOGY_VERSION}" in text
    assert "henrikaavik/estonian-legal-ontology" in text


def test_stability_md_has_predicate_tiers():
    text = (REPO / "docs" / "STABILITY.md").read_text(encoding="utf-8")
    assert "Heuristic" in text
    assert "isStubNode" in text


def test_dependabot_and_codeql_workflows_exist():
    assert (REPO / ".github" / "dependabot.yml").is_file()
    assert (REPO / ".github" / "workflows" / "codeql.yml").is_file()


def test_kov_coverage_timestamp_is_evaluation_date_not_epoch():
    """#533: coverage sidecars must not stamp 1970-01-01."""
    assert kpc.PINNED_RUN_TIMESTAMP.startswith(BUILD_EVALUATION_DATE)
    assert not kpc.PINNED_RUN_TIMESTAMP.startswith("1970-01-01")


def test_temporal_status_shape_has_closed_enum():
    """#451: temporalStatus is not an unconstrained string."""
    text = (REPO / "shacl" / "estonian_legal_shapes.ttl").read_text(encoding="utf-8")
    assert '"inForce"' in text
    assert "sh:path estleg:temporalStatus" in text


def test_harmonised_with_domain_is_act_not_provision():
    """#335: estleg:harmonisedWith rdfs:domain is Act."""
    import json

    cv = json.loads((REPO / "krr_outputs" / "controlled_vocabulary.jsonld").read_text())
    for n in cv.get("@graph", []):
        if n.get("@id") == "estleg:harmonisedWith":
            assert n.get("rdfs:domain", {}).get("@id") == "estleg:Act"
            return
    raise AssertionError("harmonisedWith missing from CV")


def test_casetype_individuals_live_in_controlled_vocabulary():
    """#270: CaseType enums are on the consumer CV load surface."""
    import json

    cv = json.loads((REPO / "krr_outputs" / "controlled_vocabulary.jsonld").read_text())
    ids = {n.get("@id") for n in cv.get("@graph", []) if isinstance(n, dict)}
    for iri in (
        "estleg:CaseType_Criminal",
        "estleg:CaseType_Civil",
        "estleg:CaseType_Administrative",
        "estleg:CaseType_Misdemeanor",
        "estleg:CaseType_ConstitutionalReview",
        "estleg:CaseType_Other",
    ):
        assert iri in ids, iri


def test_draft_and_court_generators_use_atomic_save_json():
    """#464: generators must not shadow save_json with a non-atomic open/dump."""
    import ast

    for rel in (
        "scripts/generate_draft_legislation.py",
        "scripts/generate_court_decisions.py",
    ):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        defs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        assert "save_json" not in defs, rel


def test_scripts_inventory_readme_exists():
    text = (REPO / "scripts" / "README.md").read_text(encoding="utf-8")
    assert "live-DAG" in text
    assert "spent-migration" in text
