"""Focused tests for ``generate_harmonisation_links`` fixes #334, #335, #356.

These complement the broader harmonisation coverage in
``tests/test_generate_eu_sources.py`` (query shape, NIM parsing, cache,
freshness gate, graph[0] guard) and intentionally do not overlap with it.

  * #334 — the aggregate ``Harmonisation_<celex>`` node must back-link to
    *every* Estonian law that transposes the directive, not just the first.
  * #335 — ``estleg:harmonisedWith`` must declare ``rdfs:domain estleg:Act``
    (every instance lives on an act-level node), in both the emitted OWL
    schema and the committed ``harmonisation_schema.json``.
  * #356 — ``fetch_other_transpositions`` must reject a ``celex_dir`` that
    contains characters outside the strict CELEX allowlist before it is
    interpolated into the SPARQL FILTER string literals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_harmonisation_links as harmonisation  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = (
    REPO_ROOT / "krr_outputs" / "harmonisation" / "harmonisation_schema.json"
)


def _harmonised_ids(directive_doc: dict) -> list[str]:
    """Return the ``@id`` list of the aggregate node's ``harmonisedWith``."""
    aggregate = directive_doc["@graph"][0]
    assert aggregate["@id"].startswith("estleg:Harmonisation_")
    refs = aggregate["estleg:harmonisedWith"]
    assert isinstance(refs, list), "harmonisedWith must always be an array"
    return [ref["@id"] for ref in refs]


# ---------------------------------------------------------------------------
# #334 — multi-law directives back-link to ALL transposing laws
# ---------------------------------------------------------------------------


def test_build_directive_node_lists_all_transposing_laws():
    estonian_laws = [
        {"name": "Veeseadus", "iri": "estleg:VEE_Map_2026"},
        {"name": "Keskkonnavastutuse seadus", "iri": "estleg:KEHVS_Map_2026"},
    ]
    doc = harmonisation.build_directive_node(
        celex_dir="32000L0060",
        directive_iri="estleg:EU_32000L0060",
        estonian_law_name="Veeseadus",
        estonian_laws=estonian_laws,
        other_measures=[],
    )
    assert _harmonised_ids(doc) == [
        "estleg:VEE_Map_2026",
        "estleg:KEHVS_Map_2026",
    ]


def test_build_directive_node_single_law_unchanged():
    doc = harmonisation.build_directive_node(
        celex_dir="32016L0680",
        directive_iri="estleg:EU_32016L0680",
        estonian_law_name="Some Act",
        estonian_laws=[{"name": "Some Act", "iri": "estleg:SA_Map_2026"}],
        other_measures=[],
    )
    assert _harmonised_ids(doc) == ["estleg:SA_Map_2026"]


def test_build_directive_node_skips_laws_without_iri():
    estonian_laws = [
        {"name": "Resolved Act", "iri": "estleg:RA_Map_2026"},
        {"name": "Unresolved Act"},  # no 'iri' key — must be dropped
        {"name": "Unresolved Act 2", "iri": None},  # None IRI — must be dropped
    ]
    doc = harmonisation.build_directive_node(
        celex_dir="32004L0049",
        directive_iri="estleg:EU_32004L0049",
        estonian_law_name="Resolved Act",
        estonian_laws=estonian_laws,
        other_measures=[],
    )
    assert _harmonised_ids(doc) == ["estleg:RA_Map_2026"]


def test_build_directive_node_dedupes_repeated_iris():
    estonian_laws = [
        {"name": "Act A", "iri": "estleg:A_Map_2026"},
        {"name": "Act A (dup mapping)", "iri": "estleg:A_Map_2026"},
        {"name": "Act B", "iri": "estleg:B_Map_2026"},
    ]
    doc = harmonisation.build_directive_node(
        celex_dir="32003L0098",
        directive_iri="estleg:EU_32003L0098",
        estonian_law_name="Act A",
        estonian_laws=estonian_laws,
        other_measures=[],
    )
    assert _harmonised_ids(doc) == ["estleg:A_Map_2026", "estleg:B_Map_2026"]


# ---------------------------------------------------------------------------
# #335 — harmonisedWith domain is estleg:Act (schema + emitted OWL)
# ---------------------------------------------------------------------------


