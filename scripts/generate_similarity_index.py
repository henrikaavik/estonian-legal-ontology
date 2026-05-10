#!/usr/bin/env python3
"""
Generate a semantic similarity index using keyword overlap between provisions.

Compares provisions across different laws by shared keywords in their
summary text. Adds estleg:semanticallySimilarTo links for provisions
with high keyword overlap from different laws.

Generates:
  - krr_outputs/similarity_index.json
  - krr_outputs/similarity_report.json
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from estleg_common import iter_peep_files, jsonld_text, save_json

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"

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

# Estonian stopwords to filter out
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
}

# Minimum keyword length to consider
MIN_KEYWORD_LEN = 4
# Minimum shared keywords for similarity
MIN_SHARED_KEYWORDS = 3
# Minimum Jaccard similarity threshold
MIN_SIMILARITY = 0.3
# Maximum similar provisions to link per provision
MAX_SIMILAR_PER_PROVISION = 5

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


def extract_provisions_from_file(fpath: Path) -> tuple[list[dict], str | None]:
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return [], None

    act_node = find_act_node(doc)
    if act_node is None:
        return [], None
    act_type = classify_act_type(fpath, act_node)
    file_name = relative_output_path(fpath)

    provisions: list[dict] = []
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
    return provisions, act_type


def main():
    print("=" * 60)
    print("Generating semantic similarity index (keyword-based)")
    print("=" * 60)

    # Load all provisions with their keywords
    print("\n[1/4] Loading provisions and extracting keywords...")
    provisions: list[dict] = []  # {id, label, source_act, keywords, file, act_type}
    file_counts_by_type = {"law": 0, "state_regulation": 0, "kov": 0, "other": 0}
    provision_counts_by_type = {"law": 0, "state_regulation": 0, "kov": 0, "other": 0}

    jsonld_files = iter_peep_files(include_kov=False)  # DEFERRED to Layer 3
    for fpath in jsonld_files:
        file_provisions, act_type = extract_provisions_from_file(fpath)
        if act_type is None:
            continue
        file_counts_by_type[act_type] = file_counts_by_type.get(act_type, 0) + 1
        provision_counts_by_type[act_type] = (
            provision_counts_by_type.get(act_type, 0) + len(file_provisions)
        )
        provisions.extend(file_provisions)

    print(f"  Loaded {len(provisions)} provisions with keywords")

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
            "version": "1",
            "source_fields": ["estleg:summary"],
        },
        "quality_evaluation": {
            "status": "not_evaluated",
            "note": "Similarity pairs are heuristic candidate links pending reviewed precision metrics.",
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
    prov_id_to_file = {p["id"]: p["file"] for p in provisions}
    file_updates: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for pair in similarity_pairs:
        src_file = prov_id_to_file.get(pair["source"])
        if src_file:
            file_updates[src_file][pair["source"]].append(pair["target"])

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
                targets = sorted(node_updates[node_id])
                node["estleg:semanticallySimilarTo"] = [{"@id": t} for t in targets]
                modified = True

        if modified:
            save_json(fpath, doc)
            updated_files += 1

    print(f"  Updated {updated_files} JSON-LD files with similarity links")

    # Generate report
    report = {
        "generated": datetime.now(timezone.utc).date().isoformat(),
        "relation_semantics": "candidate",
        "algorithm": {
            "name": "keyword_jaccard",
            "version": "1",
            "source_fields": ["estleg:summary"],
        },
        "quality_evaluation": {
            "status": "not_evaluated",
            "note": "Similarity pairs are heuristic candidate links pending reviewed precision metrics.",
        },
        "total_provisions_analyzed": len(provisions),
        "total_similarity_pairs": len(similarity_pairs),
        "files_updated": updated_files,
        "candidate_files_by_type": file_counts_by_type,
        "provisions_by_type": provision_counts_by_type,
        "parameters": {
            "min_similarity": MIN_SIMILARITY,
            "min_shared_keywords": MIN_SHARED_KEYWORDS,
            "max_similar_per_provision": MAX_SIMILAR_PER_PROVISION,
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
    main()
