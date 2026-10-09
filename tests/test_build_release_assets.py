"""#705: one DAG step builds, hashes and catalogues every release asset."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from estleg import build_release_assets as bra
from estleg import run_all_integration as rai
from estleg.estleg_common import COMBINED_JSONLD_TARGETS, ONTOLOGY_VERSION
from estleg.stamp_combined_dataset_heads import head_version, stamp_version_fields

RELEASE_URL = (
    "https://github.com/henrikaavik/estonian-legal-ontology/releases/download/"
    f"v{ONTOLOGY_VERSION}/"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jsonld(head: dict | None = None, *nodes: dict) -> str:
    graph = ([head] if head else []) + list(nodes)
    return json.dumps(
        {"@context": {"estleg": "https://w3id.org/estleg/"}, "@graph": graph},
        ensure_ascii=False,
        indent=2,
    )


def _metadata(names: list[str]) -> dict:
    return {
        "@context": {"dcat": "http://www.w3.org/ns/dcat#"},
        "@id": "https://w3id.org/estleg/dataset",
        "dcat:distribution": [
            {
                "@type": "dcat:Distribution",
                "dcterms:title": name,
                "dcat:downloadURL": {"@id": RELEASE_URL + name},
            }
            for name in names
        ]
        + [
            {
                "@type": "dcat:Distribution",
                "dcterms:title": "tree",
                "dcat:accessURL": {"@id": "https://github.com/x/tree/abc/krr_outputs"},
            }
        ],
    }


@pytest.fixture()
def tree(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    krr = repo / "krr_outputs"
    for relpath, _name in bra.GZIP_ASSETS:
        _write(repo / relpath, _jsonld(None, {"@id": f"estleg:{Path(relpath).stem}"}))
    for relpath, _name in bra.COPY_ASSETS:
        _write(repo / relpath, f"content of {relpath}\n")
    _combined_tree(krr)  # every COMBINED_JSONLD_TARGETS head carries the version
    metadata = repo / "metadata.jsonld"
    _write(
        metadata,
        json.dumps(_metadata(["combined_ontology.jsonld.gz", "curia_combined.jsonld.gz",
                              "not_built.jsonld.gz"]), indent=2) + "\n",
    )
    return {"repo": repo, "krr": krr, "release": krr / "release", "metadata": metadata}


def _build(tree: dict[str, Path], **kwargs) -> bra.BuildResult:
    return bra.build_release_assets(
        krr_dir=tree["krr"],
        release_dir=tree["release"],
        repo_root=tree["repo"],
        metadata_path=tree["metadata"],
        skip_rdf_dumps=True,
        skip_chunks=True,
        allow_unstamped=True,
        **kwargs,
    )


def test_gzip_is_deterministic(tmp_path: Path) -> None:
    src = tmp_path / "a.jsonld"
    src.write_text("x" * 10_000, encoding="utf-8")
    bra.gzip_deterministic(src, tmp_path / "1.gz")
    bra.gzip_deterministic(src, tmp_path / "2.gz")
    assert (tmp_path / "1.gz").read_bytes() == (tmp_path / "2.gz").read_bytes()
    assert gzip.decompress((tmp_path / "1.gz").read_bytes()) == src.read_bytes()


def test_build_writes_every_asset_and_a_v1_format_sha256sums(tree: dict[str, Path]) -> None:
    result = _build(tree)
    release = tree["release"]
    expected = {name for _, name in bra.GZIP_ASSETS} | {name for _, name in bra.COPY_ASSETS}
    expected.add(bra.METADATA_ASSET)
    assert {a.name for a in result.assets} == expected

    lines = (release / bra.SUMS_NAME).read_text(encoding="utf-8").splitlines()
    names = [line.split("  ", 1)[1] for line in lines]
    assert names == sorted(names, key=lambda n: n.encode())
    assert set(names) == expected
    for line in lines:
        digest, name = line.split("  ", 1)
        assert digest == hashlib.sha256((release / name).read_bytes()).hexdigest()
    assert "INDEX.json" in names and names.index("INDEX.json") < names.index("LICENSE")

    manifest = json.loads((release / bra.ASSET_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["skipped"] == {
        "rdf-dumps": "--skip-rdf-dumps",
        "named-graphs": "--skip-rdf-dumps (the laws slot needs a fresh combined_ontology.nq)",
        "chunks": "--skip-chunks",
        # #692: the fixture tree has no cached RT XML.
        "rt-xml": "no RT XML under data/riigiteataja/ (run generate_all_laws.py first)",
    }


def test_catalogue_gets_byte_size_and_checksum(tree: dict[str, Path]) -> None:
    result = _build(tree)
    meta = json.loads(tree["metadata"].read_text(encoding="utf-8"))
    assert meta["@context"]["spdx"] == bra.SPDX_NS
    by_title = {d["dcterms:title"]: d for d in meta["dcat:distribution"]}
    gz = tree["release"] / "combined_ontology.jsonld.gz"
    dist = by_title["combined_ontology.jsonld.gz"]
    assert dist["dcat:byteSize"] == {
        "@value": str(gz.stat().st_size), "@type": "xsd:nonNegativeInteger"}
    assert dist["spdx:checksum"]["spdx:algorithm"] == {
        "@id": "spdx:checksumAlgorithm_sha256",
        "@type": "spdx:ChecksumAlgorithm",
    }
    assert dist["spdx:checksum"]["spdx:checksumValue"]["@value"] == (
        hashlib.sha256(gz.read_bytes()).hexdigest())
    assert "dcat:byteSize" not in by_title["tree"]
    assert "dcat:byteSize" not in by_title["not_built.jsonld.gz"]
    assert sorted(result.catalogued) == ["combined_ontology.jsonld.gz", "curia_combined.jsonld.gz"]
    assert any("not_built" in w for w in result.catalogue_warnings)
    # The shipped metadata asset is the updated catalogue.
    assert (tree["release"] / "metadata.jsonld").read_bytes() == tree["metadata"].read_bytes()


def test_catalogue_warns_when_the_url_tag_is_another_release() -> None:
    meta = _metadata([])
    meta["dcat:distribution"].append(
        {"dcat:downloadURL": {"@id": RELEASE_URL.replace(f"v{ONTOLOGY_VERSION}", "v0.9.0")
                              + "x.gz"}}
    )
    asset = bra.Asset(name="x.gz", source="s", producer="p", bytes=3, sha256="0" * 64)
    catalogued, warnings = bra.update_catalogue(meta, {"x.gz": asset})
    assert catalogued == ["x.gz"]
    assert any("not v" in w for w in warnings)


def test_rebuild_is_byte_stable(tree: dict[str, Path]) -> None:
    _build(tree)
    first = {p.name: p.read_bytes() for p in tree["release"].iterdir() if p.is_file()}
    _build(tree)
    second = {p.name: p.read_bytes() for p in tree["release"].iterdir() if p.is_file()}
    assert first == second


def test_stale_assets_from_an_earlier_run_are_removed(tree: dict[str, Path]) -> None:
    _write(tree["release"] / "estleg_all.nq.gz", "stale")
    _build(tree)
    assert not (tree["release"] / "estleg_all.nq.gz").exists()
    assert "estleg_all.nq.gz" not in bra.parse_sums(tree["release"] / bra.SUMS_NAME)


def test_missing_input_fails(tree: dict[str, Path]) -> None:
    (tree["repo"] / "krr_outputs" / "curia" / "curia_combined.jsonld").unlink()
    with pytest.raises(bra.ReleaseAssetError, match="curia_combined"):
        _build(tree)


def test_lfs_pointer_input_fails(tree: dict[str, Path]) -> None:
    _write(tree["repo"] / "krr_outputs" / "INDEX.json",
           "version https://git-lfs.github.com/spec/v1\noid sha256:0\nsize 1\n")
    with pytest.raises(bra.ReleaseAssetError, match="LFS pointer"):
        _build(tree)


def test_verify_sums_detects_tampering(tree: dict[str, Path]) -> None:
    _build(tree)
    assert bra.verify_sums(tree["release"])["mismatched"] == []
    (tree["release"] / "LICENSE").write_text("changed", encoding="utf-8")
    (tree["release"] / "NOTICE").unlink()
    result = bra.verify_sums(tree["release"])
    assert [p.name for p in result["mismatched"]] == ["LICENSE"]
    assert [p.name for p in result["missing"]] == ["NOTICE"]


def test_release_manifest_hash_covers_the_assets(tree: dict[str, Path], monkeypatch) -> None:
    _build(tree)
    monkeypatch.setattr(rai, "RELEASE_ASSET_DIR", tree["release"])
    hashed = rai.hash_release_artifacts()
    assert any(path.endswith("release/combined_ontology.jsonld.gz") for path in hashed["files"])
    (tree["release"] / "LICENSE").write_text("changed", encoding="utf-8")
    hashed = rai.hash_release_artifacts()
    assert any("LICENSE (sha256 mismatch)" in m for m in hashed["missing"])


# ---------------------------------------------------------------------------
# Version stamps
# ---------------------------------------------------------------------------


def test_stamp_version_fields_overwrites() -> None:
    node = {"@id": "x", "owl:versionInfo": "0.11.0"}
    assert stamp_version_fields(node) is True
    assert node["owl:versionInfo"] == ONTOLOGY_VERSION
    assert node["owl:versionIRI"] == {"@id": f"https://w3id.org/estleg/{ONTOLOGY_VERSION}"}
    assert stamp_version_fields(node) is False


def _combined_tree(krr: Path, *, unstamped: str | None = None) -> None:
    for spec in COMBINED_JSONLD_TARGETS:
        relpath = str(spec["relpath"])
        head = {"@id": f"estleg:{Path(relpath).stem}_head", "@type": "owl:Ontology"}
        if relpath != unstamped:
            stamp_version_fields(head)
        _write(krr / relpath, _jsonld(head, {"@id": "estleg:N"}))


def test_version_check_passes_when_every_head_is_stamped(tmp_path: Path) -> None:
    krr = tmp_path / "krr"
    _combined_tree(krr)
    stamped, unstamped = bra.check_versions(krr)
    assert unstamped == []
    assert set(stamped) == {str(s["relpath"]) for s in COMBINED_JSONLD_TARGETS}


def test_unstamped_head_fails_the_build_unless_allowed(tree: dict[str, Path]) -> None:
    _combined_tree(tree["krr"], unstamped="eurlex/eurlex_combined.jsonld")
    # the combined_ontology/curia/eelnoud gz sources are the stamped files now
    before = {p: p.read_bytes() for p in tree["krr"].rglob("*_combined.jsonld")}
    with pytest.raises(bra.ReleaseAssetError, match="eurlex_combined"):
        bra.build_release_assets(
            krr_dir=tree["krr"], release_dir=tree["release"], repo_root=tree["repo"],
            metadata_path=tree["metadata"], skip_rdf_dumps=True, skip_chunks=True,
        )
    result = _build(tree)
    assert result.unstamped == ["eurlex/eurlex_combined.jsonld"]
    manifest = json.loads((tree["release"] / bra.ASSET_MANIFEST_NAME).read_text())
    assert manifest["unstampedHeads"] == ["eurlex/eurlex_combined.jsonld"]
    # The package step never rewrites a combined file the build already read.
    assert {p: p.read_bytes() for p in before} == before
    assert head_version(tree["krr"] / "eurlex" / "eurlex_combined.jsonld") is None


def test_release_step_is_last_and_reads_what_it_packages() -> None:
    step = next(s for s in rai.STEPS if s["name"] == "build_release_assets.py")
    assert rai.validate_dag(rai.STEPS, rai.COMMITTED_INPUTS)[-1] == step["name"]
    for relpath, _name in bra.GZIP_ASSETS:
        if relpath.startswith("krr_outputs/"):
            assert relpath.removeprefix("krr_outputs/") in step["reads"], relpath
    assert "../release/*" in step["writes"]
    assert bra.RELEASE_DIR.parent == bra.REPO_ROOT  # never inside krr_outputs/


def test_rdf_dumps_are_written_to_the_release_scratch_dir_not_over_committed_files(tmp_path):
    """#705: the release step must never overwrite krr_outputs/combined_ontology.{nt,nq,ttl}."""
    import json

    from estleg import build_release_assets as bra

    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    doc = {
        "@context": {"estleg": "https://w3id.org/estleg/", "rdfs": "http://www.w3.org/2000/01/rdf-schema#"},
        "@graph": [
            {"@id": "estleg:A_Map", "@type": ["estleg:Act"], "rdfs:label": "A"},
            {"@id": "estleg:A_Par_1", "@type": ["estleg:LegalProvision"], "rdfs:label": "§ 1"},
        ],
    }
    (krr / "combined_ontology.jsonld").write_text(json.dumps(doc), encoding="utf-8")
    for fmt in bra.RDF_DUMP_FORMATS:
        (krr / f"combined_ontology.{fmt}").write_text("committed\n", encoding="utf-8")
    out = tmp_path / "release" / bra.RDF_SCRATCH_SUBDIR
    info = bra.build_rdf_dumps(krr, batch_size=10, out_dir=out)
    assert info["triples"] > 0
    for fmt in bra.RDF_DUMP_FORMATS:
        assert (out / f"combined_ontology.{fmt}").is_file()
        assert (krr / f"combined_ontology.{fmt}").read_text(encoding="utf-8") == "committed\n"
    assert all(rel.startswith("release/") for rel, _ in bra.RDF_DUMP_ASSETS)


def test_named_graph_laws_slot_honours_the_fresh_dump_override(tmp_path):
    from estleg import serialize_named_graphs as sng

    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    (krr / "combined_ontology.nq").write_text("stale\n", encoding="utf-8")
    fresh = tmp_path / "release" / "rdf" / "combined_ontology.nq"
    fresh.parent.mkdir(parents=True)
    fresh.write_text("<s> <p> <o> .\n", encoding="utf-8")
    laws = next(slot for slot in sng.SLOTS if slot.name == "laws")
    assert sng.resolve_slot_files(laws, krr, {"combined_ontology.nq": fresh}) == [fresh]
    assert sng.resolve_slot_files(laws, krr) == [krr / "combined_ontology.nq"]
