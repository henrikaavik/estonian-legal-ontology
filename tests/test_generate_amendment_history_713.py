"""#713: provision-level ``amends`` and the version join inside ``main()``.

The fixture ``tests/fixtures/amendment_history/kars_trimmed.xml`` is a trimmed
copy of the committed KarS XML: five amending acts with markers at act,
chapter, paragrahv, loige, alampunkt and repealed-loige level, classic and
post-2010 publication marks.
"""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import estleg.generate_amendment_history as gah
from estleg import link_amendment_versions as lav

FIXTURE_XML = Path(__file__).parent / "fixtures" / "amendment_history" / "kars_trimmed.xml"
NS = "{tyviseadus_1_10.02.2010}"


def _records_by_act() -> dict[str, dict]:
    records = gah.extract_amendments_from_xml(FIXTURE_XML)
    return {r["akt_viide"]: r for r in records}


# --------------------------------------------------------------------------
# Marker extraction
# --------------------------------------------------------------------------


def test_extract_keeps_provision_context_through_dedupe() -> None:
    records = gah.extract_amendments_from_xml(FIXTURE_XML)
    # A, E, B classic refs; C and D dated (tuple-keyed). The two undated
    # post-2010 markers are folded into C and D, not separate records.
    assert [r["akt_viide"] for r in records] == [
        "110025", "969347", "765405", "105072013002", "110072012002",
    ]
    by_act = {r["akt_viide"]: r for r in records}
    assert by_act["110025"]["provision_refs"] == []  # act level
    assert by_act["969347"]["provision_refs"] == []  # chapter level
    b = by_act["765405"]
    assert b["provision_refs"] == [("7", None), ("7", "1")]  # paragrahv + alampunkt
    assert b["marker_count"] == 2
    c = by_act["105072013002"]
    assert c["provision_refs"] == [("9_1", "1"), ("8", "1")]  # superscript § 9¹; orphan
    assert c["publication_date"] == "2013-07-05"
    assert c["rt_reference"] is None
    d = by_act["110072012002"]
    # The repealed lõige has no body (no Subsection node) → paragraph ref.
    assert d["provision_refs"] == [("431", "1"), ("431", None)]
    assert d["kinds"] == ["repeals"]
    assert d["publication_date"] == "2012-07-10"


def test_modern_rt_citations_flag_keys_post_2010_markers() -> None:
    records = gah.extract_amendments_from_xml(FIXTURE_XML, modern_rt_citations=True)
    by_act = {r["akt_viide"]: r for r in records}
    assert by_act["105072013002"]["rt_reference"] == "RT I, 05.07.2013, 2"
    assert by_act["105072013002"]["provision_refs"] == [("8", "1"), ("9_1", "1")]
    assert by_act["110072012002"]["rt_reference"] == "RT I, 10.07.2012, 2"
    assert len(records) == 5


def _marker(own_text: str = "", parent_text: str = "") -> tuple[ET.Element, ET.Element]:
    parent = ET.Element(f"{NS}loige")
    if parent_text:
        sisu = ET.SubElement(parent, f"{NS}sisuTekst")
        ET.SubElement(sisu, f"{NS}tavatekst").text = parent_text
    marker = ET.SubElement(parent, f"{NS}muutmismarge")
    if own_text:
        ET.SubElement(marker, f"{NS}tavatekst").text = own_text
    return marker, parent


@pytest.mark.parametrize(
    ("own", "lead", "expected"),
    [
        ("Kehtetu - ", "", "repeals"),
        ("Riigikohtu üldkogu otsus tunnistab § 87² lg 2 kehtetuks.", "", "repeals"),
        ("", "Kehtetu - RT I 2010", "repeals"),
        ("lõiget täiendatud", "", "supplements"),
        ("sõnastatud uues redaktsioonis", "", "amends"),
        ("; jõustumisaeg muudetud 01.07.2014 [RT I, 22.12.2013, 1]", "", None),
        (", osaliselt 01.01.2006", "", None),
        ("", "Kohus võib tunnistada otsuse kehtetuks.", None),  # beyond the lead window
    ],
)
def test_infer_amendment_kind_only_from_explicit_tokens(own, lead, expected) -> None:
    marker, parent = _marker(own, lead)
    assert gah.infer_amendment_kind(marker, parent) == expected


