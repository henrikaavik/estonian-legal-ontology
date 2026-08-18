"""Issue #468 — spent one-shots live in scripts/archive/; migrate_uris stays live."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
ARCHIVE = SCRIPTS / "archive"


def test_spent_one_shots_are_archived() -> None:
    assert (ARCHIVE / "migrate_multipart_iri_scheme.py").is_file()
    assert (ARCHIVE / "fix_duplicate_ids.py").is_file()
    assert (SCRIPTS / "migrate_uris.py").is_file()
    assert not (SCRIPTS / "migrate_multipart_iri_scheme.py").exists()
    assert not (SCRIPTS / "fix_duplicate_ids.py").exists()


def test_archive_readme_mentions_spent() -> None:
    text = (ARCHIVE / "README.md").read_text(encoding="utf-8")
    assert "spent" in text.lower()
