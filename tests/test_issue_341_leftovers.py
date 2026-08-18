"""Issue #341 leftovers — tid stamp, target-IRI collisions, T-Box properties."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from estleg import generate_all_laws
from estleg.migrate_uris import target_iri_collisions


def _one_par_xml() -> str:
    return (
        "<akt><sisu><peatykk><peatykkNr>1</peatykkNr>"
        "<peatykkPealkiri>P</peatykkPealkiri>"
        "<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>"
        "<paragrahvPealkiri>Yks</paragrahvPealkiri>"
        "<loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>"
        "</paragrahv></peatykk></sisu></akt>"
    )


def test_structured_law_stamps_terviktekst_id_when_kehtiv_set():
    generate_all_laws._used_prefixes.clear()
    doc = generate_all_laws.generate_law_jsonld(
        "Test seadus",
        "test_seadus",
        ET.fromstring(_one_par_xml()),
        abbreviation="TKID",
        kehtiv="2026-05-01",
        terviktekst_id="122122025002",
        allocator=generate_all_laws.PrefixAllocator(registry={}),
    )
    ont = doc["@graph"][0]
    assert ont["estleg:terviktekstId"] == "122122025002"


def test_stub_law_stamps_terviktekst_id_when_kehtiv_set():
    generate_all_laws._used_prefixes.clear()
    doc = generate_all_laws.generate_law_stub_jsonld(
        "Treaty",
        "treaty_slug",
        ET.fromstring("<akt><sisu /></akt>"),
        abbreviation="TRID",
        rt_url="/akt/1.xml",
        kehtiv="2026-05-01",
        terviktekst_id="999",
        allocator=generate_all_laws.PrefixAllocator(registry={}),
    )
    assert doc["@graph"][0]["estleg:terviktekstId"] == "999"


def test_multipart_osa_nodes_stamp_terviktekst_id_when_kehtiv_set():
    generate_all_laws._used_prefixes.clear()
    xml = """<akt><sisu>
        <osa><osaNr>1</osaNr><osaPealkiri>One</osaPealkiri>
          <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><tavatekst>Aaa.</tavatekst></loige></paragrahv>
        </osa>
        <osa><osaNr>2</osaNr><osaPealkiri>Two</osaPealkiri>
          <paragrahv><paragrahvNr>2</paragrahvNr><kuvatavNr>§ 2.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><tavatekst>Bbb.</tavatekst></loige></paragrahv>
        </osa>
    </sisu></akt>"""
    results = generate_all_laws.generate_multipart_law(
        "Multi",
        "multi",
        ET.fromstring(xml),
        abbreviation="MUID",
        kehtiv="2026-05-01",
        terviktekst_id="555",
        allocator=generate_all_laws.PrefixAllocator(registry={}),
    )
    assert results
    for _name, doc in results:
        assert doc["@graph"][0]["estleg:terviktekstId"] == "555"


def test_no_terviktekst_id_without_kehtiv():
    generate_all_laws._used_prefixes.clear()
    doc = generate_all_laws.generate_law_stub_jsonld(
        "X",
        "x_slug",
        ET.fromstring("<akt><sisu /></akt>"),
        abbreviation="XXID",
        rt_url="/akt/1.xml",
        terviktekst_id="100",
        allocator=generate_all_laws.PrefixAllocator(registry={}),
    )
    assert "estleg:terviktekstId" not in doc["@graph"][0]


def test_target_iri_collisions_ignores_old_keys():
    rename_map = {"estleg:OLD_Par_1": "estleg:NEW_Par_1"}
    all_iris = {"estleg:OLD_Par_1", "estleg:OTHER_Par_1"}
    assert target_iri_collisions(rename_map, all_iris) == set()


def test_target_iri_collisions_flags_existing_unrenamed_target():
    rename_map = {"estleg:OLD_Par_1": "estleg:EXISTING_Par_9"}
    all_iris = {"estleg:OLD_Par_1", "estleg:EXISTING_Par_9"}
    assert target_iri_collisions(rename_map, all_iris) == {"estleg:EXISTING_Par_9"}


def test_controlled_vocabulary_defines_shaped_predicates():
    cv = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "krr_outputs"
            / "controlled_vocabulary.jsonld"
        ).read_text(encoding="utf-8")
    )
    by_id = {
        n["@id"]: n
        for n in cv["@graph"]
        if isinstance(n, dict) and isinstance(n.get("@id"), str)
    }
    assert "owl:ObjectProperty" in by_id["estleg:definesTerm"]["@type"]
    assert "owl:ObjectProperty" in by_id["estleg:topicCluster"]["@type"]
    change = by_id["estleg:changeType"]
    assert "owl:DatatypeProperty" in change["@type"]
    range_id = change.get("rdfs:range", {})
    if isinstance(range_id, dict):
        range_id = range_id.get("@id")
    assert range_id == "xsd:string"
