"""Issue #374 — repealed regulation peeps must not serve provision legalText.

Corpus gate plus the generator's "later repeal + temporalStatus=repealed"
tombstone. Generator unit coverage also lives in
``test_generate_regulations.py`` (TestRepealedBeforeSnapshot* /
TestStripRepealedProvisionBodies).
"""
from __future__ import annotations

import json
from pathlib import Path

import generate_regulations
from generate_regulations import (
    count_repealed_with_provision_legal_text,
    strip_repealed_provision_bodies,
)

REPO = Path(__file__).resolve().parents[1]
REGS = REPO / "krr_outputs" / "regulations"
RIIK = REGS / "riik"
KOV = REGS / "kov"


def _corpus_dirs() -> list[Path]:
    return [d for d in (RIIK, KOV) if d.is_dir()]


def test_no_repealed_regulation_peep_serves_provision_legal_text() -> None:
    """Zero ``temporalStatus=repealed`` acts may still carry provision legalText."""
    dirs = _corpus_dirs()
    if not dirs:
        return
    remaining: list[str] = []
    for out_dir in dirs:
        remaining.extend(count_repealed_with_provision_legal_text(out_dir))
    assert remaining == [], (
        f"{len(remaining)} repealed regulation peep(s) still serve "
        f"provision legalText: {remaining[:8]}"
    )


def test_active_regulations_excludes_all_temporal_status_repealed() -> None:
    """Committed indexes drop every temporalStatus=repealed act from active."""
    checks = (
        (RIIK, "REGULATIONS_RIIK_INDEX.json"),
        (KOV, "REGULATIONS_KOV_INDEX.json"),
    )
    saw_index = False
    for out_dir, name in checks:
        index_path = out_dir / name
        if not index_path.is_file() or not out_dir.is_dir():
            continue
        saw_index = True
        index = json.loads(index_path.read_text(encoding="utf-8"))
        remaining = count_repealed_with_provision_legal_text(out_dir)
        repealed = len(generate_regulations.iter_repealed_regulation_peeps(out_dir))
        assert index["totalRegulations"] == len(
            generate_regulations.regulation_files(out_dir)
        )
        assert "activeRegulations" in index
        assert index["activeRegulations"] == (
            index["totalRegulations"] - index["repealedBeforeSnapshotCount"]
        )
        assert index["repealedBeforeSnapshotCount"] == repealed
        assert index["activeRegulations"] == index["totalRegulations"] - repealed
        assert remaining == []
    if not saw_index:
        return


def test_vangla_example_has_no_provision_legal_text_when_present() -> None:
    """Ticket example: repeal 2026-05-04, still must not serve legalText."""
    matches = list(RIIK.glob("vangla_sisekorraeeskiri_t162619_peep.json"))
    if not matches:
        return
    doc = json.loads(matches[0].read_text(encoding="utf-8"))
    ont = generate_regulations._ontology_node(doc) or {}
    assert ont.get("estleg:temporalStatus") == "repealed"
    for node in doc.get("@graph", []):
        if generate_regulations._is_provision_node(node):
            assert not generate_regulations._node_has_legal_text(node)


def test_strip_helper_is_idempotent_on_already_tombstoned_doc() -> None:
    doc = {
        "@graph": [
            {
                "@id": "estleg:Reg_9_Map_2026",
                "@type": ["owl:Ontology", "estleg:Act"],
                "estleg:temporalStatus": "repealed",
                "estleg:repealDate": {"@value": "2026-05-04", "@type": "xsd:date"},
            },
            {
                "@id": "estleg:Reg_9_Par_1",
                "@type": ["owl:NamedIndividual"],
                "estleg:paragrahv": "§ 1.",
                "estleg:summary": generate_regulations.REPEALED_BODY_TOMBSTONE,
            },
        ]
    }
    first = strip_repealed_provision_bodies(doc)
    second = strip_repealed_provision_bodies(doc)
    assert first["provisions"] == 1
    assert second["stripped"] == 0
    assert "estleg:legalText" not in doc["@graph"][1]
