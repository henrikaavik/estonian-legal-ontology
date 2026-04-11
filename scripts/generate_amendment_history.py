#!/usr/bin/env python3
"""
Build amendment chain relationships between Estonian laws and their amending acts.

This script:
1. Loads all existing law JSON-LD files from krr_outputs/*_peep.json
2. Loads draft legislation from krr_outputs/eelnoud/ for amendment relationships
3. Parses cached law XML files for amendment references (muutmismarge blocks)
4. Adds estleg:amendedBy / estleg:amends / estleg:amendmentDate to JSON-LD
5. Creates krr_outputs/amendments/ directory with amendment chain data
6. Generates amendment_history_report.json
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
AMENDMENTS_DIR = KRR_DIR / "amendments"
AMENDMENTS_DIR.mkdir(parents=True, exist_ok=True)
EELNOUD_DIR = KRR_DIR / "eelnoud"

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


def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    """Get child element text by local tag name."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


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


def parse_date(value: str) -> str | None:
    """Parse date from XML, stripping timezone offsets."""
    if not value:
        return None
    from datetime import datetime
    value = value.strip()
    value = re.sub(r"\+\d{2}:\d{2}", "", value)
    value = re.sub(r"-\d{2}:\d{2}$", "", value)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def make_xsd_date(iso_date: str) -> dict:
    """Create an xsd:date typed value for JSON-LD."""
    return {"@value": iso_date, "@type": "xsd:date"}


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_law_files() -> dict[str, dict]:
    """
    Load all law JSON-LD files.
    Returns dict keyed by slug (stem without _peep).
    """
    laws = {}
    for f in sorted(KRR_DIR.glob("*_peep.json")):
        if f.parent != KRR_DIR:
            continue
        slug = f.stem.replace("_peep", "")
        try:
            with open(f, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            laws[slug] = {
                "path": f,
                "doc": doc,
                "title": extract_title(doc),
            }
        except (json.JSONDecodeError, OSError):
            pass
    return laws


def extract_title(doc: dict) -> str:
    """Extract the law title from the ontology node's dc:source."""
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if "owl:Ontology" in types:
            return node.get("dc:source", node.get("rdfs:label", ""))
    return ""


def build_title_to_slug_map(laws: dict[str, dict]) -> dict[str, str]:
    """Build a mapping from law title (lowercased) to slug for matching."""
    mapping = {}
    for slug, info in laws.items():
        title = info["title"]
        if title:
            mapping[title.lower()] = slug
            # Also map partial name (without "seadus" suffix form variations)
            # e.g., "Alkoholiseadus" -> alkoholiseadus
            mapping[slugify(title)] = slug
    return mapping


def extract_amendments_from_xml(xml_path: Path) -> list[dict]:
    """
    Extract amendment references (muutmismarge blocks) from a law XML file.
    Each muutmismarge contains info about an amending act.
    """
    amendments = []
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return amendments

    for el in root.iter():
        if ln(el.tag) != "muutmismarge":
            continue

        amendment: dict = {
            "date": None,
            "rt_reference": None,
            "entry_into_force": None,
            "akt_viide": None,
        }

        # aktikuupaev — date of the amending act
        val = ct(el, "aktikuupaev")
        if val:
            amendment["date"] = parse_date(val)

        # joustumine — when the amendment entered into force
        val = ct(el, "joustumine")
        if val:
            amendment["entry_into_force"] = parse_date(val)

        # Build RT reference from avaldamismarge child
        avaldamismarge = None
        for child in el:
            if ln(child.tag) == "avaldamismarge":
                avaldamismarge = child
                break

        if avaldamismarge is not None:
            rt_osa = ct(avaldamismarge, "RTosa") or ""
            rt_aasta = ct(avaldamismarge, "RTaasta") or ""
            rt_nr = ct(avaldamismarge, "RTnr") or ""
            rt_artikkel = ct(avaldamismarge, "RTartikkel") or ""
            akt_viide = ct(avaldamismarge, "aktViide")

            if rt_osa or rt_aasta:
                amendment["rt_reference"] = f"{rt_osa}, {rt_aasta}, {rt_nr}, {rt_artikkel}".strip(", ")
            if akt_viide:
                amendment["akt_viide"] = akt_viide

        if amendment["date"] or amendment["rt_reference"]:
            amendments.append(amendment)

    return amendments


def extract_rt_references_from_text(xml_path: Path) -> list[str]:
    """
    Scan law text for inline RT publication references like "RT I, 28.06.2023, 10".
    These often indicate amendment acts referenced in the body text.
    """
    references = []
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return references

    full_text = " ".join(root.itertext())
    # Pattern: RT I, YYYY, NR, ART  or  RT I, DD.MM.YYYY, NR
    pattern = r"RT\s+I{1,2}\s*,\s*(?:\d{2}\.\d{2}\.)?\d{4}\s*,\s*\d+(?:\s*,\s*\d+)?"
    matches = re.findall(pattern, full_text)
    # Deduplicate while preserving order
    seen = set()
    for m in matches:
        normalized = re.sub(r"\s+", " ", m.strip())
        if normalized not in seen:
            seen.add(normalized)
            references.append(normalized)
    return references


def load_draft_amendments() -> list[dict]:
    """
    Load draft legislation that are amendment bills from eelnoud directory.
    Returns list of dicts with draft_id, affected_law_name, etc.
    """
    drafts = []

    # Try the combined file first
    combined_path = EELNOUD_DIR / "eelnoud_combined.jsonld"
    if not combined_path.exists():
        return drafts

    try:
        with open(combined_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError):
        return drafts

    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if "estleg:DraftLegislation" not in types:
            continue

        # Check if it is an amendment bill
        draft_type = node.get("estleg:draftType", {})
        if isinstance(draft_type, dict):
            type_id = draft_type.get("@id", "")
        else:
            type_id = str(draft_type)

        if type_id != "estleg:DraftType_AmendmentBill":
            continue

        affected = node.get("estleg:affectedLawName")
        if not affected:
            continue

        # Normalize to list
        if isinstance(affected, str):
            affected = [affected]

        draft_id = node.get("@id", "")
        label = node.get("rdfs:label", "")
        pub_date = node.get("estleg:publicationDate")
        if isinstance(pub_date, dict):
            pub_date = pub_date.get("@value")

        for law_name in affected:
            drafts.append({
                "draft_id": draft_id,
                "draft_label": label,
                "affected_law_name": law_name,
                "publication_date": pub_date,
            })

    return drafts


def match_law_name_to_slug(
    law_name: str,
    title_map: dict[str, str],
    laws: dict[str, dict],
) -> str | None:
    """
    Try to match a law name (from a draft's affectedLawName) to an existing law slug.
    Uses multiple matching strategies.
    """
    # Direct title match (case-insensitive)
    lower_name = law_name.lower().strip()
    if lower_name in title_map:
        return title_map[lower_name]

    # Slugified match
    slug_name = slugify(law_name)
    if slug_name in title_map:
        return title_map[slug_name]

    # Try direct slug match
    if slug_name in laws:
        return slug_name

    # Partial match: check if any law title contains this name
    for title_lower, slug in title_map.items():
        if lower_name in title_lower or title_lower in lower_name:
            return slug

    # Try suffix matching: "X seaduse" -> "X seadus"
    normalized = re.sub(r"seaduse$", "seadus", lower_name)
    normalized = re.sub(r"seadustiku$", "seadustik", normalized)
    if normalized in title_map:
        return title_map[normalized]
    slug_norm = slugify(normalized)
    if slug_norm in title_map:
        return title_map[slug_norm]
    if slug_norm in laws:
        return slug_norm

    return None


def clear_amended_by_from_file(filepath: Path) -> bool:
    """
    Remove estleg:amendedBy from all nodes in a law JSON-LD file.
    Returns True if the file was modified.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return False

    modified = False
    for node in data.get("@graph", []):
        if "estleg:amendedBy" in node:
            del node["estleg:amendedBy"]
            modified = True

    if modified:
        save_json(filepath, data)
    return modified


def main():
    print("=" * 70)
    print("Estonian Legal Ontology - Generate Amendment History")
    print("=" * 70)

    # Step 0: Clear existing amendedBy references
    print("\n[0/5] Clearing existing estleg:amendedBy from all law files...")
    cleared_count = 0
    for peep_file in sorted(KRR_DIR.glob("*_peep.json")):
        if peep_file.parent != KRR_DIR:
            continue
        if clear_amended_by_from_file(peep_file):
            cleared_count += 1
    print(f"  Cleared estleg:amendedBy from {cleared_count} files")

    # Step 1: Load all law files
    print("\n[1/5] Loading law JSON-LD files...")
    laws = load_law_files()
    print(f"  Loaded {len(laws)} law files")

    title_map = build_title_to_slug_map(laws)
    print(f"  Built title-to-slug mapping with {len(title_map)} entries")

    # Step 2: Extract amendments from XML files
    print("\n[2/5] Extracting amendment references from XML files...")
    xml_files = sorted(DATA_DIR.glob("*.xml")) if DATA_DIR.exists() else []
    amendments_by_slug: dict[str, list[dict]] = {}
    rt_refs_by_slug: dict[str, list[str]] = {}
    total_amendments = 0

    for xml_path in xml_files:
        slug = xml_path.stem
        amendments = extract_amendments_from_xml(xml_path)
        if amendments:
            amendments_by_slug[slug] = amendments
            total_amendments += len(amendments)
        rt_refs = extract_rt_references_from_text(xml_path)
        if rt_refs:
            rt_refs_by_slug[slug] = rt_refs

    print(f"  Found {total_amendments} amendment references across {len(amendments_by_slug)} laws")
    print(f"  Found RT references in {len(rt_refs_by_slug)} laws")

    # Step 3: Load draft amendments
    print("\n[3/5] Loading draft legislation amendment relationships...")
    draft_amendments = load_draft_amendments()
    print(f"  Found {len(draft_amendments)} amendment bill entries")

    # Match drafts to existing laws
    draft_matches: dict[str, list[dict]] = {}  # target law slug -> list of amending drafts
    matched_drafts = 0
    unmatched_drafts = 0

    for da in draft_amendments:
        target_slug = match_law_name_to_slug(da["affected_law_name"], title_map, laws)
        if target_slug:
            draft_matches.setdefault(target_slug, []).append(da)
            matched_drafts += 1
        else:
            unmatched_drafts += 1

    print(f"  Matched {matched_drafts} drafts to existing laws")
    print(f"  Unmatched: {unmatched_drafts} (law not in ontology)")

    # Step 4: Build amendment chain data and enrich JSON-LD files
    print("\n[4/5] Building amendment chains and enriching JSON-LD files...")
    enriched = 0
    amendment_chains: list[dict] = []

    for slug, info in sorted(laws.items()):
        base_slug = re.sub(r"_osa\d+$", "", slug)
        xml_amendments = amendments_by_slug.get(slug) or amendments_by_slug.get(base_slug, [])
        drafts = draft_matches.get(slug) or draft_matches.get(base_slug, [])

        if not xml_amendments and not drafts:
            continue

        doc = info["doc"]
        graph = doc.get("@graph", [])

        # Find ontology node
        ontology_node = None
        for node in graph:
            types = node.get("@type", [])
            if "owl:Ontology" in types:
                ontology_node = node
                break
        if ontology_node is None and graph:
            ontology_node = graph[0]
        if ontology_node is None:
            continue

        # Ensure context has dcterms
        ctx = doc.get("@context", {})
        if isinstance(ctx, dict) and "dcterms" not in ctx:
            ctx["dcterms"] = "http://purl.org/dc/terms/"
            doc["@context"] = ctx

        # Build amendment references from XML muutmismarge blocks
        amended_by_refs = []
        chain_entries = []

        for i, amend in enumerate(xml_amendments):
            # Create an amendment event ID
            amend_id = f"estleg:Amendment_{sanitize_id(base_slug)}_{i + 1}"

            amend_node: dict = {
                "@id": amend_id,
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
                "estleg:amends": {"@id": ontology_node["@id"]},
            }

            if amend.get("date"):
                amend_node["estleg:amendmentDate"] = make_xsd_date(amend["date"])
            if amend.get("entry_into_force"):
                amend_node["estleg:entryIntoForce"] = make_xsd_date(amend["entry_into_force"])
            if amend.get("rt_reference"):
                amend_node["estleg:rtReference"] = amend["rt_reference"]
                amend_node["rdfs:label"] = f"Muudatus: {amend['rt_reference']}"
            else:
                amend_node["rdfs:label"] = f"Muudatus {amend.get('date', 'unknown')}"

            amended_by_refs.append({"@id": amend_id})
            chain_entries.append(amend_node)

        # Build amendment references from draft legislation
        for da in drafts:
            draft_id = da["draft_id"]
            amended_by_refs.append({"@id": draft_id})
            # Create a linking node
            link_node: dict = {
                "@id": f"estleg:AmendmentLink_{sanitize_id(draft_id.replace('estleg:', ''))}_{sanitize_id(base_slug)}",
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
                "rdfs:label": f"Eelnõu muudatus: {da['draft_label']}",
                "estleg:amends": {"@id": ontology_node["@id"]},
                "estleg:amendingDraft": {"@id": draft_id},
            }
            if da.get("publication_date"):
                link_node["estleg:amendmentDate"] = make_xsd_date(da["publication_date"])
            chain_entries.append(link_node)

        # Add amendedBy to ontology node
        if amended_by_refs:
            ontology_node["estleg:amendedBy"] = amended_by_refs

        # Save enriched law file
        save_json(info["path"], doc)
        enriched += 1

        # Save amendment chain for this law
        if chain_entries:
            chain_doc = {
                "@context": CONTEXT,
                "@graph": [
                    {
                        "@id": f"estleg:AmendmentChain_{sanitize_id(base_slug)}",
                        "@type": ["owl:Ontology"],
                        "rdfs:label": f"Muudatuste ahel: {info['title']}",
                        "dc:source": info["title"],
                        "estleg:totalAmendments": {
                            "@value": str(len(chain_entries)),
                            "@type": "xsd:integer",
                        },
                    },
                    *chain_entries,
                ],
            }
            chain_path = AMENDMENTS_DIR / f"amendments_{base_slug}.json"
            save_json(chain_path, chain_doc)
            amendment_chains.append({
                "law": info["title"],
                "slug": slug,
                "xml_amendments": len(xml_amendments),
                "draft_amendments": len(drafts),
                "total": len(chain_entries),
                "file": chain_path.name,
            })

    print(f"  Enriched {enriched} law files with amendment references")
    print(f"  Created {len(amendment_chains)} amendment chain files")

    # Step 5: Generate report
    print("\n[5/5] Generating amendment_history_report.json...")

    # Find most amended laws
    amendment_chains_sorted = sorted(amendment_chains, key=lambda x: x["total"], reverse=True)

    # Deduplicate most_amended_laws by canonical law name (multi-part laws
    # like "Võlaõigusseadus osa 1..9" share the same law name and XML
    # amendment counts — aggregate across part files).
    deduped_most_amended: dict[str, dict] = {}
    for c in amendment_chains_sorted:
        canon_name = c["law"]
        if canon_name in deduped_most_amended:
            entry = deduped_most_amended[canon_name]
            entry["total_amendments"] += c["total"]
            entry["xml_amendments"] += c["xml_amendments"]
            entry["draft_amendments"] += c["draft_amendments"]
            entry["_part_count"] += 1
        else:
            deduped_most_amended[canon_name] = {
                "law": canon_name,
                "total_amendments": c["total"],
                "xml_amendments": c["xml_amendments"],
                "draft_amendments": c["draft_amendments"],
                "_part_count": 1,
            }
    # Sort deduplicated entries by total descending, strip internal key
    most_amended_list = sorted(
        deduped_most_amended.values(), key=lambda x: x["total_amendments"], reverse=True
    )
    for entry in most_amended_list:
        del entry["_part_count"]

    report = {
        "generated": date.today().isoformat(),
        "summary": {
            "total_laws_analyzed": len(laws),
            "laws_with_amendments": len(amendment_chains),
            "total_amendment_references": total_amendments,
            "draft_amendments_matched": matched_drafts,
            "draft_amendments_unmatched": unmatched_drafts,
            "laws_enriched": enriched,
        },
        "most_amended_laws": most_amended_list[:30],
        "amendment_chains": amendment_chains_sorted,
    }

    report_path = KRR_DIR / "amendment_history_report.json"
    save_json(report_path, report)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Laws analyzed:            {len(laws)}")
    print(f"  Laws with amendments:     {len(amendment_chains)}")
    print(f"  Total amendment refs:     {total_amendments}")
    print(f"  Draft amendments matched: {matched_drafts}")
    print(f"  Law files enriched:       {enriched}")
    print(f"  Amendment chain files:    {len(amendment_chains)}")
    if most_amended_list:
        print(f"\n  Top 5 most amended laws:")
        for c in most_amended_list[:5]:
            print(f"    {c['law']}: {c['total_amendments']} amendments")
    print(f"\n  Report: {report_path.relative_to(REPO_ROOT)}")
    print(f"  Chains: {AMENDMENTS_DIR.relative_to(REPO_ROOT)}/")
    print("=" * 70)


if __name__ == "__main__":
    main()
