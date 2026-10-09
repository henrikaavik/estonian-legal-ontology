"""Failure-path regressions from the wave 5 review."""

import json

import pytest

from estleg import build_hash_manifest as hm
from estleg import emit_release_changes as delta
from estleg import generate_amendment_history as amendments
from estleg import generate_kov_state_similarity as similarity
from estleg import generate_riigikogu_proceedings as rk
from estleg import run_all_integration as integration
from estleg import serialize_tabular as tabular


def test_offline_missing_listing_cannot_look_like_an_empty_parliament(tmp_path):
    client = rk.RiigikoguClient(cache_dir=tmp_path, offline=True)
    with pytest.raises(RuntimeError, match="incomplete|missing"):
        rk.load_draft_listing(client)


def test_incremental_plan_rebuilds_after_generator_change(tmp_path, monkeypatch):
    root = tmp_path / "krr_outputs"
    root.mkdir()
    source = tmp_path / "src/estleg/generator.py"
    source.parent.mkdir(parents=True)
    source.write_text("old code")
    steps = [{"name": "generate", "script": "generator.py", "reads": [], "writes": []}]
    monkeypatch.setattr(integration, "KRR_DIR", root)
    baseline = tmp_path / "manifest.json"
    hm.write_manifest(hm.build_manifest(steps, root), baseline)
    source.write_text("new code")
    plan = integration.plan_only_changed(["generate"], baseline, steps=steps)
    assert plan["selected"] == ["generate"]


def test_incremental_plan_rebuilds_after_recipe_change(tmp_path, monkeypatch):
    root = tmp_path / "krr_outputs"
    root.mkdir()
    old = [{"name": "generate", "script": "generator.py", "args": ["--old"]}]
    new = [{"name": "generate", "script": "generator.py", "args": ["--new"]}]
    baseline = tmp_path / "manifest.json"
    hm.write_manifest(hm.build_manifest(old, root), baseline)
    monkeypatch.setattr(integration, "KRR_DIR", root)
    assert integration.plan_only_changed(["generate"], baseline, steps=new)["selected"] == ["generate"]


def test_release_delta_rejects_missing_indexed_law(tmp_path):
    snap = delta.Snapshot(tmp_path, delta.WORKTREE)
    entry = delta.LawEntry("law", ["law_peep.json"], False)
    with pytest.raises(ValueError, match="law_peep.json"):
        delta._load_docs(snap, entry, [])


@pytest.mark.parametrize("body", ["not json", '{"@graph": {}}'])
def test_tabular_export_does_not_silently_drop_corrupt_input(tmp_path, body):
    path = tmp_path / "law_peep.json"
    path.write_text(body)
    with pytest.raises(ValueError, match="law_peep.json"):
        tabular.project_files([path])


def test_tabular_version_input_cannot_be_a_pointer(tmp_path):
    path = tmp_path / "versions.jsonld"
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n")
    with pytest.raises(ValueError, match="versions.jsonld"):
        tabular.load_version_layer([path])


def test_manifest_cannot_silently_omit_unreadable_input(tmp_path, monkeypatch):
    (tmp_path / "x.json").write_text(json.dumps({}))
    def fail(_path):
        raise OSError("unreadable")
    monkeypatch.setattr(hm, "hash_file", fail)
    with pytest.raises(OSError):
        hm.build_manifest([{"reads": ["*.json"]}], tmp_path)


def test_unreadable_version_history_cannot_be_treated_as_absent(tmp_path):
    (tmp_path / "law.jsonld").write_text("not json")
    with pytest.raises(ValueError, match="law.jsonld"):
        amendments.load_versions_by_date(tmp_path, "law")


def test_similarity_cannot_drop_a_corrupt_act(tmp_path):
    path = tmp_path / "law_peep.json"
    path.write_text("not json")
    with pytest.raises(ValueError, match="law_peep.json"):
        similarity.read_acts([path], "state")
