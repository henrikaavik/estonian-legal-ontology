#!/usr/bin/env python3
"""Fetch Tsiviilseadustiku üldosa seadus (TsÜS) from Riigi Teataja and generate
the Osa-7 (§§ 138-169, "Tsiviilõiguste teostamine") OWL module.

This is the generator for ``krr_outputs/tsus_osa7_138_169_owl.jsonld``, which
shipped hand-built with no reproducible builder (the TsÜS half of issue #567).
It is modelled on :mod:`generate_kars_eriosa_jsonld` (and reuses its
identifier/label helpers): it reads the live Riigi Teataja XML, restricts to
Osa 7, and emits the same module schema — the LegalPart → Chapter → Division →
Section hierarchy, per-lõige ``estleg:LegalProvision`` nodes, the ``owl:Class`` /
``owl:ObjectProperty`` TBox, and the curated ``estleg:LegalConcept`` individuals
with their per-section ``estleg:coversConcept`` mapping.

Two layers make up the module:

* **Curated layer** (embedded constants): the concept individuals, the
  per-section ``coversConcept`` mapping, the EuroVoc ``dcterms:subject`` set, the
  class/property TBox and the ontology header. These are editorial semantics
  that are *not* present in the Riigi Teataja XML, so — exactly as the KarS
  generator embeds its ``LEGAL_DEFINITIONS`` — they are carried verbatim from the
  hand-built module.
* **Derived layer** (from RT): the Part/Chapter/Division/Section hierarchy, the
  section titles and numbers, and every provision's ``estleg:legalText``.

``owl:sameAs`` is deliberately NOT emitted: those bridges to the canonical
provisions are (re-)applied by :mod:`link_owl_modules_565` as a separate pass.

Network note: the historical ``/akt/<id>.xml`` endpoint now serves the Riigi
Teataja single-page app shell; the structured XML is fetched from the
``/public-api/api/v1/akt/<globaalID>/blob-xml`` endpoint instead. Parsing uses
the standard-library ``xml.etree.ElementTree`` for consistency with every other
generator in this repository (``riigiteataja_common``, ``generate_all_laws``,
``generate_kars_eriosa_jsonld``, …); the source is a trusted government HTTPS
endpoint.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

# Reuse the KarS generator's pure identifier/label helpers (issue #567 asks to
# reuse them where applicable). They are module-level and import-safe — the
# network-touching ``main()`` is guarded by ``if __name__ == "__main__"``.
from estleg_common import CONTEXT, parse_xml
from generate_kars_eriosa_jsonld import (  # sibling script, resolved via sys.path
    _join_label,
    child_text,
    ln,
    sanitize_identifier,
)

SEARCH_URL = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
BLOB_XML_URL = "https://www.riigiteataja.ee/public-api/api/v1/akt/{global_id}/blob-xml"
LAW_TITLE = "Tsiviilseadustiku üldosa seadus"
TARGET_OSA = "7"

# The estleg ontology base IRI. The committed module addresses every node by its
# FULL IRI (``https://w3id.org/estleg/…``) except the ontology root,
# which uses the compact ``estleg:`` form; we reproduce that split exactly.
NS = "https://w3id.org/estleg/"

OUT_DEFAULT = (
    Path(__file__).resolve().parents[1] / "krr_outputs" / "tsus_osa7_138_169_owl.jsonld"
)

# ---------------------------------------------------------------------------
# Curated layer (verbatim from the hand-built module — not in the RT XML)
# ---------------------------------------------------------------------------

# JSON-LD @context — imported from estleg_common (#465).

ROOT_ID = "estleg:TsUS_Osa7_v1"
ROOT_LABEL = "TsÜS Osa 7 ontoloogia"

# EuroVoc subjects on the ontology header.
# #421: verified EuroVoc descriptor ids (see data/eurovoc_domain_mapping.json).
# Generic "Law" (2411) is omitted — it matched every legal act.
EUROVOC_SUBJECTS: list[dict] = [
    {"@id": "http://eurovoc.europa.eu/523", "rdfs:label": "tsiviilõigus",
     "skos:prefLabel": "civil law"},
    {"@id": "http://eurovoc.europa.eu/10", "rdfs:label": "sisekaubandus",
     "skos:prefLabel": "domestic trade"},
    {"@id": "http://eurovoc.europa.eu/75", "rdfs:label": "konkurents",
     "skos:prefLabel": "competition"},
    {"@id": "http://eurovoc.europa.eu/538", "rdfs:label": "põhiõigused",
     "skos:prefLabel": "fundamental rights"},
    {"@id": "http://eurovoc.europa.eu/217", "rdfs:label": "õigusalane koostöö",
     "skos:prefLabel": "judicial cooperation"},
]

# owl:Class TBox (LegalPart … LegalConcept). NB: ``Provision`` is declared as the
# range of ``hasProvision`` even though individuals are typed ``LegalProvision`` —
# this faithfully mirrors the committed module (the TBox-completeness half of
# #567 is a separate, deferred concern and is intentionally not "fixed" here).
CLASS_DECLS: list[tuple[str, str]] = [
    ("LegalPart", "Seaduse osa"),
    ("Chapter", "Peatükk"),
    ("Division", "Jagu"),
    ("Section", "Paragrahv"),
    ("Provision", "Lõige/punkt"),
    ("LegalConcept", "Õigusmõiste"),
]

# owl:ObjectProperty TBox: fragment -> (label, domain-fragment, range-fragment).
PROPERTY_DECLS: list[tuple[str, str, str, str]] = [
    ("hasChapter", "sisaldab peatükki", "LegalPart", "Chapter"),
    ("hasDivision", "sisaldab jagu", "Chapter", "Division"),
    ("hasSection", "sisaldab paragrahvi", "Division", "Section"),
    ("hasProvision", "sisaldab normiteksti", "Section", "Provision"),
    ("coversConcept", "katab mõistet", "Section", "LegalConcept"),
]

# LegalConcept individuals. The committed module emits three cross-cutting
# concepts BEFORE the hierarchy and five aegumine-specific concepts AFTER the
# sections; the split + ordering is reproduced.
CONCEPTS_TOP: list[tuple[str, str]] = [
    ("ÕigusteKaitsmine", "Õiguste kaitsmine"),
    ("ÕigusteEnnetamine", "Õiguste ennetamine"),
    ("Aegumine", "Aegumine"),
]
CONCEPTS_BOTTOM: list[tuple[str, str]] = [
    ("AegumiseTagajärjed", "Aegumise tagajärjed"),
    ("TehingustTulenevaNoudeAegumine", "Tehingust tuleneva nõude aegumine"),
    ("SeadusestTulenevaNoudeAegumine", "Seadusest tuleneva nõude aegumine"),
    ("AegumineErijuhtudel", "Aegumine erijuhtudel"),
    ("AegumiseKatkemineJaPeatumine", "Aegumise katkemine ja peatumine"),
]

# Per-section coversConcept mapping (section local-id -> ordered concept frags).
SECTION_CONCEPTS: dict[str, list[str]] = {
    "TsUS_Par_138": ["ÕigusteEnnetamine"],
    "TsUS_Par_139": ["ÕigusteEnnetamine"],
    "TsUS_Par_140": ["ÕigusteEnnetamine", "ÕigusteKaitsmine"],
    "TsUS_Par_141": ["ÕigusteEnnetamine", "ÕigusteKaitsmine"],
    "TsUS_Par_142": ["Aegumine", "AegumiseTagajärjed"],
    "TsUS_Par_143": ["Aegumine", "AegumiseTagajärjed", "ÕigusteKaitsmine"],
    "TsUS_Par_144": ["Aegumine", "AegumiseTagajärjed"],
    "TsUS_Par_145": ["Aegumine", "AegumiseTagajärjed"],
    "Par145_1": ["Aegumine", "AegumiseTagajärjed"],
    "TsUS_Par_146": ["Aegumine", "TehingustTulenevaNoudeAegumine"],
    "TsUS_Par_147": ["Aegumine", "TehingustTulenevaNoudeAegumine"],
    "TsUS_Par_148": ["Aegumine", "ÕigusteEnnetamine", "TehingustTulenevaNoudeAegumine"],
    "TsUS_Par_149": ["Aegumine", "SeadusestTulenevaNoudeAegumine"],
    "TsUS_Par_150": ["Aegumine", "SeadusestTulenevaNoudeAegumine"],
    "TsUS_Par_151": ["Aegumine", "SeadusestTulenevaNoudeAegumine"],
    "TsUS_Par_152": ["Aegumine", "SeadusestTulenevaNoudeAegumine"],
    "TsUS_Par_153": ["Aegumine", "AegumineErijuhtudel"],
    "TsUS_Par_154": ["Aegumine", "AegumineErijuhtudel"],
    "TsUS_Par_155": ["Aegumine", "AegumineErijuhtudel"],
    "TsUS_Par_156": ["Aegumine", "AegumineErijuhtudel"],
    "TsUS_Par_157": ["Aegumine", "AegumineErijuhtudel"],
    "TsUS_Par_158": ["Aegumine", "AegumiseKatkemineJaPeatumine"],
    "TsUS_Par_159": ["Aegumine", "AegumiseKatkemineJaPeatumine", "ÕigusteKaitsmine"],
    "TsUS_Par_160": ["Aegumine", "ÕigusteKaitsmine", "AegumiseKatkemineJaPeatumine"],
    "TsUS_Par_161": ["Aegumine", "ÕigusteKaitsmine", "AegumiseKatkemineJaPeatumine"],
    "TsUS_Par_162": ["Aegumine", "AegumiseKatkemineJaPeatumine", "ÕigusteEnnetamine"],
    "TsUS_Par_163": ["Aegumine", "AegumiseKatkemineJaPeatumine"],
    "TsUS_Par_164": ["Aegumine", "AegumiseKatkemineJaPeatumine"],
    "TsUS_Par_165": ["Aegumine", "AegumiseKatkemineJaPeatumine"],
    "TsUS_Par_166": ["Aegumine", "AegumiseKatkemineJaPeatumine"],
    "TsUS_Par_167": ["Aegumine", "ÕigusteEnnetamine", "AegumiseKatkemineJaPeatumine"],
    "TsUS_Par_168": ["AegumiseKatkemineJaPeatumine", "Aegumine"],
    "TsUS_Par_169": ["Aegumine", "AegumiseKatkemineJaPeatumine"],
}

# Fallback coversConcept by division-title concept, for sections introduced by
# amendments AFTER the hand-built snapshot (e.g. §160¹/§167¹) that have no
# curated entry. The division's own theme + the umbrella "Aegumine".
DIVISION_DEFAULT_CONCEPTS: dict[str, list[str]] = {
    "Division1": ["Aegumine", "AegumiseTagajärjed"],
    "Division2": ["Aegumine", "TehingustTulenevaNoudeAegumine"],
    "Division3": ["Aegumine", "SeadusestTulenevaNoudeAegumine"],
    "Division4": ["Aegumine", "AegumineErijuhtudel"],
    "Division5": ["Aegumine", "AegumiseKatkemineJaPeatumine"],
}


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


def find_global_id(title: str, kehtiv: str, *, timeout: int = 30) -> str:
    """Return the globaalID of the in-force consolidated act named ``title``.

    The bare search returns every historical consolidation, so we filter to the
    snapshot in force on ``kehtiv`` (``YYYY-MM-DD``) and, among exact-title
    matches, keep the largest globaalID (the most recent), mirroring
    ``generate_all_laws.get_all_laws``.
    """
    resp = requests.get(
        SEARCH_URL,
        params={
            "leht": 1,
            "dokument": "seadus",
            "tekst": "terviktekst",
            "pealkiri": title,
            "kehtiv": kehtiv,
            "kehtivKehtetus": "false",
            "mitteJoustunud": "false",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    best: str | None = None
    for act in resp.json().get("aktid", []):
        if (act.get("pealkiri") or "").strip().lower() != title.lower():
            continue
        gid = str(act.get("globaalID") or "")
        if gid and (best is None or gid > best):
            best = gid
    if not best:
        raise RuntimeError(f"{title!r} not found in Riigi Teataja search (kehtiv={kehtiv})")
    return best


def fetch_act_xml(title: str, kehtiv: str, *, timeout: int = 60) -> tuple[str, str]:
    """Fetch the structured XML for the in-force ``title``; return (url, xml)."""
    global_id = find_global_id(title, kehtiv, timeout=timeout)
    url = BLOB_XML_URL.format(global_id=global_id)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    text = resp.text
    if not text.lstrip().startswith("<?xml") and "<oigusakt" not in text[:200]:
        raise RuntimeError(f"{url} did not return act XML (got {text[:60]!r})")
    return url, text


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def child(el: ET.Element, name: str) -> ET.Element | None:
    """Return the first direct child element with local tag ``name``, or None."""
    for c in el:
        if ln(c.tag) == name:
            return c
    return None


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# Riigi Teataja encodes an interpolated/superscript index two ways, even within
# the same document: as a REAL ``<sup>`` child element (inside ``tavatekst``) and
# as an ESCAPED literal ``&lt;sup&gt;…&lt;/sup&gt;`` substring (inside
# ``kuvatavNr``). Both are normalised to a ``^N`` ASCII marker so e.g. ``§ 145¹``
# renders ``§ 145^1.`` and a cross-reference to lõige ``1¹`` renders ``1^1``.
_SUP_LITERAL_RE = re.compile(r"<sup>(.*?)</sup>")


def _inline_text(el: ET.Element) -> str:
    """Flatten an element's mixed content, rendering ``<sup>`` children as ``^N``."""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for c in el:
        inner = _inline_text(c)
        parts.append(f"^{inner}" if ln(c.tag) == "sup" else inner)
        if c.tail:
            parts.append(c.tail)
    return "".join(parts)


