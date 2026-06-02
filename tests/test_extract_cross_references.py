"""Tests for scripts/extract_cross_references.py — Layer 2b additions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer2b"


def load_preamble_samples():
    with open(FIXTURE_DIR / "preamble_samples.json", "r", encoding="utf-8") as fh:
        return json.load(fh)["samples"]


class TestExtractPreambleCitations:
    """Drive the parser with corpus-derived strings, not synthetic ones.
    Each sample asserts what real KOV / state regulation preamble text
    looks like in the wild — see fixtures/kov_layer2b/preamble_samples.json."""

    @pytest.mark.parametrize("sample", load_preamble_samples(),
                              ids=lambda s: s["id"])
    def test_sample(self, sample):
        from extract_cross_references import extract_preamble_citations

        cits = extract_preamble_citations(sample["text"])
        assert len(cits) == sample["expected_count"], (
            f"expected {sample['expected_count']} citations from "
            f"{sample['id']!r}, got {len(cits)}: {cits}"
        )

        if "expected_form" in sample:
            assert cits[0]["form"] == sample["expected_form"]
        if "expected_first_form" in sample:
            assert cits[0]["form"] == sample["expected_first_form"]
        if "expected_first_detail" in sample:
            assert cits[0]["citationDetail"] == sample["expected_first_detail"]
        if "expected_law_genitive" in sample:
            # Comparison is case-insensitive — preambles vary capitalization
            assert cits[0]["law_ref"].lower() == sample["expected_law_genitive"].lower()
        if "expected_paragraph" in sample:
            assert cits[0]["paragraphs"] == [sample["expected_paragraph"]]
        if "expected_detail" in sample:
            assert cits[0]["citationDetail"] == sample["expected_detail"]
        if "expected_issuer" in sample:
            assert cits[0]["issuer"] == sample["expected_issuer"]
        if "expected_adoption_date" in sample:
            assert cits[0]["adoption_date"] == sample["expected_adoption_date"]
        if "expected_act_number" in sample:
            assert cits[0]["act_number"] == sample["expected_act_number"]

    def test_empty_text_returns_empty(self):
        from extract_cross_references import extract_preamble_citations
        assert extract_preamble_citations("") == []
        assert extract_preamble_citations(None) == []

    def test_no_match_returns_empty(self):
        from extract_cross_references import extract_preamble_citations
        assert extract_preamble_citations("Plain text with no citations.") == []

    def test_chain_extension_emits_per_paragraph_citations(self):
        """Chained same-law § references must each carry their OWN
        lg/p detail, not the leading match's. Locks in the critical
        invariant: '§ 6 lg 3 p 2 ja § 22 lg 1 p 34' produces TWO
        citations, the second with its own (lg 1 p 34) detail."""
        from extract_cross_references import extract_preamble_citations
        cits = extract_preamble_citations(
            "Määrus kehtestatakse kohaliku omavalitsuse korralduse "
            "seaduse § 6 lg 3 p 2 ja § 22 lg 1 p 34 alusel."
        )
        assert len(cits) == 2
        # Same law_ref shared
        assert cits[0]["law_ref"] == cits[1]["law_ref"]
        # First paragraph has its own detail
        assert cits[0]["paragraphs"] == ["6"]
        assert cits[0]["citationDetail"] == "lg 3 p 2"
        # Second paragraph carries its OWN detail (lg 1 p 34, not lg 3 p 2)
        assert cits[1]["paragraphs"] == ["22"]
        assert cits[1]["citationDetail"] == "lg 1 p 34"


class TestCanonicalLookups:
    def test_extends_provision_index_with_prefix_to_act_iri_and_reverse(
        self, tmp_path, monkeypatch
    ):
        """build_provision_index now returns 5 things; the two new ones
        are prefix→act_iri AND act_iri→prefix. The reverse map is
        essential for Task 5's resolver, which needs to derive a prefix
        from an act IRI without splitting on '_' (34 corpus acts have
        prefixes containing underscores, e.g. 'KARIST_2_Map_2026')."""
        from extract_cross_references import build_provision_index
        import estleg_common
        import extract_cross_references as ecr

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        # Stage two law peeps: one straight prefix, one underscore-containing prefix
        (krr / "alkoholiseadus_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Alkoholiseadus",
                 "estleg:sourceAct": "Alkoholiseadus"},
                {"@id": "estleg:AS_Par_42",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:sourceAct": "Alkoholiseadus",
                 "estleg:paragrahv": "AS § 42"}
            ],
        }), encoding="utf-8")
        (krr / "karistusseadustik_osa1_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:KARIST_2_Osa1_1_87",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Karistusseadustik (osa 1)",
                 "estleg:sourceAct": "Karistusseadustik"},
                {"@id": "estleg:KARIST_2_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:sourceAct": "Karistusseadustik",
                 "estleg:paragrahv": "KarS § 1"}
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(ecr, "KRR_DIR", krr)

        result = build_provision_index()
        # Old return — 3 values; new return — 5 values (both maps appended)
        assert len(result) == 5
        (prefix_to_provisions, source_act_to_prefix, iri_to_file,
         prefix_to_act_iri, act_iri_to_prefix) = result

        # Prefix → act IRI
        assert prefix_to_act_iri.get("AS") == "estleg:AS_Map_2026"
        assert prefix_to_act_iri.get("KARIST_2") == "estleg:KARIST_2_Osa1_1_87"

        # Reverse direction — exact mirror of prefix_to_act_iri
        assert act_iri_to_prefix.get("estleg:AS_Map_2026") == "AS"
        # The reverse lookup MUST resolve to the multi-segment prefix,
        # not 'KARIST', because provisions are keyed under 'KARIST_2'.
        assert act_iri_to_prefix.get("estleg:KARIST_2_Osa1_1_87") == "KARIST_2"

    def test_genitive_to_act_iri_chains_through_abbrev(self):
        """Build the genitive_law_name → act_iri lookup by chaining
        FULLNAME_GENITIVE → abbrev → source_act_to_prefix → prefix_to_act_iri."""
        from extract_cross_references import build_genitive_to_act_iri

        # Mock the pieces — we test the chaining logic, not the
        # underlying registries (those are tested in build_provision_index).
        source_act_to_prefix = {
            "Alkoholiseadus": "AS",
            "Karistusseadustik": "KARIST",
        }
        prefix_to_act_iri = {
            "AS": "estleg:AS_Map_2026",
            "KARIST": "estleg:KARIST_2_Map_2026",
        }
        # FULLNAME_GENITIVE has e.g. "karistusseadustiku" → "KarS"
        # KNOWN_ABBREVIATIONS would map "KarS" → "Karistusseadustik"
        # The function we build wraps that chain.

        result = build_genitive_to_act_iri(
            source_act_to_prefix=source_act_to_prefix,
            prefix_to_act_iri=prefix_to_act_iri,
        )
        # The lookup contains lowercased keys so it can be queried
        # case-insensitively against parser output.
        assert "karistusseadustiku" in result
        assert result["karistusseadustiku"] == "estleg:KARIST_2_Map_2026"

    def test_state_regulation_lookup_reads_adoption_from_xml(self, tmp_path):
        """The corpus has zero estleg:adoptionDate — adoption_date is
        only in XML at <vastuvoetud><aktikuupaev>. The lookup builder
        pairs each peep to its XML via pair_peep_with_xml and reads
        the date from there."""
        from extract_cross_references import build_state_regulation_lookup

        # Stage a state regulation peep with globalId
        riik = tmp_path / "krr_outputs" / "regulations" / "riik"
        riik.mkdir(parents=True)
        (riik / "vv_112_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_VV112_Map_2019",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:GovernmentRegulation"],
                 "estleg:issuer": "Vabariigi Valitsus",
                 "estleg:globalId": "112112",
                 "estleg:actNumber": "112"}
                 # NOTE: NO estleg:adoptionDate — that's the corpus reality
            ],
        }), encoding="utf-8")
        # Stage matching XML with the vastuvoetud block
        rt = tmp_path / "data" / "riigiteataja" / "maarus"
        rt.mkdir(parents=True)
        (rt / "reg_112112.xml").write_text(
            '<akt globaalID="112112"><metaandmed>'
            '<vastuvoetud><aktikuupaev>2019-12-19</aktikuupaev></vastuvoetud>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = build_state_regulation_lookup(
            riik_root=riik, data_dir=tmp_path / "data" / "riigiteataja",
        )
        # Issuer is normalised at construction time, so the query key is
        # lowercased + diacritic-folded.
        assert lookup[("vabariigi valitsus", "2019-12-19", "112")] == \
            "estleg:Reg_VV112_Map_2019"

    def test_kov_act_lookup_uses_registry_label(self, tmp_path):
        """Issuer label comes from the registry's rdfs:label, not from
        slug→title-case (which would produce 'Noo' instead of 'Nõo')."""
        from extract_cross_references import build_kov_act_lookup

        # Stage a KOV peep + matching XML, plus an issuers registry
        # whose rdfs:label has a diacritic.
        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "noo_vallavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_NOO_5_Map_2020",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Nõo Vallavolikogu",  # canonical form
                 "estleg:globalId": "555000",
                 "estleg:actNumber": "5",
                 "estleg:enactedBy": {"@id": "estleg:Issuer_noo_vallavolikogu"}}
            ],
        }), encoding="utf-8")
        # Issuers registry — uses rdfs:label that the lookup should respect
        (krr / "issuers_kov_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Issuer_noo_vallavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 # Note: real registry has 'Noo Vallavolikogu' (ASCII
                 # because Layer 1 derived from slug). Test verifies
                 # the lookup respects whatever the registry says.
                 "rdfs:label": "Noo Vallavolikogu"}
            ],
        }), encoding="utf-8")
        # Matching XML
        rt = tmp_path / "data" / "riigiteataja" / "maarus_kov"
        rt.mkdir(parents=True)
        (rt / "reg_555000.xml").write_text(
            '<akt globaalID="555000"><metaandmed>'
            '<vastuvoetud><aktikuupaev>2020-05-15</aktikuupaev></vastuvoetud>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = build_kov_act_lookup(
            kov_root=kov.parent,
            data_dir=tmp_path / "data" / "riigiteataja",
            issuers_path=krr / "issuers_kov_peep.json",
        )
        # The act stamps "Nõo Vallavolikogu"; the lookup key is the
        # normalised version (lowercased + diacritic-folded).
        assert lookup[("noo vallavolikogu", "2020-05-15", "5")] == \
            "estleg:Reg_NOO_5_Map_2020"

    def test_kov_act_lookup_by_number_no_date(self, tmp_path):
        """Body-text references typically don't carry adoption_date.
        A separate lookup is keyed by (issuer_label_normalized,
        act_number) and stores a LIST of candidate IRIs — the same
        issuer can have several acts under the same actNumber across
        historical revisions, and the resolver must see all candidates
        to decide whether the match is unique."""
        from extract_cross_references import build_kov_act_lookup_by_number

        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_15_Map_2020",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:actNumber": "15"}
            ],
        }), encoding="utf-8")

        lookup = build_kov_act_lookup_by_number(kov_root=kov.parent)
        # The lookup key is (issuer_label_normalized, act_number) and
        # the value is a LIST of candidate IRIs.
        assert lookup[("tallinna linnavolikogu", "15")] == [
            "estleg:Reg_TLN_15_Map_2020"
        ]

    def test_kov_act_lookup_by_number_records_all_candidates(self, tmp_path):
        """When an issuer has reused the same act_number across
        historical revisions, the lookup MUST keep all candidates so
        the resolver can detect ambiguity and abstain."""
        from extract_cross_references import build_kov_act_lookup_by_number

        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        # Two acts, same issuer + same act number, different years
        (kov / "old_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_15_Map_2010",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:actNumber": "15"}
            ],
        }), encoding="utf-8")
        (kov / "new_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_15_Map_2020",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:actNumber": "15"}
            ],
        }), encoding="utf-8")

        lookup = build_kov_act_lookup_by_number(kov_root=kov.parent)
        candidates = lookup[("tallinna linnavolikogu", "15")]
        assert isinstance(candidates, list)
        assert sorted(candidates) == sorted([
            "estleg:Reg_TLN_15_Map_2010",
            "estleg:Reg_TLN_15_Map_2020",
        ])

    def test_kov_act_lookup_normalizes_diacritics(self, tmp_path):
        """Issuer labels in Layer 1's registry are slug-derived (ASCII),
        but acts carry the canonical form with diacritics. The lookup
        must normalise both sides so 'Põlva Vallavalitsus' on the act
        side matches a body-text reference written 'Polva Vallavalitsus'
        (and vice-versa)."""
        from extract_cross_references import build_kov_act_lookup_by_number

        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "polva_vallavalitsus"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_POLVA_5_Map_2020",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuer": "Põlva Vallavalitsus",   # canonical, with õ
                 "estleg:actNumber": "5"}
            ],
        }), encoding="utf-8")

        lookup = build_kov_act_lookup_by_number(kov_root=kov.parent)
        # Both diacritic and ASCII forms must hit the same candidate list.
        assert lookup[("polva vallavalitsus", "5")] == [
            "estleg:Reg_POLVA_5_Map_2020"
        ]


class TestCitationNodeBuilder:
    def test_iri_pattern_matches_shacl(self):
        from extract_cross_references import build_citation_iri
        # Use a corpus-shaped IRI as input — Reg_<id>_Map_<year>
        iri = build_citation_iri("estleg:Reg_1014955_Map_2026", seq=1)
        assert iri == "estleg:Citation_Reg_1014955_Map_2026_1"
        import re
        assert re.match(r"^estleg:Citation_[A-Za-z0-9_]+_[0-9]+$", iri)

    def test_citation_node_with_full_detail(self):
        from extract_cross_references import build_citation_node
        node = build_citation_node(
            iri="estleg:Citation_Reg_1014955_Map_2026_1",
            # Corpus shape for provisions is <prefix>_Par_<n> (NOT
            # <prefix>_Map_<year>_Par_<n>). The act IRI carries the
            # _Map_<year> suffix; provisions reuse the bare prefix.
            target_iri="estleg:KOKS_Par_22",
            citation_detail="lg 1 p 34",
            citation_text="kohaliku omavalitsuse korralduse seaduse § 22 lõike 1 punkti 34",
        )
        assert node["@id"] == "estleg:Citation_Reg_1014955_Map_2026_1"
        assert "estleg:Citation" in node["@type"]
        assert "owl:NamedIndividual" in node["@type"]
        assert node["estleg:citationTarget"] == {"@id": "estleg:KOKS_Par_22"}
        assert node["estleg:citationDetail"] == "lg 1 p 34"
        assert node["estleg:citationText"].startswith("kohaliku omavalitsuse")

    def test_citation_node_omits_optional_when_none(self):
        from extract_cross_references import build_citation_node
        node = build_citation_node(
            iri="estleg:Citation_Reg_X_Map_2026_1",
            target_iri="estleg:Reg_Y_Map_2020",  # act-level when no §
            citation_detail=None,
            citation_text=None,
        )
        assert "estleg:citationTarget" in node
        assert "estleg:citationDetail" not in node
        assert "estleg:citationText" not in node


class TestResolvePreambleCitation:
    @pytest.fixture
    def lookups(self):
        # Fixtures use corpus-shaped IRIs throughout:
        #   act IRIs: <prefix>_Map_<year> or <prefix>_Osa<n>_...
        #   provision IRIs: <prefix>_Par_<n>  (NO _Map_<year> in the middle)
        return {
            "genitive_to_act_iri": {
                "kohaliku omavalitsuse korralduse seaduse": "estleg:KOKS_Map_2026",
                "alkoholiseaduse": "estleg:AS_Map_2026",
                "karistusseadustiku": "estleg:KARIST_2_Osa1_1_87",
            },
            "prefix_to_provisions": {
                "KOKS": {"22": "estleg:KOKS_Par_22",
                          "6": "estleg:KOKS_Par_6"},
                "AS":   {"42": "estleg:AS_Par_42"},
                "KARIST_2": {"1": "estleg:KARIST_2_Par_1"},
            },
            "act_iri_to_prefix": {
                "estleg:KOKS_Map_2026": "KOKS",
                "estleg:AS_Map_2026": "AS",
                # Multi-segment prefix — the case the v2 plan got wrong.
                "estleg:KARIST_2_Osa1_1_87": "KARIST_2",
            },
            "state_reg_lookup": {
                ("vabariigi valitsus", "2007-12-20", "251"):
                    "estleg:Reg_VV251_Map_2007",
            },
            "kov_act_lookup": {
                ("tallinna linnavolikogu", "2020-12-02", "60"):
                    "estleg:Reg_TLN60_Map_2020",
            },
        }

    def test_law_with_paragraph_returns_act_level_issuedUnder(self, lookups):
        """issuedUnder must be act-level, citation_target is the provision."""
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "kohaliku omavalitsuse korralduse seaduse",
            "paragraphs": ["22"],
            "citationDetail": "lg 1 p 34",
            "citationText": "kohaliku omavalitsuse korralduse seaduse § 22 lõike 1 punkti 34",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result is not None
        target_iri, enabling_act_iri = result
        # Provision-level for citation target — corpus shape is <prefix>_Par_<n>
        assert target_iri == "estleg:KOKS_Par_22"
        # Act-level for issuedUnder — this is the key Layer 2b semantic
        assert enabling_act_iri == "estleg:KOKS_Map_2026"

    def test_law_without_paragraph(self, lookups):
        """When citation has no §, both target and enabling_act are act-level."""
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "alkoholiseaduse",
            "paragraphs": [],
            "citationDetail": None,
            "citationText": "alkoholiseaduse",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result == ("estleg:AS_Map_2026", "estleg:AS_Map_2026")

    def test_law_with_multipart_prefix_resolves_via_reverse_map(self, lookups):
        """Reverse map (act_iri_to_prefix) MUST be consulted for the
        prefix — naive split on '_' would yield 'KARIST' for
        'estleg:KARIST_2_Osa1_1_87' and miss the real prefix
        'KARIST_2'. This is the regression that motivated the
        round-3 review (B1)."""
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "karistusseadustiku",
            "paragraphs": ["1"],
            "citationDetail": None,
            "citationText": "karistusseadustiku § 1",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result == (
            "estleg:KARIST_2_Par_1",       # provision under the multi-segment prefix
            "estleg:KARIST_2_Osa1_1_87",   # act IRI with the same multi-segment prefix
        )

    def test_state_regulation(self, lookups):
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "state-regulation-date-number",
            "issuer": "Vabariigi Valitsus",
            "adoption_date": "2007-12-20",
            "act_number": "251",
            "citationDetail": None,
            "citationText": "Vabariigi Valitsuse 20.12.2007 määrusega nr 251",
        }
        result = resolve_preamble_citation(cit, **lookups)
        # Date+number form has no provision granularity — both equal
        assert result == ("estleg:Reg_VV251_Map_2007",
                          "estleg:Reg_VV251_Map_2007")

    def test_kov_regulation(self, lookups):
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "kov-regulation-date-number",
            "issuer": "Tallinna Linnavolikogu",
            "adoption_date": "2020-12-02",
            "act_number": "60",
            "citationDetail": None,
            "citationText": "Tallinna Linnavolikogu 2. detsembri 2010 määruse nr 60",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result == ("estleg:Reg_TLN60_Map_2020",
                          "estleg:Reg_TLN60_Map_2020")

    def test_unresolved_returns_none(self, lookups):
        from extract_cross_references import resolve_preamble_citation
        cit = {
            "form": "law-genitive",
            "law_ref": "ettetuntmatu_seaduse",
            "paragraphs": ["1"],
            "citationDetail": None,
            "citationText": "ettetuntmatu seaduse § 1",
        }
        result = resolve_preamble_citation(cit, **lookups)
        assert result is None


class TestExtractKovActRefsFromText:
    def test_parses_full_issuer_label(self):
        from extract_cross_references import extract_kov_act_refs_from_text
        refs = extract_kov_act_refs_from_text(
            "Vastavalt Tallinna Linnavolikogu määruse nr 15 § 4 lõikele 1 ..."
        )
        assert len(refs) == 1
        assert refs[0]["issuer"] == "tallinna linnavolikogu"  # normalised
        assert refs[0]["body_type"] == "volikogu"
        assert refs[0]["act_number"] == "15"

    def test_parses_generic_body_word_no_place_name(self):
        from extract_cross_references import extract_kov_act_refs_from_text
        refs = extract_kov_act_refs_from_text(
            "linnavolikogu määrus nr 5 sätestab täpsemad tingimused."
        )
        assert len(refs) == 1
        assert refs[0]["issuer"] is None         # no place name → ambiguous
        assert refs[0]["body_type"] == "volikogu"
        assert refs[0]["act_number"] == "5"

    def test_parses_diacritic_issuer(self):
        from extract_cross_references import extract_kov_act_refs_from_text
        refs = extract_kov_act_refs_from_text(
            "Põlva Vallavalitsuse määrusega nr 3 kehtestatud korras."
        )
        assert len(refs) == 1
        assert refs[0]["issuer"] == "polva vallavalitsus"  # normalised + transliterated
        assert refs[0]["body_type"] == "valitsus"
        assert refs[0]["act_number"] == "3"

    def test_no_match_returns_empty(self):
        from extract_cross_references import extract_kov_act_refs_from_text
        assert extract_kov_act_refs_from_text("Plain text.") == []
        assert extract_kov_act_refs_from_text("") == []

    def test_parses_multi_word_place_name(self):
        """Regression: 'Järva Jaani' is a real two-word KOV name. The
        prefix trimmer must NOT mistakenly drop 'Järva' as a leading
        preamble word — it's part of the place name."""
        from extract_cross_references import extract_kov_act_refs_from_text
        refs = extract_kov_act_refs_from_text(
            "Vastavalt Järva Jaani Vallavolikogu määruse nr 5 § 2 ..."
        )
        assert len(refs) == 1
        # Trim removes 'Vastavalt' (preamble verb), preserves
        # 'Järva Jaani' (place name).
        assert refs[0]["issuer"] == "jarva jaani vallavolikogu"
        assert refs[0]["body_type"] == "volikogu"
        assert refs[0]["act_number"] == "5"


