"""#434: provisions are typed estleg:LegalProvision; no per-file classes."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.remint_per_document_classes import (
    CANONICAL_PROVISION_TYPE,
    is_per_doc_class_node,
    remint_document,
    rewrite_node,
)
from estleg import validate_all

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "krr_outputs" / "perekonnaseadus_peep.json"


def test_rewrite_replaces_leaf_class_with_legal_provision() -> None:
    node = {
        "@id": "estleg:PKS_Par_1",
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision_perekonnaseadus"],
        "estleg:paragrahv": "§ 1",
    }
    assert rewrite_node(node) is True
    assert "estleg:LegalProvision_perekonnaseadus" not in node["@type"]
    assert CANONICAL_PROVISION_TYPE in node["@type"]


def test_remint_drops_class_node() -> None:
    doc = {
        "@graph": [
            {
                "@id": "estleg:LegalProvision_foo",
                "@type": ["owl:Class"],
                "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
            },
            {
                "@id": "estleg:FOO_Par_1",
                "@type": ["owl:NamedIndividual", "estleg:LegalProvision_foo"],
                "estleg:paragrahv": "§ 1",
            },
        ]
    }
    dropped, rewritten = remint_document(doc)
    assert dropped == 1
    assert rewritten == 1
    ids = [node["@id"] for node in doc["@graph"]]
    assert "estleg:LegalProvision_foo" not in ids
    assert CANONICAL_PROVISION_TYPE in doc["@graph"][0]["@type"]


def test_regulation_class_rewritten_to_legal_provision() -> None:
    node = {
        "@id": "estleg:Reg_1_Par_1",
        "@type": ["owl:NamedIndividual", "estleg:Regulation_1", "estleg:KovProvision"],
        "estleg:paragrahv": "§ 1",
    }
    rewrite_node(node)
    assert "estleg:Regulation_1" not in node["@type"]
    assert CANONICAL_PROVISION_TYPE in node["@type"]
    assert "estleg:KovProvision" in node["@type"]


def test_committed_sample_has_no_per_document_class() -> None:
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert not any(is_per_doc_class_node(n) for n in doc["@graph"] if isinstance(n, dict))
    provisions = [n for n in doc["@graph"] if isinstance(n, dict) and "estleg:paragrahv" in n]
    assert provisions
    for node in provisions:
        assert CANONICAL_PROVISION_TYPE in (node.get("@type") or [])


def test_validate_per_document_classes_flags_leaf_class() -> None:
    validate_all.reset()
    validate_all.validate_per_document_classes(
        Path("x_peep.json"),
        {
            "@graph": [
                {
                    "@id": "estleg:LegalProvision_x",
                    "@type": ["owl:Class"],
                }
            ]
        },
    )
    assert validate_all.errors
