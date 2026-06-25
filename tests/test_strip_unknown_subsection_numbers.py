"""Tests for strip_unknown_subsection_numbers.py (#583).

Covers the placeholder-detection predicate, in-place doc stripping,
the real-number / no-key no-op paths, the value-object shape, and
idempotency (a re-run changes nothing).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import strip_unknown_subsection_numbers as mod
from strip_unknown_subsection_numbers import (
    SUBSECTION_NUMBER_KEY,
    _is_unknown_placeholder,
    process_file,
    strip_doc,
)


# ── _is_unknown_placeholder ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Unknown_1", True),
        ("Unknown_5", True),
        ("Unknown_12", True),
        ({"@value": "Unknown_3"}, True),
        ("Unknown", False),  # no numeric suffix
        ("Unknown_", False),  # suffix without a digit
        ("Unknown_1a", False),  # trailing non-digit
        ("1", False),
        ("1²", False),
        ("5¹", False),
        ("", False),
        ({"@value": "1"}, False),
        (None, False),
        (5, False),
        ("xUnknown_1", False),  # must anchor at start
    ],
)
def test_is_unknown_placeholder(value, expected):
    assert _is_unknown_placeholder(value) is expected


# ── strip_doc ──────────────────────────────────────────────────────────────


def test_strip_doc_removes_only_placeholder():
    doc = {
        "@graph": [
            {"@id": "estleg:X_Lg_Unknown_1", SUBSECTION_NUMBER_KEY: "Unknown_1"},
            {"@id": "estleg:X_Lg_1", SUBSECTION_NUMBER_KEY: "1"},
            {"@id": "estleg:X_Lg_2", SUBSECTION_NUMBER_KEY: "2¹"},
            {"@id": "estleg:X_Lg_Unknown_2", SUBSECTION_NUMBER_KEY: "Unknown_2"},
            {"@id": "estleg:X_NoNumber"},  # no key at all
        ]
    }
    changed = strip_doc(doc)
    assert changed == 2
    g = doc["@graph"]
    # placeholders stripped, @id preserved
    assert SUBSECTION_NUMBER_KEY not in g[0]
    assert g[0]["@id"] == "estleg:X_Lg_Unknown_1"
    assert SUBSECTION_NUMBER_KEY not in g[3]
    # real numbers untouched
    assert g[1][SUBSECTION_NUMBER_KEY] == "1"
    assert g[2][SUBSECTION_NUMBER_KEY] == "2¹"
    # no-key node untouched
    assert "@id" in g[4]


def test_strip_doc_value_object_shape():
    doc = {"@graph": [{"@id": "x", SUBSECTION_NUMBER_KEY: {"@value": "Unknown_7"}}]}
    assert strip_doc(doc) == 1
    assert SUBSECTION_NUMBER_KEY not in doc["@graph"][0]


def test_strip_doc_no_graph_is_safe():
    assert strip_doc({}) == 0
    assert strip_doc({"@graph": "not-a-list"}) == 0
    assert strip_doc({"@graph": [None, 5, "x"]}) == 0


def test_strip_doc_idempotent_on_clean_doc():
    doc = {"@graph": [{"@id": "x", SUBSECTION_NUMBER_KEY: "3"}]}
    assert strip_doc(doc) == 0


# ── process_file + idempotency ─────────────────────────────────────────────


def _write(path: Path, graph: list) -> None:
    path.write_text(json.dumps({"@graph": graph}, ensure_ascii=False), encoding="utf-8")


def test_process_file_writes_and_is_idempotent(tmp_path: Path):
    path = tmp_path / "law_peep.json"
    _write(
        path,
        [
            {"@id": "a", SUBSECTION_NUMBER_KEY: "Unknown_1"},
            {"@id": "b", SUBSECTION_NUMBER_KEY: "9"},
        ],
    )
    # first run strips one
    assert process_file(path, dry_run=False) == 1
    after = json.loads(path.read_text(encoding="utf-8"))
    assert SUBSECTION_NUMBER_KEY not in after["@graph"][0]
    assert after["@graph"][1][SUBSECTION_NUMBER_KEY] == "9"
    # re-run is a no-op (idempotent)
    assert process_file(path, dry_run=False) == 0


def test_process_file_dry_run_does_not_write(tmp_path: Path):
    path = tmp_path / "law_peep.json"
    _write(path, [{"@id": "a", SUBSECTION_NUMBER_KEY: "Unknown_4"}])
    before = path.read_text(encoding="utf-8")
    assert process_file(path, dry_run=True) == 1
    assert path.read_text(encoding="utf-8") == before


def test_main_runs_against_real_corpus_idempotently(capsys):
    """After the migration has been applied to the corpus, main() reports
    zero remaining placeholders (proves idempotency on real data)."""
    rc = mod.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Subsection numbers stripped: 0" in out
