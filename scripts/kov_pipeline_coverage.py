"""KOV pipeline coverage reporter.

Each KOV-relevant pipeline calls into this helper at the end of its
main() to record per-run metrics. The reports live at
``krr_outputs/reports/kov/<pipeline>_coverage.json`` and let
reviewers verify the absolute "after" values against the implicit
zero-KOV baseline that pre-2a represented by construction.

Pure helpers — no global state. The caller assembles a CoverageReport
and calls write_coverage_report(report, out_path).
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path


_FAILURE_SAMPLE_CAP = 20


@dataclass
class CoverageReport:
    """Per-run coverage metrics. Field set matches the spec's
    "Per-pipeline KOV coverage report" requirement (counts, runtime
    metrics, failure samples)."""

    pipeline: str
    run_timestamp: str        # ISO-8601 UTC
    pipeline_version: str     # git short SHA

    # Coverage counts
    input_files_total: int
    input_files_kov: int
    files_processed: int = 0
    files_processed_kov: int = 0  # KOV subset of files_processed
    files_with_output: int = 0    # files that produced ≥1 output triple
    files_with_output_kov: int = 0  # KOV subset of files_with_output
    files_skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    triples_emitted: int = 0
    triples_emitted_kov: int = 0  # subset of triples_emitted attributed
                                   # to KOV inputs (per-pipeline definition)
    fallback_hits: int = 0
    unresolved_references: int = 0

    # Runtime metrics
    wall_time_seconds: float = 0.0
    items_per_second: float = 0.0
    peak_memory_mb: float = 0.0

    # Failures
    error_count: int = 0
    failure_samples: list[str] = field(default_factory=list)


def write_coverage_report(report: CoverageReport, path: Path) -> None:
    """Write a CoverageReport to a JSON file atomically.

    Caps failure_samples at 20 entries (per the spec); writes to a
    temp file in the same directory, then renames into place so a
    crash mid-write doesn't leave a partial file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(report)
    payload["failure_samples"] = payload["failure_samples"][:_FAILURE_SAMPLE_CAP]

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def resolve_pipeline_version() -> str:
    """Return the current git short SHA, or 'unknown' on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def measure_runtime(
    start_time: float, items_processed: int
) -> tuple[float, float, float]:
    """Compute the runtime metrics required by CoverageReport.

    Returns ``(wall_time_seconds, items_per_second, peak_memory_mb)``.

    Wall time uses ``time.perf_counter()`` (monotonic, sub-second
    resolution). Peak memory comes from ``resource.getrusage`` with
    a platform-specific normalization to MB (Linux ru_maxrss is in KB,
    macOS in bytes). On platforms where ``resource`` is unavailable
    (Windows), peak_memory_mb is 0.0.
    """
    import time as _time
    wall = _time.perf_counter() - start_time
    rate = items_processed / wall if wall > 0 else 0.0
    try:
        import resource
        import sys as _sys
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if _sys.platform == "darwin":
            peak_mb = rss / (1024 * 1024)  # macOS: bytes
        else:
            peak_mb = rss / 1024  # Linux: KB
    except ImportError:
        peak_mb = 0.0
    return wall, rate, peak_mb
