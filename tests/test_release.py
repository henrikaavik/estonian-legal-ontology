"""Tests for the release build DAG + release manifest in ``run_all_integration``.

Covers the deliverables of issue #125:

  1. Step DAG validity — no cycles, every ``depends_on`` resolves, every
     ``reads`` is covered by a committed input or a prior step's ``writes``.
     A deliberately-cyclic / dangling-dep / unsatisfied-read DAG is rejected.
  2. Topological order — a step always runs after its declared dependencies.
  3. ``--dry-run --release`` prints the topo-order + the validators and runs
     nothing (``subprocess.run`` is patched to blow up if called).
  4. ``--release --validate-only`` runs the three validators (monkeypatched)
     and writes the manifest's validator section but no generation step.
  5. ``release_manifest.json`` shape — has the step ledger, the validator
     results, the artifact content hash, and ``release_ok``.
  6. ``release_ok`` is ``False`` if any monkeypatched step or validator fails.

No real pipeline runs: ``KRR_DIR``/``MANIFEST_DIR`` are redirected to a
``tmp_path`` tree and ``subprocess.run`` is stubbed end-to-end.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_all_integration as r  # noqa: E402  (import after sys.path mutation)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect KRR_DIR/MANIFEST_DIR + REPO_ROOT/SCRIPTS_DIR to a tmp tree.

    Seeds the committed release artifacts and one fake script per DAG step so
    ``SCRIPTS_DIR / step.script`` exists (otherwise steps are marked
    ``missing``).
    """
    repo = tmp_path / "repo"
    krr = repo / "krr_outputs"
    manifest_dir = krr / "reports" / "integration"
    scripts_dir = repo / "scripts"
    for d in (krr, manifest_dir, scripts_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Release artifacts that hash_release_artifacts() looks for.
    (krr / "combined_ontology.jsonld").write_text(
        '{"@context": {}, "@graph": []}', encoding="utf-8"
    )
    (krr / "controlled_vocabulary.jsonld").write_text(
        '{"@context": {}, "@graph": []}', encoding="utf-8"
    )
    (repo / "metadata.jsonld").write_text('{"@id": "x"}', encoding="utf-8")
    (krr / "INDEX.json").write_text('{"laws": []}', encoding="utf-8")
    for sub, name in (
        ("regulations/riik", "REGULATIONS_RIIK_INDEX.json"),
        ("regulations/kov", "REGULATIONS_KOV_INDEX.json"),
        ("eelnoud", "EELNOUD_INDEX.json"),
        ("riigikohus", "RIIGIKOHUS_INDEX.json"),
        ("curia", "CURIA_INDEX.json"),
        ("eurlex", "EURLEX_INDEX.json"),
    ):
        (krr / sub).mkdir(parents=True, exist_ok=True)
        (krr / sub / name).write_text("{}", encoding="utf-8")

    # A stub file per step so the scripts are not "missing".
    for step in r.STEPS:
        (scripts_dir / step["script"]).write_text("# stub\n", encoding="utf-8")
    for spec in r.RELEASE_VALIDATORS:
        (scripts_dir / spec["script"]).write_text("# stub\n", encoding="utf-8")

    monkeypatch.setattr(r, "REPO_ROOT", repo, raising=True)
    monkeypatch.setattr(r, "SCRIPTS_DIR", scripts_dir, raising=True)
    monkeypatch.setattr(r, "KRR_DIR", krr, raising=True)
    monkeypatch.setattr(r, "MANIFEST_DIR", manifest_dir, raising=True)
    return repo


def _run_main(monkeypatch: pytest.MonkeyPatch, *argv_extra: str) -> SystemExit:
    """Invoke ``run_all_integration.main`` with the given extra argv, capturing
    the SystemExit it raises."""
    monkeypatch.setattr(sys, "argv", ["run_all_integration.py", *argv_extra])
    with pytest.raises(SystemExit) as excinfo:
        r.main()
    return excinfo.value


# ===========================================================================
# 1. DAG validity
# ===========================================================================


class TestDAGValidity:
    def test_real_dag_validates_and_topo_sorts(self) -> None:
        topo = r.validate_dag(r.STEPS, r.COMMITTED_INPUTS)
        assert len(topo) == len(r.STEPS)
        assert set(topo) == {s["name"] for s in r.STEPS}
        # The two known dependency chains must hold in the order.
        assert topo.index("extract_cross_references.py") < topo.index(
            "generate_inverse_references.py"
        )
        assert topo.index("generate_transposition_mapping.py") < topo.index(
            "generate_harmonisation_links.py"
        )
        # The combined-ontology rebuild (build_release_artifacts.py) must be
        # last — it depends on every enrichment step and regenerates the
        # release artifact after enrichment (issue #252 / #467). The
        # similarity aggregation runs immediately before it.
        assert topo[-1] == "build_release_artifacts.py"
        assert topo[-2] == "generate_similarity_index.py"

    def test_cyclic_dag_is_rejected(self) -> None:
        cyclic = [
            {"name": "a", "description": "", "script": "a.py",
             "depends_on": ["b"], "reads": [], "writes": []},
            {"name": "b", "description": "", "script": "b.py",
             "depends_on": ["a"], "reads": [], "writes": []},
        ]
        with pytest.raises(r.DAGError, match="cycle"):
            r.validate_dag(cyclic, ())

    def test_self_cycle_is_rejected(self) -> None:
        bad = [
            {"name": "a", "description": "", "script": "a.py",
             "depends_on": ["a"], "reads": [], "writes": []},
        ]
        with pytest.raises(r.DAGError, match="itself"):
            r.validate_dag(bad, ())

    def test_dangling_dependency_is_rejected(self) -> None:
        bad = [
            {"name": "a", "description": "", "script": "a.py",
             "depends_on": ["ghost"], "reads": [], "writes": []},
        ]
        with pytest.raises(r.DAGError, match="unknown step"):
            r.validate_dag(bad, ())

    def test_duplicate_step_name_is_rejected(self) -> None:
        bad = [
            {"name": "a", "description": "", "script": "a.py",
             "depends_on": [], "reads": [], "writes": []},
            {"name": "a", "description": "", "script": "a2.py",
             "depends_on": [], "reads": [], "writes": []},
        ]
        with pytest.raises(r.DAGError, match="duplicate step"):
            r.validate_dag(bad, ())

    def test_unsatisfied_read_is_rejected(self) -> None:
        # 'b' reads something nobody writes and that isn't a committed input.
        bad = [
            {"name": "a", "description": "", "script": "a.py",
             "depends_on": [], "reads": [], "writes": ["a_out.json"]},
            {"name": "b", "description": "", "script": "b.py",
             "depends_on": ["a"], "reads": ["never_produced.json"],
             "writes": []},
        ]
        with pytest.raises(r.DAGError, match="never_produced.json"):
            r.validate_dag(bad, ())

    def test_committed_input_satisfies_read(self) -> None:
        ok = [
            {"name": "a", "description": "", "script": "a.py",
             "depends_on": [], "reads": ["*_peep.json"], "writes": ["a.json"]},
        ]
        topo = r.validate_dag(ok, ("*_peep.json",))
        assert topo == ["a"]

    def test_prior_step_write_satisfies_read(self) -> None:
        ok = [
            {"name": "a", "description": "", "script": "a.py",
             "depends_on": [], "reads": [], "writes": ["mapping.json"]},
            {"name": "b", "description": "", "script": "b.py",
             "depends_on": ["a"], "reads": ["mapping.json"], "writes": []},
        ]
        topo = r.validate_dag(ok, ())
        assert topo == ["a", "b"]

    def test_main_exits_2_on_invalid_dag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cyclic = [
            {"name": "a", "description": "", "script": "a.py",
             "depends_on": ["b"], "reads": [], "writes": []},
            {"name": "b", "description": "", "script": "b.py",
             "depends_on": ["a"], "reads": [], "writes": []},
        ]
        monkeypatch.setattr(r, "STEPS", cyclic, raising=True)
        exc = _run_main(monkeypatch, "--dry-run")
        assert exc.code == 2


# ===========================================================================
# 2. Topological order vs declared deps
# ===========================================================================


class TestTopoOrderHonoursDeps:
    def test_every_step_runs_after_its_dependencies(self) -> None:
        topo = r.validate_dag(r.STEPS, r.COMMITTED_INPUTS)
        position = {name: i for i, name in enumerate(topo)}
        for step in r.STEPS:
            for dep in step["depends_on"]:
                assert position[dep] < position[step["name"]], (
                    f"{step['name']} must run after its dependency {dep}"
                )

    def test_topo_is_deterministic_and_stable_over_source_order(self) -> None:
        # Two independent steps with no deps keep source order.
        steps = [
            {"name": "first", "description": "", "script": "first.py",
             "depends_on": [], "reads": [], "writes": []},
            {"name": "second", "description": "", "script": "second.py",
             "depends_on": [], "reads": [], "writes": []},
        ]
        assert r.validate_dag(steps, ()) == ["first", "second"]


# ===========================================================================
# 2b. --parallel: dependency-ready steps run concurrently, deps still respected
# ===========================================================================


class TestParallelExecution:
    def _three_step_dag(self) -> list[dict]:
        return [
            {"name": "a.py", "description": "A", "script": "a.py",
             "depends_on": [], "reads": [], "writes": ["x.json"]},
            {"name": "b.py", "description": "B", "script": "b.py",
             "depends_on": [], "reads": [], "writes": ["y.json"]},
            {"name": "c.py", "description": "C", "script": "c.py",
             "depends_on": ["a.py", "b.py"], "reads": ["x.json", "y.json"],
             "writes": ["z.json"]},
        ]

    @pytest.fixture
    def stub_scripts_dir(self, tmp_path: Path,
                         monkeypatch: pytest.MonkeyPatch) -> Path:
        """Point SCRIPTS_DIR at a tmp dir holding empty a/b/c.py + a logs dir."""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        for name in ("a.py", "b.py", "c.py"):
            (scripts / name).write_text("# stub\n", encoding="utf-8")
        manifest_dir = tmp_path / "krr_outputs" / "reports" / "integration"
        manifest_dir.mkdir(parents=True)
        monkeypatch.setattr(r, "SCRIPTS_DIR", scripts, raising=True)
        monkeypatch.setattr(r, "MANIFEST_DIR", manifest_dir, raising=True)
        monkeypatch.setattr(r, "REPO_ROOT", tmp_path, raising=True)
        return scripts

    def test_parallel_runs_dependent_after_its_deps(
        self, stub_scripts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steps = self._three_step_dag()
        topo = r.validate_dag(steps, ())
        order: list[str] = []

        def fake_run(cmd, **kwargs):
            order.append(Path(cmd[1]).name)
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("ok\n")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(r.subprocess, "run", fake_run)
        res = r.run_dag(steps, topo, dry_run=False, resume_from=None,
                        validate_each=False, per_script_timeout=30, parallel=2)
        assert res["succeeded"] == {"a.py", "b.py", "c.py"}
        assert not res["failed"]
        # The fan-in step must be the last one executed.
        assert order[-1] == "c.py"

    def test_parallel_skips_dependents_when_a_dep_fails(
        self, stub_scripts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steps = self._three_step_dag()
        topo = r.validate_dag(steps, ())

        def fake_run(cmd, **kwargs):
            name = Path(cmd[1]).name
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("out\n")
            return subprocess.CompletedProcess(
                args=cmd, returncode=1 if name == "a.py" else 0
            )

        monkeypatch.setattr(r.subprocess, "run", fake_run)
        res = r.run_dag(steps, topo, dry_run=False, resume_from=None,
                        validate_each=False, per_script_timeout=30, parallel=3)
        assert "a.py" in res["failed"]
        # c.py depended on the failed a.py — it must be skipped/blocked.
        assert "c.py" in res["failed"]
        c_entry = next(e for e in res["ledger"] if e["name"] == "c.py")
        assert c_entry["status"] == "blocked"


# ===========================================================================
# 2c. --parallel safety gate: reject overlapping-writes among concurrent steps
# ===========================================================================


class TestParallelWriteDisjointness:
    """``--parallel > 1`` must be rejected (exit 2 / DAGError) when two steps
    with no dependency relation both write an overlapping corpus target —
    concurrent execution there is last-writer-wins clobbering. ``--parallel
    1`` (serial — the default) is never affected, and a DAG whose
    overlapping-writes steps ARE dependency-ordered is accepted."""

    def _overlapping_unordered_dag(self) -> list[dict]:
        # a and b both write *_peep.json and neither depends on the other.
        return [
            {"name": "a.py", "description": "A", "script": "a.py",
             "depends_on": [], "reads": ["*_peep.json"],
             "writes": ["*_peep.json", "a_report.json"]},
            {"name": "b.py", "description": "B", "script": "b.py",
             "depends_on": [], "reads": ["*_peep.json"],
             "writes": ["*_peep.json", "b_report.json"]},
            {"name": "c.py", "description": "C", "script": "c.py",
             "depends_on": ["a.py", "b.py"], "reads": ["*_peep.json"],
             "writes": ["c_report.json"]},
        ]

    def _overlapping_ordered_dag(self) -> list[dict]:
        # Same overlapping *_peep.json writes, but b depends on a — they can
        # never be in flight at the same time, so --parallel is safe.
        return [
            {"name": "a.py", "description": "A", "script": "a.py",
             "depends_on": [], "reads": ["*_peep.json"],
             "writes": ["*_peep.json", "a_report.json"]},
            {"name": "b.py", "description": "B", "script": "b.py",
             "depends_on": ["a.py"], "reads": ["*_peep.json", "a_report.json"],
             "writes": ["*_peep.json", "b_report.json"]},
            {"name": "c.py", "description": "C", "script": "c.py",
             "depends_on": ["b.py"], "reads": ["*_peep.json"],
             "writes": ["c_report.json"]},
        ]

    def _disjoint_unordered_dag(self) -> list[dict]:
        # Independent steps that write *different*, non-overlapping targets.
        return [
            {"name": "a.py", "description": "A", "script": "a.py",
             "depends_on": [], "reads": [], "writes": ["a_only.json"]},
            {"name": "b.py", "description": "B", "script": "b.py",
             "depends_on": [], "reads": [], "writes": ["b_only.json"]},
            {"name": "c.py", "description": "C", "script": "c.py",
             "depends_on": ["a.py", "b.py"], "reads": [],
             "writes": ["c_only.json"]},
        ]

    # -- direct validate_dag() checks ------------------------------------

    def test_parallel2_rejected_for_overlapping_unordered_writes(self) -> None:
        steps = self._overlapping_unordered_dag()
        # Serial is fine.
        assert r.validate_dag(steps, ("*_peep.json",), parallel=1) == [
            "a.py", "b.py", "c.py"
        ]
        # Default (no parallel arg) is serial — also fine.
        assert r.validate_dag(steps, ("*_peep.json",)) == ["a.py", "b.py", "c.py"]
        # --parallel 2 must be rejected, naming both steps + the target.
        with pytest.raises(r.DAGError) as excinfo:
            r.validate_dag(steps, ("*_peep.json",), parallel=2)
        msg = str(excinfo.value)
        assert "a.py" in msg and "b.py" in msg
        assert "*_peep.json" in msg
        assert "--parallel" in msg

    def test_parallel2_accepted_when_overlapping_writes_are_dependency_ordered(
        self,
    ) -> None:
        steps = self._overlapping_ordered_dag()
        # Overlapping *_peep.json writes, but the dependency edge serializes
        # them — --parallel 2 is accepted.
        assert r.validate_dag(steps, ("*_peep.json",), parallel=2) == [
            "a.py", "b.py", "c.py"
        ]

    def test_parallel2_accepted_when_writes_are_disjoint(self) -> None:
        steps = self._disjoint_unordered_dag()
        assert r.validate_dag(steps, (), parallel=2) == ["a.py", "b.py", "c.py"]

    def test_real_dag_rejects_parallel_gt_1(self) -> None:
        # The real DAG has overlapping *_peep.json writes among
        # independent steps, so --parallel 2 must be rejected.
        assert len(r.validate_dag(r.STEPS, r.COMMITTED_INPUTS, parallel=1)) == len(
            r.STEPS
        )
        with pytest.raises(r.DAGError) as excinfo:
            r.validate_dag(r.STEPS, r.COMMITTED_INPUTS, parallel=2)
        assert "*_peep.json" in str(excinfo.value)
        assert "--parallel" in str(excinfo.value)

    # -- overlap predicate unit checks ----------------------------------

    def test_glob_overlap_predicate(self) -> None:
        # Identical globs overlap.
        assert r._globs_can_overlap("*_peep.json", "*_peep.json")
        # A concrete path under a glob overlaps the glob.
        assert r._globs_can_overlap("*_peep.json", "foo_peep.json")
        assert r._globs_can_overlap("foo_peep.json", "*_peep.json")
        # Two `**`-style globs that clearly intersect overlap.
        assert r._globs_can_overlap(
            "regulations/**/*_peep.json", "regulations/**/*_peep.json"
        )
        # The root *_peep.json glob has an empty fixed prefix, which (being
        # the repo-root) is treated as containing everything — combined with
        # identical *_peep.json basenames, this conservatively overlaps the
        # regulations/**/*_peep.json glob ("when in doubt, treat as
        # overlapping"). That is exactly why --parallel is rejected for the
        # real DAG: classify_eurovoc.py writes both *_peep.json AND
        # regulations/**/*_peep.json while extract_court_provision_links.py
        # writes *_peep.json, with no dependency between them.
        assert r._globs_can_overlap("*_peep.json", "regulations/**/*_peep.json")
        # Nested fixed prefixes with compatible basenames overlap.
        assert r._globs_can_overlap("a/**/x.json", "a/b/x.json")
        # Disjoint, sibling fixed prefixes (neither contains the other) do
        # NOT overlap even with identical basenames.
        assert r._globs_can_overlap("a/**/x.json", "b/**/x.json") is False
        # Different basenames in the same directory do NOT overlap.
        assert r._globs_can_overlap("*.json", "*.txt") is False
        assert r._globs_can_overlap("a/foo.json", "a/bar.json") is False
        # Disjoint concrete paths do not overlap.
        assert r._globs_can_overlap("a_only.json", "b_only.json") is False

    # -- end-to-end via main() ------------------------------------------

    def test_main_exits_2_for_parallel_gt_1_against_real_dag(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # subprocess.run must never be reached — we should bail at DAG
        # validation before any work starts.
        monkeypatch.setattr(
            r.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no exec")),
        )
        exc = _run_main(monkeypatch, "--release", "--parallel", "2", "--dry-run")
        assert exc.code == 2
        err = capsys.readouterr().err
        assert "--parallel" in err
        assert "*_peep.json" in err

    def test_main_parallel_1_against_real_dag_is_accepted(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            r.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no exec")),
        )
        exc = _run_main(monkeypatch, "--release", "--parallel", "1", "--dry-run")
        assert exc.code == 0


# ===========================================================================
# 3. --dry-run --release prints the plan and runs nothing
# ===========================================================================


class TestDryRunRelease:
    def test_dry_run_release_runs_no_subprocess(
        self,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def boom(*a, **k):
            raise AssertionError("subprocess.run must not run in --dry-run")

        monkeypatch.setattr(r.subprocess, "run", boom)
        exc = _run_main(monkeypatch, "--release", "--dry-run")
        assert exc.code == 0  # a dry-run is a successful preview
        out = capsys.readouterr().out
        # Topo order printed.
        assert "Step DAG (topological order):" in out
        assert "extract_cross_references.py" in out
        # The three release validators are advertised.
        assert "validate_all.py" in out
        assert "shacl_validate_all.py --all" in out
        assert "validate_seadusloome_sync.py" in out
        # No release manifest is written by a dry-run.
        assert not (r.MANIFEST_DIR / "release_manifest.json").exists()

    def test_dry_run_release_validate_only_lists_validators_only(
        self,
        fake_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            r.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no exec")),
        )
        exc = _run_main(monkeypatch, "--release", "--validate-only", "--dry-run")
        assert exc.code == 0
        out = capsys.readouterr().out
        assert "--validate-only skips all generation steps" in out
        assert "validate_seadusloome_sync.py" in out
        assert not (r.MANIFEST_DIR / "release_manifest.json").exists()


# ===========================================================================
# 4. --release --validate-only runs the three validators, no generation step
# ===========================================================================


class TestValidateOnly:
    def test_validate_only_runs_three_validators_no_generation(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        invoked: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            invoked.append(list(cmd))
            stdout = kwargs.get("stdout")
            # Emit plausible validator output so metric parsing has something.
            if stdout is not None and hasattr(stdout, "write"):
                script = Path(cmd[1]).name if len(cmd) > 1 else ""
                if script == "validate_all.py":
                    stdout.write("Files validated: 700\nErrors: 0\nWarnings: 0\n")
                elif script == "shacl_validate_all.py":
                    stdout.write("Loading 5 files from all buckets...\n"
                                 "Loaded 29000 triples. Running pyshacl...\n"
                                 "SHACL PASS\n")
                elif script == "validate_seadusloome_sync.py":
                    stdout.write("Loaded 30000 data triples; 200 shape triples "
                                 "from 3 shapes file(s).\nSHACL conformity: PASS\n"
                                 "Severity totals: (none)\n")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(r.subprocess, "run", fake_run)
        exc = _run_main(monkeypatch, "--release", "--validate-only")
        assert exc.code == 0

        # Exactly the three release validators ran — no generation script.
        scripts_called = {Path(c[1]).name for c in invoked if len(c) > 1}
        assert scripts_called == {
            "validate_all.py",
            "shacl_validate_all.py",
            "validate_seadusloome_sync.py",
        }
        for step in r.STEPS:
            assert step["script"] not in scripts_called

        manifest = json.loads(
            (r.MANIFEST_DIR / "release_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["mode"] == "release-validate-only"
        assert manifest["releaseOk"] is True
        # Validator section present with all three, each passed.
        names = {v["name"] for v in manifest["validators"]}
        assert names == {
            "validate_all", "shacl_validate_all", "validate_seadusloome_sync"
        }
        assert all(v["passed"] for v in manifest["validators"])
        # No generation step ledger in validate-only mode.
        assert manifest["stepLedger"] == []
        # Metrics were scraped from the (fake) logs.
        by_name = {v["name"]: v for v in manifest["validators"]}
        assert by_name["validate_all"]["metrics"]["filesValidated"] == 700
        assert by_name["shacl_validate_all"]["metrics"]["shaclConforms"] is True
        assert by_name["validate_seadusloome_sync"]["metrics"]["dataTriples"] == 30000

    def test_validate_only_release_not_ok_when_validator_fails(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **kwargs):
            # The SHACL validator fails (exit 1); the others pass.
            script = Path(cmd[1]).name if len(cmd) > 1 else ""
            code = 1 if script == "shacl_validate_all.py" else 0
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("SHACL FAIL\n" if code else "ok\n")
            return subprocess.CompletedProcess(args=cmd, returncode=code)

        monkeypatch.setattr(r.subprocess, "run", fake_run)
        exc = _run_main(monkeypatch, "--release", "--validate-only")
        assert exc.code == 1
        manifest = json.loads(
            (r.MANIFEST_DIR / "release_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["releaseOk"] is False
        assert manifest["validatorsPassed"] is False
        failed = [v for v in manifest["validators"] if not v["passed"]]
        assert {v["name"] for v in failed} == {"shacl_validate_all"}


# ===========================================================================
# 5. release_manifest.json shape (full --release build, all stubs succeed)
# ===========================================================================


class TestReleaseManifestShape:
    def test_full_release_manifest_has_expected_sections(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every step and validator subprocess succeeds.
        def fake_run(cmd, **kwargs):
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("ok\n")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(r.subprocess, "run", fake_run)
        # snapshot/restore poke the real fs; redirect them to no-ops since the
        # tmp KRR_DIR is tiny and we don't exercise rollback here.
        monkeypatch.setattr(r, "snapshot_outputs", lambda: r.KRR_DIR.parent /
                            f"{r.KRR_DIR.name}.bak.x")
        monkeypatch.setattr(r, "restore_outputs", lambda backup: None)
        monkeypatch.setattr(r, "cleanup_snapshot", lambda backup: None)

        exc = _run_main(monkeypatch, "--release")
        assert exc.code == 0

        manifest = json.loads(
            (r.MANIFEST_DIR / "release_manifest.json").read_text(encoding="utf-8")
        )
        # Top-level keys.
        for key in ("generated", "mode", "dag", "stepLedger", "stepSummary",
                    "validators", "validatorsPassed", "releaseArtifacts",
                    "releaseOk"):
            assert key in manifest, key
        assert manifest["mode"] == "release-build"
        # DAG section: topo order + per-step reads/writes/depends.
        assert manifest["dag"]["topoOrder"][0] == "extract_cross_references.py"
        # build_release_artifacts.py rebuilds combined_ontology.jsonld last
        # (issue #252 / #467).
        assert manifest["dag"]["topoOrder"][-1] == "build_release_artifacts.py"
        assert len(manifest["dag"]["steps"]) == len(r.STEPS)
        sample = manifest["dag"]["steps"][0]
        assert {"name", "dependsOn", "writes", "reads"} <= set(sample)
        # Step ledger: one entry per step, all succeeded.
        assert len(manifest["stepLedger"]) == len(r.STEPS)
        assert all(e["status"] == "succeeded" for e in manifest["stepLedger"])
        assert manifest["stepSummary"]["succeeded"] == len(r.STEPS)
        assert manifest["stepSummary"]["failed"] == 0
        # Validators: all three, all passed.
        assert {v["name"] for v in manifest["validators"]} == {
            "validate_all", "shacl_validate_all", "validate_seadusloome_sync"
        }
        assert manifest["validatorsPassed"] is True
        # Artifact content hash over the committed release surface.
        arts = manifest["releaseArtifacts"]
        assert isinstance(arts["contentHash"], str) and len(arts["contentHash"]) == 64
        assert "krr_outputs/combined_ontology.jsonld" in arts["files"]
        assert "metadata.jsonld" in arts["files"]
        assert arts["missing"] == []
        # Overall verdict.
        assert manifest["releaseOk"] is True

    def test_artifact_hash_changes_when_artifact_changes(
        self, fake_repo: Path
    ) -> None:
        h1 = r.hash_release_artifacts()
        (r.KRR_DIR / "combined_ontology.jsonld").write_text(
            '{"@context": {}, "@graph": [{"@id": "x"}]}', encoding="utf-8"
        )
        h2 = r.hash_release_artifacts()
        assert h1["contentHash"] != h2["contentHash"]
        assert h1["files"]["krr_outputs/combined_ontology.jsonld"] != (
            h2["files"]["krr_outputs/combined_ontology.jsonld"]
        )

    def test_artifact_hash_reports_missing(
        self, fake_repo: Path
    ) -> None:
        (r.KRR_DIR / "combined_ontology.jsonld").unlink()
        h = r.hash_release_artifacts()
        assert "krr_outputs/combined_ontology.jsonld" in h["missing"]


# ===========================================================================
# 6. release_ok is False if a step or validator fails
# ===========================================================================


class TestReleaseOkOnFailure:
    def test_release_not_ok_when_a_generation_step_fails(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The second enrichment step fails; everything else would pass.
        failing_step = r.STEPS[1]["script"]

        def fake_run(cmd, **kwargs):
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("output\n")
            script = Path(cmd[1]).name if len(cmd) > 1 else ""
            code = 1 if script == failing_step else 0
            return subprocess.CompletedProcess(args=cmd, returncode=code)

        monkeypatch.setattr(r.subprocess, "run", fake_run)
        monkeypatch.setattr(r, "snapshot_outputs", lambda: r.KRR_DIR.parent /
                            f"{r.KRR_DIR.name}.bak.x")
        monkeypatch.setattr(r, "restore_outputs", lambda backup: None)
        monkeypatch.setattr(r, "cleanup_snapshot", lambda backup: None)

        exc = _run_main(monkeypatch, "--release")
        assert exc.code == 1
        manifest = json.loads(
            (r.MANIFEST_DIR / "release_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["releaseOk"] is False
        # The failing step is recorded.
        ledger_by_name = {e["name"]: e for e in manifest["stepLedger"]
                          if "status" in e}
        assert ledger_by_name[r.STEPS[1]["name"]]["status"] == "failed"
        # Release validators were skipped because generation failed.
        assert all(v["status"] == "skipped_steps_failed"
                   for v in manifest["validators"])
        assert manifest["validatorsPassed"] is False

    def test_release_not_ok_when_a_validator_fails(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # All generation steps pass; validate_seadusloome_sync fails.
        def fake_run(cmd, **kwargs):
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("output\n")
            script = Path(cmd[1]).name if len(cmd) > 1 else ""
            code = 1 if script == "validate_seadusloome_sync.py" else 0
            return subprocess.CompletedProcess(args=cmd, returncode=code)

        monkeypatch.setattr(r.subprocess, "run", fake_run)
        monkeypatch.setattr(r, "snapshot_outputs", lambda: r.KRR_DIR.parent /
                            f"{r.KRR_DIR.name}.bak.x")
        monkeypatch.setattr(r, "restore_outputs", lambda backup: None)
        monkeypatch.setattr(r, "cleanup_snapshot", lambda backup: None)

        exc = _run_main(monkeypatch, "--release")
        assert exc.code == 1
        manifest = json.loads(
            (r.MANIFEST_DIR / "release_manifest.json").read_text(encoding="utf-8")
        )
        # All steps OK, but a validator failed → release not ok.
        assert manifest["stepSummary"]["failed"] == 0
        assert manifest["validatorsPassed"] is False
        assert manifest["releaseOk"] is False
        failed = [v for v in manifest["validators"] if not v["passed"]]
        assert {v["name"] for v in failed} == {"validate_seadusloome_sync"}


# ===========================================================================
# 6b. releaseOk also requires a complete release surface (no missing artifacts)
# ===========================================================================


class TestReleaseOkRequiresArtifactsPresent:
    """``releaseOk`` must be ``False`` when any ``RELEASE_ARTIFACTS`` entry is
    absent on disk — even if every step and validator passed. ``validate_all``
    only *warns* on a missing ``metadata.jsonld``, so this gate is what
    actually fails such a release. The ``missing`` list stays in the manifest
    so the failure is diagnosable."""

    @staticmethod
    def _all_green_run(monkeypatch: pytest.MonkeyPatch) -> None:
        """Monkeypatch every step + validator subprocess to succeed, and
        no-op the snapshot/restore (the tmp KRR_DIR is tiny)."""
        def fake_run(cmd, **kwargs):
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("ok\n")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(r.subprocess, "run", fake_run)
        monkeypatch.setattr(r, "snapshot_outputs", lambda: r.KRR_DIR.parent /
                            f"{r.KRR_DIR.name}.bak.x")
        monkeypatch.setattr(r, "restore_outputs", lambda backup: None)
        monkeypatch.setattr(r, "cleanup_snapshot", lambda backup: None)

    def test_release_not_ok_when_a_release_artifact_is_missing(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Remove a release-surface artifact (metadata.jsonld lives at repo
        # root); everything else is green.
        (fake_repo / "metadata.jsonld").unlink()
        self._all_green_run(monkeypatch)

        exc = _run_main(monkeypatch, "--release")
        assert exc.code != 0  # missing release surface fails the build

        manifest = json.loads(
            (r.MANIFEST_DIR / "release_manifest.json").read_text(encoding="utf-8")
        )
        # Steps + validators all passed, but the artifact is missing →
        # releaseOk is False.
        assert manifest["stepSummary"]["failed"] == 0
        assert manifest["validatorsPassed"] is True
        assert manifest["releaseOk"] is False
        # The missing list is recorded so the failure is diagnosable.
        assert "metadata.jsonld" in manifest["releaseArtifacts"]["missing"]

    def test_release_ok_when_all_release_artifacts_present(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # fake_repo seeds every RELEASE_ARTIFACTS entry — the green run
        # should exit 0 with releaseOk True and an empty missing list.
        self._all_green_run(monkeypatch)

        exc = _run_main(monkeypatch, "--release")
        assert exc.code == 0

        manifest = json.loads(
            (r.MANIFEST_DIR / "release_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["releaseArtifacts"]["missing"] == []
        assert manifest["releaseOk"] is True

    def test_validate_only_release_not_ok_when_artifact_missing(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # --validate-only also hashes the release surface; a missing artifact
        # must drop releaseOk to False there too.
        (r.KRR_DIR / "combined_ontology.jsonld").unlink()

        def fake_run(cmd, **kwargs):
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("ok\n")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(r.subprocess, "run", fake_run)
        exc = _run_main(monkeypatch, "--release", "--validate-only")
        assert exc.code != 0
        manifest = json.loads(
            (r.MANIFEST_DIR / "release_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["validatorsPassed"] is True
        assert manifest["releaseOk"] is False
        assert "krr_outputs/combined_ontology.jsonld" in (
            manifest["releaseArtifacts"]["missing"]
        )


# ===========================================================================
# 7. CLI guard rails
# ===========================================================================


class TestCLIGuards:
    def test_validate_only_requires_release(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exc = _run_main(monkeypatch, "--validate-only")
        assert exc.code == 2

    def test_parallel_incompatible_with_validate_each(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exc = _run_main(monkeypatch, "--parallel", "2", "--validate-each")
        assert exc.code == 2
