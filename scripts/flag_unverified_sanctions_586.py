#!/usr/bin/env python3
"""Flag municipal-regulation sanctions whose source provision text is absent (#586).

Offline, idempotent, surgical post-process over ``krr_outputs/sanctions/*.json``.
It does **not** delete sanctions and does **not** fetch anything from the
network — it *preserves* the data and annotates it so the provenance gap is
explicit and the labels are less degraded.

Background (#586)
-----------------
~90 of 2,550 sanctions come from municipal ``eeskiri`` / ``põhimäärus``
regulations and reference a provision IRI of the form
``estleg:Reg_<id>_Par_N``. For ~87 of those regs the source ``_peep.json`` full
text was, at the time the issue was filed, never emitted, so the target
provision existed only as a textless graph-closure stub (label-only
``§ N. Vastutus``, no ``estleg:legalText``). The combined-graph closure gate
passes regardless because it checks *presence* of the stub node, not whether
that node retained any ``legalText`` — so the penalty's source text is
unreachable and its provenance cannot be verified. State-law sanctions
(0/2,460) are unaffected.

What this script does
---------------------
1. Builds the set of provision IRIs that DO carry ``estleg:legalText`` anywhere
   in the corpus (one pass over ``krr_outputs``; the two large Git-LFS
   artifacts are skipped — a derived Reg provision only ever enters
   ``combined_ontology.jsonld`` as a textless stub, so it can never be the sole
   source of a Reg provision's text).
2. For every ``estleg:Sanction`` whose ``estleg:applicableProvision`` target
   (a) matches ``Reg_\\d+_Par_`` (a municipal-reg provision) AND (b) is NOT in
   that text-bearing set, it:
     * adds ``estleg:provenanceUnverified`` = ``{"@value": true,
       "@type": "xsd:boolean"}`` — a machine-readable flag that the penalty's
       source provision text is absent from the published graph; and
     * enriches the ``rdfs:label`` with the issuing municipality *iff* that can
       be resolved offline from the reg's ``estleg:Reg_<id>_Map_2026`` act node
       (its ``estleg:issuer``). Never fabricated: if the act node / issuer is
       not present in the corpus, the label is left unchanged.

Scope / safety
--------------
  * Only ``estleg:Sanction`` nodes are mutated, and only those targeting a
    *textless* municipal-reg provision. Text-bearing targets and non-reg
    (state-law) targets are never touched.
  * Idempotent: ``estleg:provenanceUnverified`` is added only when absent and
    the issuer is appended to the label only when not already present, so a
    re-run (in either mode) reports 0 newly-flagged / 0 enriched and writes
    nothing.
  * Atomic writes via ``estleg_common.save_json`` (tempfile + ``os.replace``).
  * Git-LFS-pointer guard: a file whose first line is the LFS spec marker is
    skipped (never rewrite a pointer stub into real content).
  * ``--dry-run`` is the DEFAULT; pass ``--apply`` to write.

Run (offline, idempotent):

    .venv/bin/python scripts/flag_unverified_sanctions_586.py            # dry-run
    .venv/bin/python scripts/flag_unverified_sanctions_586.py --apply    # write
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from estleg_common import KRR_DIR, canonical_estleg_ref, save_json

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

SANCTION_TYPE = "estleg:Sanction"
LEGAL_TEXT_KEY = "estleg:legalText"
APPLICABLE_PROVISION_KEY = "estleg:applicableProvision"
PROVENANCE_UNVERIFIED_KEY = "estleg:provenanceUnverified"
ISSUER_KEY = "estleg:issuer"
LABEL_KEY = "rdfs:label"

# The exact typed-literal the flag is stamped with (issue #586).
PROVENANCE_UNVERIFIED_VALUE = {"@value": True, "@type": "xsd:boolean"}

# A municipal-regulation provision IRI (the only targets in scope). Matched
# against the canonical compact ``estleg:`` id, so sub-provisions such as
# ``estleg:Reg_123_Par_8_Lg_2`` are covered too.
_REG_PROVISION_RE = re.compile(r"^estleg:Reg_\d+_Par_")
# Numeric reg id from a provision id / from a reg act (``_Map_…``) node id. Used
# only to bridge a flagged provision to its act node for issuer resolution.
_REG_NUM_FROM_PROVISION_RE = re.compile(r"^estleg:Reg_(\d+)_Par_")
_REG_NUM_FROM_ACT_RE = re.compile(r"^estleg:Reg_(\d+)_Map_")

# Large Git-LFS artifacts skipped during the legalText scan. combined is a
# DERIVED superset whose Reg provisions are textless closure stubs by
# construction (regulations/ is not a merged overlay), so it can never be the
# sole carrier of a Reg provision's text; similarity_index carries no
# provisions at all. Skipping them avoids a multi-hundred-MB read and the
# unmaterialised-pointer trap without affecting any flagging decision.
_SCAN_SKIP_FILENAMES = frozenset(
    {"combined_ontology.jsonld", "similarity_index.json"}
)
_SCAN_SUFFIXES = (".json", ".jsonld")


# ---------------------------------------------------------------------------
# Pure, per-node logic (unit-tested in isolation)
# ---------------------------------------------------------------------------
def _types(node: dict) -> list[str]:
    types = node.get("@type", [])
    if isinstance(types, str):
        return [types]
    return [t for t in types if isinstance(t, str)] if isinstance(types, list) else []


def _is_sanction(node: dict) -> bool:
    return SANCTION_TYPE in _types(node)


def has_legal_text(node: dict) -> bool:
    """True when a node carries a non-empty ``estleg:legalText``.

    Tolerates the three shapes the corpus uses: a plain string, a typed/value
    object (``{"@value": ...}``), or a list of either.
    """
    value = node.get(LEGAL_TEXT_KEY)
    return _value_is_nonempty_text(value)


def _value_is_nonempty_text(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return _value_is_nonempty_text(value.get("@value"))
    if isinstance(value, list):
        return any(_value_is_nonempty_text(item) for item in value)
    # Any other non-null scalar (number/bool) counts as present.
    return True


def applicable_provision_ids(node: dict) -> list[str]:
    """Canonical ``estleg:`` ids of a sanction's applicableProvision target(s)."""
    value = node.get(APPLICABLE_PROVISION_KEY)
    items = value if isinstance(value, list) else [value]
    ids: list[str] = []
    for item in items:
        if isinstance(item, dict):
            ref = item.get("@id")
            if isinstance(ref, str) and ref:
                ids.append(canonical_estleg_ref(ref) or ref)
    return ids


