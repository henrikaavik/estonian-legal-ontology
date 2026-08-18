from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import eurlex_common  # noqa: E402
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


def test_build_law_index_filters_missing_files_from_stale_index(
    tmp_path: Path, monkeypatch
) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write_json(
        krr / "asjaoigusseadus_osa1_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:AOS_Osa1_1_31",
                    "@type": ["owl:Ontology", "estleg:Act"],
                    "estleg:sourceAct": "Asjaõigusseadus",
                }
            ],
        },
    )
    monkeypatch.setattr(mod, "KRR_DIR", krr)

    index = mod.build_law_index(
        {
            "laws": [
                {
                    "name": "asjaoigusseadus",
                    "files": [
                        "asjaoigusseadus_osa10_peep.json",
                        "asjaoigusseadus_osa1_peep.json",
                    ],
                }
            ]
        }
    )

    entry = index[mod.normalize_text("Asjaõigusseadus")]
    assert entry["files"] == ["asjaoigusseadus_osa1_peep.json"]
    assert entry["source_act"] == "Asjaõigusseadus"


def test_build_law_index_skips_deprecated_act(tmp_path: Path, monkeypatch) -> None:
    """#578: a deprecated/replaced act (the retired volaigusseadus / VOS_* VÕS
    decomposition) is excluded from the transposition law index, so a directive
    cannot fan onto it alongside the canonical volaoigusseadus_* family."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write_json(
        krr / "volaigusseadus_osa3_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:VOS_Osa3_Contract_Obligations_Liability",
                    "@type": ["owl:Ontology", "estleg:Act"],
                    "owl:deprecated": True,
                    "dcterms:isReplacedBy": {"@id": "estleg:volaoigusseadus_Osa3_271_421"},
                    "estleg:sourceAct": "Võlaõigusseadus",
                }
            ],
        },
    )
    monkeypatch.setattr(mod, "KRR_DIR", krr)

    index = mod.build_law_index(
        {"laws": [{"name": "volaigusseadus", "files": ["volaigusseadus_osa3_peep.json"]}]}
    )
    assert index == {}


def test_build_law_index_handles_unreadable_first_file_gracefully(
    tmp_path: Path, monkeypatch
) -> None:
    """A first file that exists but is unreadable/malformed (no parseable
    ``@graph``/``sourceAct``) must not crash ``build_law_index`` — it falls
    back to the filename-derived key with ``source_act`` set to the spaced
    name (covers the try/except in the now-unguarded read)."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    # First file exists but is invalid JSON, so load_json raises.
    (krr / "tubakaseadus_peep.json").write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(mod, "KRR_DIR", krr)

    index = mod.build_law_index(
        {"laws": [{"name": "tubakaseadus", "files": ["tubakaseadus_peep.json"]}]}
    )

    entry = index[mod.normalize_text("tubakaseadus")]
    assert entry["name"] == "tubakaseadus"
    assert entry["files"] == ["tubakaseadus_peep.json"]
    # No sourceAct could be read; the key falls back to the spaced name.
    assert entry["source_act"] == "tubakaseadus"


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
    (krr / "reports").mkdir()
    (krr / "reports" / "transposition_mapping.json").write_text(json.dumps(legacy), encoding="utf-8")

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    monkeypatch.setattr(mod, "fetch_transposition_measures", lambda **_k: ([], False))
    monkeypatch.setattr(sys, "argv", ["generate_transposition_mapping.py"])

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code not in (0, None)
    # Report unchanged.
    assert json.loads((krr / "reports" / "transposition_mapping.json").read_text(encoding="utf-8")) == legacy


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

    report = json.loads((krr / "reports" / "transposition_mapping.json").read_text(encoding="utf-8"))
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
    report = json.loads((krr / "reports" / "transposition_mapping.json").read_text(encoding="utf-8"))
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

    monkeypatch.setattr(eurlex_common.requests, "post", fake_post)
    monkeypatch.setattr(eurlex_common.requests, "get", fail_get)

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

    monkeypatch.setattr(eurlex_common.requests, "post", fake_post)

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

    monkeypatch.setattr(eurlex_common.requests, "post", fake_post)
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

    report = json.loads((krr / "reports" / "transposition_mapping.json").read_text(encoding="utf-8"))
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


