"""#709: no rdfs:domain / rdfs:range axiom may type a node into a shaped class it never claimed."""

from __future__ import annotations

import json

import pytest

from estleg import check_phantom_typing as cpt
from estleg.consolidate_tbox import (
    DOMAIN_INCLUDES,
    DOMAIN_RANGE,
    FORBIDDEN_NO_RANGE,
    OVERWRITE_COMMENT,
    OVERWRITE_DOMAIN,
    OVERWRITE_RANGE,
    RANGE_INCLUDES,
    build_consolidated_graph,
)
from estleg.shacl_validate_all import BUCKETS

SHAPED = frozenset({"estleg:Act", "estleg:LegalProvision", "estleg:ProvisionVersion"})
OPEN_RANGES = {"rdfs:Resource", ""}


def _prop(prop: str, *, domain: str | None = None, range_: str | None = None) -> dict:
    node: dict = {"@id": prop, "@type": ["owl:ObjectProperty"]}
    if domain:
        node["rdfs:domain"] = {"@id": domain}
    if range_:
        node["rdfs:range"] = {"@id": range_}
    return node


def _scan(vocab: list[dict], *docs: dict) -> list[cpt.PhantomTyping]:
    return cpt.scan_documents("test", docs, cpt.TBox(vocab, SHAPED))


# --- the checker -------------------------------------------------------------


def test_range_types_a_reference_the_bucket_never_declares():
    vocab = [_prop("estleg:interpretsVersion", range_="estleg:ProvisionVersion")]
    decision = {
        "@id": "estleg:Decision_1",
        "@type": "estleg:CourtDecision",
        "estleg:interpretsVersion": [
            {"@id": "estleg:AOS_Par_1_v1"},
            {"@id": "estleg:AOS_Par_2_v1"},
        ],
    }
    (finding,) = _scan(vocab, {"@graph": [decision]})
    assert (finding.axis, finding.prop, finding.cls) == (
        "range",
        "estleg:interpretsVersion",
        "estleg:ProvisionVersion",
    )
    assert finding.nodes == ("estleg:AOS_Par_1_v1", "estleg:AOS_Par_2_v1")


def test_domain_types_a_subject_of_a_sibling_class():
    vocab = [_prop("estleg:enactedBy", domain="estleg:Act")]
    provision = {
        "@id": "estleg:Reg_1_Par_1",
        "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
        "estleg:enactedBy": {"@id": "estleg:Issuer_x"},
    }
    (finding,) = _scan(vocab, {"@graph": [provision]})
    assert (finding.axis, finding.nodes) == ("domain", ("estleg:Reg_1_Par_1",))


def test_a_declaration_anywhere_in_the_bucket_clears_the_reference():
    vocab = [_prop("estleg:versionOf", range_="estleg:LegalProvision")]
    version = {"@id": "estleg:X_Par_1_v1", "estleg:versionOf": {"@id": "estleg:X_Par_1"}}
    declared = {"@id": "estleg:X_Par_1", "@type": "estleg:LegalProvision"}
    assert _scan(vocab, {"@graph": [version]}, {"@graph": [declared]}) == []


def test_a_declared_subclass_satisfies_the_axiom():
    vocab = [
        {"@id": "estleg:Law", "@type": "owl:Class", "rdfs:subClassOf": {"@id": "estleg:Act"}},
        _prop("estleg:partOfAct", range_="estleg:Act"),
    ]
    graph = [
        {"@id": "estleg:X_Par_1", "estleg:partOfAct": {"@id": "estleg:X_Map"}},
        {"@id": "estleg:X_Map", "@type": ["owl:Ontology", "estleg:Law"]},
    ]
    assert _scan(vocab, {"@graph": graph}) == []


def test_expanded_iris_and_nested_nodes_are_read_like_compact_ones():
    vocab = [_prop("estleg:versionOf", range_="estleg:LegalProvision")]
    nested = {
        "@id": "https://w3id.org/estleg/Chapter_1",
        "estleg:hasSection": [
            {
                "@id": "https://w3id.org/estleg/KarS_Par_100",
                "@type": ["https://w3id.org/estleg/LegalProvision"],
            }
        ],
    }
    declared = {"@id": "estleg:KarS_Par_100_v1", "estleg:versionOf": {"@id": "estleg:KarS_Par_100"}}
    assert _scan(vocab, {"@graph": [nested, declared]}) == []

    expanded_key = {
        "@id": "estleg:KarS_Par_101_v1",
        "https://w3id.org/estleg/versionOf": {"@id": "https://w3id.org/estleg/KarS_Par_101"},
    }
    (finding,) = _scan(vocab, {"@graph": [expanded_key]})
    assert finding.nodes == ("estleg:KarS_Par_101",)


