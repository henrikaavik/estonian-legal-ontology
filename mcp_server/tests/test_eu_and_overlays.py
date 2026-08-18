"""#505 CURIA-for-directive and #540 overlay-layer tests.

Unit tests monkeypatch the CURIA loader so they never parse the ~22k-decision
peeps. Overlay tests either use fixtures or open a single small harm_*.json.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from estleg_mcp import data, server

try:
    data.corpus_root()
    _CORPUS_AVAILABLE = True
except FileNotFoundError:
    _CORPUS_AVAILABLE = False

_FIXTURE: list[dict] = [
    {
        "@type": ["estleg:EUCourtDecision"],
        "rdfs:label": "Judgment mentioning 32000L0060 (Water Framework).",
        "estleg:celexNumber": "62000CJ0001",
        "estleg:ecliIdentifier": "ECLI:EU:C:2001:1",
        "estleg:curiaLink": {
            "@value": "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:62000CJ0001"
        },
        "owl:sameAs": {"@id": "http://publications.europa.eu/resource/celex/62000CJ0001"},
    },
    {
        "@type": ["estleg:EUCourtDecision"],
        "rdfs:label": "Unrelated customs tariff case.",
        "estleg:celexNumber": "61996CJ0080",
        "estleg:ecliIdentifier": "ECLI:EU:C:1998:5",
        "owl:sameAs": {"@id": "http://publications.europa.eu/resource/celex/61996CJ0080"},
        "dcterms:source": {"@id": "http://publications.europa.eu/resource/celex/61996CJ0080"},
    },
    {
        "@type": ["estleg:EUCourtDecision"],
        "rdfs:label": "SameAs-only mention of a later directive.",
        "estleg:celexNumber": "62014CJ0099",
        "owl:sameAs": {"@id": "http://publications.europa.eu/resource/celex/32014L0092"},
        "dcterms:source": {"@id": "http://publications.europa.eu/resource/celex/32014L0092"},
    },
]


def _patch_curia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(data, "_curia_decisions", lambda: tuple(_FIXTURE))


def test_normalize_celex_strips_spaces_and_wrapper() -> None:
    assert data.normalize_celex("32000 L0060") == "32000L0060"
    assert data.normalize_celex("celex:32000l0060") == "32000L0060"
    assert data.normalize_celex("  32000L0060  ") == "32000L0060"
    assert data.normalize_celex("") == ""
    assert data.normalize_celex("   ") == ""


def test_eu_case_law_label_hit_and_spaces(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_curia(monkeypatch)
    items = server.eu_case_law_for_directive("32000 L0060", limit=20)
    assert items == [
        {
            "ecli_or_celex": "ECLI:EU:C:2001:1",
            "title": "Judgment mentioning 32000L0060 (Water Framework).",
            "curia_or_eurlex_url": (
                "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:62000CJ0001"
            ),
        }
    ]


def test_eu_case_law_sameas_and_celexnumber(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_curia(monkeypatch)
    sameas = server.eu_case_law_for_directive("32014L0092")
    assert len(sameas) == 1
    assert sameas[0]["ecli_or_celex"] == "62014CJ0099"
    assert sameas[0]["curia_or_eurlex_url"].endswith("CELEX:62014CJ0099")

    own = server.eu_case_law_for_directive("61996CJ0080")
    assert len(own) == 1
    assert own[0]["ecli_or_celex"] == "ECLI:EU:C:1998:5"
    assert own[0]["title"].startswith("Unrelated")


def test_eu_case_law_none_returns_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_curia(monkeypatch)
    miss = server.eu_case_law_for_directive("31990L0314")
    assert len(miss) == 1
    assert "note" in miss[0]
    assert "31990L0314" in miss[0]["note"]
    assert server.eu_case_law_for_directive("", limit=5) == [
        {"note": "No CELEX number in ''."}
    ]
    assert server.eu_case_law_for_directive("32000L0060", limit=0) == []


def test_eu_case_law_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    extra = {
        "@type": ["estleg:EUCourtDecision"],
        "rdfs:label": "Second 32000L0060 hit.",
        "estleg:celexNumber": "62000CJ0002",
        "estleg:ecliIdentifier": "ECLI:EU:C:2001:2",
        "estleg:curiaLink": "https://curia.example/62000CJ0002",
    }
    monkeypatch.setattr(data, "_curia_decisions", lambda: (*_FIXTURE, extra))
    capped = server.eu_case_law_for_directive("32000L0060", limit=1)
    assert len(capped) == 1
    assert capped[0]["ecli_or_celex"] == "ECLI:EU:C:2001:1"


@pytest.mark.skipif(not _CORPUS_AVAILABLE, reason="corpus not found")
def test_eu_case_law_cheap_corpus_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse only the tiny court-opinions peep (not all 22k decisions)."""
    path = data.krr_dir() / "curia" / "curia_court_opinions_peep.json"
    if not path.is_file():
        pytest.skip("curia_court_opinions_peep.json not present")
    data._curia_decisions.cache_clear()
    monkeypatch.setattr(data, "_curia_peep_paths", lambda: [path])
    hits = data.eu_case_law_for_directive("61991CV0001")
    if not hits:
        pytest.skip("no CELEX hit in court-opinions peep")
    assert hits[0]["ecli_or_celex"]
    assert hits[0]["title"]
    assert hits[0]["curia_or_eurlex_url"]


