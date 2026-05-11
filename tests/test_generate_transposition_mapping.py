from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_transposition_mapping as mod  # noqa: E402

REPO_ROOT_FOR_TESTS = Path(__file__).resolve().parent.parent


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_directive_celex_is_not_synthesized() -> None:
    assert mod.resolve_directive_iri("32000L0001", {}) is None
    assert mod.resolve_directive_iri(
        "32000L0001", {"32000L0001": "estleg:EU_32000L0001"}
    ) == "estleg:EU_32000L0001"


def test_transposition_schema_is_act_level() -> None:
    schema = mod.generate_schema()
    nodes = {node["@id"]: node for node in schema["@graph"]}

    assert nodes["estleg:transposesDirective"]["rdfs:domain"] == {"@id": "estleg:Act"}
    assert nodes["estleg:transposedBy"]["rdfs:range"] == {"@id": "estleg:Act"}
    assert nodes["estleg:transpositionStatus"]["rdfs:domain"] == {"@id": "estleg:Act"}


def test_law_target_iri_uses_real_ontology_node(tmp_path: Path) -> None:
    law_path = tmp_path / "law_peep.json"
    _write_json(
        law_path,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:AS_Map_2026",
                    "@type": ["owl:Ontology", "estleg:Act"],
                },
                {
                    "@id": "estleg:AS_Par_1",
                    "@type": ["owl:NamedIndividual"],
                },
            ],
        },
    )

    assert mod.get_law_transposition_target_iri(law_path) == "estleg:AS_Map_2026"


def test_inverse_transposed_by_links_to_real_law_node(
    tmp_path: Path, monkeypatch
) -> None:
    eurlex_dir = tmp_path / "eurlex"
    directives_path = eurlex_dir / "eurlex_directives_peep.json"
    _write_json(
        directives_path,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:EU_32000L0001",
                    "@type": ["estleg:EULegislation"],
                    "estleg:celexNumber": "32000L0001",
                }
            ],
        },
    )
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex_dir)

    updated = mod.update_directive_file(
        {"32000L0001": ["estleg:AS_Map_2026"]}
    )

    assert updated == 1
    doc = json.loads(directives_path.read_text(encoding="utf-8"))
    assert doc["@graph"][0]["estleg:transposedBy"] == [
        {"@id": "estleg:AS_Map_2026"}
    ]


# ---------------------------------------------------------------------------
# Regression tests for #129 — zero-fetch handling, retry, current report shape.
# ---------------------------------------------------------------------------


def test_sparql_query_with_retry_reraises_as_runtime_error(monkeypatch):
    """A persistently failing SPARQL query raises RuntimeError (#129).

    Pagination must not silently truncate on a transient error — the
    retry wrapper re-raises so the caller can either ``break`` (under
    ``--allow-partial``) or propagate to the exit code.
    """
    calls = {"n": 0}

    def _boom(_query):
        calls["n"] += 1
        raise ValueError("simulated EUR-Lex 5xx")

    monkeypatch.setattr(mod, "sparql_query", _boom)

    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        mod.sparql_query_with_retry("ASK {}", retries=3, backoff=0.0)
    assert calls["n"] == 3


def test_sparql_query_with_retry_succeeds_after_transient_failure(monkeypatch):
    """The wrapper retries and returns on a later success."""
    attempts = {"n": 0}

    def _flaky(_query):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise ConnectionError("transient")
        return [{"x": {"value": "ok"}}]

    monkeypatch.setattr(mod, "sparql_query", _flaky)

    result = mod.sparql_query_with_retry("ASK {}", retries=3, backoff=0.0)
    assert result == [{"x": {"value": "ok"}}]
    assert attempts["n"] == 2


def test_fetch_transposition_measures_raises_without_allow_partial(monkeypatch):
    """A terminal SPARQL failure propagates when ``allow_partial`` is False (#129)."""
    def _boom(_query, **_kwargs):
        raise RuntimeError("sparql down")

    monkeypatch.setattr(mod, "sparql_query_with_retry", _boom)

    with pytest.raises(RuntimeError):
        mod.fetch_transposition_measures(allow_partial=False)


