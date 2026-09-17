"""Console entry point: ``estleg-load <name>``."""

from __future__ import annotations

import argparse
import sys

from estleg_client.load import LawNotFoundError, load_law, provisions_of


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="estleg-load",
        description=(
            "Load an enacted law from the Estonian Legal Ontology corpus "
            "and print triple and provision counts."
        ),
    )
    parser.add_argument(
        "name",
        help="INDEX slug, title substring, or registry abbreviation",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Corpus root (directory that contains krr_outputs/INDEX.json)",
    )
    args = parser.parse_args(argv)
    try:
        graph = load_law(args.name, root=args.root)
    except (LawNotFoundError, FileNotFoundError, OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"triples: {len(graph)}")
    print(f"provisions: {len(provisions_of(graph))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
