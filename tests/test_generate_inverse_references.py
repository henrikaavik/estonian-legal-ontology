"""Tests for scripts/generate_inverse_references.py — Layer 2b additions
plus #157 alias-resolution refusal.

Covers:
  * TestActLevelIndex — Round-3 review item #7: collect_all_references
    must index act-level nodes (owl:Ontology, estleg:Law, etc.) so KOV
    body-text references that resolve to act-level IRIs gain
    referencedBy on the target file.
  * TestImplementedByBodyTextExclusion — body-text estleg:references
    must NOT feed estleg:implementedBy. implementedBy is a filtered
    projection from preamble citations only (issuedUnder + Citation
    targets).
  * TestImplementedByIdempotency — clear_stale_implemented_by removes
    pre-existing implementedBy/implementedByCount before each apply
    pass so removed source acts or corrected preamble parses cannot
    leave stale triples.
  * TestAliasAmbiguousRefused — #157: when an alias prefix matches
    multiple canonical prefixes, refuse to resolve and surface the
    ambiguity rather than silently picking one.
  * TestVerifySymmetryCategorisation — #157: verify_symmetry must
    emit ``unresolved-after-alias`` and ``genuine-missing-back-link``
    categories instead of the legacy fold-everything-into-"missing"
    label.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


class TestActLevelIndex:
    def test_act_level_iri_resolves_in_iri_to_file(self, tmp_path, monkeypatch):
        """A KOV body-text ref that resolves to estleg:Reg_X_Map_2026
        (an act-level IRI) must be writable as referencedBy on that
        target file. collect_all_references must index act-level
        nodes for this to work."""
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        # Source act: KOV reg with body-text ref to a SISTER KOV act
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "src_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_Src_Map_2024",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_TLN_Src_Par_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:references": [
                     {"@id": "estleg:Reg_TLN_Tgt_Map_2020"},   # ACT-LEVEL
                 ]}
            ],
        }), encoding="utf-8")

        # Target KOV act
        (kov / "tgt_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_TLN_Tgt_Map_2020",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (None, 0)

        # Target should now have referencedBy from the source act
        with open(kov / "tgt_peep.json", "r", encoding="utf-8") as fh:
            tgt = json.load(fh)
        act = tgt["@graph"][0]
        assert "estleg:referencedBy" in act
        rb = act["estleg:referencedBy"]
        if isinstance(rb, dict):
            rb = [rb]
        assert any(r["@id"] == "estleg:Reg_TLN_Src_Par_1" for r in rb)


class TestImplementedByBodyTextExclusion:
    def test_body_text_references_do_not_feed_implementedBy(
        self, tmp_path, monkeypatch
    ):
        """Run main() over a graph where source act A has both body-text
        references to target X and issuedUnder to target Y. Verify X
        does NOT gain implementedBy; Y does."""
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        # Source KOV act with both kinds of links
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "source_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_A_Map",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"],
                 "estleg:issuedUnder": [{"@id": "estleg:Y_Map_2026"}]},
                {"@id": "estleg:Reg_A_Par_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:paragrahv": "§ 1",
                 # Body-text references to X — must NOT contribute
                 # to implementedBy on X.
                 "estleg:references": [{"@id": "estleg:X_Map_2026_Par_5"}]}
            ],
        }), encoding="utf-8")

        # Target X (a law)
        (krr / "x_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:X_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:X_Map_2026_Par_5",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:paragrahv": "§ 5"}
            ],
        }), encoding="utf-8")

        # Target Y (a law)
        (krr / "y_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Y_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"]}
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (None, 0)

        # X should have referencedBy from body-text but NOT implementedBy
        with open(krr / "x_peep.json", "r", encoding="utf-8") as fh:
            x_doc = json.load(fh)
        x_par_5 = next(n for n in x_doc["@graph"]
                       if n.get("@id") == "estleg:X_Map_2026_Par_5")
        # Body-text reference produces referencedBy — that's the
        # existing inverse, unchanged
        assert "estleg:referencedBy" in x_par_5
        # But X must NOT have implementedBy from body-text
        assert "estleg:implementedBy" not in x_par_5

        # Y should have implementedBy because A.issuedUnder pointed at it
        with open(krr / "y_peep.json", "r", encoding="utf-8") as fh:
            y_doc = json.load(fh)
        y_act = y_doc["@graph"][0]
        assert "estleg:implementedBy" in y_act
        impls = y_act["estleg:implementedBy"]
        if isinstance(impls, dict):
            impls = [impls]
        assert any(e["@id"] == "estleg:Reg_A_Map" for e in impls)


class TestImplementedByIdempotency:
    def test_stale_implemented_by_cleared_before_reapply(
        self, tmp_path, monkeypatch
    ):
        """Round 4 review item #6: a target that no longer appears in
        the preamble_inverse map MUST lose its implementedBy /
        implementedByCount triples on the next run. Without
        clear_stale_implemented_by(), the apply-only path would
        leave stale triples in place forever."""
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        # Target peep with PRE-EXISTING (stale) implementedBy/Count
        # that no other act in this test references via issuedUnder.
        (krr / "stale_target_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Stale_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"],
                 "estleg:implementedBy": [
                     {"@id": "estleg:Reg_GhostSource_Map_2025"}
                 ],
                 "estleg:implementedByCount": 1}
            ],
        }), encoding="utf-8")

        # A live source act with no issuedUnder, no Citation — so
        # preamble_inverse will be empty.
        kov = krr / "regulations" / "kov" / "tallinn"
        kov.mkdir(parents=True)
        (kov / "live_source_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_Live_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]}
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (None, 0)

        # The stale target must have lost both triples.
        with open(krr / "stale_target_peep.json", "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        target = saved["@graph"][0]
        assert "estleg:implementedBy" not in target
        assert "estleg:implementedByCount" not in target


# ---------------------------------------------------------------------------
# Issue #157 — alias resolution must refuse on ambiguous matches
# ---------------------------------------------------------------------------


class TestAliasResolveAmbiguous:
    """Direct unit-test of ``_resolve_alias`` with an ambiguous index.

    When IRI_PREFIX_ALIASES maps an alias to multiple canonical
    prefixes and >1 of those canonicals carry the requested paragraph
    number, _resolve_alias must return ``AliasResolution`` with
    ``canonical=None`` and ``ambiguous_candidates`` populated.
    """

    def test_resolve_alias_refuses_when_multiple_canonicals_match(
        self, monkeypatch
    ):
        import generate_inverse_references as mod

        # Two canonical IRIs both carry Par_14: ambiguous.
        iri_to_file = {
            "estleg:VOS_Par_14": Path("/tmp/vos_peep.json"),
            "estleg:VOS3_Par_14": Path("/tmp/vos3_peep.json"),
        }
        prefix_par_index = {
            "VOS": {"14": "estleg:VOS_Par_14"},
            "VOS3": {"14": "estleg:VOS3_Par_14"},
        }
        # Inject the alias rule
        monkeypatch.setitem(
            mod.IRI_PREFIX_ALIASES,
            "Vlaigusseadus",
            ["VOS", "VOS3"],
        )

        res = mod._resolve_alias(
            "estleg:Vlaigusseadus_Par_14", iri_to_file, prefix_par_index,
        )
        assert res.canonical is None
        assert sorted(res.ambiguous_candidates) == [
            "estleg:VOS3_Par_14",
            "estleg:VOS_Par_14",
        ]

    def test_resolve_alias_resolves_when_only_one_canonical_matches(
        self, monkeypatch
    ):
        import generate_inverse_references as mod

        iri_to_file = {
            "estleg:VOS_Par_14": Path("/tmp/vos_peep.json"),
            "estleg:VOS3_Par_99": Path("/tmp/vos3_peep.json"),
        }
        prefix_par_index = {
            "VOS": {"14": "estleg:VOS_Par_14"},
            "VOS3": {"99": "estleg:VOS3_Par_99"},
        }
        monkeypatch.setitem(
            mod.IRI_PREFIX_ALIASES,
            "Vlaigusseadus",
            ["VOS", "VOS3"],
        )

        res = mod._resolve_alias(
            "estleg:Vlaigusseadus_Par_14", iri_to_file, prefix_par_index,
        )
        assert res.canonical == "estleg:VOS_Par_14"
        assert res.ambiguous_candidates == []


class TestAliasAmbiguousRefused:
    """End-to-end: when two canonical prefixes carry the same paragraph
    and an alias claims to point at it, the run must NOT write
    referencedBy on either canonical, and the report must record the
    refusal.
    """

    def test_main_records_ambiguous_alias_in_report(
        self, tmp_path, monkeypatch
    ):
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        # Source act references an alias-prefixed IRI.
        (krr / "source_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:Reg_X_Par_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:references": [
                     {"@id": "estleg:Vlaigusseadus_Par_14"},
                 ]}
            ],
        }), encoding="utf-8")

        # Two canonical targets: VOS_Par_14 AND VOS3_Par_14 (ambiguous).
        (krr / "vos_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:VOS_Map", "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:VOS_Par_14", "@type": ["owl:NamedIndividual"]},
            ],
        }), encoding="utf-8")
        (krr / "vos3_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:VOS3_Map", "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:VOS3_Par_14", "@type": ["owl:NamedIndividual"]},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        # Force the alias to be ambiguous (two canonicals share Par_14)
        monkeypatch.setitem(
            mod.IRI_PREFIX_ALIASES, "Vlaigusseadus", ["VOS", "VOS3"],
        )

        rc = mod.main()
        assert rc in (None, 0)

        # Neither canonical may have gained referencedBy from the alias.
        with open(krr / "vos_peep.json") as fh:
            vos_doc = json.load(fh)
        with open(krr / "vos3_peep.json") as fh:
            vos3_doc = json.load(fh)
        for doc in (vos_doc, vos3_doc):
            par = next(n for n in doc["@graph"]
                       if n.get("@id", "").endswith("_Par_14"))
            assert "estleg:referencedBy" not in par, (
                "ambiguous alias must not silently attribute back-links"
            )

        # The report must record the refusal.
        with open(krr / "inverse_references_report.json") as fh:
            report = json.load(fh)
        assert "alias_ambiguous" in report
        assert "estleg:Vlaigusseadus_Par_14" in report["alias_ambiguous"]
        assert sorted(report["alias_ambiguous"]["estleg:Vlaigusseadus_Par_14"]) == [
            "estleg:VOS3_Par_14",
            "estleg:VOS_Par_14",
        ]
        assert report["summary"]["alias_ambiguous_refused"] >= 1


# ---------------------------------------------------------------------------
# Issue #157 — verify_symmetry categorises mismatches
# ---------------------------------------------------------------------------


class TestVerifySymmetryCategorisation:
    def test_unresolved_after_alias_emitted_when_alias_unknown_paragraph(
        self, tmp_path, monkeypatch
    ):
        """A forward ref using an alias prefix where no canonical
        carries the paragraph number must produce a
        ``unresolved-after-alias`` mismatch (NOT ``does not exist``).
        """
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        # Source ref to an alias prefix
        (krr / "src_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Src_Map", "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:Src_Par_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:references": [
                     {"@id": "estleg:Vlaigusseadus_Par_999"},
                 ]}
            ],
        }), encoding="utf-8")
        # No canonical carries Par_999
        (krr / "vos_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:VOS_Map", "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:VOS_Par_14", "@type": ["owl:NamedIndividual"]},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        # Vlaigusseadus is a known alias here
        monkeypatch.setitem(mod.IRI_PREFIX_ALIASES, "Vlaigusseadus", ["VOS"])

        mismatches = mod.verify_symmetry()
        # Find our row
        offending = [m for m in mismatches
                     if m.get("source") == "estleg:Src_Par_1"]
        assert offending
        assert offending[0]["issue"] == "unresolved-after-alias"

    def test_genuine_missing_back_link_emitted_when_canonical_exists(
        self, tmp_path, monkeypatch
    ):
        """When a forward ref resolves cleanly to a canonical IRI and
        the canonical exists but lacks the back-link, the mismatch
        must be ``genuine-missing-back-link`` — not the legacy
        "missing referencedBy" string.
        """
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        # Source has a forward reference but the target is missing
        # the back-link.
        (krr / "src_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Src_Map", "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:Src_Par_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:references": [
                     {"@id": "estleg:Tgt_Par_5"},
                 ]}
            ],
        }), encoding="utf-8")
        # Target node exists but does NOT carry estleg:referencedBy
        # for Src_Par_1. This simulates an out-of-sync state.
        (krr / "tgt_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Tgt_Map", "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:Tgt_Par_5",
                 "@type": ["owl:NamedIndividual"]},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        mismatches = mod.verify_symmetry()
        offending = [m for m in mismatches
                     if m.get("source") == "estleg:Src_Par_1"]
        assert offending
        assert offending[0]["issue"] == "genuine-missing-back-link"
        assert offending[0].get("resolvedTarget") == "estleg:Tgt_Par_5"


# ---------------------------------------------------------------------------
# Issue #376 — the local non-atomic save_json must be replaced by the
# atomic estleg_common.save_json (tempfile + os.replace).
# ---------------------------------------------------------------------------


class TestAtomicSaveJsonImport:
    def test_module_save_json_is_estleg_common_atomic(self):
        """generate_inverse_references must NOT shadow save_json with a
        local non-atomic open('w') variant; it must reuse the atomic
        estleg_common.save_json so a crash mid-write never truncates a
        peep file to 0 bytes (#376)."""
        import generate_inverse_references as mod
        import estleg_common

        # Same function object — proves the local shadow is gone and the
        # atomic implementation is in use everywhere save_json is called.
        assert mod.save_json is estleg_common.save_json

    def test_atomic_save_json_writes_via_replace(self, tmp_path):
        """Sanity check that the imported save_json actually performs an
        atomic write: no leftover .tmp droppings and a trailing newline."""
        import generate_inverse_references as mod

        target = tmp_path / "out.json"
        mod.save_json(target, {"@graph": [{"@id": "estleg:X"}]})
        text = target.read_text(encoding="utf-8")
        assert text.endswith("\n")
        # No tempfile droppings left behind.
        leftovers = list(tmp_path.glob(".*tmp")) + list(tmp_path.glob("*.tmp"))
        assert leftovers == []


# ---------------------------------------------------------------------------
# Issue #345 — estleg:hasVersion (inverse of estleg:versionOf) must be
# materialised on each LegalProvision, one per ProvisionVersion.
# ---------------------------------------------------------------------------


def _write_versions_corpus(krr):
    """Build a minimal corpus:

      * a peep file holding two LegalProvision nodes (P1, P2);
      * a ProvisionVersions sidecar with three version nodes:
          - P1 has two versions (v1 older, v2 head),
          - P2 has one version (v1).

    Returns (provision_peep_path, sidecar_path).
    """
    versions_dir = krr / "provision_versions"
    versions_dir.mkdir(parents=True)

    # Canonical LegalProvision nodes live in the peep corpus.
    prov_peep = krr / "demo_law_peep.json"
    prov_peep.write_text(json.dumps({
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "owl": "http://www.w3.org/2002/07/owl#"},
        "@graph": [
            {"@id": "estleg:DEMO_Map_2026",
             "@type": ["owl:Ontology", "estleg:Law"]},
            {"@id": "estleg:DEMO_Par_1",
             "@type": ["owl:NamedIndividual"],
             "estleg:paragrahv": "§ 1"},
            {"@id": "estleg:DEMO_Par_2",
             "@type": ["owl:NamedIndividual"],
             "estleg:paragrahv": "§ 2"},
        ],
    }), encoding="utf-8")

    # ProvisionVersion sidecar (the estleg:versionOf source).
    sidecar = versions_dir / "demo_law.jsonld"
    sidecar.write_text(json.dumps({
        "@context": {"estleg": "https://w3id.org/estleg/",
                     "owl": "http://www.w3.org/2002/07/owl#"},
        "@graph": [
            {"@id": "estleg:ProvisionVersions_demo_law_Map",
             "@type": ["owl:Ontology"]},
            {"@id": "estleg:DEMO_Par_1_v100",
             "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
             "estleg:versionOf": {"@id": "estleg:DEMO_Par_1"},
             "estleg:supersededByVersion": {"@id": "estleg:DEMO_Par_1_v200"}},
            {"@id": "estleg:DEMO_Par_1_v200",
             "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
             "estleg:versionOf": {"@id": "estleg:DEMO_Par_1"}},
            {"@id": "estleg:DEMO_Par_2_v100",
             "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
             "estleg:versionOf": {"@id": "estleg:DEMO_Par_2"}},
        ],
    }), encoding="utf-8")

    return prov_peep, sidecar


