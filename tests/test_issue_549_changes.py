"""#549 — machine-readable inter-release change record."""

from __future__ import annotations

import json
from pathlib import Path

from estleg import emit_release_changes as erc

REPO = Path(__file__).resolve().parent.parent
METADATA = REPO / "metadata.jsonld"
CHANGES = REPO / "krr_outputs" / "changes-0.11.0.jsonld"
INDEX = REPO / "krr_outputs" / "INDEX.json"


def test_collect_iris_from_jsonld_walks_at_id(tmp_path: Path) -> None:
    path = tmp_path / "sample.jsonld"
    path.write_text(
        json.dumps(
            {
                "@id": "estleg:Dataset",
                "@graph": [
                    {"@id": "estleg:A"},
                    {"estleg:ref": {"@id": "estleg:B"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert erc.collect_iris_from_jsonld(path) == {
        "estleg:Dataset",
        "estleg:A",
        "estleg:B",
    }


def test_collect_iris_from_jsonld_index_fallback(tmp_path: Path) -> None:
    path = tmp_path / "INDEX.json"
    path.write_text(
        json.dumps(
            {
                "laws": [{"name": "perekonnaseadus", "files": ["perekonnaseadus_peep.json"]}],
                "deprecated_laws": {
                    "entries": [{"name": "alkoholi_seadus", "files": []}],
                },
            }
        ),
        encoding="utf-8",
    )
    iris = erc.collect_iris_from_jsonld(path)
    assert erc.law_name_to_iri("perekonnaseadus") in iris
    assert erc.law_name_to_iri("alkoholi_seadus") in iris


def test_diff_iris_and_build_change_record() -> None:
    diff = erc.diff_iris({"estleg:old", "estleg:keep"}, {"estleg:keep", "estleg:new"})
    assert diff["added"] == ["estleg:new"]
    assert diff["removed"] == ["estleg:old"]
    assert diff["addedCount"] == 1
    assert diff["removedCount"] == 1

    record = erc.build_change_record(
        "v-old",
        "v-new",
        diff,
        extra_counts={"estleg:extraCount": 3},
    )
    assert "dcat:Dataset" in record["@type"]
    assert "estleg:ReleaseDelta" in record["@type"]
    assert record["estleg:comparedFrom"] == "v-old"
    assert record["estleg:comparedTo"] == "v-new"
    assert record["estleg:added"] == ["estleg:new"]
    assert record["estleg:removed"] == ["estleg:old"]
    assert record["estleg:addedCount"] == 1
    assert record["estleg:removedCount"] == 1
    assert record["estleg:extraCount"] == 3


def test_build_change_record_caps_listed_iris() -> None:
    added = [f"estleg:n{i:04d}" for i in range(80)]
    removed = [f"estleg:o{i:04d}" for i in range(60)]
    record = erc.build_change_record(
        "old",
        "new",
        {"added": added, "removed": removed, "addedCount": 80, "removedCount": 60},
        listed_cap=50,
    )
    assert record["estleg:addedCount"] == 80
    assert record["estleg:removedCount"] == 60
    assert len(record["estleg:added"]) == 50
    assert len(record["estleg:removed"]) == 50


def test_committed_changes_file_has_counts_and_is_honest() -> None:
    assert CHANGES.is_file(), "krr_outputs/changes-0.11.0.jsonld is the #549 record"
    record = json.loads(CHANGES.read_text(encoding="utf-8"))
    assert record["estleg:addedCount"] > 0
    assert record["estleg:removedCount"] > 0
    assert len(record["estleg:added"]) <= record["estleg:listedIriCap"]
    assert len(record["estleg:removed"]) <= record["estleg:listedIriCap"]

    index = json.loads(INDEX.read_text(encoding="utf-8"))
    expected = erc.diff_iris(
        erc.snapshot_iris_from_index(index, deprecated=True),
        erc.snapshot_iris_from_index(index, deprecated=False),
    )
    assert record["estleg:addedCount"] == expected["addedCount"]
    assert record["estleg:removedCount"] == expected["removedCount"]
    assert record["estleg:added"] == expected["added"][: record["estleg:listedIriCap"]]
    assert record["estleg:removed"] == expected["removed"][: record["estleg:listedIriCap"]]


def test_metadata_links_changes_distribution() -> None:
    text = METADATA.read_text(encoding="utf-8")
    assert "krr_outputs/changes-0.11.0.jsonld" in text
    meta = json.loads(text)
    titles = [dist.get("dcterms:title") for dist in meta.get("dcat:distribution", [])]
    assert any("change" in (title or "").lower() for title in titles)
