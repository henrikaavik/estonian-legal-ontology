"""Tests for ``scripts/generate_kars_eriosa_jsonld.py``.

Covers the regression fixes called out in issue #177:

1. Paragraph IDs MUST be namespaced by chapter so the same paragraph
   number reused across chapters does not collide.
2. Superscript paragraph numbers (``88¹``, ``88²``) MUST be preserved
   via a ``_<digit>`` transliteration map so they cannot collapse to
   their base number after non-ASCII stripping.
3. ``rdfs:label`` MUST be non-empty even when one of the joined parts
   is missing — the legacy ``.strip(" –")`` corrupted both empty
   chapters AND the en-dashes inside legitimate labels.
4. Post-build, all ``@id`` values in the ``@graph`` MUST be unique.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from estleg import generate_kars_eriosa_jsonld as gke

# ---------------------------------------------------------------------------
# sanitize_identifier
# ---------------------------------------------------------------------------

class TestSanitizeIdentifier:
    def test_plain_digits_pass_through(self) -> None:
        assert gke.sanitize_identifier("113") == "113"

    def test_superscript_one_distinguishable_from_plain_digit(self) -> None:
        """Pre-fix bug: ``88¹`` would collapse to ``881`` (collision with
        real paragraph 881) OR to ``88`` (collision with paragraph 88).
        Post-fix: ``88¹`` becomes ``88_1`` — distinct from both.
        """
        sanitized_88 = gke.sanitize_identifier("88")
        sanitized_881 = gke.sanitize_identifier("881")
        sanitized_88_super = gke.sanitize_identifier("88¹")
        assert sanitized_88 == "88"
        assert sanitized_881 == "881"
        assert sanitized_88_super == "88_1"
        # The three values must be mutually distinct.
        assert len({sanitized_88, sanitized_881, sanitized_88_super}) == 3

    def test_all_superscripts_mapped(self) -> None:
        for digit, expected in (
            ("⁰", "_0"), ("¹", "_1"), ("²", "_2"), ("³", "_3"),
            ("⁴", "_4"), ("⁵", "_5"), ("⁶", "_6"), ("⁷", "_7"),
            ("⁸", "_8"), ("⁹", "_9"),
        ):
            assert gke.sanitize_identifier(f"100{digit}") == f"100{expected}"

    def test_estonian_diacritics_transliterate(self) -> None:
        assert gke.sanitize_identifier("Õigus") == "Oigus"

    def test_returns_unknown_for_pure_garbage(self) -> None:
        assert gke.sanitize_identifier("//-?") == "Unknown"

    def test_empty_input_yields_unknown(self) -> None:
        assert gke.sanitize_identifier("") == "Unknown"


# ---------------------------------------------------------------------------
# _join_label
# ---------------------------------------------------------------------------

class TestJoinLabel:
    def test_both_present(self) -> None:
        assert gke._join_label("8. peatükk", "Tervisevastased süüteod") == (
            "8. peatükk – Tervisevastased süüteod"
        )

    def test_first_only(self) -> None:
        assert gke._join_label("8. peatükk", "") == "8. peatükk"

    def test_second_only(self) -> None:
        assert gke._join_label("", "Tervisevastased süüteod") == (
            "Tervisevastased süüteod"
        )

    def test_both_empty_yields_empty(self) -> None:
        """Pre-fix bug: ``f"{a} – {b}".strip(" –")`` returned ``""`` here,
        masking that both inputs were missing. Post-fix returns ``""``
        explicitly so callers can distinguish missing data and fall
        back to ``kuvatavNr``."""
        assert gke._join_label("", "") == ""
        assert gke._join_label(None, None) == ""

    def test_strip_safely_does_not_eat_internal_dashes(self) -> None:
        """Pre-fix bug: ``.strip(' –')`` on the OUTSIDE of a join would
        eat trailing dashes when title ended in a dash. The new
        implementation strips per-fragment and joins; internal dashes
        survive."""
        result = gke._join_label("Number-1", "Title-2")
        assert result == "Number-1 – Title-2"


class TestSectionRange:
    def test_derives_base_section_range_metadata(self) -> None:
        root = ET.fromstring(_FAKE_KARS_XML)
        osa2 = next(
            osa
            for osa in root.iter()
            if gke.ln(osa.tag) == "osa"
            and gke.child_text(osa, "osaNr") == "2"
        )

        assert gke.section_range(osa2) == (88, 113)

    def test_empty_osa_returns_none_for_stable_act_iri_fallback(self) -> None:
        osa = ET.fromstring("<osa><osaNr>2</osaNr></osa>")

        assert gke.section_range(osa) is None


# ---------------------------------------------------------------------------
# Integration: build a fake KarS XML and verify graph invariants
# ---------------------------------------------------------------------------

# Minimal-but-realistic KarS Eriosa fragment with two chapters that
# BOTH contain a paragraph "88¹" — the structural collision case the
# pre-fix code missed.
_FAKE_KARS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <metaandmed><aktinimi>Karistusseadustik</aktinimi></metaandmed>
  <osa>
    <osaNr>2</osaNr>
    <kuvatavNr>2. osa</kuvatavNr>
    <osaPealkiri>ERIOSA</osaPealkiri>
    <peatykk>
      <peatykkNr>9</peatykkNr>
      <kuvatavNr>9. peatükk</kuvatavNr>
      <peatykkPealkiri>Süüteod isiku vastu</peatykkPealkiri>
      <paragrahv>
        <paragrahvNr>113</paragrahvNr>
        <kuvatavNr>§ 113</kuvatavNr>
        <paragrahvPealkiri>Tapmine</paragrahvPealkiri>
        <loige><loigeTekst>Loige üks tekst.</loigeTekst></loige>
      </paragrahv>
      <paragrahv>
        <paragrahvNr>88¹</paragrahvNr>
        <kuvatavNr>§ 88¹</kuvatavNr>
        <paragrahvPealkiri>Karistused alaealistele</paragrahvPealkiri>
      </paragrahv>
    </peatykk>
    <peatykk>
      <peatykkNr>14</peatykkNr>
      <kuvatavNr>14. peatükk</kuvatavNr>
      <peatykkPealkiri>Süüteod ametiseisundi vastu</peatykkPealkiri>
      <paragrahv>
        <paragrahvNr>88¹</paragrahvNr>
        <kuvatavNr>§ 88¹</kuvatavNr>
        <paragrahvPealkiri>Eelarvevahendite raiskamine</paragrahvPealkiri>
      </paragrahv>
    </peatykk>
  </osa>
</akt>
"""


