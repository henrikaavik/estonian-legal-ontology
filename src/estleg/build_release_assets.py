#!/usr/bin/env python3
"""Build, hash and catalogue every release asset in one step (#705).

The last step of the integration DAG. It runs after the combined rebuild
(``build_release_artifacts.py``) and the analytical overlay, and turns the
committed corpus into the files a GitHub Release carries:

1. **Version check.** Every ``COMBINED_JSONLD_TARGETS`` head must carry
   ``owl:versionInfo`` = ``ONTOLOGY_VERSION``; a missing one fails the step
   unless ``--allow-unstamped``. The check is read-only: the combined build
   has already read these files, so stamping them here would leave
   ``combined_ontology.jsonld`` (and the sub-corpus rebuild parity) stale.
   Producers stamp the head; ``stamp_combined_dataset_heads.py`` is the
   hand-run repair.
2. **RDF dumps.** ``krr_outputs/combined_ontology.{nt,nq,ttl}`` are
   regenerated from ``combined_ontology.jsonld`` in one bounded-memory pass
   (``serialize_corpus.serialize_jsonld_streaming``), then gzipped into the
   release directory. ``estleg_all.nq.gz`` (the seven-graph #474 dump) is
   written fail-closed by ``serialize_named_graphs.write_full_dump``.
3. **Retrieval chunks.** ``chunks.jsonl`` from
   ``generate_retrieval_projection`` (``--chunks-only`` into a scratch dir
   under the release directory), gzipped to ``chunks.jsonl.gz``.
4. **Combined dumps.** The four combined JSON-LD files advertised in
   ``metadata.jsonld`` plus the annotations layer, gzipped.
5. **Small assets.** INDEX, controlled vocabulary, SHACL shapes, VoID,
   dataset build manifest, LICENSE / NOTICE / data-rights / data-protection
   notices, copied verbatim.
6. **Catalogue.** ``dcat:byteSize`` and ``spdx:checksum`` (SHA-256) on every
   ``metadata.jsonld`` distribution whose ``dcat:downloadURL`` names a built
   asset; the updated ``metadata.jsonld`` is then copied in as an asset.
7. **SHA256SUMS** in the format of the hand-made v1.0.0 file
   (``<sha256>  <name>``, byte-sorted by name) over every top-level asset,
   plus ``release_assets.json`` recording sources, sizes and skipped parts.

Output goes to ``release/`` at the repository root (gitignored; kept out of
``krr_outputs/`` so the corpus file walkers never count the copies); only
``metadata.jsonld`` and the regenerated LFS ``combined_ontology.{nt,nq,ttl}``
touch tracked files. Gzip output is
deterministic (``mtime=0``, no embedded filename). Known generated assets are
cleared after input checks; unrelated files are preserved. ``SHA256SUMS``
lists only assets built by this run.

    python3 scripts/build_release_assets.py
    python3 scripts/build_release_assets.py --skip-rdf-dumps --skip-chunks
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    ONTOLOGY_VERSION,
    REPO_ROOT,
    save_json,
)

# Outside krr_outputs/ on purpose: every corpus walker
# (estleg_common.iter_krr_jsonld_files, the metadata file counts, validate_all)
# counts each *.json / *.jsonld under krr_outputs/, and the release copies of
# INDEX.json / metadata.jsonld / controlled_vocabulary.jsonld would be counted
# and validated as duplicate corpus files.
RELEASE_DIR = REPO_ROOT / "release"
METADATA_PATH = REPO_ROOT / "metadata.jsonld"
SUMS_NAME = "SHA256SUMS"
ASSET_MANIFEST_NAME = "release_assets.json"
SCRATCH_DIRS = ("retrieval",)
COMBINED_JSONLD = KRR_DIR / "combined_ontology.jsonld"
RDF_DUMP_FORMATS = ("nt", "nq", "ttl")
SPDX_NS = "http://spdx.org/rdf/terms#"
GZIP_CHUNK = 1 << 20

# (repo-relative source, asset name). The four combined dumps are the ones
# metadata.jsonld advertises as release downloads; the annotations layer was
# on the v1.0.0 release as well.
GZIP_ASSETS: tuple[tuple[str, str], ...] = (
    ("krr_outputs/combined_ontology.jsonld", "combined_ontology.jsonld.gz"),
    ("krr_outputs/eelnoud/eelnoud_combined.jsonld", "eelnoud_combined.jsonld.gz"),
    ("krr_outputs/eurlex/eurlex_combined.jsonld", "eurlex_combined.jsonld.gz"),
    ("krr_outputs/curia/curia_combined.jsonld", "curia_combined.jsonld.gz"),
    (
        "krr_outputs/annotations/oiguskantsler_seisukohad.jsonld",
        "oiguskantsler_seisukohad.jsonld.gz",
    ),
)
# The regenerated dumps live under release/rdf/ (gitignored); the committed
# krr_outputs/combined_ontology.{nt,nq,ttl} (LFS) are never overwritten by the
# release step -- refreshing those is a separate, deliberate commit (#705).
RDF_SCRATCH_SUBDIR = "rdf"
RDF_DUMP_ASSETS: tuple[tuple[str, str], ...] = tuple(
    (f"release/{RDF_SCRATCH_SUBDIR}/combined_ontology.{fmt}", f"combined_ontology.{fmt}.gz")
    for fmt in RDF_DUMP_FORMATS
)
COPY_ASSETS: tuple[tuple[str, str], ...] = (
    ("krr_outputs/INDEX.json", "INDEX.json"),
    ("krr_outputs/controlled_vocabulary.jsonld", "controlled_vocabulary.jsonld"),
    ("shacl/estonian_legal_shapes.ttl", "estonian_legal_shapes.ttl"),
    ("krr_outputs/void.ttl", "void.ttl"),
    ("krr_outputs/dataset_build_manifest.json", "dataset_build_manifest.json"),
    ("LICENSE", "LICENSE"),
    ("NOTICE", "NOTICE"),
    ("docs/DATA_RIGHTS.md", "DATA_RIGHTS.md"),
    ("docs/DATA_PROTECTION.md", "DATA_PROTECTION.md"),
)
NAMED_GRAPH_ASSET = "estleg_all.nq.gz"
CHUNKS_ASSET = "chunks.jsonl.gz"
METADATA_ASSET = "metadata.jsonld"


def required_asset_names() -> set[str]:
    return {name for _, name in (*GZIP_ASSETS, *COPY_ASSETS, *RDF_DUMP_ASSETS)} | {
        NAMED_GRAPH_ASSET, CHUNKS_ASSET, METADATA_ASSET,
    }


class ReleaseAssetError(RuntimeError):
    """A required release input is missing or unusable."""


@dataclass
class Asset:
    name: str
    source: str
    producer: str
    bytes: int = 0
    sha256: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "producer": self.producer,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }


@dataclass
class BuildResult:
    assets: list[Asset] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    stamped: list[str] = field(default_factory=list)  # heads carrying the version
    unstamped: list[str] = field(default_factory=list)
    rdf_dumps: dict[str, dict] = field(default_factory=dict)
    named_graph_counts: dict[str, int] = field(default_factory=dict)
    chunk_stats: dict = field(default_factory=dict)
    catalogued: list[str] = field(default_factory=list)
    catalogue_warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(GZIP_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(40).startswith(b"version https://git-lfs.github.com/spec")
    except OSError:
        return False


def _require_source(path: Path) -> None:
    if not path.is_file():
        raise ReleaseAssetError(f"missing release input: {_rel(path)}")
    if _is_lfs_pointer(path):
        raise ReleaseAssetError(
            f"{_rel(path)} is a Git LFS pointer; run `git lfs pull` first"
        )


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def gzip_deterministic(source: Path, dest: Path) -> None:
    """Gzip ``source`` to ``dest`` with no mtime and no embedded filename."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with source.open("rb") as src, tmp.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
                shutil.copyfileobj(src, gz, GZIP_CHUNK)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def clean_release_dir(release_dir: Path) -> None:
    """Remove known generated files, preserving unrelated user files."""
    release_dir.mkdir(parents=True, exist_ok=True)
    for name in required_asset_names() | {SUMS_NAME, ASSET_MANIFEST_NAME}:
        path = release_dir / name
        if path.is_file() or path.is_symlink():
            path.unlink()


