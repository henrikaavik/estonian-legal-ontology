"""HTTP routes of the w3id resolver pilot: ``/id/{local}`` and ``/vocabulary`` (#728).

``https://w3id.org/estleg/<local>`` 303-redirects here (see
``w3id/estleg/.htaccess``); the client re-sends its own ``Accept`` header to
``https://<host>/id/<local>`` and gets one of:

==========================  ===================================================
``application/ld+json``     the node as compact JSON-LD (project ``@context``,
                            the node, labelled ``estleg:`` neighbours)
``text/turtle``             the same graph serialised by rdflib
``application/n-triples``   ditto
``application/rdf+xml``     ditto
``text/html`` (default)     a small dependency-free description page
==========================  ===================================================

``?format=jsonld|ttl|nt|rdf|html`` overrides ``Accept`` (for links and
debugging). Unknown names get ``404`` with a JSON body; an unsatisfiable
``Accept`` gets ``406``. ``KarS_Par_141``-style aliases 303 to the canonical
name.

The routes are read-only and anonymous by design -- these are public
identifiers -- so :class:`security.AccessMiddleware` lets ``GET``/``HEAD`` on
them through without a token (``open_resolver=True``), rate-limits them under
one fixed bucket (:data:`security.RESOLVER_BUCKET`) and every response here
writes one audit line (consumer ``anonymous``, tool ``resolve``).
"""

from __future__ import annotations

import functools
import hashlib
import html
import json
import os
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import anyio

from . import audit, data, provenance, resolver, security

RESOLVE_TOOL = "resolve"

# format key -> (media type, rdflib serializer name or None for native)
FORMATS: dict[str, tuple[str, str | None]] = {
    "jsonld": ("application/ld+json", None),
    "turtle": ("text/turtle", "turtle"),
    "ntriples": ("application/n-triples", "nt"),
    "rdfxml": ("application/rdf+xml", "xml"),
    "html": ("text/html", None),
}
# Accept media type -> format key.
_MEDIA: dict[str, str] = {
    "application/ld+json": "jsonld",
    "application/json": "jsonld",
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "text/n3": "turtle",
    "application/n-triples": "ntriples",
    "application/rdf+xml": "rdfxml",
    "text/html": "html",
    "application/xhtml+xml": "html",
}
_WILDCARD: dict[str, str] = {"*/*": "html", "text/*": "html", "application/*": "jsonld"}
# ?format= values.
_FORMAT_PARAM: dict[str, str] = {
    "jsonld": "jsonld", "json": "jsonld", "ld+json": "jsonld",
    "ttl": "turtle", "turtle": "turtle",
    "nt": "ntriples", "ntriples": "ntriples",
    "rdf": "rdfxml", "xml": "rdfxml", "rdfxml": "rdfxml",
    "html": "html",
}
_ALTERNATE_PARAM = {"jsonld": "jsonld", "turtle": "ttl", "ntriples": "nt", "rdfxml": "rdf"}

CACHE_CONTROL = "public, max-age=3600"
_LONG_TEXT = 400


class RdfUnavailable(RuntimeError):
    """rdflib is not installed, so no RDF serialisation can be produced."""


