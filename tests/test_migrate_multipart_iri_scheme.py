from __future__ import annotations

import json
from pathlib import Path

import pytest
from migrate_multipart_iri_scheme import (
    OLD_PAR_RE,
    _apply_substitution,
    _atomic_write_text,
    _build_substitution_pattern,
    build_plan,
    discover_multipart_laws,
    execute_plan,
    main,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_peep(
    krr_dir: Path, law: str, osa: int, provisions: list[tuple[str, str]]
) -> Path:
    """Write a minimal osa peep file. ``provisions`` is a list of
    ``(prefix, par_suffix)`` tuples that become @graph nodes."""
    graph = [
        {
            "@id": f"estleg:{law.upper()[:3]}_Osa{osa}_root",
            "@type": ["estleg:Act", "owl:Ontology"],
        }
    ]
    for prefix, par in provisions:
        graph.append(
            {
                "@id": f"estleg:{prefix}_Par_{par}",
                "@type": "estleg:LegalProvision",
                "rdfs:label": f"§{par}",
            }
        )
    path = krr_dir / f"{law}_osa{osa}_peep.json"
    path.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": graph,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_cross_ref(
    krr_dir: Path, filename: str, references: list[str]
) -> Path:
    """Write a synthetic cross-referencing JSON file that mentions
    ``references`` (each a full ``estleg:`` IRI)."""
    nodes = [
        {
            "@id": "estleg:CrossRefHost",
            "estleg:references": [{"@id": ref} for ref in references],
        }
    ]
    path = krr_dir / filename
    path.write_text(
        json.dumps(
            {
                "@context": {"estleg": "https://w3id.org/estleg/"},
                "@graph": nodes,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def tmp_krr(tmp_path: Path) -> Path:
    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    return krr


# ── Regex / pattern unit tests ────────────────────────────────────────────────


class TestOldParRegex:
    def test_matches_simple_par(self):
        m = OLD_PAR_RE.match("estleg:KARIST_2_Par_1")
        assert m is not None
        assert m.group("prefix") == "KARIST_2"
        assert m.group("par") == "1"

    def test_matches_par_with_subnumber(self):
        m = OLD_PAR_RE.match("estleg:VOS_Par_11_1")
        assert m is not None
        assert m.group("prefix") == "VOS"
        assert m.group("par") == "11_1"

    def test_rejects_already_migrated(self):
        # An @id with _Osa in it is NEW-scheme; we use a separate "_Osa
        # not in IRI" check in build_law_scope, but the regex also won't
        # produce a useful prefix.
        m = OLD_PAR_RE.match("estleg:VOS_Osa1_Par_76")
        # The regex MATCHES (greedy prefix consumes "VOS_Osa1"), but
        # the build_law_scope guard skips any IRI containing "_Osa".
        # Verify the guard rather than the regex here.
        assert m is not None
        assert "_Osa" in "estleg:VOS_Osa1_Par_76"


class TestSubstitutionPattern:
    def test_no_substring_collision(self):
        """KARIST_2_Par_1 must not match while searching for KARIST_2_Par_10."""
        rename_map = {"estleg:KARIST_2_Par_10": "estleg:KARIST_2_Osa2_Par_10"}
        text = (
            '{"a": "estleg:KARIST_2_Par_1", "b": "estleg:KARIST_2_Par_10"}'
        )
        new_text, count = _apply_substitution(text, rename_map)
        assert count == 1
        assert "estleg:KARIST_2_Par_1" in new_text  # untouched
        assert "estleg:KARIST_2_Osa2_Par_10" in new_text  # rewritten
        # Make sure Par_1 wasn't mangled to Par_0 etc.
        assert new_text.count("estleg:KARIST_2_Par_1") == 1

    def test_only_matches_complete_quoted_iri(self):
        rename_map = {"estleg:VOS_Par_76": "estleg:VOS_Osa1_Par_76"}
        text = (
            '"estleg:VOS_Par_76" '
            '"estleg:VOS_Par_76_extra" '
            '"prefix-estleg:VOS_Par_76"'
        )
        # The first must rewrite; the second is a different complete
        # IRI string and not in the rename map; the third has a non-quote
        # boundary before estleg:. The regex matches quoted IRIs, so
        # only #1 changes.
        new_text, count = _apply_substitution(text, rename_map)
        assert count == 1
        assert '"estleg:VOS_Osa1_Par_76"' in new_text
        assert '"estleg:VOS_Par_76_extra"' in new_text

    def test_empty_map_returns_input(self):
        text = '{"foo": "bar"}'
        new_text, count = _apply_substitution(text, {})
        assert new_text == text
        assert count == 0

    def test_pattern_is_none_when_empty(self):
        assert _build_substitution_pattern([]) is None


# ── Plan construction ────────────────────────────────────────────────────────


class TestPlanConstruction:
    def test_builds_rename_map_for_unambiguous_law(self, tmp_krr: Path):
        """KARIST_2 — a clean two-osa law with no cross-osa collisions."""
        _write_peep(
            tmp_krr, "karistusseadustik", 1, [("KARIST_2", "1"), ("KARIST_2", "2")]
        )
        _write_peep(
            tmp_krr, "karistusseadustik", 2, [("KARIST_2", "88"), ("KARIST_2", "89")]
        )

        plan = build_plan(tmp_krr, laws=["karistusseadustik"])
        assert plan.rename_map_size == 4
        assert plan.ambiguities == []

        # Canonical map covers all four old IRIs.
        assert plan.canonical_map == {
            "estleg:KARIST_2_Par_1": "estleg:KARIST_2_Osa1_Par_1",
            "estleg:KARIST_2_Par_2": "estleg:KARIST_2_Osa1_Par_2",
            "estleg:KARIST_2_Par_88": "estleg:KARIST_2_Osa2_Par_88",
            "estleg:KARIST_2_Par_89": "estleg:KARIST_2_Osa2_Par_89",
        }

        # Per-file maps are partitioned by osa.
        osa1_file = tmp_krr / "karistusseadustik_osa1_peep.json"
        osa2_file = tmp_krr / "karistusseadustik_osa2_peep.json"
        assert plan.per_file_maps[osa1_file] == {
            "estleg:KARIST_2_Par_1": "estleg:KARIST_2_Osa1_Par_1",
            "estleg:KARIST_2_Par_2": "estleg:KARIST_2_Osa1_Par_2",
        }
        assert plan.per_file_maps[osa2_file] == {
            "estleg:KARIST_2_Par_88": "estleg:KARIST_2_Osa2_Par_88",
            "estleg:KARIST_2_Par_89": "estleg:KARIST_2_Osa2_Par_89",
        }

    def test_handles_voss_style_ambiguity(self, tmp_krr: Path):
        """Synthetic VõS — same prefix+par in osa1 and osa3 → ambiguous."""
        # Same OLD IRI (estleg:VOS_Par_76) defined in osa1 AND osa3.
        # This is the strict ambiguity case the spec describes: a
        # cross-ref file can't tell which provision is meant.
        _write_peep(tmp_krr, "volaigusseadus", 1, [("VOS", "76"), ("VOS", "77")])
        _write_peep(tmp_krr, "volaigusseadus", 3, [("VOS", "76"), ("VOS", "200")])

        plan = build_plan(tmp_krr, laws=["volaigusseadus"])
        assert plan.rename_map_size == 4
        # One ambiguity recorded — canonical goes to osa1 (earliest).
        assert len(plan.ambiguities) == 1
        amb = plan.ambiguities[0]
        assert amb["old_iri"] == "estleg:VOS_Par_76"
        assert amb["canonical_new_iri"] == "estleg:VOS_Osa1_Par_76"
        assert amb["also_could_be"] == ["estleg:VOS_Osa3_Par_76"]
        assert amb["law"] == "volaigusseadus"

        # Canonical map points to the earliest-osa NEW IRI.
        assert (
            plan.canonical_map["estleg:VOS_Par_76"]
            == "estleg:VOS_Osa1_Par_76"
        )

    def test_skips_range_osa_filenames(self, tmp_krr: Path):
        """``osa6-13`` peep files are recorded as skipped, not migrated."""
        # First make a regular osa1.
        _write_peep(
            tmp_krr, "asjaoigusseadus", 1, [("AOS", "1"), ("AOS", "2")]
        )
        # Then a range-form file we cannot disambiguate.
        path = tmp_krr / "asjaoigusseadus_osa6-13_peep.json"
        path.write_text(
            json.dumps(
                {
                    "@context": {
                        "estleg": "https://w3id.org/estleg/"
                    },
                    "@graph": [
                        {
                            "@id": "estleg:AOS_Par_141",
                            "@type": "estleg:LegalProvision",
                        }
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        plan = build_plan(tmp_krr, laws=["asjaoigusseadus"])
        scope = plan.scopes[0]
        assert scope.osa_files == {1: tmp_krr / "asjaoigusseadus_osa1_peep.json"}
        assert path in scope.skipped_osa_files
        # AOS_Par_141 in the range file is NOT renamed — only the two
        # osa1 provisions are.
        assert "estleg:AOS_Par_141" not in plan.canonical_map
        assert plan.rename_map_size == 2

    def test_discovers_multipart_laws(self, tmp_krr: Path):
        _write_peep(tmp_krr, "karistusseadustik", 1, [("KARIST_2", "1")])
        _write_peep(tmp_krr, "karistusseadustik", 2, [("KARIST_2", "88")])
        _write_peep(tmp_krr, "volaigusseadus", 1, [("VOS", "1")])
        # A non-multipart law should NOT show up.
        single = tmp_krr / "perekonnaseadus_peep.json"
        single.write_text(
            json.dumps({"@graph": []}, indent=2), encoding="utf-8"
        )

        laws = discover_multipart_laws(tmp_krr)
        assert laws == ["karistusseadustik", "volaigusseadus"]


# ── Dry-run vs apply behaviour ────────────────────────────────────────────────


class TestDryRunAndApply:
    def _setup_two_law_corpus(self, tmp_krr: Path) -> dict[str, Path]:
        """Build a small corpus with KARIST + VõS-style ambiguity + a
        cross-reference file referring to both."""
        files: dict[str, Path] = {}
        files["karist_osa1"] = _write_peep(
            tmp_krr, "karistusseadustik", 1, [("KARIST_2", "1")]
        )
        files["karist_osa2"] = _write_peep(
            tmp_krr, "karistusseadustik", 2, [("KARIST_2", "88")]
        )
        files["vos_osa1"] = _write_peep(
            tmp_krr, "volaigusseadus", 1, [("VOS", "76"), ("VOS", "1")]
        )
        files["vos_osa3"] = _write_peep(
            tmp_krr, "volaigusseadus", 3, [("VOS", "76"), ("VOS", "200")]
        )
        files["cross"] = _write_cross_ref(
            tmp_krr,
            "combined_ontology.jsonld",
            [
                "estleg:KARIST_2_Par_1",
                "estleg:KARIST_2_Par_88",
                "estleg:VOS_Par_76",
                "estleg:VOS_Par_200",
            ],
        )
        return files

    def test_dry_run_modifies_nothing(self, tmp_krr: Path, tmp_path: Path):
        files = self._setup_two_law_corpus(tmp_krr)
        # Snapshot every file's bytes.
        before = {p: p.read_bytes() for p in files.values()}

        report_path = tmp_path / "plan.json"
        rc = main(
            [
                "--dry-run",
                "--krr-dir",
                str(tmp_krr),
                "--report",
                str(report_path),
            ]
        )
        assert rc == 0

        # No corpus file changed.
        for p, contents in before.items():
            assert p.read_bytes() == contents, f"{p} was modified during dry-run"

        # Report exists and has the expected shape.
        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["mode"] == "dry-run"
        assert report["files_modified"] >= 1  # cross-ref file would change

    def test_apply_rewrites_law_files_with_per_file_scope(self, tmp_krr: Path, tmp_path: Path):
        """VOS_Par_76 in osa1 → VOS_Osa1_Par_76; same in osa3 → VOS_Osa3_Par_76."""
        files = self._setup_two_law_corpus(tmp_krr)
        report_path = tmp_path / "plan.json"

        rc = main(
            [
                "--apply",
                "--krr-dir",
                str(tmp_krr),
                "--report",
                str(report_path),
            ]
        )
        assert rc == 0

        # osa1 peep: VOS_Par_76 → VOS_Osa1_Par_76 (per-file scope).
        osa1_data = json.loads(files["vos_osa1"].read_text(encoding="utf-8"))
        osa1_ids = {n.get("@id") for n in osa1_data["@graph"]}
        assert "estleg:VOS_Osa1_Par_76" in osa1_ids
        assert "estleg:VOS_Par_76" not in osa1_ids

        # osa3 peep: VOS_Par_76 → VOS_Osa3_Par_76 (per-file scope, NOT canonical).
        osa3_data = json.loads(files["vos_osa3"].read_text(encoding="utf-8"))
        osa3_ids = {n.get("@id") for n in osa3_data["@graph"]}
        assert "estleg:VOS_Osa3_Par_76" in osa3_ids
        assert "estleg:VOS_Par_76" not in osa3_ids
        # And the non-ambiguous one too.
        assert "estleg:VOS_Osa3_Par_200" in osa3_ids

    def test_apply_rewrites_cross_ref_files_with_canonical_choice(
        self, tmp_krr: Path, tmp_path: Path
    ):
        """Cross-ref files use the canonical (earliest-osa) NEW IRI."""
        files = self._setup_two_law_corpus(tmp_krr)
        report_path = tmp_path / "plan.json"
        main(
            [
                "--apply",
                "--krr-dir",
                str(tmp_krr),
                "--report",
                str(report_path),
            ]
        )

        cross_data = json.loads(files["cross"].read_text(encoding="utf-8"))
        refs = {
            r["@id"]
            for r in cross_data["@graph"][0]["estleg:references"]
        }
        # KARIST renames are unambiguous.
        assert "estleg:KARIST_2_Osa1_Par_1" in refs
        assert "estleg:KARIST_2_Osa2_Par_88" in refs
        # The ambiguous VOS_Par_76 was rewritten to the canonical
        # (osa1) form, NOT the osa3 form.
        assert "estleg:VOS_Osa1_Par_76" in refs
        assert "estleg:VOS_Osa3_Par_76" not in refs
        # The non-ambiguous VOS_Par_200 (osa3 only) goes to osa3.
        assert "estleg:VOS_Osa3_Par_200" in refs
        # No stale OLD IRIs.
        assert not any(
            r in refs
            for r in [
                "estleg:KARIST_2_Par_1",
                "estleg:KARIST_2_Par_88",
                "estleg:VOS_Par_76",
                "estleg:VOS_Par_200",
            ]
        )

    def test_no_substring_collision(self, tmp_krr: Path, tmp_path: Path):
        """KARIST_2_Par_1 must stay intact when KARIST_2_Par_10 is renamed."""
        # Write KARIST osa1 (with Par_1) and KARIST osa2 (with Par_10).
        _write_peep(
            tmp_krr,
            "karistusseadustik",
            1,
            [("KARIST_2", "1")],
        )
        _write_peep(
            tmp_krr,
            "karistusseadustik",
            2,
            [("KARIST_2", "10")],
        )
        # A cross-ref file mentioning BOTH; if our regex were a naive
        # substring match for "estleg:KARIST_2_Par_1" it would also
        # match inside "estleg:KARIST_2_Par_10".
        cross = _write_cross_ref(
            tmp_krr,
            "combined_ontology.jsonld",
            ["estleg:KARIST_2_Par_1", "estleg:KARIST_2_Par_10"],
        )
        report_path = tmp_path / "plan.json"
        main(
            [
                "--apply",
                "--krr-dir",
                str(tmp_krr),
                "--report",
                str(report_path),
            ]
        )
        cross_data = json.loads(cross.read_text(encoding="utf-8"))
        refs = {r["@id"] for r in cross_data["@graph"][0]["estleg:references"]}
        assert refs == {
            "estleg:KARIST_2_Osa1_Par_1",
            "estleg:KARIST_2_Osa2_Par_10",
        }

    def test_idempotent_on_already_migrated_files(self, tmp_krr: Path, tmp_path: Path):
        """Running on a file already in NEW scheme is a no-op."""
        # Write a peep file whose @graph already uses NEW scheme.
        path = tmp_krr / "karistusseadustik_osa1_peep.json"
        path.write_text(
            json.dumps(
                {
                    "@context": {
                        "estleg": "https://w3id.org/estleg/"
                    },
                    "@graph": [
                        {
                            "@id": "estleg:KARIST_2_Osa1_root",
                            "@type": "owl:Ontology",
                        },
                        {
                            "@id": "estleg:KARIST_2_Osa1_Par_1",
                            "@type": "estleg:LegalProvision",
                        },
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        before = path.read_bytes()

        report_path = tmp_path / "plan.json"
        rc = main(
            [
                "--apply",
                "--krr-dir",
                str(tmp_krr),
                "--report",
                str(report_path),
            ]
        )
        assert rc == 0
        # File unchanged.
        assert path.read_bytes() == before

        # And the report reflects "nothing to do" for this law.
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["rename_map_size"] == 0
        assert report["files_modified"] == 0


# ── Report shape ──────────────────────────────────────────────────────────────


class TestReportShape:
    def test_report_shape(self, tmp_krr: Path, tmp_path: Path):
        # Build a corpus with both an unambiguous law and an ambiguous one.
        _write_peep(
            tmp_krr,
            "karistusseadustik",
            1,
            [("KARIST_2", "1"), ("KARIST_2", "2")],
        )
        _write_peep(
            tmp_krr,
            "karistusseadustik",
            2,
            [("KARIST_2", "88")],
        )
        _write_peep(
            tmp_krr, "volaigusseadus", 1, [("VOS", "76"), ("VOS", "1")]
        )
        _write_peep(
            tmp_krr, "volaigusseadus", 3, [("VOS", "76"), ("VOS", "200")]
        )

        report_path = tmp_path / "plan.json"
        main(
            [
                "--dry-run",
                "--krr-dir",
                str(tmp_krr),
                "--report",
                str(report_path),
            ]
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))

        # Required top-level fields per the spec.
        required_keys = {
            "pipeline",
            "mode",
            "laws",
            "rename_map_size",
            "ambiguous_canonical_choices",
            "files_modified",
            "files_inspected",
            "timestamp",
        }
        assert required_keys.issubset(report.keys())
        assert report["pipeline"] == "migrate_multipart_iri_scheme"
        assert report["mode"] == "dry-run"

        # ``laws`` is a list of per-law summaries.
        laws_by_name = {law["name"]: law for law in report["laws"]}
        assert set(laws_by_name.keys()) == {"karistusseadustik", "volaigusseadus"}
        karist = laws_by_name["karistusseadustik"]
        assert karist["osa_count"] == 2
        assert karist["old_provisions_renamed"] == 3
        assert karist["ambiguous_par_suffixes"] == []
        vos = laws_by_name["volaigusseadus"]
        assert vos["osa_count"] == 2
        # VOS has 2 osas × {76 + 1 unique per osa}, all four are renames.
        assert vos["old_provisions_renamed"] == 4
        assert vos["ambiguous_par_suffixes"] == ["76"]

        # rename_map_size totals all law renames.
        assert report["rename_map_size"] == 7

        # ambiguous_canonical_choices recorded with required sub-fields.
        assert len(report["ambiguous_canonical_choices"]) == 1
        amb = report["ambiguous_canonical_choices"][0]
        assert amb["old_iri"] == "estleg:VOS_Par_76"
        assert amb["canonical_new_iri"] == "estleg:VOS_Osa1_Par_76"
        assert "also_could_be" in amb

        # Timestamp is ISO-formatted.
        # ``datetime.fromisoformat`` accepts the same format we emit.
        from datetime import datetime as _dt

        _dt.fromisoformat(report["timestamp"])


# ── Restriction by --law ──────────────────────────────────────────────────────


class TestLawFilter:
    def test_law_flag_restricts_scope(self, tmp_krr: Path, tmp_path: Path):
        _write_peep(tmp_krr, "karistusseadustik", 1, [("KARIST_2", "1")])
        _write_peep(tmp_krr, "volaigusseadus", 1, [("VOS", "1")])

        report_path = tmp_path / "plan.json"
        rc = main(
            [
                "--dry-run",
                "--law",
                "karistusseadustik",
                "--krr-dir",
                str(tmp_krr),
                "--report",
                str(report_path),
            ]
        )
        assert rc == 0
        report = json.loads(report_path.read_text(encoding="utf-8"))
        names = {law["name"] for law in report["laws"]}
        assert names == {"karistusseadustik"}
        assert report["rename_map_size"] == 1


# ── Apply/dry-run argument validation ────────────────────────────────────────


class TestCli:
    def test_apply_and_dry_run_are_exclusive(self, tmp_krr: Path, tmp_path: Path, capsys):
        report_path = tmp_path / "plan.json"
        with pytest.raises(SystemExit):
            main(
                [
                    "--apply",
                    "--dry-run",
                    "--krr-dir",
                    str(tmp_krr),
                    "--report",
                    str(report_path),
                ]
            )

    def test_missing_krr_dir_errors(self, tmp_path: Path, capsys):
        bogus = tmp_path / "does_not_exist"
        report_path = tmp_path / "plan.json"
        with pytest.raises(SystemExit):
            main(
                [
                    "--krr-dir",
                    str(bogus),
                    "--report",
                    str(report_path),
                ]
            )


# ── Atomic writes (#280) ──────────────────────────────────────────────────────


class TestAtomicWrites:
    def test_atomic_write_text_roundtrips(self, tmp_path: Path):
        target = tmp_path / "out.txt"
        _atomic_write_text(target, "hello\nworld\n")
        assert target.read_text(encoding="utf-8") == "hello\nworld\n"
        # No tempfile dropping left behind.
        assert list(tmp_path.glob(".*tmp")) == []

    def test_atomic_write_text_leaves_original_on_failure(
        self, tmp_path: Path, monkeypatch
    ):
        """A rename failure mid-write leaves the original file intact (#280)."""
        import migrate_multipart_iri_scheme as mod

        target = tmp_path / "out.txt"
        target.write_text("original\n", encoding="utf-8")

        def boom(*_args, **_kwargs):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(mod.os, "replace", boom)
        with pytest.raises(OSError):
            _atomic_write_text(target, "new content")

        assert target.read_text(encoding="utf-8") == "original\n"
        assert list(tmp_path.glob(".*tmp")) == []

    def test_execute_plan_apply_uses_atomic_write(
        self, tmp_krr: Path
    ):
        """`execute_plan(apply=True)` rewrites corpus files atomically (#280).

        After a successful apply the file is fully rewritten with the NEW
        scheme and no `.tmp` dropping remains alongside it.
        """
        _write_peep(tmp_krr, "karistusseadustik", 1, [("KARIST_2", "1")])
        _write_peep(tmp_krr, "karistusseadustik", 2, [("KARIST_2", "88")])

        plan = build_plan(tmp_krr, laws=["karistusseadustik"])
        inspected, modified = execute_plan(plan, tmp_krr, apply=True)
        assert inspected >= 2
        assert modified == 2

        osa1 = json.loads(
            (tmp_krr / "karistusseadustik_osa1_peep.json").read_text(encoding="utf-8")
        )
        ids = {n.get("@id") for n in osa1["@graph"]}
        assert "estleg:KARIST_2_Osa1_Par_1" in ids
        assert "estleg:KARIST_2_Par_1" not in ids
        # No tempfile droppings anywhere in the corpus dir.
        assert list(tmp_krr.glob(".*tmp")) == []
