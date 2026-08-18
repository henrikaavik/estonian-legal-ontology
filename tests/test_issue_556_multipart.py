"""Issue #556 — multipart INDEX entries must mark osa-sequence holes."""

from __future__ import annotations

import json
from pathlib import Path

from multipart_coverage import (
    iter_multipart_index_entries,
    unmarked_multipart_gaps,
)

REPO = Path(__file__).resolve().parents[1]
INDEX_PATH = REPO / "krr_outputs" / "INDEX.json"


def _entry_missing_8_9(**extra):
    return {
        "name": "example_code",
        "files": [
            "example_code_osa1_peep.json",
            "example_code_osa2_peep.json",
            "example_code_osa3_peep.json",
            "example_code_osa4_peep.json",
            "example_code_osa5_peep.json",
            "example_code_osa6_peep.json",
            "example_code_osa7_peep.json",
            "example_code_osa10_peep.json",
            "example_code_osa11_peep.json",
            "example_code_osa12_peep.json",
            "example_code_osa13_peep.json",
            "example_code_osa14_peep.json",
            "example_code_map_peep.json",
        ],
        "parts_mapped": ["1", "2", "3", "4", "5", "6", "7", "10", "11", "12", "13", "14"],
        **extra,
    }


def _load_index() -> dict:
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def _law(index: dict, name: str) -> dict:
    for entry in index.get("laws") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    raise AssertionError(f"INDEX.json has no live law named {name!r}")


def test_unmarked_gaps_without_annotation():
    assert set(unmarked_multipart_gaps(_entry_missing_8_9())) == {8, 9}


def test_multipart_gaps_marks_holes():
    entry = _entry_missing_8_9(multipart_gaps=["8", "9"])
    assert unmarked_multipart_gaps(entry) == []


def test_intentionally_skipped_osa_marks_vos_like_hole():
    entry = {
        "name": "volaoigusseadus",
        "files": [
            "volaoigusseadus_map_peep.json",
            "volaoigusseadus_osa1_peep.json",
            "volaoigusseadus_osa2_peep.json",
            "volaoigusseadus_osa3_peep.json",
            "volaoigusseadus_osa4_peep.json",
            "volaoigusseadus_osa5_peep.json",
            "volaoigusseadus_osa7_peep.json",
            "volaoigusseadus_osa8_peep.json",
            "volaoigusseadus_osa9_peep.json",
            "volaoigusseadus_osa10_peep.json",
        ],
        "parts_mapped": ["1", "2", "3", "4", "5", "7", "8", "9", "10"],
        "intentionally_skipped_osa": ["6"],
    }
    assert unmarked_multipart_gaps(entry) == []


def test_tsus_index_annotates_gaps_1_3_5_6():
    entry = _law(_load_index(), "tsiviilseadustiku_uldosa_seadus")
    assert {int(n) for n in entry.get("multipart_gaps") or []} >= {1, 3, 5, 6}
    assert unmarked_multipart_gaps(entry) == []


def test_tsms_index_annotates_gaps_8_9():
    entry = _law(_load_index(), "tsiviilkohtumenetluse_seadustik")
    assert {int(n) for n in entry.get("multipart_gaps") or []} >= {8, 9}
    assert unmarked_multipart_gaps(entry) == []


def test_vos_osa6_is_intentionally_skipped():
    entry = _law(_load_index(), "volaoigusseadus")
    assert {int(n) for n in entry.get("intentionally_skipped_osa") or []} >= {6}
    assert "repealed §578–579" in (entry.get("multipart_gap_note") or "")
    assert unmarked_multipart_gaps(entry) == []


def test_every_index_multipart_entry_has_no_unmarked_gaps():
    leftover = []
    for entry in iter_multipart_index_entries(_load_index()):
        gaps = unmarked_multipart_gaps(entry)
        if gaps:
            leftover.append((entry.get("name"), gaps))
    assert leftover == []
