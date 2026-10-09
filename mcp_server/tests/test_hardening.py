"""#714 hardening: access control, audit line, snapshot envelope, language.

These run without the corpus (the data layer is monkeypatched where a tool is
called), so the security contract is checked on every CI run.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from estleg_mcp import audit, data, i18n, provenance, security, server

_ACCESS_ENV = (
    "ESTLEG_TOKEN",
    "ESTLEG_TOKENS",
    "ESTLEG_ALLOW_ANONYMOUS_HTTP",
    "ESTLEG_RATE_LIMIT",
    "ESTLEG_RATE_BURST",
    "ESTLEG_ALLOWED_HOSTS",
)
EXPECTED_TOOLS = {
    "search_laws", "get_law", "get_provision", "who_references", "references_of",
    "drafts_affecting_law", "court_decisions_for_law", "sanctions_for_law",
    "competent_authority_for_law", "transposition", "provision_history",
    "regulations_for_law", "get_regulation", "regulations_by_issuer",
    "define_term", "laws_for_subject", "amendment_history",
    "eu_case_law_for_directive", "harmonisation_for_directive",
    "layers_available", "what_changed", "transposition_gaps",
    "kov_regulations_citing", "explain_provision",
}
SNAPSHOT_KEYS = {
    "corpus_commit",
    "corpus_ref",
    "ontology_version",
    "evaluation_date",
    "server_version",
    "language",
}
AUDIT_KEYS = {
    "ts", "event", "consumer", "transport", "tool", "args_sha256", "status",
    "result_bytes", "truncated", "latency_ms", "corpus_commit", "corpus_ref",
    "ontology_version", "server_version",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _ACCESS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server, "mcp", server.MCPServer("estleg-test"))


@pytest.fixture
def audit_sink(monkeypatch) -> io.StringIO:
    sink = io.StringIO()
    monkeypatch.setattr(audit, "_AUDIT", audit.AuditLog(sink))
    return sink


def _lines(sink: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in sink.getvalue().splitlines()]


def _call(name: str, arguments: dict):
    """Call one wired tool through the SDK, returning its structured result."""
    server.register_tools(server.mcp)
    out = asyncio.run(server.mcp.call_tool(name, arguments))
    # SDK 1 returns (content, structured); SDK 2 a CallToolResult-like object.
    if isinstance(out, tuple):
        return out[1]
    structured = getattr(out, "structuredContent", None) or getattr(
        out, "structured_content", None
    )
    if structured is not None:
        return structured
    return json.loads(out[0].text)


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------
def test_tool_names_are_stable_and_complete() -> None:
    assert set(server.TOOLS) == EXPECTED_TOOLS
    server.register_tools(server.mcp)
    listed = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in listed} == EXPECTED_TOOLS


def test_descriptions_are_estonian_first_and_keep_english_contract() -> None:
    server.register_tools(server.mcp)
    for t in asyncio.run(server.mcp.list_tools()):
        et = i18n.TOOL_DESCRIPTIONS_ET[t.name]
        assert t.description.startswith(et), t.name
        assert "English:" in t.description, t.name
        # SDK 1 calls it inputSchema, SDK 2 input_schema.
        schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema")
        props = schema["properties"]
        assert props["language"]["default"] == "et"
        assert set(props["language"]["enum"]) == {"et", "en"}
        assert "ctx" not in props


def test_normalize_language() -> None:
    assert i18n.normalize_language(None) == "et"
    assert i18n.normalize_language("EN-gb") == "en"
    assert i18n.normalize_language("et_EE") == "et"
    with pytest.raises(i18n.UnsupportedLanguage):
        i18n.normalize_language("fr")


# ---------------------------------------------------------------------------
# Token map + fail closed
# ---------------------------------------------------------------------------
def test_parse_token_spec_inline_and_file(tmp_path: Path) -> None:
    assert security.parse_token_spec("a=one, b-2=two") == {"a": "one", "b-2": "two"}
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"rahandus": "s3cret", "sise": "other"}), encoding="utf-8")
    assert security.parse_token_spec(str(path)) == {"rahandus": "s3cret", "sise": "other"}


def test_token_file_rejects_duplicate_consumers(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text('{"same": "first", "same": "second"}')
    with pytest.raises(security.TokenConfigError):
        security.parse_token_spec(str(path))


def test_inline_tokens_survive_filename_length_limit(monkeypatch):
    import errno

    def too_long(self):
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(Path, "is_file", too_long)
    token = "x" * 256
    assert security.parse_token_spec(f"consumer={token}") == {"consumer": token}


def test_entrypoint_does_not_serve_wrong_release(tmp_path):
    import os
    import subprocess

    corpus = tmp_path / "corpus"
    (corpus / ".git").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, script in {
        "git": '#!/bin/sh\ncase " $* " in *" fetch "*) exit 1;; *" rev-parse "*) echo oldcommit;; esac\n',
        "estleg-mcp": '#!/bin/sh\necho SERVER_STARTED\n',
    }.items():
        path = bin_dir / name
        path.write_text(script)
        path.chmod(0o755)
    entrypoint = Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh"
    result = subprocess.run(
        ["sh", str(entrypoint)], capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
             "ESTLEG_CORPUS": str(corpus), "ESTLEG_CORPUS_REF": "missing-release"},
    )
    assert result.returncode != 0
    assert "SERVER_STARTED" not in result.stdout


@pytest.mark.parametrize(
    "spec",
    ["notapair", "a=", "=tok", "a=x,a=y", "a=x,b=x", "bad name=x"],
)
def test_parse_token_spec_rejects_malformed(spec: str) -> None:
    with pytest.raises(security.TokenConfigError):
        security.parse_token_spec(spec)


def test_registry_merges_legacy_token_and_authenticates() -> None:
    reg = security.load_registry({"ESTLEG_TOKENS": "ministry=abc", "ESTLEG_TOKEN": "legacy"})
    assert reg.consumers == ["default", "ministry"]
    assert reg.authenticate("Bearer abc") == "ministry"
    assert reg.authenticate("bearer legacy") == "default"
    assert reg.authenticate("Bearer nope") is None
    assert reg.authenticate("Basic abc") is None
    assert reg.authenticate("") is None
    assert reg.authenticate(None) is None


def test_http_fails_closed_without_credentials() -> None:
    with pytest.raises(security.CredentialsRequired):
        server._build_http_app()


def test_main_http_exits_without_credentials(monkeypatch, capsys) -> None:
    import uvicorn

    monkeypatch.setenv("ESTLEG_TRANSPORT", "http")
    monkeypatch.setattr(server, "check_provision_detection", lambda: None)
    called = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: called.append(1))
    with pytest.raises(SystemExit) as excinfo:
        server.main()
    assert excinfo.value.code == 1
    assert not called
    assert "ESTLEG_TOKENS" in capsys.readouterr().err


def test_main_http_exits_on_malformed_token_map(monkeypatch) -> None:
    import uvicorn

    monkeypatch.setenv("ESTLEG_TRANSPORT", "http")
    monkeypatch.setenv("ESTLEG_TOKENS", "missing-equals-sign")
    monkeypatch.setattr(server, "check_provision_detection", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        server.main()


def test_stdio_needs_no_credentials(monkeypatch) -> None:
    monkeypatch.setenv("ESTLEG_TRANSPORT", "stdio")
    monkeypatch.setattr(server, "check_provision_detection", lambda: None)
    ran = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: ran.append(1))
    server.main()
    assert ran == [1]


def test_anonymous_http_is_an_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("ESTLEG_ALLOW_ANONYMOUS_HTTP", "1")
    with TestClient(server._build_http_app()) as client:
        assert client.post("/mcp", json={}).status_code != 401


def test_http_rejects_unknown_token_and_keeps_health_open(monkeypatch) -> None:
    monkeypatch.setenv("ESTLEG_TOKENS", "a=tok-a,b=tok-b")
    with TestClient(server._build_http_app()) as client:
        assert client.get("/healthz").text == "ok"
        denied = client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
        assert denied.status_code == 401
        assert denied.headers["www-authenticate"] == "Bearer"
        for token in ("tok-a", "tok-b"):
            ok = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {token}"})
            assert ok.status_code != 401


def test_middleware_closes_non_http_scopes() -> None:
    reached: list[str] = []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {}

    gate = security.AccessMiddleware(app, security.TokenRegistry({"a": "t"}))
    asyncio.run(gate({"type": "websocket", "path": "/mcp", "headers": []}, receive, send))
    assert reached == [] and sent == [{"type": "websocket.close", "code": 1008}]
    asyncio.run(gate({"type": "lifespan"}, receive, send))
    assert reached == ["lifespan"]


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("60/minute", (1.0, 60)),
        ("5/second", (5.0, 5)),
        ("5/s", (5.0, 5)),
        ("3600/hour", (1.0, 3600)),
        ("120", (2.0, 120)),
        ("", None),
        ("off", None),
    ],
)
def test_parse_rate(spec: str, expected) -> None:
    assert security.parse_rate(spec) == expected


@pytest.mark.parametrize("spec", ["x/minute", "10/fortnight", "-1/minute"])
def test_parse_rate_rejects_garbage(spec: str) -> None:
    with pytest.raises(ValueError):
        security.parse_rate(spec)


def test_token_bucket_is_per_consumer_and_refills() -> None:
    now = [0.0]
    limiter = security.RateLimiter(1.0, 2, clock=lambda: now[0])
    assert limiter.acquire("a") == 0.0
    assert limiter.acquire("a") == 0.0
    wait = limiter.acquire("a")
    assert wait == pytest.approx(1.0)
    assert limiter.acquire("b") == 0.0  # independent bucket
    now[0] = 1.0
    assert limiter.acquire("a") == 0.0


def test_rate_limit_off_by_default_and_429_when_on(monkeypatch, audit_sink) -> None:
    monkeypatch.setenv("ESTLEG_TOKEN", "t")
    assert security.RateLimiter.from_env() is None
    monkeypatch.setenv("ESTLEG_RATE_LIMIT", "1/hour")
    with TestClient(server._build_http_app()) as client:
        headers = {"Authorization": "Bearer t"}
        assert client.post("/mcp", json={}, headers=headers).status_code != 429
        limited = client.post("/mcp", json={}, headers=headers)
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0
        # /healthz is never rate limited.
        assert client.get("/healthz").status_code == 200
    events = [r for r in _lines(audit_sink) if r["event"] == "rate_limited"]
    assert events and events[0]["consumer"] == "default"


# ---------------------------------------------------------------------------
# Audit line + envelope (through the SDK, stdio-style: no HTTP request)
# ---------------------------------------------------------------------------
def test_list_result_envelope_and_audit_line(monkeypatch, audit_sink) -> None:
    monkeypatch.setattr(data, "search_law_records", lambda query, limit: [])
    out = _call("search_laws", {"query": "isikukood 38001010000"})
    assert out["result"] == []
    assert set(out["snapshot"]) == SNAPSHOT_KEYS
    assert out["snapshot"]["language"] == "et"
    assert out["snapshot"]["server_version"] == provenance.SERVER_VERSION
    (line,) = _lines(audit_sink)
    assert set(line) == AUDIT_KEYS
    assert line["event"] == "tool_call"
    assert line["consumer"] == server.LOCAL_CONSUMER
    assert line["transport"] == "stdio"
    assert line["status"] == "ok"
    assert line["truncated"] is False
    # Never the raw text -- only its hash.
    assert "38001010000" not in audit_sink.getvalue()
    expected = audit.arguments_hash(
        {"query": "isikukood 38001010000", "limit": 10, "language": "et"}
    )
    assert line["args_sha256"] == expected


def test_dict_result_gets_snapshot_key_and_language_switch(monkeypatch, audit_sink) -> None:
    monkeypatch.setattr(data, "resolve_law", lambda _q: None)
    et = _call("get_law", {"law": "Olematu seadus"})
    en = _call("get_law", {"law": "Olematu seadus", "language": "en"})
    assert et["note"].startswith("Seadust 'Olematu seadus' ei leitud")
    assert en["note"].startswith("No law matched 'Olematu seadus'")
    assert en["snapshot"]["language"] == "en"
    assert set(et["snapshot"]) == SNAPSHOT_KEYS


def test_failed_call_is_audited_as_error(monkeypatch, audit_sink) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("corpus exploded")

    monkeypatch.setattr(data, "search_law_records", boom)
    with pytest.raises(Exception):
        _call("search_laws", {"query": "x"})
    (line,) = _lines(audit_sink)
    assert line["status"] == "error"
    assert line["error"] == "RuntimeError"
    assert "exploded" not in audit_sink.getvalue()


def test_truncated_flag_reaches_the_audit_line(monkeypatch, audit_sink) -> None:
    # An overflow sentinel and a cut text both count as "truncated".
    rows = [{"reg_id": str(i)} for i in range(3)]
    assert audit.is_truncated(server._capped(rows, 2))
    assert not audit.is_truncated(server._capped(rows, 5))
    assert audit.is_truncated({"changes": [], "truncated": True})

    regs = [
        data.RegulationRecord(
            reg_id=str(i), global_id="", title=f"Määrus {i}", issuer="Vald",
            rt_url="", status="", slug=f"m{i}", file="", is_kov=True,
            municipality="vald", issued_under=[], citations=[], num_provisions=0,
        )
        for i in range(3)
    ]
    monkeypatch.setattr(data, "regulations_by_issuer", lambda _i: regs)
    out = _call("regulations_by_issuer", {"institution": "Vald", "limit": 2})
    assert out["result"][-1]["overflow"] is True
    (line,) = _lines(audit_sink)
    assert line["truncated"] is True


def test_arguments_hash_is_order_independent_and_keyable() -> None:
    a = audit.arguments_hash({"x": 1, "y": "KarS"})
    assert a == audit.arguments_hash({"y": "KarS", "x": 1})
    keyed = audit.arguments_hash({"x": 1, "y": "KarS"}, key=b"k")
    assert keyed != a and len(keyed) == 64


def test_audit_log_from_env(tmp_path: Path) -> None:
    assert audit.AuditLog.from_env({"ESTLEG_AUDIT": "off"}).enabled is False
    target = tmp_path / "audit.jsonl"
    log = audit.AuditLog.from_env({"ESTLEG_AUDIT_LOG": str(target), "ESTLEG_AUDIT_HASH_KEY": "k"})
    log.emit({"event": "tool_call", "tool": "t"})
    log.emit({"event": "tool_call", "tool": "u"})
    lines = target.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["tool"] for x in lines] == ["t", "u"]
    assert log.hash_arguments({"q": 1}) == audit.arguments_hash({"q": 1}, key=b"k")


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
SHA = "a" * 40


def test_read_git_head_variants(tmp_path: Path) -> None:
    detached = tmp_path / "detached"
    (detached / ".git").mkdir(parents=True)
    (detached / ".git" / "HEAD").write_text(SHA + "\n")
    assert provenance.read_git_head(detached) == SHA

    branch = tmp_path / "branch"
    (branch / ".git" / "refs" / "heads").mkdir(parents=True)
    (branch / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (branch / ".git" / "refs" / "heads" / "main").write_text("b" * 40 + "\n")
    assert provenance.read_git_head(branch) == "b" * 40

    packed = tmp_path / "packed"
    (packed / ".git").mkdir(parents=True)
    (packed / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (packed / ".git" / "packed-refs").write_text(
        "# pack-refs with: peeled\n" + "c" * 40 + " refs/heads/main\n"
    )
    assert provenance.read_git_head(packed) == "c" * 40

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {detached / '.git'}\n")
    assert provenance.read_git_head(worktree) == SHA

    assert provenance.read_git_head(tmp_path / "nothing") == ""


def test_corpus_identity_prefers_entrypoint_env(monkeypatch) -> None:
    monkeypatch.setenv("ESTLEG_CORPUS_COMMIT", SHA)
    monkeypatch.setenv("ESTLEG_CORPUS_REF", "v1.0.0")
    provenance.corpus_identity.cache_clear()
    try:
        ident = provenance.corpus_identity()
        assert ident["corpus_commit"] == SHA
        assert ident["corpus_ref"] == "v1.0.0"
    finally:
        provenance.corpus_identity.cache_clear()
