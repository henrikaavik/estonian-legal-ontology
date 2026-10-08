"""#711: state-regulation index, "no measure required" rows, the NIM cache,
and an offline end-to-end run of the matcher."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from estleg import generate_transposition_mapping as mod

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _reg(path: Path, iri: str, title: str) -> None:
    _write(
        path,
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": iri,
                    "@type": ["estleg:Act", "estleg:MinisterialRegulation"],
                    "rdfs:label": f"{title} (määrus)",
                    "dc:source": title,
                }
            ],
        },
    )


def test_extract_law_name_matches_documented_maarus():
    assert mod.extract_law_name("Elamislubade registri põhimäärus") == (
        "Elamislubade registri põhimäärus"
    )
    assert mod.extract_law_name("Ravimite määrus1") == "Ravimite määrus"
    assert mod.extract_law_name("Tubakaseadus1") == "Tubakaseadus"


@pytest.mark.parametrize(
    "title",
    [
        "EM estime MNE non nécessaire - MS does not consider NEM necessary.",
        "MS does not consider NEM necessary",
    ],
)
def test_nem_not_necessary_rows_detected(title):
    assert mod.is_no_measure_required(title)


def test_ordinary_titles_are_not_nem():
    assert not mod.is_no_measure_required("Tubakaseadus")


@pytest.fixture
def riik(tmp_path: Path) -> Path:
    base = tmp_path / "krr_outputs" / "regulations" / "riik"
    _reg(base / "a_t1_peep.json", "estleg:Reg_1_Map", "Elamislubade ja töölubade registri põhimäärus")
    _reg(base / "b_t2_peep.json", "estleg:Reg_2_Map", "Aadressiandmete süsteem")
    # Two ministries reuse one title: ambiguous, never linked.
    _reg(base / "c_t3_peep.json", "estleg:Reg_3_Map", "Toetuse andmise kord")
    _reg(base / "d_t4_peep.json", "estleg:Reg_4_Map", "Toetuse andmise kord")
    return base


def test_regulation_index_and_exact_match(riik):
    index = mod.build_regulation_index(riik)
    entry, method = mod.match_regulation_title("Aadressiandmete süsteem", index)
    assert method == "regulation_exact_title"
    assert entry["files"] == ["regulations/riik/b_t2_peep.json"]
    assert entry["kind"] == "regulation"


def test_near_title_recovers_the_smoking_gun(riik):
    index = mod.build_regulation_index(riik)
    entry, method = mod.match_regulation_title(
        "Elamislubade ja töölubade registri pidamise põhimäärus", index
    )
    assert method == "regulation_near_title"
    assert entry["name"] == "a_t1"


def test_ambiguous_and_unrelated_titles_do_not_link(riik):
    index = mod.build_regulation_index(riik)
    assert mod.match_regulation_title("Toetuse andmise kord", index) == (None, "ambiguous")
    assert mod.match_regulation_title("Välisriigi lippu kandvate laevade kontrollimise kord", index) == (None, "")
    # Containment alone is not enough: a short shared prefix fails the ratio.
    assert mod.match_regulation_title(
        "Aadressiandmete süsteemi andmekoosseis ja pidamise kord", index
    )[0] is None


def test_real_corpus_reg_1032308_is_indexed():
    """The ticket's smoking gun resolves against the shipped state regulations."""
    index = mod.build_regulation_index(REPO_ROOT / "krr_outputs" / "regulations" / "riik")
    entry, method = mod.match_regulation_title(
        "Elamislubade ja töölubade registri pidamise põhimäärus", index
    )
    assert method == "regulation_near_title"
    assert entry["files"] == [
        "regulations/riik/elamislubade_ja_toolubade_registri_pohimaarus_t1032308_peep.json"
    ]


def test_measures_cache_roundtrip_is_sorted(tmp_path):
    path = tmp_path / "cache.json"
    rows = [
        {"celex_dir": "32002L0002", "directive_uri": "u2", "title_nat": "B"},
        {"celex_dir": "32001L0001", "directive_uri": "u1", "title_nat": "A", "title_en": "a"},
    ]
    mod.write_measures_cache(rows, {"32002L0002": "2003-01-01"}, partial=False, path=path)
    measures, deadlines, partial = mod.load_measures_cache(path)
    assert [m["celex_dir"] for m in measures] == ["32001L0001", "32002L0002"]
    assert measures[1]["title_en"] == ""
    assert deadlines == {"32002L0002": "2003-01-01"}
    assert partial is False


