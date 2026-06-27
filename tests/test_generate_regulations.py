"""Tests for scripts/generate_regulations.py — regulation JSON-LD builder.

Covers metadata extraction, issuer classification, structured XML parsing,
HTMLKonteiner fallback parsing, ID stability, duplicate-title disambiguation,
the KOV flag, and shared `iter_peep_files` discovery in `riigiteataja_common`.

All tests run offline against fixtures under `tests/fixtures/regulations/`.

Tests at the bottom of the file (TestGidRank, TestAnnexHrefJoin,
TestExistingIsStale, TestDocCacheThreaded, TestGatherRegulationsLimit)
provide regression coverage for issue #174.

A separate `TestMissingPartsCli` class covers issue #175 — per the parent
plan we co-locate `generate_missing_parts` regression tests here when no
dedicated test file exists yet.
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import estleg_common
import generate_regulations
import riigiteataja_common
from generate_regulations import (
    _gid_rank,
    _repealed_before_snapshot,
    build_regulation_index,
    build_regulation_jsonld,
    classify_issuer,
    existing_is_stale,
    gather_regulations,
    provision_summary,
    regulation_file_tid,
    source_removed_files,
    summarize_regulation_doc,
    write_regulation_output,
)
from riigiteataja_common import (
    SourceListFetchError,
    fetch_acts,
    iter_peep_files,
    new_page_stats,
    parse_act_metadata,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "regulations"

HTML_FIXTURE = FIXTURE_DIR / "maarus_html_konteiner.xml"
STRUCTURED_FIXTURE = FIXTURE_DIR / "maarus_structured.xml"
KOV_FIXTURE = FIXTURE_DIR / "maarus_kov.xml"

HTML_TITLE = "Investorile ja Tagatisfondile esitatavate andmete loetelu ja esitamise kord"
STRUCTURED_TITLE = "Volitatud asutuste määramine"
KOV_TITLE = "Põhimäärus"


def _parse(path: Path) -> ET.Element:
    return ET.parse(str(path)).getroot()


def _node_by_id(graph: list[dict], node_id: str) -> dict | None:
    for n in graph:
        if n.get("@id") == node_id:
            return n
    return None


def _provisions(graph: list[dict]) -> list[dict]:
    return [
        n for n in graph
        if "estleg:paragrahv" in n
        and "owl:NamedIndividual" in (n.get("@type") or [])
    ]


# ---------------------------------------------------------------------------
# 1. Metadata extraction (HTML fixture)
# ---------------------------------------------------------------------------

class TestParseActMetadata:
    def test_html_fixture_metadata(self):
        root = _parse(HTML_FIXTURE)
        meta = parse_act_metadata(root)

        assert meta["globalId"] == "191725"
        assert meta["terviktekstId"] == "155482"
        assert meta["documentType"] == "määrus"
        assert meta["issuer"] == "Rahandusminister"
        assert meta["actNumber"] == "104"
        assert meta["entryIntoForce"] == "2002-08-31"


# ---------------------------------------------------------------------------
# 2. Issuer classification
# ---------------------------------------------------------------------------

class TestClassifyIssuer:
    def test_government_regulation(self):
        classes = classify_issuer("Vabariigi Valitsus", is_kov=False)
        assert "estleg:NationalRegulation" in classes
        assert "estleg:GovernmentRegulation" in classes

    def test_minister_regulation(self):
        classes = classify_issuer("Sotsiaalminister", is_kov=False)
        assert "estleg:NationalRegulation" in classes
        assert "estleg:MinisterialRegulation" in classes

    def test_kov_regulation_only_municipal(self):
        classes = classify_issuer("Tartu Linnavolikogu", is_kov=True)
        # Issue #267: a KOV määrus is municipal legislation, NOT national.
        # It must be typed estleg:MunicipalRegulation only and must never
        # also carry estleg:NationalRegulation (a contradictory dual-type).
        assert classes == ["estleg:MunicipalRegulation"]
        assert "estleg:NationalRegulation" not in classes

    def test_kov_regulation_excludes_state_subclasses(self):
        # A municipal regulation is never a Government/Ministerial regulation
        # either, regardless of the recorded issuer string.
        classes = classify_issuer("Tallinna Linnavolikogu", is_kov=True)
        assert "estleg:GovernmentRegulation" not in classes
        assert "estleg:MinisterialRegulation" not in classes
        assert "estleg:NationalRegulation" not in classes
        assert classes == ["estleg:MunicipalRegulation"]

    def test_central_bank_only_national(self):
        # Eesti Pank is central but not a minister and not the government.
        classes = classify_issuer("Eesti Pank", is_kov=False)
        assert classes == ["estleg:NationalRegulation"]

    def test_no_issuer_falls_back_to_national(self):
        classes = classify_issuer(None, is_kov=False)
        assert classes == ["estleg:NationalRegulation"]

    # Issue #606: known issuers keep their EXACT class output; only genuinely
    # unrecognised issuers are tallied in UNCLASSIFIED_ISSUERS for visibility.

    def test_government_output_unchanged_no_counter_increment(self):
        generate_regulations.UNCLASSIFIED_ISSUERS.clear()
        classes = classify_issuer("Vabariigi Valitsus", is_kov=False)
        assert classes == [
            "estleg:NationalRegulation",
            "estleg:GovernmentRegulation",
        ]
        assert sum(generate_regulations.UNCLASSIFIED_ISSUERS.values()) == 0

    def test_minister_output_unchanged_no_counter_increment(self):
        generate_regulations.UNCLASSIFIED_ISSUERS.clear()
        classes = classify_issuer("Sotsiaalminister", is_kov=False)
        assert classes == [
            "estleg:NationalRegulation",
            "estleg:MinisterialRegulation",
        ]
        assert sum(generate_regulations.UNCLASSIFIED_ISSUERS.values()) == 0

    def test_central_bank_known_national_only_no_counter_increment(self):
        # Eesti Pank is a KNOWN National-only issuer — not data drift, so it
        # must NOT be recorded as unclassified.
        generate_regulations.UNCLASSIFIED_ISSUERS.clear()
        classes = classify_issuer("Eesti Pank", is_kov=False)
        assert classes == ["estleg:NationalRegulation"]
        assert "Eesti Pank" not in generate_regulations.UNCLASSIFIED_ISSUERS
        assert sum(generate_regulations.UNCLASSIFIED_ISSUERS.values()) == 0

    def test_unknown_issuer_recorded_as_unclassified(self):
        generate_regulations.UNCLASSIFIED_ISSUERS.clear()
        classes = classify_issuer("Mingi Tundmatu Amet", is_kov=False)
        # Output is unchanged — still bare National (no regression).
        assert classes == ["estleg:NationalRegulation"]
        # ...but the unrecognised issuer is now surfaced for the pipeline.
        assert generate_regulations.UNCLASSIFIED_ISSUERS["Mingi Tundmatu Amet"] == 1

    def test_kov_short_circuit_not_recorded_as_unclassified(self):
        generate_regulations.UNCLASSIFIED_ISSUERS.clear()
        classes = classify_issuer("Mingi Tundmatu Amet", is_kov=True)
        # KOV short-circuit is unchanged and runs before issuer classification,
        # so no unclassified bookkeeping happens.
        assert classes == ["estleg:MunicipalRegulation"]
        assert sum(generate_regulations.UNCLASSIFIED_ISSUERS.values()) == 0


# ---------------------------------------------------------------------------
# 3. Structured XML parsing
# ---------------------------------------------------------------------------

class TestStructuredParsing:
    def test_graph_node_count(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, stats = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        # Fixture has 2 paragraphs => N+2 = 4 nodes (1 ontology + 1 class + 2 provisions)
        assert stats["paragraphs"] == 2
        assert stats["annexes"] == 0
        assert len(doc["@graph"]) == 4

    def test_provision_node_shape(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        provisions = _provisions(doc["@graph"])
        assert len(provisions) == 2

        for prov in provisions:
            assert prov["estleg:paragrahv"].startswith("§")
            label = prov["rdfs:label"]
            assert isinstance(label, str)
            assert label

            type_list = prov["@type"]
            assert "owl:NamedIndividual" in type_list
            assert "estleg:Regulation_160748" in type_list

    def test_provision_summary_falls_back_to_label_or_source_title(self):
        assert provision_summary("§ 1.", "§ 1. Reguleerimisala", "Testmäärus", "") == "§ 1. Reguleerimisala"
        assert provision_summary("§ 1.", "§ 1.", "Testmäärus", "") == "Testmäärus § 1."

    def test_ontology_classes_for_government(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        ontology = doc["@graph"][0]
        type_list = ontology["@type"]
        assert "owl:Ontology" in type_list
        assert "estleg:NationalRegulation" in type_list
        assert "estleg:GovernmentRegulation" in type_list

    def test_preamble_populated_from_preambul(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        ontology = doc["@graph"][0]
        preamble = ontology.get("estleg:preambleText")
        assert preamble is not None
        assert isinstance(preamble, str)
        # Content from `<preambul>` should mention the legal basis.
        assert "Määrus kehtestatakse" in preamble
        assert "§ 12 alusel" in preamble

    def test_no_body_regulation_is_modeled_as_stub(self):
        root = ET.fromstring(
            """
            <akt>
              <metaandmed>
                <aktinimi>Menetlustoimingu määrus</aktinimi>
                <terviktekstID>999001</terviktekstID>
                <globaalID>999002</globaalID>
                <aktinumber>1</aktinumber>
              </metaandmed>
            </akt>
            """
        )

        doc, stats = build_regulation_jsonld(
            "Menetlustoimingu määrus",
            {"tid": "999001", "gid": "999002"},
            root,
            is_kov=False,
        )

        ontology = doc["@graph"][0]
        assert stats["no_paragraphs"] == 1
        assert ontology["estleg:contentStatus"] == "noStructuredBody"
        assert ontology["dcterms:title"] == "Menetlustoimingu määrus"


# ---------------------------------------------------------------------------
# 4. HTMLKonteiner fallback parsing
# ---------------------------------------------------------------------------

class TestHtmlFallbackParsing:
    def test_provisions_extracted_from_html(self):
        root = _parse(HTML_FIXTURE)
        doc, stats = build_regulation_jsonld(HTML_TITLE, {}, root, is_kov=False)

        # Must use the HTML fallback path, not the structured path.
        assert stats["html_fallback"] == 1
        provisions = _provisions(doc["@graph"])
        assert len(provisions) >= 2

    def test_provision_iri_pattern(self):
        root = _parse(HTML_FIXTURE)
        doc, _ = build_regulation_jsonld(HTML_TITLE, {}, root, is_kov=False)

        provision_ids = {n["@id"] for n in _provisions(doc["@graph"])}
        # IRIs must follow estleg:Reg_<terviktekstId>_Par_<n>
        assert "estleg:Reg_155482_Par_1" in provision_ids
        assert "estleg:Reg_155482_Par_2" in provision_ids

    def test_preamble_text_contains_legal_basis(self):
        root = _parse(HTML_FIXTURE)
        doc, _ = build_regulation_jsonld(HTML_TITLE, {}, root, is_kov=False)

        ontology = doc["@graph"][0]
        preamble = ontology.get("estleg:preambleText")
        assert preamble is not None
        assert isinstance(preamble, str)
        # The preamble is the first <p> of the HTML body — it cites the parent law.
        assert "Tagatisfondi" in preamble
        assert "§ 57" in preamble

    def test_provision_legalText_contains_body_phrase(self):
        root = _parse(HTML_FIXTURE)
        doc, _ = build_regulation_jsonld(HTML_TITLE, {}, root, is_kov=False)

        par1 = _node_by_id(doc["@graph"], "estleg:Reg_155482_Par_1")
        assert par1 is not None
        legal_text = par1.get("estleg:legalText")
        assert legal_text is not None
        assert isinstance(legal_text, str)
        # A specific phrase from § 1's body in the fixture.
        assert "Investeerimisasutuse juhatuse liige" in legal_text


# ---------------------------------------------------------------------------
# 5. ID stability across rebuilds
# ---------------------------------------------------------------------------

class TestIdStability:
    @pytest.mark.parametrize(
        "fixture,title,is_kov",
        [
            (STRUCTURED_FIXTURE, STRUCTURED_TITLE, False),
            (HTML_FIXTURE, HTML_TITLE, False),
            (KOV_FIXTURE, KOV_TITLE, True),
        ],
    )
    def test_repeat_build_yields_identical_ids(self, fixture, title, is_kov):
        # Re-parse for each call so we exercise the full pipeline twice.
        root1 = _parse(fixture)
        root2 = _parse(fixture)

        doc1, _ = build_regulation_jsonld(title, {}, root1, is_kov=is_kov)
        doc2, _ = build_regulation_jsonld(title, {}, root2, is_kov=is_kov)

        ids1 = [n.get("@id") for n in doc1["@graph"]]
        ids2 = [n.get("@id") for n in doc2["@graph"]]
        assert ids1 == ids2


# ---------------------------------------------------------------------------
# 6. Duplicate-title handling — different terviktekstId must not collide
# ---------------------------------------------------------------------------

class TestDuplicateTitleHandling:
    def test_different_tids_produce_disjoint_iris(self):
        root_struct = _parse(STRUCTURED_FIXTURE)
        root_kov = _parse(KOV_FIXTURE)

        doc_struct, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root_struct, is_kov=False)
        doc_kov, _ = build_regulation_jsonld(KOV_TITLE, {}, root_kov, is_kov=True)

        ontology_struct = doc_struct["@graph"][0]
        ontology_kov = doc_kov["@graph"][0]

        # Ontology @ids must differ — they are namespaced by terviktekstId.
        assert ontology_struct["@id"] != ontology_kov["@id"]

        # Provision IRIs from the two docs must not intersect.
        prov_struct_ids = {n["@id"] for n in _provisions(doc_struct["@graph"])}
        prov_kov_ids = {n["@id"] for n in _provisions(doc_kov["@graph"])}
        assert prov_struct_ids
        assert prov_kov_ids
        assert prov_struct_ids.isdisjoint(prov_kov_ids)


# ---------------------------------------------------------------------------
# 7. isKov flag drives @type and the boolean literal
# ---------------------------------------------------------------------------

class TestIsKovFlag:
    def test_kov_true_marks_municipal(self):
        root = _parse(KOV_FIXTURE)
        doc, _ = build_regulation_jsonld(KOV_TITLE, {}, root, is_kov=True)

        ontology = doc["@graph"][0]
        is_kov = ontology["estleg:isKov"]
        assert is_kov == {"@value": "true", "@type": "xsd:boolean"}

        type_list = ontology["@type"]
        # Issue #267: KOV act nodes are municipal regulations only — never
        # dual-typed as national/government/ministerial regulations.
        assert "estleg:MunicipalRegulation" in type_list
        assert "estleg:NationalRegulation" not in type_list
        assert "estleg:GovernmentRegulation" not in type_list
        assert "estleg:MinisterialRegulation" not in type_list
        # The act still carries the base act/ontology classes so the
        # estleg:Act SHACL shape and MunicipalRegulationShape both apply.
        assert "owl:Ontology" in type_list
        assert "estleg:Act" in type_list

    def test_kov_false_marks_national(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        ontology = doc["@graph"][0]
        is_kov = ontology["estleg:isKov"]
        assert is_kov == {"@value": "false", "@type": "xsd:boolean"}

        type_list = ontology["@type"]
        assert "estleg:NationalRegulation" in type_list
        assert "estleg:MunicipalRegulation" not in type_list


class TestKovTypeContradiction:
    """Issue #267 regression: KOV act nodes must NOT be dual-typed as both
    estleg:MunicipalRegulation AND estleg:NationalRegulation. A municipal
    regulation is not national legislation."""

    def test_kov_act_type_excludes_national(self):
        root = _parse(KOV_FIXTURE)
        doc, _ = build_regulation_jsonld(KOV_TITLE, {}, root, is_kov=True)
        ontology = doc["@graph"][0]
        type_list = ontology["@type"]

        # The whole point of the fix: no state-regulation types on a KOV act.
        for state_type in (
            "estleg:NationalRegulation",
            "estleg:GovernmentRegulation",
            "estleg:MinisterialRegulation",
        ):
            assert state_type not in type_list, (
                f"KOV act must not carry {state_type}"
            )
        assert "estleg:MunicipalRegulation" in type_list

    def test_state_act_still_typed_national(self):
        # The counterpart must be unaffected: a state regulation still
        # carries NationalRegulation (and never MunicipalRegulation).
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)
        type_list = doc["@graph"][0]["@type"]
        assert "estleg:NationalRegulation" in type_list
        assert "estleg:MunicipalRegulation" not in type_list

    def test_kov_act_satisfies_municipal_shape_requirements(self):
        # MunicipalRegulationShape requires rdfs:label; the act still carries
        # estleg:Act (ActTemporalShape) — confirm both anchors survive so
        # SHACL stays satisfied after dropping NationalRegulation.
        root = _parse(KOV_FIXTURE)
        doc, _ = build_regulation_jsonld(KOV_TITLE, {}, root, is_kov=True)
        ontology = doc["@graph"][0]
        assert "estleg:Act" in ontology["@type"]
        assert "estleg:MunicipalRegulation" in ontology["@type"]
        assert isinstance(ontology.get("rdfs:label"), str)
        assert ontology["rdfs:label"]


# ---------------------------------------------------------------------------
# 8. iter_peep_files discovery (riigiteataja_common)
# ---------------------------------------------------------------------------

class TestIterPeepFiles:
    def _setup_tmp_layout(self, tmp_path: Path) -> dict[str, Path]:
        """Lay out a tmp KRR_DIR with one law, one regulation, one KOV file."""
        law_file = tmp_path / "some_law_peep.json"
        law_file.write_text("{}", encoding="utf-8")

        riik_dir = tmp_path / "regulations" / "riik"
        riik_dir.mkdir(parents=True)
        reg_file = riik_dir / "some_regulation_t111_peep.json"
        reg_file.write_text("{}", encoding="utf-8")

        kov_dir = tmp_path / "regulations" / "kov" / "tartu_linnavolikogu"
        kov_dir.mkdir(parents=True)
        kov_file = kov_dir / "pohimaarus_t999001_peep.json"
        kov_file.write_text("{}", encoding="utf-8")

        return {"law": law_file, "regulation": reg_file, "kov": kov_file}

    def test_finds_regulation_when_included(self, tmp_path, monkeypatch):
        files = self._setup_tmp_layout(tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_path)

        found = iter_peep_files(
            include_laws=True, include_regulations=True, include_kov=False
        )
        assert files["law"] in found
        assert files["regulation"] in found
        # Default kov=False — the KOV file must NOT appear.
        assert files["kov"] not in found

    def test_excludes_regulation_when_not_included(self, tmp_path, monkeypatch):
        files = self._setup_tmp_layout(tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_path)

        found = iter_peep_files(
            include_laws=True, include_regulations=False, include_kov=False
        )
        assert files["law"] in found
        assert files["regulation"] not in found
        assert files["kov"] not in found

    def test_kov_opt_in(self, tmp_path, monkeypatch):
        files = self._setup_tmp_layout(tmp_path)
        monkeypatch.setattr(estleg_common, "KRR_DIR", tmp_path)

        found = iter_peep_files(
            include_laws=False, include_regulations=True, include_kov=True
        )
        assert files["kov"] in found
        assert files["regulation"] in found
        assert files["law"] not in found


class TestRegulationIndex:
    def test_build_index_counts_existing_files(self, tmp_path):
        out_dir = tmp_path / "riik"
        out_dir.mkdir()
        for idx in range(2):
            doc = {
                "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
                "@graph": [
                    {
                        "@id": f"estleg:Reg_{idx}_Map_2026",
                        "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                        "estleg:issuer": "Vabariigi Valitsus",
                    },
                    {
                        "@id": f"estleg:Reg_{idx}_Par_1",
                        "@type": ["owl:NamedIndividual", f"estleg:Regulation_{idx}"],
                        "estleg:paragrahv": "§ 1.",
                    },
                ],
            }
            (out_dir / f"reg_{idx}_peep.json").write_text(
                json.dumps(doc),
                encoding="utf-8",
            )

        index = build_regulation_index(
            out_dir,
            is_kov=False,
            kehtiv="2026-05-01",
            run_stats={"rebuiltFromExistingFiles": True},
        )

        assert index["totalRegulations"] == 2
        assert index["totalParagraphs"] == 2
        assert index["run"]["rebuiltFromExistingFiles"] is True

    def test_refresh_rewrites_changed_existing_output(self, tmp_path):
        out_path = tmp_path / "reg_peep.json"
        out_path.write_text(
            json.dumps({"@graph": [{"@id": "estleg:Old"}]}),
            encoding="utf-8",
        )
        new_doc = {"@graph": [{"@id": "estleg:New"}]}

        status = write_regulation_output(out_path, new_doc, mode="refresh")

        assert status == "refreshed"
        assert json.loads(out_path.read_text(encoding="utf-8")) == new_doc

    def test_refresh_leaves_unchanged_existing_output(self, tmp_path):
        out_path = tmp_path / "reg_peep.json"
        doc = {"@graph": [{"@id": "estleg:Same"}]}
        out_path.write_text(json.dumps(doc), encoding="utf-8")

        status = write_regulation_output(out_path, doc, mode="refresh")

        assert status == "unchanged"
        assert json.loads(out_path.read_text(encoding="utf-8")) == doc

    def test_missing_only_skips_existing_output(self, tmp_path):
        out_path = tmp_path / "reg_peep.json"
        old_doc = {"@graph": [{"@id": "estleg:Old"}]}
        out_path.write_text(json.dumps(old_doc), encoding="utf-8")

        status = write_regulation_output(
            out_path,
            {"@graph": [{"@id": "estleg:New"}]},
            mode="missing-only",
        )

        assert status == "existingSkipped"
        assert json.loads(out_path.read_text(encoding="utf-8")) == old_doc

    def test_source_removed_files_reports_existing_outputs_missing_from_snapshot(self, tmp_path):
        out_dir = tmp_path / "riik"
        out_dir.mkdir()
        current = {
            "@graph": [
                {
                    "@id": "estleg:Reg_1_Map_2026",
                    "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                    "estleg:terviktekstId": "1",
                }
            ]
        }
        removed = {
            "@graph": [
                {
                    "@id": "estleg:Reg_2_Map_2026",
                    "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                    "estleg:terviktekstId": "2",
                }
            ]
        }
        (out_dir / "current_t1_peep.json").write_text(json.dumps(current), encoding="utf-8")
        (out_dir / "removed_t2_peep.json").write_text(json.dumps(removed), encoding="utf-8")

        assert source_removed_files(out_dir, {"1"}) == ["removed_t2_peep.json"]

    def test_index_acts_ledger_marks_full_and_stub(self, tmp_path):
        """Issue #119: the regulation index now carries a per-act `acts`
        ledger with status (full/stub) and the act node's contentStatus
        marker for stubs."""
        out_dir = tmp_path / "riik"
        out_dir.mkdir()
        # Full regulation: has a paragraph node, no contentStatus marker.
        (out_dir / "full_reg_t100_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [
                {"@id": "estleg:Reg_100_Map_2026",
                 "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                 "estleg:issuer": "Vabariigi Valitsus"},
                {"@id": "estleg:Reg_100_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:Regulation_100"],
                 "estleg:paragrahv": "§ 1."},
            ],
        }), encoding="utf-8")
        # No-body regulation: contentStatus = noStructuredBody.
        (out_dir / "stub_reg_t200_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [
                {"@id": "estleg:Reg_200_Map_2026",
                 "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                 "estleg:issuer": "Sotsiaalminister",
                 "estleg:contentStatus": "noStructuredBody",
                 "estleg:contentStatusReason": "no structured body"},
            ],
        }), encoding="utf-8")

        index = build_regulation_index(out_dir, is_kov=False, kehtiv="2026-05-01")

        acts = {a["slug"]: a for a in index["acts"]}
        assert acts["full_reg"]["status"] == "full"
        assert "contentStatus" not in acts["full_reg"]
        assert acts["stub_reg"]["status"] == "stub"
        assert acts["stub_reg"]["contentStatus"] == "noStructuredBody"
        assert "reason" in acts["stub_reg"]
        # byStatus aggregate is present and consistent.
        assert index["byStatus"] == {"full": 1, "stub": 1}
        assert index["noStructuredBodyCount"] == 1
        # The acts ledger covers exactly the files list.
        assert {a["file"] for a in index["acts"]} == set(index["files"])

    def test_no_body_regulation_build_sets_status_via_summarize(self, tmp_path):
        """Round-trip: a no-body regulation built via build_regulation_jsonld
        is summarised as a stub."""
        root = ET.fromstring(
            "<akt><metaandmed><terviktekstID>900001</terviktekstID>"
            "<globaalID>900002</globaalID></metaandmed></akt>"
        )
        doc, _stats = build_regulation_jsonld(
            "Menetlusmäärus", {"tid": "900001", "gid": "900002"}, root, is_kov=False,
        )
        summary = generate_regulations.summarize_regulation_doc(doc)
        assert summary["status"] == "stub"
        assert summary["contentStatus"] == "noStructuredBody"


# ---------------------------------------------------------------------------
# Issue #374 — repealed-before-snapshot tombstoning
# ---------------------------------------------------------------------------


def _reg_xml(tid: str, gid: str, *, repeal: str | None, issuer: str = "Vabariigi Valitsus") -> ET.Element:
    """One structured määrus with a single §1 body, optional kehtivuseLopp.

    ``repeal`` (when given) is stamped as ``<kehtivuseLopp>`` so the builder
    can decide whether the act was already repealed before the snapshot.
    """
    lopp = f"<kehtivuseLopp>{repeal}</kehtivuseLopp>" if repeal else ""
    return ET.fromstring(
        "<akt><metaandmed>"
        f"<terviktekstiGrupiID>{tid}</terviktekstiGrupiID>"
        f"<globaalID>{gid}</globaalID>"
        f"<valjaandja>{issuer}</valjaandja>"
        "<kehtivus><kehtivuseAlgus>2018-01-01</kehtivuseAlgus>"
        f"{lopp}</kehtivus>"
        "</metaandmed><sisu><paragrahv>"
        "<paragrahvNr>1</paragrahvNr>"
        "<paragrahvPealkiri>Reguleerimisala</paragrahvPealkiri>"
        "<tavatekst>Käesolev määrus reguleerib midagi olulist ja sisukat.</tavatekst>"
        "</paragrahv></sisu></akt>"
    )


class TestRepealedBeforeSnapshotPredicate:
    """Unit coverage for the date guard itself (issue #374)."""

    def test_repealed_strictly_before_snapshot(self):
        assert _repealed_before_snapshot("2025-10-24", "2026-05-01") is True

    def test_repealed_on_snapshot_date_is_still_active(self):
        # An act repealed *on* the snapshot day is in force that day -> kept.
        assert _repealed_before_snapshot("2026-05-01", "2026-05-01") is False

    def test_repealed_after_snapshot_is_active(self):
        assert _repealed_before_snapshot("2026-06-01", "2026-05-01") is False

    def test_missing_repeal_date_is_active(self):
        assert _repealed_before_snapshot(None, "2026-05-01") is False
        assert _repealed_before_snapshot("", "2026-05-01") is False

    def test_unstripped_offset_or_time_is_compared_on_date_prefix(self):
        # Defensive: a TZ offset / time component must not flip the compare.
        assert _repealed_before_snapshot("2025-10-24+03:00", "2026-05-01") is True
        assert _repealed_before_snapshot("2026-05-01T12:00:00", "2026-05-01") is False


class TestRepealedBeforeSnapshotGenerator:
    """build_regulation_jsonld tombstones acts repealed before the snapshot."""

    def test_repealed_before_snapshot_is_tombstoned_without_body(self):
        root = _reg_xml("900", "901", repeal="2025-10-24")
        doc, stats = build_regulation_jsonld(
            "Muhu valla arengukava 2035", {}, root, is_kov=True, kehtiv="2026-05-01"
        )
        # No provision content is emitted for void law text.
        assert _provisions(doc["@graph"]) == []
        ont = _node_by_id(doc["@graph"], "estleg:Reg_900_Map_2026")
        assert ont is not None
        assert ont["estleg:temporalStatus"] == "repealed"
        assert ont["estleg:contentStatus"] == "repealedBeforeSnapshot"
        assert ont["estleg:parseMode"] == "repealedBeforeSnapshot"
        # The repeal date is still recorded on the tombstone.
        assert ont["estleg:repealDate"] == {"@value": "2025-10-24", "@type": "xsd:date"}
        assert stats["repealed_before_snapshot"] == 1
        assert stats["paragraphs"] == 0

    def test_active_regulation_is_unchanged(self):
        # Backward compatibility: an act with no repeal date keeps its body.
        root = _reg_xml("800", "801", repeal=None)
        doc, stats = build_regulation_jsonld(
            "Aktiivne määrus", {}, root, is_kov=False, kehtiv="2026-05-01"
        )
        assert len(_provisions(doc["@graph"])) == 1
        ont = _node_by_id(doc["@graph"], "estleg:Reg_800_Map_2026")
        assert "estleg:temporalStatus" not in ont
        assert ont["estleg:parseMode"] == "structured"
        assert stats["repealed_before_snapshot"] == 0

    def test_repealed_on_snapshot_date_keeps_body(self):
        # An act repealed *on* the snapshot date is still in force that day.
        root = _reg_xml("810", "811", repeal="2026-05-01")
        doc, _stats = build_regulation_jsonld(
            "Lõppev määrus", {}, root, is_kov=False, kehtiv="2026-05-01"
        )
        assert len(_provisions(doc["@graph"])) == 1
        ont = _node_by_id(doc["@graph"], "estleg:Reg_810_Map_2026")
        assert "estleg:temporalStatus" not in ont

    def test_summarize_classifies_repealed_status(self):
        root = _reg_xml("820", "821", repeal="2025-01-01")
        doc, _stats = build_regulation_jsonld(
            "Vana määrus", {}, root, is_kov=True, kehtiv="2026-05-01"
        )
        summary = summarize_regulation_doc(doc)
        assert summary["status"] == "repealed"
        assert summary["contentStatus"] == "repealedBeforeSnapshot"


class TestRepealedBeforeSnapshotIndex:
    """The regulation index excludes tombstones from the active count but
    keeps the filesystem-parity ``totalRegulations`` invariant intact."""

    def test_active_count_excludes_repealed_while_total_matches_files(self, tmp_path):
        out_dir = tmp_path / "kov"
        out_dir.mkdir()
        # One active reg with a body...
        active_root = _reg_xml("700", "701", repeal=None)
        active_doc, _ = build_regulation_jsonld(
            "Aktiivne määrus", {}, active_root, is_kov=True, kehtiv="2026-05-01"
        )
        (out_dir / "active_t700_peep.json").write_text(
            json.dumps(active_doc), encoding="utf-8"
        )
        # ...and one tombstoned reg (repealed before the snapshot).
        repealed_root = _reg_xml("701", "702", repeal="2025-10-24")
        repealed_doc, _ = build_regulation_jsonld(
            "Muhu valla arengukava 2035", {}, repealed_root, is_kov=True, kehtiv="2026-05-01"
        )
        (out_dir / "repealed_t701_peep.json").write_text(
            json.dumps(repealed_doc), encoding="utf-8"
        )

        index = build_regulation_index(out_dir, is_kov=True, kehtiv="2026-05-01")

        # totalRegulations stays == file count (validate_all parity invariant).
        assert index["totalRegulations"] == 2
        assert len(index["files"]) == 2
        # Active count excludes the tombstone.
        assert index["activeRegulations"] == 1
        assert index["repealedBeforeSnapshotCount"] == 1
        assert index["byStatus"] == {"full": 1, "repealed": 1}
        # The repealed act carries a status + reason in the ledger.
        repealed_entry = next(
            a for a in index["acts"] if a["file"] == "repealed_t701_peep.json"
        )
        assert repealed_entry["status"] == "repealed"
        assert "issue #374" in repealed_entry["reason"]
        # No void provision text was indexed for the tombstone.
        assert index["totalParagraphs"] == 1


# ---------------------------------------------------------------------------
# Issue #174 regression coverage
# ---------------------------------------------------------------------------


class TestGidRank:
    """Regression coverage for the globalID dedup string-compare bug
    (issue #174 #1)."""

    def test_numeric_value_beats_lexicographic_99(self):
        # The bug: lexicographic compare ranks "99..." above "128..."
        # because '9' > '1'. The fix casts to int.
        old = "99052024005"
        new = "128062023003"
        assert _gid_rank(new) > _gid_rank(old)

    def test_numeric_beats_non_numeric(self):
        # A numeric ID always wins against a malformed one.
        assert _gid_rank("100") > _gid_rank("not-numeric")

    def test_two_non_numeric_compared_lexicographically(self):
        assert _gid_rank("zzz") > _gid_rank("aaa")

    def test_none_or_empty_safe(self):
        assert _gid_rank("") < _gid_rank("100")
        assert _gid_rank(None) < _gid_rank("0")  # type: ignore[arg-type]


class TestAnnexHrefJoin:
    """Regression coverage for annex href base_url join (issue #174 #2)."""

    def _build_with_annex_href(self, href: str) -> dict:
        # Build a tiny fixture that has one annex with the supplied
        # href, parse it, and return the produced ontology graph.
        xml = f"""<akt>
          <metaandmed>
            <terviktekstiGrupiID>700001</terviktekstiGrupiID>
            <globaalID>700002</globaalID>
          </metaandmed>
          <lisa>
            <lisaNr>1</lisaNr>
            <lisaPealkiri>Test annex</lisaPealkiri>
            <viideURI>{href}</viideURI>
          </lisa>
        </akt>"""
        root = ET.fromstring(xml)
        doc, _stats = build_regulation_jsonld(
            "Annex test reg", {}, root, is_kov=False
        )
        return doc

    def test_relative_dot_slash(self):
        doc = self._build_with_annex_href("./akt/123/lisa.pdf")
        annex = next(
            n for n in doc["@graph"]
            if "estleg:Annex" in (n.get("@type") or [])
        )
        # Result should be a clean absolute URL — no double slashes,
        # no trailing dotting.
        url = annex["dcterms:source"]["@id"]
        assert url == "https://www.riigiteataja.ee/akt/123/lisa.pdf", url

    def test_relative_dot_dot(self):
        # `../akt/...` used to produce
        # `https://www.riigiteataja.ee//akt/...` under the old lstrip(".")
        doc = self._build_with_annex_href("../akt/123/lisa.pdf")
        annex = next(
            n for n in doc["@graph"]
            if "estleg:Annex" in (n.get("@type") or [])
        )
        url = annex["dcterms:source"]["@id"]
        # urljoin canonicalises '..' against the base; we just
        # assert no double-slash glitch and no dot-prefixed glitch.
        assert "//akt" not in url.replace("https://", "").replace("http://", ""), url
        assert url.startswith("https://www.riigiteataja.ee/")

    def test_bare_relative(self):
        # Bare-relative `lisa/foo.pdf` used to produce
        # `https://www.riigiteataja.eelisa/foo.pdf` (no slash).
        doc = self._build_with_annex_href("lisa/foo.pdf")
        annex = next(
            n for n in doc["@graph"]
            if "estleg:Annex" in (n.get("@type") or [])
        )
        url = annex["dcterms:source"]["@id"]
        assert url == "https://www.riigiteataja.ee/lisa/foo.pdf", url

    def test_absolute_url_unchanged(self):
        doc = self._build_with_annex_href("https://example.org/lisa.pdf")
        annex = next(
            n for n in doc["@graph"]
            if "estleg:Annex" in (n.get("@type") or [])
        )
        assert annex["dcterms:source"]["@id"] == "https://example.org/lisa.pdf"


class TestExistingIsStale:
    """Regression coverage for missing-only mode keeps stale (issue #174 #3)."""

    def _write_stub(self, path: Path, *, tid: str, kehtiv: str | None) -> None:
        ontology = {
            "@id": f"estleg:Reg_{tid}_Map_2026",
            "@type": ["owl:Ontology", "estleg:NationalRegulation"],
            "estleg:terviktekstId": tid,
        }
        if kehtiv:
            ontology["estleg:kehtiv"] = {"@value": kehtiv, "@type": "xsd:date"}
        path.write_text(
            json.dumps({"@graph": [ontology]}),
            encoding="utf-8",
        )

    def test_fresh_file_not_stale(self, tmp_path):
        p = tmp_path / "x_peep.json"
        self._write_stub(p, tid="100", kehtiv="2026-05-01")
        assert existing_is_stale(
            p, expected_tid="100", expected_kehtiv="2026-05-01"
        ) is False

    def test_kehtiv_drift_marks_stale(self, tmp_path):
        p = tmp_path / "x_peep.json"
        self._write_stub(p, tid="100", kehtiv="2024-01-01")
        assert existing_is_stale(
            p, expected_tid="100", expected_kehtiv="2026-05-01"
        ) is True

    def test_missing_kehtiv_marks_stale(self, tmp_path):
        # Old files without estleg:kehtiv stamping must be considered
        # stale so missing-only mode refreshes them once.
        p = tmp_path / "x_peep.json"
        self._write_stub(p, tid="100", kehtiv=None)
        assert existing_is_stale(
            p, expected_tid="100", expected_kehtiv="2026-05-01"
        ) is True

    def test_tid_drift_marks_stale(self, tmp_path):
        p = tmp_path / "x_peep.json"
        self._write_stub(p, tid="100", kehtiv="2026-05-01")
        # Caller asks about a different terviktekstId (snapshot
        # changed underneath this filename).
        assert existing_is_stale(
            p, expected_tid="200", expected_kehtiv="2026-05-01"
        ) is True

    def test_kehtiv_stamped_on_new_build(self):
        xml = """<akt><metaandmed>
                <terviktekstiGrupiID>910001</terviktekstiGrupiID>
                <globaalID>910002</globaalID>
        </metaandmed></akt>"""
        root = ET.fromstring(xml)
        doc, _ = build_regulation_jsonld(
            "X reg", {}, root, is_kov=False, kehtiv="2026-05-01"
        )
        ontology = doc["@graph"][0]
        assert ontology["estleg:kehtiv"] == {
            "@value": "2026-05-01",
            "@type": "xsd:date",
        }

    def test_write_regulation_output_refreshes_stale_in_missing_only(
        self, tmp_path
    ):
        out_path = tmp_path / "reg_peep.json"
        out_path.write_text(
            json.dumps({
                "@graph": [
                    {
                        "@id": "estleg:Reg_100_Map_2026",
                        "@type": ["owl:Ontology"],
                        "estleg:terviktekstId": "100",
                        "estleg:kehtiv": {
                            "@value": "2024-01-01", "@type": "xsd:date"
                        },
                    }
                ]
            }),
            encoding="utf-8",
        )
        new_doc = {
            "@graph": [
                {
                    "@id": "estleg:Reg_100_Map_2026",
                    "@type": ["owl:Ontology"],
                    "estleg:terviktekstId": "100",
                    "estleg:kehtiv": {
                        "@value": "2026-05-01", "@type": "xsd:date"
                    },
                }
            ]
        }
        status = write_regulation_output(
            out_path,
            new_doc,
            mode="missing-only",
            expected_kehtiv="2026-05-01",
        )
        assert status == "refreshedStale"
        assert json.loads(out_path.read_text(encoding="utf-8")) == new_doc

    def test_write_regulation_output_keeps_fresh_in_missing_only(
        self, tmp_path
    ):
        out_path = tmp_path / "reg_peep.json"
        fresh_doc = {
            "@graph": [
                {
                    "@id": "estleg:Reg_100_Map_2026",
                    "@type": ["owl:Ontology"],
                    "estleg:terviktekstId": "100",
                    "estleg:kehtiv": {
                        "@value": "2026-05-01", "@type": "xsd:date"
                    },
                }
            ]
        }
        out_path.write_text(json.dumps(fresh_doc), encoding="utf-8")
        status = write_regulation_output(
            out_path,
            fresh_doc,
            mode="missing-only",
            expected_kehtiv="2026-05-01",
        )
        # File on disk is fresh — leave it.
        assert status == "existingSkipped"


class TestDocCacheThreaded:
    """Regression coverage for index re-parses files (issue #174 #4)."""

    def test_build_index_uses_cache_when_provided(self, tmp_path):
        out_dir = tmp_path / "riik"
        out_dir.mkdir()
        path = out_dir / "reg_peep.json"
        # Write a file with NO ontology type — disk read would yield
        # zero counts. Cache path supplies the canonical doc instead.
        path.write_text("{}", encoding="utf-8")

        cached_doc = {
            "@graph": [
                {
                    "@id": "estleg:Reg_X_Map_2026",
                    "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                    "estleg:issuer": "Vabariigi Valitsus",
                },
                {
                    "@id": "estleg:Reg_X_Par_1",
                    "@type": ["owl:NamedIndividual", "estleg:Regulation_X"],
                    "estleg:paragrahv": "§ 1.",
                },
            ]
        }
        index = build_regulation_index(
            out_dir,
            is_kov=False,
            kehtiv="2026-05-01",
            doc_cache={path: cached_doc},
        )
        # Cached doc has 1 paragraph; if cache wasn't honoured, the
        # disk read would have produced zero.
        assert index["totalRegulations"] == 1
        assert index["totalParagraphs"] == 1

    def test_regulation_file_tid_uses_cache(self, tmp_path):
        path = tmp_path / "reg_peep.json"
        # Disk file is empty; cache has the real doc.
        path.write_text("{}", encoding="utf-8")
        cached = {
            "@graph": [
                {
                    "@id": "estleg:Reg_X_Map_2026",
                    "@type": ["owl:Ontology"],
                    "estleg:terviktekstId": "1234",
                }
            ]
        }
        assert regulation_file_tid(path, doc_cache={path: cached}) == "1234"

    def test_source_removed_files_uses_cache(self, tmp_path):
        out_dir = tmp_path / "riik"
        out_dir.mkdir()
        path = out_dir / "reg_peep.json"
        path.write_text("{}", encoding="utf-8")
        cached = {
            "@graph": [
                {
                    "@id": "estleg:Reg_X_Map_2026",
                    "@type": ["owl:Ontology"],
                    "estleg:terviktekstId": "abc",
                }
            ]
        }
        # tid 'abc' is NOT in current_tids -> reported as removed.
        result = source_removed_files(
            out_dir, {"xyz"}, doc_cache={path: cached}
        )
        assert result == ["reg_peep.json"]


class TestGatherRegulationsLimit:
    """Regression coverage for limit fetched too many rows (issue #174 #5).

    We avoid the network by stubbing ``fetch_acts`` and asserting the
    ``limiit`` keyword the gather function passed downstream.
    """

    def test_limit_caps_page_size(self, monkeypatch):
        import generate_regulations as mod

        captured: dict = {}

        def fake_fetch_acts(**kwargs):
            captured.update(kwargs)
            # Yield enough rows to hit the limit.
            for i in range(3):
                yield {
                    "terviktekstID": f"t{i}",
                    "globaalID": f"{1000 + i}",
                    "url": f"/akt/{i}",
                    "pealkiri": f"Reg {i}",
                    "valjaandja": "Vabariigi Valitsus",
                }

        monkeypatch.setattr(mod, "fetch_acts", fake_fetch_acts)

        regs, manifest = mod.gather_regulations(
            kov=False, kehtiv="2026-05-01", limit=5
        )
        # page_size = min(500, max(50, 5*2)) = 50
        assert captured["limiit"] == 50
        assert manifest["limited"] is True
        assert len(regs) == 3

    def test_no_limit_uses_full_page_size(self, monkeypatch):
        import generate_regulations as mod

        captured: dict = {}

        def fake_fetch_acts(**kwargs):
            captured.update(kwargs)
            return iter([])  # empty generator

        monkeypatch.setattr(mod, "fetch_acts", fake_fetch_acts)
        mod.gather_regulations(kov=False, kehtiv="2026-05-01", limit=None)
        assert captured["limiit"] == 500


# ---------------------------------------------------------------------------
# Issue #175 regression coverage
# (Co-located here because no dedicated test file existed for
# generate_missing_parts; the parent plan permits this when noted.)
# ---------------------------------------------------------------------------


class TestMissingPartsCli:
    """Regression coverage for ``generate_missing_parts`` (issue #175)."""

    def _vos_xml(self) -> str:
        # Minimal VÕS XML with three osa elements (2, 6, 10).
        # Each part has its own paragraph numbered the same § 1 so we
        # can verify cross-part IRI uniqueness (the smoke-test).
        return """<akt globaalID="100"><metaandmed><dokumentLiik>seadus</dokumentLiik></metaandmed>
            <osa><osaNr>2</osaNr><osaPealkiri>Lepingu üldosa</osaPealkiri>
              <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
                <paragrahv><paragrahvNr>1</paragrahvNr><paragrahvPealkiri>Reguleerimisala</paragrahvPealkiri>
                  <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr><lauseOsa>Test text osa 2</lauseOsa></loige>
                </paragrahv>
              </peatykk>
            </osa>
            <osa><osaNr>6</osaNr><osaPealkiri>Kindlustuslepingud</osaPealkiri>
              <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
                <paragrahv><paragrahvNr>1</paragrahvNr><paragrahvPealkiri>Mõisted</paragrahvPealkiri>
                  <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr><lauseOsa>Test text osa 6</lauseOsa></loige>
                </paragrahv>
              </peatykk>
            </osa>
            <osa><osaNr>10</osaNr><osaPealkiri>Seltsingulepingud</osaPealkiri>
              <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
                <paragrahv><paragrahvNr>1</paragrahvNr><paragrahvPealkiri>Reguleerimisala</paragrahvPealkiri>
                  <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr><lauseOsa>Test text osa 10</lauseOsa></loige>
                </paragrahv>
              </peatykk>
            </osa>
        </akt>"""

    def test_smoke_no_paragraph_id_collisions_across_parts(self, tmp_path):
        import generate_missing_parts as mod

        vos_path = tmp_path / "vos.xml"
        vos_path.write_text(self._vos_xml(), encoding="utf-8")
        out_dir = tmp_path / "out"

        rc = mod.main([
            "--input-xml-vos", str(vos_path),
            "--output-dir", str(out_dir),
            "--skip-tsus",
        ])
        assert rc == 0

        # Collect all @ids across the four part files and check
        # uniqueness — the issue's smoke test.
        all_ids: list[str] = []
        per_file_ids: dict[str, list[str]] = {}
        for osa_nr in ("2", "6", "10"):
            path = out_dir / f"volaoigusseadus_osa{osa_nr}_peep.json"
            assert path.exists(), f"missing output for osa {osa_nr}"
            doc = json.loads(path.read_text(encoding="utf-8"))
            ids = [n["@id"] for n in doc["@graph"]]
            per_file_ids[path.name] = ids
            all_ids.extend(ids)

        # Paragraph nodes must NOT collide across the three parts.
        paragraph_ids = [
            i for i in all_ids
            if i.startswith("estleg:VOS_") and "_Par_" in i
        ]
        assert len(paragraph_ids) == len(set(paragraph_ids)), (
            "duplicate paragraph @ids across VÕS parts: "
            f"{[x for x in paragraph_ids if paragraph_ids.count(x) > 1]}"
        )

    def test_class_iri_does_not_carry_osa7_typo(self, tmp_path):
        import generate_missing_parts as mod

        vos_path = tmp_path / "vos.xml"
        vos_path.write_text(self._vos_xml(), encoding="utf-8")
        out_dir = tmp_path / "out"
        rc = mod.main([
            "--input-xml-vos", str(vos_path),
            "--output-dir", str(out_dir),
            "--skip-tsus",
        ])
        assert rc == 0

        for osa_nr in ("2", "6", "10"):
            path = out_dir / f"volaoigusseadus_osa{osa_nr}_peep.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            classes = [
                n for n in doc["@graph"]
                if "owl:Class" in (n.get("@type") or [])
            ]
            assert classes, f"no class node in {path.name}"
            for c in classes:
                # The class IRI must NOT contain the literal Osa7 typo.
                assert "Osa7" not in c["@id"], c
                # Expected pattern: estleg:LegalProvision_VOS_osa<N>
                assert c["@id"] == (
                    f"estleg:LegalProvision_VOS_osa{osa_nr}"
                ), c

    def test_paragraph_iri_namespaced_per_part(self, tmp_path):
        import generate_missing_parts as mod

        vos_path = tmp_path / "vos.xml"
        vos_path.write_text(self._vos_xml(), encoding="utf-8")
        out_dir = tmp_path / "out"
        mod.main([
            "--input-xml-vos", str(vos_path),
            "--output-dir", str(out_dir),
            "--skip-tsus",
        ])

        # Each part's § 1 must produce a unique IRI namespaced by osa.
        paragraph_ids_by_osa: dict[str, str] = {}
        for osa_nr in ("2", "6", "10"):
            path = out_dir / f"volaoigusseadus_osa{osa_nr}_peep.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            for n in doc["@graph"]:
                if "estleg:paragrahv" in n:
                    paragraph_ids_by_osa[osa_nr] = n["@id"]
                    break
        assert len(set(paragraph_ids_by_osa.values())) == 3, (
            paragraph_ids_by_osa
        )
        # And the IRI shape includes Osa<N>.
        for osa_nr, iri in paragraph_ids_by_osa.items():
            assert f"_Osa{osa_nr}_" in iri, (osa_nr, iri)

    def test_vos_part_emits_subsections_and_full_legal_text(self, tmp_path):
        import generate_missing_parts as mod

        vos_path = tmp_path / "vos.xml"
        vos_path.write_text(self._vos_xml(), encoding="utf-8")
        out_dir = tmp_path / "out"
        rc = mod.main([
            "--input-xml-vos", str(vos_path),
            "--output-dir", str(out_dir),
            "--skip-tsus",
            "--vos-osa", "2",
        ])
        assert rc == 0

        doc = json.loads((out_dir / "volaoigusseadus_osa2_peep.json").read_text())
        by_id = {node["@id"]: node for node in doc["@graph"]}
        provision = by_id["estleg:VOS_Osa2_Par_1"]
        subsection = by_id["estleg:VOS_Osa2_Par_1_Lg_1"]

        assert provision["estleg:legalText"] == "(1) Test text osa 2"
        assert provision["estleg:hasSubsection"] == [
            {"@id": "estleg:VOS_Osa2_Par_1_Lg_1"}
        ]
        assert subsection["@type"] == ["estleg:Subsection", "owl:NamedIndividual"]
        assert subsection["estleg:subsectionNumber"] == "1"
        assert subsection["estleg:legalText"] == "(1) Test text osa 2"
        assert subsection["estleg:parentProvision"] == {
            "@id": "estleg:VOS_Osa2_Par_1"
        }

    def test_vos_part_without_loige_has_legal_text_but_no_subsection(self):
        import generate_missing_parts as mod

        root = ET.fromstring(
            """<akt><osa><osaNr>2</osaNr><osaPealkiri>Lepingu üldosa</osaPealkiri>
              <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
                <paragrahv><paragrahvNr>9</paragrahvNr><kuvatavNr>§ 9.</kuvatavNr>
                  <lauseOsa>Direct provision text.</lauseOsa>
                </paragrahv>
              </peatykk>
            </osa></akt>"""
        )

        doc = mod.generate_vos_part(root, "https://example.test/vos.xml", "2")
        assert doc is not None
        provision = next(
            node for node in doc["@graph"]
            if node.get("@id") == "estleg:VOS_Osa2_Par_9"
        )

        assert provision["estleg:legalText"] == "Direct provision text."
        assert "estleg:hasSubsection" not in provision
        assert not any(
            "estleg:Subsection" in node.get("@type", [])
            for node in doc["@graph"]
        )

    def test_vos_part_subsection_ids_preserve_superscripts(self):
        import generate_missing_parts as mod

        root = ET.fromstring(
            """<akt><osa><osaNr>2</osaNr><osaPealkiri>Lepingu üldosa</osaPealkiri>
              <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
                <paragrahv><paragrahvNr>9</paragrahvNr><kuvatavNr>§ 9.</kuvatavNr>
                  <loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr><lauseOsa>First text.</lauseOsa></loige>
                  <loige><loigeNr ylaIndeks="1">1</loigeNr><kuvatavNr>(1¹)</kuvatavNr><lauseOsa>Inserted text.</lauseOsa></loige>
                  <loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr><lauseOsa>Second text.</lauseOsa></loige>
                  <loige><loigeNr ylaIndeks="1">2</loigeNr><kuvatavNr>(2¹)</kuvatavNr><lauseOsa>Second inserted text.</lauseOsa></loige>
                </paragrahv>
              </peatykk>
            </osa></akt>"""
        )

        doc = mod.generate_vos_part(root, "https://example.test/vos.xml", "2")
        assert doc is not None
        provision = next(
            node for node in doc["@graph"]
            if node.get("@id") == "estleg:VOS_Osa2_Par_9"
        )

        assert provision["estleg:hasSubsection"] == [
            {"@id": "estleg:VOS_Osa2_Par_9_Lg_1"},
            {"@id": "estleg:VOS_Osa2_Par_9_Lg_1_1"},
            {"@id": "estleg:VOS_Osa2_Par_9_Lg_2"},
            {"@id": "estleg:VOS_Osa2_Par_9_Lg_2_1"},
        ]

    def test_main_returns_int_exit_code(self, tmp_path):
        import generate_missing_parts as mod

        # Failure path — a non-existent XML — must return non-zero.
        rc = mod.main([
            "--input-xml-vos", str(tmp_path / "missing.xml"),
            "--output-dir", str(tmp_path / "out"),
            "--skip-tsus",
        ])
        assert rc == 1

    def test_select_law_match_picks_max_globalid(self):
        from generate_missing_parts import _select_law_match

        rows = [
            {"pealkiri": "Võlaõigusseadus", "globaalID": "12345"},
            {"pealkiri": "Võlaõigusseadus", "globaalID": "999"},
            {"pealkiri": "Võlaõigusseadus", "globaalID": "20000"},
        ]
        match = _select_law_match(rows, "Võlaõigusseadus")
        assert match is not None
        # Numeric max — NOT lexicographic.
        assert match["globaalID"] == "20000"

    def test_select_law_match_prefers_exact_over_substring(self):
        from generate_missing_parts import _select_law_match

        rows = [
            {"pealkiri": "Võlaõigusseaduse muutmise seadus", "globaalID": "9999"},
            {"pealkiri": "Võlaõigusseadus", "globaalID": "1"},
        ]
        match = _select_law_match(rows, "Võlaõigusseadus")
        assert match is not None
        # Exact match must win even though it has a smaller globalID
        # — the substring fallback is only used when no exact match
        # exists.
        assert match["pealkiri"] == "Võlaõigusseadus"

    def test_argparse_vos_osa_repeatable(self, tmp_path):
        import generate_missing_parts as mod

        vos_path = tmp_path / "vos.xml"
        vos_path.write_text(self._vos_xml(), encoding="utf-8")
        out_dir = tmp_path / "out"

        # Override default osa list: only osa 6.
        rc = mod.main([
            "--input-xml-vos", str(vos_path),
            "--output-dir", str(out_dir),
            "--skip-tsus",
            "--vos-osa", "6",
        ])
        assert rc == 0
        assert (out_dir / "volaoigusseadus_osa6_peep.json").exists()
        # The other two parts must NOT have been written.
        assert not (out_dir / "volaoigusseadus_osa2_peep.json").exists()
        assert not (out_dir / "volaoigusseadus_osa10_peep.json").exists()


