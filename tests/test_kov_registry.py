"""Tests for scripts/kov_registry.py — issuer/municipality registry helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from kov_registry import load_municipalities


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
