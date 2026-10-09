"""#729: per-file hash manifest + ``--only-changed`` incremental plans."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from estleg import build_hash_manifest as hm
from estleg import run_all_integration as rai

pytestmark = pytest.mark.unit

TOY_STEPS = [
    {"name": "net", "tier": "ingest", "depends_on": [], "reads": ["*_peep.json"],
     "writes": ["versions/*.jsonld"]},
    {"name": "enrich", "depends_on": [], "reads": ["*_peep.json"],
     "writes": ["*_peep.json", "reports/enrich.json"]},
    {"name": "drafts", "depends_on": [], "reads": ["eelnoud/*_peep.json"],
     "writes": ["eelnoud/eelnoud_combined.jsonld"]},
    {"name": "uses_versions", "depends_on": ["net"], "reads": ["versions/*.jsonld"],
     "writes": ["reports/versions.json"]},
    {"name": "build", "depends_on": ["enrich", "drafts", "uses_versions"],
     "reads": ["*_peep.json", "eelnoud/eelnoud_combined.jsonld", "reports/enrich.json"],
     "writes": ["combined.jsonld"]},
    {"name": "assets", "depends_on": ["build"], "reads": ["combined.jsonld"],
     "writes": ["../release/*"]},
]
TOY_TOPO = [s["name"] for s in TOY_STEPS]
TOY_COMMITTED = ("*_peep.json", "eelnoud/*_peep.json")


def _tree(root: Path) -> Path:
    krr = root / "krr_outputs"
    files = {
        "a_peep.json": "{}", "b_peep.json": "{}",
        "eelnoud/d_peep.json": "{}", "eelnoud/eelnoud_combined.jsonld": "{}",
        "versions/a.jsonld": "{}", "reports/enrich.json": "{}",
        "combined.jsonld": "{}", ".cache/ignored_peep.json": "{}",
        "nested/x_peep.json": "{}",
    }
    for rel, text in files.items():
        (krr / rel).parent.mkdir(parents=True, exist_ok=True)
        (krr / rel).write_text(text, encoding="utf-8")
    (root / "release").mkdir()
    (root / "release" / "asset.gz").write_bytes(b"x")
    return krr


@pytest.mark.parametrize("rel,pattern,expected", [
    ("a_peep.json", "*_peep.json", True),
    ("nested/x_peep.json", "*_peep.json", False),
    ("regulations/kov/t/x_peep.json", "regulations/**/*_peep.json", True),
    ("regulations/x_peep.json", "regulations/**/*_peep.json", True),
    ("../release/a.gz", "../release/*", True),
    ("release/a.gz", "../release/*", False),
    ("../metadata.jsonld", "*.jsonld", False),
])
def test_path_matches(rel: str, pattern: str, expected: bool) -> None:
    assert hm.path_matches(rel, pattern) is expected


def test_manifest_covers_declared_patterns_only(tmp_path: Path) -> None:
    krr = _tree(tmp_path)
    manifest = hm.build_manifest(TOY_STEPS, krr)
    assert sorted(manifest["files"]) == [
        "../release/asset.gz", "a_peep.json", "b_peep.json", "combined.jsonld",
        "eelnoud/d_peep.json", "eelnoud/eelnoud_combined.jsonld",
        "reports/enrich.json", "versions/a.jsonld",
    ]  # hidden .cache/ and the undeclared nested/ file are not hashed
    assert manifest["stats"] == {"hashed": 8, "reusedFromCache": 0}


def test_digest_is_content_only_and_cache_is_reused(tmp_path: Path) -> None:
    krr = _tree(tmp_path)
    first = hm.build_manifest(TOY_STEPS, krr)
    os.utime(krr / "a_peep.json", ns=(1, 1))  # mtime-only change
    second = hm.build_manifest(TOY_STEPS, krr, previous=first)
    assert second["manifestDigest"] == first["manifestDigest"]
    assert second["stats"] == {"hashed": 1, "reusedFromCache": 7}
    assert hm.changed_paths(hm.diff_manifests(first, second)) == []


def test_diff_detects_modified_added_removed(tmp_path: Path) -> None:
    krr = _tree(tmp_path)
    old = hm.build_manifest(TOY_STEPS, krr)
    (krr / "a_peep.json").write_text('{"x": 1}', encoding="utf-8")
    (krr / "c_peep.json").write_text("{}", encoding="utf-8")
    (krr / "reports" / "enrich.json").unlink()
    new = hm.build_manifest(TOY_STEPS, krr, previous=old)
    assert hm.diff_manifests(old, new) == {
        "added": ["c_peep.json"], "removed": ["reports/enrich.json"],
        "modified": ["a_peep.json"],
    }


def test_manifest_round_trip(tmp_path: Path) -> None:
    krr = _tree(tmp_path)
    manifest = hm.build_manifest(TOY_STEPS, krr)
    path = tmp_path / "m" / "hash_manifest.json"
    hm.write_manifest(manifest, path)
    assert hm.load_manifest(path) == json.loads(json.dumps(manifest))
    path.write_text('{"version": 999, "files": {}}', encoding="utf-8")
    assert hm.load_manifest(path) is None
    assert hm.load_manifest(tmp_path / "absent.json") is None


def _plan(changed: list[str], **kw) -> dict:
    return hm.select_steps(TOY_STEPS, TOY_TOPO, changed, TOY_COMMITTED, **kw)


def test_select_reads_plus_dependents_and_skips_ingest() -> None:
    plan = _plan(["a_peep.json"])
    assert plan["selected"] == ["enrich", "build", "assets"]
    assert plan["excludedIngest"] == ["net"]
    assert plan["seeds"] == {"build": ["reads *_peep.json"], "enrich": ["reads *_peep.json"]}
    with_ingest = _plan(["a_peep.json"], include_ingest=True)
    assert with_ingest["selected"] == ["net", "enrich", "uses_versions", "build", "assets"]


def test_select_regenerates_a_changed_derived_artefact() -> None:
    plan = _plan(["reports/enrich.json"])
    assert plan["selected"] == ["enrich", "build", "assets"]
    assert plan["seeds"]["enrich"] == ["regenerates reports/enrich.json"]


def test_select_nothing_for_an_unchanged_tree() -> None:
    assert _plan([])["selected"] == []


# --------------------------------------------------------------------------
# The real DAG
# --------------------------------------------------------------------------

REAL_TOPO = rai.validate_dag(rai.STEPS, rai.COMMITTED_INPUTS)


def _real_plan(changed: list[str]) -> dict:
    return hm.select_steps(rai.STEPS, REAL_TOPO, changed, rai.COMMITTED_INPUTS,
                           ingest_tier=rai.TIER_INGEST)


def _expected(seed_pattern_match) -> list[str]:
    by_name = {s["name"]: s for s in rai.STEPS}
    seeds = {s["name"] for s in rai.STEPS
             if s.get("tier") != rai.TIER_INGEST and seed_pattern_match(s)}
    closure = rai._transitive_dependents(
        {n: [s["name"] for s in rai.STEPS if n in s.get("depends_on", [])] for n in by_name})
    selected = set(seeds)
    for n in seeds:
        selected |= closure[n]
    return [n for n in REAL_TOPO if n in selected and by_name[n].get("tier") != rai.TIER_INGEST]


def test_real_dag_law_peep_selects_peep_readers_and_dependents() -> None:
    plan = _real_plan(["kaitseliidu_seadus_peep.json"])
    assert plan["selected"] == _expected(lambda s: "*_peep.json" in s.get("reads", []))
    assert "build_release_artifacts.py" in plan["selected"]
    # institutions/*.json -> generate_draft_lifecycle -> rebuild_eelnoud_combined
    assert plan["selected"].index("generate_draft_lifecycle") < plan["selected"].index(
        "rebuild_eelnoud_combined")
    assert not any(rai.step_tier({"tier": s.get("tier")}) == rai.TIER_INGEST
                   for s in rai.STEPS if s["name"] in plan["selected"])


def test_real_dag_curia_peep_is_a_narrow_plan() -> None:
    plan = _real_plan(["curia/curia_ag_opinions_peep.json"])
    assert plan["selected"][:2] == ["link_curia_eu_legislation.py", "rebuild_curia_combined"]
    assert "extract_cross_references.py" not in plan["selected"]
    assert plan["selected"][-1] == "build_release_assets.py"


def test_real_dag_unchanged_tree_selects_nothing() -> None:
    assert _real_plan([])["selected"] == []


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------


@pytest.fixture
def toy_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    krr = _tree(tmp_path)
    monkeypatch.setattr(rai, "KRR_DIR", krr)
    monkeypatch.setattr(rai, "STEPS", [dict(s, script=f"{s['name']}.py", description=s["name"])
                                       for s in TOY_STEPS])
    monkeypatch.setattr(rai, "COMMITTED_INPUTS", TOY_COMMITTED)
    monkeypatch.setattr(rai, "MANIFEST_DIR", tmp_path / "manifests")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for step in rai.STEPS:
        (scripts / step["script"]).write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(rai, "SCRIPTS_DIR", scripts)
    return krr


def test_cli_dry_run_without_manifest_plans_a_full_run(toy_runner: Path, capsys) -> None:
    rai.main(["--dry-run", "--only-changed"])
    out = capsys.readouterr().out
    assert "Full rebuild required" in out
    assert "5. [enrichment] assets" in out  # every non-ingest step planned
    assert "[ingest] net" not in out
    assert not (toy_runner / ".cache" / "hash_manifest.json").exists()


def test_cli_record_then_only_changed(toy_runner: Path, capsys) -> None:
    manifest = toy_runner / ".cache" / "hash_manifest.json"
    with pytest.raises(SystemExit) as exc:
        rai.main(["--record-hash-manifest"])
    assert exc.value.code == 0 and manifest.exists()
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc:
        rai.main(["--dry-run", "--only-changed"])
    assert exc.value.code == 0
    assert "Nothing to do" in capsys.readouterr().out

    (toy_runner / "eelnoud" / "d_peep.json").write_text('{"changed": true}', encoding="utf-8")
    rai.main(["--dry-run", "--only-changed", "--manifest", str(manifest)])
    out = capsys.readouterr().out
    assert "Changed inputs since the manifest: 1" in out
    assert "- drafts  [reads eelnoud/*_peep.json]" in out
    assert "- build  [downstream dependent]" in out
    assert "    - enrich  [" not in out


def test_cli_rejects_release_with_only_changed(toy_runner: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        rai.main(["--only-changed", "--release", "--dry-run"])
    assert exc.value.code == 2


def test_run_manifest_records_only_changed(toy_runner: Path, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rai, "REPO_ROOT", tmp_path)
    with pytest.raises(SystemExit):
        rai.main(["--record-hash-manifest"])
    (toy_runner / "combined.jsonld").write_text('{"edited": 1}', encoding="utf-8")
    rai.main(["--only-changed", "--snapshot", "none"])
    run = json.loads((tmp_path / "manifests" / "latest_pipeline_manifest.json").read_text())
    assert run["onlyChanged"] is True
    assert run["changedInputCount"] == 1
    assert run["selectedSteps"] == ["build", "assets"]
    assert [p["name"] for p in run["phases"]] == ["build", "assets"]
    # The manifest was refreshed: the same tree now plans nothing.
    plan = rai.plan_only_changed(TOY_TOPO, rai.hash_manifest_path())
    assert plan["changed"] == []