def _render_text(el: ET.Element | None) -> str:
    """Whitespace-collapsed text of ``el`` with both ``<sup>`` forms → ``^N``."""
    if el is None:
        return ""
    return _collapse(_SUP_LITERAL_RE.sub(r"^\1", _inline_text(el)))


def _superscript_of(el: ET.Element, nr_tag: str) -> str:
    """Return the ``ylaIndeks`` superscript on a paragraph/lõige number, or "".

    Riigi Teataja records an interpolated number (``§ 145¹``, lõige ``(1¹)``) as
    ``<...Nr ylaIndeks="1">145</...Nr>``. ``nr_tag`` is the number child to read
    (``paragrahvNr`` / ``loigeNr``).
    """
    nr = child(el, nr_tag)
    if nr is not None:
        ya = (nr.attrib.get("ylaIndeks") or "").strip()
        if ya:
            return ya
    return ""


def _render_sisutekst(sisu: ET.Element) -> str:
    """Render a ``sisuTekst`` body: its ``tavatekst`` plus any ``alampunkt``
    sub-points, with ``muutmismarge`` amendment trailers excluded.

    Excluding ``muutmismarge`` is what keeps the legal text clean — otherwise the
    nested ``RT I YYYY …`` enactment reference leaks into ``estleg:legalText``
    (visible verbatim in the hand-built module's *absence* of such trailers).
    """
    parts: list[str] = []
    for c in sisu:
        tag = ln(c.tag)
        if tag == "tavatekst":
            parts.append(_render_text(c))
        elif tag == "alampunkt":
            parts.append(_render_alampunkt(c))
        # muutmismarge / avaldamismarge / joustumine: skip (enactment metadata)
    return " ".join(p for p in parts if p)


