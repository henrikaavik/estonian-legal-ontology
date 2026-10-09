"""Unit tests for the shared raw-vs-overlay merge-on-write rule (#697)."""

from __future__ import annotations

import argparse
import json
import logging

from estleg.ingest_overlay import (
    IngestLayer,
    add_replace_overlays_argument,
    keep_refined,
    max_int,
    merge_node,
    merge_overlays,
    prepare_write,
    union_values,
)

LAYER = IngestLayer(
    name="test",
    raw_keys=frozenset({"rdfs:label", "ex:raw"}),
    raw_node_types=frozenset({"ex:Raw", "owl:Ontology"}),
    overlay_types=frozenset({"ex:Refined"}),
    union_keys=frozenset({"ex:method"}),
    seed_keys=frozenset({"ex:seed"}),
    combiners={"ex:count": max_int, "ex:kind": keep_refined({"@id": "ex:Other"})},
)


def test_new_raw_value_wins_overlay_kept_and_key_order_preserved():
    existing = {"@id": "a", "@type": ["ex:Raw"], "rdfs:label": "old", "ex:link": [{"@id": "b"}], "ex:raw": 1}
    new = {"@id": "a", "@type": ["ex:Raw"], "rdfs:label": "new", "ex:raw": 2, "ex:fresh": True}
    merged = merge_node(new, existing, LAYER)
    assert list(merged) == ["@id", "@type", "rdfs:label", "ex:link", "ex:raw", "ex:fresh"]
    assert merged["rdfs:label"] == "new" and merged["ex:raw"] == 2
    assert merged["ex:link"] == [{"@id": "b"}]


def test_raw_key_the_source_dropped_is_removed():
    merged = merge_node({"@id": "a"}, {"@id": "a", "ex:raw": 1, "ex:overlay": 2}, LAYER)
    assert merged == {"@id": "a", "ex:overlay": 2}


def test_overlay_type_kept_other_types_follow_the_build():
    existing = {"@id": "a", "@type": ["ex:Raw", "ex:Refined", "ex:Stale"]}
    merged = merge_node({"@id": "a", "@type": ["ex:Raw", "ex:New"]}, existing, LAYER)
    assert merged["@type"] == ["ex:Raw", "ex:Refined", "ex:New"]


def test_union_combiner_and_seed_keys():
    existing = {"@id": "a", "ex:method": ["enricher"], "ex:count": 3, "ex:kind": {"@id": "ex:Civil"}}
    new = {
        "@id": "a",
        "ex:method": "ingest",
        "ex:count": 1,
        "ex:kind": {"@id": "ex:Other"},
        "ex:seed": ["raw-token"],
    }
    merged = merge_node(new, existing, LAYER)
    assert merged["ex:method"] == ["enricher", "ingest"]
    assert merged["ex:count"] == 3
    assert merged["ex:kind"] == {"@id": "ex:Civil"}
    # Seed key: absent on the existing node stays absent.
    assert "ex:seed" not in merged
    seeded = merge_node(new, {"@id": "a", "ex:seed": ["Normalised"]}, LAYER)
    assert seeded["ex:seed"] == ["Normalised"]


def test_keep_refined_lets_a_real_raw_value_win():
    combine = keep_refined({"@id": "ex:Other"})
    assert combine({"@id": "ex:Other"}, {"@id": "ex:Civil"}) == {"@id": "ex:Civil"}
    assert combine({"@id": "ex:Civil"}, {"@id": "ex:Other"}) == {"@id": "ex:Civil"}


def test_same_value_set_keeps_published_serialisation():
    existing = {"@id": "a", "ex:raw": ["y", "x"], "rdfs:label": "C-1/90"}
    merged = merge_node({"@id": "a", "ex:raw": ["x", "y"], "rdfs:label": ["C-1/90"]}, existing, LAYER)
    assert merged["ex:raw"] == ["y", "x"]
    assert merged["rdfs:label"] == "C-1/90"
    changed = merge_node({"@id": "a", "ex:raw": ["x", "z"]}, existing, LAYER)
    assert changed["ex:raw"] == ["x", "z"]


def test_union_values_shapes():
    assert union_values("a", "a") == "a"
    assert union_values("a", "b") == ["a", "b"]
    assert union_values(["a"], ["a", "b"]) == ["a", "b"]


