"""#520 inverse/closure materialization and #521 analytical overlay."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.estleg_common import STUB_SEMANTIC_EDGE_PREDICATES
from estleg.fix_all_issues import generate_combined_jsonld
from estleg.generate_analytical_overlay import (
    build_analytical_overlay,
    plan_analytical_updates,
    similarity_node,
)
from estleg.generate_inverse_references import (
    COMBINED_SKIP_INVERSE_PAIRS,
    INVERSE_PAIRS,
)
from estleg.materialize_combined_inverses import (
    materialize_combined_edges,
    plan_inverse_updates,
)

REPO = Path(__file__).resolve().parent.parent
VOCAB = REPO / "krr_outputs" / "controlled_vocabulary.jsonld"
SCHEMA_REF = REPO / "docs" / "SCHEMA_REFERENCE.md"
FIX_ALL = REPO / "src" / "estleg" / "fix_all_issues.py"
COMBINED = REPO / "krr_outputs" / "combined_ontology.jsonld"
OVERLAY = REPO / "krr_outputs" / "analytical" / "analytical_overlay.jsonld"
LFS_PREFIX = "version https://git-lfs.github.com/spec/v1"


def _vocab() -> dict[str, dict]:
    doc = json.loads(VOCAB.read_text(encoding="utf-8"))
    return {
        node["@id"]: node
        for node in doc.get("@graph", [])
        if isinstance(node, dict) and isinstance(node.get("@id"), str)
    }


def test_inverse_pairs_cover_transposition_authority_and_skip_versions() -> None:
    pairs = set(INVERSE_PAIRS)
    assert ("estleg:transposesDirective", "estleg:transposedBy") in pairs
    assert ("estleg:harmonisedWith", "estleg:harmonises") in pairs
    assert ("estleg:competentAuthority", "estleg:governs") in pairs
    assert ("estleg:versionOf", "estleg:hasVersion") in COMBINED_SKIP_INVERSE_PAIRS


def test_plan_inverses_adds_transposed_by_governs_and_partofact() -> None:
    nodes = [
        {
            "@id": "estleg:PKS_Map_2026",
            "@type": ["estleg:Law", "estleg:Act"],
            "estleg:transposesDirective": {"@id": "estleg:EU_32000L0060"},
            "estleg:competentAuthority": {"@id": "estleg:Institution_justiitsministeerium"},
        },
        {
            "@id": "estleg:PKS_Par_1",
            "@type": ["estleg:LegalProvision"],
            "estleg:partOfAct": {"@id": "estleg:PKS_Map_2026"},
            "estleg:hasSubsection": [{"@id": "estleg:PKS_Par_1_Lg_1"}],
        },
        {
            "@id": "estleg:PKS_Par_1_Lg_1",
            "@type": ["estleg:Subsection"],
            "estleg:parentProvision": {"@id": "estleg:PKS_Par_1"},
        },
        {
            "@id": "estleg:EU_32000L0060",
            "@type": ["estleg:EULegislation"],
            "rdfs:label": "Dir",
        },
        {
            "@id": "estleg:Institution_justiitsministeerium",
            "@type": ["estleg:Institution"],
            "rdfs:label": "Justiitsministeerium",
        },
        {
            "@id": "estleg:PKS_Par_1",
            "@type": ["estleg:LegalProvision"],
            "estleg:interpretedBy": [{"@id": "estleg:RK_2016_1"}],
        },
        {
            "@id": "estleg:RK_2016_1",
            "@type": ["estleg:CourtDecision"],
        },
        {
            "@id": "estleg:PKS_Map_2026",
            "@type": ["estleg:Law", "estleg:Act"],
            "estleg:implementedBy": [{"@id": "estleg:Reg_1_Map_2026"}],
        },
        {
            "@id": "estleg:Reg_1_Map_2026",
            "@type": ["estleg:NationalRegulation", "estleg:Act"],
        },
    ]
    # Collapse duplicate @id entries the way a caller would merge first.
    by_id: dict[str, dict] = {}
    for node in nodes:
        existing = by_id.get(node["@id"])
        if existing is None:
            by_id[node["@id"]] = dict(node)
        else:
            existing.update({k: v for k, v in node.items() if k != "@id"})
    graph = list(by_id.values())
    planned = plan_inverse_updates(graph)
    assert planned["estleg:EU_32000L0060"]["estleg:transposedBy"] == [
        {"@id": "estleg:PKS_Map_2026"}
    ]
    assert planned["estleg:Institution_justiitsministeerium"]["estleg:governs"] == [
        {"@id": "estleg:PKS_Map_2026"}
    ]
    assert planned["estleg:PKS_Par_1_Lg_1"]["estleg:partOfAct"] == {
        "@id": "estleg:PKS_Map_2026"
    }
    assert planned["estleg:RK_2016_1"]["estleg:interpretsLaw"] == [
        {"@id": "estleg:PKS_Par_1"}
    ]
    assert planned["estleg:Reg_1_Map_2026"]["estleg:issuedUnder"] == [
        {"@id": "estleg:PKS_Map_2026"}
    ]
    stats = materialize_combined_edges(graph)
    assert stats["nodes_updated"] >= 4
    lg = next(n for n in graph if n["@id"] == "estleg:PKS_Par_1_Lg_1")
    assert lg["estleg:partOfAct"]["@id"] == "estleg:PKS_Map_2026"
    # Idempotent.
    assert materialize_combined_edges(graph)["nodes_updated"] == 0


def test_analytical_overlay_flags_and_reified_similarity() -> None:
    nodes = [
        {
            "@id": "estleg:PKS_Map_2026",
            "@type": ["estleg:Law", "estleg:Act"],
            "estleg:competentAuthority": {"@id": "estleg:Institution_x"},
            "estleg:referencedBy": [{"@id": "estleg:VOS_Par_1"}],
        },
        {
            "@id": "estleg:TMS_Map_2026",
            "@type": ["estleg:Law", "estleg:Act"],
        },
        {
            "@id": "estleg:PKS_Par_1",
            "@type": ["estleg:LegalProvision"],
            "estleg:referencedBy": [
                {"@id": "estleg:VOS_Par_1"},
                {"@id": "estleg:VOS_Par_2"},
            ],
            "estleg:interpretedBy": [{"@id": "estleg:RK_1"}],
        },
        {
            "@id": "estleg:EU_32000L0060",
            "@type": ["estleg:EULegislation"],
            "estleg:inForce": {"@value": "true", "@type": "xsd:boolean"},
        },
        {
            "@id": "estleg:EU_32016L0679",
            "@type": ["estleg:EULegislation"],
            "estleg:inForce": {"@value": "true", "@type": "xsd:boolean"},
            "estleg:transposedBy": [{"@id": "estleg:IKS_Map_2026"}],
        },
    ]
    planned = plan_analytical_updates(nodes)
    assert planned["estleg:TMS_Map_2026"]["estleg:hasNoCompetentAuthority"]["@value"] == (
        "true"
    )
    assert planned["estleg:TMS_Map_2026"]["estleg:competentAuthorityCount"] == 0
    assert "estleg:hasNoCompetentAuthority" not in planned["estleg:PKS_Map_2026"]
    assert planned["estleg:PKS_Par_1"]["estleg:inboundCitationCount"] == 2
    assert planned["estleg:PKS_Par_1"]["estleg:interpretationCount"] == 1
    assert planned["estleg:EU_32000L0060"]["estleg:hasNoTransposition"]["@value"] == (
        "true"
    )
    assert "estleg:hasNoTransposition" not in planned.get("estleg:EU_32016L0679", {})
    overlay_gaps = build_analytical_overlay(
        nodes,
        similarity_pairs=[],
        in_force_directives={"estleg:EU_31990L0314"},
    )
    gap_ids = {
        n["@id"]
        for n in overlay_gaps["@graph"]
        if isinstance(n, dict) and n.get("estleg:hasNoTransposition")
    }
    assert "estleg:EU_31990L0314" in gap_ids

    overlay = build_analytical_overlay(
        nodes,
        similarity_pairs=[
            {
                "source": "estleg:PKS_Par_1",
                "target": "estleg:VOS_Par_1",
                "similarity": 0.42,
            }
        ],
    )
    ids = [n["@id"] for n in overlay["@graph"]]
    assert "estleg:AnalyticalOverlay" in ids
    sim = similarity_node("estleg:PKS_Par_1", "estleg:VOS_Par_1", 0.42)
    assert sim["@id"] in ids
    stamped = next(n for n in overlay["@graph"] if n["@id"] == sim["@id"])
    assert stamped["estleg:similarityScore"]["@value"] == "0.42"
    assert stamped["estleg:similarFrom"]["@id"] == "estleg:PKS_Par_1"


def test_combined_builder_hooks_520_521(tmp_path: Path) -> None:
    peep = {
        "@graph": [
            {
                "@id": "estleg:PKS_Map_2026",
                "@type": ["estleg:Law", "estleg:Act"],
                "rdfs:label": "Perekonnaseadus",
                "estleg:transposesDirective": {"@id": "estleg:EU_32000L0060"},
                "estleg:competentAuthority": {
                    "@id": "estleg:Institution_justiitsministeerium"
                },
            },
            {
                "@id": "estleg:PKS_Par_1",
                "@type": ["estleg:LegalProvision"],
                "estleg:paragrahv": "§ 1.",
                "estleg:partOfAct": {"@id": "estleg:PKS_Map_2026"},
                "estleg:hasSubsection": [{"@id": "estleg:PKS_Par_1_Lg_1"}],
            },
            {
                "@id": "estleg:PKS_Par_1_Lg_1",
                "@type": ["estleg:Subsection"],
                "estleg:parentProvision": {"@id": "estleg:PKS_Par_1"},
            },
            {
                "@id": "estleg:EU_32000L0060",
                "@type": ["estleg:EULegislation"],
                "rdfs:label": "Directive",
                "estleg:celexNumber": "32000L0060",
                "estleg:inForce": {"@value": "true", "@type": "xsd:boolean"},
            },
            {
                "@id": "estleg:EU_31999L0031",
                "@type": ["estleg:EULegislation"],
                "rdfs:label": "Untransposed",
                "estleg:celexNumber": "31999L0031",
                "estleg:inForce": {"@value": "true", "@type": "xsd:boolean"},
            },
        ]
    }
    (tmp_path / "pks_peep.json").write_text(
        json.dumps(peep, ensure_ascii=False), encoding="utf-8"
    )
    inst_dir = tmp_path / "institutions"
    inst_dir.mkdir()
    (inst_dir / "institutions.jsonld").write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Institution_justiitsministeerium",
                        "@type": ["estleg:Institution"],
                        "rdfs:label": "Justiitsministeerium",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "controlled_vocabulary.jsonld").write_text(
        json.dumps({"@graph": [{"@id": "estleg:Act", "@type": ["owl:Class"]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    generate_combined_jsonld(tmp_path)
    combined = json.loads(
        (tmp_path / "combined_ontology.jsonld").read_text(encoding="utf-8")
    )
    by_id = {n["@id"]: n for n in combined["@graph"] if isinstance(n, dict)}
    assert by_id["estleg:EU_32000L0060"]["estleg:transposedBy"][0]["@id"] == (
        "estleg:PKS_Map_2026"
    )
    assert by_id["estleg:Institution_justiitsministeerium"]["estleg:governs"][0][
        "@id"
    ] == "estleg:PKS_Map_2026"
    assert by_id["estleg:PKS_Par_1_Lg_1"]["estleg:partOfAct"]["@id"] == (
        "estleg:PKS_Map_2026"
    )
    assert "estleg:hasNoTransposition" not in by_id["estleg:EU_32000L0060"]
    assert by_id["estleg:EU_31999L0031"]["estleg:hasNoTransposition"]["@value"] == (
        "true"
    )


def test_cv_and_docs_declare_520_521_terms() -> None:
    index = _vocab()
    assert index["estleg:governs"]["owl:inverseOf"]["@id"] == (
        "estleg:competentAuthority"
    )
    assert index["estleg:hasNoTransposition"]["rdfs:range"]["@id"] == "xsd:boolean"
    assert index["estleg:similarFrom"]["rdfs:domain"]["@id"] == "estleg:Similarity"
    assert "owl:TransitiveProperty" in index["estleg:isPartOf"]["@type"]
    text = SCHEMA_REF.read_text(encoding="utf-8")
    assert "inboundCitationCount" in text
    assert "hasNoTransposition" in text
    assert "governs" in text
    assert "analytical_overlay.jsonld" in text
    source = FIX_ALL.read_text(encoding="utf-8")
    assert "materialize_combined_edges" in source
    assert "stamp_analytical_properties" in source
    assert "estleg:governs" in STUB_SEMANTIC_EDGE_PREDICATES
    assert "estleg:transposedBy" in STUB_SEMANTIC_EDGE_PREDICATES


def _iter_combined():
    import json as _json

    decoder = _json.JSONDecoder()
    text = COMBINED.read_text(encoding="utf-8")
    start = text.find('"@graph"')
    bracket = text.find("[", start)
    i = bracket + 1
    length = len(text)
    while i < length:
        while i < length and text[i] in " \t\r\n,":
            i += 1
        if i < length and text[i] == "]":
            return
        obj, end = decoder.raw_decode(text, i)
        if isinstance(obj, dict):
            yield obj
        i = end


def test_committed_combined_has_520_521_edges() -> None:
    if not COMBINED.is_file():
        import pytest

        pytest.skip("combined_ontology.jsonld missing")
    first = COMBINED.read_text(encoding="utf-8", errors="replace")[:80]
    if first.startswith(LFS_PREFIX):
        import pytest

        pytest.skip("combined_ontology.jsonld is an LFS pointer")
    transposed_by = 0
    governs = 0
    subsection_part = 0
    gap_auth = 0
    gap_dir = 0
    inbound = 0
    for node in _iter_combined():
        if node.get("estleg:transposedBy"):
            transposed_by += 1
        if node.get("estleg:governs"):
            governs += 1
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:Subsection" in types and node.get("estleg:partOfAct"):
            subsection_part += 1
        if node.get("estleg:hasNoCompetentAuthority"):
            gap_auth += 1
        if node.get("estleg:hasNoTransposition"):
            gap_dir += 1
        if node.get("estleg:inboundCitationCount"):
            inbound += 1
    assert transposed_by >= 1
    assert governs >= 1
    assert subsection_part >= 1
    assert gap_auth >= 1
    assert inbound >= 1
    assert OVERLAY.is_file()
    overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
    sim = [
        n
        for n in overlay.get("@graph") or []
        if isinstance(n, dict)
        and "estleg:Similarity" in (n.get("@type") or [])
    ]
    assert sim
    assert "estleg:similarityScore" in sim[0]
    overlay_gaps = [
        n
        for n in overlay.get("@graph") or []
        if isinstance(n, dict) and n.get("estleg:hasNoTransposition")
    ]
    assert overlay_gaps
