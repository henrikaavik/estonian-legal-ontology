"""T-Box label collision: 7 IRIs must live only in the CV (#359).

``controlled_vocabulary.jsonld`` is the canonical T-Box. The concept
extractor used to re-declare the same 7 class/property IRIs in
``concepts_combined.jsonld`` with different ``rdfs:label`` strings
(bilingual CV vs Estonian-only). Declaration nodes belong in the CV;
instance ``estleg:Concept_*`` / ``estleg:LegalConcept_*`` individuals stay.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_legal_concepts import CV_OWNED_TBOX_IDS, generate_schema_nodes

REPO_ROOT = Path(__file__).resolve().parent.parent
VOCAB = REPO_ROOT / "krr_outputs" / "controlled_vocabulary.jsonld"
CONCEPTS = REPO_ROOT / "krr_outputs" / "concepts" / "concepts_combined.jsonld"
EXTRACTOR = REPO_ROOT / "scripts" / "extract_legal_concepts.py"

TBOX_IDS = (
    "estleg:Concept",
    "estleg:LegalConcept",
    "estleg:definedIn",
    "estleg:definesConcept",
    "estleg:definitionCount",
    "estleg:definitionVariantCount",
    "estleg:hasDefinitionNode",
)


def _graph_ids(path: Path) -> set[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {
        n["@id"]
        for n in doc.get("@graph", [])
        if isinstance(n, dict) and isinstance(n.get("@id"), str)
    }


def test_tbox_ids_exist_in_controlled_vocabulary():
    ids = _graph_ids(VOCAB)
    missing = [iri for iri in TBOX_IDS if iri not in ids]
    assert not missing, f"missing from controlled_vocabulary.jsonld: {missing}"


def test_tbox_ids_absent_from_concepts_combined():
    ids = _graph_ids(CONCEPTS)
    leaked = [iri for iri in TBOX_IDS if iri in ids]
    assert not leaked, f"T-Box declarations still in concepts_combined.jsonld: {leaked}"


def test_generate_schema_nodes_omits_cv_tbox():
    nodes = generate_schema_nodes(total_concepts=0)
    ids = {n.get("@id") for n in nodes}
    leaked = sorted(ids & set(TBOX_IDS))
    assert not leaked, leaked
    assert set(TBOX_IDS) == set(CV_OWNED_TBOX_IDS)


def test_extractor_source_has_no_concept_class_declaration():
    """schema_nodes builder must not hard-code the CV class declaration."""
    src = EXTRACTOR.read_text(encoding="utf-8")
    assert '"@id": "estleg:Concept"' not in src
