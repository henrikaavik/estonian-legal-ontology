"""Tests for the act-level Expression minting (#608, FRBR phase 1).

Covers the factored pure helpers (act-fragment extraction, date-digit
formatting, Expression-IRI / label construction), the per-sidecar build
function over synthetic sidecar + peep files, the skip-when-no-act path, and
output determinism (sorted ``@graph``, byte-stable re-runs).
"""

import json
from pathlib import Path

from estleg import generate_act_expressions_608 as G


# ---------------------------------------------------------------------------
# Synthetic-document builders
# ---------------------------------------------------------------------------
def _pv(vid: str, valid_from: str, prov: str = "estleg:VKVS_Par_1") -> dict:
    """A minimal estleg:ProvisionVersion node."""
    return {
        "@id": vid,
        "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
        "estleg:versionOf": {"@id": prov},
        "estleg:versionValidFrom": {"@value": valid_from, "@type": "xsd:date"},
    }


def _version_doc(*pv_nodes: dict) -> dict:
    return {"@context": {"estleg": G.NS}, "@graph": list(pv_nodes)}


def _peep_doc(act_iri: str | None, label=None) -> dict:
    graph: list[dict] = []
    if act_iri is not None:
        node = {"@id": act_iri, "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]}
        if label is not None:
            node["rdfs:label"] = label
        graph.append(node)
    return {"@context": {"estleg": G.NS}, "@graph": graph}


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_act_local_fragment_compact():
    assert G.act_local_fragment("estleg:VKVS_Map_2026") == "VKVS_Map_2026"


def test_act_local_fragment_expanded():
    iri = "https://w3id.org/estleg/VKVS_Map_2026"
    assert G.act_local_fragment(iri) == "VKVS_Map_2026"


def test_act_local_fragment_expanded_slash():
    # The w3id slash namespace has no '#'; the local name is the final segment.
    iri = "https://w3id.org/estleg/VKVS_Map_2026"
    assert G.act_local_fragment(iri) == "VKVS_Map_2026"


def test_date_digits():
    assert G.date_digits("2005-03-09") == "20050309"
    assert G.date_digits("2002-06-01") == "20020601"


def test_expression_iri_canonical_example():
    # The headline example from the spec.
    assert (
        G.expression_iri("estleg:VKVS_Map_2026", "2005-03-09")
        == "estleg:VKVS_Map_2026_Expr_20050309"
    )


def test_expression_iri_from_expanded_act_iri():
    iri = "https://w3id.org/estleg/VKVS_Map_2026"
    assert G.expression_iri(iri, "2005-03-09") == "estleg:VKVS_Map_2026_Expr_20050309"


def test_expression_label_with_act_label():
    assert (
        G.expression_label("Väetiseseadus", "vaetiseseadus", "2005-03-09")
        == "Väetiseseadus (seisuga 2005-03-09)"
    )


def test_expression_label_falls_back_to_slug():
    assert (
        G.expression_label(None, "vaetiseseadus", "2005-03-09")
        == "vaetiseseadus (seisuga 2005-03-09)"
    )
    # Empty string is also treated as "no label".
    assert (
        G.expression_label("", "vaetiseseadus", "2005-03-09")
        == "vaetiseseadus (seisuga 2005-03-09)"
    )


def test_build_expression_node_shape():
    node = G.build_expression_node(
        "estleg:VKVS_Map_2026", "Väetiseseadus", "vaetiseseadus", "2005-03-09"
    )
    assert node == {
        "@id": "estleg:VKVS_Map_2026_Expr_20050309",
        "@type": ["owl:NamedIndividual", "estleg:ActExpression"],
        "estleg:expressionOf": {"@id": "estleg:VKVS_Map_2026"},
        "estleg:consolidationDate": {"@value": "2005-03-09", "@type": "xsd:date"},
        "rdfs:label": "Väetiseseadus (seisuga 2005-03-09)",
    }


def test_distinct_consolidation_dates_dedups_and_sorts():
    doc = _version_doc(
        _pv("estleg:VKVS_Par_1_v1", "2005-03-09"),
        _pv("estleg:VKVS_Par_2_v1", "2005-03-09"),   # duplicate date
        _pv("estleg:VKVS_Par_1_v2", "2002-06-01"),
    )
    assert G.distinct_consolidation_dates(doc) == ["2002-06-01", "2005-03-09"]


def test_distinct_consolidation_dates_ignores_non_provisionversion_nodes():
    doc = _version_doc(_pv("estleg:VKVS_Par_1_v1", "2005-03-09"))
    doc["@graph"].append({"@id": "estleg:VKVS_Map", "@type": ["owl:Ontology"]})
    doc["@graph"].append(
        {"@id": "x", "@type": ["estleg:ProvisionVersion"]}  # no versionValidFrom
    )
    assert G.distinct_consolidation_dates(doc) == ["2005-03-09"]


def test_find_act_root_returns_iri_and_label():
    doc = _peep_doc("estleg:VKVS_Map_2026", label="Väetiseseadus teemakaardistus")
    assert G.find_act_root(doc) == ("estleg:VKVS_Map_2026", "Väetiseseadus teemakaardistus")


def test_find_act_root_handles_language_tagged_label():
    doc = _peep_doc(
        "estleg:VKVS_Map_2026", label={"@value": "Väetiseseadus", "@language": "et"}
    )
    assert G.find_act_root(doc) == ("estleg:VKVS_Map_2026", "Väetiseseadus")


def test_find_act_root_none_when_no_act_node():
    assert G.find_act_root(_peep_doc(None)) == (None, None)


# ---------------------------------------------------------------------------
# Per-sidecar build function (file I/O, under tmp_path)
# ---------------------------------------------------------------------------
def test_build_for_sidecar_produces_expected_dicts(tmp_path):
    slug = "vaetiseseadus"
    sidecar = tmp_path / "provision_versions" / f"{slug}.jsonld"
    peep = tmp_path / f"{slug}_peep.json"
    _write(
        sidecar,
        _version_doc(
            _pv("estleg:VKVS_Par_1_v1", "2005-03-09"),
            _pv("estleg:VKVS_Par_2_v1", "2005-03-09"),   # duplicate date collapses
            _pv("estleg:VKVS_Par_1_v2", "2002-06-01"),
        ),
    )
    _write(peep, _peep_doc("estleg:VKVS_Map_2026", label="Väetiseseadus"))

    nodes, reason = G.build_for_sidecar(sidecar, peep)

    assert reason is None
    assert nodes == [
        {
            "@id": "estleg:VKVS_Map_2026_Expr_20020601",
            "@type": ["owl:NamedIndividual", "estleg:ActExpression"],
            "estleg:expressionOf": {"@id": "estleg:VKVS_Map_2026"},
            "estleg:consolidationDate": {"@value": "2002-06-01", "@type": "xsd:date"},
            "rdfs:label": "Väetiseseadus (seisuga 2002-06-01)",
            # #608 phase 2: the older expression is superseded by the next.
            "estleg:supersededByExpression": {"@id": "estleg:VKVS_Map_2026_Expr_20050309"},
        },
        {
            "@id": "estleg:VKVS_Map_2026_Expr_20050309",
            "@type": ["owl:NamedIndividual", "estleg:ActExpression"],
            "estleg:expressionOf": {"@id": "estleg:VKVS_Map_2026"},
            "estleg:consolidationDate": {"@value": "2005-03-09", "@type": "xsd:date"},
            "rdfs:label": "Väetiseseadus (seisuga 2005-03-09)",
            "estleg:supersedesExpression": {"@id": "estleg:VKVS_Map_2026_Expr_20020601"},
        },
    ]


def test_expression_chain_links_consecutive_consolidations():
    # 3 dates -> 2 forward + 2 backward links; head has no supersededBy, tail no
    # supersedes. dates passed sorted ascending.
    nodes = G.build_act_expression_nodes(
        "estleg:X_Map_2026", "X", "x", ["2001-01-01", "2002-01-01", "2003-01-01"]
    )
    ids = [n["@id"] for n in nodes]
    assert "estleg:supersededByExpression" not in nodes[-1]  # current consolidation
    assert "estleg:supersedesExpression" not in nodes[0]     # oldest
    assert nodes[0]["estleg:supersededByExpression"] == {"@id": ids[1]}
    assert nodes[1]["estleg:supersededByExpression"] == {"@id": ids[2]}
    assert nodes[2]["estleg:supersedesExpression"] == {"@id": ids[1]}
    assert nodes[1]["estleg:supersedesExpression"] == {"@id": ids[0]}


def test_single_consolidation_has_no_chain():
    nodes = G.build_act_expression_nodes("estleg:X_Map_2026", "X", "x", ["2001-01-01"])
    assert len(nodes) == 1
    assert "estleg:supersededByExpression" not in nodes[0]
    assert "estleg:supersedesExpression" not in nodes[0]


def test_build_for_sidecar_label_falls_back_to_slug(tmp_path):
    slug = "vaetiseseadus"
    sidecar = tmp_path / "provision_versions" / f"{slug}.jsonld"
    peep = tmp_path / f"{slug}_peep.json"
    _write(sidecar, _version_doc(_pv("estleg:VKVS_Par_1_v1", "2005-03-09")))
    _write(peep, _peep_doc("estleg:VKVS_Map_2026"))  # no rdfs:label

    nodes, reason = G.build_for_sidecar(sidecar, peep)

    assert reason is None
    assert nodes[0]["rdfs:label"] == "vaetiseseadus (seisuga 2005-03-09)"


def test_build_for_sidecar_skip_when_no_act_node(tmp_path):
    slug = "noact"
    sidecar = tmp_path / "provision_versions" / f"{slug}.jsonld"
    peep = tmp_path / f"{slug}_peep.json"
    _write(sidecar, _version_doc(_pv("estleg:X_Par_1_v1", "2005-03-09")))
    _write(peep, _peep_doc(None))  # peep present but carries no estleg:Act node

    nodes, reason = G.build_for_sidecar(sidecar, peep)
    assert nodes == []
    assert reason == G.SKIP_NO_ACT


def test_build_for_sidecar_skip_when_peep_missing(tmp_path):
    slug = "nopeep"
    sidecar = tmp_path / "provision_versions" / f"{slug}.jsonld"
    _write(sidecar, _version_doc(_pv("estleg:X_Par_1_v1", "2005-03-09")))
    peep = tmp_path / f"{slug}_peep.json"  # never written

    nodes, reason = G.build_for_sidecar(sidecar, peep)
    assert nodes == []
    assert reason == G.SKIP_NO_ACT


def test_build_for_sidecar_skip_when_sidecar_is_lfs_pointer(tmp_path):
    slug = "lfsy"
    sidecar = tmp_path / "provision_versions" / f"{slug}.jsonld"
    peep = tmp_path / f"{slug}_peep.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:deadbeef\nsize 123\n",
        encoding="utf-8",
    )
    _write(peep, _peep_doc("estleg:VKVS_Map_2026", label="Väetiseseadus"))

    nodes, reason = G.build_for_sidecar(sidecar, peep)
    assert nodes == []
    assert reason == G.SKIP_LFS


