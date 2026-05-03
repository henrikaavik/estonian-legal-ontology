"""End-to-end tests for KOV layer 1 enrichment."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from enrich_kov_layer1 import build_municipality_doc

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"
SAMPLE_KOV_ACT = FIXTURE_DIR / "sample_kov_act.json"
SAMPLE_LAW = FIXTURE_DIR / "sample_law.json"


class TestBuildMunicipalityDoc:
    def test_builds_jsonld_doc(self):
        from kov_registry import load_municipalities
        muns = load_municipalities(MIN_MUNICIPALITIES)
        doc = build_municipality_doc(muns)

        assert "@context" in doc
        assert doc["@context"]["estleg"] == "https://data.riik.ee/ontology/estleg#"

        graph = doc["@graph"]
        ids = {n["@id"] for n in graph}
        assert "estleg:Municipality_EHAK_0784" in ids

        tallinn = next(n for n in graph if n["@id"] == "estleg:Municipality_EHAK_0784")
        assert "estleg:Municipality" in tallinn["@type"]
        assert tallinn["rdfs:label"] == "Tallinn"
        assert tallinn["estleg:ehakCode"] == "0784"
        assert tallinn["estleg:county"] == "Harju maakond"


class TestBuildIssuerDoc:
    @pytest.fixture
    def issuers(self):
        return {
            "tallinna_linnavolikogu": {
                "slug": "tallinna_linnavolikogu",
                "displayName": "Tallinna Linnavolikogu",
                "bodyType": "volikogu",
                "currentMunicipalityCode": "0784",
                "mappingSource": "auto-match",
                "mappingEvidence": "",
                "historicalMunicipalityName": "Tallinn",
            },
            "abja_vallavolikogu": {
                "slug": "abja_vallavolikogu",
                "displayName": "Abja Vallavolikogu",
                "bodyType": "volikogu",
                "currentMunicipalityCode": "0480",
                "mappingSource": "haldusreform-2017",
                "mappingEvidence": "RT I 21.06.2017 1",
                "historicalMunicipalityName": "Abja",
            },
        }

    def test_builds_jsonld_doc(self, issuers):
        from enrich_kov_layer1 import build_issuer_doc
        doc = build_issuer_doc(issuers)
        graph = doc["@graph"]

        ids = {n["@id"] for n in graph}
        assert "estleg:Issuer_tallinna_linnavolikogu" in ids
        assert "estleg:Issuer_abja_vallavolikogu" in ids

        tallinn = next(n for n in graph
                       if n["@id"] == "estleg:Issuer_tallinna_linnavolikogu")
        assert "estleg:Issuer" in tallinn["@type"]
        assert tallinn["rdfs:label"] == "Tallinna Linnavolikogu"
        assert tallinn["estleg:bodyType"] == "volikogu"
        assert tallinn["estleg:currentMunicipality"] == {
            "@id": "estleg:Municipality_EHAK_0784"
        }
        assert tallinn["estleg:mappingSource"] == "auto-match"
        # Empty evidence is omitted entirely
        assert "estleg:mappingEvidence" not in tallinn

        abja = next(n for n in graph
                    if n["@id"] == "estleg:Issuer_abja_vallavolikogu")
        assert abja["estleg:mappingEvidence"] == "RT I 21.06.2017 1"
        assert abja["estleg:historicalMunicipalityName"] == "Abja"


class TestEnrichKovActFile:
    @pytest.fixture
    def issuer(self):
        return {
            "slug": "tallinna_linnavolikogu",
            "displayName": "Tallinna Linnavolikogu",
            "bodyType": "volikogu",
            "currentMunicipalityCode": "0784",
            "mappingSource": "auto-match",
            "mappingEvidence": "",
            "historicalMunicipalityName": "Tallinn",
        }

    @pytest.fixture
    def temp_act(self, tmp_path):
        dest = tmp_path / "act.json"
        shutil.copy(SAMPLE_KOV_ACT, dest)
        return dest

    def test_act_node_enriched(self, temp_act, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        enrich_kov_act_file(temp_act, issuer)
        with open(temp_act, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        act = next(n for n in doc["@graph"]
                   if n["@id"] == "estleg:Reg_1014955_Map_2026")
        assert act["estleg:enactedBy"] == {"@id": "estleg:Issuer_tallinna_linnavolikogu"}
        assert act["estleg:enactedByMunicipality"] == {"@id": "estleg:Municipality_EHAK_0784"}
        assert act["estleg:titleNormalized"] == "jaatmehoolduseeskiri"
        # Direct Act type stamped (so partOfAct SHACL doesn't need inference)
        assert "estleg:Act" in act["@type"]
        # MunicipalRegulation preserved
        assert "estleg:MunicipalRegulation" in act["@type"]

    def test_provisions_enriched(self, temp_act, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        enrich_kov_act_file(temp_act, issuer)
        with open(temp_act, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        provisions = [n for n in doc["@graph"] if "estleg:paragrahv" in n]
        assert len(provisions) == 2
        for p in provisions:
            assert "estleg:KovProvision" in p["@type"]
            assert p["estleg:enactedBy"] == {"@id": "estleg:Issuer_tallinna_linnavolikogu"}
            assert p["estleg:enactedByMunicipality"] == {"@id": "estleg:Municipality_EHAK_0784"}
            assert p["estleg:partOfAct"] == {"@id": "estleg:Reg_1014955_Map_2026"}

    def test_idempotent(self, temp_act, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        enrich_kov_act_file(temp_act, issuer)
        once = json.load(open(temp_act, encoding="utf-8"))
        enrich_kov_act_file(temp_act, issuer)
        twice = json.load(open(temp_act, encoding="utf-8"))
        assert once == twice  # second run is a no-op

    def test_missing_act_node_raises(self, tmp_path, issuer):
        # A KOV file shape that lacks any estleg:MunicipalRegulation node
        # must fail loudly — not silently skip while the orchestrator
        # increments its enriched-count.
        from enrich_kov_layer1 import enrich_kov_act_file
        bad = tmp_path / "broken.json"
        bad.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:NotAnAct_Map_2026",
                 "@type": ["owl:Ontology"],
                 "rdfs:label": "garbage"}
            ],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="no estleg:MunicipalRegulation"):
            enrich_kov_act_file(bad, issuer)

    def test_multiple_act_nodes_raise(self, tmp_path, issuer):
        # Two MunicipalRegulation nodes in one file must also fail —
        # otherwise provisions would all link to whichever comes first
        # and partial enrichment would corrupt the file silently.
        from enrich_kov_layer1 import enrich_kov_act_file
        bad = tmp_path / "two_acts.json"
        bad.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_2_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
            ],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="found 2"):
            enrich_kov_act_file(bad, issuer)


class TestStampLawType:
    @pytest.fixture
    def temp_law(self, tmp_path):
        dest = tmp_path / "law.json"
        shutil.copy(SAMPLE_LAW, dest)
        return dest

    def test_stamps_law_type(self, temp_law):
        from enrich_kov_layer1 import stamp_law_type
        stamp_law_type(temp_law)
        with open(temp_law, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        law_node = doc["@graph"][0]
        assert "owl:Ontology" in law_node["@type"]
        assert "estleg:Law" in law_node["@type"]
        # estleg:Act is stamped alongside Law so SHACL constraints with
        # sh:class estleg:Act don't require RDFS inference.
        assert "estleg:Act" in law_node["@type"]

    def test_idempotent(self, temp_law):
        from enrich_kov_layer1 import stamp_law_type
        stamp_law_type(temp_law)
        once = json.load(open(temp_law, encoding="utf-8"))
        stamp_law_type(temp_law)
        twice = json.load(open(temp_law, encoding="utf-8"))
        assert once == twice

    def test_skips_non_root_types(self, temp_law):
        # If the act node is already a different specific type (e.g.
        # NationalRegulation), don't add Law — only top-level
        # owl:Ontology act nodes should be stamped with Law. (Act is
        # stamped on regulation acts via stamp_act_type instead.)
        with open(temp_law, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["@graph"][0]["@type"] = ["owl:Ontology", "estleg:NationalRegulation"]
        with open(temp_law, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

        from enrich_kov_layer1 import stamp_law_type
        stamp_law_type(temp_law)
        with open(temp_law, "r", encoding="utf-8") as fh:
            doc2 = json.load(fh)
        assert "estleg:Law" not in doc2["@graph"][0]["@type"]


class TestStampActType:
    """Stamps estleg:Act on state regulation acts (regulations/riik/)
    so that partOfAct and issuedUnder SHACL constraints don't need
    inference."""

    def test_stamps_act_on_national_regulation(self, tmp_path):
        from enrich_kov_layer1 import stamp_act_type
        f = tmp_path / "reg.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1009410_Map_2026",
                 "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                 "rdfs:label": "test"}
            ],
        }), encoding="utf-8")
        stamp_act_type(f)
        doc = json.load(open(f, encoding="utf-8"))
        types = doc["@graph"][0]["@type"]
        assert "estleg:Act" in types
        assert "estleg:NationalRegulation" in types  # preserved

    def test_idempotent(self, tmp_path):
        from enrich_kov_layer1 import stamp_act_type
        f = tmp_path / "reg.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:GovernmentRegulation"]}
            ],
        }), encoding="utf-8")
        stamp_act_type(f)
        once = json.load(open(f, encoding="utf-8"))
        stamp_act_type(f)
        twice = json.load(open(f, encoding="utf-8"))
        assert once == twice
