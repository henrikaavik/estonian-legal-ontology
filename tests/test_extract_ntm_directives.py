"""#711: the RT normitehniline märkus parser and its asserted-transposition pass."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from estleg import extract_ntm_directives as ntm
from estleg.law_structure import normtehnmarkus_texts

REPO_ROOT = Path(__file__).resolve().parent.parent
KARS_XML = REPO_ROOT / "data" / "riigiteataja" / "karistusseadustik.xml"

KARS_NTM = (
    "Euroopa Parlamendi ja nõukogu direktiiv 2001/29/EÜ autoriõiguse … (ELT L 167, "
    "22.06.2001, lk 10–19); Euroopa Parlamendi ja nõukogu direktiiv 2005/35/EÜ, mis "
    "käsitleb laevade põhjustatud merereostust … (ELT L 255, 30.09.2005, lk 11–21); "
    "Euroopa Parlamendi ja nõukogu direktiiv 2009/123/EÜ, millega muudetakse "
    "direktiivi 2005/35/EÜ … (ELT L 280, 27.10.2009, lk 52–55); Euroopa Parlamendi "
    "ja nõukogu direktiiv 2011/93/EL, … mis asendab nõukogu raamotsuse 2004/68/JSK "
    "(ELT L 335, 17.12.2011, lk 1–14), parandus (ELT L 18, 21.01.2012, lk 7)."
)


@pytest.mark.parametrize(
    ("year", "number", "celex"),
    [
        ("2001", "29", "32001L0029"),
        ("89", "391", "31989L0391"),
        ("2015", "849", "32015L0849"),
        ("2019", "1024", "32019L1024"),
        ("1850", "1", None),
        ("2001", "12345", None),
    ],
)
def test_directive_celex(year, number, celex):
    assert ntm.directive_celex(year, number) == celex


def test_first_instrument_per_item_only():
    # 2009/123 amends 2005/35: the amended directive is not a second assertion
    # of that item, and the framework decision 2004/68 is not a directive.
    assert ntm.ntm_directive_celexes(KARS_NTM) == [
        "32001L0029",
        "32005L0035",
        "32009L0123",
        "32011L0093",
    ]


def test_post_2015_numbering_and_old_series():
    text = (
        "Euroopa Parlamendi ja nõukogu direktiiv (EL) 2015/849 … (ELT L 141, "
        "05.06.2015, lk 73–117); Nõukogu direktiiv 89/391/EMÜ … (EÜT L 183, "
        "29.06.1989, lk 1–8)"
    )
    assert ntm.ntm_directive_celexes(text) == ["32015L0849", "31989L0391"]


def test_regulation_item_that_repeals_a_directive_asserts_nothing():
    text = (
        "Euroopa Parlamendi ja nõukogu määrus (EL) 2016/679 …, millega tunnistatakse "
        "kehtetuks direktiiv 95/46/EÜ (ELT L 119, 04.05.2016, lk 1–88)"
    )
    assert ntm.ntm_directive_celexes(text) == []


def test_bare_title_footnote_marker_is_skipped():
    root = ET.fromstring(
        "<akt><aktinimi><nimi><normtehnmarkus><normtehnmarkusNr>1</normtehnmarkusNr>"
        "</normtehnmarkus></nimi></aktinimi><sisu/>"
        "<normtehnmarkus><normtehnmarkusNr>1</normtehnmarkusNr>"
        "<normtehnmarkusTekst> Nõukogu direktiiv 92/43/EMÜ\n  (EÜT L 206)</normtehnmarkusTekst>"
        "</normtehnmarkus></akt>"
    )
    assert normtehnmarkus_texts(root) == ["Nõukogu direktiiv 92/43/EMÜ (EÜT L 206)"]


def test_parser_on_committed_karistusseadustik_xml():
    """Proof on the one RT XML in the repo (a 2014 KarS redaction)."""
    parsed = ntm.parse_ntm_xml(KARS_XML)
    assert parsed["global_id"] == "105022014002"
    assert parsed["tervikteksti_id"] == "1021622"
    assert len(parsed["texts"]) == 1
    assert parsed["celexes"] == [
        "32001L0029",
        "32005L0035",
        "32008L0099",
        "32009L0123",
        "32011L0093",
    ]


def test_karistusseadustik_peeps_carry_the_asserted_set():
    """Shipped data: every KarS root (map + both parts) carries exactly the
    directives the NTM of the attested current redaction lists, as far as
    the EUR-Lex directives peep knows them. The stale 2014 redaction
    (five directives) must not leak onto any part."""
    hashes = json.loads(
        (REPO_ROOT / "krr_outputs" / "fetch_content_hashes.json").read_text(encoding="utf-8")
    )
    row = hashes["karistusseadustik"]
    parsed = ntm.parse_ntm_xml(REPO_ROOT / row["cacheFile"])
    known = ntm.directive_iris(REPO_ROOT / "krr_outputs" / "eurlex" / "eurlex_directives_peep.json")
    expected = sorted(known[celex] for celex in parsed["celexes"] if celex in known)
    stale = {f"estleg:EU_{c}" for c in ntm.parse_ntm_xml(KARS_XML)["celexes"]}
    assert stale < set(expected), "current NTM is a superset of the 2014 one"
    assert len(expected) > len(stale)
    for name in (
        "karistusseadustik_map_peep.json",
        "karistusseadustik_osa1_peep.json",
        "karistusseadustik_osa2_peep.json",
    ):
        doc = json.loads((REPO_ROOT / "krr_outputs" / name).read_text(encoding="utf-8"))
        root = ntm.act_root_node(doc)
        assert root["estleg:globalId"] == row["globalId"], name
        assert sorted(item["@id"] for item in root[ntm.PROPERTY]) == expected, name


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _xml(tid: str, text: str) -> str:
    return (
        "<akt><metaandmed><globaalID>1</globaalID>"
        f"<terviktekstiGrupiID>{tid}</terviktekstiGrupiID></metaandmed>"
        f"<normtehnmarkus><normtehnmarkusTekst>{text}</normtehnmarkusTekst></normtehnmarkus></akt>"
    )


def _fixture_corpus(tmp_path: Path) -> tuple[Path, Path]:
    krr = tmp_path / "krr_outputs"
    _write(
        krr / "INDEX.json",
        {"laws": [{"name": "tubakaseadus", "files": ["tubakaseadus_peep.json"]}]},
    )
    _write(
        krr / "tubakaseadus_peep.json",
        {
            "@graph": [
                {
                    "@id": "estleg:TUBAKA_Map",
                    "@type": ["estleg:Act", "estleg:Law"],
                    "estleg:transposesDirective": [{"@id": "estleg:EU_32014L0040"}],
                }
            ]
        },
    )
    _write(
        krr / "regulations" / "riik" / "kord_t42_peep.json",
        {"@graph": [{"@id": "estleg:Reg_42_Map", "@type": ["estleg:Act"]}]},
    )
    _write(
        krr / "eurlex" / "eurlex_directives_peep.json",
        {
            "@graph": [
                {"@id": "estleg:EU_32014L0040", "estleg:celexNumber": "32014L0040"},
                {"@id": "estleg:EU_32001L0037", "estleg:celexNumber": "32001L0037"},
            ]
        },
    )
    rt = tmp_path / "rt"
    rt.mkdir()
    (rt / "tubakaseadus__tid99.xml").write_text(
        _xml("99", "Euroopa Parlamendi ja nõukogu direktiiv 2001/37/EÜ (EÜT L 194); "
                   "Nõukogu direktiiv 2099/9999/EÜ"),
        encoding="utf-8",
    )
    (rt / "reg_t42.xml").write_text(
        _xml("42", "Euroopa Parlamendi ja nõukogu direktiiv 2014/40/EL (ELT L 127)"),
        encoding="utf-8",
    )
    (rt / "orphan.xml").write_text(_xml("7", "direktiiv 2014/40/EL"), encoding="utf-8")
    return krr, rt


def test_apply_ntm_resolves_laws_and_regulations_and_is_idempotent(tmp_path):
    krr, rt = _fixture_corpus(tmp_path)
    stats = ntm.apply_ntm([rt], krr_dir=krr)
    assert stats["xml_files"] == 3
    assert stats["xml_unresolved"] == ["orphan.xml"]
    # A directive missing from the EUR-Lex peep is reported, never minted.
    assert stats["directives_not_in_eurlex"] == ["32099L9999"]
    assert stats["files_changed"] == 2

    law = json.loads((krr / "tubakaseadus_peep.json").read_text(encoding="utf-8"))
    assert law["@graph"][0][ntm.PROPERTY] == [{"@id": "estleg:EU_32001L0037"}]
    reg = json.loads(
        (krr / "regulations" / "riik" / "kord_t42_peep.json").read_text(encoding="utf-8")
    )
    assert reg["@graph"][0][ntm.PROPERTY] == [{"@id": "estleg:EU_32014L0040"}]

    assert ntm.apply_ntm([rt], krr_dir=krr)["files_changed"] == 0

    report = ntm.build_report(krr)
    by_act = {row["act"]: row for row in report["acts"]}
    assert by_act["tubakaseadus"]["asserted_not_notified"] == ["estleg:EU_32001L0037"]
    assert by_act["tubakaseadus"]["notified_not_asserted"] == ["estleg:EU_32014L0040"]
    assert by_act["kord_t42"]["asserted_not_notified"] == ["estleg:EU_32014L0040"]
    assert report["acts_with_assertions"] == 2


def test_reprocessing_an_act_replaces_its_asserted_set(tmp_path):
    krr, rt = _fixture_corpus(tmp_path)
    ntm.apply_ntm([rt], krr_dir=krr)
    (rt / "reg_t42.xml").write_text(_xml("42", "Seaduses ei ole direktiive"), encoding="utf-8")
    ntm.apply_ntm([rt / "reg_t42.xml"], krr_dir=krr)
    reg = json.loads(
        (krr / "regulations" / "riik" / "kord_t42_peep.json").read_text(encoding="utf-8")
    )
    assert ntm.PROPERTY not in reg["@graph"][0]


def test_stale_cached_redaction_never_overwrites_the_current_one(tmp_path):
    """A stale ``<slug>.xml`` and the current ``<slug>__tid<N>.xml`` both
    resolve to the multipart act's peeps by stem; only the XML whose
    globaalID matches the roots' estleg:globalId writes, in either order."""
    krr = tmp_path / "krr"
    for name in ("seadus_map_peep.json", "seadus_osa1_peep.json"):
        _write(krr / name, {"@graph": [{"@id": f"estleg:{name}", "@type": ["estleg:Part"],
                                        "estleg:globalId": "222"}]})
    _write(krr / "INDEX.json", {"laws": [{"name": "seadus",
                                          "files": ["seadus_map_peep.json", "seadus_osa1_peep.json"]}]})
    _write(krr / "eurlex" / "eurlex_directives_peep.json", {"@graph": [
        {"@id": "estleg:EU_32001L0029", "estleg:celexNumber": "32001L0029"},
        {"@id": "estleg:EU_32014L0062", "estleg:celexNumber": "32014L0062"},
    ]})
    rt = tmp_path / "rt"
    rt.mkdir()

    def xml(gid: str, text: str) -> str:
        return (f"<akt><metaandmed><globaalID>{gid}</globaalID></metaandmed>"
                f"<normtehnmarkus><normtehnmarkusTekst>{text}</normtehnmarkusTekst>"
                "</normtehnmarkus></akt>")

    stale = rt / "seadus.xml"
    current = rt / "seadus__tid9.xml"
    stale.write_text(xml("111", "Euroopa Parlamendi ja nõukogu direktiiv 2001/29/EÜ"), encoding="utf-8")
    current.write_text(
        xml("222", "Euroopa Parlamendi ja nõukogu direktiiv 2001/29/EÜ; "
                   "Euroopa Parlamendi ja nõukogu direktiiv 2014/62/EL"),
        encoding="utf-8",
    )
    expected = [{"@id": "estleg:EU_32001L0029"}, {"@id": "estleg:EU_32014L0062"}]
    for order in ([stale, current], [current, stale]):
        stats = ntm.apply_ntm(order, krr_dir=krr)
        assert stats["xml_other_redaction"] == ["seadus.xml"]
        for name in ("seadus_map_peep.json", "seadus_osa1_peep.json"):
            root = json.loads((krr / name).read_text(encoding="utf-8"))["@graph"][0]
            assert root[ntm.PROPERTY] == expected, (order, name)


