#!/usr/bin/env python3
"""Build-time RETRIEVAL PROJECTION for RAG / agent developers (ticket #523).

The published ontology spreads a single citeable provision across three nodes in
two files: the provision node (``estleg:legalText`` + ``estleg:paragrahv``), its
``estleg:ProvisionVersion`` nodes (``versionText`` + ``versionRedactionId`` +
``versionValidFrom``), and the act root (``dcterms:title`` + Riigi Teataja URL +
``estleg:kehtiv``). A retrieval consumer therefore has to reassemble 3 nodes
across 2 files to cite one paragraph, and a provision node's bytes are dominated
by unbounded ``interpretedBy`` / ``referencedBy`` IRI arrays. This generator
flattens the graph into four self-contained, citeable projections:

1. ``chunks.jsonl`` -- one flat record PER PROVISION-VERSION (the core retrieval
   unit). For a §-level provision that has a version layer, one record per
   version; for a provision with no version layer, one record from the
   consolidated text. Each record is independently citeable
   (``provision_iri`` + ``rt_url`` + ``paragraph``) and carries the untruncated
   text plus the validity window and an ``in_force`` flag.
2. ``outlines/<law>.json`` -- a per-law skim tier: ``{paragraph, label, chapter,
   summary (<=120 chars), has_versions}`` per provision, so a consumer can
   navigate a multi-megabyte law without fetching the whole thing.
3. ``context_packs/<law>.json`` -- a per-law DENORMALISED bundle: provisions +
   versions + references + cases + amendments + sanctions in ONE fetch.
4. ``llms.txt`` -- a served plain-text entry point (https://llmstxt.org) that
   describes the corpus and points at the projections.

Design notes
------------
* The provision-version unit is **§-level**: ``estleg:LegalProvision_*`` nodes
  (those carry ``estleg:paragrahv`` and ``estleg:hasVersion``). ``estleg:
  Subsection`` (lõige) nodes are NOT separate retrieval units -- their text is
  already contained in the parent §'s ``estleg:legalText``.
* The version layer keys by ``estleg:versionOf`` (the §). ``in_force`` is
  computed from the validity window against the pinned evaluation date, so it is
  deterministic and date-stable (issue #262/#295 pattern).
* Riigi Teataja URLs and act titles are read off the act root via the same
  fields the canonical builders write (``dcterms:source`` / ``dcterms:title`` /
  ``dc:source`` / ``rdfs:label``); abbreviations come from
  ``data/law_abbreviations.json`` (the registry the URI migration maintains).
* **Determinism** (AGENTS.md): laws are processed in sorted order, provisions by
  IRI, versions by ``(validFrom, redactionId, @id)``; no wall-clock timestamps
  (the pinned ``ESTLEG_BUILD_EVALUATION_DATE`` / ``ONTOLOGY_VERSION`` are the
  only stamps); no ``set()`` iteration without a surrounding ``sorted()``. Two
  consecutive runs over the same corpus are byte-identical.

The artifacts live under ``krr_outputs/retrieval/``, which
``estleg_common.is_operational_state_file`` excludes from the corpus file
counter (the same status as ``reports/integration/`` pipeline manifests) so the
projection never drifts the pinned ``metadata.jsonld`` ``estleg:totalFiles``
count nor reaches ``validate_all`` / SHACL.

TODO(#523): wiring this as a final ``run_all_integration`` step is intentionally
deferred -- it would run AFTER ``fix_all_issues.py`` and so break the
"rebuild is strictly last" invariant asserted by
``tests/test_run_all_integration.py::test_rebuild_step_topo_sorts_strictly_last``.
Run it standalone after the pipeline (``python3 scripts/
generate_retrieval_projection.py``) until that invariant is revisited.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    NS,
    ONTOLOGY_VERSION,
    REPO_ROOT,
    act_deprecation,
    jsonld_text,
    save_json,
)

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------
DATA_DIR = REPO_ROOT / "data"
ABBREV_REGISTRY_PATH = DATA_DIR / "law_abbreviations.json"
DEFAULT_OUT_DIRNAME = "retrieval"
PROVISION_VERSIONS_DIRNAME = "provision_versions"
AMENDMENTS_DIRNAME = "amendments"
SANCTIONS_DIRNAME = "sanctions"
COURT_DIRNAMES = ("riigikohus", "curia")

SUMMARY_MAX = 120
SAMPLE_CHUNKS_DEFAULT = 200
SAMPLE_LAW_DEFAULT = "alkoholiseadus"

# riigiteataja.ee browse URL for an act; the corpus stores the resolvable
# source IRI with a trailing ``.xml`` (XML endpoint) -- strip it for the
# human/agent-facing citation, which renders HTML.
_RT_XML_SUFFIX = ".xml"

# Provision node @type prefix; subsection (lõige) nodes are estleg:Subsection
# and are deliberately NOT treated as provision-version units.
_PROVISION_TYPE_PREFIX = "estleg:LegalProvision"


# ---------------------------------------------------------------------------
# Small JSON-LD accessors (kept local + pure for unit testing)
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> Any | None:
    """Load JSON from ``path``; return ``None`` on a missing/corrupt file."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _graph(doc: Any) -> list[dict]:
    """Return the ``@graph`` list of a JSON-LD document (or ``[]``)."""
    if isinstance(doc, dict):
        graph = doc.get("@graph")
        if isinstance(graph, list):
            return [n for n in graph if isinstance(n, dict)]
        return []
    if isinstance(doc, list):
        return [n for n in doc if isinstance(n, dict)]
    return []


