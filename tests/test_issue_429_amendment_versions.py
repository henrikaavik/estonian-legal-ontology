"""#429: AmendmentEvents join the version layer; lastAmendmentDate follows it."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.generate_amendment_history import (
    collect_versions_by_date,
    link_amendments_to_versions,
    stamp_last_amendment_from_versions,
)
from estleg.validate_all import validate_last_amendment_matches_versions
from estleg import validate_all

REPO = Path(__file__).resolve().parent.parent
KARS_PEEP = REPO / "krr_outputs" / "karistusseadustik_osa1_peep.json"
KARS_VERS = REPO / "krr_outputs" / "provision_versions" / "karistusseadustik.jsonld"
KARS_AMEND = REPO / "krr_outputs" / "amendments" / "amendments_karistusseadustik.json"


def test_link_events_to_matching_version_dates() -> None:
    versions = {
        "@graph": [
            {
                "@id": "estleg:X_Par_1_v1",
                "@type": ["estleg:ProvisionVersion"],
                "estleg:versionValidFrom": {"@value": "2020-01-01", "@type": "xsd:date"},
            },
            {
                "@id": "estleg:X_Par_1_v2",
                "@type": ["estleg:ProvisionVersion"],
                "estleg:versionValidFrom": {"@value": "2024-06-01", "@type": "xsd:date"},
            },
        ]
    }
    amend = {
        "@graph": [
            {"@id": "estleg:AmendmentChain_X", "@type": ["owl:Ontology"]},
            {
                "@id": "estleg:Amendment_X_old",
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
                "estleg:entryIntoForce": {"@value": "2020-01-01", "@type": "xsd:date"},
                "estleg:amends": {"@id": "estleg:X_Map_2026"},
            },
        ]
    }
    by_date = collect_versions_by_date(versions)
    assert set(by_date) == {"2020-01-01", "2024-06-01"}
    link_amendments_to_versions(amend, by_date)
    events = [
        n
        for n in amend["@graph"]
        if "estleg:AmendmentEvent" in (n.get("@type") or [])
    ]
    assert len(events) == 2
    old = next(n for n in events if n["@id"] == "estleg:Amendment_X_old")
    assert old["estleg:resultedInVersion"] == [{"@id": "estleg:X_Par_1_v1"}]
    derived = next(n for n in events if n["@id"] != "estleg:Amendment_X_old")
    assert derived["estleg:resultedInVersion"] == [{"@id": "estleg:X_Par_1_v2"}]
    peep = {
        "@graph": [
            {"@id": "estleg:X_Map_2026", "@type": ["estleg:Act", "estleg:Law"]}
        ]
    }
    assert stamp_last_amendment_from_versions(peep, by_date) is True
    assert peep["@graph"][0]["estleg:lastAmendmentDate"]["@value"] == "2024-06-01"


def test_kars_last_amendment_matches_version_layer() -> None:
    peep = json.loads(KARS_PEEP.read_text(encoding="utf-8"))
    versions = json.loads(KARS_VERS.read_text(encoding="utf-8"))
    by_date = collect_versions_by_date(versions)
    latest = max(by_date)
    root = next(n for n in peep["@graph"] if n.get("@id") == "estleg:KARIST_2_Osa1")
    assert root["estleg:lastAmendmentDate"]["@value"] == latest
    amend = json.loads(KARS_AMEND.read_text(encoding="utf-8"))
    events = [
        n
        for n in amend["@graph"]
        if isinstance(n, dict) and "estleg:AmendmentEvent" in (n.get("@type") or [])
    ]
    linked_dates = {
        (n.get("estleg:entryIntoForce") or n.get("estleg:amendmentDate") or {}).get(
            "@value"
        )
        for n in events
        if n.get("estleg:resultedInVersion")
    }
    assert by_date.keys() <= linked_dates
    header = next(
        n for n in amend["@graph"] if str(n.get("@id", "")).startswith("estleg:AmendmentChain_")
    )
    assert header["estleg:totalAmendments"]["@value"] == str(len(by_date))


def test_validate_last_amendment_matches_versions(tmp_path: Path) -> None:
    validate_all.reset()
    versions = {
        "@graph": [
            {
                "@id": "estleg:X_Par_1_v1",
                "@type": ["estleg:ProvisionVersion"],
                "estleg:versionValidFrom": {"@value": "2024-06-01", "@type": "xsd:date"},
            }
        ]
    }
    (tmp_path / "provision_versions").mkdir()
    (tmp_path / "provision_versions" / "demo.jsonld").write_text(
        json.dumps(versions), encoding="utf-8"
    )
    peep = {
        "@graph": [
            {
                "@id": "estleg:X_Map_2026",
                "@type": ["estleg:Act", "estleg:Law"],
                "estleg:lastAmendmentDate": {
                    "@value": "2010-01-01",
                    "@type": "xsd:date",
                },
            }
        ]
    }
    (tmp_path / "demo_peep.json").write_text(json.dumps(peep), encoding="utf-8")
    validate_last_amendment_matches_versions(tmp_path)
    assert any("lastAmendmentDate" in msg for msg in validate_all.errors)
