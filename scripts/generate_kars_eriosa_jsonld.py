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


def section_range(osa_el: ET.Element) -> tuple[int, int]:
    """Return the base section-number range contained in an ``osa`` element."""
    numbers: list[int] = []
    for paragrahv in osa_el.iter():
        if ln(paragrahv.tag) != "paragrahv":
            continue
        raw = child_text(paragrahv, "paragrahvNr") or ""
        match = re.match(r"\s*(\d+)", raw)
        if match:
            numbers.append(int(match.group(1)))
    if not numbers:
        raise ValueError("cannot derive section range: no paragrahvNr values found")
    return min(numbers), max(numbers)


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


# KarS often introduces interpolated paragraphs as "88¹", "88²", "88³"
# (and so on, occasionally up to ⁹). The bare ``sanitize_identifier``
# strips non-ASCII characters, so "88¹" silently becomes "88" which
# COLLIDES with paragraph 88. We map superscripts to ``_<digit>``
# BEFORE the non-ASCII strip so the full Unicode information is
# preserved as a stable ASCII suffix.
_SUPERSCRIPT_MAP: dict[str, str] = {
    "⁰": "_0", "¹": "_1", "²": "_2", "³": "_3", "⁴": "_4",
    "⁵": "_5", "⁶": "_6", "⁷": "_7", "⁸": "_8", "⁹": "_9",
}


def _expand_superscripts(value: str) -> str:
    """Replace each superscript digit with its ``_<digit>`` ASCII form.

    Preserves the semantic distinction between ``88`` and ``88¹``,
    which would otherwise both serialize to ``88`` after non-ASCII
    stripping.
    """
    if not value:
        return value
    out = value
    for src, dst in _SUPERSCRIPT_MAP.items():
        if src in out:
            out = out.replace(src, dst)
    return out


def sanitize_identifier(value: str) -> str:
    """Sanitise an XML-derived label fragment for use in an ``estleg:`` IRI.

    Order is intentional: superscripts → transliteration → ASCII strip.
    Reversing transliteration before superscript expansion would let
    ``ä¹`` lose the ¹ before mapping ä→a (the ä mapping replaces only
    the ä codepoint).
    """
    s = _expand_superscripts(value)
    s = s.translate(_TRANSLIT_TABLE)
    # Keep ``_`` so the superscript-derived suffix survives. The
    # original ``[^0-9A-Za-z]+`` pattern would have stripped them.
    s = re.sub(r"[^0-9A-Za-z_]+", "", s)
    return s or "Unknown"


