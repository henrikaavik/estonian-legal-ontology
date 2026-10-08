"""#699 — official EU EuroVoc, precision-gated Estonian classifier, no
edit-distance closeMatch.

Unit tests are hermetic (fake CELLAR client, tmp_path). The ``corpus`` tests
gate the committed eurlex peeps and concepts layer.
"""

from __future__ import annotations

import json
import re

import pytest

from estleg import classify_eurovoc as ev
from estleg import fetch_eurovoc_official as fo
from estleg.extract_legal_concepts import (
    CONCEPTS_DIR,
    find_orthographic_variant_pairs,
    fold_orthographic_variants,
    orthographic_key,
    scrub_concept_graph,
    strip_close_match,
)
from estleg.generate_eu_legislation import legislation_to_node

XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"


def _binding(celex: str, concept: str) -> dict:
    return {
        "celex": {"type": "literal", "datatype": XSD_STRING, "value": celex},
        "concept": {"type": "uri", "value": concept},
    }


class FakeCellar:
    """Records queries; answers from a CELEX -> [concept IRI] table."""

    def __init__(self, table: dict[str, list[str]], fail_on: set[int] | None = None):
        self.table = table
        self.fail_on = fail_on or set()
        self.queries: list[str] = []

    def __call__(self, query: str) -> list[dict]:
        self.queries.append(query)
        if len(self.queries) in self.fail_on:
            raise RuntimeError("HTTP 503")
        asked = re.findall(r'"([^"]+)"\^\^', query)
        return [_binding(c, iri) for c in asked for iri in self.table.get(c, [])]


# ---------------------------------------------------------------------------
# CELLAR fetch + stamping
# ---------------------------------------------------------------------------


def test_query_uses_typed_celex_literals_and_cdm_predicate():
    q = fo.build_eurovoc_query(["32016R0679", "31996L0029"])
    assert f'"32016R0679"^^<{XSD_STRING}>' in q
    assert "cdm:work_is_about_concept_eurovoc ?concept" in q
    assert "cdm:resource_legal_id_celex ?celex" in q


def test_query_rejects_unsafe_celex():
    with pytest.raises(ValueError):
        fo.build_eurovoc_query(['3" } DROP'])


def test_parse_bindings_keeps_numeric_and_c_ids_drops_others():
    bindings = [
        _binding("A", "http://eurovoc.europa.eu/2884"),
        _binding("A", "http://eurovoc.europa.eu/192"),
        _binding("A", "http://eurovoc.europa.eu/c_1c478aa5"),
        _binding("A", "http://example.org/not-eurovoc"),
        _binding("ZZZ", "http://eurovoc.europa.eu/1"),  # not requested
    ]
    mapping, invalid = fo.parse_bindings(bindings, ["A", "B"])
    assert mapping == {"A": ["192", "2884", "c_1c478aa5"], "B": []}
    assert invalid == 1


def test_fetch_batches_caches_and_rerun_is_offline():
    table = {"C1": ["http://eurovoc.europa.eu/10"], "C3": ["http://eurovoc.europa.eu/30"]}
    fake = FakeCellar(table)
    cache: dict[str, list[str]] = {}
    subjects, stats = fo.fetch_official_subjects(
        ["C3", "C1", "C2"], cache=cache, batch_size=2, query_fn=fake, delay=0
    )
    assert subjects == {"C1": ["10"], "C2": [], "C3": ["30"]}
    assert stats["batches"] == 2 and stats["failed_batches"] == 0
    # Second run is served from the cache: no queries.
    again, stats2 = fo.fetch_official_subjects(
        ["C1", "C2", "C3"], cache=cache, query_fn=fake, delay=0
    )
    assert again == subjects
    assert stats2["queried"] == 0 and len(fake.queries) == 2


def test_failed_batch_leaves_celex_unresolved_for_retry():
    fake = FakeCellar({"C1": ["http://eurovoc.europa.eu/10"]}, fail_on={1})
    cache: dict[str, list[str]] = {}
    subjects, stats = fo.fetch_official_subjects(
        ["C1"], cache=cache, query_fn=fake, delay=0, retries=1
    )
    assert subjects == {} and "C1" not in cache
    assert stats["failed_batches"] == 1