# ---------------------------------------------------------------------------
# #265 — whole-word, floored, deterministic fuzzy matching in
# match_title_to_law / match_all_titles_to_laws.
# ---------------------------------------------------------------------------


def _law_index_with(*names: str) -> dict[str, dict]:
    """Build a minimal law_index keyed by normalized name, like build_law_index.

    Each ``name`` is treated as both the law's display ``name`` and its
    ``source_act``; the key is the normalized source_act (the first form
    ``build_law_index`` emits).
    """
    index: dict[str, dict] = {}
    for name in names:
        key = mod.normalize_text(name)
        index[key] = {
            "name": name,
            "files": [f"{key.replace(' ', '_')}_peep.json"],
            "source_act": name,
        }
    return index


def test_raudteeseadus_does_not_match_teeseadus() -> None:
    """The embedded-substring false positive (#265): ``Teeseadus`` must NOT be
    matched inside ``Raudteeseadus`` — word boundaries close the gap.
    """
    index = _law_index_with("Teeseadus", "Raudteeseadus")
    match = mod.match_title_to_law("Raudteeseadus", index)
    assert match is not None
    assert match["name"] == "Raudteeseadus"

    # And a title that really is the Teeseadus still matches it.
    assert mod.match_title_to_law("Teeseadus", index)["name"] == "Teeseadus"


def test_teeseadus_not_matched_when_only_raudteeseadus_in_title() -> None:
    """If the title contains only the longer compound, the shorter embedded
    name must not be returned via the fuzzy fallback (#265)."""
    # Only the shorter, embeddable name is in the index.
    index = _law_index_with("Teeseadus")
    # A title built so no direct/extract match fires, only fuzzy fallback.
    match = mod.match_title_to_law(
        "Raudteeseadus ja muu seadus", index
    )
    assert match is None


def test_match_is_independent_of_dict_order() -> None:
    """The fuzzy match must not depend on law_index iteration order (#265)."""
    # Two laws, one whose name is a whole-word substring scenario. Title
    # references the more specific one as a whole word.
    title = "Liiklusseaduse ja teeseaduse muutmise seadus"
    a = _law_index_with("Teeseadus", "Liiklusseadus")
    b: dict[str, dict] = {}
    for key in reversed(list(a.keys())):  # reversed insertion order
        b[key] = a[key]

    m_a = mod.match_title_to_law(title, a)
    m_b = mod.match_title_to_law(title, b)
    assert m_a is not None and m_b is not None
    # Same deterministic winner regardless of dict order.
    assert m_a["name"] == m_b["name"]
    # The longest whole-word match wins ("Liiklusseadus" > "Teeseadus").
    assert m_a["name"] == "Liiklusseadus"


def test_short_names_below_floor_do_not_match() -> None:
    """A law name shorter than the fuzzy floor must not anchor a match (#265)."""
    index = _law_index_with("Maaseadus")  # 9 chars normalized, < 10 floor
    # No direct/extract full match; fuzzy fallback must reject the short name.
    assert mod.match_title_to_law("Mingi pikem maaseadus tekst siin", index) is None


def test_match_all_titles_to_laws_returns_both_laws() -> None:
    """A combined amending-act title referencing two laws yields BOTH (#288)."""
    index = _law_index_with(
        "Liiklusseadus", "Raudteeseadus", "Lennundusseadus"
    )
    title = "Liiklusseaduse ja raudteeseaduse muutmise seadus"
    matches = mod.match_all_titles_to_laws(title, index)
    names = {m["name"] for m in matches}
    assert names == {"Liiklusseadus", "Raudteeseadus"}
    # Lennundusseadus is not in the title and must not appear.
    assert "Lennundusseadus" not in names


def test_match_all_titles_to_laws_is_deterministic() -> None:
    """match_all_titles_to_laws returns a stable order regardless of dict
    iteration order, so emitted links/report are byte-stable (#288)."""
    title = "Liiklusseaduse ja raudteeseaduse muutmise seadus"
    a = _law_index_with("Liiklusseadus", "Raudteeseadus")
    b: dict[str, dict] = {key: a[key] for key in reversed(list(a.keys()))}

    names_a = [m["name"] for m in mod.match_all_titles_to_laws(title, a)]
    names_b = [m["name"] for m in mod.match_all_titles_to_laws(title, b)]
    assert names_a == names_b


