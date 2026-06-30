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
    "estleg": "https://w3id.org/estleg/",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dcterms": "http://purl.org/dc/terms/",
}


def _write_combined(krr_dir: Path, graph_nodes: list[dict]) -> None:
    """Write ``combined_ontology.jsonld`` containing the supplied nodes.

    Auto-closes ``estleg:partOfAct`` references (issue #415): every provision
    now carries an IRI link to its act root, and graph-closure validation
    requires that root to exist in the graph. A minimal ``estleg:Act`` node is
    appended for each referenced act IRI not already present, mirroring how the
    production combined graph always contains the act roots its provisions
    point at. (The stub carries an ``rdfs:label`` so it satisfies the
    ActTemporalShape label requirement — issue #570.)
    """
    krr_dir.mkdir(parents=True, exist_ok=True)
    nodes = list(graph_nodes)
    present = {n.get("@id") for n in nodes if isinstance(n, dict)}
    referenced_acts = {
        n["estleg:partOfAct"]["@id"]
        for n in graph_nodes
        if isinstance(n, dict)
        and isinstance(n.get("estleg:partOfAct"), dict)
        and "@id" in n["estleg:partOfAct"]
    }
    for act_iri in sorted(referenced_acts - present):
        nodes.append({
            "@id": act_iri,
            "@type": ["owl:NamedIndividual", "estleg:Act"],
            "rdfs:label": "Test Act",
        })
    payload = {"@context": CONTEXT, "@graph": nodes}
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
    part_of_act: object | None = None,
) -> dict:
    """Build a LegalProvision-shaped node.

    Triggers ``LegalProvisionShape`` because the shape targets every
    subject of ``estleg:paragrahv`` (mirrors the production graph). Carries
    a valid IRI ``estleg:partOfAct`` by default (issue #415 requires one);
    callers may override it with their own value.
    """
    node: dict = {
        "@id": iri,
        "@type": ["owl:NamedIndividual"],
        "estleg:paragrahv": paragrahv,
        "estleg:partOfAct": part_of_act or {"@id": "estleg:Act_X"},
    }
    if summary is not None:
        node["estleg:summary"] = summary
    if requested_cluster is not None:
        node["estleg:requestedCluster"] = requested_cluster
    return node


