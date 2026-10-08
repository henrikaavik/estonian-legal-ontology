"""Tier 1 repairs to validate_all rules that no longer modelled the corpus.

Each accepted shape has a regression test proving the rule still fires on the
nearest bad input:

* multipart statutes (#379 / #566): map peep + per-osa ``estleg:Part`` peeps;
* act-level temporal properties copied onto those Part roots;
* cross-file @id uniqueness vs. untyped overlay join nodes (#521 / #429),
  plus the dedicated ``dcterms:isReplacedBy``-self line (#426).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from estleg import validate_all


@pytest.fixture(autouse=True)
def _reset_reporter():
    validate_all.reset()
    yield
    validate_all.reset()


def _write(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Multipart registry acceptance
# ---------------------------------------------------------------------------

ACT = "estleg:X_Map"


def _map_doc(has_part: list[str] | None) -> dict:
    node: dict = {"@id": ACT, "@type": ["estleg:Act", "estleg:Law"]}
    if has_part is not None:
        node["estleg:hasPart"] = [{"@id": p} for p in has_part]
    return {"@graph": [node]}


def _part_doc(part_id: str, *, parent: str | None = ACT) -> dict:
    root: dict = {
        "@id": part_id,
        "@type": ["estleg:Part"],
        "estleg:temporalStatus": "inForce",
        "estleg:lastAmendmentDate": {"@value": "2026-01-01", "@type": "xsd:date"},
    }
    if parent is not None:
        root["estleg:isPartOf"] = {"@id": parent}
    provision = {
        "@id": f"{part_id}_Par_1",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": "§ 1.",
    }
    return {"@graph": [root, provision]}


def _multipart_krr(
    tmp_path: Path,
    *,
    has_part: list[str] | None = ("estleg:X_Osa1", "estleg:X_Osa2"),
    osa1_parent: str | None = ACT,
    parts_mapped: bool = True,
) -> Path:
    krr = tmp_path / "krr_outputs"
    _write(krr / "x_map_peep.json", _map_doc(list(has_part) if has_part is not None else None))
    _write(krr / "x_osa1_peep.json", _part_doc("estleg:X_Osa1", parent=osa1_parent))
    _write(krr / "x_osa2_peep.json", _part_doc("estleg:X_Osa2"))
    entry: dict = {
        "name": "x",
        "files": ["x_map_peep.json", "x_osa1_peep.json", "x_osa2_peep.json"],
    }
    if parts_mapped:
        entry["parts_mapped"] = ["1", "2"]
    _write(krr / "INDEX.json", {"total_laws": 1, "total_files": 3, "laws": [entry]})
    return krr


def test_registry_accepts_multipart_map_and_part_files(tmp_path):
    validate_all.validate_registry_index(_multipart_krr(tmp_path))
    assert validate_all.errors == []


def test_registry_rejects_part_root_without_is_part_of(tmp_path):
    krr = _multipart_krr(tmp_path, osa1_parent=None)

    validate_all.validate_registry_index(krr)

    # The map now also lists a part that no sibling file roots as its Part.
    assert sorted(validate_all.errors) == [
        "x_map_peep.json: indexed file has no provision nodes and no registry exception",
        "x_osa1_peep.json: indexed file has 0 act-level nodes (expected 1)",
    ]


def test_registry_rejects_part_root_whose_parent_is_not_the_entry_act(tmp_path):
    krr = _multipart_krr(tmp_path, osa1_parent="estleg:Other_Map")

    validate_all.validate_registry_index(krr)

    assert "x_osa1_peep.json: indexed file has 0 act-level nodes (expected 1)" in validate_all.errors


def test_registry_rejects_map_listing_a_missing_part(tmp_path):
    krr = _multipart_krr(tmp_path, has_part=["estleg:X_Osa1", "estleg:X_Osa2", "estleg:X_Osa9"])

    validate_all.validate_registry_index(krr)

    assert validate_all.errors == [
        "x_map_peep.json: indexed file has no provision nodes and no registry exception"
    ]


def test_registry_rejects_map_without_has_part(tmp_path):
    krr = _multipart_krr(tmp_path, has_part=None)

    validate_all.validate_registry_index(krr)

    assert validate_all.errors == [
        "x_map_peep.json: indexed file has no provision nodes and no registry exception"
    ]


def test_registry_requires_parts_mapped_on_the_index_entry(tmp_path):
    """Without the INDEX multipart marker the split files are not accepted."""
    krr = _multipart_krr(tmp_path, parts_mapped=False)

    validate_all.validate_registry_index(krr)

    assert sorted(validate_all.errors) == [
        "x_map_peep.json: indexed file has no provision nodes and no registry exception",
        "x_osa1_peep.json: indexed file has 0 act-level nodes (expected 1)",
        "x_osa2_peep.json: indexed file has 0 act-level nodes (expected 1)",
    ]


# ---------------------------------------------------------------------------
# Temporal placement on Part roots
# ---------------------------------------------------------------------------


def test_temporal_placement_tolerates_part_root_of_a_real_act(tmp_path):
    krr = _multipart_krr(tmp_path)
    files = sorted(krr.glob("x_*_peep.json"))

    validate_all.validate_temporal_property_targets(files)

    assert validate_all.errors == []


def test_temporal_placement_flags_part_root_without_is_part_of(tmp_path):
    krr = _multipart_krr(tmp_path, osa1_parent=None)
    files = sorted(krr.glob("x_*_peep.json"))

    validate_all.validate_temporal_property_targets(files)

    assert validate_all.errors == ["2 act-level temporal properties on non-Act nodes"]


def test_temporal_placement_flags_part_root_whose_parent_is_not_an_act(tmp_path):
    krr = _multipart_krr(tmp_path)
    # The parent exists but is a concept, not an estleg:Act.
    _write(krr / "x_map_peep.json", {"@graph": [{"@id": ACT, "@type": ["estleg:LegalConcept"]}]})
    files = sorted(krr.glob("x_*_peep.json"))

    validate_all.validate_temporal_property_targets(files)

    assert validate_all.errors == ["4 act-level temporal properties on non-Act nodes"]


def test_temporal_placement_still_flags_non_part_nodes(tmp_path):
    path = _write(
        tmp_path / "c_peep.json",
        {"@graph": [{"@id": "estleg:C", "@type": ["estleg:LegalConcept"], "estleg:temporalStatus": "x"}]},
    )

    validate_all.validate_temporal_property_targets([path])

    assert validate_all.errors == ["1 act-level temporal properties on non-Act nodes"]


# ---------------------------------------------------------------------------
# Cross-file @id uniqueness vs. overlay join nodes
# ---------------------------------------------------------------------------


def _collect(krr: Path, files: list[Path]):
    """Mirror validate_all.main's id collection for the given files."""
    all_ids: dict[str, list[str]] = defaultdict(list)
    join_ids: dict[str, list[str]] = defaultdict(list)
    self_replaced: dict[str, list[str]] = defaultdict(list)
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        overlay = validate_all.is_overlay_surface_file(path, krr)
        for node in doc["@graph"]:
            nid = node.get("@id", "")
            all_ids[nid].append(path.name)
            if overlay and validate_all.is_join_assertion_node(node):
                join_ids[nid].append(path.name)
            if validate_all.is_self_replaced_node(node):
                self_replaced[nid].append(path.name)
    return all_ids, join_ids, self_replaced


