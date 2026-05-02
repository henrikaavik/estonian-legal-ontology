# KOV (Municipal Regulations) Full Integration — Design

> **Status:** Design (2026-05-02). Builds on Phase 2 of the regulations
> integration (commit `5ffacc34`), which generated 11,059 KOV acts under
> `krr_outputs/regulations/kov/` as a mapped-but-unintegrated layer. This
> spec covers turning that layer into first-class ontology content with
> proper municipality context.

## Problem

KOV (kohaliku omavalitsuse) regulations are currently in the repository as
JSON-LD (11,059 acts / 116,708 provisions / 11,522 annexes), but every
integration pipeline (cross-reference, inverse, deontic, EuroVoc, temporal,
sanctions, competence, similarity) skips them by default through
`estleg_common.iter_peep_files(include_kov=False)`. KOV act nodes carry
`estleg:issuer` as a free-text string and `estleg:isKov: true`, but there is
no formal **Municipality** entity, no link from issuer body
(*vallavolikogu*/*vallavalitsus*) to the municipality it acts for, and no
way to query "all regulations of Tallinn" or "compare jäätmehoolduseeskiri
across municipalities".

Two further issues block naive integration:

1. **357 distinct issuers but ~79 current municipalities.** The 2017
   haldusreform merged hundreds of pre-reform vallad/linnad. Many legacy
   issuers (e.g. *Abja Vallavolikogu*) are still present in the data because
   their acts remained on the books after merger.
2. **High near-duplicate-title density.** Every municipality enacts its own
   *põhimäärus*, *jäätmehoolduseeskiri*, *hankekord*, etc. A naive pairwise
   similarity run produces 121M trivially-similar pairs.

## Goal

Make KOV regulations first-class participants in the ontology, with proper
municipality context, while keeping pipeline scale and similarity output
quality under control.

Concretely, after this work:

- Every KOV act and provision is queryable by Municipality and by Issuer
- Every integration pipeline runs over KOV acts by default
- Similarity is bucketed by regulation-type so cross-municipality
  comparisons (e.g. "Tallinn vs Tartu jäätmehoolduseeskiri") are tractable
  and useful, not noise
- The historical issuer→current-municipality chain is preserved without
  modeling defunct entities

## Architecture

Three concentric layers, shipped as three sequential PRs. Each layer is
independently revertible and the earlier ones are useful on their own.

```
Layer 1 — Entity model (Municipality + Issuer)
  79 Municipality nodes + 357 Issuer nodes
  Every KOV act linked to both
  No pipeline changes; pure additive schema work
  ↓
Layer 2 — Pipeline integration
  iter_peep_files() default flipped to include_kov=True
  Cross-ref, inverse, deontic, EuroVoc, temporal, sanctions, competence,
    legal-concepts all run over KOV
  Per-pipeline coverage reports
  ↓
Layer 3 — KOV-aware similarity
  Bucket-by-regulation-type, intra-bucket pairwise only
  Plus KOV→state-law cross-layer pairs
  Gated behind include_kov_similarity=True until validated
```

### Default behavior changes

- `estleg_common.iter_peep_files(include_kov=...)` — flips default from
  `False` to `True` after Layer 2 validates clean.
- New flag `include_kov_similarity` (default `False` until Layer 3 ships)
  on `generate_similarity_index`.
- Add `--no-kov` opt-out flag for callers that want the historical
  laws-and-state-regulations-only behavior.

## Layer 1 — Municipality + Issuer entity model

### Source of truth

- **Municipalities:** EHAK (Eesti haldus- ja asustusjaotuse klassifikaator)
  code list from Statistics Estonia. Ship as a static snapshot at
  `data/ehak/municipalities.json` with ~79 entries. Each entry:
  `{ ehakCode, name, county, type: "vald" | "linn" }`.
- **Issuers:** Generated from the 357 distinct `estleg:issuer` strings
  already present in `krr_outputs/regulations/kov/`. Ship as
  `data/ehak/issuers.json`. Each entry:
  `{ slug, displayName, bodyType, currentMunicipalityCode,
     historicalMunicipalityName }`.

### Successor mapping

Build the issuer→current-municipality map semi-automatically:

1. **Auto-match** — for every issuer whose name root matches a current
   municipality (`tallinna_*`, `tartu_*`, `mulgi_*`, etc.), map to that
   EHAK code. Expected coverage ~50–60%.
2. **Curated map** — for the rest, hand-curate
   `data/ehak/issuer_successor_map.csv`, sourced from the official 2017
   haldusreform act lists. One row per legacy issuer.
3. **Join** — generate `issuers.json` by combining (1) and (2). Fail
   loudly on unmapped issuers; never silently default.

### Ontology additions

```turtle
estleg:Municipality a owl:Class ;
    rdfs:subClassOf estleg:LegalEntity ;
    rdfs:comment "Current Estonian municipality (KOV unit)" .

estleg:Issuer a owl:Class ;
    rdfs:comment "An act-issuing body (volikogu or valitsus)" .

# Properties on Municipality
estleg:ehakCode             xsd:string  # 4-digit EHAK code
estleg:county               xsd:string  # e.g. "Harju maakond"

# Properties on Issuer
estleg:bodyType             xsd:string  # "volikogu" | "valitsus"
estleg:currentMunicipality  estleg:Municipality
estleg:historicalMunicipalityName  xsd:string

# Properties on MunicipalRegulation (and its provisions)
estleg:enactedBy             estleg:Issuer
estleg:enactedByMunicipality estleg:Municipality
```

### Output

- `krr_outputs/municipalities_peep.json` — 79 Municipality nodes
- `krr_outputs/issuers_kov_peep.json` — 357 Issuer nodes
- In-place update to all 11,059 KOV act files: each act node gains
  `estleg:enactedBy` and `estleg:enactedByMunicipality`
- In-place update to all 116,708 KOV provision nodes: each provision gains
  `estleg:enactedByMunicipality` (redundant for query convenience —
  avoids requiring a join in queries)

### SHACL

Add shapes to assert:

- Every `MunicipalRegulation` has both `enactedBy` and
  `enactedByMunicipality`.
- Every `Issuer` has `currentMunicipality` and `bodyType`.
- Every `Municipality` has `ehakCode` and `county`.

These catch issuer-mapping drift the moment a future import introduces a
new unmapped issuer.

## Layer 2 — Pipeline integration

Each integrator's KOV-aware adjustment, in dependency order.

### `extract_cross_references`

- KOV preambles cite state laws (e.g. *alkoholiseaduse § 42 lg 1 alusel*).
  Existing law-name resolver covers these — no algorithm change, just
  needs to run over KOV files.
- KOV body text occasionally cites internal acts (*linnavolikogu määrus
  nr 5*). Resolve by **scoping the search to the same `estleg:enactedBy`
  issuer first**, falling back to the global registry only if no match.
  Prevents Tallinn→Tartu false matches.
- "Käesolev määrus" already handled — no change.
- Output growth: ~3–5× current cross-ref volume.

### `generate_inverse_references`

- No code change beyond running over the new file set.
- State laws will gain large `estleg:implementedBy` back-reference sets
  (e.g. one alcohol law cited by hundreds of municipal jaemüügi rules).
  Add an `estleg:implementedByCount` summary triple per law to keep
  query result sizes sane.

### `classify_deontic`

- Pure NLP over text. No KOV-specific change. Output grows ~10×
  (116k provisions vs ~12k currently classified).

### `classify_eurovoc`

- KOV regulations are concept-rich (waste, transport, education, alcohol
  retail, public spaces) — exactly EuroVoc's wheelhouse. No code change.
- Output grows substantially but adds high discoverability value.

### `extract_temporal_data`

- Each KOV act already has `entryIntoForce` and `lastAmendmentDate` from
  generation. The temporal extractor mainly hunts in-text dates
  (transition periods, sunset clauses). No KOV-specific change.

### `extract_sanctions`

- KOV regulations contain "väärtegu, mille eest on ette nähtud rahatrahv
  kuni X trahviühikut" clauses (parking, waste, public-order). Existing
  pattern handles the standard form.
- New derived property: `estleg:enforcedAtLevel = "state" | "municipality"`
  — useful for analytics distinguishing state vs municipal sanctions.

### `extract_institutional_competence`

- High value for KOV — a lot of competence delegation flows through
  municipal regulations.
- **Change:** when the extracted authority string matches a known Issuer
  slug **for the same municipality** (using the act's
  `enactedByMunicipality` to disambiguate), link to the Issuer entity
  rather than generating a new free-text node.
- **Risk:** medium. Without disambiguation, "linnavolikogu" mentioned in
  357 different municipal acts would either explode into 357 nodes or
  collapse into one nonsense node. Use the act's municipality context.

### `extract_legal_concepts`

- No KOV-specific change.

### `extract_court_provision_links`

- KOV regulations do get cited in court decisions (zoning disputes,
  traffic fines, public-order cases). Run over KOV files. No
  algorithm change beyond file discovery.

### `generate_amendment_history`

- KOV acts have amendment chains (each `globaalID` is a redaction of a
  `terviktekstID`). Existing chain logic is generic. Run over KOV files.
  No algorithm change.

### Pipelines that stay laws-and-state-only

These pipelines are EU- or draft-specific and do not apply to municipal
acts. They keep `include_kov=False` permanently — not opted out of
integration, just not relevant:

- `extract_draft_impact` — KOV acts are not in the EIS draft pipeline.
- `generate_harmonisation_links` — EU harmonisation. KOV does not
  transpose EU directives.
- `generate_transposition_mapping` — EU directive transposition. Same.

The default `iter_peep_files()` flip applies only to KOV-relevant
integrators; these three pin `include_kov=False` at the call site.

### Output partitioning

- Cross-references, inverse, deontic, EuroVoc, sanctions, competence,
  temporal, legal-concepts: combined output file per pipeline (existing
  pattern, just bigger).
- Per-pipeline KOV coverage report at
  `krr_outputs/reports/kov/<pipeline>_coverage.json` — counts of acts
  processed, fallback hits, unresolved references. Catches regressions
  over time.

### Performance budget

≤ 30 minutes of additional wall time on top of the current full-pipeline
run (~10 min). The dominant cost is similarity (Layer 3), which is gated
behind `include_kov_similarity=True`.

## Layer 3 — KOV-aware similarity

### Bucket strategy

Detect regulation-type bucket from title and structure. Run pairwise
within each bucket only.

**Type detection (keyword-based first pass):**

Top-50 most frequent title patterns from `REGULATIONS_KOV_INDEX.json`:
- `põhimäärus` (institutional charters)
- `jäätmehoolduseeskiri`
- `hankekord`
- `eelarve` / `aastaeelarve`
- `arengukava`
- `kohanime määramine` / `tänavate nimekirja kinnitamine`
- `aadressitähised`
- `huvihariduse / huvitegevuse kava`
- `koolide / lasteaedade põhimäärus`
- `linnavalitsuse põhimäärus`
- (etc., extracted from the actual top-50 by frequency)

**Coverage target:** ≥80% of KOV acts assigned to a non-uncategorized
bucket. Acts that don't match any bucket land in `_uncategorized` and
skip cross-pair similarity (intra-bucket only).

**Fallback (option C from brainstorming):** if regulation-type detection
coverage falls below 80%, switch to title-cluster mode — normalize titles
(`Tallinna jäätmehoolduseeskiri` → `jäätmehoolduseeskiri`), cluster by
normalized title, then run pairwise within each cluster.

### Cross-layer pairs

Also run **KOV→state-law** and **KOV→state-regulation** similarity (small
volume, high value — surfaces "this municipal rule reuses language from
the enabling state act"). Skip KOV→KOV cross-bucket pairs.

### Output

`krr_outputs/similarity/kov_by_type/<bucket>.json`, one file per bucket.
Each file lists top-K=20 most similar peer acts per source act. Bounded
size; tractable to load and query.

Estimated total pairs: ~200K vs naive 121M.

## Testing strategy

Extends the existing 39-test pytest suite.

### Layer 1 tests (~8)

- EHAK code list parses; 79 expected codes present
- Every issuer slug from `krr_outputs/regulations/kov/*` resolves to a
  `currentMunicipalityCode`
- Auto-mapping covers obvious matches; curated CSV covers the rest;
  intersection check
- Synthetic unmapped issuer raises a clear error
- SHACL: every Municipality has `ehakCode` + `county`; every Issuer has
  `currentMunicipality` + `bodyType`
- Pre/post-reform: *Abja Vallavolikogu* maps to Mulgi vald,
  `historicalMunicipalityName` preserved

### Act-level enrichment tests (~4)

- Sample of 20 KOV acts gain both `enactedBy` and `enactedByMunicipality`
- All 116,708 provisions gain `enactedByMunicipality` (count assertion)
- Idempotent: running enrichment twice doesn't duplicate triples
- Round-trip: serialise → re-load → triple counts match

### Pipeline KOV-mode tests (~10, one per integrator)

- Each integrator's KOV-mode produces non-empty output on a 20-act fixture
- Cross-reference: intra-municipality `linnavolikogu määrus nr X`
  resolves to same-issuer act, not cross-municipality
- Inverse: alcohol law gains `implementedBy` count > 0
- Competence: extractor binds to Issuer entity (not free-text node) for
  the matching municipality
- Sanctions: KOV waste-violation fine clause is captured with
  `enforcedAtLevel = municipality`

### Similarity tests (~5)

- Bucket detector classifies a fixture set of 30 KOV acts with ≥80%
  coverage
- Two `jäätmehoolduseeskiri` acts from different municipalities land in
  the same bucket
- Top-K bound respected; output file size sane
- Uncategorized acts skip cross-pair similarity
- KOV→state-law similarity surfaces at least one expected match (alcohol
  jaemüük rule ↔ alcohol law)

### End-to-end smoke (~2)

- Full pipeline run on a 50-act KOV fixture completes in <60s
- SHACL validation passes on the final integrated graph

## Validation gates and rollout

Three sequential PRs, each with its own gate:

- **Gate A — Layer 1.** `validate_all.py` clean, every KOV act and
  provision enriched, SHACL passes, all 357 issuers mapped. Ship as
  standalone PR.
- **Gate B — Layer 2.** All ten KOV-relevant pipelines (cross-ref,
  inverse, deontic, EuroVoc, temporal, sanctions, competence,
  legal-concepts, court-provision-links, amendment-history) run cleanly
  over the full KOV set. Per-pipeline coverage reports attached.
  `iter_peep_files()` default flipped. Ship as second PR.
- **Gate C — Layer 3.** Bucket coverage ≥80% on full KOV set, similarity
  output size bounded, KOV→state-law similarity sample reviewed. Ship
  as third PR.

Each PR is independently revertible. Layer 1 alone provides immediate
query value (per-municipality, per-issuer queries become possible) —
Layer 2 and Layer 3 are upgrades on top.

## Documentation deltas

- `README.md` — KOV section in the data inventory; mention "first-class"
  status after Gate B
- `docs/SCHEMA_REFERENCE.md` — new classes (`Municipality`, `Issuer`)
  and properties (`enactedBy`, `enactedByMunicipality`,
  `currentMunicipality`, `bodyType`, `ehakCode`, `county`,
  `historicalMunicipalityName`, `enforcedAtLevel`, `implementedByCount`)
- `docs/API_GUIDE.md` — example queries by municipality, by issuer, by
  regulation-type bucket
- `docs/REGULATIONS_INTEGRATION_PLAN.md` — mark Phase 2 complete; link
  to this spec
- `CHANGELOG.md` — three entries (one per layer)

## Out of scope

Deferred to a future phase:

- Historical Municipality entities for pre-2017 defunct units (handled
  via `historicalMunicipalityName` string only)
- Full historical-redaction temporal graphs for KOV acts
- Cross-language tags (KOV regulations are essentially Estonian-only)
- KOV→KOV similarity beyond the bucketed pairwise (clustering, theme
  detection)
- `data/ehak/municipalities.json` auto-refresh from Statistics Estonia
  API (manual snapshot for now)

## Acceptance criteria

- 79 Municipality nodes and 357 Issuer nodes generated and SHACL-valid.
- Every KOV act has `enactedBy` and `enactedByMunicipality`. Every KOV
  provision has `enactedByMunicipality`.
- Cross-reference, inverse, deontic, EuroVoc, temporal, sanctions,
  competence, legal-concepts, court-provision-links, and amendment-history
  pipelines all run over KOV by default after Gate B. EU-specific
  pipelines (draft-impact, harmonisation-links, transposition-mapping)
  remain pinned `include_kov=False`.
- Per-pipeline KOV coverage report exists at
  `krr_outputs/reports/kov/<pipeline>_coverage.json`.
- KOV-aware similarity ships behind `include_kov_similarity=True`,
  with bucket coverage ≥80% and bounded output size.
- Three documentation surfaces updated; CHANGELOG carries three entries.