def test_match_all_titles_single_law_title_returns_one() -> None:
    """A plain single-law title returns exactly that one law (no spurious
    embedded matches)."""
    index = _law_index_with("Raudteeseadus", "Teeseadus")
    matches = mod.match_all_titles_to_laws("Raudteeseadus", index)
    assert [m["name"] for m in matches] == ["Raudteeseadus"]


def test_main_emits_links_for_both_laws_in_combined_title(tmp_path, monkeypatch):
    """End-to-end (mocked SPARQL): a combined amending-act title transposes
    the directive into BOTH referenced laws, not just one (#288)."""
    krr = tmp_path / "krr_outputs"
    eurlex = krr / "eurlex"
    krr.mkdir()
    eurlex.mkdir()

    # Two matchable Estonian laws.
    liiklus = krr / "liiklusseadus_peep.json"
    _write_json(
        liiklus,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:LIIKLUS_Map_2026", "@type": ["owl:Ontology", "estleg:Act"],
                 "estleg:sourceAct": "Liiklusseadus"},
            ],
        },
    )
    raudtee = krr / "raudteeseadus_peep.json"
    _write_json(
        raudtee,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:RAUDTEE_Map_2026", "@type": ["owl:Ontology", "estleg:Act"],
                 "estleg:sourceAct": "Raudteeseadus"},
            ],
        },
    )
    _write_json(
        krr / "INDEX.json",
        {"total_laws": 2, "laws": [
            {"name": "liiklusseadus", "files": ["liiklusseadus_peep.json"]},
            {"name": "raudteeseadus", "files": ["raudteeseadus_peep.json"]},
        ]},
    )
    _write_json(
        eurlex / "eurlex_directives_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:EU_32016L0798", "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
                 "estleg:celexNumber": "32016L0798"},
            ],
        },
    )

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        mod, "fetch_transposition_measures",
        lambda **_k: ([{"celex_dir": "32016L0798", "directive_uri": "http://x/dir",
                        "title_nat": "Liiklusseaduse ja raudteeseaduse muutmise seadus"}], False),
    )
    monkeypatch.setattr(mod, "fetch_directive_deadlines", lambda **_k: ({}, False))
    monkeypatch.setattr(sys, "argv", ["generate_transposition_mapping.py"])

    rc = mod.main()
    assert rc in (0, None)

    # Both law peeps got the directive link.
    liiklus_doc = json.loads(liiklus.read_text(encoding="utf-8"))
    raudtee_doc = json.loads(raudtee.read_text(encoding="utf-8"))
    assert {"@id": "estleg:EU_32016L0798"} in liiklus_doc["@graph"][0]["estleg:transposesDirective"]
    assert {"@id": "estleg:EU_32016L0798"} in raudtee_doc["@graph"][0]["estleg:transposesDirective"]

    # The directive node is transposedBy BOTH law nodes.
    dir_doc = json.loads((eurlex / "eurlex_directives_peep.json").read_text(encoding="utf-8"))
    transposed_by = {ref["@id"] for ref in dir_doc["@graph"][0]["estleg:transposedBy"]}
    assert transposed_by == {"estleg:LIIKLUS_Map_2026", "estleg:RAUDTEE_Map_2026"}

    # Report records both law-directive pairs.
    report = json.loads((krr / "reports" / "transposition_mapping.json").read_text(encoding="utf-8"))
    matched_laws = {m["matched_law_name"] for m in report["mappings"]}
    assert matched_laws == {"liiklusseadus", "raudteeseadus"}


# ---------------------------------------------------------------------------
# #318 — the cdm:work_title fetch must be language-filtered so a single NIM
# carried in several CELLAR languages is not processed once per language
# (inflating counts / risking a false fuzzy link from a short foreign title).
# ---------------------------------------------------------------------------


def test_transposition_query_filters_title_language():
    """The pagination query restricts ``?title_nat`` to Estonian/untagged
    literals so multi-language CELLAR titles don't inflate the sweep (#318)."""
    import inspect

    src = inspect.getsource(mod.fetch_transposition_measures)
    # The OPTIONAL keeps only et / untagged titles.
    assert "lang(?title_nat) = 'et'" in src
    assert "lang(?title_nat) = ''" in src


