"""#444 — one minter and one reverse-parser for act-root IRIs."""

from __future__ import annotations

import ast
from pathlib import Path

from estleg.estleg_common import (
    MAP_IRI_YEAR,
    act_prefix_from_iri,
    is_map_iri,
    mint_act_iri,
)
from estleg.extract_cross_references import _prefix_from_act_iri

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "estleg"

MINTERS = (
    SRC / "generate_all_laws.py",
    SRC / "generate_regulations.py",
    SRC / "generate_draft_legislation.py",
    SRC / "generate_eu_legislation.py",
    SRC / "generate_eu_court_decisions.py",
    SRC / "enrich_kov_layer1.py",
    SRC / "extract_legal_concepts.py",
    SRC / "backfill_orphan_is_part_of.py",
    SRC / "retarget_osa_issued_under.py",
)

ROUND_TRIP_PREFIXES = (
    "KOKS",
    "KARIST_2",
    "Reg_1052132",
    "AVRS_2",
    "VOS_O4",
    "CURIA_Judgments",
    "EURlex_Combined",
    "Eelnoud_Review",
    "Municipalities",
    "LegalConcepts",
)


def test_round_trip_multi_segment_and_collision_prefixes() -> None:
    assert MAP_IRI_YEAR == 2026
    for prefix in ROUND_TRIP_PREFIXES:
        minted = mint_act_iri(prefix)
        assert minted == f"estleg:{prefix}_Map"
        assert act_prefix_from_iri(minted) == prefix
        assert is_map_iri(minted)
        assert _prefix_from_act_iri(minted) == prefix
        assert mint_act_iri(prefix, year=MAP_IRI_YEAR) == minted
        assert mint_act_iri(prefix, year=2027) == minted


def test_parser_handles_osa_procedure_and_provision_tails() -> None:
    assert act_prefix_from_iri("estleg:KARIST_2_Osa1_1_87") == "KARIST_2"
    assert act_prefix_from_iri("estleg:KOKS_Par_22") == "KOKS"
    assert act_prefix_from_iri("estleg:KOKS_Par_1_Lg_2") == "KOKS"
    assert act_prefix_from_iri("estleg:KrMS_ProcedureMap_2026") == "KrMS"
    assert act_prefix_from_iri("https://w3id.org/estleg/PKS_Map") == "PKS"
    assert act_prefix_from_iri("estleg:AVRS_Map") == "AVRS"


def test_minters_import_shared_helper() -> None:
    for path in MINTERS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "estleg.estleg_common":
                names = {alias.name for alias in node.names}
                if "mint_act_iri" in names:
                    imported = True
        assert imported, f"{path.name} does not import mint_act_iri"


def test_no_local_map_year_fstring_mints() -> None:
    """Generators must not embed ``_Map`` in f-strings (#444 DoD)."""
    banned = []
    for path in MINTERS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            blob = ast.unparse(node)
            if "_Map" in blob or "_Map_{" in blob:
                banned.append(f"{path.name}:{node.lineno}:{blob}")
    assert banned == []


def test_scripts_shims_have_no_map_literals() -> None:
    hits = []
    scripts = REPO / "scripts"
    for path in scripts.glob("*.py"):
        if path.name == "estleg_common.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "_Map_" in text and "mint_act_iri" not in text:
            # shims are one-liners; any leftover mint/parse is a leak
            if "f\"estleg:" in text or "re.compile" in text:
                hits.append(path.name)
    assert hits == []