class TestKovBodyTextScope:
    @pytest.fixture
    def fixture_data(self):
        # Two acts under different municipalities. Both have the same
        # actNumber to expose any cross-municipality leakage.
        kov_act_lookup_by_number = {
            ("tallinna linnavolikogu", "15"): ["estleg:Reg_TLN15_Map_2020"],
            ("tartu linnavolikogu", "15"): ["estleg:Reg_TRT15_Map_2018"],
            # Ambiguous case — same issuer reused number 7 across revisions
            ("polva vallavolikogu", "7"): [
                "estleg:Reg_POLVA7_Map_2010",
                "estleg:Reg_POLVA7_Map_2018",
            ],
        }
        issuer_registry = {
            "estleg:Issuer_tallinna_linnavolikogu": (
                "tallinna linnavolikogu",
                "estleg:Municipality_tallinn", "volikogu",
            ),
            "estleg:Issuer_tartu_linnavolikogu": (
                "tartu linnavolikogu",
                "estleg:Municipality_tartu", "volikogu",
            ),
            "estleg:Issuer_polva_vallavolikogu": (
                "polva vallavolikogu",
                "estleg:Municipality_polva_vald", "volikogu",
            ),
        }
        return kov_act_lookup_by_number, issuer_registry

    def test_explicit_issuer_in_same_municipality_resolves(self, fixture_data):
        from extract_cross_references import resolve_kov_internal_act_ref
        lookup, registry = fixture_data
        target = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_tallinn",
            explicit_issuer="tallinna linnavolikogu",
            body_type="volikogu",
            act_number="15",
            kov_act_lookup_by_number=lookup,
            issuer_registry=registry,
        )
        assert target == "estleg:Reg_TLN15_Map_2020"

    def test_explicit_issuer_in_different_municipality_returns_none(
        self, fixture_data
    ):
        """Tallinn act citing 'Tartu Linnavolikogu' explicitly is
        cross-municipality — skip rather than resolve."""
        from extract_cross_references import resolve_kov_internal_act_ref
        lookup, registry = fixture_data
        target = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_tallinn",
            explicit_issuer="tartu linnavolikogu",
            body_type="volikogu",
            act_number="15",
            kov_act_lookup_by_number=lookup,
            issuer_registry=registry,
        )
        assert target is None

    def test_generic_body_word_resolves_via_municipality_scope(
        self, fixture_data
    ):
        """No explicit issuer → enumerate volikogus in source
        municipality. Tallinn has only one volikogu, so this resolves
        uniquely."""
        from extract_cross_references import resolve_kov_internal_act_ref
        lookup, registry = fixture_data
        target = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_tallinn",
            explicit_issuer=None,
            body_type="volikogu",
            act_number="15",
            kov_act_lookup_by_number=lookup,
            issuer_registry=registry,
        )
        assert target == "estleg:Reg_TLN15_Map_2020"

    def test_ambiguous_candidates_returns_none(self, fixture_data):
        """When the issuer has reused the act_number across revisions,
        the resolver MUST abstain rather than pick a 'latest by file
        order' winner."""
        from extract_cross_references import resolve_kov_internal_act_ref
        lookup, registry = fixture_data
        target = resolve_kov_internal_act_ref(
            source_municipality="estleg:Municipality_polva_vald",
            explicit_issuer="polva vallavolikogu",
            body_type="volikogu",
            act_number="7",
            kov_act_lookup_by_number=lookup,
            issuer_registry=registry,
        )
        assert target is None


