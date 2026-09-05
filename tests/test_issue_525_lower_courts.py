"""#525 — first/second-instance court ingest from RT kohtulahendid.

#689 additionally pins the sample marking: the ingest is one capped search
page, so every write must be machine-readably flagged as a sample, and the
synthetic ``2000000xx`` placeholder rows must stay out of both the fixture
and the committed corpus.
"""

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

# #689: ids minted for a hand-written fixture, never real RT objektId values.
SYNTHETIC_ID_PREFIX = "estleg:Kohtuasi_2000000"


def ontology_header(doc: dict) -> dict:
    """The single ``owl:Ontology`` node of a kohtud peep."""
    headers = [
        node
        for node in doc["@graph"]
        if isinstance(node, dict) and "owl:Ontology" in (node.get("@type") or [])
    ]
    assert len(headers) == 1
    return headers[0]


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


def test_fixture_drops_riigikohus_and_keeps_real_row() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    total, hits = collect_hits(payload, limit=10)
    assert total == 2
    assert len(hits) == 1
    kinds = {hit["court_kind"] for hit in hits}
    assert kinds == {"county"}
    assert all(hit["case_nr"] for hit in hits)


def test_fixture_carries_no_synthetic_rows() -> None:
    """#689 — the fixture must not reintroduce invented objektId rows."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    oids = {str(row.get("objektId")) for row in payload["tulemused"]}
    assert not any(oid.startswith("2000000") for oid in oids)
    assert FIXTURE.read_text(encoding="utf-8").count("200000001") == 0


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
    index = write_corpus(hits, out_dir=tmp_path, year=None, source_total=2)
    assert index["ingested"] == 1
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


def test_write_corpus_marks_sample_without_year(tmp_path: Path) -> None:
    """#689 — the undated write is labelled and flagged as a sample."""
    index = write_corpus([], out_dir=tmp_path, year=None, source_total=0)
    assert index["sample"] is True
    assert "(näidis)" in index["note"]
    doc = json.loads((tmp_path / index["file"]).read_text(encoding="utf-8"))
    header = ontology_header(doc)
    assert header["@id"] == "estleg:Kohtud_sample"
    assert header["rdfs:label"]["@value"] == (
        "Esimese ja teise astme kohtulahendid (näidis)"
    )
    assert header["estleg:isSampleData"] is True
    assert header["rdfs:comment"]["@language"] == "en"
    assert "sample" in header["rdfs:comment"]["@value"]


def test_write_corpus_marks_sample_with_year(tmp_path: Path) -> None:
    """#689 — a year-scoped write is still one capped page, so still a sample."""
    index = write_corpus([], out_dir=tmp_path, year=2026, source_total=0)
    assert index["year"] == 2026
    assert index["sample"] is True
    assert "(näidis)" in index["note"]
    doc = json.loads((tmp_path / index["file"]).read_text(encoding="utf-8"))
    header = ontology_header(doc)
    assert header["@id"] == "estleg:Kohtud_2026"
    assert header["rdfs:label"]["@value"] == (
        "Esimese ja teise astme kohtulahendid 2026 (näidis)"
    )
    assert header["rdfs:label"]["@value"].endswith("(näidis)")
    assert header["estleg:isSampleData"] is True
    assert header["rdfs:comment"]["@language"] == "en"


def test_committed_sample_is_marked_and_synthetic_free() -> None:
    """#689 — the shipped peep carries the sample marking and no invented ids."""
    raw = SAMPLE.read_text(encoding="utf-8")
    assert SYNTHETIC_ID_PREFIX not in raw
    doc = json.loads(raw)
    header = ontology_header(doc)
    assert header["rdfs:label"]["@value"].endswith("(näidis)")
    assert header["estleg:isSampleData"] is True
    assert header["rdfs:comment"]["@value"]
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    assert index["sample"] is True
    assert "(näidis)" in index["note"]
    assert index["api_total"] == 2
    assert index["by_kind"] == {"county": 1}


def test_committed_sample_and_docs() -> None:
    assert SAMPLE.is_file()
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    courts = [
        n
        for n in doc["@graph"]
        if isinstance(n, dict) and "estleg:CourtDecision" in (n.get("@type") or [])
    ]
    assert len(courts) == 1
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    assert index["ingested"] == 1
    vocab = VOCAB.read_text(encoding="utf-8")
    assert "estleg:courtLevel" in vocab
    assert "estleg:courtKind" in vocab
    text = SCHEMA.read_text(encoding="utf-8")
    assert "courtLevel" in text
    assert "maakohus" in text
    readme = README.read_text(encoding="utf-8")
    assert "kohtud" in readme
    assert "generate_lower_court_decisions" in readme