def _build_graph_from_fake_xml(xml_text: str) -> list[dict]:
    """Drive the inner graph-build logic over a fake XML string.

    Mirrors the relevant section of ``main()`` so we can test the
    paragraph-IRI namespacing and label safety without invoking
    the network-fetching outer shell.
    """
    root = ET.fromstring(xml_text)
    osa2 = next(
        (
            osa
            for osa in root.iter()
            if gke.ln(osa.tag) == "osa"
            and gke.child_text(osa, "osaNr") == "2"
        ),
        None,
    )
    assert osa2 is not None, "fake XML must include osaNr=2"

    graph: list[dict] = []
    part_id = "estleg:KarS_Part2"
    part_label = gke._join_label(
        gke.child_text(osa2, "kuvatavNr") or "2. osa",
        gke.child_text(osa2, "osaPealkiri") or "ERIOSA",
    )
    part_node = {
        "@id": part_id,
        "@type": ["estleg:LegalPart", "owl:NamedIndividual"],
        "rdfs:label": part_label,
        "estleg:hasChapter": [],
    }

    chapter_count = 0
    for ch in [x for x in list(osa2) if gke.ln(x.tag) == "peatykk"]:
        chapter_count += 1
        ch_nr = gke.child_text(ch, "peatykkNr") or str(chapter_count)
        ch_id = f"estleg:KarS_Ch{gke.sanitize_identifier(ch_nr)}"
        part_node["estleg:hasChapter"].append({"@id": ch_id})
        ch_node: dict = {
            "@id": ch_id,
            "@type": ["estleg:Chapter", "owl:NamedIndividual"],
            "rdfs:label": gke._join_label(
                gke.child_text(ch, "kuvatavNr"),
                gke.child_text(ch, "peatykkPealkiri"),
            )
            or (gke.child_text(ch, "kuvatavNr") or f"Peatükk {ch_nr}"),
        }

        direct_sections = [x for x in list(ch) if gke.ln(x.tag) == "paragrahv"]
        if direct_sections:
            ch_node["estleg:hasSection"] = []
            for p in direct_sections:
                p_nr = gke.child_text(p, "paragrahvNr") or "?"
                kuvatav_nr = gke.child_text(p, "kuvatavNr") or ""
                # Mirror the production ``_build_paragraph`` suffix logic so
                # this helper exercises the real superscript folding (#251)
                # rather than the glyph-only ``sanitize_identifier``.
                p_id = (
                    f"estleg:KarS_Ch{gke.sanitize_identifier(ch_nr)}_"
                    f"Par{gke._paragraph_id_suffix(p, p_nr)}"
                )
                title = gke.child_text(p, "paragrahvPealkiri") or ""
                label = gke._join_label(kuvatav_nr, title, sep=" ")
                if not label:
                    label = kuvatav_nr or f"§ {p_nr}"
                ch_node["estleg:hasSection"].append({"@id": p_id})
                graph.append(
                    {
                        "@id": p_id,
                        "@type": ["estleg:Section", "owl:NamedIndividual"],
                        "rdfs:label": label,
                        "estleg:sectionNumber": p_nr,
                    }
                )
        graph.append(ch_node)
    graph.append(part_node)
    return graph


