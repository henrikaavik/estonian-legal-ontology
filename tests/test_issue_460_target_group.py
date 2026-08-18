"""#460 — estleg:targetGroup values are TargetGroup IRIs, not string tokens."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from classify_target_group import (  # noqa: E402
    classify_files,
    classify_node,
    normalize_target_group_value,
    target_group_iri,
    upgrade_node_target_group_iris,
)

REPO = Path(__file__).resolve().parent.parent
ABIPOL_PEEP = REPO / "krr_outputs" / "abipolitseiniku_seadus_peep.json"
SHAPES = REPO / "shacl" / "estonian_legal_shapes.ttl"

CITIZEN_IRI = "estleg:TargetGroup_Citizen"
BUSINESS_IRI = "estleg:TargetGroup_Business"


def _target_group_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        iri = value.get("@id")
        return [iri] if isinstance(iri, str) else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_target_group_values(item))
        return out
    return []


def test_target_group_iri_citizen():
    assert target_group_iri("citizen") == CITIZEN_IRI
    assert target_group_iri("business") == BUSINESS_IRI
    assert target_group_iri(CITIZEN_IRI) == CITIZEN_IRI


def test_normalize_upgrades_legacy_strings_and_is_idempotent_on_iris():
    assert normalize_target_group_value(["citizen", "business"]) == [
        CITIZEN_IRI,
        BUSINESS_IRI,
    ]
    already = [CITIZEN_IRI, BUSINESS_IRI]
    assert normalize_target_group_value(already) == already
    assert normalize_target_group_value([{"@id": CITIZEN_IRI}]) == [CITIZEN_IRI]


def test_classifier_emit_does_not_write_business_token(tmp_path):
    peep = tmp_path / "biz_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:TEST_Par_1",
                        "estleg:paragrahv": "§ 1",
                        "estleg:summary": "Tööandja peab pidama arvestust.",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    classify_files([peep], report_path=tmp_path / "target_group_report.json")
    doc = json.loads(peep.read_text(encoding="utf-8"))
    values = _target_group_values(doc["@graph"][0]["estleg:targetGroup"])
    assert "business" not in values
    assert BUSINESS_IRI in values


def test_alcohol_handler_is_not_citizen_only():
    node = {
        "estleg:summary": (
            "Alkoholikäitleja on isik, kes tegeleb alkoholi käitlemisega. "
            "Alkoholikäitleja ja ettevõtja peavad täitma arvestuskohustust."
        ),
    }
    groups, _ = classify_node(node)
    assert "business" in groups
    assert groups != ["citizen"]
    assert "citizen" not in groups


def test_upgrade_node_replaces_tokens_with_iri_objects():
    node = {"estleg:targetGroup": ["citizen", "business"]}
    assert upgrade_node_target_group_iris(node) is True
    assert node["estleg:targetGroup"] == [
        {"@id": CITIZEN_IRI},
        {"@id": BUSINESS_IRI},
    ]
    assert upgrade_node_target_group_iris(node) is False


def test_abipolitseiniku_target_group_values_are_iris():
    doc = json.loads(ABIPOL_PEEP.read_text(encoding="utf-8"))
    seen = 0
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        values = _target_group_values(node.get("estleg:targetGroup"))
        for value in values:
            assert value.startswith("estleg:TargetGroup_"), value
            assert value != "citizen"
        assert "citizen" not in values
        seen += len(values)
    assert seen > 0


def test_shacl_target_group_path_is_iri_enum():
    text = SHAPES.read_text(encoding="utf-8")
    start = text.index("sh:path estleg:targetGroup ;")
    end = text.index("] ;", start)
    block = text[start:end]
    assert "sh:nodeKind sh:IRI" in block
    assert "xsd:string" not in block
    assert "estleg:TargetGroup_Citizen" in block
    assert '"citizen"' not in block


def test_stream_upgrade_rewrites_pretty_printed_array(tmp_path):
    src = tmp_path / "combined_fragment.jsonld"
    src.write_text(
        '{\n  "@graph": [\n    {\n      "estleg:targetGroup": [\n'
        '        "citizen",\n        "business"\n      ]\n    }\n  ]\n}\n',
        encoding="utf-8",
    )
    from classify_target_group import upgrade_jsonld_target_group_stream

    stats = upgrade_jsonld_target_group_stream(src)
    assert stats["tokens_replaced"] == 2
    text = src.read_text(encoding="utf-8")
    assert '"citizen"' not in text
    assert '"@id": "estleg:TargetGroup_Citizen"' in text
    assert '"@id": "estleg:TargetGroup_Business"' in text
    assert upgrade_jsonld_target_group_stream(src)["tokens_replaced"] == 0


def test_combined_target_group_values_are_iris_when_materialized():
    path = REPO / "krr_outputs" / "combined_ontology.jsonld"
    if not path.is_file():
        import pytest

        pytest.skip("combined_ontology.jsonld missing")
    first = path.read_text(encoding="utf-8", errors="replace")[:80]
    if first.startswith("version https://git-lfs.github.com/spec/v1"):
        import pytest

        pytest.skip("combined_ontology.jsonld is an LFS pointer")
    leftover = 0
    iris = 0
    in_tg = False
    token_re = re.compile(
        r'^\s*"(citizen|business|public_body|official|ngo)"\s*,?\s*$'
    )
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.lstrip()
            if not in_tg:
                if stripped.startswith('"estleg:targetGroup"'):
                    in_tg = True
                continue
            if stripped.startswith("]"):
                in_tg = False
                continue
            if token_re.match(line):
                leftover += 1
            elif "estleg:TargetGroup_" in line:
                iris += 1
    assert leftover == 0
    assert iris > 0