def test_graph_overlay_nodes_keep_position_and_raw_orphans_drop():
    existing = {
        "@context": {"ex": "urn:ex:"},
        "@graph": [
            {"@id": "map", "@type": ["owl:Ontology"]},
            {"@id": "r1", "@type": ["ex:Raw"], "ex:o": 1},
            {"@id": "c1", "@type": ["ex:Citation"]},
            {"@id": "gone", "@type": ["ex:Raw"]},
            {"@id": "r2", "@type": ["ex:Raw"]},
            {"@id": "c2", "@type": ["ex:Citation"]},
        ],
    }
    new = {
        "@context": {"ex": "urn:ex:"},
        "@graph": [
            {"@id": "map", "@type": ["owl:Ontology"]},
            {"@id": "r1", "@type": ["ex:Raw"]},
            {"@id": "r2", "@type": ["ex:Raw"]},
            {"@id": "r3", "@type": ["ex:Raw"]},
        ],
    }
    merged, report = merge_overlays(new, existing, LAYER)
    assert [n["@id"] for n in merged["@graph"]] == ["map", "r1", "c1", "r2", "c2", "r3"]
    assert merged["@graph"][1]["ex:o"] == 1
    assert report.raw_nodes_dropped == 1
    assert report.overlay_nodes_kept == {"ex:Citation": 2}
    assert report.overlay_keys_kept == {"ex:o": 1}


def test_unchanged_reingest_is_identity():
    existing = {
        "@context": {"ex": "urn:ex:", "rdfs": "urn:rdfs:"},
        "@graph": [{"@id": "r1", "@type": ["ex:Raw"], "rdfs:label": "x", "ex:o": [1]}, {"@id": "c", "@type": ["ex:C"]}],
    }
    raw = {"@context": {"ex": "urn:ex:", "rdfs": "urn:rdfs:"}, "@graph": [{"@id": "r1", "@type": ["ex:Raw"], "rdfs:label": "x"}]}
    merged, _ = merge_overlays(raw, existing, LAYER)
    assert json.dumps(merged) == json.dumps(existing)


def test_published_context_kept_unless_a_used_prefix_is_missing():
    old_ctx = {"ex": "urn:ex:"}
    new_ctx = {"ex": "urn:ex:", "unused": "urn:u:", "used": "urn:used:"}
    existing = {"@context": old_ctx, "@graph": [{"@id": "ex:a", "@type": ["ex:Raw"]}]}
    same = {"@context": new_ctx, "@graph": [{"@id": "ex:a", "@type": ["ex:Raw"]}]}
    assert merge_overlays(same, existing, LAYER)[0]["@context"] == old_ctx
    needs = {"@context": new_ctx, "@graph": [{"@id": "ex:a", "@type": ["ex:Raw"], "used:p": 1}]}
    assert merge_overlays(needs, existing, LAYER)[0]["@context"] == new_ctx


def test_no_existing_document_returns_new():
    new = {"@graph": [{"@id": "a"}]}
    assert merge_overlays(new, None, LAYER)[0] == new


def test_prepare_write_replace_overlays_returns_raw_and_logs(tmp_path, caplog):
    path = tmp_path / "peep.json"
    path.write_text(json.dumps({"@graph": [{"@id": "a", "@type": ["ex:Raw"], "ex:o": 1}]}), encoding="utf-8")
    raw = {"@graph": [{"@id": "a", "@type": ["ex:Raw"]}]}
    kept, report = prepare_write(path, raw, LAYER)
    assert kept["@graph"][0]["ex:o"] == 1 and report.overlay_total == 1
    with caplog.at_level(logging.WARNING):
        dropped, _ = prepare_write(path, raw, LAYER, replace_overlays=True)
    assert dropped is raw
    assert any("--replace-overlays" in r.message and "ex:o=1" in r.message for r in caplog.records)


def test_replace_overlays_flag_defaults_off():
    parser = argparse.ArgumentParser()
    add_replace_overlays_argument(parser)
    assert parser.parse_args([]).replace_overlays is False
    assert parser.parse_args(["--replace-overlays"]).replace_overlays is True
