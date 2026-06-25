"""Tests for scripts/fix_curia_court_contamination.py (#575).

National (CELEX sector 8) and EFTA (sector E) decisions were mis-stamped as
Court of Justice. The post-process re-points ``estleg:euCourt`` from the CELEX
sector and injects the two sentinel court individuals into the schema.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fix_curia_court_contamination as fix


def _decision(celex: str, court: str = fix.COURT_OF_JUSTICE) -> dict:
    return {
        "@id": f"estleg:EUCJ_{celex.replace('(', '').replace(')', '')}",
        "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
        "estleg:celexNumber": celex,
        "estleg:euCourt": {"@id": court},
    }


class TestCorrectCourt:
    def test_sector8_national_repointed(self):
        node = _decision("82015EE1202(01)")
        assert fix.correct_court(node) is True
        assert node["estleg:euCourt"] == {"@id": fix.NATIONAL_COURT}

    def test_sectorE_efta_repointed(self):
        node = _decision("E2014J0018")
        assert fix.correct_court(node) is True
        assert node["estleg:euCourt"] == {"@id": fix.EFTA_COURT}

    def test_sectorE_application_repointed(self):
        # ESA application (E....P....) is still sector E -> EFTA.
        node = _decision("E2015P0006")
        assert fix.correct_court(node) is True
        assert node["estleg:euCourt"] == {"@id": fix.EFTA_COURT}

    def test_sector6_cjeu_left_untouched(self):
        node = _decision("62016CJ0123")
        assert fix.correct_court(node) is False
        assert node["estleg:euCourt"] == {"@id": fix.COURT_OF_JUSTICE}

    def test_celex_as_value_object(self):
        node = _decision("82008EE0319(01)")
        node["estleg:celexNumber"] = {"@value": "82008EE0319(01)"}
        assert fix.correct_court(node) is True
        assert node["estleg:euCourt"] == {"@id": fix.NATIONAL_COURT}

    def test_idempotent_already_correct(self):
        node = _decision("82015EE1202(01)", court=fix.NATIONAL_COURT)
        assert fix.correct_court(node) is False
        node_e = _decision("E2014J0018", court=fix.EFTA_COURT)
        assert fix.correct_court(node_e) is False

    def test_non_decision_node_skipped(self):
        header = {"@id": "estleg:CURIA_Other_Map_2026", "@type": ["owl:Ontology"]}
        assert fix.correct_court(header) is False

    def test_missing_celex_skipped(self):
        node = {
            "@id": "estleg:EUCJ_x",
            "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
            "estleg:euCourt": {"@id": fix.COURT_OF_JUSTICE},
        }
        assert fix.correct_court(node) is False
        assert node["estleg:euCourt"] == {"@id": fix.COURT_OF_JUSTICE}

    def test_type_as_bare_string(self):
        node = _decision("E2013J0021")
        node["@type"] = "estleg:EUCourtDecision"
        assert fix.correct_court(node) is True
        assert node["estleg:euCourt"] == {"@id": fix.EFTA_COURT}


class TestProcessPeep:
    def _write(self, path: Path, graph: list) -> None:
        path.write_text(
            json.dumps({"@context": {"estleg": "x"}, "@graph": graph}),
            encoding="utf-8",
        )

    def test_counts_and_rewrites(self, tmp_path: Path):
        peep = tmp_path / "curia_other_peep.json"
        self._write(
            peep,
            [
                {"@id": "estleg:CURIA_Other_Map_2026", "@type": ["owl:Ontology"]},
                _decision("82015EE1202(01)"),
                _decision("E2014J0018"),
                _decision("62016CJ0123"),  # untouched
            ],
        )
        changed = fix.process_peep(peep, dry_run=False)
        assert changed == 2
        reread = json.loads(peep.read_text(encoding="utf-8"))["@graph"]
        courts = {
            n["@id"]: n["estleg:euCourt"]["@id"]
            for n in reread
            if "estleg:euCourt" in n
        }
        assert fix.NATIONAL_COURT in courts.values()
        assert fix.EFTA_COURT in courts.values()
        assert fix.COURT_OF_JUSTICE in courts.values()  # the sector-6 one

    def test_dry_run_does_not_write(self, tmp_path: Path):
        peep = tmp_path / "curia_other_peep.json"
        self._write(peep, [_decision("82015EE1202(01)")])
        before = peep.read_text(encoding="utf-8")
        assert fix.process_peep(peep, dry_run=True) == 1
        assert peep.read_text(encoding="utf-8") == before  # unchanged on disk

    def test_idempotent_second_pass(self, tmp_path: Path):
        peep = tmp_path / "curia_other_peep.json"
        self._write(peep, [_decision("82015EE1202(01)"), _decision("E2014J0018")])
        assert fix.process_peep(peep, dry_run=False) == 2
        assert fix.process_peep(peep, dry_run=False) == 0


class TestEnsureSchemaIndividuals:
    def _write_schema(self, path: Path, extra: list | None = None) -> None:
        graph = [{"@id": "estleg:EUCourt_CourtOfJustice", "@type": ["owl:NamedIndividual"]}]
        graph.extend(extra or [])
        path.write_text(
            json.dumps({"@context": {"estleg": "x"}, "@graph": graph}),
            encoding="utf-8",
        )

    def test_injects_both_when_absent(self, tmp_path: Path):
        schema = tmp_path / "curia_schema.json"
        self._write_schema(schema)
        assert fix.ensure_schema_individuals(schema, dry_run=False) == 2
        ids = {
            n["@id"]
            for n in json.loads(schema.read_text(encoding="utf-8"))["@graph"]
        }
        assert fix.NATIONAL_COURT in ids
        assert fix.EFTA_COURT in ids

    def test_idempotent_when_present(self, tmp_path: Path):
        schema = tmp_path / "curia_schema.json"
        self._write_schema(schema)
        assert fix.ensure_schema_individuals(schema, dry_run=False) == 2
        assert fix.ensure_schema_individuals(schema, dry_run=False) == 0

    def test_partial_present_adds_only_missing(self, tmp_path: Path):
        schema = tmp_path / "curia_schema.json"
        self._write_schema(schema, extra=[fix.COURT_INDIVIDUALS[fix.EFTA_COURT]])
        assert fix.ensure_schema_individuals(schema, dry_run=False) == 1
        ids = {
            n["@id"]
            for n in json.loads(schema.read_text(encoding="utf-8"))["@graph"]
        }
        assert fix.NATIONAL_COURT in ids

    def test_dry_run_does_not_write(self, tmp_path: Path):
        schema = tmp_path / "curia_schema.json"
        self._write_schema(schema)
        before = schema.read_text(encoding="utf-8")
        assert fix.ensure_schema_individuals(schema, dry_run=True) == 2
        assert schema.read_text(encoding="utf-8") == before


class TestLfsPointer:
    def test_pointer_detected(self, tmp_path: Path):
        ptr = tmp_path / "curia_combined.jsonld"
        ptr.write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:abc\n",
            encoding="utf-8",
        )
        assert fix.is_lfs_pointer(ptr) is True

    def test_real_json_not_pointer(self, tmp_path: Path):
        real = tmp_path / "curia_other_peep.json"
        real.write_text('{"@graph": []}', encoding="utf-8")
        assert fix.is_lfs_pointer(real) is False


@pytest.mark.corpus
class TestRealCorpusIdempotent:
    """After the fix has been applied, re-running must change nothing and no
    sector-8/E decision may still point at Court of Justice."""

    CURIA_DIR = Path(__file__).resolve().parent.parent / "krr_outputs" / "curia"

    def test_no_contamination_remains_and_rerun_is_noop(self):
        for peep in sorted(self.CURIA_DIR.glob(fix.PEEP_GLOB)):
            if fix.is_lfs_pointer(peep):
                continue
            assert fix.process_peep(peep, dry_run=True) == 0, peep.name
            doc = json.loads(peep.read_text(encoding="utf-8"))
            for node in doc.get("@graph", []):
                if not isinstance(node, dict):
                    continue
                celex = fix._celex_value(node)
                if not celex or celex[0] == "6":
                    continue
                court = node.get(fix.EU_COURT_PREDICATE, {})
                court_id = court.get("@id") if isinstance(court, dict) else court
                assert court_id != fix.COURT_OF_JUSTICE, (peep.name, node.get("@id"))
