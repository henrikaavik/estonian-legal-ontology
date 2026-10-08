"""Tests for normalize_court_referenced_law.py (#596, #696).

Covers genitive→abbrev resolution, case-variant normalization, keeping
unresolvable values as target-less Citation nodes (#696, was: dropped),
list de-duplication, key removal on empty lists, scalar shape, the
matching generator-side fix, and idempotency (on a tmp corpus, #706).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import normalize_court_referenced_law as mod
from estleg.estleg_common import FULLNAME_GENITIVE
from estleg.normalize_court_referenced_law import (
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
        # #696: genitive title fold -> the law's abbreviation
        ("kalapüügiseaduse", "KLS"),
        ("tolliseaduse", "TKS"),
        ("jahiseaduse", None),  # no citation abbreviation -> kept as Citation
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
        ["liiklusseaduse", "KarS", "jahiseaduse", "Liiklusseaduse"]
    )
    # liiklusseaduse+Liiklusseaduse both -> LS (deduped), KarS kept,
    # jahiseaduse moved out (normalize_doc keeps it as a Citation)
    assert new == ["LS", "KarS"]
    assert rewritten == 2  # both liiklusseaduse variants differ from canonical
    assert dropped == 1


def test_value_all_dead_returns_none():
    new, rewritten, dropped = normalize_referenced_law_value(["jahiseaduse"])
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
                REFERENCED_LAW_KEY: ["jahiseaduse"],  # all unresolvable
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
    assert dropped == 2  # jahiseaduse twice (one per decision)
    assert rewritten == 1  # liiklusseaduse -> LS


def test_normalize_doc_keeps_unresolved_as_targetless_citations():
    """#696: an unresolvable value is not deleted — it becomes a
    target-less estleg:Citation (the in-law #514 shape)."""
    doc = {"@graph": [{"@id": "estleg:RK_1", REFERENCED_LAW_KEY: ["jahiseaduse", "KarS"]}]}
    normalize_doc(doc)
    g = doc["@graph"]
    assert g[0][REFERENCED_LAW_KEY] == ["KarS"]
    citations = [n for n in g if "estleg:Citation" in n.get("@type", [])]
    assert citations == [
        {
            "@id": "estleg:Citation_RK_1_RefLaw_1",
            "@type": ["owl:NamedIndividual", "estleg:Citation"],
            "estleg:citationSource": {"@id": "estleg:RK_1"},
            "estleg:citationText": "jahiseaduse",
        }
    ]
    # Idempotent: nothing left to move, no duplicate node.
    assert normalize_doc(doc) == (0, 0, 0)
    assert sum("estleg:Citation" in n.get("@type", []) for n in doc["@graph"]) == 1


def test_court_pass_does_not_own_referenced_law_citations():
    """The court-links pass clears only its own target-less Citations."""
    from estleg.extract_court_provision_links import is_court_pass_unresolved_citation

    ref_law = {"@id": "estleg:Citation_RK_1_RefLaw_1", "@type": ["estleg:Citation"]}
    court = {"@id": "estleg:Citation_RK_1_1", "@type": ["estleg:Citation"]}
    assert not is_court_pass_unresolved_citation(ref_law)
    assert is_court_pass_unresolved_citation(court)


def test_normalize_doc_no_graph_safe():
    assert normalize_doc({}) == (0, 0, 0)


# ── file processing + idempotency ──────────────────────────────────────────


def test_process_file_writes_and_idempotent(tmp_path: Path):
    path = tmp_path / "riigikohus_2000_peep.json"
    path.write_text(
        json.dumps(
            {"@graph": [{"@id": "x", REFERENCED_LAW_KEY: ["liiklusseaduse", "jahiseaduse"]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rewritten, dropped, keys_removed = process_file(path, dry_run=False)
    assert (rewritten, dropped, keys_removed) == (1, 1, 0)
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["@graph"][0][REFERENCED_LAW_KEY] == ["LS"]
    assert after["@graph"][1]["estleg:citationText"] == "jahiseaduse"
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
    from estleg.generate_court_decisions import detect_referenced_laws

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


def test_main_idempotent_on_tmp_corpus(tmp_path: Path, capsys):
    """main() migrates a tmp corpus, then a re-run changes nothing.

    #706: runs against ``--krr-dir tmp_path`` — never the real corpus.
    """
    rk = tmp_path / "riigikohus"
    rk.mkdir()
    (rk / "riigikohus_2002_peep.json").write_text(
        json.dumps(
            {"@graph": [{"@id": "estleg:RK_9", REFERENCED_LAW_KEY: ["liiklusseaduse", "jahiseaduse"]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert mod.main(["--krr-dir", str(tmp_path)]) == 0
    first = capsys.readouterr().out
    assert "Values kept as unresolved Citation nodes: 1" in first
    assert "Values rewritten (genitive/case-variant -> abbrev): 1" in first

    assert mod.main(["--krr-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Values kept as unresolved Citation nodes: 0" in out
    assert "Values rewritten (genitive/case-variant -> abbrev): 0" in out
