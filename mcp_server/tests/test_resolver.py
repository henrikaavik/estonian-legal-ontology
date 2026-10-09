"""w3id resolver pilot (#728): ``/id/{local}`` and ``/vocabulary`` on the HTTP app.

The negotiation and gate tests need no corpus; the route tests resolve real
nodes and are skipped when ``krr_outputs/INDEX.json`` is absent.
"""

from __future__ import annotations

import io
import json

import pytest
from starlette.testclient import TestClient

from estleg_mcp import audit, data, resolver, resolver_web, security, server

_ENV = (
    "ESTLEG_TOKEN",
    "ESTLEG_TOKENS",
    "ESTLEG_ALLOW_ANONYMOUS_HTTP",
    "ESTLEG_RATE_LIMIT",
    "ESTLEG_RATE_BURST",
    "ESTLEG_RESOLVER",
    "ESTLEG_RESOLVER_RATE_LIMIT",
    "ESTLEG_RESOLVER_RATE_BURST",
    "ESTLEG_ALLOWED_HOSTS",
)
AUTH = {"Authorization": "Bearer t"}
PROVISION = "KARIST_2_Osa2_Par_141"
REGULATION = "Reg_1057801_Map"
INSTITUTION = "Institution_abiminister"

try:
    data.corpus_root()
    _CORPUS = True
except FileNotFoundError:
    _CORPUS = False
needs_corpus = pytest.mark.skipif(not _CORPUS, reason="corpus (krr_outputs/INDEX.json) not found")
try:
    import rdflib  # noqa: F401  (RDF serialisations need it; JSON-LD/HTML do not)

    _RDFLIB = True
except ImportError:
    _RDFLIB = False
needs_rdflib = pytest.mark.skipif(not _RDFLIB, reason="rdflib not installed")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server, "mcp", server.MCPServer("estleg-test"))
    monkeypatch.setenv("ESTLEG_TOKEN", "t")


@pytest.fixture
def sink(monkeypatch) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(audit, "_AUDIT", audit.AuditLog(out))
    return out


def _lines(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def _client() -> TestClient:
    return TestClient(server._build_http_app())


# ---------------------------------------------------------------------------
# Content negotiation (no corpus)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        (None, "html"),
        ("", "html"),
        ("*/*", "html"),
        ("text/turtle", "turtle"),
        ("application/ld+json", "jsonld"),
        ("application/json", "jsonld"),
        ("application/n-triples", "ntriples"),
        ("application/rdf+xml", "rdfxml"),
        ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "html"),
        ("text/turtle;q=0.5, application/ld+json", "jsonld"),
        ("application/ld+json;q=0.4, text/turtle;q=0.9, */*;q=0.1", "turtle"),
        ("*/*, text/turtle", "turtle"),  # specific beats wildcard at equal q
        ("image/png", None),
        ("text/turtle;q=0", None),
    ],
)
def test_negotiate(accept, expected) -> None:
    assert resolver_web.negotiate(accept) == expected


def test_format_param_overrides_accept() -> None:
    assert resolver_web.negotiate("text/html", "ttl") == "turtle"
    assert resolver_web.negotiate("text/html", "jsonld") == "jsonld"
    assert resolver_web.negotiate("text/html", "bogus") is None


def test_explicit_format_rejection_beats_wildcard():
    assert resolver_web.negotiate("text/html;q=0, */*;q=0.5") != "html"
    assert resolver_web.negotiate("text/*;q=0, application/*;q=0, */*;q=1") is None
    assert resolver_web.negotiate("text/turtle;q=nan") is None


def test_draft_description_uses_its_file_after_phase_changes(tmp_path, monkeypatch):
    directory = tmp_path / "eelnoud"
    directory.mkdir()
    node = {"@id": "estleg:Draft_TEST", "@type": "estleg:DraftLegislation",
            "estleg:legislativePhase": {"@id": "estleg:Phase_Enacted"},
            "estleg:hasProcessStep": [{"@id": "estleg:Draft_TEST_Step_1"}]}
    doc = {"@context": {"test": "https://example.org/"}, "@graph": [node,
           {"@id": "estleg:Draft_TEST_Step_1", "rdfs:label": "Enacted"}]}
    (directory / "eelnoud_submission_peep.json").write_text(json.dumps(doc))
    monkeypatch.setattr(data, "krr_dir", lambda: tmp_path)
    monkeypatch.setattr(data, "draft_info", lambda _iri: node)
    resolver.cache_clear()
    try:
        result = resolver.resolve("Draft_TEST")
        assert result.source == "eelnoud/eelnoud_submission_peep.json"
        assert result.neighbours["estleg:Draft_TEST_Step_1"] == "Enacted"
    finally:
        resolver.cache_clear()


