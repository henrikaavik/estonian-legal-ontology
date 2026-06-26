"""TBox / SHACL / standards-conformance tests for the Cycle-2 Round-1/4
schema-correctness tickets.

Coverage:

* #563 — the EU-transposition + cross-border-harmonisation predicates and the
  five enum vocabularies (LegislativePhase/DraftType/DecisionType/
  EUDocumentType/EUCourt, with their individuals) used to be defined ONLY in
  the per-bucket ``*_schema.json`` files, which the combined builder does not
  merge and the SHACL bucket collectors do not load. They are now folded into
  ``controlled_vocabulary.jsonld`` (loaded into every bucket), so the shipped
  graph defines the predicates and the five previously-dead enum NodeShapes
  bind to real, labelled nodes instead of validating zero nodes.
* #566 — structural classes (estleg:Section / estleg:GeneralPartConcept /
  estleg:LegalPart / estleg:ProcedureStage) used as ``@type`` in the corpus but
  absent from the curated TBox are now declared in the controlled vocabulary.
* #570 — estleg:ActTemporalShape now enforces ``rdfs:label`` (sh:minCount 1) on
  statute roots, which were otherwise label-unguarded.
* #612 — estleg:EUCourtDecisionShape now constrains estleg:ecliIdentifier with
  an ECLI ``sh:pattern`` (alongside the pre-existing sh:maxCount 1).

The fold-in is reconciled with the cross-file ``@id``-uniqueness gate
(validate_all.validate_id_uniqueness): the new shared TBox ids are whitelisted
exactly as the #562 core-class fold-in was.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_all  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SHAPES = REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"
VOCAB = REPO_ROOT / "krr_outputs" / "controlled_vocabulary.jsonld"
METADATA = REPO_ROOT / "metadata.jsonld"

CONTEXT = {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}


def _vocab_index() -> dict[str, dict]:
    doc = json.loads(VOCAB.read_text(encoding="utf-8"))
    return {n["@id"]: n for n in doc["@graph"] if isinstance(n, dict) and n.get("@id")}


def _has_type(node: dict, owl_type: str) -> bool:
    t = node.get("@type")
    if isinstance(t, list):
        return owl_type in t
    return t == owl_type


def _validate(graph_json: dict) -> tuple[bool, str]:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    g = rdflib.Graph()
    g.parse(data=json.dumps(graph_json), format="json-ld")
    shapes = rdflib.Graph().parse(str(SHAPES), format="turtle")
    conforms, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
    return conforms, str(msg)


# ===========================================================================
# #563 — transposition + harmonisation predicates folded into the TBox
# ===========================================================================

TRANSPOSITION_OBJECT_PROPS = ["estleg:transposesDirective", "estleg:transposedBy"]
TRANSPOSITION_DATATYPE_PROPS = ["estleg:transpositionStatus"]
HARMONISATION_OBJECT_PROPS = [
    "estleg:harmonisedWith",
    "estleg:harmonises",
    "estleg:sharedDirective",
]
HARMONISATION_DATATYPE_PROPS = ["estleg:memberStateCode", "estleg:nationalCelex"]

# NONE of the transposition/harmonisation object properties carry rdfs:range.
# Each crosses a SHACL bucket boundary (estleg:Act <-> EU directive /
# estleg:HarmonisationLink), and the buckets validate with rdfs inference, so an
# rdfs:range would RDFS-type the bare cross-reference stub on the OBJECT side as a
# shaped class (estleg:Act / estleg:EULegislation / estleg:HarmonisationLink) and
# trip that class's label / celexNumber / euDocumentType minCount — the #570 +
# #563 false-fail (776 spurious violations in the laws bucket). The SUBJECT side is
# typed safely via rdfs:domain; canonical object typing comes from the object's own
# home-bucket node.
_OBJECT_PROPS_WITH_INVERSE = {
    "estleg:transposedBy",
    "estleg:harmonises",
    "estleg:harmonisedWith",
}


@pytest.mark.parametrize("term", TRANSPOSITION_OBJECT_PROPS + HARMONISATION_OBJECT_PROPS)
def test_transposition_harmonisation_object_props_declared(term):
    node = _vocab_index().get(term)
    assert node is not None, f"{term} missing from controlled_vocabulary.jsonld (#563)"
    assert _has_type(node, "owl:ObjectProperty"), f"{term} is not an owl:ObjectProperty"
    assert node.get("rdfs:label"), f"{term} lacks rdfs:label"
    assert node.get("rdfs:comment"), f"{term} lacks rdfs:comment"
    assert node.get("rdfs:domain"), f"{term} lacks rdfs:domain"
    # Must NOT carry rdfs:range: it would RDFS-type the bare cross-bucket object
    # stub as a shaped class and false-fail the laws/eurlex SHACL bucket (#570/#563).
    assert "rdfs:range" not in node, (
        f"{term} must not carry rdfs:range — under the SHACL buckets' rdfs "
        f"inference it would type cross-reference stubs and trip the target "
        f"class's minCount (#570/#563)"
    )
    if term in _OBJECT_PROPS_WITH_INVERSE:
        assert node.get("owl:inverseOf"), f"{term} lacks owl:inverseOf"


@pytest.mark.parametrize("term", TRANSPOSITION_DATATYPE_PROPS + HARMONISATION_DATATYPE_PROPS)
def test_transposition_harmonisation_datatype_props_declared(term):
    node = _vocab_index().get(term)
    assert node is not None, f"{term} missing from controlled_vocabulary.jsonld (#563)"
    assert _has_type(node, "owl:DatatypeProperty"), f"{term} is not an owl:DatatypeProperty"
    assert node.get("rdfs:label"), f"{term} lacks rdfs:label"


def test_transposition_predicates_seen_by_vocabulary_gate():
    """The vocabulary-coverage gate must now recognise the transposition +
    harmonisation predicates — previously they were defined only in the
    unmerged ``*_schema.json`` files, so combined used them undefined."""
    defined = validate_all.collect_defined_vocabulary_terms()
    for term in (
        TRANSPOSITION_OBJECT_PROPS
        + TRANSPOSITION_DATATYPE_PROPS
        + HARMONISATION_OBJECT_PROPS
        + HARMONISATION_DATATYPE_PROPS
    ):
        assert term in defined, f"{term} not seen by collect_defined_vocabulary_terms()"


# ===========================================================================
# #563 — the five formerly-dead enum vocabularies now bind real, labelled nodes
# ===========================================================================

ENUM_CLASSES = [
    "estleg:LegislativePhase",
    "estleg:DraftType",
    "estleg:DecisionType",
    "estleg:EUDocumentType",
    "estleg:EUCourt",
]


@pytest.mark.parametrize("cls", ENUM_CLASSES)
def test_enum_class_declared_in_vocabulary(cls):
    node = _vocab_index().get(cls)
    assert node is not None, f"{cls} missing from controlled_vocabulary.jsonld (#563)"
    assert _has_type(node, "owl:Class"), f"{cls} is not an owl:Class"


@pytest.mark.parametrize("cls", ENUM_CLASSES)
def test_enum_class_has_labelled_individuals(cls):
    """Each enum class must carry at least one owl:NamedIndividual in the TBox,
    and every such individual must carry an rdfs:label — otherwise the matching
    ``rdfs:label sh:minCount 1`` enum NodeShape would either evaluate zero nodes
    (the original #563 false-green) or fail."""
    idx = _vocab_index()
    individuals = [
        n
        for n in idx.values()
        if _has_type(n, "owl:NamedIndividual") and _has_type(n, cls)
    ]
    assert individuals, f"{cls} has no owl:NamedIndividual instances in the TBox (#563)"
    for ind in individuals:
        assert ind.get("rdfs:label"), f"{ind['@id']} ({cls}) lacks rdfs:label"


def test_enum_shapes_bind_and_conform_on_vocabulary_only():
    """Loading ONLY controlled_vocabulary.jsonld (the file appended to every
    SHACL bucket) against the shapes must (a) conform and (b) give the five enum
    NodeShapes real targets — the core #563 fix: dead shapes become live.
    """
    rdflib = pytest.importorskip("rdflib")
    pyshacl = pytest.importorskip("pyshacl")
    from rdflib import RDF, Namespace

    g = rdflib.Graph()
    g.parse(str(VOCAB), format="json-ld")
    shapes = rdflib.Graph().parse(str(SHAPES), format="turtle")
    ok, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
    assert ok, f"controlled_vocabulary.jsonld no longer conforms to the shapes:\n{msg}"

    est = Namespace("https://data.riik.ee/ontology/estleg#")
    for cls in ("LegislativePhase", "DraftType", "DecisionType", "EUDocumentType", "EUCourt"):
        targets = set(g.subjects(RDF.type, est[cls]))
        assert targets, f"enum shape for estleg:{cls} still has zero targets (#563)"


# ===========================================================================
# #566 — missing structural classes declared in the curated TBox
# ===========================================================================

STRUCTURAL_CLASSES = [
    "estleg:Section",
    "estleg:GeneralPartConcept",
    "estleg:LegalPart",
    "estleg:ProcedureStage",
]


@pytest.mark.parametrize("cls", STRUCTURAL_CLASSES)
def test_structural_class_declared_in_vocabulary(cls):
    node = _vocab_index().get(cls)
    assert node is not None, f"{cls} missing from controlled_vocabulary.jsonld (#566)"
    assert _has_type(node, "owl:Class"), f"{cls} is not an owl:Class"
    assert node.get("rdfs:label"), f"{cls} lacks rdfs:label"
    assert node.get("rdfs:comment"), f"{cls} lacks rdfs:comment"


# ===========================================================================
# #570 — ActTemporalShape enforces rdfs:label on statute roots
# ===========================================================================

_ACT_VALID = {
    "@context": CONTEXT,
    "@id": "estleg:TESTACT_Map_2026",
    "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
    "rdfs:label": "Test Act (testseadus)",
}


class TestActLabelRequirement:
    def test_act_with_label_conforms_without_warning(self):
        node = dict(_ACT_VALID)
        ok, msg = _validate(node)
        assert ok, msg
        # A labelled act must not even raise the label warning.
        assert "->rdfs:label" not in msg

    def test_act_without_label_rejected(self):
        # #570: an explicitly-typed estleg:Act with no rdfs:label is a real
        # Violation. (Cross-corpus reference stubs are not RDFS-typed as Act —
        # estleg:harmonises / estleg:transposedBy carry no rdfs:range — so this
        # rule never fires on them; see test_act_stub_reference_not_typed_as_act.)
        node = dict(_ACT_VALID)
        del node["rdfs:label"]
        ok, msg = _validate(node)
        assert not ok, "ActTemporalShape must reject an Act with no rdfs:label (#570)"
        assert "rdfs:label" in msg

    def test_act_stub_reference_not_typed_as_act(self):
        # A bare cross-reference to an act (as the object of estleg:harmonises)
        # must NOT be RDFS-inferred into estleg:Act, otherwise the #570 label
        # rule would fire on a node that legitimately has no local label.
        graph = {
            "@context": CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:Harmonisation_TEST",
                    "@type": ["owl:NamedIndividual", "estleg:HarmonisationLink"],
                    "rdfs:label": "Harmonisation: TEST",
                    "estleg:harmonises": {"@id": "estleg:SOMEACT_Map_2026"},
                }
            ],
        }
        ok, msg = _validate(graph)
        assert ok, f"a harmonises cross-reference stub must not be flagged (#570/#563):\n{msg}"

    def test_shape_text_declares_label_min_count(self):
        text = SHAPES.read_text(encoding="utf-8")
        act_block = text.split("estleg:ActTemporalShape", 1)[1].split("# Shape for LegalProvision", 1)[0]
        assert "rdfs:label" in act_block
        assert "sh:minCount 1" in act_block