def test_attested_cache_file_writes_despite_uuid_globaalid(tmp_path):
    """Some RT XML carries a UUID globaalID while the root has the numeric
    API id; the fetch_content_hashes.json attestation bridges the two."""
    krr = tmp_path / "krr"
    _write(krr / "leping_peep.json", {"@graph": [{"@id": "estleg:L", "@type": ["estleg:Law"],
                                                  "estleg:globalId": "204112010005"}]})
    _write(krr / "INDEX.json", {"laws": [{"name": "leping", "files": ["leping_peep.json"]}]})
    _write(krr / "eurlex" / "eurlex_directives_peep.json", {"@graph": [
        {"@id": "estleg:EU_32001L0029", "estleg:celexNumber": "32001L0029"}]})
    _write(krr / "fetch_content_hashes.json", {"leping": {
        "globalId": "204112010005", "cacheFile": "data/riigiteataja/leping__tid5.xml"}})
    rt = tmp_path / "rt"
    rt.mkdir()
    (rt / "leping__tid5.xml").write_text(
        "<akt><metaandmed><globaalID>0e73a759-a614-450b-a625-7a963d785e78</globaalID>"
        "</metaandmed><normtehnmarkus><normtehnmarkusTekst>"
        "Euroopa Parlamendi ja nõukogu direktiiv 2001/29/EÜ"
        "</normtehnmarkusTekst></normtehnmarkus></akt>",
        encoding="utf-8",
    )
    stats = ntm.apply_ntm([rt], krr_dir=krr)
    assert stats["xml_other_redaction"] == []
    root = json.loads((krr / "leping_peep.json").read_text(encoding="utf-8"))["@graph"][0]
    assert root[ntm.PROPERTY] == [{"@id": "estleg:EU_32001L0029"}]


def test_old_cached_redaction_cannot_replace_current_assertions(tmp_path):
    krr, rt = _fixture_corpus(tmp_path)
    path = krr / "tubakaseadus_peep.json"
    doc = json.loads(path.read_text())
    doc["@graph"][0]["dcterms:source"] = {"@id": "https://www.riigiteataja.ee/akt/2.xml"}
    doc["@graph"][0][ntm.PROPERTY] = [{"@id": "estleg:EU_32014L0040"}]
    _write(path, doc)
    before = path.read_bytes()
    ntm.apply_ntm([rt / "tubakaseadus__tid99.xml"], krr_dir=krr)
    assert path.read_bytes() == before
