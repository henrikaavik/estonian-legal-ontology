"""In-band VoID/DCAT Dataset heads on combined JSON-LD (#517).

The standalone descriptor lives in ``krr_outputs/void.ttl`` (see
``test_void_descriptor.py``). This module covers the in-band half: every
real (non-LFS-pointer) combined ``*.jsonld`` must carry a Dataset-typed
or licensed ``@graph`` head, and the official header helper / shared
context must advertise ``void`` + ``dcat``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import estleg_common

REPO = Path(__file__).resolve().parent.parent
KRR = REPO / "krr_outputs"
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

COMBINED_RELPATHS = tuple(
    spec["relpath"] for spec in estleg_common.COMBINED_JSONLD_TARGETS
)


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().startswith(LFS_POINTER_PREFIX)
    except OSError:
        return False


def _type_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _has_dataset_type(node: dict) -> bool:
    types = _type_list(node.get("@type"))
    return "void:Dataset" in types or "dcat:Dataset" in types


def _load_combined_head(path: Path) -> dict:
    """Parse ``@context`` plus ``@graph[0]`` from a pretty-printed JSON-LD file.

    Combined artifacts can be hundreds of megabytes. After the #517 stamp the
    Dataset head is always ``@graph[0]``, so tests do not need the rest.
    """
    chunks: list[str] = []
    seen_graph = False
    started_first = False
    depth = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            chunks.append(line)
            if not seen_graph:
                if '"@graph"' in line:
                    seen_graph = True
                continue
            if not started_first:
                if "{" in line:
                    started_first = True
                    depth += line.count("{") - line.count("}")
                    if depth <= 0:
                        break
                continue
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                break
    blob = "".join(chunks).rstrip()
    blob = blob.removesuffix(",")
    return json.loads(blob + "\n  ]\n}\n")


def test_combined_ontology_header_types_include_void_dataset() -> None:
    assert "void:Dataset" in estleg_common.combined_ontology_header()["@type"]


def test_context_has_void_and_dcat() -> None:
    assert "void" in estleg_common.CONTEXT
    assert "dcat" in estleg_common.CONTEXT
    assert estleg_common.CONTEXT["void"] == "http://rdfs.org/ns/void#"
    assert estleg_common.CONTEXT["dcat"] == "http://www.w3.org/ns/dcat#"


def test_combined_ontology_header_omits_example_resource() -> None:
    header = estleg_common.combined_ontology_header()
    assert "void:exampleResource" not in header


def test_stamp_flagship_replaces_existing_ontology_iri() -> None:
    official = estleg_common.combined_ontology_header()
    doc = {
        "@context": {"estleg": estleg_common.NS},
        "@graph": [
            {
                "@id": estleg_common.ONTOLOGY_IRI,
                "@type": ["owl:Ontology"],
                "rdfs:label": "stale",
            },
            {"@id": "estleg:A"},
        ],
    }
    estleg_common.stamp_combined_dataset_head(doc, flagship=True)
    assert doc["@graph"][0] == official
    assert doc["@graph"][1]["@id"] == "estleg:A"
    assert doc["@context"]["void"] == estleg_common.CONTEXT["void"]
    assert doc["@context"]["dcat"] == estleg_common.CONTEXT["dcat"]


def test_stamp_upgrades_existing_ontology_head() -> None:
    doc = {
        "@context": dict(estleg_common.CONTEXT),
        "@graph": [
            {
                "@id": "estleg:EURlex_Combined_Map_2026",
                "@type": ["owl:Ontology"],
                "rdfs:label": "keep-me-unless-relabelled",
                "dc:source": "EUR-Lex",
            },
            {"@id": "estleg:EU_1"},
        ],
    }
    estleg_common.stamp_combined_dataset_head(
        doc, label="Estonian Legal Ontology — EUR-Lex combined"
    )
    head = doc["@graph"][0]
    assert head["@id"] == "estleg:EURlex_Combined_Map_2026"
    assert "void:Dataset" in head["@type"]
    assert "dcat:Dataset" in head["@type"]
    assert head["dcterms:license"]["@id"].endswith("/by/4.0/")
    assert head["dcterms:publisher"]["@id"] == "https://github.com/henrikaavik"
    assert head["dc:source"] == "EUR-Lex"
    assert doc["@graph"][1]["@id"] == "estleg:EU_1"
    assert "void:exampleResource" not in head


def test_stamp_inserts_variant_when_no_head() -> None:
    doc = {
        "@context": {"estleg": estleg_common.NS},
        "@graph": [{"@id": "estleg:A_Expr_20000101"}],
    }
    ontology_id = f"{estleg_common.ONTOLOGY_IRI}/dataset/act-expressions"
    estleg_common.stamp_combined_dataset_head(
        doc,
        label="Estonian Legal Ontology — act expressions combined",
        ontology_id=ontology_id,
    )
    head = doc["@graph"][0]
    assert head["@id"] == ontology_id
    assert "void:Dataset" in head["@type"]
    assert head["rdfs:label"] == "Estonian Legal Ontology — act expressions combined"
    assert doc["@graph"][1]["@id"] == "estleg:A_Expr_20000101"


def test_stamp_is_idempotent() -> None:
    doc = {
        "@context": dict(estleg_common.CONTEXT),
        "@graph": [
            {"@id": estleg_common.ONTOLOGY_IRI, "@type": ["owl:Ontology"]},
        ],
    }
    estleg_common.stamp_combined_dataset_head(doc, flagship=True)
    first = json.dumps(doc, sort_keys=True)
    estleg_common.stamp_combined_dataset_head(doc, flagship=True)
    assert json.dumps(doc, sort_keys=True) == first


@pytest.mark.parametrize("relpath", COMBINED_RELPATHS)
def test_existing_combined_file_has_inband_dataset_head(relpath: str) -> None:
    path = KRR / relpath
    if not path.is_file():
        pytest.skip(f"{relpath} does not exist")
    if _is_lfs_pointer(path):
        pytest.skip(f"{relpath} is an LFS pointer")

    doc = _load_combined_head(path)
    graph = doc.get("@graph") or []
    assert graph, f"{relpath} has an empty @graph"
    head = graph[0]
    assert isinstance(head, dict)
    has_dataset = any(_has_dataset_type(node) for node in graph if isinstance(node, dict))
    has_license = "dcterms:license" in head
    assert has_dataset or has_license, (
        f"{relpath} @graph has no void:Dataset/dcat:Dataset node and "
        f"@graph[0] has no dcterms:license"
    )