def should_flag(node: dict, text_ids: set[str]) -> bool:
    """True when ``node`` is a Sanction whose municipal-reg provision text is absent.

    A node qualifies iff it is an ``estleg:Sanction`` AND at least one of its
    ``estleg:applicableProvision`` targets matches ``Reg_\\d+_Par_`` AND that
    target is NOT among the provision IRIs that carry ``estleg:legalText``
    (``text_ids``). Non-sanctions, text-bearing targets and non-reg (state-law)
    targets all return ``False``.
    """
    if not _is_sanction(node):
        return False
    for provision_id in applicable_provision_ids(node):
        if _REG_PROVISION_RE.search(provision_id) and provision_id not in text_ids:
            return True
    return False


def reg_num_for_sanction(node: dict) -> str | None:
    """Numeric reg id of the first municipal-reg provision target, else ``None``."""
    for provision_id in applicable_provision_ids(node):
        match = _REG_NUM_FROM_PROVISION_RE.match(provision_id)
        if match:
            return match.group(1)
    return None


def apply_flag(node: dict, issuer: str | None = None) -> bool:
    """Stamp the provenance-unverified flag (+ optional issuer label). Idempotent.

    Returns ``True`` iff the node was changed. ``estleg:provenanceUnverified``
    is added only when absent; ``issuer`` is appended to ``rdfs:label`` only
    when supplied, real, and not already present.
    """
    changed = False
    if PROVENANCE_UNVERIFIED_KEY not in node:
        node[PROVENANCE_UNVERIFIED_KEY] = dict(PROVENANCE_UNVERIFIED_VALUE)
        changed = True
    if issuer:
        issuer = issuer.strip()
    if issuer:
        label = node.get(LABEL_KEY)
        if isinstance(label, str) and issuer not in label:
            node[LABEL_KEY] = f"{label} [{issuer}]"
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Corpus index (text-bearing provisions + reg-act issuers)
# ---------------------------------------------------------------------------
def _is_lfs_pointer(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            first = handle.readline()
    except OSError:
        return False
    return first.startswith(LFS_POINTER_PREFIX)


def _iter_graph_nodes(doc: object):
    graph = doc.get("@graph") if isinstance(doc, dict) else doc
    if isinstance(graph, list):
        for node in graph:
            if isinstance(node, dict):
                yield node


def build_corpus_index(krr_dir: Path) -> tuple[set[str], dict[str, str]]:
    """Single pass over the corpus.

    Returns ``(text_ids, reg_issuers)`` where ``text_ids`` is the set of
    canonical provision IRIs carrying ``estleg:legalText`` and ``reg_issuers``
    maps a numeric reg id to the ``estleg:issuer`` of its ``Reg_<id>_Map_*``
    act node (for offline municipality resolution).
    """
    text_ids: set[str] = set()
    reg_issuers: dict[str, str] = {}

    for suffix in _SCAN_SUFFIXES:
        for path in krr_dir.rglob(f"*{suffix}"):
            if not path.is_file() or path.name in _SCAN_SKIP_FILENAMES:
                continue
            if _is_lfs_pointer(path):
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for node in _iter_graph_nodes(doc):
                node_id = node.get("@id")
                if not isinstance(node_id, str):
                    continue
                canonical = canonical_estleg_ref(node_id) or node_id
                if has_legal_text(node):
                    text_ids.add(canonical)
                act_match = _REG_NUM_FROM_ACT_RE.match(canonical)
                if act_match:
                    issuer = node.get(ISSUER_KEY)
                    if isinstance(issuer, str) and issuer.strip():
                        reg_issuers.setdefault(act_match.group(1), issuer.strip())

    return text_ids, reg_issuers


# ---------------------------------------------------------------------------
# Run bookkeeping + file processing
# ---------------------------------------------------------------------------
class FlagStats:
    """Mutable per-run tally."""

    __slots__ = (
        "files_scanned",
        "files_changed",
        "files_skipped_lfs",
        "sanctions_scanned",
        "sanctions_flagged",
        "flags_added",
        "labels_enriched",
    )

    def __init__(self) -> None:
        self.files_scanned = 0
        self.files_changed = 0
        self.files_skipped_lfs = 0
        self.sanctions_scanned = 0
        self.sanctions_flagged = 0
        self.flags_added = 0
        self.labels_enriched = 0


def process_doc(
    doc: dict,
    text_ids: set[str],
    reg_issuers: dict[str, str],
    stats: FlagStats,
) -> bool:
    """Flag every qualifying sanction in one document. Returns True when changed."""
    changed = False
    for node in _iter_graph_nodes(doc):
        if not _is_sanction(node):
            continue
        stats.sanctions_scanned += 1
        if not should_flag(node, text_ids):
            continue
        stats.sanctions_flagged += 1
        reg_num = reg_num_for_sanction(node)
        issuer = reg_issuers.get(reg_num) if reg_num else None
        had_flag = PROVENANCE_UNVERIFIED_KEY in node
        label_before = node.get(LABEL_KEY)
        if apply_flag(node, issuer):
            changed = True
            if not had_flag:
                stats.flags_added += 1
            if node.get(LABEL_KEY) != label_before:
                stats.labels_enriched += 1
    return changed


def process_file(
    path: Path,
    text_ids: set[str],
    reg_issuers: dict[str, str],
    stats: FlagStats,
    *,
    dry_run: bool,
) -> None:
    stats.files_scanned += 1
    if _is_lfs_pointer(path):
        stats.files_skipped_lfs += 1
        return
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(doc, dict):
        return
    if process_doc(doc, text_ids, reg_issuers, stats):
        stats.files_changed += 1
        if not dry_run:
            save_json(path, doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="Corpus root scanned for legalText + reg issuers (default: krr_outputs).",
    )
    parser.add_argument(
        "--sanctions-dir",
        type=Path,
        default=None,
        help="Directory of sanctions_*.json to flag (default: <krr-dir>/sanctions).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without it the script is a dry-run (the default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing (the default mode).",
    )
    args = parser.parse_args(argv)

    krr_dir: Path = args.krr_dir
    sanctions_dir: Path = args.sanctions_dir or (krr_dir / "sanctions")
    # Dry-run wins on conflict so an explicit --dry-run can never write.
    dry_run = args.dry_run or not args.apply

    if not sanctions_dir.is_dir():
        print(f"ERROR: not a directory: {sanctions_dir}")
        return 1

    print("=" * 70)
    print("Flag unverified municipal-regulation sanctions (#586)")
    print(f"  Corpus root:    {krr_dir}")
    print(f"  Sanctions dir:  {sanctions_dir}")
    print(f"  MODE: {'dry-run (no writes)' if dry_run else 'apply (writing)'}")
    print("=" * 70)

    text_ids, reg_issuers = build_corpus_index(krr_dir)
    print(f"  Text-bearing provisions indexed: {len(text_ids)}")
    print(f"  Reg-act issuers resolved:        {len(reg_issuers)}")

    stats = FlagStats()
    for path in sorted(sanctions_dir.glob("*.json*")):
        process_file(path, text_ids, reg_issuers, stats, dry_run=dry_run)

    verb = "would change" if dry_run else "changed"
    print(f"\n  Sanction files scanned:        {stats.files_scanned}")
    print(f"  Files skipped (LFS pointer):   {stats.files_skipped_lfs}")
    print(f"  Files {verb}:               {stats.files_changed}")
    print(f"  Sanctions scanned:             {stats.sanctions_scanned}")
    print(f"  Sanctions flagged (population):{stats.sanctions_flagged:>4}")
    print(f"  provenanceUnverified added:    {stats.flags_added}")
    print(f"  Labels enriched w/ issuer:     {stats.labels_enriched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
