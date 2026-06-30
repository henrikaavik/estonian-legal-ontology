"""Guard: the legacy ``data.riik.ee`` namespace must be fully migrated (#516).

A corpus-wide check that no tracked file (outside the historical / operational
exclusions) still references the retired ``data.riik.ee`` host. Marked
``corpus`` so it runs in the LFS-materialised CI step — the default no-LFS
``pytest`` job would false-pass the ``combined_ontology.jsonld`` pointer stub
(its ~1.4k IRIs would be invisible). See ``docs/NAMESPACE_MIGRATION.md``.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files that legitimately retain the legacy string: historical records and this
# migration's own doc + state. Kept in lockstep with the migration excludes and
# the validate.yml guard step.
EXCLUDE = (
    ":(exclude)CHANGELOG.md",
    ":(exclude)docs/superpowers/plans",
    ":(exclude)docs/NAMESPACE_MIGRATION.md",
    ":(exclude)data/namespace_migration_state.json",
    # The migration machinery itself legitimately references the legacy host.
    ":(exclude)scripts/migrate_namespace.py",
    ":(exclude)tests/test_no_legacy_namespace.py",
    ":(exclude).github/workflows/validate.yml",
)


@pytest.mark.corpus
def test_no_legacy_data_riik_ee_namespace():
    res = subprocess.run(
        ["git", "grep", "-F", "-l", "data.riik.ee", "--", *EXCLUDE],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    hits = [line for line in res.stdout.splitlines() if line]
    assert not hits, (
        "Legacy data.riik.ee namespace still present in:\n  "
        + "\n  ".join(hits)
        + "\n(re-run scripts/migrate_namespace.py --apply; see "
        "docs/NAMESPACE_MIGRATION.md)"
    )
