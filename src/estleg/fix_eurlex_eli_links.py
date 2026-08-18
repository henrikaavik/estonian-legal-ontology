"""Backfill correct ``eli:id_local`` + ELI ``owl:sameAs`` on EU acts (#607b, #610).

Two offline, deterministic corrections to the already-emitted EU-legislation
nodes (``krr_outputs/eurlex/eurlex_*_peep.json`` + ``eurlex_combined.jsonld``),
so the data matches the fixed ``generate_eu_legislation.py`` without re-running
the CELLAR-backed (network) pipeline:

  **#607(b) — ``eli:id_local`` carries the CELEX, not the ELI natural id.**
      Every EU node has ``eli:id_local`` byte-identical to ``estleg:celexNumber``
      (``"32008R0015"``), while the true ELI natural id (``reg/2008/15``) sits in
      ``estleg:eliIdentifier`` (``http://data.europa.eu/eli/reg/2008/15/oj``).
      Re-derive ``eli:id_local`` from the ELI URI's natural-id segment. Nodes
      with no ``estleg:eliIdentifier`` keep CELEX (no ELI id exists to use).
      ``estleg:celexNumber`` is never touched.

  **#610 — the ELI canonical URI is a dead ``xsd:anyURI`` literal, not a link.**
      ``estleg:eliIdentifier`` is kept (string consumers rely on it), but the
      same URI is ALSO emitted as an ``owl:sameAs`` object-link so the act is
      actually joined to the EU ELI register for follow-your-nose dereference.
      The pre-existing CELEX ``owl:sameAs``
      (``http://publications.europa.eu/resource/celex/…``) is preserved — only an
      ELI ``owl:sameAs`` is ADDED, turning the single value into a 2-item list.

Both transforms reuse :func:`generate_eu_legislation.eli_natural_id` so the
ELI-URL parsing lives in exactly one place.

Scope / safety:
  * Touches only ``@id`` ``estleg:EU_*`` nodes carrying an ``eli:id_local`` or an
    ``estleg:eliIdentifier``.
  * Idempotent: ``eli:id_local`` already holding the natural id is left as-is,
    and an ELI ``owl:sameAs`` already present is not duplicated — a re-run
    reports 0 changes.
  * Atomic writes via ``estleg_common.save_json``; git-LFS pointer files are
    skipped before ``json.loads``.
  * ``--dry-run`` reports counts without writing.

Run (offline, idempotent)::

    python3 scripts/fix_eurlex_eli_links.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg.estleg_common import REPO_ROOT, save_json
from estleg.generate_eu_legislation import eli_natural_id

EURLEX_DIR = REPO_ROOT / "krr_outputs" / "eurlex"
DEFAULT_TARGETS = (
    "eurlex_regulations_peep.json",
    "eurlex_directives_peep.json",
    "eurlex_decisions_peep.json",
    "eurlex_combined.jsonld",
)

CELEX_SAMEAS_HOST = "publications.europa.eu/resource/celex"
ELI_HOST = "data.europa.eu/eli"


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return fh.read(60).startswith("version https://git-lfs")
    except OSError:
        return True


def _as_uri(value) -> str | None:
    """Pull a URI string out of a ``{"@value": …}`` / ``{"@id": …}`` / str."""
    if isinstance(value, dict):
        v = value.get("@value") or value.get("@id")
        return v if isinstance(v, str) else None
    return value if isinstance(value, str) else None


def _sameas_targets(node: dict) -> set[str]:
    """Return the set of existing ``owl:sameAs`` @id targets on ``node``."""
    sa = node.get("owl:sameAs")
    if sa is None:
        return set()
    items = sa if isinstance(sa, list) else [sa]
    out: set[str] = set()
    for item in items:
        tid = item.get("@id") if isinstance(item, dict) else item
        if isinstance(tid, str):
            out.add(tid)
    return out


def _add_sameas(node: dict, target: str) -> bool:
    """Append ``{"@id": target}`` to ``owl:sameAs`` if absent. Returns True if
    added. Normalises a single-value field into a list when it grows to 2."""
    if target in _sameas_targets(node):
        return False
    existing = node.get("owl:sameAs")
    new_link = {"@id": target}
    if existing is None:
        node["owl:sameAs"] = new_link
    elif isinstance(existing, list):
        existing.append(new_link)
    else:
        node["owl:sameAs"] = [existing, new_link]
    return True


def fix_node(node: dict) -> tuple[bool, bool]:
    """Apply #607b + #610 to one EU node in place.

    Returns ``(id_local_fixed, sameas_added)``.
    """
    eli_uri = _as_uri(node.get("estleg:eliIdentifier"))
    natural = eli_natural_id(eli_uri) if eli_uri else None

    id_local_fixed = False
    if natural is not None and node.get("eli:id_local") != natural:
        # Only correct when the current value is NOT already the natural id
        # (so the idempotent re-run is a no-op). The CELEX is left in
        # estleg:celexNumber untouched.
        node["eli:id_local"] = natural
        id_local_fixed = True

    sameas_added = False
    if eli_uri:
        sameas_added = _add_sameas(node, eli_uri)

    return id_local_fixed, sameas_added


def migrate_doc(doc: dict) -> tuple[int, int]:
    """Fix every EU node in ``doc``. Returns ``(id_locals_fixed, sameas_added)``."""
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return 0, 0
    id_fixed = 0
    sa_added = 0
    for node in graph:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id", "")
        if not (isinstance(nid, str) and nid.startswith("estleg:EU_")):
            continue
        a, b = fix_node(node)
        id_fixed += 1 if a else 0
        sa_added += 1 if b else 0
    return id_fixed, sa_added


def process_file(path: Path, dry_run: bool) -> tuple[int, int]:
    """Migrate one eurlex file. Returns ``(id_locals_fixed, sameas_added)``."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    id_fixed, sa_added = migrate_doc(doc)
    if (id_fixed or sa_added) and not dry_run:
        save_json(path, doc)
    return id_fixed, sa_added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eurlex-dir",
        type=Path,
        default=EURLEX_DIR,
        help="Directory holding the eurlex peep/combined files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    args = parser.parse_args(argv)

    eurlex_dir: Path = args.eurlex_dir
    if not eurlex_dir.is_dir():
        print(f"ERROR: not a directory: {eurlex_dir}")
        return 1

    print("=" * 70)
    print("Fix eli:id_local (#607b) + add ELI owl:sameAs (#610) on EU acts")
    print(f"  Directory: {eurlex_dir}")
    print("=" * 70)

    total_id = 0
    total_sa = 0
    for name in DEFAULT_TARGETS:
        path = eurlex_dir / name
        if not path.is_file():
            print(f"  (skip, missing) {name}")
            continue
        if _is_lfs_pointer(path):
            print(f"  (skip, git-LFS pointer — run `git lfs pull`) {name}")
            continue
        id_fixed, sa_added = process_file(path, args.dry_run)
        verb = "would fix" if args.dry_run else "fixed"
        print(
            f"  {name}: eli:id_local {verb}={id_fixed}, ELI owl:sameAs added={sa_added}"
        )
        total_id += id_fixed
        total_sa += sa_added

    print(f"\n  TOTAL eli:id_local corrected: {total_id}")
    print(f"  TOTAL ELI owl:sameAs added:   {total_sa}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