class TestKovBodyTextSavesFile:
    """A KOV peep that gains references ONLY from the new Layer 2b
    body-text branch must still be saved and counted. Tests that
    `modified = True` and the three counters fire even when the
    in-law branch found nothing."""

    def test_kov_only_body_text_triggers_save(self, tmp_path):
        from extract_cross_references import process_law_file

        # Stage a KOV peep whose body text references a same-municipality
        # act by issuer + number, with no in-law citations.
        peep = tmp_path / "kov_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_Test_Map_2024",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"},
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:actNumber": "999"},
                {"@id": "estleg:Reg_TLN_Test_Map_2024_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:legalText":
                    "Vastavalt Tallinna Linnavolikogu määruse nr 15 § 4 ..."},
            ],
        }), encoding="utf-8")

        kov_lookup = {
            ("tallinna linnavolikogu", "15"): ["estleg:Reg_TLN15_Map_2020"],
        }
        registry = {
            "estleg:Issuer_tallinna_linnavolikogu": (
                "tallinna linnavolikogu",
                "estleg:Municipality_tallinn", "volikogu",
            ),
        }

        stats = process_law_file(
            peep,
            abbrev_to_prefix={},
            prefix_to_provisions={},
            iri_to_file={},
            kov_act_lookup_by_number=kov_lookup,
            issuer_registry=registry,
        )

        # File must have been saved with the new ref, and stats must
        # reflect that the KOV-only branch fired.
        assert stats["citations_found"] >= 1
        assert stats["citations_resolved"] >= 1
        assert stats["provisions_with_refs"] >= 1

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_1"))
        refs = prov.get("estleg:references", [])
        if isinstance(refs, dict):
            refs = [refs]
        assert {"@id": "estleg:Reg_TLN15_Map_2020"} in refs
        # Temporary marker MUST NOT be persisted.
        assert "_layer2b_counted" not in prov


class TestPreamblePassIdempotency:
    """When a prior preamble pass produced output but the current run
    produces no citations (parser tightening, source-act removal, or
    edited preambleText), the cleared in-memory mutations MUST persist
    to disk. Otherwise stale issuedUnder / implementsCitation /
    Citation_* triples survive on the next re-read."""

    def test_cleared_when_fresh_parse_empty_persists_to_disk(self, tmp_path):
        """The bug: with prior output present and fresh parse empty,
        the file was mutated in memory (pop / graph[:]) but NEVER saved
        because the save was gated only on positive output. The fix:
        save when EITHER fresh output OR had_existing."""
        from extract_cross_references import _process_preamble_for_act

        # Stage a peep with prior pass output and a preambleText that
        # the parser cannot extract any citation from.
        peep = tmp_path / "stale_preamble_peep.json"
        doc = {
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {
                    "@id": "estleg:Reg_TLN_Test_Map_2024",
                    "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                    "estleg:preambleText":
                        "Plain text with no citations.",
                    # Prior-pass output the new run must clear:
                    "estleg:issuedUnder": [
                        {"@id": "estleg:KOKS_Map_2026"}
                    ],
                    "estleg:implementsCitation": [
                        {"@id": "estleg:Citation_Reg_TLN_Test_Map_2024_1"}
                    ],
                },
                {
                    # A stale Citation node from a prior parse:
                    "@id": "estleg:Citation_Reg_TLN_Test_Map_2024_1",
                    "@type": ["owl:NamedIndividual", "estleg:Citation"],
                    "estleg:citationTarget": {"@id": "estleg:KOKS_Par_22"},
                    "estleg:citationDetail": "lg 1 p 34",
                    "estleg:citationText":
                        "kohaliku omavalitsuse korralduse seaduse § 22 "
                        "lõike 1 punkti 34",
                },
            ],
        }
        peep.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                        encoding="utf-8")

        # Re-load to pass a fresh dict to the helper (mirrors main()).
        with open(peep, "r", encoding="utf-8") as fh:
            doc_in = json.load(fh)

        result = _process_preamble_for_act(
            peep,
            doc_in,
            genitive_to_act_iri={},
            prefix_to_provisions={},
            act_iri_to_prefix={},
            state_reg_lookup={},
            kov_act_lookup={},
        )

        assert result is not None
        # No fresh output (parser cannot match), but had_existing must
        # be True since we staged prior triples.
        assert result["had_output"] is False
        assert result["had_existing"] is True
        # Helper must save the cleared state to disk.
        assert result["saved"] is True

        # Reload from disk — the cleared state MUST persist.
        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)

        act_node = next(n for n in saved["@graph"]
                        if n["@id"] == "estleg:Reg_TLN_Test_Map_2024")
        assert "estleg:issuedUnder" not in act_node
        assert "estleg:implementsCitation" not in act_node
        # No Citation_<shortid>_* nodes survive in the graph.
        assert all(
            not (isinstance(n.get("@id"), str)
                 and n["@id"].startswith(
                     "estleg:Citation_Reg_TLN_Test_Map_2024_"))
            for n in saved["@graph"]
        )

    def test_no_existing_no_fresh_does_not_save(self, tmp_path):
        """When neither prior output nor fresh output is present,
        the helper must NOT save (no spurious file writes)."""
        from extract_cross_references import _process_preamble_for_act

        peep = tmp_path / "empty_preamble_peep.json"
        doc = {
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {
                    "@id": "estleg:Reg_TLN_Empty_Map_2024",
                    "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                    "estleg:preambleText":
                        "Plain text with no citations.",
                },
            ],
        }
        peep.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        mtime_before = peep.stat().st_mtime_ns

        with open(peep, "r", encoding="utf-8") as fh:
            doc_in = json.load(fh)

        result = _process_preamble_for_act(
            peep,
            doc_in,
            genitive_to_act_iri={},
            prefix_to_provisions={},
            act_iri_to_prefix={},
            state_reg_lookup={},
            kov_act_lookup={},
        )

        assert result is not None
        assert result["had_output"] is False
        assert result["had_existing"] is False
        assert result["saved"] is False
        # Verify the file was NOT touched on disk.
        assert peep.stat().st_mtime_ns == mtime_before


class TestIsProvisionNodeHelper:
    """The single-source-of-truth predicate for "is this a provision".

    Both citation passes (in-law and KOV body-text) used to disagree on
    this question — one keyed on ``'_Par_' in @id`` while the other
    keyed on ``'estleg:paragrahv' not in node``. Locking in the
    canonical predicate prevents that drift returning."""

    @pytest.mark.parametrize("node, expected", [
        # Provision nodes — _Par_ in @id
        ({"@id": "estleg:KOKS_Par_22"}, True),
        ({"@id": "estleg:KARIST_2_Par_1"}, True),
        ({"@id": "estleg:Reg_TLN_Test_Map_2024_Par_1"}, True),
        # Provision shapes that LACK estleg:paragrahv (the old KOV pass
        # would have skipped these — the new predicate picks them up).
        ({"@id": "estleg:AS_Par_42",
          "@type": ["owl:NamedIndividual"]}, True),

        # Non-provisions — owl:Ontology act nodes, citation nodes,
        # malformed inputs, missing @id.
        ({"@id": "estleg:KOKS_Map_2026"}, False),
        ({"@id": "estleg:Citation_KOKS_Map_2026_1"}, False),
        ({"@id": ""}, False),
        ({}, False),
        # Defensive: non-string @id and non-dict input.
        ({"@id": 42}, False),
        ({"@id": None}, False),
        (None, False),
        ([], False),
    ])
    def test_predicate(self, node, expected):
        from extract_cross_references import is_provision_node
        assert is_provision_node(node) is expected