# ---------------------------------------------------------------------------
# Content negotiation
# ---------------------------------------------------------------------------
def negotiate(accept: str | None, format_param: str | None = None) -> str | None:
    """Pick a format key for ``Accept`` (``None`` = nothing acceptable -> 406).

    An explicit ``?format=`` wins. Otherwise the highest ``q`` wins, then the
    more specific range (``text/turtle`` over ``*/*``), then header order. A
    missing or empty ``Accept`` is ``*/*``, which is HTML.
    """
    if format_param:
        return _FORMAT_PARAM.get(format_param.strip().lower())
    header = (accept or "").strip() or "*/*"
    best: tuple[float, int, int] | None = None
    choice: str | None = None
    for position, part in enumerate(header.split(",")):
        fields = [f.strip() for f in part.split(";")]
        media = fields[0].lower()
        q = 1.0
        for param in fields[1:]:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if q <= 0:
            continue
        if media in _MEDIA:
            fmt, specificity = _MEDIA[media], 2
        elif media in _WILDCARD:
            fmt, specificity = _WILDCARD[media], 0 if media == "*/*" else 1
        else:
            continue
        key = (q, specificity, -position)
        if best is None or key > best:
            best, choice = key, fmt
    return choice


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def to_rdf(document: dict[str, Any], fmt: str) -> str:
    """Serialise a compact JSON-LD document with rdflib (``turtle``/``nt``/``xml``)."""
    try:
        import rdflib
    except ImportError as exc:  # pragma: no cover - exercised only without rdflib
        raise RdfUnavailable("rdflib is not installed") from exc
    graph = rdflib.Graph()
    graph.parse(data=json.dumps(document), format="json-ld")
    context = document.get("@context")
    if isinstance(context, dict):
        for prefix, namespace in context.items():
            if isinstance(namespace, str) and not prefix.startswith("@"):
                graph.bind(prefix, rdflib.Namespace(namespace), override=True)
    return graph.serialize(format=fmt)


@functools.lru_cache(maxsize=8)
def _vocabulary_rdf(fmt: str) -> str:
    return to_rdf(resolver.vocabulary_document(), fmt)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
_CSS = (
    "body{font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;max-width:60rem;"
    "margin:2rem auto;padding:0 1rem;color:#1a1a1a;background:#fff}"
    "@media(prefers-color-scheme:dark){body{color:#e6e6e6;background:#141414}"
    "a{color:#8ab4f8}th{color:#aaa}}"
    "h1{font-size:1.5rem;margin:.2rem 0}code{font-size:.9em}"
    ".iri{color:#666;word-break:break-all}table{border-collapse:collapse;width:100%}"
    "th,td{text-align:left;vertical-align:top;padding:.3rem .5rem;border-top:1px solid #8884}"
    "th{font-weight:500;color:#555;white-space:nowrap;width:1%}"
    "td{word-break:break-word}footer{margin-top:2rem;color:#777;font-size:.85rem}"
    "section{margin:1.2rem 0;padding-top:.4rem;border-top:1px solid #8884}"
    "ul.terms{columns:3 14rem;padding-left:1.2rem}"
)


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _id_href(local: str) -> str:
    return "/id/" + quote(local, safe="")


def _safe_url(url: str) -> str:
    """``url`` when it is a well-formed absolute http(s) URL, else ""."""
    return url if isinstance(url, str) and data._host_of(url) else ""


def _term_href(curie: str) -> str:
    local = resolver.local_of(curie)
    if local and local in resolver.vocabulary_nodes():
        return "/vocabulary#" + quote(local, safe="")
    return _id_href(local) if local else ""


def _render_value(value: Any, labels: dict[str, str]) -> str:
    if isinstance(value, dict):
        ref = value.get("@id")
        if isinstance(ref, str):
            local = resolver.local_of(ref)
            if local:
                label = labels.get(ref, "")
                text = f"{_e(label)} <code>{_e(ref)}</code>" if label else f"<code>{_e(ref)}</code>"
                return f'<a href="{_e(_id_href(local))}">{text}</a>'
            url = _safe_url(ref)
            if url:
                return f'<a href="{_e(url)}" rel="external">{_e(url)}</a>'
            return f"<code>{_e(ref)}</code>"
        inner = value.get("@value")
        if inner is None:
            return f"<code>{_e(json.dumps(value, ensure_ascii=False))}</code>"
        dtype = value.get("@type")
        if dtype == "xsd:anyURI" and _safe_url(str(inner)):
            return f'<a href="{_e(inner)}" rel="external">{_e(inner)}</a>'
        lang = value.get("@language")
        body = _render_text(str(inner), lang if isinstance(lang, str) else "")
        return body + (f" <small>({_e(dtype)})</small>" if isinstance(dtype, str) else "")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _e(value)
    return _render_text(str(value), "")