# ---------------------------------------------------------------------------
# Issue #114 — source-list fetch failures are fatal by default; per-page
# success/fail/retry counters; a truncated run must never publish an index
# that looks like a complete corpus refresh.
# ---------------------------------------------------------------------------


def _reg_act_row(n: int) -> dict:
    return {
        "terviktekstID": str(5000 + n),
        "globaalID": str(9000 + n),
        "url": f"/akt/{n}.xml",
        "pealkiri": f"Test määrus {n}",
        "valjaandja": "Vabariigi Valitsus",
        "kehtivus": {},
    }


def _make_fake_fetch_acts(rows: list[dict]):
    """Return a fake ``fetch_acts`` that yields ``rows`` (the successfully
    fetched "page 1"), then simulates a terminal fetch failure on "page
    2": it raises ``SourceListFetchError`` unless ``allow_partial`` is
    set, in which case it stops early. It mutates the supplied ``stats``
    dict exactly like the real generator would.
    """

    def fake_fetch_acts(*, allow_partial: bool = False, stats: dict | None = None, **_kw):
        if stats is not None:
            for key in ("pagesFetchedOk", "pagesFailed", "pagesRetried"):
                stats.setdefault(key, 0)
            stats["pagesFetchedOk"] += 1  # "page 1" came back fine
        yield from rows
        # "page 2" — the API blows up.
        if stats is not None:
            stats["pagesFailed"] += 1
        if not allow_partial:
            raise SourceListFetchError("API error on page 2: simulated outage")
        # allow_partial: stop without raising — the caller must detect the
        # truncation via stats["pagesFailed"] > 0.

    return fake_fetch_acts


