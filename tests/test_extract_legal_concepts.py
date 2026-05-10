"""Tests for extract_legal_concepts — IRI sourcing from actual @id."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))



REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer2a"
PEEP_FIXTURE = FIXTURE_DIR / "sample_kov_act.json"


class TestParToIriLookup:
    def test_builds_lookup_from_kov_peep(self):
        """Walk the KOV fixture's @graph and build a (par_nr, par_display)
        → @id lookup. The fixture's only provision is § 1 with @id
        estleg:Reg_9999_Par_1."""
        from extract_legal_concepts import build_par_to_iri_lookup

        with open(PEEP_FIXTURE, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        lookup = build_par_to_iri_lookup(doc)
        # The lookup is keyed by both par_nr and par_display for resilience —
        # XML uses paragrahvNr (e.g. "1") while peep stores paragrahv as
        # "§ 1." (display form). Either should resolve.
        assert lookup.get("1") == "estleg:Reg_9999_Par_1"
        assert lookup.get("§ 1.") == "estleg:Reg_9999_Par_1"

    def test_skips_non_provision_nodes(self):
        from extract_legal_concepts import build_par_to_iri_lookup
        doc = {
            "@graph": [
                {"@id": "estleg:Act_X", "@type": ["owl:Ontology"]},
                {"@id": "estleg:Class_Y", "@type": ["owl:Class"]},
                {"@id": "estleg:Reg_X_Par_1",
                 "@type": ["owl:NamedIndividual"],
                 "estleg:paragrahv": "§ 1.",
                 "estleg:summary": "test"},
            ]
        }
        lookup = build_par_to_iri_lookup(doc)
        assert "1" in lookup
        assert lookup["1"] == "estleg:Reg_X_Par_1"


class TestExtractConceptsWithLookup:
    def test_uses_lookup_for_provision_id(self, tmp_path):
        """When extract_concepts_from_xml receives an explicit lookup,
        the emitted concept's provision_id is the peep @id, not the
        slug-derived pattern. Test must NOT pass vacuously — assert
        at least one concept is emitted AND its IRI is exactly the
        bridged value.

        Fixture matches the existing recogniser's expected shape:
        paragrahvPealkiri == 'Mõisted' triggers
        is_definition_paragraph; the `1) <term> — <definition>;`
        body matches extract_definitions_from_text.
        """
        from extract_legal_concepts import extract_concepts_from_xml

        xml = tmp_path / "kov_act.xml"
        xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <sisu>
    <paragrahv>
      <paragrahvNr>1</paragrahvNr>
      <kuvatavNr>§ 1.</kuvatavNr>
      <paragrahvPealkiri>Mõisted</paragrahvPealkiri>
      <loige>
        <tavatekst>Käesolevas määruses kasutatakse järgmisi mõisteid:
        1) jäätmed — olmejäätmed selle määruse tähenduses;
        2) jäätmevaldaja — isik, kelle valduses on jäätmed.</tavatekst>
      </loige>
    </paragrahv>
  </sisu>
</akt>""", encoding="utf-8")

        par_to_iri = {
            "1": "estleg:Reg_9999_Par_1",
            "§ 1.": "estleg:Reg_9999_Par_1",
        }
        concepts = extract_concepts_from_xml(
            xml, "Tallinna jäätmehoolduseeskiri",
            "tallinna_jaatmehoolduseeskiri",
            par_to_iri=par_to_iri,
        )
        # Non-vacuous: at least one concept must come out, and its IRI
        # must be the KOV @id, not the slug-derived form.
        assert concepts, (
            "extract_concepts_from_xml returned no concepts — fixture "
            "shape must trigger the recogniser; if not, fixture or "
            "recogniser have drifted apart."
        )
        for c in concepts:
            assert c["provision_id"] == "estleg:Reg_9999_Par_1", (
                f"concept used wrong IRI: {c['provision_id']!r}"
            )

    def test_falls_back_to_slug_when_no_lookup(self, tmp_path):
        """Backwards compat: when par_to_iri is None or empty, the
        existing slug-derived IRI shape is preserved. Ensures we
        don't break the laws-only callers that haven't been updated
        to pass the lookup yet."""
        from extract_legal_concepts import extract_concepts_from_xml

        xml = tmp_path / "law.xml"
        xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <sisu>
    <paragrahv>
      <paragrahvNr>1</paragrahvNr>
      <kuvatavNr>§ 1.</kuvatavNr>
      <paragrahvPealkiri>Mõisted</paragrahvPealkiri>
      <loige>
        <tavatekst>Käesolevas seaduses kasutatakse järgmisi mõisteid:
        1) klient — füüsiline isik.</tavatekst>
      </loige>
    </paragrahv>
  </sisu>
</akt>""", encoding="utf-8")

        concepts = extract_concepts_from_xml(
            xml, "Test seadus", "test_seadus", par_to_iri=None
        )
        assert concepts
        for c in concepts:
            # Slug-derived form: estleg:test_seadus_Par_1 (sanitize_id
            # may transform underscores; the assertion is loose enough
            # to tolerate the sanitization).
            assert "test_seadus" in c["provision_id"].lower()
            assert "Par_1" in c["provision_id"]