def _render_text(text: str, lang: str) -> str:
    attr = f' lang="{_e(lang)}"' if lang else ""
    if len(text) <= _LONG_TEXT:
        return f"<span{attr}>{_e(text)}</span>"
    return (
        f"<details{attr}><summary>{_e(text[:_LONG_TEXT])}…</summary>"
        f"<p>{_e(text)}</p></details>"
    )


def citation_links(res: resolver.Resolution) -> list[tuple[str, str]]:
    """``[(label, url)]`` official / source links for the node, best first."""
    node = res.node
    links: list[tuple[str, str]] = []

    def add(label: str, url: str) -> None:
        url = _safe_url(url)
        if url and all(url != u for _, u in links):
            links.append((label, url))

    add("Riigi Teataja", data.rt_url(node))
    add("Riigi Teataja", data._guarded_rt(data._text(node.get("estleg:rtUrl"))))
    add("Riigi Teataja", data.rt_url(resolver.act_node_for(res)))
    if not links and res.family in {"law", "provision_version", "sanction", "amendment"} \
            and "Reg_" not in res.local:
        try:
            add("Riigi Teataja", data.citation_url_for_iri(resolver.curie(res.local)))
        except FileNotFoundError:  # no corpus: no citation, still a page
            pass
    for key, label in (
        ("estleg:eurLexLink", "EUR-Lex"),
        ("estleg:eliIdentifier", "ELI"),
        ("estleg:curiaLink", "CURIA"),
        ("estleg:decisionLink", "Riigikohus"),
        ("estleg:eisLink", "Eelnõude infosüsteem"),
    ):
        add(label, data._text(node.get(key)) or data._id_of(node.get(key)))
    for key in ("owl:sameAs", "rdfs:seeAlso", "dcterms:source"):
        for ref in data._ids_of(node.get(key)):
            add(data._external_id_family(ref) or key, ref)
    return links


def _page(title: str, head_extra: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"et\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title>{head_extra}<style>{_CSS}</style></head>"
        f"<body>{body}<footer>{_footer()}</footer></body></html>\n"
    )


def _footer() -> str:
    ident = provenance.corpus_identity()
    commit = ident.get("corpus_commit", "")[:12] or "unknown"
    version = ident.get("ontology_version", "") or "unknown"
    return (
        f"Estonian Legal Ontology {_e(version)} · corpus <code>{_e(commit)}</code> · "
        f"estleg-mcp {_e(provenance.SERVER_VERSION)} · "
        '<a href="/vocabulary">vocabulary</a> · '
        '<a href="https://github.com/henrikaavik/estonian-legal-ontology">source</a>'
    )


def _alternates(local: str) -> list[tuple[str, str]]:
    return [
        (FORMATS[fmt][0], f"{_id_href(local)}?format={param}")
        for fmt, param in _ALTERNATE_PARAM.items()
    ]


def render_node_html(res: resolver.Resolution) -> str:
    node = res.node
    iri = resolver.NAMESPACE + res.local
    label = resolver.label_of(node) or res.local
    labels = res.neighbours
    head = f'<link rel="canonical" href="{_e(iri)}">' + "".join(
        f'<link rel="alternate" type="{_e(mt)}" href="{_e(href)}">'
        for mt, href in _alternates(res.local)
    )
    types = []
    for t in data._types_of(node):
        href = _term_href(t)
        tlabel = labels.get(t, "")
        text = f"{_e(tlabel)} <code>{_e(t)}</code>" if tlabel else f"<code>{_e(t)}</code>"
        types.append(f'<a href="{_e(href)}">{text}</a>' if href else text)
    parts = [
        f"<h1>{_e(label)}</h1>",
        f'<p class="iri"><code>{_e(iri)}</code></p>',
        f"<p>{' · '.join(types)}</p>" if types else "",
    ]
    links = citation_links(res)
    if links:
        parts.append(
            "<p>"
            + " · ".join(
                f'<a href="{_e(url)}" rel="external">{_e(name)}</a>' for name, url in links
            )
            + "</p>"
        )
    rows = []
    for key, value in node.items():
        if key in ("@id", "@type"):
            continue
        href = _term_href(key) if key.startswith("estleg:") else ""
        head_cell = f'<a href="{_e(href)}">{_e(key)}</a>' if href else _e(key)
        cells = "<br>".join(_render_value(v, labels) for v in data._as_list(value))
        rows.append(f"<tr><th>{head_cell}</th><td>{cells}</td></tr>")
    parts.append(f"<table>{''.join(rows)}</table>")
    parts.append(
        "<p>Also as "
        + " · ".join(
            f'<a href="{_e(href)}" type="{_e(mt)}">{_e(mt)}</a>' for mt, href in _alternates(res.local)
        )
        + "</p>"
    )
    return _page(f"{label} — estleg", head, "".join(parts))


