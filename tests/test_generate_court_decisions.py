"""Tests for ``scripts/generate_court_decisions.py``.

Covers the regression fixes called out in issue #176:

1. ``riigikohus_link`` URL must be encoded so case numbers
   containing ``#``, ``/``, or whitespace cannot break the URL.
2. ``classify_case`` must accept leading whitespace / BOM and use
   a digit-anchored regex rather than ``case_nr[0]``.
3. ``parse_html_table`` must validate the column count and skip
   misaligned rows (preventing summaries from absorbing trailing
   columns).
4. ``detect_referenced_laws`` must canonicalise variants
   (``Kars`` → ``KarS``) and anchor on word boundaries so
   ``\\w+seaduse?`` cannot cross a word boundary greedily.
5. ``decision_to_node`` must produce stable IRIs across runs and
   skip rows missing ``object_id`` rather than collide.
"""

from __future__ import annotations

import logging
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_court_decisions as gcd


# ---------------------------------------------------------------------------
# classify_case
# ---------------------------------------------------------------------------

class TestClassifyCase:
    def test_criminal_case(self) -> None:
        type_id, _, _ = gcd.classify_case("1-21-1234")
        assert type_id == "Criminal"

    def test_civil_case(self) -> None:
        type_id, _, _ = gcd.classify_case("2-22-5678")
        assert type_id == "Civil"

    def test_administrative_case(self) -> None:
        type_id, _, _ = gcd.classify_case("3-21-2176/52")
        assert type_id == "Administrative"

    def test_misdemeanor_case(self) -> None:
        type_id, _, _ = gcd.classify_case("4-21-001")
        assert type_id == "Misdemeanor"

    def test_constitutional_review_case(self) -> None:
        type_id, _, _ = gcd.classify_case("5-21-1")
        assert type_id == "ConstitutionalReview"

    def test_leading_whitespace_does_not_corrupt_classification(self) -> None:
        """Pre-fix bug: ``case_nr[0]`` reads ``" "`` for whitespace-led
        inputs and falls through to ``Other``."""
        type_id, _, _ = gcd.classify_case("   3-21-001")
        assert type_id == "Administrative"

    def test_bom_prefix_does_not_corrupt_classification(self) -> None:
        type_id, _, _ = gcd.classify_case("﻿3-21-001")
        assert type_id == "Administrative"

    def test_empty_input_returns_other_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=gcd.logger.name):
            type_id, _, _ = gcd.classify_case("")
        assert type_id == "Other"
        assert any("empty case_nr" in r.message for r in caplog.records)

    def test_no_leading_digit_returns_other_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=gcd.logger.name):
            type_id, _, _ = gcd.classify_case("XYZ-not-a-case")
        assert type_id == "Other"
        assert any("no leading digit" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# detect_referenced_laws
# ---------------------------------------------------------------------------

class TestDetectReferencedLaws:
    def test_known_abbrev_canonicalises_case(self) -> None:
        """``KARS``/``Kars``/``KarS`` MUST collapse to canonical ``KarS``."""
        refs = gcd.detect_referenced_laws("Kars § 113 lg 1 ja KARS § 114 ja KarS § 115")
        assert refs.count("KarS") == 1, refs
        assert "KarS" in refs

    def test_multiple_abbreviations_preserve_first_occurrence_order(self) -> None:
        refs = gcd.detect_referenced_laws("VÕS § 100 ja KarS § 113 lg 1")
        assert refs == ["VÕS", "KarS"]

    def test_genitive_law_form_is_lowercased(self) -> None:
        """Pre-fix bug: ``Liiklusseaduse`` and ``liiklusseaduse`` would
        appear as separate entries."""
        refs = gcd.detect_referenced_laws(
            "Liiklusseaduse § 227 lg 2 ja liiklusseaduse § 228"
        )
        # both genitive forms collapse into one entry
        lowered = [r for r in refs if r.endswith("seaduse")]
        assert len(lowered) == 1
        assert lowered[0] == lowered[0].lower()

    def test_word_boundary_anchor_does_not_match_inside_other_words(
        self,
    ) -> None:
        """Anchored regex must not match ``KarS`` inside ``WIKARS_FOO``."""
        refs = gcd.detect_referenced_laws("WIKARSFOO 123")
        assert "KarS" not in refs

    def test_empty_summary_returns_empty(self) -> None:
        assert gcd.detect_referenced_laws("") == []
        assert gcd.detect_referenced_laws(None or "") == []


# ---------------------------------------------------------------------------
# parse_html_table
# ---------------------------------------------------------------------------

class TestParseHtmlTable:
    def test_valid_row_extracted(self) -> None:
        html = """
        <table class="search-result"><tr>
            <td>26.02.2026</td>
            <td><a href="/lahendid/?asjaNr=3-21-2176/52">3-21-2176/52</a></td>
            <td>Kohtuotsus | Keskkonnaameti kassatsioonkaebus</td>
            <td>4567890</td>
        </tr></table>
        """
        rows = gcd.parse_html_table(html)
        assert len(rows) == 1
        row = rows[0]
        assert row["case_nr"] == "3-21-2176/52"
        assert row["date"] == "26.02.2026"
        assert row["decision_type"] == "Kohtuotsus"
        assert row["summary"] == "Keskkonnaameti kassatsioonkaebus"
        assert row["object_id"] == "4567890"
        assert row["link"].startswith("https://rikos.rik.ee")

    def test_row_with_wrong_column_count_is_skipped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Pre-fix bug: a row with 5 columns silently produced a row whose
        summary absorbed extra cells."""
        html = """
        <table><tr>
            <td>26.02.2026</td>
            <td>3-21-2176/52</td>
            <td>Kohtuotsus | Summary</td>
            <td>4567890</td>
            <td>EXTRA</td>
        </tr></table>
        """
        with caplog.at_level(logging.WARNING, logger=gcd.logger.name):
            rows = gcd.parse_html_table(html)
        assert rows == []
        assert any("expected 4" in r.message for r in caplog.records)

    def test_row_missing_required_fields_is_skipped(self) -> None:
        html = """
        <table><tr>
            <td></td>
            <td>3-21-2176/52</td>
            <td>desc</td>
            <td>id</td>
        </tr></table>
        """
        rows = gcd.parse_html_table(html)
        assert rows == []

    def test_multiple_rows_parsed(self) -> None:
        html = """
        <table class="search-result">
        <tr>
            <td>01.01.2025</td><td>1-25-1</td><td>Kohtuotsus | A</td><td>10</td>
        </tr>
        <tr>
            <td>02.01.2025</td><td>2-25-2</td><td>Kohtuotsus | B</td><td>20</td>
        </tr>
        </table>
        """
        rows = gcd.parse_html_table(html)
        assert len(rows) == 2
        assert rows[0]["case_nr"] == "1-25-1"
        assert rows[1]["case_nr"] == "2-25-2"

    def test_link_normalised_to_absolute_url(self) -> None:
        html = """
        <table><tr>
            <td>01.01.2025</td>
            <td><a href="/lahendid/abc">1-25-1</a></td>
            <td>Kohtuotsus | A</td>
            <td>10</td>
        </tr></table>
        """
        rows = gcd.parse_html_table(html)
        assert rows[0]["link"] == "https://rikos.rik.ee/lahendid/abc"


# ---------------------------------------------------------------------------
# decision_to_node
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_decision() -> dict:
    return {
        "case_nr": "3-21-2176/52",
        "object_id": "4567890",
        "date": "26.02.2026",
        "decision_type": "Kohtuotsus",
        "summary": "KarS § 113 ja Kars § 114 viidatakse otsuses",
        "link": "https://rikos.rik.ee/lahend/12345",
    }


class TestDecisionToNode:
    def test_basic_node_fields(self, sample_decision: dict) -> None:
        node = gcd.decision_to_node(sample_decision, 2026, set())
        assert node is not None
        assert node["@id"].startswith("estleg:RK_")
        assert node["estleg:caseNumber"] == "3-21-2176/52"
        assert node["estleg:caseType"]["@id"] == "estleg:CaseType_Administrative"
        assert node["estleg:decisionType"]["@id"] == (
            "estleg:DecisionType_Judgment"
        )
        assert node["estleg:decisionDate"]["@value"] == "2026-02-26"
        assert node["estleg:rikObjectId"] == "4567890"

    def test_iri_includes_object_id_for_stability(
        self, sample_decision: dict
    ) -> None:
        """The IRI MUST include ``object_id`` so two runs with the same
        decisions produce identical IRIs regardless of feed order."""
        node = gcd.decision_to_node(sample_decision, 2026, set())
        assert node is not None
        assert "4567890" in node["@id"], (
            f"object_id missing from IRI: {node['@id']}"
        )

    def test_url_is_properly_encoded_for_special_chars(self) -> None:
        """Pre-fix bug: f-string concatenation left ``#`` unencoded."""
        dec = {
            "case_nr": "3-21#2176/52",
            "object_id": "abc",
            "summary": "",
            "date": "",
            "decision_type": "",
            "link": "https://rikos.rik.ee/x",
        }
        node = gcd.decision_to_node(dec, 2026, set())
        assert node is not None
        url = node["estleg:decisionLink"]["@value"]
        # The '#' MUST be encoded as %23 (or otherwise escaped) so the
        # downstream URL parser sees one query string, not a fragment.
        assert "#" not in url.split("?", 1)[1], (
            f"unencoded # in URL query: {url}"
        )
        # And the case number must round-trip back via standard parsing.
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        assert params["asjaNr"] == ["3-21#2176/52"]

    def test_url_encodes_whitespace(self) -> None:
        dec = {
            "case_nr": "3 21 2176",
            "object_id": "abc",
            "summary": "",
            "date": "",
            "decision_type": "",
            "link": "https://rikos.rik.ee/x",
        }
        node = gcd.decision_to_node(dec, 2026, set())
        assert node is not None
        url = node["estleg:decisionLink"]["@value"]
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        assert params["asjaNr"] == ["3 21 2176"]

    def test_missing_object_id_returns_none(
        self, sample_decision: dict, caplog: pytest.LogCaptureFixture
    ) -> None:
        sample_decision["object_id"] = ""
        with caplog.at_level(logging.WARNING, logger=gcd.logger.name):
            node = gcd.decision_to_node(sample_decision, 2026, set())
        assert node is None
        assert any("object_id" in r.message for r in caplog.records)

    def test_empty_case_nr_returns_none(self) -> None:
        dec = {"case_nr": "", "object_id": "abc"}
        assert gcd.decision_to_node(dec, 2026, set()) is None

    def test_duplicate_id_returns_none(self, sample_decision: dict) -> None:
        seen: set[str] = set()
        first = gcd.decision_to_node(sample_decision, 2026, seen)
        assert first is not None
        # Same input twice — second call must skip without colliding.
        second = gcd.decision_to_node(sample_decision, 2026, seen)
        assert second is None

    def test_two_decisions_same_case_nr_different_object_id_get_distinct_ids(
        self,
    ) -> None:
        """Pre-fix bug: collision-resolution flipped IDs across runs.
        With object_id always in the IRI, two decisions sharing
        ``case_nr`` but with distinct ``object_id`` produce distinct
        stable IRIs regardless of input order.
        """
        dec_a = {
            "case_nr": "3-21-2176/52",
            "object_id": "ALPHA",
            "summary": "",
            "date": "",
            "decision_type": "",
            "link": "",
        }
        dec_b = {
            "case_nr": "3-21-2176/52",
            "object_id": "BRAVO",
            "summary": "",
            "date": "",
            "decision_type": "",
            "link": "",
        }
        seen_forward: set[str] = set()
        a_first = gcd.decision_to_node(dec_a, 2026, seen_forward)["@id"]
        b_first = gcd.decision_to_node(dec_b, 2026, seen_forward)["@id"]
        # And in reverse order — IDs must be identical to the forward run.
        seen_reverse: set[str] = set()
        b_second = gcd.decision_to_node(dec_b, 2026, seen_reverse)["@id"]
        a_second = gcd.decision_to_node(dec_a, 2026, seen_reverse)["@id"]
        assert a_first == a_second, (
            "IRI for dec_a flipped between runs — not deterministic"
        )
        assert b_first == b_second, (
            "IRI for dec_b flipped between runs — not deterministic"
        )
        assert a_first != b_first

    def test_referenced_laws_canonicalised(self, sample_decision: dict) -> None:
        node = gcd.decision_to_node(sample_decision, 2026, set())
        assert node is not None
        refs = node.get("estleg:referencedLaw")
        assert refs is not None
        # KarS appears once in canonical form, not twice as 'KarS' + 'Kars'.
        assert refs.count("KarS") == 1
