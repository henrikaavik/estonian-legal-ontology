#!/usr/bin/env python3
"""Backfill the canonical § / lõige fields on the hand-modelled legacy modules.

PR #741 added ``ProvisionRequires{Paragrahv,Summary,PartOfAct}Shape``: every
``estleg:LegalProvision`` that is not a lõige (``estleg:Subsection``) must carry
``estleg:paragrahv``, ``estleg:summary`` and ``estleg:partOfAct``. The two
hand-modelled OWL modules (``karistusseadustik_eriosa_owl.jsonld``,
``tsus_osa7_138_169_owl.jsonld``) and the deprecated legacy duplicate peeps
(``volaigusseadus_osa*_peep.json`` / ``tsiviilseadustik_osa*_peep.json``, #426)
predate that contract: their § nodes carry ``estleg:sectionNumber`` + an
``rdfs:label`` "§ N. …" instead, and their lõige children (reached via
``estleg:hasProvision``) are typed bare ``estleg:LegalProvision``.

This pass mirrors the canonical peep shapes, additively and idempotently:

* **§ node** (``estleg:Section`` / ``estleg:LegalProvision``, has a
  ``sectionNumber`` or a "§" label, not a lõige): backfill ``estleg:paragrahv``
  ("§ 88.", "§ 22¹.", "§§ 276–308."), ``estleg:summary`` (the canonical
  ``law_structure`` 500-char boundary-truncated preview of the § text, else of
  its lõiked's text, else "§ N. Title") and ``estleg:partOfAct`` (the file's
  ``estleg:Act`` / ``estleg:Part`` root). A node gaining ``paragrahv`` also gains
  ``estleg:LegalProvision`` in ``@type`` (validate_all #434 requires it).
* **lõige node** (bare ``estleg:LegalProvision`` listed in a §'s
  ``estleg:hasProvision`` with a ``…_Lg<N>`` id): add ``estleg:Subsection`` to
  ``@type``, ``estleg:subsectionNumber`` (from the id) and
  ``estleg:parentProvision`` (the listing §).

Existing values are never overwritten and no type is removed, so a second run
changes nothing.

    python3 scripts/normalise_legacy_modules.py           # rewrite in place
    python3 scripts/normalise_legacy_modules.py --check   # exit 1 if work remains
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from estleg.estleg_common import KRR_DIR, jsonld_text, node_type_list, save_json
from estleg.law_structure import _truncate_at_boundary, provision_summary

MODULE_FILES = (
    "karistusseadustik_eriosa_owl.jsonld",
    "tsus_osa7_138_169_owl.jsonld",
)
LEGACY_PEEP_GLOBS = (
    "volaigusseadus_osa*_peep.json",
    "tsiviilseadustik_osa*_peep.json",
)

SECTION = "estleg:Section"
PROVISION = "estleg:LegalProvision"
SUBSECTION = "estleg:Subsection"
ROOT_TYPES = ("estleg:Act", "estleg:Part")

PARAGRAHV = "estleg:paragrahv"
SUMMARY = "estleg:summary"
PART_OF_ACT = "estleg:partOfAct"
SECTION_NUMBER = "estleg:sectionNumber"
LEGAL_TEXT = "estleg:legalText"
HAS_PROVISION = "estleg:hasProvision"
PARENT_PROVISION = "estleg:parentProvision"
SUBSECTION_NUMBER = "estleg:subsectionNumber"

# Same cap the canonical builder (law_structure.collect_text) uses.
SUMMARY_MAX_LEN = 500

_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
# "88", "22¹", "22^1", "22_1" -- a single section number with optional index.
_SECTION_NR_RE = re.compile(r"^(\d+)(?:\s*[\^_]\s*(\d+)|([⁰¹²³⁴⁵⁶⁷⁸⁹]+))?$")
_SECTION_RANGE_RE = re.compile(r"^(\d+)\s*[-–]\s*(\d+)$")
# Label prefix "§ 88." / "§§ 276–308." / "§170." -> the display reference.
_LABEL_PREFIX_RE = re.compile(r"^(§§?\s*[\d⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:\s*[-–]\s*[\d⁰¹²³⁴⁵⁶⁷⁸⁹]+)?\.?)\s*(.*)$", re.S)
# Lõige ids: estleg:Par138_Lg1, …_Par_7_Lg_1, …_Lg_2_1 (superscript "2¹").
_LOIGE_ID_RE = re.compile(r"_Lg_?(\d+)(?:_(\d+))?$")

CHANGE_KEYS = (
    "paragrahv",
    "summary",
    "partOfAct",
    "provisionType",
    "subsectionType",
    "subsectionNumber",
    "parentProvision",
)


def _ids(value: object) -> list[str]:
    """Return the ``@id`` strings of an object-ref value (single or list)."""
    items = value if isinstance(value, list) else [value]
    return [item["@id"] for item in items if isinstance(item, dict) and isinstance(item.get("@id"), str)]


def _add_type(node: dict, type_id: str) -> bool:
    types = node.get("@type")
    if isinstance(types, str):
        types = [types]
    elif not isinstance(types, list):
        types = []
    if type_id in types:
        return False
    node["@type"] = [*types, type_id]
    return True


def _set_if_missing(node: dict, key: str, value: object) -> bool:
    if key in node or value in (None, ""):
        return False
    node[key] = value
    return True


def format_paragrahv(section_number: str) -> str | None:
    """Render a ``sectionNumber`` the way canonical peeps write ``paragrahv``."""
    nr = section_number.strip()
    if m := _SECTION_NR_RE.match(nr):
        index = (m.group(2) or "").translate(_SUPERSCRIPT) or (m.group(3) or "")
        return f"§ {m.group(1)}{index}."
    if m := _SECTION_RANGE_RE.match(nr):
        return f"§§ {m.group(1)}–{m.group(2)}."
    return None


def split_label(label: str) -> tuple[str, str] | None:
    """Split "§ 88. Title" into ("§ 88.", "Title"); None if not a § label."""
    m = _LABEL_PREFIX_RE.match(label.strip())
    if not m:
        return None
    prefix = m.group(1)
    if not prefix.endswith("."):
        prefix += "."
    return prefix, m.group(2).strip()


def subsection_number_from_id(node_id: str) -> str | None:
    m = _LOIGE_ID_RE.search(node_id)
    if not m:
        return None
    return m.group(1) + (m.group(2) or "").translate(_SUPERSCRIPT)


def find_root_id(graph: list) -> str | None:
    """The file's single ``estleg:Act`` / ``estleg:Part`` root, else None."""
    roots = [
        node["@id"]
        for node in graph
        if isinstance(node, dict)
        and isinstance(node.get("@id"), str)
        and any(t in ROOT_TYPES for t in node_type_list(node))
    ]
    return roots[0] if len(roots) == 1 else None


