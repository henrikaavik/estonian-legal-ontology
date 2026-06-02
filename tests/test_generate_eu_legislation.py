from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_eu_legislation as mod  # noqa: E402


# ---------------------------------------------------------------------------
# #288 — transpositionDeadline must be directive-only; euInstitution must be
# deterministically sorted.
# ---------------------------------------------------------------------------


def test_regulation_node_never_gets_transposition_deadline() -> None:
    """Even if a (Regulation) item carries a transposition_deadline value, the
    node must NOT emit estleg:transpositionDeadline — it is directive-only
    (#288). Correctness must not rely on the CELLAR modelling assumption.
    """
    item = {
        "celex": "32016R0679",
        "title": "Some regulation",
        "transposition_deadline": "2018-05-25",
        "authors": [],
    }
    node = mod.legislation_to_node(item, "Regulation")
    assert "estleg:transpositionDeadline" not in node


def test_decision_node_never_gets_transposition_deadline() -> None:
    """A Decision node must not emit a transposition deadline either (#288)."""
    item = {
        "celex": "32016D0001",
        "title": "Some decision",
        "transposition_deadline": "2018-05-25",
        "authors": [],
    }
    node = mod.legislation_to_node(item, "Decision")
    assert "estleg:transpositionDeadline" not in node


def test_directive_node_keeps_transposition_deadline() -> None:
    """A Directive node still emits the deadline when present (#96/#288)."""
    item = {
        "celex": "32011L0083",
        "title": "Consumer rights directive",
        "transposition_deadline": "2013-12-13",
        "authors": [],
    }
    node = mod.legislation_to_node(item, "Directive")
    assert node["estleg:transpositionDeadline"] == {
        "@value": "2013-12-13",
        "@type": "xsd:date",
    }


def test_directive_without_deadline_emits_no_property() -> None:
    """A directive with no deadline value emits nothing (deadline optional)."""
    item = {
        "celex": "31980L0766",
        "title": "Old directive",
        "transposition_deadline": "",
        "authors": [],
    }
    node = mod.legislation_to_node(item, "Directive")
    assert "estleg:transpositionDeadline" not in node


def test_multi_author_euinstitution_is_sorted_and_deterministic() -> None:
    """Multi-author works emit a deterministically sorted euInstitution list,
    regardless of the SPARQL row order the authors arrived in (#288).
    """
    # EP -> EuropeanParliament, COM -> EuropeanCommission, CONSIL -> CouncilOfEU.
    # Authors deliberately given in non-sorted order.
    item_a = {
        "celex": "32016R0679",
        "title": "GDPR",
        "authors": ["EP", "CONSIL", "COM"],
    }
    item_b = {
        "celex": "32016R0679",
        "title": "GDPR",
        "authors": ["COM", "EP", "CONSIL"],  # different row order
    }
    node_a = mod.legislation_to_node(item_a, "Regulation")
    node_b = mod.legislation_to_node(item_b, "Regulation")

    # Byte-identical output regardless of incoming author order.
    assert node_a["estleg:euInstitution"] == node_b["estleg:euInstitution"]

    # And the codes are sorted (COM < CONSIL < EP), mapped to their inst ids.
    expected = [
        {"@id": "estleg:EUInst_EuropeanCommission"},   # COM
        {"@id": "estleg:EUInst_CouncilOfEU"},          # CONSIL
        {"@id": "estleg:EUInst_EuropeanParliament"},   # EP
    ]
    assert node_a["estleg:euInstitution"] == expected


def test_duplicate_authors_are_deduplicated_in_euinstitution() -> None:
    """Repeated author codes collapse to a single euInstitution ref (#288)."""
    item = {
        "celex": "32016R0679",
        "title": "GDPR",
        "authors": ["COM", "COM", "EP"],
    }
    node = mod.legislation_to_node(item, "Regulation")
    assert node["estleg:euInstitution"] == [
        {"@id": "estleg:EUInst_EuropeanCommission"},
        {"@id": "estleg:EUInst_EuropeanParliament"},
    ]


def test_single_author_euinstitution_is_object_not_list() -> None:
    """A lone author still emits a single object (not a one-element list)."""
    item = {"celex": "32016R0679", "title": "x", "authors": ["COM"]}
    node = mod.legislation_to_node(item, "Regulation")
    assert node["estleg:euInstitution"] == {"@id": "estleg:EUInst_EuropeanCommission"}


def test_directive_query_includes_deadline_optional() -> None:
    """The fetch query still projects the transposition-deadline OPTIONAL so
    directives can carry it (the guard is at emit time, not query time)."""
    import inspect

    src = inspect.getsource(mod.fetch_legislation_type)
    assert "cdm:directive_date_transposition" in src