def _title_binding(celex: str, title: str, lang: str | None) -> dict:
    """One SPARQL result row for the transposition query.

    ``lang=None`` emits an *untagged* literal (no ``xml:lang`` key), matching
    how CELLAR returns plain Estonian NIM titles.
    """
    title_val: dict[str, str] = {"type": "literal", "value": title}
    if lang is not None:
        title_val["xml:lang"] = lang
    return {
        "nim": {"type": "uri", "value": f"http://nim/{celex}/{lang or 'none'}"},
        "directive": {"type": "uri", "value": f"http://dir/{celex}"},
        "celex_dir": {"type": "literal", "value": celex},
        "title_nat": title_val,
    }


def test_fetch_drops_non_estonian_titles(monkeypatch):
    """A French/English-tagged title is filtered out post-fetch; the Estonian
    (and untagged) titles for the same NIM are kept exactly once (#318)."""
    # One logical NIM/title delivered in fr, en, et, and untagged form. Without
    # the guard the per-language dedup key would admit all four.
    rows = [
        _title_binding("32016L0798", "Commentaire en francais", "fr"),
        _title_binding("32016L0798", "Some English comment", "en"),
        _title_binding("32016L0798", "Raudteeseadus", "et"),
        _title_binding("32016L0798", "Liiklusseadus", None),  # untagged -> kept
    ]

    def _one_page(_query, **_kwargs):
        # Return the rows once, then an empty page to end pagination.
        if not getattr(_one_page, "served", False):
            _one_page.served = True
            return rows
        return []

    monkeypatch.setattr(mod, "sparql_query_with_retry", _one_page)

    items, partial = mod.fetch_transposition_measures(allow_partial=False)
    assert partial is False
    titles = sorted(i["title_nat"] for i in items)
    # FR/EN dropped; ET + untagged kept.
    assert titles == ["Liiklusseadus", "Raudteeseadus"]
    assert "Commentaire en francais" not in titles
    assert "Some English comment" not in titles


def test_fetch_keeps_estonian_case_insensitive_lang(monkeypatch):
    """An ``ET`` (upper-case) language tag is still recognised as Estonian and
    kept — the guard is case-insensitive (#318)."""
    rows = [_title_binding("32016L0798", "Raudteeseadus", "ET")]

    def _one_page(_query, **_kwargs):
        if not getattr(_one_page, "served", False):
            _one_page.served = True
            return rows
        return []

    monkeypatch.setattr(mod, "sparql_query_with_retry", _one_page)
    items, _partial = mod.fetch_transposition_measures(allow_partial=False)
    assert [i["title_nat"] for i in items] == ["Raudteeseadus"]


# ---------------------------------------------------------------------------
# #388 — match_all_titles_to_laws must not give a law that is merely
# co-amended in a combined omnibus bill a spurious transposesDirective link;
# only the primary-clause law or a law whose domain matches the directive
# subject is linked.
# ---------------------------------------------------------------------------


# Real CELLAR directive titles (rdfs:label), so the domain-keyword guard is
# exercised against the same text the pipeline sees.
_RAILWAY_SUBJECT = mod.normalize_text(
    "Euroopa Parlamendi ja nõukogu direktiiv 2004/49/EÜ, ühenduse raudteede "
    "ohutuse kohta, millega muudetakse nõukogu direktiivi 95/18/EÜ"
)
_TIMESHARE_SUBJECT = mod.normalize_text(
    "Euroopa Parlamendi ja nõukogu direktiiv 2008/122/EÜ, tarbijate kaitse "
    "kohta seoses osaajalise kasutamise õiguse"
)


