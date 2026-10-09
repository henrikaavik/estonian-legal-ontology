"""HTTP-transport access control: per-consumer tokens, fail-closed, rate limit (#714).

The stdio transport is a local process talking to one IDE and needs none of
this. The streamable-HTTP transport is a shared network endpoint, so:

* **Fail closed.** :func:`load_registry` returns the configured consumers;
  ``server._build_http_app`` refuses to build an HTTP app with none
  (:class:`CredentialsRequired`), unless the operator explicitly sets
  ``ESTLEG_ALLOW_ANONYMOUS_HTTP=1`` for local development.
* **Per-consumer tokens.** ``ESTLEG_TOKENS`` names each consumer, so the audit
  line says *who* asked (``rahandusministeerium``), not just that a valid
  shared secret was used. It is either inline ``name=token,name2=token2`` or
  the path of a JSON file ``{"name": "token", ...}`` (keep the file outside
  the image and mount it read-only). The legacy single ``ESTLEG_TOKEN`` still
  works and is the consumer ``default``.
* **Rate limit.** ``ESTLEG_RATE_LIMIT`` (off by default) enables a per-consumer
  token bucket: ``60/minute``, ``5/second``, ``1000/hour`` or a bare number
  (per minute). ``ESTLEG_RATE_BURST`` sets the bucket size (default: the
  per-period count). Over-limit requests get ``429`` with ``Retry-After``.
* **Resolver exemption (#728).** When the HTTP app mounts the w3id resolver
  (``/id/{local}``, ``/vocabulary``), ``GET``/``HEAD`` on exactly those paths
  pass without a token as the ``anonymous`` consumer and draw from the single
  :data:`RESOLVER_BUCKET` bucket (``ESTLEG_RESOLVER_RATE_LIMIT`` /
  ``ESTLEG_RESOLVER_RATE_BURST``, falling back to the shared limit). Every
  other path and method stays fail-closed.

:class:`AccessMiddleware` is a pure ASGI middleware (no response buffering,
so the SSE stream of the MCP transport passes through untouched). It records
the authenticated consumer in ``scope["state"]["estleg_consumer"]``, which is
where the tool wrapper reads it back for the audit line.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from .audit import AuditLog, utc_timestamp

CONSUMER_STATE_KEY = "estleg_consumer"
ANONYMOUS_CONSUMER = "anonymous"
LEGACY_CONSUMER = "default"
HEALTH_PATH = "/healthz"
# The w3id resolver pilot (#728): public identifiers, read-only, anonymous.
# Exactly ``/id``, ``/id/``, ``/id/<one segment>`` and ``/vocabulary`` -- a
# decoded ``/`` inside the name (``/id/a%2Fb``) does not match and stays gated.
RESOLVER_PATH = re.compile(r"^/(?:id(?:/[^/]*)?|vocabulary)$")
RESOLVER_METHODS = frozenset({"GET", "HEAD"})
# Every anonymous resolver hit shares this one rate-limit bucket, so a crawler
# can exhaust only the resolver's allowance, never a token holder's.
RESOLVER_BUCKET = "anonymous-resolver"

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class TokenConfigError(ValueError):
    """The token configuration is malformed (the server must not start)."""


class CredentialsRequired(RuntimeError):
    """HTTP transport requested with no credentials and no anonymous opt-in."""


# ---------------------------------------------------------------------------
# Token map
# ---------------------------------------------------------------------------
def _add(tokens: dict[str, str], name: str, token: str, source: str) -> None:
    name, token = name.strip(), token.strip()
    if not _NAME.match(name):
        raise TokenConfigError(
            f"{source}: consumer name {name!r} must be 1-64 chars of letters, "
            "digits, '.', '_' or '-'"
        )
    if not token:
        raise TokenConfigError(f"{source}: consumer {name!r} has an empty token")
    if name in tokens:
        raise TokenConfigError(f"{source}: consumer {name!r} is defined twice")
    if token in tokens.values():
        raise TokenConfigError(f"{source}: consumer {name!r} reuses another consumer's token")
    tokens[name] = token


def parse_token_spec(spec: str, source: str = "ESTLEG_TOKENS") -> dict[str, str]:
    """Parse ``ESTLEG_TOKENS``: inline ``name=token,…`` or a JSON file path.

    A value naming an existing file is read as JSON ``{"name": "token"}``;
    anything else is parsed inline. Returns ``{name: token}``. Raises
    :class:`TokenConfigError` on any malformed entry rather than skipping it,
    so a typo can never silently drop (or open) access.
    """
    spec = (spec or "").strip()
    tokens: dict[str, str] = {}
    if not spec:
        return tokens
    path = Path(spec).expanduser()
    try:
        is_file = path.is_file()
    except OSError:
        # Inline maps can exceed a filesystem component's length limit.
        is_file = False
    if is_file:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise TokenConfigError(f"{source}: duplicate consumer in token file")
                result[key] = value
            return result

        try:
            doc = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        except (OSError, ValueError) as exc:
            raise TokenConfigError(f"{source}: cannot read token file {spec!r}: {exc}") from exc
        if not isinstance(doc, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in doc.items()
        ):
            raise TokenConfigError(
                f"{source}: token file {spec!r} must be a JSON object of name -> token strings"
            )
        for name, token in doc.items():
            _add(tokens, name, token, f"{source} file")
        return tokens
    for entry in spec.split(","):
        if not entry.strip():
            continue
        name, sep, token = entry.partition("=")
        if not sep:
            raise TokenConfigError(
                f"{source}: an entry is not name=token "
                "(and the value is not an existing file path)"
            )
        _add(tokens, name, token, source)
    return tokens


class TokenRegistry:
    """Consumer-name lookup for bearer tokens, in constant time per token."""

    def __init__(self, tokens: Mapping[str, str]) -> None:
        self._tokens = dict(tokens)

    @property
    def consumers(self) -> list[str]:
        return sorted(self._tokens)

    def __len__(self) -> int:
        return len(self._tokens)

    def authenticate(self, authorization: str | None) -> str | None:
        """The consumer whose token matches an ``Authorization: Bearer`` value.

        Every configured token is compared with :func:`hmac.compare_digest`
        and the loop never exits early, so response timing does not reveal
        which consumer (or how much of a token) matched.
        """
        header = (authorization or "").strip()
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not presented.strip():
            return None
        presented_b = presented.strip().encode("utf-8")
        found: str | None = None
        for name, token in self._tokens.items():
            if hmac.compare_digest(presented_b, token.encode("utf-8")):
                found = name
        return found


def load_registry(environ: Mapping[str, str] | None = None) -> TokenRegistry:
    """Build the registry from ``ESTLEG_TOKENS`` plus the legacy ``ESTLEG_TOKEN``."""
    env = os.environ if environ is None else environ
    tokens = parse_token_spec(env.get("ESTLEG_TOKENS", ""))
    legacy = env.get("ESTLEG_TOKEN", "").strip()
    if legacy:
        _add(tokens, LEGACY_CONSUMER, legacy, "ESTLEG_TOKEN")
    return TokenRegistry(tokens)


def anonymous_http_allowed(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get("ESTLEG_ALLOW_ANONYMOUS_HTTP", "").strip() == "1"


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------
_PERIODS = {
    "s": 1.0, "sec": 1.0, "second": 1.0,
    "m": 60.0, "min": 60.0, "minute": 60.0,
    "h": 3600.0, "hour": 3600.0,
}


def parse_rate(spec: str) -> tuple[float, int] | None:
    """``"60/minute"`` -> ``(1.0 tokens/s, 60)``; "" / ``off`` -> ``None``.

    Returns ``(refill rate per second, per-period count)``. A bare number is a
    per-minute count. Raises :class:`ValueError` on anything unparseable.
    """
    spec = (spec or "").strip().lower()
    if not spec or spec in {"0", "off", "none"}:
        return None
    count_text, _, period_text = spec.partition("/")
    try:
        count = int(count_text)
    except ValueError as exc:
        raise ValueError(f"ESTLEG_RATE_LIMIT: {spec!r} is not like '60/minute'") from exc
    period_key = (period_text or "minute").strip()
    period = _PERIODS.get(period_key) or _PERIODS.get(period_key.rstrip("s"))
    if count <= 0 or period is None:
        raise ValueError(f"ESTLEG_RATE_LIMIT: {spec!r} is not like '60/minute'")
    return count / period, count


class RateLimiter:
    """Per-consumer token bucket. ``acquire`` returns 0 or seconds to wait."""

    def __init__(
        self,
        rate_per_second: float,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_per_second <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self.rate = rate_per_second
        self.burst = float(burst)
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        rate_var: str = "ESTLEG_RATE_LIMIT",
        burst_var: str = "ESTLEG_RATE_BURST",
    ) -> RateLimiter | None:
        env = os.environ if environ is None else environ
        try:
            parsed = parse_rate(env.get(rate_var, ""))
        except ValueError as exc:
            raise ValueError(str(exc).replace("ESTLEG_RATE_LIMIT", rate_var)) from exc
        if parsed is None:
            return None
        rate, count = parsed
        burst_text = env.get(burst_var, "").strip()
        try:
            burst = int(burst_text) if burst_text else count
        except ValueError as exc:
            raise ValueError(f"{burst_var}: {burst_text!r} is not an integer") from exc
        return cls(rate, max(1, burst))

    def acquire(self, consumer: str) -> float:
        """Take one token for ``consumer``; return 0.0, or the wait in seconds."""
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(consumer, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                self._buckets[consumer] = (tokens - 1.0, now)
                return 0.0
            self._buckets[consumer] = (tokens, now)
            return (1.0 - tokens) / self.rate


# ---------------------------------------------------------------------------
# ASGI middleware
# ---------------------------------------------------------------------------
Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


async def _json_response(
    send: Send, status: int, body: dict[str, Any], headers: list[tuple[bytes, bytes]]
) -> None:
    payload = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class AccessMiddleware:
    """Bearer-token gate + optional rate limit for every path but ``/healthz``.

    ``registry`` may be ``None`` only when anonymous HTTP was explicitly
    allowed; requests are then attributed to the ``anonymous`` consumer.

    With ``open_resolver=True`` (set by the HTTP app only when it mounted the
    resolver routes), ``GET``/``HEAD`` on :data:`RESOLVER_PATH` also bypass the
    token check as ``anonymous``, limited under :data:`RESOLVER_BUCKET` by
    ``resolver_limiter`` (or, when that is ``None``, the shared ``limiter``).
    """

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        registry: TokenRegistry | None,
        limiter: RateLimiter | None = None,
        audit_log: Callable[[], AuditLog] | None = None,
        identity: Callable[[], dict[str, str]] | None = None,
        open_resolver: bool = False,
        resolver_limiter: RateLimiter | None = None,
    ) -> None:
        self.app = app
        self.registry = registry
        self.limiter = limiter
        self.audit_log = audit_log
        self.identity = identity
        self.open_resolver = open_resolver
        self.resolver_limiter = resolver_limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        if kind == "lifespan" or (kind == "http" and scope.get("path") == HEALTH_PATH):
            await self.app(scope, receive, send)
            return
        if kind != "http":
            # Fail closed for anything else (e.g. a WebSocket upgrade): the
            # server exposes no such route, and nothing may bypass the gate.
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if (
            self.open_resolver
            and RESOLVER_PATH.match(scope.get("path") or "")
            and (scope.get("method") or "").upper() in RESOLVER_METHODS
        ):
            await self._serve_resolver(scope, receive, send)
            return
        if self.registry is None:
            consumer: str | None = ANONYMOUS_CONSUMER
        else:
            auth = ""
            for key, value in scope.get("headers") or []:
                if key.lower() == b"authorization":
                    auth = value.decode("latin-1")
                    break
            consumer = self.registry.authenticate(auth)
        if consumer is None:
            await _json_response(
                send, 401, {"error": "unauthorized"}, [(b"www-authenticate", b"Bearer")]
            )
            return
        if self.limiter is not None:
            wait = self.limiter.acquire(consumer)
            if wait > 0:
                retry = max(1, int(wait + 0.999))
                self._audit_rate_limited(consumer, retry)
                await _json_response(
                    send,
                    429,
                    {"error": "rate_limited", "retry_after_seconds": retry},
                    [(b"retry-after", str(retry).encode())],
                )
                return
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state[CONSUMER_STATE_KEY] = consumer
        await self.app(scope, receive, send)

    async def _serve_resolver(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Anonymous, bucket-limited pass-through for the resolver routes (#728)."""
        limiter = self.resolver_limiter or self.limiter
        if limiter is not None:
            wait = limiter.acquire(RESOLVER_BUCKET)
            if wait > 0:
                retry = max(1, int(wait + 0.999))
                self._audit_rate_limited(ANONYMOUS_CONSUMER, retry, tool="resolve")
                await _json_response(
                    send,
                    429,
                    {"error": "rate_limited", "retry_after_seconds": retry},
                    [(b"retry-after", str(retry).encode())],
                )
                return
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state[CONSUMER_STATE_KEY] = ANONYMOUS_CONSUMER
        await self.app(scope, receive, send)

    def _audit_rate_limited(self, consumer: str, retry_after: int, tool: str = "") -> None:
        if self.audit_log is None:
            return
        record: dict[str, Any] = {
            "ts": utc_timestamp(),
            "event": "rate_limited",
            "consumer": consumer,
            "transport": "http",
            "retry_after_seconds": retry_after,
        }
        if tool:
            record["tool"] = tool
        if self.identity is not None:
            record.update(self.identity())
        self.audit_log().emit(record)


__all__ = [
    "ANONYMOUS_CONSUMER",
    "AccessMiddleware",
    "CONSUMER_STATE_KEY",
    "CredentialsRequired",
    "LEGACY_CONSUMER",
    "RESOLVER_BUCKET",
    "RESOLVER_PATH",
    "RateLimiter",
    "TokenConfigError",
    "TokenRegistry",
    "anonymous_http_allowed",
    "load_registry",
    "parse_rate",
    "parse_token_spec",
]
