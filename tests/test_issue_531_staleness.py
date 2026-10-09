"""#531 / #693 — refresh SLA, per-corpus lag budgets, offline staleness gate.

Every test passes an explicit evaluation date (or injects ``today``): the
gate's default is the wall clock (#693), and tests must not depend on it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from estleg import check_rt_staleness as crs

REPO = Path(__file__).resolve().parent.parent
METADATA = REPO / "metadata.jsonld"
VALIDATE_YML = REPO / ".github" / "workflows" / "validate.yml"
README = REPO / "README.md"
RELEASE = REPO / "docs" / "RELEASE.md"


def test_helper_flags_kehtiv_older_than_sla() -> None:
    evaluation = date(2026, 6, 1)
    assert crs.kehtiv_lags_past_sla("2026-01-01", evaluation) is True
    assert crs.kehtiv_lags_past_sla("2026-05-24", evaluation) is False
    assert crs.kehtiv_lags_past_sla(None, evaluation) is True


def test_sample_peeps_have_committed_kehtiv() -> None:
    samples = crs.load_sample()
    assert {sample.abbrev for sample in samples} == {"PKS", "KarS", "PS"}
    for sample in samples:
        assert sample.path.is_file(), sample.path
        assert sample.kehtiv is not None, sample.abbrev
        # Within SLA as of the build pin; the gate itself runs against today.
        assert crs.kehtiv_lags_past_sla(sample.kehtiv, date(2026, 6, 1)) is False


def test_evaluation_date_defaults_to_today_not_the_build_pin() -> None:
    """#693: a gate measured against a pinned date can never fail."""
    assert crs.evaluation_date(today=date(2027, 1, 15)) == date(2027, 1, 15)
    assert crs.evaluation_date(None, today=date(2027, 1, 15)) == date(2027, 1, 15)
    assert crs.evaluation_date("2026-06-01", today=date(2027, 1, 15)) == date(2026, 6, 1)


def test_evaluation_date_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        crs.evaluation_date("next tuesday")


def test_gate_fails_when_committed_kehtiv_is_past_sla() -> None:
    """The gate fails exactly when the committed snapshot lags past the SLA.

    Measured relative to the committed kehtiv (not a fixed calendar date), so
    the test holds before and after a corpus refresh: #693 pinned it to the
    2026-09-04 re-validation, which a 2026-10-09 refresh made meaningless.
    """
    samples = crs.load_sample()
    newest = max(sample.kehtiv for sample in samples)
    oldest = min(sample.kehtiv for sample in samples)
    late = newest + timedelta(days=crs.SLA_MAX_LAG_DAYS + 1)
    failures = crs.check_samples(samples, as_of=late)
    assert len(failures) == len(samples)
    assert all(f"SLA_MAX_LAG_DAYS={crs.SLA_MAX_LAG_DAYS}" in line for line in failures)
    assert crs.check_samples(samples, as_of=oldest) == []


