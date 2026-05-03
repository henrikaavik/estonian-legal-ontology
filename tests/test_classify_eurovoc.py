"""Discovery and output-path tests for classify_eurovoc."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = (Path(__file__).parent / "fixtures" / "kov_layer2a"
           / "sample_kov_act.json")


def _read_act_metadata(path: Path):
    """The helper under test: pull title/source from a peep file's
    act-typed node without consulting INDEX.json."""
    from classify_eurovoc import read_act_metadata_from_peep
    return read_act_metadata_from_peep(path)


class TestReadActMetadataFromPeep:
    def test_reads_title_from_kov_act(self):
        meta = _read_act_metadata(FIXTURE)
        assert meta["title"] == "Test KOV act"
        assert meta["source"] == "Test KOV act"
        assert meta["@id"] == "estleg:Reg_9999_Map_2026"

    def test_returns_none_for_provision_only_file(self, tmp_path):
        # A file with no act-typed node should return None, not crash.
        bad = tmp_path / "no_act.json"
        bad.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:LooseProvision",
                 "@type": ["owl:NamedIndividual"],
                 "rdfs:label": "stray"}
            ],
        }), encoding="utf-8")
        assert _read_act_metadata(bad) is None

    def test_skips_municipality_registry_file(self, tmp_path):
        # municipalities_peep.json's top node is typed only owl:Ontology.
        # Without the registry exclusion, the broad acceptance of
        # owl:Ontology would let EuroVoc tag it as an act.
        registry = tmp_path / "municipalities_peep.json"
        registry.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Municipalities_Map_2026",
                 "@type": ["owl:Ontology"],
                 "rdfs:label": "Estonian Municipalities (current EHAK)"},
                {"@id": "estleg:Municipality_EHAK_0784",
                 "@type": ["owl:NamedIndividual", "estleg:Municipality"]},
            ],
        }), encoding="utf-8")
        # The registry header node has owl:Ontology but matches the
        # IRI prefix exclusion — read_act_metadata_from_peep returns
        # None and the file is skipped.
        assert _read_act_metadata(registry) is None

    def test_skips_issuer_registry_file(self, tmp_path):
        registry = tmp_path / "issuers_kov_peep.json"
        registry.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Issuers_Kov_Map_2026",
                 "@type": ["owl:Ontology"],
                 "rdfs:label": "KOV Issuers"},
            ],
        }), encoding="utf-8")
        assert _read_act_metadata(registry) is None
