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
from datetime import datetime
from pathlib import Path

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


def save_json(filepath: Path, doc: dict | list):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


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


def main():
    print("=" * 60)
    print("Generating semantic similarity index (keyword-based)")
    print("=" * 60)

    # Load all provisions with their keywords
    print("\n[1/4] Loading provisions and extracting keywords...")
    provisions: list[dict] = []  # {id, label, source_act, keywords, file}

    jsonld_files = sorted(KRR_DIR.glob("*_peep.json"))
    for fpath in jsonld_files:
        # Skip non-law files
        if fpath.parent != KRR_DIR:
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            continue

        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if not node_id or not node_id.startswith("estleg:"):
                continue

            # Skip ontology and class nodes
            types = node.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "owl:Ontology" in types or "owl:Class" in types:
                continue
            if "estleg:TopicCluster" in str(types):
                continue

            summary = node.get("estleg:summary", "")
            label = node.get("rdfs:label", "")
            source_act = node.get("estleg:sourceAct", "")

            # Skip boilerplate provisions (entry-into-force, repeal clauses)
            if is_boilerplate(summary):
                continue

            keywords = extract_keywords(summary) | extract_keywords(label)

            if len(keywords) >= MIN_SHARED_KEYWORDS:
                provisions.append({
                    "id": node_id,
                    "label": label,
                    "source_act": source_act,
                    "keywords": keywords,
                    "file": fpath.name,
                })

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
        best_similar.sort(reverse=True)
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

    print(f"  Found {len(similarity_pairs)} similarity pairs")

    # Save similarity index
    print("\n[4/4] Saving outputs...")
    index_path = KRR_DIR / "similarity_index.json"
    save_json(index_path, {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "total_provisions": len(provisions),
        "total_pairs": len(similarity_pairs),
        "threshold": MIN_SIMILARITY,
        "min_shared_keywords": MIN_SHARED_KEYWORDS,
        "pairs": similarity_pairs,
    })
    print(f"  Saved: {index_path.name} ({len(similarity_pairs)} pairs)")

    # Clearing pass: remove stale estleg:semanticallySimilarTo from all peep files
    for fpath in sorted(KRR_DIR.glob("*_peep.json")):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            continue
        modified = False
        for node in doc.get("@graph", []):
            if "estleg:semanticallySimilarTo" in node:
                del node["estleg:semanticallySimilarTo"]
                modified = True
        if modified:
            save_json(fpath, doc)

    # Update JSON-LD files with similarity links
    # Group pairs by source file
    prov_id_to_file = {p["id"]: p["file"] for p in provisions}
    file_updates: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for pair in similarity_pairs:
        src_file = prov_id_to_file.get(pair["source"])
        if src_file:
            file_updates[src_file][pair["source"]].append(pair["target"])

    updated_files = 0
    for filename, node_updates in file_updates.items():
        fpath = KRR_DIR / filename
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            continue

        modified = False
        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if node_id in node_updates:
                targets = node_updates[node_id]
                node["estleg:semanticallySimilarTo"] = [{"@id": t} for t in targets]
                modified = True

        if modified:
            save_json(fpath, doc)
            updated_files += 1

    print(f"  Updated {updated_files} JSON-LD files with similarity links")

    # Generate report
    report = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "total_provisions_analyzed": len(provisions),
        "total_similarity_pairs": len(similarity_pairs),
        "files_updated": updated_files,
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
        print(f"\nTop 5 most similar cross-law pairs:")
        for pair in sorted(similarity_pairs, key=lambda p: -p["similarity"])[:5]:
            print(f"  {pair['similarity']:.3f}: {pair['source_label']} <-> {pair['target_label']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
