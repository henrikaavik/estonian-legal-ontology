import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
# After scripts/ so live modules still win (#468).
sys.path.insert(1, str(_SCRIPTS / "archive"))


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers used across the test suite.

    `slow` flags corpus-wide invariant tests that iterate the full
    `krr_outputs/` tree (Layer 2c PR #1's enforcedAtLevel check at
    `test_extract_sanctions.py`, Layer 2c PR #2's
    Issuer_*-vs-Institution_* fallback check at
    `test_extract_institutional_competence.py`). They run BY DEFAULT;
    registration only suppresses `PytestUnknownMarkWarning` and lets
    CI opt out of the slow path via `pytest -m 'not slow'`. Without
    registration the warnings accumulate as more `@pytest.mark.slow`
    usages land in PR #3+, and the unspecified semantic risks
    drifting toward auto-skip — which would break the corpus
    verification gates that depend on these tests running.

    `corpus` flags tests that depend on the real populated
    ``krr_outputs/`` corpus and assert hard counts (e.g.
    ``len(kov_files) > 1000``). They are SKIPPED by default so a
    clean clone or a CI runner without git-lfs / fresh
    krr_outputs/ won't fail. Opt in with ``pytest -m corpus`` after
    confirming ``krr_outputs/regulations/kov/`` and
    ``krr_outputs/regulations/riik/`` are populated.
    """
    config.addinivalue_line(
        "markers",
        "slow: corpus-iterating invariant tests; run by default, "
        "opt out with `-m 'not slow'`.",
    )
    config.addinivalue_line(
        "markers",
        "corpus: tests that assert hard counts against the real "
        "krr_outputs/ corpus; SKIPPED by default. Opt in with "
        "`-m corpus` once a populated corpus is available.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """Auto-skip @pytest.mark.corpus when the user did not opt in.

    Hard-count corpus invariants (KOV >1000, riik >100) require a
    populated ``krr_outputs/`` tree. Make them opt-in via
    ``pytest -m corpus`` to keep clean-clone CI green.
    """
    selected_marks = config.getoption("-m") or ""
    if "corpus" in selected_marks:
        return
    skip_corpus = pytest.mark.skip(
        reason=(
            "Corpus-dependent test; opt in with `pytest -m corpus` "
            "after populating krr_outputs/."
        )
    )
    for item in items:
        if "corpus" in item.keywords:
            item.add_marker(skip_corpus)


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
    validate_all = sys.modules.get("validate_all")
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
