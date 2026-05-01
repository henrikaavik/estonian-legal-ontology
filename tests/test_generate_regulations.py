"""Tests for scripts/generate_regulations.py — regulation JSON-LD builder.

Covers metadata extraction, issuer classification, structured XML parsing,
HTMLKonteiner fallback parsing, ID stability, duplicate-title disambiguation,
the KOV flag, and shared `iter_peep_files` discovery in `riigiteataja_common`.

All tests run offline against fixtures under `tests/fixtures/regulations/`.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import estleg_common
import riigiteataja_common
from generate_regulations import build_regulation_jsonld, classify_issuer
from riigiteataja_common import (
    iter_peep_files,
    parse_act_metadata,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "regulations"

HTML_FIXTURE = FIXTURE_DIR / "maarus_html_konteiner.xml"
STRUCTURED_FIXTURE = FIXTURE_DIR / "maarus_structured.xml"
KOV_FIXTURE = FIXTURE_DIR / "maarus_kov.xml"

HTML_TITLE = "Investorile ja Tagatisfondile esitatavate andmete loetelu ja esitamise kord"
STRUCTURED_TITLE = "Volitatud asutuste määramine"
KOV_TITLE = "Põhimäärus"


def _parse(path: Path) -> ET.Element:
    return ET.parse(str(path)).getroot()


def _node_by_id(graph: list[dict], node_id: str) -> dict | None:
    for n in graph:
        if n.get("@id") == node_id:
            return n
    return None


def _provisions(graph: list[dict]) -> list[dict]:
    return [
        n for n in graph
        if "estleg:paragrahv" in n
        and "owl:NamedIndividual" in (n.get("@type") or [])
    ]


# ---------------------------------------------------------------------------
# 1. Metadata extraction (HTML fixture)
# ---------------------------------------------------------------------------

class TestParseActMetadata:
    def test_html_fixture_metadata(self):
        root = _parse(HTML_FIXTURE)
        meta = parse_act_metadata(root)

        assert meta["globalId"] == "191725"
        assert meta["terviktekstId"] == "155482"
        assert meta["documentType"] == "määrus"
        assert meta["issuer"] == "Rahandusminister"
        assert meta["actNumber"] == "104"
        assert meta["entryIntoForce"] == "2002-08-31"


# ---------------------------------------------------------------------------
# 2. Issuer classification
# ---------------------------------------------------------------------------

class TestClassifyIssuer:
    def test_government_regulation(self):
        classes = classify_issuer("Vabariigi Valitsus", is_kov=False)
        assert "estleg:NationalRegulation" in classes
        assert "estleg:GovernmentRegulation" in classes

    def test_minister_regulation(self):
        classes = classify_issuer("Sotsiaalminister", is_kov=False)
        assert "estleg:NationalRegulation" in classes
        assert "estleg:MinisterialRegulation" in classes

    def test_kov_regulation_only_municipal(self):
        classes = classify_issuer("Tartu Linnavolikogu", is_kov=True)
        # KOV must not pick up the National/Government markers — KOV is its own bucket.
        assert classes == ["estleg:MunicipalRegulation"]

    def test_central_bank_only_national(self):
        # Eesti Pank is central but not a minister and not the government.
        classes = classify_issuer("Eesti Pank", is_kov=False)
        assert classes == ["estleg:NationalRegulation"]

    def test_no_issuer_falls_back_to_national(self):
        classes = classify_issuer(None, is_kov=False)
        assert classes == ["estleg:NationalRegulation"]


# ---------------------------------------------------------------------------
# 3. Structured XML parsing
# ---------------------------------------------------------------------------

class TestStructuredParsing:
    def test_graph_node_count(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, stats = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        # Fixture has 2 paragraphs => N+2 = 4 nodes (1 ontology + 1 class + 2 provisions)
        assert stats["paragraphs"] == 2
        assert stats["annexes"] == 0
        assert len(doc["@graph"]) == 4

    def test_provision_node_shape(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        provisions = _provisions(doc["@graph"])
        assert len(provisions) == 2

        for prov in provisions:
            assert prov["estleg:paragrahv"].startswith("§")
            label = prov["rdfs:label"]
            assert isinstance(label, str)
            assert label

            type_list = prov["@type"]
            assert "owl:NamedIndividual" in type_list
            assert "estleg:Regulation_160748" in type_list

    def test_ontology_classes_for_government(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        ontology = doc["@graph"][0]
        type_list = ontology["@type"]
        assert "owl:Ontology" in type_list
        assert "estleg:NationalRegulation" in type_list
        assert "estleg:GovernmentRegulation" in type_list

    def test_preamble_populated_from_preambul(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        ontology = doc["@graph"][0]
        preamble = ontology.get("estleg:preambleText")
        assert preamble is not None
        assert isinstance(preamble, str)
        # Content from `<preambul>` should mention the legal basis.
        assert "Määrus kehtestatakse" in preamble
        assert "§ 12 alusel" in preamble


# ---------------------------------------------------------------------------
# 4. HTMLKonteiner fallback parsing
# ---------------------------------------------------------------------------

class TestHtmlFallbackParsing:
    def test_provisions_extracted_from_html(self):
        root = _parse(HTML_FIXTURE)
        doc, stats = build_regulation_jsonld(HTML_TITLE, {}, root, is_kov=False)

        # Must use the HTML fallback path, not the structured path.
        assert stats["html_fallback"] == 1
        provisions = _provisions(doc["@graph"])
        assert len(provisions) >= 2

    def test_provision_iri_pattern(self):
        root = _parse(HTML_FIXTURE)
        doc, _ = build_regulation_jsonld(HTML_TITLE, {}, root, is_kov=False)

        provision_ids = {n["@id"] for n in _provisions(doc["@graph"])}
        # IRIs must follow estleg:Reg_<terviktekstId>_Par_<n>
        assert "estleg:Reg_155482_Par_1" in provision_ids
        assert "estleg:Reg_155482_Par_2" in provision_ids

    def test_preamble_text_contains_legal_basis(self):
        root = _parse(HTML_FIXTURE)
        doc, _ = build_regulation_jsonld(HTML_TITLE, {}, root, is_kov=False)

        ontology = doc["@graph"][0]
        preamble = ontology.get("estleg:preambleText")
        assert preamble is not None
        assert isinstance(preamble, str)
        # The preamble is the first <p> of the HTML body — it cites the parent law.
        assert "Tagatisfondi" in preamble
        assert "§ 57" in preamble

    def test_provision_legalText_contains_body_phrase(self):
        root = _parse(HTML_FIXTURE)
        doc, _ = build_regulation_jsonld(HTML_TITLE, {}, root, is_kov=False)

        par1 = _node_by_id(doc["@graph"], "estleg:Reg_155482_Par_1")
        assert par1 is not None
        legal_text = par1.get("estleg:legalText")
        assert legal_text is not None
        assert isinstance(legal_text, str)
        # A specific phrase from § 1's body in the fixture.
        assert "Investeerimisasutuse juhatuse liige" in legal_text


# ---------------------------------------------------------------------------
# 5. ID stability across rebuilds
# ---------------------------------------------------------------------------

class TestIdStability:
    @pytest.mark.parametrize(
        "fixture,title,is_kov",
        [
            (STRUCTURED_FIXTURE, STRUCTURED_TITLE, False),
            (HTML_FIXTURE, HTML_TITLE, False),
            (KOV_FIXTURE, KOV_TITLE, True),
        ],
    )
    def test_repeat_build_yields_identical_ids(self, fixture, title, is_kov):
        # Re-parse for each call so we exercise the full pipeline twice.
        root1 = _parse(fixture)
        root2 = _parse(fixture)

        doc1, _ = build_regulation_jsonld(title, {}, root1, is_kov=is_kov)
        doc2, _ = build_regulation_jsonld(title, {}, root2, is_kov=is_kov)

        ids1 = [n.get("@id") for n in doc1["@graph"]]
        ids2 = [n.get("@id") for n in doc2["@graph"]]
        assert ids1 == ids2


# ---------------------------------------------------------------------------
# 6. Duplicate-title handling — different terviktekstId must not collide
# ---------------------------------------------------------------------------

class TestDuplicateTitleHandling:
    def test_different_tids_produce_disjoint_iris(self):
        root_struct = _parse(STRUCTURED_FIXTURE)
        root_kov = _parse(KOV_FIXTURE)

        doc_struct, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root_struct, is_kov=False)
        doc_kov, _ = build_regulation_jsonld(KOV_TITLE, {}, root_kov, is_kov=True)

        ontology_struct = doc_struct["@graph"][0]
        ontology_kov = doc_kov["@graph"][0]

        # Ontology @ids must differ — they are namespaced by terviktekstId.
        assert ontology_struct["@id"] != ontology_kov["@id"]

        # Provision IRIs from the two docs must not intersect.
        prov_struct_ids = {n["@id"] for n in _provisions(doc_struct["@graph"])}
        prov_kov_ids = {n["@id"] for n in _provisions(doc_kov["@graph"])}
        assert prov_struct_ids
        assert prov_kov_ids
        assert prov_struct_ids.isdisjoint(prov_kov_ids)


# ---------------------------------------------------------------------------
# 7. isKov flag drives @type and the boolean literal
# ---------------------------------------------------------------------------

class TestIsKovFlag:
    def test_kov_true_marks_municipal(self):
        root = _parse(KOV_FIXTURE)
        doc, _ = build_regulation_jsonld(KOV_TITLE, {}, root, is_kov=True)

        ontology = doc["@graph"][0]
        is_kov = ontology["estleg:isKov"]
        assert is_kov == {"@value": "true", "@type": "xsd:boolean"}

        type_list = ontology["@type"]
        assert "estleg:MunicipalRegulation" in type_list
        # Sanity: KOV docs must NOT carry the national markers.
        assert "estleg:NationalRegulation" not in type_list
        assert "estleg:GovernmentRegulation" not in type_list

    def test_kov_false_marks_national(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        ontology = doc["@graph"][0]
        is_kov = ontology["estleg:isKov"]
        assert is_kov == {"@value": "false", "@type": "xsd:boolean"}

        type_list = ontology["@type"]
        assert "estleg:NationalRegulation" in type_list
        assert "estleg:MunicipalRegulation" not in type_list


# ---------------------------------------------------------------------------
# 8. iter_peep_files discovery (riigiteataja_common)
# ---------------------------------------------------------------------------

class TestIterPeepFiles:
    def _setup_tmp_layout(self, tmp_path: Path) -> dict[str, Path]:
        """Lay out a tmp KRR_DIR with one law, one regulation, one KOV file."""
        law_file = tmp_path / "some_law_peep.json"
        law_file.write_text("{}", encoding="utf-8")

        riik_dir = tmp_path / "regulations" / "riik"
        riik_dir.mkdir(parents=True)
        reg_file = riik_dir / "some_regulation_t111_peep.json"
        reg_file.write_text("{}", encoding="utf-8")

        kov_dir = tmp_path / "regulations" / "kov" / "tartu_linnavolikogu"
        kov_dir.mkdir(parents=True)
        kov_file = kov_dir / "pohimaarus_t999001_peep.json"
        kov_file.write_text("{}", encoding="utf-8")

        return {"law": law_file, "regulation": reg_file, "kov": kov_file}

    def test_finds_regulation_when_included(self, tmp_path, monkeypatch):
        files = self._setup_tmp_layout(tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_path)

        found = iter_peep_files(include_laws=True, include_regulations=True)
        assert files["law"] in found
        assert files["regulation"] in found
        # Default kov=False — the KOV file must NOT appear.
        assert files["kov"] not in found

    def test_excludes_regulation_when_not_included(self, tmp_path, monkeypatch):
        files = self._setup_tmp_layout(tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_path)

        found = iter_peep_files(include_laws=True, include_regulations=False)
        assert files["law"] in found
        assert files["regulation"] not in found
        assert files["kov"] not in found

    def test_kov_opt_in(self, tmp_path, monkeypatch):
        files = self._setup_tmp_layout(tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_path)

        found = iter_peep_files(
            include_laws=False, include_regulations=True, include_kov=True
        )
        assert files["kov"] in found
        assert files["regulation"] in found
        assert files["law"] not in found
