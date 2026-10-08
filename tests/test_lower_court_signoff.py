"""#720 — sign-off gate on a live lower-court sweep.

``--fetch --apply`` writes first/second-instance decisions whose summaries may
name private persons, so it must refuse (exit 2) unless a valid, unexpired,
in-scope sign-off record is supplied. Dry runs and the offline fixture path
stay allowed. The fetch is always monkeypatched: these tests open no socket.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import estleg.generate_lower_court_decisions as glc

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "kohtud" / "search_hits.json"
VALID = REPO / "tests" / "fixtures" / "kohtud" / "signoff_valid.json"
TEMPLATE = REPO / "data" / "lower_court_signoff.json"
TODAY = date(2026, 10, 8)


@pytest.fixture
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def _fetch(body: dict, *, session: object | None = None) -> dict:
        calls.append(body)
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    monkeypatch.setattr(glc, "fetch_search_page", _fetch)
    return calls


def _valid_doc() -> dict:
    return json.loads(VALID.read_text(encoding="utf-8"))


def _run(tmp_path: Path, *extra: str) -> int:
    return glc.main(
        ["--fetch", "--apply", "--out-dir", str(tmp_path / "out"), *extra], today=TODAY
    )


def test_fetch_apply_without_signoff_is_refused(
    tmp_path: Path, fake_fetch: list[dict], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path) == 2
    err = capsys.readouterr().err
    assert "--signoff" in err and "refused" in err
    assert fake_fetch == []  # refused before any request
    assert not (tmp_path / "out").exists()


def test_shipped_template_fails_closed(
    tmp_path: Path, fake_fetch: list[dict], capsys: pytest.CaptureFixture[str]
) -> None:
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert template["approved_on"] is None
    assert _run(tmp_path, "--signoff", str(TEMPLATE)) == 2
    assert "approved_on" in capsys.readouterr().err
    assert fake_fetch == []
    assert not (tmp_path / "out").exists()


def test_valid_signoff_allows_the_run(tmp_path: Path, fake_fetch: list[dict]) -> None:
    assert _run(tmp_path, "--signoff", str(VALID), "--limit", "5") == 0
    assert len(fake_fetch) == 1
    index = json.loads((tmp_path / "out" / glc.INDEX_NAME).read_text(encoding="utf-8"))
    assert index["ingested"] == 1


def test_dry_run_and_fixture_paths_need_no_signoff(tmp_path: Path, fake_fetch: list[dict]) -> None:
    assert glc.main(["--fetch", "--out-dir", str(tmp_path / "dry")], today=TODAY) == 0
    assert not (tmp_path / "dry").exists()
    assert (
        glc.main(
            ["--from-fixture", str(FIXTURE), "--apply", "--out-dir", str(tmp_path / "fx")],
            today=TODAY,
        )
        == 0
    )


def test_out_of_scope_hits_are_not_written(tmp_path: Path, fake_fetch: list[dict]) -> None:
    doc = _valid_doc()
    doc["scope"]["courts"] = ["administrative"]
    path = tmp_path / "signoff.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _run(tmp_path, "--signoff", str(path), "--limit", "5") == 0
    index = json.loads((tmp_path / "out" / glc.INDEX_NAME).read_text(encoding="utf-8"))
    assert index["ingested"] == 0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(expires_on="2026-10-07"), "expired"),
        (lambda d: d.update(approved_on="2026-10-09"), "future"),
        (lambda d: d.update(approved_by="Some Person"), "approved_by"),
        (lambda d: d.update(dpo_reference=" "), "dpo_reference"),
        (lambda d: d["scope"].update(courts=["supreme"]), "scope.courts"),
        (lambda d: d["scope"].update(max_decisions=0), "max_decisions"),
        (lambda d: d.pop("scope"), "scope"),
    ],
)
def test_invalid_signoff_records_are_rejected(mutate, message: str) -> None:
    doc = _valid_doc()
    mutate(doc)
    with pytest.raises(glc.SignoffError, match=message):
        glc.validate_signoff(doc, today=TODAY)


@pytest.mark.parametrize(
    ("year", "limit", "message"),
    [(2026, 5, "--year 2026"), (1995, 11, "max_decisions"), (None, 0, "positive")],
)
def test_requested_run_must_fit_the_scope(year: int | None, limit: int, message: str) -> None:
    signoff = glc.validate_signoff(_valid_doc(), today=TODAY)
    with pytest.raises(glc.SignoffError, match=message):
        glc.check_run_scope(signoff, year=year, limit=limit)


def test_out_of_scope_year_is_refused_before_fetch(
    tmp_path: Path, fake_fetch: list[dict], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, "--signoff", str(VALID), "--year", "2026", "--limit", "5") == 2
    assert "outside the approved range" in capsys.readouterr().err
    assert fake_fetch == []
