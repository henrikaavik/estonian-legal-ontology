"""Issue #683 — Estonian personal ID codes must never reach published court text.

Covers the shared detector/masker in ``estleg.estleg_common``, the offline
backfill pass ``estleg.screen_court_personal_data``, and the release gate
``validate_all.validate_no_personal_codes``.

Every personal code used here is *generated* from a synthetic body by
:func:`synthetic_isikukood`, never copied from a real record: a test fixture
that hardcoded a live isikukood would reintroduce exactly the leak this ticket
closes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from estleg import estleg_common as ec
from estleg import screen_court_personal_data as screener
from estleg import validate_all

# Any isolated 11-digit run, used to assert that reports and screened text
# carry none. Deliberately independent of the production regex.
ANY_11_DIGIT_RUN = re.compile(r"(?<![0-9])[0-9]{11}(?![0-9])")


def synthetic_isikukood(body: str) -> str:
    """Append the correct check digit to a synthetic 10-digit body."""
    assert len(body) == 10 and body.isdigit(), body
    return body + str(ec.isikukood_check_digit(body))


# A structurally valid code: century/sex 3, birth date 1990-01-01, serial 000.
VALID = synthetic_isikukood("3900101000")
# Same body, second synthetic person.
VALID_2 = synthetic_isikukood("4900101001")


# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------

class TestChecksum:
    def test_synthetic_code_validates(self) -> None:
        assert len(VALID) == 11
        assert ec.is_valid_isikukood(VALID)

    def test_every_wrong_check_digit_is_rejected(self) -> None:
        """Only one of the ten possible final digits may pass."""
        accepted = [
            d for d in "0123456789" if ec.is_valid_isikukood(VALID[:10] + d)
        ]
        assert accepted == [VALID[10]]

    def test_round_two_branch_is_exercised(self) -> None:
        """A body whose round-1 remainder is 10 must fall through to round 2.

        Without the second weighting pass such codes would be assigned the
        impossible check digit 10 and silently never match.
        """
        weights = ec._ISIKUKOOD_WEIGHTS_ROUND1

        def round_one_remainder(body: str) -> int:
            return sum(int(c) * w for c, w in zip(body, weights)) % 11

        bodies = [
            str(n)
            for n in range(3_900_101_000, 3_900_101_200)
            if round_one_remainder(str(n)) == 10
        ]
        assert bodies, "no round-2 body found in the probe range"
        for body in bodies:
            code = synthetic_isikukood(body)
            assert ec.is_valid_isikukood(code)

    def test_first_digit_outside_1_to_8_is_rejected(self) -> None:
        for lead in "09":
            body = lead + VALID[1:10]
            assert not ec.is_valid_isikukood(synthetic_isikukood(body))

    def test_wrong_length_and_non_ascii_digits_are_rejected(self) -> None:
        assert not ec.is_valid_isikukood(VALID[:-1])
        assert not ec.is_valid_isikukood(VALID + "0")
        # Arabic-Indic digits satisfy str.isdigit() but are not an isikukood.
        assert not ec.is_valid_isikukood("٣٩٠٠" + VALID[4:])


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------

class TestMasking:
    def test_masked_form_shape(self) -> None:
        masked = ec.mask_isikukood(VALID)
        assert masked == f"{VALID[:3]}****{VALID[-2:]}"
        assert len(masked) == 9
        assert "*" in masked

    def test_masked_form_is_not_reversible_to_a_full_code(self) -> None:
        assert not ANY_11_DIGIT_RUN.search(ec.mask_isikukood(VALID))


# ---------------------------------------------------------------------------
# screen_personal_data
# ---------------------------------------------------------------------------

class TestScreenPersonalData:
    def test_valid_code_is_replaced_by_the_placeholder(self) -> None:
        text = f"Hageja U. V (isikukood {VALID}), esindaja vandeadvokaat."
        screened, findings = ec.screen_personal_data(text)
        assert VALID not in screened
        assert ec.PERSONAL_CODE_PLACEHOLDER in screened
        assert len(findings) == 1
        assert findings[0]["masked"] == ec.mask_isikukood(VALID)
        assert findings[0]["offset"] == text.index(VALID)

    def test_findings_never_carry_the_full_code(self) -> None:
        _screened, findings = ec.screen_personal_data(f"isikukood {VALID}")
        assert not ANY_11_DIGIT_RUN.search(json.dumps(findings))

    def test_invalid_checksum_is_left_untouched(self) -> None:
        bad = VALID[:10] + str((int(VALID[10]) + 1) % 10)
        assert not ec.is_valid_isikukood(bad)
        screened, findings = ec.screen_personal_data(f"isikukood {bad}")
        assert findings == []
        assert bad in screened

    def test_run_embedded_in_a_longer_digit_string_is_left_untouched(self) -> None:
        """A 12+ digit identifier must never be partially masked."""
        for embedded in (f"9{VALID}", f"{VALID}9", f"9{VALID}9"):
            screened, findings = ec.screen_personal_data(f"konto {embedded}")
            assert findings == []
            assert embedded in screened

    def test_implausible_birth_date_is_left_untouched(self) -> None:
        """Month 21 / day 57 mark a checksum collision, not a personal code."""
        for body in ("3902101000", "3900157000"):
            collision = synthetic_isikukood(body)
            screened, findings = ec.screen_personal_data(f"nr {collision}")
            assert findings == [], body
            assert collision in screened

    @pytest.mark.parametrize(
        "prefix",
        ["isikukood ", "isikukoodiga ", "isikukoodi ", "IK ", "i.k. ", "i. k. "],
    )
    def test_label_inflections_are_detected(self, prefix: str) -> None:
        _screened, findings = ec.screen_personal_data(f"Hageja A. B ({prefix}{VALID})")
        assert len(findings) == 1
        assert findings[0]["labelled"] is True

    def test_unlabelled_run_is_still_masked_but_marked_unlabelled(self) -> None:
        _screened, findings = ec.screen_personal_data(f"number {VALID} juures")
        assert len(findings) == 1
        assert findings[0]["labelled"] is False

    # --- negative labels: the number is declared to be something else -------

    @pytest.mark.parametrize(
        "prefix",
        [
            "Läti registrikood ",
            "(registrikood ",
            "registrikoodiga ",
            "reg. kood ",
            "reg kood ",
            "regkood ",
        ],
    )
    def test_registry_code_label_exempts_the_run(self, prefix: str) -> None:
        """A company registry code is 11 digits in Latvia and collides."""
        text = f"AS Reverta ({prefix}{VALID})"
        screened, findings = ec.screen_personal_data(text)
        assert findings == []
        assert screened == text

    @pytest.mark.parametrize(
        "prefix",
        ["otsuse nr ", "otsus nr ", "otsuse nr. ", "otsusega nr "],
    )
    def test_decision_number_label_exempts_the_run(self, prefix: str) -> None:
        text = f"1. novembri 2022. a {prefix}{VALID}-1 tühistamiseks"
        screened, findings = ec.screen_personal_data(text)
        assert findings == []
        assert screened == text

    def test_personal_label_beats_a_nearby_registry_label(self) -> None:
        """"registrikood X, isikukood Y" — Y is still a person's code."""
        text = f"registrikood 12345678, isikukood {VALID}"
        screened, findings = ec.screen_personal_data(text)
        assert len(findings) == 1
        assert findings[0]["labelled"] is True
        assert VALID not in screened
        assert ec.PERSONAL_CODE_PLACEHOLDER in screened

    def test_negative_label_does_not_cross_a_line_break(self) -> None:
        """A registry code on the previous line must not exempt this run."""
        _screened, findings = ec.screen_personal_data(f"registrikood\n{VALID}")
        assert len(findings) == 1
        assert findings[0]["labelled"] is False

    def test_negative_label_beyond_the_window_does_not_exempt(self) -> None:
        text = "registrikood" + ("." * 40) + VALID
        _screened, findings = ec.screen_personal_data(text)
        assert len(findings) == 1

    def test_label_beyond_the_window_does_not_count_as_labelled(self) -> None:
        text = "isikukood" + ("." * 60) + VALID
        _screened, findings = ec.screen_personal_data(text)
        assert findings[0]["labelled"] is False

    def test_labelled_only_skips_unlabelled_runs(self) -> None:
        text = f"number {VALID} ja isikukood {VALID_2}"
        screened, findings = ec.screen_personal_data(text, labelled_only=True)
        assert len(findings) == 1
        assert VALID in screened
        assert VALID_2 not in screened

    def test_multiple_codes_in_one_string(self) -> None:
        text = f"A (isikukood {VALID}) ja B (isikukood {VALID_2})."
        screened, findings = ec.screen_personal_data(text)
        assert len(findings) == 2
        assert screened.count(ec.PERSONAL_CODE_PLACEHOLDER) == 2
        assert not ANY_11_DIGIT_RUN.search(screened)
        # Surrounding prose survives intact.
        assert screened.startswith("A (isikukood ")
        assert screened.endswith(").")

    def test_screening_is_idempotent(self) -> None:
        once, first = ec.screen_personal_data(f"isikukood {VALID}")
        twice, second = ec.screen_personal_data(once)
        assert first and second == []
        assert twice == once

    def test_clean_text_is_returned_unchanged(self) -> None:
        text = "KarS § 199 alusel esitatud kaebus."
        screened, findings = ec.screen_personal_data(text)
        assert screened is text
        assert findings == []

    def test_non_string_input_is_passed_through(self) -> None:
        assert ec.screen_personal_data(None) == (None, [])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Node stamping