def _types(node: dict) -> list[str]:
    """Return ``@type`` as a list of strings."""
    raw = node.get("@type") or []
    if isinstance(raw, str):
        return [raw]
    return [t for t in raw if isinstance(t, str)]


def _ref_ids(value: Any) -> list[str]:
    """Return every ``@id`` reachable in a JSON-LD object/list/string value."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        ref = value.get("@id")
        if isinstance(ref, str):
            out.append(ref)
    elif isinstance(value, list):
        for item in value:
            out.extend(_ref_ids(item))
    return out


def _ref_id(value: Any) -> str | None:
    """Return the first ``@id`` in a JSON-LD value, else ``None``."""
    ids = _ref_ids(value)
    return ids[0] if ids else None


def _strip_xml(url: str | None) -> str | None:
    """Return the browseable RT URL: drop a trailing ``.xml`` endpoint suffix."""
    if not url:
        return None
    if url.endswith(_RT_XML_SUFFIX):
        return url[: -len(_RT_XML_SUFFIX)]
    return url


# ---------------------------------------------------------------------------
# Act-root resolution (title / rt_url / kehtiv / abbreviation / status)
# ---------------------------------------------------------------------------
def find_act_root(graph: list[dict]) -> dict | None:
    """Return the act/ontology root node (first ``owl:Ontology``)."""
    for node in graph:
        if "owl:Ontology" in _types(node):
            return node
    return None


def act_title(root: dict | None) -> str:
    """Resolve the act title: dcterms:title -> dc:source -> rdfs:label."""
    if not root:
        return ""
    for key in ("dcterms:title", "dc:source", "rdfs:label"):
        text = jsonld_text(root.get(key))
        if text:
            return text
    return ""


def act_rt_url(root: dict | None) -> str | None:
    """Resolve the act's Riigi Teataja browse URL from the act root.

    Reads ``dcterms:source`` (the canonical resolvable IRI the builders write),
    falling back to ``owl:sameAs``; the stored value is the ``.xml`` endpoint,
    which is normalised to the HTML browse URL.
    """
    if not root:
        return None
    for key in ("dcterms:source", "owl:sameAs"):
        ref = _ref_id(root.get(key))
        if ref and ref.startswith("http"):
            return _strip_xml(ref)
    # Legacy dc:source may itself be a URL.
    legacy = jsonld_text(root.get("dc:source"))
    if legacy.startswith("http"):
        return _strip_xml(legacy)
    return None


def act_kehtiv(root: dict | None) -> str | None:
    """Return the act consolidation date (``estleg:kehtiv``) or ``None``."""
    if not root:
        return None
    return jsonld_text(root.get("estleg:kehtiv")) or None


def act_in_force(root: dict | None) -> bool:
    """Return True when the act root's ``estleg:temporalStatus`` is in force.

    Used only for provisions with no version layer (the consolidated peep IS
    the current text). Unknown status is treated as in force, matching the
    consolidated-text assumption.
    """
    if not root:
        return True
    status = jsonld_text(root.get("estleg:temporalStatus"))
    if not status:
        return True
    return status == "inForce"


def derive_abbrev(act_id: str | None) -> str | None:
    """Derive a law abbreviation from an act-root ``@id`` (registry fallback).

    ``estleg:AS_Map_2026`` -> ``AS``; ``estleg:AÕS_Osa1_...`` -> ``AÕS``;
    also handles ``_ProcedureMap`` / ``_Ontology`` / ``_TopicScheme`` roots.
    """
    if not act_id:
        return None
    local = act_id
    if local.startswith("estleg:"):
        local = local[len("estleg:"):]
    elif local.startswith(NS):
        local = local[len(NS):]
    for marker in ("_Map_2026", "_ProcedureMap", "_Ontology", "_TopicScheme"):
        if marker in local:
            return local.split(marker, 1)[0] or None
    m = re.match(r"(.+?)_Osa\d+", local)
    if m:
        return m.group(1)
    return local or None


def resolve_abbrev(
    registry: dict[str, dict],
    file_slug: str,
    law_name: str,
    root: dict | None,
) -> str | None:
    """Resolve a law abbreviation, preferring the registry over derivation."""
    for key in (file_slug, law_name):
        entry = registry.get(key)
        if isinstance(entry, dict):
            abbrev = entry.get("abbrev")
            if abbrev:
                return abbrev
    return derive_abbrev(_ref_id(root) or (root or {}).get("@id"))


# ---------------------------------------------------------------------------
# Provision + version accessors
# ---------------------------------------------------------------------------
def is_provision(node: dict) -> bool:
    """True for a §-level provision node (excludes subsections/chapters)."""
    return any(t.startswith(_PROVISION_TYPE_PREFIX) for t in _types(node))


def provision_text(node: dict) -> str:
    """Consolidated provision text: legalText, falling back to summary."""
    return jsonld_text(node.get("estleg:legalText")) or jsonld_text(
        node.get("estleg:summary")
    )


def truncate_summary(text: str, max_len: int = SUMMARY_MAX) -> str:
    """Return ``text`` collapsed to a single line of at most ``max_len`` chars.

    Whitespace is collapsed; an over-length string is cut and suffixed with an
    ellipsis so the result never exceeds ``max_len`` characters.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    if max_len <= 3:
        return collapsed[:max_len]
    return collapsed[: max_len - 3].rstrip() + "..."


