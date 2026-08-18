"""Tests for ``scripts/generate_tsus_osa7_jsonld.py`` (the TsÜS Osa-7 generator,
the reproducibility half of issue #567).

All tests are offline: they drive the parser/builder with synthetic Riigi
Teataja-style XML rather than the network, exercising

1. the asymmetric Section id scheme (regular ``TsUS_Par_<N>`` vs the
   hand-built superscript form ``Par<N>_<sup>``);
2. the per-lõige id suffix (``loigeNr`` + lõige superscript);
3. legal-text rendering — both ``<sup>`` encodings → ``^N``, the lõige
   ``kuvatavNr`` prefix, ``muutmismarge`` amendment-trailer stripping, and a
   repealed lõige collapsing to its ``(N)`` marker;
4. an end-to-end ``build_graph`` over a small Osa-7 fragment: node ordering
   (provisions before their section, concepts split around the hierarchy),
   the curated TBox + concept layer, and @id uniqueness.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from estleg import generate_tsus_osa7_jsonld as gt

NS = gt.NS


# ---------------------------------------------------------------------------
# #383 — concept IRI fragments must be ASCII
# ---------------------------------------------------------------------------


class TestConceptFragmentsAscii:
    def test_curated_fragments_are_ascii_and_match_sanitize(self) -> None:
        # Constants themselves must be ASCII so the mapping is readable (#383).
        expected = {
            "ÕigusteKaitsmine": "OigusteKaitsmine",
            "ÕigusteEnnetamine": "OigusteEnnetamine",
            "AegumiseTagajärjed": "AegumiseTagajarjed",
        }
        for estonian, ascii_frag in expected.items():
            assert ascii_frag.isascii()
            assert gt.sanitize_identifier(estonian) == ascii_frag
        for frag, _label in gt.CONCEPTS_TOP + gt.CONCEPTS_BOTTOM:
            assert frag.isascii(), frag
        for concepts in (
            *gt.SECTION_CONCEPTS.values(),
            *gt.DIVISION_DEFAULT_CONCEPTS.values(),
        ):
            for frag in concepts:
                assert frag.isascii(), frag


# ---------------------------------------------------------------------------
# section_ids — the id-builder
# ---------------------------------------------------------------------------


class TestSectionIds:
    def test_regular_section_uses_tsus_par_prefix(self) -> None:
        assert gt.section_ids("145", "") == ("TsUS_Par_145", "145", "145")

    def test_superscript_section_uses_bare_par_prefix(self) -> None:
        # The hand-built module addresses §145¹ as ``Par145_1`` (no ``TsUS_``
        # prefix) — an irregular scheme we must reproduce so the @id matches.
        assert gt.section_ids("145", "1") == ("Par145_1", "145_1", "145^1")

    def test_provision_id_suffix_plain(self) -> None:
        loige = ET.fromstring("<loige><loigeNr>2</loigeNr></loige>")
        assert gt.provision_id_suffix(loige, 99) == ("2", "2")

    def test_provision_id_suffix_unnumbered_falls_back_to_position(self) -> None:
        loige = ET.fromstring("<loige><sisuTekst><tavatekst>x</tavatekst></sisuTekst></loige>")
        assert gt.provision_id_suffix(loige, 1) == ("1", "1")

    def test_provision_id_suffix_superscript_loige(self) -> None:
        loige = ET.fromstring('<loige><loigeNr ylaIndeks="1">1</loigeNr></loige>')
        assert gt.provision_id_suffix(loige, 2) == ("1_1", "1^1")


# ---------------------------------------------------------------------------
# _render_text — sup normalisation across both RT encodings
# ---------------------------------------------------------------------------


class TestRenderText:
    def test_plain_kuvatav_unchanged(self) -> None:
        assert gt._render_text(ET.fromstring("<kuvatavNr>(1)</kuvatavNr>")) == "(1)"

    def test_escaped_literal_sup_in_kuvatav(self) -> None:
        # RT stores the section superscript as an ESCAPED literal substring.
        el = ET.fromstring("<kuvatavNr>§ 145&lt;sup&gt;1&lt;/sup&gt;.</kuvatavNr>")
        assert el.text == "§ 145<sup>1</sup>."  # parser un-escapes to literal text
        assert gt._render_text(el) == "§ 145^1."

    def test_real_sup_element_in_body(self) -> None:
        # RT stores an in-sentence cross-reference superscript as a real child.
        el = ET.fromstring("<tavatekst>lõigetes 1 ja 1<sup>1</sup> nimetatud</tavatekst>")
        assert gt._render_text(el) == "lõigetes 1 ja 1^1 nimetatud"

    def test_whitespace_collapsed(self) -> None:
        el = ET.fromstring("<tavatekst>  a\n\t  b  </tavatekst>")
        assert gt._render_text(el) == "a b"

    def test_none_returns_empty(self) -> None:
        assert gt._render_text(None) == ""


# ---------------------------------------------------------------------------
# provision_text — prefix, marker stripping, repealed lõige
# ---------------------------------------------------------------------------


class TestProvisionText:
    def test_numbered_loige_prefixes_kuvatav(self) -> None:
        loige = ET.fromstring(
            "<loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>"
            "<sisuTekst><tavatekst>Toimida heas usus.</tavatekst></sisuTekst></loige>"
        )
        assert gt.provision_text(loige) == "(1) Toimida heas usus."

    def test_single_unnumbered_loige_is_body_only(self) -> None:
        loige = ET.fromstring(
            "<loige><sisuTekst><tavatekst>Üks lause.</tavatekst></sisuTekst></loige>"
        )
        assert gt.provision_text(loige) == "Üks lause."

    def test_amendment_marker_is_stripped(self) -> None:
        # The nested <muutmismarge> RT enactment reference must NOT leak into
        # legalText (the hand-built module carries no such trailers).
        loige = ET.fromstring(
            "<loige><sisuTekst><tavatekst>Sisu.</tavatekst>"
            "<muutmismarge><RTosa>RT I</RTosa><RTaasta>2005</RTaasta>"
            "<joustumine>2006-01-01</joustumine></muutmismarge></sisuTekst></loige>"
        )
        assert gt.provision_text(loige) == "Sisu."

    def test_repealed_loige_collapses_to_marker(self) -> None:
        # §147 lg4 shape: a kuvatavNr + a bare muutmismarge, no sisuTekst.
        loige = ET.fromstring(
            "<loige><loigeNr>4</loigeNr><kuvatavNr>(4)</kuvatavNr>"
            "<muutmismarge><tavatekst>Kehtetu - </tavatekst></muutmismarge></loige>"
        )
        assert gt.provision_text(loige) == "(4)"

    def test_alampunkt_subpoints_rendered(self) -> None:
        loige = ET.fromstring(
            "<loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>"
            "<sisuTekst><tavatekst>Loend:</tavatekst>"
            "<alampunkt><kuvatavNr>1)</kuvatavNr>"
            "<sisuTekst><tavatekst>esimene;</tavatekst></sisuTekst></alampunkt>"
            "<alampunkt><kuvatavNr>2)</kuvatavNr>"
            "<sisuTekst><tavatekst>teine.</tavatekst></sisuTekst></alampunkt>"
            "</sisuTekst></loige>"
        )
        assert gt.provision_text(loige) == "(2) Loend: 1) esimene; 2) teine."


# ---------------------------------------------------------------------------
# Integration: build_graph over a synthetic Osa-7 fragment
# ---------------------------------------------------------------------------

# §138 (numbered, directly under chapter 9) + chapter 10 / jagu 1 holding
# §142 and the superscript §145¹ — exercises both containers, both id schemes,
# the curated coversConcept lookup, and provision/section ordering.
_FAKE_OSA7 = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt>
  <osa>
    <osaNr>7</osaNr>
    <kuvatavNr>7. osa</kuvatavNr>
    <osaPealkiri>TSIVIILÕIGUSTE TEOSTAMINE</osaPealkiri>
    <peatykk>
      <peatykkNr>9</peatykkNr>
      <kuvatavNr>9. peatükk</kuvatavNr>
      <peatykkPealkiri>TSIVIILÕIGUSTE TEOSTAMISE VIISID</peatykkPealkiri>
      <paragrahv>
        <paragrahvNr>138</paragrahvNr>
        <kuvatavNr>§ 138.</kuvatavNr>
        <paragrahvPealkiri>Hea usu põhimõte</paragrahvPealkiri>
        <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
          <sisuTekst><tavatekst>Esimene.</tavatekst></sisuTekst></loige>
        <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
          <sisuTekst><tavatekst>Teine.</tavatekst></sisuTekst></loige>
      </paragrahv>
    </peatykk>
    <peatykk>
      <peatykkNr>10</peatykkNr>
      <kuvatavNr>10. peatükk</kuvatavNr>
      <peatykkPealkiri>AEGUMINE</peatykkPealkiri>
      <jagu>
        <jaguNr>1</jaguNr>
        <kuvatavNr>1. jagu</kuvatavNr>
        <jaguPealkiri>Aegumise tagajärjed</jaguPealkiri>
        <paragrahv>
          <paragrahvNr>142</paragrahvNr>
          <kuvatavNr>§ 142.</kuvatavNr>
          <paragrahvPealkiri>Aegumise mõiste</paragrahvPealkiri>
          <loige><sisuTekst><tavatekst>Aegumine.</tavatekst></sisuTekst></loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr ylaIndeks="1">145</paragrahvNr>
          <kuvatavNr>§ 145&lt;sup&gt;1&lt;/sup&gt;.</kuvatavNr>
          <paragrahvPealkiri>Aegumise tagajärjed tagatisele</paragrahvPealkiri>
          <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
            <sisuTekst><tavatekst>Pant.</tavatekst></sisuTekst></loige>
        </paragrahv>
      </jagu>
    </peatykk>
  </osa>
</oigusakt>"""