# ---------------------------------------------------------------------------

class TestStamping:
    def test_stamp_sets_flag_and_count(self) -> None:
        node: dict = {}
        assert ec.stamp_personal_data_screening(node, 2) is True
        assert node["estleg:personalDataScreened"] is True
        assert node["estleg:personalDataMaskedCount"] == 2

    def test_count_accumulates_across_passes(self) -> None:
        """The summary pass and the full-text pass each add to the count."""
        node: dict = {}
        ec.stamp_personal_data_screening(node, 1)
        ec.stamp_personal_data_screening(node, 3)
        assert node["estleg:personalDataMaskedCount"] == 4

    def test_restamping_with_zero_is_a_no_op(self) -> None:
        node: dict = {}
        ec.stamp_personal_data_screening(node, 2)
        assert ec.stamp_personal_data_screening(node, 0) is False
        assert node["estleg:personalDataMaskedCount"] == 2

    def test_stamp_on_a_clean_node_records_zero(self) -> None:
        node: dict = {}
        assert ec.stamp_personal_data_screening(node, 0) is True
        assert node["estleg:personalDataMaskedCount"] == 0


# ---------------------------------------------------------------------------
# screen_decision_node / screen_document
# ---------------------------------------------------------------------------

def _decision(node_id: str, **fields: object) -> dict:
    return {
        "@id": node_id,
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "estleg:caseNumber": "3-21-1/2",
        **fields,
    }


