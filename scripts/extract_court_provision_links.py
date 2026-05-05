#!/usr/bin/env python3
"""
Upgrade court decision law references from strings to granular provision IRIs.

This script:
1. Loads all riigikohus_YYYY_peep.json files from krr_outputs/riigikohus/
2. Parses estleg:summary text for specific provision citations
3. Resolves citations to existing provision IRIs
4. Adds estleg:interpretsLaw with IRI values (linking to specific provisions)
5. Adds estleg:interpretedBy inverse links on provision files
6. Keeps existing estleg:referencedLaw string values as fallback
7. Generates court_provision_links_report.json with statistics
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from estleg_common import (
    BODY_CANON,
    CONTEXT,
    FULLNAME_GENITIVE,
    KNOWN_ABBREVIATIONS,
    PAR_SUFFIX,
    _RunCounters,
    _safe_load,
    iter_peep_files,
    normalize_issuer_name,
    save_json,
    sanitize_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
RK_DIR = KRR_DIR / "riigikohus"

NS = "https://data.riik.ee/ontology/estleg#"


def expand_two_digit_year(date_str: str) -> int:
    """Resolve the year component of a citation date to a 4-digit year.

    Two-digit years use the boundary ``y > 50``: ``00..50`` → ``2000..2050``,
    ``51..99`` → ``1951..1999``. Long-form Estonian-month dates already
    carry a 4-digit year; numeric ``d.m.y`` dates may be 2- or 4-digit.
    """
    parts = date_str.split()
    last_token = parts[-1] if parts else ""
    if last_token.isdigit() and len(last_token) == 4:
        return int(last_token)
    # numeric d.m.y form
    pieces = date_str.split(".")
    if len(pieces) >= 3:
        tail = pieces[2].split()[0]   # strip optional trailing ". a." etc.
        digits = "".join(ch for ch in tail if ch.isdigit())
        if not digits:
            return 0
        y = int(digits)
        if y < 100:
            return 1900 + y if y > 50 else 2000 + y
        return y
    return 0


# ---------------------------------------------------------------------------
# KOV act citation regex (Layer 2c PR #3)
# ---------------------------------------------------------------------------
# CRITICAL: do NOT apply re.IGNORECASE globally. Under global IGNORECASE the
# municipality character class [A-ZÕÄÖÜŠŽ] would also accept lowercase
# letters, and the {1,3} quantifier would consume preceding lowercase
# words like "Õiguskantsleri taotlus" or "tunnistada kehtetuks" before
# the municipality name, polluting issuer_norm and breaking resolution.
# Case-insensitivity is scoped via inline (?i:...) only to the body word,
# month names, and act-marker keyword.
PAT_KOV_ACT = re.compile(
    # Municipality: 1-3 titlecase words. The character class enforces
    # uppercase initial + lowercase continuation (case-SENSITIVE).
    r"(?P<municipality>(?:[A-ZÕÄÖÜŠŽ][a-zõäöüšž][\wõäöüšž-]*\s+){1,3})"
    # Body word: case-insensitive scope. Genitive forms first inside each
    # pair so longest-match-wins under alternation is well-defined.
    r"(?P<body>(?i:linnavalitsuse|vallavalitsuse|"
    r"linnavalitsus|vallavalitsus|"
    r"linnavolikogu|vallavolikogu))\s+"
    # Date: numeric d.m.y or long form with case-insensitive Estonian month.
    r"(?P<date>\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4})|"
    r"\d{1,2}\.\s*(?i:jaanuari|veebruari|märtsi|aprilli|mai|juuni|juuli|"
    r"augusti|septembri|oktoobri|novembri|detsembri)\s+\d{4})"
    # Optional " . " / " . a. " / " . aasta " between date and act marker.
    r"\.?\s*(?i:a\.?|aasta)?\s+"
    # Act marker, case-insensitive scope. Covers määrus / määruse / määrust / määrusega.
    r"(?i:määrus(?:e|t|ega)?)\s*nr\s*\.?\s*(?P<num>\d+)",
    re.UNICODE,
)

# Counted-only RT IV reference detector. We do not resolve these (no
# prebuilt RT IV → IRI lookup table); they're tracked under
# skip_reasons["rtiv_form_citation"] so a future PR notices when this
# bucket becomes nonzero.
PAT_RTIV = re.compile(
    r"\bRT\s+IV[,\s]+\d{2,4}(?:[\.\-/]\d{1,2}[\.\-/]\d{2,4}|[,\s]+\d+)"
    r"(?:[,\s]+\d+)?",
    re.IGNORECASE,
)


def build_provision_index() -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, Path]]:
    """
    Scan all law *_peep.json files to build provision indexes.

    Returns:
      - prefix_to_provisions: {prefix: {par_number: full_iri}}
      - source_act_to_prefix: {source_act_name: prefix}
      - iri_to_file: {iri: filepath}
    """
    prefix_to_provisions: dict[str, dict[str, str]] = {}
    source_act_to_prefix: dict[str, str] = {}
    iri_to_file: dict[str, Path] = {}

    for json_file in iter_peep_files(include_kov=False):  # DEFERRED to Layer 2c
        # Skip court decision files
        if json_file.name.startswith("riigikohus"):
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        graph = doc.get("@graph", [])
        if not graph:
            continue

        file_prefix = None
        source_act = None
        for node in graph:
            node_id = node.get("@id", "")
            if "_Par_" in node_id and node_id.startswith("estleg:"):
                local = node_id[len("estleg:"):]
                prefix_part = local.split("_Par_")[0]
                if file_prefix is None:
                    file_prefix = prefix_part
                par_part = local.split("_Par_")[1]

                if file_prefix not in prefix_to_provisions:
                    prefix_to_provisions[file_prefix] = {}
                prefix_to_provisions[file_prefix][par_part] = node_id
                iri_to_file[node_id] = json_file

                if source_act is None:
                    source_act = node.get("estleg:sourceAct", "")

        if file_prefix and source_act:
            source_act_to_prefix[source_act] = file_prefix

    return prefix_to_provisions, source_act_to_prefix, iri_to_file


def build_abbreviation_to_prefix(
    source_act_to_prefix: dict[str, str],
) -> dict[str, str]:
    """Map law abbreviations to IRI prefixes."""
    abbrev_to_prefix: dict[str, str] = {}
    for abbrev, full_name in KNOWN_ABBREVIATIONS.items():
        if full_name in source_act_to_prefix:
            abbrev_to_prefix[abbrev] = source_act_to_prefix[full_name]
    return abbrev_to_prefix


def build_kov_act_index(
    counters: "_RunCounters",
) -> tuple[
    dict[tuple[str, int, str], str],
    set[tuple[str, int, str]],
    dict[str, Path],
    set[str],
]:
    """Build the KOV act index over MunicipalRegulation peeps.

    Returns ``(kov_index, kov_collision_keys, kov_iri_to_file, known_issuer_norms)``.
    Keys are ``(issuer_norm, entryIntoForce.year, actNumber)``. On collision
    (two acts mapping to the same key) the key is removed from kov_index and
    added to kov_collision_keys; never silent-overwrites.
    """
    kov_index: dict[tuple[str, int, str], str] = {}
    kov_collision_keys: set[tuple[str, int, str]] = set()
    kov_iri_to_file: dict[str, Path] = {}
    known_issuer_norms: set[str] = set()

    for f in iter_peep_files():
        doc = _safe_load(f, counters)
        if doc is None:
            continue
        for node in doc.get("@graph", []):
            types = node.get("@type", [])
            if "estleg:MunicipalRegulation" not in types:
                continue
            iri = node.get("@id", "")
            issuer = node.get("estleg:issuer", "")
            entry_force = ""
            eif = node.get("estleg:entryIntoForce")
            if isinstance(eif, dict):
                entry_force = eif.get("@value", "")
            act_number = str(node.get("estleg:actNumber", "") or "")
            if not (iri and issuer and entry_force and act_number):
                break
            issuer_norm = normalize_issuer_name(issuer)
            known_issuer_norms.add(issuer_norm)
            try:
                year = int(entry_force[:4])
            except ValueError:
                break
            key = (issuer_norm, year, act_number)
            if key in kov_collision_keys:
                pass   # already marked ambiguous; subsequent dupes are silent
            elif key in kov_index and kov_index[key] != iri:
                kov_collision_keys.add(key)
                del kov_index[key]
            else:
                kov_index[key] = iri
            kov_iri_to_file[iri] = f
            break    # one act node per peep
    return kov_index, kov_collision_keys, kov_iri_to_file, known_issuer_norms


def _expand_par_range(par_range: str) -> list[str]:
    """Expand '208-210' into ['208', '209', '210']."""
    par_range = par_range.replace("–", "-").replace("‑", "-")
    if "-" in par_range:
        parts = par_range.split("-", 1)
        try:
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            if end - start <= 50:
                return [str(n) for n in range(start, end + 1)]
        except ValueError:
            pass
    clean = re.sub(r"[^\d]", "", par_range)
    return [clean] if clean else []


def extract_citations_from_text(text: str | None) -> tuple[list[dict], list[dict]]:
    """Parse text for Estonian legal citation patterns.

    Returns ``(state_citations, kov_citations)``:

    - ``state_citations``: list of ``{"law_ref": <abbrev>, "paragraphs": [<p>, ...]}``
      for state-law citations matching the abbreviation+§ or genitive+§ patterns.
    - ``kov_citations``:   list of ``{"municipality", "body", "date", "num", "raw_text"}``
      for KOV act-form citations matching ``PAT_KOV_ACT``.
    """
    state_citations: list[dict] = []
    kov_citations: list[dict] = []
    if not text:
        return state_citations, kov_citations

    abbrevs = "|".join(
        re.escape(a) for a in sorted(KNOWN_ABBREVIATIONS.keys(), key=len, reverse=True)
    )

    # Pattern 1: Abbreviation + § + number(s)
    pat_abbrev = re.compile(
        rf"({abbrevs})\s*{PAR_SUFFIX}\s*(\d+(?:\s*[\-–]\s*\d+)?)",
        re.UNICODE,
    )
    for m in pat_abbrev.finditer(text):
        abbrev = m.group(1)
        par_range = m.group(2).strip()
        paragraphs = _expand_par_range(par_range)
        if paragraphs:
            state_citations.append({"law_ref": abbrev, "paragraphs": paragraphs})

    # Pattern 2: Full name in genitive + § + number
    genitive_names = "|".join(
        re.escape(g) for g in sorted(FULLNAME_GENITIVE.keys(), key=len, reverse=True)
    )
    if genitive_names:
        pat_fullname = re.compile(
            rf"({genitive_names})\s*{PAR_SUFFIX}\s*(\d+(?:\s*[\-–]\s*\d+)?)",
            re.UNICODE | re.IGNORECASE,
        )
        for m in pat_fullname.finditer(text):
            gen_name = m.group(1).lower()
            par_range = m.group(2).strip()
            paragraphs = _expand_par_range(par_range)
            abbrev = FULLNAME_GENITIVE.get(gen_name)
            if abbrev and paragraphs:
                state_citations.append({"law_ref": abbrev, "paragraphs": paragraphs})

    # Pattern 3: KOV act-level citation (Layer 2c PR #3)
    for m in PAT_KOV_ACT.finditer(text):
        kov_citations.append({
            "municipality": m.group("municipality"),
            "body":         m.group("body"),
            "date":         m.group("date"),
            "num":          m.group("num"),
            "raw_text":     m.group(0),
        })

    return state_citations, kov_citations


def resolve_citations(
    citations: list[dict],
    abbrev_to_prefix: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
) -> list[str]:
    """Resolve citations to existing provision IRIs."""
    resolved: list[str] = []
    for cit in citations:
        prefix = abbrev_to_prefix.get(cit["law_ref"])
        if not prefix:
            continue
        provisions = prefix_to_provisions.get(prefix, {})
        if not provisions:
            continue
        for par_num in cit["paragraphs"]:
            if par_num in provisions:
                resolved.append(provisions[par_num])
            else:
                stripped = par_num.lstrip("0") or "0"
                if stripped in provisions:
                    resolved.append(provisions[stripped])
    return list(dict.fromkeys(resolved))  # deduplicate


def process_court_files(
    abbrev_to_prefix: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
) -> tuple[list[dict], dict[str, list[str]]]:
    """
    Process all riigikohus_YYYY_peep.json files.

    Returns:
      - per_file_stats: list of stat dicts
      - interpreted_by: {provision_iri: [court_decision_iri, ...]}
    """
    per_file_stats: list[dict] = []
    interpreted_by: dict[str, list[str]] = defaultdict(list)

    rk_files = sorted(RK_DIR.glob("riigikohus_*_peep.json"))
    if not rk_files:
        print("  No riigikohus files found!")
        return per_file_stats, dict(interpreted_by)

    for rk_file in rk_files:
        stats = {
            "file": rk_file.name,
            "decisions_scanned": 0,
            "decisions_with_citations": 0,
            "citations_found": 0,
            "citations_resolved": 0,
        }

        try:
            with open(rk_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            stats["error"] = str(e)
            per_file_stats.append(stats)
            continue

        graph = doc.get("@graph", [])
        modified = False

        for node in graph:
            node_id = node.get("@id", "")
            node_types = node.get("@type", [])

            # Only process CourtDecision nodes
            if "estleg:CourtDecision" not in node_types:
                continue

            stats["decisions_scanned"] += 1

            # Get summary text to parse
            summary = node.get("estleg:summary", "")
            if not summary:
                continue

            # Extract citations
            citations = extract_citations_from_text(summary)
            if not citations:
                continue

            stats["citations_found"] += len(citations)
            stats["decisions_with_citations"] += 1

            # Resolve to provision IRIs
            resolved = resolve_citations(
                citations, abbrev_to_prefix, prefix_to_provisions
            )
            stats["citations_resolved"] += len(resolved)

            if not resolved:
                continue

            # Remove any existing estleg:interpretsLaw (idempotent)
            # Add resolved IRIs as estleg:interpretsLaw
            ref_iris = [{"@id": r} for r in resolved]
            node["estleg:interpretsLaw"] = ref_iris

            modified = True

            # Record inverse links for interpreted_by
            for prov_iri in resolved:
                interpreted_by[prov_iri].append(node_id)

        if modified:
            save_json(rk_file, doc)

        per_file_stats.append(stats)

    return per_file_stats, dict(interpreted_by)


def apply_interpreted_by(
    interpreted_by: dict[str, list[str]],
    iri_to_file: dict[str, Path],
) -> dict[str, int]:
    """
    Apply estleg:interpretedBy inverse links to provision files.

    Returns: {filename: nodes_updated}
    """
    # First clear existing estleg:interpretedBy for idempotent re-run
    files_to_update: dict[Path, dict[str, list[str]]] = defaultdict(dict)

    unresolved = 0
    for prov_iri, decision_iris in interpreted_by.items():
        target_file = iri_to_file.get(prov_iri)
        if target_file is None:
            unresolved += 1
            continue
        unique_decisions = list(dict.fromkeys(decision_iris))
        files_to_update[target_file][prov_iri] = unique_decisions

    if unresolved > 0:
        print(f"  Warning: {unresolved} provision IRIs not found in any file")

    update_counts: dict[str, int] = {}

    for target_file, iri_decisions in sorted(files_to_update.items()):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Error reading {target_file.name}: {e}")
            continue

        modified = False
        nodes_updated = 0

        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")

            if node_id in iri_decisions:
                decisions = iri_decisions[node_id]
                ref_iris = [{"@id": d} for d in decisions]
                node["estleg:interpretedBy"] = ref_iris
                nodes_updated += 1
                modified = True
            else:
                # Clear stale interpretedBy (idempotent)
                if "estleg:interpretedBy" in node:
                    del node["estleg:interpretedBy"]
                    modified = True

        if modified:
            save_json(target_file, doc)
            update_counts[target_file.name] = nodes_updated

    return update_counts


def clear_existing_court_links() -> int:
    """
    Clear existing estleg:interpretsLaw from court files and
    estleg:interpretedBy from law files for idempotent re-run.
    """
    cleaned = 0

    # Clear interpretsLaw from court files
    if RK_DIR.exists():
        for rk_file in sorted(RK_DIR.glob("riigikohus_*_peep.json")):
            try:
                with open(rk_file, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            modified = False
            for node in doc.get("@graph", []):
                if "estleg:interpretsLaw" in node:
                    del node["estleg:interpretsLaw"]
                    modified = True

            if modified:
                save_json(rk_file, doc)
                cleaned += 1

    # Clear interpretedBy from law files
    for law_file in iter_peep_files(include_kov=False):  # DEFERRED to Layer 2c
        if law_file.name.startswith("riigikohus"):
            continue
        try:
            with open(law_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        modified = False
        for node in doc.get("@graph", []):
            if "estleg:interpretedBy" in node:
                del node["estleg:interpretedBy"]
                modified = True

        if modified:
            save_json(law_file, doc)
            cleaned += 1

    return cleaned


def main() -> None:
    print("=" * 70)
    print("Estonian Legal Ontology - Extract Court Decision Provision Links")
    print("=" * 70)

    # Step 1: Clear existing links for idempotent re-run
    print("\n[1/5] Clearing existing court-provision links...")
    cleaned = clear_existing_court_links()
    print(f"  Cleaned {cleaned} files")

    # Step 2: Build provision index
    print("\n[2/5] Building provision index from law JSON-LD files...")
    prefix_to_provisions, source_act_to_prefix, iri_to_file = build_provision_index()
    total_provisions = sum(len(v) for v in prefix_to_provisions.values())
    print(f"  Found {len(prefix_to_provisions)} law prefixes with {total_provisions} provisions")

    # Step 3: Build abbreviation mapping
    print("\n[3/5] Building abbreviation-to-prefix mapping...")
    abbrev_to_prefix = build_abbreviation_to_prefix(source_act_to_prefix)
    print(f"  Mapped {len(abbrev_to_prefix)} abbreviations to IRI prefixes")
    for abbrev, prefix in sorted(abbrev_to_prefix.items()):
        prov_count = len(prefix_to_provisions.get(prefix, {}))
        print(f"    {abbrev} -> {prefix} ({prov_count} provisions)")

    # Step 4: Process court decision files
    print("\n[4/5] Processing court decision files...")
    per_file_stats, interpreted_by = process_court_files(
        abbrev_to_prefix, prefix_to_provisions
    )

    total_decisions = sum(s.get("decisions_scanned", 0) for s in per_file_stats)
    total_with_citations = sum(s.get("decisions_with_citations", 0) for s in per_file_stats)
    total_citations = sum(s.get("citations_found", 0) for s in per_file_stats)
    total_resolved = sum(s.get("citations_resolved", 0) for s in per_file_stats)

    for s in per_file_stats:
        resolved = s.get("citations_resolved", 0)
        if resolved > 0:
            print(f"  {s['file']}: {s['decisions_scanned']} decisions, "
                  f"{s['decisions_with_citations']} with citations, "
                  f"{resolved} resolved")

    # Step 5: Apply inverse links (interpretedBy) on provision files
    print("\n[5/5] Applying estleg:interpretedBy to provision files...")
    total_interpreted_provisions = len(interpreted_by)
    total_inverse_edges = sum(len(v) for v in interpreted_by.values())
    print(f"  {total_interpreted_provisions} provisions referenced by court decisions")
    print(f"  {total_inverse_edges} total court-provision inverse links")

    update_counts = apply_interpreted_by(interpreted_by, iri_to_file)
    print(f"  Updated {len(update_counts)} law files with interpretedBy links")

    # Generate report
    print("\n--- Generating report ---")
    report = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "court_files_processed": len(per_file_stats),
            "total_decisions_scanned": total_decisions,
            "decisions_with_citations": total_with_citations,
            "total_citations_found": total_citations,
            "total_citations_resolved": total_resolved,
            "provisions_interpreted": total_interpreted_provisions,
            "inverse_link_edges": total_inverse_edges,
            "law_files_updated": len(update_counts),
            "abbreviations_mapped": len(abbrev_to_prefix),
        },
        "abbreviation_mapping": {
            abbrev: {
                "iri_prefix": prefix,
                "provision_count": len(prefix_to_provisions.get(prefix, {})),
            }
            for abbrev, prefix in sorted(abbrev_to_prefix.items())
        },
        "top_interpreted_provisions": sorted(
            [
                {
                    "provision_iri": iri,
                    "court_decision_count": len(decisions),
                }
                for iri, decisions in interpreted_by.items()
            ],
            key=lambda x: x["court_decision_count"],
            reverse=True,
        )[:100],
        "per_file_stats": per_file_stats,
    }

    report_path = KRR_DIR / "court_provision_links_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Court files processed:        {len(per_file_stats)}")
    print(f"  Decisions scanned:            {total_decisions}")
    print(f"  Decisions with citations:     {total_with_citations}")
    print(f"  Citations found:              {total_citations}")
    print(f"  Citations resolved to IRIs:   {total_resolved}")
    print(f"  Provisions with interpretedBy: {total_interpreted_provisions}")
    print(f"  Law files updated:            {len(update_counts)}")

    if interpreted_by:
        top_prov = max(interpreted_by.items(), key=lambda x: len(x[1]))
        print(f"  Most interpreted provision:   {top_prov[0]} ({len(top_prov[1])} decisions)")

    print(f"  Report: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
