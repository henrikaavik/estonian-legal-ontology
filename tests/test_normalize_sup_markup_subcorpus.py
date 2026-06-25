"""Tests for normalize_sup_markup_subcorpus.py (#627).

Covers the recursive string normalization (nested lists / dicts /
@value literals), the no-markup no-op, parity with the reused #572
``_sup_to_unicode`` helper, file processing, and idempotency.
"""
from __future__ import annotations

import json
from pathlib import Path

from generate_all_laws import _sup_to_unicode

import normalize_sup_markup_subcorpus as mod
from normalize_sup_markup_subcorpus import (
    SUBCORPUS_DIRS,
    iter_subcorpus_files,
    normalize_doc,
    normalize_value,
    process_file,
)


# ── normalize_value ────────────────────────────────────────────────────────


def test_normalize_value_converts_digit_sup():
    counter = [0]
    out = normalize_value("§ 4<sup>1</sup>.", counter)
    assert out == "§ 4¹."
    assert counter[0] == 1


def test_normalize_value_recurses_nested_structures():
    counter = [0]
    payload = {
        "@graph": [
            {"rdfs:label": "x<sup>2</sup>", "n": 5, "flag": True},
            {"estleg:legalText": {"@value": "a<sup>10</sup>b"}},
            {"list": ["no markup", "y<sup>3</sup>"]},
        ]
    }
    out = normalize_value(payload, counter)
    assert out["@graph"][0]["rdfs:label"] == "x²"
    assert out["@graph"][0]["n"] == 5  # scalar untouched
    assert out["@graph"][0]["flag"] is True
    assert out["@graph"][1]["estleg:legalText"]["@value"] == "a¹⁰b"
    assert out["@graph"][2]["list"] == ["no markup", "y³"]
    assert counter[0] == 3  # three strings actually changed


def test_normalize_value_noop_without_markup():
    counter = [0]
    assert normalize_value("plain text", counter) == "plain text"
    assert counter[0] == 0


def test_normalize_value_strips_bare_nondigit_sup_like_572():
    """Non-digit <sup> content keeps the inner text but loses the markup,
    matching #572's _sup_to_unicode behaviour exactly."""
    counter = [0]
    src = "loetelu <sup> 1, 4, 5</sup> lõpp"
    out = normalize_value(src, counter)
    assert out == _sup_to_unicode(src)
    assert "<sup>" not in out and "</sup>" not in out


def test_normalize_doc_returns_count():
    new_doc, changed = normalize_doc({"k": "z<sup>1</sup>", "j": "plain"})
    assert new_doc["k"] == "z¹"
    assert changed == 1


# ── file processing + idempotency ──────────────────────────────────────────


def test_process_file_writes_and_idempotent(tmp_path: Path):
    path = tmp_path / "reg_peep.json"
    path.write_text(
        json.dumps({"@graph": [{"rdfs:label": "§ 9<sup>1</sup>."}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert process_file(path, dry_run=False) == 1
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["@graph"][0]["rdfs:label"] == "§ 9¹."
    # re-run: no <sup> left -> 0 changes
    assert process_file(path, dry_run=False) == 0


def test_process_file_dry_run_does_not_write(tmp_path: Path):
    path = tmp_path / "reg_peep.json"
    body = json.dumps({"x": "a<sup>1</sup>"}, ensure_ascii=False)
    path.write_text(body, encoding="utf-8")
    assert process_file(path, dry_run=True) == 1
    assert path.read_text(encoding="utf-8") == body


def test_iter_subcorpus_files_matches_json_and_jsonld(tmp_path: Path):
    (tmp_path / "regulations").mkdir()
    (tmp_path / "provision_versions").mkdir()
    a = tmp_path / "regulations" / "x_peep.json"
    b = tmp_path / "provision_versions" / "y.jsonld"
    a.write_text("{}", encoding="utf-8")
    b.write_text("{}", encoding="utf-8")
    files = iter_subcorpus_files(tmp_path)
    assert a in files
    assert b in files


def test_subcorpus_dirs_cover_ticket_scope():
    for d in ("regulations", "riigikohus", "curia", "eurlex", "eelnoud",
              "provision_versions"):
        assert d in SUBCORPUS_DIRS


def test_main_idempotent_on_real_corpus(capsys):
    """After the backfill is applied, a re-run leaves the corpus clean."""
    rc = mod.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Strings normalized: 0" in out
