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
- **`generate_similarity_index` must pin `include_kov=False` at the
  call site as part of the Gate B PR**, before the iterator default
  flips. Reason: similarity stays provision-level until Layer 3 ships,
  so it cannot accept KOV input from the new default. Without this
  pin, `run_all_integration` would either run old provision-level
  similarity over 116k KOV provisions (wrong) or fail. Layer 3
  unpins it (replacing the call with the new act-level KOV similarity
  generator) and adds a separate `include_kov_similarity` flag for
  callers that want to skip the KOV-specific output.
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

1. **Auto-match** — for every issuer, derive the candidate municipality
   from **both** the name root **and** the body suffix:
   - `*_linnavolikogu` / `*_linnavalitsus` → root must match a
     municipality of type `linn`.
   - `*_vallavolikogu` / `*_vallavalitsus` → root must match a
     municipality of type `vald`.

   This disambiguates duplicate roots — *Tartu Linnavolikogu* (the
   city) vs *Tartu Vallavolikogu* (the rural municipality), and
   similarly Rakvere, Viljandi, Võru. Auto-match **fails (does not
   guess)** if:
   - The root + body-type combination matches more than one current
     municipality, or
   - The root has no matching municipality of the required type
     (e.g. a `vallavolikogu` whose root only exists as a `linn`).

   Failed auto-matches fall through to the curated CSV. Each
   successfully auto-matched row records
   `mappingSource: "auto-match"`. Expected coverage ~50–60% with the
   stricter rule (slightly lower than naive root-only matching, but
   the failures it surfaces are exactly the ambiguous cases that need
   human review).
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

The current schema has no parent class for "act" or "law" — acts are
typed as `owl:Ontology` plus their specific subtype (`MunicipalRegulation`,
`NationalRegulation`, etc.). There is also no `estleg:Law` or
`estleg:LegalEntity` class. Layer 1 introduces the missing parent
hierarchy so `issuedUnder` and `implementedBy` can have a clean class
range:

```turtle
# New parent class for "any enacted Estonian legal act"
estleg:Act a owl:Class ;
    rdfs:comment "An enacted Estonian legal act of any level" .

# Marker class for laws (the existing 615 root-level acts).
# Stamped onto every law's act node during Layer 1 enrichment.
estleg:Law a owl:Class ;
    rdfs:subClassOf estleg:Act .

# Existing regulation classes are declared as subclasses of Act
estleg:NationalRegulation     rdfs:subClassOf estleg:Act .
estleg:GovernmentRegulation   rdfs:subClassOf estleg:NationalRegulation .
estleg:MinisterialRegulation  rdfs:subClassOf estleg:NationalRegulation .
estleg:MunicipalRegulation    rdfs:subClassOf estleg:Act .

# KOV entity model
estleg:Municipality a owl:Class ;
    rdfs:comment "Current Estonian municipality (KOV unit). Top-level
                  class — represents a territorial unit, not an
                  organizational body." .

estleg:Issuer a owl:Class ;
    rdfs:subClassOf estleg:Institution ;
    rdfs:comment "An act-issuing body (volikogu or valitsus). Subclass
                  of Institution so that competence-extractor links
                  can target Issuers using existing Institution-range
                  properties without schema duplication." .

estleg:KovProvision a owl:Class ;
    rdfs:subClassOf estleg:LegalProvision ;
    rdfs:comment "A provision belonging to a MunicipalRegulation. Added
                  during Layer 1 enrichment so SHACL can target provision-
                  level KOV constraints without inference." .

estleg:Citation a owl:Class ;
    rdfs:comment "A reified preamble citation, carrying the resolved
                  provision target and the original sub-paragraph
                  specificity." .

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

# Properties on KovProvision
estleg:enactedBy             estleg:Issuer       # direct query path
estleg:enactedByMunicipality estleg:Municipality # direct query path
estleg:partOfAct             estleg:Act          # IRI link to parent act
                                                  # (the existing sourceAct
                                                  # is a literal, not an IRI,
                                                  # so this is the join path
                                                  # KOV consumers actually
                                                  # need)

# Properties on Act (used across Law, NationalRegulation, MunicipalRegulation)
estleg:issuedUnder           estleg:Act          # range: any Act
estleg:implementsCitation    estleg:Citation     # reified preamble citation
estleg:implementedBy         estleg:Act          # inverse, from
                                                  # any Act to any Act

# Properties on Citation
estleg:citationTarget        estleg:LegalProvision
estleg:citationDetail        xsd:string          # optional, e.g. "lg 1 p 3"
estleg:citationText          xsd:string          # optional original text
```