def test_co_amended_secondary_law_not_linked_primary_is() -> None:
    """The headline #388 false positive: a railway-led omnibus bill that also
    amends the Maritime Safety and State Fees acts, against the Railway Safety
    directive, links ONLY the railway law; Maritime Safety and State Fees —
    secondary, off-subject co-amendments — must NOT be linked.

    (In the live corpus the offending NIM title is
    ``"Lennunduseaduse, meresõiduohutuse seaduse ja raudteeseaduse muutmise
    seadus"``; the railway law sits in a trailing clause but is rescued by the
    domain-keyword arm, while Maritime stays pruned.)"""
    index = _law_index_with(
        "Meresõiduohutuse seadus", "Raudteeseadus", "Riigilõivuseadus"
    )
    # Railway is the primary clause; Maritime + Fees are co-amended secondaries.
    title = (
        "Raudteeseaduse, meresõiduohutuse seaduse "
        "ja riigilõivuseaduse muutmise seadus"
    )
    matches = mod.match_all_titles_to_laws(
        title, index, directive_subject=_RAILWAY_SUBJECT
    )
    names = {m["name"] for m in matches}
    # Railway law (primary clause AND domain root ``raudtee`` ⊂ subject) kept.
    assert "Raudteeseadus" in names
    # Maritime Safety is co-amended (secondary, off-subject) -> dropped.
    assert "Meresõiduohutuse seadus" not in names
    # State Fees is co-amended (secondary, off-subject) -> dropped.
    assert "Riigilõivuseadus" not in names


def test_co_amended_railway_rescued_from_trailing_clause() -> None:
    """The exact live title: Maritime Safety leads the bill (primary clause) but
    is OFF the railway subject, while the railway law sits in a trailing clause
    yet is ON subject. The domain-keyword arm rescues railway and the
    primary-clause arm would have (wrongly) kept Maritime were it matchable —
    so this asserts the railway link survives and Maritime/Fees are still
    pruned via the subject arm (#388)."""
    index = _law_index_with(
        "Meresõiduohutuse seadus", "Raudteeseadus", "Riigilõivuseadus"
    )
    # Maritime is primary here; railway is a trailing (secondary) clause.
    title = (
        "Meresõiduohutuse seaduse ja riigilõivuseaduse "
        "ning raudteeseaduse muutmise seadus"
    )
    names = {
        m["name"]
        for m in mod.match_all_titles_to_laws(
            title, index, directive_subject=_RAILWAY_SUBJECT
        )
    }
    # Railway (trailing clause, but ``raudtee`` ⊂ subject) is rescued.
    assert "Raudteeseadus" in names
    # State Fees (secondary, off-subject) is dropped.
    assert "Riigilõivuseadus" not in names


def test_primary_clause_law_kept_even_if_off_subject() -> None:
    """The law in the PRIMARY clause (before the first ``ja``/``ning``/comma)
    is the one the bill is named for and is kept even when its domain root is
    not in the directive subject — only the trailing co-amended laws are
    pruned (#388)."""
    # Timeshare directive: Law of Obligations (võlaõigus) is primary and stays;
    # the co-amended non-profit-associations act is dropped.
    index = _law_index_with("Võlaõigusseadus", "Mittetulundusühingute seadus")
    title = (
        "Võlaõigusseaduse ja mittetulundusühingute seaduse muutmise seadus"
    )
    matches = mod.match_all_titles_to_laws(
        title, index, directive_subject=_TIMESHARE_SUBJECT
    )
    names = {m["name"] for m in matches}
    assert "Võlaõigusseadus" in names  # primary clause -> kept
    assert "Mittetulundusühingute seadus" not in names  # co-amended -> dropped


def test_no_directive_subject_fails_open_keeps_all() -> None:
    """With no directive subject to discriminate on, the function preserves the
    #288 behaviour and returns every matched law (fail open)."""
    index = _law_index_with("Liiklusseadus", "Raudteeseadus")
    title = "Liiklusseaduse ja raudteeseaduse muutmise seadus"
    # No directive_subject (and explicit empty string) both fail open.
    for subject in (None, ""):
        names = {
            m["name"]
            for m in mod.match_all_titles_to_laws(
                title, index, directive_subject=subject
            )
        }
        assert names == {"Liiklusseadus", "Raudteeseadus"}


