"""Shared helpers for Riigi Teataja-based generators (laws + regulations).

Centralises API access, XML caching, and structured/legacy XML parsing so the
seadus and määrus pipelines stay aligned. The two generators differ in:

  * the document type queried (`dokument=seadus` vs `dokument=määrus`)
  * the IRI scheme (laws use registry/abbreviation-based prefixes;
    regulations use terviktekstID-based prefixes)
  * which body formats they expect (laws are always structured; regulations
    pre-2010 mostly ship the full body inside `<HTMLKonteiner>`)

This module exposes the pieces that are identical between both pipelines.
"""

from __future__ import annotations

import html
import json
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlunsplit

import requests  # tests monkeypatch ``requests.get``

# Single source of truth: NS, CONTEXT, the Estonian transliteration table,
# sanitize_id, slugify, and save_json all live in estleg_common. They are
# re-exported here so legacy `from riigiteataja_common import ...` callers
# (e.g. generate_regulations.py) keep working without churn.
from estleg.estleg_common import (  # noqa: F401  -- re-exports for public API
    _ESTONIAN_TRANSLITERATION,
    _TRANSLIT_TABLE,
    _sup_to_unicode,
    CONTEXT,
    KRR_DIR,
    NS,
    allowed_get,
    iter_peep_files,
    parse_xml,
    parse_xml_file,
    sanitize_id,
    save_json,
    slugify,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"

SEARCH_URL = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
BASE_URL = "https://www.riigiteataja.ee"
_TALLINN = ZoneInfo("Europe/Tallinn")


class SourceListFetchError(RuntimeError):
    """Raised when a source-list page cannot be fetched completely."""


# Keys used in the per-run page-fetch counter dict surfaced by source-list
# generators in their manifest/index ``run`` blocks. ``pagesFetchedOk`` =
# pages that returned JSON; ``pagesFailed`` = pages that ultimately failed
# (after any retries); ``pagesRetried`` = pages that needed at least one
# retry attempt before resolving (success or final failure).
PAGE_STAT_KEYS = ("pagesFetchedOk", "pagesFailed", "pagesRetried")


def new_page_stats() -> dict[str, int]:
    """Return a fresh zeroed page-fetch counter dict."""
    return {key: 0 for key in PAGE_STAT_KEYS}


def ln(tag: str) -> str:
    """Strip an XML namespace prefix and return the local tag name."""
    return tag.split("}", 1)[1] if "}" in tag else tag


_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _child_text(el: ET.Element) -> str:
    """Flatten an element's text including nested children.

    Riigi Teataja titles may carry a ``<sup>`` child (``§ 217<sup>2</sup>``);
    ``el.text`` alone stops at that child (#694). Superscript digits are
    rendered as Unicode superscripts so the title reads ``§ 217²``.
    """
    parts = [el.text or ""]
    for child in el:
        text = _child_text(child)
        if ln(child.tag) == "sup":
            text = text.translate(_SUPERSCRIPT_DIGITS)
        parts.append(text)
        parts.append(child.tail or "")
    return _sup_to_unicode("".join(parts))


def ct(el: ET.Element, name: str) -> str | None:
    """Return the text of the first direct child whose local tag matches `name`.

    Nested markup inside the child (``<sup>``) is flattened, not truncated.
    """
    for c in el:
        if ln(c.tag) == name:
            text = _child_text(c).strip()
            if text:
                return text
    return None


# Sentence terminator followed by whitespace — the preferred summary cut
# point. Matches the boundary heuristic used by the other generators'
# truncation (see ``generate_annotations._truncate_to_sentence``).
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s")


def _truncate_on_boundary(text: str, max_len: int) -> str:
    """Cut ``text`` to at most ``max_len`` chars on a sentence/word boundary.

    Prefers the last sentence terminator (``.``/``!``/``?`` + space) that
    falls in the back ~30% of the window; failing that, falls back to the
    last word boundary (space) so a token is never split mid-word (issue
    #368 — the old raw ``text[:max_len]`` cut ``…kohustatud arvut``). A
    trimmed result is marked with an ellipsis so truncation stays visible.
    """
    if len(text) <= max_len:
        return text
    head = text[:max_len]
    # Last sentence boundary not earlier than 70% in, to avoid a stub summary.
    floor = int(max_len * 0.7)
    last_sentence = -1
    for match in _SENTENCE_BOUNDARY_RE.finditer(head):
        if match.start() >= floor:
            last_sentence = match.start()
    if last_sentence >= 0:
        return head[: last_sentence + 1]
    last_space = head.rfind(" ")
    cut = head[:last_space] if last_space > 0 else head
    return cut.rstrip() + "…"


# Public name used by generate_regulations (issue #368).
truncate_on_boundary = _truncate_on_boundary


# Provision text-bearing tags. These NEST in Riigi Teataja XML — a ``loige``
# subsection contains ``lause`` sentences which may wrap ``lauseOsa`` parts — so
# an ``el.iter()`` walk matching every one of them appended a parent's
# ``itertext()`` AND each descendant's text, doubling (or tripling) every span
# and emitting ``estleg:summary`` as the preview repeated. Collect from only the
# OUTERMOST matching element instead (``_iter_outermost_text``) so each span is
# counted exactly once. This is the regulation-side counterpart of the #613
# law-generator fix; stale on-disk regulation summaries it already produced are
# repaired offline by ``scripts/dedupe_summaries_629.py`` (#629).
_TEXT_TAGS: frozenset[str] = frozenset({"loige", "lauseOsa", "lause", "tavatekst"})


def _iter_outermost_text(el: ET.Element) -> Iterator[str]:
    """Yield collapsed text from the OUTERMOST text-bearing elements under ``el``.

    Recurses through ``el``'s descendants: a child whose local tag is in
    :data:`_TEXT_TAGS` contributes its full ``itertext()`` (already covering any
    nested ``lause``/``lauseOsa``) and is NOT descended into; any other child is
    recursed into so a text element wrapped in non-text markup is still reached.
    Taking only the outermost match counts each text span exactly once, which is
    what eliminates the nested-tag double-counting of issue #629/#613. The
    whitespace collapse and the ``len > 3`` noise filter mirror the historical
    behaviour so summary text is otherwise unchanged.
    """
    for child in el:
        if ln(child.tag) in _TEXT_TAGS:
            txt = re.sub(r"\s+", " ", _child_text(child)).strip()
            if len(txt) > 3:
                yield txt
        else:
            yield from _iter_outermost_text(child)


def collect_text(el: ET.Element, max_len: int = 500) -> str:
    """Concatenate provision text up to `max_len` characters (for summaries).

    Text is gathered from the outermost text-bearing elements only
    (``_iter_outermost_text``), so nested matching tags are never double-counted
    (issue #629). The final cut lands on a sentence/word boundary
    (``_truncate_on_boundary``) rather than a raw character slice, so summaries
    are never split mid-token (issue #368).
    """
    parts: list[str] = []
    for txt in _iter_outermost_text(el):
        parts.append(txt)
        if len(" ".join(parts)) >= max_len:
            break
    joined = " ".join(parts)
    return _truncate_on_boundary(joined, max_len) if joined else ""


def collect_full_text(el: ET.Element) -> str:
    """Return the complete provision text without truncation.

    Gathers text from the outermost text-bearing elements only
    (``_iter_outermost_text``), so nested ``loige``/``lause``/``lauseOsa`` spans
    are counted once rather than once per nesting level (issue #629).
    """
    return " ".join(_iter_outermost_text(el))


def fetch_acts(
    document: str,
    *,
    kov: bool | None = None,
    kehtiv: str | None = None,
    text_filter: str = "terviktekst",
    limiit: int | None = 500,
    max_pages: int = 100,
    timeout: int = 30,
    allow_partial: bool = False,
    max_retries: int = 2,
    retry_sleep: float = 1.0,
    stats: dict | None = None,
    stop_on_short_page: bool = True,
) -> Iterator[dict]:
    """Yield acts from the Riigi Teataja search API page by page.

    Parameters mirror the API exactly:
      * `document` — `seadus` or `määrus`
      * `kov` — `False` to exclude KOV regulations, `True` for KOV-only,
        `None` to omit the parameter (returns both)
      * `kehtiv` — ISO date for the snapshot (`YYYY-MM-DD`); when set the
        API returns only acts in force on that day

    When ``stats`` is supplied it is treated as a mutable counter dict and
    the ``PAGE_STAT_KEYS`` (``pagesFetchedOk`` / ``pagesFailed`` /
    ``pagesRetried``) are incremented in place so the caller can surface
    per-page success/fail/retry counts in its manifest. Missing keys are
    initialised to zero. On a terminal fetch failure with ``allow_partial``
    the generator stops early; the caller can detect that via
    ``stats["pagesFailed"] > 0``.
    """
    if stats is not None:
        for key in PAGE_STAT_KEYS:
            stats.setdefault(key, 0)
    page = 1
    while page <= max_pages:
        params: dict[str, str | int | bool] = {
            "leht": page,
            "dokument": document,
            "tekst": text_filter,
        }
        if limiit is not None:
            params["limiit"] = limiit
        if kehtiv:
            params["kehtiv"] = kehtiv
            params["kehtivKehtetus"] = "false"
            params["mitteJoustunud"] = "false"
        if kov is not None:
            params["kov"] = "true" if kov else "false"

        last_error: Exception | None = None
        attempts_made = 0
        for attempt in range(max_retries + 1):
            attempts_made = attempt
            try:
                resp = allowed_get(SEARCH_URL, params=params, timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
                last_error = None
                break
            except Exception as e:  # noqa: BLE001 - preserve underlying API error in message
                last_error = e
                if attempt < max_retries:
                    time.sleep(retry_sleep * (attempt + 1))

        if last_error is not None:
            if stats is not None:
                stats["pagesFailed"] += 1
                if max_retries > 0:
                    stats["pagesRetried"] += 1
            message = f"API error on page {page}: {last_error}"
            print(f"  {message}")
            if allow_partial:
                break
            raise SourceListFetchError(message) from last_error

        if stats is not None:
            stats["pagesFetchedOk"] += 1
            if attempts_made > 0:
                stats["pagesRetried"] += 1

        aktid = data.get("aktid", []) or []
        if not aktid:
            break
        for act in aktid:
            yield act
        if stop_on_short_page and limiit is not None and len(aktid) < limiit:
            break
        if page >= max_pages:
            if stats is not None:
                stats["pageLimitHit"] = True
            break
        page += 1


# ---------------------------------------------------------------------------
# Per-act fetch: Riigi Teataja public API (#691)
# ---------------------------------------------------------------------------
#
# Since the RT relaunch on 2026-06-01 the legacy ``/akt/{id}.xml`` path
# answers HTTP 200 ``text/html`` with the Angular app shell instead of act
# XML. The act XML now lives at ``/public-api/api/v1/akt/{id}/xml``
# (served as ``application/octet-stream``) and its JSON metadata sibling at
# ``/public-api/api/v1/akt/{id}``. ``HEAD`` on the ``/xml`` endpoint is a
# 302 to a Keycloak login, so every probe must GET. The search API
# (``SEARCH_URL``) is unchanged and still returns ``url: "/akt/{id}.xml"``;
# ``build_xml_url`` maps that legacy form onto the public API.

PUBLIC_API_AKT_PATH = "/public-api/api/v1/akt"
PUBLIC_API_AKT_URL = BASE_URL + PUBLIC_API_AKT_PATH

# Schema identity of post-relaunch law XML: the root element's namespace
# (``xmlns="tyviseadus_1_10.02.2010"``) and the ``xsi:schemaLocation`` XSD.
RT_LAW_XML_SCHEMA = "tyviseadus_1_10.02.2010"
# Live canary act: Perekonnaseadus (PKS), a stable high-traffic law that is
# also in the ``check_rt_staleness`` sample.
RT_CANARY_ACT_ID = "107052025017"

# Retry convention shared with ``fetch_acts``: ``max_retries`` extra
# attempts with a linear ``retry_sleep * attempt`` backoff.
DEFAULT_FETCH_RETRIES = 2
DEFAULT_RETRY_SLEEP = 1.0

_RT_HOST_SUFFIX = "riigiteataja.ee"
# RT act ids are numeric globaalIDs; any path-safe token is accepted so
# fixtures and odd legacy ids still map (RT itself answers 4xx/5xx for them).
_AKT_ID_RE = re.compile(
    r"/(?:public-api/api/v1/)?akt/([A-Za-z0-9_-]+?)(?:\.xml|/xml)?/?$"
)
_HTML_SNIFF_RE = re.compile(r"^\s*(?:<!doctype\s+html|<html[\s>])", re.IGNORECASE)


class RTFormatError(RuntimeError):
    """Riigi Teataja answered, but not with the expected act payload.

    Raised (never swallowed) when an act endpoint returns HTML — e.g. the
    Angular app shell the legacy ``/akt/{id}.xml`` path serves since the
    2026-06-01 relaunch — or XML outside the pinned schema. This is a
    contract change that needs a code fix, not a transient blip to retry.
    """


def rt_act_id(url_or_id: str | int) -> str:
    """Return the numeric RT act id from an id, act path, or act URL.

    Accepts ``107052025017``, ``/akt/107052025017``, ``/akt/{id}.xml`` (the
    search API's ``url`` form), ``https://www.riigiteataja.ee/akt/{id}``,
    and public-API URLs. Query strings and fragments are ignored.
    """
    text = str(url_or_id).strip()
    if text.isdigit():
        return text
    path = urlsplit(text).path.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    match = _AKT_ID_RE.search(path)
    if match is None:
        raise ValueError(f"not a Riigi Teataja act id or act URL: {url_or_id!r}")
    return match.group(1)


def _public_api_url(url_or_id: str | int, suffix: str) -> str:
    """Public-API URL for an act, keeping a caller's query string.

    Relative paths, bare ids and ``riigiteataja.ee`` URLs resolve against
    ``BASE_URL``. A foreign absolute host is kept (the host allow-list in
    ``allowed_get`` still decides whether it may be fetched).
    """
    act_id = rt_act_id(url_or_id)
    text = str(url_or_id).strip()
    parts = urlsplit(text) if not text.isdigit() else urlsplit("")
    base = urlsplit(BASE_URL)
    host = parts.netloc
    if not host or host.endswith(_RT_HOST_SUFFIX):
        scheme, netloc = base.scheme, base.netloc
    else:
        scheme, netloc = parts.scheme or base.scheme, host
    path = f"{PUBLIC_API_AKT_PATH}/{act_id}{suffix}"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def build_xml_url(url: str | int) -> str:
    """Public-API XML URL for an act id/path/URL (#691).

    ``/akt/123`` and ``/akt/123.xml`` both become
    ``https://www.riigiteataja.ee/public-api/api/v1/akt/123/xml``. The id is
    taken from the URL *path*, so a query string never leaks into it and is
    carried over unchanged (the #389 invariant).
    """
    return _public_api_url(url, "/xml")


def build_metadata_url(url: str | int) -> str:
    """Public-API JSON metadata URL for an act id/path/URL (#691)."""
    return _public_api_url(url, "")


def build_act_page_url(url_or_id: str | int) -> str:
    """Human-readable Riigi Teataja page of one redaction (#707).

    ``https://www.riigiteataja.ee/akt/{globaalID}`` — the page a reader opens,
    as opposed to :func:`build_xml_url`, the machine manifestation. The id is
    taken through :func:`rt_act_id`, so ``/akt/{id}.xml`` (the search API's
    ``url``) and a public-API URL both map onto the same page.
    """
    return f"{BASE_URL}/akt/{rt_act_id(url_or_id)}"


def _gid_number(gid: object) -> int:
    text = str(gid or "").strip()
    return int(text) if text.isdigit() else -1


def redaction_rank(row: dict) -> tuple[str, int, str]:
    """Sort key that ranks the newest redaction of an act highest (#695).

    Riigi Teataja globaalIDs are opaque: ``231052021002`` (23.05.2021) sorts
    above ``107052025017`` (07.05.2025) as a string and as an integer, and the
    5–8 digit legacy ids are a different family altogether. The redaction's
    validity start is the real order: ``kehtivus.algus`` on a search row (or a
    flat ``kehtivuseAlgus`` / ``kehtivusAlgus`` key, as the law generator
    stores it). The integer globaalID only breaks ties between redactions that
    start on the same day; the raw string is the last, deterministic tie-break.
    A row without a parseable start ranks below every dated row.
    """
    start = ""
    kehtivus = row.get("kehtivus")
    if isinstance(kehtivus, dict):
        start = str(kehtivus.get("algus") or "")
    if not start:
        start = str(row.get("kehtivuseAlgus") or row.get("kehtivusAlgus") or "")
    start = start.strip()[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
        start = ""
    gid = row.get("globaalID", row.get("gid"))
    return (start, _gid_number(gid), str(gid or ""))


def is_html_payload(body: str | bytes, content_type: str | None = None) -> bool:
    """True when an RT response is an HTML page rather than XML/JSON."""
    if content_type and "text/html" in content_type.lower():
        return True
    head = body[:512]
    if isinstance(head, bytes):
        head = head.decode("utf-8", errors="replace")
    return bool(_HTML_SNIFF_RE.match(head.lstrip("﻿")))


def _response_content_type(resp) -> str:
    headers = getattr(resp, "headers", None) or {}
    try:
        return str(headers.get("Content-Type") or headers.get("content-type") or "")
    except AttributeError:
        return ""


def _is_transient_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def _get_with_retry(
    url: str,
    *,
    timeout: int,
    max_retries: int = DEFAULT_FETCH_RETRIES,
    retry_sleep: float = DEFAULT_RETRY_SLEEP,
):
    """GET *url* via ``allowed_get``; retry network errors, 429 and 5xx.

    Non-transient HTTP errors (4xx) raise immediately. The last transient
    error is re-raised once the retries are spent.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = allowed_get(url, timeout=timeout)
            status = int(getattr(resp, "status_code", 200) or 200)
            if _is_transient_status(status):
                resp.raise_for_status()
                raise requests.HTTPError(f"HTTP {status} for {url}")
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and not _is_transient_status(int(status)):
                raise
            last_error = e
        except (requests.ConnectionError, requests.Timeout) as e:
            last_error = e
        if attempt < max_retries:
            time.sleep(retry_sleep * (attempt + 1))
    assert last_error is not None
    raise last_error


def _reject_html(resp, url: str, text: str) -> None:
    content_type = _response_content_type(resp)
    if is_html_payload(text, content_type):
        raise RTFormatError(
            f"Riigi Teataja returned HTML instead of act data for {url} "
            f"(Content-Type: {content_type or 'unknown'}); the endpoint "
            "contract changed — see #691"
        )


def _reject_redirect(resp, url: str) -> None:
    status = int(getattr(resp, "status_code", 200) or 200)
    if 300 <= status < 400:
        headers = getattr(resp, "headers", None) or {}
        location = headers.get("Location") or headers.get("location") or "?"
        raise RTFormatError(
            f"Riigi Teataja redirected {url} (HTTP {status}) to {location}; "
            "the public act endpoint may have become auth-gated — see #691"
        )


def fetch_xml(
    url: str,
    cache_name: str,
    cache_subdir: str | None = None,
    *,
    cache_dir: Path | None = None,
    fallback_cache_name: str | None = None,
    refresh: bool = False,
    timeout: int = 60,
    min_size: int = 200,
    validate_root: Callable[[ET.Element], bool] | None = None,
    on_bytes: Callable[[bytes], None] | None = None,
    max_retries: int = DEFAULT_FETCH_RETRIES,
    retry_sleep: float = DEFAULT_RETRY_SLEEP,
    strict: bool = False,
) -> ET.Element | None:
    """Fetch and cache act XML; return its parsed root element.

    The download is a GET of the public-API ``/xml`` endpoint
    (``build_xml_url``, #691), retried on network errors / 429 / 5xx with
    the ``fetch_acts`` linear backoff. ``cache_dir`` overrides the default
    ``DATA_DIR`` / ``cache_subdir`` destination so callers (and tests) can
    redirect the on-disk cache. ``fallback_cache_name`` is consulted after
    ``cache_name`` on a cache hit (law tid-qualified then slug-only files,
    #165). ``validate_root`` rejects a parsed tree (HTML/error pages,
    #601). ``on_bytes`` receives the accepted payload (cache bytes or
    downloaded UTF-8).

    An HTML body always raises :class:`RTFormatError` naming the URL — that
    is an endpoint contract change, and returning ``None`` would let a whole
    refresh silently ``SKIP`` every act (the pre-#691 failure mode). Other
    failures (network, HTTP status, redirect, unparseable / undersized /
    rejected XML) print and return ``None`` unless ``strict`` is set, in
    which case they propagate — the live schema canary uses this to tell
    "RT unreachable" apart from "RT changed format".
    """
    dest = cache_dir if cache_dir is not None else (
        DATA_DIR / cache_subdir if cache_subdir else DATA_DIR
    )
    dest.mkdir(parents=True, exist_ok=True)
    names = [cache_name]
    if fallback_cache_name and fallback_cache_name != cache_name:
        names.append(fallback_cache_name)

    # Reuse any cached file the write path would have accepted. The write
    # guard below only persists XML of at least ``min_size`` bytes, so the
    # read guard must use the SAME threshold — a stricter read guard (the
    # old hard-coded ``> 1000``) silently re-fetched every 200–1000-byte
    # cached file on each run (issue #296). Parse failures still fall
    # through to a fresh fetch, so a corrupt cache self-heals.
    if not refresh:
        for name in names:
            cache_path = dest / f"{name}.xml"
            if not (cache_path.exists() and cache_path.stat().st_size >= min_size):
                continue
            try:
                root = parse_xml_file(cache_path)
            except (ET.ParseError, ValueError):
                continue
            if validate_root is not None and not validate_root(root):
                continue
            if on_bytes is not None:
                on_bytes(cache_path.read_bytes())
            return root

    full_url = str(url)
    try:
        full_url = build_xml_url(url)
        resp = _get_with_retry(
            full_url,
            timeout=timeout,
            max_retries=max_retries,
            retry_sleep=retry_sleep,
        )
        try:
            _reject_redirect(resp, full_url)
        except RTFormatError as e:
            # A redirect can be per-act (e.g. an unpublished redaction behind
            # the login); only the canary treats it as fatal.
            if strict:
                raise
            print(f"    Fetch error: {e}")
            return None
        resp.encoding = "utf-8"
        xml_text = resp.text
        _reject_html(resp, full_url, xml_text)
        if len(xml_text) < min_size:
            if strict:
                raise RTFormatError(
                    f"Riigi Teataja returned {len(xml_text)} bytes for {full_url} "
                    f"(< min_size={min_size})"
                )
            return None
        try:
            root = parse_xml(xml_text)
        except ET.ParseError as e:
            message = f"Riigi Teataja returned unparseable XML for {full_url}: {e}"
            if strict:
                raise RTFormatError(message) from e
            print(f"    Fetch error: {message}")
            return None
        if validate_root is not None and not validate_root(root):
            if strict:
                raise RTFormatError(
                    f"Riigi Teataja XML root <{ln(root.tag)}> from {full_url} "
                    "failed validation"
                )
            return None
        cache_path = dest / f"{cache_name}.xml"
        cache_path.write_text(xml_text, encoding="utf-8")
        if on_bytes is not None:
            on_bytes(xml_text.encode("utf-8"))
        return root
    except RTFormatError:
        raise
    except Exception as e:
        if strict:
            raise
        print(f"    Fetch error ({full_url}): {e}")
        return None


def _utc_instant_to_tallinn_date(value: object) -> str | None:
    """``2024-12-31T22:00:00Z`` → ``2025-01-01`` (Estonian civil date)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else None
    if instant.tzinfo is None:
        return instant.date().isoformat()
    return instant.astimezone(_TALLINN).date().isoformat()


def parse_act_metadata_json(data: dict) -> dict[str, object]:
    """Normalise the public-API act metadata JSON (``GET /akt/{id}``).

    Keys mirror :func:`parse_act_metadata` where the two overlap. Dates are
    Estonian civil dates (the API serves midnight Tallinn as a UTC instant,
    e.g. ``2024-12-31T22:00:00Z`` for 2025-01-01). ``currentId`` is
    ``kehtivId`` — the redaction in force now; when it differs from
    ``actId`` a newer consolidation exists.
    """
    if not isinstance(data, dict):
        raise RTFormatError(f"act metadata is not a JSON object: {type(data).__name__}")
    params = data.get("aktiParameetrid")
    params = params if isinstance(params, dict) else {}

    def _opt_str(value: object) -> str | None:
        if value is None or value == "":
            return None
        return str(value)

    return {
        "currentId": _opt_str(data.get("kehtivId")),
        "terviktekstId": _opt_str(data.get("grupiId")),
        "textKind": _opt_str(data.get("tekstiliik")),
        "documentType": _opt_str(data.get("dokumentliik")),
        "status": _opt_str(data.get("aktiStaatus")),
        "title": _opt_str(params.get("pealkiri")),
        "abbreviation": _opt_str(params.get("lyhend")),
        "entryIntoForce": _utc_instant_to_tallinn_date(params.get("kehtivuseAlgus")),
        "repealDate": _utc_instant_to_tallinn_date(params.get("kehtivuseLopp")),
        "publishedDate": _utc_instant_to_tallinn_date(params.get("avaldamiseKuupaev")),
        "translationId": _opt_str(data.get("tolkeSeosId")),
    }


def fetch_act_metadata(
    url_or_id: str | int,
    *,
    timeout: int = 30,
    max_retries: int = DEFAULT_FETCH_RETRIES,
    retry_sleep: float = DEFAULT_RETRY_SLEEP,
) -> dict[str, object]:
    """GET and parse the public-API JSON metadata for one act (#691).

    Returns :func:`parse_act_metadata_json` plus ``actId`` and ``url``.
    Network / HTTP errors propagate (after retries); an HTML body or a
    non-JSON payload raises :class:`RTFormatError`.
    """
    url = build_metadata_url(url_or_id)
    resp = _get_with_retry(
        url, timeout=timeout, max_retries=max_retries, retry_sleep=retry_sleep
    )
    _reject_redirect(resp, url)
    resp.encoding = "utf-8"
    text = resp.text
    _reject_html(resp, url, text)
    try:
        data = json.loads(text)
    except ValueError as e:
        raise RTFormatError(f"Riigi Teataja act metadata at {url} is not JSON: {e}") from e
    meta = parse_act_metadata_json(data)
    meta["actId"] = rt_act_id(url_or_id)
    meta["url"] = url
    return meta


# ---------------------------------------------------------------------------
# Live schema canary (#691)
# ---------------------------------------------------------------------------

CANARY_OK = "ok"
CANARY_UNREACHABLE = "unreachable"
CANARY_FORMAT_CHANGED = "format-changed"

# Local names the law generator keys off (mirrors the offline fixture
# canary in tests/test_rt_schema_canary.py).
RT_REQUIRED_LOCALNAMES = frozenset(
    {"oigusakt", "metaandmed", "paragrahv", "paragrahvNr", "peatykk"}
)


@dataclass(frozen=True)
class LiveCanaryResult:
    status: str
    url: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == CANARY_OK


def xml_schema_name(root: ET.Element) -> str | None:
    """Schema identity of an RT act root: its namespace URI, if any."""
    tag = root.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return None


def check_act_xml_contract(
    root: ET.Element, *, expected_schema: str = RT_LAW_XML_SCHEMA
) -> list[str]:
    """Return contract violations for a law XML root (empty = conforms)."""
    problems: list[str] = []
    if ln(root.tag) != "oigusakt":
        problems.append(f"root element is <{ln(root.tag)}>, expected <oigusakt>")
    schema = xml_schema_name(root)
    if schema != expected_schema:
        problems.append(f"schema namespace is {schema!r}, expected {expected_schema!r}")
    names = {ln(el.tag) for el in root.iter()}
    missing = sorted(RT_REQUIRED_LOCALNAMES - names)
    if missing:
        problems.append(f"missing generator-contract elements: {missing}")
    return problems


def run_live_schema_canary(
    act_id: str = RT_CANARY_ACT_ID,
    *,
    timeout: int = 60,
    max_retries: int = DEFAULT_FETCH_RETRIES,
    retry_sleep: float = DEFAULT_RETRY_SLEEP,
    expected_schema: str = RT_LAW_XML_SCHEMA,
) -> LiveCanaryResult:
    """GET one live act through :func:`fetch_xml` and classify the outcome.

    * ``ok`` — XML (not HTML) with the pinned schema and generator tags.
    * ``unreachable`` — network error, timeout, 429 or 5xx after retries:
      RT is down or throttling, not evidence of a format change.
    * ``format-changed`` — RT answered, but with HTML, a redirect, a 4xx,
      unparseable XML, or XML outside the pinned contract.
    """
    url = build_xml_url(act_id)
    with tempfile.TemporaryDirectory(prefix="rt-canary-") as tmp:
        try:
            root = fetch_xml(
                act_id,
                cache_name=f"canary_{act_id}",
                cache_dir=Path(tmp),
                refresh=True,
                timeout=timeout,
                max_retries=max_retries,
                retry_sleep=retry_sleep,
                strict=True,
            )
        except RTFormatError as e:
            return LiveCanaryResult(CANARY_FORMAT_CHANGED, url, str(e))
        except (requests.ConnectionError, requests.Timeout) as e:
            return LiveCanaryResult(CANARY_UNREACHABLE, url, f"{type(e).__name__}: {e}")
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is None or _is_transient_status(int(status)):
                return LiveCanaryResult(CANARY_UNREACHABLE, url, f"HTTP error: {e}")
            return LiveCanaryResult(CANARY_FORMAT_CHANGED, url, f"HTTP {status}: {e}")
    if root is None:  # pragma: no cover - strict=True never returns None
        return LiveCanaryResult(CANARY_FORMAT_CHANGED, url, "no XML returned")
    problems = check_act_xml_contract(root, expected_schema=expected_schema)
    if problems:
        return LiveCanaryResult(CANARY_FORMAT_CHANGED, url, "; ".join(problems))
    return LiveCanaryResult(
        CANARY_OK, url, f"<oigusakt> in schema {xml_schema_name(root)}"
    )


# ---------------------------------------------------------------------------
# HTML body fallback (for pre-2010 regulations stored as HTMLKonteiner CDATA)
# ---------------------------------------------------------------------------


def strip_html_tags(text: str) -> str:
    """Remove HTML tags and decode entities; collapse whitespace.

    Tags are stripped again *after* unescape (#554) so ``&lt;script&gt;``
    cannot reconstitute a live tag in published legal text.
    """
    text = _sup_to_unicode(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = _sup_to_unicode(html.unescape(text))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r" ", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_html_konteiner(html_text: str) -> tuple[str, list[dict]]:
    """Parse a `<HTMLKonteiner>` body into (preamble, paragraphs).

    Returns the preamble text (everything before § 1) and a list of
    ``{"nr": "1", "title": "Reguleerimisala", "text": "..."}`` dicts.
    Robust to legacy HTML that mixes `<p>` paragraphs, `<b>§ N.` headers,
    and inline `<br/>` separators.
    """
    if not html_text:
        return "", []

    # Use the bold-§ heading as the paragraph anchor; treat the position of
    # the LAST closing </p> before the next heading as the section boundary.
    # We match the bold-§ form because plain text mentions of `§ 1` inside
    # a sentence should NOT be treated as a new section.
    #
    # The title group has NO trailing ``\s*`` before ``</b>`` (issue #347):
    # a lazy ``[^<]{0,200}?`` plus a trailing ``\s*`` both match whitespace,
    # so an unclosed ``<b>§ N. ` + many spaces (malformed pre-2010
    # HTMLKonteiner) forces the engine to try every whitespace partition —
    # catastrophic backtracking, O(N²). Dropping the trailing ``\s*`` removes
    # the ambiguity; the captured title is ``.strip()``-ed downstream (:296),
    # so trailing whitespace before ``</b>`` is discarded regardless.
    heading_re = re.compile(
        r"<b[^>]*>\s*§\s*(\d+(?:[′'·]\d+)?)\s*\.?\s*([^<]{0,200}?)</b>",
        re.IGNORECASE,
    )
    matches = list(heading_re.finditer(html_text))

    if not matches:
        # No bold headings found — try a looser pattern that accepts any
        # tag-bracketed § marker followed by a period and title.
        loose_re = re.compile(
            r"§\s*(\d+(?:[′'·]\d+)?)\s*\.\s*([A-ZÄÖÜÕŠŽ][^<\n]{0,200})",
            re.MULTILINE,
        )
        matches = list(loose_re.finditer(html_text))

    if not matches:
        return strip_html_tags(html_text), []

    preamble = strip_html_tags(html_text[: matches[0].start()])

    paragraphs: list[dict] = []
    for i, m in enumerate(matches):
        nr = m.group(1).strip()
        title = strip_html_tags(m.group(2)).strip().rstrip(".")
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(html_text)
        body = strip_html_tags(html_text[body_start:body_end])
        paragraphs.append({"nr": nr, "title": title, "text": body})
    return preamble, paragraphs


# `iter_peep_files`, `KRR_DIR`, `NS`, `CONTEXT`, `sanitize_id`, `slugify`,
# `save_json`, and the Estonian transliteration table are re-exported from
# `estleg_common` so a single canonical implementation lives there — see the
# import at the top.


# ---------------------------------------------------------------------------
# Metadata extraction shared between law and regulation acts
# ---------------------------------------------------------------------------

def parse_act_metadata(root: ET.Element) -> dict[str, str | None]:
    """Extract `<metaandmed>` fields shared by laws and regulations.

    Returns a dict with:
      * `globalId`           — `<globaalID>`
      * `terviktekstId`      — `<terviktekstiGrupiID>`
      * `documentType`       — `<dokumentLiik>` (e.g. `määrus`, `seadus`)
      * `issuer`             — `<valjaandja>`
      * `actNumber`          — `<vastuvoetud><aktiNr>`
      * `entryIntoForce`     — `<kehtivus><kehtivuseAlgus>` (date, no offset)
      * `repealDate`         — `<kehtivus><kehtivuseLopp>` (or None)
      * `lastAmendmentDate`  — date of the latest `<muutmismarge>`, or None
      * `schemaName`         — `<skeemiNimi>` (the XSD the text follows, #692)
      * `originalEntryIntoForce` — `<vastuvoetud><joustumine>`: when the act
        itself entered into force, as opposed to `entryIntoForce`, the start
        of this redaction's validity (#695)
    """
    meta: dict[str, str | None] = {
        "globalId": None,
        "terviktekstId": None,
        "documentType": None,
        "issuer": None,
        "actNumber": None,
        "entryIntoForce": None,
        "repealDate": None,
        "lastAmendmentDate": None,
        "schemaName": None,
        "originalEntryIntoForce": None,
    }

    def _strip_offset(date_str: str | None) -> str | None:
        if not date_str:
            return None
        # Riigi Teataja dates often carry a TZ offset like "2024-07-29+03:00".
        cleaned = date_str.split("+", 1)[0].split("Z", 1)[0]
        # #352: a well-formed 2918-10-17 would otherwise string-compare as
        # the latest lastAmendmentDate. Same 1900..2100 band as
        # extract_temporal_data.parse_date (no extra parser).
        year_token = cleaned[:4]
        if year_token.isdigit():
            year = int(year_token)
            if year < 1900 or year > 2100:
                return None
        return cleaned

    for el in root.iter():
        tag = ln(el.tag)
        if tag == "metaandmed":
            for child in el:
                ctag = ln(child.tag)
                if ctag == "globaalID" and child.text:
                    meta["globalId"] = child.text.strip()
                elif ctag == "terviktekstiGrupiID" and child.text:
                    meta["terviktekstId"] = child.text.strip()
                elif ctag == "dokumentLiik" and child.text:
                    meta["documentType"] = child.text.strip()
                elif ctag == "valjaandja" and child.text:
                    meta["issuer"] = child.text.strip()
                elif ctag == "skeemiNimi" and child.text:
                    meta["schemaName"] = child.text.strip()
                elif ctag == "vastuvoetud":
                    nr = ct(child, "aktiNr")
                    if nr:
                        meta["actNumber"] = nr
                    joustumine = ct(child, "joustumine")
                    if joustumine:
                        meta["originalEntryIntoForce"] = _strip_offset(joustumine)
                elif ctag == "kehtivus":
                    algus = ct(child, "kehtivuseAlgus")
                    lopp = ct(child, "kehtivuseLopp")
                    if algus:
                        meta["entryIntoForce"] = _strip_offset(algus)
                    if lopp:
                        meta["repealDate"] = _strip_offset(lopp)
            break

    # Latest <muutmismarge> at the act level → lastAmendmentDate.
    latest: str | None = None
    for el in root.iter():
        if ln(el.tag) != "muutmismarge":
            continue
        akt = ct(el, "aktikuupaev")
        if akt:
            akt_clean = _strip_offset(akt)
            if akt_clean and (latest is None or akt_clean > latest):
                latest = akt_clean
    meta["lastAmendmentDate"] = latest

    return meta
