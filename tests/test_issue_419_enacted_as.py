"""#419 — enactedAs resolution and terminal legislative phases."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_draft_impact import resolve_enacted_as
from generate_draft_legislation import classify_draft_type


def test_resolve_enacted_as_only_for_enacts():
    lookup = {"karistusseadustik": {"name": "karistusseadustik", "files": ["k.json"], "slug": "karistusseadustik"}}
    iri_map = {"k.json": "estleg:KARIST_2_Map_2026"}
    assert resolve_enacted_as("Karistusseadustik", "amends", lookup, iri_map) is None
    assert resolve_enacted_as("Karistusseadustik", "enacts", lookup, iri_map) == (
        "estleg:KARIST_2_Map_2026"
    )


def test_resolve_enacted_as_strips_eelnou_noise():
    lookup = {
        "isikuandmete kaitse seadus": {
            "name": "isikuandmete_kaitse_seadus",
            "files": ["i.json"],
            "slug": "isikuandmete_kaitse_seadus",
        }
    }
    iri_map = {"i.json": "estleg:IAKS_Map_2026"}
    assert resolve_enacted_as(
        "Isikuandmete kaitse seaduse eelnõu", "enacts", lookup, iri_map
    ) == "estleg:IAKS_Map_2026"


def test_terminal_phases_live_in_controlled_vocabulary():
    path = (
        Path(__file__).resolve().parent.parent
        / "krr_outputs"
        / "controlled_vocabulary.jsonld"
    )
    ids = {
        n["@id"]
        for n in json.loads(path.read_text(encoding="utf-8"))["@graph"]
        if isinstance(n, dict)
    }
    assert "estleg:Phase_Enacted" in ids
    assert "estleg:Phase_Withdrawn" in ids
    assert "estleg:Phase_Rejected" in ids
    assert "estleg:enactedAs" in ids


def test_committed_eelnoud_has_enacted_as():
    path = (
        Path(__file__).resolve().parent.parent
        / "krr_outputs"
        / "eelnoud"
        / "eelnoud_combined.jsonld"
    )
    graph = json.loads(path.read_text(encoding="utf-8"))["@graph"]
    linked = [
        n
        for n in graph
        if isinstance(n, dict) and n.get("estleg:enactedAs")
    ]
    assert len(linked) >= 1, "no enactedAs edges on eelnoud_combined"


def test_analyse_and_study_titles_are_reports_not_other():
    assert classify_draft_type("Mõjuanalüüs X kohta")[0] == "Report"
    assert classify_draft_type("Uuring Y kohta")[0] == "Report"
