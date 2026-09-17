"""Read-only loaders for enacted-law peeps and sanction sidecars.

Resolves a law name against ``krr_outputs/INDEX.json``, parses every listed
file (multi-osa assembly), and merges ``krr_outputs/sanctions/sanctions_<stem>.json``
when that sidecar exists. Producer scripts in ``scripts/`` are not imported.
"""

from __future__ import annotations

import json
import os
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from rdflib import RDF, Graph, URIRef

ESTLEG_NS = "https://w3id.org/estleg/"
_INDEX_REL = ("krr_outputs", "INDEX.json")
_ABBREV_REL = ("data", "law_abbreviations.json")
_PEEP_SUFFIX = "_peep.json"


class LawNotFoundError(LookupError):
    """Raised when ``load_law`` cannot resolve ``name`` to an INDEX entry."""


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _as_corpus_root(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    if (path.joinpath(*_INDEX_REL)).is_file():
        return path
    if path.name == "krr_outputs" and (path / "INDEX.json").is_file():
        return path.parent
    return None


def corpus_root(root: str | Path | None = None) -> Path:
    """Return the corpus root (the directory that contains ``krr_outputs/INDEX.json``).

    Resolution order:

    1. Explicit ``root`` (repo root or the ``krr_outputs`` directory itself).
    2. ``ESTLEG_CORPUS`` if set.
    3. Walk up from this file, then from the current working directory.

    Raises ``FileNotFoundError`` when no INDEX is found.
    """
    if root is not None:
        candidate = Path(root).expanduser().resolve()
        found = _as_corpus_root(candidate)
        if found is None:
            raise FileNotFoundError(f"{candidate} does not contain krr_outputs/INDEX.json")
        return found

    env = os.environ.get("ESTLEG_CORPUS")
    if env:
        candidate = Path(env).expanduser().resolve()
        found = _as_corpus_root(candidate)
        if found is None:
            raise FileNotFoundError(
                f"ESTLEG_CORPUS={env!r} does not contain krr_outputs/INDEX.json"
            )
        return found

    here = Path(__file__).resolve()
    for start in (here, Path.cwd().resolve()):
        for candidate in (start, *start.parents):
            found = _as_corpus_root(candidate)
            if found is not None:
                return found

    raise FileNotFoundError(
        "Could not locate the Estonian Legal Ontology corpus. "
        "Pass root=... or set ESTLEG_CORPUS to the directory that contains "
        "krr_outputs/INDEX.json."
    )


def _krr_dir(root: Path) -> Path:
    return root / "krr_outputs"


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=16)
def _index_laws(root_key: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    path = Path(root_key).joinpath(*_INDEX_REL)
    payload = _load_json(path)
    laws = payload.get("laws") if isinstance(payload, dict) else None
    if not isinstance(laws, list):
        raise ValueError(f"{path} has no laws list")
    entries: list[tuple[str, tuple[str, ...]]] = []
    for item in laws:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        files = item.get("files")
        if not isinstance(files, list):
            continue
        filenames = tuple(f for f in files if isinstance(f, str) and f)
        entries.append((name, filenames))
    return tuple(entries)


@lru_cache(maxsize=16)
def _abbreviation_rows(root_key: str) -> tuple[tuple[str, str, str], ...]:
    """Return ``(slug, abbrev, title)`` rows from ``data/law_abbreviations.json``.

    Missing or unreadable files yield an empty tuple so abbreviation lookup
    stays optional.
    """
    path = Path(root_key).joinpath(*_ABBREV_REL)
    if not path.is_file():
        return ()
    try:
        payload = _load_json(path)
    except (OSError, ValueError):
        return ()
    if not isinstance(payload, dict):
        return ()
    rows: list[tuple[str, str, str]] = []
    for slug, meta in payload.items():
        if not isinstance(slug, str) or not isinstance(meta, dict):
            continue
        abbrev = meta.get("abbrev")
        title = meta.get("title")
        rows.append(
            (
                slug,
                abbrev if isinstance(abbrev, str) else "",
                title if isinstance(title, str) else "",
            )
        )
    return tuple(rows)


def _title_of(slug: str, title: str) -> str:
    return title if title else slug.replace("_", " ")


def _ambiguous(name: str, slugs: list[str]) -> LawNotFoundError:
    preview = ", ".join(slugs[:5])
    extra = f" (and {len(slugs) - 5} more)" if len(slugs) > 5 else ""
    return LawNotFoundError(
        f"{name!r} matches {len(slugs)} laws; use an exact INDEX slug or "
        f"abbreviation. Candidates: {preview}{extra}."
    )


def _not_found(name: str) -> LawNotFoundError:
    return LawNotFoundError(
        f"No enacted law matched {name!r}. Use an exact INDEX slug "
        f"(e.g. 'abipolitseiniku_seadus'), a case-insensitive title substring, "
        f"or a registry abbreviation (e.g. 'ABIPOL')."
    )


def _resolve_law(name: str, root: Path) -> tuple[str, tuple[str, ...]]:
    query = name.strip()
    if not query:
        raise LawNotFoundError("Law name is empty.")

    entries = _index_laws(str(root))
    by_name = {slug: files for slug, files in entries}

    if query in by_name:
        return query, by_name[query]

    folded = _fold(query)
    ci_hits = [(slug, files) for slug, files in entries if _fold(slug) == folded]
    if len(ci_hits) == 1:
        return ci_hits[0]
    if len(ci_hits) > 1:
        raise _ambiguous(query, [slug for slug, _ in ci_hits])

    abbrev_hits: list[tuple[str, tuple[str, ...]]] = []
    exact_titles: list[tuple[str, tuple[str, ...]]] = []
    substr_titles: list[tuple[str, tuple[str, ...]]] = []
    seen_title_slugs: set[str] = set()
    for slug, abbrev, title in _abbreviation_rows(str(root)):
        files = by_name.get(slug)
        if files is None:
            continue
        if abbrev and _fold(abbrev) == folded:
            abbrev_hits.append((slug, files))
        label = _title_of(slug, title)
        folded_label = _fold(label)
        if folded_label == folded:
            exact_titles.append((slug, files))
            seen_title_slugs.add(slug)
        elif folded in folded_label:
            substr_titles.append((slug, files))
            seen_title_slugs.add(slug)

    if len(abbrev_hits) == 1:
        return abbrev_hits[0]
    if len(abbrev_hits) > 1:
        raise _ambiguous(query, [slug for slug, _ in abbrev_hits])

    registered = {slug for slug, _, _ in _abbreviation_rows(str(root))}
    for slug, files in entries:
        if slug in registered or slug in seen_title_slugs:
            continue
        label = _title_of(slug, "")
        folded_label = _fold(label)
        if folded_label == folded:
            exact_titles.append((slug, files))
        elif folded in folded_label:
            substr_titles.append((slug, files))

    if len(exact_titles) == 1:
        return exact_titles[0]
    if len(exact_titles) > 1:
        raise _ambiguous(query, [slug for slug, _ in exact_titles])
    if len(substr_titles) == 1:
        return substr_titles[0]
    if len(substr_titles) > 1:
        raise _ambiguous(query, [slug for slug, _ in substr_titles])

    raise _not_found(query)


def _sanctions_sidecar(krr: Path, peep_name: str) -> Path | None:
    filename = Path(peep_name).name
    if not filename.endswith(_PEEP_SUFFIX):
        return None
    stem = filename[: -len(_PEEP_SUFFIX)]
    path = krr / "sanctions" / f"sanctions_{stem}.json"
    return path if path.is_file() else None


def _parse_jsonld(graph: Graph, path: Path) -> None:
    graph.parse(source=str(path), format="json-ld")


def load_law(name: str, *, root: str | Path | None = None) -> Graph:
    """Load every peep (and matching sanctions sidecar) for one INDEX law.

    ``name`` is an exact INDEX slug, a registry abbreviation, or a
    case-insensitive title substring. Returns an ``rdflib.Graph``.
    """
    corpus = corpus_root(root)
    _, files = _resolve_law(name, corpus)
    krr = _krr_dir(corpus)
    graph = Graph()
    for filename in files:
        peep = krr / filename
        _parse_jsonld(graph, peep)
        sidecar = _sanctions_sidecar(krr, filename)
        if sidecar is not None:
            _parse_jsonld(graph, sidecar)
    return graph


def _iris_typed(graph: Graph, needle: str) -> list[str]:
    found: set[str] = set()
    for subject, _, type_term in graph.triples((None, RDF.type, None)):
        if needle in str(type_term):
            found.add(str(subject))
    return sorted(found)


def provisions_of(graph: Graph) -> list[str]:
    """Return provision node IRIs (RDF type contains ``LegalProvision``)."""
    return _iris_typed(graph, "LegalProvision")


def sanctions_of(graph: Graph) -> list[str]:
    """Return sanction node IRIs (RDF type contains ``Sanction``)."""
    return _iris_typed(graph, "Sanction")


def _expand_iri(iri: str) -> str:
    text = iri.strip()
    if text.startswith("estleg:"):
        return ESTLEG_NS + text[len("estleg:") :]
    return text


def _compact_id(expanded: str) -> str:
    if expanded.startswith(ESTLEG_NS):
        return expanded[len(ESTLEG_NS) :]
    return expanded


def _lookup_iri(graph: Graph, expanded: str) -> str | None:
    node = URIRef(expanded)
    if (node, None, None) in graph:
        return str(node)
    return None


def _slug_from_compact(compact: str, root: Path) -> str | None:
    """Guess an INDEX slug from a compact estleg local name."""
    entries = {slug for slug, _ in _index_laws(str(root))}
    best_slug: str | None = None
    best_len = -1
    for slug, abbrev, _title in _abbreviation_rows(str(root)):
        if slug not in entries or not abbrev:
            continue
        if compact == abbrev or compact.startswith(abbrev + "_"):
            if len(abbrev) > best_len:
                best_slug = slug
                best_len = len(abbrev)
    if best_slug:
        return best_slug
    prefix = compact.split("_", 1)[0]
    if prefix in entries:
        return prefix
    return prefix or None


def resolve_iri(
    iri: str,
    *,
    root: str | Path | None = None,
    graph: Graph | None = None,
) -> str | None:
    """Return the expanded IRI if that node is present.

    When ``graph`` is given, look the node up there (so
    ``resolve_iri("estleg:ABIPOL_Par_1", graph=load_law("abipolitseiniku_seadus"))``
    works). Otherwise load the law whose registry abbreviation matches the
    first compact-id segment (longest prefix when the abbrev contains ``_``).
    """
    expanded = _expand_iri(iri)
    if not expanded:
        return None
    if graph is not None:
        return _lookup_iri(graph, expanded)

    corpus = corpus_root(root)
    compact = _compact_id(expanded)
    guess = _slug_from_compact(compact, corpus)
    if not guess:
        return None
    try:
        loaded = load_law(guess, root=corpus)
    except LawNotFoundError:
        return None
    return _lookup_iri(loaded, expanded)
