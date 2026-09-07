"""Repo hygiene: stale overview PDF and write-only artifacts (#538, #539)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

STALE_PDF = "docs/eesti-oigusontoloogia-ulevaade.pdf"
WRITE_ONLY_PATHS = (
    "krr_outputs/similarity/kov_similarity_index.json",
    "krr_outputs/concepts/concept_crossref_report.json",
    "data/uri_migration_report.json",
    "krr_outputs/reports/multipart_iri_migration_report.json",
)
COHORT_ISSUE_REFS = tuple(f"#{n}" for n in range(483, 493))


def _git_ls_files(*paths: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", *paths],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _gitignore_text() -> str:
    return (REPO / ".gitignore").read_text(encoding="utf-8")


def _gitignore_lists(path: str) -> bool:
    """True when .gitignore lists the path or a matching pattern."""
    text = _gitignore_text()
    entries: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line)

    if path in entries or f"/{path}" in entries:
        return True

    posix = Path(path).as_posix()
    for entry in entries:
        token = entry.removeprefix("/")
        if token.endswith("/"):
            if posix.startswith(token) or posix.startswith(token.rstrip("/")):
                return True
            continue
        if "*" in token:
            if Path(posix).match(token) or Path(posix).match(token.lstrip("/")):
                return True
    return False


def test_stale_overview_pdf_not_tracked() -> None:
    tracked = _git_ls_files(STALE_PDF)
    assert tracked == [], f"stale overview PDF must not be tracked (#538): {tracked}"


def test_docs_pdfs_are_gitignored() -> None:
    assert "docs/*.pdf" in _gitignore_text(), (
        ".gitignore must contain docs/*.pdf (#538)"
    )


def test_readme_does_not_link_overview_pdf() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "eesti-oigusontoloogia-ulevaade.pdf" not in readme, (
        "README must not link the stale overview PDF (#538)"
    )
    assert "eesti-oigusontoloogia-ulevaade.html" in readme, (
        "README must keep the HTML ülevaade link (#538)"
    )


def test_changelog_unreleased_mentions_review_cohort() -> None:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = text.split("## [0.", 1)[0]
    assert unreleased.startswith("# Changelog"), "CHANGELOG must start with title"
    assert "## [Unreleased]" in unreleased
    assert any(ref in unreleased for ref in COHORT_ISSUE_REFS), (
        "CHANGELOG [Unreleased] must mention at least one of #483–#492 (#538)"
    )


def test_write_only_artifacts_not_tracked() -> None:
    tracked = _git_ls_files(*WRITE_ONLY_PATHS)
    assert tracked == [], (
        "write-only artifacts must not be tracked (#539): " + ", ".join(tracked)
    )


def test_write_only_artifacts_gitignored() -> None:
    missing = [path for path in WRITE_ONLY_PATHS if not _gitignore_lists(path)]
    assert missing == [], (
        ".gitignore must list each write-only path or a matching pattern (#539): "
        + ", ".join(missing)
    )
