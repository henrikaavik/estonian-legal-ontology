"""Issue #708: materialise ELI / schema.org triples for no-inference consumers.

The controlled vocabulary aligns estleg terms to ELI and schema.org through
``rdfs:subClassOf`` / ``rdfs:subPropertyOf`` axioms, but the Seadusloome sync
surface queries and validates with no inference, so ``?x a eli:LegalResource``
returned nothing. The combined build now asserts the entailed triples:

* class alignments ride the #519 type rollup
  (``fix_all_issues._EXTERNAL_TYPE_ALIGNMENTS``);
* property alignments are copied by ``fix_all_issues.materialize_eli_alignments``.

Coverage shapes in ``shacl/estonian_legal_shapes.ttl`` notice if either stops.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from estleg import estleg_common, fix_all_issues

ROOT = Path(__file__).resolve().parent.parent
CV = ROOT / "krr_outputs" / "controlled_vocabulary.jsonld"
SHAPES = ROOT / "shacl" / "estonian_legal_shapes.ttl"

ELI = "http://data.europa.eu/eli/ontology#"
SCHEMA = "https://schema.org/"


def _law_nodes() -> list[dict]:
    """A law root, one §, one lõige, a chapter, an expression and an event."""
    return [
        {
            "@id": "estleg:PKS_Map",
            "@type": ["estleg:Law", "estleg:Act"],
            "rdfs:label": "Põhiseaduse kommentaar",
        },
        {
            "@id": "estleg:Chapter_PKS_1",
            "@type": ["estleg:Chapter"],
            "estleg:partOfAct": {"@id": "estleg:PKS_Map"},
        },
        {
            "@id": "estleg:PKS_Par_1",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "estleg:isPartOf": {"@id": "estleg:Chapter_PKS_1"},
            "estleg:partOfAct": {"@id": "estleg:PKS_Map"},
        },
        {
            "@id": "estleg:PKS_Par_1_Lg_1",
            "@type": ["estleg:Subsection", "estleg:LegalProvision"],
            "estleg:parentProvision": {"@id": "estleg:PKS_Par_1"},
            "estleg:partOfAct": {"@id": "estleg:PKS_Map"},
        },
        {
            "@id": "estleg:PKS_Map_Expr_20200101",
            "@type": ["owl:NamedIndividual", "estleg:ActExpression"],
            "estleg:expressionOf": {"@id": "estleg:PKS_Map"},
        },
        {
            "@id": "estleg:AmendmentEvent_PKS_1",
            "@type": ["estleg:AmendmentEvent"],
            "estleg:entryIntoForce": {"@value": "2020-01-01", "@type": "xsd:date"},
        },
    ]


# --- unit: materialize_eli_alignments ----------------------------------------


def test_property_alignments_copied_per_ticket_mapping():
    nodes = _law_nodes()
    stats = fix_all_issues.materialize_eli_alignments(nodes)
    by_id = {n["@id"]: n for n in nodes}

    assert by_id["estleg:PKS_Map_Expr_20200101"]["eli:realizes"] == {"@id": "estleg:PKS_Map"}
    assert by_id["estleg:AmendmentEvent_PKS_1"]["eli:date_entry_in_force"] == {
        "@value": "2020-01-01",
        "@type": "xsd:date",
    }
    # § -> chapter (isPartOf) then act root (partOfAct)
    assert by_id["estleg:PKS_Par_1"]["eli:is_part_of"] == [
        {"@id": "estleg:Chapter_PKS_1"},
        {"@id": "estleg:PKS_Map"},
    ]
    # lõige -> § (parentProvision) then act root (partOfAct), nearest first
    assert by_id["estleg:PKS_Par_1_Lg_1"]["eli:is_part_of"] == [
        {"@id": "estleg:PKS_Par_1"},
        {"@id": "estleg:PKS_Map"},
    ]
    # a container with only partOfAct gets a single (scalar) value
    assert by_id["estleg:Chapter_PKS_1"]["eli:is_part_of"] == {"@id": "estleg:PKS_Map"}
    # the act root carries no source field, so it gains no property
    assert not any(k.startswith("eli:") for k in by_id["estleg:PKS_Map"])

    assert stats == {
        "nodes_eli:realizes": 1,
        "values_eli:realizes": 1,
        "nodes_eli:date_entry_in_force": 1,
        "values_eli:date_entry_in_force": 1,
        "nodes_eli:is_part_of": 3,
        "values_eli:is_part_of": 5,
    }


def test_estleg_sources_are_kept():
    nodes = _law_nodes()
    before = copy.deepcopy(nodes)
    fix_all_issues.materialize_eli_alignments(nodes)
    for old, new in zip(before, nodes):
        for key, value in old.items():
            assert new[key] == value


def test_idempotent_second_run_adds_nothing():
    nodes = _law_nodes()
    fix_all_issues.materialize_eli_alignments(nodes)
    snapshot = json.dumps(nodes, sort_keys=True)
    stats = fix_all_issues.materialize_eli_alignments(nodes)
    assert json.dumps(nodes, sort_keys=True) == snapshot
    assert all(count == 0 for count in stats.values())


def test_existing_target_values_are_merged_not_replaced():
    node = {
        "@id": "estleg:X_Par_1",
        "eli:is_part_of": {"@id": "estleg:Other"},
        "estleg:partOfAct": {"@id": "estleg:X_Map"},
    }
    fix_all_issues.materialize_eli_alignments([node])
    assert node["eli:is_part_of"] == [{"@id": "estleg:Other"}, {"@id": "estleg:X_Map"}]


def test_copied_values_do_not_alias_the_source():
    node = {"@id": "estleg:E", "estleg:expressionOf": {"@id": "estleg:A_Map"}}
    fix_all_issues.materialize_eli_alignments([node])
    node["eli:realizes"]["@id"] = "estleg:Changed"
    assert node["estleg:expressionOf"] == {"@id": "estleg:A_Map"}


def test_stub_gains_only_what_it_carries():
    with_edge = {
        "@id": "estleg:Reg_1_Par_1",
        "@type": ["estleg:LegalProvision"],
        "estleg:partOfAct": {"@id": "estleg:Reg_1_Map"},
        estleg_common.STUB_NODE_MARKER: True,
    }
    without_edge = {
        "@id": "estleg:Reg_2_Par_1_Lg_1",
        "@type": ["estleg:Subsection", "estleg:LegalProvision"],
        estleg_common.STUB_NODE_MARKER: True,
    }
    fix_all_issues.materialize_eli_alignments([with_edge, without_edge])
    assert with_edge["eli:is_part_of"] == {"@id": "estleg:Reg_1_Map"}
    assert "eli:is_part_of" not in without_edge
    # the copied edge is an allowlisted stub edge, so the closure gate accepts it
    preds = {p for p, _ in estleg_common.iter_node_estleg_refs(with_edge)}
    assert preds <= estleg_common.STUB_SEMANTIC_EDGE_PREDICATES


def test_deterministic_across_runs():
    outputs = set()
    for _ in range(5):
        nodes = _law_nodes()
        fix_all_issues.materialize_eli_alignments(nodes)
        outputs.add(json.dumps(nodes))
    assert len(outputs) == 1


# --- vocabulary backing and context ------------------------------------------


def _cv_nodes() -> dict[str, dict]:
    doc = json.loads(CV.read_text(encoding="utf-8"))
    return {n["@id"]: n for n in doc["@graph"] if isinstance(n, dict) and "@id" in n}


def _refs(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [v["@id"] for v in values if isinstance(v, dict) and "@id" in v]


def test_every_class_alignment_is_a_cv_subclass_axiom():
    """Materialised types must be exactly what the CV's axioms entail."""
    cv = _cv_nodes()
    for cls, aligned in fix_all_issues._EXTERNAL_TYPE_ALIGNMENTS.items():
        parents = _refs(cv[cls].get("rdfs:subClassOf"))
        for target in aligned:
            assert target in parents, f"{cls} ⊑ {target} missing from the CV"


