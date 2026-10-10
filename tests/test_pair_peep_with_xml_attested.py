"""``pair_peep_with_xml`` pins every root carrying ``estleg:globalId`` to the
redaction ``fetch_content_hashes.json`` attests, including the ``estleg:Part``
roots of a multipart act's ``_osaN`` peeps and its ``_map`` peep."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg.estleg_common import FETCH_HASH_FILENAME, pair_peep_with_xml

GID = "109072026028"


def _peep(path: Path, types: list[str], gid: str | None = GID) -> Path:
    node: dict = {"@id": f"estleg:{path.stem}", "@type": types}
    if gid is not None:
        node["estleg:globalId"] = gid
    path.write_text(json.dumps({"@graph": [{"@id": "estleg:hdr", "@type": "owl:Ontology"}, node]}),
                    encoding="utf-8")
    return path


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, Path]:
    """``<tmp>/krr_outputs`` (peeps + hashes) and ``<tmp>/data/riigiteataja``
    holding a stale legacy ``seadustik.xml`` beside the attested current one."""
    krr = tmp_path / "krr_outputs"
    rt = tmp_path / "data" / "riigiteataja"
    krr.mkdir()
    rt.mkdir(parents=True)
    (rt / "seadustik.xml").write_text('<akt globaalID="105022014002"/>', encoding="utf-8")
    (rt / "seadustik__tid162500.xml").write_text(f'<akt globaalID="{GID}"/>', encoding="utf-8")
    (krr / FETCH_HASH_FILENAME).write_text(json.dumps({
        "seadustik": {"globalId": GID, "cacheFile": "data/riigiteataja/seadustik__tid162500.xml"},
    }), encoding="utf-8")
    return krr, rt


@pytest.mark.parametrize(
    ("stem", "types"),
    [
        ("seadustik_map", ["estleg:Act", "estleg:Law"]),
        ("seadustik_osa1", ["estleg:Part", "schema:Legislation"]),
        ("seadustik_osa2", ["estleg:Part", "schema:Legislation"]),
        ("seadustik", ["estleg:Act", "estleg:Law"]),
    ],
)
def test_multipart_roots_pair_to_the_attested_redaction(corpus, stem, types):
    krr, rt = corpus
    peep = _peep(krr / f"{stem}_peep.json", types)
    expected = rt / "seadustik__tid162500.xml"
    # Without a globalId lookup, and with the stale slug XML on disk.
    assert pair_peep_with_xml(peep, {}, data_dir=rt) == expected
    assert pair_peep_with_xml(peep, {}) == expected


def test_part_root_globalid_is_read_for_the_lookup(tmp_path):
    """``estleg:Part`` roots were skipped by the old type filter, so the
    parts fell through to the stale slug XML; the lookup now sees them."""
    rt = tmp_path / "rt"
    rt.mkdir()
    (rt / "seadustik.xml").write_text("<akt/>", encoding="utf-8")
    current = rt / "current.xml"
    current.write_text("<akt/>", encoding="utf-8")
    peep = _peep(tmp_path / "seadustik_osa1_peep.json", ["estleg:Part"])
    assert pair_peep_with_xml(peep, {GID: current}, data_dir=rt) == current


def test_attestation_for_a_different_redaction_is_ignored(corpus):
    krr, rt = corpus
    peep = _peep(krr / "seadustik_osa1_peep.json", ["estleg:Part"], gid="999")
    other = rt / "other.xml"
    other.write_text('<akt globaalID="999"/>', encoding="utf-8")
    assert pair_peep_with_xml(peep, {"999": other}, data_dir=rt) == other


def test_cache_file_resolves_by_basename_under_data_dir(tmp_path):
    """A manifest whose repo-relative ``cacheFile`` does not exist under the
    manifest's repo root still resolves by basename in ``data_dir``."""
    krr = tmp_path / "krr"
    rt = tmp_path / "elsewhere"
    krr.mkdir()
    rt.mkdir()
    xml = rt / "seadustik__tid1.xml"
    xml.write_text("<akt/>", encoding="utf-8")
    (krr / FETCH_HASH_FILENAME).write_text(json.dumps({
        "seadustik": {"globalId": GID, "cacheFile": "data/riigiteataja/seadustik__tid1.xml"},
    }), encoding="utf-8")
    peep = _peep(krr / "seadustik_osa3_peep.json", ["estleg:Part"])
    assert pair_peep_with_xml(peep, {}, data_dir=rt) == xml


def test_without_globalid_the_slug_fallback_is_unchanged(corpus):
    krr, rt = corpus
    peep = _peep(krr / "seadustik_osa1_peep.json", ["owl:Ontology", "estleg:Law"], gid=None)
    assert pair_peep_with_xml(peep, {}, data_dir=rt) == rt / "seadustik.xml"


def test_unreadable_manifest_falls_back(corpus):
    krr, rt = corpus
    (krr / FETCH_HASH_FILENAME).write_text("{not json", encoding="utf-8")
    peep = _peep(krr / "seadustik_osa1_peep.json", ["estleg:Part"])
    current = rt / "seadustik__tid162500.xml"
    assert pair_peep_with_xml(peep, {GID: current}, data_dir=rt) == current
