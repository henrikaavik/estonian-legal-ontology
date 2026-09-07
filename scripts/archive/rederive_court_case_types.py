#!/usr/bin/env python3
"""Re-derive estleg:caseType for Riigikohus court decisions (#342).

``generate_court_decisions.classify_case`` was fixed for #342: an old-format
case number ``3-<chamber>-…`` (1996–2017) encodes the case type in the *chamber*
digit (segment 2), not the leading ``3`` (which is the court code). The previous
classifier read the first digit — always ``3`` — and stamped every old-format
decision ``CaseType_Administrative``. The committed ``riigikohus/*_peep.json``
predate that fix, so ~7,169 decisions are mis-typed.

This standalone, offline pass re-runs the already-correct classifier over the
existing decision nodes and rewrites the peeps in place, then refreshes the
``case_type_counts`` / ``case_type_counts_per_year`` aggregates in
``RIIGIKOHUS_INDEX.json`` so the distribution matches. No network; idempotent;
adds no new tracked file (count-drift safe). The classifier and the year-bucket
logic are IMPORTED from generate_court_decisions so this can never drift from the
generator or its #342 regression tests.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg.estleg_common import REPO_ROOT, save_json
from estleg.generate_court_decisions import _decision_year_key, classify_case

CASE_TYPE_PREFIX = "estleg:CaseType_"


def _types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else t


def _is_decision(node: dict) -> bool:
    return isinstance(node, dict) and "estleg:CourtDecision" in _types(node)


def _node_to_dec(node: dict) -> dict:
    """Adapt a peep node to the ``{case_nr, date}`` dict _decision_year_key wants.

    The node stores ``estleg:decisionDate`` as an ISO value-object
    ({"@value": "YYYY-MM-DD"}); _decision_year_key's fallback parses DD.MM.YYYY,
    so convert. The modern-case-number path needs no date and is unaffected.
    """
    cn = node.get("estleg:caseNumber", "") or ""
    dd = node.get("estleg:decisionDate")
    iso = dd.get("@value") if isinstance(dd, dict) else (dd if isinstance(dd, str) else "")
    date_ddmmyyyy = ""
    if isinstance(iso, str) and len(iso) >= 10 and iso[4] == "-" and iso[7] == "-":
        date_ddmmyyyy = f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"
    return {"case_nr": cn, "date": date_ddmmyyyy}


def rederive_file(path: Path, dry_run: bool) -> int:
    """Rewrite estleg:caseType on every decision in one peep. Returns #changed."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for node in doc.get("@graph", []):
        if not _is_decision(node):
            continue
        cn = node.get("estleg:caseNumber")
        if not isinstance(cn, str) or not cn:
            continue
        new_iri = CASE_TYPE_PREFIX + classify_case(cn)[0]
        cur = node.get("estleg:caseType")
        cur_iri = cur.get("@id") if isinstance(cur, dict) else None
        if cur_iri != new_iri:
            node["estleg:caseType"] = {"@id": new_iri}
            changed += 1
    if changed and not dry_run:
        save_json(path, doc)
    return changed


def refresh_index(rk_dir: Path, dry_run: bool) -> bool:
    """Recompute case_type_counts + case_type_counts_per_year in RIIGIKOHUS_INDEX.

    Tallies via classify_case(caseNumber) — identical to the generator — so the
    aggregates match a from-scratch build. Only these two keys are touched; the
    rest of the index (total_decisions, years, full-text stats, generated) is
    preserved. Returns True iff the index changed.
    """
    index_path = rk_dir / "RIIGIKOHUS_INDEX.json"
    if not index_path.exists():
        return False
    index = json.loads(index_path.read_text(encoding="utf-8"))
    type_counts: dict[str, int] = {}
    per_year: dict[str, dict[str, int]] = {}
    for peep in sorted(rk_dir.glob("riigikohus_*_peep.json")):
        doc = json.loads(peep.read_text(encoding="utf-8"))
        for node in doc.get("@graph", []):
            if not _is_decision(node):
                continue
            cn = node.get("estleg:caseNumber")
            if not isinstance(cn, str) or not cn:
                continue
            type_id = classify_case(cn)[0]
            type_counts[type_id] = type_counts.get(type_id, 0) + 1
            yk = _decision_year_key(_node_to_dec(node))
            per_year.setdefault(yk, {})
            per_year[yk][type_id] = per_year[yk].get(type_id, 0) + 1
    if index.get("case_type_counts") == type_counts and \
            index.get("case_type_counts_per_year") == per_year:
        return False
    index["case_type_counts"] = type_counts
    index["case_type_counts_per_year"] = per_year
    if not dry_run:
        save_json(index_path, index)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--krr-dir", type=Path, default=REPO_ROOT / "krr_outputs",
                        help="Corpus root (default: <repo>/krr_outputs).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing.")
    args = parser.parse_args(argv)
    rk_dir = args.krr_dir / "riigikohus"

    print("=" * 70)
    print("Re-derive Riigikohus court-decision caseType (#342)")
    print("=" * 70)
    total = 0
    for peep in sorted(rk_dir.glob("riigikohus_*_peep.json")):
        n = rederive_file(peep, args.dry_run)
        total += n
        if n:
            print(f"  {peep.name}: {n} caseType re-derived")
    idx_changed = refresh_index(rk_dir, args.dry_run)
    verb = "would re-derive" if args.dry_run else "re-derived"
    print(f"\n{verb} {total} caseType values across riigikohus peeps."
          f" Index {'would be ' if args.dry_run else ''}"
          f"{'updated' if idx_changed else 'unchanged'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
