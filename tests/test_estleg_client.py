"""#716: estleg_client across all corpora, exact typing, rows, download, CLI.

Hermetic tests build their inputs in ``tmp_path``. Tests that copy 1-2 real
peeps into a mini-corpus read them through ``corpus_krr`` (``committed``
tier). The download helper is exercised against a monkeypatched ``urlopen``
serving fixture bytes; no socket is opened.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from rdflib import RDF, Graph, Literal, URIRef

import estleg_client
from estleg_client import (
    CorpusNotFoundError,
    CorpusUnavailableError,
    DownloadError,
    NotFoundError,
    SanctionRow,
    corpus_root,
    fetch_corpus,
    has_type,
    iter_court_decisions,
    iter_drafts,
    iter_eu_acts,
    iter_regulations,
    iter_rows,
    load_court_decision,
    load_court_decisions,
    load_draft,
    load_eu_act,
    load_law,
    load_regulation,
    provision_rows,
    provisions_of,
    resolve_iri,
    sanction_rows,
    sanctions_of,
)
from estleg_client import _corpus, download
from estleg_client._jsonld import stream_document
from estleg_client.cli import main as cli_main
from estleg_client.rows import DictStore, build_sanction_row

REPO = Path(__file__).resolve().parent.parent
E = "https://w3id.org/estleg/"
KOV_DIR = "abja_vallavolikogu"
KOV_PEEP = "abja_muusikakooli_opetajate_tootasu_alammaara_kinnitamine_t1039736_peep.json"
RIIK_PEEP = "2024_2025_2025_2026_ja_2026_2027_oppeaasta_koolivaheajad_t1057801_peep.json"
CASE_IRI = "estleg:RK_3_18_1432_93"
EIS = "JDM/26-0214"
CELEX = "31981L0643"


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _subset(src: Path, dst: Path, keep) -> list[dict]:
    doc = json.loads(src.read_text(encoding="utf-8"))
    nodes = [n for n in doc["@graph"] if keep(n)]
    _write_json(dst, {"@context": doc["@context"], "@graph": nodes})
    return nodes


def _types(node: dict) -> list[str]:
    value = node.get("@type")
    return value if isinstance(value, list) else [value]


@pytest.fixture
def mini_corpus(tmp_path: Path, corpus_krr) -> Path:
    """A git-checkout-shaped corpus: 1-2 real records per corpus."""
    root = tmp_path / "corpus"
    krr = root / "krr_outputs"
    law = corpus_krr.path("abipolitseiniku_seadus_peep.json")
    sidecar = corpus_krr.path("sanctions/sanctions_abipolitseiniku_seadus.json")
    (krr / "sanctions").mkdir(parents=True)
    (krr / law.name).write_bytes(law.read_bytes())
    (krr / "sanctions" / sidecar.name).write_bytes(sidecar.read_bytes())
    _write_json(
        krr / "INDEX.json",
        {"laws": [{"name": "abipolitseiniku_seadus", "files": [law.name]}]},
    )
    _write_json(
        root / "data" / "law_abbreviations.json",
        {"abipolitseiniku_seadus": {"abbrev": "ABIPOL", "title": "Abipolitseiniku seadus"}},
    )
    riik = corpus_krr.path(f"regulations/riik/{RIIK_PEEP}")
    kov = corpus_krr.path(f"regulations/kov/{KOV_DIR}/{KOV_PEEP}")
    (krr / "regulations" / "riik").mkdir(parents=True)
    (krr / "regulations" / "kov" / KOV_DIR).mkdir(parents=True)
    (krr / "regulations" / "riik" / RIIK_PEEP).write_bytes(riik.read_bytes())
    (krr / "regulations" / "kov" / KOV_DIR / KOV_PEEP).write_bytes(kov.read_bytes())
    _write_json(krr / "regulations/riik/REGULATIONS_RIIK_INDEX.json", {"files": [RIIK_PEEP]})
    _write_json(
        krr / "regulations/kov/REGULATIONS_KOV_INDEX.json", {"files": [f"{KOV_DIR}/{KOV_PEEP}"]}
    )
    rk = corpus_krr.path("riigikohus/riigikohus_2020_peep.json")
    kept = {CASE_IRI}
    doc = json.loads(rk.read_text(encoding="utf-8"))
    other = next(
        n["@id"] for n in doc["@graph"]
        if "estleg:CourtDecision" in _types(n) and n["@id"] != CASE_IRI
    )
    kept.add(other)
    _subset(
        rk,
        krr / "riigikohus/riigikohus_2020_peep.json",
        lambda n: n["@id"] in kept
        or (n.get("estleg:citationSource") or {}).get("@id") in kept,
    )
    drafts = corpus_krr.path("eelnoud/eelnoud_publicconsultation_peep.json")
    eis_numbers = [
        n.get("estleg:eisNumber")
        for n in json.loads(drafts.read_text(encoding="utf-8"))["@graph"]
        if n.get("estleg:eisNumber")
    ]
    keep_eis = {EIS, next(e for e in eis_numbers if e != EIS)}
    _subset(
        drafts,
        krr / "eelnoud/eelnoud_publicconsultation_peep.json",
        lambda n: n.get("estleg:eisNumber") in keep_eis or "owl:Ontology" in _types(n),
    )
    directives = corpus_krr.path("eurlex/eurlex_directives_peep.json")
    celexes = [
        n.get("estleg:celexNumber")
        for n in json.loads(directives.read_text(encoding="utf-8"))["@graph"][:50]
    ]
    keep_celex = {CELEX, next(c for c in celexes if c and c != CELEX)}
    _subset(
        directives,
        krr / "eurlex/eurlex_directives_peep.json",
        lambda n: n.get("estleg:celexNumber") in keep_celex,
    )
    return root


# ── exact type matching (hermetic) ───────────────────────────────────────────


def _typed_graph() -> Graph:
    graph = Graph()
    for local, type_local in (
        ("P1", "LegalProvision"),
        ("P2", "KovProvision"),
        ("P3", "LegalProvisionVersion"),
        ("L1", "Subsection"),
        ("S1", "Sanction"),
        ("T1", "SanctionType"),
        ("T2", "SanctionRegime"),
    ):
        graph.add((URIRef(E + local), RDF.type, URIRef(E + type_local)))
    return graph


def test_provisions_of_is_exact_and_includes_kov_provision() -> None:
    graph = _typed_graph()
    assert provisions_of(graph) == [E + "P1", E + "P2"]
    assert provisions_of(graph, include_subsections=True) == [E + "L1", E + "P1", E + "P2"]


def test_subsections_typed_legal_provision_are_not_paragraphs() -> None:
    """The release aggregate types every lõige Subsection *and* LegalProvision."""
    graph = _typed_graph()
    graph.add((URIRef(E + "L2"), RDF.type, URIRef(E + "Subsection")))
    graph.add((URIRef(E + "L2"), RDF.type, URIRef(E + "LegalProvision")))
    assert provisions_of(graph) == [E + "P1", E + "P2"]
    assert E + "L2" in provisions_of(graph, include_subsections=True)
    assert [r.iri for r in iter_rows(graph, "provisions")] == [E + "P1", E + "P2"]
    assert {r.level for r in iter_rows(graph, "subsections")} == {"subsection"}


def test_sanctions_of_never_matches_sanction_type() -> None:
    assert sanctions_of(_typed_graph()) == [E + "S1"]


@pytest.mark.parametrize(
    ("node", "types", "expected"),
    [
        ({"@type": "estleg:Sanction"}, ("Sanction",), True),
        ({"@type": ["owl:NamedIndividual", "estleg:Sanction"]}, ("estleg:Sanction",), True),
        ({"@type": ["estleg:SanctionType"]}, ("Sanction",), False),
        ({"@type": [E + "KovProvision"]}, ("LegalProvision", "KovProvision"), True),
        ({"@type": ["estleg:KovProvision"]}, ("LegalProvision",), False),
        ({"@type": "estleg:Sanction"}, (E + "Sanction",), True),
        ({}, ("Sanction",), False),
    ],
)
def test_has_type_on_json_ld_nodes(node: dict, types: tuple[str, ...], expected: bool) -> None:
    assert has_type(node, *types) is expected


def test_has_type_on_graph_subjects() -> None:
    graph = _typed_graph()
    assert has_type("estleg:S1", "Sanction", graph=graph)
    assert not has_type("estleg:T1", "Sanction", graph=graph)
    with pytest.raises(TypeError):
        has_type({"@type": "x"})


# ── sanction normalisation (hermetic) ────────────────────────────────────────


def _sanction(**fields: Any) -> SanctionRow:
    node = {"@id": "estleg:Sanction_X_Par_1_fine", "@type": ["estleg:Sanction"]}
    for key, value in fields.items():
        node[f"estleg:{key}"] = value
    store = DictStore([node])
    return build_sanction_row(store.get(E + "Sanction_X_Par_1_fine"), store)


def _dec(value: str) -> dict:
    return {"@value": value, "@type": "xsd:decimal"}


@pytest.mark.parametrize(
    ("fields", "eur", "subject"),
    [
        ({"sanctionType": "fine", "maxPenaltyAmount": _dec("300"),
          "maxPenaltyUnit": "fine_units"}, Decimal(1200), "natural_person"),
        ({"sanctionType": "fine", "maxPenaltyAmount": _dec("400000"),
          "maxPenaltyUnit": "monetary", "maxPenaltyCurrency": "EUR"},
         Decimal(400000), "legal_person"),
        ({"sanctionType": "coercive_payment", "maxPenaltyAmount": _dec("15646.6"),
          "maxPenaltyUnit": "monetary", "maxPenaltyCurrency": "EEK"},
         Decimal("1000.00"), None),
        ({"sanctionType": "pecuniary_punishment", "maxPenaltyAmount": _dec("500"),
          "maxPenaltyUnit": "daily_rates"}, None, "natural_person"),
        ({"sanctionType": "pecuniary_punishment", "maxPenaltyAmount": _dec("10"),
          "maxPenaltyUnit": "percent_of_turnover"}, None, "legal_person"),
        ({"sanctionType": "imprisonment", "maxPenaltyAmount": _dec("5"),
          "maxPenaltyUnit": "years"}, None, "natural_person"),
        ({"sanctionType": "compulsory_dissolution"}, None, "legal_person"),
        ({"sanctionType": "confiscation"}, None, None),
    ],
)
def test_sanction_eur_normalisation_and_subject(fields, eur, subject) -> None:
    row = _sanction(**fields)
    assert row.amount_eur == eur
    assert row.max_amount_eur == eur
    assert row.subject == subject
    assert row.subject_source == ("inferred" if subject else None)


def test_sanction_min_amount_and_explicit_subject() -> None:
    row = _sanction(
        sanctionType="fine",
        minPenaltyAmount=_dec("40"),
        minPenaltyUnit="monetary",
        maxPenaltyAmount=_dec("400000"),
        maxPenaltyUnit="monetary",
        maxPenaltyCurrency="EUR",
        sanctionSubject="legal_person",
    )
    assert row.min_amount_eur == Decimal(40)
    assert (row.subject, row.subject_source) == ("legal_person", "explicit")
    record = row.as_dict()
    assert record["amount_eur"] == 400000 and isinstance(record["amount_eur"], int)
    assert list(record) == SanctionRow.columns()
    json.dumps(record)


# ── streaming parser (hermetic) ──────────────────────────────────────────────


def test_stream_document_matches_json_load(tmp_path: Path) -> None:
    nodes = [{"@id": f"estleg:N{i}", "v": "x" * (i * 37 % 500), "n": i * 1.5} for i in range(400)]
    doc = {"@graph": nodes, "@context": {"estleg": E}, "tail": [1, 2, 3]}
    path = _write_json(tmp_path / "doc.jsonld", doc)
    events = list(stream_document(path, chunk_size=97))
    assert [v for k, v in events if k == "@graph"] == nodes
    assert dict((k, v) for k, v in events if k != "@graph") == {
        "@context": {"estleg": E},
        "tail": [1, 2, 3],
    }


# ── loaders on a mini corpus (committed tier) ────────────────────────────────


def test_load_law_and_rows(mini_corpus: Path) -> None:
    graph = load_law("ABIPOL", root=mini_corpus)
    assert load_law("abipolitseiniku_seadus", root=mini_corpus).isomorphic(graph)
    rows = provision_rows(graph)
    assert len(rows) == len(provisions_of(graph)) > 0
    first = next(r for r in rows if r.iri == E + "ABIPOL_Par_1")
    assert first.paragraph == "§ 1." and first.act_prefix == "ABIPOL"
    assert first.source_url and first.source_url.startswith("https://www.riigiteataja.ee/akt/")
    assert first.temporal_status == "inForce" and not first.is_kov
    sanctions = sanction_rows(graph)
    assert {r.unit for r in sanctions} == {"days", "fine_units"}
    fine = next(r for r in sanctions if r.unit == "fine_units")
    assert fine.amount_eur == Decimal(1200) and fine.subject == "natural_person"
    subsections = list(iter_rows(graph, "subsections"))
    assert subsections and all(r.level == "subsection" and r.paragraph for r in subsections)


def test_load_regulation_by_every_key(mini_corpus: Path) -> None:
    for key in ("1057801", "t1057801", "Reg_1057801", "estleg:Reg_1057801_Map",
                "119032025005", "koolivaheajad"):
        graph = load_regulation(key, root=mini_corpus)
        assert (URIRef(E + "Reg_1057801_Map"), None, None) in graph, key
    with pytest.raises(NotFoundError):
        load_regulation("1057801", kov=True, root=mini_corpus)
    with pytest.raises(NotFoundError, match="terviktekst"):
        load_regulation("no such regulation title", root=mini_corpus)


def test_kov_regulation_provisions_are_kov(mini_corpus: Path) -> None:
    graph = load_regulation("1039736", kov=True, root=mini_corpus)
    rows = provision_rows(graph)
    assert len(rows) == 5
    assert all(r.is_kov and r.act_prefix == "Reg_1039736" for r in rows)
    (reg,) = list(iter_rows(graph, "regulations"))
    assert reg.is_kov and reg.rt_id == "1039736" and reg.provision_count == 5
    assert reg.municipality_iri == E + "Municipality_EHAK_0480"


def test_iter_regulations_filters(mini_corpus: Path) -> None:
    rows = list(iter_regulations(root=mini_corpus))
    assert sorted(r.rt_id for r in rows) == ["1039736", "1057801"]
    assert [r.rt_id for r in iter_regulations(kov=True, root=mini_corpus)] == ["1039736"]
    issuer = "haridus- ja teadusminister"
    assert [r.rt_id for r in iter_regulations(issuer=issuer, root=mini_corpus)] == ["1057801"]
    assert [r.rt_id for r in iter_regulations(issuer="Abja", root=mini_corpus)] == ["1039736"]


def test_court_decision_keys(mini_corpus: Path) -> None:
    for key in ("3-18-1432/93", "3-18-1432", "ECLI:EE:RK:2020:3.18.1432.93", CASE_IRI):
        graph = load_court_decision(key, root=mini_corpus)
        (row,) = list(iter_rows(graph, "decisions"))
        assert row.case_number == "3-18-1432/93", key
        assert row.year == 2020 and row.case_type == "Administrative"
        assert row.source_url and "asjaNr=3-18-1432/93" in row.source_url
    with pytest.raises(NotFoundError):
        load_court_decision("9-99-9999", root=mini_corpus)
    assert len(list(iter_rows(load_court_decisions(2020, root=mini_corpus), "decisions"))) == 2
    with pytest.raises(NotFoundError, match="2020-2020"):
        load_court_decisions(1999, root=mini_corpus)
    assert len(list(iter_court_decisions(year=2020, root=mini_corpus))) == 2


def test_draft_keys_and_iterator(mini_corpus: Path) -> None:
    graph = load_draft(EIS, root=mini_corpus)
    (row,) = list(iter_rows(graph, "drafts"))
    assert row.eis_number == EIS and row.phase == "PublicConsultation"
    assert row.amends_law_iris == (E + "RIIGI_Map",)
    assert load_draft("Draft_JDM26_0214", root=mini_corpus).isomorphic(graph)
    assert load_draft("riigi koosloome keskkond", root=mini_corpus).isomorphic(graph)
    rows = list(iter_drafts(phase="PublicConsultation", root=mini_corpus))
    assert len(rows) == 2
    assert list(iter_drafts(phase="Submission", root=mini_corpus)) == []


def test_eu_act_by_celex(mini_corpus: Path) -> None:
    graph = load_eu_act(CELEX, root=mini_corpus)
    (row,) = list(iter_rows(graph, "eu_acts"))
    assert row.celex == CELEX and row.doc_type == "Directive" and row.in_force is False
    assert row.source_url and CELEX in row.source_url
    assert load_eu_act("EU_" + CELEX, root=mini_corpus).isomorphic(graph)
    with pytest.raises(NotFoundError):
        load_eu_act("32016R0679", root=mini_corpus)
    assert len(list(iter_eu_acts(doc_type="directive", root=mini_corpus))) == 2
    assert list(iter_eu_acts(in_force=True, root=mini_corpus)) == []


def test_resolve_iri_dispatches_by_family(mini_corpus: Path) -> None:
    for iri in ("estleg:Reg_1057801_Par_1", CASE_IRI, "estleg:Draft_JDM26_0214",
                f"estleg:EU_{CELEX}", "estleg:ABIPOL_Par_1"):
        assert resolve_iri(iri, root=mini_corpus) == E + iri.split(":", 1)[1]
    assert resolve_iri("estleg:Reg_1057801_Par_99", root=mini_corpus) is None


def test_cli_subcommands(mini_corpus: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(mini_corpus)
    assert cli_main(["abipolitseiniku_seadus", "--root", root]) == 0
    assert "provisions: " in capsys.readouterr().out
    assert cli_main(["law", "ABIPOL", "--root", root, "--rows", "sanctions"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split(",") == SanctionRow.columns() and len(out) == 5
    assert cli_main(["regulation", "1039736", "--kov", "--root", root]) == 0
    assert capsys.readouterr().out.splitlines()[1] == "provisions: 5"
    assert cli_main(["decisions", "--year", "2020", "--root", root, "--format", "jsonl"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["year"] == 2020
    assert cli_main(["draft", EIS, "--root", root]) == 0
    assert "drafts: 1" in capsys.readouterr().out
    assert cli_main(["eu-acts", "--root", root]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 3
    assert cli_main(["regulation", "no-such-thing", "--root", root]) == 1
    assert "No regulation matched" in capsys.readouterr().err


def test_cli_module_smoke(mini_corpus: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), env.get("PYTHONPATH", "")])
    proc = subprocess.run(
        [sys.executable, "-m", "estleg_client.cli", "law", "ABIPOL", "--root", str(mini_corpus)],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("triples: ")


# ── corpus discovery (hermetic) ──────────────────────────────────────────────


@pytest.fixture
def isolated_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No env, no checkout above the package or cwd, an empty user cache."""
    for name in (_corpus.ENV_ROOT, _corpus.LEGACY_ENV_ROOT):
        monkeypatch.delenv(name, raising=False)
    cache = tmp_path / "cache"
    monkeypatch.setenv(_corpus.ENV_CACHE, str(cache))
    nowhere = tmp_path / "site-packages" / "estleg_client"
    nowhere.mkdir(parents=True)
    monkeypatch.setattr(_corpus, "__file__", str(nowhere / "_corpus.py"))
    monkeypatch.chdir(tmp_path)
    return cache