class TestRunInlawCitationPass:
    """The in-law pass extracts citations from the JSON-LD summary
    and/or paired XML text and attaches estleg:references to each
    matched provision node. This test drives the helper directly with
    an in-memory graph so the assertion isolates the pass logic from
    file IO."""

    def test_run_inlaw_citation_pass_produces_correct_references(self):
        from extract_cross_references import _run_inlaw_citation_pass

        # A provision whose summary contains a self-reference and a
        # cross-law citation. self_prefix='KARIST_2' matches a real
        # multi-segment corpus prefix; the citation '§ 121' must
        # resolve via the registry.
        graph = [
            {"@id": "estleg:KARIST_2_Map_2026",
             "@type": ["owl:Ontology"]},
            {"@id": "estleg:KARIST_2_Par_1",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             "estleg:summary":
                 "Käesoleva seaduse § 121 alusel kohaldatakse VÕS § 208."},
        ]
        prefix_to_provisions = {
            "KARIST_2": {
                "1": "estleg:KARIST_2_Par_1",
                "121": "estleg:KARIST_2_Par_121",
            },
            # 'VÕS' → 'Vlaisustseadus' or similar — only the prefix
            # mapping matters for the resolve, so use a stub prefix.
            "VOS_STUB": {"208": "estleg:VOS_STUB_Par_208"},
        }
        abbrev_to_prefix = {"VÕS": "VOS_STUB"}

        stats = _run_inlaw_citation_pass(
            graph,
            self_prefix="KARIST_2",
            abbrev_to_prefix=abbrev_to_prefix,
            prefix_to_provisions=prefix_to_provisions,
            xml_par_texts={},
        )

        assert stats["modified"] is True
        assert stats["provisions_scanned"] == 1
        assert stats["provisions_with_refs"] == 1
        # Two paragraphs cited (121 self + 208 VÕS), both resolve.
        assert stats["citations_found"] == 2
        assert stats["citations_resolved"] == 2

        # The provision must carry both refs, with self-link to itself
        # filtered out (the resolver tries Par_1 against self_prefix
        # 'KARIST_2' only when the citation says so — this test cites
        # 121 and 208, so neither equals @id Par_1).
        prov = next(n for n in graph
                    if n["@id"] == "estleg:KARIST_2_Par_1")
        refs = prov["estleg:references"]
        ref_ids = {r["@id"] for r in refs}
        assert "estleg:KARIST_2_Par_121" in ref_ids
        assert "estleg:VOS_STUB_Par_208" in ref_ids

    def test_inlaw_pass_skips_self_link(self):
        """When the self-reference resolves to the provision itself,
        the helper must drop it (no estleg:references → self)."""
        from extract_cross_references import _run_inlaw_citation_pass

        graph = [
            {"@id": "estleg:KOKS_Par_1",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             # Self-reference whose paragraph IS this provision.
             "estleg:summary": "Käesoleva seaduse § 1 alusel ..."},
        ]
        stats = _run_inlaw_citation_pass(
            graph,
            self_prefix="KOKS",
            abbrev_to_prefix={},
            prefix_to_provisions={"KOKS": {"1": "estleg:KOKS_Par_1"}},
            xml_par_texts={},
        )
        # Citation found+resolved, but the only resolved ref equals
        # the provision's @id and is filtered, so no node mutation.
        assert stats["citations_found"] == 1
        assert stats["citations_resolved"] == 1
        assert stats["provisions_with_refs"] == 0
        assert stats["modified"] is False
        assert "estleg:references" not in graph[0]


class TestRunKovBodyPass:
    """The KOV body-text pass extracts ``<Issuer> määruse nr N``
    references from each provision's legalText/summary and attaches
    them as estleg:references. This test drives the helper directly
    so the assertion isolates the pass from process_law_file."""

    def test_run_kov_body_pass_emits_implementsCitation(self):
        from extract_cross_references import _run_kov_body_pass

        graph = [
            {"@id": "estleg:Reg_TLN_Test_Map_2024",
             "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
             "estleg:enactedByMunicipality": {
                 "@id": "estleg:Municipality_tallinn"},
             "estleg:issuer": "Tallinna Linnavolikogu",
             "estleg:actNumber": "999"},
            {"@id": "estleg:Reg_TLN_Test_Map_2024_Par_1",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             "estleg:legalText":
                 "Vastavalt Tallinna Linnavolikogu määruse nr 15 § 4 ..."},
        ]
        kov_lookup = {
            ("tallinna linnavolikogu", "15"): ["estleg:Reg_TLN15_Map_2020"],
        }
        registry = {
            "estleg:Issuer_tallinna_linnavolikogu": (
                "tallinna linnavolikogu",
                "estleg:Municipality_tallinn", "volikogu",
            ),
        }

        stats = _run_kov_body_pass(
            graph,
            kov_act_lookup_by_number=kov_lookup,
            issuer_registry=registry,
        )

        assert stats["modified"] is True
        assert stats["citations_found"] == 1
        assert stats["citations_resolved"] == 1
        assert stats["provisions_with_refs"] == 1
        assert "estleg:Reg_TLN_Test_Map_2024_Par_1" in stats["counted_ids"]

        prov = graph[1]
        refs = prov["estleg:references"]
        # The KOV target was appended.
        assert {"@id": "estleg:Reg_TLN15_Map_2020"} in refs

    def test_kov_body_pass_returns_frozenset_counted_ids(self):
        """counted_ids must be a frozenset (immutable post-return).
        The old code stamped ``_layer2b_counted`` on each provision
        which leaked to disk; the new contract returns the set in the
        stats dict and never touches the document."""
        from extract_cross_references import _run_kov_body_pass

        graph = [
            {"@id": "estleg:Reg_X_Map_2024",
             "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
             "estleg:enactedByMunicipality": {
                 "@id": "estleg:Municipality_tallinn"}},
            {"@id": "estleg:Reg_X_Map_2024_Par_1",
             "@type": ["owl:NamedIndividual"],
             "estleg:legalText":
                 "Tallinna Linnavolikogu määruse nr 15 § 1 ..."},
        ]
        stats = _run_kov_body_pass(
            graph,
            kov_act_lookup_by_number={
                ("tallinna linnavolikogu", "15"):
                    ["estleg:Reg_TLN15_Map_2020"],
            },
            issuer_registry={
                "estleg:Issuer_tallinna_linnavolikogu": (
                    "tallinna linnavolikogu",
                    "estleg:Municipality_tallinn", "volikogu",
                ),
            },
        )
        assert isinstance(stats["counted_ids"], frozenset)


class TestSelfReferencePrefix:
    """The self-reference prefix MUST come from act_iri_to_prefix
    (registry-built, authoritative for the 34 corpus acts whose prefix
    contains underscores). The earlier code derived it via
    ``local.split('_Par_')[0]`` which works coincidentally for most
    files but is fragile to graph-iteration order and silently
    incorrect for a few legacy peeps."""

    def test_self_reference_prefix_uses_registry_not_string_split(self):
        from extract_cross_references import _derive_self_prefix

        # Multi-segment prefix corpus shape: an act_iri whose prefix
        # itself contains underscores. The registry-driven path MUST
        # return the multi-segment prefix even though the provision
        # @id below would also work via the fallback string split.
        graph = [
            {"@id": "estleg:KARIST_2_Osa1_1_87",
             "@type": ["owl:Ontology"]},
            {"@id": "estleg:KARIST_2_Par_1",
             "@type": ["owl:NamedIndividual"]},
        ]
        # Registry maps the act IRI to the multi-segment prefix.
        act_iri_to_prefix = {
            "estleg:KARIST_2_Osa1_1_87": "KARIST_2",
        }
        prefix = _derive_self_prefix(graph, act_iri_to_prefix)
        assert prefix == "KARIST_2"

    def test_self_reference_prefix_falls_back_when_registry_missing(self):
        """When act_iri_to_prefix is None or doesn't carry the act,
        the helper falls back to _prefix_from_act_iri (suffix-stripper)
        before resorting to the legacy provision-id string split."""
        from extract_cross_references import _derive_self_prefix

        graph = [
            {"@id": "estleg:KOKS_Map_2026",
             "@type": ["owl:Ontology"]},
            {"@id": "estleg:KOKS_Par_22",
             "@type": ["owl:NamedIndividual"]},
        ]
        prefix = _derive_self_prefix(graph, act_iri_to_prefix=None)
        assert prefix == "KOKS"

    def test_self_reference_prefix_handles_no_ontology_node(self):
        """Last-resort path: when the peep has no owl:Ontology node
        (rare legacy fragments), the helper still returns a usable
        prefix from the first provision @id."""
        from extract_cross_references import _derive_self_prefix

        graph = [
            {"@id": "estleg:LEGACY_Par_1",
             "@type": ["owl:NamedIndividual"]},
        ]
        prefix = _derive_self_prefix(graph, act_iri_to_prefix={})
        assert prefix == "LEGACY"

    def test_self_reference_prefix_threaded_through_process_law_file(
        self, tmp_path
    ):
        """The orchestrator must consult act_iri_to_prefix when given.
        Ensures the kwarg actually reaches _derive_self_prefix and
        beats the legacy provision-id split path."""
        from extract_cross_references import process_law_file

        # Stage a peep whose act_iri carries a multi-segment prefix
        # but whose ONLY provision is keyed under a different
        # surface form. The legacy split path would derive 'KARIST'
        # from 'KARIST_2_Par_1'; the registry path returns 'KARIST_2'.
        peep = tmp_path / "karist_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:KARIST_2_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act"]},
                {"@id": "estleg:KARIST_2_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 # Self-reference to § 99 — resolves only when the
                 # prefix index agrees on 'KARIST_2'.
                 "estleg:summary": "Käesoleva seaduse § 99 alusel ..."},
            ],
        }), encoding="utf-8")

        # Provision index keyed under the multi-segment prefix.
        prefix_to_provisions = {
            "KARIST_2": {
                "1": "estleg:KARIST_2_Par_1",
                "99": "estleg:KARIST_2_Par_99",
            },
        }
        act_iri_to_prefix = {"estleg:KARIST_2_Map_2026": "KARIST_2"}

        stats = process_law_file(
            peep,
            abbrev_to_prefix={},
            prefix_to_provisions=prefix_to_provisions,
            iri_to_file={},
            act_iri_to_prefix=act_iri_to_prefix,
        )
        assert stats["citations_resolved"] == 1
        # Provision must carry the resolved self-reference.
        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n["@id"] == "estleg:KARIST_2_Par_1")
        assert {"@id": "estleg:KARIST_2_Par_99"} in prov["estleg:references"]