def chapter_label(node: dict, id_label: dict[str, str]) -> str | None:
    """Resolve a provision's chapter label via ``estleg:isPartOf``."""
    parent = _ref_id(node.get("estleg:isPartOf"))
    if not parent:
        return None
    return id_label.get(parent)


def _version_record(node: dict) -> tuple[str, dict] | None:
    """Return ``(provision_iri, version-field dict)`` for a ProvisionVersion."""
    if "estleg:ProvisionVersion" not in _types(node):
        return None
    provision_iri = _ref_id(node.get("estleg:versionOf"))
    if not provision_iri:
        return None
    return provision_iri, {
        "iri": node.get("@id"),
        "redaction_id": jsonld_text(node.get("estleg:versionRedactionId")) or None,
        "valid_from": jsonld_text(node.get("estleg:versionValidFrom")) or None,
        "valid_to": jsonld_text(node.get("estleg:versionValidTo")) or None,
        "text": jsonld_text(node.get("estleg:versionText")),
    }


def load_version_map(versions_dir: Path) -> dict[str, list[dict]]:
    """Return a GLOBAL ``{provision_iri: [version dicts]}`` map.

    Keyed by ``estleg:versionOf`` over EVERY version file, so a provision
    resolves to its versions regardless of which file holds either node. This
    matters for multi-part codes (``asjaoigusseadus``, ``karistusseadustik``,
    …) whose version layer is a single law-named file while the peeps are split
    by ``osa`` — a per-file pairing would strand ~7% of versions. Each version
    dict is ``{iri, redaction_id, valid_from, valid_to, text}`` and the
    per-provision list is ordered by ``(valid_from, redaction_id, iri)`` so
    chunk emission is byte-stable.
    """
    out: dict[str, list[dict]] = {}
    if not versions_dir.is_dir():
        return out
    for path in sorted(versions_dir.glob("*.jsonld")):
        doc = _load_json(path)
        if doc is None:
            continue
        for node in _graph(doc):
            record = _version_record(node)
            if record is None:
                continue
            provision_iri, fields = record
            out.setdefault(provision_iri, []).append(fields)
    for versions in out.values():
        versions.sort(
            key=lambda v: (
                v["valid_from"] or "",
                v["redaction_id"] or "",
                v["iri"] or "",
            )
        )
    return out


