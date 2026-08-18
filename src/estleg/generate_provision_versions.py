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
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from estleg.estleg_common import (
    CONTEXT,
    KRR_DIR,
    is_domain_individual,
    iter_peep_files,
    jsonld_text,
    parse_xml,
    parse_xml_file,
    save_json,
)
from estleg.generate_all_laws import _paragraph_id_suffix, collect_full_text
from estleg.kov_pipeline_coverage import (
    PINNED_RUN_TIMESTAMP,
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)
from estleg.riigiteataja_common import DATA_DIR, ln

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

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

# U+FFFD — emitted when a byte cannot be decoded. Pre-2010 RT XML is often
# windows-1257 / iso-8859-13; forcing UTF-8 replaces each high byte.
FFFD = "\ufffd"
_RT_FALLBACK_ENCODINGS = ("windows-1257", "iso-8859-13", "latin-1")
_XML_ENCODING_DECL_RE = re.compile(br"""encoding\s*=\s*['"]([A-Za-z0-9._-]+)['"]""", re.IGNORECASE)
_XML_ENCODING_DECL_TEXT_RE = re.compile(r"""encoding\s*=\s*['"][^'"]+['"]""", re.IGNORECASE)
# UTF-8 Estonian / § / en-dash misread as latin-1 or windows-1257.
_UTF8_MOJIBAKE = (
    "Ãµ",
    "Ã¤",
    "Ã¶",
    "Ã¼",
    "Ã•",
    "Ã„",
    "Ã–",
    "Ãœ",
    "Å¡",
    "Å¾",
    "Å ",
    "Â§",
    "â€“",
    "â€”",
)

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
    act_iri: str | None = None  # owl:Ontology / act-root @id, for citation completeness (#524)


# ---------------------------------------------------------------------------
# Peep introspection — discover the law's provision IRIs (the link targets)
# ---------------------------------------------------------------------------


_PROVISION_TYPE_RE = re.compile(r"^estleg:LegalProvision_")
_PAR_IRI_RE = re.compile(r"_Par_(?P<suffix>[0-9]+(?:_[0-9]+)?)$")
# versionOf IRI → human citation: prefix, optional _OsaN, _Par_N, optional _LgN.
_PROVISION_REF_RE = re.compile(
    r"^(?P<head>.+)_Par_(?P<par>\d+(?:_\d+)?(?:_to_\d+(?:_\d+)?)?)"
    r"(?:_Lg_?(?P<lg>\d+))?$"
)
_RT_AKT_URL = "https://www.riigiteataja.ee/akt/"
_ESTLEG_PREFIX = "estleg:"
_ESTLEG_NS = "https://w3id.org/estleg/"


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


def _load_provision_map(
    peep_files: list[Path],
) -> tuple[dict[str, str], str | None, str | None, str | None]:
    """Scan peep files; return ``(par_suffix -> provision IRI, prefix, title, act_iri)``.

    ``prefix`` is the common IRI prefix (the segment before ``_Par_``); ``title`` is
    the ``dc:source`` of the ontology node (the human-readable act name).
    ``act_iri`` is the ontology/act-root ``@id`` used to denormalise
    ``estleg:partOfAct`` onto each ProvisionVersion (#524).
    """
    provisions: dict[str, str] = {}
    prefix: str | None = None
    title: str | None = None
    act_iri: str | None = None
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
            if is_domain_individual(node) and title is None:
                act_iri = iri
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
    return provisions, prefix, title, act_iri


def build_law_target(slug: str, krr_dir: Path = KRR_DIR) -> LawTarget | None:
    """Build a :class:`LawTarget` for ``slug`` from its peep file(s) on disk."""
    peep_files = _peep_files_for_slug(slug, krr_dir)
    if not peep_files:
        return None
    provisions, prefix, title, act_iri = _load_provision_map(peep_files)
    if not provisions or not prefix or not title:
        return None
    return LawTarget(
        slug=slug,
        title=title,
        prefix=prefix,
        provisions=provisions,
        peep_files=peep_files,
        act_iri=act_iri,
    )


def missing_law_target_error(slug: str, krr_dir: Path = KRR_DIR) -> str:
    """Classify why :func:`build_law_target` returned ``None`` (#430).

    ``"no peep"`` is reserved for a genuinely missing peep file. A peep that
    exists but has no eligible ``_Par_<digits>`` provision IRIs (treaty shells,
    article-numbered acts) is ``"no_numeric_provisions"``. Any other unusable
    peep (numeric provisions present but missing prefix/title) is
    ``"ineligible_peep"``.
    """
    peep_files = _peep_files_for_slug(slug, krr_dir)
    if not peep_files:
        return "no peep"
    provisions, _prefix, _title, _act_iri = _load_provision_map(peep_files)
    if not provisions:
        return "no_numeric_provisions"
    return "ineligible_peep"


def reclassify_report_missing_peep_rows(
    report: dict, *, krr_dir: Path = KRR_DIR
) -> dict:
    """Rewrite stale ``error: "no peep"`` rows (#430).

    The June 2026 report labelled every ``build_law_target is None`` skip
    as ``no peep``, including treaty shells whose peep files exist. Re-run
    :func:`missing_law_target_error` on those rows so the committed report
    matches the generator.
    """
    for row in report.get("rows") or []:
        if not isinstance(row, dict) or row.get("error") != "no peep":
            continue
        slug = row.get("slug") or ""
        new_error = missing_law_target_error(slug, krr_dir=krr_dir)
        if new_error == "no peep":
            continue
        row["error"] = new_error
        row["warnings"] = [new_error]
    return report