def test_fetch_transposition_measures_breaks_with_allow_partial(monkeypatch):
    """Under ``allow_partial`` a terminal failure stops the sweep and
    reports ``partial=True`` instead of raising (#129)."""
    def _boom(_query, **_kwargs):
        raise RuntimeError("sparql down")

    monkeypatch.setattr(mod, "sparql_query_with_retry", _boom)

    items, partial = mod.fetch_transposition_measures(allow_partial=True)
    assert items == []
    assert partial is True


def test_zero_fetch_without_allow_empty_exits_nonzero(tmp_path, monkeypatch):
    """``main()`` exits non-zero on a zero-measure fetch unless --allow-empty
    is given — and it does NOT overwrite the existing report (#129).
    """
    krr = tmp_path / "krr_outputs"
    eurlex = krr / "eurlex"
    krr.mkdir()
    (krr / "INDEX.json").write_text(json.dumps({"total_laws": 0, "laws": []}), encoding="utf-8")
    _write_json(eurlex / "eurlex_directives_peep.json", {"@context": mod.CONTEXT, "@graph": []})
    # An existing (legacy) report that must be left untouched on failure.
    legacy = {"generated": "2026-03-21", "source": "x", "total_measures_fetched": 0,
              "matched": 0, "unmatched": 0, "mappings": []}
    (krr / "transposition_mapping.json").write_text(json.dumps(legacy), encoding="utf-8")

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    monkeypatch.setattr(mod, "fetch_transposition_measures", lambda **_k: ([], False))
    monkeypatch.setattr(sys, "argv", ["generate_transposition_mapping.py"])

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code not in (0, None)
    # Report unchanged.
    assert json.loads((krr / "transposition_mapping.json").read_text(encoding="utf-8")) == legacy


def test_zero_fetch_with_allow_empty_writes_documented_empty_report(tmp_path, monkeypatch):
    """``--allow-empty`` records a current-shape documented-empty report
    and does not mutate any law peep files (#129)."""
    krr = tmp_path / "krr_outputs"
    eurlex = krr / "eurlex"
    krr.mkdir()
    (krr / "INDEX.json").write_text(json.dumps({"total_laws": 0, "laws": []}), encoding="utf-8")
    _write_json(eurlex / "eurlex_directives_peep.json", {"@context": mod.CONTEXT, "@graph": []})

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    monkeypatch.setattr(mod, "fetch_transposition_measures", lambda **_k: ([], False))
    monkeypatch.setattr(sys, "argv", ["generate_transposition_mapping.py", "--allow-empty"])

    rc = mod.main()
    assert rc in (0, None)

    report = json.loads((krr / "transposition_mapping.json").read_text(encoding="utf-8"))
    assert report["documented_empty"] is True
    assert report["mappings"] == []
    assert report["country"] == "EST"
    # Current-shape keys present.
    for key in ("total_matched", "total_unmatched", "unique_directives", "unique_laws"):
        assert key in report


def test_zero_fetch_allow_empty_partial_exits_two(tmp_path, monkeypatch):
    """A partial sweep that yielded zero measures + --allow-empty still
    writes the documented-empty report but exits 2 to flag the partial run."""
    krr = tmp_path / "krr_outputs"
    eurlex = krr / "eurlex"
    krr.mkdir()
    (krr / "INDEX.json").write_text(json.dumps({"total_laws": 0, "laws": []}), encoding="utf-8")
    _write_json(eurlex / "eurlex_directives_peep.json", {"@context": mod.CONTEXT, "@graph": []})

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    monkeypatch.setattr(mod, "fetch_transposition_measures", lambda **_k: ([], True))
    monkeypatch.setattr(
        sys, "argv",
        ["generate_transposition_mapping.py", "--allow-empty", "--allow-partial"],
    )

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 2
    report = json.loads((krr / "transposition_mapping.json").read_text(encoding="utf-8"))
    assert report["documented_empty"] is True
    assert report["partial"] is True


