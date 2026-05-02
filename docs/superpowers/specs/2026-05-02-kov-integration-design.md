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
  `False` to `True` after Layer 2 validates clean **and the per-script
  audit below passes for every consumer**.
- New flag `include_kov_similarity` (default `False` until Layer 3 ships)
  on `generate_similarity_index`.
- Add `--no-kov` opt-out flag for callers that want the historical
  laws-and-state-regulations-only behavior.

### Per-caller audit (precondition for the default flip)

Many existing scripts assume root-level files only and use patterns like
`KRR_DIR / filename` for output, which would either skip subdirectory
files or write them to the wrong path. The default flip cannot land
until every consumer of `iter_peep_files()` (or the bare `KRR_DIR.glob`
patterns it replaces) is audited and fixed. Concretely, for each
script the audit must verify:

1. **Input discovery** uses `iter_peep_files()`, not a bare
   `KRR_DIR.glob("*_peep.json")` (which silently misses subdirectories).
2. **Output path construction** uses the source file's full path or a
   relative path under `KRR_DIR`, not `KRR_DIR / filename`. Concrete
   anti-pattern, currently in `generate_similarity_index.py`:
   - Line ~140: `if fpath.parent != KRR_DIR: continue` — silently skips
     all regulation files. Must be removed.
   - Line ~289: `fpath = KRR_DIR / filename` — would clobber state-level
     files with same-named KOV files. Must be replaced with the original
     source path or a path-aware lookup.
3. **Idempotent re-run** still works after KOV files are added (no
   duplicate triples on a second pass).
4. **Per-script test** — at least one fixture-based test verifies the
   script processes a KOV file end-to-end and writes back to the right
   path under `krr_outputs/regulations/kov/<issuer>/`.

The audit lives as a checklist in the Gate B PR. Scripts that fail any
item are fixed before the default flips. Scripts genuinely
laws-and-state-only (the three EU-specific ones) pin
`include_kov=False` at the call site instead of relying on the default.

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
   EHAK code. Each auto-matched row records `mappingSource: "auto-match"`.
   Expected coverage ~50–60%.
2. **Curated map** — for the rest, hand-curate
   `data/ehak/issuer_successor_map.csv`, sourced from the official 2017
   haldusreform act lists. One row per legacy issuer with columns:
   - `issuer_slug`
   - `current_municipality_ehak`
   - `mapping_source` — `"haldusreform-2017"` for direct reform mappings,
     `"manual-review"` for cases that required human judgment
   - `mapping_evidence` — citation string (e.g.
     `"RT I, 21.06.2017, 1 § 7"`) or URL pointing to the source. Required
     and non-empty for every curated row — this is the auditable trail
     so a future reviewer can verify or correct each mapping without
     re-doing the research.
3. **Join** — generate `issuers.json` by combining (1) and (2). Fail
   loudly on unmapped issuers; never silently default. Each Issuer
   carries `mappingSource` (and `mappingEvidence` if available) on the
   `currentMunicipality` link.

### Ontology additions

```turtle
estleg:Municipality a owl:Class ;
    rdfs:subClassOf estleg:LegalEntity ;
    rdfs:comment "Current Estonian municipality (KOV unit)" .

estleg:Issuer a owl:Class ;
    rdfs:comment "An act-issuing body (volikogu or valitsus)" .

estleg:KovProvision a owl:Class ;
    rdfs:subClassOf estleg:LegalProvision ;
    rdfs:comment "A provision belonging to a MunicipalRegulation. Added
                  during Layer 1 enrichment so SHACL can target provision-
                  level KOV constraints without inference." .

# Properties on Municipality
estleg:ehakCode             xsd:string  # 4-digit EHAK code
estleg:county               xsd:string  # e.g. "Harju maakond"

# Properties on Issuer
estleg:bodyType             xsd:string  # "volikogu" | "valitsus"
estleg:currentMunicipality  estleg:Municipality
estleg:historicalMunicipalityName  xsd:string
estleg:mappingSource        xsd:string  # "auto-match" | "haldusreform-2017"
                                        # | "manual-review" | "url:<rt-citation>"
estleg:mappingEvidence      xsd:string  # free-text source citation
                                        # (e.g. "RT I, 21.06.2017, 1 § 7")

# Properties on MunicipalRegulation
estleg:enactedBy             estleg:Issuer
estleg:enactedByMunicipality estleg:Municipality

# Property on KovProvision (i.e. provisions of MunicipalRegulation acts)
estleg:enactedByMunicipality estleg:Municipality
```

