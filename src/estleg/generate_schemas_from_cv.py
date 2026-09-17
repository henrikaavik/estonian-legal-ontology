#!/usr/bin/env python3
"""Project the four subcorpus ``*_schema.json`` files from the CV (#433).

    python3 scripts/generate_schemas_from_cv.py --check
    python3 scripts/generate_schemas_from_cv.py --write
"""

from __future__ import annotations

import argparse
import sys

from estleg.consolidate_tbox import (
    VOCAB_PATH,
    load_jsonld,
    write_schemas_from_cv,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 unless every committed schema term exists in the CV",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the four schema files as CV projections",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.check and not args.write:
        print("specify --check and/or --write", file=sys.stderr)
        return 2
    vocab = load_jsonld(VOCAB_PATH)
    missing = write_schemas_from_cv(
        vocab,
        check_only=not args.write,
    )
    if missing:
        print("schema terms missing from controlled_vocabulary.jsonld:")
        for name, ids in missing.items():
            print(f"  {name}: {', '.join(ids)}")
        return 1
    action = "rewrote" if args.write else "checked"
    print(f"{action} 4 subcorpus schemas — all terms present in the CV")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
