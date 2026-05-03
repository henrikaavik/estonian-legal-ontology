"""Tests for extract_temporal_data — recursive XML discovery + globaalID pairing."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer2a"
PEEP_FIXTURE = FIXTURE_DIR / "sample_kov_act.json"
XML_FIXTURE = FIXTURE_DIR / "sample_kov_xml.xml"


class TestBuildGlobalIdLookup:
    def test_recursive_xml_discovery(self, tmp_path):
        from extract_temporal_data import build_globalid_xml_lookup

        # Stage three XML files in subdirectories to mimic the real layout
        rt = tmp_path / "data" / "riigiteataja"
        (rt / "maarus").mkdir(parents=True)
        (rt / "maarus_kov").mkdir(parents=True)
        (rt / "law_alpha.xml").write_text(
            '<akt globaalID="111"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )
        (rt / "maarus" / "reg_222.xml").write_text(
            '<akt globaalID="222"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )
        (rt / "maarus_kov" / "reg_333.xml").write_text(
            '<akt globaalID="333"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = build_globalid_xml_lookup(rt)
        assert lookup["111"].name == "law_alpha.xml"
        assert lookup["222"].name == "reg_222.xml"
        assert lookup["333"].name == "reg_333.xml"

    def test_kov_xml_in_maarus_kov_subdir(self, tmp_path):
        """KOV XML lives at data/riigiteataja/maarus_kov/reg_<gid>.xml;
        the recursive walk must find it."""
        from extract_temporal_data import build_globalid_xml_lookup
        rt = tmp_path / "data" / "riigiteataja"
        (rt / "maarus_kov").mkdir(parents=True)
        # Use the kov fixture's globalId
        (rt / "maarus_kov" / "reg_999999999.xml").write_text(
            '<akt globaalID="999999999"><metaandmed>'
            '<vastuvotjaLiik>Tallinna Linnavolikogu</vastuvotjaLiik>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )
        lookup = build_globalid_xml_lookup(rt)
        assert "999999999" in lookup
        assert "maarus_kov" in str(lookup["999999999"])


class TestPairPeepWithXml:
    def test_pairs_via_globalid_attribute(self, tmp_path):
        from extract_temporal_data import pair_peep_with_xml

        peep = tmp_path / "act.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [
                {"@id": "estleg:Reg_9999_Map_2026",
                 "estleg:globalId": "999",
                 "@type": ["estleg:MunicipalRegulation"]}
            ],
        }), encoding="utf-8")

        xml = tmp_path / "reg_999.xml"
        xml.write_text(
            '<akt globaalID="999"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = {"999": xml}
        result = pair_peep_with_xml(peep, lookup)
        assert result == xml

    def test_unpaired_returns_none(self, tmp_path):
        from extract_temporal_data import pair_peep_with_xml
        peep = tmp_path / "act.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [
                {"@id": "estleg:Reg_X",
                 "estleg:globalId": "missing-id",
                 "@type": ["estleg:Law"]}
            ],
        }), encoding="utf-8")
        assert pair_peep_with_xml(peep, lookup={}) is None

    def test_slug_fallback_for_law_without_globalid(self, tmp_path):
        """The 615 indexed laws predate the estleg:globalId field — their
        act nodes carry estleg:Law but no globalId. Without a slug
        fallback, the new pairing would silently drop XML for every
        law, regressing existing temporal/amendment-history coverage.
        """
        from extract_temporal_data import pair_peep_with_xml

        # Stage a law peep file with NO estleg:globalId
        rt = tmp_path / "data" / "riigiteataja"
        rt.mkdir(parents=True)
        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        peep = krr / "alkoholiseadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AlkS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Alkoholiseadus"}
                # NO estleg:globalId — matches real law peep shape
            ],
        }), encoding="utf-8")
        # Slug-named XML at the data root (laws are at the top level,
        # NOT under maarus/ or maarus_kov/)
        (rt / "alkoholiseadus.xml").write_text(
            '<akt><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        # globalId path returns None (no globalId on the act);
        # slug path resolves alkoholiseadus_peep -> alkoholiseadus.xml.
        result = pair_peep_with_xml(peep, lookup={}, data_dir=rt)
        assert result is not None
        assert result.name == "alkoholiseadus.xml"

    def test_slug_fallback_handles_osa_suffix(self, tmp_path):
        """Multi-part laws like asjaoigusseadus_osa1 share their
        XML with the base slug (asjaoigusseadus.xml). The slug
        fallback strips _osaN before re-resolving."""
        from extract_temporal_data import pair_peep_with_xml

        rt = tmp_path / "data" / "riigiteataja"
        rt.mkdir(parents=True)
        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        peep = krr / "asjaoigusseadus_osa3_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AOS_Osa3_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"]}
            ],
        }), encoding="utf-8")
        # XML lives at base-slug name only (no per-osa XML)
        (rt / "asjaoigusseadus.xml").write_text(
            '<akt><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        result = pair_peep_with_xml(peep, lookup={}, data_dir=rt)
        assert result is not None
        assert result.name == "asjaoigusseadus.xml"


class TestMainUsesGlobalIdLookup:
    """Pin the contract that main() actually wires the new helpers.
    The helper unit tests above pass even if main() still uses the old
    slug-based pairing (the helpers exist and work in isolation but the
    pipeline doesn't call them). This test runs main() with monkeypatched
    paths over a tiny KOV-like fixture and asserts that an XML file under
    maarus_kov/ paired by globalId actually contributed temporal data
    to the corresponding peep file.
    """

    def test_main_pairs_kov_via_globalid(self, tmp_path, monkeypatch):
        import extract_temporal_data as mod
        import estleg_common

        # Stage a tiny tree
        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (rt / "maarus_kov").mkdir(parents=True)

        # Peep file with globalId 999 — slug-based pairing would fail
        # (filename doesn't share a slug with the XML)
        peep = kov / "test_act_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "rdfs:label": "Test KOV act",
                 "estleg:globalId": "999",
                 "estleg:enactedBy": {
                     "@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_EHAK_0784"},
                 "estleg:titleNormalized": "test"}
            ],
        }), encoding="utf-8")

        # XML uses the actual tags the extractor reads:
        # vastuvoetud/aktikuupaev (adoption) and vastuvoetud/joustumine
        # (entry into force). See scripts/extract_temporal_data.py
        # lines 133-145 for the field reader.
        (rt / "maarus_kov" / "reg_999.xml").write_text(
            '<akt globaalID="999"><metaandmed>'
            '<vastuvoetud>'
            '<aktikuupaev>2023-12-15</aktikuupaev>'
            '<joustumine>2024-01-01</joustumine>'
            '</vastuvoetud>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        # Critical: iter_peep_files() is imported from estleg_common,
        # whose own KRR_DIR was bound at import time. Patch BOTH the
        # extractor module's KRR_DIR/DATA_DIR AND estleg_common.KRR_DIR
        # — otherwise the iterator will scan the real repo regardless
        # of what extract_temporal_data thinks KRR_DIR is.
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        # Run main() — should write back temporal triples to the peep
        # file based on the paired XML.
        rc = mod.main()
        assert rc in (None, 0)

        # Re-read the peep file and verify a temporal triple was added
        # from the paired XML. The script writes estleg:entryIntoForce
        # (and possibly estleg:adoptionDate / estleg:repealDate /
        # estleg:lastAmendmentDate). Any of these is acceptable
        # evidence that pairing worked.
        with open(peep, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        act = next(n for n in doc["@graph"]
                   if "estleg:MunicipalRegulation" in (n.get("@type") or []))
        date_keys = {"estleg:entryIntoForce", "estleg:adoptionDate",
                     "estleg:repealDate", "estleg:lastAmendmentDate",
                     "estleg:publicationDate"}
        present = date_keys.intersection(act.keys())
        assert present, (
            f"main() ran but no date triples appeared on the act node "
            f"after pairing — pair_peep_with_xml may not be wired into "
            f"main(). Act keys: {list(act.keys())}"
        )
