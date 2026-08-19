"""Tests for the OWL-module owl:sameAs bridge (#565).

Exercises the fragment-extraction and canonical-mapping helpers on synthetic
inputs (full-IRI and compact @id forms), the TsÜS Õ-vs-ASCII spelling
resolution, the irregular-fragment skip, idempotent re-adds, and the
``link_module`` orchestration over a synthetic module + canonical set.
"""

import json

from estleg import link_owl_modules_565 as L


def _module_doc(section_ids: list[str]) -> dict:
    """A minimal module document with the given Section @ids plus a non-Section."""
    graph: list[dict] = [
        {"@id": sid, "@type": ["estleg:Section", "owl:NamedIndividual"]}
        for sid in section_ids
    ]
    graph.append({"@id": "https://w3id.org/estleg/Section", "@type": ["owl:Class"]})
    return {"@context": {"estleg": L.NS}, "@graph": graph}


# --------------------------------------------------------------------------- #
# local_fragment
# --------------------------------------------------------------------------- #
def test_local_fragment_full_iri():
    assert L.local_fragment(f"{L.NS}KarS_Par_88") == "KarS_Par_88"


def test_local_fragment_compact():
    assert L.local_fragment("estleg:TsUS_Par_138") == "TsUS_Par_138"


def test_local_fragment_non_estleg_is_none():
    assert L.local_fragment("http://eurovoc.europa.eu/2411") is None
    assert L.local_fragment(None) is None
    assert L.local_fragment(42) is None


# --------------------------------------------------------------------------- #
# canonical_candidates / map_to_canonical
# --------------------------------------------------------------------------- #
def test_canonical_candidates_kars():
    assert L.canonical_candidates("KarS_Par_88") == ["KARIST_2_Osa2_Par_88"]


def test_canonical_candidates_tsus_tries_both_spellings():
    # ASCII first (#445); legacy Õ spelling is the fallback.
    assert L.canonical_candidates("TsUS_Par_138") == [
        "TsUS_Osa7_Par_138",
        "TsÜS_Osa7_Par_138",
    ]


def test_canonical_candidates_irregular_is_empty():
    # The hand-built modules' Par<N>_<M> enumeration nodes have no transform.
    assert L.canonical_candidates("Par145_1") == []
    assert L.canonical_candidates("Par93_2") == []


def test_map_to_canonical_kars():
    canonical = {"KARIST_2_Osa2_Par_88", "KARIST_2_Osa2_Par_90"}
    assert L.map_to_canonical("KarS_Par_88", canonical) == "KARIST_2_Osa2_Par_88"


def test_map_to_canonical_tsus_prefers_ascii_spelling():
    # When both spellings are present, the ASCII form wins (#445).
    canonical = {"TsÜS_Osa7_Par_138", "TsUS_Osa7_Par_138"}
    assert L.map_to_canonical("TsUS_Par_138", canonical) == "TsUS_Osa7_Par_138"


def test_map_to_canonical_tsus_ascii_fallback():
    # Only the ASCII spelling exists -> it is selected.
    canonical = {"TsUS_Osa7_Par_140"}
    assert L.map_to_canonical("TsUS_Par_140", canonical) == "TsUS_Osa7_Par_140"


def test_map_to_canonical_unmatched_returns_none():
    # Section number absent from the canonical set -> skipped.
    canonical = {"KARIST_2_Osa2_Par_88"}
    assert L.map_to_canonical("KarS_Par_999", canonical) is None
    assert L.map_to_canonical("Par145_1", canonical) is None


# --------------------------------------------------------------------------- #
# add_same_as idempotency
# --------------------------------------------------------------------------- #
def test_add_same_as_adds_then_is_idempotent():
    node = {"@id": "estleg:KarS_Par_88", "@type": ["estleg:Section"]}
    target = {"@id": "estleg:KARIST_2_Osa2_Par_88"}
    assert L.add_same_as(node, target) is True
    assert node["owl:sameAs"] == {"@id": "estleg:KARIST_2_Osa2_Par_88"}
    # Re-add of the same target is a no-op and does NOT turn it into a list.
    assert L.add_same_as(node, dict(target)) is False
    assert node["owl:sameAs"] == {"@id": "estleg:KARIST_2_Osa2_Par_88"}


