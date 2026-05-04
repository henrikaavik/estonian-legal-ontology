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
