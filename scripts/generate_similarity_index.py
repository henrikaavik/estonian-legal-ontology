#!/usr/bin/env python3
"""
Generate a semantic similarity index using keyword overlap between provisions.

Compares provisions across different laws by shared keywords in their
summary text. Adds estleg:semanticallySimilarTo links for provisions
with high keyword overlap from different laws.

Generates:
  - krr_outputs/similarity_index.json
  - krr_outputs/similarity_report.json
  - krr_outputs/reports/similarity_sample.json   (only with --emit-sample N)
  - krr_outputs/similarity/kov_similarity_index.json (KOV act-level pass)

KOV act-level similarity (Layer 3)
----------------------------------
The provision-level keyword-Jaccard pass above stays laws-and-state only.
A separate **act-level** pass (gated on ``--no-kov``'s inverse,
``include_kov_similarity``, default on) computes bucketed TF-IDF cosine
similarity over ``estleg:MunicipalRegulation`` acts. KOV acts are grouped
into regulation-type buckets keyed off ``estleg:titleNormalized``
(:func:`bucket_key`); only buckets with >=2 members are pairwise-compared,
keeping the comparison count bounded (the real corpus's largest bucket is
~328 acts, so the naive ~61M act all-pairs collapses to ~380k intra-bucket
comparisons). It also emits cross-layer KOV->state-Act peers via each KOV
act's ``estleg:issuedUnder`` enabling-act links. Output is one consolidated
``krr_outputs/similarity/kov_similarity_index.json`` plus idempotent
``estleg:regulationTypeBucket`` + reified ``estleg:Similarity`` back-links
written into each KOV act node.

Directionality of ``estleg:similarAct`` (cross-layer)
----------------------------------------------------
Intra-bucket KOV<->KOV ``estleg:similarAct`` peers are symmetric (each act
in a qualifying pair lists the other). The **cross-layer** KOV->state edge,
however, is **directional by design**: a KOV act gets an ``estleg:similarAct``
/ reified ``estleg:Similarity`` pointer to its enabling state/law act, but
the reciprocal pointer is *not* written back into the state act. The edge is
derived from the KOV act's ``estleg:issuedUnder`` link (a KOV->state relation
that has no state->KOV inverse in the corpus), and the KOV pass deliberately
mutates only KOV (``regulations/kov/``) peep files — its idempotent
clear/regen (:func:`clear_kov_similarity_output`) likewise scopes to the KOV
subtree. Read ``estleg:similarAct`` pointing at a non-KOV (``_Map_`` enabling)
act as "this KOV regulation is similar to the state act it was issued under",
a directed KOV->enabling-act edge, not a symmetric assertion. Writing the
state-side inverse was considered and rejected as higher-risk: it would have
the KOV pass write into laws/state peeps that the clear path does not own,
risking stale ``estleg:Similarity_*`` nodes on opt-out/regen.

Per-link provenance (strip — #422)
-----------------------------------
Each ``estleg:semanticallySimilarTo`` value injected into a provision peep
file is a **bare** ``{"@id": target}`` reference. It carries NO per-pair
``estleg:similarityScore`` / ``estleg:similarityStatus`` keys.

History: an earlier version nested those two keys inside the value object
as "lowest-churn" per-link provenance. That was semantically wrong — on
JSON-LD expansion a nested key on a ``{"@id": ...}`` value object becomes a
triple ABOUT THE TARGET node, not about the source/target pair (#422). A
provision linked from several sources therefore accumulated multiple
contradictory score literals (e.g. ``estleg:RNKU_Par_1`` carried 0.3,
0.308, 0.412 and 0.438 at once) with no way to attribute each to its pair.
The empirical scan found 1,281 of 3,631 annotated targets affected.

Decision (#422 option c, Tier-0): strip the score/status keys from the
published graph and keep only the symmetric ``estleg:semanticallySimilarTo``
link as a plain IRI reference. Every per-pair Jaccard score and the
``"candidate"`` status survive losslessly in the ``similarity_index.json``
sidecar (and the ``--emit-sample`` precision-review file), keyed by the
directed (source, target) pair — so no information is lost, it is simply no
longer asserted as a (wrong) RDF triple. Proper per-pair reification
(``estleg:SimilarityLink`` nodes / RDF-star annotation) is deferred to the
overlay-architecture work (#463); see :func:`similarity_link_value`.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from estleg_common import (
    BUILD_EVALUATION_DATE,
    iter_peep_files,
    jsonld_text,
    save_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
REPORTS_DIR = KRR_DIR / "reports"
SIMILARITY_DIR = KRR_DIR / "similarity"

# Candidate-status literal for similarity links. Since #422 it is NO longer
# written into the published graph (a per-link status key mis-attached to the
# target node); it survives only in the sidecar index/report (advertised under
# link_emission.status_literal) and mirrors the index/report-level
# relation_semantics — these are heuristic keyword-overlap links pending
# reviewed precision metrics, not asserted legal semantics.
SIMILARITY_STATUS = "candidate"

# Deterministic seed for --emit-sample so the precision-review sample is
# reproducible across runs (same corpus -> same sampled tuples).
SAMPLE_SEED = 20260511

NS = "https://data.riik.ee/ontology/estleg#"

CONTEXT = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
}

# Estonian stopwords to filter out.
#
# Two layers here:
#  1. Function words / pronouns / determiners ("ja", "see", "mis", ...) —
#     pure noise, never discriminative.
#  2. Generic legal connectives that aren't *structural* tokens but are so
#     common across Estonian statutes that two provisions sharing only
#     these words tell us nothing about topical similarity ("seadus",
#     "käesolev", "kohaldama", "sätestama", "tähenduses", "kohta",
#     "alusel", "korras", "puhul", "juhul", ...). The pre-existing
#     boilerplate filter removed *structural* tokens (paragrahv / lõige /
#     jagu); this layer extends the same idea to non-structural-but-
#     non-discriminative legal vocabulary. Stem-ish variants are listed
#     explicitly because the tokenizer does no morphological analysis.
STOPWORDS = {
    "ja", "ning", "või", "on", "ei", "see", "mis", "kes", "mille", "kelle",
    "selle", "tema", "oma", "kui", "et", "ka", "nii", "aga", "kuid", "vaid",
    "siis", "juba", "veel", "väga", "kogu", "iga", "kõik", "üks", "mõni",
    "ole", "olema", "saab", "saama", "teha", "tegema", "olla", "mitte",
    "nende", "neid", "need", "seda", "selle", "sellest", "sellele",
    "tema", "teda", "talle", "temast", "ise", "enda", "endale",
    "muu", "muud", "muude", "muudest", "teise", "teiste", "teistest",
    "sama", "samu", "samade", "samadest", "käesoleva", "käesolev",
    "seaduse", "seadus", "seadusega", "seaduses", "paragrahv", "lõige",
    "punkt", "lõikes", "punktis", "alusel", "korral", "juhul",
    "kohta", "kohaldatakse", "sätestatud", "kehtestatud", "määratud",
    "vastavalt", "järgi", "kohaselt", "tähenduses",
    # Generic legal connectives / verbs (layer 2 — see note above).
    "seadusandlik", "seadusandliku", "seadusandlikku",
    "kohaldama", "kohaldamine", "kohaldamise", "kohaldamisel", "kohaldatav",
    "sätestama", "sätestab", "sätestamine", "sätestamise", "säte", "sätte",
    "sätted", "sätteid", "sätetele", "sätetes", "sätetest",
    "korras", "korda", "korrast", "korrale",
    "puhul", "puhuks",
    "tähendus", "tähendab", "tähenduse", "tähendusega",
    "kehtestama", "kehtestab", "kehtestamine", "kehtestamise", "kehtiv",
    "kohaselt", "kohane", "kohaste",
    "alus", "aluse", "alusele", "alused", "aluseks", "alustel",
    "tulenevalt", "tulenev", "tulenevad", "tuleneva",
}

# Minimum keyword length to consider
MIN_KEYWORD_LEN = 4
# Minimum shared keywords for similarity
MIN_SHARED_KEYWORDS = 3
# Minimum Jaccard similarity threshold
MIN_SIMILARITY = 0.3
# Maximum similar provisions to link per provision
MAX_SIMILAR_PER_PROVISION = 5

# Corpus document-frequency cap for "generic" keywords.
#
# A token that shows up in more than this fraction of *all* analysed
# provisions is too generic to be a useful similarity signal — it behaves
# like an undeclared stopword (think domain-wide verbs/connectives the
# static list above can't anticipate). Such tokens are dropped from every
# provision's keyword set *after* the corpus has been scanned, before any
# pair scoring. 0.45 (≈ "appears in <45% of provisions") is a deliberately
# loose cap: real boilerplate and connectives sit far above it, genuine
# topical terms far below, so the cut is unambiguous. Set to >=1.0 to
# disable. The cap is reported in similarity_report.json for transparency.
GENERIC_DOC_FREQUENCY_CAP = 0.45

# ---------------------------------------------------------------------------
# KOV act-level similarity (Layer 3) — constants
# ---------------------------------------------------------------------------
# The KOV pass is a *separate*, act-level similarity computation that runs
# only over estleg:MunicipalRegulation acts. It groups acts into
# regulation-type buckets keyed off estleg:titleNormalized and compares
# pairwise within each bucket (plus cross-layer KOV->state via
# estleg:issuedUnder). Scale: bucketing keeps the comparison count bounded —
# the real corpus's largest bucket is ~328 acts and the total intra-bucket
# comparison count is ~380k (vs the naive ~61M act all-pairs), well under a
# few million. See bucket_key() and run_kov_similarity_pass().

# Trailing "action noun" tokens stripped from a normalized title before
# deriving the bucket head. A title like "... eeskirja muutmine" describes
# an *amendment to* an eeskiri; the bucket is the eeskiri, not "muutmine".
_BUCKET_TAIL_ACTIONS = {
    "muutmine",
    "kinnitamine",
    "kehtestamine",
    "tunnistamine",
}

# Connectives / year-words that are never the discriminative head or
# qualifier of a regulation type. Stripped from the trailing edge and
# skipped when picking the qualifier token.
_BUCKET_CONNECTIVES = {
    "ja",
    "ning",
    "voi",
    "mille",
    "selle",
    "aasta",
    "aastaks",
    "aastateks",
    "kuni",
}

# Bucket label for acts whose title yields no usable content token.
UNCATEGORIZED_BUCKET = "_uncategorized"

# Stem-normalization map for regulation-type nouns that inflect in titles
# (#317). Estonian titles carry the type noun in the nominative ("kooli
# pohimaarus") OR an oblique case when an action noun follows ("kooli
# pohimaaruse muutmine"); after the trailing action noun is stripped the
# oblique form survives and would bucket separately from the nominative,
# so an amendment to a regulation type is never compared with acts that ARE
# that type — the closest possible matches. Rather than a blanket
# suffix-stripper (which over-merges unrelated types), this is a curated
# inflected->canonical lookup over the regulation-type nouns that actually
# split in the corpus (verified against kov_similarity_index.json bucket
# heads): pohimaarus / eeskiri / kord / piirmaara and their genitive,
# partitive, and plural surface forms. Keys are the already-lowercased,
# diacritic-permissive token forms produced by _BUCKET_TOKEN_RE; both the
# transliterated forms the live corpus uses (pohimaaruse) and the
# raw-diacritic forms the tokenizer still accepts (põhimääruse) map to one
# canonical stem so inflected variants always share a bucket.
_BUCKET_STEM_MAP = {
    # pohimaarus (statute/charter): genitive/partitive/plural -> nominative.
    "pohimaaruse": "pohimaarus",
    "pohimaaruste": "pohimaarus",
    "pohimaaruses": "pohimaarus",
    "pohimaarusest": "pohimaarus",
    "pohimaarused": "pohimaarus",
    "põhimääruse": "pohimaarus",
    "põhimäärus": "pohimaarus",
    "põhimääruste": "pohimaarus",
    "põhimäärused": "pohimaarus",
    "pohimaarus": "pohimaarus",
    # eeskiri (rules/by-law): i-stem genitive "eeskirja" + plurals.
    "eeskirja": "eeskiri",
    "eeskirjad": "eeskiri",
    "eeskirjade": "eeskiri",
    "eeskiri": "eeskiri",
    # kord (procedure/order): irregular d-stem genitive "korra".
    "korra": "kord",
    "korrad": "kord",
    "kordade": "kord",
    "kord": "kord",
    # piirmaara (limit/rate): a-stem; collapse plural/partitive forms.
    "piirmaarade": "piirmaara",
    "piirmaarad": "piirmaara",
    "piirmäärade": "piirmaara",
    "piirmäärad": "piirmaara",
    "piirmaara": "piirmaara",
}


def _normalize_bucket_token(token: str) -> str:
    """Map an inflected regulation-type noun to its canonical stem (#317).

    Looks ``token`` up in :data:`_BUCKET_STEM_MAP`; unknown tokens are
    returned unchanged. Applied to *every* surviving content token (not just
    the head) so a type noun that appears as the qualifier — e.g.
    ``"kasutamise eeskirja"`` vs ``"kasutamise eeskiri"`` — also merges, and
    the merge is independent of whether the noun is the head or the qualifier.
    """
    return _BUCKET_STEM_MAP.get(token, token)


# Tokens shorter than this are treated as non-content (dropped from the
# trailing edge and ignored when picking qualifier/head). Mirrors the
# "tokens <3 chars" rule in the Layer 3 spec.
_BUCKET_MIN_TOKEN_LEN = 3

# Hard cap on bucket size before pairwise comparison. A bucket larger than
# this is first sub-split by prepending the next preceding content token to
# its key; if a sub-bucket is *still* over the cap it is recorded in the
# report and skipped for pairwise comparison (its members still get the
# bucket label stamped). The real corpus's largest bucket is ~328 < 400, so
# no split fires today — this is a forward-looking guard against a future
# import producing a pathologically large single bucket.
_BUCKET_MAX_SIZE = 400

# Tokenizer for normalized titles. titleNormalized is already lowercased
# and diacritic-transliterated at Layer 1, but the source corpus also
# carries a few residual diacritics, so the class is kept permissive.
_BUCKET_TOKEN_RE = re.compile(r"[a-zõäöüšž]+")

# Per-act peer cap and similarity floor for the KOV act-level pass.
KOV_TOP_K = 20
KOV_SIMILARITY_THRESHOLD = 0.3
KOV_SCORE_MODEL = "tfidf-cosine"

# Boilerplate provision patterns to exclude (entry-into-force, repeal clauses, etc.)
#
# Each pattern flags a provision whose text is mandatory verbatim / structural
# boilerplate rather than substantive content. Two provisions sharing only
# such text are not topically similar, so flagged provisions are dropped
# before keyword extraction (see is_boilerplate / extract_provisions_from_file)
# — otherwise identical boilerplate yields Jaccard=1.0 false-similarity
# clusters and degenerate high-in-degree hubs (#367).
BOILERPLATE_PATTERNS = [
    re.compile(r'välja jäetud', re.IGNORECASE),
    re.compile(r'valja jaetud', re.IGNORECASE),
    re.compile(r'^Kehtetu', re.IGNORECASE),
    re.compile(r"jõustub.*riigi teatajas", re.IGNORECASE),
    re.compile(r"jõustub.*avaldamisele", re.IGNORECASE),
    re.compile(r"käesolev seadus jõustub", re.IGNORECASE),
    re.compile(r"seadus jõustub", re.IGNORECASE),
    # #367: the REGULATION entry-into-force form ("Määrus jõustub … aastal." /
    # "Määrus jõustub üldises korras." / "Määrus jõustub kolmandal päeval …").
    # Complements "seadus jõustub" above (which only covers laws). Anchored to
    # an entry-into-force complement — a date digit, "aasta", "päev(al)",
    # "korras", or an avalikustam/avaldam stem — so substantive court-order
    # provisions ("… määrus jõustub, kui …" / "… määrus jõustub ja kuulub
    # täitmisele …", which have no such complement before the sentence end)
    # are NOT suppressed.
    re.compile(
        r"määrus jõustub[^.]*?(\d|aasta|päev|korras|avalikustam|avaldam)",
        re.IGNORECASE,
    ),
    # #367: mandatory verbatim nature-conservation text. Every protected-area
    # regulation repeats "<protected-area-kind> valitseja on Keskkonnaamet."
    # (e.g. "Kaitseala valitseja on Keskkonnaamet.") — identical across
    # thousands of acts, it produced a degenerate Jaccard=1.0 hub (in-degree
    # 293 on a single nature-reserve §3). The administrator name is fixed by
    # statute, so the sentence carries no discriminative topical signal.
    re.compile(r"valitseja on Keskkonnaamet", re.IGNORECASE),
    re.compile(r"tunnistatakse kehtetuks", re.IGNORECASE),
    re.compile(r"rakendatakse.*jõustumisest", re.IGNORECASE),
]


def is_boilerplate(text: str) -> bool:
    """Check if text matches any boilerplate provision pattern."""
    if not text:
        return False
    for pat in BOILERPLATE_PATTERNS:
        if pat.search(text):
            return True
    return False


def extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text."""
    if not text:
        return set()
    # Tokenize: split on non-alpha characters
    tokens = re.findall(r"[a-zäöüõšž]+", text.lower())
    # Filter stopwords and short tokens
    return {
        t for t in tokens
        if t not in STOPWORDS and len(t) >= MIN_KEYWORD_LEN
    }


def compute_generic_keywords(
    provisions: list[dict], cap: float | None = None
) -> set[str]:
    """Return keywords appearing in more than ``cap`` fraction of provisions.

    These are corpus-wide generic tokens — undeclared stopwords / domain
    connectives the static :data:`STOPWORDS` list can't anticipate. Two
    provisions sharing only such tokens are not topically similar, so they
    are stripped from every keyword set before pair scoring (see
    :func:`apply_generic_keyword_filter`). With ``cap >= 1.0`` nothing is
    flagged (filter disabled). ``cap is None`` -> use the module default
    :data:`GENERIC_DOC_FREQUENCY_CAP` (looked up at call time so tests can
    monkeypatch it).
    """
    if cap is None:
        cap = GENERIC_DOC_FREQUENCY_CAP
    if cap >= 1.0 or not provisions:
        return set()
    doc_freq: dict[str, int] = defaultdict(int)
    for prov in provisions:
        for kw in prov["keywords"]:
            doc_freq[kw] += 1
    threshold = cap * len(provisions)
    return {kw for kw, count in doc_freq.items() if count > threshold}


def apply_generic_keyword_filter(
    provisions: list[dict], generic_keywords: set[str]
) -> None:
    """Strip ``generic_keywords`` from every provision's keyword set in place.

    Provisions whose keyword set drops below :data:`MIN_SHARED_KEYWORDS`
    after the strip keep their (now-shorter) set: the per-provision keyword
    *floor* is enforced at extraction time on the raw set; here we only
    want to keep generic tokens from inflating Jaccard scores, not to
    re-run eligibility. A provision left with <MIN_SHARED_KEYWORDS keywords
    simply can't reach the shared-keyword count needed to form a pair.
    """
    if not generic_keywords:
        return
    for prov in provisions:
        prov["keywords"] = prov["keywords"] - generic_keywords


def law_prefix(iri: str) -> str:
    """Extract the law prefix from a provision IRI (everything before _Par_).

    E.g. "estleg:AOS_Par_1" -> "estleg:AOS"
         "estleg:AOS_Par_92_asjaoigusseadus_osa2" -> "estleg:AOS"
    Returns the full IRI unchanged if it contains no _Par_ segment.
    """
    idx = iri.find("_Par_")
    return iri[:idx] if idx != -1 else iri


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def node_types(node: dict) -> list[str]:
    types = node.get("@type", [])
    if isinstance(types, str):
        return [types]
    if isinstance(types, list):
        return [t for t in types if isinstance(t, str)]
    return []


def find_act_node(doc: dict) -> dict | None:
    for node in doc.get("@graph", []):
        types = set(node_types(node))
        if "estleg:Act" in types or (
            "owl:Ontology" in types
            and types.intersection(
                {
                    "estleg:Law",
                    "estleg:NationalRegulation",
                    "estleg:GovernmentRegulation",
                    "estleg:MinisterialRegulation",
                    "estleg:MunicipalRegulation",
                }
            )
        ):
            return node
    return None


def classify_act_type(fpath: Path, act_node: dict) -> str:
    types = set(node_types(act_node))
    path = fpath.as_posix()
    if "estleg:MunicipalRegulation" in types or "/regulations/kov/" in path:
        return "kov"
    if (
        types.intersection(
            {
                "estleg:NationalRegulation",
                "estleg:GovernmentRegulation",
                "estleg:MinisterialRegulation",
            }
        )
        or "/regulations/riik/" in path
    ):
        return "state_regulation"
    if "estleg:Law" in types:
        return "law"
    return "other"


def relative_output_path(fpath: Path) -> str:
    try:
        return str(fpath.relative_to(KRR_DIR))
    except ValueError:
        return fpath.name


def extract_provisions_from_file(fpath: Path) -> tuple[list[dict], str | None, int]:
    """Extract eligible provisions plus the count excluded by the keyword floor.

    Returns a tuple of (provisions, act_type, excluded_for_keyword_floor) where
    excluded_for_keyword_floor counts provisions that pass boilerplate/type
    filters but yield fewer than ``MIN_SHARED_KEYWORDS`` keywords.
    """
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return [], None, 0

    act_node = find_act_node(doc)
    if act_node is None:
        return [], None, 0
    act_type = classify_act_type(fpath, act_node)
    file_name = relative_output_path(fpath)

    provisions: list[dict] = []
    excluded_for_keyword_floor = 0
    for node in doc.get("@graph", []):
        node_id = node.get("@id", "")
        if not node_id or not node_id.startswith("estleg:"):
            continue

        types = node_types(node)
        if "owl:Ontology" in types or "owl:Class" in types:
            continue
        if "estleg:TopicCluster" in str(types):
            continue

        summary = jsonld_text(node.get("estleg:summary", ""))
        label = jsonld_text(node.get("rdfs:label", ""))
        source_act = jsonld_text(node.get("estleg:sourceAct", ""))

        if is_boilerplate(summary):
            continue

        keywords = extract_keywords(summary)

        if len(keywords) >= MIN_SHARED_KEYWORDS:
            provisions.append({
                "id": node_id,
                "label": label,
                "source_act": source_act,
                "keywords": keywords,
                "file": file_name,
                "act_type": act_type,
            })
        else:
            excluded_for_keyword_floor += 1
    return provisions, act_type, excluded_for_keyword_floor


def similarity_link_value(target: str) -> dict:
    """Build one ``estleg:semanticallySimilarTo`` list element (plain IRI ref).

    The element is a bare ``{"@id": target}`` reference. Per-pair confidence
    is **not** emitted into the published graph: a nested
    ``estleg:similarityScore`` / ``estleg:similarityStatus`` value object
    expands, on JSON-LD round-trip, into triples ABOUT THE TARGET node rather
    than about the source/target pair, so a target linked from several sources
    accumulates multiple contradictory score literals with no way to tell
    which pair each belongs to (#422). The scores survive losslessly in the
    ``similarity_index.json`` sidecar (keyed by the directed (source, target)
    pair) and in the ``--emit-sample`` precision-review file.

    Tier-0 goal here is only to stop publishing the semantically wrong
    triples now; proper per-pair reification (``estleg:SimilarityLink`` nodes
    / RDF-star) is deferred to the overlay-architecture work (#463).
    """
    return {"@id": target}


# ===========================================================================
# KOV act-level similarity (Layer 3)
# ===========================================================================


def bucket_key(title_normalized: str) -> str:
    """Derive a deterministic regulation-type bucket from a normalized title.

    Self-contained and dependency-free so it can be reasoned about (and
    unit-tested) in isolation. The algorithm, applied to an already
    lowercased/diacritic-transliterated ``estleg:titleNormalized``:

    1. Tokenize on ``[a-zõäöüšž]+``.
    2. Strip trailing **action nouns** (``muutmine`` / ``kinnitamine`` /
       ``kehtestamine`` / ``tunnistamine``) — a title describing an
       amendment to an X buckets as X, not as the amendment verb.
    3. Strip trailing **connectives / year-words** (``ja``, ``ning``,
       ``voi``, ``mille``, ``selle``, ``aasta``, ``aastaks``,
       ``aastateks``, ``kuni``) and any trailing token shorter than 3
       chars.
    4. From the surviving **content tokens** (>=3 chars, neither a
       connective nor an action noun — action nouns are filtered out of the
       whole content list, not just the trailing edge, so a mid-title
       amendment verb followed by another word never becomes the qualifier),
       **stem-normalize** each via :func:`_normalize_bucket_token` so an
       inflected regulation-type noun (genitive ``pohimaaruse`` /
       ``eeskirja`` / ``korra``) collapses onto its nominative
       (``pohimaarus`` / ``eeskiri`` / ``kord``) — applied per token so the
       merge holds whether the type noun is the head or the qualifier
       (#317). Then take the two trailing tokens as ``"{qualifier} {head}"``
       (the qualifier is the nearest preceding content token), or just
       ``head`` when only one content token remains.

    Returns :data:`UNCATEGORIZED_BUCKET` (``"_uncategorized"``) when the
    title is empty or yields no content token.

    Examples (real corpus titles)::

        "... jaatmehoolduseeskiri"                -> "jaatmehoolduseeskiri"
        "opetajate tunnustamise kord"             -> "tunnustamise kord"
        "roosi kooli arengukava aastateks 2024-2028" -> "kooli arengukava"
        "kooli pohimaaruse muutmine"              -> "kooli pohimaarus"
        "kooli pohimaarus"                        -> "kooli pohimaarus"  (#317: same bucket)
    """
    if not title_normalized:
        return UNCATEGORIZED_BUCKET
    tokens = _BUCKET_TOKEN_RE.findall(title_normalized.lower())
    # Strip trailing action nouns first (they may sit at the very end).
    while tokens and tokens[-1] in _BUCKET_TAIL_ACTIONS:
        tokens.pop()
    # Strip trailing connectives / year-words / short tokens.
    while tokens and (
        tokens[-1] in _BUCKET_CONNECTIVES or len(tokens[-1]) < _BUCKET_MIN_TOKEN_LEN
    ):
        tokens.pop()
    # Action nouns are never the discriminative head/qualifier of a
    # regulation type, so drop them from *anywhere* in the content list (not
    # just the trailing edge) — exactly as connectives are filtered. This
    # keeps a mid-title action verb like "... eeskirja muutmine ja kehtetuks"
    # from surviving as the bucket qualifier when a non-action word follows
    # it; the title buckets on "eeskiri", not "muutmine".
    content = [
        _normalize_bucket_token(t)
        for t in tokens
        if len(t) >= _BUCKET_MIN_TOKEN_LEN
        and t not in _BUCKET_CONNECTIVES
        and t not in _BUCKET_TAIL_ACTIONS
    ]
    if not content:
        return UNCATEGORIZED_BUCKET
    head = content[-1]
    if len(content) >= 2:
        return f"{content[-2]} {head}"
    return head


def _bucket_split_key(title_normalized: str, depth: int) -> str:
    """Return a sub-bucket key by prepending ``depth`` extra preceding tokens.

    ``depth == 0`` is the base :func:`bucket_key`. ``depth == 1`` prepends
    the third-from-last content token, ``depth == 2`` the fourth, etc. Used
    only when a base bucket exceeds :data:`_BUCKET_MAX_SIZE` and must be
    sub-split to stay tractable. Falls back to the base key when there are
    not enough content tokens to extend.
    """
    base = bucket_key(title_normalized)
    if depth <= 0 or base == UNCATEGORIZED_BUCKET:
        return base
    tokens = _BUCKET_TOKEN_RE.findall(title_normalized.lower())
    while tokens and tokens[-1] in _BUCKET_TAIL_ACTIONS:
        tokens.pop()
    while tokens and (
        tokens[-1] in _BUCKET_CONNECTIVES or len(tokens[-1]) < _BUCKET_MIN_TOKEN_LEN
    ):
        tokens.pop()
    # Mirror bucket_key: filter action nouns out of the content list too, and
    # stem-normalize each token (#317), so a sub-split key stays aligned with
    # the base bucket label (inflected type nouns collapse identically).
    content = [
        _normalize_bucket_token(t)
        for t in tokens
        if len(t) >= _BUCKET_MIN_TOKEN_LEN
        and t not in _BUCKET_CONNECTIVES
        and t not in _BUCKET_TAIL_ACTIONS
    ]
    # Base uses the last two content tokens; each extra depth pulls in one
    # more preceding token as a prefix qualifier.
    take = 2 + depth
    if len(content) < take:
        return base
    return " ".join(content[-take:])


def kov_similarity_link_value(source_id: str, target_id: str, score: float) -> dict:
    """Build one reified ``estleg:Similarity`` node for a KOV similar-act link.

    The KOV act-level back-link is a reified node (not a value object) so
    the peer IRI, score, and model stay attached to the right peer through
    any JSON-LD round-trip — the same ordering concern that motivated the
    reified ``estleg:Citation`` nodes. The node lives in the same ``@graph``
    as the source act and is pointed at by an ``estleg:similarAct``
    ``{"@id": ...}`` reference.

    ``source_id`` / ``target_id`` are the bare id components of the two act
    IRIs (the part after ``estleg:``), e.g. ``"Reg_1014955_Map_2026"``. The
    score is serialized as an ``xsd:decimal`` typed literal rounded to 3 dp.
    """
    return {
        "@id": f"estleg:Similarity_{source_id}_{target_id}",
        "@type": ["owl:NamedIndividual", "estleg:Similarity"],
        "estleg:similarTarget": {"@id": f"estleg:{target_id}"},
        "estleg:similarityScore": {
            "@value": f"{round(score, 3)}",
            "@type": "xsd:decimal",
        },
        "estleg:similarityModel": KOV_SCORE_MODEL,
    }


def _bare_id(iri: str) -> str:
    """Strip the ``estleg:`` compact-prefix from an IRI (``estleg:X`` -> ``X``)."""
    return iri[len("estleg:"):] if iri.startswith("estleg:") else iri


def find_kov_act_node(doc: dict) -> dict | None:
    """Return the ``estleg:MunicipalRegulation`` act node of a KOV peep doc."""
    for node in doc.get("@graph", []):
        if "estleg:MunicipalRegulation" in node_types(node):
            return node
    return None


def act_document_text(act_node: dict, provision_nodes: list[dict]) -> str:
    """Concatenate the text fields that describe one act for TF-IDF.

    Per the Layer 3 spec the per-act document is the act
    title (``rdfs:label`` / ``estleg:titleNormalized``) + the act
    ``estleg:preambleText`` + each child provision's ``estleg:summary``
    (and ``estleg:legalText`` when present). All fields are passed through
    :func:`jsonld_text` so language-tagged value objects and plain strings
    are handled uniformly.
    """
    parts: list[str] = []
    parts.append(jsonld_text(act_node.get("rdfs:label", "")))
    parts.append(jsonld_text(act_node.get("estleg:titleNormalized", "")))
    parts.append(jsonld_text(act_node.get("estleg:preambleText", "")))
    for prov in provision_nodes:
        parts.append(jsonld_text(prov.get("estleg:summary", "")))
        parts.append(jsonld_text(prov.get("estleg:legalText", "")))
    return " ".join(p for p in parts if p)


def _provision_nodes_for_act(doc: dict, act_id: str) -> list[dict]:
    """Return the child provision nodes (``estleg:..._Par_*``) of an act doc."""
    provisions: list[dict] = []
    for node in doc.get("@graph", []):
        node_id = node.get("@id", "")
        if not isinstance(node_id, str) or "_Par_" not in node_id:
            continue
        provisions.append(node)
    return provisions


def build_term_frequencies(text: str) -> Counter:
    """Tokenize ``text`` into a term-frequency Counter (reusing extract_keywords).

    :func:`extract_keywords` returns the *set* of in-vocabulary tokens (it
    drops stopwords and sub-:data:`MIN_KEYWORD_LEN` tokens); for TF-IDF we
    want raw counts of those same in-vocabulary tokens, so we re-tokenize
    with the identical rule and count, intersected with the keyword set.
    """
    if not text:
        return Counter()
    vocab = extract_keywords(text)
    if not vocab:
        return Counter()
    tokens = re.findall(r"[a-zäöüõšž]+", text.lower())
    return Counter(t for t in tokens if t in vocab)


def compute_idf(term_frequencies: list[Counter]) -> dict[str, float]:
    """Smoothed IDF over a corpus of per-document term-frequency Counters.

    ``idf[t] = log((1 + N) / (1 + df[t])) + 1`` (the scikit-learn smoothed
    form), where ``N`` is the document count and ``df[t]`` the number of
    documents containing term ``t``. The ``+1`` keeps every observed term's
    weight strictly positive.
    """
    n_docs = len(term_frequencies)
    doc_freq: dict[str, int] = defaultdict(int)
    for tf in term_frequencies:
        for term in tf:
            doc_freq[term] += 1
    return {
        term: math.log((1 + n_docs) / (1 + df)) + 1
        for term, df in doc_freq.items()
    }


def tfidf_vector(tf: Counter, idf: dict[str, float]) -> dict[str, float]:
    """Build an L2-normalized sparse tf*idf vector from a TF Counter + IDF map.

    Terms absent from ``idf`` (e.g. a cross-layer state-act term not seen in
    the KOV corpus) contribute nothing — they have no weight in the shared
    space and so cannot affect cosine. The returned dict maps term -> weight
    and is L2-normalized; an empty/zero vector returns ``{}``.
    """
    weights: dict[str, float] = {}
    for term, count in tf.items():
        weight = idf.get(term)
        if weight:
            weights[term] = count * weight
    norm = math.sqrt(sum(w * w for w in weights.values()))
    if norm == 0.0:
        return {}
    return {term: w / norm for term, w in weights.items()}


def cosine_sparse(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Cosine similarity of two L2-normalized sparse vectors (a dot product).

    Both inputs are already unit-normalized by :func:`tfidf_vector`, so the
    cosine is simply the dot product over their shared terms. Iterates the
    smaller vector for efficiency.
    """
    if not vec_a or not vec_b:
        return 0.0
    if len(vec_b) < len(vec_a):
        vec_a, vec_b = vec_b, vec_a
    return sum(weight * vec_b.get(term, 0.0) for term, weight in vec_a.items())


def emit_precision_sample(pairs: list[dict], sample_size: int, out_path: Path) -> int:
    """Write a deterministic random sample of pairs for human precision review.

    Samples ``min(sample_size, len(pairs))`` tuples of
    ``(source_provision, target_provision, score, shared_keywords)`` so a
    reviewer can label them and turn the index's permanent
    ``quality_evaluation.status == "not_evaluated"`` into a real precision-
    by-score-band number. Uses a fixed seed (:data:`SAMPLE_SEED`) so the
    same corpus yields the same sample. Returns the number of pairs written.
    """
    n = max(0, min(sample_size, len(pairs)))
    rng = random.Random(SAMPLE_SEED)
    chosen = rng.sample(pairs, n) if n else []
    chosen.sort(key=lambda p: (p["source"], p["target"]))
    payload = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "relation": "estleg:semanticallySimilarTo",
        "relation_semantics": "candidate",
        "purpose": (
            "Human precision-review sample. Label each row "
            "(relevant / not relevant) to compute reviewed precision by "
            "score band for the keyword_jaccard similarity heuristic."
        ),
        "sample_seed": SAMPLE_SEED,
        "requested_sample_size": sample_size,
        "available_pairs": len(pairs),
        "sample_size": len(chosen),
        "samples": [
            {
                "source_provision": p["source"],
                "source_label": p.get("source_label", ""),
                "target_provision": p["target"],
                "target_label": p.get("target_label", ""),
                "score": p["similarity"],
                "shared_keywords": p["shared_keywords"],
                # Reviewer fills these in:
                "reviewed_relevant": None,
                "reviewer_note": "",
            }
            for p in chosen
        ],
    }
    save_json(out_path, payload)
    return len(chosen)


def _iter_kov_peep_files() -> list[Path]:
    """Return only the KOV (``estleg:MunicipalRegulation``) peep files.

    ``iter_peep_files`` includes laws + state regs + KOV; this narrows to
    the ``regulations/kov/`` subtree, which is exactly the act set the
    KOV-aware pass operates over. State / law peeps are still loaded
    on-demand for the cross-layer ``issuedUnder`` lookup.
    """
    return [
        p
        for p in iter_peep_files(include_kov=True)
        if "/regulations/kov/" in p.as_posix()
    ]


def _load_kov_acts(kov_files: list[Path]) -> list[dict]:
    """Load each KOV peep file into an act record for the similarity pass.

    Each record carries the parsed document, the act IRI + bare id, the
    normalized title, the per-act term-frequency Counter (built from the
    act document text), the resolved bucket key, and the act's
    ``estleg:issuedUnder`` enabling-act IRIs (for the cross-layer pass).
    Files with no MunicipalRegulation act node are skipped.
    """
    acts: list[dict] = []
    for fpath in kov_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            continue
        act_node = find_kov_act_node(doc)
        if act_node is None:
            continue
        # Issue #374: a repealed act (tombstoned by generate_regulations, or
        # marked by extract_temporal_data) is legally void — keep it out of
        # the TF-IDF similarity set so its terms neither form similarity edges
        # nor contaminate active acts' nearest-neighbour vectors.
        if act_node.get("estleg:temporalStatus") == "repealed":
            continue
        act_iri = act_node.get("@id", "")
        if not isinstance(act_iri, str) or not act_iri.startswith("estleg:"):
            continue
        title_normalized = jsonld_text(act_node.get("estleg:titleNormalized", ""))
        provisions = _provision_nodes_for_act(doc, act_iri)
        tf = build_term_frequencies(act_document_text(act_node, provisions))
        issued_under = [
            ref["@id"]
            for ref in act_node.get("estleg:issuedUnder", [])
            if isinstance(ref, dict)
            and isinstance(ref.get("@id"), str)
            and ref["@id"].startswith("estleg:")
        ]
        acts.append({
            "file": fpath,
            "doc": doc,
            "act_node": act_node,
            "iri": act_iri,
            "bare_id": _bare_id(act_iri),
            "title_normalized": title_normalized,
            "tf": tf,
            "bucket": bucket_key(title_normalized),
            "issued_under": issued_under,
        })
    return acts


def _assign_buckets(acts: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    """Group acts by bucket key, sub-splitting buckets over the size cap.

    Returns ``(buckets, oversize)`` where ``buckets`` maps each (possibly
    sub-split) bucket key to its member act records, and ``oversize`` lists
    ``{"key", "size"}`` for any sub-bucket that is still over
    :data:`_BUCKET_MAX_SIZE` after the deepest useful split — those are
    stamped on their acts but skipped for pairwise comparison. Each act
    record's ``bucket`` field is updated in place to the final (sub-split)
    key so the back-link write uses the same label as the index.

    Termination: a bucket is only re-split at the next depth when that split
    actually partitions it into >1 sub-bucket; depth is bounded by the
    longest title's content-token count, so the loop always terminates. On
    the real corpus the largest bucket is ~328 < 400, so no split fires.
    """
    # Group at the base depth first.
    base: dict[str, list[dict]] = defaultdict(list)
    for act in acts:
        base[bucket_key(act["title_normalized"])].append(act)

    final: dict[str, list[dict]] = {}
    # Work queue of (key, members, depth) buckets still needing a size check.
    queue: list[tuple[str, list[dict], int]] = [
        (key, members, 0) for key, members in base.items()
    ]
    while queue:
        key, members, depth = queue.pop()
        if (
            key == UNCATEGORIZED_BUCKET
            or len(members) <= _BUCKET_MAX_SIZE
        ):
            final[key] = members
            continue
        # Oversized + categorized: try one deeper split.
        regrouped: dict[str, list[dict]] = defaultdict(list)
        for act in members:
            regrouped[_bucket_split_key(act["title_normalized"], depth + 1)].append(act)
        if len(regrouped) > 1:
            for sub_key, sub_members in regrouped.items():
                queue.append((sub_key, sub_members, depth + 1))
        else:
            # No further partition possible — accept as an oversize bucket.
            final[key] = members

    # Re-stamp each act with its final bucket label.
    for key, members in final.items():
        for act in members:
            act["bucket"] = key

    oversize = [
        {"key": key, "size": len(members)}
        for key, members in final.items()
        if key != UNCATEGORIZED_BUCKET and len(members) > _BUCKET_MAX_SIZE
    ]
    return final, oversize


def _intra_bucket_pairs(
    members: list[dict], vectors: dict[str, dict[str, float]]
) -> dict[str, list[dict]]:
    """Pairwise cosine within one bucket; return source-id -> kept peer list.

    Intra-bucket KOV<->KOV similarity is **symmetric** (``estleg:similarAct``
    asserts mutual same-type similarity), so the per-source top-K cap is
    applied with a **greedy symmetric** selection rather than independently
    per source: a candidate pair contributes its edge to *both* sources or to
    neither, and is kept only while **both** endpoints are still under
    :data:`KOV_TOP_K`. This both (a) keeps every emitted intra-bucket pair
    symmetric — the old per-source ``del peers[src][KOV_TOP_K:]`` let a
    high-degree source A evict B while B still listed A, leaving ~49.5% of
    intra-bucket pairs one-directional (#367) — and (b) preserves the
    ``<=KOV_TOP_K`` per-source bound (a node at the cap takes no further
    edges). Pairs are considered in descending ``(score, …)`` priority so the
    strongest mutual matches claim the limited slots first; ties break
    deterministically on the IRIs.
    """
    # Collect every qualifying undirected pair once, with its score.
    candidates: list[tuple[float, str, str]] = []
    n = len(members)
    for i in range(n):
        act_a = members[i]
        vec_a = vectors[act_a["iri"]]
        for j in range(i + 1, n):
            act_b = members[j]
            score = cosine_sparse(vec_a, vectors[act_b["iri"]])
            if score < KOV_SIMILARITY_THRESHOLD:
                continue
            iri_a, iri_b = act_a["iri"], act_b["iri"]
            # Order endpoints by IRI for a deterministic tie-break key.
            lo, hi = (iri_a, iri_b) if iri_a <= iri_b else (iri_b, iri_a)
            candidates.append((score, lo, hi))

    # Greedy symmetric cap: highest-scoring pairs first (deterministic
    # tie-break on the ordered IRIs), keeping a pair only while neither
    # endpoint has reached KOV_TOP_K.
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    peers: dict[str, list[dict]] = defaultdict(list)
    degree: dict[str, int] = defaultdict(int)
    for score, lo, hi in candidates:
        if degree[lo] >= KOV_TOP_K or degree[hi] >= KOV_TOP_K:
            continue
        peers[lo].append({"target": hi, "score": score})
        peers[hi].append({"target": lo, "score": score})
        degree[lo] += 1
        degree[hi] += 1

    # Per-source ordering (score desc, then target IRI) for stable output.
    for src in peers:
        peers[src].sort(key=lambda p: (-p["score"], p["target"]))
    return peers


def _cross_layer_peers(
    act: dict,
    idf: dict[str, float],
    state_vector_cache: dict[str, dict[str, float] | None],
    act_iri_to_file: dict[str, Path],
) -> list[dict]:
    """KOV->state cross-layer peers for one act via its issuedUnder links.

    For each ``estleg:issuedUnder`` enabling-act IRI that resolves to a peep
    file, build (and cache) a TF-IDF vector for that act in the KOV-corpus
    IDF space, then emit a peer when cosine >= threshold. KOV<->KOV
    cross-bucket pairs are explicitly skipped — only state/law enabling acts
    (a ``_Map_`` id that is not itself a KOV ``Reg_`` act) are considered.

    The returned peers are written only into the *KOV* act's node (see
    :func:`_write_kov_backlinks`): the cross-layer ``estleg:similarAct`` edge
    is a **directional KOV->enabling-act** relation, not a symmetric one. See
    the module docstring's "Directionality of estleg:similarAct" note.
    """
    peers: list[dict] = []
    src_vec = _CROSS_LAYER_SRC_VECTORS.get(act["iri"])
    if not src_vec:
        return peers
    for target_iri in act["issued_under"]:
        # Skip KOV->KOV: enabling links into another municipal act are not
        # cross-layer (and KOV cross-bucket pairs are out of scope).
        if target_iri in _KOV_ACT_IRIS:
            continue
        if target_iri not in state_vector_cache:
            state_vector_cache[target_iri] = _build_state_vector(
                target_iri, idf, act_iri_to_file
            )
        tgt_vec = state_vector_cache[target_iri]
        if not tgt_vec:
            continue
        score = cosine_sparse(src_vec, tgt_vec)
        if score >= KOV_SIMILARITY_THRESHOLD:
            peers.append({"target": target_iri, "score": score})
    peers.sort(key=lambda p: (-p["score"], p["target"]))
    return peers[:KOV_TOP_K]


# Module-level scratch state populated by run_kov_similarity_pass before the
# cross-layer helper runs. Kept module-level (rather than threaded through
# every call) so the per-act/state vector builders stay simple; always reset
# at the top of run_kov_similarity_pass so repeated in-process runs (tests)
# never see stale state.
_CROSS_LAYER_SRC_VECTORS: dict[str, dict[str, float]] = {}
_KOV_ACT_IRIS: set[str] = set()


def _build_state_vector(
    act_iri: str, idf: dict[str, float], act_iri_to_file: dict[str, Path]
) -> dict[str, float] | None:
    """Build a KOV-IDF-space TF-IDF vector for a state/law enabling act.

    Concatenates the enabling act's child provision summaries (the spec's
    cross-layer document) and projects them into the KOV-corpus IDF space so
    the cosine is comparable with the KOV source vectors. Returns ``None``
    when the act IRI does not resolve to a readable peep with usable text.
    """
    fpath = act_iri_to_file.get(act_iri)
    if fpath is None:
        return None
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return None
    parts: list[str] = []
    for node in doc.get("@graph", []):
        node_id = node.get("@id", "")
        if isinstance(node_id, str) and "_Par_" in node_id:
            parts.append(jsonld_text(node.get("estleg:summary", "")))
            parts.append(jsonld_text(node.get("estleg:legalText", "")))
    tf = build_term_frequencies(" ".join(p for p in parts if p))
    vec = tfidf_vector(tf, idf)
    return vec or None


def _build_corpus_act_index() -> dict[str, Path]:
    """Map every corpus act IRI -> its peep file (for cross-layer lookup)."""
    act_iri_to_file: dict[str, Path] = {}
    for fpath in iter_peep_files(include_kov=True):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            continue
        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if not isinstance(node_id, str) or not node_id.startswith("estleg:"):
                continue
            types = set(node_types(node))
            if "owl:Ontology" in types and types & {
                "estleg:Act",
                "estleg:Law",
                "estleg:MunicipalRegulation",
                "estleg:NationalRegulation",
            }:
                act_iri_to_file.setdefault(node_id, fpath)
                break
    return act_iri_to_file


def _write_kov_backlinks(act: dict, peer_iris_scores: list[dict]) -> None:
    """Idempotently write bucket + reified Similarity back-links into an act node.

    Clears any prior ``estleg:similarAct`` and prior top-level
    ``estleg:Similarity_*`` nodes from the doc, then stamps
    ``estleg:regulationTypeBucket`` and (when there are peers) a fresh
    ``estleg:similarAct`` list plus the reified ``estleg:Similarity`` nodes
    in the same ``@graph``. Source/target are act IRIs (``Reg_<id>_Map_…``),
    never ``_Par_`` provisions.
    """
    doc = act["doc"]
    act_node = act["act_node"]
    graph = doc.get("@graph", [])

    # Idempotent clear: drop prior similarAct + prior Similarity_* nodes.
    act_node.pop("estleg:similarAct", None)
    doc["@graph"] = [
        node
        for node in graph
        if not (
            isinstance(node.get("@id"), str)
            and node["@id"].startswith("estleg:Similarity_")
        )
    ]

    act_node["estleg:regulationTypeBucket"] = act["bucket"]

    if peer_iris_scores:
        src_id = act["bare_id"]
        sim_links = []
        sim_refs = []
        for peer in peer_iris_scores:
            tgt_id = _bare_id(peer["target"])
            sim_links.append(
                kov_similarity_link_value(src_id, tgt_id, peer["score"])
            )
            sim_refs.append({"@id": f"estleg:Similarity_{src_id}_{tgt_id}"})
        act_node["estleg:similarAct"] = sim_refs
        doc["@graph"].extend(sim_links)

    save_json(act["file"], doc)


def clear_kov_similarity_output() -> int:
    """Strip all KOV Layer-3 similarity output so ``--no-kov`` is effective.

    Removes ``estleg:similarAct`` + ``estleg:regulationTypeBucket`` and the
    reified ``estleg:Similarity_*`` nodes from every KOV act peep, and deletes
    the consolidated ``krr_outputs/similarity/kov_similarity_index.json``.
    Mirrors the always-clear idempotency of the court-links pipeline so an
    opt-out run does not leave stale Layer-3 artifacts on an already-generated
    tree (#250 review). Returns the number of peep files changed.
    """
    cleaned = 0
    for path in _iter_kov_peep_files():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        graph = doc.get("@graph")
        if not isinstance(graph, list):
            continue
        new_graph = [
            node
            for node in graph
            if not (
                isinstance(node, dict)
                and isinstance(node.get("@id"), str)
                and node["@id"].startswith("estleg:Similarity_")
            )
        ]
        changed = len(new_graph) != len(graph)
        doc["@graph"] = new_graph
        act_node = find_kov_act_node(doc)
        if act_node is not None:
            for prop in ("estleg:similarAct", "estleg:regulationTypeBucket"):
                if prop in act_node:
                    act_node.pop(prop, None)
                    changed = True
        if changed:
            save_json(path, doc)
            cleaned += 1
    index_path = KRR_DIR / "similarity" / "kov_similarity_index.json"
    if index_path.exists():
        index_path.unlink()
    return cleaned


def run_kov_similarity_pass() -> dict:
    """Run the KOV act-level bucketed TF-IDF similarity pass.

    Loads every KOV act, builds per-act TF-IDF vectors over the KOV act
    corpus, buckets by :func:`bucket_key`, computes intra-bucket pairwise
    cosine for buckets with >=2 members, adds cross-layer KOV->state peers
    via ``estleg:issuedUnder``, writes idempotent back-links into each KOV
    act node, and saves one consolidated
    ``krr_outputs/similarity/kov_similarity_index.json``.

    Returns a summary dict (also the headline of the consolidated index)
    describing totals, bucket stats, and the largest bucket.
    """
    global _CROSS_LAYER_SRC_VECTORS, _KOV_ACT_IRIS

    print("\n[KOV] Loading municipal regulation acts...")
    kov_files = _iter_kov_peep_files()
    acts = _load_kov_acts(kov_files)
    print(f"  Loaded {len(acts)} KOV acts")

    # TF-IDF over the KOV act corpus.
    idf = compute_idf([act["tf"] for act in acts])
    vectors: dict[str, dict[str, float]] = {
        act["iri"]: tfidf_vector(act["tf"], idf) for act in acts
    }
    _CROSS_LAYER_SRC_VECTORS = vectors
    _KOV_ACT_IRIS = {act["iri"] for act in acts}

    # Bucketing (with oversize sub-split / skip bookkeeping).
    buckets, oversize = _assign_buckets(acts)
    oversize_keys = {entry["key"] for entry in oversize}
    print(f"  {len(buckets)} buckets; {len(oversize)} over the size cap")

    # Cross-layer state-act vector cache (shared across all acts).
    act_iri_to_file = _build_corpus_act_index()
    state_vector_cache: dict[str, dict[str, float] | None] = {}

    # Intra-bucket peers are kept SEPARATE from cross-layer peers through the
    # cap so the two cap regimes don't interfere. Intra-bucket KOV<->KOV edges
    # are symmetric and already capped symmetrically (greedy, <=KOV_TOP_K per
    # source) by _intra_bucket_pairs; cross-layer KOV->state edges are
    # directional. Merging them and re-capping per source by raw score (the old
    # behaviour) could split a symmetric intra pair — evict A->B while B->A
    # stayed — reintroducing exactly the asymmetry _intra_bucket_pairs fixed
    # (#367). So intra peers are protected and cross-layer peers only fill the
    # *remaining* per-source capacity (KOV_TOP_K - intra_count).
    intra_by_source: dict[str, list[dict]] = defaultdict(list)
    cross_by_source: dict[str, list[dict]] = defaultdict(list)

    for key, members in buckets.items():
        comparable = (
            key != UNCATEGORIZED_BUCKET
            and len(members) >= 2
            and key not in oversize_keys
        )
        if comparable:
            for src_iri, peer_list in _intra_bucket_pairs(members, vectors).items():
                intra_by_source[src_iri].extend(peer_list)

    # Cross-layer pass (runs for every act regardless of bucket).
    for act in acts:
        cl_peers = _cross_layer_peers(
            act, idf, state_vector_cache, act_iri_to_file
        )
        if cl_peers:
            cross_by_source[act["iri"]].extend(cl_peers)

    # Combine per source: intra peers (symmetric, already <=KOV_TOP_K) first,
    # then the best cross-layer peers filling any remaining slots up to
    # KOV_TOP_K. Intra peers are never evicted, so intra-bucket symmetry is
    # preserved; the kept set is then sorted by (score desc, target) for a
    # stable, score-ordered output list.
    peers_by_source: dict[str, list[dict]] = defaultdict(list)
    for src_iri in set(intra_by_source) | set(cross_by_source):
        intra = intra_by_source.get(src_iri, [])
        remaining = KOV_TOP_K - len(intra)
        cross = cross_by_source.get(src_iri, [])
        if remaining > 0 and cross:
            cross = sorted(cross, key=lambda p: (-p["score"], p["target"]))[:remaining]
        else:
            cross = []
        combined = intra + cross
        combined.sort(key=lambda p: (-p["score"], p["target"]))
        peers_by_source[src_iri] = combined

    # Count cross-layer (KOV->state) pairs that SURVIVE the top-K cap. The
    # earlier pre-cap tally overreported emitted links (#250 review): an act's
    # intra-bucket peers can evict cross-layer ones during the cap. Cross-layer
    # peers are exactly those whose target is not itself a KOV act.
    cross_layer_pairs = sum(
        1
        for peers in peers_by_source.values()
        for peer in peers
        if peer["target"] not in _KOV_ACT_IRIS
    )

    # Write back-links into every KOV act node (idempotent), then build the
    # consolidated index.
    total_pairs = 0
    backlinked_acts = 0
    bucket_index: dict[str, dict] = {}
    for key in sorted(buckets):
        members = buckets[key]
        bucket_pairs = []
        for act in sorted(members, key=lambda a: a["iri"]):
            peer_list = peers_by_source.get(act["iri"], [])
            if peer_list:
                bucket_pairs.append({
                    "source": act["iri"],
                    "peers": [
                        {"target": p["target"], "score": round(p["score"], 3)}
                        for p in peer_list
                    ],
                })
                total_pairs += len(peer_list)
                backlinked_acts += 1
        bucket_index[key] = {
            "regulationCount": len(members),
            "pairs": bucket_pairs,
        }

    # Write back-links for *every* act (bucket label always; peers when any).
    for act in acts:
        _write_kov_backlinks(act, peers_by_source.get(act["iri"], []))

    uncategorized_count = len(buckets.get(UNCATEGORIZED_BUCKET, []))
    compared_buckets = sum(
        1
        for key, members in buckets.items()
        if key != UNCATEGORIZED_BUCKET
        and len(members) >= 2
        and key not in oversize_keys
    )
    largest_key = max(buckets, key=lambda k: len(buckets[k])) if buckets else None
    largest_bucket = (
        {"key": largest_key, "size": len(buckets[largest_key])}
        if largest_key is not None
        else {"key": None, "size": 0}
    )

    summary = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "scoreModel": KOV_SCORE_MODEL,
        "topK": KOV_TOP_K,
        "threshold": KOV_SIMILARITY_THRESHOLD,
        "totalActs": len(acts),
        "totalBuckets": len(buckets),
        "comparedBuckets": compared_buckets,
        "uncategorizedCount": uncategorized_count,
        "totalPairs": total_pairs,
        "largestBucket": largest_bucket,
        "crossLayerPairs": cross_layer_pairs,
        "oversizeBucketsSkipped": sorted(oversize, key=lambda e: e["key"]),
        "buckets": {key: bucket_index[key] for key in sorted(bucket_index)},
    }

    # Resolve the output dir from KRR_DIR at call time so tests that
    # monkeypatch similarity.KRR_DIR redirect this write (the module-level
    # SIMILARITY_DIR constant is the production default). Create the
    # directory explicitly so the pass is self-contained regardless of
    # whether the active save_json implementation mkdirs.
    index_dir = KRR_DIR / "similarity"
    index_dir.mkdir(parents=True, exist_ok=True)
    index_path = index_dir / "kov_similarity_index.json"
    save_json(index_path, summary)
    print(
        f"  Wrote {relative_output_path(index_path)} "
        f"({total_pairs} pairs across {compared_buckets} compared buckets; "
        f"{cross_layer_pairs} cross-layer; back-linked {backlinked_acts} acts)"
    )

    # Reset module scratch so a second in-process run starts clean.
    _CROSS_LAYER_SRC_VECTORS = {}
    _KOV_ACT_IRIS = set()
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the keyword-overlap semantic similarity index."
    )
    parser.add_argument(
        "--emit-sample",
        type=int,
        default=0,
        metavar="N",
        dest="emit_sample",
        help=(
            "Also write krr_outputs/reports/similarity_sample.json with N "
            "randomly-sampled (source, target, score, shared_keywords) "
            "tuples for human precision review. 0 (default) = don't emit."
        ),
    )
    parser.add_argument(
        "--no-kov",
        dest="include_kov_similarity",
        action="store_false",
        default=True,
        help=(
            "Skip the KOV act-level bucketed similarity pass (and its "
            "krr_outputs/similarity/kov_similarity_index.json output + KOV "
            "act-node back-links). The provision-level laws+state index is "
            "unaffected and stays byte-identical to a run without this flag."
        ),
    )
    # ``argv is None`` -> parse an empty list, not ``sys.argv``. Callers
    # that want CLI parsing (the __main__ block) pass ``sys.argv[1:]``
    # explicitly; programmatic callers (tests) get safe defaults.
    return parser.parse_args([] if argv is None else argv)


def main(argv: list[str] | None = None):
    args = parse_args(argv)

    print("=" * 60)
    print("Generating semantic similarity index (keyword-based)")
    print("=" * 60)

    # Load all provisions with their keywords
    print("\n[1/4] Loading provisions and extracting keywords...")
    provisions: list[dict] = []  # {id, label, source_act, keywords, file, act_type}
    file_counts_by_type = {"law": 0, "state_regulation": 0, "kov": 0, "other": 0}
    provision_counts_by_type = {"law": 0, "state_regulation": 0, "kov": 0, "other": 0}
    excluded_for_keyword_floor_by_type: dict[str, int] = {
        "law": 0, "state_regulation": 0, "kov": 0, "other": 0,
    }

    # provision pass stays laws+state only; KOV similarity is a separate act-level pass
    jsonld_files = iter_peep_files(include_kov=False)
    for fpath in jsonld_files:
        file_provisions, act_type, excluded_for_keyword_floor = (
            extract_provisions_from_file(fpath)
        )
        if act_type is None:
            continue
        file_counts_by_type[act_type] = file_counts_by_type.get(act_type, 0) + 1
        provision_counts_by_type[act_type] = (
            provision_counts_by_type.get(act_type, 0) + len(file_provisions)
        )
        excluded_for_keyword_floor_by_type[act_type] = (
            excluded_for_keyword_floor_by_type.get(act_type, 0)
            + excluded_for_keyword_floor
        )
        provisions.extend(file_provisions)

    print(f"  Loaded {len(provisions)} provisions with keywords")
    total_excluded = sum(excluded_for_keyword_floor_by_type.values())
    if total_excluded:
        print(
            f"\nExcluded due to keyword-floor (<{MIN_SHARED_KEYWORDS} keywords required):"
        )
        for act_type_name, count in sorted(excluded_for_keyword_floor_by_type.items()):
            if count:
                print(f"  {act_type_name}: {count} provisions")

    # Down-weight corpus-wide generic tokens: any keyword appearing in
    # >GENERIC_DOC_FREQUENCY_CAP of provisions behaves like an undeclared
    # stopword and is stripped from every keyword set before pair scoring,
    # so generic-but-not-boilerplate matches (e.g. "Menetlus", "Sunniraha
    # määr") can no longer rack up high Jaccard scores on connectives alone.
    generic_keywords = compute_generic_keywords(provisions)
    apply_generic_keyword_filter(provisions, generic_keywords)
    if generic_keywords:
        print(
            f"  Dropped {len(generic_keywords)} corpus-generic keyword(s) "
            f"(appear in >{int(GENERIC_DOC_FREQUENCY_CAP * 100)}% of provisions): "
            + ", ".join(sorted(generic_keywords)[:15])
            + (" ..." if len(generic_keywords) > 15 else "")
        )

    # Build inverted index for efficient matching
    print("\n[2/4] Building inverted keyword index...")
    keyword_index: dict[str, list[int]] = defaultdict(list)
    for idx, prov in enumerate(provisions):
        for kw in prov["keywords"]:
            keyword_index[kw].append(idx)

    print(f"  Unique keywords: {len(keyword_index)}")

    # Find similar provision pairs.
    #
    # estleg:semanticallySimilarTo is a *symmetric* relation, so each
    # provision's neighbour list is built from ALL candidates regardless of
    # scan order: the forward (j > i) scan below discovers every unordered
    # pair exactly once and scores it once, then registers the peer on BOTH
    # provisions. The MAX_SIMILAR_PER_PROVISION cap is applied per provision
    # *after* symmetrization (so it can never make A->B exist without B->A
    # unless one side's own cap genuinely drops it), with a deterministic
    # (-score, target_id) tie-break. Cap drops are reported (no silent
    # truncation) via pairs_truncated_by_cap.
    print("\n[3/4] Computing similarity pairs...")
    # provision index -> list of (score, peer_index) candidate neighbours.
    neighbours: dict[int, list[tuple[float, int]]] = defaultdict(list)

    for i, prov_a in enumerate(provisions):
        if i % 500 == 0 and i > 0:
            print(f"    Processed {i}/{len(provisions)} provisions...")

        # Find candidate provisions via inverted index (forward only: each
        # unordered pair is discovered and scored exactly once here).
        candidate_counts: dict[int, int] = defaultdict(int)
        for kw in prov_a["keywords"]:
            for j in keyword_index[kw]:
                if j > i:  # discover each unordered pair once
                    candidate_counts[j] += 1

        for j, shared_count in candidate_counts.items():
            if shared_count < MIN_SHARED_KEYWORDS:
                continue

            prov_b = provisions[j]

            # Skip same-law comparisons (by source_act string, file, or IRI prefix)
            if prov_a["source_act"] and prov_a["source_act"] == prov_b["source_act"]:
                continue
            if prov_a["id"] == prov_b["id"]:
                continue
            if prov_a["file"] == prov_b["file"]:
                continue
            # Catch multi-file laws (e.g. asjaoigusseadus_osa1 vs _osa2)
            if law_prefix(prov_a["id"]) == law_prefix(prov_b["id"]):
                continue

            sim = jaccard_similarity(prov_a["keywords"], prov_b["keywords"])
            if sim >= MIN_SIMILARITY:
                # Symmetric: the pair counts as a candidate neighbour for both.
                neighbours[i].append((sim, j))
                neighbours[j].append((sim, i))

    # Per-provision deterministic sort + top-N cap (after symmetrization).
    # Every dropped neighbour is one directed edge silently lost before this
    # fix; pairs_truncated_by_cap surfaces the count.
    pairs_truncated_by_cap = 0
    similarity_pairs: list[dict] = []
    for i, prov_a in enumerate(provisions):
        cands = neighbours.get(i)
        if not cands:
            continue
        cands.sort(key=lambda item: (-item[0], provisions[item[1]]["id"]))
        if len(cands) > MAX_SIMILAR_PER_PROVISION:
            pairs_truncated_by_cap += len(cands) - MAX_SIMILAR_PER_PROVISION
        for sim, j in cands[:MAX_SIMILAR_PER_PROVISION]:
            prov_b = provisions[j]
            similarity_pairs.append({
                "source": prov_a["id"],
                "source_label": prov_a["label"],
                "source_act": prov_a["source_act"],
                "target": prov_b["id"],
                "target_label": prov_b["label"],
                "target_act": prov_b["source_act"],
                "similarity": round(sim, 3),
                "shared_keywords": len(prov_a["keywords"] & prov_b["keywords"]),
            })

    similarity_pairs.sort(key=lambda p: (p["source"], p["target"]))
    print(f"  Found {len(similarity_pairs)} similarity pairs")
    if pairs_truncated_by_cap:
        print(
            f"  Capped {pairs_truncated_by_cap} directed edge(s) at "
            f"MAX_SIMILAR_PER_PROVISION={MAX_SIMILAR_PER_PROVISION} per provision"
        )

    # Save similarity index
    print("\n[4/4] Saving outputs...")
    index_path = KRR_DIR / "similarity_index.json"
    save_json(index_path, {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "relation_semantics": "candidate",
        "algorithm": {
            "name": "keyword_jaccard",
            # v3 (#422): per-pair score/status keys stripped from the
            # published graph (they mis-attached to the target node); the
            # in-file estleg:semanticallySimilarTo link is now a plain IRI
            # ref and the per-pair scores live only in this sidecar's
            # "pairs" list. v2 added the corpus document-frequency cap on
            # generic keywords (see module docstring).
            "version": "3",
            "source_fields": ["estleg:summary"],
            "generic_keyword_doc_frequency_cap": GENERIC_DOC_FREQUENCY_CAP,
            "generic_keywords_dropped": sorted(generic_keywords),
        },
        "link_emission": {
            # #422: graph emission of per-pair confidence stopped 2026-06.
            "in_graph_shape": (
                "estleg:semanticallySimilarTo is emitted as a plain "
                "{\"@id\": <target>} IRI reference; no per-pair "
                "estleg:similarityScore / estleg:similarityStatus is written "
                "into the published graph (those keys mis-attached to the "
                "target node — see #422)."
            ),
            "scores_location": (
                "Per-pair Jaccard scores and the candidate status live here "
                "in the sidecar 'pairs' list (each pair's 'similarity' field "
                "+ relation_semantics), keyed by the directed (source, "
                "target) pair. Deferred reification: #463."
            ),
            "status_literal": SIMILARITY_STATUS,
        },
        "quality_evaluation": {
            "status": "not_evaluated",
            "note": (
                "Similarity pairs are heuristic candidate links pending "
                "reviewed precision metrics. Run with --emit-sample N to "
                "produce krr_outputs/reports/similarity_sample.json for "
                "human labelling."
            ),
        },
        "total_provisions": len(provisions),
        # total_pairs counts *directed* edges (the symmetric relation emits
        # both A->B and B->A); see the [3/4] comment.
        "total_pairs": len(similarity_pairs),
        "pairs_truncated_by_cap": pairs_truncated_by_cap,
        "candidate_files_by_type": file_counts_by_type,
        "provisions_by_type": provision_counts_by_type,
        "threshold": MIN_SIMILARITY,
        "min_shared_keywords": MIN_SHARED_KEYWORDS,
        "max_similar_per_provision": MAX_SIMILAR_PER_PROVISION,
        "pairs": similarity_pairs,
    })
    print(f"  Saved: {index_path.name} ({len(similarity_pairs)} pairs)")

    # Update JSON-LD files with similarity links in one write per touched file.
    # Each (source, target) carries its own Jaccard score so the per-link
    # provenance written below is accurate. similarity_pairs holds *directed*
    # edges (both A->B and B->A survive symmetrization), so grouping by the
    # source provision's file writes the reciprocal estleg:semanticallySimilarTo
    # back-link into *both* files — the relation is symmetric in the graph.
    prov_id_to_file = {p["id"]: p["file"] for p in provisions}
    # file -> source_id -> {target_id: score}
    file_updates: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for pair in similarity_pairs:
        src_file = prov_id_to_file.get(pair["source"])
        if src_file:
            file_updates[src_file][pair["source"]][pair["target"]] = pair["similarity"]

    updated_files = 0
    # provision pass stays laws+state only; KOV similarity is a separate act-level pass
    candidate_files = {relative_output_path(path): path for path in iter_peep_files(include_kov=False)}
    for filename, fpath in candidate_files.items():
        node_updates = file_updates.get(filename, {})
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            continue

        modified = False
        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if "estleg:semanticallySimilarTo" in node:
                del node["estleg:semanticallySimilarTo"]
                modified = True
            if node_id in node_updates:
                # #422: emit plain {"@id": target} refs only — the per-pair
                # score lives in similarity_index.json, never as a triple on
                # the target node. target_scores' keys are the link targets;
                # the score values are intentionally unused for emission.
                target_scores = node_updates[node_id]
                node["estleg:semanticallySimilarTo"] = [
                    similarity_link_value(t)
                    for t in sorted(target_scores)
                ]
                modified = True

        if modified:
            save_json(fpath, doc)
            updated_files += 1

    print(f"  Updated {updated_files} JSON-LD files with similarity links")

    # KOV act-level similarity pass (Layer 3). Independent of the
    # provision-level pass above: a SEPARATE act-level computation over
    # estleg:MunicipalRegulation acts only. Gated on --no-kov's inverse so
    # that, when skipped, similarity_index.json and the provision
    # semanticallySimilarTo back-links are byte-identical to today.
    if args.include_kov_similarity:
        print("\n" + "=" * 60)
        print("KOV act-level similarity (bucketed TF-IDF cosine)")
        print("=" * 60)
        run_kov_similarity_pass()
    else:
        # --no-kov: clear any previously-emitted KOV similarity so the opt-out
        # is effective on an already-generated tree (not just a fresh one).
        print("\n[KOV] --no-kov: clearing existing KOV similarity output...")
        cleared = clear_kov_similarity_output()
        print(f"  Cleared KOV similarity back-links from {cleared} act file(s)")

    # Optional: human precision-review sample.
    sample_written = None
    if args.emit_sample > 0:
        sample_path = REPORTS_DIR / "similarity_sample.json"
        sample_written = emit_precision_sample(
            similarity_pairs, args.emit_sample, sample_path
        )
        print(
            f"  Wrote precision-review sample: {sample_path} "
            f"({sample_written} of {len(similarity_pairs)} pairs)"
        )

    # Generate report
    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "relation_semantics": "candidate",
        "algorithm": {
            "name": "keyword_jaccard",
            "version": "3",
            "source_fields": ["estleg:summary"],
            "generic_keyword_doc_frequency_cap": GENERIC_DOC_FREQUENCY_CAP,
        },
        "link_emission": {
            # #422: graph emission of per-pair confidence stopped 2026-06.
            "in_graph_shape": (
                "estleg:semanticallySimilarTo is emitted as a plain "
                "{\"@id\": <target>} IRI reference; no per-pair "
                "estleg:similarityScore / estleg:similarityStatus is written "
                "into the published graph (those keys mis-attached to the "
                "target node — see #422)."
            ),
            "scores_location": (
                "Per-pair Jaccard scores live in similarity_index.json's "
                "'pairs' list, keyed by the directed (source, target) pair. "
                "Deferred reification: #463."
            ),
            "status_literal": SIMILARITY_STATUS,
        },
        "quality_evaluation": {
            "status": "not_evaluated",
            "note": (
                "Similarity pairs are heuristic candidate links pending "
                "reviewed precision metrics. Run with --emit-sample N to "
                "produce krr_outputs/reports/similarity_sample.json for "
                "human labelling."
            ),
        },
        "total_provisions_analyzed": len(provisions),
        "total_similarity_pairs": len(similarity_pairs),
        "pairs_truncated_by_cap": pairs_truncated_by_cap,
        "files_updated": updated_files,
        "candidate_files_by_type": file_counts_by_type,
        "provisions_by_type": provision_counts_by_type,
        "excluded_provisions": dict(excluded_for_keyword_floor_by_type),
        "generic_keywords_dropped": sorted(generic_keywords),
        "precision_sample": (
            {"path": "reports/similarity_sample.json", "size": sample_written}
            if sample_written is not None
            else None
        ),
        "parameters": {
            "min_similarity": MIN_SIMILARITY,
            "min_shared_keywords": MIN_SHARED_KEYWORDS,
            "max_similar_per_provision": MAX_SIMILAR_PER_PROVISION,
            "generic_keyword_doc_frequency_cap": GENERIC_DOC_FREQUENCY_CAP,
        },
        "top_similar_pairs": similarity_pairs[:20],
        "similarity_distribution": {},
    }

    # Distribution
    buckets = defaultdict(int)
    for pair in similarity_pairs:
        bucket = f"{int(pair['similarity'] * 10) / 10:.1f}"
        buckets[bucket] += 1
    report["similarity_distribution"] = dict(sorted(buckets.items()))

    report_path = KRR_DIR / "similarity_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # Summary
    print("\n" + "=" * 60)
    print(f"Done! Found {len(similarity_pairs)} cross-law similarity pairs.")
    print(f"Updated {updated_files} JSON-LD files.")
    if similarity_pairs:
        avg_sim = sum(p["similarity"] for p in similarity_pairs) / len(similarity_pairs)
        print(f"Average similarity: {avg_sim:.3f}")
        print("\nTop 5 most similar cross-law pairs:")
        for pair in sorted(similarity_pairs, key=lambda p: -p["similarity"])[:5]:
            print(f"  {pair['similarity']:.3f}: {pair['source_label']} <-> {pair['target_label']}")
    print("=" * 60)


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
