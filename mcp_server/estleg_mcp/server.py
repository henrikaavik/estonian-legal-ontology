"""FastMCP stdio server exposing the Estonian Legal Ontology as a tool set.

Each tool is a natural-language entry point for a lawmaker: it takes a
plain-language argument (a law name/abbreviation, a paragraph number, a CELEX
number) and returns compact, JSON-serialisable results. Every item that maps to
a source carries its canonical URL (riigiteataja.ee / riigikohus.ee /
eelnoud.valitsus.ee / EUR-Lex), so answers stay grounded in real citations.

The tool descriptions the model reads are Estonian-first (#714): an Estonian
lead paragraph from :mod:`estleg_mcp.i18n`, then the English docstring below,
which states the field contract and gives one example question.

Two layers per tool (#714):

* The plain Python function (``server.get_law`` ...) returns the bare payload
  -- a dict or a list -- and is what the in-process tests call.
* What MCP clients call is a wrapper built by :func:`_wire`: it adds the
  ``language`` argument (``et`` default / ``en``), runs the tool off the event
  loop, attaches the ``snapshot`` envelope (corpus commit / release tag,
  ontology version, evaluation date, server version) to every result, and
  writes one JSON audit line per call (:mod:`estleg_mcp.audit`). A dict
  result gains a top-level ``snapshot`` key; a list result is returned as
  ``{"result": [...], "snapshot": {...}}`` (the ``result`` key is where the
  SDK already put list results, so structured consumers keep reading it).

Run with ``estleg-mcp`` (the console script) or ``python -m estleg_mcp.server``.
"""

from __future__ import annotations

import functools
import inspect
import os
import sys
import time
import typing
from collections.abc import Callable
from typing import Annotated, Any, Literal

import anyio
from pydantic import Field

try:
    from mcp.server.mcpserver import Context, MCPServer
except ModuleNotFoundError as exc:
    if exc.name != "mcp.server.mcpserver":
        raise
    from mcp.server.fastmcp import Context
    from mcp.server.fastmcp import FastMCP as MCPServer

    _MCP_V2 = False
else:
    _MCP_V2 = True
from mcp.server.transport_security import TransportSecuritySettings

from . import audit, data, i18n, provenance, resolver_web, security

mcp = MCPServer("estleg")

# Legal text can be very long; cap it so a single provision stays chat-sized.
# Every cut is explicit (#714): the result says ``truncated`` and the
# ``full_length`` of the uncut text, and ``full_text=True`` lifts the cap.
_MAX_LEGAL_TEXT = 2000

# The tool registry: name -> the plain function. ``@tool`` fills it and
# registers the wired wrapper on ``mcp``; :func:`register_tools` re-registers
# the same set on another server instance (tests, embedding).
TOOLS: dict[str, Callable[..., Any]] = {}


