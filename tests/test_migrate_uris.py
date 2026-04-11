"""Tests for migrate_uris.py — Tasks 1-4."""
import pytest
from migrate_uris import auto_derive_abbreviation, PAR_NO_UNDERSCORE_RE


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
