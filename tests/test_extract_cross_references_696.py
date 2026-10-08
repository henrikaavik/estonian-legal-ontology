"""#696 — cross-law citation boundaries and resolver coverage.

* ``_PAT_ABBREV`` has a left word boundary (MTÜS/ELS/XTMS must not match
  the registered TÜS/LS/TMS keys);
* abbreviations resolve through the registry's corpus prefix first;
* full-name genitive citations resolve against every registry/corpus law
  title (Pattern 4), not just ``FULLNAME_GENITIVE``;
* unresolved court citations are kept as target-less ``estleg:Citation``
  nodes instead of being dropped.
"""
from __future__ import annotations

import json

import pytest

from estleg import extract_court_provision_links as ecpl
from estleg import extract_cross_references as xr
from estleg.estleg_common import _RunCounters

# ── 1. left word boundary ───────────────────────────────────────────────────


def _abbrev_refs(text: str) -> list[str]:
    return [m.group(1) for m in xr._PAT_ABBREV.finditer(text)]


@pytest.mark.parametrize(
    "text,forbidden",
    [("MTÜS § 12 alusel", "TÜS"), ("ELS § 7", "LS"), ("XTMS § 4", "TMS")],
)
def test_abbrev_does_not_match_tail_of_longer_abbrev(text, forbidden):
    assert forbidden not in _abbrev_refs(text)
    assert all(c["law_ref"] != forbidden for c in xr.extract_citations_from_text(text))


def test_ticket_examples_resolve_to_their_own_registered_law():
    # MTÜS / ELS are official rt_api lyhend and now registered keys.
    assert _abbrev_refs("MTÜS § 12 alusel") == ["MTÜS"]
    assert _abbrev_refs("ELS § 7") == ["ELS"]
    assert _abbrev_refs("XTMS § 4") == []  # unregistered: no match at all


@pytest.mark.parametrize(
    "text", ["TÜS § 3", "vt TÜS § 3", "(TÜS §-s 3)", "lause.TÜS § 3"]
)
def test_bare_abbrev_still_matches(text):
    assert _abbrev_refs(text) == ["TÜS"]


def test_court_and_inlaw_patterns_agree_on_boundaries():
    for text in ("MTÜS § 12", "ELS § 7", "XTMS § 4", "TÜS § 3", "KarS § 121"):
        state, _kov = ecpl.extract_citations_from_text(text)
        assert [c["law_ref"] for c in state] == _abbrev_refs(text), text


# ── 2. registry-first abbreviation -> prefix ───────────────────────────────

PROVISIONS = {
    "REOS": {"5": "estleg:REOS_Par_5"},
    "REOS_2": {"5": "estleg:REOS_2_Par_5"},
    "ROS": {"3": "estleg:ROS_Par_3"},
    "ROS_2": {"3": "estleg:ROS_2_Par_3"},
    "KARIST_2_Osa1": {"1": "estleg:KARIST_2_Par_1"},
    "KARIST_2_Osa2": {"121": "estleg:KARIST_2_Par_121"},
    "TsUS_Osa2": {"67": "estleg:TsUS_Par_67"},
    "KALAPU": {"5": "estleg:KALAPU_Par_5"},
    "TUS": {"3": "estleg:TUS_Par_3"},
    "MTUS": {"12": "estleg:MTUS_Par_12"},
}


def test_prefix_family_index_groups_osa_in_order():
    fam = xr.build_prefix_family_index(
        {"A_Osa10": {}, "A_Osa2": {}, "A": {}, "B": {}}
    )
    assert fam["A"] == ["A", "A_Osa2", "A_Osa10"]
    assert fam["B"] == ["B"]


def test_registry_prefixes_skip_entries_absent_from_corpus():
    fam = xr.build_prefix_family_index(PROVISIONS)
    assert xr.registry_prefixes(("TsMS_2", "KARIST_2"), fam) == [
        "KARIST_2_Osa1", "KARIST_2_Osa2",
    ]
    assert xr.registry_prefixes(("NOPE",), fam) == []


