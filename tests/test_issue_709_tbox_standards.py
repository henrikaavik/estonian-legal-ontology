"""#709: T-Box standards repairs -- contract tests on the committed vocabulary.

Covers targetGroup as an ObjectProperty (and the retired targetGroupConcept),
one class per Estonian structural level, the CV prefixes, SKOS typing of the
controlled-value families, Institution ⊑ org:Organization with the SKOS
institutionType value set, and owl:sameAs to the Publications Office
corporate-body authority.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from estleg import consolidate_tbox as tbox
from estleg import retire_target_group_concept as retire

REPO = Path(__file__).resolve().parent.parent
KRR = REPO / "krr_outputs"
SHAPES = REPO / "shacl" / "estonian_legal_shapes.ttl"
CORPORATE_BODY = "http://publications.europa.eu/resource/authority/corporate-body/"


@pytest.fixture(scope="module")
def vocab() -> dict:
    return tbox.load_jsonld(tbox.VOCAB_PATH)


@pytest.fixture(scope="module")
def index(vocab: dict) -> dict[str, dict]:
    return tbox.index_by_id(tbox.graph_nodes(vocab))


def _ids(value: object) -> set[str]:
    return {
        item["@id"]
        for item in tbox.as_list(value)
        if isinstance(item, dict) and isinstance(item.get("@id"), str)
    }


def _label(node: dict, lang: str) -> str:
    for item in tbox.as_list(node.get("rdfs:label")):
        if isinstance(item, dict) and item.get("@language") == lang:
            return item["@value"]
    raise AssertionError(f"{node['@id']} has no @{lang} label")


# --- (3) prefixes ---------------------------------------------------------


@pytest.mark.parametrize("prefix", ["dcat", "org", "eli", "schema", "skos"])
def test_cv_context_declares_every_prefix_its_axioms_use(vocab: dict, prefix: str) -> None:
    assert vocab["@context"][prefix] == tbox.REQUIRED_PREFIXES.get(
        prefix, vocab["@context"][prefix]
    )


def test_rdf_statement_is_a_full_iri_not_an_undeclared_curie(vocab: dict, index: dict) -> None:
    assert "rdf" not in vocab["@context"]  # #392 keeps the rdf line out
    domain = json.dumps(index["estleg:referenceType"]["rdfs:domain"])
    assert tbox.RDF_NS + "Statement" in domain


def test_every_curie_in_the_cv_has_a_declared_prefix(vocab: dict) -> None:
    text = json.dumps(vocab["@graph"])
    used = set(re.findall(r'"([a-z][a-z0-9-]*):[A-Za-z_]', text))
    used -= {"http", "https"}
    assert used <= set(vocab["@context"]), sorted(used - set(vocab["@context"]))


# --- (1) targetGroup --------------------------------------------------------


def test_target_group_is_an_object_property_into_target_group(index: dict) -> None:
    node = index["estleg:targetGroup"]
    assert node["@type"] == ["owl:ObjectProperty"]
    assert node["rdfs:range"] == {"@id": "estleg:TargetGroup"}
    assert node["rdfs:domain"] == {"@id": "owl:Thing"}


def test_target_group_concept_is_deprecated_in_favour_of_target_group(index: dict) -> None:
    node = index["estleg:targetGroupConcept"]
    assert node["owl:deprecated"] is True
    assert node["dcterms:isReplacedBy"] == {"@id": "estleg:targetGroup"}


def test_root_law_peeps_carry_no_target_group_concept() -> None:
    offenders = [
        path.name
        for path in sorted(KRR.glob("*_peep.json"))
        if '"estleg:targetGroupConcept"' in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_materializer_609_no_longer_emits() -> None:
    from estleg import materialize_target_group_concepts_609 as m609

    assert m609.main(["--apply"]) == 2


def test_retire_node_strips_only_a_covered_duplicate() -> None:
    covered = {
        "@id": "estleg:X",
        "estleg:targetGroup": [{"@id": "estleg:TargetGroup_Citizen"}],
        "estleg:targetGroupConcept": [{"@id": "estleg:TargetGroup_Citizen"}],
    }
    assert retire.retire_node(covered) is True
    assert "estleg:targetGroupConcept" not in covered
    assert retire.retire_node(covered) is False

    uncovered = {
        "@id": "estleg:Y",
        "estleg:targetGroup": [{"@id": "estleg:TargetGroup_Citizen"}],
        "estleg:targetGroupConcept": [{"@id": "estleg:TargetGroup_NGO"}],
    }
    assert retire.retire_node(uncovered) is None
    assert "estleg:targetGroupConcept" in uncovered


def test_retire_run_respects_globs_and_is_idempotent(tmp_path: Path) -> None:
    node = {
        "@id": "estleg:X",
        "estleg:targetGroup": [{"@id": "estleg:TargetGroup_Business"}],
        "estleg:targetGroupConcept": [
            {"@id": "https://w3id.org/estleg/TargetGroup_Business"}
        ],
    }
    root = tmp_path / "law_peep.json"
    nested = tmp_path / "regulations" / "kov" / "x_peep.json"
    nested.parent.mkdir(parents=True)
    for path in (root, nested):
        path.write_text(json.dumps({"@graph": [dict(node)]}), encoding="utf-8")

    stats = retire.run(apply=True, krr_dir=tmp_path)
    assert (stats.nodes_stripped, stats.files_changed) == (1, 1)
    assert "targetGroupConcept" not in root.read_text(encoding="utf-8")
    assert "targetGroupConcept" in nested.read_text(encoding="utf-8")

    stats = retire.run(apply=True, krr_dir=tmp_path, globs=("regulations/**/*_peep.json",))
    assert stats.nodes_stripped == 1
    assert retire.run(apply=True, krr_dir=tmp_path, globs=("**/*_peep.json",)).files_changed == 0


# --- (2) structural classes -------------------------------------------------

STRUCTURAL_LEVELS = {
    "estleg:LegalPart": "Osa (struktuuriüksus)",
    "estleg:Chapter": "Peatükk",
    "estleg:Division": "Jagu",
    "estleg:Subdivision": "Jaotis",
    "estleg:Section": "Paragrahv",
    "estleg:Subsection": "Lõige",
}


def test_one_class_per_estonian_structural_level(index: dict) -> None:
    for nid, label in STRUCTURAL_LEVELS.items():
        assert _label(index[nid], "et") == label, nid
    estonian = [_label(index[nid], "et") for nid in (*STRUCTURAL_LEVELS, "estleg:Part")]
    assert len(set(estonian)) == len(estonian)


def test_structural_comments_name_their_own_level(index: dict) -> None:
    for nid, word in {
        "estleg:Division": "jagu",
        "estleg:Subdivision": "jaotis",
        "estleg:Section": "paragrahv",
        "estleg:Chapter": "peatükk",
    }.items():
        comment = tbox.comment_text(index[nid])
        assert comment.lower().startswith(f"a {word}"), (nid, comment)
    assert "jaotis" not in tbox.comment_text(index["estleg:Division"]).split("The next")[0]


def test_no_punkt_class_because_the_parser_emits_no_item_nodes(index: dict) -> None:
    for nid in ("estleg:Punkt", "estleg:Item", "estleg:Point", "estleg:Clause"):
        assert nid not in index
    assert "estleg:itemNumber" in tbox.comment_text(index["estleg:Subsection"])


def _section_nodes(doc: object):
    if isinstance(doc, dict):
        if "estleg:Section" in tbox.as_list(doc.get("@type")):
            yield doc
        for value in doc.values():
            yield from _section_nodes(value)
    elif isinstance(doc, list):
        for item in doc:
            yield from _section_nodes(item)


def test_every_section_instance_already_carries_legal_provision() -> None:
    """Section ⊑ LegalProvision entails nothing new: it is the § class."""
    paths = [
        KRR / "karistusseadustik_eriosa_owl.jsonld",
        KRR / "tsus_osa7_138_169_owl.jsonld",
        *(
            path
            for path in sorted(KRR.glob("*_peep.json"))
            if '"estleg:Section"' in path.read_text(encoding="utf-8")
        ),
    ]
    seen = 0
    for path in paths:
        for node in _section_nodes(tbox.load_jsonld(path)):
            seen += 1
            assert "estleg:LegalProvision" in tbox.as_list(node["@type"]), node["@id"]
    assert seen > 500


# --- (4) SKOS families ------------------------------------------------------


@pytest.mark.parametrize("family", sorted(tbox.SKOS_SCHEMES))
def test_controlled_value_family_is_a_skos_scheme(index: dict, family: str) -> None:
    assert "skos:Concept" in _ids(index[family].get("rdfs:subClassOf"))
    scheme_id = tbox.SKOS_SCHEMES[family][0]
    scheme = index[scheme_id]
    assert scheme["@type"] == ["skos:ConceptScheme"]
    members = {
        nid
        for nid, node in index.items()
        if family in tbox.node_types(node) and "owl:NamedIndividual" in tbox.node_types(node)
    }
    assert members
    assert _ids(scheme["skos:hasTopConcept"]) == members
    for nid in members:
        node = index[nid]
        assert "skos:Concept" in tbox.node_types(node)
        assert node["skos:inScheme"] == {"@id": scheme_id}


@pytest.mark.parametrize(
    "cls",
    ["estleg:Concept", "estleg:LegalConcept", "estleg:TopicCluster", "estleg:GeneralPartConcept"],
)
def test_concept_layers_are_skos_concepts(index: dict, cls: str) -> None:
    assert "skos:Concept" in _ids(index[cls].get("rdfs:subClassOf"))


# --- (5) institutions -------------------------------------------------------


def test_institution_is_an_org_organization(index: dict) -> None:
    assert "org:Organization" in _ids(index["estleg:Institution"].get("rdfs:subClassOf"))


def test_institution_type_tokens_match_the_shape_and_the_skos_notations(index: dict) -> None:
    text = SHAPES.read_text(encoding="utf-8")
    block = text[text.index("sh:path estleg:institutionType") :]
    tokens = set(re.findall(r'"([a-z_]+)"', block[block.index("sh:in") : block.index(")")]))
    assert tokens == set(tbox.INSTITUTION_TYPES)
    for token, (nid, _et, _en) in tbox.INSTITUTION_TYPES.items():
        node = index[nid]
        assert node["skos:notation"] == token
        assert node["skos:inScheme"] == {"@id": "estleg:InstitutionTypeScheme"}
    assert index["estleg:institutionType"]["rdfs:range"] == {"@id": "xsd:string"}


# --- (6) EU corporate bodies ------------------------------------------------


def test_eu_institutions_link_to_the_corporate_body_authority(index: dict) -> None:
    cache = tbox.load_jsonld(tbox.EU_CORPORATE_BODY_PATH)
    individuals = {
        nid
        for nid, node in index.items()
        if "estleg:EUInstitution" in tbox.node_types(node)
        and "owl:NamedIndividual" in tbox.node_types(node)
    }
    assert {row["individual"] for row in cache["institutions"]} == individuals
    linked = {nid for nid in individuals if "owl:sameAs" in index[nid]}
    expected = {row["individual"] for row in cache["institutions"] if row["inAuthorityTable"]}
    assert linked == expected
    assert len(linked) == cache["matched"] == 46
    for row in cache["institutions"]:
        if row["inAuthorityTable"]:
            assert index[row["individual"]]["owl:sameAs"] == {
                "@id": f"{CORPORATE_BODY}{row['code']}"
            }


def test_eurlex_schema_carries_the_same_links(index: dict) -> None:
    schema = tbox.load_jsonld(KRR / "eurlex" / "eurlex_schema.json")
    for node in tbox.graph_nodes(schema):
        if "owl:sameAs" in index.get(node["@id"], {}):
            assert node.get("owl:sameAs") == index[node["@id"]]["owl:sameAs"]


# --- builder mechanics ------------------------------------------------------


def test_merge_fills_individual_facts_a_placeholder_lacks() -> None:
    placeholder = {
        "@id": "estleg:CaseType_Other",
        "@type": ["owl:NamedIndividual", "estleg:CaseType"],
        "rdfs:label": [{"@value": "Muu", "@language": "et"}],
    }
    source = {**placeholder, "estleg:caseTypeCode": "Other"}
    merged = tbox.merge_node(placeholder, source)
    assert merged["estleg:caseTypeCode"] == "Other"
    prop = {"@id": "estleg:p", "@type": ["owl:DatatypeProperty"]}
    assert "estleg:extra" not in tbox.merge_node(prop, {**prop, "estleg:extra": 1})


def test_committed_cv_is_a_builder_fixpoint(vocab: dict) -> None:
    graph, unresolved = tbox.build_consolidated_graph(vocab)
    assert unresolved == []
    assert graph == vocab["@graph"]
