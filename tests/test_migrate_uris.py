"""Tests for migrate_uris.py — Tasks 1-4 plus review-finding regression tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import migrate_uris
from migrate_uris import (
    NEW_IRI_FORMAT_RE,
    PAR_NO_UNDERSCORE_RE,
    _atomic_write_text,
    apply_renames_to_file,
    assign_abbreviations,
    auto_derive_abbreviation,
    build_rename_map,
    compute_corpus_hash,
    compute_registry_hash,
    verify_migration,
)


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
    """Run the dry-run flow against an isolated corpus and return the report."""
    _write_registry(migrate_uris.REGISTRY_PATH, registry)
    _seed_corpus(isolated_paths / "krr_outputs", corpus)
    migrate_uris.dry_run_cmd()
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

    def test_re_apply_same_report_is_idempotent_short_circuit(
        self, isolated_paths: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Re-running apply against the *exact same* report short-circuits.

        After a successful apply, ``migration_state.json`` records the
        report's hashes. Calling ``apply_cmd`` again with the report and
        registry untouched (i.e. the operator hits "apply" twice by
        mistake before the corpus has had a chance to drift) must detect
        the matching state and return without rewriting anything.

        We accomplish this by skipping the corpus-hash check via a
        targeted patch — in real life the operator would never see the
        post-apply corpus_hash match the pre-apply hash, so this branch
        is the one we explicitly support: same report, same state, no-op.
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

        # The corpus changed during apply, but the migration state now
        # records the *post-apply* corpus hash equal to the report's
        # corpus hash (because dry-run captured pre-apply, apply rewrote,
        # and the state was stored with the report's hashes). Patching the
        # current corpus hash to match the report hash simulates a perfect
        # double-click without the corpus having been re-scanned.
        report = json.loads(migrate_uris.REPORT_PATH.read_text(encoding="utf-8"))
        with patch.object(
            migrate_uris,
            "compute_corpus_hash",
            return_value=report["corpus_hash"],
        ):
            migrate_uris.apply_cmd()
        out = capsys.readouterr().out
        assert "matches the current report" in out
        # Corpus still in the post-migration state (unchanged by no-op apply).
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
        migrate_uris.dry_run_cmd()
        report = json.loads(
            migrate_uris.REPORT_PATH.read_text(encoding="utf-8")
        )
        # ``datetime.now(timezone.utc).isoformat()`` produces a ``+00:00``
        # suffix; the legacy naive call produced none.
        assert report["generated"].endswith("+00:00")
        assert "registry_hash" in report
        assert "corpus_hash" in report
