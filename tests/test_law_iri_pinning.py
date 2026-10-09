"""A regeneration keeps the law/provision IRIs the corpus already publishes.

docs/STABILITY.md: "Law/provision local names are frozen for MINOR/PATCH. A
rename is MAJOR." The generator re-mints structural IRIs from the XML, so the
committed repair-pass IRIs (hash dedupe suffixes, osa renames) come back
different unless ``law_iri_pinning.pin_to_committed`` maps them back.
"""

from __future__ import annotations

import copy
from collections import Counter
import json

from estleg import generate_all_laws as gal
from estleg.law_iri_pinning import (
    identity_keys,
    normalise_number,
    normalise_text,
    pin_to_committed,
)

ACT = "estleg:T_Map"


def _chapter(iri: str, label: str, cluster: str) -> dict:
    return {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:Chapter"],
        "rdfs:label": {"@value": label, "@language": "et"},
        "estleg:partOfAct": {"@id": ACT},
        "dcterms:subject": {"@id": cluster},
    }


def _cluster(iri: str, label: str) -> dict:
    return {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:TopicCluster", "skos:Concept"],
        "rdfs:label": {"@value": label, "@language": "et"},
        "skos:inScheme": {"@id": "estleg:T_TopicScheme"},
    }


def _division(iri: str, label: str, chapter: str) -> dict:
    return {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:Division"],
        "rdfs:label": {"@value": label, "@language": "et"},
        "estleg:isPartOf": {"@id": chapter},
    }


def _provision(iri: str, number: str, container: str, cluster: str, subs: list[str]) -> dict:
    node = {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
        "estleg:paragrahv": f"§ {number}.",
        "rdfs:label": {"@value": f"§ {number}. Pealkiri", "@language": "et"},
        "estleg:partOfAct": {"@id": ACT},
        "estleg:requestedCluster": {"@id": cluster},
        "estleg:isPartOf": {"@id": container},
    }
    if subs:
        node["estleg:hasSubsection"] = [{"@id": s} for s in subs]
    return node


def _subsection(iri: str, number: str, parent: str, text: str = "Tekst.") -> dict:
    return {
        "@id": iri,
        "@type": ["estleg:Subsection", "owl:NamedIndividual"],
        "estleg:subsectionNumber": number,
        "estleg:legalText": f"({number}) {text}",
        "estleg:parentProvision": {"@id": parent},
    }


def _doc(*nodes: dict) -> dict:
    root = {"@id": ACT, "@type": ["estleg:Act", "estleg:Law"], "dcterms:title": "Testseadus"}
    scheme = {
        "@id": "estleg:T_TopicScheme",
        "@type": ["skos:ConceptScheme"],
        "skos:hasTopConcept": [
            {"@id": n["@id"]} for n in nodes if "estleg:TopicCluster" in n["@type"]
        ],
    }
    return {"@context": {}, "@graph": [root, scheme, *nodes]}


def _committed() -> dict:
    """What a repair pass left in the corpus: hash-suffixed duplicates."""
    return _doc(
        _cluster("estleg:Cluster_T_6", "§1–2 Üldsätted"),
        _cluster("estleg:Cluster_T_6_2_2", "§3–3 Kriisiroll"),
        _chapter("estleg:Chapter_T_6", "6. peatükk – Üldsätted", "estleg:Cluster_T_6"),
        _chapter("estleg:Chapter_T_6_807c6001", "6. peatükk – Kriisiroll", "estleg:Cluster_T_6_2_2"),
        _division("estleg:Division_T_6_1_c560c6bc", "1. jagu – Meetmed", "estleg:Chapter_T_6_807c6001"),
        _provision("estleg:T_Par_1", "1", "estleg:Chapter_T_6", "estleg:Cluster_T_6",
                   ["estleg:T_Par_1_Lg_1", "estleg:T_Par_1_Lg_2"]),
        _subsection("estleg:T_Par_1_Lg_1", "1", "estleg:T_Par_1"),
        _subsection("estleg:T_Par_1_Lg_2", "2", "estleg:T_Par_1"),
        _provision("estleg:T_Par_2", "2", "estleg:Chapter_T_6", "estleg:Cluster_T_6", []),
        _provision("estleg:T_Par_3", "3", "estleg:Division_T_6_1_c560c6bc", "estleg:Cluster_T_6_2_2",
                   ["estleg:T_Par_3_Lg_1"]),
        _subsection("estleg:T_Par_3_Lg_1", "1", "estleg:T_Par_3"),
    )


