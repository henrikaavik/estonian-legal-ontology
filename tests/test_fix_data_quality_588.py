"""Tests for the surgical #588 data-quality repairs.

Covers the three data fixers in ``fix_data_quality_588`` (exercised on
synthetic nodes so no krr_outputs artifact is required) plus the companion
``KNOWN_ABBREVIATIONS`` correction in ``estleg_common``.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import KNOWN_ABBREVIATIONS
from fix_data_quality_588 import (
    CURIA_NODE_ID,
    fix_curia_doc,
    fix_ep_doc,
    fix_eurlex_doc,
)


# ---------------------------------------------------------------------------
# Item 4 — KNOWN_ABBREVIATIONS mislabels corrected (the latent abbrev fix)
# ---------------------------------------------------------------------------
def test_ppvs_corrected_to_police_act() -> None:
    """``PPVS`` is the Police and Border Guard Act, not the Planning Act."""
    assert KNOWN_ABBREVIATIONS["PPVS"] == "Politsei ja piirivalve seadus"


def test_rsvs_corrected_to_state_secrets_act() -> None:
    """``RSVS`` is the State Secrets Act, not the Weapons Act."""
    assert KNOWN_ABBREVIATIONS["RSVS"] == "Riigisaladuse seadus"


# ---------------------------------------------------------------------------
# Item 1 — CURIA order EUCJ_62016TT0624
# ---------------------------------------------------------------------------
def _curia_node() -> dict:
    """A faithful synthetic copy of the defective curia node."""
    return {
        "@id": CURIA_NODE_ID,
        "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
        "rdfs:label": "Üldkohtu … määrus … Kohtuasi T-624/16 TO.",
        "estleg:celexNumber": "62016TT0624",
        "estleg:euCourtDecisionType": {"@id": "estleg:EUDecType_Other"},
        "estleg:euCourt": {"@id": "estleg:EUCourt_CourtOfJustice"},
        "estleg:curiaLink": {
            "@value": "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:62016TT0624",
            "@type": "xsd:anyURI",
        },
        "estleg:ecliIdentifier": "ECLI:EU:T:2019:47",
        "estleg:euCaseNumber": "T-624/16",
        "owl:sameAs": {"@id": "http://publications.europa.eu/resource/celex/62016TT0624"},
        "dcterms:source": {"@id": "http://publications.europa.eu/resource/celex/62016TT0624"},
    }


def test_curia_fix_corrects_celex_court_and_type() -> None:
    doc = {"@graph": [_curia_node()]}
    res = fix_curia_doc(doc)

    assert res["node_found"] is True
    assert res["celexNumber"] is True
    assert res["euCourt"] is True
    assert res["euCourtDecisionType"] is True
    assert res["celex_in_links"] is True

    node = doc["@graph"][0]
    assert node["estleg:celexNumber"] == "62016TO0624"
    assert node["estleg:euCourt"]["@id"] == "estleg:EUCourt_GeneralCourt"
    assert node["estleg:euCourtDecisionType"]["@id"] == "estleg:EUDecType_Order"


def test_curia_fix_rewrites_celex_in_all_link_fields() -> None:
    """celexNumber must not contradict the node's own sameAs/source identity."""
    doc = {"@graph": [_curia_node()]}
    fix_curia_doc(doc)
    node = doc["@graph"][0]

    assert "62016TT0624" not in node["estleg:curiaLink"]["@value"]
    assert node["estleg:curiaLink"]["@value"].endswith("CELEX:62016TO0624")
    assert node["owl:sameAs"]["@id"].endswith("/celex/62016TO0624")
    assert node["dcterms:source"]["@id"].endswith("/celex/62016TO0624")


def test_curia_fix_leaves_node_id_untouched() -> None:
    """The @id embeds the CELEX but is an opaque identifier — never renamed."""
    doc = {"@graph": [_curia_node()]}
    fix_curia_doc(doc)
    assert doc["@graph"][0]["@id"] == CURIA_NODE_ID


def test_curia_fix_is_idempotent() -> None:
    doc = {"@graph": [_curia_node()]}
    fix_curia_doc(doc)
    snapshot = copy.deepcopy(doc)
    res2 = fix_curia_doc(doc)

    assert res2["node_found"] is True
    assert not any(
        res2[k] for k in ("celexNumber", "euCourt", "euCourtDecisionType", "celex_in_links")
    )
    assert doc == snapshot