def _record(result: BuildResult, release_dir: Path, name: str, source: str, producer: str) -> Asset:
    path = release_dir / name
    asset = Asset(name=name, source=source, producer=producer)
    asset.bytes = path.stat().st_size
    asset.sha256 = sha256_file(path)
    result.assets = [a for a in result.assets if a.name != name]
    result.assets.append(asset)
    return asset


# ---------------------------------------------------------------------------
# Sub-steps
# ---------------------------------------------------------------------------


def check_versions(krr_dir: Path) -> tuple[list[str], list[str]]:
    """Check ``owl:versionInfo`` == ``ONTOLOGY_VERSION`` on every combined head.

    Returns ``(stamped relpaths, unstamped relpaths)``. Read-only: the heads are
    written by their producers (``stamp_combined_dataset_head``) before the
    combined build reads them, so the package step must not rewrite a file an
    earlier step already consumed. ``scripts/stamp_combined_dataset_heads.py``
    is the hand-run repair for a producer that does not stamp the version yet.
    """
    from estleg.estleg_common import COMBINED_JSONLD_TARGETS
    from estleg.stamp_combined_dataset_heads import head_version, is_lfs_pointer

    stamped: list[str] = []
    unstamped: list[str] = []
    for spec in COMBINED_JSONLD_TARGETS:
        relpath = str(spec["relpath"])
        path = krr_dir / relpath
        if not path.is_file():
            raise ReleaseAssetError(f"combined target missing: {relpath}")
        if is_lfs_pointer(path):
            raise ReleaseAssetError(f"combined target is an LFS pointer: {relpath}")
        (stamped if head_version(path) == ONTOLOGY_VERSION else unstamped).append(relpath)
    return stamped, unstamped