def compute_in_force(
    valid_from: str | None, valid_to: str | None, eval_date: str
) -> bool:
    """True when ``[valid_from, valid_to]`` contains ``eval_date``.

    ISO ``YYYY-MM-DD`` dates compare lexically. A missing ``valid_from`` means
    "already started"; a missing ``valid_to`` means "open-ended / current".
    """
    if valid_from and valid_from > eval_date:
        return False
    if valid_to and valid_to < eval_date:
        return False
    return True


# ---------------------------------------------------------------------------
# Chunk assembly (the core retrieval unit)
# ---------------------------------------------------------------------------
def build_chunk_record(
    *,
    provision_iri: str,
    redaction_id: str | None,
    paragraph: str,
    act_title_str: str,
    abbrev: str | None,
    rt_url: str | None,
    valid_from: str | None,
    valid_to: str | None,
    in_force: bool,
    text: str,
) -> dict:
    """Assemble one flat, self-contained provision-version record.

    Key order matches the ticket schema; the dict is always built in this order
    so JSONL serialisation is byte-stable across runs.
    """
    return {
        "provision_iri": provision_iri,
        "redaction_id": redaction_id,
        "paragraph": paragraph,
        "act_title": act_title_str,
        "abbrev": abbrev,
        "rt_url": rt_url,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "in_force": in_force,
        "text": text,
    }


# ---------------------------------------------------------------------------
# Court-decision index (for context-pack case resolution)
# ---------------------------------------------------------------------------
def build_decision_index(krr_dir: Path) -> dict[str, dict]:
    """Map ``CourtDecision`` @id -> ``{case_number, decision_link, decision_date}``.

    Streams each court peep file (extracting only three fields) so the index
    stays small even though the underlying corpus is large.
    """
    index: dict[str, dict] = {}
    for dirname in COURT_DIRNAMES:
        court_dir = krr_dir / dirname
        if not court_dir.is_dir():
            continue
        for path in sorted(court_dir.glob("*_peep.json")):
            doc = _load_json(path)
            if doc is None:
                continue
            for node in _graph(doc):
                if "estleg:CourtDecision" not in _types(node):
                    continue
                node_id = node.get("@id")
                if not isinstance(node_id, str) or node_id in index:
                    continue
                index[node_id] = {
                    "iri": node_id,
                    "case_number": jsonld_text(node.get("estleg:caseNumber"))
                    or None,
                    "decision_link": jsonld_text(node.get("estleg:decisionLink"))
                    or None,
                    "decision_date": jsonld_text(node.get("estleg:decisionDate"))
                    or None,
                }
    return index


# ---------------------------------------------------------------------------
# Overlay loaders (amendments / sanctions for a law)
# ---------------------------------------------------------------------------
def _overlay_nodes(krr_dir: Path, subdir: str, prefix: str, *slugs: str) -> list[dict]:
    """Return the individual (non-Ontology) nodes of an overlay file for a law.

    Tries ``<subdir>/<prefix><slug>.json`` for each candidate slug in order and
    returns the first that resolves; the ``owl:Ontology`` wrapper is dropped.
    """
    overlay_dir = krr_dir / subdir
    seen: set[str] = set()
    for slug in slugs:
        if not slug or slug in seen:
            continue
        seen.add(slug)
        doc = _load_json(overlay_dir / f"{prefix}{slug}.json")
        if doc is None:
            continue
        nodes = [n for n in _graph(doc) if "owl:Ontology" not in _types(n)]
        if nodes:
            return nodes
    return []