@pytest.mark.parametrize(
    ("path", "open_"),
    [
        ("/id/KARIST_2_Map", True),
        ("/id/", True),
        ("/id", True),
        ("/vocabulary", True),
        ("/id/a/b", False),
        ("/id/../mcp", False),
        ("/vocabulary/x", False),
        ("/mcp", False),
        ("/identity", False),
    ],
)
def test_resolver_path_pattern(path: str, open_: bool) -> None:
    assert bool(security.RESOLVER_PATH.match(path)) is open_


def test_invalid_local_names_never_touch_the_corpus() -> None:
    for name in ("", "a b", "a/b", "../x", "x" * 400, "<script>"):
        assert not resolver.valid_local_name(name)
        assert resolver.resolve(name) is None


# ---------------------------------------------------------------------------
# Gate: anonymous resolver, fail-closed everything else (no corpus needed)
# ---------------------------------------------------------------------------
def test_resolver_anonymous_while_mcp_stays_401(monkeypatch, sink) -> None:
    monkeypatch.setattr(resolver, "resolve", lambda _local: None)
    monkeypatch.setattr(resolver, "alias_target", lambda _local: None)
    with _client() as client:
        missing = client.get("/id/No_Such_Node")
        assert missing.status_code == 404  # reached the route: no token needed
        assert missing.json()["error"] == "not_found"
        assert missing.json()["iri"] == "https://w3id.org/estleg/No_Such_Node"
        assert client.get("/id/").status_code == 404
        assert client.get("/id").status_code == 404
        assert client.post("/mcp", json={}).status_code == 401
        assert client.get("/mcp").status_code == 401
        # Only GET/HEAD are open; any other method on a resolver path is gated.
        assert client.post("/id/X", json={}).status_code == 401
        # A name with a decoded slash is not a resolver path.
        assert client.get("/id/a%2Fb").status_code == 401
    calls = [r for r in _lines(sink) if r.get("tool") == "resolve"]
    assert calls and all(r["consumer"] == "anonymous" for r in calls)
    assert calls[0]["status"] == "not_found" and calls[0]["http_status"] == 404
    assert "No_Such_Node" not in json.dumps(calls[0])  # arguments are hashed


def test_resolver_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ESTLEG_RESOLVER", "off")
    with _client() as client:
        assert client.get("/id/KARIST_2_Map").status_code == 401
        assert client.get("/vocabulary").status_code == 401


def test_resolver_has_its_own_bucket(monkeypatch, sink) -> None:
    monkeypatch.setattr(resolver, "resolve", lambda _local: None)
    monkeypatch.setattr(resolver, "alias_target", lambda _local: None)
    monkeypatch.setenv("ESTLEG_RATE_LIMIT", "100/minute")
    monkeypatch.setenv("ESTLEG_RESOLVER_RATE_LIMIT", "2/hour")
    with _client() as client:
        assert client.get("/id/A").status_code == 404
        assert client.get("/id/B").status_code == 404
        limited = client.get("/id/C")
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0
        # The crawler exhausted the resolver bucket, not the token holder's.
        assert client.post("/mcp", json={}, headers=AUTH).status_code not in (401, 429)
    events = [r for r in _lines(sink) if r["event"] == "rate_limited"]
    assert events and events[0]["consumer"] == "anonymous" and events[0]["tool"] == "resolve"


def test_resolver_falls_back_to_shared_limit(monkeypatch) -> None:
    monkeypatch.setattr(resolver, "resolve", lambda _local: None)
    monkeypatch.setattr(resolver, "alias_target", lambda _local: None)
    monkeypatch.setenv("ESTLEG_RATE_LIMIT", "1/hour")
    with _client() as client:
        assert client.get("/id/A").status_code == 404
        assert client.get("/id/B").status_code == 429
        # Separate bucket: the token consumer still has its one request.
        assert client.post("/mcp", json={}, headers=AUTH).status_code != 429


def test_bad_resolver_rate_limit_names_its_variable(monkeypatch) -> None:
    monkeypatch.setenv("ESTLEG_RESOLVER_RATE_LIMIT", "lots")
    with pytest.raises(ValueError, match="ESTLEG_RESOLVER_RATE_LIMIT"):
        server._build_http_app()


