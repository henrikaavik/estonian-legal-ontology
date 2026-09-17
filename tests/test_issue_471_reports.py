"""#471 — report/index/mapping/classification sidecars live under reports/."""

from __future__ import annotations

from pathlib import Path

from estleg import estleg_common, run_all_integration

REPO = Path(__file__).resolve().parent.parent
KRR = REPO / "krr_outputs"
REPORTS = KRR / "reports"
GITATTRIBUTES = REPO / ".gitattributes"
WORKFLOW = REPO / ".github" / "workflows" / "validate.yml"


def test_fourteen_sidecars_are_under_reports_not_krr_root() -> None:
    names = estleg_common.ROOT_REPORT_SIDECARS
    assert len(names) == 14
    for name in names:
        assert (REPORTS / name).is_file(), name
        assert not (KRR / name).exists(), name


def test_report_file_helper_uses_reports_subdir() -> None:
    path = estleg_common.report_file(KRR, "transposition_mapping.json")
    assert path == REPORTS / "transposition_mapping.json"


def test_dag_writes_point_at_reports() -> None:
    expected = {f"reports/{name}" for name in estleg_common.ROOT_REPORT_SIDECARS}
    writes = {
        pattern
        for step in run_all_integration.STEPS
        for pattern in step.get("writes") or []
        if pattern in expected
    }
    assert writes == expected


def test_lfs_and_ci_track_reports_paths() -> None:
    attrs = GITATTRIBUTES.read_text(encoding="utf-8")
    assert "krr_outputs/reports/similarity_index.json filter=lfs" in attrs
    assert "krr_outputs/reports/eurovoc_classification.json filter=lfs" in attrs
    assert "krr_outputs/similarity_index.json filter=lfs" not in attrs
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "krr_outputs/reports/similarity_index.json" in workflow
    assert "krr_outputs/reports/eurovoc_classification.json" in workflow


def test_dataset_manifest_stays_at_krr_root() -> None:
    assert (KRR / "dataset_build_manifest.json").is_file()
    assert not (REPORTS / "dataset_build_manifest.json").exists()
