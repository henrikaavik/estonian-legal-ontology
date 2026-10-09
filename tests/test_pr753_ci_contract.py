import os
import subprocess
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_required_pytest_check_covers_every_matrix_result(result):
    jobs = yaml.safe_load((REPO / ".github/workflows/validate.yml").read_text())["jobs"]
    gate = jobs["pytest"]  # Exact check context required by main's branch protection.
    assert "strategy" not in gate
    assert gate["needs"] == "pytest-matrix"
    assert "always()" in gate["if"]
    step = gate["steps"][0]
    assert step["env"]["MATRIX_RESULT"] == "${{ needs.pytest-matrix.result }}"
    run = subprocess.run(["bash", "-e", "-c", step["run"]],
                         env={**os.environ, "MATRIX_RESULT": result}, capture_output=True)
    assert (run.returncode == 0) == (result == "success")


def test_corpus_gates_fail_when_downstream_sync_fails(tmp_path):
    runner = tmp_path / "python"
    runner.write_text('#!/bin/sh\ncase "$*" in *validate_seadusloome_sync.py*) exit 1;; esac\n')
    runner.chmod(0o755)
    result = subprocess.run(["make", "-f", str(REPO / "Makefile"), "corpus-gates",
                             f"PYTHON={runner}"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0, "Downstream sync failure was not checked"
    assert "validate_seadusloome_sync.py" in result.stdout


def test_client_type_marker_is_present_for_wheel_packaging():
    assert (REPO / "estleg_client/py.typed").is_file()