def _check_ids(krr: Path, files: list[Path]) -> list[str]:
    all_ids, join_ids, self_replaced = _collect(krr, files)
    validate_all.validate_id_uniqueness(all_ids, join_ids=join_ids, self_replaced_ids=self_replaced)
    return validate_all.errors


LAW_NODE = {"@id": "estleg:L_Map", "@type": ["estleg:Act", "estleg:Law"], "rdfs:label": "L"}
JOIN_NODE = {"@id": "estleg:L_Map", "estleg:inboundCitationCount": 3}


def test_uniqueness_accepts_overlay_join_node(tmp_path):
    krr = tmp_path / "krr_outputs"
    law = _write(krr / "l_peep.json", {"@graph": [LAW_NODE]})
    overlay = _write(krr / "analytical" / "analytical_overlay.jsonld", {"@graph": [JOIN_NODE]})

    assert _check_ids(krr, [law, overlay]) == []


def test_uniqueness_flags_typed_redefinition_in_overlay_dir(tmp_path):
    krr = tmp_path / "krr_outputs"
    law = _write(krr / "l_peep.json", {"@graph": [LAW_NODE]})
    overlay = _write(krr / "analytical" / "analytical_overlay.jsonld", {"@graph": [LAW_NODE]})

    assert _check_ids(krr, [law, overlay]) == [
        "1 @id values are duplicated across files (semantic collisions)"
    ]


