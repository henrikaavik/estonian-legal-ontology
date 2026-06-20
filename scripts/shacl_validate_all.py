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
    # ``data/ehak/historical_municipalities.jsonld`` (issue #130) lives
    # under the source KOV registry directory rather than ``krr_outputs/``
    # (to keep it out of the krr_outputs/ file-count statistics that are
    # validated against ``metadata.jsonld``), so it has to be listed here
    # explicitly — the ``regulations/kov/**`` glob below does not reach it.
    repo_root = krr.parent
    out = [
        krr / "municipalities_peep.json",
        krr / "issuers_kov_peep.json",
        repo_root / "data" / "ehak" / "historical_municipalities.jsonld",
        *collect_controlled_vocabulary(krr),
    ]
    out.extend(sorted((krr / "regulations" / "kov").glob("**/*_peep.json")))
    return sorted({p for p in out if p.exists()})


def collect_riigikohus(krr: Path = KRR) -> list[Path]:
    return sorted(set(sorted((krr / "riigikohus").glob("*_peep.json")) + collect_controlled_vocabulary(krr)))


def collect_drafts(krr: Path = KRR) -> list[Path]:
    return sorted(set(sorted((krr / "eelnoud").glob("*_peep.json")) + collect_controlled_vocabulary(krr)))


def collect_eurlex(krr: Path = KRR) -> list[Path]:
    # The EU-transposition harmonisation layer (issue #197) is the natural
    # home for these files: ``krr_outputs/harmonisation/harmonisation_by_directive/*.json``
    # carry ``estleg:HarmonisationLink`` nodes (validated by
    # ``HarmonisationLinkShape``). The sibling ``harmonisation_report.json``
    # (no ``@graph``) and ``harmonisation_schema.json`` (vocab declarations,
    # not shaped data) are deliberately excluded.
    harmonisation = sorted(
        (krr / "harmonisation" / "harmonisation_by_directive").glob("harm_*.json")
    )
    return sorted(
        set(
            sorted((krr / "eurlex").glob("*_peep.json"))
            + harmonisation
            + collect_controlled_vocabulary(krr)
        )
    )


def collect_curia(krr: Path = KRR) -> list[Path]:
    return sorted(set(sorted((krr / "curia").glob("*_peep.json")) + collect_controlled_vocabulary(krr)))


# Sidecar enrichment outputs live one level deep under ``krr_outputs/`` and
# carry their own SHACL target classes (``estleg:LegalConcept``,
# ``estleg:Sanction``, ``estleg:AmendmentEvent``, ``estleg:Institution`` plus
# ``estleg:Competence``, ``estleg:ProvisionVersion`` under ``provision_versions/``
# — issue #198, and ``estleg:Annotation`` under ``annotations/`` — issue #199).
# They are *not* ``*_peep.json`` files, so the other buckets miss them; without a
# bucket here those shaped classes have no validation path (issue #106).
SIDECAR_DIRS = ("concepts", "sanctions", "amendments", "institutions", "provision_versions", "annotations")
# Aggregate/report artifacts that sit alongside the shaped data but contain no
# ``@graph`` — exclude them so pyshacl is not handed plain summary JSON
# (e.g. ``concepts/concept_crossref_report.json``).
_SIDECAR_NON_DATA_SUFFIXES = ("_report", "_summary", "_index", "_review")


def _is_sidecar_data_file(path: Path) -> bool:
    stem = path.stem.lower()
    if stem.startswith("index") or stem.endswith("index"):
        return False
    return not any(stem.endswith(suffix) for suffix in _SIDECAR_NON_DATA_SUFFIXES)


def collect_sidecars(krr: Path = KRR) -> list[Path]:
    out: list[Path] = list(collect_controlled_vocabulary(krr))
    for name in SIDECAR_DIRS:
        base = krr / name
        if not base.exists():
            continue
        for pattern in ("**/*.json", "**/*.jsonld"):
            out.extend(p for p in base.glob(pattern) if _is_sidecar_data_file(p))
    return sorted(set(out))


BUCKETS: dict[str, Callable[[Path], list[Path]]] = {
    "laws": collect_laws,
    "kov": collect_kov,
    "riigikohus": collect_riigikohus,
    "drafts": collect_drafts,
    "eurlex": collect_eurlex,
    "curia": collect_curia,
    "sidecars": collect_sidecars,
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
    # A bucket collector that silently returns 0 files (missing/renamed
    # INDEX.json or corpus subdir) would otherwise validate an empty graph,
    # which pyshacl reports as conforming — turning a vanished corpus subset
    # into a green SHACL job. Treat 0 files as an error, mirroring the guard
    # in validate_seadusloome_sync.main (issue #338).
    #
    # #590: controlled_vocabulary.jsonld is appended to EVERY bucket, so a
    # vanished/renamed corpus subdir still yields exactly that one file and
    # slips past the guard. Exclude the always-present vocab file from the
    # emptiness test so the guard sees the actual corpus, not just the TBox.
    corpus_files = [f for f in files if f.name != "controlled_vocabulary.jsonld"]
    if not corpus_files:
        print(
            f"ERROR: no corpus files collected for {label} "
            "(only controlled_vocabulary.jsonld present — vanished/renamed bucket?)."
        )
        return 2
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