def test_every_property_alignment_is_a_cv_subproperty_axiom():
    """Copied properties must be exactly what the CV's axioms entail.

    The three containment rows (partOfAct / isPartOf / parentProvision ⊑
    eli:is_part_of) are declared by the T-Box owner, not by this module.
    """
    cv = _cv_nodes()
    missing = [
        f"{source} ⊑ {target}"
        for source, target in fix_all_issues._ELI_PROPERTY_ALIGNMENTS
        if target not in _refs(cv.get(source, {}).get("rdfs:subPropertyOf"))
    ]
    assert missing == []


def test_context_binds_eli_and_https_schema_like_the_cv():
    cv_ctx = json.loads(CV.read_text(encoding="utf-8"))["@context"]
    assert estleg_common.CONTEXT["eli"] == cv_ctx["eli"] == ELI
    assert estleg_common.CONTEXT["schema"] == cv_ctx["schema"] == SCHEMA


def test_shapes_prefixes_match_the_context():
    text = SHAPES.read_text(encoding="utf-8")
    assert f"@prefix eli: <{ELI}> ." in text
    assert f"@prefix schema: <{SCHEMA}> ." in text


# --- tmp-corpus end to end ---------------------------------------------------


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def test_generate_combined_jsonld_materialises_eli(tmp_path):
    _write(tmp_path / "pks_peep.json", {"@graph": _law_nodes()[:4]})
    _write(tmp_path / "act_expressions_combined.jsonld", {"@graph": [_law_nodes()[4]]})

    fix_all_issues.generate_combined_jsonld(tmp_path)
    out = tmp_path / fix_all_issues.COMBINED_OUTPUT_NAME
    doc = json.loads(out.read_text(encoding="utf-8"))
    by_id = {n["@id"]: n for n in doc["@graph"]}

    assert doc["@context"]["eli"] == ELI
    assert doc["@context"]["schema"] == SCHEMA
    root = by_id["estleg:PKS_Map"]["@type"]
    assert "eli:LegalResource" in root and "schema:Legislation" in root
    assert "eli:LegalExpression" in by_id["estleg:PKS_Map_Expr_20200101"]["@type"]
    assert by_id["estleg:PKS_Map_Expr_20200101"]["eli:realizes"] == {"@id": "estleg:PKS_Map"}
    assert not any(t.startswith("eli:") for t in by_id["estleg:PKS_Par_1"]["@type"])
    assert by_id["estleg:PKS_Par_1_Lg_1"]["eli:is_part_of"] == [
        {"@id": "estleg:PKS_Par_1"},
        {"@id": "estleg:PKS_Map"},
    ]

    # A rebuild is byte-identical (determinism + idempotence through the builder).
    first = out.read_bytes()
    fix_all_issues.generate_combined_jsonld(tmp_path)
    assert out.read_bytes() == first


