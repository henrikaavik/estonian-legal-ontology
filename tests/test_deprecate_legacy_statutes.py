"""Tests for scripts/deprecate_legacy_statutes.py (#426 legacy-statute deprecation).

The migration marks 37 duplicate legacy-statute act roots with
``owl:deprecated = true`` + ``dcterms:isReplacedBy`` (canonical IRI), keeps the
files on disk, and re-points every *exact-match* inbound reference to the legacy
``rootIri`` across the rest of the corpus to the canonical ``replacedByIri``.

The contract that matters most here is **exact string equality, never
substring**: a short legacy IRI (``estleg:PS_Map_2026``) must never corrupt a
longer IRI that merely shares it as a prefix (``estleg:PSJKS_Map_2026``). The
migration must also be deterministic and idempotent, leave the legacy files'
internal self-references intact, and never touch ``keep`` files.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import deprecate_legacy_statutes as dls

ESTLEG = "https://data.riik.ee/ontology/estleg#"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
def _context() -> dict:
    """A minimal act-peep @context that already declares owl + dcterms."""
    return {
        "estleg": ESTLEG,
        "owl": "http://www.w3.org/2002/07/owl#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "dcterms": "http://purl.org/dc/terms/",
    }


def _legacy_doc(root_iri: str, *, with_context: dict | None = None) -> dict:
    """A legacy act peep: a root act node plus one self-referencing provision."""
    return {
        "@context": _context() if with_context is None else with_context,
        "@graph": [
            {
                "@id": root_iri,
                "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                "rdfs:label": "Legacy statute teemakaardistus",
                "estleg:temporalStatus": "unknown",
            },
            {
                # Internal self-reference: provision points back at the root.
                "@id": f"{root_iri}_Par_1",
                "@type": ["owl:NamedIndividual", "estleg:Provision"],
                "estleg:partOf": {"@id": root_iri},
            },
        ],
    }


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _decisions(entries: list[dict]) -> dict:
    """Wrap deprecation entries in the decisions-file envelope."""
    return {
        "issue": "#426",
        "deprecations": entries,
        "keeps": [],
    }


# ---------------------------------------------------------------------------
# build_repoint_map
# ---------------------------------------------------------------------------
class TestBuildRepointMap:
    def test_maps_root_to_canonical(self):
        deps = [
            {"file": "a_peep.json", "rootIri": "estleg:A", "replacedByIri": "estleg:CANON_A"},
            {"file": "b_peep.json", "rootIri": "estleg:B", "replacedByIri": "estleg:CANON_B"},
        ]
        assert dls.build_repoint_map(deps) == {
            "estleg:A": "estleg:CANON_A",
            "estleg:B": "estleg:CANON_B",
        }

    def test_conflicting_target_raises(self):
        deps = [
            {"file": "a_peep.json", "rootIri": "estleg:A", "replacedByIri": "estleg:X"},
            {"file": "a2_peep.json", "rootIri": "estleg:A", "replacedByIri": "estleg:Y"},
        ]
        try:
            dls.build_repoint_map(deps)
        except ValueError as exc:
            assert "estleg:A" in str(exc)
        else:  # pragma: no cover - explicit failure path
            raise AssertionError("expected ValueError on conflicting target")


# ---------------------------------------------------------------------------
# mark_deprecated_root — field shapes + placement
# ---------------------------------------------------------------------------
class TestMarkDeprecatedRoot:
    def test_sets_bare_boolean_and_isreplacedby_object(self):
        doc = _legacy_doc("estleg:ALKS_Map_2026")
        assert dls.mark_deprecated_root(doc, "estleg:ALKS_Map_2026", "estleg:AS_Map_2026") is True
        root = doc["@graph"][0]
        # owl:deprecated is a BARE boolean literal (house style), not a value object.
        assert root["owl:deprecated"] is True
        assert not isinstance(root["owl:deprecated"], dict)
        # dcterms:isReplacedBy is an @id reference object.
        assert root["dcterms:isReplacedBy"] == {"@id": "estleg:AS_Map_2026"}

    def test_predicates_placed_immediately_after_type(self):
        doc = _legacy_doc("estleg:ALKS_Map_2026")
        dls.mark_deprecated_root(doc, "estleg:ALKS_Map_2026", "estleg:AS_Map_2026")
        keys = list(doc["@graph"][0].keys())
        type_idx = keys.index("@type")
        # Deterministic slot: the two new predicates sit right after @type.
        assert keys[type_idx + 1] == "owl:deprecated"
        assert keys[type_idx + 2] == "dcterms:isReplacedBy"
        # Pre-existing keys are preserved after the insertion.
        assert "rdfs:label" in keys
        assert "estleg:temporalStatus" in keys

    def test_lands_on_correct_root_node_only(self):
        doc = _legacy_doc("estleg:ALKS_Map_2026")
        dls.mark_deprecated_root(doc, "estleg:ALKS_Map_2026", "estleg:AS_Map_2026")
        provision = doc["@graph"][1]
        # The provision node must not be marked.
        assert "owl:deprecated" not in provision
        assert "dcterms:isReplacedBy" not in provision

    def test_unknown_root_iri_is_noop(self):
        doc = _legacy_doc("estleg:ALKS_Map_2026")
        assert dls.mark_deprecated_root(doc, "estleg:NOT_PRESENT", "estleg:AS_Map_2026") is False
        assert "owl:deprecated" not in doc["@graph"][0]

    def test_idempotent_second_mark_is_noop(self):
        doc = _legacy_doc("estleg:ALKS_Map_2026")
        assert dls.mark_deprecated_root(doc, "estleg:ALKS_Map_2026", "estleg:AS_Map_2026") is True
        # Second call: already marked -> no change.
        assert dls.mark_deprecated_root(doc, "estleg:ALKS_Map_2026", "estleg:AS_Map_2026") is False

    def test_adds_missing_owl_and_dcterms_context(self):
        # Context lacking owl/dcterms must get them added (matching canonical IRIs).
        ctx = {"estleg": ESTLEG, "rdfs": "http://www.w3.org/2000/01/rdf-schema#"}
        doc = _legacy_doc("estleg:ALKS_Map_2026", with_context=ctx)
        dls.mark_deprecated_root(doc, "estleg:ALKS_Map_2026", "estleg:AS_Map_2026")
        assert doc["@context"]["owl"] == "http://www.w3.org/2002/07/owl#"
        assert doc["@context"]["dcterms"] == "http://purl.org/dc/terms/"

    def test_existing_context_prefix_not_clobbered(self):
        doc = _legacy_doc("estleg:ALKS_Map_2026")
        before_owl = doc["@context"]["owl"]
        dls.mark_deprecated_root(doc, "estleg:ALKS_Map_2026", "estleg:AS_Map_2026")
        assert doc["@context"]["owl"] == before_owl


# ---------------------------------------------------------------------------
# repoint_value — exact-match recursion + substring safety
# ---------------------------------------------------------------------------
class TestRepointValue:
    MAP = {"estleg:PS_Map_2026": "estleg:eesti_vabariigi_pohiseadus_Map_2026"}

    def test_replaces_exact_id_dict_ref(self):
        value = {"estleg:cites": {"@id": "estleg:PS_Map_2026"}}
        new, count = dls.repoint_value(value, self.MAP)
        assert count == 1
        assert new["estleg:cites"]["@id"] == "estleg:eesti_vabariigi_pohiseadus_Map_2026"

    def test_replaces_exact_ref_in_list(self):
        value = {
            "estleg:references": [
                {"@id": "estleg:PS_Map_2026"},
                {"@id": "estleg:UNRELATED"},
                "estleg:PS_Map_2026",  # plain string occurrence too
            ]
        }
        new, count = dls.repoint_value(value, self.MAP)
        assert count == 2
        assert new["estleg:references"][0]["@id"] == "estleg:eesti_vabariigi_pohiseadus_Map_2026"
        assert new["estleg:references"][1]["@id"] == "estleg:UNRELATED"
        assert new["estleg:references"][2] == "estleg:eesti_vabariigi_pohiseadus_Map_2026"

    def test_no_substring_corruption_of_longer_iri(self):
        # estleg:PS_Map_2026 is a PREFIX of estleg:PSJKS_Map_2026; the longer
        # IRI must survive untouched (the whole point of exact-equality).
        value = {
            "estleg:a": {"@id": "estleg:PSJKS_Map_2026"},
            "estleg:b": "estleg:PS_Map_2026_extra",
            "estleg:c": "prefix-estleg:PS_Map_2026",
        }
        new, count = dls.repoint_value(value, self.MAP)
        assert count == 0
        assert new["estleg:a"]["@id"] == "estleg:PSJKS_Map_2026"
        assert new["estleg:b"] == "estleg:PS_Map_2026_extra"
        assert new["estleg:c"] == "prefix-estleg:PS_Map_2026"

    def test_deeply_nested_refs_are_reached(self):
        value = {"a": {"b": [{"c": {"@id": "estleg:PS_Map_2026"}}]}}
        new, count = dls.repoint_value(value, self.MAP)
        assert count == 1
        assert new["a"]["b"][0]["c"]["@id"] == "estleg:eesti_vabariigi_pohiseadus_Map_2026"

    def test_non_matching_string_untouched(self):
        value = "estleg:SomethingElse"
        new, count = dls.repoint_value(value, self.MAP)
        assert (new, count) == ("estleg:SomethingElse", 0)


# ---------------------------------------------------------------------------
# End-to-end via run() / main() on a mini corpus
# ---------------------------------------------------------------------------
class TestEndToEnd:
    def _build_corpus(self, tmp_path: Path) -> tuple[Path, Path]:
        """Mini corpus + decisions file.

        Legacy cohort:
          * alks (estleg:ALKS_Map_2026)  -> canonical estleg:AS_Map_2026
          * ps   (estleg:PS_Map_2026)    -> canonical estleg:eesti_..._Map_2026

        Plus a longer-IRI peep (PSJKS) that shares PS's IRI as a prefix and a
        ``keep`` peep that references the legacy roots from OUTSIDE the cohort.
        """
        krr = tmp_path / "krr_outputs"
        krr.mkdir(parents=True)

        # --- legacy peep files (in the deprecation cohort) ---
        _write(krr / "alkoholi_seadus_peep.json", _legacy_doc("estleg:ALKS_Map_2026"))
        _write(krr / "pohiseadus_peep.json", _legacy_doc("estleg:PS_Map_2026"))

        # --- PSJKS: a DIFFERENT act whose IRI shares PS_Map_2026 as a prefix ---
        # Not in the cohort; its root @id must survive the re-point untouched.
        _write(
            krr / "pohiseaduslikkuse_jarelevalve_peep.json",
            _legacy_doc("estleg:PSJKS_Map_2026"),
        )

        # --- a "keep" file that cites both legacy roots from outside ---
        _write(
            krr / "citing_keep_peep.json",
            {
                "@context": _context(),
                "@graph": [
                    {
                        "@id": "estleg:KEEP_Map_2026",
                        "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                        "rdfs:label": "A kept statute",
                        "estleg:relatedAct": [
                            {"@id": "estleg:ALKS_Map_2026"},
                            {"@id": "estleg:PS_Map_2026"},
                            {"@id": "estleg:PSJKS_Map_2026"},  # longer IRI: must NOT change
                        ],
                        "estleg:note": "estleg:PS_Map_2026",  # plain string ref too
                    }
                ],
            },
        )

        # --- excluded aggregates that must never be re-pointed ---
        _write(
            krr / "INDEX.json",
            {"@graph": [{"@id": "estleg:PS_Map_2026"}]},
        )
        _write(
            krr / "combined_ontology.jsonld",
            {"@graph": [{"@id": "estleg:PS_Map_2026"}]},
        )

        decisions = tmp_path / "legacy_statute_decisions.json"
        decisions.write_text(
            json.dumps(
                _decisions(
                    [
                        {
                            "file": "alkoholi_seadus_peep.json",
                            "rootIri": "estleg:ALKS_Map_2026",
                            "replacedByFile": "alkoholiseadus_peep.json",
                            "replacedByIri": "estleg:AS_Map_2026",
                        },
                        {
                            "file": "pohiseadus_peep.json",
                            "rootIri": "estleg:PS_Map_2026",
                            "replacedByFile": "eesti_vabariigi_pohiseadus_peep.json",
                            "replacedByIri": "estleg:eesti_vabariigi_pohiseadus_Map_2026",
                        },
                    ]
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return krr, decisions

    # --- deprecation marking lands on the right node with the right shapes ---
    def test_legacy_roots_marked_with_correct_shapes(self, tmp_path):
        krr, decisions = self._build_corpus(tmp_path)
        summary = dls.run(decisions, krr, dry_run=False)
        assert summary["roots_deprecated"] == 2

        alks = json.loads((krr / "alkoholi_seadus_peep.json").read_text(encoding="utf-8"))
        root = alks["@graph"][0]
        assert root["owl:deprecated"] is True
        assert root["dcterms:isReplacedBy"] == {"@id": "estleg:AS_Map_2026"}

    # --- re-point replaces exact-match refs in OTHER files (dict + list) ---
    def test_repoint_rewrites_external_citations(self, tmp_path):
        krr, decisions = self._build_corpus(tmp_path)
        dls.run(decisions, krr, dry_run=False)

        keep = json.loads((krr / "citing_keep_peep.json").read_text(encoding="utf-8"))
        related = keep["@graph"][0]["estleg:relatedAct"]
        # ALKS + PS re-pointed to their canonical IRIs.
        assert related[0]["@id"] == "estleg:AS_Map_2026"
        assert related[1]["@id"] == "estleg:eesti_vabariigi_pohiseadus_Map_2026"
        # plain string ref re-pointed too.
        assert keep["@graph"][0]["estleg:note"] == "estleg:eesti_vabariigi_pohiseadus_Map_2026"

    # --- NO substring corruption: longer IRI sharing the prefix survives ---
    def test_longer_iri_survives_repoint(self, tmp_path):
        krr, decisions = self._build_corpus(tmp_path)
        dls.run(decisions, krr, dry_run=False)

        keep = json.loads((krr / "citing_keep_peep.json").read_text(encoding="utf-8"))
        related = keep["@graph"][0]["estleg:relatedAct"]
        # estleg:PSJKS_Map_2026 must be byte-identical (not corrupted to
        # estleg:eesti_..._Map_2026JKS_Map_2026 or similar).
        assert related[2]["@id"] == "estleg:PSJKS_Map_2026"

        # And the PSJKS peep file's own root @id is untouched.
        psjks = json.loads(
            (krr / "pohiseaduslikkuse_jarelevalve_peep.json").read_text(encoding="utf-8")
        )
        assert psjks["@graph"][0]["@id"] == "estleg:PSJKS_Map_2026"

    # --- legacy file itself is NOT re-pointed (internal self-refs stay) ---
    def test_legacy_file_self_references_preserved(self, tmp_path):
        krr, decisions = self._build_corpus(tmp_path)
        dls.run(decisions, krr, dry_run=False)

        ps = json.loads((krr / "pohiseadus_peep.json").read_text(encoding="utf-8"))
        root, provision = ps["@graph"][0], ps["@graph"][1]
        # Root @id stays as the legacy IRI (file kept on disk, only deprecated).
        assert root["@id"] == "estleg:PS_Map_2026"
        # Provision's partOf back-edge still points at the legacy root, NOT the
        # canonical — the legacy file is excluded from the re-point walk.
        assert provision["estleg:partOf"] == {"@id": "estleg:PS_Map_2026"}

    # --- excluded aggregates (INDEX / combined) are never re-pointed ---
    def test_index_and_combined_excluded(self, tmp_path):
        krr, decisions = self._build_corpus(tmp_path)
        before_index = (krr / "INDEX.json").read_text(encoding="utf-8")
        before_combined = (krr / "combined_ontology.jsonld").read_text(encoding="utf-8")
        summary = dls.run(decisions, krr, dry_run=False)

        assert (krr / "INDEX.json").read_text(encoding="utf-8") == before_index
        assert (krr / "combined_ontology.jsonld").read_text(encoding="utf-8") == before_combined
        # Neither excluded file appears in the per-file report.
        assert "INDEX.json" not in summary["per_file_counts"]
        assert "combined_ontology.jsonld" not in summary["per_file_counts"]

    # --- keeps files that don't reference legacy roots stay byte-identical ---
    def test_unreferencing_keep_file_byte_stable(self, tmp_path):
        krr, decisions = self._build_corpus(tmp_path)
        # A keep file that references NO legacy root.
        clean = krr / "clean_keep_peep.json"
        _write(clean, _legacy_doc("estleg:CLEAN_Map_2026"))
        before = clean.read_text(encoding="utf-8")
        dls.run(decisions, krr, dry_run=False)
        assert clean.read_text(encoding="utf-8") == before

    # --- idempotency: a second run is a complete no-op ---
    def test_idempotent_second_run_zero_changes(self, tmp_path):
        krr, decisions = self._build_corpus(tmp_path)
        dls.run(decisions, krr, dry_run=False)
        snapshot = {
            p: p.read_text(encoding="utf-8")
            for p in krr.glob("*")
            if p.is_file()
        }
        summary2 = dls.run(decisions, krr, dry_run=False)
        assert summary2["roots_deprecated"] == 0
        assert summary2["files_repointed"] == 0
        assert summary2["total_replacements"] == 0
        # Byte-stable: nothing rewritten.
        for p, text in snapshot.items():
            assert p.read_text(encoding="utf-8") == text

    # --- dry-run writes nothing but still reports the counts ---
    def test_dry_run_writes_nothing_but_reports(self, tmp_path):
        krr, decisions = self._build_corpus(tmp_path)
        before = {
            p: p.read_text(encoding="utf-8")
            for p in krr.glob("*")
            if p.is_file()
        }
        summary = dls.run(decisions, krr, dry_run=True)
        # Reports what WOULD change.
        assert summary["roots_deprecated"] == 2
        assert summary["files_repointed"] == 1  # only citing_keep_peep.json
        assert summary["total_replacements"] == 3  # 2 in list + 1 plain string
        # But nothing on disk changed.
        for p, text in before.items():
            assert p.read_text(encoding="utf-8") == text


# ---------------------------------------------------------------------------
# main() CLI wiring
# ---------------------------------------------------------------------------
class TestMainCli:
    def test_main_runs_and_reports(self, tmp_path, capsys):
        krr = tmp_path / "krr_outputs"
        krr.mkdir(parents=True)
        _write(krr / "alkoholi_seadus_peep.json", _legacy_doc("estleg:ALKS_Map_2026"))
        _write(
            krr / "citing_keep_peep.json",
            {
                "@context": _context(),
                "@graph": [
                    {
                        "@id": "estleg:KEEP_Map_2026",
                        "@type": ["owl:Ontology", "estleg:Act"],
                        "estleg:relatedAct": {"@id": "estleg:ALKS_Map_2026"},
                    }
                ],
            },
        )
        decisions = tmp_path / "legacy_statute_decisions.json"
        decisions.write_text(
            json.dumps(
                _decisions(
                    [
                        {
                            "file": "alkoholi_seadus_peep.json",
                            "rootIri": "estleg:ALKS_Map_2026",
                            "replacedByFile": "alkoholiseadus_peep.json",
                            "replacedByIri": "estleg:AS_Map_2026",
                        }
                    ]
                )
            ),
            encoding="utf-8",
        )

        rc = dls.main(["--decisions", str(decisions), "--krr-dir", str(krr)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Roots deprecated:" in out
        assert "Files re-pointed:" in out

    def test_main_missing_decisions_errors(self, tmp_path, capsys):
        krr = tmp_path / "krr_outputs"
        krr.mkdir(parents=True)
        rc = dls.main(["--decisions", str(tmp_path / "nope.json"), "--krr-dir", str(krr)])
        assert rc == 1
        assert "decisions file not found" in capsys.readouterr().out

    def test_main_missing_krr_dir_errors(self, tmp_path, capsys):
        decisions = tmp_path / "d.json"
        decisions.write_text(json.dumps(_decisions([])), encoding="utf-8")
        rc = dls.main(["--decisions", str(decisions), "--krr-dir", str(tmp_path / "nope")])
        assert rc == 1
        assert "not a directory" in capsys.readouterr().out

    def test_main_dry_run_flag(self, tmp_path, capsys):
        krr = tmp_path / "krr_outputs"
        krr.mkdir(parents=True)
        _write(krr / "alkoholi_seadus_peep.json", _legacy_doc("estleg:ALKS_Map_2026"))
        before = (krr / "alkoholi_seadus_peep.json").read_text(encoding="utf-8")
        decisions = tmp_path / "legacy_statute_decisions.json"
        decisions.write_text(
            json.dumps(
                _decisions(
                    [
                        {
                            "file": "alkoholi_seadus_peep.json",
                            "rootIri": "estleg:ALKS_Map_2026",
                            "replacedByFile": "alkoholiseadus_peep.json",
                            "replacedByIri": "estleg:AS_Map_2026",
                        }
                    ]
                )
            ),
            encoding="utf-8",
        )
        rc = dls.main(
            ["--decisions", str(decisions), "--krr-dir", str(krr), "--dry-run"]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "--dry-run (no files written)" in out
        # File unchanged on disk.
        assert (krr / "alkoholi_seadus_peep.json").read_text(encoding="utf-8") == before
