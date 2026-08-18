#!/usr/bin/env python3
"""Rewrite leftover malformed IRIs from #346 / #354.

* Collapse ``__`` (and longer underscore runs) inside ``estleg:`` IRIs.
* Rename concatenated §-range IDs (``_Par_194`` labelled ``§ 1–94``) to
  ``_Par_1_to_94``.
* Strip a trailing ``_`` from title-only Division/Chapter IDs when that
  does not collide; otherwise keep a numbered suffix.

Does not mint new IRIs from titles — it only rewrites strings that already
start with ``estleg:``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from estleg_common import KRR_DIR

RANGE_LABEL = re.compile(
    r"§\s*(\d+)\s*[‐-―−-]\s*(\d+)",
    re.UNICODE,
)
PAR_TAIL = re.compile(r"(estleg:[A-Za-z0-9]+)_Par_(\d+)$")
DIV_TRAIL = re.compile(
    r"^(estleg:Division_[A-Za-z0-9_]+|estleg:Chapter_[A-Za-z0-9_]+)_$"
)
SKIP_DIRS = frozenset({"retrieval", ".cache"})


def collapse_underscores(iri: str) -> str:
    """``estleg:foo__bar`` → ``estleg:foo_bar``."""
    if not iri.startswith("estleg:"):
        return iri
    prefix, _, rest = iri.partition(":")
    return f"{prefix}:{re.sub(r'_+', '_', rest)}"


def range_target(paragrahv: str, old_iri: str) -> str | None:
    """Return the ``_Par_<a>_to_<b>`` IRI for a range-labelled provision."""
    if not isinstance(paragrahv, str) or not isinstance(old_iri, str):
        return None
    m = RANGE_LABEL.search(paragrahv)
    tail = PAR_TAIL.match(old_iri)
    if not m or not tail:
        return None
    start, end = m.group(1), m.group(2)
    return f"{tail.group(1)}_Par_{start}_to_{end}"


def collect_declared_ids(krr_dir: Path) -> set[str]:
    ids: set[str] = set()
    for path in iter_corpus_json(krr_dir):
        if path.stat().st_size > 80_000_000:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for node in _graph(doc):
            nid = node.get("@id")
            if isinstance(nid, str) and nid.startswith("estleg:"):
                ids.add(nid)
    return ids


def build_range_remap(krr_dir: Path) -> dict[str, str]:
    remap: dict[str, str] = {}
    for path in iter_corpus_json(krr_dir):
        if path.stat().st_size > 80_000_000:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for node in _graph(doc):
            nid = node.get("@id")
            if not isinstance(nid, str):
                continue
            new = range_target(node.get("estleg:paragrahv") or "", nid)
            if new and new != nid:
                remap[nid] = new
    return remap


def build_trailing_div_remap(declared: set[str]) -> dict[str, str]:
    remap: dict[str, str] = {}
    for nid in declared:
        m = DIV_TRAIL.match(nid)
        if not m:
            continue
        stripped = nid.rstrip("_")
        if stripped != nid and stripped not in declared and stripped not in remap.values():
            remap[nid] = stripped
        elif stripped != nid and stripped in declared:
            # Distinct sibling already owns the stripped form (jagu 2 vs 2¹).
            # Fall back to the numeric jagu/chapter token already in the id.
            # e.g. Division_AUTORI_IV_2_TEOSE_KASUTAMINE_ → Division_AUTORI_IV_2
            parts = nid.rstrip("_").split("_")
            # Keep up through the first all-digit segment after Division/Chapter.
            keep: list[str] = []
            seen_digit = False
            for part in parts:
                keep.append(part)
                if part.isdigit():
                    seen_digit = True
                    # keep one more digit-only token if next is also a number
                    continue
                if seen_digit and not part.isdigit():
                    keep.pop()
                    break
            candidate = "_".join(keep)
            if candidate != nid and candidate not in declared:
                remap[nid] = candidate
    return remap


def build_underscore_remap(declared: set[str]) -> dict[str, str]:
    remap: dict[str, str] = {}
    for nid in declared:
        collapsed = collapse_underscores(nid)
        if collapsed != nid:
            remap[nid] = collapsed
    collisions = {old: new for old, new in remap.items() if new in declared}
    if collisions:
        raise ValueError(
            f"{len(collisions)} underscore remaps collide with existing @ids; "
            f"sample {list(collisions.items())[:3]}"
        )
    return remap


def compose_remap(*maps: dict[str, str]) -> dict[str, str]:
    """Merge remaps; later maps apply to already-rewritten targets."""
    out: dict[str, str] = {}
    for mapping in maps:
        next_out = dict(out)
        for old, new in mapping.items():
            next_out[old] = mapping.get(new, new)
        for old, new in list(next_out.items()):
            next_out[old] = mapping.get(new, new)
        for old, new in mapping.items():
            next_out.setdefault(old, new)
        out = next_out
    # Transitive close
    changed = True
    while changed:
        changed = False
        for old, new in list(out.items()):
            if new in out and out[new] != new:
                out[old] = out[new]
                changed = True
    return {old: new for old, new in out.items() if old != new}


def apply_iri(iri: str, remap: dict[str, str]) -> str:
    if iri in remap:
        return remap[iri]
    if not iri.startswith("estleg:"):
        return iri
    collapsed = collapse_underscores(iri)
    # Prefix-extend known remaps (version / lõige / expression suffixes).
    for old, new in remap.items():
        if iri.startswith(old + "_"):
            return new + iri[len(old):]
        if collapsed.startswith(old + "_"):
            return new + collapsed[len(old):]
    return collapsed


def rewrite_value(value: object, remap: dict[str, str]) -> object:
    if isinstance(value, str):
        if value.startswith("estleg:"):
            return apply_iri(value, remap)
        return value
    if isinstance(value, dict):
        return {k: rewrite_value(v, remap) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite_value(v, remap) for v in value]
    return value


def rewrite_text(text: str, remap: dict[str, str]) -> str:
    """Longest-first string replace — used for large combined artifacts."""
    present = [(old, new) for old, new in remap.items() if old in text]
    for old, new in sorted(present, key=lambda kv: -len(kv[0])):
        text = text.replace(old, new)
    # Leftover double-underscores inside compact estleg: IRIs.
    return re.sub(r"(estleg:[A-Za-z0-9_]*)__+", r"\1_", text)


def iter_corpus_json(krr_dir: Path) -> Iterator[Path]:
    for path in sorted(krr_dir.rglob("*")):
        if path.suffix.lower() not in {".json", ".jsonld"}:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        yield path


def _graph(doc: object) -> list[dict]:
    if isinstance(doc, dict):
        graph = doc.get("@graph", [])
        if isinstance(graph, list):
            return [n for n in graph if isinstance(n, dict)]
    return []


def migrate_file(path: Path, remap: dict[str, str]) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    if "__" not in text and not any(old in text for old in remap):
        return 0
    new_text = rewrite_text(text, remap)
    if new_text == text:
        return 0
    # Preserve the original serialisation (combined is 265 MB).
    path.write_text(new_text, encoding="utf-8")
    return 1


def build_full_remap(krr_dir: Path) -> dict[str, str]:
    declared = collect_declared_ids(krr_dir)
    return compose_remap(
        build_underscore_remap(declared),
        build_range_remap(krr_dir),
        build_trailing_div_remap(declared),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    remap = build_full_remap(args.krr_dir)
    print(f"remap entries: {len(remap)}")
    if args.dry_run:
        for old, new in sorted(remap.items())[:20]:
            print(f"  {old} -> {new}")
        if len(remap) > 20:
            print(f"  ... {len(remap) - 20} more")
        return 0
    written = 0
    for path in iter_corpus_json(args.krr_dir):
        written += migrate_file(path, remap)
    print(f"files rewritten: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
