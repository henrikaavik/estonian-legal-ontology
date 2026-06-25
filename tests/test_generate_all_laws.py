"""Regression tests for scripts/generate_all_laws.py.

Covers issues #156, #165, #166, #132. Each test class has a comment that
explains which fix it locks down.
"""

from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET

import pytest

import generate_all_laws

_SHAPES_PATH = (
    generate_all_laws.REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"
)


def _shacl_conforms(graph_json: dict) -> tuple[bool, str]:
    """Validate a hand-built JSON-LD ``@graph`` against the shapes file.

    Skips the test (``importorskip``) when pyshacl/rdflib are not
    installed in the environment.
    """
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")
    data_graph = rdflib.Graph()
    data_graph.parse(data=json.dumps(graph_json), format="json-ld")
    shapes_graph = rdflib.Graph().parse(str(_SHAPES_PATH), format="turtle")
    conforms, _, msg = pyshacl.validate(
        data_graph, shacl_graph=shapes_graph, inference="rdfs"
    )
    return conforms, str(msg)


# ---------------------------------------------------------------------------
# Existing baseline tests (kept for backwards compatibility)
# ---------------------------------------------------------------------------


def test_has_existing_output_uses_exact_slug_not_short_prefix():
    existing = {"abcdefghijabcdefghij_other_law"}

    assert not generate_all_laws.has_existing_output(
        existing,
        "Different law",
        "abcdefghijabcdefghij_real_law",
    )


def test_law_stub_preserves_no_body_act():
    generate_all_laws._used_prefixes.clear()
    root = ET.fromstring("<akt><sisu /></akt>")

    doc = generate_all_laws.generate_law_stub_jsonld(
        "Rahvusvahelise lepingu ratifitseerimise seadus",
        "rahvusvahelise_lepingu_ratifitseerimise_seadus",
        root,
        "RLRS",
        "/akt/123.xml",
    )

    ontology = doc["@graph"][0]
    assert ontology["estleg:contentStatus"] == "noStructuredBody"
    assert "estleg:Act" in ontology["@type"]
    assert "estleg:Law" in ontology["@type"]
    assert ontology["dcterms:source"]["@id"].endswith("/akt/123.xml")


def test_merge_existing_enrichments_skips_stale_requested_cluster(tmp_path):
    existing_path = tmp_path / "law_peep.json"
    existing_path.write_text(
        json.dumps({
            "@graph": [
                {
                    "@id": "estleg:TEST_Par_1",
                    "estleg:requestedCluster": {
                        "@id": "estleg:Cluster_TEST_Old"
                    },
                    "estleg:normativeType": "kohustus",
                }
            ]
        }),
        encoding="utf-8",
    )
    new_doc = {"@graph": [{"@id": "estleg:TEST_Par_1"}]}

    merged = generate_all_laws.merge_existing_enrichments(new_doc, existing_path)
    node = merged["@graph"][0]

    assert "estleg:requestedCluster" not in node
    assert node["estleg:normativeType"] == "kohustus"


class _FailingResponse:
    def raise_for_status(self):
        raise RuntimeError("boom")


def test_get_all_laws_raises_on_source_list_failure(monkeypatch):
    monkeypatch.setattr(
        generate_all_laws.requests,
        "get",
        lambda *args, **kwargs: _FailingResponse(),
    )

    with pytest.raises(generate_all_laws.SourceListFetchError):
        generate_all_laws.get_all_laws()


# ---------------------------------------------------------------------------
# Issue #156 / #165 fix 1 + 7 — PrefixAllocator order independence
# ---------------------------------------------------------------------------


class TestPrefixAllocatorOrderIndependence:
    """A PrefixAllocator must return the same prefix for a given
    (slug, title) pair regardless of when it is allocated relative to
    other laws. Multi-claimant abbreviations must be in the registry.
    """

    def test_allocation_order_does_not_affect_winner(self):
        registry = {
            "abc_seadus": {"abbrev": "ABCS", "title": "Abc seadus"},
            "xyz_seadus": {"abbrev": "XYZS", "title": "Xyz seadus"},
        }
        a = generate_all_laws.PrefixAllocator(registry=registry)
        b = generate_all_laws.PrefixAllocator(registry=registry)

        prefix_a1 = a.allocate("ABCS", "abc_seadus", "Abc seadus")
        prefix_a2 = a.allocate("XYZS", "xyz_seadus", "Xyz seadus")

        prefix_b2 = b.allocate("XYZS", "xyz_seadus", "Xyz seadus")
        prefix_b1 = b.allocate("ABCS", "abc_seadus", "Abc seadus")

        assert prefix_a1 == prefix_b1 == "ABCS"
        assert prefix_a2 == prefix_b2 == "XYZS"

    def test_collision_without_registry_raises(self):
        """When two laws share an abbreviation AND every slug-prefix
        variant collides with the same prior owner, allocation must
        raise rather than silently appending a positional suffix.
        """
        alloc = generate_all_laws.PrefixAllocator(registry={})
        slug = "the_second_law_with_a_long_slug_for_collision_purposes"
        alloc._used_prefixes["XSAME"] = "First"
        for length in (40, 50, 60, 70, 80, len(slug)):
            attempt = generate_all_laws.sanitize_id(slug[:length])
            alloc._used_prefixes[attempt] = "First"

        with pytest.raises(generate_all_laws.PrefixCollisionError):
            alloc.allocate("XSAME", slug, "Second")

    def test_registry_collision_raises(self):
        """If the registry says two slugs share a prefix, the second
        allocation must raise — registry corruption, not a silent
        positional disambiguation.
        """
        registry = {
            "law_one": {"abbrev": "DUP", "title": "One"},
            "law_two": {"abbrev": "DUP", "title": "Two"},
        }
        alloc = generate_all_laws.PrefixAllocator(registry=registry)
        alloc.allocate("", "law_one", "One")
        with pytest.raises(generate_all_laws.PrefixCollisionError):
            alloc.allocate("", "law_two", "Two")

    def test_non_registry_law_cannot_claim_reserved_registry_abbrev(self):
        registered_title = "Registered long treaty"
        other_title = "Other long treaty"
        registry = {
            "shared_long_treaty_slug": {
                "abbrev": "RESERVED",
                "title": registered_title,
            },
        }
        alloc = generate_all_laws.PrefixAllocator(registry=registry)

        other_prefix = alloc.allocate(
            "RESERVED", "shared_long_treaty_slug_2", other_title
        )
        registered_prefix = alloc.allocate(
            "", "shared_long_treaty_slug", registered_title
        )

        assert other_prefix != "RESERVED"
        assert registered_prefix == "RESERVED"

    def test_duplicate_title_slugs_keep_registry_owner_on_base_slug(self):
        base = "a" * 80
        registered_title = f"{base} registered"
        other_title = f"{base} other"
        registry = {
            base: {
                "abbrev": "ALONG",
                "title": registered_title,
            },
        }

        slugs = generate_all_laws.build_law_slug_map(
            {other_title: {}, registered_title: {}},
            registry=registry,
        )

        assert slugs[registered_title] == base
        assert slugs[other_title] == f"{base}_2"

    def test_module_level_proxy_clear_resets_default_allocator(self):
        """Legacy ``generate_all_laws._used_prefixes.clear()`` must
        forward to ``_default_allocator.reset()`` so existing tests
        that flush state between cases keep working."""
        generate_all_laws._default_allocator.allocate(
            "MYTEST", "some_test_slug", "Some Test Law"
        )
        assert "MYTEST" in generate_all_laws._used_prefixes

        generate_all_laws._used_prefixes.clear()

        assert "MYTEST" not in generate_all_laws._used_prefixes
        assert generate_all_laws._default_allocator.used_prefixes == {}


# ---------------------------------------------------------------------------
# Issue #238 — deterministic, zero-churn slug collision suffixes
# (Option B: freeze committed slugs + gid-ordered assignment for new colliders)
# ---------------------------------------------------------------------------


class TestCollisionSuffixFreeze:
    """``build_law_slug_map`` collision suffixes must be deterministic and
    forward-stable: committed slugs are frozen verbatim, and brand-new
    colliders are ordered by ``(globaalId, title)`` rather than by list
    position, so a title rename/reorder never drifts another act's slug.
    """

    # Three titles whose 80-char slug stems are identical (a long treaty
    # base) but whose full titles and gids differ.
    _BASE_PHRASE = (
        "Eesti Vabariigi valitsuse ja teise riigi vaheline pikk leping "
        "investeeringute kaitse kohta "
    )

    def _colliding_titles(self) -> tuple[str, str, str]:
        t1 = self._BASE_PHRASE + "A esimene"
        t2 = self._BASE_PHRASE + "B teine"
        t3 = self._BASE_PHRASE + "C kolmas"
        # Sanity: all three collide on the same truncated base slug.
        assert (
            generate_all_laws.slugify(t1)
            == generate_all_laws.slugify(t2)
            == generate_all_laws.slugify(t3)
        )
        return t1, t2, t3

    def test_collision_suffixes_stable_across_reordered_inputs(self):
        """No freeze map: the smallest-gid title takes the bare base slug
        and the assignment is identical regardless of dict insertion order.
        """
        t1, t2, t3 = self._colliding_titles()
        base = generate_all_laws.slugify(t1)
        assert len(base) > 80 - 1  # long treaty stem

        # gids chosen so the smallest-as-string is t2 ("100..." < "27..." <
        # "9..."), proving string (not int) ordering drives the base slug.
        laws_a = {
            t1: {"gid": "27546"},
            t2: {"gid": "100200300400"},
            t3: {"gid": "9001"},
        }
        laws_b = {
            t3: {"gid": "9001"},
            t1: {"gid": "27546"},
            t2: {"gid": "100200300400"},
        }

        m1 = generate_all_laws.build_law_slug_map(laws_a, registry={})
        m2 = generate_all_laws.build_law_slug_map(laws_b, registry={})

        assert m1 == m2
        # String compare: "100200300400" < "27546" < "9001".
        assert m1[t2] == base
        assert {m1[t1], m1[t3]} == {f"{base}_2", f"{base}_3"}
        # Stable, distinct, non-base suffixes for the other two.
        assert m1[t1] != m1[t3]

    def test_existing_collision_assignments_are_frozen(self):
        """Two of three colliders carry committed slugs; those stay verbatim
        across reordered inputs, and the new third collider takes the next
        free suffix deterministically.
        """
        t1, t2, t3 = self._colliding_titles()
        base = generate_all_laws.slugify(t1)

        # Committed corpus assigned t1 -> base, t3 -> base_2 (whatever the
        # historical order produced). t2 is brand new this snapshot.
        freeze = {t1: base, t3: f"{base}_2"}

        laws_a = {
            t1: {"gid": "27546"},
            t2: {"gid": "100"},  # smallest gid, but must NOT steal the base
            t3: {"gid": "9001"},
        }
        laws_b = {
            t2: {"gid": "100"},
            t3: {"gid": "9001"},
            t1: {"gid": "27546"},
        }

        m1 = generate_all_laws.build_law_slug_map(
            laws_a, registry={}, existing_slug_by_title=freeze
        )
        m2 = generate_all_laws.build_law_slug_map(
            laws_b, registry={}, existing_slug_by_title=freeze
        )

        assert m1 == m2
        # Frozen titles keep their committed slug despite t2's smaller gid.
        assert m1[t1] == base
        assert m1[t3] == f"{base}_2"
        # The new collider gets the lowest UNUSED suffix (base and base_2 are
        # reserved -> base_3), and it is reorder-independent.
        assert m1[t2] == f"{base}_3"

    def test_collision_suffix_unaffected_by_title_amendment(self):
        """Same gids, but one title's text is amended between two calls (no
        freeze map). Each gid's slug must be unchanged — identity is the gid,
        not the raw title string.
        """
        t1, t2, t3 = self._colliding_titles()
        base = generate_all_laws.slugify(t1)

        laws_before = {
            t1: {"gid": "27546"},
            t2: {"gid": "100200300400"},
            t3: {"gid": "9001"},
        }
        m_before = generate_all_laws.build_law_slug_map(
            laws_before, registry={}
        )

        # Amend t3's title text while keeping its gid; it still collides on
        # the same base slug (tail differs only after the 80-char cutoff).
        t3_amended = self._BASE_PHRASE + "C kolmas (muudetud redaktsioon)"
        assert generate_all_laws.slugify(t3_amended) == base
        laws_after = {
            t1: {"gid": "27546"},
            t2: {"gid": "100200300400"},
            t3_amended: {"gid": "9001"},
        }
        m_after = generate_all_laws.build_law_slug_map(laws_after, registry={})

        # gid 100200300400 keeps the base slug; gid 27546 and gid 9001 keep
        # their respective suffixes across the amendment.
        assert m_before[t2] == m_after[t2] == base
        assert m_before[t1] == m_after[t1]
        assert m_before[t3] == m_after[t3_amended]

    def test_fresh_checkout_no_manifest_uses_gid_order(self):
        """``existing_slug_by_title=None`` (fresh checkout) is the default
        gid-ordered path: the smallest-gid collider takes the base slug.
        """
        t1, t2, t3 = self._colliding_titles()
        base = generate_all_laws.slugify(t1)
        laws = {
            t1: {"gid": "27546"},
            t2: {"gid": "100200300400"},
            t3: {"gid": "9001"},
        }
        m_default = generate_all_laws.build_law_slug_map(laws, registry={})
        m_explicit_none = generate_all_laws.build_law_slug_map(
            laws, registry={}, existing_slug_by_title=None
        )
        assert m_default == m_explicit_none
        assert m_default[t2] == base


# ---------------------------------------------------------------------------
# Issue #156 + #165 fix 2 — paragraph IRI suffix from ylaIndeks
# ---------------------------------------------------------------------------