def build_rdf_dumps(krr_dir: Path, *, batch_size: int, out_dir: Path) -> dict[str, dict]:
    """Regenerate ``combined_ontology.{nt,nq,ttl}`` into ``out_dir`` in one streamed pass.

    ``out_dir`` is the release scratch directory, not ``krr_dir``: the committed
    LFS dumps stay untouched (#705).
    """
    from estleg.serialize_corpus import serialize_jsonld_streaming

    source = krr_dir / COMBINED_JSONLD.name
    _require_source(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {fmt: out_dir / f"combined_ontology.{fmt}" for fmt in RDF_DUMP_FORMATS}
    result = serialize_jsonld_streaming(source, outputs, batch_size=batch_size)
    return {
        fmt: {"path": _rel(info["path"]), "bytes": info["bytes"]}
        for fmt, info in result["outputs"].items()
    } | {"triples": result["triples"]}


def build_named_graphs(
    krr_dir: Path, dest: Path, *, laws_nq: Path | None = None
) -> dict[str, int]:
    """Seven-graph ``estleg_all.nq.gz``; the laws slot reads ``laws_nq`` when given."""
    from estleg.serialize_named_graphs import write_full_dump

    overrides = {"combined_ontology.nq": laws_nq} if laws_nq is not None else None
    return write_full_dump(krr_dir=krr_dir, dest=dest, source_overrides=overrides)



def build_chunks(krr_dir: Path, scratch: Path, dest: Path) -> dict:
    from estleg.generate_retrieval_projection import generate

    stats = generate(
        krr_dir=krr_dir,
        out_dir=scratch,
        eval_date=BUILD_EVALUATION_DATE,
        chunks_only=True,
    )
    gzip_deterministic(scratch / "chunks.jsonl", dest)
    return stats


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def _iri(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("@id")
    return value if isinstance(value, str) else None


def checksum_node(sha256: str) -> dict:
    return {
        "@type": "spdx:Checksum",
        "spdx:algorithm": {"@id": "spdx:checksumAlgorithm_sha256"},
        "spdx:checksumValue": {"@value": sha256, "@type": "xsd:hexBinary"},
    }


def byte_size_literal(size: int) -> dict:
    return {"@value": str(size), "@type": "xsd:nonNegativeInteger"}


def update_catalogue(
    metadata: dict, assets: dict[str, Asset], version: str = ONTOLOGY_VERSION
) -> tuple[list[str], list[str]]:
    """Stamp byteSize/checksum on distributions that download a built asset.

    Matches on the basename of ``dcat:downloadURL`` (falling back to
    ``dcat:accessURL``). Returns ``(catalogued asset names, warnings)``; a
    warning is raised for a release-download URL whose tag is not
    ``v<version>`` (the published bytes would then belong to another release)
    and for a release-download URL whose asset was not built this run.
    """
    context = metadata.setdefault("@context", {})
    if isinstance(context, dict):
        context.setdefault("spdx", SPDX_NS)
    catalogued: list[str] = []
    warnings: list[str] = []
    for dist in metadata.get("dcat:distribution") or []:
        if not isinstance(dist, dict):
            continue
        url = _iri(dist.get("dcat:downloadURL")) or _iri(dist.get("dcat:accessURL"))
        if not url:
            continue
        path = urlparse(url).path
        name = path.rsplit("/", 1)[-1]
        is_release = "/releases/download/" in path
        asset = assets.get(name)
        if asset is None:
            dist.pop("dcat:byteSize", None)
            dist.pop("spdx:checksum", None)
            if is_release:
                warnings.append(f"{name}: release download advertised but not built")
            continue
        if is_release and f"/releases/download/v{version}/" not in path:
            warnings.append(
                f"{name}: downloadURL tag is not v{version}; the published asset "
                "will not match this checksum until the URL is updated"
            )
            continue  # Preserve that release's metadata; these bytes are different.
        dist["dcat:byteSize"] = byte_size_literal(asset.bytes)
        dist["spdx:checksum"] = checksum_node(asset.sha256)
        catalogued.append(name)
    return catalogued, warnings


def write_sums(release_dir: Path, assets: list[Asset]) -> Path:
    """Write ``SHA256SUMS`` (``<sha256>  <name>``, byte-sorted by name)."""
    lines = [
        f"{asset.sha256}  {asset.name}\n"
        for asset in sorted(assets, key=lambda a: a.name.encode("utf-8"))
    ]
    path = release_dir / SUMS_NAME
    path.write_text("".join(lines), encoding="utf-8")
    return path


def parse_sums(path: Path) -> dict[str, str]:
    """``{name: sha256}`` from a ``SHA256SUMS`` file."""
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, sep, name = line.partition("  ")
        if (not sep or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
                or not name or Path(name).name != name or name in {".", ".."}
                or name in entries):
            raise ValueError(f"malformed {path.name} line: {line!r}")
        entries[name] = digest
    return entries


def verify_sums(release_dir: Path, *, require_complete: bool = False) -> dict:
    """Re-hash every asset listed in ``SHA256SUMS``.

    ``require_complete`` also requires the full release inventory and a
    current manifest without skipped producers or unstamped heads.

    Returns ``{"files": {Path: sha}, "missing": [Path], "mismatched": [Path]}``
    (absolute paths; callers render them relative to their own root).
    """
    sums_path = release_dir / SUMS_NAME
    if not sums_path.is_file():
        return {"files": {}, "missing": [sums_path], "mismatched": []}
    files: dict[Path, str] = {}
    missing: list[Path] = []
    mismatched: list[Path] = []
    try:
        entries = parse_sums(sums_path)
    except (ValueError, OSError):
        return {"files": files, "missing": missing, "mismatched": [sums_path]}
    if not entries:
        mismatched.append(sums_path)
    if require_complete:
        missing.extend(release_dir / name for name in sorted(required_asset_names() - entries.keys()))
        manifest_path = release_dir / ASSET_MANIFEST_NAME
        if not manifest_path.is_file():
            missing.append(manifest_path)
        else:
            files[manifest_path] = sha256_file(manifest_path)
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (not isinstance(manifest, dict) or manifest.get("skipped")
                        or manifest.get("unstampedHeads")
                        or manifest.get("ontologyVersion") != ONTOLOGY_VERSION):
                    mismatched.append(manifest_path)
            except (ValueError, OSError):
                mismatched.append(manifest_path)
    for name, expected in sorted(entries.items()):
        path = release_dir / name
        if not path.is_file():
            missing.append(path)
            continue
        actual = sha256_file(path)
        files[path] = actual
        if actual != expected.lower():
            mismatched.append(path)
    return {"files": files, "missing": missing, "mismatched": mismatched}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _timed(result: BuildResult, label: str, func: Callable[[], object]) -> object:
    start = time.perf_counter()
    value = func()
    result.timings[label] = round(time.perf_counter() - start, 1)
    return value


def build_release_assets(
    *,
    krr_dir: Path = KRR_DIR,
    release_dir: Path = RELEASE_DIR,
    repo_root: Path = REPO_ROOT,
    metadata_path: Path = METADATA_PATH,
    skip_rdf_dumps: bool = False,
    skip_named_graphs: bool = False,
    skip_chunks: bool = False,
    allow_unstamped: bool = False,
    batch_size: int = 5000,
) -> BuildResult:
    """Run every sub-step; raise :class:`ReleaseAssetError` on a missing input."""
    result = BuildResult()

    def source_path(relpath: str) -> Path:
        if relpath.startswith("krr_outputs/"):
            return krr_dir / relpath.removeprefix("krr_outputs/")
        return repo_root / relpath

    sources = [source_path(rel) for rel, _ in (*GZIP_ASSETS, *COPY_ASSETS)] + [metadata_path]
    if any(p.resolve().is_relative_to(release_dir.resolve()) for p in sources):
        raise ReleaseAssetError("release directory contains release inputs")
    for source in sources:
        _require_source(source)

    result.stamped, result.unstamped = _timed(result, "version-check", lambda: check_versions(krr_dir))
    if result.unstamped and not allow_unstamped:
        raise ReleaseAssetError(
            f"owl:versionInfo != {ONTOLOGY_VERSION} on combined head(s) "
            f"{', '.join(result.unstamped)}; their producers must stamp the version "
            "(estleg_common.apply_inband_dataset_fields), or run "
            "scripts/stamp_combined_dataset_heads.py before the combined build. "
            "Pass --allow-unstamped to package anyway."
        )
    clean_release_dir(release_dir)

    if skip_rdf_dumps:
        result.skipped["rdf-dumps"] = "--skip-rdf-dumps"
    else:
        rdf_dir = release_dir / RDF_SCRATCH_SUBDIR
        result.rdf_dumps = _timed(
            result,
            "rdf-dumps",
            lambda: build_rdf_dumps(krr_dir, batch_size=batch_size, out_dir=rdf_dir),
        )
        for relpath, name in RDF_DUMP_ASSETS:
            # The dumps were just written under release_dir/rdf (never over the
            # committed LFS copies); relpath only documents the producer path.
            source = rdf_dir / Path(relpath).name
            _timed(result, f"gzip {name}", lambda s=source, n=name: gzip_deterministic(s, release_dir / n))
            _record(result, release_dir, name, relpath, "serialize_corpus --stream")

    if skip_named_graphs or skip_rdf_dumps:
        result.skipped["named-graphs"] = (
            "--skip-named-graphs" if skip_named_graphs
            else "--skip-rdf-dumps (the laws slot needs a fresh combined_ontology.nq)"
        )
    else:
        result.named_graph_counts = _timed(
            result,
            "named-graphs",
            lambda: build_named_graphs(
                krr_dir,
                release_dir / NAMED_GRAPH_ASSET,
                laws_nq=release_dir / RDF_SCRATCH_SUBDIR / "combined_ontology.nq",
            ),
        )
        _record(result, release_dir, NAMED_GRAPH_ASSET, "serialize_named_graphs.SLOTS",
                "serialize_named_graphs.write_full_dump")

    if skip_chunks:
        result.skipped["chunks"] = "--skip-chunks"
    else:
        scratch = release_dir / "retrieval"
        result.chunk_stats = _timed(
            result,
            "chunks",
            lambda: build_chunks(krr_dir, scratch, release_dir / CHUNKS_ASSET),
        )
        _record(result, release_dir, CHUNKS_ASSET, _rel(scratch / "chunks.jsonl"),
                "generate_retrieval_projection --chunks-only")

    for relpath, name in GZIP_ASSETS:
        source = source_path(relpath)
        _require_source(source)
        _timed(result, f"gzip {name}", lambda s=source, n=name: gzip_deterministic(s, release_dir / n))
        _record(result, release_dir, name, relpath, "gzip (mtime=0)")

    for relpath, name in COPY_ASSETS:
        source = source_path(relpath)
        _require_source(source)
        shutil.copyfile(source, release_dir / name)
        _record(result, release_dir, name, relpath, "copy")

    _require_source(metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    by_name = {asset.name: asset for asset in result.assets}
    result.catalogued, result.catalogue_warnings = update_catalogue(metadata, by_name)
    save_json(metadata_path, metadata)
    shutil.copyfile(metadata_path, release_dir / METADATA_ASSET)
    _record(result, release_dir, METADATA_ASSET, _rel(metadata_path), "copy (after catalogue update)")

    write_sums(release_dir, result.assets)
    manifest = {
        "ontologyVersion": ONTOLOGY_VERSION,
        "evaluationDate": BUILD_EVALUATION_DATE,
        "sums": SUMS_NAME,
        "assets": [a.as_dict() for a in sorted(result.assets, key=lambda a: a.name)],
        "skipped": result.skipped,
        "stamped": result.stamped,
        "unstampedHeads": result.unstamped,
        "rdfDumps": result.rdf_dumps,
        "namedGraphs": result.named_graph_counts,
        "catalogued": result.catalogued,
        "catalogueWarnings": result.catalogue_warnings,
    }
    save_json(release_dir / ASSET_MANIFEST_NAME, manifest)
    return result


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def print_summary(result: BuildResult, release_dir: Path) -> None:
    print("=" * 70)
    print(f"Release assets — ontology {ONTOLOGY_VERSION} -> {_rel(release_dir)}")
    print("=" * 70)
    for asset in sorted(result.assets, key=lambda a: a.name):
        print(f"  {asset.name:38s} {_human(asset.bytes):>10s}  {asset.sha256[:12]}  <- {asset.source}")
    if result.stamped:
        print(f"\n  versionInfo={ONTOLOGY_VERSION} present on: {', '.join(result.stamped)}")
    for relpath in result.unstamped:
        print(f"  WARNING: {relpath} head lacks owl:versionInfo={ONTOLOGY_VERSION} "
              "(--allow-unstamped)")
    if result.rdf_dumps:
        print(f"  RDF dumps: {json.dumps(result.rdf_dumps)}")
    if result.named_graph_counts:
        for iri, n in sorted(result.named_graph_counts.items()):
            print(f"  named graph {iri}: {n} quads")
    if result.chunk_stats:
        print(f"  chunks: {result.chunk_stats.get('total_chunks')} records")
    print(f"  catalogued in metadata.jsonld: {', '.join(result.catalogued) or '(none)'}")
    for warning in result.catalogue_warnings:
        print(f"  WARNING: {warning}")
    for part, reason in result.skipped.items():
        print(f"  SKIPPED {part}: {reason}")
    print(f"  timings (s): {json.dumps(result.timings)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--release-dir", type=Path, default=RELEASE_DIR)
    parser.add_argument("--skip-rdf-dumps", action="store_true",
                        help="Do not regenerate combined_ontology.{nt,nq,ttl} "
                        "(also skips estleg_all.nq.gz, whose laws slot reads the .nq).")
    parser.add_argument("--skip-named-graphs", action="store_true",
                        help="Do not write estleg_all.nq.gz.")
    parser.add_argument("--skip-chunks", action="store_true",
                        help="Do not build retrieval chunks.jsonl.gz.")
    parser.add_argument("--allow-unstamped", action="store_true",
                        help="Package even when a combined head lacks "
                        "owl:versionInfo=ONTOLOGY_VERSION (recorded in "
                        "release_assets.json).")
    parser.add_argument("--batch-size", type=int, default=5000,
                        help="Nodes per batch for the streamed RDF dumps.")
    args = parser.parse_args(argv)
    try:
        result = build_release_assets(
            release_dir=args.release_dir,
            skip_rdf_dumps=args.skip_rdf_dumps,
            skip_named_graphs=args.skip_named_graphs,
            skip_chunks=args.skip_chunks,
            allow_unstamped=args.allow_unstamped,
            batch_size=args.batch_size,
        )
    except ReleaseAssetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print_summary(result, args.release_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
