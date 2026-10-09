"""Local-name -> node lookup for the w3id resolver pilot (#728).

``https://w3id.org/estleg/<local>`` names one node of the corpus. This module
finds that node without loading the whole 3.9 GB load surface: the local name's
*family* (its leading segment, see AGENTS.md "Node ID naming convention")
selects the one or few files that can define it, and only those are read.

Families resolved (first match wins):

* T-Box terms and controlled-scheme members -- ``controlled_vocabulary.jsonld``
  (``Act``, ``hasProvision``, ``CaseType_Civil``, ``EUInst_Commission``).
* ``Institution_<slug>`` -- ``institutions/institution_<slug>.json``.
* ``Draft_*`` -- the eelnõud index the server already builds.
* ``RK_*`` court decisions -- the Riigikohus index the server already builds;
  other Riigikohus nodes (``Citation_RK_*``) by a byte prefilter over the
  year files.
* ``EU_<celex>`` -- the EUR-Lex file picked by the CELEX type letter;
  ``EUCJ_*`` -- the CURIA files.
* ``Reg_<terviktekstId>…`` (and ``Citation_Reg_``/``Amendment_Reg_``/
  ``AmendmentChain_Reg_``/``Sanction_Reg_``) -- every file whose name ends in
  ``_t<terviktekstId>``: the regulation peep and its provision-version,
  amendment and sanction sidecars.
* everything else -- the law: :func:`data.law_slug_for_iri` on the name with
  any structural family prefix (``Division_``, ``Cluster_``, ``Chapter_``,
  ``Citation_``, ``Sanction_``, ``Amendment_``…) and version suffix
  (``_v<redaction>``) stripped, then the law's peep files and its
  provision-version, sanction and amendment sidecars.

A ``<HumanAbbrev>_Par_<n>`` name that is not itself a node (``KarS_Par_141``)
is answered with the canonical name of that § (``KARIST_2_Osa2_Par_141``) so
the HTTP layer can 303 to it; it is an alias, never a second identifier.

Everything here is read-only and cached per file (:func:`_file_nodes`).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import data

NAMESPACE = "https://w3id.org/estleg/"
VOCABULARY_FILE = "controlled_vocabulary.jsonld"
VOCABULARY_LOCAL = "vocabulary"

# A local name: word characters (Unicode letters, digits, ``_``) plus ``.`` and
# ``-``; no ``/``, no whitespace, bounded. Anything else is a 404 without I/O.
LOCAL_NAME = re.compile(r"^\w[\w.\-]{0,299}$")

DEFAULT_CONTEXT: dict[str, str] = {
    "estleg": NAMESPACE,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "eli": "http://data.europa.eu/eli/ontology#",
    "eli-dl": "http://data.europa.eu/eli/eli-draft-legislation-ontology#",
    "prov": "http://www.w3.org/ns/prov#",
    "schema": "https://schema.org/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "void": "http://rdfs.org/ns/void#",
    "org": "http://www.w3.org/ns/org#",
}

Node = dict[str, Any]


@dataclass(frozen=True)
class Resolution:
    """A found node: its local name, the node, the file's context, the source."""

    local: str
    node: Node
    context: dict[str, Any]
    source: str  # path relative to krr_outputs/
    family: str
    neighbours: dict[str, str] = field(default_factory=dict)  # iri -> label


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------
def curie(local: str) -> str:
    return f"estleg:{local}"


def local_of(iri: str) -> str:
    """``estleg:X`` / ``https://w3id.org/estleg/X`` -> ``X``; anything else -> ""."""
    if not isinstance(iri, str):
        return ""
    if iri.startswith("estleg:"):
        return iri[len("estleg:"):]
    if iri.startswith(NAMESPACE):
        return iri[len(NAMESPACE):]
    return ""


def valid_local_name(local: str) -> bool:
    return bool(local) and bool(LOCAL_NAME.match(local))


# ---------------------------------------------------------------------------
# Per-file node maps
# ---------------------------------------------------------------------------
@lru_cache(maxsize=48)
def _file_nodes(rel: str) -> tuple[dict[str, Any], dict[str, Node]]:
    """``(context, {@id: node})`` of one JSON-LD file under krr_outputs/."""
    try:
        doc = data._load_json(data.krr_dir() / rel)
    except FileNotFoundError:  # no corpus at all: describe nothing, never crash
        return {}, {}
    if not isinstance(doc, dict):
        return {}, {}
    ctx = doc.get("@context")
    context = dict(ctx) if isinstance(ctx, dict) else {}
    nodes: dict[str, Node] = {}
    graph = doc.get("@graph")
    for node in graph if isinstance(graph, list) else []:
        if isinstance(node, dict) and isinstance(node.get("@id"), str):
            nodes.setdefault(node["@id"], node)
    return context, nodes