def _regenerated() -> dict:
    """The same act as the generator mints it today, after a redaction that
    added § 3¹ and lõige 3 of § 3 and repealed § 2."""
    return _doc(
        _cluster("estleg:Cluster_T_6", "§1–1 Üldsätted"),
        _cluster("estleg:Cluster_T_6_1", "§3–3 Kriisiroll"),
        _chapter("estleg:Chapter_T_6", "6. peatükk – Üldsätted", "estleg:Cluster_T_6"),
        _chapter("estleg:Chapter_T_6_1", "6. peatükk – Kriisiroll", "estleg:Cluster_T_6_1"),
        _division("estleg:Division_T_6_1_1", "1. jagu – Meetmed", "estleg:Chapter_T_6_1"),
        _provision("estleg:T_Par_1", "1", "estleg:Chapter_T_6", "estleg:Cluster_T_6",
                   ["estleg:T_Par_1_Lg_1", "estleg:T_Par_1_Lg_2"]),
        _subsection("estleg:T_Par_1_Lg_1", "1", "estleg:T_Par_1"),
        _subsection("estleg:T_Par_1_Lg_2", "2", "estleg:T_Par_1"),
        _provision("estleg:T_Par_3", "3", "estleg:Division_T_6_1_1", "estleg:Cluster_T_6_1",
                   ["estleg:T_Par_3_Lg_1", "estleg:T_Par_3_Lg_3"]),
        _subsection("estleg:T_Par_3_Lg_1", "1", "estleg:T_Par_3"),
        _subsection("estleg:T_Par_3_Lg_3", "3", "estleg:T_Par_3"),
        _provision("estleg:T_Par_3_1", "3¹", "estleg:Division_T_6_1_1", "estleg:Cluster_T_6_1", []),
    )


def _ids(doc: dict) -> set[str]:
    return {n["@id"] for n in doc["@graph"]}


def _node(doc: dict, iri: str) -> dict:
    return next(n for n in doc["@graph"] if n["@id"] == iri)


def _all_refs(value: object) -> set[str]:
    if isinstance(value, dict):
        refs = {value["@id"]} if isinstance(value.get("@id"), str) and len(value) == 1 else set()
        for item in value.values():
            refs |= _all_refs(item)
        return refs
    if isinstance(value, list):
        return set().union(*(_all_refs(v) for v in value)) if value else set()
    return set()


def test_normalisation_folds_superscripts_without_merging_numbers():
    assert normalise_number("§ 26¹.") == normalise_number("§ 26<sup>1</sup>.") == "26^1"
    assert normalise_number("§ 261.") == "261"
    assert normalise_number("2¹") == "2^1"
    assert normalise_text("6. peatükk – ÜLDSÄTTED") == normalise_text("6.  peatükk - Üldsätted")


def test_containers_renamed_by_the_generator_keep_committed_iris():
    pinned, stats = pin_to_committed(_regenerated(), _committed())
    ids = _ids(pinned)
    assert {"estleg:Chapter_T_6_807c6001", "estleg:Cluster_T_6_2_2",
            "estleg:Division_T_6_1_c560c6bc"} <= ids
    assert not {"estleg:Chapter_T_6_1", "estleg:Cluster_T_6_1", "estleg:Division_T_6_1_1"} & ids
    assert stats.renamed == {"Chapter": 1, "TopicCluster": 1, "Division": 1}


def test_cluster_follows_its_chapter_although_the_range_label_moved():
    pinned, _ = pin_to_committed(_regenerated(), _committed())
    # "§1–2 Üldsätted" became "§1–1 Üldsätted": still the twin of chapter 6.
    assert _node(pinned, "estleg:Cluster_T_6")["rdfs:label"]["@value"] == "§1–1 Üldsätted"


def test_every_reference_is_rewritten():
    pinned, _ = pin_to_committed(_regenerated(), _committed())
    ids = _ids(pinned)
    dangling = _all_refs(pinned["@graph"]) - ids
    assert dangling == set()
    par3 = _node(pinned, "estleg:T_Par_3")
    assert par3["estleg:isPartOf"] == {"@id": "estleg:Division_T_6_1_c560c6bc"}
    assert par3["estleg:requestedCluster"] == {"@id": "estleg:Cluster_T_6_2_2"}
    chapter = _node(pinned, "estleg:Chapter_T_6_807c6001")
    assert chapter["dcterms:subject"] == {"@id": "estleg:Cluster_T_6_2_2"}
    assert _node(pinned, "estleg:Division_T_6_1_c560c6bc")["estleg:isPartOf"] == {
        "@id": "estleg:Chapter_T_6_807c6001"
    }
    scheme = _node(pinned, "estleg:T_TopicScheme")
    assert {"@id": "estleg:Cluster_T_6_2_2"} in scheme["skos:hasTopConcept"]