### Stable IRI patterns

- **Municipality:** `estleg:Municipality_EHAK_<code>`
  (e.g. `estleg:Municipality_EHAK_0784` for Tallinn). The EHAK code is
  the natural primary key — stable, official, and immune to renames.
- **Issuer:** `estleg:Issuer_<slug>` (e.g.
  `estleg:Issuer_tallinna_linnavolikogu`). Slug is the existing
  directory name under `krr_outputs/regulations/kov/`, already
  guaranteed unique by the Phase 2 generator.

These patterns are documented in `docs/SCHEMA_REFERENCE.md` and asserted
by SHACL `sh:pattern` constraints to catch IRI drift in future imports.

### Output

- `krr_outputs/municipalities_peep.json` — 79 Municipality nodes
- `krr_outputs/issuers_kov_peep.json` — 357 Issuer nodes
- In-place update to all 11,059 KOV act files: each act node gains
  `estleg:enactedBy` and `estleg:enactedByMunicipality`
- In-place update to all 116,708 KOV provision nodes: each provision
  gains an additional `estleg:KovProvision` rdf:type **and** an
  `estleg:enactedByMunicipality` triple (redundant for query convenience
  — avoids requiring a join in queries)

### SHACL

Add shapes:

- **`MunicipalRegulation` shape** — must have both `enactedBy` and
  `enactedByMunicipality` (act-level constraint).
- **`KovProvision` shape** — must have `enactedByMunicipality`. This is
  the reason for adding the explicit `KovProvision` class during
  enrichment: provisions in the existing data are typed as
  `owl:NamedIndividual` of per-act `Regulation_<terviktekstId>` classes
  (subclasses of `LegalProvision`), so a shape targeting
  `MunicipalRegulation` would not validate them. Stamping a direct
  `KovProvision` rdf:type during Layer 1 enrichment gives SHACL a
  concrete `sh:targetClass` without needing an OWL reasoner.
- **`Issuer` shape** — must have `currentMunicipality`, `bodyType`,
  `mappingSource`. IRI conforms to `^estleg:Issuer_[a-z0-9_]+$`.
- **`Municipality` shape** — must have `ehakCode` (4-digit) and
  `county`. IRI conforms to `^estleg:Municipality_EHAK_[0-9]{4}$`.

These catch issuer-mapping drift the moment a future import introduces a
new unmapped issuer.

## Layer 2 — Pipeline integration

Each integrator's KOV-aware adjustment, in dependency order.

### `extract_cross_references`

The current extractor scans **provision nodes** (their `estleg:legalText`
and `estleg:summary`). It does not touch act-level
`estleg:preambleText`, where KOV enabling-law citations live (e.g.
*alkoholiseaduse § 42 lg 1 alusel*). Two real changes:

- **New: preamble scanning.** Add an act-level pass that runs the same
  citation regex over `estleg:preambleText` on each `MunicipalRegulation`
  and `NationalRegulation` act node. Preamble citations are typically
  enabling-law references, semantically distinct from body-text
  citations.
