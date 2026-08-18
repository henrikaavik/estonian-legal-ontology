"""#541: JSON-LD → Turtle / N-Triples / N-Quads serializer.

Fixture path goes through the shipped functions (no reimplementation).
The committed proof dump must be a real N-Triples file, not an LFS pointer.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Dataset, Graph

import serialize_corpus as sc

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
PROOF_NT = REPO / "krr_outputs" / "exports" / "abipolitseiniku_seadus.nt"
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

FIXTURE = {
    "@context": {
        "estleg": "https://w3id.org/estleg/",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    },
    "@graph": [
        {
            "@id": "estleg:ABIPOL_Map_2026",
            "@type": "estleg:Act",
            "rdfs:label": "Abipolitseiniku seadus",
        }
    ],
}


def test_fixture_roundtrip_nt_ttl_nq() -> None:
    graph = sc.graph_from_jsonld(FIXTURE)
    assert len(graph) > 0

    nt = sc.serialize_graph(graph, "nt")
    parsed_nt = Graph()
    parsed_nt.parse(data=nt, format="nt")
    assert len(parsed_nt) > 0
    assert len(parsed_nt) == len(graph)

    ttl = sc.serialize_graph(graph, "ttl")
    parsed_ttl = Graph()
    parsed_ttl.parse(data=ttl, format="turtle")
    assert len(parsed_ttl) > 0
    assert len(parsed_ttl) == len(graph)

    nq = sc.serialize_graph(graph, "nq", graph_iri=sc.DEFAULT_GRAPH_IRI)
    parsed_nq = Dataset()
    parsed_nq.parse(data=nq, format="nquads")
    assert len(parsed_nq) > 0
    assert sc.DEFAULT_GRAPH_IRI in sc.named_graph_iris(parsed_nq)


def test_nq_graph_iri_derived_from_filename(tmp_path: Path) -> None:
    source = tmp_path / "abipolitseiniku_seadus_peep.json"
    dest = tmp_path / "abipolitseiniku_seadus.nq"
    source.write_text(
        '{"@context":{"estleg":"https://w3id.org/estleg/","rdfs":'
        '"http://www.w3.org/2000/01/rdf-schema#"},'
        '"@graph":[{"@id":"estleg:ABIPOL_Map_2026","@type":"estleg:Act",'
        '"rdfs:label":"Abipolitseiniku seadus"}]}',
        encoding="utf-8",
    )
    result = sc.serialize_file(source, dest, "nq")
    assert result["graph_iri"] == "https://w3id.org/estleg/graph/abipolitseiniku_seadus"
    dataset = Dataset()
    dataset.parse(dest, format="nquads")
    assert result["graph_iri"] in sc.named_graph_iris(dataset)


def test_combined_stem_uses_default_graph_iri() -> None:
    assert sc.graph_iri_for("krr_outputs/combined_ontology.jsonld") == sc.DEFAULT_GRAPH_IRI


def test_committed_nt_proof_is_real_and_parses() -> None:
    assert PROOF_NT.is_file(), f"missing proof dump {PROOF_NT}"
    text = PROOF_NT.read_text(encoding="utf-8")
    assert not text.startswith(LFS_POINTER_PREFIX)
    graph = Graph()
    graph.parse(PROOF_NT, format="nt")
    assert len(graph) > 0
    assert "estleg:ABIPOL" in text or "Abipolitseinik" in text


def test_readme_documents_rdf_serializations_and_load_budget() -> None:
    text = README.read_text(encoding="utf-8")
    assert "### 5-minute start" in text
    assert "### Load surfaces" in text
    assert "### RDF serializations" in text
    after_qs = text.split("## Quick Start", 1)[1]
    five_min, _, rest = after_qs.partition("### Load surfaces")
    assert "5-minute start" in five_min
    assert "### RDF serializations" in rest
    assert "serialize_corpus.py" in rest
    assert "2,247,778" in rest
    assert "44.4 s" in rest
    assert "3,089 MB" in rest
    assert "prefer" in rest.lower()
    assert ".nt" in rest
    assert "N-Quads" in rest
