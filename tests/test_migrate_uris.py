"""Tests for migrate_uris.py — Tasks 1-4 plus review-finding regression tests."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import migrate_uris
from migrate_uris import (
    NEW_IRI_FORMAT_RE,
    PAR_NO_UNDERSCORE_RE,
    RENAME_FAMILY_AMENDMENT,
    RENAME_FAMILY_SANCTION,
    _already_migrated_per_state,
    _amendment_base_slug_in_iri,
    _atomic_write_text,
    _shorten_amendment_iri,
    _stem_from_amends_target,
    apply_renames_to_file,
    assign_abbreviations,
    auto_derive_abbreviation,
    build_amendment_prefix_map,
    build_rename_map,
    compute_corpus_hash,
    compute_registry_hash,
    get_all_scannable_files,
    load_migration_state,
    save_migration_state,
    verify_migration,
)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class TestAutoDerive:
    def test_multiword_with_stop_words(self):
        result = auto_derive_abbreviation(
            "Alkoholi-, tubaka-, kütuse- ja elektriaktsiisi seadus",
            "alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus",
        )
        assert result == "ATKE"

    def test_treaty_appends_year(self):
        result = auto_derive_abbreviation(
            "1974. aasta rahvusvahelise konventsiooni inimelude ohutusest merel protokoll",
            "1974_aasta_rahvusvahelise_konventsiooni_inimelude_ohutusest_merel_protokoll",
        )
        assert result.endswith("1974")
        assert len(result) <= 12

    def test_short_result_fallback_to_slug(self):
        result = auto_derive_abbreviation("Seadus", "seadus")
        assert len(result) >= 3
        assert len(result) <= 12

    def test_cap_at_12_chars(self):
        result = auto_derive_abbreviation(
            "Väga pikk nimetus mille sisu on keeruline ja raske kirjeldada",
            "vaga_pikk_nimetus_mille_sisu_on_keeruline_raske_kirjeldada",
        )
        assert len(result) <= 12

    def test_transliteration(self):
        result = auto_derive_abbreviation(
            "Öökülma tõrje ühingu põhikiri",
            "ookulma_torje_uhingu_pohikiri",
        )
        assert result == "OTUP"

    def test_single_word_non_stop(self):
        result = auto_derive_abbreviation("Arhiiviseadus", "arhiiviseadus")
        assert len(result) >= 3


class TestUnderscoreFix:
    def test_par_without_underscore(self):
        result = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", "VOS_Par271")
        assert result == "VOS_Par_271"

    def test_par_with_underscore_unchanged(self):
        result = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", "VOS_Par_271")
        assert result == "VOS_Par_271"

    def test_multiple_occurrences(self):
        result = PAR_NO_UNDERSCORE_RE.sub(r"_Par_\1", "VOS_Par1 VOS_Par2")
        assert "Par_1" in result
        assert "Par_2" in result


class TestBuildRenameMap:
    @pytest.fixture
    def sample_registry(self):
        return {
            "perekonnaseadus": {
                "abbrev": "PKS",
                "source": "rt_api",
                "title": "Perekonnaseadus",
                "old_prefix": "PKS",
            },
            "alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus": {
                "abbrev": "ATKE",
                "source": "auto",
                "title": "Alkoholi-, tubaka-, kütuse- ja elektriaktsiisi seadus",
                "old_prefix": "Alkoholi_tubaka_ktuse_ja",
            },
            "volaigusseadus": {
                "abbrev": "VOS",
                "source": "rt_api",
                "title": "Võlaõigusseadus",
                "old_prefix": "VOS",
            },
        }

    def test_unchanged_prefix_not_in_map(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:PKS_Par_1"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert "estleg:PKS_Par_1" not in rename_map

    def test_long_prefix_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:Alkoholi_tubaka_ktuse_ja_Map_2026"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map["estleg:Alkoholi_tubaka_ktuse_ja_Map_2026"] == "estleg:ATKE_Map_2026"

    def test_cluster_prefix_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:Cluster_Alkoholi_tubaka_ktuse_ja_Aktsiis"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map["estleg:Cluster_Alkoholi_tubaka_ktuse_ja_Aktsiis"] == "estleg:Cluster_ATKE_Aktsiis"

    def test_legal_provision_slug_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:LegalProvision_alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map[
            "estleg:LegalProvision_alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus"
        ] == "estleg:LegalProvision_ATKE"

    def test_legal_provision_osa_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:LegalProvision_volaigusseadus_osa11"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map[
            "estleg:LegalProvision_volaigusseadus_osa11"
        ] == "estleg:LegalProvision_VOS_osa11"

    def test_legal_concept_renamed(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:LegalConcept_alkoholiseadus"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map["estleg:LegalConcept_alkoholiseadus"] == "estleg:Concept_alkoholiseadus"

    def test_vos_underscore_fix(self, sample_registry, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"@id": "estleg:VOS_Par271"}')
        rename_map = build_rename_map(sample_registry, scan_paths=[f])
        assert rename_map["estleg:VOS_Par271"] == "estleg:VOS_Par_271"


class TestApplyRenames:
    def test_does_not_replace_inside_longer_iri_token(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('"estleg:OLD" "estleg:OLD_SUFFIX"', encoding="utf-8")

        count = apply_renames_to_file(f, [("estleg:OLD", "estleg:NEW")])

        assert count == 1
        assert f.read_text(encoding="utf-8") == '"estleg:NEW" "estleg:OLD_SUFFIX"'

    def test_python_files_are_not_rewritten_by_default(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text('IRI = "estleg:OLD"', encoding="utf-8")

        count = apply_renames_to_file(f, [("estleg:OLD", "estleg:NEW")])

        assert count == 0
        assert "estleg:OLD" in f.read_text(encoding="utf-8")

    def test_python_files_require_explicit_flag(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text('IRI = "estleg:OLD"', encoding="utf-8")

        count = apply_renames_to_file(
            f,
            [("estleg:OLD", "estleg:NEW")],
            rewrite_python=True,
        )

        assert count == 1
        assert "estleg:NEW" in f.read_text(encoding="utf-8")


# ── Issue #192 — Amendment_/AmendmentChain_/AmendmentLink_ shortening ─────────


_AMEND_REGISTRY = {
    "karistusseadustik": {
        "abbrev": "KARIST_2",
        "source": "auto",
        "title": "Karistusseadustik",
        "old_prefix": "Karistusseadustik",
    },
    "karistusseadustiku_rakendamise_seadus": {
        "abbrev": "KarSRS",
        "source": "rt_api",
        "title": "Karistusseadustiku rakendamise seadus",
        "old_prefix": "KarSRS",
    },
    "abieluvararegistri_seadus": {
        "abbrev": "AVRS",
        "source": "rt_api",
        "title": "Abieluvararegistri seadus",
        "old_prefix": "AVRS",
    },
}


def _seed_amendments_dir(amendments_dir: Path) -> None:
    """Write a small amendments/ tree covering a law, a multipart-ish slug, and a
    regulation (whose base_slug is *absent* from the registry — so it must be
    resolved via the chain's estleg:amends Reg_<id> target)."""
    amendments_dir.mkdir(parents=True, exist_ok=True)
    (amendments_dir / "amendments_karistusseadustik.json").write_text(
        json.dumps({
            "@graph": [
                {"@id": "estleg:AmendmentChain_karistusseadustik",
                 "@type": ["owl:Ontology"]},
                {"@id": "estleg:Amendment_karistusseadustik_1",
                 "estleg:amends": {"@id": "estleg:KARIST_2_Osa1_1_87"}},
                {"@id": "estleg:Amendment_karistusseadustik_2",
                 "estleg:amends": {"@id": "estleg:KARIST_2_Osa1_1_87"}},
            ]
        }), encoding="utf-8")
    (amendments_dir / "amendments_aadressiandmete_susteem_t1052132.json").write_text(
        json.dumps({
            "@graph": [
                {"@id": "estleg:AmendmentChain_aadressiandmete_susteem_t1052132",
                 "@type": ["owl:Ontology"]},
                {"@id": "estleg:Amendment_aadressiandmete_susteem_t1052132_1",
                 "estleg:amends": {"@id": "estleg:Reg_1052132_Map_2026"}},
                {"@id": "estleg:Amendment_aadressiandmete_susteem_t1052132_10",
                 "estleg:amends": {"@id": "estleg:Reg_1052132_Map_2026"}},
            ]
        }), encoding="utf-8")
    (amendments_dir / "amendments_abieluvararegistri_seadus.json").write_text(
        json.dumps({
            "@graph": [
                {"@id": "estleg:AmendmentChain_abieluvararegistri_seadus",
                 "@type": ["owl:Ontology"]},
                {"@id": "estleg:AmendmentLink_Draft_JDM16_0008_abieluvararegistri_seadus",
                 "estleg:amends": {"@id": "estleg:AVRS_Map_2026"},
                 "estleg:amendingDraft": {"@id": "estleg:Draft_JDM16_0008"}},
            ]
        }), encoding="utf-8")


class TestStemFromAmendsTarget:
    @pytest.mark.parametrize(
        "amends_iri, expected",
        [
            ("estleg:Reg_1052132_Map_2026", "Reg_1052132"),
            ("estleg:AVRS_Map_2026", "AVRS"),
            ("estleg:KARIST_2_Osa2_88_451", "KARIST_2"),
            ("estleg:STS2004_2006_2_Map_2026", "STS2004_2006_2"),
            ("estleg:VOS_Par_271", "VOS"),
            # Not an estleg: IRI / nothing compact-looking → None.
            ("http://example.org/foo", None),
            ("estleg:_Map_2026", None),
        ],
    )
    def test_recovers_compact_stem(self, amends_iri, expected):
        assert _stem_from_amends_target(amends_iri) == expected


class TestBuildAmendmentPrefixMap:
    def test_registry_then_amends_target_resolution(self, tmp_path: Path):
        amd = tmp_path / "amendments"
        _seed_amendments_dir(amd)
        prefix_map, skipped = build_amendment_prefix_map(
            _AMEND_REGISTRY, amendments_dir=amd
        )
        assert skipped == {}
        # Registry-resolved law.
        assert prefix_map["karistusseadustik"] == "KARIST_2"
        assert prefix_map["abieluvararegistri_seadus"] == "AVRS"
        # Regulation absent from the registry — resolved via the Reg_<id> stem
        # recovered from the chain's estleg:amends target (NOT the slug's
        # _t<id> suffix, which can disagree with the act's numeric id).
        assert prefix_map["aadressiandmete_susteem_t1052132"] == "Reg_1052132"

    def test_skips_when_no_shorter_stem_available(self, tmp_path: Path):
        amd = tmp_path / "amendments"
        amd.mkdir()
        # amends target is itself longer than the slug → can't improve.
        (amd / "amendments_xyz.json").write_text(
            json.dumps({"@graph": [
                {"@id": "estleg:AmendmentChain_xyz", "@type": ["owl:Ontology"]},
                {"@id": "estleg:Amendment_xyz_1",
                 "estleg:amends": {
                     "@id": "estleg:a_very_long_slug_that_is_longer_Map_2026"}},
            ]}), encoding="utf-8")
        prefix_map, skipped = build_amendment_prefix_map({}, amendments_dir=amd)
        assert "xyz" not in prefix_map
        assert "xyz" in skipped
        assert "no shorter" in skipped["xyz"]

    def test_missing_amendments_dir_returns_empty(self, tmp_path: Path):
        prefix_map, skipped = build_amendment_prefix_map(
            {}, amendments_dir=tmp_path / "nope"
        )
        assert prefix_map == {}
        assert skipped == {}


class TestShortenAmendmentIri:
    @pytest.fixture
    def shortener(self, tmp_path: Path):
        amd = tmp_path / "amendments"
        _seed_amendments_dir(amd)
        prefix_map, _ = build_amendment_prefix_map(_AMEND_REGISTRY, amendments_dir=amd)
        by_len = sorted(prefix_map.keys(), key=len, reverse=True)
        known = set(prefix_map.values())

        def _go(iri: str, unmatched=None):
            return _shorten_amendment_iri(
                iri, by_len, prefix_map, unmatched=unmatched, known_prefixes=known
            )

        return _go

    @pytest.mark.parametrize(
        "old_iri, new_iri",
        [
            # Amendment_<base_slug>_<n> (legacy decimal counter).
            ("estleg:Amendment_karistusseadustik_1", "estleg:Amendment_KARIST_2_1"),
            ("estleg:Amendment_karistusseadustik_528", "estleg:Amendment_KARIST_2_528"),
            # Disambiguated counter.
            ("estleg:Amendment_karistusseadustik_528_2",
             "estleg:Amendment_KARIST_2_528_2"),
            # md5-prefix suffix (post-#173 generator shape).
            ("estleg:Amendment_karistusseadustik_a1b2c3d4e5",
             "estleg:Amendment_KARIST_2_a1b2c3d4e5"),
            # Regulation: base_slug → Reg_<id> recovered from amends target.
            ("estleg:Amendment_aadressiandmete_susteem_t1052132_1",
             "estleg:Amendment_Reg_1052132_1"),
            ("estleg:Amendment_aadressiandmete_susteem_t1052132_10",
             "estleg:Amendment_Reg_1052132_10"),
            # Chain.
            ("estleg:AmendmentChain_karistusseadustik", "estleg:AmendmentChain_KARIST_2"),
            ("estleg:AmendmentChain_aadressiandmete_susteem_t1052132",
             "estleg:AmendmentChain_Reg_1052132"),
            # Link: <draft_id> verbatim, base_slug → abbrev.
            ("estleg:AmendmentLink_Draft_JDM16_0008_abieluvararegistri_seadus",
             "estleg:AmendmentLink_Draft_JDM16_0008_AVRS"),
        ],
    )
    def test_volaigusseadus_style_substitution(self, shortener, old_iri, new_iri):
        assert shortener(old_iri) == new_iri

    def test_unknown_base_slug_left_unchanged_and_recorded(self, shortener):
        unmatched: set[str] = set()
        # 'volaigusseadus' is the registry key form; 'volaoigusseadus' (the
        # corpus's amendments-file form) is *not* a known base_slug here.
        result = shortener("estleg:Amendment_volaoigusseadus_3", unmatched=unmatched)
        assert result is None
        assert "estleg:Amendment_volaoigusseadus_3" in unmatched

    def test_already_migrated_iri_not_recorded(self, shortener):
        """A second pass over an already-shortened corpus must stay quiet."""
        unmatched: set[str] = set()
        # KARIST_2 is a known compact prefix — the IRI is already migrated.
        assert shortener("estleg:Amendment_KARIST_2_1", unmatched=unmatched) is None
        assert shortener("estleg:Amendment_Reg_1052132_1", unmatched=unmatched) is None
        assert shortener(
            "estleg:AmendmentLink_Draft_JDM16_0008_AVRS", unmatched=unmatched
        ) is None
        assert unmatched == set()

    def test_unicode_truncated_fragment_ignored(self, shortener):
        """`ESTLEG_RE` truncates `..._ÕÕS` to `..._`; that fragment is not an
        anomaly to flag."""
        unmatched: set[str] = set()
        assert shortener(
            "estleg:AmendmentLink_Draft_HTM18_0730_", unmatched=unmatched
        ) is None
        assert shortener("estleg:Amendment_", unmatched=unmatched) is None
        assert unmatched == set()

    def test_amendment_with_no_numeric_tail_left_alone(self, shortener):
        # `Amendment_<base_slug>` with no counter is not a shape the generator
        # produces — leave it (and don't flag it).
        unmatched: set[str] = set()
        assert shortener("estleg:Amendment_karistusseadustik", unmatched=unmatched) is None


class TestAmendmentInBuildRenameMap:
    def test_build_rename_map_rewrites_definitions_and_references(self, tmp_path: Path):
        """The same OLD→NEW pairs must cover both the @id definitions in
        amendments/ AND the estleg:amendedBy references in peep files."""
        amd = tmp_path / "amendments"
        _seed_amendments_dir(amd)
        # A peep file that references the (still-long) Amendment ids.
        peep = tmp_path / "aadressiandmete_susteem_t1052132_peep.json"
        peep.write_text(json.dumps({"@graph": [
            {"@id": "estleg:Reg_1052132_Map_2026", "estleg:amendedBy": [
                {"@id": "estleg:Amendment_aadressiandmete_susteem_t1052132_1"},
                {"@id": "estleg:Amendment_aadressiandmete_susteem_t1052132_10"},
            ]},
        ]}), encoding="utf-8")
        scan = [peep, *sorted(amd.glob("*.json"))]
        skipped: dict[str, str] = {}
        rename_map = build_rename_map(
            _AMEND_REGISTRY,
            scan_paths=scan,
            amendments_dir=amd,
            skipped_amendments=skipped,
            families={RENAME_FAMILY_AMENDMENT, RENAME_FAMILY_SANCTION},
        )
        assert skipped == {}
        # @id definitions:
        assert rename_map["estleg:AmendmentChain_aadressiandmete_susteem_t1052132"] == \
            "estleg:AmendmentChain_Reg_1052132"
        assert rename_map["estleg:Amendment_aadressiandmete_susteem_t1052132_1"] == \
            "estleg:Amendment_Reg_1052132_1"
        assert rename_map["estleg:Amendment_aadressiandmete_susteem_t1052132_10"] == \
            "estleg:Amendment_Reg_1052132_10"
        # The references in the peep file are the *same* old IRIs — apply will
        # rewrite them via the same map entries.
        assert "estleg:Amendment_karistusseadustik_1" in rename_map
        assert (
            rename_map["estleg:AmendmentLink_Draft_JDM16_0008_abieluvararegistri_seadus"]
            == "estleg:AmendmentLink_Draft_JDM16_0008_AVRS"
        )

    def test_legacy_family_not_touched_in_amendment_layer(self, tmp_path: Path):
        """With families={amendment, sanction}, a legacy <prefix>_Par_ IRI is
        left alone (the legacy remap is already applied)."""
        amd = tmp_path / "amendments"
        amd.mkdir()
        f = tmp_path / "x.json"
        f.write_text(
            '{"@id": "estleg:Alkoholi_tubaka_ktuse_ja_Map_2026"}', encoding="utf-8"
        )
        rename_map = build_rename_map(
            {"alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus": {
                "abbrev": "ATKE", "source": "auto",
                "title": "x", "old_prefix": "Alkoholi_tubaka_ktuse_ja"}},
            scan_paths=[f],
            amendments_dir=amd,
            families={RENAME_FAMILY_AMENDMENT, RENAME_FAMILY_SANCTION},
        )
        assert rename_map == {}
        # …but with the legacy family included it *is* renamed.
        rename_map_legacy = build_rename_map(
            {"alkoholi_tubaka_kutuse_ja_elektriaktsiisi_seadus": {
                "abbrev": "ATKE", "source": "auto",
                "title": "x", "old_prefix": "Alkoholi_tubaka_ktuse_ja"}},
            scan_paths=[f],
            amendments_dir=amd,
            families=None,
        )
        assert rename_map_legacy["estleg:Alkoholi_tubaka_ktuse_ja_Map_2026"] == \
            "estleg:ATKE_Map_2026"

    def test_sanctions_long_slug_iri_shortened(self, tmp_path: Path):
        amd = tmp_path / "amendments"
        amd.mkdir()
        f = tmp_path / "s.json"
        f.write_text(
            '{"@id": "estleg:Sanctions_punase_risti_nimetuse_ja_embleemi_seadus_Map"}'
            ' {"@id": "estleg:Sanction_punase_risti_nimetuse_ja_embleemi_seadus_Par_13_fine"}'
            ' {"@id": "estleg:Sanction_PRS_Par_14_fine"}',
            encoding="utf-8",
        )
        rename_map = build_rename_map(
            {"punase_risti_nimetuse_ja_embleemi_seadus": {
                "abbrev": "PRS", "source": "rt_api",
                "title": "x", "old_prefix": "PRS"}},
            scan_paths=[f],
            amendments_dir=amd,
            families={RENAME_FAMILY_SANCTION},
        )
        assert rename_map[
            "estleg:Sanctions_punase_risti_nimetuse_ja_embleemi_seadus_Map"
        ] == "estleg:Sanctions_PRS_Map"
        assert rename_map[
            "estleg:Sanction_punase_risti_nimetuse_ja_embleemi_seadus_Par_13_fine"
        ] == "estleg:Sanction_PRS_Par_13_fine"
        # Already-compact one is not touched.
        assert "estleg:Sanction_PRS_Par_14_fine" not in rename_map


class TestVerifyMigrationAmendmentCheck:
    def test_rejects_amendment_iri_with_long_base_slug(
        self, isolated_paths: Path
    ) -> None:
        """If a long-slug Amendment IRI escapes the migration, verify must FAIL."""
        krr = isolated_paths / "krr_outputs"
        amd = krr / "amendments"
        amd.mkdir(parents=True)
        # Registry maps the slug → KARIST_2, so the long form is "shortenable".
        (isolated_paths / "data" / "law_abbreviations.json").write_text(
            json.dumps(_AMEND_REGISTRY), encoding="utf-8"
        )
        (amd / "amendments_karistusseadustik.json").write_text(
            json.dumps({"@graph": [
                {"@id": "estleg:AmendmentChain_karistusseadustik",
                 "@type": ["owl:Ontology"]},
                {"@id": "estleg:Amendment_karistusseadustik_1",
                 "estleg:amends": {"@id": "estleg:KARIST_2_Osa1_1_87"}},
            ]}), encoding="utf-8")
        # A peep file still carrying the long ref (the migration "missed" it).
        (krr / "a.json").write_text(
            '{"@id": "estleg:X", "estleg:amendedBy": '
            '[{"@id": "estleg:Amendment_karistusseadustik_1"}]}',
            encoding="utf-8",
        )
        ok, issues = verify_migration({})  # empty rename map → only the slug check fires
        assert ok is False
        joined = "\n".join(issues)
        assert "still embed a shortenable base slug" in joined
        assert "estleg:Amendment_karistusseadustik_1" in joined

    def test_passes_when_all_amendment_iris_are_compact(
        self, isolated_paths: Path
    ) -> None:
        krr = isolated_paths / "krr_outputs"
        amd = krr / "amendments"
        amd.mkdir(parents=True)
        (isolated_paths / "data" / "law_abbreviations.json").write_text(
            json.dumps(_AMEND_REGISTRY), encoding="utf-8"
        )
        (amd / "amendments_karistusseadustik.json").write_text(
            json.dumps({"@graph": [
                {"@id": "estleg:AmendmentChain_KARIST_2", "@type": ["owl:Ontology"]},
                {"@id": "estleg:Amendment_KARIST_2_1",
                 "estleg:amends": {"@id": "estleg:KARIST_2_Osa1_1_87"}},
            ]}), encoding="utf-8")
        (krr / "a.json").write_text(
            '{"@id": "estleg:X", "estleg:amendedBy": '
            '[{"@id": "estleg:Amendment_KARIST_2_1"}]}',
            encoding="utf-8",
        )
        ok, issues = verify_migration({})
        assert ok is True
        # The longest-@id INFO line is advisory, not a failure.
        assert all(i.startswith("INFO:") for i in issues) or issues == []

    def test_amendment_base_slug_in_iri_helper(self) -> None:
        by_len = ["karistusseadustiku_rakendamise_seadus", "karistusseadustik",
                  "aadressiandmete_susteem_t1052132", "abieluvararegistri_seadus"]
        assert _amendment_base_slug_in_iri(
            "estleg:Amendment_karistusseadustik_5", by_len
        ) == "karistusseadustik"
        assert _amendment_base_slug_in_iri(
            "estleg:AmendmentChain_karistusseadustik", by_len
        ) == "karistusseadustik"
        assert _amendment_base_slug_in_iri(
            "estleg:AmendmentLink_Draft_X_abieluvararegistri_seadus", by_len
        ) == "abieluvararegistri_seadus"
        # Already-compact / unrelated IRIs → None.
        assert _amendment_base_slug_in_iri("estleg:Amendment_KARIST_2_5", by_len) is None
        assert _amendment_base_slug_in_iri("estleg:Amendment_", by_len) is None
        assert _amendment_base_slug_in_iri("estleg:KARIST_2_Par_1", by_len) is None


# ── Helpers shared by the review-finding regression tests ────────────────────


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every module-level path to ``tmp_path``.

    Each test gets a fresh project root, krr/scripts/data subtrees, and a
    redirected ``REGISTRY_PATH`` / ``REPORT_PATH`` / ``MIGRATION_STATE_PATH``.
    Without this isolation we'd risk overwriting the real registry on the
    developer machine when exercising the apply_cmd path.
    """
    project_root = tmp_path / "project"
    krr_dir = project_root / "krr_outputs"
    data_dir = project_root / "data"
    scripts_dir = project_root / "scripts"
    krr_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)

    monkeypatch.setattr(migrate_uris, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(migrate_uris, "KRR_DIR", krr_dir)
    # ``AMENDMENTS_DIR`` is import-time-bound off the original ``KRR_DIR`` —
    # redirect it too so ``build_amendment_prefix_map`` (issue #192) reads the
    # fixture tree, not the real corpus.
    monkeypatch.setattr(migrate_uris, "AMENDMENTS_DIR", krr_dir / "amendments")
    monkeypatch.setattr(migrate_uris, "DATA_DIR", data_dir)
    monkeypatch.setattr(migrate_uris, "SCRIPTS_DIR", scripts_dir)
    monkeypatch.setattr(
        migrate_uris, "REGISTRY_PATH", data_dir / "law_abbreviations.json"
    )
    monkeypatch.setattr(
        migrate_uris, "REPORT_PATH", data_dir / "uri_migration_report.json"
    )
    monkeypatch.setattr(
        migrate_uris, "MIGRATION_STATE_PATH", data_dir / "migration_state.json"
    )
    return project_root


def _write_registry(path: Path, registry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, ensure_ascii=False))


def _seed_corpus(krr_dir: Path, content_by_relpath: dict[str, str]) -> list[Path]:
    files: list[Path] = []
    for rel, content in content_by_relpath.items():
        fp = krr_dir / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        files.append(fp)
    return files


# ── Finding 3 — atomic writes ────────────────────────────────────────────────


class TestAtomicWrite:
    """Atomic-write contract: a crash mid-write must leave the original intact."""

    def test_writes_payload(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        _atomic_write_text(target, "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_replaces_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        target.write_text("original", encoding="utf-8")
        _atomic_write_text(target, "replaced")
        assert target.read_text(encoding="utf-8") == "replaced"

    def test_simulated_crash_leaves_original_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If ``os.replace`` blows up, the destination must not be touched.

        We simulate a power loss between ``write`` and ``rename`` by patching
        ``os.replace`` to raise. The temp file should be cleaned up and the
        original payload must still be readable.
        """
        target = tmp_path / "out.txt"
        target.write_text("original-payload", encoding="utf-8")

        original_replace = os.replace

        def _boom(src: str, dst: str) -> None:  # pragma: no cover - branches
            raise OSError("simulated crash before atomic rename")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError, match="simulated crash"):
            _atomic_write_text(target, "new-payload-that-must-not-land")
        # Original content is intact — partial write never visible at ``target``.
        assert target.read_text(encoding="utf-8") == "original-payload"
        # And we cleaned up after ourselves: no stray tempfiles in the dir.
        assert not any(p.name.startswith(".out.txt.") for p in tmp_path.iterdir())

        # Restore so the fixture teardown doesn't surprise other tests.
        monkeypatch.setattr(os, "replace", original_replace)

    def test_apply_renames_to_file_is_atomic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``apply_renames_to_file`` must use the atomic helper."""
        f = tmp_path / "test.json"
        f.write_text('"estleg:OLD"', encoding="utf-8")

        def _boom(src: str, dst: str) -> None:
            raise OSError("simulated crash")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            apply_renames_to_file(f, [("estleg:OLD", "estleg:NEW")])

        assert f.read_text(encoding="utf-8") == '"estleg:OLD"'


# ── Finding 5 — collision priority rt_api > existing > auto ──────────────────


class TestCollisionPriority:
    """Source-tier priority: rt_api always keeps the bare abbrev."""

    def test_rt_api_wins_against_alphabetically_earlier_auto(self) -> None:
        """Alphabetically earlier auto law must yield to rt_api with same letters.

        Slug ``aaaa_avalik_linna_korraldus`` sorts before
        ``zzzz_alkoholi_korraldus``. The auto-derive on the first title
        produces ``ALK`` (Avalik / Linna / Korraldus, capitalised first
        letters). Under the old single-pass loop ``ALK`` would have landed
        on the auto law first, forcing rt_api ``ALK`` to ``ALK_2``. The
        three-pass algorithm reserves rt_api first, so the auto law takes
        ``ALK_2`` instead.
        """
        peep = {
            # alphabetically first; auto-derived "Avalik Linna Korraldus" → ALK
            "aaaa_avalik_linna_korraldus": {
                "title": "Avalik Linna Korraldus",
                "prefix": "aaaa_avalik_linna_korraldus_extra",
            },
            # alphabetically last; rt_api claims "ALK"
            "zzzz_alkoholi_korraldus": {
                "title": "Alkoholi Korraldus",
                "prefix": "zzzz_alkoholi_korraldus_extra_pf",
            },
        }
        rt = {"zzzz_alkoholi_korraldus": "ALK"}
        registry, stats = assign_abbreviations(peep, rt)

        # rt_api keeps the bare abbreviation regardless of alphabetic order.
        assert registry["zzzz_alkoholi_korraldus"]["abbrev"] == "ALK"
        assert registry["zzzz_alkoholi_korraldus"]["source"] == "rt_api"
        # The auto law is suffixed because rt_api reserved the bare form first.
        assert registry["aaaa_avalik_linna_korraldus"]["abbrev"] == "ALK_2"
        assert registry["aaaa_avalik_linna_korraldus"]["source"] == "auto"
        assert stats == {"rt_api": 1, "existing": 0, "auto": 1}

    def test_existing_yields_to_rt_api_too(self) -> None:
        """``existing`` (short prefix) must also yield when an rt_api shares it."""
        peep = {
            "aaa_short_pref_law": {
                "title": "First Law",
                "prefix": "FOO",  # short → "existing" tier
            },
            "zzz_other_law": {
                "title": "Other Law",
                "prefix": "zzz_other_law_long_prefix",
            },
        }
        rt = {"zzz_other_law": "FOO"}
        registry, _ = assign_abbreviations(peep, rt)
        assert registry["zzz_other_law"]["abbrev"] == "FOO"
        assert registry["zzz_other_law"]["source"] == "rt_api"
        assert registry["aaa_short_pref_law"]["abbrev"] == "FOO_2"
        assert registry["aaa_short_pref_law"]["source"] == "existing"

    def test_auto_collides_with_other_auto_uses_suffix(self) -> None:
        """Two auto laws producing the same abbrev — second one is suffixed."""
        peep = {
            "alpha_konkreetne_asi_too": {
                "title": "Konkreetne Asi Töö",  # → KAT
                "prefix": "alpha_konkreetne_asi_too_extra",
            },
            "beta_konkreetne_avalik_too": {
                "title": "Konkreetne Avalik Töö",  # → KAT (same)
                "prefix": "beta_konkreetne_avalik_too_extra",
            },
        }
        registry, stats = assign_abbreviations(peep, rt_abbrevs={})
        abbrevs = sorted(e["abbrev"] for e in registry.values())
        assert abbrevs == ["KAT", "KAT_2"]
        assert stats["auto"] == 2

    def test_three_tier_priority_chain(self) -> None:
        """Full chain: rt_api claims first, existing next, auto last.

        Three laws all have candidate abbrev ``ABC``. The rt_api law keeps
        the bare form; the existing law gets ``ABC_2``; the auto law gets
        ``ABC_3``. This pins the deterministic priority order.
        """
        peep = {
            "aa_existing": {  # tier: existing (short prefix == "ABC")
                "title": "X",
                "prefix": "ABC",
            },
            "bb_auto": {  # tier: auto via title "Avalik Bee Cee" → ABC
                "title": "Avalik Bee Cee",
                "prefix": "bb_auto_long_prefix_overflow",
            },
            "cc_rt_api": {  # tier: rt_api with explicit ABC mapping
                "title": "Y",
                "prefix": "cc_rt_api_long_prefix_overflow",
            },
        }
        rt = {"cc_rt_api": "ABC"}
        registry, _ = assign_abbreviations(peep, rt)
        # rt_api: bare ABC.
        assert registry["cc_rt_api"]["abbrev"] == "ABC"
        # existing: first to collide → ABC_2.
        assert registry["aa_existing"]["abbrev"] == "ABC_2"
        # auto: comes last → ABC_3.
        assert registry["bb_auto"]["abbrev"] == "ABC_3"


# ── Finding 4 — verify_migration extended checks ─────────────────────────────


class TestVerifyMigration:
    """Plan-time collision and format checks complement the corpus scan."""

    def test_detects_two_old_iris_collapsed_to_same_new(
        self, isolated_paths: Path
    ) -> None:
        """Two distinct old IRIs mapped to the same new IRI must FAIL."""
        # No corpus files → only the plan-time checks fire.
        rename_map = {
            "estleg:OLD_A_Par_1": "estleg:DUP_Par_1",
            "estleg:OLD_B_Par_1": "estleg:DUP_Par_1",
        }
        ok, issues = verify_migration(rename_map)
        assert ok is False
        joined = "\n".join(issues)
        assert "multiple old-IRI sources" in joined
        assert "estleg:DUP_Par_1" in joined

    def test_passes_for_clean_one_to_one_mapping(
        self, isolated_paths: Path
    ) -> None:
        rename_map = {
            "estleg:OLD_Par_1": "estleg:NEW_Par_1",
            "estleg:OLD_Par_2": "estleg:NEW_Par_2",
        }
        ok, issues = verify_migration(rename_map)
        assert ok is True
        assert issues == []

    def test_detects_malformed_new_iri_in_plan(
        self, isolated_paths: Path
    ) -> None:
        """A new IRI that doesn't match the canonical pattern must FAIL."""
        rename_map = {
            "estleg:OLD_Par_1": "estleg:_BadPrefix",  # leading underscore
        }
        ok, issues = verify_migration(rename_map)
        assert ok is False
        assert any("canonical" in i for i in issues)

    def test_detects_malformed_iri_in_corpus(
        self, isolated_paths: Path
    ) -> None:
        """A corpus IRI that fails the format pattern must surface in issues.

        Even when the rename plan is clean, a stray malformed IRI sitting in
        a file (e.g. left over from a manual edit) should be reported.
        """
        krr_dir = isolated_paths / "krr_outputs"
        # Put a malformed IRI directly in a corpus file. The rename map
        # itself is empty so we exercise the corpus-format branch.
        (krr_dir / "stray.json").write_text(
            '{"@id": "estleg:_LeadingUnderscore_Par_1"}', encoding="utf-8"
        )

        ok, issues = verify_migration({})
        assert ok is False
        joined = "\n".join(issues)
        assert "canonical" in joined
        # Includes file:line context.
        assert "stray.json" in joined

    def test_accepts_estonian_unicode_abbrevs(
        self, isolated_paths: Path
    ) -> None:
        """Real-world rt_api abbrevs (TsÜS, ÕÕS, …) must validate."""
        krr_dir = isolated_paths / "krr_outputs"
        (krr_dir / "ok.json").write_text(
            '{"@id": "estleg:TsÜS_Par_70"} {"@id": "estleg:Cluster_ÕÕS_4"}',
            encoding="utf-8",
        )
        rename_map = {"estleg:OLD_Par_1": "estleg:TsÜS_Par_70"}
        ok, issues = verify_migration(rename_map)
        assert ok is True, issues

    def test_format_pattern_smoke(self) -> None:
        assert NEW_IRI_FORMAT_RE.match("estleg:PKS_Par_1")
        assert NEW_IRI_FORMAT_RE.match("estleg:TsÜS_Par_70")
        assert NEW_IRI_FORMAT_RE.match("estleg:Cluster_ATKE_Aktsiis")
        assert not NEW_IRI_FORMAT_RE.match("estleg:_LeadingUnderscore")
        assert not NEW_IRI_FORMAT_RE.match("estleg:1_LeadingDigit")
        assert not NEW_IRI_FORMAT_RE.match("estleg:")


