"""CLI: build data/ehak/issuers.json from auto-match + curated CSV.

Run: python scripts/build_kov_registry.py
Exits non-zero with a list of unmapped issuers if any are unresolvable.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from estleg.kov_registry import (
    build_issuer_registry,
    discover_issuer_slugs,
    load_curated_map,
    load_municipalities,
)


def canonical_issuers_json(rows: list[dict]) -> str:
    """Return the canonical hashing form of the issuers list.

    Sort contract for hashing (Finding #9 in issue #182):
      * Top-level array is sorted by `slug` — this is the same order
        that `build()` writes to disk and that downstream readers
        observe.
      * Within each row, keys are emitted in `sort_keys=True` order so
        the digest is stable even if a future code-edit reorders the
        TypedDict fields.

    NOTE: this canonical form is intentionally distinct from the
    on-disk file format. `build()` writes rows in insertion order
    (matching the historical file shape so re-runs don't churn the
    diff), while the digest hashes a key-sorted form so a refactor
    that merely renames or reorders a struct field does not produce
    a fresh sha256. The sha256 surfaces in CLI output for human
    review and is the value coverage reports embed for cross-run
    drift detection.
    """
    sorted_rows = sorted(rows, key=lambda r: r["slug"])
    return (
        json.dumps(sorted_rows, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def compute_issuers_sha256(rows: list[dict]) -> str:
    """Hash the canonical-JSON form of `rows`.

    Hex digest, lowercase. Returned by `build()` so the orchestrator
    can stamp it into the coverage report. See
    `canonical_issuers_json` for the sort contract.
    """
    return hashlib.sha256(
        canonical_issuers_json(rows).encode("utf-8")
    ).hexdigest()


def _on_disk_issuers_json(rows: list[dict]) -> str:
    """Return the on-disk JSON form: array sorted by slug, dict keys
    in insertion order. Matches the historical file shape so re-runs
    against an unchanged registry don't produce a diff.
    """
    return json.dumps(rows, ensure_ascii=False, indent=2) + "\n"


def build(repo_root: Path) -> int:
    """Build issuers.json under <repo_root>/data/ehak/. Returns CLI exit code."""
    ehak_dir = repo_root / "data" / "ehak"
    kov_root = repo_root / "krr_outputs" / "regulations" / "kov"
    municipalities_path = ehak_dir / "municipalities.json"
    curated_csv = ehak_dir / "issuer_successor_map.csv"
    issuers_out = ehak_dir / "issuers.json"

    municipalities = load_municipalities(municipalities_path)
    curated = load_curated_map(curated_csv)
    slugs = discover_issuer_slugs(kov_root)
    print(f"Found {len(slugs)} issuer directories")
    print(f"Curated CSV has {len(curated)} explicit mappings")

    try:
        registry = build_issuer_registry(slugs, municipalities, curated)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Emit slug-sorted rows (array-level ordering). The on-disk
    # form preserves the historical insertion-order keys; the
    # canonical-JSON form (sha256) additionally sorts dict keys so
    # re-runs against an unchanged registry produce the same digest
    # regardless of the IssuerEntry field order.
    rows = [registry[slug] for slug in sorted(registry)]
    issuers_out.write_text(
        _on_disk_issuers_json(rows), encoding="utf-8",
    )
    digest = compute_issuers_sha256(rows)
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["mappingSource"]] = by_source.get(r["mappingSource"], 0) + 1
    print(f"Wrote {len(rows)} issuers to {issuers_out}")
    print(f"Canonical issuers.json sha256: {digest}")
    print("By mapping source:", by_source)
    return 0


def main() -> int:
    return build(Path(__file__).resolve().parents[2])


if __name__ == "__main__":
    raise SystemExit(main())
