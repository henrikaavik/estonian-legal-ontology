"""#445 — yearless ASCII act-root IRIs and remint helpers."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.estleg_common import ascii_iri_key, is_map_iri, mint_act_iri
from estleg.remint_act_iri_v2 import remint_iri, remint_local, rewrite_text

REPO = Path(__file__).resolve().parent.parent
PKS = REPO / "krr_outputs" / "perekonnaseadus_peep.json"
AUS = REPO / "krr_outputs" / "ametiuhingute_seadus_peep.json"
AOS_OSA = REPO / "krr_outputs" / "asjaoigusseadus_osa3_peep.json"
AOS_MAP = REPO / "krr_outputs" / "asjaoigusseadus_map_peep.json"
TSMS_MAP = REPO / "krr_outputs" / "tsiviilkohtumenetluse_seadustik_map_peep.json"
BRIDGE = REPO / "data" / "act_iri_v2_sameas.jsonld"


def test_mint_is_yearless_ascii_and_stable_across_years() -> None:
    assert mint_act_iri("PKS") == "estleg:PKS_Map"
    assert mint_act_iri("PKS", year=2027) == "estleg:PKS_Map"
    assert mint_act_iri("AÜS") == "estleg:AUS_Map"
    assert mint_act_iri("estleg:AÜS_Map_2026") == "estleg:AUS_Map"
    assert is_map_iri("estleg:PKS_Map")
    assert is_map_iri("estleg:PKS_Map_2026")
    assert not is_map_iri("estleg:PKS_Par_1")


def test_ascii_iri_key_transliterates_estonian_letters() -> None:
    assert ascii_iri_key("AÜS") == "AUS"
    assert ascii_iri_key("TsÜS") == "TsUS"
    assert ascii_iri_key("ÕÕS") == "OOS"
    assert ascii_iri_key("JäätS") == "JaatS"


def test_rewrite_text_drops_year_and_transliterates() -> None:
    src = '{"@id": "estleg:AÜS_Map_2026", "x": "estleg:AÜS_Par_1"}'
    out = rewrite_text(src)
    assert "estleg:AUS_Map" in out
    assert "estleg:AUS_Par_1" in out
    assert "_Map_2026" not in out
    assert "Ü" not in out
    assert remint_local("PKS_Map_2026") == "PKS_Map"
    assert remint_iri("https://w3id.org/estleg/PKS_Map_2026") == (
        "https://w3id.org/estleg/PKS_Map"
    )


def test_committed_act_roots_are_yearless_ascii() -> None:
    pks = json.loads(PKS.read_text(encoding="utf-8"))
    assert pks["@graph"][0]["@id"] == "estleg:PKS_Map"
    assert "_Map_2026" not in PKS.read_text(encoding="utf-8")
    aus = json.loads(AUS.read_text(encoding="utf-8"))
    assert aus["@graph"][0]["@id"] == "estleg:AUS_Map"
    osa = json.loads(AOS_OSA.read_text(encoding="utf-8"))
    root = osa["@graph"][0]
    assert root["@id"] == "estleg:AOS_Osa3"
    assert "estleg:Act" not in (root.get("@type") or [])
    assert "estleg:Part" in (root.get("@type") or [])
    assert root["estleg:isPartOf"]["@id"] == "estleg:AOS_Map"
    amap = json.loads(AOS_MAP.read_text(encoding="utf-8"))
    assert amap["@graph"][0]["@id"] == "estleg:AOS_Map"
    assert TSMS_MAP.is_file()
    tsms = json.loads(TSMS_MAP.read_text(encoding="utf-8"))
    assert tsms["@graph"][0]["@id"] == "estleg:TsMS_Map"
    assert BRIDGE.is_file()
    bridge = json.loads(BRIDGE.read_text(encoding="utf-8"))
    assert any(n.get("@id") == "estleg:PKS_Map" for n in bridge["@graph"])
