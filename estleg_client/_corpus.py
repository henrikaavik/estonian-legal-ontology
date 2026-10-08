"""Locate the corpus: a git checkout or a tree written by ``fetch_corpus``.

A *corpus root* is a directory that holds either ``krr_outputs/INDEX.json``
(a clone of the repository, or a downloaded release that included the INDEX
asset) or the ``estleg_corpus.json`` manifest that ``fetch_corpus`` writes.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ENV_ROOT = "ESTLEG_CORPUS_ROOT"
#: Pre-1.0 spelling of :data:`ENV_ROOT`; still honoured.
LEGACY_ENV_ROOT = "ESTLEG_CORPUS"
ENV_CACHE = "ESTLEG_CACHE_DIR"
MANIFEST_NAME = "estleg_corpus.json"
KRR = "krr_outputs"
_INDEX_REL = (KRR, "INDEX.json")


class CorpusNotFoundError(FileNotFoundError):
    """No corpus could be located.

    Subclasses ``FileNotFoundError`` so pre-1.0 callers that caught that keep
    working; the message always names ``fetch_corpus`` as the remedy.
    """


class CorpusUnavailableError(LookupError):
    """The corpus root exists but lacks the files a loader needs.

    Raised, for example, when a release download (which ships aggregates, not
    the per-act peep files) is asked for a Riigikohus decision.
    """


def default_cache_dir() -> Path:
    """Per-user cache directory for downloaded corpora.

    ``$ESTLEG_CACHE_DIR`` wins; otherwise ``~/Library/Caches/estleg`` (macOS),
    ``%LOCALAPPDATA%\\estleg\\Cache`` (Windows) or ``$XDG_CACHE_HOME/estleg``
    (default ``~/.cache/estleg``).
    """
    env = os.environ.get(ENV_CACHE)
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "estleg"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / "estleg" / "Cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else home / ".cache") / "estleg"


def default_corpus_dir(version: str) -> Path:
    """Where ``fetch_corpus(version=...)`` writes when no ``dest`` is given."""
    return default_cache_dir() / "corpus" / version


def is_corpus_root(path: Path) -> bool:
    return path.is_dir() and (
        path.joinpath(*_INDEX_REL).is_file() or (path / MANIFEST_NAME).is_file()
    )


def _as_corpus_root(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    if is_corpus_root(path):
        return path
    if path.name == KRR and is_corpus_root(path.parent):
        return path.parent
    return None


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", name)) or (-1,)


def _cached_corpora() -> list[Path]:
    base = default_cache_dir() / "corpus"
    if not base.is_dir():
        return []
    found = [child for child in base.iterdir() if (child / MANIFEST_NAME).is_file()]
    return sorted(found, key=lambda p: _version_key(p.name), reverse=True)


def _not_found_message(detail: str) -> str:
    return (
        f"{detail} Download a release with "
        "`from estleg_client import fetch_corpus; fetch_corpus()` "
        "(or `estleg-load download`), pass root=..., or set "
        f"{ENV_ROOT} to a git checkout / fetch_corpus() directory."
    )


def corpus_root(root: str | Path | None = None) -> Path:
    """Return the corpus root directory.

    Resolution order:

    1. Explicit ``root`` (repository root, its ``krr_outputs`` directory, or a
       ``fetch_corpus`` destination).
    2. ``$ESTLEG_CORPUS_ROOT`` (or the legacy ``$ESTLEG_CORPUS``).
    3. Walk up from this package, then from the current working directory
       (finds a git checkout that has the client installed in editable mode).
    4. The newest corpus ``fetch_corpus`` placed in :func:`default_cache_dir`.

    Raises :class:`CorpusNotFoundError` (a ``FileNotFoundError``) naming
    ``fetch_corpus`` when nothing is found.
    """
    if root is not None:
        candidate = Path(root).expanduser().resolve()
        found = _as_corpus_root(candidate)
        if found is None:
            raise CorpusNotFoundError(
                _not_found_message(
                    f"{candidate} holds neither krr_outputs/INDEX.json nor {MANIFEST_NAME}."
                )
            )
        return found

    for env_name in (ENV_ROOT, LEGACY_ENV_ROOT):
        env = os.environ.get(env_name)
        if env:
            candidate = Path(env).expanduser().resolve()
            found = _as_corpus_root(candidate)
            if found is None:
                raise CorpusNotFoundError(
                    _not_found_message(
                        f"{env_name}={env!r} holds neither krr_outputs/INDEX.json "
                        f"nor {MANIFEST_NAME}."
                    )
                )
            return found

    here = Path(__file__).resolve()
    for start in (here, Path.cwd().resolve()):
        for candidate in (start, *start.parents):
            found = _as_corpus_root(candidate)
            if found is not None:
                return found

    cached = _cached_corpora()
    if cached:
        return cached[0]

    raise CorpusNotFoundError(
        _not_found_message("Could not locate the Estonian Legal Ontology corpus.")
    )


def krr_dir(root: Path) -> Path:
    return root / KRR
