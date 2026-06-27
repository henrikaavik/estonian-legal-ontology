"""Tests for issue #629 — doubled ``estleg:summary`` strings.

Two halves, matching the two-part fix:

1. The generator fix in ``riigiteataja_common`` (``collect_text`` /
   ``collect_full_text``): nested text tags (``loige`` > ``lause`` >
   ``lauseOsa``) must contribute their text exactly ONCE, not once per nesting
   level.
2. The offline surgical repair ``dedupe_summaries_629`` (``dedouble`` + the
   file-processing plumbing): collapse an unmistakable whole-text double to its
   single unit, conservatively, while never altering a non-repeated summary.

All tests run against in-memory / tmp_path fixtures (no real corpus).
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import dedupe_summaries_629 as dd
import riigiteataja_common as rc

# ---------------------------------------------------------------------------
# Part 1 — generator fix: nested text tags counted once (collect_text /
# collect_full_text in riigiteataja_common).
# ---------------------------------------------------------------------------

_SENTENCE = "Käesolev paragrahv reguleerib asja menetlemise korda."


def _paragrahv(inner_xml: str) -> ET.Element:
    return ET.fromstring(f"<paragrahv>{inner_xml}</paragrahv>")


def test_collect_full_text_nested_loige_lause_appears_once():
    # The exact <loige><lause>…</lause></loige> nesting called out in #629: the
    # old el.iter() walk matched BOTH the loige and the nested lause, doubling
    # the text. It must now appear exactly once.
    el = _paragrahv(f"<loige><lause>{_SENTENCE}</lause></loige>")
    full = rc.collect_full_text(el)
    assert full == _SENTENCE
    assert full.count(_SENTENCE) == 1


def test_collect_text_nested_loige_lause_appears_once():
    el = _paragrahv(f"<loige><lause>{_SENTENCE}</lause></loige>")
    summary = rc.collect_text(el)
    assert summary == _SENTENCE
    assert summary.count(_SENTENCE) == 1


def test_collect_full_text_triple_nesting_lauseosa_appears_once():
    # loige > lause > lauseOsa — all three are text tags and all three nested;
    # the outermost (loige) must own the text, counted once (not tripled).
    el = _paragrahv(f"<loige><lause><lauseOsa>{_SENTENCE}</lauseOsa></lause></loige>")
    full = rc.collect_full_text(el)
    assert full.count(_SENTENCE) == 1


def test_collect_full_text_multiple_lause_in_loige_not_doubled():
    a = "Esimene lause kirjeldab reguleerimisala selgelt."
    b = "Teine lause täpsustab kohaldamise tingimusi."
    el = _paragrahv(f"<loige><lause>{a}</lause> <lause>{b}</lause></loige>")
    full = rc.collect_full_text(el)
    assert full.count(a) == 1
    assert full.count(b) == 1
    assert full == f"{a} {b}"


def test_collect_full_text_text_wrapped_in_non_text_markup_is_reached():
    # A text tag wrapped in a non-text container must still be collected (the
    # recursion descends into non-text children).
    el = _paragrahv(f"<jagu><loige><lause>{_SENTENCE}</lause></loige></jagu>")
    assert rc.collect_full_text(el).count(_SENTENCE) == 1


def test_collect_full_text_multiple_loige_each_once_in_order():
    a = "Esimene loige reguleerib taotluse esitamist asutusele."
    b = "Teine loige reguleerib taotluse menetlemist ametis."
    el = _paragrahv(f"<loige><lause>{a}</lause></loige><loige><lause>{b}</lause></loige>")
    full = rc.collect_full_text(el)
    assert full == f"{a} {b}"
    assert full.count(a) == 1 and full.count(b) == 1


# ---------------------------------------------------------------------------
# Part 2 — dedouble(): the conservative collapse.
# ---------------------------------------------------------------------------

_UNIT = "Püsielupaiga valitseja on Keskkonnaamet ja kohalik vald."


def test_dedouble_exact_double_returns_unit():
    s = f"{_UNIT} {_UNIT}"
    assert dd.dedouble(s) == _UNIT


def test_dedouble_truncated_prefix_double_returns_unit():
    # Second copy clipped to a >20-char prefix of U (the 500-char-cap shape).
    prefix = _UNIT[:30]
    assert len(prefix) > dd._MIN_UNIT
    s = f"{_UNIT} {prefix}"
    assert dd.dedouble(s) == _UNIT


def test_dedouble_non_doubled_returns_none():
    s = "Käesolev määrus jõustub kümnendal päeval pärast Riigi Teatajas avaldamist."
    assert dd.dedouble(s) is None


def test_dedouble_repeated_short_word_returns_none():
    # The repeated unit "kord" is < 20 chars, so this is left untouched.
    s = "kord kord kord kord kord kord kord kord"
    assert dd.dedouble(s) is None


def test_dedouble_coincidental_short_prefix_returns_none():
    # A genuine single preview that merely begins and ends with the same SHORT
    # fragment (a stray trailing "1") must NOT be collapsed — the #629
    # false-positive guard (second copy must also exceed _MIN_UNIT chars).
    s = "1 (1) Käesolevat seadust kohaldatakse järgmistel juhtudel ja korras: 1"
    assert dd.dedouble(s) is None


def test_dedouble_idempotent():
    once = dd.dedouble(f"{_UNIT} {_UNIT}")
    assert once == _UNIT
    # Collapsing the already-single unit again is a no-op.
    assert dd.dedouble(once) is None


def test_dedouble_triple_not_collapsed():
    # ~3x is not ~2x — conservatively left alone (the regen is the real fix).
    s = f"{_UNIT} {_UNIT} {_UNIT}"
    assert dd.dedouble(s) is None


def test_dedouble_short_string_returns_none():
    assert dd.dedouble("") is None
    assert dd.dedouble("Lühike.") is None


# ---------------------------------------------------------------------------
# Part 2 — file plumbing: dry-run / apply / idempotency / LFS skip / subdir.
# ---------------------------------------------------------------------------


def _peep(summary: str) -> dict:
    return {
        "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
        "@graph": [
            {"@id": "estleg:Reg_Map", "@type": ["owl:Ontology"]},
            {"@id": "estleg:Reg_Par_1", "estleg:summary": summary},
        ],
    }


def _summary_on_disk(path: Path) -> str:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc["@graph"][1]["estleg:summary"]


def test_process_file_dry_run_detects_but_does_not_write(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    sub = krr / "regulations" / "riik"
    sub.mkdir(parents=True)
    doubled = f"{_UNIT} {_UNIT}"
    peep = sub / "x_peep.json"
    peep.write_text(json.dumps(_peep(doubled), ensure_ascii=False, indent=2), encoding="utf-8")

    stats = dd.Stats()
    dd.process_file(peep, stats, krr, dry_run=True)

    assert stats.summaries_seen == 1
    assert stats.summaries_dedoubled == 1
    assert stats.files_changed == 1
    assert stats.by_subdir["regulations"] == 1
    # Dry-run never touches the file.
    assert _summary_on_disk(peep) == doubled


def test_process_file_apply_writes_and_is_idempotent(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    sub = krr / "regulations" / "riik"
    sub.mkdir(parents=True)
    peep = sub / "x_peep.json"
    peep.write_text(
        json.dumps(_peep(f"{_UNIT} {_UNIT}"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    stats = dd.Stats()
    dd.process_file(peep, stats, krr, dry_run=False)
    assert stats.summaries_dedoubled == 1
    assert _summary_on_disk(peep) == _UNIT

    # Re-running over the now-clean file changes nothing.
    stats2 = dd.Stats()
    dd.process_file(peep, stats2, krr, dry_run=False)
    assert stats2.summaries_dedoubled == 0
    assert stats2.files_changed == 0
    assert _summary_on_disk(peep) == _UNIT


def test_process_file_leaves_non_doubled_summary_alone(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    clean = "Määrus jõustub kümnendal päeval pärast Riigi Teatajas avaldamist."
    peep = krr / "law_peep.json"
    peep.write_text(json.dumps(_peep(clean), ensure_ascii=False, indent=2), encoding="utf-8")

    stats = dd.Stats()
    dd.process_file(peep, stats, krr, dry_run=False)
    assert stats.summaries_dedoubled == 0
    assert stats.files_changed == 0
    assert _summary_on_disk(peep) == clean


def test_process_file_skips_lfs_pointer(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    pointer = krr / "big_peep.json"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0123456789abcdef\nsize 123456\n",
        encoding="utf-8",
    )
    stats = dd.Stats()
    dd.process_file(pointer, stats, krr, dry_run=False)
    assert stats.files_skipped_lfs == 1
    assert stats.summaries_dedoubled == 0
    # The pointer stub is left exactly as-is.
    assert _is_pointer_intact(pointer)


def _is_pointer_intact(path: Path) -> bool:
    return path.read_text(encoding="utf-8").startswith(dd.LFS_POINTER_PREFIX)


def test_top_subdir_classification(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    (krr / "regulations" / "riik").mkdir(parents=True)
    krr.joinpath("a_peep.json").write_text("{}", encoding="utf-8")
    krr.joinpath("regulations", "riik", "b_peep.json").write_text("{}", encoding="utf-8")
    assert dd._top_subdir(krr / "a_peep.json", krr) == "(root)"
    assert dd._top_subdir(krr / "regulations" / "riik" / "b_peep.json", krr) == "regulations"


def test_main_dry_run_default_does_not_write(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    sub = krr / "regulations" / "riik"
    sub.mkdir(parents=True)
    doubled = f"{_UNIT} {_UNIT}"
    peep = sub / "x_peep.json"
    peep.write_text(json.dumps(_peep(doubled), ensure_ascii=False, indent=2), encoding="utf-8")

    # No --apply -> dry-run is the default -> nothing is written.
    rc_code = dd.main(["--krr-dir", str(krr)])
    assert rc_code == 0
    assert _summary_on_disk(peep) == doubled
