"""#716: real CSV exports, the deterministic sample and the Compose default.

* writers on a tmp mini-corpus built from real peeps (``committed`` tier);
* the sanction EUR normalisation (``unit``);
* sample determinism: two runs byte-identical, draws order-independent;
* the Compose file's structure and its dump entrypoint script;
* the committed ``krr_outputs/exports`` CSVs equal a fresh ``--sample`` run
  (``corpus`` tier).
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from estleg import serialize_tabular as st
from estleg.estleg_common import ONTOLOGY_VERSION

REPO = Path(__file__).resolve().parents[1]
EXPORTS = REPO / "krr_outputs" / "exports"
COMPOSE = REPO / "docker-compose.yml"
FETCH_SCRIPT = REPO / "scripts" / "compose_fetch_dump.sh"
API_GUIDE = REPO / "docs" / "API_GUIDE.md"

LAW = "abipolitseiniku_seadus"
KOV_FILE = (
    "regulations/kov/abja_vallavolikogu/"
    "abja_noortekeskuse_asutamine_ja_pohimaaruse_kinnitamine_t1003313_peep.json"
)
STATE_FILE = "regulations/riik/liiga_laialdase_kasutusega_perekonnanimede_loetelu_t1047825_peep.json"
INSTITUTIONS = (
    "institutions/institution_politsei_ja_piirivalveamet.json",
    "institutions/institution_haridusministeerium.json",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _slice(doc: dict, keep) -> dict:
    return {"@context": doc.get("@context", {}), "@graph": keep(doc["@graph"])}


def _first_with(nodes: list[dict], n: int, *, linked: str | None = None) -> list[dict]:
    typed = [node for node in nodes if "owl:Ontology" not in node.get("@type", [])]
    plain = [node for node in typed if not linked or linked not in node][:n]
    extra = [node for node in typed if linked and linked in node][:n]
    return plain + extra


@pytest.fixture
def mini_corpus(tmp_path: Path, corpus_krr) -> Path:
    """Copy / slice 1–2 real peeps per corpus into ``tmp_path/krr_outputs``."""
    krr = tmp_path / "krr_outputs"

    def copy(rel: str) -> None:
        dest = krr / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(corpus_krr.path(rel), dest)

    def write(rel: str, doc: object) -> None:
        dest = krr / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    for rel in (
        f"{LAW}_peep.json",
        f"sanctions/sanctions_{LAW}.json",
        f"{st.VERSION_DIRNAME}/{LAW}.jsonld",
        KOV_FILE,
        STATE_FILE,
        *INSTITUTIONS,
    ):
        copy(rel)
    for rel in (
        "regulations/riik/REGULATIONS_RIIK_INDEX.json",
        "regulations/kov/REGULATIONS_KOV_INDEX.json",
    ):
        write(rel, {"kehtiv": corpus_krr.read_json(rel)["kehtiv"]})
    rk = corpus_krr.read_json("riigikohus/riigikohus_2020_peep.json")
    write("riigikohus/riigikohus_2020_peep.json", _slice(rk, lambda g: _first_with(g, 3)))
    drafts = corpus_krr.read_json("eelnoud/eelnoud_review_peep.json")
    write(
        "eelnoud/eelnoud_review_peep.json",
        _slice(drafts, lambda g: _first_with(g, 4, linked="estleg:amendsLaw")),
    )
    eu = corpus_krr.read_json("eurlex/eurlex_directives_peep.json")
    write(
        "eurlex/eurlex_directives_peep.json",
        _slice(eu, lambda g: _first_with(g, 4, linked="estleg:transposedBy")),
    )
    return krr


def _mini_selection(**overrides) -> st.Selection:
    base = st.Selection(
        laws_globs=(f"{LAW}_peep.json",),
        sanctions_globs=(f"sanctions/sanctions_{LAW}.json",),
        court_globs=("riigikohus/riigikohus_*_peep.json",),
        regulation_globs=("regulations/riik/*_peep.json", "regulations/kov/*/*_peep.json"),
        state_issuers=None,
        draft_limit=None,
        eu_act_limit=None,
        restrict_competences=False,
    )
    return st.dataclasses.replace(base, **overrides)


def test_writers_on_real_mini_corpus(mini_corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    counts = st.serialize(
        krr_dir=mini_corpus, out_dir=out, selection=_mini_selection(), write_parquet=False
    )
    assert set(counts) == set(st.TABLE_COLUMNS)
    for name, columns in st.TABLE_COLUMNS.items():
        with (out / f"{name}.csv").open(encoding="utf-8") as handle:
            assert handle.readline().rstrip("\n").split(",") == list(columns), name
        assert counts[name] > 0, name

    provisions = {row["iri"]: row for row in _read_csv(out / "provisions.csv")}
    abipol = [row for iri, row in provisions.items() if iri.startswith("estleg:ABIPOL_")]
    assert abipol and all(row["temporalStatus"] and row["valid_from"] for row in abipol)
    # the version layer fills the current redaction (and its start date)
    par1 = provisions["estleg:ABIPOL_Par_1"]
    assert par1["current_version"].startswith("estleg:ABIPOL_Par_1_v")
    lg = next(row for iri, row in provisions.items() if iri.startswith("estleg:ABIPOL_Par_1_Lg_"))
    assert lg["current_version"] == par1["current_version"]  # lõige rolls up to its §

    sanctions = _read_csv(out / "sanctions.csv")
    fine = next(row for row in sanctions if row["unit"] == "fine_units" and row["max"])
    assert Decimal(fine["amount_eur_max"]) == Decimal(fine["max"]) * 4
    assert all(row["act"] == "estleg:ABIPOL_Map" for row in sanctions)
    assert all(row["subject"] and row["subject_source"] for row in sanctions)
    assert {row["subject_source"] for row in sanctions} <= {
        "sanctionSubject", "dutyHolder", "targetGroup", "act"
    }
    custodial = [row for row in sanctions if row["unit"] in {"days", "years", "daily_rates"}]
    assert custodial and all(not row["amount_eur_max"] for row in custodial)

    regulations = {row["level"]: row for row in _read_csv(out / "regulations.csv")}
    kov, state = regulations["kov"], regulations["state"]
    root = json.loads((mini_corpus / KOV_FILE).read_text(encoding="utf-8"))["@graph"]
    paragraphs = [
        node for node in root
        if st.is_provision_node(node) and not st.is_subsection_node(node)
    ]
    assert kov["provision_count"] == str(len(paragraphs))
    assert kov["municipality_ehak"] == "0480" and kov["municipality"] == "Mulgi vald"
    assert kov["county_code"] == "0084" and kov["county"] == "Viljandi maakond"
    assert kov["historical_municipality"].startswith("estleg:HistoricalMunicipality_")
    assert kov["enabling_provisions"] and kov["enabling_provision_outdated"] in {"true", "false"}
    assert kov["enabling_provision_count"] == str(len(kov["enabling_provisions"].split(";")))
    assert kov["kehtiv"] and state["kehtiv"]
    assert state["issuer"] == "Rahvastikuminister" and not state["municipality_ehak"]

    drafts = _read_csv(out / "drafts.csv")
    assert any(row["amends_law"] for row in drafts)
    # #717: the phase is derived from the latest lifecycle step, so a draft
    # first seen in the review feed may now be Enacted, Withdrawn, etc.
    assert all(row["phase"] and row["eis_link"].startswith("https://") for row in drafts)
    eu_acts = _read_csv(out / "eu_acts.csv")
    assert any(int(row["transposed_by_count"]) > 0 for row in eu_acts)
    assert all(row["doc_type"] == "Directive" and row["celex"] for row in eu_acts)

    institutions = {row["iri"]: row for row in _read_csv(out / "institutions.csv")}
    ppa = institutions["estleg:Institution_politsei_ja_piirivalveamet"]
    assert ppa["registrikood"] and int(ppa["competence_count"]) > 0
    assert institutions["estleg:Institution_haridusministeerium"]["successor"]
    competences = _read_csv(out / "competences.csv")
    assert {row["institution"] for row in competences} == {
        "estleg:Institution_politsei_ja_piirivalveamet"
    }
    assert all(row["competence_type"] and row["provision"] for row in competences)


def test_restrict_competences_and_sampling(mini_corpus: Path) -> None:
    tables = st.build_tables(
        mini_corpus,
        _mini_selection(draft_limit=4, eu_act_limit=4, restrict_competences=True),
    )
    known = {row["iri"] for row in tables["provisions"]}
    assert tables["competences"]
    assert all(row["provision"] in known for row in tables["competences"])
    assert len(tables["drafts"]) == 4 and len(tables["eu_acts"]) == 4
    # half the quota goes to linked rows
    assert sum(1 for row in tables["drafts"] if row["amends_law"]) == 2
    assert sum(1 for row in tables["eu_acts"] if row["transposed_by_count"] != "0") == 2


def test_two_runs_are_byte_identical(mini_corpus: Path, tmp_path: Path) -> None:
    selection = _mini_selection(draft_limit=3, eu_act_limit=3)
    for run in ("a", "b"):
        st.serialize(
            krr_dir=mini_corpus, out_dir=tmp_path / run, selection=selection, write_parquet=False
        )
    for name in st.TABLE_COLUMNS:
        assert (tmp_path / "a" / f"{name}.csv").read_bytes() == (
            tmp_path / "b" / f"{name}.csv"
        ).read_bytes(), name


def test_sample_draw_is_order_independent() -> None:
    rows = [{"iri": f"estleg:Draft_{i:03d}", "amends_law": "x" if i % 7 == 0 else ""}
            for i in range(60)]
    forward = st.sample_rows(rows, 10, linked=st._draft_linked)
    backward = st.sample_rows(list(reversed(rows)), 10, linked=st._draft_linked)
    assert forward == backward
    assert [row["iri"] for row in forward] == sorted(row["iri"] for row in forward)
    assert sum(1 for row in forward if row["amends_law"]) == 5
    assert st.sample_rows(rows, None, linked=st._draft_linked) == rows


@pytest.mark.parametrize(
    ("unit", "currency", "amount", "expected"),
    [
        ("monetary", "EUR", "32000", "32000"),
        ("monetary", "", "1200.50", "1200.5"),
        ("monetary", "EEK", "50000", "3195.58"),
        ("fine_units", "", "300", "1200"),
        ("days", "", "30", ""),
        ("years", "", "5", ""),
        ("daily_rates", "", "500", ""),
        ("percent_of_turnover", "", "4", ""),
    ],
)
def test_eur_normalisation(unit: str, currency: str, amount: str, expected: str) -> None:
    node = {
        "@id": "estleg:Sanction_X",
        "@type": ["estleg:Sanction"],
        "estleg:sanctionType": "fine",
        "estleg:maxPenaltyAmount": {"@value": amount, "@type": "xsd:decimal"},
        "estleg:maxPenaltyUnit": unit,
    }
    if currency:
        node["estleg:maxPenaltyCurrency"] = currency
    row = st.sanction_row(node)
    assert row["amount_eur_max"] == expected
    assert row["amount_eur_min"] == ""
    assert st.to_eur(None, unit, currency) is None


def test_min_amount_and_person_type() -> None:
    row = st.sanction_row(
        {
            "@id": "estleg:Sanction_Y",
            "@type": ["estleg:Sanction"],
            "estleg:sanctionType": "fine",
            "estleg:minPenaltyAmount": {"@value": "10"},
            "estleg:maxPenaltyAmount": {"@value": "400000"},
            "estleg:minPenaltyUnit": "monetary",
            "estleg:maxPenaltyUnit": "monetary",
            "estleg:maxPenaltyCurrency": "EUR",
        }
    )
    assert (row["amount_eur_min"], row["amount_eur_max"]) == ("10", "400000")
    assert row["subject_person_type"] == "legal_person"
    arrest = st.sanction_row(
        {"@id": "estleg:S", "estleg:sanctionType": "arrest", "estleg:maxPenaltyUnit": "days"}
    )
    assert arrest["subject_person_type"] == "natural_person"


def test_sanction_subject_precedence() -> None:
    provisions = [
        {"iri": "estleg:A_Par_1", "act": "estleg:A_Map", "_target_group": "estleg:TG_1",
         "_duty_holder": "", "_parent": ""},
        {"iri": "estleg:A_Par_1_Lg_1", "act": "estleg:A_Map", "_target_group": "",
         "_duty_holder": "", "_parent": "estleg:A_Par_1"},
        {"iri": "estleg:A_Par_2", "act": "estleg:A_Map", "_target_group": "",
         "_duty_holder": "", "_parent": ""},
        {"iri": "estleg:A_Par_2_Lg_1", "act": "estleg:A_Map", "_target_group": "",
         "_duty_holder": "estleg:TG_2", "_parent": "estleg:A_Par_2"},
        {"iri": "estleg:A_Par_3", "act": "estleg:A_Map", "_target_group": "",
         "_duty_holder": "", "_parent": ""},
    ]
    sanctions = [
        {"iri": f"estleg:S{i}", "provision": provision, "subject": "", "act": ""}
        for i, provision in enumerate(
            ("estleg:A_Par_1", "estleg:A_Par_1_Lg_1", "estleg:A_Par_2", "estleg:A_Par_3")
        )
    ]
    st.apply_sanction_subjects(sanctions, provisions)
    assert [(row["subject"], row["subject_source"]) for row in sanctions] == [
        ("estleg:TG_1", "targetGroup"),   # own
        ("estleg:TG_1", "targetGroup"),   # parent §
        ("estleg:TG_2", "dutyHolder"),    # its lõiked
        ("estleg:A_Map", "act"),          # fallback
    ]
    assert {row["act"] for row in sanctions} == {"estleg:A_Map"}


def test_full_refuses_the_committed_directory(capsys) -> None:
    assert st.main(["--full"]) == 2
    assert st.main(["--full", "--out", str(EXPORTS)]) == 2
    assert "never committed" in capsys.readouterr().err


# ── Compose quickstart ───────────────────────────────────────────────────────


def _load_compose() -> dict:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        yaml = None
    if yaml is not None:
        return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    ruby = shutil.which("ruby")
    if ruby:
        script = (
            "d = YAML.respond_to?(:unsafe_load_file) ? "
            "YAML.load_file(ARGV[0], aliases: true) : YAML.load_file(ARGV[0]); "
            "puts JSON.dump(d)"
        )
        done = subprocess.run(
            [ruby, "-ryaml", "-rjson", "-e", script, str(COMPOSE)],
            capture_output=True, text=True, check=True,
        )
        return json.loads(done.stdout)
    pytest.skip("no YAML parser (PyYAML or ruby) available")


def test_compose_default_loads_the_real_dump() -> None:
    services = _load_compose()["services"]
    default = {name for name, svc in services.items() if not svc.get("profiles")}
    sample = {name for name, svc in services.items() if svc.get("profiles") == ["sample"]}
    assert default == {"dump", "load", "sparql"}
    assert sample == {"sample-dump", "sample-load", "sample-sparql"}

    dump = services["dump"]
    assert dump["entrypoint"][-1] == "/usr/local/bin/compose_fetch_dump.sh"
    mounts = " ".join(dump["volumes"])
    assert "./scripts/compose_fetch_dump.sh:/usr/local/bin/compose_fetch_dump.sh:ro" in mounts
    assert "./release:/release:ro" in mounts
    assert dump["environment"]["ESTLEG_VERSION"] == f"${{ESTLEG_VERSION:-{ONTOLOGY_VERSION}}}"
    assert dump["environment"]["ESTLEG_DUMP_OVERRIDE"] == "${ESTLEG_DUMP:+1}"
    # the fixture is only reachable through the sample profile or ESTLEG_DUMP
    sample_mounts = " ".join(services["sample-dump"]["volumes"])
    assert "estleg_all_sample.nq.gz" in sample_mounts
    assert services["load"]["depends_on"]["dump"]["condition"] == "service_completed_successfully"
    assert services["sparql"]["ports"] == ["7878:7878"]
    assert services["sample-sparql"]["ports"] == ["7878:7878"]
    for name in ("load", "sparql", "sample-load", "sample-sparql"):
        assert services[name]["image"].startswith("oxigraph/oxigraph:")


def test_fetch_script_is_executable_and_verifies_checksums() -> None:
    assert FETCH_SCRIPT.is_file()
    assert os.access(FETCH_SCRIPT, os.X_OK)
    text = FETCH_SCRIPT.read_text(encoding="utf-8")
    assert "SHA256SUMS" in text and "sha256sum" in text
    assert "releases/download/v$VERSION" in text
    assert "henrikaavik/estonian-legal-ontology" in text
    assert "estleg_all.nq.gz" in text
    sh = shutil.which("sh")
    if sh:
        subprocess.run([sh, "-n", str(FETCH_SCRIPT)], check=True)


def test_fetch_script_offline_paths(tmp_path: Path) -> None:
    """Local release dump verified; tampered dump rejected; override honoured."""
    sh = shutil.which("sh")
    if sh is None or shutil.which("gzip") is None:
        pytest.skip("needs sh and gzip")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if shutil.which("sha256sum") is None:
        shim = bindir / "sha256sum"
        shim.write_text('#!/bin/sh\nshasum -a 256 "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    import gzip
    import hashlib

    release = tmp_path / "release"
    release.mkdir()
    payload = gzip.compress(b"<urn:s> <urn:p> <urn:o> <urn:g> .\n")
    (release / "estleg_all.nq.gz").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  estleg_all.nq.gz\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "OUT_DIR": str(out),
        "RELEASE_DIR": str(release),
        "ESTLEG_VERSION": ONTOLOGY_VERSION,
    }
    done = subprocess.run([sh, str(FETCH_SCRIPT)], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "sha256 ok" in done.stdout
    assert (out / "dump.nq").read_text(encoding="utf-8").count("\n") == 1

    (release / "SHA256SUMS").write_text(f"{'0' * 64}  estleg_all.nq.gz\n", encoding="utf-8")
    bad = subprocess.run([sh, str(FETCH_SCRIPT)], env=env, capture_output=True, text=True)
    assert bad.returncode != 0 and "SHA256 mismatch" in bad.stderr

    override = tmp_path / "override.nq.gz"
    override.write_bytes(gzip.compress(b"a\nb\n"))
    env.update({"ESTLEG_DUMP_OVERRIDE": "1", "OVERRIDE_FILE": str(override)})
    ok = subprocess.run([sh, str(FETCH_SCRIPT)], env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert (out / "dump.nq").read_text(encoding="utf-8") == "a\nb\n"


def test_api_guide_documents_every_column_and_the_three_dump_paths() -> None:
    guide = API_GUIDE.read_text(encoding="utf-8")
    csv_part = guide.split("## CSV Exports", 1)[1].split("## SPARQL Queries", 1)[0]
    for name, columns in st.TABLE_COLUMNS.items():
        assert f"`{name}.csv`" in csv_part, name
        for column in columns:
            assert f"`{column}`" in csv_part, (name, column)
    for needle in (
        "ESTLEG_DUMP=",
        "release/estleg_all.nq.gz",
        "releases/download/v<version>/estleg_all.nq.gz",
        "--profile sample",
        "SHA256SUMS",
    ):
        assert needle in guide, needle


# ── committed sample ─────────────────────────────────────────────────────────


@pytest.mark.corpus
def test_committed_sample_equals_a_fresh_sample_run(tmp_path: Path, corpus_krr) -> None:
    for rel in (*st.DEFAULT_LAWS_GLOBS, *st.DEFAULT_COURT_GLOBS, st.VERSION_DIRNAME):
        corpus_krr.path(rel)
    st.serialize(krr_dir=corpus_krr.root, out_dir=tmp_path, write_parquet=False)
    for name in st.TABLE_COLUMNS:
        committed = EXPORTS / f"{name}.csv"
        assert committed.read_bytes() == (tmp_path / f"{name}.csv").read_bytes(), (
            f"{committed} is stale; run `python3 scripts/serialize_tabular.py --sample`"
        )


def test_committed_sample_stays_small() -> None:
    total = sum((EXPORTS / f"{name}.csv").stat().st_size for name in st.TABLE_COLUMNS)
    assert total < 2_000_000
    counts = {name: len(_read_csv(EXPORTS / f"{name}.csv")) for name in st.TABLE_COLUMNS}
    assert counts["laws"] == len(st.SAMPLE_LAW_SLUGS)
    assert counts["drafts"] == st.SAMPLE_DRAFTS and counts["eu_acts"] == st.SAMPLE_EU_ACTS
    assert all(counts.values()), counts
    years = {row["date"][:4] for row in _read_csv(EXPORTS / "court_decisions.csv")}
    assert years == {str(year) for year in st.SAMPLE_COURT_YEARS}
    levels = {row["level"] for row in _read_csv(EXPORTS / "regulations.csv")}
    assert levels == {"state", "kov"}
