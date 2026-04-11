#!/usr/bin/env python3
"""
Classify legal provisions by normative type (obligation, right, permission, prohibition).

Analyses the estleg:summary text of each provision node in the JSON-LD law files
and assigns an estleg:normativeType based on Estonian deontic language patterns.
Also attempts to extract estleg:dutyHolder where possible.

Outputs:
  - Updated *_peep.json files with estleg:normativeType and estleg:dutyHolder
  - krr_outputs/deontic_classification_report.json  (statistics)
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

# ---------- deontic pattern definitions ----------

# Each entry: (compiled regex, weight)
# Higher weight = stronger signal for that normative type.
OBLIGATION_PATTERNS = [
    (re.compile(r"\bon kohustatud\b", re.IGNORECASE), 3),
    (re.compile(r"\bpeab\b", re.IGNORECASE), 2),
    (re.compile(r"\btuleb\b", re.IGNORECASE), 2),
    (re.compile(r"\bon kohustus\b", re.IGNORECASE), 3),
    (re.compile(r"\bkohustub\b", re.IGNORECASE), 3),
    (re.compile(r"\bon sunnitud\b", re.IGNORECASE), 2),
]

RIGHT_PATTERNS = [
    (re.compile(r"\bon õigus\b", re.IGNORECASE), 3),
    (re.compile(r"\bõigus on\b", re.IGNORECASE), 3),
    (re.compile(r"\bon õigustatud\b", re.IGNORECASE), 3),
    (re.compile(r"\bon lubatud nõuda\b", re.IGNORECASE), 3),
    (re.compile(r"\bvõib\b", re.IGNORECASE), 1),  # weak – also permissive
]

PERMISSION_PATTERNS = [
    (re.compile(r"\bon lubatud\b", re.IGNORECASE), 3),
    (re.compile(r"\btohib\b", re.IGNORECASE), 3),
    (re.compile(r"\bon vaba\b", re.IGNORECASE), 2),
    (re.compile(r"\bvõib\b", re.IGNORECASE), 1),  # permissive context
]

PROHIBITION_PATTERNS = [
    (re.compile(r"\bon keelatud\b", re.IGNORECASE), 4),
    (re.compile(r"\bei tohi\b", re.IGNORECASE), 4),
    (re.compile(r"\bei ole lubatud\b", re.IGNORECASE), 3),
    (re.compile(r"\bpole lubatud\b", re.IGNORECASE), 3),
    (re.compile(r"\bon karistatav\b", re.IGNORECASE), 3),
]

NORM_TYPES = {
    "obligation": ("estleg:NormType_Obligation", OBLIGATION_PATTERNS),
    "right": ("estleg:NormType_Right", RIGHT_PATTERNS),
    "permission": ("estleg:NormType_Permission", PERMISSION_PATTERNS),
    "prohibition": ("estleg:NormType_Prohibition", PROHIBITION_PATTERNS),
}

# ---------- duty-holder extraction ----------

# Pattern: "<noun phrase> peab / on kohustatud / kohustub / ..."
DUTY_HOLDER_RE = re.compile(
    r"(\b[A-ZÄÖÜÕŠŽ][a-zäöüõšž]+(?:\s+[a-zäöüõšž]+){0,3})"
    r"\s+(?:peab|on kohustatud|kohustub|on sunnitud|tuleb)",
    re.UNICODE,
)


def save_json(filepath: Path, doc: dict) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_json(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"  WARN: cannot load {filepath.name}: {exc}")
        return None


def score_text(text: str, patterns: list[tuple[re.Pattern, int]]) -> int:
    """Sum weights of all matching patterns in *text*."""
    total = 0
    for pat, weight in patterns:
        if pat.search(text):
            total += weight
    return total


def classify_provision(text: str) -> str | None:
    """Return the dominant normative-type IRI, or None if nothing matched."""
    scores: dict[str, int] = {}
    for norm_key, (iri, patterns) in NORM_TYPES.items():
        s = score_text(text, patterns)
        if s > 0:
            scores[norm_key] = s

    if not scores:
        return None

    # Prohibition beats permission when both match (ei tohi vs. on lubatud)
    best = max(scores, key=lambda k: scores[k])
    return NORM_TYPES[best][0]


def extract_duty_holder(text: str) -> str | None:
    """Try to extract who must comply from the provision text."""
    m = DUTY_HOLDER_RE.search(text)
    if m:
        holder = m.group(1).strip()
        # Skip very generic words that are not real actors
        skip = {"Käesolev", "Käesoleva", "Paragrahv", "Seadus", "Lõige", "Punkt"}
        if holder.split()[0] in skip:
            return None
        return holder
    return None


def main() -> None:
    print("=" * 70)
    print("Estonian Legal Ontology - Deontic Classification")
    print("=" * 70)

    law_files = sorted(KRR_DIR.glob("*_peep.json"))
    print(f"\n[1/3] Found {len(law_files)} law files to process")

    # --- Clearing pass: remove old deontic data from all files ---
    print("  Clearing old deontic classification data from all files...")
    for filepath in law_files:
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue
        cleared = False
        for node in doc["@graph"]:
            if "estleg:normativeType" in node:
                del node["estleg:normativeType"]
                cleared = True
            if "estleg:dutyHolder" in node:
                del node["estleg:dutyHolder"]
                cleared = True
        if cleared:
            save_json(filepath, doc)
    print("  Done clearing.")

    # Per-law and per-type statistics
    stats_per_law: dict[str, dict[str, int]] = {}
    stats_per_type: dict[str, int] = defaultdict(int)
    total_provisions = 0
    total_classified = 0
    total_duty_holders = 0
    files_modified = 0

    for idx, filepath in enumerate(law_files, 1):
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue

        law_name = filepath.stem.replace("_peep", "")
        law_stats: dict[str, int] = defaultdict(int)
        modified = False

        for node in doc["@graph"]:
            summary = node.get("estleg:summary", "")
            if not summary:
                continue

            total_provisions += 1

            norm_iri = classify_provision(summary)
            if norm_iri:
                node["estleg:normativeType"] = {"@id": norm_iri}
                total_classified += 1
                # Readable key for stats
                short = norm_iri.split("_", 1)[1] if "_" in norm_iri else norm_iri
                law_stats[short] += 1
                stats_per_type[short] += 1
                modified = True

            holder = extract_duty_holder(summary)
            if holder:
                node["estleg:dutyHolder"] = holder
                total_duty_holders += 1
                modified = True

        if modified:
            save_json(filepath, doc)
            files_modified += 1

        if law_stats:
            stats_per_law[law_name] = dict(law_stats)

        if idx % 100 == 0 or idx == len(law_files):
            print(f"  [{idx}/{len(law_files)}] processed – {total_classified} classified so far")

    # ---------- report ----------
    print(f"\n[2/3] Generating report...")

    report = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "summary": {
            "total_law_files": len(law_files),
            "files_modified": files_modified,
            "total_provisions_with_text": total_provisions,
            "total_classified": total_classified,
            "total_duty_holders_extracted": total_duty_holders,
            "classification_rate": f"{total_classified / max(total_provisions, 1) * 100:.1f}%",
        },
        "by_normative_type": dict(sorted(stats_per_type.items(), key=lambda x: -x[1])),
        "by_law": {
            k: v for k, v in sorted(stats_per_law.items(), key=lambda x: -sum(x[1].values()))
        },
    }

    report_path = KRR_DIR / "deontic_classification_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # ---------- summary ----------
    print(f"\n[3/3] SUMMARY")
    print("=" * 70)
    print(f"  Provisions analysed:    {total_provisions}")
    print(f"  Classified:             {total_classified}")
    print(f"  Duty holders extracted: {total_duty_holders}")
    print(f"  Files modified:         {files_modified}")
    print()
    for ntype, count in sorted(stats_per_type.items(), key=lambda x: -x[1]):
        print(f"    {ntype:20s}  {count}")
    print("=" * 70)


if __name__ == "__main__":
    main()
