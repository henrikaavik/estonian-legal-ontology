"""Tests for scripts/generate_inverse_references.py — Layer 2b additions.

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