class TestParagraphIdSuperscript:
    def test_ylaIndeks_appended_to_iri_suffix(self):
        """A paragraph element with ``ylaIndeks="1"`` must produce
        ``Par_22_1`` not ``Par_22_<positional>``.
        """
        xml = (
            "<paragrahv>"
            "<paragrahvNr ylaIndeks='1'>22</paragrahvNr>"
            "<kuvatavNr>S 22 sup1.</kuvatavNr>"
            "</paragrahv>"
        )
        el = ET.fromstring(xml)
        assert generate_all_laws._paragraph_id_suffix(el) == "22_1"

    def test_unicode_superscript_in_kuvatavNr_extracted(self):
        """Fallback: when ylaIndeks is missing but the kuvatavNr has a
        Unicode superscript glyph (¹/²), the suffix is still derived
        from it.
        """
        kuv = "S 22²."  # superscript 2 = U+00B2
        xml = (
            "<paragrahv>"
            "<paragrahvNr>22</paragrahvNr>"
            f"<kuvatavNr>{kuv}</kuvatavNr>"
            "</paragrahv>"
        )
        el = ET.fromstring(xml)
        assert generate_all_laws._paragraph_id_suffix(el) == "22_2"

    def test_no_superscript_returns_plain_number(self):
        xml = (
            "<paragrahv>"
            "<paragrahvNr>14</paragrahvNr>"
            "<kuvatavNr>S 14.</kuvatavNr>"
            "</paragrahv>"
        )
        el = ET.fromstring(xml)
        assert generate_all_laws._paragraph_id_suffix(el) == "14"

    def test_chapter_id_suffix_ylaIndeks(self):
        """Issue #253: a chapter ``6¹`` must yield suffix ``6_1`` mirroring
        the paragraph helper, not the bare ``6`` that collides with ``6``.
        """
        ch = ET.fromstring(
            "<peatykk><peatykkNr ylaIndeks='1'>6</peatykkNr>"
            "<peatykkPealkiri>Kuues prim</peatykkPealkiri></peatykk>"
        )
        assert generate_all_laws._chapter_id_suffix(ch, "6", "Kuues prim") == "6_1"

    def test_chapter_id_suffix_unicode_superscript_in_kuvatavnr(self):
        ch = ET.fromstring(
            "<peatykk><peatykkNr>6</peatykkNr>"
            "<kuvatavNr>6² peatükk</kuvatavNr>"
            "<peatykkPealkiri>Kuues</peatykkPealkiri></peatykk>"
        )
        assert generate_all_laws._chapter_id_suffix(ch, "6", "Kuues") == "6_2"

    def test_chapter_id_suffix_plain_number(self):
        ch = ET.fromstring(
            "<peatykk><peatykkNr>6</peatykkNr>"
            "<peatykkPealkiri>Kuues</peatykkPealkiri></peatykk>"
        )
        assert generate_all_laws._chapter_id_suffix(ch, "6", "Kuues") == "6"

    def test_chapter_id_suffix_title_fallback_when_no_number(self):
        ch = ET.fromstring(
            "<peatykk><peatykkPealkiri>Üldsätted</peatykkPealkiri></peatykk>"
        )
        # No number -> truncated, transliterated title suffix (unchanged behaviour).
        assert generate_all_laws._chapter_id_suffix(ch, "", "Üldsätted") == "Uldsatted"

    def test_division_id_suffix_ylaIndeks(self):
        jagu = ET.fromstring(
            "<jagu><jaguNr ylaIndeks='1'>3</jaguNr>"
            "<jaguPealkiri>Kolmas prim</jaguPealkiri></jagu>"
        )
        assert generate_all_laws._division_id_suffix(jagu, "3", "Kolmas prim") == "3_1"

    def test_duplicate_paragraphs_get_distinct_iris_in_full_law(self):
        """Round-trip: a law with two paragrahvs that share
        ``paragrahvNr=22`` (one plain, one with ``ylaIndeks=1``) must
        produce distinct ``Par_22`` and ``Par_22_1`` IRIs without raising.
        """
        xml = """
        <akt>
          <sisu>
            <peatykk>
              <peatykkNr>1</peatykkNr>
              <peatykkPealkiri>Peatykk</peatykkPealkiri>
              <paragrahv>
                <paragrahvNr>22</paragrahvNr>
                <kuvatavNr>S 22.</kuvatavNr>
                <paragrahvPealkiri>Esimene</paragrahvPealkiri>
                <loige><loigeNr>1</loigeNr><tavatekst>Eks ole.</tavatekst></loige>
              </paragrahv>
              <paragrahv>
                <paragrahvNr ylaIndeks="1">22</paragrahvNr>
                <kuvatavNr>S 22 sup1.</kuvatavNr>
                <paragrahvPealkiri>Teine</paragrahvPealkiri>
                <loige><loigeNr>1</loigeNr><tavatekst>Satestab teisiti.</tavatekst></loige>
              </paragrahv>
            </peatykk>
          </sisu>
        </akt>
        """
        root = ET.fromstring(xml)
        # Use an abbreviation > 3 chars so the allocator uses it directly.
        alloc = generate_all_laws.PrefixAllocator(registry={})
        doc = generate_all_laws.generate_law_jsonld(
            "Test seadus",
            "test_seadus",
            root,
            abbreviation="TSTAB",
            allocator=alloc,
        )
        ids = {
            n["@id"]
            for n in doc["@graph"]
            if "_Par_" in (n.get("@id") or "")
        }
        assert "estleg:TSTAB_Par_22" in ids
        assert "estleg:TSTAB_Par_22_1" in ids
        # And no positional disambiguation was used:
        assert not any(i.endswith("_22_22") for i in ids)

    def test_repeated_superscript_paragraphs_get_stable_duplicate_suffixes(self):
        """Some RT snapshots contain two identical paragrahvNr/ylaIndeks
        pairs. Keep both citable without reusing the same IRI.
        """
        xml = """
        <akt>
          <sisu>
            <paragrahv>
              <paragrahvNr ylaIndeks="1">26</paragrahvNr>
              <kuvatavNr>S 26.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Esimene tekst.</tavatekst></loige>
            </paragrahv>
            <paragrahv>
              <paragrahvNr ylaIndeks="1">26</paragrahvNr>
              <kuvatavNr>S 26 sup1.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Teine tekst.</tavatekst></loige>
            </paragrahv>
          </sisu>
        </akt>
        """
        root = ET.fromstring(xml)
        doc = generate_all_laws.generate_law_jsonld(
            "Korduv seadus",
            "korduv_seadus",
            root,
            abbreviation="KORD",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )

        graph = doc["@graph"]
        assert any(node.get("@id") == "estleg:KORD_Par_26_1" for node in graph)
        assert any(
            node.get("@id") == "estleg:KORD_Par_26_1_Dup2" for node in graph
        )
        assert any(
            node.get("@id") == "estleg:KORD_Par_26_1_Dup2_Lg_1"
            and node["estleg:parentProvision"] == {
                "@id": "estleg:KORD_Par_26_1_Dup2"
            }
            for node in graph
        )

    def test_superscript_chapters_get_distinct_non_dup_structural_ids(self):
        """Issue #253: chapter ``6`` and chapter ``6¹`` must yield distinct,
        superscript-aware structural ids (``_6`` vs ``_6_1``) — NOT the
        old ``_6`` + ``_6_Dup2`` band-aid — and each chapter's provisions
        must land in its OWN cluster (no orphan top concepts).
        """
        xml = """
        <akt>
          <sisu>
            <peatykk>
              <peatykkNr>6</peatykkNr>
              <peatykkPealkiri>Esimene peatükk</peatykkPealkiri>
              <paragrahv>
                <paragrahvNr>42</paragrahvNr>
                <kuvatavNr>S 42.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Esimene tekst.</tavatekst></loige>
              </paragrahv>
            </peatykk>
            <peatykk>
              <peatykkNr ylaIndeks="1">6</peatykkNr>
              <peatykkPealkiri>Teine peatükk</peatykkPealkiri>
              <paragrahv>
                <paragrahvNr ylaIndeks="1">42</paragrahvNr>
                <kuvatavNr>S 42 sup1.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Teine tekst.</tavatekst></loige>
              </paragrahv>
              <paragrahv>
                <paragrahvNr ylaIndeks="2">42</paragrahvNr>
                <kuvatavNr>S 42 sup2.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Kolmas tekst.</tavatekst></loige>
              </paragrahv>
            </peatykk>
          </sisu>
        </akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Korduv peatükk seadus",
            "korduv_peatukk_seadus",
            ET.fromstring(xml),
            abbreviation="KPSA",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        graph = doc["@graph"]

        ids = [node["@id"] for node in graph if "@id" in node]
        assert len(ids) == len(set(ids))
        id_set = set(ids)
        # Distinct superscript-aware ids, NO _Dup band-aid.
        assert "estleg:Cluster_KPSA_6" in id_set
        assert "estleg:Cluster_KPSA_6_1" in id_set
        assert "estleg:Chapter_KPSA_6" in id_set
        assert "estleg:Chapter_KPSA_6_1" in id_set
        assert not any("_Dup" in i for i in ids), [i for i in ids if "_Dup" in i]

        # Each chapter's provisions land in its OWN cluster.
        def _cluster_of(par_iri: str) -> str:
            node = next(n for n in graph if n.get("@id") == par_iri)
            return node["estleg:requestedCluster"]["@id"]

        assert _cluster_of("estleg:KPSA_Par_42") == "estleg:Cluster_KPSA_6"
        assert _cluster_of("estleg:KPSA_Par_42_1") == "estleg:Cluster_KPSA_6_1"
        assert _cluster_of("estleg:KPSA_Par_42_2") == "estleg:Cluster_KPSA_6_1"

        # provisionCount reflects the real assignment (1 vs 2), not int collapse.
        counts = {
            n["@id"]: n.get("estleg:provisionCount")
            for n in graph
            if n.get("@id", "").startswith("estleg:Cluster_KPSA_")
        }
        assert counts["estleg:Cluster_KPSA_6"] == 1
        assert counts["estleg:Cluster_KPSA_6_1"] == 2

        # No orphan top concepts: every scheme top concept is referenced by a
        # provision (directly, or via a part-level skos:narrower).
        scheme = next(
            n for n in graph if n.get("@type") == ["skos:ConceptScheme"]
        )
        referenced = {
            n["estleg:requestedCluster"]["@id"]
            for n in graph
            if isinstance(n.get("estleg:requestedCluster"), dict)
        }
        for top in scheme["skos:hasTopConcept"]:
            assert top["@id"] in referenced, f"orphan top concept {top['@id']}"

    def test_genuinely_duplicate_chapter_numbers_use_dedupe_last_resort(self):
        """When RT really repeats the same chapter number (no superscript,
        same osa), ``_dedupe_structural_id`` remains the last-resort
        disambiguator — both chapters stay citable and provisions resolve.
        """
        xml = """
        <akt>
          <sisu>
            <peatykk>
              <peatykkNr>6</peatykkNr>
              <peatykkPealkiri>Esimene peatükk</peatykkPealkiri>
              <paragrahv>
                <paragrahvNr>16</paragrahvNr>
                <kuvatavNr>S 16.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Esimene tekst.</tavatekst></loige>
              </paragrahv>
            </peatykk>
            <peatykk>
              <peatykkNr>6</peatykkNr>
              <peatykkPealkiri>Teine peatükk</peatykkPealkiri>
              <paragrahv>
                <paragrahvNr>17</paragrahvNr>
                <kuvatavNr>S 17.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Teine tekst.</tavatekst></loige>
              </paragrahv>
            </peatykk>
          </sisu>
        </akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Korduv peatükk seadus",
            "korduv_peatukk_seadus",
            ET.fromstring(xml),
            abbreviation="KPSA",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )

        ids = [node["@id"] for node in doc["@graph"] if "@id" in node]
        assert len(ids) == len(set(ids))
        id_set = set(ids)
        assert "estleg:Cluster_KPSA_6" in id_set
        assert "estleg:Cluster_KPSA_6_Dup2" in id_set
        # The two paragraphs resolve to the two distinct clusters.
        par16 = next(n for n in doc["@graph"] if n.get("@id") == "estleg:KPSA_Par_16")
        par17 = next(n for n in doc["@graph"] if n.get("@id") == "estleg:KPSA_Par_17")
        assert par16["estleg:requestedCluster"]["@id"] == "estleg:Cluster_KPSA_6"
        assert par17["estleg:requestedCluster"]["@id"] == "estleg:Cluster_KPSA_6_Dup2"
        for node in doc["@graph"]:
            ref = node.get("estleg:requestedCluster")
            if isinstance(ref, dict):
                assert ref["@id"] in id_set

    def test_same_chapter_number_across_osa_in_single_file_does_not_collide(self):
        """Issue #253: a non-MULTIPART law with >1 osa flows through the
        single-file path. The same ``peatykkNr`` in different osa must be
        namespaced by osa rather than colliding onto ``_Dup``.
        """
        xml = """
        <akt>
          <sisu>
            <osa>
              <osaNr>1</osaNr>
              <osaPealkiri>Esimene osa</osaPealkiri>
              <peatykk>
                <peatykkNr>1</peatykkNr>
                <peatykkPealkiri>Osa 1 peatükk 1</peatykkPealkiri>
                <paragrahv>
                  <paragrahvNr>1</paragrahvNr>
                  <kuvatavNr>S 1.</kuvatavNr>
                  <loige><loigeNr>1</loigeNr><tavatekst>Esimene tekst.</tavatekst></loige>
                </paragrahv>
              </peatykk>
            </osa>
            <osa>
              <osaNr>2</osaNr>
              <osaPealkiri>Teine osa</osaPealkiri>
              <peatykk>
                <peatykkNr>1</peatykkNr>
                <peatykkPealkiri>Osa 2 peatükk 1</peatykkPealkiri>
                <paragrahv>
                  <paragrahvNr>9</paragrahvNr>
                  <kuvatavNr>S 9.</kuvatavNr>
                  <loige><loigeNr>1</loigeNr><tavatekst>Teine tekst.</tavatekst></loige>
                </paragrahv>
              </peatykk>
            </osa>
          </sisu>
        </akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Kahe osaga seadus",
            "kahe_osaga_seadus",
            ET.fromstring(xml),
            abbreviation="OSAX",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        graph = doc["@graph"]
        ids = [node["@id"] for node in graph if "@id" in node]
        assert len(ids) == len(set(ids))
        assert not any("_Dup" in i for i in ids), [i for i in ids if "_Dup" in i]
        id_set = set(ids)
        # Osa-namespaced, distinct clusters for the same peatykkNr.
        assert "estleg:Cluster_OSAX_Osa1_1" in id_set
        assert "estleg:Cluster_OSAX_Osa2_1" in id_set
        par1 = next(n for n in graph if n.get("@id") == "estleg:OSAX_Par_1")
        par9 = next(n for n in graph if n.get("@id") == "estleg:OSAX_Par_9")
        assert par1["estleg:requestedCluster"]["@id"] == "estleg:Cluster_OSAX_Osa1_1"
        assert par9["estleg:requestedCluster"]["@id"] == "estleg:Cluster_OSAX_Osa2_1"


class TestProvisionSummaryFallback:
    def test_title_only_full_law_paragraph_gets_summary(self):
        xml = """
        <akt><sisu>
          <paragrahv>
            <paragrahvNr>1</paragrahvNr>
            <kuvatavNr>§ 1.</kuvatavNr>
            <paragrahvPealkiri>Rakendussäte</paragrahvPealkiri>
          </paragrahv>
        </sisu></akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Kokkuvõtte test",
            "kokkuvotte_test",
            ET.fromstring(xml),
            abbreviation="KTEST",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )

        provision = next(
            node for node in doc["@graph"]
            if node.get("@id") == "estleg:KTEST_Par_1"
        )
        assert provision["estleg:summary"] == "§ 1. Rakendussäte"
        assert "estleg:legalText" not in provision

    def test_title_only_multipart_paragraph_gets_summary(self):
        xml = """
        <akt><sisu>
          <osa><osaNr>1</osaNr><osaPealkiri>Üldosa</osaPealkiri>
            <paragrahv>
              <paragrahvNr>2</paragrahvNr>
              <kuvatavNr>§ 2.</kuvatavNr>
              <paragrahvPealkiri>Mõiste</paragrahvPealkiri>
            </paragrahv>
          </osa>
        </sisu></akt>
        """
        results = generate_all_laws.generate_multipart_law(
            "Kokkuvõtte multi",
            "kokkuvotte_multi",
            ET.fromstring(xml),
            abbreviation="KMULT",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        by_file = dict(results)

        provision = next(
            node for node in by_file["kokkuvotte_multi_osa1_peep.json"]["@graph"]
            if node.get("@id") == "estleg:KMULT_Osa1_Par_2"
        )
        assert provision["estleg:summary"] == "§ 2. Mõiste"
        assert "estleg:legalText" not in provision


# ---------------------------------------------------------------------------
# Issue #166 — paragrahvs in nested chapters mis-attributed
# ---------------------------------------------------------------------------


class TestNestedChapterAttribution:
    def test_paragraph_under_inner_jagu_attributes_to_division_only(self):
        """Build osa -> peatykk -> jagu -> paragrahv. The paragraph must
        link to the Division IRI of the inner jagu, NOT to the outer
        peatykk's Chapter IRI.
        """
        xml = """
        <akt>
          <sisu>
            <osa>
              <osaNr>1</osaNr>
              <osaPealkiri>Outer osa</osaPealkiri>
              <peatykk>
                <peatykkNr>1</peatykkNr>
                <peatykkPealkiri>Outer chapter</peatykkPealkiri>
                <jagu>
                  <jaguNr>1</jaguNr>
                  <jaguPealkiri>Inner jagu</jaguPealkiri>
                  <paragrahv>
                    <paragrahvNr>5</paragrahvNr>
                    <kuvatavNr>S 5.</kuvatavNr>
                    <paragrahvPealkiri>Five</paragrahvPealkiri>
                    <loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>
                  </paragrahv>
                </jagu>
              </peatykk>
            </osa>
            <osa>
              <osaNr>2</osaNr>
              <osaPealkiri>Other osa</osaPealkiri>
              <peatykk>
                <peatykkNr>1</peatykkNr>
                <peatykkPealkiri>Other chapter</peatykkPealkiri>
                <paragrahv>
                  <paragrahvNr>9</paragrahvNr>
                  <kuvatavNr>S 9.</kuvatavNr>
                  <paragrahvPealkiri>Nine</paragrahvPealkiri>
                  <loige><loigeNr>1</loigeNr><tavatekst>Muu.</tavatekst></loige>
                </paragrahv>
              </peatykk>
            </osa>
          </sisu>
        </akt>
        """
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        results = generate_all_laws.generate_multipart_law(
            "Test multi", "test_multi", root, abbreviation="TM", allocator=alloc,
        )
        assert len(results) == 2
        graph_osa1 = results[0][1]["@graph"]
        par5 = next(n for n in graph_osa1 if n.get("@id", "").endswith("Par_5"))
        container = par5.get("estleg:isPartOf", {}).get("@id", "")
        assert "Division_" in container, (
            f"par 5 should be in a Division, got {container!r}"
        )

    def test_paragraph_in_jagu_attributes_only_to_inner_jagu(self):
        """Real-world structure: peatykk -> jagu1, jagu2 with a
        paragraph in each. par 5 must end up in jagu1's Division and
        par 7 in jagu2's — not cross-contaminated by the par_to_container
        race.
        """
        xml = """
        <akt>
          <sisu>
            <peatykk>
              <peatykkNr>1</peatykkNr>
              <peatykkPealkiri>Yks peatykk</peatykkPealkiri>
              <jagu>
                <jaguNr>1</jaguNr>
                <jaguPealkiri>Esimene jagu</jaguPealkiri>
                <paragrahv>
                  <paragrahvNr>5</paragrahvNr>
                  <kuvatavNr>S 5.</kuvatavNr>
                  <paragrahvPealkiri>Five</paragrahvPealkiri>
                  <loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>
                </paragrahv>
              </jagu>
              <jagu>
                <jaguNr>2</jaguNr>
                <jaguPealkiri>Teine jagu</jaguPealkiri>
                <paragrahv>
                  <paragrahvNr>7</paragrahvNr>
                  <kuvatavNr>S 7.</kuvatavNr>
                  <paragrahvPealkiri>Seven</paragrahvPealkiri>
                  <loige><loigeNr>1</loigeNr><tavatekst>Muu.</tavatekst></loige>
                </paragrahv>
              </jagu>
            </peatykk>
          </sisu>
        </akt>
        """
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        doc = generate_all_laws.generate_law_jsonld(
            "Test", "test_nest", root, abbreviation="TNXX", allocator=alloc,
        )

        div1 = next(n for n in doc["@graph"]
                    if n.get("@id", "").startswith("estleg:Division_TNXX_")
                    and "Esimene" in n.get("rdfs:label", ""))
        div2 = next(n for n in doc["@graph"]
                    if n.get("@id", "").startswith("estleg:Division_TNXX_")
                    and "Teine" in n.get("rdfs:label", ""))

        par5 = next(n for n in doc["@graph"]
                    if n.get("@id", "").endswith("Par_5"))
        par7 = next(n for n in doc["@graph"]
                    if n.get("@id", "").endswith("Par_7"))
        assert par5["estleg:isPartOf"]["@id"] == div1["@id"]
        assert par7["estleg:isPartOf"]["@id"] == div2["@id"]

        # Issue #253: jagu paragraphs still inherit the CHAPTER's cluster
        # (the division is only their isPartOf container).
        chapter = next(n for n in doc["@graph"]
                       if n.get("@id", "").startswith("estleg:Chapter_TNXX_"))
        cluster_id = chapter["owl:sameAs"]["@id"]
        assert par5["estleg:requestedCluster"]["@id"] == cluster_id
        assert par7["estleg:requestedCluster"]["@id"] == cluster_id

        # Every paragraph maps to exactly one container.
        par_iris = [n.get("@id") for n in doc["@graph"]
                    if "_Par_" in (n.get("@id") or "")]
        assert len(par_iris) == len(set(par_iris))

    def test_chapter_whose_paragraphs_all_live_in_divisions_is_not_orphaned(self):
        """Issue #253 regression: when EVERY paragraph of a chapter sits
        inside a jagu, the chapter's cluster used to receive no provision
        references (provisionCount=0, orphaned top concept). The jagu
        paragraphs must inherit the chapter cluster.
        """
        xml = """
        <akt>
          <sisu>
            <peatykk>
              <peatykkNr>4</peatykkNr>
              <peatykkPealkiri>Meetmed</peatykkPealkiri>
              <jagu>
                <jaguNr>1</jaguNr>
                <jaguPealkiri>Esimene jagu</jaguPealkiri>
                <paragrahv>
                  <paragrahvNr>20</paragrahvNr>
                  <kuvatavNr>S 20.</kuvatavNr>
                  <loige><loigeNr>1</loigeNr><tavatekst>Esimene säte.</tavatekst></loige>
                </paragrahv>
                <paragrahv>
                  <paragrahvNr>21</paragrahvNr>
                  <kuvatavNr>S 21.</kuvatavNr>
                  <loige><loigeNr>1</loigeNr><tavatekst>Teine säte.</tavatekst></loige>
                </paragrahv>
              </jagu>
            </peatykk>
          </sisu>
        </akt>
        """
        generate_all_laws._used_prefixes.clear()
        doc = generate_all_laws.generate_law_jsonld(
            "Jaoga seadus",
            "jaoga_seadus",
            ET.fromstring(xml),
            abbreviation="JAGU",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        graph = doc["@graph"]
        cluster = next(
            n for n in graph if n.get("@id") == "estleg:Cluster_JAGU_4"
        )
        assert cluster["estleg:provisionCount"] == 2

        par20 = next(n for n in graph if n.get("@id") == "estleg:JAGU_Par_20")
        par21 = next(n for n in graph if n.get("@id") == "estleg:JAGU_Par_21")
        # Cluster = the chapter's; container = the division's.
        assert par20["estleg:requestedCluster"]["@id"] == "estleg:Cluster_JAGU_4"
        assert par21["estleg:requestedCluster"]["@id"] == "estleg:Cluster_JAGU_4"
        assert par20["estleg:isPartOf"]["@id"].startswith("estleg:Division_JAGU_")

        # No orphan top concepts.
        scheme = next(
            n for n in graph if n.get("@type") == ["skos:ConceptScheme"]
        )
        referenced = {
            n["estleg:requestedCluster"]["@id"]
            for n in graph
            if isinstance(n.get("estleg:requestedCluster"), dict)
        }
        for top in scheme["skos:hasTopConcept"]:
            assert top["@id"] in referenced


# ---------------------------------------------------------------------------
# Issue #165 fix 5 — generate_law_stub_jsonld must not crash on missing rt_url
# ---------------------------------------------------------------------------


class TestStubRtUrlGuard:
    @pytest.mark.parametrize("rt_url", [None, ""])
    def test_stub_handles_missing_rt_url(self, rt_url):
        generate_all_laws._used_prefixes.clear()
        root = ET.fromstring("<akt><sisu /></akt>")

        doc = generate_all_laws.generate_law_stub_jsonld(
            "Some treaty",
            "some_treaty_slug",
            root,
            abbreviation="ST",
            rt_url=rt_url,
        )
        ont = doc["@graph"][0]
        assert "dcterms:source" not in ont
        assert "owl:sameAs" not in ont


# ---------------------------------------------------------------------------
# Issue #165 fix 6 — collect_full_text preserves loige boundaries
# ---------------------------------------------------------------------------


class TestCollectFullTextLoigeBoundaries:
    def test_loige_boundaries_emit_paren_n_marker(self):
        xml = """
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <kuvatavNr>S 1.</kuvatavNr>
          <loige>
            <loigeNr>1</loigeNr>
            <kuvatavNr>(1)</kuvatavNr>
            <tavatekst>Esimene tekst, esimene loige.</tavatekst>
          </loige>
          <loige>
            <loigeNr>2</loigeNr>
            <kuvatavNr>(2)</kuvatavNr>
            <tavatekst>Teine loige paneb piiri.</tavatekst>
          </loige>
          <loige>
            <loigeNr>3</loigeNr>
            <kuvatavNr>(3)</kuvatavNr>
            <tavatekst>Kolmas loige.</tavatekst>
          </loige>
        </paragrahv>
        """
        el = ET.fromstring(xml)
        text = generate_all_laws.collect_full_text(el)

        # Round-trip: regex-locate "(2) ..."
        m = re.search(r"\(2\)\s+([^()]+?)(?=\s*\(\d+\)|$)", text)
        assert m, f"Did not find loige (2) marker in: {text!r}"
        assert "Teine loige" in m.group(1)
        assert "(1)" in text
        assert "(2)" in text
        assert "(3)" in text

    def test_no_loige_falls_back_to_concatenation(self):
        xml = """
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <tavatekst>Yks lause vaid.</tavatekst>
        </paragrahv>
        """
        el = ET.fromstring(xml)
        text = generate_all_laws.collect_full_text(el)
        assert "Yks lause vaid" in text


# ---------------------------------------------------------------------------
# Issue #255 — amendment-marker metadata must not leak into legalText/summary
# ---------------------------------------------------------------------------


class TestAmendmentMarkerExclusion:
    """All three text extractors must prune ``muutmismarge`` /
    ``avaldamismarge`` / ``joustumismarge`` subtrees so RT editorial
    metadata never reaches ``estleg:legalText`` / ``estleg:summary``.
    """

    _MARKER_NOISE = ("Kehtetu", "jõustumine muudetud", "RT I,", "paberkandjal")

    def _assert_clean(self, text: str, *, must_contain: str) -> None:
        assert must_contain in text, f"lost real body in: {text!r}"
        for noise in self._MARKER_NOISE:
            assert noise not in text, f"marker {noise!r} leaked into: {text!r}"

    def test_collect_full_text_drops_marker_sibling_of_loige(self):
        # noorsootoo_seadus §11 shape: paragrahv > muutmismarge > tavatekst.
        xml = """
        <paragrahv>
          <paragrahvNr>11</paragrahvNr>
          <kuvatavNr>S 11.</kuvatavNr>
          <muutmismarge>
            <tavatekst>Kehtetu -</tavatekst>
            <tavatekst>(jõustumine muudetud - RT I, 22.12.2013, 1)</tavatekst>
          </muutmismarge>
          <loige><loigeNr>1</loigeNr><tavatekst>Päris sisu siin.</tavatekst></loige>
        </paragrahv>
        """
        text = generate_all_laws.collect_full_text(ET.fromstring(xml))
        self._assert_clean(text, must_contain="Päris sisu siin.")

    def test_collect_full_text_fallback_drops_marker_only_paragraph(self):
        # A marker-only paragraph (no loige) must not emit the marker as body.
        xml = """
        <paragrahv>
          <paragrahvNr>11</paragrahvNr>
          <muutmismarge>
            <tavatekst>Kehtetu -</tavatekst>
            <tavatekst>(jõustumine muudetud - RT I, 22.12.2013, 1)</tavatekst>
          </muutmismarge>
        </paragrahv>
        """
        text = generate_all_laws.collect_full_text(ET.fromstring(xml))
        assert text == "", f"marker leaked via fallback: {text!r}"

    def test_collect_full_text_drops_marker_nested_inside_loige(self):
        xml = """
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <kuvatavNr>S 5.</kuvatavNr>
          <loige>
            <loigeNr>1</loigeNr>
            <tavatekst>Tegelik tekst kehtib.</tavatekst>
            <joustumismarge><tavatekst>(jõustumine muudetud - RT I, 01.01.2020, 5)</tavatekst></joustumismarge>
          </loige>
        </paragrahv>
        """
        text = generate_all_laws.collect_full_text(ET.fromstring(xml))
        self._assert_clean(text, must_contain="Tegelik tekst kehtib.")

    def test_collect_text_summary_drops_all_marker_kinds(self):
        xml = """
        <paragrahv>
          <paragrahvNr>2</paragrahvNr>
          <kuvatavNr>S 2.</kuvatavNr>
          <avaldamismarge><tavatekst>terviktekst RT paberkandjal</tavatekst></avaldamismarge>
          <loige><loigeNr>1</loigeNr><tavatekst>Õige sisu kehtib.</tavatekst></loige>
          <joustumismarge><lause>(jõustumine muudetud - RT I, 03.03.2019, 7)</lause></joustumismarge>
        </paragrahv>
        """
        text = generate_all_laws.collect_text(ET.fromstring(xml))
        self._assert_clean(text, must_contain="Õige sisu kehtib.")

    def test_loige_body_text_drops_nested_marker(self):
        lg = ET.fromstring(
            "<loige><loigeNr>1</loigeNr>"
            "<tavatekst>Säte kehtib edasi.</tavatekst>"
            "<muutmismarge><tavatekst>Kehtetu -</tavatekst>"
            "<tavatekst>(jõustumine muudetud - RT I, 1)</tavatekst></muutmismarge>"
            "</loige>"
        )
        body = generate_all_laws._loige_body_text(lg)
        self._assert_clean(body, must_contain="Säte kehtib edasi.")

    def test_marker_text_absent_from_emitted_provision_node(self):
        # End-to-end: the marker must not appear in legalText OR summary.
        xml = """
        <akt><sisu>
          <paragrahv>
            <paragrahvNr>11</paragrahvNr>
            <kuvatavNr>S 11.</kuvatavNr>
            <muutmismarge>
              <tavatekst>Kehtetu -</tavatekst>
              <tavatekst>(jõustumine muudetud - RT I, 22.12.2013, 1)</tavatekst>
            </muutmismarge>
            <loige><loigeNr>1</loigeNr><tavatekst>Sisuline säte.</tavatekst></loige>
          </paragrahv>
        </sisu></akt>
        """
        generate_all_laws._used_prefixes.clear()
        doc = generate_all_laws.generate_law_jsonld(
            "Marker seadus",
            "marker_seadus",
            ET.fromstring(xml),
            abbreviation="MRKR",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        par = next(
            n for n in doc["@graph"]
            if n.get("@id") == "estleg:MRKR_Par_11"
        )
        for field in ("estleg:legalText", "estleg:summary"):
            value = par.get(field, "")
            for noise in self._MARKER_NOISE:
                assert noise not in value, f"{noise!r} leaked into {field}: {value!r}"
        assert "Sisuline säte." in par["estleg:legalText"]


# ---------------------------------------------------------------------------
# Issue #165 fix 3 — multipart files respect --refresh / --force
# ---------------------------------------------------------------------------


class TestMultipartRefreshMode:
    def test_refresh_overwrites_existing_multipart_file(self, tmp_path, monkeypatch):
        """When mode='refresh' (or 'force'), an existing osa peep file
        must be overwritten. In 'missing-only' it must be skipped.
        """
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        stale_path = krr / "test_law_osa1_peep.json"
        stale_path.write_text(
            json.dumps({"@graph": [{"stale": True}]}, ensure_ascii=False),
            encoding="utf-8",
        )

        # Pad XML with comments so cache file > 1000 bytes.
        pad = "<!-- " + ("x" * 1500) + " -->"
        xml = f"""<?xml version='1.0' encoding='utf-8'?>
        {pad}
        <akt>
          <sisu>
            <osa><osaNr>1</osaNr><osaPealkiri>One</osaPealkiri>
              <paragrahv><paragrahvNr>1</paragrahvNr>
                <kuvatavNr>S 1.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Esimene paragrahvi tekst.</tavatekst></loige>
              </paragrahv>
            </osa>
            <osa><osaNr>2</osaNr><osaPealkiri>Two</osaPealkiri>
              <paragrahv><paragrahvNr>2</paragrahvNr>
                <kuvatavNr>S 2.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Teise paragrahvi tekst.</tavatekst></loige>
              </paragrahv>
            </osa>
          </sisu>
        </akt>
        """
        # Write both candidate cache filenames so fetch_xml hits one.
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        (data_dir / "test_law.xml").write_text(xml, encoding="utf-8")
        (data_dir / "test_law__tid100.xml").write_text(xml, encoding="utf-8")

        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)
        monkeypatch.setattr(
            generate_all_laws,
            "MULTIPART_LAWS",
            {"Test Law"},
        )

        def fake_get_all_laws(**kwargs):
            return ({"Test Law": {
                "gid": "1",
                "tid": "100",
                "url": "/akt/test.xml",
                "lyhend": "TLAW",
            }}, {"complete": True})
        monkeypatch.setattr(generate_all_laws, "get_all_laws", fake_get_all_laws)

        class _Args:
            refresh = True
            force = False
            missing_only = False
            kehtiv = generate_all_laws.DEFAULT_KEHTIV
            allow_partial = False
            limit = 1
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())

        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        with open(stale_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert "@graph" in saved
        assert any(
            (n.get("@id") or "").endswith("Par_1") for n in saved["@graph"]
        ), "refresh mode must overwrite stale multipart osa peep files"


# ---------------------------------------------------------------------------
# Issue #237 — reconcile stale/orphan multipart osa outputs OUTSIDE to_generate
# ---------------------------------------------------------------------------


class TestStaleOsaReconciledOutsideToGenerate:
    """The in-loop ``remove_obsolete_multipart_outputs`` only runs for laws
    selected into ``to_generate``. A multipart law that is skipped from the
    generation loop (resumed via regen state, or because its single-file
    output is already fresh) must still have its stale / orphan
    ``<slug>_osaN_peep.json`` parts reconciled by the post-generation pass.
    """

    _KEHTIV = "2026-05-01"
    _TID = "100"

    def _osa_doc(self, osa_nr: int, kehtiv: str, tid: str) -> dict:
        return {
            "@context": generate_all_laws.CONTEXT,
            "@graph": [
                {
                    "@id": "x",
                    "@type": ["owl:Ontology"],
                    "estleg:kehtiv": {"@value": kehtiv, "@type": "xsd:date"},
                    "estleg:terviktekstId": {
                        "@value": tid,
                        "@type": "xsd:string",
                    },
                },
                {"@id": f"Par_{osa_nr}", "@type": ["owl:NamedIndividual"]},
            ],
        }

    def _two_osa_xml(self) -> str:
        pad = "<!-- " + ("x" * 1500) + " -->"
        return f"""<?xml version='1.0' encoding='utf-8'?>
        {pad}
        <akt><sisu>
          <osa><osaNr>1</osaNr><osaPealkiri>One</osaPealkiri>
            <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>S 1.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Esimene.</tavatekst></loige>
            </paragrahv></osa>
          <osa><osaNr>2</osaNr><osaPealkiri>Two</osaPealkiri>
            <paragrahv><paragrahvNr>2</paragrahvNr><kuvatavNr>S 2.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Teine.</tavatekst></loige>
            </paragrahv></osa>
        </sisu></akt>
        """

    def _single_xml(self) -> str:
        pad = "<!-- " + ("x" * 1500) + " -->"
        return f"""<?xml version='1.0' encoding='utf-8'?>
        {pad}
        <akt><sisu>
          <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>S 1.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><tavatekst>Esimene.</tavatekst></loige>
          </paragrahv>
        </sisu></akt>
        """

    def _write_xml(self, data_dir, xml: str) -> None:
        (data_dir / "test_law.xml").write_text(xml, encoding="utf-8")
        (data_dir / "test_law__tid100.xml").write_text(xml, encoding="utf-8")

    def _patch_common(self, monkeypatch, krr, data_dir, multipart):
        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)
        monkeypatch.setattr(generate_all_laws, "MULTIPART_LAWS", multipart)
        monkeypatch.setattr(
            generate_all_laws,
            "get_all_laws",
            lambda **kw: (
                {
                    "Test Law": {
                        "gid": "1",
                        "tid": self._TID,
                        "url": "/akt/test.xml",
                        "lyhend": "TLAW",
                    }
                },
                {"complete": True},
            ),
        )

    def _args(self, regen_state=None):
        class _Args:
            refresh = False
            force = False
            missing_only = True
            kehtiv = TestStaleOsaReconciledOutsideToGenerate._KEHTIV
            allow_partial = False
            limit = None
            from_manifest = None

        _Args.regen_state = regen_state
        return _Args()

    def test_stale_osa_outside_to_generate_is_reconciled(
        self, tmp_path, monkeypatch
    ):
        """Multipart law resumed via regen state (so it never enters the
        generation loop): fresh osa parts survive, the stale orphan osa9 is
        deleted by the post-generation pass, and the counter records it.

        ``selectedForGeneration == 0`` proves the in-loop cleanup could not
        have done this — only the new reconciliation pass runs here.
        """
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)

        # Two FRESH osa parts (current kehtiv + matching tid) and one STALE
        # orphan part for an osa the current XML no longer produces.
        generate_all_laws.save_json(
            krr / "test_law_osa1_peep.json",
            self._osa_doc(1, self._KEHTIV, self._TID),
        )
        generate_all_laws.save_json(
            krr / "test_law_osa2_peep.json",
            self._osa_doc(2, self._KEHTIV, self._TID),
        )
        generate_all_laws.save_json(
            krr / "test_law_osa9_peep.json",
            self._osa_doc(9, "2020-01-01", self._TID),
        )

        # Mark the slug completed so it is skipped BEFORE the staleness scan,
        # i.e. excluded from to_generate (the #237 trigger).
        regen_path = krr / ".regen_state.json"
        generate_all_laws.save_json(
            regen_path,
            {
                "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
                "completed": {
                    "test_law": {
                        "title": "Test Law",
                        "kehtiv": self._KEHTIV,
                        "terviktekstId": self._TID,
                    }
                },
                "failed": {},
            },
        )

        self._write_xml(data_dir, self._two_osa_xml())
        self._patch_common(monkeypatch, krr, data_dir, {"Test Law"})
        monkeypatch.setattr(
            generate_all_laws,
            "parse_args",
            lambda: self._args(regen_state=str(regen_path)),
        )

        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        assert (krr / "test_law_osa1_peep.json").exists()
        assert (krr / "test_law_osa2_peep.json").exists()
        assert not (krr / "test_law_osa9_peep.json").exists()

        manifest = json.loads(
            (krr / generate_all_laws.MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert manifest["counts"]["selectedForGeneration"] == 0
        assert manifest["run"]["obsoleteMultipartRemoved"] >= 1

    def test_fresh_orphan_osa_outside_to_generate_is_reconciled(
        self, tmp_path, monkeypatch
    ):
        """#246 follow-up: a multipart law skipped from to_generate must also
        lose a FRESH orphan part — an osa carrying the current kehtiv/tid that
        is no longer in the law's structure. Staleness alone can't catch it;
        the pass compares against the osa set the cached XML would produce.
        ``selectedForGeneration == 0`` proves the law was not regenerated.
        """
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)

        # osa1+osa2 are the real parts; osa9 is a FRESH orphan (current kehtiv
        # + tid) left from a structure that no longer contains osa9.
        for nr in (1, 2, 9):
            generate_all_laws.save_json(
                krr / f"test_law_osa{nr}_peep.json",
                self._osa_doc(nr, self._KEHTIV, self._TID),
            )

        regen_path = krr / ".regen_state.json"
        generate_all_laws.save_json(
            regen_path,
            {
                "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
                "completed": {
                    "test_law": {
                        "title": "Test Law",
                        "kehtiv": self._KEHTIV,
                        "terviktekstId": self._TID,
                    }
                },
                "failed": {},
            },
        )

        # Cached XML produces only osa1 + osa2.
        self._write_xml(data_dir, self._two_osa_xml())
        self._patch_common(monkeypatch, krr, data_dir, {"Test Law"})
        monkeypatch.setattr(
            generate_all_laws,
            "parse_args",
            lambda: self._args(regen_state=str(regen_path)),
        )

        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        assert (krr / "test_law_osa1_peep.json").exists()
        assert (krr / "test_law_osa2_peep.json").exists()
        assert not (krr / "test_law_osa9_peep.json").exists()

        manifest = json.loads(
            (krr / generate_all_laws.MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert manifest["counts"]["selectedForGeneration"] == 0
        assert manifest["run"]["obsoleteMultipartRemoved"] >= 1

    def test_orphan_osa_when_law_no_longer_multipart_is_removed(
        self, tmp_path, monkeypatch
    ):
        """A law that is in the snapshot but no longer in MULTIPART_LAWS,
        whose single-file output is already fresh (so it is skipped from
        to_generate), keeps its ``<slug>_peep.json`` but has a leftover
        ``<slug>_osa1_peep.json`` swept by the reconciliation pass.
        """
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)

        # Fresh single-file output -> law is skipped from to_generate.
        generate_all_laws.save_json(
            krr / "test_law_peep.json",
            self._osa_doc(1, self._KEHTIV, self._TID),
        )
        # Orphan part left over from when the law used to be multipart.
        generate_all_laws.save_json(
            krr / "test_law_osa1_peep.json",
            self._osa_doc(1, self._KEHTIV, self._TID),
        )

        self._write_xml(data_dir, self._single_xml())
        # Law is NOT multipart any more.
        self._patch_common(monkeypatch, krr, data_dir, set())
        monkeypatch.setattr(
            generate_all_laws, "parse_args", lambda: self._args()
        )

        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        assert (krr / "test_law_peep.json").exists()
        assert not (krr / "test_law_osa1_peep.json").exists()

        manifest = json.loads(
            (krr / generate_all_laws.MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert manifest["counts"]["selectedForGeneration"] == 0
        assert manifest["run"]["obsoleteMultipartRemoved"] >= 1

    def test_formerly_multipart_regenerated_single_file_sweeps_orphan_osa(
        self, tmp_path, monkeypatch
    ):
        """#246: a formerly-multipart law regenerated as a SINGLE file this
        run (so it enters ``to_generate`` and goes through the single-file
        branch, which has no in-loop osa cleanup) must still have its leftover
        ``<slug>_osaN_peep.json`` parts swept by the reconciliation pass.
        ``selectedForGeneration >= 1`` proves the law was regenerated (not
        skipped), distinguishing this from the out-of-``to_generate`` cases.
        """
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)

        # Orphan part left over from when the law used to be multipart. No
        # single-file output exists, so the law is missing -> regenerated.
        generate_all_laws.save_json(
            krr / "test_law_osa1_peep.json",
            self._osa_doc(1, self._KEHTIV, self._TID),
        )

        self._write_xml(data_dir, self._single_xml())
        # Law is NOT multipart any more.
        self._patch_common(monkeypatch, krr, data_dir, set())
        monkeypatch.setattr(
            generate_all_laws, "parse_args", lambda: self._args()
        )

        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        # Regenerated single file present; orphan osa part swept.
        assert (krr / "test_law_peep.json").exists()
        assert not (krr / "test_law_osa1_peep.json").exists()

        manifest = json.loads(
            (krr / generate_all_laws.MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert manifest["counts"]["selectedForGeneration"] >= 1
        assert manifest["run"]["obsoleteMultipartRemoved"] >= 1

    def test_fresh_osa_for_out_of_scope_law_is_not_deleted(
        self, tmp_path, monkeypatch
    ):
        """Negative guard: a FRESH osa file whose slug belongs to no current
        source title is left untouched, and an in-scope multipart law's fresh
        parts are likewise preserved (no spurious deletions).
        """
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)

        # Out-of-scope law (no such title returned this run) — fresh part.
        generate_all_laws.save_json(
            krr / "other_law_osa1_peep.json",
            self._osa_doc(1, self._KEHTIV, self._TID),
        )
        # In-scope multipart law — fresh parts, resumed via regen state.
        generate_all_laws.save_json(
            krr / "test_law_osa1_peep.json",
            self._osa_doc(1, self._KEHTIV, self._TID),
        )
        generate_all_laws.save_json(
            krr / "test_law_osa2_peep.json",
            self._osa_doc(2, self._KEHTIV, self._TID),
        )
        regen_path = krr / ".regen_state.json"
        generate_all_laws.save_json(
            regen_path,
            {
                "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
                "completed": {
                    "test_law": {
                        "title": "Test Law",
                        "kehtiv": self._KEHTIV,
                        "terviktekstId": self._TID,
                    }
                },
                "failed": {},
            },
        )

        self._write_xml(data_dir, self._two_osa_xml())
        self._patch_common(monkeypatch, krr, data_dir, {"Test Law"})
        monkeypatch.setattr(
            generate_all_laws,
            "parse_args",
            lambda: self._args(regen_state=str(regen_path)),
        )

        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        # Nothing deleted: out-of-scope orphan and in-scope fresh parts stay.
        assert (krr / "other_law_osa1_peep.json").exists()
        assert (krr / "test_law_osa1_peep.json").exists()
        assert (krr / "test_law_osa2_peep.json").exists()

        manifest = json.loads(
            (krr / generate_all_laws.MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert manifest["run"]["obsoleteMultipartRemoved"] == 0


# ---------------------------------------------------------------------------
# Issue #165 fix 10 — pagination cap raises (or marks incomplete)
# ---------------------------------------------------------------------------


class TestPaginationCap:
    def test_cap_hit_raises_without_allow_partial(self, monkeypatch):
        class _Resp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"aktid": [
                    {"pealkiri": "Test", "globaalID": "1",
                     "terviktekstID": "1", "url": "/akt/1.xml", "lyhend": "TS"}
                ]}

        monkeypatch.setattr(
            generate_all_laws.requests,
            "get",
            lambda *a, **kw: _Resp(),
        )
        with pytest.raises(generate_all_laws.SourceListFetchError):
            generate_all_laws.get_all_laws(page_limit=2, allow_partial=False)

    def test_cap_hit_with_allow_partial_marks_incomplete(self, monkeypatch):
        class _Resp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"aktid": [
                    {"pealkiri": "Test", "globaalID": "1",
                     "terviktekstID": "1", "url": "/akt/1.xml", "lyhend": "TS"}
                ]}

        monkeypatch.setattr(
            generate_all_laws.requests,
            "get",
            lambda *a, **kw: _Resp(),
        )
        all_laws, manifest = generate_all_laws.get_all_laws(
            page_limit=2, allow_partial=True,
        )
        assert manifest["complete"] is False
        assert manifest["pageLimitHit"] is True
        assert manifest["pagesScanned"] >= 2


# ---------------------------------------------------------------------------
# Issue #165 fix 8 — outputsAll is mode-invariant
# ---------------------------------------------------------------------------


class TestManifestOutputsAll:
    def test_outputsAll_includes_every_known_act_regardless_of_mode(
        self, tmp_path, monkeypatch
    ):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        # Pre-existing peep so missing-only short-circuits.
        (krr / "act_one_peep.json").write_text(
            json.dumps({"@graph": []}), encoding="utf-8",
        )
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        (data_dir / "act_one.xml").write_text(
            "<akt><sisu/></akt>", encoding="utf-8",
        )

        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)
        monkeypatch.setattr(generate_all_laws, "MULTIPART_LAWS", set())

        def fake_get_all_laws(**kwargs):
            return ({"Act One": {
                "gid": "10",
                "tid": "100",
                "url": "/akt/act_one.xml",
                "lyhend": "AONE",
            }, "Act Two": {
                "gid": "20",
                "tid": "200",
                "url": "/akt/act_two.xml",
                "lyhend": "ATWO",
            }}, {"complete": True})
        monkeypatch.setattr(generate_all_laws, "get_all_laws", fake_get_all_laws)

        class _Args:
            refresh = False
            force = False
            missing_only = True
            kehtiv = generate_all_laws.DEFAULT_KEHTIV
            allow_partial = False
            limit = None
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())

        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        manifest_path = krr / "generation_manifest_laws.json"
        assert manifest_path.exists()
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        all_slugs = {entry["slug"] for entry in manifest["outputsAll"]}
        assert "act_one" in all_slugs
        assert "act_two" in all_slugs
        for entry in manifest["outputsAll"]:
            assert {"slug", "globaalId", "terviktekstId"} <= entry.keys()


# ---------------------------------------------------------------------------
# Issue #165 fix 9 — fetch_xml cache uses tervikteksti id
# ---------------------------------------------------------------------------


class TestFetchXmlTidCache:
    def test_cache_filename_includes_tid_when_provided(
        self, tmp_path, monkeypatch
    ):
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)

        # Pad inside the root element so the cache file > 200 bytes.
        class _Resp:
            text = "<?xml version='1.0'?><akt><!-- " + ("x" * 500) + " --></akt>"
            encoding = "utf-8"
            def raise_for_status(self):
                pass

        monkeypatch.setattr(
            generate_all_laws.requests,
            "get",
            lambda *a, **kw: _Resp(),
        )

        root = generate_all_laws.fetch_xml(
            "/akt/x.xml", "myslug", tid="9999",
        )
        assert root is not None

        cached = list(data_dir.glob("*.xml"))
        assert any("tid9999" in p.name for p in cached), (
            f"cache file should include tid suffix; got {[p.name for p in cached]}"
        )

    def test_legacy_slug_cache_falls_back(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        legacy = data_dir / "myslug.xml"
        # Cache must be >1000 bytes; pad inside the root so it parses.
        legacy.write_text(
            "<?xml version='1.0'?><akt><!-- " + ("x" * 1500) + " --></akt>",
            encoding="utf-8",
        )
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)

        def _bad_get(*a, **kw):
            raise AssertionError("HTTP must not be called")
        monkeypatch.setattr(generate_all_laws.requests, "get", _bad_get)

        root = generate_all_laws.fetch_xml(
            "/akt/x.xml", "myslug", tid="42",
        )
        assert root is not None


class TestFetchXmlCacheThresholdAndValidation:
    """#601 — one shared min-size floor for download AND cache-accept (so a
    written file is always re-accepted, no infinite refetch), plus rejection
    of an HTML/error root that parses as XML."""

    def _resp(self, text: str):
        class _Resp:
            encoding = "utf-8"

            def __init__(self, body: str) -> None:
                self.text = body

            def raise_for_status(self):
                pass

        return _Resp(text)

    def test_written_cache_file_is_reaccepted_no_refetch(
        self, tmp_path, monkeypatch
    ):
        # A small-but-valid response just above MIN_XML_BYTES must be cached
        # AND re-read without a second network call. Previously a 200–1000
        # byte file was written but never re-accepted (refetched forever).
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)

        body = "<?xml version='1.0'?><akt><!-- " + ("x" * 400) + " --></akt>"
        assert generate_all_laws.MIN_XML_BYTES < len(body) < 1000

        calls = {"n": 0}

        def _get(*a, **kw):
            calls["n"] += 1
            return self._resp(body)

        monkeypatch.setattr(generate_all_laws.requests, "get", _get)

        first = generate_all_laws.fetch_xml("/akt/x.xml", "slugA", tid="7")
        second = generate_all_laws.fetch_xml("/akt/x.xml", "slugA", tid="7")
        assert first is not None and second is not None
        assert calls["n"] == 1, "second call must be served from cache"

    def test_html_error_page_is_not_cached(self, tmp_path, monkeypatch):
        # An >threshold HTML error page that still parses as XML must be
        # rejected (return None) and never written to the cache.
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)

        html = "<html><body>" + ("error " * 100) + "</body></html>"
        monkeypatch.setattr(
            generate_all_laws.requests, "get", lambda *a, **kw: self._resp(html)
        )
        root = generate_all_laws.fetch_xml("/akt/x.xml", "slugB", tid="1")
        assert root is None
        assert list(data_dir.glob("*.xml")) == []

    def test_cached_html_root_is_treated_as_miss(self, tmp_path, monkeypatch):
        # A pre-existing cached HTML file must not be trusted; with no
        # network available the call returns None rather than the error page.
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        (data_dir / "slugC.xml").write_text(
            "<html>" + ("x" * 1500) + "</html>", encoding="utf-8"
        )
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)

        def _bad_get(*a, **kw):
            raise AssertionError("network used")

        monkeypatch.setattr(generate_all_laws.requests, "get", _bad_get)
        # No tid → only the legacy slug file is consulted; it is HTML, so it
        # must be rejected. The network is stubbed to fail, so fetch reports
        # the failure and returns None (never the HTML root).
        assert generate_all_laws.fetch_xml("/akt/x.xml", "slugC") is None


