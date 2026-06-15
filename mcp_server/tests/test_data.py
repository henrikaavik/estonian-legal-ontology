"""Smoke tests for the estleg_mcp data layer against the REAL corpus.

These tests assert the data-access layer works end-to-end on the committed
``krr_outputs/`` corpus: law resolution (by slug, title, abbreviation, and
accent-folded title), provision reading, riigiteataja.ee citation derivation,
and a transposition lookup. They are intentionally lenient about exact counts
(the corpus is regenerated) but strict about shapes and citation correctness.

If the corpus is not present (e.g. a clone without ``krr_outputs/``), every
test is skipped via the module-level guard rather than failing.
"""

from __future__ import annotations

import pytest

from estleg_mcp import data

# Skip the whole module if the corpus cannot be located.
try:
    data.corpus_root()
    _CORPUS_AVAILABLE = True
except FileNotFoundError:
    _CORPUS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _CORPUS_AVAILABLE,
    reason="Estonian Legal Ontology corpus (krr_outputs/INDEX.json) not found",
)


# ---------------------------------------------------------------------------
# Law resolution
# ---------------------------------------------------------------------------
def test_resolve_by_slug() -> None:
    rec = data.resolve_law("karistusseadustik")
    assert rec is not None
    assert rec.name == "karistusseadustik"
    assert rec.files  # at least one peep file


def test_resolve_by_human_abbreviation() -> None:
    # The conventional human abbreviation must resolve, even though the corpus
    # registry stores a migrated IRI-prefix abbreviation (KARIST_2) instead.
    rec = data.resolve_law("KarS")
    assert rec is not None
    assert rec.name == "karistusseadustik"
    # Põhiseadus has no registry abbreviation at all; the human "PS" must work.
    ps = data.resolve_law("PS")
    assert ps is not None
    assert ps.name == "eesti_vabariigi_pohiseadus"


def test_resolve_by_registry_abbreviation() -> None:
    # The migrated IRI-prefix abbreviation from the registry also resolves.
    rec = data.resolve_law("KARIST_2")
    assert rec is not None
    assert rec.name == "karistusseadustik"


def test_resolve_by_title_exact() -> None:
    rec = data.resolve_law("Karistusseadustik")
    assert rec is not None
    assert rec.name == "karistusseadustik"


def test_resolve_by_title_accent_folded() -> None:
    # "Võlaõigusseadus" typed without diacritics must still resolve by title.
    rec = data.resolve_law("Volaoigusseadus")
    assert rec is not None
    assert rec.name == "volaoigusseadus"


def test_resolve_unknown_returns_none() -> None:
    assert data.resolve_law("definitely-not-a-real-law-xyz") is None
    assert data.resolve_law("") is None


def test_search_law_records_substring() -> None:
    hits = data.search_law_records("karistus", limit=10)
    assert hits
    slugs = {r.name for r in hits}
    assert "karistusseadustik" in slugs


def test_search_finds_human_abbreviations() -> None:
    # search_laws must honour the same conventional abbreviations resolve_law
    # supports (regression: these used to return [] / unrelated hits because
    # the registry stores only the migrated IRI-prefix form).
    def slugs(query: str) -> list[str]:
        return [r.name for r in data.search_law_records(query)]

    assert "volaoigusseadus" in slugs("VÕS")
    assert "toolepingu_seadus" in slugs("TLS")
    # The conventional abbreviation's law must rank first.
    assert slugs("KarS")[0] == "karistusseadustik"
    assert slugs("PS")[0] == "eesti_vabariigi_pohiseadus"


def test_amendment_link_resolves_to_draft() -> None:
    # Laws whose only draft link is estleg:hasProposedAmendment (no
    # affectedBy fallback) must still resolve through the amendments sidecar.
    rec = data.resolve_law("keskkonnaseadustiku_uldosa_seadus")
    assert rec is not None
    links = data.amendment_link_drafts(rec)
    assert links  # at least one ProposedAmendment link
    assert (
        links.get("estleg:AmendmentLink_Draft_KLIM13_0996_KESKKO")
        == "estleg:Draft_KLIM13_0996"
    )
    # The resolved draft must be a real node in the eelnoud subcorpus.
    assert data.draft_info("estleg:Draft_KLIM13_0996") is not None


def test_display_abbrev_prefers_human_form() -> None:
    # The registry stores "KARIST_2"; display must show the conventional "KarS".
    kars = data.resolve_law("karistusseadustik")
    assert kars is not None
    assert data.display_abbrev(kars) == "KarS"
    # VÕS is absent from the registry entirely; the curated map still supplies it.
    vos = data.resolve_law("volaoigusseadus")
    assert vos is not None
    assert data.display_abbrev(vos) == "VÕS"


