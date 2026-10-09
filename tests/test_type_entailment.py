"""Regression tests for build-time rdf:type entailment (#519).

``generate_combined_jsonld()`` forward-chains rdf:type over the subclass
hierarchy so the shipped graph answers ``?x a estleg:LegalProvision`` /
``?x a estleg:Act`` without a reasoner. Before #519, ``?x a estleg:LegalProvision``
matched only 71 of ~160k provision nodes: instances carried only their per-file
``estleg:LegalProvision_<slug>`` (or ``estleg:Regulation_<id>``) leaf class,
``estleg:Subsection``, etc., never the bare ``estleg:LegalProvision`` parent.

The core logic lives in the pure helper ``fix_all_issues._materialize_supertypes``.
These tests exercise it directly on small in-memory ``@type`` lists (no 260 MB
corpus needed) for the rollup rules and determinism, and additionally assert the
materialization actually landed in the regenerated ``combined_ontology.jsonld``
when a populated, de-LFS-pointered corpus is present (``-m corpus``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import fix_all_issues as fix
from estleg.validate_all import _is_lfs_pointer

ROOT = Path(__file__).resolve().parent.parent

COMBINED = ROOT / "krr_outputs" / "combined_ontology.jsonld"


# --- unit: _materialize_supertypes (pure helper, always runs) ---------------


def test_legalprovision_slug_rolls_up_to_legalprovision():
    """(a) A per-file ``LegalProvision_<slug>`` instance gains the bare parent."""
    types = ["owl:NamedIndividual", "estleg:LegalProvision_karistusseadustik"]
    rolled = fix._materialize_supertypes(types)
    assert "estleg:LegalProvision" in rolled
    # Original entries preserved in place; the parent is appended, not inserted.
    assert rolled[: len(types)] == types
    assert rolled[-1] == "estleg:LegalProvision"


def test_regulation_leaf_rolls_up_to_legalprovision():
    """A per-regulation ``Regulation_<id>`` provision leaf gains LegalProvision."""
    rolled = fix._materialize_supertypes(
        ["owl:NamedIndividual", "estleg:Regulation_1000010"]
    )
    assert "estleg:LegalProvision" in rolled


def test_subsection_rolls_up_to_legalprovision():
    """(b) A ``Subsection`` (lõige) gains LegalProvision."""
    rolled = fix._materialize_supertypes(["owl:NamedIndividual", "estleg:Subsection"])
    assert "estleg:LegalProvision" in rolled


def test_kovprovision_rolls_up_to_legalprovision_once():
    """A municipal provision (two provision-typing routes) gains it exactly once."""
    rolled = fix._materialize_supertypes(
        ["owl:NamedIndividual", "estleg:Regulation_1001519", "estleg:KovProvision"]
    )
    assert rolled.count("estleg:LegalProvision") == 1


def test_law_rolls_up_to_act():
    """(c) A ``Law`` gains Act."""
    rolled = fix._materialize_supertypes(["owl:Ontology", "estleg:Law"])
    assert "estleg:Act" in rolled


def test_regulation_act_classes_roll_up_to_act():
    """(d) Each ``*Regulation`` act class gains Act (transitively for issuers)."""
    assert "estleg:Act" in fix._materialize_supertypes(
        ["owl:Ontology", "estleg:MunicipalRegulation"]
    )
    assert "estleg:Act" in fix._materialize_supertypes(
        ["owl:Ontology", "estleg:NationalRegulation"]
    )
    # Government/Ministerial -> National -> Act (transitive two-hop chain).
    gov = fix._materialize_supertypes(["owl:Ontology", "estleg:GovernmentRegulation"])
    assert "estleg:NationalRegulation" in gov and "estleg:Act" in gov
    ministerial = fix._materialize_supertypes(
        ["owl:Ontology", "estleg:MinisterialRegulation"]
    )
    assert "estleg:NationalRegulation" in ministerial and "estleg:Act" in ministerial


def test_structural_containers_are_not_rolled_up():
    """Containers stay containers — never asserted as LegalProvision (#519 note)."""
    for container in (
        "estleg:Chapter",
        "estleg:Division",
        "estleg:Section",
        "estleg:Part",
        "estleg:Subdivision",
    ):
        rolled = fix._materialize_supertypes(["owl:NamedIndividual", container])
        assert rolled == ["owl:NamedIndividual", container]


def test_idempotent_when_parent_chain_already_present():
    """A node already carrying its full parent chain is returned unchanged."""
    types = [
        "estleg:Act",
        "estleg:GovernmentRegulation",
        "estleg:NationalRegulation",
        "owl:Ontology",
        "eli:LegalResource",
        "schema:Legislation",
    ]
    assert fix._materialize_supertypes(types) == types


def test_class_declaration_node_untouched():
    """An ``owl:Class`` declaration node carries no rollup-eligible @type."""
    assert fix._materialize_supertypes(["owl:Class"]) == ["owl:Class"]


def test_bare_legalprovision_has_no_self_loop():
    """Bare ``estleg:LegalProvision`` must not match the ``LegalProvision_`` prefix."""
    assert fix._materialize_supertypes(["estleg:LegalProvision"]) == [
        "estleg:LegalProvision"
    ]


def test_helper_does_not_mutate_input():
    types = ["owl:NamedIndividual", "estleg:Subsection"]
    snapshot = list(types)
    fix._materialize_supertypes(types)
    assert types == snapshot


# --- (e) determinism --------------------------------------------------------


def test_materialize_is_deterministic():
    """(e) Same input -> byte-identical output order across repeated calls.

    The helper must not use ``set()`` for ordering (that is PYTHONHASHSEED
    sensitive); repeated calls on the same input must return identical lists so
    the combined build stays byte-stable across runs (AGENTS.md determinism).
    """
    types = [
        "owl:NamedIndividual",
        "estleg:Regulation_1001519",
        "estleg:KovProvision",
        "estleg:Subsection",
        "estleg:LegalProvision_foo",
    ]
    first = fix._materialize_supertypes(types)
    for _ in range(5):
        assert fix._materialize_supertypes(types) == first


def test_apply_type_rollup_in_place_and_count():
    """``_apply_type_rollup`` mutates only the nodes that gain a type."""
    nodes = [
        {"@id": "p", "@type": ["owl:NamedIndividual", "estleg:Subsection"]},
        # already carries Act and its #708 ELI / schema.org alignments
        {
            "@id": "a",
            "@type": ["estleg:Act", "estleg:Law", "eli:LegalResource", "schema:Legislation"],
        },
        {"@id": "c", "@type": ["owl:NamedIndividual", "estleg:Chapter"]},  # container
        {"@id": "x", "@type": "not-a-list"},  # defensively skipped
    ]
    enriched = fix._apply_type_rollup(nodes)
    assert enriched == 1  # only the Subsection node changed
    assert "estleg:LegalProvision" in nodes[0]["@type"]
    assert nodes[1]["@type"] == [
        "estleg:Act",
        "estleg:Law",
        "eli:LegalResource",
        "schema:Legislation",
    ]  # unchanged
    assert "estleg:LegalProvision" not in nodes[2]["@type"]  # container untouched
    assert nodes[3]["@type"] == "not-a-list"  # non-list skipped


# --- corpus: the assertion landed in the real combined graph ----------------


@pytest.mark.corpus
def test_combined_materializes_parent_types():
    """The regenerated combined graph answers the bare-parent type queries (#519).

    Artifact-level invariant: ``?x a estleg:LegalProvision`` covers the full
    provision population (pre-#519 it matched 71 nodes), every provision-typed
    family carries the materialized parent, and every act class carries
    ``estleg:Act``. Since c5625a748b the generators type provisions as plain
    ``estleg:LegalProvision`` and the mechanical per-file ``LegalProvision_<slug>``
    / ``Regulation_<id>`` classes are retired, so the retyped-provision invariant
    is: every instance that carries ``estleg:paragrahv`` (i.e. is a §-level
    provision) is typed ``estleg:LegalProvision``, and the leaf families — if any
    ever reappear — still roll up. SKIPS on a missing / unsmudged-LFS-pointer
    combined so a clean clone without ``git lfs pull`` stays green.
    """
    if not COMBINED.is_file() or _is_lfs_pointer(COMBINED):
        pytest.skip("combined_ontology.jsonld absent or an unsmudged LFS pointer")
    graph = json.loads(COMBINED.read_text(encoding="utf-8"))["@graph"]

    legalprovision_nodes = 0
    paragrahv_provisions = 0
    saw_kov = saw_subsection = saw_law = saw_reg_act = False
    retired_leaf_classes: list[str] = []
    reg_act_classes = {
        "estleg:MunicipalRegulation",
        "estleg:NationalRegulation",
        "estleg:GovernmentRegulation",
        "estleg:MinisterialRegulation",
    }
    leaf_prefixes = tuple(prefix for prefix, _ in fix._TYPE_ROLLUP_PREFIXES)
    for node in graph:
        types = node.get("@type")
        if not isinstance(types, list):
            continue
        ts = set(types)
        if "owl:Class" in ts:
            # TBox class declarations are not instances; c5625a748b retired the
            # per-document leaf classes, so none may be re-minted into combined.
            if str(node.get("@id", "")).startswith(leaf_prefixes):
                retired_leaf_classes.append(node["@id"])
            continue
        if "estleg:LegalProvision" in ts:
            legalprovision_nodes += 1
        # retyped provisions: every §-level provision is a LegalProvision
        if "estleg:paragrahv" in node:
            assert "estleg:LegalProvision" in ts, node.get("@id")
            paragrahv_provisions += 1
        # a leaf-family instance (should none remain) still carries the parent
        if any(t.startswith(leaf_prefixes) for t in types):
            assert "estleg:LegalProvision" in ts, node.get("@id")
        # KovProvision -> LegalProvision
        if "estleg:KovProvision" in ts:
            assert "estleg:LegalProvision" in ts, node.get("@id")
            saw_kov = True
        # Subsection -> LegalProvision
        if "estleg:Subsection" in ts:
            assert "estleg:LegalProvision" in ts, node.get("@id")
            saw_subsection = True
        # Law -> Act
        if "estleg:Law" in ts:
            assert "estleg:Act" in ts, node.get("@id")
            saw_law = True
        # regulation act class -> Act
        if ts & reg_act_classes:
            assert "estleg:Act" in ts, node.get("@id")
            saw_reg_act = True

    assert retired_leaf_classes == [], retired_leaf_classes[:10]
    assert paragrahv_provisions > 10_000, paragrahv_provisions
    assert saw_kov, "no KovProvision instance found in combined"
    assert saw_subsection, "no Subsection instance found in combined"
    assert saw_law, "no Law instance found in combined"
    assert saw_reg_act, "no *Regulation act node found in combined"
    # Headline #519 metric: was 71 pre-fix; now the full provision population.
    assert legalprovision_nodes > 100_000, legalprovision_nodes


def _subclass_parents(node: dict | None) -> list[str]:
    if node is None:
        return []
    sub = node.get("rdfs:subClassOf")
    values = sub if isinstance(sub, list) else [sub]
    out: list[str] = []
    for value in values:
        ref = value.get("@id") if isinstance(value, dict) else value
        if isinstance(ref, str):
            out.append(ref)
    return out


def _entailed_superclasses(by_id: dict, cls: str) -> list[str]:
    """Transitive ``rdfs:subClassOf`` closure of ``cls`` (BFS, cycle-safe)."""
    seen: list[str] = []
    queue = _subclass_parents(by_id.get(cls))
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.append(current)
        queue.extend(_subclass_parents(by_id.get(current)))
    return seen


@pytest.mark.corpus
def test_tbox_axioms_back_the_materialized_types():
    """Each rolled-up parent is entailed by ``rdfs:subClassOf`` axioms (#519).

    The materialized types must be what a reasoner would entail, so for every
    rollup edge ``cls -> parent`` the shipped T-Box (sourced from
    ``controlled_vocabulary.jsonld``) must reach ``parent`` from ``cls`` through
    the subclass chain. The chain need not be a single hop: the regulation
    hierarchy is ``NationalRegulation / MunicipalRegulation ⊑ DomesticRegulation
    ⊑ Act``, so the rollup's direct ``-> Act`` edge is backed transitively.
    """
    if not COMBINED.is_file() or _is_lfs_pointer(COMBINED):
        pytest.skip("combined_ontology.jsonld absent or an unsmudged LFS pointer")
    graph = json.loads(COMBINED.read_text(encoding="utf-8"))["@graph"]
    by_id = {n.get("@id"): n for n in graph if isinstance(n, dict)}

    expected = {
        "estleg:Subsection": "estleg:LegalProvision",
        "estleg:KovProvision": "estleg:LegalProvision",
        "estleg:Law": "estleg:Act",
        "estleg:NationalRegulation": "estleg:Act",
        "estleg:MunicipalRegulation": "estleg:Act",
        "estleg:GovernmentRegulation": "estleg:NationalRegulation",
        "estleg:MinisterialRegulation": "estleg:NationalRegulation",
    }
    # the builder's rollup table must not assert anything the test does not vet
    assert dict(fix._TYPE_ROLLUP_EDGES) == expected
    for cls, parent in expected.items():
        assert cls in by_id, f"{cls} class declaration missing from combined"
        supers = _entailed_superclasses(by_id, cls)
        assert parent in supers, f"{cls} does not entail {parent} (supers: {supers})"
    # the intermediate domestic-regulation tier the chain now runs through
    for cls in ("estleg:NationalRegulation", "estleg:MunicipalRegulation"):
        assert _subclass_parents(by_id[cls]) == ["estleg:DomesticRegulation"], cls
    assert "estleg:Act" in _subclass_parents(by_id.get("estleg:DomesticRegulation"))


# --- #708: ELI / schema.org class alignments ride the rollup ----------------


def test_law_gains_eli_and_schema_types_after_act():
    """A Law gains Act, then Act's ELI / schema.org alignments, in that order."""
    assert fix._materialize_supertypes(["estleg:Law"]) == [
        "estleg:Law",
        "estleg:Act",
        "eli:LegalResource",
        "schema:Legislation",
    ]


def test_regulation_root_gains_eli_types_transitively():
    rolled = fix._materialize_supertypes(["estleg:MinisterialRegulation"])
    assert rolled[-2:] == ["eli:LegalResource", "schema:Legislation"]


def test_act_expression_gains_legal_expression():
    assert fix._materialize_supertypes(["owl:NamedIndividual", "estleg:ActExpression"]) == [
        "owl:NamedIndividual",
        "estleg:ActExpression",
        "eli:LegalExpression",
    ]


def test_provisions_and_containers_gain_no_eli_type():
    """Containers carry no CV ELI axiom; provisions are out of #708's scope."""
    for container in ("estleg:Chapter", "estleg:Division", "estleg:Part", "estleg:LegalProvision"):
        assert fix._materialize_supertypes([container]) == [container]
