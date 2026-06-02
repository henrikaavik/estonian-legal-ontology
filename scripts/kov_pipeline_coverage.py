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
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


_FAILURE_SAMPLE_CAP = 20


# Pinned, deterministic ``run_timestamp`` for git-tracked coverage reports
# (issue #295). Embedding a live ``datetime.now()`` re-diffed every tracked
# coverage file on each run (timestamp-only churn). ``CoverageReport`` requires
# a valid UTC ISO-8601 timestamp, so we pin a fixed epoch sentinel rather than a
# wall clock; the epoch makes "this is not a real run clock" unmistakable, and
# genuine run time lives in ``wall_time_seconds``. Shared here so every coverage
# writer imports the same value instead of redeclaring it locally.
PINNED_RUN_TIMESTAMP = "1970-01-01T00:00:00+00:00"


# RFC-3339 / ISO-8601 UTC timestamp regex. We accept either a `Z`
# suffix (the spec's canonical UTC marker) or `+00:00` (what
# `datetime.isoformat()` produces). Sub-second precision is optional.
_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|\+00:00)$"
)


def validate_run_timestamp(value: str) -> str:
    """Validate that `value` is a UTC ISO-8601 timestamp.

    Returns the input unchanged on success. Raises ValueError with a
    pointed message on any of:
      - non-string input
      - unparseable shape (regex mismatch)
      - parses but is not UTC (tzinfo missing or offset != UTC)

    Use `format_run_timestamp(datetime)` to produce a value that
    passes this check from a `datetime` object (Finding #13 in
    issue #182).
    """
    if not isinstance(value, str):
        raise ValueError(
            f"run_timestamp must be a string, got {type(value).__name__}"
        )
    if not _UTC_TIMESTAMP_RE.match(value):
        raise ValueError(
            f"run_timestamp {value!r} is not an ISO-8601 UTC timestamp; "
            "expected e.g. '2026-05-10T12:34:56Z' or "
            "'2026-05-10T12:34:56+00:00'"
        )
    # Also parse it via fromisoformat as a defence against shapes the
    # regex permits but Python rejects (e.g. impossible day numbers).
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) != timezone.utc.utcoffset(parsed):
        raise ValueError(
            f"run_timestamp {value!r} parses with tzinfo "
            f"{parsed.tzinfo!r}; UTC offset is required."
        )
    return value


def format_run_timestamp(when: datetime) -> str:
    """Serialise a `datetime` as an ISO-8601 UTC timestamp suitable for
    `CoverageReport.run_timestamp`.

    Naive `when` values are rejected (tzinfo-required) so we don't
    silently mark a local-time wall-clock value as UTC.
    """
    if when.tzinfo is None:
        raise ValueError(
            "format_run_timestamp requires a tz-aware datetime; pass "
            "datetime.now(timezone.utc), not datetime.now()."
        )
    return when.astimezone(timezone.utc).isoformat()


@dataclass
class CoverageReport:
    """Per-run coverage metrics. Field set matches the spec's
    "Per-pipeline KOV coverage report" requirement (counts, runtime
    metrics, failure samples)."""

    pipeline: str
    run_timestamp: str        # ISO-8601 UTC; see validate_run_timestamp
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

    Validates `run_timestamp` is a UTC ISO-8601 string before
    serialising so a wall-clock-style timestamp can never end up in a
    machine-consumed report (Finding #13). Caps `failure_samples` at
    20 entries (per the spec); writes to a temp file in the same
    directory, then renames into place so a crash mid-write doesn't
    leave a partial file.
    """
    validate_run_timestamp(report.run_timestamp)
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
    a platform-specific normalization to MB:
      * Linux: ``ru_maxrss`` is in KB → divide by 1024
      * macOS: ``ru_maxrss`` is in bytes → divide by 1024**2
      * Windows: ``resource`` is unavailable → 0.0

    A sanity assertion guards against the historical macOS double-
    divide bug: a 100 MB process would have ru_maxrss ≈ 1e8 (bytes)
    or ≈ 1e5 (KB if mis-typed). If the resulting MB value implies
    >1 TB of RSS we treat that as a unit-detection regression and
    raise — silently emitting nonsensical memory numbers into the
    coverage report would mask real bugs (Finding #10).
    """
    import sys as _sys
    import time as _time
    wall = _time.perf_counter() - start_time
    rate = items_processed / wall if wall > 0 else 0.0
    if _sys.platform.startswith("win"):
        # `resource` is not available on Windows, and `psutil` is not
        # in this project's dependency set. Surface the gap
        # explicitly rather than emit a misleading 0 with no comment.
        return wall, rate, 0.0
    try:
        import resource
    except ImportError:
        return wall, rate, 0.0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if _sys.platform == "darwin":
        peak_mb = rss / (1024 * 1024)  # macOS: bytes
    else:
        peak_mb = rss / 1024  # Linux/BSD: KB
    # Sanity: any RSS reading that comes back as >1 TB is a unit-
    # detection bug, not a real datapoint. Catching this here means
    # the bad reading is loud, not silently propagated into the
    # coverage report's `peak_memory_mb` field.
    assert peak_mb < 1_000_000, (
        f"peak_memory_mb={peak_mb} on platform {_sys.platform!r}; "
        "ru_maxrss unit detection is wrong."
    )
    return wall, rate, peak_mb