def test_both_on_subject_laws_are_kept() -> None:
    """When two co-amended laws BOTH belong to the directive subject they are
    both kept — the guard prunes only off-subject secondaries (#388 does not
    over-correct the legitimate #288 multi-law case)."""
    # A (hypothetical) directive whose subject mentions both rail and road.
    subject = mod.normalize_text(
        "direktiiv liiklusohutuse ja raudteede ohutuse kohta"
    )
    index = _law_index_with("Liiklusseadus", "Raudteeseadus")
    title = "Liiklusseaduse ja raudteeseaduse muutmise seadus"
    names = {
        m["name"]
        for m in mod.match_all_titles_to_laws(
            title, index, directive_subject=subject
        )
    }
    assert names == {"Liiklusseadus", "Raudteeseadus"}


def test_single_law_title_ignores_subject_guard() -> None:
    """A single-law title short-circuits before the co-amendment guard, so an
    unrelated directive subject never suppresses a direct match (#388)."""
    index = _law_index_with("Raudteeseadus")
    matches = mod.match_all_titles_to_laws(
        "Raudteeseadus", index, directive_subject=_TIMESHARE_SUBJECT
    )
    assert [m["name"] for m in matches] == ["Raudteeseadus"]


def test_build_directive_subject_index_reads_rdfs_label(tmp_path, monkeypatch):
    """``build_directive_subject_index`` maps CELEX → normalized rdfs:label and
    leaves a label-less directive with an empty (fail-open) subject (#388)."""
    eurlex = tmp_path / "eurlex"
    eurlex.mkdir()
    _write_json(
        eurlex / "eurlex_directives_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:EU_32004L0049",
                    "estleg:celexNumber": "32004L0049",
                    "rdfs:label": "... ühenduse raudteede ohutuse kohta ...",
                },
                {
                    "@id": "estleg:EU_99999L9999",
                    "estleg:celexNumber": "99999L9999",
                    # No rdfs:label.
                },
            ],
        },
    )
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)

    subjects = mod.build_directive_subject_index()
    assert "raudteede ohutuse" in subjects["32004L0049"]
    assert subjects["32004L0049"] == subjects["32004L0049"].lower()  # normalized
    assert subjects["99999L9999"] == ""  # label-less -> fail-open marker


def test_main_does_not_link_co_amended_secondary_law(tmp_path, monkeypatch):
    """End-to-end (mocked SPARQL): a combined NIM title co-amending a railway
    law and a fee law, against the Railway Safety directive, links ONLY the
    railway law — the co-amended fee law gets no transposesDirective and the
    directive is not transposedBy it (#388)."""
    krr = tmp_path / "krr_outputs"
    eurlex = krr / "eurlex"
    krr.mkdir()
    eurlex.mkdir()

    # Primary (on-subject) railway law and a co-amended off-subject fee law.
    raudtee = krr / "raudteeseadus_peep.json"
    _write_json(
        raudtee,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:RAUDTEE_Map_2026", "@type": ["owl:Ontology", "estleg:Act"],
                 "estleg:sourceAct": "Raudteeseadus"},
            ],
        },
    )
    riigiloiv = krr / "riigiloivuseadus_peep.json"
    _write_json(
        riigiloiv,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:RIIGIL_Map_2026", "@type": ["owl:Ontology", "estleg:Act"],
                 "estleg:sourceAct": "Riigilõivuseadus"},
            ],
        },
    )
    _write_json(
        krr / "INDEX.json",
        {"total_laws": 2, "laws": [
            {"name": "raudteeseadus", "files": ["raudteeseadus_peep.json"]},
            {"name": "riigiloivuseadus", "files": ["riigiloivuseadus_peep.json"]},
        ]},
    )
    # Directive carries an rdfs:label whose subject contains only ``raudtee``.
    _write_json(
        eurlex / "eurlex_directives_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {"@id": "estleg:EU_32004L0049",
                 "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
                 "estleg:celexNumber": "32004L0049",
                 "rdfs:label": "Direktiiv 2004/49/EÜ ühenduse raudteede ohutuse kohta"},
            ],
        },
    )

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        mod, "fetch_transposition_measures",
        lambda **_k: ([{"celex_dir": "32004L0049", "directive_uri": "http://x/dir",
                        "title_nat": "Raudteeseaduse ja riigilõivuseaduse muutmise seadus"}], False),
    )
    monkeypatch.setattr(mod, "fetch_directive_deadlines", lambda **_k: ({}, False))
    monkeypatch.setattr(sys, "argv", ["generate_transposition_mapping.py"])

    rc = mod.main()
    assert rc in (0, None)

    raudtee_doc = json.loads(raudtee.read_text(encoding="utf-8"))
    riigiloiv_doc = json.loads(riigiloiv.read_text(encoding="utf-8"))

    # Railway law transposes the directive.
    assert {"@id": "estleg:EU_32004L0049"} in raudtee_doc["@graph"][0]["estleg:transposesDirective"]
    # Co-amended fee law does NOT (no transposesDirective key at all, or empty).
    assert "estleg:transposesDirective" not in riigiloiv_doc["@graph"][0]

    # Directive is transposedBy ONLY the railway law node.
    dir_doc = json.loads((eurlex / "eurlex_directives_peep.json").read_text(encoding="utf-8"))
    transposed_by = {ref["@id"] for ref in dir_doc["@graph"][0].get("estleg:transposedBy", [])}
    assert transposed_by == {"estleg:RAUDTEE_Map_2026"}

    # The report records only the railway pairing.
    report = json.loads((krr / "reports" / "transposition_mapping.json").read_text(encoding="utf-8"))
    matched_laws = {m["matched_law_name"] for m in report["mappings"]}
    assert matched_laws == {"raudteeseadus"}