# ---------------------------------------------------------------------------
# #524 — citation-complete ProvisionVersion nodes
# ---------------------------------------------------------------------------


def _node_type_list(node: dict) -> list[str]:
    types = node.get("@type") or []
    if isinstance(types, str):
        return [types]
    if isinstance(types, list):
        return [t for t in types if isinstance(t, str)]
    return []


def _citation_text(value: object) -> str:
    """Plain string from a JSON-LD literal (string, @value object, or int id)."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    return jsonld_text(value).strip()


def _local_estleg_name(iri: str) -> str:
    if iri.startswith(_ESTLEG_PREFIX):
        return iri[len(_ESTLEG_PREFIX) :]
    if iri.startswith(_ESTLEG_NS):
        return iri[len(_ESTLEG_NS) :]
    return iri


def rt_url_from_redaction_id(rid: str) -> str:
    """Return the Riigi Teataja HTML akt URL for a redaction / terviktekst id."""
    return f"{_RT_AKT_URL}{str(rid).strip()}"


def provision_ref_from_iri(iri: str) -> str:
    """Human citation from a provision IRI.

    ``estleg:PKS_Par_1`` → ``PKS § 1``;
    ``estleg:KARIST_2_Osa2_Par_88_Lg_1`` → ``KARIST_2 § 88 lg 1``.
    Multipart ``_OsaN`` is dropped from the displayed abbreviation.
    """
    local = _local_estleg_name(iri.strip())
    match = _PROVISION_REF_RE.match(local)
    if not match:
        return ""
    head = re.sub(r"_Osa_?\d+$", "", match.group("head"))
    if not head:
        return ""
    par = match.group("par").replace("_to_", "–")
    ref = f"{head} § {par}"
    lg = match.group("lg")
    if lg:
        ref = f"{ref} lg {lg}"
    return ref


def stamp_version_citation(
    node: dict,
    *,
    act_title: str,
    act_iri: str | None,
) -> bool:
    """Idempotently stamp #524 citation fields onto a ProvisionVersion node.

    Sets ``estleg:rtUrl`` from ``versionRedactionId``, ``estleg:sourceAct``
    from *act_title*, ``estleg:provisionRef`` from ``versionOf`` ``@id``, and
    ``estleg:partOfAct`` from *act_iri* when known. Returns True iff the node
    changed.
    """
    if not isinstance(node, dict):
        return False
    if "estleg:ProvisionVersion" not in _node_type_list(node):
        return False
    changed = False
    rid = _citation_text(node.get("estleg:versionRedactionId"))
    if rid:
        url = rt_url_from_redaction_id(rid)
        if node.get("estleg:rtUrl") != url:
            node["estleg:rtUrl"] = url
            changed = True
    title = (act_title or "").strip()
    if title and node.get("estleg:sourceAct") != title:
        node["estleg:sourceAct"] = title
        changed = True
    version_of = node.get("estleg:versionOf")
    if isinstance(version_of, dict):
        provision_iri = version_of.get("@id")
    elif isinstance(version_of, str):
        provision_iri = version_of
    else:
        provision_iri = None
    if isinstance(provision_iri, str) and provision_iri:
        pref = provision_ref_from_iri(provision_iri)
        if pref and node.get("estleg:provisionRef") != pref:
            node["estleg:provisionRef"] = pref
            changed = True
    if act_iri:
        expected = {"@id": act_iri}
        if node.get("estleg:partOfAct") != expected:
            node["estleg:partOfAct"] = expected
            changed = True
    return changed


def _sidecar_act_title(doc: dict) -> str:
    """Act title from the sidecar ontology node (``dc:source``, else label)."""
    for node in doc.get("@graph") or []:
        if not isinstance(node, dict):
            continue
        if not is_domain_individual(node) and "owl:Ontology" not in _node_type_list(node):
            continue
        src = _citation_text(node.get("dc:source"))
        if src:
            return src
        label = _citation_text(node.get("rdfs:label"))
        if label:
            return label
    return ""


def _act_iri_from_peep_path(path: Path) -> str | None:
    """Return the first ``estleg:Act`` ``@id`` in *path*, or None."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    for node in doc.get("@graph") or []:
        if not isinstance(node, dict):
            continue
        if "estleg:Act" not in _node_type_list(node):
            continue
        iri = node.get("@id")
        if isinstance(iri, str) and iri:
            return iri
    return None


def _index_files_by_slug(krr_dir: Path) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    index_path = krr_dir / "INDEX.json"
    try:
        doc = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return mapping
    if not isinstance(doc, dict):
        return mapping
    for law in doc.get("laws") or []:
        if not isinstance(law, dict):
            continue
        name = law.get("name")
        files = law.get("files")
        if isinstance(name, str) and name and isinstance(files, list):
            mapping[name] = [f for f in files if isinstance(f, str) and f]
    return mapping


