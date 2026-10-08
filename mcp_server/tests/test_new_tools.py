"""#714 contract tests: the four new tools, citations on every row, truncation.

Fixture tests monkeypatch the data layer (no corpus needed) and pin the exact
contract; corpus tests check the same contract against the committed per-file
sidecars (never ``combined_ontology.jsonld`` or the LFS analytical overlay).
"""

from __future__ import annotations

import pytest

from estleg_mcp import data, i18n, server

try:
    data.corpus_root()
    _CORPUS_AVAILABLE = True
except FileNotFoundError:
    _CORPUS_AVAILABLE = False

corpus = pytest.mark.skipif(
    not _CORPUS_AVAILABLE,
    reason="Estonian Legal Ontology corpus (krr_outputs/INDEX.json) not found",
)
RT = "https://www.riigiteataja.ee/akt/"
FAKE = data.LawRecord("fakelaw", ["fakelaw_peep.json"], "Näidisseadus", "FAKE")
CHANGE_KINDS = {"amended", "added", "first_recorded", "ceased"}


def _version(vid: str, frm: str, to: str = "", text: str = "t") -> dict:
    return {
        "id": vid,
        "redaction_id": vid.rsplit("_v", 1)[-1],
        "valid_from": frm,
        "valid_to": to,
        "text": text,
        "rt_url": f"{RT}{vid.rsplit('_v', 1)[-1]}",
    }


# Provision A amended 2014-03-01; B added 2014-06-01; C ceased from 2014-09-01.
_INDEX = {
    "estleg:FAKE_Par_1": [
        _version("estleg:FAKE_Par_1_v100", "2010-01-01", "2014-02-28"),
        _version("estleg:FAKE_Par_1_v200", "2014-03-01"),
    ],
    "estleg:FAKE_Par_2": [_version("estleg:FAKE_Par_2_v300", "2014-06-01")],
    "estleg:FAKE_Par_3": [_version("estleg:FAKE_Par_3_v100", "2010-01-01", "2014-08-31")],
}
_GRAPH = [
    {"@id": "estleg:FAKE_Map", "@type": ["estleg:Act"], "dcterms:title": "Näidisseadus",
     "dcterms:source": {"@id": f"{RT}999.xml"}},
    {"@id": "estleg:FAKE_Par_1", "@type": ["estleg:LegalProvision"], "estleg:paragrahv": "§ 1."},
    {"@id": "estleg:FAKE_Par_2", "@type": ["estleg:LegalProvision"], "estleg:paragrahv": "§ 2."},
]
_EVENTS = [
    {"@id": "estleg:Amendment_FAKE_vf_20140301", "@type": ["estleg:AmendmentEvent"],
     "rdfs:label": "Muudatus (versioonikiht) 2014-03-01",
     "estleg:entryIntoForce": {"@value": "2014-03-01", "@type": "xsd:date"},
     "estleg:resultedInVersion": [{"@id": "estleg:FAKE_Par_1_v200"}]},
    {"@id": "estleg:Amendment_FAKE_x", "@type": ["estleg:AmendmentEvent"],
     "rdfs:label": "Muudatus", "estleg:amendingAct": "RT I, 2014, 1, 1",
     "estleg:entryIntoForce": {"@value": "2014-06-01", "@type": "xsd:date"}},
]


@pytest.fixture
def fake_law(monkeypatch):
    def resolve(text, paragraph=None):
        law, par = data.split_law_and_paragraph(text)
        if law not in {"FAKE", "Näidisseadus"}:
            return None, None, "law"
        if not par:
            return FAKE, None, ""
        node = data.find_provision(_GRAPH, par)
        return FAKE, node, "" if node is not None else "paragraph"

    monkeypatch.setattr(data, "resolve_act_or_provision", resolve)
    monkeypatch.setattr(data, "load_law_graph", lambda rec: list(_GRAPH))
    monkeypatch.setattr(data, "law_version_index", lambda rec: _INDEX)
    monkeypatch.setattr(data, "_amendment_graph_for", lambda rec: list(_EVENTS))
    monkeypatch.setattr(data, "rt_url_for_slug", lambda slug: f"{RT}999")


# ---------------------------------------------------------------------------
# what_changed
# ---------------------------------------------------------------------------
WHAT_CHANGED_FIELDS = {
    "law", "abbrev", "rt_url", "scope", "provision_id", "paragraph", "since",
    "until", "history_available", "changes_total", "provisions_changed",
    "truncated", "changes", "amendment_events",
}
CHANGE_FIELDS = {
    "date", "change", "provision_id", "paragraph", "redaction_id",
    "previous_redaction_id", "valid_from", "valid_to", "amendment_event", "rt_url",
}


