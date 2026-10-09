"""Regressions found while reviewing the data-gate repairs."""

import json

import pytest
import requests

from estleg import generate_annotations as annotations
from estleg import generate_draft_legislation as drafts
from estleg import migrate_uris
from estleg import rebuild_subcorpus_combined as combined
from estleg import validate_all


def write_graph(path, nodes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"@context": {}, "@graph": nodes}), encoding="utf-8")


def test_missing_schema_does_not_replace_existing_aggregate(tmp_path):
    write_graph(tmp_path / "eurlex/eurlex_directives_peep.json", [{"@id": "estleg:EU_1"}])
    output = tmp_path / "eurlex/eurlex_combined.jsonld"
    output.write_text("previous complete aggregate", encoding="utf-8")
    with pytest.raises(combined.RebuildError, match="schema"):
        combined.rebuild_subcorpus_combined("eurlex", tmp_path)
    assert output.read_text() == "previous complete aggregate"


def test_empty_live_feed_preserves_accumulated_draft_history(tmp_path, monkeypatch):
    directory = tmp_path / "eelnoud"
    for feed in drafts.RSS_FEEDS.values():
        write_graph(
            directory / f"eelnoud_{feed['phase'].lower()}_peep.json",
            [{"@id": "estleg:Draft_OLD", "@type": ["estleg:DraftLegislation"]}],
        )
    monkeypatch.setattr(drafts, "EELNOUD_DIR", directory)
    monkeypatch.setattr(drafts, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(drafts, "fetch_rss", lambda _url: [])
    assert drafts.main([]) == 0
    graph = json.loads((directory / "eelnoud_combined.jsonld").read_text())["@graph"]
    # #717 changes feeds into observations of an accumulated history. A draft
    # disappearing from today's feed must no longer erase the earlier record.
    assert "estleg:Draft_OLD" in {node["@id"] for node in graph}
    index = json.loads((directory / "EELNOUD_INDEX.json").read_text())
    assert index["total_drafts"] == 1


def test_failed_draft_fetch_preserves_existing_snapshot(tmp_path, monkeypatch):
    directory = tmp_path / "eelnoud"
    output = directory / "eelnoud_review_peep.json"
    write_graph(output, [{"@id": "estleg:Draft_EXISTING"}])
    before = output.read_bytes()
    monkeypatch.setattr(drafts, "EELNOUD_DIR", directory)
    monkeypatch.setattr(drafts, "REPO_ROOT", tmp_path)

    def unavailable(*_args, **_kwargs):
        raise requests.ConnectionError("unreachable")

    monkeypatch.setattr(drafts, "allowed_get", unavailable)
    with pytest.raises(RuntimeError, match="RSS"):
        drafts.main([])
    assert output.read_bytes() == before
    assert not (directory / "EELNOUD_INDEX.json").exists()


@pytest.mark.parametrize("body", ["<html><body>Maintenance</body></html>", "<rss/>"])
def test_non_feed_response_is_not_an_empty_draft_phase(monkeypatch, body):
    class Response:
        text = body

        def raise_for_status(self):
            pass

    monkeypatch.setattr(drafts, "allowed_get", lambda *_args, **_kwargs: Response())
    with pytest.raises(RuntimeError, match="RSS"):
        drafts.fetch_rss("https://eelnoud.valitsus.ee/feed")


def test_annotation_suffixes_cannot_collide_with_existing_ids():
    url = "https://www.oiguskantsler.ee/a.pdf"
    reserved = f"opinion_{annotations._short_hash(url)}_2"
    opinions = [
        annotations.Opinion("opinion", "A", url, "2024-01-01", (), "A"),
        annotations.Opinion("opinion", "B", url, "2024-01-01", (), "B"),
        annotations.Opinion(reserved, "C", url, "2024-01-01", (), "C"),
    ]
    result = annotations.disambiguate_duplicate_opinion_ids(opinions)
    assert len({annotations.annotation_iri(op.opinion_id) for op in result}) == 3
    assert result[2].opinion_id == reserved
    reversed_result = annotations.disambiguate_duplicate_opinion_ids(list(reversed(opinions)))
    assert {op.title: op.opinion_id for op in result} == {
        op.title: op.opinion_id for op in reversed_result
    }


def test_collision_plan_rejects_unreadable_own_input(tmp_path):
    (tmp_path / "law_peep.json").write_text("version https://git-lfs.github.com/spec/v1\n")
    with pytest.raises(RuntimeError, match="law_peep.json"):
        migrate_uris.plan_law_collision(
            {"from_prefix": "ROS", "to_prefix": "ROS_2", "keeper_slug": "keeper"},
            "law",
            tmp_path,
        )


def test_same_basename_in_different_directories_does_not_hide_id_collision(tmp_path, monkeypatch):
    paths = [tmp_path / "regulations/riik/shared_peep.json", tmp_path / "regulations/kov/shared_peep.json"]
    for path in paths:
        write_graph(path, [{"@id": "estleg:Collision", "@type": ["estleg:Act"]}])
    monkeypatch.setattr(validate_all, "discover_validation_files", lambda _root: paths)
    original = validate_all.validate_id_uniqueness

    class Checked(Exception):
        pass

    def check(ids, **kwargs):
        validate_all.reset()
        original(ids, **kwargs)
        assert "1 @id values are duplicated across files (semantic collisions)" in validate_all.errors
        raise Checked

    monkeypatch.setattr(validate_all, "validate_id_uniqueness", check)
    try:
        with pytest.raises(Checked):
            validate_all.main(["--krr-dir", str(tmp_path)])
    finally:
        validate_all.reset()
