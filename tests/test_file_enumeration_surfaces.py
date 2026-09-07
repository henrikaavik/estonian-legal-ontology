"""Shared file-enumeration surfaces (issue #452).

Reports are counted by ``iter_krr_jsonld_files`` (stable corpus counts)
but excluded from validation / public-load / SHACL sidecar buckets.
"""
from __future__ import annotations

from pathlib import Path

from estleg.estleg_common import (
    NON_DATA_STEM_SUFFIXES,
    is_non_data_file,
    is_public_jsonld_data_file,
    iter_krr_jsonld_files,
)


def test_non_data_stem_suffixes_include_report_family() -> None:
    for suffix in ("_report", "_summary", "_index", "_review"):
        assert suffix in NON_DATA_STEM_SUFFIXES, suffix


def test_is_non_data_file_classifies_report_vs_peep() -> None:
    assert is_non_data_file(Path("concept_crossref_report.json")) is True
    assert is_non_data_file(Path("foo_peep.json")) is False


def test_is_public_jsonld_data_file_drops_schema_and_reports() -> None:
    assert is_public_jsonld_data_file(Path("foo_schema.json")) is False
    assert is_public_jsonld_data_file(Path("concept_crossref_report.json")) is False
    assert is_public_jsonld_data_file(Path("foo_review.json")) is False


def test_iter_krr_jsonld_files_counts_reports_skips_regen_state(tmp_path: Path) -> None:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    (krr / "foo_peep.json").write_text("{}", encoding="utf-8")
    (krr / "foo_report.json").write_text("{}", encoding="utf-8")
    (krr / "foo_schema.json").write_text("{}", encoding="utf-8")
    (krr / ".regen_state.json").write_text("{}", encoding="utf-8")

    names = {p.name for p in iter_krr_jsonld_files(krr)}
    assert "foo_peep.json" in names
    assert "foo_report.json" in names
    assert "foo_schema.json" in names
    assert ".regen_state.json" not in names


def test_validate_all_uses_shared_or_exclude_suffixes() -> None:
    from estleg import validate_all

    if hasattr(validate_all, "EXCLUDE_SUFFIXES"):
        suffixes = validate_all.EXCLUDE_SUFFIXES
        assert any("report" in suffix for suffix in suffixes)
        return

    helper = getattr(validate_all, "is_non_data_file", is_non_data_file)
    assert helper(Path("concept_crossref_report.json")) is True
    assert helper(Path("foo_peep.json")) is False


def test_shacl_sidecar_helper_excludes_review_json() -> None:
    from estleg import shacl_validate_all as sva

    review = Path("foo_review.json")
    helper = getattr(sva, "_is_sidecar_data_file", None)
    if helper is not None:
        assert helper(review) is False
        return

    helper = getattr(sva, "is_non_data_file", is_non_data_file)
    assert helper(review) is True
