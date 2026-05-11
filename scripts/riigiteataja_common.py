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
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

import requests

# Single source of truth: NS, CONTEXT, the Estonian transliteration table,
# sanitize_id, slugify, and save_json all live in estleg_common. They are
# re-exported here so legacy `from riigiteataja_common import ...` callers
# (e.g. generate_regulations.py) keep working without churn.
from estleg_common import (  # noqa: F401  -- re-exports for public API
    CONTEXT,
    KRR_DIR,
    NS,
    _ESTONIAN_TRANSLITERATION,
    _TRANSLIT_TABLE,
    iter_peep_files,
    sanitize_id,
    save_json,
    slugify,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"

SEARCH_URL = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
BASE_URL = "https://www.riigiteataja.ee"


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


def ct(el: ET.Element, name: str) -> str | None:
    """Return the text of the first direct child whose local tag matches `name`."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


def collect_text(el: ET.Element, max_len: int = 500) -> str:
    """Concatenate provision text up to `max_len` characters (for summaries)."""
    parts: list[str] = []
    for child in el.iter():
        tag = ln(child.tag)
        if tag in ("loige", "lauseOsa", "lause", "tavatekst"):
            txt = "".join(child.itertext()).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and len(txt) > 3:
                parts.append(txt)
        if len(" ".join(parts)) >= max_len:
            break
    joined = " ".join(parts)
    return joined[:max_len] if joined else ""


def collect_full_text(el: ET.Element) -> str:
    """Return the complete provision text without truncation."""
    parts: list[str] = []
    for child in el.iter():
        tag = ln(child.tag)
        if tag in ("loige", "lauseOsa", "lause", "tavatekst"):
            txt = "".join(child.itertext()).strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and len(txt) > 3:
                parts.append(txt)
    return " ".join(parts)


def fetch_acts(
    document: str,
    *,
    kov: bool | None = None,
    kehtiv: str | None = None,
    text_filter: str = "terviktekst",
    limiit: int = 500,
    max_pages: int = 100,
    timeout: int = 30,
    allow_partial: bool = False,
    max_retries: int = 2,
    retry_sleep: float = 1.0,
    stats: dict | None = None,
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
            "limiit": limiit,
            "dokument": document,
            "tekst": text_filter,
        }
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
                resp = requests.get(SEARCH_URL, params=params, timeout=timeout)
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
        if len(aktid) < limiit:
            break
        page += 1


def fetch_xml(
    url: str,
    cache_name: str,
    cache_subdir: str | None = None,
    *,
    refresh: bool = False,
    timeout: int = 60,
    min_size: int = 200,
) -> ET.Element | None:
    """Fetch and cache act XML; return its parsed root element."""
    cache_dir = DATA_DIR / cache_subdir if cache_subdir else DATA_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_name}.xml"

    if not refresh and cache_path.exists() and cache_path.stat().st_size > 1000:
        try:
            return ET.parse(str(cache_path)).getroot()
        except ET.ParseError:
            pass

    full_url = BASE_URL + url if url.startswith("/") else url
    if not full_url.endswith(".xml"):
        full_url = full_url + ".xml"

    try:
        resp = requests.get(full_url, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        xml_text = resp.text
        if len(xml_text) < min_size:
            return None
        cache_path.write_text(xml_text, encoding="utf-8")
        return ET.fromstring(xml_text)
    except Exception as e:
        print(f"    Fetch error: {e}")
        return None


# ---------------------------------------------------------------------------
# HTML body fallback (for pre-2010 regulations stored as HTMLKonteiner CDATA)
# ---------------------------------------------------------------------------


def strip_html_tags(text: str) -> str:
    """Remove HTML tags and decode entities; collapse whitespace."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
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
    heading_re = re.compile(
        r"<b[^>]*>\s*§\s*(\d+(?:[′'·]\d+)?)\s*\.?\s*([^<]{0,200}?)\s*</b>",
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
    }

    def _strip_offset(date_str: str | None) -> str | None:
        if not date_str:
            return None
        # Riigi Teataja dates often carry a TZ offset like "2024-07-29+03:00".
        return date_str.split("+", 1)[0].split("Z", 1)[0]

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
                elif ctag == "vastuvoetud":
                    nr = ct(child, "aktiNr")
                    if nr:
                        meta["actNumber"] = nr
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