# ---------------------------------------------------------------------------
# #597 — the co-amendment guard must not silently drop the sole transposing
# law when it has a SHORT (< _MIN_DOMAIN_ROOT_LEN) domain root and sits in a
# non-primary clause (after a comma / ``ja`` / ``ning``). A short root that is
# explicitly named in the title AND whose root anchors a whole word in the
# directive subject is now rescued; a co-amended short-root law whose root is
# absent/incidental in the subject stays pruned (no blanket fall-open).
# ---------------------------------------------------------------------------


# A realistic customs-directive subject (rdfs:label): the Estonian word for
# customs (``toll``) surfaces as ``tolliseadustiku`` (the Union Customs Code),
# so the short root ``tolli`` anchors a whole word in it.
_CUSTOMS_SUBJECT = mod.normalize_text(
    "Euroopa Parlamendi ja nõukogu direktiiv liidu tolliseadustiku kohta"
)


def test_short_root_co_amendment_rescued_when_subject_matches() -> None:
    """#597: ``tolliseadus`` (root ``tolli``, 5 chars < _MIN_DOMAIN_ROOT_LEN)
    sits AFTER the comma (non-primary clause) of
    ``"Maksukorralduse seaduse, tolliseaduse muutmise seadus"`` yet is the real
    transposer of a customs directive. It used to be dropped (short root +
    non-primary, both guard arms False); now its whole-word subject hit
    (``tolli`` ⊂ ``tolliseadustiku``) rescues it."""
    index = _law_index_with("Maksukorralduse seadus", "Tolliseadus")
    title = "Maksukorralduse seaduse, tolliseaduse muutmise seadus"
    names = {
        m["name"]
        for m in mod.match_all_titles_to_laws(
            title, index, directive_subject=_CUSTOMS_SUBJECT
        )
    }
    # The short-root, non-primary, explicitly-named transposer is recovered.
    assert "Tolliseadus" in names
    # The primary-clause law is kept as before (primary-clause arm, #388).
    assert "Maksukorralduse seadus" in names


def test_short_root_co_amendment_dropped_when_subject_unrelated() -> None:
    """#597 guard: the SAME short-root co-amendment is still pruned when the
    directive subject has nothing to do with it — i.e. the fix is subject-gated,
    NOT a blanket fall-open (which the issue's naive 'always keep short roots'
    suggestion would have been). ``tolliseadus`` co-amended in a bill transposing
    a railway directive (no ``tolli`` in the subject) gets no link."""
    index = _law_index_with("Maksukorralduse seadus", "Tolliseadus")
    title = "Maksukorralduse seaduse, tolliseaduse muutmise seadus"
    names = {
        m["name"]
        for m in mod.match_all_titles_to_laws(
            title, index, directive_subject=_RAILWAY_SUBJECT
        )
    }
    # Off-subject short-root co-amendment stays dropped (incidental).
    assert "Tolliseadus" not in names
    # The primary-clause law is still kept (primary-clause arm, #388).
    assert "Maksukorralduse seadus" in names


