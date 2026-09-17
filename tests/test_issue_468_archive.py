"""Issue #468 — spent one-shots live in scripts/archive/; migrate_uris stays live."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
ARCHIVE = SCRIPTS / "archive"


def test_spent_one_shots_are_archived() -> None:
    assert (ARCHIVE / "migrate_multipart_iri_scheme.py").is_file()
    assert (ARCHIVE / "fix_duplicate_ids.py").is_file()
    assert (ARCHIVE / "rederive_court_case_types.py").is_file()
    assert (ARCHIVE / "backfill_eu_provenance.py").is_file()
    assert (SCRIPTS / "migrate_uris.py").is_file()
    assert not (SCRIPTS / "migrate_multipart_iri_scheme.py").exists()
    assert not (SCRIPTS / "fix_duplicate_ids.py").exists()
    assert not (SCRIPTS / "rederive_court_case_types.py").exists()
    assert not (SCRIPTS / "backfill_eu_provenance.py").exists()


def test_archive_readme_mentions_spent() -> None:
    text = (ARCHIVE / "README.md").read_text(encoding="utf-8")
    assert "spent" in text.lower()
    assert "rederive_court_case_types.py" in text
    assert "backfill_eu_provenance.py" in text


def test_release_docs_assert_zero_backfill() -> None:
    text = (REPO / "docs" / "RELEASE.md").read_text(encoding="utf-8")
    assert "From-scratch regen (zero backfill)" in text
    assert "build_release_artifacts.py" in text
    assert "rederive_court_case_types.py" in text
