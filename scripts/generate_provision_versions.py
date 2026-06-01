#!/usr/bin/env python3
"""Populate ``estleg:ProvisionVersion`` nodes from historical Riigi Teataja redactions.

The enacted-law corpus is *current-snapshot-only*: every ``estleg:LegalProvision``
carries the text from one selected consolidated text (``terviktekst``). This script
adds the historical dimension defined by issue #131 / #198: for each law it walks the
Riigi Teataja redaction history (the chronological list of consolidated-text editions,
each with its own ``globaalID`` and entry-into-force date), re-fetches the act at every
redaction point, diffs at the provision level, and emits one ``estleg:ProvisionVersion``
per redaction *in which a provision's text actually changed* — chained with
``estleg:supersededByVersion`` and carrying ``estleg:versionText`` /
``estleg:versionValidFrom`` / ``estleg:versionValidTo`` / ``estleg:versionRedactionId``.

Output placement (issue #198 iteration — "infra + sample, full run is a follow-up"):
  * the ProvisionVersion nodes go to a sidecar ``krr_outputs/provision_versions/<slug>.jsonld``
    (one file per processed law) — mirroring ``krr_outputs/sanctions/`` and
    ``krr_outputs/amendments/``; ``shacl_validate_all.py``'s ``sidecars`` bucket validates
    the directory against ``estleg:ProvisionVersionShape``;
  * each ProvisionVersion links *into* the law via ``estleg:versionOf`` (a one-directional
    edge to the existing ``estleg:LegalProvision`` IRI). To make the *head* version of
    each chain forward-reachable (issue #271 — ``versionOf`` alone leaves the current
    version with zero inbound edges), the sidecar **also** carries one back-link stub per
    versioned provision: a node keyed on the stable ``estleg:LegalProvision`` IRI with
    ``estleg:currentVersion`` -> head ``ProvisionVersion`` and ``estleg:hasVersion`` ->
    every ``ProvisionVersion`` of that provision. Both objects are version nodes emitted
    into *this same sidecar*, so the edges resolve locally and never dangle (the original
    #198 objection was specifically that emitting these into the *law peep* would point at
    sidecar-only ids). The stubs carry no ``estleg:paragrahv`` / ``@type``, so neither
    ``LegalProvisionShape`` (``sh:targetSubjectsOf estleg:paragrahv``) nor
    ``ProvisionVersionShape`` (``sh:targetClass estleg:ProvisionVersion``) fires on them.
  * a coverage report goes to ``krr_outputs/reports/kov/extract_provision_versions_coverage.json``
    (the same place the other pipeline coverage reports live).

Redaction enumeration (how we get the history from Riigi Teataja):
  The act-search API (``/api/oigusakt_otsing/1/otsi``) returns, for ``?dokument=seadus&
  tekst=terviktekst&pealkiri=<title>`` *without* a ``kehtiv`` filter, every consolidated-text
  edition the act ever had — most carry a ``kehtivus`` interval ``{algus, lopp}``. Entries
  with a closed interval (``lopp`` set) are superseded redactions; deduped by ``algus`` and
  sorted ascending they form the chronological history. The *current* redaction is whatever
  ``?kehtiv=<today>`` returns (exactly one entry). The full chain is therefore
  ``[superseded redactions older than today, oldest->newest] + [current redaction]``. Each
  redaction's XML is fetched from ``/akt/<globaalID>.xml`` and cached under
  ``data/riigiteataja/provision_versions/`` keyed on the ``globaalID`` (gitignored), so
  re-runs are offline-fast.

Usage::

    python3 scripts/generate_provision_versions.py --limit 2
    python3 scripts/generate_provision_versions.py --law tulumaksuseadus --law kaibemaksuseadus
    python3 scripts/generate_provision_versions.py --law karistusseadustik --max-redactions 0   # full history

``--max-redactions N`` caps each law to its N most recent redactions (default 12 — keeps a
sample run bounded; pass ``0`` for the unbounded full-history run that the follow-up issue
tracks).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from estleg_common import CONTEXT, KRR_DIR, iter_peep_files  # noqa: E402
from generate_all_laws import _paragraph_id_suffix, collect_full_text  # noqa: E402
from kov_pipeline_coverage import (  # noqa: E402
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)
from riigiteataja_common import DATA_DIR, ln  # noqa: E402

SEARCH_URL = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
BASE_URL = "https://www.riigiteataja.ee"

VERSIONS_DIR = KRR_DIR / "provision_versions"
XML_CACHE_SUBDIR = "provision_versions"
COVERAGE_PATH = KRR_DIR / "reports" / "kov" / "extract_provision_versions_coverage.json"
LAW_REPORT_NAME = "provision_versions_report.json"

DEFAULT_LIMIT = 2
DEFAULT_MAX_REDACTIONS = 12
FETCH_SLEEP_SECONDS = 0.3
LAW_REPORT_SCHEMA_VERSION = 1

# A curated default selection of heavily-amended laws (slug form) used when neither
# ``--law`` nor an explicit list is given; ``--limit N`` takes the first N that have a
# peep on disk. Ordered roughly by amendment frequency.
DEFAULT_LAW_SLUGS: tuple[str, ...] = (
    "tulumaksuseadus",
    "kaibemaksuseadus",
    "karistusseadustik",
    "tsiviilseadustiku_uldosa_seadus",
    "kaubandustegevuse_seadus",
    "liiklusseadus",
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Redaction:
    """One consolidated-text edition of an act."""

    global_id: str
    valid_from: str | None  # ISO date (entry into force) — may be None for old base texts
    valid_to: str | None    # ISO date (superseded on) — None ⇒ still current
    url: str                # e.g. "/akt/122122025002.xml"


@dataclass
class LawTarget:
    """A law selected for processing."""

    slug: str
    title: str
    prefix: str  # the IRI prefix already in use in the peep(s), e.g. "TULUMA" / "KARIST_2"
    # par_suffix -> provision IRI (e.g. "1" -> "estleg:TULUMA_Par_1", "22_1" -> "...")
    provisions: dict[str, str]
    peep_files: list[Path]


# ---------------------------------------------------------------------------
# Peep introspection — discover the law's provision IRIs (the link targets)
# ---------------------------------------------------------------------------


_PROVISION_TYPE_RE = re.compile(r"^estleg:LegalProvision_")
_PAR_IRI_RE = re.compile(r"_Par_(?P<suffix>[0-9]+(?:_[0-9]+)?)$")


def _peep_files_for_slug(slug: str, krr_dir: Path = KRR_DIR) -> list[Path]:
    """Return the peep file(s) for a law slug.

    Single-file laws live at ``<slug>_peep.json``; the large multi-part laws
    (Karistusseadustik, Asjaõigusseadus, ...) are split across
    ``<slug>_osa<N>_peep.json`` — all of them share one IRI prefix, so we merge
    their provision maps.
    """
    single = krr_dir / f"{slug}_peep.json"
    if single.exists():
        return [single]
    osa = sorted(krr_dir.glob(f"{slug}_osa*_peep.json"))
    return osa


def _load_provision_map(peep_files: list[Path]) -> tuple[dict[str, str], str | None, str | None]:
    """Scan peep files; return ``(par_suffix -> provision IRI, prefix, title)``.

    ``prefix`` is the common IRI prefix (the segment before ``_Par_``); ``title`` is
    the ``dc:source`` of the ontology node (the human-readable act name).
    """
    provisions: dict[str, str] = {}
    prefix: str | None = None
    title: str | None = None
    for path in peep_files:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            iri = node.get("@id")
            if not isinstance(iri, str):
                continue
            if "owl:Ontology" in types and title is None:
                src = node.get("dc:source")
                if isinstance(src, str):
                    title = src
                elif isinstance(src, list) and src and isinstance(src[0], str):
                    title = src[0]
            is_provision = any(_PROVISION_TYPE_RE.match(t) for t in types if isinstance(t, str))
            m = _PAR_IRI_RE.search(iri)
            if is_provision and m:
                suffix = m.group("suffix")
                provisions.setdefault(suffix, iri)
                if prefix is None:
                    prefix = iri[: m.start()].removeprefix("estleg:")
    return provisions, prefix, title


def build_law_target(slug: str, krr_dir: Path = KRR_DIR) -> LawTarget | None:
    """Build a :class:`LawTarget` for ``slug`` from its peep file(s) on disk."""
    peep_files = _peep_files_for_slug(slug, krr_dir)
    if not peep_files:
        return None
    provisions, prefix, title = _load_provision_map(peep_files)
    if not provisions or not prefix or not title:
        return None
    return LawTarget(
        slug=slug,
        title=title,
        prefix=prefix,
        provisions=provisions,
        peep_files=peep_files,
    )


# ---------------------------------------------------------------------------
# Riigi Teataja: redaction enumeration
# ---------------------------------------------------------------------------


_ISO_DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")


def _strip_offset(value: str | None) -> str | None:
    """Normalise an RT timestamp to a bare ``YYYY-MM-DD`` calendar date.

    RT ``kehtivus`` endpoints arrive in several shapes — ``2020-01-01``,
    ``2020-01-01T00:00:00``, ``2020-01-01 00:00:00+02:00``, ``2020-01-01T...Z``
    and (defensively) negative offsets like ``...-05:00``. The downstream chain
    logic compares these against a ``YYYY-MM-DD`` ``today``, so we must drop any
    ``T``/space-separated time component *and* any signed (``+``/``-``) or ``Z``
    timezone offset uniformly, leaving only the date. We prefer the strict
    ``datetime`` parse (which understands all of the above) and fall back to a
    leading-date regex for anything it cannot parse, returning ``None`` only when
    no calendar date is present at all.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(candidate).date().isoformat()
    except ValueError:
        pass
    match = _ISO_DATE_RE.match(text)
    if match:
        try:
            return date.fromisoformat(match.group("date")).isoformat()
        except ValueError:
            return None
    return None


