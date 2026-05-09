"""Tests for the Seadusloome-compatible SHACL validator.

These tests mirror the way the Seadusloome sync converter loads
ontology data (priority `combined_ontology.jsonld` plus per-bucket
ingest of every JSON/JSON-LD under `eelnoud/`, `riigikohus/`, `curia/`,
and `eurlex/`) and call the validator's ``main`` directly so the exit
code and the printed grouped summary can be asserted against.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_seadusloome_sync  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SHAPES_DIR = REPO_ROOT / "shacl"

CONTEXT = {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}


def _write_combined(krr_dir: Path, graph_nodes: list[dict]) -> None:
    """Write ``combined_ontology.jsonld`` containing the supplied nodes."""
    krr_dir.mkdir(parents=True, exist_ok=True)
    payload = {"@context": CONTEXT, "@graph": graph_nodes}
    (krr_dir / "combined_ontology.jsonld").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _seed_seadusloome_subdirs(krr_dir: Path) -> None:
    """Create the four Seadusloome sub-directories so the loader walks them."""
    for name in ("eelnoud", "riigikohus", "curia", "eurlex"):
        (krr_dir / name).mkdir(parents=True, exist_ok=True)


def _provision_node(
    *,
    iri: str,
    paragrahv: str = "§ 1.",
    summary: str | None = "ok",
    requested_cluster: object | None = None,
) -> dict:
    """Build a LegalProvision-shaped node.

    Triggers ``LegalProvisionShape`` because the shape targets every
    subject of ``estleg:paragrahv`` (mirrors the production graph).
    """
    node: dict = {
        "@id": iri,
        "@type": ["owl:NamedIndividual"],
        "estleg:paragrahv": paragrahv,
    }
    if summary is not None:
        node["estleg:summary"] = summary
    if requested_cluster is not None:
        node["estleg:requestedCluster"] = requested_cluster
    return node


def _run_validator(krr_dir: Path, capsys, *, max_warnings: int = 0) -> tuple[int, str]:
    argv = [
        "--krr-dir",
        str(krr_dir),
        "--shapes-dir",
        str(SHAPES_DIR),
        "--max-warnings",
        str(max_warnings),
    ]
    code = validate_seadusloome_sync.main(argv)
    captured = capsys.readouterr()
    return code, captured.out


def test_literal_requested_cluster_fails(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(
        krr,
        [
            _provision_node(
                iri="estleg:Prov_LiteralCluster",
                summary="ok",
                requested_cluster="estleg:Cluster_X",  # raw string -> Literal
            )
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code != 0, output
    assert "LegalProvisionShape" in output
    assert "requestedCluster" in output
    assert "DIAGNOSTIC" in output


def test_missing_summary_fails(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(
        krr,
        [
            _provision_node(
                iri="estleg:Prov_MissingSummary",
                summary=None,  # forces sh:minCount on summary to fail
                requested_cluster={"@id": "estleg:Cluster_X"},
            )
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code != 0, output
    assert "summary" in output
    assert "LegalProvisionShape" in output


def test_corrected_inputs_pass(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(
        krr,
        [
            _provision_node(
                iri="estleg:Prov_Clean",
                paragrahv="§ 1.",
                summary="ok",
                requested_cluster={"@id": "estleg:Cluster_X"},
            )
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code == 0, output
    assert "PASS" in output


def test_grouped_summary_includes_focus_nodes(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    iri_a = "estleg:Prov_FailA"
    iri_b = "estleg:Prov_FailB"
    _write_combined(
        krr,
        [
            _provision_node(
                iri=iri_a,
                summary="ok",
                requested_cluster="estleg:Cluster_A",  # literal -> NodeKind violation
            ),
            _provision_node(
                iri=iri_b,
                summary="ok",
                requested_cluster="estleg:Cluster_B",
            ),
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code != 0, output
    iri_a_full = "https://data.riik.ee/ontology/estleg#Prov_FailA"
    iri_b_full = "https://data.riik.ee/ontology/estleg#Prov_FailB"
    assert iri_a_full in output or iri_b_full in output, output
    # Group should aggregate both failures under one row.
    assert "2x LegalProvisionShape" in output or "2x  LegalProvisionShape" in output
