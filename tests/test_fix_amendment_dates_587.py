"""Tests for fix_amendment_dates_587 — the offline surgical repair of corrupt
amendment dates and the ``isCurrentAmendment`` flag (issue #587).

All tests run against tmp_path fixtures (no real corpus); the single
corpus-invariant check is marked ``@pytest.mark.corpus`` and skipped by default.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

import fix_amendment_dates_587 as fix


def _event(
    eid: str,
    *,
    date_val: str | None = None,
    eif: str | None = None,
    current: bool = False,
) -> dict:
    node: dict = {
        "@id": eid,
        "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
    }
    if date_val is not None:
        node["estleg:amendmentDate"] = {"@value": date_val, "@type": "xsd:date"}
    if eif is not None:
        node["estleg:entryIntoForce"] = {"@value": eif, "@type": "xsd:date"}
    if current:
        node["estleg:isCurrentAmendment"] = {"@value": "true", "@type": "xsd:boolean"}
    return node


def _chain_doc(events: list[dict]) -> dict:
    return {
        "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
        "@graph": [
            {"@id": "estleg:AmendmentChain_X", "@type": ["owl:Ontology"]},
            *events,
        ],
    }


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _events(doc: dict) -> list[dict]:
    return [
        n
        for n in doc["@graph"]
        if "estleg:AmendmentEvent" in (n.get("@type") or [])
    ]


def _current_ids(doc: dict) -> list[str]:
    return [
        n["@id"]
        for n in _events(doc)
        if "estleg:isCurrentAmendment" in n
    ]


def _run(amend_dir: Path, *, dry_run: bool = False) -> int:
    argv = ["--amendments-dir", str(amend_dir)]
    if dry_run:
        argv.append("--dry-run")
    return fix.main(argv)


def test_corrupt_future_date_dropped_and_current_moves(tmp_path):
    # A corrupt 2918 date wrongly carries isCurrentAmendment; the real latest
    # amendment is 2024-12-19. After the fix the corrupt date is gone and the
    # flag sits on the genuine latest event.
    f = tmp_path / "amendments_corrupt.json"
    _write(
        f,
        _chain_doc([
            _event("estleg:Amendment_X_a", date_val="2022-01-01", eif="2022-02-01"),
            _event("estleg:Amendment_X_b", date_val="2024-12-19", eif="2025-01-01"),
            _event("estleg:Amendment_X_c", date_val="2918-10-17", current=True),
        ]),
    )
    rc = _run(tmp_path)
    assert rc == 0
    doc = json.loads(f.read_text("utf-8"))
    by_id = {n["@id"]: n for n in _events(doc)}
    # Corrupt date dropped entirely.
    assert "estleg:amendmentDate" not in by_id["estleg:Amendment_X_c"]
    # Flag moved off the corrupt event onto the real latest one.
    assert _current_ids(doc) == ["estleg:Amendment_X_b"]


def test_impossible_inversion_eif_nulled(tmp_path):
    # entry_into_force 356 days BEFORE adoption is impossible -> eif nulled,
    # adoption date kept.
    f = tmp_path / "amendments_invert.json"
    _write(
        f,
        _chain_doc([
            _event(
                "estleg:Amendment_X_a",
                date_val="2026-02-26",
                eif="2025-03-07",
                current=True,
            ),
        ]),
    )
    _run(tmp_path)
    doc = json.loads(f.read_text("utf-8"))
    node = _events(doc)[0]
    assert node["estleg:amendmentDate"]["@value"] == "2026-02-26"
    assert "estleg:entryIntoForce" not in node
    # Still the only (and latest) validly-dated event -> stays current.
    assert _current_ids(doc) == ["estleg:Amendment_X_a"]


def test_small_retroactive_window_is_preserved(tmp_path):
    # An 18-day retroactive eif is within tolerance and must NOT be nulled.
    f = tmp_path / "amendments_retro.json"
    _write(
        f,
        _chain_doc([
            _event("estleg:Amendment_X_a", date_val="2019-03-26", eif="2019-03-08"),
        ]),
    )
    _run(tmp_path)
    doc = json.loads(f.read_text("utf-8"))
    node = _events(doc)[0]
    assert node["estleg:entryIntoForce"]["@value"] == "2019-03-08"


def test_current_flag_added_when_missing(tmp_path):
    # A chain with valid dates but NO isCurrentAmendment gets the flag stamped
    # on the latest-effect event.
    f = tmp_path / "amendments_noflag.json"
    _write(
        f,
        _chain_doc([
            _event("estleg:Amendment_X_a", date_val="2020-01-01", eif="2020-02-01"),
            _event("estleg:Amendment_X_b", date_val="2023-01-01", eif="2023-02-01"),
        ]),
    )
    _run(tmp_path)
    doc = json.loads(f.read_text("utf-8"))
    assert _current_ids(doc) == ["estleg:Amendment_X_b"]


def test_no_valid_date_means_no_current_flag(tmp_path):
    # Every event's date is corrupt -> all dropped -> no event is flagged.
    f = tmp_path / "amendments_allbad.json"
    _write(
        f,
        _chain_doc([
            _event("estleg:Amendment_X_a", date_val="2918-01-01", current=True),
            _event("estleg:Amendment_X_b", date_val="3006-01-01"),
        ]),
    )
    _run(tmp_path)
    doc = json.loads(f.read_text("utf-8"))
    assert _current_ids(doc) == []


def test_clean_file_is_untouched(tmp_path):
    # A well-formed chain is a no-op; bytes unchanged.
    f = tmp_path / "amendments_clean.json"
    _write(
        f,
        _chain_doc([
            _event("estleg:Amendment_X_a", date_val="2020-01-01", eif="2020-02-01"),
            _event(
                "estleg:Amendment_X_b",
                date_val="2023-01-01",
                eif="2023-02-01",
                current=True,
            ),
        ]),
    )
    before = f.read_text("utf-8")
    rc = _run(tmp_path)
    assert rc == 0
    # save_json is only called when fix_doc reports a change; a clean file is
    # not rewritten, so the on-disk bytes are identical.
    assert f.read_text("utf-8") == before


def test_idempotent_second_run_is_noop(tmp_path):
    f = tmp_path / "amendments_idem.json"
    _write(
        f,
        _chain_doc([
            _event("estleg:Amendment_X_a", date_val="2022-01-01", eif="2022-02-01"),
            _event("estleg:Amendment_X_b", date_val="2024-12-19", eif="2025-01-01"),
            _event("estleg:Amendment_X_c", date_val="2918-10-17", current=True),
        ]),
    )
    _run(tmp_path)
    after_first = f.read_text("utf-8")
    _run(tmp_path)
    after_second = f.read_text("utf-8")
    assert after_first == after_second


def test_dry_run_writes_nothing(tmp_path):
    f = tmp_path / "amendments_dry.json"
    doc = _chain_doc([
        _event("estleg:Amendment_X_c", date_val="2918-10-17", current=True),
        _event("estleg:Amendment_X_a", date_val="2020-01-01", eif="2020-02-01"),
    ])
    _write(f, doc)
    before = f.read_text("utf-8")
    rc = _run(tmp_path, dry_run=True)
    assert rc == 0
    assert f.read_text("utf-8") == before


def test_lfs_pointer_is_skipped(tmp_path):
    # A file whose first line is the LFS spec marker must never be rewritten.
    f = tmp_path / "amendments_lfs.json"
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:deadbeef\nsize 123\n"
    )
    f.write_text(pointer, encoding="utf-8")
    stats = fix.FixStats()
    fix.process_file(f, stats, dry_run=False)
    assert stats.files_skipped_lfs == 1
    assert stats.files_changed == 0
    assert f.read_text("utf-8") == pointer


def test_proposed_amendment_nodes_are_untouched(tmp_path):
    # ProposedAmendment (draft-derived) nodes never carry these date props and
    # must not be considered for the current-flag computation.
    f = tmp_path / "amendments_proposed.json"
    doc = {
        "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
        "@graph": [
            {"@id": "estleg:AmendmentChain_X", "@type": ["owl:Ontology"]},
            _event("estleg:Amendment_X_a", date_val="2023-01-01", eif="2023-02-01"),
            {
                "@id": "estleg:AmendmentLink_draft_X",
                "@type": ["owl:NamedIndividual", "estleg:ProposedAmendment"],
                "estleg:publicationDate": {"@value": "2025-01-01", "@type": "xsd:date"},
            },
        ],
    }
    _write(f, doc)
    _run(tmp_path)
    out = json.loads(f.read_text("utf-8"))
    proposed = next(
        n for n in out["@graph"]
        if "estleg:ProposedAmendment" in (n.get("@type") or [])
    )
    assert "estleg:isCurrentAmendment" not in proposed
    assert proposed["estleg:publicationDate"]["@value"] == "2025-01-01"
    assert _current_ids(out) == ["estleg:Amendment_X_a"]


@pytest.mark.corpus
def test_corpus_has_no_corrupt_or_future_current_amendments():
    """Post-fix invariant on the real corpus: no AmendmentEvent flagged
    ``isCurrentAmendment`` carries an implausible/future amendmentDate."""
    import glob

    max_year = date.today().year + fix._MAX_FUTURE_YEAR_SLACK
    offenders = []
    for fp in glob.glob(str(fix.DEFAULT_AMENDMENTS_DIR / "amendments_*.json")):
        with open(fp, encoding="utf-8") as fh:
            if fh.readline().startswith(fix.LFS_POINTER_PREFIX):
                continue
        with open(fp, encoding="utf-8") as fh:
            doc = json.load(fh)
        for node in doc.get("@graph", []):
            if "estleg:isCurrentAmendment" not in node:
                continue
            val = fix._typed_date_value(node, "estleg:amendmentDate")
            parsed = fix._parse_iso(val)
            if parsed is not None and parsed.year > max_year:
                offenders.append((Path(fp).name, val))
    assert not offenders, offenders