# ===========================================================================
# #612 — ECLI syntax pattern on estleg:ecliIdentifier
# ===========================================================================

_ECLI_BASE = {
    "@context": CONTEXT,
    "@id": "estleg:EUCJ_TEST_1",
    "@type": "estleg:EUCourtDecision",
    "rdfs:label": "Test otsus",
    "estleg:celexNumber": "62016CJ0001",
    "estleg:euCourtDecisionType": {"@id": "estleg:EUDecType_Judgment"},
    "estleg:euCourt": {"@id": "estleg:EUCourt_CourtOfJustice"},
}


class TestEcliPattern:
    def test_valid_ecli_conforms(self):
        node = dict(_ECLI_BASE)
        node["estleg:ecliIdentifier"] = "ECLI:EU:C:2016:758"
        ok, msg = _validate(node)
        assert ok, msg

    def test_missing_ecli_conforms(self):
        # No sh:minCount: a decision the source lacks an ECLI for must still pass
        # (we do not fabricate identifiers — #612).
        ok, msg = _validate(dict(_ECLI_BASE))
        assert ok, msg

    def test_malformed_ecli_rejected(self):
        node = dict(_ECLI_BASE)
        node["estleg:ecliIdentifier"] = "NOT-AN-ECLI"
        ok, _ = _validate(node)
        assert not ok, "EUCourtDecisionShape must reject a malformed ECLI (#612)"

    def test_lowercase_country_ecli_rejected(self):
        node = dict(_ECLI_BASE)
        node["estleg:ecliIdentifier"] = "ECLI:eu:C:2016:758"
        ok, _ = _validate(node)
        assert not ok

    def test_shape_text_declares_ecli_pattern(self):
        text = SHAPES.read_text(encoding="utf-8")
        assert 'sh:pattern "^ECLI:' in text


