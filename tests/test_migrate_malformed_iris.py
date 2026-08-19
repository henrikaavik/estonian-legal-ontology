"""#346 / #354 malformed-IRI remaps."""
from __future__ import annotations

import json
from pathlib import Path

from estleg.migrate_malformed_iris import (
    apply_iri,
    build_trailing_div_remap,
    collapse_underscores,
    range_target,
    rewrite_value,
)


def test_collapse_double_underscore() -> None:
    assert (
        collapse_underscores("estleg:1992_aasta_rahvusvahelise_konventsiooni__Map")
        == "estleg:1992_aasta_rahvusvahelise_konventsiooni_Map"
    )
    assert collapse_underscores("estleg:KarS_Par_1") == "estleg:KarS_Par_1"


def test_range_target_from_label() -> None:
    assert (
        range_target("§ 1–94.", "estleg:TsK_Par_194")
        == "estleg:TsK_Par_1_to_94"
    )
    assert (
        range_target("§ 95–161.", "estleg:TsK_Par_95161")
        == "estleg:TsK_Par_95_to_161"
    )
    assert range_target("§ 194.", "estleg:TsK_Par_194") is None


def test_apply_iri_prefix_extends_versions() -> None:
    remap = {"estleg:TsK_Par_194": "estleg:TsK_Par_1_to_94"}
    assert apply_iri("estleg:TsK_Par_194_v12979807", remap) == (
        "estleg:TsK_Par_1_to_94_v12979807"
    )


def test_trailing_div_rstrip_when_free() -> None:
    declared = {"estleg:Division_AUTORI_IV_3_TEOSE_KASUTAMINE_"}
    remap = build_trailing_div_remap(declared)
    assert remap["estleg:Division_AUTORI_IV_3_TEOSE_KASUTAMINE_"] == (
        "estleg:Division_AUTORI_IV_3_TEOSE_KASUTAMINE"
    )


def test_trailing_div_numbered_fallback_on_collision() -> None:
    declared = {
        "estleg:Division_AUTORI_IV_2_TEOSE_KASUTAMINE_",
        "estleg:Division_AUTORI_IV_2_TEOSE_KASUTAMINE",
    }
    remap = build_trailing_div_remap(declared)
    assert remap["estleg:Division_AUTORI_IV_2_TEOSE_KASUTAMINE_"] == (
        "estleg:Division_AUTORI_IV_2"
    )
    assert "estleg:Division_AUTORI_IV_2_TEOSE_KASUTAMINE" not in remap


def test_rewrite_walks_nested_refs() -> None:
    remap = {"estleg:FOO__Map": "estleg:FOO_Map"}
    doc = {
        "@id": "estleg:FOO__Map",
        "estleg:partOfAct": {"@id": "estleg:FOO__Map"},
    }
    out = rewrite_value(doc, remap)
    assert out["@id"] == "estleg:FOO_Map"
    assert out["estleg:partOfAct"]["@id"] == "estleg:FOO_Map"


def test_slugify_still_rstrips_after_cut() -> None:
    from estleg.estleg_common import slugify

    # 80-char cut landing on '_' must not leave a trailing underscore (#346).
    text = "a" * 70 + "_" + "bbbbbbbbbb"
    assert not slugify(text, max_len=80).endswith("_")


def test_corpus_peeps_have_no_double_underscore_or_whitespace_ids() -> None:
    repo = Path(__file__).resolve().parent.parent
    bad_dd: list[str] = []
    bad_ws: list[str] = []
    for path in (repo / "krr_outputs").glob("*_peep.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            nid = node.get("@id")
            if not isinstance(nid, str):
                continue
            if "__" in nid:
                bad_dd.append(nid)
            if any(ch.isspace() for ch in nid):
                bad_ws.append(nid)
            req = node.get("estleg:requestedCluster")
            if isinstance(req, dict):
                rid = req.get("@id")
                if isinstance(rid, str) and any(ch.isspace() for ch in rid):
                    bad_ws.append(rid)
    assert bad_ws == [], bad_ws[:10]
    assert bad_dd == [], bad_dd[:10]


def test_tsk_range_provision_uses_to_not_concatenated_digits() -> None:
    repo = Path(__file__).resolve().parent.parent
    peep = repo / "krr_outputs" / "eesti_nsv_tsiviilkoodeks_peep.json"
    doc = json.loads(peep.read_text(encoding="utf-8"))
    ids = {n.get("@id") for n in doc.get("@graph", []) if isinstance(n, dict)}
    assert "estleg:TsK_Par_1_to_94" in ids
    assert "estleg:TsK_Par_194" not in ids


def test_autori_division_trailing_underscore_gone() -> None:
    repo = Path(__file__).resolve().parent.parent
    peep = repo / "krr_outputs" / "autorioiguse_seadus_peep.json"
    text = peep.read_text(encoding="utf-8")
    assert "Division_AUTORI_IV_2_TEOSE_KASUTAMINE_" not in text
    assert "Division_AUTORI_IV_2" in text
