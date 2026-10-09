"""#691 follow-up: redaction XML is fetched from the public API, not /akt/{id}.xml."""

from __future__ import annotations

from pathlib import Path

from estleg import generate_provision_versions as gpv

MINIMAL_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<oigusakt xmlns="tyviseadus_1_10.02.2010"><metaandmed><globaalID>122122025002</globaalID>'
    "</metaandmed><!-- " + "padding so the body passes the 200-byte sanity check " * 4 + "--></oigusakt>"
)


class _Resp:
    status_code = 200
    encoding = "utf-8"
    apparent_encoding = "utf-8"
    content = MINIMAL_XML.encode("utf-8")
    text = MINIMAL_XML

    def raise_for_status(self) -> None:
        return None


def test_redaction_xml_is_fetched_from_the_public_api(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(gpv.requests, "get", fake_get)
    monkeypatch.setattr(gpv, "_xml_cache_path", lambda slug, gid: tmp_path / f"{slug}__g{gid}.xml")
    red = gpv.Redaction(global_id="122122025002", valid_from="2025-12-22", valid_to=None, url="/akt/122122025002.xml")
    root = gpv.fetch_redaction_xml("tulumaksuseadus", red, sleep=0)
    assert root is not None
    assert seen == ["https://www.riigiteataja.ee/public-api/api/v1/akt/122122025002/xml"]
    assert ".xml" not in seen[0].rsplit("/", 1)[-1]