def _resolve_act_iri(
    slug: str, krr_dir: Path, index_files: dict[str, list[str]]
) -> str | None:
    """Act ``@id`` from ``<slug>_peep.json``, else the first INDEX peep file."""
    single = krr_dir / f"{slug}_peep.json"
    if single.is_file():
        iri = _act_iri_from_peep_path(single)
        if iri:
            return iri
    files = index_files.get(slug) or []
    if files:
        first = krr_dir / files[0]
        if first.is_file():
            return _act_iri_from_peep_path(first)
    return None


def backfill_version_sidecars(pv_dir: Path, krr_dir: Path) -> dict[str, int]:
    """Stamp #524 citation fields onto committed ``provision_versions/*.jsonld``.

    Resolves ``act_title`` from each sidecar's ontology ``dc:source`` (else
    ``rdfs:label``) and ``act_iri`` from ``<slug>_peep.json`` or the first
    INDEX file for multipart laws. Rewrites via :func:`save_json`.
    """
    stats = {
        "files": 0,
        "files_scanned": 0,
        "nodes": 0,
        "nodes_seen": 0,
        "missing_act_iri": 0,
    }
    if not pv_dir.is_dir():
        return stats
    index_files = _index_files_by_slug(krr_dir)
    act_iri_cache: dict[str, str | None] = {}
    for path in sorted(pv_dir.glob("*.jsonld")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        stats["files_scanned"] += 1
        act_title = _sidecar_act_title(doc)
        slug = path.stem
        if slug not in act_iri_cache:
            act_iri_cache[slug] = _resolve_act_iri(slug, krr_dir, index_files)
        act_iri = act_iri_cache[slug]
        if act_iri is None:
            stats["missing_act_iri"] += 1
        graph = doc.get("@graph")
        if not isinstance(graph, list):
            continue
        file_changed = False
        for node in graph:
            if not isinstance(node, dict):
                continue
            if "estleg:ProvisionVersion" not in _node_type_list(node):
                continue
            stats["nodes_seen"] += 1
            if stamp_version_citation(node, act_title=act_title, act_iri=act_iri):
                stats["nodes"] += 1
                file_changed = True
        if file_changed:
            save_json(path, doc)
            stats["files"] += 1
    return stats


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
    # yields several, take the one with the latest entry-into-force. Tie-break on
    # ``globaalID`` (as a string) so the winner — and thus the version ``@id``
    # derived from it — is deterministic regardless of the API's row order when two
    # matches share an ``algus``.
    best = max(
        matches,
        key=lambda a: (
            (a.get("kehtivus") or {}).get("algus") or "",
            str(a.get("globaalID") or ""),
        ),
    )
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
    # ended on/before today (so it is genuinely historical). Dedupe by entry-
    # into-force date keeping the most-recently-touched metadata record.
    by_from: dict[str, dict] = {}
    for a in matches:
        keh = a.get("kehtivus") or {}
        algus = _strip_offset(keh.get("algus"))
        lopp = _strip_offset(keh.get("lopp"))
        if not algus or not lopp:
            continue
        # Keep iff the edition started on/before today (algus <= today) AND it had
        # ended on/before today (lopp <= today). Issue #328: an edition ending
        # exactly on the run date (``lopp == today``, i.e. the day a new redaction
        # enters force) was previously dropped by a strict ``lopp < today`` test —
        # it appeared in neither the superseded list nor ``fetch_current_redaction``
        # (which returns the new edition), silently losing that edition's history.
        # Using an inclusive ``lopp <= today`` retains it; the ``global_id`` dedup
        # against the current edition below absorbs any coincidental overlap.
        if not (
            _date_before(algus, today_date, inclusive=True)
            and _date_before(lopp, today_date, inclusive=True)
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

    # Monotonicity guard: the superseded editions above are already sorted ascending
    # by ``valid_from`` and ``current`` is normally the newest, so appending it last
    # keeps the chain ordered. If a data anomaly gives ``current`` an earlier
    # entry-into-force date than the last superseded edition, this stable sort
    # repositions it so the chain stays non-decreasing by ``valid_from`` — a
    # non-monotonic chain would otherwise yield inverted ``versionValidTo`` /
    # backwards ``supersededByVersion`` downstream. Superseded editions always carry
    # a non-None ``valid_from``; ``or ""`` only guards a (defensive) None ``current``.
    chain.sort(key=lambda r: r.valid_from or "")

    return chain


# ---------------------------------------------------------------------------
# Riigi Teataja: per-redaction XML fetch + provision-text extraction
# ---------------------------------------------------------------------------


def _xml_cache_path(slug: str, global_id: str) -> Path:
    cache_dir = DATA_DIR / XML_CACHE_SUBDIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z_]", "", global_id) or "unknown"
    return cache_dir / f"{slug}__g{safe}.xml"


# ---------------------------------------------------------------------------
# Encoding (#355) — decode RT XML without forcing UTF-8; repair committed FFFD
# ---------------------------------------------------------------------------


def _xml_declared_encoding(data: bytes) -> str | None:
    """Return the ``encoding=`` value from a leading XML declaration, if any."""
    match = _XML_ENCODING_DECL_RE.search(data[:200])
    return match.group(1).decode("ascii", errors="replace") if match else None


def _force_utf8_xml_declaration(text: str) -> str:
    """Rewrite a leading XML encoding declaration to UTF-8.

    After we have decoded the payload to ``str``, :func:`parse_xml` re-encodes
    as UTF-8. Leaving ``encoding="windows-1257"`` in the declaration would make
    ElementTree re-decode those UTF-8 bytes as Baltic and recreate mojibake.
    """
    stripped = text.lstrip()
    if not stripped.startswith("<?xml"):
        return text
    return _XML_ENCODING_DECL_TEXT_RE.sub('encoding="UTF-8"', text, count=1)


def _decode_score(text: str) -> int:
    """Lower is better. Penalise U+FFFD, ``ï¿½``, and UTF-8-as-latin-1 mojibake."""
    score = text.count(FFFD) * 100
    # U+FFFD's UTF-8 bytes (EF BF BD) read as a single-byte encoding.
    score += text.count("ï¿½") * 200
    score += text.count("ļæ½") * 200  # EF BF BD read as windows-1257
    for pat in _UTF8_MOJIBAKE:
        score += text.count(pat) * 20
    # š/ž in windows-1257 become Icelandic ð/þ when forced through latin-1.
    for ch in "ðþÐÞ":
        score += text.count(ch) * 5
    return score


def decode_rt_xml_bytes(
    data: bytes,
    *,
    declared_encoding: str | None = None,
    apparent_encoding: str | None = None,
) -> str:
    """Decode Riigi Teataja XML bytes without forcing UTF-8 (#355).

    Preference order: HTTP Content-Type charset (``declared_encoding``),
    requests' ``apparent_encoding``, the XML declaration, then UTF-8. If the
    preferred decode still contains U+FFFD (or loses to a cleaner fallback),
    try ``windows-1257``, ``iso-8859-13``, and ``latin-1`` and keep the
    candidate with the fewest replacement characters / mojibake markers.

    Tests call this helper on raw Baltic bytes — do not wrap it in a mock.
    """
    # Already-corrupted UTF-8 (U+FFFD stored as EF BF BD). A single-byte
    # encoding would turn that into ï¿½ / ļæ½ and "win" the FFFD count;
    # keep UTF-8 so strip_or_repair_fffd_in_version_text can see the pairs.
    if b"\xef\xbf\xbd" in data:
        return data.decode("utf-8", errors="replace")

    encodings: list[str] = []
    seen: set[str] = set()
    for enc in (
        declared_encoding,
        apparent_encoding,
        _xml_declared_encoding(data),
        "utf-8",
        *_RT_FALLBACK_ENCODINGS,
    ):
        if not enc:
            continue
        key = enc.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        encodings.append(enc)

    best: tuple[int, int, str] | None = None
    for rank, enc in enumerate(encodings):
        try:
            text = data.decode(enc, errors="replace")
        except LookupError:
            continue
        item = (_decode_score(text), rank, text)
        if best is None or item < best:
            best = item
    if best is None:
        return data.decode("utf-8", errors="replace")
    return best[2]


# Pair of U+FFFD = one 2-byte UTF-8 Estonian letter (õ/ä/ö/ü/š/ž) or §.
# A lone U+FFFD is almost always š/Š. A triple between tokens is an en-dash
# (U+2013 = E2 80 93). Recover those; leftover U+FFFD is dropped so committed
# files can reach zero (a hole is worse than a recovered letter, but better
# than leaving the replacement character for full-text search).
_FFFD_RUN_INSERTIONS: dict[int, tuple[str, ...]] = {
    1: tuple("šžõäöüŠŽÕÄÖÜ"),
    2: tuple("õäöüšžÕÄÖÜŠŽ§"),
    3: ("–", "—") + tuple("õäöüšžÕÄÖÜ"),
    4: tuple(a + b for a in "õäöü" for b in "õäöü"),
}

# Exact recovered forms from the committed #355 sidecar residue (lowercase).
# Used to pick among substitutions; stems below cover future FFFD text.
_FFFD_WORD_FORMS = frozenset(
    ["aastaväärtus", "aktsionäride", "alamsüsteemi", "andmebüroole", "duši", "edasimüügi", "eelhääletamist", "eesõigus", "elektritöö", "elektritööd", "elektritööle", "emaettevõtja", "eraõigusliku", "erisöödamaterjali", "ettenähtud", "ettenäitamist", "ettevõte", "ettevõtjana", "ettevõtlusega", "ettevõtte", "füüsiliste", "haldusülesannet", "häireplaani", "häirimata", "hävimise", "hääletada", "hääletamissedeli", "hääletamistulemuste", "hääletavate", "hääli", "häälte", "hüvitamispäeval", "hüvitatakse", "hüvitise", "hüvitiste", "jäetud", "jälitustoiminguga", "jäljend", "järelevalve", "järelevalvemenetluses", "järelevalvesüsteem", "järgi", "järgmisi", "järgmiste", "järjekorra", "jätmise", "jätta", "jääkide", "jäätmetele", "jäätmevaldajate", "jõu", "jõust", "jõustumiseni", "jõustunud", "kaadrikaitseväelaste", "kaitseväes", "kaitseväeteenistuskohustuse", "kasutusõiguse", "kaubamärki", "kindlaksmääramise", "kooskõlas", "kuumäära", "käesolev", "käesoleva", "käesolevas", "käesolevast", "käesolevat", "käibemaksumäär", "käigus", "käive", "käsitleva", "kätte", "kättetoimetamise", "kõigi", "kõikide", "kümme", "küsimusi", "läbi", "läbivaatamise", "lähevad", "lähisugulane", "lähiümbrus", "lõigete", "lõigetes", "lõike", "lõikes", "lõpuni", "lõpus", "lühinumbri", "maaparandusbüroode", "maaparandustööde", "maksuvõla", "masinatööde", "miinimumpäevamäära", "mittenõustumise", "märgistamisel", "määrab", "määral", "määramise", "määramisel", "määranud", "määras", "määrata", "määratakse", "määratud", "määruse", "määrusega", "määruskaebuse", "määruste", "mõne", "möödub", "möödumisest", "möödumist", "müügil", "näidatud", "nõude", "nõuete", "nõuetega", "nõuetekohase", "nõuetele", "nõukogu", "nõusolekul", "nõustunud", "pakendijäätmete", "peatükis", "peatükk", "prokuröri", "pädeva", "pädevate", "päeva", "päeval", "päevast", "pärandvara", "pärast", "päästa", "põhistab", "põhivara", "põhjendust", "põhjusel", "põllumajandussaaduste", "pööramine", "raammääratlusele", "režiimis", "riigilõivu", "sihtmäärangud", "sätestata", "sätestatud", "sätestatut", "sõidukikaardi", "sõjaväeatašeedega", "sõlmimisega", "sõlmitakse", "sõlmitud", "sõltumata", "sööta", "sügavamal", "sündmuse", "sündmuskoha", "süsteemi", "süü", "süüdistatav", "süüdistatava", "süütamine", "tagasivõetud", "tarbijaühenduste", "telefonivõrgule", "telekommunikatsioonivõrgu", "tolliväärtuse", "turuväärtusest", "tähtaegu", "tähtpäevaks", "tähtpäevast", "täiendava", "täiendavate", "täita", "täitedokument", "täitma", "täitmise", "täitmiseks", "täpset", "tõestatud", "tõsteseadmetööde", "töö", "töökoht", "tööohutuse", "tööpäeva", "tööpäeval", "töörõhuga", "töötajaid", "töötatakse", "töötervishoiu", "töövõimetus", "töövõtu", "tööülesannete", "tühistab", "tühistada", "tüübikinnitustunnistuse", "tšeki", "vahistamismääruse", "vähemalt", "vähendatakse", "välisesinduses", "välisesindustes", "välisriigi", "välisriigis", "väljaandes", "väljaandja", "väljalaske", "väljaõppe", "väärkasutamine", "väärteo", "väärteoasi", "väärteoasja", "väärteomenetlust", "väärteomenetlusõiguse", "väärtpabereid", "väärtpaberibörsil", "väärtpaberiga", "väärtpaberikontole", "väärtpaberiturul", "väärtuse", "võetakse", "võetud", "või", "võib", "võimalda", "võimalik", "võlausaldajate", "võlgade", "võrdväärsetel", "võrguettevõtja", "võrguettevõtjal", "võtab", "võõrandaja", "võõrandatud", "§", "§-des", "§-le", "ärakirju", "äriregistris", "äriühingu", "õigsuse", "õigsust", "õigus", "õiguse", "õiguserikkumiste", "õiguskaitset", "õigusnorme", "õigust", "õiguste", "õigustloova", "õppepraktika", "ööpäevaks", "ühe", "ühendusesisest", "ühes", "ühingulepingu", "üksnes", "üksus", "üle", "ülejäänud", "ülekande-", "ülekuulamise", "ülemmäär", "ülesandeid", "ülevõtmiskomisjoni", "ürituste", "šahtisuudmele", "šveitsi"]
)

# Longest-first stems so a future FFFD word still prefers the right letter.
_FFFD_STEMS = tuple(
    sorted(
        (
            "käesolev",
            "sätesta",
            "riigilõiv",
            "lõiget",
            "lõike",
            "väärtpaber",
            "väärteomenetlus",
            "väärteo",
            "väärkasut",
            "väärt",
            "vähemalt",
            "välis",
            "välja",
            "võlausaldaja",
            "võrguettevõt",
            "võõrand",
            "võimal",
            "võrd",
            "võlg",
            "võrk",
            "võrg",
            "võet",
            "võta",
            "võib",
            "või",
            "ettenäht",
            "ettenäit",
            "ettevõt",
            "haldusülesand",
            "häälet",
            "hüvitis",
            "hüvita",
            "häire",
            "häiri",
            "hääl",
            "järelevalve",
            "järjekorr",
            "jäätme",
            "jälitus",
            "jõustum",
            "jõust",
            "kaitseväe",
            "kasutusõig",
            "kindlaksmäär",
            "kooskõlas",
            "käsitl",
            "käigus",
            "kättetoimetamise",
            "kätte",
            "kõigi",
            "kõik",
            "kümme",
            "küsim",
            "läbivaatamise",
            "lähisugulane",
            "lähiümbrus",
            "lühinumbri",
            "lõpuni",
            "määruskaebuse",
            "määr",
            "mõne",
            "möödu",
            "nõuet",
            "nõukogu",
            "nõusolek",
            "nõustu",
            "peatük",
            "prokurör",
            "pädev",
            "tähtpäev",
            "täiend",
            "täitedokument",
            "täit",
            "tühist",
            "tõesta",
            "tööülesand",
            "töövõimetus",
            "töötervishoiu",
            "pärast",
            "põhjus",
            "põhjend",
            "põhista",
            "pärand",
            "raammääratlus",
            "režiim",
            "sõiduk",
            "sõlmi",
            "sündmus",
            "süsteem",
            "süütamine",
            "süüdistatav",
            "tagasivõet",
            "tarbijaühend",
            "tolliväärt",
            "ülesand",
            "ülevõtmis",
            "ühendus",
            "ühing",
            "üksnes",
            "üksus",
            "ülekuulamise",
            "ülemmäär",
            "üritust",
            "õigustloova",
            "õiguserikkumiste",
            "õiguskaitse",
            "õigusnorme",
            "õigus",
            "õigs",
            "õppe",
            "ärakirj",
            "äriühing",
            "äriregistr",
            "šaht",
            "šveits",
            "tšek",
            "duš",
        ),
        key=len,
        reverse=True,
    )
)

_LETTER_PRIOR = {
    "õ": 6,
    "ä": 5,
    "ü": 4,
    "ö": 3,
    "š": 2,
    "ž": 1,
    "§": 7,
    "–": 8,
    "—": 7,
    "Õ": 6,
    "Ä": 5,
    "Ü": 4,
    "Ö": 3,
    "Š": 2,
    "Ž": 1,
}


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch in "-§ÕÄÖÜŠŽõäöüšž"


def _fffd_insertions(run: int) -> tuple[str, ...]:
    return _FFFD_RUN_INSERTIONS.get(run, ("",))


def _generate_fffd_candidates(token: str) -> list[str]:
    """Replace each FFFD run in ``token`` with Estonian-letter / § / dash options."""
    parts: list[tuple[str | None, int]] = []
    i = 0
    while i < len(token):
        if token[i] == FFFD:
            j = i
            while j < len(token) and token[j] == FFFD:
                j += 1
            parts.append((None, j - i))
            i = j
        else:
            j = i
            while j < len(token) and token[j] != FFFD:
                j += 1
            parts.append((token[i:j], 0))
            i = j
    cands = [""]
    for lit, run in parts:
        if lit is not None:
            cands = [c + lit for c in cands]
        else:
            inserts = _fffd_insertions(run)
            cands = [c + ins for c in cands for ins in inserts]
    return cands


def _score_fffd_candidate(cand: str) -> tuple[int, int, int]:
    core = cand.lower()
    key = core.rstrip("-")
    if core in _FFFD_WORD_FORMS or key in _FFFD_WORD_FORMS:
        return (3, len(core), sum(_LETTER_PRIOR.get(ch, 0) for ch in cand))
    best_stem = 0
    for stem in _FFFD_STEMS:
        if stem in core:
            best_stem = len(stem)
            break  # stems are longest-first
    if best_stem:
        return (2, best_stem, sum(_LETTER_PRIOR.get(ch, 0) for ch in cand))
    return (0, 0, sum(_LETTER_PRIOR.get(ch, 0) for ch in cand))


def _apply_case(token: str, replacement: str, *, at_sentence_start: bool) -> str:
    """Keep Šveitsi capitalised; otherwise prefer the source token's case."""
    if replacement.lower() == "šveitsi":
        return "Šveitsi"
    if token and token[0].isupper() and replacement:
        return replacement[0].upper() + replacement[1:]
    if at_sentence_start and replacement and replacement[0].islower():
        if replacement[0] in "õäöüšž":
            return replacement[0].upper() + replacement[1:]
    return replacement


def strip_or_repair_fffd_in_version_text(text: str) -> str:
    """Repair U+FFFD runs in a ProvisionVersion text, or drop them (#355).

    Pre-2010 Riigi Teataja XML was often windows-1257. Forcing a UTF-8 decode
    replaced each byte of a 2-byte UTF-8 Estonian letter with U+FFFD, so the
    common surviving pattern is a PAIR (``v\\ufffd\\ufffdi`` → ``või``). A lone
    U+FFFD is almost always š/Š; a triple between tokens is an en-dash
    (U+2013). We try those recoveries against a legal-Estonian word/stem list.
    Leftover U+FFFD is dropped — a hole is worse than a recovered letter, but
    better than leaving the replacement character in the committed corpus.
    """
    if FFFD not in text:
        return text

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != FFFD:
            out.append(text[i])
            i += 1
            continue
        j = i
        while j < n and text[j] == FFFD:
            j += 1
        run = j - i
        left = text[i - 1] if i else ""
        right = text[j] if j < n else ""

        # Structural recoveries that are not Estonian letters.
        if run == 3 and (not left or not _is_word_char(left)) and (
            not right or not _is_word_char(right)
        ):
            out.append("–")
            i = j
            continue
        if run == 2 and left.isdigit() and right.isdigit():
            out.append("–")
            i = j
            continue
        if run == 2 and (not left or not _is_word_char(left)) and (
            right.isdigit() or right == "-"
        ):
            out.append("§")
            i = j
            continue
        if run == 2 and (not left or not _is_word_char(left)) and (
            not right or not _is_word_char(right)
        ):
            out.append("§")
            i = j
            continue

        a, b = i, j
        while a > 0 and _is_word_char(text[a - 1]):
            a -= 1
        while b < n and _is_word_char(text[b]):
            b += 1
        token = text[a:b]
        prefix = text[a:i]
        # Already-emitted prefix of this token lives in ``out``.
        if prefix:
            del out[-len(prefix) :]
        cands = _generate_fffd_candidates(token)
        if not cands:
            i = j
            continue
        best = max(cands, key=_score_fffd_candidate)
        at_start = a == 0 or (a >= 2 and text[a - 2 : a] in (". ", ".\n")) or (
            a >= 1 and text[a - 1] in ".!?"
        )
        out.append(_apply_case(token, best, at_sentence_start=at_start))
        i = b
    return "".join(out).replace(FFFD, "")


def _repair_json_strings(value: object) -> object:
    """Recursively repair U+FFFD in JSON-LD strings (versionText / labels)."""
    if isinstance(value, str):
        return strip_or_repair_fffd_in_version_text(value) if FFFD in value else value
    if isinstance(value, dict):
        return {k: _repair_json_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_repair_json_strings(v) for v in value]
    return value


def cleanup_committed_provision_version_fffd(
    versions_dir: Path = VERSIONS_DIR,
) -> dict[str, int]:
    """Rewrite committed ``provision_versions/*.jsonld`` that still contain U+FFFD.

    Atomic via :func:`estleg_common.save_json`. Returns counts of files / nodes
    / string fields rewritten so a caller (or ``--cleanup-fffd``) can log them.
    """
    stats = {"files": 0, "nodes": 0, "fields": 0}
    if not versions_dir.is_dir():
        return stats
    for path in sorted(versions_dir.glob("*.jsonld")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if FFFD not in raw:
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, dict):
            continue
        changed_nodes = 0
        changed_fields = 0
        graph = doc.get("@graph")
        if isinstance(graph, list):
            new_graph = []
            for node in graph:
                if not isinstance(node, dict):
                    new_graph.append(node)
                    continue
                if FFFD not in json.dumps(node, ensure_ascii=False):
                    new_graph.append(node)
                    continue
                repaired = _repair_json_strings(node)
                if repaired != node:
                    changed_nodes += 1
                    for key, old in node.items():
                        if repaired.get(key) != old:  # type: ignore[union-attr]
                            changed_fields += 1
                new_graph.append(repaired)
            doc["@graph"] = new_graph
        else:
            doc = _repair_json_strings(doc)  # type: ignore[assignment]
            changed_nodes = 1
        save_json(path, doc)
        stats["files"] += 1
        stats["nodes"] += changed_nodes
        stats["fields"] += changed_fields
    return stats


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
            return parse_xml_file(cache_path)
        except (ET.ParseError, ValueError):
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
        # #355: do not force resp.encoding = "utf-8". Pre-2010 RT XML is
        # often windows-1257 / iso-8859-13; a forced UTF-8 decode replaces
        # each Baltic high byte with U+FFFD (või → v��i).
        text = decode_rt_xml_bytes(
            resp.content,
            declared_encoding=resp.encoding,
            apparent_encoding=getattr(resp, "apparent_encoding", None),
        )
        text = _force_utf8_xml_declaration(text)
        if len(text) < 200:
            return None
        cache_path.write_text(text, encoding="utf-8")
        return parse_xml(text)
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


def _day_before(iso_date: str) -> str:
    """Return the ISO ``YYYY-MM-DD`` calendar day immediately before ``iso_date``.

    Used to derive a version's *exclusive* ``versionValidTo`` from its successor's
    ``versionValidFrom`` (issue #306). Falls back to the input unchanged if it is
    not a parseable ISO date (defensive — upstream values are ``_strip_offset``
    normalised, so this should not occur).
    """
    try:
        return (date.fromisoformat(iso_date) - timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        return iso_date


def _normalise_text(text: str) -> str:
    """Collapse insignificant whitespace differences before diffing."""
    return re.sub(r"\s+", " ", text or "").strip()


def _dedup_same_valid_from(
    redaction_texts: list[tuple[Redaction, dict[str, str]]],
) -> list[tuple[Redaction, dict[str, str]]]:
    """Collapse redactions that share an entry-into-force date, keeping the last.

    Issue #393: when two Riigi Teataja redactions report the same
    ``versionValidFrom`` (entry-into-force date), chaining them produces a
    superseded version whose ``versionValidTo`` (the successor's
    ``versionValidFrom``) equals its own ``versionValidFrom`` — a zero-duration
    (``from == to``) node that an as-of-date query on that boundary returns
    *alongside* its successor. Two versions of one provision cannot both take
    effect on the same calendar day, so we keep only the **last** redaction in
    each same-date group (the most recent edition for that date, mirroring the
    ``by_from`` dedup in :func:`fetch_redaction_chain` which keeps the
    most-recently-touched record). Redactions with no usable ``valid_from`` are
    never collapsed — they cannot be emitted anyway and must not swallow a
    neighbouring valid redaction.

    The chain is assumed oldest -> newest; relative order of the surviving
    entries is preserved.
    """
    last_index_by_from: dict[str, int] = {}
    for index, (redaction, _texts) in enumerate(redaction_texts):
        valid_from = redaction.valid_from
        if not valid_from:
            continue
        last_index_by_from[valid_from] = index

    deduped: list[tuple[Redaction, dict[str, str]]] = []
    for index, entry in enumerate(redaction_texts):
        valid_from = entry[0].valid_from
        if valid_from and last_index_by_from.get(valid_from) != index:
            # A later redaction shares this entry-into-force date; drop this one.
            continue
        deduped.append(entry)
    return deduped


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
    of version *k* is set to the day *before* ``versionValidFrom`` of version *k+1*
    (an exclusive end, so an as-of-date query matches exactly one version on the
    transition day — issue #306); the last version stays open (no ``versionValidTo``
    / ``supersededByVersion``).

    Redactions sharing an entry-into-force date are collapsed first (issue #393)
    so no zero-duration (``versionValidFrom == versionValidTo``) version is emitted.
    """
    # Issue #393: collapse same-``valid_from`` redactions before diffing/chaining so
    # two editions taking effect on one calendar day cannot yield a zero-day version.
    redaction_texts = _dedup_same_valid_from(redaction_texts)
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
                continue  # unchanged vs the last *emitted* version — no new version
            if not redaction.valid_from:
                # No entry-into-force date for this redaction — we cannot give the
                # version a (required) ``versionValidFrom``. Skip WITHOUT advancing
                # ``current_norm``: a redaction we cannot emit must not become the new
                # baseline, or it would suppress a later *valid* redaction that carries
                # the same text (a malformed redaction followed by a good one with
                # identical text would otherwise emit nothing at all).
                continue
            if not redaction.global_id:
                # No redaction id ⇒ no stable, non-colliding version identity and no
                # ``versionRedactionId``. Skip WITHOUT advancing ``current_norm`` (same
                # reasoning as the ``valid_from`` guard above) so the next emittable
                # redaction carrying this text still produces a version.
                continue
            # Commit to emitting this redaction as a new version: only now does its
            # text become the baseline that subsequent redactions are diffed against.
            current_norm = norm
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
                "estleg:versionText": strip_or_repair_fffd_in_version_text(text),
                "rdfs:label": {
                    "@value": f"{provision_iri.removeprefix('estleg:')} (redaktsioon {redaction.global_id})",
                    "@language": "et",
                },
            }
            if redaction.global_id:
                node["estleg:versionRedactionId"] = redaction.global_id
            # #524: citation-complete on the version node itself.
            stamp_version_citation(
                node, act_title=target.title, act_iri=target.act_iri
            )
            versions_here.append(node)
        if versions_here:
            per_provision[provision_iri] = versions_here
            nodes.extend(versions_here)

    # Chain consecutive versions: supersededByVersion + close validity intervals.
    # Issue #306: ``versionValidTo`` of version k is the day *before* version k+1's
    # ``versionValidFrom`` (an *exclusive* end), so the canonical inclusive as-of-date
    # query (``?from <= D && ?to >= D``) returns exactly one version on the transition
    # day instead of both k and k+1. The last version stays open (still current).
    for versions_here in per_provision.values():
        for i, node in enumerate(versions_here[:-1]):
            nxt = versions_here[i + 1]
            this_from = node["estleg:versionValidFrom"]["@value"]
            next_from = nxt["estleg:versionValidFrom"]["@value"]
            exclusive_end = _day_before(next_from)
            # Monotonicity guard: only chain forward + close the interval when the
            # successor genuinely starts AFTER this version, i.e. the exclusive end
            # ``_day_before(next_from)`` lands on/after this version's start. A
            # residual anomaly (successor dated on/before this node — e.g. an
            # out-of-order ``current`` redaction that slipped past the chain sort)
            # would otherwise emit an inverted ``versionValidTo < versionValidFrom``
            # and a backwards ``supersededByVersion``; in that case leave this node's
            # interval open and skip the bad edge. Normal ascending chains always
            # satisfy the guard, preserving the #306 exclusive-end semantics.
            if exclusive_end < this_from:
                continue
            node["estleg:supersededByVersion"] = {"@id": nxt["@id"]}
            node["estleg:versionValidTo"] = _xsd_date(exclusive_end)
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
        stem = path.stem.removesuffix("_peep")
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
        run_timestamp=PINNED_RUN_TIMESTAMP,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
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
    parser.add_argument(
        "--cleanup-fffd",
        action="store_true",
        help=(
            "Rewrite committed provision_versions/*.jsonld, repairing U+FFFD "
            "in versionText / labels (#355). Does not fetch."
        ),
    )
    parser.add_argument(
        "--backfill-citations",
        action="store_true",
        help=(
            "Stamp estleg:rtUrl / sourceAct / provisionRef / partOfAct on "
            "committed provision_versions/*.jsonld (#524). Does not fetch."
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
    if args.cleanup_fffd:
        stats = cleanup_committed_provision_version_fffd(VERSIONS_DIR)
        print(
            f"U+FFFD cleanup: {stats['files']} file(s), "
            f"{stats['nodes']} node(s), {stats['fields']} field(s)"
        )
        if not args.backfill_citations:
            return 0
    if args.backfill_citations:
        stats = backfill_version_sidecars(VERSIONS_DIR, KRR_DIR)
        print(
            f"#524 citation backfill: {stats['files']} file(s), "
            f"{stats['nodes']} node(s) of {stats['nodes_seen']} "
            f"(scanned {stats['files_scanned']}, "
            f"missing_act_iri={stats['missing_act_iri']})"
        )
        return 0
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
            skip_error = missing_law_target_error(slug, krr_dir=krr_dir)
            print(f"\n=== {slug}: {skip_error} — skipping ===")
            results.append(LawResult(
                slug=slug, title=slug, redactions_total=0, redactions_fetched=0,
                redactions_failed=0, provisions_in_law=0, provisions_versioned=0,
                versions_emitted=0, sidecar_path=None,
                max_redactions=max_redactions, error=skip_error,
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
