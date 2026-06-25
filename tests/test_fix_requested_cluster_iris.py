"""Unit tests for scripts/fix_requested_cluster_iris.py (#564).

Pure tmp_path / in-memory tests — no corpus, no LFS, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fix_requested_cluster_iris as fix  # noqa: E402

STEM = "politsei_seadus_peep"


def test_is_bare_cluster_id():
    # Bare: no scheme, or a space.
    assert fix.is_bare_cluster_id("law enforcement") is True
    assert fix.is_bare_cluster_id("military_service") is True
    assert fix.is_bare_cluster_id("politsei ülesanded") is True
    # Well-formed CURIE, no space -> not bare.
    assert fix.is_bare_cluster_id("estleg:Cluster_PoliceFunctions") is False
    assert fix.is_bare_cluster_id("estleg:Cluster_law_enforcement_x_peep") is False


def test_mint_cluster_iri_namespaces_by_file_stem():
    # Space -> underscore, diacritics transliterated, CURIE-prefixed, and the
    # file stem appended for global uniqueness.
    assert (
        fix.mint_cluster_iri("law enforcement", "politsei_seadus_peep")
        == "estleg:Cluster_law_enforcement_politsei_seadus_peep"
    )
    assert (
        fix.mint_cluster_iri("politsei ülesanded", "politsei_seadus_peep")
        == "estleg:Cluster_politsei_ulesanded_politsei_seadus_peep"
    )


def test_mint_cluster_iri_distinguishes_same_label_across_files():
    # The #564 collision: the same bare label in two files must mint DISTINCT
    # IRIs so the defining nodes do not collide on @id.
    a = fix.mint_cluster_iri("keskkonnakaitse", "keskkonnaseadus_peep")
    b = fix.mint_cluster_iri("keskkonnakaitse", "maapoue_seadus_peep")
    assert a != b
    assert a == "estleg:Cluster_keskkonnakaitse_keskkonnaseadus_peep"
    assert b == "estleg:Cluster_keskkonnakaitse_maapoue_seadus_peep"


def test_migrate_doc_rewrites_bare_and_defines_cluster():
    doc = {
        "@graph": [
            {
                "@id": "estleg:POLS_Par_1",
                "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                "estleg:requestedCluster": {"@id": "politsei ülesanded"},
            }
        ]
    }
    edges, clusters = fix.migrate_doc(doc, STEM)
    assert edges == 1
    assert clusters == 1
    g = doc["@graph"]
    minted = "estleg:Cluster_politsei_ulesanded_politsei_seadus_peep"
    # Edge rewritten to the namespaced CURIE.
    assert g[0]["estleg:requestedCluster"]["@id"] == minted
    # Defining node appended, typed + labelled with the ORIGINAL label.
    defn = g[-1]
    assert defn["@id"] == minted
    assert fix.TOPIC_CLUSTER_TYPE in defn["@type"]
    assert "skos:Concept" in defn["@type"]
    assert defn["rdfs:label"] == "politsei ülesanded"
    assert defn["skos:prefLabel"] == "politsei ülesanded"


def test_migrate_doc_leaves_wellformed_curies_untouched():
    doc = {
        "@graph": [
            {
                "@id": "estleg:P1",
                "estleg:requestedCluster": {"@id": "estleg:Cluster_PoliceFunctions"},
            },
            {
                "@id": "estleg:Cluster_PoliceFunctions",
                "@type": ["owl:NamedIndividual", "estleg:TopicCluster"],
            },
        ]
    }
    edges, clusters = fix.migrate_doc(doc, STEM)
    assert edges == 0
    assert clusters == 0
    # Graph length unchanged (no spurious cluster node minted).
    assert len(doc["@graph"]) == 2


def test_migrate_doc_does_not_redefine_existing_cluster():
    # The minted IRI already exists as a subject -> rewrite the edge but do
    # NOT append a duplicate defining node.
    minted = "estleg:Cluster_law_enforcement_" + STEM
    doc = {
        "@graph": [
            {
                "@id": "estleg:P1",
                "estleg:requestedCluster": {"@id": "law enforcement"},
            },
            {
                "@id": minted,
                "@type": ["owl:NamedIndividual", "estleg:TopicCluster"],
                "rdfs:label": "Existing label",
            },
        ]
    }
    edges, clusters = fix.migrate_doc(doc, STEM)
    assert edges == 1
    assert clusters == 0  # already defined
    assert len(doc["@graph"]) == 2  # no node appended
    assert doc["@graph"][0]["estleg:requestedCluster"]["@id"] == minted


def test_migrate_doc_handles_list_valued_cluster():
    doc = {
        "@graph": [
            {
                "@id": "estleg:P1",
                "estleg:requestedCluster": [
                    {"@id": "avalik kord"},
                    {"@id": "estleg:Cluster_PublicOrder"},  # already fine
                ],
            }
        ]
    }
    edges, clusters = fix.migrate_doc(doc, STEM)
    assert edges == 1  # only the bare one
    assert clusters == 1
    targets = {it["@id"] for it in doc["@graph"][0]["estleg:requestedCluster"]}
    assert "estleg:Cluster_avalik_kord_" + STEM in targets
    assert "estleg:Cluster_PublicOrder" in targets


def test_idempotent_on_already_clean_doc():
    doc = {
        "@graph": [
            {"@id": "estleg:P1", "estleg:requestedCluster": {"@id": "müük"}},
        ]
    }
    fix.migrate_doc(doc, STEM)
    # Second pass: everything is now a CURIE + defined -> no-op.
    edges2, clusters2 = fix.migrate_doc(doc, STEM)
    assert edges2 == 0
    assert clusters2 == 0


def test_no_cross_file_collision_when_same_label_in_two_files():
    # Two separate docs (files) referencing the SAME bare label must end up with
    # DISTINCT minted @ids — the exact #564 regression.
    doc_a = {"@graph": [{"@id": "estleg:A1",
                         "estleg:requestedCluster": {"@id": "müük"}}]}
    doc_b = {"@graph": [{"@id": "estleg:B1",
                         "estleg:requestedCluster": {"@id": "müük"}}]}
    fix.migrate_doc(doc_a, "alkoholi_seadus_peep")
    fix.migrate_doc(doc_b, "tubaka_seadus_peep")
    id_a = doc_a["@graph"][-1]["@id"]
    id_b = doc_b["@graph"][-1]["@id"]
    assert id_a != id_b


def test_process_file_writes_atomically(tmp_path: Path):
    p = tmp_path / "demo_peep.json"
    p.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:P1",
                        "estleg:requestedCluster": {"@id": "data exchange"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    edges, clusters = fix.process_file(p, dry_run=False)
    assert edges == 1
    assert clusters == 1
    reloaded = json.loads(p.read_text(encoding="utf-8"))
    ids = {n["@id"] for n in reloaded["@graph"]}
    # File stem ("demo_peep") namespaces the minted IRI.
    assert "estleg:Cluster_data_exchange_demo_peep" in ids


def test_dry_run_does_not_write(tmp_path: Path):
    p = tmp_path / "demo_peep.json"
    original = json.dumps(
        {
            "@graph": [
                {
                    "@id": "estleg:P1",
                    "estleg:requestedCluster": {"@id": "data exchange"},
                }
            ]
        }
    )
    p.write_text(original, encoding="utf-8")
    edges, _ = fix.process_file(p, dry_run=True)
    assert edges == 1
    # File on disk is unchanged.
    assert p.read_text(encoding="utf-8") == original


def test_lfs_pointer_is_skipped(tmp_path: Path):
    p = tmp_path / "pointer_peep.json"
    p.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n",
        encoding="utf-8",
    )
    assert fix._is_lfs_pointer(p) is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
