"""#695 — law roots carry the RT metadata block; redactions rank by validity start."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from estleg import generate_all_laws as gal
from estleg.backfill_rt_eli import rt_date_fields, stamp_map_root_doc
from estleg.riigiteataja_common import parse_act_metadata, redaction_rank

LAW_XML = """<oigusakt xmlns="tyviseadus_1_10.02.2010">
  <metaandmed>
    <valjaandja>Riigikogu</valjaandja>
    <dokumentLiik>seadus</dokumentLiik>
    <vastuvoetud><aktikuupaev>2001-06-06</aktikuupaev><aktiNr>12</aktiNr>
      <joustumine>2002-09-01</joustumine></vastuvoetud>
    <kehtivus><kehtivuseAlgus>2026-10-01</kehtivuseAlgus>
      <kehtivuseLopp>2026-12-31+02:00</kehtivuseLopp></kehtivus>
    <skeemiNimi>tyviseadus_1_10.02.2010.xsd</skeemiNimi>
    <globaalID>130062026005</globaalID>
    <terviktekstiGrupiID>159211</terviktekstiGrupiID>
  </metaandmed>
  <muutmismarge><aktikuupaev>2026-06-02</aktikuupaev></muutmismarge>
  <sisu>
    <osa><osaNr>1</osaNr><osaPealkiri>Yld</osaPealkiri>
      <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
        <loige><loigeNr>1</loigeNr><sisuTekst><tavatekst>Esimene.</tavatekst></sisuTekst></loige>
      </paragrahv></osa>
    <osa><osaNr>2</osaNr><osaPealkiri>Eri</osaPealkiri>
      <paragrahv><paragrahvNr>2</paragrahvNr><kuvatavNr>§ 2.</kuvatavNr>
        <loige><loigeNr>1</loigeNr><sisuTekst><tavatekst>Teine.</tavatekst></sisuTekst></loige>
      </paragrahv></osa>
  </sisu>