# ===========================================================================
# #563/#566 — the fold-in must not break the cross-file @id-uniqueness gate
# ===========================================================================


def test_folded_ids_are_whitelisted_against_uniqueness_gate():
    """Every @id newly shared between controlled_vocabulary.jsonld and a
    subcorpus ``*_schema.json`` / peep must be in validate_id_uniqueness's
    shared-class allowlist, or the cross-file uniqueness gate would flag it as a
    semantic collision (the #562 reconciliation pattern)."""
    import inspect

    src = inspect.getsource(validate_all.validate_id_uniqueness)
    for term in (
        TRANSPOSITION_OBJECT_PROPS
        + TRANSPOSITION_DATATYPE_PROPS
        + HARMONISATION_OBJECT_PROPS
        + HARMONISATION_DATATYPE_PROPS
        + ENUM_CLASSES
        + ["estleg:GeneralPartConcept", "estleg:ProcedureStage"]
    ):
        assert f'"{term}"' in src, f"{term} not whitelisted in validate_id_uniqueness (#563/#566)"


# ===========================================================================
# #611 — schema.org Dataset projection on the dataset header
# ===========================================================================


def _metadata() -> dict:
    return json.loads(METADATA.read_text(encoding="utf-8"))


def test_dataset_typed_as_schema_dataset():
    doc = _metadata()
    types = doc.get("@type", [])
    assert "schema:Dataset" in types, "dataset header is not typed schema:Dataset (#611)"
    # the existing DCAT/OWL typings must be preserved
    assert "dcat:Dataset" in types
    assert "owl:Ontology" in types


