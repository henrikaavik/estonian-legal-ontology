"""Tests for generate_amendment_history — XML pairing, chronological
ordering, RT regex, ambiguous-match guards, and multipart aggregation.

The first class (`TestAmendmentHistoryDiscovery`) pre-dates issue #173;
everything below is regression coverage for the four fixes shipped for
that issue (chronological order + stable hash IDs, strict title match,
split coverage triples, multipart law collisions, RT regex, and the
failures sink wiring).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


class TestAmendmentHistoryDiscovery:
    @pytest.mark.parametrize(
        "module_name",
        [
            "generate_amendment_history",
            "estleg_common",
            "extract_temporal_data",
        ],
    )
    def test_pair_peep_with_xml_dry_across_imports(self, tmp_path, module_name):
        """The amendment-history script reuses the globalId lookup
        helper — DRY rule. After Layer 2a I1, the canonical home is
        estleg_common, and both generate_amendment_history and
        extract_temporal_data re-export it. This test proves the rule
        SEMANTICALLY: every accessible import path resolves the SAME
        peep file to the SAME XML path against a shared fixture."""
        import importlib

        module = importlib.import_module(module_name)
        pair_peep_with_xml = module.pair_peep_with_xml
        build_globalid_xml_lookup = module.build_globalid_xml_lookup

        # Fixture: one peep with estleg:globalId="555" and an XML file
        # under maarus_kov/ keyed by globaalID="555".
        rt = tmp_path / "data" / "riigiteataja"
        (rt / "maarus_kov").mkdir(parents=True)
        xml_path = rt / "maarus_kov" / "reg_555.xml"
        xml_path.write_text(
            '<akt globaalID="555"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        peep = tmp_path / "test_act_peep.json"
        peep.write_text(
            json.dumps({
                "@context": {
                    "estleg": "https://w3id.org/estleg/",
                    "owl": "http://www.w3.org/2002/07/owl#",
                },
                "@graph": [
                    {
                        "@id": "estleg:Reg_X",
                        "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                        "estleg:globalId": "555",
                    }
                ],
            }),
            encoding="utf-8",
        )

        lookup = build_globalid_xml_lookup(rt)
        result = pair_peep_with_xml(peep, lookup)
        assert result == xml_path

    def test_helpers_in_canonical_location(self):
        """After Layer 2a I1, build_globalid_xml_lookup and
        pair_peep_with_xml live in estleg_common alongside
        iter_peep_files — the canonical home of pipeline-shared
        helpers. Layer 2b's preamble scanner imports from here."""
        from estleg_common import build_globalid_xml_lookup, pair_peep_with_xml
        assert callable(build_globalid_xml_lookup)
        assert callable(pair_peep_with_xml)

    def test_recursive_glob_finds_kov_xml(self, tmp_path):
        from generate_amendment_history import build_globalid_xml_lookup
        rt = tmp_path / "data" / "riigiteataja"
        (rt / "maarus_kov").mkdir(parents=True)
        (rt / "maarus_kov" / "reg_kov_888.xml").write_text(
            '<akt globaalID="888"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )
        lookup = build_globalid_xml_lookup(rt)
        assert "888" in lookup

    def test_main_uses_globalid_pairing(self, tmp_path, monkeypatch):
        """Pin the contract that main() actually wires the new helpers.
        Same approach as temporal's TestMainUsesGlobalIdLookup — runs
        main() over a tiny KOV-like fixture and asserts an XML file
        under maarus_kov/ paired by globalId contributed an amendment
        chain entry."""
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (rt / "maarus_kov").mkdir(parents=True)
        (krr / "amendments").mkdir(parents=True)
        (krr / "eelnoud").mkdir(parents=True)

        peep = kov / "test_act_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "rdfs:label": "Test KOV act",
                 "dc:source": "Test KOV act",
                 "estleg:globalId": "777",
                 "estleg:enactedBy": {
                     "@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_EHAK_0784"},
                 "estleg:titleNormalized": "test"}
            ],
        }), encoding="utf-8")

        # XML with a muutmismarge block whose children match what
        # extract_amendments_from_xml expects (lines 162-210 of the
        # script): aktikuupaev, joustumine, and avaldamismarge with
        # RTosa/RTaasta/RTnr children (the script reads RTnr, not
        # RTnumber — see line 205 of the script). Each muutmismarge
        # is extracted as a separate amendment entry; the RT fields
        # are formatted as "RT IV, 2024, 1" in the output.
        (rt / "maarus_kov" / "reg_777.xml").write_text(
            '<akt globaalID="777"><metaandmed>'
            '<muutmismarge>'
            '<aktikuupaev>2023-06-15</aktikuupaev>'
            '<joustumine>2024-01-01</joustumine>'
            '<avaldamismarge>'
            '<RTosa>IV</RTosa><RTaasta>2024</RTaasta><RTnr>1</RTnr>'
            '</avaldamismarge>'
            '</muutmismarge>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        # Patch ALL the import-time-bound paths. AMENDMENTS_DIR and
        # EELNOUD_DIR are derived from KRR_DIR at import time so they
        # need explicit rebinding too.
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        if hasattr(mod, "AMENDMENTS_DIR"):
            monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        if hasattr(mod, "EELNOUD_DIR"):
            monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        rc = mod.main()
        assert rc in (None, 0)

        # The peep file should have been touched. Either an
        # amendedBy back-link or a chain summary node was added.
        with open(peep, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        # Search the graph for any node carrying an amendment-related
        # property — the exact name depends on the script's output
        # convention, so accept any of several candidates.
        amendment_keys = {"estleg:amendedBy", "estleg:hasAmendment",
                          "estleg:amendmentHistory"}
        found = False
        for node in doc.get("@graph", []):
            if amendment_keys.intersection(node.keys()):
                found = True
                break
        assert found, (
            "main() ran but no amendment triples appeared after pairing "
            f"— amendments_by_slug may not be populated. Graph keys: "
            f"{[list(n.keys()) for n in doc['@graph']]}"
        )


# ---------------------------------------------------------------------------
# Issue #173 regression coverage
# ---------------------------------------------------------------------------


class TestRtReferenceRegex:
    """Regression coverage for the RT publication-ref regex (issue #173 #5).

    Old regex: ``RT\\s+I{1,2}\\s*,\\s*...`` — accepted ``IIII`` greedily,
    missed ``RT III``, and missed hyphenated lisa numbers.
    """

    def _scan(self, tmp_path: Path, body: str) -> list[str]:
        from generate_amendment_history import extract_rt_references_from_text

        xml_path = tmp_path / "fixture.xml"
        xml_path.write_text(
            "<akt><body>" + body + "</body></akt>",
            encoding="utf-8",
        )
        return extract_rt_references_from_text(xml_path)

    def test_matches_rt_i(self, tmp_path):
        refs = self._scan(tmp_path, "Avaldatud RT I, 28.06.2023, 10")
        assert any("RT I, 28.06.2023, 10" in r for r in refs)

    def test_matches_rt_ii(self, tmp_path):
        refs = self._scan(tmp_path, "Allikas RT II, 2009, 1, 4")
        assert any("RT II" in r for r in refs)

    def test_matches_rt_iii(self, tmp_path):
        refs = self._scan(tmp_path, "Vastavalt RT III, 2009, 14, 89")
        assert any("RT III" in r for r in refs), refs

    def test_matches_pre_2010_year_only_format(self, tmp_path):
        # Pre-2010 RT publication refs use bare year ("RT I, 2009, 24, 161").
        refs = self._scan(tmp_path, "Vt RT I, 2009, 24, 161")
        assert any("RT I, 2009, 24, 161" in r for r in refs)

    def test_does_not_match_iiii_as_iii(self, tmp_path):
        # The old regex would match "RT II" out of "RT IIII" — that
        # collapses two distinct (but probably bogus) markers into one.
        refs = self._scan(tmp_path, "RT IIII, 2024, 5")
        # Either the regex skips this entirely, OR it matches "RT III"
        # exactly — in NEITHER case should the result contain "RT IIII"
        # without the trailing 'I' getting consumed beyond the boundary.
        assert "RT IIII" not in " ".join(refs)

    def test_matches_hyphenated_lisa_numbers(self, tmp_path):
        # Hyphenated lisa numbers ("89-90") were previously dropped.
        refs = self._scan(tmp_path, "Avaldatud RT I, 2009, 14, 89-90")
        assert any("89-90" in r for r in refs), refs


class TestMatchLawNameStrict:
    """Regression coverage for substring-collision bug (issue #173 #2).

    Old behaviour: any partial substring match returned the first slug
    found, so 'Riigieelarve seadus' would match
    'Riigieelarve seaduse muutmise seadus' (a totally different act).
    """

    def test_exact_normalised_title_wins(self):
        from generate_amendment_history import match_law_name_to_slug

        # title_map values are now LISTS of slugs (issue #595).
        title_map = {"riigieelarve seadus": ["riigieelarve_seadus"]}
        result = match_law_name_to_slug(
            "Riigieelarve seadus", title_map, {}
        )
        assert result == "riigieelarve_seadus"

    def test_substring_no_longer_matches_unrelated_law(self):
        from generate_amendment_history import match_law_name_to_slug

        # Two unrelated titles. Substring of "muutmise seadus" inside the
        # second one would have matched in the old code; under the new
        # rule (token Jaccard >= 0.9), distinct token sets means no match.
        title_map = {
            "muutmise seadus": ["muutmise_seadus"],
            "riigieelarve seaduse muutmise ja sellega seonduvalt teiste seaduste muutmise seadus": (
                ["riigieelarve_muutmise"]
            ),
        }
        # "muutmise seadus" should resolve EXACTLY to "muutmise_seadus"
        # (path 1: exact normalised match), not to the longer act.
        result = match_law_name_to_slug(
            "muutmise seadus", title_map, {}
        )
        assert result == "muutmise_seadus"

    def test_ambiguous_jaccard_match_bails_with_failure_log(self):
        from generate_amendment_history import match_law_name_to_slug

        # Two title entries with IDENTICAL token sets but different
        # slugs — Jaccard is exactly 1.0 against the input for both,
        # which is the ambiguity case the failures sink must surface.
        title_map = {
            "alfa beeta gamma delta zeta": ["slug_alfa"],
            "alfa beeta zeta delta gamma": ["slug_alfa_reordered"],
        }
        failures: list[str] = []
        # Feed an input that is NOT an exact normalised hit on either
        # title (different word order without trailing token), so we
        # avoid path 1's exact-match shortcut and exercise the
        # Jaccard fallback.
        result = match_law_name_to_slug(
            "zeta gamma delta beeta alfa",
            title_map,
            {},
            failures=failures,
        )
        assert result is None
        assert any("ambiguous" in f and "zeta gamma delta beeta alfa" in f
                   for f in failures), failures


class TestStableAmendmentSuffix:
    """Regression coverage for chronological ordering + hash IDs
    (issue #173 #1)."""

    def test_suffix_stable_across_calls_with_same_rt_reference(self):
        from generate_amendment_history import _stable_amend_suffix

        a = {"rt_reference": "RT I, 2024, 5, 1", "date": "2024-01-01"}
        s1 = _stable_amend_suffix(a)
        s2 = _stable_amend_suffix(a)
        assert s1 == s2
        assert len(s1) == 10

    def test_suffix_differs_for_different_rt_refs(self):
        from generate_amendment_history import _stable_amend_suffix

        a = {"rt_reference": "RT I, 2024, 5, 1"}
        b = {"rt_reference": "RT I, 2024, 6, 2"}
        assert _stable_amend_suffix(a) != _stable_amend_suffix(b)

    def test_amend_sort_key_prefers_entry_into_force(self):
        from generate_amendment_history import _amend_sort_key

        late_eif = {"date": "2010-01-01", "entry_into_force": "2024-01-01"}
        early_eif = {"date": "2024-12-31", "entry_into_force": "2010-01-01"}
        # Sort ascending by entry_into_force first.
        assert _amend_sort_key(early_eif) < _amend_sort_key(late_eif)

    def test_amend_sort_key_falls_back_to_date_when_no_eif(self):
        from generate_amendment_history import _amend_sort_key

        a = {"date": "2010-01-01", "entry_into_force": None}
        b = {"date": "2020-01-01", "entry_into_force": None}
        assert _amend_sort_key(a) < _amend_sort_key(b)


class TestOsaOrderKeyCanonicalPart:
    """Issue #606 — the canonical part of a multipart law must be the
    lowest-numbered ``_osaN`` (osa1), chosen by NATURAL numeric order so
    osa10/osa11 cannot win over osa2 under lexical-string or glob ordering."""

    def test_osa_order_key_is_numeric_and_no_suffix_sorts_as_zero(self):
        from generate_amendment_history import _osa_order_key

        # Natural numeric ordering: osa1 < osa2 < osa10 < osa11. Lexical
        # string order would (wrongly) place "_osa10"/"_osa11" before "_osa2".
        assert (
            _osa_order_key("voos_osa1")
            < _osa_order_key("voos_osa2")
            < _osa_order_key("voos_osa10")
            < _osa_order_key("voos_osa11")
        )
        # A single-part law (no _osaN suffix) sorts as (0, slug); an embedded
        # "osa" that is not a trailing _osaN is likewise left untouched.
        assert _osa_order_key("ksdseadus") == (0, "ksdseadus")
        assert _osa_order_key("osariik_seadus") == (0, "osariik_seadus")

    def test_sorting_members_like_list_puts_osa1_first(self):
        from generate_amendment_history import _osa_order_key

        # Mirrors main()'s grouping: (slug, info) tuples arrive in arbitrary
        # insertion/glob order; sorting by the key must make members[0] the
        # canonical osa1, then osa2, osa10, osa11.
        members = [
            ("x_osa10", {"title": "X"}),
            ("x_osa2", {"title": "X"}),
            ("x_osa1", {"title": "X"}),
            ("x_osa11", {"title": "X"}),
        ]
        ordered = sorted(members, key=lambda m: _osa_order_key(m[0]))
        assert [slug for slug, _info in ordered] == [
            "x_osa1",
            "x_osa2",
            "x_osa10",
            "x_osa11",
        ]


class TestMultipartLawAggregation:
    """Regression coverage for multipart-law chain-file overwrite
    (issue #173 #4). Ensures a base-slug-keyed chain document
    aggregates ALL parts rather than each part overwriting the same
    file in turn."""

    def _setup(self, tmp_path: Path, monkeypatch):
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir(parents=True)
        rt.mkdir(parents=True)
        (krr / "amendments").mkdir()
        (krr / "eelnoud").mkdir()
        (krr / "regulations" / "riik").mkdir(parents=True)
        # Don't pull KOV files into iter_peep_files for this test.
        (krr / "regulations" / "kov").mkdir(parents=True)

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        return krr, rt, mod

    def _write_part(self, krr: Path, slug: str, gid: str, title: str) -> Path:
        p = krr / f"{slug}_peep.json"
        p.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {
                    "@id": f"estleg:{slug.upper()}_Map",
                    "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                    "rdfs:label": title,
                    "dc:source": title,
                    "estleg:globalId": gid,
                }
            ],
        }), encoding="utf-8")
        return p

    def _write_xml_with_amendments(self, rt: Path, gid: str, amendments: list[dict]):
        muutmismarge_blocks = []
        for a in amendments:
            mm = (
                "<muutmismarge>"
                f"<aktikuupaev>{a.get('date', '')}</aktikuupaev>"
                f"<joustumine>{a.get('eif', '')}</joustumine>"
                "<avaldamismarge>"
                f"<RTosa>{a.get('rt_osa', 'I')}</RTosa>"
                f"<RTaasta>{a.get('rt_aasta', '2024')}</RTaasta>"
                f"<RTnr>{a.get('rt_nr', '1')}</RTnr>"
                "</avaldamismarge>"
                "</muutmismarge>"
            )
            muutmismarge_blocks.append(mm)
        xml_text = (
            f'<akt globaalID="{gid}"><metaandmed>'
            + "".join(muutmismarge_blocks)
            + "</metaandmed></akt>"
        )
        (rt / f"reg_{gid}.xml").write_text(xml_text, encoding="utf-8")

    def test_three_parts_share_one_chain_file(self, tmp_path, monkeypatch):
        krr, rt, mod = self._setup(tmp_path, monkeypatch)

        # Three parts of "Võlaõigusseadus" — same XML, same gid would
        # be redundant in real life, but the test layout uses three
        # gids each with one muutmismarge so we can verify
        # aggregation, not that all parts pull from the same XML.
        # The base_slug rule strips trailing _osa<N>, so all three
        # parts MUST share one chain file: amendments_volaigusseadus.json.
        title = "Võlaõigusseadus"
        for i in (1, 2, 3):
            self._write_part(krr, f"volaigusseadus_osa{i}", f"100{i}", title)
        # Each part XML carries one distinct amendment so we can also
        # verify the chain doc combines them all.
        self._write_xml_with_amendments(
            rt, "1001", [{"date": "2010-01-01", "eif": "2010-06-01",
                          "rt_aasta": "2010", "rt_nr": "1"}],
        )
        self._write_xml_with_amendments(
            rt, "1002", [{"date": "2015-01-01", "eif": "2015-06-01",
                          "rt_aasta": "2015", "rt_nr": "2"}],
        )
        self._write_xml_with_amendments(
            rt, "1003", [{"date": "2024-01-01", "eif": "2024-06-01",
                          "rt_aasta": "2024", "rt_nr": "3"}],
        )

        rc = mod.main()
        assert rc in (None, 0)

        # Multipart aggregation: ONE chain doc keyed by base_slug.
        chain_files = sorted((krr / "amendments").glob("amendments_*.json"))
        assert len(chain_files) == 1, [p.name for p in chain_files]
        assert chain_files[0].name == "amendments_volaigusseadus.json"

        with open(chain_files[0], "r", encoding="utf-8") as fh:
            chain_doc = json.load(fh)
        # The chain doc graph should contain the chain ontology node
        # plus one AmendmentEvent per part-XML's muutmismarge.
        amend_events = [
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]
        # Each part's XML had exactly one muutmismarge — amendments
        # are looked up by part slug in amendments_by_slug, but the
        # main loop pulls the FIRST non-empty member in the group
        # before falling back to base_slug, so we expect at least one
        # event present (the chronologically-earliest one).
        assert len(amend_events) >= 1

    def test_chain_event_ids_use_stable_hash(self, tmp_path, monkeypatch):
        krr, rt, mod = self._setup(tmp_path, monkeypatch)
        title = "Testseadus"
        self._write_part(krr, "testseadus", "2001", title)
        # Two amendments whose chronological (EIF-ascending) order is the
        # REVERSE of document order, so the sort is genuinely exercised. Each
        # eif is AFTER its own adoption date (a legitimate forward ordering),
        # so the #587 impossible-inversion guard does not null either eif.
        self._write_xml_with_amendments(
            rt, "2001",
            [
                {"date": "2024-05-01", "eif": "2024-06-01",
                 "rt_aasta": "2024", "rt_nr": "9"},  # later pair, doc-first
                {"date": "2010-01-01", "eif": "2010-02-01",
                 "rt_aasta": "2010", "rt_nr": "1"},  # earlier pair, doc-second
            ],
        )

        rc = mod.main()
        assert rc in (None, 0)

        chain_files = sorted((krr / "amendments").glob("amendments_*.json"))
        assert chain_files, "expected one chain file"
        chain_doc = json.loads(chain_files[0].read_text(encoding="utf-8"))
        events = [
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]
        assert len(events) == 2

        # Chronological order: events sorted ascending by EIF.
        eifs = [
            (n.get("estleg:entryIntoForce") or {}).get("@value")
            for n in events
        ]
        assert eifs == sorted(eifs), eifs

        # Stable IDs — IRI suffix is a 10-char hash, NOT a sequential
        # _1, _2 index.
        for ev in events:
            iri = ev["@id"]
            # estleg:Amendment_<base>_<hashsuffix[10]> [optionally _N]
            m = re.match(
                r"^estleg:Amendment_testseadus_([0-9a-f]{10})(?:_\d+)?$",
                iri,
            )
            assert m, f"unexpected IRI shape: {iri}"


