"""Tests for scripts/rederive_riigikohus_case_types.py (#579).

Legacy 1993-1996 Riigikohus decisions dumped as ``CaseType_Other`` are
re-derived from the deciding-chamber token in their ``estleg:legalText``.
Also covers the matching ``classify_case`` patch in
``generate_court_decisions.py`` so a regen no longer re-dumps the
unambiguous Roman-numeral Criminal/Civil cases.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import rederive_riigikohus_case_types as red
from estleg.generate_court_decisions import classify_case

CRIMINAL = "estleg:CaseType_Criminal"
CIVIL = "estleg:CaseType_Civil"
ADMIN = "estleg:CaseType_Administrative"
CONST = "estleg:CaseType_ConstitutionalReview"
OTHER = "estleg:CaseType_Other"


def _other_node(case_nr: str, legal_text: str = "") -> dict:
    node: dict = {
        "@id": f"estleg:RK_{case_nr.replace('/', '_').replace('-', '_')}",
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "estleg:caseNumber": case_nr,
        "estleg:caseType": {"@id": OTHER},
    }
    if legal_text:
        node["estleg:legalText"] = legal_text
    return node


class TestDeriveCaseType:
    def test_criminal_chamber(self):
        txt = "K O H T U O T S U S Eesti Vabariigi Riigikohtu kriminaalkolleegium koosseisus"
        assert red.derive_case_type(txt) == CRIMINAL

    def test_civil_chamber(self):
        txt = "Tsiviilasi nr. III-2/3-9/93 EV Riigikohtu tsiviilkolleegium koosseisus"
        assert red.derive_case_type(txt) == CIVIL

    def test_administrative_chamber(self):
        txt = "M Ä Ä R U S Riigikohtu halduskolleegium koosseisus"
        assert red.derive_case_type(txt) == ADMIN

    def test_constitutional_review_chamber(self):
        txt = "OTSUS Riigikohtu põhiseaduslikkuse järelevalve kolleegium"
        assert red.derive_case_type(txt) == CONST

    def test_ocr_typo_kollegium_single_e(self):
        # 1994 OCR drops a letter: "kriminaalkollegium".
        txt = "K O H T U O T S U S Riigikohtu kriminaalkollegium kooseisus"
        assert red.derive_case_type(txt) == CRIMINAL

    def test_body_cross_reference_does_not_retype(self):
        # A civil judgment that *cites* a separate criminal case in the body
        # must classify as Civil (chamber), not Criminal (the cross-ref).
        txt = (
            "III-2/1-104/95 M Ä Ä R U S Tartus, 21. detsembril 1995. a. "
            "Tsiviilkolleegium koosseisus: eesistuja J. Luik ... "
            + "x" * 300
            + " Kriminaalasi nr. 94740005 on peatatud teadmata ajaks"
        )
        assert red.derive_case_type(txt) == CIVIL

    def test_head_declaration_tiebreak_when_no_chamber(self):
        # No "...kolleegium" token, but an explicit head declaration.
        txt = "K O H T U O T S U S Eesti Vabariigi nimel Tsiviilasi nr. III-2/3-1/93"
        assert red.derive_case_type(txt) == CIVIL

    def test_ambiguous_multiple_chambers_returns_none(self):
        txt = "halduskolleegium ... tsiviilkolleegium ... arutas"
        assert red.derive_case_type(txt) is None

    def test_empty_text_returns_none(self):
        assert red.derive_case_type("") is None

    def test_no_signal_returns_none(self):
        assert red.derive_case_type("OTSUS Eesti Vabariigi nimel Tartus 1994") is None


class TestRetypeNode:
    def test_retypes_other_with_signal(self):
        node = _other_node("III-1/3-1/93", "Riigikohtu kriminaalkolleegium koosseisus")
        assert red.retype_node(node) == CRIMINAL
        assert node["estleg:caseType"] == {"@id": CRIMINAL}

    def test_non_other_node_untouched(self):
        node = _other_node("III-1/3-1/93", "Riigikohtu kriminaalkolleegium")
        node["estleg:caseType"] = {"@id": CIVIL}  # already a real type
        assert red.retype_node(node) is None
        assert node["estleg:caseType"] == {"@id": CIVIL}

    def test_other_without_signal_stays_other(self):
        node = _other_node("III-4/1-4/93", "OTSUS Tartus 1993")
        assert red.retype_node(node) is None
        assert node["estleg:caseType"] == {"@id": OTHER}

    def test_idempotent(self):
        node = _other_node("III-2/3-9/93", "Riigikohtu tsiviilkolleegium")
        assert red.retype_node(node) == CIVIL
        assert red.retype_node(node) is None  # no longer Other


class TestProcessFile:
    def _write(self, path: Path, graph: list) -> None:
        path.write_text(
            json.dumps({"@context": {"estleg": "x"}, "@graph": graph}),
            encoding="utf-8",
        )

    def test_counts_per_type(self, tmp_path: Path):
        peep = tmp_path / "riigikohus_1994_peep.json"
        self._write(
            peep,
            [
                _other_node("III-1/3-1/94", "Riigikohtu kriminaalkolleegium"),
                _other_node("III-2/3-2/94", "Riigikohtu tsiviilkolleegium"),
                _other_node("III-3/1-3/94", "Riigikohtu halduskolleegium"),
                _other_node("III-4/1-4/94", "no signal here"),  # stays Other
            ],
        )
        changed, per_type = red.process_file(peep, dry_run=False)
        assert changed == 3
        assert per_type == {CRIMINAL: 1, CIVIL: 1, ADMIN: 1}
        types = [
            n["estleg:caseType"]["@id"]
            for n in json.loads(peep.read_text(encoding="utf-8"))["@graph"]
        ]
        assert types.count(OTHER) == 1

    def test_dry_run_does_not_write(self, tmp_path: Path):
        peep = tmp_path / "riigikohus_1994_peep.json"
        self._write(peep, [_other_node("III-1/3-1/94", "kriminaalkolleegium")])
        before = peep.read_text(encoding="utf-8")
        changed, _ = red.process_file(peep, dry_run=True)
        assert changed == 1
        assert peep.read_text(encoding="utf-8") == before

    def test_idempotent_second_pass(self, tmp_path: Path):
        peep = tmp_path / "riigikohus_1994_peep.json"
        self._write(
            peep,
            [
                _other_node("III-1/3-1/94", "kriminaalkolleegium"),
                _other_node("III-2/3-2/94", "tsiviilkolleegium"),
            ],
        )
        assert red.process_file(peep, dry_run=False)[0] == 2
        assert red.process_file(peep, dry_run=False)[0] == 0


class TestLfsPointer:
    def test_pointer_detected(self, tmp_path: Path):
        ptr = tmp_path / "riigikohus_2020_peep.json"
        ptr.write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:abc\n",
            encoding="utf-8",
        )
        assert red.is_lfs_pointer(ptr) is True


class TestGeneratorClassifyCasePatch:
    """The generator patch: Roman III-1/ / III-2/ resolve from the case
    number alone; III-3/ / III-4/ stay Other for the text re-derivation;
    modern and old formats are unaffected."""

    def test_roman_chamber1_criminal(self):
        assert classify_case("III-1/3-1/93")[0] == "Criminal"

    def test_roman_chamber2_civil(self):
        assert classify_case("III-2/3-9/93")[0] == "Civil"

    def test_roman_chamber3_stays_other(self):
        assert classify_case("III-3/1-7/94")[0] == "Other"

    def test_roman_chamber4_stays_other(self):
        assert classify_case("III-4/1-4/93")[0] == "Other"

    def test_old_format_unaffected(self):
        assert classify_case("3-1-1-99")[0] == "Criminal"
        assert classify_case("3-2-2-15-96")[0] == "Civil"

    def test_modern_format_unaffected(self):
        assert classify_case("1-17-1234")[0] == "Criminal"
        # Modern 3-21-... must NOT be read as a Roman/old chamber.
        assert classify_case("3-21-2176/52")[0] == "Administrative"


@pytest.mark.corpus
class TestRealCorpusIdempotent:
    """After the fix, re-running changes nothing and every retyped node's
    caseType is one of the five declared enum individuals (never Other for
    the ones we recovered)."""

    RK_DIR = Path(__file__).resolve().parent.parent / "krr_outputs" / "riigikohus"
    VALID = {CRIMINAL, CIVIL, ADMIN, CONST, "estleg:CaseType_Misdemeanor", OTHER}

    def test_rerun_is_noop_and_types_valid(self):
        for peep in sorted(self.RK_DIR.glob(red.PEEP_GLOB)):
            if red.is_lfs_pointer(peep):
                continue
            changed, _ = red.process_file(peep, dry_run=True)
            assert changed == 0, peep.name
            doc = json.loads(peep.read_text(encoding="utf-8"))
            for node in doc.get("@graph", []):
                if not isinstance(node, dict):
                    continue
                ct = red._case_type_id(node)
                if ct is not None and ct.startswith("estleg:CaseType_"):
                    assert ct in self.VALID, (peep.name, node.get("@id"), ct)
