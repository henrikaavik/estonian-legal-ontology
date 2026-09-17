"""Riigikohus schema enum individuals (#344 remainder)."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "krr_outputs" / "riigikohus" / "riigikohus_schema.json"


def _schema_ids() -> set[str]:
    doc = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return {
        n["@id"]
        for n in doc.get("@graph", [])
        if isinstance(n, dict) and n.get("@id")
    }


def test_schema_does_not_declare_unused_decisiontype_resolution():
    """DecisionType_Resolution is unused in riigikohus peeps — drop it from the schema."""
    assert "estleg:DecisionType_Resolution" not in _schema_ids()


def test_schema_declares_casetype_other():
    """CaseType_Other is used in peeps and must sit with the other CaseType individuals."""
    assert "estleg:CaseType_Other" in _schema_ids()
