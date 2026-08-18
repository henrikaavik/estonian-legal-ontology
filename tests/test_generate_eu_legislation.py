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


def test_legislation_node_emits_dcterms_title() -> None:
    """#348: legislation nodes ship dcterms:title, not only rdfs:label."""
    item = {
        "celex": "32016R0679",
        "title": "Isikuandmete kaitse üldmäärus",
        "authors": [],
    }
    node = mod.legislation_to_node(item, "Regulation")
    assert node["dcterms:title"] == {
        "@value": "Isikuandmete kaitse üldmäärus",
        "@language": "et",
    }
    assert node["rdfs:label"]["@value"] == node["dcterms:title"]["@value"]


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


# ---------------------------------------------------------------------------
# #394 — non-Regulation CELEX returned by the regulations query must NOT be
# typed EUDocType_Regulation. Sector-4 type-X (UN-ECE) -> InternationalAgreement,
# sector-5 type-AP (EP positions) -> ParliamentPosition; unknown sectors drop.
# ---------------------------------------------------------------------------


def test_classify_sector3_regulation_is_regulation() -> None:
    """A genuine sector-3 type-R CELEX classifies as a Regulation."""
    assert mod.classify_eu_doc_type("32016R0679", "Regulation") == "Regulation"


def test_classify_sector4_unece_is_international_agreement() -> None:
    """A sector-4 type-X UN-ECE CELEX routes to InternationalAgreement (#394)."""
    assert (
        mod.classify_eu_doc_type("42024X0211", "Regulation")
        == "InternationalAgreement"
    )


def test_classify_sector5_ep_position_is_parliament_position() -> None:
    """A sector-5 type-AP EP-position CELEX routes to ParliamentPosition (#394)."""
    assert (
        mod.classify_eu_doc_type("52025AP0047", "Regulation")
        == "ParliamentPosition"
    )


def test_classify_directive_and_decision_pass_through_unchanged() -> None:
    """Only the regulations query is reclassified; directive/decision queries
    pass their nominal type through unchanged (#394)."""
    assert mod.classify_eu_doc_type("32011L0083", "Directive") == "Directive"
    assert mod.classify_eu_doc_type("32016D0001", "Decision") == "Decision"


def test_classify_unknown_nonsector3_celex_returns_none() -> None:
    """A non-sector-3-R CELEX from the regulations query with no override
    mapping returns None so the caller drops it rather than mistyping it (#394)."""
    assert mod.classify_eu_doc_type("62024A0001", "Regulation") is None
    assert mod.classify_eu_doc_type("not-a-celex", "Regulation") is None


def test_sector4_node_typed_international_agreement_not_regulation() -> None:
    """A sector-4 record emitted via the regulations query must carry
    EUDocType_InternationalAgreement, never EUDocType_Regulation (#394)."""
    item = {"celex": "42024X0211", "title": "UN-ECE rule", "authors": []}
    node = mod.legislation_to_node(item, "Regulation")
    assert node is not None
    assert node["estleg:euDocumentType"] == {
        "@id": "estleg:EUDocType_InternationalAgreement"
    }


def test_sector5_node_typed_parliament_position_not_regulation() -> None:
    """A sector-5 type-AP record must carry EUDocType_ParliamentPosition (#394)."""
    item = {"celex": "52025AP0047", "title": "EP position", "authors": []}
    node = mod.legislation_to_node(item, "Regulation")
    assert node is not None
    assert node["estleg:euDocumentType"] == {
        "@id": "estleg:EUDocType_ParliamentPosition"
    }


def test_sector3_regulation_node_keeps_regulation_type() -> None:
    """A genuine sector-3 regulation still gets EUDocType_Regulation (#394)."""
    item = {"celex": "32016R0679", "title": "GDPR", "authors": []}
    node = mod.legislation_to_node(item, "Regulation")
    assert node is not None
    assert node["estleg:euDocumentType"] == {"@id": "estleg:EUDocType_Regulation"}


def test_undroppable_celex_returns_none_node() -> None:
    """An unmapped non-sector-3-R CELEX yields a dropped (None) node (#394)."""
    item = {"celex": "62024A0001", "title": "x", "authors": []}
    assert mod.legislation_to_node(item, "Regulation") is None


def test_override_doc_type_individuals_declared_in_schema() -> None:
    """The InternationalAgreement / ParliamentPosition individuals referenced by
    routed nodes must be declared in the schema so the @id refs resolve (#394)."""
    ids = {n.get("@id") for n in mod.generate_schema_nodes()}
    assert "estleg:EUDocType_InternationalAgreement" in ids
    assert "estleg:EUDocType_ParliamentPosition" in ids


# ---------------------------------------------------------------------------
# #394 — documentDate / transpositionDeadline must be strptime-validated;
# malformed values are skipped (+warned), the rest of the node is preserved.
# ---------------------------------------------------------------------------


