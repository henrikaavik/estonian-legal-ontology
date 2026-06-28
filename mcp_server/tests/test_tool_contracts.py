"""Per-tool contract tests for the estleg_mcp server (ticket #495).

Each of the 10 MCP tools advertises a documented return-field set in its
docstring (the contract a lawmaker-facing client codes against). These tests
boot the tools against the committed ``krr_outputs/`` corpus and assert, for
every tool:

* the documented field set is present on each returned item;
* the key behaviours the ticket calls out -- search recall, OWL/TBox
  ``*_owl`` module files ABSENT from search/resolution, the ``limit`` overflow
  cap on the list tools, and real riigiteataja.ee / riigikohus.ee /
  eelnoud.valitsus.ee / EUR-Lex citation strings; and
* the "no match returns an empty list (or a ``note``), never an error" guard.

They complement the data-layer unit tests in ``test_data.py`` and the two
regression smoke tests in ``test_server.py``. Like those, the whole module
skips when the corpus is absent, and the assertions are strict about shapes /
citation prefixes but lenient about exact counts (the corpus is regenerated).
"""

from __future__ import annotations

import pytest

from estleg_mcp import data, server

try:
    data.corpus_root()
    _CORPUS_AVAILABLE = True
except FileNotFoundError:
    _CORPUS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _CORPUS_AVAILABLE,
    reason="Estonian Legal Ontology corpus (krr_outputs/INDEX.json) not found",
)

# A law that is reliably present and richly populated in the committed corpus.
KARS = "KarS"
RT_PREFIX = "https://www.riigiteataja.ee/akt/"


def _assert_fields(item: dict, expected: set[str]) -> None:
    """Every documented field must be present on the returned item."""
    missing = expected - set(item)
    assert not missing, f"missing documented fields {missing} in {item!r}"


# ---------------------------------------------------------------------------
# 1. search_laws -> {name, title, abbrev, rt_url, status}
# ---------------------------------------------------------------------------
SEARCH_FIELDS = {"name", "title", "abbrev", "rt_url", "status"}


def test_search_laws_contract_fields_and_recall() -> None:
    hits = server.search_laws("karistus", limit=20)
    assert hits, "search recall: 'karistus' must surface at least one law"
    for h in hits:
        _assert_fields(h, SEARCH_FIELDS)
    # Recall: the Penal Code itself must be in the results.
    names = {h["name"] for h in hits}
    assert "karistusseadustik" in names
    # A real citation accompanies a resolvable law.
    kars = next(h for h in hits if h["name"] == "karistusseadustik")
    assert kars["rt_url"].startswith(RT_PREFIX)


def test_search_laws_excludes_owl_tbox_modules() -> None:
    # OWL/TBox module artifacts (``*_owl.jsonld``: karistusseadustik_eriosa_owl,
    # tsus_osa7_138_169_owl) must NOT surface as queryable laws even though
    # their slug contains the search needle.
    for needle in ("karistus", "tsus", "eriosa", "owl"):
        names = {h["name"] for h in server.search_laws(needle, limit=50)}
        leaked = {n for n in names if n.endswith("_owl")}
        assert not leaked, f"OWL/TBox module leaked into search({needle!r}): {leaked}"
    # ... and they must not resolve either.
    assert data.resolve_law("karistusseadustik_eriosa_owl") is None
    assert data.resolve_law("tsus_osa7_138_169_owl") is None


def test_search_laws_limit_cap_and_empty() -> None:
    assert len(server.search_laws("seadus", limit=5)) <= 5
    assert server.search_laws("seadus", limit=0) == []
    assert server.search_laws("zzzqqq-nope-xyz") == []


# ---------------------------------------------------------------------------
# 2. get_law -> {title, abbrev, status, consolidated_as_of, rt_url,
#                eurovoc_subjects, num_provisions, num_chapters}
# ---------------------------------------------------------------------------
GET_LAW_FIELDS = {
    "title",
    "abbrev",
    "status",
    "consolidated_as_of",
    "rt_url",
    "eurovoc_subjects",
    "num_provisions",
    "num_chapters",
}


