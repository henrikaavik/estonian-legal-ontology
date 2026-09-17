"""HTTP-transport security/config tests.

These exercise the DNS-rebinding policy and the FastMCP settings wiring of the
streamable-HTTP transport. They need only ``mcp`` installed (no corpus), so
they run unconditionally -- unlike the corpus-backed suites.

Regression coverage for the remote ``/mcp`` returning ``421 Invalid Host
header`` behind a reverse proxy, and for ESTLEG_HOST never reaching FastMCP's
own settings (it used to go to uvicorn only).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from estleg_mcp import server


@pytest.fixture(autouse=True)
def fresh_server(monkeypatch):
    # App/session-manager construction differs between SDK majors; isolate it.
    monkeypatch.setattr(server, "mcp", server.MCPServer("estleg-test"))


def test_transport_security_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ESTLEG_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("ESTLEG_ALLOWED_ORIGINS", raising=False)
    ts = server._transport_security()
    # Protection OFF so a proxied public Host is not answered with 421.
    assert ts.enable_dns_rebinding_protection is False
    assert ts.allowed_hosts == []


def test_transport_security_enabled_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ESTLEG_ALLOWED_HOSTS", "estleg.sixtyfour.ee, localhost:*")
    monkeypatch.setenv("ESTLEG_ALLOWED_ORIGINS", "https://estleg.sixtyfour.ee")
    ts = server._transport_security()
    assert ts.enable_dns_rebinding_protection is True
    assert ts.allowed_hosts == ["estleg.sixtyfour.ee", "localhost:*"]
    assert ts.allowed_origins == ["https://estleg.sixtyfour.ee"]


def test_allowed_origins_ignored_without_hosts(monkeypatch) -> None:
    # Origins alone must NOT enable protection (that would 421 every request
    # because the host allow-list would be empty).
    monkeypatch.delenv("ESTLEG_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("ESTLEG_ALLOWED_ORIGINS", "https://example.com")
    ts = server._transport_security()
    assert ts.enable_dns_rebinding_protection is False


def test_build_http_app_applies_settings_and_is_reachable(monkeypatch) -> None:
    monkeypatch.delenv("ESTLEG_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("ESTLEG_HOST", "0.0.0.0")
    monkeypatch.setenv("ESTLEG_PORT", "9100")
    app = server._build_http_app()

    # The session manager that serves /mcp carries the (off-by-default) policy,
    # so a proxied Host is accepted rather than rejected with 421.
    sm = server.mcp.session_manager
    assert sm is not None
    assert sm.security_settings is not None
    assert sm.security_settings.enable_dns_rebinding_protection is False
    # Unauthenticated health check is still mounted.
    assert any(getattr(r, "path", "") == "/healthz" for r in app.routes)
    with TestClient(app, base_url="http://estleg.sixtyfour.ee") as client:
        assert client.get("/healthz").text == "ok"
        # A protocol error is expected without a session; DNS rejection is not.
        response = client.post("/mcp", json={})
        assert response.status_code != 421


def test_http_bearer_gate_and_health(monkeypatch):
    monkeypatch.setenv("ESTLEG_TOKEN", "test-token")
    app = server._build_http_app()
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.post("/mcp", json={}).status_code == 401
        response = client.post(
            "/mcp", json={}, headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code != 401


def test_http_enforces_explicit_allowed_hosts(monkeypatch):
    monkeypatch.delenv("ESTLEG_TOKEN", raising=False)
    monkeypatch.setenv("ESTLEG_ALLOWED_HOSTS", "estleg.sixtyfour.ee")
    with TestClient(server._build_http_app(), base_url="http://untrusted.example") as client:
        response = client.post("/mcp", json={}, headers={"Content-Type": "application/json"})
        assert response.status_code == 421


def test_main_http_binds_configured_address(monkeypatch):
    import uvicorn

    monkeypatch.setenv("ESTLEG_TRANSPORT", "http")
    monkeypatch.setenv("ESTLEG_HOST", "0.0.0.0")
    monkeypatch.setenv("ESTLEG_PORT", "9100")
    monkeypatch.setattr(server, "check_provision_detection", lambda: None)
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    server.main()
    assert calls == [{"host": "0.0.0.0", "port": 9100}]


def test_registered_tools_are_exposed_by_sdk():
    # The other suites call Python functions directly. Exercise SDK registration
    # too, so an import-only major-version compatibility shim cannot pass alone.
    from estleg_mcp.server import search_laws

    server.mcp.add_tool(search_laws)
    tools = asyncio.run(server.mcp.list_tools())
    assert [tool.name for tool in tools] == ["search_laws"]


def test_http_protocol_initializes_and_calls_tool(monkeypatch):
    monkeypatch.delenv("ESTLEG_TOKEN", raising=False)
    monkeypatch.delenv("ESTLEG_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(server.data, "search_law_records", lambda query, limit: [])
    server.mcp.add_tool(server.search_laws)
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(server._build_http_app()) as client:
        initialized = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        })
        assert initialized.status_code == 200
        headers["Mcp-Session-Id"] = initialized.headers["mcp-session-id"]
        headers["MCP-Protocol-Version"] = "2025-06-18"
        client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        response = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "search_laws", "arguments": {"query": "missing"}},
        })
        assert response.status_code == 200
        payload = next(line[6:] for line in response.text.splitlines() if line.startswith("data: "))
        result = json.loads(payload)["result"]
        assert not result.get("isError")
        assert result["structuredContent"] == {"result": []}
