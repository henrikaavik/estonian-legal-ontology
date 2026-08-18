"""Issue #436 — Chapter refers to TopicCluster via dcterms:subject, not owl:sameAs."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import generate_all_laws
from generate_all_laws import (
    chapter_cluster_subject,
    retarget_chapter_cluster_identity,
)

REPO = Path(__file__).resolve().parents[1]
KRR = REPO / "krr_outputs"


def _sameas_iris(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        iri = value.get("@id")
        return [iri] if isinstance(iri, str) else []
    if isinstance(value, list):
        iris: list[str] = []
        for item in value:
            iris.extend(_sameas_iris(item))
        return iris
    return []


def _is_chapter(node: dict) -> bool:
    types = node.get("@type", [])
    if isinstance(types, str):
        types = [types]
    return isinstance(types, list) and "estleg:Chapter" in types


def test_generator_chapter_uses_subject_not_sameas():
    xml = """
    <akt>
      <sisu>
        <peatykk>
          <peatykkNr>1</peatykkNr>
          <peatykkPealkiri>Üldsätted</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>1</paragrahvNr>
            <kuvatavNr>§ 1.</kuvatavNr>
            <paragrahvPealkiri>Eesmärk</paragrahvPealkiri>
            <loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </akt>
    """
    generate_all_laws._used_prefixes.clear()
    doc = generate_all_laws.generate_law_jsonld(
        "Abipolitseiniku seadus",
        "abipolitseiniku_seadus",
        ET.fromstring(xml),
        abbreviation="ABIPOL",
        allocator=generate_all_laws.PrefixAllocator(registry={}),
    )
    chapter = next(
        n for n in doc["@graph"] if str(n.get("@id", "")).startswith("estleg:Chapter_")
    )
    cluster = next(
        n for n in doc["@graph"] if str(n.get("@id", "")).startswith("estleg:Cluster_")
    )
    assert "owl:sameAs" not in chapter
    assert chapter["dcterms:subject"] == {"@id": cluster["@id"]}
    assert cluster.get("skos:inScheme") == {"@id": "estleg:ABIPOL_TopicScheme"}


def test_retarget_moves_cluster_sameas_on_chapter_only():
    chapter = {
        "@id": "estleg:Chapter_ABIPOL_1",
        "@type": ["owl:NamedIndividual", "estleg:Chapter"],
        "estleg:partOfAct": {"@id": "estleg:ABIPOL_Map_2026"},
        "owl:sameAs": {"@id": "estleg:Cluster_ABIPOL_1"},
    }
    assert retarget_chapter_cluster_identity(chapter) is True
    assert "owl:sameAs" not in chapter
    assert chapter["dcterms:subject"] == chapter_cluster_subject("estleg:Cluster_ABIPOL_1")
    keys = list(chapter)
    assert keys[keys.index("estleg:partOfAct") + 1] == "dcterms:subject"

    act = {
        "@id": "estleg:ABIPOL_Map_2026",
        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        "owl:sameAs": {"@id": "https://www.riigiteataja.ee/akt/106072023009.xml"},
    }
    snapshot = dict(act)
    assert retarget_chapter_cluster_identity(act) is False
    assert act == snapshot


def test_abipol_chapter_1_subject_pin():
    path = KRR / "abipolitseiniku_seadus_peep.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    chapter = next(n for n in doc["@graph"] if n.get("@id") == "estleg:Chapter_ABIPOL_1")
    assert chapter["dcterms:subject"]["@id"] == "estleg:Cluster_ABIPOL_1"
    assert "owl:sameAs" not in chapter


def test_root_peeps_have_zero_chapter_cluster_sameas():
    leftovers: list[tuple[str, object, str]] = []
    for path in sorted(KRR.glob("*_peep.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        graph = doc.get("@graph") if isinstance(doc, dict) else None
        if not isinstance(graph, list):
            continue
        for node in graph:
            if not isinstance(node, dict) or not _is_chapter(node):
                continue
            for iri in _sameas_iris(node.get("owl:sameAs")):
                if iri.startswith("estleg:Cluster_"):
                    leftovers.append((path.name, node.get("@id"), iri))
    assert leftovers == [], leftovers[:10]