def _join_label(*parts: str | None, sep: str = " – ") -> str:
    """Join non-empty label fragments with ``sep`` between them.

    Pre-fix bug: ``f"{a} – {b}".strip(" –")`` strips spaces and en-dash
    from BOTH ends, so a chapter with no title and a section with no
    Roman number would yield ``""`` (empty rdfs:label). This builder
    drops empties before the join, so the result is non-empty exactly
    when at least one fragment carries content.
    """
    cleaned = [p.strip() for p in parts if p and p.strip()]
    return sep.join(cleaned)


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
    par_min, par_max = section_range(osa2)
    ontology_id = f"estleg:KarS_Eriosa_{par_min}_{par_max}_Ontology_v1"

    graph: list[dict] = [
        {
            "@id": ontology_id,
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
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

    part_id = "estleg:KarS_Part2"
    part_label = _join_label(
        child_text(osa2, "kuvatavNr") or "2. osa",
        child_text(osa2, "osaPealkiri") or "ERIOSA",
    )
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

    def _build_paragraph(
        paragraph_el: ET.Element,
        chapter_nr: str,
    ) -> tuple[str, dict] | None:
        """Build the (id, node) pair for a paragrahv element.

        Paragraph IRIs MUST be namespaced by chapter so the same
        paragraph number reused across chapters does not collide.
        Falls back to ``kuvatavNr`` when ``paragrahvNr`` is missing.
        """
        p_nr = child_text(paragraph_el, "paragrahvNr") or "?"
        kuvatav_nr = child_text(paragraph_el, "kuvatavNr") or ""
        # Chapter-scoped IRI prevents 88¹ in chapter 9 and 88¹ in chapter
        # 14 from collapsing to the same IRI.
        p_id = (
            f"estleg:KarS_Ch{sanitize_identifier(chapter_nr)}_"
            f"Par{sanitize_identifier(p_nr)}"
        )
        # Label: prefer the kuvatavNr + pealkiri pair; fall back to
        # kuvatavNr alone (or the IRI fragment) so the label is never
        # empty.
        title = child_text(paragraph_el, "paragrahvPealkiri") or ""
        label = _join_label(kuvatav_nr, title, sep=" ")
        if not label:
            label = kuvatav_nr or f"§ {p_nr}"
        return p_id, {
            "@id": p_id,
            "@type": ["estleg:Section", "owl:NamedIndividual"],
            "rdfs:label": label,
            "estleg:sectionNumber": p_nr,
            "estleg:legalText": collect_loige_preview(paragraph_el),
        }

    for ch in [x for x in list(osa2) if ln(x.tag) == "peatykk"]:
        chapter_count += 1
        ch_nr = child_text(ch, "peatykkNr") or str(chapter_count)
        ch_id = f"estleg:KarS_Ch{sanitize_identifier(ch_nr)}"
        part_node["estleg:hasChapter"].append({"@id": ch_id})
        ch_label = _join_label(
            child_text(ch, "kuvatavNr"),
            child_text(ch, "peatykkPealkiri"),
        )
        if not ch_label:
            ch_label = child_text(ch, "kuvatavNr") or f"Peatükk {ch_nr}"
        ch_node: dict = {
            "@id": ch_id,
            "@type": ["estleg:Chapter", "owl:NamedIndividual"],
            "rdfs:label": ch_label,
        }

        # Collect direct paragrahvid (if no jagu)
        direct_sections = [x for x in list(ch) if ln(x.tag) == "paragrahv"]
        if direct_sections:
            ch_node["estleg:hasSection"] = []
            for p in direct_sections:
                built = _build_paragraph(p, ch_nr)
                if built is None:
                    continue
                p_id, p_node = built
                ch_node["estleg:hasSection"].append({"@id": p_id})
                graph.append(p_node)
                section_count += 1

        divisions = [x for x in list(ch) if ln(x.tag) == "jagu"]
        if divisions:
            ch_node["estleg:hasDivision"] = []
            for d in divisions:
                division_count += 1
                d_nr = child_text(d, "jaguNr") or str(division_count)
                d_id = (
                    f"estleg:KarS_Ch{sanitize_identifier(ch_nr)}"
                    f"_Div{sanitize_identifier(d_nr)}"
                )
                ch_node["estleg:hasDivision"].append({"@id": d_id})

                d_label = _join_label(
                    child_text(d, "kuvatavNr"),
                    child_text(d, "jaguPealkiri"),
                )
                if not d_label:
                    d_label = (
                        child_text(d, "kuvatavNr") or f"Jagu {d_nr}"
                    )
                d_node: dict = {
                    "@id": d_id,
                    "@type": ["estleg:Division", "owl:NamedIndividual"],
                    "rdfs:label": d_label,
                }

                # direct paragrahvid in jagu
                d_pars = [x for x in list(d) if ln(x.tag) == "paragrahv"]
                if d_pars:
                    d_node["estleg:hasSection"] = []
                    for p in d_pars:
                        built = _build_paragraph(p, ch_nr)
                        if built is None:
                            continue
                        p_id, p_node = built
                        d_node["estleg:hasSection"].append({"@id": p_id})
                        graph.append(p_node)
                        section_count += 1

                subds = [x for x in list(d) if ln(x.tag) == "jaotis"]
                if subds:
                    d_node["estleg:hasSubdivision"] = []
                    for s in subds:
                        subdivision_count += 1
                        s_nr = child_text(s, "jaotisNr") or str(subdivision_count)
                        s_id = (
                            f"estleg:KarS_Ch{sanitize_identifier(ch_nr)}"
                            f"_Div{sanitize_identifier(d_nr)}"
                            f"_Sub{sanitize_identifier(s_nr)}"
                        )
                        d_node["estleg:hasSubdivision"].append({"@id": s_id})

                        s_label = _join_label(
                            child_text(s, "kuvatavNr"),
                            child_text(s, "jaotisPealkiri"),
                        )
                        if not s_label:
                            s_label = (
                                child_text(s, "kuvatavNr")
                                or f"Jaotis {s_nr}"
                            )
                        s_node: dict = {
                            "@id": s_id,
                            "@type": ["estleg:Subdivision", "owl:NamedIndividual"],
                            "rdfs:label": s_label,
                            "estleg:hasSection": [],
                        }
                        for p in [x for x in list(s) if ln(x.tag) == "paragrahv"]:
                            built = _build_paragraph(p, ch_nr)
                            if built is None:
                                continue
                            p_id, p_node = built
                            s_node["estleg:hasSection"].append({"@id": p_id})
                            graph.append(p_node)
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

    # Post-build invariant: all @id values in the @graph must be unique.
    # Duplicates would cause merge ambiguity in RDF stores; we abort
    # rather than silently emit a corrupt JSON-LD file.
    seen_ids: dict[str, int] = {}
    for node in graph:
        node_id = node.get("@id")
        if not node_id:
            continue
        seen_ids[node_id] = seen_ids.get(node_id, 0) + 1
    duplicates = {nid: count for nid, count in seen_ids.items() if count > 1}
    if duplicates:
        raise RuntimeError(
            "generate_kars_eriosa_jsonld: duplicate @id detected — refusing "
            f"to emit a corrupt graph. Offending ids: {sorted(duplicates)}"
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