# ---------------------------------------------------------------------------
# Per-law processing
# ---------------------------------------------------------------------------
class _LawAccumulator:
    """Mutable per-law accumulator across a law's (possibly multi-part) files."""

    def __init__(self, law_name: str) -> None:
        self.law_name = law_name
        self.title = ""
        self.abbrev: str | None = None
        self.rt_url: str | None = None
        self.kehtiv: str | None = None
        self.in_force = True
        self.header_set = False
        self.chunks: list[dict] = []
        self.outline: list[dict] = []
        self.provisions: list[dict] = []
        self.versions: list[dict] = []
        self.case_ids: set[str] = set()


def process_law_file(
    doc: Any,
    *,
    file_slug: str,
    law_name: str,
    registry: dict[str, dict],
    version_map: dict[str, list[dict]],
    eval_date: str,
    acc: _LawAccumulator,
) -> None:
    """Fold one peep file's provisions into the per-law accumulator."""
    graph = _graph(doc)
    root = find_act_root(graph)
    title = act_title(root)
    abbrev = resolve_abbrev(registry, file_slug, law_name, root)
    rt_url = act_rt_url(root)
    kehtiv = act_kehtiv(root)
    act_active = act_in_force(root)

    if not acc.header_set:
        acc.title = title
        acc.abbrev = abbrev
        acc.rt_url = rt_url
        acc.kehtiv = kehtiv
        acc.in_force = act_active
        acc.header_set = True

    id_label = {
        n["@id"]: jsonld_text(n.get("rdfs:label"))
        for n in graph
        if isinstance(n.get("@id"), str) and n.get("rdfs:label")
    }

    provisions = sorted(
        (n for n in graph if is_provision(n)),
        key=lambda n: n.get("@id") or "",
    )
    for node in provisions:
        provision_iri = node.get("@id")
        if not isinstance(provision_iri, str):
            continue
        paragraph = jsonld_text(node.get("estleg:paragrahv"))
        label = jsonld_text(node.get("rdfs:label"))
        chapter = chapter_label(node, id_label)
        consolidated_text = provision_text(node)
        # pop (not get): each provision @id is globally unique, so consuming
        # the entry drains the resident version map as the run progresses.
        versions = version_map.pop(provision_iri, [])

        # --- chunks: one per version, else one consolidated record ---
        if versions:
            for ver in versions:
                in_force = compute_in_force(
                    ver["valid_from"], ver["valid_to"], eval_date
                )
                acc.chunks.append(
                    build_chunk_record(
                        provision_iri=provision_iri,
                        redaction_id=ver["redaction_id"],
                        paragraph=paragraph,
                        act_title_str=title,
                        abbrev=abbrev,
                        rt_url=rt_url,
                        valid_from=ver["valid_from"],
                        valid_to=ver["valid_to"],
                        in_force=in_force,
                        text=ver["text"],
                    )
                )
                acc.versions.append(
                    {
                        "iri": ver["iri"],
                        "provision_iri": provision_iri,
                        "redaction_id": ver["redaction_id"],
                        "valid_from": ver["valid_from"],
                        "valid_to": ver["valid_to"],
                        "in_force": in_force,
                        "text": ver["text"],
                    }
                )
        else:
            acc.chunks.append(
                build_chunk_record(
                    provision_iri=provision_iri,
                    redaction_id=None,
                    paragraph=paragraph,
                    act_title_str=title,
                    abbrev=abbrev,
                    rt_url=rt_url,
                    valid_from=kehtiv,
                    valid_to=None,
                    in_force=act_active,
                    text=consolidated_text,
                )
            )

        # --- outline entry (skim tier) ---
        acc.outline.append(
            {
                "paragraph": paragraph,
                "label": label,
                "chapter": chapter,
                "summary": truncate_summary(
                    jsonld_text(node.get("estleg:summary")) or label
                ),
                "has_versions": bool(versions),
            }
        )

        # --- context-pack provision entry ---
        references = sorted(set(_ref_ids(node.get("estleg:references"))))
        referenced_by = sorted(set(_ref_ids(node.get("estleg:referencedBy"))))
        interpreted_by = sorted(set(_ref_ids(node.get("estleg:interpretedBy"))))
        acc.case_ids.update(interpreted_by)
        acc.provisions.append(
            {
                "iri": provision_iri,
                "paragraph": paragraph,
                "label": label,
                "chapter": chapter,
                "text": consolidated_text,
                "references": references,
                "referenced_by": referenced_by,
                "interpreted_by": interpreted_by,
                "has_versions": bool(versions),
                "version_count": len(versions),
            }
        )


