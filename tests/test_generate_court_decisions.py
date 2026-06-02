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

And the full-decision-text ingestion added for issue #135:

6. ``extract_decision_text`` strips both the modern ``WordSection1``
   and the legacy bare-``<P>`` detail-page layouts to plain text, and
   rejects the search-form fallback page.
7. ``fetch_decision_text`` serves a fresh cache entry without any HTTP
   call, and writes the cache on a miss.
8. ``--fetch-full-text --full-text-limit N`` adds a plain-string
   ``estleg:legalText`` to exactly the first N decision nodes; without
   the flag no node is touched.
"""

from __future__ import annotations

import json
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

    @pytest.mark.skipif(
        not gcd._BS4_AVAILABLE, reason="bs4 unavailable — cannot compare paths"
    )
    def test_bs4_and_regex_paths_agree_on_inline_tags_and_single_quoted_href(
        self,
    ) -> None:
        """#272: the bs4 and regex parsers must agree on ``case_nr`` and
        ``link``. Pre-fix, an inline tag inside the case-number cell made bs4
        emit ``3 -1-1`` (space-joined) while the regex emitted ``3-1-1``, and a
        single-quoted ``href='...'`` was extracted by bs4 but dropped by the
        regex (it matched only double quotes) — a silent IRI/link divergence
        whenever bs4 was absent.
        """
        html = """
        <table class="search-result"><tr>
            <td>01.01.2025</td>
            <td><a href='/lahendid/abc'>3<wbr>-1-1<sup>1</sup></a></td>
            <td>Kohtuotsus | A</td>
            <td>1 0</td>
        </tr></table>
        """
        bs4_rows = gcd._parse_with_bs4(html)
        regex_rows = gcd._parse_with_regex(html)

        assert len(bs4_rows) == 1
        assert len(regex_rows) == 1
        # The two paths must yield byte-identical case_nr, link, and object_id.
        assert bs4_rows[0]["case_nr"] == regex_rows[0]["case_nr"]
        assert bs4_rows[0]["link"] == regex_rows[0]["link"]
        assert bs4_rows[0]["object_id"] == regex_rows[0]["object_id"]
        # And both must have actually parsed the single-quoted href (not "").
        assert regex_rows[0]["link"] == "https://rikos.rik.ee/lahendid/abc"
        # Inline tags are joined with a single space (matching bs4's
        # get_text(" ")) and runs of whitespace are collapsed — never doubled.
        assert regex_rows[0]["case_nr"] == "3 -1-1 1"
        assert regex_rows[0]["object_id"] == "1 0"


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


# ---------------------------------------------------------------------------
# extract_decision_text (issue #135)
# ---------------------------------------------------------------------------

# Modern RIK detail page: text wrapped in <div class=WordSection1>, with
# the LahendiDokument meta marker and the data-faili-objekt-id attribute.
_MODERN_DETAIL_HTML = """<!DOCTYPE HTML>
<html><head>
<meta name="description" content="LahendiDokument">
<title>5-25-80/23</title></head>
<body><div id="print_nupp"><a href="#"><img src="x.gif" alt="print"></a></div>
<div id="custom-data-container" data-faili-objekt-id="445816132"
     data-toimingu-nr="abc" data-on-lahend-annoteeritud="True"></div><br/>
<div class=WordSection1>
<p align=center><b>RIIGIKOHUS<br>PÕHISEADUSLIKKUSE JÄRELEVALVE KOLLEEGIUM</b></p>
<p align=center><b>KOHTUOTSUS<br></b>Eesti Vabariigi nimel</p>
<p>RIIGIKOHUS OTSUSTAB&nbsp;1.&nbsp;J&#228;tta veeseadus p&#245;hiseadusevastaseks tunnistamata.</p>
</div></body></html>"""

# Legacy detail page: no WordSection1, bare <P> straight under <body>.
_LEGACY_DETAIL_HTML = """<!DOCTYPE HTML>
<html><head>
<meta name="description" content="LahendiDokument">
<title>3-2-1-136-96</title></head>
<body><div id="print_nupp"><a href="#"><img src="x.gif"></a></div>
<div id="custom-data-container" data-faili-objekt-id="206086349"></div><br/>
<P><a href="x">3-2-1-136-96</a></P>
<B><P ALIGN="CENTER">K O H T U O T S U S</P></B>
<P ALIGN="CENTER">Eesti Vabariigi nimel</P>
<P>Riigikohtu tsiviilkolleegium koosseisus: eesistuja J. Luik vaatas l&#228;bi.</P>
<B><P ALIGN="CENTER">o t s u s t a s:</P></B>
<P>T&#252;histada otsus.</P></BODY></html>"""

