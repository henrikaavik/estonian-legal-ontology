#!/usr/bin/env python3
"""Derive act-level ``estleg:temporalStatus`` from version sidecars (#617, #682).

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
node(s).

Status semantics match ``extract_temporal_data.determine_temporal_status``
(``inForce`` / ``repealed`` / ``notYetEffective`` / ``unknown``).

**#682 — honest unknowns.** An earlier residual pass stamped ``inForce`` on every
indexed act that had no sidecar evidence, and a deprecated act copied the status
of its ``dcterms:isReplacedBy`` successor. Together those made all 1,146 act
roots claim ``inForce``, which is an assertion the corpus cannot support:
``determine_temporal_status`` deliberately refuses to default to ``inForce``
without dates, and the residual pass undid that honesty. Now an act with no
sidecar evidence and no act-level dates settles at ``unknown``, and a deprecated
(retired-IRI) act root always carries ``unknown`` — a retired identifier is a
legal-succession artefact, not a repeal, so neither ``inForce`` nor ``repealed``
may be inferred from it.

This is a **surgical, offline, deterministic** post-process (no network). It is
NOT in the legal-review-gated paths (sanctions / deontic / court / transposition);
it is a mechanical derivation from authoritative redaction dates. Defaults to the
VÕS flagship; ``--all`` applies corpus-wide (a larger change — review the eval
delta + closure gate).

Idempotent; ``--dry-run`` reports without writing; atomic writes via ``save_json``.
``--recompute`` re-derives every targeted act root instead of only filling
``unknown``/missing, so a previously stamped residual ``inForce`` is corrected.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from estleg.estleg_common import BUILD_EVALUATION_DATE, KRR_DIR, save_json

# Default demonstration scope: Võlaõigusseadus (the obligations code — currently
# ``unknown`` despite being plainly in force, and the act behind the #578/#631
# harmonisation work).
DEFAULT_FLAGSHIP_LAWS = ("volaoigusseadus",)

VERSION_DIR = KRR_DIR / "provision_versions"

STATUS_PROPERTY = "estleg:temporalStatus"
UNKNOWN = "unknown"

# Canonical print order for status distributions.
STATUS_ORDER = ("inForce", "notYetEffective", "repealed", UNKNOWN)

# Act-level date triples written by ``extract_temporal_data``. When a node
# carries one of these its status was determined from dates, and #682's
# re-derivation must not clobber it.
ACT_DATE_PROPERTIES = ("estleg:repealDate", "estleg:entryIntoForce")


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


def _types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    if isinstance(raw, str):
        return [raw]
    return [item for item in raw if isinstance(item, str)]


def _is_act(node: dict) -> bool:
    """Act/Law work node — not a multipart ``Part`` (#128 / #445)."""
    types = _types(node)
    if "estleg:Part" in types and "estleg:Act" not in types:
        return False
    return "estleg:Act" in types or "estleg:Law" in types


def _is_deprecated(node: dict) -> bool:
    return node.get("owl:deprecated") is True


def _has_act_dates(node: dict) -> bool:
    """True when the node carries its own entry-into-force / repeal date.

    Such a status came from ``extract_temporal_data.determine_temporal_status``
    (authoritative act-level XML metadata), so re-derivation leaves it alone.
    """
    return any(node.get(prop) is not None for prop in ACT_DATE_PROPERTIES)


def residual_status(*, law: dict | None, node: dict) -> str | None:
    """Classify an Act that has no version-sidecar status (#409, #682).

    Always ``None``: an indexed act with no sidecar evidence and no act-level
    dates stays ``unknown``. This used to return ``inForce`` for any live INDEX
    law on the theory that a published Riigi Teataja consolidation is current,
    which silently asserted in-force status for ~385 acts on no evidence at all
    and contradicted ``determine_temporal_status``'s refusal to default. The
    seam is kept (with its inputs) so a *real* residual rule — one backed by RT
    ``kehtivus`` or an act-level date — can be reintroduced here.
    """
    return None


def desired_status(node: dict, sidecar_status: str | None, law: dict | None = None) -> str:
    """The temporalStatus an Act root should carry, on the evidence available.

    Deprecated (retired-IRI) act roots are ``unknown``: a superseded identifier
    says nothing about whether the statute behind it is in force (#682/#426).
    """
    if _is_deprecated(node):
        return UNKNOWN
    if sidecar_status:
        return sidecar_status
    return residual_status(law=law, node=node) or UNKNOWN


def _set_status(node: dict, status: str, recompute: bool) -> bool:
    """Write ``status`` onto one Act node. Returns True when it changed.

    Fill-only (default): an already-determined value is preserved. Under
    ``recompute``: only a *date-determined* value is preserved, so residual
    stamps from before #682 are corrected.
    """
    existing = node.get(STATUS_PROPERTY)
    determined = existing not in (None, UNKNOWN)
    if determined and (not recompute or _has_act_dates(node)):
        return False
    if existing == status:
        return False
    node[STATUS_PROPERTY] = status
    return True


def act_status_counts(doc: dict) -> Counter:
    """Status distribution over the Act roots of one loaded peep document."""
    counts: Counter = Counter()
    for node in doc.get("@graph") or []:
        if isinstance(node, dict) and _is_act(node):
            counts[node.get(STATUS_PROPERTY) or "missing"] += 1
    return counts


def format_distribution(counts: Counter) -> str:
    """Render a status Counter with the canonical statuses always present."""
    ordered = {status: counts.get(status, 0) for status in STATUS_ORDER}
    ordered.update({k: v for k, v in sorted(counts.items()) if k not in ordered})
    return str(ordered)


def apply_to_file(
    path: Path, status: str | None, recompute: bool = False, law: dict | None = None
) -> tuple[int, dict]:
    """Stamp the derived status onto the Act nodes of one peep file.

    ``status`` is the law's sidecar-derived status, or ``None`` when the sidecar
    yields nothing — in which case the act settles at ``unknown`` rather than a
    residual ``inForce`` (#682). Returns (acts_changed, doc); the document is
    mutated in memory either way, so dry runs can still report the outcome.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for node in doc.get("@graph") or []:
        if not isinstance(node, dict) or not _is_act(node):
            continue
        if _set_status(node, desired_status(node, status, law), recompute):
            changed += 1
    return changed, doc


def process_deprecated_status(
    krr_dir: Path, dry_run: bool, recompute: bool = False
) -> dict:
    """Set ``unknown`` on deprecated Act roots corpus-wide (#682).

    Replaces the pre-#682 pass that copied the ``dcterms:isReplacedBy``
    successor's status onto the retired IRI. A retired IRI is legal succession,
    not repeal: copying ``inForce`` asserted the retired act is in force, and
    stamping ``repealed`` would assert e.g. that the Constitution was repealed
    because ``estleg:PS_Map`` was superseded by
    ``estleg:eesti_vabariigi_pohiseadus_Map``. Neither is knowable here.
    """
    result = {"files_changed": 0, "acts_changed": 0, "acts": 0, "statuses": Counter()}
    for path in sorted(krr_dir.glob("*_peep.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        changed = 0
        deprecated = 0
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict) or not _is_act(node) or not _is_deprecated(node):
                continue
            deprecated += 1
            if _set_status(node, UNKNOWN, recompute):
                changed += 1
            result["statuses"][node.get(STATUS_PROPERTY) or "missing"] += 1
        result["acts"] += deprecated
        if changed:
            result["files_changed"] += 1
            result["acts_changed"] += changed
            if not dry_run:
                save_json(path, doc)
    return result


def process_law(law: dict, evaluation_date: str, dry_run: bool, recompute: bool = False) -> dict:
    name = law["name"]
    status = derive_act_status(name, evaluation_date)
    summary = {
        "law": name,
        "status": status or UNKNOWN,
        "acts_changed": 0,
        "source": "sidecar" if status else "no-evidence",
        "statuses": Counter(),
    }
    for fname in law["files"]:
        path = KRR_DIR / fname
        if not path.exists():
            continue
        try:
            changed, doc = apply_to_file(path, status, recompute, law)
        except (OSError, json.JSONDecodeError):
            continue
        summary["acts_changed"] += changed
        summary["statuses"] += act_status_counts(doc)
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
    parser.add_argument(
        "--recompute", action="store_true",
        help="Re-derive every targeted act root (#682) instead of only filling "
             "unknown/missing; preserves only date-determined statuses.",
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

    mode = "recompute" if args.recompute else "fill-only"
    print("=" * 70)
    print("Derive act-level temporalStatus from version sidecars (#617, #128, #682)")
    print(f"  As-of date: {args.evaluation_date}{'  [DRY RUN]' if args.dry_run else ''}")
    print(f"  Laws: {len(targets)}  Mode: {mode}")
    print("=" * 70)

    total_acts = 0
    act_statuses: Counter = Counter()
    by_source: Counter = Counter()
    laws_changed = 0
    for law in targets:
        summary = process_law(law, args.evaluation_date, args.dry_run, args.recompute)
        act_statuses += summary["statuses"]
        by_source[summary["source"]] += 1
        if summary["acts_changed"]:
            laws_changed += 1
            if not args.all:
                verb = "would set" if args.dry_run else "set"
                print(f"  {summary['law']}: {verb} {summary['acts_changed']} act node(s) -> {summary['status']}")
        total_acts += summary["acts_changed"]

    deprecated = process_deprecated_status(KRR_DIR, args.dry_run, args.recompute)
    print(f"\n  Laws {'would change' if args.dry_run else 'changed'}: {laws_changed}")
    print(f"  Act nodes {'would change' if args.dry_run else 'changed'}: {total_acts}")
    print(f"  Act root status distribution (INDEX laws): {format_distribution(act_statuses)}")
    print(f"  Evidence per law: {dict(sorted(by_source.items()))}")
    print(
        f"  Deprecated act roots (corpus-wide): {deprecated['acts']} — "
        f"{deprecated['files_changed']} file(s) / {deprecated['acts_changed']} node(s) "
        f"{'would change' if args.dry_run else 'changed'}"
    )
    print(f"  Deprecated status distribution: {format_distribution(deprecated['statuses'])}")
    stale_deprecated = deprecated["statuses"].total() - deprecated["statuses"][UNKNOWN]
    if stale_deprecated and not args.recompute:
        print(
            f"  NOTE: {stale_deprecated} deprecated act root(s) still carry a "
            "non-unknown status; rerun with --recompute to correct them (#682)."
        )
    if not args.dry_run and (total_acts or deprecated["acts_changed"]):
        print("\n  NOTE: rebuild combined_ontology.jsonld so the load surface carries "
              "the new temporalStatus (fix_all_issues.generate_combined_jsonld).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
