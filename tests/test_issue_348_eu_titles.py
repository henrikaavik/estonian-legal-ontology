"""Corpus pins for #348 EU provenance + titles."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

KRR = Path(__file__).resolve().parent.parent / "krr_outputs"


def _types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    return [raw] if isinstance(raw, str) else list(raw)


def test_curia_judgments_have_dcterms_title() -> None:
    path = KRR / "curia" / "curia_judgments_peep.json"
    if not path.exists():
        pytest.fail(f"missing {path}")
    graph = json.loads(path.read_text(encoding="utf-8")).get("@graph", [])
    decisions = [
        n for n in graph
        if isinstance(n, dict) and "estleg:EUCourtDecision" in _types(n)
    ]
    assert decisions, "no EUCourtDecision nodes in curia_judgments_peep.json"
    missing = [n.get("@id") for n in decisions if "dcterms:title" not in n]
    assert missing == [], (
        f"{len(missing)}/{len(decisions)} CURIA judgments still lack "
        f"dcterms:title (sample {missing[:3]})"
    )


def test_eurlex_directives_have_provenance_and_title() -> None:
    path = KRR / "eurlex" / "eurlex_directives_peep.json"
    if not path.exists():
        pytest.fail(f"missing {path}")
    graph = json.loads(path.read_text(encoding="utf-8")).get("@graph", [])
    acts = [
        n for n in graph
        if isinstance(n, dict) and "estleg:EULegislation" in _types(n)
    ]
    assert acts, "no EULegislation nodes in eurlex_directives_peep.json"
    for field in ("owl:sameAs", "dcterms:source", "eli:id_local", "dcterms:title"):
        missing = sum(1 for n in acts if field not in n)
        assert missing == 0, f"{missing}/{len(acts)} directives missing {field}"