def test_get_law_contract_fields() -> None:
    law = server.get_law(KARS)
    _assert_fields(law, GET_LAW_FIELDS)
    assert law["abbrev"] == "KarS"
    assert law["rt_url"].startswith(RT_PREFIX)
    assert isinstance(law["eurovoc_subjects"], list)
    assert law["num_provisions"] > 10
    assert isinstance(law["num_chapters"], int)


def test_get_law_not_found_returns_note() -> None:
    out = server.get_law("definitely-not-a-real-law-xyz")
    assert "note" in out  # graceful note, never an exception


# ---------------------------------------------------------------------------
# 3. get_provision -> {id, paragrahv, label, summary, legal_text, rt_url}
# ---------------------------------------------------------------------------
GET_PROVISION_FIELDS = {"id", "paragrahv", "label", "summary", "legal_text", "rt_url"}


def test_get_provision_contract_fields() -> None:
    prov = server.get_provision(KARS, "§ 13")
    _assert_fields(prov, GET_PROVISION_FIELDS)
    assert "13" in prov["paragrahv"]
    assert prov["legal_text"]
    assert prov["rt_url"].startswith(RT_PREFIX)


def test_get_provision_truncates_long_text() -> None:
    prov = server.get_provision(KARS, "§ 13")
    # Tool caps legal text to keep a provision chat-sized.
    assert len(prov["legal_text"]) <= server._MAX_LEGAL_TEXT


def test_get_provision_missing_paragraph_returns_note() -> None:
    # Missing § in a real law -> a note, not an exception.
    assert "note" in server.get_provision(KARS, "999999")
    # Missing law altogether -> the law-not-found note.
    assert "note" in server.get_provision("no-such-law-xyz", "§ 1")


# ---------------------------------------------------------------------------
# 4/5. who_references & references_of -> {source_id, source_label,
#                                         source_law, rt_url}
# ---------------------------------------------------------------------------
REFERENCE_FIELDS = {"source_id", "source_label", "source_law", "rt_url"}


def test_who_references_contract_fields() -> None:
    items = server.who_references(KARS)
    assert items, "KarS has incoming references in the corpus"
    for it in items[:10]:
        _assert_fields(it, REFERENCE_FIELDS)
        assert it["source_id"]  # an IRI is always present


def test_references_of_contract_fields() -> None:
    items = server.references_of(KARS)
    assert items, "KarS references something"
    for it in items[:10]:
        _assert_fields(it, REFERENCE_FIELDS)
    # Cross-law targets carry a riigiteataja URL when resolvable.
    assert any(it["rt_url"].startswith(RT_PREFIX) for it in items)


def test_reference_tools_unknown_law_returns_empty() -> None:
    assert server.who_references("no-such-law-xyz") == []
    assert server.references_of("no-such-law-xyz") == []


# ---------------------------------------------------------------------------
# 6. drafts_affecting_law -> {title, eis_number, phase, link}
# ---------------------------------------------------------------------------
DRAFT_FIELDS = {"title", "eis_number", "phase", "link"}


def test_drafts_affecting_law_contract_fields_and_citation() -> None:
    items = server.drafts_affecting_law("TLS", limit=10)
    assert items, "TLS has pending drafts in the corpus"
    for it in items:
        _assert_fields(it, DRAFT_FIELDS)
    # At least one resolved draft links to EIS (eelnoud.valitsus.ee).
    assert any(it["link"].startswith("https://eelnoud.valitsus.ee/") for it in items)


def test_drafts_affecting_law_limit_cap_and_empty() -> None:
    assert len(server.drafts_affecting_law("TLS", limit=3)) <= 3
    assert server.drafts_affecting_law("TLS", limit=0) == []
    assert server.drafts_affecting_law("no-such-law-xyz") == []


# ---------------------------------------------------------------------------
# 7. court_decisions_for_law -> {case_number, label, decision_link}
# ---------------------------------------------------------------------------
COURT_FIELDS = {"case_number", "label", "decision_link"}


