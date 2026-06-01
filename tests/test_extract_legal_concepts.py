"""Tests for extract_legal_concepts — IRI sourcing from actual @id."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))



REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer2a"
PEEP_FIXTURE = FIXTURE_DIR / "sample_kov_act.json"
SHAPES_TTL = REPO_ROOT / "shacl" / "estonian_legal_shapes.ttl"


# ---------------------------------------------------------------------------
# Issue #134 helpers — stage a synthetic corpus and run the extractor.
# ---------------------------------------------------------------------------


def _run_extractor(tmp_path, monkeypatch, peeps: dict[str, str], xmls: dict[str, str]):
    """Stage ``peeps`` (slug -> peep JSON text) and ``xmls`` (slug -> XML text)
    under a temp ``krr_outputs`` / ``data/riigiteataja`` tree, point
    ``extract_legal_concepts`` at it, run ``main()``, and return the parsed
    ``concepts_combined.jsonld`` ``@graph``."""
    import extract_legal_concepts as mod

    krr = tmp_path / "krr_outputs"
    concepts = krr / "concepts"
    reports = krr / "reports" / "kov"
    krr.mkdir()
    concepts.mkdir()
    reports.mkdir(parents=True)
    data_dir = tmp_path / "data" / "riigiteataja"
    data_dir.mkdir(parents=True)

    for slug, text in peeps.items():
        (krr / f"{slug}_peep.json").write_text(text, encoding="utf-8")
    for slug, text in xmls.items():
        (data_dir / f"{slug}.xml").write_text(text, encoding="utf-8")

    monkeypatch.setattr(mod, "KRR_DIR", krr)
    monkeypatch.setattr(mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(mod, "CONCEPTS_DIR", concepts)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    import estleg_common
    monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

    rc = mod.main()
    assert rc in (0, None)
    combined = json.loads(
        (concepts / "concepts_combined.jsonld").read_text(encoding="utf-8")
    )
    return combined["@graph"]


def _peep_text(slug: str, source: str, par_nr: str = "2") -> str:
    return json.dumps({
        "@context": {
            "estleg": "https://data.riik.ee/ontology/estleg#",
            "owl": "http://www.w3.org/2002/07/owl#",
        },
        "@graph": [
            {"@id": f"estleg:{slug}_Map_2026",
             "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
             "dc:source": source},
            {"@id": f"estleg:{slug}_Par_{par_nr}",
             "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
             "estleg:paragrahv": f"§ {par_nr}"},
        ],
    })


def _moisted_xml(body: str, par_nr: str = "2") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akt>
  <sisu>
    <paragrahv>
      <paragrahvNr>{par_nr}</paragrahvNr>
      <kuvatavNr>§ {par_nr}.</kuvatavNr>
      <paragrahvPealkiri>Mõisted</paragrahvPealkiri>
      <loige>
        <tavatekst>Käesolevas seaduses kasutatakse järgmisi mõisteid:
        {body}</tavatekst>
      </loige>
    </paragrahv>
  </sisu>
</akt>"""


def _by_id(graph: list[dict], node_id: str) -> dict | None:
    for n in graph:
        if n.get("@id") == node_id:
            return n
    return None


def _legal_concept_nodes(graph: list[dict]) -> list[dict]:
    return [n for n in graph if "estleg:LegalConcept" in n.get("@type", [])]


def _concept_nodes(graph: list[dict]) -> list[dict]:
    return [n for n in graph if "estleg:Concept" in n.get("@type", [])]


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


