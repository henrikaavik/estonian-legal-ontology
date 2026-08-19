"""#463 — EuroVoc subjects live in an overlay, not in regenerated peeps."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.classify_eurovoc import (
    overlay_node_for_act,
    write_eurovoc_overlay,
)
from estleg.estleg_common import COMBINED_OVERLAY_SUBDIRS
from estleg.fix_all_issues import generate_combined_jsonld

REPO = Path(__file__).resolve().parent.parent
DESIGN = REPO / "docs" / "EUROVOC_OVERLAY.md"
DOMAINS = [
    ("527", "constitutional-law", "riigiõigus", "constitutional law", 2, ["põhiseadus"]),
]


def test_design_doc_and_combined_merges_eurovoc_subdir() -> None:
    text = DESIGN.read_text(encoding="utf-8")
    assert "eurovoc_overlay.jsonld" in text
    assert "COMBINED_OVERLAY_SUBDIRS" in text
    assert "eurovoc" in COMBINED_OVERLAY_SUBDIRS


def test_overlay_node_is_subjects_only() -> None:
    node = overlay_node_for_act("estleg:PKS_Map_2026", DOMAINS)
    assert node["@id"] == "estleg:PKS_Map_2026"
    assert node["dcterms:subject"] == [{"@id": "http://eurovoc.europa.eu/527"}]
    assert node["eli:is_about"] == node["dcterms:subject"]
    assert "rdfs:label" not in node
    assert "estleg:legalText" not in node


def test_classify_main_writes_overlay_not_peep(tmp_path: Path, monkeypatch) -> None:
    from estleg import classify_eurovoc as ev

    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    peep = krr / "pks_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:PKS_Map_2026",
                        "@type": ["estleg:Act", "estleg:Law"],
                        "rdfs:label": "Perekonnaseadus",
                    },
                    {
                        "@id": "estleg:PKS_Par_1",
                        "@type": ["estleg:LegalProvision"],
                        "estleg:summary": "põhiseadus riigikogu president",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ev, "KRR_DIR", krr)
    monkeypatch.setattr(ev, "iter_peep_files", lambda *a, **k: [peep])
    monkeypatch.setattr(ev, "write_coverage_report", lambda *a, **k: None)
    ev.main([])
    overlay = json.loads(
        (krr / "eurovoc" / "eurovoc_overlay.jsonld").read_text(encoding="utf-8")
    )
    acts = [n for n in overlay["@graph"] if n.get("@id") == "estleg:PKS_Map_2026"]
    assert acts
    assert acts[0]["dcterms:subject"]
    peep_doc = json.loads(peep.read_text(encoding="utf-8"))
    root = peep_doc["@graph"][0]
    assert "dcterms:subject" not in root


def test_combined_keeps_overlay_after_peep_regen(tmp_path: Path) -> None:
    """A peep without subjects still gets them from the overlay in combined."""
    peep = {
        "@graph": [
            {
                "@id": "estleg:PKS_Map_2026",
                "@type": ["estleg:Law", "estleg:Act"],
                "rdfs:label": "Perekonnaseadus",
            }
        ]
    }
    (tmp_path / "pks_peep.json").write_text(
        json.dumps(peep, ensure_ascii=False), encoding="utf-8"
    )
    write_eurovoc_overlay(
        [overlay_node_for_act("estleg:PKS_Map_2026", DOMAINS)],
        dest=tmp_path / "eurovoc" / "eurovoc_overlay.jsonld",
    )
    (tmp_path / "controlled_vocabulary.jsonld").write_text(
        json.dumps({"@graph": [{"@id": "estleg:Act", "@type": ["owl:Class"]}]}),
        encoding="utf-8",
    )
    generate_combined_jsonld(tmp_path)
    combined = json.loads(
        (tmp_path / "combined_ontology.jsonld").read_text(encoding="utf-8")
    )
    by_id = {n["@id"]: n for n in combined["@graph"] if isinstance(n, dict)}
    assert by_id["estleg:PKS_Map_2026"]["dcterms:subject"] == [
        {"@id": "http://eurovoc.europa.eu/527"}
    ]
