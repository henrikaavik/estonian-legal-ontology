"""CLI: build data/ehak/issuers.json from auto-match + curated CSV.

Run: python scripts/build_kov_registry.py
Exits non-zero with a list of unmapped issuers if any are unresolvable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kov_registry import (
    build_issuer_registry,
    discover_issuer_slugs,
    load_curated_map,
    load_municipalities,
)


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

    rows = [registry[slug] for slug in sorted(registry)]
    with open(issuers_out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["mappingSource"]] = by_source.get(r["mappingSource"], 0) + 1
    print(f"Wrote {len(rows)} issuers to {issuers_out}")
    print("By mapping source:", by_source)
    return 0


def main() -> int:
    return build(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