def _exists(rel: str) -> bool:
    return (data.krr_dir() / rel).is_file()


def _files_containing(rels: list[str], needle: str) -> list[str]:
    """The files among ``rels`` whose bytes contain ``"<needle>"`` (quoted).

    A cheap prefilter for families spread over many large files (Riigikohus
    year files, CURIA): reading bytes is an order of magnitude cheaper than
    parsing JSON, and only files that mention the id at all are parsed.
    """
    token = f'"{needle}"'.encode("utf-8")
    out: list[str] = []
    for rel in rels:
        try:
            with open(data.krr_dir() / rel, "rb") as fh:
                if token in fh.read():
                    out.append(rel)
        except OSError:
            continue
    return out


def _glob(pattern: str) -> list[str]:
    base = data.krr_dir()
    return sorted(str(p.relative_to(base)) for p in base.glob(pattern))


# ---------------------------------------------------------------------------
# The regulation file index (terviktekstId -> files), from file names only
# ---------------------------------------------------------------------------
_T_ID = re.compile(r"_t(\d+)(?:_peep)?\.(?:json|jsonld)$")


@lru_cache(maxsize=1)
def _reg_files() -> dict[str, tuple[str, ...]]:
    """``terviktekstId`` -> every file named ``…_t<id>[_peep].json[ld]``.

    Covers ``regulations/riik``, ``regulations/kov/<municipality>``,
    ``provision_versions``, ``amendments`` and ``sanctions``. Built from
    directory listings alone (no JSON is read), so it costs one scandir pass.
    The regulation peep is listed first.
    """
    base = data.krr_dir()
    found: dict[str, list[str]] = {}

    def scan(directory: Path) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for entry in entries:
            if not entry.is_file():
                continue
            m = _T_ID.search(entry.name)
            if m:
                found.setdefault(m.group(1), []).append(
                    str(Path(entry.path).relative_to(base))
                )

    scan(base / "regulations" / "riik")
    kov = base / "regulations" / "kov"
    if kov.is_dir():
        for municipality in sorted(kov.iterdir()):
            if municipality.is_dir():
                scan(municipality)
    for sidecar in ("provision_versions", "amendments", "sanctions"):
        scan(base / sidecar)
    return {
        reg_id: tuple(sorted(files, key=lambda f: (not f.startswith("regulations/"), f)))
        for reg_id, files in found.items()
    }


# ---------------------------------------------------------------------------
# Family dispatch
# ---------------------------------------------------------------------------
# Leading segments that wrap a law/regulation IRI body without changing which
# law it belongs to (``Division_KARIST_2_2_9_7``, ``Sanction_KARIST_2_…``).
_WRAPPER_PREFIX = re.compile(
    r"^(?:Division|Cluster|Chapter|Citation|Sanction|Amendment|AmendmentChain|"
    r"Similarity|Subdivision|Title|Part)_"
)
_VERSION_SUFFIX = re.compile(r"_v\d+$")
_REG_IN_BODY = re.compile(r"(?:^|_)Reg_(\d+)(?:_|$)")
_EU_CELEX = re.compile(r"^EU_(\d)(\d{4})([A-Z]{1,2})")
_EURLEX_BY_LETTER = {
    "L": "eurlex/eurlex_directives_peep.json",
    "R": "eurlex/eurlex_regulations_peep.json",
    "D": "eurlex/eurlex_decisions_peep.json",
}


def _law_candidates(body: str) -> list[str]:
    """Files that can define a law-family node ``body`` (most likely first)."""
    core = body
    while True:
        stripped = _WRAPPER_PREFIX.sub("", core, count=1)
        if stripped == core:
            break
        core = stripped
    is_version = bool(_VERSION_SUFFIX.search(core))
    core = _VERSION_SUFFIX.sub("", core)
    if not core:
        return []
    out: list[str] = []
    for slug in _law_slugs_for(core):
        record = data._records_by_slug().get(slug)
        if record is not None:
            out.extend(f for f in _law_files(record, body, is_version) if f not in out)
    return out


