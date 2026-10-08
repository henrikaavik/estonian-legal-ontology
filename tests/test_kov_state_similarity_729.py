"""#729: KOV <-> state topical similarity index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import generate_kov_state_similarity as ks

WASTE = ("Olmejäätmete kogumine ja jäätmevedu toimub korraldatud jäätmeveo "
         "piirkonnas. Jäätmevaldaja sorteerib biojäätmed, pakendijäätmed ja "
         "paberijäätmed ning annab need jäätmekäitlejale.")
ALCOHOL = ("Kange alkohoolse joogi jaemüük kaupluses on keelatud öisel ajal. "
           "Alkohoolse joogi jaemüüja tagab joogi müügi alaealisele keelu "
           "täitmise ning alkoholireklaami piirangud.")
SCHOOL = ("Põhikooli õpilane osaleb õppetöös. Õppekava järgi korraldatakse "
          "õppetunnid, hindamine ja klassikursuse lõpetamine; õpetaja "
          "toetab õpilase arengut koolis.")


def _act(iri: str, types: list[str], label: str, **extra) -> dict:
    return {"@id": iri, "@type": types, "rdfs:label": label, **extra}


def _prov(act: str, n: int, text: str) -> dict:
    return {"@id": f"{act.removesuffix('_Map')}_Par_{n}",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "rdfs:label": f"§ {n}.", "estleg:legalText": text,
            "estleg:partOfAct": {"@id": act}}


def _write(path: Path, graph: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"@context": {}, "@graph": graph}, ensure_ascii=False),
                    encoding="utf-8")


def _law(krr: Path, name: str, iri: str, label: str, texts: list[str]) -> None:
    graph = [_act(iri, ["estleg:Act", "estleg:Law"], label, **{"dc:source": label})]
    graph += [_prov(iri, i + 1, t) for i, t in enumerate(texts)]
    _write(krr / f"{name}_peep.json", graph)


def _kov(krr: Path, issuer: str, tid: str, label: str, texts: list[str], *,
         preamble: str = "", issued_under: tuple[str, ...] = ()) -> str:
    iri = f"estleg:Reg_{tid}_Map"
    extra = {"estleg:preambleText": preamble,
             "estleg:issuedUnder": [{"@id": x} for x in issued_under]}
    graph = [_act(iri, ["estleg:Act", "estleg:MunicipalRegulation"], f"{label} (määrus)", **extra)]
    graph += [_prov(iri, i + 1, t) for i, t in enumerate(texts)]
    _write(krr / "regulations" / "kov" / issuer / f"act_t{tid}_peep.json", graph)
    return iri


@pytest.fixture
def mini_krr(tmp_path: Path) -> Path:
    krr = tmp_path / "krr_outputs"
    _law(krr, "jaatmeseadus", "estleg:JS_Map", "Jäätmeseadus", [WASTE, WASTE + " Prügila."])
    _law(krr, "alkoholiseadus", "estleg:AS_Map", "Alkoholiseadus", [ALCOHOL])
    _law(krr, "pohikooli_seadus", "estleg:PGS_Map", "Põhikooli- ja gümnaasiumiseadus", [SCHOOL])
    reg = "estleg:Reg_500_Map"
    _write(krr / "regulations" / "riik" / "jaatmete_sortimine_peep.json", [
        _act(reg, ["estleg:Act", "estleg:NationalRegulation"], "Jäätmete sortimise nõuded"),
        _prov(reg, 1, WASTE)])
    # The waste act's preamble and issuedUnder point at the ALCOHOL act: the
    # topical score must ignore both and still rank the waste law first.
    _kov(krr, "tartu_linnavolikogu", "1", "Tartu linna jäätmehoolduseeskiri",
         [WASTE + " Tartu linnas Tartu Ülikooli juures ja Tartu turul."],
         preamble="Määrus kehtestatakse alkoholiseaduse § 1 alusel.",
         issued_under=("estleg:AS_Map",))
    _kov(krr, "vormsi_vallavolikogu", "2", "Alkohoolse joogi jaemüügi piirangud",
         [ALCOHOL], issued_under=("estleg:AS_Map",))
    _kov(krr, "kihnu_vallavalitsus", "3", "Kihnu kooli põhimäärus", [SCHOOL])
    return krr


def _top(index: dict, kov: str, group: str = "kovToLaw") -> list:
    return index[group].get(kov, [])


def test_each_kov_act_finds_its_topical_law(mini_krr: Path) -> None:
    index = ks.build_index(mini_krr, verbose=False)
    assert _top(index, "estleg:Reg_1_Map")[0][0] == "estleg:JS_Map"
    assert _top(index, "estleg:Reg_2_Map")[0][:1] == ["estleg:AS_Map"]
    assert _top(index, "estleg:Reg_3_Map")[0][0] == "estleg:PGS_Map"
    assert _top(index, "estleg:Reg_1_Map", "kovToStateRegulation")[0][0] == "estleg:Reg_500_Map"
    # enabling column: set only where the matched act is also issuedUnder.
    assert _top(index, "estleg:Reg_2_Map")[0][2] == 1
    assert all(row[2] == 0 for row in _top(index, "estleg:Reg_1_Map") if row[0] != "estleg:AS_Map")


def test_structural_links_do_not_change_scores(mini_krr: Path) -> None:
    with_links = ks.build_index(mini_krr, verbose=False)
    path = mini_krr / "regulations" / "kov" / "tartu_linnavolikogu" / "act_t1_peep.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["@graph"][0].pop("estleg:issuedUnder")
    doc["@graph"][0].pop("estleg:preambleText")
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    without = ks.build_index(mini_krr, verbose=False)
    scores = [row[:2] for row in _top(with_links, "estleg:Reg_1_Map")]
    assert scores == [row[:2] for row in _top(without, "estleg:Reg_1_Map")]


def test_inverse_lists_mirror_forward_scores(mini_krr: Path) -> None:
    index = ks.build_index(mini_krr, verbose=False)
    inverse = index["stateToKov"]["estleg:JS_Map"]
    assert inverse[0][0] == "estleg:Reg_1_Map"
    forward = {row[0]: row[1] for row in _top(index, "estleg:Reg_1_Map")}
    assert inverse[0][1] == forward["estleg:JS_Map"]


def test_provision_level_matches(mini_krr: Path) -> None:
    index = ks.build_index(mini_krr, verbose=False)
    assert index["counts"]["kovProvisionsAnalysed"] == 3
    target, score = index["provisionMatches"]["estleg:Reg_3_Par_1"]
    assert target == "estleg:PGS_Par_1" and score >= ks.PROVISION_MIN_SCORE


def test_build_is_byte_deterministic(mini_krr: Path) -> None:
    first = ks.dumps_index(ks.build_index(mini_krr, verbose=False))
    second = ks.dumps_index(ks.build_index(mini_krr, verbose=False))
    assert first == second
    json.loads(first)  # valid JSON despite the line-per-entry layout


def test_repealed_and_deprecated_acts_are_excluded(mini_krr: Path) -> None:
    path = mini_krr / "alkoholiseadus_peep.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["@graph"][0]["owl:deprecated"] = {"@value": "true", "@type": "xsd:boolean"}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    index = ks.build_index(mini_krr, verbose=False)
    assert "estleg:AS_Map" not in index["stateToKov"]
    assert index["counts"]["stateActsByType"] == {"law": 2, "state_regulation": 1}


def test_proper_nouns_are_detected() -> None:
    texts = ["Turg asub Tartu kesklinnas. Elanikud käivad Tartu turul ja Tartu "
             "jõe ääres. Jäätmed viiakse prügilasse."]
    proper = ks.proper_noun_terms(texts)
    assert "tartu" in proper
    assert "jäätmed" not in proper and "turg" not in proper  # sentence-initial only


def test_run_writes_index_and_merges_report(mini_krr: Path) -> None:
    report = mini_krr / ks.REPORT_RELPATH
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"provisions_by_type": {"law": 9, "kov": 0}}), encoding="utf-8")
    ks.run(mini_krr, verbose=False)
    merged = json.loads(report.read_text(encoding="utf-8"))
    assert merged["provisions_by_type"] == {"law": 9, "kov": 3}
    assert merged["kov_state_topical"]["index"] == ks.INDEX_RELPATH
    assert (mini_krr / ks.INDEX_RELPATH).exists()


@pytest.mark.corpus
def test_committed_index_matches_a_fresh_build(corpus_krr) -> None:
    committed = corpus_krr.path(ks.INDEX_RELPATH).read_text(encoding="utf-8")
    fresh = ks.dumps_index(ks.build_index(corpus_krr.root, verbose=False))
    assert fresh == committed, (
        "kov_state_similarity_index.json is stale; regenerate with "
        "`python3 scripts/generate_kov_state_similarity.py`"
    )
    report = corpus_krr.read_json("reports/similarity_report.json")
    assert report["provisions_by_type"]["kov"] > 0
    assert report["kov_state_topical"]["counts"] == json.loads(fresh)["counts"]
