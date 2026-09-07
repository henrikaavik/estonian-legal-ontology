"""Issue #377 — no over-narrow rdfs:domain/range that mint types under inference=rdfs."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"

NO_DOMAIN_RANGE = (
    "estleg:hasSection",
    "estleg:hasProvision",
    "estleg:coversConcept",
)


def _vocab_index() -> dict[str, dict]:
    doc = json.loads(VOCAB.read_text(encoding="utf-8"))
    return {n["@id"]: n for n in doc["@graph"] if isinstance(n, dict) and n.get("@id")}


def _has_type(node: dict, owl_type: str) -> bool:
    t = node.get("@type")
    if isinstance(t, list):
        return owl_type in t
    return t == owl_type


def test_hassection_hasprovision_coversconcept_have_no_domain_or_range():
    idx = _vocab_index()
    for term in NO_DOMAIN_RANGE:
        node = idx.get(term)
        assert node is not None, f"{term} missing from controlled_vocabulary.jsonld"
        assert "rdfs:domain" not in node, f"{term} must not carry rdfs:domain (#377)"
        assert "rdfs:range" not in node, f"{term} must not carry rdfs:range (#377)"


def test_committed_tsus_owl_drops_inference_unsafe_ranges() -> None:
    path = (
        Path(__file__).resolve().parent.parent
        / "krr_outputs/tsus_osa7_138_169_owl.jsonld"
    )
    graph = json.loads(path.read_text(encoding="utf-8")).get("@graph", [])
    by_id = {n["@id"]: n for n in graph if isinstance(n, dict) and "@id" in n}
    ns = "https://w3id.org/estleg/"
    for frag in ("hasSection", "hasProvision", "coversConcept"):
        node = by_id[f"{ns}{frag}"]
        assert "rdfs:domain" not in node
        assert "rdfs:range" not in node


def test_interprets_eu_law_has_no_range() -> None:
    node = _vocab_index().get("estleg:interpretsEULaw")
    assert node is not None, "estleg:interpretsEULaw missing from controlled_vocabulary.jsonld (#418)"
    assert _has_type(node, "owl:ObjectProperty")
    assert "rdfs:range" not in node, (
        "estleg:interpretsEULaw must not carry rdfs:range — curia bucket objects "
        "are often bare estleg:EU_* stubs (#418/#377)"
    )