def _run_validator(
    krr_dir: Path,
    capsys,
    *,
    max_warnings: int = 0,
    shapes_dir: Path = SHAPES_DIR,
) -> tuple[int, str]:
    argv = [
        "--krr-dir",
        str(krr_dir),
        "--shapes-dir",
        str(shapes_dir),
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
                "rdfs:label": "Act Without Subject",
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
                "rdfs:label": "Act With Non EuroVoc Subject",
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
    # Present so the historical-municipalities special case (issue #285a) does
    # not emit its missing-input warning — this test asserts a clean warning set.
    _write_historical_municipalities(krr, [])

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


def test_graph_closure_exempt_predicates_are_loaded_from_shapes():
    exempt = validate_seadusloome_sync.graph_closure_exempt_predicates()

    required = {"estleg:caseType", "estleg:decisionType", "estleg:euCourtDecisionType"}
    assert required <= exempt, exempt
    assert all(p.startswith("estleg:") for p in exempt)


def test_graph_closure_exempt_predicates_ignore_malformed_markers(tmp_path):
    shapes = tmp_path / "shapes"
    shapes.mkdir()
    (shapes / "shapes.ttl").write_text(
        """
@prefix estleg: <https://w3id.org/estleg/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

estleg:HostShape
    a sh:NodeShape ;
    sh:property [
        sh:path estleg:stringTrue ;
        estleg:graphClosureExempt "True" ;
    ] ;
    sh:property [
        sh:path estleg:intTrue ;
        estleg:graphClosureExempt 1 ;
    ] ;
    sh:property [
        sh:path [ sh:alternativePath ( estleg:left estleg:right ) ] ;
        estleg:graphClosureExempt true ;
    ] ;
    sh:property [
        estleg:graphClosureExempt true ;
    ] .

estleg:LooseNodeShape
    a sh:NodeShape ;
    sh:path estleg:notAPropertyShape ;
    estleg:graphClosureExempt true .
""",
        encoding="utf-8",
    )

    exempt = validate_seadusloome_sync.graph_closure_exempt_predicates(shapes)

    assert exempt == {"estleg:stringTrue", "estleg:intTrue"}


def test_graph_closure_uses_supplied_shapes_dir_for_exemptions(tmp_path):
    shapes = tmp_path / "shapes"
    shapes.mkdir()
    (shapes / "custom.ttl").write_text(
        """
@prefix estleg: <https://w3id.org/estleg/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .

estleg:CustomShape
    a sh:NodeShape ;
    sh:property [
        sh:path estleg:customEnum ;
        estleg:graphClosureExempt true ;
    ] .
""",
        encoding="utf-8",
    )
    path = tmp_path / "data.jsonld"
    path.write_text(
        json.dumps(
            {
                "@context": CONTEXT,
                "@graph": [
                    {
                        "@id": "estleg:Source_Node",
                        "estleg:customEnum": {"@id": "estleg:EnumValue"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = validate_seadusloome_sync.validate_graph_closure(
        [path],
        shapes_dir=shapes,
    )

    assert summary["total"] == 0


def test_main_uses_cli_shapes_dir_for_graph_closure_exemptions(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(
        krr,
        [
            {
                "@id": "estleg:Source_Node",
                "@type": ["owl:NamedIndividual"],
                "estleg:customEnum": {"@id": "estleg:EnumValue"},
            }
        ],
    )
    shapes = tmp_path / "shapes"
    shapes.mkdir()
    (shapes / "custom.ttl").write_text(
        """
@prefix estleg: <https://w3id.org/estleg/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .

estleg:CustomShape
    a sh:NodeShape ;
    sh:property [
        sh:path estleg:customEnum ;
        estleg:graphClosureExempt true ;
    ] .
""",
        encoding="utf-8",
    )

    code, output = _run_validator(krr, capsys, shapes_dir=shapes)

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
    iri_a_full = "https://w3id.org/estleg/Prov_FailA"
    iri_b_full = "https://w3id.org/estleg/Prov_FailB"
    assert iri_a_full in output or iri_b_full in output, output
    # Group should aggregate both failures under one row.
    assert "2x LegalProvisionShape" in output or "2x  LegalProvisionShape" in output


# ---------------------------------------------------------------------------
# Historical municipalities loaded into the gate (issue #285a)
# ---------------------------------------------------------------------------


def _write_historical_municipalities(krr_dir: Path, nodes: list[dict]) -> Path:
    """Write ``<repo>/data/ehak/historical_municipalities.jsonld``.

    The file deliberately lives a sibling of ``krr_outputs`` (``krr_dir.parent``)
    to mirror the real source-registry layout the loader special-cases.
    """
    path = krr_dir.parent / "data" / "ehak" / "historical_municipalities.jsonld"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"@context": CONTEXT, "@graph": nodes}), encoding="utf-8"
    )
    return path


def test_collect_inputs_includes_historical_municipalities(tmp_path):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(krr, [])
    hist = _write_historical_municipalities(krr, [])

    inputs, warnings, errors = validate_seadusloome_sync.collect_inputs(krr)

    assert errors == []
    assert hist in inputs, inputs
    # Must not warn when the file is present.
    assert all("historical municipalities" not in w for w in warnings), warnings


def test_collect_inputs_warns_when_historical_municipalities_missing(tmp_path):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(krr, [])
    # Intentionally do not write data/ehak/historical_municipalities.jsonld.

    inputs, warnings, errors = validate_seadusloome_sync.collect_inputs(krr)

    assert errors == []
    assert any("historical municipalities" in w for w in warnings), warnings


def test_historical_municipality_nodes_are_validated_by_the_gate(tmp_path, capsys):
    """A malformed HistoricalMunicipality must now fail the Seadusloome gate.

    Before issue #285a the shape validated zero nodes here, so this would
    have passed vacuously. The successor target is defined in the combined
    artifact to keep graph-closure green; only the shape constraint fires.
    """
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(
        krr,
        [{"@id": "estleg:Municipality_EHAK_0480", "@type": ["owl:NamedIndividual"]}],
    )
    # formerEhakCode violates sh:pattern "^[0-9]{4}$" (too short).
    _write_historical_municipalities(
        krr,
        [
            {
                "@id": "estleg:HistoricalMunicipality_0105",
                "@type": ["owl:NamedIndividual", "estleg:HistoricalMunicipality"],
                "rdfs:label": "Abja vald",
                "estleg:formerEhakCode": "10",
                "estleg:formerName": "Abja vald",
                "estleg:succeededBy": {"@id": "estleg:Municipality_EHAK_0480"},
            }
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code != 0, output
    assert "HistoricalMunicipalityShape" in output, output


def test_well_formed_historical_municipality_passes_the_gate(tmp_path, capsys):
    krr = tmp_path / "krr_outputs"
    _seed_seadusloome_subdirs(krr)
    _write_combined(
        krr,
        [{"@id": "estleg:Municipality_EHAK_0480", "@type": ["owl:NamedIndividual"]}],
    )
    _write_historical_municipalities(
        krr,
        [
            {
                "@id": "estleg:HistoricalMunicipality_0105",
                "@type": ["owl:NamedIndividual", "estleg:HistoricalMunicipality"],
                "rdfs:label": "Abja vald",
                "estleg:formerEhakCode": "0105",
                "estleg:formerName": "Abja vald",
                "estleg:succeededBy": {"@id": "estleg:Municipality_EHAK_0480"},
                "estleg:municipalityType": "vald",
            }
        ],
    )

    code, output = _run_validator(krr, capsys)

    assert code == 0, output


# ---------------------------------------------------------------------------
# Enum-like IRI predicates carry graphClosureExempt uniformly (issue #290)
# ---------------------------------------------------------------------------


def test_enum_iri_predicates_are_graph_closure_exempt():
    """All enum-like IRI predicates must be exempt so they never break closure."""
    exempt = validate_seadusloome_sync.graph_closure_exempt_predicates()

    expected = {
        "estleg:normativeType",
        "estleg:legislativePhase",
        "estleg:draftType",
        "estleg:euDocumentType",
        "estleg:euInstitution",
        "estleg:euCourt",
        # Pre-existing markers must remain in place.
        "estleg:caseType",
        "estleg:decisionType",
        "estleg:euCourtDecisionType",
    }
    assert expected <= exempt, sorted(expected - exempt)
