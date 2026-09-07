"""Tests for scripts/kov_pipeline_coverage.py — pipeline coverage reporter."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from estleg.kov_pipeline_coverage import (
    CoverageReport,
    format_run_timestamp,
    measure_runtime,
    validate_run_timestamp,
    write_coverage_report,
)

# Centralised valid timestamp for placeholder use in CoverageReport
# fixtures across tests below — keeps the value out of inline
# repetition and lets us flip the canonical value in one place.
VALID_TS = "2026-05-10T12:34:56+00:00"


class TestCoverageReport:
    def test_dataclass_round_trips(self, tmp_path):
        report = CoverageReport(
            pipeline="classify_eurovoc",
            run_timestamp="2026-05-03T12:00:00Z",
            pipeline_version="abc1234",
            input_files_total=15508,
            input_files_kov=11059,
            files_processed=15500,
            files_processed_kov=11055,
            files_with_output=14800,
            files_with_output_kov=10500,
            files_skipped=8,
            skip_reasons={"no_act_node": 6, "json_decode_error": 2},
            triples_emitted=42000,
            triples_emitted_kov=28000,
            fallback_hits=0,
            unresolved_references=0,
            wall_time_seconds=125.4,
            items_per_second=124.0,
            peak_memory_mb=312.5,
            error_count=0,
            failure_samples=[],
        )
        out = tmp_path / "coverage.json"
        write_coverage_report(report, out)

        with open(out, "r", encoding="utf-8") as fh:
            doc = json.load(fh)

        assert doc["pipeline"] == "classify_eurovoc"
        assert doc["input_files_total"] == 15508
        assert doc["input_files_kov"] == 11059
        assert doc["files_processed"] == 15500
        # The KOV-specific fields are the load-bearing ones for the
        # reviewer gate — assert each is correctly round-tripped.
        assert doc["files_processed_kov"] == 11055
        assert doc["files_with_output"] == 14800
        assert doc["files_with_output_kov"] == 10500
        assert doc["files_skipped"] == 8
        assert doc["skip_reasons"] == {"no_act_node": 6,
                                        "json_decode_error": 2}
        assert doc["triples_emitted"] == 42000
        assert doc["triples_emitted_kov"] == 28000
        assert doc["fallback_hits"] == 0
        assert doc["unresolved_references"] == 0
        assert isinstance(doc["wall_time_seconds"], (int, float))
        assert isinstance(doc["items_per_second"], (int, float))
        assert isinstance(doc["peak_memory_mb"], (int, float))
        assert doc["pipeline_version"] == "abc1234"

    def test_required_fields_only(self, tmp_path):
        """All count/runtime fields default to zero — only pipeline/
        timestamp/version + the two input_files counts are required."""
        report = CoverageReport(
            pipeline="x",
            run_timestamp=VALID_TS,
            pipeline_version="v",
            input_files_total=0,
            input_files_kov=0,
        )
        out = tmp_path / "c.json"
        write_coverage_report(report, out)
        doc = json.load(open(out, encoding="utf-8"))
        # All of the KOV-specific + extended fields must default to
        # 0 / empty when not explicitly set.
        assert doc["files_processed"] == 0
        assert doc["files_processed_kov"] == 0
        assert doc["files_with_output"] == 0
        assert doc["files_with_output_kov"] == 0
        assert doc["files_skipped"] == 0
        assert doc["skip_reasons"] == {}
        assert doc["triples_emitted"] == 0
        assert doc["triples_emitted_kov"] == 0
        assert doc["fallback_hits"] == 0
        assert doc["unresolved_references"] == 0
        assert doc["items_per_second"] == 0.0
        assert doc["peak_memory_mb"] == 0.0

    def test_failure_samples_capped(self, tmp_path):
        # Helper rejects more than 20 samples (the documented cap)
        report = CoverageReport(
            pipeline="x", run_timestamp=VALID_TS, pipeline_version="v",
            input_files_total=0, input_files_kov=0,
            error_count=25,
            failure_samples=[f"sample-{i}" for i in range(25)],
        )
        out = tmp_path / "c.json"
        write_coverage_report(report, out)
        doc = json.load(open(out, encoding="utf-8"))
        assert len(doc["failure_samples"]) == 20

    def test_atomic_write(self, tmp_path):
        # Pre-existing file must be replaced atomically (temp + rename)
        out = tmp_path / "c.json"
        out.write_text('{"old": true}', encoding="utf-8")
        report = CoverageReport(
            pipeline="x", run_timestamp=VALID_TS, pipeline_version="v",
            input_files_total=1, input_files_kov=0,
        )
        write_coverage_report(report, out)
        doc = json.load(open(out, encoding="utf-8"))
        assert "old" not in doc
        assert doc["pipeline"] == "x"


class TestPipelineVersionResolver:
    def test_returns_short_sha(self):
        from estleg.kov_pipeline_coverage import resolve_pipeline_version
        sha = resolve_pipeline_version()
        # 7-char short SHA from `git rev-parse --short HEAD`
        assert len(sha) >= 7
        assert all(c in "0123456789abcdef" for c in sha)


class TestValidateRunTimestamp:
    """Regression tests for Finding #13: write_coverage_report must
    reject malformed run_timestamps so a wall-clock-style placeholder
    can never end up in a machine-consumed report.
    """

    def test_iso_z_passes(self):
        assert validate_run_timestamp("2026-05-10T12:34:56Z") == (
            "2026-05-10T12:34:56Z"
        )

    def test_iso_offset_passes(self):
        assert validate_run_timestamp("2026-05-10T12:34:56+00:00") == (
            "2026-05-10T12:34:56+00:00"
        )

    def test_iso_with_microseconds_passes(self):
        assert validate_run_timestamp("2026-05-10T12:34:56.789Z") == (
            "2026-05-10T12:34:56.789Z"
        )

    def test_garbage_string_raises(self):
        with pytest.raises(ValueError, match="ISO-8601"):
            validate_run_timestamp("t")

    def test_naive_datetime_string_raises(self):
        # No tzinfo → not UTC. The regex only accepts Z or +00:00,
        # so this fails before the parse step.
        with pytest.raises(ValueError, match="ISO-8601"):
            validate_run_timestamp("2026-05-10T12:34:56")

    def test_non_utc_offset_raises(self):
        with pytest.raises(ValueError, match="ISO-8601"):
            # +03:00 is a valid offset but it's not UTC.
            validate_run_timestamp("2026-05-10T12:34:56+03:00")

    def test_non_string_raises(self):
        with pytest.raises(ValueError, match="must be a string"):
            validate_run_timestamp(1234)  # type: ignore[arg-type]

    def test_write_coverage_report_rejects_bad_timestamp(self, tmp_path):
        report = CoverageReport(
            pipeline="x",
            run_timestamp="not-a-timestamp",
            pipeline_version="v",
            input_files_total=0,
            input_files_kov=0,
        )
        with pytest.raises(ValueError, match="ISO-8601"):
            write_coverage_report(report, tmp_path / "c.json")


class TestFormatRunTimestamp:
    """Helper that produces a timestamp validate_run_timestamp accepts.

    Pinned here so callers always have a working `datetime → str` path
    (rather than hand-rolling `.isoformat()` and getting it wrong).
    """

    def test_aware_utc_round_trips(self):
        when = datetime(2026, 5, 10, 12, 34, 56, tzinfo=UTC)
        result = format_run_timestamp(when)
        assert validate_run_timestamp(result) == result

    def test_aware_non_utc_normalised_to_utc(self):
        # An eastern offset is converted to UTC, not preserved as +03:00.
        eastern = timezone(timedelta(hours=3))
        when = datetime(2026, 5, 10, 15, 34, 56, tzinfo=eastern)
        result = format_run_timestamp(when)
        # The resulting string must pass validation (UTC) and equal
        # the equivalent UTC instant.
        validate_run_timestamp(result)
        assert result.startswith("2026-05-10T12:34:56")

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError, match="tz-aware"):
            format_run_timestamp(datetime(2026, 5, 10, 12, 34, 56))


class TestMeasureRuntime:
    """Regression tests for Finding #10: peak_memory_mb must use a
    portable conversion, sanity-check the result, and not silently
    emit nonsense values on Windows or on platform-detection
    regressions.
    """

    def test_returns_three_floats(self):
        import time
        wall, rate, peak = measure_runtime(
            start_time=time.perf_counter() - 0.001,
            items_processed=10,
        )
        # Wall is small but positive; rate is 10/wall.
        assert wall > 0
        assert rate > 0
        # peak_memory_mb is platform-dependent but must be non-
        # negative and well below the 1 TB sanity threshold.
        assert 0 <= peak < 1_000_000

    def test_peak_memory_within_sanity_threshold(self):
        # Catch the macOS "double-divide" regression: pre-fix,
        # ru_maxrss in bytes was being divided by 1024 (treating it
        # as KB) producing inflated MB values. The sanity assertion
        # in measure_runtime would now raise on >1 TB; test that we
        # are nowhere near it on a normal pytest run.
        import time
        _, _, peak = measure_runtime(time.perf_counter(), 1)
        assert peak < 100_000  # 100 GB ceiling — comfortably well under
                                # any real measurement on a test box.

    def test_windows_path_returns_zero(self, monkeypatch):
        # Simulate Windows: measure_runtime must short-circuit before
        # the resource import and return 0.0 rather than raising.
        import sys as _sys
        monkeypatch.setattr(_sys, "platform", "win32")
        import time
        _, _, peak = measure_runtime(time.perf_counter(), 1)
        assert peak == 0.0