class _PageOneFullPageTwoFailsHttpGet:
    """Fake ``requests.get`` for the Riigi Teataja search API: returns a
    full page (exactly ``limiit`` rows) on ``leht=1`` so ``fetch_acts``
    advances, then fails on ``leht>=2``.
    """

    class _OkResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class _BoomResp:
        def raise_for_status(self):
            raise RuntimeError("simulated 503")

        def json(self):  # pragma: no cover - never reached
            return {}

    def __call__(self, url, *, params=None, timeout=None, **kw):
        params = params or {}
        page = int(params.get("leht", 1))
        limiit = int(params.get("limiit", 1))
        if page == 1:
            return self._OkResp({"aktid": [_reg_act_row(i) for i in range(limiit)]})
        return self._BoomResp()


class TestFetchActsPageCounters:
    """``fetch_acts`` must populate the per-page counter dict and stop /
    raise correctly on a mid-enumeration failure (issue #114)."""

    def test_terminal_failure_raises_and_counts_pages(self, monkeypatch):
        monkeypatch.setattr(
            riigiteataja_common.requests, "get", _PageOneFullPageTwoFailsHttpGet()
        )
        monkeypatch.setattr(riigiteataja_common.time, "sleep", lambda *_a, **_k: None)

        stats = new_page_stats()
        gen = fetch_acts(document="määrus", limiit=2, stats=stats)
        with pytest.raises(SourceListFetchError):
            list(gen)

        assert stats["pagesFetchedOk"] == 1
        assert stats["pagesFailed"] == 1
        # Default max_retries=2 > 0, so the failed page counts as retried.
        assert stats["pagesRetried"] == 1

    def test_allow_partial_stops_early_and_counts_pages(self, monkeypatch):
        monkeypatch.setattr(
            riigiteataja_common.requests, "get", _PageOneFullPageTwoFailsHttpGet()
        )
        monkeypatch.setattr(riigiteataja_common.time, "sleep", lambda *_a, **_k: None)

        stats = new_page_stats()
        rows = list(
            fetch_acts(document="määrus", limiit=2, allow_partial=True, stats=stats)
        )
        # Page 1's two rows still made it through.
        assert len(rows) == 2
        assert stats["pagesFetchedOk"] == 1
        assert stats["pagesFailed"] == 1

    def test_retry_then_success_increments_pages_retried(self, monkeypatch):
        calls = {"n": 0}

        class _OkResp:
            def raise_for_status(self):
                pass

            def json(self):
                # Short page (< limiit) terminates enumeration cleanly.
                return {"aktid": [_reg_act_row(1)]}

        class _BoomResp:
            def raise_for_status(self):
                raise RuntimeError("transient")

            def json(self):  # pragma: no cover
                return {}

        def flaky_get(url, *, params=None, timeout=None, **kw):
            calls["n"] += 1
            # Fail the first attempt, succeed on the retry.
            return _BoomResp() if calls["n"] == 1 else _OkResp()

        monkeypatch.setattr(riigiteataja_common.requests, "get", flaky_get)
        monkeypatch.setattr(riigiteataja_common.time, "sleep", lambda *_a, **_k: None)

        stats = new_page_stats()
        rows = list(fetch_acts(document="määrus", limiit=10, stats=stats))
        assert len(rows) == 1
        assert stats["pagesFetchedOk"] == 1
        assert stats["pagesFailed"] == 0
        assert stats["pagesRetried"] == 1


