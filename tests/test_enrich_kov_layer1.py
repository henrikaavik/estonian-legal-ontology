"""End-to-end tests for KOV layer 1 enrichment."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from enrich_kov_layer1 import build_municipality_doc

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer1"
MIN_MUNICIPALITIES = FIXTURE_DIR / "municipalities_min.json"
SAMPLE_KOV_ACT = FIXTURE_DIR / "sample_kov_act.json"
SAMPLE_LAW = FIXTURE_DIR / "sample_law.json"


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


class TestEnrichKovActFile:
    @pytest.fixture
    def issuer(self):
        return {
            "slug": "tallinna_linnavolikogu",
            "displayName": "Tallinna Linnavolikogu",
            "bodyType": "volikogu",
            "currentMunicipalityCode": "0784",
            "mappingSource": "auto-match",
            "mappingEvidence": "",
            "historicalMunicipalityName": "Tallinn",
        }

    @pytest.fixture
    def temp_act(self, tmp_path):
        dest = tmp_path / "act.json"
        shutil.copy(SAMPLE_KOV_ACT, dest)
        return dest

    def test_act_node_enriched(self, temp_act, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        enrich_kov_act_file(temp_act, issuer)
        with open(temp_act, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        act = next(n for n in doc["@graph"]
                   if n["@id"] == "estleg:Reg_1014955_Map_2026")
        assert act["estleg:enactedBy"] == {"@id": "estleg:Issuer_tallinna_linnavolikogu"}
        assert act["estleg:enactedByMunicipality"] == {"@id": "estleg:Municipality_EHAK_0784"}
        assert act["estleg:titleNormalized"] == "jaatmehoolduseeskiri"
        # Direct Act type stamped (so partOfAct SHACL doesn't need inference)
        assert "estleg:Act" in act["@type"]
        # MunicipalRegulation preserved
        assert "estleg:MunicipalRegulation" in act["@type"]

    def test_provisions_enriched(self, temp_act, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        enrich_kov_act_file(temp_act, issuer)
        with open(temp_act, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        provisions = [n for n in doc["@graph"] if "estleg:paragrahv" in n]
        assert len(provisions) == 2
        for p in provisions:
            assert "estleg:KovProvision" in p["@type"]
            assert p["estleg:enactedBy"] == {"@id": "estleg:Issuer_tallinna_linnavolikogu"}
            assert p["estleg:enactedByMunicipality"] == {"@id": "estleg:Municipality_EHAK_0784"}
            assert p["estleg:partOfAct"] == {"@id": "estleg:Reg_1014955_Map_2026"}

    def test_idempotent(self, temp_act, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        enrich_kov_act_file(temp_act, issuer)
        once = json.load(open(temp_act, encoding="utf-8"))
        enrich_kov_act_file(temp_act, issuer)
        twice = json.load(open(temp_act, encoding="utf-8"))
        assert once == twice  # second run is a no-op

    def test_missing_act_node_raises(self, tmp_path, issuer):
        # A KOV file shape that lacks any estleg:MunicipalRegulation node
        # must fail loudly — not silently skip while the orchestrator
        # increments its enriched-count.
        from enrich_kov_layer1 import enrich_kov_act_file
        bad = tmp_path / "broken.json"
        bad.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:NotAnAct_Map_2026",
                 "@type": ["owl:Ontology"],
                 "rdfs:label": "garbage"}
            ],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="no estleg:MunicipalRegulation"):
            enrich_kov_act_file(bad, issuer)

    def test_multiple_act_nodes_raise(self, tmp_path, issuer):
        # Two MunicipalRegulation nodes in one file must also fail —
        # otherwise provisions would all link to whichever comes first
        # and partial enrichment would corrupt the file silently.
        from enrich_kov_layer1 import enrich_kov_act_file
        bad = tmp_path / "two_acts.json"
        bad.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_2_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
            ],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="found 2"):
            enrich_kov_act_file(bad, issuer)


class TestStampLawType:
    @pytest.fixture
    def temp_law(self, tmp_path):
        dest = tmp_path / "law.json"
        shutil.copy(SAMPLE_LAW, dest)
        return dest

    def test_stamps_law_type(self, temp_law):
        from enrich_kov_layer1 import stamp_law_type
        stamp_law_type(temp_law)
        with open(temp_law, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        law_node = doc["@graph"][0]
        assert "owl:Ontology" in law_node["@type"]
        assert "estleg:Law" in law_node["@type"]
        # estleg:Act is stamped alongside Law so SHACL constraints with
        # sh:class estleg:Act don't require RDFS inference.
        assert "estleg:Act" in law_node["@type"]

    def test_idempotent(self, temp_law):
        from enrich_kov_layer1 import stamp_law_type
        stamp_law_type(temp_law)
        once = json.load(open(temp_law, encoding="utf-8"))
        stamp_law_type(temp_law)
        twice = json.load(open(temp_law, encoding="utf-8"))
        assert once == twice

    def test_skips_non_root_types(self, temp_law):
        # If the act node is already a different specific type (e.g.
        # NationalRegulation), don't add Law — only top-level
        # owl:Ontology act nodes should be stamped with Law. (Act is
        # stamped on regulation acts via stamp_act_type instead.)
        with open(temp_law, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["@graph"][0]["@type"] = ["owl:Ontology", "estleg:NationalRegulation"]
        with open(temp_law, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

        from enrich_kov_layer1 import stamp_law_type
        stamp_law_type(temp_law)
        with open(temp_law, "r", encoding="utf-8") as fh:
            doc2 = json.load(fh)
        assert "estleg:Law" not in doc2["@graph"][0]["@type"]


class TestStampActType:
    """Stamps estleg:Act on state regulation acts (regulations/riik/)
    so that partOfAct and issuedUnder SHACL constraints don't need
    inference."""

    def test_stamps_act_on_national_regulation(self, tmp_path):
        from enrich_kov_layer1 import stamp_act_type
        f = tmp_path / "reg.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1009410_Map_2026",
                 "@type": ["owl:Ontology", "estleg:NationalRegulation"],
                 "rdfs:label": "test"}
            ],
        }), encoding="utf-8")
        stamp_act_type(f)
        doc = json.load(open(f, encoding="utf-8"))
        types = doc["@graph"][0]["@type"]
        assert "estleg:Act" in types
        assert "estleg:NationalRegulation" in types  # preserved

    def test_idempotent(self, tmp_path):
        from enrich_kov_layer1 import stamp_act_type
        f = tmp_path / "reg.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:GovernmentRegulation"]}
            ],
        }), encoding="utf-8")
        stamp_act_type(f)
        once = json.load(open(f, encoding="utf-8"))
        stamp_act_type(f)
        twice = json.load(open(f, encoding="utf-8"))
        assert once == twice


class TestOrchestratorEndToEnd:
    def test_run_against_temp_tree(self, tmp_path, monkeypatch):
        """End-to-end: stage a small KOV tree + a law file in tmp, run main(),
        assert outputs."""
        # Arrange a faux KRR tree
        krr = tmp_path / "krr_outputs"
        ehak = tmp_path / "data" / "ehak"
        krr.mkdir()
        ehak.mkdir(parents=True)

        shutil.copy(MIN_MUNICIPALITIES, ehak / "municipalities.json")
        (ehak / "issuers.json").write_text(json.dumps([
            {
                "slug": "tallinna_linnavolikogu",
                "displayName": "Tallinna Linnavolikogu",
                "bodyType": "volikogu",
                "currentMunicipalityCode": "0784",
                "mappingSource": "auto-match",
                "mappingEvidence": "",
                "historicalMunicipalityName": "Tallinn",
            }
        ]), encoding="utf-8")

        # KOV act
        kov_dir = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov_dir.mkdir(parents=True)
        shutil.copy(SAMPLE_KOV_ACT, kov_dir / "act_peep.json")

        # Law + INDEX.json (source of canonical law file list)
        shutil.copy(SAMPLE_LAW, krr / "alkoholiseadus_peep.json")
        (krr / "INDEX.json").write_text(json.dumps({
            "generated": "2026-05-02",
            "total_files": 1,
            "total_laws": 1,
            "laws": [{"name": "alkoholiseadus",
                      "files": ["alkoholiseadus_peep.json"]}],
        }), encoding="utf-8")

        # State regulation under regulations/riik/
        riik = krr / "regulations" / "riik"
        riik.mkdir(parents=True)
        (riik / "valitsuse_maarus_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1009410_Map_2026",
                 "@type": ["owl:Ontology", "estleg:GovernmentRegulation"],
                 "rdfs:label": "Test government regulation"}
            ],
        }), encoding="utf-8")

        # Patch module-level paths
        import enrich_kov_layer1 as mod
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EHAK_DIR", ehak)
        monkeypatch.setattr(mod, "KOV_DIR", krr / "regulations" / "kov")
        monkeypatch.setattr(mod, "MUNICIPALITIES_OUT", krr / "municipalities_peep.json")
        monkeypatch.setattr(mod, "ISSUERS_OUT", krr / "issuers_kov_peep.json")

        rc = mod.main()
        assert rc == 0

        # Outputs
        assert (krr / "municipalities_peep.json").exists()
        assert (krr / "issuers_kov_peep.json").exists()

        # KOV act enriched
        kov_doc = json.load(open(kov_dir / "act_peep.json", encoding="utf-8"))
        act = next(n for n in kov_doc["@graph"]
                   if n["@id"] == "estleg:Reg_1014955_Map_2026")
        assert "estleg:enactedBy" in act
        assert act["estleg:titleNormalized"] == "jaatmehoolduseeskiri"

        # Law type stamped (Law + Act both)
        law_doc = json.load(open(krr / "alkoholiseadus_peep.json", encoding="utf-8"))
        assert "estleg:Law" in law_doc["@graph"][0]["@type"]
        assert "estleg:Act" in law_doc["@graph"][0]["@type"]

        # State regulation stamped with Act, GovernmentRegulation preserved
        reg_doc = json.load(open(riik / "valitsuse_maarus_peep.json", encoding="utf-8"))
        types = reg_doc["@graph"][0]["@type"]
        assert "estleg:Act" in types
        assert "estleg:GovernmentRegulation" in types


class TestStampLawTypeApplicability:
    """Regression tests for Finding #1: stamp_law_type previously
    silently skipped files without `owl:Ontology`. The function now
    returns whether it found an applicable node so the orchestrator
    can compare `stamped_count` against `expected_applicable_count`
    instead of guessing.
    """

    def test_returns_true_when_ontology_node_present(self, tmp_path):
        from enrich_kov_layer1 import stamp_law_type
        f = tmp_path / "law.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AlkS_Map_2026",
                 "@type": ["owl:Ontology"],
                 "rdfs:label": "Alkoholiseadus"}
            ],
        }), encoding="utf-8")
        assert stamp_law_type(f) is True

    def test_returns_false_when_no_ontology_node(self, tmp_path):
        from enrich_kov_layer1 import stamp_law_type
        # A sub-part file that lacks an owl:Ontology node — typical
        # for *_osa6_peep.json, where the parent file carries the
        # owl:Ontology marker.
        f = tmp_path / "law_osa6.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Provision_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:paragrahv": "§ 1"}
            ],
        }), encoding="utf-8")
        assert stamp_law_type(f) is False

    def test_returns_false_when_ontology_is_already_a_regulation(self, tmp_path):
        # Files whose root is already a regulation subclass go
        # through stamp_act_type — stamp_law_type must skip them
        # AND report False so callers don't double-count.
        from enrich_kov_layer1 import stamp_law_type
        f = tmp_path / "regulation.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:NationalRegulation"]}
            ],
        }), encoding="utf-8")
        assert stamp_law_type(f) is False

    def test_is_stampable_law_node_shared_with_verifier(self):
        # `is_stampable_law_node` is the SHARED predicate that both
        # stamp_law_type and verify_layer1.check_laws use. If the
        # contract drifts, verify_layer1 silently undercounts. This
        # test pins the contract.
        from enrich_kov_layer1 import is_stampable_law_node
        # Stampable: an owl:Ontology that isn't a regulation subclass.
        assert is_stampable_law_node(["owl:Ontology"]) is True
        assert is_stampable_law_node(
            ["owl:Ontology", "owl:NamedIndividual"]
        ) is True
        # Not stampable: missing owl:Ontology.
        assert is_stampable_law_node(["owl:NamedIndividual"]) is False
        # Not stampable: already a regulation subclass.
        assert is_stampable_law_node(
            ["owl:Ontology", "estleg:NationalRegulation"]
        ) is False
        assert is_stampable_law_node(
            ["owl:Ontology", "estleg:MunicipalRegulation"]
        ) is False


class TestStampLawTypeIdempotentNoMtimeChurn:
    """Regression test for Finding #2: stamp_law_type used to write
    the file even on a no-op pass, churning mtimes. Now it skips the
    write whenever the in-memory result equals the on-disk content.
    """

    def test_second_run_does_not_rewrite(self, tmp_path):
        from enrich_kov_layer1 import stamp_law_type
        f = tmp_path / "law.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AlkS_Map_2026",
                 "@type": ["owl:Ontology"]},
            ],
        }), encoding="utf-8")
        stamp_law_type(f)
        first_mtime = os.stat(f).st_mtime_ns
        # Second invocation must be a true no-op — the write is the
        # gate for mtime, not the json.dump call's return.
        stamp_law_type(f)
        second_mtime = os.stat(f).st_mtime_ns
        assert first_mtime == second_mtime, (
            "stamp_law_type re-wrote the file on an idempotent run "
            "(mtime changed). The no-op short-circuit regressed."
        )


class TestEnrichKovActFileIdempotentNoMtimeChurn:
    """Regression test for Finding #2 (KOV side): enrich_kov_act_file
    must skip the write when the on-disk content already matches the
    enriched form.
    """

    def _stage(self, tmp_path: Path) -> Path:
        dest = tmp_path / "act.json"
        shutil.copy(SAMPLE_KOV_ACT, dest)
        return dest

    @pytest.fixture
    def issuer(self):
        return {
            "slug": "tallinna_linnavolikogu",
            "displayName": "Tallinna Linnavolikogu",
            "bodyType": "volikogu",
            "currentMunicipalityCode": "0784",
            "mappingSource": "auto-match",
            "mappingEvidence": "",
            "historicalMunicipalityName": "Tallinn",
        }

    def test_first_run_writes_returns_true(self, tmp_path, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        f = self._stage(tmp_path)
        assert enrich_kov_act_file(f, issuer) is True

    def test_second_run_does_not_write_returns_false(self, tmp_path, issuer):
        from enrich_kov_layer1 import enrich_kov_act_file
        f = self._stage(tmp_path)
        enrich_kov_act_file(f, issuer)
        first_mtime = os.stat(f).st_mtime_ns
        result = enrich_kov_act_file(f, issuer)
        second_mtime = os.stat(f).st_mtime_ns
        assert result is False
        assert first_mtime == second_mtime, (
            "enrich_kov_act_file rewrote the file on a no-op pass; "
            "Finding #2 regression."
        )


class TestStampActTypeRaisesOnKovInput:
    """Regression test for Finding #11: stamp_act_type previously had
    a dead `MunicipalRegulation` skip branch. Calling it on a KOV
    file is a wiring bug; the function now raises so the bug surfaces
    here rather than silently mis-stamping.
    """

    def test_raises_on_municipal_regulation(self, tmp_path):
        from enrich_kov_layer1 import stamp_act_type
        f = tmp_path / "kov.json"
        f.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
            ],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="MunicipalRegulation"):
            stamp_act_type(f)


class TestParallelKovEnrichment:
    """Regression test for Finding #3: --workers flag spins up a
    process pool. We exercise N=2 against a tiny synthetic tree to
    confirm the parallel path produces the same results as the
    serial path. The test does not rely on speed-ups, just
    correctness."""

    @pytest.fixture
    def staged_repo(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        ehak = tmp_path / "data" / "ehak"
        krr.mkdir()
        ehak.mkdir(parents=True)
        shutil.copy(MIN_MUNICIPALITIES, ehak / "municipalities.json")
        # Two KOV issuer dirs so the worker has actual parallelism to
        # do.
        rows = []
        for slug, ehak_code in (
            ("tallinna_linnavolikogu", "0784"),
            ("tartu_linnavolikogu", "0793"),
        ):
            rows.append({
                "slug": slug,
                "displayName": slug.replace("_", " ").title(),
                "bodyType": "volikogu",
                "currentMunicipalityCode": ehak_code,
                "mappingSource": "auto-match",
                "mappingEvidence": "",
                "historicalMunicipalityName": slug.split("_")[0].title(),
            })
            kov_dir = krr / "regulations" / "kov" / slug
            kov_dir.mkdir(parents=True)
            shutil.copy(SAMPLE_KOV_ACT, kov_dir / f"{slug}_peep.json")
        (ehak / "issuers.json").write_text(
            json.dumps(rows), encoding="utf-8",
        )
        # Empty INDEX so the law-stamping path is a no-op.
        (krr / "INDEX.json").write_text(
            json.dumps({"laws": []}), encoding="utf-8",
        )
        return tmp_path, krr, ehak

    def _patch_paths(self, monkeypatch, tmp_path, krr, ehak):
        import enrich_kov_layer1 as mod
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EHAK_DIR", ehak)
        monkeypatch.setattr(mod, "KOV_DIR", krr / "regulations" / "kov")
        monkeypatch.setattr(
            mod, "MUNICIPALITIES_OUT", krr / "municipalities_peep.json",
        )
        monkeypatch.setattr(
            mod, "ISSUERS_OUT", krr / "issuers_kov_peep.json",
        )

    def test_workers_two_produces_same_result_as_serial(
        self, staged_repo, monkeypatch,
    ):
        import enrich_kov_layer1 as mod
        tmp_path, krr, ehak = staged_repo
        self._patch_paths(monkeypatch, tmp_path, krr, ehak)
        rc = mod.main(["--workers=2"])
        assert rc == 0
        # Both KOV files end up enriched regardless of worker order
        for slug in ("tallinna_linnavolikogu", "tartu_linnavolikogu"):
            doc = json.load(
                open(krr / "regulations" / "kov" / slug / f"{slug}_peep.json",
                     encoding="utf-8"),
            )
            act = next(
                n for n in doc["@graph"]
                if "estleg:MunicipalRegulation" in (
                    n.get("@type") if isinstance(n.get("@type"), list)
                    else [n.get("@type")] if n.get("@type") else []
                )
            )
            assert "estleg:enactedBy" in act
            assert "estleg:titleNormalized" in act

    def test_invalid_workers_value_rejected(self, staged_repo, monkeypatch):
        import enrich_kov_layer1 as mod
        tmp_path, krr, ehak = staged_repo
        self._patch_paths(monkeypatch, tmp_path, krr, ehak)
        with pytest.raises(SystemExit):
            mod.main(["--workers=0"])


class TestVerifyLayer1:
    """Regression tests for Findings #5 and #6 in verify_layer1.py.

    Live in this file (rather than a dedicated test_verify_layer1.py)
    because the verifier reuses `is_stampable_law_node` from
    `enrich_kov_layer1.py` — keeping them in one suite makes drift
    obvious if someone forks the predicate.
    """

    def _stage(self, tmp_path: Path) -> Path:
        krr = tmp_path / "krr_outputs"
        ehak = tmp_path / "data" / "ehak"
        krr.mkdir()
        ehak.mkdir(parents=True)
        (krr / "regulations" / "kov").mkdir(parents=True)
        (krr / "regulations" / "riik").mkdir(parents=True)
        return tmp_path

    def _patch_paths(self, monkeypatch, tmp_path: Path) -> None:
        import verify_layer1 as mod
        monkeypatch.setattr(mod, "REPO", tmp_path)
        monkeypatch.setattr(mod, "KRR", tmp_path / "krr_outputs")
        monkeypatch.setattr(
            mod, "KOV_DIR", tmp_path / "krr_outputs" / "regulations" / "kov",
        )

    def test_check_issuer_count_reads_filesystem(self, tmp_path, monkeypatch):
        # Finding #6: the expected count is the number of issuer
        # directories under regulations/kov/ — NOT a hardcoded 357.
        # Stage 2 directories and an issuers.json with 2 entries; both
        # numbers should match.
        self._stage(tmp_path)
        kov = tmp_path / "krr_outputs" / "regulations" / "kov"
        for slug in ("tallinna_linnavolikogu", "tartu_linnavolikogu"):
            (kov / slug).mkdir()
        ehak = tmp_path / "data" / "ehak"
        (ehak / "issuers.json").write_text(json.dumps([
            {"slug": "tallinna_linnavolikogu"},
            {"slug": "tartu_linnavolikogu"},
        ]), encoding="utf-8")
        self._patch_paths(monkeypatch, tmp_path)
        import verify_layer1 as mod
        ok, detail = mod.check_issuer_count()
        assert ok is True
        assert detail == "2/2"

    def test_check_issuer_count_detects_drift(self, tmp_path, monkeypatch):
        # If issuers.json drifts away from the actual KOV dir count,
        # the verifier reports the mismatch — both numbers, so the
        # operator can see the gap without a guess.
        self._stage(tmp_path)
        kov = tmp_path / "krr_outputs" / "regulations" / "kov"
        for slug in ("a_vallavolikogu", "b_vallavolikogu", "c_vallavolikogu"):
            (kov / slug).mkdir()
        ehak = tmp_path / "data" / "ehak"
        (ehak / "issuers.json").write_text(
            # Only two entries — drift!
            json.dumps([{"slug": "a_vallavolikogu"},
                        {"slug": "b_vallavolikogu"}]),
            encoding="utf-8",
        )
        self._patch_paths(monkeypatch, tmp_path)
        import verify_layer1 as mod
        ok, detail = mod.check_issuer_count()
        assert ok is False
        assert detail == "2/3"

    def test_check_kov_provisions_zero_act_nodes_flagged(
        self, tmp_path, monkeypatch,
    ):
        # Finding #5: a KOV file with no MunicipalRegulation node
        # must be a hard-fail with the path surfaced — not silently
        # skipped (which would let the count tick up while leaving
        # provisions stranded).
        self._stage(tmp_path)
        kov = tmp_path / "krr_outputs" / "regulations" / "kov" / "x_vallavolikogu"
        kov.mkdir(parents=True)
        (kov / "broken_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                # Provision without an act anchor.
                {"@id": "estleg:Provision_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:paragrahv": "§ 1"},
            ],
        }), encoding="utf-8")
        self._patch_paths(monkeypatch, tmp_path)
        import verify_layer1 as mod
        ok, detail = mod.check_kov_provisions()
        assert ok is False
        # The verifier names the failure mode in the detail string so
        # operators don't have to introspect the code to find out.
        assert "0 MunicipalRegulation" in detail

    def test_check_kov_provisions_multiple_act_nodes_flagged(
        self, tmp_path, monkeypatch,
    ):
        self._stage(tmp_path)
        kov = tmp_path / "krr_outputs" / "regulations" / "kov" / "x_vallavolikogu"
        kov.mkdir(parents=True)
        (kov / "two_acts_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_1_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_2_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
            ],
        }), encoding="utf-8")
        self._patch_paths(monkeypatch, tmp_path)
        import verify_layer1 as mod
        ok, detail = mod.check_kov_provisions()
        assert ok is False
        assert ">1 MunicipalRegulation" in detail

    def test_check_kov_provisions_missing_act_iri_flagged(
        self, tmp_path, monkeypatch,
    ):
        # Finding #5: act_iri==None must be treated as a hard-fail
        # with a sample list, not as silent acceptance.
        self._stage(tmp_path)
        kov = tmp_path / "krr_outputs" / "regulations" / "kov" / "x_vallavolikogu"
        kov.mkdir(parents=True)
        (kov / "no_id_peep.json").write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                # MunicipalRegulation node WITHOUT @id
                {"@type": ["owl:Ontology", "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_x_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:enactedBy": {"@id": "estleg:Issuer_x"},
                 "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0001"},
                 "estleg:partOfAct": {"@id": "estleg:Reg_x_Map_2026"}},
            ],
        }), encoding="utf-8")
        self._patch_paths(monkeypatch, tmp_path)
        import verify_layer1 as mod
        ok, detail = mod.check_kov_provisions()
        assert ok is False
        assert "no @id" in detail
        # The path of the offending file appears in the sample list.
        assert "no_id_peep.json" in detail

    def _write_kov_act(
        self, tmp_path: Path, name: str, extra_types: list[str],
    ) -> None:
        """Stage one fully-enriched KOV act file with the given extra
        @type entries (so the presence-check passes and only the
        Finding #267 absence-warning varies)."""
        kov = (
            tmp_path / "krr_outputs" / "regulations" / "kov"
            / "x_vallavolikogu"
        )
        kov.mkdir(parents=True, exist_ok=True)
        (kov / name).write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_x_Map_2026",
                 "@type": ["owl:Ontology", "estleg:MunicipalRegulation",
                           "estleg:Act", *extra_types],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_x"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_EHAK_0001"},
                 "estleg:titleNormalized": "hankekord"},
            ],
        }), encoding="utf-8")

    def test_kov_act_dual_typed_as_state_regulation_warns_not_fails(
        self, tmp_path, monkeypatch,
    ):
        # Finding #267 absence-assertion (synthetic, in-isolation): a KOV
        # act node that ALSO carries a state-regulation type must be
        # surfaced as a WARN in the detail string — but must NOT flip the
        # check to FAIL, because the committed corpus is still dual-typed
        # pre-regen and a hard gate would break the green build.
        self._stage(tmp_path)
        self._write_kov_act(
            tmp_path, "dual_peep.json",
            extra_types=["estleg:NationalRegulation"],
        )
        self._patch_paths(monkeypatch, tmp_path)
        import verify_layer1 as mod
        ok, detail = mod.check_kov_acts()
        # Non-fatal: the file is fully enriched, so the check passes.
        assert ok is True
        # ...but the contradictory state-regulation type is reported.
        assert "WARN" in detail
        assert "state-regulation" in detail
        assert "estleg:NationalRegulation" in detail
        assert "dual_peep.json" in detail

    def test_clean_kov_act_emits_no_state_regulation_warning(
        self, tmp_path, monkeypatch,
    ):
        # The complement: a correctly-typed KOV act (MunicipalRegulation
        # + Act only) produces NO Finding #267 warning. This is the shape
        # the corpus will have post-regen.
        self._stage(tmp_path)
        self._write_kov_act(tmp_path, "clean_peep.json", extra_types=[])
        self._patch_paths(monkeypatch, tmp_path)
        import verify_layer1 as mod
        ok, detail = mod.check_kov_acts()
        assert ok is True
        assert "WARN" not in detail
        assert "state-regulation" not in detail