def test_valid_document_date_is_emitted() -> None:
    """A well-formed YYYY-MM-DD documentDate is emitted as xsd:date (#394)."""
    item = {
        "celex": "32016R0679",
        "title": "GDPR",
        "authors": [],
        "date": "2016-04-27",
    }
    node = mod.legislation_to_node(item, "Regulation")
    assert node["estleg:documentDate"] == {"@value": "2016-04-27", "@type": "xsd:date"}


def test_malformed_document_date_is_skipped_node_preserved() -> None:
    """A malformed documentDate is skipped, but the rest of the node survives (#394)."""
    item = {
        "celex": "32016R0679",
        "title": "GDPR",
        "authors": [],
        "date": "2016-13-99",  # month 13 / day 99 — invalid
    }
    node = mod.legislation_to_node(item, "Regulation")
    assert "estleg:documentDate" not in node
    # The rest of the node is still emitted.
    assert node["estleg:celexNumber"] == "32016R0679"


def test_partial_document_date_is_skipped() -> None:
    """A partial (year-only) documentDate is not a valid xsd:date and is skipped (#394)."""
    item = {"celex": "32016R0679", "title": "x", "authors": [], "date": "2016"}
    node = mod.legislation_to_node(item, "Regulation")
    assert "estleg:documentDate" not in node


def test_malformed_transposition_deadline_is_skipped() -> None:
    """A malformed directive transpositionDeadline is skipped (+warned) (#394)."""
    item = {
        "celex": "32011L0083",
        "title": "Consumer rights directive",
        "authors": [],
        "transposition_deadline": "2013-99-99",  # invalid
    }
    node = mod.legislation_to_node(item, "Directive")
    assert "estleg:transpositionDeadline" not in node


def test_valid_transposition_deadline_still_emitted() -> None:
    """A well-formed directive deadline is still emitted after validation (#394)."""
    item = {
        "celex": "32011L0083",
        "title": "Consumer rights directive",
        "authors": [],
        "transposition_deadline": "2013-12-13",
    }
    node = mod.legislation_to_node(item, "Directive")
    assert node["estleg:transpositionDeadline"] == {
        "@value": "2013-12-13",
        "@type": "xsd:date",
    }


def test_is_valid_iso_date_helper() -> None:
    """The shared date guard accepts strict YYYY-MM-DD and rejects everything else."""
    assert mod._is_valid_iso_date("2016-04-27") is True
    assert mod._is_valid_iso_date("2016-13-01") is False
    assert mod._is_valid_iso_date("2016/04/27") is False
    assert mod._is_valid_iso_date("2016") is False
    assert mod._is_valid_iso_date("") is False
    assert mod._is_valid_iso_date(None) is False
    assert mod._is_valid_iso_date(20160427) is False


# ---------------------------------------------------------------------------
# #348 — derivable provenance (owl:sameAs / dcterms:source / eli:id_local) is
# formulaic from the CELEX and must be emitted by the generator.
# ---------------------------------------------------------------------------


def test_derived_provenance_fields_present_and_correct() -> None:
    """owl:sameAs + dcterms:source are derived from the CELEX (#348). With no
    ``eli`` on the record, ``eli:id_local`` falls back to the CELEX (#607b)."""
    celex = "32016R0679"
    item = {"celex": celex, "title": "GDPR", "authors": []}
    node = mod.legislation_to_node(item, "Regulation")

    expected_cellar = f"http://publications.europa.eu/resource/celex/{celex}"
    # No ELI -> single CELEX owl:sameAs (not a list).
    assert node["owl:sameAs"] == {"@id": expected_cellar}
    assert node["dcterms:source"] == {"@id": expected_cellar}
    assert node["eli:id_local"] == celex


def test_derived_provenance_matches_routed_celex() -> None:
    """The CELEX-derived provenance is correct even for a routed (non-Regulation)
    record — it follows the CELEX, not the document type (#348/#394)."""
    celex = "42024X0211"
    item = {"celex": celex, "title": "UN-ECE rule", "authors": []}
    node = mod.legislation_to_node(item, "Regulation")

    expected_cellar = f"http://publications.europa.eu/resource/celex/{celex}"
    assert node["owl:sameAs"] == {"@id": expected_cellar}
    assert node["dcterms:source"] == {"@id": expected_cellar}
    assert node["eli:id_local"] == celex


