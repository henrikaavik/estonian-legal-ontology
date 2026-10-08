"""#712: reference-integrity must accept ids published outside ``krr_outputs``.

``data/ehak/historical_municipalities.jsonld`` carries the 150
``estleg:HistoricalMunicipality`` nodes that KOV issuers and acts point at;
``validate_all`` walks only the corpus tree, so without the seed every such
edge reads as a dangling ``estleg:`` reference.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import validate_all


def _write(path: Path, graph: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"@context": {}, "@graph": graph}), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    _write(
        tmp_path / "data" / "ehak" / "historical_municipalities.jsonld",
        [
            {"@id": "estleg:HistoricalMunicipalities_Map", "@type": ["owl:Ontology"]},
            {"@id": "estleg:HistMun_Vaivara", "@type": ["estleg:HistoricalMunicipality"]},
        ],
    )
    return tmp_path


def test_external_reference_ids_reads_the_ehak_file(repo: Path) -> None:
    seeded = validate_all.external_reference_ids(repo / "krr_outputs")
    assert set(seeded) == {"estleg:HistoricalMunicipalities_Map", "estleg:HistMun_Vaivara"}
    assert seeded["estleg:HistMun_Vaivara"] == ["historical_municipalities.jsonld"]


def test_external_reference_ids_missing_file_is_empty(tmp_path: Path) -> None:
    (tmp_path / "krr_outputs").mkdir()
    assert validate_all.external_reference_ids(tmp_path / "krr_outputs") == {}


def test_seeded_ids_resolve_historical_municipality_edges(repo: Path, capsys) -> None:
    all_ids = {"estleg:Reg_1_Map": ["reg_peep.json"]}
    refs = [
        ("reg_peep.json", "estleg:Reg_1_Map", "estleg:enactedByHistoricalMunicipality", "estleg:HistMun_Vaivara"),
    ]
    before = len(validate_all.errors)
    validate_all.validate_internal_references(all_ids, refs)
    assert len(validate_all.errors) == before + 1, "dangling without the seed"
    validate_all.errors.pop()

    validate_all.validate_internal_references(
        all_ids, refs, extra_ids=validate_all.external_reference_ids(repo / "krr_outputs")
    )
    assert len(validate_all.errors) == before
    out = capsys.readouterr().out
    assert "seeded 2 external ids from historical_municipalities.jsonld" in out
    assert "1 internal object references resolve" in out
