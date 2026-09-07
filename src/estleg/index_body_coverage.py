#!/usr/bin/env python3
"""INDEX law-body coverage helpers (issue #507).

INDEX consumers that look up ``estleg:legalText`` (or LegalProvision-typed
nodes) dead-end on two different shapes:

* treaty / ratification slugs with no provision instances (expected)
* substantive statutes whose provision nodes only carry ``estleg:summary``

This module classifies each INDEX law, copies existing summary text onto
``estleg:legalText`` (never invents wording), and restamps INDEX with
``provisionCount`` / ``legalTextCount`` / ``stubKind``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from estleg.estleg_common import KRR_DIR, act_root_node, jsonld_text, save_json

LEGAL_TEXT_KEY = "estleg:legalText"
SUMMARY_KEY = "estleg:summary"

# International-instrument stems. Treaty classification is only applied to
# zero-provision INDEX entries, so a token that also appears in a body-bearing
# statute slug is harmless. ``konven`` / ``lepin`` / ``_rat`` catch truncated
# INDEX names (``…_konven``, ``…_lepin``, ``…_kokkulepete_rat``).
TREATY_SLUG_TOKENS: tuple[str, ...] = (
    "konventsioon",
    "konven",
    "lepingu",
    "lepin",
    "protokoll",
    "ratifitseer",
    "uhinemis",
    "valisleping",
    "välisleping",
    "tropilise",
    "troopilise",
    "genfi",
    "kokkulep",
    "memorandum",
    "topeltmaksustamise",
    "assotsie",
    "haagi",
    "vahelise",
    "vaheline",
    "uhelt_poolt",
    "rahvusvahelis",
    "eesti_vabariigi_ja_",
    "eesti_vabariigi_valitsuse_",
    "eesti_vabariigi_ning_",
    "harta",
    "nato_",
    "merereostuse",
    "jalavaemiinide",
    "tavarelvade",
    "bakterioloogiliste",
    "keskkonnainfo",
    "ohusoiduki",
    "jalgpallivoistluste",
    "kuningriigi",
    "majandusuhenduse",
    "meteoroloogiasatelliitide",
    "euroopa_uhenduse",
    "korgharidustunnistuste",
)

# Post-backfill count of non-treaty INDEX laws with 0 LegalProvision nodes.
# ``validate_index_body_coverage`` errors if the live count exceeds this.
# Set from the measured corpus after summary→legalText backfill (summaryOnly
# becomes 0; empty/treaty counts are unchanged by that copy).
EMPTY_SUBSTANTIVE_BASELINE = 2

RATIFICATION_SHELL_TRUE = {"@value": "true", "@type": "xsd:boolean"}
RATIFICATION_SHELL_REASON = (
    "Ratification/accession act only — the treaty body (välisleping) "
    "is not ingested (#528)."
)


def is_treaty_slug(name: str) -> bool:
    """True when ``name`` looks like a treaty / ratification / instrument slug."""
    if not isinstance(name, str) or not name:
        return False
    slug = name.casefold()
    if any(token in slug for token in TREATY_SLUG_TOKENS):
        return True
    return slug.endswith("_rat") or "_rat_" in slug


def stamp_ratification_shell(node: dict) -> bool:
    """Mark an act root as a ratification shell (#528). Returns True if changed."""
    changed = False
    if node.get("estleg:isRatificationShell") != RATIFICATION_SHELL_TRUE:
        node["estleg:isRatificationShell"] = dict(RATIFICATION_SHELL_TRUE)
        changed = True
    reason = node.get("estleg:contentStatusReason")
    if isinstance(reason, dict):
        reason = reason.get("@value")
    if not reason:
        node["estleg:contentStatusReason"] = RATIFICATION_SHELL_REASON
        changed = True
    elif isinstance(reason, str) and "välisleping" not in reason and "#528" not in reason:
        node["estleg:contentStatusReason"] = f"{reason.rstrip('.')} {RATIFICATION_SHELL_REASON}"
        changed = True
    return changed


def _node_types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str)]
    return []


def is_legal_provision_node(node: object) -> bool:
    """True for a LegalProvision *instance* (not the per-law owl:Class)."""
    if not isinstance(node, dict):
        return False
    types = _node_types(node)
    if "owl:Class" in types:
        return False
    return any(
        item == "estleg:LegalProvision" or item.startswith("estleg:LegalProvision_")
        for item in types
    )


def _iter_nodes(doc: object) -> list[dict]:
    if isinstance(doc, dict):
        graph = doc.get("@graph")
        if isinstance(graph, list):
            return [node for node in graph if isinstance(node, dict)]
        if "@id" in doc or "@type" in doc:
            return [doc]
        return []
    if isinstance(doc, list):
        return [node for node in doc if isinstance(node, dict)]
    return []


