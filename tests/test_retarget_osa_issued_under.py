"""Tests for retarget_osa_issued_under.py (#585).

Covers the malformed-IRI pattern, existence-gated resolution (a known
base resolves only when its act root exists), the unresolved path,
in-place doc rewriting, and idempotency.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg.retarget_osa_issued_under import (
    ISSUED_UNDER_KEY,
    OSA_BASE_TO_SHORTCODE,
    OSA_TARGET_RE,
    discover_act_roots,
    resolve_target,
    retarget_doc,
)

# ── pattern + resolve_target ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "ref,base",
    [
        ("estleg:tsiviilkohtumenetluse_seadustik_Osa10_362_474", "tsiviilkohtumenetluse_seadustik"),
        ("estleg:volaoigusseadus_Osa1_1_207", "volaoigusseadus"),
        ("estleg:AOS_Osa1_1_31", "AOS"),
        ("estleg:KARIST_2_Osa1_1_87", "KARIST_2"),
    ],
)
def test_pattern_matches_malformed(ref, base):
    m = OSA_TARGET_RE.match(ref)
    assert m is not None
    assert m.group("base") == base


@pytest.mark.parametrize(
    "ref",
    [
        "estleg:TsMS_Map_2026",  # well-formed act root
        "estleg:foo_Par_5",  # provision
        "estleg:foo_Osa1",  # missing the _start_end range
        "estleg:foo_Osa1_5",  # only one numeric segment
        "not-an-iri",
    ],
)
def test_pattern_ignores_wellformed(ref):
    assert OSA_TARGET_RE.match(ref) is None


def test_resolve_target_gated_on_existence():
    roots = {"estleg:TsMS_Map_2026"}  # only TsMS act root present
    # known base WITH act root -> resolves
    assert (
        resolve_target("estleg:tsiviilkohtumenetluse_seadustik_Osa10_362_474", roots)
        == "estleg:TsMS_Map_2026"
    )
    # known base WITHOUT act root -> None (left in place)
    assert resolve_target("estleg:volaoigusseadus_Osa1_1_207", roots) is None
    assert resolve_target("estleg:AOS_Osa1_1_31", roots) is None
    # unknown base -> None
    assert resolve_target("estleg:somethingelse_Osa1_1_9", roots) is None
    # well-formed -> None
    assert resolve_target("estleg:TsMS_Map_2026", roots) is None


def test_shortcode_table_has_expected_entries():
    # Guard the known mapping so a typo can't silently break resolution.
    assert OSA_BASE_TO_SHORTCODE["tsiviilkohtumenetluse_seadustik"] == "TsMS"
    assert OSA_BASE_TO_SHORTCODE["volaoigusseadus"] == "VÕS"
    assert OSA_BASE_TO_SHORTCODE["AOS"] == "AÕS"
    assert OSA_BASE_TO_SHORTCODE["KARIST_2"] == "KarS"


# ── discover_act_roots ─────────────────────────────────────────────────────


def test_discover_act_roots(tmp_path: Path):
    p = tmp_path / "a_peep.json"
    p.write_text(
        json.dumps(
            {
                "@graph": [
                    {"@id": "estleg:TsMS_Map_2026", "@type": ["estleg:Act", "estleg:Law"]},
                    # a Part node -> excluded even though it ends _Map_2026
                    {
                        "@id": "estleg:Foo_Osa1_Map_2026",
                        "@type": ["estleg:Part", "estleg:Act"],
                    },
                    # non-act type -> excluded
                    {"@id": "estleg:Bar_Map_2026", "@type": ["skos:Concept"]},
                    # provision -> excluded (not _Map_2026)
                    {"@id": "estleg:TsMS_Par_5", "@type": ["estleg:LegalProvision"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    roots = discover_act_roots([p])
    assert roots == {"estleg:TsMS_Map_2026"}


# ── retarget_doc + idempotency ─────────────────────────────────────────────


def _doc_with_issued_under(refs: list[str]) -> dict:
    return {
        "@graph": [
            {
                "@id": "estleg:Reg_1_Map_2026",
                ISSUED_UNDER_KEY: [{"@id": r} for r in refs],
            }
        ]
    }


def test_retarget_doc_rewrites_only_resolvable():
    roots = {"estleg:TsMS_Map_2026"}
    doc = _doc_with_issued_under(
        [
            "estleg:NotS_Map_2026",  # well-formed, untouched
            "estleg:tsiviilkohtumenetluse_seadustik_Osa10_362_474",  # -> TsMS
            "estleg:volaoigusseadus_Osa1_1_207",  # unresolved, left
        ]
    )
    unresolved: dict[str, int] = {}
    changed = retarget_doc(doc, roots, unresolved)
    assert changed == 1
    ids = [x["@id"] for x in doc["@graph"][0][ISSUED_UNDER_KEY]]
    assert ids == [
        "estleg:NotS_Map_2026",
        "estleg:TsMS_Map_2026",
        "estleg:volaoigusseadus_Osa1_1_207",
    ]
    assert unresolved == {"estleg:volaoigusseadus_Osa1_1_207": 1}


def test_retarget_doc_idempotent():
    roots = {"estleg:TsMS_Map_2026"}
    doc = _doc_with_issued_under(
        ["estleg:tsiviilkohtumenetluse_seadustik_Osa10_362_474"]
    )
    assert retarget_doc(doc, roots, {}) == 1
    # second pass: target is now the canonical Map IRI -> no change
    assert retarget_doc(doc, roots, {}) == 0


def test_retarget_doc_scalar_value_shape():
    """issuedUnder may be a single object (not a list)."""
    roots = {"estleg:TsMS_Map_2026"}
    doc = {
        "@graph": [
            {
                "@id": "estleg:Reg_2_Map_2026",
                ISSUED_UNDER_KEY: {
                    "@id": "estleg:tsiviilkohtumenetluse_seadustik_Osa10_362_474"
                },
            }
        ]
    }
    assert retarget_doc(doc, roots, {}) == 1
    assert doc["@graph"][0][ISSUED_UNDER_KEY]["@id"] == "estleg:TsMS_Map_2026"
