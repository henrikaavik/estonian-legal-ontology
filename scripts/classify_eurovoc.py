#!/usr/bin/env python3
"""
Classify Estonian laws with EuroVoc subject taxonomy using keyword matching.

Scans existing law JSON-LD files for Estonian legal terminology and assigns
EuroVoc concept URIs (http://eurovoc.europa.eu/{code}) via dcterms:subject.

Generates:
  - krr_outputs/eurovoc_classification.json    (report of all classifications)
  - Updates existing law JSON-LD files with dcterms:subject
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
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

EUROVOC_URI_BASE = "http://eurovoc.europa.eu/"

# EuroVoc domain mapping: code → (slug, label_et, label_en, [keywords])
# Keywords are Estonian stems/substrings matched case-insensitively against law text.
# Prefix a keyword with "r:" to use it as a regex pattern instead of a plain substring.
EUROVOC_DOMAINS: dict[str, tuple[str, str, str, list[str]]] = {
    # "2411" (Law) removed: 609/615 laws matched — tautological for a legal ontology.
    "2421": (
        "criminal-law", "kriminaalõigus", "Criminal law",
        ["karistus", "kuritegu", "süüdi", "kriminaal", "vangist", "väärtegu", "kahtlust"],
    ),
    "2431": (
        "civil-law", "tsiviilõigus", "Civil law",
        ["tsiviil", "leping", "võlg", "kahju", "nõue", "hagi", "kostja", "hageja"],
    ),
    "2441": (
        "administrative-law", "haldusõigus", "Administrative law",
        ["haldus", "haldusmenetl", "järelevalve", "ettekirjut", "haldusakt"],
    ),
    "2446": (
        "constitutional-law", "riigiõigus", "Constitutional law",
        ["põhiseadus", "riigikogu", "president", "valitsus", "vabariig", "riigikohus"],
    ),
    "2806": (
        "consumer-protection", "tarbijakaitse", "Consumer protection",
        ["tarbija", "tarbijakaits", "tooteohutus", "garantii"],
    ),
    "2841": (
        "competition", "konkurents", "Competition",
        ["konkurents", "monopol", "koondum", "turgu valitsev", "riigiabi"],
    ),
    "3606": (
        "employment", "tööhõive", "Employment",
        ["töö", "tööandja", "töötaja", "palk", "puhkus", "töölepingu", "töösuhte",
         "tööaeg", "töötasu", "koondami"],
    ),
    "3611": (
        "social-security", "sotsiaalkindlustus", "Social security",
        ["sotsiaal", "pension", "toetus", "hüvitis", "ravikindlust", "töötuskindlust",
         "puue", "hooldus"],
    ),
    "5206": (
        "taxation", "maksundus", "Taxation",
        ["maks", "tulumaks", "käibemaks", "aktsiis", "maksuhaldu", "maksukohust",
         "sotsiaalmaks"],
    ),
    "5211": (
        "budget", "eelarve", "Budget",
        ["eelarve", "riigieelarve", "kohalik eelarve"],
    ),
    "5616": (
        "transport", "transport", "Transport",
        ["liiklusseadu", "liikluskindlust", "transpordiseadu", "raudteeseadu",
         "lennundus", "meresõit", "maanteeseadu", "teeseadu",
         "autoparkla", "ühistransport", "sadamaseadu",
         "autoveoseadu", "kaubavedu", "reisijatevedu",
         "raskeveokimaks"],
    ),
    "5231": (
        "banking", "pangandus", "Banking",
        ["pank", "krediit", "finants", "pangand", "laenu", "hoius", "investeeri",
         "väärtpaber", "fond"],
    ),
    "6411": (
        "building", "ehitus", "Building and public works",
        ["ehit", "planeeri", "kinnisvara", "hoone", "detailplaneering", "üldplaneering"],
    ),
    "5216": (
        "customs", "toll", "Customs",
        ["toll", "import", "eksport", "tollikontroll"],
    ),
    "2826": (
        "data-protection", "andmekaitse", "Data protection",
        ["andmekaitse", "isikuandm", "privaatsus", "andmetöötl"],
    ),
    "3216": (
        "intellectual-property", "intellektuaalomand", "Intellectual property",
        ["autoriõigus", "patent", "kaubamärk", "leiutis", "tööstusdisain", "intellektuaalomand"],
    ),
    "2451": (
        "judicial-cooperation", "õiguskoostöö", "Judicial cooperation",
        ["õigusabi", "väljaandmi", "loovutami", "vastastikune tunnustami"],
    ),
    "5226": (
        "trade", "kaubandus", "Trade",
        ["kauband", "müük", "ost", "jaekauband", "hulgikauband"],
    ),
    "2416": (
        "human-rights", "inimõigused", "Human rights",
        ["inimõig", "põhiõig", "vabadus", "võrdsus", "diskrimineeri", "soolise võrdõiguslikkuse"],
    ),
    "5606": (
        "energy", "energeetika", "Energy",
        ["energ", "elektr", "gaas", "küte", "taastuvenerg", "tuumaenerg", "kütus"],
    ),
    "5611": (
        "environment", "keskkond", "Environment",
        ["keskkond", "saaste", "jäätm", "loodus", "vesi", "looduskaits",
         "keskkonnamõj", "heitm", "kliima", "bioloogiline mitmekesisus"],
    ),
    "2821": (
        "communications", "side", "Communications",
        ["side", "telekommunikatsioon", "ringhääling", "meedia", "elektrooniline side"],
    ),
    "5621": (
        "agriculture", "põllumajandus", "Agriculture",
        ["põllumajandus", "maaelu", "mets", "kalandu", "taimekait", "loomakait",
         "mahepõllumajandus", "sööt"],
    ),
    "5631": (
        "health", "tervishoid", "Health",
        ["tervis", "ravim", "haigla", "arst", "meditsiini", "patsien", "tervisekaits",
         "nakkushaig", "vaktsineer"],
    ),
    "5636": (
        "education", "haridus", "Education",
        ["haridus", "kool", "ülikool", "õpe", "õppekav", "kutseharidus", "teaduskraad",
         "stipendium"],
    ),
    "0431": (
        "defence", "riigikaitse", "Defence",
        ["sõjaväe", "kaitseväe", "riigikaitse", "mobilisatsioon", "rahvusvaheline sõjaline"],
    ),
    "0806": (
        "migration", "ränne", "Migration",
        ["välismaalane", "pagulane", "varjupaig", "ränne", "kodakondsus", "elamisluba",
         "viisa"],
    ),
    "2016": (
        "elections", "valimised", "Elections",
        ["valim", "häälet", "valimisnimekiri", "valimiskom", "kandidaat", "referendum"],
    ),
    "6006": (
        "science", "teadus", "Science and research",
        ["teadus", "uurim", "innovatsioon", "teadusasutus"],
    ),
    "0411": (
        "international-affairs", "välissuhted", "International affairs",
        ["rahvusvaheli", "välislepingu", "konventsioon", "protokoll"],
    ),
    "1011": (
        "EU-law", "Euroopa Liidu õigus", "EU law",
        ["euroopa liidu", "euroopa ühendus", "direktiiv", "ülevõtmi"],
    ),
    "1221": (
        "local-government", "kohalik omavalitsus", "Local government",
        ["kohalik omavalitsus", "vald", "linn", "volikogu", "linnavalitsus",
         "vallavalitsus", "omavalitsusüksus"],
    ),
    "4421": (
        "maritime-law", "mereõigus", "Maritime and inland waterway transport",
        ["meresõit", "laev", "sadam", "merendus"],
    ),
    "2031": (
        "political-parties", "erakonnad", "Political framework",
        ["erakond", "partei"],
    ),
    "1236": (
        "public-finance", "avalik rahandus", "Public finance and budget policy",
        ["riigikass", "avaliku sektori", "riigi raamatupidami"],
    ),
    "5241": (
        "insurance", "kindlustus", "Insurance",
        ["kindlust", "liikluskindlust", "elukindlust"],
    ),
    "2811": (
        "information-technology", "infotehnoloogia", "Information technology",
        ["infotehnoloog", "infosüsteem", "küberturv", "digitaal", "e-teenus"],
    ),
    "0816": (
        "public-order", "avalik kord", "Public order and safety",
        ["politsei", "päästeteenius", "hädaolukord", "avalik kord", "turvalisus"],
    ),
    "5246": (
        "accountancy", "raamatupidamine", "Accountancy",
        ["raamatupidami", "aruandlus", "audiitor", "majandusaasta aruann"],
    ),
    "6016": (
        "land-use", "maakasutus", "Land use",
        ["maakatast", "maareform", "maakorraldus", "kinnistusraamat"],
    ),
    "3221": (
        "culture", "kultuur", "Culture",
        ["kultuur", "muuseum", "raamatukogu", "kunst", "pärandkaits"],
    ),
    "2836": (
        "advertising", "reklaam", "Advertising",
        ["reklaam"],
    ),
    "4416": (
        "air-transport", "lennundus", "Air transport",
        ["lennundus", "lennuk", "lennujaam", "õhusõiduk"],
    ),
}

# Maximum number of EuroVoc domains to assign per law
MAX_DOMAINS_PER_LAW = 5
# Minimum keyword hits to assign a domain (default)
MIN_HITS_THRESHOLD = 1
# Per-domain overrides for minimum hits (domain code → threshold)
MIN_HITS_OVERRIDES: dict[str, int] = {
    "5616": 3,  # Transport: require 3+ keyword hits to reduce over-classification
}


def save_json(filepath: Path, doc: dict | list):
    """Write a JSON document to disk with UTF-8 encoding."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_json(filepath: Path) -> dict:
    """Load a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_text_from_law(data: dict) -> str:
    """
    Extract all searchable text from a law JSON-LD file.
    Combines rdfs:label, estleg:sourceAct, and estleg:summary fields.
    """
    parts: list[str] = []

    for node in data.get("@graph", []):
        label = node.get("rdfs:label", "")
        if label:
            parts.append(label)

        source_act = node.get("estleg:sourceAct", "")
        if source_act:
            parts.append(source_act)

        summary = node.get("estleg:summary", "")
        if summary:
            parts.append(summary)

        # Also check skos:prefLabel, dc:description
        pref_label = node.get("skos:prefLabel", "")
        if pref_label:
            parts.append(pref_label)

        dc_desc = node.get("dc:description", "")
        if dc_desc:
            parts.append(dc_desc)

    return " ".join(parts).lower()


def classify_text(text: str) -> list[tuple[str, str, str, str, int]]:
    """
    Classify text against EuroVoc domains by keyword matching.
    Returns list of (code, slug, label_et, label_en, hit_count) sorted by hit_count desc.
    """
    results: list[tuple[str, str, str, str, int]] = []

    for code, (slug, label_et, label_en, keywords) in EUROVOC_DOMAINS.items():
        hit_count = 0
        for kw in keywords:
            if kw.startswith("r:"):
                # Regex pattern (e.g. for word-boundary-aware matching)
                hit_count += len(re.findall(kw[2:], text, re.IGNORECASE))
            else:
                # Plain case-insensitive substring match
                hit_count += len(re.findall(re.escape(kw), text, re.IGNORECASE))

        threshold = MIN_HITS_OVERRIDES.get(code, MIN_HITS_THRESHOLD)
        if hit_count >= threshold:
            results.append((code, slug, label_et, label_en, hit_count))

    # Sort by hit count descending, then by code
    results.sort(key=lambda r: (-r[4], r[0]))

    # Limit to top domains
    return results[:MAX_DOMAINS_PER_LAW]


def update_law_file_eurovoc(filepath: Path, domains: list[tuple[str, str, str, str, int]]) -> bool:
    """
    Add dcterms:subject with EuroVoc concept URIs to a law JSON-LD file.
    Targets the owl:Ontology metadata node.
    Returns True if the file was modified.
    """
    try:
        data = load_json(filepath)
    except Exception as e:
        print(f"    ERROR loading {filepath.name}: {e}")
        return False

    # Ensure dcterms is in context
    ctx = data.get("@context", {})
    if "dcterms" not in ctx:
        ctx["dcterms"] = "http://purl.org/dc/terms/"
        data["@context"] = ctx

    graph = data.get("@graph", [])

    # Find the ontology metadata node
    target_node = None
    for node in graph:
        types = node.get("@type", [])
        if "owl:Ontology" in types:
            target_node = node
            break

    if target_node is None and graph:
        target_node = graph[0]

    if target_node is None:
        return False

    # Build subject references — bare IRI refs only (SHACL expects sh:nodeKind sh:IRI)
    subject_refs = []
    for code, slug, label_et, label_en, _ in domains:
        subject_refs.append({
            "@id": f"{EUROVOC_URI_BASE}{code}",
        })

    # Check for existing dcterms:subject (non-EuroVoc entries preserved)
    existing = target_node.get("dcterms:subject", [])
    if isinstance(existing, dict):
        existing = [existing]

    # Normalize existing refs to bare IRIs (strip any embedded labels)
    existing_clean = []
    for ref in existing:
        if isinstance(ref, dict) and "@id" in ref:
            existing_clean.append({"@id": ref["@id"]})
        elif isinstance(ref, dict):
            existing_clean.append(ref)

    existing_ids = {ref.get("@id", "") for ref in existing_clean if isinstance(ref, dict)}

    new_refs = [ref for ref in subject_refs if ref["@id"] not in existing_ids]
    if not new_refs:
        return False

    all_refs = existing_clean + new_refs
    target_node["dcterms:subject"] = all_refs

    save_json(filepath, data)
    return True


def clear_eurovoc_subjects_from_file(filepath: Path) -> bool:
    """
    Remove all dcterms:subject entries whose @id starts with the EuroVoc URI base.
    Preserves any non-EuroVoc subjects. Returns True if the file was modified.
    """
    try:
        data = load_json(filepath)
    except Exception:
        return False

    modified = False
    for node in data.get("@graph", []):
        existing = node.get("dcterms:subject")
        if existing is None:
            continue

        if isinstance(existing, dict):
            existing = [existing]
        elif not isinstance(existing, list):
            continue

        kept = [
            ref for ref in existing
            if not (isinstance(ref, dict) and ref.get("@id", "").startswith(EUROVOC_URI_BASE))
        ]

        if len(kept) != len(existing):
            modified = True
            if not kept:
                del node["dcterms:subject"]
            else:
                node["dcterms:subject"] = kept

    if modified:
        save_json(filepath, data)
    return modified


def main():
    print("=" * 60)
    print("Classify Estonian laws with EuroVoc subject taxonomy")
    print(f"EuroVoc domains defined: {len(EUROVOC_DOMAINS)}")
    print("=" * 60)

    # --- Step 0: Clear existing EuroVoc subjects ---
    print("\n--- Clearing existing EuroVoc subjects ---")
    cleared_count = 0
    for peep_file in sorted(KRR_DIR.glob("*_peep.json")):
        if peep_file.parent != KRR_DIR:
            continue
        if clear_eurovoc_subjects_from_file(peep_file):
            cleared_count += 1
    print(f"  Cleared EuroVoc subjects from {cleared_count} files")

    # --- Step 1: Load index ---
    print("\n--- Loading INDEX.json ---")
    index_path = KRR_DIR / "INDEX.json"
    if not index_path.exists():
        print(f"ERROR: {index_path} not found. Run generate_all_laws.py first.")
        sys.exit(1)

    index_data = load_json(index_path)
    laws = index_data.get("laws", [])
    print(f"  Total laws in index: {len(laws)}")

    # --- Step 2: Classify each law ---
    print("\n--- Classifying laws ---")

    classifications: list[dict] = []
    domain_counts: dict[str, int] = {}  # code → number of laws assigned
    files_updated = 0
    files_skipped = 0
    files_error = 0
    unclassified: list[str] = []

    for i, law in enumerate(laws):
        name = law.get("name", "")
        files = law.get("files", [])
        if not files:
            continue

        # Load the primary law file
        primary_file = KRR_DIR / files[0]
        if not primary_file.exists():
            files_error += 1
            continue

        try:
            data = load_json(primary_file)
        except Exception as e:
            print(f"    ERROR loading {primary_file.name}: {e}")
            files_error += 1
            continue

        # Extract text and classify
        text = extract_text_from_law(data)
        domains = classify_text(text)

        if not domains:
            unclassified.append(name)
            files_skipped += 1
            continue

        # Record classification
        entry = {
            "law_name": name,
            "file": files[0],
            "domains": [
                {
                    "code": code,
                    "slug": slug,
                    "label_et": label_et,
                    "label_en": label_en,
                    "eurovoc_uri": f"{EUROVOC_URI_BASE}{code}",
                    "keyword_hits": hits,
                }
                for code, slug, label_et, label_en, hits in domains
            ],
        }
        classifications.append(entry)

        # Count domain assignments
        for code, *_ in domains:
            domain_counts[code] = domain_counts.get(code, 0) + 1

        # Update law file(s)
        for law_file in files:
            filepath = KRR_DIR / law_file
            if filepath.exists():
                if update_law_file_eurovoc(filepath, domains):
                    files_updated += 1

        if (i + 1) % 50 == 0:
            print(f"  Processed {i + 1}/{len(laws)} laws ({files_updated} files updated)...")

    print(f"  Processed all {len(laws)} laws")

    # --- Step 3: Generate domain statistics ---
    print("\n--- Domain statistics ---")
    domain_stats: list[dict] = []
    for code, count in sorted(domain_counts.items(), key=lambda x: -x[1]):
        slug, label_et, label_en, _ = EUROVOC_DOMAINS[code]
        domain_stats.append({
            "code": code,
            "slug": slug,
            "label_et": label_et,
            "label_en": label_en,
            "eurovoc_uri": f"{EUROVOC_URI_BASE}{code}",
            "laws_count": count,
        })
        print(f"    {code} {label_en:35s} ({label_et:30s}): {count:4d} laws")

    # --- Step 4: Generate report ---
    print("\n--- Generating classification report ---")
    report = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "method": "keyword-matching",
        "eurovoc_domains_defined": len(EUROVOC_DOMAINS),
        "total_laws_processed": len(laws),
        "total_classified": len(classifications),
        "total_unclassified": len(unclassified),
        "files_updated": files_updated,
        "files_error": files_error,
        "domain_statistics": domain_stats,
        "classifications": sorted(classifications, key=lambda c: c["law_name"]),
        "unclassified_laws": sorted(unclassified),
    }

    report_path = KRR_DIR / "eurovoc_classification.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("EuroVoc classification complete!")
    print(f"  Laws processed:       {len(laws)}")
    print(f"  Laws classified:      {len(classifications)}")
    print(f"  Laws unclassified:    {len(unclassified)}")
    print(f"  Law files updated:    {files_updated}")
    print(f"  File errors:          {files_error}")
    print(f"  Domains used:         {len(domain_counts)}/{len(EUROVOC_DOMAINS)}")
    print(f"\nOutput: {report_path.relative_to(REPO_ROOT)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
