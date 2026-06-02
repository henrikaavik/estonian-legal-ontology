from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import pytest

import riigiteataja_common
from riigiteataja_common import (
    build_xml_url,
    collect_text,
    fetch_xml,
    parse_html_konteiner,
)


class _FailingResponse:
    def raise_for_status(self):
        raise RuntimeError("boom")


def test_fetch_acts_raises_on_source_list_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        return _FailingResponse()

    monkeypatch.setattr(riigiteataja_common.requests, "get", fail_get)

    with pytest.raises(riigiteataja_common.SourceListFetchError):
        list(riigiteataja_common.fetch_acts("määrus", max_retries=0))


def test_fetch_acts_allow_partial_stops_without_results(monkeypatch):
    def fail_get(*args, **kwargs):
        return _FailingResponse()

    monkeypatch.setattr(riigiteataja_common.requests, "get", fail_get)

    assert list(
        riigiteataja_common.fetch_acts(
            "määrus",
            allow_partial=True,
            max_retries=0,
        )
    ) == []


class _GetSpy:
    """Records whether (and how often) ``requests.get`` was invoked. Returns
    a benign empty-XML response so the write path stays inert when reached.
    ``fetch_xml`` swallows fetch exceptions, so we assert on the call count
    rather than relying on a raised error propagating out.
    """

    def __init__(self):
        self.calls = 0

    class _Resp:
        text = "<akt><metaandmed></metaandmed></akt>"
        encoding = "utf-8"

        def raise_for_status(self):
            pass

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._Resp()


class TestFetchXmlCacheThreshold:
    """Issue #296 regression: the read guard must reuse any cached file the
    write guard would have accepted. The old read guard (``> 1000`` bytes)
    disagreed with the write guard (``>= min_size`` == 200 bytes), so a
    200–1000-byte cached XML was persisted but never reused and got
    re-fetched on every run.
    """

    def test_mid_size_cached_xml_is_reused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(riigiteataja_common, "DATA_DIR", tmp_path)
        spy = _GetSpy()
        monkeypatch.setattr(riigiteataja_common.requests, "get", spy)

        # Build a valid XML payload between 200 and 1000 bytes — exactly the
        # window the old `> 1000` read guard skipped (forcing a re-fetch).
        body = "<sisu>" + ("x" * 400) + "</sisu>"
        xml_text = (
            "<akt><metaandmed><terviktekstID>123</terviktekstID></metaandmed>"
            f"{body}</akt>"
        )
        assert 200 <= len(xml_text.encode("utf-8")) <= 1000

        (tmp_path / "reg_mid.xml").write_text(xml_text, encoding="utf-8")

        root = fetch_xml("/akt/123", cache_name="reg_mid", min_size=200)
        assert root is not None
        assert root.tag == "akt"
        # The fix: the cached file was served, so no network fetch happened.
        assert spy.calls == 0

    def test_subthreshold_cache_is_not_reused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(riigiteataja_common, "DATA_DIR", tmp_path)
        spy = _GetSpy()
        monkeypatch.setattr(riigiteataja_common.requests, "get", spy)

        cache_path = tmp_path / "reg_tiny.xml"
        cache_path.write_text("<akt/>", encoding="utf-8")  # < 200 bytes
        assert cache_path.stat().st_size < 200

        fetch_xml("/akt/9", cache_name="reg_tiny", min_size=200)
        # Too-small cache is skipped -> a fresh fetch is attempted.
        assert spy.calls == 1

    def test_large_cache_reused_without_fetch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(riigiteataja_common, "DATA_DIR", tmp_path)
        spy = _GetSpy()
        monkeypatch.setattr(riigiteataja_common.requests, "get", spy)

        (tmp_path / "reg_big.xml").write_text(
            "<akt>" + ("y" * 1200) + "</akt>", encoding="utf-8"
        )
        root = fetch_xml("/akt/1", cache_name="reg_big", min_size=200)
        assert root is not None
        assert spy.calls == 0

    def test_refresh_bypasses_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(riigiteataja_common, "DATA_DIR", tmp_path)
        spy = _GetSpy()
        monkeypatch.setattr(riigiteataja_common.requests, "get", spy)

        (tmp_path / "reg_refresh.xml").write_text(
            "<akt>" + ("y" * 1200) + "</akt>", encoding="utf-8"
        )
        # refresh=True ignores the cache and forces a network fetch.
        fetch_xml("/akt/1", cache_name="reg_refresh", refresh=True, min_size=200)
        assert spy.calls == 1


