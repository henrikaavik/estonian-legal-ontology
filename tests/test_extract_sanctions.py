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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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


class TestIssue273ImprisonmentRangeOrdering:
    """Issue #273: the range parser read group 1 as min and group 2 as max
    with no ordering check, so a high-to-low source range yielded
    ``max < min`` (e.g. 'kümne kuni viieaastase' → min 10 / max 5). Both the
    word-based and the digit-based range branches must now sort the bounds."""

    def test_high_to_low_word_range_is_reordered(self):
        """The exact case named in the issue: 'kümne kuni viieaastase'
        (ten down to five years) must yield min 5 / max 10."""
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("kümne kuni viieaastase vangistusega")
        assert any(
            r.get("min_penalty") == "5 years"
            and r.get("max_penalty") == "10 years"
            for r in results
        ), results

    def test_high_to_low_digit_range_is_reordered(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("10 kuni 5 aastase vangistusega")
        assert any(
            r.get("min_penalty") == "5 years"
            and r.get("max_penalty") == "10 years"
            for r in results
        ), results

    def test_low_to_high_word_range_unchanged(self):
        """A normal low-to-high range must pass through untouched (no
        spurious swap)."""
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("kahe kuni viieaastase vangistusega")
        assert any(
            r.get("min_penalty") == "2 years"
            and r.get("max_penalty") == "5 years"
            for r in results
        ), results

    def test_min_never_exceeds_max(self):
        """Invariant across both branches: for every range record emitted,
        the numeric min must be <= the numeric max."""
        from extract_sanctions import extract_imprisonment
        for text in (
            "kümne kuni viieaastase vangistusega",
            "10 kuni 5 aastase vangistusega",
            "kahe kuni viieaastase vangistusega",
            "viie kuni kümneaastase vangistusega",
        ):
            for r in extract_imprisonment(text):
                if "min_penalty" in r and "max_penalty" in r:
                    lo = int(r["min_penalty"].split()[0])
                    hi = int(r["max_penalty"].split()[0])
                    assert lo <= hi, f"{text!r} -> {r!r}"

    def test_ordered_year_range_helper_swaps_and_sorts(self):
        from extract_sanctions import _ordered_year_range
        assert _ordered_year_range(10, 5) == ("5 years", "10 years")
        assert _ordered_year_range(2, 5) == ("2 years", "5 years")
        assert _ordered_year_range(7, 7) == ("7 years", "7 years")


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
                "estleg": "https://w3id.org/estleg/",
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
                "estleg": "https://w3id.org/estleg/",
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
        from estleg_common import BUILD_EVALUATION_DATE

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)

        # Empty corpus — main() produces an empty report.
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)
        report_path = krr / "reports" / "sanctions_report.json"
        assert report_path.exists()
        with open(report_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
        # #295: 'generated' must be the pinned deterministic build date
        # (no wall-clock churn in the git-tracked sanctions_report.json).
        assert report["generated"] == BUILD_EVALUATION_DATE, (
            f"report 'generated' should match pinned BUILD_EVALUATION_DATE; "
            f"expected={BUILD_EVALUATION_DATE} got={report['generated']}"
        )


# --------------------------------------------------------------------- #
# Regression tests for issue #133 — structured, computable penalties.
# --------------------------------------------------------------------- #


class TestParsePenaltyToStructured:
    """``_parse_penalty_to_structured`` maps the free-text strings the
    extractors produce onto an (amount, unit, currency) triple from the
    controlled unit set, or returns ``None`` when the string can't be
    confidently parsed."""

    @pytest.mark.parametrize(
        "penalty_str, expected",
        [
            ("5 years", ("5", "years", None)),
            ("12 years", ("12", "years", None)),
            ("1 year", ("1", "years", None)),
            ("6 months", ("6", "months", None)),
            ("1 month", ("1", "months", None)),
            ("30 days", ("30", "days", None)),
            ("14 days", ("14", "days", None)),
            ("1 day", ("1", "days", None)),
            ("300 daily rates", ("300", "daily_rates", None)),
            ("500 daily rates", ("500", "daily_rates", None)),
            ("1 daily rate", ("1", "daily_rates", None)),
            ("300 fine units", ("300", "fine_units", None)),
            ("20 fine units", ("20", "fine_units", None)),
            ("1 fine unit", ("1", "fine_units", None)),
            ("15000 EUR", ("15000", "monetary", "EUR")),
            ("640 EUR", ("640", "monetary", "EUR")),
            # ``_normalise_penalty_text`` never singularises EUR, but
            # the parser is tolerant of the digit-form anyway.
            ("1 EUR", ("1", "monetary", "EUR")),
        ],
    )
    def test_parseable_strings(self, penalty_str, expected):
        from decimal import Decimal

        from extract_sanctions import _parse_penalty_to_structured

        result = _parse_penalty_to_structured(penalty_str)
        assert result is not None, f"{penalty_str!r} should parse"
        amount, unit, currency = result
        assert (str(amount), unit, currency) == expected
        assert amount == Decimal(expected[0])

    @pytest.mark.parametrize(
        "penalty_str",
        [
            "life",          # life imprisonment — no finite amount
            "",              # empty
            "   ",           # whitespace only
            "12 weeks",      # unit token outside the controlled set
            "five years",    # non-numeric amount
            "years",         # no amount
            "30",            # no unit
            "0 days",        # non-positive amount
        ],
    )
    def test_unparseable_strings_return_none(self, penalty_str):
        from extract_sanctions import _parse_penalty_to_structured

        assert _parse_penalty_to_structured(penalty_str) is None

    def test_returned_unit_is_in_controlled_set(self):
        from extract_sanctions import (
            PENALTY_UNITS,
            _parse_penalty_to_structured,
        )

        for s in ("5 years", "6 months", "30 days", "300 daily rates",
                  "300 fine units", "15000 EUR"):
            res = _parse_penalty_to_structured(s)
            assert res is not None
            assert res[1] in PENALTY_UNITS

    def test_currency_only_emitted_for_monetary(self):
        from extract_sanctions import (
            PENALTY_UNIT_MONETARY,
            _parse_penalty_to_structured,
        )

        for s in ("5 years", "300 fine units", "300 daily rates", "30 days"):
            amount, unit, currency = _parse_penalty_to_structured(s)
            assert currency is None
            assert unit != PENALTY_UNIT_MONETARY
        amount, unit, currency = _parse_penalty_to_structured("15000 EUR")
        assert unit == PENALTY_UNIT_MONETARY
        assert currency == "EUR"


class TestStructuredPenaltyFieldHelpers:
    """``_structured_penalty_fields`` / ``_attach_structured_penalties``
    build the JSON-LD property block for a Sanction node and return the
    count of penalty strings that couldn't be parsed."""

    def test_fields_block_for_monetary(self):
        from extract_sanctions import _structured_penalty_fields

        fields, miss = _structured_penalty_fields("15000 EUR", "max")
        assert miss == 0
        assert fields["estleg:maxPenaltyAmount"] == {
            "@value": "15000", "@type": "xsd:decimal",
        }
        assert fields["estleg:maxPenaltyUnit"] == "monetary"
        assert fields["estleg:maxPenaltyCurrency"] == "EUR"

    def test_fields_block_for_duration_has_no_currency(self):
        from extract_sanctions import _structured_penalty_fields

        fields, miss = _structured_penalty_fields("5 years", "max")
        assert miss == 0
        assert fields["estleg:maxPenaltyAmount"] == {
            "@value": "5", "@type": "xsd:decimal",
        }
        assert fields["estleg:maxPenaltyUnit"] == "years"
        assert "estleg:maxPenaltyCurrency" not in fields

    def test_fields_block_unparseable_returns_empty_and_one(self):
        from extract_sanctions import _structured_penalty_fields

        fields, miss = _structured_penalty_fields("life", "max")
        assert fields == {}
        assert miss == 1

    def test_min_prefix_uses_min_property_names(self):
        from extract_sanctions import _structured_penalty_fields

        fields, _miss = _structured_penalty_fields("3 years", "min")
        assert "estleg:minPenaltyAmount" in fields
        assert "estleg:minPenaltyUnit" in fields
        assert fields["estleg:minPenaltyUnit"] == "years"

    def test_attach_handles_both_min_and_max(self):
        from extract_sanctions import _attach_structured_penalties

        node = {"@id": "estleg:X"}
        miss = _attach_structured_penalties(
            node, {"max_penalty": "12 years", "min_penalty": "3 years"}
        )
        assert miss == 0
        assert node["estleg:maxPenaltyAmount"] == {
            "@value": "12", "@type": "xsd:decimal",
        }
        assert node["estleg:maxPenaltyUnit"] == "years"
        assert node["estleg:minPenaltyAmount"] == {
            "@value": "3", "@type": "xsd:decimal",
        }
        assert node["estleg:minPenaltyUnit"] == "years"

    def test_attach_skips_missing_penalty_keys(self):
        from extract_sanctions import _attach_structured_penalties

        node = {"@id": "estleg:X"}
        miss = _attach_structured_penalties(node, {"sanction_type": "fine"})
        assert miss == 0
        assert node == {"@id": "estleg:X"}

    def test_attach_unparseable_max_only_bumps_one(self):
        from extract_sanctions import _attach_structured_penalties

        node = {"@id": "estleg:X"}
        miss = _attach_structured_penalties(node, {"max_penalty": "life"})
        assert miss == 1
        # No structured fields landed.
        assert all(
            k not in node
            for k in (
                "estleg:maxPenaltyAmount",
                "estleg:maxPenaltyUnit",
                "estleg:maxPenaltyCurrency",
            )
        )

    def test_decimal_literal_renders_cleanly(self):
        from decimal import Decimal

        from extract_sanctions import _decimal_literal

        assert _decimal_literal(Decimal("5")) == "5"
        assert _decimal_literal(Decimal("15000")) == "15000"
        assert _decimal_literal(Decimal("300")) == "300"
        # A genuine fractional value is preserved (trailing zeros trimmed).
        assert _decimal_literal(Decimal("5.50")) == "5.5"


class TestStructuredPenaltyEndToEnd:
    """A Sanction node built from a parseable penalty carries the
    structured trio with the right ``xsd:decimal`` typing; an
    unparseable penalty gets only the free-text field and bumps the
    ``penalty_unparsed`` counter in the report."""

    def test_parseable_fine_node_has_structured_trio(
        self, tmp_path, monkeypatch
    ):
        import extract_sanctions as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "struct_fine_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:SF_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:SF_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Rikkumise eest, mis on väärtegu, mille eest on "
                     "ette nähtud rahatrahv kuni 300 trahviühikut."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        from extract_sanctions import sanitize_id
        sanc_file = sanctions_dir / f"sanctions_{sanitize_id('struct_fine')}.json"
        assert sanc_file.exists()
        with open(sanc_file, "r", encoding="utf-8") as fh:
            sdoc = json.load(fh)
        fine_nodes = [
            n for n in sdoc["@graph"]
            if n.get("estleg:sanctionType") == "fine"
        ]
        assert fine_nodes, "expected a fine sanction node"
        node = fine_nodes[0]
        # Free-text field preserved (backward compat).
        assert node["estleg:maxPenalty"] == "300 fine units"
        # Structured trio present with correct typing.
        assert node["estleg:maxPenaltyAmount"] == {
            "@value": "300", "@type": "xsd:decimal",
        }
        assert node["estleg:maxPenaltyUnit"] == "fine_units"
        # fine_units is not monetary → no currency.
        assert "estleg:maxPenaltyCurrency" not in node

        # Report records the structured count, no unparsed.
        report = json.load(open(krr / "reports" / "sanctions_report.json"))
        assert report["penalty_normalisation"]["penalty_structured"] >= 1
        assert report["penalty_normalisation"]["penalty_unparsed"] == 0

    def test_life_imprisonment_node_has_no_structured_fields_and_bumps_counter(
        self, tmp_path, monkeypatch
    ):
        import extract_sanctions as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "life_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:LIFE_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:LIFE_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Selle eest karistatakse eluaegse vangistusega."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        from extract_sanctions import sanitize_id
        sanc_file = sanctions_dir / f"sanctions_{sanitize_id('life')}.json"
        assert sanc_file.exists()
        with open(sanc_file, "r", encoding="utf-8") as fh:
            sdoc = json.load(fh)
        imp_nodes = [
            n for n in sdoc["@graph"]
            if n.get("estleg:sanctionType") == "imprisonment"
        ]
        assert imp_nodes, "expected an imprisonment sanction node"
        node = imp_nodes[0]
        assert node["estleg:maxPenalty"] == "life"
        # No structured fields for an unparseable penalty.
        for k in ("estleg:maxPenaltyAmount", "estleg:maxPenaltyUnit",
                  "estleg:maxPenaltyCurrency"):
            assert k not in node, f"{k} must not be emitted for 'life'"

        report = json.load(open(krr / "reports" / "sanctions_report.json"))
        assert report["penalty_normalisation"]["penalty_unparsed"] >= 1

    def test_imprisonment_range_node_has_both_min_and_max_structured(
        self, tmp_path, monkeypatch
    ):
        import extract_sanctions as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "range_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:RNG_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:RNG_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Karistatakse kahe kuni viieaastase vangistusega."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        from extract_sanctions import sanitize_id
        sanc_file = sanctions_dir / f"sanctions_{sanitize_id('range')}.json"
        with open(sanc_file, "r", encoding="utf-8") as fh:
            sdoc = json.load(fh)
        imp_nodes = [
            n for n in sdoc["@graph"]
            if n.get("estleg:sanctionType") == "imprisonment"
            and "estleg:minPenalty" in n
        ]
        assert imp_nodes, "expected a range imprisonment sanction node"
        node = imp_nodes[0]
        assert node["estleg:minPenalty"] == "2 years"
        assert node["estleg:maxPenalty"] == "5 years"
        assert node["estleg:minPenaltyAmount"] == {
            "@value": "2", "@type": "xsd:decimal",
        }
        assert node["estleg:minPenaltyUnit"] == "years"
        assert node["estleg:maxPenaltyAmount"] == {
            "@value": "5", "@type": "xsd:decimal",
        }
        assert node["estleg:maxPenaltyUnit"] == "years"

    def test_statutory_default_flag_coexists_with_structured_fields(
        self, tmp_path, monkeypatch
    ):
        """The #169 ``estleg:isStatutoryDefault`` flag still lands on a
        node that also carries the #133 structured penalty fields."""
        import extract_sanctions as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "pecu_struct_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:PS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:PS_Par_1",
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

        from extract_sanctions import sanitize_id
        sanc_file = sanctions_dir / f"sanctions_{sanitize_id('pecu_struct')}.json"
        with open(sanc_file, "r", encoding="utf-8") as fh:
            sdoc = json.load(fh)
        pecu = [
            n for n in sdoc["@graph"]
            if n.get("estleg:sanctionType") == "pecuniary_punishment"
        ]
        assert pecu
        node = pecu[0]
        # Statutory-default fallback: "500 daily rates".
        assert node["estleg:maxPenalty"] == "500 daily rates"
        assert node["estleg:isStatutoryDefault"] is True
        # ...and the structured trio is present too.
        assert node["estleg:maxPenaltyAmount"] == {
            "@value": "500", "@type": "xsd:decimal",
        }
        assert node["estleg:maxPenaltyUnit"] == "daily_rates"


class TestStructuredPenaltySHACL:
    """``estleg:SanctionShape`` accepts both a Sanction carrying the
    structured penalty fields and one carrying only the free-text
    display string; and it rejects an out-of-set unit value."""

    def _shapes_graph(self):
        rdflib = pytest.importorskip("rdflib")
        repo_root = Path(__file__).resolve().parents[1]
        g = rdflib.Graph()
        g.parse(
            str(repo_root / "shacl" / "estonian_legal_shapes.ttl"),
            format="turtle",
        )
        return g

    def _validate(self, data_jsonld):
        pyshacl = pytest.importorskip("pyshacl")
        rdflib = pytest.importorskip("rdflib")
        data = rdflib.Graph()
        data.parse(data=json.dumps(data_jsonld), format="json-ld")
        conforms, _, msg = pyshacl.validate(
            data, shacl_graph=self._shapes_graph(),
            inference="rdfs", abort_on_first=False,
        )
        return conforms, msg

    _CTX = {
        "estleg": "https://w3id.org/estleg/",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "owl": "http://www.w3.org/2002/07/owl#",
    }

    def test_structured_sanction_validates(self):
        conforms, msg = self._validate({
            "@context": self._CTX,
            "@graph": [{
                "@id": "estleg:Sanction_TEST_struct",
                "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                "rdfs:label": "Fine, max 15000 EUR (test)",
                "estleg:sanctionType": "fine",
                "estleg:applicableProvision": {"@id": "estleg:Test_Par_1"},
                "estleg:maxPenalty": "15000 EUR",
                "estleg:maxPenaltyAmount": {
                    "@value": "15000", "@type": "xsd:decimal",
                },
                "estleg:maxPenaltyUnit": "monetary",
                "estleg:maxPenaltyCurrency": "EUR",
            }],
        })
        assert conforms, f"structured Sanction should validate:\n{msg}"

    def test_structured_imprisonment_range_validates(self):
        conforms, msg = self._validate({
            "@context": self._CTX,
            "@graph": [{
                "@id": "estleg:Sanction_TEST_range",
                "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                "rdfs:label": "Imprisonment, 3 years to 12 years (test)",
                "estleg:sanctionType": "imprisonment",
                "estleg:applicableProvision": {"@id": "estleg:Test_Par_2"},
                "estleg:maxPenalty": "12 years",
                "estleg:minPenalty": "3 years",
                "estleg:maxPenaltyAmount": {
                    "@value": "12", "@type": "xsd:decimal",
                },
                "estleg:maxPenaltyUnit": "years",
                "estleg:minPenaltyAmount": {
                    "@value": "3", "@type": "xsd:decimal",
                },
                "estleg:minPenaltyUnit": "years",
                "estleg:isStatutoryDefault": False,
            }],
        })
        assert conforms, f"structured range Sanction should validate:\n{msg}"

    def test_free_text_only_sanction_still_validates(self):
        conforms, msg = self._validate({
            "@context": self._CTX,
            "@graph": [{
                "@id": "estleg:Sanction_TEST_freetext",
                "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                "rdfs:label": "Imprisonment, life (test)",
                "estleg:sanctionType": "imprisonment",
                "estleg:applicableProvision": {"@id": "estleg:Test_Par_3"},
                # Only the free-text display string — no structured trio,
                # mirroring the unparseable "life" case.
                "estleg:maxPenalty": "life",
            }],
        })
        assert conforms, (
            f"free-text-only Sanction should still validate:\n{msg}"
        )

    def test_out_of_set_unit_value_rejected(self):
        conforms, msg = self._validate({
            "@context": self._CTX,
            "@graph": [{
                "@id": "estleg:Sanction_TEST_badunit",
                "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                "rdfs:label": "Fine, bad unit (test)",
                "estleg:sanctionType": "fine",
                "estleg:applicableProvision": {"@id": "estleg:Test_Par_4"},
                "estleg:maxPenaltyAmount": {
                    "@value": "12", "@type": "xsd:decimal",
                },
                # "weeks" is not in the controlled sh:in (...) set.
                "estleg:maxPenaltyUnit": "weeks",
            }],
        })
        assert not conforms, (
            "Sanction with an out-of-set maxPenaltyUnit must NOT validate"
        )

    def test_shape_pins_structured_property_paths(self):
        """Direct shapes-graph assertion: SanctionShape has property
        shapes for the new structured-penalty paths (SHACL allows extra
        properties by default, so a passing instance alone wouldn't pin
        the shape change)."""
        rdflib = pytest.importorskip("rdflib")
        SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        ESTLEG = rdflib.Namespace("https://w3id.org/estleg/")
        g = self._shapes_graph()
        shape = ESTLEG.SanctionShape
        paths = {
            str(g.value(pshape, SH.path))
            for pshape in g.objects(shape, SH["property"])
        }
        for prop in ("maxPenaltyAmount", "minPenaltyAmount",
                     "maxPenaltyUnit", "minPenaltyUnit",
                     "maxPenaltyCurrency", "minPenaltyCurrency"):
            assert str(ESTLEG[prop]) in paths, (
                f"SanctionShape must declare a property shape for "
                f"estleg:{prop}; found paths: {sorted(paths)}"
            )


class TestStructuredPenaltyVocabulary:
    """The 6 structured-penalty DatatypeProperties are defined in the
    controlled vocabulary so ``validate_all.py``'s vocabulary-coverage
    gate accepts the regenerated sanctions corpus."""

    def test_new_properties_defined_in_controlled_vocab(self):
        repo_root = Path(__file__).resolve().parents[1]
        vocab = json.load(
            open(repo_root / "krr_outputs" / "controlled_vocabulary.jsonld")
        )
        ids = {n["@id"] for n in vocab.get("@graph", []) if isinstance(n, dict)}
        for prop in ("estleg:maxPenaltyAmount", "estleg:minPenaltyAmount",
                     "estleg:maxPenaltyUnit", "estleg:minPenaltyUnit",
                     "estleg:maxPenaltyCurrency", "estleg:minPenaltyCurrency",
                     "estleg:isStatutoryDefault"):
            assert prop in ids, f"{prop} must be defined in controlled_vocabulary"

    def test_new_properties_typed_as_datatype_property(self):
        repo_root = Path(__file__).resolve().parents[1]
        vocab = json.load(
            open(repo_root / "krr_outputs" / "controlled_vocabulary.jsonld")
        )
        by_id = {n["@id"]: n for n in vocab.get("@graph", [])
                 if isinstance(n, dict)}
        amount_props = ("estleg:maxPenaltyAmount", "estleg:minPenaltyAmount")
        for prop in amount_props:
            node = by_id[prop]
            types = node["@type"]
            types = types if isinstance(types, list) else [types]
            assert "owl:DatatypeProperty" in types
            assert node.get("rdfs:range", {}).get("@id") == "xsd:decimal"
        for prop in ("estleg:maxPenaltyUnit", "estleg:minPenaltyUnit",
                     "estleg:maxPenaltyCurrency", "estleg:minPenaltyCurrency"):
            node = by_id[prop]
            assert node.get("rdfs:range", {}).get("@id") == "xsd:string"


# --------------------------------------------------------------------- #
# Regression tests for issue #302 — doubled "§ §" in sanction labels.
# --------------------------------------------------------------------- #


class TestProvisionRefStripsSectionSign:
    """``estleg:paragrahv`` is stored WITH the section sign (e.g.
    ``"§ 53."``); the label template prepends another ``§``. The
    leading sign must be stripped so the reference is ``"AS § 53."``,
    not the doubled ``"AS § § 53."``.
    """

    def test_paragrahv_with_section_sign_not_doubled(self):
        from extract_sanctions import _provision_ref
        ref = _provision_ref({
            "estleg:paragrahv": "§ 53.",
            "estleg:lawAbbreviation": "AS",
        })
        assert ref == "AS § 53."
        assert "§ §" not in ref

    def test_paragrahv_with_section_sign_and_extra_space(self):
        from extract_sanctions import _provision_ref
        ref = _provision_ref({
            "estleg:paragrahv": "§   8.",
            "estleg:lawAbbreviation": "Reg",
        })
        assert ref == "Reg § 8."
        assert "§ §" not in ref

    def test_paragrahv_without_section_sign_unchanged(self):
        """A bare numeric paragrahv (no leading sign) is unaffected —
        backward-compat with the existing _provision_ref tests."""
        from extract_sanctions import _provision_ref
        ref = _provision_ref({
            "estleg:paragrahv": "121",
            "estleg:lawAbbreviation": "KarS",
        })
        assert ref == "KarS § 121"
        assert "§ §" not in ref

    def test_build_label_has_no_double_section_sign(self):
        """End-to-end: the rdfs:label built from a ``"§ N"`` paragrahv
        carries a single section sign (issue #302's user-visible
        symptom: ``"Arrest, max 30 days (AS § § 53.)"``)."""
        from extract_sanctions import _build_label, _provision_ref
        ref = _provision_ref({
            "estleg:paragrahv": "§ 53.",
            "estleg:lawAbbreviation": "AS",
        })
        label = _build_label("arrest", {"max_penalty": "30 days"}, ref)
        assert label == "Arrest, max 30 days (AS § 53.)"
        assert "§ §" not in label


# --------------------------------------------------------------------- #
# Regression tests for issue #301 — karistatakse fallback capturing
# citation years / cross-reference paragraph numbers as imprisonment.
# --------------------------------------------------------------------- #


class TestKaristatakseFallbackScoped:
    """The ``karistatakse ... aastase vangistus`` digit fallback used a
    greedy ``.*?`` that crossed sentences and grabbed citation years
    (RT I 2002 …) or cross-ref paragraph numbers (§-s 118) as the
    imprisonment maximum. It now requires ``kuni`` adjacent to the
    number, stays within the sentence, and discards values > 25 years
    (KarS §45 ceiling).
    """

    def test_citation_year_not_captured_as_imprisonment(self):
        from extract_sanctions import extract_imprisonment
        # "2002" is a citation year; no plausible "kuni 2002 aastase".
        text = ("Karistatakse 2002 kohaselt rahatrahvi või "
                "aastase vangistusega.")
        results = extract_imprisonment(text)
        assert not any(
            r.get("max_penalty") == "2002 years" for r in results
        ), results

    def test_cross_ref_paragraph_number_not_captured(self):
        from extract_sanctions import extract_imprisonment
        text = ("Karistatakse käesoleva seadustiku §-s 118 "
                "sätestatud aastase vangistusega.")
        results = extract_imprisonment(text)
        assert not any(
            r.get("max_penalty") == "118 years" for r in results
        ), results

    def test_bogus_year_does_not_add_second_node(self):
        """The real value comes from the word form; the citation year
        must NOT be appended as an extra imprisonment_2 record."""
        from extract_sanctions import extract_imprisonment
        text = ("RT I 2002, 56, 350 redaktsioonis karistatakse seda "
                "tegu, mis on 2002 aastast kehtinud, kuni "
                "kolmeaastase vangistusega.")
        results = extract_imprisonment(text)
        maxes = {r.get("max_penalty") for r in results}
        assert "3 years" in maxes
        assert "2002 years" not in maxes, results

    def test_fallback_requires_adjacent_kuni(self):
        """The greedy ``karistatakse <citation> ... aastase vangistus``
        shape (no ``kuni`` before the number) used to emit the citation
        digit as the imprisonment max. The ``kuni``-adjacency guard now
        suppresses it — and the digit max-only branch can't fire either
        (it also requires ``kuni``), so the result is empty. This
        isolates the fallback guard from the unguarded digit max-only
        branch (which legitimately handles ``kuni N aastase``)."""
        from extract_sanctions import extract_imprisonment
        text = "Karistatakse 1995 redaktsiooni alusel aastase vangistusega."
        results = extract_imprisonment(text)
        assert not any(
            r.get("max_penalty") == "1995 years" for r in results
        ), results
        assert results == [], results

    def test_fallback_plausibility_cap_present(self):
        """Defense-in-depth: the fallback also discards values > 25
        (KarS §45 ceiling). Verified at the regex+guard level so the cap
        is pinned even though the shared digit max-only branch covers the
        same surface form. A 4-digit ``kuni`` value reachable only via the
        greedy fallback span is dropped."""
        import re
        from extract_sanctions import _ESTONIAN_NUMBERS  # noqa: F401
        # Re-derive the exact guarded fallback contract: kuni-adjacent
        # digit, sentence-bounded, value <= 25.
        pat = (r"karistatakse[^.]*?\bkuni\s+(\d+)\s*[-\s]*"
               r"aasta(?:se)?\s+vangistus")
        text = ("karistatakse 2002 aasta redaktsiooni järgi kuni 2002 "
                "aastase vangistusega")
        captured = [m.group(1) for m in re.finditer(pat, text, re.IGNORECASE)]
        kept = [v for v in captured if int(v) <= 25]
        assert "2002" in captured  # the greedy span can reach it
        assert kept == []          # ...but the >25 cap discards it

    def test_legitimate_digit_kuni_still_captured(self):
        """A genuine ``karistatakse ... kuni N aastase vangistusega``
        (N <= 25) is still captured."""
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment(
            "Selle eest karistatakse rahatrahvi või kuni 12 "
            "aastase vangistusega."
        )
        assert any(r.get("max_penalty") == "12 years" for r in results)


# --------------------------------------------------------------------- #
# Regression tests for issue #320 / #372 — split & hyphenated numeral
# imprisonment surface forms.
# --------------------------------------------------------------------- #


class TestImprisonmentSplitAndHyphenatedNumerals:
    """Word-numeral imprisonment patterns must tolerate an optional
    space (#320: "kümne aastase", "kaheksateist aastase") and an
    optional hyphen (#372: "kahe- kuni kaheksa-aastase") between the
    numeral and ``aasta``.
    """

    def test_split_teen_numeral_max(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("kuni kaheksateist aastase vangistusega")
        assert any(r.get("max_penalty") == "18 years" for r in results), results

    def test_split_round_numeral_max(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("kuni kümne aastase vangistusega")
        assert any(r.get("max_penalty") == "10 years" for r in results), results

    def test_hyphenated_numeral_range(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("kahe- kuni kaheksa-aastase vangistusega")
        assert any(
            r.get("min_penalty") == "2 years"
            and r.get("max_penalty") == "8 years"
            for r in results
        ), results

    def test_compound_numeral_still_matches(self):
        """Regression: the original compound form (no space/hyphen)
        must keep matching."""
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("kuni viieaastase vangistusega")
        assert any(r.get("max_penalty") == "5 years" for r in results), results

    def test_compound_range_still_matches(self):
        from extract_sanctions import extract_imprisonment
        results = extract_imprisonment("kahe kuni viieaastase vangistusega")
        assert any(
            r.get("min_penalty") == "2 years"
            and r.get("max_penalty") == "5 years"
            for r in results
        ), results


# --------------------------------------------------------------------- #
# Regression tests for issue #372 — euro-fine surface forms + mõjutustrahv.
# --------------------------------------------------------------------- #


class TestFineEurosBroadened:
    """``extract_fine_euros`` must match genitive ``rahatrahvi kuni N
    eurot``, intervening-word ``suuruses kuni N eurot``, and dash
    ranges ``rahatrahv 64–16 000 eurot``.
    """

    def test_genitive_rahatrahvi_kuni(self):
        from extract_sanctions import extract_fine_euros
        results = extract_fine_euros("rahatrahvi kuni 32 000 eurot")
        assert any(r.get("max_penalty") == "32000 EUR" for r in results), results

    def test_suuruses_kuni_with_intervening_words(self):
        from extract_sanctions import extract_fine_euros
        results = extract_fine_euros(
            "Rahatrahv määratakse suuruses kuni 5000 eurot"
        )
        assert any(r.get("max_penalty") == "5000 EUR" for r in results), results

    def test_dash_range_min_and_max(self):
        from extract_sanctions import extract_fine_euros
        results = extract_fine_euros("rahatrahv 64–16 000 eurot")
        assert any(
            r.get("min_penalty") == "64 EUR"
            and r.get("max_penalty") == "16000 EUR"
            for r in results
        ), results

    def test_dash_range_does_not_also_emit_single_value(self):
        """The dash branch must not additionally emit a lone-number
        record for the same phrase."""
        from extract_sanctions import extract_fine_euros
        results = extract_fine_euros("rahatrahv 64–16 000 eurot")
        assert len(results) == 1, results

    def test_plain_rahatrahviga_still_matches(self):
        """Regression: the original ``rahatrahv(iga) N eurot`` form must
        keep matching (the broadened ``\\w{0,3}`` covers ``-iga``)."""
        from extract_sanctions import extract_fine_euros
        results = extract_fine_euros("rahatrahviga 800 eurot")
        assert any(r.get("max_penalty") == "800 EUR" for r in results), results

    def test_kuni_max_still_matches(self):
        from extract_sanctions import extract_fine_euros
        results = extract_fine_euros("rahatrahv kuni 1200 eurot")
        assert any(r.get("max_penalty") == "1200 EUR" for r in results), results


class TestMojutustrahvExtractor:
    """``mõjutustrahv`` (simplified-procedure fine) must be matched by a
    dedicated extractor wired into ``ALL_EXTRACTORS``.
    """

    def test_mojutustrahv_in_all_extractors(self):
        from extract_sanctions import ALL_EXTRACTORS, extract_mojutustrahv
        assert extract_mojutustrahv in ALL_EXTRACTORS

    def test_mojutustrahv_with_amount(self):
        from extract_sanctions import extract_mojutustrahv
        results = extract_mojutustrahv("mõjutustrahv suurusega 40 eurot")
        assert any(
            r.get("sanction_type") == "fine"
            and r.get("max_penalty") == "40 EUR"
            for r in results
        ), results

    def test_mojutustrahv_kuni_amount(self):
        from extract_sanctions import extract_mojutustrahv
        results = extract_mojutustrahv("mõjutustrahv kuni 1200 eurot")
        assert any(r.get("max_penalty") == "1200 EUR" for r in results), results

    def test_mojutustrahv_bare_mention(self):
        from extract_sanctions import extract_mojutustrahv
        results = extract_mojutustrahv("kohaldatakse mõjutustrahvi")
        assert results
        assert results[0].get("sanction_type") == "fine"
        assert "max_penalty" not in results[0]

    def test_mojutustrahv_via_extract_sanctions_pipeline(self):
        """``extract_sanctions`` (the full dispatcher) emits the
        mõjutustrahv fine record."""
        from extract_sanctions import extract_sanctions
        results = extract_sanctions("mõjutustrahv suurusega 40 eurot")
        assert any(
            r.get("sanction_type") == "fine"
            and r.get("max_penalty") == "40 EUR"
            for r in results
        ), results


# --------------------------------------------------------------------- #
# Regression tests for issue #358 — quantified sunniraha amounts.
# --------------------------------------------------------------------- #


class TestCoerciveBoilerplate:
    """``extract_coercive`` must bridge the asendustäitmise boilerplate
    between ``sunniraha`` and ``kuni N eurot`` so the quantified amount
    (incl. the 20,000,000 EUR GDPR ceiling) is captured rather than
    falling through to a bare unquantified mention.
    """

    def test_asendustaitmise_boilerplate_captures_amount(self):
        from extract_sanctions import extract_coercive
        text = ("sunniraha asendustäitmise ja sunniraha seaduses "
                "sätestatud korras kuni 15 000 eurot")
        results = extract_coercive(text)
        assert any(
            r.get("sanction_type") == "coercive_payment"
            and r.get("max_penalty") == "15000 EUR"
            for r in results
        ), results

    def test_gdpr_ceiling_20m_captured(self):
        from extract_sanctions import extract_coercive
        text = ("kohaldada sunniraha asendustäitmise ja sunniraha "
                "seaduses sätestatud korras kuni 20 000 000 eurot")
        results = extract_coercive(text)
        assert any(r.get("max_penalty") == "20000000 EUR" for r in results), results

    def test_tight_form_still_matches(self):
        """Regression: the original tight ``sunniraha kuni N eurot``
        form must keep matching."""
        from extract_sanctions import extract_coercive
        results = extract_coercive("sunniraha kuni 6400 eurot")
        assert any(r.get("max_penalty") == "6400 EUR" for r in results), results

    def test_bare_mention_without_amount_unchanged(self):
        """A sunniraha mention with no euro amount still yields a bare
        coercive_payment record (no max_penalty)."""
        from extract_sanctions import extract_coercive
        results = extract_coercive("Kohus võib määrata sunniraha.")
        assert results
        assert results[0].get("sanction_type") == "coercive_payment"
        assert "max_penalty" not in results[0]

    def test_no_double_emission_across_sentence_boundary(self):
        """The 150-char window stays within the sentence — a sunniraha
        in one sentence does not bind to a ``kuni N eurot`` in the
        next."""
        from extract_sanctions import extract_coercive
        text = ("Kohus määrab sunniraha. Eraldi sätestatakse kuni "
                "9999 eurot muudel alustel.")
        results = extract_coercive(text)
        # Bare mention only — the cross-sentence amount is not bound.
        assert all(r.get("max_penalty") != "9999 EUR" for r in results), results


# --------------------------------------------------------------------- #
# Regression tests for issue #393 — arrest condition-clause false
# positive + severity-index monetary exposure.
# --------------------------------------------------------------------- #


class TestArrestConditionClauseNotImposed:
    """The arrest statutory-default fallback must NOT fire when the
    ``arest`` token is the object of a past-passive condition verb
    (kohaldatud|rakendatud|määratud) within ~10 tokens — that's a
    precondition (KarS §331¹), not an imposed sanction.
    """

    def test_kohaldatud_aresti_condition_no_arrest(self):
        from extract_sanctions import extract_arrest
        # KarS §331¹ shape: aresti is the object of "kohaldatud".
        text = ("Kui isiku suhtes on selle eest täitemenetluses "
                "kohaldatud aresti, – karistatakse rahalise karistuse "
                "või kuni kaheaastase vangistusega.")
        results = extract_arrest(text)
        assert results == [], results

    def test_rakendatud_aresti_condition_no_arrest(self):
        from extract_sanctions import extract_arrest
        text = ("Kui on rakendatud aresti, karistatakse "
                "vangistusega.")
        assert extract_arrest(text) == []

    def test_maaratud_aresti_condition_no_arrest(self):
        from extract_sanctions import extract_arrest
        text = ("Kui on määratud aresti, kohaldatakse "
                "rahatrahvi.")
        assert extract_arrest(text) == []

    def test_genuine_imposed_arrest_still_fires(self):
        """A real ``karistatakse ... arestiga`` (no condition verb
        before the token) still yields the 30-day statutory default."""
        from extract_sanctions import extract_arrest
        results = extract_arrest(
            "Selle eest karistatakse rahatrahviga või arestiga."
        )
        assert any(
            r.get("max_penalty") == "30 days"
            and r.get("is_statutory_default") is True
            for r in results
        ), results

    def test_explicit_day_count_unaffected_by_condition_gate(self):
        """An explicit day count is read by the earlier branch and is
        never gated by the condition-clause check."""
        from extract_sanctions import extract_arrest
        results = extract_arrest("Karistatakse aresti kuni 15 päeva.")
        assert any(r.get("max_penalty") == "15 days" for r in results)

    def test_condition_verb_far_away_does_not_suppress(self):
        """A past-passive verb more than ~10 tokens before the arrest
        token does not suppress a genuine imposed arrest."""
        from extract_sanctions import extract_arrest
        text = ("Kui kohtu poolt on kohaldatud rahatrahvi ja samuti "
                "mitmeid muid haldusmeetmeid ning lisaks veel teisi "
                "menetlustoiminguid erinevatel alustel, siis "
                "karistatakse arestiga.")
        results = extract_arrest(text)
        assert any(
            r.get("max_penalty") == "30 days" for r in results
        ), results


class TestSeverityIndexMonetaryExposure:
    """The severity index additionally exposes ``max_monetary_eur`` and a
    normalized ``monetary_score`` alongside ``max_severity`` so consumers
    can rank within the fine tier. The legal type ordering is unchanged.
    """

    def test_monetary_amount_helper_reads_monetary_unit_only(self):
        from decimal import Decimal
        from extract_sanctions import _monetary_amount_eur
        monetary = {
            "estleg:maxPenaltyUnit": "monetary",
            "estleg:maxPenaltyAmount": {"@value": "20000000",
                                        "@type": "xsd:decimal"},
        }
        assert _monetary_amount_eur(monetary) == Decimal("20000000")
        # Non-monetary units return None.
        years = {
            "estleg:maxPenaltyUnit": "years",
            "estleg:maxPenaltyAmount": {"@value": "5"},
        }
        assert _monetary_amount_eur(years) is None
        # Missing structured amount returns None.
        assert _monetary_amount_eur(
            {"estleg:sanctionType": "imprisonment"}
        ) is None

    def test_monetary_score_monotonic_and_bounded(self):
        from decimal import Decimal
        from extract_sanctions import _monetary_score
        assert _monetary_score(None) == 0.0
        s64 = _monetary_score(Decimal("64"))
        s_mid = _monetary_score(Decimal("32000"))
        s_max = _monetary_score(Decimal("20000000"))
        assert 0.0 < s64 < s_mid < s_max
        assert s_max == 1.0
        assert 0.0 <= s64 <= 1.0

    def test_severity_index_entry_has_monetary_fields(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: a law with a large euro fine gets a populated
        ``max_monetary_eur``/``monetary_score`` in the report's
        severity_index; the type ordinal is still exposed."""
        import extract_sanctions as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        reports_dir = krr / "reports" / "kov"
        sanctions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        peep = krr / "bigfine_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:BF_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:BF_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Rikkumise eest määratakse rahatrahvi kuni "
                     "32 000 eurot."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        report = json.load(open(krr / "reports" / "sanctions_report.json"))
        entry = next(
            e for e in report["severity_index"] if e["law"] == "bigfine"
        )
        # Type ordinal unchanged (fine == 2).
        assert entry["max_severity"] == 2
        assert entry["max_severity_type"] == "fine"
        # New additive fields populated.
        assert entry["max_monetary_eur"] == 32000.0
        assert 0.0 < entry["monetary_score"] <= 1.0

    def test_non_monetary_law_has_null_max_monetary(
        self, tmp_path, monkeypatch
    ):
        """A law whose only sanction is imprisonment exposes
        ``max_monetary_eur == None`` and ``monetary_score == 0.0`` —
        the field is additive and does not perturb the type ordering."""
        import extract_sanctions as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        reports_dir = krr / "reports" / "kov"
        sanctions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        peep = krr / "imponly_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:IO_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:IO_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Karistatakse kuni viieaastase vangistusega."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        report = json.load(open(krr / "reports" / "sanctions_report.json"))
        entry = next(
            e for e in report["severity_index"] if e["law"] == "imponly"
        )
        assert entry["max_severity"] == 5  # imprisonment
        assert entry["max_monetary_eur"] is None
        assert entry["monetary_score"] == 0.0


# --------------------------------------------------------------------- #
# Regression tests for issue #376 — atomic save_json.
# --------------------------------------------------------------------- #


class TestAtomicSaveJson:
    """The local non-atomic ``save_json`` (bare ``open(w)``) is replaced
    by the atomic ``estleg_common.save_json`` (tempfile + os.replace).
    """

    def test_save_json_is_estleg_common_atomic(self):
        import extract_sanctions as mod
        import estleg_common
        # The module-level name now resolves to the shared atomic impl.
        assert mod.save_json is estleg_common.save_json
        assert mod.save_json.__module__ == "estleg_common"

    def test_save_json_writes_via_atomic_replace(self, tmp_path):
        """A successful write produces the expected JSON with a trailing
        newline and leaves no ``.tmp`` droppings."""
        import extract_sanctions as mod
        out = tmp_path / "doc.json"
        mod.save_json(out, {"@graph": [{"@id": "estleg:X"}]})
        text = out.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert json.loads(text) == {"@graph": [{"@id": "estleg:X"}]}
        # No leftover tempfiles in the directory.
        assert [p.name for p in tmp_path.iterdir()] == ["doc.json"]

    def test_save_json_no_partial_file_on_serialization_error(self, tmp_path):
        """If serialization fails mid-write, no partial/zero-byte file
        is left at the target path (the atomic contract). A set is not
        JSON-serialisable, so json.dump raises."""
        import extract_sanctions as mod
        out = tmp_path / "doc.json"
        with pytest.raises(TypeError):
            mod.save_json(out, {"bad": {1, 2, 3}})
        # Target path was never created (replace only happens on success).
        assert not out.exists()
        # And no tempfile droppings remain.
        assert list(tmp_path.iterdir()) == []

    def test_save_json_monkeypatch_still_works(self, monkeypatch):
        """The existing test pattern ``monkeypatch.setattr(mod,
        'save_json', ...)`` must still intercept calls (the gate-fail
        coverage test relies on this)."""
        import extract_sanctions as mod
        calls = []
        monkeypatch.setattr(mod, "save_json", lambda fp, doc: calls.append(fp))
        mod.save_json(Path("/nonexistent/x.json"), {"a": 1})
        assert calls == [Path("/nonexistent/x.json")]


class TestIssue602AmountRobustness:
    """#602 — euro-amount captures must never store a value containing a
    newline/tab (a window that bridged two numbers), and a comma-decimal
    sunniraha capture must not be inflated 100x by stripping the comma."""

    def test_clean_euro_amount_rejects_newline_span(self):
        from extract_sanctions import _clean_euro_amount

        assert _clean_euro_amount("500\n2024") is None
        assert _clean_euro_amount("500\t2024") is None

    def test_clean_euro_amount_strips_space_and_nbsp(self):
        from extract_sanctions import _clean_euro_amount

        assert _clean_euro_amount("16 000") == "16000"
        assert _clean_euro_amount("16 000") == "16000"
        assert _clean_euro_amount("32") == "32"

    def test_kuni_fine_does_not_store_newline_spanning_amount(self):
        from extract_sanctions import extract_fine_euros

        # The window bridges a line break; the result must not be a junk
        # "500\n2024 EUR" record. (No valid same-line amount → no fine.)
        out = extract_fine_euros("rahatrahv kuni 500\n2024 eurot")
        assert all("\n" not in r.get("max_penalty", "") for r in out)
        assert all("\t" not in r.get("max_penalty", "") for r in out)

    def test_bare_fine_does_not_store_newline_spanning_amount(self):
        from extract_sanctions import extract_fine_euros

        out = extract_fine_euros("rahatrahv 500\n2024 eurot")
        assert all("\n" not in r.get("max_penalty", "") for r in out)

    def test_valid_fine_amounts_still_extracted(self):
        from extract_sanctions import extract_fine_euros

        assert extract_fine_euros("rahatrahvi kuni 1200 eurot") == [
            {"sanction_type": "fine", "max_penalty": "1200 EUR"}
        ]
        assert extract_fine_euros("rahatrahv kuni 16 000 eurot") == [
            {"sanction_type": "fine", "max_penalty": "16000 EUR"}
        ]

    def test_dash_range_still_extracted(self):
        from extract_sanctions import extract_fine_euros

        out = extract_fine_euros("rahatrahv 64–16 000 eurot")
        assert {"sanction_type": "fine", "max_penalty": "16000 EUR",
                "min_penalty": "64 EUR"} in out

    def test_sunniraha_comma_decimal_is_rejected_not_inflated(self):
        from extract_sanctions import extract_coercive

        out = extract_coercive("sunniraha kuni 1 000,50 eurot")
        # The pre-fix bug stored "100050 EUR" (comma stripped → 100x). The
        # amount must NOT appear; only a bare coercive mention is acceptable.
        amounts = [r.get("max_penalty") for r in out if r.get("max_penalty")]
        assert "100050 EUR" not in amounts
        assert amounts == []

    def test_sunniraha_integral_comma_decimal_kept_as_integer(self):
        from extract_sanctions import extract_coercive

        out = extract_coercive("sunniraha kuni 1 000,00 eurot")
        assert {"sanction_type": "coercive_payment",
                "max_penalty": "1000 EUR"} in out

    def test_sunniraha_plain_thousands_still_extracted(self):
        from extract_sanctions import extract_coercive

        out = extract_coercive("sunniraha kuni 20 000 000 eurot")
        assert {"sanction_type": "coercive_payment",
                "max_penalty": "20000000 EUR"} in out

    def test_clean_decimal_helper(self):
        from extract_sanctions import _clean_decimal_euro_amount

        assert _clean_decimal_euro_amount("1000,50") is None
        assert _clean_decimal_euro_amount("1000,00") == "1000"
        assert _clean_decimal_euro_amount("1 000") == "1000"
        assert _clean_decimal_euro_amount("500\n2024") is None
