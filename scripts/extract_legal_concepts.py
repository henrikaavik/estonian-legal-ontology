#!/usr/bin/env python3
"""
Extract defined legal terms from Estonian laws and build a concept cross-reference graph.

This script:
1. Loads all law JSON-LD files and their source XML from data/riigiteataja/
2. Detects definition sections ("Mõisted", "Põhimõisted", etc.)
3. Extracts term-definition pairs from numbered definition paragraphs
4. Creates estleg:LegalConcept nodes with SKOS labels and definitions
5. Matches identical/similar terms across laws with skos:exactMatch / skos:closeMatch
6. Outputs krr_outputs/concepts/ with combined and cross-reference data
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"
CONCEPTS_DIR = KRR_DIR / "concepts"
CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)

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

# Definition section titles to look for (case-insensitive matching)
DEFINITION_TITLES = {
    "mõisted",
    "seaduses kasutatavad mõisted",
    "põhimõisted",
    "mõistete selgitus",
    "terminid",
    "terminid ja mõisted",
    "mõisted ja lühendid",
}

# Text patterns that introduce definitions within a paragraph
DEFINITION_INTRO_PATTERNS = [
    r"käesolevas\s+seaduses\s+kasutatakse\s+järgmises\s+tähenduses",
    r"käesoleva\s+seaduse\s+tähenduses",
    r"käesolevas\s+seaduses\s+tähendab",
    r"käesolevas\s+seaduses\s+mõistetakse",
    r"käesolevas\s+seadustikus\s+kasutatakse",
    r"käesoleva\s+seadustiku\s+tähenduses",
    r"käesoleva\s+seaduse\s+mõistes",
]

# Pattern for extracting numbered definitions: "1) term – definition;"
DEFINITION_PATTERN = re.compile(
    r"(\d+)\)\s*"                     # number and closing paren
    r"([\wäöüõšžÄÖÜÕŠŽ]+(?:\s+[\wäöüõšžÄÖÜÕŠŽ-]+)*?)"  # term (one or more words)
    r"\s*[–\-—]\s*"                   # dash separator
    r"(.+?)(?:;|$)",                  # definition text until semicolon or end
    re.UNICODE,
)


def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    """Get child element text by local tag name."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


