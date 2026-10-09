"""Failure-path regressions from the wave 5 review."""

import json

import pytest

from estleg import build_hash_manifest as hm
from estleg import emit_release_changes as delta
from estleg import generate_amendment_history as amendments
from estleg import generate_kov_state_similarity as similarity
from estleg import generate_riigikogu_proceedings as rk
from estleg import link_curia_eu_legislation as curia
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


def test_retracted_official_curia_edge_is_not_relabelled_as_title_evidence():
    node = {"@id": "estleg:EUCJ_TEST", "@type": "estleg:EUCourtDecision",
            "estleg:celexNumber": "62020CJ0001", "rdfs:label": "No legislative citation",
            "estleg:interpretsEULaw": {"@id": "estleg:EU_32000L0001"},
            "estleg:derivationMethod": curia.METHOD_CELLAR}
    curia.link_decision_node(node, {"estleg:EU_32000L0001"}, {"62020CJ0001": []})
    assert "estleg:interpretsEULaw" not in node
    assert curia.METHOD_TITLE not in curia.derivation_methods(node)


def test_missing_curia_cache_does_not_downgrade_official_evidence():
    node = {"@id": "estleg:EUCJ_TEST", "@type": "estleg:EUCourtDecision",
            "estleg:celexNumber": "62020CJ0001", "rdfs:label": "No legislative citation",
            "estleg:interpretsEULaw": {"@id": "estleg:EU_32000L0001"},
            "estleg:derivationMethod": curia.METHOD_CELLAR}
    before = json.dumps(node, sort_keys=True)
    curia.link_decision_node(node, {"estleg:EU_32000L0001"}, {})
    assert json.dumps(node, sort_keys=True) == before


def test_empty_tabular_selection_cannot_overwrite_exports(tmp_path):
    with pytest.raises(ValueError, match="No selected corpus inputs"):
        tabular.build_tables(tmp_path)


def test_partial_curia_provenance_fetch_exits_unsuccessfully(monkeypatch):
    from types import SimpleNamespace
    from estleg import generate_eu_court_decisions as generator
    monkeypatch.setattr(generator, "parse_args", lambda: SimpleNamespace(fetch_interprets=True, refresh_interprets=False))
    monkeypatch.setattr(generator, "run_fetch_interprets", lambda **kw: {"failed_batches": 1})
    with pytest.raises(SystemExit) as exc:
        generator.main()
    assert exc.value.code == 2


def test_law_shards_treat_iri_as_data_not_a_path(tmp_path):
    from estleg_client import _shards
    doc = {"@context": {"estleg": "https://w3id.org/estleg/"}, "@graph": [
        {"@id": "estleg:../escape", "@type": "estleg:Law", "rdfs:label": "Law"}]}
    (tmp_path / _shards.COMBINED_NAME).write_text(json.dumps(doc))
    index = _shards.build_law_shards(tmp_path)
    entry = json.loads(index.read_text())["laws"][0]
    assert (tmp_path / _shards.SHARD_DIR / entry["file"]).parent == tmp_path / _shards.SHARD_DIR / "laws"
    assert not (tmp_path / _shards.SHARD_DIR / "escape.jsonl").exists()
    assert _shards.read_shard_nodes(tmp_path, entry["file"]) == doc["@graph"]


def test_client_rejects_shard_index_escape(tmp_path):
    from estleg_client import _shards
    (tmp_path / "secret.jsonl").write_text('{"secret": true}\n')
    with pytest.raises(ValueError, match="escapes corpus"):
        _shards.read_shard_nodes(tmp_path, "../secret.jsonl")


def test_missing_descriptor_cannot_erase_draft_subjects(tmp_path):
    from collections import Counter
    client = rk.RiigikoguClient(cache_dir=tmp_path, offline=True)
    with pytest.raises(RuntimeError, match="missing EuroVoc descriptor"):
        rk.eurovoc_codes({"descriptors": [{"edid": 10001}]}, client, Counter())


def test_default_client_cache_cannot_escape_via_release_tag(tmp_path, monkeypatch):
    from estleg_client import _corpus
    monkeypatch.setattr(_corpus, "default_cache_dir", lambda: tmp_path)
    for tag in ["../elsewhere", "/tmp/elsewhere", "release/version", "C:\\path"]:
        assert _corpus.default_corpus_dir(tag).parent == tmp_path / "corpus"


def test_offline_rebuild_does_not_consume_pending_ingest_change(tmp_path, monkeypatch):
    root = tmp_path / "krr_outputs"
    root.mkdir()
    source = tmp_path / "src/estleg/generator.py"
    source.parent.mkdir(parents=True)
    source.write_text("old code")
    steps = [{"name": "ingest", "script": "generator.py", "tier": integration.TIER_INGEST},
             {"name": "enrich", "script": "enrich.py", "depends_on": ["ingest"]}]
    monkeypatch.setattr(integration, "KRR_DIR", root)
    monkeypatch.setattr(integration, "STEPS", steps)
    baseline = tmp_path / "manifest.json"
    integration.refresh_hash_manifest(baseline)
    source.write_text("new code")
    plan = integration.plan_only_changed(["ingest", "enrich"], baseline)
    assert plan["selected"] == ["enrich"]
    integration.refresh_hash_manifest(baseline, previous=plan["current"], include_ingest=False)
    plan = integration.plan_only_changed(["ingest", "enrich"], baseline, include_ingest=True)
    assert plan["selected"] == ["ingest", "enrich"]
    integration.refresh_hash_manifest(baseline, include_ingest=True)
    assert integration.plan_only_changed(["ingest", "enrich"], baseline, include_ingest=True)["selected"] == []


@pytest.mark.parametrize("folder,filename,iterator", [
    ("riigikohus", "riigikohus_2020_peep.json", "iter_court_decisions"),
    ("eelnoud", "eelnoud_public_consultation_peep.json", "iter_drafts"),
    ("eurlex", "eurlex_directives_peep.json", "iter_eu_acts"),
])
def test_collection_readers_reject_partial_lfs_corpus(tmp_path, folder, filename, iterator):
    import estleg_client
    root = tmp_path / "krr_outputs"
    (root / folder).mkdir(parents=True)
    (root / "INDEX.json").write_text('{"laws": []}')
    (root / folder / filename).write_text('{"@graph": []}')
    missing = "riigikohus_2021_peep.json" if folder == "riigikohus" else filename.replace(".json", "_missing_peep.json")
    (root / folder / missing).write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n")
    with pytest.raises(ValueError, match="Git LFS pointer"):
        list(getattr(estleg_client, iterator)(root=tmp_path))


def test_regulation_collection_rejects_missing_indexed_input(tmp_path):
    import estleg_client
    root = tmp_path / "krr_outputs"
    folder = root / "regulations/riik"
    folder.mkdir(parents=True)
    (root / "INDEX.json").write_text('{"laws": []}')
    (folder / "REGULATIONS_RIIK_INDEX.json").write_text('{"files": ["missing_t1234_peep.json"]}')
    with pytest.raises(FileNotFoundError):
        list(estleg_client.iter_regulations(root=tmp_path))


def test_client_cannot_mix_release_versions_in_one_destination(tmp_path):
    from estleg_client import download
    (tmp_path / download.MANIFEST_NAME).write_text('{"version": "v-old"}')
    with pytest.raises(download.DownloadError, match="different corpus version"):
        download.fetch_corpus(version="v-new", dest=tmp_path)
