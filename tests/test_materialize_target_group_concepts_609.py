"""Tests for the SKOS target-group concept materialization (#609).

Covers the per-node transform (``materialize_node``) on synthetic nodes — single
string, list, unknown value, duplicates, idempotency, orphan cleanup — plus the
file-walking ``run`` / ``main`` orchestration (dry-run default, ``--apply``
writes, ``--dry-run`` overrides ``--apply``, Git-LFS pointer skip).
"""

import json
import sys
from pathlib import Path

# conftest.py already adds scripts/ to sys.path; this mirror keeps the module
# importable when the file is run in isolation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import materialize_target_group_concepts_609 as M  # noqa: E402

CITIZEN = {"@id": "estleg:TargetGroup_Citizen"}
BUSINESS = {"@id": "estleg:TargetGroup_Business"}
PUBLIC_BODY = {"@id": "estleg:TargetGroup_PublicBody"}
NGO = {"@id": "estleg:TargetGroup_NGO"}


# ---------------------------------------------------------------------------
# Per-node transform
# ---------------------------------------------------------------------------

def test_single_string_maps_and_retains_original():
    node = {"@id": "estleg:X_Par_1", "estleg:targetGroup": "citizen"}
    change = M.materialize_node(node)

    assert node["estleg:targetGroupConcept"] == [CITIZEN]
    # Original bare-string enum is left untouched (purely additive).
    assert node["estleg:targetGroup"] == "citizen"
    assert change.changed is True
    assert change.had_target_group is True
    assert change.links_emitted == 1
    assert change.unknown_values == []


def test_list_value_is_sorted_and_deduped():
    # Input order is reversed relative to the sorted IRI order to prove sorting.
    node = {"estleg:targetGroup": ["business", "ngo"]}
    change = M.materialize_node(node)

    assert node["estleg:targetGroupConcept"] == [BUSINESS, NGO]
    assert node["estleg:targetGroup"] == ["business", "ngo"]
    assert change.links_emitted == 2


def test_list_value_sorts_out_of_order_input():
    node = {"estleg:targetGroup": ["public_body", "ngo", "business"]}
    M.materialize_node(node)
    # Sorted by IRI string: Business < NGO < PublicBody.
    assert node["estleg:targetGroupConcept"] == [BUSINESS, NGO, PUBLIC_BODY]


def test_unknown_value_is_skipped_and_counted():
    node = {"estleg:targetGroup": ["citizen", "alien"]}
    change = M.materialize_node(node)

    # The unmapped value contributes no link, only the known one survives.
    assert node["estleg:targetGroupConcept"] == [CITIZEN]
    assert change.links_emitted == 1
    assert change.unknown_values == ["alien"]


def test_all_unknown_emits_no_concept_key():
    node = {"estleg:targetGroup": "alien"}
    change = M.materialize_node(node)

    assert "estleg:targetGroupConcept" not in node
    assert change.had_target_group is True
    assert change.links_emitted == 0
    assert change.unknown_values == ["alien"]
    # previous None -> new None: no net change.
    assert change.changed is False


def test_duplicate_values_deduped():
    node = {"estleg:targetGroup": ["citizen", "citizen", "business", "citizen"]}
    change = M.materialize_node(node)

    assert node["estleg:targetGroupConcept"] == [BUSINESS, CITIZEN]
    assert change.links_emitted == 2


def test_idempotent_rerun_is_noop():
    node = {"estleg:targetGroup": ["business", "citizen"]}
    first = M.materialize_node(node)
    snapshot = json.loads(json.dumps(node))  # deep copy

    second = M.materialize_node(node)

    assert first.changed is True
    assert second.changed is False
    assert second.links_emitted == 2  # still reports the (unchanged) links
    assert node == snapshot  # byte-identical structure after the no-op re-run


def test_node_without_target_group_untouched():
    node = {"@id": "estleg:X", "rdfs:label": "no addressee here"}
    change = M.materialize_node(node)

    assert change.changed is False
    assert change.had_target_group is False
    assert change.links_emitted == 0
    assert "estleg:targetGroupConcept" not in node
    assert node == {"@id": "estleg:X", "rdfs:label": "no addressee here"}


def test_orphan_concept_cleaned_when_target_group_removed():
    # targetGroup was removed since the last run; the stale concept must go.
    node = {"@id": "estleg:X", "estleg:targetGroupConcept": [CITIZEN]}
    change = M.materialize_node(node)

    assert "estleg:targetGroupConcept" not in node
    assert change.changed is True
    assert change.had_target_group is False


def test_stale_concept_recomputed_from_current_target_group():
    # A wrong/stale link is corrected by the strip-then-recompute.
    node = {"estleg:targetGroup": "business", "estleg:targetGroupConcept": [CITIZEN]}
    change = M.materialize_node(node)

    assert node["estleg:targetGroupConcept"] == [BUSINESS]
    assert change.changed is True


def test_compact_iri_strings_map_to_concepts():
    node = {"estleg:targetGroup": ["estleg:TargetGroup_Business"]}
    change = M.materialize_node(node)
    assert node["estleg:targetGroupConcept"] == [BUSINESS]
    assert change.links_emitted == 1
    assert change.unknown_values == []


def test_iri_object_values_map_to_concepts():
    node = {"estleg:targetGroup": [{"@id": "estleg:TargetGroup_Citizen"}]}
    change = M.materialize_node(node)
    assert node["estleg:targetGroupConcept"] == [CITIZEN]
    assert change.links_emitted == 1
    assert node["estleg:targetGroup"] == [{"@id": "estleg:TargetGroup_Citizen"}]


