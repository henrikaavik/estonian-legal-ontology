"""Tests for scripts/generate_provision_versions.py (issue #198).

Locks down the historical-redaction ProvisionVersion population mechanism:
provision-level diffing across redactions, the ``supersededByVersion`` chain,
validity-interval closing, redaction-list reconstruction from the Riigi Teataja
search API, ``--limit`` / ``--law`` selection, and SHACL conformance of the
emitted ``estleg:ProvisionVersion`` nodes.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import generate_provision_versions as gpv
from generate_provision_versions import (
    LawTarget,
    Redaction,
    build_law_target,
    extract_provision_texts,
    fetch_redaction_chain,
    select_law_slugs,
    synthesise_versions,
    write_sidecar,
)

_SHAPES_PATH = gpv.REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shacl_conforms(graph_json: dict) -> tuple[bool, str]:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    data_graph = rdflib.Graph()
    data_graph.parse(data=json.dumps(graph_json), format="json-ld")
    shapes_graph = rdflib.Graph().parse(str(_SHAPES_PATH), format="turtle")
    conforms, _, msg = pyshacl.validate(
        data_graph, shacl_graph=shapes_graph, inference="rdfs"
    )
    return conforms, str(msg)


def _paragraph_xml(nr: str, *, title: str, body: str) -> str:
    return (
        f'<paragrahv><paragrahvNr>{nr}</paragrahvNr>'
        f'<paragrahvPealkiri>{title}</paragrahvPealkiri>'
        f'<loige><loigeNr>1</loigeNr><lauseOsa>{body}</lauseOsa></loige>'
        f'</paragrahv>'
    )


def _redaction_root(paragraphs: list[str]) -> ET.Element:
    return ET.fromstring("<akt>" + "".join(paragraphs) + "</akt>")


def _make_target(prefix: str = "FIX", par_suffixes: tuple[str, ...] = ("1", "2")) -> LawTarget:
    return LawTarget(
        slug="fixture_law",
        title="Fikseeritud seadus",
        prefix=prefix,
        provisions={s: f"estleg:{prefix}_Par_{s}" for s in par_suffixes},
        peep_files=[],
    )


# Three redactions: § 1 changes at r2, § 2 changes at r3.
_R1 = Redaction(global_id="111", valid_from="2010-01-01", valid_to="2014-12-31", url="/akt/111.xml")
_R2 = Redaction(global_id="222", valid_from="2015-01-01", valid_to="2019-12-31", url="/akt/222.xml")
_R3 = Redaction(global_id="333", valid_from="2020-01-01", valid_to=None, url="/akt/333.xml")


def _three_redaction_chain() -> list[tuple[Redaction, dict[str, str]]]:
    return [
        (_R1, {"1": "Algtekst paragrahv 1.", "2": "Algtekst paragrahv 2."}),
        (_R2, {"1": "MUUDETUD paragrahv 1 redaktsioonis 2.", "2": "Algtekst paragrahv 2."}),
        (_R3, {"1": "MUUDETUD paragrahv 1 redaktsioonis 2.", "2": "MUUDETUD paragrahv 2 redaktsioonis 3."}),
    ]


# ---------------------------------------------------------------------------
# Diff + chaining
# ---------------------------------------------------------------------------


class TestSynthesiseVersions:
    """`synthesise_versions` builds one ProvisionVersion per real text change."""

    def test_three_redactions_two_changes_each_provision(self):
        target = _make_target(par_suffixes=("1", "2"))
        nodes = synthesise_versions(target, _three_redaction_chain())
        by_provision: dict[str, list[dict]] = {}
        for n in nodes:
            by_provision.setdefault(n["estleg:versionOf"]["@id"], []).append(n)

        # § 1: versions at r1 and r2 (changed at r2; unchanged at r3 → no v333).
        p1 = by_provision["estleg:FIX_Par_1"]
        assert [n["@id"] for n in p1] == ["estleg:FIX_Par_1_v111", "estleg:FIX_Par_1_v222"]
        # § 2: versions at r1 and r3 (unchanged at r2 → no v222).
        p2 = by_provision["estleg:FIX_Par_2"]
        assert [n["@id"] for n in p2] == ["estleg:FIX_Par_2_v111", "estleg:FIX_Par_2_v333"]

        # supersededByVersion chains each provision's versions; absent on the last.
        assert p1[0]["estleg:supersededByVersion"] == {"@id": "estleg:FIX_Par_1_v222"}
        assert "estleg:supersededByVersion" not in p1[1]
        assert p2[0]["estleg:supersededByVersion"] == {"@id": "estleg:FIX_Par_2_v333"}
        assert "estleg:supersededByVersion" not in p2[1]

        # versionValidTo of version k == versionValidFrom of version k+1; absent on last.
        assert p1[0]["estleg:versionValidFrom"] == {"@value": "2010-01-01", "@type": "xsd:date"}
        assert p1[0]["estleg:versionValidTo"] == {"@value": "2015-01-01", "@type": "xsd:date"}
        assert "estleg:versionValidTo" not in p1[1]
        assert p2[1]["estleg:versionValidFrom"] == {"@value": "2020-01-01", "@type": "xsd:date"}
        assert "estleg:versionValidTo" not in p2[1]

        # Every node carries versionOf / versionText / versionRedactionId.
        for n in nodes:
            assert n["@type"] == ["owl:NamedIndividual", "estleg:ProvisionVersion"]
            assert n["estleg:versionOf"]["@id"].startswith("estleg:FIX_Par_")
            assert isinstance(n["estleg:versionText"], str) and n["estleg:versionText"]
            assert n["estleg:versionRedactionId"] in {"111", "222", "333"}

    def test_provision_never_changed_gets_one_version_no_successor(self):
        target = _make_target(par_suffixes=("1",))
        chain = [
            (_R1, {"1": "Sama tekst kogu aeg."}),
            (_R2, {"1": "Sama tekst kogu aeg."}),
            (_R3, {"1": "Sama tekst kogu aeg."}),
        ]
        nodes = synthesise_versions(target, chain)
        assert len(nodes) == 1
        n = nodes[0]
        assert n["@id"] == "estleg:FIX_Par_1_v111"
        assert n["estleg:versionValidFrom"] == {"@value": "2010-01-01", "@type": "xsd:date"}
        assert "estleg:supersededByVersion" not in n
        assert "estleg:versionValidTo" not in n

    def test_versionof_targets_resolve_to_law_provision_iris(self):
        # The provision map IS the set of LegalProvision IRIs the peep declares;
        # synthesise_versions must only ever point versionOf at one of those.
        target = _make_target(par_suffixes=("1", "2"))
        nodes = synthesise_versions(target, _three_redaction_chain())
        valid_targets = set(target.provisions.values())
        assert valid_targets == {"estleg:FIX_Par_1", "estleg:FIX_Par_2"}
        for n in nodes:
            assert n["estleg:versionOf"]["@id"] in valid_targets
        # And supersededByVersion only ever points at a node emitted in this graph.
        emitted = {n["@id"] for n in nodes}
        for n in nodes:
            sup = n.get("estleg:supersededByVersion")
            if sup is not None:
                assert sup["@id"] in emitted

    def test_provision_absent_from_a_redaction_does_not_break_chain(self):
        # § 1 missing in r2 (e.g. fetch glitch / mid-amendment), present again r3.
        target = _make_target(par_suffixes=("1",))
        chain = [
            (_R1, {"1": "Esimene tekst."}),
            (_R2, {}),  # § 1 not extracted here
            (_R3, {"1": "Teine tekst."}),
        ]
        nodes = synthesise_versions(target, chain)
        assert [n["@id"] for n in nodes] == ["estleg:FIX_Par_1_v111", "estleg:FIX_Par_1_v333"]
        assert nodes[0]["estleg:versionValidTo"] == {"@value": "2020-01-01", "@type": "xsd:date"}

    def test_whitespace_only_difference_is_not_a_new_version(self):
        target = _make_target(par_suffixes=("1",))
        chain = [
            (_R1, {"1": "(1) Tekst   ühe   tühikuga."}),
            (_R2, {"1": "(1) Tekst ühe tühikuga.\n"}),
        ]
        nodes = synthesise_versions(target, chain)
        assert len(nodes) == 1


# ---------------------------------------------------------------------------
# XML extraction
# ---------------------------------------------------------------------------


class TestExtractProvisionTexts:
    def test_maps_paragrahvnr_to_par_suffix(self):
        root = _redaction_root([
            _paragraph_xml("1", title="Esimene", body="Esimese paragrahvi tekst."),
            _paragraph_xml("22", title="Kahekümne teine", body="22. paragrahvi tekst."),
        ])
        texts = extract_provision_texts(root)
        assert set(texts) == {"1", "22"}
        assert "Esimese paragrahvi tekst." in texts["1"]
        assert "22. paragrahvi tekst." in texts["22"]

    def test_superscript_paragraph_gets_underscore_suffix(self):
        # `§ 22¹` (ylaIndeks="1") must map to suffix "22_1" — same as the peep IRI.
        xml = (
            '<akt><paragrahv><paragrahvNr ylaIndeks="1">22</paragrahvNr>'
            '<paragrahvPealkiri>Lisatud paragrahv</paragrahvPealkiri>'
            '<loige><loigeNr>1</loigeNr><lauseOsa>Lisatud sätte tekst.</lauseOsa></loige>'
            '</paragrahv></akt>'
        )
        texts = extract_provision_texts(ET.fromstring(xml))
        assert set(texts) == {"22_1"}


# ---------------------------------------------------------------------------
# Redaction-list reconstruction (mock the RT search API)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:  # noqa: D401 - mimic requests.Response
        return None

    def json(self) -> dict:
        return self._payload


def test_fetch_redaction_chain_sorted_oldest_to_newest_with_dates(monkeypatch: pytest.MonkeyPatch):
    title = "Näidisseadus"
    today = "2026-05-12"

    # The "full" search (no kehtiv): a mix of superseded redactions (closed
    # interval), an "announcement" entry (lopp == None, old algus — must be
    # ignored), a future-dated edition (algus > today — ignored), and out of
    # order rows. The kehtiv=today search: exactly the current edition.
    full_payload = {
        "aktid": [
            {"globaalID": 30, "pealkiri": title, "kehtivus": {"algus": "2020-01-01", "lopp": "2024-12-31"}, "muudetud": 3, "url": "/akt/30.xml"},
            {"globaalID": 10, "pealkiri": title, "kehtivus": {"algus": "2010-01-01", "lopp": "2014-12-31"}, "muudetud": 1, "url": "/akt/10.xml"},
            {"globaalID": 20, "pealkiri": title, "kehtivus": {"algus": "2015-01-01", "lopp": "2019-12-31"}, "muudetud": 2, "url": "/akt/20.xml"},
            # announcement: lopp None, algus in the past → ignored
            {"globaalID": 99, "pealkiri": title, "kehtivus": {"algus": "2015-01-01", "lopp": None}, "muudetud": 9, "url": "/akt/99.xml"},
            # future-dated → ignored
            {"globaalID": 50, "pealkiri": title, "kehtivus": {"algus": "2030-01-01", "lopp": "2030-12-31"}, "muudetud": 5, "url": "/akt/50.xml"},
            # different title → ignored
            {"globaalID": 60, "pealkiri": "Hoopis teine seadus", "kehtivus": {"algus": "2018-01-01", "lopp": "2018-12-31"}, "muudetud": 6, "url": "/akt/60.xml"},
        ]
    }
    current_payload = {
        "aktid": [
            {"globaalID": 40, "pealkiri": title, "kehtivus": {"algus": "2025-01-01", "lopp": None}, "muudetud": 4, "url": "/akt/40.xml"},
        ]
    }

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        assert url == gpv.SEARCH_URL
        if params and params.get("kehtiv"):
            return _FakeResponse(current_payload)
        return _FakeResponse(full_payload)

    monkeypatch.setattr(gpv.requests, "get", fake_get)
    chain = fetch_redaction_chain(title, today=today)
    assert [r.global_id for r in chain] == ["10", "20", "30", "40"]
    assert [r.valid_from for r in chain] == ["2010-01-01", "2015-01-01", "2020-01-01", "2025-01-01"]
    # The last (current) one is open-ended; the rest carry their closing date.
    assert chain[-1].valid_to is None
    assert chain[0].valid_to == "2014-12-31"


# ---------------------------------------------------------------------------
# Law selection (--limit / --law) and peep introspection
# ---------------------------------------------------------------------------


def _write_peep(krr: Path, slug: str, prefix: str, par_suffixes: list[str], *, title: str) -> None:
    graph = [
        {"@id": f"estleg:{prefix}_Map_2026", "@type": ["estleg:Act", "estleg:Law", "owl:Ontology"],
         "rdfs:label": f"{title} kaardistus", "dc:source": title},
        {"@id": f"estleg:LegalProvision_{prefix}", "@type": ["owl:Class"]},
    ]
    for s in par_suffixes:
        graph.append({
            "@id": f"estleg:{prefix}_Par_{s}",
            "@type": ["owl:NamedIndividual", f"estleg:LegalProvision_{prefix}"],
            "estleg:paragrahv": f"§ {s}.",
            "estleg:sourceAct": title,
        })
    (krr / f"{slug}_peep.json").write_text(
        json.dumps({"@context": {"estleg": gpv.CONTEXT["estleg"]}, "@graph": graph}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_build_law_target_reads_prefix_and_provisions_from_peep(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write_peep(krr, "naidis_seadus", "NAIDIS", ["1", "2", "5"], title="Näidisseadus")
    target = build_law_target("naidis_seadus", krr_dir=krr)
    assert target is not None
    assert target.title == "Näidisseadus"
    assert target.prefix == "NAIDIS"
    assert target.provisions == {
        "1": "estleg:NAIDIS_Par_1",
        "2": "estleg:NAIDIS_Par_2",
        "5": "estleg:NAIDIS_Par_5",
    }


def test_build_law_target_merges_multipart_osa_peeps(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    # Two osa files sharing one prefix — like Karistusseadustik.
    for osa, suffixes in (("osa1", ["1", "2"]), ("osa2", ["88", "89"])):
        graph = [
            {"@id": f"estleg:BIG_{osa}", "@type": ["estleg:Act", "estleg:Law", "owl:Ontology"],
             "rdfs:label": "Suur seadus", "dc:source": "Suur seadus"},
        ]
        for s in suffixes:
            graph.append({"@id": f"estleg:BIG_Par_{s}",
                          "@type": ["owl:NamedIndividual", "estleg:LegalProvision_BIG"],
                          "estleg:paragrahv": f"§ {s}."})
        (krr / f"suur_seadus_{osa}_peep.json").write_text(
            json.dumps({"@context": {"estleg": gpv.CONTEXT["estleg"]}, "@graph": graph}, ensure_ascii=False),
            encoding="utf-8",
        )
    target = build_law_target("suur_seadus", krr_dir=krr)
    assert target is not None
    assert target.prefix == "BIG"
    assert set(target.provisions) == {"1", "2", "88", "89"}


def test_select_law_slugs_limit_and_explicit(tmp_path: Path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    for slug in ("alfa_seadus", "beeta_seadus", "gamma_seadus"):
        _write_peep(krr, slug, slug[:4].upper(), ["1"], title=slug)
    # --limit 0 → nothing
    assert select_law_slugs(explicit=None, limit=0, krr_dir=krr) == []
    # --limit 2 → first two (curated defaults absent → alphabetical)
    chosen = select_law_slugs(explicit=None, limit=2, krr_dir=krr)
    assert len(chosen) == 2 and set(chosen) <= {"alfa_seadus", "beeta_seadus", "gamma_seadus"}
    # --all → every on-disk law slug, no curated/default sample truncation.
    assert select_law_slugs(explicit=None, limit=None, all_laws=True, krr_dir=krr) == [
        "alfa_seadus",
        "beeta_seadus",
        "gamma_seadus",
    ]
    # explicit --law wins, unknown slugs dropped
    assert select_law_slugs(explicit=["gamma_seadus", "puudub_seadus"], limit=None, krr_dir=krr) == ["gamma_seadus"]


def test_max_redactions_default_distinguishes_all_mode():
    assert gpv.effective_max_redactions(gpv._parse_args([])) == gpv.DEFAULT_MAX_REDACTIONS
    assert gpv.effective_max_redactions(gpv._parse_args(["--all"])) == 0
    assert (
        gpv.effective_max_redactions(gpv._parse_args(["--all", "--max-redactions", "12"]))
        == 12
    )


# ---------------------------------------------------------------------------
# Sidecar writing
# ---------------------------------------------------------------------------


def test_write_sidecar_shape_and_context(tmp_path: Path):
    target = _make_target(par_suffixes=("1", "2"))
    nodes = synthesise_versions(target, _three_redaction_chain())
    out_dir = tmp_path / "provision_versions"
    out_path = write_sidecar(target, nodes, out_dir=out_dir)
    assert out_path == out_dir / "fixture_law.jsonld"
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["@context"]["estleg"] == gpv.CONTEXT["estleg"]
    graph = doc["@graph"]
    # First node is the owl:Ontology map header; the rest are ProvisionVersions.
    assert graph[0]["@type"] == ["owl:Ontology"]
    assert graph[0]["@id"] == "estleg:ProvisionVersions_fixture_law_Map"
    assert graph[0]["dc:source"] == "Fikseeritud seadus"
    assert all("estleg:ProvisionVersion" in n["@type"] for n in graph[1:])
    # No undefined estleg:total* count predicate leaked onto the map node.
    assert not any(k.startswith("estleg:total") for k in graph[0])


# ---------------------------------------------------------------------------
# SHACL conformance
# ---------------------------------------------------------------------------


def test_shacl_accepts_well_formed_provision_versions():
    target = _make_target(par_suffixes=("1", "2"))
    nodes = synthesise_versions(target, _three_redaction_chain())
    graph_json = {"@context": dict(gpv.CONTEXT), "@graph": nodes}
    conforms, msg = _shacl_conforms(graph_json)
    assert conforms, msg


@pytest.mark.parametrize("drop_prop", ["estleg:versionOf", "estleg:versionValidFrom", "estleg:versionText"])
def test_shacl_rejects_provision_version_missing_required_property(drop_prop: str):
    node = {
        "@id": "estleg:FIX_Par_1_v111",
        "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
        "estleg:versionOf": {"@id": "estleg:FIX_Par_1"},
        "estleg:versionValidFrom": {"@value": "2010-01-01", "@type": "xsd:date"},
        "estleg:versionText": "Mingi sätte tekst.",
        "estleg:versionRedactionId": "111",
    }
    node.pop(drop_prop)
    graph_json = {"@context": dict(gpv.CONTEXT), "@graph": [node]}
    conforms, _ = _shacl_conforms(graph_json)
    assert not conforms


# ---------------------------------------------------------------------------
# End-to-end (mock fetches): process_law writes a sidecar + coverage report
# ---------------------------------------------------------------------------


def test_main_processes_law_with_mocked_fetches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write_peep(krr, "fixture_law", "FIX", ["1", "2"], title="Fikseeritud seadus")
    monkeypatch.setattr(gpv, "KRR_DIR", krr)
    monkeypatch.setattr(gpv, "VERSIONS_DIR", krr / "provision_versions")
    monkeypatch.setattr(gpv, "COVERAGE_PATH", krr / "reports" / "kov" / "extract_provision_versions_coverage.json")
    monkeypatch.setattr(gpv, "iter_peep_files", lambda *a, **k: list(krr.glob("*_peep.json")))

    chain = [_R1, _R2, _R3]
    texts_by_gid = {
        "111": _redaction_root([_paragraph_xml("1", title="A", body="Algtekst 1."),
                                _paragraph_xml("2", title="B", body="Algtekst 2.")]),
        "222": _redaction_root([_paragraph_xml("1", title="A", body="MUUDETUD 1 r2."),
                                _paragraph_xml("2", title="B", body="Algtekst 2.")]),
        "333": _redaction_root([_paragraph_xml("1", title="A", body="MUUDETUD 1 r2."),
                                _paragraph_xml("2", title="B", body="MUUDETUD 2 r3.")]),
    }
    monkeypatch.setattr(gpv, "fetch_redaction_chain", lambda *a, **k: chain)
    monkeypatch.setattr(gpv, "fetch_redaction_xml", lambda slug, redaction, **k: texts_by_gid[redaction.global_id])

    rc = gpv.main(["--law", "fixture_law", "--max-redactions", "0", "--no-sleep"])
    assert rc == 0

    sidecar = krr / "provision_versions" / "fixture_law.jsonld"
    assert sidecar.exists()
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    pv_nodes = [n for n in doc["@graph"] if "estleg:ProvisionVersion" in n.get("@type", [])]
    # § 1: v111, v222 ; § 2: v111, v333 → 4 ProvisionVersion nodes.
    assert {n["@id"] for n in pv_nodes} == {
        "estleg:FIX_Par_1_v111", "estleg:FIX_Par_1_v222",
        "estleg:FIX_Par_2_v111", "estleg:FIX_Par_2_v333",
    }
    # versionOf targets all resolve to provision IRIs declared in the peep.
    peep = json.loads((krr / "fixture_law_peep.json").read_text(encoding="utf-8"))
    peep_ids = {n["@id"] for n in peep["@graph"]}
    for n in pv_nodes:
        assert n["estleg:versionOf"]["@id"] in peep_ids

    cov = json.loads((krr / "reports" / "kov" / "extract_provision_versions_coverage.json").read_text("utf-8"))
    assert cov["pipeline"] == "extract_provision_versions"
    assert cov["files_processed"] == 1
    assert cov["files_with_output"] == 1
    assert cov["triples_emitted"] == 4  # ProvisionVersion node count
    law_report = json.loads(
        (krr / "reports" / "provision_versions_report.json").read_text("utf-8")
    )
    assert law_report["laws_processed"] == 1
    assert law_report["versions_emitted"] == 4
    assert law_report["rows"][0]["redactions_processed"] == 3
    assert law_report["rows"][0]["redactions_failed"] == 0
    assert law_report["rows"][0]["warnings"] == []


def test_main_limit_zero_writes_zeroed_coverage_and_no_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    monkeypatch.setattr(gpv, "KRR_DIR", krr)
    monkeypatch.setattr(gpv, "VERSIONS_DIR", krr / "provision_versions")
    monkeypatch.setattr(gpv, "COVERAGE_PATH", krr / "reports" / "kov" / "extract_provision_versions_coverage.json")
    monkeypatch.setattr(gpv, "iter_peep_files", lambda *a, **k: [])

    def _boom(*a, **k):  # noqa: ANN001
        raise AssertionError("--limit 0 must not hit the network")

    monkeypatch.setattr(gpv.requests, "get", _boom)
    rc = gpv.main(["--limit", "0"])
    assert rc == 0
    assert not (krr / "provision_versions").exists() or not list((krr / "provision_versions").glob("*.jsonld"))
    cov = json.loads((krr / "reports" / "kov" / "extract_provision_versions_coverage.json").read_text("utf-8"))
    assert cov["files_processed"] == 0 and cov["triples_emitted"] == 0
    law_report = json.loads(
        (krr / "reports" / "provision_versions_report.json").read_text("utf-8")
    )
    assert law_report["laws_processed"] == 0
    assert law_report["rows"] == []
