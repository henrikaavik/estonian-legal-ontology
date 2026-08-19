"""#557 — EE harmonises on combined stubs + draft law-naming coverage."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.estleg_common import (
    STUB_SEMANTIC_EDGE_PREDICATES,
    required_closure_props,
)
from estleg.extract_draft_impact import (
    LAW_NAMING_COVERAGE_FLOOR,
    is_usable_law_name,
    law_names_for_draft,
)
from estleg.fix_all_issues import _make_closure_stub
from estleg.generate_harmonisation_links import (
    apply_ee_edges_to_node,
    load_sidecar_ee_edges,
)

REPO = Path(__file__).resolve().parent.parent
KRR = REPO / "krr_outputs"
COMBINED = KRR / "eelnoud" / "eelnoud_combined.jsonld"
REPORT = KRR / "reports" / "draft_impact_report.json"
SIDECAR = (
    KRR / "harmonisation" / "harmonisation_by_directive" / "harm_32012L0043.json"
)
ONTOLOGY = KRR / "combined_ontology.jsonld"


def test_usable_law_name_rejects_junk() -> None:
    assert is_usable_law_name("Riigi Teataja seadus")
    assert is_usable_law_name("Karistusseadustik")
    assert not is_usable_law_name("1 muutmise seadus")
    assert not is_usable_law_name("10 täiendamise seadus")
    assert not is_usable_law_name("X seaduse muutmise seadus")
    assert not is_usable_law_name("eelnõu")
    assert not is_usable_law_name("seadus")


def test_law_names_for_draft_uses_title_when_field_missing() -> None:
    node = {
        "rdfs:label": {
            "@value": "Riigi Teataja seaduse muutmise seadus",
            "@language": "et",
        }
    }
    assert law_names_for_draft(node) == ["Riigi Teataja seaduse"]


def test_law_names_for_draft_drops_stored_junk() -> None:
    node = {
        "rdfs:label": {"@value": "Muu eelnõu", "@language": "et"},
        "estleg:affectedLawName": "1 muutmise seadus",
    }
    assert law_names_for_draft(node) == []


def test_harmonisation_stub_keeps_ee_edges() -> None:
    assert "estleg:harmonises" in required_closure_props("estleg:HarmonisationLink")
    assert "estleg:harmonises" in STUB_SEMANTIC_EDGE_PREDICATES
    assert "estleg:sharedDirective" in STUB_SEMANTIC_EDGE_PREDICATES
    source = {
        "@id": "estleg:Harmonisation_32012L0043",
        "@type": ["owl:NamedIndividual", "estleg:HarmonisationLink"],
        "rdfs:label": "Harmonisation: 32012L0043",
        "estleg:sharedDirective": {"@id": "estleg:EU_32012L0043"},
        "estleg:harmonises": [{"@id": "estleg:BIOTSI_Map"}],
        "estleg:memberStateCode": "EST",
    }
    stub = _make_closure_stub(source)
    assert stub["estleg:harmonises"] == [{"@id": "estleg:BIOTSI_Map"}]
    assert stub["estleg:sharedDirective"] == {"@id": "estleg:EU_32012L0043"}
    # Comparative member-state code is not a required EE edge; still stripped.
    assert "estleg:memberStateCode" not in stub


def test_apply_ee_edges_is_idempotent() -> None:
    node = {"@id": "estleg:Harmonisation_X"}
    payload = {
        "estleg:harmonises": [{"@id": "estleg:VOS_Map"}],
        "estleg:sharedDirective": {"@id": "estleg:EU_32000L0060"},
    }
    assert apply_ee_edges_to_node(node, payload) is True
    assert apply_ee_edges_to_node(node, payload) is False


def test_sidecar_aggregates_have_ee_harmonises() -> None:
    edges = load_sidecar_ee_edges()
    assert len(edges) >= 100
    sample = json.loads(SIDECAR.read_text(encoding="utf-8"))
    agg = next(
        n
        for n in sample["@graph"]
        if n.get("@id") == "estleg:Harmonisation_32012L0043"
    )
    assert agg["estleg:harmonises"][0]["@id"].startswith("estleg:")
    assert edges["estleg:Harmonisation_32012L0043"]["estleg:harmonises"]


def _draft_nodes() -> list[dict]:
    doc = json.loads(COMBINED.read_text(encoding="utf-8"))
    nodes = []
    for node in doc.get("@graph") or []:
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:DraftLegislation" in types:
            nodes.append(node)
    return nodes


def test_committed_draft_law_naming_coverage() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    coverage = report["summary"]["law_naming_coverage"]
    assert coverage >= LAW_NAMING_COVERAGE_FLOOR
    drafts = _draft_nodes()
    naming = [n for n in drafts if law_names_for_draft(n)]
    resolved = [n for n in naming if n.get("estleg:amendsLaw")]
    assert naming
    assert len(resolved) / len(naming) >= LAW_NAMING_COVERAGE_FLOOR
    # Peep-only consumers must see the IRI too.
    peep = json.loads(
        (KRR / "eelnoud" / "eelnoud_review_peep.json").read_text(encoding="utf-8")
    )
    peep_drafts = [
        n
        for n in peep.get("@graph") or []
        if isinstance(n, dict)
        and "estleg:DraftLegislation"
        in (n.get("@type") if isinstance(n.get("@type"), list) else [n.get("@type")])
    ]
    peep_with_iri = sum(
        1
        for n in peep_drafts
        if isinstance(n.get("estleg:amendsLaw"), dict)
        or isinstance(n.get("estleg:amendsLaw"), list)
    )
    assert peep_with_iri >= 100


def test_combined_harmonisation_stub_has_ee_edge() -> None:
    """Pin after --patch-combined: the Biotsiidiseadus sidecar edge is visible."""
    text = ONTOLOGY.read_text(encoding="utf-8")
    needle = '"@id": "estleg:Harmonisation_32012L0043"'
    idx = 0
    window = ""
    while True:
        found = text.find(needle, idx)
        assert found >= 0
        window = text[found : found + 1200]
        if "estleg:HarmonisationLink" in window:
            break
        idx = found + len(needle)
    assert "estleg:harmonises" in window
    assert "estleg:BIOTSI_Map" in window
    assert "estleg:sharedDirective" in window
