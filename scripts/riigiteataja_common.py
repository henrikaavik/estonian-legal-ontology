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

from estleg_common import KRR_DIR, iter_peep_files, save_json as _save_json  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"

SEARCH_URL = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
BASE_URL = "https://www.riigiteataja.ee"
NS = "https://data.riik.ee/ontology/estleg#"

CONTEXT: dict[str, str] = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}


class SourceListFetchError(RuntimeError):
    """Raised when a source-list page cannot be fetched completely."""

_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def ln(tag: str) -> str:
    """Strip an XML namespace prefix and return the local tag name."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    """Return the text of the first direct child whose local tag matches `name`."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


def slugify(text: str, max_len: int = 80) -> str:
    """Convert Estonian text to a filename-safe slug."""
    text = text.translate(_TRANSLIT_TABLE)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:max_len]


def sanitize_id(value: str) -> str:
    """Create an ASCII-only ID component safe for use inside an IRI."""
    s = value.replace(" ", "_").translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s or "Unknown"


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


def save_json(filepath: Path, doc: dict) -> None:
    _save_json(filepath, doc)


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
) -> Iterator[dict]:
    """Yield acts from the Riigi Teataja search API page by page.

    Parameters mirror the API exactly:
      * `document` — `seadus` or `määrus`
      * `kov` — `False` to exclude KOV regulations, `True` for KOV-only,
        `None` to omit the parameter (returns both)
      * `kehtiv` — ISO date for the snapshot (`YYYY-MM-DD`); when set the
        API returns only acts in force on that day
    """
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
        for attempt in range(max_retries + 1):
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
            message = f"API error on page {page}: {last_error}"
            print(f"  {message}")
            if allow_partial:
                break
            raise SourceListFetchError(message) from last_error

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

# Match the start of a regulation paragraph in HTML body text.
# Examples we want to catch:
#   <b>§ 1. Reguleerimisala</b>
#   <b>§ 2. Tuukrite tervisenõuded</b>
#   §-de 5–7
# We match the literal `§` symbol followed by a paragraph number and (usually)
# a period and a heading. The trailing capture is optional because some legacy
# regulations only have `§ 1` without a heading.
_HTML_PARAGRAPH_RE = re.compile(
    r"§\s*(\d+(?:[′'·]\d+)?)\s*[\. ]?\s*([^<\n]{0,200}?)(?=<|$)",
    re.MULTILINE,
)


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


# `iter_peep_files` and `KRR_DIR` are re-exported from `estleg_common` so a
# single canonical implementation lives there — see the import at the top.


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
