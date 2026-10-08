"""#704 / #705: every produced layer is a declared DAG step; the
pipeline-version gate; ingest-tier steps are recorded but not run by default."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from estleg import run_all_integration as rai

REPO = Path(__file__).resolve().parent.parent


def _names() -> list[str]:
    return [s["name"] for s in rai.STEPS]


def _by_name() -> dict[str, dict]:
    return {s["name"]: s for s in rai.STEPS}


@pytest.mark.parametrize(
    "script",
    [
        "generate_provision_versions.py",
        "derive_act_temporal_status.py",
        "derive_court_interpretation_staleness.py",
        "generate_annotations.py",
        "generate_analytical_overlay.py",
        "generate_act_expressions_608.py",
        "link_amendment_versions.py",
        "build_release_assets.py",
    ],
)
def test_every_produced_layer_has_a_step(script: str) -> None:
    assert any(s["script"] == script for s in rai.STEPS), script
    assert (REPO / "scripts" / script).is_file()


def test_combined_inverses_are_declared_as_embedded_in_the_build() -> None:
    """#520 runs inside the combined build; it must be declared there and
    never scheduled as a second pass over combined."""
    build = _by_name()["build_release_artifacts.py"]
    assert any("materialize_combined_inverses" in e for e in build["embeds"])
    assert not any(s["script"] == "materialize_combined_inverses.py" for s in rai.STEPS)


def test_tiers_are_known_and_ordered() -> None:
    topo = rai.validate_dag(rai.STEPS, rai.COMMITTED_INPUTS)
    by_name = _by_name()
    rank = {tier: i for i, tier in enumerate(rai.TIERS)}
    for step in rai.STEPS:
        assert rai.step_tier(step) in rai.TIERS, step["name"]
    # build is unique; package steps come after it; ingest steps come first.
    tiers = [rai.step_tier(by_name[n]) for n in topo]
    assert tiers.count(rai.TIER_BUILD) == 1
    first_package = tiers.index(rai.TIER_PACKAGE)
    assert tiers.index(rai.TIER_BUILD) < first_package
    assert all(rank[t] == rank[rai.TIER_PACKAGE] for t in tiers[first_package:])
    assert all(t == rai.TIER_INGEST for t in tiers[: tiers.count(rai.TIER_INGEST)])


def test_network_generators_are_ingest_tier() -> None:
    by_name = _by_name()
    for name in ("generate_provision_versions.py", "generate_annotations.py"):
        assert rai.step_tier(by_name[name]) == rai.TIER_INGEST


def test_version_join_follows_the_chains_and_the_version_layer() -> None:
    topo = rai.validate_dag(rai.STEPS, rai.COMMITTED_INPUTS)
    join = topo.index("link_amendment_versions.py")
    assert topo.index("generate_amendment_history.py") < join
    assert topo.index("generate_provision_versions.py") < join
    assert join < topo.index("build_release_artifacts.py")


def test_act_status_runs_after_extract_temporal_data() -> None:
    """Both write temporalStatus; the version-derived pass must win."""
    deps = _by_name()["derive_act_temporal_status.py"]["depends_on"]
    assert "extract_temporal_data.py" in deps
    args = _by_name()["derive_act_temporal_status.py"]["args"]
    assert args[-1] == rai.BUILD_EVALUATION_DATE


def test_validate_dag_rejects_an_unordered_writer_of_a_derived_read() -> None:
    steps = [
        {"name": "a", "script": "a", "depends_on": [], "reads": [],
         "writes": ["reports/x.json"]},
        {"name": "b", "script": "b", "depends_on": ["a"], "reads": ["reports/x.json"],
         "writes": []},
        {"name": "c", "script": "c", "depends_on": [], "reads": [],
         "writes": ["reports/x.json"]},
    ]
    with pytest.raises(rai.DAGError, match="neither depends on the other"):
        rai.validate_dag(steps, ())
    steps[2]["depends_on"] = ["b"]  # writes after the reader: stale read
    with pytest.raises(rai.DAGError, match="would be stale"):
        rai.validate_dag(steps, ())
    steps[2]["depends_on"] = []
    steps[1]["depends_on"] = ["a", "c"]  # both writers precede the reader
    assert rai.validate_dag(steps, ()) == ["a", "c", "b"]


def test_package_steps_never_rewrite_what_the_build_read() -> None:
    """#705: version stamping after the build would leave combined stale."""
    build = next(s for s in rai.STEPS if s["name"] == "build_release_artifacts.py")
    for step in rai.STEPS:
        if rai.step_tier(step) != rai.TIER_PACKAGE:
            continue
        for written in step["writes"]:
            assert not any(rai._paths_can_overlap(read, written) for read in build["reads"]), (
                step["name"], written)


def test_committed_inputs_are_exempt_from_the_ordering_check() -> None:
    steps = [
        {"name": "a", "script": "a", "depends_on": [], "reads": ["*_peep.json"],
         "writes": ["*_peep.json"]},
        {"name": "b", "script": "b", "depends_on": [], "reads": ["*_peep.json"],
         "writes": ["*_peep.json"]},
    ]
    assert rai.validate_dag(steps, ("*_peep.json",)) == ["a", "b"]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("*_peep.json", "amendments/**/*.json", False),
        ("amendments/**/*.json", "amendments/amendments_x.json", True),
        ("provision_versions/*.jsonld", "provision_versions/kars.jsonld", True),
        ("regulations/**/*_peep.json", "regulations/riik/a_peep.json", True),
        ("curia/*_peep.json", "eurlex/*_peep.json", False),
        ("../release/*", "../release/SHA256SUMS", True),
    ],
)
def test_paths_can_overlap(a: str, b: str, expected: bool) -> None:
    assert rai._paths_can_overlap(a, b) is expected
    assert rai._paths_can_overlap(b, a) is expected


