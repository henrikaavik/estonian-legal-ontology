from __future__ import annotations

import pytest

import riigiteataja_common
from riigiteataja_common import fetch_xml


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
