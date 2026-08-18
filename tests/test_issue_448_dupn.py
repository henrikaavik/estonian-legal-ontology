"""#448 — content-derived collision suffixes replace _DupN."""
from __future__ import annotations

from estleg.generate_all_laws import (
    content_derived_dup_suffix,
    remint_dupn_id,
    remint_dupn_ids,
)


def test_content_suffix_from_section_range():
    assert content_derived_dup_suffix("§16–30 Meditsiiniseadmed") == "16_30"
    assert content_derived_dup_suffix("§ 1-15 ÜLDSÄTTED") == "1_15"


def test_remint_dupn_id_drops_dup_tail():
    taken = {"estleg:Cluster_MEDITS_3"}
    new = remint_dupn_id(
        "estleg:Cluster_MEDITS_3_Dup2", "§16–30 Seadmed", taken
    )
    assert new == "estleg:Cluster_MEDITS_3_16_30"
    assert "_Dup" not in new


def test_remint_dupn_ids_rewrites_graph_and_refs():
    doc = {
        "@graph": [
            {
                "@id": "estleg:Cluster_X_3",
                "rdfs:label": "§1–15 A",
                "skos:hasTopConcept": [{"@id": "estleg:Cluster_X_3_Dup2"}],
            },
            {
                "@id": "estleg:Cluster_X_3_Dup2",
                "rdfs:label": "§16–30 B",
            },
        ]
    }
    remap = remint_dupn_ids(doc)
    assert remap == {"estleg:Cluster_X_3_Dup2": "estleg:Cluster_X_3_16_30"}
    ids = [n["@id"] for n in doc["@graph"]]
    assert ids == ["estleg:Cluster_X_3", "estleg:Cluster_X_3_16_30"]
    assert doc["@graph"][0]["skos:hasTopConcept"] == [
        {"@id": "estleg:Cluster_X_3_16_30"}
    ]


def test_committed_law_peeps_have_no_dupn_iris():
    from estleg.validate_all import count_dupn_iris, root_law_peep_files

    count = count_dupn_iris(root_law_peep_files())
    assert count == 0, f"still {count} _DupN IRIs on root law peeps"