def _write_index(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_corpus_budgets_read_index_stamps_and_flag_lag(tmp_path: Path) -> None:
    budgets = tuple(b for b in crs.CORPUS_BUDGETS if b.key in {"regulations-riik", "riigikohus"})
    _write_index(
        tmp_path / "regulations/riik/REGULATIONS_RIIK_INDEX.json",
        {"generated": "2026-06-01T00:00:00+00:00", "kehtiv": "2026-05-01"},
    )
    # ``fetched`` (the live sweep date) wins over the pinned ``generated``.
    _write_index(
        tmp_path / "riigikohus/RIIGIKOHUS_INDEX.json",
        {"generated": "2026-01-01", "fetched": "2026-06-01"},
    )
    # Exactly on budget (60 d) still passes; riigikohus would be 180 d
    # behind if ``generated`` were read instead of ``fetched``.
    stamps, failures = crs.check_corpora(as_of=date(2026, 6, 30), krr_dir=tmp_path, budgets=budgets)
    assert {s.budget.key: (s.field, s.stamp) for s in stamps} == {
        "regulations-riik": ("kehtiv", date(2026, 5, 1)),
        "riigikohus": ("fetched", date(2026, 6, 1)),
    }
    assert failures == []
    _, failures = crs.check_corpora(as_of=date(2026, 7, 1), krr_dir=tmp_path, budgets=budgets)
    assert failures == ["regulations-riik: kehtiv=2026-05-01 is 61d behind 2026-07-01 (budget 60d)"]


def test_corpus_missing_index_or_stamp_fails(tmp_path: Path) -> None:
    budgets = tuple(b for b in crs.CORPUS_BUDGETS if b.key in {"eurlex", "curia"})
    _write_index(tmp_path / "eurlex/EURLEX_INDEX.json", {"total_acts": 1})
    _, failures = crs.check_corpora(as_of=date(2026, 7, 1), krr_dir=tmp_path, budgets=budgets)
    assert any(line.startswith("eurlex:") and "has no date" in line for line in failures)
    assert any(line.startswith("curia:") and "missing index" in line for line in failures)


def test_every_committed_corpus_index_has_a_readable_stamp() -> None:
    for budget in crs.CORPUS_BUDGETS:
        if budget.stamp_file is None:
            continue
        stamp = crs.read_corpus_stamp(budget)
        assert stamp.stamp is not None, f"{budget.key}: no date in {budget.stamp_file}"


def test_budget_table_is_internally_consistent() -> None:
    keys = [b.key for b in crs.CORPUS_BUDGETS]
    assert len(keys) == len(set(keys))
    laws = next(b for b in crs.CORPUS_BUDGETS if b.key == "laws")
    assert laws.max_lag_days == crs.SLA_MAX_LAG_DAYS
    for budget in crs.CORPUS_BUDGETS:
        assert budget.rationale, budget.key
        if budget.max_lag_days is None:
            continue
        # A budget covers its published cadence plus at most a month of grace.
        nominal = crs.FREQ_NOMINAL_DAYS[budget.accrual_periodicity]
        assert nominal <= budget.max_lag_days <= nominal + crs.MAX_GRACE_DAYS, budget.key


def test_metadata_distributions_publish_the_code_budgets() -> None:
    """#693: every dcat:distribution carries the periodicity the gate enforces."""
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    distributions = meta["dcat:distribution"]
    by_title = {
        b.distribution_title: b.accrual_periodicity
        for b in crs.CORPUS_BUDGETS
        if b.distribution_title is not None
    }
    published: dict[str, str] = {}
    for dist in distributions:
        title = dist.get("dcterms:title")
        period = dist.get("dcterms:accrualPeriodicity")
        iri = period.get("@id") if isinstance(period, dict) else period
        assert iri, f"distribution {title!r} has no dcterms:accrualPeriodicity"
        assert iri.startswith(crs.EU_FREQUENCY), iri  # #710: EU frequency authority
        published[title] = iri
    assert published == by_title


def test_main_reports_remediation_on_failure(capsys: pytest.CaptureFixture[str]) -> None:
    assert crs.main(["--evaluation-date", "2026-09-04"]) == 1
    out = capsys.readouterr().out
    assert "evaluation_date=2026-09-04 (explicit)" in out
    assert "SLA FAIL" in out
    assert "Remediation:" in out and "#691" in out


def test_main_laws_only_passes_at_build_pin(capsys: pytest.CaptureFixture[str]) -> None:
    assert crs.main(["--evaluation-date", "2026-06-01", "--laws-only"]) == 0
    assert "SLA PASS" in capsys.readouterr().out


def test_workflow_runs_check_rt_staleness() -> None:
    text = VALIDATE_YML.read_text(encoding="utf-8")
    job = text.split("  rt-staleness:\n", 1)[1].split("\n  json-validation:\n", 1)[0]
    assert "check_rt_staleness.py" in job
    assert "--fetch" in text
    # #693: CI measures against today, never a pinned date.
    assert "--evaluation-date" not in job
    # #691: the live schema canary runs as its own step in the same job.
    assert "check_rt_staleness.py --schema-canary" in job


def test_metadata_accrual_periodicity_is_monthly() -> None:
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    period = meta.get("dcterms:accrualPeriodicity")
    iri = period.get("@id") if isinstance(period, dict) else period
    assert iri
    assert "IRREG" not in iri
    assert "monthly" in iri.lower()


def test_release_and_readme_publish_monthly_sla() -> None:
    readme = README.read_text(encoding="utf-8")
    release = RELEASE.read_text(encoding="utf-8")
    combined = readme + "\n" + release
    assert "monthly" in combined.lower()
    assert "SLA" in combined or "sla" in combined
    assert "check_rt_staleness.py" in combined
    assert "estleg:kehtiv" in combined
    assert "### 5-minute start" in readme


def test_fetch_flags_a_newer_rt_consolidation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--fetch: a public-API ``kehtivId`` != committed act id means RT moved on."""
    peep = tmp_path / "pks_peep.json"
    peep.write_text("{}", encoding="utf-8")
    sample = crs.SampleKehtiv(
        "PKS", peep, date(2026, 5, 24), "https://www.riigiteataja.ee/akt/107052025017"
    )
    monkeypatch.setattr(crs, "fetch_act_metadata", lambda url: {"currentId": "999"})
    monkeypatch.setattr(crs, "fetch_rt_consolidation_date", lambda url: "2026-05-01")
    failures = crs.check_samples([sample], as_of=date(2026, 6, 1), fetch=True)
    assert failures == [
        "PKS: RT has a newer consolidation (kehtivId=999) than the committed act 107052025017"
    ]
