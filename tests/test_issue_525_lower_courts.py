"""#525 — first/second-instance court ingest from RT kohtulahendid."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.generate_lower_court_decisions import (
    classify_court,
    collect_hits,
    decision_node,
    parse_search_hit,
    write_corpus,
)
from estleg.generate_lower_court_decisions import main as ingest_main

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "kohtud" / "search_hits.json"
SAMPLE = REPO / "krr_outputs" / "kohtud" / "kohtud_sample_peep.json"
INDEX = REPO / "krr_outputs" / "kohtud" / "KOHTUD_INDEX.json"
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
SCHEMA = REPO / "docs" / "SCHEMA_REFERENCE.md"
README = REPO / "README.md"


def test_classify_court_skips_supreme_keeps_lower() -> None:
    assert classify_court("Riigikohus") is None
    assert classify_court("Riigikohtu kriminaalkolleegium") is None
    assert classify_court("Harju Maakohus Kentmanni kohtumaja") == (
        "firstInstance",
        "county",
    )
    assert classify_court("Tallinna Halduskohus") == (
        "firstInstance",
        "administrative",
    )
    assert classify_court("Tallinna Ringkonnakohus") == ("appeal", "circuit")
    assert classify_court("Tallinna ringkonnakohtu halduskolleegium") == (
        "appeal",
        "circuit",
    )


def test_fixture_drops_riigikohus_and_keeps_three_instances() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    total, hits = collect_hits(payload, limit=10)
    assert total == 4
    assert len(hits) == 3
    kinds = {hit["court_kind"] for hit in hits}
    assert kinds == {"county", "administrative", "circuit"}
    assert all(hit["case_nr"] for hit in hits)


def test_decision_node_uses_objekt_id() -> None:
    hit = parse_search_hit(
        json.loads(FIXTURE.read_text(encoding="utf-8"))["tulemused"][0]
    )
    assert hit is not None
    node = decision_node(hit)
    assert node["@id"] == "estleg:Kohtuasi_108397986"
    assert "estleg:CourtDecision" in node["@type"]
    assert node["estleg:courtLevel"] == "firstInstance"
    assert node["estleg:courtKind"] == "county"
    assert node["estleg:caseNumber"] == "2-04-2243/2"
    assert node["estleg:caseType"] == {"@id": "estleg:CaseType_Civil"}
    assert node["estleg:rtObjektId"] == "108397986"
    assert node["estleg:decisionLink"]["@value"].endswith("/kohtulahendid/108397986")


def test_write_corpus_and_cli_from_fixture(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _, hits = collect_hits(payload, limit=10)
    index = write_corpus(hits, out_dir=tmp_path, year=None, source_total=4)
    assert index["ingested"] == 3
    doc = json.loads((tmp_path / index["file"]).read_text(encoding="utf-8"))
    ids = [n["@id"] for n in doc["@graph"] if n.get("@id", "").startswith("estleg:Kohtuasi_")]
    assert "estleg:Kohtuasi_108397986" in ids
    assert not any(n.get("estleg:courtName") == "Riigikohus" for n in doc["@graph"])
    out = tmp_path / "cli"
    ingest_main(
        [
            "--from-fixture",
            str(FIXTURE),
            "--apply",
            "--out-dir",
            str(out),
        ]
    )
    assert (out / "KOHTUD_INDEX.json").is_file()


def test_committed_sample_and_docs() -> None:
    assert SAMPLE.is_file()
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    courts = [
        n
        for n in doc["@graph"]
        if isinstance(n, dict) and "estleg:CourtDecision" in (n.get("@type") or [])
    ]
    assert len(courts) == 3
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    assert index["ingested"] == 3
    vocab = VOCAB.read_text(encoding="utf-8")
    assert "estleg:courtLevel" in vocab
    assert "estleg:courtKind" in vocab
    text = SCHEMA.read_text(encoding="utf-8")
    assert "courtLevel" in text
    assert "maakohus" in text
    readme = README.read_text(encoding="utf-8")
    assert "kohtud" in readme
    assert "generate_lower_court_decisions" in readme