# ---------------------------------------------------------------------------
# Output assembly + determinism
# ---------------------------------------------------------------------------
def test_build_output_document_sorts_graph_by_id():
    unsorted = [
        G.build_expression_node("estleg:B_Map", "B", "b", "2005-03-09"),
        G.build_expression_node("estleg:A_Map", "A", "a", "2001-01-01"),
        G.build_expression_node("estleg:A_Map", "A", "a", "2000-01-01"),
    ]
    doc = G.build_output_document(unsorted)
    ids = [node["@id"] for node in doc["@graph"]]
    assert ids[0] == f"{G.ONTOLOGY_IRI}/dataset/act-expressions"
    assert ids[1:] == [
        "estleg:A_Map_Expr_20000101",
        "estleg:A_Map_Expr_20010101",
        "estleg:B_Map_Expr_20050309",
    ]


def test_output_context_includes_dataset_prefixes():
    doc = G.build_output_document([])
    assert {"estleg", "owl", "rdfs", "xsd", "void", "dcat", "dcterms"} <= set(
        doc["@context"]
    )
    assert doc["@context"]["estleg"] == G.NS


def test_build_output_document_is_byte_stable():
    nodes = [
        G.build_expression_node("estleg:A_Map", "A", "a", "2001-01-01"),
        G.build_expression_node("estleg:B_Map", "B", "b", "2005-03-09"),
    ]
    first = json.dumps(G.build_output_document(list(nodes)), ensure_ascii=False, indent=2)
    second = json.dumps(
        G.build_output_document(list(reversed(nodes))), ensure_ascii=False, indent=2
    )
    assert first == second


