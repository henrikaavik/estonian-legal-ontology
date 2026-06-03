"""Tests for scripts/rederive_court_case_types.py (#342 caseType re-derivation)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import rederive_court_case_types as rc


def _decision(case_nr: str, case_type_id: str, iso_date: str = "2015-12-21") -> dict:
    return {
        "@id": f"estleg:RK_{case_nr.replace('-', '_')}",
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "estleg:caseNumber": case_nr,
        "estleg:caseType": {"@id": f"estleg:CaseType_{case_type_id}"},
        "estleg:decisionDate": {"@value": iso_date, "@type": "xsd:date"},
    }


def _write_peep(path: Path, nodes: list[dict]) -> None:
    path.write_text(json.dumps({"@context": {"estleg": "x:"}, "@graph": nodes}),
                    encoding="utf-8")


class TestRederiveFile:
    def test_old_format_misclassified_administrative_is_corrected(self, tmp_path):
        peep = tmp_path / "riigikohus_2015_peep.json"
        _write_peep(peep, [
            _decision("3-1-1-100-15", "Administrative"),   # chamber 1 → Criminal
            _decision("3-2-1-144-15", "Administrative"),   # chamber 2 → Civil
            _decision("3-4-1-27-15", "Administrative"),    # chamber 4 → ConstitutionalReview
            _decision("3-3-1-55-15", "Administrative"),    # chamber 3 → stays Administrative
        ])
        changed = rc.rederive_file(peep, dry_run=False)
        assert changed == 3  # the 3-3 one already correct
        out = {n["estleg:caseNumber"]: n["estleg:caseType"]["@id"]
               for n in json.loads(peep.read_text())["@graph"]}
        assert out["3-1-1-100-15"] == "estleg:CaseType_Criminal"
        assert out["3-2-1-144-15"] == "estleg:CaseType_Civil"
        assert out["3-4-1-27-15"] == "estleg:CaseType_ConstitutionalReview"
        assert out["3-3-1-55-15"] == "estleg:CaseType_Administrative"

    def test_idempotent_second_pass_changes_nothing(self, tmp_path):
        peep = tmp_path / "riigikohus_2015_peep.json"
        _write_peep(peep, [_decision("3-1-1-100-15", "Administrative")])
        assert rc.rederive_file(peep, dry_run=False) == 1
        assert rc.rederive_file(peep, dry_run=False) == 0

    def test_dry_run_does_not_write(self, tmp_path):
        peep = tmp_path / "riigikohus_2015_peep.json"
        _write_peep(peep, [_decision("3-1-1-100-15", "Administrative")])
        before = peep.read_text()
        assert rc.rederive_file(peep, dry_run=True) == 1
        assert peep.read_text() == before  # unchanged on disk

    def test_modern_format_preserved(self, tmp_path):
        peep = tmp_path / "riigikohus_2019_peep.json"
        # Modern '1-17-…' → first digit 1 → Criminal; already correct.
        _write_peep(peep, [_decision("1-17-10573/181", "Criminal", "2019-03-04")])
        assert rc.rederive_file(peep, dry_run=False) == 0


class TestNodeToDec:
    def test_iso_value_object_converts_to_ddmmyyyy(self):
        node = {"estleg:caseNumber": "3-1-1-5-02",
                "estleg:decisionDate": {"@value": "2002-04-15", "@type": "xsd:date"}}
        dec = rc._node_to_dec(node)
        assert dec == {"case_nr": "3-1-1-5-02", "date": "15.04.2002"}

    def test_missing_date_yields_empty(self):
        dec = rc._node_to_dec({"estleg:caseNumber": "3-1-1-5-02"})
        assert dec == {"case_nr": "3-1-1-5-02", "date": ""}


class TestRefreshIndex:
    def test_recomputes_counts_keyed_by_short_type_id(self, tmp_path):
        rk = tmp_path / "riigikohus"
        rk.mkdir()
        _write_peep(rk / "riigikohus_2015_peep.json", [
            _decision("3-1-1-100-15", "Administrative"),  # → Criminal
            _decision("3-2-1-144-15", "Administrative"),  # → Civil
        ])
        (rk / "RIIGIKOHUS_INDEX.json").write_text(json.dumps({
            "total_decisions": 2,
            "case_type_counts": {"Administrative": 2},  # stale
            "case_type_counts_per_year": {"2015": {"Administrative": 2}},
        }), encoding="utf-8")
        assert rc.refresh_index(rk, dry_run=False) is True
        idx = json.loads((rk / "RIIGIKOHUS_INDEX.json").read_text())
        assert idx["case_type_counts"] == {"Criminal": 1, "Civil": 1}
        assert idx["case_type_counts_per_year"] == {"2015": {"Criminal": 1, "Civil": 1}}
        assert idx["total_decisions"] == 2  # untouched
        # Idempotent: a second refresh reports no change.
        assert rc.refresh_index(rk, dry_run=False) is False