class TestLoadLawPathsFailThreshold:
    """Regression test for Finding #4: the orchestrator must hard-
    fail when INDEX.json references >MISSING_LAW_FILES_FAIL_THRESHOLD
    missing files, not just print a WARN.
    """

    def _patch_paths(self, monkeypatch, tmp_path, krr, ehak):
        import enrich_kov_layer1 as mod
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "EHAK_DIR", ehak)
        monkeypatch.setattr(mod, "KOV_DIR", krr / "regulations" / "kov")
        monkeypatch.setattr(
            mod, "MUNICIPALITIES_OUT", krr / "municipalities_peep.json",
        )
        monkeypatch.setattr(
            mod, "ISSUERS_OUT", krr / "issuers_kov_peep.json",
        )

    def test_above_threshold_returns_nonzero(self, tmp_path, monkeypatch, capsys):
        import enrich_kov_layer1 as mod
        krr = tmp_path / "krr_outputs"
        ehak = tmp_path / "data" / "ehak"
        krr.mkdir()
        ehak.mkdir(parents=True)
        shutil.copy(MIN_MUNICIPALITIES, ehak / "municipalities.json")
        (ehak / "issuers.json").write_text("[]", encoding="utf-8")
        # No KOV dir to enrich
        (krr / "regulations" / "kov").mkdir(parents=True)

        # Build an INDEX with N+1 missing entries (N = threshold)
        n_missing = mod.MISSING_LAW_FILES_FAIL_THRESHOLD + 1
        (krr / "INDEX.json").write_text(json.dumps({
            "laws": [
                {"name": f"law_{i}", "files": [f"missing_law_{i}_peep.json"]}
                for i in range(n_missing)
            ],
        }), encoding="utf-8")

        self._patch_paths(monkeypatch, tmp_path, krr, ehak)
        rc = mod.main()
        assert rc == 2
        captured = capsys.readouterr()
        assert "exceeds the tolerance threshold" in captured.err

    def test_at_or_below_threshold_passes(self, tmp_path, monkeypatch):
        import enrich_kov_layer1 as mod
        krr = tmp_path / "krr_outputs"
        ehak = tmp_path / "data" / "ehak"
        krr.mkdir()
        ehak.mkdir(parents=True)
        shutil.copy(MIN_MUNICIPALITIES, ehak / "municipalities.json")
        (ehak / "issuers.json").write_text("[]", encoding="utf-8")
        (krr / "regulations" / "kov").mkdir(parents=True)
        # Exactly at the threshold
        n_missing = mod.MISSING_LAW_FILES_FAIL_THRESHOLD
        (krr / "INDEX.json").write_text(json.dumps({
            "laws": [
                {"name": f"law_{i}", "files": [f"missing_law_{i}_peep.json"]}
                for i in range(n_missing)
            ],
        }), encoding="utf-8")
        self._patch_paths(monkeypatch, tmp_path, krr, ehak)
        rc = mod.main()
        # Tolerance threshold met exactly → still passes (only > N
        # triggers the hard fail).
        assert rc == 0