def test_court_decisions_contract_fields_and_citation() -> None:
    items = server.court_decisions_for_law(KARS, limit=10)
    assert items, "KarS is interpreted by Riigikohus decisions in the corpus"
    for it in items:
        _assert_fields(it, COURT_FIELDS)
    # Real riigikohus.ee citation strings.
    assert any(
        it["decision_link"].startswith("https://www.riigikohus.ee/") for it in items
    )


def test_court_decisions_limit_cap_and_empty() -> None:
    # KarS has more than 3 linked decisions, so the cap is exercised, not just
    # a short list that happens to fit.
    assert len(server.court_decisions_for_law(KARS, limit=3)) == 3
    assert server.court_decisions_for_law(KARS, limit=0) == []
    assert server.court_decisions_for_law("no-such-law-xyz") == []


# ---------------------------------------------------------------------------
# 8. sanctions_for_law -> {provision, sanction_type, penalty, rt_url}
# ---------------------------------------------------------------------------
SANCTION_FIELDS = {"provision", "sanction_type", "penalty", "rt_url"}


def test_sanctions_for_law_contract_fields_and_citation() -> None:
    items = server.sanctions_for_law(KARS)
    assert items, "KarS defines sanctions in the corpus"
    for it in items[:20]:
        _assert_fields(it, SANCTION_FIELDS)
        # Every sanction carries the act's riigiteataja URL.
        assert it["rt_url"].startswith(RT_PREFIX)
    # The penalty string is populated for at least some sanctions.
    assert any(it["penalty"] for it in items)


def test_sanctions_for_law_unknown_returns_empty() -> None:
    # sanctions_for_law takes no ``limit`` (unlike search/drafts/court); the
    # contract here is graceful emptiness on a miss, never an exception.
    assert server.sanctions_for_law("no-such-law-xyz") == []


# ---------------------------------------------------------------------------
# 9. competent_authority_for_law -> {institution, provision_count}
# ---------------------------------------------------------------------------
AUTHORITY_FIELDS = {"institution", "provision_count"}


def test_competent_authority_contract_fields_and_ranking() -> None:
    items = server.competent_authority_for_law(KARS)
    assert items, "KarS names competent authorities in the corpus"
    for it in items:
        _assert_fields(it, AUTHORITY_FIELDS)
        assert it["institution"]
        assert isinstance(it["provision_count"], int)
    # Ranked most-cited first (non-increasing provision_count).
    counts = [it["provision_count"] for it in items]
    assert counts == sorted(counts, reverse=True)


def test_competent_authority_unknown_returns_empty() -> None:
    assert server.competent_authority_for_law("no-such-law-xyz") == []


# ---------------------------------------------------------------------------
# 10. transposition -> {directive_celex, eurlex_url, national_title,
#                       matched_law_name}
# ---------------------------------------------------------------------------
TRANSPOSITION_FIELDS = {
    "directive_celex",
    "eurlex_url",
    "national_title",
    "matched_law_name",
}


def test_transposition_by_celex_contract_fields() -> None:
    items = server.transposition("31990L0314")
    assert items
    for it in items:
        _assert_fields(it, TRANSPOSITION_FIELDS)
    hit = next(it for it in items if it["directive_celex"] == "31990L0314")
    assert hit["eurlex_url"] == (
        "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:31990L0314"
    )
    assert hit["national_title"]


def test_transposition_by_law_name_other_direction() -> None:
    # Passing a law (abbreviation) must find the directive(s) it transposes.
    items = server.transposition(KARS)
    assert items, "KarS transposes EU directives in the corpus"
    for it in items:
        _assert_fields(it, TRANSPOSITION_FIELDS)
        assert it["eurlex_url"].startswith(
            "https://eur-lex.europa.eu/legal-content/"
        )


def test_transposition_no_match_returns_empty() -> None:
    assert server.transposition("zzzqqq-nope-xyz") == []
    assert server.transposition("") == []
