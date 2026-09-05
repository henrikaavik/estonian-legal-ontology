"""#687 — the three legacy-namespace exclusion lists must stay in lockstep.

Three places name the paths that legitimately keep the retired host:

  A. the ``:(exclude)`` pathspecs in the "No legacy … namespace" step of
     ``.github/workflows/validate.yml`` (the CI guard),
  B. the ``EXCLUDE`` pathspecs in ``tests/test_no_legacy_namespace.py``
     (the same guard, as a ``corpus``-marked test),
  C. ``EXCLUDE_PREFIXES`` in ``src/estleg/migrate_namespace.py`` (the paths
     the ``--apply`` string-swap skips).

Drift between them is silently destructive in both directions: a path in C but
not in A/B is a file the swap never rewrites and the guard then fails on (CI
red), while a path in A/B but not in C is a file the swap rewrites with nothing
watching — which is exactly how ``--apply`` came to rewrite the migrator's own
``REPLACEMENTS`` table and the ``w3id/`` redirect config. Assert plain set
equality, normalised for the ``:(exclude)`` pathspec prefix and for trailing
slashes (C uses directory prefixes, A/B use git pathspecs).

Runs by default — it reads three committed text files and needs neither a
populated ``krr_outputs/`` nor materialised Git-LFS artifacts.
"""

from __future__ import annotations

import re
from pathlib import Path

from estleg.migrate_namespace import EXCLUDE_PREFIXES, LEGACY_HOST
from tests.test_no_legacy_namespace import EXCLUDE as GUARD_TEST_EXCLUDE

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate.yml"

# The live migrator (the module that owns REPLACEMENTS + LEGACY_HOST) versus
# the runpy shim kept for the old ``python3 scripts/…`` invocation. Only the
# former carries the legacy string, so only the former belongs in the lists.
LIVE_MIGRATOR = "src/estleg/migrate_namespace.py"
MIGRATOR_SHIM = "scripts/migrate_namespace.py"

# PyYAML is not a project dependency, so the workflow is read as text and the
# pathspecs are pulled straight out of the guard step's shell body. The step
# name is rebuilt from LEGACY_HOST so this file never spells the retired host
# itself — it is not on any exclusion list and would trip the guard it tests.
STEP_NAME = f"No legacy {LEGACY_HOST} namespace"
STEP_END = 'if [ -n "$hits" ]'
PATHSPEC_RE = re.compile(r"':\(exclude\)([^']*)'")


def _normalise(pathspec: str) -> str:
    """Strip the git ``:(exclude)`` prefix and any trailing slash."""
    return pathspec.removeprefix(":(exclude)").rstrip("/")


def _workflow_excludes() -> set[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index(STEP_NAME)
    end = text.index(STEP_END, start)
    return {_normalise(token) for token in PATHSPEC_RE.findall(text[start:end])}


def _all_lists() -> dict[str, set[str]]:
    return {
        "validate.yml guard step": _workflow_excludes(),
        "tests/test_no_legacy_namespace.py EXCLUDE": {
            _normalise(p) for p in GUARD_TEST_EXCLUDE
        },
        "estleg.migrate_namespace.EXCLUDE_PREFIXES": {
            _normalise(p) for p in EXCLUDE_PREFIXES
        },
    }


def test_three_exclusion_lists_are_identical() -> None:
    lists = _all_lists()
    workflow = lists["validate.yml guard step"]
    assert workflow, (
        f"no ':(exclude)' pathspecs parsed out of the {STEP_NAME!r} step in "
        f"{WORKFLOW.relative_to(REPO_ROOT)} — the step was renamed or "
        "restructured, so this parity check is no longer reading it"
    )
    union = set().union(*lists.values())
    diffs = {
        name: sorted(union - values) for name, values in lists.items() if union - values
    }
    assert not diffs, "Legacy-namespace exclusion lists have drifted (#687):\n" + "\n".join(
        f"  missing from {name}: {', '.join(missing)}" for name, missing in diffs.items()
    )


def test_live_migrator_is_excluded_and_the_shim_is_not() -> None:
    """Only the module that actually holds the legacy string is excluded."""
    assert LEGACY_HOST in (REPO_ROOT / LIVE_MIGRATOR).read_text(encoding="utf-8")
    assert LEGACY_HOST not in (REPO_ROOT / MIGRATOR_SHIM).read_text(encoding="utf-8")
    for name, values in _all_lists().items():
        assert LIVE_MIGRATOR in values, f"{name} does not exclude {LIVE_MIGRATOR}"
        assert MIGRATOR_SHIM not in values, (
            f"{name} still excludes {MIGRATOR_SHIM}, which holds no legacy string"
        )
