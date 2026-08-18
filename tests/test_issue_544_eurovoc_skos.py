"""#544: EuroVoc descriptors ship as a navigable SKOS ConceptScheme."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import classify_eurovoc as ev  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SKOS_PATH = REPO / "krr_outputs" / "eurovoc_concept_scheme.jsonld"
EUROVOC_PREFIX = "http://eurovoc.europa.eu/"


def _types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _pref_labels(node: dict) -> dict[str, str]:
    raw = node.get("skos:prefLabel")
    assert isinstance(raw, list), raw
    labels: dict[str, str] = {}
    for item in raw:
        assert isinstance(item, dict), item
        lang = item.get("@language")
        value = item.get("@value")
        assert isinstance(lang, str) and isinstance(value, str)
        labels[lang] = value
    return labels


def _scheme_and_concepts(doc: dict) -> tuple[dict, list[dict]]:
    graph = doc.get("@graph")
    assert isinstance(graph, list)
    schemes = [n for n in graph if isinstance(n, dict) and "skos:ConceptScheme" in _types(n)]
    concepts = [n for n in graph if isinstance(n, dict) and "skos:Concept" in _types(n)]
    assert len(schemes) == 1
    return schemes[0], concepts


def test_graph_has_one_scheme_and_n_concepts() -> None:
    doc = ev.build_eurovoc_skos_graph()
    scheme, concepts = _scheme_and_concepts(doc)
    assert scheme["@id"] == ev.EUROVOC_SKOS_SCHEME_ID
    assert scheme.get("rdfs:label")
    assert len(concepts) == len(ev.EUROVOC_DOMAINS)


def test_every_concept_id_is_eurovoc_domain_key() -> None:
    _, concepts = _scheme_and_concepts(ev.build_eurovoc_skos_graph())
    domain_ids = {f"{EUROVOC_PREFIX}{code}" for code in ev.EUROVOC_DOMAINS}
    concept_ids = {node["@id"] for node in concepts}
    assert concept_ids == domain_ids
    for nid in concept_ids:
        assert nid.startswith(EUROVOC_PREFIX)
        assert nid.removeprefix(EUROVOC_PREFIX) in ev.EUROVOC_DOMAINS


def test_every_concept_has_bilingual_preflabel() -> None:
    _, concepts = _scheme_and_concepts(ev.build_eurovoc_skos_graph())
    for node in concepts:
        labels = _pref_labels(node)
        assert set(labels) == {"et", "en"}
        assert labels["et"].strip()
        assert labels["en"].strip()
        code = node["@id"].removeprefix(EUROVOC_PREFIX)
        _slug, label_et, label_en, _kws = ev.EUROVOC_DOMAINS[code]
        assert labels["et"] == label_et
        assert labels["en"] == label_en


def test_every_concept_inscheme_points_at_scheme() -> None:
    scheme, concepts = _scheme_and_concepts(ev.build_eurovoc_skos_graph())
    scheme_id = scheme["@id"]
    top = scheme["skos:hasTopConcept"]
    assert isinstance(top, list)
    top_ids = {ref["@id"] for ref in top}
    for node in concepts:
        inscheme = node["skos:inScheme"]
        if isinstance(inscheme, dict):
            assert inscheme.get("@id") == scheme_id
        else:
            assert {"@id": scheme_id} in inscheme
        assert node["@id"] in top_ids


def test_527_is_constitutional_law_not_food_policy() -> None:
    """#421 pin: 527 is constitutional law / riigiõigus, not food policy."""
    slug, label_et, label_en, _kws = ev.EUROVOC_DOMAINS["527"]
    assert slug == "constitutional-law"
    assert label_et == "riigiõigus"
    assert label_en == "constitutional law"
    assert "food" not in label_en.lower()

    _, concepts = _scheme_and_concepts(ev.build_eurovoc_skos_graph())
    node = next(c for c in concepts if c["@id"] == f"{EUROVOC_PREFIX}527")
    labels = _pref_labels(node)
    assert labels["et"] == "riigiõigus"
    assert labels["en"] == "constitutional law"
    assert "food" not in labels["en"].lower()


def test_committed_file_matches_builder() -> None:
    built = ev.build_eurovoc_skos_graph()
    on_disk = json.loads(SKOS_PATH.read_text(encoding="utf-8"))
    assert on_disk == built


def test_context_includes_skos() -> None:
    ctx = ev.build_eurovoc_skos_graph()["@context"]
    assert ctx["skos"] == "http://www.w3.org/2004/02/skos/core#"


def test_update_law_file_eurovoc_writes_bare_iri_subjects(tmp_path: Path) -> None:
    peep = tmp_path / "law_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:X_Map_2026",
                        "@type": ["owl:Ontology"],
                        "rdfs:label": "X",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    domains = [("527", "constitutional-law", "riigiõigus", "constitutional law", 2, ["põhiseadus"])]
    assert ev.update_law_file_eurovoc(peep, domains) is True
    node = json.loads(peep.read_text(encoding="utf-8"))["@graph"][0]
    subjects = node["dcterms:subject"]
    assert subjects == [{"@id": f"{EUROVOC_PREFIX}527"}]
    for ref in subjects:
        assert set(ref) == {"@id"}
        assert "skos:prefLabel" not in ref
        assert "rdfs:label" not in ref


def test_write_skos_only_skips_classification(tmp_path: Path, monkeypatch) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    monkeypatch.setattr(ev, "KRR_DIR", krr)

    def _fail_if_walked(*_a, **_k):
        raise AssertionError("classify pass must not run under --write-skos-only")

    monkeypatch.setattr(ev, "iter_peep_files", _fail_if_walked)
    ev.main(["--write-skos-only"])
    dest = krr / ev.EUROVOC_SKOS_FILENAME
    assert dest.is_file()
    assert json.loads(dest.read_text(encoding="utf-8")) == ev.build_eurovoc_skos_graph()
    assert not (krr / "eurovoc_classification.json").exists()