def test_what_changed_act_window_fixture(fake_law) -> None:
    out = server.what_changed("FAKE", "2014-01-01", "2014-12-31")
    assert set(out) == WHAT_CHANGED_FIELDS
    assert out["scope"] == "act" and out["rt_url"] == f"{RT}999"
    assert [(c["date"], c["change"], c["paragraph"]) for c in out["changes"]] == [
        ("2014-03-01", "amended", "§ 1"),
        ("2014-06-01", "added", "§ 2"),
        ("2014-09-01", "ceased", "§ 3"),  # not in the current graph: from the IRI
    ]
    for row in out["changes"]:
        assert set(row) == CHANGE_FIELDS
        assert row["rt_url"].startswith(RT)
    amended = out["changes"][0]
    assert amended["previous_redaction_id"] == "100"
    assert amended["redaction_id"] == "200"
    assert amended["amendment_event"] == "estleg:Amendment_FAKE_vf_20140301"
    assert out["changes_total"] == 3 and out["provisions_changed"] == 3
    assert out["truncated"] is False
    events = {e["event_id"]: e for e in out["amendment_events"]}
    assert events["estleg:Amendment_FAKE_vf_20140301"]["changed_provisions"] == 1
    assert events["estleg:Amendment_FAKE_x"]["rt_reference"] == "RT I, 2014, 1, 1"
    assert events["estleg:Amendment_FAKE_x"]["rt_url"] == f"{RT}999"


def test_what_changed_baseline_provision_scope_and_cap(fake_law) -> None:
    base = server.what_changed("FAKE", "2010-01-01", "2010-12-31")
    assert {c["change"] for c in base["changes"]} == {"first_recorded"}

    one = server.what_changed("FAKE § 1", "2014-01-01", "2014-12-31")
    assert one["scope"] == "provision"
    assert one["provision_id"] == "estleg:FAKE_Par_1"
    assert [c["change"] for c in one["changes"]] == ["amended"]
    # Only the event linked to that §'s change is listed.
    assert [e["event_id"] for e in one["amendment_events"]] == [
        "estleg:Amendment_FAKE_vf_20140301"
    ]

    capped = server.what_changed("FAKE", "2014-01-01", "2014-12-31", limit=1)
    assert capped["truncated"] is True
    assert len(capped["changes"]) == 1 and capped["changes_total"] == 3


def test_what_changed_notes(fake_law) -> None:
    assert "note" in server.what_changed("FAKE", "not-a-date")
    assert "note" in server.what_changed("FAKE", "2014-01-01", "nope")
    assert "note" in server.what_changed("FAKE", "2015-01-01", "2014-01-01")
    assert "note" in server.what_changed("NOPE", "2014-01-01")
    assert "note" in server.what_changed("FAKE § 99", "2014-01-01")
    with i18n.use_language("en"):
        assert server.what_changed("FAKE", "x")["note"].startswith("since must be")


# ---------------------------------------------------------------------------
# transposition_gaps
# ---------------------------------------------------------------------------
_DIRECTIVES = {
    "32001L0001": {"iri": "estleg:EU_32001L0001", "celex": "32001L0001", "title": "Gap",
                   "in_force": True, "deadline": "2003-01-01",
                   "eurlex_url": "https://eur-lex.europa.eu/x1", "transposed_by": []},
    "32002L0002": {"iri": "estleg:EU_32002L0002", "celex": "32002L0002", "title": "Done",
                   "in_force": True, "deadline": "2004-01-01",
                   "eurlex_url": "https://eur-lex.europa.eu/x2",
                   "transposed_by": ["estleg:FAKE_Map"]},
    "31990L0003": {"iri": "estleg:EU_31990L0003", "celex": "31990L0003", "title": "Old",
                   "in_force": False, "deadline": "1992-01-01",
                   "eurlex_url": "https://eur-lex.europa.eu/x3", "transposed_by": []},
    "32005L0004": {"iri": "estleg:EU_32005L0004", "celex": "32005L0004", "title": "Mapped",
                   "in_force": True, "deadline": "", "eurlex_url": "https://eur-lex.europa.eu/x4",
                   "transposed_by": []},
    "32006L0005": {"iri": "estleg:EU_32006L0005", "celex": "32006L0005", "title": "Unknown",
                   "in_force": None, "deadline": "", "eurlex_url": "https://eur-lex.europa.eu/x5",
                   "transposed_by": []},
}


