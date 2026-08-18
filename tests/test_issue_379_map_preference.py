"""Issue #379 — multipart laws must not resolve to lexicographic osa10."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.ensure_multipart_map_peeps import map_iri_for_osa
from estleg.extract_draft_impact import prefer_act_iri


def test_prefer_act_iri_map_beats_osa():
    assert prefer_act_iri(
        [
            "estleg:tsiviilkohtumenetluse_seadustik_Osa10",
            "estleg:TsMS_Map_2026",
            "estleg:tsiviilkohtumenetluse_seadustik_Osa1",
        ]
    ) == "estleg:TsMS_Map_2026"


def test_prefer_act_iri_lowest_osa_when_no_map():
    assert prefer_act_iri(
        [
            "estleg:volaoigusseadus_Osa10",
            "estleg:volaoigusseadus_Osa2",
            "estleg:volaoigusseadus_Osa1",
        ]
    ) == "estleg:volaoigusseadus_Osa1"


def test_map_iri_for_osa_known_prefixes():
    assert map_iri_for_osa("estleg:KARIST_2_Osa1") == "estleg:KARIST_2_Map_2026"
    assert map_iri_for_osa("estleg:AOS_Osa1") == "estleg:AOS_Map_2026"
    assert map_iri_for_osa("estleg:PKS_Map_2026") is None
    assert map_iri_for_osa("estleg:KARIST_2_Par_1") is None


def test_committed_annotations_do_not_target_osa_parts():
    import re

    path = (
        Path(__file__).resolve().parents[1]
        / "krr_outputs"
        / "annotations"
        / "oiguskantsler_seisukohad.jsonld"
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    leftovers = []
    for node in doc.get("@graph", []):
        raw = node.get("estleg:annotates")
        items = raw if isinstance(raw, list) else [raw] if raw else []
        for item in items:
            iri = item.get("@id") if isinstance(item, dict) else None
            if isinstance(iri, str) and re.search(r"_Osa\d+$", iri):
                leftovers.append(iri)
    assert leftovers == []


def test_index_base_name_groups_map_with_osa_parts():
    from estleg import fix_all_issues as fix

    assert fix._index_base_name("karistusseadustik_map") == ("karistusseadustik", None)
    assert fix._index_base_name("karistusseadustik_osa1") == ("karistusseadustik", "1")


def test_kars_map_peep_exists_and_is_index_first():
    repo = Path(__file__).resolve().parents[1]
    peep = json.loads(
        (repo / "krr_outputs" / "karistusseadustik_map_peep.json").read_text(
            encoding="utf-8"
        )
    )
    assert peep["@graph"][0]["@id"] == "estleg:KARIST_2_Map_2026"
    index = json.loads((repo / "krr_outputs" / "INDEX.json").read_text(encoding="utf-8"))
    kars = next(law for law in index["laws"] if law["name"] == "karistusseadustik")
    assert kars["files"][0] == "karistusseadustik_map_peep.json"
