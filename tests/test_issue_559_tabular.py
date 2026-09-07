"""#559: star-schema CSV/Parquet export from committed peeps.

Calls the shipped ``write_tables`` / row builders — this file does not
reimplement the flattener. The committed sample under
``krr_outputs/exports/`` is a small real subset, not the 23k-file corpus.
"""

from __future__ import annotations

import csv
from pathlib import Path

from estleg import serialize_tabular as st

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
EXPORTS = REPO / "krr_outputs" / "exports"
SCRIPT = REPO / "src" / "estleg" / "serialize_tabular.py"

FIXTURE_GRAPH = {
    "@context": {
        "estleg": "https://w3id.org/estleg/",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "dcterms": "http://purl.org/dc/terms/",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
    },
    "@graph": [
        {
            "@id": "estleg:FOO_Map",
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
            "dcterms:title": "Foo seadus",
            "estleg:temporalStatus": "inForce",
            "estleg:kehtiv": {"@value": "2026-05-24", "@type": "xsd:date"},
            "estleg:references": [{"@id": "estleg:BAR_Par_1"}],
        },
        {
            "@id": "estleg:FOO_Par_1",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision_foo"],
            "estleg:paragrahv": "§ 1.",
            "estleg:partOfAct": {"@id": "estleg:FOO_Map"},
            "estleg:legalText": "Hello world " + ("x" * 2100),
            "estleg:inForce": True,
            "estleg:references": [{"@id": "estleg:FOO_Par_2"}],
        },
        {
            "@id": "estleg:FOO_Par_1_Lg_1",
            "@type": ["estleg:Subsection", "owl:NamedIndividual"],
            "estleg:subsectionNumber": "1",
            "estleg:parentProvision": {"@id": "estleg:FOO_Par_1"},
            "estleg:legalText": "(1) Hello",
        },
        {
            "@id": "estleg:Sanction_FOO_Par_1_fine",
            "@type": ["owl:NamedIndividual", "estleg:Sanction"],
            "estleg:sanctionType": "fine",
            "estleg:applicableProvision": {"@id": "estleg:FOO_Par_1"},
            "estleg:maxPenaltyAmount": {"@value": "300", "@type": "xsd:decimal"},
            "estleg:maxPenaltyUnit": "fine_units",
        },
        {
            "@id": "estleg:RK_1_20_1",
            "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
            "estleg:caseNumber": "1-20-1",
            "estleg:decisionDate": {"@value": "2020-01-02", "@type": "xsd:date"},
            "estleg:ecliIdentifier": "ECLI:EE:RK:2020:1.20.1",
            "estleg:chamber": "Tsiviilkolleegium",
        },
    ],
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_row_builders_on_fixture_graph() -> None:
    law = FIXTURE_GRAPH["@graph"][0]
    provision = FIXTURE_GRAPH["@graph"][1]
    subsection = FIXTURE_GRAPH["@graph"][2]
    sanction = FIXTURE_GRAPH["@graph"][3]
    decision = FIXTURE_GRAPH["@graph"][4]

    law_row = st.law_row(law, slug="foo_seadus", abbreviation="FOO")
    assert law_row["iri"] == "estleg:FOO_Map"
    assert law_row["slug"] == "foo_seadus"
    assert law_row["title"] == "Foo seadus"
    assert law_row["abbreviation"] == "FOO"
    assert law_row["temporalStatus"] == "inForce"
    assert law_row["kehtiv"] == "2026-05-24"

    provision_row = st.provision_row(provision)
    assert provision_row["iri"] == "estleg:FOO_Par_1"
    assert provision_row["act"] == "estleg:FOO_Map"
    assert provision_row["paragrahv"] == "§ 1."
    assert provision_row["subsection"] == ""
    assert len(provision_row["legalText"]) == st.LEGAL_TEXT_MAX
    assert provision_row["legalText"].startswith("Hello world ")

    subsection_row = st.provision_row(subsection)
    assert subsection_row["subsection"] == "1"
    assert subsection_row["legalText"] == "(1) Hello"

    cites = st.citation_rows(law)
    assert cites == [
        {
            "source": "estleg:FOO_Map",
            "target": "estleg:BAR_Par_1",
            "predicate": "estleg:references",
        }
    ]

    sanction_row = st.sanction_row(sanction)
    assert sanction_row["provision"] == "estleg:FOO_Par_1"
    assert sanction_row["type"] == "fine"
    assert sanction_row["max"] == "300"
    assert sanction_row["unit"] == "fine_units"

    court_row = st.court_decision_row(decision)
    assert court_row["caseNumber"] == "1-20-1"
    assert court_row["date"] == "2020-01-02"
    assert court_row["ecli"] == "ECLI:EE:RK:2020:1.20.1"
    assert court_row["chamber"] == "Tsiviilkolleegium"


def test_write_tables_on_fixture_graph(tmp_path: Path) -> None:
    counts = st.write_tables(
        tmp_path,
        graph=FIXTURE_GRAPH,
        slug="foo_seadus",
        abbreviation="FOO",
        write_parquet=False,
    )
    assert counts["laws"] == 1
    assert counts["provisions"] == 2
    assert counts["citations"] >= 1
    assert counts["sanctions"] == 1
    assert counts["court_decisions"] == 1

    laws = _read_csv(tmp_path / "laws.csv")
    assert list(laws[0]) == list(st.LAW_COLUMNS)
    assert laws[0]["abbreviation"] == "FOO"
    assert laws[0]["title"] == "Foo seadus"

    provisions = _read_csv(tmp_path / "provisions.csv")
    assert list(provisions[0]) == list(st.PROVISION_COLUMNS)
    by_iri = {row["iri"]: row for row in provisions}
    assert by_iri["estleg:FOO_Par_1"]["in_force"] == "true"
    assert by_iri["estleg:FOO_Par_1_Lg_1"]["act"] == "estleg:FOO_Map"
    assert by_iri["estleg:FOO_Par_1_Lg_1"]["paragrahv"] == "§ 1."
    assert len(by_iri["estleg:FOO_Par_1"]["legalText"]) == st.LEGAL_TEXT_MAX

    citations = _read_csv(tmp_path / "citations.csv")
    assert list(citations[0]) == list(st.CITATION_COLUMNS)
    predicates = {row["predicate"] for row in citations}
    assert "estleg:references" in predicates

    sanctions = _read_csv(tmp_path / "sanctions.csv")
    assert list(sanctions[0]) == list(st.SANCTION_COLUMNS)
    assert sanctions[0]["iri"] == "estleg:Sanction_FOO_Par_1_fine"

    courts = _read_csv(tmp_path / "court_decisions.csv")
    assert list(courts[0]) == list(st.COURT_COLUMNS)
    assert courts[0]["ecli"] == "ECLI:EE:RK:2020:1.20.1"


def test_write_tables_accepts_projected_rows(tmp_path: Path) -> None:
    tables = st.project_graph(
        FIXTURE_GRAPH, slug="foo_seadus", abbreviation="FOO"
    )
    counts = st.write_tables(tmp_path, tables, write_parquet=False)
    assert counts["laws"] == 1
    assert (tmp_path / "laws.csv").is_file()


def test_parquet_skipped_note_without_pyarrow(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    st.write_tables(tmp_path, graph=FIXTURE_GRAPH, write_parquet=None)
    err = capsys.readouterr().err
    assert "parquet skipped: pyarrow is not installed" in err
    assert not list(tmp_path.glob("*.parquet"))
    assert (tmp_path / "laws.csv").is_file()


def test_default_inputs_skip_combined_ontology() -> None:
    paths = st.resolve_input_paths(REPO / "krr_outputs")
    names = {path.name for path in paths}
    assert "combined_ontology.jsonld" not in names
    assert "abipolitseiniku_seadus_peep.json" in names
    assert "sanctions_abipolitseiniku_seadus.json" in names
    assert "riigikohus_2020_peep.json" in names
    assert "combined_ontology.jsonld" in st.SKIP_FILENAMES


def test_serialize_on_staged_subset(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    (krr / "sanctions").mkdir(parents=True)
    (krr / "riigikohus").mkdir()
    (krr / "abipolitseiniku_seadus_peep.json").write_text(
        '{"@graph":[{"@id":"estleg:FOO_Map",'
        '"@type":["estleg:Law"],"dcterms:title":"Foo",'
        '"estleg:references":{"@id":"estleg:BAR_Par_1"}}]}',
        encoding="utf-8",
    )
    (krr / "combined_ontology.jsonld").write_text(
        '{"@graph":[{"@id":"estleg:SHOULD_NOT_LOAD","@type":["estleg:Law"]}]}',
        encoding="utf-8",
    )
    (krr / "sanctions" / "sanctions_abipolitseiniku_seadus.json").write_text(
        '{"@graph":[{"@id":"estleg:Sanction_x","@type":["estleg:Sanction"],'
        '"estleg:sanctionType":"fine",'
        '"estleg:applicableProvision":{"@id":"estleg:FOO_Par_1"}}]}',
        encoding="utf-8",
    )
    (krr / "riigikohus" / "riigikohus_2020_peep.json").write_text(
        '{"@graph":[{"@id":"estleg:RK_1","@type":["estleg:CourtDecision"],'
        '"estleg:caseNumber":"1-20-1"}]}',
        encoding="utf-8",
    )
    out = tmp_path / "exports"
    counts = st.serialize(
        krr_dir=krr,
        out_dir=out,
        write_parquet=False,
        abbrev_registry={"abipolitseiniku_seadus": "FOO"},
    )
    assert counts["laws"] == 1
    assert counts["sanctions"] == 1
    assert counts["court_decisions"] == 1
    laws = _read_csv(out / "laws.csv")
    assert laws[0]["iri"] == "estleg:FOO_Map"
    assert laws[0]["abbreviation"] == "FOO"
    assert all(row["iri"] != "estleg:SHOULD_NOT_LOAD" for row in laws)


def test_committed_sample_csv_headers_and_rows() -> None:
    laws = _read_csv(EXPORTS / "laws.csv")
    provisions = _read_csv(EXPORTS / "provisions.csv")
    citations = _read_csv(EXPORTS / "citations.csv")
    sanctions = _read_csv(EXPORTS / "sanctions.csv")
    courts = _read_csv(EXPORTS / "court_decisions.csv")

    assert laws, f"missing law rows in {EXPORTS / 'laws.csv'}"
    assert list(laws[0]) == list(st.LAW_COLUMNS)
    assert any(
        row["slug"] == "abipolitseiniku_seadus" or row["abbreviation"] == "ABIPOL"
        for row in laws
    )

    assert provisions, f"missing provision rows in {EXPORTS / 'provisions.csv'}"
    assert list(provisions[0]) == list(st.PROVISION_COLUMNS)
    assert any("ABIPOL_Par_" in row["iri"] for row in provisions)
    assert all(len(row.get("legalText") or "") <= st.LEGAL_TEXT_MAX for row in provisions)

    assert citations, f"missing citation rows in {EXPORTS / 'citations.csv'}"
    assert list(citations[0]) == list(st.CITATION_COLUMNS)
    assert any(
        row["predicate"] in {"estleg:references", "estleg:referencedBy"}
        for row in citations
    )

    assert sanctions
    assert list(sanctions[0]) == list(st.SANCTION_COLUMNS)
    assert any("ABIPOL" in row["iri"] or "ABIPOL" in row["provision"] for row in sanctions)

    assert courts
    assert list(courts[0]) == list(st.COURT_COLUMNS)
    assert any(row["ecli"].startswith("ECLI:EE:RK:2020") for row in courts)

    for name in st.TABLE_COLUMNS:
        path = EXPORTS / f"{name}.csv"
        assert path.stat().st_size < 1_000_000, f"{path} must stay under 1 MB"


def test_readme_documents_tabular_export() -> None:
    text = README.read_text(encoding="utf-8")
    assert "### 5-minute start" in text
    after_qs = text.split("## Quick Start", 1)[1]
    five_min, _, rest = after_qs.partition("### Load surfaces")
    assert "5-minute start" in five_min
    assert "python3 examples/quickstart.py" in five_min
    assert "### Tabular export" in rest
    assert "serialize_tabular.py" in rest
    assert "krr_outputs/exports" in rest
    assert "--laws-glob" in rest
    script = SCRIPT.read_text(encoding="utf-8")
    assert "never" in script and "combined_ontology.jsonld" in script