def test_offline_never_queries():
    fake = FakeCellar({})
    subjects, _ = fo.fetch_official_subjects(["C1"], cache={}, offline=True, query_fn=fake)
    assert subjects == {} and fake.queries == []


def test_cache_roundtrip(tmp_path):
    path = tmp_path / "cache.json"
    fo.save_cache({"B": ["c_ff", "30", "4"], "A": []}, path)
    assert fo.load_cache(path) == {"A": [], "B": ["4", "30", "c_ff"]}
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["celex_with_subjects"] == 1


def test_stamp_sets_subjects_is_about_and_provenance():
    node = {"@id": "estleg:EU_X"}
    assert fo.stamp_official_subjects(node, ["30", "4"]) is True
    refs = [{"@id": "http://eurovoc.europa.eu/4"}, {"@id": "http://eurovoc.europa.eu/30"}]
    assert node["dcterms:subject"] == refs
    assert node["eli:is_about"] == refs
    assert node["estleg:subjectSource"] == "cellar"
    assert fo.stamp_official_subjects(node, ["4", "30"]) is False  # idempotent


def test_stamp_empty_removes_only_cellar_subjects():
    cellar = {"@id": "a", "dcterms:subject": [{"@id": "x"}], "eli:is_about": [{"@id": "x"}],
              "estleg:subjectSource": "cellar"}
    assert fo.stamp_official_subjects(cellar, []) is True
    assert set(cellar) == {"@id"}
    other = {"@id": "b", "dcterms:subject": [{"@id": "x"}]}
    assert fo.stamp_official_subjects(other, []) is False
    assert other["dcterms:subject"] == [{"@id": "x"}]


def _eu_peep(celex_list):
    return {
        "@context": {},
        "@graph": [{"@id": "estleg:EURlex_Directives_Map", "@type": ["owl:Ontology"]}]
        + [
            {"@id": f"estleg:EU_{c}", "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
             "estleg:celexNumber": c}
            for c in celex_list
        ],
    }


def test_stamp_eurlex_peeps_reports_coverage(tmp_path):
    (tmp_path / "eurlex_directives_peep.json").write_text(
        json.dumps(_eu_peep(["C1", "C2", "C3"])), encoding="utf-8"
    )
    stats = fo.stamp_eurlex_peeps({"C1": ["10"], "C2": []}, tmp_path)
    assert stats["acts"] == 3
    assert stats["with_subjects"] == 1
    assert stats["unresolved"] == 1
    graph = json.loads((tmp_path / "eurlex_directives_peep.json").read_text())["@graph"]
    by_id = {n["@id"]: n for n in graph}
    assert by_id["estleg:EU_C1"]["estleg:subjectSource"] == "cellar"
    assert "dcterms:subject" not in by_id["estleg:EU_C2"]
    assert "dcterms:subject" not in by_id["estleg:EURlex_Directives_Map"]


def test_generator_node_carries_official_subjects():
    item = {"celex": "32016L0680", "title": "Direktiiv", "eurovoc_ids": ["5181", "c_ab"]}
    node = legislation_to_node(item, "Directive")
    assert node["dcterms:subject"] == [
        {"@id": "http://eurovoc.europa.eu/5181"},
        {"@id": "http://eurovoc.europa.eu/c_ab"},
    ]
    assert node["estleg:subjectSource"] == "cellar"
    bare = legislation_to_node({"celex": "32016L0681", "title": "D"}, "Directive")
    assert "dcterms:subject" not in bare and "estleg:subjectSource" not in bare


# ---------------------------------------------------------------------------
# Estonian classifier: constitutional-law gate, density ranking, cap 3
# ---------------------------------------------------------------------------


def _codes(text: str) -> list[str]:
    return [row[0] for row in ev.classify_text(text)]


def test_cap_is_three():
    assert ev.MAX_DOMAINS_PER_LAW == 3
    text = " ".join([
        "kuritegu karistus",  # criminal 573
        "haldusakt järelevalve",  # administrative 517
        "reklaam reklaam",  # advertising 2862
        "lennujaam lennuk",  # air transport 4505
        "muuseum kultuur",  # culture 317
    ])
    assert len(ev.classify_text(text)) == 3


def test_ranked_by_hits_per_1000_tokens_then_distinct_keywords():
    text = "reklaam " * 6 + "kuritegu karistus " * 2 + "muuseum kultuur"
    rows = ev.classify_text(text)
    assert [r[0] for r in rows] == ["2862", "573", "317"]
    # Equal density: more distinct keywords wins, then code.
    tie = ev.classify_text("muuseum kultuur kuritegu karistus")
    assert [r[0] for r in tie] == ["317", "573"]


