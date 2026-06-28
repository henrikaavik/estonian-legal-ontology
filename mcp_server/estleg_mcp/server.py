"""FastMCP stdio server exposing the Estonian Legal Ontology as a tool set.

Each tool is a natural-language entry point for a lawmaker: it takes a
plain-language argument (a law name/abbreviation, a paragraph number, a CELEX
number) and returns compact, JSON-serialisable results. Every item that maps to
a source carries its canonical URL (riigiteataja.ee / riigikohus.ee /
eelnoud.valitsus.ee / EUR-Lex), so answers stay grounded in real citations.

The tool docstrings ARE the descriptions the model reads when choosing a tool,
so each one states what it does and gives one example question.

Run with ``estleg-mcp`` (the console script) or ``python -m estleg_mcp.server``.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import data

mcp = FastMCP("estleg")

# Legal text can be very long; cap it so a single provision stays chat-sized.
_MAX_LEGAL_TEXT = 2000


def _truncate(text: str, limit: int = _MAX_LEGAL_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _law_not_found(query: str) -> dict[str, Any]:
    return {
        "note": (
            f"No law matched {query!r}. Try a title (e.g. 'Karistusseadustik'), "
            "an official abbreviation (e.g. 'KarS', 'VÕS'), or use search_laws "
            "to discover the exact name."
        ),
    }


# ---------------------------------------------------------------------------
# 1. search_laws
# ---------------------------------------------------------------------------
@mcp.tool()
def search_laws(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Find Estonian laws by title, official abbreviation, or slug substring.

    Use this first when you are unsure of a law's exact name. Matching is
    accent-insensitive. Each result carries the canonical riigiteataja.ee URL.

    Example question: "Which Estonian laws mention 'töölepingu' / employment?"

    Returns a list of {name, title, abbrev, rt_url, status}.
    """
    results: list[dict[str, Any]] = []
    for rec in data.search_law_records(query, limit=limit):
        graph = data.load_law_graph(rec)
        act = data.act_node(graph)
        results.append(
            {
                "name": rec.name,
                "title": data.act_title(act) or rec.title,
                "abbrev": data.display_abbrev(rec),
                "rt_url": data.rt_url(act),
                "status": data._text(act.get("estleg:temporalStatus")) if act else "",
            }
        )
    return results


