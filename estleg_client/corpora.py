"""Loaders and row iterators for regulations, court decisions, drafts and EU acts.

One ``load_*`` per corpus returns an ``rdflib.Graph`` for a single record; one
``iter_*`` per corpus yields typed rows straight from the JSON-LD files (no
rdflib parse, so a full pass over 22,832 drafts or 33,242 EU acts stays fast).

==================  ===============================  ==========================
corpus              files read                       keys ``load_*`` accepts
==================  ===============================  ==========================
regulations         ``regulations/{riik,kov}/``      RT terviktekst id
                    ``REGULATIONS_*_INDEX.json``      (``1057801``, ``t1057801``),
                    + peeps + sanctions sidecars     ``Reg_1057801``, RT global
                                                     id (``119032025005``),
                                                     title / slug substring
Riigikohus          ``riigikohus/riigikohus_<year>``  case number (``3-18-1432``
                    ``_peep.json``                   or ``3-18-1432/93``), ECLI,
                                                     ``RK_...`` IRI
drafts              ``eelnoud/eelnoud_combined``     EIS number (``JDM/26-0214``),
                    ``.jsonld`` (or the phase peeps)  ``Draft_...`` IRI, title
EU acts             ``eurlex/eurlex_*_peep.json``    CELEX (``32016R0679``),
                    (or ``eurlex_combined.jsonld``)   ``EU_<celex>`` IRI
==================  ===============================  ==========================

Regulations and Riigikohus decisions exist only as per-act / per-year peep
files, which a git checkout has and a ``fetch_corpus`` release download does
not; their loaders raise :class:`CorpusUnavailableError` there.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from rdflib import Graph

from estleg_client._corpus import CorpusUnavailableError, corpus_root, krr_dir
from estleg_client._jsonld import (
    as_list,
    compact,
    expand,
    graph_from_nodes,
    graph_nodes,
    is_real_file,
    parse_into,
    prefixes_of,
    read_document,
)
from estleg_client._shards import slugify
from estleg_client.load import NotFoundError
from estleg_client.rows import (
    DecisionRow,
    DictStore,
    DraftRow,
    EuActRow,
    RegulationRow,
    iter_store_rows,
)

__all__ = [
    "iter_court_decisions",
    "iter_drafts",
    "iter_eu_acts",
    "iter_regulations",
    "load_court_decision",
    "load_court_decisions",
    "load_draft",
    "load_eu_act",
    "load_for_compact_id",
    "load_regulation",
]

_PEEP_SUFFIX = "_peep.json"
_REG_SCOPES = {"riik": False, "kov": True}
_REG_ID_RE = re.compile(r"^(?:estleg:|https://w3id\.org/estleg/)?Reg_(\d+)(?:_.*)?$")
_TID_RE = re.compile(r"^t?(\d{4,9})$")
_GLOBAL_ID_RE = re.compile(r"^\d{12}$")
_FILE_TID_RE = re.compile(r"_t(\d+)_peep\.json$")
_ECLI_RE = re.compile(r"^ECLI:EE:RK:(\d{4}):", re.IGNORECASE)
_RK_YEAR_RE = re.compile(r"^riigikohus_(\d{4})_peep\.json$")
_CELEX_RE = re.compile(r"^(?:estleg:|https://w3id\.org/estleg/)?(?:EU_)?(\d[0-9A-Z()]+)$")
_EURLEX_FILE_BY_LETTER = {
    "R": "eurlex_regulations_peep.json",
    "L": "eurlex_directives_peep.json",
    "D": "eurlex_decisions_peep.json",
}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold().strip()


def _ambiguous(kind: str, key: str, candidates: list[str]) -> NotFoundError:
    preview = ", ".join(candidates[:5])
    extra = f" (and {len(candidates) - 5} more)" if len(candidates) > 5 else ""
    return NotFoundError(
        f"{key!r} matches {len(candidates)} {kind}; use an exact identifier. "
        f"Candidates: {preview}{extra}."
    )


def _missing(kind: str, where: Path) -> CorpusUnavailableError:
    return CorpusUnavailableError(
        f"No {kind} files under {where}. {kind.capitalize()} ship as per-record peep "
        "files in a git checkout of henrikaavik/estonian-legal-ontology; a "
        "fetch_corpus() release download carries only the combined aggregates."
    )


def _selection_graph(doc: Mapping[str, Any], nodes: Iterable[Mapping[str, Any]]) -> Graph:
    return graph_from_nodes(nodes, doc.get("@context") or {})


def _citations_for(doc: Mapping[str, Any], iris: set[str]) -> list[dict[str, Any]]:
    prefixes = prefixes_of(doc.get("@context") or {})
    out = []
    for node in graph_nodes(doc):
        for value in as_list(node.get("estleg:citationSource")):
            ref = value.get("@id") if isinstance(value, Mapping) else None
            if isinstance(ref, str) and expand(ref, prefixes) in iris:
                out.append(node)
                break
    return out


# ── regulations ──────────────────────────────────────────────────────────────


@lru_cache(maxsize=8)
def _regulation_index(root_key: str, scope: str) -> tuple[str, ...]:
    base = Path(root_key) / "regulations" / scope
    index = base / f"REGULATIONS_{scope.upper()}_INDEX.json"
    if index.is_file():
        payload = json.loads(index.read_text(encoding="utf-8"))
        files = payload.get("files") if isinstance(payload, dict) else None
        if isinstance(files, list):
            return tuple(f for f in files if isinstance(f, str) and f.endswith(_PEEP_SUFFIX))
    if base.is_dir():
        return tuple(sorted(str(p.relative_to(base)) for p in base.rglob(f"*{_PEEP_SUFFIX}")))
    return ()


def _regulation_files(krr: Path, kov: bool | None) -> list[Path]:
    files: list[Path] = []
    for scope, is_kov in _REG_SCOPES.items():
        if kov is not None and kov != is_kov:
            continue
        base = krr / "regulations" / scope
        files.extend(base / rel for rel in _regulation_index(str(krr), scope))
    return files


def _stem(path: Path) -> str:
    return path.name[: -len(_PEEP_SUFFIX)] if path.name.endswith(_PEEP_SUFFIX) else path.stem


def _global_id_hit(path: Path, global_id: str) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(16384)
    except OSError:
        return False
    return f'"estleg:globalId": "{global_id}"'.encode() in head or (
        f"akt/{global_id}.xml".encode() in head
    )


def _resolve_regulation(key: str, files: list[Path]) -> Path:
    query = key.strip()
    if not query:
        raise NotFoundError("Regulation key is empty.")
    match = _REG_ID_RE.match(query) or _TID_RE.match(query)
    if match:
        suffix = f"_t{match.group(1)}{_PEEP_SUFFIX}"
        hits = [p for p in files if p.name.endswith(suffix)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise _ambiguous("regulations", query, [str(p) for p in hits])
        raise NotFoundError(f"No regulation with RT terviktekst id {match.group(1)}.")
    if _GLOBAL_ID_RE.match(query):
        hits = [p for p in files if _global_id_hit(p, query)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise _ambiguous("regulations", query, [str(p) for p in hits])
        raise NotFoundError(f"No regulation with RT global id {query}.")
    slug = slugify(query)
    if not slug:
        raise NotFoundError(f"No regulation matched {query!r}.")
    titles = {p: _FILE_TID_RE.sub("", p.name) for p in files}
    exact = [p for p, title in titles.items() if title == slug or _stem(p) == slug]
    if len(exact) == 1:
        return exact[0]
    partial = exact or [
        p for p in files if slug in titles[p] or slug in slugify(str(p.parent.name))
    ]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise _ambiguous("regulations", query, [f"{p.parent.name}/{p.name}" for p in partial])
    raise NotFoundError(
        f"No regulation matched {query!r}. Use an RT terviktekst id (e.g. 1057801), "
        "an RT global id (e.g. 119032025005), Reg_<id>, or a title substring."
    )


def _sidecar(krr: Path, peep: Path) -> Path | None:
    path = krr / "sanctions" / f"sanctions_{_stem(peep)}.json"
    return path if path.is_file() else None


def load_regulation(
    key: str,
    *,
    kov: bool | None = None,
    root: str | Path | None = None,
) -> Graph:
    """Load one state or municipal regulation (plus its sanctions sidecar).

    ``key`` is an RT terviktekst id (``1057801`` / ``t1057801``), an RT global
    id (``119032025005``, the number in ``riigiteataja.ee/akt/<id>``), a
    ``Reg_<id>`` IRI / local name, or a title substring. ``kov=True`` limits
    the search to municipal (KOV) regulations, ``kov=False`` to state ones.
    """
    krr = krr_dir(corpus_root(root))
    files = _regulation_files(krr, kov)
    if not files:
        raise _missing("regulations", krr / "regulations")
    peep = _resolve_regulation(key, files)
    graph = Graph()
    parse_into(graph, peep)
    sidecar = _sidecar(krr, peep)
    if sidecar is not None:
        parse_into(graph, sidecar)
    return graph


def iter_regulations(
    *,
    kov: bool | None = None,
    issuer: str | None = None,
    root: str | Path | None = None,
) -> Iterator[RegulationRow]:
    """Yield a ``RegulationRow`` per regulation (``kov`` / ``issuer`` filters).

    ``issuer`` is a case-insensitive substring of ``estleg:issuer`` (for KOV
    acts also matched against the issuer directory, e.g. ``tallinna``).
    """
    krr = krr_dir(corpus_root(root))
    files = _regulation_files(krr, kov)
    if not files:
        raise _missing("regulations", krr / "regulations")
    wanted = _fold(issuer) if issuer else None
    wanted_slug = slugify(issuer) if issuer else None
    for peep in files:
        issuer_dir = peep.parent.name if peep.parent.name not in _REG_SCOPES else None
        # KOV directories are issuer slugs: skip whole directories that cannot match.
        if wanted_slug and issuer_dir is not None and wanted_slug not in issuer_dir:
            continue
        if not is_real_file(peep):
            continue
        doc = read_document(peep)
        store = DictStore(graph_nodes(doc), doc.get("@context"))
        for row in iter_store_rows(store, "regulations"):
            if wanted and issuer_dir is None:
                if row.issuer is None or wanted not in _fold(row.issuer):
                    continue
            yield row


# ── Riigikohus decisions ─────────────────────────────────────────────────────


def _riigikohus_files(krr: Path) -> dict[int, Path]:
    base = krr / "riigikohus"
    out: dict[int, Path] = {}
    if base.is_dir():
        for path in base.glob(f"riigikohus_*{_PEEP_SUFFIX}"):
            match = _RK_YEAR_RE.match(path.name)
            if match and is_real_file(path):
                out[int(match.group(1))] = path
    return dict(sorted(out.items()))


def load_court_decisions(year: int, *, root: str | Path | None = None) -> Graph:
    """Load every Riigikohus decision of ``year`` (one ``riigikohus_<year>`` peep)."""
    krr = krr_dir(corpus_root(root))
    files = _riigikohus_files(krr)
    if not files:
        raise _missing("Riigikohus decision", krr / "riigikohus")
    path = files.get(int(year))
    if path is None:
        raise NotFoundError(
            f"No Riigikohus decisions for {year}; years available: "
            f"{min(files)}-{max(files)}."
        )
    graph = Graph()
    parse_into(graph, path)
    return graph


def _decision_matcher(key: str) -> tuple[Any, bytes, int | None]:
    query = key.strip()
    ecli = _ECLI_RE.match(query)
    if ecli:
        folded = query.casefold()

        def by_ecli(node: Mapping[str, Any]) -> bool:
            value = node.get("estleg:ecliIdentifier")
            return isinstance(value, str) and value.casefold() == folded

        return by_ecli, query.split(":", 3)[-1].encode(), int(ecli.group(1))
    local = compact(expand(query)) if query.startswith(("estleg:", "https://")) else query
    if local.startswith("RK_"):
        target = "estleg:" + local

        def by_iri(node: Mapping[str, Any]) -> bool:
            return node.get("@id") == target

        return by_iri, f'"{target}"'.encode(), None
    case = query

    def by_case(node: Mapping[str, Any]) -> bool:
        value = node.get("estleg:caseNumber")
        return isinstance(value, str) and (value == case or value.startswith(case + "/"))

    return by_case, case.encode(), None


def load_court_decision(key: str, *, root: str | Path | None = None) -> Graph:
    """Load the Riigikohus decision(s) for a case number, ECLI or ``RK_...`` IRI.

    A bare case number (``3-18-1432``) returns every decision filed under it;
    ``3-18-1432/93`` or an ECLI returns one. The decisions' ``estleg:Citation``
    nodes are included.
    """
    krr = krr_dir(corpus_root(root))
    files = _riigikohus_files(krr)
    if not files:
        raise _missing("Riigikohus decision", krr / "riigikohus")
    matcher, needle, year = _decision_matcher(key)
    paths = [files[year]] if year in files else list(files.values())
    graph = Graph()
    found = 0
    for path in paths:
        if needle not in path.read_bytes():
            continue
        doc = read_document(path)
        hits = [node for node in graph_nodes(doc) if matcher(node)]
        if not hits:
            continue
        prefixes = prefixes_of(doc.get("@context") or {})
        iris = {expand(str(node["@id"]), prefixes) for node in hits}
        graph += _selection_graph(doc, hits + _citations_for(doc, iris))
        found += len(hits)
    if not found:
        raise NotFoundError(f"No Riigikohus decision matched {key!r}.")
    return graph


def iter_court_decisions(
    *,
    year: int | None = None,
    root: str | Path | None = None,
) -> Iterator[DecisionRow]:
    """Yield a ``DecisionRow`` per Riigikohus decision (optionally one year)."""
    krr = krr_dir(corpus_root(root))
    files = _riigikohus_files(krr)
    if not files:
        raise _missing("Riigikohus decision", krr / "riigikohus")
    for file_year, path in files.items():
        if year is not None and file_year != int(year):
            continue
        doc = read_document(path)
        store = DictStore(graph_nodes(doc), doc.get("@context"))
        yield from iter_store_rows(store, "decisions")


# ── drafts ───────────────────────────────────────────────────────────────────


def _draft_documents(krr: Path) -> list[Path]:
    base = krr / "eelnoud"
    combined = base / "eelnoud_combined.jsonld"
    if is_real_file(combined):
        return [combined]
    if base.is_dir():
        return sorted(p for p in base.glob(f"eelnoud_*{_PEEP_SUFFIX}") if is_real_file(p))
    return []


def _draft_nodes(krr: Path) -> Iterator[tuple[Mapping[str, Any], dict[str, Any]]]:
    paths = _draft_documents(krr)
    if not paths:
        raise _missing("draft", krr / "eelnoud")
    for path in paths:
        doc = read_document(path)
        for node in graph_nodes(doc):
            if "estleg:DraftLegislation" in as_list(node.get("@type")):
                yield doc, node


def load_draft(key: str, *, root: str | Path | None = None) -> Graph:
    """Load one EIS draft by EIS number, ``Draft_...`` IRI or title (substring)."""
    krr = krr_dir(corpus_root(root))
    query = key.strip()
    if not query:
        raise NotFoundError("Draft key is empty.")
    folded = _fold(query)
    target = "estleg:" + compact(expand(query)) if "Draft_" in query else None
    by_id: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    exact: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    partial: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    for doc, node in _draft_nodes(krr):
        eis = node.get("estleg:eisNumber")
        label = node.get("rdfs:label")
        if node.get("@id") == target or (isinstance(eis, str) and _fold(eis) == folded):
            by_id.append((doc, node))
        elif isinstance(label, str):
            if _fold(label) == folded:
                exact.append((doc, node))
            elif folded in _fold(label):
                partial.append((doc, node))
    for group in (by_id, exact, partial):
        if len(group) == 1:
            doc, node = group[0]
            return _selection_graph(doc, [node])
        if group:
            names = [str(n.get("estleg:eisNumber") or n.get("@id")) for _, n in group]
            raise _ambiguous("drafts", query, names)
    raise NotFoundError(
        f"No draft matched {query!r}. Use an EIS number (e.g. 'JDM/26-0214'), "
        "a Draft_... IRI, or a title substring."
    )


def iter_drafts(
    *,
    phase: str | None = None,
    root: str | Path | None = None,
) -> Iterator[DraftRow]:
    """Yield a ``DraftRow`` per draft; ``phase`` e.g. ``PublicConsultation``."""
    krr = krr_dir(corpus_root(root))
    wanted = _fold(phase) if phase else None
    paths = _draft_documents(krr)
    if not paths:
        raise _missing("draft", krr / "eelnoud")
    for path in paths:
        doc = read_document(path)
        store = DictStore(graph_nodes(doc), doc.get("@context"))
        for row in iter_store_rows(store, "drafts"):
            if wanted and _fold(row.phase or "") != wanted:
                continue
            yield row


# ── EU acts ──────────────────────────────────────────────────────────────────


def _eurlex_documents(krr: Path) -> list[Path]:
    base = krr / "eurlex"
    peeps = sorted(p for p in base.glob(f"eurlex_*{_PEEP_SUFFIX}") if is_real_file(p))
    if peeps:
        return peeps
    combined = base / "eurlex_combined.jsonld"
    return [combined] if is_real_file(combined) else []


def load_eu_act(celex: str, *, root: str | Path | None = None) -> Graph:
    """Load one EUR-Lex act by CELEX number (``32016R0679``) or ``EU_<celex>``."""
    krr = krr_dir(corpus_root(root))
    match = _CELEX_RE.match(celex.strip())
    if not match:
        raise NotFoundError(f"{celex!r} is not a CELEX number (e.g. '32016R0679').")
    number = match.group(1)
    paths = _eurlex_documents(krr)
    if not paths:
        raise _missing("EU act", krr / "eurlex")
    preferred = _EURLEX_FILE_BY_LETTER.get(number[5:6])
    paths.sort(key=lambda p: p.name != preferred)
    target = f"estleg:EU_{number}"
    for path in paths:
        doc = read_document(path)
        for node in graph_nodes(doc):
            if node.get("@id") == target or node.get("estleg:celexNumber") == number:
                return _selection_graph(doc, [node])
    raise NotFoundError(f"No EU act with CELEX {number}.")


def iter_eu_acts(
    *,
    doc_type: str | None = None,
    in_force: bool | None = None,
    estonia_relevant: bool | None = None,
    root: str | Path | None = None,
) -> Iterator[EuActRow]:
    """Yield an ``EuActRow`` per EUR-Lex act.

    ``doc_type`` is ``Regulation`` / ``Directive`` / ``Decision`` / ...;
    ``in_force`` and ``estonia_relevant`` filter on the boolean flags (acts
    without the flag never match a filter).
    """
    krr = krr_dir(corpus_root(root))
    paths = _eurlex_documents(krr)
    if not paths:
        raise _missing("EU act", krr / "eurlex")
    wanted = _fold(doc_type) if doc_type else None
    for path in paths:
        doc = read_document(path)
        store = DictStore(graph_nodes(doc), doc.get("@context"))
        for row in iter_store_rows(store, "eu_acts"):
            if wanted and _fold(row.doc_type or "") != wanted:
                continue
            if in_force is not None and row.in_force is not in_force:
                continue
            if estonia_relevant is not None and row.estonia_relevant is not estonia_relevant:
                continue
            yield row


# ── IRI dispatch ─────────────────────────────────────────────────────────────


def load_for_compact_id(compact_id: str, *, root: str | Path | None = None) -> Graph | None:
    """Load the record that owns a non-law estleg local name, or ``None``.

    Used by ``resolve_iri``: ``Reg_<id>...`` -> regulation, ``RK_...`` ->
    Riigikohus decision, ``Draft_...`` -> draft, ``EU_<celex>`` -> EU act.
    Returns ``None`` for any other family (enacted-law nodes) or when the
    record is missing.
    """
    try:
        if _REG_ID_RE.match(compact_id):
            return load_regulation(compact_id, root=root)
        if compact_id.startswith("RK_"):
            base = re.sub(r"_(?:Citation|Par)_.*$", "", compact_id)
            return load_court_decision(base, root=root)
        if compact_id.startswith("Draft_"):
            return load_draft(compact_id, root=root)
        if compact_id.startswith("EU_"):
            return load_eu_act(compact_id, root=root)
    except (NotFoundError, CorpusUnavailableError):
        return None
    return None