**Layer 1 enrichment also stamps the new parent types:**

- Every existing law peep file's act node gains `estleg:Law` rdf:type
  (additive — keeps `owl:Ontology` and any existing types).
- Every existing regulation peep file's act node already has its
  specific subtype (NationalRegulation / MunicipalRegulation / etc.);
  these are declared subclasses of `estleg:Act` in the schema so no
  per-file rewrite is needed for the parent type.
- Every KOV provision gains both `estleg:KovProvision` rdf:type **and**
  `estleg:partOfAct` (IRI to the parent act node).

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
- In-place update to all 11,059 KOV act files. Each act node gains:
  - `estleg:enactedBy` (Issuer IRI)
  - `estleg:enactedByMunicipality` (Municipality IRI)
  - `estleg:titleNormalized` (string, used by Layer 3 bucket detection)
- In-place update to all 116,708 KOV provision nodes. Each provision
  gains:
  - additional `estleg:KovProvision` rdf:type
  - `estleg:enactedByMunicipality` (Municipality IRI)
  - `estleg:enactedBy` (Issuer IRI) — direct query path so consumers
    don't need a join to ask "who enacted this provision"
  - `estleg:partOfAct` (IRI of the parent act node) — the existing
    `estleg:sourceAct` is a literal title, not an IRI link, so this
    is the actual structural join path
- In-place update to all 615 existing law peep files: each act node
  gains an additional `estleg:Law` rdf:type stamp (Layer 1 schema
  hierarchy completion).

### SHACL

Add shapes:

- **`MunicipalRegulation` shape** — must have `enactedBy`,
  `enactedByMunicipality`, and `titleNormalized` (act-level
  constraints).
- **`KovProvision` shape** — must have `enactedByMunicipality`,
  `enactedBy`, and `partOfAct`. Adding the explicit `KovProvision`
  class during enrichment is what makes this shape targetable:
  provisions in the existing data are typed as `owl:NamedIndividual`
  of per-act `Regulation_<terviktekstId>` classes (subclasses of
  `LegalProvision`), so a shape targeting `MunicipalRegulation` would
  not validate them. Stamping a direct `KovProvision` rdf:type during
  Layer 1 enrichment gives SHACL a concrete `sh:targetClass` without
  needing an OWL reasoner.
- **`Issuer` shape** — must have `currentMunicipality`, `bodyType`,
  `mappingSource`. IRI conforms to `^estleg:Issuer_[a-z0-9_]+$`.
- **`Municipality` shape** — must have `ehakCode` (4-digit) and
  `county`. IRI conforms to `^estleg:Municipality_EHAK_[0-9]{4}$`.
- **`Law` shape** — every law act node has both `owl:Ontology` and
  `estleg:Law` types after Layer 1 enrichment.
- **`Citation` shape** — must have `citationTarget`. `citationDetail`
  and `citationText` are optional. (Used by Layer 2.)
- **`Similarity` shape** — must have `similarTarget`, `similarityScore`,
  `similarityModel`. (Used by Layer 3.)

These catch issuer-mapping drift the moment a future import introduces a
new unmapped issuer, and they catch any KOV act/provision that the
Layer 1 enrichment fails to fully decorate.

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

  - `estleg:issuedUnder` — links the act to the **enabling act**
    (range: `estleg:Act`, the new parent class — i.e. any
    `Law`/`NationalRegulation`/`MunicipalRegulation`). Used for
    coarse "which act authorizes this regulation?" queries, where
    sub-paragraph specificity does not matter. Examples of citations
    handled: *alkoholiseaduse alusel* (Law), *Vabariigi Valitsuse 19.
    detsembri 2019. a määruse nr 112 alusel* (NationalRegulation),
    *Tallinna Linnavolikogu 18. juuni 2020 määruse nr 15 alusel*
    (MunicipalRegulation). Range expansion is important: real KOV
    preambles routinely cite state regulations and same-municipality
    regulations as enabling bases, not just laws.

  - `estleg:implementsCitation` — range: `estleg:Citation`. Used for
    full-detail preamble citations including paragraph and
    sub-paragraph specificity. Each Citation is a **reified node**
    rather than a value pair, because pairing a target with a
    `citationDetail` literal by list ordering is unsafe in JSON-LD:
    serialization, compaction, or graph round-trip can re-order the
    two arrays independently and silently lose which detail belongs
    to which target.

  - `estleg:Citation` — new class. Each instance is a self-contained
    object with:
    - `estleg:citationTarget` — IRI of the resolved provision
      (paragraph-level — see below)
    - `estleg:citationDetail` — optional literal (`"lg 1 p 3"`,
      `"lg 2"`) for sub-paragraph specificity
    - `estleg:citationText` — optional original citation string
      (`"§ 42 lg 1 alusel"`) for traceability

  IRI scheme: `estleg:Citation_<source-act-shortid>_<seq>` (e.g.
  `Citation_1014955_Map_1` for the first preamble citation of
  the Tallinn alcohol act). Each Citation is a `owl:NamedIndividual`
  so SHACL can constrain it.

