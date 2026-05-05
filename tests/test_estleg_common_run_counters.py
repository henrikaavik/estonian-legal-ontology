"""Tests for _RunCounters and _safe_load in estleg_common."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import _FAILURE_SAMPLES_INMEMORY_CAP, _RunCounters, _safe_load


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