def _date_before(left: str, right: str, *, inclusive: bool) -> bool:
    """Return ``left <= right`` (``inclusive``) / ``left < right`` as calendar dates.

    Both arguments are expected to be ``_strip_offset``-normalised ``YYYY-MM-DD``
    strings; if either cannot be parsed as a date we fall back to a lexicographic
    comparison (which is order-preserving for fixed-width ISO dates).
    """
    try:
        left_d = date.fromisoformat(left)
        right_d = date.fromisoformat(right)
    except (TypeError, ValueError):
        return left <= right if inclusive else left < right
    return left_d <= right_d if inclusive else left_d < right_d


def _search(params: dict[str, object], *, timeout: int = 30) -> list[dict]:
    resp = requests.get(SEARCH_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data.get("aktid", []) or []


def fetch_current_redaction(title: str, *, today: str, timeout: int = 30) -> Redaction | None:
    """Return the consolidated-text edition in force on ``today`` for ``title``."""
    aktid = _search(
        {
            "leht": 1,
            "limiit": 500,
            "dokument": "seadus",
            "tekst": "terviktekst",
            "kehtiv": today,
            "kehtivKehtetus": "false",
            "mitteJoustunud": "false",
            "pealkiri": title,
        },
        timeout=timeout,
    )
    matches = [a for a in aktid if (a.get("pealkiri") or "").strip() == title]
    if not matches:
        return None
    # ``?kehtiv`` should return exactly one current consolidated text; if a quirk
    # yields several, take the one with the latest entry-into-force.
    best = max(matches, key=lambda a: (a.get("kehtivus") or {}).get("algus") or "")
    keh = best.get("kehtivus") or {}
    return Redaction(
        global_id=str(best.get("globaalID", "")),
        valid_from=_strip_offset(keh.get("algus")),
        valid_to=_strip_offset(keh.get("lopp")),
        url=str(best.get("url") or f"/akt/{best.get('globaalID')}.xml"),
    )


def fetch_redaction_chain(
    title: str,
    *,
    today: str | None = None,
    timeout: int = 30,
) -> list[Redaction]:
    """Return the chronological redaction chain (oldest -> newest, current last).

    Reconstructed from the act-search API: superseded editions (closed ``kehtivus``
    interval), deduped by entry-into-force date and sorted ascending, plus the single
    edition currently in force (from a ``?kehtiv=<today>`` query). Future-dated editions
    (entry into force after ``today``) are excluded — they are not yet in force.
    """
    today = today or date.today().isoformat()
    # Compare validity-interval endpoints as calendar dates (not as raw strings):
    # ``_strip_offset`` already normalises each endpoint to ``YYYY-MM-DD``, and we
    # normalise ``today`` the same way so a caller passing a ``T``-time/offset
    # ``today`` is handled too. Fall back to the original string compare only if
    # ``today`` is somehow unparseable.
    today_date = _strip_offset(today) or today
    aktid = _search(
        {
            "leht": 1,
            "limiit": 500,
            "dokument": "seadus",
            "tekst": "terviktekst",
            "pealkiri": title,
        },
        timeout=timeout,
    )
    matches = [a for a in aktid if (a.get("pealkiri") or "").strip() == title]

    # Superseded editions: closed interval, both endpoints present, and the edition
    # ended strictly before today (so it is genuinely historical). Dedupe by entry-
    # into-force date keeping the most-recently-touched metadata record.
    by_from: dict[str, dict] = {}
    for a in matches:
        keh = a.get("kehtivus") or {}
        algus = _strip_offset(keh.get("algus"))
        lopp = _strip_offset(keh.get("lopp"))
        if not algus or not lopp:
            continue
        # Keep iff the edition started on/before today (algus <= today) AND it had
        # already ended before today (lopp < today). Equivalent to the original
        # ``algus > today or lopp >= today`` skip, but compared as dates.
        if not (
            _date_before(algus, today_date, inclusive=True)
            and _date_before(lopp, today_date, inclusive=False)
        ):
            continue
        existing = by_from.get(algus)
        if existing is None or (a.get("muudetud") or 0) > (existing.get("muudetud") or 0):
            by_from[algus] = a

    chain: list[Redaction] = []
    for algus in sorted(by_from):
        a = by_from[algus]
        keh = a.get("kehtivus") or {}
        chain.append(
            Redaction(
                global_id=str(a.get("globaalID", "")),
                valid_from=algus,
                valid_to=_strip_offset(keh.get("lopp")),
                url=str(a.get("url") or f"/akt/{a.get('globaalID')}.xml"),
            )
        )

    current = fetch_current_redaction(title, today=today, timeout=timeout)
    if current is not None:
        # Avoid duplicating the current edition if it already appeared in the chain
        # (it would not — current has lopp >= today — but be defensive).
        if not any(r.global_id == current.global_id for r in chain):
            chain.append(current)

    return chain


# ---------------------------------------------------------------------------
# Riigi Teataja: per-redaction XML fetch + provision-text extraction
# ---------------------------------------------------------------------------


def _xml_cache_path(slug: str, global_id: str) -> Path:
    cache_dir = DATA_DIR / XML_CACHE_SUBDIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_]", "", global_id) or "unknown"
    return cache_dir / f"{slug}__g{safe}.xml"