# The "no such decision" fallback: RIK serves the search form, not a 404.
_SEARCH_FORM_HTML = """<!DOCTYPE html>
<html><head><title>Lahendi Otsing</title></head>
<body><div id="lahenditeOtsing" class="container body-content">
<form action="/" method="get"><label>Aasta</label>
<input name="aasta" type="text" /></form></div></body></html>"""


class TestExtractDecisionText:
    def test_modern_wordsection_layout_extracted(self) -> None:
        text = gcd.extract_decision_text(_MODERN_DETAIL_HTML)
        assert text is not None
        assert "RIIGIKOHUS OTSUSTAB" in text
        # HTML entities decoded; markup gone; NBSP folded to a space.
        assert "Jätta veeseadus põhiseadusevastaseks tunnistamata" in text
        assert "<" not in text and "&#" not in text
        # The print-button / data-container wrappers contribute no text.
        assert "print" not in text.lower()

    def test_legacy_bare_p_layout_extracted(self) -> None:
        text = gcd.extract_decision_text(_LEGACY_DETAIL_HTML)
        assert text is not None
        assert "K O H T U O T S U S" in text
        assert "Riigikohtu tsiviilkolleegium koosseisus" in text
        assert "Tühistada otsus" in text

    def test_search_form_fallback_returns_none(self) -> None:
        """A bogus asjaNr makes RIK serve the search form — not a decision."""
        assert gcd.extract_decision_text(_SEARCH_FORM_HTML) is None

    def test_empty_input_returns_none(self) -> None:
        assert gcd.extract_decision_text("") is None
        assert gcd.extract_decision_text(None or "") is None

    def test_too_short_decision_text_returns_none(self) -> None:
        tiny = (
            '<html><head><meta name="description" content="LahendiDokument">'
            '</head><body><div data-faili-objekt-id="1"></div><p>X</p></body></html>'
        )
        assert gcd.extract_decision_text(tiny) is None

    def test_mojibake_like_text_returns_none(self) -> None:
        garbled = (
            '<html><head><meta name="description" content="LahendiDokument">'
            '</head><body><div data-faili-objekt-id="1"></div><p>'
            + ("Ã�Â¤�" * 30)
            + "</p></body></html>"
        )
        assert gcd.extract_decision_text(garbled) is None

    def test_realistic_mojibake_markers_return_none(self) -> None:
        garbled = (
            '<html><head><meta name="description" content="LahendiDokument">'
            '</head><body><div data-faili-objekt-id="1"></div><p>'
            + ("PÃµhiseaduse Â§ 12 alusel lahendatud kohtuasja tekst. " * 4)
            + "</p></body></html>"
        )
        assert gcd.extract_decision_text(garbled) is None

    def test_normal_decision_text_quality_ratio_passes(self) -> None:
        text = gcd.extract_decision_text(_MODERN_DETAIL_HTML)
        assert text is not None
        assert gcd.decision_text_quality_ratio(text) >= (
            gcd.MIN_DECISION_TEXT_VALID_CHAR_RATIO
        )


# ---------------------------------------------------------------------------
# fetch_decision_text — caching (issue #135)
# ---------------------------------------------------------------------------