def _render_alampunkt(ap: ET.Element) -> str:
    """Render an ``alampunkt`` sub-point as ``<kuvatavNr> <body>``."""
    parts: list[str] = []
    kuv = _render_text(child(ap, "kuvatavNr"))
    if kuv:
        parts.append(kuv)
    sisu = child(ap, "sisuTekst")
    if sisu is not None:
        parts.append(_render_sisutekst(sisu))
    return " ".join(p for p in parts if p)


def provision_text(loige: ET.Element) -> str:
    """Return the ``estleg:legalText`` for a ``loige`` element.

    ``<lõige kuvatavNr> <body>`` — e.g. ``(1) Õiguste teostamisel …``. A single
    unnumbered lõige has no ``kuvatavNr`` and yields just the body; a repealed
    lõige (only a ``muutmismarge``, no ``sisuTekst``) yields just ``(N)``.
    """
    parts: list[str] = []
    kuv = _render_text(child(loige, "kuvatavNr"))
    if kuv:
        parts.append(kuv)
    sisu = child(loige, "sisuTekst")
    if sisu is not None:
        body = _render_sisutekst(sisu)
        if body:
            parts.append(body)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Identifier / label construction
# ---------------------------------------------------------------------------


def section_ids(par_nr: str, sup: str) -> tuple[str, str, str]:
    """Return ``(section_local_id, par_core, display_nr)`` for a paragraph.

    * regular ``§ 145`` -> ``("TsUS_Par_145", "145", "145")``
    * superscript ``§ 145¹`` -> ``("Par145_1", "145_1", "145^1")``

    ``par_core`` is the stem used for provision ids (``Par<core>_Lg<n>``);
    ``display_nr`` is the ``^``-superscript form used in labels. The asymmetric
    id scheme (regular sections carry the ``TsUS_Par_`` prefix, superscript ones
    the bare ``Par`` prefix) reproduces the hand-built module's irregular ids.
    """
    base = sanitize_identifier(par_nr)
    if sup:
        core = f"{base}_{sanitize_identifier(sup)}"
        return f"Par{core}", core, f"{base}^{sup}"
    return f"TsUS_Par_{base}", base, base