@pytest.fixture
def fake_directives(monkeypatch):
    monkeypatch.setattr(data, "_eu_directives", lambda: _DIRECTIVES)
    monkeypatch.setattr(
        data,
        "_transposition_mappings",
        lambda: [{"directive_celex": "32005L0004", "matched_law_name": "fakelaw"}],
    )
    monkeypatch.setattr(data, "_records_by_slug", lambda: {"fakelaw": FAKE})
    monkeypatch.setattr(data, "law_slug_for_iri", lambda iri: "fakelaw" if "FAKE" in iri else None)
    monkeypatch.setattr(data, "rt_url_for_slug", lambda slug: f"{RT}999")
    edges = data._transposing_laws_by_celex.__wrapped__
    monkeypatch.setattr(data, "_transposing_laws_by_celex", edges)


def test_transposition_gaps_list_fixture(fake_directives) -> None:
    out = server.transposition_gaps()
    assert set(out) == {
        "coverage_flag", "caveat", "in_force_directives", "gaps_total", "truncated", "gaps",
    }
    # Only in-force directives with no edge from either source; unknown force
    # and not-in-force directives are never reported as gaps.
    assert [g["celex"] for g in out["gaps"]] == ["32001L0001"]
    assert out["in_force_directives"] == 3
    assert out["gaps"][0]["coverage_flag"] == "noTranspositionEdgeInCorpus"
    assert out["caveat"].startswith("Katvuse tõdemus")
    assert server.transposition_gaps(limit=0)["truncated"] is True


def test_transposition_gaps_single_directive_fixture(fake_directives) -> None:
    gap = server.transposition_gaps("CELEX:32001 L0001")
    assert gap["coverage_flag"] == "noTranspositionEdgeInCorpus" and gap["caveat"]
    assert gap["transposing_laws"] == []
    done = server.transposition_gaps("32002L0002")
    assert done["coverage_flag"] == "" and done["caveat"] == ""
    assert done["transposing_laws"] == [
        {"name": "fakelaw", "title": "Näidisseadus", "rt_url": f"{RT}999"}
    ]
    assert server.transposition_gaps("32005L0004")["coverage_flag"] == ""  # mapping edge
    assert "note" in server.transposition_gaps("39999L9999")


# ---------------------------------------------------------------------------
# kov_regulations_citing
# ---------------------------------------------------------------------------
def _reg(i: int, links: list[dict], issued: list[str], kov: bool = True):
    return data.RegulationRecord(
        reg_id=str(i), global_id="", title=f"Määrus {i}", issuer="Vallavolikogu",
        rt_url=f"{RT}{i}", status="kehtiv", slug=f"m{i}", file="", is_kov=kov,
        municipality="vald" if kov else "", issued_under=issued,
        citations=[ln["text"] for ln in links], num_provisions=1, citation_links=links,
    )


_LINK6 = {"text": "FAKE § 6 lõike 3", "target": "estleg:FAKE_Par_6_Lg_3", "detail": "lg 3"}
_LINK22 = {"text": "FAKE § 22 lõike 1", "target": "estleg:FAKE_Par_22_Lg_1", "detail": "lg 1"}


@pytest.fixture
def fake_kov(monkeypatch):
    regs = [
        _reg(1, [_LINK6], ["estleg:FAKE_Map"]),
        _reg(2, [_LINK22], []),
        _reg(3, [], ["estleg:FAKE_Map"]),
        _reg(4, [_LINK6], [], kov=False),  # a state regulation: not KOV
    ]
    monkeypatch.setattr(data, "_regulation_records", lambda: regs)
    monkeypatch.setattr(data, "law_slug_for_iri", lambda iri: "fakelaw" if "FAKE" in iri else None)
    monkeypatch.setattr(
        data, "_law_slug_from_issued_under", lambda iri: "fakelaw" if "FAKE" in iri else None
    )
    monkeypatch.setattr(data, "_kov_citation_index", data._kov_citation_index.__wrapped__)
    monkeypatch.setattr(
        data,
        "resolve_law_and_paragraph",
        lambda text, paragraph=None: (
            (FAKE, paragraph or data.split_law_and_paragraph(text)[1])
            if text.startswith("FAKE")
            else (None, "")
        ),
    )


