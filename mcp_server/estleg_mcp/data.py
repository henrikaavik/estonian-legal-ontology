"""Data-access layer for the Estonian Legal Ontology MCP server.

Pure functions over the JSON-LD corpus under ``krr_outputs/``. This module has
**no MCP imports** so it can be unit-tested directly against the real corpus.

Design constraints (see the corpus ``docs/`` and ``AGENTS.md``):

* Read per-law files on demand; never load the whole corpus or the
  ``combined_ontology.jsonld`` aggregate (it is a Git LFS pointer in most
  clones and is huge even when materialised).
* Every result that maps to a source carries its canonical riigiteataja.ee /
  riigikohus.ee / eelnoud.valitsus.ee / EUR-Lex URL.
* A missing predicate, file, or sidecar yields an empty list, never an
  exception. Corrupt JSON is skipped, not fatal.

Confirmed corpus predicate names (verified by inspecting real law files such as
``karistusseadustik_osa1_peep.json`` and ``volaoigusseadus_osa2_peep.json``):

* Act node ``@type`` contains ``estleg:Act`` / ``estleg:Law`` (also
  ``owl:Ontology``); carries ``dcterms:source`` ({"@id": rt .xml URL}),
  ``dcterms:title``, ``rdfs:label``, ``estleg:temporalStatus``,
  ``dcterms:subject`` (EuroVoc IRIs), ``estleg:transposesDirective``,
  ``estleg:affectedBy`` / ``estleg:hasProposedAmendment`` (draft links live on
  the act/map node).
* Provision node ``@type`` contains the bare ``estleg:LegalProvision``
  class (issue #434 onwards; municipal-regulation sections additionally carry
  ``estleg:KovProvision``, and legacy peeps used a per-law
  ``estleg:LegalProvision_<abbrev>`` subclass -- :func:`_is_provision` accepts
  all three). It carries
  ``estleg:paragrahv`` ("§ 1."), ``rdfs:label``, ``estleg:summary``,
  ``estleg:legalText``, ``estleg:references``, ``estleg:referencedBy``,
  ``estleg:interpretedBy`` (court-decision IRIs), ``estleg:competentAuthority``
  (institution IRIs), ``estleg:hasSanction``, ``estleg:normativeType``.
* Subsection node ``@type`` contains ``estleg:Subsection``.
"""

from __future__ import annotations

import ast
import json
import os
import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
Node = dict[str, Any]
Graph = list[Node]


# ---------------------------------------------------------------------------
# Corpus root resolution
# ---------------------------------------------------------------------------
_INDEX_REL = ("krr_outputs", "INDEX.json")


def _looks_like_corpus(root: Path) -> bool:
    return (root / _INDEX_REL[0] / _INDEX_REL[1]).is_file()


@lru_cache(maxsize=1)
def corpus_root() -> Path:
    """Return the corpus root (the dir containing ``krr_outputs/INDEX.json``).

    Resolution order:

    1. ``ESTLEG_CORPUS`` environment variable, if set and valid.
    2. Walk up from this file until a dir containing ``krr_outputs/INDEX.json``
       is found (handles both an installed package living inside the repo and a
       ``mcp_server/`` subdirectory layout).

    Raises ``FileNotFoundError`` with an actionable message if neither works.
    """
    env = os.environ.get("ESTLEG_CORPUS")
    if env:
        root = Path(env).expanduser().resolve()
        if _looks_like_corpus(root):
            return root
        raise FileNotFoundError(
            f"ESTLEG_CORPUS={env!r} does not contain krr_outputs/INDEX.json"
        )

    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if candidate.is_dir() and _looks_like_corpus(candidate):
            return candidate
    raise FileNotFoundError(
        "Could not locate the Estonian Legal Ontology corpus. Set the "
        "ESTLEG_CORPUS environment variable to the directory that contains "
        "krr_outputs/INDEX.json."
    )


def krr_dir() -> Path:
    """Return the ``krr_outputs`` directory of the resolved corpus."""
    return corpus_root() / "krr_outputs"


@lru_cache(maxsize=1)
def ontology_version() -> str:
    """Read ``owl:versionInfo`` from the committed dataset header (#530)."""
    meta = _load_json(corpus_root() / "metadata.jsonld")
    if isinstance(meta, dict):
        raw = meta.get("owl:versionInfo")
        if isinstance(raw, str) and raw:
            return raw
    return ""


# ---------------------------------------------------------------------------
# Low-level JSON helpers
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> Any | None:
    """Load JSON from ``path``; return ``None`` on any read/parse error.

    Git LFS pointer files (``similarity_index.json``,
    ``combined_ontology.jsonld``) are not valid JSON in a clone without
    ``git lfs pull`` and therefore return ``None`` here -- callers degrade
    gracefully rather than crash.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _graph_of(path: Path) -> Graph:
    """Return the ``@graph`` list of a JSON-LD file, or ``[]`` on failure."""
    doc = _load_json(path)
    if isinstance(doc, dict):
        graph = doc.get("@graph")
        if isinstance(graph, list):
            return [n for n in graph if isinstance(n, dict)]
    return []


def _as_list(value: Any) -> list[Any]:
    """Normalise a JSON-LD field to a list (scalars wrap, ``None`` -> [])."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _types_of(node: Node) -> list[str]:
    """Return the ``@type`` of a node as a list of strings."""
    return [t for t in _as_list(node.get("@type")) if isinstance(t, str)]