def test_new_elements_keep_generator_iris_and_removals_stay_removed():
    pinned, stats = pin_to_committed(_regenerated(), _committed())
    ids = _ids(pinned)
    assert "estleg:T_Par_3_1" in ids and "estleg:T_Par_3_Lg_3" in ids
    assert "estleg:T_Par_2" not in ids
    assert stats.removed_iris == ["estleg:T_Par_2"]
    assert stats.unmatched == {"LegalProvision": 1, "Subsection": 1}


def test_reassignment_is_impossible():
    """A new element never takes the committed IRI of a removed element."""
    committed = _committed()
    regenerated = _regenerated()
    # § 2 is repealed; the generator now mints T_Par_2 for a different §.
    for node in regenerated["@graph"]:
        if node["@id"] == "estleg:T_Par_3_1":
            node["@id"] = "estleg:T_Par_2"
    pinned, stats = pin_to_committed(regenerated, committed)
    node = next(n for n in pinned["@graph"] if n.get("estleg:paragrahv") == "§ 3¹.")
    assert node["@id"] != "estleg:T_Par_2"
    assert node["@id"].startswith("estleg:T_Par_2_")
    assert stats.disambiguated == {"estleg:T_Par_2": node["@id"]}
    assert "estleg:T_Par_2" not in _ids(pinned)


def test_reassignment_to_a_matched_iri_is_impossible():
    """Two elements cannot end up sharing one committed IRI."""
    committed = _committed()
    regenerated = _regenerated()
    # The generator mints Chapter_T_6_807c6001 for a brand-new chapter while
    # the committed holder of that IRI is matched by label elsewhere.
    regenerated["@graph"].append(
        _chapter("estleg:Chapter_T_6_807c6001", "6¹. peatükk – Uus", "estleg:Cluster_T_6")
    )
    pinned, _ = pin_to_committed(regenerated, committed)
    ids = [n["@id"] for n in pinned["@graph"]]
    assert len(ids) == len(set(ids))
    kriis = next(n for n in pinned["@graph"] if n.get("rdfs:label", {}).get("@value") == "6. peatükk – Kriisiroll")
    assert kriis["@id"] == "estleg:Chapter_T_6_807c6001"


def test_unmatched_lõige_is_rebased_onto_a_pinned_parent():
    committed = _doc(
        _provision("estleg:T_Par_5_abcdef12", "5", ACT, "estleg:X", ["estleg:T_Par_5_abcdef12_Lg_1"]),
        _subsection("estleg:T_Par_5_abcdef12_Lg_1", "1", "estleg:T_Par_5_abcdef12"),
    )
    regenerated = _doc(
        _provision("estleg:T_Par_5", "5", ACT, "estleg:X", ["estleg:T_Par_5_Lg_1", "estleg:T_Par_5_Lg_2"]),
        _subsection("estleg:T_Par_5_Lg_1", "1", "estleg:T_Par_5"),
        _subsection("estleg:T_Par_5_Lg_2", "2", "estleg:T_Par_5"),
    )
    pinned, _ = pin_to_committed(regenerated, committed)
    assert {"estleg:T_Par_5_abcdef12", "estleg:T_Par_5_abcdef12_Lg_1",
            "estleg:T_Par_5_abcdef12_Lg_2"} <= _ids(pinned)
    lg2 = _node(pinned, "estleg:T_Par_5_abcdef12_Lg_2")
    assert lg2["estleg:parentProvision"] == {"@id": "estleg:T_Par_5_abcdef12"}


