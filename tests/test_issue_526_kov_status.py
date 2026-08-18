"""#526 — abolished vs current EHAK status on KOV issuers/acts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from enrich_kov_layer1 import (
    issuer_status_map,
    stamp_kov_act_municipality_status,
)
from kov_registry import municipality_status


def test_municipality_status_rules():
    assert municipality_status("auto-match") == "current"
    assert municipality_status("auto-match", "") == "current"
    assert municipality_status("haldusreform-2017", "Abja") == "abolished"
    assert municipality_status("manual-review", "Ahja") == "abolished"


def test_stamp_act_from_issuer_map():
    act = {
        "@id": "estleg:Reg_1_Map_2026",
        "estleg:enactedBy": {"@id": "estleg:Issuer_abja_vallavolikogu"},
    }
    assert stamp_kov_act_municipality_status(
        act, {"abja_vallavolikogu": "abolished"}
    )
    assert act["estleg:municipalityStatus"] == "abolished"
    assert stamp_kov_act_municipality_status(
        act, {"abja_vallavolikogu": "abolished"}
    ) is False


def test_committed_issuers_all_have_status():
    path = (
        Path(__file__).resolve().parent.parent
        / "krr_outputs"
        / "issuers_kov_peep.json"
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    statuses = issuer_status_map(doc)
    issuers = [
        n
        for n in doc["@graph"]
        if isinstance(n, dict) and n.get("@id", "").startswith("estleg:Issuer_")
    ]
    assert issuers
    assert len(statuses) == len(issuers)
    assert "abolished" in statuses.values()
    assert "current" in statuses.values()
    abja = statuses.get("abja_vallavolikogu")
    assert abja == "abolished"
    tallinn = statuses.get("tallinna_linnavolikogu")
    assert tallinn == "current"


def test_committed_kov_act_inherits_issuer_status():
    path = (
        Path(__file__).resolve().parent.parent
        / "krr_outputs"
        / "regulations"
        / "kov"
        / "kaina_vallavolikogu"
        / "hallatava_asutuse_kaina_spordikeskus_moodustamine_t1013354_peep.json"
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    from generate_similarity_index import find_kov_act_node

    act = find_kov_act_node(doc)
    assert act is not None
    assert act["estleg:municipalityStatus"] == "abolished"
