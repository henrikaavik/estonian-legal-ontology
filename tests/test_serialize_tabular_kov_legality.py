"""KOV legality table of the tabular export (#712)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from estleg import serialize_tabular as st
from estleg.derive_kov_enabling_staleness import build_version_layer

REPO = Path(__file__).resolve().parents[1]
EXPORTS = REPO / "krr_outputs" / "exports"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _stage(tmp_path: Path) -> Path:
    krr = tmp_path / "krr_outputs"
    vdir = krr / "provision_versions"
    vdir.mkdir(parents=True)
    versions = [
        {
            "@id": f"estleg:KOKS_Par_22_v{i}",
            "@type": ["estleg:ProvisionVersion"],
            "estleg:versionOf": {"@id": "estleg:KOKS_Par_22"},
            "estleg:versionValidFrom": {"@value": start},
            "estleg:versionText": text,
        }
        for i, (start, text) in enumerate((("2002-06-01", "A"), ("2015-01-01", "B")), 1)
    ]
    (vdir / "koks.jsonld").write_text(json.dumps({"@graph": versions}), encoding="utf-8")

    def act(reg: str, issuer: str, entry: str, targets: list[str], hist: str | None):
        root = {
            "@id": f"estleg:Reg_{reg}_Map",
            "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
            "dc:source": f"Määrus {reg}",
            "estleg:enactedBy": {"@id": f"estleg:Issuer_{issuer}"},
            "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0480"},
            "estleg:entryIntoForce": {"@value": entry, "@type": "xsd:date"},
            "estleg:municipalityStatus": "abolished" if hist else "current",
            "estleg:implementsCitation": [
                {"@id": f"estleg:Citation_Reg_{reg}_Map_{i}"} for i in range(len(targets))
            ],
        }
        if hist:
            root["estleg:enactedByHistoricalMunicipality"] = {"@id": hist}
        cits = [
            {"@id": f"estleg:Citation_Reg_{reg}_Map_{i}", "estleg:citationTarget": {"@id": t}}
            for i, t in enumerate(targets)
        ]
        return {"@graph": [root, *cits]}

    for slug, doc in (
        ("abja_vallavolikogu", act("1", "abja_vallavolikogu", "2010-01-01",
                                   ["estleg:KOKS_Par_22_Lg_1", "estleg:KOKS_Map"],
                                   "estleg:HistoricalMunicipality_0105")),
        ("mulgi_vallavolikogu", act("2", "mulgi_vallavolikogu", "2020-01-01",
                                    ["estleg:KOKS_Par_22_Lg_2"], None)),
        ("mulgi_vallavalitsus", act("3", "mulgi_vallavalitsus", "2020-01-01", [], None)),
    ):
        d = krr / "regulations" / "kov" / slug
        d.mkdir(parents=True)
        (d / f"reg_{slug}_peep.json").write_text(json.dumps(doc), encoding="utf-8")
    return krr


CONTEXT = st.KovContext(
    municipalities={"0480": {"name": "Mulgi vald", "county": "Viljandi maakond"}},
    county_codes={"Viljandi maakond": "0084"},
    historical_names={"estleg:HistoricalMunicipality_0105": "Abja vald"},
)


def test_kov_legality_rows_and_columns(tmp_path: Path) -> None:
    krr = _stage(tmp_path)
    out = tmp_path / "exports"
    n = st.serialize_kov_legality(
        krr_dir=krr,
        out_dir=out,
        kov_globs=["regulations/kov/*/*_peep.json"],
        context=CONTEXT,
    )
    rows = _read_csv(out / "kov_legality.csv")
    assert n == len(rows) == 4
    assert list(rows[0]) == list(st.KOV_LEGALITY_COLUMNS)
    by_key = {(r["act_iri"], r["enabling_provision"]): r for r in rows}

    old = by_key[("estleg:Reg_1_Map", "estleg:KOKS_Par_22_Lg_1")]
    assert old["municipality_ehak"] == "0480"
    assert old["municipality"] == "Mulgi vald"
    assert old["county_code"] == "0084"
    assert old["county"] == "Viljandi maakond"
    assert old["issuer"] == "estleg:Issuer_abja_vallavolikogu"
    assert old["historical_municipality"] == "estleg:HistoricalMunicipality_0105"
    assert old["historical_municipality_name"] == "Abja vald"
    assert old["citation_status"] == "resolved"
    assert old["version_provision"] == "estleg:KOKS_Par_22"
    assert old["version_in_force"] == "estleg:KOKS_Par_22_v1"
    assert old["provision_outdated"] == "true"
    assert old["provision_superseding_date"] == "2015-01-01"
    assert old["act_outdated"] == "true"
    assert old["act_earliest_superseding_date"] == "2015-01-01"

    fallback = by_key[("estleg:Reg_1_Map", "estleg:KOKS_Map")]
    assert fallback["citation_status"] == "act_level"
    assert fallback["provision_outdated"] == ""
    assert fallback["act_outdated"] == "true"  # act-level flag still from the § citation

    new = by_key[("estleg:Reg_2_Map", "estleg:KOKS_Par_22_Lg_2")]
    assert new["version_in_force"] == "estleg:KOKS_Par_22_v2"
    assert new["provision_outdated"] == "false"
    assert new["act_outdated"] == "false"
    assert new["historical_municipality"] == ""

    bare = by_key[("estleg:Reg_3_Map", "")]
    assert bare["citation_status"] == "no_citation"
    assert bare["act_outdated"] == ""


def test_kov_legality_municipality_filter(tmp_path: Path) -> None:
    krr = _stage(tmp_path)
    out = tmp_path / "exports"
    versions = build_version_layer(krr / "provision_versions")
    kwargs = dict(krr_dir=krr, out_dir=out, kov_globs=["regulations/kov/*/*_peep.json"],
                  versions=versions, context=CONTEXT)
    assert st.serialize_kov_legality(municipalities=["0784"], **kwargs) == 0
    assert list(_read_csv(out / "kov_legality.csv")) == []
    assert st.serialize_kov_legality(municipalities=["0480"], **kwargs) == 4


def test_cli_flag_writes_only_kov_table(tmp_path: Path, monkeypatch) -> None:
    krr = _stage(tmp_path)
    out = tmp_path / "exports"
    monkeypatch.setattr(st, "load_kov_context", lambda: CONTEXT)
    rc = st.main([
        "--kov-legality", "--krr-dir", str(krr), "--out", str(out),
        "--kov-glob", "regulations/kov/*/*_peep.json", "--kov-municipality", "0480",
    ])
    assert rc == 0
    assert sorted(p.name for p in out.iterdir()) == ["kov_legality.csv"]


def test_committed_kov_legality_sample() -> None:
    rows = _read_csv(EXPORTS / "kov_legality.csv")
    assert rows, "missing committed sample krr_outputs/exports/kov_legality.csv"
    assert list(rows[0]) == list(st.KOV_LEGALITY_COLUMNS)
    assert {r["municipality_ehak"] for r in rows} == {"0480"}
    assert all(r["county_code"] == "0084" for r in rows)
    assert any(r["historical_municipality_name"] == "Abja vald" for r in rows)
    assert any(r["act_outdated"] == "true" for r in rows)
    assert any(r["act_outdated"] == "false" for r in rows)
    assert (EXPORTS / "kov_legality.csv").stat().st_size < 1_000_000


def test_default_kov_sample_reproduces_committed_file(tmp_path: Path) -> None:
    out = tmp_path / "exports"
    st.serialize_kov_legality(out_dir=out)
    assert (out / "kov_legality.csv").read_text(encoding="utf-8") == (
        EXPORTS / "kov_legality.csv"
    ).read_text(encoding="utf-8")