class TestGatherRegulationsFetchFailure:
    """``gather_regulations`` must propagate the fatal error by default and,
    under ``--allow-partial``, mark the manifest incomplete with the page
    counters intact."""

    def test_fetch_failure_is_fatal_by_default(self, monkeypatch):
        monkeypatch.setattr(
            generate_regulations,
            "fetch_acts",
            _make_fake_fetch_acts([_reg_act_row(1)]),
        )
        with pytest.raises(SourceListFetchError):
            gather_regulations(kov=False, kehtiv="2026-05-01", limit=None)

    def test_allow_partial_marks_manifest_incomplete_with_page_counters(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            generate_regulations,
            "fetch_acts",
            _make_fake_fetch_acts([_reg_act_row(1), _reg_act_row(2)]),
        )
        regs, manifest = gather_regulations(
            kov=False, kehtiv="2026-05-01", limit=None, allow_partial=True
        )
        # Page 1's two rows still produced two unique regulations.
        assert len(regs) == 2
        assert manifest["complete"] is False
        assert manifest["pages"]["pagesFetchedOk"] >= 1
        assert manifest["pages"]["pagesFailed"] >= 1
        assert set(manifest["pages"]) == {
            "pagesFetchedOk",
            "pagesFailed",
            "pagesRetried",
        }

    def test_clean_run_marks_manifest_complete(self, monkeypatch):
        def fake_fetch_acts(*, stats=None, **_kw):
            if stats is not None:
                for key in ("pagesFetchedOk", "pagesFailed", "pagesRetried"):
                    stats.setdefault(key, 0)
                stats["pagesFetchedOk"] += 1
            yield _reg_act_row(1)
            # No failure — enumeration ran to the end.

        monkeypatch.setattr(generate_regulations, "fetch_acts", fake_fetch_acts)
        regs, manifest = gather_regulations(
            kov=False, kehtiv="2026-05-01", limit=None
        )
        assert manifest["complete"] is True
        assert manifest["pages"]["pagesFailed"] == 0
        assert manifest["pages"]["pagesFetchedOk"] >= 1


