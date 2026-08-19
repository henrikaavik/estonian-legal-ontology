"""#447: act roots must not owl:sameAs a dated Riigi Teataja XML file."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from estleg.estleg_common import (
    DOMAIN_INDIVIDUAL_TYPES,
    is_domain_individual,
    jsonld_id_values,
    strip_xml_sameas,
)
from estleg.generate_all_laws import generate_law_jsonld
from estleg.generate_regulations import build_regulation_jsonld
from estleg import validate_all

REPO = Path(__file__).resolve().parent.parent
AOS_OSA1 = REPO / "krr_outputs" / "asjaoigusseadus_osa1_peep.json"
PS_PEEP = REPO / "krr_outputs" / "eesti_vabariigi_pohiseadus_peep.json"
RT_XML = "https://www.riigiteataja.ee/akt/128012026007.xml"
CELLAR = "http://publications.europa.eu/resource/celex/32000L0031"
WIKIDATA = "http://www.wikidata.org/entity/Q1370367"

LAW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt>
  <metaandmed><aktinimi><nimi>Testseadus</nimi></aktinimi></metaandmed>
  <sisu>
    <paragrahv id="p1">
      <kuvatavNr>§ 1.</kuvatavNr>
      <paragrahvNr>1</paragrahvNr>
      <pealkiri>Reguleerimisala</pealkiri>
      <loige><loigeNr>1</loigeNr><sisutekst>Tekst.</sisutekst></loige>
    </paragrahv>
  </sisu>
</oigusakt>
"""

REG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt>
  <metaandmed>
    <aktinimi><nimi>Testmäärus</nimi></aktinimi>
    <valjaandja>Vabariigi Valitsus</valjaandja>
    <terviktekstId>160748</terviktekstId>
  </metaandmed>
  <sisu>
    <paragrahv>
      <paragrahvNr>1</paragrahvNr>
      <kuvatavNr>§ 1.</kuvatavNr>
      <pealkiri>Reguleerimisala</pealkiri>
      <loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>
    </paragrahv>
  </sisu>
</oigusakt>
"""


def test_strip_xml_sameas_keeps_non_xml_and_dcterms_source() -> None:
    node = {
        "@type": ["estleg:Act", "estleg:Law"],
        "dcterms:source": {"@id": RT_XML},
        "owl:sameAs": [
            {"@id": RT_XML},
            {"@id": WIKIDATA},
        ],
    }
    assert strip_xml_sameas(node) is True
    assert jsonld_id_values(node["dcterms:source"]) == [RT_XML]
    assert jsonld_id_values(node.get("owl:sameAs")) == [WIKIDATA]
    assert strip_xml_sameas(node) is False


def test_generate_law_root_has_source_not_xml_sameas() -> None:
    root = ET.fromstring(LAW_XML)
    doc = generate_law_jsonld(
        "Testseadus",
        "testseadus",
        root,
        abbreviation="TS",
        rt_url="/akt/128012026007.xml",
        kehtiv="2026-05-24",
    )
    act = doc["@graph"][0]
    assert act["dcterms:source"]["@id"].endswith(".xml")
    assert all(not iri.endswith(".xml") for iri in jsonld_id_values(act.get("owl:sameAs")))


def test_generate_regulation_root_has_source_not_xml_sameas() -> None:
    root = ET.fromstring(REG_XML)
    doc, _ = build_regulation_jsonld(
        "Testmäärus",
        {"url": "/akt/160748.xml", "tid": "160748"},
        root,
        is_kov=False,
    )
    act = doc["@graph"][0]
    assert "estleg:Act" in act["@type"]
    assert act["dcterms:source"]["@id"].endswith(".xml")
    assert all(not iri.endswith(".xml") for iri in jsonld_id_values(act.get("owl:sameAs")))


def test_validate_rejects_act_xml_sameas_and_keeps_cellar() -> None:
    validate_all.reset()
    bad = {
        "@graph": [
            {
                "@id": "estleg:X_Map",
                "@type": ["estleg:Act", "estleg:Law"],
                "owl:sameAs": {"@id": RT_XML},
            }
        ]
    }
    validate_all.validate_act_xml_sameas(Path("bad.json"), bad)
    assert any(".xml" in msg and "owl:sameAs" in msg for msg in validate_all.errors)

    validate_all.reset()
    eu = {
        "@graph": [
            {
                "@id": "estleg:EU_32000L0031",
                "@type": ["estleg:EULegislation"],
                "owl:sameAs": {"@id": CELLAR},
            }
        ]
    }
    validate_all.validate_act_xml_sameas(Path("eu.json"), eu)
    assert validate_all.errors == []


def test_aos_parts_do_not_share_xml_sameas() -> None:
    leftovers: list[str] = []
    for path in sorted((REPO / "krr_outputs").glob("asjaoigusseadus_osa*_peep.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict) or not is_domain_individual(node):
                continue
            for iri in jsonld_id_values(node.get("owl:sameAs")):
                if iri.endswith(".xml"):
                    leftovers.append(f"{path.name}:{node.get('@id')}:{iri}")
            source = jsonld_id_values(node.get("dcterms:source"))
            assert any(item.endswith(".xml") for item in source), path.name
    assert leftovers == []


def test_ps_keeps_wikidata_sameas_not_rt_xml() -> None:
    doc = json.loads(PS_PEEP.read_text(encoding="utf-8"))
    node = next(
        n
        for n in doc["@graph"]
        if n.get("@id") == "estleg:eesti_vabariigi_pohiseadus_Map"
    )
    sameas = jsonld_id_values(node.get("owl:sameAs"))
    assert WIKIDATA in sameas
    assert all(not iri.endswith(".xml") for iri in sameas)
    assert any(iri.endswith(".xml") for iri in jsonld_id_values(node.get("dcterms:source")))


def test_committed_domain_individuals_have_no_xml_sameas() -> None:
    hits = 0
    scanned = 0
    for path in (REPO / "krr_outputs").glob("*_peep.json"):
        scanned += 1
        text = path.read_text(encoding="utf-8")
        if "owl:sameAs" not in text or ".xml" not in text:
            continue
        doc = json.loads(text)
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            types = set(node.get("@type") or [])
            if not types & DOMAIN_INDIVIDUAL_TYPES:
                continue
            for iri in jsonld_id_values(node.get("owl:sameAs")):
                if iri.endswith(".xml"):
                    hits += 1
    assert scanned > 500
    assert hits == 0
