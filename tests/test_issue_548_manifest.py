"""#548 — committed dataset build manifest (in-repo half)."""

from __future__ import annotations

import json
from pathlib import Path

from estleg import write_build_manifest as wbm
from estleg.estleg_common import ONTOLOGY_VERSION

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
    assert manifest["datasetVersion"] == ONTOLOGY_VERSION
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


def test_metadata_catalog_urls_are_not_mutable_main() -> None:
    """#548: dcat/schema GitHub URLs must cite DATASET_CONTENT_SHA, not /main."""
    meta = json.loads((REPO / "metadata.jsonld").read_text(encoding="utf-8"))
    record = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sha = wbm.DATASET_CONTENT_SHA
    assert record["contentSha"] == sha
    assert record["catalogModified"]
    assert record["datasetVersion"] == ONTOLOGY_VERSION

    urls: list[str] = []

    def _walk(value: object) -> None:
        if isinstance(value, dict):
            ident = value.get("@id")
            if isinstance(ident, str):
                urls.append(ident)
            for item in value.values():
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(meta.get("dcat:distribution"))
    _walk(meta.get("schema:distribution"))
    github = [
        url
        for url in urls
        if "github.com/henrikaavik/estonian-legal-ontology" in url
    ]
    assert github, "expected catalog GitHub URLs"
    release_prefix = (
        "https://github.com/henrikaavik/estonian-legal-ontology"
        f"/releases/download/v{ONTOLOGY_VERSION}/"
    )
    flagship = wbm.github_release_asset_url("combined_ontology.jsonld.gz")
    assert flagship in github, flagship
    for url in github:
        assert not wbm.is_mutable_main_url(url), url
        assert sha in url or url.startswith(release_prefix), url


def test_void_data_dump_is_content_sha_not_main() -> None:
    text = (REPO / "krr_outputs" / "void.ttl").read_text(encoding="utf-8")
    assert "/raw/main/" not in text
    assert (
        wbm.DATASET_CONTENT_SHA in text
        or f"/releases/download/v{ONTOLOGY_VERSION}/" in text
    )


def test_validate_metadata_repro_pins_flags_main(tmp_path, monkeypatch) -> None:
    from estleg import validate_all as va

    va.reset()
    monkeypatch.setattr(va, "REPO_ROOT", tmp_path)
    doc = {
        "owl:versionInfo": ONTOLOGY_VERSION,
        "dcat:distribution": [
            {
                "dcat:downloadURL": {
                    "@id": (
                        "https://github.com/henrikaavik/estonian-legal-ontology"
                        "/raw/main/krr_outputs/combined_ontology.jsonld"
                    )
                }
            }
        ],
    }
    va.validate_metadata_repro_pins(doc)
    assert any("mutable /main" in err for err in va.errors), va.errors
