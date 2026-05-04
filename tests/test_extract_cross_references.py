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