class TestLayer2bMarkerDoesNotLeak:
    """The temporary bookkeeping flag ``_layer2b_counted`` was
    historically stamped on provision nodes and stripped just before
    save. If the save loop crashed mid-iteration, or if save_json
    silently failed, the marker would leak to disk. The new contract
    tracks counted ids in a local set, never mutating the document."""

    def test_layer2b_marker_does_not_leak_to_output(self, tmp_path):
        from extract_cross_references import process_law_file

        peep = tmp_path / "kov_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_Test_Map_2024",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"},
                 "estleg:issuer": "Tallinna Linnavolikogu",
                 "estleg:actNumber": "999"},
                {"@id": "estleg:Reg_TLN_Test_Map_2024_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:legalText":
                     "Vastavalt Tallinna Linnavolikogu määruse nr 15 § 4 ..."},
            ],
        }), encoding="utf-8")

        kov_lookup = {
            ("tallinna linnavolikogu", "15"): ["estleg:Reg_TLN15_Map_2020"],
        }
        registry = {
            "estleg:Issuer_tallinna_linnavolikogu": (
                "tallinna linnavolikogu",
                "estleg:Municipality_tallinn", "volikogu",
            ),
        }

        process_law_file(
            peep,
            abbrev_to_prefix={},
            prefix_to_provisions={},
            iri_to_file={},
            kov_act_lookup_by_number=kov_lookup,
            issuer_registry=registry,
        )

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)

        # No node — provision OR act — may carry the temporary marker.
        for node in saved["@graph"]:
            assert "_layer2b_counted" not in node, (
                f"_layer2b_counted leaked to disk on {node.get('@id')}"
            )


# ---------------------------------------------------------------------------
# Regression for #121: text fields (estleg:summary / estleg:legalText /
# rdfs:label / estleg:sourceAct) must be read through jsonld_text so a
# fresh-generator value-object corpus is parsed identically to a normalized
# plain-string one.
# ---------------------------------------------------------------------------

class TestValueObjectTextReads:
    def test_inlaw_pass_handles_value_object_summary(self):
        """A provision whose estleg:summary is a {@value,@language} object
        yields the same estleg:references as the plain-string form
        (covers _load_provision_text)."""
        from extract_cross_references import _run_inlaw_citation_pass

        summary_text = "Käesoleva seaduse § 121 alusel kohaldatakse VÕS § 208."
        prefix_to_provisions = {
            "KARIST_2": {
                "1": "estleg:KARIST_2_Par_1",
                "121": "estleg:KARIST_2_Par_121",
            },
            "VOS_STUB": {"208": "estleg:VOS_STUB_Par_208"},
        }
        abbrev_to_prefix = {"VÕS": "VOS_STUB"}

        def run(summary_value):
            graph = [
                {"@id": "estleg:KARIST_2_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:summary": summary_value},
            ]
            stats = _run_inlaw_citation_pass(
                graph,
                self_prefix="KARIST_2",
                abbrev_to_prefix=abbrev_to_prefix,
                prefix_to_provisions=prefix_to_provisions,
                xml_par_texts={},
            )
            prov = graph[0]
            return stats, {r["@id"] for r in prov.get("estleg:references", [])}

        plain_stats, plain_refs = run(summary_text)
        vo_stats, vo_refs = run({"@value": summary_text, "@language": "et"})
        list_stats, list_refs = run([{"@value": summary_text, "@language": "et"}])

        assert plain_stats["citations_resolved"] == 2
        assert plain_refs == {"estleg:KARIST_2_Par_121", "estleg:VOS_STUB_Par_208"}
        assert (vo_stats["citations_resolved"], vo_refs) == (2, plain_refs)
        assert (list_stats["citations_resolved"], list_refs) == (2, plain_refs)

    def test_kov_body_pass_handles_value_object_text_fields(self):
        """estleg:legalText / estleg:summary as value objects produce the
        same KOV references as plain strings."""
        from extract_cross_references import _run_kov_body_pass

        body = "Vastavalt Tallinna Linnavolikogu määruse nr 15 § 4 ..."
        kov_lookup = {
            ("tallinna linnavolikogu", "15"): ["estleg:Reg_TLN15_Map_2020"],
        }
        registry = {
            "estleg:Issuer_tallinna_linnavolikogu": (
                "tallinna linnavolikogu",
                "estleg:Municipality_tallinn", "volikogu",
            ),
        }

        def run(legal_text_value):
            graph = [
                {"@id": "estleg:Reg_TLN_Test_Map_2024",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_TLN_Test_Map_2024_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:legalText": legal_text_value},
            ]
            stats = _run_kov_body_pass(
                graph,
                kov_act_lookup_by_number=kov_lookup,
                issuer_registry=registry,
            )
            return stats, [r["@id"] for r in graph[1].get("estleg:references", [])]

        plain_stats, plain_refs = run(body)
        vo_stats, vo_refs = run({"@value": body, "@language": "et"})

        assert plain_stats["citations_resolved"] == 1
        assert plain_refs == ["estleg:Reg_TLN15_Map_2020"]
        assert (vo_stats["citations_resolved"], vo_refs) == (1, plain_refs)

    def test_build_issuer_registry_handles_value_object_label(self, tmp_path):
        """build_issuer_registry must unwrap a {@value,@language} rdfs:label
        so the normalised label key is the text, not a stringified dict."""
        from extract_cross_references import build_issuer_registry

        issuers = tmp_path / "issuers_kov_peep.json"
        issuers.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [
                {"@id": "estleg:Issuer_tallinna_linnavolikogu",
                 "@type": ["estleg:Issuer"],
                 "rdfs:label": {"@value": "Tallinna Linnavolikogu",
                                "@language": "et"},
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tallinn"},
                 "estleg:bodyType": {"@id": "estleg:BodyType_volikogu"}},
            ],
        }), encoding="utf-8")

        registry = build_issuer_registry(issuers)
        assert "estleg:Issuer_tallinna_linnavolikogu" in registry
        label_norm, muni, body_type = registry["estleg:Issuer_tallinna_linnavolikogu"]
        assert label_norm == "tallinna linnavolikogu"
        assert "@value" not in label_norm
        assert muni == "estleg:Municipality_tallinn"


class TestSuperscriptParagraphCitations:
    """Regression for #254: the § citation parser dropped superscripts, so
    ``AÕS § 158²`` resolved to the WRONG provision (``_Par_158`` instead of
    ``_Par_158_2``), and the XML paragraph-text key collapsed ``§ 158`` and
    ``§ 158²`` to the same dict key (last-write-wins). These tests lock in
    superscript-aware capture (Unicode glyph AND ``<sup>`` markup),
    normalisation into the ``_<m>`` IRI suffix, and distinct XML keys."""

    # -- parser: capture + normalise the superscript ------------------

    def test_unicode_superscript_captured_as_suffix_key(self):
        from extract_cross_references import extract_citations_from_text

        cits = extract_citations_from_text("AÕS § 158² kohaselt kohaldatakse.")
        assert len(cits) == 1
        assert cits[0]["law_ref"] == "AÕS"
        # Folded into the generator's IRI-key shape, NOT bare "158".
        assert cits[0]["paragraphs"] == ["158_2"]

    def test_sup_markup_superscript_captured_as_suffix_key(self):
        from extract_cross_references import extract_citations_from_text

        cits = extract_citations_from_text(
            "AÕS § 158<sup>2</sup> kohaselt kohaldatakse."
        )
        assert len(cits) == 1
        assert cits[0]["paragraphs"] == ["158_2"]

    def test_plain_number_unaffected_by_superscript_handling(self):
        from extract_cross_references import extract_citations_from_text

        cits = extract_citations_from_text("AÕS § 158 kohaselt kohaldatakse.")
        assert len(cits) == 1
        assert cits[0]["paragraphs"] == ["158"]

    def test_range_still_digit_only(self):
        from extract_cross_references import extract_citations_from_text

        cits = extract_citations_from_text("VÕS §-de 208-210 alusel.")
        assert len(cits) == 1
        assert cits[0]["paragraphs"] == ["208", "209", "210"]

    def test_self_reference_superscript_captured(self):
        from extract_cross_references import extract_citations_from_text

        cits = extract_citations_from_text("Käesoleva seaduse § 22¹ kohaselt.")
        assert len(cits) == 1
        assert cits[0]["is_self_ref"] is True
        assert cits[0]["paragraphs"] == ["22_1"]

    # -- _expand_par_range / _normalize_par_number --------------------

    def test_expand_par_range_normalizes_unicode_superscript(self):
        from extract_cross_references import _expand_par_range

        assert _expand_par_range("158²") == ["158_2"]

    def test_expand_par_range_normalizes_sup_markup(self):
        from extract_cross_references import _expand_par_range

        assert _expand_par_range("22<sup>1</sup>") == ["22_1"]

    def test_expand_par_range_plain_and_range_unchanged(self):
        from extract_cross_references import _expand_par_range

        assert _expand_par_range("158") == ["158"]
        assert _expand_par_range("208-210") == ["208", "209", "210"]

    def test_normalize_par_number_forms(self):
        from extract_cross_references import _normalize_par_number

        assert _normalize_par_number("158²") == "158_2"
        assert _normalize_par_number("158<sup>2</sup>") == "158_2"
        assert _normalize_par_number("158") == "158"
        # Multi-glyph superscript (rare but well-formed).
        assert _normalize_par_number("3¹²") == "3_12"
        assert _normalize_par_number("") == ""

    # -- resolution path: superscript citation -> _Par_158_2 ----------

    def test_resolve_citation_superscript_hits_correct_iri(self):
        from extract_cross_references import resolve_citation

        prefix_to_provisions = {
            "AOS_Osa3": {
                "158": "estleg:AOS_Osa3_Par_158",
                "158_2": "estleg:AOS_Osa3_Par_158_2",
            }
        }
        abbrev = {"AÕS": "AOS_Osa3"}
        sup = {"law_ref": "AÕS", "paragraphs": ["158_2"], "is_self_ref": False}
        plain = {"law_ref": "AÕS", "paragraphs": ["158"], "is_self_ref": False}
        assert resolve_citation(sup, "", abbrev, prefix_to_provisions) == [
            "estleg:AOS_Osa3_Par_158_2"
        ]
        # The plain citation must NOT bleed into the superscript provision.
        assert resolve_citation(plain, "", abbrev, prefix_to_provisions) == [
            "estleg:AOS_Osa3_Par_158"
        ]

    def test_end_to_end_unicode_superscript_resolves_to_158_2(self):
        """Full parse + resolve: ``§ 158²`` in real body text lands on
        ``_Par_158_2``, the superscript IRI that exists on disk."""
        from extract_cross_references import (
            extract_citations_from_text,
            resolve_citation,
        )

        prefix_to_provisions = {
            "AOS_Osa3": {
                "158": "estleg:AOS_Osa3_Par_158",
                "158_2": "estleg:AOS_Osa3_Par_158_2",
            }
        }
        abbrev = {"AÕS": "AOS_Osa3"}
        for text in ("AÕS § 158² kohaselt", "AÕS § 158<sup>2</sup> kohaselt"):
            cits = extract_citations_from_text(text)
            resolved = [
                iri
                for c in cits
                for iri in resolve_citation(c, "", abbrev, prefix_to_provisions)
            ]
            assert resolved == ["estleg:AOS_Osa3_Par_158_2"], text


