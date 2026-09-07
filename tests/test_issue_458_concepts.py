"""#458 — concepts-layer hygiene: drop kehtetu noise, pin scheme membership.

Plural fold: 12 safe canonical pairs matched by exact prefLabel suffix ``-d``
(0 ``-te`` pairs) among remaining ``estleg:Concept`` nodes. The fold
threshold is 3, so the 12 pairs were merged (plural prefLabel →
``skos:altLabel`` on the singular; plural node deleted).
"""
from __future__ import annotations

import json

import pytest

from estleg.extract_legal_concepts import (
    CONCEPTS_DIR,
    _strip_term_brackets,
    is_noise_term,
    strip_kehtetu_concepts,
)

CONCEPTS_FILE = CONCEPTS_DIR / "concepts_combined.jsonld"


def _types(node: dict) -> set[str]:
    raw = node.get("@type", [])
    if isinstance(raw, str):
        return {raw}
    return {t for t in (raw or []) if isinstance(t, str)}


def _pref_values(node: dict) -> list[str]:
    raw = node.get("skos:prefLabel")
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("@value"), str):
            out.append(item["@value"])
        elif isinstance(item, str):
            out.append(item)
    return out


def test_is_noise_term_kehtetu_is_true():
    assert is_noise_term("kehtetu") is True


def test_strip_kehtetu_concepts_removes_node_and_incoming_links():
    """Fixture graph: kehtetu node + incoming links go; a real concept stays."""
    real = {
        "@id": "estleg:Concept_isik",
        "@type": ["owl:NamedIndividual", "estleg:Concept", "skos:Concept"],
        "skos:prefLabel": {"@value": "isik", "@language": "et"},
        "rdfs:label": "isik",
        "skos:inScheme": {"@id": "estleg:LegalConceptScheme"},
        "skos:closeMatch": {"@id": "estleg:Concept_act_kehtetu"},
        "skos:exactMatch": [{"@id": "estleg:Concept_act_kehtetu"}],
    }
    noise_pref = {
        "@id": "estleg:Concept_act_kehtetu",
        "@type": ["owl:NamedIndividual", "estleg:LegalConcept", "skos:Concept"],
        "skos:prefLabel": {"@value": "kehtetu", "@language": "et"},
        "rdfs:label": "kehtetu (act)",
        "estleg:definesConcept": {"@id": "estleg:Concept_isik"},
    }
    noise_bracket = {
        "@id": "estleg:Concept_other_repealed",
        "@type": ["skos:Concept"],
        "skos:prefLabel": "[kehtetu]",
    }
    noise_mid_id = {
        "@id": "estleg:Concept_act_kehtetu_act_2_3",
        "@type": ["estleg:LegalConcept", "skos:Concept"],
        "skos:prefLabel": {"@value": "Kehtetu", "@language": "et"},
    }
    linker = {
        "@id": "estleg:Concept_muu",
        "@type": ["estleg:Concept"],
        "skos:prefLabel": {"@value": "muu", "@language": "et"},
        "estleg:definesConcept": {"@id": "estleg:Concept_act_kehtetu"},
        "skos:closeMatch": [
            {"@id": "estleg:Concept_isik"},
            {"@id": "estleg:Concept_act_kehtetu"},
        ],
        "skos:exactMatch": {"@id": "estleg:Concept_act_kehtetu"},
    }
    graph = [real, noise_pref, noise_bracket, noise_mid_id, linker]
    removed = strip_kehtetu_concepts(graph)
    assert removed == 3
    ids = [n["@id"] for n in graph]
    assert ids == ["estleg:Concept_isik", "estleg:Concept_muu"]
    assert "skos:closeMatch" not in graph[0]
    assert "skos:exactMatch" not in graph[0]
    assert "estleg:definesConcept" not in graph[1]
    assert "skos:exactMatch" not in graph[1]
    close = graph[1]["skos:closeMatch"]
    if isinstance(close, dict):
        close = [close]
    assert close == [{"@id": "estleg:Concept_isik"}]


@pytest.fixture(scope="module")
def concepts_graph() -> list[dict]:
    doc = json.loads(CONCEPTS_FILE.read_text(encoding="utf-8"))
    graph = doc.get("@graph")
    assert isinstance(graph, list)
    return graph


def test_corpus_zero_kehtetu_preflabel_and_ids(concepts_graph):
    """0 concepts with prefLabel kehtetu; 0 @ids containing ``_kehtetu``."""
    leftover_pref: list[str] = []
    leftover_ids: list[str] = []
    for node in concepts_graph:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id", "")
        if isinstance(nid, str) and "_kehtetu" in nid.casefold():
            leftover_ids.append(nid)
        types = _types(node)
        if not (types & {"estleg:Concept", "estleg:LegalConcept", "skos:Concept"}):
            continue
        for pref in _pref_values(node):
            if _strip_term_brackets(pref.strip().casefold()) == "kehtetu":
                leftover_pref.append(nid or pref)
    assert leftover_pref == []
    assert leftover_ids == []


def test_corpus_every_concept_has_inscheme(concepts_graph):
    """Every remaining skos:Concept / estleg:Concept has skos:inScheme."""
    missing = [
        node.get("@id")
        for node in concepts_graph
        if isinstance(node, dict)
        and (_types(node) & {"skos:Concept", "estleg:Concept"})
        and not node.get("skos:inScheme")
    ]
    assert missing == []


def test_corpus_concept_5imbsusteem_absent(concepts_graph):
    ids = {
        node.get("@id")
        for node in concepts_graph
        if isinstance(node, dict)
    }
    assert "estleg:Concept_5imbsusteem" not in ids
