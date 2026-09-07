#!/usr/bin/env python3
"""Screen committed court decisions for Estonian personal ID codes (#683).

Riigikohus decision text is scraped verbatim from rikos.rik.ee. The court's own
anonymisation reliably initials party *names*, but an ``isikukood`` sometimes
survives in the body of the reasoning ("isikukoodiga 3XXXXXXXXXX"). A personal
identification code is a direct identifier under the GDPR, so it must not sit
in a published ``estleg:summary`` / ``estleg:legalText`` literal.

``estleg.generate_court_decisions`` now screens both literals at their write
sites, but the committed corpus predates that guard. This module is the
offline backfill: a deterministic, idempotent pass over
``krr_outputs/riigikohus/*_peep.json`` that replaces every isolated,
checksum-valid, date-plausible 11-digit run with
:data:`estleg.estleg_common.PERSONAL_CODE_PLACEHOLDER` — except a run
introduced by a label that states it is not a person (``registrikood``,
``otsuse nr``); see ``screen_personal_data`` — and stamps
``estleg:personalDataScreened`` / ``estleg:personalDataMaskedCount`` on every
``estleg:CourtDecision`` node.

Usage::

    python3 -m estleg.screen_court_personal_data --dry-run
    python3 -m estleg.screen_court_personal_data

Idempotency: the placeholder holds no digits, and the masked count accumulates
rather than overwrites, so a second run finds nothing, changes nothing, and
rewrites no file. The audit sidecar
``krr_outputs/reports/personal_data_mask_report.json`` is merged with whatever
it already holds for the same reason — the codes are gone from the corpus after
the first run, so the report is the only surviving record and must not be
emptied by a rerun.

The report and every log line carry masked forms only (``344****38``): the
whole point of the pass is that the full codes stop existing in tracked files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    report_file,
    save_json,
    screen_personal_data,
    stamp_personal_data_screening,
)

COURT_DECISION_TYPE = "estleg:CourtDecision"

# Only these two literals are published prose scraped from the court site.
# Structured fields (case number, ECLI, rikObjectId) are court-assigned
# document identifiers, not personal data, and are deliberately not screened.
SCREENED_FIELDS: tuple[str, ...] = ("estleg:summary", "estleg:legalText")

REPORT_NAME = "personal_data_mask_report.json"
DEFAULT_DIR = KRR_DIR / "riigikohus"
INDEX_NAME = "RIIGIKOHUS_INDEX.json"
SCREENING_BLOCK = "personal_data_screening"
# The block is inserted after this sibling so it sits with the other
# provenance metadata rather than after the per-year count tables.
INDEX_ANCHOR_KEY = "full_text"


# ---------------------------------------------------------------------------
# Node-level screening
# ---------------------------------------------------------------------------

def _screen_value(value: object) -> tuple[object, list[dict]]:
    """Screen one JSON-LD literal value. Returns ``(new_value, findings)``.

    Handles the three shapes the corpus actually uses for the screened
    fields: a plain string (``estleg:legalText``, and older
    ``estleg:summary`` rows), a language-tagged object (newer
    ``estleg:summary`` rows), and a list mixing the two. Unrecognised shapes
    are returned untouched rather than coerced.
    """
    if isinstance(value, str):
        return screen_personal_data(value)

    if isinstance(value, dict):
        inner = value.get("@value")
        if not isinstance(inner, str):
            return value, []
        screened, findings = screen_personal_data(inner)
        if not findings:
            return value, []
        # Copy so the language tag / datatype and key order survive.
        replacement = dict(value)
        replacement["@value"] = screened
        return replacement, findings

    if isinstance(value, list):
        findings: list[dict] = []
        items: list[object] = []
        for item in value:
            new_item, found = _screen_value(item)
            items.append(new_item)
            findings.extend(found)
        return (items, findings) if findings else (value, [])

    return value, []


def is_court_decision(node: object) -> bool:
    """True for a JSON-LD node typed ``estleg:CourtDecision``."""
    if not isinstance(node, dict):
        return False
    types = node.get("@type")
    if isinstance(types, str):
        return types == COURT_DECISION_TYPE
    if isinstance(types, list):
        return COURT_DECISION_TYPE in types
    return False


def screen_decision_node(node: dict) -> tuple[bool, list[dict]]:
    """Screen one decision node in place. Returns ``(changed, findings)``.

    ``changed`` is True when a literal was rewritten *or* the #683 stamp was
    added or updated, so a corpus that is already clean still gets stamped
    exactly once.
    """
    findings: list[dict] = []
    changed = False
    for field in SCREENED_FIELDS:
        if field not in node:
            continue
        new_value, found = _screen_value(node[field])
        if not found:
            continue
        # Assigning to an existing key keeps its position in the node.
        node[field] = new_value
        findings.extend(found)
        changed = True

    if stamp_personal_data_screening(node, len(findings)):
        changed = True
    return changed, findings


# ---------------------------------------------------------------------------
# File-level screening
# ---------------------------------------------------------------------------

def screen_document(doc: object, file_name: str) -> tuple[bool, list[dict]]:
    """Screen every decision node in a loaded peep. Returns ``(changed, rows)``.

    Each row is ``{"@id", "file", "masked", "labelled"}`` for one node that
    actually had a code masked; nodes that were merely stamped produce no row.
    """
    if not isinstance(doc, dict):
        return False, []
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return False, []

    changed = False
    rows: list[dict] = []
    for node in graph:
        if not is_court_decision(node):
            continue
        node_changed, findings = screen_decision_node(node)
        changed = changed or node_changed
        if not findings:
            continue
        rows.append(
            {
                "@id": node.get("@id", "?"),
                "file": file_name,
                "masked": sorted(f["masked"] for f in findings),
                "labelled": sum(1 for f in findings if f["labelled"]),
            }
        )
    return changed, rows


def count_decision_nodes(doc: object) -> int:
    """Number of ``estleg:CourtDecision`` nodes in a loaded peep."""
    if not isinstance(doc, dict):
        return 0
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return 0
    return sum(1 for node in graph if is_court_decision(node))


def peep_files(directory: Path) -> list[Path]:
    """Deterministically ordered ``*_peep.json`` files under *directory*."""
    return sorted(directory.glob("*_peep.json"), key=lambda p: p.name)


# ---------------------------------------------------------------------------
# Audit report
# ---------------------------------------------------------------------------

def _load_report(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def merge_report(existing: dict, rows: list[dict], nodes_scanned: int) -> dict:
    """Fold this run's rows into the previous report.

    The corpus no longer holds the codes after the first run, so the report is
    the only record that a given node was masked. Merging (rather than
    replacing) is what makes a rerun a byte-for-byte no-op instead of an
    erasure. Rows are keyed by node ``@id``; masked forms are unioned and
    sorted so the output does not depend on file iteration order.
    """
    merged: dict[str, dict] = {}
    for entry in existing.get("entries") or []:
        if isinstance(entry, dict) and isinstance(entry.get("@id"), str):
            merged[entry["@id"]] = {
                "@id": entry["@id"],
                "file": entry.get("file", ""),
                "masked": sorted(
                    m for m in (entry.get("masked") or []) if isinstance(m, str)
                ),
                "labelled": int(entry.get("labelled") or 0),
            }

    for row in rows:
        node_id = row["@id"]
        prior = merged.get(node_id)
        if prior is None:
            merged[node_id] = dict(row)
            continue
        prior["file"] = row["file"]
        prior["masked"] = sorted(prior["masked"] + row["masked"])
        prior["labelled"] += row["labelled"]

    entries = [merged[key] for key in sorted(merged)]
    codes_masked = sum(len(entry["masked"]) for entry in entries)
    labelled = sum(entry["labelled"] for entry in entries)
    return {
        "generated": BUILD_EVALUATION_DATE,
        "totals": {
            "nodes_scanned": nodes_scanned,
            "nodes_masked": len(entries),
            "codes_masked": codes_masked,
            "labelled": labelled,
            "unlabelled": codes_masked - labelled,
        },
        "entries": entries,
    }


def update_index(index_path: Path, totals: dict) -> bool:
    """Add / refresh the ``personal_data_screening`` block. True if changed.

    Values come from the *merged* report totals, not from this run's finds, so
    a rerun that masks nothing leaves the index byte-identical.
    """
    if not index_path.is_file():
        return False
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(index, dict):
        return False

    block = {
        "generated": BUILD_EVALUATION_DATE,
        "nodes_masked": totals["nodes_masked"],
        "codes_masked": totals["codes_masked"],
    }
    if index.get(SCREENING_BLOCK) == block:
        return False

    if SCREENING_BLOCK in index:
        index[SCREENING_BLOCK] = block
        rebuilt = index
    else:
        rebuilt = {}
        for key, value in index.items():
            rebuilt[key] = value
            if key == INDEX_ANCHOR_KEY:
                rebuilt[SCREENING_BLOCK] = block
        if SCREENING_BLOCK not in rebuilt:
            rebuilt[SCREENING_BLOCK] = block

    save_json(index_path, rebuilt)
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    directory: Path = DEFAULT_DIR,
    *,
    dry_run: bool = False,
    krr_dir: Path = KRR_DIR,
) -> dict:
    """Screen every peep under *directory*. Returns a stats dict."""
    files = peep_files(directory)
    if not files:
        raise SystemExit(f"no *_peep.json files found under {directory}")

    rows: list[dict] = []
    nodes_scanned = 0
    files_changed: list[str] = []
    per_file: dict[str, dict[str, int]] = {}

    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"{path.name}: unreadable JSON — {exc}") from exc

        nodes_scanned += count_decision_nodes(doc)
        changed, file_rows = screen_document(doc, path.name)
        rows.extend(file_rows)
        if file_rows:
            per_file[path.name] = {
                "nodes_masked": len(file_rows),
                "codes_masked": sum(len(r["masked"]) for r in file_rows),
                "labelled": sum(r["labelled"] for r in file_rows),
            }
        if changed:
            files_changed.append(path.name)
            if not dry_run:
                save_json(path, doc)

    report_path = report_file(krr_dir, REPORT_NAME)
    report = merge_report(_load_report(report_path), rows, nodes_scanned)
    index_changed = False
    if not dry_run:
        save_json(report_path, report)
        index_changed = update_index(directory / INDEX_NAME, report["totals"])

    return {
        "files_scanned": len(files),
        "files_changed": files_changed,
        "nodes_scanned": nodes_scanned,
        "run_nodes_masked": len(rows),
        "run_codes_masked": sum(len(r["masked"]) for r in rows),
        "run_labelled": sum(r["labelled"] for r in rows),
        "per_file": per_file,
        "report": report,
        "report_path": report_path,
        "index_changed": index_changed,
        "dry_run": dry_run,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Mask Estonian personal ID codes in committed court decision "
            "literals and stamp #683 screening provenance."
        )
    )
    parser.add_argument(
        "--dir",
        dest="directory",
        type=Path,
        default=DEFAULT_DIR,
        help=f"directory of *_peep.json files to screen (default: {DEFAULT_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing any file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stats = run(args.directory, dry_run=args.dry_run)

    totals = stats["report"]["totals"]
    mode = "DRY RUN — no files written" if stats["dry_run"] else "applied"
    print(f"--- Personal ID code screening (#683) — {mode} ---")
    print(f"  files scanned:            {stats['files_scanned']}")
    print(f"  decision nodes scanned:   {stats['nodes_scanned']}")
    print(f"  nodes masked this run:    {stats['run_nodes_masked']}")
    print(f"  codes masked this run:    {stats['run_codes_masked']} "
          f"({stats['run_labelled']} labelled, "
          f"{stats['run_codes_masked'] - stats['run_labelled']} unlabelled)")
    print(f"  files needing a rewrite:  {len(stats['files_changed'])}")
    print(
        f"  report totals (cumulative): nodes_masked={totals['nodes_masked']}, "
        f"codes_masked={totals['codes_masked']}, "
        f"labelled={totals['labelled']}, unlabelled={totals['unlabelled']}"
    )
    for name in sorted(stats["per_file"]):
        counts = stats["per_file"][name]
        print(
            f"    {name}: {counts['nodes_masked']} nodes, "
            f"{counts['codes_masked']} codes "
            f"({counts['labelled']} labelled)"
        )
    if not stats["dry_run"]:
        print(f"  report: {stats['report_path']}")
        print(f"  index block updated: {stats['index_changed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