def _text(value: Any) -> str:
    """Extract a plain string from common JSON-LD text shapes.

    Handles plain strings, ``{"@value": ...}`` objects, and lists of those
    (joined). Mirrors ``scripts/estleg_common.jsonld_text`` semantics without
    importing it, so the MCP package stays standalone.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("@value")
        return inner if isinstance(inner, str) else ""
    if isinstance(value, list):
        parts = [t for item in value if (t := _text(item))]
        return " ".join(parts)
    return ""


def _id_of(value: Any) -> str:
    """Return the ``@id`` of an IRI-object, or "" if not an IRI reference."""
    if isinstance(value, dict):
        ref = value.get("@id")
        return ref if isinstance(ref, str) else ""
    return ""


def _ids_of(value: Any) -> list[str]:
    """Return all ``@id`` references under a (possibly list) field value."""
    out: list[str] = []
    for item in _as_list(value):
        ref = _id_of(item)
        if ref:
            out.append(ref)
    return out


# ---------------------------------------------------------------------------
# Normalisation for the law resolver
# ---------------------------------------------------------------------------
def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


_FOLD_MAP = str.maketrans(
    {
        "õ": "o", "ä": "a", "ö": "o", "ü": "u", "š": "s", "ž": "z",
        "Õ": "o", "Ä": "a", "Ö": "o", "Ü": "u", "Š": "s", "Ž": "z",
    }
)


def _fold(text: str) -> str:
    """Lowercase + strip Estonian diacritics, for accent-insensitive lookup."""
    return _nfc(text).lower().translate(_FOLD_MAP)


def _title_from_slug(slug: str) -> str:
    """Best-effort human title from a corpus slug (resolver fallback)."""
    return slug.replace("_", " ").strip().capitalize()


def _slugify_like(text: str) -> str:
    """Approximate the corpus ``slugify``: fold, lowercase, non-alnum -> ``_``.

    Mirrors ``scripts/estleg_common.slugify`` closely enough to map a known
    title to its corpus slug for resolver lookups (used so human abbreviations
    whose laws are absent from the abbreviation registry still resolve via the
    slug index). Not truncated to 80 chars here -- callers match it against slug
    keys that are themselves the (already truncated) corpus slugs, and short
    titles like the major codes are never truncated.
    """
    folded = _fold(text)
    out: list[str] = []
    prev_us = False
    for ch in folded:
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        elif not prev_us:
            out.append("_")
            prev_us = True
    return "".join(out).strip("_")


# ---------------------------------------------------------------------------
# Well-known human abbreviations -> full law title.
#
# The corpus registry (data/law_abbreviations.json) stores the *migrated*
# IRI-prefix abbreviation (e.g. karistusseadustik -> "KARIST_2",
# toolepingu_seadus -> "TOOLEP"), and some major laws have no registry
# abbreviation at all (volaoigusseadus, eesti_vabariigi_pohiseadus). Those
# IRI-prefix forms are NOT what a lawmaker types. This curated map (sourced
# from scripts/estleg_common.KNOWN_ABBREVIATIONS) lets the resolver accept the
# conventional Estonian abbreviations (KarS, VÕS, TLS, PS, ...) by mapping them
# to the canonical title, which then resolves to the slug via the title index.
# ---------------------------------------------------------------------------
_HUMAN_ABBREVIATIONS: dict[str, str] = {
    "KarS": "Karistusseadustik",
    "VÕS": "Võlaõigusseadus",
    "TsÜS": "Tsiviilseadustiku üldosa seadus",
    "AÕS": "Asjaõigusseadus",
    "PKS": "Perekonnaseadus",
    "ÄS": "Äriseadustik",
    "HMS": "Haldusmenetluse seadus",
    "TsMS": "Tsiviilkohtumenetluse seadustik",
    "KrMS": "Kriminaalmenetluse seadustik",
    "TMS": "Täitemenetluse seadustik",
    "KOKS": "Kohaliku omavalitsuse korralduse seadus",
    "PS": "Eesti Vabariigi põhiseadus",
    "PankrS": "Pankrotiseadus",
    "MKS": "Maksukorralduse seadus",
    "TLS": "Töölepingu seadus",
    "RLS": "Riigilõivuseadus",
    "TTKS": "Töötervishoiu ja tööohutuse seadus",
    "IKS": "Isikuandmete kaitse seadus",
    "RHS": "Riigihangete seadus",
    "SHS": "Sotsiaalhoolekande seadus",
    "EhS": "Ehitusseadustik",
    "KVS": "Korruptsioonivastane seadus",
    "LKS": "Looduskaitseseadus",
    "RavS": "Ravimiseadus",
    "AVTS": "Avaliku teabe seadus",
    "KMS": "Käibemaksuseadus",
    "TuMS": "Tulumaksuseadus",
    "SMS": "Sotsiaalmaksuseadus",
    "KorrS": "Korrakaitseseadus",
    "RES": "Riigieelarve seadus",
    "VeeS": "Veeseadus",
    "LS": "Liiklusseadus",
    "VMS": "Välismaalaste seadus",
    "KAS": "Krediidiasutuste seadus",
    "NotS": "Notariaadiseadus",
    "LasteKS": "Lastekaitseseadus",
    "PGS": "Põhikooli- ja gümnaasiumiseadus",
}


# ---------------------------------------------------------------------------
# Law records and the resolver index
# ---------------------------------------------------------------------------
class LawRecord:
    """A resolved law: its corpus slug, peep files, title, and abbreviation."""

    __slots__ = ("name", "files", "title", "abbrev")

    def __init__(
        self, name: str, files: list[str], title: str, abbrev: str | None
    ) -> None:
        self.name = name
        self.files = files
        self.title = title
        self.abbrev = abbrev

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"LawRecord(name={self.name!r}, abbrev={self.abbrev!r})"


@lru_cache(maxsize=1)
def _records_by_slug() -> dict[str, LawRecord]:
    """Build the slug -> LawRecord map from INDEX.json + law_abbreviations.json.

    Source of truth for the files list is ``INDEX.json`` ``laws``; titles and
    the (migrated IRI-prefix) abbreviation come from
    ``data/law_abbreviations.json`` (keyed by the same slug). Many index laws
    (mostly long-titled international conventions) are absent from the registry
    or use a differently-slugified key; those get a slug-derived fallback title
    and no registry abbreviation. ``get_law`` / ``search_laws`` upgrade the
    displayed title to the act node's ``dcterms:title`` when the graph is loaded.
    """
    root = corpus_root()
    index = _load_json(root / "krr_outputs" / "INDEX.json")
    abbr = _load_json(root / "data" / "law_abbreviations.json")
    abbr = abbr if isinstance(abbr, dict) else {}

    records: dict[str, LawRecord] = {}
    laws = index.get("laws") if isinstance(index, dict) else None
    for entry in laws or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        files = [f for f in _as_list(entry.get("files")) if isinstance(f, str)]
        # Skip reproducible OWL/TBox module artifacts (e.g.
        # ``karistusseadustik_eriosa_owl`` / ``tsus_osa7_138_169_owl``, whose
        # files are ``*_owl.jsonld``). Those carry an owl:Class / estleg:Section
        # TBox shape rather than the per-law ``*_peep.json`` instance graph this
        # layer reads: they expose zero provision instances
        # and must never surface to a lawmaker as a queryable "law" (they would
        # otherwise pollute search_laws/resolve_law for KarS / TsÜS).
        if files and not any(f.endswith("_peep.json") for f in files):
            continue
        meta = abbr.get(name) if isinstance(abbr.get(name), dict) else {}
        title = meta.get("title") if isinstance(meta.get("title"), str) else ""
        abbrev = meta.get("abbrev") if isinstance(meta.get("abbrev"), str) else None
        if not title:
            title = _title_from_slug(name)
        records[name] = LawRecord(name=name, files=files, title=title, abbrev=abbrev)
    return records


@lru_cache(maxsize=1)
def _resolver_index() -> dict[str, str]:
    """Build a lookup key -> slug map for ``resolve_law``.

    All keys are normalised (lowercased NFC, and an accent-folded variant).
    Built in increasing-priority order, first-writer-wins, so later groups
    override earlier ones for the same string:

    1. slugs (lowest priority)
    2. registry (IRI-prefix) abbreviations from law_abbreviations.json
    3. canonical titles
    4. well-known human abbreviations (KarS, VÕS, ...), resolved via title
       (highest priority, so "PS" maps to the põhiseadus even if some other
       law's slug/registry-abbrev folded to the same key)
    """
    index: dict[str, str] = {}

    def put(key: str, slug: str) -> None:
        key = key.strip()
        if key and key not in index:
            index[key] = slug

    records = _records_by_slug()
    # 1. Slugs.
    for slug in records:
        put(slug.lower(), slug)
        put(_fold(slug), slug)
    # 2. Registry (migrated IRI-prefix) abbreviations.
    for slug, rec in records.items():
        if rec.abbrev:
            put(_nfc(rec.abbrev).lower(), slug)
            put(_fold(rec.abbrev), slug)
    # 3. Canonical titles.
    for slug, rec in records.items():
        if rec.title:
            put(_nfc(rec.title).lower(), slug)
            put(_fold(rec.title), slug)
    # 4. Well-known human abbreviations -> slug. Resolve each abbrev's canonical
    #    title to a slug by trying, in order: the exact/folded title key (works
    #    when the law is in the registry), then the slugified title matched
    #    against the known slug set (works for major codes absent from the
    #    registry, e.g. Võlaõigusseadus / Eesti Vabariigi põhiseadus). The
    #    snapshot is taken before inserting so abbrev keys never resolve via
    #    another abbrev.
    title_index = dict(index)
    known_slugs = set(records)
    for human_abbrev, title in _HUMAN_ABBREVIATIONS.items():
        slug = title_index.get(_nfc(title).lower()) or title_index.get(_fold(title))
        if slug is None:
            candidate = _slugify_like(title)
            if candidate in known_slugs:
                slug = candidate
        if slug:
            # Force these to win even over a colliding slug/registry key.
            index[_nfc(human_abbrev).lower()] = slug
            index.setdefault(_fold(human_abbrev), slug)
    return index


@lru_cache(maxsize=1)
def _iri_prefix_to_slug() -> list[tuple[str, str]]:
    """Build a (registry-abbrev, slug) list for resolving internal IRIs.

    Provision/act IRIs are abbreviation-prefixed with the *registry* (migrated)
    abbreviation, e.g. ``estleg:KARIST_2_Osa1_Par_13`` -> ``KARIST_2``,
    ``estleg:TLS_Par_5`` -> ``TLS`` (registry abbrev, which for toolepingu_seadus
    is "TOOLEP", so a TLS-prefixed IRI is a different law's). The list is sorted
    by descending abbreviation length so ``_law_slug_from_iri`` can do a
    longest-prefix match (abbreviations such as ``KARIST_2`` / ``KELS_LASTEAS``
    contain underscores, so a naive split on ``_`` would mis-segment them).
    """
    pairs: list[tuple[str, str]] = []
    for slug, rec in _records_by_slug().items():
        if rec.abbrev:
            pairs.append((rec.abbrev, slug))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


# ---------------------------------------------------------------------------
# Public resolver API
# ---------------------------------------------------------------------------
def resolve_law(query: str) -> LawRecord | None:
    """Resolve a free-text law reference to a :class:`LawRecord`.

    Matches (in order): exact title, accent-folded title, abbreviation
    (e.g. ``KarS``, ``VÕS``), and slug. Returns ``None`` if nothing matches.
    """
    if not query or not query.strip():
        return None
    q = query.strip()
    records = _records_by_slug()
    index = _resolver_index()
    for key in (_nfc(q).lower(), _fold(q)):
        slug = index.get(key)
        if slug:
            return records.get(slug)
    return None


# One group: (query-side labels, haystack terms = labels + keywords).
_EurovocGroup = tuple[tuple[str, ...], tuple[str, ...]]


def _folded_terms(*values: Any) -> list[str]:
    """Fold string values (and lists/tuples of them) into unique non-empty terms."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        items = value if isinstance(value, (list, tuple)) else _as_list(value)
        for item in items:
            if not isinstance(item, str):
                continue
            term = _fold(item.strip())
            if term and term not in seen:
                seen.add(term)
                out.append(term)
    return out


# Where the classifier's ``EUROVOC_DOMAINS`` table lives, newest location
# first. Issue #472 moved the implementation into the package and left
# ``scripts/classify_eurovoc.py`` as a nine-line runpy shim with no table in
# it, which silently emptied the keyword expansion here.
_EUROVOC_TABLE_RELPATHS = (
    ("src", "estleg", "classify_eurovoc.py"),
    ("scripts", "classify_eurovoc.py"),
)


def _eurovoc_domains_literal(path: Path) -> dict[Any, Any] | None:
    """Read the literal ``EUROVOC_DOMAINS`` table out of a module, without importing it.

    Parses the file with :mod:`ast` and literal-evaluates the assignment, so a
    corpus script is never executed. Returns ``None`` when the file is absent,
    unparseable, or carries no literal ``EUROVOC_DOMAINS`` (e.g. the runpy
    shim), which lets the caller fall through to the next candidate path.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        if target == "EUROVOC_DOMAINS" and value is not None:
            try:
                raw = ast.literal_eval(value)
            except (ValueError, TypeError):
                return None
            return raw if isinstance(raw, dict) else None
    return None


@lru_cache(maxsize=1)
def _eurovoc_keywords_by_id() -> dict[str, tuple[str, ...]]:
    """Optional keyword lists from the classifier's ``EUROVOC_DOMAINS`` table.

    ``data/eurovoc_domain_mapping.json`` stores verified EN/ET labels but not
    the classifier's Estonian stems. When the classifier module is present we
    read the literal ``EUROVOC_DOMAINS`` table (no import) and key it by
    descriptor id so a search for ``criminal law`` can also hit a title that
    only contains ``karistus``. Tries ``src/estleg/classify_eurovoc.py`` first
    and the older ``scripts/`` path second; no table anywhere yields ``{}``.
    """
    try:
        root = corpus_root()
    except FileNotFoundError:
        return {}
    raw: dict[Any, Any] | None = None
    for relpath in _EUROVOC_TABLE_RELPATHS:
        raw = _eurovoc_domains_literal(root.joinpath(*relpath))
        if raw is not None:
            break
    if not raw:
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for code, spec in raw.items():
        if not isinstance(spec, (list, tuple)) or len(spec) < 4:
            continue
        kws = [
            kw
            for kw in spec[3]
            if isinstance(kw, str) and kw.strip() and not kw.startswith("r:")
        ]
        if kws:
            out[str(code)] = tuple(kws)
    return out


@lru_cache(maxsize=1)
def _eurovoc_expansion_groups() -> tuple[_EurovocGroup, ...]:
    """Load EuroVoc synonym groups from ``data/eurovoc_domain_mapping.json``.

    Each group is ``(labels, terms)`` with already-folded strings. ``labels``
    are the English and Estonian domain labels used to recognise a query
    phrase; ``terms`` are those labels plus any domain keywords (optional
    ``keywords`` field on the mapping entry, else the classifier stems for
    ``newId``). Returns ``()`` when the corpus or mapping is missing.
    """
    try:
        doc = _load_json(corpus_root() / "data" / "eurovoc_domain_mapping.json")
    except FileNotFoundError:
        return ()
    if not isinstance(doc, list):
        return ()
    kw_by_id = _eurovoc_keywords_by_id()
    groups: list[_EurovocGroup] = []
    for entry in doc:
        if not isinstance(entry, dict):
            continue
        labels = tuple(_folded_terms(entry.get("labelEn"), entry.get("labelEt")))
        extra = entry.get("keywords")
        if extra is None:
            mapped_kws = kw_by_id.get(str(entry.get("newId") or ""))
        else:
            mapped_kws = extra
        terms = tuple(_folded_terms(*labels, mapped_kws))
        if labels:
            groups.append((labels, terms or labels))
    return tuple(groups)


def _eurovoc_query_parts(needle: str) -> tuple[list[tuple[str, ...]], list[str]]:
    """Split a folded query into EuroVoc synonym-groups and leftover AND tokens.

    A complete English or Estonian domain label (casefolded) is consumed as a
    phrase, longest first, and replaced by its synonym group. Tokens that are
    not a domain label stay required — token-AND is not widened into OR.
    """
    groups = _eurovoc_expansion_groups()
    if not needle or not groups:
        return [], []
    leftover = f" {needle} "
    matched: list[tuple[str, ...]] = []
    pairs: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for labels, terms in groups:
        for lab in labels:
            if lab and lab not in seen:
                seen.add(lab)
                pairs.append((lab, terms))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    for lab, terms in pairs:
        wrapped = f" {lab} "
        if wrapped in leftover:
            matched.append(terms)
            leftover = leftover.replace(wrapped, " ")
    return matched, leftover.split()


def search_law_records(query: str, limit: int = 10) -> list[LawRecord]:
    """Return laws whose title, abbreviation, or slug contains ``query``.

    Substring match is accent-insensitive and also covers the *conventional*
    human abbreviation (``KarS``, ``VÕS``, ``TLS``, ``PS``, ...), which the
    registry does not store -- so ``search_laws`` honours the same title/
    abbreviation contract as ``resolve_law``. Multi-token queries are AND
    (every token must hit). A token that is a EuroVoc domain label (English
    or Estonian, from ``data/eurovoc_domain_mapping.json``) also matches if
    the haystack contains the other-language label or a domain keyword.
    Results are sorted with exact title/abbrev matches first, then by title.
    An empty ``query`` returns ``[]``.
    """
    if not query or not query.strip():
        return []
    needle = _fold(query.strip())
    tokens = [t for t in needle.split() if t]
    ev_groups, ev_leftover = _eurovoc_query_parts(needle)
    records = _records_by_slug()
    human = _slug_to_human_abbrev()
    scored: list[tuple[int, str, LawRecord]] = []
    for rec in records.values():
        hay_title = _fold(rec.title)
        hay_abbrev = _fold(rec.abbrev) if rec.abbrev else ""
        hay_human = _fold(human.get(rec.name, ""))
        hay_slug = _fold(rec.name)
        hay_all = f"{hay_title} {hay_abbrev} {hay_human} {hay_slug}"
        token_hit = bool(tokens) and all(t in hay_all for t in tokens)
        ev_hit = bool(ev_groups) and all(
            any(term in hay_all for term in terms) for terms in ev_groups
        ) and all(t in hay_all for t in ev_leftover)
        if (
            needle in hay_title
            or needle in hay_abbrev
            or (hay_human and needle in hay_human)
            or needle in hay_slug
            or token_hit
            or ev_hit
        ):
            if needle in (hay_title, hay_abbrev, hay_human):
                rank = 0
            elif (
                hay_title.startswith(needle)
                or hay_abbrev.startswith(needle)
                or hay_human.startswith(needle)
            ):
                rank = 1
            else:
                rank = 2
            scored.append((rank, hay_title, rec))
    scored.sort(key=lambda t: (t[0], t[1]))
    cap = max(0, int(limit))
    return [rec for _, _, rec in scored[:cap]]


def laws_for_subject(subject: str, limit: int = 20) -> list[dict[str, str]]:
    """Laws whose EuroVoc ``dcterms:subject`` IRI or title contains ``subject`` (#504)."""
    if not subject or not subject.strip() or limit <= 0:
        return []
    needle = _fold(subject.strip())
    out: list[dict[str, str]] = []
    for rec in _records_by_slug().values():
        graph = load_law_graph(rec)
        act = act_node(graph)
        iris = _ids_of(act.get("dcterms:subject")) if act else []
        hay = _fold(" ".join([rec.title, rec.name, *iris]))
        if needle not in hay:
            continue
        out.append(
            {
                "name": rec.name,
                "title": rec.title,
                "abbrev": display_abbrev(rec),
                "subjects": " ".join(iris),
            }
        )
        if len(out) >= limit:
            break
    return out


def define_term(term: str, limit: int = 10) -> list[dict[str, str]]:
    """Lookup ``estleg:LegalConcept`` / Concept nodes by prefLabel (#501/#540).

    Reads ``krr_outputs/concepts/concepts_combined.jsonld`` via the overlay
    loader -- not the law peeps.
    """
    if not term or not term.strip() or limit <= 0:
        return []
    needle = _fold(term.strip())
    graph = overlay_graph("concepts")
    out: list[dict[str, str]] = []
    for node in graph:
        types = _types_of(node)
        if "estleg:LegalConcept" not in types and "estleg:Concept" not in types:
            continue
        label = _text(node.get("skos:prefLabel")) or _text(node.get("rdfs:label"))
        if needle not in _fold(label):
            continue
        out.append(
            {
                "id": _id_of(node) or "",
                "label": label,
                "definition": _text(node.get("skos:definition") or node.get("estleg:definition")),
            }
        )
        if len(out) >= limit:
            break
    return out


@lru_cache(maxsize=1)
def _slug_to_human_abbrev() -> dict[str, str]:
    """Invert ``_HUMAN_ABBREVIATIONS`` to slug -> conventional abbrev.

    The abbreviation registry stores the *migrated IRI-prefix* form
    (``karistusseadustik`` -> ``KARIST_2``), which is not what a lawmaker
    recognises, and major codes (VÕS, PS) are absent from it entirely. This
    inverts the curated human map so display can prefer ``KarS`` / ``VÕS`` / ``PS``.
    """
    out: dict[str, str] = {}
    for human, title in _HUMAN_ABBREVIATIONS.items():
        rec = resolve_law(title)
        if rec and rec.name not in out:
            out[rec.name] = human
    return out


def display_abbrev(record: LawRecord) -> str:
    """The abbreviation to show a human: conventional form if known.

    Prefers the curated human abbreviation (``KarS``, ``VÕS``, ``TLS``, ...);
    falls back to the registry (migrated IRI-prefix) abbreviation, then "".
    """
    return _slug_to_human_abbrev().get(record.name) or record.abbrev or ""


# ---------------------------------------------------------------------------
# Law-graph access
# ---------------------------------------------------------------------------
def load_law_graph(record: LawRecord) -> Graph:
    """Concatenate the ``@graph`` of every peep file for a law.

    Multi-part laws (``volaoigusseadus_osa1..10``) span several files; this
    merges them into one node list. Missing or corrupt files are skipped.
    """
    base = krr_dir()
    graph: Graph = []
    for fname in record.files:
        graph.extend(_graph_of(base / fname))
    return graph


def act_node(graph: Graph) -> Node | None:
    """Return the act/law metadata node of a law graph, if present.

    Prefers a node typed ``estleg:Act`` (the canonical act root); falls back to
    ``estleg:Law`` or ``owl:Ontology``. A non-deprecated node wins over a
    deprecated one (some legacy duplicates carry ``owl:deprecated``).
    """
    candidates: list[Node] = []
    for node in graph:
        types = _types_of(node)
        if "estleg:Act" in types or "estleg:Law" in types or "owl:Ontology" in types:
            candidates.append(node)
    if not candidates:
        return None
    for node in candidates:
        if node.get("owl:deprecated") not in (True, "true"):
            return node
    return candidates[0]


# The §-node classes the corpus stamps. Since issue #434 the generators write
# the bare ``estleg:LegalProvision``; municipal-regulation sections carry
# ``estleg:KovProvision`` as well, and legacy peeps used a per-law
# ``estleg:LegalProvision_<abbrev>`` subclass. Requiring only the legacy prefix
# is what made every provision-backed tool return empty (issue #678).
_PROVISION_TYPES = frozenset({"estleg:LegalProvision", "estleg:KovProvision"})
_LEGACY_PROVISION_PREFIX = "estleg:LegalProvision_"


def _types_are_provision(types: list[str]) -> bool:
    """True when a node's ``@type`` list marks it as a § (any stamped form)."""
    return any(
        t in _PROVISION_TYPES or t.startswith(_LEGACY_PROVISION_PREFIX) for t in types
    )


def _is_provision(node: Node) -> bool:
    """True when ``node`` is a section (§) instance.

    Accepts the bare ``estleg:LegalProvision``, the municipal
    ``estleg:KovProvision``, and the legacy per-law
    ``estleg:LegalProvision_<abbrev>`` subclass. A node carrying two of those
    at once is still one provision.
    """
    return _types_are_provision(_types_of(node))


def provision_nodes(graph: Graph) -> list[Node]:
    """Return all provision (§) nodes of a law graph.

    A provision is any node whose ``@type`` marks it as one -- see
    :func:`_is_provision` for the accepted classes. Every provision-backed tool
    filters through here, so :func:`provision_detection_check` guards this
    against a silent upstream retype.
    """
    return [n for n in graph if _is_provision(n)]


def chapter_nodes(graph: Graph) -> list[Node]:
    """Return all chapter (peatükk) nodes of a law graph."""
    return [n for n in graph if "estleg:Chapter" in _types_of(n)]


def provision_detection_check() -> dict[str, Any]:
    """Self-test that the corpus's § nodes are still detected as provisions (#678).

    ``get_provision``, ``provision_history``, ``who_references``,
    ``references_of``, ``court_decisions_for_law``,
    ``competent_authority_for_law`` and every ``num_provisions`` count filter
    the law graph through :func:`provision_nodes`. A generator change that
    retypes § nodes breaks no import and loses no file -- the tools simply go
    quiet and answer "none" with full confidence. Counting the Penal Code's
    sections is a cheap canary for that, checked at boot by ``server.main()``
    and reported by :func:`layers_available`.

    Returns ``{"law", "abbrev", "provisions", "ok"}``. ``ok`` is False when the
    reference law cannot be resolved, its files are unreadable, or not one node
    in its graph is recognised as a provision.
    """
    law, abbrev = "karistusseadustik", "KarS"
    try:
        rec = resolve_law(abbrev)
        if rec is None:
            return {"law": law, "abbrev": abbrev, "provisions": 0, "ok": False}
        count = len(provision_nodes(load_law_graph(rec)))
    except OSError:  # includes FileNotFoundError from corpus_root()
        return {"law": law, "abbrev": abbrev, "provisions": 0, "ok": False}
    return {
        "law": rec.name,
        "abbrev": display_abbrev(rec) or abbrev,
        "provisions": count,
        "ok": count > 0,
    }


# ---------------------------------------------------------------------------
# Citation derivation
# ---------------------------------------------------------------------------
def act_title(node: Node | None) -> str:
    """Best title for an act node: ``dcterms:title`` then ``rdfs:label``.

    Used to enrich tool output for laws whose slug-derived fallback title is
    poor (e.g. truncated international-convention slugs absent from the
    abbreviation registry). Reads only the already-loaded act node, so it adds
    no extra file I/O beyond the law graph the caller already has.
    """
    if not isinstance(node, dict):
        return ""
    return _text(node.get("dcterms:title")) or _text(node.get("rdfs:label"))


def act_kehtiv_date(node: Node | None) -> str:
    """The consolidated-text currency date (``estleg:kehtiv``) of an act node.

    This is the "as of" date of the consolidated text the ontology captured,
    not an in-force flag. Useful context when ``estleg:temporalStatus`` is
    ``"unknown"`` (a known upstream data gap for some laws). Returns "" if absent.
    """
    if not isinstance(node, dict):
        return ""
    val = node.get("estleg:kehtiv")
    if isinstance(val, dict):
        inner = val.get("@value")
        return inner if isinstance(inner, str) else ""
    return val if isinstance(val, str) else ""


_RT_HOST = "riigiteataja.ee"


def _host_of(url: str) -> str:
    """Lower-cased hostname of an absolute URL ("" for a CURIE or a bare string)."""
    netloc = urlsplit(url).netloc
    return netloc.rpartition("@")[2].partition(":")[0].strip().lower().rstrip(".")


def _is_rt_host(url: str) -> bool:
    """True when ``url`` is served by riigiteataja.ee or a subdomain of it."""
    host = _host_of(url)
    return host == _RT_HOST or host.endswith("." + _RT_HOST)


def rt_url(node: Node | None) -> str:
    """Derive the human riigiteataja.ee URL for an act / law / regulation node.

    Reads ``dcterms:source`` ({"@id": "https://www.riigiteataja.ee/akt/<id>.xml"})
    and strips the trailing ``.xml`` to yield the human-facing act URL; an
    ``owl:sameAs`` is accepted only as a fallback.

    The returned URL is **always** on riigiteataja.ee (issue #680). Some acts
    carry no ``dcterms:source`` at all and only an ``owl:sameAs`` Wikidata IRI
    (KarS, VÕS), which the old fallback happily returned under a field every
    tool documents as *the official riigiteataja.ee URL*. Those now yield ""
    -- an honest gap -- and the Wikidata IRI is surfaced by
    :func:`external_ids` instead. ``dc:source`` holds the act title, not a URL,
    and is deliberately not consulted.
    """
    if not isinstance(node, dict):
        return ""
    for key in ("dcterms:source", "owl:sameAs"):
        for iri in _ids_of(node.get(key)):
            if not _is_rt_host(iri):
                continue
            return iri[: -len(".xml")] if iri.endswith(".xml") else iri
    return ""


def _external_id_family(url: str) -> str:
    """Short key for an external identifier IRI (``wikidata`` for wikidata.org)."""
    host = _host_of(url)
    if host.startswith("www."):
        host = host[4:]
    return host.split(".")[0]


def external_ids(node: Node | None) -> dict[str, str]:
    """Identifiers an act node links itself to outside riigiteataja.ee (#680).

    :func:`rt_url` now returns riigiteataja.ee URLs only, so the ``owl:sameAs``
    IRIs a few acts carry would otherwise be dropped entirely -- including the
    two (KarS, VÕS) that used to leak a wikidata.org URL into ``rt_url``. They
    are returned here keyed by host family, e.g.
    ``{"wikidata": "http://www.wikidata.org/entity/Q2352833"}``, with the first
    IRI per family winning.

    No ELI identifier is emitted: no act node in this corpus carries an
    ``estleg:eli`` predicate, and ``eli:is_about`` holds EuroVoc subject IRIs
    (already surfaced as ``eurovoc_subjects``) rather than an identifier for
    the act itself. Returns ``{}`` for a missing node or one with nothing
    external.
    """
    if not isinstance(node, dict):
        return {}
    out: dict[str, str] = {}
    for iri in _ids_of(node.get("owl:sameAs")):
        if _is_rt_host(iri):
            continue
        family = _external_id_family(iri)
        if family:
            out.setdefault(family, iri)
    return out


@lru_cache(maxsize=256)
def rt_url_for_slug(slug: str) -> str:
    """Cached riigiteataja.ee URL for a law by slug.

    Loads the law's act node once per slug and caches the derived URL, so a
    whole-law reference scan with many cross-law targets does not re-read the
    same referenced law's files repeatedly.
    """
    rec = _records_by_slug().get(slug)
    if rec is None:
        return ""
    return rt_url(act_node(load_law_graph(rec)))


def eurlex_url(celex: str) -> str:
    """Build the EUR-Lex URL for a CELEX number."""
    celex = (celex or "").strip()
    if not celex:
        return ""
    return f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}"


def normalize_celex(raw: str) -> str:
    """Normalise a CELEX query the same way the EU tools accept one.

    Strips surrounding whitespace and internal spaces, drops a leading
    ``CELEX:`` wrapper, and uppercases. ``32000 L0060`` and ``celex:32000l0060``
    both become ``32000L0060``. Returns ``""`` for an empty input.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    if text[:6].upper() == "CELEX:":
        text = text[6:]
    return text.upper()


# ---------------------------------------------------------------------------
# Provision lookup
# ---------------------------------------------------------------------------
_SUPERSCRIPT_MAP = {
    "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
    "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁰": "0",
}


def _normalise_paragraph(raw: str) -> str:
    """Normalise a paragraph reference (query OR stored value) to a digit-key.

    Produces the same key for every spelling of a section number, including the
    superscripted "ordinal" sections (corpus ``estleg:paragrahv`` stores these
    as HTML, e.g. ``§ 133<sup>1</sup>.``; users may type ``§ 133¹`` or
    ``§ 133-1``). The superscript boundary becomes ``_``:

    * "§ 22", "§22.", "22", "Par 22"        -> "22"
    * "§ 133<sup>1</sup>.", "§ 133¹", "133-1", "133_1" -> "133_1"

    Plain runs of digits with no superscript marker stay concatenated, so
    "133 1" -> "1331" (intentionally distinct from the ordinal "133_1").
    """
    text = _nfc(raw).strip().lower()
    # HTML superscript wrappers -> a "^" boundary before the ordinal digits.
    text = text.replace("<sup>", "^").replace("</sup>", "")
    # Unicode superscripts -> "^" + the plain digit.
    for sup, digit in _SUPERSCRIPT_MAP.items():
        text = text.replace(sup, f"^{digit}")
    # Explicit user separators between the section number and its ordinal.
    for sep in ("-", "‑", "_", "/"):
        text = text.replace(sep, "^")
    out_chars: list[str] = []
    for ch in text:
        if ch.isdigit() or ch == "^":
            out_chars.append(ch)
    # Collapse repeated/edge "^" so "133^^1" / "^133" normalise cleanly.
    token = "".join(out_chars).strip("^")
    while "^^" in token:
        token = token.replace("^^", "^")
    return token.replace("^", "_")


def find_provision(graph: Graph, paragraph: str) -> Node | None:
    """Find a single provision node by paragraph number.

    Matches the corpus ``estleg:paragrahv`` ("§ 13.") or ``rdfs:label`` against
    a flexible query: "§ 13", "13", "§13.", "§ 22¹" all work. Returns the first
    match, or ``None``.
    """
    target = _normalise_paragraph(paragraph)
    if not target:
        return None
    for node in provision_nodes(graph):
        par = _text(node.get("estleg:paragrahv"))
        if par and _normalise_paragraph(par) == target:
            return node
    # Fallback: try the label (some provisions carry the § only in the label).
    for node in provision_nodes(graph):
        label = _text(node.get("rdfs:label"))
        if label:
            norm = _normalise_paragraph(label.split()[0] if label.split() else "")
            if norm and norm == target:
                return node
    return None


_SUP_DISPLAY = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
}


def clean_display(text: str) -> str:
    """Render corpus HTML ``<sup>N</sup>`` markers as Unicode superscripts.

    The corpus stores ordinal sections as ``§ 133<sup>1</sup>.``; for a clean,
    chat-friendly display we turn those into ``§ 133¹.`` rather than leaking raw
    markup. Unknown/other tags are left untouched (the corpus text is otherwise
    plain).
    """
    if "<sup>" not in text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("<sup>", i):
            j = text.find("</sup>", i)
            if j != -1:
                inner = text[i + 5 : j]
                out.append("".join(_SUP_DISPLAY.get(c, c) for c in inner))
                i = j + 6
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def provision_label(node: Node) -> str:
    """Human label for a provision: prefer ``rdfs:label`` then ``paragrahv``."""
    label = _text(node.get("rdfs:label")) or _text(node.get("estleg:paragrahv"))
    return clean_display(label)


# ---------------------------------------------------------------------------
# Cross-corpus IRI -> readable-name helpers
# ---------------------------------------------------------------------------
def _law_slug_from_iri(iri: str) -> str | None:
    """Best-effort: map an internal provision/act IRI to a known law slug.

    Provision IRIs are prefixed with the law's *registry* abbreviation
    (e.g. ``estleg:KARIST_2_Osa1_Par_13`` -> ``KARIST_2`` -> karistusseadustik).
    Uses a longest-prefix match against the registry-abbrev table, requiring the
    match to be followed by ``_`` (or to equal the whole body) so ``KARIST_2``
    does not spuriously match ``KARIST_20``. Returns ``None`` when no abbrev
    prefixes the IRI (the reference is then surfaced by IRI with an empty url).
    """
    body = iri[len("estleg:"):] if iri.startswith("estleg:") else iri
    if not body:
        return None
    for abbrev, slug in _iri_prefix_to_slug():
        if body == abbrev or body.startswith(abbrev + "_"):
            return slug
    return None


# ---------------------------------------------------------------------------
# Court-decision lookup (lazy, cached across the whole riigikohus subcorpus)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _court_decision_index() -> dict[str, Node]:
    """Build a decision-IRI -> court-decision node map.

    Court IRIs encode the year inconsistently across the modern and legacy
    case-number formats, so we cannot target a single year file from an IRI.
    Instead we scan every ``riigikohus/riigikohus_<year>_peep.json`` once and
    cache the result for the process lifetime. Returns an empty dict if the
    subcorpus is absent.
    """
    out: dict[str, Node] = {}
    rk_dir = krr_dir() / "riigikohus"
    if not rk_dir.is_dir():
        return out
    for path in sorted(rk_dir.glob("riigikohus_*_peep.json")):
        for node in _graph_of(path):
            if "estleg:CourtDecision" not in _types_of(node):
                continue
            iri = node.get("@id")
            if isinstance(iri, str):
                out[iri] = node
    return out


def court_decision(iri: str) -> Node | None:
    """Resolve a court-decision IRI to its node, or ``None``."""
    return _court_decision_index().get(iri)


# ---------------------------------------------------------------------------
# Institution lookup (lazy, cached)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _institution_labels() -> dict[str, str]:
    """Build an institution-IRI -> human label map from the institutions dir."""
    out: dict[str, str] = {}
    inst_dir = krr_dir() / "institutions"
    if not inst_dir.is_dir():
        return out
    for path in sorted(inst_dir.glob("institution_*.json")):
        for node in _graph_of(path):
            if "estleg:Institution" not in _types_of(node):
                continue
            iri = node.get("@id")
            label = _text(node.get("rdfs:label"))
            if isinstance(iri, str) and label:
                out[iri] = label
    return out


def institution_label(iri: str) -> str:
    """Human label for an institution IRI, with an IRI-slug fallback.

    Prefers the ``rdfs:label`` from the institutions sidecar; if that subcorpus
    is missing, derives a readable name from the ``estleg:Institution_<slug>``
    IRI so the tool still returns something useful.
    """
    label = _institution_labels().get(iri)
    if label:
        return label
    prefix = "estleg:Institution_"
    if iri.startswith(prefix):
        return iri[len(prefix):].replace("_", " ").strip().capitalize()
    return iri


# ---------------------------------------------------------------------------
# Draft lookup (lazy, cached across the eelnoud subcorpus peep files)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _draft_index() -> dict[str, Node]:
    """Build a draft-IRI -> DraftLegislation node map from the eelnoud peeps.

    The draft links on law/act nodes (``estleg:affectedBy``) point at
    ``estleg:Draft_*`` IRIs whose full title/phase/link live in the three
    ``eelnoud/eelnoud_<phase>_peep.json`` files. We index those once.
    """
    out: dict[str, Node] = {}
    eel_dir = krr_dir() / "eelnoud"
    if not eel_dir.is_dir():
        return out
    for name in (
        "eelnoud_publicconsultation_peep.json",
        "eelnoud_review_peep.json",
        "eelnoud_submission_peep.json",
    ):
        for node in _graph_of(eel_dir / name):
            if "estleg:DraftLegislation" not in _types_of(node):
                continue
            iri = node.get("@id")
            if isinstance(iri, str):
                out[iri] = node
    return out


def _phase_label(node: Node) -> str:
    """Readable legislative-phase name from a draft node."""
    iri = _id_of(node.get("estleg:legislativePhase"))
    prefix = "estleg:Phase_"
    if iri.startswith(prefix):
        return iri[len(prefix):]
    return iri


def draft_info(iri: str) -> Node | None:
    """Resolve a draft IRI to its node, or ``None``."""
    return _draft_index().get(iri)


def _amendment_graph_for(record: LawRecord) -> Graph:
    """Concatenate the proposed-amendment sidecar graph for a law.

    Amendment chains live in ``amendments/amendments_<base-slug>.json`` where
    the base slug is the law's INDEX slug with any trailing ``_osaN`` stripped
    (the multi-part codes share one chain file). Mirrors the filename rule in
    ``scripts/generate_amendment_history._amendment_chain_path``.
    """
    base = krr_dir() / "amendments"
    if not base.is_dir():
        return []
    base_slug = re.sub(r"_osa\d+$", "", record.name)
    return _graph_of(base / f"amendments_{base_slug}.json")


def amendment_events(record: LawRecord, limit: int = 50) -> list[dict[str, str]]:
    """Effected ``AmendmentEvent`` rows from the law's amendments sidecar (#502)."""
    if limit <= 0:
        return []
    rows: list[dict[str, str]] = []
    for node in _amendment_graph_for(record):
        if "estleg:AmendmentEvent" not in _types_of(node):
            continue
        rows.append(
            {
                "event_id": _id_of(node) or "",
                "label": _text(node.get("rdfs:label")),
                "amendment_date": _text(node.get("estleg:amendmentDate")),
                "entry_into_force": _text(node.get("estleg:entryIntoForce")),
                "amends": _id_of(node.get("estleg:amends")),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def amendment_link_drafts(record: LawRecord) -> dict[str, str]:
    """Map each ``estleg:ProposedAmendment`` link IRI -> its amending Draft IRI.

    Law/act (map) nodes carry ``estleg:hasProposedAmendment`` pointing at
    ``estleg:ProposedAmendment`` *link* nodes (e.g.
    ``estleg:AmendmentLink_Draft_KLIM13_0996_KESKKO``) that live in the law's
    amendments sidecar; each link's ``estleg:amendingDraft`` is the
    ``estleg:Draft_*`` IRI resolvable via :func:`draft_info`. Returns ``{}``
    when the sidecar is absent.
    """
    out: dict[str, str] = {}
    for node in _amendment_graph_for(record):
        if "estleg:ProposedAmendment" not in _types_of(node):
            continue
        link_iri = node.get("@id")
        draft_iri = _id_of(node.get("estleg:amendingDraft"))
        if isinstance(link_iri, str) and draft_iri:
            out[link_iri] = draft_iri
    return out


# ---------------------------------------------------------------------------
# Sanctions lookup
# ---------------------------------------------------------------------------
def _sanction_graph_for(record: LawRecord) -> Graph:
    """Concatenate sanction sidecar graphs for a law.

    Sanction files are named ``sanctions/sanctions_<peep-stem>.json`` where the
    stem is the law's peep filename without the ``_peep.json`` suffix.
    """
    base = krr_dir() / "sanctions"
    if not base.is_dir():
        return []
    graph: Graph = []
    for fname in record.files:
        stem = fname[: -len("_peep.json")] if fname.endswith("_peep.json") else fname
        path = base / f"sanctions_{stem}.json"
        graph.extend(_graph_of(path))
    return graph


# ---------------------------------------------------------------------------
# Transposition (EU directive <-> Estonian law), both directions
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _transposition_mappings() -> list[Node]:
    """Return the ``mappings`` list from ``transposition_mapping.json``."""
    doc = _load_json(krr_dir() / "reports" / "transposition_mapping.json")
    if isinstance(doc, dict):
        mappings = doc.get("mappings")
        if isinstance(mappings, list):
            return [m for m in mappings if isinstance(m, dict)]
    return []


def transposition_matches(query: str) -> list[Node]:
    """Return transposition rows matching a CELEX number or law name/abbrev.

    Matches a row when the query (accent-folded) appears in the directive CELEX,
    the national title, or the matched law slug, OR when the query resolves to a
    law whose slug equals the row's ``matched_law_name``. Covers both query
    directions (EU -> EE and EE -> EU).
    """
    q = (query or "").strip()
    if not q:
        return []
    needle = _fold(q)
    rec = resolve_law(q)
    target_slug = rec.name if rec else None

    matches: list[Node] = []
    for row in _transposition_mappings():
        celex = _fold(_text(row.get("directive_celex")))
        title = _fold(_text(row.get("national_title")))
        slug = _text(row.get("matched_law_name"))
        slug_folded = _fold(slug)
        if (
            (needle and (needle in celex or needle in title or needle in slug_folded))
            or (target_slug is not None and slug == target_slug)
        ):
            matches.append(row)
    return matches


# ---------------------------------------------------------------------------
# Point-in-time provision versions (ticket #499)
#
# ``provision_versions/<base-slug>.jsonld`` holds, per law, the full redaction
# history of every section as ``estleg:ProvisionVersion`` nodes. Each version
# carries ``estleg:versionOf`` (the provision IRI -- the SAME IRI the law graph
# stamps on the section node), ``estleg:versionValidFrom`` /
# ``estleg:versionValidTo`` (xsd:date; ``versionValidTo`` is the *inclusive*
# last in-force day and is ABSENT for the current, still-in-force redaction),
# ``estleg:versionRedactionId`` and the untruncated ``estleg:versionText``. The
# file is keyed by the law's BASE slug, so the multi-part codes
# (``volaoigusseadus_osa1..10``) share one history file.
# ---------------------------------------------------------------------------
def _date_text(value: Any) -> str:
    """Extract an ISO date string from a JSON-LD ``xsd:date`` value (or "")."""
    if isinstance(value, dict):
        inner = value.get("@value")
        return inner if isinstance(inner, str) else ""
    return value if isinstance(value, str) else ""


def _base_slug(record: LawRecord) -> str:
    """The base slug shared by a multi-part law's per-law sidecar files.

    Strips a trailing ``_osaN`` so ``volaoigusseadus_osa1..10`` collapse to the
    single ``volaoigusseadus`` history file (mirrors the provision_versions and
    amendments file-naming rule, see :func:`_amendment_graph_for`).
    """
    return re.sub(r"_osa\d+$", "", record.name)


@lru_cache(maxsize=64)
def _provision_version_index(base_slug: str) -> dict[str, list[dict[str, Any]]]:
    """Map provision-IRI -> its chronologically sorted version timeline.

    Loads ``provision_versions/<base_slug>.jsonld`` once per law and caches it
    (the data layer reads sidecars per law rather than loading the whole
    corpus, so the 124k version nodes are never all held at once). Each timeline
    entry is a compact dict ``{id, redaction_id, valid_from, valid_to, text}``
    sorted by ``valid_from`` ascending; ``valid_to`` is "" for the still-in-force
    redaction. Returns ``{}`` when the law has no versions sidecar.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    path = krr_dir() / "provision_versions" / f"{base_slug}.jsonld"
    for node in _graph_of(path):
        if "estleg:ProvisionVersion" not in _types_of(node):
            continue
        prov_iri = _id_of(node.get("estleg:versionOf"))
        if not prov_iri:
            continue
        out.setdefault(prov_iri, []).append(
            {
                "id": node.get("@id", ""),
                "redaction_id": _text(node.get("estleg:versionRedactionId")),
                "valid_from": _date_text(node.get("estleg:versionValidFrom")),
                "valid_to": _date_text(node.get("estleg:versionValidTo")),
                "text": _text(node.get("estleg:versionText")),
            }
        )
    for versions in out.values():
        versions.sort(key=lambda v: v["valid_from"] or "")
    return out


def provision_version_timeline(
    record: LawRecord, provision_iri: str
) -> list[dict[str, Any]]:
    """The chronological version timeline for one provision IRI (or ``[]``)."""
    if not provision_iri:
        return []
    return _provision_version_index(_base_slug(record)).get(provision_iri, [])


def normalize_iso_date(value: str) -> str | None:
    """Normalise an ISO date to ``YYYY-MM-DD``; return ``None`` if not a date.

    Accepts any ``datetime.date``-parseable ISO string and re-emits the
    canonical ``YYYY-MM-DD`` form so it compares lexicographically (==
    chronologically) against the corpus's stored ``xsd:date`` strings.
    """
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def version_in_force_on(
    versions: list[dict[str, Any]], as_of: str
) -> dict[str, Any] | None:
    """Select the version in force on ``as_of`` from a sorted timeline (or None).

    A version is in force on ``as_of`` when ``valid_from <= as_of <= valid_to``,
    treating an absent ``valid_to`` as open-ended (the still-in-force redaction).
    The corpus stores ``valid_to`` as the inclusive last in-force day and the
    redactions tile contiguously (the next ``valid_from`` is the previous
    ``valid_to`` + 1 day), so exactly one version contains any date within the
    provision's recorded coverage; a date before the earliest redaction (or
    after a repeal) matches nothing and yields ``None``. ``as_of`` must already
    be normalised to ``YYYY-MM-DD`` (see :func:`normalize_iso_date`).
    """
    if not as_of:
        return None
    for version in versions:
        valid_from = version.get("valid_from") or ""
        valid_to = version.get("valid_to") or ""
        if valid_from and valid_from <= as_of and (not valid_to or as_of <= valid_to):
            return version
    return None


def law_version_index(record: LawRecord) -> dict[str, list[dict[str, Any]]]:
    """The full provision-IRI -> version-timeline index for a whole law (or {}).

    A law-level view over :func:`_provision_version_index`, used by
    ``get_law(as_of=...)`` to describe the act as it stood on a date. Returns
    ``{}`` for a law with no provision-versions sidecar.
    """
    return _provision_version_index(_base_slug(record))


def count_provisions_in_force(
    index: dict[str, list[dict[str, Any]]], as_of: str
) -> int:
    """How many of a law's provisions had a redaction in force on ``as_of``.

    The point-in-time section count for a law overview: a provision counts when
    :func:`version_in_force_on` selects one of its versions for ``as_of``
    (already normalised to ``YYYY-MM-DD``).
    """
    return sum(1 for versions in index.values() if version_in_force_on(versions, as_of))


def version_coverage_span(index: dict[str, list[dict[str, Any]]]) -> tuple[str, str]:
    """``(earliest valid_from, latest valid_to or 'present')`` across all versions.

    Describes a law's recorded version-history window so an ``as_of`` that falls
    outside it can say so. ``latest`` is ``"present"`` when any provision is
    still in force (an open-ended redaction); ``("", "")`` for an empty index.
    """
    earliest = ""
    latest = ""
    open_ended = False
    for versions in index.values():
        for version in versions:
            valid_from = version.get("valid_from") or ""
            if valid_from and (not earliest or valid_from < earliest):
                earliest = valid_from
            valid_to = version.get("valid_to") or ""
            if not valid_to:
                open_ended = True
            elif not latest or valid_to > latest:
                latest = valid_to
    return earliest, ("present" if open_ended else latest)


# ---------------------------------------------------------------------------
# Regulations (määrused): state (riik) + municipal (kov) -- ticket #500
#
# ``regulations/riik/*_peep.json`` (state regulations) and
# ``regulations/kov/<municipality>/*_peep.json`` (municipal regulations) each
# hold one regulation. Its act/map node (``estleg:Act`` + ``owl:Ontology``,
# @id ``estleg:Reg_<id>_Map_2026``) carries ``estleg:issuer`` (a plain string
# such as "Vabariigi Valitsus" / "Rahandusminister" / "<X> Vallavolikogu"),
# ``estleg:issuedUnder`` (the parent statute's map-node IRI(s)),
# ``estleg:implementsCitation`` (in-file ``estleg:Citation`` nodes whose
# ``estleg:citationText`` is the statutory basis), ``estleg:temporalStatus``,
# ``estleg:terviktekstId`` / ``estleg:globalId`` and ``dcterms:source`` (the
# riigiteataja .xml IRI). Sections are typed ``estleg:LegalProvision`` like a
# law's (municipal ones additionally ``estleg:KovProvision``); legacy files
# used a per-regulation ``estleg:Regulation_<id>`` subclass.
# ---------------------------------------------------------------------------
class RegulationRecord:
    """A resolved regulation: core metadata + parent-statute / issuer links."""

    __slots__ = (
        "reg_id",
        "global_id",
        "title",
        "issuer",
        "rt_url",
        "status",
        "slug",
        "file",
        "is_kov",
        "municipality",
        "issued_under",
        "citations",
        "num_provisions",
    )

    def __init__(
        self,
        *,
        reg_id: str,
        global_id: str,
        title: str,
        issuer: str,
        rt_url: str,
        status: str,
        slug: str,
        file: str,
        is_kov: bool,
        municipality: str,
        issued_under: list[str],
        citations: list[str],
        num_provisions: int,
    ) -> None:
        self.reg_id = reg_id
        self.global_id = global_id
        self.title = title
        self.issuer = issuer
        self.rt_url = rt_url
        self.status = status
        self.slug = slug
        self.file = file
        self.is_kov = is_kov
        self.municipality = municipality
        self.issued_under = issued_under
        self.citations = citations
        self.num_provisions = num_provisions

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RegulationRecord(reg_id={self.reg_id!r}, title={self.title!r})"


def _reg_slug_from_filename(name: str) -> str:
    """Title-slug of a regulation peep file (drop the ``_t<id>_peep.json`` tail)."""
    stem = name[: -len("_peep.json")] if name.endswith("_peep.json") else name
    return re.sub(r"_t\d+$", "", stem)


def _strip_map_suffix(body: str) -> str:
    """Drop a trailing ``_Map_<year>`` / ``_ProcedureMap_<year>`` / ``_Ontology…``."""
    return re.sub(r"(_Map_\d+|_ProcedureMap_\d+|_Ontology.*)$", "", body)


def _types_are_regulation_provision(types: list[str]) -> bool:
    """True when a regulation-file node's ``@type`` marks it as a section.

    Regulation peeps type their sections exactly like a law's since issue #434
    (bare ``estleg:LegalProvision``, plus ``estleg:KovProvision`` on municipal
    ones); older files used a per-regulation ``estleg:Regulation_<id>``
    subclass. A KOV section carries two of those classes at once, so this is a
    per-node predicate and never double-counts.
    """
    return _types_are_provision(types) or any(
        t.startswith("estleg:Regulation_") for t in types
    )


def _regulation_record_from_graph(
    graph: Graph, file_rel: str, municipality: str
) -> RegulationRecord | None:
    """Build a :class:`RegulationRecord` from one regulation file's graph.

    The act/map node is the single ``estleg:Act`` node in a regulation file;
    in-file ``estleg:Citation`` nodes supply the ``implementsCitation`` texts and
    the section nodes are counted, not loaded (see
    :func:`_types_are_regulation_provision` for the classes that count).
    Returns ``None`` for a file with no act node.
    """
    map_node: Node | None = None
    citation_text: dict[str, str] = {}
    num_provisions = 0
    for node in graph:
        types = _types_of(node)
        if "estleg:Act" in types:
            map_node = node
        elif "estleg:Citation" in types:
            cid = node.get("@id")
            if isinstance(cid, str):
                citation_text[cid] = _text(node.get("estleg:citationText"))
        if _types_are_regulation_provision(types):
            num_provisions += 1
    if map_node is None:
        return None
    citations = [
        t
        for c in _ids_of(map_node.get("estleg:implementsCitation"))
        if (t := citation_text.get(c, ""))
    ]
    title = _text(map_node.get("dc:source")) or _text(map_node.get("rdfs:label"))
    return RegulationRecord(
        reg_id=_text(map_node.get("estleg:terviktekstId")),
        global_id=_text(map_node.get("estleg:globalId")),
        title=title,
        issuer=_text(map_node.get("estleg:issuer")),
        rt_url=rt_url(map_node),
        status=_text(map_node.get("estleg:temporalStatus")),
        slug=_reg_slug_from_filename(Path(file_rel).name),
        file=file_rel,
        is_kov=bool(municipality),
        municipality=municipality,
        issued_under=_ids_of(map_node.get("estleg:issuedUnder")),
        citations=citations,
        num_provisions=num_provisions,
    )


@lru_cache(maxsize=1)
def _regulation_records() -> list[RegulationRecord]:
    """Scan every regulation file once into a cached list of records.

    Reads ``regulations/riik/*_peep.json`` and
    ``regulations/kov/<municipality>/*_peep.json`` (the whole määrus subcorpus,
    ~15k files) a single time, mirroring how :func:`_court_decision_index` scans
    the riigikohus subcorpus once. Files are visited in sorted order so every
    derived list is deterministic. Returns ``[]`` when the subcorpus is absent.
    """
    base = krr_dir() / "regulations"
    records: list[RegulationRecord] = []
    riik = base / "riik"
    if riik.is_dir():
        for path in sorted(riik.glob("*_peep.json")):
            rec = _regulation_record_from_graph(
                _graph_of(path), str(path.relative_to(krr_dir())), ""
            )
            if rec is not None:
                records.append(rec)
    kov = base / "kov"
    if kov.is_dir():
        for path in sorted(kov.glob("*/*_peep.json")):
            rec = _regulation_record_from_graph(
                _graph_of(path), str(path.relative_to(krr_dir())), path.parent.name
            )
            if rec is not None:
                records.append(rec)
    return records


@lru_cache(maxsize=1)
def _law_map_prefix_to_slug() -> dict[str, str]:
    """Map each law's act-node IRI prefix -> its slug.

    A regulation's ``estleg:issuedUnder`` points at the parent statute's map
    node, e.g. ``estleg:KOKS_Map_2026`` / ``estleg:kohanimeseadus_Map_2026``.
    That prefix is the law's act-node prefix, which is NOT always the registry
    abbreviation (``KOKS`` is absent from the registry; some laws use the raw
    slug as the prefix). We read each law's act node once and index
    ``<prefix> -> slug`` so :func:`_law_slug_from_issued_under` resolves the link
    reliably (first-writer-wins on a prefix collision).
    """
    out: dict[str, str] = {}
    for slug, rec in _records_by_slug().items():
        act = act_node(load_law_graph(rec))
        aid = act.get("@id", "") if act else ""
        if isinstance(aid, str) and aid.startswith("estleg:"):
            prefix = _strip_map_suffix(aid[len("estleg:") :])
            if prefix:
                out.setdefault(prefix, slug)
    return out


@lru_cache(maxsize=2048)
def _law_slug_from_issued_under(iri: str) -> str | None:
    """Resolve an ``issuedUnder`` map-node IRI to a parent-law slug (or None).

    Tries, in order: the registry-abbreviation longest-prefix match
    (:func:`_law_slug_from_iri`), the law act-node prefix index
    (:func:`_law_map_prefix_to_slug`), then :func:`resolve_law` on the bare
    prefix (which catches human abbreviations such as ``KOKS`` and raw-slug
    prefixes). Returns ``None`` for an ``issuedUnder`` that targets another
    regulation (``estleg:Reg_…``) rather than a statute.
    """
    slug = _law_slug_from_iri(iri)
    if slug:
        return slug
    body = iri[len("estleg:") :] if iri.startswith("estleg:") else iri
    if body.startswith("Reg_"):
        return None
    prefix = _strip_map_suffix(body)
    mapped = _law_map_prefix_to_slug().get(prefix)
    if mapped:
        return mapped
    rec = resolve_law(prefix)
    return rec.name if rec else None


@lru_cache(maxsize=1)
def _regulations_by_law() -> dict[str, list[RegulationRecord]]:
    """Reverse index: parent-law slug -> regulations issued under it."""
    out: dict[str, list[RegulationRecord]] = {}
    for rec in _regulation_records():
        slugs = {
            s for iri in rec.issued_under if (s := _law_slug_from_issued_under(iri))
        }
        for slug in slugs:
            out.setdefault(slug, []).append(rec)
    return out


@lru_cache(maxsize=1)
def _regulations_by_issuer() -> dict[str, list[RegulationRecord]]:
    """Index: accent-folded issuer name -> that issuer's regulations."""
    out: dict[str, list[RegulationRecord]] = {}
    for rec in _regulation_records():
        if rec.issuer:
            out.setdefault(_fold(rec.issuer), []).append(rec)
    return out


@lru_cache(maxsize=1)
def _regulations_by_key() -> dict[str, RegulationRecord]:
    """Resolver index: id / slug / folded-title -> first matching regulation."""
    out: dict[str, RegulationRecord] = {}

    def put(key: str, rec: RegulationRecord) -> None:
        key = (key or "").strip()
        if key and key not in out:
            out[key] = rec

    for rec in _regulation_records():
        put(rec.reg_id, rec)
        put(rec.global_id, rec)
        put(rec.slug.lower(), rec)
        put(_fold(rec.slug), rec)
        put(_fold(rec.title), rec)
    return out


def regulations_for_law(law_slug: str) -> list[RegulationRecord]:
    """Regulations issued under / implementing the statute with ``law_slug``."""
    return _regulations_by_law().get(law_slug, [])


def resolve_regulation(query: str) -> RegulationRecord | None:
    """Resolve a free-text regulation reference (title / slug / id) to a record.

    Matches an exact id (``terviktekstId`` / ``globalId``, with or without a
    leading ``t``), the corpus slug, or the accent-folded title. Returns
    ``None`` when nothing matches.
    """
    if not query or not query.strip():
        return None
    q = query.strip()
    idx = _regulations_by_key()
    for key in (q, q.lower(), _fold(q)):
        rec = idx.get(key)
        if rec is not None:
            return rec
    m = re.fullmatch(r"[tT](\d+)", q)
    if m:
        return idx.get(m.group(1))
    return None


def regulations_by_issuer(institution: str) -> list[RegulationRecord]:
    """Regulations issued by ``institution`` (exact folded match, else substring).

    Prefers an exact accent-folded issuer match (the institution names are
    canonical strings in the corpus, e.g. "Vabariigi Valitsus"); when none match
    exactly, falls back to a substring scan over the issuer names so a partial
    like "Rahandusmin" still discovers the ministry. Returns ``[]`` when nothing
    matches.
    """
    if not institution or not institution.strip():
        return []
    needle = _fold(institution.strip())
    by_issuer = _regulations_by_issuer()
    exact = by_issuer.get(needle)
    if exact:
        return list(exact)
    matched: list[RegulationRecord] = []
    for folded, regs in by_issuer.items():
        if needle in folded:
            matched.extend(regs)
    return matched


# ---------------------------------------------------------------------------
# Overlay sidecars (#540) -- concepts + per-CELEX harmonisation
#
# MCP already reads sanctions / amendments / regulations / provision_versions
# per law. These two extra loaders open the remaining high-value sidecars
# *when asked* (one concepts file, or one harm_<celex>.json) instead of
# merging the whole enrichment tree.
# ---------------------------------------------------------------------------
def overlay_path(layer: str, key: str = "") -> Path | None:
    """Resolve a sidecar overlay file. Unknown layer or empty key -> ``None``."""
    name = (layer or "").strip().lower()
    if name == "concepts":
        return krr_dir() / "concepts" / "concepts_combined.jsonld"
    if name in {"harmonisation", "harmonization"}:
        celex = normalize_celex(key)
        if not celex:
            return None
        return (
            krr_dir()
            / "harmonisation"
            / "harmonisation_by_directive"
            / f"harm_{celex}.json"
        )
    return None


@lru_cache(maxsize=64)
def overlay_graph(layer: str, key: str = "") -> tuple[Node, ...]:
    """``@graph`` of an overlay sidecar, or ``()`` when the file is missing."""
    path = overlay_path(layer, key)
    if path is None:
        return ()
    return tuple(_graph_of(path))


def harmonisation_for_directive(celex: str, limit: int = 20) -> list[dict[str, str]]:
    """Member-state / EE harmonisation rows for one directive CELEX (#540).

    Opens ``harmonisation/harmonisation_by_directive/harm_<celex>.json`` via
    :func:`overlay_graph`. Returns ``[]`` when the CELEX is empty, ``limit``
    is not positive, or no harm file exists.
    """
    if limit <= 0:
        return []
    needle = normalize_celex(celex)
    if not needle:
        return []
    rows: list[dict[str, str]] = []
    for node in overlay_graph("harmonisation", needle):
        if "estleg:HarmonisationLink" not in _types_of(node):
            continue
        rows.append(
            {
                "id": node.get("@id", "") if isinstance(node.get("@id"), str) else "",
                "label": _text(node.get("rdfs:label")),
                "member_state": _text(node.get("estleg:memberStateCode")),
                "national_celex": _text(node.get("estleg:nationalCelex")),
                "harmonises": " ".join(_ids_of(node.get("estleg:harmonises"))),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def layers_available() -> list[dict[str, str]]:
    """Which ``krr_outputs/`` sidecars the MCP surface reads (#540).

    ``status`` is ``wired`` (a tool reads it), ``loadable`` (opened on demand
    via :func:`overlay_graph`), or ``excluded`` (documented gap). ``present``
    is ``yes``/``no`` for the on-disk path in this clone.
    """
    krr = krr_dir()

    def _present(rel: str) -> str:
        path = krr / rel
        if any(ch in rel for ch in "*?"):
            return "yes" if path.parent.is_dir() and any(path.parent.glob(path.name)) else "no"
        if path.is_dir():
            return "yes" if any(path.iterdir()) else "no"
        return "yes" if path.is_file() else "no"

    rows = (
        (
            "laws",
            "*_peep.json",
            "INDEX.json",
            "wired",
            "search_laws, get_law, get_provision",
            "Per-law peeps; never combined_ontology.jsonld",
        ),
        (
            "concepts",
            "concepts/concepts_combined.jsonld",
            "concepts/concepts_combined.jsonld",
            "wired",
            "define_term",
            "LegalConcept / Concept prefLabels via overlay_graph",
        ),
        (
            "harmonisation",
            "harmonisation/harmonisation_by_directive/harm_*.json",
            "harmonisation/harmonisation_by_directive/harm_*.json",
            "loadable",
            "harmonisation_for_directive",
            "One harm_<celex>.json when asked; not a full-layer scan",
        ),
        (
            "sanctions",
            "sanctions/sanctions_*.json",
            "sanctions",
            "wired",
            "sanctions_for_law",
            "",
        ),
        (
            "amendments",
            "amendments/amendments_*.json",
            "amendments",
            "wired",
            "amendment_history, drafts_affecting_law",
            "",
        ),
        (
            "regulations",
            "regulations/riik + regulations/kov",
            "regulations",
            "wired",
            "regulations_for_law, get_regulation, regulations_by_issuer",
            "",
        ),
        (
            "provision_versions",
            "provision_versions/*.jsonld",
            "provision_versions",
            "wired",
            "get_provision(as_of), provision_history, get_law(as_of)",
            "",
        ),
        (
            "riigikohus",
            "riigikohus/riigikohus_*_peep.json",
            "riigikohus",
            "wired",
            "court_decisions_for_law",
            "",
        ),
        (
            "curia",
            "curia/curia_*_peep.json",
            "curia",
            "wired",
            "eu_case_law_for_directive",
            "No interprets edges (#418); CELEX substring match",
        ),
        (
            "eelnoud",
            "eelnoud/eelnoud_*_peep.json",
            "eelnoud",
            "wired",
            "drafts_affecting_law",
            "",
        ),
        (
            "institutions",
            "institutions/institution_*.json",
            "institutions",
            "wired",
            "competent_authority_for_law",
            "",
        ),
        (
            "transposition",
            "reports/transposition_mapping.json",
            "reports/transposition_mapping.json",
            "wired",
            "transposition",
            "",
        ),
        (
            "eurlex",
            "eurlex/*_peep.json",
            "eurlex",
            "excluded",
            "",
            "Reached via transposition + CELEX URLs, not the eurlex peeps",
        ),
        (
            "annotations",
            "annotations/",
            "annotations",
            "excluded",
            "",
            "MCP does not load the annotations sidecar",
        ),
        (
            "similarity",
            "reports/similarity_index.json",
            "reports/similarity_index.json",
            "excluded",
            "",
            "Git LFS pointer; no semantic-search tool in v1",
        ),
        (
            "combined_ontology",
            "combined_ontology.jsonld",
            "combined_ontology.jsonld",
            "excluded",
            "",
            "Git LFS; MCP never loads the aggregate graph",
        ),
        (
            "concept_crossref",
            "concepts/concept_crossref_report.json",
            "concepts/concept_crossref_report.json",
            "excluded",
            "",
            "Build report only; define_term reads concepts_combined",
        ),
    )
    out: list[dict[str, str]] = []
    for layer, display, check, status, tools, note in rows:
        out.append(
            {
                "layer": layer,
                "path": f"krr_outputs/{display}",
                "status": status,
                "tools": tools,
                "present": _present(check),
                "note": note,
            }
        )
    # Not a sidecar but a runtime invariant, reported in the same table: the
    # provision-backed tools all filter through provision_nodes(), so an
    # upstream retype of the § class empties them without any file going
    # missing (issue #678). ``present`` is the live detection result, not a
    # path test, so the regression is visible from the tool itself.
    detection = provision_detection_check()
    out.insert(
        1,
        {
            "layer": "provision_detection",
            "path": "krr_outputs/*_peep.json (§ node @type)",
            "status": "wired",
            "tools": (
                "get_provision, provision_history, who_references, "
                "references_of, court_decisions_for_law, "
                "competent_authority_for_law"
            ),
            "present": "yes" if detection["ok"] else "no",
            "note": (
                f"{detection['abbrev']} resolves {detection['provisions']} "
                "§ nodes"
                + ("" if detection["ok"] else "; § @type changed upstream (#678)")
            ),
        },
    )
    return out


# ---------------------------------------------------------------------------
# CURIA decisions for a directive CELEX (#505)
# ---------------------------------------------------------------------------
def _curia_peep_paths() -> list[Path]:
    """Committed CURIA peeps (not the combined / schema / INDEX artifacts)."""
    curia = krr_dir() / "curia"
    if not curia.is_dir():
        return []
    return sorted(curia.glob("curia_*_peep.json"))


@lru_cache(maxsize=1)
def _curia_decisions() -> tuple[Node, ...]:
    """All ``estleg:EUCourtDecision`` nodes from the CURIA peeps.

    Cached for the process lifetime. Tests should monkeypatch this (or
    :func:`_curia_peep_paths`) rather than parse the ~22k-decision corpus.
    """
    out: list[Node] = []
    for path in _curia_peep_paths():
        for node in _graph_of(path):
            if "estleg:EUCourtDecision" in _types_of(node):
                out.append(node)
    return tuple(out)


def _node_mentions_celex(node: Node, celex: str) -> bool:
    """True when label / celexNumber / sameAs / source / text mention ``celex``.

    CURIA has no ``estleg:interprets`` edges (#418), so a CELEX substring on
    those fields is the join the MCP surface can actually evaluate.
    """
    if not celex:
        return False
    needle = celex.casefold()
    for field in (
        "estleg:celexNumber",
        "rdfs:label",
        "estleg:legalText",
        "estleg:summary",
        "dcterms:title",
        "dc:description",
    ):
        if needle in _text(node.get(field)).casefold():
            return True
    for field in ("owl:sameAs", "dcterms:source", "dc:source"):
        if needle in _text(node.get(field)).casefold():
            return True
        for iri in _ids_of(node.get(field)):
            if needle in iri.casefold():
                return True
    return False


def _curia_or_eurlex_url(node: Node) -> str:
    """Prefer ``estleg:eurLexLink``, then legacy ``estleg:curiaLink``, else CELEX URL."""
    for field in ("estleg:eurLexLink", "estleg:curiaLink"):
        link = _text(node.get(field)) or _id_of(node.get(field))
        if link:
            return link
    own = _text(node.get("estleg:celexNumber"))
    return eurlex_url(own) if own else ""


def eu_case_law_for_directive(celex: str, limit: int = 20) -> list[dict[str, str]]:
    """CURIA decisions whose metadata mentions a directive CELEX (#505).

    Empty CELEX or ``limit`` <= 0 yields ``[]`` (the server wraps a miss as
    ``[{note}]``). Hits are ``{ecli_or_celex, title, curia_or_eurlex_url}``.
    """
    if limit <= 0:
        return []
    needle = normalize_celex(celex)
    if not needle:
        return []
    cap = max(0, int(limit))
    rows: list[dict[str, str]] = []
    for node in _curia_decisions():
        if not _node_mentions_celex(node, needle):
            continue
        ecli = _text(node.get("estleg:ecliIdentifier"))
        own_celex = _text(node.get("estleg:celexNumber"))
        rows.append(
            {
                "ecli_or_celex": ecli or own_celex,
                "title": _text(node.get("rdfs:label")) or _text(node.get("dcterms:title")),
                "curia_or_eurlex_url": _curia_or_eurlex_url(node),
            }
        )
        if len(rows) >= cap:
            break
    return rows


__all__ = [
    "Graph",
    "LawRecord",
    "Node",
    "RegulationRecord",
    "_law_slug_from_iri",
    "act_kehtiv_date",
    "act_node",
    "act_title",
    "amendment_events",
    "amendment_link_drafts",
    "chapter_nodes",
    "clean_display",
    "corpus_root",
    "count_provisions_in_force",
    "court_decision",
    "define_term",
    "display_abbrev",
    "draft_info",
    "eu_case_law_for_directive",
    "eurlex_url",
    "find_provision",
    "harmonisation_for_directive",
    "institution_label",
    "krr_dir",
    "law_version_index",
    "laws_for_subject",
    "layers_available",
    "load_law_graph",
    "normalize_celex",
    "normalize_iso_date",
    "ontology_version",
    "overlay_graph",
    "overlay_path",
    "provision_label",
    "provision_nodes",
    "provision_version_timeline",
    "regulations_by_issuer",
    "regulations_for_law",
    "resolve_law",
    "resolve_regulation",
    "rt_url",
    "rt_url_for_slug",
    "search_law_records",
    "transposition_matches",
    "version_coverage_span",
    "version_in_force_on",
]