class TestParagraphIdNamespacing:
    def test_paragraph_id_includes_chapter(self) -> None:
        """Every paragraph's IRI MUST start with ``estleg:KarS_Ch<chapter>_``."""
        graph = _build_graph_from_fake_xml(_FAKE_KARS_XML)
        section_ids = [
            n["@id"]
            for n in graph
            if "estleg:Section" in (n.get("@type") or [])
        ]
        assert section_ids, "no sections built — graph empty"
        for sid in section_ids:
            assert sid.startswith("estleg:KarS_Ch"), sid
            assert "_Par" in sid, sid

    def test_same_paragraph_in_two_chapters_yields_distinct_ids(self) -> None:
        """Pre-fix bug: ``estleg:Par88_1`` would collide across chapters."""
        graph = _build_graph_from_fake_xml(_FAKE_KARS_XML)
        ids = [n["@id"] for n in graph]
        assert len(ids) == len(set(ids)), (
            f"duplicate IRIs in graph: "
            f"{[i for i in ids if ids.count(i) > 1]}"
        )

    def test_superscript_preserved_in_paragraph_iri(self) -> None:
        """``§ 88¹`` MUST serialize as ``Par88_1`` not ``Par881`` or
        ``Par88``."""
        graph = _build_graph_from_fake_xml(_FAKE_KARS_XML)
        section_ids = [
            n["@id"] for n in graph if "Par88_1" in n.get("@id", "")
        ]
        assert len(section_ids) == 2, (
            f"expected one § 88¹ per chapter (2 total); got {section_ids}"
        )
        # The base 88 IRI from a different chapter must not collide.
        bare_88_in_ch9 = "estleg:KarS_Ch9_Par88"
        assert bare_88_in_ch9 not in [n["@id"] for n in graph]

    def test_label_strips_safely_when_title_missing(self) -> None:
        """A paragraph with kuvatavNr but no paragrahvPealkiri MUST yield
        a non-empty label."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <akt><metaandmed><aktinimi>Karistusseadustik</aktinimi></metaandmed>
          <osa><osaNr>2</osaNr><kuvatavNr>2. osa</kuvatavNr>
            <peatykk><peatykkNr>1</peatykkNr><kuvatavNr>1. peatükk</kuvatavNr>
              <paragrahv>
                <paragrahvNr>1</paragrahvNr>
                <kuvatavNr>§ 1</kuvatavNr>
              </paragrahv>
            </peatykk>
          </osa>
        </akt>
        """
        graph = _build_graph_from_fake_xml(xml)
        para_node = next(
            n for n in graph if n.get("estleg:sectionNumber") == "1"
        )
        # Pre-fix: ``"§ 1 ".strip()`` would have produced "§ 1" but
        # the join-with-empty-then-strip pattern produced "" for cases
        # where pealkiri AND kuvatavNr both ended up trimmed. The new
        # builder filters empties before joining.
        assert para_node["rdfs:label"], (
            "paragraph label is empty — fallback to kuvatavNr should fire"
        )
        assert "§ 1" in para_node["rdfs:label"]

