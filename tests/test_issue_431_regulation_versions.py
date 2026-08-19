"""#431: state regulations get ProvisionVersion sidecars."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.generate_provision_versions import (
    build_regulation_target,
    process_regulation_snapshot,
    riik_version_coverage,
)
from estleg.validate_all import validate_regulation_version_coverage
from estleg import validate_all

REPO = Path(__file__).resolve().parent.parent
RIIK = REPO / "krr_outputs" / "regulations" / "riik"
SAMPLE = RIIK / "sissesoidukeeldude_riikliku_registri_pohimaarus_t1055293_peep.json"


def test_build_regulation_target_reads_regulation_provisions() -> None:
    target = build_regulation_target(SAMPLE)
    assert target is not None
    assert target.prefix.startswith("Reg_")
    assert any(iri.endswith("_Par_3") for iri in target.provisions.values())
    assert target.act_iri == "estleg:Reg_1055293_Map_2026"


def test_snapshot_sidecar_emits_current_provision_version(tmp_path: Path) -> None:
    target = build_regulation_target(SAMPLE)
    assert target is not None
    result = process_regulation_snapshot(target, out_dir=tmp_path)
    assert result.error is None
    assert result.versions_emitted > 0
    sidecar = tmp_path / f"{target.slug}.jsonld"
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    versions = [
        n
        for n in doc["@graph"]
        if isinstance(n, dict) and "estleg:ProvisionVersion" in (n.get("@type") or [])
    ]
    assert versions
    assert all(n.get("estleg:versionOf") for n in versions)
    assert all(n.get("estleg:versionValidFrom") for n in versions)


def test_riik_version_coverage_meets_ninety_percent() -> None:
    have, total, ratio = riik_version_coverage(REPO / "krr_outputs")
    assert total >= 3000
    assert ratio >= 0.9
    assert have >= int(total * 0.9)


def test_validate_regulation_version_coverage_errors_when_thin(tmp_path: Path) -> None:
    validate_all.reset()
    riik = tmp_path / "regulations" / "riik"
    riik.mkdir(parents=True)
    (riik / "a_peep.json").write_text("{}", encoding="utf-8")
    (riik / "b_peep.json").write_text("{}", encoding="utf-8")
    validate_regulation_version_coverage(tmp_path)
    assert any("regulation version" in msg.lower() or "431" in msg for msg in validate_all.errors)
