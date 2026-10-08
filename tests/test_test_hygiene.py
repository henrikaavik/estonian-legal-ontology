"""#706: the test-suite guards themselves (tiers, corpus accessor, krr guard).

Every check here is hermetic: the dirty-tree guard is exercised in a
throwaway git repository under tmp_path via a pytest subprocess, never
against the real ``krr_outputs``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests import conftest

pytest_plugins = ["pytester"]

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pythonpath_excludes_archive_and_examples():
    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_cfg = cfg["tool"]["pytest"]["ini_options"]
    assert pytest_cfg["pythonpath"] == [".", "src"]
    assert pytest_cfg["addopts"] == ["-m", "not corpus"]
    assert pytest_cfg["timeout"] > 0
    dev = " ".join(cfg["project"]["optional-dependencies"]["dev"])
    for plugin in ("pytest-xdist", "pytest-timeout", "pytest-socket"):
        assert plugin in dev


def test_archive_one_shots_are_not_importable_by_bare_name():
    for name in ("fix_duplicate_ids", "quickstart", "legacy_repairs"):
        assert not any(
            (Path(p) / f"{name}.py").is_file() for p in sys.path if p
        ), f"{name}.py is on sys.path again"


def test_script_loader_rejects_paths_outside_archive_and_examples():
    from tests._script_loader import load_script

    with pytest.raises(ValueError):
        load_script("src/estleg/estleg_common.py")


def test_network_is_blocked_for_non_live_tests():
    import socket

    from pytest_socket import SocketBlockedError

    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


# ── corpus_krr accessor ─────────────────────────────────────────────────────


class _Node:
    def __init__(self, *markers: str) -> None:
        self._markers = set(markers)

    def get_closest_marker(self, name: str):
        return name if name in self._markers else None


@pytest.fixture
def fake_krr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "krr_outputs"
    root.mkdir()
    (root / "real.json").write_text('{"ok": true}', encoding="utf-8")
    (root / "pointer.jsonld").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 1\n"
    )
    (root / "empty").mkdir()
    monkeypatch.setattr(conftest.CorpusKRR, "root", root)
    return root


def test_corpus_accessor_returns_real_files(fake_krr: Path):
    acc = conftest.CorpusKRR(_Node())
    assert acc.path("real.json") == fake_krr / "real.json"
    assert acc.read_json("real.json") == {"ok": True}


@pytest.mark.parametrize("rel", ["pointer.jsonld", "missing.json", "empty"])
def test_corpus_accessor_skips_outside_corpus_tier(fake_krr: Path, rel: str):
    with pytest.raises(pytest.skip.Exception) as info:
        conftest.CorpusKRR(_Node("committed")).path(rel)
    assert rel in str(info.value)


@pytest.mark.parametrize("rel", ["pointer.jsonld", "missing.json", "empty"])
def test_corpus_accessor_fails_in_corpus_tier(fake_krr: Path, rel: str):
    with pytest.raises(pytest.fail.Exception) as info:
        conftest.CorpusKRR(_Node("corpus")).path(rel)
    assert "corpus gate input unavailable" in str(info.value)


def test_is_lfs_pointer(fake_krr: Path):
    assert conftest.is_lfs_pointer(fake_krr / "pointer.jsonld")
    assert not conftest.is_lfs_pointer(fake_krr / "real.json")
    assert not conftest.is_lfs_pointer(fake_krr / "missing.json")


# ── krr_outputs dirty-tree guard (subprocess in a scratch repo) ─────────────


@pytest.fixture(autouse=True)
def _guard_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # The outer session may run with the guard disabled; the scratch
    # subprocesses must not inherit that.
    monkeypatch.delenv(conftest.ALLOW_KRR_WRITES_ENV, raising=False)


def _scratch_repo(pytester: pytest.Pytester, test_body: str) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    root = pytester.path
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(Path(conftest.__file__), root / "tests" / "conftest.py")
    (root / "tests" / "test_scratch.py").write_text(test_body, encoding="utf-8")
    krr = root / "krr_outputs"
    krr.mkdir()
    (krr / "a_peep.json").write_text("{}\n", encoding="utf-8")
    (krr / "dirty_peep.json").write_text("{}\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(git + ["init", "-q"], cwd=root, check=True)
    subprocess.run(git + ["add", "-A"], cwd=root, check=True)
    subprocess.run(git + ["commit", "-qm", "seed"], cwd=root, check=True)
    # Pre-existing dirt must not fail the session on its own.
    (krr / "dirty_peep.json").write_text('{"pre": 1}\n', encoding="utf-8")
    return root


_WRITER = """
from pathlib import Path

def test_writes_real_corpus():
    p = Path(__file__).resolve().parent.parent / "krr_outputs" / "a_peep.json"
    p.write_text('{"changed": true}\\n', encoding="utf-8")
"""

_READER = """
def test_noop():
    assert True
"""


def test_krr_guard_fails_session_when_a_test_writes(pytester: pytest.Pytester):
    _scratch_repo(pytester, _WRITER)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.stdout.fnmatch_lines(["*krr_outputs dirty-tree guard*", "*a_peep.json*"])
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_krr_guard_tolerates_preexisting_dirt(pytester: pytest.Pytester):
    _scratch_repo(pytester, _READER)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.OK
    assert "dirty-tree guard" not in result.stdout.str()


def test_krr_guard_can_be_disabled_for_data_work(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
):
    _scratch_repo(pytester, _WRITER)
    monkeypatch.setenv(conftest.ALLOW_KRR_WRITES_ENV, "1")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.OK
