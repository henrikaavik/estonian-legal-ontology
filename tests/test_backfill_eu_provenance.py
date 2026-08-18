"""Tests for scripts/backfill_eu_provenance.py (#348 local provenance backfill)."""
from __future__ import annotations

import json
from pathlib import Path

import backfill_eu_provenance as bep

CELEX_LEG = "32016R0679"
CELEX_DEC = "61999TO0159"
RESOURCE = "http://publications.europa.eu/resource/celex/"


ELI_URI = "http://data.europa.eu/eli/reg/2016/679/oj"
ELI_NATURAL = "reg/2016/679"


class TestBackfillNode:
    def test_legislation_without_eli_falls_back_to_celex(self):
        # #607b fallback path: no estleg:eliIdentifier -> eli:id_local stays the
        # CELEX (there is no ELI natural id available) and only the CELEX
        # owl:sameAs is emitted.
        node = {"@id": "estleg:EU_32016R0679",
                "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
                "estleg:celexNumber": CELEX_LEG}
        assert bep.backfill_node(node) is True
        assert node["owl:sameAs"] == {"@id": RESOURCE + CELEX_LEG}
        assert node["dcterms:source"] == {"@id": RESOURCE + CELEX_LEG}
        assert node["eli:id_local"] == CELEX_LEG

    def test_legislation_with_eli_uses_natural_id_and_adds_eli_sameas(self):
        # #607b + #610: when an ELI URI is present, eli:id_local is the natural
        # id (NOT the CELEX), and the ELI URI is appended as an owl:sameAs link
        # alongside the CELEX PURL.
        node = {"@id": "estleg:EU_32016R0679",
                "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
                "estleg:celexNumber": CELEX_LEG,
                "estleg:eliIdentifier": {"@value": ELI_URI, "@type": "xsd:anyURI"}}
        assert bep.backfill_node(node) is True
        assert node["eli:id_local"] == ELI_NATURAL
        assert node["eli:id_local"] != CELEX_LEG
        # owl:sameAs is now a 2-item list: CELEX PURL + ELI URI.
        targets = {s["@id"] for s in node["owl:sameAs"]}
        assert targets == {RESOURCE + CELEX_LEG, ELI_URI}
        # CELEX still lives in celexNumber, untouched.
        assert node["estleg:celexNumber"] == CELEX_LEG

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
        assert "dcterms:title" not in node  # no rdfs:label to copy from
        # Second pass is a no-op.
        assert bep.backfill_node(node) is False

    def test_court_decision_copies_label_object_to_title(self):
        label = {"@value": "Kohtuotsus C-159/99", "@language": "et"}
        node = {"@id": "estleg:EUCJ_61999TO0159",
                "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
                "estleg:celexNumber": CELEX_DEC,
                "rdfs:label": label}
        assert bep.backfill_node(node) is True
        assert node["dcterms:title"] == label
        assert node["dcterms:title"] is not label  # copied, not aliased
        # Title backfill is idempotent once the other provenance fields exist.
        assert bep.backfill_node(node) is False

    def test_legislation_copies_string_label_to_title(self):
        node = {"@type": ["estleg:EULegislation"],
                "estleg:celexNumber": CELEX_LEG,
                "rdfs:label": "Isikuandmete kaitse üldmäärus"}
        assert bep.backfill_node(node) is True
        assert node["dcterms:title"] == "Isikuandmete kaitse üldmäärus"
        assert bep.backfill_node(node) is False

    def test_existing_title_is_preserved(self):
        title = {"@value": "Existing title", "@language": "et"}
        node = {"@type": "estleg:EUCourtDecision",
                "estleg:celexNumber": CELEX_DEC,
                "owl:sameAs": {"@id": RESOURCE + CELEX_DEC},
                "dcterms:source": {"@id": RESOURCE + CELEX_DEC},
                "rdfs:label": {"@value": "Different label", "@language": "et"},
                "dcterms:title": title}
        assert bep.backfill_node(node) is False
        assert node["dcterms:title"] == title


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