_VOCAB_GROUPS = (
    ("owl:Ontology", "Ontology"),
    ("owl:Class", "Classes"),
    ("owl:ObjectProperty", "Object properties"),
    ("owl:DatatypeProperty", "Datatype properties"),
    ("skos:ConceptScheme", "Concept schemes"),
)


def render_vocabulary_html() -> str:
    nodes = resolver.vocabulary_nodes()
    grouped: dict[str, list[str]] = {title: [] for _, title in _VOCAB_GROUPS}
    grouped["Controlled values"] = []
    for local, node in sorted(nodes.items(), key=lambda kv: kv[0].lower()):
        types = data._types_of(node)
        title = next((t for c, t in _VOCAB_GROUPS if c in types), "Controlled values")
        grouped[title].append(local)
    head = (
        f'<link rel="canonical" href="{_e(resolver.NAMESPACE)}vocabulary">'
        '<link rel="alternate" type="application/ld+json" href="/vocabulary?format=jsonld">'
        '<link rel="alternate" type="text/turtle" href="/vocabulary?format=ttl">'
    )
    parts = [
        "<h1>Estonian Legal Ontology vocabulary</h1>",
        f'<p class="iri"><code>{_e(resolver.NAMESPACE)}vocabulary</code> · '
        f"{len(nodes)} terms · also as "
        '<a href="/vocabulary?format=jsonld">JSON-LD</a> · '
        '<a href="/vocabulary?format=ttl">Turtle</a></p>',
    ]
    for title, locals_ in grouped.items():
        if not locals_:
            continue
        index = "".join(
            f'<li><a href="#{_e(quote(n, safe=""))}">{_e(n)}</a></li>' for n in locals_
        )
        parts.append(f"<h2>{_e(title)} ({len(locals_)})</h2><ul class=\"terms\">{index}</ul>")
    relation_keys = ("rdfs:subClassOf", "rdfs:domain", "rdfs:range", "skos:inScheme",
                     "rdfs:subPropertyOf", "owl:inverseOf", "owl:equivalentClass")
    for local, node in sorted(nodes.items(), key=lambda kv: kv[0].lower()):
        labels = []
        for value in data._as_list(node.get("rdfs:label")):
            text = data._text(value)
            lang = value.get("@language", "") if isinstance(value, dict) else ""
            if text:
                labels.append(f"<span lang=\"{_e(lang)}\">{_e(text)}</span>" if lang else _e(text))
        rels = []
        for key in relation_keys:
            for ref in data._ids_of(node.get(key)):
                href = _term_href(ref) or _safe_url(ref)
                target = f'<a href="{_e(href)}"><code>{_e(ref)}</code></a>' if href else _e(ref)
                rels.append(f"{_e(key)} {target}")
        comment = data._text(node.get("rdfs:comment")) or data._text(node.get("skos:definition"))
        parts.append(
            f'<section id="{_e(local)}"><h3><code>estleg:{_e(local)}</code></h3>'
            f"<p>{' · '.join(labels)}</p>"
            f"<p><small>{_e(', '.join(data._types_of(node)))}</small></p>"
            + (f"<p>{_e(comment)}</p>" if comment else "")
            + (f"<p>{'<br>'.join(rels)}</p>" if rels else "")
            + f'<p><a href="{_e(_id_href(local))}">/id/{_e(local)}</a></p></section>'
        )
    return _page("Estonian Legal Ontology vocabulary", head, "".join(parts))