# ---------------------------------------------------------------------------
# Corpus-walk collector (monkeypatched KRR_DIR, no writes)
# ---------------------------------------------------------------------------
def test_collect_all_expressions_over_tmp_corpus(tmp_path, monkeypatch):
    krr = tmp_path / "krr_outputs"
    monkeypatch.setattr(G, "KRR_DIR", krr)

    # Act A: two distinct dates.
    _write(
        krr / "provision_versions" / "act_a.jsonld",
        _version_doc(
            _pv("estleg:A_Par_1_v1", "2005-03-09"),
            _pv("estleg:A_Par_1_v2", "2002-06-01"),
        ),
    )
    _write(krr / "act_a_peep.json", _peep_doc("estleg:A_Map_2026", label="Act A"))

    # Act B: one date.
    _write(
        krr / "provision_versions" / "act_b.jsonld",
        _version_doc(_pv("estleg:B_Par_1_v1", "2010-01-01")),
    )
    _write(krr / "act_b_peep.json", _peep_doc("estleg:B_Map_2026", label="Act B"))

    # Act C: sidecar present but peep missing -> skipped (no act root).
    _write(
        krr / "provision_versions" / "act_c.jsonld",
        _version_doc(_pv("estleg:C_Par_1_v1", "2011-01-01")),
    )

    nodes, stats = G.collect_all_expressions()

    assert stats["sidecars_total"] == 3
    assert stats["sidecars_scanned"] == 2
    assert stats["sidecars_skipped_no_act"] == 1
    assert stats["sidecars_skipped_lfs"] == 0
    assert stats["distinct_acts"] == 2
    assert stats["total_expressions"] == 3  # A:2 + B:1

    ids = sorted(node["@id"] for node in nodes)
    assert ids == [
        "estleg:A_Map_2026_Expr_20020601",
        "estleg:A_Map_2026_Expr_20050309",
        "estleg:B_Map_2026_Expr_20100101",
    ]


def test_collect_all_expressions_is_deterministic(tmp_path, monkeypatch):
    krr = tmp_path / "krr_outputs"
    monkeypatch.setattr(G, "KRR_DIR", krr)
    _write(
        krr / "provision_versions" / "act_a.jsonld",
        _version_doc(
            _pv("estleg:A_Par_1_v1", "2005-03-09"),
            _pv("estleg:A_Par_2_v1", "2002-06-01"),
        ),
    )
    _write(krr / "act_a_peep.json", _peep_doc("estleg:A_Map_2026", label="Act A"))

    nodes1, stats1 = G.collect_all_expressions()
    nodes2, stats2 = G.collect_all_expressions()
    doc1 = json.dumps(G.build_output_document(nodes1), ensure_ascii=False, indent=2)
    doc2 = json.dumps(G.build_output_document(nodes2), ensure_ascii=False, indent=2)
    assert doc1 == doc2
    assert stats1 == stats2
