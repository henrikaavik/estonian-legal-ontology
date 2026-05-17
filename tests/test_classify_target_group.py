"""Tests for scripts/classify_target_group.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_classify_text_covers_all_target_group_enums():
    from classify_target_group import classify_text

    assert classify_text("Töötaja peab esitama andmed") == ["citizen"]
    assert classify_text("Tööandja peab pidama arvestust") == ["business"]
    assert classify_text("Keskkonnaamet võib anda loa") == ["public_body"]
    assert classify_text("Ametnik on kohustatud kontrollima") == ["official"]
    assert classify_text("Mittetulundusühing võib taotleda toetust") == ["ngo"]


def test_classify_node_accepts_multi_valued_duty_holder():
    from classify_target_group import classify_node

    node = {
        "estleg:dutyHolder": "Tööandja ja töötaja",
        "estleg:summary": "Pooltel on õigus kokku leppida.",
    }

    groups, used_duty_holder = classify_node(node)

    assert used_duty_holder is True
    assert groups == ["citizen", "business"]


def test_classify_node_uses_deontic_text_when_duty_holder_missing():
    from classify_target_group import classify_node

    node = {
        "estleg:summary": "Tarbijal on õigus saada ettevõtjalt teavet.",
    }

    groups, used_duty_holder = classify_node(node)

    assert used_duty_holder is False
    assert groups == ["citizen", "business"]


def test_classify_files_writes_target_groups_and_report(tmp_path):
    from classify_target_group import classify_files

    peep = tmp_path / "sample_peep.json"
    report_path = tmp_path / "target_group_report.json"
    peep.write_text(
        json.dumps(
            {
                "@context": {
                    "estleg": "https://data.riik.ee/ontology/estleg#",
                    "owl": "http://www.w3.org/2002/07/owl#",
                },
                "@graph": [
                    {"@id": "estleg:TEST_Map_2026", "@type": ["owl:Ontology", "estleg:Act"]},
                    {
                        "@id": "estleg:TEST_Par_1",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 1",
                        "estleg:summary": "Tööandja ja töötaja peavad järgima korda.",
                        "estleg:dutyHolder": "Tööandja ja töötaja",
                    },
                    {
                        "@id": "estleg:TEST_Par_2",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                        "estleg:paragrahv": "§ 2",
                        "estleg:summary": "Määratlus, sihtgruppi ei tuletata.",
                        "estleg:dutyHolder": "Tundmatu roll",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = classify_files([peep], report_path=report_path)
    doc = json.loads(peep.read_text(encoding="utf-8"))
    by_id = {node["@id"]: node for node in doc["@graph"]}

    assert by_id["estleg:TEST_Par_1"]["estleg:targetGroup"] == ["citizen", "business"]
    assert "estleg:targetGroup" not in by_id["estleg:TEST_Par_2"]
    assert report["summary"]["provisions_with_dutyHolder"] == 2
    assert report["summary"]["dutyHolder_classified"] == 1
    assert report["summary"]["files_changed"] == 1
    assert report_path.exists()
    assert report["top_unmapped_duty_holders"] == [
        {"value": "Tundmatu roll", "count": 1}
    ]
