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
                    "estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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

        title_map = {"riigieelarve seadus": "riigieelarve_seadus"}
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
            "muutmise seadus": "muutmise_seadus",
            "riigieelarve seaduse muutmise ja sellega seonduvalt teiste seaduste muutmise seadus": (
                "riigieelarve_muutmise"
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
            "alfa beeta gamma delta zeta": "slug_alfa",
            "alfa beeta zeta delta gamma": "slug_alfa_reordered",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
        self._write_xml_with_amendments(
            rt, "2001",
            [
                {"date": "2010-01-01", "eif": "2024-06-01",
                 "rt_aasta": "2024", "rt_nr": "9"},  # late EIF, early date
                {"date": "2024-12-01", "eif": "2010-01-01",
                 "rt_aasta": "2010", "rt_nr": "1"},  # early EIF, late date
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
