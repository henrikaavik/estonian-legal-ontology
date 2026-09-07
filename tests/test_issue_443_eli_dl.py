"""#443 — ELI-DL v3 T-Box bridges for EIS drafts.

Subclass axiom only (no extra @type on the 22k draft peeps). Phase individuals
are NamedIndividuals, so they are typed eli-dl:ProcessStage rather than
subclassed. Shared estleg_common.CONTEXT must not grow an unused eli-dl prefix.
"""

from __future__ import annotations

import json
from pathlib import Path

from estleg.estleg_common import CONTEXT
from estleg.generate_draft_legislation import (
    ELI_DL_NS,
    ELI_DL_SCHEMA_CONTEXT,
    generate_schema_nodes,
)

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
EELNOUD_SCHEMA = REPO / "krr_outputs" / "eelnoud" / "eelnoud_schema.json"
SCHEMA_REF = REPO / "docs" / "SCHEMA_REFERENCE.md"

ELI_DL_URI = "http://data.europa.eu/eli/eli-draft-legislation-ontology#"
MAIN_PHASES = (
    "estleg:Phase_PublicConsultation",
    "estleg:Phase_Review",
    "estleg:Phase_Submission",
    "estleg:Phase_Enacted",
    "estleg:Phase_Withdrawn",
    "estleg:Phase_Rejected",
)
ELI_DL_STAGE_IRIS = frozenset(
    {
        "eli-dl:ProcessStage",
        "eli-dl:LegislativeActivity",
    }
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _index(doc: dict) -> dict[str, dict]:
    return {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }


def _has_type(node: dict, owl_type: str) -> bool:
    raw = node.get("@type")
    if isinstance(raw, list):
        return owl_type in raw
    return raw == owl_type


def _as_ids(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        ids: set[str] = set()
        iri = value.get("@id")
        if isinstance(iri, str):
            ids.add(iri)
        for item in value.values():
            ids |= _as_ids(item)
        return ids
    if isinstance(value, list):
        ids = set()
        for item in value:
            ids |= _as_ids(item)
        return ids
    return set()


def _literal_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("@value")
        return inner if isinstance(inner, str) else ""
    if isinstance(value, list):
        return " ".join(_literal_text(item) for item in value)
    return ""


def _linked_to_eli_dl_stage(node: dict) -> bool:
    raw = node.get("@type")
    type_ids = set(raw if isinstance(raw, list) else [raw] if raw else [])
    if type_ids & ELI_DL_STAGE_IRIS:
        return True
    related = (
        _as_ids(node.get("rdfs:subClassOf"))
        | _as_ids(node.get("rdfs:seeAlso"))
        | _as_ids(node.get("owl:sameAs"))
    )
    return bool(related & ELI_DL_STAGE_IRIS)


def test_cv_context_declares_official_eli_dl_namespace() -> None:
    ctx = _load(VOCAB)["@context"]
    assert ctx["eli-dl"] == ELI_DL_URI


def test_draft_legislation_subclass_of_draft_legislation_work() -> None:
    node = _index(_load(VOCAB))["estleg:DraftLegislation"]
    assert _has_type(node, "owl:Class")
    assert "eli-dl:DraftLegislationWork" in _as_ids(node.get("rdfs:subClassOf"))
    assert node.get("rdfs:label")
    assert node.get("rdfs:comment")


def test_main_phase_individuals_link_to_eli_dl_stage() -> None:
    idx = _index(_load(VOCAB))
    for iri in MAIN_PHASES:
        node = idx.get(iri)
        assert node is not None, f"{iri} missing from controlled_vocabulary.jsonld"
        assert _has_type(node, "owl:NamedIndividual"), iri
        assert _has_type(node, "estleg:LegislativePhase"), iri
        assert _linked_to_eli_dl_stage(node), iri


def test_eelnoud_schema_declares_eli_dl_and_subclass() -> None:
    doc = _load(EELNOUD_SCHEMA)
    assert doc["@context"]["eli-dl"] == ELI_DL_URI
    node = _index(doc)["estleg:DraftLegislation"]
    assert "eli-dl:DraftLegislationWork" in _as_ids(node.get("rdfs:subClassOf"))
    comment = _literal_text(node.get("rdfs:comment"))
    assert "DraftLegislationWork" in comment
    for iri in MAIN_PHASES:
        assert _linked_to_eli_dl_stage(_index(doc)[iri]), iri


def test_generator_schema_nodes_stay_aligned() -> None:
    nodes = {
        node["@id"]: node
        for node in generate_schema_nodes()
        if isinstance(node, dict) and node.get("@id")
    }
    draft = nodes["estleg:DraftLegislation"]
    assert "eli-dl:DraftLegislationWork" in _as_ids(draft.get("rdfs:subClassOf"))
    assert "DraftLegislationWork" in _literal_text(draft.get("rdfs:comment"))
    for iri in MAIN_PHASES:
        assert _linked_to_eli_dl_stage(nodes[iri]), iri


def test_eli_dl_prefix_not_injected_into_shared_peep_context() -> None:
    assert "eli-dl" not in CONTEXT
    assert ELI_DL_NS == ELI_DL_URI
    assert ELI_DL_SCHEMA_CONTEXT["eli-dl"] == ELI_DL_URI
    assert "eli-dl" not in CONTEXT


def test_schema_reference_notes_eli_dl_alignment() -> None:
    text = SCHEMA_REF.read_text(encoding="utf-8")
    assert "eli-dl:DraftLegislationWork" in text
    assert ELI_DL_URI in text
    assert "#443" in text