class TestSuperscriptXmlKeyBuilder:
    """Regression for #254: ``collect_text_from_xml`` keyed paragraphs with
    ``re.sub(r"[^\\d]", "", par_nr)`` so ``§ 158`` and ``§ 158²`` collided.
    The key must now mirror ``_paragraph_id_suffix`` — ``158`` vs ``158_2`` —
    so plain and superscript provisions keep their own body text."""

    def _write_xml(self, tmp_path, body: str):
        p = tmp_path / "law.xml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_xml_key_builder_keeps_158_and_158_2_distinct_via_ylaindeks(
        self, tmp_path
    ):
        from extract_cross_references import collect_text_from_xml

        xml = (
            '<akt xmlns="x">'
            "<paragrahv><paragrahvNr>158</paragrahvNr>"
            "<sisu>plain body</sisu></paragrahv>"
            '<paragrahv><paragrahvNr ylaIndeks="2">158</paragrahvNr>'
            "<sisu>superscript body</sisu></paragrahv>"
            "</akt>"
        )
        keys = collect_text_from_xml(self._write_xml(tmp_path, xml))
        assert set(keys) == {"158", "158_2"}
        assert "plain body" in keys["158"]
        assert "superscript body" in keys["158_2"]
        # No collision: the two bodies are different.
        assert keys["158"] != keys["158_2"]

    def test_xml_key_builder_recovers_superscript_from_kuvatavnr_glyph(
        self, tmp_path
    ):
        from extract_cross_references import collect_text_from_xml

        xml = (
            '<akt xmlns="x">'
            "<paragrahv><paragrahvNr>158</paragrahvNr>"
            "<sisu>plain body</sisu></paragrahv>"
            "<paragrahv><paragrahvNr>158</paragrahvNr>"
            "<kuvatavNr>158²</kuvatavNr>"
            "<sisu>superscript body</sisu></paragrahv>"
            "</akt>"
        )
        keys = collect_text_from_xml(self._write_xml(tmp_path, xml))
        assert set(keys) == {"158", "158_2"}
        assert "superscript body" in keys["158_2"]

    def test_xml_key_builder_plain_number_unchanged(self, tmp_path):
        from extract_cross_references import collect_text_from_xml

        xml = (
            '<akt xmlns="x">'
            "<paragrahv><paragrahvNr>22</paragrahvNr>"
            "<sisu>plain body</sisu></paragrahv>"
            "</akt>"
        )
        keys = collect_text_from_xml(self._write_xml(tmp_path, xml))
        assert set(keys) == {"22"}

    def test_xml_key_matches_load_provision_text_lookup(self, tmp_path):
        """The key collect_text_from_xml emits must be the same shape
        _load_provision_text derives from the ``_Par_<suffix>`` IRI, so the
        superscript provision reads its OWN body."""
        from extract_cross_references import (
            collect_text_from_xml,
            _load_provision_text,
        )

        xml = (
            '<akt xmlns="x">'
            "<paragrahv><paragrahvNr>158</paragrahvNr>"
            "<sisu>plain body 158</sisu></paragrahv>"
            '<paragrahv><paragrahvNr ylaIndeks="2">158</paragrahvNr>'
            "<sisu>superscript body 158 2</sisu></paragrahv>"
            "</akt>"
        )
        xml_par_texts = collect_text_from_xml(self._write_xml(tmp_path, xml))

        plain_node = {"@id": "estleg:AOS_Osa3_Par_158"}
        sup_node = {"@id": "estleg:AOS_Osa3_Par_158_2"}
        assert "plain body 158" in _load_provision_text(plain_node, xml_par_texts)
        assert "superscript body 158 2" in _load_provision_text(
            sup_node, xml_par_texts
        )

    def test_inlaw_pass_superscript_provision_reads_own_body(self, tmp_path):
        """End-to-end: with distinct XML keys, the superscript provision
        (_Par_158_2) scans its OWN body and the plain provision (_Par_158)
        scans its own — no last-write-wins collision (#254)."""
        from extract_cross_references import (
            collect_text_from_xml,
            _run_inlaw_citation_pass,
        )

        # The superscript provision body self-cites § 158 (the plain one);
        # the plain provision body has no citation. Pre-fix, both keyed to
        # "158" so the plain provision would have wrongly scanned the
        # superscript body.
        xml = (
            '<akt xmlns="x">'
            "<paragrahv><paragrahvNr>158</paragrahvNr>"
            "<sisu>Tavaline paragrahv ilma viideteta.</sisu></paragrahv>"
            '<paragrahv><paragrahvNr ylaIndeks="2">158</paragrahvNr>'
            "<sisu>Käesoleva seaduse § 158 kohaldatakse.</sisu></paragrahv>"
            "</akt>"
        )
        xml_par_texts = collect_text_from_xml(self._write_xml(tmp_path, xml))
        graph = [
            {"@id": "estleg:AOS_Osa3_Par_158",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"]},
            {"@id": "estleg:AOS_Osa3_Par_158_2",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"]},
        ]
        prefix_to_provisions = {
            "AOS_Osa3": {
                "158": "estleg:AOS_Osa3_Par_158",
                "158_2": "estleg:AOS_Osa3_Par_158_2",
            }
        }
        _run_inlaw_citation_pass(
            graph,
            self_prefix="AOS_Osa3",
            abbrev_to_prefix={},
            prefix_to_provisions=prefix_to_provisions,
            xml_par_texts=xml_par_texts,
        )
        plain = next(n for n in graph if n["@id"] == "estleg:AOS_Osa3_Par_158")
        sup = next(n for n in graph if n["@id"] == "estleg:AOS_Osa3_Par_158_2")
        # Superscript provision emits the self-cite to _Par_158.
        assert [r["@id"] for r in sup.get("estleg:references", [])] == [
            "estleg:AOS_Osa3_Par_158"
        ]
        # Plain provision scanned its OWN (citation-free) body: no refs.
        assert "estleg:references" not in plain


class TestMultiOsaPrefixAccumulation:
    """Regression for #299: a multi-osa law shares one ``estleg:sourceAct``
    across many osa peep files, each with its OWN provision prefix.
    ``source_act_to_prefix`` must accumulate ALL of them (a list), and
    ``resolve_citation`` must search every prefix — otherwise the
    alphabetically-last osa was the only reachable one and §§ in every
    other osa silently dropped (~10,900 provisions)."""

    def _stage_osa(self, krr, fname, act_iri, prov_iri, source_act):
        (krr / fname).write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": act_iri, "@type": ["owl:Ontology"],
                 "estleg:sourceAct": source_act},
                {"@id": prov_iri,
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:sourceAct": source_act},
            ],
        }), encoding="utf-8")

    def test_source_act_to_prefix_accumulates_all_osa_prefixes(
        self, tmp_path, monkeypatch
    ):
        import estleg_common
        import extract_cross_references as ecr

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        # Two osa files, ONE sourceAct, distinct provision prefixes.
        self._stage_osa(krr, "aos_osa1_peep.json", "estleg:AOS_Osa1_1_87",
                        "estleg:AOS_Osa1_Par_1", "Asjaõigusseadus")
        self._stage_osa(krr, "aos_osa5_peep.json", "estleg:AOS_Osa5_X",
                        "estleg:AOS_Osa5_Par_200", "Asjaõigusseadus")
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(ecr, "KRR_DIR", krr)

        (prefix_to_provisions, source_act_to_prefix, _i2f,
         _p2a, _a2p) = ecr.build_provision_index()

        # Both osa prefixes accumulated under the single sourceAct.
        assert sorted(source_act_to_prefix["Asjaõigusseadus"]) == [
            "AOS_Osa1", "AOS_Osa5"
        ]
        # Each osa's provisions keyed under its OWN prefix.
        assert prefix_to_provisions["AOS_Osa1"]["1"] == "estleg:AOS_Osa1_Par_1"
        assert prefix_to_provisions["AOS_Osa5"]["200"] == "estleg:AOS_Osa5_Par_200"

    def test_resolve_citation_searches_every_osa_prefix(self):
        """A §§ in osa1 and a §§ in osa5 of the SAME law both resolve
        when the abbrev maps to the list of osa prefixes."""
        from extract_cross_references import resolve_citation

        prefix_to_provisions = {
            "AOS_Osa1": {"1": "estleg:AOS_Osa1_Par_1"},
            "AOS_Osa5": {"200": "estleg:AOS_Osa5_Par_200"},
        }
        abbrev_to_prefix = {"AÕS": ["AOS_Osa1", "AOS_Osa5"]}

        # §1 lives in osa1, §200 in osa5 — first matching prefix wins.
        assert resolve_citation(
            {"law_ref": "AÕS", "paragraphs": ["1"], "is_self_ref": False},
            "", abbrev_to_prefix, prefix_to_provisions,
        ) == ["estleg:AOS_Osa1_Par_1"]
        assert resolve_citation(
            {"law_ref": "AÕS", "paragraphs": ["200"], "is_self_ref": False},
            "", abbrev_to_prefix, prefix_to_provisions,
        ) == ["estleg:AOS_Osa5_Par_200"]
        # Both at once, in citation order.
        assert resolve_citation(
            {"law_ref": "AÕS", "paragraphs": ["1", "200"],
             "is_self_ref": False},
            "", abbrev_to_prefix, prefix_to_provisions,
        ) == ["estleg:AOS_Osa1_Par_1", "estleg:AOS_Osa5_Par_200"]

    def test_resolve_citation_tolerates_bare_string_prefix(self):
        """Older callers / fixtures pass a bare string prefix value;
        ``resolve_citation`` must still resolve it (back-compat)."""
        from extract_cross_references import resolve_citation

        prefix_to_provisions = {"AOS_Osa1": {"1": "estleg:AOS_Osa1_Par_1"}}
        assert resolve_citation(
            {"law_ref": "AÕS", "paragraphs": ["1"], "is_self_ref": False},
            "", {"AÕS": "AOS_Osa1"}, prefix_to_provisions,
        ) == ["estleg:AOS_Osa1_Par_1"]

    def test_build_abbreviation_to_prefix_returns_list_values(self):
        from extract_cross_references import build_abbreviation_to_prefix

        # AÕS → Asjaõigusseadus is a real KNOWN_ABBREVIATIONS entry.
        source_act_to_prefix = {
            "Asjaõigusseadus": ["AOS_Osa1", "AOS_Osa5"],
        }
        out = build_abbreviation_to_prefix(source_act_to_prefix)
        assert out["AÕS"] == ["AOS_Osa1", "AOS_Osa5"]
        # The returned list must be a copy, not the shared bucket.
        out["AÕS"].append("MUT")
        assert source_act_to_prefix["Asjaõigusseadus"] == ["AOS_Osa1", "AOS_Osa5"]