class TestHeadingReNoCatastrophicBacktracking:
    """Issue #347 regression: the bold-§ heading regex must not exhibit
    catastrophic backtracking on a malformed pre-2010 HTMLKonteiner where a
    ``<b>§ N. `` opener is followed by a long run of whitespace and never
    closed with ``</b>``. The old pattern paired a lazy ``[^<]{0,200}?`` with
    a trailing ``\\s*`` before ``</b>``; both matched spaces, so the engine
    tried every whitespace partition — O(N²), ~1s for N≈2000 spaces.
    """

    def test_unclosed_bold_long_whitespace_returns_promptly(self):
        # A pathological input for the OLD regex: an unclosed <b> with a long
        # whitespace tail. With the fix this matches no heading and returns
        # essentially instantly; the assertion guards against a regression
        # reintroducing the quadratic blow-up.
        html_text = "<b>§ 1. " + (" " * 4000) + "no closing tag"
        start = time.perf_counter()
        preamble, paragraphs = parse_html_konteiner(html_text)
        elapsed = time.perf_counter() - start
        # Generous bound: the fixed linear scan is sub-millisecond, while the
        # quadratic version needed ~1s at N=2000 (so ~4s at N=4000).
        assert elapsed < 0.5
        # No closing </b> -> the bold-heading pattern yields no match; the
        # loose fallback also fails (no titlecase title), so the whole body
        # becomes the preamble and there are no paragraphs.
        assert paragraphs == []

    def test_well_formed_heading_still_parsed_and_title_stripped(self):
        # Removing the trailing ``\\s*`` must not regress normal parsing:
        # the title is captured and stripped of trailing whitespace (the
        # downstream ``.strip()`` at :296 handles space before ``</b>``).
        html_text = (
            "<p>preamble</p>"
            "<b>§ 1. Reguleerimisala   </b>"
            "<p>Body of section one.</p>"
            "<b>§ 2. Mõisted</b>"
            "<p>Body of section two.</p>"
        )
        preamble, paragraphs = parse_html_konteiner(html_text)
        assert preamble == "preamble"
        assert [p["nr"] for p in paragraphs] == ["1", "2"]
        assert paragraphs[0]["title"] == "Reguleerimisala"
        assert paragraphs[1]["title"] == "Mõisted"
        assert paragraphs[0]["text"] == "Body of section one."


class TestBuildXmlUrl:
    """Issue #389 batch: ``.xml`` must be appended to the URL *path*, not after
    a query string. ``build_xml_url`` is a pure function, so the URL-construction
    logic is verified here with no network access.
    """

    def test_relative_path_gets_base_and_xml_suffix(self):
        assert (
            build_xml_url("/akt/123")
            == riigiteataja_common.BASE_URL + "/akt/123.xml"
        )

    def test_query_string_path_appends_xml_before_query(self):
        # The bug: a raw ``url + '.xml'`` produced ``...?version=3.xml``.
        assert (
            build_xml_url("/akt/123?version=3")
            == riigiteataja_common.BASE_URL + "/akt/123.xml?version=3"
        )

    def test_existing_xml_suffix_not_doubled(self):
        assert (
            build_xml_url("/akt/123.xml")
            == riigiteataja_common.BASE_URL + "/akt/123.xml"
        )

    def test_existing_xml_suffix_with_query_not_doubled(self):
        assert (
            build_xml_url("/akt/123.xml?version=3")
            == riigiteataja_common.BASE_URL + "/akt/123.xml?version=3"
        )

    def test_absolute_url_used_as_is(self):
        assert (
            build_xml_url("https://example.test/akt/9?x=1")
            == "https://example.test/akt/9.xml?x=1"
        )

    def test_fetch_xml_uses_path_safe_url(self, tmp_path, monkeypatch):
        # End-to-end: fetch_xml must hand requests.get a path-safe URL even
        # when the act path carries a query string.
        monkeypatch.setattr(riigiteataja_common, "DATA_DIR", tmp_path)
        captured: dict[str, str] = {}

        class _Resp:
            text = "<akt><metaandmed></metaandmed></akt>"
            encoding = "utf-8"

            def raise_for_status(self):
                pass

        def fake_get(url, *args, **kwargs):
            captured["url"] = url
            return _Resp()

        monkeypatch.setattr(riigiteataja_common.requests, "get", fake_get)
        fetch_xml("/akt/123?version=3", cache_name="reg_q", min_size=10)
        assert (
            captured["url"]
            == riigiteataja_common.BASE_URL + "/akt/123.xml?version=3"
        )


def _wrap_loige(text: str) -> ET.Element:
    """Build a minimal ``<sisu><loige>text</loige></sisu>`` element."""
    root = ET.Element("sisu")
    loige = ET.SubElement(root, "loige")
    loige.text = text
    return root


class TestCollectTextBoundaryCut:
    """Issue #368 regression: ``collect_text`` must cut summaries on a
    sentence/word boundary rather than a raw ``[:max_len]`` slice that split
    tokens mid-word (e.g. ``...kohustatud arvut``).
    """

    def test_short_text_returned_verbatim(self):
        el = _wrap_loige("Lühike lause.")
        assert collect_text(el, max_len=500) == "Lühike lause."

    def test_cut_prefers_sentence_boundary(self):
        # Two sentences; the boundary after the first falls in the back ~30%
        # window, so the cut lands there and the second sentence is dropped.
        first = "A" * 380 + "."
        second = " " + "B" * 200 + "."
        el = _wrap_loige(first + second)
        out = collect_text(el, max_len=500)
        assert out == first
        assert "B" not in out

    def test_cut_falls_back_to_word_boundary(self):
        # No sentence terminator in range -> fall back to the last space so no
        # token is split. The result is shorter than max_len and ends on a
        # whole word (plus an ellipsis marker), never mid-token.
        words = ("kohustatud " * 60).strip()  # ~660 chars, single run, no '.'
        el = _wrap_loige(words)
        out = collect_text(el, max_len=500)
        assert len(out) <= 500
        assert out.endswith("…")
        body = out[:-1]
        # The final retained token is a complete word, not a truncated stub.
        assert body.split()[-1] == "kohustatud"

    def test_result_never_exceeds_max_len(self):
        el = _wrap_loige("Sõna " * 400)  # 2000 chars
        out = collect_text(el, max_len=500)
        assert len(out) <= 500
