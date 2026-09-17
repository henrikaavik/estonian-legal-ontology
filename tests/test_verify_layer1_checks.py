"""#311: verify_layer1.check_laws / check_state_regulations unit tests.

These two checkers were listed in CHECKS and run from the CLI but had no
dedicated tests. They decide whether INDEX law peeps are stamped
``estleg:Law`` + ``estleg:Act`` and whether state-regulation peeps carry
``estleg:Act``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _stage(tmp_path: Path) -> Path:
    krr = tmp_path / "krr_outputs"
    (krr / "regulations" / "riik").mkdir(parents=True)
    (krr / "regulations" / "kov").mkdir(parents=True)
    (tmp_path / "data" / "ehak").mkdir(parents=True)
    return krr


def _patch(monkeypatch, tmp_path: Path) -> None:
    from estleg import verify_layer1 as mod

    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "KRR", tmp_path / "krr_outputs")
    monkeypatch.setattr(
        mod, "KOV_DIR", tmp_path / "krr_outputs" / "regulations" / "kov"
    )


def _write_peep(path: Path, node: dict) -> None:
    path.write_text(
        json.dumps({"@context": {"estleg": "https://w3id.org/estleg/"}, "@graph": [node]}),
        encoding="utf-8",
    )


def test_check_laws_passes_when_stampable_roots_carry_law_and_act(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    krr = _stage(tmp_path)
    _write_peep(
        krr / "alks_peep.json",
        {
            "@id": "estleg:AlkS_Map",
            "@type": ["owl:Ontology", "estleg:Law", "estleg:Act"],
        },
    )
    (krr / "INDEX.json").write_text(
        json.dumps({"laws": [{"name": "alks", "files": ["alks_peep.json"]}]}),
        encoding="utf-8",
    )
    _patch(monkeypatch, tmp_path)
    from estleg import verify_layer1 as mod

    ok, detail = mod.check_laws()
    assert ok is True
    assert detail == "1/1"


def test_check_laws_fails_when_stampable_root_missing_law_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    krr = _stage(tmp_path)
    _write_peep(
        krr / "alks_peep.json",
        {"@id": "estleg:AlkS_Map", "@type": ["owl:Ontology", "estleg:Act"]},
    )
    (krr / "INDEX.json").write_text(
        json.dumps({"laws": [{"name": "alks", "files": ["alks_peep.json"]}]}),
        encoding="utf-8",
    )
    _patch(monkeypatch, tmp_path)
    from estleg import verify_layer1 as mod

    ok, detail = mod.check_laws()
    assert ok is False
    assert detail == "0/1"


def test_check_laws_skips_regulation_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    krr = _stage(tmp_path)
    _write_peep(
        krr / "reg_peep.json",
        {
            "@id": "estleg:Reg_1_Map",
            "@type": ["owl:Ontology", "estleg:NationalRegulation"],
        },
    )
    (krr / "INDEX.json").write_text(
        json.dumps({"laws": [{"name": "reg", "files": ["reg_peep.json"]}]}),
        encoding="utf-8",
    )
    _patch(monkeypatch, tmp_path)
    from estleg import verify_layer1 as mod

    ok, detail = mod.check_laws()
    assert ok is True
    assert detail == "0/0"


def test_enrich_main_calls_check_laws_after_stamp(monkeypatch) -> None:
    """#333: dead stamped<expected comparison replaced by check_laws()."""
    import inspect

    from estleg import enrich_kov_layer1 as ekl

    src = inspect.getsource(ekl.main)
    assert "check_laws" in src
    assert "if stamped < expected_stamped" not in src


def test_check_state_regulations_requires_act_on_regulation_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    krr = _stage(tmp_path)
    riik = krr / "regulations" / "riik"
    _write_peep(
        riik / "ok_t1_peep.json",
        {
            "@id": "estleg:Reg_1_Map",
            "@type": ["owl:Ontology", "estleg:NationalRegulation", "estleg:Act"],
        },
    )
    _patch(monkeypatch, tmp_path)
    from estleg import verify_layer1 as mod

    ok, detail = mod.check_state_regulations()
    assert ok is True
    assert detail == "1/1"

    _write_peep(
        riik / "bad_t2_peep.json",
        {
            "@id": "estleg:Reg_2_Map",
            "@type": ["owl:Ontology", "estleg:MinisterialRegulation"],
        },
    )
    ok, detail = mod.check_state_regulations()
    assert ok is False
    assert detail == "1/2"
