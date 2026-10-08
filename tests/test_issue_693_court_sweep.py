"""#693: the Riigikohus year sweep is current, retried, and loud when partial."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from estleg import generate_court_decisions as gcd


class _Resp:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def rk_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    out = tmp_path / "riigikohus"
    out.mkdir()
    monkeypatch.setattr(gcd, "RK_DIR", out)
    monkeypatch.setattr(gcd.time, "sleep", lambda *_a, **_k: None)
    return out


def test_end_year_follows_the_evaluation_date() -> None:
    assert gcd.default_end_year(date(2027, 1, 3)) == 2027
    assert gcd.default_end_year(date(2026, 10, 8)) == 2026


def test_end_year_defaults_to_today(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2031, 2, 1)

    monkeypatch.setattr(gcd, "date", _FakeDate)
    assert gcd.default_end_year() == 2031


def test_fetch_year_retries_a_failed_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gcd.time, "sleep", lambda *_a, **_k: None)
    calls: list[dict] = []
    responses = [_Resp("busy", 503), _Resp("Tulemusi leiti kokku: 0")]

    def fake_get(url, params=None, **kwargs):
        calls.append(dict(params or {}))
        return responses.pop(0)

    monkeypatch.setattr(gcd.requests, "get", fake_get)
    assert gcd.fetch_year(2026) == []
    assert len(calls) == 2


def test_fetch_year_raises_partial_year_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gcd.time, "sleep", lambda *_a, **_k: None)
    calls: list[int] = []

    def fake_get(url, params=None, **kwargs):
        calls.append(1)
        return _Resp("down", 500)

    monkeypatch.setattr(gcd.requests, "get", fake_get)
    with pytest.raises(gcd.PartialYearError, match="year 2026"):
        gcd.fetch_year(2026)
    assert len(calls) == gcd.YEAR_RETRIES


def _fake_fetch_year(failing: set[int]):
    def fake(year: int) -> list[dict]:
        if year in failing:
            raise gcd.PartialYearError(f"rikos page 1 for year {year} failed")
        return []

    return fake


def test_sweep_covers_through_the_evaluation_year(
    rk_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def fake(year: int) -> list[dict]:
        seen.append(year)
        return []

    monkeypatch.setattr(gcd, "fetch_year", fake)
    gcd.main(["--evaluation-date", "2028-02-01"])
    assert seen[0] == 2028 and seen[-1] == gcd.START_YEAR
    index = json.loads((rk_dir / "RIIGIKOHUS_INDEX.json").read_text(encoding="utf-8"))
    assert index["fetched"] == "2028-02-01"
    assert index["partial"] is False
    assert "partial_years" not in index


def test_failed_year_aborts_without_allow_partial(
    rk_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gcd, "fetch_year", _fake_fetch_year({2025}))
    with pytest.raises(gcd.PartialYearError):
        gcd.main(["--evaluation-date", "2026-10-08"])
    # The previous index must not be overwritten by a truncated sweep.
    assert not (rk_dir / "RIIGIKOHUS_INDEX.json").exists()


def test_allow_partial_marks_index_and_exits_2(
    rk_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gcd, "fetch_year", _fake_fetch_year({2026, 2019}))
    with pytest.raises(SystemExit) as excinfo:
        gcd.main(["--evaluation-date", "2026-10-08", "--allow-partial"])
    assert excinfo.value.code == 2
    index = json.loads((rk_dir / "RIIGIKOHUS_INDEX.json").read_text(encoding="utf-8"))
    assert index["partial"] is True
    assert index["partial_years"] == [2026, 2019]


def test_bad_evaluation_date_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        gcd.parse_args(["--evaluation-date", "soon"])
    assert excinfo.value.code == 2
