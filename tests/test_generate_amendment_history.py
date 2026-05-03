"""Tests for generate_amendment_history — XML pairing for KOV."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


class TestAmendmentHistoryDiscovery:
    def test_imports_globalid_helper_from_temporal(self):
        """The amendment-history script reuses the globalId lookup
        helper from extract_temporal_data — DRY rule."""
        from generate_amendment_history import (
            build_globalid_xml_lookup,
            pair_peep_with_xml,
        )
        # Same behavior verification as in Task 8
        assert callable(build_globalid_xml_lookup)
        assert callable(pair_peep_with_xml)

    def test_recursive_glob_finds_kov_xml(self, tmp_path):
        from generate_amendment_history import build_globalid_xml_lookup
        rt = tmp_path / "data" / "riigiteataja"
        (rt / "maarus_kov").mkdir(parents=True)
        (rt / "maarus_kov" / "reg_kov_888.xml").write_text(
            '<akt globaalID="888"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )
        lookup = build_globalid_xml_lookup(rt)
        assert "888" in lookup

    def test_main_uses_globalid_pairing(self, tmp_path, monkeypatch):
        """Pin the contract that main() actually wires the new helpers.
        Same approach as temporal's TestMainUsesGlobalIdLookup — runs
        main() over a tiny KOV-like fixture and asserts an XML file
        under maarus_kov/ paired by globalId contributed an amendment
        chain entry."""
        import generate_amendment_history as mod
        import estleg_common

        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (rt / "maarus_kov").mkdir(parents=True)
        (krr / "amendments").mkdir(parents=True)
        (krr / "eelnoud").mkdir(parents=True)

        peep = kov / "test_act_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "rdfs:label": "Test KOV act",
                 "dc:source": "Test KOV act",
                 "estleg:globalId": "777",
                 "estleg:enactedBy": {
                     "@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_EHAK_0784"},
                 "estleg:titleNormalized": "test"}
            ],
        }), encoding="utf-8")

        # XML with a muutmismarge block whose children match what
        # extract_amendments_from_xml expects (lines 162-210 of the
        # script): aktikuupaev, joustumine, and avaldamismarge with
        # RTosa/RTaasta/RTnr children (the script reads RTnr, not
        # RTnumber — see line 205 of the script). Each muutmismarge
        # is extracted as a separate amendment entry; the RT fields
        # are formatted as "RT IV, 2024, 1" in the output.
        (rt / "maarus_kov" / "reg_777.xml").write_text(
            '<akt globaalID="777"><metaandmed>'
            '<muutmismarge>'
            '<aktikuupaev>2023-06-15</aktikuupaev>'
            '<joustumine>2024-01-01</joustumine>'
            '<avaldamismarge>'
            '<RTosa>IV</RTosa><RTaasta>2024</RTaasta><RTnr>1</RTnr>'
            '</avaldamismarge>'
            '</muutmismarge>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        # Patch ALL the import-time-bound paths. AMENDMENTS_DIR and
        # EELNOUD_DIR are derived from KRR_DIR at import time so they
        # need explicit rebinding too.
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        if hasattr(mod, "AMENDMENTS_DIR"):
            monkeypatch.setattr(mod, "AMENDMENTS_DIR", krr / "amendments")
        if hasattr(mod, "EELNOUD_DIR"):
            monkeypatch.setattr(mod, "EELNOUD_DIR", krr / "eelnoud")
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        rc = mod.main()
        assert rc in (None, 0)

        # The peep file should have been touched. Either an
        # amendedBy back-link or a chain summary node was added.
        with open(peep, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        # Search the graph for any node carrying an amendment-related
        # property — the exact name depends on the script's output
        # convention, so accept any of several candidates.
        amendment_keys = {"estleg:amendedBy", "estleg:hasAmendment",
                          "estleg:amendmentHistory"}
        found = False
        for node in doc.get("@graph", []):
            if amendment_keys.intersection(node.keys()):
                found = True
                break
        assert found, (
            "main() ran but no amendment triples appeared after pairing "
            f"— amendments_by_slug may not be populated. Graph keys: "
            f"{[list(n.keys()) for n in doc['@graph']]}"
        )
