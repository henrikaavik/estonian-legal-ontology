from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_eu_court_decisions as curia
import generate_eu_legislation as eurlex
import generate_harmonisation_links as harmonisation


def test_eurlex_pagination_query_is_ordered(monkeypatch):
    queries: list[str] = []

    def fake_query(query: str) -> list[dict]:
        queries.append(query)
        return []

    monkeypatch.setattr(eurlex, "sparql_query", fake_query)

    assert eurlex.fetch_legislation_type("cdm:directive") == []
    assert "ORDER BY ?work" in queries[0]


def test_eurlex_in_force_accepts_boolean_literals():
    assert eurlex.is_in_force_value("1")
    assert eurlex.is_in_force_value("true")
    assert eurlex.is_in_force_value("True")
    assert not eurlex.is_in_force_value("0")


def test_curia_pagination_query_is_ordered(monkeypatch):
    queries: list[str] = []

    def fake_query(query: str) -> list[dict]:
        queries.append(query)
        return []

    monkeypatch.setattr(curia, "sparql_query", fake_query)

    assert curia.fetch_all_case_law() == []
    assert "ORDER BY ?work" in queries[0]


def test_curia_classifies_efta_and_newer_order_codes():
    assert curia.classify_from_celex("E2024CB0001") == (
        "Order",
        "Kohtumäärus",
        "CourtOfJustice",
        "orders",
    )
    assert curia.classify_from_celex("62024CD0001")[0] == "Order"


def test_curia_preserves_full_title_separately_from_label():
    long_title = "Kohtuasi C-1/24#" + ("Väga pikk pealkiri " * 40)
    node = curia.decision_to_node({"celex": "62024CJ0001", "title": long_title})

    assert len(node["rdfs:label"]["@value"]) <= 500
    assert node["dcterms:title"]["@value"] == curia.clean_title(long_title)


def test_harmonisation_query_is_ordered(monkeypatch):
    queries: list[str] = []

    def fake_query(query: str) -> list[dict]:
        queries.append(query)
        return []

    monkeypatch.setattr(harmonisation, "sparql_query", fake_query)

    assert harmonisation.fetch_other_transpositions("32000L0001") == []
    assert "ORDER BY ?country ?celex_nat" in queries[0]


def test_harmonisation_resolves_real_law_iri(tmp_path, monkeypatch):
    krr = tmp_path / "krr_outputs"
    law = krr / "law_peep.json"
    law.parent.mkdir(parents=True, exist_ok=True)
    law.write_text(
        '{"@graph": [{"@id": "estleg:AS_Map_2026", "@type": ["owl:Ontology"]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(harmonisation, "KRR_DIR", krr)

    assert harmonisation.get_law_harmonisation_target_iri("law_peep.json") == "estleg:AS_Map_2026"