def _truncate(text: str, limit: int = _MAX_LEGAL_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _cut(text: str, full_text: bool = False) -> tuple[str, bool, int]:
    """``(shown_text, truncated, full_length)`` for a legal-text field (#714)."""
    shown = text if full_text else _truncate(text)
    return shown, shown != text, len(text)


def _law_not_found(query: str) -> dict[str, Any]:
    return {"note": i18n.msg("law_not_found_detail", query=query)}


def _law_not_found_list(query: str) -> list[dict[str, Any]]:
    """List-tool miss: unknown target, same envelope as overflow note objects."""
    return [{"note": i18n.msg("law_not_found", query=query)}]


# ---------------------------------------------------------------------------
# Wire layer: language, snapshot envelope, audit line (#714)
# ---------------------------------------------------------------------------
LOCAL_CONSUMER = "local"
_LanguageArg = Annotated[Literal["et", "en"], Field(description=i18n.LANGUAGE_PARAM_DESCRIPTION)]


def _ctx_request(ctx: Any) -> Any:
    """The Starlette request behind an MCP call (``None`` on stdio)."""
    if ctx is None:
        return None
    try:
        request_context = ctx.request_context
    except (AttributeError, ValueError, LookupError):
        return None
    return getattr(request_context, "request", None)


def _consumer_for(request: Any) -> str:
    """Who is calling: the token-map consumer on HTTP, ``local`` on stdio.

    The HTTP name is the one :class:`security.AccessMiddleware` authenticated
    and stored in the ASGI scope state -- never a client-supplied header.
    """
    if request is None:
        return LOCAL_CONSUMER
    scope = getattr(request, "scope", None)
    state = scope.get("state") if isinstance(scope, dict) else None
    consumer = state.get(security.CONSUMER_STATE_KEY) if isinstance(state, dict) else None
    return consumer if isinstance(consumer, str) and consumer else "unknown"


def _envelope(payload: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Attach the snapshot: a dict gains a key, a list goes under ``result``."""
    if isinstance(payload, dict):
        return {**payload, "snapshot": snapshot}
    return {"result": payload, "snapshot": snapshot}


def _call_in_language(fn: Callable[..., Any], arguments: dict[str, Any], language: str) -> Any:
    with i18n.use_language(language):
        return fn(**arguments)


async def _invoke(
    name: str,
    fn: Callable[..., Any],
    arguments: dict[str, Any],
    language: str | None,
    ctx: Any,
) -> dict[str, Any]:
    """Run one tool call: language, worker thread, envelope, audit line."""
    started = time.perf_counter()
    request = _ctx_request(ctx)
    log = audit.get_audit_log()
    record: dict[str, Any] = {
        "ts": audit.utc_timestamp(),
        "event": "tool_call",
        "consumer": _consumer_for(request),
        "transport": "stdio" if request is None else "http",
        "tool": name,
        "args_sha256": log.hash_arguments({**arguments, "language": language}),
    }

    def finish(**fields: Any) -> None:
        record.update(fields)
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        record.update(provenance.corpus_identity())
        record["server_version"] = provenance.SERVER_VERSION
        log.emit(record)

    try:
        lang = i18n.normalize_language(language)
        # Off the event loop: the first call into a cold index (regulations,
        # court decisions) reads thousands of files, and one consumer's slow
        # query must not stall every other HTTP session.
        payload = await anyio.to_thread.run_sync(
            functools.partial(_call_in_language, fn, arguments, lang)
        )
    except Exception as exc:
        finish(status="error", error=type(exc).__name__, result_bytes=0, truncated=False)
        raise
    result = _envelope(payload, provenance.snapshot(lang))
    finish(
        status="ok",
        result_bytes=len(audit.canonical_json(result).encode("utf-8")),
        truncated=audit.is_truncated(payload),
    )
    return result


def _wire(fn: Callable[..., Any]) -> Callable[..., Any]:
    """The client-facing wrapper of a plain tool function (#714).

    Same name and parameters as ``fn`` plus a keyword ``language`` argument and
    an injected MCP ``Context`` (used only to identify the HTTP consumer). The
    signature is built explicitly -- resolved annotations, no ``__wrapped__``
    -- so both SDK majors derive the same input schema and find the context
    parameter.
    """
    name = fn.__name__
    hints = typing.get_type_hints(fn)
    params = [
        p.replace(annotation=hints.get(p.name, p.annotation))
        for p in inspect.signature(fn).parameters.values()
    ]
    params.append(
        inspect.Parameter(
            "language",
            inspect.Parameter.KEYWORD_ONLY,
            default=i18n.DEFAULT_LANGUAGE,
            annotation=_LanguageArg,
        )
    )
    params.append(
        inspect.Parameter(
            "ctx", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Context | None
        )
    )

    async def wired(**kwargs: Any) -> dict[str, Any]:
        ctx = kwargs.pop("ctx", None)
        language = kwargs.pop("language", None)
        return await _invoke(name, fn, kwargs, language, ctx)

    wired.__name__ = name
    wired.__qualname__ = name
    wired.__module__ = fn.__module__
    wired.__doc__ = fn.__doc__
    wired.__signature__ = inspect.Signature(params, return_annotation=dict[str, Any])  # type: ignore[attr-defined]
    wired.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict[str, Any]}
    return wired


def _register(server: Any, fn: Callable[..., Any]) -> None:
    server.add_tool(
        _wire(fn),
        name=fn.__name__,
        description=i18n.tool_description(fn.__name__, fn.__doc__),
    )


def register_tools(server: Any) -> None:
    """Register every estleg tool (wired) on another MCP server instance."""
    for fn in TOOLS.values():
        _register(server, fn)


def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register ``fn`` as an MCP tool (wired, see :func:`_wire`); return it unchanged."""
    TOOLS[fn.__name__] = fn
    _register(mcp, fn)
    return fn


# ---------------------------------------------------------------------------
# 1. search_laws
# ---------------------------------------------------------------------------
@tool
def search_laws(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Find Estonian laws by title, official abbreviation, or slug substring.

    Use this first when you are unsure of a law's exact name. Matching is
    accent-insensitive and expands EuroVoc domain labels (English or Estonian)
    so the other-language label or a domain keyword can also hit. Each result
    carries the canonical riigiteataja.ee URL.

    Example question: "Which Estonian laws mention 'töölepingu' / employment?"

    Returns a list of {name, title, abbrev, rt_url, status, external_ids}.
    ``rt_url`` is a riigiteataja.ee URL or "" (never another host);
    ``external_ids`` carries any non-riigiteataja identifier the act links
    itself to, e.g. {"wikidata": "http://www.wikidata.org/entity/Q2352833"}.
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
                "external_ids": data.external_ids(act),
                "status": data._text(act.get("estleg:temporalStatus")) if act else "",
            }
        )
    return results


# ---------------------------------------------------------------------------
# 2. get_law
# ---------------------------------------------------------------------------
@tool
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
    external_ids, eurovoc_subjects, num_provisions, num_chapters}. ``rt_url``
    is the act's riigiteataja.ee URL, or "" when the ontology records no
    riigiteataja source for it (a handful of acts, including KarS and VÕS);
    ``external_ids`` then still carries what the act does link to, e.g.
    {"wikidata": "http://www.wikidata.org/entity/Q2352833"}. ``status`` is the
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
        "ontology_version": data.ontology_version(),
        "rt_url": data.rt_url(act),
        "external_ids": data.external_ids(act),
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
        return {"note": i18n.msg("bad_date", field="as_of", value=as_of)}
    index = data.law_version_index(rec)
    if not index:
        return {"note": i18n.msg("no_law_history", law=rec.title)}
    in_force = data.count_provisions_in_force(index, iso)
    if in_force == 0:
        earliest, latest = data.version_coverage_span(index)
        if latest == "present":
            latest = i18n.msg("present")
        return {
            "note": i18n.msg(
                "no_provisions_in_force", law=rec.title, date=iso, earliest=earliest, latest=latest
            )
        }
    result["as_of"] = iso
    result["num_provisions_as_of"] = in_force
    return result


# ---------------------------------------------------------------------------
# 3. get_provision
# ---------------------------------------------------------------------------
@tool
def get_provision(
    law: str,
    paragraph: str,
    as_of: str | None = None,
    full_text: bool = False,
) -> dict[str, Any]:
    """Read a single section (§) of an Estonian law, optionally as of a past date.

    Give the law (title/abbreviation/slug) and a paragraph reference. The
    paragraph accepts "§ 13", "13", "§13.", or a superscripted "§ 22¹". Without
    ``as_of`` you get the current consolidated text. Pass ``as_of`` as an ISO
    date (e.g. "2010-06-15") to read the § exactly as it stood on that date: the
    tool selects the historical redaction whose validity window contains the
    date and reports its redaction id and window. Legal text is cut at ~2000
    characters unless ``full_text=True`` (on both the current and the ``as_of``
    path); ``truncated`` says whether it was cut and ``full_length`` gives the
    uncut length. The rt_url points to the full act on riigiteataja.ee (or is
    "" for the few acts whose ontology node records no riigiteataja source).

    Example question: "What did § 13 of the Penal Code (KarS) say on 2010-06-15?"

    Returns {id, paragrahv, label, summary, legal_text, truncated, full_length,
    rt_url}; with ``as_of`` it additionally carries {as_of, redaction_id,
    valid_from, valid_to, currently_in_force, redaction_rt_url}, the last
    being that historical redaction's own riigiteataja.ee URL ("" when the
    corpus records none). The returned text is the redaction that was in force on
    ``as_of``; ``currently_in_force`` is true only when that redaction is still
    the live one today (``valid_to`` is null), so a historical hit is correctly
    false without contradicting that it was in force on the requested date. A
    missing law/§, or an ``as_of`` outside the recorded version history, yields a
    {note}.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found(law)
    graph = data.load_law_graph(rec)
    node = data.find_provision(graph, paragraph)
    if node is None:
        return {"note": i18n.msg("paragraph_not_found", law=rec.title, paragraph=paragraph)}
    result: dict[str, Any] = {
        "id": node.get("@id", ""),
        "paragrahv": data.clean_display(data._text(node.get("estleg:paragrahv"))),
        "label": data.clean_display(data._text(node.get("rdfs:label"))),
        "summary": data.clean_display(data._text(node.get("estleg:summary"))),
        "rt_url": data.rt_url(data.act_node(graph)),
    }
    if as_of is None:
        raw = data.clean_display(data._text(node.get("estleg:legalText")))
        _put_text(result, raw, full_text)
        return result
    return _provision_as_of(rec, node, result, as_of, full_text)


def _put_text(result: dict[str, Any], raw: str, full_text: bool) -> None:
    """Set ``legal_text`` / ``truncated`` / ``full_length`` on ``result``."""
    shown, truncated, full_length = _cut(raw, full_text)
    result["legal_text"] = shown
    result["truncated"] = truncated
    result["full_length"] = full_length


def _provision_as_of(
    rec: data.LawRecord,
    node: dict[str, Any],
    result: dict[str, Any],
    as_of: str,
    full_text: bool = False,
) -> dict[str, Any]:
    """Fill ``result`` with the historical redaction in force on ``as_of``.

    Returns a {note} when ``as_of`` is not an ISO date or falls outside the
    provision's recorded version history (before the first redaction or after a
    repeal), so the caller never gets a misleadingly "current" text.
    """
    iso = data.normalize_iso_date(as_of)
    if iso is None:
        return {"note": i18n.msg("bad_date", field="as_of", value=as_of)}
    timeline = data.provision_version_timeline(rec, node.get("@id", ""))
    chosen = data.version_in_force_on(timeline, iso)
    if chosen is None:
        if timeline:
            note = i18n.msg(
                "no_redaction_on",
                paragraph=result["paragrahv"] or i18n.msg("that_section"),
                law=rec.title,
                date=iso,
                earliest=timeline[0]["valid_from"],
                latest=timeline[-1]["valid_to"] or i18n.msg("present"),
            )
        else:
            note = i18n.msg("no_provision_history", law=rec.title)
        return {"note": note}
    _put_text(result, data.clean_display(chosen["text"]), full_text)
    result["as_of"] = iso
    result["redaction_id"] = chosen["redaction_id"]
    result["valid_from"] = chosen["valid_from"]
    result["valid_to"] = chosen["valid_to"] or None
    # The chosen redaction was, by construction, in force on ``as_of``; this
    # field reports whether it is ALSO the redaction still in force today
    # (open-ended valid_to), not whether it was in force on the queried date.
    result["currently_in_force"] = not chosen["valid_to"]
    # The act citation stays in ``rt_url``; the historical redaction's own
    # riigiteataja.ee URL is the precise citation for this text (#714).
    result["redaction_rt_url"] = chosen.get("rt_url", "")
    return result


# ---------------------------------------------------------------------------
# Shared helper for the two reference directions
# ---------------------------------------------------------------------------
def _reference_items(
    law: str, paragraph: str | None, predicate: str
) -> list[dict[str, Any]]:
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found_list(law)
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
@tool
def who_references(law: str, paragraph: str | None = None) -> list[dict[str, Any]]:
    """Find what cites a law or section (incoming references = legal impact).

    This is the impact lens: which provisions point AT the target. Pass a
    paragraph to scope to one §, or omit it for the whole law. Unknown law
    returns ``[{note}]``; a known law with no incoming references returns ``[]``.

    Example question: "Which provisions reference § 60 of the Penal Code (KarS)?"

    Returns a list of {source_id, source_label, source_law, rt_url}.
    """
    return _reference_items(law, paragraph, "estleg:referencedBy")


# ---------------------------------------------------------------------------
# 5. references_of
# ---------------------------------------------------------------------------
@tool
def references_of(law: str, paragraph: str | None = None) -> list[dict[str, Any]]:
    """Find what a law or section cites (outgoing references).

    The outgoing lens: which other provisions the target points to. Pass a
    paragraph to scope to one §, or omit it for the whole law. Unknown law
    returns ``[{note}]``; a known law that cites nothing returns ``[]``.

    Example question: "What does § 13 of the Penal Code (KarS) reference?"

    Returns a list of {source_id, source_label, source_law, rt_url}.
    """
    return _reference_items(law, paragraph, "estleg:references")


# ---------------------------------------------------------------------------
# 6. drafts_affecting_law
# ---------------------------------------------------------------------------
@tool
def drafts_affecting_law(law: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find pending draft legislation (eelnõud) that would change a law.

    The pending-legislation radar: which bills currently in the legislative
    pipeline propose to amend the target law. Each item links to the draft in
    EIS (eelnoud.valitsus.ee). Unknown law returns ``[{note}]``; a known law
    with no drafts returns ``[]``.

    Example question: "What pending bills affect the Health Services Organisation
    Act (Tervishoiuteenuste korraldamise seadus)?"

    Returns a list of {title, eis_number, phase, link}.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found_list(law)
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
                    "note": i18n.msg("draft_not_found"),
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
@tool
def court_decisions_for_law(law: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find Supreme Court (Riigikohus) decisions interpreting a law.

    The case-law lens: which Riigikohus decisions interpret provisions of the
    target law. Each item links to the full decision on riigikohus.ee. Unknown
    law returns ``[{note}]``; a known law with no linked decisions returns ``[]``.

    Example question: "Which Supreme Court cases interpret the Penal Code (KarS)?"

    Returns a list of {case_number, label, decision_link}.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found_list(law)
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
@tool
def sanctions_for_law(law: str, limit: int = 50) -> list[dict[str, Any]]:
    """List the penalties / sanctions defined by a law.

    The enforcement-teeth lens: imprisonment, fines, and other penalties
    attached to the law's provisions, with the § that imposes each. Each item
    carries the riigiteataja.ee URL of the act, or "" for the few acts whose
    ontology node records no riigiteataja source (KarS is one). Unknown law
    returns
    ``[{note}]``; a known law that defines no sanctions returns ``[]``.
    ``limit`` caps the list (KarS has hundreds).

    Example question: "What penalties does the Penal Code (KarS) define?"

    Returns a list of {provision, sanction_type, penalty, rt_url}.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found_list(law)
    if limit <= 0:
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
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# 9. competent_authority_for_law
# ---------------------------------------------------------------------------
@tool
def competent_authority_for_law(law: str) -> list[dict[str, Any]]:
    """Find which state institutions enforce / administer a law.

    The who-is-in-charge lens: the institutions named as the competent
    authority on the law's provisions, with how many provisions each one
    covers. Unknown law returns ``[{note}]``; a known law with no competence
    links returns ``[]``.

    Example question: "Which authority enforces the Personal Data Protection Act
    (Isikuandmete kaitse seadus)?"

    Returns a list of {institution, institution_id, provision_count, rt_url},
    most-cited first. ``rt_url`` is the law's riigiteataja.ee URL (the
    citation for the competence assignment), or "" when the act records none.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found_list(law)
    graph = data.load_law_graph(rec)
    rt = data.rt_url(data.act_node(graph))
    counts: dict[str, int] = {}
    for node in data.provision_nodes(graph):
        for iri in data._ids_of(node.get("estleg:competentAuthority")):
            counts[iri] = counts.get(iri, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {
            "institution": data.institution_label(iri),
            "institution_id": iri,
            "provision_count": count,
            "rt_url": rt,
        }
        for iri, count in ranked
    ]


# ---------------------------------------------------------------------------
# 10. transposition
# ---------------------------------------------------------------------------
@tool
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
@tool
def provision_history(
    law: str, paragraph: str, full_text: bool = False
) -> list[dict[str, Any]]:
    """Show the full redaction timeline of one § (point-in-time history).

    The version-history lens: every recorded redaction of a section, oldest
    first, with the date window each was in force and its text. Pair it with
    ``get_provision(as_of=...)`` to read any single past redaction.
    Unknown law returns ``[{note}]``; a known law/§ with no recorded history
    (or an unknown §) returns ``[]``.

    Example question: "How has § 13 of the Penal Code (KarS) changed over time?"

    Returns a list of {redaction_id, valid_from, valid_to, currently_in_force,
    text, truncated, full_length, rt_url}, ordered by valid_from. ``valid_to``
    is null for an open-ended redaction; ``currently_in_force`` is evaluated
    against today's UTC date using both validity bounds. Each ``text`` is cut at ~2000 characters
    unless ``full_text=True``; ``truncated`` / ``full_length`` report the cut.
    ``rt_url`` is that redaction's own riigiteataja.ee URL when the corpus
    records one, else the act's URL, else "".
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found_list(law)
    graph = data.load_law_graph(rec)
    node = data.find_provision(graph, paragraph)
    if node is None:
        return []
    act_rt = data.rt_url(data.act_node(graph))
    rows: list[dict[str, Any]] = []
    timeline = data.provision_version_timeline(rec, node.get("@id", ""))
    current = data.version_in_force_on(timeline, provenance.evaluation_date())
    for v in timeline:
        text, truncated, full_length = _cut(data.clean_display(v["text"]), full_text)
        rows.append(
            {
                "redaction_id": v["redaction_id"],
                "valid_from": v["valid_from"],
                "valid_to": v["valid_to"] or None,
                "currently_in_force": v is current,
                "text": text,
                "truncated": truncated,
                "full_length": full_length,
                "rt_url": v.get("rt_url") or act_rt,
            }
        )
    return rows


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
                "note": i18n.msg("overflow", shown=cap, total=len(rows)),
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
@tool
def regulations_for_law(law: str, limit: int = 50) -> list[dict[str, Any]]:
    """Find the regulations (määrused) issued under / implementing a statute.

    The delegated-legislation lens: state and municipal regulations whose
    ``issuedUnder`` / ``implementsCitation`` points at the target law -- the
    secondary legislation enacted on its authority. Each row carries the
    regulation's riigiteataja.ee URL and the statutory citation text(s) it
    implements. Capped by ``limit`` (a major enabling act has thousands).
    Unknown law returns ``[{note}]``; a known law with none issued under it
    returns ``[]``.

    Example question: "Which regulations are issued under the Local Government
    Organisation Act (KOKS)?"

    Returns a list of {reg_id, title, issuer, status, rt_url, citations, is_kov,
    municipality}. When more than ``limit`` match, a trailing {note, overflow,
    total_available} entry signals the truncation.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found_list(law)
    rows = [_regulation_row(r) for r in data.regulations_for_law(rec.name)]
    return _capped(rows, limit)


# ---------------------------------------------------------------------------
# 13. get_regulation (ticket #500)
# ---------------------------------------------------------------------------
@tool
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
        return {"note": i18n.msg("regulation_not_found", query=name)}
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
@tool
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


@tool
def define_term(term: str, limit: int = 10) -> list[dict[str, Any]]:
    """Look up a legal term in the concept graph, with the defining act's citation.

    Matches the term (accent-insensitive substring) against the concepts
    overlay's prefLabels.

    Example question: "What does 'elatis' mean in the ontology?"

    Returns a list of {id, label, definition, defined_in, source_act, rt_url}:
    ``defined_in`` is the provision IRI that defines the term ("" if not
    recorded), ``source_act`` the defining act's title, and ``rt_url`` that
    act's riigiteataja.ee URL ("" when unknown or not recorded).
    """
    return data.define_term(term, limit=limit)


@tool
def laws_for_subject(subject: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find laws tagged with a EuroVoc subject IRI or keyword.

    Example question: "Which laws are about criminal law / eurovoc 573?"

    Returns a list of {name, title, abbrev, subjects, rt_url}; ``rt_url`` is
    the act's riigiteataja.ee URL or "" when the act records none.
    """
    return data.laws_for_subject(subject, limit=limit)


