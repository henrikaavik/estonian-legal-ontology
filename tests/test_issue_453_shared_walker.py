"""Issue #453 — one JSON-LD object-ref walker for both closure gates."""

from __future__ import annotations

from pathlib import Path

from estleg import estleg_common, validate_all
from estleg import validate_seadusloome_sync as seadusloome


def test_sync_gate_walker_is_the_shared_helper():
    payload = {
        "estleg:amendsLaw": {"@id": "estleg:Foo"},
        "wrapper": {"estleg:inner": {"@id": "https://w3id.org/estleg/Bar"}},
    }
    shared = list(estleg_common.walk_object_refs(payload, "root"))
    via_sync = list(seadusloome._walk_jsonld_refs(payload, "root"))
    assert via_sync == shared
    assert seadusloome._canonical_estleg_id("https://w3id.org/estleg/Bar") == (
        estleg_common.canonical_estleg_ref("https://w3id.org/estleg/Bar")
    )


def test_validate_all_collect_internal_refs_uses_innermost_predicate():
    doc = {
        "@graph": [
            {
                "@id": "estleg:Src",
                "estleg:amendsLaw": {"@id": "estleg:Tgt"},
                "wrapper": {"estleg:inner": {"@id": "estleg:Nested"}},
            }
        ]
    }
    refs = validate_all.collect_internal_refs(Path("sample.json"), doc)
    preds = {(prop, ref) for _file, _src, prop, ref in refs}
    assert ("estleg:amendsLaw", "estleg:Tgt") in preds
    assert ("estleg:inner", "estleg:Nested") in preds
