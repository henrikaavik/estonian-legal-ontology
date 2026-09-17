#!/usr/bin/env python3
"""Fetch Karistusseadustik from Riigi Teataja and generate Eriosa JSON-LD mapping."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from estleg.estleg_common import CONTEXT, parse_xml
from estleg.estleg_common import sanitize_id as _shared_sanitize_id

SEARCH_URL = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
BASE_URL = "https://www.riigiteataja.ee"
# The historical ``/akt/<id>.xml`` path now serves the Riigi Teataja single-page
# app shell (HTML); the structured act XML is fetched from this public-api blob
# endpoint, keyed by globaalID. (Discovered while making the TsÜS Osa-7 generator
# reproducible — the other half of issue #567.)
BLOB_XML_URL = BASE_URL + "/public-api/api/v1/akt/{global_id}/blob-xml"
LAW_TITLE = "Karistusseadustik"

# ``estleg`` namespace IRI (the ontology base). Module-level so the
# JSON-LD ``@context`` can be built and unit-tested without the
# network-fetching ``main()`` shell.
ESTLEG_BASE = "https://w3id.org/estleg/"


def build_context() -> dict[str, str]:
    """Return the JSON-LD ``@context`` prefix map for the Eriosa artifact.

    ``dc`` is Dublin Core *elements/1.1* (matching estleg_common.py and
    generate_all_laws.py), ``dcterms`` is the distinct /dc/terms/
    namespace; conflating them breaks corpus-wide RDF joins on
    ``dc:source``/``dc:references`` (#308).
    """
    ctx = dict(CONTEXT)
    ctx["estleg"] = ESTLEG_BASE
    return ctx


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


def section_range(osa_el: ET.Element) -> tuple[int, int] | None:
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
        return None
    return min(numbers), max(numbers)


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
    return _shared_sanitize_id(_expand_superscripts(value))


# Bare superscript glyph → digit (no leading underscore), used when
# parsing a ``kuvatavNr`` that carries the superscript as a glyph.
_SUPERSCRIPT_DIGIT_MAP: dict[str, str] = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
}


def _paragraph_superscript(paragraph_el: ET.Element) -> str:
    """Return the superscript index carried by a ``paragrahv`` element.

    Riigi Teataja records superscripted section numbers in two forms and
    the bug in #251 was that this generator read *neither*:

      * ``<paragrahvNr ylaIndeks="N">133</paragrahvNr>`` — the canonical,
        machine-friendly attribute (this is what the real KarS XML uses;
        ``grep ylaIndeks`` over the script was previously empty);
      * the ``kuvatavNr`` CDATA, either as a Unicode superscript glyph
        (``§ 133¹``) or as ``<sup>N</sup>`` markup.

    Returns the index as a plain digit string ("1", "2", ...) or ``""``
    when no superscript is present. Mirrors
    ``generate_all_laws._superscript_index`` but is replicated locally so
    this generator does not depend on that module's import surface.
    """
    for c in paragraph_el:
        if ln(c.tag) == "paragrahvNr":
            ya = c.attrib.get("ylaIndeks")
            if ya and ya.strip():
                return ya.strip()
            break

    kuv = None
    for c in paragraph_el:
        if ln(c.tag) == "kuvatavNr":
            kuv = "".join(c.itertext()) or ""
            break
    if not kuv:
        return ""

    m = re.search(r"\d+\s*(?:<sup>\s*(\d+)\s*</sup>|([¹²³⁰⁴-⁹]+))", kuv)
    if not m:
        return ""
    if m.group(1):
        return m.group(1).strip()
    if m.group(2):
        return "".join(_SUPERSCRIPT_DIGIT_MAP.get(ch, "") for ch in m.group(2))
    return ""


def _paragraph_id_suffix(paragraph_el: ET.Element, p_nr: str) -> str:
    """Return the IRI suffix for a paragrahv: base number + superscript.

    ``§ 133`` → ``133``; ``§ 133¹`` (``ylaIndeks="1"``) → ``133_1``. Folding
    the superscript into the suffix keeps §133 and §133¹/§133²/§133³
    distinct so they no longer collapse to one ``@id`` and trip the
    post-build dup-guard (#251).

    The superscript is appended from a single authoritative source
    (``_paragraph_superscript``: ``ylaIndeks`` → ``kuvatavNr``). To avoid a
    double suffix when RT instead embeds the glyph directly in the
    ``paragrahvNr`` *text* (``<paragrahvNr>133¹</paragrahvNr>`` →
    ``sanitize_identifier`` would already expand it to ``133_1``), the base
    is taken from the leading run of digits in ``paragrahvNr`` only.
    """
    sup = _paragraph_superscript(paragraph_el)
    if p_nr:
        m = re.match(r"\s*(\d+)", p_nr)
        # Plain digit prefix when present (the common case); otherwise fall
        # back to the fully-sanitised value so non-numeric numbers (rare)
        # still yield a stable suffix.
        base = m.group(1) if m else sanitize_identifier(p_nr)
    else:
        base = "Unknown"
    if sup:
        return f"{base}_{sanitize_identifier(sup)}"
    return base


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


_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s+")


def _truncate_on_boundary(text: str, max_len: int) -> str:
    """Trim ``text`` to ``max_len`` chars at a sentence/word boundary.

    A raw ``text[:max_len]`` slice cuts mid-token (``…kohustatud arvut``),
    which corrupts the downstream summary that every classifier reads
    (#368). Mirrors the boundary-aware approach in
    ``generate_annotations._truncate_to_sentence``:

    1. Prefer the LAST sentence boundary (``.``/``!``/``?`` + space) that
       falls within the budget, provided it keeps at least ~70 % of the
       text (so a single short leading sentence does not gut the preview).
    2. Otherwise fall back to the last whole word and append ``…`` to
       signal the cut.

    Returns the (possibly trimmed) text with surrounding whitespace
    stripped; short inputs are returned unchanged apart from an
    ``rstrip``.
    """
    if len(text) <= max_len:
        return text.rstrip()
    head = text[:max_len]
    min_index = int(max_len * 0.7)
    # The LAST sentence stop inside the budget, if it keeps >= ~70 % of the
    # text (otherwise a short leading sentence would gut the preview).
    boundaries = [m.start() for m in _SENTENCE_BOUNDARY_RE.finditer(head)]
    if boundaries and boundaries[-1] > min_index:
        # Keep the sentence-ending punctuation, drop the trailing space.
        return head[: boundaries[-1] + 1].rstrip()
    # Word-boundary fallback: trim to the last whole word, mark the cut.
    last_space = head.rfind(" ")
    trimmed = head[:last_space] if last_space > 0 else head
    return trimmed.rstrip() + "…"


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
    return _truncate_on_boundary(joined, max_len)


def find_global_id(title: str, kehtiv: str, *, timeout: int = 30) -> str:
    """Return the globaalID of the in-force consolidated act named ``title``.

    The bare search returns every historical consolidation, so we filter to the
    snapshot in force on ``kehtiv`` (``YYYY-MM-DD``) and, among exact-title
    matches, keep the largest globaalID (the most recent). Mirrors
    ``generate_all_laws.get_all_laws``'s newest-wins selection.
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


def fetch_act_xml(title: str, kehtiv: str, *, timeout: int = 60) -> tuple[str, str, str]:
    """Fetch the structured XML for the in-force ``title``.

    Returns ``(source_url, xml_text, global_id)``. The structured XML is served
    from ``/public-api/api/v1/akt/<globaalID>/blob-xml`` — the legacy
    ``/akt/<id>.xml`` path now returns the Riigi Teataja SPA shell.
    """
    global_id = find_global_id(title, kehtiv, timeout=timeout)
    url = BLOB_XML_URL.format(global_id=global_id)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    text = resp.text
    if not text.lstrip().startswith("<?xml") and "<oigusakt" not in text[:200]:
        raise RuntimeError(f"{url} did not return act XML (got {text[:60]!r})")
    return url, text, global_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    workspace = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=workspace / "krr_outputs" / "karistusseadustik_eriosa_owl.jsonld",
        help="Output JSON-LD path (default: the canonical krr_outputs module).",
    )
    parser.add_argument(
        "--kehtiv",
        default=_dt.date.today().isoformat(),
        help="Snapshot date (YYYY-MM-DD) for the in-force consolidation "
        "(default: today).",
    )
    parser.add_argument(
        "--xml",
        type=Path,
        default=None,
        help="Read the act XML from a local file instead of fetching it "
        "(offline / testing).",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Also write the fetched act XML to this path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.xml is not None:
        xml_text = args.xml.read_text(encoding="utf-8")
        xml_url = args.xml.as_uri()
        global_id: str | None = None
    else:
        xml_url, xml_text, global_id = fetch_act_xml(LAW_TITLE, args.kehtiv)
        if args.cache is not None:
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            args.cache.write_text(xml_text, encoding="utf-8")

    root = parse_xml(xml_text)

    parsed_title = next((e.text.strip() for e in root.iter() if ln(e.tag) == "aktinimi" and e.text), "")
    title = parsed_title or LAW_TITLE

    osa2 = None
    for osa in root.iter():
        if ln(osa.tag) != "osa":
            continue
        if child_text(osa, "osaNr") == "2":
            osa2 = osa
            break
    if osa2 is None:
        raise RuntimeError("Could not find Eriosa (osaNr=2)")

    base = ESTLEG_BASE
    par_range = section_range(osa2)
    ontology_id = "estleg:KarS_Eriosa_v1"
    if par_range is None:
        print("WARNING: cannot derive KarS Eriosa section range; using stable act IRI")

    graph: list[dict] = [
        {
            "@id": ontology_id,
            "@type": ["estleg:Act", "estleg:Law"],
            "rdfs:label": "KarS Eriosa ontoloogia",
            "dc:title": f"{title} – Eriosa",
            "dc:source": title,
            "dcterms:source": {"@id": xml_url},
        },
    ]
    if par_range is not None:
        graph[0]["dcterms:extent"] = f"§{par_range[0]}-§{par_range[1]}"

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
        # 14 from collapsing to the same IRI. The paragraph suffix folds in
        # any ``ylaIndeks`` / <sup> superscript so §133 and §133¹/²/³ stay
        # distinct instead of all collapsing to ``Par133`` (#251).
        p_id = (
            f"estleg:KarS_Ch{sanitize_identifier(chapter_nr)}_"
            f"Par{_paragraph_id_suffix(paragraph_el, p_nr)}"
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
            "@type": ["estleg:Section", "estleg:LegalProvision", "owl:NamedIndividual"],
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
        "@context": build_context(),
        "@graph": graph,
    }

    out_file = args.out
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "source_xml_url": xml_url,
        "source_globaalID": global_id,
        "title": title,
        "mapped_part": "2. osa (ERIOSA)",
        "counts": {
            "chapters": chapter_count,
            "divisions": division_count,
            "subdivisions": subdivision_count,
            "sections": section_count,
            "legal_definitions": len(LEGAL_DEFINITIONS),
        },
        "jsonld": str(out_file),
    }
    # Summary lands beside the output file, so a non-default ``--out`` (e.g. a
    # /tmp comparison path) never overwrites the committed krr_outputs summary.
    summary_path = out_file.with_name("karistusseadustik_eriosa_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
