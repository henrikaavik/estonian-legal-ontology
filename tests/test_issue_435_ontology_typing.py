"""#435: act/regulation individuals are not typed owl:Ontology."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.estleg_common import (
    DOMAIN_INDIVIDUAL_TYPES,
    is_domain_individual,
    strip_ontology_from_domain_individual,
)
from estleg.extract_sanctions import _find_act_node
from estleg.generate_all_laws import generate_law_jsonld

REPO = Path(__file__).resolve().parent.parent
ABIPOL = REPO / "krr_outputs" / "abipolitseiniku_seadus_peep.json"


def test_act_root_helpers_prefer_domain_individual() -> None:
    from estleg.estleg_common import act_root_node, is_graph_header

    doc = {
        "@graph": [
            {"@id": "estleg:Header", "@type": ["owl:Ontology"]},
            {"@id": "estleg:X_Map_2026", "@type": ["estleg:Act", "estleg:Law"]},
        ]
    }
    root = act_root_node(doc)
    assert root is not None
    assert root["@id"] == "estleg:X_Map_2026"
    assert is_graph_header(doc["@graph"][0])
    assert not is_graph_header(root)


def test_strip_removes_ontology_only_from_domain_individuals() -> None:
    act = {"@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]}
    assert is_domain_individual(act)
    assert strip_ontology_from_domain_individual(act) is True
    assert act["@type"] == ["estleg:Act", "estleg:Law"]
    assert strip_ontology_from_domain_individual(act) is False

    header = {"@type": ["owl:Ontology", "void:Dataset"]}
    assert not is_domain_individual(header)
    assert strip_ontology_from_domain_individual(header) is False
    assert "owl:Ontology" in header["@type"]


def test_find_act_node_does_not_require_owl_ontology() -> None:
    doc = {
        "@graph": [
            {"@id": "estleg:Header", "@type": ["owl:Ontology"]},
            {"@id": "estleg:X_Map_2026", "@type": ["estleg:Act", "estleg:Law"]},
        ]
    }
    found = _find_act_node(doc)
    assert found is not None
    assert found["@id"] == "estleg:X_Map_2026"


def test_generate_law_root_is_not_owl_ontology() -> None:
    import xml.etree.ElementTree as ET

    xml = """<?xml version="1.0" encoding="UTF-8"?>
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
    root = ET.fromstring(xml)
    doc = generate_law_jsonld(
        "Testseadus",
        "testseadus",
        root,
        abbreviation="TS",
        rt_url="/akt/1.xml",
        kehtiv="2026-05-24",
    )
    types = doc["@graph"][0]["@type"]
    assert "estleg:Act" in types
    assert "estleg:Law" in types
    assert "owl:Ontology" not in types


def test_committed_abipol_root_is_not_dual_typed() -> None:
    doc = json.loads(ABIPOL.read_text(encoding="utf-8"))
    root = doc["@graph"][0]
    types = set(root.get("@type") or [])
    assert "estleg:Act" in types
    assert "owl:Ontology" not in types


def test_committed_law_peeps_have_no_ontology_act_dual_types() -> None:
    dual = 0
    scanned = 0
    for path in (REPO / "krr_outputs").glob("*_peep.json"):
        scanned += 1
        text = path.read_text(encoding="utf-8")
        if '"estleg:Act"' not in text:
            continue
        doc = json.loads(text)
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            types = set(node.get("@type") or [])
            if "owl:Ontology" in types and types & DOMAIN_INDIVIDUAL_TYPES:
                dual += 1
    assert scanned > 500
    assert dual == 0


def test_combined_has_no_ontology_act_dual_types() -> None:
    combined = REPO / "krr_outputs" / "combined_ontology.jsonld"
    assert combined.is_file()
    dual = 0
    ontology_arrays = 0
    in_type = False
    items: list[str] = []
    with combined.open(encoding="utf-8") as handle:
        for line in handle:
            if not in_type:
                if line.rstrip().endswith('"@type": ['):
                    in_type = True
                    items = []
                continue
            stripped = line.strip()
            if stripped.startswith("]"):
                in_type = False
                if "owl:Ontology" in items:
                    ontology_arrays += 1
                    if DOMAIN_INDIVIDUAL_TYPES.intersection(items):
                        dual += 1
                continue
            items.append(stripped.rstrip(",").strip().strip('"'))
    assert ontology_arrays > 0
    assert dual == 0