def provision_id_suffix(loige: ET.Element, position: int) -> tuple[str, str]:
    """Return ``(id_suffix, label_suffix)`` for a lõige.

    ``loigeNr`` drives the suffix (``Lg2``); a lõige superscript appends it
    (``Lg1_1`` / label ``1^1``); an unnumbered single lõige falls back to its
    1-based ``position`` so the id is always present and unique.
    """
    nr = child_text(loige, "loigeNr") or str(position)
    nr = sanitize_identifier(nr)
    lsup = _superscript_of(loige, "loigeNr")
    if lsup:
        lsup = sanitize_identifier(lsup)
        return f"{nr}_{lsup}", f"{nr}^{lsup}"
    return nr, nr


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def find_osa(root: ET.Element, osa_nr: str) -> ET.Element | None:
    for osa in root.iter():
        if ln(osa.tag) == "osa" and child_text(osa, "osaNr") == osa_nr:
            return osa
    return None


def _concepts_for(section_local_id: str, container_id: str | None) -> list[str]:
    """Ordered coversConcept fragments for a section.

    Curated map first; for amendment-added sections absent from it, fall back to
    the section's division-theme default (or [] when directly under a chapter).
    """
    if section_local_id in SECTION_CONCEPTS:
        return SECTION_CONCEPTS[section_local_id]
    if container_id in DIVISION_DEFAULT_CONCEPTS:
        return DIVISION_DEFAULT_CONCEPTS[container_id]
    return []


