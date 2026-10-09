"""``--snapshot auto``: skip the 3.3 GB copytree on a clean tree (#722)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from estleg import run_all_integration as rai

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def git_krr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    krr = repo / "krr_outputs"
    (krr / "sub").mkdir(parents=True)
    (krr / "a_peep.json").write_text('{"v": 1}\n', encoding="utf-8")
    (krr / "sub" / "b.json").write_text('{"v": 2}\n', encoding="utf-8")
    (repo / ".gitignore").write_text("krr_outputs/.cache/\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "commit", "-qm", "init")
    monkeypatch.setattr(rai, "REPO_ROOT", repo, raising=True)
    monkeypatch.setattr(rai, "KRR_DIR", krr, raising=True)
    return krr


class TestResolveSnapshotMode:
    def test_git_config_cannot_hide_untracked_work(self, git_krr: Path) -> None:
        _git(git_krr.parent, "config", "status.showUntrackedFiles", "no")
        (git_krr / "precious.json").write_text("untracked work")
        assert rai.resolve_snapshot_mode("auto") == rai.ROLLBACK_COPY

    def test_auto_on_clean_tree_uses_git(self, git_krr: Path) -> None:
        assert rai.krr_outputs_git_clean() is True
        assert rai.resolve_snapshot_mode("auto") == rai.ROLLBACK_GIT

    def test_auto_on_dirty_tree_falls_back_to_copy(self, git_krr: Path) -> None:
        (git_krr / "a_peep.json").write_text('{"v": 99}\n', encoding="utf-8")
        assert rai.krr_outputs_git_clean() is False
        assert rai.resolve_snapshot_mode("auto") == rai.ROLLBACK_COPY

    def test_auto_with_untracked_file_is_dirty(self, git_krr: Path) -> None:
        (git_krr / "new.json").write_text("{}", encoding="utf-8")
        assert rai.resolve_snapshot_mode("auto") == rai.ROLLBACK_COPY

    def test_ignored_files_do_not_dirty_the_tree(self, git_krr: Path) -> None:
        (git_krr / ".cache").mkdir()
        (git_krr / ".cache" / "state.json").write_text("{}", encoding="utf-8")
        assert rai.resolve_snapshot_mode("auto") == rai.ROLLBACK_GIT

    def test_auto_without_git_falls_back_to_copy(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(rai, "REPO_ROOT", tmp_path, raising=True)
        monkeypatch.setattr(rai, "KRR_DIR", tmp_path / "krr_outputs", raising=True)
        assert rai.krr_outputs_git_clean() is None
        assert rai.resolve_snapshot_mode("auto") == rai.ROLLBACK_COPY

    def test_explicit_modes_are_kept(self, git_krr: Path) -> None:
        assert rai.resolve_snapshot_mode("copy") == rai.ROLLBACK_COPY
        assert rai.resolve_snapshot_mode("none") == rai.ROLLBACK_NONE
        assert rai.resolve_snapshot_mode("auto", no_restore_on_failure=True) == rai.ROLLBACK_NONE
        assert rai.resolve_snapshot_mode("copy", no_restore_on_failure=True) == rai.ROLLBACK_NONE

    def test_default_is_copy(self) -> None:
        assert rai.parse_args([]).snapshot == "copy"


class TestGitRollback:
    def test_restore_from_git_reverts_edits_and_removes_new_files(self, git_krr: Path) -> None:
        (git_krr / "a_peep.json").write_text('{"v": "mutated"}\n', encoding="utf-8")
        (git_krr / "sub" / "b.json").unlink()
        (git_krr / "partial_new.json").write_text("{}", encoding="utf-8")
        rai.restore_outputs_from_git()
        assert (git_krr / "a_peep.json").read_text(encoding="utf-8") == '{"v": 1}\n'
        assert (git_krr / "sub" / "b.json").exists()
        assert not (git_krr / "partial_new.json").exists()
        assert rai.krr_outputs_git_clean() is True

    def test_main_auto_skips_copytree_and_rolls_back_on_interrupt(
        self, git_krr: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            rai, "snapshot_outputs",
            mock.Mock(side_effect=AssertionError("copytree must be skipped on a clean tree")),
        )

        def boom_run_dag(*args, **kwargs):
            (git_krr / "a_peep.json").write_text("half-written", encoding="utf-8")
            raise KeyboardInterrupt()

        monkeypatch.setattr(rai, "run_dag", boom_run_dag)
        monkeypatch.setattr(sys, "argv", ["run_all_integration.py", "--release", "--snapshot", "auto"])
        with pytest.raises(KeyboardInterrupt):
            rai.main()
        assert (git_krr / "a_peep.json").read_text(encoding="utf-8") == '{"v": 1}\n'

    def test_main_auto_on_dirty_tree_takes_copy_snapshot(
        self, git_krr: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (git_krr / "a_peep.json").write_text('{"v": "uncommitted"}\n', encoding="utf-8")
        fake_backup = git_krr.parent / "fake.bak"
        snap = mock.Mock(return_value=fake_backup)
        restore = mock.Mock()
        git_restore = mock.Mock()
        monkeypatch.setattr(rai, "snapshot_outputs", snap)
        monkeypatch.setattr(rai, "restore_outputs", restore)
        monkeypatch.setattr(rai, "restore_outputs_from_git", git_restore)

        def boom_run_dag(*args, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(rai, "run_dag", boom_run_dag)
        monkeypatch.setattr(sys, "argv", ["run_all_integration.py", "--release", "--snapshot", "auto"])
        with pytest.raises(KeyboardInterrupt):
            rai.main()
        snap.assert_called_once_with()
        restore.assert_called_once_with(fake_backup)
        git_restore.assert_not_called()

    def test_snapshot_none_never_restores(self, git_krr: Path, monkeypatch) -> None:
        monkeypatch.setattr(rai, "snapshot_outputs", mock.Mock(side_effect=AssertionError))
        git_restore = mock.Mock()
        monkeypatch.setattr(rai, "restore_outputs_from_git", git_restore)

        def boom_run_dag(*args, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(rai, "run_dag", boom_run_dag)
        monkeypatch.setattr(sys, "argv", ["run_all_integration.py", "--release", "--snapshot", "none"])
        with pytest.raises(KeyboardInterrupt):
            rai.main()
        git_restore.assert_not_called()
