#!/usr/bin/env python3
"""Generate ``docs/DUPLICATE_IDS_REPORT.md`` from the corpus (#702).

The committed report was produced from a pytest fixture, not from
``krr_outputs/``: it named one collision, ``estleg:Shared``, across files
including ``root_a_peep.json``, which exists only inside a ``tmp_path`` in
``tests/test_fix_all_issues.py``. Meanwhile the real corpus carries dozens of
genuine in-file duplicates. A wrong report in ``docs/`` is worse than no
report, so this module regenerates it from the shipped data.

Two distinct defects are reported:

* **in-file duplicates** -- the same ``@id`` twice inside one ``@graph``. In
  JSON-LD these silently merge on load, so one node's fields overwrite the
  other's.
* **cross-file collisions** -- one ``@id`` declared in several files. Legitimate
  for shared vocabulary terms, so these are reported as context, not as errors.

Usage::

    python3 scripts/generate_duplicate_ids_report.py
    python3 scripts/generate_duplicate_ids_report.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from estleg.estleg_common import KRR_DIR

REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "DUPLICATE_IDS_REPORT.md"
_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return handle.readline().startswith(_LFS_POINTER_PREFIX)
    except (OSError, UnicodeDecodeError):
        return False


def iter_corpus_files(krr_dir: Path | None = None) -> list[Path]:
    """Every readable JSON/JSON-LD file in the corpus, LFS pointers excluded."""
    krr_dir = krr_dir if krr_dir is not None else KRR_DIR
    files = [
        path
        for path in sorted(krr_dir.rglob("*"))
        if path.suffix in {".json", ".jsonld"} and path.is_file()
    ]
    return [path for path in files if not _is_lfs_pointer(path)]


def collect(krr_dir: Path | None = None) -> tuple[dict[str, Counter], dict[str, set[str]]]:
    """Return ``(in_file_duplicates, cross_file_ids)`` for the corpus."""
    krr_dir = krr_dir if krr_dir is not None else KRR_DIR
    in_file: dict[str, Counter] = {}
    cross_file: dict[str, set[str]] = defaultdict(set)
    for path in iter_corpus_files(krr_dir):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if not isinstance(doc, dict):
            continue
        graph = doc.get("@graph")
        if not isinstance(graph, list):
            continue
        counts: Counter = Counter(
            node["@id"]
            for node in graph
            if isinstance(node, dict) and isinstance(node.get("@id"), str)
        )
        duplicates = Counter({nid: n for nid, n in counts.items() if n > 1})
        if duplicates:
            in_file[str(path.relative_to(krr_dir))] = duplicates
        for nid in counts:
            cross_file[nid].add(str(path.relative_to(krr_dir)))
    return in_file, cross_file


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def render(
    in_file: dict[str, Counter],
    cross_file: dict[str, set[str]],
    scanned: int,
) -> str:
    total_dupes = sum(sum(c.values()) - len(c) for c in in_file.values())
    collisions = {
        nid: files for nid, files in cross_file.items() if len(files) > 1
    }
    lines = [
        "# Duplicate `@id` Report",
        "",
        f"Generated from `krr_outputs/` at commit `{_git_sha()}` on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by "
        "`scripts/generate_duplicate_ids_report.py`. Do not hand-edit.",
        "",
        f"Files scanned: {scanned:,}.",
        "",
        "## In-file duplicates",
        "",
        "The same `@id` appearing twice in one `@graph`. JSON-LD merges these "
        "on load, so one node's fields silently overwrite the other's. These "
        "are defects.",
        "",
    ]
    if not in_file:
        lines += ["None.", ""]
    else:
        lines += [
            f"**{total_dupes} duplicate node(s) across {len(in_file)} file(s).**",
            "",
            "| File | `@id` | Occurrences |",
            "|------|-------|------------:|",
        ]
        for path in sorted(in_file):
            for nid, count in sorted(in_file[path].items()):
                lines.append(f"| `{path}` | `{nid}` | {count} |")
        lines.append("")

    lines += [
        "## Cross-file `@id` reuse",
        "",
        "One `@id` declared in more than one file. This is expected for shared "
        "vocabulary and closure stubs, so it is reported as context rather "
        "than as an error.",
        "",
        f"**{len(collisions)} `@id`(s) appear in more than one file.**",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed report differs from a fresh render",
    )
    args = parser.parse_args(argv)

    scanned = len(iter_corpus_files())
    in_file, cross_file = collect()
    rendered = render(in_file, cross_file, scanned)

    if args.check:
        current = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.is_file() else ""
        # Added/deleted inputs and missing LFS materialisation both change the
        # count. Neither permits us to certify the committed report as current.
        recorded = _scanned(current)
        if recorded is not None and recorded != scanned:
            print(
                f"Cannot verify DUPLICATE_IDS_REPORT.md: this environment scanned {scanned:,} "
                f"files, the committed report records {recorded:,}. "
                "Materialise missing inputs or regenerate the report for the changed corpus."
            )
            return 1
        # The generated-at line carries a timestamp, so compare the body only.
        if _body(current) != _body(rendered):
            print("DUPLICATE_IDS_REPORT.md is stale; regenerate it.")
            return 1
        print("DUPLICATE_IDS_REPORT.md is current.")
        return 0

    REPORT_PATH.write_text(rendered, encoding="utf-8")
    print(
        f"wrote {REPORT_PATH.relative_to(REPORT_PATH.parents[1])}: "
        f"{len(in_file)} file(s) with in-file duplicates"
    )
    return 0


def _scanned(text: str) -> int | None:
    """The `Files scanned` figure recorded in a report, if present."""
    match = re.search(r"^Files scanned: ([\d,]+)\.", text, re.MULTILINE)
    return int(match.group(1).replace(",", "")) if match else None


def _body(text: str) -> str:
    """Strip the generated-at line so --check ignores SHA/timestamp churn."""
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith("Generated from")
    )


if __name__ == "__main__":
    raise SystemExit(main())