class TestPerProvisionPrefixKeying:
    """Regression for #312: a file mixing several provision-prefix
    families (e.g. erigi_seadus_peep.json: ERIIGI_DI / ERIIGI_ES /
    ERIIGI_DE) must key every provision under its OWN ``@id`` prefix,
    not the file-level FIRST prefix — otherwise par 20/30 of the
    non-first family resolve to a wrong-law IRI."""

    def test_mixed_prefix_file_keys_each_provision_under_own_prefix(
        self, tmp_path, monkeypatch
    ):
        import estleg_common
        import extract_cross_references as ecr

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        (krr / "erigi_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:ERIIGI_DI_Map_2026",
                 "@type": ["owl:Ontology"],
                 "estleg:sourceAct": "Erigi seadus"},
                {"@id": "estleg:ERIIGI_DI_Par_20",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:sourceAct": "Erigi seadus"},
                # Second prefix family in the SAME file, same par number.
                {"@id": "estleg:ERIIGI_ES_Par_20",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:sourceAct": "Erigi seadus"},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(ecr, "KRR_DIR", krr)

        (prefix_to_provisions, _satp, _i2f, _p2a, _a2p) = (
            ecr.build_provision_index()
        )
        # Each par-20 indexed under its OWN prefix, pointing at its OWN IRI.
        assert prefix_to_provisions["ERIIGI_DI"]["20"] == \
            "estleg:ERIIGI_DI_Par_20"
        assert prefix_to_provisions["ERIIGI_ES"]["20"] == \
            "estleg:ERIIGI_ES_Par_20"
        # The wrong-prefix routing bug would have stored the ES par-20
        # under the DI prefix — assert that did NOT happen.
        assert prefix_to_provisions["ERIIGI_DI"]["20"] != \
            "estleg:ERIIGI_ES_Par_20"


class TestSubsectionLegalTextFallback:
    """Regression for #362: ``_load_provision_text`` never read
    ``estleg:legalText``, so 15,258 lõige (``_Lg_``) nodes whose
    subsection text carries a ``§`` citation yielded '' and were skipped.
    legalText is now a fallback AFTER xml+summary so paragraph-level
    nodes (which carry both summary and legalText) don't double-count."""

    def test_lg_node_uses_legaltext_when_summary_empty(self):
        from extract_cross_references import _load_provision_text

        # A subsection node: @id suffix '5_Lg_1' is never an XML key,
        # summary is absent — citable body lives in legalText.
        node = {
            "@id": "estleg:ETRJS_Par_5_Lg_1",
            "estleg:legalText":
                "(1) Kõik füüsilised ja juriidilised isikud "
                "käesoleva seaduse § 1 lõikes 2 nimetatud korras.",
        }
        text = _load_provision_text(node, {})
        assert "käesoleva seaduse § 1" in text

    def test_lg_node_value_object_legaltext(self):
        """legalText may be a language-tagged value object; jsonld_text
        must unwrap it for the fallback."""
        from extract_cross_references import _load_provision_text

        node = {
            "@id": "estleg:X_Par_3_Lg_3",
            "estleg:legalText": {
                "@value": "KarS § 121 alusel kohaldatakse.",
                "@language": "et",
            },
        }
        assert "KarS § 121" in _load_provision_text(node, {})

    def test_paragraph_node_does_not_double_count_legaltext(self):
        """A paragraph node with BOTH summary and legalText must scan
        only summary (xml/summary win) so the same citation isn't
        counted twice. legalText is a strict fallback."""
        from extract_cross_references import _load_provision_text

        node = {
            "@id": "estleg:X_Par_5",
            "estleg:summary": "summary body",
            "estleg:legalText": "legalText body",
        }
        # Summary present → legalText is NOT appended.
        assert _load_provision_text(node, {}) == "summary body"

    def test_xml_text_still_preferred_over_legaltext(self):
        from extract_cross_references import _load_provision_text

        node = {
            "@id": "estleg:X_Par_5",
            "estleg:legalText": "legalText body",
        }
        # XML text present for key '5' → that wins, legalText ignored.
        assert _load_provision_text(node, {"5": "xml body"}) == "xml body"

    def test_lg_node_end_to_end_gets_reference(self):
        """Full in-law pass: a _Lg_ node with a §-citation in legalText
        now resolves to an estleg:references edge (#362)."""
        from extract_cross_references import _run_inlaw_citation_pass

        graph = [
            {"@id": "estleg:ETRJS_Par_5",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             "estleg:summary": "Paragrahvi tekst."},
            {"@id": "estleg:ETRJS_Par_5_Lg_1",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             "estleg:legalText":
                 "(1) Käesoleva seaduse § 1 lõikes 2 nimetatud korras."},
        ]
        stats = _run_inlaw_citation_pass(
            graph,
            self_prefix="ETRJS",
            abbrev_to_prefix={},
            prefix_to_provisions={
                "ETRJS": {"1": "estleg:ETRJS_Par_1",
                          "5": "estleg:ETRJS_Par_5"}},
            xml_par_texts={},
        )
        assert stats["modified"] is True
        lg = next(n for n in graph if n["@id"] == "estleg:ETRJS_Par_5_Lg_1")
        assert {"@id": "estleg:ETRJS_Par_1"} in lg["estleg:references"]


class TestKoodeksSelfReference:
    """Regression for #363(b): the self-ref regex
    ``käesoleva seadus(?:e|tiku)?`` didn't match ``käesoleva koodeksi
    §``, dropping 71 intra-code self-references in the maritime /
    criminal-procedure codes. The pattern now also matches ``koodeks``
    / ``koodeksi``."""

    def test_koodeksi_genitive_self_ref_matched(self):
        from extract_cross_references import extract_citations_from_text

        cits = extract_citations_from_text(
            "Käesoleva koodeksi §§ 112, 113, 114 kohaldatakse."
        )
        self_refs = [c for c in cits if c["is_self_ref"]]
        assert self_refs, "koodeksi self-ref must be captured"
        assert self_refs[0]["paragraphs"] == ["112"]

    def test_koodeks_nominative_self_ref_matched(self):
        from extract_cross_references import extract_citations_from_text

        cits = extract_citations_from_text("Käesoleva koodeks § 5 alusel.")
        assert any(c["is_self_ref"] for c in cits)

    def test_seaduse_and_seadustiku_self_ref_still_matched(self):
        """The pre-existing seaduse / seadustiku forms must keep working."""
        from extract_cross_references import extract_citations_from_text

        for txt in ("Käesoleva seaduse § 5 alusel.",
                    "Käesoleva seadustiku § 5 alusel."):
            cits = extract_citations_from_text(txt)
            assert any(c["is_self_ref"] for c in cits), txt


class TestExpandParRangeOverLimit:
    """Regression for #341: ``_expand_par_range`` fell through to
    ``_normalize_par_number`` for reversed / over-wide numeric ranges,
    which strips the hyphen and CONCATENATES digits ('1-52' → '152'),
    fabricating a provision key that can collide with a real §. Such
    ranges must return [] instead."""

    def test_over_wide_range_returns_empty(self):
        from extract_cross_references import _expand_par_range

        # span 55 > 50 sanity limit — would have become '459'.
        assert _expand_par_range("4-59") == []

    def test_exactly_over_limit_range_returns_empty(self):
        from extract_cross_references import _expand_par_range

        # span 100 — would have become '100200'.
        assert _expand_par_range("100-200") == []

    def test_reversed_range_returns_empty(self):
        from extract_cross_references import _expand_par_range

        # end < start — would have become '200100'.
        assert _expand_par_range("200-100") == []

    def test_in_limit_range_still_expands(self):
        from extract_cross_references import _expand_par_range

        assert _expand_par_range("208-210") == ["208", "209", "210"]
        # Boundary: span exactly 50 stays valid.
        assert _expand_par_range("1-51") == [str(n) for n in range(1, 52)]

    def test_over_wide_range_does_not_concatenate(self):
        """The concrete bug: '1-52' must NOT resolve to the bare key
        '152' (which could hit a real AUDIIT_Par_152)."""
        from extract_cross_references import _expand_par_range

        assert _expand_par_range("1-52") == []
        assert "152" not in _expand_par_range("1-52")


class TestHoistedCitationPatterns:
    """#386: the abbrev/genitive/self alternations are compiled ONCE at
    module import (``_PAT_ABBREV`` / ``_PAT_SELF`` / ``_PAT_FULLNAME``)
    instead of being rebuilt on every ``extract_citations_from_text``
    call. These guard the constants exist, are pre-compiled, and stay
    behaviourally equivalent to the old per-call construction."""

    def test_module_level_patterns_are_compiled(self):
        import re

        import extract_cross_references as ecr

        assert isinstance(ecr._PAT_ABBREV, re.Pattern)
        assert isinstance(ecr._PAT_SELF, re.Pattern)
        assert isinstance(ecr._PAT_FULLNAME, re.Pattern)

    def test_abbrev_pattern_built_from_known_abbreviations(self):
        import extract_cross_references as ecr
        from estleg_common import KNOWN_ABBREVIATIONS

        # Every known abbreviation is reachable through the hoisted
        # alternation (spot-check a few representative ones).
        for abbrev in ("KarS", "VÕS", "KOKS"):
            assert abbrev in KNOWN_ABBREVIATIONS
            m = ecr._PAT_ABBREV.search(f"{abbrev} § 5")
            assert m is not None and m.group(1) == abbrev

    def test_hoisted_patterns_match_old_behaviour(self):
        """The hoisted patterns produce the same citations the old
        per-call build did for representative inputs."""
        from extract_cross_references import extract_citations_from_text

        cits = extract_citations_from_text(
            "KarS § 121 ja käesoleva seaduse § 5 ning "
            "karistusseadustiku § 200 kohaldatakse."
        )
        law_refs = {c["law_ref"] for c in cits}
        assert "KarS" in law_refs
        assert "__SELF__" in law_refs
        # 'karistusseadustiku' resolves through FULLNAME_GENITIVE → KarS.
        assert any(c["law_ref"] == "KarS" and c["paragraphs"] == ["200"]
                   for c in cits)


class TestCorpusTitleFallback:
    """#390(a): ``resolve_preamble_citation`` consults a corpus-title
    fallback (``law_title_to_iri``) when a genitive law_ref is absent
    from the 40-entry ``genitive_to_act_iri`` chain. The genitive is
    folded to its nominative title form and looked up against the
    ~236-entry corpus-title map."""

    def test_build_law_title_to_iri_lowercases_sourceact(self):
        from extract_cross_references import build_law_title_to_iri

        source_act_to_prefix = {"Kaitseliidu seadus": ["KAITSE"]}
        prefix_to_act_iri = {"KAITSE": "estleg:KAITSE_Map_2026"}
        out = build_law_title_to_iri(source_act_to_prefix, prefix_to_act_iri)
        assert out == {"kaitseliidu seadus": "estleg:KAITSE_Map_2026"}

    def test_build_law_title_to_iri_handles_multi_osa_list(self):
        from extract_cross_references import build_law_title_to_iri

        # First prefix with a known act_iri wins.
        source_act_to_prefix = {"Asjaõigusseadus": ["AOS_Osa1", "AOS_Osa5"]}
        prefix_to_act_iri = {"AOS_Osa5": "estleg:AOS_Osa5_X"}
        out = build_law_title_to_iri(source_act_to_prefix, prefix_to_act_iri)
        assert out["asjaõigusseadus"] == "estleg:AOS_Osa5_X"

    def test_genitive_law_ref_to_title_folds_markers(self):
        from extract_cross_references import _genitive_law_ref_to_title

        assert _genitive_law_ref_to_title("kaitseliidu seaduse") == \
            "kaitseliidu seadus"
        assert _genitive_law_ref_to_title("karistusseadustiku") == \
            "karistusseadustik"
        assert _genitive_law_ref_to_title("kohtumenetluse koodeksi") == \
            "kohtumenetluse koodeks"
        # Not a law marker → None (skip the fallback).
        assert _genitive_law_ref_to_title("midagi muud") is None

    def test_resolve_preamble_uses_title_fallback_when_genitive_missing(self):
        """The motivating case: 'kaitseliidu seaduse' isn't in
        genitive_to_act_iri but resolves via the corpus-title map."""
        from extract_cross_references import resolve_preamble_citation

        cit = {
            "form": "law-genitive",
            "law_ref": "Kaitseliidu seaduse",
            "paragraphs": [],
            "citationDetail": None,
            "citationText": "Kaitseliidu seaduse § 69",
        }
        result = resolve_preamble_citation(
            cit,
            genitive_to_act_iri={},          # genitive NOT known
            prefix_to_provisions={},
            act_iri_to_prefix={},
            state_reg_lookup={},
            kov_act_lookup={},
            law_title_to_iri={"kaitseliidu seadus": "estleg:KAITSE_Map_2026"},
        )
        assert result == ("estleg:KAITSE_Map_2026", "estleg:KAITSE_Map_2026")

    def test_genitive_chain_still_preferred_over_title_fallback(self):
        """When the genitive IS in the primary chain, that wins — the
        fallback is only consulted on a miss."""
        from extract_cross_references import resolve_preamble_citation

        cit = {
            "form": "law-genitive",
            "law_ref": "karistusseadustiku",
            "paragraphs": [],
            "citationDetail": None,
            "citationText": "karistusseadustiku",
        }
        result = resolve_preamble_citation(
            cit,
            genitive_to_act_iri={"karistusseadustiku": "estleg:PRIMARY"},
            prefix_to_provisions={},
            act_iri_to_prefix={},
            state_reg_lookup={},
            kov_act_lookup={},
            law_title_to_iri={"karistusseadustik": "estleg:FALLBACK"},
        )
        assert result == ("estleg:PRIMARY", "estleg:PRIMARY")

    def test_no_fallback_when_title_map_absent(self):
        """Without a title map (the default), an unknown genitive still
        returns None — back-compat with existing callers."""
        from extract_cross_references import resolve_preamble_citation

        cit = {
            "form": "law-genitive",
            "law_ref": "tundmatu seaduse",
            "paragraphs": [],
            "citationDetail": None,
            "citationText": "tundmatu seaduse",
        }
        assert resolve_preamble_citation(
            cit,
            genitive_to_act_iri={},
            prefix_to_provisions={},
            act_iri_to_prefix={},
            state_reg_lookup={},
            kov_act_lookup={},
        ) is None


class TestPreambleConnectorTrim:
    """#390(b): ``_trim_preamble_prefix`` strips a leading connector
    ``ning`` / ``ja`` ONLY when the remainder still ends in seaduse /
    seadustiku — so a chained second enabling law ('ning avaliku
    teenistuse seaduse') parses cleanly without breaking multiword law
    names where ``ja`` is internal."""

    def test_leading_ning_stripped(self):
        from extract_cross_references import _trim_preamble_prefix

        assert _trim_preamble_prefix("ning avaliku teenistuse seaduse") == \
            "avaliku teenistuse seaduse"

    def test_leading_ja_stripped(self):
        from extract_cross_references import _trim_preamble_prefix

        assert _trim_preamble_prefix("ja avaliku teabe seaduse") == \
            "avaliku teabe seaduse"

    def test_internal_ja_in_multiword_name_preserved(self):
        from extract_cross_references import _trim_preamble_prefix

        # 'ja' is INSIDE the law name — leading token is 'põhikooli-'.
        assert _trim_preamble_prefix("põhikooli- ja gümnaasiumiseaduse") == \
            "põhikooli- ja gümnaasiumiseaduse"
        assert _trim_preamble_prefix(
            "ühisveevärgi ja -kanalisatsiooni seaduse"
        ) == "ühisveevärgi ja -kanalisatsiooni seaduse"

    def test_leading_connector_not_stripped_without_law_marker(self):
        """A leading 'ning' whose remainder does NOT end in a law marker
        is left untouched (don't over-trim)."""
        from extract_cross_references import _trim_preamble_prefix

        assert _trim_preamble_prefix("ning midagi muud") == "ning midagi muud"

    def test_license_verb_then_connector_then_law(self):
        """The connector strip runs AFTER the license-verb strip, so
        'Määrus kehtestatakse ja <law>' yields the bare law name."""
        from extract_cross_references import _trim_preamble_prefix

        assert _trim_preamble_prefix(
            "Määrus kehtestatakse ja avaliku teabe seaduse"
        ) == "avaliku teabe seaduse"


class TestIssuedUnderSelfReferenceGuard:
    """#390(d): ``_process_preamble_for_act`` must not emit an act-level
    ``issuedUnder`` edge pointing at the act ITSELF (a few pre-1994 regs
    name their own title). The provision-level reified Citation node is
    still allowed — only the self ``issuedUnder`` is suppressed."""

    def test_self_issued_under_suppressed(self, tmp_path):
        from extract_cross_references import _process_preamble_for_act

        peep = tmp_path / "self_peep.json"
        doc = {
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:SELF_Map_2026",
                 "@type": ["owl:Ontology"],
                 "estleg:preambleText":
                     "Määrus kehtestatakse iseenda seaduse § 5 alusel."},
                {"@id": "estleg:SELF_Par_5",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"]},
            ],
        }
        peep.write_text(json.dumps(doc), encoding="utf-8")

        result = _process_preamble_for_act(
            peep, doc,
            genitive_to_act_iri={},
            prefix_to_provisions={"SELF": {"5": "estleg:SELF_Par_5"}},
            act_iri_to_prefix={"estleg:SELF_Map_2026": "SELF"},
            state_reg_lookup={},
            kov_act_lookup={},
            # Title fallback resolves the genitive to the SAME act.
            law_title_to_iri={"iseenda seadus": "estleg:SELF_Map_2026"},
        )
        act = next(n for n in doc["@graph"]
                   if n["@id"] == "estleg:SELF_Map_2026")
        # Self issuedUnder suppressed …
        assert "estleg:issuedUnder" not in act
        # … but the provision-level self-citation node IS emitted.
        assert act.get("estleg:implementsCitation")
        assert result["citations_resolved"] == 1

    def test_cross_act_issued_under_still_emitted(self, tmp_path):
        """Sanity: a DIFFERENT enabling act still produces issuedUnder."""
        from extract_cross_references import _process_preamble_for_act

        peep = tmp_path / "cross_peep.json"
        doc = {
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:REG_Map_2026",
                 "@type": ["owl:Ontology"],
                 # A § is required for the parser to emit a law-genitive
                 # citation; the enabling act differs from this reg.
                 "estleg:preambleText":
                     "Määrus kehtestatakse kaitseliidu seaduse § 69 alusel."},
            ],
        }
        peep.write_text(json.dumps(doc), encoding="utf-8")

        _process_preamble_for_act(
            peep, doc,
            genitive_to_act_iri={},
            prefix_to_provisions={},
            act_iri_to_prefix={},
            state_reg_lookup={},
            kov_act_lookup={},
            law_title_to_iri={"kaitseliidu seadus": "estleg:KAITSE_Map_2026"},
        )
        act = next(n for n in doc["@graph"]
                   if n["@id"] == "estleg:REG_Map_2026")
        assert act["estleg:issuedUnder"] == [{"@id": "estleg:KAITSE_Map_2026"}]