# --------------------------------------------------------------------------
# Provision resolution / amends payload
# --------------------------------------------------------------------------


def _provision(iri: str) -> dict:
    return {"@id": iri, "@type": ["owl:NamedIndividual", "estleg:LegalProvision"]}


def _subsection(iri: str) -> dict:
    return {"@id": iri, "@type": ["estleg:Subsection", "owl:NamedIndividual"]}


def test_provision_index_resolves_only_existing_unambiguous_iris() -> None:
    index = gah.ProvisionIndex([{"@graph": [
        _provision("estleg:TOY_Par_7"),
        _subsection("estleg:TOY_Par_7_Lg_1"),
        _provision("estleg:TOY_Par_22"),
        _provision("estleg:TOY_Par_22_x2"),  # duplicate § number
    ]}])
    assert index.resolve(("7", "1")) == ("estleg:TOY_Par_7_Lg_1", "resolved_subsection")
    assert index.resolve(("7", None)) == ("estleg:TOY_Par_7", "resolved_paragraph")
    assert index.resolve(("7", "9")) == ("estleg:TOY_Par_7", "subsection_fell_back_to_paragraph")
    assert index.resolve(("22", None)) == (None, "unresolved")
    assert index.resolve(("99", "1")) == (None, "unresolved")


def test_amends_payload_keeps_act_root_first_and_shape_without_provisions() -> None:
    assert gah.amends_value_with_provisions(["estleg:A"], []) == {"@id": "estleg:A"}
    assert gah.amends_value_with_provisions(["estleg:A", "estleg:B"], []) == [
        {"@id": "estleg:A"}, {"@id": "estleg:B"}]
    assert gah.amends_value_with_provisions(
        ["estleg:A"], ["estleg:A_Par_1", "estleg:A_Par_1", "estleg:A"]
    ) == [{"@id": "estleg:A"}, {"@id": "estleg:A_Par_1"}]
    assert gah.amends_value_with_provisions([], ["estleg:A_Par_1"]) is None


# --------------------------------------------------------------------------
# main(): the canonical regeneration path
# --------------------------------------------------------------------------


def _toy_peep() -> dict:
    return {"@context": {}, "@graph": [
        {"@id": "estleg:TOY_Map", "@type": ["owl:NamedIndividual", "estleg:Act", "estleg:Law"],
         "dc:source": "Toyseadus", "rdfs:label": "Toyseadus"},
        _provision("estleg:TOY_Par_7"),
        _subsection("estleg:TOY_Par_7_Lg_1"),
        _provision("estleg:TOY_Par_8"),
        _subsection("estleg:TOY_Par_8_Lg_1"),
        _provision("estleg:TOY_Par_9_1"),
        _subsection("estleg:TOY_Par_9_1_Lg_1"),
        _provision("estleg:TOY_Par_431"),
        _subsection("estleg:TOY_Par_431_Lg_1"),
    ]}


def _version(iri: str, valid_from: str) -> dict:
    return {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
        "estleg:versionValidFrom": {"@value": valid_from, "@type": "xsd:date"},
    }


