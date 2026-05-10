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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:VOS_Map", "@type": ["owl:Ontology", "estleg:Law"]},
                {"@id": "estleg:VOS_Par_14", "@type": ["owl:NamedIndividual"]},
            ],
        }), encoding="utf-8")
        (krr / "vos3_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
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