class TestSaveJsonAtomic:
    """#601 — the local save_json must write atomically (tempfile +
    os.replace) so a crash mid-dump cannot leave a half-written file."""

    def test_save_json_roundtrips(self, tmp_path):
        out = tmp_path / "x_peep.json"
        generate_all_laws.save_json(out, {"@graph": [{"@id": "estleg:X"}]})
        assert json.loads(out.read_text(encoding="utf-8")) == {
            "@graph": [{"@id": "estleg:X"}]
        }

    def test_failed_dump_leaves_no_tmp_and_no_partial(self, tmp_path, monkeypatch):
        out = tmp_path / "x_peep.json"
        out.write_text('{"old": true}', encoding="utf-8")

        # A non-serialisable payload makes json.dump raise mid-write.
        class _Bad:
            pass

        with pytest.raises(TypeError):
            generate_all_laws.save_json(out, {"bad": _Bad()})

        # The original file is untouched (atomic replace never happened)...
        assert json.loads(out.read_text(encoding="utf-8")) == {"old": True}
        # ...and no .tmp dropping is left behind.
        assert not list(tmp_path.glob("*.tmp"))


class TestStructuralStalenessGuard:
    """#594 — a zero-paragraph parse must only become a stub when the act is
    GENUINELY body-less; a degraded/foreign parse (root carries block tags
    but no paragrahv, or a structured peep already exists) must NOT be
    laundered into a sticky body-less stub."""

    def test_root_with_block_tags_but_no_paragraph_is_degraded(self):
        root = ET.fromstring("<akt><peatykk><pealkiri>X</pealkiri></peatykk></akt>")
        assert generate_all_laws.xml_root_has_structure(root) is True

    def test_genuinely_bodyless_root_has_no_structure(self):
        root = ET.fromstring("<akt><metaandmed><pealkiri>X</pealkiri></metaandmed></akt>")
        assert generate_all_laws.xml_root_has_structure(root) is False

    def test_loige_only_root_is_degraded(self):
        root = ET.fromstring("<akt><loige>tekst</loige></akt>")
        assert generate_all_laws.xml_root_has_structure(root) is True

    def test_existing_structured_peep_detected(self, tmp_path):
        peep = tmp_path / "law_peep.json"
        peep.write_text(
            json.dumps(
                {
                    "@graph": [
                        {"@id": "estleg:X_Map_2026", "@type": ["owl:Ontology"]},
                        {
                            "@id": "estleg:X_Par_1",
                            "@type": ["estleg:LegalProvision"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert generate_all_laws.existing_peep_is_structured(peep) is True

    def test_existing_stub_peep_is_not_structured(self, tmp_path):
        peep = tmp_path / "law_peep.json"
        peep.write_text(
            json.dumps(
                {
                    "@graph": [
                        {
                            "@id": "estleg:X_Map_2026",
                            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                            "estleg:contentStatus": "noStructuredBody",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert generate_all_laws.existing_peep_is_structured(peep) is False

    def test_missing_peep_is_not_structured(self, tmp_path):
        assert (
            generate_all_laws.existing_peep_is_structured(tmp_path / "nope.json")
            is False
        )


# ---------------------------------------------------------------------------
# Issue #114 — fail on source-list fetch failure; per-page counters; the
# index/manifest must never look "complete" after a truncated enumeration.
# ---------------------------------------------------------------------------


def _law_row(n: int) -> dict:
    return {
        "pealkiri": f"Test seadus {n}",
        "globaalID": str(100 + n),
        "terviktekstID": str(200 + n),
        "url": f"/akt/{n}.xml",
        "lyhend": f"TS{n}",
    }


class _PageOneOkPageTwoFailsGet:
    """A fake ``requests.get`` for the law-list API that returns a full
    page of acts on ``leht=1`` and raises (via ``raise_for_status``) on
    ``leht>=2`` — i.e. a transient failure mid-enumeration.
    """

    class _OkResp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return self._payload

    class _BoomResp:
        def raise_for_status(self) -> None:
            raise RuntimeError("API error on page 2: boom")

        def json(self) -> dict:  # pragma: no cover - never reached
            return {}

    def __call__(self, url, *, params=None, timeout=None, **kw):
        params = params or {}
        page = int(params.get("leht", 1))
        if page == 1:
            # Non-empty page so ``get_all_laws`` advances to page 2.
            return self._OkResp({"aktid": [_law_row(1)]})
        return self._BoomResp()


class TestGetAllLawsPageCounters:
    """``get_all_laws`` must surface per-page success/fail/retry counts."""

    def test_happy_path_reports_pages_fetched_ok(self, monkeypatch):
        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._payload

        def fake_get(url, *, params=None, timeout=None, **kw):
            params = params or {}
            page = int(params.get("leht", 1))
            if page == 1:
                return _Resp({"aktid": [_law_row(1)]})
            # Empty page terminates enumeration cleanly.
            return _Resp({"aktid": []})

        monkeypatch.setattr(generate_all_laws.requests, "get", fake_get)
        all_laws, manifest = generate_all_laws.get_all_laws(allow_partial=False)

        assert manifest["complete"] is True
        assert manifest["pages"]["pagesFetchedOk"] == 2
        assert manifest["pages"]["pagesFailed"] == 0
        assert manifest["pages"]["pagesRetried"] == 0
        assert set(manifest["pages"]) == {
            "pagesFetchedOk",
            "pagesFailed",
            "pagesRetried",
        }

    def test_partial_run_counts_the_failed_page(self, monkeypatch):
        monkeypatch.setattr(
            generate_all_laws.requests, "get", _PageOneOkPageTwoFailsGet()
        )
        all_laws, manifest = generate_all_laws.get_all_laws(allow_partial=True)

        assert manifest["complete"] is False
        assert manifest["partialAllowed"] is True
        assert manifest["pages"]["pagesFetchedOk"] == 1
        assert manifest["pages"]["pagesFailed"] == 1
        # Page 1's single act still made it through.
        assert len(all_laws) == 1


class TestGenerateAllLawsNoPartialWrite:
    """When a law-list page fails mid-enumeration and ``--allow-partial``
    is NOT set, ``main()`` must exit non-zero and write NEITHER the
    manifest NOR any ``*_peep.json`` output — a consumer must not be able
    to mistake a truncated run for a complete corpus refresh.
    """

    def _wire_tmp_run(self, tmp_path, monkeypatch, *, allow_partial: bool):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)
        monkeypatch.setattr(generate_all_laws, "MULTIPART_LAWS", set())
        monkeypatch.setattr(
            generate_all_laws.requests, "get", _PageOneOkPageTwoFailsGet()
        )
        # No real XML behind the (only, partial) row — keep the per-act
        # fetch offline; main() counts it as a failed fetch.
        monkeypatch.setattr(generate_all_laws, "fetch_xml", lambda *a, **kw: None)

        class _Args:
            refresh = False
            force = False
            missing_only = True
            kehtiv = generate_all_laws.DEFAULT_KEHTIV
            limit = None

        _Args.allow_partial = allow_partial
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())
        return krr

    def test_main_exits_nonzero_and_writes_nothing(self, tmp_path, monkeypatch):
        krr = self._wire_tmp_run(tmp_path, monkeypatch, allow_partial=False)
        generate_all_laws._used_prefixes.clear()

        with pytest.raises(SystemExit) as exc_info:
            generate_all_laws.main()
        assert exc_info.value.code != 0

        assert not (krr / "generation_manifest_laws.json").exists(), (
            "manifest must NOT be written after a truncated source enumeration"
        )
        assert list(krr.glob("*_peep.json")) == [], (
            "no peep outputs may be written when the source list fetch failed"
        )

    def test_get_all_laws_raises_so_main_never_proceeds(
        self, tmp_path, monkeypatch
    ):
        # Direct check on the helper used by main(): the partial fetch is
        # fatal by default, so no caller can observe a partial dict.
        monkeypatch.setattr(
            generate_all_laws.requests, "get", _PageOneOkPageTwoFailsGet()
        )
        with pytest.raises(generate_all_laws.SourceListFetchError):
            generate_all_laws.get_all_laws(allow_partial=False)

    def test_allow_partial_writes_manifest_marked_incomplete(
        self, tmp_path, monkeypatch
    ):
        krr = self._wire_tmp_run(tmp_path, monkeypatch, allow_partial=True)
        generate_all_laws._used_prefixes.clear()

        # ``--allow-partial`` lets main() complete; page 1's act has no
        # cached XML and the fake get() only serves the search API, so
        # fetch_xml returns None and the act is counted as a failed fetch
        # — but the run still produces a (visibly partial) manifest.
        generate_all_laws.main()

        manifest_path = krr / "generation_manifest_laws.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["source"]["complete"] is False
        assert manifest["source"]["partialAllowed"] is True
        pages = manifest["source"]["pages"]
        assert pages["pagesFetchedOk"] >= 1
        assert pages["pagesFailed"] >= 1


# ---------------------------------------------------------------------------
# Issue #108 residual — estleg:kehtiv stamping, stale-snapshot refresh,
# unchanged/refreshed counters, source-removed detection, manifest replay.
# ---------------------------------------------------------------------------


class TestKehtivStamping:
    def test_structured_law_node_carries_kehtiv(self):
        xml = (
            "<akt><sisu><peatykk><peatykkNr>1</peatykkNr>"
            "<peatykkPealkiri>P</peatykkPealkiri>"
            "<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>S 1.</kuvatavNr>"
            "<paragrahvPealkiri>Yks</paragrahvPealkiri>"
            "<loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>"
            "</paragrahv></peatykk></sisu></akt>"
        )
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        doc = generate_all_laws.generate_law_jsonld(
            "Test seadus", "test_seadus", root, abbreviation="TKS",
            kehtiv="2026-05-01", allocator=alloc,
        )
        ont = doc["@graph"][0]
        assert ont["estleg:kehtiv"] == {"@value": "2026-05-01", "@type": "xsd:date"}

    def test_stub_law_node_carries_kehtiv(self):
        root = ET.fromstring("<akt><sisu /></akt>")
        alloc = generate_all_laws.PrefixAllocator(registry={})
        doc = generate_all_laws.generate_law_stub_jsonld(
            "Treaty", "treaty_slug", root, abbreviation="TR",
            rt_url="/akt/1.xml", kehtiv="2026-05-01", allocator=alloc,
        )
        ont = doc["@graph"][0]
        assert ont["estleg:kehtiv"] == {"@value": "2026-05-01", "@type": "xsd:date"}

    def test_multipart_osa_nodes_carry_kehtiv(self):
        xml = """<akt><sisu>
            <osa><osaNr>1</osaNr><osaPealkiri>One</osaPealkiri>
              <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>S 1.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Aaa.</tavatekst></loige></paragrahv>
            </osa>
            <osa><osaNr>2</osaNr><osaPealkiri>Two</osaPealkiri>
              <paragrahv><paragrahvNr>2</paragrahvNr><kuvatavNr>S 2.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Bbb.</tavatekst></loige></paragrahv>
            </osa>
        </sisu></akt>"""
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        results = generate_all_laws.generate_multipart_law(
            "Multi", "multi", root, abbreviation="MUL",
            kehtiv="2026-05-01", allocator=alloc,
        )
        assert len(results) == 2
        for _name, doc in results:
            assert doc["@graph"][0]["estleg:kehtiv"] == {
                "@value": "2026-05-01", "@type": "xsd:date"
            }

    def test_no_kehtiv_means_no_stamp(self):
        root = ET.fromstring("<akt><sisu /></akt>")
        alloc = generate_all_laws.PrefixAllocator(registry={})
        doc = generate_all_laws.generate_law_stub_jsonld(
            "X", "x_slug", root, abbreviation="XX", rt_url="/akt/1.xml",
            allocator=alloc,
        )
        assert "estleg:kehtiv" not in doc["@graph"][0]


class TestExistingLawIsStale:
    def _stub_doc(self, *, tid="100", kehtiv="2026-05-01"):
        ont = {
            "@id": "estleg:X_Map_2026",
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
            "estleg:terviktekstId": tid,
        }
        if kehtiv is not None:
            ont["estleg:kehtiv"] = {"@value": kehtiv, "@type": "xsd:date"}
        return {"@graph": [ont]}

    def test_fresh_file_not_stale(self):
        assert generate_all_laws.existing_law_is_stale(
            self._stub_doc(), "2026-05-01", "100"
        ) is False

    def test_kehtiv_drift_marks_stale(self):
        assert generate_all_laws.existing_law_is_stale(
            self._stub_doc(kehtiv="2024-01-01"), "2026-05-01", "100"
        ) is True

    def test_missing_kehtiv_marks_stale(self):
        assert generate_all_laws.existing_law_is_stale(
            self._stub_doc(kehtiv=None), "2026-05-01", "100"
        ) is True

    def test_tid_drift_marks_stale(self):
        assert generate_all_laws.existing_law_is_stale(
            self._stub_doc(tid="100"), "2026-05-01", "200"
        ) is True

    def test_no_ontology_node_not_stale(self):
        assert generate_all_laws.existing_law_is_stale(
            {"@graph": [{"@id": "x"}]}, "2026-05-01", "100"
        ) is False

    def test_non_dict_not_stale(self):
        assert generate_all_laws.existing_law_is_stale(None, "2026-05-01", "100") is False  # type: ignore[arg-type]


class TestWriteLawOutput:
    def test_missing_only_skips_fresh(self, tmp_path):
        p = tmp_path / "x_peep.json"
        doc = {"@graph": [{"@id": "estleg:X_Map_2026", "@type": ["owl:Ontology"],
                           "estleg:terviktekstId": "100",
                           "estleg:kehtiv": {"@value": "2026-05-01", "@type": "xsd:date"}}]}
        p.write_text(json.dumps(doc), encoding="utf-8")
        status = generate_all_laws.write_law_output(
            p, doc, mode="missing-only", expected_kehtiv="2026-05-01", expected_tid="100",
        )
        assert status == "existingSkipped"

    def test_missing_only_refreshes_stale(self, tmp_path):
        p = tmp_path / "x_peep.json"
        stale = {"@graph": [{"@id": "estleg:X_Map_2026", "@type": ["owl:Ontology"],
                             "estleg:terviktekstId": "100",
                             "estleg:kehtiv": {"@value": "2024-01-01", "@type": "xsd:date"}}]}
        fresh = {"@graph": [{"@id": "estleg:X_Map_2026", "@type": ["owl:Ontology"],
                             "estleg:terviktekstId": "100",
                             "estleg:kehtiv": {"@value": "2026-05-01", "@type": "xsd:date"}}]}
        p.write_text(json.dumps(stale), encoding="utf-8")
        status = generate_all_laws.write_law_output(
            p, fresh, mode="missing-only", expected_kehtiv="2026-05-01", expected_tid="100",
        )
        assert status == "refreshedStale"
        assert json.loads(p.read_text(encoding="utf-8")) == fresh

    def test_missing_only_new_file(self, tmp_path):
        p = tmp_path / "x_peep.json"
        doc = {"@graph": [{"@id": "estleg:X_Map_2026", "@type": ["owl:Ontology"]}]}
        status = generate_all_laws.write_law_output(p, doc, mode="missing-only")
        assert status == "newlyGenerated"
        assert p.exists()

    def test_refresh_unchanged(self, tmp_path):
        p = tmp_path / "x_peep.json"
        doc = {"@graph": [{"@id": "estleg:X"}]}
        p.write_text(json.dumps(doc), encoding="utf-8")
        # save_json writes with indent=2 + trailing newline; existing_doc_matches
        # compares parsed JSON, so the formatting difference is fine.
        assert generate_all_laws.write_law_output(p, doc, mode="refresh") == "unchanged"

    def test_refresh_rewrites_changed(self, tmp_path):
        p = tmp_path / "x_peep.json"
        p.write_text(json.dumps({"@graph": [{"@id": "estleg:Old"}]}), encoding="utf-8")
        new_doc = {"@graph": [{"@id": "estleg:New"}]}
        assert generate_all_laws.write_law_output(p, new_doc, mode="refresh") == "refreshed"
        assert json.loads(p.read_text(encoding="utf-8")) == new_doc

    def test_force_rewrites_even_unchanged(self, tmp_path):
        p = tmp_path / "x_peep.json"
        doc = {"@graph": [{"@id": "estleg:Same"}]}
        p.write_text(json.dumps(doc), encoding="utf-8")
        assert generate_all_laws.write_law_output(p, doc, mode="force") == "forceRewritten"

    def test_unknown_mode_raises(self, tmp_path):
        with pytest.raises(ValueError):
            generate_all_laws.write_law_output(
                tmp_path / "x.json", {"@graph": []}, mode="bogus"
            )


class TestEnrichmentPreservation:
    """#205 drift #3: regenerating a law preserves enrichment fields layered
    on by separate scripts (EuroVoc subjects, transposition, harmonisation,
    drafts, ProvisionVersion back-links, sanctions …)."""

    def test_force_rewrite_preserves_act_enrichments(self, tmp_path):
        p = tmp_path / "x_peep.json"
        existing = {
            "@graph": [
                {
                    "@id": "estleg:X_Map_2026",
                    "@type": ["owl:Ontology"],
                    "rdfs:label": "stale label",
                    "dcterms:subject": [{"@id": "http://eurovoc.europa.eu/1"}],
                    "estleg:transposesDirective": [{"@id": "estleg:EU_32009L0001"}],
                    "estleg:harmonisedWith": [{"@id": "estleg:Harmonisation_32009L0001"}],
                    "estleg:transpositionStatus": "complete",
                    "estleg:temporalStatus": "inForce",
                }
            ]
        }
        p.write_text(json.dumps(existing), encoding="utf-8")
        fresh = {
            "@graph": [
                {
                    "@id": "estleg:X_Map_2026",
                    "@type": ["owl:Ontology"],
                    "rdfs:label": "fresh label",
                    "estleg:contentStatus": "structuredBody",
                }
            ]
        }
        status = generate_all_laws.write_law_output(p, fresh, mode="force")
        assert status == "forceRewritten"
        written = json.loads(p.read_text(encoding="utf-8"))
        act = written["@graph"][0]
        assert act["rdfs:label"] == "fresh label"
        assert act["estleg:contentStatus"] == "structuredBody"
        assert act["dcterms:subject"] == [{"@id": "http://eurovoc.europa.eu/1"}]
        assert act["estleg:transposesDirective"] == [{"@id": "estleg:EU_32009L0001"}]
        assert act["estleg:harmonisedWith"] == [
            {"@id": "estleg:Harmonisation_32009L0001"}
        ]
        assert act["estleg:transpositionStatus"] == "complete"
        assert act["estleg:temporalStatus"] == "inForce"

    def test_force_rewrite_preserves_provision_enrichments(self, tmp_path):
        p = tmp_path / "x_peep.json"
        existing = {
            "@graph": [
                {
                    "@id": "estleg:X_Par_1",
                    "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                    "estleg:paragrahv": "§ 1.",
                    "estleg:normativeType": "obligation",
                    "estleg:hasVersion": [{"@id": "estleg:X_Par_1_v123"}],
                    "estleg:hasOpinion": [{"@id": "estleg:Annotation_OK_x"}],
                }
            ]
        }
        p.write_text(json.dumps(existing), encoding="utf-8")
        fresh = {
            "@graph": [
                {
                    "@id": "estleg:X_Par_1",
                    "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                    "estleg:paragrahv": "§ 1.",
                    "estleg:summary": "Refreshed summary.",
                }
            ]
        }
        status = generate_all_laws.write_law_output(p, fresh, mode="force")
        assert status == "forceRewritten"
        written = json.loads(p.read_text(encoding="utf-8"))
        prov = written["@graph"][0]
        assert prov["estleg:summary"] == "Refreshed summary."
        assert prov["estleg:normativeType"] == "obligation"
        assert prov["estleg:hasVersion"] == [{"@id": "estleg:X_Par_1_v123"}]
        assert prov["estleg:hasOpinion"] == [{"@id": "estleg:Annotation_OK_x"}]

    def test_generator_fields_win_over_existing(self, tmp_path):
        p = tmp_path / "x_peep.json"
        existing = {
            "@graph": [
                {
                    "@id": "estleg:X_Par_1",
                    "@type": ["owl:NamedIndividual"],
                    "estleg:summary": "old summary",
                    "estleg:normativeType": "permission",
                }
            ]
        }
        p.write_text(json.dumps(existing), encoding="utf-8")
        fresh = {
            "@graph": [
                {
                    "@id": "estleg:X_Par_1",
                    "@type": ["owl:NamedIndividual"],
                    "estleg:summary": "new summary",
                    "estleg:legalText": "Fresh legal text.",
                }
            ]
        }
        status = generate_all_laws.write_law_output(p, fresh, mode="force")
        assert status == "forceRewritten"
        prov = json.loads(p.read_text(encoding="utf-8"))["@graph"][0]
        assert prov["estleg:summary"] == "new summary"
        assert prov["estleg:normativeType"] == "permission"

    def test_curated_summary_survives_textless_fallback(self, tmp_path):
        p = tmp_path / "x_peep.json"
        existing = {
            "@graph": [
                {
                    "@id": "estleg:X_Par_1",
                    "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                    "estleg:summary": "curated summary",
                    "estleg:normativeType": "permission",
                }
            ]
        }
        p.write_text(json.dumps(existing), encoding="utf-8")
        fresh = {
            "@graph": [
                {
                    "@id": "estleg:X_Par_1",
                    "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                    "estleg:summary": "§ 1. Generator fallback",
                }
            ]
        }
        status = generate_all_laws.write_law_output(p, fresh, mode="force")
        assert status == "forceRewritten"
        prov = json.loads(p.read_text(encoding="utf-8"))["@graph"][0]
        assert prov["estleg:summary"] == "curated summary"
        assert prov["estleg:normativeType"] == "permission"

    def test_new_node_in_fresh_doc_has_no_merge_source(self, tmp_path):
        """Subsection nodes appear only in fresh output; no merge target."""
        p = tmp_path / "x_peep.json"
        existing = {
            "@graph": [
                {
                    "@id": "estleg:X_Par_1",
                    "estleg:summary": "p1",
                    "estleg:normativeType": "obligation",
                }
            ]
        }
        p.write_text(json.dumps(existing), encoding="utf-8")
        fresh = {
            "@graph": [
                {"@id": "estleg:X_Par_1", "estleg:summary": "p1"},
                {
                    "@id": "estleg:X_Par_1_Lg_1",
                    "@type": ["estleg:Subsection", "owl:NamedIndividual"],
                    "estleg:legalText": "(1) ...",
                },
            ]
        }
        generate_all_laws.write_law_output(p, fresh, mode="force")
        written = json.loads(p.read_text(encoding="utf-8"))
        prov = next(n for n in written["@graph"] if n["@id"] == "estleg:X_Par_1")
        sub = next(n for n in written["@graph"] if n["@id"] == "estleg:X_Par_1_Lg_1")
        assert prov["estleg:normativeType"] == "obligation"
        assert sub["estleg:legalText"] == "(1) ..."
        assert "estleg:normativeType" not in sub

    def test_preserve_disabled_does_not_merge(self, tmp_path):
        p = tmp_path / "x_peep.json"
        existing = {
            "@graph": [
                {"@id": "estleg:X_Par_1", "estleg:normativeType": "obligation"}
            ]
        }
        p.write_text(json.dumps(existing), encoding="utf-8")
        fresh = {"@graph": [{"@id": "estleg:X_Par_1", "estleg:summary": "new"}]}
        generate_all_laws.write_law_output(
            p, fresh, mode="force", preserve_enrichments=False
        )
        prov = json.loads(p.read_text(encoding="utf-8"))["@graph"][0]
        assert prov == {"@id": "estleg:X_Par_1", "estleg:summary": "new"}

    def test_no_existing_file_is_passthrough(self, tmp_path):
        p = tmp_path / "x_peep.json"
        fresh = {"@graph": [{"@id": "estleg:X_Par_1"}]}
        generate_all_laws.write_law_output(p, fresh, mode="force")
        assert json.loads(p.read_text(encoding="utf-8")) == fresh


class TestSourceRemovedLawFiles:
    def test_reports_files_whose_slug_left_the_snapshot(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        # File contents don't matter — identity is slug-based on the
        # filename so the check is robust to dc:source format drift.
        for name in ("keep_seadus_peep.json", "gone_seadus_peep.json"):
            (krr / name).write_text(json.dumps({"@graph": []}), encoding="utf-8")
        removed = generate_all_laws.source_removed_law_files(krr, {"Keep seadus"})
        assert removed == ["gone_seadus_peep.json"]

    def test_multipart_osa_files_resolve_to_parent_slug(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        # A multipart law's per-osa files must NOT be reported when the
        # parent title is still in the snapshot — _law_file_base_slug
        # strips the _osaN suffix back to the parent slug.
        parent_slug = generate_all_laws.slugify("Võlaõigusseadus")
        (krr / f"{parent_slug}_osa2_peep.json").write_text(json.dumps({"@graph": []}), "utf-8")
        (krr / f"{parent_slug}_osa6_peep.json").write_text(json.dumps({"@graph": []}), "utf-8")
        (krr / "kadunud_seadus_peep.json").write_text(json.dumps({"@graph": []}), "utf-8")
        removed = generate_all_laws.source_removed_law_files(
            krr, {"Võlaõigusseadus"}, multipart_titles={"Võlaõigusseadus"}
        )
        assert removed == ["kadunud_seadus_peep.json"]

    def test_law_file_base_slug_strips_osa_and_peep(self):
        assert generate_all_laws._law_file_base_slug("advokatuuriseadus_peep.json") == "advokatuuriseadus"
        assert generate_all_laws._law_file_base_slug("volaigusseadus_osa10_peep.json") == "volaigusseadus"


def test_obsolete_multipart_outputs_are_removed(tmp_path):
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    for name in (
        "asjaoigusseadus_osa1_peep.json",
        "asjaoigusseadus_osa2_peep.json",
        "asjaoigusseadus_osa9_peep.json",
        "asjaoigusseadus_osa6-13_peep.json",
        "asjaoigusseadus_osaline_test_peep.json",
        "muu_seadus_osa9_peep.json",
    ):
        (krr / name).write_text(json.dumps({"@graph": []}), encoding="utf-8")

    removed = generate_all_laws.remove_obsolete_multipart_outputs(
        krr,
        "asjaoigusseadus",
        {"asjaoigusseadus_osa1_peep.json", "asjaoigusseadus_osa2_peep.json"},
    )

    assert [path.name for path in removed] == [
        "asjaoigusseadus_osa9_peep.json",
    ]
    assert (krr / "asjaoigusseadus_osa1_peep.json").exists()
    assert (krr / "asjaoigusseadus_osa2_peep.json").exists()
    assert (krr / "asjaoigusseadus_osa6-13_peep.json").exists()
    assert (krr / "asjaoigusseadus_osaline_test_peep.json").exists()
    assert (krr / "muu_seadus_osa9_peep.json").exists()


class TestLoadLawListFromManifest:
    def test_round_trip_reconstructs_act_list(self, tmp_path):
        manifest = {
            "outputsAll": [
                {"title": "Act A", "slug": "act_a", "terviktekstId": "100",
                 "globaalId": "10", "sourceUrl": "https://www.riigiteataja.ee/akt/100.xml",
                 "status": "full"},
                {"title": "Act B", "slug": "act_b", "terviktekstId": "200",
                 "globaalId": "20", "sourceUrl": "/akt/200.xml", "status": "stub"},
            ]
        }
        p = tmp_path / "m.json"
        p.write_text(json.dumps(manifest), encoding="utf-8")
        laws = generate_all_laws.load_law_list_from_manifest(p)
        assert set(laws) == {"Act A", "Act B"}
        assert laws["Act A"]["tid"] == "100"
        assert laws["Act A"]["url"] == "/akt/100.xml"  # BASE_URL stripped
        assert laws["Act B"]["url"] == "/akt/200.xml"

    def test_falls_back_to_outputs_key(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"outputs": [
            {"title": "Only", "slug": "only", "terviktekstId": "1", "globaalId": "1",
             "sourceUrl": "/akt/1.xml"}
        ]}), encoding="utf-8")
        laws = generate_all_laws.load_law_list_from_manifest(p)
        assert set(laws) == {"Only"}

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError):
            generate_all_laws.load_law_list_from_manifest(tmp_path / "nope.json")

    def test_empty_manifest_raises(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"outputsAll": []}), encoding="utf-8")
        with pytest.raises(ValueError):
            generate_all_laws.load_law_list_from_manifest(p)


def _padded_law_xml(*, paragraphs: list[int]) -> str:
    """Minimal single-law XML with the given paragraph numbers, padded so
    the on-disk cache file exceeds the 1000-byte threshold fetch_xml uses."""
    pad = "<!-- " + ("x" * 1500) + " -->"
    pars = "".join(
        f"<paragrahv><paragrahvNr>{n}</paragrahvNr><kuvatavNr>S {n}.</kuvatavNr>"
        f"<paragrahvPealkiri>Par {n}</paragrahvPealkiri>"
        f"<loige><loigeNr>1</loigeNr><tavatekst>Tekst {n}.</tavatekst></loige>"
        f"</paragrahv>"
        for n in paragraphs
    )
    return (
        f"<?xml version='1.0' encoding='utf-8'?>{pad}"
        f"<akt><sisu><peatykk><peatykkNr>1</peatykkNr>"
        f"<peatykkPealkiri>Peatykk</peatykkPealkiri>{pars}</peatykk></sisu></akt>"
    )


def _padded_multipart_law_xml() -> str:
    pad = "<!-- " + ("x" * 1500) + " -->"
    parts = []
    for osa_nr, par_nr in ((1, 10), (2, 20)):
        parts.append(
            f"<osa><osaNr>{osa_nr}</osaNr><osaPealkiri>Osa {osa_nr}</osaPealkiri>"
            f"<paragrahv><paragrahvNr>{par_nr}</paragrahvNr>"
            f"<kuvatavNr>S {par_nr}.</kuvatavNr>"
            f"<paragrahvPealkiri>Par {par_nr}</paragrahvPealkiri>"
            f"<loige><loigeNr>1</loigeNr><tavatekst>Tekst {par_nr}.</tavatekst></loige>"
            f"</paragrahv></osa>"
        )
    return f"<?xml version='1.0' encoding='utf-8'?>{pad}<akt><sisu>{''.join(parts)}</sisu></akt>"


class _RerunArgs:
    """Mutable args object for driving main() in rerun tests."""
    refresh = False
    force = False
    missing_only = True
    kehtiv = generate_all_laws.DEFAULT_KEHTIV
    allow_partial = False
    limit = None
    from_manifest = None
    reset_regen_state = False


class TestRerunNoOpAndStaleRefresh:
    """#108: a clean run then a second missing-only run rewrites nothing;
    a stale-kehtiv file gets refreshed; --from-manifest reproduces the
    same outputsAll."""

    def _wire(self, tmp_path, monkeypatch, *, titles_to_xml: dict[str, str]):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)
        monkeypatch.setattr(generate_all_laws, "MULTIPART_LAWS", set())
        # No network: every per-act fetch comes from the on-disk cache.
        monkeypatch.setattr(
            generate_all_laws.requests, "get",
            lambda *a, **kw: pytest.fail("no network expected"),
        )

        laws: dict[str, dict] = {}
        for i, (title, xml) in enumerate(sorted(titles_to_xml.items()), start=1):
            slug = generate_all_laws.slugify(title)
            tid = str(1000 + i)
            laws[title] = {
                "gid": str(2000 + i), "tid": tid,
                "url": f"/akt/{slug}.xml", "lyhend": f"L{i}",
            }
            # Write the tid-keyed cache file so fetch_xml hits it.
            (data_dir / f"{slug}__tid{tid}.xml").write_text(xml, encoding="utf-8")

        def fake_get_all_laws(**kwargs):
            return ({k: dict(v) for k, v in laws.items()}, {"complete": True})
        monkeypatch.setattr(generate_all_laws, "get_all_laws", fake_get_all_laws)
        return krr

    def test_clean_run_then_missing_only_is_noop(self, tmp_path, monkeypatch):
        krr = self._wire(tmp_path, monkeypatch, titles_to_xml={
            "Esimene seadus": _padded_law_xml(paragraphs=[1, 2]),
            "Teine seadus": _padded_law_xml(paragraphs=[1]),
        })
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _RerunArgs())
        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        peeps = sorted(krr.glob("*_peep.json"))
        assert len(peeps) == 2
        before = {p: p.read_bytes() for p in peeps}
        first_manifest = json.loads((krr / "generation_manifest_laws.json").read_text("utf-8"))
        assert first_manifest["run"]["newlyGenerated"] == 2

        # Second run, same mode — nothing should be rewritten.
        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()
        after = {p: p.read_bytes() for p in sorted(krr.glob("*_peep.json"))}
        assert after == before, "missing-only re-run must not rewrite up-to-date files"

        second_manifest = json.loads((krr / "generation_manifest_laws.json").read_text("utf-8"))
        assert second_manifest["run"]["newlyGenerated"] == 0
        assert second_manifest["counts"]["alreadyMapped"] == 2
        # outputsAll is stable across runs (status differs: skipped vs full,
        # but every act is present both times).
        assert {e["slug"] for e in second_manifest["outputsAll"]} == {
            generate_all_laws.slugify("Esimene seadus"),
            generate_all_laws.slugify("Teine seadus"),
        }

    def test_stale_kehtiv_file_is_refreshed(self, tmp_path, monkeypatch):
        krr = self._wire(tmp_path, monkeypatch, titles_to_xml={
            "Esimene seadus": _padded_law_xml(paragraphs=[1]),
        })
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _RerunArgs())
        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()
        slug = generate_all_laws.slugify("Esimene seadus")
        peep = krr / f"{slug}_peep.json"
        assert peep.exists()
        # Tamper: overwrite the stored kehtiv with an old snapshot date.
        doc = json.loads(peep.read_text("utf-8"))
        doc["@graph"][0]["estleg:kehtiv"] = {"@value": "2024-01-01", "@type": "xsd:date"}
        peep.write_text(json.dumps(doc), encoding="utf-8")

        # Re-run missing-only: the stale file must be refreshed back to the
        # current --kehtiv.
        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()
        refreshed = json.loads(peep.read_text("utf-8"))
        assert refreshed["@graph"][0]["estleg:kehtiv"] == {
            "@value": generate_all_laws.DEFAULT_KEHTIV, "@type": "xsd:date"
        }
        manifest = json.loads((krr / "generation_manifest_laws.json").read_text("utf-8"))
        assert manifest["run"]["refreshedStale"] >= 1

    def test_from_manifest_reproduces_outputsAll(self, tmp_path, monkeypatch):
        krr = self._wire(tmp_path, monkeypatch, titles_to_xml={
            "Esimene seadus": _padded_law_xml(paragraphs=[1, 2]),
            "Teine seadus": _padded_law_xml(paragraphs=[1]),
        })
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _RerunArgs())
        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()
        manifest_path = krr / "generation_manifest_laws.json"
        first = json.loads(manifest_path.read_text("utf-8"))
        first_acts = sorted(
            (e["title"], e["slug"], e["terviktekstId"], e["globaalId"])
            for e in first["outputsAll"]
        )

        # Now replay from the manifest — get_all_laws must NOT be consulted.
        monkeypatch.setattr(
            generate_all_laws, "get_all_laws",
            lambda **kw: pytest.fail("get_all_laws must not run during --from-manifest"),
        )

        class _ReplayArgs(_RerunArgs):
            from_manifest = str(manifest_path)
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _ReplayArgs())
        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()
        second = json.loads(manifest_path.read_text("utf-8"))
        second_acts = sorted(
            (e["title"], e["slug"], e["terviktekstId"], e["globaalId"])
            for e in second["outputsAll"]
        )
        assert second_acts == first_acts
        assert second["source"].get("replayedFromManifest") == str(manifest_path)


def _degraded_block_only_law_xml() -> str:
    """A >threshold law XML whose body carries block tags (peatykk/loige) but
    ZERO ``paragrahv`` — simulates a future RT paragraph-tag rename or a
    partial fetch. ``par_count == 0`` here must be treated as degraded, not as
    a genuinely body-less act (#594)."""
    pad = "<!-- " + ("x" * 1500) + " -->"
    return (
        f"<?xml version='1.0' encoding='utf-8'?>{pad}"
        f"<akt><sisu><peatykk><peatykkNr>1</peatykkNr>"
        f"<peatykkPealkiri>Peatykk</peatykkPealkiri>"
        f"<loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>"
        f"</peatykk></sisu></akt>"
    )


class TestStructuralStalenessGuardEndToEnd:
    """#594 — driving main() in ``force`` mode over a degraded parse of a
    previously-structured law must NOT overwrite the structured peep with a
    body-less stub; the act is recorded ``failed`` so a later run retries."""

    def test_degraded_parse_does_not_clobber_structured_peep(
        self, tmp_path, monkeypatch
    ):
        title = "Esimene seadus"
        krr = TestRerunNoOpAndStaleRefresh()._wire(
            tmp_path, monkeypatch,
            titles_to_xml={title: _padded_law_xml(paragraphs=[1, 2])},
        )
        slug = generate_all_laws.slugify(title)
        peep = krr / f"{slug}_peep.json"

        # Round 1: structured generation.
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _RerunArgs())
        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()
        assert peep.exists()
        assert generate_all_laws.existing_peep_is_structured(peep) is True
        structured_bytes = peep.read_bytes()

        # Replace the cached XML for this law with a degraded (block-only,
        # zero-paragraph) body, then force a regeneration.
        data_dir = generate_all_laws.DATA_DIR
        for cache in data_dir.glob(f"{slug}*.xml"):
            cache.write_text(_degraded_block_only_law_xml(), encoding="utf-8")

        class _ForceArgs(_RerunArgs):
            missing_only = False
            force = True

        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _ForceArgs())
        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        # The structured peep must be byte-for-byte untouched (no stub write).
        assert peep.read_bytes() == structured_bytes
        assert generate_all_laws.existing_peep_is_structured(peep) is True

        # The manifest records the act as failed (so missing-only retries),
        # not as a stub.
        manifest = json.loads(
            (krr / "generation_manifest_laws.json").read_text("utf-8")
        )
        statuses = {
            e["title"]: e.get("status")
            for e in manifest["outputsAll"]
            if e.get("title") == title
        }
        assert statuses.get(title) == "failed", statuses


