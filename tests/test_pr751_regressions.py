"""Repeat refreshes and rollback must preserve previously published state."""
import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from estleg import generate_regulations as gr
from estleg import derive_kov_enabling_staleness as stale
from tests.test_generate_regulations import TestRegenStateResume722 as _ResumeFixture


def _setup(tmp_path, monkeypatch):
    helper = _ResumeFixture()
    out, _ = helper._setup(tmp_path, monkeypatch, n_acts=1)
    calls = []
    monkeypatch.setattr(gr, "fetch_xml", helper._fake_fetch(calls))
    return helper, out, calls


def _run(helper, monkeypatch, *args):
    helper._run(monkeypatch, "--refresh", "--kehtiv", "2026-10-01", "--sleep", "0",
                "--max-rps", "0", "--workers", "1", *args)


def test_auto_scheme_remains_stable_across_refreshes(tmp_path, monkeypatch):
    helper, out, _ = _setup(tmp_path, monkeypatch)
    _run(helper, monkeypatch)
    path = next(out.glob("*_peep.json"))
    before = json.loads(path.read_text())
    _run(helper, monkeypatch)
    assert json.loads(path.read_text()) == before


def test_resume_detects_replaced_output(tmp_path, monkeypatch):
    helper, out, calls = _setup(tmp_path, monkeypatch)
    state = tmp_path / "state.json"
    _run(helper, monkeypatch, "--regen-state", str(state))
    next(out.glob("*_peep.json")).write_text('{"@graph": []}')
    calls.clear()
    _run(helper, monkeypatch, "--regen-state", str(state))
    assert len(calls) == 1


def test_resume_does_not_override_explicit_scheme_change(tmp_path, monkeypatch):
    helper, _, calls = _setup(tmp_path, monkeypatch)
    state = tmp_path / "state.json"
    _run(helper, monkeypatch, "--iri-scheme", "legacy", "--regen-state", str(state))
    calls.clear()
    _run(helper, monkeypatch, "--iri-scheme", "law", "--regen-state", str(state))
    assert len(calls) == 1


def test_failed_xml_fetch_fails_run_and_marks_incomplete(tmp_path, monkeypatch):
    helper, out, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(gr, "fetch_xml", lambda *a, **kw: None)
    with pytest.raises(SystemExit) as exc:
        _run(helper, monkeypatch)
    assert exc.value.code != 0
    index = json.loads((out / "REGULATIONS_RIIK_INDEX.json").read_text())
    assert index["run"]["complete"] is False


def test_repealed_enabling_provision_is_outdated():
    layer = stale.VersionLayer(chains={"estleg:X_Par_1": [("2000-01-01", "2010-12-31", "v1")]})
    result = stale.evaluate_provision("estleg:X_Par_1", "2005-01-01", layer, "2026-01-01")
    assert result.outdated
    assert result.superseding_date == "2011-01-01"
    assert not stale.evaluate_provision("estleg:X_Par_1", "2005-01-01", layer, "2010-01-01").outdated
    layer.chains["estleg:X_Par_1"].append(("2011-01-01", "2014-12-31", "v2"))
    layer.text_digest.update(v1="same", v2="same")
    result = stale.evaluate_provision("estleg:X_Par_1", "2005-01-01", layer, "2026-01-01")
    assert result.superseding_date == "2015-01-01"


@pytest.mark.parametrize("payload", ["invalid JSON", "version https://git-lfs.github.com/spec/v1\noid sha256:x\n"])
def test_unreadable_version_layer_cannot_clear_staleness_flags(tmp_path, payload):
    (tmp_path / "broken.jsonld").write_text(payload)
    with pytest.raises(ValueError, match="Unreadable"):
        stale.build_version_layer(tmp_path)


def test_invalid_snapshot_date_is_rejected(tmp_path, monkeypatch):
    helper, _, calls = _setup(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _run(helper, monkeypatch, "--kehtiv", "2026-02-30")
    assert exc.value.code == 2
    assert calls == []


def test_superscript_section_display_survives_regulation_generation():
    root = ET.fromstring('<akt><paragrahv><paragrahvNr>1</paragrahvNr>'
                         '<kuvatavNr>§ 1<sup>2</sup>.</kuvatavNr></paragrahv></akt>')
    nodes = gr.collect_structured_paragraphs(root, "Reg_1", "Test", "estleg:LegalProvision", "estleg:Reg_1_Map")
    assert nodes[0]["estleg:paragrahv"] == "§ 1²."
    assert nodes[0]["@id"] == "estleg:Reg_1_Par_1_2"


def test_refresh_workflow_can_update_existing_branch_from_shallow_checkout(tmp_path):
    import yaml

    def git(*args, cwd=tmp_path):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    git("init", "--bare", str(remote))
    git("init", "-b", "main", str(repo))
    git("config", "user.name", "Test", cwd=repo)
    git("config", "user.email", "test@example.invalid", cwd=repo)
    target = repo / "krr_outputs/regulations/data.json"
    target.parent.mkdir(parents=True)
    target.write_text("original")
    git("add", ".", cwd=repo)
    git("commit", "-m", "initial", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    branch = "refresh/regulations-2026-10-01"
    git("push", "origin", f"HEAD:refs/heads/{branch}", cwd=repo)
    expected = git("rev-parse", "HEAD", cwd=repo)
    # checkout@actions fetches main alone, so the refresh remote-tracking
    # branch is unavailable. An implicit force-with-lease wrongly expects absence.
    git("update-ref", "-d", f"refs/remotes/origin/{branch}", cwd=repo)
    target.write_text("refreshed")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "gh").write_text("#!/bin/sh\nexit 0\n")
    (shim / "gh").chmod(0o755)
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/refresh-regulations.yml"
    steps = yaml.safe_load(workflow.read_text())["jobs"]["refresh"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == "Open the pull request")
    result = subprocess.run(["bash", "-e", "-c", script], cwd=repo, text=True, capture_output=True,
                            env={**os.environ, "PATH": f"{shim}:{os.environ['PATH']}",
                                 "BRANCH": branch, "KEHTIV": "2026-10-01", "EXPECTED_HEAD": expected,
                                 "RUNNER_TEMP": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert git("show", f"{branch}:krr_outputs/regulations/data.json", cwd=remote) == "refreshed"
