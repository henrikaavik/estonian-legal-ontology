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


class TestSanctionsIdempotency:
    """Idempotency tests for both peep-side mutations and the
    sanctions JSON file lifecycle. Each test invokes main() twice
    over the same fixture; the second run must converge to the
    correct steady state regardless of the first run's output.
    """

    def test_stale_enforcement_cleared_when_sanctions_removed(
        self, tmp_path, monkeypatch
    ):
        """Provision starts with prior hasSanction + enforcedAtLevel.
        The act's summary is updated to text the regex won't match.
        Second run strips BOTH triples (peep-side idempotency)."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "stale_peep.json"
        # Stage with PRIOR triples already present.
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Stale_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Stale_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 # Summary now contains NO sanction language.
                 "estleg:summary":
                     "See paragrahv kirjeldab üldist eesmärki ja "
                     "ei sätesta karistusi.",
                 # Stale triples from a prior run:
                 "estleg:hasSanction": [{"@id": "estleg:Sanction_Stale_Par_1_fine"}],
                 "estleg:enforcedAtLevel": "state"},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Stale_Par_1")
        # Stale triples must be GONE.
        assert "estleg:hasSanction" not in prov
        assert "estleg:enforcedAtLevel" not in prov

    def test_classification_recomputed_when_act_type_flips(
        self, tmp_path, monkeypatch
    ):
        """Edge case: classification is stateless. If a peep's @type
        is changed by an upstream corpus correction (Law → Municipal
        Regulation or vice versa), the recomputed enforcedAtLevel
        reflects the new type, not the prior emission."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "regulations" / "kov" / "test"
        peep.mkdir(parents=True)
        peep_file = peep / "act_peep.json"
        # @type was Law (some prior corpus state); now correctly
        # MunicipalRegulation. Prior emission was 'state'; this run
        # must produce 'municipality'.
        peep_file.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_Test_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_Test_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Parkimisrikkumise eest, mis on väärtegu, mille "
                     "eest on ette nähtud rahatrahv kuni 50 "
                     "trahviühikut.",
                 # Stale (wrong) classification from a prior run.
                 "estleg:enforcedAtLevel": "state"},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        # KOV-staged fixture; force include_kov=True since Task 6
        # hasn't unpinned yet.
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep_file, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Reg_Test_Par_1")
        # Recomputed from current @type — 'municipality', not the
        # stale 'state'.
        assert prov.get("estleg:enforcedAtLevel") == "municipality"

    def test_no_op_when_no_existing_no_fresh(
        self, tmp_path, monkeypatch
    ):
        """A peep with no sanctions and no prior stamps shouldn't
        change between runs (no spurious writes)."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "boring_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Boring_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Boring_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Käesolev seadus reguleerib üldisi suhteid."},
            ],
        }), encoding="utf-8")
        before_mtime = peep.stat().st_mtime
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        # File untouched (mtime unchanged) — no spurious save.
        after_mtime = peep.stat().st_mtime
        assert before_mtime == after_mtime, (
            "no-op file must not be re-saved when nothing changes"
        )

    def test_stale_sanctions_json_deleted_when_act_loses_all_sanctions(
        self, tmp_path, monkeypatch
    ):
        """When an act loses ALL its sanction matches between runs,
        the corresponding krr_outputs/sanctions/sanctions_<law>.json
        file is DELETED so orphan Sanction_* nodes don't outlive the
        inverse pass."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)

        # Stage a peep whose summary has NO sanction language.
        peep = krr / "lostsanc_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:LostSanc_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Lost sanctions law"},
                {"@id": "estleg:LostSanc_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "See seadus käsitleb üldisi põhimõtteid."},
            ],
        }), encoding="utf-8")

        # Stage a STALE sanctions file from a prior run, containing
        # a Sanction_* node + applicableProvision pointing at the
        # provision that no longer carries hasSanction.
        # The filename uses sanitize_id of the law slug — match the
        # script's filename convention exactly.
        from extract_sanctions import sanitize_id
        stale_filename = f"sanctions_{sanitize_id('lostsanc')}.json"
        stale_path = sanctions_dir / stale_filename
        stale_path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Sanctions_lostsanc_Map",
                 "@type": ["owl:Ontology"],
                 "rdfs:label": "Sanctions – lostsanc",
                 "dc:source": "lostsanc"},
                {"@id": "estleg:Sanction_LostSanc_Par_1_fine",
                 "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                 "estleg:sanctionType": "fine",
                 "estleg:applicableProvision": {"@id": "estleg:LostSanc_Par_1"}},
            ],
        }), encoding="utf-8")
        assert stale_path.exists()

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        # The stale sanctions JSON file MUST be gone.
        assert not stale_path.exists(), (
            "stale sanctions JSON must be deleted when act loses "
            "all matches"
        )