class TestRegenState:
    """#213: long subsection-regeneration runs can resume per law."""

    def test_corrupt_regen_state_warns_and_resets(self, tmp_path, capsys):
        state_path = tmp_path / "regen_state.json"
        state_path.write_bytes(b"{not json")

        state = generate_all_laws.load_regen_state(state_path)

        captured = capsys.readouterr()
        assert f"WARNING: regen state {state_path} unreadable; starting fresh" in (
            captured.err
        )
        assert state == {
            "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
            "completed": {},
            "failed": {},
        }

    def test_completed_laws_are_recorded_and_skipped_on_resume(
        self, tmp_path, monkeypatch
    ):
        krr = TestRerunNoOpAndStaleRefresh()._wire(
            tmp_path,
            monkeypatch,
            titles_to_xml={
                "Esimene seadus": _padded_law_xml(paragraphs=[1, 2]),
                "Teine seadus": _padded_law_xml(paragraphs=[1]),
            },
        )
        state_path = tmp_path / "regen_state.json"

        class _Args(_RerunArgs):
            refresh = True
            missing_only = False
            regen_state = str(state_path)

        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())
        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        completed = state["completed"]
        assert set(completed) == {
            generate_all_laws.slugify("Esimene seadus"),
            generate_all_laws.slugify("Teine seadus"),
        }
        first_entry = completed[generate_all_laws.slugify("Esimene seadus")]
        assert first_entry["status"] == "full"
        assert first_entry["outputPaths"] == ["esimene_seadus_peep.json"]
        assert first_entry["subsectionCount"] == 2

        peeps = sorted(krr.glob("*_peep.json"))
        before = {p: p.read_bytes() for p in peeps}

        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        after = {p: p.read_bytes() for p in sorted(krr.glob("*_peep.json"))}
        assert after == before
        manifest = json.loads(
            (krr / "generation_manifest_laws.json").read_text("utf-8")
        )
        assert manifest["counts"]["selectedForGeneration"] == 0
        assert manifest["run"]["resumedCompleted"] == 2
        assert manifest["run"]["existingSkipped"] == 0
        assert {
            entry["reason"] for entry in manifest["outputsAll"]
        } == {"completed in regen state"}

    def test_state_write_is_atomic_on_dump_failure(self, tmp_path, monkeypatch):
        state_path = tmp_path / "regen_state.json"
        original = {"completed": {"old": {"status": "full"}}, "failed": {}}
        state_path.write_text(json.dumps(original), encoding="utf-8")

        def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated interrupted write")

        monkeypatch.setattr(generate_all_laws.json, "dump", _boom)
        with pytest.raises(RuntimeError, match="simulated interrupted write"):
            generate_all_laws.save_regen_state(
                state_path, {"completed": {"new": {"status": "full"}}, "failed": {}}
            )

        assert json.loads(state_path.read_text(encoding="utf-8")) == original
        assert not state_path.with_suffix(state_path.suffix + ".tmp").exists()

    def test_multipart_partial_write_records_failed_entry(self, tmp_path, monkeypatch):
        title = "Multipart seadus"
        slug = generate_all_laws.slugify(title)
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)
        monkeypatch.setattr(generate_all_laws, "MULTIPART_LAWS", {title})
        monkeypatch.setattr(
            generate_all_laws.requests,
            "get",
            lambda *a, **kw: pytest.fail("no network expected"),
        )
        (data_dir / f"{slug}__tid1001.xml").write_text(
            _padded_multipart_law_xml(),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            generate_all_laws,
            "get_all_laws",
            lambda **kwargs: (
                {title: {"gid": "2001", "tid": "1001", "url": f"/akt/{slug}.xml", "lyhend": "MULT"}},
                {"complete": True},
            ),
        )
        state_path = tmp_path / "regen_state.json"

        class _Args(_RerunArgs):
            refresh = True
            missing_only = False
            regen_state = str(state_path)

        original_write = generate_all_laws.write_law_output
        calls: list[str] = []

        def flaky_write(path, doc, **kwargs):
            calls.append(path.name)
            if len(calls) == 2:
                raise RuntimeError("simulated second-part write failure")
            return original_write(path, doc, **kwargs)

        monkeypatch.setattr(generate_all_laws, "write_law_output", flaky_write)
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())
        generate_all_laws._used_prefixes.clear()

        generate_all_laws.main()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        failed_entry = state["failed"][slug]
        assert failed_entry["status"] == "failed"
        assert failed_entry["outputPaths"] == [f"{slug}_osa1_peep.json"]
        assert failed_entry["subsectionCount"] == 1
        assert "simulated second-part write failure" in failed_entry["reason"]
        assert slug not in state.get("completed", {})
        assert calls == [f"{slug}_osa1_peep.json", f"{slug}_osa2_peep.json"]

    def test_multipart_cleanup_failure_does_not_mark_law_failed(
        self, tmp_path, monkeypatch
    ):
        title = "Multipart seadus"
        slug = generate_all_laws.slugify(title)
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)
        monkeypatch.setattr(generate_all_laws, "MULTIPART_LAWS", {title})
        monkeypatch.setattr(
            generate_all_laws.requests,
            "get",
            lambda *a, **kw: pytest.fail("no network expected"),
        )
        (data_dir / f"{slug}__tid1001.xml").write_text(
            _padded_multipart_law_xml(),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            generate_all_laws,
            "get_all_laws",
            lambda **kwargs: (
                {title: {"gid": "2001", "tid": "1001", "url": f"/akt/{slug}.xml", "lyhend": "MULT"}},
                {"complete": True},
            ),
        )
        monkeypatch.setattr(
            generate_all_laws,
            "remove_obsolete_multipart_outputs",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                PermissionError("simulated cleanup lock")
            ),
        )
        state_path = tmp_path / "regen_state.json"

        class _Args(_RerunArgs):
            refresh = True
            missing_only = False
            regen_state = str(state_path)

        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())
        generate_all_laws._used_prefixes.clear()

        generate_all_laws.main()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        completed_entry = state["completed"][slug]
        assert completed_entry["status"] == "full"
        assert completed_entry["outputPaths"] == [
            f"{slug}_osa1_peep.json",
            f"{slug}_osa2_peep.json",
        ]
        assert slug not in state.get("failed", {})
        assert completed_entry["warnings"] == [
            "obsolete multipart cleanup failed: simulated cleanup lock"
        ]

    def test_multipart_generation_failure_records_no_partial_outputs(
        self, tmp_path, monkeypatch
    ):
        title = "Multipart seadus"
        slug = generate_all_laws.slugify(title)
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)
        monkeypatch.setattr(generate_all_laws, "MULTIPART_LAWS", {title})
        monkeypatch.setattr(
            generate_all_laws.requests,
            "get",
            lambda *a, **kw: pytest.fail("no network expected"),
        )
        (data_dir / f"{slug}__tid1001.xml").write_text(
            _padded_multipart_law_xml(),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            generate_all_laws,
            "get_all_laws",
            lambda **kwargs: (
                {title: {"gid": "2001", "tid": "1001", "url": f"/akt/{slug}.xml", "lyhend": "MULT"}},
                {"complete": True},
            ),
        )
        state_path = tmp_path / "regen_state.json"

        class _Args(_RerunArgs):
            refresh = True
            missing_only = False
            regen_state = str(state_path)

        monkeypatch.setattr(
            generate_all_laws,
            "generate_multipart_law",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("simulated pre-write failure")
            ),
        )
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())
        generate_all_laws._used_prefixes.clear()

        generate_all_laws.main()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        failed_entry = state["failed"][slug]
        assert failed_entry["status"] == "failed"
        assert failed_entry["outputPaths"] == []
        assert failed_entry["subsectionCount"] == 0
        assert "multipart generation failed: simulated pre-write failure" in (
            failed_entry["reason"]
        )

    def test_schema_version_mismatch_triggers_full_reset(self):
        state = {
            "schemaVersion": 0,
            "completed": {"vana_seadus": {"status": "full"}},
            "failed": {"nurjunud_seadus": {"status": "failed"}},
        }

        assert generate_all_laws.prune_completed_regen_state(
            state, {"Vana seadus": {"tid": "1"}}, kehtiv="2026-05-01"
        ) == set()
        assert state["schemaVersion"] == generate_all_laws.REGEN_STATE_SCHEMA_VERSION
        assert state["completed"] == {}
        assert state["failed"] == {}
        assert state["droppedCompleted"][-1]["entries"] == {
            "vana_seadus": "schemaVersion changed"
        }

    def test_schema_version_mismatch_round_trips_through_disk(self, tmp_path):
        state_path = tmp_path / "regen_state.json"
        state_path.write_text(
            json.dumps({
                "schemaVersion": 0,
                "completed": {"vana_seadus": {"status": "full"}},
                "failed": {"nurjunud_seadus": {"status": "failed"}},
            }),
            encoding="utf-8",
        )

        state = generate_all_laws.load_regen_state(state_path)

        assert generate_all_laws.prune_completed_regen_state(
            state, {"Vana seadus": {"tid": "1"}}, kehtiv="2026-05-01"
        ) == set()
        generate_all_laws.save_regen_state(state_path, state)
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert saved["schemaVersion"] == generate_all_laws.REGEN_STATE_SCHEMA_VERSION
        assert saved["completed"] == {}
        assert saved["failed"] == {}
        assert saved["droppedCompleted"][-1]["entries"] == {
            "vana_seadus": "schemaVersion changed"
        }

    def test_zero_subsection_warning_appended(self, tmp_path, monkeypatch):
        title = "Hoiatus seadus"
        slug = generate_all_laws.slugify(title)
        krr = TestRerunNoOpAndStaleRefresh()._wire(
            tmp_path,
            monkeypatch,
            titles_to_xml={title: _padded_law_xml(paragraphs=[1])},
        )
        state_path = tmp_path / "regen_state.json"

        class _Args(_RerunArgs):
            refresh = True
            missing_only = False
            regen_state = str(state_path)

        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())
        monkeypatch.setattr(generate_all_laws, "build_subsections", lambda *args, **kwargs: [])
        generate_all_laws._used_prefixes.clear()

        generate_all_laws.main()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        entry = state["completed"][slug]
        assert entry["subsectionCount"] == 0
        assert entry["warnings"] == [
            "source XML has loige elements but emitted no Subsection nodes"
        ]
        assert (krr / f"{slug}_peep.json").exists()

    def test_completed_state_must_match_kehtiv_and_terviktekst_id(self):
        old_slug = generate_all_laws.slugify("Vana seadus")
        stale_tid_slug = generate_all_laws.slugify("Uuenenud seadus")
        current_slug = generate_all_laws.slugify("Kehtiv seadus")
        state = {
            "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
            "completed": {
                old_slug: {
                    "kehtiv": "2026-05-01",
                    "terviktekstId": "111",
                    "status": "full",
                },
                stale_tid_slug: {
                    "kehtiv": "2027-01-01",
                    "terviktekstId": "222",
                    "status": "full",
                },
                current_slug: {
                    "kehtiv": "2027-01-01",
                    "terviktekstId": "333",
                    "status": "full",
                },
            },
            "failed": {},
        }
        all_laws = {
            "Vana seadus": {"tid": "111"},
            "Uuenenud seadus": {"tid": "222-new"},
            "Kehtiv seadus": {"tid": "333"},
        }

        assert generate_all_laws.completed_regen_slugs(
            state, all_laws, kehtiv="2027-01-01"
        ) == {current_slug}
        assert set(state["completed"]) == {old_slug, stale_tid_slug, current_slug}

        assert generate_all_laws.prune_completed_regen_state(
            state, all_laws, kehtiv="2027-01-01"
        ) == {current_slug}
        assert set(state["completed"]) == {current_slug}
        assert state["droppedCompleted"][-1]["entries"] == {
            old_slug: "kehtiv changed",
            stale_tid_slug: "terviktekstId changed",
        }

    def test_completed_regen_slugs_is_pure_getter(self):
        slug = generate_all_laws.slugify("Kehtiv seadus")
        state = {
            "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
            "completed": {
                slug: {
                    "title": "Kehtiv seadus",
                    "kehtiv": "2027-01-01",
                    "terviktekstId": "333",
                    "status": "full",
                },
                "stale": {
                    "title": "Stale seadus",
                    "kehtiv": "2026-01-01",
                    "terviktekstId": "111",
                    "status": "full",
                },
            },
            "failed": {},
        }
        before = copy.deepcopy(state)

        assert generate_all_laws.completed_regen_slugs(
            state,
            {"Kehtiv seadus": {"tid": "333"}},
            kehtiv="2027-01-01",
        ) == {slug}
        assert state == before

    def test_invalid_completed_entry_records_stale_reason(self):
        state = {
            "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
            "completed": {"katki": "not a row"},
            "failed": {},
        }

        assert generate_all_laws.prune_completed_regen_state(
            state,
            {"Kehtiv seadus": {"tid": "333"}},
            kehtiv="2027-01-01",
        ) == set()

        assert state["completed"] == {}
        assert state["droppedCompleted"][-1]["entries"] == {
            "katki": "invalid completed entry"
        }

    def test_completed_state_rejects_slug_collision_title_mismatch(self):
        base = "b" * 80
        registered_title = f"{base} registered"
        other_title = f"{base} other"
        slug_by_title = generate_all_laws.build_law_slug_map(
            {
                other_title: {"tid": "222"},
                registered_title: {"tid": "111"},
            },
            registry={
                base: {
                    "abbrev": "BLONG",
                    "title": registered_title,
                }
            },
        )
        state = {
            "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
            "completed": {
                base: {
                    "title": other_title,
                    "kehtiv": "2027-01-01",
                    "terviktekstId": "222",
                    "status": "full",
                },
                f"{base}_2": {
                    "title": other_title,
                    "kehtiv": "2027-01-01",
                    "terviktekstId": "222",
                    "status": "full",
                },
            },
            "failed": {},
        }
        all_laws = {
            other_title: {"tid": "222"},
            registered_title: {"tid": "111"},
        }

        assert generate_all_laws.completed_regen_slugs(
            state,
            all_laws,
            kehtiv="2027-01-01",
            slug_by_title=slug_by_title,
        ) == {f"{base}_2"}

        assert generate_all_laws.prune_completed_regen_state(
            state,
            all_laws,
            kehtiv="2027-01-01",
            slug_by_title=slug_by_title,
        ) == {f"{base}_2"}
        assert set(state["completed"]) == {f"{base}_2"}
        assert state["droppedCompleted"][-1]["entries"] == {
            base: "title changed"
        }

    def test_dropped_completed_history_is_appended(self):
        old_slug = generate_all_laws.slugify("Vana seadus")
        stale_tid_slug = generate_all_laws.slugify("Uuenenud seadus")
        state = {
            "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
            "completed": {
                old_slug: {
                    "kehtiv": "2026-05-01",
                    "terviktekstId": "111",
                    "status": "full",
                },
                stale_tid_slug: {
                    "kehtiv": "2027-01-01",
                    "terviktekstId": "222",
                    "status": "full",
                },
            },
            "failed": {},
            "droppedCompleted": [
                {
                    "at": "2026-05-24T00:00:00+00:00",
                    "entries": {"eelmine": "kehtiv changed"},
                }
            ],
        }

        generate_all_laws.prune_completed_regen_state(
            state,
            {
                "Vana seadus": {"tid": "111"},
                "Uuenenud seadus": {"tid": "222-new"},
            },
            kehtiv="2027-01-01",
        )

        assert len(state["droppedCompleted"]) == 2
        assert state["droppedCompleted"][0]["entries"] == {
            "eelmine": "kehtiv changed"
        }
        assert state["droppedCompleted"][1]["entries"] == {
            old_slug: "kehtiv changed",
            stale_tid_slug: "terviktekstId changed",
        }

    def test_legacy_dropped_completed_dict_is_normalized_before_append(self):
        old_slug = generate_all_laws.slugify("Vana seadus")
        state = {
            "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
            "completed": {
                old_slug: {
                    "kehtiv": "2026-05-01",
                    "terviktekstId": "111",
                    "status": "full",
                },
            },
            "failed": {},
            "droppedCompleted": {
                "at": "2026-05-24T00:00:00+00:00",
                "entries": {"eelmine": "kehtiv changed"},
            },
        }

        generate_all_laws.prune_completed_regen_state(
            state,
            {"Vana seadus": {"tid": "111"}},
            kehtiv="2027-01-01",
        )

        assert isinstance(state["droppedCompleted"], list)
        assert len(state["droppedCompleted"]) == 2
        assert state["droppedCompleted"][0]["entries"] == {
            "eelmine": "kehtiv changed"
        }
        assert state["droppedCompleted"][1]["entries"] == {
            old_slug: "kehtiv changed"
        }

    def test_malformed_legacy_dropped_completed_dict_is_not_preserved(self):
        old_slug = generate_all_laws.slugify("Vana seadus")
        state = {
            "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
            "completed": {
                old_slug: {
                    "kehtiv": "2026-05-01",
                    "terviktekstId": "111",
                    "status": "full",
                },
            },
            "failed": {},
            "droppedCompleted": {"note": "user edited"},
        }

        generate_all_laws.prune_completed_regen_state(
            state,
            {"Vana seadus": {"tid": "111"}},
            kehtiv="2027-01-01",
        )

        assert len(state["droppedCompleted"]) == 1
        assert state["droppedCompleted"][0]["entries"] == {
            old_slug: "kehtiv changed"
        }

    def test_default_regen_state_path_resolves_after_krr_monkeypatch(
        self, tmp_path, monkeypatch
    ):
        krr = tmp_path / "krr_outputs"
        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)

        assert generate_all_laws._regen_state_path(
            generate_all_laws.DEFAULT_REGEN_STATE_SENTINEL
        ) == krr / generate_all_laws.DEFAULT_REGEN_STATE_NAME

    def test_reset_regen_state_discards_prior_completed_entries(
        self, tmp_path, monkeypatch
    ):
        title = "Esimene seadus"
        slug = generate_all_laws.slugify(title)
        krr = TestRerunNoOpAndStaleRefresh()._wire(
            tmp_path,
            monkeypatch,
            titles_to_xml={title: _padded_law_xml(paragraphs=[1])},
        )
        state_path = tmp_path / "regen_state.json"
        state_path.write_text(
            json.dumps({
                "schemaVersion": generate_all_laws.REGEN_STATE_SCHEMA_VERSION,
                "completed": {
                    slug: {
                        "kehtiv": generate_all_laws.DEFAULT_KEHTIV,
                        "terviktekstId": "1001",
                        "status": "full",
                    }
                },
                "failed": {},
            }),
            encoding="utf-8",
        )

        class _Args(_RerunArgs):
            refresh = True
            missing_only = False
            regen_state = str(state_path)
            reset_regen_state = True

        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())
        generate_all_laws._used_prefixes.clear()

        generate_all_laws.main()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert set(state["completed"]) == {slug}
        assert state["completed"][slug]["subsectionCount"] == 1
        manifest = json.loads(
            (krr / "generation_manifest_laws.json").read_text("utf-8")
        )
        assert manifest["run"]["resumedCompleted"] == 0

    def test_reset_regen_state_requires_regen_state(self, monkeypatch):
        class _Args(_RerunArgs):
            regen_state = None
            reset_regen_state = True

        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())

        with pytest.raises(SystemExit) as excinfo:
            generate_all_laws.main()

        assert excinfo.value.code == 2

    def test_reset_regen_state_reports_unlink_errors(self, monkeypatch, capsys):
        class _Args(_RerunArgs):
            regen_state = "ignored.json"
            reset_regen_state = True

        class _UnlinkFails:
            def unlink(self):
                raise PermissionError("denied")

            def __str__(self):
                return "/tmp/regen_state.json"

        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _Args())
        monkeypatch.setattr(
            generate_all_laws,
            "_regen_state_path",
            lambda _value: _UnlinkFails(),
        )

        with pytest.raises(SystemExit) as excinfo:
            generate_all_laws.main()

        assert excinfo.value.code == 2
        assert "FATAL: cannot reset regen state: denied" in capsys.readouterr().err