def test_abbrev_to_prefix_uses_registry_corpus_prefix():
    a2p = xr.build_abbreviation_to_prefix({}, PROVISIONS)
    assert a2p["REÕS"] == ["REOS"]
    assert a2p["REOS"] == ["REOS_2"]
    assert a2p["ROS"] == ["ROS"]
    assert a2p["RÕS"] == ["ROS_2"]
    assert a2p["KarS"] == ["KARIST_2_Osa1", "KARIST_2_Osa2"]
    assert a2p["MTÜS"] == ["MTUS"]


def test_registry_prefix_must_be_corroborated_by_corpus_source_act():
    """STS2004_2006_2 is registered as the Struktuuritoetuse seadus but the
    corpus file under that prefix is another law: registry-first must not
    link there; the sourceAct fallback finds the real file."""
    provisions = {
        "STS2004_2006_2": {"5": "estleg:STS2004_2006_2_Par_5"},
        "struktuuritoetuse_seadus": {"5": "estleg:struktuuritoetuse_seadus_Par_5"},
    }
    source_acts = {
        "Riiklike peretoetuste seadus": ["STS2004_2006_2"],
        "Struktuuritoetuse seadus": ["struktuuritoetuse_seadus"],
    }
    titles = xr.build_law_title_to_prefixes(source_acts, provisions)
    assert titles["struktuuritoetuse seadus"] == ["struktuuritoetuse_seadus"]


def test_series_short_title_is_not_resolved():
    provisions = {p: {"5": f"estleg:{p}_Par_5"} for p in ("STS", "STS1", "STS2", "LAS")}
    source_acts = {
        "Struktuuritoetuse seadus": ["STS"],
        "Perioodi 2007–2013 struktuuritoetuse seadus": ["STS1"],
        "Perioodi 2014–2020 struktuuritoetuse seadus": ["STS2"],
        "Laeva asjaõigusseadus": ["LAS"],
    }
    titles = xr.build_law_title_to_prefixes(source_acts, provisions)
    assert "struktuuritoetuse seadus" not in titles
    assert titles["perioodi 2014–2020 struktuuritoetuse seadus"] == ["STS2"]
    assert "laeva asjaõigusseadus" in titles


def test_title_fallback_skips_prefix_owned_by_another_law():
    provisions = {
        "KS": {"5": "estleg:KS_Par_5"},
        "ulikooliseadus": {"5": "estleg:ulikooliseadus_Par_5"},
    }
    source_acts = {"Kohtute seadus": ["KS"], "Ülikooliseadus": ["KS", "ulikooliseadus"]}
    titles = xr.build_law_title_to_prefixes(source_acts, provisions)
    assert titles["kohtute seadus"] == ["KS"]
    assert titles["ülikooliseadus"] == ["ulikooliseadus"]


def test_abbrev_to_prefix_falls_back_to_source_act():
    # Võlaõigusseadus has no registry entry -> sourceAct chain.
    a2p = xr.build_abbreviation_to_prefix(
        {"Võlaõigusseadus": ["volaoigusseadus_Osa1"]}, PROVISIONS
    )
    assert a2p["VÕS"] == ["volaoigusseadus_Osa1"]
    # Legacy single-arg call keeps the pure sourceAct behaviour.
    assert xr.build_abbreviation_to_prefix({"Karistusseadustik": ["K"]})["KarS"] == ["K"]


def test_court_abbrev_to_prefix_uses_registry_corpus_prefix():
    a2p = ecpl.build_abbreviation_to_prefix({}, PROVISIONS)
    assert a2p["RÕS"] == {"ROS_2"}
    assert a2p["REOS"] == {"REOS_2"}


# ── 3. full-name genitive via the law-title map ─────────────────────────────