def test_transposition_query_has_order_by():
    """The pagination query carries an ORDER BY so OFFSET is stable (#183)."""
    import inspect

    src = inspect.getsource(mod.fetch_transposition_measures)
    assert "ORDER BY" in src


# ---------------------------------------------------------------------------
# GET → POST: the Publications Office Virtuoso endpoint 202s on GET (#129/#96).
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, raise_on_status=False):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {
            "results": {"bindings": [{"x": {"value": "ok"}}]}
        }
        self._raise = raise_on_status

    def raise_for_status(self):
        if self._raise:
            raise RuntimeError("HTTP error")

    def json(self):
        if self._json is _RAISE_VALUE_ERROR:
            raise ValueError("not JSON")
        return self._json


_RAISE_VALUE_ERROR = object()


def test_sparql_query_posts_not_gets(monkeypatch):
    """``sparql_query`` must POST the query (form-encoded) — never GET it.

    The Virtuoso endpoint answers GET for non-trivial queries with HTTP 202
    + an empty body, which ``raise_for_status()`` doesn't flag, so a GET
    helper silently returns ``[]`` (the real cause of #129/#96).
    """
    posted = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        posted["url"] = url
        posted["data"] = data
        posted["headers"] = headers
        return _FakeResp()

    def fail_get(*_a, **_k):  # pragma: no cover - must not be reached
        raise AssertionError("sparql_query must not use requests.get")

    monkeypatch.setattr(mod.requests, "post", fake_post)
    monkeypatch.setattr(mod.requests, "get", fail_get)

    result = mod.sparql_query("ASK {}")
    assert result == [{"x": {"value": "ok"}}]
    assert posted["url"] == mod.SPARQL_ENDPOINT
    assert posted["data"] == {"query": "ASK {}"}
    assert posted["headers"]["Accept"] == "application/sparql-results+json"
    assert "x-www-form-urlencoded" in posted["headers"]["Content-Type"]


def test_sparql_query_202_triggers_retry(monkeypatch):
    """A bare HTTP 202 (empty body) from the endpoint must raise so the
    retry layer reacts rather than treating it as an empty result set."""
    calls = {"n": 0}

    def fake_post(url, data=None, headers=None, timeout=None):
        calls["n"] += 1
        return _FakeResp(status_code=202, json_data={})

    monkeypatch.setattr(mod.requests, "post", fake_post)

    # Direct call raises.
    with pytest.raises(RuntimeError, match="202"):
        mod.sparql_query("ASK {}")

    # And the retry wrapper re-raises after exhausting attempts.
    calls["n"] = 0
    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        mod.sparql_query_with_retry("ASK {}", retries=3, backoff=0.0)
    assert calls["n"] == 3


def test_sparql_query_non_json_body_raises(monkeypatch):
    """A 200 with a non-JSON body must raise (not crash with a bare ValueError)."""
    def fake_post(url, data=None, headers=None, timeout=None):
        return _FakeResp(status_code=200, json_data=_RAISE_VALUE_ERROR)

    monkeypatch.setattr(mod.requests, "post", fake_post)
    with pytest.raises(RuntimeError, match="non-JSON"):
        mod.sparql_query("ASK {}")


# ---------------------------------------------------------------------------
# #129 — a successful (mocked) fetch populates transposition_mapping.json and
# satisfies validate_all.validate_transposition_mapping.
# ---------------------------------------------------------------------------