# ---------------------------------------------------------------------------
# Routes against the real corpus
# ---------------------------------------------------------------------------
@needs_corpus
@needs_rdflib
def test_provision_jsonld_turtle_html(sink) -> None:
    import rdflib
    iri = f"https://w3id.org/estleg/{PROVISION}"
    with _client() as client:
        doc = client.get(f"/id/{PROVISION}", headers={"Accept": "application/ld+json"})
        assert doc.status_code == 200
        assert doc.headers["content-type"].startswith("application/ld+json")
        assert doc.headers["vary"] == "Accept"
        assert doc.headers["access-control-allow-origin"] == "*"
        assert "max-age" in doc.headers["cache-control"]
        assert doc.headers["etag"].startswith('W/"')
        body = doc.json()
        assert body["@context"]["estleg"] == "https://w3id.org/estleg/"
        node = body["@graph"][0]
        assert node["@id"] == f"estleg:{PROVISION}"
        assert "estleg:LegalProvision" in node["@type"]
        assert node["estleg:partOfAct"] == {"@id": "estleg:KARIST_2_Map"}
        # Neighbours are @id references with a label only, never full nodes.
        assert all(set(n) <= {"@id", "rdfs:label"} for n in body["@graph"][1:])

        ttl = client.get(f"/id/{PROVISION}", headers={"Accept": "text/turtle"})
        assert ttl.status_code == 200
        assert ttl.headers["content-type"].startswith("text/turtle")
        graph = rdflib.Graph().parse(data=ttl.text, format="turtle")
        subject = rdflib.URIRef(iri)
        assert (subject, rdflib.RDF.type,
                rdflib.URIRef("https://w3id.org/estleg/LegalProvision")) in graph
        assert (subject, rdflib.URIRef("https://w3id.org/estleg/partOfAct"),
                rdflib.URIRef("https://w3id.org/estleg/KARIST_2_Map")) in graph

        nt = client.get(f"/id/{PROVISION}?format=nt")
        assert nt.headers["content-type"].startswith("application/n-triples")
        assert f"<{iri}>" in nt.text

        page = client.get(f"/id/{PROVISION}", headers={"Accept": "text/html"})
        assert page.status_code == 200
        text = page.text
        assert "§ 141. Vägistamine" in text
        assert f'<link rel="canonical" href="{iri}">' in text
        assert f'<link rel="alternate" type="text/turtle" href="/id/{PROVISION}?format=ttl">' in text
        assert 'type="application/ld+json"' in text
        assert 'href="/id/KARIST_2_Map"' in text  # outgoing link as /id/ anchor
        assert 'href="/vocabulary#LegalProvision"' in text
    calls = [r for r in _lines(sink) if r.get("tool") == "resolve"]
    assert len(calls) == 4
    assert {r["status"] for r in calls} == {"ok"} and {r["family"] for r in calls} == {"law"}
    assert all(r["consumer"] == "anonymous" and r["result_bytes"] > 0 for r in calls)


@needs_corpus
@needs_rdflib
def test_regulation_root(sink) -> None:
    with _client() as client:
        doc = client.get(f"/id/{REGULATION}", headers={"Accept": "application/ld+json"}).json()
        assert "estleg:Act" in doc["@graph"][0]["@type"]
        ttl = client.get(f"/id/{REGULATION}", headers={"Accept": "text/turtle"})
        assert ttl.status_code == 200 and f"estleg:{REGULATION}" in ttl.text
        page = client.get(f"/id/{REGULATION}").text  # no Accept -> HTML
        rt = data.citation_url_for_iri(f"estleg:{REGULATION}")
        assert rt.startswith("https://www.riigiteataja.ee/")
        assert f'href="{rt}"' in page
    assert {r["family"] for r in _lines(sink) if r.get("tool") == "resolve"} == {"regulation"}


@needs_corpus
@needs_rdflib
def test_institution() -> None:
    with _client() as client:
        doc = client.get(f"/id/{INSTITUTION}?format=jsonld").json()
        assert doc["@graph"][0]["rdfs:label"] == "abiminister"
        ttl = client.get(f"/id/{INSTITUTION}", headers={"Accept": "text/turtle"}).text
        assert "estleg:Institution" in ttl
        page = client.get(f"/id/{INSTITUTION}", headers={"Accept": "text/html"}).text
        assert "<h1>abiminister</h1>" in page
        assert "wikidata.org/entity/Q31273169" in page


@needs_corpus
@needs_rdflib
def test_alias_redirects_to_canonical_name() -> None:
    with _client() as client:
        hop = client.get("/id/KarS_Par_141", headers={"Accept": "text/turtle"},
                         follow_redirects=False)
        assert hop.status_code == 303
        assert hop.headers["location"] == f"/id/{PROVISION}"
        final = client.get("/id/KarS_Par_141", headers={"Accept": "text/turtle"})
        assert final.status_code == 200 and f"estleg:{PROVISION}" in final.text


@needs_corpus
def test_unknown_name_is_json_404_and_406_for_unsupported() -> None:
    with _client() as client:
        missing = client.get("/id/Definitely_Not_A_Node_12345",
                             headers={"Accept": "text/html"})
        assert missing.status_code == 404
        assert missing.headers["content-type"].startswith("application/json")
        assert missing.json()["local_name"] == "Definitely_Not_A_Node_12345"
        refused = client.get(f"/id/{PROVISION}", headers={"Accept": "image/png"})
        assert refused.status_code == 406
        assert "text/turtle" in refused.json()["supported"]