class TestActStatusInManifest:
    """#119: each outputsAll entry carries a per-act status (full / stub /
    failed / skipped) and stub acts get a reason."""

    def test_full_stub_and_failed_statuses_recorded(self, tmp_path, monkeypatch):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        monkeypatch.setattr(generate_all_laws, "KRR_DIR", krr)
        monkeypatch.setattr(generate_all_laws, "DATA_DIR", data_dir)
        monkeypatch.setattr(generate_all_laws, "MULTIPART_LAWS", set())

        # Full law: cached XML with a paragraph.
        (data_dir / "full_seadus__tid1001.xml").write_text(
            _padded_law_xml(paragraphs=[1]), encoding="utf-8",
        )
        # Stub law: cached XML with no paragraphs (but >1000 bytes so the
        # cache is used).
        pad = "<!-- " + ("x" * 1500) + " -->"
        (data_dir / "treaty_seadus__tid1002.xml").write_text(
            f"<?xml version='1.0'?>{pad}<akt><sisu /></akt>", encoding="utf-8",
        )
        # Failed law: no cached XML; the (only) network call we expect is
        # the per-act XML fetch for "Missing seadus", which returns a
        # failing response so fetch_xml -> None -> status "failed".
        class _FailResp:
            def raise_for_status(self):
                raise RuntimeError("404")
        monkeypatch.setattr(
            generate_all_laws.requests, "get", lambda *a, **kw: _FailResp(),
        )

        def fake_get_all_laws(**kwargs):
            return ({
                "Full seadus": {"gid": "2001", "tid": "1001",
                                "url": "/akt/full_seadus.xml", "lyhend": "FS"},
                "Treaty seadus": {"gid": "2002", "tid": "1002",
                                  "url": "/akt/treaty_seadus.xml", "lyhend": "TS"},
                "Missing seadus": {"gid": "2003", "tid": "1003",
                                   "url": "/akt/missing_seadus.xml", "lyhend": "MS"},
            }, {"complete": True})
        monkeypatch.setattr(generate_all_laws, "get_all_laws", fake_get_all_laws)
        monkeypatch.setattr(generate_all_laws, "parse_args", lambda: _RerunArgs())

        generate_all_laws._used_prefixes.clear()
        generate_all_laws.main()

        manifest = json.loads((krr / "generation_manifest_laws.json").read_text("utf-8"))
        by_slug = {e["slug"]: e for e in manifest["outputsAll"]}
        assert by_slug[generate_all_laws.slugify("Full seadus")]["status"] == "full"
        stub_entry = by_slug[generate_all_laws.slugify("Treaty seadus")]
        assert stub_entry["status"] == "stub"
        assert "reason" in stub_entry
        failed_entry = by_slug[generate_all_laws.slugify("Missing seadus")]
        assert failed_entry["status"] == "failed"
        assert manifest["counts"]["stubActs"] == 1
        assert manifest["counts"]["failedActs"] == 1
        assert manifest["counts"]["fullActs"] == 1
        # The stub file on disk carries the noStructuredBody marker.
        stub_peep = json.loads(
            (krr / f"{generate_all_laws.slugify('Treaty seadus')}_peep.json").read_text("utf-8")
        )
        assert stub_peep["@graph"][0]["estleg:contentStatus"] == "noStructuredBody"
        assert stub_peep["@graph"][0]["estleg:kehtiv"] == {
            "@value": generate_all_laws.DEFAULT_KEHTIV, "@type": "xsd:date"
        }


