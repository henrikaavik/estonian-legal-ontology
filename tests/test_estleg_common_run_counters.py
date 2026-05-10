"""Tests for _RunCounters and _safe_load in estleg_common."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import (
    PAR_SUFFIX,
    _FAILURE_SAMPLES_INMEMORY_CAP,
    _RunCounters,
    _safe_load,
    build_globalid_xml_lookup,
    pair_peep_with_xml,
)
import re


def test_run_counters_defaults() -> None:
    c = _RunCounters()
    assert c.file_skips == 0
    assert c.error_count == 0
    assert c.skip_reasons == {}
    assert c.failures == []
    assert c.seen_error_paths == set()


def test_run_counters_bump_skip() -> None:
    c = _RunCounters()
    c.bump_skip("json_decode_error", "json_decode_error | foo.json | ValueError: bad")
    assert c.file_skips == 1
    assert c.error_count == 1
    assert c.skip_reasons["json_decode_error"] == 1
    assert c.failures == ["json_decode_error | foo.json | ValueError: bad"]


def test_run_counters_failures_capped_at_inmemory_cap() -> None:
    c = _RunCounters()
    over = _FAILURE_SAMPLES_INMEMORY_CAP + 50
    for i in range(over):
        c.bump_skip("x", f"sample_{i}")
    assert c.skip_reasons["x"] == over
    assert len(c.failures) == _FAILURE_SAMPLES_INMEMORY_CAP


def test_safe_load_success(tmp_path: Path) -> None:
    p = tmp_path / "good.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    c = _RunCounters()
    doc = _safe_load(p, c)
    assert doc == {"a": 1}
    assert c.file_skips == 0


def test_safe_load_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = _RunCounters()
    doc = _safe_load(p, c)
    assert doc is None
    assert c.file_skips == 1
    assert c.error_count == 1
    assert c.skip_reasons["json_decode_error"] == 1
    assert len(c.failures) == 1
    assert "bad.json" in c.failures[0]


def test_safe_load_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    c = _RunCounters()
    doc = _safe_load(p, c)
    assert doc is None
    assert c.file_skips == 1


def test_safe_load_unicode_decode_error(tmp_path: Path) -> None:
    p = tmp_path / "mojibake.json"
    p.write_bytes(b"\xff\xfe{")
    c = _RunCounters()

    doc = _safe_load(p, c)

    assert doc is None
    assert c.file_skips == 1
    assert c.skip_reasons["json_decode_error"] == 1


def test_safe_load_dedup_same_path(tmp_path: Path) -> None:
    """Same malformed file revisited by multiple walks counts ONCE."""
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = _RunCounters()
    _safe_load(p, c)
    _safe_load(p, c)   # second walk
    _safe_load(p, c)   # third walk
    assert c.file_skips == 1
    assert c.skip_reasons["json_decode_error"] == 1
    assert len(c.failures) == 1
    assert p in c.seen_error_paths


def test_bump_citation_skip_does_not_touch_file_skips() -> None:
    c = _RunCounters()
    c.bump_citation_skip("kov_citation_ambiguous", "ambiguous | A | B")
    assert c.file_skips == 0           # citation-level, not file-level
    assert c.error_count == 0
    assert c.skip_reasons["kov_citation_ambiguous"] == 1
    assert c.failures == ["ambiguous | A | B"]


def test_bump_citation_count_no_sample() -> None:
    c = _RunCounters()
    c.bump_citation_count("rtiv_form_citation")
    c.bump_citation_count("rtiv_form_citation")
    assert c.skip_reasons["rtiv_form_citation"] == 2
    assert c.failures == []
    assert c.file_skips == 0


def test_par_suffix_accepts_double_section_with_case_suffix() -> None:
    pattern = re.compile(PAR_SUFFIX)

    for value in ("§", "§§", "§-le", "§§-le", "§‑des", "§§‑des"):
        assert pattern.fullmatch(value)


def test_build_globalid_lookup_keeps_first_duplicate_deterministically(tmp_path: Path) -> None:
    first = tmp_path / "a.xml"
    second = tmp_path / "b.xml"
    first.write_text('<root globaalID="G1"></root>', encoding="utf-8")
    second.write_text('<root globaalID="G1"></root>', encoding="utf-8")

    lookup = build_globalid_xml_lookup(tmp_path)

    assert lookup["G1"] == first


def test_pair_peep_with_xml_reports_corrupt_json_when_counters_provided(tmp_path: Path) -> None:
    peep = tmp_path / "bad_peep.json"
    peep.write_text("{not json", encoding="utf-8")
    counters = _RunCounters()

    assert pair_peep_with_xml(peep, {}, counters=counters) is None
    assert counters.file_skips == 1
    assert counters.skip_reasons["json_decode_error"] == 1
