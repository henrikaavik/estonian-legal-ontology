# KOV Integration Layer 2a — Foundation + Discovery Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Layer 2 schema (Citation class + 5 new properties + SHACL Citation shape) into the ontology, complete the per-caller audit on all 14 consumers of `iter_peep_files()`, flip the iterator default to `include_kov=True`, and fix the 5 KOV-relevant discovery-only pipelines (deontic, EuroVoc, temporal, legal-concepts, amendment-history) so they process KOV files.

**Architecture:** Schema changes are additive (no semantics yet — Citation/`issuedUnder`/`implementsCitation`/`implementedBy`/`implementedByCount`/`enforcedAtLevel` are declared but not emitted). The default iterator flip is paired with explicit `include_kov=False` pins on the 9 deferred or KOV-irrelevant call sites, so the input universe expands only where each pipeline is verified ready. A new `scripts/kov_pipeline_coverage.py` helper produces per-pipeline JSON coverage reports. Pre-2a baseline is implicit (KOV file count = 0 by construction, since `include_kov=False` was the iterator default and the helper itself didn't exist on `main`); the reports in this PR are the absolute "after" values reviewers verify on their own merits.

**Tech Stack:** Python 3.11+, pytest, JSON-LD, SHACL (pyshacl), rdflib. Builds on the Layer 1 `kov_registry.py` + `enrich_kov_layer1.py` infrastructure.

**Spec reference:** `docs/superpowers/specs/2026-05-02-kov-integration-design.md` (Layer 2 section).
**Layer 1 reference:** `docs/superpowers/plans/2026-05-02-kov-integration-layer1.md`.

---

## Crisp 2a invariant

After 2a lands:

1. The Layer 2 schema is fully declared in `metadata.jsonld` and `shacl/estonian_legal_shapes.ttl` — Citation class, `issuedUnder`, `implementsCitation`, `citationTarget`, `citationDetail`, `citationText`, `implementedBy`, `implementedByCount`, `enforcedAtLevel`. **No** Layer-2 triples are emitted yet — that's 2b and 2c.
2. `estleg_common.iter_peep_files()` defaults `include_kov=True`. Every call site has been audited. The 9 KOV-deferred or KOV-irrelevant pipelines (cross-ref, inverse, sanctions, competence, court-links, similarity, draft-impact, harmonisation-links, transposition-mapping) explicitly pin `include_kov=False` at their call sites — they remain laws-and-state-only until their dedicated PRs land.
3. The 5 KOV-relevant discovery-only pipelines (deontic, EuroVoc, temporal, legal-concepts, amendment-history) process KOV files end-to-end and emit their existing-shape triples (deontic classifications, EuroVoc tags, temporal dates, legal concepts, amendment chains) over the expanded input universe, with no regressions on the laws-and-state subset.
4. Each of the 5 fixed pipelines emits `krr_outputs/reports/kov/<pipeline>_coverage.json` with per-run metrics (counts, triples emitted, runtime, failure samples). Pre-2a, KOV file counts were 0 by iter-default; the reports here are the absolute "after" values that reviewers verify on their own merits (input_files_kov ≥ 11k, triples_emitted > 0, error_count ≤ small).

**Not in 2a:** preamble scanning (2b), `implementedBy` aggregation (2b), KOV-aware competence resolution (2c), KOV citation resolver for court links (2c), `enforcedAtLevel` derivation (2c), Citation reified instances (2b — only the schema lands here).

**Gate B is the umbrella milestone.** It is reached once 2a, 2b, and 2c have all landed.

---

## File Structure

### New files

| Path | Responsibility |
| --- | --- |
| `scripts/kov_pipeline_coverage.py` | Pure-function instrumentation helper. Wraps a pipeline's main with metrics capture; writes `krr_outputs/reports/kov/<pipeline>_coverage.json`. |
| `tests/test_kov_pipeline_coverage.py` | Unit tests for the coverage helper. |
| `tests/test_classify_eurovoc.py` | Discovery-fix tests for EuroVoc. |
| `tests/test_extract_temporal_data.py` | Discovery + globaalID-pairing tests for temporal. |
| `tests/test_extract_legal_concepts.py` | IRI-sourcing tests for legal-concepts. |
| `tests/test_generate_amendment_history.py` | Discovery + globaalID-pairing tests for amendment-history. |
| `tests/test_classify_deontic.py` | KOV-discovery smoke tests for deontic. |
| `tests/test_iter_peep_files.py` | Tests for the default flip in `estleg_common.iter_peep_files()`. |
| `tests/fixtures/kov_layer2a/` | Fixture bundle: minimal KOV act, minimal law, minimal state regulation, minimal XML for date extraction. |
| `krr_outputs/reports/kov/<pipeline>_coverage.json` (×5) | Generated per-pipeline coverage reports, committed. |
| `krr_outputs/reports/kov/README.md` | Documents the coverage report format and the baseline-by-construction verification workflow for reviewers. |

### Modified files

| Path | Change |
| --- | --- |
| `metadata.jsonld` | Promote Citation from placeholder to full declaration (citationTarget/citationDetail/citationText properties); add 5 new act/provision properties (`issuedUnder`, `implementsCitation`, `implementedBy`, `implementedByCount`, `enforcedAtLevel`). |
| `shacl/estonian_legal_shapes.ttl` | Append CitationShape (sh:targetClass Citation; required citationTarget; optional citationDetail/citationText; IRI pattern). |
| `scripts/estleg_common.py` | Flip default of `iter_peep_files(include_kov=...)` from `False` to `True`. |
| `scripts/classify_deontic.py` | Audited; coverage-instrumented. No semantic change. |
| `scripts/classify_eurovoc.py` | Switch input discovery from `INDEX.json` to `iter_peep_files`; pull act-level title/source from each peep file's act node; remove the root-only filter; output paths use source path; coverage-instrumented. |
| `scripts/extract_temporal_data.py` | Recursive XML discovery (`maarus/`, `maarus_kov/`); new `globalId → XML path` lookup helper; remove root-only filter; coverage-instrumented. |
| `scripts/extract_legal_concepts.py` | Source provision IRIs from each node's `@id` (not `f"{slug}_Par_{n}"`); remove root-only filter; output paths use source path; coverage-instrumented. |
| `scripts/generate_amendment_history.py` | Recursive XML discovery; reuse the `globalId → XML path` helper from temporal; remove root-only filters; coverage-instrumented. |
| `scripts/extract_cross_references.py` | Pin `include_kov=False` at all 3 call sites (`# DEFERRED to Layer 2b`). |
| `scripts/generate_inverse_references.py` | Pin `include_kov=False` at all 3 call sites (`# DEFERRED to Layer 2b`). |
| `scripts/extract_sanctions.py` | Pin `include_kov=False` (`# DEFERRED to Layer 2c`). |
| `scripts/extract_institutional_competence.py` | Pin `include_kov=False` (`# DEFERRED to Layer 2c`). |
| `scripts/extract_court_provision_links.py` | Pin `include_kov=False` at both call sites (`# DEFERRED to Layer 2c`). |
| `scripts/generate_similarity_index.py` | Pin `include_kov=False` at both call sites (`# DEFERRED to Layer 3`). |
| `scripts/extract_draft_impact.py` | Pin `include_kov=False` at both call sites (`# KOV does not apply — EU/draft pipeline`). |
| `scripts/generate_harmonisation_links.py` | Pin `include_kov=False` (`# KOV does not apply`). |
| `scripts/generate_transposition_mapping.py` | Pin `include_kov=False` (`# KOV does not apply`). |
| `docs/SCHEMA_REFERENCE.md` | Document the new Citation class and 5 new properties (declared, not yet populated). |
| `docs/REGULATIONS_INTEGRATION_PLAN.md` | Mark Layer 2a complete. |
| `CHANGELOG.md` | Entry: "Add KOV integration Layer 2a (foundation + discovery fixes)". |
| `README.md` | Note that 5 pipelines now process KOV; new schema terms declared. |

---

### Schema additions reference

**New classes** (Citation class declared as placeholder in Layer 1; this PR adds its property declarations):

```turtle
estleg:Citation a owl:Class ;
    rdfs:label "Citation" ;
    rdfs:comment "Reified preamble citation. Each instance carries a
                  resolved provision target plus optional sub-paragraph
                  specificity and the original citation text. Layer 2b
                  populates instances; Layer 2a only declares the
                  shape." .
```

**New properties** (all declared in `metadata.jsonld`; no triples emitted in 2a):

| Property | Domain | Range | Purpose |
| --- | --- | --- | --- |
| `estleg:issuedUnder` | `Act` | `Act` | Act → enabling act (preamble simple form). Populated 2b. |
| `estleg:implementsCitation` | `Act` | `Citation` | Act → reified Citation node. Populated 2b. |
| `estleg:citationTarget` | `Citation` | `LegalProvision` | Citation → resolved provision IRI. Populated 2b. |
| `estleg:citationDetail` | `Citation` | `xsd:string` | Optional sub-paragraph string ("lg 1 p 3"). Populated 2b. |
| `estleg:citationText` | `Citation` | `xsd:string` | Optional original citation string. Populated 2b. |
| `estleg:implementedBy` | `Act` ∪ `LegalProvision` | `Act` | Inverse of issuedUnder + Citation.citationTarget. Populated 2b. |
| `estleg:implementedByCount` | `Act` ∪ `LegalProvision` | `xsd:integer` | Aggregate count of implementedBy. Populated 2b. |
| `estleg:enforcedAtLevel` | `LegalProvision` | `xsd:string` | "state" \| "municipality". Populated 2c. |

**IRI patterns:**
- `estleg:Citation_<source-act-shortid>_<seq>` — Citation instances. Asserted by SHACL `sh:pattern` once instances exist (2b).

---

## Task 1: Promote Citation to a fully-declared class in metadata.jsonld

**Files:**
- Modify: `metadata.jsonld`
- Modify: `tests/test_kov_registry.py` (extend `TestMetadataJsonLd`)

Layer 1 declared `estleg:Citation` as a placeholder class with only `@id`/`@type`/`rdfs:label`/`rdfs:comment`. This task adds full declarations for its three properties (`citationTarget`, `citationDetail`, `citationText`) plus the 5 new act/provision properties listed in the schema reference above.

- [ ] **Step 1: Extend the existing schema smoke test**

In `tests/test_kov_registry.py`, find `class TestMetadataJsonLd::test_new_classes_present` and extend the required-id list with the new property identifiers. Find the existing list:

```python
        for required in (
            "estleg:Act", "estleg:Law",
            "estleg:Municipality", "estleg:Issuer", "estleg:KovProvision",
            "estleg:Citation", "estleg:Similarity",
            "estleg:enactedBy", "estleg:enactedByMunicipality",
            "estleg:titleNormalized", "estleg:partOfAct",
            "estleg:ehakCode", "estleg:county", "estleg:bodyType",
            "estleg:currentMunicipality", "estleg:historicalMunicipalityName",
            "estleg:mappingSource", "estleg:mappingEvidence",
        ):
```

Replace with:

```python
        for required in (
            "estleg:Act", "estleg:Law",
            "estleg:Municipality", "estleg:Issuer", "estleg:KovProvision",
            "estleg:Citation", "estleg:Similarity",
            "estleg:enactedBy", "estleg:enactedByMunicipality",
            "estleg:titleNormalized", "estleg:partOfAct",
            "estleg:ehakCode", "estleg:county", "estleg:bodyType",
            "estleg:currentMunicipality", "estleg:historicalMunicipalityName",
            "estleg:mappingSource", "estleg:mappingEvidence",
            # Layer 2a additions:
            "estleg:citationTarget", "estleg:citationDetail",
            "estleg:citationText",
            "estleg:issuedUnder", "estleg:implementsCitation",
            "estleg:implementedBy", "estleg:implementedByCount",
            "estleg:enforcedAtLevel",
        ):
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_kov_registry.py::TestMetadataJsonLd::test_new_classes_present -v`
Expected: FAIL on the first missing id (one of the 8 new properties).

- [ ] **Step 3: Add the 8 property nodes to `metadata.jsonld`**

Open `metadata.jsonld`, locate the `@graph` array, and append these 8 nodes inside it (immediately after the existing Layer 1 property declarations):

```json
{
  "@id": "estleg:citationTarget",
  "@type": "owl:ObjectProperty",
  "rdfs:label": "citationTarget",
  "rdfs:comment": "Resolved provision IRI for a Citation. Range: LegalProvision (paragraph-level — sub-paragraph detail rides on citationDetail).",
  "rdfs:range": {"@id": "estleg:LegalProvision"}
},
{
  "@id": "estleg:citationDetail",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "citationDetail",
  "rdfs:comment": "Optional sub-paragraph specificity for a Citation, e.g. 'lg 1 p 3'. Layer 2b populates from preamble text.",
  "rdfs:range": {"@id": "xsd:string"}
},
{
  "@id": "estleg:citationText",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "citationText",
  "rdfs:comment": "Optional original citation string for traceability, e.g. '§ 42 lg 1 alusel'.",
  "rdfs:range": {"@id": "xsd:string"}
},
{
  "@id": "estleg:issuedUnder",
  "@type": "owl:ObjectProperty",
  "rdfs:label": "issuedUnder",
  "rdfs:comment": "Act → enabling act link, derived from preamble citations. Range: Act (Law, NationalRegulation, MunicipalRegulation). Layer 2b populates.",
  "rdfs:range": {"@id": "estleg:Act"}
},
{
  "@id": "estleg:implementsCitation",
  "@type": "owl:ObjectProperty",
  "rdfs:label": "implementsCitation",
  "rdfs:comment": "Act → reified Citation node carrying the resolved target plus original detail. Layer 2b populates.",
  "rdfs:range": {"@id": "estleg:Citation"}
},
{
  "@id": "estleg:implementedBy",
  "@type": "owl:ObjectProperty",
  "rdfs:label": "implementedBy",
  "rdfs:comment": "Act → acts enacted under this act. Filtered projection of preamble enabling-link inverses (issuedUnder + Citation.citationTarget). Range: Act. Layer 2b populates.",
  "rdfs:range": {"@id": "estleg:Act"}
},
{
  "@id": "estleg:implementedByCount",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "implementedByCount",
  "rdfs:comment": "Aggregate count of implementedBy entries on an act or provision. Layer 2b populates.",
  "rdfs:range": {"@id": "xsd:integer"}
},
{
  "@id": "estleg:enforcedAtLevel",
  "@type": "owl:DatatypeProperty",
  "rdfs:label": "enforcedAtLevel",
  "rdfs:comment": "Sanction enforcement level: 'state' or 'municipality'. Derived in Layer 2c from the parent act's type.",
  "rdfs:range": {"@id": "xsd:string"}
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_kov_registry.py::TestMetadataJsonLd -v`
Expected: 2 passed (both methods).

Confirm the file is still valid JSON:

```
python3 -m json.tool metadata.jsonld > /dev/null && echo "JSON OK"
```

- [ ] **Step 5: Commit**

```bash
git add metadata.jsonld tests/test_kov_registry.py
git commit -m "Declare Layer 2 schema (Citation properties + 5 act/provision properties)"
```

---

## Task 2: SHACL Citation shape

**Files:**
- Modify: `shacl/estonian_legal_shapes.ttl`
- Modify: `tests/test_shacl_kov_layer1.py` (extend with a Citation shape test class)

Layer 1 declared `estleg:Citation` as a placeholder class but did not add a SHACL shape (no instances yet). This task adds `CitationShape` so Layer 2b's emitted Citation nodes will be validated immediately when they appear.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shacl_kov_layer1.py`:

```python
class TestCitationShape:
    def test_valid_citation_passes(self):
        # Minimal graph: Citation pointing to any IRI target. The shape
        # only enforces nodeKind sh:IRI on citationTarget — class-level
        # range is documented in metadata.jsonld via rdfs:range, not
        # SHACL (avoiding subclass-inference brittleness).
        ok, msg = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Citation_1014955_Map_2026_1",
            "@type": ["owl:NamedIndividual", "estleg:Citation"],
            "estleg:citationTarget": {"@id": "estleg:Reg_1014955_Par_42"},
            "estleg:citationDetail": "lg 1",
            "estleg:citationText": "§ 42 lg 1 alusel",
        })
        assert ok, msg

    def test_citation_missing_target_fails(self):
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Citation_1014955_Map_2026_1",
            "@type": ["owl:NamedIndividual", "estleg:Citation"],
            "estleg:citationDetail": "lg 1",
        })
        assert not ok

    def test_citation_iri_pattern_enforced(self):
        # Provide a valid citationTarget so the failure can ONLY be the
        # pattern constraint (not the missing-target one).
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:NotACitation",  # wrong IRI pattern
            "@type": ["owl:NamedIndividual", "estleg:Citation"],
            "estleg:citationTarget": {"@id": "estleg:Reg_1014955_Par_42"},
        })
        assert not ok

    def test_citation_target_must_be_iri(self):
        # citationTarget as a literal (not an IRI) must fail nodeKind.
        ok, _ = _validate({
            "@context": CONTEXT,
            "@id": "estleg:Citation_1014955_Map_2026_1",
            "@type": ["owl:NamedIndividual", "estleg:Citation"],
            "estleg:citationTarget": "literal-not-iri",
        })
        assert not ok
```

- [ ] **Step 2: Run test (expect failure)**

Run: `pytest tests/test_shacl_kov_layer1.py::TestCitationShape -v`
Expected: shapes don't exist; positive test fails (no constraint to satisfy by adding `Citation` typing — actually it passes trivially, since SHACL with no shape says all valid). Negative tests fail because no shape rejects them. So the entire class is in an "all wrongly-pass" state until the shape is added.

- [ ] **Step 3: Append `CitationShape` to `shacl/estonian_legal_shapes.ttl`**

Add at the end of the file:

```turtle
# CitationShape — Layer 2b populates instances; Layer 2a declares the shape.
#
# Note on citationTarget: we deliberately do NOT use sh:class
# estleg:LegalProvision for the target, mirroring the project-wide
# convention used by LegalProvisionShape (which uses
# sh:targetSubjectsOf estleg:paragrahv to dodge subclass-inference
# requirements). KovProvision instances are typed as
# estleg:KovProvision (and per-act subclasses) but not directly as
# estleg:LegalProvision; pyshacl with inference="rdfs" needs the
# subclass triples loaded into the data graph to resolve that.
# Validating only that citationTarget is an IRI (sh:nodeKind sh:IRI)
# avoids the brittleness; the LegalProvision range constraint is
# documented via rdfs:range in metadata.jsonld.
estleg:CitationShape
    a sh:NodeShape ;
    sh:targetClass estleg:Citation ;
    sh:nodeKind sh:IRI ;
    sh:pattern "^https://data\\.riik\\.ee/ontology/estleg#Citation_[A-Za-z0-9_]+_[0-9]+$" ;
    sh:property [
        sh:path estleg:citationTarget ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:nodeKind sh:IRI ;
        sh:name "citationTarget" ;
    ] ;
    sh:property [
        sh:path estleg:citationDetail ;
        sh:maxCount 1 ;
        sh:datatype xsd:string ;
        sh:name "citationDetail" ;
    ] ;
    sh:property [
        sh:path estleg:citationText ;
        sh:maxCount 1 ;
        sh:datatype xsd:string ;
        sh:name "citationText" ;
    ] .
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_shacl_kov_layer1.py -v`
Expected: 11 passed (the 7 prior shapes + 4 new Citation tests).

- [ ] **Step 5: Commit**

```bash
git add shacl/estonian_legal_shapes.ttl tests/test_shacl_kov_layer1.py
git commit -m "Add SHACL CitationShape for Layer 2b instance validation"
```

---

## Task 3: Per-caller audit + pin `include_kov=False` on the 9 deferred/irrelevant call sites

**Files:**
- Modify: `scripts/extract_cross_references.py` (3 call sites)
- Modify: `scripts/generate_inverse_references.py` (3 call sites)
- Modify: `scripts/extract_sanctions.py` (1 call site)
- Modify: `scripts/extract_institutional_competence.py` (1 call site)
- Modify: `scripts/extract_court_provision_links.py` (2 call sites)
- Modify: `scripts/generate_similarity_index.py` (2 call sites)
- Modify: `scripts/extract_draft_impact.py` (2 call sites)
- Modify: `scripts/generate_harmonisation_links.py` (1 call site)
- Modify: `scripts/generate_transposition_mapping.py` (1 call site)

This task **must precede** Task 4 (the iterator default flip). Without these pins, flipping the default would silently route KOV files into pipelines that aren't ready, producing wrong/incomplete output.

The pinning pattern is uniform: every `iter_peep_files()` call without an explicit `include_kov=...` argument gets `include_kov=False` plus a one-line comment indicating which Layer (2b, 2c, or 3) will unpin it, or "permanent" for the EU-only ones.

- [ ] **Step 1: Pin all 9 scripts in one commit**

For each call site, the textual replacement depends on context: a
naive `iter_peep_files()` → `iter_peep_files(include_kov=False)  #
DEFERRED` swap **breaks Python syntax** when the call is inside a
`for ... in iter_peep_files():` loop, because the `# comment` lands
between the closing `)` and the `:`.

Two safe patterns:

**Pattern A — `for x in iter_peep_files():` loops:**

```python
# BEFORE
for json_file in iter_peep_files():
    ...

# AFTER (note the colon comes BEFORE the comment)
for json_file in iter_peep_files(include_kov=False):  # DEFERRED to Layer 2b
    ...
```

**Pattern B — `xs = iter_peep_files()` assignments:**

```python
# BEFORE
law_files = iter_peep_files()

# AFTER (comment can go inline at the end of the assignment)
law_files = iter_peep_files(include_kov=False)  # DEFERRED to Layer 2b
```

Both forms keep `:` adjacent to `)` for the loop case and put the
comment after the full statement.

For each script, first list the call sites:

```bash
grep -n "iter_peep_files()" scripts/extract_cross_references.py
```

Then apply Pattern A or B per site by reading the surrounding line.
Required pins:

- `scripts/extract_cross_references.py` — 3 sites; comment `# DEFERRED to Layer 2b`
- `scripts/generate_inverse_references.py` — 3 sites; comment `# DEFERRED to Layer 2b`
- `scripts/extract_sanctions.py` — 1 site; comment `# DEFERRED to Layer 2c`
- `scripts/extract_institutional_competence.py` — 1 site; comment `# DEFERRED to Layer 2c`
- `scripts/extract_court_provision_links.py` — 2 sites; comment `# DEFERRED to Layer 2c`
- `scripts/generate_similarity_index.py` — 2 sites; comment `# DEFERRED to Layer 3`
- `scripts/extract_draft_impact.py` — 2 sites; comment `# KOV does not apply (EU/draft pipeline)`
- `scripts/generate_harmonisation_links.py` — 1 site; comment `# KOV does not apply`
- `scripts/generate_transposition_mapping.py` — 1 site; comment `# KOV does not apply`

After editing, sanity-check that every modified script still imports
without syntax errors:

```bash
for script in scripts/extract_cross_references.py scripts/generate_inverse_references.py scripts/extract_sanctions.py scripts/extract_institutional_competence.py scripts/extract_court_provision_links.py scripts/generate_similarity_index.py scripts/extract_draft_impact.py scripts/generate_harmonisation_links.py scripts/generate_transposition_mapping.py; do
    python3 -c "import ast; ast.parse(open('$script').read())" && echo "OK $script" || echo "SYNTAX ERROR $script"
done
```

Expected: all 9 print `OK`. Any `SYNTAX ERROR` must be fixed before commit.

- [ ] **Step 2: Verify the changes**

After Task 3, the only `iter_peep_files()` calls without an explicit
`include_kov=` argument should be in the 5 KOV-ready pipelines that are
supposed to inherit the new `include_kov=True` default — those are
adjusted in Tasks 6–10. Every other consumer must be pinned now.

```bash
python3 - <<'PY'
import re, sys
from pathlib import Path

# Whitelist: scripts that intentionally rely on the new default (KOV-ready)
KOV_READY = {
    "classify_deontic.py",
    "classify_eurovoc.py",
    "extract_temporal_data.py",
    "extract_legal_concepts.py",
    "generate_amendment_history.py",
}

defaulted = []  # iter_peep_files( with no include_kov in the args
errors = []
for path in sorted(Path("scripts").glob("*.py")):
    if path.name == "estleg_common.py":
        continue
    text = path.read_text()
    for m in re.finditer(r"iter_peep_files\(([^)]*)\)", text):
        args = m.group(1)
        if "include_kov" not in args:
            defaulted.append((path.name, args.strip() or "<no args>"))

unexpected = [(n, a) for n, a in defaulted if n not in KOV_READY]
print(f"defaulted calls (KOV-ready, OK): "
      f"{sum(1 for n,_ in defaulted if n in KOV_READY)}")
print(f"defaulted calls (UNEXPECTED — must be pinned):")
for n, a in unexpected:
    print(f"  {n}: iter_peep_files({a})")
sys.exit(1 if unexpected else 0)
PY
```

Expected: zero unexpected defaulted calls (exit 0). The KOV-ready scripts
will appear in the "OK" count — Tasks 6–10 will then verify each one
processes KOV correctly.

- [ ] **Step 3: Run the full test suite**

```
pytest -q
```

Expected: still 102 passed (same as Layer 1 end state). No new tests yet; just verifying we didn't break anything.

- [ ] **Step 4: Commit**

```bash
git add scripts/extract_cross_references.py scripts/generate_inverse_references.py scripts/extract_sanctions.py scripts/extract_institutional_competence.py scripts/extract_court_provision_links.py scripts/generate_similarity_index.py scripts/extract_draft_impact.py scripts/generate_harmonisation_links.py scripts/generate_transposition_mapping.py
git commit -m "Pin include_kov=False on 9 deferred/irrelevant pipelines

Pre-flip audit: explicitly opt out of KOV input on every consumer that
isn't ready to handle it yet. Comments mark each pin as DEFERRED to
Layer 2b/2c/3 or 'KOV does not apply' (EU pipelines)."
```

---

## Task 4: Flip `iter_peep_files()` default to `include_kov=True`

**Files:**
- Modify: `scripts/estleg_common.py:133-158`
- Create: `tests/test_iter_peep_files.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_iter_peep_files.py`:

```python
"""Tests for the default-flip in estleg_common.iter_peep_files()."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from estleg_common import iter_peep_files


REPO_ROOT = Path(__file__).resolve().parent.parent
KRR_DIR = REPO_ROOT / "krr_outputs"


class TestIterPeepFilesDefault:
    def test_default_includes_kov(self):
        """After Layer 2a flips the default, calling iter_peep_files() with
        no arguments must include KOV files."""
        files = iter_peep_files()
        kov_files = [f for f in files if "regulations/kov" in str(f)]
        assert len(kov_files) > 1000, (
            f"expected KOV files in default iter; got {len(kov_files)}"
        )

    def test_explicit_include_kov_false_excludes(self):
        """Pinned call sites (Layer 2b/2c/3 deferrals) must still get a
        KOV-free file set when they pass include_kov=False."""
        files = iter_peep_files(include_kov=False)
        kov_files = [f for f in files if "regulations/kov" in str(f)]
        assert kov_files == []

    def test_default_includes_state_regulations(self):
        """include_regulations=True is still the default; state regs
        under regulations/riik/ should appear."""
        files = iter_peep_files()
        riik_files = [f for f in files if "regulations/riik" in str(f)]
        assert len(riik_files) > 100
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_iter_peep_files.py -v`
Expected: FAIL on `test_default_includes_kov` (current default is `include_kov=False`).

- [ ] **Step 3: Flip the default**

Open `scripts/estleg_common.py`, find `def iter_peep_files(` around line 133. Change:

```python
def iter_peep_files(
    *,
    include_laws: bool = True,
    include_regulations: bool = True,
    include_kov: bool = False,
) -> list[Path]:
```

to:

```python
def iter_peep_files(
    *,
    include_laws: bool = True,
    include_regulations: bool = True,
    include_kov: bool = True,
) -> list[Path]:
```

Update the docstring to reflect the change. Find:

```python
    """Return every ``*_peep.json`` ontology file managed by the pipeline.

    Pipeline-wide file iterator that includes both top-level laws and the
    state-level regulations under ``regulations/riik/``. KOV regulations
    (``regulations/kov/``) are opt-in to keep ~11k near-duplicate municipal
    acts out of routine cross-reference / similarity passes until KOV
    integration is explicitly ramped in.
    """
```

Replace with:

```python
    """Return every ``*_peep.json`` ontology file managed by the pipeline.

    Pipeline-wide file iterator that includes top-level laws, state-level
    regulations under ``regulations/riik/``, and KOV regulations under
    ``regulations/kov/``. The Layer 2a default flip (2026-05-03) made KOV
    inclusion the new baseline; pipelines that aren't yet KOV-ready opt
    out explicitly via ``include_kov=False`` (Layer 2b/2c/3 deferrals,
    or KOV-irrelevant pipelines like harmonisation links).
    """
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_iter_peep_files.py -v`
Expected: 3 passed.

Then run the full suite to confirm the 9 pinned pipelines still get a KOV-free file set:

```
pytest -q
```

Expected: prior count + 3 new iter_peep_files tests, all passing.

- [ ] **Step 5: Commit**

```bash
git add scripts/estleg_common.py tests/test_iter_peep_files.py
git commit -m "Flip iter_peep_files() default to include_kov=True

The 9 deferred/irrelevant pipelines pinned include_kov=False in the
prior commit, so this flip routes KOV files only to the 5 KOV-ready
pipelines (deontic, EuroVoc, temporal, legal-concepts, amendment-
history). Per-pipeline coverage reports in subsequent tasks let
reviewers verify the input universe shift."
```

---

## Task 5: Coverage instrumentation helper

**Files:**
- Create: `scripts/kov_pipeline_coverage.py`
- Create: `tests/test_kov_pipeline_coverage.py`
- Create: `krr_outputs/reports/kov/README.md`

A pure-function helper that wraps a pipeline run and produces a JSON report. Pipelines call into it at start/end of `main()`. Reports go to `krr_outputs/reports/kov/<pipeline>_coverage.json`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_kov_pipeline_coverage.py`:

```python
"""Tests for scripts/kov_pipeline_coverage.py — pipeline coverage reporter."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from kov_pipeline_coverage import (
    CoverageReport,
    write_coverage_report,
)


class TestCoverageReport:
    def test_dataclass_round_trips(self, tmp_path):
        report = CoverageReport(
            pipeline="classify_eurovoc",
            run_timestamp="2026-05-03T12:00:00Z",
            pipeline_version="abc1234",
            input_files_total=11796,
            input_files_kov=11059,
            files_processed=11796,
            files_skipped=0,
            skip_reasons={},
            triples_emitted=42000,
            fallback_hits=0,
            unresolved_references=0,
            wall_time_seconds=125.4,
            items_per_second=94.0,
            peak_memory_mb=312.5,
            error_count=0,
            failure_samples=[],
        )
        out = tmp_path / "coverage.json"
        write_coverage_report(report, out)

        with open(out, "r", encoding="utf-8") as fh:
            doc = json.load(fh)

        assert doc["pipeline"] == "classify_eurovoc"
        assert doc["input_files_total"] == 11796
        assert doc["input_files_kov"] == 11059
        assert doc["files_processed"] == 11796
        assert doc["files_skipped"] == 0
        assert doc["triples_emitted"] == 42000
        assert isinstance(doc["wall_time_seconds"], (int, float))
        assert isinstance(doc["items_per_second"], (int, float))
        assert isinstance(doc["peak_memory_mb"], (int, float))
        assert doc["pipeline_version"] == "abc1234"

    def test_required_fields_only(self, tmp_path):
        """All count/runtime fields default to zero — only pipeline/
        timestamp/version are required by the dataclass."""
        report = CoverageReport(
            pipeline="x",
            run_timestamp="t",
            pipeline_version="v",
            input_files_total=0,
            input_files_kov=0,
        )
        out = tmp_path / "c.json"
        write_coverage_report(report, out)
        doc = json.load(open(out, encoding="utf-8"))
        assert doc["files_processed"] == 0
        assert doc["skip_reasons"] == {}
        assert doc["fallback_hits"] == 0

    def test_failure_samples_capped(self, tmp_path):
        # Helper rejects more than 20 samples (the documented cap)
        report = CoverageReport(
            pipeline="x", run_timestamp="t", pipeline_version="v",
            input_files_total=0, input_files_kov=0,
            error_count=25,
            failure_samples=[f"sample-{i}" for i in range(25)],
        )
        out = tmp_path / "c.json"
        write_coverage_report(report, out)
        doc = json.load(open(out, encoding="utf-8"))
        assert len(doc["failure_samples"]) == 20

    def test_atomic_write(self, tmp_path):
        # Pre-existing file must be replaced atomically (temp + rename)
        out = tmp_path / "c.json"
        out.write_text('{"old": true}', encoding="utf-8")
        report = CoverageReport(
            pipeline="x", run_timestamp="t", pipeline_version="v",
            input_files_total=1, input_files_kov=0,
        )
        write_coverage_report(report, out)
        doc = json.load(open(out, encoding="utf-8"))
        assert "old" not in doc
        assert doc["pipeline"] == "x"


class TestPipelineVersionResolver:
    def test_returns_short_sha(self):
        from kov_pipeline_coverage import resolve_pipeline_version
        sha = resolve_pipeline_version()
        # 7-char short SHA from `git rev-parse --short HEAD`
        assert len(sha) >= 7
        assert all(c in "0123456789abcdef" for c in sha)
```

- [ ] **Step 2: Run the test (expect ImportError)**

Run: `pytest tests/test_kov_pipeline_coverage.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the helper**

Create `scripts/kov_pipeline_coverage.py`:

```python
"""KOV pipeline coverage reporter.

Each KOV-relevant pipeline calls into this helper at the end of its
main() to record per-run metrics. The reports live at
``krr_outputs/reports/kov/<pipeline>_coverage.json`` and let
reviewers verify the absolute "after" values against the implicit
zero-KOV baseline that pre-2a represented by construction.

Pure helpers — no global state. The caller assembles a CoverageReport
and calls write_coverage_report(report, out_path).
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path


_FAILURE_SAMPLE_CAP = 20


@dataclass
class CoverageReport:
    """Per-run coverage metrics. Field set matches the spec's
    "Per-pipeline KOV coverage report" requirement (counts, runtime
    metrics, failure samples)."""

    pipeline: str
    run_timestamp: str        # ISO-8601 UTC
    pipeline_version: str     # git short SHA

    # Coverage counts
    input_files_total: int
    input_files_kov: int
    files_processed: int = 0
    files_processed_kov: int = 0  # KOV subset of files_processed
    files_with_output: int = 0    # files that produced ≥1 output triple
    files_with_output_kov: int = 0  # KOV subset of files_with_output
    files_skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    triples_emitted: int = 0
    triples_emitted_kov: int = 0  # subset of triples_emitted attributed
                                   # to KOV inputs (per-pipeline definition)
    fallback_hits: int = 0
    unresolved_references: int = 0

    # Runtime metrics
    wall_time_seconds: float = 0.0
    items_per_second: float = 0.0
    peak_memory_mb: float = 0.0

    # Failures
    error_count: int = 0
    failure_samples: list[str] = field(default_factory=list)


def write_coverage_report(report: CoverageReport, path: Path) -> None:
    """Write a CoverageReport to a JSON file atomically.

    Caps failure_samples at 20 entries (per the spec); writes to a
    temp file in the same directory, then renames into place so a
    crash mid-write doesn't leave a partial file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(report)
    payload["failure_samples"] = payload["failure_samples"][:_FAILURE_SAMPLE_CAP]

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def resolve_pipeline_version() -> str:
    """Return the current git short SHA, or 'unknown' on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def measure_runtime(
    start_time: float, items_processed: int
) -> tuple[float, float, float]:
    """Compute the runtime metrics required by CoverageReport.

    Returns ``(wall_time_seconds, items_per_second, peak_memory_mb)``.

    Wall time uses ``time.perf_counter()`` (monotonic, sub-second
    resolution). Peak memory comes from ``resource.getrusage`` with
    a platform-specific normalization to MB (Linux ru_maxrss is in KB,
    macOS in bytes). On platforms where ``resource`` is unavailable
    (Windows), peak_memory_mb is 0.0.
    """
    import time as _time
    wall = _time.perf_counter() - start_time
    rate = items_processed / wall if wall > 0 else 0.0
    try:
        import resource
        import sys as _sys
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if _sys.platform == "darwin":
            peak_mb = rss / (1024 * 1024)  # macOS: bytes
        else:
            peak_mb = rss / 1024  # Linux: KB
    except ImportError:
        peak_mb = 0.0
    return wall, rate, peak_mb
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_kov_pipeline_coverage.py -v`
Expected: 5 passed (round-trip, required-fields-only, samples-capped, atomic-write, version-resolver).

- [ ] **Step 5: Add the README**

Create `krr_outputs/reports/kov/README.md`:

```markdown
# KOV pipeline coverage reports

Each KOV-relevant pipeline emits a per-run JSON report here at the end
of its `main()`. Reports include input file counts (total + KOV-only),
triples emitted, wall time, errors, and a capped sample of failures
for diagnostics.

## Format

```json
{
  "pipeline": "classify_eurovoc",
  "run_timestamp": "2026-05-03T12:00:00Z",
  "pipeline_version": "abc1234",
  "input_files_total": 15508,
  "input_files_kov": 11059,
  "files_processed": 15500,
  "files_processed_kov": 11055,
  "files_with_output": 14800,
  "files_with_output_kov": 10500,
  "files_skipped": 8,
  "skip_reasons": {"no_act_node": 6, "json_decode_error": 2},
  "triples_emitted": 42000,
  "triples_emitted_kov": 28000,
  "fallback_hits": 0,
  "unresolved_references": 0,
  "wall_time_seconds": 125.4,
  "items_per_second": 94.0,
  "peak_memory_mb": 312.5,
  "error_count": 0,
  "failure_samples": []
}
```

Field reference:

| Field | Meaning |
| --- | --- |
| `pipeline` | Script name (without `.py` and `scripts/` prefix). |
| `run_timestamp` | ISO-8601 UTC start time. |
| `pipeline_version` | Git short SHA at run time. |
| `input_files_total` | Total files iterated by `iter_peep_files()`. |
| `input_files_kov` | Subset under `regulations/kov/`. |
| `files_processed` | Files that classified/extracted successfully. |
| `files_processed_kov` | KOV subset of `files_processed`. |
| `files_with_output` | Files that produced ≥1 output triple. |
| `files_with_output_kov` | KOV subset of `files_with_output`. **The proof that KOV actually contributed; ≥1 ≪ files_processed_kov is fine, but 0 means KOV produced nothing.** |
| `files_skipped` | Files skipped, total. |
| `skip_reasons` | Skip-reason → count, one per category. |
| `triples_emitted` | Total output triples written back (laws + state-regs + KOV). |
| `triples_emitted_kov` | Subset attributed to KOV inputs. The KOV-output sanity check. |
| `fallback_hits` | Times a fallback path fired (slug-based XML resolution, etc.). |
| `unresolved_references` | References that couldn't pair (e.g. KOV peep with no XML). |
| `wall_time_seconds` | Total run wall time. |
| `items_per_second` | `files_processed / wall_time_seconds`. |
| `peak_memory_mb` | Peak RSS in MB (0.0 on Windows where `resource` is unavailable). |
| `error_count` | Number of exceptions captured. |
| `failure_samples` | Up to 20 failure messages for diagnosis. |

## Baseline-by-construction (the "before" state)

Pre-Layer-2a, none of these pipelines processed KOV files — `iter_peep_files()`
defaulted to `include_kov=False`, so the KOV-on-pipelines input universe
was empty by construction. The coverage helper itself didn't exist on
`main`, so a literal "checkout main and run the report" doesn't work
(no helper to invoke).

The "before" baseline is therefore implicit:

| Field | Pre-2a value (by construction) |
| --- | --- |
| `input_files_kov` | 0 |
| triples emitted from KOV files | 0 |

The "after" values come from this PR's coverage reports, committed at
`krr_outputs/reports/kov/<pipeline>_coverage.json`.

## Reviewer verification

Verify the absolute values are reasonable, not a literal diff:

1. `input_files_kov ≈ 11059` for each of the 5 fixed pipelines (full
   KOV corpus participated).
2. `input_files_total ≈ 15508` (current corpus size with KOV enabled).
3. **`files_with_output_kov > 0`** — the actual KOV-contributed-output
   check. Total `triples_emitted > 0` is not enough because laws/state
   regs alone could account for it while every KOV file is skipped.
   Likewise check `triples_emitted_kov > 0`.
4. `error_count` — ideally 0; small positive numbers OK if `failure_samples`
   shows the failures are KOV-specific edge cases rather than systemic.
5. `items_per_second` — sanity: classify_deontic ≫ extract_temporal_data
   in throughput because deontic is text-only and temporal does XML parsing.

If the implementer wants a true before/after diff later (e.g., when 2b
runs over a corpus that already has 2a's output), they can copy the 2a
report aside before re-running and diff manually. That is not a 2a
deliverable; 2a establishes the baseline.
```

- [ ] **Step 6: Commit**

```bash
git add scripts/kov_pipeline_coverage.py tests/test_kov_pipeline_coverage.py krr_outputs/reports/kov/README.md
git commit -m "Add KOV pipeline coverage reporter"
```

---

## Task 6: Fix `classify_deontic` — KOV discovery + coverage instrumentation

**Files:**
- Modify: `scripts/classify_deontic.py:151`
- Create: `tests/test_classify_deontic.py`
- Create: `tests/fixtures/kov_layer2a/sample_kov_act.json` (minimal; reused across the 5 pipeline-fix tasks)

`classify_deontic` already uses `iter_peep_files()`. With the Task 4 default flip, it now sees KOV files automatically. The remaining work: confirm it processes them, add coverage instrumentation.

- [ ] **Step 1: Create the shared fixture**

Create `tests/fixtures/kov_layer2a/sample_kov_act.json`:

```json
{
  "@context": {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/"
  },
  "@graph": [
    {
      "@id": "estleg:Reg_9999_Map_2026",
      "@type": ["owl:Ontology", "estleg:Act", "estleg:MunicipalRegulation"],
      "rdfs:label": "Test KOV act",
      "dc:source": "Test KOV act",
      "estleg:globalId": "999999999",
      "estleg:terviktekstId": "9999",
      "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
      "estleg:titleNormalized": "test"
    },
    {
      "@id": "estleg:Reg_9999_Par_1",
      "@type": ["owl:NamedIndividual", "estleg:KovProvision"],
      "estleg:paragrahv": "§ 1.",
      "rdfs:label": "§ 1. Test",
      "estleg:summary": "Korraldaja peab tagama jäätmete kogumise.",
      "estleg:legalText": "Korraldaja peab tagama jäätmete kogumise vastavalt määrusele.",
      "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0784"},
      "estleg:partOfAct": {"@id": "estleg:Reg_9999_Map_2026"}
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_classify_deontic.py`:

```python
"""Discovery and coverage tests for classify_deontic."""
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


class TestClassifyDeonticDiscoversKov:
    def test_classify_provision_returns_norm_iri(self):
        """Smoke test: the pipeline's per-provision classifier recognises
        the KOV fixture's 'peab tagama' as obligation. The function
        returns an estleg:NormType_* IRI (or None)."""
        from classify_deontic import classify_provision
        norm_iri = classify_provision(
            "Korraldaja peab tagama jäätmete kogumise vastavalt määrusele."
        )
        # Real return values from the existing NORM_TYPES table — IRIs,
        # not bare labels. Look at NORM_TYPES in classify_deontic.py for
        # the full set; obligation matches "peab" + "tagama".
        assert norm_iri is not None
        assert norm_iri.startswith("estleg:NormType_")

    def test_classify_provision_returns_none_for_descriptive(self):
        """Sanity: text without modal verbs returns None."""
        from classify_deontic import classify_provision
        norm_iri = classify_provision(
            "Käesoleva määruse alusel mõeldakse jäätmete all olmejäätmeid."
        )
        # Descriptive sentence — no modal — should return None.
        assert norm_iri is None
```

- [ ] **Step 3: Run test to verify it passes (sanity check — no fix needed yet)**

```
pytest tests/test_classify_deontic.py -v
```

Expected: 2 passed (existing classifier already handles arbitrary Estonian text).

- [ ] **Step 4: Add coverage instrumentation to `classify_deontic.py`**

This is the **canonical instrumentation pattern** that the four other
pipeline-fix tasks (Tasks 7–10) reference verbatim. All five pipelines
use the same shape — only `pipeline=` and the metric-counter names
differ.

Open `scripts/classify_deontic.py` and add to the top of the imports:

```python
import time
from datetime import datetime, timezone
from kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)
```

Wrap `main()` with timing + counters. Track six things during the run:

| Counter | Meaning |
| --- | --- |
| `_files_processed` | files that were read and classified (succeeded) |
| `_files_skipped` | files skipped, total |
| `_skip_reasons` | dict of skip-reason → count |
| `_triples` | triples emitted (each script counts these its own way) |
| `_fallback_hits` | times a fallback path was used |
| `_unresolved` | references that didn't resolve (where applicable) |
| `_failures` | exception messages or skip-detail samples |

Layout:

```python
def main() -> int:
    _start = time.perf_counter()
    _files = iter_peep_files()  # uses new include_kov=True default
    _kov_files = [f for f in _files if "regulations/kov" in str(f)]
    _files_processed = 0
    _files_skipped = 0
    _skip_reasons: dict[str, int] = {}
    _triples = 0
    _fallback_hits = 0
    _unresolved = 0
    _failures: list[str] = []

    _files_processed_kov = 0
    _files_with_output = 0
    _files_with_output_kov = 0
    _triples_kov = 0

    for f in _files:
        is_kov = "regulations/kov" in str(f)
        try:
            _triples_before = _triples
            # ... existing per-file logic ...
            # On each successful classification add to _triples (and to
            # _triples_kov when is_kov).
            # On per-provision skip ("no summary"), bump
            # _skip_reasons["no_summary"] and _files_skipped if the
            # whole file was skipped. Never silently skip — always
            # categorize.
            _files_processed += 1
            if is_kov:
                _files_processed_kov += 1
            if _triples > _triples_before:
                _files_with_output += 1
                if is_kov:
                    _files_with_output_kov += 1
                    _triples_kov += (_triples - _triples_before)
        except Exception as exc:  # noqa: BLE001
            _failures.append(f"{f.name}: {exc!r}")

    # ... post-processing, summary writes, etc. ...

    _wall, _rate, _peak_mb = measure_runtime(_start, _files_processed)
    write_coverage_report(
        CoverageReport(
            pipeline="classify_deontic",
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(_files),
            input_files_kov=len(_kov_files),
            files_processed=_files_processed,
            files_processed_kov=_files_processed_kov,
            files_with_output=_files_with_output,
            files_with_output_kov=_files_with_output_kov,
            files_skipped=_files_skipped,
            skip_reasons=_skip_reasons,
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            fallback_hits=_fallback_hits,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        REPO_ROOT / "krr_outputs" / "reports" / "kov"
        / "classify_deontic_coverage.json",
    )
    return 0
```

For `_triples`: deontic writes `estleg:normativeType` (object value
`{"@id": norm_iri}`) on each classified provision node — see the
script's existing line 198: `node["estleg:normativeType"] = {"@id": norm_iri}`.
Increment `_triples` each time that assignment runs successfully.
Optionally also count the `estleg:dutyHolder` assignment (line 200)
as a separate triple — both are emitted per classified provision.

For pipelines that don't have an obvious "fallback" or "unresolved"
concept (e.g., classify_deontic), leave those counters at 0. They're
required by the shape but their value is just informational.

`REPO_ROOT` is defined in the script (most scripts compute it as
`Path(__file__).resolve().parents[1]`). If a script is missing it,
add it.

**The remaining four pipeline-fix tasks (Tasks 7–10) say "same pattern
as Task 6 Step 4". Apply this full scaffold each time, including all
six counters.** Pipeline-specific notes:

- `classify_eurovoc`: `_triples` = count of EuroVoc subject IRIs added
  to act nodes. `_fallback_hits` = 0. `_unresolved` = 0.
- `extract_temporal_data`: `_triples` = count of date triples emitted.
  `_fallback_hits` = count of times the act-level date fallback fires
  (when XML date fields are missing, helper falls back to peep file's
  `entryIntoForce`). `_unresolved` = count of peep files whose XML
  could not be paired (`pair_peep_with_xml` returned None).
- `extract_legal_concepts`: `_triples` = count of concept-defines /
  concept-definedIn triples. `_unresolved` = count of concepts whose
  par_nr isn't in the peep file's par_to_iri lookup (so the slug
  fallback fired). `_fallback_hits` = count of times the slug-based
  XML resolution fallback ran.
- `generate_amendment_history`: `_triples` = count of amendedBy
  triples emitted. `_unresolved` = same as temporal (XML pairing
  failures). `_fallback_hits` = count of slug-based pairings that
  worked when globalId pairing didn't.

- [ ] **Step 5: Verify the script still runs end-to-end on a small fixture**

```bash
# Smoke: run the script in a tmp dir with just the fixture
mkdir -p /tmp/deontic_smoke/krr_outputs/regulations/kov/x_vallavolikogu
cp tests/fixtures/kov_layer2a/sample_kov_act.json /tmp/deontic_smoke/krr_outputs/regulations/kov/x_vallavolikogu/
# (Run the actual pipeline if straightforward; otherwise skip — the
# real corpus run in Task 11 will exercise it.)
```

Skip — the real validation is the corpus run in Task 11. Per-pipeline
smoke tests like this are uneven across the 5 pipelines (some are
trivial to set up, some need an XML fixture), so the plan delegates
the integration-level check to Task 11 uniformly.

- [ ] **Step 6: Run the full test suite**

```
pytest -q
```

Expected: prior count + 2 new deontic tests, all passing.

- [ ] **Step 7: Commit**

```bash
git add scripts/classify_deontic.py tests/test_classify_deontic.py tests/fixtures/kov_layer2a/sample_kov_act.json
git commit -m "Add coverage instrumentation to classify_deontic; verify KOV input"
```

---

## Task 7: Fix `classify_eurovoc` — switch from INDEX.json to iter_peep_files

**Files:**
- Modify: `scripts/classify_eurovoc.py:432-433, 441-512`
- Create: `tests/test_classify_eurovoc.py`

The current classifier (a) drives input from `krr_outputs/INDEX.json` (laws-only), and (b) at line ~433 explicitly skips files where `peep_file.parent != KRR_DIR`. After this task, EuroVoc gets discovery from `iter_peep_files()` and pulls per-file titles from each peep file's act node.

- [ ] **Step 1: Locate the relevant lines**

Run:

```
grep -n "INDEX.json\|peep_file.parent\|KRR_DIR / law_file\|KRR_DIR / filename" scripts/classify_eurovoc.py
```

You should see hits around lines 433, 441, 467, 512, 553. Read those sections to understand the current flow before editing.

- [ ] **Step 2: Write the failing test**

Create `tests/test_classify_eurovoc.py`:

```python
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
```

- [ ] **Step 3: Run the test (expect ImportError)**

Run: `pytest tests/test_classify_eurovoc.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement `read_act_metadata_from_peep`**

Add to `scripts/classify_eurovoc.py` (place near the top of the module-level functions, before `main()`):

```python
def read_act_metadata_from_peep(path: Path) -> dict | None:
    """Pull act-level metadata from a peep file without consulting
    INDEX.json. Returns dict with @id, title, source — or None if no
    act-typed node is present.

    Recognises any node typed as one of the concrete legal-act
    classes. Importantly, ``owl:Ontology`` ALONE is NOT enough: the
    Layer 1 KOV registry files (``municipalities_peep.json``,
    ``issuers_kov_peep.json``) use ``owl:Ontology`` on their top
    metadata node, but they are not legal acts and must not get
    EuroVoc tags. The node must also carry one of the concrete act
    types.
    """
    ACT_TYPES = {
        "estleg:Act", "estleg:Law",
        "estleg:NationalRegulation", "estleg:GovernmentRegulation",
        "estleg:MinisterialRegulation", "estleg:MunicipalRegulation",
    }
    # Registry files identified by stable IRI prefixes — even if Layer 2
    # later stamps them with new types, these map files remain
    # ineligible for EuroVoc.
    REGISTRY_ID_PREFIXES = (
        "estleg:Municipalities_",
        "estleg:Issuers_",
        "estleg:LegalConcepts_",
        "estleg:Citations_",
        "estleg:Similarity_",
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    for node in doc.get("@graph", []):
        node_id = node.get("@id", "")
        if any(node_id.startswith(p) for p in REGISTRY_ID_PREFIXES):
            return None  # registry map — not classifiable
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if not ACT_TYPES.intersection(types):
            continue
        return {
            "@id": node_id,
            "title": node.get("rdfs:label", ""),
            "source": node.get("dc:source", node.get("rdfs:label", "")),
        }
    return None
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_classify_eurovoc.py -v`
Expected: 4 passed (act metadata + provision-only rejection +
municipality registry rejection + issuer registry rejection).

- [ ] **Step 6: Replace the discovery + write-back loop in `main()`**

The existing flow is roughly:
1. Load INDEX.json → list of law slugs
2. For each slug, find the peep file at `KRR_DIR / files[0]`
3. Skip non-root files
4. Load the file with `load_json(filepath)` and call `extract_text_from_law(data)` to gather classification text from labels, sourceAct, summaries, definedTerm, etc.
5. Run `classify_text(text)`
6. Write back via `update_law_file_eurovoc(filepath, domains)`

The fix changes only **discovery and write-back paths**, NOT the
classification text source. `extract_text_from_law(doc)` already
produces a richer text bag than just title/source — keep using it,
otherwise classification under-fires on KOV files.

Replace with:
1. Discover via `iter_peep_files()` (gets KOV via the new default).
2. For each path, call `read_act_metadata_from_peep(path)` for path/title
   bookkeeping (skip files that return None). Then load the file fully
   and pass to the existing `extract_text_from_law(data)`.
3. Classify with `classify_text(text)` (unchanged).
4. Write back via `update_law_file_eurovoc(meta["path"], domains)` —
   pass the source path, not `KRR_DIR / law_file`.

**At the discovery block (around line 432-475):**

Find:

```python
    print("\n--- Loading INDEX.json ---")
    index_path = KRR_DIR / "INDEX.json"
    # ... INDEX-loading logic ...
```

Replace with:

```python
    print("\n--- Discovering peep files ---")
    peep_files = iter_peep_files()
    print(f"  Discovered {len(peep_files)} peep files")
    laws_meta = []
    for f in peep_files:
        meta = read_act_metadata_from_peep(f)
        if meta is None:
            continue
        meta["path"] = f
        laws_meta.append(meta)
    print(f"  {len(laws_meta)} have a recognized act node")
```

**At the per-file classification loop (around line 467):**

The current loop probably looks like (reading existing code):

```python
    primary_file = KRR_DIR / files[0]
    # ... checks ...
    data = load_json(primary_file)
    text = extract_text_from_law(data)
    domains = classify_text(text)
    update_law_file_eurovoc(primary_file, domains)
```

Replace with:

```python
    for meta in laws_meta:
        data = load_json(meta["path"])
        if data is None:
            continue
        text = extract_text_from_law(data)
        domains = classify_text(text)
        if domains:
            update_law_file_eurovoc(meta["path"], domains)
```

Crucially: keep `extract_text_from_law(data)` — it walks the @graph and
gathers `rdfs:label`, `dc:source`, summaries, defined terms, and other
text so classification has enough signal. The new
`read_act_metadata_from_peep` is only for path/identity bookkeeping.

- [ ] **Step 7: Remove the root-only filter**

Find the line:

```python
        if peep_file.parent != KRR_DIR:
            continue
```

Delete it. The pinned 9 pipelines opt out of KOV via `include_kov=False`; this filter was the root-only fallback that's now obsolete.

- [ ] **Step 8: Add coverage instrumentation**

Same pattern as Task 6 Step 4 — wrap `main()` with timing + counting, write to
`krr_outputs/reports/kov/classify_eurovoc_coverage.json`.

- [ ] **Step 9: Run the full test suite**

```
pytest -q
```

Expected: prior count + 4 new EuroVoc tests, all passing.

- [ ] **Step 10: Commit**

```bash
git add scripts/classify_eurovoc.py tests/test_classify_eurovoc.py
git commit -m "Switch classify_eurovoc to iter_peep_files; remove INDEX-only discovery"
```

---

## Task 8: Fix `extract_temporal_data` — recursive XML + globaalID pairing

**Files:**
- Modify: `scripts/extract_temporal_data.py:279, 312`
- Create: `tests/test_extract_temporal_data.py`
- Create: `tests/fixtures/kov_layer2a/sample_kov_xml.xml` — minimal Riigi Teataja XML for date extraction

The two issues from the spec:
1. `data/riigiteataja/*.xml` is non-recursive (skips `maarus/`, `maarus_kov/`).
2. Pairs XML to peep files by **slug**, but KOV peep files are title-based and don't share slugs with their `reg_<globalId>.xml` counterparts.

The fix: recursive glob + a `globaalID → XML path` lookup helper that uses each peep file's `estleg:globalId` value to find its XML.

- [ ] **Step 1: Create the XML fixture**

Create `tests/fixtures/kov_layer2a/sample_kov_xml.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<akt globaalID="999999999">
  <metaandmed>
    <vastuvotjaLiik>Tallinna Linnavolikogu</vastuvotjaLiik>
    <vastuvoetud>
      <aktikuupaev>2020-09-17</aktikuupaev>
      <joustumine>2020-10-02</joustumine>
    </vastuvoetud>
  </metaandmed>
</akt>
```

The `<vastuvoetud>` block with `<aktikuupaev>` (adoption date) and
`<joustumine>` (entry into force) matches the real shape that
`extract_temporal_from_xml` reads at lines 133-145 of
`scripts/extract_temporal_data.py`. Earlier draft fixture used
`<vastuvotmiseAeg>` and `<jouAeg>` — those are NOT what the parser
reads; it would silently extract no dates.

- [ ] **Step 2: Write the failing test**

Create `tests/test_extract_temporal_data.py`:

```python
"""Tests for extract_temporal_data — recursive XML discovery + globaalID pairing."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kov_layer2a"
PEEP_FIXTURE = FIXTURE_DIR / "sample_kov_act.json"
XML_FIXTURE = FIXTURE_DIR / "sample_kov_xml.xml"


class TestBuildGlobalIdLookup:
    def test_recursive_xml_discovery(self, tmp_path):
        from extract_temporal_data import build_globalid_xml_lookup

        # Stage three XML files in subdirectories to mimic the real layout
        rt = tmp_path / "data" / "riigiteataja"
        (rt / "maarus").mkdir(parents=True)
        (rt / "maarus_kov").mkdir(parents=True)
        (rt / "law_alpha.xml").write_text(
            '<akt globaalID="111"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )
        (rt / "maarus" / "reg_222.xml").write_text(
            '<akt globaalID="222"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )
        (rt / "maarus_kov" / "reg_333.xml").write_text(
            '<akt globaalID="333"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = build_globalid_xml_lookup(rt)
        assert lookup["111"].name == "law_alpha.xml"
        assert lookup["222"].name == "reg_222.xml"
        assert lookup["333"].name == "reg_333.xml"

    def test_kov_xml_in_maarus_kov_subdir(self, tmp_path):
        """KOV XML lives at data/riigiteataja/maarus_kov/reg_<gid>.xml;
        the recursive walk must find it."""
        from extract_temporal_data import build_globalid_xml_lookup
        rt = tmp_path / "data" / "riigiteataja"
        (rt / "maarus_kov").mkdir(parents=True)
        # Use the kov fixture's globalId
        (rt / "maarus_kov" / "reg_999999999.xml").write_text(
            '<akt globaalID="999999999"><metaandmed>'
            '<vastuvotjaLiik>Tallinna Linnavolikogu</vastuvotjaLiik>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )
        lookup = build_globalid_xml_lookup(rt)
        assert "999999999" in lookup
        assert "maarus_kov" in str(lookup["999999999"])


class TestPairPeepWithXml:
    def test_pairs_via_globalid_attribute(self, tmp_path):
        from extract_temporal_data import pair_peep_with_xml

        peep = tmp_path / "act.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [
                {"@id": "estleg:Reg_9999_Map_2026",
                 "estleg:globalId": "999",
                 "@type": ["estleg:MunicipalRegulation"]}
            ],
        }), encoding="utf-8")

        xml = tmp_path / "reg_999.xml"
        xml.write_text(
            '<akt globaalID="999"><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        lookup = {"999": xml}
        result = pair_peep_with_xml(peep, lookup)
        assert result == xml

    def test_unpaired_returns_none(self, tmp_path):
        from extract_temporal_data import pair_peep_with_xml
        peep = tmp_path / "act.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [
                {"@id": "estleg:Reg_X",
                 "estleg:globalId": "missing-id",
                 "@type": ["estleg:Law"]}
            ],
        }), encoding="utf-8")
        assert pair_peep_with_xml(peep, lookup={}) is None

    def test_slug_fallback_for_law_without_globalid(self, tmp_path):
        """The 615 indexed laws predate the estleg:globalId field — their
        act nodes carry estleg:Law but no globalId. Without a slug
        fallback, the new pairing would silently drop XML for every
        law, regressing existing temporal/amendment-history coverage.
        """
        from extract_temporal_data import pair_peep_with_xml

        # Stage a law peep file with NO estleg:globalId
        rt = tmp_path / "data" / "riigiteataja"
        rt.mkdir(parents=True)
        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        peep = krr / "alkoholiseadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AlkS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Alkoholiseadus"}
                # NO estleg:globalId — matches real law peep shape
            ],
        }), encoding="utf-8")
        # Slug-named XML at the data root (laws are at the top level,
        # NOT under maarus/ or maarus_kov/)
        (rt / "alkoholiseadus.xml").write_text(
            '<akt><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        # globalId path returns None (no globalId on the act);
        # slug path resolves alkoholiseadus_peep -> alkoholiseadus.xml.
        result = pair_peep_with_xml(peep, lookup={}, data_dir=rt)
        assert result is not None
        assert result.name == "alkoholiseadus.xml"

    def test_slug_fallback_handles_osa_suffix(self, tmp_path):
        """Multi-part laws like asjaoigusseadus_osa1 share their
        XML with the base slug (asjaoigusseadus.xml). The slug
        fallback strips _osaN before re-resolving."""
        from extract_temporal_data import pair_peep_with_xml

        rt = tmp_path / "data" / "riigiteataja"
        rt.mkdir(parents=True)
        krr = tmp_path / "krr_outputs"
        krr.mkdir()

        peep = krr / "asjaoigusseadus_osa3_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:AOS_Osa3_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Law"]}
            ],
        }), encoding="utf-8")
        # XML lives at base-slug name only (no per-osa XML)
        (rt / "asjaoigusseadus.xml").write_text(
            '<akt><metaandmed></metaandmed></akt>',
            encoding="utf-8",
        )

        result = pair_peep_with_xml(peep, lookup={}, data_dir=rt)
        assert result is not None
        assert result.name == "asjaoigusseadus.xml"


class TestMainUsesGlobalIdLookup:
    """Pin the contract that main() actually wires the new helpers.
    The helper unit tests above pass even if main() still uses the old
    slug-based pairing (the helpers exist and work in isolation but the
    pipeline doesn't call them). This test runs main() with monkeypatched
    paths over a tiny KOV-like fixture and asserts that an XML file under
    maarus_kov/ paired by globalId actually contributed temporal data
    to the corresponding peep file.
    """

    def test_main_pairs_kov_via_globalid(self, tmp_path, monkeypatch):
        import extract_temporal_data as mod
        import estleg_common

        # Stage a tiny tree
        krr = tmp_path / "krr_outputs"
        rt = tmp_path / "data" / "riigiteataja"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (rt / "maarus_kov").mkdir(parents=True)

        # Peep file with globalId 999 — slug-based pairing would fail
        # (filename doesn't share a slug with the XML)
        peep = kov / "test_act_peep.json"
        peep.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                         "owl": "http://www.w3.org/2002/07/owl#"},
            "@graph": [
                {"@id": "estleg:Reg_X_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "rdfs:label": "Test KOV act",
                 "estleg:globalId": "999",
                 "estleg:enactedBy": {
                     "@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_EHAK_0784"},
                 "estleg:titleNormalized": "test"}
            ],
        }), encoding="utf-8")

        # XML uses the actual tags the extractor reads:
        # vastuvoetud/aktikuupaev (adoption) and vastuvoetud/joustumine
        # (entry into force). See scripts/extract_temporal_data.py
        # lines 133-145 for the field reader.
        (rt / "maarus_kov" / "reg_999.xml").write_text(
            '<akt globaalID="999"><metaandmed>'
            '<vastuvoetud>'
            '<aktikuupaev>2023-12-15</aktikuupaev>'
            '<joustumine>2024-01-01</joustumine>'
            '</vastuvoetud>'
            '</metaandmed></akt>',
            encoding="utf-8",
        )

        # Critical: iter_peep_files() is imported from estleg_common,
        # whose own KRR_DIR was bound at import time. Patch BOTH the
        # extractor module's KRR_DIR/DATA_DIR AND estleg_common.KRR_DIR
        # — otherwise the iterator will scan the real repo regardless
        # of what extract_temporal_data thinks KRR_DIR is.
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "DATA_DIR", rt)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        if hasattr(mod, "REPO_ROOT"):
            monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

        # Run main() — should write back temporal triples to the peep
        # file based on the paired XML.
        rc = mod.main()
        assert rc in (None, 0)

        # Re-read the peep file and verify a temporal triple was added
        # from the paired XML. The script writes estleg:entryIntoForce
        # (and possibly estleg:adoptionDate / estleg:repealDate /
        # estleg:lastAmendmentDate). Any of these is acceptable
        # evidence that pairing worked.
        with open(peep, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        act = next(n for n in doc["@graph"]
                   if "estleg:MunicipalRegulation" in (n.get("@type") or []))
        date_keys = {"estleg:entryIntoForce", "estleg:adoptionDate",
                     "estleg:repealDate", "estleg:lastAmendmentDate",
                     "estleg:publicationDate"}
        present = date_keys.intersection(act.keys())
        assert present, (
            f"main() ran but no date triples appeared on the act node "
            f"after pairing — pair_peep_with_xml may not be wired into "
            f"main(). Act keys: {list(act.keys())}"
        )
```

- [ ] **Step 3: Run the test (expect ImportError)**

Run: `pytest tests/test_extract_temporal_data.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement the two helpers**

Add to `scripts/extract_temporal_data.py` (near the top, before `main()`):

```python
import xml.etree.ElementTree as _ET


def build_globalid_xml_lookup(rt_root: Path) -> dict[str, Path]:
    """Recursively scan rt_root for *.xml files and build a
    globalId → Path lookup keyed by the root element's globaalID
    attribute. Files without a recognisable globaalID are skipped.

    Walks rt_root and all subdirectories so KOV XML under
    ``maarus_kov/`` and state regulation XML under ``maarus/`` are
    paired alongside law XML at the root.
    """
    lookup: dict[str, Path] = {}
    if not rt_root.is_dir():
        return lookup
    for xml_path in rt_root.rglob("*.xml"):
        try:
            tree = _ET.parse(str(xml_path))
        except _ET.ParseError:
            continue
        root = tree.getroot()
        # Primary: root element's globaalID attribute (most RT XML).
        gid = root.get("globaalID")
        if gid is None:
            # Fallback: scan top-level <metaandmed> children for a
            # globaalID leaf element.
            #
            # NOTE: ElementTree's truthiness on Element is False when
            # the element has no children, so `child.find(a) or
            # child.find(b)` would silently discard a leaf hit. Use
            # explicit `is None` checks throughout.
            for child in root:
                if not child.tag.endswith("metaandmed"):
                    continue
                gid_el = child.find("globaalID")
                if gid_el is None:
                    gid_el = child.find("{*}globaalID")
                if gid_el is not None and gid_el.text:
                    gid = gid_el.text.strip()
                    break
        if gid:
            lookup[str(gid)] = xml_path
    return lookup


def pair_peep_with_xml(
    peep_path: Path,
    lookup: dict[str, Path],
    *,
    data_dir: Path | None = None,
) -> Path | None:
    """Pair a peep file to its XML.

    Two paths in priority order:

    1. **globalId-based** (the new path, KOV + state regs): the
       generators stamp ``estleg:globalId`` on every act node, and KOV
       XML filenames embed the same id (``reg_<globalId>.xml``). This
       is the canonical pairing for everything generated post-Phase-1.

    2. **Slug-based fallback** (legacy laws): the 615 indexed laws
       were generated before the ``estleg:globalId`` field existed,
       so their act nodes carry ``estleg:Law``/``owl:Ontology`` types
       but no globalId. For these, fall back to matching the peep
       file's stem (sans ``_peep``) against
       ``<data_dir>/<stem>.xml``. ``data_dir`` is required for the
       fallback; if not supplied, only the globalId path runs.

    Returns the XML path or None if neither path resolves.
    """
    try:
        with open(peep_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    gid = None
    for node in doc.get("@graph", []):
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if not any(t.endswith("Regulation") or t == "estleg:Law"
                   or t == "owl:Ontology" for t in types):
            continue
        gid = node.get("estleg:globalId")
        if gid:
            break
    # Path 1: globalId
    if gid:
        xml = lookup.get(str(gid))
        if xml is not None:
            return xml
    # Path 2: slug fallback (laws without globalId)
    if data_dir is not None:
        slug = peep_path.stem.replace("_peep", "")
        xml = data_dir / f"{slug}.xml"
        if xml.exists():
            return xml
        # Stripped-_osa fallback for multi-part laws
        import re as _re
        base_slug = _re.sub(r"_osa\d+$", "", slug)
        xml = data_dir / f"{base_slug}.xml"
        if xml.exists():
            return xml
    return None
```

- [ ] **Step 5: Rewire `main()` — concrete block replacement**

The current main() (lines 272-380) does this in sequence:
1. Globs `DATA_DIR/*.xml` (laws only) → `xml_files`
2. Loops `xml_files` → `temporal_by_slug[xml_path.stem] = temporal`
3. Loops `law_files` (root-only) → for each peep, looks up
   `temporal_by_slug.get(xml_slug)` and writes back triples

The fix replaces (1) and (2) with a globalId XML index, leaves the
intermediate `temporal_by_slug` dict alone (it stays slug-keyed —
the keys come from peep files in step 3), and switches step 3's
lookup to use `pair_peep_with_xml` per peep.

**Concrete edit at lines 277-301** (step 1+2 in the existing main).
Replace:

```python
    # Step 1: Find all XML files
    print("\n[1/4] Scanning XML files...")
    xml_files = sorted(DATA_DIR.glob("*.xml")) if DATA_DIR.exists() else []
    print(f"  Found {len(xml_files)} XML files in {DATA_DIR.relative_to(REPO_ROOT)}")

    # Step 2: Extract temporal data from each XML
    print("\n[2/4] Extracting temporal metadata from XML...")
    temporal_by_slug: dict[str, dict] = {}
    extracted = 0
    parse_errors = 0

    for xml_path in xml_files:
        slug = xml_path.stem
        try:
            temporal = extract_temporal_from_xml(xml_path)
            has_data = any(v is not None for v in temporal.values())
            if has_data:
                extracted += 1
            temporal_by_slug[slug] = temporal
        except Exception as e:
            parse_errors += 1
            print(f"  ERROR parsing {xml_path.name}: {e}")

    print(f"  Extracted temporal data from {extracted} files ({parse_errors} errors)")
```

with:

```python
    # Step 1: Build the globalId → XML path index (recursive — covers
    # laws at the root, state regs under maarus/, and KOV under
    # maarus_kov/).
    print("\n[1/4] Indexing XML files by globalId...")
    xml_lookup = build_globalid_xml_lookup(DATA_DIR)
    print(f"  Indexed {len(xml_lookup)} XML files")

    # Step 2: Per-peep XML pairing + temporal extraction. We pair each
    # peep file individually rather than pre-extracting all XML to a
    # dict, so the slug fallback for laws-without-globalId fires per
    # file. Result is still keyed by peep filename stem (without the
    # _peep suffix) so step 4's enrichment logic doesn't change.
    print("\n[2/4] Extracting temporal metadata per peep file...")
    temporal_by_slug: dict[str, dict] = {}
    extracted = 0
    parse_errors = 0

    for peep in iter_peep_files():
        xml_path = pair_peep_with_xml(peep, xml_lookup, data_dir=DATA_DIR)
        if xml_path is None:
            continue
        slug = peep.stem.replace("_peep", "")
        try:
            temporal = extract_temporal_from_xml(xml_path)
            has_data = any(v is not None for v in temporal.values())
            if has_data:
                extracted += 1
            temporal_by_slug[slug] = temporal
        except Exception as e:
            parse_errors += 1
            print(f"  ERROR parsing {xml_path.name}: {e}")

    print(f"  Extracted temporal data from {extracted} files ({parse_errors} errors)")
```

**Concrete edit at line 312** — remove the root-only filter:

```python
    law_files = [f for f in law_files if f.parent == KRR_DIR]
```

Delete that line. After the iter_peep_files default flip and the
globalId-aware pairing, the loop should walk all files.

**Concrete edit at line 361** — the per-peep lookup. The existing
code has:

```python
        xml_slug = ...  # derived from peep slug
        temporal = temporal_by_slug.get(xml_slug) or temporal_by_slug.get(base_slug)
```

Read 3 lines of context around line 361. The `xml_slug` and
`base_slug` come from the peep's filename. Since `temporal_by_slug`
is now keyed by `peep.stem.replace("_peep", "")` (the same key these
two compute), the lookup may already work without changes. If
`xml_slug` was previously derived as `xml_path.stem` (the XML
filename), it now needs to use the peep stem instead — change to:

```python
        slug = law_file.stem.replace("_peep", "")
        temporal = temporal_by_slug.get(slug)
```

Confirm by re-reading the surrounding 20 lines — this is one of the
spots where the existing code's exact shape decides the cleanest
patch.

- [ ] **Step 6: Add coverage instrumentation**

Same pattern as Task 6 Step 4 — write to `krr_outputs/reports/kov/extract_temporal_data_coverage.json`.

- [ ] **Step 7: Run the test to verify it passes**

Run: `pytest tests/test_extract_temporal_data.py -v`
Expected: 7 passed (2 lookup + 4 pairing including slug fallback + 1 main()-integration).

```
pytest -q
```

Expected: prior count + 7 new temporal tests, all passing.

- [ ] **Step 8: Commit**

```bash
git add scripts/extract_temporal_data.py tests/test_extract_temporal_data.py tests/fixtures/kov_layer2a/sample_kov_xml.xml
git commit -m "Recursive XML discovery + globaalID pairing for extract_temporal_data"
```

---

## Task 9: Fix `extract_legal_concepts` — XML→peep IRI bridging + KOV XML discovery

**Files:**
- Modify: `scripts/extract_legal_concepts.py:196-247, 254, 297-310`
- Create: `tests/test_extract_legal_concepts.py`

There are **three** coupled problems:

1. **Peep discovery filter:** root-only at line ~254.
2. **XML discovery is slug-based:** main() at line ~297-300 looks for XML at
   `DATA_DIR / f"{slug}.xml"` (and a `_osa`-stripped fallback). Real KOV
   XML lives at `data/riigiteataja/maarus_kov/reg_<globalId>.xml` — there
   is no slug match. Without the same `globaalID` bridge that Task 8
   built for temporal, every KOV peep file would be discovered and then
   skipped for missing XML.
3. **Provision IRI sourcing:** `extract_concepts_from_xml` (line 196)
   extracts concept definitions from XML and constructs provision IRIs
   as `f"estleg:{slug}_Par_{par_nr}"`. The IRI is built from the
   **filename slug**, but for KOV files the actual provision @id in the
   JSON-LD is `estleg:Reg_<terviktekstId>_Par_<n>`. We bridge XML to
   peep by passing a `(par_nr, par_display) → @id` lookup into the
   XML extractor.

The fix reuses the `build_globalid_xml_lookup` + `pair_peep_with_xml`
helpers from Task 8 for problem #2, plus a new `build_par_to_iri_lookup`
for problem #3.

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract_legal_concepts.py`:

```python
"""Tests for extract_legal_concepts — IRI sourcing from actual @id."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


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
```

- [ ] **Step 2: Run the test (expect ImportError)**

Run: `pytest tests/test_extract_legal_concepts.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `build_par_to_iri_lookup`**

Add to `scripts/extract_legal_concepts.py`:

```python
def build_par_to_iri_lookup(doc: dict) -> dict[str, str]:
    """Build a (par_nr | par_display) → provision @id lookup from a
    loaded peep file's @graph.

    Used to bridge XML-extracted concepts (which know paragraphs by
    their numeric par_nr or display form like "§ 1.") to the
    JSON-LD provision IRIs (which use Reg_<terviktekstId>_Par_<n>
    for KOV files — the actual @id is decoupled from the filename
    slug).

    The lookup keys both forms so the XML extractor can use whichever
    field it has: paragrahvNr ("1") OR kuvatavNr ("§ 1."). If a
    peep file has multiple provisions with the same par_nr (rare
    but possible across different chapters), later entries win — for
    Layer 2a's purposes that's acceptable.
    """
    lookup: dict[str, str] = {}
    for node in doc.get("@graph", []):
        par_display = node.get("estleg:paragrahv")
        if not par_display:
            continue
        node_id = node.get("@id")
        if not node_id:
            continue
        # Display form: "§ 1." → also key under "1"
        lookup[par_display] = node_id
        # Strip "§ " prefix and trailing "." to get the bare number
        bare = par_display.lstrip("§").strip().rstrip(".")
        if bare:
            lookup[bare] = node_id
    return lookup
```

- [ ] **Step 4: Update `extract_concepts_from_xml` to accept the lookup**

Find the function signature at line ~196:

```python
def extract_concepts_from_xml(xml_path: Path, law_title: str, law_slug: str) -> list[dict]:
```

Change to:

```python
def extract_concepts_from_xml(
    xml_path: Path,
    law_title: str,
    law_slug: str,
    par_to_iri: dict[str, str] | None = None,
) -> list[dict]:
```

Find (around line 232):

```python
            provision_id = f"estleg:{sanitize_id(law_slug)}_Par_{sanitize_id(par_nr)}"
```

Replace with:

```python
            # Bridge XML par_nr to peep @id when a lookup is provided.
            # Falls back to the slug-derived form so existing law-only
            # callers still work — Layer 2a callers should always pass
            # par_to_iri.
            provision_id = None
            if par_to_iri is not None:
                provision_id = par_to_iri.get(par_nr) or par_to_iri.get(par_display)
            if provision_id is None:
                provision_id = f"estleg:{sanitize_id(law_slug)}_Par_{sanitize_id(par_nr)}"
```

- [ ] **Step 5: Wire the globaalID XML lookup AND the par-to-iri lookup into `main()`**

Find `main()` (around line 278). The existing flow:

```python
    for i, law in enumerate(laws):
        slug = law["slug"]
        title = law["title"]
        base_slug = re.sub(r"_osa\d+$", "", slug)
        xml_path = DATA_DIR / f"{slug}.xml"
        if not xml_path.exists():
            xml_path = DATA_DIR / f"{base_slug}.xml"
        if not xml_path.exists():
            continue
        concepts = extract_concepts_from_xml(xml_path, title, slug)
        # ... rest of existing loop ...
```

Two changes:

**(a) Replace the slug-based `xml_path` resolution with the globaalID bridge.**
Add at the top of `main()` (right after loading the laws list):

```python
    from extract_temporal_data import (
        build_globalid_xml_lookup,
        pair_peep_with_xml,
    )
    xml_lookup = build_globalid_xml_lookup(DATA_DIR)
    print(f"  Indexed {len(xml_lookup)} XML files by globalId")
```

Then inside the per-law loop, replace the `xml_path = DATA_DIR / f"{slug}.xml"` block with:

```python
        # pair_peep_with_xml does both globalId (KOV/state-regs) and
        # slug fallback (laws without globalId) when data_dir is passed.
        xml_path = pair_peep_with_xml(
            law["path"], xml_lookup, data_dir=DATA_DIR
        )
        if xml_path is None:
            continue
```

**(b) Build the par-to-iri lookup and pass it through.**
Just before the `extract_concepts_from_xml(...)` call:

```python
        try:
            with open(law["path"], "r", encoding="utf-8") as fh:
                peep_doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            peep_doc = {}
        par_to_iri = build_par_to_iri_lookup(peep_doc)
        concepts = extract_concepts_from_xml(
            xml_path, title, slug, par_to_iri=par_to_iri
        )
        # ... rest of existing loop ...
```

- [ ] **Step 6: Remove the root-only filter**

Find (line ~254):

```python
        if f.parent != KRR_DIR:
            continue
```

Delete it.

- [ ] **Step 7: Update output paths**

Grep the script for `KRR_DIR / filename` and `KRR_DIR / law_file`
patterns:

```bash
grep -nE "KRR_DIR / (filename|law_file|f\")" scripts/extract_legal_concepts.py
```

For each match, replace the path with the source path captured in
the discovery loop (`law["path"]` in the new flow). Re-grep to
confirm zero matches remain. The pattern is identical to Task 7
Step 6 for EuroVoc — the same anti-pattern existed in both scripts
because they share a heritage from the laws-only era.

- [ ] **Step 8: Add coverage instrumentation**

Same pattern as Task 6 — write to `krr_outputs/reports/kov/extract_legal_concepts_coverage.json`.

- [ ] **Step 9: Run the test to verify it passes**

Run: `pytest tests/test_extract_legal_concepts.py -v`
Expected: 4 passed (2 lookup-builder tests + 2 extractor tests including the slug-fallback compat case).

```
pytest -q
```

Expected: prior count + 4 new legal-concepts tests, all passing.

- [ ] **Step 10: Commit**

```bash
git add scripts/extract_legal_concepts.py tests/test_extract_legal_concepts.py
git commit -m "Bridge XML par_nr to peep @id in extract_legal_concepts

KOV provisions use @id Reg_<terviktekstId>_Par_<n> which has no
relation to the filename slug. The XML extractor was building
provision_id from the slug, producing dangling concept->IRI links
on KOV files. Add build_par_to_iri_lookup helper that walks the
peep @graph and produces a (par_nr, par_display) -> @id map; thread
it through extract_concepts_from_xml so concepts emitted from XML
attach to the real peep IRIs."
```

---

## Task 10: Fix `generate_amendment_history` — recursive XML, globaalID pairing

**Files:**
- Modify: `scripts/generate_amendment_history.py:124, 377, 393`
- Create: `tests/test_generate_amendment_history.py`

This is structurally similar to Task 8 — same XML discovery + globaalID-pairing problems. The chain-building logic itself is unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_generate_amendment_history.py`:

```python
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
        # RTosa/RTaasta/RTnumber children. Each muutmismarge is
        # extracted as a separate amendment entry.
        (rt / "maarus_kov" / "reg_777.xml").write_text(
            '<akt globaalID="777"><metaandmed>'
            '<muutmismarge>'
            '<aktikuupaev>2023-06-15</aktikuupaev>'
            '<joustumine>2024-01-01</joustumine>'
            '<avaldamismarge>'
            '<RTosa>IV</RTosa><RTaasta>2024</RTaasta><RTnumber>1</RTnumber>'
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
```

- [ ] **Step 2: Run test (expect ImportError)**

Run: `pytest tests/test_generate_amendment_history.py -v`
Expected: FAIL.

- [ ] **Step 3: Reuse the helpers from `extract_temporal_data`**

In `scripts/generate_amendment_history.py`, near the top, add:

```python
from extract_temporal_data import build_globalid_xml_lookup, pair_peep_with_xml
```

There's no circular-import risk: `extract_temporal_data` doesn't
import anything from `generate_amendment_history`. If a future change
introduces one, extract both helpers into a new
`scripts/riigiteataja_xml_lookup.py` module — but don't preemptively
do that here.

- [ ] **Step 4: Replace the existing XML discovery and pairing logic**

The current main() (around line 392) builds two parallel
slug-keyed dicts:

```python
    xml_files = sorted(DATA_DIR.glob("*.xml")) if DATA_DIR.exists() else []
    amendments_by_slug: dict[str, list[dict]] = {}
    rt_refs_by_slug: dict[str, list[str]] = {}
    total_amendments = 0

    for xml_path in xml_files:
        slug = xml_path.stem
        amendments = extract_amendments_from_xml(xml_path)
        if amendments:
            amendments_by_slug[slug] = amendments
            total_amendments += len(amendments)
        rt_refs = extract_rt_references_from_text(xml_path)
        if rt_refs:
            rt_refs_by_slug[slug] = rt_refs
```

The chain-building step (further down in main()) iterates the
laws dict (`for slug, info in sorted(laws.items())`) and looks up
`amendments_by_slug.get(slug, [])`. So the slug is the join key
through the whole pipeline — but for KOV files, the slug doesn't
match any XML filename.

Replace the discovery+pairing block with **two** dicts keyed by
globalId, plus a per-law lookup using the act's own globalId:

```python
    xml_lookup = build_globalid_xml_lookup(DATA_DIR)
    print(f"  Indexed {len(xml_lookup)} XML files by globalId")

    amendments_by_slug: dict[str, list[dict]] = {}
    rt_refs_by_slug: dict[str, list[str]] = {}
    total_amendments = 0
    paired_count = 0
    unpaired_count = 0

    for slug, info in laws.items():
        # info["path"] is the peep file path; pair via globalId, with
        # slug fallback for laws that don't carry estleg:globalId.
        xml_path = pair_peep_with_xml(
            info["path"], xml_lookup, data_dir=DATA_DIR
        )
        if xml_path is None:
            unpaired_count += 1
            continue
        paired_count += 1
        amendments = extract_amendments_from_xml(xml_path)
        if amendments:
            amendments_by_slug[slug] = amendments
            total_amendments += len(amendments)
        rt_refs = extract_rt_references_from_text(xml_path)
        if rt_refs:
            rt_refs_by_slug[slug] = rt_refs

    print(f"  Paired XML for {paired_count} of {len(laws)} laws "
          f"({unpaired_count} unpaired — see coverage report).")
```

The downstream chain-building step (around lines 420–500) is
**unchanged** — it still iterates `laws.items()` and looks up
`amendments_by_slug[slug]`. By keying the new dicts by `slug` (not
globalId), the chain-building path is preserved and only the
discovery+pairing path is fixed.

This means `load_law_files` (line 117) must also be updated so it
doesn't filter root-only — see Step 5 below.

- [ ] **Step 5: Remove the two root-only peep filters**

Find:

```python
    for f in iter_peep_files():
        if f.parent != KRR_DIR:
            continue
        slug = f.stem.replace("_peep", "")
```

at line ~123 inside `load_law_files()`. Delete the `if ... continue` line.

Find the second occurrence at line ~377 inside the "Step 0: Clear
existing amendedBy" block:

```python
    for peep_file in iter_peep_files():
        if peep_file.parent != KRR_DIR:
            continue
```

Delete the `if ... continue` line.

Important: with both filters removed, `load_law_files` will now key
KOV peep files by their full filename stem (e.g.,
`alkohoolse_joogi_jaemuugi_kitsendused_t1014955`) — that becomes the
slug for the chain-building dicts. That's fine; the slug just needs
to be consistent across `laws.items()` and `amendments_by_slug`,
which it is by construction (both derive from the same iter).

- [ ] **Step 6: Add coverage instrumentation**

Same pattern as Task 6 — write to `krr_outputs/reports/kov/generate_amendment_history_coverage.json`.

- [ ] **Step 7: Run the test to verify it passes**

Run: `pytest tests/test_generate_amendment_history.py -v`
Expected: 3 passed (helper-import + recursive-glob + main()-integration).

```
pytest -q
```

Expected: prior count + 3 new amendment-history tests, all passing.

- [ ] **Step 8: Commit**

```bash
git add scripts/generate_amendment_history.py tests/test_generate_amendment_history.py
git commit -m "Recursive XML + globaalID pairing in generate_amendment_history (reuse temporal helpers)"
```

---

## Task 11: Run all 5 fixed pipelines on the real corpus + capture coverage

**Files:**
- Generated: `krr_outputs/reports/kov/<pipeline>_coverage.json` (×5)
- Generated: in-place updates to peep files where each pipeline writes back classifications/concepts/dates/amendments

This task **runs** the pipelines, capturing baseline-vs-current metrics. No new code; this is the integration step.

- [ ] **Step 1: Run each pipeline in turn (with proper exit-code propagation)**

A naive `python script.py 2>&1 | tail -5` masks Python's exit code
because `tail` exits 0 even if Python crashed. Use `pipefail` (so the
pipeline exits non-zero if any stage fails) and check explicitly:

```bash
set -o pipefail

for pipeline in \
    classify_deontic \
    classify_eurovoc \
    extract_temporal_data \
    extract_legal_concepts \
    generate_amendment_history
do
    echo "=== running ${pipeline} ==="
    python "scripts/${pipeline}.py" > "/tmp/${pipeline}.log" 2>&1
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "FAIL: ${pipeline} exited ${rc}"
        tail -50 "/tmp/${pipeline}.log"
        # Stop on the first failure so you don't compound errors
        exit 1
    fi
    tail -5 "/tmp/${pipeline}.log"
done
```

Expected: each script exits 0 and writes a coverage JSON. A crashing
pipeline halts the loop with a non-zero exit and the last 50 lines of
its log; capture and report.

- [ ] **Step 2: Verify coverage reports are well-formed**

```bash
for p in classify_deontic classify_eurovoc extract_temporal_data extract_legal_concepts generate_amendment_history; do
  echo "=== $p ==="
  python3 -m json.tool krr_outputs/reports/kov/${p}_coverage.json | head -15
done
```

Expected: each report shows reasonable values. Compute the ground
truth from the iterator itself rather than hard-coding a threshold:

```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from estleg_common import iter_peep_files
all_files = iter_peep_files()
kov = [f for f in all_files if 'regulations/kov' in str(f)]
print(f'expected input_files_total ≈ {len(all_files)}')
print(f'expected input_files_kov ≈ {len(kov)}')
"
```

Each pipeline's `input_files_total` should match this count (within
±5 for files added/removed during the run); `input_files_kov` should
match the KOV count exactly. Layer 1's corpus settled around 15,508
total / 11,059 KOV; if your numbers are very different, investigate
before committing.

- [ ] **Step 3: Run validate_all (JSON/schema hygiene)**

```bash
set -o pipefail
python scripts/validate_all.py > /tmp/validate_all.log 2>&1 || \
    { tail -50 /tmp/validate_all.log; exit 1; }
tail -5 /tmp/validate_all.log
```

Expected: 0 errors.

- [ ] **Step 3b: Run SHACL on Layer 2a outputs**

`scripts/shacl_validate_all.py` was created in Layer 1's Task 14 and
landed on `main`. It loads the union graph (registries + indexed laws +
KOV + state regs) and runs pyshacl. The same pre-existing violations
(`requestedCluster` IRI-as-literal ~21.5k; missing `summary` ~523)
will surface — they are tracked separately and are NOT a Layer 2a
regression.

For Layer 2a we run a **targeted SHACL check** rather than the full
corpus run, because the full run takes ~7 minutes and surfaces only
pre-existing violations:

```bash
python3 - <<'PY'
import sys, glob, json
sys.path.insert(0, "scripts")
import rdflib, pyshacl

# Build a focused graph: registries + a sample of one act per type
g = rdflib.Graph()
g.parse("krr_outputs/municipalities_peep.json", format="json-ld")
g.parse("krr_outputs/issuers_kov_peep.json", format="json-ld")

# Sample: one Tallinn KOV act (post-Layer-2a enrichment) + one law +
# one state regulation. Picks a deterministic file in each category.
import os
tallinn_dir = "krr_outputs/regulations/kov/tallinna_linnavolikogu"
sample_kov = sorted(os.listdir(tallinn_dir))[0]
g.parse(f"{tallinn_dir}/{sample_kov}", format="json-ld")
g.parse("krr_outputs/alkoholiseadus_peep.json", format="json-ld")
sample_riik = sorted(glob.glob("krr_outputs/regulations/riik/*_peep.json"))[0]
g.parse(sample_riik, format="json-ld")

shapes = rdflib.Graph().parse("shacl/estonian_legal_shapes.ttl", format="turtle")
ok, _, msg = pyshacl.validate(g, shacl_graph=shapes, inference="rdfs")
# Filter to Layer-2a-relevant violation paths (Citation, issuedUnder,
# implementsCitation — none of these should fire because no instances
# exist yet).
LAYER2_PATHS = {"citationTarget", "citationDetail", "citationText",
                "issuedUnder", "implementsCitation",
                "implementedBy", "implementedByCount", "enforcedAtLevel"}
layer2a_violations = [
    line for line in (msg or "").splitlines()
    if any(p in line for p in LAYER2_PATHS)
]
print(f"SHACL_TARGETED {'PASS' if ok else 'FAIL'}")
print(f"Layer-2a-property violations: {len(layer2a_violations)}")
for v in layer2a_violations[:5]:
    print(f"  {v}")
# Exit non-zero if any Layer-2a property is in violation. Pre-existing
# violations (requestedCluster/summary) are tolerated; Layer-2a-property
# violations are not — they would mean we shipped invalid Citation /
# issuedUnder / implementsCitation / etc. data.
import sys as _sys
_sys.exit(1 if layer2a_violations else 0)
PY
```

Expected:
- `Layer-2a-property violations: 0` (no Citation instances exist yet
  in 2a; Layer 2b populates them).
- `SHACL_TARGETED FAIL` is acceptable iff every violation falls into
  the pre-existing categories noted above. If there are violations on
  `enactedBy`/`enactedByMunicipality`/`partOfAct`/`titleNormalized`/
  `Act`/`Law` (Layer 1 properties), that's a regression — escalate.

Skip the full-corpus `python scripts/shacl_validate_all.py` run for
Layer 2a; rerun it as part of Layer 2c when more semantic triples
exist.

- [ ] **Step 4: Stage and commit**

The 5 pipelines write back to multiple locations beyond the in-place
peep updates: `krr_outputs/concepts/`, `krr_outputs/amendments/`, root
JSON reports (e.g. `temporal_data_report.json`,
`amendment_history_report.json`, `eurovoc_classification.json`,
`legal_concepts.json`). The full staging set:

```bash
# Coverage reports (this PR's primary deliverable)
git add krr_outputs/reports/kov/

# In-place updates to KOV peep files (deontic, eurovoc, temporal,
# legal-concepts, amendment-history all write back per-act/per-provision
# triples)
git add krr_outputs/regulations/kov

# State regulation in-place updates (eurovoc, temporal, legal-concepts,
# amendment-history may add triples here too)
git add krr_outputs/regulations/riik

# Indexed law files (driven by INDEX.json — same chunked-staging
# pattern as Layer 1's Task 14):
python3 - <<'PY'
import json, subprocess, os
idx = json.load(open("krr_outputs/INDEX.json"))
paths = [
    f"krr_outputs/{f}"
    for entry in idx["laws"] for f in entry.get("files", [])
    if os.path.exists(f"krr_outputs/{f}")
]
chunk = 500
for i in range(0, len(paths), chunk):
    subprocess.check_call(["git", "add", "--"] + paths[i:i + chunk])
print(f"staged {len(paths)} indexed law files")
PY

# New per-pipeline output files / directories. Use --ignore-errors so
# git doesn't fail if a directory doesn't exist (e.g. concepts/ or
# amendments/ may be created only when the corresponding pipeline runs).
for path in \
    krr_outputs/concepts \
    krr_outputs/amendments \
    krr_outputs/eelnoud \
    krr_outputs/deontic_classification_report.json \
    krr_outputs/temporal_data_report.json \
    krr_outputs/amendment_history_report.json \
    krr_outputs/eurovoc_classification.json \
    krr_outputs/legal_concepts.json \
    krr_outputs/legal_concepts_report.json
do
    git add "$path" 2>/dev/null || true
done

git status --short | head -30
git commit -m "Run Layer 2a pipelines on full corpus

Generates initial KOV coverage reports for the 5 fixed pipelines, plus:
- in-place updates to KOV / state-reg / law peep files (deontic
  classifications, EuroVoc tags, temporal dates on act+provision
  nodes)
- per-pipeline output files (concepts/, amendments/, root JSON
  reports)

All five pipelines now process KOV files via the iter_peep_files
default flip; the deferred / KOV-irrelevant pipelines remain pinned
to include_kov=False until 2b/2c."
```

The commit will be sizeable (likely 15k–25k files modified across in-place
updates and the new per-pipeline output directories) — that's expected.

If `git status --short` after the commit shows anything **not** in the
above paths, investigate before treating the corpus run as complete.
Common culprits: a script that wrote to `krr_outputs/` without
cleaning up a stale temp file, or a newly-introduced output path that
isn't in the staging list above. Add it to the `for path in ...` loop
and re-commit (or amend).

---

## Task 12: Documentation updates

**Files:**
- Modify: `docs/SCHEMA_REFERENCE.md`
- Modify: `docs/REGULATIONS_INTEGRATION_PLAN.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: Schema reference**

In `docs/SCHEMA_REFERENCE.md`, find the existing "KOV Entity Model (Layer 1)" section. Append after it:

```markdown
## KOV Layer 2a — Schema Foundations

The Layer 2a PR declares the schema needed by Layers 2b and 2c. Instances
of these classes/properties begin appearing in 2b (preamble citations) and
2c (sanctions enforcement level). In 2a, only the declarations land.

### Classes

- `estleg:Citation` — reified preamble citation (Layer 2b populates).

### Properties

| Property | Domain | Range | Layer | Purpose |
| --- | --- | --- | --- | --- |
| `estleg:citationTarget` | `Citation` | `LegalProvision` | 2b | Resolved provision (paragraph-level) |
| `estleg:citationDetail` | `Citation` | `xsd:string` | 2b | Sub-paragraph specificity (`"lg 1 p 3"`) |
| `estleg:citationText` | `Citation` | `xsd:string` | 2b | Original citation text |
| `estleg:issuedUnder` | `Act` | `Act` | 2b | Act → enabling act (preamble simple form) |
| `estleg:implementsCitation` | `Act` | `Citation` | 2b | Act → reified Citation node |
| `estleg:implementedBy` | `Act` ∪ `LegalProvision` | `Act` | 2b | Inverse — acts enacted under |
| `estleg:implementedByCount` | `Act` ∪ `LegalProvision` | `xsd:integer` | 2b | Aggregate count |
| `estleg:enforcedAtLevel` | `LegalProvision` | `xsd:string` | 2c | `"state"` \| `"municipality"` |

### SHACL

`shacl/estonian_legal_shapes.ttl` adds `estleg:CitationShape` (target Citation;
required `citationTarget`; optional `citationDetail`/`citationText`; IRI pattern).

### Pipeline coverage reports

Each KOV-relevant pipeline emits `krr_outputs/reports/kov/<pipeline>_coverage.json`
with per-run metrics. See `krr_outputs/reports/kov/README.md`.
```

- [ ] **Step 2: Status note**

In `docs/REGULATIONS_INTEGRATION_PLAN.md`, add another blockquote after the existing 2026-05-02 Layer 1 status:

```markdown
> **Status (2026-05-03):** KOV integration Layer 2a (foundation +
> discovery fixes) implemented. 5 pipelines (deontic, EuroVoc, temporal,
> legal-concepts, amendment-history) now process KOV files; the iterator
> default flips to `include_kov=True`; 9 deferred/irrelevant pipelines pin
> `include_kov=False`. Layer 2b (cross-references + inverse) and 2c
> (semantic resolvers) follow as separate PRs. Gate B = umbrella milestone
> reached when 2a + 2b + 2c have all landed. See
> `docs/superpowers/plans/2026-05-03-kov-integration-layer2a.md`.
```

- [ ] **Step 3: CHANGELOG**

Add to the `[Unreleased]` section in `CHANGELOG.md` (extend the existing entry from Layer 1 if still under `[Unreleased]`, or open a new heading):

```markdown
### Added (Layer 2a)
- KOV integration Layer 2a — schema declarations for Citation class and
  8 new properties (`citationTarget`, `citationDetail`, `citationText`,
  `issuedUnder`, `implementsCitation`, `implementedBy`, `implementedByCount`,
  `enforcedAtLevel`); `estleg:CitationShape` SHACL shape; `iter_peep_files()`
  default flips to `include_kov=True`; 5 KOV-relevant discovery-only
  pipelines (deontic, EuroVoc, temporal, legal-concepts, amendment-history)
  process KOV files end-to-end with per-pipeline coverage reports.
- Per-pipeline coverage reports under `krr_outputs/reports/kov/` for the
  5 KOV-ready pipelines.
- 9 KOV-deferred or KOV-irrelevant pipelines explicitly pin
  `include_kov=False` at their call sites with comments indicating which
  Layer (2b/2c/3) will unpin or that KOV does not apply.
```

- [ ] **Step 4: README**

In `README.md`, find the KOV entity-model section (added in Layer 1). Add a sub-section:

```markdown
#### Layer 2a — KOV in 5 pipelines

Five KOV-relevant pipelines now process the 11,059 KOV act files end-to-end:

- `classify_deontic` — modal classifications over KOV provisions
- `classify_eurovoc` — EuroVoc tags over KOV acts (driven by `iter_peep_files()` instead of `INDEX.json`)
- `extract_temporal_data` — entry-into-force / amendment dates from KOV XML (via `globaalID` pairing)
- `extract_legal_concepts` — concept definitions from KOV provision text (provision IRIs sourced from actual `@id`)
- `generate_amendment_history` — amendment chains for KOV regulations

Per-pipeline coverage reports at `krr_outputs/reports/kov/`. See [Layer 2a plan](docs/superpowers/plans/2026-05-03-kov-integration-layer2a.md) for details.

The remaining 5 KOV-relevant pipelines (cross-references, inverse, sanctions, competence, court-provision-links) are deferred to Layers 2b and 2c.
```

- [ ] **Step 5: Commit**

```bash
git add docs/SCHEMA_REFERENCE.md docs/REGULATIONS_INTEGRATION_PLAN.md CHANGELOG.md README.md
git commit -m "Document KOV integration Layer 2a"
```

---

## Task 13: Final verification + push + PR

**Files:**
- (no source changes; this task validates cumulative work and ships the PR)

- [ ] **Step 1: Run the full pytest suite**

```
pytest -q
```

Layer 1 ended at **102 passed**. Layer 2a adds new tests in Tasks 1, 2,
4, 5, 6, 7, 8, 9, 10. The exact total depends on subagent decisions
(some tasks added a test in review; some merged tests). The
implementer should record whatever pytest actually reports here and
paste it into the PR body — no exact number is enforced by the plan.

Sanity-check the increment makes sense by spot-grepping new test
methods:

```bash
git diff main...HEAD -- 'tests/' | grep -cE "^\+\s*def test_"
```

Expected: ≥25 new test methods, all passing. If pytest's `passed`
count is significantly less than `(102 + new test method count)`,
investigate before pushing.

- [ ] **Step 2: Run validate_all (with proper exit-code propagation)**

```bash
set -o pipefail
python scripts/validate_all.py > /tmp/validate_all_final.log 2>&1 || \
    { tail -50 /tmp/validate_all_final.log; exit 1; }
tail -5 /tmp/validate_all_final.log
```

Expected: 0 errors. A non-zero exit halts and dumps the last 50 log
lines for diagnosis (avoids `tail -5` masking the Python crash).

- [ ] **Step 3: Verify all coverage reports exist and look reasonable**

```bash
ls -la krr_outputs/reports/kov/*.json
for p in classify_deontic classify_eurovoc extract_temporal_data extract_legal_concepts generate_amendment_history; do
  echo "=== $p ==="
  python3 -c "
import json
d = json.load(open('krr_outputs/reports/kov/${p}_coverage.json'))
print(f\"  input total/kov:     {d['input_files_total']} / {d['input_files_kov']}\")
print(f\"  files processed:     {d['files_processed']}\")
print(f\"  files skipped:       {d['files_skipped']}  (reasons: {d.get('skip_reasons')})\")
print(f\"  triples emitted:     {d['triples_emitted']}\")
print(f\"  fallback hits:       {d['fallback_hits']}\")
print(f\"  unresolved refs:     {d['unresolved_references']}\")
print(f\"  wall time:           {d['wall_time_seconds']}s ({d['items_per_second']} items/s)\")
print(f\"  peak memory:         {d['peak_memory_mb']} MB\")
print(f\"  errors:              {d['error_count']}\")
"
done
```

Expected: each pipeline shows `input_files_kov ≥ 11059`, `triples_emitted > 0`, and the runtime + memory fields populated (not 0.0). If `peak_memory_mb` is 0.0 you may be on Windows or the `resource` module is unavailable — note this in the PR body but don't block the PR.

- [ ] **Step 4: Verify the 9 pinned pipelines still skip KOV**

```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from estleg_common import iter_peep_files
files_no_kov = iter_peep_files(include_kov=False)
files_default = iter_peep_files()
print(f'  default: {len(files_default)} files')
print(f'  include_kov=False: {len(files_no_kov)} files')
print(f'  KOV-only delta: {len(files_default) - len(files_no_kov)} files')
"
```

Expected: KOV-only delta around 11,000 — the gap between with-KOV and without-KOV input universes.

- [ ] **Step 5: Push the branch and open the PR**

```
git push -u origin <branch-name>
gh pr create --base main --head <branch-name> --title "KOV integration Layer 2a — Foundation + Discovery Fixes" --body "$(cat <<'EOF'
## Summary
- Declares Layer 2 schema (Citation class + 8 new properties + SHACL CitationShape).
- Flips `iter_peep_files()` default to `include_kov=True`.
- 9 KOV-deferred / KOV-irrelevant pipelines pin `include_kov=False` explicitly.
- 5 KOV-relevant discovery-only pipelines (deontic, EuroVoc, temporal, legal-concepts, amendment-history) now process KOV files end-to-end.
- Per-pipeline coverage reports under `krr_outputs/reports/kov/`.

Layer 2a of the three-PR Layer 2 split. Gate B = umbrella milestone, reached when 2a + 2b + 2c land. See `docs/superpowers/plans/2026-05-03-kov-integration-layer2a.md`.

## What's NOT in this PR (deferred)
- Preamble citation extraction (`issuedUnder`/`implementsCitation`) — Layer 2b
- Inverse `implementedBy` projection — Layer 2b
- KOV-aware sanctions / competence / court-links — Layer 2c
- KOV-aware similarity — Layer 3

## Test plan
- [ ] `pytest -q` — record actual pass count (≈ Layer 1 baseline + ≥25 new tests)
- [ ] `python scripts/validate_all.py` — 0 errors
- [ ] Coverage reports show input_files_kov ≥ 11k for the 5 fixed pipelines
- [ ] Pinned pipelines still receive a KOV-free file set when run

## Reviewer guide for the coverage reports

Each KOV-relevant pipeline emits `krr_outputs/reports/kov/<pipeline>_coverage.json`. The coverage helper didn't exist on `main`, so the "before" baseline is implicit: pre-2a, `input_files_kov = 0` for all 5 fixed pipelines (KOV was excluded by the iterator default).

Verify the absolute values in this PR's reports look reasonable:
- `input_files_kov >= 11000` (full KOV corpus participated)
- `triples_emitted > 0`
- `error_count` ideally 0; small numbers OK if `failure_samples` shows KOV-specific edge cases, not systemic failures
- `items_per_second` and `peak_memory_mb` within budget

See `krr_outputs/reports/kov/README.md` for the full format.

## Known issues (carryover from Layer 1)
Pre-existing SHACL violations on `requestedCluster` IRI-as-literal (~21.5k) and missing `summary` on some KOV/state-reg provisions (~523) still need a separate cleanup PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

The branch name should be `feat/kov-integration-layer2a`.

- [ ] **Step 6: Wait for review + iterate as needed**

After the PR opens, address any review comments per `superpowers:receiving-code-review`.

---

## Self-Review Checklist

After all 13 tasks complete, verify:

- [ ] **Spec coverage.** Every Layer 2a requirement in the spec maps to a task:
  - Schema declarations (Citation, 8 properties): Tasks 1+2
  - Per-caller audit: Task 3
  - Default flip: Task 4
  - Coverage reporter: Task 5
  - 5 pipeline fixes: Tasks 6–10
  - Corpus run: Task 11
  - Docs: Task 12
  - Final verification: Task 13
- [ ] **No actionable gaps.** No "TODO", "TBD", "implement later",
  "Add appropriate <thing>" in the plan. (Code-block `...` ellipses
  are allowed and signal "the surrounding existing code stays as it
  is" — they have a clear meaning in context. Hedges like "if X
  arises, Y" are treated as actionable gaps and must be replaced
  with concrete instructions.)
- [ ] **Type consistency.** `CoverageReport` fields match across Tasks 5, 6, 7, 8, 9, 10, 11, 12. `build_globalid_xml_lookup` signature matches between Tasks 8 and 10.
- [ ] **Per-caller audit completeness.** All 14 `iter_peep_files()` consumers (in 13 scripts) are accounted for: 5 fixed (Tasks 6–10), 9 pinned (Task 3).
- [ ] **Crisp invariant satisfied.** After Task 13, the four invariant clauses hold: schema declared, default flipped with explicit pins, 5 pipelines run KOV, coverage reports diffable.