class TestCollectVersionInverse:
    def test_groups_versions_by_provision_in_order(self, tmp_path, monkeypatch):
        import generate_inverse_references as mod

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        _write_versions_corpus(krr)
        monkeypatch.setattr(mod, "VERSIONS_DIR", krr / "provision_versions")

        inverse = mod.collect_version_inverse()
        assert inverse == {
            "estleg:DEMO_Par_1": [
                "estleg:DEMO_Par_1_v100",
                "estleg:DEMO_Par_1_v200",
            ],
            "estleg:DEMO_Par_2": ["estleg:DEMO_Par_2_v100"],
        }

    def test_missing_versions_dir_returns_empty(self, tmp_path, monkeypatch):
        import generate_inverse_references as mod

        monkeypatch.setattr(
            mod, "VERSIONS_DIR", tmp_path / "does_not_exist",
        )
        assert mod.collect_version_inverse() == {}

    def test_dedupes_duplicate_version_edge(self, tmp_path, monkeypatch):
        """If a version IRI appears twice pointing at the same provision
        (e.g. an accidentally merged sidecar), it is listed only once."""
        import generate_inverse_references as mod

        krr = tmp_path / "krr_outputs"
        vd = krr / "provision_versions"
        vd.mkdir(parents=True)
        (vd / "dupe.jsonld").write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/"},
            "@graph": [
                {"@id": "estleg:D_Par_1_v1",
                 "estleg:versionOf": {"@id": "estleg:D_Par_1"}},
                {"@id": "estleg:D_Par_1_v1",
                 "estleg:versionOf": {"@id": "estleg:D_Par_1"}},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "VERSIONS_DIR", vd)

        assert mod.collect_version_inverse() == {
            "estleg:D_Par_1": ["estleg:D_Par_1_v1"],
        }


