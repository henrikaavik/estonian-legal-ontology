#!/usr/bin/env python3
"""
Fetch missing law parts from Riigi Teataja and generate JSON-LD ontology files.

Missing parts:
  - VÕS Osa 2 (Lepingu üldosa / General part of contracts)
  - VÕS Osa 6 (Kindlustuslepingud / Insurance contracts)
  - VÕS Osa 10 (Seltsingulepingud / Partnership)
  - TsÜS Osa 1 (Üldsätted / General provisions)
"""

from __future__ import annotations

import json
import re
import sys
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
    "skos": "http://www.w3.org/2004/02/skos/core#",
}


def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def child_text(el: ET.Element, name: str) -> str | None:
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


def sanitize_id(value: str) -> str:
    s = re.sub(r"[^0-9A-Za-z_]", "", value.replace(" ", "_"))
    return s or "Unknown"


def collect_text(el: ET.Element, max_len: int = 800) -> str:
    """Collect all text from an element and its children."""
    parts: list[str] = []
    for child in el.iter():
        if ln(child.tag) in ("loige", "lauseOsa", "lause"):
            txt = "".join(child.itertext()).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt:
                parts.append(txt)
        if len(" ".join(parts)) >= max_len:
            break
    joined = " ".join(parts)
    return joined[:max_len] if joined else ""


