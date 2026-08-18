"""Tests for scripts/link_curia_eu_legislation.py (#418 CURIA → EU edges)."""
from __future__ import annotations

import json
from pathlib import Path

from estleg import link_curia_eu_legislation as linker


def test_parse_directive_classic_emu():
    assert linker.parse_eu_citations("Direktiiv 80/987/EMÜ") == ["31980L0987"]


def test_parse_directive_four_digit_eu():
    assert linker.parse_eu_citations("Direktiiv 2008/98/EÜ") == ["32008L0098"]


def test_parse_directive_el_modern():
    assert linker.parse_eu_citations("Direktiiv (EL) 2019/790") == ["32019L0790"]


def test_parse_regulation_emu_number_year():
    assert linker.parse_eu_citations(
        "Nõukogu määrus (EMÜ) nr 1408/71"
    ) == ["31971R1408"]


def test_parse_regulation_ec_number_year():
    assert linker.parse_eu_citations("Määrus (EÜ) nr 44/2001") == ["32001R0044"]


def test_parse_regulation_el_number_year():
    assert linker.parse_eu_citations("Määrus (EL) nr 650/2012") == ["32012R0650"]


def test_parse_regulation_el_year_number():
    assert linker.parse_eu_citations("Määrus (EL) 2016/679") == ["32016R0679"]


def test_two_digit_year_bands():
    assert linker.parse_eu_citations("Direktiiv 80/987/EMÜ")[0].startswith("31980")
    assert linker.parse_eu_citations("Direktiiv 08/98/EÜ") == ["32008L0098"]


def test_bare_celex_and_dedup():
    text = "CELEX 32016R0679 ja Määrus (EL) 2016/679"
    assert linker.parse_eu_citations(text) == ["32016R0679"]


def test_artikkel_is_not_a_citation():
    assert linker.parse_eu_citations("artikkel 80 lõige 1") == []


def test_celex_to_iri():
    assert linker.celex_to_iri("31980L0987") == "estleg:EU_31980L0987"


def test_unknown_celex_emits_no_edge():
    node = {
        "@id": "estleg:EUCJ_1",
        "@type": ["estleg:EUCourtDecision"],
        "rdfs:label": "Direktiiv 80/987/EMÜ",
    }
    assert linker.link_decision_node(node, set()) is False
    assert "estleg:interpretsEULaw" not in node


def test_known_celex_appends_edge_and_is_idempotent():
    iri = "estleg:EU_31980L0987"
    node = {
        "@id": "estleg:EUCJ_1",
        "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
        "rdfs:label": {
            "@value": "Direktiiv 80/987/EMÜ töötajate kaitse kohta",
            "@language": "et",
        },
    }
    assert linker.link_decision_node(node, {iri}) is True
    assert node["estleg:interpretsEULaw"] == {"@id": iri}
    assert linker.link_decision_node(node, {iri}) is False
    assert node["estleg:interpretsEULaw"] == {"@id": iri}


def test_process_file_tmp_peep(tmp_path):
    peep = tmp_path / "curia_judgments_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": [
                    {
                        "@id": "estleg:EUCJ_1",
                        "@type": ["estleg:EUCourtDecision"],
                        "rdfs:label": "Direktiiv 80/987/EMÜ",
                    },
                    {"@id": "estleg:Header", "@type": ["owl:Ontology"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    known = {"estleg:EU_31980L0987"}
    changed, decisions, edges = linker.process_file(peep, known, dry_run=False)
    assert (changed, decisions, edges) == (1, 1, 1)
    doc = json.loads(peep.read_text(encoding="utf-8"))
    assert doc["@graph"][0]["estleg:interpretsEULaw"] == {
        "@id": "estleg:EU_31980L0987"
    }
    changed2, _, edges2 = linker.process_file(peep, known, dry_run=False)
    assert (changed2, edges2) == (0, 0)


def test_committed_report_meets_match_rate() -> None:
    report = Path(__file__).resolve().parent.parent / (
        "krr_outputs/curia/curia_eu_link_report.json"
    )
    if not report.exists():
        raise AssertionError("missing curia_eu_link_report.json")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["match_rate_among_parsed"] >= 0.5
    assert data["decisions_with_parsed_citation_linked"] >= 1


def test_ticket_sample_directive_80_987_is_linked() -> None:
    """AG opinion Cosmas / Mosbæk cites Direktiiv 80/987/EMÜ (#418)."""
    path = (
        Path(__file__).resolve().parent.parent
        / "krr_outputs/curia/curia_ag_opinions_peep.json"
    )
    if not path.exists():
        raise AssertionError("missing curia_ag_opinions_peep.json")
    graph = json.loads(path.read_text(encoding="utf-8")).get("@graph", [])
    node = next(n for n in graph if n.get("@id") == "estleg:EUCJ_61996CC0117")
    raw = node["estleg:interpretsEULaw"]
    iris = [raw["@id"]] if isinstance(raw, dict) else [x["@id"] for x in raw]
    assert "estleg:EU_31980L0987" in iris