# ---------------------------------------------------------------------------
# File-walking orchestration
# ---------------------------------------------------------------------------

def _write_peep(path: Path, graph: list) -> None:
    path.write_text(json.dumps({"@graph": graph}), encoding="utf-8")


def test_run_apply_writes_concept_to_disk(tmp_path):
    peep = tmp_path / "demo_peep.json"
    _write_peep(
        peep,
        [
            {"@id": "estleg:X_Par_1", "estleg:targetGroup": "citizen"},
            {"@id": "estleg:X_Par_2", "estleg:targetGroup": ["business", "ngo"]},
            {"@id": "estleg:X_Map", "@type": ["owl:Ontology"]},  # no targetGroup
        ],
    )
    stats = M.run(should_write=True, krr_dir=tmp_path)

    doc = json.loads(peep.read_text(encoding="utf-8"))
    nodes = {n["@id"]: n for n in doc["@graph"]}
    assert nodes["estleg:X_Par_1"]["estleg:targetGroupConcept"] == [CITIZEN]
    assert nodes["estleg:X_Par_2"]["estleg:targetGroupConcept"] == [BUSINESS, NGO]
    assert "estleg:targetGroupConcept" not in nodes["estleg:X_Map"]

    assert stats.peeps_scanned == 1
    assert stats.provisions_with_target_group == 2
    assert stats.links_emitted == 3
    assert stats.nodes_changed == 2
    assert stats.peeps_changed == 1


def test_run_dry_run_does_not_write(tmp_path):
    peep = tmp_path / "demo_peep.json"
    _write_peep(peep, [{"@id": "estleg:X", "estleg:targetGroup": "citizen"}])
    original = peep.read_text(encoding="utf-8")

    stats = M.run(should_write=False, krr_dir=tmp_path)

    # Counts still computed, but the file on disk is untouched.
    assert stats.nodes_changed == 1
    assert stats.peeps_changed == 1
    assert peep.read_text(encoding="utf-8") == original


def test_run_idempotent_second_pass_no_change(tmp_path):
    peep = tmp_path / "demo_peep.json"
    _write_peep(peep, [{"@id": "estleg:X", "estleg:targetGroup": ["citizen", "business"]}])

    M.run(should_write=True, krr_dir=tmp_path)
    after_first = peep.read_text(encoding="utf-8")
    stats2 = M.run(should_write=True, krr_dir=tmp_path)

    assert stats2.nodes_changed == 0
    assert stats2.peeps_changed == 0
    assert peep.read_text(encoding="utf-8") == after_first


def test_run_cleans_orphan_concept_on_disk(tmp_path):
    peep = tmp_path / "stale_peep.json"
    _write_peep(peep, [{"@id": "estleg:X", "estleg:targetGroupConcept": [CITIZEN]}])

    stats = M.run(should_write=True, krr_dir=tmp_path)

    doc = json.loads(peep.read_text(encoding="utf-8"))
    assert "estleg:targetGroupConcept" not in doc["@graph"][0]
    assert stats.nodes_changed == 1
    assert stats.peeps_changed == 1


def test_run_skips_lfs_pointer(tmp_path):
    peep = tmp_path / "big_peep.json"
    peep.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:deadbeef\nsize 999999\n",
        encoding="utf-8",
    )
    stats = M.run(should_write=True, krr_dir=tmp_path)

    assert stats.peeps_skipped_lfs == 1
    assert stats.peeps_scanned == 0
    assert stats.nodes_changed == 0


def test_run_unknown_values_aggregated(tmp_path):
    peep = tmp_path / "demo_peep.json"
    _write_peep(
        peep,
        [
            {"@id": "estleg:A", "estleg:targetGroup": ["citizen", "alien"]},
            {"@id": "estleg:B", "estleg:targetGroup": "robot"},
            {"@id": "estleg:C", "estleg:targetGroup": "alien"},
        ],
    )
    stats = M.run(should_write=False, krr_dir=tmp_path)

    assert stats.unknown_values["alien"] == 2
    assert stats.unknown_values["robot"] == 1


# ---------------------------------------------------------------------------
# main() flag semantics
# ---------------------------------------------------------------------------

def test_main_default_is_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "KRR_DIR", tmp_path)
    peep = tmp_path / "demo_peep.json"
    _write_peep(peep, [{"@id": "estleg:X", "estleg:targetGroup": "citizen"}])
    original = peep.read_text(encoding="utf-8")

    rc = M.main([])

    assert rc == 0
    assert peep.read_text(encoding="utf-8") == original  # nothing written


def test_main_apply_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "KRR_DIR", tmp_path)
    peep = tmp_path / "demo_peep.json"
    _write_peep(peep, [{"@id": "estleg:X", "estleg:targetGroup": "citizen"}])

    rc = M.main(["--apply"])

    assert rc == 0
    doc = json.loads(peep.read_text(encoding="utf-8"))
    assert doc["@graph"][0]["estleg:targetGroupConcept"] == [CITIZEN]


def test_main_dry_run_overrides_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "KRR_DIR", tmp_path)
    peep = tmp_path / "demo_peep.json"
    _write_peep(peep, [{"@id": "estleg:X", "estleg:targetGroup": "citizen"}])
    original = peep.read_text(encoding="utf-8")

    rc = M.main(["--apply", "--dry-run"])

    assert rc == 0
    assert peep.read_text(encoding="utf-8") == original  # override wins: no write