- **Provision-citation granularity.** The current ontology has
  paragraph-level provision IRIs only — `loige` (lg) and `punkt` (p)
  exist as text inside a paragraph but are not separately
  addressable. `citationTarget` therefore resolves to the paragraph
  IRI; the lg/p detail rides along on `citationDetail`. Adding a
  real `Loige` / `Punkt` provision-IRI model is deferred to a future
  phase.

- **Body-text references — scoping rules.** KOV body text cites
  internal acts (*linnavolikogu määrus nr 5*) including
  cross-body-within-municipality cases (a *vallavalitsus* act citing a
  *vallavolikogu* act of the same municipality is normal — e.g. an
  executive ordinance enacted under a council regulation). Scope the
  resolver in this order:
  1. **Same `enactedByMunicipality`** — scope to all issuers (volikogu
     and valitsus) of the same municipality. Catches the common
     vallavalitsus → vallavolikogu case.
  2. **Within that scope, narrow by issuer body type** if the citation
     specifies one ("linnavolikogu määrus" → volikogu; "linnavalitsuse
     määrus" → valitsus).
  3. **Then narrow by act number / title** if the citation provides
     one.
  4. **Global fallback only on a unique, high-confidence match** —
     skip if multiple plausible candidates exist outside the
     municipality, rather than picking arbitrarily.
- "Käesolev määrus" already handled — no change.
- Output growth: ~3–5× current cross-ref volume on body text, plus
  ~11k preamble-derived `issuedUnder` triples and a comparable number
  of `implementsCitation`/`Citation` nodes.

### `generate_inverse_references`

This is **not code-free**. The current generator emits
`estleg:referencedBy` from `estleg:references` body-text citations.
Implementing-law inverses are a new concept on top:

- **`estleg:implementedBy`** — a **new property** on enabling acts and
  their provisions (range/domain: `estleg:Act` ∪
  `estleg:LegalProvision`), populated by inverting the act-level
  `estleg:issuedUnder` triples and the `Citation.citationTarget`
  links from `implementsCitation` reified nodes. Semantically "acts
  enacted under this act", not "any text that mentions it". It is a
  **filtered projection** of references where the source link is
  preamble-derived, not a copy of the broader `referencedBy` set. The
  range covers all `Act` subtypes because state regulations and KOV
  regulations can themselves be implementation targets of higher-level
  acts.
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

This is **not code-free**. The current classifier (line ~441) drives
discovery from `krr_outputs/INDEX.json`, and line ~433 explicitly skips
files where `peep_file.parent != KRR_DIR`. `INDEX.json` is laws-only and
does not list regulations or KOV acts. Required changes:

- **Switch input discovery** from `INDEX.json` to
  `iter_peep_files(include_kov=True)`. `INDEX.json` becomes a
  fallback-only metadata source for laws.
- **Remove the root-only filter** at line ~433.
- **Title/context source for KOV acts** — pull `rdfs:label` and
  `dc:source` directly from each KOV peep file's act node (these fields
  already exist from generation; no INDEX.json equivalent needed).
- **Output path** — use the source path, not `KRR_DIR / filename`,
  per the per-caller audit.
- KOV regulations are concept-rich (waste, transport, education,
  alcohol retail, public spaces) — once discovery is fixed, EuroVoc
  output grows substantially with high discoverability value.

