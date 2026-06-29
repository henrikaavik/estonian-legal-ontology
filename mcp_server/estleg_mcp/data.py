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
* Provision node ``@type`` contains a per-law subclass with prefix
  ``estleg:LegalProvision_`` (NOT the bare ``estleg:LegalProvision``); carries
  ``estleg:paragrahv`` ("§ 1."), ``rdfs:label``, ``estleg:summary``,
  ``estleg:legalText``, ``estleg:references``, ``estleg:referencedBy``,
  ``estleg:interpretedBy`` (court-decision IRIs), ``estleg:competentAuthority``
  (institution IRIs), ``estleg:hasSanction``, ``estleg:normativeType``.
* Subsection node ``@type`` contains ``estleg:Subsection``.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

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
        # layer reads: they expose zero ``estleg:LegalProvision_*`` provisions
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


def search_law_records(query: str, limit: int = 10) -> list[LawRecord]:
    """Return laws whose title, abbreviation, or slug contains ``query``.

    Substring match is accent-insensitive and also covers the *conventional*
    human abbreviation (``KarS``, ``VÕS``, ``TLS``, ``PS``, ...), which the
    registry does not store -- so ``search_laws`` honours the same title/
    abbreviation contract as ``resolve_law``. Results are sorted with exact
    title/abbrev matches first, then by title. An empty ``query`` returns ``[]``.
    """
    if not query or not query.strip():
        return []
    needle = _fold(query.strip())
    records = _records_by_slug()
    human = _slug_to_human_abbrev()
    scored: list[tuple[int, str, LawRecord]] = []
    for rec in records.values():
        hay_title = _fold(rec.title)
        hay_abbrev = _fold(rec.abbrev) if rec.abbrev else ""
        hay_human = _fold(human.get(rec.name, ""))
        hay_slug = _fold(rec.name)
        if (
            needle in hay_title
            or needle in hay_abbrev
            or (hay_human and needle in hay_human)
            or needle in hay_slug
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


def _is_provision(node: Node) -> bool:
    return any(t.startswith("estleg:LegalProvision_") for t in _types_of(node))


def provision_nodes(graph: Graph) -> list[Node]:
    """Return all provision (§) nodes of a law graph.

    A provision is any node whose ``@type`` carries a per-law subclass with the
    ``estleg:LegalProvision_`` prefix (the bare ``estleg:LegalProvision`` class
    is never stamped on instances).
    """
    return [n for n in graph if _is_provision(n)]


def chapter_nodes(graph: Graph) -> list[Node]:
    """Return all chapter (peatükk) nodes of a law graph."""
    return [n for n in graph if "estleg:Chapter" in _types_of(n)]


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


def rt_url(node: Node | None) -> str:
    """Derive the human riigiteataja.ee URL for an act/law node.

    Reads ``dcterms:source`` ({"@id": "https://www.riigiteataja.ee/akt/<id>.xml"})
    and strips the trailing ``.xml`` to yield the human-facing act URL. Falls
    back to ``owl:sameAs``. Returns "" when no resolvable source exists.
    """
    if not isinstance(node, dict):
        return ""
    src = _id_of(node.get("dcterms:source")) or _id_of(node.get("owl:sameAs"))
    if not src:
        return ""
    if src.endswith(".xml"):
        src = src[: -len(".xml")]
    return src


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
    doc = _load_json(krr_dir() / "transposition_mapping.json")
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


__all__ = [
    "LawRecord",
    "Node",
    "Graph",
    "corpus_root",
    "krr_dir",
    "resolve_law",
    "search_law_records",
    "display_abbrev",
    "_law_slug_from_iri",
    "load_law_graph",
    "act_node",
    "provision_nodes",
    "chapter_nodes",
    "find_provision",
    "provision_label",
    "clean_display",
    "act_title",
    "act_kehtiv_date",
    "rt_url",
    "rt_url_for_slug",
    "eurlex_url",
    "court_decision",
    "institution_label",
    "draft_info",
    "amendment_link_drafts",
    "transposition_matches",
]