def fetch_law_xml(title: str) -> tuple[ET.Element, str, dict]:
    """Fetch a law's XML from Riigi Teataja."""
    print(f"  Searching for '{title}'...")
    resp = requests.get(
        SEARCH_URL,
        params={"leht": 1, "dokument": "seadus", "pealkiri": title},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    match = None
    for law in data.get("aktid", []):
        if title.lower() in (law.get("pealkiri") or "").lower():
            match = law
            break
    if not match:
        raise RuntimeError(f"'{title}' not found in Riigi Teataja API")

    xml_url = BASE_URL + match["url"]
    print(f"  Fetching XML from {xml_url}...")
    xml_resp = requests.get(xml_url, timeout=60)
    xml_resp.raise_for_status()
    xml_resp.encoding = "utf-8"
    xml_text = xml_resp.text

    # Cache locally
    safe_name = re.sub(r"[^a-z0-9_]", "_", title.lower())
    cache_path = DATA_DIR / f"{safe_name}.xml"
    cache_path.write_text(xml_text, encoding="utf-8")
    print(f"  Cached XML to {cache_path.name}")

    root = ET.fromstring(xml_text)
    return root, xml_url, match


def find_osa(root: ET.Element, osa_nr: str) -> ET.Element | None:
    """Find a specific osa (part) by number."""
    for osa in root.iter():
        if ln(osa.tag) == "osa":
            nr = child_text(osa, "osaNr")
            if nr == osa_nr:
                return osa
    return None


def extract_paragrahvid(parent: ET.Element) -> list[ET.Element]:
    """Recursively collect all paragrahv elements under a parent."""
    result = []
    for el in parent.iter():
        if ln(el.tag) == "paragrahv":
            result.append(el)
    return result


def generate_vos_part(root: ET.Element, xml_url: str, osa_nr: str) -> dict | None:
    """Generate JSON-LD for a VÕS part."""
    osa = find_osa(root, osa_nr)
    if osa is None:
        print(f"  WARNING: VÕS osa {osa_nr} not found in XML!")
        return None

    osa_title = child_text(osa, "osaPealkiri") or f"Osa {osa_nr}"
    osa_display = child_text(osa, "kuvatavNr") or f"{osa_nr}. osa"
    print(f"  Found: {osa_display} – {osa_title}")

    paragrahvid = extract_paragrahvid(osa)
    print(f"  Paragraphs found: {len(paragrahvid)}")

    if not paragrahvid:
        print("  WARNING: No paragraphs found!")
        return None

    # Determine paragraph range
    par_numbers = []
    for p in paragrahvid:
        nr = child_text(p, "paragrahvNr")
        if nr:
            try:
                par_numbers.append(int(re.sub(r"[^\d]", "", nr)))
            except ValueError:
                pass

    par_min = min(par_numbers) if par_numbers else "?"
    par_max = max(par_numbers) if par_numbers else "?"

    # Build graph
    prefix = "VOS"
    ontology_id = f"estleg:{prefix}_Osa{osa_nr}_{par_min}_{par_max}"

    graph: list[dict] = [
        {
            "@id": ontology_id,
            "@type": ["owl:Ontology"],
            "rdfs:label": f"VÕS Osa {osa_nr} ({osa_title}) §{par_min}–{par_max} kaardistus",
            "dc:source": "Võlaõigusseadus",
        },
        {
            "@id": f"estleg:LegalProvision_volaigusseadus_osa{osa_nr}",
            "@type": ["owl:Class"],
            "rdfs:label": "Õigusnorm (paragrahv)",
        },
    ]

    # Collect topic clusters from peatükk (chapter) structure
    clusters = []
    for ch in osa.iter():
        if ln(ch.tag) == "peatykk":
            ch_nr = child_text(ch, "peatykkNr") or ""
            ch_title = child_text(ch, "peatykkPealkiri") or ""
            if ch_title:
                cluster_id = f"estleg:Cluster_VOS_{osa_nr}_{sanitize_id(ch_nr or ch_title[:20])}"
                ch_pars = extract_paragrahvid(ch)
                ch_par_nrs = []
                for p in ch_pars:
                    nr = child_text(p, "paragrahvNr")
                    if nr:
                        try:
                            ch_par_nrs.append(int(re.sub(r"[^\d]", "", nr)))
                        except ValueError:
                            pass
                par_range = f"§{min(ch_par_nrs)}–{max(ch_par_nrs)}" if ch_par_nrs else ""
                clusters.append({
                    "id": cluster_id,
                    "label": f"{par_range} {ch_title}".strip(),
                    "par_nrs": ch_par_nrs,
                })

    # Add jagu-level clusters too
    for jg in osa.iter():
        if ln(jg.tag) == "jagu":
            jg_nr = child_text(jg, "jaguNr") or ""
            jg_title = child_text(jg, "jaguPealkiri") or ""
            if jg_title:
                cluster_id = f"estleg:Cluster_VOS_{osa_nr}_jagu_{sanitize_id(jg_nr or jg_title[:20])}"
                jg_pars = extract_paragrahvid(jg)
                jg_par_nrs = []
                for p in jg_pars:
                    nr = child_text(p, "paragrahvNr")
                    if nr:
                        try:
                            jg_par_nrs.append(int(re.sub(r"[^\d]", "", nr)))
                        except ValueError:
                            pass
                par_range = f"§{min(jg_par_nrs)}–{max(jg_par_nrs)}" if jg_par_nrs else ""
                clusters.append({
                    "id": cluster_id,
                    "label": f"{par_range} {jg_title}".strip(),
                    "par_nrs": jg_par_nrs,
                })

    # Add cluster nodes
    for cl in clusters:
        graph.append({
            "@id": cl["id"],
            "@type": ["owl:NamedIndividual", "estleg:TopicCluster"],
            "rdfs:label": cl["label"],
        })

    # Add paragraph nodes
    for p in paragrahvid:
        p_nr = child_text(p, "paragrahvNr") or "?"
        p_title = child_text(p, "paragrahvPealkiri") or ""
        p_display = child_text(p, "kuvatavNr") or f"§ {p_nr}"
        text = collect_text(p)

        p_id = f"estleg:VOS_Par_{sanitize_id(p_nr)}"

        # Find which cluster this paragraph belongs to
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
            "@type": [
                "owl:NamedIndividual",
                f"estleg:LegalProvision_volaigusseadus_osa{osa_nr}",
            ],
            "estleg:paragrahv": p_display,
            "rdfs:label": f"{p_display} {p_title}".strip() if p_title else p_display,
            "estleg:sourceAct": "Võlaõigusseadus",
        }

        if text:
            node["estleg:summary"] = text

        if cluster_ref:
            node["estleg:requestedCluster"] = cluster_ref

        graph.append(node)

    return {"@context": CONTEXT, "@graph": graph}


