"""#524 — ProvisionVersion self-citing citation fields; #532 lag helper."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_provision_versions as gpv
import validate_all
from generate_provision_versions import (
    backfill_version_sidecars,
    provision_ref_from_iri,
    rt_url_from_redaction_id,
    stamp_version_citation,
)

REPO = Path(__file__).resolve().parent.parent
PKS_SIDECAR = REPO / "krr_outputs" / "provision_versions" / "perekonnaseadus.jsonld"
PKS_VERSION_ID = "estleg:PKS_Par_1_v22303"


def test_rt_url_from_redaction_id() -> None:
    assert rt_url_from_redaction_id("22303") == "https://www.riigiteataja.ee/akt/22303"
    assert rt_url_from_redaction_id(" 13231473 ") == (
        "https://www.riigiteataja.ee/akt/13231473"
    )


def test_provision_ref_from_iri() -> None:
    assert provision_ref_from_iri("estleg:PKS_Par_1") == "PKS § 1"
    assert provision_ref_from_iri("estleg:KARIST_2_Osa2_Par_88_Lg_1") == (
        "KARIST_2 § 88 lg 1"
    )
    assert provision_ref_from_iri("estleg:KARIST_2_Osa2_Par_88") == "KARIST_2 § 88"
    assert provision_ref_from_iri("estleg:PKS_Par_22_1") == "PKS § 22_1"
    assert provision_ref_from_iri("estleg:SOS_Par_54_to_55") == "SOS § 54–55"
    assert provision_ref_from_iri("estleg:TsK_Par_1_to_94") == "TsK § 1–94"


def test_stamp_version_citation_is_idempotent() -> None:
    node = {
        "@id": "estleg:PKS_Par_1_v22303",
        "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
        "estleg:versionOf": {"@id": "estleg:PKS_Par_1"},
        "estleg:versionRedactionId": "22303",
    }
    assert stamp_version_citation(
        node, act_title="Perekonnaseadus", act_iri="estleg:PKS_Map_2026"
    ) is True
    assert node["estleg:rtUrl"] == "https://www.riigiteataja.ee/akt/22303"
    assert node["estleg:sourceAct"] == "Perekonnaseadus"
    assert node["estleg:provisionRef"] == "PKS § 1"
    assert node["estleg:partOfAct"] == {"@id": "estleg:PKS_Map_2026"}
    snapshot = json.loads(json.dumps(node))
    assert stamp_version_citation(
        node, act_title="Perekonnaseadus", act_iri="estleg:PKS_Map_2026"
    ) is False
    assert node == snapshot


def test_pks_par_1_v22303_is_citation_complete() -> None:
    assert PKS_SIDECAR.is_file(), PKS_SIDECAR
    doc = json.loads(PKS_SIDECAR.read_text(encoding="utf-8"))
    node = next(
        n
        for n in doc["@graph"]
        if isinstance(n, dict) and n.get("@id") == PKS_VERSION_ID
    )
    assert node["estleg:rtUrl"] == "https://www.riigiteataja.ee/akt/22303"
    source = node["estleg:sourceAct"]
    if isinstance(source, dict):
        source = source.get("@value")
    assert isinstance(source, str)
    assert "Perekonnaseadus" in source
    assert node["estleg:provisionRef"] == "PKS § 1"
    part = node.get("estleg:partOfAct")
    if isinstance(part, dict):
        assert part.get("@id")
    else:
        pytest.fail("partOfAct missing on PKS_Par_1_v22303 after backfill")


def test_pks_sidecar_rturl_when_redaction_id_present() -> None:
    doc = json.loads(PKS_SIDECAR.read_text(encoding="utf-8"))
    missing = 0
    for node in doc.get("@graph") or []:
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:ProvisionVersion" not in types:
            continue
        rid = node.get("estleg:versionRedactionId")
        if isinstance(rid, dict):
            rid = rid.get("@value")
        if not rid:
            continue
        if not node.get("estleg:rtUrl"):
            missing += 1
    assert missing == 0


def test_version_layer_lag_detects_behind_kehtiv() -> None:
    behind = {
        "@graph": [
            {
                "@type": ["estleg:ProvisionVersion"],
                "estleg:versionValidFrom": {
                    "@value": "2026-05-22",
                    "@type": "xsd:date",
                },
            },
            {
                "@type": ["estleg:ProvisionVersion"],
                "estleg:versionValidFrom": {
                    "@value": "2026-05-01",
                    "@type": "xsd:date",
                },
            },
        ]
    }
    assert validate_all.version_layer_lag(behind, "2026-05-24") == "2026-05-22"
    current = {
        "@graph": [
            {
                "@type": ["estleg:ProvisionVersion"],
                "estleg:versionValidFrom": {
                    "@value": "2026-05-24",
                    "@type": "xsd:date",
                },
            }
        ]
    }
    assert validate_all.version_layer_lag(current, "2026-05-24") is None


def test_backfill_version_sidecars_stamps_and_is_idempotent(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    pv = krr / "provision_versions"
    pv.mkdir(parents=True)
    (krr / "fixture_law_peep.json").write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:FIX_Map_2026",
                        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                        "dc:source": "Fikseeritud seadus",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    sidecar = {
        "@graph": [
            {
                "@id": "estleg:ProvisionVersions_fixture_law_Map",
                "@type": ["owl:Ontology"],
                "dc:source": "Fikseeritud seadus",
            },
            {
                "@id": "estleg:FIX_Par_1_v111",
                "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
                "estleg:versionOf": {"@id": "estleg:FIX_Par_1"},
                "estleg:versionRedactionId": "111",
            },
        ]
    }
    path = pv / "fixture_law.jsonld"
    path.write_text(json.dumps(sidecar), encoding="utf-8")
    stats = backfill_version_sidecars(pv, krr)
    assert stats["files"] == 1
    assert stats["nodes"] == 1
    doc = json.loads(path.read_text(encoding="utf-8"))
    node = doc["@graph"][1]
    assert node["estleg:rtUrl"] == "https://www.riigiteataja.ee/akt/111"
    assert node["estleg:sourceAct"] == "Fikseeritud seadus"
    assert node["estleg:provisionRef"] == "FIX § 1"
    assert node["estleg:partOfAct"] == {"@id": "estleg:FIX_Map_2026"}
    again = backfill_version_sidecars(pv, krr)
    assert again["files"] == 0
    assert again["nodes"] == 0


def test_synthesise_versions_still_self_citing() -> None:
    """Generator path stays citation-complete after helper extraction."""
    target = gpv.LawTarget(
        slug="fixture_law",
        title="Fikseeritud seadus",
        prefix="FIX",
        provisions={"1": "estleg:FIX_Par_1"},
        peep_files=[],
        act_iri="estleg:FIX_Map_2026",
    )
    redaction = gpv.Redaction("111", "2010-01-01", None, "/akt/111.xml")
    nodes = gpv.synthesise_versions(target, [(redaction, {"1": "Algtekst."})])
    assert nodes[0]["estleg:rtUrl"] == "https://www.riigiteataja.ee/akt/111"
    assert nodes[0]["estleg:provisionRef"] == "FIX § 1"
