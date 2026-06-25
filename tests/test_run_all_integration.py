"""Tests for ``scripts/run_all_integration.py``.

Covers the three review findings on commit ``e0717778b``:

  1. Atomic snapshot/restore semantics for ``krr_outputs/`` (P1).
  2. Validator subprocess gets a timeout + log capture (P2).
  3. Dry-run summary reports ``Planned: N`` instead of ``Succeeded: 0`` (P3).

These tests never touch the real ``krr_outputs/`` tree — they monkeypatch
``KRR_DIR``/``MANIFEST_DIR`` to ``tmp_path``-relative locations, and patch
``subprocess.run`` so no integration scripts or validators ever execute.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable
from unittest import mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_all_integration  # noqa: E402  (import after sys.path mutation)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _write_tree(root: Path, files: Iterable[tuple[str, str]]) -> None:
    """Create ``root`` and write each (rel_path, content) pair."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _read_tree(root: Path) -> dict[str, str]:
    """Read every file under ``root`` as a {rel_path: content} mapping."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
    return out


@pytest.fixture
def fake_krr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect KRR_DIR/MANIFEST_DIR to a tmp tree and seed canonical content."""
    krr = tmp_path / "krr_outputs"
    manifest_dir = krr / "reports" / "integration"
    _write_tree(
        krr,
        [
            ("laws/foo_peep.json", '{"@id": "estleg:Foo"}'),
            ("regulations/bar_peep.json", '{"@id": "estleg:Bar"}'),
            ("nested/deep/baz.txt", "canonical"),
        ],
    )
    monkeypatch.setattr(run_all_integration, "KRR_DIR", krr, raising=True)
    monkeypatch.setattr(
        run_all_integration, "MANIFEST_DIR", manifest_dir, raising=True
    )
    return krr


# ---------------------------------------------------------------------------
# Finding 1 — atomic snapshot / restore
# ---------------------------------------------------------------------------


