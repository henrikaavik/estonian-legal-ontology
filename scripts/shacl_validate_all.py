"""SHACL validation across ontology corpus buckets."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KRR = REPO / "krr_outputs"
SHAPES = REPO / "shacl" / "estonian_legal_shapes.ttl"


def collect_controlled_vocabulary(krr: Path = KRR) -> list[Path]:
    path = krr / "controlled_vocabulary.jsonld"
    return [path] if path.exists() else []


def collect_indexed_laws(krr: Path = KRR) -> list[Path]:
    out: list[Path] = []
    idx = json.load(open(krr / "INDEX.json"))
    for entry in idx.get("laws", []):
        for f in entry.get("files", []):
            p = krr / f
            if p.exists():
                out.append(p)
    return sorted(set(out))


def collect_laws(krr: Path = KRR) -> list[Path]:
    out = collect_indexed_laws(krr) + collect_controlled_vocabulary(krr)
    out.extend(sorted((krr / "regulations" / "riik").glob("*_peep.json")))
    return sorted(set(out))


def collect_kov(krr: Path = KRR) -> list[Path]:
    out = [
        krr / "municipalities_peep.json",
        krr / "issuers_kov_peep.json",
        *collect_controlled_vocabulary(krr),
    ]
    out.extend(sorted((krr / "regulations" / "kov").glob("**/*_peep.json")))
    return sorted({p for p in out if p.exists()})


def collect_riigikohus(krr: Path = KRR) -> list[Path]:
    return sorted(set(sorted((krr / "riigikohus").glob("*_peep.json")) + collect_controlled_vocabulary(krr)))


def collect_drafts(krr: Path = KRR) -> list[Path]:
    return sorted(set(sorted((krr / "eelnoud").glob("*_peep.json")) + collect_controlled_vocabulary(krr)))


def collect_eurlex(krr: Path = KRR) -> list[Path]:
    return sorted(set(sorted((krr / "eurlex").glob("*_peep.json")) + collect_controlled_vocabulary(krr)))


def collect_curia(krr: Path = KRR) -> list[Path]:
    return sorted(set(sorted((krr / "curia").glob("*_peep.json")) + collect_controlled_vocabulary(krr)))


BUCKETS: dict[str, Callable[[Path], list[Path]]] = {
    "laws": collect_laws,
    "kov": collect_kov,
    "riigikohus": collect_riigikohus,
    "drafts": collect_drafts,
    "eurlex": collect_eurlex,
    "curia": collect_curia,
}


def collect_files(bucket: str | None = None, *, all_buckets: bool = False, krr: Path = KRR) -> list[Path]:
    if all_buckets:
        out: list[Path] = []
        for collector in BUCKETS.values():
            out.extend(collector(krr))
        return sorted(set(out))
    if bucket is None:
        bucket = "laws"
    return BUCKETS[bucket](krr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket",
        choices=sorted(BUCKETS),
        help="Validate one corpus bucket instead of the full corpus.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate all corpus buckets in one graph. This is intended for release checks.",
    )
    parser.add_argument(
        "--list-buckets",
        action="store_true",
        help="List available buckets and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import rdflib
    import pyshacl

    args = parse_args(argv)
    if args.list_buckets:
        for name in sorted(BUCKETS):
            print(name)
        return 0

    all_buckets = args.all or args.bucket is None
    files = collect_files(args.bucket, all_buckets=all_buckets)
    label = "all buckets" if all_buckets else f"{args.bucket!r} bucket"
    print(f"Loading {len(files)} files from {label} into a single graph...")
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
