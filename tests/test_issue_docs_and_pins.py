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


def test_scripts_inventory_readme_exists():
    text = (REPO / "scripts" / "README.md").read_text(encoding="utf-8")
    assert "live-DAG" in text
    assert "spent-migration" in text
