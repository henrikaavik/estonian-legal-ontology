"""#532: version layer covers peep kehtiv via interval containment."""

from __future__ import annotations

import json
from pathlib import Path

from estleg import validate_all

REPO = Path(__file__).resolve().parent.parent
PV_DIR = REPO / "krr_outputs" / "provision_versions"

LAG_SLUGS = (
    "perehuvitiste_seadus",
    "puuetega_inimeste_sotsiaaltoetuste_seadus",
    "riikliku_pensionikindlustuse_seadus",
    "sotsiaalmaksuseadus",
    "tootuskindlustuse_seadus",
    "toovoimetoetuse_seadus",
    "valismaalaste_seadus",
)


def _types(node: dict) -> set[str]:
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw}
    return {item for item in (raw or []) if isinstance(item, str)}


def test_version_covers_date_open_ended_current() -> None:
    node = {
        "@type": ["estleg:ProvisionVersion"],
        "estleg:versionValidFrom": {"@value": "2026-05-22", "@type": "xsd:date"},
    }
    assert validate_all.version_covers_date(node, "2026-05-22")
    assert validate_all.version_covers_date(node, "2026-05-24")
    assert not validate_all.version_covers_date(node, "2026-05-21")


def test_version_covers_date_respects_inclusive_valid_to() -> None:
    node = {
        "@type": ["estleg:ProvisionVersion"],
        "estleg:versionValidFrom": {"@value": "2026-05-22", "@type": "xsd:date"},
        "estleg:versionValidTo": {"@value": "2026-05-23", "@type": "xsd:date"},
    }
    assert validate_all.version_covers_date(node, "2026-05-23")
    assert not validate_all.version_covers_date(node, "2026-05-24")


def test_reported_laggers_cover_kehtiv_2026_05_24() -> None:
    """The seven 2026-05-22 current redactions still cover peep kehtiv."""
    for slug in LAG_SLUGS:
        path = PV_DIR / f"{slug}.jsonld"
        assert path.is_file(), slug
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert validate_all.version_layer_lag(doc, "2026-05-24") is None, slug


def test_committed_sidecars_cover_their_peep_kehtiv() -> None:
    krr = REPO / "krr_outputs"
    index_files = validate_all._index_files_by_slug(krr)
    holes = []
    scanned = 0
    for path in sorted(PV_DIR.glob("*.jsonld")):
        kehtiv = validate_all._kehtiv_for_slug(path.stem, krr, index_files)
        if not kehtiv:
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        scanned += 1
        lag = validate_all.version_layer_lag(doc, kehtiv)
        if lag is not None:
            holes.append(f"{path.name} newest={lag} kehtiv={kehtiv}")
    assert scanned >= 600
    assert holes == []


def test_freshness_gate_errors_on_coverage_hole(tmp_path, monkeypatch) -> None:
    krr = tmp_path / "krr_outputs"
    pv = krr / "provision_versions"
    pv.mkdir(parents=True)
    (krr / "hole_peep.json").write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:HOLE_Map_2026",
                        "@type": ["owl:Ontology", "estleg:Act"],
                        "estleg:kehtiv": {"@value": "2026-05-24", "@type": "xsd:date"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (pv / "hole.jsonld").write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@type": ["estleg:ProvisionVersion"],
                        "estleg:versionValidFrom": {
                            "@value": "2026-05-22",
                            "@type": "xsd:date",
                        },
                        "estleg:versionValidTo": {
                            "@value": "2026-05-23",
                            "@type": "xsd:date",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (krr / "INDEX.json").write_text(
        json.dumps({"laws": [{"name": "hole", "files": ["hole_peep.json"]}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(validate_all, "KRR_DIR", krr)
    validate_all.reset()
    validate_all.validate_version_layer_freshness(krr)
    assert any("#532" in err for err in validate_all.errors), validate_all.errors
