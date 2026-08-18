"""#555: published legal facts, not parser-vs-fixture plumbing.

Each row in ``golden_facts/published_facts.json`` is a hand-checked
(file, node, field, expected) triple from committed peeps. The default
CI job runs these without git-LFS. Combined-graph facts stay under
``@pytest.mark.corpus``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KRR = REPO / "krr_outputs"
FACTS = Path(__file__).parent / "golden_facts" / "published_facts.json"


def _load_facts() -> list[dict]:
    return json.loads(FACTS.read_text(encoding="utf-8"))


def _node_value(node: dict, field: str):
    return node.get(field)


def _as_ids(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, str):
        return {value}
    ids: set[str] = set()
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("@id"), str):
            ids.add(item["@id"])
        elif isinstance(item, str):
            ids.add(item)
    return ids


def _resolve_node(doc: dict, fact: dict) -> dict:
    graph = doc.get("@graph") or []
    if fact.get("act_root"):
        for node in graph:
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:Act" in types or "owl:Ontology" in types:
                return node
        raise AssertionError(f"no act root in {fact['file']}")
    target = fact["id"]
    for node in graph:
        if node.get("@id") == target:
            return node
    raise AssertionError(f"{target} missing from {fact['file']}")


def test_golden_facts_file_has_at_least_twenty_rows() -> None:
    facts = _load_facts()
    assert len(facts) >= 20, f"only {len(facts)} golden facts — #555 wants 20+"


@pytest.mark.parametrize("fact", _load_facts(), ids=lambda f: f"{f['file']}:{f.get('id')}:{f['field']}")
def test_published_golden_fact(fact: dict) -> None:
    doc = json.loads((KRR / fact["file"]).read_text(encoding="utf-8"))
    node = _resolve_node(doc, fact)
    value = _node_value(node, fact["field"])
    if "expected_contains" in fact:
        ids = _as_ids(value)
        assert fact["expected_contains"] in ids, (
            f"{fact['id']} {fact['field']} lost {fact['expected_contains']}: {ids}"
        )
        return
    actual = value
    if isinstance(actual, dict) and "@value" in actual:
        actual = actual["@value"]
    assert actual == fact["expected"], (
        f"{fact.get('id')} {fact['field']} drifted: {actual!r} != {fact['expected']!r}"
    )
