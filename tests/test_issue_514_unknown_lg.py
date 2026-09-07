"""#514: lossless lõige IRIs, unresolved Citation nodes, itemNumber."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from estleg.extract_cross_references import (
    _run_inlaw_citation_pass,
    extract_citations_from_text,
)
from estleg.generate_all_laws import generate_law_jsonld
from estleg.law_structure import (
    rewrite_unknown_lg_iris,
    stamp_item_numbers_from_text,
)


def test_unnumbered_loige_uses_sibling_index() -> None:
    xml = """
    <oigusakt>
      <metaandmed><aktinimi><nimi>Testseadus</nimi></aktinimi></metaandmed>
      <sisu>
        <paragrahv>
          <paragrahvNr>98</paragrahvNr>
          <kuvatavNr>§ 98.</kuvatavNr>
          <loige><tavatekst>Ainuke lõige ilma numbrita.</tavatekst></loige>
        </paragrahv>
      </sisu>
    </oigusakt>
    """
    doc = generate_law_jsonld(
        "Testseadus", "testseadus_514", ET.fromstring(xml), abbreviation="T514"
    )
    subs = [
        n
        for n in doc["@graph"]
        if isinstance(n, dict) and "estleg:Subsection" in (n.get("@type") or [])
    ]
    assert [n["@id"] for n in subs] == ["estleg:T514_Par_98_Lg_1"]
    assert subs[0]["estleg:subsectionNumber"] == "1"
    assert "_Lg_Unknown" not in subs[0]["@id"]


def test_rewrite_unknown_lg_iris_is_collision_safe() -> None:
    graph = [
        {"@id": "estleg:X_Par_1_Lg_Unknown_1", "@type": ["estleg:Subsection"]},
        {"@id": "estleg:X_Par_2_Lg_1", "@type": ["estleg:Subsection"]},
        {"@id": "estleg:X_Par_2_Lg_Unknown_1", "@type": ["estleg:Subsection"]},
        {"estleg:hasSubsection": {"@id": "estleg:X_Par_1_Lg_Unknown_1"}},
    ]
    changed = rewrite_unknown_lg_iris(graph)
    assert changed == 2
    ids = [n.get("@id") for n in graph if isinstance(n, dict)]
    assert "estleg:X_Par_1_Lg_1" in ids
    assert "estleg:X_Par_2_Lg_1_x2" in ids
    assert "estleg:X_Par_2_Lg_Unknown_1" not in ids
    assert graph[3]["estleg:hasSubsection"]["@id"] == "estleg:X_Par_1_Lg_1"


def test_unresolved_law_citation_is_reified() -> None:
    graph = [
        {
            "@id": "estleg:T514_Par_1",
            "@type": ["estleg:LegalProvision"],
            "estleg:paragrahv": "§ 1.",
            "estleg:legalText": "Kohaldatakse KarS § 9999.",
        }
    ]
    stats = _run_inlaw_citation_pass(
        graph,
        self_prefix="T514",
        abbrev_to_prefix={"KarS": ["KARIST_2"]},
        prefix_to_provisions={"KARIST_2": {"98": "estleg:KARIST_2_Osa2_Par_98"}},
        xml_par_texts={},
    )
    cites = [
        n
        for n in graph
        if isinstance(n, dict) and "estleg:Citation" in (n.get("@type") or [])
    ]
    assert stats["citations_unresolved"] >= 1
    assert cites
    assert "estleg:citationTarget" not in cites[0]
    assert cites[0]["estleg:citationSource"]["@id"] == "estleg:T514_Par_1"
    assert "KarS" in (cites[0].get("estleg:citationText") or "")


def test_extract_citations_keeps_surface_text() -> None:
    cites = extract_citations_from_text("KarS § 121 lg 2")
    assert cites
    assert "KarS" in cites[0]["citationText"]


def test_item_number_from_punkt_xml_and_text() -> None:
    xml = """
    <oigusakt>
      <metaandmed><aktinimi><nimi>Testseadus</nimi></aktinimi></metaandmed>
      <sisu>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <kuvatavNr>§ 5.</kuvatavNr>
          <loige>
            <loigeNr>1</loigeNr>
            <tavatekst>Keelatud on:</tavatekst>
            <punkt><punktNr>1</punktNr><tavatekst>esimene.</tavatekst></punkt>
            <punkt><punktNr>3</punktNr><tavatekst>kolmas.</tavatekst></punkt>
          </loige>
        </paragrahv>
      </sisu>
    </oigusakt>
    """
    doc = generate_law_jsonld(
        "Testseadus", "testseadus_514b", ET.fromstring(xml), abbreviation="T514B"
    )
    sub = next(
        n
        for n in doc["@graph"]
        if isinstance(n, dict) and n.get("@id") == "estleg:T514B_Par_5_Lg_1"
    )
    items = sub["estleg:itemNumber"]
    if isinstance(items, str):
        items = [items]
    assert "1" in items and "3" in items

    node = {
        "@type": ["estleg:Subsection"],
        "estleg:legalText": "Kehtib p 2 ja p 4 alusel.",
    }
    assert stamp_item_numbers_from_text(node) is True
    stamped = node["estleg:itemNumber"]
    if isinstance(stamped, str):
        stamped = [stamped]
    assert stamped == ["2", "4"]


REPO = Path(__file__).resolve().parent.parent


def test_kars_osa2_has_no_unknown_lg_iris() -> None:
    text = (REPO / "krr_outputs" / "karistusseadustik_osa2_peep.json").read_text(
        encoding="utf-8"
    )
    assert "_Lg_Unknown_" not in text
    assert "estleg:KARIST_2_Osa2_Par_98_Lg_1" in text


def test_committed_law_peep_has_unresolved_citation() -> None:
    found = False
    for path in (REPO / "krr_outputs").glob("*_peep.json"):
        text = path.read_text(encoding="utf-8")
        if "estleg:Citation" not in text or "citationSource" not in text:
            continue
        doc = json.loads(text)
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            types = node.get("@type") or []
            if "estleg:Citation" in types and "estleg:citationTarget" not in node:
                assert node.get("estleg:citationText")
                assert node.get("estleg:citationSource")
                found = True
                break
        if found:
            break
    assert found


def test_committed_subsection_has_item_number() -> None:
    hits = 0
    for path in (REPO / "krr_outputs").glob("*_peep.json"):
        text = path.read_text(encoding="utf-8")
        if "estleg:itemNumber" not in text:
            continue
        doc = json.loads(text)
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            if "estleg:Subsection" in (node.get("@type") or []) and node.get(
                "estleg:itemNumber"
            ):
                hits += 1
                break
        if hits:
            break
    assert hits == 1