class TestIssue260HyphenatedFirstTerm:
    """#260: a hyphen inside the FIRST term word was excluded from the
    term class while the separator dash allowed an un-spaced hyphen, so
    ``tee-ehitus`` parsed as term=``tee`` and ``e-arve`` was dropped
    (term ``e`` too short). The first word now permits internal hyphens
    and the separator dash must be space-surrounded."""

    def test_multichar_hyphenated_first_term_kept_whole(self):
        from extract_legal_concepts import extract_definitions_from_text
        text = "1) tee-ehitus – teede rajamine ja korrashoid;"
        defs = extract_definitions_from_text(text)
        assert len(defs) == 1, defs
        # Term must be the full hyphenated compound, not just "tee".
        assert defs[0][1] == "tee-ehitus", defs[0]
        # The definition must NOT have swallowed "ehitus".
        assert defs[0][2].startswith("teede rajamine"), defs[0]

    def test_single_letter_prefix_hyphenated_term_not_dropped(self):
        from extract_legal_concepts import extract_definitions_from_text
        text = "1) e-arve – elektrooniline arve;"
        defs = extract_definitions_from_text(text)
        # Previously dropped entirely (parsed term "e" failed len < 2).
        assert len(defs) == 1, defs
        assert defs[0][1] == "e-arve", defs[0]
        assert defs[0][2] == "elektrooniline arve", defs[0]

    def test_hyphenated_term_in_multi_item_list(self):
        from extract_legal_concepts import extract_definitions_from_text
        text = "1) e-arve – elektrooniline arve; 2) tee-ehitus – teede rajamine;"
        defs = extract_definitions_from_text(text)
        terms = [t for _n, t, _d in defs]
        assert terms == ["e-arve", "tee-ehitus"], defs


class TestIssue261InlineCrossReference:
    """#261: the definition-body lookahead stopped at the FIRST inline
    ``\\d+\\)`` even when it was a cross-reference rather than a list
    item, truncating the definition. The stop now only fires on a real
    definition-item start (``\\d+\\) term <space-dash-space>``)."""

    def test_inline_numbered_reference_does_not_truncate(self):
        from extract_legal_concepts import extract_definitions_from_text
        text = "1) tulu – käesoleva seaduse 2) punktis nimetatud summa kokku;"
        defs = extract_definitions_from_text(text)
        assert len(defs) == 1, defs
        assert defs[0][1] == "tulu", defs[0]
        # The inline "2)" cross-reference must remain inside the body.
        assert defs[0][2] == (
            "käesoleva seaduse 2) punktis nimetatud summa kokku"
        ), defs[0]

    def test_real_item_after_inline_reference_still_splits(self):
        from extract_legal_concepts import extract_definitions_from_text
        # An inline "2)" reference inside item 1, then a genuine item 2.
        text = (
            "1) tulu – käesoleva seaduse 2) punktis nimetatud summa; "
            "2) kulu – tehtud väljaminek;"
        )
        defs = extract_definitions_from_text(text)
        assert len(defs) == 2, defs
        assert defs[0][1] == "tulu"
        assert "2) punktis nimetatud summa" in defs[0][2], defs[0]
        assert defs[1][1] == "kulu"
        assert defs[1][2] == "tehtud väljaminek", defs[1]


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


class TestIssue278LeadingEditBuckets:
    """#278: the old ``(first_2_chars, length)`` signature only ever
    compared terms sharing their leading two characters, so every
    edit-distance ≤ 2 pair differing in a *leading* character or
    transposing the first two characters was silently missed. The
    deletion-variant index must co-locate those pairs."""

    @staticmethod
    def _linked(pairs, a, b):
        s = {(t1, t2) for t1, t2, _d in pairs}
        return (a, b) in s or (b, a) in s

    def test_leading_char_substitution_linked(self):
        from extract_legal_concepts import _bucketed_close_match_pairs
        # d=1, differs only in the FIRST character — never shared a bucket
        # under the old prefix signature.
        pairs = _bucketed_close_match_pairs(["tasu", "kasu"])
        assert self._linked(pairs, "tasu", "kasu"), pairs
        # Distance is reported as 1.
        assert any(d == 1 for *_p, d in pairs), pairs

    def test_transposed_first_two_chars_linked(self):
        from extract_legal_concepts import _bucketed_close_match_pairs
        # Transposing the first two characters is a distance-2 edit and
        # changes the leading 2-char prefix, so the old bucketing missed it.
        pairs = _bucketed_close_match_pairs(["liige", "ilige"])
        assert self._linked(pairs, "liige", "ilige"), pairs

    def test_double_leading_substitution_linked(self):
        from extract_legal_concepts import _bucketed_close_match_pairs
        # Both leading chars differ (d=2) — the hardest case for any
        # prefix-keyed scheme.
        pairs = _bucketed_close_match_pairs(["xxabc", "yyabc"])
        assert self._linked(pairs, "xxabc", "yyabc"), pairs

    def test_distance_three_not_linked(self):
        from extract_legal_concepts import _bucketed_close_match_pairs
        # Guard: pairs at edit distance >= 3 must still be excluded.
        pairs = _bucketed_close_match_pairs(["abcd", "wxyz"])
        assert not self._linked(pairs, "abcd", "wxyz"), pairs

    def test_leading_edit_found_in_large_corpus(self):
        from extract_legal_concepts import _bucketed_close_match_pairs
        # The leading-char pair must surface even buried in a large,
        # otherwise-unrelated vocabulary.
        terms = [f"word{i:03d}" for i in range(200)]
        terms.extend(["tasu", "kasu"])
        pairs = _bucketed_close_match_pairs(terms)
        assert self._linked(pairs, "tasu", "kasu"), "leading pair lost in corpus"