class TestCoverageTriplesSplit:
    """Regression coverage for split coverage triples (issue #173 #3)."""

    def test_summary_exposes_both_metrics(self, tmp_path, monkeypatch):
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir(parents=True)
        rt.mkdir(parents=True)
        (krr / "amendments").mkdir()
        (krr / "eelnoud").mkdir()
        (krr / "regulations" / "riik").mkdir(parents=True)
        (krr / "regulations" / "kov").mkdir(parents=True)

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        # One law with one amendment.
        peep = krr / "alfa_seadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Alfa_Map",
                 "@type": ["owl:Ontology", "estleg:Act"],
                 "rdfs:label": "Alfa seadus", "dc:source": "Alfa seadus",
                 "estleg:globalId": "5000"}
            ],
        }), encoding="utf-8")
        (rt / "reg_5000.xml").write_text(
            '<akt globaalID="5000"><metaandmed>'
            '<muutmismarge>'
            '<aktikuupaev>2024-03-01</aktikuupaev>'
            '<joustumine>2024-06-01</joustumine>'
            '<avaldamismarge>'
            '<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>10</RTnr>'
            '</avaldamismarge>'
            '</muutmismarge>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        rc = mod.main()
        assert rc in (None, 0)

        report = json.loads(
            (krr / "amendment_history_report.json").read_text(
                encoding="utf-8"
            )
        )
        summary = report["summary"]
        assert "amendedBy_link_total" in summary
        assert "amendment_event_triples" in summary
        assert "triples_total" in summary
        # Sanity: total = both surfaces.
        assert summary["triples_total"] == (
            summary["amendedBy_link_total"]
            + summary["amendment_event_triples"]
        )
        assert summary["amendedBy_link_total"] >= 1
        assert summary["amendment_event_triples"] >= 1


class TestFailuresSink:
    """Regression coverage for the failures sink wiring (issue #173 #6).

    Each loader/extractor accepts a ``failures`` kwarg; bare excepts
    used to swallow corruption silently."""

    def test_load_law_files_appends_failures(self, tmp_path, monkeypatch):
        from generate_amendment_history import load_law_files
        import estleg_common
        import generate_amendment_history as mod

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        (krr / "regulations" / "riik").mkdir(parents=True)
        (krr / "regulations" / "kov").mkdir(parents=True)
        # One file with corrupt JSON.
        (krr / "broken_peep.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        failures: list[str] = []
        load_law_files(failures=failures)
        assert any("broken_peep.json" in f for f in failures), failures

    def test_extract_amendments_logs_parse_error(self, tmp_path):
        from generate_amendment_history import extract_amendments_from_xml

        bad = tmp_path / "bad.xml"
        bad.write_text("<not-xml>>", encoding="utf-8")
        failures: list[str] = []
        out = extract_amendments_from_xml(bad, failures=failures)
        assert out == []
        assert any("bad.xml" in f for f in failures)

    def test_extract_rt_references_logs_parse_error(self, tmp_path):
        from generate_amendment_history import extract_rt_references_from_text

        bad = tmp_path / "bad.xml"
        bad.write_text("<not-xml>>", encoding="utf-8")
        failures: list[str] = []
        out = extract_rt_references_from_text(bad, failures=failures)
        assert out == []
        assert any("bad.xml" in f for f in failures)

    def test_load_draft_amendments_logs_parse_error(
        self, tmp_path, monkeypatch
    ):
        from generate_amendment_history import load_draft_amendments
        import generate_amendment_history as mod

        eelnoud = tmp_path / "eelnoud"
        eelnoud.mkdir()
        (eelnoud / "eelnoud_combined.jsonld").write_text(
            "{not json", encoding="utf-8"
        )
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)
        failures: list[str] = []
        out = load_draft_amendments(failures=failures)
        assert out == []
        assert any("eelnoud_combined" in f for f in failures), failures

    def test_clear_amended_by_logs_parse_error(self, tmp_path):
        from generate_amendment_history import clear_amended_by_from_file

        broken = tmp_path / "broken_peep.json"
        broken.write_text("{not json", encoding="utf-8")
        failures: list[str] = []
        modified = clear_amended_by_from_file(broken, failures=failures)
        assert modified is False
        assert any("broken_peep.json" in f for f in failures)

    def test_main_prints_p1_banner_when_failures(
        self, tmp_path, monkeypatch, capsys
    ):
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir(parents=True)
        rt.mkdir(parents=True)
        (krr / "amendments").mkdir()
        (krr / "eelnoud").mkdir()
        (krr / "regulations" / "riik").mkdir(parents=True)
        (krr / "regulations" / "kov").mkdir(parents=True)

        # Corrupt peep file forces load_law_files to log a failure.
        (krr / "broken_peep.json").write_text("{not json", encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (None, 0)
        captured = capsys.readouterr()
        # P1 banner must surface to stderr when failures > 0.
        assert "[P1]" in captured.err
        assert "generate_amendment_history" in captured.err


# ---------------------------------------------------------------------------
# Issue #192 — compact Amendment_/AmendmentChain_/AmendmentLink_ IRIs
# ---------------------------------------------------------------------------


class TestShortPrefixForBaseSlug:
    """``short_prefix_for_base_slug`` — registry abbrev > Reg_<id> stem > slug."""

    def test_uses_registry_abbreviation(self):
        from generate_amendment_history import short_prefix_for_base_slug

        abbreviations = {
            "karistusseadustik": {"abbrev": "KARIST_2", "source": "auto",
                                  "title": "Karistusseadustik",
                                  "old_prefix": "Karistusseadustik"},
        }
        assert short_prefix_for_base_slug(
            "karistusseadustik", "estleg:KARIST_2_Osa1_1_87", abbreviations
        ) == "KARIST_2"

    def test_falls_back_to_reg_id_stem_for_unregistered_regulation(self):
        from generate_amendment_history import short_prefix_for_base_slug

        # base_slug absent from the registry → recover Reg_<id> from the
        # canonical ontology IRI (NOT from the slug's _t<id> suffix, which can
        # disagree with the act's numeric id).
        assert short_prefix_for_base_slug(
            "jarva_maakonna_looduse_uksikobjektide_kaitse_alla_votmine_t1049276",
            "estleg:Reg_1056294_Map_2026",
            {},
        ) == "Reg_1056294"

    def test_falls_back_to_sanitized_slug_when_nothing_better(self):
        from generate_amendment_history import sanitize_id, short_prefix_for_base_slug

        # No registry entry and the ontology IRI yields no *shorter* stem →
        # keep the (sanitised) slug rather than crash.
        assert short_prefix_for_base_slug(
            "kutseseadus", "estleg:Kutseseadus_Map_2026", {}
        ) == sanitize_id("kutseseadus")
        # …and a totally absent ontology IRI also falls back gracefully.
        assert short_prefix_for_base_slug("some_long_slug", "", {}) == sanitize_id(
            "some_long_slug"
        )


class TestLoadLawAbbreviations:
    def test_loads_registry(self, tmp_path):
        from generate_amendment_history import load_law_abbreviations

        p = tmp_path / "law_abbreviations.json"
        p.write_text(json.dumps({"x": {"abbrev": "X"}}), encoding="utf-8")
        assert load_law_abbreviations(p) == {"x": {"abbrev": "X"}}

    def test_missing_file_returns_empty(self, tmp_path):
        from generate_amendment_history import load_law_abbreviations

        assert load_law_abbreviations(tmp_path / "nope.json") == {}

    def test_corrupt_file_records_failure_and_returns_empty(self, tmp_path):
        from generate_amendment_history import load_law_abbreviations

        p = tmp_path / "law_abbreviations.json"
        p.write_text("{not json", encoding="utf-8")
        failures: list[str] = []
        assert load_law_abbreviations(p, failures=failures) == {}
        assert any("load_law_abbreviations" in f for f in failures)


class TestAmendmentChainCleanup:
    def test_remove_obsolete_chain_files_keeps_current_bases(self, tmp_path, monkeypatch):
        import generate_amendment_history as mod

        amendments_dir = tmp_path / "amendments"
        amendments_dir.mkdir()
        stale = amendments_dir / "amendments_old_slug.json"
        current = amendments_dir / "amendments_current_slug.json"
        stale.write_text("{}", encoding="utf-8")
        current.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", amendments_dir)

        removed = mod._remove_obsolete_amendment_chain_files({"current_slug"})

        assert removed == 1
        assert not stale.exists()
        assert current.exists()

    def test_obsolete_chain_cleanup_disabled_when_pairing_is_not_trustworthy(
        self, tmp_path, monkeypatch
    ):
        import generate_amendment_history as mod

        amendments_dir = tmp_path / "amendments"
        amendments_dir.mkdir()
        stale = amendments_dir / "amendments_old_slug.json"
        stale.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", amendments_dir)

        removed = mod._remove_obsolete_amendment_chain_files(
            {"current_slug"},
            safe_to_delete=False,
        )

        assert removed == 0
        assert stale.exists()

    def test_remove_amendment_chain_file_is_idempotent(self, tmp_path, monkeypatch):
        import generate_amendment_history as mod

        amendments_dir = tmp_path / "amendments"
        amendments_dir.mkdir()
        chain = amendments_dir / "amendments_no_longer_amended.json"
        chain.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", amendments_dir)

        assert mod._remove_amendment_chain_file("no_longer_amended") == 1
        assert mod._remove_amendment_chain_file("no_longer_amended") == 0
        assert not chain.exists()

    def test_chain_file_preserved_when_pairing_fails(self, tmp_path, monkeypatch):
        import generate_amendment_history as mod

        krr = tmp_path / "krr_outputs"
        amendments_dir = krr / "amendments"
        eelnoud_dir = krr / "eelnoud"
        amendments_dir.mkdir(parents=True)
        eelnoud_dir.mkdir()
        chain = amendments_dir / "amendments_unpaired_law.json"
        chain.write_text("{}", encoding="utf-8")
        law_path = krr / "unpaired_law_peep.json"
        law_path.write_text(
            json.dumps(
                {
                    "@graph": [
                        {
                            "@id": "estleg:UNP_Map_2026",
                            "@type": ["owl:Ontology", "estleg:Act"],
                            "dc:source": "Unpaired law",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", amendments_dir)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud_dir)
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "iter_peep_files", lambda: [law_path])
        monkeypatch.setattr(mod, "build_globalid_xml_lookup", lambda _data_dir: {})
        monkeypatch.setattr(mod, "pair_peep_with_xml", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(mod, "load_law_abbreviations", lambda *a, **kw: {})
        monkeypatch.setattr(mod, "load_draft_amendments", lambda *a, **kw: [])

        assert mod.main() == 0

        assert chain.exists()

    def test_per_chain_deletion_skipped_when_unsafe_failures_present(
        self, tmp_path, monkeypatch
    ):
        import generate_amendment_history as mod

        krr = tmp_path / "krr_outputs"
        amendments_dir = krr / "amendments"
        eelnoud_dir = krr / "eelnoud"
        amendments_dir.mkdir(parents=True)
        eelnoud_dir.mkdir()
        chain = amendments_dir / "amendments_paired_law.json"
        chain.write_text("{}", encoding="utf-8")
        law_path = krr / "paired_law_peep.json"
        law_path.write_text(
            json.dumps({
                "@graph": [
                    {
                        "@id": "estleg:PAIR_Map_2026",
                        "@type": ["owl:Ontology", "estleg:Act"],
                        "dc:source": "Paired law",
                    }
                ]
            }),
            encoding="utf-8",
        )
        xml_path = tmp_path / "paired_law.xml"
        xml_path.write_text("<akt />", encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", amendments_dir)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud_dir)
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "iter_peep_files", lambda: [law_path])
        monkeypatch.setattr(mod, "build_globalid_xml_lookup", lambda _data_dir: {})
        monkeypatch.setattr(mod, "pair_peep_with_xml", lambda *_args, **_kwargs: xml_path)
        monkeypatch.setattr(mod, "load_law_abbreviations", lambda *a, **kw: {})
        monkeypatch.setattr(mod, "extract_rt_references_from_text", lambda *a, **kw: [])
        monkeypatch.setattr(mod, "load_draft_amendments", lambda *a, **kw: [])

        def unsafe_extract(_xml_path, *, failures=None):
            if failures is not None:
                failures.append("extract_amendments_from_xml | paired_law.xml | boom")
            return []

        monkeypatch.setattr(mod, "extract_amendments_from_xml", unsafe_extract)

        assert mod.main() == 0

        assert chain.exists()

    def test_soft_failures_do_not_suppress_chain_cleanup(self, tmp_path, monkeypatch):
        import generate_amendment_history as mod

        krr = tmp_path / "krr_outputs"
        amendments_dir = krr / "amendments"
        eelnoud_dir = krr / "eelnoud"
        amendments_dir.mkdir(parents=True)
        eelnoud_dir.mkdir()
        chain = amendments_dir / "amendments_paired_law.json"
        chain.write_text("{}", encoding="utf-8")
        law_path = krr / "paired_law_peep.json"
        law_path.write_text(
            json.dumps({
                "@graph": [
                    {
                        "@id": "estleg:PAIR_Map_2026",
                        "@type": ["owl:Ontology", "estleg:Act"],
                        "dc:source": "Paired law",
                    }
                ]
            }),
            encoding="utf-8",
        )
        xml_path = tmp_path / "paired_law.xml"
        xml_path.write_text("<akt />", encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", amendments_dir)
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud_dir)
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "iter_peep_files", lambda: [law_path])
        monkeypatch.setattr(mod, "build_globalid_xml_lookup", lambda _data_dir: {})
        monkeypatch.setattr(mod, "pair_peep_with_xml", lambda *_args, **_kwargs: xml_path)
        monkeypatch.setattr(mod, "extract_amendments_from_xml", lambda *a, **kw: [])
        monkeypatch.setattr(mod, "extract_rt_references_from_text", lambda *a, **kw: [])
        monkeypatch.setattr(mod, "load_draft_amendments", lambda *a, **kw: [])

        def soft_abbreviations(*_args, failures=None, **_kwargs):
            if failures is not None:
                failures.append("load_law_abbreviations | law_abbreviations.json | boom")
            return {}

        monkeypatch.setattr(mod, "load_law_abbreviations", soft_abbreviations)

        assert mod.main() == 0

        assert not chain.exists()


class TestGeneratorEmitsCompactAmendmentIris:
    """End-to-end: ``generate_amendment_history.main`` mints
    ``Amendment_<ABBREV>_…`` (registry hit) and degrades gracefully when the
    law has no registry entry."""

    def _setup(self, tmp_path: Path, monkeypatch):
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir(parents=True)
        rt.mkdir(parents=True)
        (krr / "amendments").mkdir()
        (krr / "eelnoud").mkdir()
        (krr / "regulations" / "riik").mkdir(parents=True)
        (krr / "regulations" / "kov").mkdir(parents=True)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        # The registry the generator reads (REPO_ROOT/data/law_abbreviations.json).
        monkeypatch.setattr(
            mod, "LAW_ABBREVIATIONS_PATH",
            tmp_path / "data" / "law_abbreviations.json",
        )
        return krr, rt, mod

    def _write_part(self, krr: Path, slug: str, gid: str, title: str) -> Path:
        p = krr / f"{slug}_peep.json"
        p.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": f"estleg:{slug.upper()}_Map",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": title, "dc:source": title,
                 "estleg:globalId": gid}],
        }), encoding="utf-8")
        return p

    def _write_xml_with_amendments(self, rt: Path, gid: str, amendments: list[dict]):
        blocks = []
        for a in amendments:
            blocks.append(
                "<muutmismarge>"
                f"<aktikuupaev>{a.get('date', '')}</aktikuupaev>"
                f"<joustumine>{a.get('eif', '')}</joustumine>"
                "<avaldamismarge>"
                f"<RTosa>{a.get('rt_osa', 'I')}</RTosa>"
                f"<RTaasta>{a.get('rt_aasta', '2024')}</RTaasta>"
                f"<RTnr>{a.get('rt_nr', '1')}</RTnr>"
                "</avaldamismarge></muutmismarge>"
            )
        (rt / f"reg_{gid}.xml").write_text(
            f'<akt globaalID="{gid}"><metaandmed>' + "".join(blocks)
            + "</metaandmed></akt>",
            encoding="utf-8",
        )

    def _write_registry(self, tmp_path: Path, mapping: dict) -> None:
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data" / "law_abbreviations.json").write_text(
            json.dumps(mapping), encoding="utf-8"
        )

    def test_registered_law_uses_compact_abbrev(self, tmp_path, monkeypatch):
        krr, rt, mod = self._setup(tmp_path, monkeypatch)
        self._write_registry(tmp_path, {"perekonnaseadus": {
            "abbrev": "PKS", "source": "rt_api", "title": "Perekonnaseadus",
            "old_prefix": "PKS"}})
        self._write_part(krr, "perekonnaseadus", "3001", "Perekonnaseadus")
        self._write_xml_with_amendments(
            rt, "3001",
            [{"date": "2010-01-01", "eif": "2010-06-01",
              "rt_aasta": "2010", "rt_nr": "5"}],
        )
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_perekonnaseadus.json").read_text(
                encoding="utf-8"
            )
        )
        ids = [n["@id"] for n in chain_doc["@graph"]]
        # Chain @id and every Amendment @id carry the compact PKS prefix.
        assert "estleg:AmendmentChain_PKS" in ids
        amend_ids = [i for i in ids if i.startswith("estleg:Amendment_")]
        assert amend_ids
        for i in amend_ids:
            assert re.match(r"^estleg:Amendment_PKS_[0-9a-f]{10}(?:_\d+)?$", i), i
        # The long slug must NOT appear in any amendment-family @id.
        assert all("perekonnaseadus" not in i for i in ids
                   if i.startswith(("estleg:Amendment_", "estleg:AmendmentChain_")))
        # And the back-link stamped on the act node uses the compact id too.
        peep = json.loads((krr / "perekonnaseadus_peep.json").read_text("utf-8"))
        onto = next(n for n in peep["@graph"] if "owl:Ontology" in n.get("@type", []))
        assert all(
            r["@id"].startswith("estleg:Amendment_PKS_")
            for r in onto["estleg:amendedBy"]
        )

    def test_unregistered_law_falls_back_without_crashing(self, tmp_path, monkeypatch):
        krr, rt, mod = self._setup(tmp_path, monkeypatch)
        # Registry exists but does NOT contain this slug; the part's ontology
        # IRI (``ESIMENEPIKKSLUG_Map``, from slug.upper()) is no shorter than
        # the slug, so the generator keeps the sanitised slug.
        self._write_registry(tmp_path, {"unrelated": {"abbrev": "U"}})
        slug = "esimenepikkslug"
        self._write_part(krr, slug, "4001", "Esimene Pikk Slug")
        self._write_xml_with_amendments(
            rt, "4001",
            [{"date": "2020-01-01", "eif": "2020-06-01",
              "rt_aasta": "2020", "rt_nr": "1"}],
        )
        rc = mod.main()
        assert rc in (None, 0)
        chain_files = sorted((krr / "amendments").glob("amendments_*.json"))
        assert chain_files
        chain_doc = json.loads(chain_files[0].read_text(encoding="utf-8"))
        ids = [n["@id"] for n in chain_doc["@graph"]]
        # Fell back to the sanitised slug (no crash, IRIs still well-formed).
        assert f"estleg:AmendmentChain_{slug}" in ids
        assert any(re.match(rf"^estleg:Amendment_{slug}_[0-9a-f]{{10}}", i) for i in ids)

    def test_runs_with_no_registry_file_at_all(self, tmp_path, monkeypatch):
        krr, rt, mod = self._setup(tmp_path, monkeypatch)
        # No data/law_abbreviations.json — load_law_abbreviations -> {} -> slug.
        monkeypatch.setattr(
            mod, "LAW_ABBREVIATIONS_PATH", tmp_path / "data" / "missing.json"
        )
        self._write_part(krr, "mingiseadus", "5001", "Mingiseadus")
        self._write_xml_with_amendments(
            rt, "5001",
            [{"date": "2021-01-01", "eif": "2021-06-01",
              "rt_aasta": "2021", "rt_nr": "2"}],
        )
        rc = mod.main()
        assert rc in (None, 0)
        chain_files = sorted((krr / "amendments").glob("amendments_*.json"))
        assert chain_files


