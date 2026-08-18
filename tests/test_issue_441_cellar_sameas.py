"""#441 — CURIA CELLAR sameAs (CELEX + encoded ECLI), CELEX-derived
``euCaseNumber``, and ``estleg:curiaLink`` → ``estleg:eurLexLink``.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_eu_court_decisions as mod

KRR = Path(__file__).resolve().parent.parent / "krr_outputs"
COURT_OPINIONS = KRR / "curia" / "curia_court_opinions_peep.json"


def _types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    return [raw] if isinstance(raw, str) else list(raw or [])


def _sameas_iris(value: object) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    iris: list[str] = []
    for item in items:
        if isinstance(item, dict):
            iri = item.get("@id")
            if isinstance(iri, str):
                iris.append(iri)
        elif isinstance(item, str):
            iris.append(item)
    return iris


def _case_numbers(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, dict):
        inner = value.get("@value")
        return [inner] if isinstance(inner, str) and inner.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_case_numbers(item))
        return out
    return []


def test_cellar_ecli_iri_percent_encodes_colons() -> None:
    assert mod.cellar_ecli_iri("ECLI:EU:C:1991:490") == (
        "http://publications.europa.eu/resource/ecli/ECLI%3AEU%3AC%3A1991%3A490"
    )


def test_cellar_celex_iri_is_unencoded_psi() -> None:
    assert mod.cellar_celex_iri("61991CV0001") == (
        "http://publications.europa.eu/resource/celex/61991CV0001"
    )


def test_derive_case_number_from_celex_cj_cv_tt() -> None:
    assert mod.derive_case_number_from_celex("61991CV0001") == "C-1/91"
    assert mod.derive_case_number_from_celex("62016CJ0438") == "C-438/16"
    assert mod.derive_case_number_from_celex("62016TT0624") == "T-624/16"


def test_derive_case_number_ignores_celex_suffix() -> None:
    assert mod.derive_case_number_from_celex("62015CV0001(01)") == "C-1/15"


def test_derive_case_number_rejects_unknown_shape() -> None:
    assert mod.derive_case_number_from_celex("82015EE1202(01)") is None
    assert mod.derive_case_number_from_celex("E2014J0018") is None
    assert mod.derive_case_number_from_celex("") is None


def test_decision_to_node_emits_both_cellar_psis_and_eurlex_link() -> None:
    item = {
        "celex": "61991CV0001",
        "title": "Euroopa Kohtu arvamus, 14. detsember 1991. — Arvamus 1/91.",
        "date": "1991-12-14",
        "ecli": "ECLI:EU:C:1991:490",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    same_as = node["owl:sameAs"]
    assert isinstance(same_as, list)
    iris = {entry["@id"] for entry in same_as}
    assert iris == {
        mod.cellar_celex_iri("61991CV0001"),
        mod.cellar_ecli_iri("ECLI:EU:C:1991:490"),
    }
    assert "estleg:eurLexLink" in node
    assert "estleg:curiaLink" not in node
    assert node["estleg:eurLexLink"]["@value"].endswith("CELEX:61991CV0001")


def test_decision_to_node_derives_eu_case_number_when_title_has_none() -> None:
    item = {
        "celex": "61991CV0001",
        "title": "Euroopa Kohtu arvamus, 14. detsember 1991. — Arvamus 1/91.",
        "date": "1991-12-14",
        "ecli": "ECLI:EU:C:1991:490",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    assert _case_numbers(node.get("estleg:euCaseNumber")) == ["C-1/91"]


def test_decision_to_node_does_not_overwrite_title_case_numbers() -> None:
    item = {
        "celex": "62016CJ0438",
        "title": "Kohtuasi C-438/14",
        "date": "2016-10-13",
        "ecli": "",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    assert node["estleg:euCaseNumber"] == ["C-438/14"]


def test_retarget_curia_identity_is_idempotent() -> None:
    node = {
        "@id": "estleg:EUCJ_61991CV0001",
        "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
        "estleg:celexNumber": "61991CV0001",
        "estleg:ecliIdentifier": "ECLI:EU:C:1991:490",
        "estleg:curiaLink": {
            "@value": "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:61991CV0001",
            "@type": "xsd:anyURI",
        },
        "owl:sameAs": {
            "@id": "http://publications.europa.eu/resource/celex/61991CV0001"
        },
    }
    assert mod.retarget_curia_identity(node) is True
    assert "estleg:curiaLink" not in node
    assert "estleg:eurLexLink" in node
    assert node["estleg:euCaseNumber"] == "C-1/91"
    iris = _sameas_iris(node["owl:sameAs"])
    assert iris == [
        mod.cellar_celex_iri("61991CV0001"),
        mod.cellar_ecli_iri("ECLI:EU:C:1991:490"),
    ]
    snapshot = copy.deepcopy(node)
    assert mod.retarget_curia_identity(node) is False
    assert node == snapshot


def test_retarget_skips_non_decision_nodes() -> None:
    header = {"@id": "estleg:CURIA_Court_Opinions_Map_2026", "@type": ["owl:Ontology"]}
    assert mod.retarget_curia_identity(header) is False
    assert header == {
        "@id": "estleg:CURIA_Court_Opinions_Map_2026",
        "@type": ["owl:Ontology"],
    }


def test_court_opinions_corpus_has_cellar_sameas_and_case_numbers() -> None:
    assert COURT_OPINIONS.is_file(), f"missing {COURT_OPINIONS}"
    graph = json.loads(COURT_OPINIONS.read_text(encoding="utf-8")).get("@graph", [])
    decisions = [
        node
        for node in graph
        if isinstance(node, dict) and "estleg:EUCourtDecision" in _types(node)
    ]
    assert decisions, "no EUCourtDecision nodes in court-opinions peep"

    for node in decisions:
        celex = node.get("estleg:celexNumber")
        assert isinstance(celex, str) and celex, node.get("@id")
        iris = _sameas_iris(node.get("owl:sameAs"))
        assert mod.cellar_celex_iri(celex) in iris, node.get("@id")
        ecli = node.get("estleg:ecliIdentifier")
        if ecli:
            assert mod.cellar_ecli_iri(ecli) in iris, node.get("@id")
        assert "estleg:curiaLink" not in node, node.get("@id")
        assert _case_numbers(node.get("estleg:euCaseNumber")), node.get("@id")

    sample = next(
        node for node in decisions if node.get("estleg:celexNumber") == "61991CV0001"
    )
    assert "C-1/91" in _case_numbers(sample.get("estleg:euCaseNumber"))
    assert "estleg:curiaLink" not in json.dumps(graph)
