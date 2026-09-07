"""#702: the two docs reports are generated and CI-checkable, not hand-written.

`VALIDATION_REPORT.md` published "Errors: 0 / PASSED" against a run dated
2026-05-26 while `json-validation` had been red on every `main` run since
v1.0.0, and `DUPLICATE_IDS_REPORT.md` was rendered from a pytest fixture
(`root_a_peep.json`, a `tmp_path` artefact) rather than from the corpus.
"""

from __future__ import annotations

import json
from collections import Counter

from estleg import generate_duplicate_ids_report as dup
from estleg import generate_validation_report as vr


class TestValidationReportGenerator:
    def test_parses_the_validator_summary(self):
        out = "Files validated: 26,961\nErrors: 122\nWarnings: 2\n"
        assert vr.parse_summary(out) == {
            "files": 26961,
            "errors": 122,
            "warnings": 2,
        }

    def test_missing_summary_line_is_an_error(self):
        import pytest

        with pytest.raises(ValueError, match="errors"):
            vr.parse_summary("Files validated: 1\nWarnings: 0\n")

    def test_categories_collapse_per_node_detail(self):
        out = (
            "  ERROR: a.json: dcterms:subject is not an array at graph[3] (@id=estleg:X)\n"
            "  ERROR: b.json: dcterms:subject is not an array at graph[9] (@id=estleg:Y)\n"
            "  ERROR: c.json: Duplicate @id within file: estleg:Z\n"
        )
        assert vr.categorise(out) == Counter(
            {"dcterms:subject is not an array": 2, "Duplicate @id within file": 1}
        )

    def test_result_reflects_the_error_count(self):
        passed = vr.render_block({"files": 1, "errors": 0, "warnings": 0}, Counter())
        failed = vr.render_block({"files": 1, "errors": 3, "warnings": 0}, Counter())
        assert "**PASSED**" in passed
        assert "**FAILED**" in failed

    def test_splice_only_replaces_the_generated_block(self):
        report = f"# Title\n\nprose\n\n{vr.BEGIN_MARKER}\nold\n{vr.END_MARKER}\n\nmore prose\n"
        spliced = vr.splice(report, f"{vr.BEGIN_MARKER}\nnew\n{vr.END_MARKER}")
        assert "prose" in spliced and "more prose" in spliced
        assert "old" not in spliced and "new" in spliced

    def test_missing_markers_are_an_error(self):
        import pytest

        with pytest.raises(ValueError, match="markers"):
            vr.splice("# Title\n\nno markers\n", "block")

    def test_committed_report_carries_the_markers(self):
        text = vr.REPORT_PATH.read_text(encoding="utf-8")
        assert vr.BEGIN_MARKER in text and vr.END_MARKER in text


class TestDuplicateIdsReportGenerator:
    def test_counts_in_file_duplicates_only(self, tmp_path):
        (tmp_path / "dupes.json").write_text(
            json.dumps(
                {"@graph": [{"@id": "estleg:A"}, {"@id": "estleg:A"}, {"@id": "estleg:B"}]}
            ),
            encoding="utf-8",
        )
        (tmp_path / "clean.json").write_text(
            json.dumps({"@graph": [{"@id": "estleg:C"}]}), encoding="utf-8"
        )
        in_file, cross_file = dup.collect(tmp_path)
        assert in_file == {"dupes.json": Counter({"estleg:A": 2})}
        assert cross_file["estleg:A"] == {"dupes.json"}

    def test_cross_file_reuse_is_reported_separately(self, tmp_path):
        for name in ("one.json", "two.json"):
            (tmp_path / name).write_text(
                json.dumps({"@graph": [{"@id": "estleg:Shared"}]}), encoding="utf-8"
            )
        in_file, cross_file = dup.collect(tmp_path)
        assert in_file == {}
        assert cross_file["estleg:Shared"] == {"one.json", "two.json"}

    def test_lfs_pointers_are_skipped(self, tmp_path):
        (tmp_path / "big.jsonld").write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:abc\n", encoding="utf-8"
        )
        assert dup.collect(tmp_path) == ({}, {})

    def test_report_no_longer_names_the_pytest_fixture(self):
        """`root_a_peep.json` exists only in a tmp_path inside the test suite."""
        text = dup.REPORT_PATH.read_text(encoding="utf-8")
        assert "root_a_peep.json" not in text
        assert "generate_duplicate_ids_report.py" in text


class TestAuditDoesNotMutateDocs:
    """#702: a test run must not rewrite the committed corpus report.

    `fix_all_issues.audit_duplicate_ids` wrote `REPO_ROOT/docs/
    DUPLICATE_IDS_REPORT.md` unconditionally, ignoring the `krr_dir` it was
    given. `tests/test_fix_all_issues.py` monkeypatches `KRR_DIR` to a
    `tmp_path` holding three fixture files, so every full-suite run replaced
    the real report with fixture output naming `root_a_peep.json` -- a file
    that exists only inside that test.
    """

    def test_audit_leaves_the_committed_report_alone(self, tmp_path, monkeypatch):
        from estleg import fix_all_issues

        before = dup.REPORT_PATH.read_text(encoding="utf-8")
        monkeypatch.setattr(fix_all_issues, "KRR_DIR", tmp_path)
        for name in ("root_a_peep.json", "other_peep.json"):
            (tmp_path / name).write_text(
                json.dumps({"@graph": [{"@id": "estleg:Shared"}]}), encoding="utf-8"
            )

        fix_all_issues.audit_duplicate_ids(tmp_path)

        assert dup.REPORT_PATH.read_text(encoding="utf-8") == before
