"""HTTP-transport security/config tests.

These exercise the DNS-rebinding policy and the FastMCP settings wiring of the
streamable-HTTP transport. They need only ``mcp`` installed (no corpus), so
they run unconditionally -- unlike the corpus-backed suites.

Regression coverage for the remote ``/mcp`` returning ``421 Invalid Host
header`` behind a reverse proxy, and for ESTLEG_HOST never reaching FastMCP's
own settings (it used to go to uvicorn only).
"""

from __future__ import annotations

from estleg_mcp import server


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
    # Drop any cached session manager so the build re-reads settings.
    server.mcp._session_manager = None

    app = server._build_http_app()

    # ESTLEG_HOST/PORT now reach FastMCP's own settings, not just uvicorn.
    assert server.mcp.settings.host == "0.0.0.0"
    assert server.mcp.settings.port == 9100
    # The session manager that serves /mcp carries the (off-by-default) policy,
    # so a proxied Host is accepted rather than rejected with 421.
    sm = server.mcp._session_manager
    assert sm is not None
    assert sm.security_settings is not None
    assert sm.security_settings.enable_dns_rebinding_protection is False
    # Unauthenticated health check is still mounted.
    assert any(getattr(r, "path", "") == "/healthz" for r in app.routes)
