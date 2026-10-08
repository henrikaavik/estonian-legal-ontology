"""One JSON audit line per tool call (#714).

Every tool call -- successful, failed, or rejected by the rate limiter --
produces exactly one line of JSON on the audit sink:

.. code-block:: json

    {"ts": "2026-10-08T16:02:11.482Z", "event": "tool_call",
     "consumer": "rahandusministeerium", "transport": "http",
     "tool": "get_provision", "args_sha256": "1f0c…", "status": "ok",
     "result_bytes": 1840, "truncated": true, "latency_ms": 12.4,
     "corpus_commit": "f018cf05…", "corpus_ref": "v1.0.0",
     "ontology_version": "1.0.0", "server_version": "0.2.0"}

The raw arguments are **never** logged, only a SHA-256 of their canonical JSON
(keys sorted). Short legal queries ("KarS") are guessable from a plain hash,
so set ``ESTLEG_AUDIT_HASH_KEY`` to switch to a keyed HMAC-SHA256: the
deployment can still correlate identical queries, a reader of the log without
the key cannot dictionary-reverse them.

Configuration:

* ``ESTLEG_AUDIT`` -- ``off`` / ``0`` / ``false`` disables the log (default on).
* ``ESTLEG_AUDIT_LOG`` -- file path to append to; unset or ``-`` = stderr
  (stdout is the stdio transport's protocol channel and is never used).
* ``ESTLEG_AUDIT_HASH_KEY`` -- optional HMAC key for ``args_sha256``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any, TextIO

_OFF = {"0", "off", "false", "no"}


def canonical_json(value: Any) -> str:
    """Deterministic JSON (sorted keys, no whitespace) for hashing / sizing."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def arguments_hash(arguments: dict[str, Any], key: bytes | None = None) -> str:
    """Hex SHA-256 (or HMAC-SHA256 with ``key``) of the canonical arguments."""
    payload = canonical_json(arguments).encode("utf-8")
    if key:
        return hmac.new(key, payload, hashlib.sha256).hexdigest()
    return hashlib.sha256(payload).hexdigest()


def is_truncated(value: Any) -> bool:
    """True when any object in ``value`` carries ``truncated`` / ``overflow`` true."""
    if isinstance(value, dict):
        if value.get("truncated") is True or value.get("overflow") is True:
            return True
        return any(is_truncated(v) for v in value.values())
    if isinstance(value, list):
        return any(is_truncated(v) for v in value)
    return False


def utc_timestamp() -> str:
    """ISO-8601 UTC timestamp with millisecond precision and a ``Z`` suffix."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class AuditLog:
    """Thread-safe JSON-lines writer (one ``emit`` = one line, flushed)."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        enabled: bool = True,
        hash_key: bytes | None = None,
    ) -> None:
        self._stream = stream
        self.enabled = enabled and stream is not None
        self.hash_key = hash_key
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> AuditLog:
        env = os.environ if environ is None else environ
        if env.get("ESTLEG_AUDIT", "").strip().lower() in _OFF:
            return cls(None, enabled=False)
        key = env.get("ESTLEG_AUDIT_HASH_KEY", "").encode("utf-8") or None
        target = env.get("ESTLEG_AUDIT_LOG", "").strip()
        if not target or target == "-":
            return cls(sys.stderr, hash_key=key)
        # Line-buffered append; the file stays open for the process lifetime.
        stream = open(target, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        return cls(stream, hash_key=key)

    def hash_arguments(self, arguments: dict[str, Any]) -> str:
        return arguments_hash(arguments, self.hash_key)

    def emit(self, record: dict[str, Any]) -> None:
        """Write ``record`` as one JSON line. Never raises into the tool call."""
        if not self.enabled or self._stream is None:
            return
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock:
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
            except (OSError, ValueError):
                # A full disk or closed stream must not take the answer down
                # with it; the operator sees the gap in the log instead.
                pass


_AUDIT: AuditLog | None = None
_AUDIT_LOCK = threading.Lock()


def get_audit_log() -> AuditLog:
    """The process-wide audit log, built from the environment on first use."""
    global _AUDIT
    with _AUDIT_LOCK:
        if _AUDIT is None:
            _AUDIT = AuditLog.from_env()
        return _AUDIT


def set_audit_log(log: AuditLog | None) -> None:
    """Replace (or with ``None`` reset to env-built) the process audit log."""
    global _AUDIT
    with _AUDIT_LOCK:
        _AUDIT = log


__all__ = [
    "AuditLog",
    "arguments_hash",
    "canonical_json",
    "get_audit_log",
    "is_truncated",
    "set_audit_log",
    "utc_timestamp",
]
