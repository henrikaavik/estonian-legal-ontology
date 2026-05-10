"""Tests for scripts/kov_pipeline_coverage.py — pipeline coverage reporter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


from kov_pipeline_coverage import (
    CoverageReport,
    write_coverage_report,
)


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
            run_timestamp="t",
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
            pipeline="x", run_timestamp="t", pipeline_version="v",
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
            pipeline="x", run_timestamp="t", pipeline_version="v",
            input_files_total=1, input_files_kov=0,
        )
        write_coverage_report(report, out)
        doc = json.load(open(out, encoding="utf-8"))
        assert "old" not in doc
        assert doc["pipeline"] == "x"


class TestPipelineVersionResolver:
    def test_returns_short_sha(self):
        from kov_pipeline_coverage import resolve_pipeline_version
        sha = resolve_pipeline_version()
        # 7-char short SHA from `git rev-parse --short HEAD`
        assert len(sha) >= 7
        assert all(c in "0123456789abcdef" for c in sha)