def test_only_a_multipart_part_root_is_tolerated_as_an_act():
    vocab = [_prop("estleg:temporalStatus", domain="estleg:Act")]
    root = {
        "@id": "estleg:AOS_Osa1",
        "@type": ["estleg:Part"],
        "estleg:isPartOf": {"@id": "estleg:AOS_Map"},
        "estleg:temporalStatus": "inForce",
    }
    stray = {"@id": "estleg:AOS_Osa9", "@type": ["estleg:Part"], "estleg:temporalStatus": "inForce"}
    (finding,) = _scan(vocab, {"@graph": [root, stray]})
    assert finding.nodes == ("estleg:AOS_Osa9",)


def test_a_sub_property_inherits_the_range_above_it():
    vocab = [
        _prop("estleg:superProp", range_="estleg:Act"),
        {**_prop("estleg:subProp"), "rdfs:subPropertyOf": {"@id": "estleg:superProp"}},
    ]
    doc = {"@id": "estleg:Doc_1", "estleg:subProp": {"@id": "estleg:Target"}}
    (finding,) = _scan(vocab, {"@graph": [doc]})
    assert (finding.prop, finding.cls, finding.nodes) == (
        "estleg:subProp",
        "estleg:Act",
        ("estleg:Target",),
    )


def test_a_reference_outside_the_estleg_namespace_is_still_typed():
    vocab = [_prop("estleg:partOfAct", range_="estleg:Act")]
    doc = {"@id": "estleg:X_Par_1", "estleg:partOfAct": {"@id": "http://example.org/act/1"}}
    (finding,) = _scan(vocab, {"@graph": [doc]})
    assert finding.nodes == ("http://example.org/act/1",)


def test_an_open_axiom_types_nothing():
    vocab = [_prop("estleg:references", domain="owl:Thing", range_="rdfs:Resource")]
    node = {"@id": "estleg:N", "estleg:references": {"@id": "estleg:M"}}
    assert _scan(vocab, {"@graph": [node]}) == []


def test_a_range_no_shape_targets_is_not_reported():
    vocab = [_prop("estleg:hasTopic", range_="estleg:Topic")]
    node = {"@id": "estleg:N", "estleg:hasTopic": {"@id": "estleg:Topic_Civil"}}
    assert _scan(vocab, {"@graph": [node]}) == []


def test_a_union_domain_entails_no_named_type():
    union = {"@type": "owl:Class", "owl:unionOf": {"@list": [{"@id": "estleg:Act"}]}}
    vocab = [{**_prop("estleg:partOfAct"), "rdfs:domain": union}]
    node = {"@id": "estleg:N", "estleg:partOfAct": {"@id": "estleg:A"}}
    assert _scan(vocab, {"@graph": [node]}) == []


def test_every_domain_a_property_declares_applies():
    listed = {
        **_prop("estleg:versionText"),
        "rdfs:domain": [{"@id": "estleg:ProvisionVersion"}, {"@id": "estleg:Act"}],
    }
    node = {"@id": "estleg:N", "@type": "estleg:ProvisionVersion", "estleg:versionText": "x"}
    (finding,) = _scan([listed], {"@graph": [node]})
    assert (finding.axis, finding.cls) == ("domain", "estleg:Act")


def test_shapes_file_yields_the_shaped_classes():
    """An empty set would make every bucket scan clean; every other test injects SHAPED."""
    assert {
        "estleg:Act",
        "estleg:LegalProvision",
        "estleg:ProvisionVersion",
    } <= cpt.shaped_classes()


# --- scanning a bucket from disk -----------------------------------------------


def _bucket(tmp_path, monkeypatch, files: dict[str, str], vocab: list[dict] | None = None):
    """Point the ``curia`` bucket at ``files`` under a throwaway krr directory."""
    if vocab is not None:
        (tmp_path / cpt.VOCAB_NAME).write_text(json.dumps({"@graph": vocab}), encoding="utf-8")
    paths = []
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        paths.append(tmp_path / name)
    monkeypatch.setitem(cpt.BUCKETS, "curia", lambda krr: paths)
    return tmp_path


def test_an_axiom_declared_in_a_corpus_file_types_nodes_too(tmp_path, monkeypatch):
    """pyshacl loads the whole bucket into one graph; legacy OWL modules carry their own axioms."""
    module = {
        "@graph": [
            _prop("estleg:localProp", range_="estleg:Act"),
            {"@id": "estleg:Doc_1", "estleg:localProp": {"@id": "estleg:Stub_X"}},
        ]
    }
    krr = _bucket(tmp_path, monkeypatch, {"module_peep.json": json.dumps(module)}, vocab=[])
    (finding,) = cpt.scan_bucket("curia", krr=krr)
    assert (finding.prop, finding.nodes) == ("estleg:localProp", ("estleg:Stub_X",))


