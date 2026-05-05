import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


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
    """
    config.addinivalue_line(
        "markers",
        "slow: corpus-iterating invariant tests; run by default, "
        "opt out with `-m 'not slow'`.",
    )
