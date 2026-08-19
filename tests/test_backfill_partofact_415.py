"""Tests for scripts/backfill_partofact_415.py (#415, #604).

Covers the dry-run safety added in #604: a bare invocation must NOT rewrite the
corpus. ``main`` defaults to dry-run and gates writes behind ``--apply``, and
``backfill_file(path, write=False)`` computes the planned edges without calling
``save_json``.

All tests use synthetic fixtures under ``tmp_path``; the real corpus is never
touched (the module's ``KRR`` corpus-root constant is monkeypatched per test).
"""

from __future__ import annotations

import json
from pathlib import Path

from estleg import backfill_partofact_415 as bpa

# ── Helpers ────────────────────────────────────────────────────────────────


def write_peep(path: Path, graph: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"@graph": graph}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_graph(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["@graph"]


def _act_and_provision_graph() -> list[dict]:
    """An act root + one provision that LACKS ``estleg:partOfAct``.

    The provision carries ``estleg:paragrahv`` (the SHACL LegalProvisionShape
    target) and an ``estleg:sourceAct`` key, so the backfill inserts
    ``estleg:partOfAct`` right after ``sourceAct``.
    """
    return [
        {
            "@id": "estleg:TestLaw_Map",
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
            "rdfs:label": "Test law",
        },
        {
            "@id": "estleg:TestLaw_Par_1",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "estleg:paragrahv": "§ 1.",
            "estleg:sourceAct": {"@value": "Test law", "@language": "et"},
        },
    ]


# ── #604: dry-run writes nothing ─────────────────────────────────────────────


def test_main_dry_run_writes_nothing(tmp_path, monkeypatch):
    """``main(["--dry-run"])`` previews missing edges but rewrites no file (#604).

    A bare invocation used to stamp partOfAct across the whole corpus in place.
    With #604 dry-run is the DEFAULT and writes are gated behind ``--apply``:
    this seeds a provision genuinely missing ``partOfAct`` (which a real run
    WOULD stamp), snapshots the file bytes, runs the dry-run, and asserts the
    file is byte-identical afterward.
    """
    monkeypatch.setattr(bpa, "KRR", tmp_path)
    peep = tmp_path / "testlaw_peep.json"
    write_peep(peep, _act_and_provision_graph())
    before = peep.read_bytes()

    # Sanity: the provision really lacks partOfAct, so a real run would stamp.
    assert "estleg:partOfAct" not in before.decode("utf-8")

    rc = bpa.main(["--dry-run"])
    assert rc == 0

    after = peep.read_bytes()
    assert after == before, "dry-run rewrote the peep file (must write nothing)"
    # No atomic-write tempfile droppings.
    assert list(tmp_path.glob(".*tmp")) == []


def test_main_default_is_dry_run(tmp_path, monkeypatch):
    """A bare ``main([])`` defaults to dry-run and writes nothing (#604)."""
    monkeypatch.setattr(bpa, "KRR", tmp_path)
    peep = tmp_path / "testlaw_peep.json"
    write_peep(peep, _act_and_provision_graph())
    before = peep.read_bytes()

    rc = bpa.main([])
    assert rc == 0

    assert peep.read_bytes() == before, "bare main() wrote (must dry-run)"


def test_main_apply_stamps_partofact(tmp_path, monkeypatch):
    """``main(["--apply"])`` stamps the partOfAct edge on the provision (#604).

    The complement of the dry-run test: the SAME missing edge, run with
    --apply, must rewrite the file and add an IRI-valued ``estleg:partOfAct``
    pointing at the act root, inserted right after ``estleg:sourceAct``.
    """
    monkeypatch.setattr(bpa, "KRR", tmp_path)
    peep = tmp_path / "testlaw_peep.json"
    write_peep(peep, _act_and_provision_graph())

    rc = bpa.main(["--apply"])
    assert rc == 0

    graph = read_graph(peep)
    prov = next(n for n in graph if n["@id"] == "estleg:TestLaw_Par_1")
    # The edge was added and points at the act root by IRI.
    assert prov["estleg:partOfAct"] == {"@id": "estleg:TestLaw_Map"}
    # Inserted in the generators' key position: right after sourceAct.
    keys = list(prov.keys())
    assert keys.index("estleg:partOfAct") == keys.index("estleg:sourceAct") + 1


def test_main_apply_and_dry_run_are_mutually_exclusive(tmp_path, monkeypatch):
    """``--apply --dry-run`` is rejected rather than silently writing (#604)."""
    import pytest

    monkeypatch.setattr(bpa, "KRR", tmp_path)
    with pytest.raises(SystemExit):
        bpa.main(["--apply", "--dry-run"])


# ── backfill_file(write=False) computes-without-writing ──────────────────────


def test_backfill_file_write_false_counts_but_does_not_write(tmp_path):
    """``backfill_file(path, write=False)`` returns counts without writing (#604).

    The importable ``backfill_file`` gains a keyword-only ``write`` parameter
    (default True). With ``write=False`` it still computes/returns the
    provision+chapter counts but leaves the file byte-unchanged.
    """
    peep = tmp_path / "testlaw_peep.json"
    write_peep(peep, _act_and_provision_graph())
    before = peep.read_bytes()

    prov_n, chap_n = bpa.backfill_file(peep, write=False)
    # One provision would be stamped; no chapters in this fixture.
    assert (prov_n, chap_n) == (1, 0)
    assert peep.read_bytes() == before, "write=False must not rewrite the file"


def test_backfill_file_default_write_true_writes(tmp_path):
    """The default ``backfill_file(path)`` still writes in place (back-compat).

    Existing callers/tests pass no ``write`` arg and must keep their original
    write-in-place behaviour.
    """
    peep = tmp_path / "testlaw_peep.json"
    write_peep(peep, _act_and_provision_graph())

    prov_n, chap_n = bpa.backfill_file(peep)
    assert (prov_n, chap_n) == (1, 0)

    prov = next(n for n in read_graph(peep) if n["@id"] == "estleg:TestLaw_Par_1")
    assert prov["estleg:partOfAct"] == {"@id": "estleg:TestLaw_Map"}
