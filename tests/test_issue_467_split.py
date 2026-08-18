"""#467 — release build is INDEX + combined only; repairs are emergency-only."""

from __future__ import annotations

import inspect
from pathlib import Path

from estleg import build_release_artifacts as build
from estleg import fix_all_issues, run_all_integration

REPO = Path(__file__).resolve().parent.parent


def test_rebuild_step_is_build_release_artifacts() -> None:
    names = [step["name"] for step in run_all_integration.STEPS]
    assert names[-1] == "build_release_artifacts.py"
    assert "fix_all_issues.py" not in names


def test_release_mains_do_not_invoke_legacy_repairs() -> None:
    for source in (
        inspect.getsource(fix_all_issues.main),
        inspect.getsource(build.main),
    ):
        assert "process_all_json_files" not in source
        assert "fix_aos_naming" not in source
        assert "fix_notariaadiseadus_naming" not in source
        assert "fix_intra_file_duplicates" not in source
        assert "generate_index" in source
        assert "generate_combined_jsonld" in source


def test_rename_passes_are_gone() -> None:
    assert not hasattr(fix_all_issues, "fix_aos_naming")
    assert not hasattr(fix_all_issues, "fix_notariaadiseadus_naming")
    text = (REPO / "src" / "estleg" / "fix_all_issues.py").read_text(encoding="utf-8")
    assert "asjaigusseadus" not in text
    assert "notari_seadus_peep" not in text


def test_legacy_repairs_require_yes() -> None:
    sys_path = REPO / "scripts" / "archive"
    assert (sys_path / "legacy_repairs.py").is_file()
    from legacy_repairs import main as repairs_main

    assert repairs_main([]) == 2


def test_builder_reexports_merge_primitive() -> None:
    assert build.generate_combined_jsonld is fix_all_issues.generate_combined_jsonld
    assert build.generate_index is fix_all_issues.generate_index
    assert callable(build._merge_node_properties)
