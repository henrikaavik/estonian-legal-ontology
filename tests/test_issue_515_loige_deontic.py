"""#515: deontic / targetGroup / sanctions attach to lõige nodes."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.classify_deontic import classify_provision
from estleg.classify_target_group import classify_files, classify_node
from estleg.extract_sanctions import extract_sanctions

REPO = Path(__file__).resolve().parent.parent
KARS = REPO / "krr_outputs" / "karistusseadustik_osa2_peep.json"


def test_classifiers_read_subsection_legal_text() -> None:
    text = (
        "Sõjavangi halva kohtlemise eest – karistatakse rahalise karistuse "
        "või kuni kolmeaastase vangistusega."
    )
    assert classify_provision(text)
    groups, _ = classify_node(
        {"estleg:legalText": text, "@type": ["estleg:Subsection"]}
    )
    assert extract_sanctions(text)
    assert groups is not None


def test_target_group_classify_files_includes_subsections(tmp_path: Path) -> None:
    peep = tmp_path / "demo_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:X_Par_1_Lg_1",
                        "@type": ["estleg:Subsection"],
                        "estleg:legalText": "Töötaja peab teatama tööandjale.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    classify_files([peep], report_path=tmp_path / "rep.json", write=True)
    doc = json.loads(peep.read_text(encoding="utf-8"))
    node = doc["@graph"][0]
    assert node.get("estleg:targetGroup")


def test_kars_loige_has_deontic_or_sanction() -> None:
    doc = json.loads(KARS.read_text(encoding="utf-8"))
    node = next(
        n
        for n in doc["@graph"]
        if n.get("@id") == "estleg:KARIST_2_Osa2_Par_98_Lg_1"
    )
    assert "estleg:Subsection" in node["@type"]
    assert node.get("estleg:normativeType") or node.get("estleg:hasSanction")
    assert node.get("estleg:targetGroup") or node.get("estleg:hasSanction")
