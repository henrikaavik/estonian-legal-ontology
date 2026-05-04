"""Tests for scripts/extract_sanctions.py — Layer 2c additions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


def _iter_kov_inclusive(*args, **kwargs):
    """Wrapper that forces include_kov=True regardless of caller args.

    Tasks 2-5 are scheduled BEFORE the unpin in Task 6, so production
    main() still calls iter_peep_files(include_kov=False) at this
    point. Tests that stage KOV fixtures must monkeypatch
    `mod.iter_peep_files` with this wrapper so KOV-staged peeps are
    visible regardless of whether the production unpin has landed yet.
    After Task 6, production defaults to include_kov=True and the
    wrapper becomes a no-op — but keep it in the tests so they remain
    self-contained and independent of task-execution order.
    """
    from estleg_common import iter_peep_files as _real_iter
    kwargs.pop("include_kov", None)
    return _real_iter(include_kov=True, **kwargs)


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


class TestSanctionExtractionWithEnforcementStamp:
    """Integration tests verifying enforcedAtLevel lands on every
    provision that gains hasSanction, with one literal per provision
    regardless of sanction count."""

    @pytest.fixture
    def sample_kov_peep(self, tmp_path):
        """A KOV act with one provision that triggers a fine sanction.

        Uses Estonian text the existing extract_sanctions regex
        already recognises ('väärtegu, mille eest on ette nähtud
        rahatrahv kuni X trahviühikut').
        """
        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        peep = kov / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_15_Map_2020",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "rdfs:label": "Tallinna parkimismaks"},
                {"@id": "estleg:Reg_TLN_15_Map_2020_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Parkimisreeglite rikkumise eest, mis on "
                     "väärtegu, mille eest on ette nähtud rahatrahv "
                     "kuni 100 trahviühikut."},
            ],
        }), encoding="utf-8")
        return peep

    @pytest.fixture
    def sample_state_peep(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        peep = krr / "alkoholiseadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:AS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Alkoholiseadus"},
                {"@id": "estleg:AS_Par_42",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 42",
                 "estleg:summary":
                     "Alkoholi tarvitamise piirangute rikkumise eest, "
                     "mis on väärtegu, mille eest on ette nähtud "
                     "rahatrahv kuni 200 trahviühikut."},
            ],
        }), encoding="utf-8")
        return peep

    def test_kov_act_sanctions_get_municipality_stamp(
        self, sample_kov_peep, tmp_path, monkeypatch
    ):
        """KOV act with a fine-bearing provision → provision gains
        BOTH estleg:hasSanction AND
        estleg:enforcedAtLevel = 'municipality'."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir()
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        # Production main() still calls iter_peep_files(include_kov=False)
        # at this point in the task order; force KOV inclusion so the
        # staged KOV fixture is visible.
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(sample_kov_peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_1"))
        assert "estleg:hasSanction" in prov
        assert prov.get("estleg:enforcedAtLevel") == "municipality"

    def test_state_law_sanctions_get_state_stamp(
        self, sample_state_peep, tmp_path, monkeypatch
    ):
        """Law-typed act → provision gains 'state' stamp."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir()
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        # State-only fixture, but use the wrapper for consistency
        # with the other tests in this class.
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(sample_state_peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_42"))
        assert "estleg:hasSanction" in prov
        assert prov.get("estleg:enforcedAtLevel") == "state"

    def test_provision_with_no_sanction_gets_no_enforcement_stamp(
        self, tmp_path, monkeypatch
    ):
        """Provision whose summary has no sanction pattern does NOT
        gain estleg:enforcedAtLevel. Preserves the 'only on
        sanction-bearing provisions' rule."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "ametiuhingute_seadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:AmS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:AmS_Par_5",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 5",
                 "estleg:summary":
                     "Ametiühingu eesmärk on töötajate huvide "
                     "kaitsmine ning töötingimuste parandamine."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_5"))
        assert "estleg:hasSanction" not in prov
        assert "estleg:enforcedAtLevel" not in prov

    def test_provision_with_multiple_sanctions_gets_one_enforcement_literal(
        self, tmp_path, monkeypatch
    ):
        """A provision that emits MULTIPLE Sanction_* records still
        gains exactly ONE estleg:enforcedAtLevel literal (a bare
        string, NOT a list of repeated values)."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "karistusseadustik_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:KARIST_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:KARIST_Par_113",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 113",
                 "estleg:summary":
                     "Tervisekahjustuse tekitamise eest karistatakse "
                     "rahalise karistusega või kuni üheaastase "
                     "vangistusega."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_113"))
        # hasSanction is a list of multiple sanction refs
        sanctions = prov.get("estleg:hasSanction", [])
        if isinstance(sanctions, dict):
            sanctions = [sanctions]
        assert len(sanctions) >= 2, f"expected ≥2 sanctions, got {sanctions}"
        # enforcedAtLevel is ONE bare literal — not a list
        level = prov.get("estleg:enforcedAtLevel")
        assert isinstance(level, str), (
            f"expected str literal, got {type(level).__name__}: {level!r}"
        )
        assert level == "state"