@pytest.mark.parametrize(
    ("files", "vocab", "needle"),
    [
        (
            {"pointer_peep.json": "version https://git-lfs.github.com/spec/v1\n"},
            [],
            "pointer_peep.json",
        ),
        ({}, [], "no corpus files"),
        ({"ok_peep.json": '{"@graph": []}'}, None, cpt.VOCAB_NAME),
    ],
    ids=["lfs-pointer", "vanished-bucket", "missing-vocabulary"],
)
def test_unusable_input_is_an_error_not_a_clean_bucket(tmp_path, monkeypatch, files, vocab, needle):
    krr = _bucket(tmp_path, monkeypatch, files, vocab)
    with pytest.raises(cpt.CannotScan, match=needle):
        cpt.scan_bucket("curia", krr=krr)


def test_cli_reports_unusable_input_with_its_own_exit_code(tmp_path, monkeypatch, capsys):
    _bucket(tmp_path, monkeypatch, {})
    assert cpt.main(["--bucket", "curia"]) == 2
    assert "no corpus files" in capsys.readouterr().out


# --- the builder tables ------------------------------------------------------


def test_backfill_table_agrees_with_the_overwrites():
    """Two tables, one truth: a stale DOMAIN_RANGE row is how #433 undid #618."""
    for prop, domain in OVERWRITE_DOMAIN.items():
        if prop in DOMAIN_RANGE:
            assert DOMAIN_RANGE[prop][0] == domain, prop
    for prop, range_ in OVERWRITE_RANGE.items():
        if prop in DOMAIN_RANGE and prop not in FORBIDDEN_NO_RANGE:
            assert DOMAIN_RANGE[prop][1] == range_, prop


def test_includes_hints_only_annotate_open_axioms():
    for prop in DOMAIN_INCLUDES:
        assert OVERWRITE_DOMAIN.get(prop) == "owl:Thing", prop
    for prop in RANGE_INCLUDES:
        assert prop in FORBIDDEN_NO_RANGE or OVERWRITE_RANGE.get(prop) in OPEN_RANGES, prop


def test_consolidator_overwrites_the_merged_copy_and_rebuilds_hints():
    stale = [
        _prop(
            "estleg:interpretsVersion",
            domain="estleg:CourtDecision",
            range_="estleg:ProvisionVersion",
        ),
        {
            **_prop("estleg:changeType", domain="estleg:ProposedAmendment"),
            "@type": ["owl:DatatypeProperty"],
        },
        _prop("estleg:enactedBy", domain="estleg:Act", range_="estleg:Issuer"),
        # A hint whose table entry is gone must not survive a rebuild.
        {**_prop("estleg:references"), "schema:rangeIncludes": [{"@id": "estleg:Act"}]},
    ]
    nodes, _ = build_consolidated_graph({"@graph": stale}, extra_sources=[])
    by_id = {node["@id"]: node for node in nodes}

    version_edge = by_id["estleg:interpretsVersion"]
    assert version_edge["rdfs:range"] == {"@id": "rdfs:Resource"}
    assert version_edge["schema:rangeIncludes"] == [{"@id": "estleg:ProvisionVersion"}]
    assert version_edge["rdfs:domain"] == {"@id": "estleg:CourtDecision"}

    assert by_id["estleg:changeType"]["rdfs:domain"] == {"@id": "estleg:DraftLegislation"}

    issuer_edge = by_id["estleg:enactedBy"]
    assert issuer_edge["rdfs:domain"] == {"@id": "owl:Thing"}
    assert issuer_edge["schema:domainIncludes"] == [
        {"@id": "estleg:Act"},
        {"@id": "estleg:LegalProvision"},
    ]
    assert issuer_edge["rdfs:range"] == {"@id": "estleg:Issuer"}

    assert "schema:rangeIncludes" not in by_id["estleg:references"]


def test_a_live_comment_the_corpus_contradicts_is_overwritten():
    """REAL_COMMENTS only replaces placeholders; a wrong comment that is already there needs this."""
    stale = {
        **_prop("estleg:amendingDraft"),
        "rdfs:comment": "Links an enacted act to a draft bill.",
    }
    nodes, _ = build_consolidated_graph({"@graph": [stale]}, extra_sources=[])
    (node,) = [n for n in nodes if n["@id"] == "estleg:amendingDraft"]
    assert node["rdfs:comment"] == OVERWRITE_COMMENT["estleg:amendingDraft"]


# --- the committed corpus ----------------------------------------------------


@pytest.mark.corpus
@pytest.mark.parametrize("bucket", sorted(BUCKETS))
def test_committed_bucket_has_no_phantom_typing(bucket):
    findings = cpt.scan_bucket(bucket)
    assert not findings, "\n".join(f.format() for f in findings)
