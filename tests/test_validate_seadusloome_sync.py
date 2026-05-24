"""Tests for the Seadusloome-compatible SHACL validator.

These tests mirror the way the Seadusloome sync converter loads
ontology data (priority `combined_ontology.jsonld` plus the documented
public section 2 load surface) and call the validator's ``main`` directly
so the exit code and the printed grouped summary can be asserted against.
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
    "dcterms": "http://purl.org/dc/terms/",
}


def _write_combined(krr_dir: Path, graph_nodes: list[dict]) -> None:
    """Write ``combined_ontology.jsonld`` containing the supplied nodes."""
    krr_dir.mkdir(parents=True, exist_ok=True)
    payload = {"@context": CONTEXT, "@graph": graph_nodes}
    (krr_dir / "combined_ontology.jsonld").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _seed_seadusloome_subdirs(krr_dir: Path) -> None:
    """Create public load-surface sub-directories so the loader walks them."""
    for name in validate_seadusloome_sync.SEADUSLOOME_SUBDIRS:
        (krr_dir / name).mkdir(parents=True, exist_ok=True)


def _write_sidecar_target(krr_dir: Path, iri: str = "estleg:Cluster_X") -> None:
    """Write a tiny sidecar node used to close object references in tests."""
    concepts = krr_dir / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    payload = {
        "@context": CONTEXT,
        "@graph": [{"@id": iri, "@type": ["owl:NamedIndividual"]}],
    }
    (concepts / "concepts_combined.jsonld").write_text(
        json.dumps(payload), encoding="utf-8"
    )


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
    _write_sidecar_target(krr)
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
    _write_sidecar_target(krr)
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


def test_act_without_subject_passes_optional_eurovoc_contract(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:Act_Without_Subject",
                "@type": ["estleg:Act"],
            }
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code == 0, output


def test_non_eurovoc_subject_warns(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:Act_With_Non_EuroVoc_Subject",
                "@type": ["estleg:Act"],
                "dcterms:subject": {"@id": "http://example.com/topic"},
            }
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code != 0, output
    assert "Warning" in output
    assert "subject" in output


def test_missing_combined_ontology_is_fatal(tmp_path, capsys):
    """Regression for issue #140/#141.

    If ``combined_ontology.jsonld`` is missing, the gate must exit 2 — a
    PASS on the partial sub-directory inputs would silently skip the
    hundreds of root ``*_peep.json`` enacted-law files that the consumer
    only receives via the combined artifact.
    """
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    # No combined_ontology.jsonld is written.

    code, output = _run_validator(krr, capsys)

    assert code == 2, output
    assert "combined_ontology.jsonld" in output
    assert "ERROR" in output


def test_collect_inputs_loads_public_sidecars_recursively_and_skips_reports(tmp_path):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(krr, [])

    data_file = krr / "harmonisation" / "harmonisation_by_directive" / "harm_1.json"
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps({"@context": CONTEXT, "@graph": []}), encoding="utf-8")

    report_file = krr / "harmonisation" / "harmonisation_report.json"
    report_file.write_text(json.dumps({"summary": {}}), encoding="utf-8")

    inputs, warnings, errors = validate_seadusloome_sync.collect_inputs(krr)

    assert errors == []
    assert warnings == []
    assert data_file in inputs
    assert report_file not in inputs


def test_graph_closure_fails_on_unresolved_public_reference(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:Draft_X",
                "@type": ["owl:NamedIndividual"],
                "estleg:amendsLaw": {"@id": "estleg:Missing_Map_2026"},
            }
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code == 1, output
    assert "GRAPH CLOSURE FAILURES" in output
    assert "estleg:amendsLaw: 1" in output
    assert "estleg:Missing_Map_2026" in output


def test_graph_closure_passes_when_target_is_loaded_from_sidecar(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_sidecar_target(krr, "estleg:Target_Map_2026")
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:Draft_X",
                "@type": ["owl:NamedIndividual"],
                "estleg:amendsLaw": {"@id": "estleg:Target_Map_2026"},
                "estleg:decisionType": {"@id": "estleg:DecisionType_Judgment"},
            }
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code == 0, output
    assert "Graph closure: PASS" in output


def test_graph_closure_indexes_top_level_jsonld_nodes(tmp_path):
    single = tmp_path / "single.jsonld"
    single.write_text(
        json.dumps(
            {
                "@context": CONTEXT,
                "@id": "estleg:Single_Target",
                "@type": ["owl:NamedIndividual"],
            }
        ),
        encoding="utf-8",
    )
    array = tmp_path / "array.jsonld"
    array.write_text(
        json.dumps(
            [
                {
                    "@id": "estleg:Source_Node",
                    "estleg:references": {"@id": "estleg:Single_Target"},
                },
                {"@id": "estleg:Array_Target"},
            ]
        ),
        encoding="utf-8",
    )

    summary = validate_seadusloome_sync.validate_graph_closure([single, array])

    assert summary["total"] == 0


def test_graph_closure_preserves_outer_predicate_for_list_wrapped_refs(tmp_path):
    path = tmp_path / "wrapped.jsonld"
    path.write_text(
        json.dumps(
            {
                "@context": CONTEXT,
                "@graph": [
                    {
                        "@id": "estleg:Source_Node",
                        "estleg:references": {
                            "@list": [{"@id": "estleg:Missing_Target"}]
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = validate_seadusloome_sync.validate_graph_closure([path])

    assert summary["by_predicate"] == {"estleg:references": 1}


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
