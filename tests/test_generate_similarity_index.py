import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_similarity_index as similarity


def write_doc(path: Path, act_type: str = "estleg:Law") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Act_1",
                        "@type": ["owl:Ontology", "estleg:Act", act_type],
                    },
                    {
                        "@id": "estleg:Act_1_Par_1",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_Act1"],
                        "rdfs:label": "§ 1. Maakasutuse kontroll ja teavitamine",
                        "estleg:sourceAct": "Test act",
                        "estleg:summary": (
                            "Maakasutuse kontroll teavitamine ehitusluba "
                            "planeering järelevalve"
                        ),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def write_fresh_generator_shape_doc(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Act_1",
                        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                    },
                    {
                        "@id": "estleg:Act_1_Par_1",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_Act1"],
                        "rdfs:label": {
                            "@value": "§ 1. Maakasutuse kontroll ja teavitamine",
                            "@language": "et",
                        },
                        "estleg:sourceAct": {"@value": "Test act", "@language": "et"},
                        "estleg:summary": {
                            "@value": (
                                "Maakasutuse kontroll teavitamine ehitusluba "
                                "planeering järelevalve"
                            ),
                            "@language": "et",
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_state_regulation_file_contributes_similarity_candidates(tmp_path, monkeypatch):
    krr = tmp_path / "krr_outputs"
    path = krr / "regulations" / "riik" / "state_peep.json"
    write_doc(path, "estleg:NationalRegulation")
    monkeypatch.setattr(similarity, "KRR_DIR", krr)

    provisions, act_type = similarity.extract_provisions_from_file(path)

    assert act_type == "state_regulation"
    assert len(provisions) == 1
    assert provisions[0]["file"] == "regulations/riik/state_peep.json"
    assert provisions[0]["act_type"] == "state_regulation"


def test_non_act_file_is_not_a_similarity_candidate(tmp_path):
    path = tmp_path / "krr_outputs" / "concepts" / "concept_peep.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"@graph": [{"@id": "estleg:Concept", "@type": ["estleg:LegalConcept"]}]}),
        encoding="utf-8",
    )

    provisions, act_type = similarity.extract_provisions_from_file(path)

    assert provisions == []
    assert act_type is None


def test_fresh_generator_language_maps_contribute_similarity_candidates(tmp_path):
    path = tmp_path / "krr_outputs" / "law_peep.json"
    write_fresh_generator_shape_doc(path)

    provisions, act_type = similarity.extract_provisions_from_file(path)

    assert act_type == "law"
    assert len(provisions) == 1
    assert provisions[0]["label"] == "§ 1. Maakasutuse kontroll ja teavitamine"
    assert provisions[0]["source_act"] == "Test act"
    assert "maakasutuse" in provisions[0]["keywords"]


def test_label_keywords_do_not_make_similarity_candidate(tmp_path):
    path = tmp_path / "krr_outputs" / "law_peep.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Act_1",
                        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                    },
                    {
                        "@id": "estleg:Act_1_Par_1",
                        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_Act1"],
                        "rdfs:label": "§ 1. Maakasutuse kontroll ja teavitamine",
                        "estleg:summary": "Maakasutus",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    provisions, act_type = similarity.extract_provisions_from_file(path)

    assert act_type == "law"
    assert provisions == []
