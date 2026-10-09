"""#701: extraction-gap booleans use coverage vocabulary and carry provenance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdflib import Graph, Namespace

from estleg.estleg_common import BUILD_EVALUATION_DATE
from estleg.generate_analytical_overlay import (
    COVERAGE_AS_OF_KEY,
    COVERAGE_FLAG_METHOD,
    COVERAGE_METHOD_KEY,
    LEGACY_FLAG_KEYS,
    NO_AUTHORITY_FLAG,
    NO_TRANSPOSITION_FLAG,
    build_analytical_overlay,
    mark_coverage_gap,
    patch_combined_analytical,
    plan_analytical_updates,
    stamp_analytical_properties,
    sync_analytical_node,
)

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
SHAPES = REPO / "shacl" / "estonian_legal_shapes.ttl"
OVERLAY = REPO / "krr_outputs" / "analytical" / "analytical_overlay.jsonld"
FIX_ALL = REPO / "src" / "estleg" / "fix_all_issues.py"
LFS_PREFIX = "version https://git-lfs.github.com/spec/v1"
ESTLEG = Namespace("https://w3id.org/estleg/")
SH = Namespace("http://www.w3.org/ns/shacl#")

TRUE = {"@value": "true", "@type": "xsd:boolean"}
AS_OF = {"@value": BUILD_EVALUATION_DATE, "@type": "xsd:date"}


def _nodes() -> list[dict]:
    return [
        {"@id": "estleg:TMS_Map", "@type": ["estleg:Act", "estleg:Law"]},
        {
            "@id": "estleg:PKS_Map",
            "@type": ["estleg:Act"],
            "estleg:competentAuthority": [{"@id": "estleg:Institution_x"}],
        },
        {
            "@id": "estleg:EU_32000L0060",
            "@type": ["estleg:EULegislation"],
            "estleg:inForce": TRUE,
        },
        {
            "@id": "estleg:EU_32016L0679",
            "@type": ["estleg:EULegislation"],
            "estleg:inForce": TRUE,
            "estleg:transposedBy": [{"@id": "estleg:PKS_Map"}],
        },
    ]


def _assert_stamped(props: dict, flag: str) -> None:
    assert props[flag] == TRUE
    assert props[COVERAGE_METHOD_KEY] == COVERAGE_FLAG_METHOD
    assert props[COVERAGE_AS_OF_KEY] == AS_OF


def test_plan_emits_coverage_names_with_provenance() -> None:
    planned = plan_analytical_updates(_nodes())
    _assert_stamped(planned["estleg:TMS_Map"], NO_AUTHORITY_FLAG)
    _assert_stamped(planned["estleg:EU_32000L0060"], NO_TRANSPOSITION_FLAG)
    assert COVERAGE_METHOD_KEY not in planned["estleg:PKS_Map"]
    assert "estleg:EU_32016L0679" not in planned
    for props in planned.values():
        assert not set(LEGACY_FLAG_KEYS) & set(props)


def test_overlay_never_emits_legacy_names() -> None:
    doc = build_analytical_overlay(
        _nodes(),
        similarity_pairs=[],
        in_force_directives={"estleg:EU_31990L0314"},
    )
    by_id = {node["@id"]: node for node in doc["@graph"]}
    _assert_stamped(by_id["estleg:EU_31990L0314"], NO_TRANSPOSITION_FLAG)
    text = json.dumps(doc)
    for legacy in LEGACY_FLAG_KEYS:
        assert legacy not in text
    header = by_id["estleg:AnalyticalOverlay"]["rdfs:comment"]
    assert "not a legal finding" in header


def test_mark_coverage_gap_rejects_non_flag() -> None:
    with pytest.raises(ValueError):
        mark_coverage_gap({}, "estleg:hasNoTransposition")


def test_sync_strips_legacy_and_closed_flags() -> None:
    node = {
        "@id": "estleg:X",
        "estleg:hasNoTransposition": TRUE,
        NO_AUTHORITY_FLAG: TRUE,
        COVERAGE_METHOD_KEY: "old",
        "estleg:inboundCitationCount": 3,
    }
    assert sync_analytical_node(node, None)
    assert node == {"@id": "estleg:X", "estleg:inboundCitationCount": 3}
    assert not sync_analytical_node(node, None)


def test_stamp_in_memory_replaces_legacy_flag() -> None:
    nodes = _nodes()
    nodes[0]["estleg:hasNoCompetentAuthority"] = TRUE
    stamp_analytical_properties(nodes)
    assert "estleg:hasNoCompetentAuthority" not in nodes[0]
    _assert_stamped(nodes[0], NO_AUTHORITY_FLAG)


def test_patch_combined_restamps_and_is_idempotent(tmp_path: Path) -> None:
    nodes = _nodes()
    nodes[0]["estleg:hasNoCompetentAuthority"] = TRUE
    nodes[2]["estleg:hasNoTransposition"] = TRUE
    # Transposed directive with a stale legacy flag: only a removal is planned.
    nodes[3]["estleg:hasNoTransposition"] = TRUE
    combined = tmp_path / "combined_ontology.jsonld"
    combined.write_text(
        json.dumps({"@context": {}, "@graph": nodes}, indent=2) + "\n",
        encoding="utf-8",
    )
    assert patch_combined_analytical(combined) == 4
    by_id = {
        node["@id"]: node
        for node in json.loads(combined.read_text(encoding="utf-8"))["@graph"]
    }
    _assert_stamped(by_id["estleg:TMS_Map"], NO_AUTHORITY_FLAG)
    _assert_stamped(by_id["estleg:EU_32000L0060"], NO_TRANSPOSITION_FLAG)
    for node in by_id.values():
        assert not set(LEGACY_FLAG_KEYS) & set(node)
    first = combined.read_bytes()
    assert patch_combined_analytical(combined) == 0
    assert combined.read_bytes() == first


def _vocab() -> dict[str, dict]:
    doc = json.loads(VOCAB.read_text(encoding="utf-8"))
    return {node["@id"]: node for node in doc["@graph"] if "@id" in node}


def _labels(node: dict) -> dict[str, str]:
    return {item["@language"]: item["@value"] for item in node["rdfs:label"]}


def test_cv_declares_coverage_terms_and_deprecates_legacy() -> None:
    index = _vocab()
    for term in (NO_TRANSPOSITION_FLAG, NO_AUTHORITY_FLAG):
        node = index[term]
        assert node["rdfs:range"]["@id"] == "xsd:boolean"
        assert set(_labels(node)) == {"et", "en"}
        comments = {item["@language"]: item["@value"] for item in node["rdfs:comment"]}
        assert "NOT a legal finding" in comments["en"]
        assert "MITTE õiguslik järeldus" in comments["et"]
        assert not node.get("owl:deprecated")
    assert index[COVERAGE_METHOD_KEY]["rdfs:range"]["@id"] == "xsd:string"
    assert index[COVERAGE_AS_OF_KEY]["rdfs:range"]["@id"] == "xsd:date"
    replacements = dict(
        zip(LEGACY_FLAG_KEYS, (NO_TRANSPOSITION_FLAG, NO_AUTHORITY_FLAG))
    )
    for legacy, replacement in replacements.items():
        node = index[legacy]
        assert node["owl:deprecated"] is True
        assert node["dcterms:isReplacedBy"]["@id"] == replacement
        assert replacement in node["rdfs:comment"]


def test_shapes_use_coverage_names() -> None:
    graph = Graph()
    graph.parse(SHAPES)
    paths = set(graph.objects(None, SH.path))
    for local in (
        "noTranspositionEdgeInCorpus",
        "competentAuthorityNotExtracted",
        "coverageFlagMethod",
        "coverageFlagAsOf",
    ):
        assert ESTLEG[local] in paths
    for local in ("hasNoTransposition", "hasNoCompetentAuthority"):
        assert ESTLEG[local] not in paths


def test_combined_builder_stamps_through_this_module() -> None:
    source = FIX_ALL.read_text(encoding="utf-8")
    assert (
        "from estleg.generate_analytical_overlay import stamp_analytical_properties"
        in source
    )


def test_committed_overlay_uses_coverage_names() -> None:
    if not OVERLAY.is_file():
        pytest.skip("analytical overlay missing")
    text = OVERLAY.read_text(encoding="utf-8")
    if text.startswith(LFS_PREFIX):
        pytest.skip("analytical overlay is an LFS pointer")
    for legacy in LEGACY_FLAG_KEYS:
        assert f'"{legacy}"' not in text
    flagged = 0
    for node in json.loads(text)["@graph"]:
        if NO_TRANSPOSITION_FLAG in node or NO_AUTHORITY_FLAG in node:
            flagged += 1
            assert node[COVERAGE_METHOD_KEY] == COVERAGE_FLAG_METHOD
            assert node[COVERAGE_AS_OF_KEY]["@type"] == "xsd:date"
    assert flagged


def test_item_number_comment_names_alampunkt() -> None:
    comment = _vocab()["estleg:itemNumber"]["rdfs:comment"]
    assert "alampunktNr" in comment
    assert "from the Riigi Teataja punktNr elements" not in comment


def test_remint_combined_strips_deprecated_flag(tmp_path: Path) -> None:
    from estleg.materialize_combined_inverses import remint_combined_520_521

    nodes = _nodes()
    nodes[0]["estleg:hasNoCompetentAuthority"] = TRUE
    # PKS_Map has an authority, so a stale legacy flag there is a closed gap.
    nodes[1]["estleg:hasNoCompetentAuthority"] = TRUE
    combined = tmp_path / "combined_ontology.jsonld"
    combined.write_text(
        json.dumps({"@context": {}, "@graph": nodes}, indent=2) + "\n",
        encoding="utf-8",
    )
    remint_combined_520_521(combined)
    by_id = {
        node["@id"]: node
        for node in json.loads(combined.read_text(encoding="utf-8"))["@graph"]
    }
    for node in by_id.values():
        assert "estleg:hasNoCompetentAuthority" not in node
    _assert_stamped(by_id["estleg:TMS_Map"], NO_AUTHORITY_FLAG)
    assert NO_AUTHORITY_FLAG not in by_id["estleg:PKS_Map"]
    assert COVERAGE_METHOD_KEY not in by_id["estleg:PKS_Map"]
    first = combined.read_bytes()
    remint_combined_520_521(combined)
    assert combined.read_bytes() == first
