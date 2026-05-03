"""Tests for scripts/kov_registry.py — issuer/municipality registry helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from kov_registry import (
    auto_match_municipality,
    build_issuer_registry,
    discover_issuer_slugs,
    load_curated_map,
    load_municipalities,
    normalize_title,
    parse_issuer_slug,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"


class TestLoadMunicipalities:
    def test_returns_dict_keyed_by_ehak(self):
        muns = load_municipalities(MIN_MUNICIPALITIES)
        assert isinstance(muns, dict)
        assert "0784" in muns  # Tallinn

    def test_each_entry_has_required_fields(self):
        muns = load_municipalities(MIN_MUNICIPALITIES)
        for code, entry in muns.items():
            assert entry["ehakCode"] == code
            assert entry["name"]
            assert entry["county"]
            assert entry["type"] in {"linn", "vald"}

    def test_duplicate_ehak_raises(self, tmp_path):
        path = tmp_path / "dup.json"
        path.write_text(json.dumps([
            {"ehakCode": "0784", "name": "Tallinn",
             "county": "Harju maakond", "type": "linn"},
            {"ehakCode": "0784", "name": "Duplicate",
             "county": "Harju maakond", "type": "linn"},
        ]), encoding="utf-8")
        with pytest.raises(ValueError, match="0784"):
            load_municipalities(path)

    def test_invalid_type_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps([
            {"ehakCode": "0784", "name": "Tallinn",
             "county": "Harju maakond", "type": "city"},  # not linn|vald
        ]), encoding="utf-8")
        with pytest.raises(ValueError, match="city"):
            load_municipalities(path)

    def test_invalid_ehak_shape_raises(self, tmp_path):
        path = tmp_path / "bad_code.json"
        path.write_text(json.dumps([
            {"ehakCode": "784", "name": "Tallinn",  # 3 digits
             "county": "Harju maakond", "type": "linn"},
        ]), encoding="utf-8")
        with pytest.raises(ValueError, match="4-digit"):
            load_municipalities(path)

    def test_real_file_has_79_entries(self):
        path = REPO_ROOT / "data" / "ehak" / "municipalities.json"
        muns = load_municipalities(path)
        assert len(muns) == 79


class TestParseIssuerSlug:
    def test_linnavolikogu(self):
        parts = parse_issuer_slug("tallinna_linnavolikogu")
        assert parts == {
            "slug": "tallinna_linnavolikogu",
            "root": "tallinna",
            "body": "linnavolikogu",
            "bodyType": "volikogu",
            "municipalityType": "linn",
        }

    def test_vallavalitsus(self):
        parts = parse_issuer_slug("mulgi_vallavalitsus")
        assert parts["bodyType"] == "valitsus"
        assert parts["municipalityType"] == "vald"

    def test_linnavalitsus(self):
        parts = parse_issuer_slug("tartu_linnavalitsus")
        assert parts["bodyType"] == "valitsus"
        assert parts["municipalityType"] == "linn"

    def test_vallavolikogu(self):
        parts = parse_issuer_slug("kose_vallavolikogu")
        assert parts["bodyType"] == "volikogu"
        assert parts["municipalityType"] == "vald"

    def test_compound_root(self):
        parts = parse_issuer_slug("kohtla_jarve_linnavolikogu")
        assert parts["root"] == "kohtla_jarve"
        assert parts["body"] == "linnavolikogu"

    def test_alevivolikogu_historical(self):
        # Vändra alev existed pre-2017 reform; its volikogu issued
        # municipal regulations that are still in the corpus. Treated
        # as municipalityType=vald because alev maps to a vald-class
        # successor in current EHAK.
        parts = parse_issuer_slug("vandra_alevivolikogu")
        assert parts["root"] == "vandra"
        assert parts["body"] == "alevivolikogu"
        assert parts["bodyType"] == "volikogu"
        assert parts["municipalityType"] == "vald"

    def test_unknown_body_raises(self):
        with pytest.raises(ValueError, match="unknown body suffix"):
            parse_issuer_slug("foo_riigikogu")

    def test_no_underscore_raises(self):
        with pytest.raises(ValueError, match="unknown body suffix"):
            parse_issuer_slug("foo")


class TestAutoMatchMunicipality:
    @pytest.fixture
    def muns(self):
        return load_municipalities(MIN_MUNICIPALITIES)

    def test_unique_linn_match(self, muns):
        parts = {"slug": "tallinna_linnavolikogu", "root": "tallinna",
                 "body": "linnavolikogu", "bodyType": "volikogu",
                 "municipalityType": "linn"}
        assert auto_match_municipality(parts, muns) == "0784"

    def test_unique_vald_match(self, muns):
        parts = {"slug": "mulgi_vallavolikogu", "root": "mulgi",
                 "body": "vallavolikogu", "bodyType": "volikogu",
                 "municipalityType": "vald"}
        assert auto_match_municipality(parts, muns) == "0480"

    def test_disambiguates_linn_from_vald(self, muns):
        # Tartu exists as both linn (0793) and vald (0796)
        # tartu_linnavolikogu must match only the linn
        linn_parts = {"slug": "tartu_linnavolikogu", "root": "tartu",
                      "body": "linnavolikogu", "bodyType": "volikogu",
                      "municipalityType": "linn"}
        vald_parts = {"slug": "tartu_vallavolikogu", "root": "tartu",
                      "body": "vallavolikogu", "bodyType": "volikogu",
                      "municipalityType": "vald"}
        assert auto_match_municipality(linn_parts, muns) == "0793"
        assert auto_match_municipality(vald_parts, muns) == "0796"

    def test_no_match_returns_none(self, muns):
        # 'abja' is a defunct pre-2017 vald, not in the current 79
        parts = {"slug": "abja_vallavolikogu", "root": "abja",
                 "body": "vallavolikogu", "bodyType": "volikogu",
                 "municipalityType": "vald"}
        assert auto_match_municipality(parts, muns) is None

    def test_multi_match_returns_none(self):
        # Synthetic edge case: two same-type municipalities normalise to the
        # same root via the diacritic translation table. auto_match must
        # return None (defer to curated CSV), not silently pick one.
        muns = {
            "0001": {"ehakCode": "0001", "name": "Tartu vald",
                     "county": "X maakond", "type": "vald"},
            "0002": {"ehakCode": "0002", "name": "Tärtu vald",  # ä -> a
                     "county": "Y maakond", "type": "vald"},
        }
        parts = {"slug": "tartu_vallavolikogu", "root": "tartu",
                 "body": "vallavolikogu", "bodyType": "volikogu",
                 "municipalityType": "vald"}
        assert auto_match_municipality(parts, muns) is None

    def test_genitive_form_matches_consonant_final_name(self):
        # 'Tallinn' is the only consonant-final current municipality;
        # its slug root is 'tallinna' (genitive). The +"a" rule must
        # remain in place to keep that pairing.
        from kov_registry import _matches_municipality_root
        assert _matches_municipality_root("tallinna", "Tallinn") is True
        assert _matches_municipality_root("tallinn", "Tallinn") is True
        # Vowel-final names should not silently match an `+a` extension
        # of an unrelated name.
        assert _matches_municipality_root("tartua", "Tartu vald") is False


class TestLoadCuratedMap:
    @pytest.fixture
    def csv_path(self, tmp_path):
        p = tmp_path / "map.csv"
        p.write_text(
            "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"
            "abja_vallavolikogu,0480,haldusreform-2017,RT I 21.06.2017 1\n"
            "aegviidu_vallavolikogu,0150,haldusreform-2017,RT I 21.06.2017 1\n",
            encoding="utf-8",
        )
        return p

    def test_loads_rows_keyed_by_slug(self, csv_path):
        rows = load_curated_map(csv_path)
        assert set(rows) == {"abja_vallavolikogu", "aegviidu_vallavolikogu"}
        abja = rows["abja_vallavolikogu"]
        assert abja["currentMunicipalityCode"] == "0480"
        assert abja["mappingSource"] == "haldusreform-2017"
        assert abja["mappingEvidence"] == "RT I 21.06.2017 1"

    def test_empty_evidence_rejected(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text(
            "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"
            "x_vallavolikogu,0480,haldusreform-2017,\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="mapping_evidence"):
            load_curated_map(p)

    def test_duplicate_slug_rejected(self, tmp_path):
        p = tmp_path / "dup.csv"
        p.write_text(
            "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"
            "x_vallavolikogu,0480,haldusreform-2017,evidence-a\n"
            "x_vallavolikogu,0784,haldusreform-2017,evidence-b\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicate"):
            load_curated_map(p)

    def test_header_only_returns_empty(self, tmp_path):
        # The seed file at data/ehak/issuer_successor_map.csv ships
        # with the header only — Task 5 fills it in. Loading it must
        # return an empty dict, not raise.
        p = tmp_path / "header_only.csv"
        p.write_text(
            "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n",
            encoding="utf-8",
        )
        assert load_curated_map(p) == {}


class TestDiscoverIssuerSlugs:
    def test_finds_directory_names(self, tmp_path):
        kov_root = tmp_path / "regulations" / "kov"
        for name in ("tallinna_linnavolikogu", "mulgi_vallavolikogu"):
            (kov_root / name).mkdir(parents=True)
        # Also create the index file that should be ignored
        (kov_root / "REGULATIONS_KOV_INDEX.json").write_text("{}")

        slugs = discover_issuer_slugs(kov_root)
        assert slugs == ["mulgi_vallavolikogu", "tallinna_linnavolikogu"]

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            discover_issuer_slugs(tmp_path / "does_not_exist")


class TestBuildIssuerRegistry:
    def test_combines_auto_and_curated(self, tmp_path):
        kov_root = tmp_path / "regulations" / "kov"
        (kov_root / "tallinna_linnavolikogu").mkdir(parents=True)
        (kov_root / "abja_vallavolikogu").mkdir(parents=True)

        csv_path = tmp_path / "map.csv"
        csv_path.write_text(
            "issuer_slug,current_municipality_ehak,mapping_source,mapping_evidence\n"
            "abja_vallavolikogu,0480,haldusreform-2017,RT I 21.06.2017 1\n",
            encoding="utf-8",
        )

        muns = load_municipalities(MIN_MUNICIPALITIES)
        curated = load_curated_map(csv_path)
        slugs = discover_issuer_slugs(kov_root)
        issuers = build_issuer_registry(slugs, muns, curated)

        tallinn = issuers["tallinna_linnavolikogu"]
        assert tallinn["currentMunicipalityCode"] == "0784"
        assert tallinn["mappingSource"] == "auto-match"
        assert tallinn["mappingEvidence"] == ""
        assert tallinn["bodyType"] == "volikogu"

        abja = issuers["abja_vallavolikogu"]
        assert abja["currentMunicipalityCode"] == "0480"
        assert abja["mappingSource"] == "haldusreform-2017"
        assert abja["mappingEvidence"] == "RT I 21.06.2017 1"

    def test_unmapped_issuer_raises(self, tmp_path):
        kov_root = tmp_path / "regulations" / "kov"
        (kov_root / "obscure_vallavolikogu").mkdir(parents=True)

        muns = load_municipalities(MIN_MUNICIPALITIES)
        slugs = discover_issuer_slugs(kov_root)

        with pytest.raises(ValueError, match="obscure_vallavolikogu"):
            build_issuer_registry(slugs, muns, curated={})

    def test_curated_with_unknown_ehak_raises(self, tmp_path):
        kov_root = tmp_path / "regulations" / "kov"
        (kov_root / "abja_vallavolikogu").mkdir(parents=True)

        muns = load_municipalities(MIN_MUNICIPALITIES)
        slugs = discover_issuer_slugs(kov_root)
        curated = {
            "abja_vallavolikogu": {
                "currentMunicipalityCode": "9999",
                "mappingSource": "haldusreform-2017",
                "mappingEvidence": "RT I 21.06.2017 1",
            }
        }
        with pytest.raises(ValueError, match="9999"):
            build_issuer_registry(slugs, muns, curated)

    def test_curated_with_invalid_source_raises(self, tmp_path):
        kov_root = tmp_path / "regulations" / "kov"
        (kov_root / "abja_vallavolikogu").mkdir(parents=True)

        muns = load_municipalities(MIN_MUNICIPALITIES)
        slugs = discover_issuer_slugs(kov_root)
        curated = {
            "abja_vallavolikogu": {
                "currentMunicipalityCode": "0480",
                "mappingSource": "guess",
                "mappingEvidence": "anything",
            }
        }
        with pytest.raises(ValueError, match="mapping_source"):
            build_issuer_registry(slugs, muns, curated)


class TestNormalizeTitle:
    def test_strips_municipality_prefix(self):
        assert normalize_title(
            "Tallinna jäätmehoolduseeskiri", "Tallinna Linnavolikogu"
        ) == "jaatmehoolduseeskiri"

    def test_strips_genitive_form(self):
        # "Mulgi valla" is the genitive of "Mulgi vald"
        assert normalize_title(
            "Mulgi valla hankekord", "Mulgi Vallavolikogu"
        ) == "hankekord"

    def test_no_prefix_match_keeps_full_title(self):
        assert normalize_title(
            "Üldhariduskooli põhimäärus", "Tartu Linnavolikogu"
        ) == "uldhariduskooli pohimaarus"

    def test_lowercases_and_transliterates(self):
        assert normalize_title(
            "Põhimäärus", "Põlva Vallavolikogu"
        ) == "pohimaarus"

    def test_strips_compound_hyphenated_prefix(self):
        # Real-world case: Kohtla-Järve linn / Kohtla Jarve Linnavolikogu
        assert normalize_title(
            "Kohtla-Järve linna jäätmehoolduseeskiri",
            "Kohtla Jarve Linnavolikogu",
        ) == "jaatmehoolduseeskiri"

    def test_strips_compound_space_prefix(self):
        # Same compound issuer, but the title uses spaces instead of hyphen
        assert normalize_title(
            "Kohtla Järve linna jäätmehoolduseeskiri",
            "Kohtla Jarve Linnavolikogu",
        ) == "jaatmehoolduseeskiri"

    def test_strips_compound_genitive_form(self):
        # Põhja-Sakala vald (compound vald) genitive form
        assert normalize_title(
            "Põhja-Sakala valla hankekord",
            "Pohja Sakala Vallavolikogu",
        ) == "hankekord"


class TestMetadataJsonLd:
    def test_new_classes_present(self):
        path = REPO_ROOT / "metadata.jsonld"
        if not path.exists():
            pytest.skip("metadata.jsonld missing")
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        ids = {n.get("@id") for n in doc.get("@graph", [])}
        for required in (
            "estleg:Act", "estleg:Law",
            "estleg:Municipality", "estleg:Issuer", "estleg:KovProvision",
            "estleg:Citation", "estleg:Similarity",
            "estleg:enactedBy", "estleg:enactedByMunicipality",
            "estleg:titleNormalized", "estleg:partOfAct",
            "estleg:ehakCode", "estleg:county", "estleg:bodyType",
            "estleg:currentMunicipality", "estleg:historicalMunicipalityName",
            "estleg:mappingSource", "estleg:mappingEvidence",
        ):
            assert required in ids, f"missing in metadata.jsonld: {required}"

    def test_subclass_triples_present(self):
        path = REPO_ROOT / "metadata.jsonld"
        if not path.exists():
            pytest.skip("metadata.jsonld missing")
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)

        def parents_of(class_id):
            for n in doc["@graph"]:
                if n.get("@id") == class_id:
                    raw = n.get("rdfs:subClassOf", [])
                    if isinstance(raw, dict):
                        raw = [raw]
                    return {p.get("@id") for p in raw}
            return set()

        assert "estleg:Act" in parents_of("estleg:Law")
        assert "estleg:Act" in parents_of("estleg:NationalRegulation")
        assert "estleg:Act" in parents_of("estleg:MunicipalRegulation")
        assert "estleg:Institution" in parents_of("estleg:Issuer")
        assert "estleg:LegalProvision" in parents_of("estleg:KovProvision")