class TestSanctionsCoverageReport:
    """The script must write a kov_pipeline_coverage CoverageReport
    JSON to krr_outputs/reports/kov/extract_sanctions_coverage.json,
    and main() must return non-zero when the gate fails."""

    def test_coverage_report_written_with_kov_split(
        self, tmp_path, monkeypatch
    ):
        """Stage 1 KOV act + 1 state law, both with sanction text.
        Run main(). Assert the coverage JSON exists with files_with_output_kov >= 1
        and triples_emitted_kov >= 1."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        reports_dir = krr / "reports" / "kov"
        sanctions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        # KOV act with sanction
        kov = krr / "regulations" / "kov" / "test"
        kov.mkdir(parents=True)
        (kov / "kov_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TST_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_TST_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Parkimisrikkumise eest, mis on väärtegu, mille "
                     "eest on ette nähtud rahatrahv kuni 50 trahviühikut."},
            ],
        }), encoding="utf-8")

        # State law with sanction
        (krr / "alko_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:AS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:AS_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Alkoholi keelu rikkumise eest, mis on väärtegu, "
                     "mille eest on ette nähtud rahatrahv kuni 100 "
                     "trahviühikut."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc == 0, f"main() should return 0 on success, got {rc}"

        coverage_path = reports_dir / "extract_sanctions_coverage.json"
        assert coverage_path.exists()
        with open(coverage_path, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        assert cov["files_processed_kov"] >= 1
        assert cov["files_with_output_kov"] >= 1
        assert cov["triples_emitted_kov"] >= 1
        # Total counters include KOV + non-KOV
        assert cov["files_with_output"] >= 2
        assert cov["triples_emitted"] >= 2

    def test_gate_fails_when_kov_input_nontrivial_but_no_output(
        self, tmp_path, monkeypatch
    ):
        """Mock iter_peep_files to return ≥11,000 KOV paths whose
        summaries contain no sanction language. main() must return 1
        (gate fail).

        Implementation note: rather than physically writing 11,001
        fixture files (slow on every full pytest run after Task 6
        unpins the script), monkeypatch BOTH `mod.iter_peep_files`
        (returns synthetic Path objects) AND `mod.load_json` (returns
        a single canned doc for each synthetic path). The script's
        per-file flow processes each path identically, so one canned
        doc covers all 11,001 calls.
        """
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        reports_dir = krr / "reports" / "kov"
        sanctions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        # Synthetic KOV paths — never physically created, only used
        # for path-identity checks (KOV attribution scans for
        # "regulations/kov/" in str(path)).
        kov_dir_marker = krr / "regulations" / "kov" / "no_sanctions"
        synthetic_kov_paths = [
            kov_dir_marker / f"act_{i}_peep.json" for i in range(11001)
        ]

        # Canned doc — KOV act with NO sanction text. The script's
        # extract_sanctions() returns [] on this summary, so no
        # sanctions are emitted regardless of how many times the
        # doc is read.
        canned_doc = {
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_Boring_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_Boring_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Käesolev määrus reguleerib administratiivseid "
                     "küsimusi ega sätesta karistusi."},
            ],
        }

        def fake_iter(*args, **kwargs):
            return synthetic_kov_paths

        def fake_load(filepath):
            # Return a deepcopy so each per-file iteration mutates an
            # independent dict (the script clears stale triples
            # in-memory; sharing one dict would corrupt later
            # iterations).
            import copy
            return copy.deepcopy(canned_doc)

        # Stub save_json to a no-op so per-file save calls don't try
        # to write the synthetic (non-existent) paths.
        def fake_save(filepath, doc):
            return None

        monkeypatch.setattr(mod, "iter_peep_files", fake_iter)
        monkeypatch.setattr(mod, "load_json", fake_load)
        monkeypatch.setattr(mod, "save_json", fake_save)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc == 1, (
            f"gate must fail (return 1) when KOV input ≥11k but "
            f"no output; got rc={rc}"
        )


class TestCorpusInvariant:
    """Corpus-wide invariant: every provision carrying
    estleg:hasSanction has exactly ONE estleg:enforcedAtLevel
    literal in {"state", "municipality"}.

    Fails when krr_outputs/ is empty because this is a corpus gate.
    Marked @pytest.mark.slow so CI can opt out on the fast path; runs
    in the integration suite.
    """

    @pytest.mark.slow
    def test_every_has_sanction_provision_has_exactly_one_enforced_at_level(self):
        from estleg_common import iter_peep_files, KRR_DIR
        if not KRR_DIR.exists() or not list(KRR_DIR.glob("*_peep.json")):
            pytest.fail("krr_outputs/ empty; corpus invariant was not checked")

        violations = []
        for peep in iter_peep_files():
            try:
                with open(peep, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            for node in doc.get("@graph", []):
                if "estleg:hasSanction" not in node:
                    continue
                level = node.get("estleg:enforcedAtLevel")
                if level is None:
                    violations.append(
                        f"{peep.name}: {node.get('@id')} has hasSanction "
                        f"but no enforcedAtLevel"
                    )
                elif isinstance(level, list):
                    violations.append(
                        f"{peep.name}: {node.get('@id')} has list-valued "
                        f"enforcedAtLevel: {level}"
                    )
                elif not isinstance(level, str):
                    violations.append(
                        f"{peep.name}: {node.get('@id')} has non-string "
                        f"enforcedAtLevel ({type(level).__name__}): {level!r}"
                    )
                elif level not in {"state", "municipality"}:
                    violations.append(
                        f"{peep.name}: {node.get('@id')} has unexpected "
                        f"enforcedAtLevel value: {level!r}"
                    )
        assert not violations, (
            f"Corpus invariant violated by {len(violations)} provisions:\n  "
            + "\n  ".join(violations[:20])
            + (f"\n  ... and {len(violations) - 20} more"
               if len(violations) > 20 else "")
        )


# --------------------------------------------------------------------- #
# Regression tests for ontology-review-plan issue #169.
# --------------------------------------------------------------------- #


class TestImprisonmentUnitHeuristic:
    """The fallback ``vangistus(ega) N`` pattern must require an
    explicit unit token (aasta/kuu/päev). The previous heuristic
    guessed ``years`` for N <= 20 and ``months`` otherwise, which
    silently mis-attributed sentences. If no unit is present, no
    ``max_penalty`` is emitted from this branch.
    """

    def test_no_unit_emits_no_penalty(self):
        from extract_sanctions import extract_imprisonment
        # No unit: previous code would have emitted "5 years"; new code
        # emits nothing from the fallback (no other branch matches
        # the bare digit form either).
        results = extract_imprisonment("vangistusega 5")
        assert results == [], (
            f"unit-less fallback must emit nothing, got {results}"
        )

    def test_no_unit_at_30_emits_no_penalty(self):
        """The previous heuristic mapped 30 → '30 months'. The new
        code refuses to guess and emits nothing."""
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("vangistusega 30")
        assert results == []

    def test_explicit_aasta_unit_yields_years(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("vangistus 5 aasta")
        assert any(r.get("max_penalty") == "5 years" for r in results)

    def test_explicit_kuu_unit_yields_months(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("vangistusega 6 kuud")
        assert any(r.get("max_penalty") == "6 months" for r in results)

    def test_explicit_paev_unit_yields_days(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("vangistusega 14 päeva")
        assert any(r.get("max_penalty") == "14 days" for r in results)


class TestImprisonmentWordRegexAnchored:
    """``extract_imprisonment`` word-based patterns must be anchored
    to the closed set of supported Estonian numerals so an arbitrary
    token before ``aasta`` does not silently match.
    """

    def test_unsupported_numeral_does_not_match(self):
        from extract_sanctions import extract_imprisonment
        # 'ksdjflk' is not a numeral; the prior `\w+` pattern would
        # have captured it and yielded a None _parse_number, but the
        # new pattern won't even match.
        results = extract_imprisonment("kuni ksdjflkaasta vangistus")
        assert all(r.get("max_penalty") != "None years" for r in results)
        assert results == []

    def test_supported_numeral_still_matches(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("kuni viieaastase vangistusega")
        assert any(r.get("max_penalty") == "5 years" for r in results)

    def test_range_pattern_with_supported_numerals(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment(
            "kahe kuni viieaastase vangistusega"
        )
        assert any(
            r.get("min_penalty") == "2 years"
            and r.get("max_penalty") == "5 years"
            for r in results
        )


class TestSanctionIRICounterDeterministic:
    """The sanction loop sorts sanctions before assigning suffix
    counts so IRI assignment is reproducible across runs."""

    def test_sorted_before_iri_assignment(self, tmp_path, monkeypatch):
        import extract_sanctions as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)

        # Provision text that triggers BOTH imprisonment AND a fine.
        peep = krr / "deterministic_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Det_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Det_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 # Two imprisonment sanctions: max-only (3 years) and
                 # range (1-5 years) — these used to compete on the
                 # same ``Sanction_<par>_imprisonment`` base IRI.
                 "estleg:summary":
                     "Karistatakse kuni kolmeaastase vangistusega või "
                     "ühe kuni viieaastase vangistusega."},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        # Run twice and capture the IRI suffix order both times.
        runs = []
        for _ in range(2):
            mod.main()
            with open(peep, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            prov = next(n for n in doc["@graph"]
                        if n.get("@id") == "estleg:Det_Par_1")
            iris = [
                ref["@id"]
                for ref in (prov.get("estleg:hasSanction") or [])
            ]
            runs.append(iris)

        assert runs[0] == runs[1], (
            f"sanction IRIs must be deterministic across runs; "
            f"run1={runs[0]} run2={runs[1]}"
        )


class TestPecuniaryStatutoryDefaultFlag:
    """Pecuniary punishment fallback emits ``estleg:isStatutoryDefault``
    on the Sanction node so consumers can distinguish text-extracted
    from statutory-default values.
    """

    def test_pecuniary_default_flag_lands(self, tmp_path, monkeypatch):
        import extract_sanctions as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)

        peep = krr / "pecu_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Pecu_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Pecu_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Selle eest karistatakse rahalise karistusega."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        # Find the sanction file produced for this law.
        from extract_sanctions import sanitize_id
        sanc_file = sanctions_dir / f"sanctions_{sanitize_id('pecu')}.json"
        assert sanc_file.exists()
        with open(sanc_file, "r", encoding="utf-8") as fh:
            sdoc = json.load(fh)
        pecu_nodes = [
            n for n in sdoc["@graph"]
            if n.get("estleg:sanctionType") == "pecuniary_punishment"
        ]
        assert pecu_nodes, "expected at least one pecuniary sanction node"
        for n in pecu_nodes:
            assert n.get("estleg:isStatutoryDefault") is True, (
                f"pecuniary statutory-default emission must carry "
                f"the flag; got {n}"
            )

    def test_extract_pecuniary_records_flag(self):
        """Unit-level: extract_pecuniary tags its emitted records."""
        from extract_sanctions import extract_pecuniary
        results = extract_pecuniary("karistatakse rahalise karistusega")
        assert results
        assert all(r.get("is_statutory_default") is True for r in results)


class TestArrestFallbackTightened:
    """Bare ``\\barest`` no longer triggers the statutory-default
    fallback. Both a punishment-context suffix AND a sentencing verb
    must co-occur. When fallback fires it carries the
    ``is_statutory_default`` flag.
    """

    def test_arestima_word_does_not_match(self):
        """``arestima`` (verb 'to arrest') outside sentencing
        language must not produce a 30-day record."""
        from extract_sanctions import extract_arrest
        results = extract_arrest("Politsei võib arestima kahtlustatava.")
        # No fallback emission — no sentencing verb in context.
        assert results == []

    def test_punishment_suffix_without_sentencing_verb_does_not_match(self):
        """``arestiga`` alone (no karistatakse/kohaldatakse) does
        not trigger the fallback."""
        from extract_sanctions import extract_arrest
        results = extract_arrest("Otsus täidetakse arestiga.")
        assert results == []

    def test_full_sentencing_context_triggers_fallback(self):
        """``karistatakse ... arestiga`` triggers the statutory
        fallback with the flag set."""
        from extract_sanctions import extract_arrest
        results = extract_arrest(
            "Selle eest karistatakse rahatrahviga või arestiga."
        )
        assert results
        assert any(
            r.get("max_penalty") == "30 days"
            and r.get("is_statutory_default") is True
            for r in results
        )

    def test_explicit_day_count_wins_over_fallback(self):
        from extract_sanctions import extract_arrest
        results = extract_arrest(
            "Karistatakse aresti kuni 15 päeva."
        )
        assert any(r.get("max_penalty") == "15 days" for r in results)
        # Explicit count is NOT statutory default.
        assert all(
            not r.get("is_statutory_default") for r in results
        )


class TestEstonianNumeralsHelpers:
    """Public helper for tests/diagnostics returns the supported set."""

    def test_helper_returns_frozenset(self):
        from extract_sanctions import estonian_numerals_supported
        s = estonian_numerals_supported()
        assert isinstance(s, frozenset)

    def test_helper_includes_canonical_lemmas(self):
        from extract_sanctions import estonian_numerals_supported
        s = estonian_numerals_supported()
        for lemma in ("ühe", "kahe", "viie", "kümne",
                      "kahekümne", "sada", "tuhat"):
            assert lemma in s, (
                f"{lemma!r} should be in supported numerals; got {sorted(s)}"
            )


class TestProvisionRefEmptyFallback:
    """``_provision_ref`` returns '' (not 'unknown') when no info
    is available. ``_build_label`` already suppresses the
    parenthetical when ref is empty.
    """

    def test_empty_when_no_info(self):
        from extract_sanctions import _provision_ref
        assert _provision_ref({}) == ""

    def test_with_paragrahv_and_law_abbr(self):
        from extract_sanctions import _provision_ref
        ref = _provision_ref({
            "estleg:paragrahv": "121",
            "estleg:lawAbbreviation": "KarS",
        })
        assert "KarS" in ref
        assert "121" in ref

    def test_with_node_id_only(self):
        """Falls back to law abbreviation parsed from @id when
        explicit lawAbbreviation is missing."""
        from extract_sanctions import _provision_ref
        ref = _provision_ref({
            "@id": "estleg:KarS_p121",
            "estleg:paragrahv": "121",
        })
        assert ref.startswith("KarS")

    def test_label_no_parenthetical_when_ref_empty(self):
        """Integration: ``_build_label`` does not emit '(unknown)'
        when ``_provision_ref`` returns ''."""
        from extract_sanctions import _build_label, _provision_ref
        empty_ref = _provision_ref({})
        assert empty_ref == ""
        label = _build_label(
            "imprisonment",
            {"max_penalty": "5 years"},
            empty_ref,
        )
        assert "(" not in label
        assert "unknown" not in label.lower()


class TestDatetimeNowUTC:
    """``datetime.now()`` must use ``timezone.utc`` for date stamps
    so the report's day boundary aligns with other UTC-stamped
    artefacts."""

    def test_report_generated_uses_utc_today(
        self, tmp_path, monkeypatch
    ):
        import extract_sanctions as mod
        import estleg_common
        from datetime import datetime, timezone

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)

        # Empty corpus — main() produces an empty report.
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)
        report_path = krr / "sanctions_report.json"
        assert report_path.exists()
        with open(report_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
        # The 'generated' field must be the UTC date in ISO form.
        expected = datetime.now(timezone.utc).date().isoformat()
        assert report["generated"] == expected, (
            f"report 'generated' should match UTC date; "
            f"expected={expected} got={report['generated']}"
        )