def test_eli_id_local_is_natural_id_and_eli_sameas_emitted() -> None:
    """#607b + #610: with an ELI URI, ``eli:id_local`` is the ELI natural id
    (NOT the CELEX), and the ELI canonical URI is emitted as an ``owl:sameAs``
    object-link alongside the CELEX PURL."""
    celex = "32008R0015"
    eli = "http://data.europa.eu/eli/reg/2008/15/oj"
    item = {"celex": celex, "title": "Reg 15/2008", "authors": [], "eli": eli}
    node = mod.legislation_to_node(item, "Regulation")

    # eli:id_local is the natural id segment, not the CELEX.
    assert node["eli:id_local"] == "reg/2008/15"
    assert node["eli:id_local"] != celex
    # CELEX is preserved in its own property.
    assert node["estleg:celexNumber"] == celex
    # owl:sameAs is now a 2-item list: CELEX PURL + ELI URI.
    same_as_targets = {s["@id"] for s in node["owl:sameAs"]}
    assert same_as_targets == {
        f"http://publications.europa.eu/resource/celex/{celex}",
        eli,
    }
    # The ELI literal is still present for string consumers.
    assert node["estleg:eliIdentifier"] == {"@value": eli, "@type": "xsd:anyURI"}


def test_eli_natural_id_helper() -> None:
    """``eli_natural_id`` strips the ELI host + ``/oj`` suffix, and returns None
    for non-ELI / empty inputs (so callers fall back to CELEX)."""
    assert mod.eli_natural_id("http://data.europa.eu/eli/reg/2008/15/oj") == "reg/2008/15"
    assert (
        mod.eli_natural_id("http://data.europa.eu/eli/reg_impl/2013/1247/oj")
        == "reg_impl/2013/1247"
    )
    # Parenthesised decision id is preserved.
    assert (
        mod.eli_natural_id("http://data.europa.eu/eli/dec/2013/168(1)/oj")
        == "dec/2013/168(1)"
    )
    assert mod.eli_natural_id("") is None
    assert mod.eli_natural_id("http://example.com/not-eli") is None


# ---------------------------------------------------------------------------
# #398 — EURLEX_INDEX.json by_type/in_force/total counts must be derived from
# the EMITTED nodes' effective euDocumentType + inForce, NOT from raw
# fetched-row tallies. A dropped (None) row must not be counted; a retyped
# row must count under its effective type, never the query's nominal type.
# ---------------------------------------------------------------------------


def _emit(raw_rows: list[dict]) -> list[dict]:
    """Emit JSON-LD nodes from raw regulations-query rows exactly as ``main``
    does: call ``legislation_to_node`` with the nominal ``Regulation`` query
    type and drop ``None`` results (#394/#398). No network."""
    return [
        node
        for row in raw_rows
        if (node := mod.legislation_to_node(row, "Regulation")) is not None
    ]


def test_count_emitted_by_doc_type_uses_effective_type_not_query_type() -> None:
    """Build the index tally from a small list of EMITTED nodes that includes
    (a) a DROPPED row (legislation_to_node -> None, never appended) and
    (b) a RETYPED InternationalAgreement row routed through the regulations
    query. Counts must follow the emitted effective types: the dropped row is
    absent, and the sector-4 record counts under InternationalAgreement — NOT
    under Regulation (#398).
    """
    # Raw rows as they would arrive from the cdm:regulation query, all bearing
    # the nominal "Regulation" query type. in_force payloads are mixed.
    raw_regulation_rows = [
        # Genuine sector-3-R regulation, in force.
        {"celex": "32016R0679", "title": "GDPR", "authors": [], "in_force": "true"},
        # Genuine sector-3-R regulation, repealed (not in force).
        {"celex": "31999R0001", "title": "Old reg", "authors": [], "in_force": "false"},
        # Sector-4 UN-ECE record -> RETYPED to InternationalAgreement, in force.
        {"celex": "42024X0211", "title": "UN-ECE rule", "authors": [], "in_force": "true"},
        # Unmapped non-sector-3-R CELEX -> DROPPED (node is None).
        {"celex": "62024A0001", "title": "to be dropped", "authors": []},
    ]

    emitted = _emit(raw_regulation_rows)
    # The droppable row really produced no node (4 raw rows -> 3 emitted nodes).
    assert len(emitted) == 3

    counts = mod.count_emitted_by_doc_type(emitted)

    # Two genuine sector-3-R regulations: one in force, one not. The dropped
    # row and the retyped row do NOT inflate this bucket.
    assert counts["Regulation"] == {"total": 2, "in_force": 1, "not_in_force": 1}


