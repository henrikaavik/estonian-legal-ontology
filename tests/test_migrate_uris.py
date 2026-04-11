"""Tests for migrate_uris.py — Tasks 1-4."""
import pytest
from migrate_uris import auto_derive_abbreviation, build_rename_map, PAR_NO_UNDERSCORE_RE


class TestAutoDerive:
    def test_multiword_with_stop_words(self):
        result = auto_derive_abbreviation(
            "Alkoholi-, tubaka-, kütuse- ja elektriaktsiisi seadus",
            "alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus",
        )
        assert result == "ATKE"

    def test_treaty_appends_year(self):
        result = auto_derive_abbreviation(
            "1974. aasta rahvusvahelise konventsiooni inimelude ohutusest merel protokoll",
            "1974_aasta_rahvusvahelise_konventsiooni_inimelude_ohutusest_merel_protokoll",
        )
        assert result.endswith("1974")
        assert len(result) <= 12

    def test_short_result_fallback_to_slug(self):
        result = auto_derive_abbreviation("Seadus", "seadus")
        assert len(result) >= 3
        assert len(result) <= 12

    def test_cap_at_12_chars(self):
        result = auto_derive_abbreviation(
            "Väga pikk nimetus mille sisu on keeruline ja raske kirjeldada",
            "vaga_pikk_nimetus_mille_sisu_on_keeruline_raske_kirjeldada",
        )
        assert len(result) <= 12

    def test_transliteration(self):
        result = auto_derive_abbreviation(
            "Öökülma tõrje ühingu põhikiri",
            "ookulma_torje_uhingu_pohikiri",
        )
        assert result == "OTUP"

    def test_single_word_non_stop(self):
        result = auto_derive_abbreviation("Arhiiviseadus", "arhiiviseadus")
        assert len(result) >= 3


class TestUnderscoreFix:
    def test_par_without_underscore(self):
        result = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", "VOS_Par271")
        assert result == "VOS_Par_271"

    def test_par_with_underscore_unchanged(self):
        result = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", "VOS_Par_271")
        assert result == "VOS_Par_271"

    def test_multiple_occurrences(self):
        result = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", "VOS_Par1 VOS_Par2")
        assert "Par_1" in result
        assert "Par_2" in result


class TestBuildRenameMap:
    @pytest.fixture
    def sample_registry(self):
        return {
            "perekonnaseadus": {
                "abbrev": "PKS",
                "source": "rt_api",
                "title": "Perekonnaseadus",
                "old_prefix": "PKS",
            },
            "alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus": {
                "abbrev": "ATKE",
                "source": "auto",
                "title": "Alkoholi-, tubaka-, kütuse- ja elektriaktsiisi seadus",
                "old_prefix": "Alkoholi_tubaka_ktuse_ja",
            },
            "volaigusseadus": {
                "abbrev": "VOS",
                "source": "rt_api",
                "title": "Võlaõigusseadus",
                "old_prefix": "VOS",
            },
        }

    def test_unchanged_prefix_not_in_map(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:PKS_Par_1"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert "estleg:PKS_Par_1" not in rename_map

    def test_long_prefix_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:Alkoholi_tubaka_ktuse_ja_Map_2026"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map["estleg:Alkoholi_tubaka_ktuse_ja_Map_2026"] == "estleg:ATKE_Map_2026"

    def test_cluster_prefix_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:Cluster_Alkoholi_tubaka_ktuse_ja_Aktsiis"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map["estleg:Cluster_Alkoholi_tubaka_ktuse_ja_Aktsiis"] == "estleg:Cluster_ATKE_Aktsiis"

    def test_legal_provision_slug_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:LegalProvision_alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map[
            "estleg:LegalProvision_alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus"
        ] == "estleg:LegalProvision_ATKE"

    def test_legal_provision_osa_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:LegalProvision_volaigusseadus_osa11"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map[
            "estleg:LegalProvision_volaigusseadus_osa11"
        ] == "estleg:LegalProvision_VOS_osa11"

    def test_legal_concept_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:LegalConcept_alkoholiseadus"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map["estleg:LegalConcept_alkoholiseadus"] == "estleg:Concept_alkoholiseadus"

    def test_vos_underscore_fix(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:VOS_Par271"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map["estleg:VOS_Par271"] == "estleg:VOS_Par_271"