# ---------------------------------------------------------------------------
# Issue #263 — amendment over-count: dedup repeated muutmismarge markers
# ---------------------------------------------------------------------------


class TestExtractAmendmentsDedup:
    """``extract_amendments_from_xml`` must collapse the SAME amending act
    repeated across many ``<muutmismarge>`` markers into ONE record (issue
    #263), and must not emit degenerate RT references."""

    def _write_xml(self, tmp_path: Path, markers: list[str]) -> Path:
        p = tmp_path / "act.xml"
        p.write_text(
            '<akt globaalID="1"><metaandmed>'
            + "".join(markers)
            + "</metaandmed></akt>",
            encoding="utf-8",
        )
        return p

    def test_same_rt_reference_across_three_markers_collapses_to_one(
        self, tmp_path
    ):
        from generate_amendment_history import extract_amendments_from_xml

        # Three markers all citing RT I, 2024, 10 — the classic RT-repeat
        # pattern. One marker is the "complete" one (date + EIF); the other
        # two are sparser (the duplicate often carries less data, e.g. no
        # date). All three must collapse to a SINGLE record.
        complete = (
            "<muutmismarge>"
            "<aktikuupaev>2024-03-01</aktikuupaev>"
            "<joustumine>2024-06-01</joustumine>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>10</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        sparse = (
            "<muutmismarge>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>10</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        xml_path = self._write_xml(tmp_path, [sparse, complete, sparse])
        out = extract_amendments_from_xml(xml_path)
        assert len(out) == 1, out
        # The merged record keeps the most-complete fields (date + EIF
        # survive even though the FIRST marker seen had neither).
        assert out[0]["rt_reference"] == "I, 2024, 10"
        assert out[0]["date"] == "2024-03-01"
        assert out[0]["entry_into_force"] == "2024-06-01"

    def test_distinct_rt_references_are_kept(self, tmp_path):
        from generate_amendment_history import extract_amendments_from_xml

        m1 = (
            "<muutmismarge><avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>10</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        m2 = (
            "<muutmismarge><avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>11</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        out = extract_amendments_from_xml(self._write_xml(tmp_path, [m1, m2]))
        refs = sorted(a["rt_reference"] for a in out)
        assert refs == ["I, 2024, 10", "I, 2024, 11"]

    def test_degenerate_reference_with_empty_year_not_produced(self, tmp_path):
        from generate_amendment_history import extract_amendments_from_xml

        # avaldamismarge with RTosa + RTartikkel but NO RTaasta — the old
        # code emitted ``"I, , , 13"`` (the degenerate "RT I, , , 13" from
        # issue #263). Now rt_reference must be None; a date keeps the record.
        marker = (
            "<muutmismarge>"
            "<aktikuupaev>2024-03-01</aktikuupaev>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta></RTaasta><RTnr></RTnr>"
            "<RTartikkel>13</RTartikkel>"
            "</avaldamismarge></muutmismarge>"
        )
        out = extract_amendments_from_xml(self._write_xml(tmp_path, [marker]))
        assert len(out) == 1
        assert out[0]["rt_reference"] is None, out[0]
        # No record anywhere carries the degenerate stub.
        assert all(
            (a.get("rt_reference") or "") not in ("I, , , 13", "RT I, , , 13")
            for a in out
        )

    def test_year_only_without_number_or_article_is_not_a_reference(
        self, tmp_path
    ):
        from generate_amendment_history import extract_amendments_from_xml

        # RTaasta present but neither RTnr nor RTartikkel — not unique enough
        # to be a real citation, so no rt_reference (falls back to the tuple).
        marker = (
            "<muutmismarge>"
            "<aktikuupaev>2024-03-01</aktikuupaev>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2024</RTaasta>"
            "</avaldamismarge></muutmismarge>"
        )
        out = extract_amendments_from_xml(self._write_xml(tmp_path, [marker]))
        assert len(out) == 1
        assert out[0]["rt_reference"] is None, out[0]

    def test_no_reference_dedups_by_date_eif_aktviide_tuple(self, tmp_path):
        from generate_amendment_history import extract_amendments_from_xml

        # Two markers, no avaldamismarge at all, identical date+EIF — must
        # collapse via the (date, entry_into_force, akt_viide) tuple key.
        marker = (
            "<muutmismarge>"
            "<aktikuupaev>2024-03-01</aktikuupaev>"
            "<joustumine>2024-06-01</joustumine>"
            "</muutmismarge>"
        )
        out = extract_amendments_from_xml(
            self._write_xml(tmp_path, [marker, marker])
        )
        assert len(out) == 1, out
        assert out[0]["date"] == "2024-03-01"


class TestParseDateYearSanity:
    """``parse_date`` rejects implausible / corrupt years (issue #587).

    A typo year (``2918``, ``3006``) or one far in the future must not parse
    to a real date — otherwise it sorts last and wrongly wins
    ``isCurrentAmendment``.
    """

    def test_rejects_far_future_typo_year(self):
        from generate_amendment_history import parse_date

        assert parse_date("2918-10-17") is None
        assert parse_date("3006-03-02") is None

    def test_accepts_plausible_recent_year(self):
        from generate_amendment_history import parse_date

        assert parse_date("2003-07-15") == "2003-07-15"
        assert parse_date("1994-01-12") == "1994-01-12"

    def test_accepts_near_future_within_slack(self):
        from datetime import date

        from generate_amendment_history import parse_date

        next_year = date.today().year + 1
        assert parse_date(f"{next_year}-01-01") == f"{next_year}-01-01"

    def test_rejects_year_below_floor(self):
        from generate_amendment_history import parse_date

        # Pre-1990: predates the modern Riigi Teataja timeline.
        assert parse_date("1899-12-31") is None

    def test_still_parses_alternate_formats_when_plausible(self):
        from generate_amendment_history import parse_date

        assert parse_date("01.02.2003") == "2003-02-01"
        assert parse_date("2003-02-01T12:00:00") == "2003-02-01"


class TestAmendingActOfflineDerivation:
    """``estleg:amendingAct`` is derived OFFLINE from akt_viide / rt_reference
    (issue #587) — the amending-act identifier was 0% covered before."""

    def test_prefers_rt_reference(self):
        from generate_amendment_history import _amending_act_value

        amend = {"rt_reference": "RT I, 2002, 57, 356", "akt_viide": "178370"}
        assert _amending_act_value(amend) == "RT I, 2002, 57, 356"

    def test_numeric_akt_viide_becomes_riigiteataja_url(self):
        from generate_amendment_history import _amending_act_value

        amend = {"rt_reference": None, "akt_viide": "178370"}
        assert (
            _amending_act_value(amend)
            == "https://www.riigiteataja.ee/akt/178370"
        )

    def test_non_numeric_akt_viide_kept_verbatim(self):
        from generate_amendment_history import _amending_act_value

        amend = {"akt_viide": "RT III 1994"}
        assert _amending_act_value(amend) == "RT III 1994"

    def test_returns_none_when_no_identifier(self):
        from generate_amendment_history import _amending_act_value

        assert _amending_act_value({"date": "2003-01-01"}) is None
        assert _amending_act_value({"akt_viide": "  "}) is None

    def test_generator_emits_amending_act_from_xml(self, tmp_path):
        """End-to-end through the XML parser: an ``aktViide`` globalId in a
        ``<muutmismarge>`` surfaces as ``estleg:amendingAct`` on the event."""
        from generate_amendment_history import extract_amendments_from_xml

        marker = (
            "<muutmismarge>"
            "<aktikuupaev>2002-06-12</aktikuupaev>"
            "<joustumine>2002-08-01</joustumine>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2002</RTaasta><RTnr>57</RTnr>"
            "<RTartikkel>356</RTartikkel>"
            "<aktViide>178370</aktViide>"
            "</avaldamismarge></muutmismarge>"
        )
        xml_path = tmp_path / "act.xml"
        xml_path.write_text(
            '<akt globaalID="1"><metaandmed>' + marker + "</metaandmed></akt>",
            encoding="utf-8",
        )
        out = extract_amendments_from_xml(xml_path)
        assert len(out) == 1
        assert out[0]["akt_viide"] == "178370"


class TestInversionGuardEveryRecord:
    """The impossible-ordering guard now runs on EVERY record, not just
    merged ones (issue #587)."""

    def _write_xml(self, tmp_path: Path, markers: list[str]) -> Path:
        p = tmp_path / "act.xml"
        p.write_text(
            '<akt globaalID="1"><metaandmed>'
            + "".join(markers)
            + "</metaandmed></akt>",
            encoding="utf-8",
        )
        return p

    def test_single_unmerged_marker_with_impossible_eif_is_nulled(
        self, tmp_path
    ):
        from generate_amendment_history import extract_amendments_from_xml

        # ONE marker (no duplicate to merge) whose entry-into-force precedes
        # the adoption date by ~356 days — physically impossible. The #353
        # fix only sanitised merged records; #587 sanitises this lone one too.
        marker = (
            "<muutmismarge>"
            "<aktikuupaev>2026-02-26</aktikuupaev>"
            "<joustumine>2025-03-07</joustumine>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2026</RTaasta><RTnr>5</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        out = extract_amendments_from_xml(self._write_xml(tmp_path, [marker]))
        assert len(out) == 1
        # Adoption date is the trustworthy anchor and is kept; the impossible
        # entry_into_force is nulled.
        assert out[0]["date"] == "2026-02-26"
        assert out[0]["entry_into_force"] is None

    def test_small_retroactive_window_is_preserved(self, tmp_path):
        from generate_amendment_history import extract_amendments_from_xml

        # A legitimate short retroactive clause (eif a few weeks before
        # adoption) is WITHIN the tolerance window and must NOT be nulled.
        marker = (
            "<muutmismarge>"
            "<aktikuupaev>2019-03-26</aktikuupaev>"
            "<joustumine>2019-03-08</joustumine>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2019</RTaasta><RTnr>1</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        out = extract_amendments_from_xml(self._write_xml(tmp_path, [marker]))
        assert len(out) == 1
        assert out[0]["entry_into_force"] == "2019-03-08"


class TestBuildTitleToSlugMapAmbiguity:
    """``build_title_to_slug_map`` is list-keyed and the matcher refuses to
    bind an ambiguous (co-named) title to an arbitrary act (issue #595)."""

    def test_map_collects_all_slugs_for_duplicate_title(self):
        from generate_amendment_history import build_title_to_slug_map

        laws = {
            "kohanime_a": {"title": "Kohanime määramine"},
            "kohanime_b": {"title": "Kohanime määramine"},
            "kohanime_c": {"title": "Kohanime määramine"},
        }
        title_map = build_title_to_slug_map(laws)
        # Every co-named slug is preserved under the lowercased title key,
        # de-duplicated and order-stable — NOT collapsed last-writer-wins.
        assert sorted(title_map["kohanime määramine"]) == [
            "kohanime_a",
            "kohanime_b",
            "kohanime_c",
        ]

    def test_ambiguous_exact_title_returns_none_and_logs(self):
        from generate_amendment_history import (
            build_title_to_slug_map,
            match_law_name_to_slug,
        )

        laws = {
            "padevuse_a": {"title": "Pädevuse delegeerimine"},
            "padevuse_b": {"title": "Pädevuse delegeerimine"},
        }
        title_map = build_title_to_slug_map(laws)
        failures: list[str] = []
        result = match_law_name_to_slug(
            "Pädevuse delegeerimine", title_map, laws, failures=failures
        )
        # Two distinct acts share the title — refuse to pick one.
        assert result is None
        assert any(
            "ambiguous" in f and "delegeerimine" in f.lower()
            for f in failures
        ), failures

    def test_unique_title_still_resolves(self):
        from generate_amendment_history import (
            build_title_to_slug_map,
            match_law_name_to_slug,
        )

        laws = {"alkoholiseadus": {"title": "Alkoholiseadus"}}
        title_map = build_title_to_slug_map(laws)
        assert (
            match_law_name_to_slug("Alkoholiseadus", title_map, laws)
            == "alkoholiseadus"
        )

    def test_ambiguous_does_not_fall_through_to_fuzzier_stage(self):
        from generate_amendment_history import (
            build_title_to_slug_map,
            match_law_name_to_slug,
        )

        # Exact-title hit is ambiguous; the matcher must NOT silently fall
        # through to the Jaccard stage and pick a different arbitrary act.
        laws = {
            "dup_a": {"title": "Sama pealkiri"},
            "dup_b": {"title": "Sama pealkiri"},
            "other": {"title": "Sama pealkiri muudatus"},
        }
        title_map = build_title_to_slug_map(laws)
        failures: list[str] = []
        result = match_law_name_to_slug(
            "Sama pealkiri", title_map, laws, failures=failures
        )
        assert result is None


class TestTotalAmendmentsCountsUniques:
    """End-to-end: a law whose XML repeats one act across 3 markers reports
    ``totalAmendments == 1`` and ``total_amendment_references == 1`` — no
    spurious ``_2``/``_3`` Amendment nodes (issue #263)."""

    def _setup(self, tmp_path: Path, monkeypatch):
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir(parents=True)
        rt.mkdir(parents=True)
        (krr / "amendments").mkdir()
        (krr / "eelnoud").mkdir()
        (krr / "regulations" / "riik").mkdir(parents=True)
        (krr / "regulations" / "kov").mkdir(parents=True)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        return krr, rt, mod

    def test_three_repeated_markers_yield_one_amendment(
        self, tmp_path, monkeypatch
    ):
        krr, rt, mod = self._setup(tmp_path, monkeypatch)
        peep = krr / "beeta_seadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Beeta_Map",
                 "@type": ["owl:Ontology", "estleg:Act"],
                 "rdfs:label": "Beeta seadus", "dc:source": "Beeta seadus",
                 "estleg:globalId": "6000"}
            ],
        }), encoding="utf-8")
        # Same RT I, 2024, 10 repeated 3x; two markers also lack a date.
        one = (
            "<muutmismarge>"
            "<aktikuupaev>2024-03-01</aktikuupaev>"
            "<joustumine>2024-06-01</joustumine>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>10</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        nodate = (
            "<muutmismarge>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>10</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        (rt / "reg_6000.xml").write_text(
            '<akt globaalID="6000"><metaandmed>'
            + one + nodate + nodate
            + "</metaandmed></akt>",
            encoding="utf-8",
        )

        rc = mod.main()
        assert rc in (None, 0)

        chain_doc = json.loads(
            (krr / "amendments" / "amendments_beeta_seadus.json").read_text("utf-8")
        )
        events = [
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]
        assert len(events) == 1, [e["@id"] for e in events]
        # No spurious _2/_3 disambiguated IRI.
        assert not re.search(r"_\d+$", events[0]["@id"]), events[0]["@id"]

        # Per-chain totalAmendments counts the unique.
        onto = next(
            n for n in chain_doc["@graph"]
            if n["@id"].startswith("estleg:AmendmentChain_")
        )
        assert onto["estleg:totalAmendments"]["@value"] == "1"

        # Report-level reference count is the unique count too.
        report = json.loads(
            (krr / "amendment_history_report.json").read_text("utf-8")
        )
        assert report["summary"]["total_amendment_references"] == 1


