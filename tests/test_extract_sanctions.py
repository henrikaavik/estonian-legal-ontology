"""Tests for scripts/extract_sanctions.py — Layer 2c additions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


class TestClassifyEnforcementLevel:
    """Unit tests for the act-type → enforcement-level classification.

    The rule: estleg:MunicipalRegulation → "municipality"; anything
    else → "state". @type may be either str or list — both must be
    handled.
    """

    def test_municipal_regulation_returns_municipality(self):
        from extract_sanctions import _classify_enforcement_level
        act = {
            "@id": "estleg:Reg_TLN_15_Map_2020",
            "@type": ["owl:Ontology", "estleg:Act",
                       "estleg:MunicipalRegulation"],
        }
        assert _classify_enforcement_level(act) == "municipality"

    def test_law_returns_state(self):
        from extract_sanctions import _classify_enforcement_level
        act = {
            "@id": "estleg:KOKS_Map_2026",
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        }
        assert _classify_enforcement_level(act) == "state"

    def test_government_regulation_returns_state(self):
        from extract_sanctions import _classify_enforcement_level
        act = {
            "@id": "estleg:Reg_VV251_Map_2007",
            "@type": ["owl:Ontology", "estleg:Act",
                       "estleg:GovernmentRegulation"],
        }
        assert _classify_enforcement_level(act) == "state"

    def test_string_type_normalised(self):
        """@type as a bare string (not list) must be handled."""
        from extract_sanctions import _classify_enforcement_level
        # Edge case — Layer 1 generators always emit lists, but the
        # JSON-LD spec allows strings, so the helper must be robust.
        act_string_type = {
            "@id": "estleg:Reg_X",
            "@type": "estleg:MunicipalRegulation",
        }
        assert _classify_enforcement_level(act_string_type) == "municipality"
