"""Multipart INDEX coverage helpers (issue #556).

Multi-part codes can ship an incomplete osa sequence. INDEX consumers used
to treat that as a complete `parts_mapped` list. These helpers detect holes
in the min–max `_osaN` file sequence and require each hole to be listed in
`multipart_gaps` or `intentionally_skipped_osa`.
"""

from __future__ import annotations

import re

# Known incompleteness annotations restamped onto INDEX entries by
# ``generate_index`` so a registry rebuild does not silently drop them.
KNOWN_MULTIPART_ANNOTATIONS: dict[str, dict] = {
    "tsiviilseadustiku_uldosa_seadus": {
        "multipart_gaps": ["1", "3", "5", "6"],
        "multipart_gap_note": (
            "TsÜS osa 1, 3, 5 and 6 are not published in this corpus."
        ),
    },
    "tsiviilkohtumenetluse_seadustik": {
        "multipart_gaps": ["8", "9"],
        "multipart_gap_note": (
            "TsMS osa 8 and 9 are not published in this corpus."
        ),
    },
    "volaoigusseadus": {
        "intentionally_skipped_osa": ["6"],
        "multipart_gap_note": "repealed §578–579",
    },
}

_OSA_FILENAME_RE = re.compile(r"_osa(\d+)", re.IGNORECASE)
_MAP_FILENAME_RE = re.compile(r"_map_", re.IGNORECASE)
_ANNOTATION_KEYS = (
    "multipart_gaps",
    "multipart_gap_note",
    "intentionally_skipped_osa",
)


def osa_numbers_from_files(files) -> set[int]:
    """Return osa numbers encoded in `_osaN` filenames.

    `_map_` files are ignored even if the name also contains `_osa`.
    """
    numbers: set[int] = set()
    if not files:
        return numbers
    for name in files:
        if not isinstance(name, str) or _MAP_FILENAME_RE.search(name):
            continue
        match = _OSA_FILENAME_RE.search(name)
        if match:
            numbers.add(int(match.group(1)))
    return numbers


def is_multipart_index_entry(entry: object) -> bool:
    """True when an INDEX law entry is a multi-part (osa-split) code."""
    if not isinstance(entry, dict):
        return False
    parts = entry.get("parts_mapped")
    if isinstance(parts, list) and parts:
        return True
    return len(osa_numbers_from_files(entry.get("files") or [])) >= 2


def iter_multipart_index_entries(index) -> list:
    """Return INDEX `laws` entries that are multi-part codes."""
    if isinstance(index, dict):
        laws = index.get("laws")
    elif isinstance(index, list):
        laws = index
    else:
        return []
    if not isinstance(laws, list):
        return []
    return [entry for entry in laws if is_multipart_index_entry(entry)]


def _as_int_set(values) -> set[int]:
    if values is None:
        return set()
    if isinstance(values, (str, int)):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return set()
    numbers: set[int] = set()
    for value in values:
        try:
            numbers.add(int(value))
        except (TypeError, ValueError):
            continue
    return numbers


def unmarked_multipart_gaps(entry) -> list[int]:
    """Osa numbers missing from the min–max file sequence and not annotated.

    Holes listed in `multipart_gaps` or `intentionally_skipped_osa` are
    treated as marked. `_map_` files do not participate in the sequence.
    """
    if not isinstance(entry, dict):
        return []
    present = osa_numbers_from_files(entry.get("files") or [])
    if len(present) < 2:
        return []
    missing = [n for n in range(min(present), max(present) + 1) if n not in present]
    marked = _as_int_set(entry.get("multipart_gaps")) | _as_int_set(
        entry.get("intentionally_skipped_osa")
    )
    return [n for n in missing if n not in marked]


def apply_known_multipart_annotations(entry: dict) -> dict:
    """Stamp the #556 constant-map annotations onto an INDEX law entry."""
    extra = KNOWN_MULTIPART_ANNOTATIONS.get(entry.get("name"))
    if extra:
        entry.update(extra)
    return entry


def preserve_multipart_annotations(entry: dict, previous: dict | None) -> dict:
    """Keep hand-authored gap fields from a previous INDEX entry.

    Known-map values win; anything else already on `entry` is left in place.
    """
    if not previous:
        return entry
    for key in _ANNOTATION_KEYS:
        if key not in entry and key in previous:
            entry[key] = previous[key]
    return entry