class TestApplyHasVersion:
    def test_writes_has_version_on_provision_nodes(self, tmp_path):
        import generate_inverse_references as mod

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        prov_peep, _ = _write_versions_corpus(krr)

        version_inverse = {
            "estleg:DEMO_Par_1": [
                "estleg:DEMO_Par_1_v100",
                "estleg:DEMO_Par_1_v200",
            ],
            "estleg:DEMO_Par_2": ["estleg:DEMO_Par_2_v100"],
        }
        iri_to_file = {
            "estleg:DEMO_Par_1": prov_peep,
            "estleg:DEMO_Par_2": prov_peep,
        }

        counts, unresolved = mod.apply_has_version(version_inverse, iri_to_file)
        assert unresolved == []
        assert counts == {prov_peep: 2}

        saved = json.loads(prov_peep.read_text(encoding="utf-8"))
        by_id = {n["@id"]: n for n in saved["@graph"]}
        p1 = by_id["estleg:DEMO_Par_1"]
        assert p1["estleg:hasVersion"] == [
            {"@id": "estleg:DEMO_Par_1_v100"},
            {"@id": "estleg:DEMO_Par_1_v200"},
        ]
        p2 = by_id["estleg:DEMO_Par_2"]
        assert p2["estleg:hasVersion"] == [{"@id": "estleg:DEMO_Par_2_v100"}]

    def test_unresolved_provision_is_reported_not_written(self, tmp_path):
        """A provision referenced by a sidecar but absent from any peep
        file must be returned as unresolved, never silently dropped."""
        import generate_inverse_references as mod

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        prov_peep, _ = _write_versions_corpus(krr)

        version_inverse = {
            "estleg:GHOST_Par_9": ["estleg:GHOST_Par_9_v1"],
        }
        counts, unresolved = mod.apply_has_version(version_inverse, {})
        assert counts == {}
        assert unresolved == ["estleg:GHOST_Par_9"]