def build_section(
    paragraph: ET.Element,
    container_key: str,
    container_id: str,
    graph: list[dict],
) -> str:
    """Append a section's provision nodes + the section node to ``graph``.

    ``container_key`` is ``"inChapter"`` or ``"inDivision"`` and ``container_id``
    the local id of the enclosing chapter/division. Returns the section's local
    id (for the parent's hasSection list).
    """
    par_nr = child_text(paragraph, "paragrahvNr") or "0"
    sup = _superscript_of(paragraph, "paragrahvNr")
    section_local_id, par_core, display_nr = section_ids(par_nr, sup)

    kuvatav = _render_text(child(paragraph, "kuvatavNr"))
    title = child_text(paragraph, "paragrahvPealkiri") or ""
    label = _join_label(kuvatav, title, sep=" ") or kuvatav or f"§ {par_nr}"

    provisions: list[dict] = []
    refs: list[dict] = []
    for position, loige in enumerate(
        (c for c in paragraph if ln(c.tag) == "loige"), start=1
    ):
        id_suffix, label_suffix = provision_id_suffix(loige, position)
        prov_id = f"{NS}Par{par_core}_Lg{id_suffix}"
        provisions.append(
            {
                "@id": prov_id,
                "@type": ["estleg:LegalProvision", "owl:NamedIndividual"],
                "rdfs:label": f"§{display_nr} lg {label_suffix}",
                "estleg:legalText": provision_text(loige),
            }
        )
        refs.append({"@id": prov_id})

    # Provisions are emitted BEFORE their section node (committed order).
    graph.extend(provisions)

    section_node: dict = {
        "@id": f"{NS}{section_local_id}",
        "@type": ["estleg:Section", "owl:NamedIndividual"],
        "estleg:sectionNumber": par_nr,
        "rdfs:label": label,
        "estleg:hasProvision": refs,
        f"estleg:{container_key}": {"@id": f"{NS}{container_id}"},
    }
    concepts = _concepts_for(section_local_id, container_id if container_key == "inDivision" else None)
    if concepts:
        section_node["estleg:coversConcept"] = [{"@id": f"{NS}{c}"} for c in concepts]
    graph.append(section_node)
    return section_local_id