def test_combined_output_parses_to_eli_and_schema_iris(tmp_path):
    rdflib = pytest.importorskip("rdflib")
    _write(tmp_path / "pks_peep.json", {"@graph": _law_nodes()[:4]})
    fix_all_issues.generate_combined_jsonld(tmp_path)
    graph = rdflib.Graph().parse(
        str(tmp_path / fix_all_issues.COMBINED_OUTPUT_NAME), format="json-ld"
    )
    act = rdflib.URIRef("https://w3id.org/estleg/PKS_Map")
    assert (act, rdflib.RDF.type, rdflib.URIRef(ELI + "LegalResource")) in graph
    assert (act, rdflib.RDF.type, rdflib.URIRef(SCHEMA + "Legislation")) in graph
    lg = rdflib.URIRef("https://w3id.org/estleg/PKS_Par_1_Lg_1")
    assert (lg, rdflib.URIRef(ELI + "is_part_of"), act) in graph


# --- SHACL coverage shapes ---------------------------------------------------

_CTX = {
    "estleg": "https://w3id.org/estleg/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "eli": ELI,
    "schema": SCHEMA,
}
_COVERAGE_SHAPES = {
    "ActEliLegalResourceShape",
    "ActSchemaLegislationShape",
    "ActExpressionEliShape",
    "ProvisionEliPartOfShape",
}
#: The CV axioms the bucket gate's inference="rdfs" relies on, as a fixture.
_TBOX = [
    {"@id": "estleg:Law", "rdfs:subClassOf": {"@id": "estleg:Act"}},
    {
        "@id": "estleg:Act",
        "rdfs:subClassOf": [{"@id": "eli:LegalResource"}, {"@id": "schema:Legislation"}],
    },
    {"@id": "estleg:ActExpression", "rdfs:subClassOf": {"@id": "eli:LegalExpression"}},
    {"@id": "estleg:expressionOf", "rdfs:subPropertyOf": {"@id": "eli:realizes"}},
    {"@id": "estleg:partOfAct", "rdfs:subPropertyOf": {"@id": "eli:is_part_of"}},
    {"@id": "estleg:isPartOf", "rdfs:subPropertyOf": {"@id": "eli:is_part_of"}},
    {"@id": "estleg:parentProvision", "rdfs:subPropertyOf": {"@id": "eli:is_part_of"}},
    {
        "@id": "estleg:MinisterialRegulation",
        "rdfs:subClassOf": {"@id": "estleg:NationalRegulation"},
    },
]