def test_offline_run_links_regulations_and_classifies_nem(tmp_path, monkeypatch):
    """End-to-end from the NIM cache: law link, regulation link, NEM status,
    no_evidence status, gap CSV — and no network call."""
    krr = tmp_path / "krr_outputs"
    eurlex = krr / "eurlex"
    _write(
        krr / "tubakaseadus_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": "estleg:TUBAKA_Map",
                    "@type": ["estleg:Act"],
                    "estleg:sourceAct": "Tubakaseadus",
                    "estleg:transpositionStatus": "unknown",
                }
            ],
        },
    )
    _write(krr / "INDEX.json", {"total_laws": 1, "laws": [{"name": "tubakaseadus", "files": ["tubakaseadus_peep.json"]}]})
    _reg(krr / "regulations" / "riik" / "elamislubade_t1032308_peep.json", "estleg:Reg_1032308_Map",
         "Elamislubade ja töölubade registri põhimäärus")
    directives = [
        ("32014L0040", "2016-05-20"),
        ("32004L0038", "2006-04-30"),
        ("32012L0043", "2013-09-01"),
        ("32020L0001", "2021-01-01"),
        ("32020L0002", None),
    ]
    _write(
        eurlex / "eurlex_directives_peep.json",
        {
            "@context": mod.CONTEXT,
            "@graph": [
                {
                    "@id": f"estleg:EU_{celex}",
                    "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
                    "rdfs:label": f"Direktiiv {celex}",
                    "estleg:celexNumber": celex,
                    "estleg:inForce": {"@value": "true", "@type": "xsd:boolean"},
                    **(
                        {"estleg:transpositionDeadline": {"@value": d, "@type": "xsd:date"}}
                        if d
                        else {}
                    ),
                }
                for celex, d in directives
            ],
        },
    )
    measures = [
        {"celex_dir": "32014L0040", "directive_uri": "u", "title_nat": "TUBAKASEADUS1"},
        {"celex_dir": "32004L0038", "directive_uri": "u",
         "title_nat": "Elamislubade ja töölubade registri pidamise põhimäärus"},
        {"celex_dir": "32012L0043", "directive_uri": "u",
         "title_nat": "EM estime MNE non nécessaire - MS does not consider NEM necessary."},
        {"celex_dir": "32020L0001", "directive_uri": "u", "title_nat": "Tundmatu kord"},
    ]
    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    mod.write_measures_cache(measures, None, partial=False)

    def _no_network(**_k):
        raise AssertionError("--offline must not query CELLAR")

    monkeypatch.setattr(mod, "fetch_transposition_measures", _no_network)
    monkeypatch.setattr(mod, "fetch_directive_deadlines", _no_network)
    monkeypatch.setattr(sys, "argv", ["generate_transposition_mapping.py", "--offline"])
    mod.main()

    report = json.loads((krr / "reports" / "transposition_mapping.json").read_text(encoding="utf-8"))
    assert report["measures_source"] == "cache"
    assert report["total_matched"] == 2
    assert report["total_matched_regulation_pairs"] == 1
    assert report["unique_laws"] == 1 and report["unique_regulations"] == 1
    assert report["total_unmatched"] == 1
    assert report["total_no_measure_required_rows"] == 1
    assert [r["directive_celex"] for r in report["no_measure_required"]] == ["32012L0043"]
    reg_row = next(m for m in report["mappings"] if m["matched_act_kind"] == "regulation")
    assert reg_row["match_method"] == "regulation_near_title"
    assert report["transposition_status"]["counts"] == {
        "transposed": 2,
        "no_measure_required": 1,
        "no_evidence_in_corpus": 1,
    }

    reg = json.loads((krr / "regulations" / "riik" / "elamislubade_t1032308_peep.json").read_text(encoding="utf-8"))
    assert reg["@graph"][0]["estleg:transposesDirective"] == [{"@id": "estleg:EU_32004L0038"}]
    law = json.loads((krr / "tubakaseadus_peep.json").read_text(encoding="utf-8"))
    assert "estleg:transpositionStatus" not in law["@graph"][0]

    status = {
        n["estleg:celexNumber"]: n.get("estleg:transpositionStatus")
        for n in json.loads((eurlex / "eurlex_directives_peep.json").read_text(encoding="utf-8"))["@graph"]
    }
    assert status == {
        "32014L0040": "transposed",
        "32004L0038": "transposed",
        "32012L0043": "no_measure_required",
        "32020L0001": "no_evidence_in_corpus",
        "32020L0002": None,  # no deadline, no evidence: no status
    }
    with open(krr / "exports" / "transposition_gap.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["celex"] for r in rows] == sorted(status)[:-1]
    # Deadlines were left as they were (the cache had none).
    assert next(r for r in rows if r["celex"] == "32020L0001")["transposition_deadline"] == "2021-01-01"


def test_online_row_order_does_not_change_outputs(tmp_path, monkeypatch):
    """CELLAR pages by NIM URI, the cache by CELEX: the representative title of
    a pair and the written link lists must not depend on row order (#711)."""
    rows = [
        {"celex_dir": "32004L0049", "directive_uri": "u1", "title_nat": "RAUDTEESEADUS1"},
        {"celex_dir": "32004L0049", "directive_uri": "u2", "title_nat": "Raudteeseadus1"},
        {"celex_dir": "32001L0016", "directive_uri": "u3", "title_nat": "Raudteeseadus"},
    ]
    outputs = []
    for order in (rows, list(reversed(rows))):
        krr = tmp_path / f"run{len(outputs)}" / "krr_outputs"
        eurlex = krr / "eurlex"
        _write(krr / "raudteeseadus_peep.json", {"@context": mod.CONTEXT, "@graph": [
            {"@id": "estleg:RTS_Map", "@type": ["estleg:Act"], "estleg:sourceAct": "Raudteeseadus"}]})
        _write(krr / "INDEX.json", {"laws": [{"name": "raudteeseadus", "files": ["raudteeseadus_peep.json"]}]})
        _write(eurlex / "eurlex_directives_peep.json", {"@context": mod.CONTEXT, "@graph": [
            {"@id": f"estleg:EU_{c}", "@type": ["estleg:EULegislation"], "estleg:celexNumber": c}
            for c in ("32004L0049", "32001L0016")]})
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
        monkeypatch.setattr(mod, "fetch_transposition_measures", lambda _o=order, **_k: (list(_o), False))
        monkeypatch.setattr(mod, "fetch_directive_deadlines", lambda **_k: ({}, False))
        mod.main([])
        outputs.append(
            [
                (krr / rel).read_text(encoding="utf-8")
                for rel in (
                    "reports/transposition_mapping.json",
                    "reports/transposition_measures.json",
                    "raudteeseadus_peep.json",
                    "eurlex/eurlex_directives_peep.json",
                )
            ]
        )
    assert outputs[0] == outputs[1]


def test_sentinel_deadlines_never_reach_the_peep(tmp_path, monkeypatch):
    """#352: CELLAR's 1001-01-01 / 1002-02-02 null sentinels are dropped on
    the fetch path and on the write path (an old cache may still hold them)."""
    monkeypatch.setattr(
        mod,
        "sparql_query_with_retry",
        lambda _q: [
            {"celex": {"value": "31995L0060"}, "deadline": {"value": "1001-01-01"}},
            {"celex": {"value": "31992L0014"}, "deadline": {"value": "2002-04-01"}},
        ],
    )
    deadlines, partial = mod.fetch_directive_deadlines()
    assert deadlines == {"31992L0014": "2002-04-01"} and partial is False

    eurlex = tmp_path / "eurlex"
    _write(eurlex / "eurlex_directives_peep.json", {"@graph": [
        {"@id": "estleg:EU_31995L0060", "estleg:celexNumber": "31995L0060"},
        {"@id": "estleg:EU_31992L0014", "estleg:celexNumber": "31992L0014"},
    ]})
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    assert mod.update_directive_deadlines({"31995L0060": "1002-02-02", "31992L0014": "2002-04-01"}) == 1
    graph = json.loads((eurlex / "eurlex_directives_peep.json").read_text(encoding="utf-8"))["@graph"]
    assert "estleg:transpositionDeadline" not in graph[0]
    assert graph[1]["estleg:transpositionDeadline"]["@value"] == "2002-04-01"


def test_status_pass_restamps_estonia_relevance_lens(tmp_path, monkeypatch):
    """#527: estoniaRelevant + the INDEX lens follow the new mapping, and the
    trailing keys come out in one canonical order whatever the node's history."""
    krr = tmp_path / "krr_outputs"
    eurlex = krr / "eurlex"
    _write(eurlex / "EURLEX_INDEX.json", {"total_acts": 2})
    _write(eurlex / "eurlex_directives_peep.json", {"@graph": [
        {"@id": "estleg:EU_32000L0053", "@type": ["estleg:EULegislation"],
         "estleg:celexNumber": "32000L0053",
         "estleg:euDocumentType": {"@id": "estleg:EUDocType_Directive"},
         "estleg:transpositionStatus": "no_evidence_in_corpus",
         "estleg:estoniaRelevant": {"@value": "true", "@type": "xsd:boolean"},
         "estleg:transpositionDeadline": {"@value": "2002-04-21", "@type": "xsd:date"},
         "estleg:transposedBy": [{"@id": "estleg:X_Map"}]},
        {"@id": "estleg:EU_31990L0001", "@type": ["estleg:EULegislation"],
         "estleg:celexNumber": "31990L0001",
         "estleg:euDocumentType": {"@id": "estleg:EUDocType_Directive"},
         "estleg:estoniaRelevant": {"@value": "true", "@type": "xsd:boolean"},
         "estleg:transpositionDeadline": {"@value": "1992-01-01", "@type": "xsd:date"}},
    ]})
    _write(krr / "reports" / "transposition_mapping.json", {"mappings": []})
    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "EURLEX_DIR", eurlex)
    mod.apply_transposition_status(no_measure_required=set(), act_files=[])
    graph = json.loads((eurlex / "eurlex_directives_peep.json").read_text(encoding="utf-8"))["@graph"]
    transposed, stale = graph
    assert list(transposed)[-2:] == ["estleg:transpositionStatus", "estleg:estoniaRelevant"]
    assert transposed["estleg:transpositionStatus"] == "transposed"
    assert "estleg:estoniaRelevant" not in stale  # no longer in the mapping / transposedBy
    lens = json.loads((eurlex / "EURLEX_INDEX.json").read_text(encoding="utf-8"))["lens"]
    assert lens["estonia_relevant"] == 1
