"""Shared pytest configuration: test tiers, corpus access and guards (#706).

Tiers (see tests/README.md):

* ``unit`` - default; hermetic (tmp_path, fixtures under tests/fixtures/).
  Applied automatically to every test that carries no other tier marker.
* ``committed`` - reads committed, non-LFS data from the real checkout.
  Applied automatically to tests that use ``corpus_krr`` without ``corpus``.
* ``corpus`` - LFS-backed / whole-corpus invariant gates. Deselected by the
  default ``-m "not corpus"`` in pyproject ``addopts``; run with
  ``pytest -m corpus``. Missing LFS inputs FAIL here instead of skipping.
* ``live`` - talks to the network (Riigi Teataja). Skipped unless
  ``ESTLEG_LIVE_CANARY=1``; the only tier allowed to open sockets.
* ``slow`` - orthogonal; runs by default, opt out with ``-m "not slow"``.

Guards: every non-``live`` test runs with sockets disabled (pytest-socket);
every test has a default timeout (pyproject ``timeout``; ``corpus``/``slow``
get ``HEAVY_TEST_TIMEOUT``); and the session fails if ``krr_outputs/``
gained new uncommitted changes while it ran (``ESTLEG_ALLOW_KRR_WRITES=1``
disables that check for intentional data work).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KRR_ROOT = REPO_ROOT / "krr_outputs"
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
LIVE_ENV = "ESTLEG_LIVE_CANARY"
ALLOW_KRR_WRITES_ENV = "ESTLEG_ALLOW_KRR_WRITES"
HEAVY_TEST_TIMEOUT = 1800
TIER_MARKERS = ("unit", "committed", "corpus", "live")

try:
    import pytest_socket
except ImportError:  # pragma: no cover - surfaced as a UsageError below
    pytest_socket = None


def pytest_configure(config: pytest.Config) -> None:
    """Register the tier markers and require the guard plugins."""
    for line in (
        "unit: hermetic test (default tier; applied automatically).",
        "committed: reads committed non-LFS data from the real checkout "
        "(applied automatically to tests using the corpus_krr fixture).",
        "corpus: LFS / whole-corpus invariant gate; deselected by default, "
        "run with `pytest -m corpus`. Missing inputs fail, never skip.",
        "live: network test (Riigi Teataja); skipped unless "
        f"{LIVE_ENV}=1, and the only tier allowed to open sockets.",
        "slow: corpus-iterating invariant test; runs by default, opt out "
        "with `-m 'not slow'`.",
    ):
        config.addinivalue_line("markers", line)
    missing = [
        name
        for name, present in (
            ("pytest-socket", pytest_socket is not None),
            ("pytest-timeout", config.pluginmanager.hasplugin("timeout")),
        )
        if not present
    ]
    if missing:
        raise pytest.UsageError(
            f"tests/conftest.py needs {', '.join(missing)}; "
            'install the dev extras: python3 -m pip install -e ".[dev]"'
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """Assign default tiers, gate ``live`` and widen heavy-test timeouts."""
    live_enabled = os.environ.get(LIVE_ENV) == "1"
    skip_live = pytest.mark.skip(reason=f"live network test; set {LIVE_ENV}=1 to run")
    for item in items:
        if "live" in item.keywords and not live_enabled:
            item.add_marker(skip_live)
        if not any(item.get_closest_marker(m) for m in TIER_MARKERS):
            uses_corpus = "corpus_krr" in getattr(item, "fixturenames", ())
            item.add_marker(pytest.mark.committed if uses_corpus else pytest.mark.unit)
        heavy = item.get_closest_marker("corpus") or item.get_closest_marker("slow")
        if heavy and not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(HEAVY_TEST_TIMEOUT))


@pytest.fixture(autouse=True)
def _network_guard(request: pytest.FixtureRequest):
    """Disable sockets for every test that is not marked ``live`` (#706).

    Unix-domain sockets stay allowed (asyncio's self-pipe, local IPC).
    """
    if request.node.get_closest_marker("live"):
        yield
        return
    pytest_socket.disable_socket(allow_unix_socket=True)
    try:
        yield
    finally:
        pytest_socket.enable_socket()


# ── krr_outputs dirty-tree guard ─────────────────────────────────────────────


def _krr_status() -> dict[str, tuple[str, int, int]] | None:
    """Map each dirty ``krr_outputs`` path to (git status, mtime_ns, size).

    Returns None when git is unavailable (e.g. an sdist without .git).
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "krr_outputs"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    entries: dict[str, tuple[str, int, int]] = {}
    records = proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if len(record) < 4:
            continue
        code, rel = record[:2], record[3:]
        if code[0] in "RC":  # rename/copy: the next record is the source path
            i += 1
        try:
            st = (REPO_ROOT / rel).stat()
            entries[rel] = (code, st.st_mtime_ns, st.st_size)
        except OSError:
            entries[rel] = (code, 0, -1)
    return entries


def _krr_guard_enabled(config: pytest.Config) -> bool:
    return (
        not hasattr(config, "workerinput")  # xdist workers: controller checks once
        and os.environ.get(ALLOW_KRR_WRITES_ENV) != "1"
    )


_KRR_BASELINE = pytest.StashKey[dict]()


def pytest_sessionstart(session: pytest.Session) -> None:
    if _krr_guard_enabled(session.config):
        baseline = _krr_status()
        if baseline is not None:
            session.config.stash[_KRR_BASELINE] = baseline


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run if any test wrote to the real ``krr_outputs`` (#706).

    Compares the end-of-session dirty set (status + mtime + size) against
    the start, so a tree that was already dirty does not fail the run;
    only changes made while the session ran do.
    """
    baseline = session.config.stash.get(_KRR_BASELINE, None)
    if baseline is None:
        return
    current = _krr_status() or {}
    changed = sorted(p for p, state in current.items() if baseline.get(p) != state)
    changed += sorted(p for p in baseline if p not in current)  # reverted mid-run
    if not changed:
        return
    shown = "\n".join(f"  {p}" for p in changed[:20])
    more = f"\n  ... and {len(changed) - 20} more" if len(changed) > 20 else ""
    message = (
        f"krr_outputs/ changed during the test session ({len(changed)} path(s)):\n"
        f"{shown}{more}\n"
        "A test wrote to the real corpus; use the isolated_krr fixture. "
        f"Set {ALLOW_KRR_WRITES_ENV}=1 only for intentional data work."
    )
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", "krr_outputs dirty-tree guard", red=True)
        reporter.write_line(message)
    else:
        print(message, file=sys.stderr)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


# ── corpus access ────────────────────────────────────────────────────────────


def is_lfs_pointer(path: Path) -> bool:
    """True when ``path`` is an un-pulled Git LFS pointer file."""
    try:
        with open(path, "rb") as fh:
            return fh.read(len(LFS_POINTER_PREFIX)) == LFS_POINTER_PREFIX
    except OSError:
        return False


def _corpus_input_problem(path: Path) -> str | None:
    if path.is_dir():
        return None if any(path.iterdir()) else "is an empty directory"
    if not path.exists():
        return "is missing"
    if is_lfs_pointer(path):
        return "is a Git LFS pointer (run `git lfs pull`)"
    return None


class CorpusKRR:
    """LFS-aware read accessor for the real ``krr_outputs`` tree.

    ``path(rel)`` returns ``KRR_ROOT / rel`` when it is present and real.
    When it is missing, an empty directory or an LFS pointer, the test:

    * FAILS if it is marked ``corpus`` - a release/invariant gate must not
      hide a missing input behind a skip (AGENTS.md);
    * otherwise SKIPS with the reason, so a clean clone without LFS still
      runs the rest of the suite.

    The accessor is read-only by contract; the session-level dirty-tree
    guard fails the run if a test writes through it.
    """

    root = KRR_ROOT

    def __init__(self, node: pytest.Item) -> None:
        self._gate = node.get_closest_marker("corpus") is not None

    def path(self, rel: str | Path) -> Path:
        target = self.root / rel
        problem = _corpus_input_problem(target)
        if problem is not None:
            message = f"krr_outputs/{rel} {problem}"
            if self._gate:
                pytest.fail(f"corpus gate input unavailable: {message}", pytrace=False)
            pytest.skip(f"{message}; this input is required by `-m corpus` runs")
        return target

    def read_json(self, rel: str | Path):
        with open(self.path(rel), encoding="utf-8") as fh:
            return json.load(fh)


@pytest.fixture
def corpus_krr(request: pytest.FixtureRequest) -> CorpusKRR:
    """Read the real ``krr_outputs`` through the LFS-aware accessor."""
    return CorpusKRR(request.node)


@pytest.fixture
def isolated_krr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Stage an empty isolated KRR_DIR and rebind every ``*_DIR`` attribute.

    Returns a builder callable ``builder(script)`` that, given a script
    module, walks every uppercase attribute ending in ``_DIR`` (e.g.
    ``KRR_DIR``, ``RK_DIR``, ``DRAFTS_DIR``) and rebinds it under
    ``tmp_path`` preserving its original layout relative to its parent
    KRR_DIR. The fixture itself yields ``tmp_path / "krr_outputs"``,
    which is also the canonical KRR root used for the rebind.

    Usage::

        def test_my_script_does_x(isolated_krr, monkeypatch):
            tmp_krr = isolated_krr.krr
            isolated_krr.bind(my_script_module)
            # Now my_script_module.KRR_DIR == tmp_krr (and any other
            # *_DIR attributes are rebased under tmp_krr).

    This collapses the boilerplate where every test manually builds
    its own ``tmp_krr = tmp_path / "krr_outputs"`` and then calls
    ``monkeypatch.setattr(script, "KRR_DIR", tmp_krr)`` etc.
    """
    tmp_krr = tmp_path / "krr_outputs"
    tmp_krr.mkdir(parents=True, exist_ok=True)

    class _Binder:
        krr = tmp_krr

        @staticmethod
        def write_json(rel: str | Path, data: object) -> Path:
            """Stage ``data`` as JSON at ``tmp_krr / rel`` (parents created)."""
            target = tmp_krr / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return target

        @staticmethod
        def bind(script: ModuleType) -> Path:
            """Rebind every ``*_DIR`` attribute on ``script`` under tmp_krr.

            For each attribute on ``script`` that is a ``Path`` and
            ends in ``_DIR``, compute its position relative to its
            originally-configured KRR_DIR (if any) and rebase it
            under the test ``tmp_krr``. Falls back to attribute-name
            heuristics when the original isn't a child of KRR_DIR.
            """
            # Determine the script's notion of KRR_DIR (if any).
            original_krr = getattr(script, "KRR_DIR", None)
            if isinstance(original_krr, Path):
                monkeypatch.setattr(script, "KRR_DIR", tmp_krr, raising=False)
            for name in dir(script):
                if not name.endswith("_DIR") or name == "KRR_DIR":
                    continue
                if not name.isupper() and not name[0].isupper():
                    continue
                value = getattr(script, name, None)
                if not isinstance(value, Path):
                    continue
                # Try to rebase relative to original KRR_DIR.
                rebased: Path | None = None
                if isinstance(original_krr, Path):
                    try:
                        rel = value.resolve().relative_to(
                            original_krr.resolve()
                        )
                        rebased = tmp_krr / rel
                    except (ValueError, OSError):
                        rebased = None
                if rebased is None:
                    # Fallback: derive a directory name from the attribute.
                    rebased = tmp_krr / name.lower().removesuffix("_dir")
                rebased.mkdir(parents=True, exist_ok=True)
                monkeypatch.setattr(script, name, rebased, raising=False)
            return tmp_krr

    return _Binder()


@pytest.fixture(autouse=True)
def _clear_validate_all_errors(request: pytest.FixtureRequest):
    """Auto-clear ``validate_all.errors`` for any test that imports it.

    Module-level ``errors``/``warnings`` lists in ``validate_all`` are
    shared mutable state; tests that miss a manual ``errors.clear()``
    silently leak diagnostics. This fixture runs for every test
    function whose test module has ``validate_all`` imported, before
    AND after the test, so the ordering of test discovery cannot
    cause false positives.
    """
    test_module = request.module
    validate_all = sys.modules.get("estleg.validate_all") or sys.modules.get(
        "validate_all"
    )
    target = getattr(test_module, "validate_all", None) or validate_all
    if target is not None and hasattr(target, "errors"):
        target.errors.clear()
        if hasattr(target, "warnings"):
            target.warnings.clear()
    yield
    if target is not None and hasattr(target, "errors"):
        target.errors.clear()
        if hasattr(target, "warnings"):
            target.warnings.clear()