@lru_cache(maxsize=1)
def _law_prefixes() -> tuple[tuple[str, str], ...]:
    """``(IRI prefix, law slug)`` pairs, longest prefix first.

    Union of the registry abbreviations and every law's act-node prefix with
    the map suffix removed. :func:`data._law_map_prefix_to_slug` strips only
    ``_Map_<year>``, so act nodes named ``<prefix>_Map`` (``KortTS_Map``, the
    truncated-slug treaty laws) are indexed there under ``…_Map`` and
    :func:`data.law_slug_for_iri` cannot find their provisions; stripping the
    bare ``_Map`` here closes that gap for the resolver without changing the
    shared helper the tools rely on.
    """
    pairs: dict[str, str] = {}
    for abbrev, slug in data._iri_prefix_to_slug():
        pairs.setdefault(abbrev, slug)
    for key, slug in data._law_map_prefix_to_slug().items():
        pairs.setdefault(re.sub(r"_Map$", "", key), slug)
    return tuple(sorted(pairs.items(), key=lambda kv: (-len(kv[0]), kv[0])))


def _law_slugs_for(core: str, limit: int = 3) -> list[str]:
    """Law slugs whose IRI prefix heads ``core``: the data layer's answer first,
    then longest-prefix matches (on a ``_`` boundary), at most ``limit``."""
    slugs: list[str] = []
    first = data.law_slug_for_iri(curie(core))
    if first:
        slugs.append(first)
    for prefix, slug in _law_prefixes():
        if len(slugs) >= limit:
            break
        if (core == prefix or core.startswith(prefix + "_")) and slug not in slugs:
            slugs.append(slug)
    return slugs


def _law_files(record: data.LawRecord, body: str, is_version: bool) -> list[str]:
    """One law's peep + sidecar files, ordered by what ``body`` most likely is."""
    base = data._base_slug(record)
    peeps = list(record.files)
    versions = [f"provision_versions/{base}.jsonld"]
    sanctions = []
    for fname in record.files:
        stem = fname[: -len("_peep.json")] if fname.endswith("_peep.json") else fname
        sanctions.append(f"sanctions/sanctions_{stem}.json")
    amendments = [f"amendments/amendments_{base}.json"]
    if is_version:
        ordered = versions + peeps
    elif body.startswith("Sanction"):
        ordered = sanctions + peeps
    elif body.startswith("Amendment"):
        ordered = amendments + peeps
    else:
        ordered = peeps + sanctions + amendments + versions
    if "_Expr_" in body:
        ordered.insert(0, "act_expressions_combined.jsonld")
    seen: set[str] = set()
    return [f for f in ordered if not (f in seen or seen.add(f)) and _exists(f)]


def _candidates(body: str) -> list[str]:
    """Candidate files for a non-indexed family, most likely first."""
    if body.startswith("Institution_"):
        exact = f"institutions/institution_{body[len('Institution_'):]}.json"
        return [exact] if _exists(exact) else _glob("institutions/institution_*.json")
    m = _EU_CELEX.match(body)
    if m:
        letter = m.group(3)[0]
        rel = _EURLEX_BY_LETTER.get(letter)
        return [rel] if rel else list(_EURLEX_BY_LETTER.values())
    if body.startswith("EURlex_") or body.startswith("EU_"):
        return _files_containing(_glob("eurlex/*.json*"), curie(body))
    if body.startswith(("EUCJ_", "CURIA_")):
        return _files_containing(_glob("curia/*_peep.json"), curie(body))
    if body.startswith(("RK_", "Citation_RK_", "Riigikohus_")):
        return _files_containing(_glob("riigikohus/riigikohus_*_peep.json"), curie(body))
    if body.startswith(("Eelnoud_", "Draft_")):
        return _files_containing(_glob("eelnoud/eelnoud_*_peep.json"), curie(body))
    if body.startswith("Concept_"):
        return _glob("concepts/*.jsonld")
    if body.startswith("Annotation_"):
        return _glob("annotations/*.jsonld")
    if body.startswith("EuroVoc"):
        return ["eurovoc_concept_scheme.jsonld"]
    if body.startswith("Sanctions_"):
        stem = re.sub(r"_Map$", "", body[len("Sanctions_"):])
        rel = f"sanctions/sanctions_{stem}.json"
        return [rel] if _exists(rel) else []
    if body.startswith("ProvisionVersions_"):
        slug = re.sub(r"_Map$", "", body[len("ProvisionVersions_"):])
        rel = f"provision_versions/{slug}.jsonld"
        return [rel] if _exists(rel) else []
    m = _REG_IN_BODY.search(body)
    if m:
        return list(_reg_files().get(m.group(1), ()))
    return _law_candidates(body)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
