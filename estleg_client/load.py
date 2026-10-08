"""Read-only loaders for enacted-law peeps and sanction sidecars.

Resolves a law name against ``krr_outputs/INDEX.json`` (plus
``data/law_abbreviations.json``), parses every listed file (multi-osa
assembly), and merges ``krr_outputs/sanctions/sanctions_<stem>.json`` when that
sidecar exists. In a ``fetch_corpus`` download (no per-law peeps) the same
names resolve against the per-law shards cut from ``combined_ontology.jsonld``.
Producer modules (``estleg``, ``scripts``) are never imported.
"""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from rdflib import RDF, Graph, URIRef

from estleg_client import _shards
from estleg_client._corpus import CorpusUnavailableError, corpus_root
from estleg_client._jsonld import (
    ESTLEG_NS,
    expand,
    graph_from_nodes,
    is_real_file,
    parse_into,
)
from estleg_client.vocab import PROVISION_TYPES, SANCTION_TYPES, SUBSECTION_TYPES

__all__ = [
    "ESTLEG_NS",
    "LawNotFoundError",
    "NotFoundError",
    "corpus_root",
    "load_law",
    "provisions_of",
    "resolve_iri",
    "sanctions_of",
    "typed_iris",
]

_INDEX_REL = ("krr_outputs", "INDEX.json")
_ABBREV_REL = ("data", "law_abbreviations.json")
_PEEP_SUFFIX = "_peep.json"


class NotFoundError(LookupError):
    """A loader could not resolve its key (or the key was ambiguous)."""


