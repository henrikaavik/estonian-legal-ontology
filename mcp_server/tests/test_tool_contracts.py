"""Per-tool contract tests for the estleg_mcp server (ticket #495).

Each of the 14 MCP tools advertises a documented return-field set in its
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

from datetime import date, timedelta

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


def _data_rows(items: list[dict]) -> list[dict]:
    """The real result rows, dropping any trailing overflow/note sentinel.

    The regulation list tools append a {note, overflow, total_available} entry
    when they truncate (see server._capped); contract assertions about the
    documented item fields apply to the data rows, not that sentinel.
    """
    return [it for it in items if "note" not in it]


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
    "ontology_version",
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
    assert law["ontology_version"] == data.ontology_version()
    assert law["ontology_version"]


def test_get_law_not_found_returns_note() -> None:
    out = server.get_law("definitely-not-a-real-law-xyz")
    assert "note" in out  # graceful note, never an exception


def test_get_law_without_as_of_is_unchanged() -> None:
    # The default overview is byte-identical to before: exactly the documented
    # fields, with no point-in-time keys leaking in.
    law = server.get_law(KARS)
    assert set(law) == GET_LAW_FIELDS


def test_get_law_as_of_reports_point_in_time_snapshot() -> None:
    # With as_of, the overview echoes the date and adds the count of sections
    # that had a redaction in force then (from the version layer).
    snap = server.get_law(KARS, as_of="2015-01-01")
    _assert_fields(snap, GET_LAW_FIELDS | {"as_of", "num_provisions_as_of"})
    assert snap["as_of"] == "2015-01-01"
    assert isinstance(snap["num_provisions_as_of"], int)
    assert snap["num_provisions_as_of"] > 0
    # The act-level overview fields are preserved alongside the snapshot.
    today = server.get_law(KARS)
    assert snap["rt_url"] == today["rt_url"]
    assert snap["title"] == today["title"]
    # Genuinely point-in-time: the Penal Code accreted sections over time, so an
    # earlier snapshot has no more sections in force than a later one.
    early = server.get_law(KARS, as_of="2003-01-01")
    assert early["num_provisions_as_of"] <= snap["num_provisions_as_of"]


def test_get_law_as_of_invalid_or_out_of_range_returns_note() -> None:
    # A non-date as_of -> note, never an exception.
    assert "note" in server.get_law(KARS, as_of="not-a-date")
    # A date before the Penal Code's earliest redaction -> no section in force.
    assert "note" in server.get_law(KARS, as_of="1900-01-01")
    # Missing law still degrades to a note even with as_of supplied.
    assert "note" in server.get_law("no-such-law-xyz", as_of="2015-01-01")


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


def test_get_provision_full_text_flag_skips_cap() -> None:
    capped = server.get_provision(KARS, "§ 13")
    full = server.get_provision(KARS, "§ 13", full_text=True)
    assert len(full["legal_text"]) >= len(capped["legal_text"])


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


def test_reference_tools_unknown_law_returns_note() -> None:
    note = [{"note": "law not found: no-such-law-xyz"}]
    assert server.who_references("no-such-law-xyz") == note
    assert server.references_of("no-such-law-xyz") == note


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
    assert server.drafts_affecting_law("no-such-law-xyz") == [
        {"note": "law not found: no-such-law-xyz"}
    ]


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
    assert server.court_decisions_for_law("no-such-law-xyz") == [
        {"note": "law not found: no-such-law-xyz"}
    ]


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


def test_sanctions_for_law_unknown_returns_note() -> None:
    assert server.sanctions_for_law("no-such-law-xyz") == [
        {"note": "law not found: no-such-law-xyz"}
    ]
    assert server.sanctions_for_law(KARS, limit=0) == []


def test_sanctions_for_law_limit_caps_kars() -> None:
    # #498: KarS has hundreds of sanctions; unbounded return overflowed chats.
    capped = server.sanctions_for_law(KARS, limit=5)
    assert len(capped) == 5
    uncapped = server.sanctions_for_law(KARS, limit=10_000)
    assert len(uncapped) > 5


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


def test_competent_authority_unknown_returns_note() -> None:
    assert server.competent_authority_for_law("no-such-law-xyz") == [
        {"note": "law not found: no-such-law-xyz"}
    ]


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


# ---------------------------------------------------------------------------
# 11. get_provision(as_of=...) -> historical redaction (ticket #499)
#     adds {as_of, redaction_id, valid_from, valid_to, currently_in_force}
# ---------------------------------------------------------------------------
AS_OF_FIELDS = GET_PROVISION_FIELDS | {
    "as_of",
    "redaction_id",
    "valid_from",
    "valid_to",
    "currently_in_force",
}


def test_get_provision_as_of_selects_historical_redaction() -> None:
    # KarS §13 has a clean redaction boundary in the corpus, so the date
    # selection picks a deterministic redaction either side of it. Derive the
    # boundary from the timeline itself (robust to a corpus rebuild) rather than
    # hard-coding redaction ids.
    hist = server.provision_history(KARS, "§ 13")
    assert len(hist) >= 2, "KarS §13 must have multiple recorded redactions"
    older, newer = hist[0], hist[1]

    # On the first day of the newer redaction, the tool returns the newer one.
    at_newer = server.get_provision(KARS, "§ 13", as_of=newer["valid_from"])
    _assert_fields(at_newer, AS_OF_FIELDS)
    assert at_newer["redaction_id"] == newer["redaction_id"]
    assert at_newer["valid_from"] == newer["valid_from"]
    assert at_newer["rt_url"].startswith(RT_PREFIX)

    # On the day BEFORE the newer redaction began, the older one is in force --
    # the corpus stores valid_to as the inclusive last in-force day, so the
    # boundary day must resolve to the older redaction, not fall into a gap.
    day_before = (
        date.fromisoformat(newer["valid_from"]) - timedelta(days=1)
    ).isoformat()
    at_older = server.get_provision(KARS, "§ 13", as_of=day_before)
    assert at_older["redaction_id"] == older["redaction_id"]
    # currently_in_force reports "still the live redaction today", NOT "in force
    # on as_of" -- so a historical hit is False without contradicting that it
    # was the text in force on the requested date (it was: that's why it was
    # selected). The older redaction has a closing date; the newer (open) one
    # does not.
    assert at_older["currently_in_force"] is False
    assert at_older["valid_to"] is not None
    assert at_newer["currently_in_force"] == (at_newer["valid_to"] is None)
    # The two redactions are genuinely different text (point-in-time, not just a
    # relabelled current text).
    assert at_older["legal_text"] != at_newer["legal_text"]


def test_get_provision_without_as_of_is_unchanged() -> None:
    # Omitting as_of keeps the original contract exactly: the consolidated text
    # and none of the point-in-time fields.
    prov = server.get_provision(KARS, "§ 13")
    _assert_fields(prov, GET_PROVISION_FIELDS)
    assert prov["legal_text"]
    assert not (AS_OF_FIELDS - GET_PROVISION_FIELDS) & set(prov)


def test_get_provision_as_of_invalid_or_out_of_range_returns_note() -> None:
    # A non-date as_of -> note, never an exception.
    assert "note" in server.get_provision(KARS, "§ 13", as_of="not-a-date")
    # A date long before the Penal Code's earliest redaction -> no version in
    # force -> note (rather than a misleadingly "current" text).
    assert "note" in server.get_provision(KARS, "§ 13", as_of="1900-01-01")
    # Missing law / § still degrade to a note even with as_of supplied.
    assert "note" in server.get_provision("no-such-law-xyz", "§ 1", as_of="2015-01-01")


# ---------------------------------------------------------------------------
# 12. provision_history -> {redaction_id, valid_from, valid_to,
#     currently_in_force, text}
# ---------------------------------------------------------------------------
HISTORY_FIELDS = {
    "redaction_id",
    "valid_from",
    "valid_to",
    "currently_in_force",
    "text",
}


def test_provision_history_ordered_timeline_and_fields() -> None:
    hist = server.provision_history(KARS, "§ 13")
    assert hist, "KarS §13 has recorded redactions in the corpus"
    for h in hist:
        _assert_fields(h, HISTORY_FIELDS)
        # currently_in_force is exactly "has no closing date".
        assert h["currently_in_force"] == (h["valid_to"] is None)
    # Ordered oldest-first by valid_from.
    froms = [h["valid_from"] for h in hist]
    assert froms == sorted(froms)
    # At most one redaction is currently in force (the open-ended latest one).
    assert sum(1 for h in hist if h["currently_in_force"]) <= 1


def test_provision_history_unknown_returns_note_or_empty() -> None:
    assert server.provision_history("no-such-law-xyz", "§ 1") == [
        {"note": "law not found: no-such-law-xyz"}
    ]
    # Known law, unknown § — empty success, not a not-found note.
    assert server.provision_history(KARS, "999999") == []


# ---------------------------------------------------------------------------
# 13. regulations_for_law -> {reg_id, title, issuer, status, rt_url,
#                             citations, is_kov, municipality}
# ---------------------------------------------------------------------------
# KOKS (the Local Government Organisation Act) is a major enabling statute, so
# the corpus has thousands of regulations issued under it -- enough to exercise
# the overflow cap, with real citations and riigiteataja URLs.
KOKS = "KOKS"
REG_FOR_LAW_FIELDS = {
    "reg_id",
    "title",
    "issuer",
    "status",
    "rt_url",
    "citations",
    "is_kov",
    "municipality",
}


def test_regulations_for_law_fields_and_citation() -> None:
    items = server.regulations_for_law(KOKS, limit=20)
    rows = _data_rows(items)
    assert rows, "regulations are issued under KOKS in the corpus"
    for it in rows:
        _assert_fields(it, REG_FOR_LAW_FIELDS)
        assert isinstance(it["citations"], list)
    # Real riigiteataja.ee citation on at least one regulation.
    assert any(it["rt_url"].startswith(RT_PREFIX) for it in rows)
    # At least one regulation states the statutory citation text it implements.
    assert any(it["citations"] for it in rows)


def test_regulations_for_law_limit_overflow_and_empty() -> None:
    items = server.regulations_for_law(KOKS, limit=5)
    rows = _data_rows(items)
    assert len(rows) == 5  # capped to the limit
    overflow = [it for it in items if it.get("overflow")]
    assert overflow, "KOKS has more than 5 regulations, so the cap must overflow"
    assert overflow[0]["total_available"] > 5
    # limit<=0 on a known law is empty success; unknown law is a note.
    assert server.regulations_for_law(KOKS, limit=0) == []
    assert server.regulations_for_law("no-such-law-xyz") == [
        {"note": "law not found: no-such-law-xyz"}
    ]


# ---------------------------------------------------------------------------
# 14. get_regulation -> {reg_id, title, issuer, status, rt_url,
#         num_provisions, is_kov, municipality, issued_under}
# ---------------------------------------------------------------------------
GET_REG_FIELDS = {
    "reg_id",
    "title",
    "issuer",
    "status",
    "rt_url",
    "num_provisions",
    "is_kov",
    "municipality",
    "issued_under",
}


def test_get_regulation_fields_and_parent_links() -> None:
    # Derive a real regulation id from the corpus (robust to a rebuild) rather
    # than hard-coding one; any regulation issued under KOKS must, by
    # construction, resolve a non-empty issued_under back to a statute.
    source = _data_rows(server.regulations_for_law(KOKS, limit=1))
    assert source, "need at least one regulation to look up"
    reg = server.get_regulation(source[0]["reg_id"])
    _assert_fields(reg, GET_REG_FIELDS)
    assert reg["rt_url"].startswith(RT_PREFIX)
    assert reg["issuer"]
    assert isinstance(reg["num_provisions"], int) and reg["num_provisions"] >= 0
    assert reg["issued_under"], "a KOKS regulation must link back to its statute"
    parent = reg["issued_under"][0]
    assert {"law", "slug", "rt_url"} <= set(parent)
    assert parent["rt_url"].startswith(RT_PREFIX)


def test_get_regulation_unknown_returns_note() -> None:
    out = server.get_regulation("definitely-not-a-real-regulation-xyz")
    assert "note" in out  # graceful note, never an exception


# ---------------------------------------------------------------------------
# 15. regulations_by_issuer -> {reg_id, title, issuer, status, rt_url}
# ---------------------------------------------------------------------------
VABARIIGI_VALITSUS = "Vabariigi Valitsus"
REG_BY_ISSUER_FIELDS = {"reg_id", "title", "issuer", "status", "rt_url"}


def test_regulations_by_issuer_fields_and_citation() -> None:
    items = server.regulations_by_issuer(VABARIIGI_VALITSUS, limit=10)
    rows = _data_rows(items)
    assert rows, "Vabariigi Valitsus issues regulations in the corpus"
    for it in rows:
        _assert_fields(it, REG_BY_ISSUER_FIELDS)
    # Real riigiteataja.ee citation strings.
    assert any(it["rt_url"].startswith(RT_PREFIX) for it in rows)


def test_regulations_by_issuer_limit_overflow_and_empty() -> None:
    items = server.regulations_by_issuer(VABARIIGI_VALITSUS, limit=5)
    rows = _data_rows(items)
    assert len(rows) == 5  # capped to the limit
    overflow = [it for it in items if it.get("overflow")]
    assert overflow, "Vabariigi Valitsus issues more than 5 regulations"
    assert overflow[0]["total_available"] > 5
    # limit<=0 and an unknown institution follow the list-tool empty contract.
    assert server.regulations_by_issuer(VABARIIGI_VALITSUS, limit=0) == []
    assert server.regulations_by_issuer("no-such-institution-xyz") == []


AMENDMENT_FIELDS = {"event_id", "label", "amendment_date", "entry_into_force", "amends"}


def test_define_term_and_laws_for_subject() -> None:
    terms = server.define_term("leping", limit=5)
    assert isinstance(terms, list)
    subjects = server.laws_for_subject("573", limit=5)
    assert isinstance(subjects, list)
    assert server.define_term("", limit=5) == []
    assert server.laws_for_subject("no-such-subject-xyz", limit=5) == []


def test_amendment_history_fields_and_empty() -> None:
    items = server.amendment_history(KARS, limit=10)
    assert items, "KarS has effected AmendmentEvents"
    for it in items:
        _assert_fields(it, AMENDMENT_FIELDS)
    assert server.amendment_history("no-such-law-xyz") == [
        {"note": "law not found: no-such-law-xyz"}
    ]
    assert server.amendment_history(KARS, limit=0) == []
