"""Issue #380 — year-prefixed budget bills must not resolve to another year."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.estleg_common import jsonld_text, jsonld_texts
from estleg.extract_draft_impact import resolve_law_name, year_compatible_law_match

REPO = Path(__file__).resolve().parents[1]
EELNOUD = REPO / "krr_outputs" / "eelnoud" / "eelnoud_combined.jsonld"
BUDGET_2026 = "estleg:2026_aasta_riigieelarve_seadus_Map"


def _lookup() -> dict[str, dict]:
    return {
        "2026 aasta riigieelarve seadus": {
            "name": "2026_aasta_riigieelarve_seadus",
            "slug": "2026_aasta_riigieelarve_seadus",
            "files": ["2026_aasta_riigieelarve_seadus_peep.json"],
        },
        "2026_aasta_riigieelarve_seadus": {
            "name": "2026_aasta_riigieelarve_seadus",
            "slug": "2026_aasta_riigieelarve_seadus",
            "files": ["2026_aasta_riigieelarve_seadus_peep.json"],
        },
        "riigieelarve seadus": {
            "name": "riigieelarve_seadus",
            "slug": "riigieelarve_seadus",
            "files": ["riigieelarve_seadus_peep.json"],
        },
        "riigieelarve_seadus": {
            "name": "riigieelarve_seadus",
            "slug": "riigieelarve_seadus",
            "files": ["riigieelarve_seadus_peep.json"],
        },
    }


def test_year_prefixed_budget_bill_does_not_resolve_to_other_year():
    result = resolve_law_name(
        "2016. aasta riigieelarve seaduse muutmise seadus",
        _lookup(),
    )
    assert result is None


def test_yearless_extracted_name_still_uses_title_year():
    """#557 title detect may drop '2017.'; the title must still year-guard."""
    result = resolve_law_name(
        "aasta riigieelarve seadus",
        _lookup(),
        context_text="2017. aasta riigieelarve seaduse muutmise seadus",
    )
    assert result is None


def test_yearless_framework_budget_bill_still_resolves():
    # Resolver input is the affected-law name, not the bill's own title.
    result = resolve_law_name("riigieelarve seaduse", _lookup())
    assert result is not None
    assert result["slug"] == "riigieelarve_seadus"


def test_matching_year_annual_budget_still_resolves():
    result = resolve_law_name("2026. aasta riigieelarve seadus", _lookup())
    assert result is not None
    assert result["slug"] == "2026_aasta_riigieelarve_seadus"


def test_year_compatible_helper_rejects_conflicting_years():
    assert year_compatible_law_match(
        "2016. aasta riigieelarve seadus",
        BUDGET_2026,
    ) is False
    assert year_compatible_law_match(
        "Riigieelarve seaduse muutmise seadus",
        BUDGET_2026,
    ) is True
    # Underscore-delimited IRI years must count (``2026_aasta``).
    assert year_compatible_law_match(
        "2016. aasta riigieelarve seaduse muutmise seadus",
        "estleg:2026_aasta_riigieelarve_seadus_Map",
    ) is False
    # The ``_Map`` snapshot stamp on a yearless act is not a year conflict.
    assert year_compatible_law_match(
        "2016. aasta riigieelarve seaduse muutmise seadus",
        "estleg:REELS_Map",
    ) is True
    # Treaty founding years are identity, not vintage — do not reject.
    assert year_compatible_law_match(
        "Troopilise puidu 2006. aasta lepingu ratifitseerimise seadus",
        "estleg:2006_aasta_rahvusvaheline_troopilise_pui_Map",
    ) is True


def test_committed_eelnoud_has_no_cross_year_budget_amends():
    """Corpus gate: no draft whose title/affected name names year Y
    may amendsLaw the 2026 annual budget (or any other differently-year'd IRI).
    """
    doc = json.loads(EELNOUD.read_text(encoding="utf-8"))
    bad: list[tuple[str, str, str]] = []
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        raw = node.get("estleg:amendsLaw")
        if not raw:
            continue
        items = raw if isinstance(raw, list) else [raw]
        title = jsonld_text(node.get("rdfs:label", ""))
        names = jsonld_texts(node.get("estleg:affectedLawName"))
        query = " ".join([title, *names]).strip()
        for item in items:
            iri = item.get("@id") if isinstance(item, dict) else str(item)
            if iri and not year_compatible_law_match(query, iri):
                bad.append((str(node.get("@id")), query[:80], iri))
    assert bad == [], f"cross-year amendsLaw leftovers: {bad[:8]}"
