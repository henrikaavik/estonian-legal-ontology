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

Per-link provenance
-------------------
Each ``estleg:semanticallySimilarTo`` value injected into a provision peep
file is enriched in place (lowest-churn option: same property, same node,
same list — only the list *elements* gain provenance) from a bare
``{"@id": target}`` to::

    {"@id": target, "estleg:similarityScore": <jaccard>, "estleg:similarityStatus": "candidate"}

so every published link is self-describing about its confidence and the
fact that it is a heuristic candidate, not an asserted legal relation.
A reified ``estleg:SimilarityLink`` node was considered but rejected: it
would add a top-level node + four new vocabulary terms to every one of
the ~450 peep files, exploding the diff for no extra signal. The nested
``estleg:similarityScore`` (xsd:decimal Jaccard value) and
``estleg:similarityStatus`` (literal ``"candidate"``) keys appear only
inside ``estleg:semanticallySimilarTo`` value objects.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from estleg_common import iter_peep_files, jsonld_text, save_json

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
REPORTS_DIR = KRR_DIR / "reports"

# Status literal attached to every published similarity link. "candidate"
# mirrors the index/report-level relation_semantics — these are heuristic
# keyword-overlap links pending reviewed precision metrics, not asserted
# legal semantics.
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

# Boilerplate provision patterns to exclude (entry-into-force, repeal clauses, etc.)
BOILERPLATE_PATTERNS = [
    re.compile(r'välja jäetud', re.IGNORECASE),
    re.compile(r'valja jaetud', re.IGNORECASE),
    re.compile(r'^Kehtetu', re.IGNORECASE),
    re.compile(r"jõustub.*riigi teatajas", re.IGNORECASE),
    re.compile(r"jõustub.*avaldamisele", re.IGNORECASE),
    re.compile(r"käesolev seadus jõustub", re.IGNORECASE),
    re.compile(r"seadus jõustub", re.IGNORECASE),
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


def similarity_link_value(target: str, score: float) -> dict:
    """Build one ``estleg:semanticallySimilarTo`` list element with provenance.

    Lowest-churn per-link provenance: the value object keeps ``@id`` plus
    two nested keys — ``estleg:similarityScore`` (the Jaccard value as an
    ``xsd:decimal`` typed literal) and ``estleg:similarityStatus`` (the
    literal ``"candidate"``). No reified node, no new provision-level
    property. Score is rounded to 3 dp to mirror the index file.
    """
    return {
        "@id": target,
        "estleg:similarityScore": {
            "@value": f"{round(score, 3)}",
            "@type": "xsd:decimal",
        },
        "estleg:similarityStatus": SIMILARITY_STATUS,
    }


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
        "generated": datetime.now(timezone.utc).date().isoformat(),
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

    jsonld_files = iter_peep_files(include_kov=False)  # DEFERRED to Layer 3
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

    # Find similar provision pairs
    print("\n[3/4] Computing similarity pairs...")
    # For each provision, find candidates that share keywords
    similarity_pairs: list[dict] = []
    processed = 0

    for i, prov_a in enumerate(provisions):
        if i % 500 == 0 and i > 0:
            print(f"    Processed {i}/{len(provisions)} provisions...")

        # Find candidate provisions via inverted index
        candidate_counts: dict[int, int] = defaultdict(int)
        for kw in prov_a["keywords"]:
            for j in keyword_index[kw]:
                if j > i:  # Only check forward to avoid duplicates
                    candidate_counts[j] += 1

        # Filter candidates with enough shared keywords
        best_similar: list[tuple[float, int]] = []
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
                best_similar.append((sim, j))

        # Keep top N similar provisions
        best_similar.sort(key=lambda item: (-item[0], provisions[item[1]]["id"]))
        for sim, j in best_similar[:MAX_SIMILAR_PER_PROVISION]:
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
            processed += 1

    similarity_pairs.sort(key=lambda p: (p["source"], p["target"]))
    print(f"  Found {len(similarity_pairs)} similarity pairs")

    # Save similarity index
    print("\n[4/4] Saving outputs...")
    index_path = KRR_DIR / "similarity_index.json"
    save_json(index_path, {
        "generated": datetime.now(timezone.utc).date().isoformat(),
        "relation_semantics": "candidate",
        "algorithm": {
            "name": "keyword_jaccard",
            # v2: per-link provenance on estleg:semanticallySimilarTo
            # value objects + corpus document-frequency cap on generic
            # keywords (see module docstring).
            "version": "2",
            "source_fields": ["estleg:summary"],
            "generic_keyword_doc_frequency_cap": GENERIC_DOC_FREQUENCY_CAP,
            "generic_keywords_dropped": sorted(generic_keywords),
        },
        "link_provenance": {
            "shape": (
                "estleg:semanticallySimilarTo value objects carry "
                "estleg:similarityScore (xsd:decimal Jaccard) and "
                "estleg:similarityStatus literals"
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
        "total_pairs": len(similarity_pairs),
        "candidate_files_by_type": file_counts_by_type,
        "provisions_by_type": provision_counts_by_type,
        "threshold": MIN_SIMILARITY,
        "min_shared_keywords": MIN_SHARED_KEYWORDS,
        "pairs": similarity_pairs,
    })
    print(f"  Saved: {index_path.name} ({len(similarity_pairs)} pairs)")

    # Update JSON-LD files with similarity links in one write per touched file.
    # Each (source, target) carries its own Jaccard score so the per-link
    # provenance written below is accurate.
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
                target_scores = node_updates[node_id]
                node["estleg:semanticallySimilarTo"] = [
                    similarity_link_value(t, target_scores[t])
                    for t in sorted(target_scores)
                ]
                modified = True

        if modified:
            save_json(fpath, doc)
            updated_files += 1

    print(f"  Updated {updated_files} JSON-LD files with similarity links")

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
        "generated": datetime.now(timezone.utc).date().isoformat(),
        "relation_semantics": "candidate",
        "algorithm": {
            "name": "keyword_jaccard",
            "version": "2",
            "source_fields": ["estleg:summary"],
            "generic_keyword_doc_frequency_cap": GENERIC_DOC_FREQUENCY_CAP,
        },
        "link_provenance": {
            "shape": (
                "estleg:semanticallySimilarTo value objects carry "
                "estleg:similarityScore (xsd:decimal Jaccard) and "
                "estleg:similarityStatus literals"
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
