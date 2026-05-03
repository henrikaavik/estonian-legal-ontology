"""Subprocess tests for scripts/build_kov_registry.py CLI behaviour.

Drives the CLI as a child process so the test exercises the real
argument parsing, exit code, and stderr handling — not just the
underlying library functions.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_kov_registry.py"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"


def _stage_repo(tmp_path: Path, kov_slugs: list[str], curated_csv: str):
    """Create a fake repo tree under tmp_path so the CLI can run."""
    (tmp_path / "krr_outputs" / "regulations" / "kov").mkdir(parents=True)
    (tmp_path / "data" / "ehak").mkdir(parents=True)
    for slug in kov_slugs:
        (tmp_path / "krr_outputs" / "regulations" / "kov" / slug).mkdir()
    shutil.copy(MIN_MUNICIPALITIES,
                tmp_path / "data" / "ehak" / "municipalities.json")
    (tmp_path / "data" / "ehak" / "issuer_successor_map.csv").write_text(
        curated_csv, encoding="utf-8"
    )
    # The script imports kov_registry; symlink scripts/ so imports resolve.
    (tmp_path / "scripts").symlink_to(REPO_ROOT / "scripts")
    return tmp_path


def _run_cli(repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )


HEADER = "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"


class TestBuildKovRegistryCLI:
    def test_success_writes_issuers_json(self, tmp_path):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["tallinna_linnavolikogu", "abja_vallavolikogu"],
            curated_csv=(
                HEADER
                + "abja_vallavolikogu,0480,haldusreform-2017,RT I 21.06.2017 1\n"
            ),
        )
        result = _run_cli(repo)
        assert result.returncode == 0, result.stderr
        out_path = repo / "data" / "ehak" / "issuers.json"
        assert out_path.exists()
        rows = json.load(out_path.open())
        slugs = {r["slug"] for r in rows}
        assert slugs == {"tallinna_linnavolikogu", "abja_vallavolikogu"}
        # Stdout should mention the source breakdown
        assert "auto-match" in result.stdout

    def test_unmapped_issuer_exits_nonzero(self, tmp_path):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["obscure_vallavolikogu"],
            curated_csv=HEADER,  # empty — no curated mapping for obscure
        )
        result = _run_cli(repo)
        assert result.returncode != 0
        assert "obscure_vallavolikogu" in result.stderr

    def test_curated_unknown_ehak_exits_nonzero(self, tmp_path):
        # A curated row pointing at a nonexistent EHAK code surfaces here,
        # not at SHACL time.
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["abja_vallavolikogu"],
            curated_csv=(
                HEADER
                + "abja_vallavolikogu,9999,haldusreform-2017,evidence\n"
            ),
        )
        result = _run_cli(repo)
        assert result.returncode != 0
        assert "9999" in (result.stderr + result.stdout)
