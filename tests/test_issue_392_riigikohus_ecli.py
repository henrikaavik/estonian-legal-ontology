"""#392 leftovers: unused prefixes, Riigikohus ECLI, EuroVoc label IDs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import estleg_common
import generate_court_decisions as gcd
from classify_eurovoc import EUROVOC_DOMAINS
from extract_legal_concepts import is_noise_term


def test_context_drops_unused_rdf_and_schema_prefixes():
    assert "rdf" not in estleg_common.CONTEXT
    assert "schema" not in estleg_common.CONTEXT
    text = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "fix_all_issues.py"
    ).read_text(encoding="utf-8")
    assert 'combined_context["schema"]' not in text


def test_mint_ecli_official_example():
    # e-justice.europa.eu EE: case 1-16-2798/84 → ECLI:EE:RK:2016:1.16.2798.84
    assert gcd.mint_riigikohus_ecli("1-16-2798/84", "2016-11-02") == (
        "ECLI:EE:RK:2016:1.16.2798.84"
    )


def test_mint_ecli_skips_pre_assignment():
    assert gcd.mint_riigikohus_ecli("3-1-1-20-15", "2015-12-01") is None
    assert gcd.mint_riigikohus_ecli("3-1-1-20-16", "2016-06-30") is None
    assert gcd.mint_riigikohus_ecli("3-1-1-20-16", "2016-07-01") == (
        "ECLI:EE:RK:2016:3.1.1.20.16"
    )


def test_decision_to_node_emits_ecli_for_modern_case():
    dec = {
        "case_nr": "1-16-2798/84",
        "object_id": "123",
        "date": "02.11.2016",
        "decision_type": "Kohtuotsus",
    }
    node = gcd.decision_to_node(dec, 2016, set())
    assert node is not None
    assert node["estleg:ecliIdentifier"] == "ECLI:EE:RK:2016:1.16.2798.84"


def test_decision_to_node_omits_ecli_before_2016():
    dec = {
        "case_nr": "3-1-1-20-15",
        "object_id": "99",
        "date": "01.12.2015",
        "decision_type": "Kohtuotsus",
    }
    node = gcd.decision_to_node(dec, 2015, set())
    assert node is not None
    assert "estleg:ecliIdentifier" not in node


def test_backfill_ecli_on_node_is_idempotent():
    node = {
        "@type": ["estleg:CourtDecision"],
        "estleg:caseNumber": "2-17-1234",
        "estleg:decisionDate": {"@value": "2017-03-01", "@type": "xsd:date"},
    }
    assert gcd.backfill_ecli_on_node(node) is True
    assert node["estleg:ecliIdentifier"] == "ECLI:EE:RK:2017:2.17.1234"
    assert gcd.backfill_ecli_on_node(node) is False


def test_committed_post_2016_decisions_have_ecli():
    path = (
        Path(__file__).resolve().parent.parent
        / "krr_outputs"
        / "riigikohus"
        / "riigikohus_2020_peep.json"
    )
    graph = json.loads(path.read_text(encoding="utf-8"))["@graph"]
    decisions = [
        n
        for n in graph
        if isinstance(n, dict)
        and "estleg:CourtDecision"
        in (n.get("@type") if isinstance(n.get("@type"), list) else [n.get("@type")])
    ]
    assert decisions, "no 2020 CourtDecision nodes"
    missing = [n.get("@id") for n in decisions if "estleg:ecliIdentifier" not in n]
    assert missing == [], f"{len(missing)} 2020 decisions lack ECLI (sample {missing[:3]})"
    sample = decisions[0]["estleg:ecliIdentifier"]
    assert sample.startswith("ECLI:EE:RK:2020:")


def test_uppercase_ta_et_are_not_stopwords():
    assert is_noise_term("ta", "TA") is False
    assert is_noise_term("et", "ET") is False


def test_eurovoc_stale_descriptor_ids_are_gone():
    # Ticket listed 4421/3611/2416 with wrong labels; #421 remapped to
    # official descriptors 4522 / 4050 / 538.
    assert "4421" not in EUROVOC_DOMAINS
    assert "3611" not in EUROVOC_DOMAINS
    assert "2416" not in EUROVOC_DOMAINS
    assert EUROVOC_DOMAINS["4522"][0] == "maritime-transport"
    assert EUROVOC_DOMAINS["4050"][0] == "social-security"
    assert EUROVOC_DOMAINS["538"][0] == "fundamental-rights"


def test_no_true_duplicate_rk_content_keys_in_2020():
    path = (
        Path(__file__).resolve().parent.parent
        / "krr_outputs"
        / "riigikohus"
        / "riigikohus_2020_peep.json"
    )
    graph = json.loads(path.read_text(encoding="utf-8"))["@graph"]
    keys: list[tuple[str, str, str]] = []
    for node in graph:
        if not isinstance(node, dict):
            continue
        types = node.get("@type")
        tlist = types if isinstance(types, list) else [types]
        if "estleg:CourtDecision" not in tlist:
            continue
        case = node.get("estleg:caseNumber") or ""
        date = node.get("estleg:decisionDate")
        if isinstance(date, dict):
            date = date.get("@value") or ""
        dtype = node.get("estleg:decisionType")
        if isinstance(dtype, dict):
            dtype = dtype.get("@id") or ""
        keys.append((case, str(date), str(dtype)))
    assert len(keys) == len(set(keys))