# ---------------------------------------------------------------------------
# Responses + audit
# ---------------------------------------------------------------------------
def _etag(kind: str, local: str, fmt: str) -> str:
    commit = provenance.corpus_identity().get("corpus_commit", "")[:12] or "nocommit"
    digest = hashlib.sha256(f"{kind}\0{local}\0{fmt}".encode()).hexdigest()[:16]
    return f'W/"{commit}-{provenance.SERVER_VERSION}-{digest}"'


def _base_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Vary": "Accept",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": CACHE_CONTROL,
    }
    headers.update(extra or {})
    return headers


def _consumer(request: Any) -> str:
    state = request.scope.get("state")
    consumer = state.get(security.CONSUMER_STATE_KEY) if isinstance(state, dict) else None
    return consumer if isinstance(consumer, str) and consumer else security.ANONYMOUS_CONSUMER


def _emit(
    request: Any, started: float, *, target: str, fmt: str | None, status: str,
    http_status: int, result_bytes: int, family: str = "",
) -> None:
    log = audit.get_audit_log()
    record: dict[str, Any] = {
        "ts": audit.utc_timestamp(),
        "event": "tool_call",
        "consumer": _consumer(request),
        "transport": "http",
        "tool": RESOLVE_TOOL,
        "args_sha256": log.hash_arguments({"local": target, "format": fmt or ""}),
        "status": status,
        "http_status": http_status,
        "result_bytes": result_bytes,
        "truncated": False,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    if family:
        record["family"] = family
    record.update(provenance.corpus_identity())
    record["server_version"] = provenance.SERVER_VERSION
    log.emit(record)


def _json(status: int, body: dict[str, Any], headers: dict[str, str] | None = None):
    from starlette.responses import Response

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return Response(payload, status_code=status, media_type="application/json",
                    headers=_base_headers(headers))


def _not_acceptable():
    return _json(406, {
        "error": "not_acceptable",
        "supported": [mt for mt, _ in FORMATS.values()],
        "hint": "send one of the supported types in Accept, or ?format=jsonld|ttl|nt|rdf|html",
    })


async def _render(
    request: Any, started: float, *, kind: str, target: str, fmt: str,
    build: Callable[[], tuple[str | bytes, str]], family: str,
):
    """Shared tail: ETag/304, render off the event loop, headers, audit line."""
    from starlette.responses import Response

    etag = _etag(kind, target, fmt)
    if etag in (request.headers.get("if-none-match") or ""):
        _emit(request, started, target=target, fmt=fmt, status="not_modified",
              http_status=304, result_bytes=0, family=family)
        return Response(status_code=304, headers=_base_headers({"ETag": etag}))
    try:
        body, media = await anyio.to_thread.run_sync(build)
    except RdfUnavailable:
        _emit(request, started, target=target, fmt=fmt, status="rdf_unavailable",
              http_status=406, result_bytes=0, family=family)
        return _json(406, {
            "error": "not_acceptable",
            "detail": "RDF serialisation is unavailable on this server (rdflib missing)",
            "supported": ["application/ld+json", "text/html"],
        })
    payload = body.encode("utf-8") if isinstance(body, str) else body
    canonical = resolver.NAMESPACE + target
    headers = _base_headers({"ETag": etag, "Link": f'<{canonical}>; rel="canonical"'})
    _emit(request, started, target=target, fmt=fmt, status="ok", http_status=200,
          result_bytes=len(payload), family=family)
    return Response(payload, status_code=200, media_type=media, headers=headers)


def _media(fmt: str) -> str:
    media = FORMATS[fmt][0]
    return media + "; charset=utf-8" if media.startswith("text/") or fmt == "jsonld" else media


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def describe(request: Any):
    """``GET /id/{local}``: one node, content-negotiated."""
    from starlette.responses import Response

    started = time.perf_counter()
    local = request.path_params.get("local", "")
    fmt = negotiate(request.headers.get("accept"), request.query_params.get("format"))
    if fmt is None:
        _emit(request, started, target=local, fmt=None, status="not_acceptable",
              http_status=406, result_bytes=0)
        return _not_acceptable()
    res = await anyio.to_thread.run_sync(resolver.resolve, local)
    if res is None:
        alias = (
            await anyio.to_thread.run_sync(resolver.alias_target, local)
            if resolver.valid_local_name(local) else None
        )
        if alias:
            location = _id_href(alias)
            if request.query_params.get("format"):
                location += "?format=" + quote(request.query_params["format"], safe="")
            _emit(request, started, target=local, fmt=fmt, status="alias",
                  http_status=303, result_bytes=0)
            return Response(status_code=303, headers=_base_headers({"Location": location}))
        _emit(request, started, target=local, fmt=fmt, status="not_found",
              http_status=404, result_bytes=0)
        return _json(404, {
            "error": "not_found",
            "local_name": local,
            "iri": resolver.NAMESPACE + local,
            "hint": "no node with this local name in the served corpus",
        })

    def build() -> tuple[str, str]:
        if fmt == "html":
            return render_node_html(res), _media(fmt)
        document = resolver.compact_document(res)
        if fmt == "jsonld":
            return resolver.dumps(document), _media(fmt)
        return to_rdf(document, FORMATS[fmt][1] or fmt), _media(fmt)

    return await _render(request, started, kind="id", target=local, fmt=fmt,
                         build=build, family=res.family)


async def id_index(request: Any):
    """``GET /id`` and ``GET /id/``: no name, so 404 (with a hint)."""
    started = time.perf_counter()
    _emit(request, started, target="", fmt=None, status="not_found",
          http_status=404, result_bytes=0)
    return _json(404, {
        "error": "not_found",
        "hint": "GET /id/<local name>, e.g. /id/KARIST_2_Osa2_Par_141; the vocabulary is at /vocabulary",
    })


async def vocabulary(request: Any):
    """``GET /vocabulary``: the controlled vocabulary (T-Box), content-negotiated."""
    started = time.perf_counter()
    fmt = negotiate(request.headers.get("accept"), request.query_params.get("format"))
    if fmt is None:
        _emit(request, started, target=resolver.VOCABULARY_LOCAL, fmt=None,
              status="not_acceptable", http_status=406, result_bytes=0)
        return _not_acceptable()
    if not resolver.vocabulary_document():
        _emit(request, started, target=resolver.VOCABULARY_LOCAL, fmt=fmt,
              status="not_found", http_status=404, result_bytes=0)
        return _json(404, {"error": "not_found", "detail": "controlled vocabulary missing"})

    def build() -> tuple[str, str]:
        if fmt == "html":
            return render_vocabulary_html(), _media(fmt)
        if fmt == "jsonld":
            return resolver.dumps(resolver.vocabulary_document()), _media(fmt)
        return _vocabulary_rdf(FORMATS[fmt][1] or fmt), _media(fmt)

    return await _render(request, started, kind="vocabulary", target=resolver.VOCABULARY_LOCAL,
                         fmt=fmt, build=build, family="vocabulary")


# ---------------------------------------------------------------------------
# Mounting
# ---------------------------------------------------------------------------
def enabled(environ: dict[str, str] | None = None) -> bool:
    """``ESTLEG_RESOLVER`` = ``off``/``0``/``false`` disables the routes (default on)."""
    env = os.environ if environ is None else environ
    return env.get("ESTLEG_RESOLVER", "").strip().lower() not in {"0", "off", "false", "no"}


def mount(app: Any) -> None:
    """Append the resolver routes to a Starlette app (before the gate wraps it)."""
    from starlette.routing import Route

    app.routes.append(Route("/id/{local}", describe, methods=["GET"]))
    app.routes.append(Route("/id/", id_index, methods=["GET"]))
    app.routes.append(Route("/id", id_index, methods=["GET"]))
    app.routes.append(Route("/vocabulary", vocabulary, methods=["GET"]))


__all__ = [
    "FORMATS",
    "RESOLVE_TOOL",
    "RdfUnavailable",
    "citation_links",
    "describe",
    "enabled",
    "mount",
    "negotiate",
    "render_node_html",
    "render_vocabulary_html",
    "to_rdf",
    "vocabulary",
]