def collect_full_text(el: ET.Element) -> str:
    """Collect all text content from an element and its children."""
    text = " ".join(el.itertext())
    return re.sub(r"\s+", " ", text).strip()


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    """Create a safe ID from a string."""
    s = value.replace(" ", "_").replace("-", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s[:80] or "Unknown"


def slugify(text: str) -> str:
    """Convert Estonian text to a filename-safe slug."""
    replacements = {
        "ä": "a", "ö": "o", "ü": "u", "õ": "o",
        "Ä": "A", "Ö": "O", "Ü": "U", "Õ": "O",
        "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:80]


def edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return edit_distance(b, a)
    if len(b) == 0:
        return len(a)

    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr_row = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr_row.append(min(
                curr_row[j] + 1,       # insertion
                prev_row[j + 1] + 1,   # deletion
                prev_row[j] + cost,    # substitution
            ))
        prev_row = curr_row
    return prev_row[len(b)]


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def is_definition_paragraph(par_el: ET.Element) -> bool:
    """Check if a paragraph is a definition section based on its title."""
    title = ct(par_el, "paragrahvPealkiri")
    if title:
        title_lower = title.lower().strip()
        for def_title in DEFINITION_TITLES:
            if def_title in title_lower:
                return True
    return False


def has_definition_intro(text: str) -> bool:
    """Check if text contains a definition introduction pattern."""
    text_lower = text.lower()
    for pattern in DEFINITION_INTRO_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def extract_definitions_from_text(text: str) -> list[tuple[str, str, str]]:
    """
    Extract term-definition pairs from text.
    Returns list of (number, term, definition).
    """
    results = []
    for match in DEFINITION_PATTERN.finditer(text):
        number = match.group(1)
        term = match.group(2).strip()
        definition = match.group(3).strip()

        # Skip overly short terms or definitions
        if len(term) < 2 or len(definition) < 5:
            continue
        # Skip if term looks like a number or single letter
        if re.match(r"^\d+$", term):
            continue

        results.append((number, term, definition))

    return results


def extract_concepts_from_xml(xml_path: Path, law_title: str, law_slug: str) -> list[dict]:
    """
    Extract legal concept definitions from a law's XML file.
    Returns list of concept dicts.
    """
    concepts = []

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return concepts

    # Find paragraphs that contain definitions
    for el in root.iter():
        if ln(el.tag) != "paragrahv":
            continue

        par_nr = ct(el, "paragrahvNr") or ""
        par_title = ct(el, "paragrahvPealkiri") or ""
        par_display = ct(el, "kuvatavNr") or f"§ {par_nr}"

        # Check if this paragraph is a definition section
        is_def_section = is_definition_paragraph(el)
        full_text = collect_full_text(el)

        if not is_def_section:
            # Also check if the text contains definition intro patterns
            if not has_definition_intro(full_text):
                continue

        # Extract definitions from the paragraph text
        defs = extract_definitions_from_text(full_text)

        for number, term, definition in defs:
            concept_id = f"estleg:Concept_{sanitize_id(law_slug)}_{sanitize_id(term)}"
            provision_id = f"estleg:{sanitize_id(law_slug)}_Par_{sanitize_id(par_nr)}"

            concepts.append({
                "concept_id": concept_id,
                "term": term,
                "term_lower": term.lower(),
                "definition": definition,
                "law_title": law_title,
                "law_slug": law_slug,
                "paragraph": par_display,
                "par_nr": par_nr,
                "provision_id": provision_id,
                "def_number": number,
            })

    return concepts


def load_law_files() -> list[dict]:
    """Load all law JSON-LD files (top-level only) and return their metadata."""
    laws = []
    for f in sorted(KRR_DIR.glob("*_peep.json")):
        if f.parent != KRR_DIR:
            continue
        slug = f.stem.replace("_peep", "")
        try:
            with open(f, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        title = ""
        for node in doc.get("@graph", []):
            types = node.get("@type", [])
            if "owl:Ontology" in types:
                title = node.get("dc:source", node.get("rdfs:label", ""))
                break

        laws.append({
            "slug": slug,
            "title": title,
            "path": f,
        })
    return laws


def main():
    print("=" * 70)
    print("Estonian Legal Ontology - Extract Legal Concepts")
    print("=" * 70)

    # Step 1: Load law files
    print("\n[1/5] Loading law JSON-LD files...")
    laws = load_law_files()
    print(f"  Found {len(laws)} law files")

    # Step 2: Extract concepts from XML
    print("\n[2/5] Extracting defined terms from XML files...")
    all_concepts: list[dict] = []
    laws_with_concepts = 0

    for i, law in enumerate(laws):
        slug = law["slug"]
        title = law["title"]

        # Find XML file — try exact slug, then base slug (without _osa suffix)
        base_slug = re.sub(r"_osa\d+$", "", slug)
        xml_path = DATA_DIR / f"{slug}.xml"
        if not xml_path.exists():
            xml_path = DATA_DIR / f"{base_slug}.xml"
        if not xml_path.exists():
            continue

        concepts = extract_concepts_from_xml(xml_path, title, slug)
        if concepts:
            all_concepts.extend(concepts)
            laws_with_concepts += 1
            if len(concepts) >= 3:
                print(f"  {title}: {len(concepts)} defined terms")

    print(f"\n  Total: {len(all_concepts)} defined terms from {laws_with_concepts} laws")

    # Step 3: Build cross-references
    print("\n[3/5] Building cross-reference graph...")

    # Group concepts by lowercased term
    term_index: dict[str, list[dict]] = {}
    for concept in all_concepts:
        key = concept["term_lower"]
        term_index.setdefault(key, []).append(concept)

    # Find terms that appear in multiple laws (exact matches)
    exact_matches: list[dict] = []
    for term_lower, entries in sorted(term_index.items()):
        law_slugs = list({e["law_slug"] for e in entries})
        if len(law_slugs) > 1:
            exact_matches.append({
                "term": entries[0]["term"],
                "term_lower": term_lower,
                "laws": [
                    {"title": e["law_title"], "slug": e["law_slug"], "paragraph": e["paragraph"]}
                    for e in entries
                ],
                "count": len(law_slugs),
            })

    print(f"  Exact matches (same term in multiple laws): {len(exact_matches)}")

    # Find close matches (edit distance < 3) between unique terms
    unique_terms = list(term_index.keys())
    close_matches: list[dict] = []

    # Only compare if manageable number of terms
    if len(unique_terms) <= 5000:
        for i in range(len(unique_terms)):
            for j in range(i + 1, len(unique_terms)):
                t1 = unique_terms[i]
                t2 = unique_terms[j]
                # Quick length check to skip obviously different terms
                if abs(len(t1) - len(t2)) >= 3:
                    continue
                # Skip very short terms for close matching
                if len(t1) < 4 or len(t2) < 4:
                    continue
                dist = edit_distance(t1, t2)
                if 0 < dist < 3:
                    close_matches.append({
                        "term_a": term_index[t1][0]["term"],
                        "term_b": term_index[t2][0]["term"],
                        "distance": dist,
                        "laws_a": list({e["law_slug"] for e in term_index[t1]}),
                        "laws_b": list({e["law_slug"] for e in term_index[t2]}),
                    })
    else:
        print(f"  Skipping close-match computation ({len(unique_terms)} unique terms too many)")

    print(f"  Close matches (edit distance < 3): {len(close_matches)}")

    # Step 4: Generate JSON-LD output
    print("\n[4/5] Generating JSON-LD concept files...")

    # Build @graph for combined file
    graph: list[dict] = [
        {
            "@id": "estleg:LegalConcepts_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "Eesti õiguse mõisted (Estonian Legal Concepts)",
            "dc:description": "Seaduses defineeritud õigusmõisted ja nende ristviited.",
            "dc:source": "Riigi Teataja XML",
            "estleg:totalConcepts": {
                "@value": str(len(all_concepts)),
                "@type": "xsd:integer",
            },
        },
        # LegalConcept class definition
        {
            "@id": "estleg:LegalConcept",
            "@type": ["owl:Class"],
            "rdfs:label": "Õigusmõiste (Legal Concept)",
            "rdfs:comment": "Seaduses defineeritud mõiste.",
        },
        # definesTerm property
        {
            "@id": "estleg:definesTerm",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "defineerib mõiste",
            "rdfs:comment": "Links a legal provision to a concept it defines.",
        },
        # definedIn property
        {
            "@id": "estleg:definedIn",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "defineeritud aktis",
            "rdfs:comment": "Links a concept to the provision where it is defined.",
            "owl:inverseOf": {"@id": "estleg:definesTerm"},
        },
    ]

    # Track concept IDs to avoid duplicates in graph
    seen_concept_ids: set[str] = set()

    for concept in all_concepts:
        cid = concept["concept_id"]
        if cid in seen_concept_ids:
            # Disambiguate by appending def number
            cid = f"{cid}_{concept['def_number']}"
            if cid in seen_concept_ids:
                continue
        seen_concept_ids.add(cid)

        node: dict = {
            "@id": cid,
            "@type": ["owl:NamedIndividual", "estleg:LegalConcept"],
            "skos:prefLabel": concept["term"],
            "skos:definition": concept["definition"],
            "estleg:definedIn": {"@id": concept["provision_id"]},
            "estleg:sourceAct": concept["law_title"],
            "rdfs:label": f"{concept['term']} ({concept['law_slug']})",
        }
        graph.append(node)

    # Build a lookup from @id to graph node for in-place updates
    graph_node_by_id: dict[str, dict] = {}
    for node in graph:
        nid = node.get("@id", "")
        if nid:
            graph_node_by_id[nid] = node

    # Add skos:exactMatch links for terms appearing in multiple laws
    for em in exact_matches:
        entries = term_index[em["term_lower"]]
        concept_ids = []
        for e in entries:
            cid = e["concept_id"]
            if cid not in seen_concept_ids:
                cid = f"{cid}_{e['def_number']}"
            concept_ids.append(cid)

        # Link each pair with skos:exactMatch — merge into existing nodes
        for i in range(len(concept_ids)):
            for j in range(i + 1, len(concept_ids)):
                target_node = graph_node_by_id.get(concept_ids[i])
                if target_node is not None:
                    existing = target_node.get("skos:exactMatch", [])
                    if isinstance(existing, dict):
                        existing = [existing]
                    existing.append({"@id": concept_ids[j]})
                    target_node["skos:exactMatch"] = existing

    # Add skos:closeMatch links — merge into existing nodes
    for cm in close_matches:
        t_a = cm["term_a"].lower()
        t_b = cm["term_b"].lower()
        entries_a = term_index.get(t_a, [])
        entries_b = term_index.get(t_b, [])
        if entries_a and entries_b:
            id_a = entries_a[0]["concept_id"]
            id_b = entries_b[0]["concept_id"]
            target_node = graph_node_by_id.get(id_a)
            if target_node is not None:
                existing = target_node.get("skos:closeMatch", [])
                if isinstance(existing, dict):
                    existing = [existing]
                existing.append({"@id": id_b})
                target_node["skos:closeMatch"] = existing

    # Save combined concepts file
    combined_doc = {"@context": CONTEXT, "@graph": graph}
    combined_path = CONCEPTS_DIR / "concepts_combined.jsonld"
    save_json(combined_path, combined_doc)
    print(f"  Saved: {combined_path.name} ({len(graph)} nodes)")

    # Step 5: Generate cross-reference report
    print("\n[5/5] Generating concept_crossref_report.json...")

    # Statistics by law
    concepts_per_law: dict[str, int] = {}
    for c in all_concepts:
        concepts_per_law[c["law_slug"]] = concepts_per_law.get(c["law_slug"], 0) + 1
    laws_ranked = sorted(concepts_per_law.items(), key=lambda x: x[1], reverse=True)

    report = {
        "generated": date.today().isoformat(),
        "summary": {
            "total_laws_analyzed": len(laws),
            "laws_with_definitions": laws_with_concepts,
            "total_defined_terms": len(all_concepts),
            "unique_terms": len(unique_terms),
            "terms_in_multiple_laws": len(exact_matches),
            "close_match_pairs": len(close_matches),
        },
        "top_laws_by_definitions": [
            {"slug": slug, "count": count}
            for slug, count in laws_ranked[:30]
        ],
        "cross_referenced_terms": [
            {
                "term": em["term"],
                "appears_in": em["count"],
                "laws": [
                    {"title": l["title"], "paragraph": l["paragraph"]}
                    for l in em["laws"]
                ],
            }
            for em in sorted(exact_matches, key=lambda x: x["count"], reverse=True)
        ],
        "close_matches": [
            {
                "term_a": cm["term_a"],
                "term_b": cm["term_b"],
                "edit_distance": cm["distance"],
            }
            for cm in close_matches[:100]  # Limit to top 100
        ],
    }

    report_path = CONCEPTS_DIR / "concept_crossref_report.json"
    save_json(report_path, report)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Laws analyzed:              {len(laws)}")
    print(f"  Laws with definitions:      {laws_with_concepts}")
    print(f"  Total defined terms:        {len(all_concepts)}")
    print(f"  Unique terms:               {len(unique_terms)}")
    print(f"  Terms in multiple laws:     {len(exact_matches)}")
    print(f"  Close-match pairs:          {len(close_matches)}")
    if laws_ranked:
        print(f"\n  Top 5 laws by definitions:")
        for slug, count in laws_ranked[:5]:
            print(f"    {slug}: {count} terms")
    if exact_matches:
        top_crossref = sorted(exact_matches, key=lambda x: x["count"], reverse=True)[:5]
        print(f"\n  Top 5 cross-referenced terms:")
        for em in top_crossref:
            print(f"    \"{em['term']}\" appears in {em['count']} laws")
    print(f"\n  Output: {CONCEPTS_DIR.relative_to(REPO_ROOT)}/")
    print(f"  Combined: concepts_combined.jsonld")
    print(f"  Report:   concept_crossref_report.json")
    print("=" * 70)


if __name__ == "__main__":
    main()
