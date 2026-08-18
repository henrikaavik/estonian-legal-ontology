"""#548 — committed dataset build manifest (in-repo half)."""

from __future__ import annotations

import json
from pathlib import Path

from estleg import write_build_manifest as wbm

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "krr_outputs" / "dataset_build_manifest.json"
RELEASE = REPO / "docs" / "RELEASE.md"

SAMPLE_KEYS = {
    "perekonnaseadus",
    "karistusseadustik_osa1",
    "eesti_vabariigi_pohiseadus",
}


def test_build_manifest_returns_version_and_sample_keys() -> None:
    manifest = wbm.build_manifest()
    assert manifest["datasetVersion"] == "0.11.0"
    assert SAMPLE_KEYS <= set(manifest["kehtivBySample"])


def test_committed_manifest_exists_with_sha_and_pks_kehtiv() -> None:
    assert MANIFEST.is_file(), "krr_outputs/dataset_build_manifest.json is the #548 record"
    record = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sha = record.get("gitSha")
    assert isinstance(sha, str) and sha
    assert sha == "unknown" or len(sha) >= 40
    assert "perekonnaseadus" in record["kehtivBySample"]
    assert record["kehtivBySample"]["perekonnaseadus"]


def test_release_md_mentions_dataset_build_manifest() -> None:
    text = RELEASE.read_text(encoding="utf-8")
    assert "dataset_build_manifest" in text