def _active_shapes():
    rdflib = pytest.importorskip("rdflib")
    return rdflib.Graph().parse(str(SHAPES), format="turtle")


def _coverage_failures(nodes: list[dict], inference: str) -> list[str]:
    """Focus nodes the #708 coverage shapes report (other shapes are ignored)."""
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    data = rdflib.Graph().parse(
        data=json.dumps({"@context": _CTX, "@graph": nodes}), format="json-ld"
    )
    shapes = _active_shapes()
    _, results, _ = pyshacl.validate(
        data, shacl_graph=shapes, inference=inference, abort_on_first=False
    )
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    out: list[str] = []
    for result in results.subjects(rdflib.RDF.type, sh.ValidationResult):
        shape = results.value(result, sh.sourceShape)
        # property-shape results point at a blank node; walk up to the node shape
        owner = shapes.value(predicate=sh.property, object=shape) or shape
        if str(owner).rsplit("/", 1)[-1] in _COVERAGE_SHAPES:
            assert results.value(result, sh.resultSeverity) == sh.Warning
            out.append(str(results.value(result, sh.focusNode)).rsplit("/", 1)[-1])
    return sorted(out)


def _materialised(nodes: list[dict]) -> list[dict]:
    nodes = copy.deepcopy(nodes)
    fix_all_issues._apply_type_rollup(nodes)
    fix_all_issues.materialize_eli_alignments(nodes)
    return nodes


def test_shapes_pass_on_materialised_build_without_inference():
    assert _coverage_failures(_materialised(_law_nodes()), "none") == []


def test_shapes_flag_unmaterialised_nodes_without_inference():
    nodes = copy.deepcopy(_law_nodes())
    fix_all_issues._apply_type_rollup(nodes)
    for node in nodes:  # strip the #708 class alignments the rollup added
        node["@type"] = [t for t in node["@type"] if not t.startswith(("eli:", "schema:"))]
    failures = _coverage_failures(nodes, "none")
    assert failures == sorted(
        [
            "PKS_Map",  # eli:LegalResource
            "PKS_Map",  # schema:Legislation
            "PKS_Map_Expr_20200101",  # eli:LegalExpression
            "PKS_Map_Expr_20200101",  # eli:realizes
            "PKS_Par_1",  # eli:is_part_of
            "PKS_Par_1_Lg_1",  # eli:is_part_of
        ]
    )


def test_cv_subclass_axiom_alone_does_not_satisfy_the_type_shape():
    """The shapes check asserted rdf:type, so a loaded T-Box cannot mask a gap."""
    act = {"@id": "estleg:A_Map", "@type": ["estleg:Act", "estleg:Law"]}
    failures = _coverage_failures([act, *_TBOX], "none")
    assert failures == ["A_Map", "A_Map"]


def test_unmaterialised_sources_pass_under_rdfs_with_the_cv_axioms():
    """Bucket gate surface: the per-file peeps entail the triples from the CV."""
    assert _coverage_failures([*_law_nodes(), *_TBOX], "rdfs") == []


def test_raw_regulation_files_are_exempt():
    """regulations/ is loaded raw on the sync surface and never materialised."""
    nodes = [
        {
            "@id": "estleg:Reg_1_Map",
            "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
        },
        {
            "@id": "estleg:Reg_2_Map",
            "@type": ["estleg:MinisterialRegulation"],
        },
        {
            "@id": "estleg:Reg_1_Par_1",
            "@type": ["estleg:LegalProvision", "estleg:KovProvision"],
            "estleg:partOfAct": {"@id": "estleg:Reg_1_Map"},
        },
        {
            "@id": "estleg:Reg_2_Par_1",
            "@type": ["estleg:LegalProvision"],
            "estleg:partOfAct": {"@id": "estleg:Reg_2_Map"},
        },
    ]
    assert _coverage_failures([*nodes, *_TBOX], "none") == []


def test_coverage_shapes_are_core_shacl_and_warnings():
    rdflib = pytest.importorskip("rdflib")
    shapes = rdflib.Graph().parse(str(SHAPES), format="turtle")
    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    est = rdflib.Namespace("https://w3id.org/estleg/")
    for name in _COVERAGE_SHAPES:
        shape = est[name]
        assert (shape, rdflib.RDF.type, sh.NodeShape) in shapes, name
        assert shapes.value(shape, sh.severity) == sh.Warning, name
        assert shapes.value(shape, sh.sparql) is None, name
        assert shapes.value(shape, sh.deactivated) is None, name
