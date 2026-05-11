"""Tests for scripts/kov_registry.py — issuer/municipality registry helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from kov_registry import (
    HALDUSREFORM_2017_MERGE_DATE,
    auto_match_municipality,
    build_issuer_registry,
    discover_issuer_slugs,
    extract_historical_municipalities,
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

    def test_overrides_take_precedence(self, muns):
        # Regression test for Finding #7's curated-CSV override hook:
        # an override pins a slug to a specific EHAK code regardless
        # of what auto-match would produce. Used for edge cases where
        # the slug root collides with another municipality.
        parts = {"slug": "tallinna_linnavolikogu", "root": "tallinna",
                 "body": "linnavolikogu", "bodyType": "volikogu",
                 "municipalityType": "linn"}
        # Auto-match would have returned 0784. The override forces 0793.
        assert auto_match_municipality(
            parts, muns, overrides={"tallinna_linnavolikogu": "0793"},
        ) == "0793"

    def test_overrides_unknown_ehak_raises(self, muns):
        # An override that points at a code not in the municipalities
        # registry is a configuration bug — surface it loudly.
        parts = {"slug": "tallinna_linnavolikogu", "root": "tallinna",
                 "body": "linnavolikogu", "bodyType": "volikogu",
                 "municipalityType": "linn"}
        with pytest.raises(ValueError, match="9999"):
            auto_match_municipality(
                parts, muns, overrides={"tallinna_linnavolikogu": "9999"},
            )

    def test_no_pairwise_collision_on_real_registry(self):
        # Finding #7 audit: every (root, municipalityType) pair that
        # auto-match resolves on the real 79-municipality registry
        # must resolve to a UNIQUE EHAK code. If a future EHAK
        # update introduces a collision, this test fails before the
        # silent mismatch can land in production.
        muns = load_municipalities(
            REPO_ROOT / "data" / "ehak" / "municipalities.json"
        )
        # Use the live issuers.json so the test follows real corpus
        # growth — adding a slug that collides will fail this test
        # in the same PR that adds the slug.
        issuers = json.loads(
            (REPO_ROOT / "data" / "ehak" / "issuers.json").read_text(
                encoding="utf-8",
            )
        )
        seen: dict[tuple[str, str], str] = {}
        for entry in issuers:
            if entry["mappingSource"] != "auto-match":
                continue  # only the auto-match path is at risk
            parts = parse_issuer_slug(entry["slug"])
            ehak = auto_match_municipality(parts, muns)
            assert ehak is not None, (
                f"{entry['slug']!r} is mappingSource=auto-match in "
                "issuers.json but auto_match_municipality returned "
                "None — registry/CSV drift."
            )
            key = (parts["root"], parts["municipalityType"])
            if key in seen:
                assert seen[key] == ehak, (
                    f"slug-root collision: {parts['root']!r} "
                    f"({parts['municipalityType']}) maps to both "
                    f"{seen[key]!r} and {ehak!r}"
                )
            seen[key] = ehak


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

    def test_unmapped_listed_in_sorted_order(self):
        # Regression test for Finding #12: the error message must be
        # reproducible across runs — sorted alphabetically — so a
        # diff against a previous run is meaningful (not just
        # "different file iteration order"). Pass slugs in reverse-
        # sorted order to verify the sort is on the OUTPUT, not the
        # input.
        muns = load_municipalities(MIN_MUNICIPALITIES)
        # Use slugs whose roots don't match any municipality so all
        # of them land in `unmapped`.
        slugs = ["zzz_vallavolikogu", "aaa_vallavolikogu", "mmm_vallavolikogu"]
        with pytest.raises(ValueError) as exc_info:
            build_issuer_registry(slugs, muns, curated={})
        msg = str(exc_info.value)
        # Slugs must appear in alphabetical order regardless of the
        # input order.
        a_pos = msg.index("aaa_vallavolikogu")
        m_pos = msg.index("mmm_vallavolikogu")
        z_pos = msg.index("zzz_vallavolikogu")
        assert a_pos < m_pos < z_pos


class TestNormalizeTitle:
    """`normalize_title` takes a parts-dict (from `parse_issuer_slug`)
    rather than the display-name string so the alevi-genitive prefix
    rule can be gated on (municipalityType, root) — see the function
    docstring for why (Finding #8 in issue #182).
    """

    def test_strips_municipality_prefix(self):
        assert normalize_title(
            "Tallinna jäätmehoolduseeskiri",
            parse_issuer_slug("tallinna_linnavolikogu"),
        ) == "jaatmehoolduseeskiri"

    def test_strips_genitive_form(self):
        # "Mulgi valla" is the genitive of "Mulgi vald"
        assert normalize_title(
            "Mulgi valla hankekord",
            parse_issuer_slug("mulgi_vallavolikogu"),
        ) == "hankekord"

    def test_no_prefix_match_keeps_full_title(self):
        assert normalize_title(
            "Üldhariduskooli põhimäärus",
            parse_issuer_slug("tartu_linnavolikogu"),
        ) == "uldhariduskooli pohimaarus"

    def test_lowercases_and_transliterates(self):
        assert normalize_title(
            "Põhimäärus",
            parse_issuer_slug("polva_vallavolikogu"),
        ) == "pohimaarus"

    def test_strips_compound_hyphenated_prefix(self):
        # Real-world case: Kohtla-Järve linn / kohtla_jarve_linnavolikogu
        assert normalize_title(
            "Kohtla-Järve linna jäätmehoolduseeskiri",
            parse_issuer_slug("kohtla_jarve_linnavolikogu"),
        ) == "jaatmehoolduseeskiri"

    def test_strips_compound_space_prefix(self):
        # Same compound issuer, but the title uses spaces instead of hyphen
        assert normalize_title(
            "Kohtla Järve linna jäätmehoolduseeskiri",
            parse_issuer_slug("kohtla_jarve_linnavolikogu"),
        ) == "jaatmehoolduseeskiri"

    def test_strips_compound_genitive_form(self):
        # Põhja-Sakala vald (compound vald) genitive form
        assert normalize_title(
            "Põhja-Sakala valla hankekord",
            parse_issuer_slug("pohja_sakala_vallavolikogu"),
        ) == "hankekord"

    def test_strips_alevi_genitive_for_known_alev(self):
        # Vändra alev (historical small-town unit, abolished in 2017
        # haldusreform). The single alevivolikogu issuer in the corpus
        # produces titles like "Vändra alevi <topic>"; the alevi
        # genitive must be stripped.
        assert normalize_title(
            "Vändra alevi terviseprofiil",
            parse_issuer_slug("vandra_alevivolikogu"),
        ) == "terviseprofiil"

    def test_alevi_prefix_not_stripped_for_unrelated_vald(self):
        # Regression test for Finding #8: a vallavolikogu slug whose
        # root happens to produce a title starting with `<root> alevi
        # ...` MUST keep the prefix — only known historical-alev
        # roots ('vandra' today) get the alevi-strip treatment.
        # Without this gate, distinct titles would silently collapse
        # into the same titleNormalized.
        out = normalize_title(
            "Mulgi alevi piirkonna määrus",
            parse_issuer_slug("mulgi_vallavolikogu"),
        )
        # The "mulgi " single-token prefix still strips, but the
        # following "alevi piirkonna määrus" must NOT.
        assert out == "alevi piirkonna maarus"

    def test_alevi_prefix_not_stripped_for_linn(self):
        # Same gate, but for a linnavolikogu — alevi has no semantic
        # meaning for a linn issuer, so the prefix must be left alone.
        out = normalize_title(
            "Tallinna alevi haldusakt",
            parse_issuer_slug("tallinna_linnavolikogu"),
        )
        assert out == "alevi haldusakt"


def _issuer_row(
    slug: str,
    current: str,
    *,
    source: str = "haldusreform-2017",
    evidence: str = "",
    historical_name: str = "",
) -> dict:
    return {
        "slug": slug,
        "displayName": slug.replace("_", " ").title(),
        "bodyType": "volikogu" if slug.endswith("volikogu") else "valitsus",
        "currentMunicipalityCode": current,
        "mappingSource": source,
        "mappingEvidence": evidence,
        "historicalMunicipalityName": historical_name,
    }


class TestExtractHistoricalMunicipalities:
    """`extract_historical_municipalities` derives one
    estleg:HistoricalMunicipality per distinct pre-merger EHAK code from
    the issuer registry's mappingEvidence citations — issue #130."""

    def test_parses_haldusreform_arrow_evidence(self):
        rows = [
            _issuer_row(
                "abja_vallavolikogu", "0480",
                evidence=(
                    "Wikidata: Abja vald (EHAK 0105) -> Mulgi vald "
                    "(EHAK 0480); per RT I 21.06.2017 1"
                ),
                historical_name="Abja",
            ),
        ]
        hist = extract_historical_municipalities(rows)
        assert set(hist) == {"0105"}
        abja = hist["0105"]
        assert abja["formerEhakCode"] == "0105"
        assert abja["formerName"] == "Abja vald"
        assert abja["municipalityType"] == "vald"
        assert abja["succeededByCode"] == "0480"
        assert abja["mergedAt"] == HALDUSREFORM_2017_MERGE_DATE == "2017-10-15"
        assert "EHAK 0105" in abja["mergerEvidence"]
        assert abja["issuerSlugs"] == ["abja_vallavolikogu"]

    def test_parses_linn_unit_type(self):
        rows = [
            _issuer_row(
                "elva_linnavolikogu", "0171",
                evidence=(
                    "Wikidata: Elva linn (EHAK 0170) -> Elva vald "
                    "(EHAK 0171); per RT I 21.06.2017 1"
                ),
            ),
        ]
        hist = extract_historical_municipalities(rows)
        assert hist["0170"]["municipalityType"] == "linn"
        assert hist["0170"]["formerName"] == "Elva linn"

    def test_parses_compound_hyphenated_name(self):
        rows = [
            _issuer_row(
                "kohtla_nomme_vallavolikogu", "0803",
                evidence=(
                    "Wikidata: Kohtla-Nõmme vald (EHAK 0323) -> Toila "
                    "vald (EHAK 0803); per RT I 21.06.2017 1"
                ),
            ),
        ]
        hist = extract_historical_municipalities(rows)
        assert hist["0323"]["formerName"] == "Kohtla-Nõmme vald"

    def test_parses_manual_review_split_prose(self):
        # The manual-review rows use prose evidence that still opens
        # with "<Old> vald (EHAK <old>)" — the predecessor.
        rows = [
            _issuer_row(
                "misso_vallavolikogu", "0698",
                source="manual-review",
                evidence=(
                    "Misso vald (EHAK 0468) was split in the 2017 "
                    "reform: most territory plus the administrative "
                    "center merged into Rõuge vald (0698); southern "
                    "parishes joined Setomaa vald (0732)."
                ),
            ),
        ]
        hist = extract_historical_municipalities(rows)
        assert set(hist) == {"0468"}
        assert hist["0468"]["succeededByCode"] == "0698"
        assert hist["0468"]["mergedAt"] == "2017-10-15"

    def test_dedups_multiple_issuers_for_one_unit(self):
        ev = (
            "Wikidata: Alatskivi vald (EHAK 0126) -> Peipsiääre vald "
            "(EHAK 0586); per RT I 21.06.2017 1"
        )
        rows = [
            _issuer_row("alatskivi_vallavalitsus", "0586", evidence=ev),
            _issuer_row("alatskivi_vallavolikogu", "0586", evidence=ev),
        ]
        hist = extract_historical_municipalities(rows)
        assert set(hist) == {"0126"}
        # Both issuers collected, sorted.
        assert hist["0126"]["issuerSlugs"] == [
            "alatskivi_vallavalitsus", "alatskivi_vallavolikogu",
        ]

    def test_auto_match_issuers_contribute_no_historical_node(self):
        # auto-match issuers map to a still-current municipality and
        # have empty mappingEvidence — no historical node.
        rows = [
            _issuer_row(
                "tallinna_linnavolikogu", "0784", source="auto-match",
                evidence="",
            ),
        ]
        assert extract_historical_municipalities(rows) == {}

    def test_unparseable_evidence_is_skipped(self):
        rows = [
            _issuer_row(
                "weird_vallavolikogu", "0480",
                evidence="some note without a parseable predecessor EHAK ref",
            ),
        ]
        assert extract_historical_municipalities(rows) == {}

    def test_conflicting_successors_raise(self):
        # Same historical code reached via two issuers but with
        # different successors → data bug, surface it.
        rows = [
            _issuer_row(
                "x_vallavalitsus", "0480",
                evidence="Foo vald (EHAK 0105) -> Bar vald (EHAK 0480)",
            ),
            _issuer_row(
                "x_vallavolikogu", "0481",
                evidence="Foo vald (EHAK 0105) -> Baz vald (EHAK 0481)",
            ),
        ]
        with pytest.raises(ValueError, match="conflicting successors"):
            extract_historical_municipalities(rows)

    def test_successor_not_in_municipalities_raises_when_checked(self):
        rows = [
            _issuer_row(
                "x_vallavolikogu", "9999",
                evidence="Foo vald (EHAK 0105) -> Ghost vald (EHAK 9999)",
            ),
        ]
        muns = load_municipalities(MIN_MUNICIPALITIES)
        with pytest.raises(ValueError, match="9999"):
            extract_historical_municipalities(rows, muns)
        # Without the municipalities arg the check is skipped.
        assert "0105" in extract_historical_municipalities(rows)

    def test_self_succession_evidence_is_skipped(self):
        # If the first (EHAK NNNN) match equals the current code, it is
        # not a predecessor — don't emit a self-succession edge.
        rows = [
            _issuer_row(
                "x_vallavolikogu", "0480",
                evidence="Mulgi vald (EHAK 0480) absorbed several units",
            ),
        ]
        assert extract_historical_municipalities(rows) == {}

    def test_real_registry_yields_expected_shape(self):
        # Drive the live data/ehak/issuers.json: every issuer that is
        # NOT mappingSource=auto-match must contribute to exactly one
        # historical node, and every node must have a 4-digit code, a
        # non-empty name ending in " vald" or " linn", and a successor
        # that exists in the current municipalities registry.
        issuers = json.loads(
            (REPO_ROOT / "data" / "ehak" / "issuers.json").read_text(
                encoding="utf-8",
            )
        )
        muns = load_municipalities(
            REPO_ROOT / "data" / "ehak" / "municipalities.json"
        )
        hist = extract_historical_municipalities(issuers, muns)
        non_auto = [r for r in issuers if r["mappingSource"] != "auto-match"]
        assert len(hist) >= 130, (
            f"expected ~150 historical municipalities, got {len(hist)}"
        )
        # Every non-auto issuer is accounted for in some node's slug list.
        accounted = {s for h in hist.values() for s in h["issuerSlugs"]}
        assert {r["slug"] for r in non_auto} == accounted
        for code, h in hist.items():
            assert len(code) == 4 and code.isdigit()
            assert h["formerName"].endswith((" vald", " linn"))
            assert h["municipalityType"] in {"vald", "linn"}
            assert h["succeededByCode"] in muns
            assert h["succeededByCode"] != code
            # All real entries are 2017-haldusreform (or manual-review
            # 2017 splits), so every node carries the merger date.
            assert h["mergedAt"] == "2017-10-15"


class TestMetadataJsonLd:
    def test_new_classes_present(self):
        path = REPO_ROOT / "metadata.jsonld"
        if not path.exists():
            pytest.fail("metadata.jsonld missing")
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
            # Layer 2a additions:
            "estleg:citationTarget", "estleg:citationDetail",
            "estleg:citationText",
            "estleg:issuedUnder", "estleg:implementsCitation",
            "estleg:implementedBy", "estleg:implementedByCount",
            "estleg:enforcedAtLevel",
        ):
            assert required in ids, f"missing in metadata.jsonld: {required}"

    def test_subclass_triples_present(self):
        path = REPO_ROOT / "metadata.jsonld"
        if not path.exists():
            pytest.fail("metadata.jsonld missing")
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