class TestSuperscriptFromYlaIndeks:
    """Regression #251: the real KarS XML stores superscripted section
    numbers in the ``ylaIndeks`` attribute (``<paragrahvNr ylaIndeks="1">
    133</paragrahvNr>``), NOT as Unicode glyphs in the text. The pre-fix
    generator read neither — so §133, §133¹, §133², §133³ all serialised to
    ``estleg:KarS_Ch9_Par133`` and tripped the post-build dup-guard.
    """

    @staticmethod
    def _par(xml: str) -> ET.Element:
        return ET.fromstring(xml)

    def test_ylaindeks_attribute_is_read(self) -> None:
        p = self._par(
            '<paragrahv><paragrahvNr ylaIndeks="1">133</paragrahvNr>'
            "<kuvatavNr>§ 133¹</kuvatavNr></paragrahv>"
        )
        assert gke._paragraph_superscript(p) == "1"

    def test_no_superscript_returns_empty(self) -> None:
        p = self._par(
            "<paragrahv><paragrahvNr>133</paragrahvNr>"
            "<kuvatavNr>§ 133</kuvatavNr></paragrahv>"
        )
        assert gke._paragraph_superscript(p) == ""

    def test_kuvatavnr_glyph_fallback(self) -> None:
        """When ``ylaIndeks`` is absent, a glyph in ``kuvatavNr`` is used."""
        p = self._par(
            "<paragrahv><paragrahvNr>133</paragrahvNr>"
            "<kuvatavNr>§ 133²</kuvatavNr></paragrahv>"
        )
        assert gke._paragraph_superscript(p) == "2"

    def test_ylaindeks_suffix_distinct_from_base(self) -> None:
        base = self._par(
            "<paragrahv><paragrahvNr>133</paragrahvNr></paragrahv>"
        )
        sup1 = self._par(
            '<paragrahv><paragrahvNr ylaIndeks="1">133</paragrahvNr></paragrahv>'
        )
        sup2 = self._par(
            '<paragrahv><paragrahvNr ylaIndeks="2">133</paragrahvNr></paragrahv>'
        )
        s_base = gke._paragraph_id_suffix(base, "133")
        s_1 = gke._paragraph_id_suffix(sup1, "133")
        s_2 = gke._paragraph_id_suffix(sup2, "133")
        assert s_base == "133"
        assert s_1 == "133_1"
        assert s_2 == "133_2"
        assert len({s_base, s_1, s_2}) == 3

    def test_glyph_in_paragrahvnr_text_not_double_suffixed(self) -> None:
        """If RT embeds the glyph in ``paragrahvNr`` text the base is taken
        from the leading digits only, so the suffix is applied exactly once
        (``88_1``), never ``88_1_1``."""
        p = self._par(
            "<paragrahv><paragrahvNr>88¹</paragrahvNr>"
            "<kuvatavNr>§ 88¹</kuvatavNr></paragrahv>"
        )
        assert gke._paragraph_id_suffix(p, "88¹") == "88_1"

    def test_sup_markup_fallback(self) -> None:
        """RT delivers ``kuvatavNr`` as CDATA, so ``<sup>`` arrives as
        *literal text* (escaped here), not a parsed child element — the
        regex matches that literal ``<sup>2</sup>`` run."""
        p = self._par(
            "<paragrahv><paragrahvNr>50</paragrahvNr>"
            "<kuvatavNr>§ 50&lt;sup&gt;2&lt;/sup&gt;</kuvatavNr></paragrahv>"
        )
        assert gke._paragraph_id_suffix(p, "50") == "50_2"


