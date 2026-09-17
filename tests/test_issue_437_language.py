"""Language-tag policy on the vocabulary (#437).

T-Box only: high-value CV class labels are tagged ``@et`` (and ``@en``
when an English gloss exists). Law peeps stay plain ``xsd:string``.
``estleg_common.CONTEXT`` must not grow a default ``@language``.
"""

from __future__ import annotations

import json
from pathlib import Path

from estleg import estleg_common

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "docs" / "SCHEMA_REFERENCE.md"
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"

HIGH_VALUE_CLASSES = (
    "estleg:Act",
    "estleg:LegalProvision",
    "estleg:DraftLegislation",
    "estleg:CourtDecision",
    "estleg:Chapter",
    "estleg:TopicCluster",
)


def _vocab_index() -> dict[str, dict]:
    doc = json.loads(VOCAB.read_text(encoding="utf-8"))
    return {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }


def _label_objects(node: dict) -> list[dict]:
    raw = node.get("rdfs:label")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def test_schema_reference_mentions_language_tag_policy() -> None:
    text = SCHEMA.read_text(encoding="utf-8")
    assert "Language-tag policy" in text
    assert "@et" in text


def test_act_and_legal_provision_labels_are_lang_tagged_et() -> None:
    idx = _vocab_index()
    for iri in ("estleg:Act", "estleg:LegalProvision"):
        labels = _label_objects(idx[iri])
        assert labels, f"{iri} rdfs:label is not a lang-tagged object"
        langs = {item.get("@language") for item in labels}
        assert "et" in langs, f"{iri} missing @language et: {labels}"
        assert all("@value" in item for item in labels), labels


def test_high_value_class_labels_are_bilingual() -> None:
    idx = _vocab_index()
    for iri in HIGH_VALUE_CLASSES:
        langs = {item.get("@language") for item in _label_objects(idx[iri])}
        assert {"et", "en"} <= langs, f"{iri} langs={langs}"


def test_shared_context_has_no_default_language() -> None:
    assert "@language" not in estleg_common.CONTEXT