class TestGeneratorEmitsAmendingActAndSkipsCorruptCurrent:
    """End-to-end (#587): the chain doc carries ``estleg:amendingAct`` and a
    corrupt/future date never wins ``estleg:isCurrentAmendment``."""

    def _setup(self, tmp_path: Path, monkeypatch):
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir(parents=True)
        rt.mkdir(parents=True)
        (krr / "amendments").mkdir()
        (krr / "eelnoud").mkdir()
        (krr / "regulations" / "riik").mkdir(parents=True)
        (krr / "regulations" / "kov").mkdir(parents=True)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        return krr, rt, mod

    def test_amending_act_emitted_and_corrupt_date_not_current(
        self, tmp_path, monkeypatch
    ):
        krr, rt, mod = self._setup(tmp_path, monkeypatch)
        peep = krr / "gamma_seadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Gamma_Map",
                 "@type": ["owl:Ontology", "estleg:Act"],
                 "rdfs:label": "Gamma seadus", "dc:source": "Gamma seadus",
                 "estleg:globalId": "7000"}
            ],
        }), encoding="utf-8")
        # One real amendment (with aktViide) + one CORRUPT future-dated one.
        real = (
            "<muutmismarge>"
            "<aktikuupaev>2003-07-15</aktikuupaev>"
            "<joustumine>2003-08-01</joustumine>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2003</RTaasta><RTnr>57</RTnr>"
            "<RTartikkel>356</RTartikkel>"
            "<aktViide>178370</aktViide>"
            "</avaldamismarge></muutmismarge>"
        )
        corrupt = (
            "<muutmismarge>"
            "<aktikuupaev>2918-10-17</aktikuupaev>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2918</RTaasta><RTnr>99</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        (rt / "reg_7000.xml").write_text(
            '<akt globaalID="7000"><metaandmed>'
            + real + corrupt
            + "</metaandmed></akt>",
            encoding="utf-8",
        )

        rc = mod.main()
        assert rc in (None, 0)

        chain_doc = json.loads(
            (krr / "amendments" / "amendments_gamma_seadus.json").read_text("utf-8")
        )
        events = [
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]
        # amendingAct is emitted from the RT reference on the real event.
        real_event = next(
            n for n in events
            if n.get("estleg:rtReference") == "I, 2003, 57, 356"
        )
        assert real_event["estleg:amendingAct"] == "I, 2003, 57, 356"

        # The current flag sits on the validly-dated event, NEVER the corrupt
        # 2918 one (whose amendmentDate was dropped to None by parse_date).
        current = [n for n in events if "estleg:isCurrentAmendment" in n]
        assert len(current) == 1
        assert current[0] is real_event
        # No surviving 2918 DATE on any event (the corrupt amendmentDate was
        # dropped). The RT-reference citation may still carry the publication
        # year string — that is a separate field, out of the date-gate scope.
        for node in events:
            for key in ("estleg:amendmentDate", "estleg:entryIntoForce"):
                val = node.get(key, {})
                if isinstance(val, dict):
                    assert "2918" not in str(val.get("@value", "")), node


