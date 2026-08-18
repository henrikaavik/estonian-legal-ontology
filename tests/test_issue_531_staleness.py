"""#531 — monthly refresh SLA + offline RT content-staleness canary."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import check_rt_staleness as crs

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
        assert crs.kehtiv_lags_past_sla(sample.kehtiv) is False


def test_workflow_runs_check_rt_staleness() -> None:
    text = VALIDATE_YML.read_text(encoding="utf-8")
    assert "check_rt_staleness.py" in text
    assert "--fetch" in text


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
