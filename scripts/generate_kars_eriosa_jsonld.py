#!/usr/bin/env python3
"""Fetch Karistusseadustik from Riigi Teataja and generate Eriosa JSON-LD mapping."""

from __future__ import annotations

import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

SEARCH_URL = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
BASE_URL = "https://www.riigiteataja.ee"
LAW_TITLE = "Karistusseadustik"


LEGAL_DEFINITIONS = [
    {
        "id": "Syytegu",
        "label": "Süütegu",
        "definition": "Karistusseadustikus või muus seaduses sätestatud karistatav tegu.",
        "crossReference": "KarS § 3 lg 1",
    },
    {
        "id": "Kuritegu",
        "label": "Kuritegu",
        "definition": "Karistusseadustikus sätestatud süütegu, mille eest on füüsilisele isikule põhikaristusena ette nähtud rahaline karistus või vangistus ja juriidilisele isikule rahaline karistus või sundlõpetamine.",
        "crossReference": "KarS § 3 lg 3",
    },
    {
        "id": "Vaartegu",
        "label": "Väärtegu",
        "definition": "Karistusseadustikus või muus seaduses sätestatud süütegu, mille eest on põhikaristusena ette nähtud rahatrahv või arest.",
        "crossReference": "KarS § 3 lg 4",
    },
    {
        "id": "Tahtlus",
        "label": "Tahtlus",
        "definition": "Tahtlus on kavatsetus, otsene tahtlus või kaudne tahtlus.",
        "crossReference": "KarS § 16",
    },
    {
        "id": "Ettevaatamatus",
        "label": "Ettevaatamatus",
        "definition": "Ettevaatamatus on kergemeelsus ja hooletus.",
        "crossReference": "KarS § 18 lg 1",
    },
]


def ln(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def child_text(el: ET.Element, name: str) -> str | None:
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


def sanitize_identifier(value: str) -> str:
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = value.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z]+", "", s)
    return s or "Unknown"


def collect_loige_preview(paragrahv: ET.Element, max_len: int = 500) -> str:
    previews: list[str] = []
    for el in paragrahv.iter():
        if ln(el.tag) == "loige":
            txt = "".join(el.itertext()).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt:
                previews.append(txt)
        if len(" ".join(previews)) >= max_len:
            break
    joined = " ".join(previews)
    return joined[:max_len]


