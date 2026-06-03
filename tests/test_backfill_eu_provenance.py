"""Tests for scripts/backfill_eu_provenance.py (#348 local provenance backfill)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import backfill_eu_provenance as bep


CELEX_LEG = "32016R0679"
CELEX_DEC = "61999TO0159"
RESOURCE = "http://publications.europa.eu/resource/celex/"


class TestBackfillNode:
    def test_legislation_gets_all_three_fields(self):
        node = {"@id": "estleg:EU_32016R0679",
                "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
                "estleg:celexNumber": CELEX_LEG}
        assert bep.backfill_node(node) is True
        assert node["owl:sameAs"] == {"@id": RESOURCE + CELEX_LEG}
        assert node["dcterms:source"] == {"@id": RESOURCE + CELEX_LEG}
        assert node["eli:id_local"] == CELEX_LEG

    def test_court_decision_gets_no_eli_id_local(self):
        node = {"@id": "estleg:EUCJ_61999TO0159",
                "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
                "estleg:celexNumber": CELEX_DEC}
        assert bep.backfill_node(node) is True
        assert node["owl:sameAs"] == {"@id": RESOURCE + CELEX_DEC}
        assert node["dcterms:source"] == {"@id": RESOURCE + CELEX_DEC}
        assert "eli:id_local" not in node  # case-law has no ELI

    def test_skips_non_eu_node_and_node_without_celex(self):
        # An ontology/map header node (no celex) must be left untouched.
        header = {"@id": "estleg:EURlex_Regulations_Map_2026",
                  "@type": ["owl:Ontology"]}
        assert bep.backfill_node(header) is False
        assert "dcterms:source" not in header
        # An EU node missing its celex is skipped too.
        no_celex = {"@id": "estleg:EU_x",
                    "@type": ["estleg:EULegislation"]}
        assert bep.backfill_node(no_celex) is False
        assert "dcterms:source" not in no_celex

    def test_idempotent_and_preserves_existing_values(self):
        node = {"@type": "estleg:EULegislation",  # @type as bare str
                "estleg:celexNumber": CELEX_LEG,
                "dcterms:source": {"@id": "PRE-EXISTING"}}
        assert bep.backfill_node(node) is True   # adds sameAs + eli:id_local
        assert node["dcterms:source"] == {"@id": "PRE-EXISTING"}  # untouched
        # Second pass is a no-op.
        assert bep.backfill_node(node) is False


class TestProcessFile:
    def _write(self, path: Path, context: dict, graph: list) -> None:
        path.write_text(json.dumps({"@context": context, "@graph": graph}),
                        encoding="utf-8")

    def test_eurlex_injects_eli_context_and_backfills(self, tmp_path):
        f = tmp_path / "eurlex_directives_peep.json"
        self._write(
            f,
            {"estleg": "x:", "owl": "y:", "dcterms": "z:"},  # no eli
            [{"@id": "estleg:EU_1", "@type": ["estleg:EULegislation"],
              "estleg:celexNumber": CELEX_LEG}],
        )
        changed, total = bep.process_file(f, inject_eli=True, dry_run=False)
        assert (changed, total) == (1, 1)
        doc = json.loads(f.read_text(encoding="utf-8"))
        assert doc["@context"]["eli"] == bep.ELI_PREFIX_IRI
        assert doc["@graph"][0]["eli:id_local"] == CELEX_LEG

    def test_curia_does_not_inject_eli_context(self, tmp_path):
        f = tmp_path / "curia_orders_peep.json"
        self._write(
            f,
            {"estleg": "x:", "owl": "y:", "dcterms": "z:"},
            [{"@id": "estleg:EUCJ_1", "@type": ["estleg:EUCourtDecision"],
              "estleg:celexNumber": CELEX_DEC}],
        )
        changed, total = bep.process_file(f, inject_eli=False, dry_run=False)
        assert (changed, total) == (1, 1)
        doc = json.loads(f.read_text(encoding="utf-8"))
        assert "eli" not in doc["@context"]
        assert "eli:id_local" not in doc["@graph"][0]

    def test_dry_run_writes_nothing(self, tmp_path):
        f = tmp_path / "eurlex_directives_peep.json"
        self._write(
            f,
            {"estleg": "x:", "owl": "y:", "dcterms": "z:"},
            [{"@id": "estleg:EU_1", "@type": ["estleg:EULegislation"],
              "estleg:celexNumber": CELEX_LEG}],
        )
        before = f.read_text(encoding="utf-8")
        changed, total = bep.process_file(f, inject_eli=True, dry_run=True)
        assert changed == 1
        assert f.read_text(encoding="utf-8") == before  # unchanged on disk