def generate_tsus_part1(root: ET.Element, xml_url: str) -> dict | None:
    """Generate JSON-LD for TsÜS Osa 1."""
    osa = find_osa(root, "1")
    if osa is None:
        # TsÜS might not have explicit osa markers for part 1
        # Try to find paragraphs 1-7 directly
        print("  Osa 1 not found as explicit element, looking for §§ 1-7...")
        paragrahvid = []
        for p in root.iter():
            if ln(p.tag) == "paragrahv":
                nr = child_text(p, "paragrahvNr")
                if nr:
                    try:
                        num = int(re.sub(r"[^\d]", "", nr))
                        if 1 <= num <= 7:
                            paragrahvid.append(p)
                    except ValueError:
                        pass
        if not paragrahvid:
            print("  WARNING: Could not find TsÜS §§ 1-7!")
            return None
    else:
        osa_title = child_text(osa, "osaPealkiri") or "Üldsätted"
        print(f"  Found: 1. osa – {osa_title}")
        paragrahvid = extract_paragrahvid(osa)

    print(f"  Paragraphs found: {len(paragrahvid)}")

    par_numbers = []
    for p in paragrahvid:
        nr = child_text(p, "paragrahvNr")
        if nr:
            try:
                par_numbers.append(int(re.sub(r"[^\d]", "", nr)))
            except ValueError:
                pass

    par_min = min(par_numbers) if par_numbers else 1
    par_max = max(par_numbers) if par_numbers else 7

    graph: list[dict] = [
        {
            "@id": f"estleg:TsUS_Osa1_{par_min}_{par_max}",
            "@type": ["owl:Ontology"],
            "rdfs:label": f"TsÜS Osa 1 (Üldsätted) §{par_min}–{par_max} kaardistus",
            "dc:source": "Tsiviilseadustiku üldosa seadus",
        },
        {
            "@id": "estleg:LegalProvision_tsiviilseadustik_osa1",
            "@type": ["owl:Class"],
            "rdfs:label": "Õigusnorm (paragrahv)",
        },
        {
            "@id": "estleg:Cluster_TsUS_Uldsatted",
            "@type": ["owl:NamedIndividual", "estleg:TopicCluster"],
            "rdfs:label": "Üldsätted (tsiviilõiguse aluspõhimõtted)",
        },
    ]

    for p in paragrahvid:
        p_nr = child_text(p, "paragrahvNr") or "?"
        p_title = child_text(p, "paragrahvPealkiri") or ""
        p_display = child_text(p, "kuvatavNr") or f"§ {p_nr}"
        text = collect_text(p)

        p_id = f"estleg:TsUS_Par_{sanitize_id(p_nr)}"

        node: dict = {
            "@id": p_id,
            "@type": [
                "owl:NamedIndividual",
                "estleg:LegalProvision_tsiviilseadustik_osa1",
            ],
            "estleg:paragrahv": p_display,
            "rdfs:label": f"{p_display} {p_title}".strip() if p_title else p_display,
            "estleg:requestedCluster": "estleg:Cluster_TsUS_Uldsatted",
            "estleg:sourceAct": "Tsiviilseadustiku üldosa seadus",
        }

        if text:
            node["estleg:summary"] = text

        graph.append(node)

    return {"@context": CONTEXT, "@graph": graph}


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  Saved: {filepath.name}")


def main():
    print("=" * 60)
    print("Generating missing law parts from Riigi Teataja")
    print("=" * 60)

    # === VÕS ===
    print("\n--- Fetching Võlaõigusseadus ---")
    try:
        vos_root, vos_url, vos_match = fetch_law_xml("Võlaõigusseadus")
    except Exception as e:
        print(f"  ERROR fetching VÕS: {e}")
        sys.exit(1)

    # List all osa elements for debugging
    print("\n  Available parts in VÕS:")
    for el in vos_root.iter():
        if ln(el.tag) == "osa":
            nr = child_text(el, "osaNr")
            title = child_text(el, "osaPealkiri")
            print(f"    Osa {nr}: {title}")

    for osa_nr in ["2", "6", "10"]:
        print(f"\n--- Generating VÕS Osa {osa_nr} ---")
        doc = generate_vos_part(vos_root, vos_url, osa_nr)
        if doc:
            out_path = KRR_DIR / f"volaigusseadus_osa{osa_nr}_peep.json"
            save_json(out_path, doc)
            node_count = len(doc["@graph"])
            print(f"  Generated {node_count} nodes")
        else:
            print(f"  FAILED to generate VÕS Osa {osa_nr}")

    # === TsÜS ===
    print("\n--- Fetching Tsiviilseadustiku üldosa seadus ---")
    try:
        tsus_root, tsus_url, tsus_match = fetch_law_xml("Tsiviilseadustiku üldosa seadus")
    except Exception as e:
        print(f"  ERROR fetching TsÜS: {e}")
        sys.exit(1)

    # List all osa elements
    print("\n  Available parts in TsÜS:")
    for el in tsus_root.iter():
        if ln(el.tag) == "osa":
            nr = child_text(el, "osaNr")
            title = child_text(el, "osaPealkiri")
            print(f"    Osa {nr}: {title}")

    print("\n--- Generating TsÜS Osa 1 ---")
    doc = generate_tsus_part1(tsus_root, tsus_url)
    if doc:
        out_path = KRR_DIR / "tsiviilseadustik_osa1_peep.json"
        save_json(out_path, doc)
        node_count = len(doc["@graph"])
        print(f"  Generated {node_count} nodes")
    else:
        print("  FAILED to generate TsÜS Osa 1")

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
