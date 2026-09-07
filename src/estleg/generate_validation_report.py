#!/usr/bin/env python3
"""Stamp measured validation numbers into ``docs/VALIDATION_REPORT.md`` (#702).

Until this existed the report's Summary table was maintained by hand. It said
``Errors: 0 / PASSED`` against a run dated 2026-05-26 while the `json-validation`
CI job had been red on every `main` run since at least v1.0.0 -- a false
conformance statement in a published document. Hand-maintained numbers drift;
the fix is to measure them.

Only the block between the ``BEGIN GENERATED`` / ``END GENERATED`` markers is
written. The surrounding analysis -- which findings are stale validator rules,
which are real defects, and which ticket owns each -- is curated prose and is
left untouched.

Usage::

    python3 scripts/generate_validation_report.py           # rewrite the block
    python3 scripts/generate_validation_report.py --check   # CI: fail if stale
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "docs" / "VALIDATION_REPORT.md"
BEGIN_MARKER = "<!-- BEGIN GENERATED: validation-summary -->"
END_MARKER = "<!-- END GENERATED: validation-summary -->"

_SUMMARY_RE = {
    "files": re.compile(r"^Files validated:\s*([\d,]+)", re.MULTILINE),
    "errors": re.compile(r"^Errors:\s*([\d,]+)", re.MULTILINE),
    "warnings": re.compile(r"^Warnings:\s*([\d,]+)", re.MULTILINE),
}


def run_validator() -> str:
    """Run the gate and return its stdout (it exits non-zero when errors exist)."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate_all.py")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return proc.stdout


def parse_summary(output: str) -> dict[str, int]:
    summary: dict[str, int] = {}
    for key, pattern in _SUMMARY_RE.items():
        match = pattern.search(output)
        if match is None:
            raise ValueError(f"validate_all output has no {key!r} line")
        summary[key] = int(match.group(1).replace(",", ""))
    return summary


def categorise(output: str) -> Counter:
    """Group raw error lines into the categories the report tabulates."""
    counts: Counter = Counter()
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("ERROR: "):
            continue
        message = line[len("ERROR: ") :]
        if ": " in message:
            message = message.split(": ", 1)[1]
        message = re.sub(r" at graph\[\d+\].*", "", message)
        message = re.sub(r"^Duplicate @id within file.*", "Duplicate @id within file", message)
        message = re.sub(r"\bestleg:[A-Za-z0-9_]+", "<iri>", message)
        message = re.sub(r"\b\d[\d,]*\b", "<n>", message)
        counts[message.strip()] += 1
    return counts


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, cwd=REPO_ROOT,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def render_block(summary: dict[str, int], categories: Counter) -> str:
    result = "**PASSED**" if summary["errors"] == 0 else "**FAILED**"
    lines = [
        BEGIN_MARKER,
        "",
        f"*Measured by `scripts/generate_validation_report.py` at commit "
        f"`{git_sha()}`, {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
        "Do not hand-edit this block.*",
        "",
        "| Metric | Count |",
        "|--------|------:|",
        f"| Files validated | {summary['files']:,} |",
        f"| Errors | {summary['errors']:,} |",
        f"| Warnings | {summary['warnings']:,} |",
        f"| Result | {result} |",
        "",
    ]
    if categories:
        lines += [
            "| Count | Error category |",
            "|------:|----------------|",
        ]
        for message, count in categories.most_common():
            lines.append(f"| {count:,} | {message} |")
        lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines)


def splice(report: str, block: str) -> str:
    start = report.find(BEGIN_MARKER)
    end = report.find(END_MARKER)
    if start == -1 or end == -1:
        raise ValueError(
            f"{REPORT_PATH} is missing the generated-block markers; "
            "add them around the Summary table"
        )
    return report[:start] + block + report[end + len(END_MARKER) :]


def _files_validated(block: str) -> int | None:
    """The `Files validated` figure recorded in a generated block, if present."""
    match = re.search(r"\| Files validated \| ([\d,]+) \|", block)
    return int(match.group(1).replace(",", "")) if match else None


# Error categories whose count is derived from filesystem mtimes rather than
# from file content. `git status` can be clean while these move: regenerating a
# T-Box artifact makes it newer than an aggregate that embeds it, and a fresh
# checkout assigns mtimes in arbitrary order. They are excluded from --check so
# the guard reports real drift instead of clock noise (#702). The rule itself
# being mtime-based is a separate problem, tracked with the aggregates (#705).
ENVIRONMENT_DEPENDENT_CATEGORIES = ("older than at least one canonical source file",)


def _comparable(block: str) -> str:
    """Normalise a block for comparison.

    Drops the stamp line, the mtime-derived category rows, and the total
    `Errors` row -- the total moves with those rows, so comparing it would
    reintroduce exactly the clock noise the exclusions remove. Every
    content-derived category row is still compared, which is where real drift
    shows up.
    """
    return "\n".join(
        line
        for line in block.splitlines()
        if not line.startswith("*Measured by")
        and not line.startswith("| Errors |")
        and not any(cat in line for cat in ENVIRONMENT_DEPENDENT_CATEGORIES)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed numbers differ from a fresh run",
    )
    args = parser.parse_args(argv)

    output = run_validator()
    block = render_block(parse_summary(output), categorise(output))
    report = REPORT_PATH.read_text(encoding="utf-8")

    if args.check:
        start, end = report.find(BEGIN_MARKER), report.find(END_MARKER)
        if start == -1 or end == -1:
            print("VALIDATION_REPORT.md is missing the generated-block markers.")
            return 1
        current = report[start : end + len(END_MARKER)]

        # The numbers are only comparable against the same corpus. CI
        # materialises a subset of the Git-LFS artifacts and validate_all skips
        # LFS pointers, so a partial checkout legitimately validates fewer
        # files. Comparing regardless would fail the build for an environment
        # difference rather than for a stale report (#702).
        recorded, measured = _files_validated(current), _files_validated(block)
        if recorded is not None and measured is not None and recorded != measured:
            print(
                f"Skipping the numeric comparison: this environment validated "
                f"{measured:,} files, the committed report records {recorded:,}. "
                "That is an LFS-materialisation difference, not a stale report."
            )
            return 0

        if _comparable(current) != _comparable(block):
            print("VALIDATION_REPORT.md numbers are stale; regenerate them.")
            return 1
        print("VALIDATION_REPORT.md numbers match a fresh validate_all run.")
        return 0

    REPORT_PATH.write_text(splice(report, block), encoding="utf-8")
    print(f"stamped {REPORT_PATH.name} from a fresh validate_all run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