def test_law_title_to_prefixes_registry_first_then_source_act():
    titles = xr.build_law_title_to_prefixes(
        {"Kalapüügiseadus": ["WRONG"], "Jahiseadus": ["JAHI"]},
        {**PROVISIONS, "JAHI": {"1": "estleg:JAHI_Par_1"}},
    )
    assert titles["kalapüügiseadus"] == ["KALAPU"]  # registry prefix wins
    assert titles["jahiseadus"] == ["JAHI"]  # sourceAct fallback
    assert titles["riigi õigusabi seadus"] == ["ROS_2"]


TITLES = {
    "kalapüügiseadus": ["KALAPU"],
    "riigi õigusabi seadus": ["ROS_2"],
    "eesti vabariigi põhiseadus": ["eesti_vabariigi_pohiseadus"],
}


def test_genitive_title_citation_resolves():
    cits = xr.extract_citations_from_text("kalapüügiseaduse § 5 lõike 2 alusel", TITLES)
    assert len(cits) == 1
    cit = cits[0]
    assert cit["law_ref"] == "kalapüügiseadus"
    assert cit["prefixes"] == ["KALAPU"]
    assert cit["paragraphs"] == ["5"] and cit["lg"] == "2"
    assert cit["citationText"] == "kalapüügiseaduse § 5 lõike 2"
    assert xr.resolve_citation(cit, "SELF", {}, PROVISIONS) == ["estleg:KALAPU_Par_5"]


def test_multiword_title_takes_longest_known_suffix_and_exact_span():
    text = "Vastavalt  Riigi õigusabi\nseaduse §-le 3 antakse"
    (cit,) = xr.extract_citations_from_text(text, TITLES)
    assert cit["law_ref"] == "riigi õigusabi seadus"
    assert cit["citationText"] == "Riigi õigusabi\nseaduse §-le 3"


def test_curated_genitive_and_self_refs_are_not_double_counted():
    text = "Eesti Vabariigi põhiseaduse § 10 ja käesoleva seaduse § 2"
    cits = xr.extract_citations_from_text(text, TITLES)
    assert sorted(c["law_ref"] for c in cits) == ["PS", "__SELF__"]


def test_year_and_period_titles_never_truncate_to_another_law():
    titles = {
        "struktuuritoetuse seadus": ["STS2004_2006_2"],
        "perioodi 2014–2020 struktuuritoetuse seadus": ["STS2014_2020"],
        "riigieelarve seadus": ["REELS"],
        "2026. aasta riigieelarve seadus": ["2026_aasta_riigieelarve_seadus"],
    }
    def refs(text):
        return [c["law_ref"] for c in xr.extract_citations_from_text(text, titles)]
    assert refs("perioodi 2014–2020 struktuuritoetuse seaduse § 5") == [
        "perioodi 2014–2020 struktuuritoetuse seadus"]
    assert refs("2026. aasta riigieelarve seaduse § 3") == ["2026. aasta riigieelarve seadus"]
    # Unknown period / year: a truncated match would be the wrong statute.
    assert refs("perioodi 2021–2027 struktuuritoetuse seaduse § 5") == []
    assert refs("2025. aasta riigieelarve seaduse § 3") == []
    assert refs("struktuuritoetuse seaduse § 5") == ["struktuuritoetuse seadus"]
    # A date year before a law name is not part of it.
    assert [c["law_ref"] for c in xr.extract_citations_from_text(
        "Enne 1. juulit 2002 riigieelarve seaduse § 63 alusel")] == ["RES"]
    # The curated FULLNAME_GENITIVE pattern obeys the same guard (in-law + court).
    assert xr.extract_citations_from_text("2025. aasta riigieelarve seaduse § 3") == []
    assert [c["law_ref"] for c in xr.extract_citations_from_text("riigieelarve seaduse § 3")] == ["RES"]
    assert ecpl.extract_citations_from_text("2025. aasta riigieelarve seaduse § 3")[0] == []


