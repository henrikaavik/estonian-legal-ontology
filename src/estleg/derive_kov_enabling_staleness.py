#!/usr/bin/env python3
"""Stamp KOV enabling-provision staleness from version sidecars (#712).

A municipal regulation (``estleg:MunicipalRegulation``) is issued under an
enabling provision of a national law ("Määrus kehtestatakse KOKS § 22 lg 1
p 5 alusel"). The KOV layer already resolves that preamble into
``estleg:implementsCitation`` → ``estleg:Citation`` → ``estleg:citationTarget``
(a provision IRI such as ``estleg:KOKS_Par_22_Lg_1``), and the per-provision
redaction chains under ``krr_outputs/provision_versions/*.jsonld`` record when
each § was rewritten. This module joins the two so the supervision question
"which municipal acts rest on an enabling provision that has changed since the
act entered into force?" is answerable from the KOV peeps alone.

For every KOV root ``R`` with entry-into-force date ``e`` (the as-of date) it
walks each cited provision ``P``:

  1. ``P`` is reduced to the provision the version layer keys on. Version
     chains are paragraph-level (``<ABBR>_Par_<n>``); a lõige-level target
     (``KOKS_Par_22_Lg_1``) is rolled up to its paragraph. Act-level / part
     level fallbacks (``KOKS_Map``, ``..._Osa2``) cannot be resolved to a
     version and are counted as such.
  2. The version in force at ``e`` is the row with the greatest
     ``versionValidFrom <= e`` whose ``versionValidTo`` (if set) is ``>= e``.
     A version that starts ON ``e`` is the one in force. If no version is in
     force at ``e`` — ``e`` predates the earliest known redaction — the
     provision is "predates history" and is NOT used for the flag: the text in
     force at ``e`` is unknown, so claiming either state would be a guess.
  3. The provision was superseded since iff a later row starts after ``e``
     (``versionValidFrom > e``) AND its ``versionText`` differs from the text
     in force at ``e``. ~0.7% of chain transitions corpus-wide repeat the
     previous text verbatim (a redaction of the act that left this § alone);
     those are not a change of the enabling provision and are skipped.

and stamps on ``R``, but only when at least one cited provision resolved to a
version in force at ``e``:

  * ``estleg:enablingProvisionOutdated`` — ``xsd:boolean``; ``true`` iff ANY
    resolved enabling provision has a redaction starting after ``e``.
  * ``estleg:earliestSupersedingDate`` — ``xsd:date``; only when outdated: the
    earliest such ``versionValidFrom`` across the resolved provisions. (The
    same property the court-staleness module uses, #618.)

No ``ProvisionVersion`` edge is written onto the peep: version nodes live in the
separate ``provision_versions/`` load surface and an edge to them would dangle
in the combined graph unless it were registered in
``estleg_common.COMBINED_STRIPPED_PREDICATES``. The tabular export
(``serialize_tabular --kov-legality``) carries the in-force version IRI per
citation instead.

Caveat (documented in the coverage report): because the chains are
paragraph-level, a lõige-level citation counts as outdated when ANY part of its
paragraph was rewritten, not only the cited lõige.

``--as-of DATE`` evaluates the view as it stood on ``DATE``: redactions starting
after ``DATE`` are ignored, and roots not yet in force on ``DATE`` are left
unstamped. Without it every known redaction counts (deterministic output).

Idempotent strip-then-recompute: each run first deletes the two derived props on
every KOV root, then recomputes them, so re-running is a no-op diff and a root
that no longer resolves is cleaned up. Offline and deterministic. Dry-run is the
DEFAULT; pass ``--apply`` to write (atomic ``save_json``). ``--dry-run`` is an
explicit no-op that overrides ``--apply``:

    python3 scripts/derive_kov_enabling_staleness.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from estleg.derive_court_interpretation_staleness import (
    VERSION_DIR,
    VersionRow,
    _date_value,
    _load_jsonld,
    _types,
)
from estleg.estleg_common import KRR_DIR, save_json
from estleg.kov_pipeline_coverage import resolve_pipeline_version

KOV_DIR = KRR_DIR / "regulations" / "kov"
REPORT_PATH = KRR_DIR / "reports" / "kov" / "derive_kov_enabling_staleness_coverage.json"

KOV_ROOT_TYPE = "estleg:MunicipalRegulation"
PROP_OUTDATED = "estleg:enablingProvisionOutdated"
PROP_SUPERSEDING = "estleg:earliestSupersedingDate"
OUTPUT_PROPS = (PROP_OUTDATED, PROP_SUPERSEDING)

# Per-citation resolution outcomes.
STATUS_RESOLVED = "resolved"              # version in force at the as-of found
STATUS_ACT_LEVEL = "act_level"            # target is an act/part (_Map/_Osa), no § to version
STATUS_NO_VERSIONS = "no_versions"        # § target, but the version layer has no chain for it
STATUS_PREDATES = "predates_history"      # as-of earlier than the first known redaction
STATUS_NO_TARGET = "no_target"            # citation node missing or without a target IRI

_SUBPARAGRAPH_RE = re.compile(r"_Lg_.*$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class VersionLayer:
    """Version chains keyed by provision, plus a text digest per version IRI.

    ``chains`` has the exact shape of
    ``derive_court_interpretation_staleness.build_version_index`` (sorted
    ``(validFrom, validTo, versionIRI)`` rows); ``text_digest`` lets the
    superseding test ignore a later redaction whose text is unchanged.
    """

    chains: dict[str, list[VersionRow]] = field(default_factory=dict)
    text_digest: dict[str, str] = field(default_factory=dict)

    def __contains__(self, provision: str) -> bool:
        return provision in self.chains

    def __len__(self) -> int:
        return len(self.chains)


def build_version_layer(version_dir: Path = VERSION_DIR) -> VersionLayer:
    """Index every version, refusing incomplete inputs before changing KOV flags."""
    layer = VersionLayer()
    paths = sorted(version_dir.glob("*.jsonld"))
    if not paths:
        raise ValueError(f"No provision-version inputs in {version_dir}")
    for path in paths:
        doc = _load_jsonld(path)
        if doc is None:
            raise ValueError(f"Unreadable provision-version input: {path}")
        if not isinstance(doc, dict) or not isinstance(doc.get("@graph"), list):
            raise ValueError(f"Invalid provision-version graph: {path}")
        for node in doc.get("@graph", []):
            if not isinstance(node, dict) or "estleg:ProvisionVersion" not in _types(node):
                continue
            target = _ref_id(node.get("estleg:versionOf"))
            valid_from = _date_value(node.get("estleg:versionValidFrom"))
            version_iri = node.get("@id")
            if not target or not valid_from or not isinstance(version_iri, str):
                continue
            valid_to = _date_value(node.get("estleg:versionValidTo"))
            layer.chains.setdefault(target, []).append((valid_from, valid_to, version_iri))
            text = node.get("estleg:versionText")
            if isinstance(text, str):
                layer.text_digest[version_iri] = hashlib.sha1(
                    text.encode("utf-8")
                ).hexdigest()
    for rows in layer.chains.values():
        rows.sort()
    return layer


@dataclass(frozen=True)
class ProvisionEvaluation:
    """How one cited enabling provision resolved against the version layer."""

    target: str                    # citationTarget IRI as written
    provision: str                 # version-layer key (paragraph IRI) or ""
    status: str
    version_in_force: str = ""     # ProvisionVersion IRI in force at the as-of
    superseding_date: str = ""     # first versionValidFrom after the as-of, if any

    @property
    def outdated(self) -> bool:
        return self.status == STATUS_RESOLVED and bool(self.superseding_date)


@dataclass
class RootEvaluation:
    """All enabling-provision evaluations for one KOV root."""

    as_of: str | None
    provisions: list[ProvisionEvaluation] = field(default_factory=list)

    @property
    def resolved(self) -> list[ProvisionEvaluation]:
        return [p for p in self.provisions if p.status == STATUS_RESOLVED]

    @property
    def outdated(self) -> bool:
        return any(p.outdated for p in self.resolved)

    @property
    def earliest_superseding_date(self) -> str | None:
        dates = [p.superseding_date for p in self.resolved if p.superseding_date]
        return min(dates) if dates else None


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ref_id(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("@id")
    return value if isinstance(value, str) and value else None


def is_kov_root(node: object) -> bool:
    return isinstance(node, dict) and KOV_ROOT_TYPE in _types(node)


def version_key(target: str, index: VersionLayer) -> str | None:
    """Map a citation target onto the key the version index uses.

    Exact match first (future-proof for sub-paragraph chains), then the
    paragraph roll-up (``X_Par_22_Lg_1`` → ``X_Par_22``). Returns ``None``
    when neither has a chain.
    """
    if target in index:
        return target
    paragraph = _SUBPARAGRAPH_RE.sub("", target)
    if paragraph != target and paragraph in index:
        return paragraph
    return None


def evaluate_provision(
    target: str,
    as_of: str,
    index: VersionLayer,
    eval_date: str | None = None,
) -> ProvisionEvaluation:
    """Resolve one citation target against the version chains at ``as_of``."""
    if "_Par_" not in target:
        return ProvisionEvaluation(target=target, provision="", status=STATUS_ACT_LEVEL)
    key = version_key(target, index)
    if key is None:
        return ProvisionEvaluation(
            target=target,
            provision=_SUBPARAGRAPH_RE.sub("", target),
            status=STATUS_NO_VERSIONS,
        )
    rows = [
        row for row in index.chains[key]
        if eval_date is None or row[0] <= eval_date  # not yet in force on eval date
    ]
    in_force: str | None = None
    for valid_from, valid_to, version_iri in rows:
        if valid_from <= as_of and (valid_to is None or valid_to >= as_of):
            in_force = version_iri
    if in_force is None:
        return ProvisionEvaluation(target=target, provision=key, status=STATUS_PREDATES)
    in_force_text = index.text_digest.get(in_force)
    superseding = ""
    for valid_from, _valid_to, version_iri in rows:  # validFrom-sorted
        if valid_from <= as_of:
            continue
        text = index.text_digest.get(version_iri)
        if in_force_text is not None and text == in_force_text:
            continue  # same wording re-issued: not a change of the provision
        superseding = valid_from
        break
    # A repeal without a successor also removes the enabling basis. Consider
    # only cessation dates reached by the requested evaluation date.
    for _valid_from, valid_to, _version_iri in rows:
        if not valid_to or valid_to < as_of or valid_to >= "9999-12-31":
            continue
        ceased_on = (date.fromisoformat(valid_to) + timedelta(days=1)).isoformat()
        successor = any(start <= ceased_on and (end is None or end >= ceased_on)
                        for start, end, _ in rows)
        if not successor and (eval_date is None or ceased_on <= eval_date):
            superseding = min(superseding, ceased_on) if superseding else ceased_on
    return ProvisionEvaluation(
        target=target,
        provision=key,
        status=STATUS_RESOLVED,
        version_in_force=in_force,
        superseding_date=superseding,
    )


def cited_targets(root: dict, by_id: dict[str, dict]) -> list[str | None]:
    """Citation target IRIs of ``root`` (``None`` for an unresolvable citation).

    Order follows ``implementsCitation``; duplicates are kept out.
    """
    out: list[str | None] = []
    seen: set[str] = set()
    for ref in _as_list(root.get("estleg:implementsCitation")):
        citation = by_id.get(_ref_id(ref) or "")
        targets = [
            _ref_id(t) for t in _as_list((citation or {}).get("estleg:citationTarget"))
        ]
        targets = [t for t in targets if t]
        if not targets:
            out.append(None)
            continue
        for target in targets:
            if target not in seen:
                seen.add(target)
                out.append(target)
    return out


def evaluate_root(
    root: dict,
    by_id: dict[str, dict],
    index: VersionLayer,
    eval_date: str | None = None,
) -> RootEvaluation:
    """Evaluate every enabling provision of a KOV root (pure; no mutation)."""
    as_of = _date_value(root.get("estleg:entryIntoForce"))
    if not as_of or not _ISO_DATE_RE.match(as_of):
        return RootEvaluation(as_of=None)
    if eval_date is not None and as_of > eval_date:
        return RootEvaluation(as_of=None)  # not yet in force on the evaluation date
    result = RootEvaluation(as_of=as_of)
    for target in cited_targets(root, by_id):
        if target is None:
            result.provisions.append(
                ProvisionEvaluation(target="", provision="", status=STATUS_NO_TARGET)
            )
        else:
            result.provisions.append(evaluate_provision(target, as_of, index, eval_date))
    return result


def derive_node(
    root: dict,
    by_id: dict[str, dict],
    index: VersionLayer,
    eval_date: str | None = None,
) -> tuple[bool, RootEvaluation]:
    """Strip-then-recompute the two derived props on ``root``, in place.

    Returns ``(changed, evaluation)``.
    """
    before = {k: root[k] for k in OUTPUT_PROPS if k in root}
    for k in OUTPUT_PROPS:
        root.pop(k, None)
    evaluation = evaluate_root(root, by_id, index, eval_date)
    if evaluation.resolved:
        root[PROP_OUTDATED] = {"@value": evaluation.outdated, "@type": "xsd:boolean"}
        earliest = evaluation.earliest_superseding_date
        if evaluation.outdated and earliest:
            root[PROP_SUPERSEDING] = {"@value": earliest, "@type": "xsd:date"}
    after = {k: root[k] for k in OUTPUT_PROPS if k in root}
    return before != after, evaluation


def graph_index(doc: dict) -> dict[str, dict]:
    return {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }


def process_file(
    path: Path,
    index: VersionLayer,
    apply: bool,
    stats: Counter,
    eval_date: str | None = None,
) -> None:
    """(Re)derive staleness on one KOV peep; write atomically iff changed."""
    doc = _load_jsonld(path)
    if doc is None:
        stats["files_unreadable"] += 1
        return
    by_id = graph_index(doc)
    file_changed = False
    for node in doc.get("@graph", []):
        if not is_kov_root(node):
            continue
        stats["roots_evaluated"] += 1
        changed, evaluation = derive_node(node, by_id, index, eval_date)
        _tally(stats, node, evaluation)
        if changed:
            stats["roots_changed"] += 1
            file_changed = True
    if file_changed:
        stats["files_changed"] += 1
        if apply:
            save_json(path, doc)


def _tally(stats: Counter, root: dict, evaluation: RootEvaluation) -> None:
    if evaluation.as_of is None:
        stats["roots_without_as_of"] += 1
        return
    if evaluation.provisions:
        stats["roots_with_citations"] += 1
    for p in evaluation.provisions:
        stats["citations"] += 1
        stats[f"citation_status:{p.status}"] += 1
        if p.status == STATUS_RESOLVED:
            stats[f"provision_resolved:{p.provision}"] += 1
            if p.outdated:
                stats[f"provision_outdated:{p.provision}"] += 1
    if evaluation.resolved:
        stats["roots_resolved"] += 1
        if evaluation.outdated:
            stats["roots_outdated"] += 1
            earliest = evaluation.earliest_superseding_date or ""
            stats[f"superseding_year:{earliest[:4]}"] += 1
        else:
            stats["roots_current"] += 1
        status = root.get("estleg:municipalityStatus")
        if isinstance(status, str):
            stats[f"municipality_status:{status}:resolved"] += 1
            if evaluation.outdated:
                stats[f"municipality_status:{status}:outdated"] += 1


def iter_kov_peeps(kov_dir: Path) -> list[Path]:
    return sorted(
        p for p in kov_dir.glob("*/*_peep.json")
        if not p.name.startswith("REGULATIONS_KOV_INDEX")
    )


def build_report(stats: Counter, eval_date: str | None, top: int = 15) -> dict:
    """Deterministic coverage report (no timestamps → no churn on re-run)."""

    def prefixed(prefix: str) -> dict[str, int]:
        return {
            k[len(prefix):]: v for k, v in sorted(stats.items()) if k.startswith(prefix)
        }

    resolved_by_prov = prefixed("provision_resolved:")
    outdated_by_prov = prefixed("provision_outdated:")
    top_provisions = sorted(resolved_by_prov.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return {
        # #704 pipeline-version gate: every coverage report names the commit
        # that produced it (git short SHA; "unknown" outside a checkout).
        "pipeline_version": resolve_pipeline_version(),
        "pipeline": "derive_kov_enabling_staleness",
        "issue": "#712",
        "as_of_override": eval_date,
        "as_of_rule": "estleg:entryIntoForce of the KOV root",
        "version_granularity": (
            "paragraph-level (provision_versions chains); lõige-level citations "
            "are rolled up to their paragraph"
        ),
        "roots_evaluated": stats["roots_evaluated"],
        "roots_without_as_of": stats["roots_without_as_of"],
        "roots_with_citations": stats["roots_with_citations"],
        "roots_resolved_to_version": stats["roots_resolved"],
        "roots_outdated": stats["roots_outdated"],
        "roots_current": stats["roots_current"],
        "citations": stats["citations"],
        "citation_status": prefixed("citation_status:"),
        "earliest_superseding_year": prefixed("superseding_year:"),
        "by_municipality_status": prefixed("municipality_status:"),
        "top_enabling_provisions": [
            {"provision": prov, "resolved": n, "outdated": outdated_by_prov.get(prov, 0)}
            for prov, n in top_provisions
        ],
    }


def _parse_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write changed KOV peeps and the coverage report (default: dry-run).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report without writing (the default; overrides --apply).",
    )
    parser.add_argument(
        "--as-of", type=_parse_date, default=None, metavar="YYYY-MM-DD",
        help="Evaluate the view as it stood on this date: ignore redactions that "
             "start later and roots not yet in force. Default: all known versions.",
    )
    parser.add_argument(
        "--kov-dir", type=Path, default=KOV_DIR, help="KOV peep root (for tests).",
    )
    parser.add_argument(
        "--report", type=Path, default=REPORT_PATH,
        help="Coverage report path (written only with --apply).",
    )
    args = parser.parse_args(argv)
    apply = args.apply and not args.dry_run

    index = build_version_layer()
    peeps = iter_kov_peeps(args.kov_dir)
    stats: Counter = Counter()
    for path in peeps:
        process_file(path, index, apply, stats, args.as_of)
    report = build_report(stats, args.as_of)

    print("=" * 70)
    print("Derive KOV enabling-provision staleness from version sidecars (#712)")
    print(f"  Provisions with version data: {len(index):,}")
    print(f"  KOV peeps scanned: {len(peeps):,}")
    print(f"  As-of override: {args.as_of or '(none — entryIntoForce, all versions)'}")
    print(f"  Mode: {'APPLY' if apply else 'DRY RUN (no writes)'}")
    print("=" * 70)
    for key in (
        "roots_evaluated", "roots_with_citations", "roots_resolved_to_version",
        "roots_outdated", "roots_current", "citations",
    ):
        print(f"  {key:<28} {report[key]:>8,}")
    for status, n in report["citation_status"].items():
        print(f"    citations {status:<20} {n:>8,}")
    verb = "changed" if apply else "would change"
    print(f"  Roots {verb}: {stats['roots_changed']:,}  Peeps {verb}: {stats['files_changed']:,}")
    if apply:
        save_json(args.report, report)
        print(f"  Wrote {args.report}")
    elif stats["roots_changed"]:
        print("\n  NOTE: dry run — re-run with --apply to write these stamps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