# Two §133 paragraphs in the SAME chapter, distinguished only by the
# ``ylaIndeks`` attribute (0 and 1) — the exact collision the pre-fix
# code produced on real KarS data.
_FAKE_KARS_YLAINDEKS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <metaandmed><aktinimi>Karistusseadustik</aktinimi></metaandmed>
  <osa>
    <osaNr>2</osaNr>
    <kuvatavNr>2. osa</kuvatavNr>
    <osaPealkiri>ERIOSA</osaPealkiri>
    <peatykk>
      <peatykkNr>9</peatykkNr>
      <kuvatavNr>9. peatükk</kuvatavNr>
      <peatykkPealkiri>Süüteod isiku vastu</peatykkPealkiri>
      <paragrahv>
        <paragrahvNr ylaIndeks="0">133</paragrahvNr>
        <kuvatavNr>§ 133</kuvatavNr>
        <paragrahvPealkiri>Orjastamine</paragrahvPealkiri>
      </paragrahv>
      <paragrahv>
        <paragrahvNr ylaIndeks="1">133</paragrahvNr>
        <kuvatavNr>§ 133¹</kuvatavNr>
        <paragrahvPealkiri>Inimkaubandus</paragrahvPealkiri>
      </paragrahv>
    </peatykk>
  </osa>