def main() -> None:
    workspace = Path(__file__).resolve().parents[1]
    data_dir = workspace / "data" / "riigiteataja"
    out_dir = workspace / "krr_outputs"
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    search_resp = requests.get(
        SEARCH_URL,
        params={"leht": 1, "dokument": "seadus", "pealkiri": LAW_TITLE},
        timeout=30,
    )
    search_resp.raise_for_status()
    search_data = search_resp.json()

    match = None
    for law in search_data.get("aktid", []):
        if (law.get("pealkiri") or "").strip().lower() == LAW_TITLE.lower():
            match = law
            break
    if not match:
        raise RuntimeError("Karistusseadustik not found in API response")

    xml_url = BASE_URL + match["url"]
    xml_resp = requests.get(xml_url, timeout=30)
    xml_resp.raise_for_status()
    xml_resp.encoding = "utf-8"
    xml_text = xml_resp.text

    xml_path = data_dir / "karistusseadustik.xml"
    xml_path.write_text(xml_text, encoding="utf-8")

    root = ET.fromstring(xml_text)

    parsed_title = next((e.text.strip() for e in root.iter() if ln(e.tag) == "aktinimi" and e.text), "")
    title = parsed_title or (match.get("pealkiri") or LAW_TITLE)

    osa2 = None
    for osa in root.iter():
        if ln(osa.tag) != "osa":
            continue
        if child_text(osa, "osaNr") == "2":
            osa2 = osa
            break
    if osa2 is None:
        raise RuntimeError("Could not find Eriosa (osaNr=2)")

    base = "https://data.riik.ee/ontology/estleg#"

    graph: list[dict] = [
        {
            "@id": "estleg:KarS_Eriosa_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "KarS Eriosa ontoloogia",
            "dc:title": f"{title} – Eriosa",
            "dc:source": title,
            "dcterms:source": {"@id": xml_url},
        },
        {"@id": "estleg:LegalPart", "@type": ["owl:Class"], "rdfs:label": "Seaduse osa"},
        {"@id": "estleg:Chapter", "@type": ["owl:Class"], "rdfs:label": "Peatükk"},
        {"@id": "estleg:Division", "@type": ["owl:Class"], "rdfs:label": "Jagu"},
        {"@id": "estleg:Subdivision", "@type": ["owl:Class"], "rdfs:label": "Jaotis"},
        {"@id": "estleg:Section", "@type": ["owl:Class"], "rdfs:label": "Paragrahv"},
        {"@id": "estleg:LegalConcept", "@type": ["owl:Class"], "rdfs:label": "Õigusmõiste"},
    ]

    part_id = "estleg:Part2"
    part_label = f"{child_text(osa2, 'kuvatavNr') or '2. osa'} – {child_text(osa2, 'osaPealkiri') or 'ERIOSA'}"
    part_node = {
        "@id": part_id,
        "@type": ["estleg:LegalPart", "owl:NamedIndividual"],
        "rdfs:label": part_label,
        "estleg:hasChapter": [],
    }

    section_count = 0
    chapter_count = 0
    division_count = 0
    subdivision_count = 0

    for ch in [x for x in list(osa2) if ln(x.tag) == "peatykk"]:
        chapter_count += 1
        ch_nr = child_text(ch, "peatykkNr") or str(chapter_count)
        ch_id = f"estleg:Chapter{sanitize_identifier(ch_nr)}"
        part_node["estleg:hasChapter"].append({"@id": ch_id})
        ch_node: dict = {
            "@id": ch_id,
            "@type": ["estleg:Chapter", "owl:NamedIndividual"],
            "rdfs:label": f"{child_text(ch, 'kuvatavNr') or ''} – {child_text(ch, 'peatykkPealkiri') or ''}".strip(" –"),
        }

        # Collect direct paragrahvid (if no jagu)
        direct_sections = [x for x in list(ch) if ln(x.tag) == "paragrahv"]
        if direct_sections:
            ch_node["estleg:hasSection"] = []
            for p in direct_sections:
                p_nr = child_text(p, "paragrahvNr") or "?"
                p_id = f"estleg:Par{sanitize_identifier(p_nr)}"
                ch_node["estleg:hasSection"].append({"@id": p_id})
                graph.append(
                    {
                        "@id": p_id,
                        "@type": ["estleg:Section", "owl:NamedIndividual"],
                        "rdfs:label": f"{child_text(p, 'kuvatavNr') or ''} {child_text(p, 'paragrahvPealkiri') or ''}".strip(),
                        "estleg:sectionNumber": p_nr,
                        "estleg:legalText": collect_loige_preview(p),
                    }
                )
                section_count += 1

        divisions = [x for x in list(ch) if ln(x.tag) == "jagu"]
        if divisions:
            ch_node["estleg:hasDivision"] = []
            for d in divisions:
                division_count += 1
                d_nr = child_text(d, "jaguNr") or str(division_count)
                d_id = f"estleg:Division{sanitize_identifier(ch_nr)}_{sanitize_identifier(d_nr)}"
                ch_node["estleg:hasDivision"].append({"@id": d_id})

                d_node: dict = {
                    "@id": d_id,
                    "@type": ["estleg:Division", "owl:NamedIndividual"],
                    "rdfs:label": f"{child_text(d, 'kuvatavNr') or ''} – {child_text(d, 'jaguPealkiri') or ''}".strip(" –"),
                }

                # direct paragrahvid in jagu
                d_pars = [x for x in list(d) if ln(x.tag) == "paragrahv"]
                if d_pars:
                    d_node["estleg:hasSection"] = []
                    for p in d_pars:
                        p_nr = child_text(p, "paragrahvNr") or "?"
                        p_id = f"estleg:Par{sanitize_identifier(p_nr)}"
                        d_node["estleg:hasSection"].append({"@id": p_id})
                        graph.append(
                            {
                                "@id": p_id,
                                "@type": ["estleg:Section", "owl:NamedIndividual"],
                                "rdfs:label": f"{child_text(p, 'kuvatavNr') or ''} {child_text(p, 'paragrahvPealkiri') or ''}".strip(),
                                "estleg:sectionNumber": p_nr,
                                "estleg:legalText": collect_loige_preview(p),
                            }
                        )
                        section_count += 1

                subds = [x for x in list(d) if ln(x.tag) == "jaotis"]
                if subds:
                    d_node["estleg:hasSubdivision"] = []
                    for s in subds:
                        subdivision_count += 1
                        s_nr = child_text(s, "jaotisNr") or str(subdivision_count)
                        s_id = f"estleg:Subdivision{sanitize_identifier(ch_nr)}_{sanitize_identifier(d_nr)}_{sanitize_identifier(s_nr)}"
                        d_node["estleg:hasSubdivision"].append({"@id": s_id})

                        s_node: dict = {
                            "@id": s_id,
                            "@type": ["estleg:Subdivision", "owl:NamedIndividual"],
                            "rdfs:label": f"{child_text(s, 'kuvatavNr') or ''} – {child_text(s, 'jaotisPealkiri') or ''}".strip(" –"),
                            "estleg:hasSection": [],
                        }
                        for p in [x for x in list(s) if ln(x.tag) == "paragrahv"]:
                            p_nr = child_text(p, "paragrahvNr") or "?"
                            p_id = f"estleg:Par{sanitize_identifier(p_nr)}"
                            s_node["estleg:hasSection"].append({"@id": p_id})
                            graph.append(
                                {
                                    "@id": p_id,
                                    "@type": ["estleg:Section", "owl:NamedIndividual"],
                                    "rdfs:label": f"{child_text(p, 'kuvatavNr') or ''} {child_text(p, 'paragrahvPealkiri') or ''}".strip(),
                                    "estleg:sectionNumber": p_nr,
                                    "estleg:legalText": collect_loige_preview(p),
                                }
                            )
                            section_count += 1

                        graph.append(s_node)

                graph.append(d_node)

        graph.append(ch_node)

    graph.append(part_node)

    # Marta-provided legal definitions as concepts
    for concept in LEGAL_DEFINITIONS:
        graph.append(
            {
                "@id": f"{base}{concept['id']}",
                "@type": ["estleg:LegalConcept", "owl:NamedIndividual"],
                "rdfs:label": concept["label"],
                "skos:definition": concept["definition"],
                "dc:references": concept["crossReference"],
            }
        )

    doc = {
        "@context": {
            "owl": "http://www.w3.org/2002/07/owl#",
            "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#",
            "dc": "http://purl.org/dc/terms/",
            "skos": "http://www.w3.org/2004/02/skos/core#",
            "estleg": base,
            "dcterms": "http://purl.org/dc/terms/",
        },
        "@graph": graph,
    }

    out_file = out_dir / "karistusseadustik_eriosa_owl.jsonld"
    out_file.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "source_xml_url": xml_url,
        "source_globaalID": match.get("globaalID"),
        "title": title,
        "mapped_part": "2. osa (ERIOSA)",
        "counts": {
            "chapters": chapter_count,
            "divisions": division_count,
            "subdivisions": subdivision_count,
            "sections": section_count,
            "legal_definitions": len(LEGAL_DEFINITIONS),
        },
        "jsonld": str(out_file.relative_to(workspace)),
    }
    summary_path = out_dir / "karistusseadustik_eriosa_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
