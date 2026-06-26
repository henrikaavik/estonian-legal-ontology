#!/usr/bin/env python3
"""Guard the deliberate *string* typing of numeric identity/label fields (#569).

#569 observed that ~43,919 purely-numeric values (``estleg:terviktekstId``,
``estleg:globalId``, ``estleg:actNumber``, ``estleg:ehakCode``,
``estleg:chapterNumber``, ``estleg:sectionNumber`` …) ship as plain
``xsd:string`` literals while *count* properties (``estleg:definitionCount`` …)
ship as ``xsd:integer``, and proposed typing the numeric ones as integers too.

After review (see the ticket's maintainer comment) that fix was rejected as
data loss / inconsistency, and the narrowing is captured here as an executable
decision rather than a code change to the data:

  * **Identifiers stay strings.** ``terviktekstId`` / ``globalId`` /
    ``actNumber`` / ``ehakCode`` are identifiers, not quantities. ``ehakCode``
    in particular carries leading zeros (``0130``, ``0141``) that an
    ``xsd:integer`` would silently erase. They are never sorted or
    range-filtered as numbers; their lexical form IS the value.

  * **Positional "numbers" stay strings because they are NOT guaranteed
    numeric.** ``chapterNumber`` holds Roman numerals (``I``, ``II``, ``III``
    — 81 values), ``sectionNumber`` holds ranges (``276-308``),
    ``subsectionNumber`` holds superscript forms (``1¹``, ``2¹`` — the Estonian
    "and-a-half" provision style, 6,000+ values) and the ``Unknown`` dedup
    placeholder. ``annexNumber`` is parsed straight from free-text source XML
    attributes (``lisaNr`` / ``viideNr`` / ``nr``) that a future act may
    populate with ``A`` or ``I``; the generator deliberately stores it via
    ``str(...)`` + ``sanitize_id`` for exactly that reason. Typing any of these
    as ``xsd:integer`` would make the property *mixed-datatype* the moment a
    non-numeric value appears — strictly worse than the current uniform
    ``xsd:string`` — and would re-introduce the cross-property inconsistency
    #569 complained about, in reverse.

So #569's narrowing makes it a **no-op for the data**: there are no
guaranteed-numeric *true ordinals* to convert. This script does not mutate any
file. Instead it is a regression *guard*: it asserts that every property in
``STRING_IDENTITY_PROPERTIES`` remains a plain string literal (never a
``{"@value": …, "@type": "xsd:integer"}`` value object) across the corpus, so a
future well-meaning "type the numbers" change — especially one that would emit
``{"@value": "0130", "@type": "xsd:integer"}`` and erase a leading zero — fails
loudly here instead of silently corrupting identifiers.

Scope: everything ``estleg_common.iter_peep_files`` yields (root law peeps plus
state/KOV regulation peeps). LFS-pointer files (whose first line is
``version https://git-lfs.github.com/spec/v1``) are skipped — they have no real
JSON to inspect.

Run (offline, read-only):

    .venv/bin/python scripts/check_numeric_identity_strings.py
    .venv/bin/python scripts/check_numeric_identity_strings.py --dry-run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg_common import iter_peep_files

# Properties that #569 + the maintainer review decided MUST remain lexical
# strings: identifiers (leading-zero / opaque-code semantics) and positional
# "numbers" that are not guaranteed numeric (Roman numerals, ranges,
# superscripts, free-text annex labels). Keep this list in sync with the
# module docstring; adding a property here makes it guarded.
STRING_IDENTITY_PROPERTIES: frozenset[str] = frozenset(
    {
        # --- opaque identifiers / codes (never quantities) ---
        "estleg:terviktekstId",
        "estleg:globalId",
        "estleg:actNumber",
        "estleg:ehakCode",  # leading zeros: 0130, 0141, …
        # --- positional "numbers" that are NOT guaranteed numeric ---
        "estleg:chapterNumber",  # Roman numerals: I, II, III, …
        "estleg:sectionNumber",  # ranges: 276-308
        "estleg:subsectionNumber",  # superscripts: 1¹, 2¹ … + Unknown_N
        "estleg:annexNumber",  # free-text source label (may be "A"/"I")
    }
)

# The datatype a careless "type the numbers" change would (wrongly) apply.
# A value object carrying this @type on a guarded property is the violation.
_NUMERIC_XSD_TYPES: frozenset[str] = frozenset(
    {
        "xsd:integer",
        "http://www.w3.org/2001/XMLSchema#integer",
        "xsd:int",
        "xsd:long",
        "xsd:decimal",
    }
)

_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


def _is_lfs_pointer(path: Path) -> bool:
    """True iff ``path`` is a Git-LFS pointer rather than real JSON."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
    except (OSError, UnicodeDecodeError):
        return False
    return first.startswith(_LFS_POINTER_PREFIX)


def _is_numeric_typed_object(value: object) -> bool:
    """True iff ``value`` is a ``{"@value": …, "@type": <numeric xsd>}`` object.

    This is the exact shape #569 proposed and the review rejected for these
    properties. A plain string (the correct representation) returns False.
    """
    if not isinstance(value, dict):
        return False
    vtype = value.get("@type")
    if isinstance(vtype, str):
        return vtype in _NUMERIC_XSD_TYPES
    if isinstance(vtype, list):
        return any(t in _NUMERIC_XSD_TYPES for t in vtype if isinstance(t, str))
    return False


def find_violations(doc: dict) -> list[tuple[str, str, object]]:
    """Return ``(node_id, property, value)`` for every numeric-typed identity.

    A violation is a guarded property whose value (or any element of a
    list-valued property) is a numeric-typed value object instead of a plain
    string literal.
    """
    graph = doc.get("@graph", [])
    if not isinstance(graph, list):
        return []
    violations: list[tuple[str, str, object]] = []
    for node in graph:
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id", "<no @id>")
        for prop in STRING_IDENTITY_PROPERTIES:
            if prop not in node:
                continue
            value = node[prop]
            candidates = value if isinstance(value, list) else [value]
            for item in candidates:
                if _is_numeric_typed_object(item):
                    violations.append((str(node_id), prop, item))
    return violations


def process_file(path: Path) -> list[tuple[str, str, str, object]]:
    """Scan one peep file. Returns ``(file, node, property, value)`` violations.

    LFS-pointer files (no real JSON) are skipped. Unreadable / malformed JSON
    is skipped (the dedicated syntax validators own that failure mode).
    """
    if _is_lfs_pointer(path):
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return []
    return [(path.name, n, p, v) for (n, p, v) in find_violations(doc)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only guard anyway; accepted for interface symmetry "
        "with the migration scripts (this check never writes).",
    )
    parser.parse_args(argv)

    files = iter_peep_files()

    print("=" * 70)
    print("Guard: numeric identity/label fields stay xsd:string (#569)")
    print(f"  Peep files scanned:   {len(files)}")
    print(f"  Guarded properties:   {len(STRING_IDENTITY_PROPERTIES)}")
    print("=" * 70)

    all_violations: list[tuple[str, str, str, object]] = []
    for path in files:
        all_violations.extend(process_file(path))

    if all_violations:
        print(f"\n  VIOLATIONS: {len(all_violations)} numeric-typed identity values")
        print("  (these must remain plain xsd:string per #569 — see docstring)\n")
        for fname, node_id, prop, value in all_violations[:50]:
            print(f"    {fname}: {node_id} {prop} = {value!r}")
        if len(all_violations) > 50:
            print(f"    … and {len(all_violations) - 50} more")
        return 1

    print("\n  OK: all guarded identity/label fields are plain strings.")
    print("  #569 narrowing holds — no safe ordinal conversions exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
