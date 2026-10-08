#!/usr/bin/env python3
"""KOV <-> state topical similarity index (#729).

Answers the cross-layer question "which state laws / state regulations deal
with the same subject as this municipal (KOV) regulation", and its inverse
"which municipal regulations regulate the subject of this state act" — the
"how do other municipalities regulate this" lookup.

This is a **topical** index, not a structural one. The existing KOV pass in
``generate_similarity_index.py`` restates each KOV act's ``estleg:issuedUnder``
enabling-act link as a directed KOV->state ``estleg:similarAct`` edge; here the
enabling relation is deliberately kept OUT of the score:

* the KOV act's ``estleg:preambleText`` ("Määrus kehtestatakse … seaduse § N
  alusel") is not indexed — it is the textual restatement of ``issuedUnder``;
* ``estleg:issuedUnder`` / ``estleg:implementsCitation`` are never read for
  scoring. They are only consulted afterwards to flag (``enabling`` = 1)
  whether a topical match also happens to be an enabling act, so a reviewer
  can see how often the two signals agree.

Method (reuses the tokenizer and TF-IDF helpers of
``generate_similarity_index.py``: ``build_term_frequencies`` with its
stop-word list, ``compute_idf`` smoothed IDF, ``tfidf_vector``):

1. One document per act: KOV acts (``regulations/kov/**``), laws (root
   ``*_peep.json``, multi-part codes merged through ``estleg:partOfAct``) and
   state regulations (``regulations/riik/``). Text = act title + the
   ``estleg:legalText`` (else ``estleg:summary``) of every ``_Par_`` provision
   that is not boilerplate (``is_boilerplate``: repeal / entry-into-force /
   omitted-text clauses). Repealed (``temporalStatus == "repealed"``) and
   ``owl:deprecated`` acts are excluded on both sides.
2. One IDF over the joint KOV + state document set; each vector is pruned to
   its :data:`ACT_MAX_TERMS` heaviest terms (ties by term) and L2-renormalised,
   so the all-pairs cosine is an exact sparse dot product over a bounded
   inverted index (pure Python, no numpy).
3. ``kovToLaw`` / ``kovToStateRegulation``: per KOV act, the :data:`TOP_K`
   best laws and, separately, the :data:`TOP_K` best state regulations
   (score >= :data:`MIN_SCORE`); ``stateToKov``: per state act, the
   :data:`TOP_K` best KOV acts. Act-form words (:data:`TOPICAL_STOPWORDS`)
   and proper nouns (:func:`proper_noun_terms`) are removed first. Scores are rounded to :data:`SCORE_DECIMALS` before ranking and ties
   break on the IRI, so the output is byte-deterministic.
4. ``provisionMatches`` (paragraph level): for the first
   :data:`PROVISION_CAP_PER_ACT` non-boilerplate provisions of each KOV act, the
   single best provision among the matched laws and regulations of that act (score
   >= :data:`PROVISION_MIN_SCORE`). This is what ``provisions_by_type.kov`` in
   ``reports/similarity_report.json`` counts.

There is **no gold set**: ``quality_evaluation.status`` is ``not_evaluated``.
The output is a non-graph sidecar (like ``reports/similarity_index.json``,
#462) — nothing is written into any peep.

Output: ``krr_outputs/similarity/kov_state_similarity_index.json``.
CLI: ``python3 scripts/generate_kov_state_similarity.py [--update-report]``.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import re
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from estleg.estleg_common import BUILD_EVALUATION_DATE, jsonld_text
from estleg.generate_similarity_index import (
    MIN_SHARED_KEYWORDS,
    build_term_frequencies,
    classify_act_type,
    compute_idf,
    extract_keywords,
    find_act_node,
    is_boilerplate,
    tfidf_vector,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
INDEX_RELPATH = "similarity/kov_state_similarity_index.json"
REPORT_RELPATH = "reports/similarity_report.json"

SCORE_MODEL = "tfidf-cosine-topical"
ALGORITHM_VERSION = "1"
TOP_K = 5
MIN_SCORE = 0.05
ACT_MAX_TERMS = 150
PROVISION_CAP_PER_ACT = 25
PROVISION_MAX_TERMS = 48
PROVISION_MIN_SCORE = 0.1
SCORE_DECIMALS = 4

# Act-form and municipal-structure words: they say what KIND of act this is
# ("… korra kinnitamine", "… määruse muutmine") or who issued it, not what
# it regulates. Dropped from the topical documents on both sides.
TOPICAL_STOPWORDS = frozenset({
    "kinnitamine", "kinnitamise", "kinnitada", "kinnitatakse", "kinnitatud",
    "korra", "korrad", "korrale", "kord", "määrus", "määruse", "määrusega",
    "määruses", "määrusele", "määruste", "muutmine", "muutmise", "muuta",
    "muudetakse", "muudatus", "muudatused", "tunnistamine", "tunnistada",
    "jõustub", "jõustumine", "jõustumise", "lisale", "lisas", "lisaga",
    "vallavalitsus", "vallavalitsuse", "vallavolikogu", "vallavolikogule",
    "linnavalitsus", "linnavalitsuse", "linnavolikogu", "linnavolikogule",
    "valla", "vallas", "vallale", "vallast", "linna", "linnas", "linnale",
    "linnast", "omavalitsus", "omavalitsuse", "omavalitsusüksus",
    "omavalitsusüksuse", "volikogu", "volikogule", "volikogus",
})
# A token is a proper noun (a place, institution or person name such as
# "Tartu", "Tähtvere", "Kihnu") when it is written capitalised in at least
# this share of its non-sentence-initial occurrences, with at least
# PROPER_NOUN_MIN_COUNT such occurrences. Place names otherwise dominate the
# municipal documents and pull every Tartu act towards "Tartu Vangla".
PROPER_NOUN_SHARE = 0.9
PROPER_NOUN_MIN_COUNT = 3
_WORD_RE = re.compile(r"[A-Za-zÄÖÜÕŠŽäöüõšž]+|[.!?:;]")
_KOV_TITLE_SUFFIXES = (" (määrus)", " (otsus)", " (korraldus)")


# ---------------------------------------------------------------------------
# Corpus reading
# ---------------------------------------------------------------------------


def corpus_files(krr_dir: Path) -> dict[str, list[Path]]:
    """``{"kov": [...], "state": [...]}`` peep files, sorted for determinism."""
    def _sorted(paths) -> list[Path]:
        return sorted(paths, key=lambda p: p.as_posix())

    state = _sorted(krr_dir.glob("*_peep.json"))
    state += _sorted((krr_dir / "regulations" / "riik").glob("*_peep.json"))
    kov = _sorted((krr_dir / "regulations" / "kov").glob("**/*_peep.json"))
    return {"kov": kov, "state": state}


def _read_graph(path: Path) -> list[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    graph = doc.get("@graph") if isinstance(doc, dict) else None
    return [n for n in graph if isinstance(n, dict)] if isinstance(graph, list) else []


def _is_provision(node_id: str) -> bool:
    return "_Par_" in node_id and "_Lg_" not in node_id


def provision_text(node: dict) -> str:
    """``legalText`` (else ``summary``) of a provision; '' for boilerplate."""
    text = jsonld_text(node.get("estleg:legalText", ""), prefer_language="et").strip()
    if not text:
        text = jsonld_text(node.get("estleg:summary", ""), prefer_language="et").strip()
    if not text or is_boilerplate(text):
        return ""
    return text


def _act_title(node: dict, kind: str) -> str:
    if kind == "kov":
        title = jsonld_text(node.get("rdfs:label", ""), prefer_language="et")
        for suffix in _KOV_TITLE_SUFFIXES:
            title = title.removesuffix(suffix)
        return title or jsonld_text(node.get("dc:source", ""))
    for key in ("dc:source", "dcterms:title", "rdfs:label"):
        title = jsonld_text(node.get(key, ""), prefer_language="et")
        if title:
            return title
    return ""


def _excluded(node: dict) -> bool:
    if node.get("estleg:temporalStatus") == "repealed":
        return True
    dep = node.get("owl:deprecated")
    if isinstance(dep, dict):
        dep = dep.get("@value")
    return dep is True or str(dep).lower() == "true"


def read_acts(files: list[Path], kind: str, *, keep_provisions: bool = False) -> dict[str, dict]:
    """Group the files' acts and provisions into act records keyed by act IRI.

    Provisions attach to their ``estleg:partOfAct`` act, else to the act
    node of the same file, so a multi-part code (``asjaoigusseadus_osa1..8``
    + ``asjaoigusseadus_map``) becomes one document. ``kind`` is ``"kov"``
    or ``"state"``; state acts are typed ``law`` / ``state_regulation``.
    With ``keep_provisions`` the record keeps ``(provision_id, text)`` pairs.
    """
    acts: dict[str, dict] = {}
    orphan: dict[str, list[tuple[str, str]]] = defaultdict(list)
    owner_files: dict[str, set[Path]] = defaultdict(set)

    def record(iri: str) -> dict:
        return acts.setdefault(iri, {"iri": iri, "title": "", "type": None, "files": [],
                                     "issuedUnder": [], "excluded": False,
                                     "texts": [], "provisions": []})

    for path in files:
        graph = _read_graph(path)
        file_act: str | None = None
        node = find_act_node({"@graph": graph})
        node_id = node.get("@id") if node is not None else None
        if isinstance(node_id, str) and node_id.startswith("estleg:"):
            file_act = node_id
            rec = record(node_id)
            rec["title"] = rec["title"] or _act_title(node, kind)
            act_type = "kov" if kind == "kov" else classify_act_type(path, node)
            rec["type"] = rec["type"] or act_type
            rec["excluded"] = rec["excluded"] or _excluded(node)
            rec["issuedUnder"] = sorted({
                ref["@id"] for ref in node.get("estleg:issuedUnder", []) or []
                if isinstance(ref, dict) and isinstance(ref.get("@id"), str)
            })
            owner_files[node_id].add(path)
        for node in graph:
            node_id = node.get("@id")
            if not isinstance(node_id, str) or not _is_provision(node_id):
                continue
            text = provision_text(node)
            if not text:
                continue
            owner = node.get("estleg:partOfAct")
            owner = owner.get("@id") if isinstance(owner, dict) else None
            owner = owner or file_act
            if owner is None:
                continue
            owner_files[owner].add(path)
            if owner in acts:
                rec = acts[owner]
                rec["texts"].append(text)
                if keep_provisions:
                    rec["provisions"].append((node_id, text))
            else:
                orphan[owner].append((node_id, text))
    # Provisions whose partOfAct node lives in a later file.
    for owner, items in orphan.items():
        if owner in acts:
            acts[owner]["texts"].extend(t for _, t in items)
            if keep_provisions:
                acts[owner]["provisions"].extend(items)
    for iri, rec in acts.items():
        rec["files"] = sorted(owner_files.get(iri, ()), key=lambda p: p.as_posix())
    keep_types = {"kov"} if kind == "kov" else {"law", "state_regulation"}
    return {
        iri: rec for iri, rec in sorted(acts.items())
        if not rec["excluded"] and rec["type"] in keep_types
    }


# ---------------------------------------------------------------------------
# Vectors and ranking
# ---------------------------------------------------------------------------


def proper_noun_terms(texts) -> frozenset[str]:
    """Lower-cased tokens written capitalised mid-sentence (names).

    Sentence-initial tokens (first token, or after ``. ! ? : ;``) are not
    counted either way, so an ordinary word that opens many sentences is
    not mistaken for a name.
    """
    capital: Counter = Counter()
    lower: Counter = Counter()
    for text in texts:
        initial = True
        for match in _WORD_RE.finditer(text):
            token = match.group()
            if len(token) == 1 and token in ".!?:;":
                initial = True
                continue
            if not initial:
                if token[0].isupper():
                    if not token.isupper():  # skip ALL-CAPS headings / acronyms
                        capital[token.lower()] += 1
                else:
                    lower[token] += 1
            initial = False
    return frozenset(
        term for term, n in capital.items()
        if n >= PROPER_NOUN_MIN_COUNT and n >= PROPER_NOUN_SHARE * (n + lower.get(term, 0))
    )


def topical_tf(text: str, stop: frozenset[str]) -> Counter:
    """``build_term_frequencies`` minus act-form words and proper nouns."""
    tf = build_term_frequencies(text)
    for term in [t for t in tf if t in stop]:
        del tf[term]
    return tf


def prune_vector(vec: dict[str, float], max_terms: int) -> dict[str, float]:
    """Keep the ``max_terms`` heaviest terms (ties by term) and renormalise."""
    if len(vec) > max_terms:
        kept = sorted(vec.items(), key=lambda kv: (-kv[1], kv[0]))[:max_terms]
    else:
        kept = list(vec.items())
    norm = math.sqrt(sum(w * w for _, w in kept))
    if norm == 0.0:
        return {}
    return {t: w / norm for t, w in sorted(kept)}


def _postings(vectors: list[dict[str, float]]) -> dict[str, list[tuple[int, float]]]:
    index: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for i, vec in enumerate(vectors):
        for term, weight in vec.items():
            index[term].append((i, weight))
    return index


def _score(vec: dict[str, float], postings: dict[str, list[tuple[int, float]]]) -> dict[int, float]:
    acc: dict[int, float] = defaultdict(float)
    for term in sorted(vec):
        weight = vec[term]
        for j, w in postings.get(term, ()):
            acc[j] += weight * w
    return acc


def rank_acts(
    kov_vecs: list[dict[str, float]],
    kov_keys: list[str],
    state_vecs: list[dict[str, float]],
    state_keys: list[str],
    state_groups: list[str],
    *,
    top_k: int = TOP_K,
    floor: float = MIN_SCORE,
) -> tuple[dict[int, dict[str, list[tuple[int, float]]]], dict[int, list[tuple[int, float]]]]:
    """Forward (KOV -> state, top-k per state group) and inverse lists.

    ``state_groups[j]`` is the group of state act ``j`` (``law`` /
    ``state_regulation``); the forward lists hold the top-k of EACH group.
    ``kov_keys`` / ``state_keys`` are sorted IRIs, so the inverse heap's tie
    key ``-kov_index`` prefers the smaller IRI, matching the forward order.
    """
    postings = _postings(state_vecs)
    groups = sorted(set(state_groups))
    forward: dict[int, dict[str, list[tuple[int, float]]]] = {}
    heaps: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for i, vec in enumerate(kov_vecs):
        scores = {j: round(raw, SCORE_DECIMALS) for j, raw in _score(vec, postings).items()}
        scores = {j: s for j, s in scores.items() if s >= floor}
        by_group: dict[str, list[tuple[int, float]]] = {g: [] for g in groups}
        for j, s in scores.items():
            by_group[state_groups[j]].append((j, s))
        forward[i] = {
            g: sorted(items, key=lambda js: (-js[1], state_keys[js[0]]))[:top_k]
            for g, items in by_group.items() if items
        }
        for j, s in scores.items():
            heap = heaps[j]
            item = (s, -i)
            if len(heap) < top_k:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
    inverse = {
        j: [(-neg_i, s) for s, neg_i in sorted(heap, key=lambda it: (-it[0], -it[1]))]
        for j, heap in heaps.items()
    }
    return forward, inverse


def _vectorise(texts: list[str], idf: dict[str, float], max_terms: int,
               stop: frozenset[str] = frozenset()) -> dict[str, float]:
    return prune_vector(tfidf_vector(topical_tf(" ".join(texts), stop), idf), max_terms)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

GROUP_MAPS = {"law": "kovToLaw", "state_regulation": "kovToStateRegulation"}


def build_index(krr_dir: Path | None = None, *, verbose: bool = True) -> dict:
    """Compute the KOV <-> state topical index (no file writes)."""
    krr_dir = KRR_DIR if krr_dir is None else krr_dir
    log = print if verbose else (lambda *a, **k: None)
    started = time.perf_counter()
    files = corpus_files(krr_dir)
    state_acts = read_acts(files["state"], "state")
    kov_acts = read_acts(files["kov"], "kov")
    log(f"  [KOV-state] {len(kov_acts)} KOV acts, {len(state_acts)} state acts "
        f"({time.perf_counter() - started:.1f}s)")

    kov_keys = [k for k in kov_acts if kov_acts[k]["texts"] or kov_acts[k]["title"]]
    state_keys = [k for k in state_acts if state_acts[k]["texts"] or state_acts[k]["title"]]
    documents = [(kov_acts, k) for k in kov_keys] + [(state_acts, k) for k in state_keys]
    proper = proper_noun_terms(
        text for acts, k in documents for text in (acts[k]["title"], *acts[k]["texts"]))
    stop = TOPICAL_STOPWORDS | proper
    tfs = [topical_tf(" ".join([acts[k]["title"], *acts[k]["texts"]]), stop)
           for acts, k in documents]
    idf = compute_idf(tfs)
    vecs = [prune_vector(tfidf_vector(tf, idf), ACT_MAX_TERMS) for tf in tfs]
    kov_vecs, state_vecs = vecs[:len(kov_keys)], vecs[len(kov_keys):]
    del tfs, vecs, documents
    for rec in (*kov_acts.values(), *state_acts.values()):
        rec["texts"] = []  # free the act text; provisions are re-read below
    log(f"  [KOV-state] vocabulary {len(idf)} terms, {len(proper)} proper nouns dropped "
        f"({time.perf_counter() - started:.1f}s)")

    state_groups = [state_acts[k]["type"] for k in state_keys]
    forward, inverse = rank_acts(kov_vecs, kov_keys, state_vecs, state_keys, state_groups)
    del kov_vecs, state_vecs
    log(f"  [KOV-state] ranked ({time.perf_counter() - started:.1f}s)")

    per_group: dict[str, dict[str, list]] = {g: {} for g in GROUP_MAPS}
    for i, key in enumerate(kov_keys):
        enabling = set(kov_acts[key]["issuedUnder"])
        for group, items in forward.get(i, {}).items():
            per_group[group][key] = [
                [state_keys[j], s, 1 if state_keys[j] in enabling else 0] for j, s in items
            ]
    state_to_kov = {
        state_keys[j]: [[kov_keys[i], s] for i, s in inverse[j]]
        for j in sorted(inverse, key=lambda j: state_keys[j])
    }
    targets_by_kov: dict[str, list[str]] = defaultdict(list)
    for group in sorted(per_group):
        for key, peers in per_group[group].items():
            targets_by_kov[key].extend(t for t, _s, _e in peers)

    provision_matches, provisions_analysed = _provision_pass(
        files, targets_by_kov, state_acts, idf, stop)
    log(f"  [KOV-state] provision pass: {provisions_analysed} KOV provisions, "
        f"{len(provision_matches)} matched ({time.perf_counter() - started:.1f}s)")

    labels: dict[str, str] = {}
    for key, targets in targets_by_kov.items():
        labels[key] = kov_acts[key]["title"]
        for target in targets:
            labels[target] = state_acts[target]["title"]
    for key, peers in state_to_kov.items():
        labels[key] = state_acts[key]["title"]
        for target, _s in peers:
            labels[target] = kov_acts[target]["title"]

    laws = per_group["law"]
    in_degree = Counter(t for peers in laws.values() for t, _s, _e in peers)
    counts = {
        "kovActs": len(kov_keys),
        "stateActs": len(state_keys),
        "stateActsByType": {g: state_groups.count(g) for g in GROUP_MAPS},
        "kovActsWithMatches": len(targets_by_kov),
        "kovToLawPairs": sum(len(p) for p in laws.values()),
        "kovToStateRegulationPairs": sum(len(p) for p in per_group["state_regulation"].values()),
        "stateActsWithKovMatches": len(state_to_kov),
        "stateToKovPairs": sum(len(p) for p in state_to_kov.values()),
        "kovProvisionsAnalysed": provisions_analysed,
        "kovProvisionsMatched": len(provision_matches),
        "vocabularySize": len(idf),
        "properNounsDropped": len(proper),
        "kovActsWithIssuedUnder": sum(1 for k in kov_keys if kov_acts[k]["issuedUnder"]),
        "top1LawIsEnablingAct": sum(1 for p in laws.values() if p and p[0][2]),
        "top5LawsContainEnablingAct": sum(1 for p in laws.values() if any(x[2] for x in p)),
        "lawHubs": [[t, n] for t, n in sorted(in_degree.items(), key=lambda kv: (-kv[1], kv[0]))[:10]],
    }
    return {
        "generated": BUILD_EVALUATION_DATE,
        "relation_semantics": "candidate",
        "description": (
            "Topical KOV<->state similarity: for each municipal regulation the "
            "most topically similar state laws and state regulations, and the "
            "inverse. Non-graph sidecar (#462/#729)."
        ),
        "algorithm": {
            "name": SCORE_MODEL,
            "version": ALGORITHM_VERSION,
            "tokenizer": "generate_similarity_index.build_term_frequencies (STOPWORDS, min length 4)",
            "idf": "smoothed, joint over KOV + state act documents",
            "document": "act title + legalText (else summary) of non-boilerplate _Par_ provisions",
            "structural_exclusions": [
                "estleg:preambleText (restates the enabling act) is not indexed",
                "estleg:issuedUnder / estleg:implementsCitation are not scored; "
                "they only set the 'enabling' column after ranking",
                "act-form / municipal-structure words (TOPICAL_STOPWORDS) and "
                "proper nouns (place, institution and person names)",
                "repealed and owl:deprecated acts",
            ],
        },
        "parameters": {
            "top_k": TOP_K,
            "min_score": MIN_SCORE,
            "act_max_terms": ACT_MAX_TERMS,
            "provision_cap_per_act": PROVISION_CAP_PER_ACT,
            "provision_max_terms": PROVISION_MAX_TERMS,
            "provision_min_score": PROVISION_MIN_SCORE,
            "score_decimals": SCORE_DECIMALS,
            "min_keywords_per_provision": MIN_SHARED_KEYWORDS,
            "proper_noun_share": PROPER_NOUN_SHARE,
            "proper_noun_min_count": PROPER_NOUN_MIN_COUNT,
        },
        "quality_evaluation": {
            "status": "not_evaluated",
            "note": (
                "No gold set exists. A gold set needs, per sampled KOV act, the "
                "state acts a domain reviewer judges to regulate the same "
                "subject (graded), stratified by municipality size and "
                "regulation type; precision@5 / nDCG@5 are then computable."
            ),
        },
        "columns": {
            "kovToLaw": ["law", "score", "enabling (1 = also in issuedUnder)"],
            "kovToStateRegulation": ["state_regulation", "score", "enabling (1 = also in issuedUnder)"],
            "stateToKov": ["kov_act", "score"],
            "provisionMatches": ["state_provision", "score"],
        },
        "counts": counts,
        "kovToLaw": laws,
        "kovToStateRegulation": per_group["state_regulation"],
        "stateToKov": state_to_kov,
        "provisionMatches": provision_matches,
        "labels": dict(sorted(labels.items())),
    }


def _provision_pass(
    files: dict[str, list[Path]],
    targets_by_kov: dict[str, list[str]],
    state_acts: dict[str, dict],
    idf: dict[str, float],
    stop: frozenset[str],
) -> tuple[dict[str, list], int]:
    """Best state provision per KOV provision, among the act's matched acts."""
    needed = sorted({t for targets in targets_by_kov.values() for t in targets})
    needed_files = sorted({p for t in needed for p in state_acts[t]["files"]},
                          key=lambda p: p.as_posix())
    state_prov = read_acts(needed_files, "state", keep_provisions=True)
    prov_index: dict[str, tuple[list[str], dict]] = {}
    for iri in needed:
        rec = state_prov.get(iri)
        if rec is None:
            continue
        ids, vecs = [], []
        for pid, text in sorted(rec["provisions"]):
            vec = _vectorise([text], idf, PROVISION_MAX_TERMS, stop)
            if vec:
                ids.append(pid)
                vecs.append(vec)
        prov_index[iri] = (ids, _postings(vecs))
    del state_prov

    matches: dict[str, list] = {}
    analysed = 0
    for path in files["kov"]:
        for iri, rec in read_acts([path], "kov", keep_provisions=True).items():
            targets = targets_by_kov.get(iri)
            if not targets:
                continue
            candidates = [prov_index[t] for t in targets if t in prov_index]
            kept = [(pid, text) for pid, text in rec["provisions"]
                    if len(extract_keywords(text)) >= MIN_SHARED_KEYWORDS]
            for pid, text in kept[:PROVISION_CAP_PER_ACT]:
                vec = _vectorise([text], idf, PROVISION_MAX_TERMS, stop)
                if not vec:
                    continue
                analysed += 1
                best: tuple[float, str] | None = None
                for ids, postings in candidates:
                    for j, raw in _score(vec, postings).items():
                        s = round(raw, SCORE_DECIMALS)
                        if best is None or s > best[0] or (s == best[0] and ids[j] < best[1]):
                            best = (s, ids[j])
                if best is not None and best[0] >= PROVISION_MIN_SCORE:
                    matches[pid] = [best[1], best[0]]
    return dict(sorted(matches.items())), analysed


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_LINE_MAPS = ("kovToLaw", "kovToStateRegulation", "stateToKov", "provisionMatches", "labels")