class TestBuildGraphIntegration:
    def setup_method(self) -> None:
        root = ET.fromstring(_FAKE_OSA7)
        self.osa = gt.find_osa(root, "7")
        self.graph = gt.build_graph(self.osa)
        self.by_id = {n["@id"]: n for n in self.graph}

    def test_all_ids_unique(self) -> None:
        ids = [n["@id"] for n in self.graph if "@id" in n]
        assert len(ids) == len(set(ids))

    def test_no_at_id_contains_non_ascii(self) -> None:
        # #383: LegalConcept fragments (and every other minted @id) must be ASCII.
        def walk(obj: object) -> None:
            if isinstance(obj, dict):
                node_id = obj.get("@id")
                if isinstance(node_id, str):
                    assert node_id.isascii(), node_id
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        walk(self.graph)

    def test_ontology_header_type_and_title(self) -> None:
        root = self.by_id[gt.ROOT_ID]
        assert root["@type"] == ["estleg:Act", "estleg:Law", "owl:Ontology"]
        # Range derives from the base section numbers present (138 … 145).
        assert root["dc:title"] == "Tsiviilõiguste teostamine (§138-145)"
        assert root["rdfs:label"] == "TsÜS Osa 7 ontoloogia"

    def test_curated_tbox_and_concepts_present(self) -> None:
        for frag, _ in gt.CLASS_DECLS:
            assert self.by_id[f"{NS}{frag}"]["@type"] == ["owl:Class"]
        for frag, _, _, _ in gt.PROPERTY_DECLS:
            assert self.by_id[f"{NS}{frag}"]["@type"] == ["owl:ObjectProperty"]
        # #377: these three must not RDFS-type stubs under inference=rdfs.
        for frag in ("hasSection", "hasProvision", "coversConcept"):
            node = self.by_id[f"{NS}{frag}"]
            assert "rdfs:domain" not in node
            assert "rdfs:range" not in node
        for frag, label in gt.CONCEPTS_TOP + gt.CONCEPTS_BOTTOM:
            node = self.by_id[f"{NS}{frag}"]
            assert "estleg:LegalConcept" in node["@type"]
            assert node["rdfs:label"] == label
        # Human-readable labels keep Estonian letters; fragments do not (#383).
        assert self.by_id[f"{NS}OigusteKaitsmine"]["rdfs:label"] == "Õiguste kaitsmine"
        assert self.by_id[f"{NS}OigusteEnnetamine"]["rdfs:label"] == "Õiguste ennetamine"
        assert self.by_id[f"{NS}AegumiseTagajarjed"]["rdfs:label"] == "Aegumise tagajärjed"

    def test_regular_section_under_chapter(self) -> None:
        sec = self.by_id[f"{NS}TsUS_Par_138"]
        assert sec["estleg:sectionNumber"] == "138"
        assert sec["rdfs:label"] == "§ 138. Hea usu põhimõte"
        assert sec["estleg:inChapter"] == {"@id": f"{NS}TsUS_Chapter_9"}
        assert sec["estleg:coversConcept"] == [{"@id": f"{NS}OigusteEnnetamine"}]
        assert [p["@id"] for p in sec["estleg:hasProvision"]] == [
            f"{NS}Par138_Lg1",
            f"{NS}Par138_Lg2",
        ]

    def test_superscript_section_id_and_label(self) -> None:
        sec = self.by_id[f"{NS}Par145_1"]
        assert sec["estleg:sectionNumber"] == "145"
        assert sec["rdfs:label"] == "§ 145^1. Aegumise tagajärjed tagatisele"
        assert sec["estleg:inDivision"] == {"@id": f"{NS}Division1"}
        assert self.by_id[f"{NS}Par145_1_Lg1"]["rdfs:label"] == "§145^1 lg 1"

    def test_provision_text_and_prefix(self) -> None:
        assert self.by_id[f"{NS}Par138_Lg1"]["estleg:legalText"] == "(1) Esimene."
        # single unnumbered lõige → body only, id still ends _Lg1
        assert self.by_id[f"{NS}Par142_Lg1"]["estleg:legalText"] == "Aegumine."

    def test_provisions_emitted_before_their_section(self) -> None:
        ids = [n["@id"] for n in self.graph]
        assert ids.index(f"{NS}Par138_Lg1") < ids.index(f"{NS}TsUS_Par_138")
        assert ids.index(f"{NS}Par138_Lg2") < ids.index(f"{NS}TsUS_Par_138")

    def test_concept_ordering_splits_around_hierarchy(self) -> None:
        ids = [n["@id"] for n in self.graph]
        part = ids.index(f"{NS}Part7")
        # the three cross-cutting concepts precede the Part …
        for frag, _ in gt.CONCEPTS_TOP:
            assert ids.index(f"{NS}{frag}") < part
        # … and the five aegumine concepts follow every section.
        last_section = max(
            i for i, n in enumerate(self.graph) if "estleg:Section" in n.get("@type", [])
        )
        for frag, _ in gt.CONCEPTS_BOTTOM:
            assert ids.index(f"{NS}{frag}") > last_section

    def test_part_and_division_structure(self) -> None:
        part = self.by_id[f"{NS}Part7"]
        assert part["rdfs:label"] == "TsÜS 7. osa – Tsiviilõiguste teostamine"
        assert part["estleg:hasChapter"] == [
            {"@id": f"{NS}TsUS_Chapter_9"},
            {"@id": f"{NS}TsUS_Chapter_10"},
        ]
        div = self.by_id[f"{NS}Division1"]
        assert div["estleg:inChapter"] == {"@id": f"{NS}TsUS_Chapter_10"}
        assert div["rdfs:label"] == "1. jagu – Aegumise tagajärjed"
