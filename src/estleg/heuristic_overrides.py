#!/usr/bin/env python3
"""Durable human overrides for the heuristic classifiers (#700).

The keyword classifiers (deontic, target group, institutional competence,
EuroVoc) clear their own layer before rewriting it, so a reviewed correction
written straight into a peep is lost on the next regeneration. This module is
the shared, git-tracked override store they all consult instead:
``data/heuristic_overrides.jsonl``. See ``docs/HEURISTIC_OVERRIDES.md``.

One JSON object per line::

    {"node": "estleg:KarS_Par_121_Lg1", "predicate": "estleg:normativeType",
     "value": {"@id": "estleg:NormType_Prohibition"},
     "reviewer": "Justiitsministeerium", "date": "2026-10-01",
     "basis": "RT I, 10.03.2023, 12; § 121 lg 1", "action": "set"}

``action`` is ``set`` (the node carries exactly ``value`` for the predicate)
or ``remove`` (the node carries no value for the predicate; ``value`` must be
omitted or ``null``). Blank lines are ignored; nothing else is.

Classifier contract (implemented by each classifier with the helpers below):

* the "clear before rewrite" step skips every (node, predicate) the store owns
  (:func:`clear_unowned`);
* the heuristic result is computed as usual, then the override is applied
  LAST (:func:`apply_node_overrides`) so it always wins;
* the node gets ``prov:wasAttributedTo`` (a plain string literal naming the
  reviewer role/organisation; a sorted list when several reviewers own
  predicates on the node) and ``estleg:assertionConfidence`` is restamped
  override-aware (:func:`restamp_confidence`): an owned predicate counts as
  1.0, so a node whose heuristic layers are all human-owned reads 1.0;
* the peep's ``@context`` gains the ``prov`` prefix (:func:`ensure_prov_context`).

``prov:wasAttributedTo`` on peep / overlay nodes is owned by this mechanism: a
node with no override has the property removed on the next classifier pass,
so deleting a store line cleanly retracts the attribution.
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from estleg.estleg_common import (
    DEONTIC_ASSERTION_CONFIDENCE,
    EUROVOC_ASSERTION_CONFIDENCE,
    REPO_ROOT,
    TARGET_GROUP_ASSERTION_CONFIDENCE,
    heuristic_confidence_for_node,
    stamp_assertion_confidence,
)

OVERRIDES_PATH = REPO_ROOT / "data" / "heuristic_overrides.jsonl"

PROV_PREFIX = "prov"
PROV_IRI = "http://www.w3.org/ns/prov#"
ATTRIBUTION_PREDICATE = "prov:wasAttributedTo"
CONFIDENCE_PREDICATE = "estleg:assertionConfidence"
# Human-reviewed assertions are certain by construction (#700).
HUMAN_ASSERTION_CONFIDENCE = "1.0"

EUROVOC_URI_BASE = "http://eurovoc.europa.eu/"

# Predicates each classifier owns. An override names exactly one of these.
DEONTIC_PREDICATES: tuple[str, ...] = ("estleg:normativeType", "estleg:dutyHolder")
TARGET_GROUP_PREDICATES: tuple[str, ...] = ("estleg:targetGroup",)
COMPETENCE_PREDICATES: tuple[str, ...] = (
    "estleg:competentAuthority",
    "estleg:competenceType",
)
EUROVOC_PREDICATES: tuple[str, ...] = ("dcterms:subject",)

CLASSIFIER_PREDICATES: dict[str, tuple[str, ...]] = {
    "deontic": DEONTIC_PREDICATES,
    "target_group": TARGET_GROUP_PREDICATES,
    "competence": COMPETENCE_PREDICATES,
    "eurovoc": EUROVOC_PREDICATES,
}
KNOWN_PREDICATES: frozenset[str] = frozenset(
    p for preds in CLASSIFIER_PREDICATES.values() for p in preds
)

# Node-level confidence layers (mirrors estleg_common.heuristic_confidence_for_node).
_CONFIDENCE_LAYERS: tuple[tuple[str, str], ...] = (
    ("estleg:normativeType", DEONTIC_ASSERTION_CONFIDENCE),
    ("estleg:targetGroup", TARGET_GROUP_ASSERTION_CONFIDENCE),
    ("dcterms:subject", EUROVOC_ASSERTION_CONFIDENCE),
)

NORM_TYPE_IRIS: frozenset[str] = frozenset(
    f"estleg:NormType_{name}"
    for name in ("Obligation", "Right", "Permission", "Prohibition", "Definition")
)
TARGET_GROUP_IRIS: frozenset[str] = frozenset(
    f"estleg:TargetGroup_{name}"
    for name in ("Citizen", "Business", "PublicBody", "Official", "NGO")
)
# Mirrors the sh:in list on estleg:competenceType in shacl/estonian_legal_shapes.ttl.
COMPETENCE_TYPES: frozenset[str] = frozenset(
    {"supervision", "licensing", "enforcement", "regulation", "advisory", "general"}
)

RECORD_KEYS: frozenset[str] = frozenset(
    {"node", "predicate", "value", "reviewer", "date", "basis", "action"}
)
REQUIRED_KEYS: frozenset[str] = frozenset(
    {"node", "predicate", "reviewer", "date", "basis", "action"}
)
ACTIONS: frozenset[str] = frozenset({"set", "remove"})

# A compact estleg: IRI: no whitespace or characters JSON-LD/Turtle cannot carry.
_ESTLEG_IRI_RE = re.compile(r'^estleg:[^\s<>"{}|\\^`]+$')
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Personal-data guard: a reviewer is a role or organisation, never an address.
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


class OverrideError(ValueError):
    """A malformed override store. The message names the offending line."""


@dataclass(frozen=True)
class Override:
    """One validated override line."""

    node: str
    predicate: str
    value: object
    reviewer: str
    date: str
    basis: str
    action: str
    line: int = 0

    def as_record(self) -> dict:
        record = {
            "node": self.node,
            "predicate": self.predicate,
            "reviewer": self.reviewer,
            "date": self.date,
            "basis": self.basis,
            "action": self.action,
        }
        if self.action == "set":
            record["value"] = self.value
        return record


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _iri_ref(value: object) -> str | None:
    if isinstance(value, dict) and set(value) == {"@id"} and isinstance(value["@id"], str):
        return value["@id"]
    return None


def _check_ref_list(value: object, allowed: Iterable[str] | None, what: str) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"value must be a non-empty list of {{\"@id\": ...}} {what} refs")
    seen: set[str] = set()
    for item in value:
        iri = _iri_ref(item)
        if iri is None:
            raise ValueError(f"value entries must be {{\"@id\": ...}} refs, got {item!r}")
        if allowed is not None and iri not in allowed:
            raise ValueError(f"{iri!r} is not a known {what} IRI")
        if iri in seen:
            raise ValueError(f"duplicate {iri!r} in value")
        seen.add(iri)


def _validate_value(predicate: str, value: object) -> None:
    """Check ``value`` has the peep's own form for ``predicate``."""
    if predicate == "estleg:normativeType":
        iri = _iri_ref(value)
        if iri is None or iri not in NORM_TYPE_IRIS:
            raise ValueError(
                "value must be one {\"@id\": \"estleg:NormType_*\"} ref "
                f"({', '.join(sorted(NORM_TYPE_IRIS))})"
            )
    elif predicate in ("estleg:targetGroup", "estleg:dutyHolder"):
        _check_ref_list(value, TARGET_GROUP_IRIS, "TargetGroup")
    elif predicate == "estleg:competentAuthority":
        _check_ref_list(value, None, "authority")
        for item in value:  # type: ignore[union-attr]
            if not _ESTLEG_IRI_RE.match(item["@id"]):
                raise ValueError(f"authority {item['@id']!r} is not an estleg: IRI")
    elif predicate == "estleg:competenceType":
        if value not in COMPETENCE_TYPES:
            raise ValueError(
                f"value must be one of {', '.join(sorted(COMPETENCE_TYPES))}"
            )
    elif predicate == "dcterms:subject":
        _check_ref_list(value, None, "EuroVoc")
        for item in value:  # type: ignore[union-attr]
            if not re.fullmatch(re.escape(EUROVOC_URI_BASE) + r"\d+", item["@id"]):
                raise ValueError(f"{item['@id']!r} is not a EuroVoc concept IRI")