def fetch_redaction_xml(
    slug: str,
    redaction: Redaction,
    *,
    timeout: int = 60,
    sleep: float = FETCH_SLEEP_SECONDS,
) -> ET.Element | None:
    """Fetch (and cache) the XML for one redaction; return its parsed root.

    Cache is keyed on ``slug`` + ``globaalID`` under ``data/riigiteataja/provision_versions/``
    (gitignored), so historical fetches are done once.
    """
    cache_path = _xml_cache_path(slug, redaction.global_id)
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        try:
            return ET.parse(str(cache_path)).getroot()
        except ET.ParseError:
            pass
    url = redaction.url
    full_url = BASE_URL + url if url.startswith("/") else url
    if not full_url.endswith(".xml"):
        full_url = full_url + ".xml"
    try:
        if sleep:
            time.sleep(sleep)
        resp = requests.get(full_url, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        text = resp.text
        if len(text) < 200:
            return None
        cache_path.write_text(text, encoding="utf-8")
        return ET.fromstring(text)
    except Exception as exc:  # noqa: BLE001 — surface the API error, keep going
        print(f"    fetch error for {slug} g{redaction.global_id}: {exc}")
        return None


def extract_provision_texts(root: ET.Element) -> dict[str, str]:
    """Return ``{par_suffix: full_text}`` for every ``paragrahv`` in a redaction XML.

    ``par_suffix`` is computed by :func:`generate_all_laws._paragraph_id_suffix` so it
    matches the ``_Par_<suffix>`` segment of the corresponding ``estleg:LegalProvision``
    IRI exactly (including superscript handling, e.g. ``§ 22¹`` -> ``22_1``). When a
    redaction repeats the same paragraph number (rare data quirk) the first occurrence
    wins.
    """
    out: dict[str, str] = {}
    for el in root.iter():
        if ln(el.tag) != "paragrahv":
            continue
        suffix = _paragraph_id_suffix(el)
        if not suffix or suffix == "Unknown":
            continue
        text = collect_full_text(el).strip()
        if not text:
            continue
        out.setdefault(suffix, text)
    return out


# ---------------------------------------------------------------------------
# Diff + ProvisionVersion synthesis
# ---------------------------------------------------------------------------


def _xsd_date(value: str) -> dict:
    return {"@value": value, "@type": "xsd:date"}


def _normalise_text(text: str) -> str:
    """Collapse insignificant whitespace differences before diffing."""
    return re.sub(r"\s+", " ", text or "").strip()


def synthesise_versions(
    target: LawTarget,
    redaction_texts: list[tuple[Redaction, dict[str, str]]],
) -> list[dict]:
    """Build the ProvisionVersion ``@graph`` nodes for a law.

    ``redaction_texts`` is the chain (oldest -> newest) paired with the
    ``{par_suffix: text}`` extracted from each redaction's XML.

    For every provision known in the law's peep, we walk the chain tracking the
    current text; each time the text changes (or first appears) we emit a new
    ``estleg:ProvisionVersion`` (``<provision IRI>_v<globalId>``). Consecutive versions
    of the same provision are chained with ``estleg:supersededByVersion``; ``versionValidTo``
    of version *k* is set to ``versionValidFrom`` of version *k+1* (the next change);
    the last version stays open (no ``versionValidTo`` / ``supersededByVersion``).
    """
    nodes: list[dict] = []
    # Group emitted version IRIs per provision so we can chain afterwards.
    per_provision: dict[str, list[dict]] = {}
    # Guard ProvisionVersion @id uniqueness across the whole sidecar. The id is
    # ``<provision IRI>_v<globalId>``; an empty/duplicate ``globalId`` would
    # otherwise collide (e.g. two redactions both missing a ``globaalID`` for the
    # same provision -> identical ``..._v`` id). Mirror the amendment generator's
    # disambiguation so re-runs stay idempotent.
    seen_version_ids: set[str] = set()

    for suffix, provision_iri in sorted(target.provisions.items()):
        current_norm: str | None = None
        versions_here: list[dict] = []
        for redaction, texts in redaction_texts:
            text = texts.get(suffix)
            if text is None:
                # Provision absent from this redaction (not yet enacted, or text could
                # not be extracted). Skip — do not break the chain.
                continue
            norm = _normalise_text(text)
            if norm == current_norm:
                continue  # unchanged at this redaction — no new version
            current_norm = norm
            if not redaction.valid_from:
                # No entry-into-force date for this redaction — we cannot give the
                # version a (required) ``versionValidFrom``. Treat the text as carried
                # forward rather than emit an invalid node.
                continue
            if not redaction.global_id:
                # No redaction id ⇒ no stable, non-colliding version identity and no
                # ``versionRedactionId``. Carry the text forward (advance current_norm
                # above) rather than emit a node with an ambiguous ``..._v`` @id.
                continue
            version_iri = f"{provision_iri}_v{redaction.global_id}"
            # Disambiguate any residual collision (belt-and-braces; empty ids are
            # already skipped above) so the sidecar never carries a duplicate @id.
            base_version_iri = version_iri
            disambig = 1
            while version_iri in seen_version_ids:
                disambig += 1
                version_iri = f"{base_version_iri}_{disambig}"
            seen_version_ids.add(version_iri)
            node = {
                "@id": version_iri,
                "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
                "estleg:versionOf": {"@id": provision_iri},
                "estleg:versionValidFrom": _xsd_date(redaction.valid_from),
                "estleg:versionText": text,
                "rdfs:label": {
                    "@value": f"{provision_iri.removeprefix('estleg:')} (redaktsioon {redaction.global_id})",
                    "@language": "et",
                },
            }
            if redaction.global_id:
                node["estleg:versionRedactionId"] = redaction.global_id
            versions_here.append(node)
        if versions_here:
            per_provision[provision_iri] = versions_here
            nodes.extend(versions_here)

    # Chain consecutive versions: supersededByVersion + close validity intervals.
    # ``versionValidTo`` of version k == ``versionValidFrom`` of version k+1 (the next
    # *change*); the last version stays open (still current).
    for versions_here in per_provision.values():
        for i, node in enumerate(versions_here[:-1]):
            nxt = versions_here[i + 1]
            node["estleg:supersededByVersion"] = {"@id": nxt["@id"]}
            node["estleg:versionValidTo"] = _xsd_date(nxt["estleg:versionValidFrom"]["@value"])
    return nodes


def build_provision_backlinks(version_nodes: list[dict]) -> list[dict]:
    """Derive ``LegalProvision`` forward-link nodes from a list of version nodes.

    Issue #271: ``versionOf`` is one-directional, so the *head* version of each
    chain (the one nothing supersedes) is unreachable by forward traversal — a
    consumer cannot navigate ``LegalProvision -> current ProvisionVersion``. We
    materialise that edge by emitting, per provision, a node keyed on the stable
    ``estleg:LegalProvision`` IRI carrying:

    * ``estleg:currentVersion`` -> the head ``ProvisionVersion`` (the node with no
      ``estleg:supersededByVersion``), and
    * ``estleg:hasVersion`` -> every ``ProvisionVersion`` of that provision (the
      inverse of ``versionOf``; safe to emit now that all version nodes exist in
      the same sidecar).

    Every referenced version IRI is present in ``version_nodes`` (same sidecar), so
    no edge dangles. These back-link nodes deliberately carry **no**
    ``estleg:paragrahv`` and **no** ``@type``: ``estleg:LegalProvisionShape`` targets
    ``sh:targetSubjectsOf estleg:paragrahv`` and ``estleg:ProvisionVersionShape``
    targets ``estleg:ProvisionVersion``, so neither shape fires on these merge
    stubs — they only contribute the (optional, ``sh:nodeKind sh:IRI``)
    ``currentVersion`` / ``hasVersion`` edges onto the existing provision node.
    """
    # Preserve first-seen provision order; collect versions and detect the head.
    order: list[str] = []
    versions_by_provision: dict[str, list[str]] = {}
    head_by_provision: dict[str, str] = {}
    for node in version_nodes:
        vof = node.get("estleg:versionOf")
        vid = node.get("@id")
        if not isinstance(vof, dict) or not isinstance(vid, str):
            continue
        provision_iri = vof.get("@id")
        if not isinstance(provision_iri, str) or not provision_iri:
            continue
        if provision_iri not in versions_by_provision:
            versions_by_provision[provision_iri] = []
            order.append(provision_iri)
        versions_by_provision[provision_iri].append(vid)
        # The head is the (unique) version with no successor. Last-writer-wins is
        # fine: a well-formed chain has exactly one such node.
        if "estleg:supersededByVersion" not in node:
            head_by_provision[provision_iri] = vid

    backlinks: list[dict] = []
    for provision_iri in order:
        version_ids = versions_by_provision[provision_iri]
        node: dict = {
            "@id": provision_iri,
            "estleg:hasVersion": [{"@id": vid} for vid in version_ids],
        }
        head = head_by_provision.get(provision_iri)
        if head is not None:
            node["estleg:currentVersion"] = {"@id": head}
        backlinks.append(node)
    return backlinks


def _rel(path: Path) -> str:
    """``path`` relative to the repo root when possible, else the path itself."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Sidecar writing
# ---------------------------------------------------------------------------


def write_sidecar(target: LawTarget, version_nodes: list[dict], *, out_dir: Path = VERSIONS_DIR) -> Path:
    """Write ``<out_dir>/<slug>.jsonld`` with a small ``@context`` + the version nodes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # The map node is a minimal ``owl:Ontology`` header (mirroring the sanctions /
    # amendments sidecars). It deliberately carries no ``estleg:total*`` count
    # predicate — there is no defined vocabulary term for "provision-version count"
    # and the per-run totals live in the coverage report instead.
    map_node = {
        "@id": f"estleg:ProvisionVersions_{target.slug}_Map",
        "@type": ["owl:Ontology"],
        "rdfs:label": {
            "@value": f"Sätete redaktsioonid: {target.title} ({len(version_nodes)} versiooni)",
            "@language": "et",
        },
        "dc:source": target.title,
    }
    # Issue #271: append LegalProvision back-link stubs (estleg:currentVersion /
    # estleg:hasVersion) so the head version of every chain is forward-reachable.
    # Every referenced version IRI is one of ``version_nodes`` above, so the
    # ``currentVersion`` / ``hasVersion`` objects resolve within this sidecar and
    # do not dangle.
    backlink_nodes = build_provision_backlinks(version_nodes)
    doc = {"@context": dict(CONTEXT), "@graph": [map_node, *version_nodes, *backlink_nodes]}
    out_path = out_dir / f"{target.slug}.jsonld"
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    tmp.replace(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Per-law processing
# ---------------------------------------------------------------------------


@dataclass
class LawResult:
    slug: str
    title: str
    redactions_total: int
    redactions_fetched: int
    redactions_failed: int
    provisions_in_law: int
    provisions_versioned: int
    versions_emitted: int
    sidecar_path: str | None
    max_redactions: int = 0
    current_redaction_id: str | None = None
    current_redaction_valid_from: str | None = None
    error: str | None = None


def process_law(
    target: LawTarget,
    *,
    today: str,
    max_redactions: int,
    out_dir: Path = VERSIONS_DIR,
    fetch_sleep: float = FETCH_SLEEP_SECONDS,
) -> LawResult:
    """Fetch + diff a law's redaction history and write its ProvisionVersion sidecar."""
    print(f"\n=== {target.slug} ({target.title}) — prefix {target.prefix}, "
          f"{len(target.provisions)} provisions ===")
    try:
        chain = fetch_redaction_chain(target.title, today=today)
    except Exception as exc:  # noqa: BLE001
        print(f"  redaction-list fetch failed: {exc}")
        return LawResult(
            slug=target.slug, title=target.title,
            redactions_total=0, redactions_fetched=0, redactions_failed=0,
            provisions_in_law=len(target.provisions), provisions_versioned=0,
            versions_emitted=0, sidecar_path=None, max_redactions=max_redactions,
            error=f"redaction-list: {exc}",
        )
    redactions_total = len(chain)
    if not chain:
        print("  no redactions returned by Riigi Teataja — skipping")
        return LawResult(
            slug=target.slug, title=target.title,
            redactions_total=0, redactions_fetched=0, redactions_failed=0,
            provisions_in_law=len(target.provisions), provisions_versioned=0,
            versions_emitted=0, sidecar_path=None, max_redactions=max_redactions,
            error="no redactions",
        )
    current_redaction = chain[-1]
    if max_redactions and max_redactions > 0 and len(chain) > max_redactions:
        chain = chain[-max_redactions:]
        print(f"  capping to {len(chain)} most-recent redactions of {redactions_total} "
              f"(--max-redactions {max_redactions})")
    else:
        print(f"  {len(chain)} redactions: "
              f"{chain[0].valid_from} .. {chain[-1].valid_from or 'current'}")

    redaction_texts: list[tuple[Redaction, dict[str, str]]] = []
    failed = 0
    for i, redaction in enumerate(chain, 1):
        root = fetch_redaction_xml(target.slug, redaction, sleep=fetch_sleep)
        if root is None:
            failed += 1
            print(f"  [{i}/{len(chain)}] g{redaction.global_id} ({redaction.valid_from}): FETCH FAILED")
            continue
        texts = extract_provision_texts(root)
        redaction_texts.append((redaction, texts))
        if i % 10 == 0 or i == len(chain):
            print(f"  [{i}/{len(chain)}] g{redaction.global_id} ({redaction.valid_from}): "
                  f"{len(texts)} provisions")

    if not redaction_texts:
        print("  no redaction XML could be fetched — skipping")
        return LawResult(
            slug=target.slug, title=target.title,
            redactions_total=redactions_total, redactions_fetched=0, redactions_failed=failed,
            provisions_in_law=len(target.provisions), provisions_versioned=0,
            versions_emitted=0, sidecar_path=None, max_redactions=max_redactions,
            current_redaction_id=current_redaction.global_id,
            current_redaction_valid_from=current_redaction.valid_from,
            error="all fetches failed",
        )

    version_nodes = synthesise_versions(target, redaction_texts)
    versioned_provisions = len({
        n["estleg:versionOf"]["@id"] for n in version_nodes if isinstance(n.get("estleg:versionOf"), dict)
    })
    if not version_nodes:
        print("  no ProvisionVersion nodes produced (no provision text changes detected "
              "and no provisions resolvable) — skipping sidecar")
        return LawResult(
            slug=target.slug, title=target.title,
            redactions_total=redactions_total, redactions_fetched=len(redaction_texts),
            redactions_failed=failed, provisions_in_law=len(target.provisions),
            provisions_versioned=0, versions_emitted=0, sidecar_path=None,
            max_redactions=max_redactions,
            current_redaction_id=current_redaction.global_id,
            current_redaction_valid_from=current_redaction.valid_from,
        )
    out_path = write_sidecar(target, version_nodes, out_dir=out_dir)
    print(f"  wrote {len(version_nodes)} ProvisionVersion nodes across "
          f"{versioned_provisions} provisions -> {_rel(out_path)}")
    return LawResult(
        slug=target.slug, title=target.title,
        redactions_total=redactions_total, redactions_fetched=len(redaction_texts),
        redactions_failed=failed, provisions_in_law=len(target.provisions),
        provisions_versioned=versioned_provisions, versions_emitted=len(version_nodes),
        sidecar_path=_rel(out_path), max_redactions=max_redactions,
        current_redaction_id=current_redaction.global_id,
        current_redaction_valid_from=current_redaction.valid_from,
    )


# ---------------------------------------------------------------------------
# Law selection
# ---------------------------------------------------------------------------


def _all_law_slugs(krr_dir: Path = KRR_DIR) -> list[str]:
    """Every law slug with a peep on disk (multi-part osa files collapsed to the base slug)."""
    slugs: set[str] = set()
    for path in sorted(krr_dir.glob("*_peep.json")):
        stem = path.stem[: -len("_peep")] if path.stem.endswith("_peep") else path.stem
        base = re.sub(r"_osa\d+$", "", stem)
        slugs.add(base)
    return sorted(slugs)


def select_law_slugs(
    *,
    explicit: list[str] | None,
    limit: int | None,
    all_laws: bool = False,
    krr_dir: Path = KRR_DIR,
) -> list[str]:
    """Resolve the list of law slugs to process.

    * ``--law`` (repeatable) wins — those exact slugs (that exist on disk), in order.
    * ``--all`` processes every on-disk law slug.
    * otherwise the curated :data:`DEFAULT_LAW_SLUGS`, then any remaining on-disk slugs,
      truncated to ``limit`` (``--limit``; ``0`` ⇒ none, ``None`` ⇒ default).
    """
    on_disk = set(_all_law_slugs(krr_dir))
    if explicit:
        return [s for s in explicit if s in on_disk or _peep_files_for_slug(s, krr_dir)]
    if all_laws:
        return sorted(on_disk)
    if limit is not None and limit <= 0:
        return []
    effective_limit = DEFAULT_LIMIT if limit is None else limit
    ordered: list[str] = []
    seen: set[str] = set()
    for s in DEFAULT_LAW_SLUGS:
        if s in on_disk and s not in seen:
            ordered.append(s)
            seen.add(s)
    for s in sorted(on_disk):
        if s not in seen:
            ordered.append(s)
            seen.add(s)
    return ordered[:effective_limit]


def _parse_nonnegative_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _max_redactions_covers(prior: int | None, requested: int) -> bool:
    """Return True when a prior redaction cap is at least as complete as this run."""
    if prior is None:
        return False
    if prior == 0:
        return True
    if requested == 0:
        return False
    return prior >= requested


def _load_law_report(path: Path) -> dict | None:
    """Load a compatible per-law report, or return None if it cannot be used."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: could not parse prior ProvisionVersion report {path}: {exc}",
            file=sys.stderr,
        )
        return None

    if not isinstance(report, dict):
        print(
            f"WARNING: ignoring prior ProvisionVersion report {path}: "
            f"expected object, got {type(report).__name__}",
            file=sys.stderr,
        )
        return None
    if report.get("schemaVersion") != LAW_REPORT_SCHEMA_VERSION:
        print(
            f"WARNING: ignoring prior ProvisionVersion report {path}: "
            f"unsupported schemaVersion {report.get('schemaVersion')!r}",
            file=sys.stderr,
        )
        return None
    return report


def _completed_report_slugs(
    report: dict | None,
    *,
    max_redactions: int,
    current_redactions: dict[str, Redaction] | None = None,
) -> list[str]:
    """Return slugs safely resumable from a loaded per-law report."""
    if report is None:
        return []

    rows = report.get("rows")
    if not isinstance(rows, list):
        return []
    completed: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = row.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        try:
            versions_emitted = int(row.get("versions_emitted") or 0)
        except (TypeError, ValueError):
            versions_emitted = 0
        if versions_emitted <= 0:
            continue
        if not _max_redactions_covers(
            _parse_nonnegative_int(row.get("max_redactions")), max_redactions
        ):
            continue
        error = row.get("error")
        warnings = row.get("warnings")
        if error not in (None, ""):
            continue
        if warnings:
            continue
        if current_redactions is not None:
            current = current_redactions.get(slug)
            if current is None:
                continue
            if row.get("current_redaction_id") != current.global_id:
                continue
            if row.get("current_redaction_valid_from") != current.valid_from:
                continue
        completed.append(slug)
    return completed


def _fetch_current_redactions(
    slugs: list[str],
    *,
    krr_dir: Path,
    today: str,
    fetch_sleep: float = 0.0,
) -> dict[str, Redaction]:
    """Fetch current redaction heads for resume freshness checks."""
    current: dict[str, Redaction] = {}
    for index, slug in enumerate(slugs):
        try:
            target = build_law_target(slug, krr_dir=krr_dir)
            if target is None:
                continue
            try:
                head = fetch_current_redaction(target.title, today=today)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"WARNING: could not verify current redaction for {slug}; "
                    f"will reprocess: {exc}",
                    file=sys.stderr,
                )
                continue
            if head is None:
                print(
                    f"WARNING: no current redaction found for {slug}; will reprocess",
                    file=sys.stderr,
                )
                continue
            current[slug] = head
        finally:
            if fetch_sleep > 0 and index < len(slugs) - 1:
                time.sleep(fetch_sleep)
    return current


def resolve_resume_skips(
    *,
    candidates: list[str],
    law_report_path: Path,
    max_redactions: int,
    krr_dir: Path,
    today: str,
    fetch_sleep: float,
) -> set[str]:
    """Return candidate slugs that can be skipped under resumable --all mode."""
    report = _load_law_report(law_report_path)
    candidate_set = set(candidates)
    resume_candidates = [
        slug
        for slug in _completed_report_slugs(report, max_redactions=max_redactions)
        if slug in candidate_set
    ]
    if not resume_candidates:
        return set()

    current_redactions = _fetch_current_redactions(
        resume_candidates,
        krr_dir=krr_dir,
        today=today,
        fetch_sleep=fetch_sleep,
    )
    return {
        slug
        for slug in _completed_report_slugs(
            report,
            max_redactions=max_redactions,
            current_redactions=current_redactions,
        )
        if slug in candidate_set
    }


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def _write_coverage(results: list[LawResult], *, start_perf: float, path: Path = COVERAGE_PATH) -> None:
    all_input_files = list(iter_peep_files())
    kov_files = [p for p in all_input_files if "regulations/kov/" in str(p)]
    failures = [f"{r.slug}: {r.error}" for r in results if r.error]
    wall, rate, peak_mb = measure_runtime(start_perf, len(results))
    report = CoverageReport(
        pipeline="extract_provision_versions",
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        pipeline_version=resolve_pipeline_version(),
        input_files_total=len(all_input_files),
        input_files_kov=len(kov_files),
        files_processed=len(results),
        files_processed_kov=0,  # laws-only pipeline; KOV regulations have no redaction history yet
        files_with_output=sum(1 for r in results if r.versions_emitted > 0),
        files_with_output_kov=0,
        files_skipped=sum(1 for r in results if r.error or r.versions_emitted == 0),
        skip_reasons={},
        triples_emitted=sum(r.versions_emitted for r in results),  # counts ProvisionVersion nodes
        triples_emitted_kov=0,
        unresolved_references=0,
        wall_time_seconds=round(wall, 2),
        items_per_second=round(rate, 2),
        peak_memory_mb=round(peak_mb, 1),
        error_count=len(failures),
        failure_samples=failures,
    )
    write_coverage_report(report, path)
    print(f"\nCoverage report -> {_rel(path)}")


def write_law_report(results: list[LawResult], path: Path) -> None:
    """Persist per-law progress rows for resumable/full-run review."""
    if not results and path.exists():
        print(f"Per-law report unchanged (no results) -> {_rel(path)}")
        return
    rows = [
        {
            "slug": r.slug,
            "title": r.title,
            "redactions_total": r.redactions_total,
            "redactions_processed": r.redactions_fetched,
            "redactions_failed": r.redactions_failed,
            "max_redactions": r.max_redactions,
            "current_redaction_id": r.current_redaction_id,
            "current_redaction_valid_from": r.current_redaction_valid_from,
            "provisions_in_law": r.provisions_in_law,
            "provisions_versioned": r.provisions_versioned,
            "versions_emitted": r.versions_emitted,
            "sidecar_path": r.sidecar_path,
            "error": r.error,
            "warnings": [r.error] if r.error else [],
        }
        for r in results
    ]
    payload = {
        "schemaVersion": LAW_REPORT_SCHEMA_VERSION,
        "laws_processed": len(results),
        "laws_with_output": sum(1 for r in results if r.versions_emitted > 0),
        "versions_emitted": sum(r.versions_emitted for r in results),
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)
    print(f"Per-law report -> {_rel(path)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=f"Process at most N laws from the curated default selection (default {DEFAULT_LIMIT}). "
             "0 = none. Ignored when --law is given.",
    )
    parser.add_argument(
        "--law",
        action="append",
        metavar="SLUG",
        help="Process this specific law slug (repeatable). Overrides --limit.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process every law slug with an on-disk peep file. This is the full ingestion mode.",
    )
    parser.add_argument(
        "--max-redactions",
        type=int,
        default=None,
        metavar="N",
        help=f"Cap each law to its N most-recent redactions (default {DEFAULT_MAX_REDACTIONS}; "
             "--all defaults to unbounded; 0 = unbounded / full history).",
    )
    parser.add_argument(
        "--today",
        default=None,
        metavar="YYYY-MM-DD",
        help="Treat this date as 'now' when picking the current redaction (default: today).",
    )
    parser.add_argument(
        "--no-sleep",
        action="store_true",
        help="Disable the polite inter-fetch delay (use only against a warm cache).",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Continue past laws whose redaction list or XML cannot be fetched "
             "(failures are recorded in the coverage report). On by default for sample runs; "
             "the flag is accepted for parity with the other generators.",
    )
    parser.add_argument(
        "--force",
        "--no-resume",
        action="store_false",
        dest="resume",
        default=True,
        help=(
            "Ignore an existing per-law report and process selected laws from "
            "scratch. By default --all resumes only rows with a compatible "
            "report schema, redaction cap, and unchanged current redaction."
        ),
    )
    args = parser.parse_args(argv)
    if args.max_redactions is not None and args.max_redactions < 0:
        parser.error("--max-redactions must be non-negative (0 = unbounded)")
    return args


def effective_max_redactions(args: argparse.Namespace) -> int:
    """Return the redaction cap implied by CLI flags."""
    if args.max_redactions is not None:
        return args.max_redactions
    return 0 if args.all else DEFAULT_MAX_REDACTIONS


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    start_perf = time.perf_counter()
    today = args.today or date.today().isoformat()
    fetch_sleep = 0.0 if args.no_sleep else FETCH_SLEEP_SECONDS
    max_redactions = effective_max_redactions(args)
    # Resolve the module-level path globals at call time so a test (or a caller)
    # that monkeypatches them is honoured everywhere downstream.
    krr_dir = KRR_DIR
    versions_dir = VERSIONS_DIR
    coverage_path = COVERAGE_PATH
    law_report_path = krr_dir / "reports" / LAW_REPORT_NAME
    candidate_slugs = select_law_slugs(
        explicit=args.law,
        limit=args.limit,
        all_laws=args.all,
        krr_dir=krr_dir,
    )
    skip_completed: set[str] = set()
    if args.all and args.resume:
        skip_completed = resolve_resume_skips(
            candidates=candidate_slugs,
            law_report_path=law_report_path,
            max_redactions=max_redactions,
            krr_dir=krr_dir,
            today=today,
            fetch_sleep=fetch_sleep,
        )
        if skip_completed:
            print(
                f"Resuming from {_rel(law_report_path)}; "
                f"skipping {len(skip_completed)} completed law(s) "
                "with unchanged current redactions."
            )
        slugs = [slug for slug in candidate_slugs if slug not in skip_completed]
    else:
        slugs = candidate_slugs
    if not slugs:
        if candidate_slugs and skip_completed:
            print(
                f"All {len(candidate_slugs)} selected law(s) are already complete "
                "with compatible resume state. Nothing to do."
            )
        else:
            print("No laws selected (--limit 0 or --law matched nothing). Nothing to do.")
        # Still write a (zeroed) coverage report so the artefact exists.
        _write_coverage([], start_perf=start_perf, path=coverage_path)
        write_law_report([], law_report_path)
        return 0

    print("=" * 70)
    print(f"Provision version ingestion — {len(slugs)} law(s), "
          f"max-redactions={max_redactions or 'unbounded'}, today={today}")
    print("Selected:", ", ".join(slugs))
    print("=" * 70)

    results: list[LawResult] = []
    for slug in slugs:
        target = build_law_target(slug, krr_dir=krr_dir)
        if target is None:
            print(f"\n=== {slug}: no peep / no provisions found — skipping ===")
            results.append(LawResult(
                slug=slug, title=slug, redactions_total=0, redactions_fetched=0,
                redactions_failed=0, provisions_in_law=0, provisions_versioned=0,
                versions_emitted=0, sidecar_path=None,
                max_redactions=max_redactions, error="no peep",
            ))
            continue
        result = process_law(
            target, today=today, max_redactions=max_redactions,
            out_dir=versions_dir, fetch_sleep=fetch_sleep,
        )
        results.append(result)

    _write_coverage(results, start_perf=start_perf, path=coverage_path)
    write_law_report(results, law_report_path)

    # Summary
    total_versions = sum(r.versions_emitted for r in results)
    total_provisions = sum(r.provisions_versioned for r in results)
    total_redactions = sum(r.redactions_fetched for r in results)
    laws_with_output = sum(1 for r in results if r.versions_emitted > 0)
    print("\n" + "=" * 70)
    print(f"Done. {total_versions} estleg:ProvisionVersion nodes across "
          f"{total_provisions} provisions, from {total_redactions} fetched redactions, "
          f"in {laws_with_output}/{len(results)} laws.")
    for r in results:
        flag = f"  ({r.error})" if r.error else ""
        print(f"  {r.slug:45s} versions={r.versions_emitted:5d} provisions={r.provisions_versioned:4d} "
              f"redactions={r.redactions_fetched}/{r.redactions_total}{flag}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
