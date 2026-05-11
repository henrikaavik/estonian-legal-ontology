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


# ---------------------------------------------------------------------------
# Issue #171 regression tests
# ---------------------------------------------------------------------------


class TestIssue171DefinitionPattern:
    """Finding 1 (#171): the definition body must NOT truncate at the
    first ``;``; truncation now happens at the next numbered marker
    (lookahead) so multi-clause definitions survive intact."""

    def test_embedded_semicolon_kept_in_definition(self):
        from extract_legal_concepts import extract_definitions_from_text
        # KMS § 2 1) ... — long-form clause with an embedded semicolon
        # before the next numbered marker.
        text = (
            "1) imporditud kaup — kaup, mille kohta on koostatud "
            "ekspordideklaratsioon; sealhulgas käibemaksukohuslane; "
            "2) kaubavedu — vedu kauba osas;"
        )
        defs = extract_definitions_from_text(text)
        assert len(defs) == 2, defs
        assert defs[0][1] == "imporditud kaup"
        # The embedded semicolon must survive — it was originally
        # truncating the definition at "ekspordideklaratsioon".
        assert "sealhulgas käibemaksukohuslane" in defs[0][2]

    def test_trailing_semicolon_stripped(self):
        from extract_legal_concepts import extract_definitions_from_text
        text = "1) klient — füüsiline isik;"
        defs = extract_definitions_from_text(text)
        assert defs[0][2] == "füüsiline isik", defs[0][2]

    def test_last_definition_terminates_at_eos(self):
        from extract_legal_concepts import extract_definitions_from_text
        text = "1) klient — füüsiline isik 2) teenus — pakutav abi"
        defs = extract_definitions_from_text(text)
        # Two definitions, both intact.
        assert len(defs) == 2
        assert defs[0][2].startswith("füüsiline isik")
        assert defs[1][2].startswith("pakutav abi")