def build_graph(osa: ET.Element) -> list[dict]:
    """Assemble the full @graph node list from the Osa-7 subtree."""
    graph: list[dict] = []

    # 1. ontology header --------------------------------------------------
    section_numbers = [
        int(m.group(1))
        for p in osa.iter()
        if ln(p.tag) == "paragrahv"
        for m in [re.match(r"\s*(\d+)", child_text(p, "paragrahvNr") or "")]
        if m
    ]
    lo, hi = (min(section_numbers), max(section_numbers)) if section_numbers else (0, 0)
    osa_title = child_text(osa, "osaPealkiri") or "Tsiviilõiguste teostamine"
    graph.append(
        {
            "@id": ROOT_ID,
            "@type": ["estleg:Act", "estleg:Law", "owl:Ontology"],
            "rdfs:label": ROOT_LABEL,
            "dc:title": f"{osa_title.capitalize()} (§{lo}-{hi})",
            "dcterms:subject": EUROVOC_SUBJECTS,
        }
    )

    # 2. owl:Class TBox ---------------------------------------------------
    for frag, label in CLASS_DECLS:
        graph.append({"@id": f"{NS}{frag}", "@type": ["owl:Class"], "rdfs:label": label})

    # 3. owl:ObjectProperty TBox -----------------------------------------
    for frag, label, dom, rng in PROPERTY_DECLS:
        graph.append(
            {
                "@id": f"{NS}{frag}",
                "@type": ["owl:ObjectProperty"],
                "rdfs:label": label,
                "rdfs:domain": {"@id": f"{NS}{dom}"},
                "rdfs:range": {"@id": f"{NS}{rng}"},
            }
        )

    # 4. cross-cutting concepts (before the hierarchy) -------------------
    for frag, label in CONCEPTS_TOP:
        graph.append(_concept_node(frag, label))

    # 5. Part + Chapters + Divisions + Sections --------------------------
    part_nr = child_text(osa, "osaNr") or TARGET_OSA
    part_id = f"Part{sanitize_identifier(part_nr)}"
    part_kuvatav = child_text(osa, "kuvatavNr") or f"{part_nr}. osa"
    part_node = {
        "@id": f"{NS}{part_id}",
        "@type": ["estleg:LegalPart", "owl:NamedIndividual"],
        "rdfs:label": f"TsÜS {part_kuvatav} – {osa_title.capitalize()}",
        "estleg:hasChapter": [],
    }

    chapter_nodes: list[dict] = []
    division_nodes: list[dict] = []
    section_block: list[dict] = []
    division_seq = 0

    for chapter in (c for c in osa if ln(c.tag) == "peatykk"):
        ch_nr = child_text(chapter, "peatykkNr") or "0"
        ch_id = f"TsUS_Chapter_{sanitize_identifier(ch_nr)}"
        part_node["estleg:hasChapter"].append({"@id": f"{NS}{ch_id}"})
        ch_node: dict = {
            "@id": f"{NS}{ch_id}",
            "@type": ["estleg:Chapter", "owl:NamedIndividual"],
            "rdfs:label": _join_label(
                child_text(chapter, "kuvatavNr"), child_text(chapter, "peatykkPealkiri")
            ),
        }

        direct_sections = [c for c in chapter if ln(c.tag) == "paragrahv"]
        divisions = [c for c in chapter if ln(c.tag) == "jagu"]

        if direct_sections:
            ch_node["estleg:hasSection"] = []
            for paragraph in direct_sections:
                sid = build_section(paragraph, "inChapter", ch_id, section_block)
                ch_node["estleg:hasSection"].append({"@id": f"{NS}{sid}"})

        if divisions:
            ch_node["estleg:hasDivision"] = []
            for division in divisions:
                division_seq += 1
                d_nr = child_text(division, "jaguNr") or str(division_seq)
                d_id = f"Division{sanitize_identifier(d_nr)}"
                ch_node["estleg:hasDivision"].append({"@id": f"{NS}{d_id}"})
                d_node: dict = {
                    "@id": f"{NS}{d_id}",
                    "@type": ["estleg:Division", "owl:NamedIndividual"],
                    "rdfs:label": _join_label(
                        child_text(division, "kuvatavNr"),
                        child_text(division, "jaguPealkiri"),
                    ),
                    "estleg:inChapter": {"@id": f"{NS}{ch_id}"},
                    "estleg:hasSection": [],
                }
                for paragraph in (c for c in division if ln(c.tag) == "paragrahv"):
                    sid = build_section(paragraph, "inDivision", d_id, section_block)
                    d_node["estleg:hasSection"].append({"@id": f"{NS}{sid}"})
                division_nodes.append(d_node)

        chapter_nodes.append(ch_node)

    graph.append(part_node)
    graph.extend(chapter_nodes)
    graph.extend(division_nodes)
    graph.extend(section_block)

    # 6. aegumine-specific concepts (after the sections) -----------------
    for frag, label in CONCEPTS_BOTTOM:
        graph.append(_concept_node(frag, label))

    _assert_unique_ids(graph)
    return graph