def test_density_floor_drops_incidental_mention():
    filler = "sõna " * 4000
    assert "2862" not in _codes(filler + "reklaam")  # 0.25 hits / 1000 tokens
    assert "2862" in _codes(filler + "reklaam " * 8)  # 2 hits / 1000 tokens


def test_hits_per_1000_tokens():
    assert ev.hits_per_1000_tokens(3, 1500) == pytest.approx(2.0)
    assert ev.count_tokens("") == 1


def test_constitutional_law_ignores_government_and_republic():
    text = "Vabariigi Valitsus kehtestab. Vabariigi Valitsuse määrus. Riigikogu president"
    assert "527" not in _codes(text)


def test_constitutional_law_needs_two_constitutional_hits():
    assert "527" not in _codes("vastavalt põhiseaduse §-le 3 kehtestatakse kord")
    assert "527" in _codes("põhiseaduslikkuse järelevalve ja põhiseaduse muutmine")
    assert "527" in ev.EUROVOC_DOMAINS  # still in the SKOS scheme / override set
    assert not {"valitsus", "vabariig", "riigikogu", "president"} & set(
        ev.EUROVOC_DOMAINS["527"][3]
    )


# ---------------------------------------------------------------------------
# Concepts: no closeMatch, orthographic variants -> altLabel
# ---------------------------------------------------------------------------


def _concept(cid: str, label: str, defs: int = 1, **extra) -> dict:
    node = {
        "@id": cid,
        "@type": ["owl:NamedIndividual", "estleg:Concept", "skos:Concept"],
        "skos:prefLabel": {"@value": label, "@language": "et"},
        "estleg:hasDefinitionNode": [{"@id": f"{cid}_def{i}"} for i in range(defs)],
        "estleg:definitionCount": {"@value": str(defs), "@type": "xsd:integer"},
    }
    node.update(extra)
    return node


def test_orthographic_key_folds_spacing_hyphen_case_and_s_caron():
    assert orthographic_key("teenuse pakkuja") == orthographic_key("Teenusepakkuja")
    assert orthographic_key("Wi-Fi") == orthographic_key("WiFi")
    assert orthographic_key("t öötasu") == orthographic_key("töötasu")
    assert orthographic_key("šahht") == orthographic_key("shahht")


def test_orthographic_key_keeps_estonian_letters_distinct():
    assert orthographic_key("kasu") != orthographic_key("käsu")
    assert orthographic_key("laev") != orthographic_key("laps")


def test_fold_merges_variants_into_alt_label_and_retargets_links():
    graph = [
        _concept("estleg:Concept_teenuse_pakkuja", "teenuse pakkuja", defs=1),
        _concept("estleg:Concept_teenusepakkuja", "teenusepakkuja", defs=3),
        {"@id": "estleg:LC_1", "@type": ["estleg:LegalConcept"],
         "estleg:definesConcept": {"@id": "estleg:Concept_teenuse_pakkuja"}},
    ]
    pairs = find_orthographic_variant_pairs(graph)
    assert [(k, d) for k, d, _a, _b in pairs] == [
        ("estleg:Concept_teenusepakkuja", "estleg:Concept_teenuse_pakkuja")
    ]
    assert fold_orthographic_variants(graph) == 1
    ids = {n["@id"] for n in graph}
    assert "estleg:Concept_teenuse_pakkuja" not in ids
    keep = next(n for n in graph if n["@id"] == "estleg:Concept_teenusepakkuja")
    assert keep["skos:altLabel"] == [{"@value": "teenuse pakkuja", "@language": "et"}]
    lc = next(n for n in graph if n["@id"] == "estleg:LC_1")
    assert lc["estleg:definesConcept"] == {"@id": "estleg:Concept_teenusepakkuja"}


def test_split_word_artefact_is_folded_but_not_kept_as_label():
    graph = [
        _concept("estleg:Concept_tootasu", "töötasu", defs=5),
        _concept("estleg:Concept_t_ootasu", "t öötasu", defs=1),
    ]
    assert fold_orthographic_variants(graph) == 1
    assert len(graph) == 1
    assert "skos:altLabel" not in graph[0]