def test_schema_org_fields_present():
    doc = _metadata()
    for key in (
        "schema:name",
        "schema:description",
        "schema:license",
        "schema:keywords",
        "schema:creator",
        "schema:identifier",
        "schema:url",
        "schema:version",
    ):
        assert key in doc, f"{key} missing from the dataset header (#611)"


def test_schema_org_fields_stay_in_sync_with_canonical_metadata():
    """The schema.org projection must mirror the canonical DCAT/OWL fields so
    the two metadata surfaces cannot drift (acceptance criterion on #611)."""
    doc = _metadata()
    assert doc["schema:version"] == doc["owl:versionInfo"]
    assert doc["schema:license"]["@id"] == doc["dcterms:license"]["@id"]
    assert doc["schema:identifier"] == doc["@id"]
    en_title = next(
        t["@value"] for t in doc["dcterms:title"] if t.get("@language") == "en"
    )
    assert doc["schema:name"] == en_title


def test_metadata_parses_as_jsonld_with_schema_dataset_type():
    rdflib = pytest.importorskip("rdflib")
    from rdflib import RDF, Namespace

    g = rdflib.Graph()
    g.parse(str(METADATA), format="json-ld")
    schema = Namespace("http://schema.org/")
    ds = rdflib.URIRef(_metadata()["@id"])
    assert (ds, RDF.type, schema.Dataset) in g