</akt>
"""


class TestYlaIndeksIntegration:
    def test_two_par133_with_ylaindeks_0_and_1_yield_distinct_ids(self) -> None:
        """The issue's named acceptance case: §133 (ylaIndeks 0) and §133
        (ylaIndeks 1) MUST produce distinct ids."""
        graph = _build_graph_from_fake_xml(_FAKE_KARS_YLAINDEKS_XML)
        section_ids = [
            n["@id"]
            for n in graph
            if "estleg:Section" in (n.get("@type") or [])
        ]
        assert len(section_ids) == 2
        assert len(set(section_ids)) == 2, (
            f"§133 ylaIndeks 0/1 collapsed to one id: {section_ids}"
        )
        assert "estleg:KarS_Ch9_Par133_0" in section_ids
        assert "estleg:KarS_Ch9_Par133_1" in section_ids

    def test_ylaindeks_graph_passes_dup_guard(self) -> None:
        """Building the graph and running the same uniqueness invariant as
        ``main()`` MUST NOT raise (pre-fix: RuntimeError)."""
        graph = _build_graph_from_fake_xml(_FAKE_KARS_YLAINDEKS_XML)
        # Replicate main()'s post-build dup-guard.
        seen: dict[str, int] = {}
        for node in graph:
            nid = node.get("@id")
            if nid:
                seen[nid] = seen.get(nid, 0) + 1
        duplicates = {nid: c for nid, c in seen.items() if c > 1}
        assert not duplicates, f"dup-guard would raise on: {sorted(duplicates)}"


class TestContextDcNamespace:
    """Regression #308: ``dc`` MUST be Dublin Core *elements/1.1* (as in
    every other corpus file), NOT /dc/terms/. Conflating them makes
    ``dc:source``/``dc:references`` expand to the wrong IRIs so RDF joins
    across the corpus silently miss this artifact after any regeneration.
    """

    def test_dc_prefix_maps_to_elements_1_1(self) -> None:
        ctx = gke.build_context()
        assert ctx["dc"] == "http://purl.org/dc/elements/1.1/"

    def test_dc_is_not_dcterms(self) -> None:
        """``dc`` and ``dcterms`` are distinct namespaces; the bug pointed
        both at /dc/terms/."""
        ctx = gke.build_context()
        assert ctx["dcterms"] == "http://purl.org/dc/terms/"
        assert ctx["dc"] != ctx["dcterms"]

    def test_dc_matches_canonical_estleg_common_context(self) -> None:
        """The eriosa ``dc`` IRI MUST equal the shared corpus context so
        ``dc:title``/``dc:source``/``dc:references`` expand identically."""
        try:
            from estleg import estleg_common
        except Exception:  # pragma: no cover - defensive: skip if unavailable
            import pytest

            pytest.skip("estleg_common not importable")
        assert gke.build_context()["dc"] == estleg_common.CONTEXT["dc"]


def _para_with_loiget(*texts: str) -> ET.Element:
    """Build a ``<paragrahv>`` whose ``<loige>`` children carry ``texts``."""
    loiget = "".join(
        f"<loige><loigeTekst>{t}</loigeTekst></loige>" for t in texts
    )
    return ET.fromstring(f"<paragrahv>{loiget}</paragrahv>")


class TestCollectLoigePreviewBoundary:
    """Regression #368: the ~500-char preview MUST cut on a sentence/word
    boundary, never mid-token. A raw ``joined[:max_len]`` slice ends in the
    middle of a word (e.g. ``…kohustatud arvut``), corrupting the summary
    that every downstream classifier reads.
    """

    def test_short_text_returned_intact(self) -> None:
        para = _para_with_loiget("Lühike tekst.")
        assert gke.collect_loige_preview(para) == "Lühike tekst."

    def test_cut_does_not_split_a_word(self) -> None:
        # 60 words of 9 chars + space = 600 chars > 100, no sentence stop,
        # so the helper must fall back to the LAST whole word.
        word = "kohustus"  # 8 chars
        sentence = " ".join([word] * 60)
        para = _para_with_loiget(sentence)
        out = gke.collect_loige_preview(para, max_len=100)
        assert len(out) <= 100
        # Ellipsis signals the word-boundary fallback fired.
        assert out.endswith("…")
        # Drop the ellipsis; every remaining token is the whole word.
        body = out[:-1].strip()
        assert all(tok == word for tok in body.split(" "))
        # And the raw slice WOULD have split a word — confirm we differ.
        raw = sentence[:100]
        assert raw[-1] != " " and raw != out

    def test_prefers_sentence_boundary_within_budget(self) -> None:
        # First sentence ends well past 70% of an 80-char budget, so the
        # cut should land on that period and keep punctuation.
        first = "See on esimene lause mis on piisavalt pikk lopetamiseks siin."
        rest = "Teine lause laeb jaaki ja peab olema piisavalt pikk et ulatuda ule eelarve piiri."
        para = _para_with_loiget(f"{first} {rest}")
        out = gke.collect_loige_preview(para, max_len=80)
        assert out == first
        assert out.endswith(".")
        assert "…" not in out

    def test_no_trailing_partial_token_for_real_world_case(self) -> None:
        # Mirrors the ATKE_Par_24 symptom from #368: long text with a final
        # token that would be split by a raw slice.
        prefix = "x" * 495  # fills the budget up to char 495
        para = _para_with_loiget(f"{prefix} arvutiprogramm tekst")
        out = gke.collect_loige_preview(para, max_len=500)
        assert len(out) <= 500
        # The trailing token must be whole, never a fragment like ``arvut``.
        assert not out.rstrip("…").endswith("arvut")

    def test_respects_max_len_when_joining_multiple_loiget(self) -> None:
        para = _para_with_loiget("Esimene loige sisu siin.", "Teine " + "x" * 600)
        out = gke.collect_loige_preview(para, max_len=120)
        assert len(out) <= 120


class TestChapterLabel:
    def test_chapter_label_safe_when_title_missing(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <akt><metaandmed><aktinimi>Karistusseadustik</aktinimi></metaandmed>
          <osa><osaNr>2</osaNr><kuvatavNr>2. osa</kuvatavNr>
            <peatykk>
              <peatykkNr>5</peatykkNr>
              <kuvatavNr>5. peatükk</kuvatavNr>
              <paragrahv>
                <paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1</kuvatavNr>
              </paragrahv>
            </peatykk>
          </osa>
        </akt>
        """
        graph = _build_graph_from_fake_xml(xml)
        ch_node = next(
            n for n in graph if "estleg:Chapter" in (n.get("@type") or [])
        )
        # Without a title, label falls back to kuvatavNr alone — and
        # MUST be non-empty.
        assert ch_node["rdfs:label"]
        assert "5. peatükk" in ch_node["rdfs:label"]
