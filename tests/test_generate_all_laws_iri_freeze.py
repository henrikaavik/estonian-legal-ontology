"""Refresh keeps committed law IRIs and filenames (#692 refresh, PrefixAllocator fix)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import generate_all_laws as gal


def _peep(path: Path, iri: str, title: str, *, kehtiv: bool = True) -> None:
    root = {"@id": iri, "@type": ["estleg:Act", "estleg:Law"], "dc:source": title}
    if kehtiv:
        root["estleg:kehtiv"] = {"@value": "2026-05-24", "@type": "xsd:date"}
    path.write_text(json.dumps({"@graph": [root]}), encoding="utf-8")


def test_registry_abbrev_is_returned_and_compared_transliterated() -> None:
    registry = {"jaatmeseadus": {"abbrev": "JäätS", "title": "Jäätmeseadus"}}
    alloc = gal.PrefixAllocator(registry=registry)
    # The corpus is ASCII since #445: JaatS_Par_1, not JäätS_Par_1.
    assert alloc.allocate("JäätS", "jaatmeseadus", "Jäätmeseadus") == "JaatS"
    # A derived "JaatS" for another law is the same IRI prefix: it collides.
    assert alloc.allocate("JaatS", "jaatmekava_seadus", "Jäätmekava seadus") != "JaatS"


def test_frozen_prefix_wins_and_is_reserved() -> None:
    alloc = gal.PrefixAllocator(
        registry={},
        frozen={"Treaty B": "eesti_vabariigi_valitsuse_ja_x"},
    )
    # Treaty A, allocated first, would derive the frozen prefix of Treaty B.
    a = alloc.allocate("", "eesti_vabariigi_valitsuse_ja_x_lepingu_a", "Treaty A")
    b = alloc.allocate("", "eesti_vabariigi_valitsuse_ja_x_lepingu_b", "Treaty B")
    assert b == "eesti_vabariigi_valitsuse_ja_x"
    assert a != b


def test_frozen_prefix_beats_the_registry() -> None:
    alloc = gal.PrefixAllocator(
        registry={"x_seadus": {"abbrev": "NEWX", "title": "X seadus"}},
        frozen={"X seadus": "OLDX"},
    )
    assert alloc.allocate("NEWX", "x_seadus", "X seadus") == "OLDX"


def test_disk_slug_map_prefers_generator_files(tmp_path) -> None:
    _peep(tmp_path / "pikk_pealkiri__peep.json", "estleg:PP_Map", "Pikk pealkiri")
    _peep(tmp_path / "pikk_pealkiri_vana_peep.json", "estleg:PPV_Map", "Pikk pealkiri", kehtiv=False)
    _peep(tmp_path / "pikk_pealkiri_map_peep.json", "estleg:PPM_Map", "Pikk pealkiri")
    _peep(tmp_path / "seadus_osa1_peep.json", "estleg:S_Osa1", "Seadus")
    assert gal.load_disk_slug_map(tmp_path) == {
        "Pikk pealkiri": "pikk_pealkiri_",
        "Seadus": "seadus",
    }


def test_disk_prefix_map_reads_single_and_part_roots(tmp_path) -> None:
    _peep(tmp_path / "a_peep.json", "estleg:AAA_Map", "A")
    _peep(tmp_path / "b_osa1_peep.json", "estleg:KARIST_2_Osa1", "B")
    _peep(tmp_path / "c_peep.json", "estleg:CCC_Map", "Not C")
    frozen = gal.load_disk_prefix_map(tmp_path, {"A": "a", "B": "b", "C": "c", "D": "d"})
    assert frozen == {"A": "AAA", "B": "KARIST_2"}


def test_multipart_act_iri_uses_the_registered_map() -> None:
    assert gal.multipart_act_iri("tsiviilkohtumenetluse_seadustik") == "estleg:TsMS_Map"
    assert gal.multipart_act_iri("AOS") == "estleg:AOS_Map"
    assert gal.multipart_act_iri("UNKNOWN") == "estleg:UNKNOWN_Map"


def test_main_reuses_the_on_disk_slug_and_iri(tmp_path, monkeypatch) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    title = "Pikk pealkiri"
    _peep(krr / "pikk_pealkiri__peep.json", "estleg:OLD_PREFIX_Map", title)
    pad = "<!-- " + "x" * 400 + " -->"
    (data / "pikk_pealkiri___tid7.xml").write_text(
        f"<?xml version='1.0' encoding='utf-8'?>{pad}<oigusakt><sisu><paragrahv>"
        "<paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr><loige><loigeNr>1</loigeNr>"
        "<sisuTekst><tavatekst>T.</tavatekst></sisuTekst></loige></paragrahv></sisu></oigusakt>",
        encoding="utf-8",
    )
    monkeypatch.setattr(gal, "KRR_DIR", krr)
    monkeypatch.setattr(gal, "DATA_DIR", data)
    monkeypatch.setattr(gal, "time", type("T", (), {"sleep": staticmethod(lambda _s: None)}))
    monkeypatch.setattr(gal, "get_all_laws", lambda **kw: (
        {title: {"gid": "9", "tid": "7", "url": "/akt/9.xml", "lyhend": "PPS"}}, {"complete": True},
    ))
    monkeypatch.setattr(gal, "common_fetch_xml", lambda *a, **k: pytest.fail("network"))

    class _Args:
        refresh, force, missing_only = True, False, False
        kehtiv, allow_partial, limit, from_manifest = "2026-10-09", False, None, None
        regen_state = None
        reset_regen_state = False

    monkeypatch.setattr(gal, "parse_args", lambda: _Args())
    gal.main()
    assert sorted(p.name for p in krr.glob("*_peep.json")) == ["pikk_pealkiri__peep.json"]
    head = json.loads((krr / "pikk_pealkiri__peep.json").read_text(encoding="utf-8"))["@graph"][0]
    assert head["@id"] == "estleg:OLD_PREFIX_Map"
    manifest = json.loads((krr / gal.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["outputsAll"][0]["slug"] == "pikk_pealkiri_"
    hashes = json.loads((krr / "fetch_content_hashes.json").read_text(encoding="utf-8"))
    assert hashes["pikk_pealkiri_"]["cacheFile"].endswith("pikk_pealkiri___tid7.xml")
    assert head["estleg:contentHash"] == hashes["pikk_pealkiri_"]["sha256"]
