#!/usr/bin/env python3
"""Emergency-only corpus repair passes (#467).

Do **not** run this on a routine release. Generators already emit the
shapes these passes used to repair. The release builder is
``scripts/build_release_artifacts.py``.
"""

from __future__ import annotations

import argparse
import sys

from estleg import fix_all_issues as _fix


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="required confirmation; refuses to rewrite the live corpus otherwise",
    )
    args = parser.parse_args(argv)
    if not args.yes:
        print(
            "Refusing to run emergency repairs without --yes. "
            "Use scripts/build_release_artifacts.py for a release build.",
            file=sys.stderr,
        )
        return 2
    print("=" * 60)
    print("Estonian Legal Ontology - emergency legacy repairs (#467)")
    print("=" * 60)
    _fix.process_all_json_files()
    _fix.fix_intra_file_duplicates()
    _fix.audit_duplicate_ids()
    _fix.fix_generator_script()
    _fix.fix_docs_namespace()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