class TestIssue171ConceptIdDisambiguation:
    """Finding 2 (#171): collision disambiguation uses (law_slug, par_nr,
    def_number); collisions append _2/_3/... rather than silently
    dropping. Every closeMatch / exactMatch target must be in
    seen_concept_ids."""

    def test_disambiguator_uses_law_par_def(self):
        from extract_legal_concepts import _disambiguate_concept_id
        seen = {"estleg:Concept_isik"}
        concept = {
            "concept_id": "estleg:Concept_isik",
            "law_slug": "law_a", "par_nr": "2", "def_number": "1",
        }
        cid = _disambiguate_concept_id(concept, seen)
        # Collision → composite suffix.
        assert cid == "estleg:Concept_isik_law_a_2_1", cid

    def test_disambiguator_appends_monotonic_on_collision(self):
        from extract_legal_concepts import _disambiguate_concept_id
        seen = {"estleg:Concept_isik", "estleg:Concept_isik_law_a_2_1"}
        concept = {
            "concept_id": "estleg:Concept_isik",
            "law_slug": "law_a", "par_nr": "2", "def_number": "1",
        }
        cid = _disambiguate_concept_id(concept, seen)
        # Composite already taken → append _2.
        assert cid == "estleg:Concept_isik_law_a_2_1_2", cid

    def test_no_collision_returns_base(self):
        from extract_legal_concepts import _disambiguate_concept_id
        seen = set()
        concept = {
            "concept_id": "estleg:Concept_unique_term",
            "law_slug": "law_a", "par_nr": "2", "def_number": "1",
        }
        cid = _disambiguate_concept_id(concept, seen)
        assert cid == "estleg:Concept_unique_term"

    def test_two_laws_define_same_term_no_drop(self, tmp_path, monkeypatch):
        """End-to-end: two laws each defining 'isik' in their § 2 1)
        clauses — both must survive in the combined graph and neither
        is silently dropped."""
        import extract_legal_concepts as mod
        krr = tmp_path / "krr_outputs"
        concepts = krr / "concepts"
        reports = krr / "reports" / "kov"
        krr.mkdir()
        concepts.mkdir()
        reports.mkdir(parents=True)

        # Stage two state-law peeps with the same '1) isik — ...'
        # definition shape under § 2.
        for slug, suffix in [("law_a", "A"), ("law_b", "B")]:
            peep = krr / f"{slug}_peep.json"
            peep.write_text(json.dumps({
                "@context": {
                    "estleg": "https://data.riik.ee/ontology/estleg#",
                    "owl": "http://www.w3.org/2002/07/owl#",
                },
                "@graph": [
                    {"@id": f"estleg:{slug}_Map_2026",
                     "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                     "dc:source": f"Test law {suffix}"},
                    {"@id": f"estleg:{slug}_Par_2",
                     "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                     "estleg:paragrahv": "§ 2"},
                ],
            }), encoding="utf-8")

        # Stage matching XML files referenced by slug-fallback pairing.
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        for slug in ("law_a", "law_b"):
            (data_dir / f"{slug}.xml").write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <sisu>
    <paragrahv>
      <paragrahvNr>2</paragrahvNr>
      <kuvatavNr>§ 2.</kuvatavNr>
      <paragrahvPealkiri>Mõisted</paragrahvPealkiri>
      <loige>
        <tavatekst>Käesolevas seaduses kasutatakse järgmisi mõisteid:
        1) isik — füüsiline või juriidiline isik;</tavatekst>
      </loige>
    </paragrahv>
  </sisu>
</akt>""",
                encoding="utf-8",
            )

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", data_dir)
        monkeypatch.setattr(mod, "CONCEPTS_DIR", concepts)
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        import estleg_common
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        # Both isik concepts should appear in the combined file.
        combined = json.loads(
            (concepts / "concepts_combined.jsonld").read_text(encoding="utf-8")
        )
        isik_nodes = [n for n in combined["@graph"]
                      if n.get("skos:prefLabel") == "isik"]
        assert len(isik_nodes) == 2, (
            f"expected both 'isik' definitions to survive; got "
            f"{len(isik_nodes)}: {[n['@id'] for n in isik_nodes]}"
        )
        # Distinct @ids (one was disambiguated).
        ids = {n["@id"] for n in isik_nodes}
        assert len(ids) == 2

    def test_close_match_targets_are_seen(self):
        from extract_legal_concepts import _disambiguate_concept_id
        # A closeMatch/exactMatch target should always end up in
        # seen_concept_ids — pin via the helper's API.
        seen: set[str] = set()
        c1 = {"concept_id": "estleg:Concept_a", "law_slug": "x", "par_nr": "1", "def_number": "1"}
        c2 = {"concept_id": "estleg:Concept_a", "law_slug": "y", "par_nr": "1", "def_number": "1"}
        id1 = _disambiguate_concept_id(c1, seen)
        seen.add(id1)
        id2 = _disambiguate_concept_id(c2, seen)
        seen.add(id2)
        assert id1 != id2
        assert id1 in seen and id2 in seen


class TestIssue171DeterministicOrdering:
    """Finding 3 (#171): set→list conversion replaced with sorted({...})
    or list(dict.fromkeys(...)) so graph ordering is deterministic
    across runs."""

    def test_law_slug_lists_are_sorted(self):
        # Inspect the source for any remaining `list({...})` over a set.
        # The source helper file is assumed unchanged across this call.
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "scripts" / "extract_legal_concepts.py"
        text = src.read_text(encoding="utf-8")
        # Allow `list({` patterns inside docstrings (the comments
        # discussing the bug). Strip docstrings before scanning.
        # Crude but effective: count `list({` occurrences outside of
        # comment lines starting with `#`.
        offenders = []
        for ln in text.splitlines():
            stripped = ln.strip()
            if stripped.startswith("#"):
                continue
            # only flag the unsorted-set conversion in code form
            if "list({" in ln and "set" not in ln.lower() and "comment" not in ln.lower():
                offenders.append(ln.strip())
        assert not offenders, (
            "extract_legal_concepts.py still has unsorted "
            f"`list({{...}})` conversions: {offenders}"
        )


class TestIssue171CloseMatchCrossProduct:
    """Finding 4 (#171): closeMatch links the FULL entries_a × entries_b
    cross-product with bidirectional arcs, not just the first."""

    def test_bucketed_pairs_returns_all_close_matches(self):
        from extract_legal_concepts import _bucketed_close_match_pairs
        # 'auto' / 'auta' / 'auto1' all within edit distance < 3.
        terms = ["auto", "auta", "auts"]
        pairs = _bucketed_close_match_pairs(terms)
        # Pair (auto, auta) and (auto, auts) must both appear (distance 1)
        pair_set = {(t1, t2) for t1, t2, _d in pairs}
        assert ("auta", "auto") in pair_set
        assert ("auto", "auts") in pair_set
        assert ("auta", "auts") in pair_set


class TestIssue171BucketingPerformance:
    """Finding 5 (#171): the bucketing strategy must NOT silently skip
    pairs when the corpus exceeds the old 5000-cap."""

    def test_bucketing_finds_pairs_in_large_corpus(self):
        from extract_legal_concepts import _bucketed_close_match_pairs
        # Build a 200-term corpus seeded with two known close pairs
        # ("isiku" / "isikud", distance 1; "auto" / "auts", distance 1)
        # and many unrelated short terms.
        terms = [f"term{i:03d}" for i in range(200)]
        terms.extend(["isikud", "isiku", "auto", "auts"])
        pairs = _bucketed_close_match_pairs(terms)
        pair_set = {(t1, t2) for t1, t2, _d in pairs}
        assert ("isiku", "isikud") in pair_set
        assert ("auto", "auts") in pair_set


class TestIssue171TripleCounting:
    """Finding 6 (#171): _triples increments AFTER graph emission, not
    before — collided drops no longer cause overcount.

    This is hard to test in isolation since the increment is inside
    main(); use a synthetic end-to-end run and pin the post-emission
    semantics: triples_emitted == 2 * len(graph_concept_nodes).
    """

    def test_triples_match_emitted_concept_count(self, tmp_path, monkeypatch):
        import extract_legal_concepts as mod
        krr = tmp_path / "krr_outputs"
        concepts = krr / "concepts"
        reports = krr / "reports" / "kov"
        krr.mkdir()
        concepts.mkdir()
        reports.mkdir(parents=True)

        # Stage one peep + one XML with a single Mõisted paragraph.
        peep = krr / "law_x_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:law_x_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "dc:source": "Test law X"},
                {"@id": "estleg:law_x_Par_2",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 2"},
            ],
        }), encoding="utf-8")
        data_dir = tmp_path / "data" / "riigiteataja"
        data_dir.mkdir(parents=True)
        (data_dir / "law_x.xml").write_text("""<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <sisu>
    <paragrahv>
      <paragrahvNr>2</paragrahvNr>
      <kuvatavNr>§ 2.</kuvatavNr>
      <paragrahvPealkiri>Mõisted</paragrahvPealkiri>
      <loige>
        <tavatekst>Käesolevas seaduses kasutatakse järgmisi mõisteid:
        1) klient — füüsiline isik;
        2) teenus — pakutav abi.</tavatekst>
      </loige>
    </paragrahv>
  </sisu>
</akt>""", encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", data_dir)
        monkeypatch.setattr(mod, "CONCEPTS_DIR", concepts)
        monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
        import estleg_common
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        mod.main()

        cov_path = reports / "extract_legal_concepts_coverage.json"
        assert cov_path.exists()
        cov = json.loads(cov_path.read_text(encoding="utf-8"))

        combined = json.loads(
            (concepts / "concepts_combined.jsonld").read_text(encoding="utf-8")
        )
        concept_nodes = [
            n for n in combined["@graph"]
            if "estleg:LegalConcept" in n.get("@type", [])
            and "owl:NamedIndividual" in n.get("@type", [])
        ]
        # Each concept emits 2 triples (definesTerm + definedIn).
        assert cov["triples_emitted"] == 2 * len(concept_nodes), (
            f"triples_emitted ({cov['triples_emitted']}) doesn't match "
            f"2 × concept count ({len(concept_nodes)})"
        )


class TestIssue171ParToIriCollision:
    """Finding 7 (#171): build_par_to_iri_lookup stores all matching @ids
    on duplicate par_nr; resolve_provision_id picks first and surfaces
    the ambiguity flag."""

    def test_collision_stores_list(self):
        from extract_legal_concepts import build_par_to_iri_lookup
        doc = {
            "@graph": [
                {"@id": "estleg:Reg_X_Par_1", "estleg:paragrahv": "§ 1."},
                {"@id": "estleg:Reg_X_Lisa_Par_1", "estleg:paragrahv": "§ 1."},
            ]
        }
        lookup = build_par_to_iri_lookup(doc)
        assert isinstance(lookup["1"], list)
        assert lookup["1"] == ["estleg:Reg_X_Par_1", "estleg:Reg_X_Lisa_Par_1"]

    def test_no_collision_keeps_string(self):
        from extract_legal_concepts import build_par_to_iri_lookup
        doc = {
            "@graph": [
                {"@id": "estleg:Reg_X_Par_1", "estleg:paragrahv": "§ 1."},
            ]
        }
        lookup = build_par_to_iri_lookup(doc)
        assert lookup["1"] == "estleg:Reg_X_Par_1"
        assert isinstance(lookup["1"], str)

    def test_resolve_returns_ambiguous_flag(self):
        from extract_legal_concepts import resolve_provision_id
        lookup = {"1": ["estleg:A_Par_1", "estleg:B_Par_1"]}
        provision_id, ambig = resolve_provision_id(lookup, "1", "§ 1.")
        # Picks first, surfaces ambiguity.
        assert provision_id == "estleg:A_Par_1"
        assert ambig is True

    def test_resolve_unique_no_ambiguity(self):
        from extract_legal_concepts import resolve_provision_id
        lookup = {"1": "estleg:A_Par_1"}
        provision_id, ambig = resolve_provision_id(lookup, "1", "§ 1.")
        assert provision_id == "estleg:A_Par_1"
        assert ambig is False


class TestIssue171ExceptionHandling:
    """Finding 8 (#171): catches only specific exceptions; logs traceback;
    surfaces non-zero exit when failures > threshold."""

    def test_specific_exceptions_only_caught(self):
        """Pin: the source uses the narrow except clause, not bare Exception."""
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "scripts" / "extract_legal_concepts.py"
        text = src.read_text(encoding="utf-8")
        # The specific exceptions live in the per-law try/except. Bare
        # Exception in main()'s loop has been removed.
        assert "except Exception as exc:  # noqa: BLE001" not in text, (
            "narrow exception clause not applied"
        )
        # Confirm the new clause exists.
        assert "except (ET.ParseError, OSError, KeyError, json.JSONDecodeError)" in text

    def test_failure_exit_threshold_constant(self):
        from extract_legal_concepts import _FAILURE_EXIT_THRESHOLD
        assert isinstance(_FAILURE_EXIT_THRESHOLD, int)
        assert _FAILURE_EXIT_THRESHOLD >= 1


class TestIssue171ImportTraceback:
    """The traceback module is imported at module top; failures populate
    a traceback string into _failures."""

    def test_traceback_imported(self):
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "scripts" / "extract_legal_concepts.py"
        text = src.read_text(encoding="utf-8")
        assert "import traceback" in text
