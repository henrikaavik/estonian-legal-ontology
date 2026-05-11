"""Regression tests for scripts/generate_all_laws.py.

Covers issues #156, #165, #166. Each test class has a comment that
explains which fix it locks down.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import pytest

import generate_all_laws


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
                    and "Esimene" in n.get("rdfs:label", {}).get("@value", ""))
        div2 = next(n for n in doc["@graph"]
                    if n.get("@id", "").startswith("estleg:Division_TNXX_")
                    and "Teine" in n.get("rdfs:label", {}).get("@value", ""))

        par5 = next(n for n in doc["@graph"]
                    if n.get("@id", "").endswith("Par_5"))
        par7 = next(n for n in doc["@graph"]
                    if n.get("@id", "").endswith("Par_7"))
        assert par5["estleg:isPartOf"]["@id"] == div1["@id"]
        assert par7["estleg:isPartOf"]["@id"] == div2["@id"]

        # Every paragraph maps to exactly one container.
        par_iris = [n.get("@id") for n in doc["@graph"]
                    if "_Par_" in (n.get("@id") or "")]
        assert len(par_iris) == len(set(par_iris))


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