# ── Finding 1 + 2 — apply_cmd hash binding & idempotency ─────────────────────


def _run_dry_run_with_registry(
    isolated_paths: Path, registry: dict, corpus: dict[str, str]
) -> dict:
    """Run the dry-run flow against an isolated corpus and return the report.

    Uses ``families=None`` (the full historical remap) so these legacy
    prefix-rename fixtures still produce renames — the CLI's default
    (amendment + sanction only, issue #192) is exercised by the
    ``TestAmendmentMigration`` tests below.
    """
    _write_registry(migrate_uris.REGISTRY_PATH, registry)
    _seed_corpus(isolated_paths / "krr_outputs", corpus)
    migrate_uris.dry_run_cmd(families=None)
    with open(migrate_uris.REPORT_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestApplyHashBinding:
    """``apply_cmd`` must bind to the registry+corpus that produced its report."""

    def _baseline_registry(self) -> dict:
        return {
            "long_prefix_one": {
                "abbrev": "LP1",
                "source": "auto",
                "title": "Long Prefix One",
                "old_prefix": "long_prefix_one_extra_long_pref",
            }
        }

    def test_apply_refuses_when_registry_changed_after_dry_run(
        self, isolated_paths: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Mutating the registry between dry-run and apply must abort."""
        registry = self._baseline_registry()
        _run_dry_run_with_registry(
            isolated_paths,
            registry,
            {"a.json": '{"@id": "estleg:long_prefix_one_extra_long_pref_Par_1"}'},
        )
        # Mutate the registry — even a benign metadata bump invalidates the
        # report's content-addressed binding.
        registry["long_prefix_one"]["title"] = "Mutated Title"
        _write_registry(migrate_uris.REGISTRY_PATH, registry)

        with pytest.raises(SystemExit) as excinfo:
            migrate_uris.apply_cmd()
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "Registry has changed" in out
        assert "Re-run 'dry-run'" in out

    def test_apply_refuses_when_corpus_changed_after_dry_run(
        self, isolated_paths: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Mutating a scanned file between dry-run and apply must abort."""
        registry = self._baseline_registry()
        _run_dry_run_with_registry(
            isolated_paths,
            registry,
            {"a.json": '{"@id": "estleg:long_prefix_one_extra_long_pref_Par_1"}'},
        )
        # Touch a corpus file — content hash diverges.
        (isolated_paths / "krr_outputs" / "a.json").write_text(
            '{"@id": "estleg:long_prefix_one_extra_long_pref_Par_1"}\n// drift',
            encoding="utf-8",
        )
        with pytest.raises(SystemExit):
            migrate_uris.apply_cmd()
        out = capsys.readouterr().out
        assert "Corpus has changed" in out

    def test_apply_refuses_when_report_lacks_hash_stamps(
        self, isolated_paths: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A legacy report without the new stamps cannot be safely applied."""
        registry = self._baseline_registry()
        _write_registry(migrate_uris.REGISTRY_PATH, registry)
        # Hand-craft a legacy-shaped report (no registry_hash / corpus_hash).
        legacy_report = {
            "generated": "2026-01-01T00:00:00",
            "total_renames": 0,
            "collisions": [],
            "files_affected_count": 0,
            "total_replacements": 0,
            "renames": {},
            "files_affected": {},
            "py_hardcoded_iris": {},
        }
        migrate_uris.REPORT_PATH.write_text(
            json.dumps(legacy_report), encoding="utf-8"
        )
        with pytest.raises(SystemExit):
            migrate_uris.apply_cmd()
        out = capsys.readouterr().out
        assert "missing hash stamps" in out

    def test_apply_succeeds_and_writes_state_when_hashes_match(
        self, isolated_paths: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        registry = {
            "long_prefix_one": {
                "abbrev": "LP1",
                "source": "auto",
                "title": "Long Prefix One",
                "old_prefix": "long_prefix_one_extra_long_pref",
            }
        }
        _run_dry_run_with_registry(
            isolated_paths,
            registry,
            {"a.json": '{"@id": "estleg:long_prefix_one_extra_long_pref_Par_1"}'},
        )
        migrate_uris.apply_cmd()
        out = capsys.readouterr().out
        assert "VERIFICATION PASSED" in out
        assert migrate_uris.MIGRATION_STATE_PATH.exists()
        state = json.loads(
            migrate_uris.MIGRATION_STATE_PATH.read_text(encoding="utf-8")
        )
        assert "last_applied_at" in state
        assert "last_applied_registry_hash" in state
        assert "last_applied_corpus_hash" in state
        # File was rewritten correctly.
        assert (
            "estleg:LP1_Par_1"
            in (isolated_paths / "krr_outputs" / "a.json").read_text(
                encoding="utf-8"
            )
        )

    def test_re_apply_after_migration_short_circuits_via_state(
        self, isolated_paths: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Re-running apply on an already-migrated corpus is a no-op.

        After a successful apply, ``migration_state.json`` records the
        post-migration hashes. The ``apply_cmd`` fast path
        (``_already_migrated_per_state``) recognises that the *current*
        registry+corpus already match the recorded state and returns
        without touching anything — and without needing a fresh dry-run
        report.

        We patch ``compute_corpus_hash`` so the post-apply corpus hash the
        helper reports equals the hash stored in the state (in real life
        the state stores the report's pre-apply hash, which only matches
        when nothing about the scanned corpus has changed since dry-run).
        """
        registry = {
            "long_prefix_one": {
                "abbrev": "LP1",
                "source": "auto",
                "title": "Long Prefix One",
                "old_prefix": "long_prefix_one_extra_long_pref",
            }
        }
        _run_dry_run_with_registry(
            isolated_paths,
            registry,
            {"a.json": '{"@id": "estleg:long_prefix_one_extra_long_pref_Par_1"}'},
        )
        # First apply — succeeds.
        migrate_uris.apply_cmd()
        capsys.readouterr()  # drain

        state = json.loads(
            migrate_uris.MIGRATION_STATE_PATH.read_text(encoding="utf-8")
        )
        with patch.object(
            migrate_uris,
            "compute_corpus_hash",
            return_value=state["last_applied_corpus_hash"],
        ):
            migrate_uris.apply_cmd()
        out = capsys.readouterr().out
        assert "already migrated per migration_state.json" in out
        assert "nothing to do" in out
        # Corpus still in the post-migration state (unchanged by no-op apply).
        assert (
            "estleg:LP1_Par_1"
            in (isolated_paths / "krr_outputs" / "a.json").read_text(
                encoding="utf-8"
            )
        )

    def test_force_reapply_overrides_already_migrated_fast_path(
        self, isolated_paths: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--force-reapply`` must bypass the already-migrated fast path.

        Even when ``migration_state.json`` says the corpus is current, an
        operator can force a re-run (e.g. to repair files). Re-running the
        original (now satisfied) plan against the already-migrated corpus
        finds no old IRIs to replace, so verification still passes and the
        state is re-recorded — but it did not take the no-op fast path.
        """
        registry = {
            "long_prefix_one": {
                "abbrev": "LP1",
                "source": "auto",
                "title": "Long Prefix One",
                "old_prefix": "long_prefix_one_extra_long_pref",
            }
        }
        _run_dry_run_with_registry(
            isolated_paths,
            registry,
            {"a.json": '{"@id": "estleg:long_prefix_one_extra_long_pref_Par_1"}'},
        )
        migrate_uris.apply_cmd()
        capsys.readouterr()

        state = json.loads(
            migrate_uris.MIGRATION_STATE_PATH.read_text(encoding="utf-8")
        )
        with patch.object(
            migrate_uris,
            "compute_corpus_hash",
            return_value=state["last_applied_corpus_hash"],
        ):
            migrate_uris.apply_cmd(force_reapply=True)
        out = capsys.readouterr().out
        # Did NOT take the no-op fast path.
        assert "already migrated per migration_state.json" not in out
        # Re-applying a satisfied plan rewrites nothing but still verifies.
        assert "VERIFICATION PASSED" in out
        # The corpus is untouched (already in the migrated form).
        assert (
            "estleg:LP1_Par_1"
            in (isolated_paths / "krr_outputs" / "a.json").read_text(
                encoding="utf-8"
            )
        )

    def test_re_apply_after_corpus_drift_requires_fresh_dry_run(
        self, isolated_paths: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """After apply, the corpus drifts — re-applying the stale report aborts.

        Because the corpus mutated during apply, the original report's
        corpus_hash no longer matches the on-disk corpus. The operator
        must re-run ``dry-run`` to generate a report bound to the new
        corpus state. This is the natural workflow: "the corpus has
        changed since dry-run".
        """
        registry = {
            "long_prefix_one": {
                "abbrev": "LP1",
                "source": "auto",
                "title": "Long Prefix One",
                "old_prefix": "long_prefix_one_extra_long_pref",
            }
        }
        _run_dry_run_with_registry(
            isolated_paths,
            registry,
            {"a.json": '{"@id": "estleg:long_prefix_one_extra_long_pref_Par_1"}'},
        )
        migrate_uris.apply_cmd()
        capsys.readouterr()

        # Re-applying the same (now-stale) report aborts because the
        # corpus has been rewritten.
        with pytest.raises(SystemExit):
            migrate_uris.apply_cmd()
        out = capsys.readouterr().out
        assert "Corpus has changed" in out

    def test_re_apply_with_different_plan_refuses_without_force(
        self, isolated_paths: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A *different* migration on top of an applied one must require --force-reapply."""
        registry_v1 = {
            "long_prefix_one": {
                "abbrev": "LP1",
                "source": "auto",
                "title": "Long Prefix One",
                "old_prefix": "long_prefix_one_extra_long_pref",
            }
        }
        _run_dry_run_with_registry(
            isolated_paths,
            registry_v1,
            {"a.json": '{"@id": "estleg:long_prefix_one_extra_long_pref_Par_1"}'},
        )
        migrate_uris.apply_cmd()
        capsys.readouterr()

        # Now publish a registry v2 and a fresh dry-run against the
        # already-migrated corpus.
        registry_v2 = {
            "long_prefix_one": {
                "abbrev": "XYZ",  # changed!
                "source": "auto",
                "title": "Long Prefix One",
                "old_prefix": "LP1",  # rename target from previous run
            }
        }
        _write_registry(migrate_uris.REGISTRY_PATH, registry_v2)
        migrate_uris.dry_run_cmd()
        capsys.readouterr()

        with pytest.raises(SystemExit):
            migrate_uris.apply_cmd()
        out = capsys.readouterr().out
        assert "different migration is already recorded" in out
        assert "--force-reapply" in out

        # With --force-reapply, the new migration goes through.
        migrate_uris.apply_cmd(force_reapply=True)
        out = capsys.readouterr().out
        assert "VERIFICATION PASSED" in out
        assert (
            "estleg:XYZ_Par_1"
            in (isolated_paths / "krr_outputs" / "a.json").read_text(
                encoding="utf-8"
            )
        )


# ── Finding 7 — Python-rewrite docstring/comment behaviour ───────────────────


class TestPythonRewriteSafety:
    """Pin the documented behaviour of ``--rewrite-python`` for non-string contexts.

    The rewriter operates at the regex-token level, not the AST level. This
    is an intentional trade-off: AST-level rewriting would require a full
    parse/serialise loop and would lose comments, blank lines, and string
    quoting style. These tests document the actual behaviour so reviewers
    don't have to re-derive it from the implementation.
    """

    def test_rewrites_iri_in_string_literal(self, tmp_path: Path) -> None:
        f = tmp_path / "src.py"
        f.write_text('IRI = "estleg:OLD"\n', encoding="utf-8")
        count = apply_renames_to_file(
            f, [("estleg:OLD", "estleg:NEW")], rewrite_python=True
        )
        assert count == 1
        assert 'IRI = "estleg:NEW"' in f.read_text(encoding="utf-8")

    def test_also_rewrites_iri_inside_comment(self, tmp_path: Path) -> None:
        """Documented limitation: comments are also rewritten.

        This is acceptable in practice because comments referencing IRIs
        should track the canonical form anyway. If we ever need true
        AST-only safety, switch to ``ast`` / ``tokenize`` and rewrite
        only ``Constant`` string nodes.
        """
        f = tmp_path / "src.py"
        f.write_text("# see estleg:OLD for context\n", encoding="utf-8")
        count = apply_renames_to_file(
            f, [("estleg:OLD", "estleg:NEW")], rewrite_python=True
        )
        # Pin the actual behaviour — this is the trade-off.
        assert count == 1
        assert "# see estleg:NEW for context" in f.read_text(encoding="utf-8")

    def test_also_rewrites_iri_inside_docstring(self, tmp_path: Path) -> None:
        """Documented limitation: docstrings are rewritten too — same trade-off."""
        f = tmp_path / "src.py"
        f.write_text(
            '"""Module docstring referencing estleg:OLD."""\n',
            encoding="utf-8",
        )
        count = apply_renames_to_file(
            f, [("estleg:OLD", "estleg:NEW")], rewrite_python=True
        )
        assert count == 1
        assert "estleg:NEW" in f.read_text(encoding="utf-8")
        assert "estleg:OLD" not in f.read_text(encoding="utf-8")

    def test_python_rewrite_uses_atomic_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crash mid-rewrite must leave the .py file intact (no half-edit)."""
        f = tmp_path / "src.py"
        original = 'IRI = "estleg:OLD"  # documented\n'
        f.write_text(original, encoding="utf-8")

        def _boom(src: str, dst: str) -> None:
            raise OSError("simulated crash mid-rewrite")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            apply_renames_to_file(
                f, [("estleg:OLD", "estleg:NEW")], rewrite_python=True
            )
        assert f.read_text(encoding="utf-8") == original


# ── Finding 1 + 2 supplementary — registry hash plumbing ─────────────────────


class TestHashHelpers:
    """Direct coverage for the hash helpers used by the binding/idempotency logic."""

    def test_registry_hash_changes_when_file_mutates(
        self, isolated_paths: Path
    ) -> None:
        path = migrate_uris.REGISTRY_PATH
        path.write_text('{"a": 1}', encoding="utf-8")
        h1 = compute_registry_hash(path)
        path.write_text('{"a": 2}', encoding="utf-8")
        h2 = compute_registry_hash(path)
        assert h1 != h2
        # Stable for unchanged content.
        assert compute_registry_hash(path) == h2

    def test_corpus_hash_is_order_independent(self, isolated_paths: Path) -> None:
        """Re-ordering the input file list must not change the hash.

        The implementation sorts internally so callers don't have to.
        """
        krr = isolated_paths / "krr_outputs"
        files = _seed_corpus(krr, {"a.json": "AAA", "b.json": "BBB"})
        h1 = compute_corpus_hash(files)
        h2 = compute_corpus_hash(list(reversed(files)))
        assert h1 == h2

    def test_corpus_hash_changes_when_file_changes(
        self, isolated_paths: Path
    ) -> None:
        krr = isolated_paths / "krr_outputs"
        files = _seed_corpus(krr, {"a.json": "AAA"})
        h1 = compute_corpus_hash(files)
        (krr / "a.json").write_text("AAB", encoding="utf-8")
        h2 = compute_corpus_hash(files)
        assert h1 != h2

    def test_corpus_hash_changes_when_file_added(
        self, isolated_paths: Path
    ) -> None:
        krr = isolated_paths / "krr_outputs"
        files = _seed_corpus(krr, {"a.json": "AAA"})
        h1 = compute_corpus_hash(files)
        files = _seed_corpus(krr, {"a.json": "AAA", "b.json": "BBB"})
        h2 = compute_corpus_hash(files)
        assert h1 != h2


# ── Finding 6 — UTC timestamp on dry-run report ──────────────────────────────


class TestDryRunReport:
    def test_generated_timestamp_is_utc(self, isolated_paths: Path) -> None:
        registry = {
            "demo_long_prefix_law": {
                "abbrev": "DLP",
                "source": "auto",
                "title": "Demo",
                "old_prefix": "demo_long_prefix_law_overflow",
            }
        }
        _write_registry(migrate_uris.REGISTRY_PATH, registry)
        _seed_corpus(
            isolated_paths / "krr_outputs",
            {"a.json": '{"@id": "estleg:demo_long_prefix_law_overflow_Par_1"}'},
        )
        migrate_uris.dry_run_cmd(families=None)
        report = json.loads(
            migrate_uris.REPORT_PATH.read_text(encoding="utf-8")
        )
        # ``datetime.now(timezone.utc).isoformat()`` produces a ``+00:00``
        # suffix; the legacy naive call produced none.
        assert report["generated"].endswith("+00:00")
        assert "registry_hash" in report
        assert "corpus_hash" in report
        # Issue #192 added this field (always present, even when empty).
        assert "skipped_amendment_slugs" in report
        assert isinstance(report["skipped_amendment_slugs"], dict)

    def test_cli_default_families_skip_the_legacy_prefix_remap(
        self, isolated_paths: Path
    ) -> None:
        """``dry_run_cmd(families=DEFAULT_MIGRATION_FAMILIES)`` — the CLI's
        default — must skip the historical law/regulation prefix remap and
        only shorten the Amendment/Sanction families."""
        registry = {
            "demo_long_prefix_law": {
                "abbrev": "DLP",
                "source": "auto",
                "title": "Demo",
                "old_prefix": "demo_long_prefix_law_overflow",
            }
        }
        _write_registry(migrate_uris.REGISTRY_PATH, registry)
        krr = isolated_paths / "krr_outputs"
        (krr / "amendments").mkdir(parents=True)
        _seed_corpus(
            krr,
            {"a.json": '{"@id": "estleg:demo_long_prefix_law_overflow_Par_1"}'},
        )
        migrate_uris.dry_run_cmd(families=migrate_uris.DEFAULT_MIGRATION_FAMILIES)
        report = json.loads(migrate_uris.REPORT_PATH.read_text(encoding="utf-8"))
        # The legacy <prefix>_Par_ IRI is NOT in the rename map (no Amendment_
        # or Sanction_ IRI in the corpus → nothing to do).
        assert report["renames"] == {}
        # …but with the legacy family it *would* be renamed.
        migrate_uris.dry_run_cmd(families=None)
        report2 = json.loads(migrate_uris.REPORT_PATH.read_text(encoding="utf-8"))
        assert report2["renames"] == {
            "estleg:demo_long_prefix_law_overflow_Par_1": "estleg:DLP_Par_1"
        }


# ── #83 task 1 — migration_state.json sentinel ───────────────────────────────


class TestMigrationStateSentinelRoundTrip:
    """``save_migration_state`` / ``load_migration_state`` are exact inverses."""

    def test_save_then_load_roundtrips(self, isolated_paths: Path) -> None:
        state = {
            "last_applied_at": "2026-05-11T07:48:18.502144+00:00",
            "last_applied_registry_hash": "a" * 64,
            "last_applied_corpus_hash": "b" * 64,
            "total_renames_applied": 14651,
            "files_changed": 714,
            "note": "backfilled — unicode ok: Võlaõigusseadus / TsÜS",
        }
        save_migration_state(state)
        assert migrate_uris.MIGRATION_STATE_PATH.exists()
        # Round-trips via the public loader …
        assert load_migration_state() == state
        # … and via raw JSON (unicode preserved, not \u-escaped).
        on_disk = migrate_uris.MIGRATION_STATE_PATH.read_text(encoding="utf-8")
        assert "Võlaõigusseadus" in on_disk
        assert json.loads(on_disk) == state

    def test_load_missing_returns_none(self, isolated_paths: Path) -> None:
        assert not migrate_uris.MIGRATION_STATE_PATH.exists()
        assert load_migration_state() is None

    def test_load_corrupt_returns_none(self, isolated_paths: Path) -> None:
        migrate_uris.MIGRATION_STATE_PATH.write_text("{not json", encoding="utf-8")
        assert load_migration_state() is None

    def test_already_migrated_predicate(self, isolated_paths: Path) -> None:
        """``_already_migrated_per_state`` is True iff both hashes match now."""
        _write_registry(migrate_uris.REGISTRY_PATH, {"x": {"abbrev": "X"}})
        files = _seed_corpus(isolated_paths / "krr_outputs", {"a.json": "AAA"})
        reg_hash = compute_registry_hash(migrate_uris.REGISTRY_PATH)
        corpus_hash = compute_corpus_hash(get_all_scannable_files())

        assert _already_migrated_per_state(None) is False
        assert (
            _already_migrated_per_state(
                {"last_applied_registry_hash": reg_hash}
            )
            is False
        )  # missing corpus hash
        assert (
            _already_migrated_per_state(
                {
                    "last_applied_registry_hash": reg_hash,
                    "last_applied_corpus_hash": corpus_hash,
                }
            )
            is True
        )
        # Drift in either hash flips it to False.
        assert (
            _already_migrated_per_state(
                {
                    "last_applied_registry_hash": "0" * 64,
                    "last_applied_corpus_hash": corpus_hash,
                }
            )
            is False
        )
        (isolated_paths / "krr_outputs" / "a.json").write_text(
            "MUTATED", encoding="utf-8"
        )
        assert (
            _already_migrated_per_state(
                {
                    "last_applied_registry_hash": reg_hash,
                    "last_applied_corpus_hash": corpus_hash,
                }
            )
            is False
        )
        # silence unused-var lint on ``files``
        assert files


class TestRealMigrationStateFile:
    """The committed ``data/migration_state.json`` is well-formed and current.

    These exercise the *real* repo file rather than an isolated fixture. The
    registry hash is asserted exactly (``data/law_abbreviations.json`` is a
    committed, stable artifact). The corpus hash is checked for shape and for
    *self-consistency* with the ``apply`` fast path — but not asserted equal to
    a freshly recomputed value, because the scanned corpus (which includes
    ``scripts/*.py``) legitimately drifts between commits; the #178 sentinel is
    designed to detect that drift and ask for a fresh ``dry-run`` rather than
    to be a frozen invariant.
    """

    def test_file_exists_and_has_expected_shape(self) -> None:
        path = migrate_uris.MIGRATION_STATE_PATH
        assert path.exists(), "data/migration_state.json should be committed"
        state = json.loads(path.read_text(encoding="utf-8"))
        for key in (
            "last_applied_at",
            "last_applied_registry_hash",
            "last_applied_corpus_hash",
            "note",
        ):
            assert key in state, f"missing key: {key}"
        assert _SHA256_HEX_RE.match(state["last_applied_registry_hash"])
        assert _SHA256_HEX_RE.match(state["last_applied_corpus_hash"])
        # Timestamp parses as ISO-8601 (and is UTC-qualified).
        parsed = datetime.fromisoformat(state["last_applied_at"])
        assert parsed.tzinfo is not None
        # The note records the migration provenance: Layer 1 (#83 commits) and
        # Layer 2 (#192 — the Amendment_ IRI shortening).
        assert "3880cb688" in state["note"]
        assert "c5c0f88b2" in state["note"]
        assert "#192" in state["note"]
        # The public loader returns the same content.
        assert load_migration_state() == state

    def test_registry_hash_matches_helper(self) -> None:
        state = load_migration_state()
        assert state is not None
        assert state["last_applied_registry_hash"] == compute_registry_hash(
            migrate_uris.REGISTRY_PATH
        )

    def test_corpus_hash_is_consistent_with_apply_fast_path(self) -> None:
        """If the recorded corpus hash still matches the live corpus, the
        ``apply`` fast path must recognise the corpus as already-migrated.

        This is the "round-trips against the current corpus" property: when
        nothing has drifted, ``migration_state.json`` plus the hash helpers
        plus ``_already_migrated_per_state`` all agree. When the corpus *has*
        drifted (e.g. unrelated edits to scanned files landed since the
        sentinel was backfilled), we don't fail — drift is expected and
        ``migrate_uris.py apply`` will guide a re-run.
        """
        state = load_migration_state()
        assert state is not None
        live_corpus_hash = compute_corpus_hash(get_all_scannable_files())
        if state["last_applied_corpus_hash"] == live_corpus_hash:
            assert _already_migrated_per_state(state) is True
        else:
            # Recorded hash refers to an earlier corpus snapshot; still must be
            # a syntactically valid digest.
            assert _SHA256_HEX_RE.match(state["last_applied_corpus_hash"])

    def test_apply_command_recognises_already_migrated_when_in_sync(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``migrate_uris.py apply`` no-ops on an already-migrated corpus.

        Runs the real ``apply_cmd`` against the real repo. When the committed
        ``migration_state.json`` matches the live registry+corpus the command
        prints the "already migrated" no-op message and returns. If they have
        drifted (unrelated scanned-file edits since the backfill) the command
        instead refuses with a "Corpus has changed"/"missing hash stamps"
        error — both are correct, neither mutates files, so we accept either.
        """
        state = load_migration_state()
        assert state is not None
        in_sync = _already_migrated_per_state(state)
        if in_sync:
            migrate_uris.apply_cmd()
            out = capsys.readouterr().out
            assert "already migrated per migration_state.json" in out
            assert "nothing to do" in out
        else:
            with pytest.raises(SystemExit):
                migrate_uris.apply_cmd()
            out = capsys.readouterr().out
            assert (
                "Corpus has changed" in out
                or "missing hash stamps" in out
                or "different migration is already recorded" in out
            )