def test_count_emitted_by_doc_type_retyped_counts_under_effective_type() -> None:
    """A retyped sector-4 record counts under InternationalAgreement (its
    effective emitted type), and never inflates the Regulation bucket; the
    dropped row contributes nothing, so sum(totals) == emitted node count (#398)."""
    raw_regulation_rows = [
        {"celex": "32016R0679", "title": "GDPR", "authors": [], "in_force": "true"},
        {"celex": "31999R0001", "title": "Old reg", "authors": [], "in_force": "false"},
        {"celex": "42024X0211", "title": "UN-ECE rule", "authors": [], "in_force": "true"},
        {"celex": "62024A0001", "title": "dropped", "authors": []},  # -> None
    ]
    counts = mod.count_emitted_by_doc_type(_emit(raw_regulation_rows))

    # The retyped record lands under InternationalAgreement, in force.
    assert counts["InternationalAgreement"] == {
        "total": 1,
        "in_force": 1,
        "not_in_force": 0,
    }
    # It is NOT folded into Regulation.
    assert counts["Regulation"]["total"] == 2
    # The dropped row contributed nothing: 3 emitted nodes total.
    assert sum(b["total"] for b in counts.values()) == 3


def test_count_emitted_skips_non_legislation_nodes() -> None:
    """Nodes without an estleg:euDocumentType (e.g. the owl:Ontology header
    node prepended to every graph) are not counted (#398)."""
    header = {
        "@id": "estleg:EURlex_Combined_Map_2026",
        "@type": ["owl:Ontology"],
    }
    reg = mod.legislation_to_node(
        {"celex": "32016R0679", "title": "GDPR", "authors": [], "in_force": "true"},
        "Regulation",
    )
    counts = mod.count_emitted_by_doc_type([header, reg])
    assert set(counts) == {"Regulation"}
    assert counts["Regulation"]["total"] == 1


def test_count_emitted_absent_inforce_counts_as_not_in_force() -> None:
    """A node with no estleg:inForce property (upstream in_force was falsy/absent)
    counts as not-in-force, mirroring the raw is_in_force_value semantics (#398)."""
    # No in_force key -> legislation_to_node emits no estleg:inForce property.
    node = mod.legislation_to_node(
        {"celex": "32016R0679", "title": "GDPR", "authors": []}, "Regulation"
    )
    assert "estleg:inForce" not in node
    counts = mod.count_emitted_by_doc_type([node])
    assert counts["Regulation"] == {"total": 1, "in_force": 0, "not_in_force": 1}


# ---------------------------------------------------------------------------
# #606 — fetch_legislation_type dedup/merge must be O(1) per duplicate via a
# CELEX->item dict, while preserving the exact prior merge semantics and
# (insertion) order so the returned list is byte-identical.
# ---------------------------------------------------------------------------


def _celex_binding(celex: str, *, author: str = "", deadline: str = "") -> dict:
    """Build one SPARQL result binding row exactly as the fetch loop reads it
    (``b.get(var, {}).get("value", "")``). ``author`` is a full corporate-body
    URI; the loop derives its code from the last path segment."""
    row: dict[str, dict] = {"celex": {"value": celex}}
    if author:
        row["author"] = {"value": author}
    if deadline:
        row["deadline"] = {"value": deadline}
    return row


def test_fetch_dedup_merges_authors_and_keeps_earliest_deadline(monkeypatch) -> None:
    """Drive the dedup/merge path (#606): a duplicate CELEX carrying (i) a second
    distinct author and (ii) an earlier transposition deadline must collapse into
    a single item with BOTH authors (order preserved, no dup) and the EARLIEST
    deadline, while overall item order follows first-seen (insertion) order."""
    auth = "http://publications.europa.eu/resource/authority/corporate-body/"
    bindings = [
        # First-seen directive: author EP, deadline 2013-12-13.
        _celex_binding("32011L0083", author=f"{auth}EP", deadline="2013-12-13"),
        # A second, distinct CELEX in between — proves order is preserved.
        _celex_binding("32016R0679", author=f"{auth}COM"),
        # Duplicate of the directive: NEW author COM + EARLIER deadline.
        _celex_binding("32011L0083", author=f"{auth}COM", deadline="2013-06-01"),
        # Duplicate again: author EP already present (no dup) + LATER deadline
        # (must NOT replace the earlier one).
        _celex_binding("32011L0083", author=f"{auth}EP", deadline="2015-01-01"),
    ]

    # Single page (len < PAGE_SIZE) so the paginating loop calls the seam once
    # and exits without sleeping. Returning [] on any further call is defensive.
    pages = [bindings]
    monkeypatch.setattr(
        mod, "sparql_query_with_retry", lambda _query: pages.pop(0) if pages else []
    )

    items, partial = mod.fetch_legislation_type("cdm:directive")

    assert partial is False
    # Two distinct CELEX -> two items, in first-seen (insertion) order.
    assert [it["celex"] for it in items] == ["32011L0083", "32016R0679"]
    # Both authors merged, original order preserved, EP not duplicated.
    assert items[0]["authors"] == ["EP", "COM"]
    # Earliest deadline kept; the later 2015 value did not overwrite it.
    assert items[0]["transposition_deadline"] == "2013-06-01"
    # The unrelated second item is untouched.
    assert items[1]["authors"] == ["COM"]