class TestGenerateRegulationsNoPartialWrite:
    """``generate_regulations.main()`` must, on a mid-enumeration fetch
    failure without ``--allow-partial``, exit non-zero and write NEITHER
    the index NOR any ``*_peep.json`` output. With ``--allow-partial`` the
    run completes and the index carries ``complete: false`` plus the page
    counters so a consumer cannot mistake it for a full refresh.
    """

    def _wire_outputs(self, tmp_path, monkeypatch):
        riik = tmp_path / "regulations" / "riik"
        kov = tmp_path / "regulations" / "kov"
        monkeypatch.setattr(generate_regulations, "OUTPUT_RIIK", riik)
        monkeypatch.setattr(generate_regulations, "OUTPUT_KOV", kov)
        return riik

    def test_main_exits_nonzero_and_writes_no_index(self, tmp_path, monkeypatch):
        riik = self._wire_outputs(tmp_path, monkeypatch)
        monkeypatch.setattr(
            generate_regulations,
            "fetch_acts",
            _make_fake_fetch_acts([_reg_act_row(1)]),
        )
        # fetch_xml must never be reached — gather_regulations raises first.
        monkeypatch.setattr(
            generate_regulations,
            "fetch_xml",
            lambda *a, **kw: pytest.fail("fetch_xml must not run after fetch failure"),
        )
        monkeypatch.setattr(sys, "argv", ["generate_regulations.py"])

        with pytest.raises(SystemExit) as exc_info:
            generate_regulations.main()
        assert exc_info.value.code != 0

        assert not (riik / "REGULATIONS_RIIK_INDEX.json").exists(), (
            "index must NOT be written after a truncated source enumeration"
        )
        assert list(riik.glob("**/*_peep.json")) == [], (
            "no per-regulation outputs may be written when the fetch failed"
        )

    def test_allow_partial_writes_index_marked_incomplete(
        self, tmp_path, monkeypatch
    ):
        riik = self._wire_outputs(tmp_path, monkeypatch)
        monkeypatch.setattr(
            generate_regulations,
            "fetch_acts",
            _make_fake_fetch_acts([_reg_act_row(1)]),
        )
        # Serve a minimal act XML offline so the partial run actually
        # produces one peep file alongside the partial index.
        monkeypatch.setattr(
            generate_regulations,
            "fetch_xml",
            lambda *a, **kw: ET.fromstring(
                "<akt><metaandmed>"
                "<terviktekstiGrupiID>5001</terviktekstiGrupiID>"
                "<globaalID>9001</globaalID>"
                "</metaandmed></akt>"
            ),
        )
        monkeypatch.setattr(
            sys, "argv", ["generate_regulations.py", "--allow-partial", "--sleep", "0"]
        )

        generate_regulations.main()

        index_path = riik / "REGULATIONS_RIIK_INDEX.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert index["run"]["complete"] is False
        assert index["run"]["partialAllowed"] is True
        pages = index["run"]["pages"]
        assert pages["pagesFetchedOk"] >= 1
        assert pages["pagesFailed"] >= 1
        # The partial run still wrote (and indexed) the rows it got.
        assert index["totalRegulations"] >= 1
