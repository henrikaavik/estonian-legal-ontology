"""#552 empty-result contract and #496 EuroVoc search expansion.

Corpus-free: list tools and ``search_law_records`` are exercised with
injected records / synonym groups so these tests do not load INDEX.json.
"""

from __future__ import annotations

from estleg_mcp import data, server

_UNKNOWN = "no-such-law-xyz"
_NOTE = [{"note": f"law not found: {_UNKNOWN}"}]


def _fake_law() -> data.LawRecord:
    return data.LawRecord(name="dummy_act", files=[], title="Dummy Act", abbrev=None)


def test_list_tools_unknown_law_returns_note(monkeypatch) -> None:
    monkeypatch.setattr(data, "resolve_law", lambda _q: None)
    assert server.who_references(_UNKNOWN) == _NOTE
    assert server.references_of(_UNKNOWN) == _NOTE
    assert server.drafts_affecting_law(_UNKNOWN) == _NOTE
    assert server.court_decisions_for_law(_UNKNOWN) == _NOTE
    assert server.sanctions_for_law(_UNKNOWN) == _NOTE
    assert server.competent_authority_for_law(_UNKNOWN) == _NOTE
    assert server.provision_history(_UNKNOWN, "§ 1") == _NOTE
    assert server.regulations_for_law(_UNKNOWN) == _NOTE
    assert server.amendment_history(_UNKNOWN) == _NOTE


def test_list_tools_known_law_zero_hits_returns_empty(monkeypatch) -> None:
    rec = _fake_law()
    monkeypatch.setattr(data, "resolve_law", lambda _q: rec)
    monkeypatch.setattr(data, "load_law_graph", lambda _r: [])
    monkeypatch.setattr(data, "act_node", lambda _g: None)
    monkeypatch.setattr(data, "provision_nodes", lambda _g: [])
    monkeypatch.setattr(data, "find_provision", lambda _g, _p: None)
    monkeypatch.setattr(data, "amendment_link_drafts", lambda _r: {})
    monkeypatch.setattr(data, "amendment_events", lambda _r, limit=50: [])
    monkeypatch.setattr(data, "regulations_for_law", lambda _name: [])
    monkeypatch.setattr(data, "_sanction_graph_for", lambda _r: [])
    assert server.who_references("Dummy Act") == []
    assert server.references_of("Dummy Act") == []
    assert server.drafts_affecting_law("Dummy Act") == []
    assert server.court_decisions_for_law("Dummy Act") == []
    assert server.sanctions_for_law("Dummy Act") == []
    assert server.competent_authority_for_law("Dummy Act") == []
    assert server.provision_history("Dummy Act", "§ 1") == []
    assert server.regulations_for_law("Dummy Act") == []
    assert server.amendment_history("Dummy Act") == []


def test_search_english_eurovoc_label_matches_estonian_title(monkeypatch) -> None:
    """#496: 'criminal law' hits a record whose title only has kriminaalõigus."""
    rec = data.LawRecord(
        name="dummy_krim",
        files=[],
        title="Ainult kriminaalõigus",
        abbrev=None,
    )
    groups = (
        (
            ("criminal law", "kriminaaloigus"),
            ("criminal law", "kriminaaloigus", "karistus"),
        ),
    )
    monkeypatch.setattr(data, "_records_by_slug", lambda: {rec.name: rec})
    monkeypatch.setattr(data, "_slug_to_human_abbrev", dict)
    monkeypatch.setattr(data, "_eurovoc_expansion_groups", lambda: groups)

    hits = data.search_law_records("criminal law")
    assert [r.name for r in hits] == ["dummy_krim"]

    # Domain keyword (not the ET label) also satisfies the expanded phrase.
    kw_only = data.LawRecord("dummy_kw", [], "Karistusasi eriseadus", None)
    monkeypatch.setattr(
        data, "_records_by_slug", lambda: {rec.name: rec, kw_only.name: kw_only}
    )
    names = {r.name for r in data.search_law_records("criminal law")}
    assert names == {"dummy_krim", "dummy_kw"}


def test_search_eurovoc_expansion_keeps_token_and(monkeypatch) -> None:
    rec = data.LawRecord("dummy_krim", [], "Ainult kriminaalõigus", None)
    groups = (
        (
            ("criminal law", "kriminaaloigus"),
            ("criminal law", "kriminaaloigus", "karistus"),
        ),
    )
    monkeypatch.setattr(data, "_records_by_slug", lambda: {rec.name: rec})
    monkeypatch.setattr(data, "_slug_to_human_abbrev", dict)
    monkeypatch.setattr(data, "_eurovoc_expansion_groups", lambda: groups)

    # Unrelated leftover token still has to appear — not OR'd away.
    assert data.search_law_records("criminal law xyzzy") == []
    # A bare word that is not a complete domain label does not expand.
    assert data.search_law_records("criminal") == []
