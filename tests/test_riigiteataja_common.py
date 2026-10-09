from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET

import pytest
import requests

from estleg import riigiteataja_common
from estleg.riigiteataja_common import (
    RTFormatError,
    build_metadata_url,
    build_xml_url,
    collect_text,
    fetch_act_metadata,
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
    """#691: per-act XML comes from the RT public API, not ``/akt/{id}.xml``.

    Since the 2026-06-01 RT relaunch the legacy path serves the Angular app
    shell (HTTP 200 ``text/html``). ``build_xml_url`` must map every act
    form the pipeline sees (search-API ``url``, corpus ``dcterms:source``,
    bare id) onto ``/public-api/api/v1/akt/{id}/xml``. The #389 invariant
    still holds: the id comes from the URL *path*, so a query string never
    leaks into it.
    """

    API = riigiteataja_common.BASE_URL + "/public-api/api/v1/akt"

    def test_relative_path_maps_to_public_api(self):
        assert build_xml_url("/akt/123") == f"{self.API}/123/xml"

    def test_search_api_url_form_maps_to_public_api(self):
        # The search API still returns ``url: "/akt/{id}.xml"``.
        assert build_xml_url("/akt/13118547.xml") == f"{self.API}/13118547/xml"

    def test_corpus_source_url_maps_to_public_api(self):
        assert (
            build_xml_url("https://www.riigiteataja.ee/akt/107052025017")
            == f"{self.API}/107052025017/xml"
        )

    def test_bare_id_maps_to_public_api(self):
        assert build_xml_url("107052025017") == f"{self.API}/107052025017/xml"
        assert build_xml_url(107052025017) == f"{self.API}/107052025017/xml"

    def test_public_api_url_is_idempotent(self):
        url = f"{self.API}/5/xml"
        assert build_xml_url(url) == url

    def test_query_string_never_lands_in_the_id(self):
        assert build_xml_url("/akt/123?version=3") == f"{self.API}/123/xml?version=3"
        assert build_xml_url("/akt/123.xml?version=3") == f"{self.API}/123/xml?version=3"

    def test_foreign_host_is_kept(self):
        assert (
            build_xml_url("https://example.test/akt/9?x=1")
            == "https://example.test/public-api/api/v1/akt/9/xml?x=1"
        )

    def test_metadata_url_is_the_json_sibling(self):
        assert build_metadata_url("/akt/123.xml") == f"{self.API}/123"
        assert (
            build_metadata_url("https://www.riigiteataja.ee/akt/107052025017")
            == f"{self.API}/107052025017"
        )

    def test_non_act_url_is_rejected(self):
        with pytest.raises(ValueError, match="not a Riigi Teataja act"):
            build_xml_url("https://www.riigiteataja.ee/otsingu_tulemused.html")

    def test_fetch_xml_uses_public_api_url(self, tmp_path, monkeypatch):
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
        assert captured["url"] == f"{self.API}/123/xml?version=3"


class _Resp:
    """Minimal ``requests.Response`` stand-in with status and headers."""

    def __init__(self, text: str, status: int = 200, content_type: str = "", headers=None):
        self.text = text
        self.status_code = status
        self.encoding = "utf-8"
        self.headers = {"Content-Type": content_type, **(headers or {})}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


_ANGULAR_SHELL = "<!doctype html><html lang=et><head><title>Riigi Teataja</title></head><body><app-root></app-root></body></html>"
_ACT_XML = "<oigusakt xmlns='tyviseadus_1_10.02.2010'><metaandmed/>" + ("<x/>" * 80) + "</oigusakt>"


class TestFetchXmlPublicApi:
    """#691: HTML is rejected loudly; transient failures are retried."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(riigiteataja_common.time, "sleep", lambda *_a, **_k: None)

    def test_html_shell_raises_rt_format_error_naming_the_url(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            riigiteataja_common.requests,
            "get",
            lambda *a, **k: _Resp(_ANGULAR_SHELL, content_type="text/html"),
        )
        with pytest.raises(RTFormatError, match=r"public-api/api/v1/akt/123/xml"):
            fetch_xml("/akt/123", cache_name="x", cache_dir=tmp_path, min_size=10)

    def test_html_body_without_html_content_type_is_still_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            riigiteataja_common.requests,
            "get",
            lambda *a, **k: _Resp(_ANGULAR_SHELL, content_type="application/octet-stream"),
        )
        with pytest.raises(RTFormatError, match="HTML instead of act data"):
            fetch_xml("/akt/123", cache_name="x", cache_dir=tmp_path, min_size=10)

    def test_octet_stream_xml_is_accepted_and_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            riigiteataja_common.requests,
            "get",
            lambda *a, **k: _Resp(_ACT_XML, content_type="application/octet-stream;charset=UTF-8"),
        )
        root = fetch_xml("/akt/1", cache_name="act1", cache_dir=tmp_path, min_size=10)
        assert root is not None and root.tag.endswith("oigusakt")
        assert (tmp_path / "act1.xml").is_file()

    def test_5xx_is_retried_then_succeeds(self, tmp_path, monkeypatch):
        responses = [_Resp("busy", status=503), _Resp(_ACT_XML)]
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            return responses.pop(0)

        monkeypatch.setattr(riigiteataja_common.requests, "get", fake_get)
        root = fetch_xml("/akt/1", cache_name="r", cache_dir=tmp_path, min_size=10)
        assert root is not None
        assert len(calls) == 2

    def test_connection_error_is_retried_then_returns_none(self, tmp_path, monkeypatch):
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            raise requests.ConnectionError("down")

        monkeypatch.setattr(riigiteataja_common.requests, "get", fake_get)
        assert fetch_xml("/akt/1", cache_name="r", cache_dir=tmp_path, max_retries=2) is None
        assert len(calls) == 3

    def test_strict_mode_propagates_network_errors(self, tmp_path, monkeypatch):
        def fake_get(*a, **k):
            raise requests.Timeout("slow")

        monkeypatch.setattr(riigiteataja_common.requests, "get", fake_get)
        with pytest.raises(requests.Timeout):
            fetch_xml("/akt/1", cache_name="r", cache_dir=tmp_path, max_retries=0, strict=True)

    def test_4xx_is_not_retried(self, tmp_path, monkeypatch):
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            return _Resp("nope", status=404)

        monkeypatch.setattr(riigiteataja_common.requests, "get", fake_get)
        assert fetch_xml("/akt/1", cache_name="r", cache_dir=tmp_path) is None
        assert len(calls) == 1

    def test_redirect_to_login_is_none_unless_strict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            riigiteataja_common.requests,
            "get",
            lambda *a, **k: _Resp("", status=302, headers={"Location": "http://akt-teenus/oauth2"}),
        )
        assert fetch_xml("/akt/1", cache_name="r", cache_dir=tmp_path) is None
        with pytest.raises(RTFormatError, match="redirected"):
            fetch_xml("/akt/1", cache_name="r", cache_dir=tmp_path, refresh=True, strict=True)


class TestFetchActMetadata:
    """#691: the JSON metadata sibling (``GET /public-api/api/v1/akt/{id}``)."""

    PAYLOAD = {
        "kehtivId": 107052025017,
        "grupiId": 339043,
        "tekstiliik": "terviktekst",
        "dokumentliik": "seadus",
        "aktiStaatus": "KEHTIV",
        "tolkeSeosId": 516052025002,
        "aktiParameetrid": {
            "pealkiri": "Perekonnaseadus",
            "lyhend": "PKS",
            "kehtivuseAlgus": "2024-12-31T22:00:00Z",
            "kehtivuseLopp": None,
            "avaldamiseKuupaev": "2025-05-06T21:00:00Z",
        },
    }

    def test_parses_fields_and_converts_utc_instants_to_tallinn_dates(self, monkeypatch):
        captured = {}

        def fake_get(url, *a, **k):
            captured["url"] = url
            return _Resp(json.dumps(self.PAYLOAD), content_type="application/json")

        monkeypatch.setattr(riigiteataja_common.requests, "get", fake_get)
        meta = fetch_act_metadata("https://www.riigiteataja.ee/akt/107052025017")
        assert captured["url"].endswith("/public-api/api/v1/akt/107052025017")
        assert meta["actId"] == "107052025017"
        assert meta["currentId"] == "107052025017"
        assert meta["terviktekstId"] == "339043"
        assert meta["abbreviation"] == "PKS"
        assert meta["title"] == "Perekonnaseadus"
        # Midnight Tallinn served as a UTC instant must not shift a day back.
        assert meta["entryIntoForce"] == "2025-01-01"
        assert meta["publishedDate"] == "2025-05-07"
        assert meta["repealDate"] is None
        assert meta["translationId"] == "516052025002"

    def test_html_metadata_body_raises(self, monkeypatch):
        monkeypatch.setattr(
            riigiteataja_common.requests,
            "get",
            lambda *a, **k: _Resp(_ANGULAR_SHELL, content_type="text/html"),
        )
        with pytest.raises(RTFormatError):
            fetch_act_metadata("123")

    def test_non_json_body_raises(self, monkeypatch):
        monkeypatch.setattr(
            riigiteataja_common.requests,
            "get",
            lambda *a, **k: _Resp("not json", content_type="application/json"),
        )
        with pytest.raises(RTFormatError, match="not JSON"):
            fetch_act_metadata("123")


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


def test_strip_html_tags_does_not_reconstitute_script_after_unescape():
    """#554: unescape-after-strip must not revive ``&lt;script&gt;`` tags."""
    raw = "ohutu &lt;script&gt;alert(1)&lt;/script&gt; tekst"
    out = riigiteataja_common.strip_html_tags(raw)
    assert "<script" not in out.lower()
    assert "alert(1)" in out
    assert "ohutu" in out


def test_ct_flattens_sup_children_instead_of_truncating():
    """#694: a title with a <sup> child must not be cut at the child."""
    import xml.etree.ElementTree as ET

    from estleg.riigiteataja_common import ct

    el = ET.fromstring(
        "<akt><pealkiri>§ 217<sup>2</sup>. Avalik kord</pealkiri></akt>"
    )
    assert ct(el, "pealkiri") == "§ 217². Avalik kord"
    plain = ET.fromstring("<akt><pealkiri>  Plain  </pealkiri><muu>x</muu></akt>")
    assert ct(plain, "pealkiri") == "Plain"
    assert ct(plain, "puudub") is None
