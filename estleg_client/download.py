"""Download a tagged corpus release so ``pip install estleg-client`` works without a clone.

``fetch_corpus()`` fetches ``SHA256SUMS`` and the requested assets from the
GitHub Release of ``henrikaavik/estonian-legal-ontology``, verifies every
asset's SHA-256 against ``SHA256SUMS`` (fail closed: an unlisted or mismatching
required asset aborts), extracts the gzipped JSON-LD aggregates into the
``krr_outputs/`` layout the loaders expect, cuts per-law shards from
``combined_ontology.jsonld``, and writes an ``estleg_corpus.json`` manifest
that makes :func:`estleg_client.corpus_root` discover the tree.

Integrity note: ``SHA256SUMS`` comes from the same release as the assets, so
the check catches truncated or corrupted downloads and stale cache files; it
does not authenticate the publisher beyond HTTPS to github.com. Pass
``expected_sums`` (asset name -> SHA-256) to pin hashes you obtained elsewhere.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from estleg_client._corpus import KRR, MANIFEST_NAME, default_corpus_dir
from estleg_client._shards import build_law_shards

__all__ = [
    "ASSET_LAYOUT",
    "DEFAULT_ASSETS",
    "DEFAULT_VERSION",
    "OPTIONAL_ASSETS",
    "RELEASE_BASE_URL",
    "DownloadError",
    "fetch_corpus",
    "parse_sha256sums",
]

DEFAULT_VERSION = "v1.0.0"
RELEASE_BASE_URL = "https://github.com/henrikaavik/estonian-legal-ontology/releases/download"
SUMS_NAME = "SHA256SUMS"
USER_AGENT = "estleg-client (+https://github.com/henrikaavik/estonian-legal-ontology)"
_CHUNK = 1 << 20

#: Where each extracted asset lands, relative to ``dest`` (``.gz`` is stripped
#: before lookup). Other plain assets (LICENSE, NOTICE, ...) are copied to
#: ``dest/<name>``; other ``.gz`` assets stay compressed in ``dest/downloads/``.
ASSET_LAYOUT: Mapping[str, str] = {
    "combined_ontology.jsonld": f"{KRR}/combined_ontology.jsonld",
    "eelnoud_combined.jsonld": f"{KRR}/eelnoud/eelnoud_combined.jsonld",
    "eurlex_combined.jsonld": f"{KRR}/eurlex/eurlex_combined.jsonld",
    "curia_combined.jsonld": f"{KRR}/curia/curia_combined.jsonld",
    "oiguskantsler_seisukohad.jsonld": f"{KRR}/annotations/oiguskantsler_seisukohad.jsonld",
    "INDEX.json": f"{KRR}/INDEX.json",
    "controlled_vocabulary.jsonld": f"{KRR}/controlled_vocabulary.jsonld",
}

#: Required by default: enacted laws, drafts and EU acts (the corpora a
#: release carries as aggregates). Missing from ``SHA256SUMS`` -> error.
DEFAULT_ASSETS: tuple[str, ...] = (
    "combined_ontology.jsonld.gz",
    "eelnoud_combined.jsonld.gz",
    "eurlex_combined.jsonld.gz",
)

#: Fetched whenever the release lists them (skipped silently otherwise): the
#: INDEX and the licence / data-rights / personal-data notices that must travel
#: with the data (#684).
OPTIONAL_ASSETS: tuple[str, ...] = (
    "INDEX.json",
    "controlled_vocabulary.jsonld",
    "LICENSE",
    "NOTICE",
    "DATA_RIGHTS.md",
    "DATA_PROTECTION.md",
)

Progress = Callable[[str], None]


class DownloadError(RuntimeError):
    """A release asset could not be fetched, verified or extracted."""


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse ``<sha256>  <name>`` lines (``*name`` binary marker tolerated)."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise DownloadError(f"malformed {SUMS_NAME} line: {line!r}")
        digest, name = parts[0].lower(), parts[1].strip().lstrip("*")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise DownloadError(f"malformed SHA-256 in {SUMS_NAME}: {line!r}")
        sums[name] = digest
    return sums


def _check_name(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or len(path.parts) != 1
            or name in {".", ".."} or "\\" in name or ":" in name):
        raise DownloadError(f"refusing unsafe asset name {name!r}")
    return name


def _asset_url(base_url: str, version: str, name: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(version, safe='')}/{quote(name, safe='')}"


def _download(url: str, target: Path, timeout: float) -> str:
    """Stream ``url`` to ``target`` (atomic rename); return its SHA-256."""
    scheme = urlparse(url).scheme
    if scheme not in {"https", "http", "file"}:
        raise DownloadError(f"unsupported URL scheme in {url!r}")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    partial = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(request, timeout=timeout) as response, partial.open("wb") as out:
            status = getattr(response, "status", 200)
            if status is not None and status >= 400:
                raise DownloadError(f"HTTP {status} for {url}")
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
    except DownloadError:
        partial.unlink(missing_ok=True)
        raise
    except OSError as exc:  # URLError / HTTPError / socket errors are OSErrors
        partial.unlink(missing_ok=True)
        raise DownloadError(f"could not download {url}: {exc}") from exc
    partial.replace(target)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within(base: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _extract(asset: Path, dest: Path) -> list[str]:
    """Place one verified asset into the corpus layout; return written paths."""
    name = asset.name
    if name.endswith((".tar.gz", ".tgz")):
        target_dir = dest / KRR
        target_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(asset, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                if not (member.isfile() or member.isdir()):
                    raise DownloadError(f"{name}: refusing non-regular member {member.name!r}")
                if not _within(target_dir, target_dir / member.name):
                    raise DownloadError(f"{name}: member escapes the corpus: {member.name!r}")
            if hasattr(tarfile, "data_filter"):
                archive.extractall(target_dir, members=members, filter="data")
            else:  # pragma: no cover - Python < 3.11.4
                archive.extractall(target_dir, members=members)
        return [f"{KRR}/{m.name}" for m in members if m.isfile()]
    stripped = name[: -len(".gz")] if name.endswith(".gz") else name
    rel = ASSET_LAYOUT.get(stripped)
    if name.endswith(".gz") and rel is not None:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        with gzip.open(asset, "rb") as source, tmp.open("wb") as out:
            shutil.copyfileobj(source, out, _CHUNK)
        tmp.replace(target)
        return [rel]
    if rel is None and name.endswith(".gz"):
        return []  # e.g. estleg_all.nq.gz: kept compressed under downloads/
    rel = rel or name
    target = dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(asset, target)
    return [rel]


def _stderr_progress(message: str) -> None:
    print(message, file=sys.stderr)


def fetch_corpus(
    dest: str | Path | None = None,
    version: str = DEFAULT_VERSION,
    assets: Iterable[str] = DEFAULT_ASSETS,
    *,
    optional_assets: Iterable[str] = OPTIONAL_ASSETS,
    base_url: str = RELEASE_BASE_URL,
    expected_sums: Mapping[str, str] | None = None,
    force: bool = False,
    build_shards: bool = True,
    timeout: float = 60.0,
    progress: Progress | None = _stderr_progress,
) -> Path:
    """Download, verify and unpack a corpus release; return the corpus root.

    ``dest`` defaults to ``<user cache>/estleg/corpus/<version>`` (see
    :func:`estleg_client.default_cache_dir`), which :func:`corpus_root` finds
    without configuration; for any other ``dest`` set ``ESTLEG_CORPUS_ROOT`` or
    pass ``root=dest`` to the loaders.

    ``assets`` must all be listed in the release's ``SHA256SUMS``;
    ``optional_assets`` are fetched only when listed. Files already present
    with the right hash are not downloaded again unless ``force=True``.
    """
    root = Path(dest).expanduser() if dest is not None else default_corpus_dir(version)
    root.mkdir(parents=True, exist_ok=True)
    existing_manifest = root / MANIFEST_NAME
    if existing_manifest.is_file():
        existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
        if existing.get("version") != version:
            raise DownloadError("Destination contains a different corpus version; use a fresh directory")
    downloads = root / "downloads"
    say = progress or (lambda _message: None)

    if isinstance(assets, str):
        assets = (assets,)
    if isinstance(optional_assets, str):
        optional_assets = (optional_assets,)
    required = [_check_name(name) for name in dict.fromkeys(assets)]
    optional = [
        _check_name(name) for name in dict.fromkeys(optional_assets) if name not in required
    ]

    sums_path = downloads / SUMS_NAME
    say(f"estleg: fetching {SUMS_NAME} for {version}")
    _download(_asset_url(base_url, version, SUMS_NAME), sums_path, timeout)
    sums = parse_sha256sums(sums_path.read_text(encoding="utf-8"))
    if expected_sums:
        for name, digest in expected_sums.items():
            listed = sums.get(name)
            if listed is not None and listed != digest.lower():
                raise DownloadError(
                    f"{SUMS_NAME} lists {listed} for {name}, expected_sums pins {digest}"
                )
            sums[name] = digest.lower()

    unlisted = [name for name in required if name not in sums]
    if unlisted:
        raise DownloadError(
            f"release {version} does not list {', '.join(unlisted)} in {SUMS_NAME}; "
            f"available: {', '.join(sorted(sums)) or 'nothing'}"
        )
    wanted = required + [name for name in optional if name in sums]
    skipped = [name for name in optional if name not in sums]

    records: dict[str, dict[str, object]] = {}
    for name in wanted:
        target = downloads / name
        expected = sums[name]
        if not force and target.is_file() and _sha256(target) == expected:
            say(f"estleg: {name} already verified")
        else:
            say(f"estleg: downloading {name}")
            actual = _download(_asset_url(base_url, version, name), target, timeout)
            if actual != expected:
                target.unlink(missing_ok=True)
                raise DownloadError(
                    f"SHA-256 mismatch for {name}: got {actual}, {SUMS_NAME} says {expected}"
                )
        records[name] = {"sha256": expected, "bytes": target.stat().st_size}

    # Verify every download, decompress and build shards before replacing any
    # served corpus file. A corrupt later asset must not mix two snapshots.
    with tempfile.TemporaryDirectory(prefix=".estleg-stage-", dir=root) as temporary:
        stage = Path(temporary)
        for name in wanted:
            records[name]["placed"] = _extract(downloads / name, stage)
        shard_index = None
        if build_shards and (stage / KRR / "combined_ontology.jsonld").is_file():
            say("estleg: indexing enacted laws (one pass over combined_ontology.jsonld)")
            index = build_law_shards(stage / KRR)
            shard_index = str(index.relative_to(stage)) if index else None
        staged_files = sorted(p for p in stage.rglob("*") if p.is_file())
        for path in staged_files:
            if not _within(root, root / path.relative_to(stage)):
                raise DownloadError("destination symlink escapes the corpus")
        for path in staged_files:
            target = root / path.relative_to(stage)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)

    manifest = {
        "version": version,
        "source": f"{base_url.rstrip('/')}/{version}/",
        "fetched_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "assets": records,
        "skipped_optional_assets": skipped,
        "law_shards": shard_index,
    }
    (root / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    say(f"estleg: corpus {version} ready at {root}")
    return root
