"""Version-layer AmendmentEvents always carry ``estleg:amends`` (#429 repair).

``link_amendments_to_versions`` used to copy ``amends`` from the first
RT-derived event in a chain. Chains with no RT-derived event (178 files,
4,843 ``_vf_`` events) therefore minted events without it, failing
``AmendmentEventShape`` (``estleg:amends sh:minCount 1``).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from estleg.generate_amendment_history import (
    link_amendments_to_versions,
    relink_version_events,
    stamp_version_event_amends,
)

REPO = Path(__file__).resolve().parent.parent
AMENDMENTS_DIR = REPO / "krr_outputs" / "amendments"

_VERSIONS = {
    "2020-01-01": ["estleg:X_Par_1_v1"],
    "2024-06-01": ["estleg:X_Par_1_v2"],
}


def _events(doc: dict) -> list[dict]:
    return [
        n
        for n in doc["@graph"]
        if isinstance(n, dict) and "estleg:AmendmentEvent" in (n.get("@type") or [])
    ]


def _vf_event(prefix: str, date: str, **extra: object) -> dict:
    return {
        "@id": f"estleg:Amendment_{prefix}_vf_{date.replace('-', '')}",
        "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
        "estleg:entryIntoForce": {"@value": date, "@type": "xsd:date"},
        "rdfs:label": f"Muudatus (versioonikiht) {date}",
        **extra,
    }


def test_chain_without_rt_events_gets_amends_on_minted_events() -> None:
    doc = {
        "@graph": [
            {"@id": "estleg:AmendmentChain_X", "@type": ["owl:Ontology"]},
            {
                "@id": "estleg:AmendmentLink_Draft_1_X",
                "@type": ["owl:NamedIndividual", "estleg:ProposedAmendment"],
                "estleg:proposesToAmend": {"@id": "estleg:X_Map"},
            },
        ]
    }
    link_amendments_to_versions(doc, _VERSIONS, amends_target={"@id": "estleg:X_Map"})
    events = _events(doc)
    assert len(events) == 2
    for event in events:
        assert event["estleg:amends"] == {"@id": "estleg:X_Map"}
        # Same key position as RT-derived events: right after @type.
        assert list(event)[:3] == ["@id", "@type", "estleg:amends"]
    # The proposal is never given an effected-amendment predicate.
    assert "estleg:amends" not in doc["@graph"][1]


def test_link_without_target_preserves_copied_amends_shape() -> None:
    doc = {
        "@graph": [
            {"@id": "estleg:AmendmentChain_X", "@type": ["owl:Ontology"]},
            {
                "@id": "estleg:Amendment_X_old",
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
                "estleg:amends": {"@id": "estleg:X_Map"},
                "estleg:entryIntoForce": {"@value": "2020-01-01", "@type": "xsd:date"},
            },
        ]
    }
    link_amendments_to_versions(doc, _VERSIONS)
    minted = next(e for e in _events(doc) if "_vf_" in e["@id"])
    assert minted["estleg:amends"] == {"@id": "estleg:X_Map"}


def test_multipart_target_is_a_list() -> None:
    target = [{"@id": "estleg:X_Osa1"}, {"@id": "estleg:X_Osa2"}]
    doc = {"@graph": [{"@id": "estleg:AmendmentChain_X", "@type": ["owl:Ontology"]}]}
    link_amendments_to_versions(doc, _VERSIONS, amends_target=target)
    assert all(e["estleg:amends"] == target for e in _events(doc))


def test_stamp_is_idempotent_and_keeps_existing_amends() -> None:
    doc = {
        "@graph": [
            {"@id": "estleg:AmendmentChain_X", "@type": ["owl:Ontology"]},
            _vf_event("X", "2020-01-01"),
            _vf_event("X", "2021-01-01", **{"estleg:amends": {"@id": "estleg:Other"}}),
            {
                "@id": "estleg:Amendment_X_abc123",
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
                "estleg:amends": {"@id": "estleg:X_Map"},
            },
        ]
    }
    untouched = copy.deepcopy(doc["@graph"][2:])
    assert stamp_version_event_amends(doc, {"@id": "estleg:X_Map"}) == 1
    assert doc["@graph"][1]["estleg:amends"] == {"@id": "estleg:X_Map"}
    assert doc["@graph"][2:] == untouched
    snapshot = copy.deepcopy(doc)
    assert stamp_version_event_amends(doc, {"@id": "estleg:X_Map"}) == 0
    assert doc == snapshot


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_relink_resolves_act_roots_from_peeps_and_is_idempotent(tmp_path: Path) -> None:
    amend_dir = tmp_path / "amendments"
    amend_dir.mkdir()
    single_peep = tmp_path / "demo_seadus_peep.json"
    _write(single_peep, {"@graph": [{"@id": "estleg:DEMO_Map", "@type": ["estleg:Act", "estleg:Law"]}]})
    parts = []
    for n in (2, 1, 10):  # unordered on purpose: payload follows osa order
        peep = tmp_path / f"multi_seadustik_osa{n}_peep.json"
        _write(peep, {"@graph": [{"@id": f"estleg:MULTI_Osa{n}", "@type": ["estleg:Part"]}]})
        parts.append(peep)
    _write(
        amend_dir / "amendments_demo_seadus.json",
        {"@graph": [{"@id": "estleg:AmendmentChain_DEMO"}, _vf_event("DEMO", "2020-01-01")]},
    )
    _write(
        amend_dir / "amendments_multi_seadustik.json",
        {"@graph": [{"@id": "estleg:AmendmentChain_multi"}, _vf_event("multi", "2020-01-01")]},
    )
    already_ok = amend_dir / "amendments_ok.json"
    _write(
        already_ok,
        {"@graph": [_vf_event("OK", "2020-01-01", **{"estleg:amends": {"@id": "estleg:OK_Map"}})]},
    )
    ok_bytes = already_ok.read_bytes()

    stats = relink_version_events(amend_dir, [single_peep, *parts])
    assert stats == {
        "files_scanned": 3,
        "files_changed": 2,
        "events_stamped": 2,
        "files_unresolved": 0,
    }
    single = json.loads((amend_dir / "amendments_demo_seadus.json").read_text(encoding="utf-8"))
    assert single["@graph"][1]["estleg:amends"] == {"@id": "estleg:DEMO_Map"}
    multi = json.loads((amend_dir / "amendments_multi_seadustik.json").read_text(encoding="utf-8"))
    assert multi["@graph"][1]["estleg:amends"] == [
        {"@id": "estleg:MULTI_Osa1"},
        {"@id": "estleg:MULTI_Osa2"},
        {"@id": "estleg:MULTI_Osa10"},
    ]
    assert already_ok.read_bytes() == ok_bytes

    before = {p.name: p.read_bytes() for p in amend_dir.iterdir()}
    again = relink_version_events(amend_dir, [single_peep, *parts])
    assert again["files_changed"] == 0 and again["events_stamped"] == 0
    assert {p.name: p.read_bytes() for p in amend_dir.iterdir()} == before


def test_relink_reports_unresolved_chain(tmp_path: Path) -> None:
    amend_dir = tmp_path / "amendments"
    amend_dir.mkdir()
    chain = amend_dir / "amendments_orphan.json"
    _write(chain, {"@graph": [_vf_event("ORPHAN", "2020-01-01")]})
    raw = chain.read_bytes()
    stats = relink_version_events(amend_dir, [])
    assert stats["files_unresolved"] == 1
    assert chain.read_bytes() == raw


@pytest.mark.corpus
def test_corpus_every_amendment_event_has_amends() -> None:
    files = sorted(AMENDMENTS_DIR.glob("amendments_*.json"))
    assert len(files) > 1000, "amendment chains not populated?"
    missing = [
        f"{path.name}:{node.get('@id')}"
        for path in files
        for node in _events(json.loads(path.read_text(encoding="utf-8")))
        if not node.get("estleg:amends")
    ]
    assert not missing, f"{len(missing)} AmendmentEvents lack estleg:amends: {missing[:5]}"