def _loige_parents(graph: list) -> dict[str, str]:
    """Map lõige-like child id -> id of the § that lists it in hasProvision."""
    by_id = {n["@id"]: n for n in graph if isinstance(n, dict) and isinstance(n.get("@id"), str)}
    parents: dict[str, str] = {}
    for node in by_id.values():
        for child_id in _ids(node.get(HAS_PROVISION)):
            child = by_id.get(child_id)
            if child is None or not _LOIGE_ID_RE.search(child_id):
                continue
            if PROVISION not in node_type_list(child):
                continue
            parents.setdefault(child_id, node["@id"])
    return parents


def _is_section_node(node: dict, loige_ids: set[str]) -> bool:
    types = node_type_list(node)
    if SUBSECTION in types or node.get("@id") in loige_ids:
        return False
    if SECTION not in types and PROVISION not in types:
        return False
    if SECTION_NUMBER in node:
        return True
    return split_label(jsonld_text(node.get("rdfs:label"))) is not None


def _section_summary(node: dict, by_id: dict[str, dict], display: str, title: str) -> str:
    text = jsonld_text(node.get(LEGAL_TEXT)).strip()
    if not text:
        # TsÜS module §s carry no text of their own; their lõiked do.
        parts = (jsonld_text(by_id.get(cid, {}).get(LEGAL_TEXT)).strip() for cid in _ids(node.get(HAS_PROVISION)))
        text = " ".join(p for p in parts if p)
    preview = _truncate_at_boundary(text, SUMMARY_MAX_LEN) if text else ""
    return provision_summary(preview, title, display)


def normalise_document(doc: dict) -> Counter:
    """Apply the § / lõige backfills to ``doc`` in place; return change counts."""
    changes: Counter = Counter()
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return changes
    by_id = {n["@id"]: n for n in graph if isinstance(n, dict) and isinstance(n.get("@id"), str)}
    parents = _loige_parents(graph)
    root_id = find_root_id(graph)

    for node in graph:
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id")
        if node_id in parents:
            changes["subsectionType"] += _add_type(node, SUBSECTION)
            changes["subsectionNumber"] += _set_if_missing(
                node, SUBSECTION_NUMBER, subsection_number_from_id(node_id)
            )
            changes["parentProvision"] += _set_if_missing(node, PARENT_PROVISION, {"@id": parents[node_id]})
            continue
        if not _is_section_node(node, set(parents)):
            continue

        label_parts = split_label(jsonld_text(node.get("rdfs:label")))
        label_prefix, title = label_parts if label_parts else ("", "")
        display = format_paragrahv(jsonld_text(node.get(SECTION_NUMBER))) or label_prefix
        if _set_if_missing(node, PARAGRAHV, display):
            changes["paragrahv"] += 1
            changes["provisionType"] += _add_type(node, PROVISION)
        if SUMMARY not in node:
            summary = _section_summary(node, by_id, jsonld_text(node.get(PARAGRAHV)) or display, title)
            changes["summary"] += _set_if_missing(node, SUMMARY, summary)
        if root_id and root_id != node_id:
            changes["partOfAct"] += _set_if_missing(node, PART_OF_ACT, {"@id": root_id})
    return +changes


def iter_target_files(krr_dir: Path = KRR_DIR) -> Iterator[Path]:
    for name in MODULE_FILES:
        path = krr_dir / name
        if path.exists():
            yield path
    for pattern in LEGACY_PEEP_GLOBS:
        yield from sorted(krr_dir.glob(pattern))


def normalise_files(paths: Iterable[Path], *, write: bool = True) -> dict[str, Counter]:
    """Normalise each file; rewrite it (via ``save_json``) only when it changed."""
    report: dict[str, Counter] = {}
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        changes = normalise_document(doc)
        if changes:
            report[path.name] = changes
            if write:
                save_json(path, doc)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    parser.add_argument("--check", action="store_true", help="report pending changes, write nothing, exit 1 if any")
    args = parser.parse_args(argv)

    report = normalise_files(iter_target_files(args.krr_dir), write=not args.check)
    for name, changes in report.items():
        detail = ", ".join(f"{key}={changes[key]}" for key in CHANGE_KEYS if changes[key])
        print(f"{name}: {detail}")
    verb = "would change" if args.check else "changed"
    print(f"{len(report)} file(s) {verb}.")
    return 1 if args.check and report else 0


if __name__ == "__main__":
    raise SystemExit(main())