def _concept_node(frag: str, label: str) -> dict:
    return {
        "@id": f"{NS}{frag}",
        "@type": ["estleg:LegalConcept", "owl:NamedIndividual"],
        "rdfs:label": label,
    }


def _assert_unique_ids(graph: list[dict]) -> None:
    """Abort on a duplicate @id rather than emit a merge-ambiguous graph."""
    seen: dict[str, int] = {}
    for node in graph:
        nid = node.get("@id")
        if nid:
            seen[nid] = seen.get(nid, 0) + 1
    dups = sorted(nid for nid, n in seen.items() if n > 1)
    if dups:
        raise RuntimeError(f"duplicate @id detected — refusing to emit: {dups}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=OUT_DEFAULT,
        help=f"Output JSON-LD path (default: {OUT_DEFAULT}).",
    )
    parser.add_argument(
        "--kehtiv", default=_dt.date.today().isoformat(),
        help="Snapshot date (YYYY-MM-DD) for the in-force consolidation "
        "(default: today).",
    )
    parser.add_argument(
        "--xml", type=Path, default=None,
        help="Read the act XML from a local file instead of fetching it "
        "(offline / testing).",
    )
    parser.add_argument(
        "--cache", type=Path, default=None,
        help="Also write the fetched act XML to this path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.xml is not None:
        xml_text = args.xml.read_text(encoding="utf-8")
        source_url = args.xml.as_uri()
    else:
        source_url, xml_text = fetch_act_xml(LAW_TITLE, args.kehtiv)
        if args.cache is not None:
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            args.cache.write_text(xml_text, encoding="utf-8")

    root = parse_xml(xml_text)
    osa = find_osa(root, TARGET_OSA)
    if osa is None:
        raise RuntimeError(f"Osa {TARGET_OSA} not found in {LAW_TITLE}")

    graph = build_graph(osa)
    doc = {"@context": CONTEXT, "@graph": graph}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    sections = sum(1 for n in graph if "estleg:Section" in n.get("@type", []))
    provisions = sum(1 for n in graph if "estleg:LegalProvision" in n.get("@type", []))
    print(
        json.dumps(
            {
                "source": source_url,
                "out": str(args.out),
                "nodes": len(graph),
                "sections": sections,
                "provisions": provisions,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