def test_successful_fetch_populates_mapping_and_passes_gate(tmp_path, monkeypatch):
    """End-to-end (mocked SPARQL): a non-empty NIM fetch yields a non-empty,
    current-shape transposition_mapping.json that validate_all accepts (#129)."""
    krr = tmp_path / "krr_outputs"
    eurlex = krr / "eurlex"
    krr.mkdir()
    eurlex.mkdir()

    # One matchable Estonian law: "Tubakaseadus", file tubakaseadus_peep.json.
    law_file = krr / "tubakaseadus_peep.json"
    _write_json(
        law_file,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:TUBAKA_Map_2026", "@type": ["owl:Ontology", "estleg:Act"],
                 "estleg:sourceAct": "Tubakaseadus"},
            ],
        },
    )
    _write_json(
        krr / "INDEX.json",
        {"total_laws": 1, "laws": [{"name": "tubakaseadus", "files": ["tubakaseadus_peep.json"]}]},
    )
    # A directive node so the CELEX resolves to a real IRI.
    _write_json(
        eurlex / "eurlex_directives_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:EU_32003L0033", "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
                 "estleg:celexNumber": "32003L0033"},
            ],
        },
    )

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)  # for the summary's relative_to()
    # Mocked SPARQL: NIM fetch returns one EST measure; deadline fetch returns one row.
    monkeypatch.setattr(
        mod, "fetch_transposition_measures",
        lambda **_k: ([{"celex_dir": "32003L0033", "directive_uri": "http://x/dir",
                        "title_nat": "TUBAKASEADUS1"}], False),
    )
    monkeypatch.setattr(
        mod, "fetch_directive_deadlines",
        lambda **_k: ({"32003L0033": "2004-07-31"}, False),
    )
    monkeypatch.setattr(sys, "argv", ["generate_transposition_mapping.py"])

    rc = mod.main()
    assert rc in (0, None)

    report = json.loads((krr / "transposition_mapping.json").read_text(encoding="utf-8"))
    assert report["documented_empty"] is False
    assert report["total_measures_fetched"] == 1
    assert len(report["mappings"]) == 1
    assert report["mappings"][0]["directive_celex"] == "32003L0033"
    assert report["mappings"][0]["matched_law_name"] == "tubakaseadus"
    assert report["directive_deadline_nodes_updated"] == 1

    # The law peep got estleg:transposesDirective pointing at the real directive IRI.
    law_doc = json.loads(law_file.read_text(encoding="utf-8"))
    act_node = law_doc["@graph"][0]
    assert {"@id": "estleg:EU_32003L0033"} in act_node["estleg:transposesDirective"]
    # The directive node got estleg:transposedBy + estleg:transpositionDeadline.
    dir_doc = json.loads((eurlex / "eurlex_directives_peep.json").read_text(encoding="utf-8"))
    dir_node = dir_doc["@graph"][0]
    assert {"@id": "estleg:TUBAKA_Map_2026"} in dir_node["estleg:transposedBy"]
    assert dir_node["estleg:transpositionDeadline"] == {"@value": "2004-07-31", "@type": "xsd:date"}

    # validate_all's gate accepts the populated report (no error recorded).
    import importlib
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    validate_all = importlib.import_module("validate_all")
    validate_all.errors.clear()
    validate_all.validate_transposition_mapping(krr_dir=krr)
    assert validate_all.errors == []


# ---------------------------------------------------------------------------
# #96 — transposition deadline on EU directive nodes.
# ---------------------------------------------------------------------------


def test_update_directive_deadlines_adds_xsd_date(tmp_path, monkeypatch):
    """Directive nodes with a known deadline get estleg:transpositionDeadline
    (xsd:date typed); nodes without a deadline get nothing (it's optional, #96)."""
    eurlex = tmp_path / "eurlex"
    _write_json(
        eurlex / "eurlex_directives_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:EU_32011L0083", "@type": ["estleg:EULegislation"],
                 "estleg:celexNumber": "32011L0083"},
                {"@id": "estleg:EU_31980L0766", "@type": ["estleg:EULegislation"],
                 "estleg:celexNumber": "31980L0766"},
            ],
        },
    )
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)

    updated = mod.update_directive_deadlines({"32011L0083": "2013-12-13"})
    assert updated == 1

    doc = json.loads((eurlex / "eurlex_directives_peep.json").read_text(encoding="utf-8"))
    nodes = {n["@id"]: n for n in doc["@graph"]}
    assert nodes["estleg:EU_32011L0083"]["estleg:transpositionDeadline"] == {
        "@value": "2013-12-13", "@type": "xsd:date",
    }
    # The directive without a deadline must NOT carry the property.
    assert "estleg:transpositionDeadline" not in nodes["estleg:EU_31980L0766"]

    # Idempotent re-application is a no-op.
    assert mod.update_directive_deadlines({"32011L0083": "2013-12-13"}) == 0