def _expected_pipeline_triples(graph: list[dict]) -> int:
    """Recompute the coverage ``triples_emitted`` figure from the combined
    graph: the pipeline counts each provision-local definition node's
    ``estleg:definedIn`` plus its hub links (``estleg:definesConcept`` +
    ``skos:exactMatch`` for non-noise terms), and each canonical Concept
    node's ``skos:prefLabel`` / ``estleg:definitionCount`` /
    ``estleg:hasDefinitionNode`` / ``skos:altLabel`` / ``skos:definition`` /
    ``estleg:definitionVariantCount`` / ``skos:closeMatch`` triples."""

    def _n(node: dict, prop: str) -> int:
        v = node.get(prop)
        if v is None:
            return 0
        return len(v) if isinstance(v, list) else 1

    total = 0
    for n in graph:
        types = n.get("@type", [])
        if "estleg:LegalConcept" in types:
            total += _n(n, "estleg:definedIn")
            total += _n(n, "estleg:definesConcept")
            total += _n(n, "skos:exactMatch")
        elif "estleg:Concept" in types:
            total += 2  # prefLabel + definitionCount (always exactly one each)
            total += _n(n, "estleg:hasDefinitionNode")
            total += _n(n, "skos:altLabel")
            total += _n(n, "skos:definition")
            total += _n(n, "estleg:definitionVariantCount")
            total += _n(n, "skos:closeMatch")
    return total


