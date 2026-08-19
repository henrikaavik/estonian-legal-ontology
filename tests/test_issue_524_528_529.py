"""#524 embeddings surface, #528 ratification shells, #529 otsus/seadlus."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.generate_embedding_index import (
    HashingEmbedder,
    build_index,
    cosine,
    semantic_search,
)
from estleg.generate_rt_act_kinds import KIND_OTSUS, KIND_SEADLUS, retarget_act_kind
from estleg.index_body_coverage import (
    is_treaty_slug,
    stamp_ratification_shell,
)
REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
INDEX = REPO / "krr_outputs" / "INDEX.json"
SCHEMA_REF = REPO / "docs" / "SCHEMA_REFERENCE.md"
PYPROJECT = REPO / "pyproject.toml"
COTONOU = (
    REPO
    / "krr_outputs"
    / "cotonou_lepingu_muutmislepingu_ratifitseerimise_seadus_peep.json"
)
VERSIONS = REPO / "krr_outputs" / "provision_versions" / "perekonnaseadus.jsonld"
RESOLUTIONS = REPO / "krr_outputs" / "resolutions" / "RESOLUTIONS_INDEX.json"


def _vocab() -> dict[str, dict]:
    doc = json.loads(VOCAB.read_text(encoding="utf-8"))
    return {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }


def test_cv_has_new_528_529_terms() -> None:
    index = _vocab()
    assert index["estleg:isRatificationShell"]["rdfs:domain"]["@id"] == "estleg:Act"
    assert index["estleg:ParliamentaryResolution"]["rdfs:subClassOf"]["@id"] == (
        "estleg:Act"
    )
    assert index["estleg:PresidentialDecree"]["rdfs:subClassOf"]["@id"] == "estleg:Act"
    text = SCHEMA_REF.read_text(encoding="utf-8")
    assert "isRatificationShell" in text
    assert "ParliamentaryResolution" in text
    assert "välisleping" in text


def test_treaty_slug_and_shell_stamp() -> None:
    assert is_treaty_slug("cotonou_lepingu_muutmislepingu_ratifitseerimise_seadus")
    assert not is_treaty_slug("perekonnaseadus")
    node: dict = {"@id": "estleg:X_Map"}
    assert stamp_ratification_shell(node) is True
    assert node["estleg:isRatificationShell"]["@value"] == "true"
    assert "välisleping" in node["estleg:contentStatusReason"]
    assert stamp_ratification_shell(node) is False


def test_committed_treaty_peep_is_ratification_shell() -> None:
    doc = json.loads(COTONOU.read_text(encoding="utf-8"))
    root = doc["@graph"][0]
    assert root["estleg:isRatificationShell"]["@value"] == "true"
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    treaty = [
        law
        for law in index.get("laws") or []
        if isinstance(law, dict) and law.get("stubKind") == "treaty"
    ]
    assert len(treaty) >= 300


def test_retarget_act_kind_drops_law() -> None:
    doc = {
        "@graph": [
            {
                "@id": "estleg:X_Map",
                "@type": ["estleg:Act", "estleg:Law"],
            }
        ]
    }
    retarget_act_kind(doc, KIND_OTSUS["type_id"])
    assert doc["@graph"][0]["@type"] == [
        "estleg:Act",
        "estleg:ParliamentaryResolution",
    ]
    retarget_act_kind(doc, KIND_SEADLUS["type_id"])
    assert "estleg:PresidentialDecree" in doc["@graph"][0]["@type"]


def test_hashing_embedder_is_deterministic_and_searchable() -> None:
    embedder = HashingEmbedder(dim=32)
    first = embedder.encode(["whistleblower protection in the public sector"])
    second = embedder.encode(["whistleblower protection in the public sector"])
    assert first == second
    records = build_index(
        [
            {
                "id": "estleg:A",
                "text": "whistleblower protection and disclosure of corruption",
                "rt_url": "https://www.riigiteataja.ee/akt/1",
            },
            {
                "id": "estleg:B",
                "text": "customs tariff schedule for imported grain",
                "rt_url": "https://www.riigiteataja.ee/akt/2",
            },
        ],
        embedder,
    )
    query = embedder.encode(["whistleblower protection"])[0]
    hits = semantic_search(query, records, top_k=1)
    assert hits[0]["id"] == "estleg:A"
    assert hits[0]["rt_url"].endswith("/akt/1")
    assert cosine(records[0]["vector"], records[0]["vector"]) > 0.99


def test_pyproject_declares_embeddings_extra() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert "sentence-transformers" in text
    assert "embeddings" in text


def test_resolutions_index_lists_otsus_and_seadlus() -> None:
    index = json.loads(RESOLUTIONS.read_text(encoding="utf-8"))
    otsus = index["kinds"]["otsus"]
    seadlus = index["kinds"]["seadlus"]
    assert otsus["rt_total"] >= 200
    assert seadlus["rt_total"] >= 50
    assert otsus["type"] == "estleg:ParliamentaryResolution"
    assert seadlus["type"] == "estleg:PresidentialDecree"
    peeps = list((REPO / "krr_outputs" / "resolutions").glob("*_peep.json"))
    assert len(peeps) >= 10
    sample = json.loads(peeps[0].read_text(encoding="utf-8"))["@graph"][0]
    types = sample["@type"]
    assert "estleg:Law" not in types
    assert "estleg:ParliamentaryResolution" in types or "estleg:PresidentialDecree" in types


def test_provision_version_is_still_self_citing() -> None:
    """#524 (a) already reminted — keep the citation fields."""
    doc = json.loads(VERSIONS.read_text(encoding="utf-8"))
    versions = [
        node
        for node in doc.get("@graph") or []
        if isinstance(node, dict)
        and "estleg:ProvisionVersion"
        in (
            node.get("@type")
            if isinstance(node.get("@type"), list)
            else [node.get("@type")]
        )
    ]
    assert versions
    node = versions[0]
    assert str(node.get("estleg:rtUrl", "")).startswith("https://www.riigiteataja.ee/akt/")
    assert node.get("estleg:sourceAct")
    assert node.get("estleg:provisionRef")
