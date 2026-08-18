"""Issue #309 — multipart act @id must not encode a volatile §-range."""

from __future__ import annotations

import json

from migrate_osa_range_ids import collect_osa_range_remap, stable_osa_iri


def test_stable_osa_iri_strips_par_range():
    assert stable_osa_iri("estleg:KARIST_2_Osa1_1_87") == "estleg:KARIST_2_Osa1"
    assert stable_osa_iri("estleg:volaoigusseadus_Osa2_208_270") == (
        "estleg:volaoigusseadus_Osa2"
    )
    assert stable_osa_iri("estleg:TsÜS_Osa7_138_169") == "estleg:TsÜS_Osa7"
    assert stable_osa_iri("estleg:KARIST_2_Osa1") is None
    assert stable_osa_iri("estleg:KARIST_2_Par_1") is None


def test_committed_peeps_have_no_range_encoded_osa_ids():
    """Corpus gate: the volatile ``_OsaN_min_max`` act @id is gone."""
    from pathlib import Path
    import re

    krr = Path(__file__).resolve().parents[1] / "krr_outputs"
    leftover = []
    pat = re.compile(r"estleg:\w+_Osa\d+_\d+_\d+\b")
    for path in krr.glob("*_peep.json"):
        text = path.read_text(encoding="utf-8")
        hits = pat.findall(text)
        if hits:
            leftover.append((path.name, hits[:3]))
    assert leftover == [], leftover
    kars = json.loads(
        (krr / "karistusseadustik_osa1_peep.json").read_text(encoding="utf-8")
    )
    assert kars["@graph"][0]["@id"] == "estleg:KARIST_2_Osa1"


def test_collect_remap_includes_kars_osa1(tmp_path):
    peep = tmp_path / "kars_osa1_peep.json"
    peep.write_text(
        '{"@graph":[{"@id":"estleg:KARIST_2_Osa1_1_87"}]}',
        encoding="utf-8",
    )
    remap = collect_osa_range_remap(tmp_path)
    assert remap["estleg:KARIST_2_Osa1_1_87"] == "estleg:KARIST_2_Osa1"