def test_strip_close_match_and_scrub_counts():
    graph = [
        _concept("estleg:Concept_laev", "laev",
                 **{"skos:closeMatch": [{"@id": "estleg:Concept_laps"}]}),
        _concept("estleg:Concept_laps", "laps",
                 **{"skos:closeMatch": {"@id": "estleg:Concept_laev"}}),
    ]
    counts = scrub_concept_graph(graph)
    assert counts["close_match_removed"] == 2
    assert counts["orthographic_folded"] == 0
    assert all("skos:closeMatch" not in n for n in graph)
    assert strip_close_match(graph) == 0


# ---------------------------------------------------------------------------
# Corpus gates
# ---------------------------------------------------------------------------


@pytest.mark.corpus
def test_corpus_concepts_have_no_close_match():
    graph = json.loads(
        (CONCEPTS_DIR / "concepts_combined.jsonld").read_text(encoding="utf-8")
    )["@graph"]
    holders = [n["@id"] for n in graph if "skos:closeMatch" in n]
    assert holders == [], holders[:10]
    # Hygiene is at a fixed point: re-running it changes nothing.
    assert not any(scrub_concept_graph(graph).values())


@pytest.mark.corpus
def test_corpus_eu_acts_with_subjects_carry_cellar_provenance():
    acts = with_subjects = 0
    for name in fo.EURLEX_PEEP_NAMES:
        doc = json.loads((fo.EURLEX_DIR / name).read_text(encoding="utf-8"))
        for node in fo.iter_eu_act_nodes(doc["@graph"]):
            acts += 1
            subjects = node.get("dcterms:subject")
            if not subjects:
                assert "estleg:subjectSource" not in node, node["@id"]
                continue
            with_subjects += 1
            assert node["estleg:subjectSource"] == "cellar", node["@id"]
            assert isinstance(subjects, list), node["@id"]
            assert node["eli:is_about"] == subjects, node["@id"]
            for ref in subjects:
                assert fo.eurovoc_id(ref["@id"]) is not None, (node["@id"], ref)
    assert acts > 30000
    # 33,206 / 33,242 at the 2026-10-08 fetch; CELLAR indexes nearly all acts.
    assert with_subjects / acts > 0.99


# ---------------------------------------------------------------------------
# EUDocType T-Box individuals live in the CV/schema only (#709 drift fix)
# ---------------------------------------------------------------------------


def test_strip_doc_type_declarations_keeps_acts_and_references():
    from estleg.generate_eu_legislation import strip_doc_type_declarations

    graph = [
        {"@id": "estleg:EURlex_Regulations_Map", "@type": ["owl:Ontology"]},
        {"@id": "estleg:EUDocType_ParliamentPosition",
         "@type": ["owl:NamedIndividual", "estleg:EUDocumentType"]},
        {"@id": "estleg:EU_52025AP0047", "@type": ["estleg:EULegislation"],
         "estleg:euDocumentType": {"@id": "estleg:EUDocType_ParliamentPosition"}},
    ]
    assert strip_doc_type_declarations(graph) == 1
    assert [n["@id"] for n in graph] == ["estleg:EURlex_Regulations_Map", "estleg:EU_52025AP0047"]
    assert strip_doc_type_declarations(graph) == 0


def test_schema_doc_type_individuals_use_cv_skos_form():
    from estleg.generate_eu_legislation import generate_schema_nodes

    doc_types = [n for n in generate_schema_nodes() if n["@id"].startswith("estleg:EUDocType_")]
    assert len(doc_types) == 5
    for node in doc_types:
        assert node["@type"] == ["owl:NamedIndividual", "estleg:EUDocumentType", "skos:Concept"]
        assert node["skos:inScheme"] == {"@id": "estleg:EUDocumentTypeScheme"}
        assert node["skos:topConceptOf"] == {"@id": "estleg:EUDocumentTypeScheme"}


def test_committed_eurlex_peeps_declare_no_doc_type_individuals():
    for name in fo.EURLEX_PEEP_NAMES:
        doc = json.loads((fo.EURLEX_DIR / name).read_text(encoding="utf-8"))
        ids = [n.get("@id", "") for n in doc["@graph"] if isinstance(n, dict)]
        assert not [i for i in ids if i.startswith("estleg:EUDocType_")], name