def _has_legal_text(node: dict) -> bool:
    return bool(jsonld_text(node.get(LEGAL_TEXT_KEY)).strip())


def _load_json(path: Path) -> object | None:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def count_provisions(doc) -> tuple[int, int]:
    """Return ``(provision_typed, with_legal_text)`` for one JSON-LD document."""
    typed = 0
    with_legal_text = 0
    for node in _iter_nodes(doc):
        if not is_legal_provision_node(node):
            continue
        typed += 1
        if _has_legal_text(node):
            with_legal_text += 1
    return typed, with_legal_text


def iter_index_laws(index) -> list[dict]:
    """Return INDEX ``laws`` entries (accepts the INDEX object or a list)."""
    if isinstance(index, dict):
        laws = index.get("laws")
    elif isinstance(index, list):
        laws = index
    else:
        return []
    if not isinstance(laws, list):
        return []
    return [entry for entry in laws if isinstance(entry, dict)]


def classify_index_entry(entry, krr_dir) -> dict:
    """Classify one INDEX law by provision / legalText counts.

    Returns ``provisionCount``, ``legalTextCount``, and ``stubKind`` when the
    law has no legalText-bearing provision:

    * omitted when ``legalTextCount > 0``
    * ``\"treaty\"`` — zero provisions and a treaty slug
    * ``\"summaryOnly\"`` — provisions exist but none have legalText
    * ``\"empty\"`` — zero provisions and not a treaty slug
    """
    provision_count = 0
    legal_text_count = 0
    files = entry.get("files") if isinstance(entry, dict) else None
    krr = Path(krr_dir)
    if isinstance(files, list):
        for file_name in files:
            if not isinstance(file_name, str) or not file_name:
                continue
            path = krr / file_name
            if not path.is_file():
                continue
            doc = _load_json(path)
            if doc is None:
                continue
            typed, with_text = count_provisions(doc)
            provision_count += typed
            legal_text_count += with_text

    result: dict = {
        "provisionCount": provision_count,
        "legalTextCount": legal_text_count,
    }
    if legal_text_count > 0:
        return result
    name = ""
    if isinstance(entry, dict) and isinstance(entry.get("name"), str):
        name = entry["name"]
    if provision_count == 0 and is_treaty_slug(name):
        result["stubKind"] = "treaty"
    elif provision_count > 0:
        result["stubKind"] = "summaryOnly"
    else:
        result["stubKind"] = "empty"
    return result


def apply_body_coverage_fields(entry: dict, krr_dir) -> dict:
    """Stamp coverage fields onto an INDEX law entry. Omits ``stubKind`` if None."""
    coverage = classify_index_entry(entry, krr_dir)
    entry["provisionCount"] = coverage["provisionCount"]
    entry["legalTextCount"] = coverage["legalTextCount"]
    if coverage.get("stubKind"):
        entry["stubKind"] = coverage["stubKind"]
    else:
        entry.pop("stubKind", None)
    return entry


def classification_counts(index, krr_dir) -> dict[str, int]:
    """Return ``ok`` / ``treaty`` / ``summaryOnly`` / ``empty`` counts."""
    counts = {"ok": 0, "treaty": 0, "summaryOnly": 0, "empty": 0}
    for entry in iter_index_laws(index):
        kind = classify_index_entry(entry, krr_dir).get("stubKind")
        if kind is None:
            counts["ok"] += 1
        else:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def unmarked_empty_entries(index, krr_dir) -> list[dict]:
    """INDEX laws that classify as ``empty`` but are not stamped ``stubKind=empty``."""
    unmarked: list[dict] = []
    for entry in iter_index_laws(index):
        coverage = classify_index_entry(entry, krr_dir)
        if coverage.get("stubKind") == "empty" and entry.get("stubKind") != "empty":
            unmarked.append(entry)
    return unmarked


def legal_text_from_summary(node) -> bool:
    """Copy a provision's non-empty summary onto ``estleg:legalText``.

    No-op when the node is not a provision, already has legalText, or has no
    summary text. Returns True only when the node was changed.
    """
    if not is_legal_provision_node(node):
        return False
    if _has_legal_text(node):
        return False
    summary = node.get(SUMMARY_KEY)
    if not jsonld_text(summary).strip():
        return False
    if LEGAL_TEXT_KEY in node:
        node[LEGAL_TEXT_KEY] = summary
        return True
    rebuilt: dict = {}
    for key, value in node.items():
        rebuilt[key] = value
        if key == SUMMARY_KEY:
            rebuilt[LEGAL_TEXT_KEY] = summary
    node.clear()
    node.update(rebuilt)
    return True


