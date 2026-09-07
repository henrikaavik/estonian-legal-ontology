"""#438: KarS / TsÜS OWL modules align with the canonical T-Box."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.align_owl_modules import align_combined, align_document, align_node
from estleg.consolidate_tbox import graph_nodes, load_jsonld

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
KARS = REPO / "krr_outputs" / "karistusseadustik_eriosa_owl.jsonld"
TSUS = REPO / "krr_outputs" / "tsus_osa7_138_169_owl.jsonld"


def _types(node: dict) -> list[str]:
    value = node.get("@type")
    if isinstance(value, str):
        return [value]
    return [item for item in (value or []) if isinstance(item, str)]


def test_section_is_subclass_of_legal_provision() -> None:
    idx = {n["@id"]: n for n in graph_nodes(load_jsonld(VOCAB))}
    sub = idx["estleg:Section"].get("rdfs:subClassOf")
    if isinstance(sub, dict):
        assert sub.get("@id") == "estleg:LegalProvision"
    else:
        ids = [
            item.get("@id") if isinstance(item, dict) else item for item in (sub or [])
        ]
        assert "estleg:LegalProvision" in ids


def test_legal_part_stays_distinct_from_multipart_part() -> None:
    idx = {n["@id"]: n for n in graph_nodes(load_jsonld(VOCAB))}
    assert "estleg:LegalPart" in idx
    assert "estleg:Part" in idx
    assert idx["estleg:LegalPart"].get("owl:equivalentClass") != {"@id": "estleg:Part"}


def test_kars_sections_are_legal_provisions() -> None:
    doc = load_jsonld(KARS)
    sections = [
        n for n in graph_nodes(doc) if "estleg:Section" in _types(n)
    ]
    assert len(sections) >= 400
    assert all("estleg:LegalProvision" in _types(n) for n in sections)
    classes = [
        n.get("@id")
        for n in graph_nodes(doc)
        if "owl:Class" in _types(n)
    ]
    assert not any(
        str(cid).endswith(frag)
        for cid in classes
        for frag in ("LegalPart", "Section", "Provision")
    )
    header = next(n for n in graph_nodes(doc) if n.get("@id") == "estleg:KarS_Eriosa_v1")
    source = header.get("dc:source")
    assert isinstance(source, str) and not source.startswith("http")
    dcterms = header.get("dcterms:source")
    assert isinstance(dcterms, dict) and str(dcterms.get("@id", "")).startswith("http")


def test_tsus_sections_and_part_align() -> None:
    doc = load_jsonld(TSUS)
    sections = [n for n in graph_nodes(doc) if "estleg:Section" in _types(n)]
    assert sections
    assert all("estleg:LegalProvision" in _types(n) for n in sections)
    parts = [n for n in graph_nodes(doc) if "estleg:LegalPart" in _types(n)]
    assert parts
    classes = [n.get("@id") for n in graph_nodes(doc) if "owl:Class" in _types(n)]
    assert not any(
        str(cid).endswith(frag)
        for cid in classes
        for frag in ("LegalPart", "Section", "Provision", "Chapter")
    )


def test_align_node_adds_legal_provision() -> None:
    node = {"@id": "estleg:KarS_Par_88", "@type": ["estleg:Section", "owl:NamedIndividual"]}
    assert align_node(node) is True
    assert "estleg:LegalProvision" in node["@type"]


def test_align_document_drops_local_class() -> None:
    doc = {
        "@graph": [
            {"@id": "https://w3id.org/estleg/Provision", "@type": ["owl:Class"]},
            {"@id": "estleg:X", "@type": ["estleg:LegalPart", "owl:NamedIndividual"]},
        ]
    }
    stats = align_document(doc)
    assert stats["dropped"] == 1
    assert doc["@graph"][0]["@type"] == ["estleg:LegalPart", "owl:NamedIndividual"]


def test_align_combined_preserves_unchanged_indent(tmp_path: Path) -> None:
    path = tmp_path / "combined.jsonld"
    path.write_text(
        '{\n  "@graph": [\n    {\n      "@id": "estleg:Keep",\n      "@type": ["estleg:Act"]\n    },\n'
        '    {\n      "@id": "estleg:Chapter",\n      "@type": ["owl:Class"]\n    },\n'
        '    {\n      "@id": "estleg:Sec",\n      "@type": ["estleg:Section"]\n    },\n'
        '    {\n      "@id": "https://w3id.org/estleg/Provision",\n      "@type": ["owl:Class"]\n    }\n'
        "  ]\n}\n",
        encoding="utf-8",
    )
    stats = align_combined(path)
    text = path.read_text(encoding="utf-8")
    assert stats["dropped"] == 1
    assert stats["rewritten"] == 1
    assert '    {\n      "@id": "estleg:Keep"' in text
    parsed = json.loads(text)
    ids = [n["@id"] for n in parsed["@graph"]]
    assert ids == ["estleg:Keep", "estleg:Chapter", "estleg:Sec"]
    assert "estleg:LegalProvision" in parsed["@graph"][2]["@type"]