def build_outline_doc(acc: _LawAccumulator) -> dict:
    """Build the per-law skim outline document."""
    return {
        "law": acc.law_name,
        "abbrev": acc.abbrev,
        "title": acc.title,
        "rt_url": acc.rt_url,
        "provision_count": len(acc.outline),
        "provisions": acc.outline,
    }


def build_context_pack(
    acc: _LawAccumulator,
    *,
    krr_dir: Path,
    decision_index: dict[str, dict],
) -> dict:
    """Build the per-law denormalised bundle (provisions + versions + refs +
    cases + amendments + sanctions) in one fetch."""
    amendments = _overlay_nodes(
        krr_dir, AMENDMENTS_DIRNAME, "amendments_", acc.law_name
    )
    sanctions = _overlay_nodes(
        krr_dir, SANCTIONS_DIRNAME, "sanctions_", acc.law_name
    )
    cases = [
        decision_index.get(case_id, {"iri": case_id})
        for case_id in sorted(acc.case_ids)
    ]
    return {
        "law": acc.law_name,
        "abbrev": acc.abbrev,
        "title": acc.title,
        "rt_url": acc.rt_url,
        "kehtiv": acc.kehtiv,
        "in_force": acc.in_force,
        "counts": {
            "provisions": len(acc.provisions),
            "versions": len(acc.versions),
            "amendments": len(amendments),
            "sanctions": len(sanctions),
            "cases": len(cases),
        },
        "provisions": acc.provisions,
        "versions": acc.versions,
        "amendments": amendments,
        "sanctions": sanctions,
        "cases": cases,
    }


# ---------------------------------------------------------------------------
# Corpus enumeration
# ---------------------------------------------------------------------------
def iter_index_laws(krr_dir: Path) -> list[dict]:
    """Return the INDEX ``laws`` entries (sorted by ``name``), or ``[]``."""
    doc = _load_json(krr_dir / "INDEX.json")
    if not isinstance(doc, dict):
        return []
    laws = doc.get("laws")
    if not isinstance(laws, list):
        return []
    return sorted(
        (law for law in laws if isinstance(law, dict) and law.get("name")),
        key=lambda law: law["name"],
    )


def _file_slug(filename: str) -> str:
    """Return the peep stem (the ``_peep.json`` suffix removed)."""
    stem = filename
    if stem.endswith(".json"):
        stem = stem[: -len(".json")]
    if stem.endswith("_peep"):
        stem = stem[: -len("_peep")]
    return stem