def vocabulary_document() -> dict[str, Any]:
    """The controlled vocabulary JSON-LD document (``{}`` when absent)."""
    doc = data._load_json(data.krr_dir() / VOCABULARY_FILE)
    return doc if isinstance(doc, dict) else {}


def vocabulary_nodes() -> dict[str, Node]:
    """``{local name: node}`` for every node of the controlled vocabulary."""
    _ctx, nodes = _file_nodes(VOCABULARY_FILE)
    return {local_of(iri): node for iri, node in nodes.items() if local_of(iri)}


def vocabulary_label(local: str) -> str:
    node = vocabulary_nodes().get(local)
    return label_of(node) if node else ""


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
_LABEL_KEYS = ("rdfs:label", "skos:prefLabel", "dcterms:title", "dc:title", "estleg:title")


def label_of(node: Node | None, prefer: str = "et") -> str:
    """The node's display label, preferring language ``prefer`` when tagged."""
    if not isinstance(node, dict):
        return ""
    for key in _LABEL_KEYS:
        values = data._as_list(node.get(key))
        tagged = [
            v for v in values
            if isinstance(v, dict) and v.get("@language") == prefer and isinstance(v.get("@value"), str)
        ]
        if tagged:
            return tagged[0]["@value"]
        for value in values:
            text = data._text(value)
            if text:
                return text
    return ""


def _neighbour_labels(node: Node, nodes_in_file: dict[str, Node]) -> dict[str, str]:
    """Labels for the node's outgoing ``estleg:`` references, where known cheaply.

    Only the defining file and the vocabulary are consulted (both already in
    memory), so describing a node never fans out into further file reads.
    """
    vocab = vocabulary_nodes()
    out: dict[str, str] = {}
    for key, value in node.items():
        if key.startswith("@"):
            continue
        for ref in data._ids_of(value):
            local = local_of(ref)
            if not local or ref in out:
                continue
            target = nodes_in_file.get(ref) or vocab.get(local)
            label = label_of(target) if target else ""
            if label:
                out[ref] = label
    for t in data._types_of(node):
        local = local_of(t)
        if local and t not in out and (label := label_of(vocab.get(local))):
            out[t] = label
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _resolution(local: str, node: Node, rel: str, family: str) -> Resolution:
    context, nodes = _file_nodes(rel) if rel else ({}, {})
    return Resolution(
        local=local,
        node=node,
        context={**DEFAULT_CONTEXT, **context},
        source=rel,
        family=family,
        neighbours=_neighbour_labels(node, nodes),
    )


def resolve(local: str) -> Resolution | None:
    """Find the node named ``https://w3id.org/estleg/<local>``, or ``None``."""
    if not valid_local_name(local):
        return None
    iri = curie(local)
    vocab = vocabulary_nodes()
    if local in vocab:
        return _resolution(local, vocab[local], VOCABULARY_FILE, "vocabulary")
    if local.startswith("Draft_"):
        node = data.draft_info(iri)
        if node is not None:
            # Peeps retain their first-observed feed, while lifecycle phases
            # change. Locate the owning file instead of deriving it from phase.
            for rel in _candidates(local):
                if iri in _file_nodes(rel)[1]:
                    return _resolution(local, node, rel, "draft")
            return _resolution(local, node, "", "draft")
    if local.startswith("RK_"):
        node = data.court_decision(iri)
        if node is not None:
            return _resolution(local, node, "", "court_decision")
    tried = _candidates(local)
    m = _REG_IN_BODY.search(local)
    for rel in tried:
        _ctx, nodes = _file_nodes(rel)
        node = nodes.get(iri)
        if node is not None:
            return _resolution(local, node, rel, family_of(local, rel))
    if m:
        # 21 of 14,871 regulation peeps are filed under a different _t<id>
        # than their own terviktekstId; the record index (one scan of the
        # subcorpus, shared with the regulation tools) knows the real file.
        rel = _reg_record_files().get(m.group(1), "")
        if rel and rel not in tried:
            node = _file_nodes(rel)[1].get(iri)
            if node is not None:
                return _resolution(local, node, rel, family_of(local, rel))
    return None