### `extract_temporal_data`

This is **not code-free** either. The current extractor:

- line ~279: globs only `data/riigiteataja/*.xml` (non-recursive,
  laws-only). KOV source XML lives under
  `data/riigiteataja/maarus_kov/reg_<globalId>.xml`; state regulations
  are at `data/riigiteataja/maarus/reg_<globalId>.xml`.
- line ~312: explicitly filters `f.parent == KRR_DIR`, skipping all
  regulation files.
- pairs XML to laws by **law slug** (filename), but KOV JSON filenames
  are title/`terviktekst`-based and don't share a slug with their XML
  filenames.

Required changes:

- **Recursive XML discovery** — extend the glob to traverse
  `data/riigiteataja/`, `data/riigiteataja/maarus/`, and
  `data/riigiteataja/maarus_kov/`.
- **Build a `globaalID` → XML path lookup** during discovery, since
  every peep file already carries `estleg:globalId` and KOV XML
  filenames embed that ID (`reg_<globalId>.xml`). This replaces the
  fragile slug-based filename match for regulations.
- **Remove the root-only peep-file filter** at line ~312.
- Each KOV act already has `entryIntoForce` and `lastAmendmentDate`
  from generation, so the act-level fallback (when XML date fields
  are missing) just reads from the peep file itself rather than
  `INDEX.json`.
- The in-text date extraction logic is unchanged — only discovery and
  XML pairing need work.

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

This is **not code-free**. The current extractor (line ~254) filters
to root-only files and (line ~240) builds provision IRIs from the
**file slug** plus paragraph number rather than reading the actual
node `@id` from the JSON-LD graph. For KOV files this would either
skip them entirely or emit concept links to non-existent provision
IRIs, since KOV provisions use `Reg_<terviktekstId>_Par_<n>` IDs that
don't derive from filename slugs.

Required changes:

- **Switch input discovery** to `iter_peep_files(include_kov=True)`;
  remove the root-only filter.
- **Source provision IRIs from the actual node `@id`**, not from a
  derived `f"{law_slug}_Par_{n}"` pattern. Walk the `@graph` of the
  loaded file and use each node's own `@id` for the
  `definedIn`/`definesTerm` triple targets.
- **Output path** uses the source path, per the per-caller audit.

### `extract_court_provision_links`

This is **not code-free**. The current resolver maps citations to
provisions via **law abbreviations** (e.g. `KarS § 121`) and known
law names. KOV regulations have no abbreviation registry — court
decisions cite municipal acts by a different shape: RT IV reference,
title, municipality, date, and act number (e.g.
*Tallinna Linnavolikogu 18. juuni 2020 määruse nr 15 § 4*). Without a
KOV-specific resolver, this pipeline would run cleanly but produce
zero KOV→court links, defeating the integration.

Required changes:

- **Add a KOV citation resolver** that recognizes the
  municipality+date+number citation form and resolves it via the
  Issuer registry + act metadata (`actNumber`, `entryIntoForce`).
- **Add an RT IV reference resolver** that maps the
  `RT IV, YYYY-MM-DD, N` reference shape to the corresponding KOV
  act's `globaalID`. The mapping table is built once at start-up by
  scanning all KOV peep files for their `RT IV` source URLs.
- **File discovery** through `iter_peep_files(include_kov=True)`;
  remove any root-only filter.
- **Citations that don't resolve** are logged to the per-pipeline
  coverage report's failure samples, not silently dropped.

### `generate_amendment_history`

This is **not code-free**. The current script (lines ~124, ~377)
filters to root-only peep files and (line ~393) globs only
`data/riigiteataja/*.xml` — laws-only, non-recursive. KOV regulation
XML lives under `data/riigiteataja/maarus_kov/reg_<globalId>.xml`, so
this needs the same `globaalID`/`terviktekst` mapping as the temporal
extractor.

Required changes:

- **Recursive XML discovery** over `data/riigiteataja/`,
  `data/riigiteataja/maarus/`, and `data/riigiteataja/maarus_kov/`.
- **Pair XML to peep files via `globaalID`**, not by slug. KOV chain
  logic uses `terviktekstID` as the stable group key (multiple
  `globaalID` redactions map to one `terviktekstID`).