# ---------------------------------------------------------------------------
# Served entry point + manifest
# ---------------------------------------------------------------------------
def render_llms_txt(stats: dict) -> str:
    """Render the llms.txt entry point (https://llmstxt.org)."""
    return f"""# Estonian Legal Ontology — Retrieval Projection

> A machine-readable JSON-LD/RDF ontology of Estonian and EU law (~1,120 enacted
> laws plus state and municipal regulations, Riigikohus decisions, and EU acts).
> This retrieval projection flattens the graph into self-contained, citeable
> records so RAG and agent developers do not have to reassemble three nodes
> across two files to cite one paragraph.

The core unit is a provision-version. Each line of `chunks.jsonl` is one
`{{provision_iri, redaction_id, paragraph, act_title, abbrev, rt_url, valid_from,
valid_to, in_force, text}}` record: a single §-level provision in one redaction,
independently citeable via `provision_iri` + `rt_url`, carrying the untruncated
text and the validity window. Filter `in_force == true` for the current law;
group by `provision_iri` for a paragraph's amendment history.

Projection built from ontology version {ONTOLOGY_VERSION}, evaluation date
{stats['evaluation_date']}. Provision-version records: {stats['total_chunks']};
laws: {stats['laws']}; per-law outlines and context packs: {stats['outlines']}.

## Retrieval artifacts
- [chunks.jsonl](chunks.jsonl): one JSON record per provision-version — the core
  retrieval / embedding unit (untruncated text).
- [outlines/](outlines/): per-law skim tier — `{{paragraph, label, chapter,
  summary, has_versions}}` per provision, to navigate a large law without
  fetching it whole.
- [context_packs/](context_packs/): per-law denormalised bundle — provisions +
  versions + references + cases + amendments + sanctions in one fetch.

## Source corpus
- [INDEX.json](../INDEX.json): the law manifest these projections enumerate.
- [combined_ontology.jsonld](../combined_ontology.jsonld): the load-everything
  graph (Git LFS).
- [README](../../README.md): corpus overview, schema, and SPARQL examples.
"""


def write_manifest(out_dir: Path, stats: dict) -> None:
    """Write the deterministic projection manifest."""
    save_json(out_dir / "manifest.json", stats)