def _harmonised_with_node(graph: list[dict]) -> dict:
    for node in graph:
        if node.get("@id") == "estleg:harmonisedWith":
            return node
    raise AssertionError("estleg:harmonisedWith node not found in @graph")


def test_generated_schema_harmonised_with_domain_is_act():
    schema = harmonisation.generate_schema()
    node = _harmonised_with_node(schema["@graph"])
    assert node["rdfs:domain"] == {"@id": "estleg:Act"}
    assert node["rdfs:range"] == {"@id": "estleg:HarmonisationLink"}


def test_committed_schema_harmonised_with_domain_is_act():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    node = _harmonised_with_node(schema["@graph"])
    assert node["rdfs:domain"] == {"@id": "estleg:Act"}
    assert node["rdfs:range"] == {"@id": "estleg:HarmonisationLink"}


def test_committed_schema_matches_generator_for_harmonised_with():
    """The committed schema's harmonisedWith definition must match what the
    generator now emits (so a regeneration is a no-op for this property)."""
    generated = _harmonised_with_node(harmonisation.generate_schema()["@graph"])
    committed = _harmonised_with_node(
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["@graph"]
    )
    assert generated["rdfs:domain"] == committed["rdfs:domain"]
    assert generated["rdfs:range"] == committed["rdfs:range"]


# ---------------------------------------------------------------------------
# #356 — SPARQL injection guard on celex_dir
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "celex_dir",
    [
        # The injection payload from ticket #356 (space, quote, '=', '.').
        '32009L0110"} . ?x ?y ?z . FILTER(1=1) {',
        '32016L0680" }',  # closing brace + quote
        "32016L0680\n?x ?y ?z",  # newline-based break-out
        "32016L0680 OR 1=1",  # spaces
        "32016L0680;DROP",  # semicolon
        "",  # empty (fullmatch requires at least one char)
    ],
)
def test_fetch_other_transpositions_rejects_malformed_celex(celex_dir, monkeypatch):
    # The endpoint must never be reached for a malformed value.
    def fail_query(_query: str) -> list[dict]:  # pragma: no cover - must not run
        raise AssertionError("sparql_query must not be called for a bad celex_dir")

    monkeypatch.setattr(harmonisation, "sparql_query", fail_query)

    with pytest.raises(ValueError, match="CELEX allowlist"):
        harmonisation.fetch_other_transpositions(celex_dir, use_cache=False)


def test_fetch_other_transpositions_rejected_before_cache(tmp_path, monkeypatch):
    """A malformed value must be refused before any cache file is touched —
    sanitize_celex strips disallowed characters, so a bad value could
    otherwise collide with a legitimate cache entry."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(harmonisation, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(
        harmonisation,
        "sparql_query",
        lambda _q: (_ for _ in ()).throw(AssertionError("must not query")),
    )

    with pytest.raises(ValueError):
        # Default use_cache=True so the cache path is exercised too.
        harmonisation.fetch_other_transpositions('32009L0110" inj')

    assert not cache_dir.exists(), "no cache file should be created for a bad celex"


@pytest.mark.parametrize(
    "celex_dir",
    [
        "32016L0680",  # canonical directive CELEX
        "32016L0680R(01)",  # corrigendum form with parentheses
        "32000L0060",  # the #334 example directive
    ],
)
def test_fetch_other_transpositions_accepts_valid_celex(
    celex_dir, monkeypatch, tmp_path
):
    """A well-formed CELEX passes the guard and reaches the (faked) endpoint."""
    monkeypatch.setattr(harmonisation, "CACHE_DIR", tmp_path / "cache")
    seen: list[str] = []

    def fake_query(query: str) -> list[dict]:
        seen.append(query)
        return []

    monkeypatch.setattr(harmonisation, "sparql_query", fake_query)

    assert harmonisation.fetch_other_transpositions(celex_dir, use_cache=False) == []
    assert len(seen) == 1
    # The validated value is interpolated verbatim into the FILTER literal.
    assert f'FILTER(STR(?celex_dir) = "{celex_dir}")' in seen[0]
