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


COMBINED_NT = REPO / "krr_outputs" / "combined_ontology.nt"
COMBINED_NQ = REPO / "krr_outputs" / "combined_ontology.nq"
COMBINED_TTL = REPO / "krr_outputs" / "combined_ontology.ttl"
GITATTRIBUTES = REPO / ".gitattributes"
# Last serialize_corpus.py run over combined_ontology.jsonld (do not
# rdflib-parse the 400+ MB dumps in pytest).
COMBINED_TRIPLE_COUNT = 2_664_215


def _count_nonempty_lines(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def _skip_if_missing_or_lfs(path: Path) -> None:
    if not path.is_file():
        import pytest

        pytest.skip(f"missing serialization {path.name}")
    if sc.is_lfs_pointer(path):
        import pytest

        pytest.skip(f"{path.name} is an LFS pointer; git lfs pull required")


def test_gitattributes_lfs_tracks_combined_serializations() -> None:
    text = GITATTRIBUTES.read_text(encoding="utf-8")
    for ext in (".nt", ".nq", ".ttl"):
        assert f"krr_outputs/combined_ontology{ext} filter=lfs" in text


def test_combined_nt_streams_expected_triple_count() -> None:
    _skip_if_missing_or_lfs(COMBINED_NT)
    assert _count_nonempty_lines(COMBINED_NT) == COMBINED_TRIPLE_COUNT
    with COMBINED_NT.open(encoding="utf-8", errors="replace") as handle:
        first = handle.readline()
    assert first.startswith("<")
    assert first.rstrip().endswith(".")


def test_combined_nq_uses_default_named_graph() -> None:
    _skip_if_missing_or_lfs(COMBINED_NQ)
    assert _count_nonempty_lines(COMBINED_NQ) == COMBINED_TRIPLE_COUNT
    with COMBINED_NQ.open(encoding="utf-8", errors="replace") as handle:
        sample = handle.readline()
    assert sc.DEFAULT_GRAPH_IRI in sample
    assert sample.rstrip().endswith(".")


def test_combined_nt_target_group_objects_are_iris() -> None:
    _skip_if_missing_or_lfs(COMBINED_NT)
    leftover = 0
    iris = 0
    predicate = "<https://w3id.org/estleg/targetGroup>"
    with COMBINED_NT.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if predicate not in line:
                continue
            if "TargetGroup_" in line:
                iris += 1
            elif any(
                token in line
                for token in (
                    '"citizen"',
                    '"business"',
                    '"public_body"',
                    '"official"',
                    '"ngo"',
                )
            ):
                leftover += 1
    assert leftover == 0
    assert iris > 0


def test_combined_ttl_is_prefixed_turtle() -> None:
    _skip_if_missing_or_lfs(COMBINED_TTL)
    with COMBINED_TTL.open(encoding="utf-8", errors="replace") as handle:
        first = handle.readline()
    assert not first.startswith(LFS_POINTER_PREFIX)
    assert first.startswith("@prefix") or first.startswith("@base")


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