# ---------------------------------------------------------------------------
# 2. get_law
# ---------------------------------------------------------------------------
@mcp.tool()
def get_law(law: str, as_of: str | None = None) -> dict[str, Any]:
    """Get an overview of one Estonian law, optionally as it stood on a past date.

    Accepts a title, abbreviation (KarS, VÕS, PKS, ...), or slug. Returns the
    canonical riigiteataja.ee URL, the in-force status, the EuroVoc subject
    IRIs, and counts of provisions and chapters. Pass ``as_of`` as an ISO date
    (e.g. "2015-01-01") to add a point-in-time snapshot: the tool echoes the
    date and reports how many sections had a redaction in force then (from the
    provision-version layer). Pair it with ``get_provision(as_of=...)`` /
    ``provision_history`` to read the actual historical text.

    Example question: "Give me an overview of the Penal Code (Karistusseadustik)."

    Returns {title, abbrev, status, consolidated_as_of, rt_url,
    eurovoc_subjects, num_provisions, num_chapters}. ``status`` is the
    ontology's ``temporalStatus`` (may be "unknown" for some laws);
    ``consolidated_as_of`` is the date of the consolidated text captured. With
    ``as_of`` it additionally carries {as_of, num_provisions_as_of}; the
    act-level metadata (status, subjects, chapters) still reflects the current
    consolidated act, since only the per-provision text layer is historical. An
    ``as_of`` that is not a date, or that falls outside the recorded version
    history, yields a {note}.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found(law)
    graph = data.load_law_graph(rec)
    act = data.act_node(graph)
    subjects = data._ids_of(act.get("dcterms:subject")) if act else []
    result: dict[str, Any] = {
        "title": data.act_title(act) or rec.title,
        "abbrev": data.display_abbrev(rec),
        "status": data._text(act.get("estleg:temporalStatus")) if act else "",
        "consolidated_as_of": data.act_kehtiv_date(act),
        "rt_url": data.rt_url(act),
        "eurovoc_subjects": subjects,
        "num_provisions": len(data.provision_nodes(graph)),
        "num_chapters": len(data.chapter_nodes(graph)),
    }
    if as_of is None:
        return result
    return _law_as_of(rec, result, as_of)


def _law_as_of(
    rec: data.LawRecord, result: dict[str, Any], as_of: str
) -> dict[str, Any]:
    """Add a point-in-time snapshot ({as_of, num_provisions_as_of}) to a law.

    Returns a {note} when ``as_of`` is not an ISO date, when the law has no
    recorded version history, or when no section was in force on that date
    (before the law's earliest redaction or after a full repeal) -- matching the
    get_provision contract, so the caller never gets a misleading "current"
    overview presented as historical.
    """
    iso = data.normalize_iso_date(as_of)
    if iso is None:
        return {"note": f"as_of must be an ISO date like '2015-01-01'; got {as_of!r}."}
    index = data.law_version_index(rec)
    if not index:
        return {
            "note": (
                f"No version history is recorded for {rec.title}; omit as_of for "
                "the current consolidated overview."
            ),
        }
    in_force = data.count_provisions_in_force(index, iso)
    if in_force == 0:
        earliest, latest = data.version_coverage_span(index)
        return {
            "note": (
                f"No provisions of {rec.title} were in force on {iso}; recorded "
                f"history spans {earliest}..{latest}."
            ),
        }
    result["as_of"] = iso
    result["num_provisions_as_of"] = in_force
    return result


# ---------------------------------------------------------------------------
# 3. get_provision
# ---------------------------------------------------------------------------
@mcp.tool()
def get_provision(
    law: str, paragraph: str, as_of: str | None = None
) -> dict[str, Any]:
    """Read a single section (§) of an Estonian law, optionally as of a past date.

    Give the law (title/abbreviation/slug) and a paragraph reference. The
    paragraph accepts "§ 13", "13", "§13.", or a superscripted "§ 22¹". Without
    ``as_of`` you get the current consolidated text. Pass ``as_of`` as an ISO
    date (e.g. "2010-06-15") to read the § exactly as it stood on that date: the
    tool selects the historical redaction whose validity window contains the
    date and reports its redaction id and window. Legal text is truncated to
    ~2000 characters; the rt_url points to the full act on riigiteataja.ee.

    Example question: "What did § 13 of the Penal Code (KarS) say on 2010-06-15?"

    Returns {id, paragrahv, label, summary, legal_text, rt_url}; with ``as_of``
    it additionally carries {as_of, redaction_id, valid_from, valid_to,
    in_force} (``valid_to`` is null for the redaction still in force). A missing
    law/§, or an ``as_of`` outside the recorded version history, yields a {note}.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found(law)
    graph = data.load_law_graph(rec)
    node = data.find_provision(graph, paragraph)
    if node is None:
        return {
            "note": (
                f"No § matching {paragraph!r} found in {rec.title}. "
                "Use get_law to see how many provisions exist."
            ),
        }
    result: dict[str, Any] = {
        "id": node.get("@id", ""),
        "paragrahv": data.clean_display(data._text(node.get("estleg:paragrahv"))),
        "label": data.clean_display(data._text(node.get("rdfs:label"))),
        "summary": data.clean_display(data._text(node.get("estleg:summary"))),
        "rt_url": data.rt_url(data.act_node(graph)),
    }
    if as_of is None:
        result["legal_text"] = _truncate(
            data.clean_display(data._text(node.get("estleg:legalText")))
        )
        return result
    return _provision_as_of(rec, node, result, as_of)


def _provision_as_of(
    rec: data.LawRecord,
    node: dict[str, Any],
    result: dict[str, Any],
    as_of: str,
) -> dict[str, Any]:
    """Fill ``result`` with the historical redaction in force on ``as_of``.

    Returns a {note} when ``as_of`` is not an ISO date or falls outside the
    provision's recorded version history (before the first redaction or after a
    repeal), so the caller never gets a misleadingly "current" text.
    """
    iso = data.normalize_iso_date(as_of)
    if iso is None:
        return {"note": f"as_of must be an ISO date like '2015-01-01'; got {as_of!r}."}
    timeline = data.provision_version_timeline(rec, node.get("@id", ""))
    chosen = data.version_in_force_on(timeline, iso)
    if chosen is None:
        if timeline:
            note = (
                f"No redaction of {result['paragrahv'] or 'that §'} of {rec.title} "
                f"was in force on {iso}; recorded history spans "
                f"{timeline[0]['valid_from']}..{timeline[-1]['valid_to'] or 'present'}."
            )
        else:
            note = (
                f"No version history is recorded for that § of {rec.title}; "
                "omit as_of for the current consolidated text."
            )
        return {"note": note}
    result["legal_text"] = _truncate(data.clean_display(chosen["text"]))
    result["as_of"] = iso
    result["redaction_id"] = chosen["redaction_id"]
    result["valid_from"] = chosen["valid_from"]
    result["valid_to"] = chosen["valid_to"] or None
    result["in_force"] = not chosen["valid_to"]
    return result


# ---------------------------------------------------------------------------
# Shared helper for the two reference directions
# ---------------------------------------------------------------------------
def _reference_items(
    law: str, paragraph: str | None, predicate: str
) -> list[dict[str, Any]]:
    rec = data.resolve_law(law)
    if rec is None:
        return []
    graph = data.load_law_graph(rec)
    act = data.act_node(graph)
    rt = data.rt_url(act)

    # Build a local id -> label map for same-law resolution.
    local_label: dict[str, str] = {}
    for node in data.provision_nodes(graph):
        nid = node.get("@id")
        if isinstance(nid, str):
            local_label[nid] = data.provision_label(node)

    if paragraph:
        prov = data.find_provision(graph, paragraph)
        sources = [prov] if prov is not None else []
    else:
        sources = data.provision_nodes(graph)

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for src in sources:
        for ref in data._ids_of(src.get(predicate)):
            if ref in seen:
                continue
            seen.add(ref)
            ref_slug = data._law_slug_from_iri(ref)
            same_law = ref_slug == rec.name
            if same_law:
                source_law = rec.title
                ref_url = rt
            elif ref_slug:
                ref_rec = data._records_by_slug().get(ref_slug)
                source_law = ref_rec.title if ref_rec else ""
                ref_url = data.rt_url_for_slug(ref_slug)
            else:
                source_law = ""
                ref_url = ""
            items.append(
                {
                    "source_id": ref,
                    "source_label": local_label.get(ref, ""),
                    "source_law": source_law,
                    "rt_url": ref_url,
                }
            )
    return items


# ---------------------------------------------------------------------------
# 4. who_references
# ---------------------------------------------------------------------------
@mcp.tool()
def who_references(law: str, paragraph: str | None = None) -> list[dict[str, Any]]:
    """Find what cites a law or section (incoming references = legal impact).

    This is the impact lens: which provisions point AT the target. Pass a
    paragraph to scope to one §, or omit it for the whole law. Returns an empty
    list when nothing references it.

    Example question: "Which provisions reference § 60 of the Penal Code (KarS)?"

    Returns a list of {source_id, source_label, source_law, rt_url}.
    """
    return _reference_items(law, paragraph, "estleg:referencedBy")


# ---------------------------------------------------------------------------
# 5. references_of
# ---------------------------------------------------------------------------
@mcp.tool()
def references_of(law: str, paragraph: str | None = None) -> list[dict[str, Any]]:
    """Find what a law or section cites (outgoing references).

    The outgoing lens: which other provisions the target points to. Pass a
    paragraph to scope to one §, or omit it for the whole law. Returns an empty
    list when it references nothing.

    Example question: "What does § 13 of the Penal Code (KarS) reference?"

    Returns a list of {source_id, source_label, source_law, rt_url}.
    """
    return _reference_items(law, paragraph, "estleg:references")


# ---------------------------------------------------------------------------
# 6. drafts_affecting_law
# ---------------------------------------------------------------------------
@mcp.tool()
def drafts_affecting_law(law: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find pending draft legislation (eelnõud) that would change a law.

    The pending-legislation radar: which bills currently in the legislative
    pipeline propose to amend the target law. Each item links to the draft in
    EIS (eelnoud.valitsus.ee). Returns an empty list when no drafts affect it.

    Example question: "What pending bills affect the Health Services Organisation
    Act (Tervishoiuteenuste korraldamise seadus)?"

    Returns a list of {title, eis_number, phase, link}.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return []
    graph = data.load_law_graph(rec)

    # Collect draft IRIs from two link shapes, in node order, de-duplicated:
    #   * ``estleg:affectedBy`` -> a Draft IRI directly.
    #   * ``estleg:hasProposedAmendment`` -> a ProposedAmendment *link* IRI
    #     whose ``estleg:amendingDraft`` (in the amendments sidecar) is the
    #     Draft IRI. Most of the corpus uses this shape, so reading only
    #     ``affectedBy`` would miss the laws that have no affectedBy fallback.
    link_to_draft = data.amendment_link_drafts(rec)
    draft_iris: list[str] = []
    seen: set[str] = set()

    def _add(iri: str) -> None:
        if iri and iri not in seen:
            seen.add(iri)
            draft_iris.append(iri)

    for node in graph:
        for ref in data._ids_of(node.get("estleg:affectedBy")):
            _add(ref)
        for link in data._ids_of(node.get("estleg:hasProposedAmendment")):
            # Fall back to the raw link IRI if the sidecar can't resolve it, so
            # an unresolved link is surfaced rather than silently dropped.
            _add(link_to_draft.get(link, link))

    cap = max(0, int(limit))
    items: list[dict[str, Any]] = []
    for iri in draft_iris:
        if len(items) >= cap:
            break
        draft = data.draft_info(iri)
        if draft is None:
            # Draft IRI present on the law but not resolvable in the eelnoud
            # peeps (id-scheme drift); still surface it rather than dropping it.
            items.append(
                {
                    "title": "",
                    "eis_number": iri.removeprefix("estleg:Draft_"),
                    "phase": "",
                    "link": "",
                    "note": "draft not found in eelnoud subcorpus",
                }
            )
        else:
            items.append(
                {
                    "title": data._text(draft.get("rdfs:label")),
                    "eis_number": data._text(draft.get("estleg:eisNumber")),
                    "phase": data._phase_label(draft),
                    "link": data._text(draft.get("estleg:eisLink")),
                }
            )
    return items


# ---------------------------------------------------------------------------
# 7. court_decisions_for_law
# ---------------------------------------------------------------------------
@mcp.tool()
def court_decisions_for_law(law: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find Supreme Court (Riigikohus) decisions interpreting a law.

    The case-law lens: which Riigikohus decisions interpret provisions of the
    target law. Each item links to the full decision on riigikohus.ee. Returns
    an empty list when no decisions are linked.

    Example question: "Which Supreme Court cases interpret the Penal Code (KarS)?"

    Returns a list of {case_number, label, decision_link}.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return []
    graph = data.load_law_graph(rec)

    decision_iris: list[str] = []
    seen: set[str] = set()
    for node in data.provision_nodes(graph):
        for ref in data._ids_of(node.get("estleg:interpretedBy")):
            if ref not in seen:
                seen.add(ref)
                decision_iris.append(ref)

    cap = max(0, int(limit))
    items: list[dict[str, Any]] = []
    for iri in decision_iris:
        if len(items) >= cap:
            break
        dec = data.court_decision(iri)
        if dec is None:
            continue
        items.append(
            {
                "case_number": data._text(dec.get("estleg:caseNumber")),
                "label": data._text(dec.get("rdfs:label")),
                "decision_link": data._text(dec.get("estleg:decisionLink")),
            }
        )
    return items


# ---------------------------------------------------------------------------
# 8. sanctions_for_law
# ---------------------------------------------------------------------------
@mcp.tool()
def sanctions_for_law(law: str) -> list[dict[str, Any]]:
    """List the penalties / sanctions defined by a law.

    The enforcement-teeth lens: imprisonment, fines, and other penalties
    attached to the law's provisions, with the § that imposes each. Each item
    carries the riigiteataja.ee URL of the act. Returns an empty list when the
    law defines no sanctions.

    Example question: "What penalties does the Penal Code (KarS) define?"

    Returns a list of {provision, sanction_type, penalty, rt_url}.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return []
    rt = data.rt_url(data.act_node(data.load_law_graph(rec)))
    items: list[dict[str, Any]] = []
    for node in data._sanction_graph_for(rec):
        if "estleg:Sanction" not in data._types_of(node):
            continue
        min_p = data._text(node.get("estleg:minPenalty"))
        max_p = data._text(node.get("estleg:maxPenalty"))
        if min_p and max_p:
            penalty = f"{min_p} to {max_p}"
        else:
            penalty = max_p or min_p
        items.append(
            {
                "provision": data._id_of(node.get("estleg:applicableProvision")),
                "sanction_type": data._text(node.get("estleg:sanctionType")),
                "penalty": penalty,
                "rt_url": rt,
            }
        )
    return items


# ---------------------------------------------------------------------------
# 9. competent_authority_for_law
# ---------------------------------------------------------------------------
@mcp.tool()
def competent_authority_for_law(law: str) -> list[dict[str, Any]]:
    """Find which state institutions enforce / administer a law.

    The who-is-in-charge lens: the institutions named as the competent
    authority on the law's provisions, with how many provisions each one
    covers. Returns an empty list when no competence links exist.

    Example question: "Which authority enforces the Personal Data Protection Act
    (Isikuandmete kaitse seadus)?"

    Returns a list of {institution, provision_count}, most-cited first.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return []
    graph = data.load_law_graph(rec)
    counts: dict[str, int] = {}
    for node in data.provision_nodes(graph):
        for iri in data._ids_of(node.get("estleg:competentAuthority")):
            counts[iri] = counts.get(iri, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {
            "institution": data.institution_label(iri),
            "provision_count": count,
        }
        for iri, count in ranked
    ]


# ---------------------------------------------------------------------------
# 10. transposition
# ---------------------------------------------------------------------------
@mcp.tool()
def transposition(query: str) -> list[dict[str, Any]]:
    """Map EU directives to the Estonian laws that transpose them (both ways).

    The EU-link lens, bidirectional: pass a CELEX number (e.g. "31990L0314") to
    find the transposing Estonian law(s), OR pass a law name/abbreviation
    (e.g. "Võlaõigusseadus" / "VÕS") to find the EU directive(s) it transposes.
    Each item carries the EUR-Lex URL for the directive.

    Example question: "Which Estonian law transposes EU directive 31990L0314?"

    Returns a list of {directive_celex, eurlex_url, national_title,
    matched_law_name}.
    """
    items: list[dict[str, Any]] = []
    for row in data.transposition_matches(query):
        celex = data._text(row.get("directive_celex"))
        items.append(
            {
                "directive_celex": celex,
                "eurlex_url": data.eurlex_url(celex),
                "national_title": data._text(row.get("national_title")),
                "matched_law_name": data._text(row.get("matched_law_name")),
            }
        )
    return items


# ---------------------------------------------------------------------------
# 11. provision_history (point-in-time, ticket #499)
# ---------------------------------------------------------------------------
@mcp.tool()
def provision_history(law: str, paragraph: str) -> list[dict[str, Any]]:
    """Show the full redaction timeline of one § (point-in-time history).

    The version-history lens: every recorded redaction of a section, oldest
    first, with the date window each was in force and its text. Pair it with
    ``get_provision(as_of=...)`` to read any single past redaction in full.
    Returns an empty list when the law/§ is unknown or has no recorded history.

    Example question: "How has § 13 of the Penal Code (KarS) changed over time?"

    Returns a list of {redaction_id, valid_from, valid_to, in_force, text},
    ordered by valid_from. ``valid_to`` is null for the redaction still in force;
    each ``text`` is truncated to ~2000 characters to stay chat-sized.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return []
    graph = data.load_law_graph(rec)
    node = data.find_provision(graph, paragraph)
    if node is None:
        return []
    return [
        {
            "redaction_id": v["redaction_id"],
            "valid_from": v["valid_from"],
            "valid_to": v["valid_to"] or None,
            "in_force": not v["valid_to"],
            "text": _truncate(data.clean_display(v["text"])),
        }
        for v in data.provision_version_timeline(rec, node.get("@id", ""))
    ]


# ---------------------------------------------------------------------------
# Shared cap helper for the (potentially huge) regulation list tools
# ---------------------------------------------------------------------------
def _capped(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Cap a result list to ``limit`` items, flagging an overflow when truncated.

    Mirrors the hard-cap pattern of search_laws / court_decisions_for_law, but --
    because one statute or issuer can have hundreds/thousands of regulations --
    appends a final {note, overflow, total_available} entry when the list was cut,
    so the caller knows more exist. ``limit`` <= 0 yields an empty list (matching
    the other list tools).
    """
    cap = max(0, int(limit))
    out = rows[:cap]
    if cap and len(rows) > cap:
        out = out + [
            {
                "note": (
                    f"showing first {cap} of {len(rows)} matches; raise `limit` "
                    "or narrow the query to see more."
                ),
                "overflow": True,
                "total_available": len(rows),
            }
        ]
    return out


def _regulation_row(r: data.RegulationRecord) -> dict[str, Any]:
    """Compact list-row for a regulation (issuer + status + citation + rt_url)."""
    return {
        "reg_id": r.reg_id or "",
        "title": r.title or "",
        "issuer": r.issuer or "",
        "status": r.status or "",
        "rt_url": r.rt_url or "",
        "citations": list(r.citations or []),
        "is_kov": bool(r.is_kov),
        "municipality": r.municipality or "",
    }


# ---------------------------------------------------------------------------
# 12. regulations_for_law (ticket #500)
# ---------------------------------------------------------------------------
@mcp.tool()
def regulations_for_law(law: str, limit: int = 50) -> list[dict[str, Any]]:
    """Find the regulations (määrused) issued under / implementing a statute.

    The delegated-legislation lens: state and municipal regulations whose
    ``issuedUnder`` / ``implementsCitation`` points at the target law -- the
    secondary legislation enacted on its authority. Each row carries the
    regulation's riigiteataja.ee URL and the statutory citation text(s) it
    implements. Capped by ``limit`` (a major enabling act has thousands); returns
    an empty list when none are issued under it.

    Example question: "Which regulations are issued under the Local Government
    Organisation Act (KOKS)?"

    Returns a list of {reg_id, title, issuer, status, rt_url, citations, is_kov,
    municipality}. When more than ``limit`` match, a trailing {note, overflow,
    total_available} entry signals the truncation.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return []
    rows = [_regulation_row(r) for r in data.regulations_for_law(rec.name)]
    return _capped(rows, limit)


# ---------------------------------------------------------------------------
# 13. get_regulation (ticket #500)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_regulation(name: str) -> dict[str, Any]:
    """Get an overview of one regulation (määrus) by title, slug, or id.

    Accepts a regulation title, its corpus slug, or its riigiteataja id
    (terviktekst or global id). Returns the issuer, in-force status, the
    statute(s) it is issued under (each with its own riigiteataja URL), the
    regulation's own riigiteataja URL, and how many sections it has.

    Example question: "Give me an overview of the regulation 'Abikõlblike kulude
    määramise üldised tingimused ja kord'."

    Returns {reg_id, title, issuer, status, rt_url, num_provisions, is_kov,
    municipality, issued_under}, where issued_under is a list of
    {law, slug, rt_url}. An unknown regulation yields a {note}.
    """
    rec = data.resolve_regulation(name)
    if rec is None:
        return {
            "note": (
                f"No regulation matched {name!r}. Try its exact title, corpus "
                "slug, or riigiteataja id, or use regulations_for_law / "
                "regulations_by_issuer to discover one."
            ),
        }
    issued_under: list[dict[str, Any]] = []
    seen: set[str] = set()
    for iri in rec.issued_under:
        slug = data._law_slug_from_issued_under(iri)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        parent = data._records_by_slug().get(slug)
        issued_under.append(
            {
                "law": parent.title if parent else data._title_from_slug(slug),
                "slug": slug,
                "rt_url": data.rt_url_for_slug(slug),
            }
        )
    return {
        "reg_id": rec.reg_id or "",
        "title": rec.title or "",
        "issuer": rec.issuer or "",
        "status": rec.status or "",
        "rt_url": rec.rt_url or "",
        "num_provisions": rec.num_provisions or 0,
        "is_kov": bool(rec.is_kov),
        "municipality": rec.municipality or "",
        "issued_under": issued_under,
    }


# ---------------------------------------------------------------------------
# 14. regulations_by_issuer (ticket #500)
# ---------------------------------------------------------------------------
@mcp.tool()
def regulations_by_issuer(institution: str, limit: int = 50) -> list[dict[str, Any]]:
    """List the regulations (määrused) issued by an institution.

    The issuer lens: every regulation enacted by a given body -- a ministry
    ("Rahandusminister"), the government ("Vabariigi Valitsus"), or a municipal
    council ("Tartu Linnavolikogu"). Matching prefers the exact institution name
    and falls back to a substring search. Capped by ``limit`` (the government
    alone has hundreds), with an overflow note when truncated; returns an empty
    list when the institution issues none.

    Example question: "Which regulations has the Minister of Finance
    (Rahandusminister) issued?"

    Returns a list of {reg_id, title, issuer, status, rt_url}. When more than
    ``limit`` match, a trailing {note, overflow, total_available} entry signals
    the truncation.
    """
    rows = [
        {
            "reg_id": r.reg_id or "",
            "title": r.title or "",
            "issuer": r.issuer or "",
            "status": r.status or "",
            "rt_url": r.rt_url or "",
        }
        for r in data.regulations_by_issuer(institution)
    ]
    return _capped(rows, limit)


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection policy for the streamable-HTTP transport.

    MCP validates the incoming ``Host`` header against an allow-list. With the
    default empty list and protection enabled, the transport answers every
    request from a non-localhost host with ``421 Invalid Host header`` -- so a
    deployment behind a reverse proxy (e.g. ``estleg.sixtyfour.ee``) is
    unreachable. We make the policy explicit instead of relying on the
    version-dependent default:

    * If ``ESTLEG_ALLOWED_HOSTS`` is set (comma-separated ``host`` or
      ``host:port`` values; ``host:*`` matches any port), enable protection
      scoped to that allow-list, plus any ``ESTLEG_ALLOWED_ORIGINS``.
    * Otherwise disable it, so the endpoint is reachable behind a trusted proxy
      that terminates the public host; the bearer token (``ESTLEG_TOKEN``) is
      the actual access gate for this public-law data.
    """

    def _csv(name: str) -> list[str]:
        return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]

    hosts = _csv("ESTLEG_ALLOWED_HOSTS")
    if hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=_csv("ESTLEG_ALLOWED_ORIGINS"),
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _build_http_app():
    """Build the Starlette app for the streamable-HTTP transport.

    Adds an unauthenticated ``/healthz`` (for the container/proxy health
    check) and, when ``ESTLEG_TOKEN`` is set, a bearer-token gate on every
    other path. The ontology is public law, so the token is about preventing
    anonymous abuse of a public endpoint, not protecting secret data.
    """
    # Apply the deployment config to FastMCP's own settings BEFORE the app and
    # its session manager are built (streamable_http_app() reads
    # ``settings.transport_security`` when it lazily creates the manager).
    # Previously only uvicorn received ESTLEG_HOST, leaving FastMCP on its
    # localhost defaults and the transport rejecting the proxied Host header.
    mcp.settings.host = os.environ.get("ESTLEG_HOST", "127.0.0.1")
    mcp.settings.port = int(os.environ.get("ESTLEG_PORT", "8000"))
    mcp.settings.transport_security = _transport_security()
    app = mcp.streamable_http_app()  # serves MCP at ``/mcp`` with its lifespan

    from starlette.requests import Request
    from starlette.responses import JSONResponse, PlainTextResponse

    async def _health(_req: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app.add_route("/healthz", _health, methods=["GET"])

    token = os.environ.get("ESTLEG_TOKEN", "").strip()
    if token:
        from starlette.middleware.base import BaseHTTPMiddleware

        class _BearerAuth(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):  # type: ignore[override]
                if request.url.path == "/healthz":
                    return await call_next(request)
                header = request.headers.get("authorization", "")
                expected = f"Bearer {token}"
                # constant-time compare to avoid leaking the token by timing
                if not hmac.compare_digest(header, expected):
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
                return await call_next(request)

        app.add_middleware(_BearerAuth)
    return app


def main() -> None:
    """Run the estleg MCP server.

    Transport is chosen by ``ESTLEG_TRANSPORT`` (default ``stdio`` for local
    IDE clients). Set it to ``http`` to serve the streamable-HTTP transport for
    a remote deployment; ``ESTLEG_HOST`` (default 127.0.0.1; use 0.0.0.0 in a
    container), ``ESTLEG_PORT`` (default 8000), and ``ESTLEG_TOKEN`` (bearer
    token; unset = open) tune it. Behind a reverse proxy, set
    ``ESTLEG_ALLOWED_HOSTS`` to the public host(s) to re-enable DNS-rebinding
    protection (see :func:`_transport_security`).
    """
    transport = os.environ.get("ESTLEG_TRANSPORT", "stdio").strip().lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        import uvicorn

        # _build_http_app() resolves ESTLEG_HOST/ESTLEG_PORT onto mcp.settings;
        # bind uvicorn to the same values so there is a single source of truth.
        app = _build_http_app()
        uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port)
    else:
        mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
