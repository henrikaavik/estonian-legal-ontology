#!/usr/bin/env python3
"""Build ``combined_ontology.jsonld`` and ``INDEX.json`` (#467).

Release path: only the two essential builders. Legacy one-time repair
passes stay importable from ``fix_all_issues`` and can be invoked via
``scripts/archive/legacy_repairs.py`` — they are not part of an ordinary
release.
"""

from __future__ import annotations

import argparse

from estleg import fix_all_issues as _fix

generate_combined_jsonld = _fix.generate_combined_jsonld
generate_index = _fix.generate_index
_merge_node_properties = _fix._merge_node_properties


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    print("=" * 60)
    print("Estonian Legal Ontology - release artifact build (#467)")
    print("=" * 60)
    generate_index()
    generate_combined_jsonld()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
