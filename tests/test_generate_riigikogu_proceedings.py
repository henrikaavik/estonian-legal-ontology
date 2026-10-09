"""#717 — Riigikogu proceedings ingest (api.riigikogu.ee, CC BY-SA 3.0)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
import requests

from estleg import generate_draft_legislation as gdl
from estleg import generate_riigikogu_proceedings as rk
from estleg.estleg_common import ALLOWED_HTTP_HOSTS

REPO = Path(__file__).resolve().parent.parent

MEMBERSHIPS = [
    {"membership": 14, "startDate": "2019-04-04", "endDate": "2023-02-23"},
    {"membership": 15, "startDate": "2023-04-10", "endDate": "2027-02-25"},
]


def _draft(draft_id: str, title: str, day: str, eis: str = "", dtype: str = "AmendmentBill") -> dict:
    node = {
        "@id": draft_id,
        "@type": ["owl:NamedIndividual", "estleg:DraftLegislation"],
        "rdfs:label": title,
        "estleg:draftType": {"@id": f"estleg:DraftType_{dtype}"},
        "estleg:publicationDate": {"@value": day, "@type": "xsd:date"},
    }
    if eis:
        node["estleg:eisNumber"] = eis
    return node


def _row(uuid: str, mark: int, title: str, initiated: str, membership: int = 15, code: str = "SE") -> dict:
    return {
        "uuid": uuid,
        "title": title,
        "mark": mark,
        "membership": membership,
        "draftTypeCode": code,
        "activeDraftStage": "VASTU_VOETUD",
        "activeDraftStatus": "AVALDATUD_RIIGITEATAJAS",
        "proceedingStatus": "PROCESSED",
        "activeDraftStatusDate": "2024-06-01",
        "initiated": initiated,
    }


def test_api_host_is_allow_listed():
    assert "api.riigikogu.ee" in ALLOWED_HTTP_HOSTS
    assert rk.API_BASE == "https://api.riigikogu.ee"


def test_parse_marks_and_normalize_title():
    assert rk.parse_marks("Liiklusseaduse muutmise seaduse eelnõu (835 SE)") == [(835, "SE")]
    assert rk.parse_marks("… seaduse eelnõu 236SE põhimõttelise …") == [(236, "SE")]
    assert rk.parse_marks("§ 32 1 muutmise seadus") == []
    assert rk.normalize_title("Liiklusseaduse muutmise seaduse eelnõu (835 SE)") == rk.normalize_title(
        "Liiklusseaduse muutmise seadus"
    )


def test_membership_on():
    assert rk.membership_on("2020-05-01", MEMBERSHIPS) == 14
    assert rk.membership_on("2023-03-01", MEMBERSHIPS) == 14  # between terms: the outgoing one
    assert rk.membership_on("2024-01-01", MEMBERSHIPS) == 15


def test_eis_numbers_from_notice_files_and_trim():
    detail = {
        "uuid": "u1",
        "mark": 897,
        "draftTypeCode": "SE",
        "membership": 15,
        "initiators": [{"name": "Vabariigi Valitsus", "type": "usergroup", "_links": {}}],
        "descriptors": [{"edid": 5945, "text": "haldusjärelevalve"}],
        "readings": [
            {"readingCode": "INITIATION", "proceedingEvents": [
                {"date": "2026-05-04T15:13:06.4", "status": "ALGATATUD"},
                {"date": "2026-05-12T14:00:00", "sittingTitle": "Komisjoni istung"},
            ]},
        ],
        "opinions": [{"files": [{"fileName": "[EIS]_Eelnõu_esitamine__RIIGIKOGU-26-0522_-_X_(897_SE).txt"}]}],
        "texts": [{"file": {"fileName": "20260427_EN_KüTS_muutmine.asice"}}],
    }
    trimmed = rk.trim_detail(detail)
    assert trimmed["eisNumbers"] == ["RIIGIKOGU/26-0522"]
    assert trimmed["readings"] == [{"readingCode": "INITIATION", "events": [{"date": "2026-05-04T15:13:06.4", "status": "ALGATATUD"}]}]
    assert trimmed["initiators"] == [{"name": "Vabariigi Valitsus", "type": "usergroup"}]


def test_candidate_joins_mark_related_document_and_title_date():
    drafts = [
        _draft("estleg:Draft_A", "Liiklusseaduse muutmise seaduse eelnõu (835 SE)", "2026-02-01"),
        _draft("estleg:Draft_B", "X seaduse 835 SE rakendusaktide eelnõud", "2026-02-01"),
        _draft("estleg:Draft_C", "Kliimaseaduse eelnõu", "2024-01-10", dtype="Bill"),
        _draft("estleg:Draft_D", "Kliimaseaduse eelnõu", "2020-01-10", dtype="Bill"),  # outside window
    ]
    listing = [
        _row("u-835", 835, "Liiklusseaduse muutmise seadus", "2026-03-01"),
        _row("u-kliima", 400, "Kliimaseadus", "2024-05-01"),
    ]
    joins, stats = rk.candidate_joins(drafts, listing, MEMBERSHIPS)
    assert {(j.draft_id, j.rk_uuid, j.method) for j in joins} == {
        ("estleg:Draft_A", "u-835", rk.JOIN_MARK),
        ("estleg:Draft_C", "u-kliima", rk.JOIN_TITLE_DATE),
    }
    assert stats["mark_skipped_related_document"] == 1


def test_resolve_joins_checks_government_and_prefers_eis_number():
    drafts = [
        _draft("estleg:Draft_C", "Kliimaseaduse eelnõu", "2024-01-10", eis="KLIM/24-0001", dtype="Bill"),
        _draft("estleg:Draft_E", "Other", "2024-01-10", eis="RIIGIKOGU/24-0002"),
    ]
    candidates = [rk.Join("estleg:Draft_C", "u-kliima", rk.JOIN_TITLE_DATE)]
    member_bill = {"uuid": "u-kliima", "initiators": [{"name": "Eesti Reformierakonna fraktsioon"}], "eisNumbers": []}
    final, stats = rk.resolve_joins(drafts, candidates, {"u-kliima": member_bill})
    assert final == {}
    assert stats["title_date_rejected_not_government"] == 1
    gov = {"uuid": "u-kliima", "initiators": [{"name": "Vabariigi Valitsus"}], "eisNumbers": ["RIIGIKOGU/24-0002"]}
    final, _ = rk.resolve_joins(drafts, candidates, {"u-kliima": gov})
    assert final["estleg:Draft_C"].method == rk.JOIN_TITLE_DATE
    assert final["estleg:Draft_E"] == rk.Join("estleg:Draft_E", "u-kliima", rk.JOIN_EIS_NUMBER)
    # A mark join wins over a conflicting EIS-number join.
    marked = [rk.Join("estleg:Draft_E", "u-other", rk.JOIN_MARK)]
    other = {"uuid": "u-other", "initiators": [], "eisNumbers": []}
    final, stats = rk.resolve_joins(drafts, marked, {"u-kliima": gov, "u-other": other})
    assert final["estleg:Draft_E"].method == rk.JOIN_MARK
    assert stats["eis_number_conflicts_with_mark_kept_mark"] == 1


def test_step_specs_map_statuses_and_stamp_the_licence():
    detail = {
        "uuid": "u1",
        "mark": 897,
        "draftTypeCode": "SE",
        "readings": [
            {"readingCode": "INITIATION", "events": [{"date": "2026-05-04T15:13", "status": "ALGATATUD"}]},
            {"readingCode": "FIRST_READING", "events": [{"date": "2026-05-20T14:00", "status": "I_LUGEMINE_LOPETATUD"}]},
            {"readingCode": "THIRD_READING", "events": [
                {"date": "2026-09-23T10:00", "status": "VASTU_VOETUD"},
                {"date": "2026-09-23T11:00", "status": "VASTU_VOETUD"},  # same day dup
            ]},
        ],
    }
    specs = rk.step_specs_for(detail, rk.JOIN_MARK)
    assert [(s["phase"], s["day"]) for s in specs] == [
        ("RiigikoguProceeding", "2026-05-04"),
        ("FirstReading", "2026-05-20"),
        ("Enacted", "2026-09-23"),
    ]
    assert all(s["extra"]["dcterms:license"] == {"@id": rk.LICENSE_IRI} for s in specs)
    assert rk.event_phase("SECOND_READING", "TAGASI_VOETUD") == "Withdrawn"
    assert rk.event_phase("", "VALJA_LANGENUD_KOOSEISU_LOPPEMISEGA") == "Lapsed"
    assert rk.event_phase("", "VALJA_KUULUTAMATA_JAETUD") == "Reconsideration"


def test_step_specs_fall_back_to_the_listing_stage():
    detail = {"uuid": "u1", "mark": 5, "draftTypeCode": "SE", "readings": [], "activeDraftStage": "TAGASI_LYKATUD"}
    specs = rk.step_specs_for(detail, rk.JOIN_MARK, {"activeDraftStatusDate": "2012-01-05"})
    assert [(s["phase"], s["day"]) for s in specs] == [("Rejected", "2012-01-05")]


def test_eurovoc_codes_classic_and_looked_up(tmp_path):
    cache = tmp_path / "rk"
    (cache / "eurovoc").mkdir(parents=True)
    (cache / "eurovoc" / "454791.json").write_text(
        json.dumps({"edid": 454791, "code": "c_04ae3ba8", "text": "infoturve"}), encoding="utf-8"
    )
    client = rk.RiigikoguClient(cache_dir=cache, offline=True)
    (cache / "eurovoc" / "999999.json").write_text(json.dumps({"edid": 999999, "code": "invalid"}))
    detail = {"descriptors": [{"edid": 454791}, {"edid": 5945}, {"edid": 999999}]}
    stats: Counter = Counter()
    assert rk.eurovoc_codes(detail, client, stats) == ["5945", "c_04ae3ba8"]
    assert stats["descriptor_without_eurovoc_code"] == 1  # 999999 has an invalid code
    assert client.requests == 0


def test_apply_to_draft_sets_and_clears_the_riigikogu_layer():
    draft = _draft("estleg:Draft_A", "Liiklusseaduse muutmise seaduse eelnõu (835 SE)", "2026-02-01")
    steps = gdl.merge_steps(draft["@id"], [], [gdl.eis_step_spec("submission", "2026-02-01")])
    detail = {
        "uuid": "u-835", "mark": 835, "draftTypeCode": "SE", "membership": 15,
        "readings": [{"readingCode": "INITIATION", "events": [{"date": "2026-03-01", "status": "ALGATATUD"}]}],
    }
    join = rk.Join(draft["@id"], "u-835", rk.JOIN_MARK)
    new = rk.apply_to_draft(draft, steps, join, detail, None, ["5945"])
    assert draft["estleg:riigikoguMark"] == "835 SE"
    assert draft["estleg:riigikoguMembership"] == {"@value": "15", "@type": "xsd:integer"}
    assert draft["dcterms:subject"] == [{"@id": "http://eurovoc.europa.eu/5945"}]
    assert draft["estleg:subjectSource"] == "riigikogu"
    assert [gdl.step_phase(s) for s in new] == ["Submission", "RiigikoguProceeding"]
    assert gdl.derive_phase(new) == "RiigikoguProceeding"
    # A rerun without the join retracts the whole Riigikogu layer.
    cleared = rk.apply_to_draft(draft, new, None, None, None, [])
    assert [gdl.step_phase(s) for s in cleared] == ["Submission"]
    assert "estleg:riigikoguMark" not in draft and "dcterms:subject" not in draft


def test_offline_client_never_fetches(tmp_path):
    client = rk.RiigikoguClient(cache_dir=tmp_path, offline=True)
    assert client.cached("x.json", "/api/memberships") is None
    with pytest.raises(RuntimeError):
        client.fetch("/api/memberships")


def test_listing_splits_a_404_window_and_replays_offline(tmp_path, monkeypatch):
    rows = [{"uuid": f"u{i}", "mark": i} for i in range(12)]
    bad = 7

    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return self._payload

    def fake_get(url, params=None, timeout=None):
        page, size = params["page"], params["size"]
        window = rows[page * size:(page + 1) * size]
        if any(int(r["uuid"][1:]) == bad for r in window):
            return Resp(404, {})
        return Resp(200, {"_embedded": {"content": window}, "page": {"totalElements": len(rows)}})

    monkeypatch.setattr(rk, "allowed_get", fake_get)
    monkeypatch.setattr(rk, "LIST_SPLIT_SIZES", (10, 5, 1))
    client = rk.RiigikoguClient(cache_dir=tmp_path, rate_delay=0)
    got, skipped = rk.load_draft_listing(client)
    assert sorted(r["uuid"] for r in got) == sorted(f"u{i}" for i in range(12) if i != bad)
    assert skipped == [bad]
    offline = rk.RiigikoguClient(cache_dir=tmp_path, offline=True)
    again, skipped_again = rk.load_draft_listing(offline)
    assert sorted(r["uuid"] for r in again) == sorted(r["uuid"] for r in got)
    assert skipped_again == [bad]


# --------------------------------------------------------------------------- #
# Committed layer contract
# --------------------------------------------------------------------------- #


def test_committed_cache_records_the_licence():
    doc = json.loads((REPO / "data" / "riigikogu" / "LICENSE.json").read_text(encoding="utf-8"))
    assert doc["license"] == rk.LICENSE_IRI
    assert "CC BY-SA 3.0" in doc["note"]


def test_committed_riigikogu_steps_are_licensed_and_joined():
    docs = gdl.load_phase_peeps(REPO / "krr_outputs" / "eelnoud")
    rk_steps = 0
    joined = 0
    for doc in docs.values():
        for draft, steps in gdl.drafts_with_steps(doc):
            mine = [s for s in steps if s.get("estleg:derivationMethod") in rk.JOIN_PRIORITY]
            if mine:
                joined += 1
                assert draft.get("estleg:riigikoguUuid"), draft["@id"]
                assert draft.get("estleg:riigikoguMark"), draft["@id"]
            for step in mine:
                rk_steps += 1
                assert step["dcterms:license"] == {"@id": rk.LICENSE_IRI}
                assert step["estleg:riigikoguStatus"]
                assert step["dcterms:source"]["@id"].startswith(rk.API_BASE + "/api/volumes/drafts/")
            if draft.get("estleg:subjectSource") == "riigikogu":
                assert mine, draft["@id"]
    assert joined >= 500
    assert rk_steps >= joined
