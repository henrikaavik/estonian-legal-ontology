"""#720 — re-identification check tooling.

The offline scan must report aggregates only: no candidate name from the
input may reach the report. The live comparison must refuse without the
``ESTLEG_LIVE_CANARY=1`` opt-in; its comparison logic is exercised with an
injected fetcher, so no test opens a socket. Fixture names are invented
Estonian placeholder names.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import estleg.check_reidentification as cr

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "reidentification"
FIXTURE_NAMES = (
    "Mari Maasikas", "Mari Maasika", "Juku Juurikas", "Juku Juurikase",
    "Olev Ootaja", "Toomas Testija", "Kati Karuputk", "Peeter Proovija",
)


@pytest.fixture
def rk_dir(tmp_path: Path) -> Path:
    out = tmp_path / "riigikohus"
    out.mkdir()
    for path in FIXTURES.glob("*_peep.json"):
        shutil.copy(path, out / path.name)
    return out


def _assert_no_names(text: str) -> None:
    folded = text.casefold()
    leaked = [name for name in FIXTURE_NAMES if name.casefold() in folded]
    assert leaked == []
    for surname in ("maasik", "juurikas", "ootaja", "testija", "karuputk", "proovija"):
        assert surname not in folded


def test_offline_report_holds_aggregates_only(rk_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    assert cr.main(["--offline", "--rk-dir", str(rk_dir), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    _assert_no_names(text)
    report = json.loads(text)
    assert report["contains_names"] is False
    assert report["decisions_scanned"] == 2
    assert report["decisions_with_unattributed_candidate"] == 1
    assert report["decisions_with_initials_marker"] == 1
    assert set(report["year_histogram"]) == {"2019", "2020"}
    assert report["fields_scanned"] == list(cr.SCANNED_FIELDS)
    assert report["fields"]["rdfs:label"]["decisions_with_candidate_by_role"]["unattributed"] == 0
    assert len(report["fingerprints"]["unattributed"]) == 64
    assert report["live_check"]["status"] == "not_run"


def test_roles_separate_judges_counsel_and_parties(rk_dir: Path) -> None:
    scans = {s.case_number: s for s in cr.scan_corpus(rk_dir, repo_root=REPO)}
    named = scans["2-19-1"]
    roles = {c.text: c.role for c in named.by_field["estleg:legalText"]}
    assert roles["Toomas Testija"] == cr.ROLE_JUDGE
    assert roles["Olev Ootaja"] == cr.ROLE_PROFESSIONAL
    assert roles["Mari Maasikas"] == cr.ROLE_UNATTRIBUTED
    assert not any("Kalakaubandus" in text for text in roles)  # OÜ-marked org
    assert not any("Maakohus" in text for text in roles)
    assert scans["1-20-2"].unattributed() == []
    assert scans["1-20-2"].initials == 3


def test_live_refuses_without_canary(
    rk_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(cr.LIVE_ENV, raising=False)
    out = tmp_path / "live.json"
    assert cr.main(["--live", "--rk-dir", str(rk_dir), "--out", str(out)]) == 2
    assert cr.LIVE_ENV in capsys.readouterr().err
    assert not out.exists()
    with pytest.raises(cr.LiveCheckRefused):
        cr.assert_live_allowed({cr.LIVE_ENV: "0"})


def test_live_refuses_host_off_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "LIVE_DETAIL_URL", "https://example.invalid/?asjaNr=")
    with pytest.raises(cr.LiveCheckRefused, match="allow-list"):
        cr.assert_live_allowed({cr.LIVE_ENV: "1"})


def test_live_comparison_logic_with_injected_fetch(rk_dir: Path, tmp_path: Path) -> None:
    # The official text shows the parties as initials only -> re-identified.
    official = "Kohtuasi M. M. hagi J. J. vastu. Hageja esindaja vandeadvokaat Olev Ootaja."
    fetched: list[str] = []

    def fetch(case_number: str) -> str:
        fetched.append(case_number)
        return official

    out = tmp_path / "live.json"
    report = cr.run_live(
        rk_dir, out, sample_size=5, fetch=fetch, env={cr.LIVE_ENV: "1"},
        min_interval=0, repo_root=REPO,
    )
    assert fetched == ["2-19-1"]  # only decisions with unattributed candidates
    assert report["verdicts"]["re_identified"] == 1
    assert report["candidate_status"]["initialised"] >= 1
    assert report["re_identified_case_numbers"] == ["2-19-1"]
    _assert_no_names(out.read_text(encoding="utf-8"))


def test_classify_candidate_forms() -> None:
    assert cr.classify_candidate("Mari Maasikas", "... Mari  Maasikas ...") == "present"
    assert cr.classify_candidate("Mari Maasikas", "... M. M. ...") == "initialised"
    assert cr.classify_candidate("Mari Maasikas", "... X ...") == "absent"


def test_stratified_sample_is_deterministic_and_capped(rk_dir: Path) -> None:
    scans = cr.scan_corpus(rk_dir, repo_root=REPO)
    first = cr.stratified_sample(scans, 1)
    assert [s.case_number for s in first] == [s.case_number for s in cr.stratified_sample(scans, 1)]
    assert len(first) == 1
    assert cr.stratified_sample(scans, 0) == []
