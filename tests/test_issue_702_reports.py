"""#702: the two docs reports are generated and CI-checkable, not hand-written.

`VALIDATION_REPORT.md` published "Errors: 0 / PASSED" against a run dated
2026-05-26 while `json-validation` had been red on every `main` run since
v1.0.0, and `DUPLICATE_IDS_REPORT.md` was rendered from a pytest fixture
(`root_a_peep.json`, a `tmp_path` artefact) rather than from the corpus.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

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


class TestChecksRequireComparableCorpora:
    """Changed input counts must never bypass the report checks (#702)."""

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

    @pytest.mark.parametrize("measured_files", [0, 1, 3])
    def test_validation_check_rejects_changed_file_count(
        self, measured_files, tmp_path, monkeypatch, capsys
    ):
        report = tmp_path / "REPORT.md"
        report.write_text(vr.render_block({"files": 2, "errors": 0, "warnings": 0}, Counter()))
        monkeypatch.setattr(vr, "REPORT_PATH", report)
        monkeypatch.setattr(
            vr, "run_validator", lambda: (
                "  ERROR: new.json: Duplicate @id within file: estleg:A\n"
                f"Files validated: {measured_files}\nErrors: 1\nWarnings: 0\n"
            )
        )
        assert vr.main(["--check"]) == 1
        assert "Cannot verify" in capsys.readouterr().out

    @pytest.mark.parametrize("errors", [0, 1])
    def test_validation_check_compares_content_at_same_size(
        self, errors, tmp_path, monkeypatch
    ):
        report = tmp_path / "REPORT.md"
        report.write_text(vr.render_block({"files": 2, "errors": 0, "warnings": 0}, Counter()))
        monkeypatch.setattr(vr, "REPORT_PATH", report)
        output = "  ERROR: new.json: Duplicate @id within file: estleg:A\n" if errors else ""
        output += f"Files validated: 2\nErrors: {errors}\nWarnings: 0\n"
        monkeypatch.setattr(vr, "run_validator", lambda: output)
        assert vr.main(["--check"]) == errors

    @pytest.mark.parametrize("change", ["add", "remove", "lfs_pointer", "edit", "none"])
    def test_duplicate_check_detects_changed_inputs(
        self, change, tmp_path, monkeypatch
    ):
        """Exercise discovery and collection on real files, including the added-file bypass."""
        corpus = tmp_path / "krr_outputs"
        corpus.mkdir()
        clean = json.dumps({"@graph": [{"@id": "estleg:A"}]})
        (corpus / "one.json").write_text(clean)
        second = corpus / "two.json"
        second.write_text(json.dumps({"@graph": [{"@id": "estleg:B"}]}))
        report = tmp_path / "REPORT.md"
        report.write_text(dup.render(*dup.collect(corpus), len(dup.iter_corpus_files(corpus))))
        monkeypatch.setattr(dup, "REPORT_PATH", report)
        monkeypatch.setattr(dup, "KRR_DIR", corpus)
        duplicates = json.dumps({"@graph": [{"@id": "estleg:C"}, {"@id": "estleg:C"}]})
        if change == "add":
            (corpus / "new.json").write_text(duplicates)
        elif change == "remove":
            second.unlink()
        elif change == "lfs_pointer":
            second.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")
        elif change == "edit":
            second.write_text(duplicates)
        assert dup.main(["--check"]) == (0 if change == "none" else 1)


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