@tool
def amendment_history(law: str, limit: int = 50) -> list[dict[str, Any]]:
    """List effected amendment events for a law (not pending drafts).

    Example question: "What amendments has KarS already received?"

    Returns a list of {event_id, label, amendment_date, entry_into_force,
    amends, amended_provisions, rt_reference, rt_url, changed_provisions}.
    ``amends`` is the amended act's root IRI; ``amended_provisions`` lists the
    § / subsection IRIs the amending act touched (#713; empty for an
    act-level event). ``rt_reference`` is the
    amending act's Riigi Teataja reference as recorded ("RT I, 2002, 86, 504"
    or an RT URL, else ""); ``rt_url`` is the amending act's riigiteataja.ee
    URL when that reference is one, otherwise the amended act's URL, otherwise
    "". ``changed_provisions`` counts the § redactions the event produced
    (version-layer events; 0 otherwise -- see ``what_changed``).
    Unknown law returns ``[{note}]``; a known law with no events returns ``[]``.
    """
    rec = data.resolve_law(law)
    if rec is None:
        return _law_not_found_list(law)
    return data.amendment_events(rec, limit=limit)


# ---------------------------------------------------------------------------
# 18. eu_case_law_for_directive (ticket #505)
# ---------------------------------------------------------------------------
@tool
def eu_case_law_for_directive(celex: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find CURIA decisions that mention an EU directive CELEX number.

    The EU-side mirror of ``court_decisions_for_law``: after ``transposition``
    returns a CELEX (e.g. ``32000L0060``), this searches the committed CURIA
    peeps for judgments / orders / opinions whose label, ``celexNumber``,
    ``owl:sameAs``, ``dcterms:source``, or text mention that CELEX. Spaces and
    a ``CELEX:`` prefix are stripped. The CURIA corpus currently has no
    ``interprets`` edges (#418), so the join is a CELEX substring match, not a
    graph walk. Each item carries the CURIA / EUR-Lex URL.

    Example question: "Which CURIA judgments interpret directive 32000L0060?"

    Returns a list of {ecli_or_celex, title, curia_or_eurlex_url}. No matches
    (or an empty CELEX) yield ``[{note}]``; ``limit`` <= 0 yields ``[]``.
    """
    if limit <= 0:
        return []
    needle = data.normalize_celex(celex)
    if not needle:
        return [{"note": i18n.msg("no_celex", query=celex)}]
    rows = data.eu_case_law_for_directive(needle, limit=limit)
    if rows:
        return rows
    return [{"note": i18n.msg("curia_none", celex=needle)}]


# ---------------------------------------------------------------------------
# 19. harmonisation_for_directive (ticket #540)
# ---------------------------------------------------------------------------
@tool
def harmonisation_for_directive(celex: str, limit: int = 20) -> list[dict[str, Any]]:
    """Load the cross-border harmonisation sidecar for one directive CELEX.

    Opens ``krr_outputs/harmonisation/harmonisation_by_directive/harm_<celex>.json``
    on demand (the overlay loader; not a scan of every harm file). Lists the
    Estonian act(s) and other member-state measures that share that directive.

    Example question: "Which neighbouring states also transposed 32000L0060?"

    Returns a list of {id, label, member_state, national_celex, harmonises,
    eurlex_url, rt_url}: ``eurlex_url`` is the shared directive on EUR-Lex,
    ``rt_url`` the riigiteataja.ee URL of the Estonian act a row harmonises
    ("" for foreign member-state measures or an act with no RT source).
    Unknown / missing CELEX yields ``[{note}]``; ``limit`` <= 0 yields ``[]``.
    """
    if limit <= 0:
        return []
    needle = data.normalize_celex(celex)
    if not needle:
        return [{"note": i18n.msg("no_celex", query=celex)}]
    rows = data.harmonisation_for_directive(needle, limit=limit)
    if rows:
        return rows
    return [{"note": i18n.msg("harm_none", celex=needle)}]


# ---------------------------------------------------------------------------
# 20. layers_available (ticket #540)
# ---------------------------------------------------------------------------
@tool
def layers_available() -> list[dict[str, Any]]:
    """List which corpus sidecars the MCP surface reads vs excludes.

    Overlay coverage (#540): concepts are wired through ``define_term``;
    harmonisation is opened per-CELEX by ``harmonisation_for_directive``;
    sanctions / amendments / regulations / provision versions are already
    wired. Combined-graph-only and LFS artifacts (similarity,
    ``combined_ontology.jsonld``, annotations, EUR-Lex peeps) are listed as
    ``excluded`` so a silent dead layer is documented rather than implied.

    The table also carries a ``provision_detection`` row, which is a live
    self-test rather than a path check: it reports how many § nodes the Penal
    Code currently resolves, so an upstream retype of the provision class
    (which would silently empty half the tool surface) is visible here.

    Returns a list of {layer, path, status, tools, present, note} where
    ``status`` is ``wired``, ``loadable``, or ``excluded``.
    """
    return data.layers_available()


# ---------------------------------------------------------------------------
# 21. what_changed (#714)
# ---------------------------------------------------------------------------
@tool
def what_changed(
    act_or_provision: str,
    since: str,
    until: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """What changed in a law, or in one §, between two dates.

    The change-log lens, built from the provision-version layer
    (``provision_versions/``) and the amendment events' ``resultedInVersion``
    links. Pass a law (title / abbreviation / slug), a "law § n" reference
    ("KarS § 424"), or a provision IRI ("estleg:KARIST_2_Osa2_Par_424"), and an
    ISO ``since`` date; ``until`` defaults to today (UTC). Both bounds are
    inclusive and refer to the date a change took effect.

    Example question: "What changed in the Penal Code (KarS) during 2014?"

    Returns {law, abbrev, rt_url, scope ("act" | "provision"), provision_id,
    paragraph, since, until, history_available, changes_total,
    provisions_changed, truncated, changes, amendment_events}.
    ``changes`` (capped by ``limit``; ``truncated`` says so, ``changes_total``
    is the uncapped count) are {date, change, provision_id, paragraph,
    redaction_id, previous_redaction_id, valid_from, valid_to,
    amendment_event, rt_url}, ordered by date, where ``change`` is
    ``amended`` (a new redaction replaced an older one), ``added`` (first
    redaction, starting after the law's recorded history begins),
    ``first_recorded`` (first redaction at the start of the recorded history;
    a baseline, not a change) or ``ceased`` (no longer in force from
    ``date``). ``rt_url`` is the redaction's riigiteataja.ee URL when recorded.
    ``amendment_events`` lists the effected amendment events in the window
    (only those linked to the changes when scoped to one §), each with
    {event_id, label, amendment_date, entry_into_force, amends,
    amended_provisions, rt_reference, rt_url, changed_provisions}. ``history_available`` is false (with a
    ``note``) when the corpus has no version history for the law. A bad date
    or an unknown law / § yields a {note}.
    """
    since_iso = data.normalize_iso_date(since)
    if since_iso is None:
        return {"note": i18n.msg("bad_date", field="since", value=since)}
    if until is None or not str(until).strip():
        until_iso = provenance.evaluation_date()
    else:
        until_iso = data.normalize_iso_date(until)
        if until_iso is None:
            return {"note": i18n.msg("bad_date", field="until", value=until)}
    if since_iso > until_iso:
        return {"note": i18n.msg("bad_window", since=since_iso, until=until_iso)}

    raw = (act_or_provision or "").strip()
    rec, node, problem = data.resolve_act_or_provision(raw)
    provision_iri = node.get("@id", "") if node is not None else ""
    if problem == "paragraph" and rec is not None and raw in data.law_version_index(rec):
        # A provision IRI that is no longer in the current graph (repealed or
        # renumbered) still has a history worth reporting.
        problem, provision_iri = "", raw
    if problem == "law" or rec is None:
        return _law_not_found(raw)
    if problem == "paragraph":
        _, par = data.resolve_law_and_paragraph(raw)
        return {"note": i18n.msg("paragraph_not_found", law=rec.title, paragraph=par or raw)}

    graph = data.load_law_graph(rec)
    nodes_by_id = {
        n.get("@id"): n for n in data.provision_nodes(graph) if isinstance(n.get("@id"), str)
    }
    history_available = bool(data.law_version_index(rec))
    changes = data.provision_changes(rec, since_iso, until_iso, provision_iri or None)
    for row in changes:
        row["paragraph"] = data.provision_display(
            nodes_by_id.get(row["provision_id"]), row["provision_id"]
        )
    events = data.amendment_events_between(rec, since_iso, until_iso)
    if provision_iri:
        linked = {row["amendment_event"] for row in changes if row["amendment_event"]}
        events = [e for e in events if e["event_id"] in linked]
    cap = max(0, int(limit))
    result: dict[str, Any] = {
        "law": data.act_title(data.act_node(graph)) or rec.title,
        "abbrev": data.display_abbrev(rec),
        "rt_url": data.rt_url(data.act_node(graph)),
        "scope": "provision" if provision_iri else "act",
        "provision_id": provision_iri,
        "paragraph": data.provision_display(node, provision_iri) if provision_iri else "",
        "since": since_iso,
        "until": until_iso,
        "history_available": history_available,
        "changes_total": len(changes),
        "provisions_changed": len({row["provision_id"] for row in changes}),
        "truncated": len(changes) > cap or len(events) > cap,
        "changes": changes[:cap],
        "amendment_events": events[:cap],
    }
    if not history_available:
        result["note"] = i18n.msg("no_law_history", law=rec.title)
    return result


# ---------------------------------------------------------------------------
# 22. transposition_gaps (#714)
# ---------------------------------------------------------------------------
@tool
def transposition_gaps(directive: str | None = None, limit: int = 50) -> dict[str, Any]:
    """In-force EU directives with no transposition edge in the corpus.

    The coverage-gap lens over the #701 ``noTranspositionEdgeInCorpus`` flag,
    computed from the EUR-Lex directives peep (``estleg:inForce``,
    ``estleg:transposedBy``) and ``reports/transposition_mapping.json``. It is
    a statement about this corpus, **not** a legal finding that a directive is
    untransposed: the transposition mapping covers the measures the EU
    publications office reports and the corpus could match to a law.

    Without ``directive`` it lists the gaps (oldest transposition deadline
    first); with a CELEX ``directive`` it reports that one directive's status.

    Example question: "Which directives in force have no transposing Estonian
    law in the corpus?"

    Returns, without ``directive``: {coverage_flag, caveat,
    in_force_directives, gaps_total, truncated, gaps}, where ``gaps`` (capped
    by ``limit``) are {celex, title, transposition_deadline, eurlex_url,
    coverage_flag}. With ``directive``: {celex, title, in_force,
    transposition_deadline, eurlex_url, transposing_laws, coverage_flag,
    caveat}; ``transposing_laws`` are {name, title, rt_url}, and
    ``coverage_flag`` is "noTranspositionEdgeInCorpus" only for an in-force
    directive without any edge (else ""). An empty or unknown CELEX yields a
    {note}.
    """
    caveat = i18n.msg("coverage_caveat")
    if directive is not None and str(directive).strip():
        celex = data.normalize_celex(directive)
        if not celex:
            return {"note": i18n.msg("no_celex", query=directive)}
        status = data.transposition_status(celex)
        if status is None:
            return {"note": i18n.msg("directive_not_found", celex=celex)}
        return {**status, "caveat": caveat if status["coverage_flag"] else ""}
    gaps = data.transposition_gaps()
    cap = max(0, int(limit))
    return {
        "coverage_flag": "noTranspositionEdgeInCorpus",
        "caveat": caveat,
        "in_force_directives": data.in_force_directive_count(),
        "gaps_total": len(gaps),
        "truncated": len(gaps) > cap,
        "gaps": gaps[:cap],
    }


# ---------------------------------------------------------------------------
# 23. kov_regulations_citing (#714)
# ---------------------------------------------------------------------------
@tool
def kov_regulations_citing(
    law_or_provision: str,
    paragraph: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Find municipal (KOV) regulations that cite a law or one of its sections.

    The local-implementation lens: municipal regulations whose statutory basis
    (``estleg:implementsCitation``, e.g. "KOKS § 22 lg 1 p 5") targets the law
    -- or, at law level, that are ``issuedUnder`` it. Pass a law, a "law § n"
    reference ("KOKS § 22") or a provision IRI; ``paragraph`` overrides the §.
    At § level only regulations whose citation names that § are returned, each
    with just the matching citations. Capped by ``limit``.

    Example question: "Which municipal regulations rely on § 22 of the Local
    Government Organisation Act (KOKS)?"

    Returns a list of {reg_id, title, issuer, status, rt_url, citations,
    is_kov, municipality, matched_by, matched_citations}, where ``matched_by``
    is ``implementsCitation`` or ``issuedUnder`` and ``matched_citations`` are
    {text, target, detail}. ``rt_url`` is the regulation's riigiteataja.ee
    URL. Unknown law returns ``[{note}]``; no citing regulation returns ``[]``.
    When more than ``limit`` match, a trailing {note, overflow,
    total_available} entry signals the truncation.
    """
    rec, par = data.resolve_law_and_paragraph(law_or_provision, paragraph)
    if rec is None:
        return _law_not_found_list(law_or_provision)
    rows: list[dict[str, Any]] = []
    for reg, links in data.kov_regulations_citing(rec.name, par or None):
        row = _regulation_row(reg)
        row["matched_by"] = "implementsCitation" if links else "issuedUnder"
        row["matched_citations"] = [dict(link) for link in links]
        rows.append(row)
    return _capped(rows, limit)


# ---------------------------------------------------------------------------
# 24. explain_provision (#714)
# ---------------------------------------------------------------------------
_EXPLAIN_LIST_CAP = 10


@tool
def explain_provision(iri: str, full_text: bool = False) -> dict[str, Any]:
    """Explain one provision in a single answer: text, history, links, context.

    Composes what the other tools return piecemeal for one §: its current
    text, its redaction history, the provisions it cites and that cite it,
    the Supreme Court decisions interpreting it, the competent authorities,
    the sanctions it carries, and the municipal regulations citing it -- plus
    a short ``explanation`` sentence in the requested language. Accepts a
    provision IRI ("estleg:KARIST_2_Osa2_Par_424") or a "law § n" reference
    ("KarS § 424").

    Example question: "Explain § 424 of the Penal Code (KarS)."

    Returns {id, law, abbrev, paragrahv, label, summary, legal_text,
    truncated, full_length, rt_url, history, references_out, referenced_by,
    court_decisions, competent_authorities, sanctions, kov_regulations,
    counts, explanation}. ``history`` is {redactions, first_valid_from,
    current_redaction_id, current_valid_from, last_valid_to,
    currently_in_force, rt_url}. Each list is capped at 10 rows; ``counts``
    carries the uncapped totals. Reference rows are {id, label, law, rt_url};
    court decisions {case_number, label, decision_link}; authorities
    {institution, institution_id}; sanctions {sanction_type, penalty, rt_url};
    KOV rows {reg_id, title, municipality, rt_url}. Legal text is cut at ~2000
    characters unless ``full_text=True``. ``truncated`` also reports capped
    lists (even with ``full_text=True``). An unknown provision yields a {note}.
    """
    rec, node, problem = data.resolve_act_or_provision(iri)
    if rec is None or node is None:
        if rec is not None and problem == "paragraph":
            _, par = data.resolve_law_and_paragraph(iri)
            return {"note": i18n.msg("paragraph_not_found", law=rec.title, paragraph=par or iri)}
        return {"note": i18n.msg("provision_not_found", query=iri)}
    pid = node.get("@id", "")
    graph = data.load_law_graph(rec)
    act_rt = data.rt_url(data.act_node(graph))
    paragrahv = data.clean_display(data._text(node.get("estleg:paragrahv")))
    display = data.provision_display(node, pid)

    timeline = data.provision_version_timeline(rec, pid)
    today = provenance.evaluation_date()
    current = data.version_in_force_on(timeline, today)
    last = timeline[-1] if timeline else None
    history = {
        "redactions": len(timeline),
        "first_valid_from": timeline[0]["valid_from"] if timeline else "",
        "current_redaction_id": current["redaction_id"] if current else "",
        "current_valid_from": current["valid_from"] if current else "",
        "last_valid_to": (last["valid_to"] or None) if last else None,
        "currently_in_force": current is not None,
        "rt_url": (current.get("rt_url") or act_rt) if current else act_rt,
    }

    refs_out = data.provision_reference_rows(rec, node, "estleg:references")
    refs_in = data.provision_reference_rows(rec, node, "estleg:referencedBy")
    decision_iris = list(dict.fromkeys(data._ids_of(node.get("estleg:interpretedBy"))))
    decisions: list[dict[str, str]] = []
    for d_iri in decision_iris[:_EXPLAIN_LIST_CAP]:
        dec = data.court_decision(d_iri)
        if dec is not None:
            decisions.append(
                {
                    "case_number": data._text(dec.get("estleg:caseNumber")),
                    "label": data._text(dec.get("rdfs:label")),
                    "decision_link": data._text(dec.get("estleg:decisionLink")),
                }
            )
    authorities = [
        {"institution": data.institution_label(a), "institution_id": a}
        for a in dict.fromkeys(data._ids_of(node.get("estleg:competentAuthority")))
    ]
    sanctions = [
        {**row, "rt_url": act_rt} for row in data.sanctions_for_provision(rec, pid)
    ]
    par_key = data.paragraph_key_from_iri(pid) or paragrahv
    kov = data.kov_regulations_citing(rec.name, par_key) if par_key else []
    kov_rows = [
        {
            "reg_id": reg.reg_id or "",
            "title": reg.title or "",
            "municipality": reg.municipality or "",
            "rt_url": reg.rt_url or "",
        }
        for reg, _links in kov[:_EXPLAIN_LIST_CAP]
    ]
    counts = {
        "references_out": len(refs_out),
        "referenced_by": len(refs_in),
        "court_decisions": len(decision_iris),
        "competent_authorities": len(authorities),
        "sanctions": len(sanctions),
        "kov_regulations": len(kov),
    }

    if not timeline:
        history_text = i18n.msg("explain_history_none")
    elif history["currently_in_force"]:
        history_text = i18n.msg(
            "explain_history",
            n=len(timeline),
            first=history["first_valid_from"],
            current=history["current_redaction_id"],
            since=history["current_valid_from"],
        )
    elif history["last_valid_to"] and history["last_valid_to"] < today:
        history_text = i18n.msg(
            "explain_history_ceased",
            n=len(timeline),
            first=history["first_valid_from"],
            until=history["last_valid_to"],
        )
    else:
        history_text = i18n.msg("explain_history_not_active", date=today)
    authority_names = ", ".join(a["institution"] for a in authorities[:3]) or "0"
    law_title = data.act_title(data.act_node(graph)) or rec.title
    explanation = i18n.msg(
        "explain_text",
        paragraph=display or pid,
        law=law_title,
        history=history_text,
        refs_out=counts["references_out"],
        refs_in=counts["referenced_by"],
        decisions=counts["court_decisions"],
        authorities=authority_names,
        sanctions=counts["sanctions"],
        kov=counts["kov_regulations"],
    )

    result: dict[str, Any] = {
        "id": pid,
        "law": law_title,
        "abbrev": data.display_abbrev(rec),
        "paragrahv": paragrahv,
        "label": data.clean_display(data._text(node.get("rdfs:label"))),
        "summary": data.clean_display(data._text(node.get("estleg:summary"))),
        "rt_url": act_rt,
    }
    _put_text(result, data.clean_display(data._text(node.get("estleg:legalText"))), full_text)
    result["truncated"] = result["truncated"] or any(
        count > _EXPLAIN_LIST_CAP for count in counts.values()
    )
    result.update(
        {
            "history": history,
            "references_out": refs_out[:_EXPLAIN_LIST_CAP],
            "referenced_by": refs_in[:_EXPLAIN_LIST_CAP],
            "court_decisions": decisions,
            "competent_authorities": authorities[:_EXPLAIN_LIST_CAP],
            "sanctions": sanctions[:_EXPLAIN_LIST_CAP],
            "kov_regulations": kov_rows,
            "counts": counts,
            "explanation": explanation,
        }
    )
    return result


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
      that terminates the public host; the per-consumer bearer tokens
      (``ESTLEG_TOKENS`` / ``ESTLEG_TOKEN``, required on HTTP) are the actual
      access gate for this public-law data.
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


def _http_access() -> tuple[security.TokenRegistry | None, security.RateLimiter | None]:
    """Resolve the HTTP access policy from the environment, failing closed.

    Returns ``(registry, limiter)``. ``registry`` is ``None`` only when no
    token is configured *and* ``ESTLEG_ALLOW_ANONYMOUS_HTTP=1`` was set
    explicitly. Raises :class:`security.CredentialsRequired` when neither
    holds, and :class:`security.TokenConfigError` / :class:`ValueError` on a
    malformed token map or rate-limit setting -- the server must not start
    half-configured.
    """
    registry = security.load_registry()
    if len(registry) == 0:
        if not security.anonymous_http_allowed():
            raise security.CredentialsRequired(
                "refusing to serve HTTP without credentials. Set "
                "ESTLEG_TOKENS (name=token,... or a JSON file path) or "
                "ESTLEG_TOKEN; for local development only, set "
                "ESTLEG_ALLOW_ANONYMOUS_HTTP=1."
            )
        registry = None
    return registry, security.RateLimiter.from_env()


def _build_http_app():
    """Build the Starlette app for the streamable-HTTP transport.

    Adds an unauthenticated ``/healthz`` (for the container/proxy health
    check) and the :class:`security.AccessMiddleware` gate on every other
    path: a bearer token from the per-consumer map is required (fail closed,
    see :func:`_http_access`), the consumer name is recorded for the audit
    line, and the optional per-consumer rate limit applies. The ontology is
    public law; the gate exists to attribute and bound use of a shared
    endpoint, not to protect secret data.
    """
    registry, limiter = _http_access()
    if registry is None:
        print(
            "estleg-mcp: WARNING ESTLEG_ALLOW_ANONYMOUS_HTTP=1 -- the HTTP "
            "endpoint is open to anyone who can reach it.",
            file=sys.stderr,
        )
    host = os.environ.get("ESTLEG_HOST", "127.0.0.1")
    security_settings = _transport_security()
    # SDK 2 moved transport configuration from settings to the app factory.
    if _MCP_V2:
        app = mcp.streamable_http_app(host=host, transport_security=security_settings)
    else:
        mcp.settings.host = host
        mcp.settings.port = int(os.environ.get("ESTLEG_PORT", "8000"))
        mcp.settings.transport_security = security_settings
        app = mcp.streamable_http_app()

    from starlette.requests import Request
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def _health(_req: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app.routes.append(Route(security.HEALTH_PATH, _health, methods=["GET"]))
    # w3id resolver pilot (#728): public, read-only /id/{local} + /vocabulary.
    open_resolver = resolver_web.enabled()
    if open_resolver:
        resolver_web.mount(app)
    app.add_middleware(
        security.AccessMiddleware,
        registry=registry,
        limiter=limiter,
        open_resolver=open_resolver,
        resolver_limiter=security.RateLimiter.from_env(
            rate_var="ESTLEG_RESOLVER_RATE_LIMIT", burst_var="ESTLEG_RESOLVER_RATE_BURST"
        ),
        audit_log=audit.get_audit_log,
        identity=lambda: {
            **provenance.corpus_identity(),
            "server_version": provenance.SERVER_VERSION,
        },
    )
    return app


def check_provision_detection() -> None:
    """Refuse to boot when the corpus's § nodes are no longer detected (#678).

    A generator change that retypes provisions breaks no import and loses no
    file: ``get_provision``, ``provision_history``, the reference tools, the
    court/authority lenses and every ``num_provisions`` count simply return
    nothing, and a lawmaker reads that as "the corpus says there is nothing".
    Failing loudly at startup is the only place that regression is cheap to
    catch. ``ESTLEG_ALLOW_EMPTY_PROVISIONS=1`` downgrades it to a warning for
    an operator who knowingly wants the remaining tools.
    """
    check = data.provision_detection_check()
    if check["ok"]:
        return
    print(
        f"estleg-mcp: provision detection is dark -- {check['abbrev']} "
        f"({check['law']}) resolves {check['provisions']} § nodes.\n"
        "  Every provision-backed tool (get_provision, provision_history, "
        "who_references, references_of, court_decisions_for_law, "
        "competent_authority_for_law, num_provisions) would answer empty.\n"
        "  Check that ESTLEG_CORPUS points at a corpus containing "
        "krr_outputs/INDEX.json, and that its *_peep.json § nodes still carry "
        "an estleg:LegalProvision @type (see estleg_mcp.data._is_provision).\n"
        "  Set ESTLEG_ALLOW_EMPTY_PROVISIONS=1 to start anyway.",
        file=sys.stderr,
    )
    if os.environ.get("ESTLEG_ALLOW_EMPTY_PROVISIONS", "").strip() == "1":
        print(
            "estleg-mcp: ESTLEG_ALLOW_EMPTY_PROVISIONS=1 set; starting anyway.",
            file=sys.stderr,
        )
        return
    raise SystemExit(1)


def main() -> None:
    """Run the estleg MCP server.

    Transport is chosen by ``ESTLEG_TRANSPORT`` (default ``stdio`` for local
    IDE clients). Set it to ``http`` to serve the streamable-HTTP transport for
    a remote deployment; ``ESTLEG_HOST`` (default 127.0.0.1; use 0.0.0.0 in a
    container) and ``ESTLEG_PORT`` (default 8000) tune it. HTTP **fails
    closed**: it refuses to start unless ``ESTLEG_TOKENS`` / ``ESTLEG_TOKEN``
    configure at least one consumer (or ``ESTLEG_ALLOW_ANONYMOUS_HTTP=1`` is
    set for local development). stdio stays credential-free: it is a local
    process owned by the IDE user. Behind a reverse proxy, set
    ``ESTLEG_ALLOWED_HOSTS`` to the public host(s) to re-enable DNS-rebinding
    protection (see :func:`_transport_security`).

    Either transport is preceded by :func:`check_provision_detection`, so a
    corpus whose § nodes stopped being detected fails at boot instead of
    serving confident empty answers, and by opening the audit log, so an
    unwritable ``ESTLEG_AUDIT_LOG`` fails at boot rather than per call.
    """
    check_provision_detection()
    try:
        audit.get_audit_log()
    except OSError as exc:
        print(f"estleg-mcp: cannot open ESTLEG_AUDIT_LOG: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    transport = os.environ.get("ESTLEG_TRANSPORT", "stdio").strip().lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        import uvicorn

        try:
            app = _build_http_app()
        except (security.CredentialsRequired, ValueError) as exc:
            # TokenConfigError is a ValueError, as is a bad ESTLEG_RATE_LIMIT.
            print(f"estleg-mcp: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        uvicorn.run(
            app,
            host=os.environ.get("ESTLEG_HOST", "127.0.0.1"),
            port=int(os.environ.get("ESTLEG_PORT", "8000")),
        )
    else:
        mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
