"""Shared EUR-Lex SPARQL helpers.

Centralises CELEX sanitisation, the POST-not-GET SPARQL helper
(root cause of #129/#96), and the bounded exponential-backoff retry
wrapper used by every script that queries the Publications Office
Virtuoso endpoint. Previously these were paste-cloned across four
generators; that meant POST/202/retry bug fixes had to land in four
files in lockstep.
"""

from __future__ import annotations

import re
import time
from typing import Callable

import requests

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"


def sanitize_celex(celex: str) -> str:
    """Create a safe ID from a CELEX number."""
    return re.sub(r"[^0-9A-Za-z]", "", celex)[:40] or "Unknown"


def sparql_query(query: str) -> list[dict]:
    """Execute a SPARQL query and return bindings.

    We POST the query (``application/x-www-form-urlencoded``) instead of
    GETting it. The Publications Office Virtuoso endpoint answers GET for
    non-trivial queries with ``HTTP 202 Accepted`` and an *empty* body;
    ``raise_for_status()`` does not raise on 2xx, so a GET helper silently
    returns ``[]`` (root cause of #129/#96). POST returns ``200`` +
    ``application/sparql-results+json``. We still guard against a 202 /
    non-JSON body so the retry layer reacts if POST ever misbehaves too.
    """
    resp = requests.post(
        SPARQL_ENDPOINT,
        data={"query": query},
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=120,
    )
    resp.raise_for_status()
    if resp.status_code == 202:
        raise RuntimeError(
            f"SPARQL endpoint returned HTTP 202 (empty body) — "
            f"endpoint unhealthy or rate-limiting: {SPARQL_ENDPOINT}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(
            f"SPARQL endpoint returned a non-JSON body "
            f"(status {resp.status_code}): {exc}"
        ) from exc
    return data.get("results", {}).get("bindings", [])


def sparql_query_with_retry(
    query: str,
    *,
    query_fn: Callable[[str], list[dict]] | None = None,
    retries: int = 3,
    backoff: float = 2.0,
) -> list[dict]:
    """Execute a SPARQL query with bounded exponential backoff on failure.

    EUR-Lex 5xxs intermittently. Without a retry layer a single transient
    error in the middle of a paginated sweep silently truncates the
    dataset. We sleep ``backoff * 2**attempt`` between attempts and
    re-raise the underlying exception (wrapped in ``RuntimeError``) on
    terminal failure so the caller can either ``break`` (under
    ``--allow-partial``) or propagate to the run's exit code.

    ``query_fn`` lets callers pass in their own module's ``sparql_query``
    so test monkeypatches at the caller's module level propagate;
    defaults to this module's ``sparql_query``.
    """
    fn = query_fn if query_fn is not None else sparql_query
    last_exc: BaseException | None = None
    for attempt in range(retries):
        try:
            return fn(query)
        except Exception as exc:  # noqa: BLE001 — broad to retry network/JSON
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise RuntimeError(
        f"sparql_query failed after {retries} attempts: {last_exc}"
    ) from last_exc