KOV_FIELDS = {
    "reg_id", "title", "issuer", "status", "rt_url", "citations", "is_kov",
    "municipality", "matched_by", "matched_citations",
}


def test_kov_regulations_citing_law_and_section_fixture(fake_kov) -> None:
    rows = server.kov_regulations_citing("FAKE")
    assert [r["reg_id"] for r in rows] == ["1", "2", "3"]
    for r in rows:
        assert set(r) == KOV_FIELDS and r["is_kov"] and r["rt_url"].startswith(RT)
    by_id = {r["reg_id"]: r for r in rows}
    assert by_id["1"]["matched_by"] == "implementsCitation"
    assert by_id["3"]["matched_by"] == "issuedUnder"
    assert by_id["3"]["matched_citations"] == []

    sec = server.kov_regulations_citing("FAKE § 6")
    assert [r["reg_id"] for r in sec] == ["1"]
    assert sec[0]["matched_citations"] == [_LINK6]
    assert [r["reg_id"] for r in server.kov_regulations_citing("FAKE", paragraph="22")] == ["2"]

    capped = server.kov_regulations_citing("FAKE", limit=2)
    assert capped[-1]["overflow"] is True and capped[-1]["total_available"] == 3
    assert server.kov_regulations_citing("NOPE") == [{"note": "seadust ei leitud: NOPE"}]


def test_paragraph_key_from_iri() -> None:
    assert data.paragraph_key_from_iri("estleg:KARIST_2_Osa1_Par_13") == "13"
    assert data.paragraph_key_from_iri("estleg:KOKS_Par_22_1_Lg_3") == "22_1"
    assert data.paragraph_key_from_iri("estleg:KOKS_Par_6_Lg_3") == "6"
    assert data.paragraph_key_from_iri("estleg:KOKS_Map") == ""
    assert data.split_law_and_paragraph("KOKS § 22 lg 1 p 5") == ("KOKS", "§ 22")
    assert data.split_law_and_paragraph("KarS") == ("KarS", "")


# ---------------------------------------------------------------------------
# explain_provision (fixture: not-found contract)
# ---------------------------------------------------------------------------
def test_explain_provision_not_found_fixture(fake_law) -> None:
    assert "note" in server.explain_provision("NOPE § 1")
    assert "note" in server.explain_provision("FAKE § 99")
    assert "note" in server.explain_provision("FAKE")  # a law is not a provision


# ---------------------------------------------------------------------------
# Real corpus
# ---------------------------------------------------------------------------
@corpus
def test_what_changed_kars_2014_corpus() -> None:
    out = server.what_changed("KarS", "2014-01-01", "2014-12-31")
    assert set(out) == WHAT_CHANGED_FIELDS
    assert out["history_available"] is True
    assert out["changes"], "KarS changed during 2014 in the version layer"
    for row in out["changes"]:
        assert set(row) == CHANGE_FIELDS
        assert "2014-01-01" <= row["date"] <= "2014-12-31"
        assert row["change"] in CHANGE_KINDS
        assert row["rt_url"] == "" or row["rt_url"].startswith(RT)
    linked = {r["amendment_event"] for r in out["changes"] if r["amendment_event"]}
    assert linked, "version-layer events link changes via resultedInVersion"
    assert linked <= {e["event_id"] for e in out["amendment_events"]}


@corpus
def test_what_changed_single_provision_corpus() -> None:
    out = server.what_changed("KarS § 424", "2009-01-01", "2010-12-31")
    assert out["scope"] == "provision"
    assert out["provision_id"] == "estleg:KARIST_2_Osa2_Par_424"
    assert out["changes"]
    assert {r["provision_id"] for r in out["changes"]} == {"estleg:KARIST_2_Osa2_Par_424"}
    same = server.what_changed("estleg:KARIST_2_Osa2_Par_424", "2009-01-01", "2010-12-31")
    assert same["changes"] == out["changes"]


@corpus
def test_transposition_gaps_corpus() -> None:
    out = server.transposition_gaps(limit=5)
    assert 0 < out["gaps_total"] <= out["in_force_directives"]
    assert len(out["gaps"]) == 5 and out["truncated"] is True
    for gap in out["gaps"]:
        assert gap["coverage_flag"] == "noTranspositionEdgeInCorpus"
        assert gap["eurlex_url"].startswith("https://eur-lex.europa.eu/")
    flagged = server.transposition_gaps(out["gaps"][0]["celex"])
    assert flagged["coverage_flag"] == "noTranspositionEdgeInCorpus"
    assert flagged["in_force"] is True and flagged["transposing_laws"] == []
    # A mapped directive (VÕS transposes the package-travel directive).
    mapped = server.transposition_gaps("31990L0314")
    assert "volaoigusseadus" in {law["name"] for law in mapped["transposing_laws"]}
    assert mapped["coverage_flag"] == ""