@needs_corpus
@needs_rdflib
def test_etag_revalidation() -> None:
    with _client() as client:
        first = client.get(f"/id/{PROVISION}", headers={"Accept": "text/turtle"})
        again = client.get(f"/id/{PROVISION}", headers={
            "Accept": "text/turtle", "If-None-Match": first.headers["etag"]})
        assert again.status_code == 304 and again.content == b""
        other = client.get(f"/id/{PROVISION}", headers={
            "Accept": "application/ld+json", "If-None-Match": first.headers["etag"]})
        assert other.status_code == 200  # the ETag is per representation


@needs_corpus
@needs_rdflib
def test_vocabulary_endpoint_and_term_ids() -> None:
    import rdflib
    with _client() as client:
        doc = client.get("/vocabulary", headers={"Accept": "application/ld+json"}).json()
        ids = {n["@id"] for n in doc["@graph"]}
        assert "estleg:Act" in ids and "https://w3id.org/estleg/vocabulary" in ids
        ttl = client.get("/vocabulary", headers={"Accept": "text/turtle"})
        graph = rdflib.Graph().parse(data=ttl.text, format="turtle")
        assert (rdflib.URIRef("https://w3id.org/estleg/Act"), rdflib.RDF.type,
                rdflib.OWL.Class) in graph
        page = client.get("/vocabulary", headers={"Accept": "text/html"}).text
        assert '<section id="Act">' in page and 'href="#LegalProvision"' in page
        term = client.get("/id/Act", headers={"Accept": "application/ld+json"}).json()
        assert term["@graph"][0]["@id"] == "estleg:Act"
        scheme_member = client.get("/id/CaseType_Civil?format=ttl")
        assert scheme_member.status_code == 200


@needs_corpus
@pytest.mark.parametrize(
    "local",
    [
        "KARIST_2_Map",
        "Division_KARIST_2_2_9_7",
        "Draft_RAM26_0281",
        "RK_3_21_2176_52",
        "EU_31981L0643",
        "EUCJ_61996CC0117",
        "Reg_1057801_Par_1",
        "RKIOMPU1974_Par_1_v606759",
        "Sanction_ABIPOL_Par_43_fine",
        "Riigikohus_2026_Map",
    ],
)
def test_every_family_resolves(local: str) -> None:
    res = resolver.resolve(local)
    assert res is not None and res.node["@id"] == f"estleg:{local}"


@needs_corpus
def test_html_escapes_corpus_text(monkeypatch) -> None:
    hostile = resolver.Resolution(
        local="X",
        node={"@id": "estleg:X", "rdfs:label": "<script>alert(1)</script>",
              "rdfs:seeAlso": {"@id": "javascript:alert(1)"}},
        context=dict(resolver.DEFAULT_CONTEXT), source="", family="law",
    )
    page = resolver_web.render_node_html(hostile)
    assert "<script>alert" not in page
    assert 'href="javascript:' not in page


def _synthetic() -> resolver.Resolution:
    return resolver.Resolution(
        local="X_Par_1",
        node={"@id": "estleg:X_Par_1", "@type": ["estleg:LegalProvision"],
              "rdfs:label": "§ 1. Test", "estleg:partOfAct": {"@id": "estleg:X_Map"}},
        context=dict(resolver.DEFAULT_CONTEXT), source="", family="law",
        neighbours={"estleg:X_Map": "Test Act"},
    )


def test_without_rdflib_turtle_is_406_and_jsonld_still_served(monkeypatch, sink) -> None:
    monkeypatch.setattr(resolver, "resolve", lambda _local: _synthetic())

    def no_rdflib(_document, _fmt):
        raise resolver_web.RdfUnavailable("rdflib is not installed")

    monkeypatch.setattr(resolver_web, "to_rdf", no_rdflib)
    with _client() as client:
        refused = client.get("/id/X_Par_1", headers={"Accept": "text/turtle"})
        assert refused.status_code == 406
        assert refused.json()["supported"] == ["application/ld+json", "text/html"]
        doc = client.get("/id/X_Par_1", headers={"Accept": "application/ld+json"}).json()
        assert doc["@graph"] == [
            _synthetic().node, {"@id": "estleg:X_Map", "rdfs:label": "Test Act"}]
        page = client.get("/id/X_Par_1").text
        assert 'href="/id/X_Map">Test Act <code>estleg:X_Map</code></a>' in page
    statuses = [r["status"] for r in _lines(sink) if r.get("tool") == "resolve"]
    assert statuses == ["rdf_unavailable", "ok", "ok"]
