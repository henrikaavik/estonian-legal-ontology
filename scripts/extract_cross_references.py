#!/usr/bin/env python3
"""
Extract cross-law references from Estonian law XML and JSON-LD files.

This script:
1. Builds a mapping of law abbreviations and full names to ontology provision IRIs
2. Parses XML source files and JSON-LD summary text for Estonian legal citation patterns
3. Resolves citations to existing provision IRIs
4. Adds estleg:references IRI links to provision nodes in each JSON-LD file
5. Generates cross_references_report.json with statistics

Citation patterns detected:
  - KarS § 121            (abbreviation + paragraph)
  - KarS § 121 lg 2       (with subsection)
  - KarS § 121 lg 2 p 3   (with point)
  - VÕS §-de 208-210      (paragraph range)
  - käesoleva seaduse § 12 (self-reference)
  - tsiviilseadustiku üldosa seaduse § 67 (full name reference)
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from estleg_common import (
    CONTEXT,
    FULLNAME_GENITIVE,
    KNOWN_ABBREVIATIONS,
    PAR_SUFFIX,
    save_json,
    sanitize_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"

NS = "https://data.riik.ee/ontology/estleg#"

# Full law name -> abbreviation (reverse mapping)
FULLNAME_TO_ABBREV = {v.lower(): k for k, v in KNOWN_ABBREVIATIONS.items()}


def ln(tag: str) -> str:
    """Extract local name from a possibly namespaced XML tag."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def build_provision_index() -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, Path]]:
    """
    Scan all *_peep.json files to build:
    1. prefix_to_provisions: {prefix: {par_number: full_iri}} e.g. {"Karistusseadustik": {"121": "estleg:KARIST_2_Par_121"}}
    2. source_act_to_prefix: {source_act_name: prefix} e.g. {"Karistusseadustik": "Karistusseadustik"}
    3. iri_to_file: {iri: filepath} mapping each provision IRI to its containing file
    """
    prefix_to_provisions: dict[str, dict[str, str]] = {}
    source_act_to_prefix: dict[str, str] = {}
    iri_to_file: dict[str, Path] = {}

    # Scan all JSON-LD law files (not riigikohus, not schema/index files)
    for json_file in sorted(KRR_DIR.glob("*_peep.json")):
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

        # Detect the prefix used in this file by scanning provision nodes
        file_prefix = None
        source_act = None
        for node in graph:
            node_id = node.get("@id", "")
            if "_Par_" in node_id and node_id.startswith("estleg:"):
                # Extract prefix: "estleg:KARIST_2_Par_121" -> "Karistusseadustik"
                local = node_id[len("estleg:"):]
                prefix_part = local.split("_Par_")[0]
                if file_prefix is None:
                    file_prefix = prefix_part
                # Extract paragraph number
                par_part = local.split("_Par_")[1]
                # Store the provision
                if file_prefix not in prefix_to_provisions:
                    prefix_to_provisions[file_prefix] = {}
                prefix_to_provisions[file_prefix][par_part] = node_id
                iri_to_file[node_id] = json_file

                # Get sourceAct
                if source_act is None:
                    source_act = node.get("estleg:sourceAct", "")

        if file_prefix and source_act:
            source_act_to_prefix[source_act] = file_prefix

    # Also scan riigikohus subdirectory files (but those don't have provisions)
    for json_file in sorted((KRR_DIR / "riigikohus").glob("*_peep.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if "_Par_" in node_id:
                iri_to_file[node_id] = json_file

    return prefix_to_provisions, source_act_to_prefix, iri_to_file


def build_abbreviation_to_prefix(
    source_act_to_prefix: dict[str, str],
) -> dict[str, str]:
    """
    Map law abbreviations (KarS, VÕS, etc.) to the prefix used in provision IRIs.

    Returns: {abbreviation: iri_prefix} e.g. {"KarS": "Karistusseadustik"}
    """
    abbrev_to_prefix: dict[str, str] = {}

    for abbrev, full_name in KNOWN_ABBREVIATIONS.items():
        if full_name in source_act_to_prefix:
            abbrev_to_prefix[abbrev] = source_act_to_prefix[full_name]

    return abbrev_to_prefix


def extract_citations_from_text(text: str) -> list[dict]:
    """
    Parse text for Estonian legal citation patterns.

    Returns list of dicts with keys:
      - law_ref: abbreviation or full name reference
      - paragraphs: list of paragraph numbers (strings)
      - is_self_ref: True if "käesoleva seaduse" pattern
    """
    citations: list[dict] = []
    if not text:
        return citations

    # Known abbreviation list for regex alternation
    abbrevs = "|".join(re.escape(a) for a in sorted(KNOWN_ABBREVIATIONS.keys(), key=len, reverse=True))

    # Pattern 1: Abbreviation + § + number(s)
    # KarS § 121, KarS §-s 121, KarS § 121 lg 2 p 3
    # Also handles: KarS §-de 208-210, KarS §§ 1-10
    pat_abbrev = re.compile(
        rf"({abbrevs})\s*{PAR_SUFFIX}\s*(\d+(?:\s*[\-–]\s*\d+)?)",
        re.UNICODE,
    )

    for m in pat_abbrev.finditer(text):
        abbrev = m.group(1)
        par_range = m.group(2).strip()
        paragraphs = _expand_par_range(par_range)
        citations.append({
            "law_ref": abbrev,
            "paragraphs": paragraphs,
            "is_self_ref": False,
        })

    # Pattern 2: käesoleva seaduse § N (self-reference)
    pat_self = re.compile(
        rf"k[äa]esoleva\s+seadus(?:e|tiku)?\s*{PAR_SUFFIX}\s*(\d+(?:\s*[\-–]\s*\d+)?)",
        re.UNICODE | re.IGNORECASE,
    )
    for m in pat_self.finditer(text):
        par_range = m.group(1).strip()
        paragraphs = _expand_par_range(par_range)
        citations.append({
            "law_ref": "__SELF__",
            "paragraphs": paragraphs,
            "is_self_ref": True,
        })

    # Pattern 3: Full name in genitive form + § + number
    # "tsiviilseadustiku üldosa seaduse § 67"
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
            if abbrev:
                citations.append({
                    "law_ref": abbrev,
                    "paragraphs": paragraphs,
                    "is_self_ref": False,
                })

    return citations


def _expand_par_range(par_range: str) -> list[str]:
    """Expand '208-210' into ['208', '209', '210']. Single numbers return as-is."""
    par_range = par_range.replace("–", "-").replace("‑", "-")
    if "-" in par_range:
        parts = par_range.split("-", 1)
        try:
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            if end - start <= 50:  # sanity limit
                return [str(n) for n in range(start, end + 1)]
        except ValueError:
            pass
    # Single number or failed range
    clean = re.sub(r"[^\d]", "", par_range)
    return [clean] if clean else []


def resolve_citation(
    citation: dict,
    self_prefix: str,
    abbrev_to_prefix: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
) -> list[str]:
    """
    Resolve a citation dict to a list of existing provision IRIs.

    Returns list of IRI strings (e.g. ["estleg:KARIST_2_Par_121"]).
    """
    resolved: list[str] = []

    # Determine the IRI prefix for this law reference
    if citation["is_self_ref"]:
        prefix = self_prefix
    else:
        law_ref = citation["law_ref"]
        prefix = abbrev_to_prefix.get(law_ref)
        if not prefix:
            return []

    provisions = prefix_to_provisions.get(prefix, {})
    if not provisions:
        return []

    for par_num in citation["paragraphs"]:
        # Try exact match first
        if par_num in provisions:
            resolved.append(provisions[par_num])
        else:
            # Try stripping leading zeros
            stripped = par_num.lstrip("0") or "0"
            if stripped in provisions:
                resolved.append(provisions[stripped])

    return resolved


def collect_text_from_xml(xml_path: Path) -> dict[str, str]:
    """
    Parse a Riigi Teataja XML and extract text content per paragraph.

    Returns: {paragraph_number: full_text}
    """
    par_texts: dict[str, str] = {}
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return par_texts

    for el in root.iter():
        if ln(el.tag) == "paragrahv":
            # Find paragraph number
            par_nr = None
            for child in el:
                if ln(child.tag) == "paragrahvNr" and child.text:
                    par_nr = child.text.strip()
                    break
            if not par_nr:
                continue
            # Collect all text within this paragraph
            full_text = " ".join(el.itertext())
            full_text = re.sub(r"\s+", " ", full_text).strip()
            par_nr_clean = re.sub(r"[^\d]", "", par_nr)
            if par_nr_clean:
                par_texts[par_nr_clean] = full_text

    return par_texts


def process_law_file(
    json_file: Path,
    abbrev_to_prefix: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
    source_act_to_prefix: dict[str, str],
) -> dict:
    """
    Process a single law JSON-LD file to extract and add cross-references.

    Returns statistics dict.
    """
    stats = {
        "file": json_file.name,
        "provisions_scanned": 0,
        "citations_found": 0,
        "citations_resolved": 0,
        "provisions_with_refs": 0,
    }

    try:
        with open(json_file, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        stats["error"] = str(e)
        return stats

    graph = doc.get("@graph", [])
    if not graph:
        return stats

    # Determine this file's prefix and source act for self-references
    self_prefix = None
    self_source_act = None
    for node in graph:
        node_id = node.get("@id", "")
        if "_Par_" in node_id and node_id.startswith("estleg:"):
            local = node_id[len("estleg:"):]
            self_prefix = local.split("_Par_")[0]
            self_source_act = node.get("estleg:sourceAct", "")
            break

    if not self_prefix:
        return stats

    # Try to find corresponding XML file for richer text
    # The JSON-LD filename pattern is {slug}_peep.json or {slug}_osa{N}_peep.json
    slug = json_file.stem.replace("_peep", "")
    # Remove _osa{N} suffix for XML lookup
    xml_slug = re.sub(r"_osa\d+$", "", slug)
    xml_path = DATA_DIR / f"{xml_slug}.xml"
    xml_par_texts: dict[str, str] = {}
    if xml_path.exists():
        xml_par_texts = collect_text_from_xml(xml_path)

    modified = False
    for node in graph:
        node_id = node.get("@id", "")
        if "_Par_" not in node_id:
            continue

        stats["provisions_scanned"] += 1

        # Get text to scan: prefer XML text, fall back to JSON-LD summary
        local = node_id[len("estleg:"):]
        par_num = local.split("_Par_")[1]
        text_to_scan = xml_par_texts.get(par_num, "")
        summary = node.get("estleg:summary", "")
        if summary:
            text_to_scan = text_to_scan + " " + summary if text_to_scan else summary

        if not text_to_scan:
            continue

        # Extract and resolve citations
        citations = extract_citations_from_text(text_to_scan)
        if not citations:
            continue

        # Count individual paragraph references (not citation objects) so
        # that total_citations_found >= total_citations_resolved always holds.
        stats["citations_found"] += sum(len(c["paragraphs"]) for c in citations)

        all_refs: list[str] = []
        for cit in citations:
            resolved = resolve_citation(
                cit, self_prefix, abbrev_to_prefix, prefix_to_provisions
            )
            all_refs.extend(resolved)
            stats["citations_resolved"] += len(resolved)

        # Deduplicate and remove self-links
        all_refs = list(dict.fromkeys(r for r in all_refs if r != node_id))

        if not all_refs:
            continue

        # Add estleg:references (using IRI references, always as list)
        ref_iris = [{"@id": r} for r in all_refs]
        node["estleg:references"] = ref_iris

        stats["provisions_with_refs"] += 1
        modified = True

    if modified:
        save_json(json_file, doc)

    return stats


def clear_existing_references() -> int:
    """
    Remove estleg:references from all provision nodes in *_peep.json files
    so the script is idempotent on re-run.
    """
    cleaned = 0
    for json_file in sorted(KRR_DIR.glob("*_peep.json")):
        if json_file.name.startswith("riigikohus"):
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        modified = False
        for node in doc.get("@graph", []):
            if "estleg:references" in node:
                del node["estleg:references"]
                modified = True

        if modified:
            save_json(json_file, doc)
            cleaned += 1

    return cleaned


def main() -> None:
    print("=" * 70)
    print("Estonian Legal Ontology - Extract Cross-Law References")
    print("=" * 70)

    # Step 1: Clear existing references for idempotent re-run
    print("\n[1/5] Clearing existing estleg:references from law files...")
    cleaned = clear_existing_references()
    print(f"  Cleaned {cleaned} files")

    # Step 2: Build provision index
    print("\n[2/5] Building provision index from JSON-LD files...")
    prefix_to_provisions, source_act_to_prefix, iri_to_file = build_provision_index()
    total_provisions = sum(len(v) for v in prefix_to_provisions.values())
    print(f"  Found {len(prefix_to_provisions)} law prefixes with {total_provisions} provisions")
    print(f"  Source act mappings: {len(source_act_to_prefix)}")

    # Step 3: Build abbreviation mapping
    print("\n[3/5] Building abbreviation-to-prefix mapping...")
    abbrev_to_prefix = build_abbreviation_to_prefix(source_act_to_prefix)
    print(f"  Mapped {len(abbrev_to_prefix)} abbreviations to IRI prefixes")
    for abbrev, prefix in sorted(abbrev_to_prefix.items()):
        provisions = prefix_to_provisions.get(prefix, {})
        print(f"    {abbrev} -> {prefix} ({len(provisions)} provisions)")

    # Step 4: Process each law file
    print("\n[4/5] Processing law files for cross-references...")
    law_files = sorted(KRR_DIR.glob("*_peep.json"))
    # Exclude riigikohus files and non-law files
    law_files = [f for f in law_files if not f.name.startswith("riigikohus")]

    all_stats: list[dict] = []
    total_citations = 0
    total_resolved = 0
    total_with_refs = 0
    files_modified = 0

    for i, json_file in enumerate(law_files, 1):
        stats = process_law_file(
            json_file, abbrev_to_prefix, prefix_to_provisions, source_act_to_prefix
        )
        all_stats.append(stats)

        # Accumulate totals unconditionally (#81: include every file)
        total_citations += stats.get("citations_found", 0)
        total_resolved += stats.get("citations_resolved", 0)
        total_with_refs += stats.get("provisions_with_refs", 0)
        if stats.get("provisions_with_refs", 0) > 0:
            files_modified += 1

        if i % 50 == 0 or i == len(law_files):
            print(f"  Processed {i}/{len(law_files)} files "
                  f"(refs found so far: {total_resolved})")

    # Step 5: Generate report
    print("\n[5/5] Generating cross-references report...")
    total_unresolved = total_citations - total_resolved
    report = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total_law_files": len(law_files),
            "files_with_references": files_modified,
            "total_citations_found": total_citations,
            "total_citations_resolved": total_resolved,
            "total_citations_unresolved": total_unresolved,
            "total_provisions_with_refs": total_with_refs,
            "abbreviations_mapped": len(abbrev_to_prefix),
            "law_prefixes_indexed": len(prefix_to_provisions),
            "total_provisions_indexed": total_provisions,
        },
        "abbreviation_mapping": {
            abbrev: {
                "iri_prefix": prefix,
                "provision_count": len(prefix_to_provisions.get(prefix, {})),
            }
            for abbrev, prefix in sorted(abbrev_to_prefix.items())
        },
        "per_file_stats": all_stats,
    }

    report_path = KRR_DIR / "cross_references_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Law files processed:         {len(law_files)}")
    print(f"  Files with references added:  {files_modified}")
    print(f"  Total citations found:        {total_citations}")
    print(f"  Total citations resolved:     {total_resolved}")
    print(f"  Total citations unresolved:   {total_unresolved}")
    print(f"  Provisions with references:   {total_with_refs}")
    print(f"  Report: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