def _artifact_size(path: Path) -> int:
    """Return a file's byte size, or 0 if absent."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def generate(
    *,
    krr_dir: Path,
    out_dir: Path,
    eval_date: str,
    limit: int | None = None,
    with_cases: bool = True,
    chunks_only: bool = False,
    sample_chunks: int = SAMPLE_CHUNKS_DEFAULT,
    sample_law: str = SAMPLE_LAW_DEFAULT,
) -> dict:
    """Generate the retrieval projection; return a stats/manifest dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    outlines_dir = out_dir / "outlines"
    context_dir = out_dir / "context_packs"
    if not chunks_only:
        outlines_dir.mkdir(parents=True, exist_ok=True)
        context_dir.mkdir(parents=True, exist_ok=True)

    registry = _load_json(ABBREV_REGISTRY_PATH)
    if not isinstance(registry, dict):
        registry = {}
    decision_index = (
        build_decision_index(krr_dir) if (with_cases and not chunks_only) else {}
    )

    laws = iter_index_laws(krr_dir)
    if limit is not None:
        laws = laws[:limit]

    versions_dir = krr_dir / PROVISION_VERSIONS_DIRNAME
    version_map = load_version_map(versions_dir)
    chunks_tmp = out_dir / "chunks.jsonl.tmp"
    stats = {
        "ontology_version": ONTOLOGY_VERSION,
        "evaluation_date": eval_date,
        "laws": 0,
        "laws_skipped_deprecated": 0,
        "total_chunks": 0,
        "version_records": 0,
        "consolidated_records": 0,
        "outlines": 0,
        "context_packs": 0,
        "cases_indexed": len(decision_index),
    }
    sample_chunk_lines: list[str] = []
    sample_outline: dict | None = None
    sample_context: dict | None = None
    first_acc: _LawAccumulator | None = None

    with open(chunks_tmp, "w", encoding="utf-8") as chunks_fh:
        for law in laws:
            law_name = law["name"]
            files = sorted(law.get("files") or [])
            acc = _LawAccumulator(law_name)
            deprecated = False
            for filename in files:
                doc = _load_json(krr_dir / filename)
                if doc is None:
                    continue
                is_dep, _ = act_deprecation(doc)
                if is_dep:
                    deprecated = True
                    break
                file_slug = _file_slug(filename)
                process_law_file(
                    doc,
                    file_slug=file_slug,
                    law_name=law_name,
                    registry=registry,
                    version_map=version_map,
                    eval_date=eval_date,
                    acc=acc,
                )
            if deprecated:
                stats["laws_skipped_deprecated"] += 1
                continue

            stats["laws"] += 1
            for record in acc.chunks:
                line = json.dumps(record, ensure_ascii=False)
                chunks_fh.write(line + "\n")
                if record["redaction_id"] is None:
                    stats["consolidated_records"] += 1
                else:
                    stats["version_records"] += 1
                if len(sample_chunk_lines) < sample_chunks:
                    sample_chunk_lines.append(line)
            stats["total_chunks"] += len(acc.chunks)

            if not chunks_only:
                outline_doc = build_outline_doc(acc)
                context_doc = build_context_pack(
                    acc, krr_dir=krr_dir, decision_index=decision_index
                )
                save_json(outlines_dir / f"{law_name}.json", outline_doc)
                save_json(context_dir / f"{law_name}.json", context_doc)
                stats["outlines"] += 1
                stats["context_packs"] += 1
                if law_name == sample_law:
                    sample_outline = outline_doc
                    sample_context = context_doc
            if first_acc is None:
                first_acc = acc

    os.replace(chunks_tmp, out_dir / "chunks.jsonl")

    # Fall back to the first processed law for samples when the configured
    # sample law is absent (e.g. a --limit run or a test corpus).
    if not chunks_only and sample_outline is None and first_acc is not None:
        sample_outline = build_outline_doc(first_acc)
        sample_context = build_context_pack(
            first_acc, krr_dir=krr_dir, decision_index=decision_index
        )

    stats["chunks_bytes"] = _artifact_size(out_dir / "chunks.jsonl")

    if not chunks_only:
        # Served entry point + samples (committed; the heavy trees are ignored).
        (out_dir / "llms.txt").write_text(render_llms_txt(stats), encoding="utf-8")
        with open(out_dir / "chunks.sample.jsonl", "w", encoding="utf-8") as fh:
            for line in sample_chunk_lines:
                fh.write(line + "\n")
        if sample_outline is not None:
            save_json(out_dir / "sample_outline.json", sample_outline)
        if sample_context is not None:
            save_json(out_dir / "sample_context_pack.json", sample_context)

    write_manifest(out_dir, stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit the retrieval projection (chunks / outlines / "
        "context packs / llms.txt) for RAG and agent developers (#523)."
    )
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="output directory (default: <krr-dir>/retrieval)",
    )
    parser.add_argument(
        "--evaluation-date",
        default=BUILD_EVALUATION_DATE,
        help="pinned as-of date for in_force selection (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="process only the first N laws"
    )
    parser.add_argument(
        "--no-cases",
        action="store_true",
        help="skip the court-decision index (faster; context-pack cases become "
        "bare IRIs)",
    )
    parser.add_argument(
        "--chunks-only",
        action="store_true",
        help="emit only chunks.jsonl + manifest (skip outlines/context packs/"
        "samples)",
    )
    parser.add_argument(
        "--sample-chunks", type=int, default=SAMPLE_CHUNKS_DEFAULT
    )
    parser.add_argument("--sample-law", default=SAMPLE_LAW_DEFAULT)
    args = parser.parse_args(argv)

    out_dir = args.out_dir or (args.krr_dir / DEFAULT_OUT_DIRNAME)
    stats = generate(
        krr_dir=args.krr_dir,
        out_dir=out_dir,
        eval_date=args.evaluation_date,
        limit=args.limit,
        with_cases=not args.no_cases,
        chunks_only=args.chunks_only,
        sample_chunks=args.sample_chunks,
        sample_law=args.sample_law,
    )
    print(
        "retrieval projection: "
        f"{stats['laws']} laws, {stats['total_chunks']} chunks "
        f"({stats['version_records']} version + "
        f"{stats['consolidated_records']} consolidated), "
        f"{stats['outlines']} outlines, {stats['context_packs']} context packs, "
        f"{stats['cases_indexed']} cases indexed, "
        f"{stats['laws_skipped_deprecated']} deprecated laws skipped"
    )
    print(f"  chunks.jsonl: {stats['chunks_bytes']:,} bytes -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
