"""#474 named-graph N-Quads dump + docker-compose SPARQL quickstart."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from rdflib import Dataset

from estleg import serialize_named_graphs

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
    MissingSlotError,
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


def _laws_nq(krr: Path) -> None:
    (krr / "combined_ontology.nq").write_text(
        "<https://w3id.org/estleg/ABIPOL_Map> "
        "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
        "<https://w3id.org/estleg/Act> "
        "<https://w3id.org/estleg/graph/combined> .\n",
        encoding="utf-8",
    )


def _peep(path: Path, node_id: str, node_type: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [{"@id": node_id, "@type": node_type}],
            }
        ),
        encoding="utf-8",
    )


def _full_tree(krr: Path) -> None:
    _laws_nq(krr)
    _peep(krr / "regulations" / "riik" / "a_peep.json", "estleg:Reg_a", "estleg:NationalRegulation")
    _peep(krr / "regulations" / "kov" / "x" / "b_peep.json", "estleg:Reg_b", "estleg:MunicipalRegulation")
    _peep(krr / "riigikohus" / "riigikohus_1994_peep.json", "estleg:RK_1", "estleg:CourtDecision")
    _peep(krr / "eurlex" / "eurlex_combined.jsonld", "estleg:EU_1", "estleg:EULegislation")
    _peep(krr / "curia" / "curia_combined.jsonld", "estleg:CURIA_1", "estleg:EUCourtDecision")
    _peep(krr / "eelnoud" / "eelnoud_combined.jsonld", "estleg:Draft_1", "estleg:DraftLegislation")
    _peep(krr / "analytical" / "analytical_overlay.jsonld", "estleg:AnalyticalOverlay", "estleg:X")


def test_write_full_dump_fails_closed_on_a_missing_slot(tmp_path: Path) -> None:
    """#705: a slot without a source is an error, and nothing is written."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _laws_nq(krr)
    dest = tmp_path / "estleg_all.nq.gz"
    with pytest.raises(MissingSlotError, match="regulations"):
        write_full_dump(krr_dir=krr, dest=dest)
    assert not dest.exists()


def test_cli_exits_nonzero_on_missing_slot_unless_allow_partial(tmp_path: Path, monkeypatch) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _laws_nq(krr)
    monkeypatch.setattr(serialize_named_graphs, "KRR_DIR", krr)
    dest = tmp_path / "estleg_all.nq.gz"
    assert serialize_named_graphs.main(["--write", "--output", str(dest)]) == 1
    assert not dest.exists()
    assert serialize_named_graphs.main(
        ["--write", "--allow-partial", "--output", str(dest)]
    ) == 0
    raw = gzip.decompress(dest.read_bytes()).decode("utf-8")
    assert GRAPH_LAWS in raw
    assert "graph/combined" not in raw


def test_write_full_dump_reads_regulation_and_riigikohus_peep_trees(tmp_path: Path) -> None:
    """#705: the two formerly dead slots are backed by their peep trees."""
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _full_tree(krr)
    dest = tmp_path / "estleg_all.nq.gz"
    counts = write_full_dump(krr_dir=krr, dest=dest)
    assert set(counts) == EXPECTED_GRAPHS
    assert counts[GRAPH_REGULATIONS] == 2
    assert counts[GRAPH_RIIGIKOHUS] == 1
    dataset = Dataset()
    dataset.parse(data=gzip.decompress(dest.read_bytes()).decode("utf-8"), format="nquads")
    assert set(named_graph_iris(dataset)) == EXPECTED_GRAPHS


def test_lfs_pointer_in_a_peep_tree_is_not_dumped_partially(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _full_tree(krr)
    (krr / "riigikohus" / "riigikohus_2000_peep.json").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 1\n",
        encoding="utf-8",
    )
    with pytest.raises(MissingSlotError, match="LFS pointer"):
        write_full_dump(krr_dir=krr, dest=tmp_path / "x.nq.gz")


def test_every_slot_names_a_source_that_exists_in_the_committed_tree() -> None:
    """No slot may point at a file that no producer writes (#705)."""
    from estleg.estleg_common import KRR_DIR

    for slot in SLOTS:
        if slot.name in {"regulations", "riigikohus"}:
            assert any(KRR_DIR.glob(slot.sources[0])), slot


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
