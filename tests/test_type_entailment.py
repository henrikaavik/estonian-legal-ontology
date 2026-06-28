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
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fix_all_issues as fix  # noqa: E402
from validate_all import _is_lfs_pointer  # noqa: E402

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
        {"@id": "a", "@type": ["estleg:Act", "estleg:Law"]},  # already has Act
        {"@id": "c", "@type": ["owl:NamedIndividual", "estleg:Chapter"]},  # container
        {"@id": "x", "@type": "not-a-list"},  # defensively skipped
    ]
    enriched = fix._apply_type_rollup(nodes)
    assert enriched == 1  # only the Subsection node changed
    assert "estleg:LegalProvision" in nodes[0]["@type"]
    assert nodes[1]["@type"] == ["estleg:Act", "estleg:Law"]  # unchanged
    assert "estleg:LegalProvision" not in nodes[2]["@type"]  # container untouched
    assert nodes[3]["@type"] == "not-a-list"  # non-list skipped


# --- corpus: the assertion landed in the real combined graph ----------------


@pytest.mark.corpus
def test_combined_materializes_parent_types():
    """The regenerated combined graph answers the bare-parent type queries (#519).

    Artifact-level invariant: every sampled-family instance carries the
    materialized parent, and ``?x a estleg:LegalProvision`` is far above the
    pre-fix value of 71. SKIPS on a missing / unsmudged-LFS-pointer combined so a
    clean clone without ``git lfs pull`` stays green.
    """
    if not COMBINED.is_file() or _is_lfs_pointer(COMBINED):
        pytest.skip("combined_ontology.jsonld absent or an unsmudged LFS pointer")
    graph = json.loads(COMBINED.read_text(encoding="utf-8"))["@graph"]

    legalprovision_nodes = 0
    saw_slug = saw_subsection = saw_law = saw_reg_act = False
    reg_act_classes = {
        "estleg:MunicipalRegulation",
        "estleg:NationalRegulation",
        "estleg:GovernmentRegulation",
        "estleg:MinisterialRegulation",
    }
    for node in graph:
        types = node.get("@type")
        if not isinstance(types, list):
            continue
        ts = set(types)
        if "owl:Class" in ts:
            continue  # TBox class declarations are not instances
        if "estleg:LegalProvision" in ts:
            legalprovision_nodes += 1
        # (a) per-file slug instance also carries the bare parent
        if any(t.startswith("estleg:LegalProvision_") for t in types):
            assert "estleg:LegalProvision" in ts, node.get("@id")
            saw_slug = True
        # (b) Subsection
        if "estleg:Subsection" in ts:
            assert "estleg:LegalProvision" in ts, node.get("@id")
            saw_subsection = True
        # (c) Law -> Act
        if "estleg:Law" in ts:
            assert "estleg:Act" in ts, node.get("@id")
            saw_law = True
        # (d) regulation act class -> Act
        if ts & reg_act_classes:
            assert "estleg:Act" in ts, node.get("@id")
            saw_reg_act = True

    assert saw_slug, "no LegalProvision_<slug> instance found in combined"
    assert saw_subsection, "no Subsection instance found in combined"
    assert saw_law, "no Law instance found in combined"
    assert saw_reg_act, "no *Regulation act node found in combined"
    # Headline #519 metric: was 71 pre-fix; now the full provision population.
    assert legalprovision_nodes > 100_000, legalprovision_nodes


@pytest.mark.corpus
def test_tbox_axioms_back_the_materialized_types():
    """Each rolled-up parent is backed by an ``rdfs:subClassOf`` axiom (#519).

    The materialized types must equal what a reasoner would entail, so the
    bare classes the rollup relies on carry the matching subclass axiom in the
    shipped graph (sourced from ``controlled_vocabulary.jsonld``).
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
    for cls, parent in expected.items():
        node = by_id.get(cls)
        assert node is not None, f"{cls} class declaration missing from combined"
        sub = node.get("rdfs:subClassOf")
        sub_id = sub.get("@id") if isinstance(sub, dict) else sub
        assert sub_id == parent, f"{cls} rdfs:subClassOf {sub_id} != {parent}"
