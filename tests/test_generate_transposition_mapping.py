from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_transposition_mapping as mod  # noqa: E402


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