def backfill_legal_text(index, krr_dir) -> tuple[int, int]:
    """Copy summary→legalText on INDEX ``summaryOnly`` root ``*_peep.json`` files.

    Returns ``(files_changed, nodes_changed)``. Does not invent text.
    """
    krr = Path(krr_dir)
    files_changed = 0
    nodes_changed = 0
    seen: set[Path] = set()
    for entry in iter_index_laws(index):
        coverage = classify_index_entry(entry, krr)
        if coverage.get("stubKind") != "summaryOnly":
            continue
        files = entry.get("files") or []
        if not isinstance(files, list):
            continue
        for file_name in files:
            if not isinstance(file_name, str) or not file_name.endswith("_peep.json"):
                continue
            path = krr / file_name
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            doc = _load_json(path)
            if not isinstance(doc, dict):
                continue
            graph = doc.get("@graph")
            if not isinstance(graph, list):
                continue
            changed = False
            for node in graph:
                if legal_text_from_summary(node):
                    changed = True
                    nodes_changed += 1
            if changed:
                save_json(path, doc)
                files_changed += 1
    return files_changed, nodes_changed


def stamp_ratification_shells(index, krr_dir) -> tuple[int, int]:
    """Stamp ``isRatificationShell`` on treaty INDEX peeps (#528)."""
    krr = Path(krr_dir)
    files_changed = 0
    nodes_changed = 0
    seen: set[Path] = set()
    for entry in iter_index_laws(index):
        name = entry.get("name") if isinstance(entry, dict) else None
        if not is_treaty_slug(str(name or "")) and entry.get("stubKind") != "treaty":
            continue
        files = entry.get("files") if isinstance(entry, dict) else None
        if not isinstance(files, list):
            continue
        for file_name in files:
            if not isinstance(file_name, str):
                continue
            path = krr / file_name
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            doc = _load_json(path)
            if not isinstance(doc, dict):
                continue
            root = act_root_node(doc)
            if root is None:
                continue
            if stamp_ratification_shell(root):
                save_json(path, doc)
                files_changed += 1
                nodes_changed += 1
    return files_changed, nodes_changed


def stamp_index(index_path: Path, krr_dir: Path | None = None) -> dict:
    """Rewrite INDEX.json coverage fields in place. Preserves other keys."""
    index_path = Path(index_path)
    krr = Path(krr_dir) if krr_dir is not None else index_path.parent
    index = _load_json(index_path)
    if not isinstance(index, dict):
        raise ValueError(f"INDEX is not an object: {index_path}")
    for entry in iter_index_laws(index):
        apply_body_coverage_fields(entry, krr)
    save_json(index_path, index)
    return index


def _print_counts(label: str, counts: dict[str, int]) -> None:
    total = sum(counts.values())
    print(
        f"{label}: total={total} ok={counts.get('ok', 0)} "
        f"treaty={counts.get('treaty', 0)} "
        f"empty={counts.get('empty', 0)} "
        f"summaryOnly={counts.get('summaryOnly', 0)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    parser.add_argument(
        "--backfill-legal-text",
        action="store_true",
        help="Copy estleg:summary onto estleg:legalText for summaryOnly INDEX peeps",
    )
    parser.add_argument(
        "--stamp-index",
        action="store_true",
        help="Stamp provisionCount / legalTextCount / stubKind onto INDEX.json",
    )
    parser.add_argument(
        "--stamp-shells",
        action="store_true",
        help="Stamp estleg:isRatificationShell on treaty INDEX peeps (#528)",
    )
    args = parser.parse_args(argv)

    krr_dir = Path(args.krr_dir)
    index_path = krr_dir / "INDEX.json"
    index = _load_json(index_path)
    if not isinstance(index, dict):
        print(f"ERROR: cannot load {index_path}", file=sys.stderr)
        return 1

    before = classification_counts(index, krr_dir)
    _print_counts("before", before)

    if args.backfill_legal_text:
        files_changed, nodes_changed = backfill_legal_text(index, krr_dir)
        print(f"backfill: files={files_changed} nodes={nodes_changed}")

    if args.stamp_index:
        index = stamp_index(index_path, krr_dir)
        print(f"stamped {index_path}")

    if args.stamp_shells:
        files_changed, nodes_changed = stamp_ratification_shells(index, krr_dir)
        print(f"ratification shells: files={files_changed} nodes={nodes_changed}")

    after = classification_counts(index, krr_dir)
    _print_counts("after", after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
