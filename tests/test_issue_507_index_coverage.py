"""Issue #507 — INDEX body coverage and summary→legalText backfill."""

from __future__ import annotations

import json
from pathlib import Path

import validate_all
from estleg_common import KRR_DIR
from index_body_coverage import (
    EMPTY_SUBSTANTIVE_BASELINE,
    classify_index_entry,
    is_treaty_slug,
    legal_text_from_summary,
    unmarked_empty_entries,
)

REPO = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO / "krr_outputs" / "INDEX.json"


def _load_index() -> dict:
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def _law(index: dict, name: str) -> dict:
    for entry in index.get("laws") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    raise AssertionError(f"INDEX.json has no live law named {name!r}")


def test_is_treaty_slug_ratification_vs_statute():
    assert is_treaty_slug("foo_bar_ratifitseerimise_seadus") is True
    assert is_treaty_slug("andmekogude_seadus") is False


def test_legal_text_from_summary_copies_and_noops():
    node = {
        "@id": "estleg:X_Par_1",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_X"],
        "estleg:summary": "Kehtiv tekst.",
    }
    assert legal_text_from_summary(node) is True
    assert node["estleg:legalText"] == "Kehtiv tekst."
    assert legal_text_from_summary(node) is False

    already = {
        "@id": "estleg:Y_Par_1",
        "@type": ["estleg:LegalProvision"],
        "estleg:summary": "Summary",
        "estleg:legalText": "Original",
    }
    assert legal_text_from_summary(already) is False
    assert already["estleg:legalText"] == "Original"


def test_andmekogude_par_1_has_legal_text():
    path = KRR_DIR / "andmekogude_seadus_peep.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    node = next(
        item
        for item in doc.get("@graph") or []
        if isinstance(item, dict) and item.get("@id") == "estleg:ANDMEK_Par_1"
    )
    text = node.get("estleg:legalText")
    assert isinstance(text, str) and text.strip()


def test_andmekogude_index_entry_has_body():
    entry = _law(_load_index(), "andmekogude_seadus")
    assert entry.get("provisionCount", 0) > 0
    assert entry.get("legalTextCount", 0) > 0
    assert "stubKind" not in entry


def test_unmarked_empty_matches_classify_and_baseline():
    index = _load_index()
    classified = [
        entry["name"]
        for entry in index.get("laws") or []
        if isinstance(entry, dict)
        and classify_index_entry(entry, KRR_DIR).get("stubKind") == "empty"
    ]
    stamped = [
        entry["name"]
        for entry in index.get("laws") or []
        if isinstance(entry, dict) and entry.get("stubKind") == "empty"
    ]
    unmarked = unmarked_empty_entries(index, KRR_DIR)
    assert [entry.get("name") for entry in unmarked] == []
    assert stamped == classified
    assert len(classified) <= EMPTY_SUBSTANTIVE_BASELINE


def test_validate_errors_on_new_empty_non_treaty(tmp_path):
    peep = {
        "@graph": [
            {
                "@id": "estleg:NEW_Map_2026",
                "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
            }
        ]
    }
    (tmp_path / "brand_new_substantive_seadus_peep.json").write_text(
        json.dumps(peep), encoding="utf-8"
    )
    index = {
        "laws": [
            {
                "name": "brand_new_substantive_seadus",
                "files": ["brand_new_substantive_seadus_peep.json"],
            }
        ]
    }
    (tmp_path / "INDEX.json").write_text(json.dumps(index), encoding="utf-8")
    validate_all.reset()
    validate_all.validate_index_body_coverage(tmp_path)
    assert validate_all.errors
    assert any(
        "empty" in message.lower() or "brand_new_substantive_seadus" in message
        for message in validate_all.errors
    )
