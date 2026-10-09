"""#469 / #691: fail CI if the Riigi Teataja XML schema silently drifts.

The law generator keys off local-names (``oigusakt``, ``paragrahv``,
``paragrahvNr``, ``peatykk``). An RT format change that dropped those
tags would still parse and emit stubs. The offline checks load the
committed KarS cache and assert the structural contract
``generate_all_laws`` depends on, plus the pinned schema identity
(``tyviseadus_1_10.02.2010``).

The live check (#691) GETs one act through ``fetch_xml`` from the RT
public API. It is opt-in so the default suite stays offline::

    ESTLEG_LIVE_CANARY=1 python3 -m pytest -q tests/test_rt_schema_canary.py -k live

CI runs the same probe as ``check_rt_staleness.py --schema-canary`` in
the ``rt-staleness`` job, where "RT unreachable" is a warning and "RT
answered with a changed format" is a failure.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import requests

from estleg import generate_all_laws as gal
from estleg import riigiteataja_common as rtc

REPO = Path(__file__).resolve().parent.parent

KARS_XML = REPO / "data" / "riigiteataja" / "karistusseadustik.xml"

_REQUIRED_LOCALNAMES = {
    "oigusakt",
    "metaandmed",
    "paragrahv",
    "paragrahvNr",
    "peatykk",
}


def _localnames(root: ET.Element) -> set[str]:
    names = {gal.ln(root.tag)}
    for el in root.iter():
        names.add(gal.ln(el.tag))
    return names


def test_committed_kars_xml_is_a_trustworthy_act_root() -> None:
    assert KARS_XML.is_file(), "KarS RT cache missing — cannot canary the schema"
    root = ET.fromstring(KARS_XML.read_bytes())
    assert gal._is_trustworthy_xml_root(root)
    assert gal.ln(root.tag) == "oigusakt"


def test_committed_kars_xml_still_has_generator_contract_tags() -> None:
    """#469: RT format drift that drops paragraph/chapter tags must fail CI."""
    root = ET.fromstring(KARS_XML.read_bytes())
    names = _localnames(root)
    missing = sorted(_REQUIRED_LOCALNAMES - names)
    assert missing == [], f"RT XML lost tags the law generator requires: {missing}"
    paragrahvid = [el for el in root.iter() if gal.ln(el.tag) == "paragrahv"]
    assert len(paragrahvid) >= 10
    numbered = [p for p in paragrahvid if gal.ct(p, "paragrahvNr")]
    assert numbered, "paragrahv elements no longer carry paragrahvNr"


def test_html_error_root_is_not_trusted() -> None:
    html = ET.fromstring("<html><body>error</body></html>")
    assert gal._is_trustworthy_xml_root(html) is False


def test_committed_kars_xml_pins_the_schema_identity() -> None:
    """#691: the post-relaunch public API still serves this schema."""
    root = ET.fromstring(KARS_XML.read_bytes())
    assert rtc.RT_LAW_XML_SCHEMA == "tyviseadus_1_10.02.2010"
    assert rtc.xml_schema_name(root) == rtc.RT_LAW_XML_SCHEMA
    assert rtc.check_act_xml_contract(root) == []


def test_contract_check_flags_a_schema_change() -> None:
    root = ET.fromstring(
        "<oigusakt xmlns='tyviseadus_2_01.01.2027'><metaandmed/><peatykk>"
        "<paragrahv><paragrahvNr>1</paragrahvNr></paragrahv></peatykk></oigusakt>"
    )
    problems = rtc.check_act_xml_contract(root)
    assert any("schema namespace" in p for p in problems)


# --- live canary classification (mocked; offline) --------------------------


class _Resp:
    def __init__(self, text: str, status: int = 200, content_type: str = "application/octet-stream"):
        self.text = text
        self.status_code = status
        self.encoding = "utf-8"
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rtc.time, "sleep", lambda *_a, **_k: None)


def _serve(monkeypatch: pytest.MonkeyPatch, factory) -> None:
    monkeypatch.setattr(rtc.requests, "get", lambda *a, **k: factory())


@pytest.mark.usefixtures("_no_sleep")
def test_canary_ok_on_fixture_xml(monkeypatch: pytest.MonkeyPatch) -> None:
    body = KARS_XML.read_text(encoding="utf-8")
    _serve(monkeypatch, lambda: _Resp(body))
    result = rtc.run_live_schema_canary()
    assert result.status == rtc.CANARY_OK, result.detail


@pytest.mark.usefixtures("_no_sleep")
def test_canary_html_shell_is_format_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda: _Resp("<!doctype html><html><body></body></html>", content_type="text/html"))
    result = rtc.run_live_schema_canary()
    assert result.status == rtc.CANARY_FORMAT_CHANGED
    assert "HTML" in result.detail


@pytest.mark.usefixtures("_no_sleep")
def test_canary_404_is_format_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda: _Resp("gone", status=404))
    assert rtc.run_live_schema_canary().status == rtc.CANARY_FORMAT_CHANGED


@pytest.mark.usefixtures("_no_sleep")
def test_canary_network_error_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise requests.ConnectionError("no route")

    _serve(monkeypatch, boom)
    assert rtc.run_live_schema_canary().status == rtc.CANARY_UNREACHABLE


@pytest.mark.usefixtures("_no_sleep")
def test_canary_5xx_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda: _Resp("down", status=503))
    assert rtc.run_live_schema_canary().status == rtc.CANARY_UNREACHABLE


# --- live canary (network; opt-in) ------------------------------------------

LIVE = os.environ.get("ESTLEG_LIVE_CANARY") == "1"


@pytest.mark.skipif(not LIVE, reason="set ESTLEG_LIVE_CANARY=1 to GET one live RT act")
def test_live_rt_act_xml_matches_pinned_schema() -> None:
    """#691: GET one live act's XML via ``fetch_xml`` and check the contract."""
    result = rtc.run_live_schema_canary()
    if result.status == rtc.CANARY_UNREACHABLE:
        pytest.skip(f"RT unreachable (not a format change): {result.detail}")
    assert result.status == rtc.CANARY_OK, f"{result.url}: {result.detail}"