- **Remove the two root-only peep-file filters.**
- The chain-building algorithm itself is generic and unchanged.

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
- Output IDs are **act IRIs** (e.g. `estleg:Reg_1014955_Map`), not
  provision IRIs.
- Provision-level KOV similarity stays out of scope — defer to a future
  phase if a concrete need surfaces.

The existing law/state-regulation similarity index continues to operate
at provision-level — this is purely a new act-level index added
alongside it.

### Bucket strategy

Detect regulation-type bucket from title and structure. Run pairwise
within each bucket only.

**Type detection (keyword-based first pass).** This needs a real
title-frequency source as a precondition, because
`REGULATIONS_KOV_INDEX.json` today only carries aggregate counts and
the `byIssuer` breakdown — **it has no titles**. Before bucket
detection runs, generate a one-time `data/kov/title_frequency.json`
artefact:

1. Walk `krr_outputs/regulations/kov/**/*_peep.json` and read each act
   node's `estleg:titleNormalized` literal (added during Layer 1 — see
   below).
2. Compute frequency over normalized titles and save the top-200 to
   `data/kov/title_frequency.json`. The bucket detector reads this.

`estleg:titleNormalized` is a **Layer 1 deliverable**, not a Layer 3
runtime field. It is written onto every KOV act node during Layer 1
enrichment by a small normalization step:

- Lowercase, transliterate diacritics, strip the municipality-name
  prefix (using the `Issuer.displayName` token), strip leading/trailing
  whitespace.

Layer 1 acceptance criteria, SHACL, and tests cover it (see updates in
those sections). Putting normalization in Layer 1 keeps the bucket
pre-pass to a simple read-and-count over already-normalized data.

The expected top-50 patterns (verified once `title_frequency.json`
is generated):
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

Two output surfaces, both with explicit schemas:

**1. Per-bucket similarity reports** at
`krr_outputs/similarity/kov_by_type/<bucket>.json`. One JSON object
per file with the following shape:

```json
{
  "bucket": "jaatmehoolduseeskiri",
  "regulationCount": 359,
  "topK": 20,
  "generatedAt": "2026-05-02T12:34:56Z",
  "pipelineVersion": "<git-sha>",
  "pairs": [
    {
      "source": "estleg:Reg_1014955_Map",
      "peers": [
        {
          "target": "estleg:Reg_1023711_Map",
          "score": 0.873,
          "scoreModel": "tfidf-cosine"
        },
        ...
      ]
    },
    ...
  ]
}
```

`scoreModel` is fixed at `"tfidf-cosine"` for v1 and is recorded so a
later switch (e.g. to embeddings) is identifiable from the file alone.

**2. Act-level back-links written into each KOV peep file's act node**,
so consumers can navigate similarity from a single act without loading
the bucket file:

- `estleg:regulationTypeBucket` — the bucket label (`xsd:string`),
  e.g. `"jaatmehoolduseeskiri"` or `"_uncategorized"`.
- `estleg:similarAct` — repeated property pointing at peer acts. Each
  value is a reified `estleg:Similarity` object:
  - `estleg:similarTarget` — peer act IRI
  - `estleg:similarityScore` — `xsd:float`
  - `estleg:similarityModel` — `"tfidf-cosine"`

`estleg:Similarity` is a new class (sibling to `Citation`), declared
in the schema and validated by SHACL. Reified so the score+model stay
attached to the right peer (same JSON-LD ordering concern as
citations). Top-K bound enforced at write time.

These triples are **idempotent**: each Layer 3 rerun first clears any
existing `estleg:similarAct` and `estleg:regulationTypeBucket` from
KOV act nodes before rewriting. Same pattern as the existing
`referencedBy` clear-then-regenerate flow.

Estimated total pairs: ~200K vs naive 121M.

## Testing strategy

Extends the existing 39-test pytest suite.

### Layer 1 tests (~14)

- EHAK code list parses; 79 expected codes present
- Every issuer slug from `krr_outputs/regulations/kov/*` resolves to a
  `currentMunicipalityCode`
- Auto-mapping uses both name root and body type (linn vs vald);
  ambiguous case *Tartu Linnavolikogu* vs *Tartu Vallavolikogu* maps
  to two different municipalities, not one
- Auto-mapping fails (does not silently guess) when root + body-type
  is ambiguous; failure feeds the curated CSV
