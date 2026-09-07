"""#459 — one annotation node per Õiguskantsler document."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from estleg.generate_annotations import (
    SIDECAR_PATH,
    classify_annotation_type,
    extract_section_numbers,
    provision_iris_for_acts,
)

REPO = Path(__file__).resolve().parent.parent
SIDECAR = REPO / "krr_outputs" / "annotations" / "oiguskantsler_seisukohad.jsonld"


def test_classify_annotation_type_from_title() -> None:
    assert classify_annotation_type("Soovitus andmekaitse kohta") == "guidance"
    assert classify_annotation_type("Ettepanek Riigikogule") == "guidance"
    assert classify_annotation_type("Hoiatus tööandjatele") == "warning"
    assert classify_annotation_type("Kommentaar karistusseadustiku kohta") == "commentary"
    assert classify_annotation_type("Selgitus menetluskorrast") == "practice_note"
    assert classify_annotation_type("Maa maksu põhiseaduspärasus") == "interpretation"


def test_provision_iris_prefer_cited_sections() -> None:
    acts = ["estleg:VOS_Map"]
    known = {"estleg:VOS_Par_40", "estleg:VOS_Par_41"}
    assert provision_iris_for_acts(acts, "Võlaõigusseaduse § 40 tõlgendamine", known) == [
        "estleg:VOS_Par_40"
    ]
    assert provision_iris_for_acts(acts, "üldine seisukoht", known) == acts
    assert extract_section_numbers("§ 12 ja § 381¹") == ["12", "381_1"]


def _annotation_nodes() -> list[dict]:
    doc = json.loads(SIDECAR.read_text(encoding="utf-8"))
    nodes = []
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:Annotation" in types:
            nodes.append(node)
    return nodes


# LFS artifact; runs in the json-validation `-m corpus` job (#679)
@pytest.mark.corpus
def test_committed_sidecar_is_one_node_per_document() -> None:
    nodes = _annotation_nodes()
    assert 3000 <= len(nodes) <= 4500
    texts = Counter()
    urls = Counter()
    types = Counter()
    for node in nodes:
        text = node.get("estleg:annotationText") or ""
        if isinstance(text, dict):
            text = text.get("@value") or ""
        texts[text] += 1
        url = node.get("estleg:annotationSourceUrl")
        if isinstance(url, dict):
            url = url.get("@value")
        if url:
            urls[url] += 1
        types[node.get("estleg:annotationType")] += 1
    assert texts
    assert max(texts.values()) == 1
    assert urls
    assert max(urls.values()) == 1
    assert len(types) >= 3
    assert "interpretation" in types
    assert SIDECAR_PATH.name == "oiguskantsler_seisukohad.jsonld"
