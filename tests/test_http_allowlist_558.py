"""Host allow-list for scraper HTTP GETs (issue #558).

``estleg_common.allowed_get`` must refuse any host outside the official
legal-source set and default ``allow_redirects`` to False so a 30x cannot
bounce a fetch off-host. Fetched bodies are hashed with
``sha256_hex`` and stored as ``estleg:contentHash`` (#558).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import estleg_common
from estleg_common import ALLOWED_HTTP_HOSTS, allowed_get, assert_allowed_http_url


class _Resp:
    status_code = 200
    text = "ok"
    url = ""
    headers: dict = {}
    is_redirect = False
    is_permanent_redirect = False

    def __init__(self, url: str = "") -> None:
        self.url = url

    def raise_for_status(self) -> None:
        return None


def test_allowed_host_calls_requests_get(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_get(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Resp(url)

    monkeypatch.setattr(estleg_common.requests, "get", fake_get)
    resp = allowed_get("https://www.riigiteataja.ee/akt/1", timeout=5)
    assert resp.text == "ok"
    assert captured["url"] == "https://www.riigiteataja.ee/akt/1"
    assert captured["kwargs"]["timeout"] == 5
    assert captured["kwargs"]["allow_redirects"] is False


@pytest.mark.parametrize("host", sorted(ALLOWED_HTTP_HOSTS))
def test_each_allowlisted_host_is_accepted(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def fake_get(url: str, **_kwargs):
        called.append(url)
        return _Resp(url)

    monkeypatch.setattr(estleg_common.requests, "get", fake_get)
    url = f"https://{host}/path"
    assert allowed_get(url).text == "ok"
    assert called == [url]


def test_disallowed_host_raises_and_does_not_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("requests.get must not be called for a disallowed host")

    monkeypatch.setattr(estleg_common.requests, "get", boom)
    with pytest.raises(ValueError, match="allow-list"):
        allowed_get("https://evil.example/steal")


def test_relative_url_and_non_http_scheme_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("requests.get must not be called")

    monkeypatch.setattr(estleg_common.requests, "get", boom)
    with pytest.raises(ValueError, match="allow-list|scheme"):
        allowed_get("/akt/123")
    with pytest.raises(ValueError, match="scheme"):
        allowed_get("file:///etc/passwd")


def test_trailing_dns_dot_still_matches_allowlist() -> None:
    assert assert_allowed_http_url("https://www.riigiteataja.ee./akt/1") == (
        "www.riigiteataja.ee"
    )


def test_allow_redirects_true_rejects_off_host_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RedirectResp(_Resp):
        status_code = 302
        is_redirect = True
        headers = {"Location": "https://evil.example/next"}

    def fake_get(url: str, **kwargs):
        resp = _RedirectResp(url)
        hooks = (kwargs.get("hooks") or {}).get("response") or []
        if callable(hooks):
            hooks = [hooks]
        for hook in hooks:
            hook(resp)
        return resp

    monkeypatch.setattr(estleg_common.requests, "get", fake_get)
    with pytest.raises(ValueError, match="allow-list"):
        allowed_get(
            "https://www.riigiteataja.ee/old",
            allow_redirects=True,
        )


def test_sha256_hex_is_stable() -> None:
    from estleg_common import sha256_hex

    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert len(sha256_hex("abc")) == 64
    assert sha256_hex("abc") != sha256_hex("abd")


def test_record_fetch_hash_writes_manifest(tmp_path: Path) -> None:
    from estleg_common import record_fetch_hash, sha256_hex

    dest = tmp_path / "fetch_content_hashes.json"
    digest = sha256_hex("body")
    record_fetch_hash("demo", digest, source="https://www.riigiteataja.ee/x", nbytes=4, path=dest)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["demo"]["sha256"] == digest
    assert payload["demo"]["bytes"] == 4


def test_committed_kars_content_hash_matches_cached_xml() -> None:
    """#558: published KarS act node carries sha256 of the cached RT XML."""
    import json
    from pathlib import Path

    from estleg_common import sha256_hex

    repo = Path(__file__).resolve().parents[1]
    xml = (repo / "data" / "riigiteataja" / "karistusseadustik.xml").read_bytes()
    peep = json.loads(
        (repo / "krr_outputs" / "karistusseadustik_osa1_peep.json").read_text()
    )
    root = next(n for n in peep["@graph"] if n.get("@id") == "estleg:KARIST_2_Osa1_1_87")
    assert root.get("estleg:contentHash") == sha256_hex(xml)
