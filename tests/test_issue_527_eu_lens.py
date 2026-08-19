"""#527 — in-force + Estonia-relevance lens over the EUR-Lex acquis."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.generate_eu_legislation import (
    apply_estonia_relevance_lens,
    build_eurlex_lens,
    collect_estonia_relevant_celex,
    legislation_to_node,
    stamp_estonia_relevant,
)

REPO = Path(__file__).resolve().parent.parent
KRR = REPO / "krr_outputs"
VOCAB = KRR / "controlled_vocabulary.jsonld"
INDEX = KRR / "eurlex" / "EURLEX_INDEX.json"
DIRECTIVES = KRR / "eurlex" / "eurlex_directives_peep.json"
REPORT = KRR / "reports" / "transposition_mapping.json"
SCHEMA_REF = REPO / "docs" / "SCHEMA_REFERENCE.md"


def _vocab_index() -> dict[str, dict]:
    doc = json.loads(VOCAB.read_text(encoding="utf-8"))
    return {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }


def _as_id(value: object) -> str | None:
    if isinstance(value, dict):
        iri = value.get("@id")
        return iri if isinstance(iri, str) else None
    return value if isinstance(value, str) else None


def test_cv_declares_estonia_relevant() -> None:
    node = _vocab_index()["estleg:estoniaRelevant"]
    assert "owl:DatatypeProperty" in node["@type"]
    assert _as_id(node.get("rdfs:domain")) == "estleg:EULegislation"
    assert _as_id(node.get("rdfs:range")) == "xsd:boolean"


def test_schema_reference_documents_lens() -> None:
    text = SCHEMA_REF.read_text(encoding="utf-8")
    assert "estleg:estoniaRelevant" in text
    assert "EURLEX_INDEX.json" in text


def test_collect_reads_transposition_report(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "transposition_mapping.json").write_text(
        json.dumps({
            "mappings": [
                {"directive_celex": "32016L0798"},
                {"directive_celex": "31993L0013"},
                {"directive_celex": ""},
            ]
        }),
        encoding="utf-8",
    )
    assert collect_estonia_relevant_celex(tmp_path) == {
        "32016L0798",
        "31993L0013",
    }


def test_stamp_estonia_relevant_omits_false() -> None:
    node: dict = {"estleg:celexNumber": "31981L0643"}
    assert stamp_estonia_relevant(node, False) is False
    assert "estleg:estoniaRelevant" not in node
    assert stamp_estonia_relevant(node, True) is True
    assert node["estleg:estoniaRelevant"] == {
        "@value": "true",
        "@type": "xsd:boolean",
    }
    assert stamp_estonia_relevant(node, True) is False
    assert stamp_estonia_relevant(node, False) is True
    assert "estleg:estoniaRelevant" not in node


def test_legislation_to_node_emits_flag_when_item_is_relevant() -> None:
    item = {
        "celex": "32016L0798",
        "title": "Raudteeohutus",
        "authors": [],
        "estonia_relevant": True,
    }
    node = legislation_to_node(item, "Directive")
    assert node["estleg:estoniaRelevant"] == {
        "@value": "true",
        "@type": "xsd:boolean",
    }
    bare = legislation_to_node(
        {"celex": "31981L0643", "title": "Old", "authors": []},
        "Directive",
    )
    assert "estleg:estoniaRelevant" not in bare


def test_build_lens_counts_in_force_and_relevant() -> None:
    nodes = [
        legislation_to_node(
            {
                "celex": "32016L0798",
                "title": "A",
                "authors": [],
                "in_force": "true",
                "estonia_relevant": True,
            },
            "Directive",
        ),
        legislation_to_node(
            {
                "celex": "31981L0643",
                "title": "B",
                "authors": [],
                "in_force": "false",
            },
            "Directive",
        ),
        legislation_to_node(
            {
                "celex": "32000L0060",
                "title": "C",
                "authors": [],
                "in_force": "true",
            },
            "Directive",
        ),
    ]
    lens = build_eurlex_lens(nodes, {"32016L0798"})
    assert lens["in_force"] == 2
    assert lens["not_in_force"] == 1
    assert lens["estonia_relevant"] == 1
    assert lens["in_force_and_estonia_relevant"] == 1


def test_apply_lens_on_tmp_peep(tmp_path: Path) -> None:
    eurlex = tmp_path / "eurlex"
    eurlex.mkdir()
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "transposition_mapping.json").write_text(
        json.dumps({"mappings": [{"directive_celex": "32016L0798"}]}),
        encoding="utf-8",
    )
    node = legislation_to_node(
        {"celex": "32016L0798", "title": "Rail", "authors": [], "in_force": "true"},
        "Directive",
    )
    other = legislation_to_node(
        {"celex": "31981L0643", "title": "Old", "authors": []},
        "Directive",
    )
    (eurlex / "eurlex_directives_peep.json").write_text(
        json.dumps({"@graph": [node, other]}),
        encoding="utf-8",
    )
    (eurlex / "EURLEX_INDEX.json").write_text(
        json.dumps({"total_acts": 2, "by_type": {}}),
        encoding="utf-8",
    )
    stats = apply_estonia_relevance_lens(krr_dir=tmp_path, dry_run=False)
    assert stats["relevant_celex"] == 1
    assert stats["nodes_changed"] == 1
    graph = json.loads(
        (eurlex / "eurlex_directives_peep.json").read_text(encoding="utf-8")
    )["@graph"]
    by_celex = {n["estleg:celexNumber"]: n for n in graph}
    assert by_celex["32016L0798"]["estleg:estoniaRelevant"]["@value"] == "true"
    assert "estleg:estoniaRelevant" not in by_celex["31981L0643"]
    index = json.loads((eurlex / "EURLEX_INDEX.json").read_text(encoding="utf-8"))
    assert index["lens"]["estonia_relevant"] == 1
    assert index["lens"]["in_force"] == 1


def test_committed_index_has_lens_and_directives_are_tagged() -> None:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    lens = index["lens"]
    in_force = sum(entry["in_force"] for entry in index["by_type"].values())
    not_in_force = sum(entry["not_in_force"] for entry in index["by_type"].values())
    assert lens["in_force"] == in_force
    assert lens["not_in_force"] == not_in_force
    assert in_force + not_in_force == index["total_acts"]
    assert lens["estonia_relevant"] >= 200
    assert lens["in_force_and_estonia_relevant"] >= 1
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mapped = {m["directive_celex"] for m in report["mappings"]}
    graph = json.loads(DIRECTIVES.read_text(encoding="utf-8"))["@graph"]
    tagged = {
        node["estleg:celexNumber"]
        for node in graph
        if isinstance(node, dict)
        and isinstance(node.get("estleg:estoniaRelevant"), dict)
        and node["estleg:estoniaRelevant"].get("@value") == "true"
    }
    assert mapped <= tagged
    habitats = next(
        n for n in graph if n.get("estleg:celexNumber") == "31992L0043"
    )
    assert habitats["estleg:estoniaRelevant"]["@value"] == "true"