class TestAbbreviationMappingReport:
    """Regression guard for the #299 list-valued abbrev→prefix contract.

    A multi-osa law (e.g. Karistusseadustik split across osa peeps) maps one
    abbreviation to SEVERAL provision prefixes, so ``build_abbreviation_to_prefix``
    values are ``list[str]`` since #299. The diagnostic print loop and the
    cross_references_report.json builder in ``main()`` both consumed that value
    and, before this fix, used it directly as a ``prefix_to_provisions`` key —
    ``TypeError: unhashable type: 'list'`` — which crashed the whole extractor
    (and therefore step 1 of run_all_integration). Both call sites now share
    ``build_abbreviation_mapping_report``; this locks the contract.
    """

    def test_build_abbreviation_to_prefix_is_list_valued(self):
        from extract_cross_references import build_abbreviation_to_prefix

        # Unsorted on purpose to prove the report sorts, not the builder.
        abbrev_to_prefix = build_abbreviation_to_prefix(
            {"Karistusseadustik": ["KARIST_3", "KARIST_2"]}
        )
        assert abbrev_to_prefix["KarS"] == ["KARIST_3", "KARIST_2"]
        # The raw value is a list — using it as a dict key is exactly the
        # bug this module regressed on; assert that misuse still raises so
        # the helper's reason-to-exist is documented in the test.
        with pytest.raises(TypeError):
            {"KARIST_2": {}}.get(abbrev_to_prefix["KarS"])  # type: ignore[arg-type]

    def test_report_sums_provisions_across_all_prefixes(self):
        from extract_cross_references import (
            build_abbreviation_to_prefix,
            build_abbreviation_mapping_report,
        )

        abbrev_to_prefix = build_abbreviation_to_prefix(
            {"Karistusseadustik": ["KARIST_3", "KARIST_2"]}
        )
        prefix_to_provisions = {
            "KARIST_2": {"1": "estleg:KARIST_2_Par_1", "2": "estleg:KARIST_2_Par_2"},
            "KARIST_3": {"5": "estleg:KARIST_3_Par_5"},
        }
        report = build_abbreviation_mapping_report(
            abbrev_to_prefix, prefix_to_provisions
        )
        # Sorted list of every prefix, not the unsorted builder order.
        assert report["KarS"]["iri_prefixes"] == ["KARIST_2", "KARIST_3"]
        # Summed across BOTH prefixes (2 + 1), not just the first.
        assert report["KarS"]["provision_count"] == 3
        # The whole report must be JSON-serialisable (it is saved to disk).
        json.dumps(report)

    def test_report_tolerates_bare_string_value_pre_299(self):
        """A pre-#299 single-prefix string value must still work (back-compat
        via ``_iter_prefixes``), not be iterated char-by-char."""
        from extract_cross_references import build_abbreviation_mapping_report

        report = build_abbreviation_mapping_report(
            {"KarS": "KARIST_2"},
            {"KARIST_2": {"1": "estleg:KARIST_2_Par_1"}},
        )
        assert report["KarS"]["iri_prefixes"] == ["KARIST_2"]
        assert report["KarS"]["provision_count"] == 1
