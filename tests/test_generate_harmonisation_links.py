"""Focused tests for ``generate_harmonisation_links`` fixes #334, #335, #356, #425.

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
  * #425 — the aggregate node's link→act back-edge uses the inverse
    property ``estleg:harmonises`` (NOT ``estleg:harmonisedWith``, which is
    reserved for the act→link direction in the law peeps); both the emitted
    OWL schema and the committed ``harmonisation_schema.json`` must declare
    ``estleg:harmonises`` with ``owl:inverseOf estleg:harmonisedWith`` and
    swapped domain/range. Also covers ``update_law_file_harmonisation``
    (act→link direction, part of #339).
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
    """Return the ``@id`` list of the aggregate node's ``harmonises`` back-edge.

    #425: the aggregate ``Harmonisation_<celex>`` node carries the inverse
    property ``estleg:harmonises`` (link → act), never ``estleg:harmonisedWith``
    (which is the act → link direction emitted on the law peeps).
    """
    aggregate = directive_doc["@graph"][0]
    assert aggregate["@id"].startswith("estleg:Harmonisation_")
    assert "estleg:harmonisedWith" not in aggregate, (
        "aggregate node must not re-use estleg:harmonisedWith (#425)"
    )
    refs = aggregate["estleg:harmonises"]
    assert isinstance(refs, list), "harmonises must always be an array"
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
# #425 — inverse property estleg:harmonises (link → act) replaces the
# inverted use of estleg:harmonisedWith on the aggregate node
# ---------------------------------------------------------------------------


def _node_by_id(graph: list[dict], node_id: str) -> dict:
    for node in graph:
        if node.get("@id") == node_id:
            return node
    raise AssertionError(f"{node_id} not found in @graph")


def test_aggregate_node_uses_harmonises_not_harmonised_with():
    """The link→act back-edge must be estleg:harmonises, and the aggregate
    node must NOT carry estleg:harmonisedWith (that is the act→link
    direction, owned by the law peeps)."""
    doc = harmonisation.build_directive_node(
        celex_dir="32000L0060",
        directive_iri="estleg:EU_32000L0060",
        estonian_law_name="Veeseadus",
        estonian_laws=[{"name": "Veeseadus", "iri": "estleg:VEE_Map_2026"}],
        other_measures=[],
    )
    aggregate = doc["@graph"][0]
    assert aggregate["estleg:harmonises"] == [{"@id": "estleg:VEE_Map_2026"}]
    assert "estleg:harmonisedWith" not in aggregate


def test_per_country_measure_nodes_have_no_back_edge():
    """Per-country NIM nodes carry neither direction of the act↔link edge —
    only the aggregate node back-links to the transposing act(s)."""
    other = [
        {
            "country_code": "LVA",
            "country_en": "Latvia",
            "celex_nat": "72000L0060LVA_1",
            "title": "Latvian water act",
        }
    ]
    doc = harmonisation.build_directive_node(
        celex_dir="32000L0060",
        directive_iri="estleg:EU_32000L0060",
        estonian_law_name="Veeseadus",
        estonian_laws=[{"name": "Veeseadus", "iri": "estleg:VEE_Map_2026"}],
        other_measures=other,
    )
    measure_nodes = [
        n for n in doc["@graph"] if n["@id"].startswith("estleg:Harm_")
    ]
    assert measure_nodes, "expected at least one per-country measure node"
    for node in measure_nodes:
        assert "estleg:harmonises" not in node
        assert "estleg:harmonisedWith" not in node


def test_generated_schema_harmonises_is_inverse_of_harmonised_with():
    schema = harmonisation.generate_schema()
    node = _node_by_id(schema["@graph"], "estleg:harmonises")
    assert node["@type"] == ["owl:ObjectProperty"]
    # Inverse direction: domain/range are the swap of harmonisedWith's.
    assert node["rdfs:domain"] == {"@id": "estleg:HarmonisationLink"}
    assert node["rdfs:range"] == {"@id": "estleg:Act"}
    assert node["owl:inverseOf"] == {"@id": "estleg:harmonisedWith"}


def test_generated_schema_harmonised_with_declares_inverse():
    """harmonisedWith must point back at harmonises via owl:inverseOf."""
    schema = harmonisation.generate_schema()
    node = _harmonised_with_node(schema["@graph"])
    assert node["owl:inverseOf"] == {"@id": "estleg:harmonises"}


def test_committed_schema_declares_harmonises_inverse_pair():
    """The committed schema must carry the full inverse pair so a
    regeneration is a no-op."""
    graph = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["@graph"]
    harmonises = _node_by_id(graph, "estleg:harmonises")
    assert harmonises["rdfs:domain"] == {"@id": "estleg:HarmonisationLink"}
    assert harmonises["rdfs:range"] == {"@id": "estleg:Act"}
    assert harmonises["owl:inverseOf"] == {"@id": "estleg:harmonisedWith"}
    assert _harmonised_with_node(graph)["owl:inverseOf"] == {
        "@id": "estleg:harmonises"
    }


def test_committed_schema_matches_generator_for_harmonises():
    """Committed estleg:harmonises node must equal the generator's emission
    (full node equality — a fresh schema regen changes nothing)."""
    generated = _node_by_id(
        harmonisation.generate_schema()["@graph"], "estleg:harmonises"
    )
    committed = _node_by_id(
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["@graph"],
        "estleg:harmonises",
    )
    assert generated == committed


# ---------------------------------------------------------------------------
# #339 (partial) — update_law_file_harmonisation: the act→link direction
# stays on estleg:harmonisedWith, idempotent, array-typed.
# ---------------------------------------------------------------------------


def _write_law_peep(tmp_path: Path, ontology_node: dict) -> Path:
    path = tmp_path / "some_law_peep.json"
    path.write_text(
        json.dumps({"@context": {}, "@graph": [ontology_node]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_update_law_file_adds_harmonised_with_to_ontology_node(tmp_path):
    path = _write_law_peep(
        tmp_path,
        {"@id": "estleg:SomeAct_Map_2026", "@type": ["owl:Ontology", "estleg:Act"]},
    )
    changed = harmonisation.update_law_file_harmonisation(
        path, ["estleg:Harmonisation_31990L0314", "estleg:Harmonisation_31993L0013"]
    )
    assert changed is True
    node = json.loads(path.read_text(encoding="utf-8"))["@graph"][0]
    refs = node["estleg:harmonisedWith"]
    assert isinstance(refs, list), "act→link direction must be an array"
    assert [r["@id"] for r in refs] == [
        "estleg:Harmonisation_31990L0314",
        "estleg:Harmonisation_31993L0013",
    ]


def test_update_law_file_is_idempotent(tmp_path):
    path = _write_law_peep(
        tmp_path,
        {"@id": "estleg:SomeAct_Map_2026", "@type": ["owl:Ontology"]},
    )
    ids = ["estleg:Harmonisation_31990L0314"]
    assert harmonisation.update_law_file_harmonisation(path, ids) is True
    # Second call with the same id adds nothing and reports no modification.
    assert harmonisation.update_law_file_harmonisation(path, ids) is False
    refs = json.loads(path.read_text(encoding="utf-8"))["@graph"][0][
        "estleg:harmonisedWith"
    ]
    assert [r["@id"] for r in refs] == ids


def test_update_law_file_merges_with_existing_refs(tmp_path):
    """A pre-existing harmonisedWith list is preserved and only genuinely
    new ids are appended (no duplicates)."""
    path = _write_law_peep(
        tmp_path,
        {
            "@id": "estleg:SomeAct_Map_2026",
            "@type": ["owl:Ontology"],
            "estleg:harmonisedWith": [{"@id": "estleg:Harmonisation_31990L0314"}],
        },
    )
    changed = harmonisation.update_law_file_harmonisation(
        path,
        ["estleg:Harmonisation_31990L0314", "estleg:Harmonisation_32000L0060"],
    )
    assert changed is True
    refs = json.loads(path.read_text(encoding="utf-8"))["@graph"][0][
        "estleg:harmonisedWith"
    ]
    assert [r["@id"] for r in refs] == [
        "estleg:Harmonisation_31990L0314",
        "estleg:Harmonisation_32000L0060",
    ]


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


# ---------------------------------------------------------------------------
# #425 — migrate_harmonises_inverse: offline rename of the committed harm
# files' inverted back-edge (estleg:harmonisedWith → estleg:harmonises).
# ---------------------------------------------------------------------------

import migrate_harmonises_inverse as migrate  # noqa: E402


def _make_harm_file(tmp_path: Path, *, predicate: str) -> Path:
    """Write a minimal harm file whose aggregate node uses ``predicate``."""
    doc = {
        "@context": {},
        "@graph": [
            {
                "@id": "estleg:Harmonisation_31990L0314",
                "@type": ["owl:NamedIndividual", "estleg:HarmonisationLink"],
                "rdfs:label": "Harmonisation: 31990L0314",
                "estleg:sharedDirective": {"@id": "estleg:EU_31990L0314"},
                predicate: [
                    {"@id": "estleg:volaoigusseadus_Osa10_1005_1067"},
                    {"@id": "estleg:VOS_Osa11_271_338"},
                ],
            },
            {
                # Per-country measure node — must be left untouched.
                "@id": "estleg:Harm_31990L0314_LVA_1",
                "@type": ["owl:NamedIndividual", "estleg:HarmonisationLink"],
                "rdfs:label": "Latvia: x",
                "estleg:memberStateCode": "LVA",
                "estleg:nationalCelex": "71990L0314LVA_1",
            },
        ],
    }
    path = tmp_path / "harm_31990L0314.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_migrate_renames_inverted_edge_preserving_objects(tmp_path):
    path = _make_harm_file(tmp_path, predicate="estleg:harmonisedWith")
    changed = migrate.process_file(path, dry_run=False)
    assert changed == 1
    agg = json.loads(path.read_text(encoding="utf-8"))["@graph"][0]
    assert "estleg:harmonisedWith" not in agg
    assert [r["@id"] for r in agg["estleg:harmonises"]] == [
        "estleg:volaoigusseadus_Osa10_1005_1067",
        "estleg:VOS_Osa11_271_338",
    ]


def test_migrate_is_idempotent(tmp_path):
    path = _make_harm_file(tmp_path, predicate="estleg:harmonisedWith")
    assert migrate.process_file(path, dry_run=False) == 1
    # A file already on the new predicate is a no-op.
    assert migrate.process_file(path, dry_run=False) == 0


def test_migrate_dry_run_writes_nothing(tmp_path):
    path = _make_harm_file(tmp_path, predicate="estleg:harmonisedWith")
    before = path.read_text(encoding="utf-8")
    assert migrate.process_file(path, dry_run=True) == 1
    assert path.read_text(encoding="utf-8") == before


def test_migrate_leaves_per_country_nodes_untouched(tmp_path):
    path = _make_harm_file(tmp_path, predicate="estleg:harmonisedWith")
    migrate.process_file(path, dry_run=False)
    measure = json.loads(path.read_text(encoding="utf-8"))["@graph"][1]
    assert "estleg:harmonises" not in measure
    assert "estleg:harmonisedWith" not in measure
    assert measure["estleg:nationalCelex"] == "71990L0314LVA_1"


def test_get_law_harmonisation_target_skips_deprecated(tmp_path, monkeypatch):
    """#578: a deprecated/replaced act must not receive a harmonisation link —
    get_law_harmonisation_target_iri returns None so the caller skips the retired
    VOS_*/volaigusseadus VÕS decomposition."""
    path = _write_law_peep(tmp_path, {
        "@id": "estleg:VOS_Osa3_Contract_Obligations_Liability",
        "@type": ["owl:Ontology", "estleg:Act"],
        "owl:deprecated": True,
        "dcterms:isReplacedBy": {"@id": "estleg:volaoigusseadus_Osa3_271_421"},
    })
    monkeypatch.setattr(harmonisation, "KRR_DIR", tmp_path)
    assert harmonisation.get_law_harmonisation_target_iri(path.name) is None


def test_update_law_file_harmonisation_skips_deprecated(tmp_path):
    """#578 P2: even called directly with a deprecated act, the write path must
    NOT add estleg:harmonisedWith — defense in depth against a stale/hand-edited
    mapping reintroducing the exact deprecated links this change prevents."""
    path = _write_law_peep(tmp_path, {
        "@id": "estleg:VOS_Osa3_Contract_Obligations_Liability",
        "@type": ["owl:Ontology", "estleg:Act"],
        "owl:deprecated": True,
        "dcterms:isReplacedBy": {"@id": "estleg:volaoigusseadus_Osa3_271_421"},
    })
    updated = harmonisation.update_law_file_harmonisation(
        path, ["estleg:Harmonisation_31990L0314"]
    )
    assert updated is False
    written = json.loads(path.read_text(encoding="utf-8"))["@graph"][0]
    assert "estleg:harmonisedWith" not in written


class TestPickHarmonisationLawFile:
    """#578b/#631: VÕS multi-Part directives must anchor on the subject-correct
    Part, not the string-sorted law_files[0] (= the torts Part Osa10)."""

    def test_vos_directive_uses_override_part(self):
        # Consumer Sales (31999L0044) must land on the sales Part (Osa2), even
        # though osa10 sorts first in the law_files list.
        files = ["volaoigusseadus_osa10_peep.json", "volaoigusseadus_osa2_peep.json"]
        assert harmonisation.pick_harmonisation_law_file("31999L0044", files) == (
            "volaoigusseadus_osa2_peep.json"
        )

    def test_vos_directive_drop_returns_none(self):
        # Employer Sanctions (32009L0052): VÕS is not the transposing vehicle.
        files = ["volaoigusseadus_osa10_peep.json", "volaoigusseadus_osa1_peep.json"]
        assert harmonisation.pick_harmonisation_law_file("32009L0052", files) is None

    def test_non_vos_row_uses_first_file(self):
        # A non-VÕS row (no volaoigusseadus_osa* file) keeps the default first-file
        # behaviour even for an override CELEX.
        assert harmonisation.pick_harmonisation_law_file(
            "32008L0048", ["tarbijakaitseseadus_peep.json"]
        ) == "tarbijakaitseseadus_peep.json"

    def test_vos_non_override_directive_uses_first_file(self):
        assert harmonisation.pick_harmonisation_law_file(
            "39999L9999", ["volaoigusseadus_osa1_peep.json"]
        ) == "volaoigusseadus_osa1_peep.json"

    def test_empty_files_returns_none(self):
        assert harmonisation.pick_harmonisation_law_file("31999L0044", []) is None
