"""Tests for scripts/build_kov_registry.py CLI behaviour.

Drives the `build()` function directly with a tmp-path repo root,
which exercises the same code path as the CLI without subprocess
overhead or symlink fragility.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


from build_kov_registry import build


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"


def _stage_repo(
    tmp_path: Path,
    kov_slugs: list[str],
    curated_csv: str,
) -> Path:
    """Create a fake repo tree under tmp_path so build() can run."""
    (tmp_path / "krr_outputs" / "regulations" / "kov").mkdir(parents=True)
    (tmp_path / "data" / "ehak").mkdir(parents=True)
    for slug in kov_slugs:
        (tmp_path / "krr_outputs" / "regulations" / "kov" / slug).mkdir()
    shutil.copy(
        MIN_MUNICIPALITIES,
        tmp_path / "data" / "ehak" / "municipalities.json",
    )
    (tmp_path / "data" / "ehak" / "issuer_successor_map.csv").write_text(
        curated_csv, encoding="utf-8"
    )
    return tmp_path


HEADER = "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"


class TestBuildKovRegistry:
    def test_success_writes_issuers_json(self, tmp_path, capsys):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["tallinna_linnavolikogu", "abja_vallavolikogu"],
            curated_csv=(
                HEADER
                + "abja_vallavolikogu,0480,haldusreform-2017,RT I 21.06.2017 1\n"
            ),
        )
        rc = build(repo)
        assert rc == 0
        out_path = repo / "data" / "ehak" / "issuers.json"
        assert out_path.exists()
        rows = json.load(out_path.open())
        slugs = {r["slug"] for r in rows}
        assert slugs == {"tallinna_linnavolikogu", "abja_vallavolikogu"}
        captured = capsys.readouterr()
        assert "auto-match" in captured.out

    def test_unmapped_issuer_exits_nonzero(self, tmp_path, capsys):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["obscure_vallavolikogu"],
            curated_csv=HEADER,
        )
        rc = build(repo)
        assert rc != 0
        captured = capsys.readouterr()
        assert "obscure_vallavolikogu" in captured.err

    def test_curated_unknown_ehak_exits_nonzero(self, tmp_path, capsys):
        repo = _stage_repo(
            tmp_path,
            kov_slugs=["abja_vallavolikogu"],
            curated_csv=(
                HEADER
                + "abja_vallavolikogu,9999,haldusreform-2017,evidence\n"
            ),
        )
        rc = build(repo)
        assert rc != 0
        captured = capsys.readouterr()
        assert "9999" in (captured.err + captured.out)