class LawNotFoundError(NotFoundError):
    """Raised when ``load_law`` cannot resolve ``name`` to an INDEX entry."""


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _krr_dir(root: Path) -> Path:
    return root / "krr_outputs"


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=16)
def _index_laws(root_key: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    path = Path(root_key).joinpath(*_INDEX_REL)
    if not path.is_file():
        return ()
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


def _shard_entry(name: str, root: Path) -> dict[str, Any] | None:
    """Resolve ``name`` against the release shard index (slug, prefix, title)."""
    index = _shards.read_shard_index(_krr_dir(root))
    if index is None:
        return None
    entries = [e for e in index.get("laws", []) if isinstance(e, dict)]
    query = name.strip()
    folded = _fold(query)
    slug_query = _shards.slugify(query)
    for key in ("slug", "prefix"):
        hits = [e for e in entries if _fold(str(e.get(key, ""))) == folded]
        if len(hits) == 1:
            return hits[0]
    exact = [e for e in entries if _fold(str(e.get("title", ""))) == folded]
    if len(exact) == 1:
        return exact[0]
    subs = [
        e
        for e in entries
        if folded in _fold(str(e.get("title", "")))
        or (slug_query and slug_query in str(e.get("slug", "")))
    ]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1 or len(exact) > 1:
        raise _ambiguous(query, [str(e.get("slug") or e.get("prefix")) for e in (exact or subs)])
    raise _not_found(query)


def _load_law_from_shards(name: str, root: Path, slug: str | None) -> Graph:
    krr = _krr_dir(root)
    index = _shards.read_shard_index(krr)
    if index is None:
        raise CorpusUnavailableError(
            f"The per-law peep files for {name!r} are not in {krr} and no law shards "
            "exist. Use a git checkout, or re-run fetch_corpus() with the "
            "combined_ontology.jsonld.gz asset."
        )
    entry = None
    if slug:
        entry = next(
            (e for e in index.get("laws", []) if isinstance(e, dict) and e.get("slug") == slug),
            None,
        )
    if entry is None:
        entry = _shard_entry(name, root)
    if entry is None:
        raise _not_found(name)
    nodes = _shards.read_shard_nodes(krr, str(entry["file"]))
    return graph_from_nodes(nodes, index.get("context") or {})


def load_law(name: str, *, root: str | Path | None = None) -> Graph:
    """Load one enacted law as an ``rdflib.Graph``.

    ``name`` is an exact INDEX slug (``abipolitseiniku_seadus``), a registry
    abbreviation (``ABIPOL``), or a case-insensitive title substring. In a git
    checkout every peep file of the law plus its sanctions sidecar is parsed;
    in a ``fetch_corpus`` download the law's shard is parsed instead.

    Raises :class:`LawNotFoundError` for an unknown or ambiguous name.
    """
    corpus = corpus_root(root)
    krr = _krr_dir(corpus)
    slug: str | None = None
    files: tuple[str, ...] = ()
    if _index_laws(str(corpus)):
        try:
            slug, files = _resolve_law(name, corpus)
        except LawNotFoundError:
            if _shards.read_shard_index(krr) is None:
                raise
    if files and all(is_real_file(krr / filename) for filename in files):
        graph = Graph()
        for filename in files:
            parse_into(graph, krr / filename)
            sidecar = _sanctions_sidecar(krr, filename)
            if sidecar is not None:
                parse_into(graph, sidecar)
        return graph
    return _load_law_from_shards(name, corpus, slug)


def typed_iris(
    graph: Graph,
    *types: str,
    exclude: tuple[str, ...] = (),
) -> list[str]:
    """Sorted subject IRIs whose ``rdf:type`` is exactly one of ``types``.

    Subjects that also carry one of the ``exclude`` types are dropped.
    """
    excluded = [URIRef(expand(t)) for t in exclude]
    found: set[str] = set()
    for type_iri in (URIRef(expand(t)) for t in types):
        for subject in graph.subjects(RDF.type, type_iri):
            if any((subject, RDF.type, ex) in graph for ex in excluded):
                continue
            found.add(str(subject))
    return sorted(found)


def provisions_of(graph: Graph, *, include_subsections: bool = False) -> list[str]:
    """Paragraph-level provision IRIs (exact ``LegalProvision`` / ``KovProvision``).

    ``estleg:Subsection`` (lõige) nodes are excluded even when they also carry
    ``LegalProvision`` -- the combined release aggregate materialises that
    supertype on every subsection, the per-law peeps do not -- so a checkout
    and a release download give the same answer. ``include_subsections=True``
    returns paragraphs and subsections together.
    """
    if include_subsections:
        return typed_iris(graph, *PROVISION_TYPES, *SUBSECTION_TYPES)
    return typed_iris(graph, *PROVISION_TYPES, exclude=SUBSECTION_TYPES)


def sanctions_of(graph: Graph) -> list[str]:
    """Sanction node IRIs: exact ``estleg:Sanction`` (never a ``SanctionType``)."""
    return typed_iris(graph, *SANCTION_TYPES)


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
    """Return the expanded IRI if that node is present, else ``None``.

    With ``graph`` the node is looked up there (so
    ``resolve_iri("estleg:ABIPOL_Par_1", graph=load_law("ABIPOL"))`` works).
    Without it the owning record is loaded from its local-name family:
    ``Reg_<id>_...`` (regulation), ``RK_...`` (Riigikohus decision),
    ``Draft_...`` (draft), ``EU_<celex>`` (EU act); anything else is treated as
    an enacted-law node whose registry abbreviation is the longest matching
    prefix.
    """
    expanded = _expand_iri(iri)
    if not expanded:
        return None
    if graph is not None:
        return _lookup_iri(graph, expanded)

    corpus = corpus_root(root)
    compact_id = _compact_id(expanded)
    from estleg_client.corpora import load_for_compact_id

    loaded = load_for_compact_id(compact_id, root=corpus)
    if loaded is not None:
        return _lookup_iri(loaded, expanded)
    guess = _slug_from_compact(compact_id, corpus)
    if not guess:
        return None
    try:
        loaded = load_law(guess, root=corpus)
    except (LawNotFoundError, CorpusUnavailableError):
        return None
    return _lookup_iri(loaded, expanded)
