#!/usr/bin/env python3
"""
Fetch ALL Estonian laws from Riigi Teataja and generate JSON-LD ontology files.

This script:
1. Queries the Riigi Teataja API for all seadused (laws)
2. Keeps only the latest version of each law
3. Fetches the XML for each
4. Generates a JSON-LD ontology file for each
5. Skips laws that already have output files
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_URL = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
BASE_URL = "https://www.riigiteataja.ee"
NS = "https://data.riik.ee/ontology/estleg#"

CONTEXT = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}


def ln(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


def slugify(text: str) -> str:
    """Convert Estonian text to a filename-safe slug."""
    # Transliterate Estonian chars
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


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    s = value.replace(" ", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s or "Unknown"


def collect_text(el: ET.Element, max_len: int = 500) -> str:
    parts: list[str] = []
    for child in el.iter():
        tag = ln(child.tag)
        if tag in ("loige", "lauseOsa", "lause", "tavatekst"):
            txt = "".join(child.itertext()).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and len(txt) > 3:
                parts.append(txt)
        if len(" ".join(parts)) >= max_len:
            break
    joined = " ".join(parts)
    return joined[:max_len] if joined else ""


def get_all_laws() -> dict[str, dict]:
    """Fetch all law entries from Riigi Teataja API, keeping latest version of each."""
    all_laws = {}
    page = 1
    while True:
        try:
            resp = requests.get(
                SEARCH_URL,
                params={"leht": page, "dokument": "seadus", "tekst": "terviktekst"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  API error on page {page}: {e}")
            break

        aktid = data.get("aktid", [])
        if not aktid:
            break

        for law in aktid:
            title = law.get("pealkiri", "").strip()
            if not title:
                continue
            gid = str(law.get("globaalID", ""))
            # Keep the entry with the largest globalID (most recent)
            if title not in all_laws or gid > all_laws[title]["gid"]:
                all_laws[title] = {
                    "gid": gid,
                    "tid": str(law.get("terviktekstID", "")),
                    "url": law.get("url", ""),
                    "kehtivus": law.get("kehtivus", {}),
                    "lyhend": law.get("lyhend", ""),
                }
        page += 1
        if page > 250:
            break

    return all_laws


def get_existing_files() -> set[str]:
    """Get set of existing output file stems."""
    existing = set()
    for f in KRR_DIR.glob("*_peep.json"):
        existing.add(f.stem.replace("_peep", ""))
    return existing


def fetch_xml(url: str, cache_name: str) -> ET.Element | None:
    """Fetch law XML, using cache if available."""
    cache_path = DATA_DIR / f"{cache_name}.xml"

    if cache_path.exists() and cache_path.stat().st_size > 1000:
        try:
            return ET.parse(str(cache_path)).getroot()
        except ET.ParseError:
            pass

    full_url = BASE_URL + url if url.startswith("/") else url
    try:
        resp = requests.get(full_url, timeout=60)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        xml_text = resp.text

        if len(xml_text) < 200:
            return None

        cache_path.write_text(xml_text, encoding="utf-8")
        return ET.fromstring(xml_text)
    except Exception as e:
        print(f"    Fetch error: {e}")
        return None


_used_prefixes: dict[str, str] = {}  # prefix -> title that claimed it


def _unique_prefix(abbreviation: str, slug: str, title: str) -> str:
    """Return a collision-free prefix for IRI generation.

    Strategy:
    1. Try the abbreviation if it is long enough (>3 chars) and unique.
    2. Otherwise, try progressively longer slug prefixes (40, 50, 60, 70, 80, full).
    3. If all slug lengths still collide, append a numeric suffix.
    """
    candidate = sanitize_id(abbreviation) if abbreviation else None

    # Use slug-based prefix when abbreviation is too short or absent
    if not candidate or len(candidate) <= 3:
        candidate = sanitize_id(slug[:40])

    # Check for collision with a *different* law and resolve it
    if candidate in _used_prefixes and _used_prefixes[candidate] != title:
        # Try progressively longer slug prefixes
        resolved = False
        for length in (40, 50, 60, 70, 80, len(slug)):
            candidate = sanitize_id(slug[:length])
            if candidate not in _used_prefixes or _used_prefixes[candidate] == title:
                resolved = True
                break
        # If slug-based prefixes all collide, append a numeric suffix
        if not resolved:
            base = sanitize_id(slug)
            counter = 2
            candidate = f"{base}_{counter}"
            while candidate in _used_prefixes and _used_prefixes[candidate] != title:
                counter += 1
                candidate = f"{base}_{counter}"

    _used_prefixes[candidate] = title
    return candidate


def generate_law_jsonld(title: str, slug: str, root: ET.Element, abbreviation: str = "", rt_url: str = "") -> dict:
    """Generate JSON-LD for a single law."""
    prefix = _unique_prefix(abbreviation, slug, title)

    # Collect all paragrahv elements
    paragrahvid = [el for el in root.iter() if ln(el.tag) == "paragrahv"]

    # Get paragraph range
    par_numbers = []
    for p in paragrahvid:
        nr = ct(p, "paragrahvNr")
        if nr:
            try:
                par_numbers.append(int(re.sub(r"[^\d]", "", nr)))
            except ValueError:
                pass

    par_min = min(par_numbers) if par_numbers else "?"
    par_max = max(par_numbers) if par_numbers else "?"

    # Check if law has osa (parts) structure
    osad = []
    for el in root.iter():
        if ln(el.tag) == "osa":
            nr = ct(el, "osaNr")
            osa_title = ct(el, "osaPealkiri")
            if nr:
                osad.append({"nr": nr, "title": osa_title or ""})

    ontology_id = f"estleg:{prefix}_Map_2026"
    class_id = f"estleg:LegalProvision_{slug}"

    # Construct Riigi Teataja source URL
    rt_source_url = ""
    if rt_url:
        rt_source_url = BASE_URL + rt_url if rt_url.startswith("/") else rt_url

    ontology_node: dict = {
        "@id": ontology_id,
        "@type": ["owl:Ontology"],
        "rdfs:label": {"@value": f"{title} teemakaardistus", "@language": "et"},
        "dc:source": {"@value": title, "@language": "et"},
    }
    if rt_source_url:
        ontology_node["dcterms:source"] = {"@id": rt_source_url}

    graph: list[dict] = [
        ontology_node,
        {
            "@id": class_id,
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Õigusnorm (paragrahv)", "@language": "et"},
            "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
        },
    ]

    # Add topic clusters from chapters (peatükk)
    clusters = []
    for ch in root.iter():
        if ln(ch.tag) == "peatykk":
            ch_nr = ct(ch, "peatykkNr") or ""
            ch_title = ct(ch, "peatykkPealkiri") or ""
            if ch_title:
                ch_pars = [p for p in ch.iter() if ln(p.tag) == "paragrahv"]
                ch_par_nrs = []
                for p in ch_pars:
                    nr = ct(p, "paragrahvNr")
                    if nr:
                        try:
                            ch_par_nrs.append(int(re.sub(r"[^\d]", "", nr)))
                        except ValueError:
                            pass

                cluster_id = f"estleg:Cluster_{prefix}_{sanitize_id(ch_nr or ch_title[:20])}"
                par_range = f"§{min(ch_par_nrs)}–{max(ch_par_nrs)}" if ch_par_nrs else ""
                clusters.append({
                    "id": cluster_id,
                    "label": f"{par_range} {ch_title}".strip(),
                    "par_nrs": set(ch_par_nrs),
                })

                graph.append({
                    "@id": cluster_id,
                    "@type": ["owl:NamedIndividual", "estleg:TopicCluster"],
                    "rdfs:label": {"@value": f"{par_range} {ch_title}".strip(), "@language": "et"},
                    "skos:prefLabel": {"@value": f"{par_range} {ch_title}".strip(), "@language": "et"},
                })

    # Add paragraph nodes
    seen_ids = set()
    for p in paragrahvid:
        p_nr = ct(p, "paragrahvNr") or "?"
        p_title = ct(p, "paragrahvPealkiri") or ""
        p_display = ct(p, "kuvatavNr") or f"§ {p_nr}"
        text = collect_text(p)

        p_id = f"estleg:{prefix}_Par_{sanitize_id(p_nr)}"

        # Handle duplicates
        if p_id in seen_ids:
            p_id = f"{p_id}_{len(seen_ids)}"
        seen_ids.add(p_id)

        # Find cluster
        try:
            p_num = int(re.sub(r"[^\d]", "", p_nr))
        except ValueError:
            p_num = 0

        cluster_ref = None
        for cl in clusters:
            if p_num in cl["par_nrs"]:
                cluster_ref = cl["id"]
                break

        node: dict = {
            "@id": p_id,
            "@type": ["owl:NamedIndividual", class_id],
            "estleg:paragrahv": p_display,
            "rdfs:label": {"@value": f"{p_display} {p_title}".strip() if p_title else p_display, "@language": "et"},
            "estleg:sourceAct": {"@value": title, "@language": "et"},
        }

        if text:
            node["estleg:summary"] = {"@value": text, "@language": "et"}

        if cluster_ref:
            node["estleg:requestedCluster"] = cluster_ref

        graph.append(node)

    return {"@context": CONTEXT, "@graph": graph}


def generate_multipart_law(title: str, slug: str, root: ET.Element, abbreviation: str = "", rt_url: str = "") -> list[tuple[str, dict]]:
    """Generate separate JSON-LD files for each osa (part) of a multi-part law."""
    prefix = _unique_prefix(abbreviation, slug, title)
    results = []

    # Construct Riigi Teataja source URL
    rt_source_url = ""
    if rt_url:
        rt_source_url = BASE_URL + rt_url if rt_url.startswith("/") else rt_url

    for osa_el in root.iter():
        if ln(osa_el.tag) != "osa":
            continue

        osa_nr = ct(osa_el, "osaNr")
        osa_title = ct(osa_el, "osaPealkiri") or ""
        if not osa_nr:
            continue

        paragrahvid = [el for el in osa_el.iter() if ln(el.tag) == "paragrahv"]
        if not paragrahvid:
            continue

        par_numbers = []
        for p in paragrahvid:
            nr = ct(p, "paragrahvNr")
            if nr:
                try:
                    par_numbers.append(int(re.sub(r"[^\d]", "", nr)))
                except ValueError:
                    pass

        par_min = min(par_numbers) if par_numbers else "?"
        par_max = max(par_numbers) if par_numbers else "?"

        ontology_id = f"estleg:{prefix}_Osa{osa_nr}_{par_min}_{par_max}"
        class_id = f"estleg:LegalProvision_{slug}_osa{osa_nr}"

        osa_ontology_node: dict = {
            "@id": ontology_id,
            "@type": ["owl:Ontology"],
            "rdfs:label": {"@value": f"{title} Osa {osa_nr} ({osa_title}) §{par_min}–{par_max} kaardistus", "@language": "et"},
            "dc:source": {"@value": title, "@language": "et"},
        }
        if rt_source_url:
            osa_ontology_node["dcterms:source"] = {"@id": rt_source_url}

        graph: list[dict] = [
            osa_ontology_node,
            {
                "@id": class_id,
                "@type": ["owl:Class"],
                "rdfs:label": {"@value": "Õigusnorm (paragrahv)", "@language": "et"},
                "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
            },
        ]

        # Clusters from peatükk within this osa
        clusters = []
        for ch in osa_el:
            if ln(ch.tag) == "peatykk":
                ch_nr = ct(ch, "peatykkNr") or ""
                ch_title = ct(ch, "peatykkPealkiri") or ""
                if ch_title:
                    ch_pars = [p for p in ch.iter() if ln(p.tag) == "paragrahv"]
                    ch_par_nrs = set()
                    for p in ch_pars:
                        nr = ct(p, "paragrahvNr")
                        if nr:
                            try:
                                ch_par_nrs.add(int(re.sub(r"[^\d]", "", nr)))
                            except ValueError:
                                pass
                    cluster_id = f"estleg:Cluster_{prefix}_{osa_nr}_{sanitize_id(ch_nr or ch_title[:20])}"
                    par_range = f"§{min(ch_par_nrs)}–{max(ch_par_nrs)}" if ch_par_nrs else ""
                    clusters.append({"id": cluster_id, "label": f"{par_range} {ch_title}".strip(), "par_nrs": ch_par_nrs})
                    graph.append({
                        "@id": cluster_id,
                        "@type": ["owl:NamedIndividual", "estleg:TopicCluster"],
                        "rdfs:label": {"@value": f"{par_range} {ch_title}".strip(), "@language": "et"},
                        "skos:prefLabel": {"@value": f"{par_range} {ch_title}".strip(), "@language": "et"},
                    })

        seen_ids = set()
        for p in paragrahvid:
            p_nr = ct(p, "paragrahvNr") or "?"
            p_title = ct(p, "paragrahvPealkiri") or ""
            p_display = ct(p, "kuvatavNr") or f"§ {p_nr}"
            text = collect_text(p)
            p_id = f"estleg:{prefix}_Osa{osa_nr}_Par_{sanitize_id(p_nr)}"
            if p_id in seen_ids:
                p_id = f"{p_id}_{len(seen_ids)}"
            seen_ids.add(p_id)

            try:
                p_num = int(re.sub(r"[^\d]", "", p_nr))
            except ValueError:
                p_num = 0

            cluster_ref = None
            for cl in clusters:
                if p_num in cl["par_nrs"]:
                    cluster_ref = cl["id"]
                    break

            node: dict = {
                "@id": p_id,
                "@type": ["owl:NamedIndividual", class_id],
                "estleg:paragrahv": p_display,
                "rdfs:label": {"@value": f"{p_display} {p_title}".strip() if p_title else p_display, "@language": "et"},
                "estleg:sourceAct": {"@value": title, "@language": "et"},
            }
            if text:
                node["estleg:summary"] = {"@value": text, "@language": "et"}
            if cluster_ref:
                node["estleg:requestedCluster"] = cluster_ref
            graph.append(node)

        filename = f"{slug}_osa{osa_nr}_peep.json"
        results.append((filename, {"@context": CONTEXT, "@graph": graph}))

    return results


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


# Laws that should be split by osa (large multi-part laws)
MULTIPART_LAWS = {
    "Võlaõigusseadus",
    "Tsiviilseadustiku üldosa seadus",
    "Asjaõigusseadus",
    "Karistusseadustik",
    "Tsiviilkohtumenetluse seadustik",
    "Kriminaalmenetluse seadustik",
}


def main():
    print("=" * 70)
    print("Estonian Legal Ontology - Generate ALL Laws from Riigi Teataja")
    print("=" * 70)

    # Step 1: Get all laws from API
    print("\n[1/4] Fetching law list from Riigi Teataja API...")
    all_laws = get_all_laws()
    print(f"  Found {len(all_laws)} unique law titles")

    # Step 2: Check existing files
    print("\n[2/4] Checking existing files...")
    existing = get_existing_files()
    print(f"  Found {len(existing)} existing output files")

    # Step 3: Determine which laws to generate
    to_generate = {}
    already_mapped = 0
    for title, info in sorted(all_laws.items()):
        slug = slugify(title)
        if slug in existing:
            already_mapped += 1
            continue
        # Also check if any existing file starts with this slug
        matched = False
        for ex in existing:
            if ex.startswith(slug[:20]):
                matched = True
                already_mapped += 1
                break
        if not matched:
            to_generate[title] = {**info, "slug": slug}

    print(f"  Already mapped: {already_mapped}")
    print(f"  To generate: {len(to_generate)}")

    # Step 4: Generate each law
    print(f"\n[3/4] Generating {len(to_generate)} laws...")
    generated = 0
    failed = 0
    skipped = 0

    for i, (title, info) in enumerate(sorted(to_generate.items()), 1):
        slug = info["slug"]
        url = info["url"]
        abbreviation = info.get("lyhend", "")

        print(f"\n  [{i}/{len(to_generate)}] {title}")
        print(f"    slug: {slug}, url: {url}")

        # Fetch XML
        root = fetch_xml(url, slug)
        if root is None:
            print(f"    SKIP: Could not fetch XML")
            failed += 1
            continue

        # Count paragraphs
        par_count = sum(1 for el in root.iter() if ln(el.tag) == "paragrahv")
        if par_count == 0:
            print(f"    SKIP: No paragraphs found (likely a ratification/procedural law)")
            skipped += 1
            continue

        # Check if multi-part
        osa_count = sum(1 for el in root.iter() if ln(el.tag) == "osa")

        if title in MULTIPART_LAWS and osa_count > 1:
            # Generate separate files per osa
            results = generate_multipart_law(title, slug, root, abbreviation, rt_url=url)
            for filename, doc in results:
                out_path = KRR_DIR / filename
                if not out_path.exists():
                    save_json(out_path, doc)
                    print(f"    Saved: {filename} ({len(doc['@graph'])} nodes)")
                    generated += 1
        else:
            # Single file
            doc = generate_law_jsonld(title, slug, root, abbreviation, rt_url=url)
            filename = f"{slug}_peep.json"
            out_path = KRR_DIR / filename
            save_json(out_path, doc)
            node_count = len(doc["@graph"])
            print(f"    Saved: {filename} ({node_count} nodes, {par_count} paragraphs)")
            generated += 1

        # Rate limit - be polite to Riigi Teataja
        time.sleep(0.3)

    # Step 4: Summary
    print("\n" + "=" * 70)
    print("[4/4] SUMMARY")
    print("=" * 70)
    print(f"  Total laws in Riigi Teataja: {len(all_laws)}")
    print(f"  Already mapped: {already_mapped}")
    print(f"  Newly generated: {generated}")
    print(f"  Skipped (no paragraphs): {skipped}")
    print(f"  Failed (fetch errors): {failed}")
    print(f"  Total files now: {len(list(KRR_DIR.glob('*_peep.json')))}")


if __name__ == "__main__":
    main()
