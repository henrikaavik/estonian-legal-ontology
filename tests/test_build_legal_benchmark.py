"""Tests for the legal-reasoning benchmark builder (issue #727).

Default tier: the builder runs on ``tests/fixtures/legal_benchmark`` — trimmed
copies of the real Abieluvararegistri seadus / Notariaadiseadus peeps, the
AVRS provision-version sidecar and six 2015 Riigikohus decisions. The decision
free-text fields in the fixture are replaced by sentinel strings so a builder
that leaked decision text, party names or judges would fail here.

``corpus`` tier: the real build with lowered caps, plus a byte-for-byte rebuild
of the committed ``eval/benchmark/sample``.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from estleg import build_legal_benchmark as bench

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "legal_benchmark"
SCHEMA = REPO / "eval" / "benchmark" / "item.schema.json"
SAMPLE = REPO / "eval" / "benchmark" / "sample"
SENTINEL = "FIXTURE-SENTINEL"


@pytest.fixture
def mini_krr(tmp_path: Path) -> Path:
    target = tmp_path / "krr"
    shutil.copytree(FIXTURE, target)
    return target


@pytest.fixture
def built(mini_krr: Path) -> tuple[list[dict], dict]:
    return bench.build_benchmark(mini_krr, bench.BuildConfig())


def _schema_errors(items: list[dict]) -> list[str]:
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8")),
        format_checker=jsonschema.FormatChecker(),
    )
    return [f"{it['id']}: {e.message}" for it in items for e in validator.iter_errors(it)]


def _by_task(items: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {task: [] for task in bench.TASKS}
    for item in items:
        out[item["task"]].append(item)
    return out


# ── build on the mini-corpus ─────────────────────────────────────────────────


def test_builds_all_three_tasks(built):
    items, manifest = built
    by_task = _by_task(items)
    assert all(by_task[task] for task in bench.TASKS), {t: len(v) for t, v in by_task.items()}
    assert manifest["total_items"] == len(items)
    assert manifest["source_stats"]["point_in_time"]["sidecars_with_distinct_history"] == 1


def test_items_match_json_schema_and_builtin_validator(built):
    items, _ = built
    assert _schema_errors(items) == []
    assert all(bench.validate_item(it) == [] for it in items)


def test_build_is_deterministic(mini_krr):
    first, m1 = bench.build_benchmark(mini_krr, bench.BuildConfig())
    second, m2 = bench.build_benchmark(mini_krr, bench.BuildConfig())
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert m1 == m2


def test_no_group_crosses_splits(built):
    items, _ = built
    splits: dict[str, set[str]] = {}
    for item in items:
        splits.setdefault(item["group"], set()).add(item["split"])
    assert all(len(s) == 1 for s in splits.values())
    assert all(it["split"] == bench.assign_split(it["group"]) for it in items)


def test_point_in_time_answer_is_the_version_in_force(built, mini_krr):
    items, _ = built
    sidecar = json.loads(
        (mini_krr / "provision_versions" / "abieluvararegistri_seadus.jsonld").read_text("utf-8")
    )
    versions = {n["@id"]: n for n in sidecar["@graph"] if "estleg:versionText" in n}
    for item in _by_task(items)["point_in_time"]:
        snap = item["snapshot"]
        version = versions[snap["version_iri"]]
        day = date.fromisoformat(snap["query_date"])
        assert date.fromisoformat(version["estleg:versionValidFrom"]["@value"]) <= day
        if "estleg:versionValidTo" in version:
            assert day <= date.fromisoformat(version["estleg:versionValidTo"]["@value"])
        assert item["answers"] == [version["estleg:versionText"].strip()]
        answer_key = bench.normalize_text(item["answers"][0])
        assert all(bench.normalize_text(d) != answer_key for d in item["distractors"])
        assert item["choices"][item["answer_index"]] == item["answers"][0]
        assert item["rt_citation"] == "https://www.riigiteataja.ee/akt/114032025013.xml"
        assert "Abieluvararegistri seadus" in item["prompt"]


def test_cross_reference_answers_are_existing_edges(built, mini_krr):
    items, _ = built
    edges: dict[str, set[str]] = {}
    for name in ("abieluvararegistri_seadus", "notariaadiseadus"):
        for node in json.loads((mini_krr / f"{name}_peep.json").read_text("utf-8"))["@graph"]:
            edges[node["@id"]] = {r["@id"] for r in node.get("estleg:references") or []}
    xrefs = _by_task(items)["cross_reference"]
    assert any(it["answers"] == ["estleg:NotS_Par_41"] for it in xrefs), "cross-law citation"
    assert any(it["answers"] == ["estleg:AVRS_Par_37"] for it in xrefs), "in-law citation"
    for item in xrefs:
        source = item["source_iris"][0]
        assert item["answers"][0] in edges[source]
        assert item["snapshot"]["citation_text"] in item["prompt"]
        assert item["answers"][0] not in item["distractors"]


def test_court_items_carry_no_decision_free_text(built):
    items, _ = built
    court = _by_task(items)["court_interpretation"]
    assert court
    for item in court:
        assert SENTINEL not in json.dumps(item, ensure_ascii=False)
        assert item["rt_citation"] is None
        assert item["distractors"] == []
        assert item["snapshot"]["case_number"] in item["prompt"]


def test_criminal_cases_excluded_unless_requested(mini_krr):
    default, manifest = bench.build_benchmark(mini_krr, bench.BuildConfig())
    widened, _ = bench.build_benchmark(mini_krr, bench.BuildConfig(include_criminal=True))
    n_default = len(_by_task(default)["court_interpretation"])
    n_widened = len(_by_task(widened)["court_interpretation"])
    skipped = manifest["source_stats"]["court_interpretation"]["skip_criminal_or_misdemeanour"]
    assert skipped == 2
    assert n_widened == n_default + skipped


def test_caps_are_applied_and_recorded(mini_krr):
    items, manifest = bench.build_benchmark(
        mini_krr, bench.BuildConfig(cap_per_task=1, per_group_cap=1)
    )
    assert all(len(v) <= 1 for v in _by_task(items).values())
    assert manifest["caps"]["per_task"] == 1
    assert manifest["caps"]["per_law_group"] == 1


def test_cli_writes_splits_and_sample(mini_krr, tmp_path):
    out = tmp_path / "out"
    assert bench.main(["--krr", str(mini_krr), "--out", str(out), "--sample-per-task", "2"]) == 0
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))
    items = bench.read_items(sorted(out.glob("*.jsonl")))
    assert len(items) == manifest["total_items"] <= 2 * len(bench.TASKS)
    assert manifest["full_build"]["total_items"] >= len(items)
    assert _schema_errors(items) == []


# ── reference scorer ─────────────────────────────────────────────────────────


def test_score_perfect_and_empty_predictions(built):
    items, _ = built
    gold = {
        it["id"]: (it["answer_index"] if it["task"] == "point_in_time" else it["answers"])
        for it in items
    }
    perfect = bench.score_predictions(items, gold)
    assert all(t["exact_match"] == 1.0 and t["set_f1"] == 1.0 for t in perfect["per_task"].values())
    assert perfect["overall"]["macro_set_f1"] == 1.0
    empty = bench.score_predictions(items, {})
    assert all(t["exact_match"] == 0.0 and t["answered"] == 0 for t in empty["per_task"].values())


def test_score_partial_set_and_iri_forms():
    court = {
        "id": "court-x", "task": "court_interpretation", "answer_type": "iri_set",
        "answers": ["estleg:A_Par_1", "estleg:A_Par_2"],
    }
    xref = {
        "id": "xref-x", "task": "cross_reference", "answer_type": "iri",
        "answers": ["estleg:B_Par_3"], "choices": ["estleg:B_Par_4", "estleg:B_Par_3"],
    }
    pit = {
        "id": "pit-x", "task": "point_in_time", "answer_type": "choice",
        "answers": ["(1) Tekst  siin."], "choices": ["muu", "(1) Tekst  siin."],
    }
    result = bench.score_predictions(
        [court, xref, pit],
        {
            "court-x": ["https://w3id.org/estleg/A_Par_1", "estleg:A_Par_9"],
            "xref-x": 1,
            "pit-x": "(1) tekst siin.",
        },
    )["per_task"]
    assert result["court_interpretation"]["exact_match"] == 0.0
    assert result["court_interpretation"]["set_f1"] == 0.5
    assert result["cross_reference"]["exact_match"] == 1.0
    assert result["point_in_time"]["exact_match"] == 1.0


def test_assign_split_is_stable_and_roughly_proportional():
    groups = [f"law_{n}" for n in range(2000)]
    splits = [bench.assign_split(g) for g in groups]
    assert splits == [bench.assign_split(g) for g in groups]
    share = splits.count("train") / len(splits)
    assert 0.75 < share < 0.85


# ── committed sample ─────────────────────────────────────────────────────────


def test_committed_sample_is_schema_valid_and_matches_manifest():
    manifest = json.loads((SAMPLE / "manifest.json").read_text("utf-8"))
    items = bench.read_items(sorted(SAMPLE.glob("*.jsonl")))
    assert _schema_errors(items) == []
    counts, _ = bench.count_by_split(items, bench.TASKS)
    assert counts == manifest["items"]
    assert all(sum(by_split.values()) <= manifest["sample_per_task"] for by_split in counts.values())
    court_blob = json.dumps(
        [it for it in items if it["task"] == "court_interpretation"], ensure_ascii=False
    )
    for forbidden in ('"estleg:summary"', '"estleg:legalText"', '"estleg:judge"'):
        assert forbidden not in court_blob


# ── corpus tier ──────────────────────────────────────────────────────────────


@pytest.mark.corpus
def test_real_corpus_build_with_lowered_caps(corpus_krr):
    for rel in ("INDEX.json", "provision_versions", "riigikohus", "abieluvararegistri_seadus_peep.json"):
        corpus_krr.path(rel)
    items, manifest = bench.build_benchmark(
        corpus_krr.root, bench.BuildConfig(cap_per_task=60, per_group_cap=5)
    )
    by_task = _by_task(items)
    assert all(len(by_task[t]) == 60 for t in bench.TASKS)
    assert _schema_errors(items) == []
    stats = manifest["source_stats"]
    assert stats["point_in_time"]["sidecars_with_distinct_history"] >= 400
    assert stats["court_interpretation"]["resolved_citations"] >= 80_000


@pytest.mark.corpus
def test_committed_sample_rebuilds_byte_for_byte(corpus_krr, tmp_path):
    corpus_krr.path("provision_versions")
    out = tmp_path / "sample"
    assert bench.main(["--krr", str(corpus_krr.root), "--out", str(out), "--sample-per-task", "200"]) == 0
    for name in ("train.jsonl", "dev.jsonl", "test.jsonl", "manifest.json"):
        assert (out / name).read_bytes() == (SAMPLE / name).read_bytes(), (
            f"{name} drifted; regenerate with "
            "`python3 scripts/build_legal_benchmark.py --sample-per-task 200 --out eval/benchmark/sample`"
        )


def test_cli_scores_one_split(mini_krr, tmp_path, capsys):
    out = tmp_path / "out"
    bench.main(["--krr", str(mini_krr), "--out", str(out)])
    train = bench.read_items([out / "train.jsonl"])
    predictions = tmp_path / "pred.json"
    predictions.write_text(json.dumps({it["id"]: it["answers"] for it in train}), "utf-8")
    capsys.readouterr()
    assert bench.main(["--out", str(out), "--score", str(predictions), "--split", "train"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["overall"]["n"] == len(train)
    assert report["overall"]["macro_set_f1"] == 1.0


def test_cmdi_stub_is_well_formed_and_flags_open_items():
    from estleg.estleg_common import parse_xml_file

    root = parse_xml_file(REPO / "eval" / "benchmark" / "cmdi.xml")
    ns = {"cmd": "http://www.clarin.eu/cmd/1"}
    assert root.find("cmd:Header/cmd:MdProfile", ns) is not None
    assert "TO-VERIFY" in (REPO / "eval" / "benchmark" / "cmdi.xml").read_text("utf-8")