- Synthetic unmapped issuer raises a clear error
- Curated rows without `mapping_evidence` are rejected at load time
- SHACL: every Municipality has `ehakCode` + `county`; every Issuer
  has `currentMunicipality`, `bodyType`, and `mappingSource`
- SHACL: IRI patterns match `Municipality_EHAK_<4digit>` and
  `Issuer_<slug>`
- SHACL: every `KovProvision` has `enactedByMunicipality`,
  `enactedBy`, and `partOfAct`
- SHACL: every `MunicipalRegulation` act has `titleNormalized`
- SHACL: every existing law act node has both `owl:Ontology` and
  `estleg:Law` types after enrichment
- `titleNormalized` strips municipality prefix correctly: e.g.
  `"Tallinna jäätmehoolduseeskiri"` →
  `"jaatmehoolduseeskiri"` (lowercase, transliterated, prefix
  removed)
- `partOfAct` on a sample of 20 KOV provisions points to a real act
  IRI in the same file's `@graph`
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
  *alkoholiseaduse § 42 lg 1 alusel* produces `issuedUnder` (to
  the alcohol law act IRI) and one `implementsCitation` reified node
  on the act with `citationTarget` = paragraph-level IRI of `§ 42`,
  `citationDetail = "lg 1"`, and `citationText = "§ 42 lg 1 alusel"`
- Cross-reference: a preamble with two citations produces two
  separate `Citation` instances, each with its own target and detail
  — verify by serialise/round-trip that no detail/target mismatch
  occurs (the original ordering-pair concern)
- Cross-reference: preamble citation of *Vabariigi Valitsuse määrus
  nr 112* resolves to a `NationalRegulation` target, not silently
  dropped
- Cross-reference: preamble citation of a same-municipality
  *vallavolikogu määrus* resolves to a `MunicipalRegulation` target
  via the municipality-scoped resolver
- Cross-reference: vallavalitsus act citing a vallavolikogu act of
  the same municipality resolves correctly under the
  municipality-first scoping rules
- Cross-reference: act with no preamble emits no `issuedUnder` or
  `implementsCitation`
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

### Similarity tests (~10)

- `data/kov/title_frequency.json` is generated from the actual KOV
  corpus, has ≥200 entries, and survives a re-run unchanged
  (deterministic ordering)
- Output IDs in per-bucket similarity files are act IRIs
  (`estleg:Reg_<terviktekstId>_Map_<year>`), not provision IRIs
- Per-bucket file conforms to the documented JSON shape (top-level
  fields `bucket`, `regulationCount`, `topK`, `generatedAt`,
  `pipelineVersion`, `pairs`)
- Each `pairs` entry has `source` and a `peers` list; each peer has
  `target`, `score`, and `scoreModel = "tfidf-cosine"`
- Act-level back-links: every KOV act with similar peers has an
  `estleg:regulationTypeBucket` and at least one `estleg:similarAct`
  triple. Each `similarAct` value is a `Similarity` reified node
  carrying its own `similarTarget`, `similarityScore`, and
  `similarityModel`
- Idempotent rerun: clearing and regenerating produces identical
  output, no duplicate `similarAct` triples
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

- **Gate A — Layer 1.** `validate_all.py` clean. SHACL passes for all
  Layer 1 shapes (Municipality, Issuer, MunicipalRegulation,
  KovProvision, Law). All 357 issuers mapped using the body-type-aware
  auto-match plus curated CSV. Every KOV act has `enactedBy`,
  `enactedByMunicipality`, and `titleNormalized`. Every KOV provision
  has `enactedBy`, `enactedByMunicipality`, `partOfAct`, and
  `KovProvision` rdf:type. Every existing law act node has the new
  `estleg:Law` rdf:type. Ship as standalone PR.
- **Gate B — Layer 2.** All ten KOV-relevant pipelines (cross-ref,
  inverse, deontic, EuroVoc, temporal, sanctions, competence,
  legal-concepts, court-provision-links, amendment-history) run cleanly
  over the full KOV set. Per-pipeline coverage reports attached, with
  runtime metrics. **The per-caller audit (input discovery + output
  path handling + idempotency + per-script test) passes for every
  consumer** before the `iter_peep_files()` default flips.
  **`generate_similarity_index` pins `include_kov=False` at the call
  site in the same PR** — the iterator default cannot flip while
  similarity is still provision-level. Ship as second PR.