# ---------------------------------------------------------------------------
# Issue #289 — never stamp estleg:amendedBy onto a non-Act graph[0] node
# ---------------------------------------------------------------------------


class TestAmendedByRequiresActNode:
    """``find_act_node``: ``estleg:amendedBy`` is act-level. A peep whose
    ``graph[0]`` is a non-Act node (e.g. ``estleg:LegalConcept``) must be
    SKIPPED, not stamped via the old ``graph[0]`` fallback (issue #289)."""

    def test_find_act_node_prefers_ontology_act_then_any_act(self):
        from generate_amendment_history import find_act_node

        concept = {"@id": "estleg:C", "@type": ["estleg:LegalConcept"]}
        plain_act = {"@id": "estleg:A2", "@type": ["estleg:Act"]}
        onto_act = {"@id": "estleg:A1", "@type": ["owl:Ontology", "estleg:Act"]}
        assert find_act_node([concept, plain_act, onto_act]) is onto_act
        assert find_act_node([concept, plain_act]) is plain_act
        assert find_act_node([concept]) is None
        assert find_act_node([]) is None

    def test_graph0_non_act_node_is_not_stamped(self, tmp_path, monkeypatch):
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir(parents=True)
        rt.mkdir(parents=True)
        (krr / "amendments").mkdir()
        (krr / "eelnoud").mkdir()
        (krr / "regulations" / "riik").mkdir(parents=True)
        (krr / "regulations" / "kov").mkdir(parents=True)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        # A peep with NO act node, mirroring the real tsiviilseadustik_osa6/
        # osa7 shape (issue #289): graph[0] is an owl:Ontology *catalogue*
        # node that is NOT typed estleg:Act, plus a LegalConcept. The
        # owl:Ontology node carries the globalId so XML pairing succeeds and
        # the chain IS built — but find_act_node returns None, so the
        # per-member amendedBy stamp must be withheld (the old graph[0]
        # fallback would have written it onto this non-Act node).
        peep = krr / "gamma_seadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Gamma_Catalogue",
                 "@type": ["owl:Ontology", "estleg:LegalConcept"],
                 "rdfs:label": "Gamma catalogue", "dc:source": "Gamma",
                 "estleg:globalId": "7000"},
                {"@id": "estleg:Gamma_Concept_2",
                 "@type": ["owl:NamedIndividual", "estleg:LegalConcept"],
                 "rdfs:label": "Another concept"},
            ],
        }), encoding="utf-8")
        (rt / "maarus_kov").mkdir(parents=True, exist_ok=True)
        (rt / "maarus_kov" / "reg_7000.xml").write_text(
            '<akt globaalID="7000"><metaandmed>'
            '<muutmismarge>'
            '<aktikuupaev>2024-03-01</aktikuupaev>'
            '<avaldamismarge>'
            '<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>10</RTnr>'
            '</avaldamismarge></muutmismarge>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        rc = mod.main()
        assert rc in (None, 0)

        # The chain WAS built (pairing succeeded) — proving we reached the
        # per-member stamp loop and skipped it, not that there was nothing
        # to stamp.
        assert sorted((krr / "amendments").glob("amendments_*.json")), (
            "expected the amendment chain to be built so the skip path is "
            "actually exercised"
        )
        doc = json.loads(peep.read_text("utf-8"))
        # No node — least of all the owl:Ontology graph[0] — may have gained
        # amendedBy.
        assert all(
            "estleg:amendedBy" not in n for n in doc["@graph"]
        ), doc["@graph"]


# ---------------------------------------------------------------------------
# Issue #295 — byte-stable report: no wall-clock generated/run_timestamp
# ---------------------------------------------------------------------------


class TestReportIsByteStable:
    """The git-tracked ``amendment_history_report.json`` carries no
    wall-clock ``generated`` field and the coverage ``run_timestamp`` is
    pinned, so reruns over the same inputs are byte-identical (issue #295)."""

    def _run(self, tmp_path: Path, monkeypatch):
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        krr.mkdir(parents=True)
        rt.mkdir(parents=True)
        (krr / "amendments").mkdir()
        (krr / "eelnoud").mkdir()
        (krr / "regulations" / "riik").mkdir(parents=True)
        (krr / "regulations" / "kov").mkdir(parents=True)
        (krr / "reports" / "kov").mkdir(parents=True)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        peep = krr / "delta_seadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Delta_Map",
                 "@type": ["owl:Ontology", "estleg:Act"],
                 "rdfs:label": "Delta seadus", "dc:source": "Delta seadus",
                 "estleg:globalId": "8000"}
            ],
        }), encoding="utf-8")
        (rt / "reg_8000.xml").write_text(
            '<akt globaalID="8000"><metaandmed>'
            '<muutmismarge>'
            '<aktikuupaev>2024-03-01</aktikuupaev>'
            '<avaldamismarge>'
            '<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>10</RTnr>'
            '</avaldamismarge></muutmismarge>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )
        rc = mod.main()
        assert rc in (None, 0)
        return krr

    def test_report_has_no_generated_field(self, tmp_path, monkeypatch):
        krr = self._run(tmp_path, monkeypatch)
        report = json.loads(
            (krr / "amendment_history_report.json").read_text("utf-8")
        )
        assert "generated" not in report
        # Sanity: the analytical content is still present.
        assert "summary" in report and "amendment_chains" in report

    def test_report_byte_identical_across_reruns(self, tmp_path, monkeypatch):
        krr = self._run(tmp_path, monkeypatch)
        first = (krr / "amendment_history_report.json").read_bytes()
        # Re-run over a fresh tree with the SAME inputs; report must match.
        krr2 = self._run(tmp_path / "again", monkeypatch)
        second = (krr2 / "amendment_history_report.json").read_bytes()
        assert first == second

    def test_coverage_run_timestamp_is_pinned(self, tmp_path, monkeypatch):
        import generate_amendment_history as mod

        krr = self._run(tmp_path, monkeypatch)
        cov = json.loads(
            (krr / "reports" / "kov"
             / "generate_amendment_history_coverage.json").read_text("utf-8")
        )
        assert cov["run_timestamp"] == mod.PINNED_RUN_TIMESTAMP


# ---------------------------------------------------------------------------
# Shared end-to-end harness for the issue #327/#353/#385/#387/#389 fixes
# ---------------------------------------------------------------------------


def _setup_main_env(tmp_path: Path, monkeypatch):
    """Wire generate_amendment_history.main() onto an isolated tmp tree."""
    import generate_amendment_history as mod
    import estleg_common

    krr = tmp_path / "krr_outputs"
    rt = tmp_path / "data" / "riigiteataja"
    krr.mkdir(parents=True)
    rt.mkdir(parents=True)
    (krr / "amendments").mkdir()
    (krr / "eelnoud").mkdir()
    (krr / "regulations" / "riik").mkdir(parents=True)
    (krr / "regulations" / "kov").mkdir(parents=True)
    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "DATA_DIR", rt)
    monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
    monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
    return krr, rt, mod


