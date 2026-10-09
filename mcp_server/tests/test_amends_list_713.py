"""List-valued ``estleg:amends`` on amendment events (#713).

Since #713 the generator writes ``amends`` as the act root(s) first, then the
provision IRIs the amending act touched. A multipart act lists one root per
part. The row must name the act and expose the provisions instead of
collapsing the list to "".
"""

from __future__ import annotations

import pytest

from estleg_mcp import data, server

RT = "https://www.riigiteataja.ee/akt/"
ROOTS = [{"@id": "estleg:KARIST_2_Osa1"}, {"@id": "estleg:KARIST_2_Osa2"}]


def _date(value: str) -> dict:
    return {"@value": value, "@type": "xsd:date"}


CHAIN = [
    {"@id": "estleg:AmendmentChain_KARIST_2", "@type": ["owl:Ontology"]},
    {"@id": "estleg:Amendment_KARIST_2_a", "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
     "rdfs:label": "Muudatus: RT I, 2004, 46, 329", "estleg:rtReference": "RT I, 2004, 46, 329",
     "estleg:entryIntoForce": _date("2004-07-01"),
     "estleg:amends": [*ROOTS, {"@id": "estleg:KARIST_2_Osa1_Par_7"},
                       {"@id": "estleg:KARIST_2_Osa1_Par_7_Lg_1"}]},
    {"@id": "estleg:Amendment_KARIST_2_b", "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
     "rdfs:label": "Muudatus: RT I, 2002, 44, 284", "estleg:rtReference": "RT I, 2002, 44, 284",
     "estleg:entryIntoForce": _date("2002-09-01"), "estleg:amends": list(ROOTS)},
    {"@id": "estleg:Amendment_KARIST_2_vf_20040701", "@type": ["estleg:AmendmentEvent"],
     "rdfs:label": "Muudatus (versioonikiht) 2004-07-01",
     "estleg:entryIntoForce": _date("2004-07-01"), "estleg:amends": list(ROOTS),
     "estleg:resultedInVersion": [{"@id": "estleg:KARIST_2_Osa1_Par_7_v1"}]},
]


@pytest.fixture
def kars(monkeypatch):
    rec = data.LawRecord("karistusseadustik_osa1", ["karistusseadustik_osa1_peep.json"],
                         "Karistusseadustik", "KARIST_2")
    monkeypatch.setattr(data, "_amendment_graph_for", lambda _rec: list(CHAIN))
    monkeypatch.setattr(data, "rt_url_for_slug", lambda _slug: f"{RT}999")
    return rec


def test_act_root_count_is_the_shared_prefix() -> None:
    assert data._act_root_count(CHAIN) == 2
    assert data._act_root_count([]) == 0
    # A single-object payload (pre-#713 single-part chains) still counts one root.
    single = [{"@type": ["estleg:AmendmentEvent"], "estleg:amends": {"@id": "estleg:TOY_Map"}}]
    assert data._act_root_count(single) == 1


def test_amendment_events_name_the_act_and_expose_provisions(kars) -> None:
    rows = {r["event_id"]: r for r in data.amendment_events(kars)}
    a = rows["estleg:Amendment_KARIST_2_a"]
    assert a["amends"] == "estleg:KARIST_2_Osa1"  # was "" for a list payload
    assert a["amended_provisions"] == ["estleg:KARIST_2_Osa1_Par_7",
                                       "estleg:KARIST_2_Osa1_Par_7_Lg_1"]
    assert rows["estleg:Amendment_KARIST_2_b"]["amends"] == "estleg:KARIST_2_Osa1"
    assert rows["estleg:Amendment_KARIST_2_b"]["amended_provisions"] == []
    vf = rows["estleg:Amendment_KARIST_2_vf_20040701"]
    assert vf["amended_provisions"] == [] and vf["changed_provisions"] == 1


def test_window_rows_carry_provisions_inclusively(kars) -> None:
    rows = data.amendment_events_between(kars, "2004-07-01", "2004-07-01")
    assert [r["event_id"] for r in rows] == [
        "estleg:Amendment_KARIST_2_a", "estleg:Amendment_KARIST_2_vf_20040701"]
    assert rows[0]["rt_reference"] == "RT I, 2004, 46, 329"
    assert "estleg:KARIST_2_Osa1_Par_7" in rows[0]["amended_provisions"]


def test_amendment_history_tool_rows(kars, monkeypatch) -> None:
    monkeypatch.setattr(data, "resolve_law", lambda _law: kars)
    rows = server.amendment_history("KarS")
    assert all(r["amends"] == "estleg:KARIST_2_Osa1" for r in rows)
    assert {"amends", "amended_provisions"} <= set(rows[0])
