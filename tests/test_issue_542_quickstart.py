"""#542: README 5-minute start + examples/quickstart.py answer real SPARQL facts.

The default (non-corpus) suite must not parse the 265 MB combined graph.
Helpers are imported from the example; SPARQL is not reimplemented here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdflib import Graph

import quickstart

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
README = REPO / "README.md"

ESTLEG = "https://w3id.org/estleg/"

FIXTURE_CONTEXT = {
    "estleg": ESTLEG,
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

FIXTURE_DOC = {
    "@context": FIXTURE_CONTEXT,
    "@graph": [
        {
            "@id": "estleg:KARIST_2_Osa2",
            "@type": ["estleg:Act", "estleg:Law"],
            "rdfs:label": "Karistusseadustik",
        },
        {
            "@id": "estleg:KARIST_2_Osa2_Par_113",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "rdfs:label": "§ 113. Tapmine",
            "estleg:partOfAct": {"@id": "estleg:KARIST_2_Osa2"},
            "estleg:hasSanction": {
                "@id": "estleg:Sanction_KARIST_2_Osa2_Par_113_imprisonment"
            },
        },
        {
            "@id": "estleg:Sanction_KARIST_2_Osa2_Par_113_imprisonment",
            "@type": ["owl:NamedIndividual", "estleg:Sanction"],
            "estleg:sanctionType": "imprisonment",
            "estleg:applicableProvision": {"@id": "estleg:KARIST_2_Osa2_Par_113"},
            "estleg:minPenalty": "6 years",
            "estleg:maxPenalty": "15 years",
            "estleg:minPenaltyAmount": {"@value": "6", "@type": "xsd:decimal"},
            "estleg:maxPenaltyAmount": {"@value": "15", "@type": "xsd:decimal"},
            "estleg:minPenaltyUnit": "years",
            "estleg:maxPenaltyUnit": "years",
            "rdfs:label": "Imprisonment, 6 years to 15 years (KARIST § 113.)",
        },
    ],
}


def _fixture_graph() -> Graph:
    graph = Graph()
    graph.parse(data=json.dumps(FIXTURE_DOC), format="json-ld")
    return graph


def test_query_helpers_answer_fixture_facts() -> None:
    graph = _fixture_graph()

    assert quickstart.query_act_count(graph) == 1

    part = quickstart.query_part_of_act(graph)
    assert part is not None
    assert part["part"].endswith("KARIST_2_Osa2_Par_113")
    assert part["act"].endswith("KARIST_2_Osa2")
    assert "113" in part["part_label"]
    assert "Karistusseadustik" in part["act_label"]

    sanction = quickstart.query_sanction(graph)
    assert sanction is not None
    assert sanction["provision"].endswith("KARIST_2_Osa2_Par_113")
    assert sanction["type"] == "imprisonment"
    assert "6" in (sanction["min"] or sanction["min_amt"])
    assert "15" in (sanction["max"] or sanction["max_amt"])

    acts, part_sentence, sanction_sentence = quickstart.answered_sentences(graph)
    assert acts == "The loaded graph contains 1 Act."
    assert "§ 113. Tapmine" in part_sentence
    assert "Karistusseadustik" in part_sentence
    assert "is part of" in part_sentence
    assert "KarS" in sanction_sentence
    assert "113" in sanction_sentence
    assert "6–15" in sanction_sentence
    assert "imprisonment" in sanction_sentence


def test_readme_has_three_command_five_minute_start() -> None:
    text = README.read_text(encoding="utf-8")
    assert "## Quick Start" in text
    assert "examples/quickstart.py" in text

    after_qs = text.split("## Quick Start", 1)[1]
    five_min, _, rest = after_qs.partition("### Load surfaces")
    assert "5-minute start" in five_min
    assert "### Load surfaces" in after_qs
    assert "Load a single file with Python (rdflib)" in rest

    pull = "git lfs pull -I krr_outputs/combined_ontology.jsonld"
    pip = "python3 -m pip install 'rdflib>=7.1,<8'"
    run = "python3 examples/quickstart.py"
    assert pull in five_min
    assert pip in five_min
    assert run in five_min
    assert five_min.index(pull) < five_min.index(pip) < five_min.index(run)


def test_is_lfs_pointer_matches_pointer_prefix(tmp_path: Path) -> None:
    pointer = tmp_path / "combined_ontology.jsonld"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n",
        encoding="utf-8",
    )
    real = tmp_path / "peep.json"
    real.write_text('{"@graph": []}\n', encoding="utf-8")
    assert quickstart.is_lfs_pointer(pointer) is True
    assert quickstart.is_real_jsonld(pointer) is False
    assert quickstart.is_lfs_pointer(real) is False
    assert quickstart.is_real_jsonld(real) is True


def test_resolve_prefers_real_combined_over_fallback(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    combined = krr / "combined_ontology.jsonld"
    combined.write_text('{"@graph": []}\n', encoding="utf-8")
    (krr / "perekonnaseadus_peep.json").write_text('{"@graph": []}\n', encoding="utf-8")
    paths = quickstart.resolve_load_paths(repo_root=tmp_path)
    assert paths == [combined]


def test_resolve_falls_back_when_combined_is_lfs_pointer(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    sanctions = krr / "sanctions"
    sanctions.mkdir(parents=True)
    (krr / "combined_ontology.jsonld").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n",
        encoding="utf-8",
    )
    peep = krr / "perekonnaseadus_peep.json"
    peep.write_text('{"@graph": []}\n', encoding="utf-8")
    sidecar = sanctions / "sanctions_karistusseadustik_osa2.json"
    sidecar.write_text('{"@graph": []}\n', encoding="utf-8")
    paths = quickstart.resolve_load_paths(repo_root=tmp_path)
    assert combined_not_loaded(paths)
    assert peep in paths
    assert sidecar in paths


def combined_not_loaded(paths: list[Path]) -> bool:
    return all(path.name != "combined_ontology.jsonld" for path in paths)


def test_main_graph_flag_prints_answered_sentences(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "tiny.jsonld"
    path.write_text(json.dumps(FIXTURE_DOC), encoding="utf-8")
    assert quickstart.main(["--graph", str(path)]) == 0
    out = capsys.readouterr().out
    assert "The loaded graph contains 1 Act." in out
    assert "is part of" in out
    assert "6–15" in out
    assert " -> " not in out