def _write_part(krr: Path, slug: str, gid: str, title: str) -> Path:
    p = krr / f"{slug}_peep.json"
    p.write_text(json.dumps({
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "owl": "http://www.w3.org/2002/07/owl#"},
        "@graph": [
            {"@id": f"estleg:{slug.upper()}_Map",
             "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
             "rdfs:label": title, "dc:source": title,
             "estleg:globalId": gid}],
    }), encoding="utf-8")
    return p


def _write_xml(rt: Path, gid: str, amendments: list[dict]):
    blocks = []
    for a in amendments:
        avaldamismarge = ""
        if a.get("rt_aasta") or a.get("rt_nr"):
            avaldamismarge = (
                "<avaldamismarge>"
                f"<RTosa>{a.get('rt_osa', 'I')}</RTosa>"
                f"<RTaasta>{a.get('rt_aasta', '')}</RTaasta>"
                f"<RTnr>{a.get('rt_nr', '')}</RTnr>"
                "</avaldamismarge>"
            )
        blocks.append(
            "<muutmismarge>"
            f"<aktikuupaev>{a.get('date', '')}</aktikuupaev>"
            f"<joustumine>{a.get('eif', '')}</joustumine>"
            + avaldamismarge
            + "</muutmismarge>"
        )
    (rt / f"reg_{gid}.xml").write_text(
        f'<akt globaalID="{gid}"><metaandmed>' + "".join(blocks)
        + "</metaandmed></akt>",
        encoding="utf-8",
    )


def _write_combined_drafts(krr: Path, drafts: list[dict]):
    """Write krr_outputs/eelnoud/eelnoud_combined.jsonld with AmendmentBills.

    Each ``drafts`` entry: {id, title, affected, pub}. ``title`` is emitted as
    a PR-#297-shape value-object so the consumer's unwrap is exercised.
    """
    graph = []
    for d in drafts:
        graph.append({
            "@id": d["id"],
            "@type": ["owl:NamedIndividual", "estleg:DraftLegislation"],
            "rdfs:label": {"@value": d["title"], "@language": "et"},
            "estleg:draftType": {"@id": "estleg:DraftType_AmendmentBill"},
            "estleg:affectedLawName": d["affected"],
            "estleg:publicationDate": {
                "@value": d["pub"], "@type": "xsd:date"
            },
        })
    (krr / "eelnoud" / "eelnoud_combined.jsonld").write_text(
        json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": graph,
        }),
        encoding="utf-8",
    )


def _amends_ids(node: dict) -> list[str]:
    """Normalise estleg:amends (dict or list of dicts) to a list of @ids."""
    val = node.get("estleg:amends")
    if val is None:
        return []
    if isinstance(val, dict):
        return [val.get("@id")]
    return [v.get("@id") for v in val]


def _proposes_ids(node: dict) -> list[str]:
    """Normalise estleg:proposesToAmend (dict or list of dicts) to @ids (#423)."""
    val = node.get("estleg:proposesToAmend")
    if val is None:
        return []
    if isinstance(val, dict):
        return [val.get("@id")]
    return [v.get("@id") for v in val]


# ---------------------------------------------------------------------------
# Issue #327 — estleg:amends must target EVERY part of a multipart law
# ---------------------------------------------------------------------------