# ---------------------------------------------------------------------------
# Issue #132 — estleg:Subsection (lõige) nodes
# ---------------------------------------------------------------------------


def _subsection_nodes(graph: list[dict]) -> list[dict]:
    return [
        n
        for n in graph
        if isinstance(n, dict)
        and isinstance(n.get("@type"), list)
        and "estleg:Subsection" in n["@type"]
    ]


class TestSubsectionEmission:
    """#132: a paragrahv with lõige children emits one estleg:Subsection
    node per lõige; the § node carries estleg:hasSubsection listing them
    in document order; a paragrahv with no lõiked emits neither."""

    def _three_loige_law(self) -> dict:
        xml = """
        <akt><sisu><peatykk>
          <peatykkNr>1</peatykkNr><peatykkPealkiri>Peatykk</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>14</paragrahvNr>
            <kuvatavNr>§ 14. </kuvatavNr>
            <paragrahvPealkiri>Teovõime</paragrahvPealkiri>
            <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
              <tavatekst>Esimene loige tekst siin.</tavatekst></loige>
            <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
              <tavatekst>Teine loige paneb piiri.</tavatekst></loige>
            <loige><loigeNr>3</loigeNr><kuvatavNr>(3)</kuvatavNr>
              <tavatekst>Kolmas loige tekst.</tavatekst></loige>
          </paragrahv>
          <paragrahv>
            <paragrahvNr>15</paragrahvNr>
            <kuvatavNr>§ 15. </kuvatavNr>
            <paragrahvPealkiri>Üks lause</paragrahvPealkiri>
            <tavatekst>Lihtsalt tekst ilma loigeteta siin.</tavatekst>
          </paragrahv>
        </peatykk></sisu></akt>
        """
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        return generate_all_laws.generate_law_jsonld(
            "Test seadus", "test_seadus", root, abbreviation="TSTS",
            kehtiv="2026-05-01", allocator=alloc,
        )

    def test_three_loige_emits_three_subsection_nodes(self):
        doc = self._three_loige_law()
        subs = _subsection_nodes(doc["@graph"])
        sub_ids = [s["@id"] for s in subs]
        assert sub_ids == [
            "estleg:TSTS_Par_14_Lg_1",
            "estleg:TSTS_Par_14_Lg_2",
            "estleg:TSTS_Par_14_Lg_3",
        ]
        for s, n in zip(subs, ("1", "2", "3")):
            assert s["@type"] == ["estleg:Subsection", "owl:NamedIndividual"]
            assert s["estleg:subsectionNumber"] == n
            # legalText is a plain (xsd:string) literal carrying the (N) marker.
            assert isinstance(s["estleg:legalText"], str)
            assert s["estleg:legalText"].startswith(f"({n}) ")
            assert s["estleg:parentProvision"] == {"@id": "estleg:TSTS_Par_14"}
            assert s["rdfs:label"] == f"§ 14 lg {n}"

    def test_provision_has_subsection_list_in_order(self):
        doc = self._three_loige_law()
        par14 = next(
            n for n in doc["@graph"] if n.get("@id") == "estleg:TSTS_Par_14"
        )
        assert par14["estleg:hasSubsection"] == [
            {"@id": "estleg:TSTS_Par_14_Lg_1"},
            {"@id": "estleg:TSTS_Par_14_Lg_2"},
            {"@id": "estleg:TSTS_Par_14_Lg_3"},
        ]
        # The provision keeps its full concatenated legalText (the sum of
        # the subsections) for backward compatibility.
        full = par14["estleg:legalText"]
        assert "(1)" in full and "(2)" in full and "(3)" in full

    def test_paragraph_without_loige_has_no_subsections(self):
        doc = self._three_loige_law()
        par15 = next(
            n for n in doc["@graph"] if n.get("@id") == "estleg:TSTS_Par_15"
        )
        assert "estleg:hasSubsection" not in par15
        # No Subsection node points at § 15.
        for s in _subsection_nodes(doc["@graph"]):
            assert s["estleg:parentProvision"]["@id"] != "estleg:TSTS_Par_15"

    def test_superscript_loige_id_and_number(self):
        """§ 14 lg 2¹ → Subsection @id ..._Par_14_Lg_2_1, subsectionNumber
        is the display form "2¹"."""
        xml = """
        <akt><sisu><peatykk>
          <peatykkNr>1</peatykkNr><peatykkPealkiri>P</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>14</paragrahvNr><kuvatavNr>§ 14. </kuvatavNr>
            <paragrahvPealkiri>T</paragrahvPealkiri>
            <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
              <tavatekst>Teine loige.</tavatekst></loige>
            <loige><loigeNr ylaIndeks="1">2</loigeNr><kuvatavNr>(2¹)</kuvatavNr>
              <tavatekst>Lõike kaks ülaindeks üks tekst.</tavatekst></loige>
          </paragrahv>
        </peatykk></sisu></akt>
        """
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        doc = generate_all_laws.generate_law_jsonld(
            "Test", "test_sup", root, abbreviation="TSUPX", allocator=alloc,
        )
        subs = {s["@id"]: s for s in _subsection_nodes(doc["@graph"])}
        assert "estleg:TSUPX_Par_14_Lg_2" in subs
        assert "estleg:TSUPX_Par_14_Lg_2_1" in subs
        assert subs["estleg:TSUPX_Par_14_Lg_2_1"]["estleg:subsectionNumber"] == "2¹"
        # And both are listed on the provision, in order.
        par14 = next(
            n for n in doc["@graph"] if n.get("@id") == "estleg:TSUPX_Par_14"
        )
        assert par14["estleg:hasSubsection"] == [
            {"@id": "estleg:TSUPX_Par_14_Lg_2"},
            {"@id": "estleg:TSUPX_Par_14_Lg_2_1"},
        ]

    def test_parent_provision_resolves_to_a_real_provision(self):
        """Every Subsection's estleg:parentProvision equals an actual
        LegalProvision @id in the same graph — no dangling refs."""
        doc = self._three_loige_law()
        provision_ids = {
            n["@id"]
            for n in doc["@graph"]
            if isinstance(n, dict)
            and isinstance(n.get("@type"), list)
            and any(t.startswith("estleg:LegalProvision") for t in n["@type"] if isinstance(t, str))
        }
        assert provision_ids  # sanity
        for s in _subsection_nodes(doc["@graph"]):
            assert s["estleg:parentProvision"]["@id"] in provision_ids

    def test_empty_loige_does_not_emit_a_subsection(self):
        """A lõige with no text fragments (e.g. a repealed placeholder)
        is skipped — no Subsection node, and it is absent from
        estleg:hasSubsection. SHACL requires estleg:legalText, so an
        empty Subsection would be invalid."""
        xml = """
        <akt><sisu><peatykk>
          <peatykkNr>1</peatykkNr><peatykkPealkiri>P</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>7</paragrahvNr><kuvatavNr>§ 7. </kuvatavNr>
            <paragrahvPealkiri>T</paragrahvPealkiri>
            <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
              <tavatekst>Esimene loige on olemas.</tavatekst></loige>
            <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr></loige>
            <loige><loigeNr>3</loigeNr><kuvatavNr>(3)</kuvatavNr>
              <tavatekst>Kolmas loige on ka olemas.</tavatekst></loige>
          </paragrahv>
        </peatykk></sisu></akt>
        """
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        doc = generate_all_laws.generate_law_jsonld(
            "Test", "test_empty", root, abbreviation="TEMPX", allocator=alloc,
        )
        sub_ids = [s["@id"] for s in _subsection_nodes(doc["@graph"])]
        assert sub_ids == ["estleg:TEMPX_Par_7_Lg_1", "estleg:TEMPX_Par_7_Lg_3"]
        par7 = next(
            n for n in doc["@graph"] if n.get("@id") == "estleg:TEMPX_Par_7"
        )
        assert par7["estleg:hasSubsection"] == [
            {"@id": "estleg:TEMPX_Par_7_Lg_1"},
            {"@id": "estleg:TEMPX_Par_7_Lg_3"},
        ]

    def test_subsection_ids_unique_within_a_law(self):
        """All Subsection @ids in one law file are distinct (the
        generator asserts this; a collision raises ValueError)."""
        doc = self._three_loige_law()
        sub_ids = [s["@id"] for s in _subsection_nodes(doc["@graph"])]
        assert len(sub_ids) == len(set(sub_ids))

    def test_repeated_subsection_suffixes_get_stable_duplicate_ids(self):
        """If the source XML repeats the same lõige number, keep both
        citable by suffixing the later duplicate."""
        # Two lõiked both resolving to suffix "2": one plain loigeNr=2,
        # one loigeNr=2 again (no ylaIndeks) — same id.
        xml = """
        <akt><sisu><peatykk>
          <peatykkNr>1</peatykkNr><peatykkPealkiri>P</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1. </kuvatavNr>
            <paragrahvPealkiri>T</paragrahvPealkiri>
            <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
              <tavatekst>Esimene tekst.</tavatekst></loige>
            <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
              <tavatekst>Teine tekst.</tavatekst></loige>
          </paragrahv>
        </peatykk></sisu></akt>
        """
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        doc = generate_all_laws.generate_law_jsonld(
            "Test", "test_dup", root, abbreviation="TDUPX", allocator=alloc,
        )

        subs = _subsection_nodes(doc["@graph"])
        assert [s["@id"] for s in subs] == [
            "estleg:TDUPX_Par_1_Lg_2",
            "estleg:TDUPX_Par_1_Lg_2_Dup2",
        ]
        par1 = next(
            node for node in doc["@graph"] if node.get("@id") == "estleg:TDUPX_Par_1"
        )
        assert par1["estleg:hasSubsection"] == [
            {"@id": "estleg:TDUPX_Par_1_Lg_2"},
            {"@id": "estleg:TDUPX_Par_1_Lg_2_Dup2"},
        ]

    def test_missing_loige_numbers_get_ordered_fallback_ids(self):
        """Some RT treaty acts contain textual lõiked with blank loigeNr and
        kuvatavNr fields. Keep them citable without colliding on Lg_Unknown."""
        xml = """
        <akt><sisu><peatykk>
          <peatykkNr>1</peatykkNr><peatykkPealkiri>P</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>2</paragrahvNr><kuvatavNr>§ 2. </kuvatavNr>
            <paragrahvPealkiri>T</paragrahvPealkiri>
            <loige><loigeNr></loigeNr><kuvatavNr></kuvatavNr>
              <tavatekst>Esimene tekst.</tavatekst></loige>
            <loige><tavatekst>Teine tekst.</tavatekst></loige>
          </paragrahv>
        </peatykk></sisu></akt>
        """
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        doc = generate_all_laws.generate_law_jsonld(
            "Test", "test_unknown", root, abbreviation="TUNKX", allocator=alloc,
        )

        subs = _subsection_nodes(doc["@graph"])
        assert [s["@id"] for s in subs] == [
            "estleg:TUNKX_Par_2_Lg_Unknown_1",
            "estleg:TUNKX_Par_2_Lg_Unknown_2",
        ]
        assert [s["estleg:subsectionNumber"] for s in subs] == [
            "Unknown_1",
            "Unknown_2",
        ]

    def test_multipart_subsection_ids_are_osa_scoped(self):
        """In a multipart law each Subsection IRI is namespaced by osa:
        estleg:<PREFIX>_Osa<N>_Par_<n>_Lg_<m>; the parent provision link
        points at the osa-scoped § IRI."""
        xml = """
        <akt><sisu>
          <osa><osaNr>1</osaNr><osaPealkiri>Üldosa</osaPealkiri>
            <paragrahv><paragrahvNr>5</paragrahvNr><kuvatavNr>§ 5. </kuvatavNr>
              <paragrahvPealkiri>T</paragrahvPealkiri>
              <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
                <tavatekst>Esimene osa1 loige tekst.</tavatekst></loige>
              <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
                <tavatekst>Teine osa1 loige tekst.</tavatekst></loige>
            </paragrahv>
          </osa>
          <osa><osaNr>2</osaNr><osaPealkiri>Eriosa</osaPealkiri>
            <paragrahv><paragrahvNr>10</paragrahvNr><kuvatavNr>§ 10. </kuvatavNr>
              <paragrahvPealkiri>T2</paragrahvPealkiri>
              <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
                <tavatekst>Osa2 loige tekst.</tavatekst></loige>
            </paragrahv>
          </osa>
        </sisu></akt>
        """
        root = ET.fromstring(xml)
        alloc = generate_all_laws.PrefixAllocator(registry={})
        results = generate_all_laws.generate_multipart_law(
            "Multi", "multi_test", root, abbreviation="MULTX", allocator=alloc,
        )
        by_file = dict(results)
        osa1_subs = [s["@id"] for s in _subsection_nodes(by_file["multi_test_osa1_peep.json"]["@graph"])]
        assert osa1_subs == [
            "estleg:MULTX_Osa1_Par_5_Lg_1",
            "estleg:MULTX_Osa1_Par_5_Lg_2",
        ]
        par5 = next(
            n for n in by_file["multi_test_osa1_peep.json"]["@graph"]
            if n.get("@id") == "estleg:MULTX_Osa1_Par_5"
        )
        assert par5["estleg:hasSubsection"] == [
            {"@id": "estleg:MULTX_Osa1_Par_5_Lg_1"},
            {"@id": "estleg:MULTX_Osa1_Par_5_Lg_2"},
        ]
        # Subsections of osa1 only point at osa1's provisions.
        for s in _subsection_nodes(by_file["multi_test_osa1_peep.json"]["@graph"]):
            assert s["estleg:parentProvision"]["@id"] == "estleg:MULTX_Osa1_Par_5"
        osa2_subs = [s["@id"] for s in _subsection_nodes(by_file["multi_test_osa2_peep.json"]["@graph"])]
        assert osa2_subs == ["estleg:MULTX_Osa2_Par_10_Lg_1"]