@lru_cache(maxsize=1)
def _reg_record_files() -> dict[str, str]:
    """``terviktekstId`` -> regulation peep, from the data layer's record scan."""
    return {r.reg_id: r.file for r in data._regulation_records() if r.reg_id}


def _act_in(rel: str) -> Node | None:
    _ctx, nodes = _file_nodes(rel)
    for node in nodes.values():
        if "estleg:Act" in data._types_of(node):
            return node
    return None


def act_node_for(res: Resolution) -> Node | None:
    """The act node of the law / regulation ``res`` belongs to, when in reach.

    Read from the defining file (a law or regulation peep holds its own act
    node) or, for a regulation sidecar node, from the regulation peep found by
    its ``terviktekstId``. Never triggers a subcorpus-wide scan, unlike
    :func:`data.citation_url_for_iri` for regulations (15k files on first use).
    """
    if res.source.startswith("regulations/") or (
        res.source.endswith("_peep.json") and "/" not in res.source
    ):
        act = _act_in(res.source)
        if act is not None:
            return act
    m = _REG_IN_BODY.search(res.local)
    if m:
        for rel in _reg_files().get(m.group(1), ()):
            if rel.startswith("regulations/"):
                return _act_in(rel)
    return None


def family_of(local: str, rel: str = "") -> str:
    """A short family tag for audit/metrics (``law``, ``regulation``…)."""
    if rel == VOCABULARY_FILE:
        return "vocabulary"
    if rel.startswith("regulations/"):
        return "regulation"
    if rel.startswith("provision_versions/"):
        return "provision_version"
    for prefix, fam in (
        ("Institution_", "institution"),
        ("Draft_", "draft"),
        ("RK_", "court_decision"),
        ("EUCJ_", "eu_court_decision"),
        ("EU_", "eu_act"),
        ("Sanction", "sanction"),
        ("Amendment", "amendment"),
    ):
        if local.startswith(prefix):
            return fam
    if rel.startswith("riigikohus/"):
        return "court"
    if rel.startswith(("eurlex/", "curia/")):
        return "eu"
    return "law"


_ALIAS = re.compile(r"^(?P<law>.+?)_Par_(?P<par>\d+(?:_\d+)?)$")


def alias_target(local: str) -> str | None:
    """Canonical local name for a human ``<Abbrev>_Par_<n>`` alias, or ``None``.

    ``KarS_Par_141`` is not a node (the corpus IRI is ``KARIST_2_Osa2_Par_141``)
    but it is what people type. Resolve the abbreviation with the server's law
    resolver and the § with :func:`data.find_provision`; return the § node's
    local name only when it differs from ``local``.
    """
    m = _ALIAS.match(local or "")
    if not m:
        return None
    record = data.resolve_law(m.group("law"))
    if record is None:
        return None
    # "133_1" (§ 133¹) is a spelling find_provision already understands.
    node = data.find_provision(data.load_law_graph(record), m.group("par"))
    target = local_of(node.get("@id", "")) if node else ""
    return target if target and target != local else None


def compact_document(res: Resolution) -> dict[str, Any]:
    """The node as compact JSON-LD: ``@context`` + the node + labelled neighbours."""
    graph: list[Node] = [res.node]
    for ref, label in sorted(res.neighbours.items()):
        if ref != res.node.get("@id"):
            graph.append({"@id": ref, "rdfs:label": label})
    return {"@context": res.context, "@graph": graph}


def dumps(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2)


def cache_clear() -> None:
    """Drop the resolver's own caches (tests swap corpora)."""
    _file_nodes.cache_clear()
    _reg_files.cache_clear()
    _law_prefixes.cache_clear()
    _reg_record_files.cache_clear()


__all__ = [
    "DEFAULT_CONTEXT",
    "act_node_for",
    "NAMESPACE",
    "Resolution",
    "alias_target",
    "cache_clear",
    "compact_document",
    "curie",
    "dumps",
    "family_of",
    "label_of",
    "local_of",
    "resolve",
    "valid_local_name",
    "vocabulary_document",
    "vocabulary_label",
    "vocabulary_nodes",
]