def test_clear_directive_deadlines_strips_property(tmp_path, monkeypatch):
    eurlex = tmp_path / "eurlex"
    _write_json(
        eurlex / "eurlex_directives_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:EU_32011L0083", "@type": ["estleg:EULegislation"],
                 "estleg:celexNumber": "32011L0083",
                 "estleg:transpositionDeadline": {"@value": "2013-12-13", "@type": "xsd:date"}},
            ],
        },
    )
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    assert mod.clear_directive_deadlines() == 1
    doc = json.loads((eurlex / "eurlex_directives_peep.json").read_text(encoding="utf-8"))
    assert "estleg:transpositionDeadline" not in doc["@graph"][0]


def test_fetch_directive_deadlines_picks_earliest(monkeypatch):
    """When a directive has several deadline rows, the earliest wins."""
    def fake_query(_q):
        return [
            {"celex": {"value": "32007L0073"}, "deadline": {"value": "2007-12-18"}},
            {"celex": {"value": "31980L0766"}, "deadline": {"value": "1980-10-01"}},
        ]

    monkeypatch.setattr(mod, "sparql_query_with_retry", lambda q: fake_query(q))
    deadlines, partial = mod.fetch_directive_deadlines()
    assert partial is False
    assert deadlines == {"32007L0073": "2007-12-18", "31980L0766": "1980-10-01"}


def test_normalize_text_strips_marks_and_trailing_digits():
    """``õ``→``o`` and trailing consolidation digits are dropped so NIM
    titles match the filename-derived law-index keys."""
    assert mod.normalize_text("AUDIITORTEGEVUSE SEADUS1") == "audiitortegevuse seadus"
    assert mod.normalize_text("Asjaõigusseadus1") == "asjaoigusseadus"
    assert mod.normalize_text("TUBAKASEADUS") == "tubakaseadus"
    assert mod.normalize_text("Asjaõigusseadus") == mod.normalize_text("asjaoigusseadus")


def test_transposition_schema_does_not_redeclare_deadline_property():
    """estleg:transpositionDeadline (#96) is declared once, in
    controlled_vocabulary.jsonld — NOT in this per-layer schema (declaring
    the same @id in two files would trip validate_all's @id-uniqueness gate)."""
    schema = mod.generate_schema()
    ids = {n["@id"] for n in schema["@graph"]}
    assert "estleg:transpositionDeadline" not in ids
    # The act-level transposition props *are* still here.
    assert "estleg:transposesDirective" in ids
    assert "estleg:transposedBy" in ids


def test_controlled_vocabulary_declares_transposition_deadline():
    """The corpus-wide vocabulary declares estleg:transpositionDeadline (#96)."""
    vocab_path = REPO_ROOT_FOR_TESTS / "krr_outputs" / "controlled_vocabulary.jsonld"
    doc = json.loads(vocab_path.read_text(encoding="utf-8"))
    nodes = {n.get("@id"): n for n in doc.get("@graph", []) if isinstance(n, dict)}
    assert "estleg:transpositionDeadline" in nodes
    assert "owl:DatatypeProperty" in nodes["estleg:transpositionDeadline"]["@type"]
    assert nodes["estleg:transpositionDeadline"]["rdfs:range"] == {"@id": "xsd:date"}
