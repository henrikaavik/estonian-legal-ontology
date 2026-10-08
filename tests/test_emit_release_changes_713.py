"""#713: provision-level, uncapped release delta between a git ref and the tree.

Every test builds its own git repository in ``tmp_path`` (two tagged commits
of a mini corpus); the project repository is never touched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from estleg import emit_release_changes as erc

NS = "https://w3id.org/estleg/"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        check=True, capture_output=True,
    )


def _act(iri: str) -> dict:
    return {"@id": iri, "@type": ["owl:NamedIndividual", "estleg:Act", "estleg:Law"]}


def _par(iri: str, text: str, **extra) -> dict:
    return {"@id": iri, "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "estleg:legalText": text, **extra}


def _lg(iri: str, text: str, **extra) -> dict:
    return {"@id": iri, "@type": ["estleg:Subsection", "owl:NamedIndividual"],
            "estleg:legalText": text, **extra}


def _write(repo: Path, rel: str, doc: object) -> None:
    path = repo / "krr_outputs" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _index(live: list[str], deprecated: list[str] = ()) -> dict:
    return {
        "laws": [{"name": n, "files": [f"{n}_peep.json"]} for n in live],
        "deprecated_laws": {"entries": [
            {"name": n, "files": [f"{n}_peep.json"], "replaced_by": "x"} for n in deprecated]},
    }


def _graph(*nodes: dict) -> dict:
    return {"@context": {}, "@graph": list(nodes)}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    # --- release v0.9.0 ----------------------------------------------------
    _write(root, "INDEX.json", _index(["alpha", "beta", "gamma", "epsilon"]))
    _write(root, "alpha_peep.json", _graph(
        _act("estleg:ALPHA_Map"),
        _par("estleg:ALPHA_Par_1", "(1) vana"),
        _lg("estleg:ALPHA_Par_1_Lg_1", "(1) vana"),
        _par("estleg:ALPHA_Par_2", "kehtetu"),
        _par("estleg:ALPHA_Par_3", "sama", **{"estleg:summary": "kokkuvõte"}),
    ))
    _write(root, "beta_peep.json", _graph(_act("estleg:BETA_Map"), _par("estleg:BETA_Par_1", "beta")))
    _write(root, "gamma_peep.json", _graph(_act("estleg:GAMMA_Map"), _par("estleg:GAMMA_Par_1", "g")))
    _write(root, "epsilon_peep.json", _graph(_act("estleg:EPS_Map"), _par("estleg:EPS_Par_1", "e")))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "v0.9.0")
    _git(root, "tag", "v0.9.0")
    # --- next release v0.10.0 ---------------------------------------------
    _write(root, "INDEX.json", _index(["alpha", "beta", "delta"], deprecated=["gamma"]))
    _write(root, "alpha_peep.json", _graph(
        _act("estleg:ALPHA_Map"),
        _par("estleg:ALPHA_Par_1", "(1) uus"),
        # a non-tracked enrichment (citations) is not a provision change
        _lg("estleg:ALPHA_Par_1_Lg_1", "(1) vana", **{"estleg:references": [{"@id": "estleg:X"}]}),
        _par("estleg:ALPHA_Par_3", "sama", **{"estleg:summary": "kokkuvõte",
                                               "estleg:temporalStatus": "Repealed"}),
        _par("estleg:ALPHA_Par_4", "lisatud"),
    ))
    # IRI collision re-prefix: same provision, new IRI
    _write(root, "beta_peep.json", _graph(_act("estleg:BETA_2_Map"), _par("estleg:BETA_2_Par_1", "beta")))
    _write(root, "delta_peep.json", _graph(_act("estleg:DELTA_Map"), _par("estleg:DELTA_Par_1", "d")))
    (root / "krr_outputs" / "epsilon_peep.json").unlink()
    _write(root, "provision_versions/alpha.jsonld", {"@graph": [
        {"@id": f"estleg:ALPHA_Par_1_v{n}", "@type": ["estleg:ProvisionVersion"],
         "estleg:versionOf": {"@id": "estleg:ALPHA_Par_1"},
         "estleg:versionValidFrom": {"@value": d, "@type": "xsd:date"}}
        for n, d in ((1, "2020-01-01"), (2, "2023-05-01"))
    ] + [
        {"@id": "estleg:ALPHA_Par_4_v1", "@type": ["estleg:ProvisionVersion"],
         "estleg:versionOf": {"@id": "estleg:ALPHA_Par_4"},
         "estleg:versionValidFrom": {"@value": "2024-01-01", "@type": "xsd:date"}},
    ]})
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "v0.10.0")
    _git(root, "tag", "v0.10.0")
    return root


def _rows(delta: erc.ReleaseDelta) -> dict[str, dict]:
    return {row["iri"].removeprefix(NS): row for row in delta.provisions}


def test_latest_release_tag_uses_version_order(repo: Path) -> None:
    assert erc.latest_release_tag(repo) == "v0.10.0"  # not v0.9.0 (lexical order)


def test_provision_and_law_level_delta(repo: Path) -> None:
    delta = erc.compute_release_delta(repo, "v0.9.0", "HEAD")
    rows = _rows(delta)
    assert set(rows) == {
        "ALPHA_Par_1", "ALPHA_Par_2", "ALPHA_Par_3", "ALPHA_Par_4",
        "BETA_Par_1", "BETA_2_Par_1", "DELTA_Par_1", "EPS_Par_1",
    }
    assert rows["ALPHA_Par_1"] == {
        "law": "alpha", "iri": NS + "ALPHA_Par_1", "change": "changed",
        "fields": ["estleg:legalText"], "versionValidFrom": "2023-05-01",
    }
    assert rows["ALPHA_Par_3"]["fields"] == ["estleg:temporalStatus"]
    assert "versionValidFrom" not in rows["ALPHA_Par_3"]
    assert rows["ALPHA_Par_2"]["change"] == "removed"
    assert rows["ALPHA_Par_4"] == {
        "law": "alpha", "iri": NS + "ALPHA_Par_4", "change": "added",
        "versionValidFrom": "2024-01-01",
    }
    assert rows["BETA_Par_1"]["replacedBy"] == NS + "BETA_2_Par_1"
    assert rows["BETA_2_Par_1"]["replaces"] == NS + "BETA_Par_1"
    assert rows["EPS_Par_1"]["change"] == "removed"
    assert rows["DELTA_Par_1"]["change"] == "added"
    assert delta.counts() == {"added": 3, "removed": 3, "changed": 2}
    assert [r["law"] for r in delta.added_laws] == ["delta"]
    assert delta.added_laws[0]["iri"] == NS + "DELTA_Map"
    assert [r["law"] for r in delta.removed_laws] == ["epsilon"]
    assert [r["law"] for r in delta.deprecated_laws] == ["gamma"]
    assert delta.missing_files == []


def test_working_tree_side_sees_uncommitted_edits(repo: Path) -> None:
    head = erc.compute_release_delta(repo, "v0.9.0", "HEAD")
    tree = erc.compute_release_delta(repo, "v0.9.0", erc.WORKTREE)
    assert tree.provisions == head.provisions
    _write(repo, "delta_peep.json", _graph(
        _act("estleg:DELTA_Map"), _par("estleg:DELTA_Par_1", "d"), _par("estleg:DELTA_Par_2", "uus")))
    tree = erc.compute_release_delta(repo, "v0.9.0", erc.WORKTREE)
    assert "DELTA_Par_2" in _rows(tree)
    assert "DELTA_Par_2" not in _rows(erc.compute_release_delta(repo, "v0.9.0", "HEAD"))
    assert tree.to_commit == head.to_commit


def test_emit_writes_uncapped_inline_record_and_report(repo: Path) -> None:
    out = repo / "krr_outputs" / "changes-9.9.9.jsonld"
    report_path = repo / "krr_outputs" / "reports" / "release_changes_report.json"
    delta, record, report = erc.emit_release_delta(
        repo, from_ref="v0.9.0", to_ref="HEAD", output=out, report_path=report_path,
        version="9.9.9", threshold=100,
    )
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == record
    assert record["@type"] == ["dcat:Dataset", "estleg:ReleaseDelta"]
    assert record["estleg:comparedFrom"].startswith("git v0.9.0 (")
    assert (record["estleg:addedCount"], record["estleg:removedCount"],
            record["estleg:changedCount"]) == (3, 3, 2)
    assert record["estleg:listedInline"] is True
    assert "estleg:listedIriCap" not in record
    assert record["estleg:changed"] == [NS + "ALPHA_Par_1", NS + "ALPHA_Par_3"]
    assert record["estleg:deprecatedLaw"] == [NS + "GAMMA_Map"]
    assert not out.with_suffix(".jsonl").exists()
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved == report
    assert saved["comparedFrom"]["ref"] == "v0.9.0"
    assert saved["provisions"]["reMintedPairs"] == 1
    assert saved["provisions"]["changedByField"] == {
        "estleg:legalText": 1, "estleg:temporalStatus": 1}
    assert saved["laws"] == {"compared": 5, "added": 1, "removed": 1, "deprecated": 1,
                             "withProvisionChanges": 4}
    assert saved["generated"] == erc.BUILD_EVALUATION_DATE


def test_emit_moves_lists_to_jsonl_above_threshold_and_cleans_up(repo: Path) -> None:
    out = repo / "krr_outputs" / "changes-9.9.9.jsonld"
    kwargs = dict(from_ref="v0.9.0", to_ref="HEAD", output=out, version="9.9.9")
    _delta, record, report = erc.emit_release_delta(repo, threshold=7, **kwargs)
    jsonl = out.with_suffix(".jsonl")
    assert record["estleg:listedInline"] is False
    assert "estleg:added" not in record
    assert record["dcat:distribution"]["dcat:downloadURL"] == {"@id": "changes-9.9.9.jsonl"}
    rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
    assert [r["change"] for r in rows[:3]] == ["law_added", "law_removed", "law_deprecated"]
    assert sum(1 for r in rows if r["change"] in {"added", "removed", "changed"}) == 8
    assert report["outputs"]["jsonl"].endswith("changes-9.9.9.jsonl")
    # Back under the threshold: the stale sibling is removed.
    erc.emit_release_delta(repo, threshold=100, **kwargs)
    assert not jsonl.exists()


def test_default_from_ref_is_latest_tag(repo: Path) -> None:
    delta = erc.compute_release_delta(repo, None, erc.WORKTREE)
    assert delta.from_ref == "v0.10.0"
    assert delta.provisions == []  # clean tree == latest release


def test_lfs_pointer_input_is_an_error(repo: Path) -> None:
    (repo / "krr_outputs" / "alpha_peep.json").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:0\nsize 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="LFS pointer"):
        erc.compute_release_delta(repo, "v0.9.0", erc.WORKTREE)


def test_cli_release_delta_and_legacy_mode(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "changes.jsonld"
    report = tmp_path / "report.json"
    assert erc.main(["--repo", str(repo), "--from-ref", "v0.9.0", "--to-ref", "HEAD",
                     "--output", str(out), "--report", str(report)]) == 0
    assert json.loads(out.read_text())["estleg:addedCount"] == 3
    assert erc.main(["--repo", str(repo), "--from-ref", "v404"]) == 2

    legacy = tmp_path / "legacy.jsonld"
    assert erc.main(["--mode", "index-deprecated", "--index",
                     str(repo / "krr_outputs" / "INDEX.json"), "--output", str(legacy),
                     "--cap", "1"]) == 0
    record = json.loads(legacy.read_text())
    assert record["estleg:comparedFrom"] == erc.OLD_LABEL_DEFAULT
    assert record["estleg:listedIriCap"] == 1
    assert record["estleg:removedCount"] == 1  # gamma (deprecated) vs live