def test_act_kehtiv_date_is_iso_date() -> None:
    rec = data.resolve_law("KarS")
    assert rec is not None
    date = data.act_kehtiv_date(data.act_node(data.load_law_graph(rec)))
    # Consolidated-text date like "2026-05-24" (or "" if a law lacks it).
    assert date == "" or date[:4].isdigit()
    assert data.act_kehtiv_date(None) == ""


# ---------------------------------------------------------------------------
# Graph access + provision reading
# ---------------------------------------------------------------------------
def test_load_graph_and_act_node() -> None:
    rec = data.resolve_law("KarS")
    assert rec is not None
    graph = data.load_law_graph(rec)
    assert graph
    act = data.act_node(graph)
    assert act is not None
    types = data._types_of(act)
    assert "estleg:Act" in types or "estleg:Law" in types


def test_provision_nodes_nonempty() -> None:
    rec = data.resolve_law("KarS")
    assert rec is not None
    provs = data.provision_nodes(data.load_law_graph(rec))
    assert len(provs) > 10
    # Every provision must carry the per-law subclass prefix.
    for node in provs[:5]:
        assert any(t.startswith("estleg:LegalProvision_") for t in data._types_of(node))


def test_find_provision_by_paragraph() -> None:
    rec = data.resolve_law("KarS")
    assert rec is not None
    graph = data.load_law_graph(rec)
    node = data.find_provision(graph, "§ 13")
    assert node is not None
    assert "13" in data._text(node.get("estleg:paragrahv"))
    # Bare-number and dotted forms resolve to the same node.
    assert data.find_provision(graph, "13") is not None
    assert data.find_provision(graph, "§13.") is not None


def test_find_provision_missing_returns_none() -> None:
    rec = data.resolve_law("KarS")
    assert rec is not None
    graph = data.load_law_graph(rec)
    assert data.find_provision(graph, "99999") is None


def test_normalise_paragraph_superscript_forms_agree() -> None:
    # Corpus stores "§ 133<sup>1</sup>."; users may type these variants.
    stored = data._normalise_paragraph("§ 133<sup>1</sup>.")
    assert stored == "133_1"
    assert data._normalise_paragraph("§ 133¹") == "133_1"
    assert data._normalise_paragraph("133-1") == "133_1"
    assert data._normalise_paragraph("133_1") == "133_1"
    # Plain section number stays clean.
    assert data._normalise_paragraph("§ 13.") == "13"


def test_find_ordinal_provision_in_kars() -> None:
    # KarS special part has ordinal sections like § 133¹; one must be findable
    # via the superscripted query even though it is stored as HTML.
    rec = data.resolve_law("KarS")
    assert rec is not None
    graph = data.load_law_graph(rec)
    node = data.find_provision(graph, "§ 133¹")
    assert node is not None
    par = data._text(node.get("estleg:paragrahv"))
    assert "133" in par


def test_clean_display_renders_superscripts() -> None:
    assert data.clean_display("§ 133<sup>1</sup>.") == "§ 133¹."
    assert data.clean_display("§ 13.") == "§ 13."


# ---------------------------------------------------------------------------
# Internal IRI -> law slug (longest-prefix match on the registry abbrev)
# ---------------------------------------------------------------------------
def test_law_slug_from_iri_handles_underscored_abbrev() -> None:
    # KARIST_2 contains an underscore; a naive split on "_" would mis-segment.
    slug = data._law_slug_from_iri("estleg:KARIST_2_Osa1_Par_13")
    assert slug == "karistusseadustik"


def test_law_slug_from_iri_unknown_returns_none() -> None:
    assert data._law_slug_from_iri("estleg:ZZZNOPE_Par_1") is None


# ---------------------------------------------------------------------------
# Citation derivation
# ---------------------------------------------------------------------------
def test_rt_url_strips_xml_and_is_riigiteataja() -> None:
    rec = data.resolve_law("KarS")
    assert rec is not None
    url = data.rt_url(data.act_node(data.load_law_graph(rec)))
    assert url.startswith("https://www.riigiteataja.ee/akt/")
    assert not url.endswith(".xml")


def test_rt_url_handles_missing_node() -> None:
    assert data.rt_url(None) == ""
    assert data.rt_url({}) == ""


def test_eurlex_url() -> None:
    url = data.eurlex_url("31990L0314")
    assert url == (
        "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:31990L0314"
    )
    assert data.eurlex_url("") == ""


# ---------------------------------------------------------------------------
# Transposition
# ---------------------------------------------------------------------------
def test_transposition_by_celex() -> None:
    rows = data.transposition_matches("31990L0314")
    assert rows
    assert any(
        data._text(r.get("directive_celex")) == "31990L0314" for r in rows
    )


def test_transposition_by_law_name() -> None:
    rows = data.transposition_matches("Võlaõigusseadus")
    # At least one directive should be transposed by VÕS.
    assert rows
    for r in rows:
        assert data._text(r.get("national_title"))


def test_transposition_empty_query() -> None:
    assert data.transposition_matches("") == []