def test_curia_fix_absent_node_is_noop() -> None:
    doc = {"@graph": [{"@id": "estleg:EUCJ_other", "estleg:celexNumber": "62016R0001"}]}
    res = fix_curia_doc(doc)
    assert res["node_found"] is False


# ---------------------------------------------------------------------------
# Item 2 — 5 EP "Texts Adopted" mistyped as in-force Regulations
# ---------------------------------------------------------------------------
def _eurlex_ap_node(celex: str = "52025AP0047") -> dict:
    return {
        "@id": f"estleg:EU_{celex}",
        "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
        "rdfs:label": "P10_TA(2025)0047 — …",
        "estleg:celexNumber": celex,
        "estleg:euDocumentType": {"@id": "estleg:EUDocType_Regulation"},
    }


def test_eurlex_retypes_ap_record_to_parliament_position() -> None:
    doc = {"@graph": [_eurlex_ap_node("52025AP0047")]}
    changed = fix_eurlex_doc(doc)
    assert changed == 1
    assert doc["@graph"][0]["estleg:euDocumentType"] == {
        "@id": "estleg:EUDocType_ParliamentPosition"
    }


def test_eurlex_retypes_all_five_targets() -> None:
    doc = {
        "@graph": [
            _eurlex_ap_node(c)
            for c in ("52025AP0047", "52025AP0045", "52025AP0108", "52025AP0102", "52025AP0101")
        ]
    }
    assert fix_eurlex_doc(doc) == 5
    for node in doc["@graph"]:
        assert node["estleg:euDocumentType"]["@id"] == "estleg:EUDocType_ParliamentPosition"


def test_eurlex_leaves_genuine_regulation_untouched() -> None:
    """A real sector-3 type-R regulation must keep ``EUDocType_Regulation``."""
    reg = {
        "@id": "estleg:EU_32016R0679",
        "estleg:celexNumber": "32016R0679",
        "estleg:euDocumentType": {"@id": "estleg:EUDocType_Regulation"},
    }
    doc = {"@graph": [reg]}
    assert fix_eurlex_doc(doc) == 0
    assert doc["@graph"][0]["estleg:euDocumentType"]["@id"] == "estleg:EUDocType_Regulation"


def test_eurlex_fix_is_idempotent() -> None:
    doc = {"@graph": [_eurlex_ap_node("52025AP0047")]}
    fix_eurlex_doc(doc)
    assert fix_eurlex_doc(doc) == 0


# ---------------------------------------------------------------------------
# Item 3 — Eesti Pank issuer case
# ---------------------------------------------------------------------------
def test_ep_normalises_lowercase_issuer() -> None:
    doc = {"@graph": [{"@id": "estleg:Reg_x", "estleg:issuer": "Eesti Panga president"}]}
    assert fix_ep_doc(doc) == 1
    assert doc["@graph"][0]["estleg:issuer"] == "Eesti Panga President"


def test_ep_leaves_canonical_issuer_untouched() -> None:
    doc = {"@graph": [{"@id": "estleg:Reg_x", "estleg:issuer": "Eesti Panga President"}]}
    assert fix_ep_doc(doc) == 0
    assert doc["@graph"][0]["estleg:issuer"] == "Eesti Panga President"


def test_ep_handles_issuer_list_form() -> None:
    doc = {
        "@graph": [
            {"@id": "estleg:Reg_x", "estleg:issuer": ["Vabariigi Valitsus", "Eesti Panga president"]}
        ]
    }
    assert fix_ep_doc(doc) == 1
    assert doc["@graph"][0]["estleg:issuer"] == ["Vabariigi Valitsus", "Eesti Panga President"]


def test_ep_does_not_touch_other_issuers() -> None:
    doc = {"@graph": [{"@id": "estleg:Reg_x", "estleg:issuer": "Vabariigi Valitsus"}]}
    assert fix_ep_doc(doc) == 0
    assert doc["@graph"][0]["estleg:issuer"] == "Vabariigi Valitsus"


def test_ep_fix_is_idempotent() -> None:
    doc = {"@graph": [{"@id": "estleg:Reg_x", "estleg:issuer": "Eesti Panga president"}]}
    fix_ep_doc(doc)
    assert fix_ep_doc(doc) == 0