def test_law_matches_directive_subject_short_root_requires_word_anchor() -> None:
    """#597: a short domain root matches a directive subject only at a WHOLE-WORD
    boundary, never as a mid-word fragment — so a genuine named transposer is
    recovered without re-admitting the coincidental substring hits the length
    floor was guarding against. Longer roots keep the permissive substring test."""
    # Whole-word anchor: ``tolli`` starts ``tolliseadustiku`` -> domain match.
    assert mod._law_matches_directive_subject("tolliseadus", _CUSTOMS_SUBJECT) is True

    # Mid-word fragment: ``tolli`` appears ONLY inside ``atolli`` (atoll), i.e.
    # preceded by a word char, so it is not anchored. A bare substring test WOULD
    # match here — the word-boundary rule is exactly what keeps such incidental
    # short fragments out (#597).
    buried = mod.normalize_text("atolli ja laguuni kohta")
    assert "tolli" in buried  # the bare substring is present...
    assert mod._law_matches_directive_subject("tolliseadus", buried) is False  # ...but not anchored

    # Root entirely absent from the subject -> not a domain match (the plain
    # incidental co-amendment case).
    assert mod._law_matches_directive_subject("tolliseadus", _RAILWAY_SUBJECT) is False

    # A long root (>= _MIN_DOMAIN_ROOT_LEN) keeps the permissive plain-substring
    # behaviour, so the #388 railway rescue is unchanged.
    assert mod._law_matches_directive_subject("raudteeseadus", _RAILWAY_SUBJECT) is True


# ---------------------------------------------------------------------------
# #319 — do not queue a forward transposesDirective write when the law file
# has no resolvable act-level IRI (same guard as the inverse transposedBy).
# ---------------------------------------------------------------------------


def test_unresolvable_law_file_is_not_queued_for_forward_link(tmp_path: Path) -> None:
    """#319: collection must skip a law file that has no Act/Ontology target.

    The old loop appended every matched filepath to ``law_file_directives``
    and only None-guarded the inverse IRI map. A file whose
    ``find_law_transposition_target`` result is None must not appear in the
    forward map either, or ``update_law_file`` would write an unpaired
    ``estleg:transposesDirective``.
    """
    krr = tmp_path / "krr_outputs"
    krr.mkdir()

    good = "tubakaseadus_peep.json"
    _write_json(
        krr / good,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:TUBAKA_Map_2026",
                    "@type": ["owl:Ontology", "estleg:Act"],
                }
            ],
        },
    )
    # Empty graph: no Act/Ontology (or any) node, so the target finder
    # returns None and get_law_transposition_target_iri is None.
    bad = "orphan_peep.json"
    _write_json(krr / bad, {"@context": mod.CONTEXT, "@graph": []})
    assert mod.find_law_transposition_target(
        json.loads((krr / bad).read_text(encoding="utf-8"))
    ) is None
    assert mod.get_law_transposition_target_iri(krr / bad) is None

    law_file_directives: dict[str, list[str]] = {}
    directive_celex_to_law_iris: dict[str, list[str]] = {}
    missing_law_iris: list[dict] = []
    directive_iri = "estleg:EU_32003L0033"

    mod.collect_transposition_file_links(
        [good, bad],
        directive_iri=directive_iri,
        directive_celex="32003L0033",
        matched_law_name="tubakaseadus",
        law_file_directives=law_file_directives,
        directive_celex_to_law_iris=directive_celex_to_law_iris,
        missing_law_iris=missing_law_iris,
        krr_dir=krr,
    )

    good_path = str(krr / good)
    bad_path = str(krr / bad)
    # Resolvable file is queued for the forward write and the inverse.
    assert law_file_directives == {good_path: [directive_iri]}
    assert directive_celex_to_law_iris == {"32003L0033": ["estleg:TUBAKA_Map_2026"]}
    # Unresolvable file must not be queued (the old unconditional append
    # would have inserted ``bad_path`` here).
    assert bad_path not in law_file_directives
    assert missing_law_iris == [
        {
            "directive_celex": "32003L0033",
            "law_file": bad,
            "matched_law_name": "tubakaseadus",
        }
    ]