@corpus
def test_kov_regulations_citing_koks_corpus() -> None:
    sec = server.kov_regulations_citing("KOKS § 22", limit=500)
    rows = [r for r in sec if "note" not in r]
    assert rows, "municipal regulations cite KOKS § 22"
    for r in rows:
        assert set(r) == KOV_FIELDS and r["is_kov"] is True
        assert r["rt_url"].startswith(RT)
        assert r["matched_by"] == "implementsCitation"
        assert all(
            data.paragraph_key_from_iri(c["target"]) == "22" for c in r["matched_citations"]
        )
    law_level = server.kov_regulations_citing("KOKS", limit=1)
    assert law_level[-1]["overflow"] is True
    assert law_level[-1]["total_available"] >= len(rows)


@corpus
def test_explain_provision_corpus() -> None:
    out = server.explain_provision("KarS § 424")
    for key in (
        "id", "law", "abbrev", "paragrahv", "label", "summary", "legal_text",
        "truncated", "full_length", "rt_url", "history", "references_out",
        "referenced_by", "court_decisions", "competent_authorities", "sanctions",
        "kov_regulations", "counts", "explanation",
    ):
        assert key in out, key
    assert out["id"] == "estleg:KARIST_2_Osa2_Par_424"
    assert out["history"]["redactions"] > 1
    assert out["sanctions"] and out["counts"]["sanctions"] == len(out["sanctions"])
    assert "§ 424" in out["explanation"]
    same = server.explain_provision("estleg:KARIST_2_Osa2_Par_424")
    assert same["counts"] == out["counts"]
    with i18n.use_language("en"):
        assert "It cites" in server.explain_provision("KarS § 424")["explanation"]
    ps = server.explain_provision("PS § 1")
    assert ps["rt_url"].startswith(RT)


@corpus
def test_citation_on_every_row_corpus() -> None:
    def ok(url: str) -> bool:
        return url == "" or url.startswith("https://www.riigiteataja.ee/")

    for row in server.define_term("leping", limit=5):
        assert {"defined_in", "source_act", "rt_url"} <= set(row) and ok(row["rt_url"])
    subjects = server.laws_for_subject("573", limit=5)
    assert subjects and all(ok(r["rt_url"]) for r in subjects)
    harm = server.harmonisation_for_directive("32000L0060", limit=10)
    assert harm and all(r["eurlex_url"].startswith("https://eur-lex") for r in harm)
    assert all(ok(r["rt_url"]) for r in harm)
    for row in server.amendment_history("KarS", limit=20):
        assert {"rt_reference", "rt_url"} <= set(row) and ok(row["rt_url"])
    auth = server.competent_authority_for_law("PS")
    assert auth and all(r["rt_url"].startswith(RT) and r["institution_id"] for r in auth)
    hist = server.provision_history("KarS", "§ 13")
    assert hist and all(r["rt_url"].startswith(RT) for r in hist)


@corpus
def test_truncation_is_explicit_and_full_text_everywhere_corpus() -> None:
    cut = server.get_provision("LS", "§ 2")
    assert cut["truncated"] is True
    assert cut["full_length"] > len(cut["legal_text"])
    full = server.get_provision("LS", "§ 2", full_text=True)
    assert full["truncated"] is False and len(full["legal_text"]) == full["full_length"]

    hist = server.provision_history("LS", "§ 2")
    # A historical (closed) redaction long enough to be cut.
    as_of = next(r["valid_from"] for r in hist if r["truncated"] and r["valid_to"])
    old_cut = server.get_provision("LS", "§ 2", as_of=as_of)
    assert old_cut["truncated"] is True
    assert old_cut["redaction_rt_url"].startswith(RT)
    old_full = server.get_provision("LS", "§ 2", as_of=as_of, full_text=True)
    assert old_full["truncated"] is False
    assert len(old_full["legal_text"]) == old_cut["full_length"]

    assert any(r["truncated"] for r in hist)
    assert not any(r["truncated"] for r in server.provision_history("LS", "§ 2", full_text=True))
    short = server.get_provision("PS", "§ 1")
    assert short["truncated"] is False and short["full_length"] == len(short["legal_text"])
