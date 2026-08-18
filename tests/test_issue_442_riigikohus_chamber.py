"""#442 leftovers: rikosUrl, chamber-from-header, ecliIdentifier domain."""
from __future__ import annotations

import json
from pathlib import Path

from estleg import generate_court_decisions as gcd

REPO = Path(__file__).resolve().parent.parent
CURIA_SCHEMA = REPO / "krr_outputs" / "curia" / "curia_schema.json"
RK_SCHEMA = REPO / "krr_outputs" / "riigikohus" / "riigikohus_schema.json"
RK_2020 = REPO / "krr_outputs" / "riigikohus" / "riigikohus_2020_peep.json"

PIN_2020_ID = "estleg:RK_3_18_1432_93"
PIN_2020_OID = "281761593"
PIN_2020_RIKOS = f"https://rikos.rik.ee/Lahend/Index?id={PIN_2020_OID}"
PIN_2020_DECISION_LINK = (
    "https://www.riigikohus.ee/et/lahendid/?asjaNr=3-18-1432/93"
)


def test_extract_chamber_on_sample_headers():
    assert gcd.extract_chamber(
        "RIIGIKOHUS KRIMINAALKOLLEEGIUM KOHTUOTSUS Eesti Vabariigi nimel"
    ) == "Kriminaalkolleegium"
    assert gcd.extract_chamber(
        "RIIGIKOHUS TSIVIILKOLLEEGIUM KOHTUOTSUS Eesti Vabariigi nimel"
    ) == "Tsiviilkolleegium"
    assert gcd.extract_chamber(
        "RIIGIKOHUS HALDUSKOLLEEGIUM KOHTUMÄÄRUS Kohtuasja number 3-18-1432"
    ) == "Halduskolleegium"
    assert gcd.extract_chamber(
        "RIIGIKOHUS PÕHISEADUSLIKKUSE JÄRELEVALVE KOLLEEGIUM KOHTUOTSUS"
    ) == "Põhiseaduslikkuse järelevalve kolleegium"
    assert gcd.extract_chamber(
        "RIIGIKOHUS ERIKOGU KOHTUOTSUS Eesti Vabariigi nimel"
    ) == "Erikogu"
    assert gcd.extract_chamber(
        "RIIGIKOHUS ÜLDKOGU KOHTUOTSUS Eesti Vabariigi nimel"
    ) == "Üldkogu"


def test_extract_chamber_legacy_and_ocr_headers():
    assert gcd.extract_chamber(
        "K O H T U O T S U S EV Riigikohtu tsiviilkolleegium koosseisus"
    ) == "Tsiviilkolleegium"
    assert gcd.extract_chamber(
        "Riigikohtu kriminaalkollegium kooseisus"
    ) == "Kriminaalkolleegium"
    assert gcd.extract_chamber(
        "Riigikohtu halduskohtukolleegium koosseisus"
    ) == "Halduskolleegium"


def test_extract_chamber_ignores_body_uldkogu_and_empty():
    header = (
        "RIIGIKOHUS HALDUSKOLLEEGIUM KOHTUMÄÄRUS Kohtuasja number 3-18-1432 "
        "RESOLUTSIOON Anda asi lahendada Riigikohtu üldkogule."
    )
    assert gcd.extract_chamber(header) == "Halduskolleegium"
    assert gcd.extract_chamber("") is None
    assert gcd.extract_chamber("KOHTUOTSUS Eesti Vabariigi nimel Tartus") is None


def test_rikos_url_from_object_id_shape():
    assert gcd.rikos_url_from_object_id("390882180") == (
        "https://rikos.rik.ee/Lahend/Index?id=390882180"
    )
    assert gcd.rikos_url_from_object_id(" 281761593 ") == PIN_2020_RIKOS