class TestScreenDecisionNode:
    def test_plain_string_summary_is_screened(self) -> None:
        """Older peeps store estleg:summary as a bare string."""
        node = _decision("estleg:RK_1", **{"estleg:summary": f"isikukood {VALID}"})
        changed, findings = screener.screen_decision_node(node)
        assert changed and len(findings) == 1
        assert isinstance(node["estleg:summary"], str)
        assert VALID not in node["estleg:summary"]

    def test_langstring_summary_keeps_its_shape_and_language(self) -> None:
        """Newer peeps store estleg:summary as {"@value", "@language"}."""
        node = _decision(
            "estleg:RK_2",
            **{"estleg:summary": {"@value": f"isikukood {VALID}", "@language": "et"}},
        )
        changed, findings = screener.screen_decision_node(node)
        assert changed and len(findings) == 1
        summary = node["estleg:summary"]
        assert isinstance(summary, dict)
        assert summary["@language"] == "et"
        assert VALID not in summary["@value"]
        assert ec.PERSONAL_CODE_PLACEHOLDER in summary["@value"]

    def test_legal_text_stays_a_plain_string(self) -> None:
        """SHACL declares sh:datatype xsd:string — never a langString."""
        node = _decision("estleg:RK_3", **{"estleg:legalText": f"isikukood {VALID}"})
        screener.screen_decision_node(node)
        assert isinstance(node["estleg:legalText"], str)

    def test_count_spans_both_fields(self) -> None:
        node = _decision(
            "estleg:RK_4",
            **{
                "estleg:summary": {"@value": f"isikukood {VALID}", "@language": "et"},
                "estleg:legalText": f"isikukood {VALID_2}",
            },
        )
        _changed, findings = screener.screen_decision_node(node)
        assert len(findings) == 2
        assert node["estleg:personalDataMaskedCount"] == 2

    def test_clean_node_is_stamped_with_zero(self) -> None:
        node = _decision("estleg:RK_5", **{"estleg:summary": "puhas tekst"})
        changed, findings = screener.screen_decision_node(node)
        assert changed is True and findings == []
        assert node["estleg:personalDataScreened"] is True
        assert node["estleg:personalDataMaskedCount"] == 0

    def test_second_pass_reports_no_change(self) -> None:
        node = _decision("estleg:RK_6", **{"estleg:legalText": f"isikukood {VALID}"})
        screener.screen_decision_node(node)
        changed, findings = screener.screen_decision_node(node)
        assert changed is False and findings == []

    def test_non_decision_nodes_are_ignored(self) -> None:
        doc = {
            "@graph": [
                {
                    "@id": "estleg:Riigikohus_2007_Map",
                    "@type": ["owl:Ontology"],
                    "estleg:legalText": f"isikukood {VALID}",
                }
            ]
        }
        changed, rows = screener.screen_document(doc, "riigikohus_2007_peep.json")
        assert changed is False and rows == []
        assert VALID in doc["@graph"][0]["estleg:legalText"]