def test_uniqueness_flags_untyped_node_outside_overlay_dirs(tmp_path):
    krr = tmp_path / "krr_outputs"
    law = _write(krr / "l_peep.json", {"@graph": [LAW_NODE]})
    other = _write(krr / "m_peep.json", {"@graph": [JOIN_NODE]})

    assert _check_ids(krr, [law, other]) == [
        "1 @id values are duplicated across files (semantic collisions)"
    ]


def test_uniqueness_flags_collision_even_when_overlay_also_joins(tmp_path):
    """Two law peeps defining one IRI collide regardless of the overlay."""
    krr = tmp_path / "krr_outputs"
    a = _write(krr / "a_peep.json", {"@graph": [LAW_NODE]})
    b = _write(krr / "b_peep.json", {"@graph": [LAW_NODE]})
    overlay = _write(krr / "analytical" / "analytical_overlay.jsonld", {"@graph": [JOIN_NODE]})

    assert _check_ids(krr, [a, b, overlay]) == [
        "1 @id values are duplicated across files (semantic collisions)"
    ]


def test_uniqueness_flags_two_overlays_joining_an_undefined_iri(tmp_path):
    krr = tmp_path / "krr_outputs"
    one = _write(krr / "analytical" / "analytical_overlay.jsonld", {"@graph": [JOIN_NODE]})
    two = _write(krr / "provision_versions" / "l.jsonld", {"@graph": [JOIN_NODE]})

    assert _check_ids(krr, [one, two]) == [
        "1 @id values are duplicated across files (semantic collisions)"
    ]


def test_uniqueness_leaves_within_file_repeats_to_their_own_rule(tmp_path):
    krr = tmp_path / "krr_outputs"
    law = _write(krr / "l_peep.json", {"@graph": [LAW_NODE, LAW_NODE]})

    assert _check_ids(krr, [law]) == []


def test_uniqueness_reports_self_replaced_node_on_its_own_line(tmp_path):
    krr = tmp_path / "krr_outputs"
    deprecated = dict(LAW_NODE, **{"dcterms:isReplacedBy": {"@id": "estleg:L_Map"}})
    old = _write(krr / "l_old_peep.json", {"@graph": [deprecated]})
    new = _write(krr / "l_peep.json", {"@graph": [LAW_NODE]})

    assert _check_ids(krr, [old, new]) == [
        "1 @id values are duplicated across files (semantic collisions)",
        "1 node(s) declare dcterms:isReplacedBy their own @id "
        "(#426 deprecated duplicate shares its replacement's IRI)",
    ]


def test_replacement_by_another_iri_is_not_self_replacement():
    node = dict(LAW_NODE, **{"dcterms:isReplacedBy": {"@id": "estleg:New_Map"}})
    assert not validate_all.is_self_replaced_node(node)


def test_overlay_surface_detection_uses_public_load_subdirs(tmp_path):
    krr = tmp_path / "krr_outputs"
    assert validate_all.is_overlay_surface_file(krr / "analytical" / "a.jsonld", krr)
    assert validate_all.is_overlay_surface_file(krr / "provision_versions" / "a.jsonld", krr)
    assert not validate_all.is_overlay_surface_file(krr / "a_peep.json", krr)
    assert not validate_all.is_overlay_surface_file(krr / "scratch" / "a.jsonld", krr)