@pytest.fixture
def toy_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    krr = tmp_path / "krr_outputs"
    (krr / "provision_versions").mkdir(parents=True)
    (krr / "amendments").mkdir()
    xml_dir = tmp_path / "data" / "riigiteataja"
    xml_dir.mkdir(parents=True)
    shutil.copy(FIXTURE_XML, xml_dir / "toyseadus.xml")
    (krr / "toyseadus_peep.json").write_text(json.dumps(_toy_peep()), encoding="utf-8")
    (krr / "provision_versions" / "toyseadus.jsonld").write_text(json.dumps({"@graph": [
        _version("estleg:TOY_Par_7_v1", "2004-07-01"),
        _version("estleg:TOY_Par_8_v2", "2020-01-01"),
    ]}), encoding="utf-8")
    monkeypatch.setattr(gah, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gah, "KRR_DIR", krr)
    monkeypatch.setattr(gah, "AMENDMENTS_DIR", krr / "amendments")
    monkeypatch.setattr(gah, "EELNOUD_DIR", krr / "eelnoud")
    monkeypatch.setattr(gah, "DATA_DIR", xml_dir)
    monkeypatch.setattr(gah, "LAW_ABBREVIATIONS_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(gah, "iter_peep_files", lambda **_: sorted(krr.glob("*_peep.json")))
    return krr


def _chain_events(krr: Path) -> dict[str, dict]:
    doc = json.loads((krr / "amendments" / "amendments_toyseadus.json").read_text())
    return {n["@id"]: n for n in doc["@graph"] if "estleg:AmendmentEvent" in n["@type"]}


def _event_for(events: dict[str, dict], rt_or_label: str) -> dict:
    return next(e for e in events.values() if rt_or_label in e["rdfs:label"])


def _ids(value) -> list[str]:
    return [v["@id"] for v in (value if isinstance(value, list) else [value])]


def test_main_writes_provision_amends_and_runs_the_version_join(toy_repo: Path) -> None:
    assert gah.main() == 0
    events = _chain_events(toy_repo)
    root = "estleg:TOY_Map"
    # Act- and chapter-level markers keep the single-object payload.
    assert _event_for(events, "RT I, 2002, 44, 284")["estleg:amends"] == {"@id": root}
    assert _event_for(events, "RT I, 2005, 68, 529")["estleg:amends"] == {"@id": root}
    b = _event_for(events, "RT I, 2004, 46, 329")
    assert _ids(b["estleg:amends"]) == [root, "estleg:TOY_Par_7", "estleg:TOY_Par_7_Lg_1"]
    c = _event_for(events, "2013-06-12")
    assert _ids(c["estleg:amends"]) == [root, "estleg:TOY_Par_9_1_Lg_1", "estleg:TOY_Par_8_Lg_1"]
    assert c["estleg:publicationDate"] == {"@value": "2013-07-05", "@type": "xsd:date"}
    d = _event_for(events, "2012-06-13")
    assert _ids(d["estleg:amends"]) == [root, "estleg:TOY_Par_431_Lg_1", "estleg:TOY_Par_431"]
    assert gah.AMENDMENT_KIND_PROPERTY not in d  # off until the CV declares it

    # Version join ran inside main(): B matched by date, 2020 minted as _vf_.
    assert b["estleg:resultedInVersion"] == [{"@id": "estleg:TOY_Par_7_v1"}]
    vf = events["estleg:Amendment_TOY_vf_20200101"]
    assert vf["estleg:amends"] == {"@id": root}
    assert vf["estleg:resultedInVersion"] == [{"@id": "estleg:TOY_Par_8_v2"}]
    peep = json.loads((toy_repo / "toyseadus_peep.json").read_text())
    act = peep["@graph"][0]
    assert act["estleg:lastAmendmentDate"] == {"@value": "2020-01-01", "@type": "xsd:date"}
    assert len(act["estleg:amendedBy"]) == 5  # RT-derived events only

    summary = json.loads(
        (toy_repo / "reports" / "amendment_history_report.json").read_text()
    )["summary"]
    assert summary["laws_version_joined"] == 1
    assert summary["version_layer_events"] == 1
    assert summary["provision_amends"]["provision_edges"] == 6
    assert summary["provision_amends"]["unresolved"] == 0
    assert summary["provision_amends"]["kinds"] == {"repeals": 1}


def test_main_is_idempotent_and_the_dag_join_is_a_no_op_after_it(toy_repo: Path) -> None:
    gah.main()
    chain = toy_repo / "amendments" / "amendments_toyseadus.json"
    first = chain.read_bytes()
    gah.main()
    assert chain.read_bytes() == first
    stats = lav.join_version_layer(toy_repo, dry_run=True)
    assert (stats.chains_seen, stats.chains_changed, stats.peeps_changed) == (1, 0, 0)


def test_main_can_emit_the_inferred_kind_behind_the_flag(toy_repo: Path) -> None:
    gah.main(emit_amendment_kind=True)
    d = _event_for(_chain_events(toy_repo), "2012-06-13")
    assert d[gah.AMENDMENT_KIND_PROPERTY] == "repeals"


def test_cli_exposes_the_kind_flag(toy_repo: Path) -> None:
    assert gah.cli(["--emit-amendment-kind"]) == 0
    d = _event_for(_chain_events(toy_repo), "2012-06-13")
    assert d[gah.AMENDMENT_KIND_PROPERTY] == "repeals"


# --------------------------------------------------------------------------
# Fixed point of the join on committed chains
# --------------------------------------------------------------------------

COMMITTED_LAWS = {
    # multipart (osa1 + osa2 → list-valued act-root amends), 45 _vf_ events
    "karistusseadustik": ("karistusseadustik_osa1", "karistusseadustik_osa2"),
    "rahvusvahelise_raudteeveo_konventsiooniga_uhinemise_seadus": (
        "rahvusvahelise_raudteeveo_konventsiooniga_uhinemise_seadus",),
    "laanemere_piirkonna_merekeskkonna_kaitse_konventsiooni_ratifitseerimise_seadus": (
        "laanemere_piirkonna_merekeskkonna_kaitse_konventsiooni_ratifitseerimise_seadus",),
}


def _stage_committed(corpus_krr, dest: Path) -> None:
    for base, slugs in COMMITTED_LAWS.items():
        for rel in (
            f"amendments/amendments_{base}.json",
            f"provision_versions/{base}.jsonld",
            *(f"{slug}_peep.json" for slug in slugs),
        ):
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(corpus_krr.path(rel), target)


def _strip_version_layer(chain_path: Path) -> None:
    doc = json.loads(chain_path.read_text(encoding="utf-8"))
    doc["@graph"] = [n for n in doc["@graph"] if not gah._is_version_layer_event(n)]
    for node in doc["@graph"]:
        node.pop("estleg:resultedInVersion", None)
    chain_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _graph_by_id(path: Path) -> dict[str, dict]:
    return {n["@id"]: n for n in json.loads(path.read_text(encoding="utf-8"))["@graph"]}


def test_join_is_a_fixed_point_on_committed_chains(corpus_krr, tmp_path: Path) -> None:
    _stage_committed(corpus_krr, tmp_path)
    stats = lav.join_version_layer(tmp_path, dry_run=True)
    assert stats.as_dict() == {
        "laws_with_versions": 3, "chains_seen": 3, "chains_changed": 0,
        "chains_absent": 0, "peeps_changed": 0,
    }


def test_join_restores_stripped_version_events_on_committed_chains(
    corpus_krr, tmp_path: Path
) -> None:
    """A chain written without the join (pre-#713 main()) gets every _vf_
    event and resultedInVersion stamp back, identical to the committed file."""
    _stage_committed(corpus_krr, tmp_path)
    for base in COMMITTED_LAWS:
        _strip_version_layer(tmp_path / "amendments" / f"amendments_{base}.json")
    stats = lav.join_version_layer(tmp_path)
    assert stats.chains_changed == 3
    for base in COMMITTED_LAWS:
        rel = f"amendments/amendments_{base}.json"
        assert _graph_by_id(tmp_path / rel) == _graph_by_id(corpus_krr.path(rel))