@pytest.mark.skipif(not _CORPUS_AVAILABLE, reason="corpus not found")
def test_eu_case_law_directive_smoke_skips_if_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Directive CELEX in CURIA is #418-empty; skip rather than fail."""
    path = data.krr_dir() / "curia" / "curia_court_opinions_peep.json"
    if not path.is_file():
        pytest.skip("curia_court_opinions_peep.json not present")
    data._curia_decisions.cache_clear()
    monkeypatch.setattr(data, "_curia_peep_paths", lambda: [path])
    hits = data.eu_case_law_for_directive("32000L0060")
    if not hits:
        pytest.skip("no CURIA hit for 32000L0060 (expected under #418)")
    assert hits[0]["ecli_or_celex"]


def test_define_term_uses_concepts_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = (
        {
            "@id": "estleg:Concept_elatis",
            "@type": ["estleg:Concept"],
            "skos:prefLabel": "elatis",
            "skos:definition": "Maintenance obligation.",
        },
        {
            "@id": "estleg:LegalConcept_leping",
            "@type": ["estleg:LegalConcept"],
            "rdfs:label": "leping",
            "estleg:definition": "A contract.",
        },
    )
    monkeypatch.setattr(data, "overlay_graph", lambda layer, key="": graph)
    terms = server.define_term("elatis", limit=5)
    assert terms == [
        {
            "id": "estleg:Concept_elatis",
            "label": "elatis",
            "definition": "Maintenance obligation.",
        }
    ]
    assert server.define_term("", limit=5) == []


def test_overlay_path_and_harmonisation_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = (
        {
            "@id": "estleg:Harmonisation_32000L0060",
            "@type": ["estleg:HarmonisationLink"],
            "rdfs:label": "Harmonisation: 32000L0060",
            "estleg:harmonises": [{"@id": "estleg:VEE_Map_2026"}],
        },
        {
            "@id": "estleg:Harm_32000L0060_FIN_1",
            "@type": ["estleg:HarmonisationLink"],
            "rdfs:label": "Finland: water measure",
            "estleg:memberStateCode": "FIN",
            "estleg:nationalCelex": "72000L0060FIN_1",
        },
    )
    monkeypatch.setattr(data, "overlay_graph", lambda layer, key="": graph)
    monkeypatch.setattr(data, "krr_dir", lambda: Path("/tmp/krr_outputs"))
    harm = data.overlay_path("harmonisation", "32000 L0060")
    assert harm is not None and harm.name == "harm_32000L0060.json"
    assert data.overlay_path("concepts") is not None
    assert data.overlay_path("nope") is None
    rows = server.harmonisation_for_directive("32000 L0060", limit=10)
    assert {r["id"] for r in rows} == {
        "estleg:Harmonisation_32000L0060",
        "estleg:Harm_32000L0060_FIN_1",
    }
    fin = next(r for r in rows if r["member_state"] == "FIN")
    assert fin["national_celex"] == "72000L0060FIN_1"
    assert server.harmonisation_for_directive("", limit=5) == [
        {"note": "No CELEX number in ''."}
    ]
    assert server.harmonisation_for_directive("32000L0060", limit=0) == []


@pytest.mark.skipif(not _CORPUS_AVAILABLE, reason="corpus not found")
def test_harmonisation_overlay_opens_real_harm_file() -> None:
    data.overlay_graph.cache_clear()
    path = data.overlay_path("harmonisation", "32000L0060")
    assert path is not None and path.is_file()
    rows = data.harmonisation_for_directive("32000L0060", limit=5)
    assert rows
    assert any("32000L0060" in r["label"] or r["id"] for r in rows)


def test_layers_available_documents_overlays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        data,
        "layers_available",
        lambda: [
            {
                "layer": "concepts",
                "path": "krr_outputs/concepts/concepts_combined.jsonld",
                "status": "wired",
                "tools": "define_term",
                "present": "yes",
                "note": "",
            },
            {
                "layer": "harmonisation",
                "path": "krr_outputs/harmonisation/harmonisation_by_directive/harm_*.json",
                "status": "loadable",
                "tools": "harmonisation_for_directive",
                "present": "yes",
                "note": "",
            },
            {
                "layer": "similarity",
                "path": "krr_outputs/similarity_index.json",
                "status": "excluded",
                "tools": "",
                "present": "no",
                "note": "Git LFS",
            },
        ],
    )
    rows = server.layers_available()
    by_layer = {r["layer"]: r for r in rows}
    assert by_layer["concepts"]["status"] == "wired"
    assert by_layer["harmonisation"]["status"] == "loadable"
    assert by_layer["similarity"]["status"] == "excluded"


@pytest.mark.skipif(not _CORPUS_AVAILABLE, reason="corpus not found")
def test_layers_available_corpus_shapes() -> None:
    rows = server.layers_available()
    by_layer = {r["layer"]: r for r in rows}
    for key in ("concepts", "harmonisation", "sanctions", "similarity", "combined_ontology"):
        assert key in by_layer, key
        assert {"layer", "path", "status", "tools", "present", "note"} <= set(by_layer[key])
    assert by_layer["concepts"]["status"] == "wired"
    assert "define_term" in by_layer["concepts"]["tools"]
    assert by_layer["harmonisation"]["status"] == "loadable"
    assert by_layer["similarity"]["status"] == "excluded"
    assert by_layer["combined_ontology"]["status"] == "excluded"
    assert by_layer["annotations"]["status"] == "excluded"