- **Gate C — Layer 3.** Bucket coverage ≥80% on full KOV set,
  similarity output size bounded, KOV→state-law similarity sample
  reviewed. Per-bucket output files conform to the documented JSON
  schema. Act-level `similarAct` and `regulationTypeBucket` triples
  written back into KOV peep files; SHACL `Similarity` shape passes.
  The Gate B `include_kov=False` pin on `generate_similarity_index`
  is removed and replaced with the new act-level KOV similarity
  generator gated by `include_kov_similarity=True`. Ship as third PR.

Each PR is independently revertible. Layer 1 alone provides immediate
query value (per-municipality, per-issuer queries become possible) —
Layer 2 and Layer 3 are upgrades on top.

## Documentation deltas

- `README.md` — KOV section in the data inventory; mention "first-class"
  status after Gate B
- `docs/SCHEMA_REFERENCE.md` — new classes and every new property
  introduced by this spec:
  - **New classes:** `Act` (parent), `Law` (subclass of Act),
    `Municipality`, `Issuer` (subclass of `Institution`),
    `KovProvision` (subclass of `LegalProvision`),
    `Citation`, `Similarity`. Existing `*Regulation` classes
    declared as subclasses of `Act`.
  - **Entity-layer properties:** `enactedBy`, `enactedByMunicipality`,
    `currentMunicipality`, `bodyType`, `ehakCode`, `county`,
    `historicalMunicipalityName`, `mappingSource`, `mappingEvidence`,
    `titleNormalized`, `partOfAct`
  - **Cross-reference / inverse properties:** `issuedUnder`,
    `implementsCitation`, `citationTarget`, `citationDetail`,
    `citationText`, `implementedBy`, `implementedByCount`
  - **Sanctions:** `enforcedAtLevel`
  - **Similarity:** `similarAct` (object-valued, points to a reified
    `Similarity` node), `similarTarget`, `similarityScore`,
    `similarityModel`, and the `regulationTypeBucket` annotation on
    each KOV act
  - **IRI patterns** documented for `Municipality_EHAK_<code>`,
    `Issuer_<slug>`, `Citation_<source>_<seq>`, and
    `Similarity_<source>_<peer>`
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
- Auto-match uses both name root and body type (linn vs vald);
  ambiguous cases fail rather than guess.
- Every Issuer carries `mappingSource`; every curated mapping carries
  non-empty `mappingEvidence`.
- New parent class hierarchy in place: `estleg:Act` declared, with
  `estleg:Law`, `estleg:NationalRegulation`, and
  `estleg:MunicipalRegulation` as subclasses. Every existing law act
  node carries the new `estleg:Law` rdf:type.
- Every KOV act has `enactedBy`, `enactedByMunicipality`, and
  `titleNormalized`. Every KOV provision is typed as `KovProvision`
  and has `enactedBy`, `enactedByMunicipality`, and `partOfAct`.
- The cross-reference extractor scans both provision text and act-level
  `preambleText`. Preamble references attach to the act node via
  `issuedUnder` (range: `Act`) and `implementsCitation` (range:
  `Citation`, with `citationTarget`, optional `citationDetail`, and
  optional `citationText` per reified node).
- Internal-act references use the **municipality-first** resolver
  (vallavalitsus → vallavolikogu within the same municipality is
  supported); global fallback only on a unique high-confidence match.
- The inverse generator emits both `implementedBy` (filtered projection
  from preamble enabling-act links, allowing all three target types)
  and `implementedByCount` (aggregate) on each enabling act and its
  provisions.
- `classify_eurovoc` and `extract_temporal_data` use
  `iter_peep_files()` for discovery; temporal extractor recurses
  `data/riigiteataja/maarus/` and `data/riigiteataja/maarus_kov/`
  and pairs XML to peep files via `globaalID`. Neither retains the
  root-only filter.
- Bucket detection reads `data/kov/title_frequency.json`, which is
  generated as a one-time pre-pass over all KOV peep files.
- `Issuer` is declared `rdfs:subClassOf estleg:Institution` so
  competence-extractor links target Issuers using existing
  Institution-range properties.
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
  output size. Per-bucket output files conform to the documented JSON
  schema. Each KOV act node also carries back-link triples
  (`regulationTypeBucket`, `similarAct` → reified `Similarity` nodes).
- Three documentation surfaces updated; CHANGELOG carries three entries.