@pytest.fixture
def _isolated_court_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the detail-page cache at a throwaway dir and silence sleeps."""
    cache_dir = tmp_path / "court_decisions"
    monkeypatch.setattr(gcd, "COURT_TEXT_CACHE_DIR", cache_dir)
    monkeypatch.setattr(gcd.time, "sleep", lambda *_a, **_k: None)
    return cache_dir


class TestFetchDecisionTextCache:
    def test_legacy_cache_files_are_purged(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "court_decisions"
        cache_dir.mkdir()
        legacy_plain = cache_dir / "321217652.txt"
        legacy_underscored = cache_dir / "3_21_2176_52.txt"
        current = cache_dir / "321217652_0123456789.txt"
        for path in (legacy_plain, legacy_underscored, current):
            path.write_text("cached", encoding="utf-8")

        assert gcd._migrate_legacy_cache(cache_dir) == 2
        assert not legacy_plain.exists()
        assert not legacy_underscored.exists()
        assert current.exists()

    def test_cache_hit_does_not_call_requests_get(
        self, _isolated_court_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh cache entry must short-circuit the HTTP request entirely."""
        case_nr = "5-25-80/23"
        _isolated_court_cache.mkdir(parents=True, exist_ok=True)
        cache_path = gcd._court_text_cache_path(case_nr)
        cache_path.write_text("CACHED FULL TEXT OF DECISION 5-25-80", encoding="utf-8")

        def _boom(*_a, **_k):  # pragma: no cover - must not be reached
            raise AssertionError("requests.get must not be called on a cache hit")

        monkeypatch.setattr(gcd.requests, "get", _boom)
        text = gcd.fetch_decision_text(case_nr)
        assert text == "CACHED FULL TEXT OF DECISION 5-25-80"

    def test_cache_hit_sets_session_fetched_false(
        self, _isolated_court_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        case_nr = "1-25-3086/25"
        _isolated_court_cache.mkdir(parents=True, exist_ok=True)
        gcd._court_text_cache_path(case_nr).write_text("x" * 100, encoding="utf-8")
        monkeypatch.setattr(
            gcd.requests, "get",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no HTTP on cache hit")),
        )
        flag: list[bool] = [True]
        gcd.fetch_decision_text(case_nr, session_fetched=flag)
        assert flag == [False]

    def test_cache_miss_fetches_and_writes_cache(
        self, _isolated_court_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        case_nr = "2-24-401/36"
        calls: list[str] = []

        class _Resp:
            text = _MODERN_DETAIL_HTML

            def raise_for_status(self) -> None:
                return None

        def _fake_get(url, **_kw):
            calls.append(url)
            return _Resp()

        monkeypatch.setattr(gcd.requests, "get", _fake_get)
        flag: list[bool] = [False]
        text = gcd.fetch_decision_text(case_nr, session_fetched=flag)
        assert text is not None and "RIIGIKOHUS OTSUSTAB" in text
        assert flag == [True], "a network fetch must mark session_fetched True"
        assert len(calls) == 1
        # asjaNr must be query-string encoded into the detail URL.
        assert "asjaNr=2-24-401%2F36" in calls[0]
        # And the result must now be cached for the next run.
        cache_path = gcd._court_text_cache_path(case_nr)
        assert cache_path.exists()
        assert "RIIGIKOHUS OTSUSTAB" in cache_path.read_text(encoding="utf-8")

        # Second call: cache hit, no further HTTP.
        monkeypatch.setattr(
            gcd.requests, "get",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must hit cache")),
        )
        assert gcd.fetch_decision_text(case_nr) == text

    def test_cache_filename_preserves_case_number_uniqueness(
        self, _isolated_court_cache: Path
    ) -> None:
        case_numbers = ["5-25-80/23", "52-580/23", "5-25-8/023", "525-8/023"]
        names = {gcd._court_text_cache_path(case_nr).name for case_nr in case_numbers}
        assert len(names) == len(case_numbers)

    def test_search_form_response_not_cached_and_returns_none(
        self, _isolated_court_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        case_nr = "BOGUS-99-9999/99"

        class _Resp:
            text = _SEARCH_FORM_HTML

            def raise_for_status(self) -> None:
                return None

        monkeypatch.setattr(gcd.requests, "get", lambda *_a, **_k: _Resp())
        assert gcd.fetch_decision_text(case_nr) is None
        cache_path = gcd._court_text_cache_path(case_nr)
        assert not cache_path.exists()

    def test_transient_failure_returns_none(
        self, _isolated_court_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gcd, "DETAIL_RETRIES", 2)

        def _always_500(*_a, **_k):
            raise RuntimeError("HTTP 500")

        monkeypatch.setattr(gcd.requests, "get", _always_500)
        assert gcd.fetch_decision_text("3-21-2176/52") is None

    def test_empty_case_nr_returns_none_without_http(
        self, _isolated_court_cache: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            gcd.requests, "get",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no HTTP for empty case_nr")),
        )
        assert gcd.fetch_decision_text("") is None
        assert gcd.fetch_decision_text("   ") is None


# ---------------------------------------------------------------------------
# enrich_full_text / --fetch-full-text (issue #135)
# ---------------------------------------------------------------------------

def _write_peep(path: Path, year: int, decisions: list[tuple[str, str]]) -> None:
    """Write a minimal ``riigikohus_<year>_peep.json`` with given decisions.

    ``decisions`` is a list of ``(case_nr, object_id)`` pairs.
    """
    graph: list[dict] = [
        {
            "@id": f"estleg:Riigikohus_{year}_Map",
            "@type": ["owl:Ontology"],
            "rdfs:label": {"@value": f"Riigikohtu lahendid {year}", "@language": "et"},
        }
    ]
    for case_nr, object_id in decisions:
        graph.append(
            {
                "@id": f"estleg:RK_{gcd.sanitize_id(case_nr)}_{gcd.sanitize_id(object_id)}",
                "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
                "rdfs:label": {"@value": f"RK {case_nr}", "@language": "et"},
                "estleg:caseNumber": case_nr,
                "estleg:caseType": {"@id": "estleg:CaseType_Administrative"},
                "estleg:rikObjectId": object_id,
                "estleg:summary": {"@value": "lühike kokkuvõte", "@language": "et"},
            }
        )
    path.write_text(json.dumps({"@context": gcd.CONTEXT, "@graph": graph}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def _fake_rk_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch ``riigikohus/`` dir with two year files + isolated cache."""
    rk_dir = tmp_path / "riigikohus"
    rk_dir.mkdir()
    monkeypatch.setattr(gcd, "RK_DIR", rk_dir)
    monkeypatch.setattr(gcd, "COURT_TEXT_CACHE_DIR", tmp_path / ".cache" / "court_decisions")
    monkeypatch.setattr(gcd.time, "sleep", lambda *_a, **_k: None)
    # 2026 (newest, processed first): two decisions; 2025: one decision.
    _write_peep(rk_dir / "riigikohus_2026_peep.json", 2026, [("3-26-1/2", "111"), ("2-26-2/3", "222")])
    _write_peep(rk_dir / "riigikohus_2025_peep.json", 2025, [("1-25-9/9", "999")])
    # An index file so the stats-merge path is exercised too.
    (rk_dir / "RIIGIKOHUS_INDEX.json").write_text(
        json.dumps({"generated": "2026-01-01", "total_decisions": 3}, indent=2) + "\n", encoding="utf-8"
    )
    return rk_dir


def _stub_fetch_ok(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make ``fetch_decision_text`` return deterministic text; track calls."""
    seen: list[str] = []

    def _fake(case_nr, *, use_cache=True, session_fetched=None):  # noqa: ARG001
        if session_fetched is not None:
            session_fetched[:] = [True]
        seen.append(case_nr)
        return f"FULL TEXT FOR {case_nr} " + "x" * 80

    monkeypatch.setattr(gcd, "fetch_decision_text", _fake)
    return seen


class TestEnrichFullText:
    def test_limit_one_enriches_exactly_first_decision(
        self, _fake_rk_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _stub_fetch_ok(monkeypatch)
        stats = gcd.enrich_full_text(limit=1)
        assert stats["fetch_attempted"] == 1
        assert stats["fetch_succeeded"] == 1
        # Only the first decision of the newest year file gets text.
        assert seen == ["3-26-1/2"]

        doc_2026 = json.loads((_fake_rk_dir / "riigikohus_2026_peep.json").read_text())
        decs_2026 = [n for n in doc_2026["@graph"] if "estleg:CourtDecision" in n.get("@type", [])]
        with_text = [n for n in decs_2026 if "estleg:legalText" in n]
        assert len(with_text) == 1
        node = with_text[0]
        # Plain string literal — NOT a {"@value": ..., "@language": ...} dict.
        assert isinstance(node["estleg:legalText"], str)
        assert node["estleg:legalText"].startswith("FULL TEXT FOR 3-26-1/2")
        assert node["estleg:caseNumber"] == "3-26-1/2"

        # The other decision in 2026 and the one in 2025 are untouched.
        assert all("estleg:legalText" not in n for n in decs_2026 if n is not node)
        doc_2025 = json.loads((_fake_rk_dir / "riigikohus_2025_peep.json").read_text())
        decs_2025 = [n for n in doc_2025["@graph"] if "estleg:CourtDecision" in n.get("@type", [])]
        assert all("estleg:legalText" not in n for n in decs_2025)

    def test_limit_all_enriches_every_decision(
        self, _fake_rk_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_fetch_ok(monkeypatch)
        stats = gcd.enrich_full_text(limit=0)  # 0 == all
        assert stats["fetch_attempted"] == 3
        assert stats["fetch_succeeded"] == 3
        assert stats["with_full_text_total"] == 3
        assert stats["without_full_text_total"] == 0
        for fname in ("riigikohus_2026_peep.json", "riigikohus_2025_peep.json"):
            doc = json.loads((_fake_rk_dir / fname).read_text())
            decs = [n for n in doc["@graph"] if "estleg:CourtDecision" in n.get("@type", [])]
            assert decs and all(isinstance(n.get("estleg:legalText"), str) for n in decs)

    def test_already_having_legaltext_is_skipped(
        self, _fake_rk_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pre-stamp the first 2026 decision with legalText.
        doc = json.loads((_fake_rk_dir / "riigikohus_2026_peep.json").read_text())
        for n in doc["@graph"]:
            if n.get("estleg:caseNumber") == "3-26-1/2":
                n["estleg:legalText"] = "PRE-EXISTING"
        (_fake_rk_dir / "riigikohus_2026_peep.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

        seen = _stub_fetch_ok(monkeypatch)
        stats = gcd.enrich_full_text(limit=1)
        # The pre-stamped one is skipped; the next decision is fetched instead.
        assert seen == ["2-26-2/3"]
        assert stats["fetch_succeeded"] == 1
        doc = json.loads((_fake_rk_dir / "riigikohus_2026_peep.json").read_text())
        by_case = {n.get("estleg:caseNumber"): n for n in doc["@graph"] if "estleg:caseNumber" in n}
        assert by_case["3-26-1/2"]["estleg:legalText"] == "PRE-EXISTING"
        assert by_case["2-26-2/3"]["estleg:legalText"].startswith("FULL TEXT FOR 2-26-2/3")

    def test_failed_fetch_does_not_add_property(
        self, _fake_rk_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fake(case_nr, *, use_cache=True, session_fetched=None):  # noqa: ARG001
            if session_fetched is not None:
                session_fetched[:] = [True]
            return None  # simulate a non-decision / transient-failure response

        monkeypatch.setattr(gcd, "fetch_decision_text", _fake)
        stats = gcd.enrich_full_text(limit=2)
        assert stats["fetch_attempted"] == 2
        assert stats["fetch_succeeded"] == 0
        assert stats["fetch_failed"] == 2
        doc = json.loads((_fake_rk_dir / "riigikohus_2026_peep.json").read_text())
        assert all("estleg:legalText" not in n for n in doc["@graph"])

    def test_cli_main_without_flag_does_not_touch_legaltext(
        self, _fake_rk_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default behaviour: no --fetch-full-text → enrich_full_text never runs."""
        called: list[bool] = []
        monkeypatch.setattr(
            gcd, "enrich_full_text",
            lambda *a, **k: called.append(True) or {},  # noqa: ARG005
        )
        # Stub out the heavy scrape path so plain main() returns fast.
        monkeypatch.setattr(gcd, "fetch_year", lambda *_a, **_k: [])
        monkeypatch.setattr(gcd, "save_json", lambda *_a, **_k: None)
        gcd.main(["--full-text-limit", "5"])  # flag absent
        assert called == []
        # And the peep files retain no estleg:legalText.
        for fname in ("riigikohus_2026_peep.json", "riigikohus_2025_peep.json"):
            doc = json.loads((_fake_rk_dir / fname).read_text())
            assert all("estleg:legalText" not in n for n in doc["@graph"])

    def test_cli_main_with_flag_dispatches_enrichment(
        self, _fake_rk_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _stub_fetch_ok(monkeypatch)
        gcd.main(["--fetch-full-text", "--full-text-limit", "1"])
        assert seen == ["3-26-1/2"]
        doc = json.loads((_fake_rk_dir / "riigikohus_2026_peep.json").read_text())
        with_text = [n for n in doc["@graph"] if "estleg:legalText" in n]
        assert len(with_text) == 1 and isinstance(with_text[0]["estleg:legalText"], str)
        # The index file was updated with coverage stats.
        index = json.loads((_fake_rk_dir / "RIIGIKOHUS_INDEX.json").read_text())
        assert index["full_text"]["decisions_with_full_text"] == 1
        assert index["full_text"]["property"] == "estleg:legalText"
