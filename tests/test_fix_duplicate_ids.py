"""Tests for scripts/fix_duplicate_ids.py (#279, #280).

Covers the two correctness fixes in the cross-file @id deduplicator:

* **Idempotency (#279a)** — running the dedup twice yields the same corpus
  as running it once. The old cluster branch re-prepended a file's slug on a
  re-run (``Cluster_alpha_seadus_X`` → ``Cluster_alpha_seadus_alpha_seadus_X``)
  because its old-prefix stripping only looked at ``_Par_`` siblings.
* **Range-osa skip (#279b)** — range-form osa files (``..._osa6-13_peep.json``)
  are NOT remapped, because ``Osa6-13`` would produce a hyphenated, invalid
  IRI. They are recorded for human review instead.
* **Cross-file abbreviation collision** — two different laws sharing a short
  ``_Par_`` / ``Cluster_`` / ``LegalProvision_`` id each get a unique slug.
* **Reference rewrite** — cross-file references to a remapped id are updated
  in the *other* files.

All tests use synthetic fixtures under ``tmp_path``; the real corpus is never
touched (``KRR_DIR`` is monkeypatched per test).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fix_duplicate_ids as fdi  # noqa: E402

# Canonical "fully-migrated IRI" grammar from migrate_uris.NEW_IRI_FORMAT_RE
# (hyphen is NOT allowed). We re-declare it here so the test asserts the same
# invariant the migration tooling enforces, without importing the heavier
# migrate_uris module.
NEW_IRI_FORMAT_RE = re.compile(
    r"^estleg:[A-Za-zÄÖÕÜäöõüŠŽšž][A-Za-z0-9_ÄÖÕÜäöõüŠŽšž]*$"
)


# ── Helpers ────────────────────────────────────────────────────────────────


def write_peep(path: Path, graph: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"@graph": graph}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_graph(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["@graph"]


def all_ids(krr_dir: Path) -> list[str]:
    ids: list[str] = []
    for f in sorted(krr_dir.glob("*_peep.json")):
        for node in read_graph(f):
            if "@id" in node:
                ids.append(node["@id"])
    return sorted(ids)


def run_pipeline(krr_dir: Path) -> set[str]:
    """Run detect → build → apply → cross-ref once. Returns skipped range files."""
    dupes = fdi.detect_duplicates()
    skipped: set[str] = set()
    remap = fdi.build_remap_table(dupes, skipped_range_files=skipped)
    for fname, id_map in remap.items():
        fdi.apply_remap_to_file(str(krr_dir / fname), id_map)
    fdi.update_cross_references(remap)
    return skipped


# ── #279a: idempotency ───────────────────────────────────────────────────────


def test_run_twice_equals_run_once_cluster_and_par(tmp_path, monkeypatch):
    """The full dedup pipeline is idempotent (run-twice == run-once).

    Regression for the cluster double-prefix bug (#279a): two different laws
    share both a short ``_Par_`` id and a ``Cluster_`` id. After the first
    run the ids are disambiguated with each file's slug; a second run must be
    a no-op rather than re-prepending the slug.
    """
    monkeypatch.setattr(fdi, "KRR_DIR", tmp_path)
    write_peep(
        tmp_path / "alpha_seadus_peep.json",
        [{"@id": "estleg:AS_Par_1"}, {"@id": "estleg:Cluster_Shared"}],
    )
    write_peep(
        tmp_path / "beeta_seadus_peep.json",
        [{"@id": "estleg:AS_Par_1"}, {"@id": "estleg:Cluster_Shared"}],
    )

    run_pipeline(tmp_path)
    after_first = all_ids(tmp_path)

    run_pipeline(tmp_path)
    after_second = all_ids(tmp_path)

    assert after_first == after_second, (
        "dedup must be idempotent; second run changed the corpus"
    )
    # No id grew a doubled slug like Cluster_alpha_seadus_alpha_seadus_*.
    for nid in after_second:
        assert "alpha_seadus_alpha_seadus" not in nid
        assert "beeta_seadus_beeta_seadus" not in nid
    # And every resulting id is a structurally valid estleg IRI.
    for nid in after_second:
        assert NEW_IRI_FORMAT_RE.match(nid), f"invalid IRI minted: {nid!r}"


def test_cluster_already_carrying_slug_is_noop(tmp_path, monkeypatch):
    """A cluster id that already embeds this file's slug is left unchanged.

    Directly pins the no-op guard: when the cluster's local part already
    starts with the file's new prefix, ``build_remap_table`` must NOT emit a
    rename (which would double the slug).
    """
    monkeypatch.setattr(fdi, "KRR_DIR", tmp_path)
    write_peep(
        tmp_path / "verylongslugname_seadus_peep.json",
        [
            {"@id": "estleg:VLS_Par_1"},
            {"@id": "estleg:Cluster_verylongslugname_seadus_X"},
        ],
    )
    write_peep(
        tmp_path / "other_seadus_peep.json",
        [
            {"@id": "estleg:OTH_Par_1"},
            {"@id": "estleg:Cluster_verylongslugname_seadus_X"},
        ],
    )

    dupes = fdi.detect_duplicates()
    remap = fdi.build_remap_table(dupes)

    # The file whose slug is already embedded gets no cluster rename.
    vls_map = remap.get("verylongslugname_seadus_peep.json", {})
    assert "estleg:Cluster_verylongslugname_seadus_X" not in vls_map

    # The other file strips the embedded slug and prepends its own — exactly
    # once, never stacking the two slugs.
    oth_new = remap["other_seadus_peep.json"]["estleg:Cluster_verylongslugname_seadus_X"]
    assert oth_new == "estleg:Cluster_other_seadus_X"
    assert "verylongslugname_seadus_verylongslugname_seadus" not in oth_new


# ── #279b: range-osa files are skipped ────────────────────────────────────────


def test_range_osa_file_is_skipped_and_recorded(tmp_path, monkeypatch):
    """Range-form osa files (``osa6-13``) are skipped, not remapped (#279b).

    A single-integer osa sibling still gets the ``Osa<N>`` disambiguation,
    but the range file produces NO rename (it would be a hyphenated, invalid
    IRI) and its name is recorded in ``skipped_range_files``.
    """
    monkeypatch.setattr(fdi, "KRR_DIR", tmp_path)
    write_peep(
        tmp_path / "asjaoigusseadus_osa1_peep.json",
        [{"@id": "estleg:AOS_Par_70"}],
    )
    write_peep(
        tmp_path / "asjaoigusseadus_osa6-13_peep.json",
        [{"@id": "estleg:AOS_Par_70"}],
    )

    dupes = fdi.detect_duplicates()
    skipped: set[str] = set()
    remap = fdi.build_remap_table(dupes, skipped_range_files=skipped)

    # The range file is recorded and gets NO rename.
    assert "asjaoigusseadus_osa6-13_peep.json" in skipped
    assert remap.get("asjaoigusseadus_osa6-13_peep.json", {}) == {}

    # The single-integer osa1 file IS disambiguated to a valid IRI.
    osa1_new = remap["asjaoigusseadus_osa1_peep.json"]["estleg:AOS_Par_70"]
    assert osa1_new == "estleg:AOS_Osa1_Par_70"
    assert NEW_IRI_FORMAT_RE.match(osa1_new)

    # No remap value anywhere contains a hyphenated osa segment.
    for id_map in remap.values():
        for new_id in id_map.values():
            assert NEW_IRI_FORMAT_RE.match(new_id), f"invalid IRI: {new_id!r}"


def test_is_range_osa_file_helper():
    """The range-form detector matches ``osa6-13`` but not single-integer osas."""
    assert fdi._is_range_osa_file("asjaoigusseadus_osa6-13_peep.json")
    assert not fdi._is_range_osa_file("asjaoigusseadus_osa1_peep.json")
    assert not fdi._is_range_osa_file("asjaoigusseadus_osa11_peep.json")
    # Word fragments embedding "osa" must not be mistaken for a range.
    assert not fdi._is_range_osa_file("osalemise_seadus_peep.json")


def test_single_osa_regex_rejects_range_form():
    """``SINGLE_OSA_RE`` extracts a single integer only, never a range."""
    assert fdi.SINGLE_OSA_RE.search("law_osa11_peep").group(1) == "11"
    # Range form does not yield a single-integer match.
    assert fdi.SINGLE_OSA_RE.search("law_osa6-13_peep") is None


# ── Cross-file abbreviation collision ─────────────────────────────────────────


def test_cross_file_par_collision_gets_unique_prefixes(tmp_path, monkeypatch):
    """Two different laws sharing a short ``_Par_`` id each get a unique slug."""
    monkeypatch.setattr(fdi, "KRR_DIR", tmp_path)
    write_peep(tmp_path / "aktsiisiseadus_peep.json", [{"@id": "estleg:AS_Par_5"}])
    write_peep(tmp_path / "abieluseadus_peep.json", [{"@id": "estleg:AS_Par_5"}])

    run_pipeline(tmp_path)

    aktsiisi_ids = {n["@id"] for n in read_graph(tmp_path / "aktsiisiseadus_peep.json")}
    abielu_ids = {n["@id"] for n in read_graph(tmp_path / "abieluseadus_peep.json")}

    # The collision is resolved: the two files no longer share the id.
    assert aktsiisi_ids.isdisjoint(abielu_ids)
    # Each id encodes its own file slug and stays a valid IRI.
    assert any("aktsiisiseadus" in nid for nid in aktsiisi_ids)
    assert any("abieluseadus" in nid for nid in abielu_ids)
    for nid in aktsiisi_ids | abielu_ids:
        assert NEW_IRI_FORMAT_RE.match(nid), f"invalid IRI: {nid!r}"

    # The whole corpus now has no cross-file duplicate ids.
    assert fdi.detect_duplicates() == {}


def test_cross_file_legalprovision_collision(tmp_path, monkeypatch):
    """``estleg:LegalProvision_<short>`` shared across laws is disambiguated."""
    monkeypatch.setattr(fdi, "KRR_DIR", tmp_path)
    write_peep(
        tmp_path / "aktsiisiseadus_peep.json",
        [{"@id": "estleg:AS_Par_1"}, {"@id": "estleg:LegalProvision_AS"}],
    )
    write_peep(
        tmp_path / "abieluseadus_peep.json",
        [{"@id": "estleg:AS_Par_1"}, {"@id": "estleg:LegalProvision_AS"}],
    )

    run_pipeline(tmp_path)

    assert fdi.detect_duplicates() == {}
    all_provision_ids = [nid for nid in all_ids(tmp_path) if "LegalProvision_" in nid]
    # Two distinct LegalProvision ids now, both valid.
    assert len(set(all_provision_ids)) == 2
    for nid in all_provision_ids:
        assert NEW_IRI_FORMAT_RE.match(nid)


# ── Reference rewrite ─────────────────────────────────────────────────────────


def test_reference_rewrite_updates_other_files(tmp_path, monkeypatch):
    """A cross-file reference to a remapped id is rewritten in the other file.

    ``aktsiisiseadus`` and ``abieluseadus`` collide on ``estleg:AS_Par_1``;
    after disambiguation, a third file that referenced the (now-remapped)
    ``aktsiisiseadus`` id must point at the NEW id.
    """
    monkeypatch.setattr(fdi, "KRR_DIR", tmp_path)
    write_peep(tmp_path / "aktsiisiseadus_peep.json", [{"@id": "estleg:AS_Par_1"}])
    write_peep(tmp_path / "abieluseadus_peep.json", [{"@id": "estleg:AS_Par_1"}])
    # Consumer references the aktsiisiseadus provision by its OLD id.
    write_peep(
        tmp_path / "consumer_peep.json",
        [
            {
                "@id": "estleg:Consumer",
                "estleg:references": [{"@id": "estleg:AS_Par_1"}],
            }
        ],
    )

    run_pipeline(tmp_path)

    # Find the new id that the aktsiisiseadus file ended up with.
    aktsiisi_ids = {n["@id"] for n in read_graph(tmp_path / "aktsiisiseadus_peep.json")}
    new_aktsiisi_id = next(iter(aktsiisi_ids))

    consumer = read_graph(tmp_path / "consumer_peep.json")[0]
    ref = consumer["estleg:references"][0]["@id"]
    # The reference was rewritten to the aktsiisiseadus file's new id, and no
    # longer points at the now-ambiguous OLD id.
    assert ref != "estleg:AS_Par_1"
    assert ref == new_aktsiisi_id


def test_reference_rewrite_handles_duplicate_list_entries(tmp_path, monkeypatch):
    """List references with the same old id twice both get rewritten (#159).

    A focused check that the index-based list rewrite in
    ``update_cross_references`` handles duplicate entries — exercised via the
    public ``apply_remap_to_file`` + cross-ref path used by the rest of the
    suite.
    """
    monkeypatch.setattr(fdi, "KRR_DIR", tmp_path)
    write_peep(tmp_path / "owner_peep.json", [{"@id": "estleg:Old"}])
    write_peep(
        tmp_path / "consumer_peep.json",
        [
            {
                "@id": "estleg:Consumer",
                "estleg:references": ["estleg:Old", "estleg:Old"],
            }
        ],
    )

    remap = {"owner_peep.json": {"estleg:Old": "estleg:New"}}
    updated = fdi.update_cross_references(remap)
    assert updated == 2

    refs = read_graph(tmp_path / "consumer_peep.json")[0]["estleg:references"]
    assert refs == ["estleg:New", "estleg:New"]


# ── #280: atomic writes ───────────────────────────────────────────────────────


def test_atomic_write_json_roundtrips(tmp_path):
    """``_atomic_write_json`` writes valid, re-readable JSON with a trailing newline."""
    target = tmp_path / "out.json"
    doc = {"@graph": [{"@id": "estleg:X"}]}
    fdi._atomic_write_json(target, doc)
    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == doc
    # No tempfile droppings left behind.
    assert list(tmp_path.glob(".*tmp")) == []


def test_atomic_write_json_leaves_original_on_failure(tmp_path):
    """A serialization failure leaves the existing file untouched (#280).

    A set is not JSON-serialisable, so ``json.dump`` raises mid-write. The
    pre-existing file at the target path must survive intact, and no tempfile
    may be left behind.
    """
    import pytest

    target = tmp_path / "out.json"
    target.write_text('{"@graph": []}\n', encoding="utf-8")
    original = target.read_text(encoding="utf-8")

    with pytest.raises(TypeError):
        fdi._atomic_write_json(target, {"bad": {1, 2, 3}})

    # Original preserved, no `.tmp` dropping.
    assert target.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".*tmp")) == []


def test_apply_remap_to_file_uses_atomic_write(tmp_path):
    """``apply_remap_to_file`` rewrites via the atomic path and updates ids."""
    path = tmp_path / "law_peep.json"
    write_peep(path, [{"@id": "estleg:Old", "estleg:references": ["estleg:Old"]}])

    count = fdi.apply_remap_to_file(str(path), {"estleg:Old": "estleg:New"})
    assert count == 2  # @id + the self-reference string

    graph = read_graph(path)
    assert graph[0]["@id"] == "estleg:New"
    assert graph[0]["estleg:references"] == ["estleg:New"]
    assert list(tmp_path.glob(".*tmp")) == []