# ---------------------------------------------------------------------------
# End-to-end backfill over a temporary corpus
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_corpus(tmp_path: Path) -> Path:
    rk_dir = tmp_path / "krr_outputs" / "riigikohus"
    rk_dir.mkdir(parents=True)
    doc = {
        "@context": {"estleg": "https://w3id.org/estleg/"},
        "@graph": [
            {"@id": "estleg:Riigikohus_2007_Map", "@type": ["owl:Ontology"]},
            _decision(
                "estleg:RK_leaky",
                **{
                    "estleg:summary": {
                        "@value": f"Hageja A. B (isikukood {VALID}).",
                        "@language": "et",
                    },
                    "estleg:legalText": f"Kostja C. D isikukoodiga {VALID_2} vaidleb.",
                },
            ),
            _decision("estleg:RK_clean", **{"estleg:summary": "puhas kokkuvõte"}),
        ],
    }
    (rk_dir / "riigikohus_2007_peep.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (rk_dir / "RIIGIKOHUS_INDEX.json").write_text(
        json.dumps(
            {"generated": "2026-01-01", "total_decisions": 2, "full_text": {"x": 1}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return rk_dir


def _peep(rk_dir: Path) -> dict:
    return json.loads(
        (rk_dir / "riigikohus_2007_peep.json").read_text(encoding="utf-8")
    )


class TestBackfillRun:
    def test_dry_run_writes_nothing(self, fake_corpus: Path) -> None:
        before = (fake_corpus / "riigikohus_2007_peep.json").read_bytes()
        stats = screener.run(
            fake_corpus, dry_run=True, krr_dir=fake_corpus.parent
        )
        assert stats["run_codes_masked"] == 2
        assert (fake_corpus / "riigikohus_2007_peep.json").read_bytes() == before
        assert not (fake_corpus.parent / "reports").exists()

    def test_apply_masks_and_stamps_every_decision(self, fake_corpus: Path) -> None:
        stats = screener.run(fake_corpus, krr_dir=fake_corpus.parent)
        assert stats["nodes_scanned"] == 2
        assert stats["run_nodes_masked"] == 1
        assert stats["run_codes_masked"] == 2
        assert stats["run_labelled"] == 2

        nodes = {n["@id"]: n for n in _peep(fake_corpus)["@graph"] if "@id" in n}
        leaky = nodes["estleg:RK_leaky"]
        assert VALID not in leaky["estleg:summary"]["@value"]
        assert VALID_2 not in leaky["estleg:legalText"]
        assert leaky["estleg:personalDataMaskedCount"] == 2
        assert nodes["estleg:RK_clean"]["estleg:personalDataScreened"] is True
        assert nodes["estleg:RK_clean"]["estleg:personalDataMaskedCount"] == 0
        # The graph header is untouched and node order is preserved.
        assert [n["@id"] for n in _peep(fake_corpus)["@graph"]] == [
            "estleg:Riigikohus_2007_Map",
            "estleg:RK_leaky",
            "estleg:RK_clean",
        ]

    def test_rerun_is_byte_identical(self, fake_corpus: Path) -> None:
        screener.run(fake_corpus, krr_dir=fake_corpus.parent)
        snapshot = {
            path: path.read_bytes()
            for path in sorted(fake_corpus.parent.rglob("*.json"))
        }
        stats = screener.run(fake_corpus, krr_dir=fake_corpus.parent)
        assert stats["run_codes_masked"] == 0
        assert stats["files_changed"] == []
        assert {p: p.read_bytes() for p in sorted(snapshot)} == snapshot

    def test_report_shape_and_contents(self, fake_corpus: Path) -> None:
        screener.run(fake_corpus, krr_dir=fake_corpus.parent)
        report_path = (
            fake_corpus.parent / "reports" / screener.REPORT_NAME
        )
        raw = report_path.read_text(encoding="utf-8")
        report = json.loads(raw)

        assert set(report) == {"generated", "totals", "entries"}
        assert report["generated"] == ec.BUILD_EVALUATION_DATE
        assert report["totals"] == {
            "nodes_scanned": 2,
            "nodes_masked": 1,
            "codes_masked": 2,
            "labelled": 2,
            "unlabelled": 0,
        }
        entry = report["entries"][0]
        assert set(entry) == {"@id", "file", "masked", "labelled"}
        assert entry["@id"] == "estleg:RK_leaky"
        assert entry["file"] == "riigikohus_2007_peep.json"
        assert entry["masked"] == sorted(
            [ec.mask_isikukood(VALID), ec.mask_isikukood(VALID_2)]
        )
        # The whole point: the audit trail must not itself leak a code.
        assert not ANY_11_DIGIT_RUN.search(raw)

    def test_report_entries_are_sorted_by_id(self, fake_corpus: Path) -> None:
        doc = _peep(fake_corpus)
        doc["@graph"].insert(
            1,
            _decision(
                "estleg:RK_aaa", **{"estleg:legalText": f"isikukood {VALID}"}
            ),
        )
        (fake_corpus / "riigikohus_2007_peep.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        screener.run(fake_corpus, krr_dir=fake_corpus.parent)
        report = json.loads(
            (fake_corpus.parent / "reports" / screener.REPORT_NAME).read_text(
                encoding="utf-8"
            )
        )
        ids = [e["@id"] for e in report["entries"]]
        assert ids == sorted(ids)

    def test_rerun_does_not_empty_the_report(self, fake_corpus: Path) -> None:
        """The codes are gone after run 1, so run 2 must not erase the record."""
        screener.run(fake_corpus, krr_dir=fake_corpus.parent)
        screener.run(fake_corpus, krr_dir=fake_corpus.parent)
        report = json.loads(
            (fake_corpus.parent / "reports" / screener.REPORT_NAME).read_text(
                encoding="utf-8"
            )
        )
        assert report["totals"]["codes_masked"] == 2
        assert len(report["entries"]) == 1

    def test_index_block_is_added_after_full_text(self, fake_corpus: Path) -> None:
        screener.run(fake_corpus, krr_dir=fake_corpus.parent)
        index = json.loads(
            (fake_corpus / "RIIGIKOHUS_INDEX.json").read_text(encoding="utf-8")
        )
        assert index["personal_data_screening"] == {
            "generated": ec.BUILD_EVALUATION_DATE,
            "nodes_masked": 1,
            "codes_masked": 2,
        }
        assert list(index) == [
            "generated",
            "total_decisions",
            "full_text",
            "personal_data_screening",
        ]
        # Pre-existing content is untouched.
        assert index["total_decisions"] == 2
        assert index["full_text"] == {"x": 1}

    def test_empty_directory_is_a_hard_failure(self, tmp_path: Path) -> None:
        empty = tmp_path / "riigikohus"
        empty.mkdir()
        with pytest.raises(SystemExit):
            screener.run(empty, krr_dir=tmp_path)

    def test_cli_dry_run_reports_without_writing(
        self, fake_corpus: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        before = (fake_corpus / "riigikohus_2007_peep.json").read_bytes()
        assert screener.main(["--dir", str(fake_corpus), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert not ANY_11_DIGIT_RUN.search(out)
        assert (fake_corpus / "riigikohus_2007_peep.json").read_bytes() == before


# ---------------------------------------------------------------------------
# validate_all gate
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_reporter():
    validate_all.reset()
    yield
    validate_all.reset()


def _write_peep(path: Path, nodes: list[dict]) -> Path:
    path.write_text(
        json.dumps({"@graph": nodes}, ensure_ascii=False), encoding="utf-8"
    )
    return path


class TestValidateNoPersonalCodes:
    def test_leaked_code_in_legal_text_is_an_error(self, tmp_path: Path) -> None:
        path = _write_peep(
            tmp_path / "riigikohus_2007_peep.json",
            [_decision("estleg:RK_leak", **{"estleg:legalText": f"isikukood {VALID}"})],
        )
        validate_all.validate_no_personal_codes([path])
        assert any(
            "Estonian personal ID codes in published court text" in e
            for e in validate_all.errors
        ), validate_all.errors

    def test_leaked_code_in_langstring_summary_is_an_error(
        self, tmp_path: Path
    ) -> None:
        path = _write_peep(
            tmp_path / "riigikohus_2008_peep.json",
            [
                _decision(
                    "estleg:RK_leak2",
                    **{
                        "estleg:summary": {
                            "@value": f"isikukood {VALID}",
                            "@language": "et",
                        }
                    },
                )
            ],
        )
        validate_all.validate_no_personal_codes([path])
        assert validate_all.errors

    def test_error_output_carries_only_the_masked_form(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = _write_peep(
            tmp_path / "riigikohus_2009_peep.json",
            [_decision("estleg:RK_leak3", **{"estleg:legalText": f"ik {VALID}"})],
        )
        validate_all.validate_no_personal_codes([path])
        printed = capsys.readouterr().out
        assert ec.mask_isikukood(VALID) in printed
        assert VALID not in printed
        assert not ANY_11_DIGIT_RUN.search(" ".join(validate_all.errors))

    def test_screened_corpus_passes(self, tmp_path: Path) -> None:
        node = _decision(
            "estleg:RK_ok",
            **{
                "estleg:summary": {
                    "@value": f"isikukood {VALID}",
                    "@language": "et",
                },
                "estleg:legalText": f"isikukood {VALID_2}",
            },
        )
        screener.screen_decision_node(node)
        path = _write_peep(tmp_path / "riigikohus_2010_peep.json", [node])
        validate_all.validate_no_personal_codes([path])
        assert validate_all.errors == [], validate_all.errors

    def test_non_court_node_is_not_flagged(self, tmp_path: Path) -> None:
        """Only CourtDecision prose is in scope for this gate."""
        path = _write_peep(
            tmp_path / "some_law_peep.json",
            [
                {
                    "@id": "estleg:KarS_Par_1",
                    "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                    "estleg:legalText": f"näidiskood {VALID}",
                }
            ],
        )
        validate_all.validate_no_personal_codes([path])
        assert validate_all.errors == [], validate_all.errors

    def test_registry_code_is_not_flagged(self, tmp_path: Path) -> None:
        """The gate must apply the same carve-out as the screener."""
        path = _write_peep(
            tmp_path / "riigikohus_2013_peep.json",
            [
                _decision(
                    "estleg:RK_reg",
                    **{"estleg:legalText": f"AS Reverta (registrikood {VALID})"},
                )
            ],
        )
        validate_all.validate_no_personal_codes([path])
        assert validate_all.errors == [], validate_all.errors

    def test_checksum_collision_is_not_flagged(self, tmp_path: Path) -> None:
        collision = synthetic_isikukood("3902101000")  # month 21
        path = _write_peep(
            tmp_path / "riigikohus_2011_peep.json",
            [_decision("estleg:RK_nr", **{"estleg:legalText": f"otsus nr {collision}"})],
        )
        validate_all.validate_no_personal_codes([path])
        assert validate_all.errors == [], validate_all.errors

    def test_unparseable_file_is_left_to_the_syntax_gate(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "broken_peep.json"
        path.write_text(f"{{not json {VALID}", encoding="utf-8")
        validate_all.validate_no_personal_codes([path])
        assert validate_all.errors == [], validate_all.errors


# ---------------------------------------------------------------------------
# Committed corpus invariant
# ---------------------------------------------------------------------------

def test_committed_riigikohus_corpus_is_screened() -> None:
    """Release gate: the shipped Riigikohus peeps carry no personal codes."""
    rk_dir = ec.KRR_DIR / "riigikohus"
    files = screener.peep_files(rk_dir)
    if not files:
        pytest.fail(f"no riigikohus peeps found under {rk_dir}")
    validate_all.reset()
    validate_all.validate_no_personal_codes(files)
    assert validate_all.errors == [], validate_all.errors


@pytest.mark.parametrize('prefix', ['registrikood 12345678, ', 'otsuse nr 42, '])
def test_negative_label_does_not_exempt_a_later_number(prefix: str) -> None:
    screened, findings = ec.screen_personal_data(prefix + VALID)
    assert len(findings) == 1
    assert VALID not in screened
    assert screened.startswith(prefix)


def test_gate_checks_unicode_escaped_digits(tmp_path: Path) -> None:
    path = _write_peep(
        tmp_path / 'escaped_peep.json',
        [_decision('estleg:RK_escaped', **{'estleg:legalText': f'isikukood {VALID}'})],
    )
    escaped = ''.join('\\u%04x' % ord(c) for c in VALID)
    path.write_text(path.read_text().replace(VALID, escaped), encoding='utf-8')
    validate_all.validate_no_personal_codes([path])
    assert validate_all.errors
