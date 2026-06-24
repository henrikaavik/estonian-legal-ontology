#!/usr/bin/env python3
"""Derive act-level ``estleg:temporalStatus`` from version sidecars (#617).

The fitness eval (#617) flagged ``temporalStatus = unknown`` on ~100 % of laws —
the corpus can't say whether a law is currently in force. But the answer already
ships: each law's per-provision redaction chain in
``krr_outputs/provision_versions/<law>.jsonld`` records, from authoritative Riigi
Teataja dates, exactly which redaction is current. ``temporalStatus`` is
``unknown`` only because the *act-level* XML metadata lacked entry-into-force /
repeal dates (the historical reason in ``extract_temporal_data``), not because
the information is absent.

The ontology models temporal validity at the **act** level (#128 — a closed-world
gate in ``validate_all`` rejects ``temporalStatus`` on non-Act nodes), so this
fills the gap *there*: for each law it aggregates the current-redaction status of
its provisions and stamps the derived status onto the law's ``estleg:Act``
node(s) — but **only where the act is currently ``unknown``**, never overwriting a
value ``extract_temporal_data`` already determined.

Status semantics match ``extract_temporal_data.determine_temporal_status``
(``inForce`` / ``repealed`` / ``notYetEffective``). A law with no version data is
left ``unknown`` (nothing to derive from).

This is a **surgical, offline, deterministic** post-process (no network). It is
NOT in the legal-review-gated paths (sanctions / deontic / court / transposition);
it is a mechanical derivation from authoritative redaction dates. Defaults to the
VÕS flagship; ``--all`` applies corpus-wide (a larger change — review the eval
delta + closure gate).

Idempotent; ``--dry-run`` reports without writing; atomic writes via ``save_json``.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from estleg_common import BUILD_EVALUATION_DATE, KRR_DIR, save_json

# Default demonstration scope: Võlaõigusseadus (the obligations code — currently
# ``unknown`` despite being plainly in force, and the act behind the #578/#631
# harmonisation work).
DEFAULT_FLAGSHIP_LAWS = ("volaoigusseadus",)

VERSION_DIR = KRR_DIR / "provision_versions"


def _date_value(value) -> str | None:
    if isinstance(value, dict):
        v = value.get("@value")
        return v if isinstance(v, str) else None
    return value if isinstance(value, str) else None


def _current_version(versions: list[dict]) -> dict | None:
    """The chain tail — the redaction nothing supersedes (#574: walk the
    supersession links, don't validFrom-sort). Falls back to latest validFrom if
    the data is malformed and presents several tails."""
    tails = [v for v in versions if not v.get("estleg:supersededByVersion")]
    if not tails:
        return None
    if len(tails) == 1:
        return tails[0]
    return max(tails, key=lambda v: _date_value(v.get("estleg:versionValidFrom")) or "")


def _provision_status(versions: list[dict], evaluation_date: str) -> str | None:
    current = _current_version(versions)
    if current is None:
        return None
    valid_from = _date_value(current.get("estleg:versionValidFrom"))
    valid_to = _date_value(current.get("estleg:versionValidTo"))
    if valid_from and valid_from > evaluation_date:
        return "notYetEffective"
    if valid_to and valid_to < evaluation_date:
        return "repealed"
    if valid_from is None and valid_to is None:
        return None
    return "inForce"


def derive_act_status(law_name: str, evaluation_date: str) -> str | None:
    """Aggregate a law's provision redactions into one act-level temporalStatus.

    A law is ``inForce`` if any provision's current redaction is in force; else
    ``notYetEffective`` if any is future; else ``repealed`` if it has (only) ended
    redactions. ``None`` when the law has no usable version data.
    """
    sidecar = VERSION_DIR / f"{law_name}.jsonld"
    if not sidecar.exists():
        return None
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    by_provision: dict[str, list[dict]] = defaultdict(list)
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        target = node.get("estleg:versionOf")
        if isinstance(target, dict) and isinstance(target.get("@id"), str):
            by_provision[target["@id"]].append(node)
    seen: Counter = Counter()
    for versions in by_provision.values():
        status = _provision_status(versions, evaluation_date)
        if status is not None:
            seen[status] += 1
    if not seen:
        return None
    if seen.get("inForce"):
        return "inForce"
    if seen.get("notYetEffective"):
        return "notYetEffective"
    return "repealed"


def _is_act(node: dict) -> bool:
    types = node.get("@type", [])
    if isinstance(types, str):
        types = [types]
    return "estleg:Act" in types


def apply_to_file(path: Path, status: str) -> tuple[int, dict]:
    """Stamp the derived status onto Act nodes that are currently ``unknown``.

    Returns (acts_changed, doc). Only fills ``unknown`` / missing — never
    overwrites a status ``extract_temporal_data`` already set.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for node in doc.get("@graph", []):
        if not isinstance(node, dict) or not _is_act(node):
            continue
        existing = node.get("estleg:temporalStatus")
        if existing in (None, "unknown"):
            if existing != status:
                node["estleg:temporalStatus"] = status
                changed += 1
    return changed, doc


def process_law(law: dict, evaluation_date: str, dry_run: bool) -> dict:
    name = law["name"]
    status = derive_act_status(name, evaluation_date)
    summary = {"law": name, "status": status, "acts_changed": 0}
    if status is None:
        return summary
    for fname in law["files"]:
        path = KRR_DIR / fname
        if not path.exists():
            continue
        changed, doc = apply_to_file(path, status)
        summary["acts_changed"] += changed
        if changed and not dry_run:
            save_json(path, doc)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--law", action="append", default=None,
        help="INDEX law name to process (repeatable). Default: VÕS flagship.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Process every indexed law with a version sidecar (corpus-wide).",
    )
    parser.add_argument(
        "--evaluation-date", default=BUILD_EVALUATION_DATE,
        help=f"As-of date for in-force computation (default pinned: {BUILD_EVALUATION_DATE}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    args = parser.parse_args(argv)

    index = json.loads((KRR_DIR / "INDEX.json").read_text(encoding="utf-8"))
    laws = index["laws"]
    if args.all:
        targets = laws
    else:
        wanted = set(args.law) if args.law else set(DEFAULT_FLAGSHIP_LAWS)
        targets = [law for law in laws if law["name"] in wanted]
        missing = wanted - {law["name"] for law in targets}
        if missing:
            print(f"ERROR: law(s) not in INDEX: {sorted(missing)}")
            return 1

    print("=" * 70)
    print("Derive act-level temporalStatus from version sidecars (#617, #128)")
    print(f"  As-of date: {args.evaluation_date}{'  [DRY RUN]' if args.dry_run else ''}")
    print(f"  Laws: {len(targets)}")
    print("=" * 70)

    total_acts = 0
    by_status: Counter = Counter()
    laws_changed = 0
    for law in targets:
        summary = process_law(law, args.evaluation_date, args.dry_run)
        if summary["acts_changed"]:
            laws_changed += 1
            by_status[summary["status"]] += 1
            if not args.all:
                verb = "would set" if args.dry_run else "set"
                print(f"  {summary['law']}: {verb} {summary['acts_changed']} act node(s) -> {summary['status']}")
        total_acts += summary["acts_changed"]

    print(f"\n  Laws {'would change' if args.dry_run else 'changed'}: {laws_changed}")
    print(f"  Act nodes {'would change' if args.dry_run else 'changed'}: {total_acts}")
    print(f"  Derived status distribution (laws): {dict(sorted(by_status.items()))}")
    if not args.dry_run and total_acts:
        print("\n  NOTE: rebuild combined_ontology.jsonld so the load surface carries "
              "the new temporalStatus (fix_all_issues.generate_combined_jsonld).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