class TestSnapshotRestoreAtomicity:
    """The snapshot/restore pair must keep the canonical tree recoverable
    at every interruption point — never lose the only good copy."""

    def test_snapshot_creates_sibling_backup_and_writable_krr(
        self, fake_krr: Path
    ) -> None:
        before = _read_tree(fake_krr)
        backup = run_all_integration.snapshot_outputs()
        try:
            assert backup.exists(), "backup directory must exist after snapshot"
            assert backup.parent == fake_krr.parent, (
                "backup must be a sibling of KRR_DIR for the rename to be atomic"
            )
            assert backup.name.startswith(fake_krr.name + ".bak."), backup.name
            assert fake_krr.exists(), (
                "KRR_DIR must remain present after snapshot so the run can mutate it"
            )
            # Both trees should be identical immediately after snapshot.
            assert _read_tree(fake_krr) == before
            assert _read_tree(backup) == before
        finally:
            run_all_integration.cleanup_snapshot(backup)

    def test_restore_recovers_original_after_mutation(
        self, fake_krr: Path
    ) -> None:
        before = _read_tree(fake_krr)
        backup = run_all_integration.snapshot_outputs()

        # Simulate a pipeline run that mutates the live tree: change a file,
        # add a new one, and delete one.
        (fake_krr / "laws" / "foo_peep.json").write_text(
            '{"@id": "estleg:Mutated"}', encoding="utf-8"
        )
        (fake_krr / "laws" / "new_run_artifact.json").write_text(
            "partial", encoding="utf-8"
        )
        (fake_krr / "regulations" / "bar_peep.json").unlink()

        run_all_integration.restore_outputs(backup)

        assert _read_tree(fake_krr) == before, (
            "restore must reinstate every file from the snapshot"
        )
        assert not backup.exists(), "consumed backup must not linger"

    def test_snapshot_has_known_path_so_sigint_leaves_recoverable_copy(
        self, fake_krr: Path
    ) -> None:
        before = _read_tree(fake_krr)
        backup = run_all_integration.snapshot_outputs()
        try:
            # Mutate KRR_DIR as a partial run would; then assert that even if
            # the process is interrupted before restore, the original is still
            # recoverable from the backup path.
            (fake_krr / "laws" / "foo_peep.json").write_text(
                "corrupted", encoding="utf-8"
            )
            assert _read_tree(backup) == before, (
                "snapshot path must hold an untouched copy of the original tree"
            )
        finally:
            run_all_integration.cleanup_snapshot(backup)

    def test_restore_rolls_back_on_failure_keeping_canonical_path_populated(
        self, fake_krr: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the inner rename (backup -> KRR_DIR) fails mid-restore, the
        rollback-the-rollback path must put a tree back at KRR_DIR. The
        invariant is: KRR_DIR is never empty at any observable moment."""

        backup = run_all_integration.snapshot_outputs()
        # Simulate a partial-run mutation so KRR_DIR differs from backup.
        (fake_krr / "laws" / "foo_peep.json").write_text(
            "partial", encoding="utf-8"
        )

        original_move = shutil.move
        call_state = {"count": 0}

        def flaky_move(src: str, dst: str, *args, **kwargs):
            # First call: KRR_DIR -> failed_run (succeed).
            # Second call: backup -> KRR_DIR (raise to simulate SIGINT/IOError).
            # Third call: failed_run -> KRR_DIR (rollback-the-rollback, succeed).
            call_state["count"] += 1
            if call_state["count"] == 2:
                raise OSError("simulated mid-restore failure")
            return original_move(src, dst, *args, **kwargs)

        monkeypatch.setattr(shutil, "move", flaky_move)

        with pytest.raises(OSError, match="simulated mid-restore failure"):
            run_all_integration.restore_outputs(backup)

        # After the failed restore, KRR_DIR must still contain *something* —
        # the partial-run tree is preferable to nothing.
        assert fake_krr.exists()
        assert (fake_krr / "laws" / "foo_peep.json").read_text(
            encoding="utf-8"
        ) == "partial", "rollback-the-rollback must reinstate the partial tree"
        # Backup remains on disk — recoverable by hand if needed.
        assert backup.exists()

    def test_cleanup_snapshot_removes_backup(self, fake_krr: Path) -> None:
        backup = run_all_integration.snapshot_outputs()
        assert backup.exists()
        run_all_integration.cleanup_snapshot(backup)
        assert not backup.exists()

    def test_snapshot_overwrites_stale_backup_at_same_path(
        self, fake_krr: Path
    ) -> None:
        """If a previous run with the same PID left a backup behind (rare but
        possible after a crash), the new snapshot must clear it rather than
        fail with FileExistsError."""

        stale = fake_krr.parent / f"{fake_krr.name}.bak.{os.getpid()}"
        stale.mkdir(parents=True)
        (stale / "stale.txt").write_text("old", encoding="utf-8")

        backup = run_all_integration.snapshot_outputs()
        try:
            assert backup == stale
            assert not (backup / "stale.txt").exists(), (
                "stale backup must be cleared before the new snapshot is taken"
            )
            assert (backup / "laws" / "foo_peep.json").exists()
        finally:
            run_all_integration.cleanup_snapshot(backup)


# ---------------------------------------------------------------------------
# Finding 2 — validator subprocess gets timeout + log capture
# ---------------------------------------------------------------------------


class TestRunValidatorTimeoutAndLogging:
    def test_run_validator_passes_timeout_and_captures_output(
        self, fake_krr: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["timeout"] = kwargs.get("timeout")
            captured["stdout"] = kwargs.get("stdout")
            captured["stderr"] = kwargs.get("stderr")
            captured["cwd"] = kwargs.get("cwd")
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("validator stdout line\n")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(run_all_integration.subprocess, "run", fake_run)

        exit_code, log_path = run_all_integration.run_validator(
            timeout=42, after_script="extract_cross_references.py"
        )

        assert exit_code == 0
        assert captured["timeout"] == 42, "timeout must be forwarded to subprocess.run"
        assert captured["stderr"] == subprocess.STDOUT, (
            "stderr must be merged into stdout so the log captures everything"
        )
        # The stdout fd handed to subprocess.run must be a writable file
        # object — that is how the output gets tee'd into the log.
        stdout_arg = captured["stdout"]
        assert stdout_arg is not None
        assert hasattr(stdout_arg, "write")

        # Log file must live under MANIFEST_DIR/logs and follow the
        # validate_<stem>.log naming so manifest entries can reference it.
        assert log_path.parent == run_all_integration.MANIFEST_DIR / "logs"
        assert log_path.name == "validate_extract_cross_references.log"
        assert log_path.exists()
        body = log_path.read_text(encoding="utf-8")
        assert "validate_all.py started" in body
        assert "after=extract_cross_references.py" in body
        assert "validator stdout line" in body

    def test_run_validator_records_timeout_as_exit_124(
        self, fake_krr: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

        monkeypatch.setattr(run_all_integration.subprocess, "run", fake_run)

        exit_code, log_path = run_all_integration.run_validator(
            timeout=5, after_script="generate_inverse_references.py"
        )

        assert exit_code == 124, "timeout must surface as exit code 124"
        assert log_path.exists()
        body = log_path.read_text(encoding="utf-8")
        assert "TIMEOUT after 5 seconds" in body

    def test_run_validator_default_stem_when_no_script_provided(
        self, fake_krr: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            run_all_integration.subprocess,
            "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0),
        )

        exit_code, log_path = run_all_integration.run_validator(timeout=10)

        assert exit_code == 0
        assert log_path.name == "validate_phase.log", (
            "phase-level validator logs must use the 'phase' stem"
        )


# ---------------------------------------------------------------------------
# Finding 3 — dry-run summary
# ---------------------------------------------------------------------------


class TestDryRunSummary:
    """Dry-run must announce the plan, never invoke subprocesses, and emit
    a ``Planned: N`` line instead of a misleading ``Succeeded: 0``."""

    def _run_main_dry(
        self,
        fake_krr: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        argv_extra: list[str] | None = None,
    ) -> str:
        # Block any subprocess invocation — dry-run must never execute scripts.
        def boom(*args, **kwargs):
            raise AssertionError(
                f"subprocess.run must not be called in --dry-run mode (got {args!r})"
            )

        monkeypatch.setattr(run_all_integration.subprocess, "run", boom)

        # Force argv so parse_args sees only --dry-run.
        argv = ["run_all_integration.py", "--dry-run"]
        if argv_extra:
            argv.extend(argv_extra)
        monkeypatch.setattr(sys, "argv", argv)

        run_all_integration.main()
        return capsys.readouterr().out

    def test_dry_run_reports_planned_not_succeeded_zero(
        self,
        fake_krr: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = self._run_main_dry(fake_krr, monkeypatch, capsys)

        expected_planned = len(run_all_integration.PIPELINE)
        assert f"Planned:   {expected_planned}" in out, out
        # The misleading "Succeeded: 0" line must NOT appear in dry-run.
        assert "Succeeded: 0" not in out, out
        assert "Failed:" not in out, (
            "dry-run summary must omit Failed: which is meaningless without execution"
        )

    def test_dry_run_emits_banner_at_start_and_end(
        self,
        fake_krr: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = self._run_main_dry(fake_krr, monkeypatch, capsys)

        # Start banner.
        assert "[DRY-RUN] No scripts will be executed." in out
        # End banner — both the heading line and the trailing reminder.
        assert "[DRY-RUN] PIPELINE PLAN COMPLETE" in out
        assert "[DRY-RUN] No scripts were executed." in out

        # Sanity: the start banner must precede the end banner.
        start_idx = out.index("[DRY-RUN] No scripts will be executed.")
        end_idx = out.index("[DRY-RUN] PIPELINE PLAN COMPLETE")
        assert start_idx < end_idx

    def test_dry_run_does_not_create_snapshot(
        self,
        fake_krr: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # If snapshot_outputs is invoked in dry-run mode that's a regression —
        # we have nothing to roll back from.
        sentinel = io.StringIO()

        def fail_snapshot() -> Path:
            sentinel.write("snapshot_outputs called in dry-run\n")
            raise AssertionError("snapshot_outputs must not run in --dry-run mode")

        monkeypatch.setattr(run_all_integration, "snapshot_outputs", fail_snapshot)

        self._run_main_dry(fake_krr, monkeypatch, capsys)

        assert sentinel.getvalue() == "", sentinel.getvalue()

    def test_dry_run_does_not_write_manifest(
        self,
        fake_krr: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._run_main_dry(fake_krr, monkeypatch, capsys)

        manifest_path = (
            run_all_integration.MANIFEST_DIR / "latest_pipeline_manifest.json"
        )
        assert not manifest_path.exists(), (
            "dry-run must not persist a manifest (the actual run will)"
        )


# ---------------------------------------------------------------------------
# Finding #252 — combined_ontology.jsonld rebuild is a mandatory DAG step
# ---------------------------------------------------------------------------


class TestCombinedOntologyRebuildStep:
    """``fix_all_issues.py`` must be a DAG step that rebuilds
    ``combined_ontology.jsonld`` (+ ``INDEX.json``) AFTER every enrichment
    step and BEFORE the release validators. Without it the release artifact
    and the Seadusloome sync gate validate the pre-enrichment corpus."""

    REBUILD_STEP = "fix_all_issues.py"

    def _step(self, name: str) -> dict:
        return next(s for s in run_all_integration.STEPS if s["name"] == name)

    def test_rebuild_step_is_registered(self) -> None:
        names = [s["name"] for s in run_all_integration.STEPS]
        assert self.REBUILD_STEP in names, (
            "fix_all_issues.py must be registered as a DAG step so the "
            "combined ontology is rebuilt after enrichment"
        )

    def test_rebuild_step_writes_combined_ontology_and_index(self) -> None:
        writes = self._step(self.REBUILD_STEP)["writes"]
        assert "combined_ontology.jsonld" in writes
        assert "INDEX.json" in writes

    def test_rebuild_step_reads_the_enriched_peeps(self) -> None:
        reads = self._step(self.REBUILD_STEP)["reads"]
        # It consumes the (now-enriched) root peeps that the prior steps
        # rewrote — that is the whole point of running it last.
        assert "*_peep.json" in reads

    def test_rebuild_step_depends_on_all_enrichment_steps(self) -> None:
        enrichment = [
            s["name"]
            for s in run_all_integration.STEPS
            if s["name"] != self.REBUILD_STEP
        ]
        deps = set(self._step(self.REBUILD_STEP)["depends_on"])
        missing = set(enrichment) - deps
        assert not missing, (
            f"rebuild step must depend on every enrichment step; missing: "
            f"{sorted(missing)}"
        )

    def test_rebuild_step_topo_sorts_strictly_last(self) -> None:
        topo = run_all_integration.validate_dag(
            run_all_integration.STEPS, run_all_integration.COMMITTED_INPUTS
        )
        assert topo[-1] == self.REBUILD_STEP, (
            "the combined-ontology rebuild must be the final step so it runs "
            "after all enrichment and before the release validators"
        )

    def test_rebuild_runs_before_release_validators(self) -> None:
        """The release validators run from ``main()`` only after ``run_dag``
        returns, so being the last DAG node already orders the rebuild before
        them. Guard that the validator set still parses the combined artifact
        the rebuild produces (the Seadusloome gate)."""
        validator_names = {v["name"] for v in run_all_integration.RELEASE_VALIDATORS}
        assert "validate_seadusloome_sync" in validator_names
        # combined_ontology.jsonld is a hashed release artifact: the rebuild's
        # output is exactly what the release surface ships.
        assert any(
            a.endswith("combined_ontology.jsonld")
            for a in run_all_integration.RELEASE_ARTIFACTS
        )

    def test_dag_still_valid_and_acyclic_with_rebuild_node(self) -> None:
        # validate_dag raises DAGError on a cycle / dangling dep / uncovered
        # read; a clean return proves the new node integrates correctly.
        topo = run_all_integration.validate_dag(
            run_all_integration.STEPS, run_all_integration.COMMITTED_INPUTS
        )
        assert len(topo) == len(run_all_integration.STEPS)
        assert sorted(topo) == sorted(
            s["name"] for s in run_all_integration.STEPS
        )

    def test_parallel_still_rejected_with_rebuild_node(self) -> None:
        # The rebuild depends on every other step, so it is never
        # concurrent-eligible; the pre-existing *_peep.json write overlaps
        # among the enrichment steps must still make --parallel > 1 unsafe.
        with pytest.raises(run_all_integration.DAGError):
            run_all_integration.validate_dag(
                run_all_integration.STEPS,
                run_all_integration.COMMITTED_INPUTS,
                parallel=2,
            )


# ---------------------------------------------------------------------------
# Finding #262 — temporalStatus must be evaluated against a declared date
# ---------------------------------------------------------------------------


class TestTemporalEvaluationDateIsDeclared:
    """``extract_temporal_data.py`` must be invoked with an explicit
    ``--evaluation-date`` so ``temporalStatus`` is deterministic across runs
    (it otherwise falls back to ``date.today()``)."""

    TEMPORAL_STEP = "extract_temporal_data.py"

    def _step(self, name: str) -> dict:
        return next(s for s in run_all_integration.STEPS if s["name"] == name)

    def test_build_evaluation_date_constant_is_a_valid_iso_date(self) -> None:
        from datetime import date

        value = run_all_integration.BUILD_EVALUATION_DATE
        assert isinstance(value, str) and value, "constant must be a non-empty str"
        # Round-trips through date.fromisoformat → it is a real YYYY-MM-DD date.
        assert date.fromisoformat(value).isoformat() == value

    def test_temporal_step_declares_explicit_evaluation_date(self) -> None:
        args = self._step(self.TEMPORAL_STEP).get("args", [])
        assert "--evaluation-date" in args, (
            "temporal step must pass --evaluation-date for determinism (#262)"
        )
        idx = args.index("--evaluation-date")
        assert args[idx + 1] == run_all_integration.BUILD_EVALUATION_DATE

    def test_temporal_step_command_includes_evaluation_date(self) -> None:
        cmd = run_all_integration.step_command(self._step(self.TEMPORAL_STEP))
        assert "--evaluation-date" in cmd, (
            "the resolved argv for the temporal step must carry the flag"
        )
        idx = cmd.index("--evaluation-date")
        assert cmd[idx + 1] == run_all_integration.BUILD_EVALUATION_DATE
        # The flag/value pair must come after the script path.
        assert cmd[idx - 1].endswith(self.TEMPORAL_STEP)

    def test_step_command_appends_declared_args_generically(self) -> None:
        # step_command must append a step's declarative ``args`` after the
        # script path (the mechanism the temporal step relies on).
        step = {"script": "demo.py", "args": ["--foo", "bar"]}
        cmd = run_all_integration.step_command(step)
        assert cmd[-2:] == ["--foo", "bar"]
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("demo.py")

    def test_explicit_command_overrides_args(self) -> None:
        # An explicit ``command`` wins outright; ``args`` is ignored.
        step = {"command": ["echo", "hi"], "args": ["--ignored"]}
        assert run_all_integration.step_command(step) == ["echo", "hi"]

    def test_no_step_other_than_temporal_passes_evaluation_date(self) -> None:
        # Spot-guard against the flag leaking onto an unrelated step.
        for s in run_all_integration.STEPS:
            if s["name"] == self.TEMPORAL_STEP:
                continue
            assert "--evaluation-date" not in s.get("args", [])


# ---------------------------------------------------------------------------
# Finding #341.7 — serial break-on-failure must drain the unreached tail
# ---------------------------------------------------------------------------


class TestSerialBreakOnFailureDrainsTail:
    """When a step fails in the SERIAL path, ``run_dag`` used to ``break``
    without recording the later (unexecuted) steps, leaving the manifest's
    stepLedger / stepSummary incomplete: ``succeeded+failed+skipped <
    totalSteps``. The drain must append every unreached step to the ledger as
    ``status="not_reached"`` (mirroring the parallel path's blocked-drain) and
    add it to ``skipped``+``failed`` so the ledger is complete and
    ``release_ok`` stays False."""

    def _independent_dag(self, n: int) -> list[dict]:
        """``n`` standalone steps with NO dependencies among them.

        Topo order equals source order, and because no later step depends on
        an earlier one, none of the post-failure steps are ``blocked`` — they
        are exactly the ``not_reached`` tail the fix must record (the steps the
        old ``break`` silently dropped)."""
        return [
            {"name": f"s{i}.py", "description": f"step {i}",
             "script": f"s{i}.py", "depends_on": [], "reads": [], "writes": []}
            for i in range(1, n + 1)
        ]

    @pytest.fixture
    def stub_scripts_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Path:
        """Point SCRIPTS_DIR/MANIFEST_DIR/REPO_ROOT at a tmp tree with stub
        step scripts present on disk (so ``_missing_script`` is False)."""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        for i in range(1, 6):
            (scripts / f"s{i}.py").write_text("# stub\n", encoding="utf-8")
        manifest_dir = tmp_path / "krr_outputs" / "reports" / "integration"
        manifest_dir.mkdir(parents=True)
        monkeypatch.setattr(run_all_integration, "SCRIPTS_DIR", scripts,
                            raising=True)
        monkeypatch.setattr(run_all_integration, "MANIFEST_DIR", manifest_dir,
                            raising=True)
        monkeypatch.setattr(run_all_integration, "REPO_ROOT", tmp_path,
                            raising=True)
        return scripts

    def _fail_on(
        self, fail_script: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Patch subprocess.run so ``fail_script`` exits 1, others exit 0."""

        def fake_run(cmd, **kwargs):
            name = Path(cmd[1]).name
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("out\n")
            return subprocess.CompletedProcess(
                args=cmd, returncode=1 if name == fail_script else 0
            )

        monkeypatch.setattr(run_all_integration.subprocess, "run", fake_run)

    def test_early_failure_records_remaining_steps_as_not_reached(
        self, stub_scripts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steps = self._independent_dag(4)
        topo = run_all_integration.validate_dag(steps, ())
        assert topo == ["s1.py", "s2.py", "s3.py", "s4.py"]
        # The very first step fails; s2/s3/s4 are independent of it, so the
        # old code would break and drop them entirely.
        self._fail_on("s1.py", monkeypatch)

        res = run_all_integration.run_dag(
            steps, topo, dry_run=False, resume_from=None,
            validate_each=False, per_script_timeout=30, parallel=1,
        )

        ledger = res["ledger"]
        statuses = {e["name"]: e["status"] for e in ledger}
        # Every later step is recorded as not_reached (not silently dropped).
        assert statuses["s1.py"] == "failed"
        assert statuses["s2.py"] == "not_reached"
        assert statuses["s3.py"] == "not_reached"
        assert statuses["s4.py"] == "not_reached"
        # The ledger is COMPLETE — one entry per step, no duplicates.
        assert len(ledger) == len(topo)
        assert [e["name"] for e in ledger] == topo
        # not_reached entries mirror the parallel blocked-drain shape.
        nr = next(e for e in ledger if e["name"] == "s4.py")
        assert nr["script"] == "s4.py"

    def test_counts_sum_to_total_steps_after_early_failure(
        self, stub_scripts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steps = self._independent_dag(5)
        topo = run_all_integration.validate_dag(steps, ())
        # Fail on the second step: s1 succeeds, s2 fails, s3/s4/s5 not_reached.
        self._fail_on("s2.py", monkeypatch)

        res = run_all_integration.run_dag(
            steps, topo, dry_run=False, resume_from=None,
            validate_each=False, per_script_timeout=30, parallel=1,
        )

        total = len(topo)
        # The ledger partitions cleanly over the topo set: every step appears
        # exactly once, succeeded/failed/not_reached are disjoint and total.
        succeeded_in_ledger = {
            e["name"] for e in res["ledger"] if e["status"] == "succeeded"
        }
        failed_in_ledger = {
            e["name"] for e in res["ledger"] if e["status"] == "failed"
        }
        not_reached_in_ledger = {
            e["name"] for e in res["ledger"] if e["status"] == "not_reached"
        }
        assert succeeded_in_ledger == {"s1.py"}
        assert failed_in_ledger == {"s2.py"}
        assert not_reached_in_ledger == {"s3.py", "s4.py", "s5.py"}
        assert (
            len(succeeded_in_ledger)
            + len(failed_in_ledger)
            + len(not_reached_in_ledger)
        ) == total

    def test_release_manifest_step_summary_is_complete_and_not_ok(
        self, stub_scripts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point: the assembled release manifest's stepSummary now
        accounts for every step (no silent gap), and release_ok stays False."""
        steps = self._independent_dag(4)
        topo = run_all_integration.validate_dag(steps, ())
        self._fail_on("s2.py", monkeypatch)

        res = run_all_integration.run_dag(
            steps, topo, dry_run=False, resume_from=None,
            validate_each=False, per_script_timeout=30, parallel=1,
        )

        # Build the manifest exactly as main() would for a release build.
        monkeypatch.setattr(run_all_integration, "STEPS", steps, raising=True)
        manifest = run_all_integration.build_release_manifest(
            dag_result=res,
            validator_records=[],
            validators_passed=True,  # isolate the steps_ok contribution
            artifact_hash={"files": {}, "missing": [], "contentHash": "x"},
            dry_run=False,
            validate_only=False,
            resume_from=None,
            parallel=1,
        )

        # stepLedger carries one record per step — nothing dropped. This is
        # the authoritative completeness check: before the fix the post-failure
        # tail was absent, so len(stepLedger) < totalSteps.
        assert len(manifest["stepLedger"]) == len(topo)
        ledger_names = [e["name"] for e in manifest["stepLedger"]]
        assert sorted(ledger_names) == sorted(topo)
        assert len(ledger_names) == len(set(ledger_names))  # no duplicates
        summary = manifest["stepSummary"]
        assert summary["totalSteps"] == len(topo)
        # Every step is accounted for in the run's result sets: the union of
        # succeeded/failed/skipped covers the full topo set (the historical bug
        # left the not_reached tail out of all three).
        assert (
            res["succeeded"] | res["failed"] | res["skipped"]
        ) == set(topo)
        # Failure is recorded and the release is correctly blocked.
        assert summary["failed"] >= 1
        assert manifest["releaseOk"] is False

    def test_validation_failure_also_drains_the_tail(
        self, stub_scripts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The second serial break (``--validate-each`` failure) must drain the
        tail too: the step itself ran fine, but the post-step validator failed,
        so later steps are still ``not_reached``."""
        steps = self._independent_dag(3)
        topo = run_all_integration.validate_dag(steps, ())

        # All step subprocesses succeed; the per-step validator fails after s1.
        def all_ok(cmd, **kwargs):
            stdout = kwargs.get("stdout")
            if stdout is not None and hasattr(stdout, "write"):
                stdout.write("out\n")
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(run_all_integration.subprocess, "run", all_ok)
        calls = {"n": 0}

        def fake_validator(timeout, after_script=None):
            calls["n"] += 1
            # Fail the validator that runs after the first step.
            log = run_all_integration.validator_log_path(after_script or "phase")
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("v\n", encoding="utf-8")
            return (1, log)

        monkeypatch.setattr(run_all_integration, "run_validator",
                            fake_validator)

        res = run_all_integration.run_dag(
            steps, topo, dry_run=False, resume_from=None,
            validate_each=True, per_script_timeout=30, parallel=1,
        )

        statuses = {e["name"]: e["status"] for e in res["ledger"]}
        assert statuses["s1.py"] == "validation_failed"
        assert statuses["s2.py"] == "not_reached"
        assert statuses["s3.py"] == "not_reached"
        assert len(res["ledger"]) == len(topo)


# ---------------------------------------------------------------------------
# Finding #603 — --validate-only must reject a STALE combined artifact
# ---------------------------------------------------------------------------


class TestCombinedStalenessGuard:
    """``--release --validate-only`` never rebuilds ``combined_ontology.jsonld``
    / ``INDEX.json``. ``_combined_staleness_error`` is the backstop: if a
    ``*_peep.json`` was enriched since the last combined build, it must report
    staleness so main() fails fast instead of stamping ``releaseOk:true`` on a
    stale combined."""

    def _seed_combined_tree(self, krr: Path) -> tuple[Path, Path, Path]:
        """Write a peep + combined + INDEX and return their paths."""
        peep = krr / "laws" / "foo_peep.json"
        combined = krr / "combined_ontology.jsonld"
        index = krr / "INDEX.json"
        _write_tree(
            krr,
            [
                ("laws/foo_peep.json", '{"@id": "estleg:Foo"}'),
                ("combined_ontology.jsonld", '{"@graph": []}'),
                ("INDEX.json", '{"laws": []}'),
            ],
        )
        return peep, combined, index

    def _set_mtime(self, path: Path, mtime: float) -> None:
        """Pin both atime and mtime of ``path`` to ``mtime``."""
        os.utime(path, (mtime, mtime))

    def test_returns_none_when_combined_newer_than_all_peeps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        krr = tmp_path / "krr_outputs"
        peep, combined, index = self._seed_combined_tree(krr)
        # Make the derived artifacts strictly newer than the peep source.
        self._set_mtime(peep, 1_000.0)
        self._set_mtime(combined, 2_000.0)
        self._set_mtime(index, 2_000.0)

        monkeypatch.setattr(run_all_integration, "KRR_DIR", krr, raising=True)
        assert run_all_integration._combined_staleness_error() is None

    def test_flags_staleness_when_a_peep_is_newer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        krr = tmp_path / "krr_outputs"
        peep, combined, index = self._seed_combined_tree(krr)
        # Combined/INDEX were built, THEN the peep was enriched (future mtime).
        self._set_mtime(combined, 1_000.0)
        self._set_mtime(index, 1_000.0)
        self._set_mtime(peep, 5_000.0)

        monkeypatch.setattr(run_all_integration, "KRR_DIR", krr, raising=True)
        err = run_all_integration._combined_staleness_error()
        assert err is not None
        assert "stale" in err
        # It names the derived artifact and points at the newer source.
        assert "combined_ontology.jsonld" in err
        assert "foo_peep.json" in err

    def test_flags_staleness_for_overlay_peep_via_rglob(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The source set is recursive: a newer *subcorpus* overlay peep
        (not just a root peep) must also trip the guard."""
        krr = tmp_path / "krr_outputs"
        _, combined, index = self._seed_combined_tree(krr)
        overlay = krr / "eurlex" / "32016_peep.json"
        _write_tree(krr, [("eurlex/32016_peep.json", '{"@id": "estleg:Dir"}')])
        self._set_mtime(combined, 1_000.0)
        self._set_mtime(index, 1_000.0)
        self._set_mtime(krr / "laws" / "foo_peep.json", 1_000.0)
        self._set_mtime(overlay, 9_000.0)

        monkeypatch.setattr(run_all_integration, "KRR_DIR", krr, raising=True)
        err = run_all_integration._combined_staleness_error()
        assert err is not None and "stale" in err

    def test_same_second_tie_is_not_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A same-second filesystem tie (e.g. a fresh checkout) must NOT be
        flagged — the guard uses ``<``, not ``<=``."""
        krr = tmp_path / "krr_outputs"
        peep, combined, index = self._seed_combined_tree(krr)
        for p in (peep, combined, index):
            self._set_mtime(p, 3_000.0)

        monkeypatch.setattr(run_all_integration, "KRR_DIR", krr, raising=True)
        assert run_all_integration._combined_staleness_error() is None

    def test_missing_combined_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        krr = tmp_path / "krr_outputs"
        # Peep + INDEX present, but no combined artifact at all.
        _write_tree(
            krr,
            [
                ("laws/foo_peep.json", '{"@id": "estleg:Foo"}'),
                ("INDEX.json", '{"laws": []}'),
            ],
        )
        monkeypatch.setattr(run_all_integration, "KRR_DIR", krr, raising=True)
        err = run_all_integration._combined_staleness_error()
        assert err is not None
        assert "missing" in err
        assert "combined_ontology.jsonld" in err

    def test_missing_index_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        krr = tmp_path / "krr_outputs"
        _write_tree(
            krr,
            [
                ("laws/foo_peep.json", '{"@id": "estleg:Foo"}'),
                ("combined_ontology.jsonld", '{"@graph": []}'),
            ],
        )
        monkeypatch.setattr(run_all_integration, "KRR_DIR", krr, raising=True)
        err = run_all_integration._combined_staleness_error()
        assert err is not None
        assert "missing" in err
        assert "INDEX.json" in err

    def test_no_peeps_is_not_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no corpus to validate against there is nothing to declare
        stale — the guard returns None (the validators themselves will flag
        an empty corpus)."""
        krr = tmp_path / "krr_outputs"
        krr.mkdir(parents=True)
        monkeypatch.setattr(run_all_integration, "KRR_DIR", krr, raising=True)
        assert run_all_integration._combined_staleness_error() is None


# ---------------------------------------------------------------------------
# Finding #593 — release build must be interrupt-safe (restore on Ctrl-C)
# ---------------------------------------------------------------------------


class TestReleaseInterruptSafety:
    """A KeyboardInterrupt escaping ``run_dag`` must restore the pristine
    snapshot before propagating, so krr_outputs/ is never left partially
    mutated with the backup orphaned."""

    def test_restore_recovers_partially_mutated_tree(
        self, fake_krr: Path
    ) -> None:
        """The restore primitive the except path relies on must reinstate the
        original even after the live tree was partially mutated mid-build."""
        before = _read_tree(fake_krr)
        backup = run_all_integration.snapshot_outputs()

        # Mimic a partial build that was interrupted: a peep was rewritten,
        # a half-written artifact appeared, and another file was deleted.
        (fake_krr / "laws" / "foo_peep.json").write_text(
            '{"@id": "estleg:HalfWritten"}', encoding="utf-8"
        )
        (fake_krr / "combined_ontology.jsonld").write_text(
            "<<partial>>", encoding="utf-8"
        )
        (fake_krr / "regulations" / "bar_peep.json").unlink()

        run_all_integration.restore_outputs(backup)

        assert _read_tree(fake_krr) == before, (
            "restore must reinstate the pristine tree over a partial build"
        )
        assert not backup.exists(), "consumed backup must not linger"

    def test_main_restores_snapshot_when_run_dag_interrupted(
        self, fake_krr: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Drive main() far enough to prove the except path fires: run_dag
        raises KeyboardInterrupt, and restore_outputs must be called exactly
        once with the snapshot before the interrupt propagates."""
        fake_backup = fake_krr.parent / "fake.bak"

        monkeypatch.setattr(
            run_all_integration, "snapshot_outputs",
            lambda: fake_backup, raising=True,
        )

        def boom_run_dag(*args, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(run_all_integration, "run_dag", boom_run_dag,
                            raising=True)
        restore_mock = mock.Mock()
        monkeypatch.setattr(run_all_integration, "restore_outputs",
                            restore_mock, raising=True)
        # No script/validator subprocess should be reached before the raise.
        monkeypatch.setattr(
            run_all_integration.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=0),
        )
        monkeypatch.setattr(
            sys, "argv", ["run_all_integration.py", "--release"]
        )

        with pytest.raises(KeyboardInterrupt):
            run_all_integration.main()

        restore_mock.assert_called_once_with(fake_backup)

    def test_main_does_not_restore_when_snapshot_disabled_on_interrupt(
        self, fake_krr: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With ``--no-restore-on-failure`` there is no snapshot, so an
        interrupt must propagate WITHOUT attempting a restore."""

        def boom_run_dag(*args, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(run_all_integration, "run_dag", boom_run_dag,
                            raising=True)
        # If a snapshot is ever taken here it's a bug — restore would have a
        # backup to act on. Guard by failing the snapshot call.
        monkeypatch.setattr(
            run_all_integration, "snapshot_outputs",
            mock.Mock(side_effect=AssertionError("snapshot must be skipped")),
            raising=True,
        )
        restore_mock = mock.Mock()
        monkeypatch.setattr(run_all_integration, "restore_outputs",
                            restore_mock, raising=True)
        monkeypatch.setattr(
            run_all_integration.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(args=a, returncode=0),
        )
        monkeypatch.setattr(
            sys, "argv",
            ["run_all_integration.py", "--release", "--no-restore-on-failure"],
        )

        with pytest.raises(KeyboardInterrupt):
            run_all_integration.main()

        restore_mock.assert_not_called()
