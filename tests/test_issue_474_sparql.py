"""#474 named-graph N-Quads dump + docker-compose SPARQL quickstart."""

from __future__ import annotations

import gzip
from pathlib import Path

from rdflib import Dataset

from estleg.serialize_corpus import named_graph_iris
from estleg.serialize_named_graphs import (
    GRAPH_CURIA,
    GRAPH_DRAFTS,
    GRAPH_ENRICHMENT,
    GRAPH_EURLEX,
    GRAPH_LAWS,
    GRAPH_REGULATIONS,
    GRAPH_RIIGIKOHUS,
    SAMPLE_DUMP,
    SLOTS,
    nquads_from_jsonld,
    retarget_nquad_line,
    sample_documents,
    write_full_dump,
    write_sample_dump,
)

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
COMPOSE = REPO / "docker-compose.yml"
API_GUIDE = REPO / "docs" / "API_GUIDE.md"
EXPECTED_GRAPHS = {
    GRAPH_LAWS,
    GRAPH_REGULATIONS,
    GRAPH_RIIGIKOHUS,
    GRAPH_EURLEX,
    GRAPH_CURIA,
    GRAPH_DRAFTS,
    GRAPH_ENRICHMENT,
}


def test_retarget_nquad_line_rewrites_existing_graph() -> None:
    line = (
        "<https://w3id.org/estleg/ABIPOL_Map> "
        "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
        "<https://w3id.org/estleg/Act> "
        "<https://w3id.org/estleg/graph/combined> .\n"
    )
    out = retarget_nquad_line(line, GRAPH_LAWS)
    assert GRAPH_LAWS in out
    assert "graph/combined" not in out
    assert out.endswith(" .\n")


def test_retarget_nquad_line_appends_graph_to_ntriples() -> None:
    line = (
        "<https://w3id.org/estleg/ABIPOL_Map> "
        "<http://www.w3.org/2000/01/rdf-schema#label> "
        '"Abipolitseiniku seadus" .\n'
    )
    out = retarget_nquad_line(line, GRAPH_LAWS)
    assert out.endswith(f"<{GRAPH_LAWS}> .\n")


def test_sample_documents_cover_all_seven_slots() -> None:
    docs = sample_documents()
    assert set(docs) == {slot.name for slot in SLOTS}
    assert {slot.graph_iri for slot in SLOTS} == EXPECTED_GRAPHS


def test_nquads_from_jsonld_uses_slot_graph(tmp_path: Path) -> None:
    text = nquads_from_jsonld(sample_documents()["laws"], GRAPH_LAWS)
    dataset = Dataset()
    dataset.parse(data=text, format="nquads")
    assert GRAPH_LAWS in named_graph_iris(dataset)
    assert GRAPH_EURLEX not in named_graph_iris(dataset)


def test_write_sample_dump_has_seven_named_graphs(tmp_path: Path) -> None:
    dest = tmp_path / "estleg_all_sample.nq.gz"
    counts = write_sample_dump(dest)
    assert set(counts) == EXPECTED_GRAPHS
    assert dest.is_file()
    raw = gzip.decompress(dest.read_bytes()).decode("utf-8")
    dataset = Dataset()
    dataset.parse(data=raw, format="nquads")
    assert set(named_graph_iris(dataset)) == EXPECTED_GRAPHS
    assert any("ABIPOL_Map" in line for line in raw.splitlines())


def test_write_full_dump_retargets_nq_and_skips_missing(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    nq = krr / "combined_ontology.nq"
    nq.write_text(
        "<https://w3id.org/estleg/ABIPOL_Map> "
        "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
        "<https://w3id.org/estleg/Act> "
        "<https://w3id.org/estleg/graph/combined> .\n",
        encoding="utf-8",
    )
    dest = tmp_path / "estleg_all.nq.gz"
    counts = write_full_dump(krr_dir=krr, dest=dest)
    assert counts == {GRAPH_LAWS: 1}
    raw = gzip.decompress(dest.read_bytes()).decode("utf-8")
    assert GRAPH_LAWS in raw
    assert "graph/combined" not in raw


def test_committed_sample_dump_parses_seven_graphs() -> None:
    assert SAMPLE_DUMP.is_file(), f"missing {SAMPLE_DUMP}"
    raw = gzip.decompress(SAMPLE_DUMP.read_bytes()).decode("utf-8")
    dataset = Dataset()
    dataset.parse(data=raw, format="nquads")
    assert set(named_graph_iris(dataset)) == EXPECTED_GRAPHS


def test_compose_and_readme_document_oxigraph_7878() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "oxigraph/oxigraph" in compose
    assert "7878:7878" in compose
    assert "estleg_all_sample.nq.gz" in compose
    assert "ESTLEG_DUMP" in compose
    readme = README.read_text(encoding="utf-8")
    assert "docker compose up" in readme
    assert "http://localhost:7878" in readme
    assert "estleg_all.nq.gz" in readme
    assert "graph/laws" in readme
    guide = API_GUIDE.read_text(encoding="utf-8")
    assert "docker compose up" in guide
    assert "localhost:7878" in guide