def test_unknown_genitive_names_never_become_citations():
    text = "nimetatud seaduse § 6 ja selle seaduse § 7"
    assert xr.extract_citations_from_text(text, TITLES) == []
    # Without the map, Pattern 4 is off entirely (legacy behaviour).
    assert xr.extract_citations_from_text("kalapüügiseaduse § 5") == []


def test_inlaw_pass_threads_title_map_and_keeps_unresolved():
    graph = [
        {"@id": "estleg:X_Par_1", "estleg:paragrahv": "1",
         "estleg:summary": "kalapüügiseaduse § 5 ja kalapüügiseaduse § 99"},
    ]
    stats = xr._run_inlaw_citation_pass(
        graph, self_prefix="X", abbrev_to_prefix={},
        prefix_to_provisions=PROVISIONS, xml_par_texts={},
        law_title_to_prefixes=TITLES,
    )
    assert graph[0]["estleg:references"] == [{"@id": "estleg:KALAPU_Par_5"}]
    assert stats["citations_resolved"] == 1
    unresolved = [n for n in graph if "estleg:Citation" in n.get("@type", [])]
    assert len(unresolved) == 1
    assert "estleg:citationTarget" not in unresolved[0]
    assert unresolved[0]["estleg:citationDetail"] == "kalapüügiseadus"


# ── 4. court: unresolved citations kept ─────────────────────────────────────


def _write_rk(rk_dir, graph):
    rk_dir.mkdir()
    path = rk_dir / "riigikohus_2026_peep.json"
    path.write_text(json.dumps({"@graph": graph}, ensure_ascii=False), encoding="utf-8")
    return path


def test_court_unresolved_citations_become_targetless_citation_nodes(tmp_path, monkeypatch):
    rk_dir = tmp_path / "riigikohus"
    path = _write_rk(rk_dir, [{
        "@id": "estleg:RK_1",
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "estleg:legalText": "KarS § 121 ja KarS § 999 ning VÕS § 5 ja VÕS § 5.",
    }])
    monkeypatch.setattr(ecpl, "RK_DIR", rk_dir)
    a2p = {"KarS": {"KARIST_2_Osa2"}}
    for _run in range(2):  # second run proves idempotency
        result = ecpl.process_court_files(
            a2p, PROVISIONS, kov_index={}, kov_collision_keys=set(),
            known_issuer_norms=set(), counters=_RunCounters(),
        )
        graph = json.loads(path.read_text(encoding="utf-8"))["@graph"]
        assert graph[0]["estleg:interpretsLaw"] == [{"@id": "estleg:KARIST_2_Par_121"}]
        cits = [n for n in graph if "estleg:Citation" in n.get("@type", [])]
        assert [(c["@id"], c["estleg:citationDetail"], c["estleg:citationText"]) for c in cits] == [
            ("estleg:Citation_RK_1_1", "KarS", "KarS § 999"),
            ("estleg:Citation_RK_1_2", "VÕS", "VÕS § 5"),
        ]
        assert all(c["estleg:citationSource"] == {"@id": "estleg:RK_1"} for c in cits)
        assert all("estleg:citationTarget" not in c for c in cits)
        assert result.per_file_stats[0]["state_citations_unresolved"] == 3


def test_court_pass_keeps_referenced_law_citations(tmp_path, monkeypatch):
    rk_dir = tmp_path / "riigikohus"
    ref_law = {
        "@id": "estleg:Citation_RK_1_RefLaw_1",
        "@type": ["owl:NamedIndividual", "estleg:Citation"],
        "estleg:citationText": "jahiseaduse",
    }
    path = _write_rk(rk_dir, [
        {"@id": "estleg:RK_1", "@type": ["estleg:CourtDecision"], "estleg:legalText": "x"},
        ref_law,
    ])
    monkeypatch.setattr(ecpl, "RK_DIR", rk_dir)
    ecpl.process_court_files(
        {}, {}, kov_index={}, kov_collision_keys=set(),
        known_issuer_norms=set(), counters=_RunCounters(),
    )
    assert ref_law in json.loads(path.read_text(encoding="utf-8"))["@graph"]
