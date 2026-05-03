"""End-to-end tests for KOV layer 1 enrichment."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from enrich_kov_layer1 import build_municipality_doc

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"


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
