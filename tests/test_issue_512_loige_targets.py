"""#512: references / interpretsLaw resolve to existing lõige nodes."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.extract_court_provision_links import (
    extract_citations_from_text as court_extract,
    resolve_citations as court_resolve,
)
from estleg.extract_cross_references import (
    extract_citations_from_text,
    resolve_citation,
)

REPO = Path(__file__).resolve().parent.parent
KARS = REPO / "krr_outputs" / "karistusseadustik_osa2_peep.json"

PREFIX_TO_PROVISIONS = {
    "KARIST_2_Osa2": {
        "88": "estleg:KARIST_2_Osa2_Par_88",
        "88_Lg_1": "estleg:KARIST_2_Osa2_Par_88_Lg_1",
        "88_Lg_2": "estleg:KARIST_2_Osa2_Par_88_Lg_2",
        "99": "estleg:KARIST_2_Osa2_Par_99",
    }
}
ABBREV = {"KarS": ["KARIST_2_Osa2"]}


def test_extract_captures_lg_number() -> None:
    cites = extract_citations_from_text("KarS § 88 lg 2")
    assert cites
    assert cites[0]["paragraphs"] == ["88"]
    assert cites[0]["lg"] == "2"


def test_resolve_citation_prefers_existing_loige() -> None:
    cit = {
        "law_ref": "KarS",
        "paragraphs": ["88"],
        "lg": "2",
        "is_self_ref": False,
    }
    assert resolve_citation(cit, "", ABBREV, PREFIX_TO_PROVISIONS) == [
        "estleg:KARIST_2_Osa2_Par_88_Lg_2"
    ]


def test_resolve_citation_falls_back_to_section_when_loige_missing() -> None:
    cit = {
        "law_ref": "KarS",
        "paragraphs": ["99"],
        "lg": "7",
        "is_self_ref": False,
    }
    assert resolve_citation(cit, "", ABBREV, PREFIX_TO_PROVISIONS) == [
        "estleg:KARIST_2_Osa2_Par_99"
    ]


def test_resolve_citation_without_lg_stays_on_section() -> None:
    cit = {
        "law_ref": "KarS",
        "paragraphs": ["88"],
        "is_self_ref": False,
    }
    assert resolve_citation(cit, "", ABBREV, PREFIX_TO_PROVISIONS) == [
        "estleg:KARIST_2_Osa2_Par_88"
    ]


def test_court_resolver_prefers_loige() -> None:
    state, _kov = court_extract("KarS § 88 lg 2")
    assert state
    assert state[0].get("lg") == "2"
    hits = court_resolve(state, ABBREV, PREFIX_TO_PROVISIONS)
    assert hits == ["estleg:KARIST_2_Osa2_Par_88_Lg_2"]


def test_committed_kars_has_loige_reference_target() -> None:
    doc = json.loads(KARS.read_text(encoding="utf-8"))
    lg_hits = 0
    for node in doc.get("@graph") or []:
        if not isinstance(node, dict):
            continue
        for ref in node.get("estleg:references") or []:
            iri = ref.get("@id") if isinstance(ref, dict) else None
            if isinstance(iri, str) and "_Lg_" in iri:
                lg_hits += 1
    assert lg_hits > 0


def test_committed_court_interprets_a_loige() -> None:
    hits = 0
    for path in (REPO / "krr_outputs" / "riigikohus").glob("riigikohus_*_peep.json"):
        text = path.read_text(encoding="utf-8")
        if "interpretsLaw" not in text or "_Lg_" not in text:
            continue
        doc = json.loads(text)
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            for ref in node.get("estleg:interpretsLaw") or []:
                iri = ref.get("@id") if isinstance(ref, dict) else None
                if isinstance(iri, str) and "_Lg_" in iri:
                    hits += 1
                    break
        if hits:
            break
    assert hits > 0