def test_duplicate_lõiked_pair_by_occurrence():
    """Committed ``Lg_1_x2`` / ``Lg_1`` keep pairing with the same text."""
    committed = _doc(
        _provision("estleg:T_Par_2", "2", ACT, "estleg:X", []),
        {**_subsection("estleg:T_Par_2_Lg_1_x2", "1", "estleg:T_Par_2", "Sissejuhatus"),
         "estleg:subsectionNumber": None},
        _subsection("estleg:T_Par_2_Lg_1", "1", "estleg:T_Par_2", "Sisu"),
    )
    regenerated = _doc(
        _provision("estleg:T_Par_2", "2", ACT, "estleg:X", []),
        _subsection("estleg:T_Par_2_Lg_1", "1", "estleg:T_Par_2", "Sissejuhatus"),
        _subsection("estleg:T_Par_2_Lg_1_x2", "1", "estleg:T_Par_2", "Sisu"),
    )
    pinned, _ = pin_to_committed(regenerated, committed)
    by_text = {n.get("estleg:legalText"): n["@id"] for n in pinned["@graph"]}
    assert by_text["(1) Sissejuhatus"] == "estleg:T_Par_2_Lg_1_x2"
    assert by_text["(1) Sisu"] == "estleg:T_Par_2_Lg_1"


def test_pinning_is_idempotent_and_leaves_inputs_alone():
    committed = _committed()
    regenerated = _regenerated()
    committed_before = copy.deepcopy(committed)
    regenerated_before = copy.deepcopy(regenerated)
    once, _ = pin_to_committed(regenerated, committed)
    twice, stats = pin_to_committed(once, committed)
    assert twice == once
    assert stats.renamed == {}
    assert committed == committed_before and regenerated == regenerated_before
    same, stats = pin_to_committed(committed, committed)
    assert same == committed and stats.removed == {} and stats.renamed == {}


def test_no_committed_document_is_a_no_op():
    regenerated = _regenerated()
    pinned, stats = pin_to_committed(regenerated, None)
    assert pinned == regenerated and stats.matched == {}


def test_identity_keys_ignore_iris():
    renamed = json.loads(json.dumps(_committed()).replace("_807c6001", "_zzz"))
    assert sorted(identity_keys(renamed).values(), key=repr) == sorted(
        identity_keys(_committed()).values(), key=repr
    )


def test_write_law_output_pins_then_merges_enrichments(tmp_path):
    committed = _committed()
    _node(committed, "estleg:Chapter_T_6_807c6001")["estleg:competentAuthority"] = {"@id": "estleg:Institution_X"}
    _node(committed, "estleg:T_Par_3")["estleg:interpretedBy"] = [{"@id": "estleg:RK_1"}]
    out = tmp_path / "t_peep.json"
    out.write_text(json.dumps(committed), encoding="utf-8")
    status = gal.write_law_output(out, _regenerated(), mode="refresh")
    assert status == "refreshed"
    written = json.loads(out.read_text(encoding="utf-8"))
    assert _node(written, "estleg:Chapter_T_6_807c6001")["estleg:competentAuthority"] == {"@id": "estleg:Institution_X"}
    assert _node(written, "estleg:T_Par_3")["estleg:interpretedBy"] == [{"@id": "estleg:RK_1"}]
    assert "estleg:Chapter_T_6_1" not in _ids(written)


def test_write_law_output_no_pin_opt_out(tmp_path):
    out = tmp_path / "t_peep.json"
    out.write_text(json.dumps(_committed()), encoding="utf-8")
    gal.write_law_output(out, _regenerated(), mode="refresh", pin_iris=False)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert "estleg:Chapter_T_6_1" in _ids(written)


def test_merge_drops_structure_the_new_redaction_no_longer_has(tmp_path):
    """A § repealed in the new redaction keeps neither its old text nor
    references to lõiked the generator no longer emits."""
    committed = _committed()
    regenerated = _regenerated()
    par1 = _node(regenerated, "estleg:T_Par_1")
    del par1["estleg:hasSubsection"]
    regenerated["@graph"] = [
        n for n in regenerated["@graph"] if n.get("estleg:parentProvision") != {"@id": "estleg:T_Par_1"}
    ]
    _node(committed, "estleg:T_Par_1")["estleg:legalText"] = "(1) Vana tekst."
    out = tmp_path / "t_peep.json"
    out.write_text(json.dumps(committed), encoding="utf-8")
    gal.write_law_output(out, regenerated, mode="force")
    written = _node(json.loads(out.read_text(encoding="utf-8")), "estleg:T_Par_1")
    assert "estleg:hasSubsection" not in written
    assert "estleg:legalText" not in written