</oigusakt>"""


def _root() -> ET.Element:
    return ET.fromstring(LAW_XML)


def _alloc() -> gal.PrefixAllocator:
    return gal.PrefixAllocator(registry={})


# --- ranking ---------------------------------------------------------------


def test_validity_start_beats_globaalid_order() -> None:
    # The ticket's pair: 23.05.2021 vs 07.05.2025. Both string and integer
    # order pick the 2021 id; the validity start picks 2025.
    old = {"globaalID": 231052021002, "kehtivus": {"algus": "2021-05-23"}}
    new = {"globaalID": 107052025017, "kehtivus": {"algus": "2025-05-07"}}
    assert str(old["globaalID"]) > str(new["globaalID"])
    assert redaction_rank(new) > redaction_rank(old)


def test_same_start_falls_back_to_integer_gid_and_undated_ranks_last() -> None:
    a = {"globaalID": "99052024005", "kehtivus": {"algus": "2024-05-01"}}
    b = {"globaalID": "128062023003", "kehtivus": {"algus": "2024-05-01"}}
    assert redaction_rank(b) > redaction_rank(a)
    undated = {"globaalID": "999999999999", "kehtivus": {}}
    assert redaction_rank(a) > redaction_rank(undated)
    # The generator's stored row shape ranks the same way.
    assert redaction_rank({"gid": "1", "kehtivusAlgus": "2026-01-01"}) > redaction_rank(a)


def test_get_all_laws_keeps_the_redaction_that_starts_last(monkeypatch) -> None:
    rows = [
        {"pealkiri": "Perekonnaseadus", "globaalID": 231052021002, "terviktekstID": 1,
         "url": "/akt/231052021002.xml", "kehtivus": {"algus": "2021-05-23"}},
        {"pealkiri": "Perekonnaseadus", "globaalID": 107052025017, "terviktekstID": 1,
         "url": "/akt/107052025017.xml", "kehtivus": {"algus": "2025-05-07"}},
    ]

    def fake_fetch_acts(*_a, **kw):
        kw["stats"]["pagesFetchedOk"] = 1
        yield from rows

    monkeypatch.setattr(gal, "fetch_acts", fake_fetch_acts)
    laws, _meta = gal.get_all_laws(kehtiv="2026-10-09")
    assert laws["Perekonnaseadus"]["gid"] == "107052025017"
    assert laws["Perekonnaseadus"]["kehtivusAlgus"] == "2025-05-07"


# --- parse_act_metadata ----------------------------------------------------


def test_parse_act_metadata_reads_schema_and_original_entry_into_force() -> None:
    meta = parse_act_metadata(_root())
    assert meta["schemaName"] == "tyviseadus_1_10.02.2010.xsd"
    assert meta["originalEntryIntoForce"] == "2002-09-01"
    assert meta["entryIntoForce"] == "2026-10-01"  # the redaction start
    assert meta["repealDate"] == "2026-12-31"  # the redaction end


def test_date_block_uses_joustumine_and_never_redaction_end() -> None:
    fields = rt_date_fields(parse_act_metadata(_root()))
    assert fields["estleg:entryIntoForce"] == {"@value": "2002-09-01", "@type": "xsd:date"}
    assert fields["estleg:lastAmendmentDate"] == {"@value": "2026-06-02", "@type": "xsd:date"}
    assert "estleg:repealDate" not in fields


# --- the block on every root kind -------------------------------------------


def _assert_block(node: dict, *, act_root: bool) -> None:
    assert node["estleg:globalId"] == "130062026005"
    assert node["estleg:terviktekstId"] == "159211"
    assert node["estleg:issuer"] == "Riigikogu"
    assert node["estleg:actNumber"] == "12"
    assert node["estleg:skeemiNimi"] == "tyviseadus_1_10.02.2010.xsd"
    assert node["estleg:entryIntoForce"]["@value"] == "2002-09-01"
    assert node["estleg:lastAmendmentDate"]["@value"] == "2026-06-02"
    assert node["estleg:kehtiv"] == {"@value": "2026-10-09", "@type": "xsd:date"}
    assert ("eli:id_local" in node) is act_root


def test_single_file_law_root_carries_the_block() -> None:
    doc = gal.generate_law_jsonld(
        "Testseadus", "testseadus", _root(), "TS", "/akt/130062026005.xml",
        kehtiv="2026-10-09", terviktekst_id="159211", allocator=_alloc(),
    )
    _assert_block(doc["@graph"][0], act_root=True)


def test_stub_root_carries_the_block() -> None:
    root = ET.fromstring(LAW_XML.replace("<sisu>", "<sisuX>").replace("</sisu>", "</sisuX>"))
    doc = gal.generate_law_stub_jsonld(
        "Testseadus", "testseadus", root, "TS", "/akt/130062026005.xml",
        kehtiv="2026-10-09", allocator=_alloc(),
    )
    _assert_block(doc["@graph"][0], act_root=True)


def test_multipart_part_roots_carry_the_block_without_eli_id() -> None:
    results = gal.generate_multipart_law(
        "Testseadus", "testseadus", _root(), "TS", "/akt/130062026005.xml",
        kehtiv="2026-10-09", allocator=_alloc(),
    )
    assert len(results) == 2
    for _name, doc in results:
        _assert_block(doc["@graph"][0], act_root=False)


def test_multipart_map_root_is_stamped_and_keeps_enrichment_dates() -> None:
    results = gal.generate_multipart_law(
        "Testseadus", "testseadus", _root(), "TS", "/akt/130062026005.xml",
        kehtiv="2026-10-09", allocator=_alloc(),
    )
    act_iri = results[0][1]["@graph"][0]["estleg:partOfAct"]["@id"]
    map_doc = {"@graph": [{
        "@id": act_iri, "@type": ["estleg:Act", "estleg:Law"],
        "dc:source": "Testseadus", "dcterms:title": "Testseadus",
        "estleg:contentStatus": "structuredBody",
        # Written by generate_amendment_history (#429); must survive.
        "estleg:lastAmendmentDate": {"@value": "2026-07-01", "@type": "xsd:date"},
    }]}
    changed = stamp_map_root_doc(
        map_doc, [d for _n, d in results], _root(),
        kehtiv_value={"@value": "2026-10-09", "@type": "xsd:date"},
    )
    root = map_doc["@graph"][0]
    assert changed is True
    assert root["estleg:globalId"] == "130062026005"
    assert root["eli:id_local"] == "130062026005"
    assert root["estleg:lastAmendmentDate"]["@value"] == "2026-07-01"
    # Idempotent.
    assert stamp_map_root_doc(
        map_doc, [d for _n, d in results], _root(),
        kehtiv_value={"@value": "2026-10-09", "@type": "xsd:date"},
    ) is False


def test_map_root_for_another_act_is_left_alone() -> None:
    results = gal.generate_multipart_law(
        "Testseadus", "testseadus", _root(), "TS", "/akt/1.xml", allocator=_alloc(),
    )
    map_doc = {"@graph": [{"@id": "estleg:Other_Map", "@type": ["estleg:Act", "estleg:Law"]}]}
    assert stamp_map_root_doc(map_doc, [d for _n, d in results], _root()) is False


def test_regen_keeps_enrichment_dates_but_not_stale_identity(tmp_path) -> None:
    doc = gal.generate_law_jsonld(
        "Testseadus", "testseadus", _root(), "TS", "/akt/130062026005.xml",
        kehtiv="2026-10-09", allocator=_alloc(),
    )
    act_id = doc["@graph"][0]["@id"]
    existing = {"@graph": [{
        "@id": act_id,
        "estleg:entryIntoForce": {"@value": "1999-01-01", "@type": "xsd:date"},
        "estleg:lastAmendmentDate": {"@value": "2026-07-01", "@type": "xsd:date"},
        "estleg:repealDate": {"@value": "2030-01-01", "@type": "xsd:date"},
        # Generator-owned: an act number the current XML no longer has.
        "estleg:actNumber": "999",
        "dcterms:subject": [{"@id": "http://eurovoc.europa.eu/1"}],
    }]}
    path = tmp_path / "testseadus_peep.json"
    path.write_text(json.dumps(existing), encoding="utf-8")
    fresh = gal.generate_law_jsonld(
        "Testseadus", "testseadus", ET.fromstring(LAW_XML.replace("<aktiNr>12</aktiNr>", "")),
        "TS", "/akt/130062026005.xml", kehtiv="2026-10-09", allocator=_alloc(),
    )
    merged = gal.merge_existing_enrichments(fresh, path)["@graph"][0]
    assert merged["estleg:entryIntoForce"]["@value"] == "1999-01-01"
    assert merged["estleg:lastAmendmentDate"]["@value"] == "2026-07-01"
    assert merged["estleg:repealDate"]["@value"] == "2030-01-01"
    assert "estleg:actNumber" not in merged
    assert merged["dcterms:subject"] == [{"@id": "http://eurovoc.europa.eu/1"}]
