"""#703: legal-text fidelity gate (coverage baseline + RT sampling diff).

Offline throughout: RT fetches are injected or monkeypatched. One opt-in
live run follows the ``tests/test_rt_schema_canary.py`` convention::

    ESTLEG_LIVE_CANARY=1 python3 -m pytest -q tests/test_check_text_fidelity.py -k live
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import requests

from estleg import check_text_fidelity as ctf
from estleg import riigiteataja_common as rt

RT_XML = """<oigusakt xmlns="tyviseadus_1_10.02.2010"><sisu><peatykk>
<peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
<paragrahvPealkiri>Reguleerimisala</paragrahvPealkiri>
<loige><loigeNr>1</loigeNr><sisuTekst><tavatekst>Seadus reguleerib asju.</tavatekst></sisuTekst></loige>
<loige><loigeNr>2</loigeNr><sisuTekst><tavatekst>Teine lõige.</tavatekst></sisuTekst></loige>
</paragrahv>
<paragrahv><paragrahvNr ylaIndeks="1">7</paragrahvNr><kuvatavNr><![CDATA[§ 7<sup>1</sup>.]]></kuvatavNr>
<paragrahvPealkiri>Lisasäte</paragrahvPealkiri>
<loige><sisuTekst><tavatekst>Lisasätte tekst.</tavatekst></sisuTekst></loige>
</paragrahv>
</peatykk></sisu></oigusakt>"""

RT_XML_NEWER = RT_XML.replace("Teine lõige.", "Teine lõige uues redaktsioonis.")

ACT = "111"
NEWER = "222"


def _root(text: str = RT_XML) -> ET.Element:
    return ET.fromstring(text)


def _record(paragrahv: str, text: str, *, iri: str = "estleg:T_Par_1", act: str = ACT):
    return ctf.ProvisionRecord(
        iri=iri, paragrahv=paragrahv, legal_text=text, act_id=act, file="t_peep.json"
    )


def _fetchers(xml_by_id: dict[str, str], current: str | None = ACT, calls: list | None = None):
    def fetch_xml_root(act_id: str) -> ET.Element:
        if calls is not None:
            calls.append(("xml", act_id))
        return _root(xml_by_id[act_id])

    def fetch_metadata(act_id: str) -> dict:
        if calls is not None:
            calls.append(("meta", act_id))
        return {"currentId": current, "actId": act_id}

    return {"fetch_xml_root": fetch_xml_root, "fetch_metadata": fetch_metadata}


# --- normalisation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("corpus", "rt_text"),
    [
        ("§ 7<sup>2</sup> lõige (1)", "§ 7² lõige  (1)"),
        ("(1)  Tekst on siin.", "(1) Tekst on siin."),
        ("(1) Kehtetu - (2) Tekst.", "(2) Tekst."),
        ("(1¹) Kehtetu - (2) Tekst.", "(2) Tekst."),
        ("Kehtetu -", ""),
        ("märkida: 1) nimi; 2) kehtetu - 3) asukoht;", "märkida: nimi; asukoht;"),
        ("märkida: nimi; asukoht;", "märkida: 1) nimi; 2¹) asukoht;"),
    ],
)
def test_normalise_text_folds_rendering_only_differences(corpus: str, rt_text: str) -> None:
    assert ctf.normalise_text(corpus) == ctf.normalise_text(rt_text)


@pytest.mark.parametrize(
    ("corpus", "rt_text"),
    [
        ("Tehing on kehtetu.", "Tehing on tühine."),
        ("Tehing on kehtetu.", "Tehing on."),
        ("(1) Tekst. (jõustumine muudetud - RT I, 22.12.2013, 1)", "(1) Tekst."),
        ("trahv kuni 300 trahviühikut", "trahv kuni 30 trahviühikut"),
        ("(1) Tekst.", "(2) Tekst."),
    ],
)
def test_normalise_text_keeps_wording_differences(corpus: str, rt_text: str) -> None:
    assert ctf.normalise_text(corpus) != ctf.normalise_text(rt_text)


def test_provision_key_keeps_superscripts_distinct() -> None:
    assert ctf.provision_key("§ 7².") == "7^2"
    assert ctf.provision_key("§ 7<sup>2</sup>.") == "7^2"
    assert ctf.provision_key("§ 72.") == "72"
    assert ctf.provision_key("§67") == ctf.provision_key("§ 67.")


# --- parser reuse ---------------------------------------------------------------


def test_parse_rt_provisions_uses_generator_text() -> None:
    parsed = ctf.parse_rt_provisions(_root())
    assert parsed == {
        "1": ["(1) Seadus reguleerib asju. (2) Teine lõige."],
        "7^1": ["Lisasätte tekst."],
    }


@pytest.mark.committed
def test_parse_rt_provisions_on_committed_kars_xml() -> None:
    kars = Path(__file__).resolve().parent.parent / "data" / "riigiteataja" / "karistusseadustik.xml"
    parsed = ctf.parse_rt_provisions(ET.parse(kars).getroot())
    assert len(parsed) > 400
    assert parsed["1"][0].startswith("(1) Karistusseadustiku üldosa sätteid")
    assert any("^" in key for key in parsed)  # superscript §§ keep their own key


# --- check_act ------------------------------------------------------------------


def test_check_act_match() -> None:
    rec = _record("§ 1.", "(1) Seadus reguleerib asju.  (2) Teine lõige.")
    [res] = ctf.check_act(ACT, [rec], **_fetchers({ACT: RT_XML}))
    assert res.status == ctf.MATCH


def test_check_act_superscript_section_matches() -> None:
    rec = _record("§ 7¹.", "Lisasätte tekst.", iri="estleg:T_Par_7_1")
    [res] = ctf.check_act(ACT, [rec], **_fetchers({ACT: RT_XML}))
    assert res.status == ctf.MATCH


def test_check_act_mismatch_has_diff() -> None:
    rec = _record("§ 1.", "(1) Seadus reguleerib muid asju. (2) Teine lõige.")
    [res] = ctf.check_act(ACT, [rec], **_fetchers({ACT: RT_XML}))
    assert res.status == ctf.MISMATCH
    assert "-muid" in res.diff and "+++ RT re-parse" in res.diff


def test_check_act_missing_section_is_not_found() -> None:
    rec = _record("§ 99.", "Tekst.")
    [res] = ctf.check_act(ACT, [rec], **_fetchers({ACT: RT_XML}))
    assert res.status == ctf.NOT_FOUND


def test_newer_consolidation_is_reported_not_failed() -> None:
    """The committed redaction is fetched, so a newer one never fails the text."""
    rec = _record("§ 1.", "(1) Seadus reguleerib asju. (2) Teine lõige.")
    calls: list = []
    [res] = ctf.check_act(
        ACT, [rec], **_fetchers({ACT: RT_XML, NEWER: RT_XML_NEWER}, current=NEWER, calls=calls)
    )
    assert res.status == ctf.MATCH
    assert res.current_id == NEWER
    assert ("xml", NEWER) not in calls


def test_text_from_newer_redaction_is_stale_source_id() -> None:
    rec = _record("§ 1.", "(1) Seadus reguleerib asju. (2) Teine lõige uues redaktsioonis.")
    [res] = ctf.check_act(
        ACT, [rec], **_fetchers({ACT: RT_XML, NEWER: RT_XML_NEWER}, current=NEWER)
    )
    assert res.status == ctf.STALE_SOURCE_ID
    assert res.status not in ctf.FAILING_STATUSES


def test_drift_does_not_excuse_a_real_mismatch() -> None:
    rec = _record("§ 1.", "(1) Midagi muud. (2) Teine lõige.")
    [res] = ctf.check_act(
        ACT, [rec], **_fetchers({ACT: RT_XML, NEWER: RT_XML_NEWER}, current=NEWER)
    )
    assert res.status == ctf.MISMATCH


def _raise(exc: Exception):
    def _f(_act_id: str):
        raise exc

    return _f


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=resp)


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (requests.ConnectionError("down"), ctf.UNREACHABLE),
        (requests.Timeout("slow"), ctf.UNREACHABLE),
        (_http_error(503), ctf.UNREACHABLE),
        (_http_error(404), ctf.FETCH_ERROR),
        (rt.RTFormatError("HTML shell"), ctf.FORMAT_ERROR),
    ],
)
def test_check_act_classifies_fetch_errors(exc: Exception, status: str) -> None:
    rec = _record("§ 1.", "x")
    [res] = ctf.check_act(
        ACT, [rec], fetch_xml_root=_raise(exc), fetch_metadata=lambda _a: {"currentId": ACT}
    )
    assert res.status == status


# --- run_sample exit codes --------------------------------------------------------


def test_run_sample_all_match_exits_zero() -> None:
    pop = [
        _record("§ 1.", "(1) Seadus reguleerib asju. (2) Teine lõige."),
        _record("§ 7¹.", "Lisasätte tekst.", iri="estleg:T_Par_7_1"),
    ]
    report = ctf.run_sample(pop, 25, 1, **_fetchers({ACT: RT_XML}))
    assert report.exit_code == ctf.EXIT_OK
    assert report.count(ctf.MATCH) == 2


def test_run_sample_unreachable_exits_two_and_stops_fetching() -> None:
    pop = [_record("§ 1.", "x", iri=f"estleg:T{i}_Par_1", act=str(i)) for i in range(5)]
    calls: list = []

    def meta(act_id: str) -> dict:
        calls.append(act_id)
        raise requests.ConnectionError("down")

    report = ctf.run_sample(pop, 5, 1, fetch_xml_root=_raise(AssertionError()), fetch_metadata=meta)
    assert report.exit_code == ctf.EXIT_UNREACHABLE
    assert len(calls) == 1
    assert report.count(ctf.UNREACHABLE) == 5


def test_run_sample_mismatch_dominates_unreachable() -> None:
    pop = [
        _record("§ 1.", "vale tekst"),
        _record("§ 1.", "x", iri="estleg:U_Par_1", act="999"),
    ]

    def fetch_xml_root(act_id: str) -> ET.Element:
        if act_id == "999":
            raise requests.ConnectionError("down")
        return _root()

    report = ctf.run_sample(
        pop, 2, 1, fetch_xml_root=fetch_xml_root, fetch_metadata=lambda a: {"currentId": a}
    )
    assert report.exit_code == ctf.EXIT_FAIL


def test_choose_sample_is_seeded() -> None:
    pop = [_record("§ 1.", "x", iri=f"estleg:T_Par_{i}") for i in range(100)]
    assert ctf.choose_sample(pop, 10, 7) == ctf.choose_sample(pop, 10, 7)
    assert ctf.choose_sample(pop, 10, 7) != ctf.choose_sample(pop, 10, 8)
    assert len(ctf.choose_sample(pop, 500, 7)) == 100


# --- corpus walk and coverage baseline ------------------------------------------


def _write_peep(krr: Path, name: str, graph: list[dict]) -> None:
    (krr / name).write_text(json.dumps({"@context": {}, "@graph": graph}), encoding="utf-8")


def _prov(iri: str, part_of: str, text: str | None) -> dict:
    node = {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": "§ 1.",
        "estleg:partOfAct": {"@id": part_of},
        "estleg:summary": "s",
    }
    if text is not None:
        node["estleg:legalText"] = text
    return node


@pytest.fixture
def krr(tmp_path: Path) -> Path:
    src = {"@id": "https://www.riigiteataja.ee/akt/111.xml"}
    kehtiv = {"@value": "2026-05-24", "@type": "xsd:date"}
    _write_peep(
        tmp_path,
        "good_peep.json",
        [
            {"@id": "estleg:G_Map", "@type": ["estleg:Act", "estleg:Law"],
             "estleg:contentStatus": "structuredBody", "dcterms:source": src, "estleg:kehtiv": kehtiv},
            _prov("estleg:G_Par_1", "estleg:G_Map", "(1) Tekst."),
            _prov("estleg:G_Par_2", "estleg:G_Map", None),
        ],
    )
    _write_peep(
        tmp_path,
        "bare_peep.json",
        [
            {"@id": "estleg:B_Map", "@type": ["estleg:Act", "estleg:Law"],
             "estleg:contentStatus": "noStructuredBody"},
            _prov("estleg:B_Par_1", "estleg:B_Map", None),
        ],
    )
    _write_peep(
        tmp_path,
        "multi_osa1_peep.json",
        [
            {"@id": "estleg:M_Osa1", "@type": ["estleg:Part"],
             "estleg:contentStatus": "structuredBody", "dcterms:source": {"@id": "https://www.riigiteataja.ee/akt/333.xml"},
             "estleg:kehtiv": kehtiv},
            _prov("estleg:M_Osa1_Par_1", "estleg:M_Map", "(1) Osa tekst."),
        ],
    )
    regs = tmp_path / "regulations" / "riik"
    regs.mkdir(parents=True)
    _write_peep(
        regs,
        "reg_peep.json",
        [{"@id": "estleg:Reg_1_Map", "@type": ["estleg:Act", "estleg:Regulation"],
          "estleg:contentStatus": "structuredBody"}],
    )
    return tmp_path


def test_measure_coverage_uses_file_heads_and_skips_regulations(krr: Path) -> None:
    assert ctf.measure_coverage(krr) == {
        "missing_legal_text": ["estleg:G_Par_2"],
        "root_missing_source": ["estleg:B_Map"],
        "root_missing_kehtiv": ["estleg:B_Map"],
    }


def test_collect_sample_population(krr: Path) -> None:
    pop = ctf.collect_sample_population(krr)
    assert [(r.iri, r.act_id) for r in pop] == [
        ("estleg:G_Par_1", "111"),
        ("estleg:M_Osa1_Par_1", "333"),
    ]


def test_coverage_baseline_may_only_shrink(krr: Path, tmp_path: Path, capsys) -> None:
    baseline = tmp_path / "baseline.json"
    assert ctf.run_coverage(krr, baseline) == ctf.EXIT_FAIL  # missing baseline
    assert ctf.run_coverage(krr, baseline, update=True) == ctf.EXIT_OK
    assert ctf.run_coverage(krr, baseline) == ctf.EXIT_OK

    # A new offender fails.
    _write_peep(
        krr,
        "zz_new_peep.json",
        [{"@id": "estleg:Z_Map", "@type": ["estleg:Act", "estleg:Law"],
          "estleg:contentStatus": "structuredBody"}],
    )
    capsys.readouterr()
    assert ctf.run_coverage(krr, baseline) == ctf.EXIT_FAIL
    out = capsys.readouterr().out
    assert "::error::root_missing_source: 1 new offender(s)" in out
    assert "estleg:Z_Map" in out

    # Fixing an offender passes and asks for the baseline to shrink.
    (krr / "zz_new_peep.json").unlink()
    (krr / "bare_peep.json").unlink()
    assert ctf.run_coverage(krr, baseline) == ctf.EXIT_OK
    assert "shrink the baseline" in capsys.readouterr().out


@pytest.mark.committed
def test_committed_corpus_within_committed_baseline() -> None:
    """The release gate itself: the corpus may not exceed the baseline."""
    current = ctf.measure_coverage()
    baseline = ctf.load_baseline()
    for delta in ctf.compare_coverage(current, baseline):
        assert not delta.new, f"{delta.rule}: new offenders {delta.new[:10]}"


@pytest.mark.committed
def test_committed_baseline_counts_are_recorded() -> None:
    data = json.loads(ctf.BASELINE_PATH.read_text(encoding="utf-8"))
    for rule in ctf.COVERAGE_RULES:
        entry = data["rules"][rule]
        assert entry["count"] == len(entry["offenders"])
        assert entry["offenders"] == sorted(entry["offenders"])


# --- CLI ----------------------------------------------------------------------------


def test_main_sample_offline(krr: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    xml_by_id = {"111": RT_XML, "333": RT_XML}

    def fake_fetch_xml(url, cache_name, *a, **kw):
        assert kw.get("strict") is True and kw.get("refresh") is True
        return _root(xml_by_id[rt.rt_act_id(url)])

    monkeypatch.setattr(rt, "fetch_xml", fake_fetch_xml)
    monkeypatch.setattr(rt, "fetch_act_metadata", lambda a: {"currentId": "444" if a == "333" else a})
    code = ctf.main(["--krr-dir", str(krr), "--sample", "5", "--seed", "1"])
    out = capsys.readouterr().out
    # Corpus "(1) Tekst." vs RT "(1) Seadus reguleerib asju. (2) Teine lõige."
    assert code == ctf.EXIT_FAIL
    assert "[mismatch] estleg:G_Par_1" in out
    assert "::error::legal-text fidelity" in out
    # M_Osa1_Par_1 likewise mismatches, and its act has a newer consolidation.
    assert "newer consolidation available for act 333: RT kehtivId is 444" in out


def test_main_unreachable_prints_warning(krr: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def down(*_a, **_kw):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(rt, "fetch_act_metadata", down)
    monkeypatch.setattr(rt, "fetch_xml", down)
    assert ctf.main(["--krr-dir", str(krr)]) == ctf.EXIT_UNREACHABLE
    assert "::warning::Riigi Teataja unreachable" in capsys.readouterr().out


def test_update_baseline_requires_coverage() -> None:
    with pytest.raises(SystemExit):
        ctf.parse_args(["--update-baseline"])


# --- live (network; opt-in) ---------------------------------------------------------

LIVE = os.environ.get("ESTLEG_LIVE_CANARY") == "1"


@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="set ESTLEG_LIVE_CANARY=1 to GET live RT acts")
def test_live_text_fidelity_sample(tmp_path: Path) -> None:
    """End-to-end against RT: every sampled act must fetch, parse and locate.

    Text mismatches are the gate's own verdict (see the CI job), so this
    asserts only that no sample failed for a pipeline reason.
    """
    report = ctf.run_sample(
        ctf.collect_sample_population(),
        3,
        20261008,
        fetch_xml_root=ctf.make_xml_fetcher(tmp_path),
        fetch_metadata=rt.fetch_act_metadata,
    )
    if report.count(ctf.UNREACHABLE):
        pytest.skip("RT unreachable")
    bad = [r for r in report.results if r.status in {ctf.NOT_FOUND, ctf.FETCH_ERROR, ctf.FORMAT_ERROR}]
    assert not bad, [(r.record.iri, r.status, r.detail) for r in bad]