def test_decision_to_node_emits_rikos_url_and_chamber():
    dec = {
        "case_nr": "3-18-1432/93",
        "object_id": PIN_2020_OID,
        "date": "29.12.2020",
        "decision_type": "Kohtumäärus",
        "summary": "",
        "link": "https://rikos.rik.ee/x",
        "legal_text": (
            "RIIGIKOHUS HALDUSKOLLEEGIUM KOHTUMÄÄRUS Kohtuasja number 3-18-1432"
        ),
    }
    node = gcd.decision_to_node(dec, 2020, set())
    assert node is not None
    assert node["estleg:rikosUrl"] == {
        "@value": PIN_2020_RIKOS,
        "@type": "xsd:anyURI",
    }
    assert node["estleg:chamber"] == "Halduskolleegium"
    assert node["estleg:rikObjectId"] == PIN_2020_OID
    assert node["estleg:decisionLink"]["@value"].startswith(
        "https://www.riigikohus.ee/et/lahendid/?"
    )


def test_backfill_rk_identity_is_idempotent():
    node = {
        "@type": ["estleg:CourtDecision"],
        "estleg:rikObjectId": PIN_2020_OID,
        "estleg:legalText": (
            "RIIGIKOHUS HALDUSKOLLEEGIUM KOHTUMÄÄRUS Kohtuasja number 3-18-1432"
        ),
        "estleg:decisionLink": {
            "@value": PIN_2020_DECISION_LINK,
            "@type": "xsd:anyURI",
        },
    }
    assert gcd.backfill_rk_identity(node) is True
    assert node["estleg:rikosUrl"]["@value"] == PIN_2020_RIKOS
    assert node["estleg:chamber"] == "Halduskolleegium"
    assert node["estleg:decisionLink"]["@value"] == PIN_2020_DECISION_LINK
    assert gcd.backfill_rk_identity(node) is False


def test_generate_schema_nodes_declare_rikos_url_and_chamber():
    by_id = {n["@id"]: n for n in gcd.generate_schema_nodes() if "@id" in n}
    rikos = by_id["estleg:rikosUrl"]
    assert rikos["rdfs:range"]["@id"] == "xsd:anyURI"
    assert rikos["rdfs:domain"]["@id"] == "estleg:CourtDecision"
    chamber = by_id["estleg:chamber"]
    assert chamber["rdfs:range"]["@id"] == "xsd:string"
    assert chamber["rdfs:domain"]["@id"] == "estleg:CourtDecision"


def test_committed_2020_pin_has_rikos_url_and_chamber():
    graph = json.loads(RK_2020.read_text(encoding="utf-8"))["@graph"]
    node = next(n for n in graph if n.get("@id") == PIN_2020_ID)
    assert node["estleg:rikObjectId"] == PIN_2020_OID
    assert node["estleg:rikosUrl"]["@value"] == PIN_2020_RIKOS
    assert node["estleg:rikosUrl"]["@type"] == "xsd:anyURI"
    assert node["estleg:chamber"] == "Halduskolleegium"
    assert node["estleg:decisionLink"]["@value"] == PIN_2020_DECISION_LINK


def test_curia_schema_ecli_identifier_has_no_domain():
    graph = json.loads(CURIA_SCHEMA.read_text(encoding="utf-8"))["@graph"]
    node = next(n for n in graph if n.get("@id") == "estleg:ecliIdentifier")
    assert "rdfs:domain" not in node
    assert node["rdfs:range"]["@id"] == "xsd:string"
    assert "ECLI" in node["rdfs:comment"]


def test_committed_rk_schema_declares_rikos_url_and_chamber():
    graph = json.loads(RK_SCHEMA.read_text(encoding="utf-8"))["@graph"]
    by_id = {n["@id"]: n for n in graph if isinstance(n, dict) and n.get("@id")}
    assert by_id["estleg:rikosUrl"]["rdfs:range"]["@id"] == "xsd:anyURI"
    assert by_id["estleg:chamber"]["rdfs:range"]["@id"] == "xsd:string"
    assert "rdfs:domain" not in by_id["estleg:rikosUrl"] or (
        by_id["estleg:rikosUrl"]["rdfs:domain"]["@id"] == "estleg:CourtDecision"
    )
