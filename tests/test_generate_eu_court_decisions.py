"""Tests for ``scripts/generate_eu_court_decisions.py``.

Covers the classification / encoding regressions tracked in:

* #343 — EFTA Court (CELEX sector ``E``) single-letter type codes
  (``E2014J0018``) must classify as ``EUCourt_EFTACourt`` (the regex used
  to require two letters, so the EFTA branch was dead code); and the
  ``cdm:case-law`` SPARQL branch must exclude national-court (CELEX
  sector ``8``) works so they are never emitted as
  ``EUCourt_CourtOfJustice``.
* #355 — non-breaking spaces (U+00A0) in curia titles must be normalized
  to a regular space in ``clean_title`` / ``truncate_label`` so emitted
  ``rdfs:label`` values are not NBSP-bearing.
* #383 — CELEX type code ``TT`` (General Court tierce-opposition order,
  e.g. ``62016TT0624``) must map to a General Court ``Order`` rather than
  fall through to ``CourtOfJustice``/``Other``.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_eu_court_decisions as mod  # noqa: E402

NBSP = "\u00a0"  # non-breaking space (U+00A0)


# ---------------------------------------------------------------------------
# #343 (a) — EFTA Court single-letter CELEX type codes
# ---------------------------------------------------------------------------


def test_efta_judgment_classifies_as_efta_court() -> None:
    """``E2014J0018`` (EFTA Court judgment) → EFTACourt / Judgment.

    The old regex ``([6E])\\d{4}([A-Z]{2})`` required two letters, so the
    single-letter ``J`` never matched and the EFTA branch was unreachable
    (#343). The widened ``[A-Z]{1,2}`` must now resolve it.
    """
    type_id, type_label, court_id, category = mod.classify_from_celex("E2014J0018")
    assert court_id == "EFTACourt"
    assert type_id == "Judgment"
    assert type_label == "Kohtuotsus"
    assert category == "judgments"


def test_efta_order_and_ag_opinion_single_letter_codes() -> None:
    """EFTA single-letter ``O``/``P`` → Order, ``A`` → AGOpinion (#343)."""
    assert mod.classify_from_celex("E2020O0001")[:3] == (
        "Order",
        "Kohtumäärus",
        "EFTACourt",
    )
    assert mod.classify_from_celex("E2021P0001")[:3] == (
        "Order",
        "Kohtumäärus",
        "EFTACourt",
    )
    assert mod.classify_from_celex("E2019A0001")[:3] == (
        "AGOpinion",
        "Kohtujuristi ettepanek",
        "EFTACourt",
    )


def test_efta_two_letter_code_falls_back_to_shared_map() -> None:
    """A two-letter EFTA work (``E2024CB0001``) still resolves via the shared
    ``CELEX_TYPE_MAP`` (``CB`` → Order) while keeping the EFTACourt bucket so
    EFTA jurisprudence stays out of CJEU statistics (#343)."""
    type_id, _, court_id, category = mod.classify_from_celex("E2024CB0001")
    assert court_id == "EFTACourt"
    assert type_id == "Order"
    assert category == "orders"


def test_efta_decision_to_node_emits_efta_court_individual() -> None:
    """End-to-end: an EFTA item must point at ``estleg:EUCourt_EFTACourt``
    (previously every EFTA decision was stamped CourtOfJustice) (#343)."""
    item = {
        "celex": "E2014J0018",
        "title": "Kohtuasi E-18/14",
        "date": "2014-05-01",
        "ecli": "",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    assert node["estleg:euCourt"] == {"@id": "estleg:EUCourt_EFTACourt"}
    assert node["estleg:euCourtDecisionType"] == {"@id": "estleg:EUDecType_Judgment"}


def test_efta_single_letter_code_not_recorded_as_unknown() -> None:
    """EFTA single-letter codes are *known* — they must not leak into the
    ``unknown_celex_type_codes`` index field (#343)."""
    mod.UNKNOWN_CELEX_CODES.clear()
    try:
        mod.classify_from_celex("E2014J0018")
        mod.classify_from_celex("E2020O0001")
        assert mod.UNKNOWN_CELEX_CODES == set()
    finally:
        mod.UNKNOWN_CELEX_CODES.clear()


# ---------------------------------------------------------------------------
# #343 (b) — national-court (CELEX sector 8) works must not be emitted as
# EUCourt_CourtOfJustice.
# ---------------------------------------------------------------------------


def test_case_law_query_filters_to_sector_6_and_e() -> None:
    """The case-law SPARQL must restrict CELEX to the CJEU (``6``) and EFTA
    (``E``) sectors so national-court (sector ``8``) works are never
    ingested and mis-stamped as CourtOfJustice (#343)."""
    src = inspect.getsource(mod.fetch_all_case_law)
    assert 'STRSTARTS(?celex, "6")' in src
    assert 'STRSTARTS(?celex, "E")' in src
    # Both arms in a single FILTER OR — the union, not just one sector.
    assert 'STRSTARTS(?celex, "6") || STRSTARTS(?celex, "E")' in src


def test_sector_8_celex_not_classified_as_court_of_justice_judgment() -> None:
    """A sector-8 national-court CELEX does not match the ``[6E]`` regex, so it
    falls through to the ``Other`` default rather than being positively
    classified as a CourtOfJustice judgment/order (#343).

    The real defense is the SPARQL FILTER above (such rows never enter the
    pipeline); this guards the classifier so a stray sector-8 value can never
    be typed as a genuine CJEU decision.
    """
    type_id, type_label, court_id, category = mod.classify_from_celex("82016CJ0001")
    # Not a matched CJEU type — the default fallthrough.
    assert (type_id, type_label, category) == ("Other", "Muu", "other")
    # It must NOT be classified as a Judgment/Order/AGOpinion of any court.
    assert type_id != "Judgment"


# ---------------------------------------------------------------------------
# #383 — CELEX type code TT (General Court tierce-opposition order)
# ---------------------------------------------------------------------------


def test_tt_code_maps_to_general_court_order() -> None:
    """``62016TT0624`` (ECLI:EU:T:2019:47, case T-624/16) → General Court /
    Order, not the CourtOfJustice/Other default (#383)."""
    type_id, type_label, court_id, category = mod.classify_from_celex("62016TT0624")
    assert court_id == "GeneralCourt"
    assert type_id == "Order"
    assert type_label == "Kohtumäärus"
    assert category == "orders"


def test_tt_present_in_celex_maps() -> None:
    """The ``TT`` entry is registered in both lookup tables (#383)."""
    assert mod.CELEX_TYPE_MAP["TT"] == ("Order", "Kohtumäärus", "Order")
    assert mod.CELEX_COURT_MAP["TT"] == "GeneralCourt"


def test_tt_decision_to_node_emits_general_court_order() -> None:
    """End-to-end node for a ``TT`` work points at the General Court order
    individuals (#383)."""
    item = {
        "celex": "62016TT0624",
        "title": "Kohtuasi T-624/16",
        "date": "2019-01-31",
        "ecli": "ECLI:EU:T:2019:47",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    assert node["estleg:euCourt"] == {"@id": "estleg:EUCourt_GeneralCourt"}
    assert node["estleg:euCourtDecisionType"] == {"@id": "estleg:EUDecType_Order"}
    assert "estleg:curiaLink" not in node
    assert node["estleg:eurLexLink"]["@value"].endswith("CELEX:62016TT0624")
    same_as = {item["@id"] for item in node["owl:sameAs"]}
    assert mod.cellar_celex_iri("62016TT0624") in same_as
    assert mod.cellar_ecli_iri("ECLI:EU:T:2019:47") in same_as


# ---------------------------------------------------------------------------
# #355 — non-breaking space (U+00A0) normalization
# ---------------------------------------------------------------------------


def test_clean_title_normalizes_nbsp() -> None:
    """``clean_title`` collapses U+00A0 to a regular space (#355)."""
    out = mod.clean_title("C-534/11#M." + NBSP + "Wathelet")
    assert NBSP not in out
    assert "M. Wathelet" in out


def test_truncate_label_normalizes_nbsp() -> None:
    """``truncate_label`` normalizes NBSP even when called directly on a
    not-yet-cleaned string, and even for short (untruncated) input (#355)."""
    out = mod.truncate_label("M." + NBSP + "Wathelet")
    assert NBSP not in out
    assert out == "M. Wathelet"


def test_truncate_label_normalizes_nbsp_when_truncating() -> None:
    """NBSP normalization also applies on the truncation path (#355)."""
    long_title = ("Liidetud kohtuasjad" + NBSP) * 60  # > 500 chars
    out = mod.truncate_label(long_title, max_len=500)
    assert NBSP not in out
    assert out.endswith("...")
    assert len(out) <= 500


def test_decision_node_label_has_no_nbsp() -> None:
    """The emitted ``rdfs:label`` and ``dcterms:title`` must be NBSP-free for
    a title carrying non-breaking spaces (#355)."""
    item = {
        "celex": "62011CC0534",
        "title": "Kohtuasi C-534/11#M." + NBSP + "Wathelet#Kohtujuristi ettepanek",
        "date": "2012-06-21",
        "ecli": "",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    assert NBSP not in node["rdfs:label"]["@value"]
    assert NBSP not in node["dcterms:title"]["@value"]


# ---------------------------------------------------------------------------
# Regression guards — widened regex must not break existing CJEU codes.
# ---------------------------------------------------------------------------


def test_two_letter_cjeu_codes_still_classify() -> None:
    """Greedy ``[A-Z]{1,2}`` must keep preferring two-letter CJEU codes so
    the existing classification is unchanged (#343)."""
    assert mod.classify_from_celex("62016CJ0438")[:3] == (
        "Judgment",
        "Kohtuotsus",
        "CourtOfJustice",
    )
    assert mod.classify_from_celex("62011CC0534")[2] == "CourtOfJustice"
    assert mod.classify_from_celex("62016TO0123")[2:] == ("GeneralCourt", "orders")


# ---------------------------------------------------------------------------
# #606 (item 5) — joined cases separated by a comma must all be captured
# ---------------------------------------------------------------------------


def test_extract_case_numbers_keeps_all_comma_joined_cases() -> None:
    """``extract_case_numbers`` returns *every* case in a comma-joined title.

    The single-value ``extract_case_number`` only captures the first
    contiguous run (a ``" ja "`` chain), so it dropped ``C-429/08`` from
    ``'Liidetud kohtuasjad C-403/08, C-429/08'`` (#606)."""
    assert mod.extract_case_numbers(
        "Liidetud kohtuasjad C-403/08, C-429/08"
    ) == ["C-403/08", "C-429/08"]


def test_extract_case_numbers_single_case_is_one_element_list() -> None:
    """A single-case title yields a one-element list (#606)."""
    assert mod.extract_case_numbers("Kohtuasi C-438/14") == ["C-438/14"]


def test_extract_case_numbers_handles_ja_separator() -> None:
    """The historic ``" ja "`` joined-case form is still fully captured (#606)."""
    assert mod.extract_case_numbers(
        "Liidetud kohtuasjad C-6/90 ja C-9/90"
    ) == ["C-6/90", "C-9/90"]


def test_extract_case_numbers_dedupes_preserving_order() -> None:
    """Repeated numbers are de-duplicated in first-seen order (#606)."""
    assert mod.extract_case_numbers(
        "C-403/08, C-429/08, C-403/08"
    ) == ["C-403/08", "C-429/08"]


def test_extract_case_numbers_no_match_returns_empty_list() -> None:
    """No case number → empty list, so the caller emits nothing (#606)."""
    assert mod.extract_case_numbers("Pealkiri ilma numbrita") == []


def test_decision_to_node_emits_all_joined_case_numbers() -> None:
    """The node builder emits every joined case number as a multi-valued
    ``estleg:euCaseNumber`` (#606).

    NOTE: the SHACL ``EUCourtDecisionShape`` currently pins this property to
    ``sh:maxCount 1``; emitting a list is the intended behaviour and a
    deferred maxCount relaxation is required before regeneration."""
    item = {
        "celex": "62008CJ0403",
        "title": "Liidetud kohtuasjad C-403/08, C-429/08",
        "date": "2011-10-04",
        "ecli": "",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    assert node["estleg:euCaseNumber"] == ["C-403/08", "C-429/08"]


def test_decision_to_node_single_case_number_is_list() -> None:
    """A single-case node carries a one-element list (#606)."""
    item = {
        "celex": "62016CJ0438",
        "title": "Kohtuasi C-438/14",
        "date": "2016-10-13",
        "ecli": "",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    assert node["estleg:euCaseNumber"] == ["C-438/14"]


# ---------------------------------------------------------------------------
# #606 (item 13a) — documentDate plausibility (year-range) guard
# ---------------------------------------------------------------------------


def test_decision_to_node_skips_implausible_year(capsys) -> None:
    """A format-valid but implausible year (``0044-01-01``) is skipped with a
    warning, never emitted as an ``xsd:date`` (#606)."""
    item = {
        "celex": "62008CJ0001",
        "title": "Kohtuasi C-1/08",
        "date": "0044-01-01",
        "ecli": "",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    assert "estleg:documentDate" not in node
    err = capsys.readouterr().err
    assert "documentDate" in err
    assert "0044-01-01" in err


def test_schema_declares_eurlex_link_not_curialink() -> None:
    """#441: the CURIA schema property is ``eurLexLink``, not ``curiaLink``."""
    nodes = {node.get("@id"): node for node in mod.generate_schema_nodes()}
    assert "estleg:eurLexLink" in nodes
    assert "estleg:curiaLink" not in nodes
    # #442: ecliIdentifier is shared with Riigikohus CourtDecision.
    assert "rdfs:domain" not in nodes["estleg:ecliIdentifier"]


def test_decision_to_node_emits_valid_recent_date() -> None:
    """A plausible recent date (``2015-03-04``) is emitted unchanged (#606)."""
    item = {
        "celex": "62013CJ0333",
        "title": "Kohtuasi C-333/13",
        "date": "2015-03-04",
        "ecli": "",
        "authors": [],
    }
    node = mod.decision_to_node(item)
    assert node["estleg:documentDate"] == {
        "@value": "2015-03-04",
        "@type": "xsd:date",
    }