def test_corpus_root_without_corpus_names_fetch_corpus(isolated_discovery: Path) -> None:
    with pytest.raises(CorpusNotFoundError, match="fetch_corpus") as caught:
        corpus_root()
    assert isinstance(caught.value, FileNotFoundError)


def test_corpus_root_env_and_cache(isolated_discovery: Path, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    older = isolated_discovery / "corpus" / "v1.0.0"
    newer = isolated_discovery / "corpus" / "v1.10.0"
    for path in (older, newer):
        _write_json(path / _corpus.MANIFEST_NAME, {"version": path.name})
    assert corpus_root() == newer
    monkeypatch.setenv(_corpus.ENV_ROOT, str(older / "krr_outputs"))
    with pytest.raises(CorpusNotFoundError, match="ESTLEG_CORPUS_ROOT"):
        corpus_root()
    (older / "krr_outputs").mkdir()
    assert corpus_root() == older


# ── download helper (hermetic, fake urlopen) ─────────────────────────────────


class _FakeRelease:
    """Serves release assets from memory in place of ``urllib.request.urlopen``."""

    def __init__(self, files: dict[str, bytes], *, sums: dict[str, str] | None = None):
        self.files = files
        digests = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
        digests.update(sums or {})
        self.files["SHA256SUMS"] = "".join(
            f"{digests[name]}  {name}\n" for name in sorted(digests)
        ).encode()
        self.requests: list[str] = []

    def __call__(self, request, timeout: float = 0):
        url = request.full_url
        self.requests.append(url)
        name = url.rsplit("/", 1)[1]
        if name not in self.files:
            raise download.DownloadError(f"404 {url}")
        return _Response(self.files[name])


class _Response(io.BytesIO):
    status = 200


def _gz(payload: Any) -> bytes:
    return gzip.compress(json.dumps(payload, ensure_ascii=False).encode(), mtime=0)


def _release_files(corpus_krr) -> dict[str, bytes]:
    law = json.loads(corpus_krr.path("abipolitseiniku_seadus_peep.json").read_text("utf-8"))
    sanc = json.loads(
        corpus_krr.path("sanctions/sanctions_abipolitseiniku_seadus.json").read_text("utf-8")
    )
    stub = {"@id": "estleg:Reg_1_Map", "@type": ["estleg:Act"], "estleg:isStubNode": True}
    combined_nodes = [
        n for n in law["@graph"] + sanc["@graph"] if "owl:Ontology" not in _types(n)
    ] + [stub]
    drafts = json.loads(
        corpus_krr.path("eelnoud/eelnoud_publicconsultation_peep.json").read_text("utf-8")
    )
    eurlex = json.loads(corpus_krr.path("eurlex/eurlex_directives_peep.json").read_text("utf-8"))
    return {
        "combined_ontology.jsonld.gz": _gz(
            {"@context": law["@context"], "@graph": combined_nodes}
        ),
        "eelnoud_combined.jsonld.gz": _gz(
            {"@context": drafts["@context"],
             "@graph": [n for n in drafts["@graph"] if "owl:Ontology" not in _types(n)][:5]}
        ),
        "eurlex_combined.jsonld.gz": _gz(
            {"@context": eurlex["@context"],
             "@graph": [n for n in eurlex["@graph"] if n.get("estleg:celexNumber") == CELEX]}
        ),
        "LICENSE": b"MIT\n",
    }


def test_fetch_corpus_end_to_end(tmp_path: Path, corpus_krr, isolated_discovery: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRelease(_release_files(corpus_krr))
    monkeypatch.setattr(download, "urlopen", fake)
    root = fetch_corpus(progress=None)
    assert root == isolated_discovery / "corpus" / "v1.0.0"
    assert all(url.startswith(download.RELEASE_BASE_URL + "/v1.0.0/") for url in fake.requests)
    manifest = json.loads((root / "estleg_corpus.json").read_text("utf-8"))
    assert set(manifest["assets"]) == {
        "combined_ontology.jsonld.gz", "eelnoud_combined.jsonld.gz",
        "eurlex_combined.jsonld.gz", "LICENSE",
    }
    assert "NOTICE" in manifest["skipped_optional_assets"]
    assert (root / "LICENSE").read_bytes() == b"MIT\n"

    # corpus_root() discovers the download in the user cache, no configuration.
    assert corpus_root() == root
    graph = load_law("ABIPOL")
    assert load_law("abipolitseiniku_seadus").isomorphic(graph)
    assert len(provisions_of(graph)) == 49
    assert len(sanction_rows(graph)) == 4
    assert all(r.source_url for r in sanction_rows(graph))
    assert (URIRef(E + "Reg_1_Map"), None, None) not in graph  # stubs are not sharded
    assert list(iter_rows(load_draft(EIS), "drafts"))[0].eis_number == EIS
    assert len(list(iter_drafts())) == 5
    assert list(iter_rows(load_eu_act(CELEX), "eu_acts"))[0].celex == CELEX
    with pytest.raises(CorpusUnavailableError, match="git checkout"):
        load_regulation("1057801")
    with pytest.raises(CorpusUnavailableError):
        load_court_decision("3-18-1432/93")

    # Second run: everything verified from disk, only SHA256SUMS is fetched again.
    fake.requests.clear()
    fetch_corpus(progress=None)
    assert [u.rsplit("/", 1)[1] for u in fake.requests] == ["SHA256SUMS"]


def test_fetch_corpus_rejects_bad_hash_and_unlisted(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    files = {"eelnoud_combined.jsonld.gz": _gz({"@graph": []})}
    fake = _FakeRelease(files, sums={"eelnoud_combined.jsonld.gz": "0" * 64})
    monkeypatch.setattr(download, "urlopen", fake)
    with pytest.raises(DownloadError, match="SHA-256 mismatch"):
        fetch_corpus(tmp_path / "a", assets=["eelnoud_combined.jsonld.gz"],
                     optional_assets=(), progress=None)
    assert not (tmp_path / "a" / "downloads" / "eelnoud_combined.jsonld.gz").exists()
    assert not (tmp_path / "a" / "estleg_corpus.json").exists()
    with pytest.raises(DownloadError, match="does not list"):
        fetch_corpus(tmp_path / "b", assets=["curia_combined.jsonld.gz"], progress=None)
    with pytest.raises(DownloadError, match="unsafe"):
        fetch_corpus(tmp_path / "c", assets=["../x"], progress=None)


def test_fetch_corpus_expected_sums_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = {"eelnoud_combined.jsonld.gz": _gz({"@graph": []})}
    monkeypatch.setattr(download, "urlopen", _FakeRelease(files))
    with pytest.raises(DownloadError, match="expected_sums"):
        fetch_corpus(tmp_path, assets=["eelnoud_combined.jsonld.gz"], progress=None,
                     expected_sums={"eelnoud_combined.jsonld.gz": "f" * 64})


def test_fetch_corpus_tar_assets_are_contained(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    def tar_bytes(member: str) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            data = b"{}"
            info = tarfile.TarInfo(member)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    files = {"ok.tar.gz": tar_bytes("riigikohus/riigikohus_2020_peep.json"),
             "evil.tar.gz": tar_bytes("../../escape.json")}
    monkeypatch.setattr(download, "urlopen", _FakeRelease(files))
    root = fetch_corpus(tmp_path / "t", assets=["ok.tar.gz"], optional_assets=(), progress=None)
    assert (root / "krr_outputs/riigikohus/riigikohus_2020_peep.json").read_bytes() == b"{}"
    with pytest.raises(DownloadError, match="escapes"):
        fetch_corpus(tmp_path / "u", assets=["evil.tar.gz"], optional_assets=(), progress=None)
    assert not (tmp_path / "escape.json").exists()


def test_parse_sha256sums_format() -> None:
    digest = "a" * 64
    assert download.parse_sha256sums(f"{digest}  INDEX.json\n{digest} *x.gz\n\n") == {
        "INDEX.json": digest, "x.gz": digest,
    }
    with pytest.raises(DownloadError):
        download.parse_sha256sums("nothex  INDEX.json\n")


def test_failed_later_asset_preserves_existing_corpus(tmp_path, monkeypatch):
    original = tmp_path / "krr_outputs/eelnoud/eelnoud_combined.jsonld"
    original.parent.mkdir(parents=True)
    original.write_text('{"@graph": [{"@id": "estleg:Draft_OLD"}]}')
    before = original.read_bytes()
    files = {"eelnoud_combined.jsonld.gz": _gz({"@graph": []}),
             "eurlex_combined.jsonld.gz": b"not a gzip file"}
    assets = tuple(files)
    monkeypatch.setattr(download, "urlopen", _FakeRelease(files))
    with pytest.raises((DownloadError, OSError)):
        fetch_corpus(tmp_path, assets=assets, optional_assets=(), progress=None)
    assert original.read_bytes() == before


@pytest.mark.parametrize("name", [r"..\escape", r"C:\escape", "/escape", "x/y"])
def test_asset_names_are_safe_on_every_supported_platform(name):
    with pytest.raises(DownloadError):
        download._check_name(name)


def test_rows_are_typed_and_frozen() -> None:
    graph = Graph()
    node = URIRef(E + "S1")
    graph.add((node, RDF.type, URIRef(E + "Sanction")))
    graph.add((node, URIRef(E + "sanctionType"), Literal("fine")))
    (row,) = sanction_rows(graph)
    with pytest.raises(AttributeError):
        row.iri = "x"  # type: ignore[misc]
    assert (Path(estleg_client.__file__).parent / "py.typed").is_file()
