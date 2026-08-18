#!/usr/bin/env python3
"""Fetch Estonian state-level regulations (määrused) from Riigi Teataja and
generate JSON-LD ontology files under ``krr_outputs/regulations/riik/``.

Scope:
  * State-level regulations by default — Vabariigi Valitsus, ministers,
    Eesti Pank, and other central issuers (`kov=false` in the API).
  * Municipal regulations when ``--kov`` is passed.
  * Current snapshot only — generated against an explicit `kehtiv=YYYY-MM-DD`
    date for reproducibility. Historical redactions are out of scope.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    act_root_node,
    is_domain_individual,
)
from estleg.riigiteataja_common import (
    BASE_URL,
    CONTEXT,
    KRR_DIR,
    SourceListFetchError,
    collect_full_text,
    collect_text,
    ct,
    fetch_acts,
    fetch_xml,
    ln,
    new_page_stats,
    parse_act_metadata,
    parse_html_konteiner,
    sanitize_id,
    save_json,
    slugify,
    truncate_on_boundary,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEHTIV = "2026-05-01"
OUTPUT_RIIK = KRR_DIR / "regulations" / "riik"
OUTPUT_KOV = KRR_DIR / "regulations" / "kov"
GENERATION_MODES = ("missing-only", "refresh", "force")


# ---------------------------------------------------------------------------
# Issuer → regulation class
# ---------------------------------------------------------------------------

#: Explicit lookup of KNOWN central issuers (lowercased exact match) to the
#: extra regulation classes they carry on top of ``estleg:NationalRegulation``.
#: An empty list marks a recognised National-only issuer (e.g. Eesti Pank): it
#: is a known issuer, so it is deliberately NOT recorded as unclassified.
ISSUER_CLASS_TABLE: dict[str, list[str]] = {
    "vabariigi valitsus": ["estleg:GovernmentRegulation"],
    "eesti pank": [],
}

#: Issuer strings matching neither ``ISSUER_CLASS_TABLE`` nor the ``*minister``
#: heuristic. Recorded so the pipeline can surface genuinely-unrecognised
#: issuers (data drift) instead of silently typing them bare National. This is
#: process-wide bookkeeping only; callers/tests may ``.clear()`` / inspect it.
UNCLASSIFIED_ISSUERS: Counter[str] = Counter()


def classify_issuer(issuer: str | None, is_kov: bool) -> list[str]:
    """Return the list of regulation classes to attach to an act node.

    Both branches are subclasses of ``estleg:DomesticRegulation`` (the common
    superclass introduced in issue #424). State-level regulations carry
    ``estleg:NationalRegulation`` (plus a more specific issuer subclass).
    Municipal regulations are NOT national legislation — a KOV määrus is
    enacted by a local council, not by a state organ — so a KOV act gets
    ``estleg:MunicipalRegulation`` only and must never also be typed
    ``estleg:NationalRegulation`` (issue #267: the two are contradictory at
    the instance level; issue #424: they are now sibling subclasses of
    ``DomesticRegulation``, so there is no entailment path between them).

    ``estleg:DomesticRegulation`` is intentionally NOT stamped on instances:
    membership follows by RDFS entailment over the T-Box, and the shared
    domestic-regulation SHACL coverage is provided by listing
    ``estleg:MunicipalRegulation`` as an additional ``sh:targetClass`` on the
    domestic-regulation shapes (so it holds under inference=none too). Adding
    it explicitly would only add redundant triples to ~11k KOV roots.

    Issuers that are neither a known central issuer (``ISSUER_CLASS_TABLE``)
    nor a ``*minister`` fall through to a bare ``estleg:NationalRegulation``
    and are tallied in ``UNCLASSIFIED_ISSUERS`` so genuinely-unrecognised
    issuers (data drift) can be surfaced rather than passing silently. The
    class output for the known cases below is unchanged.

    Examples:
      Vabariigi Valitsus            -> [NationalRegulation, GovernmentRegulation]
      Sotsiaalminister              -> [NationalRegulation, MinisterialRegulation]
      Eesti Pank                    -> [NationalRegulation]
      <KOV name>, is_kov=True       -> [MunicipalRegulation]
    """
    if is_kov:
        return ["estleg:MunicipalRegulation"]

    classes = ["estleg:NationalRegulation"]
    if not issuer:
        return classes

    issuer_l = issuer.lower()
    if issuer_l in ISSUER_CLASS_TABLE:
        # Known issuer: extend with its explicit extra classes (possibly none,
        # e.g. Eesti Pank is National-only) — never recorded as unclassified.
        classes.extend(ISSUER_CLASS_TABLE[issuer_l])
    elif issuer_l.endswith("minister"):
        classes.append("estleg:MinisterialRegulation")
    else:
        # Neither a known central issuer nor a *minister: surface it so data
        # drift is visible rather than being silently typed bare National.
        UNCLASSIFIED_ISSUERS[issuer] += 1
    return classes


# ---------------------------------------------------------------------------
# Annex extraction
# ---------------------------------------------------------------------------

def extract_annexes(root: ET.Element, prefix: str) -> list[dict]:
    """Collect `<lisa>` / `<lisaViide>` / `<fail>` elements as Annex nodes."""
    annexes: list[dict] = []
    seen: set[str] = set()

    for el in root.iter():
        tag = ln(el.tag)
        if tag not in ("lisa", "lisaViide", "fail"):
            continue

        # The annex number is sometimes a direct attribute, sometimes a child.
        annex_nr = (
            ct(el, "lisaNr")
            or ct(el, "viideNr")
            or el.attrib.get("nr")
            or el.attrib.get("number")
            or str(len(annexes) + 1)
        )
        title = (
            ct(el, "lisaPealkiri")
            or ct(el, "viidePealkiri")
            or ct(el, "kuvatavTekst")
            or ""
        )
        # File annexes carry a source URL via the `viideURI` child or a
        # `kuvatavTekst` link.
        href = None
        for child in el.iter():
            ctag = ln(child.tag)
            if ctag in ("viideURI", "url", "link") and child.text:
                href = child.text.strip()
                break

        annex_key = f"{annex_nr}::{title}"
        if annex_key in seen:
            continue
        seen.add(annex_key)

        annex_id = f"estleg:{prefix}_Annex_{sanitize_id(annex_nr)}"
        node: dict = {
            "@id": annex_id,
            "@type": ["owl:NamedIndividual", "estleg:Annex"],
            "rdfs:label": f"Lisa {annex_nr}{(' – ' + title) if title else ''}",
            "estleg:annexNumber": str(annex_nr),
        }
        if href:
            full = (
                href
                if href.startswith(("http://", "https://"))
                # urljoin handles ./, ../, and bare-relative correctly;
                # the previous "BASE_URL + href.lstrip('.')" produced
                # "https://www.riigiteataja.ee//akt/..." for "../akt/..."
                # and "https://www.riigiteataja.eelisa/foo.pdf" for
                # bare-relative "lisa/foo.pdf".
                else urllib.parse.urljoin(BASE_URL + "/", href)
            )
            node["dcterms:source"] = {"@id": full}
        annexes.append(node)

    return annexes


# ---------------------------------------------------------------------------
# Preamble extraction (legal basis)
# ---------------------------------------------------------------------------

def extract_preamble(root: ET.Element) -> str:
    """Return the regulation preamble (legal basis text) as plain text.

    Modern XML stores it under `<sisu><preambul>`. Pre-2010 regulations
    embed it as the first paragraph of the `<HTMLKonteiner>` body — the
    HTML fallback path returns it directly so we don't repeat that here.
    """
    for el in root.iter():
        if ln(el.tag) != "preambul":
            continue
        parts: list[str] = []
        for child in el.iter():
            tag = ln(child.tag)
            # Pull both plain text and the rendered text of any cross-references.
            if tag in ("tavatekst", "kuvatavTekst") and child.text:
                parts.append(child.text.strip())
        joined = " ".join(parts)
        return " ".join(joined.split())
    return ""


# ---------------------------------------------------------------------------
# Provision extraction — structured XML path
# ---------------------------------------------------------------------------

def provision_summary(display: str, label: str, source_title: str, body_text: str = "") -> str:
    """Return the SHACL-required compact summary for a regulation provision."""
    if body_text:
        return truncate_on_boundary(body_text, 500)

    label_text = " ".join(label.split())
    display_text = " ".join(display.split())
    if label_text and label_text != display_text:
        return label_text[:500]

    fallback = " ".join(part for part in (source_title, display_text) if part)
    return fallback[:500] if fallback else display_text


def collect_structured_paragraphs(root: ET.Element, prefix: str, title: str, class_id: str, act_iri: str) -> list[dict]:
    """Build provision nodes from `<paragrahv>` elements (modern XML)."""
    nodes: list[dict] = []
    seen_ids: set[str] = set()

    for p in [el for el in root.iter() if ln(el.tag) == "paragrahv"]:
        nr = ct(p, "paragrahvNr") or "?"
        ptitle = ct(p, "paragrahvPealkiri") or ""
        display = ct(p, "kuvatavNr") or f"§ {nr}"
        text = collect_text(p)
        full_text = collect_full_text(p)

        p_id = f"estleg:{prefix}_Par_{sanitize_id(nr)}"
        if p_id in seen_ids:
            p_id = f"{p_id}_{len(seen_ids)}"
        seen_ids.add(p_id)

        if ptitle:
            label = f"{display} {ptitle}"
        elif text:
            excerpt = text[:80].rstrip()
            if len(text) > 80:
                excerpt = excerpt + "..."
            label = f"{display} [{excerpt}]"
        else:
            label = display

        node: dict = {
            "@id": p_id,
            "@type": ["owl:NamedIndividual", class_id],
            "estleg:paragrahv": display,
            "rdfs:label": label,
            "estleg:sourceAct": title,
            # Issue #415: structural IRI join from provision up to its act root.
            "estleg:partOfAct": {"@id": act_iri},
            "estleg:summary": provision_summary(display, label, title, text),
        }
        if full_text:
            node["estleg:legalText"] = full_text
        nodes.append(node)

    return nodes


# ---------------------------------------------------------------------------
# Provision extraction — legacy HTMLKonteiner fallback
# ---------------------------------------------------------------------------

def collect_html_paragraphs(root: ET.Element, prefix: str, title: str, class_id: str, act_iri: str) -> tuple[str, list[dict]]:
    """Build provision nodes from the legacy HTMLKonteiner CDATA body.

    Returns ``(preamble_text, paragraph_nodes)``.
    """
    container = None
    for el in root.iter():
        if ln(el.tag) == "HTMLKonteiner":
            container = el
            break
    if container is None or not (container.text or "").strip():
        return "", []

    preamble, paragraphs = parse_html_konteiner(container.text)

    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for entry in paragraphs:
        nr = entry["nr"]
        display = f"§ {nr}"
        ptitle = entry["title"].strip()
        text_full = entry["text"].strip()
        # #368: cut on a sentence/word boundary, not mid-token.
        summary = truncate_on_boundary(text_full, 500) if text_full else ""

        p_id = f"estleg:{prefix}_Par_{sanitize_id(nr)}"
        if p_id in seen_ids:
            p_id = f"{p_id}_{len(seen_ids)}"
        seen_ids.add(p_id)

        if ptitle:
            label = f"{display} {ptitle}"
        elif summary:
            excerpt = summary[:80].rstrip()
            if len(summary) > 80:
                excerpt = excerpt + "..."
            label = f"{display} [{excerpt}]"
        else:
            label = display

        node: dict = {
            "@id": p_id,
            "@type": ["owl:NamedIndividual", class_id],
            "estleg:paragrahv": display,
            "rdfs:label": label,
            "estleg:sourceAct": title,
            # Issue #415: structural IRI join from provision up to its act root.
            "estleg:partOfAct": {"@id": act_iri},
            "estleg:summary": provision_summary(display, label, title, summary),
        }
        if text_full:
            node["estleg:legalText"] = text_full
        nodes.append(node)

    return preamble, nodes


# ---------------------------------------------------------------------------
# Temporal coherence — repealed-act tombstone (issue #374)
# ---------------------------------------------------------------------------

# SHACL ``LegalProvisionShape`` requires ``estleg:summary``. Surgical
# corpus stripping therefore replaces void-law summaries with this marker
# rather than deleting the predicate. ``estleg:legalText`` is dropped.
REPEALED_BODY_TOMBSTONE = "Repealed; provision body suppressed (issue #374)."
_TEMPORAL_STATUS_REPEALED_MARKER = b'"estleg:temporalStatus": "repealed"'


def _iso_day(value: str | None) -> str | None:
    """Return the ``YYYY-MM-DD`` prefix, or None when blank/missing."""
    if not value:
        return None
    day = str(value).strip()[:10]
    return day or None


def _xsd_date_value(value) -> str | None:
    """Unwrap a JSON-LD xsd:date (plain string or ``{"@value": ...}``)."""
    if isinstance(value, dict):
        value = value.get("@value")
    if value is None:
        return None
    return _iso_day(str(value))


def _repealed_before_snapshot(repeal_date: str | None, kehtiv: str | None) -> bool:
    """Return True when an act was already repealed *before* the snapshot.

    The snapshot is taken at ``kehtiv`` (``YYYY-MM-DD``). An act whose
    ``kehtivuseLopp`` repeal date (``repeal_date``) is strictly earlier than
    ``kehtiv`` was legally void at snapshot time — Riigi Teataja occasionally
    returns such stale rows under a ``kehtiv`` query (issue #374). We treat
    only ``repeal_date < kehtiv`` as repealed-before-snapshot so acts repealed
    *on* the snapshot date (still in force that day) remain snapshot-active.

    Both dates are ISO ``YYYY-MM-DD`` strings (offset already stripped by
    :func:`parse_act_metadata`); lexicographic comparison is therefore a
    correct chronological comparison. Missing/blank either side -> False.
    """
    repeal_day = _iso_day(repeal_date)
    snapshot_day = _iso_day(kehtiv)
    if not repeal_day or not snapshot_day:
        return False
    return repeal_day < snapshot_day


def _repealed_as_of(repeal_date: str | None, as_of: str | None) -> bool:
    """Return True when ``repeal_date`` is on or before ``as_of``.

    Inclusive, matching ``extract_temporal_data.determine_temporal_status``
    (an act is ``repealed`` on its repeal day).
    """
    repeal_day = _iso_day(repeal_date)
    as_of_day = _iso_day(as_of)
    if not repeal_day or not as_of_day:
        return False
    return repeal_day <= as_of_day


def _should_tombstone_regulation(
    repeal_date: str | None,
    kehtiv: str | None,
    *,
    temporal_status: str | None = None,
    evaluation_date: str | None = None,
) -> bool:
    """Tombstone when the act is repealed *or* void at the snapshot.

    Issue #374: legally-void text must not be served as substantive.

    * ``temporal_status == "repealed"`` always tombstones, even when
      ``repeal_date`` is after an older hardcoded ``kehtiv`` (the vangla
      sisekorraeeskiri case: repeal 2026-05-04, snapshot 2026-05-01).
    * ``repeal_date < kehtiv`` tombstones snapshot-stale RT rows.
    * When ``temporal_status`` is omitted, status is inferred against
      ``evaluation_date`` (default ``BUILD_EVALUATION_DATE``), the same
      pin ``extract_temporal_data`` uses.
    """
    if temporal_status == "repealed":
        return True
    if _repealed_before_snapshot(repeal_date, kehtiv):
        return True
    if temporal_status is None:
        return _repealed_as_of(
            repeal_date, evaluation_date or BUILD_EVALUATION_DATE
        )
    return False


def _is_provision_node(node: object) -> bool:
    return isinstance(node, dict) and "estleg:paragrahv" in node


def _node_has_legal_text(node: dict) -> bool:
    text = node.get("estleg:legalText")
    if text is None:
        return False
    if isinstance(text, dict):
        text = text.get("@value")
    if text is None:
        return False
    return bool(str(text).strip())


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------

def build_regulation_jsonld(
    title: str,
    info: dict,
    root: ET.Element,
    *,
    is_kov: bool,
    kehtiv: str | None = None,
    temporal_status: str | None = None,
    evaluation_date: str | None = None,
) -> tuple[dict, dict[str, int]]:
    """Generate the JSON-LD document for one regulation.

    Returns the doc plus a small stats dict used for reporting.

    ``kehtiv`` (when provided) is stamped onto the ontology node as
    ``estleg:kehtiv`` so downstream staleness checks (missing-only
    re-runs) can compare a stored snapshot date against the current
    run.

    ``temporal_status`` (optional) is the already-derived
    ``estleg:temporalStatus``. When omitted, a repealed status is
    inferred from ``repealDate`` vs ``evaluation_date`` (default
    ``BUILD_EVALUATION_DATE``). Either a derived ``repealed`` status
    or a pre-snapshot repeal tombstones the body (issue #374).
    """
    metadata = parse_act_metadata(root)
    tid = metadata.get("terviktekstId") or info.get("tid") or ""
    gid = metadata.get("globalId") or info.get("gid") or ""
    issuer = metadata.get("issuer") or info.get("valjaandja") or ""
    rt_url = info.get("url", "")

    if not tid:
        # Without a stable ID we can't produce a reliable IRI — bail out.
        raise ValueError(f"Regulation has no terviktekstID: {title}")

    prefix = f"Reg_{tid}"
    class_id = f"estleg:Regulation_{tid}"
    ontology_id = f"estleg:{prefix}_Map_2026"

    rt_source_url = ""
    if rt_url:
        rt_source_url = BASE_URL + rt_url if rt_url.startswith("/") else rt_url

    # Issue #374: an act that is already repealed (temporalStatus), or
    # whose repealDate is strictly before the snapshot, is legally void
    # as substantive law. Riigi Teataja sometimes returns stale rows
    # under a `kehtiv` query, and extract_temporal_data later marks
    # acts that lapsed after an older hardcoded snapshot. Tombstone
    # both — keep the act node (repealDate / temporalStatus so the
    # index can exclude it from the active count) but emit NO
    # provision/annex/preamble content.
    repeal_date = metadata.get("repealDate")
    repealed_before_snapshot = _repealed_before_snapshot(repeal_date, kehtiv)
    tombstone = _should_tombstone_regulation(
        repeal_date,
        kehtiv,
        temporal_status=temporal_status,
        evaluation_date=evaluation_date,
    )

    if tombstone:
        provisions: list[dict] = []
        annexes: list[dict] = []
        preamble = ""
        parse_mode = (
            "repealedBeforeSnapshot" if repealed_before_snapshot else "repealed"
        )
    else:
        # Provisions: try structured first, fall back to HTMLKonteiner
        provisions = collect_structured_paragraphs(root, prefix, title, class_id, ontology_id)
        parse_mode = "structured"
        preamble_html = ""
        if not provisions:
            preamble_html, provisions = collect_html_paragraphs(root, prefix, title, class_id, ontology_id)
            parse_mode = "html_fallback" if provisions else "no_paragraphs"

        # Preamble: prefer structured `<preambul>`, fall back to HTML preamble
        preamble = extract_preamble(root) or preamble_html
        annexes = extract_annexes(root, prefix)

    # ---- Build the act ontology node ------------------------------------
    act_classes = ["estleg:Act", *classify_issuer(issuer, is_kov)]
    ontology_node: dict = {
        "@id": ontology_id,
        "@type": act_classes,
        "rdfs:label": f"{title} (määrus)",
        "dc:source": title,
        "dcterms:title": title,
        "estleg:documentType": "määrus",
        "estleg:isKov": {"@value": "true" if is_kov else "false", "@type": "xsd:boolean"},
        "estleg:parseMode": parse_mode,
    }
    if rt_source_url:
        ontology_node["dcterms:source"] = {"@id": rt_source_url}
        ontology_node["owl:sameAs"] = {"@id": rt_source_url}
    if issuer:
        ontology_node["estleg:issuer"] = issuer
    if metadata.get("actNumber"):
        ontology_node["estleg:actNumber"] = metadata["actNumber"]
    if gid:
        ontology_node["estleg:globalId"] = str(gid)
    ontology_node["estleg:terviktekstId"] = str(tid)
    if kehtiv:
        # Snapshot date as xsd:date so downstream queries can compare
        # cleanly against today() during a staleness audit.
        ontology_node["estleg:kehtiv"] = {"@value": kehtiv, "@type": "xsd:date"}
    if metadata.get("entryIntoForce"):
        ontology_node["estleg:entryIntoForce"] = {
            "@value": metadata["entryIntoForce"],
            "@type": "xsd:date",
        }
    if metadata.get("repealDate"):
        ontology_node["estleg:repealDate"] = {
            "@value": metadata["repealDate"],
            "@type": "xsd:date",
        }
    if metadata.get("lastAmendmentDate"):
        ontology_node["estleg:lastAmendmentDate"] = {
            "@value": metadata["lastAmendmentDate"],
            "@type": "xsd:date",
        }
    if preamble:
        ontology_node["estleg:preambleText"] = preamble
    if annexes:
        ontology_node["estleg:hasAnnex"] = [{"@id": a["@id"]} for a in annexes]
    if parse_mode == "no_paragraphs":
        ontology_node["estleg:contentStatus"] = "noStructuredBody"
        ontology_node["estleg:contentStatusReason"] = (
            "No structured paragraphs, HTML paragraphs, annexes, or preamble "
            "were parsed from the source XML."
        )
    elif tombstone:
        # Tombstone marker (issue #374). ``temporalStatus=repealed`` mirrors
        # extract_temporal_data's vocabulary so downstream consumers (e.g. the
        # KOV similarity pass) can filter on a single predicate. Pre-snapshot
        # voids also get ``contentStatus=repealedBeforeSnapshot``; a later
        # repeal (in force at kehtiv, repealed as of the evaluation pin)
        # keeps temporalStatus only so we do not mis-label the snapshot.
        ontology_node["estleg:temporalStatus"] = "repealed"
        if repealed_before_snapshot:
            ontology_node["estleg:contentStatus"] = "repealedBeforeSnapshot"
            ontology_node["estleg:contentStatusReason"] = (
                f"Act repealed on {repeal_date}, before the snapshot "
                f"date {kehtiv}; provision content suppressed (issue #374)."
            )
        else:
            ontology_node["estleg:contentStatusReason"] = (
                f"Act temporalStatus=repealed (repealDate {repeal_date}); "
                "provision content suppressed (issue #374)."
            )

    graph: list[dict] = [
        ontology_node,
        {
            "@id": class_id,
            "@type": ["owl:Class"],
            "rdfs:label": "Õigusnorm (paragrahv)",
            "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
        },
    ]
    graph.extend(annexes)
    graph.extend(provisions)

    stats = {
        "paragraphs": len(provisions),
        "annexes": len(annexes),
        "has_preamble": int(bool(preamble)),
        "html_fallback": int(parse_mode == "html_fallback"),
        "no_paragraphs": int(parse_mode == "no_paragraphs"),
        "repealed_before_snapshot": int(repealed_before_snapshot),
        "repealed": int(tombstone),
    }

    return {"@context": CONTEXT, "@graph": graph}, stats


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def make_filename(title: str, tid: str, is_kov: bool, issuer: str | None) -> tuple[Path, str]:
    """Return (output_path, slug) for a regulation, namespacing by issuer for KOV."""
    slug_base = slugify(title, max_len=60) or f"regulation_{tid}"
    filename = f"{slug_base}_t{tid}_peep.json"
    if is_kov:
        issuer_slug = slugify(issuer or "kov", max_len=40) or "kov"
        return OUTPUT_KOV / issuer_slug / filename, slug_base
    return OUTPUT_RIIK / filename, slug_base


def _gid_rank(gid: str) -> tuple[int, int, str]:
    """Return a sort key for a globalID dedup comparison.

    Riigi Teataja globalIDs are numeric strings of varying lengths
    (e.g. ``"99052024005"`` vs ``"128062023003"``). A naive lexicographic
    compare ranks ``"99..."`` above ``"128..."`` so the OLDER redaction
    wins — exactly the bug we're fixing here.

    The returned tuple sorts so:
      * numeric-castable IDs are ranked above non-numeric (rank=1 > rank=0),
      * within numeric, larger numeric value wins,
      * within non-numeric (rank=0), the integer field stays 0 and we
        fall back to lexicographic order on the original string.
    """
    if gid is None:
        return (0, 0, "")
    s = str(gid).strip()
    try:
        return (1, int(s), s)
    except (TypeError, ValueError):
        return (0, 0, s)


def gather_regulations(
    kov: bool,
    kehtiv: str,
    limit: int | None,
    *,
    allow_partial: bool = False,
) -> tuple[dict[str, dict], dict]:
    """Run the search API and return ``{terviktekstID: act_info}``.

    De-duplication: keep the entry with the largest globalID per terviktekstID
    (matches the convention used for laws — newest redaction wins).
    """
    by_tid: dict[str, dict] = {}
    seen = 0
    # Per-page success/fail/retry counters populated in-place by
    # ``fetch_acts``. Surfaced in the index ``run`` block so a consumer
    # can see exactly which pages were fetched, failed, or retried.
    page_stats = new_page_stats()
    # When a --limit is set, ask the API for fewer rows so we don't
    # gratuitously pull a full 500-row page when the caller just wants
    # a 5-act dry run. Floor at 50 to keep page-iteration efficient
    # for moderate limits.
    if limit is None:
        page_size = 500
    else:
        page_size = min(500, max(50, limit * 2))
    for act in fetch_acts(
        document="määrus",
        kov=kov,
        kehtiv=kehtiv,
        limiit=page_size,
        allow_partial=allow_partial,
        stats=page_stats,
    ):
        seen += 1
        tid = str(act.get("terviktekstID") or "")
        gid = str(act.get("globaalID") or "")
        if not tid:
            continue
        prev = by_tid.get(tid)
        if prev is None or _gid_rank(gid) > _gid_rank(str(prev.get("gid", ""))):
            by_tid[tid] = {
                "tid": tid,
                "gid": gid,
                "url": act.get("url", ""),
                "pealkiri": (act.get("pealkiri") or "").strip(),
                "valjaandja": act.get("valjaandja") or "",
                "kehtivus": act.get("kehtivus") or {},
            }
        if limit is not None and len(by_tid) >= limit:
            break
    # A terminal fetch failure under --allow-partial makes ``fetch_acts``
    # stop early without raising; detect that here so the run is marked
    # incomplete rather than masquerading as a full snapshot.
    completed = page_stats["pagesFailed"] == 0
    print(f"  Pulled {seen} search rows -> {len(by_tid)} unique regulations")
    return by_tid, {
        "requestedDocument": "määrus",
        "kov": kov,
        "kehtiv": kehtiv,
        "searchRowsSeen": seen,
        "uniqueActs": len(by_tid),
        "complete": completed,
        "limited": limit is not None,
        "pages": page_stats,
    }


def regulation_files(out_dir: Path) -> list[Path]:
    return sorted(p for p in out_dir.glob("**/*_peep.json") if p.is_file())


def summarize_regulation_doc(doc: dict) -> dict:
    graph = doc.get("@graph", [])
    ontology = act_root_node({"@graph": graph}) or {}
    issuer = ontology.get("estleg:issuer") or "(unknown)"
    content_status = ontology.get("estleg:contentStatus")
    is_stub = content_status == "noStructuredBody"
    # Issue #374: any ``temporalStatus=repealed`` act (pre-snapshot
    # tombstone *or* later-repealed) is excluded from the ``full``
    # coverage bucket and from ``activeRegulations``.
    is_repealed = (
        content_status == "repealedBeforeSnapshot"
        or ontology.get("estleg:temporalStatus") == "repealed"
    )
    if is_repealed:
        status = "repealed"
    elif is_stub:
        status = "stub"
    else:
        status = "full"
    return {
        "paragraphs": sum(1 for n in graph if isinstance(n, dict) and "estleg:paragrahv" in n),
        "annexes": sum(1 for n in graph if isinstance(n, dict) and "estleg:Annex" in (n.get("@type") or [])),
        "has_preamble": int(bool(ontology.get("estleg:preambleText"))),
        "html_fallback": int(ontology.get("estleg:parseMode") == "html_fallback"),
        "no_paragraphs": int(is_stub),
        "repealed_before_snapshot": int(is_repealed),
        "issuer": issuer if isinstance(issuer, str) else "(unknown)",
        # Issue #119: per-act coverage status surfaced in the index.
        # ``stub`` = act-level node only (no structured body); ``full``
        # = structured (or HTML-fallback) provisions present; ``repealed``
        # = temporalStatus=repealed tombstone (issue #374).
        "status": status,
        "contentStatus": content_status if isinstance(content_status, str) else None,
    }


def build_regulation_index(
    out_dir: Path,
    *,
    is_kov: bool,
    kehtiv: str,
    run_stats: dict | None = None,
    doc_cache: dict[Path, dict] | None = None,
) -> dict:
    """Build the regulation index for ``out_dir``.

    ``doc_cache`` (optional) supplies in-memory ``{Path: doc}`` entries
    written during the current run so the index can avoid re-reading
    JSON files we just produced. Paths missing from the cache fall
    back to disk read.
    """
    files = regulation_files(out_dir)
    totals = Counter()
    issuer_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    file_entries = []
    # Issue #119: per-act coverage ledger — slug, on-disk path, status
    # (full/stub), and the act node's contentStatus marker (None when
    # structured). Mirrors the laws manifest's outputsAll entries so a
    # consumer can distinguish no-body acts from fully-mapped ones from
    # the index alone, not just by re-opening each peep file.
    act_entries: list[dict] = []

    for path in files:
        doc: dict | None = None
        if doc_cache is not None:
            doc = doc_cache.get(path)
        if doc is None:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
        stats = summarize_regulation_doc(doc)
        for key in ("paragraphs", "annexes", "has_preamble", "html_fallback", "no_paragraphs", "repealed_before_snapshot"):
            totals[key] += stats[key]
        issuer_counts[stats["issuer"]] += 1
        status_counts[stats["status"]] += 1
        rel = str(path.relative_to(out_dir))
        file_entries.append(rel)
        # Slug: filename stem with the trailing ``_t<id>_peep`` /
        # ``_peep`` suffix stripped, matching make_filename's slug_base.
        slug = re.sub(r"(_t\d+)?_peep$", "", path.stem)
        entry: dict = {
            "slug": slug,
            "file": rel,
            "status": stats["status"],
        }
        if stats["contentStatus"]:
            entry["contentStatus"] = stats["contentStatus"]
        if stats["status"] == "stub":
            entry["reason"] = "no structured paragraphs, annexes, or preamble parsed from source XML"
        elif stats["status"] == "repealed":
            if stats["contentStatus"] == "repealedBeforeSnapshot":
                entry["reason"] = (
                    "repealed before snapshot; provision content suppressed (issue #374)"
                )
            else:
                entry["reason"] = (
                    "temporalStatus=repealed; provision content suppressed (issue #374)"
                )
        act_entries.append(entry)

    return {
        "generated": f"{BUILD_EVALUATION_DATE}T00:00:00+00:00",  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "kehtiv": kehtiv,
        "kov": is_kov,
        # ``totalRegulations`` stays == len(files) (the validate_all
        # filesystem-parity invariant). Issue #374: the *active* corpus size
        # excludes every ``temporalStatus=repealed`` act (not only
        # pre-snapshot tombstones) so consumers do not need to rescan.
        "totalRegulations": len(files),
        "activeRegulations": len(files) - totals["repealed_before_snapshot"],
        "repealedBeforeSnapshotCount": totals["repealed_before_snapshot"],
        "totalParagraphs": totals["paragraphs"],
        "totalAnnexes": totals["annexes"],
        "regulationsWithPreamble": totals["has_preamble"],
        "htmlFallbackCount": totals["html_fallback"],
        "noStructuredBodyCount": totals["no_paragraphs"],
        "byIssuer": dict(sorted(issuer_counts.items(), key=lambda x: (-x[1], x[0]))),
        "byStatus": dict(sorted(status_counts.items())),
        "files": file_entries,
        "acts": act_entries,
        "run": run_stats or {},
    }


def existing_doc_matches(path: Path, doc: dict) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    return existing == doc


def _ontology_node(doc: dict) -> dict | None:
    """Return the act-root node in a doc's @graph (#435)."""
    return act_root_node(doc)


def existing_is_stale(
    path: Path,
    *,
    expected_tid: str | None,
    expected_kehtiv: str | None,
) -> bool:
    """Return True when an existing output is stale w.r.t. current run.

    Stale = the on-disk file's stored ``terviktekstId`` differs from
    the current build's terviktekstId, OR its stored ``estleg:kehtiv``
    snapshot date differs from the current run's kehtiv. Used by
    missing-only mode to refresh quietly when a previous snapshot is
    out of date — the previous behaviour was to keep stale files
    forever once written.

    A file is considered NOT stale (returns False) when:
      * it is unreadable / missing — caller treats that as "needs
        write" anyway, so the dispatch is unchanged;
      * neither check could be made (no expected values supplied).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False

    ontology = _ontology_node(existing)
    if ontology is None:
        return False

    if expected_tid:
        stored_tid = ontology.get("estleg:terviktekstId")
        if isinstance(stored_tid, dict):
            stored_tid = stored_tid.get("@value")
        if stored_tid and str(stored_tid) != str(expected_tid):
            return True

    if expected_kehtiv:
        stored_kehtiv = ontology.get("estleg:kehtiv")
        if isinstance(stored_kehtiv, dict):
            stored_kehtiv = stored_kehtiv.get("@value")
        if stored_kehtiv is None or str(stored_kehtiv) != str(expected_kehtiv):
            return True

    return False


def write_regulation_output(
    out_path: Path,
    doc: dict,
    *,
    mode: str,
    expected_kehtiv: str | None = None,
) -> str:
    """Write one regulation artifact and return a run-stat status key.

    ``expected_kehtiv`` (optional) drives a staleness check in
    missing-only mode: when the on-disk file's stored kehtiv or
    terviktekstId differs from the current run, the output is
    refreshed in place rather than skipped silently.
    """
    if mode not in GENERATION_MODES:
        raise ValueError(f"Unsupported generation mode: {mode}")

    existed = out_path.exists()
    if existed and mode == "missing-only":
        # Find the new doc's tid up front so the stale check has it.
        new_ontology = _ontology_node(doc) or {}
        new_tid = new_ontology.get("estleg:terviktekstId")
        if isinstance(new_tid, dict):
            new_tid = new_tid.get("@value")
        if existing_is_stale(
            out_path,
            expected_tid=new_tid if new_tid else None,
            expected_kehtiv=expected_kehtiv,
        ):
            save_json(out_path, doc)
            return "refreshedStale"
        return "existingSkipped"
    if existed and mode == "refresh" and existing_doc_matches(out_path, doc):
        return "unchanged"

    save_json(out_path, doc)
    if not existed:
        return "newlyGenerated"
    if mode == "force":
        return "forceRewritten"
    return "refreshed"


def regulation_file_tid(
    path: Path,
    *,
    doc_cache: dict[Path, dict] | None = None,
) -> str | None:
    """Read the terviktekstId stored on an existing regulation peep file.

    ``doc_cache`` (optional) supplies in-memory ``{Path: doc}`` entries
    so the index pass doesn't re-read files written earlier in the
    same run.
    """
    doc: dict | None = None
    if doc_cache is not None:
        doc = doc_cache.get(path)
    if doc is None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if not is_domain_individual(node) and "owl:Ontology" not in types:
            continue
        tid = node.get("estleg:terviktekstId")
        if isinstance(tid, str) and tid:
            return tid
    return None


def source_removed_files(
    out_dir: Path,
    current_tids: set[str],
    *,
    doc_cache: dict[Path, dict] | None = None,
) -> list[str]:
    """List existing peep files whose tid is no longer in the API snapshot.

    ``doc_cache`` is threaded through to ``regulation_file_tid`` so we
    skip re-reading JSON files we've just written or read earlier in
    the run.
    """
    removed: list[str] = []
    for path in regulation_files(out_dir):
        tid = regulation_file_tid(path, doc_cache=doc_cache)
        if tid and tid not in current_tids:
            removed.append(str(path.relative_to(out_dir)))
    return removed


def strip_repealed_provision_bodies(
    doc: dict,
    *,
    kehtiv: str | None = None,
) -> dict[str, int]:
    """Drop void provision body fields from a ``temporalStatus=repealed`` act.

    Keeps provision ``@id`` / types / structural links and the act-level
    repeal metadata. ``estleg:legalText`` is removed; ``estleg:summary``
    is replaced with :data:`REPEALED_BODY_TOMBSTONE` so SHACL
    ``LegalProvisionShape`` still sees a required summary. Pre-snapshot
    repeals also receive ``contentStatus=repealedBeforeSnapshot``.

    No-op (and returns zeros) when the act is not repealed.
    """
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return {"stripped": 0, "provisions": 0}

    ontology = _ontology_node(doc) or {}
    if ontology.get("estleg:temporalStatus") != "repealed":
        return {"stripped": 0, "provisions": 0}

    repeal_date = _xsd_date_value(ontology.get("estleg:repealDate"))
    file_kehtiv = _xsd_date_value(ontology.get("estleg:kehtiv")) or kehtiv
    stripped = 0
    provisions = 0
    for node in graph:
        if not _is_provision_node(node):
            continue
        provisions += 1
        changed = False
        if "estleg:legalText" in node:
            del node["estleg:legalText"]
            changed = True
        summary = node.get("estleg:summary")
        if summary != REPEALED_BODY_TOMBSTONE:
            node["estleg:summary"] = REPEALED_BODY_TOMBSTONE
            changed = True
        if changed:
            stripped += 1

    if _repealed_before_snapshot(repeal_date, file_kehtiv or DEFAULT_KEHTIV):
        if ontology.get("estleg:contentStatus") != "repealedBeforeSnapshot":
            ontology["estleg:contentStatus"] = "repealedBeforeSnapshot"
            ontology["estleg:contentStatusReason"] = (
                f"Act repealed on {repeal_date}, before the snapshot "
                f"date {file_kehtiv or DEFAULT_KEHTIV}; provision content "
                "suppressed (issue #374)."
            )

    return {"stripped": stripped, "provisions": provisions}


def iter_repealed_regulation_peeps(out_dir: Path) -> list[Path]:
    """Return peep paths whose bytes mention ``temporalStatus=repealed``."""
    hits: list[Path] = []
    for path in regulation_files(out_dir):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if _TEMPORAL_STATUS_REPEALED_MARKER not in raw:
            continue
        hits.append(path)
    return hits


def strip_repealed_regulation_corpus(
    out_dir: Path,
    *,
    kehtiv: str | None = None,
) -> dict[str, int]:
    """Surgical issue #374 pass over every repealed peep under ``out_dir``."""
    totals: Counter[str] = Counter()
    for path in iter_repealed_regulation_peeps(out_dir):
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        ontology = _ontology_node(doc) or {}
        if ontology.get("estleg:temporalStatus") != "repealed":
            continue
        totals["repealed_acts"] += 1
        stats = strip_repealed_provision_bodies(doc, kehtiv=kehtiv)
        totals["provisions"] += stats["provisions"]
        if stats["stripped"]:
            save_json(path, doc)
            totals["acts_stripped"] += 1
            totals["provisions_stripped"] += stats["stripped"]
    return dict(totals)


def count_repealed_with_provision_legal_text(out_dir: Path) -> list[str]:
    """Return peep paths that are repealed and still carry provision legalText."""
    remaining: list[str] = []
    for path in iter_repealed_regulation_peeps(out_dir):
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        ontology = _ontology_node(doc) or {}
        if ontology.get("estleg:temporalStatus") != "repealed":
            continue
        for node in doc.get("@graph", []):
            if _is_provision_node(node) and _node_has_legal_text(node):
                remaining.append(str(path))
                break
    return remaining


def update_index_repealed_counts(
    out_dir: Path,
    *,
    is_kov: bool,
    kehtiv: str | None = None,
    doc_cache: dict[Path, dict] | None = None,
) -> dict:
    """Patch ``activeRegulations`` onto an existing regulation index.

    Preserves the committed ``files`` list and other fields. Counts every
    ``temporalStatus=repealed`` act (not only pre-snapshot tombstones).
    ``totalRegulations`` stays equal to the on-disk peep count.
    """
    index_name = "REGULATIONS_KOV_INDEX.json" if is_kov else "REGULATIONS_RIIK_INDEX.json"
    index_path = out_dir / index_name
    if index_path.is_file():
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {"kov": is_kov, "files": []}

    files = regulation_files(out_dir)
    repealed = 0
    for path in files:
        doc: dict | None = None
        if doc_cache is not None:
            doc = doc_cache.get(path)
        if doc is None:
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"repealed" not in raw:
                continue
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if summarize_regulation_doc(doc)["status"] == "repealed":
            repealed += 1

    index["kov"] = is_kov
    if kehtiv:
        index["kehtiv"] = kehtiv
    elif "kehtiv" not in index:
        index["kehtiv"] = DEFAULT_KEHTIV
    index["totalRegulations"] = len(files)
    index["activeRegulations"] = len(files) - repealed
    index["repealedBeforeSnapshotCount"] = repealed
    save_json(index_path, index)
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kov", action="store_true", help="Generate KOV (municipal) regulations instead of state-level.")
    parser.add_argument("--kehtiv", default=DEFAULT_KEHTIV, help="Snapshot date YYYY-MM-DD (default: %(default)s).")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N regulations (for dry runs).")
    parser.add_argument("--sleep", type=float, default=0.3, help="Seconds to sleep between XML fetches (be polite).")
    parser.add_argument(
        "--strip-repealed-bodies",
        action="store_true",
        help=(
            "Offline issue #374 pass: strip legalText/summary from every "
            "temporalStatus=repealed peep under the output directory and "
            "refresh activeRegulations on the index. Does not fetch XML."
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--missing-only",
        action="store_true",
        help="Only generate missing files; existing outputs are left untouched. This is the default.",
    )
    mode_group.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch current XML and rewrite existing outputs when generated JSON-LD changed.",
    )
    mode_group.add_argument(
        "--force",
        action="store_true",
        help="Fetch current XML and rewrite all selected outputs, even when unchanged.",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow a source-list fetch failure to produce a visibly partial exploratory run.",
    )
    args = parser.parse_args()

    is_kov = args.kov
    mode = "force" if args.force else "refresh" if args.refresh else "missing-only"
    out_dir = OUTPUT_KOV if is_kov else OUTPUT_RIIK
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_subdir = "maarus_kov" if is_kov else "maarus"

    if args.strip_repealed_bodies:
        print("=" * 70)
        print(
            f"Strip repealed regulation bodies "
            f"(kov={'true' if is_kov else 'false'}, kehtiv={args.kehtiv})"
        )
        print("=" * 70)
        stats = strip_repealed_regulation_corpus(out_dir, kehtiv=args.kehtiv)
        index_doc = update_index_repealed_counts(
            out_dir, is_kov=is_kov, kehtiv=args.kehtiv
        )
        remaining = count_repealed_with_provision_legal_text(out_dir)
        print(f"  Repealed acts seen:         {stats.get('repealed_acts', 0)}")
        print(f"  Acts stripped:              {stats.get('acts_stripped', 0)}")
        print(f"  Provisions stripped:        {stats.get('provisions_stripped', 0)}")
        print(f"  Remaining with legalText:   {len(remaining)}")
        print(f"  Active regulations:         {index_doc['activeRegulations']}")
        print(
            f"  Repealed (index count):     {index_doc['repealedBeforeSnapshotCount']}"
        )
        return

    print("=" * 70)
    print(f"Generate regulations (kov={'true' if is_kov else 'false'}, kehtiv={args.kehtiv}, mode={mode})")
    print("=" * 70)

    print("\n[1/3] Querying Riigi Teataja API...")
    try:
        regs, source_manifest = gather_regulations(
            kov=is_kov,
            kehtiv=args.kehtiv,
            limit=args.limit,
            allow_partial=args.allow_partial,
        )
    except SourceListFetchError as exc:
        print(f"  FATAL: {exc}")
        sys.exit(2)

    print(f"\n[2/3] Generating JSON-LD for {len(regs)} regulations...")
    run_counts = Counter({
        "newlyGenerated": 0,
        "existingSkipped": 0,
        "refreshedStale": 0,
        "unchanged": 0,
        "refreshed": 0,
        "forceRewritten": 0,
        "failedFetches": 0,
    })
    failed = 0
    totals = {"paragraphs": 0, "annexes": 0, "has_preamble": 0, "html_fallback": 0, "no_paragraphs": 0}
    # Cache the JSON-LD docs we just built (or read off disk for the
    # missing-only-skip path). Threaded into the index builder so it
    # doesn't re-read every file we just wrote.
    built_docs: dict[Path, dict] = {}

    for i, (tid, info) in enumerate(sorted(regs.items()), 1):
        title = info["pealkiri"]
        url = info["url"]
        issuer = info.get("valjaandja", "")
        out_path, slug = make_filename(title, tid, is_kov, issuer)

        # Missing-only fast path: if the existing file is already up
        # to date with the current (kehtiv, terviktekstId) signature,
        # skip the network/XML round-trip entirely.
        if out_path.exists() and mode == "missing-only":
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    existing_doc = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing_doc = None
            if existing_doc is not None and not existing_is_stale(
                out_path,
                expected_tid=tid,
                expected_kehtiv=args.kehtiv,
            ):
                run_counts["existingSkipped"] += 1
                built_docs[out_path] = existing_doc
                continue

        print(f"  [{i}/{len(regs)}] {issuer} | {title[:80]}")

        # Cache name: use globalID first (cheap to verify against RT), tid as fallback
        cache_name = f"reg_{info.get('gid') or tid}"
        root = fetch_xml(
            url,
            cache_name=cache_name,
            cache_subdir=cache_subdir,
            refresh=mode in {"refresh", "force"},
        )
        if root is None:
            print("    SKIP: could not fetch XML")
            failed += 1
            run_counts["failedFetches"] += 1
            continue

        try:
            doc, stats = build_regulation_jsonld(
                title, info, root, is_kov=is_kov, kehtiv=args.kehtiv
            )
        except Exception as e:
            print(f"    FAIL: {e}")
            failed += 1
            run_counts["failedFetches"] += 1
            continue

        status = write_regulation_output(
            out_path, doc, mode=mode, expected_kehtiv=args.kehtiv
        )
        run_counts[status] += 1
        built_docs[out_path] = doc
        for k, v in stats.items():
            totals[k] = totals.get(k, 0) + v

        if args.sleep > 0:
            time.sleep(args.sleep)

    # ---------------------------------------------------------------------
    print("\n[3/3] Writing index...")
    index_path = out_dir / ("REGULATIONS_KOV_INDEX.json" if is_kov else "REGULATIONS_RIIK_INDEX.json")
    removed_from_source = source_removed_files(
        out_dir, set(regs), doc_cache=built_docs
    )
    index_doc = build_regulation_index(
        out_dir,
        is_kov=is_kov,
        kehtiv=args.kehtiv,
        run_stats={
            **source_manifest,
            "generationMode": mode,
            "newlyGenerated": run_counts["newlyGenerated"],
            "existingSkipped": run_counts["existingSkipped"],
            "refreshedStale": run_counts["refreshedStale"],
            "unchanged": run_counts["unchanged"],
            "refreshed": run_counts["refreshed"],
            "forceRewritten": run_counts["forceRewritten"],
            "failedFetches": run_counts["failedFetches"],
            "partialAllowed": args.allow_partial,
            "sourceRemovedFromSnapshotCount": len(removed_from_source),
            "sourceRemovedFromSnapshot": removed_from_source,
        },
        doc_cache=built_docs,
    )
    save_json(index_path, index_doc)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total regulations from API: {len(regs)}")
    print(f"  Generation mode:            {mode}")
    print(f"  Newly generated:            {run_counts['newlyGenerated']}")
    print(f"  Skipped (existing):         {run_counts['existingSkipped']}")
    print(f"  Refreshed stale (missing-only): {run_counts['refreshedStale']}")
    print(f"  Unchanged:                  {run_counts['unchanged']}")
    print(f"  Refreshed:                  {run_counts['refreshed']}")
    print(f"  Force rewritten:            {run_counts['forceRewritten']}")
    print(f"  Source removed from snapshot: {len(removed_from_source)}")
    print(f"  Failed:                     {failed}")
    print(f"  Corpus regulations indexed: {index_doc['totalRegulations']}")
    print(f"  Active regulations:         {index_doc['activeRegulations']}")
    print(f"  Repealed before snapshot:   {index_doc['repealedBeforeSnapshotCount']}")
    print(f"  Corpus paragraphs indexed:  {index_doc['totalParagraphs']}")
    print(f"  Corpus annexes indexed:     {index_doc['totalAnnexes']}")
    print(f"  No-body stubs indexed:      {index_doc['noStructuredBodyCount']}")
    print(f"  Unrecognized issuers:       {sum(UNCLASSIFIED_ISSUERS.values())}")
    for _issuer_name, _issuer_count in UNCLASSIFIED_ISSUERS.most_common():
        print(f"      - {_issuer_name}: {_issuer_count}")
    print(f"  Output directory:           {out_dir}")
    print(f"  Index file:                 {index_path.name}")


if __name__ == "__main__":
    main()
