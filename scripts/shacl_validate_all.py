"""Full SHACL validation across the Layer 1 output corpus."""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KRR = REPO / "krr_outputs"
SHAPES = REPO / "shacl" / "estonian_legal_shapes.ttl"


def collect_files() -> list[Path]:
    out: list[Path] = [
        KRR / "municipalities_peep.json",
        KRR / "issuers_kov_peep.json",
    ]
    # Indexed law files (covers .json and .jsonld)
    idx = json.load(open(KRR / "INDEX.json"))
    for entry in idx["laws"]:
        for f in entry.get("files", []):
            p = KRR / f
            if p.exists():
                out.append(p)
    # All KOV acts (recursive)
    out += [
        Path(p)
        for p in glob.glob(str(KRR / "regulations/kov/**/*_peep.json"),
                            recursive=True)
        if not p.endswith("REGULATIONS_KOV_INDEX.json")
    ]
    # State regulations
    out += [Path(p) for p in glob.glob(str(KRR / "regulations/riik/*_peep.json"))]
    return sorted(set(out))


def main() -> int:
    import rdflib
    import pyshacl

    files = collect_files()
    print(f"Loading {len(files)} files into a single graph...")
    g = rdflib.Graph()
    parse_failures: list[tuple[str, str]] = []
    for i, f in enumerate(files):
        if i % 1000 == 0:
            print(f"  {i}/{len(files)}")
        try:
            g.parse(str(f), format="json-ld")
        except Exception as exc:
            parse_failures.append((str(f), str(exc)))

    if parse_failures:
        print(f"\nPARSE FAILURES: {len(parse_failures)}")
        for f, exc in parse_failures[:10]:
            print(f"  {f}: {exc}")
        return 2

    print(f"\nLoaded {len(g)} triples. Running pyshacl...")
    shapes = rdflib.Graph().parse(str(SHAPES), format="turtle")
    ok, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
    print("SHACL", "PASS" if ok else "FAIL")
    if not ok:
        print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