def test_add_same_as_preserves_distinct_existing_edge():
    node = {"@id": "x", "owl:sameAs": {"@id": "estleg:Other"}}
    assert L.add_same_as(node, {"@id": "estleg:KARIST_2_Osa2_Par_88"}) is True
    ids = {item["@id"] for item in node["owl:sameAs"]}
    assert ids == {"estleg:Other", "estleg:KARIST_2_Osa2_Par_88"}
    # And re-adding either is still a no-op.
    assert L.add_same_as(node, {"@id": "estleg:KARIST_2_Osa2_Par_88"}) is False


# --------------------------------------------------------------------------- #
# link_module orchestration
# --------------------------------------------------------------------------- #
def test_link_module_full_and_compact_and_unmatched(tmp_path):
    doc = _module_doc(
        [
            f"{L.NS}KarS_Par_88",   # full IRI form, maps
            "estleg:KarS_Par_90",   # compact form, maps
            f"{L.NS}Par93_2",       # irregular fragment, unmatched
        ]
    )
    path = tmp_path / "mod.jsonld"
    path.write_text(json.dumps(doc), encoding="utf-8")
    canonical = {"KARIST_2_Osa2_Par_88", "KARIST_2_Osa2_Par_90"}

    out, stats = L.link_module(path, canonical)

    assert stats["sections_scanned"] == 3
    assert stats["sameas_added"] == 2
    assert stats["already_linked"] == 0
    assert stats["unmatched"] == 1
    assert stats["unmatched_sample"] == ["Par93_2"]

    by_id = {n["@id"]: n for n in out["@graph"]}
    # owl:sameAs written as the compact estleg: form regardless of module @id form.
    assert by_id[f"{L.NS}KarS_Par_88"]["owl:sameAs"] == {"@id": "estleg:KARIST_2_Osa2_Par_88"}
    assert by_id["estleg:KarS_Par_90"]["owl:sameAs"] == {"@id": "estleg:KARIST_2_Osa2_Par_90"}
    assert "owl:sameAs" not in by_id[f"{L.NS}Par93_2"]


def test_link_module_is_idempotent(tmp_path):
    doc = _module_doc([f"{L.NS}TsUS_Par_138"])
    path = tmp_path / "mod.jsonld"
    path.write_text(json.dumps(doc), encoding="utf-8")
    canonical = {"TsUS_Osa7_Par_138"}

    out, first = L.link_module(path, canonical)
    assert first["sameas_added"] == 1
    assert out["@graph"][0]["owl:sameAs"] == {"@id": "estleg:TsUS_Osa7_Par_138"}

    # Persist and re-run: a no-op diff.
    path.write_text(json.dumps(out), encoding="utf-8")
    out2, second = L.link_module(path, canonical)
    assert second["sameas_added"] == 0
    assert second["already_linked"] == 1
    assert out2["@graph"][0]["owl:sameAs"] == {"@id": "estleg:TsUS_Osa7_Par_138"}


def test_link_module_skips_lfs_pointer(tmp_path):
    path = tmp_path / "mod.jsonld"
    path.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:deadbeef\nsize 123\n",
        encoding="utf-8",
    )
    doc, stats = L.link_module(path, {"KARIST_2_Osa2_Par_88"})
    assert doc is None
    assert stats["sections_scanned"] == 0


def test_build_canonical_fragments_collects_par_only(tmp_path):
    peep = tmp_path / "x_peep.json"
    peep.write_text(
        json.dumps(
            {
                "@graph": [
                    {"@id": "estleg:KARIST_2_Osa2_Par_88", "@type": ["estleg:Section"]},
                    {"@id": f"{L.NS}KARIST_2_Osa2_Par_88_Lg_1", "@type": ["estleg:Subsection"]},
                    {"@id": "estleg:KARIST_2_Osa2", "@type": ["estleg:LegalPart"]},  # no _Par_
                ]
            }
        ),
        encoding="utf-8",
    )
    frags = L.build_canonical_fragments(tmp_path)
    assert "KARIST_2_Osa2_Par_88" in frags
    assert "KARIST_2_Osa2_Par_88_Lg_1" in frags
    assert "KARIST_2_Osa2" not in frags
