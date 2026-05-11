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
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import estleg_common
from generate_regulations import (
    _gid_rank,
    build_regulation_index,
    build_regulation_jsonld,
    classify_issuer,
    existing_is_stale,
    provision_summary,
    regulation_file_tid,
    source_removed_files,
    write_regulation_output,
)
from riigiteataja_common import (
    iter_peep_files,
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
        # KOV regulations are municipal and also carry the domestic-regulation
        # umbrella class so common SHACL constraints target them directly.
        assert classes == ["estleg:NationalRegulation", "estleg:MunicipalRegulation"]

    def test_central_bank_only_national(self):
        # Eesti Pank is central but not a minister and not the government.
        classes = classify_issuer("Eesti Pank", is_kov=False)
        assert classes == ["estleg:NationalRegulation"]

    def test_no_issuer_falls_back_to_national(self):
        classes = classify_issuer(None, is_kov=False)
        assert classes == ["estleg:NationalRegulation"]


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
        assert "estleg:MunicipalRegulation" in type_list
        assert "estleg:NationalRegulation" in type_list
        assert "estleg:GovernmentRegulation" not in type_list

    def test_kov_false_marks_national(self):
        root = _parse(STRUCTURED_FIXTURE)
        doc, _ = build_regulation_jsonld(STRUCTURED_TITLE, {}, root, is_kov=False)

        ontology = doc["@graph"][0]
        is_kov = ontology["estleg:isKov"]
        assert is_kov == {"@value": "false", "@type": "xsd:boolean"}

        type_list = ontology["@type"]
        assert "estleg:NationalRegulation" in type_list
        assert "estleg:MunicipalRegulation" not in type_list


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
                  <loige><lauseOsa>Test text osa 2</lauseOsa></loige>
                </paragrahv>
              </peatykk>
            </osa>
            <osa><osaNr>6</osaNr><osaPealkiri>Kindlustuslepingud</osaPealkiri>
              <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
                <paragrahv><paragrahvNr>1</paragrahvNr><paragrahvPealkiri>Mõisted</paragrahvPealkiri>
                  <loige><lauseOsa>Test text osa 6</lauseOsa></loige>
                </paragrahv>
              </peatykk>
            </osa>
            <osa><osaNr>10</osaNr><osaPealkiri>Seltsingulepingud</osaPealkiri>
              <peatykk><peatykkNr>1</peatykkNr><peatykkPealkiri>Üldsätted</peatykkPealkiri>
                <paragrahv><paragrahvNr>1</paragrahvNr><paragrahvPealkiri>Reguleerimisala</paragrahvPealkiri>
                  <loige><lauseOsa>Test text osa 10</lauseOsa></loige>
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
            path = out_dir / f"volaigusseadus_osa{osa_nr}_peep.json"
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
            path = out_dir / f"volaigusseadus_osa{osa_nr}_peep.json"
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
            path = out_dir / f"volaigusseadus_osa{osa_nr}_peep.json"
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
        assert (out_dir / "volaigusseadus_osa6_peep.json").exists()
        # The other two parts must NOT have been written.
        assert not (out_dir / "volaigusseadus_osa2_peep.json").exists()
        assert not (out_dir / "volaigusseadus_osa10_peep.json").exists()
