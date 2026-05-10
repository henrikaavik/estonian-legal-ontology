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
