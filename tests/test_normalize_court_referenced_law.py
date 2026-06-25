"""Tests for normalize_court_referenced_law.py (#596).

Covers genitive→abbrev resolution, case-variant normalization, dropping
unresolvable dead-tokens, list de-duplication, key removal on empty
lists, scalar shape, the matching generator-side fix, and idempotency.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg_common import FULLNAME_GENITIVE

import normalize_court_referenced_law as mod
from normalize_court_referenced_law import (
    REFERENCED_LAW_KEY,
    normalize_doc,
    normalize_referenced_law_value,
    process_file,
    resolve_referenced_law,
)


# ── resolve_referenced_law ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("KarS", "KarS"),  # already canonical abbrev
        ("TsMS", "TsMS"),
        ("Kars", "KarS"),  # case-variant abbrev -> canonical case
        ("KaRS", "KarS"),
        ("liiklusseaduse", "LS"),  # genitive -> abbrev
        ("Liiklusseaduse", "LS"),  # capitalized genitive resolves too
        ("põhiseaduse", "PS"),
        ("Põhiseaduse", "PS"),
        ("kalapüügiseaduse", None),  # dead-token, no table entry
        ("jahiseaduse", None),
        ("Pôhiseaduse", None),  # typo'd char -> unresolvable
        ("Karistusseadustik", None),  # nominative full name, not an abbrev/genitive
        ("", None),
    ],
)
def test_resolve_referenced_law(value, expected):
    assert resolve_referenced_law(value) == expected


def test_resolve_mirrors_fullname_genitive_table():
    # Every genitive in the table must resolve to its mapped abbreviation.
    for genitive, abbrev in FULLNAME_GENITIVE.items():
        assert resolve_referenced_law(genitive) == abbrev


# ── normalize_referenced_law_value ─────────────────────────────────────────


def test_value_list_rewrite_and_drop_with_dedup():
    new, rewritten, dropped = normalize_referenced_law_value(
        ["liiklusseaduse", "KarS", "kalapüügiseaduse", "Liiklusseaduse"]
    )
    # liiklusseaduse+Liiklusseaduse both -> LS (deduped), KarS kept,
    # kalapüügiseaduse dropped
    assert new == ["LS", "KarS"]
    assert rewritten == 2  # both liiklusseaduse variants differ from canonical
    assert dropped == 1


def test_value_all_dead_returns_none():
    new, rewritten, dropped = normalize_referenced_law_value(["kalapüügiseaduse"])
    assert new is None
    assert dropped == 1


def test_value_scalar_resolvable_stays_scalar():
    new, rewritten, dropped = normalize_referenced_law_value("liiklusseaduse")
    assert new == "LS"
    assert rewritten == 1
    assert dropped == 0


def test_value_scalar_clean_unchanged():
    new, rewritten, dropped = normalize_referenced_law_value("KarS")
    assert new == "KarS"
    assert rewritten == 0
    assert dropped == 0


# ── normalize_doc + key removal ────────────────────────────────────────────


def test_normalize_doc_removes_emptied_key_keeps_interprets():
    doc = {
        "@graph": [
            {
                "@id": "estleg:RK_1",
                REFERENCED_LAW_KEY: ["kalapüügiseaduse"],  # all dead
                "estleg:interpretsLaw": [{"@id": "estleg:KLS_Par_1"}],
            },
            {
                "@id": "estleg:RK_2",
                REFERENCED_LAW_KEY: ["liiklusseaduse", "jahiseaduse"],  # mixed
            },
        ]
    }
    rewritten, dropped, keys_removed = normalize_doc(doc)
    g = doc["@graph"]
    # node 1: key removed, interpretsLaw untouched
    assert REFERENCED_LAW_KEY not in g[0]
    assert g[0]["estleg:interpretsLaw"] == [{"@id": "estleg:KLS_Par_1"}]
    # node 2: kept resolvable LS, dropped dead jahiseaduse
    assert g[1][REFERENCED_LAW_KEY] == ["LS"]
    assert keys_removed == 1
    assert dropped == 2  # kalapüügiseaduse + jahiseaduse
    assert rewritten == 1  # liiklusseaduse -> LS


def test_normalize_doc_no_graph_safe():
    assert normalize_doc({}) == (0, 0, 0)


# ── file processing + idempotency ──────────────────────────────────────────


def test_process_file_writes_and_idempotent(tmp_path: Path):
    path = tmp_path / "riigikohus_2000_peep.json"
    path.write_text(
        json.dumps(
            {"@graph": [{"@id": "x", REFERENCED_LAW_KEY: ["liiklusseaduse", "tolliseaduse"]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rewritten, dropped, keys_removed = process_file(path, dry_run=False)
    assert (rewritten, dropped, keys_removed) == (1, 1, 0)
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["@graph"][0][REFERENCED_LAW_KEY] == ["LS"]
    # re-run: LS is canonical, no genitive left -> no change
    assert process_file(path, dry_run=False) == (0, 0, 0)


def test_process_file_dry_run_does_not_write(tmp_path: Path):
    path = tmp_path / "riigikohus_2001_peep.json"
    body = json.dumps(
        {"@graph": [{"@id": "x", REFERENCED_LAW_KEY: ["liiklusseaduse"]}]},
        ensure_ascii=False,
    )
    path.write_text(body, encoding="utf-8")
    assert process_file(path, dry_run=True) == (1, 0, 0)
    assert path.read_text(encoding="utf-8") == body


# ── generator-side fix (no re-run) ─────────────────────────────────────────


def test_generator_detect_referenced_laws_resolves_genitives():
    """The generator emit is patched to resolve genitives and drop
    dead-tokens, so a future regen won't reintroduce the defect."""
    from generate_court_decisions import detect_referenced_laws

    # genitive resolves to abbrev
    out = detect_referenced_laws("Kohus tugines liiklusseaduse § 20 alusel.")
    assert "LS" in out
    assert "liiklusseaduse" not in out

    # dead-token genitive is dropped (not emitted verbatim)
    out2 = detect_referenced_laws("Vaidlus puudutas kalapüügiseaduse § 5.")
    assert "kalapüügiseaduse" not in out2

    # known abbrev still emitted canonically
    out3 = detect_referenced_laws("KarS § 199 rikkumine.")
    assert "KarS" in out3


def test_main_idempotent_on_real_corpus(capsys):
    """After the migration is applied, a re-run leaves no genitive
    dead-tokens to change."""
    rc = mod.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Values dropped (unresolvable dead-tokens): 0" in out
    assert "Values rewritten (genitive/case-variant -> abbrev): 0" in out
