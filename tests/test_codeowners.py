"""#685: CODEOWNERS must route safety-critical review at real source files.

Every generator line in `.github/CODEOWNERS` used to name a 9-line `runpy`
shim under `scripts/` while the extraction heuristics (28-80 KB each) sat in
`src/estleg/` under the default owner only. A legal-correctness review was
therefore requested for the shim and never for the code that decides what a
sanction, a deontic force or a court holding says.

These tests are the CI check for that: they run in the default (non-corpus)
suite and fail if a CODEOWNERS path stops existing, points at a shim again, or
drifts out of sync with the CONTRIBUTING.md review list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CODEOWNERS = REPO / ".github" / "CODEOWNERS"
CONTRIBUTING = REPO / "CONTRIBUTING.md"

# A `scripts/` shim is ~250 bytes; the smallest real implementation module is
# ~28 KB. 1 KiB separates them with room to spare in both directions.
MIN_SOURCE_BYTES = 1024

# `src/estleg/<name>.py`, with or without a leading slash.
SRC_MODULE_RE = re.compile(r"src/estleg/([A-Za-z0-9_]+\.py)")


def _entries() -> list[tuple[int, str, list[str]]]:
    """Parse CODEOWNERS into ``(line_number, pattern, owners)`` triples."""
    entries: list[tuple[int, str, list[str]]] = []
    for lineno, raw in enumerate(
        CODEOWNERS.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        entries.append((lineno, pattern, owners))
    return entries


ENTRIES = _entries()
PATH_ENTRIES = [entry for entry in ENTRIES if entry[1] != "*"]


def test_codeowners_is_not_empty() -> None:
    """Guard the parametrised cases below against vacuous success."""
    assert ENTRIES, f"{CODEOWNERS} has no owner lines"
    assert PATH_ENTRIES, (
        "CODEOWNERS declares no path-specific owners; the safety-critical "
        "routing (#685) is gone"
    )


@pytest.mark.parametrize(
    ("lineno", "pattern", "owners"),
    ENTRIES,
    ids=[f"L{lineno}:{pattern}" for lineno, pattern, _ in ENTRIES],
)
def test_every_line_has_an_owner(
    lineno: int, pattern: str, owners: list[str]
) -> None:
    assert owners, f"CODEOWNERS line {lineno} ({pattern}) has no owner"
    assert all(owner.startswith("@") for owner in owners), (
        f"CODEOWNERS line {lineno} ({pattern}) has a non-handle owner: {owners}"
    )


@pytest.mark.parametrize(
    ("lineno", "pattern"),
    [(lineno, pattern) for lineno, pattern, _ in PATH_ENTRIES],
    ids=[f"L{lineno}:{pattern}" for lineno, pattern, _ in PATH_ENTRIES],
)
def test_owned_path_exists_and_is_not_a_shim(lineno: int, pattern: str) -> None:
    """A routed path must resolve, and a file must be real source, not a shim."""
    target = REPO / pattern.lstrip("/")

    if pattern.endswith("/"):
        assert target.is_dir(), (
            f"CODEOWNERS line {lineno} routes {pattern}, which is not a "
            f"directory in the repo"
        )
        assert any(child.is_file() for child in target.rglob("*")), (
            f"CODEOWNERS line {lineno} routes empty directory {pattern}"
        )
        return

    assert target.is_file(), (
        f"CODEOWNERS line {lineno} routes {pattern}, which is not a file in "
        f"the repo"
    )
    size = target.stat().st_size
    assert size > MIN_SOURCE_BYTES, (
        f"CODEOWNERS line {lineno} routes {pattern} ({size} bytes) — too "
        f"small to be an implementation. The `scripts/` entry points are "
        f"`runpy` shims; route the module under `src/estleg/` instead (#685)."
    )


def _codeowners_modules() -> set[str]:
    return {
        match.group(1)
        for _, pattern, _ in PATH_ENTRIES
        for match in [SRC_MODULE_RE.fullmatch(pattern.lstrip("/"))]
        if match
    }


def _contributing_modules() -> set[str]:
    text = CONTRIBUTING.read_text(encoding="utf-8")
    return set(SRC_MODULE_RE.findall(text))


def test_safety_critical_modules_are_routed() -> None:
    """The generator routing must actually name `src/estleg/` modules."""
    assert _codeowners_modules(), (
        "CODEOWNERS routes no src/estleg/ module; the safety-critical "
        "generators are back under the default owner only (#685)"
    )


def test_codeowners_and_contributing_list_the_same_modules() -> None:
    """CONTRIBUTING.md's review list is the human-readable half of CODEOWNERS."""
    owned = _codeowners_modules()
    documented = _contributing_modules()

    missing_from_docs = sorted(owned - documented)
    assert not missing_from_docs, (
        "CODEOWNERS routes these modules but CONTRIBUTING.md's "
        f"'Mandatory legal-correctness review' list omits them: "
        f"{missing_from_docs}"
    )

    missing_from_owners = sorted(documented - owned)
    assert not missing_from_owners, (
        "CONTRIBUTING.md requires legal review for these modules but "
        f"CODEOWNERS does not route them: {missing_from_owners}"
    )