def dumps_index(payload: dict) -> str:
    """Serialise with one map entry per line (diffable, compact)."""
    def one(value) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    lines = ["{"]
    keys = list(payload)
    for n, key in enumerate(keys):
        tail = "," if n < len(keys) - 1 else ""
        value = payload[key]
        if key in _LINE_MAPS and isinstance(value, dict):
            lines.append(f"  {one(key)}: {{")
            items = list(value.items())
            for m, (k, v) in enumerate(items):
                lines.append(f"    {one(k)}: {one(v)}{',' if m < len(items) - 1 else ''}")
            lines.append(f"  }}{tail}")
        else:
            lines.append(f"  {one(key)}: {json.dumps(value, ensure_ascii=False)}{tail}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def report_section(index: dict) -> dict:
    """The ``kov_state_topical`` block merged into similarity_report.json."""
    return {
        "index": INDEX_RELPATH,
        "algorithm": index["algorithm"]["name"],
        "version": index["algorithm"]["version"],
        "relation_semantics": index["relation_semantics"],
        "quality_evaluation": index["quality_evaluation"]["status"],
        "parameters": index["parameters"],
        "counts": index["counts"],
    }


def merge_into_report(report: dict, index: dict) -> dict:
    """Stamp the KOV topical counts into a similarity report dict (in place).

    ``provisions_by_type.kov`` / ``candidate_files_by_type.kov`` count the
    KOV provisions and acts this paragraph-level pass analysed; the
    keyword-Jaccard provision pass itself stays laws + state only.
    """
    counts = index["counts"]
    report.setdefault("provisions_by_type", {})["kov"] = counts["kovProvisionsAnalysed"]
    report.setdefault("candidate_files_by_type", {})["kov"] = counts["kovActs"]
    report["provisions_by_type_note"] = (
        "law / state_regulation / other: keyword-Jaccard provision pass. kov: "
        "KOV provisions scored at paragraph level by the KOV<->state topical "
        f"pass ({INDEX_RELPATH})."
    )
    report["kov_state_topical"] = report_section(index)
    return report


def write_outputs(index: dict, krr_dir: Path | None = None, *, update_report: bool = True) -> Path:
    krr_dir = KRR_DIR if krr_dir is None else krr_dir
    index_path = krr_dir / INDEX_RELPATH
    _atomic_write(index_path, dumps_index(index))
    report_path = krr_dir / REPORT_RELPATH
    if update_report and report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        merge_into_report(report, index)
        _atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return index_path


def run(krr_dir: Path | None = None, *, update_report: bool = True, verbose: bool = True) -> dict:
    """Build the index, write it (and the report section); return the index."""
    index = build_index(krr_dir, verbose=verbose)
    path = write_outputs(index, krr_dir, update_report=update_report)
    if verbose:
        c = index["counts"]
        print(f"  Wrote {path} ({c['kovToLawPairs']} KOV->law, "
              f"{c['kovToStateRegulationPairs']} KOV->state regulation, "
              f"{c['stateToKovPairs']} state->KOV, {c['kovProvisionsMatched']} provision matches)")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KOV <-> state topical similarity index (#729).")
    parser.add_argument("--no-report", action="store_true",
                        help="Do not merge the counts into reports/similarity_report.json.")
    args = parser.parse_args(argv)
    run(update_report=not args.no_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