class TestSubsectionHelpers:
    """Unit coverage for the lõige-text and -number helpers."""

    def test_loige_numbers_plain(self):
        lg = ET.fromstring(
            "<loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>"
            "<tavatekst>x</tavatekst></loige>"
        )
        assert generate_all_laws._loige_numbers(lg) == ("2", "2")

    def test_loige_numbers_ylaindeks(self):
        lg = ET.fromstring(
            "<loige><loigeNr ylaIndeks='1'>2</loigeNr><kuvatavNr>(2¹)</kuvatavNr>"
            "<tavatekst>x</tavatekst></loige>"
        )
        assert generate_all_laws._loige_numbers(lg) == ("2¹", "2_1")

    def test_loige_numbers_unicode_superscript_in_kuvatavnr(self):
        # No ylaIndeks but kuvatavNr carries the superscript glyph.
        lg = ET.fromstring(
            "<loige><loigeNr>2</loigeNr><kuvatavNr>2²</kuvatavNr>"
            "<tavatekst>x</tavatekst></loige>"
        )
        display, suffix = generate_all_laws._loige_numbers(lg)
        assert display == "2²"
        assert suffix == "2_2"

    def test_loige_body_text_joins_and_filters(self):
        lg = ET.fromstring(
            "<loige><loigeNr>1</loigeNr>"
            "<lause>Esimene lause.</lause><lause>ok</lause>"
            "<tavatekst>Teine tekstiosa.</tavatekst></loige>"
        )
        # "ok" is <= 3 chars and is dropped.
        assert generate_all_laws._loige_body_text(lg) == "Esimene lause. Teine tekstiosa."

    def test_build_subsections_returns_empty_for_no_loige(self):
        par = ET.fromstring(
            "<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>"
            "<tavatekst>Pole loiget.</tavatekst></paragrahv>"
        )
        assert generate_all_laws.build_subsections(
            par, "estleg:X_Par_1", abbrev_prefix="X", par_suffix="1",
            paragraph_display="§ 1.",
        ) == []


# ---------------------------------------------------------------------------
# Issue #132 — vocabulary + SHACL coverage for the new Subsection model
# ---------------------------------------------------------------------------


class TestSubsectionVocabularyAndShapes:
    """The new estleg:Subsection class and its properties are present and
    correctly typed in the controlled vocabulary, and the SHACL shapes
    file declares estleg:SubsectionShape plus the optional hasSubsection
    property shape on LegalProvisionShape."""

    def _vocab(self) -> dict:
        path = (
            generate_all_laws.REPO_ROOT
            / "krr_outputs"
            / "controlled_vocabulary.jsonld"
        )
        return json.loads(path.read_text(encoding="utf-8"))

    def test_subsection_class_and_properties_defined_and_typed(self):
        vocab = self._vocab()
        by_id = {n["@id"]: n for n in vocab["@graph"] if isinstance(n, dict) and "@id" in n}
        assert "owl:Class" in by_id["estleg:Subsection"]["@type"]
        assert "owl:DatatypeProperty" in by_id["estleg:subsectionNumber"]["@type"]
        assert by_id["estleg:subsectionNumber"]["rdfs:range"]["@id"] == "xsd:string"
        assert "owl:ObjectProperty" in by_id["estleg:hasSubsection"]["@type"]
        assert by_id["estleg:hasSubsection"]["rdfs:range"]["@id"] == "estleg:Subsection"
        assert by_id["estleg:hasSubsection"]["owl:inverseOf"]["@id"] == "estleg:parentProvision"
        assert "owl:ObjectProperty" in by_id["estleg:parentProvision"]["@type"]
        assert by_id["estleg:parentProvision"]["rdfs:range"]["@id"] == "estleg:LegalProvision"
        assert by_id["estleg:parentProvision"]["owl:inverseOf"]["@id"] == "estleg:hasSubsection"
        # Every term carries a label and a comment.
        for term in ("estleg:Subsection", "estleg:subsectionNumber",
                     "estleg:hasSubsection", "estleg:parentProvision"):
            assert by_id[term].get("rdfs:label")
            assert by_id[term].get("rdfs:comment")

    def test_shacl_declares_subsection_shape_and_property(self):
        ttl = (
            generate_all_laws.REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"
        ).read_text(encoding="utf-8")
        assert "estleg:SubsectionShape" in ttl
        assert "sh:targetClass estleg:Subsection" in ttl
        assert "estleg:subsectionNumber" in ttl
        assert "estleg:parentProvision" in ttl
        # hasSubsection is wired into LegalProvisionShape as an optional
        # (minCount 0) IRI-valued property.
        assert "estleg:hasSubsection" in ttl


_CONTEXT = generate_all_laws.CONTEXT


def _doc(*nodes: dict) -> dict:
    return {"@context": _CONTEXT, "@graph": list(nodes)}


def _well_formed_subsection() -> dict:
    return {
        "@id": "estleg:X_Par_14_Lg_2",
        "@type": ["estleg:Subsection", "owl:NamedIndividual"],
        "estleg:subsectionNumber": "2",
        "estleg:legalText": "(2) Lõike kaks tekst.",
        "estleg:parentProvision": {"@id": "estleg:X_Par_14"},
        "rdfs:label": {"@value": "§ 14 lg 2", "@language": "et"},
    }


class TestSubsectionShaclConformance:
    """#132: estleg:SubsectionShape accepts a well-formed Subsection and
    rejects one missing subsectionNumber / legalText / parentProvision;
    a LegalProvision with (and without) estleg:hasSubsection conforms."""

    def test_well_formed_subsection_conforms(self):
        ok, msg = _shacl_conforms(_doc(_well_formed_subsection()))
        assert ok, msg

    def test_subsection_without_label_conforms(self):
        node = _well_formed_subsection()
        del node["rdfs:label"]
        ok, msg = _shacl_conforms(_doc(node))
        assert ok, msg

    def test_subsection_missing_number_rejected(self):
        node = _well_formed_subsection()
        del node["estleg:subsectionNumber"]
        ok, _msg = _shacl_conforms(_doc(node))
        assert not ok

    def test_subsection_missing_legal_text_rejected(self):
        node = _well_formed_subsection()
        del node["estleg:legalText"]
        ok, _msg = _shacl_conforms(_doc(node))
        assert not ok

    def test_subsection_missing_parent_provision_rejected(self):
        node = _well_formed_subsection()
        del node["estleg:parentProvision"]
        ok, _msg = _shacl_conforms(_doc(node))
        assert not ok

    def test_subsection_parent_provision_literal_not_iri_rejected(self):
        node = _well_formed_subsection()
        node["estleg:parentProvision"] = {"@value": "estleg:X_Par_14"}
        ok, _msg = _shacl_conforms(_doc(node))
        assert not ok

    def test_provision_with_has_subsection_still_conforms(self):
        # estleg:summary on a provision is a plain (xsd:string) literal per
        # estleg:LegalProvisionShape; mirror that here so the test isolates
        # the optional estleg:hasSubsection property.
        provision = {
            "@id": "estleg:X_Par_14",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "estleg:paragrahv": "§ 14",
            "estleg:partOfAct": {"@id": "estleg:X_Map_2026"},
            "estleg:summary": "Teovõime.",
            "estleg:legalText": "(2) Lõike kaks tekst.",
            "estleg:hasSubsection": [{"@id": "estleg:X_Par_14_Lg_2"}],
        }
        ok, msg = _shacl_conforms(_doc(provision, _well_formed_subsection()))
        assert ok, msg

    def test_provision_without_has_subsection_still_conforms(self):
        provision = {
            "@id": "estleg:X_Par_15",
            "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
            "estleg:paragrahv": "§ 15",
            "estleg:partOfAct": {"@id": "estleg:X_Map_2026"},
            "estleg:summary": "Üks lause.",
            "estleg:legalText": "Üks lause vaid.",
        }
        ok, msg = _shacl_conforms(_doc(provision))
        assert ok, msg


# ---------------------------------------------------------------------------
# Issue #298 — collect_text must not duplicate lõige body text or leak
# <HTMLKonteiner> CDATA into estleg:summary (drop "loige" from the tag list).
# ---------------------------------------------------------------------------


class TestCollectTextNoLoigeDuplication:
    def test_multi_loige_body_appears_once(self):
        """A provision with several lõiked must list each body sentence
        exactly once — previously the ``loige`` container AND its leaf
        ``tavatekst`` both matched, doubling every sentence."""
        xml = """
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <kuvatavNr>§ 1.</kuvatavNr>
          <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
            <tavatekst>Alpha kohaldatakse siin.</tavatekst></loige>
          <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
            <tavatekst>Beeta kohaldatakse seal.</tavatekst></loige>
        </paragrahv>
        """
        summary = generate_all_laws.collect_text(ET.fromstring(xml))
        assert summary.count("Alpha kohaldatakse siin.") == 1, summary
        assert summary.count("Beeta kohaldatakse seal.") == 1, summary

    def test_htmlkonteiner_cdata_does_not_leak(self):
        """Raw signature-table / <a href> CDATA carried in an
        <HTMLKonteiner> sibling of the body must never reach the summary."""
        xml = """
        <paragrahv>
          <paragrahvNr>2</paragrahvNr>
          <kuvatavNr>§ 2.</kuvatavNr>
          <loige>
            <loigeNr>1</loigeNr>
            <tavatekst>Pärisõiguslik tekst kehtib.</tavatekst>
            <HTMLKonteiner><![CDATA[<a href="x">allkirjastatud tabel</a>]]></HTMLKonteiner>
          </loige>
        </paragrahv>
        """
        summary = generate_all_laws.collect_text(ET.fromstring(xml))
        assert "Pärisõiguslik tekst kehtib." in summary
        assert "allkirjastatud tabel" not in summary, summary
        assert "href" not in summary, summary

    def test_loige_text_still_captured_via_leaves(self):
        """Dropping ``loige`` from the match list must not lose the body —
        the leaf ``tavatekst`` descendants still carry it."""
        xml = """
        <paragrahv>
          <paragrahvNr>3</paragrahvNr>
          <loige><loigeNr>1</loigeNr><tavatekst>Säte on olemas.</tavatekst></loige>
        </paragrahv>
        """
        summary = generate_all_laws.collect_text(ET.fromstring(xml))
        assert "Säte on olemas." in summary

    def test_end_to_end_summary_has_no_repeat(self):
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <paragrahv>
            <paragrahvNr>1</paragrahvNr>
            <kuvatavNr>§ 1.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
              <tavatekst>Esimene siduv lause.</tavatekst></loige>
            <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
              <tavatekst>Teine siduv lause.</tavatekst></loige>
          </paragrahv>
        </sisu></akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Kordus", "kordus", ET.fromstring(xml), abbreviation="DUPX",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        prov = next(
            n for n in doc["@graph"] if n.get("@id") == "estleg:DUPX_Par_1"
        )
        assert prov["estleg:summary"].count("Esimene siduv lause.") == 1
        assert prov["estleg:summary"].count("Teine siduv lause.") == 1


