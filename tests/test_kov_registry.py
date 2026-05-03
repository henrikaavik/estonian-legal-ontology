"""Tests for scripts/kov_registry.py — issuer/municipality registry helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from kov_registry import load_municipalities, parse_issuer_slug


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"


class TestLoadMunicipalities:
    def test_returns_dict_keyed_by_ehak(self):
        muns = load_municipalities(MIN_MUNICIPALITIES)
        assert isinstance(muns, dict)
        assert "0784" in muns  # Tallinn

    def test_each_entry_has_required_fields(self):
        muns = load_municipalities(MIN_MUNICIPALITIES)
        for code, entry in muns.items():
            assert entry["ehakCode"] == code
            assert entry["name"]
            assert entry["county"]
            assert entry["type"] in {"linn", "vald"}

    def test_duplicate_ehak_raises(self, tmp_path):
        path = tmp_path / "dup.json"
        path.write_text(json.dumps([
            {"ehakCode": "0784", "name": "Tallinn",
             "county": "Harju maakond", "type": "linn"},
            {"ehakCode": "0784", "name": "Duplicate",
             "county": "Harju maakond", "type": "linn"},
        ]), encoding="utf-8")
        with pytest.raises(ValueError, match="0784"):
            load_municipalities(path)

    def test_invalid_type_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps([
            {"ehakCode": "0784", "name": "Tallinn",
             "county": "Harju maakond", "type": "city"},  # not linn|vald
        ]), encoding="utf-8")
        with pytest.raises(ValueError, match="city"):
            load_municipalities(path)

    def test_invalid_ehak_shape_raises(self, tmp_path):
        path = tmp_path / "bad_code.json"
        path.write_text(json.dumps([
            {"ehakCode": "784", "name": "Tallinn",  # 3 digits
             "county": "Harju maakond", "type": "linn"},
        ]), encoding="utf-8")
        with pytest.raises(ValueError, match="4-digit"):
            load_municipalities(path)

    def test_real_file_has_79_entries(self):
        path = REPO_ROOT / "data" / "ehak" / "municipalities.json"
        muns = load_municipalities(path)
        assert len(muns) == 79


class TestParseIssuerSlug:
    def test_linnavolikogu(self):
        parts = parse_issuer_slug("tallinna_linnavolikogu")
        assert parts == {
            "slug": "tallinna_linnavolikogu",
            "root": "tallinna",
            "body": "linnavolikogu",
            "bodyType": "volikogu",
            "municipalityType": "linn",
        }

    def test_vallavalitsus(self):
        parts = parse_issuer_slug("mulgi_vallavalitsus")
        assert parts["bodyType"] == "valitsus"
        assert parts["municipalityType"] == "vald"

    def test_linnavalitsus(self):
        parts = parse_issuer_slug("tartu_linnavalitsus")
        assert parts["bodyType"] == "valitsus"
        assert parts["municipalityType"] == "linn"

    def test_vallavolikogu(self):
        parts = parse_issuer_slug("kose_vallavolikogu")
        assert parts["bodyType"] == "volikogu"
        assert parts["municipalityType"] == "vald"

    def test_compound_root(self):
        parts = parse_issuer_slug("kohtla_jarve_linnavolikogu")
        assert parts["root"] == "kohtla_jarve"
        assert parts["body"] == "linnavolikogu"

    def test_alevivolikogu_historical(self):
        # Vändra alev existed pre-2017 reform; its volikogu issued
        # municipal regulations that are still in the corpus. Treated
        # as municipalityType=vald because alev maps to a vald-class
        # successor in current EHAK.
        parts = parse_issuer_slug("vandra_alevivolikogu")
        assert parts["root"] == "vandra"
        assert parts["body"] == "alevivolikogu"
        assert parts["bodyType"] == "volikogu"
        assert parts["municipalityType"] == "vald"

    def test_unknown_body_raises(self):
        with pytest.raises(ValueError, match="unknown body suffix"):
            parse_issuer_slug("foo_riigikogu")

    def test_no_underscore_raises(self):
        with pytest.raises(ValueError, match="unknown body suffix"):
            parse_issuer_slug("foo")


from kov_registry import auto_match_municipality


class TestAutoMatchMunicipality:
    @pytest.fixture
    def muns(self):
        return load_municipalities(MIN_MUNICIPALITIES)

    def test_unique_linn_match(self, muns):
        parts = {"slug": "tallinna_linnavolikogu", "root": "tallinna",
                 "body": "linnavolikogu", "bodyType": "volikogu",
                 "municipalityType": "linn"}
        assert auto_match_municipality(parts, muns) == "0784"

    def test_unique_vald_match(self, muns):
        parts = {"slug": "mulgi_vallavolikogu", "root": "mulgi",
                 "body": "vallavolikogu", "bodyType": "volikogu",
                 "municipalityType": "vald"}
        assert auto_match_municipality(parts, muns) == "0480"

    def test_disambiguates_linn_from_vald(self, muns):
        # Tartu exists as both linn (0793) and vald (0796)
        # tartu_linnavolikogu must match only the linn
        linn_parts = {"slug": "tartu_linnavolikogu", "root": "tartu",
                      "body": "linnavolikogu", "bodyType": "volikogu",
                      "municipalityType": "linn"}
        vald_parts = {"slug": "tartu_vallavolikogu", "root": "tartu",
                      "body": "vallavolikogu", "bodyType": "volikogu",
                      "municipalityType": "vald"}
        assert auto_match_municipality(linn_parts, muns) == "0793"
        assert auto_match_municipality(vald_parts, muns) == "0796"

    def test_no_match_returns_none(self, muns):
        # 'abja' is a defunct pre-2017 vald, not in the current 79
        parts = {"slug": "abja_vallavolikogu", "root": "abja",
                 "body": "vallavolikogu", "bodyType": "volikogu",
                 "municipalityType": "vald"}
        assert auto_match_municipality(parts, muns) is None
