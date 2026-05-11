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
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from estleg_common import iter_peep_files, jsonld_text
from kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

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
NORM_TIE_PRIORITY = {
    "prohibition": 4,
    "obligation": 3,
    "right": 2,
    "permission": 1,
}

# ---------- duty-holder extraction ----------

# Pattern: "<noun phrase> peab / on kohustatud / kohustub / ..."
# The first word must be Capital-initial. Subsequent words must be either
# Capital-initial themselves (e.g. "Vastutav Töötleja") or one of a small
# whitelist of Estonian connectors ("ja", "või", "ning", "ega") so that
# adverbials such as "Igal aastal …" do not get absorbed into the
# duty-holder phrase. Hyphenation inside a single word (e.g. "Tervise-")
# is supported via the trailing ``-`` in the letter character class.
# Digits are deliberately excluded (we use an explicit Estonian letter
# class instead of ``\w``).
_LETTER = r"[A-Za-zÄÖÜÕŠŽäöüõšž-]"
_CAPITAL_WORD = rf"[A-ZÄÖÜÕŠŽ]{_LETTER}+"
_CONNECTOR = r"(?:ja|või|ning|ega)\b"
DUTY_HOLDER_RE = re.compile(
    rf"(\b{_CAPITAL_WORD}"
    rf"(?:\s+(?:{_CAPITAL_WORD}|{_CONNECTOR})){{0,4}})"
    r"\s+(?:peab|on kohustatud|kohustub|on sunnitud|tuleb)",
    re.UNICODE,
)
GENERIC_DUTY_HOLDER_STARTS = {"Käesolev", "Käesoleva", "Paragrahv", "Seadus", "Lõige", "Punkt"}


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

    best = max(scores, key=lambda k: (scores[k], NORM_TIE_PRIORITY[k]))
    return NORM_TYPES[best][0]


def extract_duty_holder(text: str) -> str | None:
    """Try to extract who must comply from the provision text."""
    for m in DUTY_HOLDER_RE.finditer(text):
        holder = m.group(1).strip()
        if holder.split()[0] in GENERIC_DUTY_HOLDER_STARTS:
            continue
        return holder
    return None


def main() -> None:
    print("=" * 70)
    print("Estonian Legal Ontology - Deontic Classification")
    print("=" * 70)

    _start = time.perf_counter()
    law_files = iter_peep_files()  # uses new include_kov=True default
    _kov_files = [f for f in law_files if "regulations/kov" in str(f)]
    print(f"\n[1/3] Found {len(law_files)} law files to process")

    # Coverage counters
    _files_processed = 0
    _files_processed_kov = 0
    _files_with_output = 0
    _files_with_output_kov = 0
    _files_skipped = 0
    _skip_reasons: dict[str, int] = {}
    _triples = 0
    _triples_kov = 0
    _fallback_hits = 0
    _unresolved = 0
    _failures: list[str] = []

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
        is_kov = "regulations/kov" in str(filepath)
        try:
            doc = load_json(filepath)
            if doc is None:
                _files_skipped += 1
                _skip_reasons["json_decode_error"] = (
                    _skip_reasons.get("json_decode_error", 0) + 1
                )
                continue
            if "@graph" not in doc:
                _files_skipped += 1
                _skip_reasons["no_graph"] = _skip_reasons.get("no_graph", 0) + 1
                continue

            _triples_before = _triples
            law_name = filepath.stem.replace("_peep", "")
            law_stats: dict[str, int] = defaultdict(int)
            modified = False

            for node in doc["@graph"]:
                # Generators may emit estleg:summary as a plain string or as a
                # {"@value": ..., "@language": "et"} value object; jsonld_text
                # normalises both before the regex-based classifier runs.
                summary = jsonld_text(node.get("estleg:summary", ""))
                if not summary:
                    continue

                total_provisions += 1

                norm_iri = classify_provision(summary)
                if norm_iri:
                    node["estleg:normativeType"] = {"@id": norm_iri}
                    _triples += 1
                    if is_kov:
                        _triples_kov += 1
                    total_classified += 1
                    # Readable key for stats
                    short = norm_iri.split("_", 1)[1] if "_" in norm_iri else norm_iri
                    law_stats[short] += 1
                    stats_per_type[short] += 1
                    modified = True

                holder = extract_duty_holder(summary)
                if holder:
                    node["estleg:dutyHolder"] = holder
                    _triples += 1
                    if is_kov:
                        _triples_kov += 1
                    total_duty_holders += 1
                    modified = True

            if modified:
                save_json(filepath, doc)
                files_modified += 1

            if law_stats:
                stats_per_law[law_name] = dict(law_stats)

            _files_processed += 1
            if is_kov:
                _files_processed_kov += 1
            if _triples > _triples_before:
                _files_with_output += 1
                if is_kov:
                    _files_with_output_kov += 1
        except Exception as exc:  # noqa: BLE001
            _failures.append(f"{filepath.name}: {exc!r}")

        if idx % 100 == 0 or idx == len(law_files):
            print(f"  [{idx}/{len(law_files)}] processed – {total_classified} classified so far")

    # ---------- report ----------
    print("\n[2/3] Generating report...")

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
    print("\n[3/3] SUMMARY")
    print("=" * 70)
    print(f"  Provisions analysed:    {total_provisions}")
    print(f"  Classified:             {total_classified}")
    print(f"  Duty holders extracted: {total_duty_holders}")
    print(f"  Files modified:         {files_modified}")
    print()
    for ntype, count in sorted(stats_per_type.items(), key=lambda x: -x[1]):
        print(f"    {ntype:20s}  {count}")
    print("=" * 70)

    # ---------- coverage report ----------
    _wall, _rate, _peak_mb = measure_runtime(_start, _files_processed)
    write_coverage_report(
        CoverageReport(
            pipeline="classify_deontic",
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(law_files),
            input_files_kov=len(_kov_files),
            files_processed=_files_processed,
            files_processed_kov=_files_processed_kov,
            files_with_output=_files_with_output,
            files_with_output_kov=_files_with_output_kov,
            files_skipped=_files_skipped,
            skip_reasons=_skip_reasons,
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            fallback_hits=_fallback_hits,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        REPO_ROOT / "krr_outputs" / "reports" / "kov"
        / "classify_deontic_coverage.json",
    )


if __name__ == "__main__":
    main()