class TestHasVersionRoundTrip:
    def test_main_materialises_has_version_symmetric_to_version_of(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: after main(), every ProvisionVersion's
        estleg:versionOf -> P has a matching estleg:hasVersion -> V on P
        (round-trip symmetry, #345)."""
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        prov_peep, sidecar = _write_versions_corpus(krr)

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "VERSIONS_DIR", krr / "provision_versions")
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (None, 0)

        # Rebuild the two directions and assert they are mutually inverse.
        sc = json.loads(sidecar.read_text(encoding="utf-8"))
        version_of = {}  # version_iri -> provision_iri
        for n in sc["@graph"]:
            vof = n.get("estleg:versionOf")
            if isinstance(vof, dict):
                version_of[n["@id"]] = vof["@id"]

        peep = json.loads(prov_peep.read_text(encoding="utf-8"))
        has_version = {}  # provision_iri -> set(version_iri)
        for n in peep["@graph"]:
            hv = n.get("estleg:hasVersion")
            if hv is None:
                continue
            if isinstance(hv, dict):
                hv = [hv]
            has_version[n["@id"]] = {e["@id"] for e in hv}

        # Forward direction: every versionOf edge has a hasVersion back-edge.
        for v, p in version_of.items():
            assert p in has_version, f"{p} lost its hasVersion set"
            assert v in has_version[p], f"hasVersion {p} missing {v}"

        # Reverse direction: every hasVersion edge has a versionOf back-edge.
        for p, versions in has_version.items():
            for v in versions:
                assert version_of.get(v) == p, (
                    f"hasVersion {p} -> {v} has no matching versionOf"
                )

        # Concrete counts: 3 version edges across 2 provisions.
        assert has_version["estleg:DEMO_Par_1"] == {
            "estleg:DEMO_Par_1_v100", "estleg:DEMO_Par_1_v200",
        }
        assert has_version["estleg:DEMO_Par_2"] == {"estleg:DEMO_Par_2_v100"}

        # The report records the version-inverse stats.
        report = json.loads(
            (krr / "inverse_references_report.json").read_text(encoding="utf-8")
        )
        summ = report["summary"]
        assert summ["provisions_with_versions"] == 2
        assert summ["total_version_edges"] == 3
        assert summ["has_version_nodes_updated"] == 2
        assert summ["has_version_unresolved_provisions"] == 0

    def test_main_has_version_is_idempotent(self, tmp_path, monkeypatch):
        """Running main() twice produces identical hasVersion (no
        duplication, no stale accumulation) — the clear pass strips the
        prior run's triples before re-applying."""
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        prov_peep, _ = _write_versions_corpus(krr)

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "VERSIONS_DIR", krr / "provision_versions")
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        assert mod.main() in (None, 0)
        first = prov_peep.read_text(encoding="utf-8")
        assert mod.main() in (None, 0)
        second = prov_peep.read_text(encoding="utf-8")
        assert first == second

        peep = json.loads(second)
        p1 = next(n for n in peep["@graph"]
                  if n.get("@id") == "estleg:DEMO_Par_1")
        # Exactly two entries, not four.
        assert p1["estleg:hasVersion"] == [
            {"@id": "estleg:DEMO_Par_1_v100"},
            {"@id": "estleg:DEMO_Par_1_v200"},
        ]

    def test_main_clears_stale_has_version(self, tmp_path, monkeypatch):
        """A provision carrying a pre-existing (stale) hasVersion pointing
        at a version that no longer exists in any sidecar must lose it on
        the next run."""
        import generate_inverse_references as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        # Provision with a stale hasVersion; the sidecar dir is EMPTY, so
        # collect_version_inverse() returns nothing for it.
        (krr / "provision_versions").mkdir(parents=True)
        prov_peep = krr / "stale_peep.json"
        prov_peep.write_text(json.dumps({
            "@context": {"estleg": "https://w3id.org/estleg/",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:STALE_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:STALE_Par_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:hasVersion": [
                     {"@id": "estleg:STALE_Par_1_vGHOST"}
                 ]},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "VERSIONS_DIR", krr / "provision_versions")
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        assert mod.main() in (None, 0)

        peep = json.loads(prov_peep.read_text(encoding="utf-8"))
        p1 = next(n for n in peep["@graph"]
                  if n.get("@id") == "estleg:STALE_Par_1")
        assert "estleg:hasVersion" not in p1


def test_require_cross_reference_report_fails_when_index_present_and_report_missing(
    tmp_path, monkeypatch
):
    """#470: production tree (has INDEX.json) without the forward-ref report."""
    import generate_inverse_references as mod

    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    (krr / "INDEX.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mod, "KRR_DIR", krr)
    with pytest.raises(SystemExit, match="cross_references_report"):
        mod.require_cross_reference_report()


def test_require_cross_reference_report_fails_on_zero_citations(tmp_path):
    import generate_inverse_references as mod

    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    (krr / "cross_references_report.json").write_text(
        json.dumps({"summary": {"total_citations_found": 0}}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="zero citations"):
        mod.require_cross_reference_report(krr_dir=krr)


def test_require_cross_reference_report_accepts_populated_report(tmp_path):
    import generate_inverse_references as mod

    krr = tmp_path / "krr_outputs"
    krr.mkdir()
    (krr / "cross_references_report.json").write_text(
        json.dumps({"summary": {"total_citations_found": 12}}),
        encoding="utf-8",
    )
    mod.require_cross_reference_report(krr_dir=krr)


# ---------------------------------------------------------------------------
# Issue #606 — count gate that loudly surfaces estleg:hasVersion
# under-materialisation (stale ProvisionVersion sidecars whose versionOf @ids
# predate the #345 IRI scheme) instead of silently emitting 13 / 38,512.
# ---------------------------------------------------------------------------


class TestHasVersionResolutionDegraded:
    """Pure unit tests for ``hasversion_resolution_degraded`` (#606)."""

    def test_healthy_resolution_not_degraded(self):
        import generate_inverse_references as mod

        # All provisions resolved (empty unresolved) -> healthy.
        version_inverse = {
            "estleg:DEMO_Par_1": ["estleg:DEMO_Par_1_v1"],
            "estleg:DEMO_Par_2": ["estleg:DEMO_Par_2_v1"],
        }
        assert mod.hasversion_resolution_degraded(version_inverse, []) is False

        # Most provisions resolved (1 of 10 unresolved -> 0.9) -> healthy.
        many = {f"estleg:P_{i}": [f"estleg:P_{i}_v1"] for i in range(10)}
        assert mod.hasversion_resolution_degraded(many, ["estleg:P_0"]) is False

    def test_degraded_resolution_flagged(self):
        import generate_inverse_references as mod

        # The real-world symptom: 13 of 38,512 provisions resolved.
        version_inverse = {
            f"estleg:P_{i}": [f"estleg:P_{i}_v1"] for i in range(38512)
        }
        unresolved = [f"estleg:P_{i}" for i in range(13, 38512)]  # 38,499
        assert len(unresolved) == 38499
        assert (
            mod.hasversion_resolution_degraded(version_inverse, unresolved)
            is True
        )

    def test_empty_version_inverse_not_degraded(self):
        import generate_inverse_references as mod

        # No targets at all -> no signal, and no division by zero.
        assert mod.hasversion_resolution_degraded({}, []) is False