- **Where preamble references attach.** Attach to the **act node** via
  two new properties:
  - `estleg:issuedUnder` — links the act to the enabling **law** (when
    only the law is identifiable, e.g. *alkoholiseaduse alusel*).
  - `estleg:implementsProvision` — links the act to a specific
    **provision** of the enabling law (when the citation resolves to a
    `§/lg/p` granularity, e.g. *§ 42 lg 1*).
  Both go on the act node, not on a provision. Reason: the preamble is a
  property of the regulation as a whole, not of any individual paragraph.
  This also keeps query semantics clean ("which law authorizes this
  regulation?" is one triple, not a join via the first §).

- **Body-text references unchanged.** KOV body text occasionally cites
  internal acts (*linnavolikogu määrus nr 5*). Resolve by **scoping the
  search to the same `estleg:enactedBy` issuer first**, falling back to
  the global registry only if no match. Prevents Tallinn→Tartu false
  matches.
- "Käesolev määrus" already handled — no change.
- Output growth: ~3–5× current cross-ref volume on body text, plus
  ~11k preamble-derived `issuedUnder` / `implementsProvision` triples.

### `generate_inverse_references`

This is **not code-free**. The current generator emits
`estleg:referencedBy` from `estleg:references` body-text citations.
Implementing-law inverses are a new concept on top:

- **`estleg:implementedBy`** — a **new property** on enabling laws and
  law provisions, populated by inverting the act-level
  `estleg:issuedUnder` and `estleg:implementsProvision` triples added
  in the cross-reference extractor change above. Semantically this is
  "regulations enacted under this law", not "any text that mentions
  this law". It is a **filtered projection** of regulation→law
  references where the source link is preamble-derived, not a copy of
  the broader `referencedBy` set.
- **`estleg:implementedByCount`** — a derived **aggregate** triple on
  each law and law provision, set to the integer count of
  `implementedBy` entries. Lets queries like "which laws spawn the most
  municipal regulations?" run without materialising the full
  `implementedBy` array.
- **`estleg:referencedBy`** — unchanged behavior, just runs over the
  expanded file set. Will gain large back-reference sets on state laws.
  No additional summary triple needed beyond `implementedByCount` (which
  is what consumers actually want for the regulation-implementation
  relationship).
- **Idempotency rule:** the existing "clear before regenerate" pattern
  for `referencedBy` extends to both `implementedBy` and
  `implementedByCount`. Each rerun fully rebuilds.

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
  temporal, legal-concepts, court-provision-links, amendment-history:
  combined output file per pipeline (existing pattern, just bigger).
- Per-pipeline KOV coverage report at
  `krr_outputs/reports/kov/<pipeline>_coverage.json`. Each report
  carries:
  - **Coverage counts:** total input files, files processed, files
    skipped (and reason), triples emitted, fallback-pattern hits,
    unresolved references.
  - **Runtime metrics:** wall-time seconds, items-per-second, peak
    memory MB, input-file-count, error-count, run-timestamp,
    pipeline-version (git commit short SHA).
  - **Failure samples:** up to 20 example unresolved references or
    parse failures per run, so regressions over time are diagnosable
    from the report alone without rerunning.

### Performance budget

≤ 30 minutes of additional wall time on top of the current full-pipeline
run (~10 min). The dominant cost is similarity (Layer 3), which is gated
behind `include_kov_similarity=True`.

## Layer 3 — KOV-aware similarity

### Granularity: act-level

Choose **act-level similarity** for KOV, not provision-level. The
existing `generate_similarity_index` works at provision granularity
(comparing individual `LegalProvision` nodes); for KOV, the answerable
question is *"how do two municipalities' regulations on the same topic
compare as a whole?"*, which is an act-to-act question, not a
paragraph-to-paragraph question. Provision-level comparison would also
explode to 116k × 116k naive pairs, which the existing constraints
were never designed for.

Concretely:

- One TF-IDF vector (or embedding) per **act**, built by concatenating
  the `legalText` of all child provisions plus the act title and
  preamble.
- Pairwise cosine similarity within each bucket.
- Output IDs are **act IRIs** (e.g. `estleg:Reg_1014955_Map_2026`), not
  provision IRIs.
- Provision-level KOV similarity stays out of scope — defer to a future
  phase if a concrete need surfaces.

The existing law/state-regulation similarity index continues to operate
at provision-level — this is purely a new act-level index added
alongside it.

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

### Layer 1 tests (~10)

- EHAK code list parses; 79 expected codes present
- Every issuer slug from `krr_outputs/regulations/kov/*` resolves to a
  `currentMunicipalityCode`
- Auto-mapping covers obvious matches; curated CSV covers the rest;
  intersection check
- Synthetic unmapped issuer raises a clear error
- Curated rows without `mapping_evidence` are rejected at load time
- SHACL: every Municipality has `ehakCode` + `county`; every Issuer has
  `currentMunicipality`, `bodyType`, and `mappingSource`
- SHACL: IRI patterns match `Municipality_EHAK_<4digit>` and
  `Issuer_<slug>`
- SHACL: every `KovProvision` has `enactedByMunicipality`
- Pre/post-reform: *Abja Vallavolikogu* maps to Mulgi vald,
  `historicalMunicipalityName` preserved

### Act-level enrichment tests (~4)

- Sample of 20 KOV acts gain both `enactedBy` and `enactedByMunicipality`
- All 116,708 provisions gain `enactedByMunicipality` (count assertion)
- Idempotent: running enrichment twice doesn't duplicate triples
- Round-trip: serialise → re-load → triple counts match

### Pipeline KOV-mode tests (~14)

- Each KOV-relevant integrator produces non-empty output on a 20-act
  fixture
- Cross-reference: preamble citation
  *alkoholiseaduse § 42 lg 1 alusel* produces both `issuedUnder` (to
  the alcohol law act IRI) and `implementsProvision` (to `§ 42 lg 1`)
  triples on the act node, not on any provision
- Cross-reference: act with no preamble emits no `issuedUnder`
- Cross-reference: intra-municipality `linnavolikogu määrus nr X`
  resolves to same-issuer act, not cross-municipality
- Inverse: alcohol law gains `implementedBy` triples derived **only**
  from preamble enabling-law links, not from incidental body-text
  references
- Inverse: `implementedByCount` equals the cardinality of
  `implementedBy`, and is rebuilt cleanly on a second run
- Per-script audit harness: every consumer of `iter_peep_files()` has a
  fixture test that processes a KOV file and writes back to the
  correct path under `krr_outputs/regulations/kov/<issuer>/`
- Per-script audit harness: catches the `KRR_DIR / filename` bug — a
  fixture with same-named files in root and KOV directories does not
  cause clobbering
- Competence: extractor binds to Issuer entity (not free-text node) for
  the matching municipality
- Sanctions: KOV waste-violation fine clause is captured with
  `enforcedAtLevel = municipality`

### Similarity tests (~6)

- Output IDs in similarity files are act IRIs
  (`estleg:Reg_<terviktekstId>_Map_<year>`), not provision IRIs
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
  over the full KOV set. Per-pipeline coverage reports attached, with
  runtime metrics. **The per-caller audit (input discovery + output
  path handling + idempotency + per-script test) passes for every
  consumer** before the `iter_peep_files()` default flips. Ship as
  second PR.
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

- 79 Municipality nodes and 357 Issuer nodes generated and SHACL-valid,
  using the documented IRI patterns
  (`Municipality_EHAK_<code>`, `Issuer_<slug>`).
- Every Issuer carries `mappingSource`; every curated mapping carries
  non-empty `mappingEvidence`.
- Every KOV act has `enactedBy` and `enactedByMunicipality`. Every KOV
  provision is typed as `KovProvision` and has `enactedByMunicipality`.
- The cross-reference extractor scans both provision text and act-level
  `preambleText`. Preamble references attach to the act node via
  `issuedUnder` and `implementsProvision`.
- The inverse generator emits both `implementedBy` (filtered projection
  from preamble enabling-law links) and `implementedByCount` (aggregate)
  on each enabling law and law-provision.
- Cross-reference, inverse, deontic, EuroVoc, temporal, sanctions,
  competence, legal-concepts, court-provision-links, and amendment-history
  pipelines all run over KOV by default after Gate B. EU-specific
  pipelines (draft-impact, harmonisation-links, transposition-mapping)
  remain pinned `include_kov=False`.
- Per-caller audit complete: every consumer of `iter_peep_files()` uses
  the iterator for input discovery, preserves source paths for output,
  is idempotent, and has a fixture test that processes a KOV file
  end-to-end. The default flip lands only after this audit passes.
- Per-pipeline KOV coverage report exists at
  `krr_outputs/reports/kov/<pipeline>_coverage.json`, including runtime
  metrics (wall-time, items-per-second, memory) and failure samples.
- KOV-aware similarity is **act-level**, ships behind
  `include_kov_similarity=True`, with bucket coverage ≥80% and bounded
  output size.
- Three documentation surfaces updated; CHANGELOG carries three entries.