def test_repealed_provision_does_not_keep_a_summary_of_its_old_text(tmp_path):
    committed = _committed()
    _node(committed, "estleg:T_Par_1")["estleg:legalText"] = "(1) Vana tekst."
    _node(committed, "estleg:T_Par_1")["estleg:summary"] = "(1) Vana tekst."
    _node(committed, "estleg:T_Par_2")["estleg:summary"] = "Käsitsi kirjutatud kokkuvõte."
    regenerated = _regenerated()
    par1 = _node(regenerated, "estleg:T_Par_1")
    par1["estleg:summary"] = "§ 1. Pealkiri"
    del par1["estleg:hasSubsection"]
    regenerated["@graph"] = [
        n for n in regenerated["@graph"] if n.get("estleg:parentProvision") != {"@id": "estleg:T_Par_1"}
    ]
    regenerated["@graph"].append(
        {**_provision("estleg:T_Par_2", "2", "estleg:Chapter_T_6", "estleg:Cluster_T_6", []),
         "estleg:summary": "§ 2."}
    )
    out = tmp_path / "t_peep.json"
    out.write_text(json.dumps(committed), encoding="utf-8")
    gal.write_law_output(out, regenerated, mode="force")
    written = json.loads(out.read_text(encoding="utf-8"))
    assert _node(written, "estleg:T_Par_1")["estleg:summary"] == "§ 1. Pealkiri"
    # A summary curated for a node that never had text still survives.
    assert _node(written, "estleg:T_Par_2")["estleg:summary"] == "Käsitsi kirjutatud kokkuvõte."


def _citation(iri: str, source: str) -> dict:
    return {
        "@id": iri,
        "@type": ["owl:NamedIndividual", "estleg:Citation"],
        "estleg:citationSource": {"@id": source},
        "estleg:citationText": "Sotsiaalmaksuseaduse § 21",
    }


def _committed_with_citations() -> dict:
    committed = _committed()
    committed["@graph"] += [
        _citation("estleg:Citation_T_P_1_Lg_2_1", "estleg:T_Par_1_Lg_2"),
        _citation("estleg:Citation_T_P_2_1", "estleg:T_Par_2"),  # § 2 is repealed
        _citation("estleg:Citation_T_P_3_Lg_1_1", "estleg:T_Par_3_Lg_1"),
    ]
    return committed


def test_overlay_citation_nodes_survive_regeneration(tmp_path):
    """#697: cross-reference Citation nodes are overlay, kept on unchanged identities."""
    out = tmp_path / "t_peep.json"
    out.write_text(json.dumps(_committed_with_citations()), encoding="utf-8")
    before = Counter(gal.DANGLING_OVERLAY_DROPPED)
    gal.write_law_output(out, _regenerated(), mode="force")
    ids = _ids(json.loads(out.read_text(encoding="utf-8")))
    assert {"estleg:Citation_T_P_1_Lg_2_1", "estleg:Citation_T_P_3_Lg_1_1"} <= ids
    # The citation of the repealed § 2 points at nothing: dropped and counted.
    assert "estleg:Citation_T_P_2_1" not in ids
    assert gal.DANGLING_OVERLAY_DROPPED["estleg:Citation"] - before["estleg:Citation"] == 1


def test_overlay_merge_is_idempotent(tmp_path):
    out = tmp_path / "t_peep.json"
    out.write_text(json.dumps(_committed_with_citations()), encoding="utf-8")
    gal.write_law_output(out, _regenerated(), mode="force")
    first = out.read_text(encoding="utf-8")
    assert gal.write_law_output(out, _regenerated(), mode="refresh") == "unchanged"
    assert out.read_text(encoding="utf-8") == first


def test_replace_overlays_opt_out_drops_citations(tmp_path, monkeypatch):
    monkeypatch.setattr(gal, "REPLACE_OVERLAYS", True)
    out = tmp_path / "t_peep.json"
    out.write_text(json.dumps(_committed_with_citations()), encoding="utf-8")
    gal.write_law_output(out, _regenerated(), mode="force")
    ids = _ids(json.loads(out.read_text(encoding="utf-8")))
    assert not any(i.startswith("estleg:Citation_") for i in ids)
    # IRIs are still pinned: the opt-out only discards the overlay.
    assert "estleg:Chapter_T_6_807c6001" in ids


def test_provisions_without_a_number_are_not_matched():
    committed = _doc({"@id": "estleg:Old", "@type": ["estleg:LegalProvision"]})
    regenerated = _doc({"@id": "estleg:New", "@type": ["estleg:LegalProvision"]})
    pinned, stats = pin_to_committed(regenerated, committed)
    assert "estleg:New" in _ids(pinned) and stats.renamed == {}