def parse_override(record: object, line: int = 0) -> Override:
    """Validate one decoded record. Raises :class:`OverrideError`."""

    def fail(msg: str) -> OverrideError:
        return OverrideError(f"line {line}: {msg}")

    if not isinstance(record, dict):
        raise fail("record must be a JSON object")
    unknown = set(record) - RECORD_KEYS
    if unknown:
        raise fail(f"unknown key(s) {sorted(unknown)}")
    missing = REQUIRED_KEYS - set(record)
    if missing:
        raise fail(f"missing key(s) {sorted(missing)}")

    node = record["node"]
    if not isinstance(node, str) or not _ESTLEG_IRI_RE.match(node):
        raise fail(f"malformed node IRI {node!r} (expected compact estleg:<local>)")
    predicate = record["predicate"]
    if predicate not in KNOWN_PREDICATES:
        raise fail(
            f"unknown predicate {predicate!r}; overridable: {sorted(KNOWN_PREDICATES)}"
        )
    action = record["action"]
    if action not in ACTIONS:
        raise fail(f"action must be 'set' or 'remove', got {action!r}")

    value = record.get("value")
    if action == "set":
        if "value" not in record or value is None:
            raise fail("action 'set' requires a value")
        try:
            _validate_value(predicate, value)
        except ValueError as exc:
            raise fail(f"{predicate}: {exc}") from None
    elif value is not None:
        raise fail("action 'remove' must not carry a value")

    reviewer = record["reviewer"]
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise fail("reviewer must be a non-empty role or organisation name")
    if reviewer != reviewer.strip() or _EMAIL_RE.search(reviewer):
        raise fail("reviewer must be a trimmed role/organisation name, not an address")

    date = record["date"]
    if not isinstance(date, str) or not _DATE_RE.match(date):
        raise fail(f"date must be YYYY-MM-DD, got {date!r}")
    try:
        _dt.date.fromisoformat(date)
    except ValueError:
        raise fail(f"date {date!r} is not a calendar date") from None

    basis = record["basis"]
    if not isinstance(basis, str) or not basis.strip():
        raise fail("basis must be a non-empty RT citation or short reason")

    return Override(
        node=node,
        predicate=predicate,
        value=value if action == "set" else None,
        reviewer=reviewer,
        date=date,
        basis=basis,
        action=action,
        line=line,
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclass
class OverrideStore:
    """Validated overrides indexed by node IRI, then predicate."""

    by_node: dict[str, dict[str, Override]] = field(default_factory=dict)
    source: Path | None = None

    @classmethod
    def from_records(cls, records: Iterable[Override], source: Path | None = None) -> OverrideStore:
        store = cls(source=source)
        for rec in records:
            preds = store.by_node.setdefault(rec.node, {})
            if rec.predicate in preds:
                first = preds[rec.predicate].line
                raise OverrideError(
                    f"line {rec.line}: duplicate override for ({rec.node}, "
                    f"{rec.predicate}); first defined on line {first}"
                )
            preds[rec.predicate] = rec
        return store

    def __len__(self) -> int:
        return sum(len(preds) for preds in self.by_node.values())

    def __bool__(self) -> bool:
        return bool(self.by_node)

    def for_node(
        self, node_id: object, predicates: Iterable[str] | None = None
    ) -> dict[str, Override]:
        """Overrides on ``node_id`` (optionally limited to ``predicates``)."""
        if not isinstance(node_id, str):
            return {}
        owned = self.by_node.get(node_id)
        if not owned:
            return {}
        if predicates is None:
            return dict(owned)
        return {p: owned[p] for p in predicates if p in owned}

    def owns(self, node_id: object, predicate: str) -> bool:
        return isinstance(node_id, str) and predicate in self.by_node.get(node_id, {})

    def owned_predicates(self, node_id: object) -> set[str]:
        if not isinstance(node_id, str):
            return set()
        return set(self.by_node.get(node_id, {}))

    def reviewers(self, node_id: object) -> list[str]:
        """Sorted distinct reviewers across every override on the node."""
        return sorted({rec.reviewer for rec in self.for_node(node_id).values()})

    def records(self, predicates: Iterable[str] | None = None) -> list[Override]:
        """All overrides in store-file order, optionally filtered by predicate."""
        wanted = None if predicates is None else set(predicates)
        out = [
            rec
            for preds in self.by_node.values()
            for rec in preds.values()
            if wanted is None or rec.predicate in wanted
        ]
        return sorted(out, key=lambda r: r.line)


def parse_overrides_text(text: str, source: Path | None = None) -> OverrideStore:
    """Parse JSONL ``text`` into a validated store (hard error on any bad line)."""
    records: list[Override] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OverrideError(f"line {line_no}: invalid JSON: {exc.msg}") from None
        records.append(parse_override(decoded, line_no))
    return OverrideStore.from_records(records, source=source)


def load_overrides(path: Path | str | None = None) -> OverrideStore:
    """Load and validate the store; an explicitly requested file must exist."""
    src = Path(path) if path is not None else OVERRIDES_PATH
    if not src.exists():
        if path is not None:
            raise OverrideError(f"{src}: override store is missing")
        return OverrideStore(source=src)
    try:
        text = src.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise OverrideError(f"{src}: not UTF-8: {exc}") from None
    try:
        return parse_overrides_text(text, source=src)
    except OverrideError as exc:
        raise OverrideError(f"{src}: {exc}") from None


# ---------------------------------------------------------------------------
# Applying overrides to nodes
# ---------------------------------------------------------------------------


def ensure_prov_context(doc: dict) -> bool:
    """Ensure a dict ``@context`` maps ``prov``. Returns True if added.

    Same contract as ``deprecate_legacy_statutes._ensure_context_prefix``: a
    missing or non-dict context is left untouched.
    """
    ctx = doc.get("@context")
    if not isinstance(ctx, dict) or ctx.get(PROV_PREFIX) == PROV_IRI:
        return False
    ctx[PROV_PREFIX] = PROV_IRI
    return True


def clear_unowned(node: dict, predicates: Iterable[str], store: OverrideStore) -> bool:
    """Pop each of ``predicates`` from ``node`` unless an override owns it.

    The drop-in replacement for a classifier's "clear before rewrite" step.
    Returns True when the node changed.
    """
    node_id = node.get("@id")
    changed = False
    for pred in predicates:
        if pred in node and not store.owns(node_id, pred):
            del node[pred]
            changed = True
    return changed


def _is_eurovoc_ref(ref: object) -> bool:
    return isinstance(ref, dict) and str(ref.get("@id", "")).startswith(EUROVOC_URI_BASE)


def _as_list(value: object) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _apply_eurovoc_subject(node: dict, rec: Override) -> bool:
    """EuroVoc owns only the EuroVoc part of ``dcterms:subject``.

    Non-EuroVoc subjects are preserved (matching the classifier's clear step);
    ``eli:is_about`` mirrors the EuroVoc refs (#446).
    """
    before = (copy.deepcopy(node.get("dcterms:subject")), copy.deepcopy(node.get("eli:is_about")))
    kept = [ref for ref in _as_list(node.get("dcterms:subject")) if not _is_eurovoc_ref(ref)]
    eurovoc = copy.deepcopy(rec.value) if rec.action == "set" else []
    subjects = kept + eurovoc
    if subjects:
        node["dcterms:subject"] = subjects
    else:
        node.pop("dcterms:subject", None)
    about = [ref for ref in _as_list(node.get("eli:is_about")) if not _is_eurovoc_ref(ref)]
    about += copy.deepcopy(eurovoc)
    if about:
        node["eli:is_about"] = about
    else:
        node.pop("eli:is_about", None)
    return before != (node.get("dcterms:subject"), node.get("eli:is_about"))


def sync_attribution(node: dict, store: OverrideStore) -> bool:
    """Make ``prov:wasAttributedTo`` match the store's reviewers for the node."""
    reviewers = store.reviewers(node.get("@id"))
    if not reviewers:
        if ATTRIBUTION_PREDICATE in node:
            del node[ATTRIBUTION_PREDICATE]
            return True
        return False
    value: object = reviewers[0] if len(reviewers) == 1 else reviewers
    if node.get(ATTRIBUTION_PREDICATE) == value:
        return False
    node[ATTRIBUTION_PREDICATE] = value
    return True


def apply_node_overrides(
    node: dict, store: OverrideStore, predicates: Iterable[str]
) -> bool:
    """Apply the store's overrides for ``predicates`` to ``node`` (LAST step).

    Also syncs ``prov:wasAttributedTo`` (added for an overridden node, removed
    from a node that no longer has any override). Returns True on change.
    """
    node_id = node.get("@id")
    changed = False
    for pred, rec in store.for_node(node_id, predicates).items():
        if pred == "dcterms:subject":
            changed |= _apply_eurovoc_subject(node, rec)
        elif rec.action == "set":
            if node.get(pred) != rec.value:
                node[pred] = copy.deepcopy(rec.value)
                changed = True
        elif pred in node:
            del node[pred]
            changed = True
    changed |= sync_attribution(node, store)
    return changed


def confidence_for_node(node: dict, store: OverrideStore) -> str | None:
    """Override-aware node confidence.

    A node with no override gets exactly
    ``estleg_common.heuristic_confidence_for_node``. Otherwise each heuristic
    layer present scores 1.0 when an override owns it and its layer constant
    when not; the node takes the minimum (same min-semantics as the shared
    helper). A node whose present layers are all human-owned, or which carries
    only overridden non-layer predicates / removals, scores 1.0.
    """
    owned = store.owned_predicates(node.get("@id"))
    if not owned:
        return heuristic_confidence_for_node(node)
    scores = [
        HUMAN_ASSERTION_CONFIDENCE if pred in owned else layer
        for pred, layer in _CONFIDENCE_LAYERS
        if node.get(pred)
    ]
    if not scores:
        return HUMAN_ASSERTION_CONFIDENCE
    return min(scores, key=float)


def restamp_confidence(node: dict, store: OverrideStore) -> bool:
    """Stamp :func:`confidence_for_node`. Returns True on change.

    Heuristic stamps are never removed (existing behaviour), but a stale
    human 1.0 stamp on a node that lost its last override is replaced or
    dropped: the heuristic layers never produce 1.0, so it is unambiguous.
    """
    value = confidence_for_node(node, store)
    if value:
        return stamp_assertion_confidence(node, value)
    if _has_human_stamp(node):
        del node[CONFIDENCE_PREDICATE]
        return True
    return False


def _has_human_stamp(node: dict) -> bool:
    current = node.get(CONFIDENCE_PREDICATE)
    return isinstance(current, dict) and current.get("@value") == HUMAN_ASSERTION_CONFIDENCE


def finalize_node(node: dict, store: OverrideStore, predicates: Iterable[str]) -> bool:
    """Apply overrides, then restamp confidence on overridden nodes only.

    For classifiers that do not otherwise stamp confidence (competence). A
    node with no override keeps its confidence untouched unless it carries a
    stale human 1.0 stamp, which is recomputed.
    """
    changed = apply_node_overrides(node, store, predicates)
    if store.for_node(node.get("@id")) or _has_human_stamp(node):
        changed |= restamp_confidence(node, store)
    return changed


# ---------------------------------------------------------------------------
# Stale reporting
# ---------------------------------------------------------------------------


def collect_node_ids(files: Iterable[Path]) -> set[str]:
    """Every string ``@id`` in the ``@graph`` of each readable JSON-LD file."""
    ids: set[str] = set()
    for path in files:
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        graph = doc.get("@graph") if isinstance(doc, dict) else None
        if not isinstance(graph, list):
            continue
        for node in graph:
            if isinstance(node, dict) and isinstance(node.get("@id"), str):
                ids.add(node["@id"])
    return ids


def check_overrides(
    store: OverrideStore,
    files: Iterable[Path] | None = None,
    predicates: Iterable[str] | None = None,
    *,
    node_ids: set[str] | None = None,
) -> dict:
    """Dry run: which overrides would apply and which are stale.

    Pass either the corpus ``files`` to scan or a precomputed ``node_ids`` set.
    A stale override names a node absent from the scanned graphs.
    """
    if node_ids is None:
        node_ids = collect_node_ids(files or [])
    records = store.records(predicates)
    applicable = [rec for rec in records if rec.node in node_ids]
    stale = [rec for rec in records if rec.node not in node_ids]
    return {
        "total": len(records),
        "applicable": len(applicable),
        "stale": [
            {"line": rec.line, "node": rec.node, "predicate": rec.predicate}
            for rec in stale
        ],
    }


def print_check_report(report: Mapping, label: str, out=None) -> None:
    out = out or sys.stdout
    print(f"Heuristic overrides ({label}):", file=out)
    print(f"  Overrides for this classifier: {report['total']}", file=out)
    print(f"  Would apply:                   {report['applicable']}", file=out)
    print(f"  Stale (node not found):        {len(report['stale'])}", file=out)
    for item in report["stale"]:
        print(f"    line {item['line']}: {item['node']} {item['predicate']}", file=out)


# ---------------------------------------------------------------------------
# CLI: validate / list / stale
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and inspect data/heuristic_overrides.jsonl (#700)."
    )
    parser.add_argument(
        "--path", type=Path, default=None, help="override store (default: data/heuristic_overrides.jsonl)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate every line; non-zero exit on error")
    lst = sub.add_parser("list", help="print overrides as JSON lines")
    lst.add_argument(
        "--classifier", choices=sorted(CLASSIFIER_PREDICATES), default=None
    )
    stale = sub.add_parser("stale", help="report overrides whose node is not in the corpus")
    stale.add_argument(
        "--classifier", choices=sorted(CLASSIFIER_PREDICATES), default=None
    )
    stale.add_argument(
        "--exclude-kov", action="store_true", help="skip KOV regulation peeps"
    )
    args = parser.parse_args(argv)

    try:
        store = load_overrides(args.path)
    except OverrideError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.command == "validate":
        print(f"OK: {len(store)} override(s) in {store.source}")
        return 0

    predicates = CLASSIFIER_PREDICATES.get(args.classifier) if args.classifier else None
    if args.command == "list":
        for rec in store.records(predicates):
            print(json.dumps({"line": rec.line, **rec.as_record()}, ensure_ascii=False))
        return 0

    from estleg.estleg_common import iter_peep_files

    report = check_overrides(
        store, iter_peep_files(include_kov=not args.exclude_kov), predicates
    )
    print_check_report(report, args.classifier or "all classifiers")
    return 1 if report["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
