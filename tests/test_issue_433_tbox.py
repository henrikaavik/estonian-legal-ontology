"""#433: controlled_vocabulary.jsonld is the canonical default-graph T-Box."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.consolidate_tbox import (
    FORBIDDEN_NO_AXIOM,
    FORBIDDEN_NO_RANGE,
    JUNK_TERMS,
    METADATA_PATH,
    UNRESOLVED_PATH,
    VOCAB_PATH,
    VOCABULARY_IRI,
    build_consolidated_graph,
    graph_nodes,
    has_placeholder_comment,
    index_by_id,
    is_unresolved_individual,
    load_jsonld,
    placeholder_ids,
    project_schema_nodes,
    property_coverage,
    remap_junk_document,
    schema_ids_missing_from_vocab,
    schema_paths,
    schema_term_ids,
    strip_metadata_tbox,
)
from estleg import validate_all

REPO = Path(__file__).resolve().parent.parent


def _vocab_nodes() -> list[dict]:
    return graph_nodes(load_jsonld(VOCAB_PATH))


def test_vocabulary_has_ontology_header() -> None:
    header = index_by_id(_vocab_nodes())[VOCABULARY_IRI]
    types = header.get("@type")
    types = types if isinstance(types, list) else [types]
    assert "owl:Ontology" in types
    assert header.get("owl:versionInfo")


def test_property_domain_range_coverage_meets_dod() -> None:
    both, total, missing = property_coverage(_vocab_nodes())
    assert total >= 160
    assert both / total >= 0.95, (both, total, missing)
    assert set(missing) <= (FORBIDDEN_NO_AXIOM | FORBIDDEN_NO_RANGE)


def test_no_placeholder_comments() -> None:
    assert placeholder_ids(_vocab_nodes()) == []
    assert not any(has_placeholder_comment(node) for node in _vocab_nodes())


def test_junk_terms_removed_from_cv() -> None:
    ids = set(index_by_id(_vocab_nodes()))
    assert not (JUNK_TERMS & ids)


def test_citation_and_act_hierarchy_live_in_cv() -> None:
    idx = index_by_id(_vocab_nodes())
    assert "estleg:Citation" in idx
    assert "owl:Class" in (
        idx["estleg:Citation"]["@type"]
        if isinstance(idx["estleg:Citation"]["@type"], list)
        else [idx["estleg:Citation"]["@type"]]
    )
    law = idx["estleg:Law"]
    subclass = law.get("rdfs:subClassOf")
    subclass_ids = []
    if isinstance(subclass, dict):
        subclass_ids.append(subclass.get("@id"))
    elif isinstance(subclass, list):
        subclass_ids.extend(
            item.get("@id") if isinstance(item, dict) else item for item in subclass
        )
    assert "estleg:Act" in subclass_ids


def test_unresolved_individuals_moved_out_of_cv() -> None:
    parked = [n for n in _vocab_nodes() if is_unresolved_individual(n)]
    assert parked == []
    unresolved = graph_nodes(load_jsonld(UNRESOLVED_PATH))
    assert len(unresolved) == 61
    assert all(is_unresolved_individual(node) for node in unresolved)


def test_metadata_is_dcat_only_and_conforms_to_cv() -> None:
    metadata = load_jsonld(METADATA_PATH)
    assert "@graph" not in metadata
    assert metadata["dcterms:conformsTo"]["@id"] == VOCABULARY_IRI
    types = metadata.get("@type", [])
    assert "dcat:Dataset" in types


def test_four_schemas_are_cv_projections() -> None:
    idx = index_by_id(_vocab_nodes())
    assert schema_ids_missing_from_vocab(idx) == {}
    for path in schema_paths().values():
        ids = schema_term_ids(path)
        projected = project_schema_nodes(idx, ids)
        assert [node["@id"] for node in projected] == ids


def test_validate_canonical_tbox_passes() -> None:
    validate_all.reset()
    validate_all.validate_canonical_tbox()
    assert validate_all.errors == []


def test_build_consolidated_graph_is_idempotent() -> None:
    vocab = load_jsonld(VOCAB_PATH)
    first, unresolved = build_consolidated_graph(vocab)
    second, unresolved2 = build_consolidated_graph({"@graph": first})
    assert [n["@id"] for n in first] == [n["@id"] for n in second]
    assert len(unresolved2) == 0
    both, total, _ = property_coverage(first)
    assert both / total >= 0.95
    assert len(unresolved) == 0  # already moved on disk; rebuild from CV keeps them out


def test_strip_metadata_tbox_drops_named_graph() -> None:
    cleaned = strip_metadata_tbox(
        {
            "@id": "https://example.test/dataset",
            "dcterms:title": "x",
            "@graph": [{"@id": "estleg:Citation", "@type": "owl:Class"}],
        }
    )
    assert "@graph" not in cleaned
    assert cleaned["dcterms:conformsTo"]["@id"] == VOCABULARY_IRI


def test_remap_junk_predicates_to_standard_terms() -> None:
    doc = {
        "@graph": [
            {
                "@id": "estleg:X",
                "estleg:note": "quality note",
                "estleg:scope": "topic",
                "estleg:title": "Title",
            }
        ]
    }
    assert remap_junk_document(doc) is True
    node = doc["@graph"][0]
    assert node["rdfs:comment"] == "quality note"
    assert node["dcterms:abstract"] == "topic"
    assert node["dcterms:title"] == "Title"
    assert "estleg:note" not in node
    assert "estleg:scope" not in node
    assert "estleg:title" not in node


def test_publication_date_domain_is_not_draft_only() -> None:
    """Acts and AmendmentEvents carry publicationDate; DraftLegislation
    domain would RDFS-type them as drafts and trip DraftLegislationShape."""
    node = index_by_id(_vocab_nodes())["estleg:publicationDate"]
    domain = node["rdfs:domain"]
    domain_id = domain.get("@id") if isinstance(domain, dict) else domain
    assert domain_id == "owl:Thing"


def test_inference_unsafe_properties_keep_required_gaps() -> None:
    idx = index_by_id(_vocab_nodes())
    for term in FORBIDDEN_NO_AXIOM:
        node = idx[term]
        assert "rdfs:domain" not in node, term
        assert "rdfs:range" not in node, term
    for term in FORBIDDEN_NO_RANGE:
        node = idx[term]
        assert "rdfs:range" not in node, term


def test_peeps_no_longer_use_junk_predicates() -> None:
    sample = REPO / "krr_outputs" / "volgade_sissenoudmise_peep.json"
    text = sample.read_text(encoding="utf-8")
    assert '"estleg:scope"' not in text
    assert '"estleg:note"' not in text
    doc = json.loads(text)
    assert any("dcterms:abstract" in node for node in doc["@graph"])