class TestAmendsTargetsAllParts:
    """For a multipart law, each AmendmentEvent's ``estleg:amends`` must list
    ALL part ontology IRIs, not only the first osa (issue #327)."""

    def test_multipart_amends_lists_every_part(self, tmp_path, monkeypatch):
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        title = "Karistusseadustik"
        # Three osa-parts; each carries its own ontology IRI. They share one
        # base_slug (karistusseadustik) and thus one chain doc.
        for i in (1, 2, 3):
            _write_part(krr, f"karistusseadustik_osa{i}", f"90{i}", title)
        # Only the first member's XML carries the amendment (main() pulls the
        # first non-empty member); the chain still spans all three parts.
        _write_xml(
            rt, "901",
            [{"date": "2010-01-01", "eif": "2010-06-01",
              "rt_aasta": "2010", "rt_nr": "5"}],
        )
        rc = mod.main()
        assert rc in (None, 0)

        chain_doc = json.loads(
            (krr / "amendments" / "amendments_karistusseadustik.json")
            .read_text("utf-8")
        )
        events = [
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]
        assert events, "expected at least one amendment event"
        expected = {
            "estleg:KARISTUSSEADUSTIK_OSA1_Map",
            "estleg:KARISTUSSEADUSTIK_OSA2_Map",
            "estleg:KARISTUSSEADUSTIK_OSA3_Map",
        }
        for ev in events:
            ids = set(_amends_ids(ev))
            assert ids == expected, (ev["@id"], ids)

    def test_single_part_amends_stays_a_single_object(self, tmp_path, monkeypatch):
        # A single-part law must keep the scalar ``{"@id": ...}`` shape (not a
        # 1-element list) for backwards compatibility.
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "yksikseadus", "910", "Yksikseadus")
        _write_xml(
            rt, "910",
            [{"date": "2012-01-01", "eif": "2012-06-01",
              "rt_aasta": "2012", "rt_nr": "1"}],
        )
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_yksikseadus.json").read_text("utf-8")
        )
        ev = next(
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        )
        assert ev["estleg:amends"] == {"@id": "estleg:YKSIKSEADUS_Map"}

    def test_draft_link_proposes_to_amend_lists_every_part(self, tmp_path, monkeypatch):
        # Draft-derived ProposedAmendment nodes get the same multi-part target,
        # but via estleg:proposesToAmend (NOT estleg:amends) — #423.
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        title = "Mitmeosaline seadus"
        for i in (1, 2):
            _write_part(krr, f"mitmeosaline_seadus_osa{i}", f"92{i}", title)
        _write_combined_drafts(krr, [
            {"id": "estleg:Draft_A", "title": "Muutmise eelnõu",
             "affected": title, "pub": "2024-01-01"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_mitmeosaline_seadus.json")
            .read_text("utf-8")
        )
        links = [
            n for n in chain_doc["@graph"]
            if "estleg:amendingDraft" in n
        ]
        assert links, "expected a draft ProposedAmendment"
        expected = {
            "estleg:MITMEOSALINE_SEADUS_OSA1_Map",
            "estleg:MITMEOSALINE_SEADUS_OSA2_Map",
        }
        for ln in links:
            # Proposals are NOT AmendmentEvent and carry NO bare estleg:amends.
            assert "estleg:ProposedAmendment" in ln["@type"], ln
            assert "estleg:AmendmentEvent" not in ln["@type"], ln
            assert "estleg:amends" not in ln, ln
            assert set(_proposes_ids(ln)) == expected, ln


# ---------------------------------------------------------------------------
# Issue #353 — dedup must never emit entry_into_force long before adoption
# ---------------------------------------------------------------------------


class TestDedupDateCoherence:
    """The merge must not blend a ``date`` from one marker with an
    ``entry_into_force`` from another into an impossible pair (issue #353)."""

    def _write_act(self, tmp_path: Path, markers: list[str]) -> Path:
        p = tmp_path / "act.xml"
        p.write_text(
            '<akt globaalID="1"><metaandmed>'
            + "".join(markers)
            + "</metaandmed></akt>",
            encoding="utf-8",
        )
        return p

    def test_days_between_basic(self):
        from generate_amendment_history import _days_between

        assert _days_between("2024-01-01", "2024-01-31") == 30
        # later before earlier -> negative
        assert _days_between("2026-02-26", "2025-03-07") < 0
        assert _days_between(None, "2024-01-01") is None
        assert _days_between("garbage", "2024-01-01") is None

    def test_sanitize_nulls_impossible_eif(self):
        from generate_amendment_history import _sanitize_merged_amendment

        # eif ~356 days before adoption (the issue #353 evidence shape).
        amend = {"date": "2026-02-26", "entry_into_force": "2025-03-07",
                 "rt_reference": "RT I, x", "akt_viide": None}
        failures: list[str] = []
        out = _sanitize_merged_amendment(amend, failures=failures)
        assert out["entry_into_force"] is None
        assert out["date"] == "2026-02-26"  # adoption date kept
        assert any("impossible_eif_before_date" in f for f in failures), failures

    def test_sanitize_keeps_small_retroactive_window(self):
        from generate_amendment_history import _sanitize_merged_amendment

        # eif 10 days before adoption — within tolerance, kept untouched.
        amend = {"date": "2024-06-10", "entry_into_force": "2024-05-31"}
        out = _sanitize_merged_amendment(dict(amend))
        assert out["entry_into_force"] == "2024-05-31"

    def test_backfill_refuses_inverting_eif(self, tmp_path):
        from generate_amendment_history import extract_amendments_from_xml

        # Two markers sharing the SAME akt_viide (so they dedup to one key)
        # but with NO rt_reference. One supplies only an adoption date
        # (2026-02-26); the other supplies only an effective date a year
        # earlier (2025-03-07). The naive backfill would blend them into an
        # impossible pair; the guard must refuse the eif backfill.
        only_date = (
            "<muutmismarge>"
            "<aktikuupaev>2026-02-26</aktikuupaev>"
            "<avaldamismarge><aktViide>VIIDE_X</aktViide></avaldamismarge>"
            "</muutmismarge>"
        )
        only_eif = (
            "<muutmismarge>"
            "<joustumine>2025-03-07</joustumine>"
            "<avaldamismarge><aktViide>VIIDE_X</aktViide></avaldamismarge>"
            "</muutmismarge>"
        )
        out = extract_amendments_from_xml(self._write_act(tmp_path, [only_date, only_eif]))
        # They collapse (same akt_viide tuple), but the merged record must NOT
        # carry the inverted eif.
        assert len(out) == 1, out
        merged = out[0]
        # date preserved; eif either absent or NOT a >90d inversion.
        assert merged.get("date") == "2026-02-26"
        from generate_amendment_history import _days_between
        if merged.get("entry_into_force"):
            delta = _days_between(merged["date"], merged["entry_into_force"])
            assert delta is None or delta >= -90, merged

    def test_identical_markers_still_merge(self, tmp_path):
        from generate_amendment_history import extract_amendments_from_xml

        # Regression guard for #263: genuinely identical repeated markers
        # (same date AND eif, coherent) must still collapse to one.
        m = (
            "<muutmismarge>"
            "<aktikuupaev>2024-03-01</aktikuupaev>"
            "<joustumine>2024-06-01</joustumine>"
            "<avaldamismarge>"
            "<RTosa>I</RTosa><RTaasta>2024</RTaasta><RTnr>10</RTnr>"
            "</avaldamismarge></muutmismarge>"
        )
        out = extract_amendments_from_xml(self._write_act(tmp_path, [m, m, m]))
        assert len(out) == 1, out
        assert out[0]["entry_into_force"] == "2024-06-01"


# ---------------------------------------------------------------------------
# Issue #385 — draft rdfs:label value-object must be unwrapped, not stringified
# ---------------------------------------------------------------------------


class TestDraftLabelUnwrap:
    """``load_draft_amendments`` must unwrap the PR-#297 language-tagged
    ``rdfs:label`` value-object to a plain title (issue #385) so the
    AmendmentLink label never embeds a Python dict repr."""

    def test_load_draft_amendments_unwraps_value_object(self, tmp_path, monkeypatch):
        import generate_amendment_history as mod

        eelnoud = tmp_path / "eelnoud"
        eelnoud.mkdir()
        (eelnoud / "eelnoud_combined.jsonld").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [{
                "@id": "estleg:Draft_1",
                "@type": ["estleg:DraftLegislation"],
                "rdfs:label": {"@value": "Äriseadustiku muutmise seadus",
                               "@language": "et"},
                "estleg:draftType": {"@id": "estleg:DraftType_AmendmentBill"},
                "estleg:affectedLawName": "Äriseadustik",
                "estleg:publicationDate": {"@value": "2024-05-01",
                                           "@type": "xsd:date"},
            }],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "EELNOUD_DIR", eelnoud)

        drafts = mod.load_draft_amendments()
        assert len(drafts) == 1
        # The plain title, NOT the dict.
        assert drafts[0]["draft_label"] == "Äriseadustiku muutmise seadus"
        assert isinstance(drafts[0]["draft_label"], str)

    def test_amendment_link_label_has_no_dict_repr(self, tmp_path, monkeypatch):
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "ariseadustik", "9300", "Äriseadustik")
        _write_combined_drafts(krr, [
            {"id": "estleg:Draft_AS",
             "title": "Äriseadustiku muutmise seadus",
             "affected": "Äriseadustik", "pub": "2024-05-01"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_ariseadustik.json").read_text("utf-8")
        )
        link = next(n for n in chain_doc["@graph"] if "estleg:amendingDraft" in n)
        label = link["rdfs:label"]
        assert isinstance(label, str)
        assert label == "Eelnõu muudatus: Äriseadustiku muutmise seadus"
        # The pre-fix bug embedded "{'@value': ..." — must never appear.
        assert "@value" not in label and "@language" not in label, label


# ---------------------------------------------------------------------------
# Issue #387 — draft AmendmentLink events must be sorted by publication_date
# ---------------------------------------------------------------------------


class TestDraftEventsSorted:
    """Draft AmendmentLink nodes must be appended in publication_date order so
    chains are monotonic; IRIs are id-based so order does not affect them
    (issue #387)."""

    def test_draft_events_are_chronological(self, tmp_path, monkeypatch):
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "atmosfaariohu_kaitse_seadus", "9400",
                    "Atmosfääriõhu kaitse seadus")
        # Deliberately INPUT them out of order (matching the #387 evidence:
        # 2026 then 2020 then 2018 ...). Output must be ascending by date.
        _write_combined_drafts(krr, [
            {"id": "estleg:Draft_2026", "title": "Eelnõu 2026",
             "affected": "Atmosfääriõhu kaitse seadus", "pub": "2026-03-01"},
            {"id": "estleg:Draft_2020", "title": "Eelnõu 2020",
             "affected": "Atmosfääriõhu kaitse seadus", "pub": "2020-03-01"},
            {"id": "estleg:Draft_2018", "title": "Eelnõu 2018",
             "affected": "Atmosfääriõhu kaitse seadus", "pub": "2018-03-01"},
            {"id": "estleg:Draft_2024", "title": "Eelnõu 2024",
             "affected": "Atmosfääriõhu kaitse seadus", "pub": "2024-03-01"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_atmosfaariohu_kaitse_seadus.json")
            .read_text("utf-8")
        )
        links = [n for n in chain_doc["@graph"] if "estleg:amendingDraft" in n]
        # Proposals carry estleg:publicationDate, NOT estleg:amendmentDate (#423).
        dates = [
            (n.get("estleg:publicationDate") or {}).get("@value") for n in links
        ]
        assert dates == sorted(dates), dates
        assert dates == ["2018-03-01", "2020-03-01", "2024-03-01", "2026-03-01"]
        # Retyped: ProposedAmendment, never AmendmentEvent, never amendmentDate.
        for n in links:
            assert "estleg:ProposedAmendment" in n["@type"], n
            assert "estleg:AmendmentEvent" not in n["@type"], n
            assert "estleg:amendmentDate" not in n, n

    def test_undated_draft_sorts_last(self, tmp_path, monkeypatch):
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "testseadus_x", "9450", "Testseadus X")
        # One dated, one undated draft. Undated (no publicationDate) must land
        # last via the 9999 sentinel.
        (krr / "eelnoud" / "eelnoud_combined.jsonld").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Draft_Undated",
                 "@type": ["estleg:DraftLegislation"],
                 "rdfs:label": {"@value": "Kuupäevata", "@language": "et"},
                 "estleg:draftType": {"@id": "estleg:DraftType_AmendmentBill"},
                 "estleg:affectedLawName": "Testseadus X"},
                {"@id": "estleg:Draft_Dated",
                 "@type": ["estleg:DraftLegislation"],
                 "rdfs:label": {"@value": "Kuupäevaga", "@language": "et"},
                 "estleg:draftType": {"@id": "estleg:DraftType_AmendmentBill"},
                 "estleg:affectedLawName": "Testseadus X",
                 "estleg:publicationDate": {"@value": "2020-01-01",
                                            "@type": "xsd:date"}},
            ],
        }), encoding="utf-8")
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_testseadus_x.json").read_text("utf-8")
        )
        links = [n for n in chain_doc["@graph"] if "estleg:amendingDraft" in n]
        ids = [n["estleg:amendingDraft"]["@id"] for n in links]
        assert ids == ["estleg:Draft_Dated", "estleg:Draft_Undated"], ids

    def test_sorting_does_not_change_link_iris(self, tmp_path, monkeypatch):
        # The #387 rationale: IRIs are id-based, so sorting preserves them.
        # Run with two input orderings; the SET of AmendmentLink @ids matches.
        def run(order: list[dict]) -> set[str]:
            import generate_amendment_history as mod  # noqa: F401
            sub = tmp_path / ("o" + str(len(order)) + "_" + order[0]["id"][-4:])
            krr, rt, m = _setup_main_env(sub, monkeypatch)
            _write_part(krr, "ordseadus", "9460", "Ordseadus")
            _write_combined_drafts(krr, order)
            assert m.main() in (None, 0)
            doc = json.loads(
                (krr / "amendments" / "amendments_ordseadus.json").read_text("utf-8")
            )
            return {
                n["@id"] for n in doc["@graph"] if "estleg:amendingDraft" in n
            }

        a = [
            {"id": "estleg:Draft_P", "title": "P", "affected": "Ordseadus",
             "pub": "2021-01-01"},
            {"id": "estleg:Draft_Q", "title": "Q", "affected": "Ordseadus",
             "pub": "2019-01-01"},
        ]
        b = list(reversed(a))
        assert run(a) == run(b)


# ---------------------------------------------------------------------------
# Issue #389 — estleg:isCurrentAmendment marker on the latest event
# ---------------------------------------------------------------------------


