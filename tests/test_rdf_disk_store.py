"""The public-load disk store must preserve the existing validation results."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pyshacl
import pytest
from rdflib import BNode, Graph, Literal, Namespace
from rdflib.compare import isomorphic
from rdflib.namespace import RDF, SH, XSD

from estleg import validate_seadusloome_sync as validator
from estleg.rdf_disk_store import DiskStore

EX = Namespace("https://example.org/")


@pytest.mark.parametrize("valid", [True, False])
def test_jsonld_disk_and_memory_have_same_graph_and_shacl_results(tmp_path, valid):
    source = tmp_path / "data.jsonld"
    source.write_text(json.dumps({
        "@context": {"ex": str(EX)},
        "@graph": [
            {"@id": "_:item", "@type": "ex:Item", "ex:name": "A",
             "ex:count": 1 if valid else "bad", "ex:related": {"@id": "ex:other"}},
            {"@id": "ex:other", "ex:label": {"@value": "Tere", "@language": "et"}},
            {"@id": "ex:named", "@graph": [{"@id": "ex:hidden", "ex:name": "Hidden"}]},
        ],
    }))
    memory, errors = validator.load_graph([source, source])
    assert not errors
    disk, errors = validator.load_graph([source, source], store_dir=tmp_path / "store")
    assert not errors
    try:
        assert isomorphic(memory, disk)
        assert (EX.hidden, EX.name, None) not in disk
        shapes = Graph().parse(data=f"""
            @prefix ex: <{EX}> .
            @prefix sh: <{SH}> .
            @prefix xsd: <{XSD}> .
            ex:Shape a sh:NodeShape; sh:targetClass ex:Item;
                sh:property [sh:path ex:count; sh:datatype xsd:integer];
                sh:sparql [sh:message "Wrong name"; sh:select '''
                    SELECT $this WHERE {{ $this <{EX.name}> ?name .
                        FILTER (?name != "A") }}'''] .
        """, format="turtle")
        before = pyshacl.validate(memory, shacl_graph=shapes, inference="none")
        after = pyshacl.validate(disk, shacl_graph=shapes, inference="none")
        assert before[0] is after[0] is valid
        assert isomorphic(before[1], after[1])
        for node in disk.subjects(RDF.type, EX.Item):
            assert isinstance(node, BNode)
            assert list(disk.query(
                "SELECT ?count WHERE { ?item ex:count ?count }",
                initBindings={"item": node}, initNs={"ex": EX},
            )) == [(Literal(1) if valid else Literal("bad"),)]
    finally:
        disk.close()


def test_disk_store_preserves_lexical_values_and_uses_rdflib_queries(tmp_path):
    graph = Graph(store=DiskStore())
    graph.open(str(tmp_path / "store"), create=True)
    try:
        value = Literal("01", datatype=XSD.integer, normalize=False)
        graph.add((EX.subject, EX.value, value))
        graph.add((EX.subject, EX.name, Literal("same")))
        graph.add((EX.subject, EX.name, Literal("same", datatype=XSD.string)))
        assert len(list(graph.objects(EX.subject, EX.name))) == 2
        assert graph.value(EX.subject, EX.value) == value
        assert str(graph.value(EX.subject, EX.value)) == "01"
        # A native query call would bypass our RDFLib evaluator policy.
        with pytest.raises(NotImplementedError):
            graph.store.query("ASK {}")
        assert bool(graph.query("ASK { <https://example.org/subject> ?p ?o }"))
        assert list(graph.subjects(EX.value, value)) == [EX.subject]
        assert len(list(graph.store.contexts((EX.subject, EX.value, value)))) == 1
        graph.remove((EX.subject, EX.value, value))
        assert graph.value(EX.subject, EX.value) is None
        graph.addN([(EX.subject, EX.value, value, graph)])
        assert graph.value(EX.subject, EX.value) == value
    finally:
        graph.close()
    with pytest.raises(ValueError, match="already exist"):
        validator.load_graph([], store_dir=tmp_path / "store")


def test_disk_loader_reports_parse_errors(tmp_path):
    broken = tmp_path / "broken.jsonld"
    broken.write_text("{ broken")
    graph, errors = validator.load_graph([broken], store_dir=tmp_path / "store")
    try:
        assert len(errors) == 1
        assert errors[0][0] == str(broken)
    finally:
        graph.close()


@pytest.mark.parametrize("fail", [False, True])
def test_main_removes_temporary_store_even_if_validation_raises(tmp_path, monkeypatch, fail):
    source = tmp_path / "combined_ontology.jsonld"
    source.write_text('{"@id":"https://example.org/item"}')
    monkeypatch.setattr(validator, "collect_inputs", lambda _: ([source], [], []))
    monkeypatch.setattr(validator, "validate_graph_closure", lambda *a, **kw: {"total": 0})
    temporary_paths = []

    def temporary_dir(**kwargs):
        directory = TemporaryDirectory(dir=tmp_path, **kwargs)
        temporary_paths.append(Path(directory.name))
        return directory

    def validate(graph, args):
        assert isinstance(graph.store, DiskStore)
        if fail:
            raise RuntimeError("validation failed")
        return 0

    monkeypatch.setattr(validator, "TemporaryDirectory", temporary_dir)
    monkeypatch.setattr(validator, "_validate_loaded_graph", validate)
    if fail:
        with pytest.raises(RuntimeError, match="validation failed"):
            validator.main(["--krr-dir", str(tmp_path)])
    else:
        assert validator.main(["--krr-dir", str(tmp_path)]) == 0
    assert temporary_paths
    assert all(not path.exists() for path in temporary_paths)