def test_ingest_steps_are_recorded_not_run(tmp_path: Path, monkeypatch) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    marker = tmp_path / "ran.txt"
    for name in ("fetch.py", "enrich.py"):
        (scripts / name).write_text(
            f"open({str(marker)!r}, 'a').write({name!r} + '\\n')\n", encoding="utf-8"
        )
    monkeypatch.setattr(rai, "SCRIPTS_DIR", scripts)
    monkeypatch.setattr(rai, "MANIFEST_DIR", tmp_path / "manifest")
    steps = [
        {"name": "fetch.py", "script": "fetch.py", "description": "f",
         "tier": rai.TIER_INGEST, "depends_on": [], "reads": [], "writes": []},
        {"name": "enrich.py", "script": "enrich.py", "description": "e",
         "depends_on": ["fetch.py"], "reads": [], "writes": []},
    ]
    topo = rai.validate_dag(steps, ())
    result = rai.run_dag(steps, topo, dry_run=False, resume_from=None,
                         validate_each=False, per_script_timeout=60, parallel=1)
    statuses = {r["name"]: r["status"] for r in result["ledger"]}
    assert statuses == {"fetch.py": "skipped_ingest", "enrich.py": "succeeded"}
    assert marker.read_text(encoding="utf-8").split() == ["enrich.py"]
    assert not result["failed"]

    marker.unlink()
    result = rai.run_dag(steps, topo, dry_run=False, resume_from=None,
                         validate_each=False, per_script_timeout=60, parallel=1,
                         include_ingest=True)
    assert marker.read_text(encoding="utf-8").split() == ["fetch.py", "enrich.py"]


# ---------------------------------------------------------------------------
# pipeline_version gate
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org",
             "HOME": str(repo), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    ).stdout.strip()


@pytest.fixture()
def gate_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f").write_text("x", encoding="utf-8")
    _git(repo, "add", "f")
    _git(repo, "commit", "-q", "-m", "c")
    return repo, _git(repo, "rev-parse", "--short=10", "HEAD")


def _report(krr: Path, name: str, version: str | None) -> None:
    path = krr / "reports" / "kov" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {} if version is None else {"pipeline_version": version}
    path.write_text(json.dumps(body), encoding="utf-8")


def test_gate_passes_resolving_and_baselined_reports(gate_repo, tmp_path: Path) -> None:
    repo, sha = gate_repo
    krr = tmp_path / "krr"
    _report(krr, "a_coverage.json", sha)
    _report(krr, "b_coverage.json", "deadbeef1")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"reports": {"b_coverage.json": "deadbeef1"}}), encoding="utf-8")
    result = rai.check_pipeline_versions(krr, baseline_path=baseline, repo=repo)
    assert result["failures"] == []
    assert result["baselined"] == ["b_coverage.json"]
    assert result["staleBaseline"] == []


def test_gate_fails_on_an_unknown_sha_or_missing_field(gate_repo, tmp_path: Path) -> None:
    repo, _sha = gate_repo
    krr = tmp_path / "krr"
    _report(krr, "a_coverage.json", "deadbeef1")
    _report(krr, "b_coverage.json", None)
    result = rai.check_pipeline_versions(krr, baseline_path=tmp_path / "none.json", repo=repo)
    assert result["failures"] == ["a_coverage.json", "b_coverage.json"]


def test_baseline_exempts_only_the_recorded_sha(gate_repo, tmp_path: Path) -> None:
    repo, sha = gate_repo
    krr = tmp_path / "krr"
    _report(krr, "a_coverage.json", "cafebabe2")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"reports": {"a_coverage.json": "deadbeef1",
                                                "gone_coverage.json": "deadbeef1"}}),
                        encoding="utf-8")
    result = rai.check_pipeline_versions(krr, baseline_path=baseline, repo=repo)
    assert result["failures"] == ["a_coverage.json"]
    assert result["staleBaseline"] == ["a_coverage.json", "gone_coverage.json"]


# The frozen reports the 2026-09-04 re-validation found unresolvable. The
# baseline may only shrink: an entry is removed when its report is
# regenerated from a commit that exists.
FROZEN_PIPELINE_VERSIONS = {
    "extract_annotations_coverage.json": "2d46eb74b",
    "extract_cross_references_coverage.json": "2074016e2",
    "extract_legal_concepts_coverage.json": "2074016e2",
    "extract_temporal_data_coverage.json": "96abdac2f0",
    "generate_inverse_references_coverage.json": "2074016e2",
    "extract_sanctions_coverage.json": "f6c71444bd",
}


def test_committed_baseline_only_shrinks() -> None:
    baseline = rai.load_pipeline_version_baseline()
    assert baseline.items() <= FROZEN_PIPELINE_VERSIONS.items()