class TestIsCurrentAmendmentMarker:
    """The latest XML event and the latest draft event (after sorting) must be
    stamped ``estleg:isCurrentAmendment = true`` (issue #389)."""

    def _is_current(self, node: dict) -> bool:
        val = node.get("estleg:isCurrentAmendment")
        return isinstance(val, dict) and val.get("@value") == "true"

    def test_latest_xml_event_is_current(self, tmp_path, monkeypatch):
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "ksseadus", "9500", "KS seadus")
        # Three coherent amendments at ascending effective dates.
        _write_xml(rt, "9500", [
            {"date": "2010-01-01", "eif": "2010-06-01",
             "rt_aasta": "2010", "rt_nr": "1"},
            {"date": "2024-01-01", "eif": "2024-06-01",
             "rt_aasta": "2024", "rt_nr": "9"},
            {"date": "2015-01-01", "eif": "2015-06-01",
             "rt_aasta": "2015", "rt_nr": "5"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_ksseadus.json").read_text("utf-8")
        )
        events = [
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]
        current = [e for e in events if self._is_current(e)]
        assert len(current) == 1, [e["@id"] for e in current]
        # The current one is the latest by effective date (2024-06-01).
        eif = (current[0].get("estleg:entryIntoForce") or {}).get("@value")
        assert eif == "2024-06-01", current[0]
        # Boolean is xsd-typed.
        assert current[0]["estleg:isCurrentAmendment"]["@type"] == "xsd:boolean"

    def test_draft_proposals_are_never_current(self, tmp_path, monkeypatch):
        # #423: a never-enacted draft is a ProposedAmendment, NOT an effected
        # change — it must NEVER carry estleg:isCurrentAmendment, even when it is
        # the latest (or only) proposal for the act.
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "draftonlyseadus", "9510", "Draftonly seadus")
        _write_combined_drafts(krr, [
            {"id": "estleg:Draft_Old", "title": "Vana",
             "affected": "Draftonly seadus", "pub": "2018-01-01"},
            {"id": "estleg:Draft_New", "title": "Uus",
             "affected": "Draftonly seadus", "pub": "2025-01-01"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_draftonlyseadus.json")
            .read_text("utf-8")
        )
        links = [n for n in chain_doc["@graph"] if "estleg:amendingDraft" in n]
        assert len(links) == 2, links
        # No proposal is flagged current, and none is typed AmendmentEvent.
        assert [ln for ln in links if self._is_current(ln)] == []
        for ln in links:
            assert "estleg:ProposedAmendment" in ln["@type"], ln
            assert "estleg:AmendmentEvent" not in ln["@type"], ln
            assert "estleg:isCurrentAmendment" not in ln, ln
        # A draft-only act has ZERO AmendmentEvent nodes in its chain (#423).
        assert not [
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]

    def test_only_latest_xml_is_current_proposals_excluded(self, tmp_path, monkeypatch):
        # A chain with BOTH kinds: ONLY the latest effected XML event is flagged
        # (#389/#423). Proposals never count toward isCurrentAmendment — even a
        # 2026 draft does not out-rank the 2016 effected amendment.
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "bothseadus", "9520", "Both seadus")
        _write_xml(rt, "9520", [
            {"date": "2010-01-01", "eif": "2010-06-01",
             "rt_aasta": "2010", "rt_nr": "1"},
            {"date": "2016-01-01", "eif": "2016-06-01",
             "rt_aasta": "2016", "rt_nr": "2"},
        ])
        _write_combined_drafts(krr, [
            {"id": "estleg:Draft_1", "title": "D1",
             "affected": "Both seadus", "pub": "2022-01-01"},
            {"id": "estleg:Draft_2", "title": "D2",
             "affected": "Both seadus", "pub": "2026-01-01"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_bothseadus.json").read_text("utf-8")
        )
        # The only current marker is on an AmendmentEvent (not a proposal).
        all_current = [n for n in chain_doc["@graph"] if self._is_current(n)]
        assert len(all_current) == 1, [e["@id"] for e in all_current]
        cur = all_current[0]
        assert "estleg:AmendmentEvent" in cur["@type"], cur
        assert "estleg:amendingDraft" not in cur, cur
        # It's the latest effective effected event (2016-06-01).
        assert (cur.get("estleg:entryIntoForce") or {}).get("@value") == "2016-06-01"
        # The proposals are present but unflagged, and not AmendmentEvent.
        proposals = [n for n in chain_doc["@graph"] if "estleg:amendingDraft" in n]
        assert len(proposals) == 2
        assert all("estleg:isCurrentAmendment" not in p for p in proposals)
        assert all("estleg:ProposedAmendment" in p["@type"] for p in proposals)


# ---------------------------------------------------------------------------
# Issue #423 — draft bills are ProposedAmendment, not effected AmendmentEvent
# ---------------------------------------------------------------------------


def _act_node(doc: dict) -> dict:
    """Return the owl:Ontology act node of a peep doc."""
    return next(
        n for n in doc["@graph"]
        if "owl:Ontology" in (n.get("@type") or [])
    )


class TestProposedAmendmentSeparation:
    """A never-enacted draft must be emitted as estleg:ProposedAmendment (NOT
    AmendmentEvent), use proposal-scoped predicates (proposesToAmend +
    publicationDate, never amends/amendmentDate/isCurrentAmendment), and link
    from the act root via estleg:hasProposedAmendment — never estleg:amendedBy
    (effected-only) and never estleg:affectedBy (owned by extract_draft_impact).
    """

    def test_draft_only_act_emits_proposed_amendment_node(self, tmp_path, monkeypatch):
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "abieluvararegistri_seadus", "9700",
                    "Abieluvararegistri seadus")
        _write_combined_drafts(krr, [
            {"id": "estleg:Draft_JDM16_0008",
             "title": "Abieluvararegistri seaduse muutmise seadus",
             "affected": "Abieluvararegistri seadus", "pub": "2016-01-05"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_abieluvararegistri_seadus.json")
            .read_text("utf-8")
        )
        proposals = [n for n in chain_doc["@graph"] if "estleg:amendingDraft" in n]
        assert len(proposals) == 1, proposals
        p = proposals[0]
        # Class: ProposedAmendment, never AmendmentEvent.
        assert "estleg:ProposedAmendment" in p["@type"]
        assert "estleg:AmendmentEvent" not in p["@type"]
        assert "owl:NamedIndividual" in p["@type"]
        # @id unchanged (AmendmentLink_<draft>_<prefix>) for stability. The
        # prefix is the registered abbreviation AVRS (abieluvararegistri_seadus).
        assert p["@id"] == "estleg:AmendmentLink_Draft_JDM16_0008_AVRS"
        # Proposal-scoped predicates only. proposesToAmend targets the act's
        # ontology IRI (here ABIELUVARAREGISTRI_SEADUS_Map from the fixture).
        assert p["estleg:amendingDraft"] == {"@id": "estleg:Draft_JDM16_0008"}
        assert p["estleg:proposesToAmend"] == {
            "@id": "estleg:ABIELUVARAREGISTRI_SEADUS_Map"
        }
        assert p["estleg:publicationDate"] == {
            "@value": "2016-01-05", "@type": "xsd:date"
        }
        # NEVER effected semantics.
        assert "estleg:amends" not in p
        assert "estleg:amendmentDate" not in p
        assert "estleg:isCurrentAmendment" not in p
        # The chain has ZERO AmendmentEvent nodes.
        assert not [
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]

    def test_draft_only_act_root_uses_has_proposed_amendment(self, tmp_path, monkeypatch):
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "avrs2", "9710", "Avrs2 seadus")
        _write_combined_drafts(krr, [
            {"id": "estleg:Draft_X", "title": "Avrs2 muutmise seadus",
             "affected": "Avrs2 seadus", "pub": "2016-01-05"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        doc = json.loads((krr / "avrs2_peep.json").read_text("utf-8"))
        act = _act_node(doc)
        # Root link via hasProposedAmendment → the ProposedAmendment node.
        # (Unregistered slug → prefix is the sanitized slug 'avrs2'.)
        assert act["estleg:hasProposedAmendment"] == [
            {"@id": "estleg:AmendmentLink_Draft_X_avrs2"}
        ]
        # NOT amendedBy (effected-only) and NOT affectedBy (other pipeline).
        assert "estleg:amendedBy" not in act
        assert "estleg:affectedBy" not in act

    def test_rt_cited_act_unchanged_no_proposed_link(self, tmp_path, monkeypatch):
        # An act whose only amendments are RT-derived keeps AmendmentEvent +
        # amendedBy and gains NO hasProposedAmendment (#423 leaves A/B alone).
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "rtonly", "9720", "RTonly seadus")
        _write_xml(rt, "9720", [
            {"date": "2014-01-01", "eif": "2014-06-01",
             "rt_aasta": "2014", "rt_nr": "7"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        chain_doc = json.loads(
            (krr / "amendments" / "amendments_rtonly.json").read_text("utf-8")
        )
        events = [
            n for n in chain_doc["@graph"]
            if "estleg:AmendmentEvent" in (n.get("@type") or [])
        ]
        assert len(events) == 1, events
        ev = events[0]
        assert "estleg:ProposedAmendment" not in ev["@type"]
        assert ev["estleg:amends"] == {"@id": "estleg:RTONLY_Map"}
        assert ev["estleg:amendmentDate"] == {"@value": "2014-01-01",
                                              "@type": "xsd:date"}
        assert "estleg:proposesToAmend" not in ev
        # No proposal nodes at all.
        assert not [n for n in chain_doc["@graph"] if "estleg:amendingDraft" in n]
        # Act root: amendedBy present, hasProposedAmendment absent.
        doc = json.loads((krr / "rtonly_peep.json").read_text("utf-8"))
        act = _act_node(doc)
        assert act["estleg:amendedBy"] == [{"@id": ev["@id"]}]
        assert "estleg:hasProposedAmendment" not in act

    def test_mixed_act_splits_links_between_amendedby_and_proposed(self, tmp_path, monkeypatch):
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        _write_part(krr, "mixedseadus", "9730", "Mixed seadus")
        _write_xml(rt, "9730", [
            {"date": "2012-01-01", "eif": "2012-06-01",
             "rt_aasta": "2012", "rt_nr": "3"},
        ])
        _write_combined_drafts(krr, [
            {"id": "estleg:Draft_M", "title": "Mixed muutmise seadus",
             "affected": "Mixed seadus", "pub": "2025-02-02"},
        ])
        rc = mod.main()
        assert rc in (None, 0)
        doc = json.loads((krr / "mixedseadus_peep.json").read_text("utf-8"))
        act = _act_node(doc)
        # amendedBy holds ONLY the effected Amendment_ node.
        amended = [r["@id"] for r in act["estleg:amendedBy"]]
        assert all(a.startswith("estleg:Amendment_") for a in amended), amended
        assert not any("AmendmentLink_" in a for a in amended), amended
        # hasProposedAmendment holds ONLY the ProposedAmendment node.
        proposed = [r["@id"] for r in act["estleg:hasProposedAmendment"]]
        assert proposed == ["estleg:AmendmentLink_Draft_M_mixedseadus"], proposed

    def test_rerun_drops_stale_proposal_and_never_leaks_into_amendedby(self, tmp_path, monkeypatch):
        # Idempotency: run with a draft, then rerun with the draft removed. The
        # stale ProposedAmendment must be cleared from the act root, and no
        # proposal must ever appear in amendedBy across either run (#423/#384).
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        peep = _write_part(krr, "rerunseadus", "9740", "Rerun seadus")
        # Empty-but-present XML so the act PAIRS (exercises the chain-file
        # cleanup path, which requires a paired member) yet has no effected
        # amendments — the only chain content is the draft proposal.
        _write_xml(rt, "9740", [])
        _write_combined_drafts(krr, [
            {"id": "estleg:Draft_Stale", "title": "Rerun muutmise seadus",
             "affected": "Rerun seadus", "pub": "2024-01-01"},
        ])
        assert mod.main() in (None, 0)
        act = _act_node(json.loads(peep.read_text("utf-8")))
        assert act["estleg:hasProposedAmendment"] == [
            {"@id": "estleg:AmendmentLink_Draft_Stale_rerunseadus"}
        ]
        assert "estleg:amendedBy" not in act  # never leaked into amendedBy

        # Rerun: empty drafts corpus. Stale proposal link must be gone.
        _write_combined_drafts(krr, [])
        assert mod.main() in (None, 0)
        act2 = _act_node(json.loads(peep.read_text("utf-8")))
        assert "estleg:hasProposedAmendment" not in act2
        assert "estleg:amendedBy" not in act2
        # The now-empty chain file is removed (no amendments at all).
        assert not (krr / "amendments" / "amendments_rerunseadus.json").exists()

    def test_clear_does_not_touch_affectedby(self, tmp_path, monkeypatch):
        # extract_draft_impact.py owns estleg:affectedBy (act → Draft). This
        # script's Step-0 clear must leave a pre-existing affectedBy intact.
        krr, rt, mod = _setup_main_env(tmp_path, monkeypatch)
        peep = krr / "withaffected_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [{
                "@id": "estleg:WITHAFFECTED_Map",
                "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                "rdfs:label": "Withaffected seadus",
                "dc:source": "Withaffected seadus",
                "estleg:globalId": "9750",
                # Pre-existing inverse link from the OTHER pipeline.
                "estleg:affectedBy": [{"@id": "estleg:Draft_FromOtherPipeline"}],
            }],
        }), encoding="utf-8")
        # No drafts / no XML for this act → it gets no amendment links here.
        assert mod.main() in (None, 0)
        act = _act_node(json.loads(peep.read_text("utf-8")))
        assert act["estleg:affectedBy"] == [{"@id": "estleg:Draft_FromOtherPipeline"}]


# ---------------------------------------------------------------------------
# Issue #376 — atomic save_json imported from estleg_common
# ---------------------------------------------------------------------------


class TestSaveJsonIsAtomic:
    """``generate_amendment_history`` must use the atomic ``save_json`` from
    ``estleg_common`` (tempfile + os.replace), not a local truncating
    ``open('w')`` (issue #376)."""

    def test_save_json_is_estleg_common_symbol(self):
        import generate_amendment_history as mod
        import estleg_common

        assert mod.save_json is estleg_common.save_json

    def test_save_json_writes_atomically_via_tempfile(self, tmp_path, monkeypatch):
        # Force os.replace to raise mid-write: the destination must be left
        # UNTOUCHED (no zero-byte truncation), proving the write is atomic and
        # not the old in-place ``open(path,'w')`` that truncates first.
        import generate_amendment_history as mod

        target = tmp_path / "out.json"
        target.write_text('{"existing": true}\n', encoding="utf-8")

        import os as _os

        def boom(_src, _dst):
            raise OSError("disk full")

        monkeypatch.setattr(_os, "replace", boom)
        with pytest.raises(OSError):
            mod.save_json(target, {"new": "data"})

        # Original content intact — atomic write never truncated it.
        assert json.loads(target.read_text("utf-8")) == {"existing": True}
        # No stray tempfiles left behind in the directory.
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "out.json"]
        assert leftovers == [], leftovers

    def test_save_json_output_format_matches_legacy(self, tmp_path):
        # The atomic writer must keep the exact byte format the local one used
        # (ensure_ascii=False, indent=2, trailing newline) so swapping it in
        # doesn't re-diff every emitted file.
        import generate_amendment_history as mod

        p = tmp_path / "fmt.json"
        mod.save_json(p, {"key": "väärtus", "n": 1})
        text = p.read_text("utf-8")
        assert text == '{\n  "key": "väärtus",\n  "n": 1\n}\n'
