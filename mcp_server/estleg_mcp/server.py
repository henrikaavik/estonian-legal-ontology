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
def get_law(law: str) -> dict[str, Any]:
    """Get an overview of one Estonian law: title, status, scope, and citation.

    Accepts a title, abbreviation (KarS, VÕS, PKS, ...), or slug. Returns the
    canonical riigiteataja.ee URL, the in-force status, the EuroVoc subject
    IRIs, and counts of provisions and chapters.

    Example question: "Give me an overview of the Penal Code (Karistusseadustik)."

    Returns {title, abbrev, status, consolidated_as_of, rt_url,
    eurovoc_subjects, num_provisions, num_chapters}. ``status`` is the
    ontology's ``temporalStatus`` (may be "unknown" for some laws);
    ``consolidated_as_of`` is the date of the consolidated text captured.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found(law)
    graph = data.load_law_graph(rec)
    act = data.act_node(graph)
    subjects = data._ids_of(act.get("dcterms:subject")) if act else []
    return {
        "title": data.act_title(act) or rec.title,
        "abbrev": data.display_abbrev(rec),
        "status": data._text(act.get("estleg:temporalStatus")) if act else "",
        "consolidated_as_of": data.act_kehtiv_date(act),
        "rt_url": data.rt_url(act),
        "eurovoc_subjects": subjects,
        "num_provisions": len(data.provision_nodes(graph)),
        "num_chapters": len(data.chapter_nodes(graph)),
    }


# ---------------------------------------------------------------------------
# 3. get_provision
# ---------------------------------------------------------------------------
@mcp.tool()
def get_provision(law: str, paragraph: str) -> dict[str, Any]:
    """Read a single section (§) of an Estonian law in full.

    Give the law (title/abbreviation/slug) and a paragraph reference. The
    paragraph accepts "§ 13", "13", "§13.", or a superscripted "§ 22¹". The
    legal text is truncated to ~2000 characters; the rt_url points to the full
    act on riigiteataja.ee.

    Example question: "What does § 13 of the Penal Code (KarS) say?"

    Returns {id, paragrahv, label, summary, legal_text, rt_url}.
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
    return {
        "id": node.get("@id", ""),
        "paragrahv": data.clean_display(data._text(node.get("estleg:paragrahv"))),
        "label": data.clean_display(data._text(node.get("rdfs:label"))),
        "summary": data.clean_display(data._text(node.get("estleg:summary"))),
        "legal_text": _truncate(
            data.clean_display(data._text(node.get("estleg:legalText")))
        ),
        "rt_url": data.rt_url(data.act_node(graph)),
    }


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


def _build_http_app():
    """Build the Starlette app for the streamable-HTTP transport.

    Adds an unauthenticated ``/healthz`` (for the container/proxy health
    check) and, when ``ESTLEG_TOKEN`` is set, a bearer-token gate on every
    other path. The ontology is public law, so the token is about preventing
    anonymous abuse of a public endpoint, not protecting secret data.
    """
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
    token; unset = open) tune it.
    """
    transport = os.environ.get("ESTLEG_TRANSPORT", "stdio").strip().lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        import uvicorn

        host = os.environ.get("ESTLEG_HOST", "127.0.0.1")
        port = int(os.environ.get("ESTLEG_PORT", "8000"))
        uvicorn.run(_build_http_app(), host=host, port=port)
    else:
        mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