# ---------------------------------------------------------------------------
# Issue #368 — estleg:summary must be cut on a sentence/word boundary, never
# mid-token, while still honouring the hard cap.
# ---------------------------------------------------------------------------


class TestSummaryBoundaryTruncation:
    def test_short_text_is_unchanged(self):
        assert generate_all_laws._truncate_at_boundary("Lühike.", 500) == "Lühike."

    def test_cut_prefers_sentence_boundary(self):
        body = "".join(f"Lause number {i} siin. " for i in range(1, 80))
        out = generate_all_laws._truncate_at_boundary(body, 500)
        assert len(out) <= 500
        # Ends on a sentence terminator, not mid-word.
        assert out.rstrip()[-1] in ".!?…", repr(out[-30:])

    def test_cut_falls_back_to_word_boundary(self):
        # No sentence terminator anywhere: must cut at the last space.
        body = "woord " * 200  # 1200 chars, spaces but no '.'
        out = generate_all_laws._truncate_at_boundary(body, 500)
        assert len(out) <= 500
        # Not severed mid-token: the char after the cut in the source is a space.
        assert not out.endswith("woor"), repr(out[-10:])
        assert out.endswith("woord"), repr(out[-10:])

    def test_no_boundary_in_window_honours_cap(self):
        body = "x" * 600  # no spaces, no terminators
        out = generate_all_laws._truncate_at_boundary(body, 500)
        assert len(out) == 500

    def test_collect_text_applies_boundary_cut(self):
        body = "".join(f"Pikk lause nummer {i}. " for i in range(1, 90))
        xml = (
            "<paragrahv><paragrahvNr>1</paragrahvNr>"
            f"<tavatekst>{body}</tavatekst></paragrahv>"
        )
        summary = generate_all_laws.collect_text(ET.fromstring(xml))
        assert len(summary) <= 500
        # A raw slice would sever a word; the boundary cut ends cleanly.
        assert summary.rstrip()[-1] in ".!?…", repr(summary[-30:])
        assert summary != body[:500]


# ---------------------------------------------------------------------------
# Issue #346 — slugify must strip a trailing '_' produced by the 80-char cut.
# ---------------------------------------------------------------------------


class TestSlugifyTrailingUnderscore:
    def test_eighty_char_cut_on_underscore_is_stripped(self):
        # 79 'a's then a separator then a new word -> slug[:80] ends with '_'.
        slug = generate_all_laws.slugify("a" * 79 + " bcd")
        assert not slug.endswith("_"), slug
        assert slug == "a" * 79

    def test_no_double_underscore_when_suffix_appended(self):
        slug = generate_all_laws.slugify("x" * 79 + " more text here")
        iri = f"estleg:{slug}_Map_2026"
        assert "__" not in iri, iri

    def test_short_slug_unaffected(self):
        assert generate_all_laws.slugify("Karistusseadustik") == "karistusseadustik"


# ---------------------------------------------------------------------------
# Issue #354 — §-range canonicalisation + Division/Chapter trailing underscore.
# ---------------------------------------------------------------------------


class TestSanitizeIdRangeCanonicalisation:
    def test_en_dash_range_becomes_to(self):
        assert generate_all_laws.sanitize_id("1–94") == "1_to_94"

    def test_em_dash_and_minus_also_canonicalised(self):
        assert generate_all_laws.sanitize_id("1—94") == "1_to_94"
        assert generate_all_laws.sanitize_id("1−94") == "1_to_94"

    def test_ascii_hyphen_unchanged_behaviour(self):
        # ASCII hyphen is NOT a §-range separator; preserve the prior
        # strip-to-nothing behaviour so unrelated ids do not shift.
        assert generate_all_laws.sanitize_id("a-b") == "ab"

    def test_plain_number_unchanged(self):
        assert generate_all_laws.sanitize_id("194") == "194"

    def test_range_provision_iri_is_distinct_from_real_section(self):
        """``§ 1–94`` must mint ``Par_1_to_94`` — not ``Par_194`` which would
        collide with a real §194."""
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <paragrahv>
            <paragrahvNr>1–94</paragrahvNr>
            <kuvatavNr>§ 1–94.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><tavatekst>Käesolev seadustik kehtib.</tavatekst></loige>
          </paragrahv>
        </sisu></akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Tsiviilkoodeks", "tsk", ET.fromstring(xml), abbreviation="TSKZ",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        ids = {n["@id"] for n in doc["@graph"]}
        assert "estleg:TSKZ_Par_1_to_94" in ids, ids
        assert "estleg:TSKZ_Par_194" not in ids


class TestStructuralSuffixTrailingUnderscore:
    def test_chapter_title_suffix_strips_trailing_underscore(self):
        ch = ET.fromstring("<peatykk><peatykkPealkiri>x</peatykkPealkiri></peatykk>")
        # 19 chars + a separator at index 20 of the title -> trailing '_'.
        suffix = generate_all_laws._chapter_id_suffix(
            ch, "", "ABCDEFGHIJKLMNOPQRS more"
        )
        assert not suffix.endswith("_"), suffix
        assert suffix == "ABCDEFGHIJKLMNOPQRS"

    def test_division_title_suffix_strips_trailing_underscore(self):
        jagu = ET.fromstring("<jagu><jaguPealkiri>x</jaguPealkiri></jagu>")
        suffix = generate_all_laws._division_id_suffix(
            jagu, "", "ABCDEFGHIJKLMNOPQRS more"
        )
        assert not suffix.endswith("_"), suffix
        assert suffix == "ABCDEFGHIJKLMNOPQRS"

    def test_title_only_division_does_not_collide_with_sibling(self):
        """A title that truncates onto an underscore must not near-collide
        with a sibling whose first 20 chars match exactly."""
        jagu_a = ET.fromstring("<jagu><jaguPealkiri>x</jaguPealkiri></jagu>")
        a = generate_all_laws._chapter_id_suffix(
            jagu_a, "", "TEOSE KASUTAMINE OSA toode"
        )
        b = generate_all_laws._chapter_id_suffix(
            jagu_a, "", "TEOSE KASUTAMINE OSA"
        )
        # Both collapse to the same canonical suffix (no spurious '_' tail on a).
        assert a == b
        assert not a.endswith("_")


# ---------------------------------------------------------------------------
# Issue #309 — multipart act @id must be snapshot-stable (no §-range).
# ---------------------------------------------------------------------------


class TestMultipartActIdStable:
    def _osa_xml(self, par_lo: int, par_hi: int) -> str:
        return f"""
        <akt><sisu>
          <osa><osaNr>1</osaNr><osaPealkiri>Üldosa</osaPealkiri>
            <paragrahv><paragrahvNr>{par_lo}</paragrahvNr><kuvatavNr>§ {par_lo}.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Esimene säte.</tavatekst></loige></paragrahv>
            <paragrahv><paragrahvNr>{par_hi}</paragrahvNr><kuvatavNr>§ {par_hi}.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Viimane säte.</tavatekst></loige></paragrahv>
          </osa>
        </sisu></akt>
        """

    def test_act_id_has_no_par_range(self):
        generate_all_laws._used_prefixes.clear()
        results = dict(generate_all_laws.generate_multipart_law(
            "Karistusseadustik", "kars",
            ET.fromstring(self._osa_xml(1, 87)),
            abbreviation="KARSY",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        ))
        act = results["kars_osa1_peep.json"]["@graph"][0]
        assert act["@id"] == "estleg:KARSY_Osa1", act["@id"]
        # The §-range survives only in the human-readable label.
        assert "1–87" in act["rdfs:label"], act["rdfs:label"]

    def test_act_id_stable_when_par_range_shifts(self):
        """Inserting/removing a paragraph shifts the §-range but must NOT
        change the act @id (the whole point of #309)."""
        generate_all_laws._used_prefixes.clear()
        a = dict(generate_all_laws.generate_multipart_law(
            "Karistusseadustik", "kars",
            ET.fromstring(self._osa_xml(1, 87)),
            abbreviation="KARSY",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        ))
        generate_all_laws._used_prefixes.clear()
        b = dict(generate_all_laws.generate_multipart_law(
            "Karistusseadustik", "kars",
            ET.fromstring(self._osa_xml(1, 90)),  # §-range now 1–90
            abbreviation="KARSY",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        ))
        id_a = a["kars_osa1_peep.json"]["@graph"][0]["@id"]
        id_b = b["kars_osa1_peep.json"]["@graph"][0]["@id"]
        assert id_a == id_b == "estleg:KARSY_Osa1"


# ---------------------------------------------------------------------------
# Issue #371 — unnamed <jagu> and pre-<peatykk> preamble paragraphs must get
# a container (and cluster). Both single-file and multipart code paths.
# ---------------------------------------------------------------------------


class TestUnnamedJaguAndPreambleContainer:
    def test_single_unnamed_jagu_paragraph_gets_chapter_container(self):
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
            <jagu>
              <paragrahv><paragrahvNr>5</paragrahvNr><kuvatavNr>§ 5.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Nimetu jao säte.</tavatekst></loige></paragrahv>
            </jagu>
          </peatykk>
        </sisu></akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Seadus", "seadus", ET.fromstring(xml), abbreviation="UNJX",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        p5 = next(n for n in doc["@graph"] if n.get("@id") == "estleg:UNJX_Par_5")
        assert p5["estleg:isPartOf"]["@id"].startswith("estleg:Chapter_UNJX_1"), p5
        assert p5.get("estleg:requestedCluster") is not None, p5

    def test_single_preamble_paragraph_gets_fallback_container(self):
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><tavatekst>Preambuli säte.</tavatekst></loige></paragrahv>
          <paragrahv><paragrahvNr>2</paragrahvNr><kuvatavNr>§ 2.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><tavatekst>Teine preambuli säte.</tavatekst></loige></paragrahv>
          <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Põhisätted</peatykkPealkiri>
            <paragrahv><paragrahvNr>3</paragrahvNr><kuvatavNr>§ 3.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Peatüki säte.</tavatekst></loige></paragrahv>
          </peatykk>
        </sisu></akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Eriõiguse seadus", "reos", ET.fromstring(xml), abbreviation="PRMB",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        graph = doc["@graph"]
        p1 = next(n for n in graph if n.get("@id") == "estleg:PRMB_Par_1")
        p2 = next(n for n in graph if n.get("@id") == "estleg:PRMB_Par_2")
        p3 = next(n for n in graph if n.get("@id") == "estleg:PRMB_Par_3")
        assert p1["estleg:isPartOf"]["@id"] == "estleg:Chapter_PRMB_Preamble"
        assert p2["estleg:isPartOf"]["@id"] == "estleg:Chapter_PRMB_Preamble"
        assert p1["estleg:requestedCluster"]["@id"] == "estleg:Cluster_PRMB_Preamble"
        # Real-chapter paragraph is untouched.
        assert p3["estleg:isPartOf"]["@id"] != "estleg:Chapter_PRMB_Preamble"
        assert p3["estleg:isPartOf"]["@id"].startswith("estleg:Chapter_PRMB_")

    def test_preamble_cluster_counted_and_is_top_concept(self):
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><tavatekst>Preambul üks.</tavatekst></loige></paragrahv>
          <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Sätted</peatykkPealkiri>
            <paragrahv><paragrahvNr>2</paragrahvNr><kuvatavNr>§ 2.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Säte.</tavatekst></loige></paragrahv>
          </peatykk>
        </sisu></akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Seadus", "seadus", ET.fromstring(xml), abbreviation="PTOP",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        graph = doc["@graph"]
        cluster = next(
            n for n in graph if n.get("@id") == "estleg:Cluster_PTOP_Preamble"
        )
        assert cluster["estleg:provisionCount"] == 1
        scheme = next(
            n for n in graph if n.get("@type") == ["skos:ConceptScheme"]
        )
        top_ids = {t["@id"] for t in scheme["skos:hasTopConcept"]}
        assert "estleg:Cluster_PTOP_Preamble" in top_ids, top_ids

    def test_no_preamble_node_when_every_paragraph_has_a_chapter(self):
        """A clean structuredBody law (all paragraphs inside chapters) must
        NOT sprout a spurious preamble container."""
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Sätted</peatykkPealkiri>
            <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Ainus.</tavatekst></loige></paragrahv>
          </peatykk>
        </sisu></akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Seadus", "seadus", ET.fromstring(xml), abbreviation="NOPR",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        ids = {n["@id"] for n in doc["@graph"]}
        assert "estleg:Chapter_NOPR_Preamble" not in ids
        assert "estleg:Cluster_NOPR_Preamble" not in ids

    def test_multipart_unnamed_jagu_paragraph_gets_chapter_container(self):
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <osa><osaNr>1</osaNr><osaPealkiri>Üldosa</osaPealkiri>
            <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
              <jagu>
                <paragrahv><paragrahvNr>5</paragrahvNr><kuvatavNr>§ 5.</kuvatavNr>
                  <loige><loigeNr>1</loigeNr><tavatekst>Nimetu jagu.</tavatekst></loige></paragrahv>
              </jagu>
            </peatykk>
          </osa>
        </sisu></akt>
        """
        results = dict(generate_all_laws.generate_multipart_law(
            "Multi", "multi_unj", ET.fromstring(xml), abbreviation="MUNJ",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        ))
        graph = results["multi_unj_osa1_peep.json"]["@graph"]
        prefix = graph[0]["@id"].split("estleg:")[1].rsplit("_Osa", 1)[0]
        p5 = next(
            n for n in graph if n.get("@id") == f"estleg:{prefix}_Osa1_Par_5"
        )
        assert p5["estleg:isPartOf"]["@id"].startswith(
            f"estleg:Chapter_{prefix}_1"
        ), p5
        assert p5.get("estleg:requestedCluster") is not None, p5

    def test_multipart_preamble_paragraph_gets_fallback_container(self):
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <osa><osaNr>2</osaNr><osaPealkiri>Eriosa</osaPealkiri>
            <paragrahv><paragrahvNr>208</paragrahvNr><kuvatavNr>§ 208.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Osa preambul.</tavatekst></loige></paragrahv>
            <peatykk><peatykkNr>20</peatykkNr><peatykkPealkiri>Lepingud</peatykkPealkiri>
              <paragrahv><paragrahvNr>209</paragrahvNr><kuvatavNr>§ 209.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Peatüki säte.</tavatekst></loige></paragrahv>
            </peatykk>
          </osa>
        </sisu></akt>
        """
        results = dict(generate_all_laws.generate_multipart_law(
            "Võlaõigusseadus", "vos_pre", ET.fromstring(xml), abbreviation="VOSP",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        ))
        graph = results["vos_pre_osa2_peep.json"]["@graph"]
        prefix = graph[0]["@id"].split("estleg:")[1].rsplit("_Osa", 1)[0]
        p208 = next(
            n for n in graph if n.get("@id") == f"estleg:{prefix}_Osa2_Par_208"
        )
        # Multipart cluster/chapter IRIs use the bare osa number, not "Osa<n>".
        assert p208["estleg:isPartOf"]["@id"] == f"estleg:Chapter_{prefix}_2_Preamble"
        assert (
            p208["estleg:requestedCluster"]["@id"]
            == f"estleg:Cluster_{prefix}_2_Preamble"
        )
        # The preamble cluster joins the part-level grouping.
        part = next(
            n for n in graph if n.get("@id") == f"estleg:Cluster_{prefix}_2_Part"
        )
        narrower = {x["@id"] for x in part.get("skos:narrower", [])}
        assert f"estleg:Cluster_{prefix}_2_Preamble" in narrower, narrower


# ---------------------------------------------------------------------------
# Issue #375 — a Chapter with BOTH direct-child provisions and Divisions must
# list both the Division ids and the direct provision ids in estleg:hasPart.
# ---------------------------------------------------------------------------


class TestChapterHasPartIncludesDirectProvisions:
    _XML = """
    <akt><sisu>
      <peatykk><peatykkNr>5</peatykkNr><peatykkPealkiri>Ravimid</peatykkPealkiri>
        <paragrahv><paragrahvNr>87</paragrahvNr><kuvatavNr>§ 87.</kuvatavNr>
          <loige><loigeNr>1</loigeNr><tavatekst>Otse peatüki all.</tavatekst></loige></paragrahv>
        <jagu><jaguNr>1</jaguNr><jaguPealkiri>Esimene jagu</jaguPealkiri>
          <paragrahv><paragrahvNr>88</paragrahvNr><kuvatavNr>§ 88.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><tavatekst>Jaos.</tavatekst></loige></paragrahv>
        </jagu>
      </peatykk>
    </sisu></akt>
    """

    def test_single_path_haspart_lists_division_and_direct_provision(self):
        generate_all_laws._used_prefixes.clear()
        doc = generate_all_laws.generate_law_jsonld(
            "Ravimiseadus", "ravs", ET.fromstring(self._XML), abbreviation="RVSX",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        graph = doc["@graph"]
        chapter = next(
            n for n in graph
            if n.get("@id", "").startswith("estleg:Chapter_RVSX_5")
            and "Preamble" not in n["@id"]
        )
        haspart = {x["@id"] for x in chapter.get("estleg:hasPart", [])}
        division = next(
            n for n in graph if n.get("@id", "").startswith("estleg:Division_RVSX_")
        )
        assert division["@id"] in haspart, haspart
        assert "estleg:RVSX_Par_87" in haspart, haspart
        # The division's own provision is NOT a direct child of the chapter.
        assert "estleg:RVSX_Par_88" not in haspart, haspart
        # Symmetry with isPartOf.
        p87 = next(n for n in graph if n.get("@id") == "estleg:RVSX_Par_87")
        assert p87["estleg:isPartOf"]["@id"] == chapter["@id"]

    def test_chapter_with_only_direct_provisions_keeps_no_haspart(self):
        """Scope guard for #375: a chapter with NO divisions still emits no
        hasPart (unchanged behaviour)."""
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üld</peatykkPealkiri>
            <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Ainus.</tavatekst></loige></paragrahv>
          </peatykk>
        </sisu></akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "X", "x", ET.fromstring(xml), abbreviation="ONLY",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        chapter = next(
            n for n in doc["@graph"]
            if n.get("@id", "").startswith("estleg:Chapter_ONLY_")
        )
        assert "estleg:hasPart" not in chapter

    def test_multipart_path_haspart_lists_division_and_direct_provision(self):
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <osa><osaNr>1</osaNr><osaPealkiri>Üldosa</osaPealkiri>
            <peatykk><peatykkNr>3</peatykkNr><peatykkPealkiri>Sätted</peatykkPealkiri>
              <paragrahv><paragrahvNr>141</paragrahvNr><kuvatavNr>§ 141.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Otse.</tavatekst></loige></paragrahv>
              <jagu><jaguNr>1</jaguNr><jaguPealkiri>Esimene</jaguPealkiri>
                <paragrahv><paragrahvNr>142</paragrahvNr><kuvatavNr>§ 142.</kuvatavNr>
                  <loige><loigeNr>1</loigeNr><tavatekst>Jaos.</tavatekst></loige></paragrahv>
              </jagu>
            </peatykk>
          </osa>
        </sisu></akt>
        """
        results = dict(generate_all_laws.generate_multipart_law(
            "VÕS", "vos_hp", ET.fromstring(xml), abbreviation="VHPX",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        ))
        graph = results["vos_hp_osa1_peep.json"]["@graph"]
        prefix = graph[0]["@id"].split("estleg:")[1].rsplit("_Osa", 1)[0]
        chapter = next(
            n for n in graph
            if n.get("@id", "").startswith(f"estleg:Chapter_{prefix}_1")
            and "Preamble" not in n["@id"]
        )
        haspart = {x["@id"] for x in chapter.get("estleg:hasPart", [])}
        assert any(
            x.startswith(f"estleg:Division_{prefix}_1") for x in haspart
        ), haspart
        assert f"estleg:{prefix}_Osa1_Par_141" in haspart, haspart
        assert f"estleg:{prefix}_Osa1_Par_142" not in haspart, haspart


# ---------------------------------------------------------------------------
# Combined SHACL conformance: a structuredBody law exercising the preamble
# container (#371), unnamed-jagu attribution (#371) and direct-child hasPart
# (#375) at once must still conform to the shapes.
# ---------------------------------------------------------------------------


class TestGeneratorFixesShaclConformance:
    def test_generated_structuredbody_with_all_fixes_conforms(self):
        generate_all_laws._used_prefixes.clear()
        xml = """
        <akt><sisu>
          <paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
            <loige><loigeNr>1</loigeNr><tavatekst>Preambuli säte enne peatükke.</tavatekst></loige></paragrahv>
          <peatykk><peatykkNr>5</peatykkNr><peatykkPealkiri>Ravimid</peatykkPealkiri>
            <paragrahv><paragrahvNr>87</paragrahvNr><kuvatavNr>§ 87.</kuvatavNr>
              <loige><loigeNr>1</loigeNr><tavatekst>Otse peatüki all.</tavatekst></loige></paragrahv>
            <jagu><jaguNr>1</jaguNr><jaguPealkiri>Esimene jagu</jaguPealkiri>
              <paragrahv><paragrahvNr>88</paragrahvNr><kuvatavNr>§ 88.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Jaos olev säte.</tavatekst></loige></paragrahv>
            </jagu>
            <jagu>
              <paragrahv><paragrahvNr>89</paragrahvNr><kuvatavNr>§ 89.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Nimetu jaos säte.</tavatekst></loige></paragrahv>
            </jagu>
          </peatykk>
        </sisu></akt>
        """
        doc = generate_all_laws.generate_law_jsonld(
            "Ravimiseadus", "ravs", ET.fromstring(xml), abbreviation="SHXX",
            allocator=generate_all_laws.PrefixAllocator(registry={}),
        )
        ok, msg = _shacl_conforms(doc)
        assert ok, msg
