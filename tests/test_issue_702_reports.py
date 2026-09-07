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


class TestChecksTolerateAPartialCorpus:
    """#702: `--check` must not fail the build for an LFS-materialisation gap.

    CI's `json-validation` job pulls a subset of the Git-LFS artifacts, and both
    generators skip LFS pointers. A partial checkout therefore scans fewer files
    and legitimately produces different numbers. Comparing regardless reported a
    stale report when the report was fine -- which is how the first version of
    these CI steps failed.
    """

    def test_validation_report_reads_back_its_file_count(self):
        block = vr.render_block(
            {"files": 26961, "errors": 122, "warnings": 2}, Counter()
        )
        assert vr._files_validated(block) == 26961

    def test_validation_report_file_count_absent_is_none(self):
        assert vr._files_validated("no table here") is None

    def test_duplicate_report_reads_back_its_scanned_count(self):
        block = dup.render({}, {}, 27012)
        assert dup._scanned(block) == 27012

    def test_duplicate_report_scanned_count_absent_is_none(self):
        assert dup._scanned("# Duplicate `@id` Report\n") is None

    def test_duplicate_check_skips_when_the_corpus_differs(self, tmp_path, monkeypatch, capsys):
        """A smaller corpus must exit 0 with an explicit notice, not fail."""
        (tmp_path / "one.json").write_text(
            json.dumps({"@graph": [{"@id": "estleg:A"}, {"@id": "estleg:A"}]}),
            encoding="utf-8",
        )
        report = tmp_path / "REPORT.md"
        # A report recorded against a much larger corpus than we can see now.
        report.write_text(dup.render({}, {}, 27012), encoding="utf-8")
        monkeypatch.setattr(dup, "REPORT_PATH", report)
        monkeypatch.setattr(dup, "KRR_DIR", tmp_path)
        monkeypatch.setattr(dup, "iter_corpus_files", lambda krr_dir=tmp_path: [tmp_path / "one.json"])
        monkeypatch.setattr(dup, "collect", lambda krr_dir=tmp_path: ({}, {}))

        assert dup.main(["--check"]) == 0
        assert "LFS-materialisation difference" in capsys.readouterr().out

    def test_duplicate_check_still_fails_on_a_real_drift(self, tmp_path, monkeypatch, capsys):
        """Same corpus size, different content -> that is a stale report."""
        report = tmp_path / "REPORT.md"
        report.write_text(dup.render({}, {}, 1), encoding="utf-8")
        monkeypatch.setattr(dup, "REPORT_PATH", report)
        monkeypatch.setattr(dup, "iter_corpus_files", lambda krr_dir=None: [tmp_path / "one.json"])
        monkeypatch.setattr(
            dup, "collect", lambda krr_dir=None: ({"one.json": Counter({"estleg:A": 2})}, {})
        )

        assert dup.main(["--check"]) == 1
        assert "stale" in capsys.readouterr().out


class TestMtimeDerivedRowsAreExcluded:
    """#702: the freshness rule counts by mtime, so --check must ignore it.

    `older than at least one canonical source file` compares filesystem
    timestamps, not content. Regenerating a T-Box artifact makes it newer than
    an aggregate that embeds it, and a fresh checkout assigns mtimes in
    arbitrary order -- so this row moved 3 -> 4 (and the total 122 -> 123) with
    a clean `git status`, which made --check report a stale report twice over.
    """

    def _block(self, freshness: int, duplicates: int = 38):
        return vr.render_block(
            {"files": 26961, "errors": freshness + duplicates, "warnings": 2},
            Counter(
                {
                    "Duplicate @id within file": duplicates,
                    "older than at least one canonical source file": freshness,
                }
            ),
        )

    def test_freshness_drift_alone_is_not_treated_as_stale(self):
        assert vr._comparable(self._block(3)) == vr._comparable(self._block(4))

    def test_a_content_category_drifting_is_still_caught(self):
        assert vr._comparable(self._block(3)) != vr._comparable(
            self._block(3, duplicates=39)
        )

    def test_the_committed_block_still_shows_the_real_totals(self):
        """Excluded from comparison, but still published for the reader."""
        text = vr.REPORT_PATH.read_text(encoding="utf-8")
        block = text[text.find(vr.BEGIN_MARKER) : text.find(vr.END_MARKER)]
        assert "| Errors |" in block
        assert "older than at least one canonical source file" in block