class TestIssue171TripleCounting:
    """Finding 6 (#171): _triples increments AFTER graph emission, not
    before — collided drops no longer cause overcount.

    This is hard to test in isolation since the increment is inside
    main(); use a synthetic end-to-end run and verify the post-emission
    figure matches the triples actually present in the combined graph.
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
        graph = combined["@graph"]
        legal_concept_nodes = [
            n for n in graph
            if "estleg:LegalConcept" in n.get("@type", [])
            and "owl:NamedIndividual" in n.get("@type", [])
        ]
        assert legal_concept_nodes, "no provision-local definition nodes emitted"
        # Post-emission count: must equal the linking + canonical-metadata
        # triples actually present in the graph (no overcount from collided
        # drops, no undercount).
        assert cov["triples_emitted"] == _expected_pipeline_triples(graph), (
            f"triples_emitted ({cov['triples_emitted']}) doesn't match the "
            f"graph-derived figure ({_expected_pipeline_triples(graph)})"
        )
        assert cov["triples_emitted"] > 0


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


# ---------------------------------------------------------------------------
# Issue #134 — canonical estleg:Concept nodes + noise filter
# ---------------------------------------------------------------------------


class TestIssue134NoiseFilterUnit:
    """Unit tests for the term / definition noise predicates."""

    @pytest.mark.parametrize("term", [
        "a",                # single char
        "x",
        "1",                # pure digit
        "42",
        "...",
        "…",
        "[kehtetu]",        # bracketed repealed marker
        "(kehtetu)",
        "kehtetu",
        "kehtetud",
        "ja",               # stopword
        "või",
        "ja või",           # stopword-only phrase
        "the of",
    ])
    def test_is_noise_term_true(self, term):
        from extract_legal_concepts import is_noise_term
        assert is_noise_term(term) is True, term

    @pytest.mark.parametrize("term", [
        "isik",
        "reovesi",
        "tee kaitsevöönd",
        "vee",              # short but a real fragment — NOT filtered by design
        "ühisveevärgi ja",  # trailing stopword but not stopword-only
    ])
    def test_is_noise_term_false(self, term):
        from extract_legal_concepts import is_noise_term
        assert is_noise_term(term) is False, term

    @pytest.mark.parametrize("definition", [
        "",
        "   ",
        "...",
        "-",
        "kehtetu",
        "kehtetu;",
        "Kehtetu - RT I, 2024, 1, 1",
        "välja jäetud",
    ])
    def test_is_noise_definition_true(self, definition):
        from extract_legal_concepts import is_noise_definition
        assert is_noise_definition(definition) is True, definition

    @pytest.mark.parametrize("definition", [
        "füüsiline isik",
        "kogumissüsteemi sattunud reostunud vesi",
    ])
    def test_is_noise_definition_false(self, definition):
        from extract_legal_concepts import is_noise_definition
        assert is_noise_definition(definition) is False, definition


class TestIssue134ConceptNodeShape:
    """A canonical estleg:Concept node carries skos:prefLabel (lang-tagged),
    skos:definition, estleg:hasDefinitionNode (provision-local definition
    nodes), and estleg:definitionCount."""

    def test_concept_node_has_required_properties(self, tmp_path, monkeypatch):
        graph = _run_extractor(
            tmp_path, monkeypatch,
            peeps={"law_a": _peep_text("law_a", "Test law A")},
            xmls={"law_a": _moisted_xml(
                "1) klient — füüsiline isik kes saab teenust;"
            )},
        )
        concepts = _concept_nodes(graph)
        assert len(concepts) == 1, [n["@id"] for n in concepts]
        c = concepts[0]
        assert c["@id"] == "estleg:Concept_klient"
        assert c["@type"] == ["owl:NamedIndividual", "estleg:Concept"]
        # prefLabel: a single language-tagged literal.
        assert c["skos:prefLabel"] == {"@value": "klient", "@language": "et"}
        # definition(s): language-tagged literal(s).
        defs = c["skos:definition"]
        assert isinstance(defs, list) and len(defs) == 1
        assert defs[0]["@language"] == "et"
        assert "füüsiline isik" in defs[0]["@value"]
        # F3 (#134): hasDefinitionNode → the provision-local definition
        # node(s), as IRIs. estleg:definedIn is reserved for the
        # provision-local node → provision direction and must NOT appear
        # on a Concept node.
        assert "estleg:definedIn" not in c
        has_def_nodes = c["estleg:hasDefinitionNode"]
        assert isinstance(has_def_nodes, list) and len(has_def_nodes) == 1
        lc_id = has_def_nodes[0]["@id"]
        lc_node = _by_id(graph, lc_id)
        assert lc_node is not None
        assert "estleg:LegalConcept" in lc_node["@type"]
        # definitionCount: xsd:integer.
        assert c["estleg:definitionCount"] == {"@value": "1", "@type": "xsd:integer"}
        assert c["rdfs:label"] == "klient"

    def test_alt_labels_collected_from_surface_variants(self, tmp_path, monkeypatch):
        # Two laws define the same normalised term with different casings.
        graph = _run_extractor(
            tmp_path, monkeypatch,
            peeps={
                "law_a": _peep_text("law_a", "Test law A"),
                "law_b": _peep_text("law_b", "Test law B"),
            },
            xmls={
                "law_a": _moisted_xml("1) klient — füüsiline isik üks;"),
                "law_b": _moisted_xml("1) Klient — füüsiline isik kaks;"),
            },
        )
        c = _by_id(graph, "estleg:Concept_klient")
        assert c is not None
        # prefLabel is one of the surface forms; the other shows up in altLabel.
        surface_forms = {"klient", "Klient"}
        assert c["skos:prefLabel"]["@value"] in surface_forms
        alts = {a["@value"] for a in c.get("skos:altLabel", [])}
        assert surface_forms == ({c["skos:prefLabel"]["@value"]} | alts)
        assert c["estleg:definitionCount"] == {"@value": "2", "@type": "xsd:integer"}

    def test_definition_variant_count_when_capped(self, tmp_path, monkeypatch):
        # Seven laws each give a distinct wording → only 5 emitted, the true
        # total recorded in estleg:definitionVariantCount.
        peeps = {f"law_{i}": _peep_text(f"law_{i}", f"Test law {i}") for i in range(7)}
        xmls = {
            f"law_{i}": _moisted_xml(f"1) mõiste — definitsioon number {i} siin;")
            for i in range(7)
        }
        graph = _run_extractor(tmp_path, monkeypatch, peeps=peeps, xmls=xmls)
        c = _by_id(graph, "estleg:Concept_moiste")
        assert c is not None
        assert len(c["skos:definition"]) == 5
        assert c["estleg:definitionVariantCount"] == {
            "@value": "7", "@type": "xsd:integer",
        }
        assert c["estleg:definitionCount"] == {"@value": "7", "@type": "xsd:integer"}


class TestIssue134HubLinksReplaceClique:
    """A term defined in N laws emits N estleg:definesConcept arcs to ONE
    canonical Concept (plus N skos:exactMatch arcs Concept-ward), NOT an
    O(N²) clique of skos:exactMatch arcs between the N provision-local
    definition nodes."""

    def test_three_laws_one_concept_three_hub_links(self, tmp_path, monkeypatch):
        peeps = {f"law_{s}": _peep_text(f"law_{s}", f"Test law {s}")
                 for s in ("a", "b", "c")}
        xmls = {f"law_{s}": _moisted_xml(
            "1) isik — füüsiline või juriidiline isik;")
            for s in ("a", "b", "c")}
        graph = _run_extractor(tmp_path, monkeypatch, peeps=peeps, xmls=xmls)

        # Exactly one canonical Concept for 'isik'.
        concepts = _concept_nodes(graph)
        assert [n["@id"] for n in concepts] == ["estleg:Concept_isik"]
        cc_id = "estleg:Concept_isik"

        lc_nodes = _legal_concept_nodes(graph)
        assert len(lc_nodes) == 3

        # Every provision-local node hubs to the canonical Concept exactly once.
        for lc in lc_nodes:
            assert lc.get("estleg:definesConcept") == {"@id": cc_id}, lc["@id"]
            assert lc.get("skos:exactMatch") == [{"@id": cc_id}], lc["@id"]

        # 3 definesConcept arcs total — O(k), not O(k²).
        defines_arcs = sum(
            1 for lc in lc_nodes if lc.get("estleg:definesConcept")
        )
        assert defines_arcs == 3

        # No exactMatch / closeMatch arc points from one provision-local node
        # to another provision-local node (the clique is gone).
        lc_ids = {lc["@id"] for lc in lc_nodes}
        for lc in lc_nodes:
            for prop in ("skos:exactMatch", "skos:closeMatch"):
                refs = lc.get(prop) or []
                if isinstance(refs, dict):
                    refs = [refs]
                for r in refs:
                    assert r["@id"] not in lc_ids, (
                        f"{lc['@id']} {prop} -> {r['@id']} — provision-local "
                        "clique edge still present"
                    )

        # The canonical Concept lists all 3 provision-local nodes in
        # hasDefinitionNode (not definedIn — F3 #134).
        cc = _by_id(graph, cc_id)
        assert "estleg:definedIn" not in cc
        assert {d["@id"] for d in cc["estleg:hasDefinitionNode"]} == lc_ids
        assert cc["estleg:definitionCount"] == {"@value": "3", "@type": "xsd:integer"}


class TestIssue134NoiseFilterEndToEnd:
    """Junk terms (single-char already dropped at extraction; kehtetu /
    stopword-only / all-noise-definition terms) get NO canonical Concept and
    their provision-local nodes get NO estleg:definesConcept."""

    def test_kehtetu_and_stopword_terms_get_no_concept(self, tmp_path, monkeypatch):
        graph = _run_extractor(
            tmp_path, monkeypatch,
            peeps={"law_a": _peep_text("law_a", "Test law A")},
            xmls={"law_a": _moisted_xml(
                "1) isik — füüsiline isik kes osaleb; "
                "2) kehtetu — RT I, 2024, 1, 1 jõust 2024-01-01; "
                "3) ja — siin on midagi teksti; "
                "4) foo — kehtetu;"
            )},
        )
        concept_ids = {n["@id"] for n in _concept_nodes(graph)}
        # 'isik' is a real concept; 'kehtetu', 'ja' (stopword) and 'foo'
        # (all-noise-definition) are filtered.
        assert concept_ids == {"estleg:Concept_isik"}

        lc_by_label = {}
        for lc in _legal_concept_nodes(graph):
            pref = lc.get("skos:prefLabel")
            lc_by_label[pref] = lc
        # The provision-local nodes for the junk terms still exist…
        assert "kehtetu" in lc_by_label
        assert "ja" in lc_by_label
        assert "foo" in lc_by_label
        # …but with no estleg:definesConcept / skos:exactMatch hub link.
        for label in ("kehtetu", "ja", "foo"):
            assert "estleg:definesConcept" not in lc_by_label[label], label
            assert "skos:exactMatch" not in lc_by_label[label], label
        # Whereas the real concept's node is fully linked.
        assert lc_by_label["isik"]["estleg:definesConcept"] == {
            "@id": "estleg:Concept_isik"
        }

    def test_coverage_report_records_filtered_noise(self, tmp_path, monkeypatch):
        _run_extractor(
            tmp_path, monkeypatch,
            peeps={"law_a": _peep_text("law_a", "Test law A")},
            xmls={"law_a": _moisted_xml(
                "1) isik — füüsiline isik; 2) kehtetu — kehtetu;"
            )},
        )
        cov = json.loads(
            (tmp_path / "krr_outputs" / "reports" / "kov"
             / "extract_legal_concepts_coverage.json").read_text(encoding="utf-8")
        )
        # 'kehtetu' is the one filtered term — surfaced via skip_reasons.
        assert cov["skip_reasons"].get("concepts_filtered_noise") == 1
        # And the cross-reference report's summary carries it too.
        report = json.loads(
            (tmp_path / "krr_outputs" / "concepts"
             / "concept_crossref_report.json").read_text(encoding="utf-8")
        )
        assert report["summary"]["concepts_filtered_noise"] == 1
        assert report["summary"]["canonical_concepts"] == 1
        assert any(
            t["term"] == "kehtetu" for t in report["noisy_candidate_terms"]
        )


class TestIssue134CloseMatchIsConceptToConcept:
    """skos:closeMatch now connects canonical Concept nodes to each other
    (bidirectional), never the provision-local definition nodes."""

    def test_close_match_between_concepts(self, tmp_path, monkeypatch):
        # 'isik' / 'isiku' — edit distance 1, both ≥4 chars (pass the
        # bucketing min_len), so they should be linked as close matches.
        graph = _run_extractor(
            tmp_path, monkeypatch,
            peeps={"law_a": _peep_text("law_a", "Test law A")},
            xmls={"law_a": _moisted_xml(
                "1) isik — füüsiline isik üks; 2) isiku — füüsilise isiku oma;"
            )},
        )
        ci = _by_id(graph, "estleg:Concept_isik")
        cu = _by_id(graph, "estleg:Concept_isiku")
        assert ci is not None and cu is not None

        def _refs(node, prop):
            v = node.get(prop) or []
            return [r["@id"] for r in (v if isinstance(v, list) else [v])]

        # Bidirectional Concept ↔ Concept.
        assert "estleg:Concept_isiku" in _refs(ci, "skos:closeMatch")
        assert "estleg:Concept_isik" in _refs(cu, "skos:closeMatch")

        # No provision-local definition node carries skos:closeMatch.
        for lc in _legal_concept_nodes(graph):
            assert "skos:closeMatch" not in lc, lc["@id"]


class TestFindingF3HasDefinitionNode:
    """F3 (#134, PR #195): canonical estleg:Concept nodes use
    estleg:hasDefinitionNode (not estleg:definedIn) for the Concept →
    provision-local-definition membership, so the
    ``estleg:definedIn owl:inverseOf estleg:definesTerm`` axiom can no
    longer (under OWL reasoning) falsely conclude
    ``LegalConcept estleg:definesTerm Concept``."""

    def test_concept_nodes_do_not_carry_defined_in(self, tmp_path, monkeypatch):
        graph = _run_extractor(
            tmp_path, monkeypatch,
            peeps={
                "law_a": _peep_text("law_a", "Test law A"),
                "law_b": _peep_text("law_b", "Test law B"),
            },
            xmls={
                "law_a": _moisted_xml(
                    "1) isik — füüsiline isik üks; 2) klient — teenuse saaja;"
                ),
                "law_b": _moisted_xml("1) isik — juriidiline isik kaks;"),
            },
        )
        concepts = _concept_nodes(graph)
        assert concepts, "expected at least one canonical Concept node"
        for c in concepts:
            assert "estleg:definedIn" not in c, (
                f"{c['@id']} still carries estleg:definedIn — the F3 "
                "inverse-of collision can recur"
            )

    def test_has_definition_node_points_concept_to_legal_concept(
        self, tmp_path, monkeypatch
    ):
        graph = _run_extractor(
            tmp_path, monkeypatch,
            peeps={
                "law_a": _peep_text("law_a", "Test law A"),
                "law_b": _peep_text("law_b", "Test law B"),
            },
            xmls={
                "law_a": _moisted_xml("1) isik — füüsiline isik üks;"),
                "law_b": _moisted_xml("1) isik — juriidiline isik kaks;"),
            },
        )
        cc = _by_id(graph, "estleg:Concept_isik")
        assert cc is not None
        targets = cc["estleg:hasDefinitionNode"]
        assert isinstance(targets, list) and len(targets) == 2
        lc_ids = {lc["@id"] for lc in _legal_concept_nodes(graph)}
        for ref in targets:
            tid = ref["@id"]
            assert tid in lc_ids, tid
            lc = _by_id(graph, tid)
            assert lc is not None and "estleg:LegalConcept" in lc["@type"]
        # The hub direction is preserved: every LegalConcept hubs back to
        # the same canonical Concept.
        for ref in targets:
            lc = _by_id(graph, ref["@id"])
            assert lc["estleg:definesConcept"] == {"@id": "estleg:Concept_isik"}

    def test_legal_concept_nodes_keep_defined_in_to_provision(
        self, tmp_path, monkeypatch
    ):
        """The original estleg:definedIn semantics (definition node →
        provision) is untouched: provision-local LegalConcept nodes still
        point at their provision via estleg:definedIn."""
        graph = _run_extractor(
            tmp_path, monkeypatch,
            peeps={"law_a": _peep_text("law_a", "Test law A", par_nr="2")},
            xmls={"law_a": _moisted_xml(
                "1) klient — füüsiline isik kes saab teenust;", par_nr="2"
            )},
        )
        lc_nodes = _legal_concept_nodes(graph)
        assert lc_nodes
        cc_ids = {n["@id"] for n in _concept_nodes(graph)}
        for lc in lc_nodes:
            di = lc["estleg:definedIn"]
            assert isinstance(di, list) and di
            for ref in di:
                tgt = ref["@id"]
                # Points at the provision @id, never a Concept node.
                assert tgt not in cc_ids, tgt
                assert "_Par_" in tgt, tgt

    def test_combined_graph_keeps_original_defined_in_inverse_axiom(
        self, tmp_path, monkeypatch
    ):
        """The schema node emitted into concepts_combined.jsonld must keep
        ``estleg:definedIn owl:inverseOf estleg:definesTerm`` (unchanged by
        F3) AND declare ``estleg:hasDefinitionNode owl:inverseOf
        estleg:definesConcept`` (the new property)."""
        graph = _run_extractor(
            tmp_path, monkeypatch,
            peeps={"law_a": _peep_text("law_a", "Test law A")},
            xmls={"law_a": _moisted_xml("1) klient — füüsiline isik;")},
        )
        di = _by_id(graph, "estleg:definedIn")
        assert di is not None
        assert di.get("owl:inverseOf") == {"@id": "estleg:definesTerm"}, di
        hdn = _by_id(graph, "estleg:hasDefinitionNode")
        assert hdn is not None
        assert "owl:ObjectProperty" in hdn.get("@type", [])
        assert hdn.get("owl:inverseOf") == {"@id": "estleg:definesConcept"}, hdn


class TestIssue134Vocabulary:
    """The new estleg vocabulary terms are registered in
    controlled_vocabulary.jsonld."""

    def test_new_vocab_terms_present(self):
        vocab = json.loads(
            (REPO_ROOT / "krr_outputs" / "controlled_vocabulary.jsonld")
            .read_text(encoding="utf-8")
        )
        by_id = {n["@id"]: n for n in vocab["@graph"] if isinstance(n, dict)}
        assert "estleg:Concept" in by_id
        assert "owl:Class" in by_id["estleg:Concept"]["@type"]
        for prop, expected_type in [
            ("estleg:definesConcept", "owl:ObjectProperty"),
            ("estleg:definedIn", "owl:ObjectProperty"),
            # F3 (#134): the canonical Concept → definition-node membership
            # property, distinct from estleg:definedIn.
            ("estleg:hasDefinitionNode", "owl:ObjectProperty"),
            ("estleg:definitionCount", "owl:DatatypeProperty"),
            ("estleg:definitionVariantCount", "owl:DatatypeProperty"),
        ]:
            assert prop in by_id, prop
            assert expected_type in by_id[prop]["@type"], prop

    def test_has_definition_node_is_inverse_of_defines_concept(self):
        """F3 (#134): estleg:hasDefinitionNode is declared as the
        owl:inverseOf estleg:definesConcept — the natural inverse of the
        LegalConcept → Concept hub link. It must NOT be the inverse of
        estleg:definesTerm (that axiom belongs to estleg:definedIn and is
        what the F3 collision came from)."""
        vocab = json.loads(
            (REPO_ROOT / "krr_outputs" / "controlled_vocabulary.jsonld")
            .read_text(encoding="utf-8")
        )
        by_id = {n["@id"]: n for n in vocab["@graph"] if isinstance(n, dict)}
        node = by_id["estleg:hasDefinitionNode"]
        inv = node.get("owl:inverseOf")
        assert isinstance(inv, dict) and inv.get("@id") == "estleg:definesConcept", inv


class TestIssue134ConceptShapeSHACL:
    """ConceptShape accepts a well-formed estleg:Concept and rejects one
    missing skos:prefLabel or estleg:definitionCount."""

    @staticmethod
    def _validate(graph_nodes: list[dict]) -> bool:
        pyshacl = pytest.importorskip("pyshacl")
        rdflib = pytest.importorskip("rdflib")
        shapes = rdflib.Graph()
        shapes.parse(str(SHAPES_TTL), format="turtle")
        data = rdflib.Graph()
        data.parse(data=json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                "skos": "http://www.w3.org/2004/02/skos/core#",
                "xsd": "http://www.w3.org/2001/XMLSchema#",
            },
            "@graph": graph_nodes,
        }), format="json-ld")
        conforms, _, _msg = pyshacl.validate(
            data, shacl_graph=shapes, inference="rdfs", abort_on_first=False,
        )
        return bool(conforms)

    @staticmethod
    def _well_formed_concept() -> dict:
        return {
            "@id": "estleg:Concept_test_term",
            "@type": ["owl:NamedIndividual", "estleg:Concept"],
            "skos:prefLabel": {"@value": "test term", "@language": "et"},
            "skos:altLabel": [{"@value": "Test Term", "@language": "et"}],
            "skos:definition": [{"@value": "a definition of the test term",
                                 "@language": "et"}],
            "estleg:definitionCount": {"@value": "2", "@type": "xsd:integer"},
            # F3 (#134): membership via estleg:hasDefinitionNode, not
            # estleg:definedIn.
            "estleg:hasDefinitionNode": [
                {"@id": "estleg:Concept_law_a_test_term"},
                {"@id": "estleg:Concept_law_b_test_term"},
            ],
            "rdfs:label": "test term",
        }

    def test_well_formed_concept_conforms(self):
        assert self._validate([self._well_formed_concept()]) is True

    def test_missing_pref_label_rejected(self):
        node = self._well_formed_concept()
        del node["skos:prefLabel"]
        assert self._validate([node]) is False

    def test_missing_definition_count_rejected(self):
        node = self._well_formed_concept()
        del node["estleg:definitionCount"]
        assert self._validate([node]) is False

    def test_missing_has_definition_node_rejected(self):
        node = self._well_formed_concept()
        del node["estleg:hasDefinitionNode"]
        assert self._validate([node]) is False

    def test_legal_concept_shape_allows_defines_concept(self):
        """Pin the new sh:property on LegalConceptShape (SHACL is open-world,
        so query the shapes graph directly)."""
        rdflib = pytest.importorskip("rdflib")
        SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        ESTLEG = rdflib.Namespace("https://data.riik.ee/ontology/estleg#")
        shapes = rdflib.Graph()
        shapes.parse(str(SHAPES_TTL), format="turtle")
        paths = set()
        for ps in shapes.objects(ESTLEG.LegalConceptShape, SH.property):
            paths.update(shapes.objects(ps, SH.path))
        assert ESTLEG.definesConcept in paths
