# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Namespace migration (#516)

- **Canonical IRI namespace changed to `https://w3id.org/estleg/`** (w3id.org
  PURL, slash form). EstLeg is an independent project and is **not affiliated
  with, endorsed by, or published by the Estonian government.** Earlier
  pre-release builds used `https://data.riik.ee/ontology/estleg#` — an
  official-looking namespace under a government-controlled host that was **never
  assigned to or controlled by this project.** That is an identifier-authority /
  provenance error (the IRIs implied official status, did not dereference, and
  could not be maintained), corrected here before v1.0.0. All generated JSON-LD,
  source constants, SHACL, tests, metadata, docs, examples, and
  `combined_ontology.jsonld` now use the new namespace (dataset/ontology IRIs
  too, e.g. `https://w3id.org/estleg/dataset/estonian-legal-ontology`). A CI
  guard blocks any `data.riik.ee` reference outside historical records. Old IRIs
  are **not** kept alive via `owl:sameAs` — the old host is uncontrolled and
  implies wrong provenance; a migration note is cleaner. Registering the
  `w3id.org/estleg/` redirect (`w3id/estleg/`) is a separate maintainer step;
  until it lands the namespace is a *planned* stable identifier. See
  `docs/NAMESPACE_MIGRATION.md`.

## [0.11.0] - 2026-06-22

### Versioning & governance (#616)

- The ontology now carries a **pinnable version**. `owl:versionInfo` (`0.11.0`)
  and `owl:versionIRI` are stamped on the `metadata.jsonld` dataset header and on
  a new dataset-level `owl:Ontology` node at `@graph[0]` of
  `combined_ontology.jsonld` — re-emitted every build from the single
  `estleg_common.ONTOLOGY_VERSION` constant, kept in lockstep with
  `pyproject.toml` by a drift-guard test. Consumers can finally pin/cite a
  release instead of an undated 245 MB blob.
- Added `CONTRIBUTING.md` (contribution flow + the full validation-gate
  checklist) with a **mandatory legal-correctness review** step for changes to
  safety-critical data — sanctions, deontic (`normativeType`), court decisions,
  and transposition/harmonisation — routed via a new `.github/CODEOWNERS`.
- Added `.github/` issue + pull-request templates (with a legal-data-impact
  flag) and a **Versioning Policy** section in `docs/RELEASE.md`.

### Cycle-2 technical-correctness pass (#560, label `review-2026-06-20`)

This release also folds in the cycle-2 correctness fixes merged as PRs
#620–#634, including: the combined-graph closure gate no longer exempts
`hasVersion`/`amendedBy` and the SHACL emptiness guard is restored (#589/#590);
the test suite now loads the real shipped artifacts (#591); fabricated
publication dates fixed (#571); sidecars + KOV merged into combined and the
README SPARQL examples answerable (#561/#615); point-in-time version intervals
regenerated non-overlapping (#574); `<sup>` markup and doubled summaries
normalised (#572/#613); deprecated VÕS/act-family references rejected in
cross-reference, transposition, and harmonisation resolution, with the
harmonisation data migrated onto the canonical acts (#573/#578/#631).

### Combined ontology is now a self-contained graph (#416)

- `generate_combined_jsonld()` no longer ships a laws-only aggregate. It now
  (1) fully merges the `sanctions/`, `institutions/`, `concepts/`, and
  `annotations/` enrichment overlays, and (2) mints lightweight leaf **stub
  nodes** (`estleg:isStubNode`, carrying `@type` + `rdfs:label` + an identifier
  + an external link) for every remaining cross-corpus reference — court (RK),
  EU, draft, regulation, amendment-link, and harmonisation IRIs whose full node
  lives in a sibling corpus. Loading `combined_ontology.jsonld` on its own now
  yields **zero dangling `estleg:` object IRIs** except the deliberately
  external `estleg:hasVersion` (provision_versions sidecar) and `estleg:amendedBy`
  (provisional draft amendments). Artifact grows ~178 MB → ~245 MB
  (165,620 law + 29,415 overlay + 33,111 stub nodes).
- The build now reads the git-LFS `annotations/oiguskantsler_seisukohad.jsonld`
  and `eurlex/eurlex_combined.jsonld` sources and fails fast if either is an
  un-materialised pointer (`_require_combined_build_sources`).
- New `validate_combined_graph_closure()` gate in `validate_all.py` asserts the
  closure invariant (zero dangling non-exempt refs; every stub referenced and a
  leaf) and so fails CI on a stale or incomplete combined. The existing parity
  gate now treats overlay nodes as canonical sources and stub nodes as expected
  extras. Shared constants/helpers (`COMBINED_OVERLAY_SUBDIRS`,
  `COMBINED_CLOSURE_EXEMPT_PREDICATES`, `STUB_NODE_MARKER`,
  `iter_combined_overlay_files`, `iter_node_estleg_refs`) live in `estleg_common`.
- `estleg:isStubNode` declared in `controlled_vocabulary.jsonld`. The four
  `avaliku_teabe_seadus` `implementedBy` targets flagged in the issue already
  resolve in the regulations corpus (KOV pipeline backfill); no data change.

### KOV integration Layer 2c PR #3 — Court-Provision Links (closes Gate B)

- `extract_court_provision_links.py` now resolves KOV act-level citations
  in Riigikohus court decision summaries to `MunicipalRegulation` IRIs,
  alongside the existing state-law provision-level resolution.
- `estleg:interpretsLaw` formal range widened to
  `owl:unionOf (LegalProvision Act)` so it admits `MunicipalRegulation`
  (Act) IRIs in addition to `LegalProvision`.
- `estleg:interpretedBy` usage broadens: the predicate now also appears
  on `MunicipalRegulation` act nodes (subject side); the object remains
  a `CourtDecision` IRI in all cases. No formal `rdfs:range` is declared
  for `interpretedBy` in this PR. SHACL `MunicipalRegulationShape` gains
  a parallel `interpretedBy` property shape (`sh:nodeKind sh:IRI`).
- New per-pipeline coverage report at
  `krr_outputs/reports/kov/court_provision_links_coverage.json`.
- New helpers in `estleg_common.py`: `BODY_CANON`, `normalize_issuer_name`,
  `_RunCounters`, `_safe_load`.
- Empirical impact: ~5 new `interpretsLaw` edges with KOV act IRI targets;
  ~29 unresolved historical citations surfaced in `failure_samples`.
- RT IV citation resolver deferred — counted only under
  `skip_reasons["rtiv_form_citation"]`.

**Gate B closes.** All ten KOV-relevant pipelines now run cleanly over
the full KOV set with per-pipeline coverage reports.

### Added
- KOV integration Layer 1: Municipality + Issuer entity model
  (`estleg:Municipality`, `estleg:Issuer`, `estleg:KovProvision`,
  `estleg:Act`, `estleg:Law` classes; 79 Municipality nodes; 357
  Issuer nodes; per-act `enactedBy`/`enactedByMunicipality`/
  `titleNormalized` enrichment; per-provision
  `KovProvision`/`enactedBy`/`enactedByMunicipality`/`partOfAct`
  enrichment; `estleg:Law` rdf:type stamped onto all existing law
  nodes). See `docs/superpowers/specs/2026-05-02-kov-integration-design.md`.

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

### Added (Layer 2c PR #2 — Institutional Competence)
- `extract_institutional_competence.py` runs the full corpus
  including KOV by default (single `include_kov=False` pin
  removed).
- `GENERIC_PATTERNS` gains two new regex entries for KOV body
  words: `linnavolikogu` / `vallavolikogu` / `alevivolikogu`
  (volikogu bodies) and `linnavalitsus` / `vallavalitsus`
  (executive bodies), in all common Estonian case suffixes
  including the short illative (`vallavalitsusse`).
- KOV-scoped `competentAuthority` Issuer-binding via a
  3-priority resolver: Path 1 (same-body source_issuer),
  Path 2 (paired-body slug swap with municipal-family
  compatibility), Path 3 (municipality-wide unique fallback
  for inconsistent Layer 1 mappings).
- `_canonical_body_slug` strips Estonian case inflections;
  `_swap_issuer_body_suffix` derives paired-body Issuer IRIs;
  `_resolve_kov_authority` orchestrates the 3-priority
  resolution; `_is_path3_case` predicate counts Path 3
  diagnostic firings.
- The Issuer registry stays canonical: KOV-bound
  `competentAuthority` IRIs come from the same registry Layer 1
  emits as `enactedBy` targets. NO parallel
  `institutions/institution_<body_slug>.json` files are written
  for KOV-bound resolutions.

### Changed (Layer 2c PR #2)
- `extract_institutional_competence.main()` returns `int`;
  `raise SystemExit(main())` propagates the gate's exit code.
- Per-file in-memory processing replaces the inline global
  pre-clear pass. The per-file flow detects pre-existing
  peep-side output BEFORE clearing and saves the file when
  EITHER fresh competence output exists OR pre-existing output
  existed (so cleared-but-empty files persist the cleared
  state to disk).
- Stale `krr_outputs/institutions/institution_<slug>.json`
  files are DELETED at end-of-run when no provisions cite the
  slug AND no per-peep load errors occurred. Without this,
  orphan `Institution_*` nodes survive pointing at provisions
  that no longer carry `competentAuthority`.
- Coverage instrumentation reuses
  `kov_pipeline_coverage.CoverageReport` (same schema as Layer 2a
  / Layer 2b / Layer 2c PR #1).
- SHACL: `LegalProvisionShape`'s existing `competentAuthority`
  constraint (`sh:nodeKind sh:IRI`) is unchanged structurally;
  only the `sh:description` is updated to mention the broader
  Issuer/Institution value range.

### Coverage (Layer 2c PR #2, 2026-05-05 corpus run — post-review fixes)
- `extract_institutional_competence`: 15,508 input files
  (11,059 KOV); 8,386 KOV files with output (76% of KOV acts);
  45,233 KOV competentAuthority references emitted (56,262 total
  including state-side); 1,232 body-word abstains; 0 Path 3
  fallback hits (Layer 1 issuer assignment is fully consistent
  with enactedByMunicipality); 0 errors; wall time 22.88s at
  677.69 items/s, 95.9 MB peak; GATE OK.
- **Review-driven re-run (after PR #101 review):** extended GENERIC_PATTERNS
  to cover adessive/ablative/elative/comitative case forms
  (e.g. `vallavalitsusel`, `linnavolikogult`, `vallavalitsusega`,
  `linnavolikogust`); added per-provision deduplication so a
  summary with multiple inflected mentions of the same body emits
  exactly one `competentAuthority` ref. Pre-fix corpus had 4,194
  provisions with duplicate refs; post-fix: 0. Coverage numbers
  above reflect the post-fix run. `triples_emitted_kov` dropped
  from 48,431 to 45,233 due to dedupe; `files_with_output_kov`
  rose from 8,302 to 8,386 as new case forms catch additional
  peeps.

### Internal (Layer 2c PR #2)
- New `tests/test_extract_institutional_competence.py` with
  30 tests across 7 classes (3 detection + 13 resolver +
  3 integration + 3 idempotency + 3 institution-file
  deletion + 4 coverage + 1 corpus-wide invariant marked
  `@pytest.mark.slow`).
- `_unresolved_count` and `_fallback_hits` counters track
  body-word abstains and successful Path 3 resolutions
  respectively. `fallback_hits` is a diagnostic signal: high
  values surface Layer 1 mapping inconsistencies for
  follow-up investigation. The 2026-05-04 corpus run reports
  zero fallback hits — Layer 1's enactedBy assignments are
  fully consistent with enactedByMunicipality scoping.
- 77 of 158 `(currentMunicipality, bodyType)` registry buckets
  are ambiguous (276 of 357 issuers — 77%, mostly post-2017
  haldusreform mergers). The source-issuer-based Path 1+2
  authoritative rule resolves these correctly using the act's
  own enactedBy identity rather than collapsing to a
  same-modern-municipality conflated issuer.

### Gate B umbrella status (after this PR)
- 2a COMPLETE (#98)
- 2b COMPLETE (#99)
- 2c PR #1 sanctions: COMPLETE (#100)
- 2c PR #2 institutional-competence: this PR
- 2c PR #3 court-provision-links: pending

Gate B closes when Layer 2c PR #3 lands.

### Added (Layer 2c PR #1 — Sanctions)
- `extract_sanctions.py` runs the full corpus including KOV by
  default (single `include_kov=False` pin removed).
- `estleg:enforcedAtLevel` populated on every provision that
  carries `estleg:hasSanction`. Value is `"municipality"` for
  provisions in `MunicipalRegulation` acts, `"state"` otherwise.
- SHACL: `LegalProvisionShape` gains a property constraint for
  `enforcedAtLevel` (`sh:datatype xsd:string`, `sh:maxCount 1`,
  `sh:in ("state" "municipality")`). Closes the Layer 2a gap
  where the property was declared in `metadata.jsonld` but not
  referenced in `shacl/estonian_legal_shapes.ttl`.

### Changed (Layer 2c PR #1)
- `extract_sanctions.main()` returns `int`; `raise SystemExit(main())`
  propagates the gate's exit code.
- Per-file in-memory processing replaces the global `_clear_existing_sanctions`
  pre-clear pass. Each peep is processed independently; the per-file
  flow detects pre-existing peep-side output BEFORE clearing and
  saves the file when EITHER fresh sanction output exists OR
  pre-existing output existed (so cleared-but-empty files persist
  the cleared state to disk).
- Stale `krr_outputs/sanctions/sanctions_<law>.json` files are
  DELETED when an act loses all its sanction matches between runs.
  Without this, orphan `Sanction_*` nodes survive pointing at
  provisions that no longer carry `hasSanction`.
- Coverage instrumentation reuses `kov_pipeline_coverage.CoverageReport`
  (same schema as the 5 Layer 2a pipelines + 2 Layer 2b pipelines).

### Coverage (Layer 2c PR #1, 2026-05-04 corpus run)
- `extract_sanctions`: 15,508 input files (11,059 KOV); 282 files with
  output (82 KOV = ~0.74% of KOV acts — sanction-bearing KOV regulations
  are a minority of the corpus); 2,067 sanction records (85 KOV);
  27.59 s wall-time at 562 items/s, 91 MB peak; GATE OK.

### Internal (Layer 2c PR #1)
- New `tests/test_extract_sanctions.py` with 15 tests across 5
  classes (4 unit + 4 integration + 4 idempotency + 2 coverage +
  1 corpus-wide invariant marked `@pytest.mark.slow`).
- `_classify_enforcement_level(act_node) -> str` and
  `_find_act_node(doc) -> dict | None` helpers added to the
  module.
- `triples_emitted` counts SANCTION RECORDS (not RDF triples) —
  matches the existing `total_sanction_count` semantics from
  `sanctions_report.json`. Documented inline so readers don't
  misinterpret across pipelines (cross-references counts actual
  triples; sanctions counts records — intentional).

### Gate B umbrella status (after this PR)
- 2a COMPLETE (#98)
- 2b COMPLETE (#99)
- 2c PR #1 sanctions: this PR
- 2c PR #2 institutional-competence: pending
- 2c PR #3 court-provision-links: pending

Gate B closes when all three Layer 2c sub-PRs land.

### Added (Layer 2b)
- KOV integration Layer 2b — preamble citations and filtered inverse
  projection populated for the full corpus (laws + state regulations +
  KOV acts).
- 13,618 preamble citations resolved (50% of 27,299 found) into
  `estleg:issuedUnder` (act-level) + reified `estleg:Citation` nodes
  (with `citationTarget`, `citationDetail`, `citationText`) on every
  parseable preamble. 23,618 preamble triples emitted (22,783 KOV).
- 882 target nodes carrying `estleg:implementedBy` across 604 files
  (21,256 total source-link edges), plus `estleg:implementedByCount`
  aggregates — filtered projection that draws strictly from preamble
  citations. Body-text references (`estleg:references`) continue to
  feed `estleg:referencedBy` (3,175 edges across 346 files, unchanged
  from pre-Layer-2b).
- KOV body-text act references resolved with `enactedByMunicipality`
  scoping; cross-municipality matches are explicitly skipped to prevent
  false positives between like-named issuers.
- 16 new entries in `KNOWN_ABBREVIATIONS` and 24 new entries in
  `FULLNAME_GENITIVE` covering the most-cited KOV enabling laws.

### Changed (Layer 2b)
- `extract_cross_references.py` and `generate_inverse_references.py`
  run the full corpus (laws + state regs + KOV) by default; all 3
  `include_kov=False` pins removed.
- `build_provision_index` now returns a 5-tuple — added
  `prefix_to_act_iri` and `act_iri_to_prefix` for act-level resolution.
  Callers must update their unpacking.
- `generate_inverse_references.collect_all_references` now indexes
  act-level nodes in `iri_to_file` (was provisions-only). Fixes a
  silent `referencedBy` drop on KOV body-text refs that target act
  IRIs.
- `apply_inverse_references` now returns `dict[Path, int]` (was
  `dict[str, int]` keyed by basename) so per-municipality KOV
  subdirectories disambiguate correctly.

### Internal (Layer 2b)
- `_normalize_issuer_label` transliterates Estonian diacritics (õ, ä,
  ö, ü, š, ž) so registry-side ASCII labels match act-side canonical
  forms.
- `clear_stale_implemented_by` idempotency pass — re-runs leave 0
  stale `implementedBy` edges, 0 symmetry mismatches.
- 11 corpus-derived preamble fixtures drive the parser tests.

### Coverage (Layer 2b, 2026-05-04 corpus run, post-fix)
- `extract_cross_references`: 15,508 input files (11,059 KOV); 9,648
  files with output (8,837 KOV = 80% of KOV acts); 32,413 triples
  emitted (24,020 KOV).
- `generate_inverse_references`: 921 files with output (511 KOV);
  4,057 triples emitted (512 KOV).
- The post-fix re-run recovered 1,918 chained-paragraph citations
  (resolved 11,700 → 13,618, +16%) that the initial parser missed in
  forms like `<law> § 6 lg 3 p 2 ja § 22 lg 1 p 34`.

### Known issues
- Pre-existing SHACL violations on `estleg:requestedCluster` (IRI-as-literal
  serialization, ~21.5k) and missing `estleg:summary` on some KOV/state-reg
  provisions (~523) surface when running full-corpus SHACL validation. These
  pre-date Layer 1 and will be addressed in a follow-up cleanup PR. Layer 1
  properties themselves have 0 SHACL violations.

## [0.10.0] - 2026-05-01

### Added
- **Domestic regulations (määrused)** — new ontology classes `estleg:NationalRegulation`, `estleg:GovernmentRegulation`, `estleg:MinisterialRegulation`, `estleg:MunicipalRegulation`, `estleg:Annex`
- `scripts/generate_regulations.py` — fetches state-level regulations from Riigi Teataja
- `scripts/riigiteataja_common.py` — shared API/XML helpers used by both law and regulation generators
- Pipeline-wide `estleg_common.iter_peep_files()` helper so integration scripts iterate laws + regulations from a single canonical source
- New properties: `estleg:documentType`, `estleg:issuer`, `estleg:actNumber`, `estleg:globalId`, `estleg:terviktekstId`, `estleg:isKov`, `estleg:preambleText`, `estleg:hasAnnex`, `estleg:annexNumber`
- ~3,820 state regulations under `krr_outputs/regulations/riik/`
- HTMLKonteiner fallback parser for pre-2010 regulations published as embedded HTML rather than structured XML
- 23 pytest tests covering metadata extraction, structured/HTML parsing, ID stability, and KOV opt-in
- SHACL shapes: `NationalRegulationShape`, `GovernmentRegulationShape`, `MinisterialRegulationShape`, `MunicipalRegulationShape`, `AnnexShape`

### Changed
- 13 integration scripts (cross-references, EuroVoc, deontic, sanctions, etc.) now iterate via `iter_peep_files()` and automatically include state regulations
- README, API_GUIDE, and SCHEMA_REFERENCE updated to cover the new domestic-regulation layer
- `validate_all.py` recognises `REGULATIONS_*_INDEX.json` index files and the new multi-valued `estleg:hasAnnex` property

### Notes
- KOV (municipal) regulations are intentionally not generated by default — opt in via `python scripts/generate_regulations.py --kov`
- Historical redactions are out of scope for Phase 1; the snapshot is `kehtiv=2026-05-01`

## [0.9.0] - 2026-03-21

### Added
- 14 integration scripts for cross-linking and analysis
- Cross-law reference extraction (17,505 citations resolved)
- Bidirectional reference links (8,572 inverse links)
- Court decision → specific provision IRI links (1,263 resolved)
- EuroVoc subject taxonomy classification (615 laws, 44 domains)
- Deontic classification: obligations, rights, permissions, prohibitions (8,322 provisions)
- Institutional competence mapping (102 institutions, 4,165 provisions)
- Penalty and sanction cross-reference index (339 sanctions)
- Temporal validity data: entry-into-force and repeal dates (649 laws)
- Amendment history chains (16,062 amendment references)
- Legal concept cross-reference graph with SKOS linking
- Draft legislation provision-level impact analysis (2,770 links)
- Semantic similarity index (10,832 cross-law pairs)
- EU directive transposition mapping (schema ready)
- Cross-border harmonisation links (schema ready)
- New SHACL shapes for Sanction, Institution, NormativeType, AmendmentEvent
- New output directories: concepts/, institutions/, sanctions/, amendments/
- Master orchestration script: run_all_integration.py
- Shared utilities module: estleg_common.py

### Changed
- Updated SHACL shapes with 15+ new property shapes
- Updated SCHEMA_REFERENCE.md with all new classes and properties
- Updated README.md with integration features section
- Extended validate_all.py with new multi-valued properties

## [0.8.1] - 2026-03-07

### Changed
- Refreshed EIS draft legislation data with latest entries (total: 22,832 drafts)
- Updated README and docs with corrected draft counts

## [0.8.0] - 2026-03-03

### Added
- **EU Court Decisions (CURIA)** — new ontology class `estleg:EUCourtDecision`
- New classes: `estleg:EUCourtDecisionType`, `estleg:EUCourt`
- Decision types: Judgment, Order, AGOpinion, CourtOpinion
- EU courts: Court of Justice, General Court, Civil Service Tribunal
- Properties: `ecliIdentifier`, `euCaseNumber`, `euCourtDecisionType`, `euCourt`, `curiaLink`, `documentDate`
- `scripts/generate_eu_court_decisions.py` — fetches all CJEU decisions from EUR-Lex SPARQL
- `krr_outputs/curia/` directory with schema, per-type files, combined file, and index
- 22,290 EU court decisions in Estonian (5,641 judgments, 6,619 orders, 9,952 AG opinions)
- SHACL shapes for EUCourtDecision, EUCourtDecisionType, EUCourt

### Changed
- Updated all documentation with EU court decisions coverage and data sources

## [0.7.0] - 2026-03-03

### Added
- **EU Legislation (EUR-Lex)** — new ontology class `estleg:EULegislation`
- New classes: `estleg:EUDocumentType`, `estleg:EUInstitution`
- Document types: Regulation, Directive, Decision
- EU institutions: European Commission, Council, Parliament, ECB, and DGs
- Properties: `celexNumber`, `euDocumentType`, `eurLexLink`, `eliIdentifier`, `documentDate`, `inForce`, `euInstitution`
- `scripts/generate_eu_legislation.py` — fetches all EU acts from EUR-Lex SPARQL endpoint
- `krr_outputs/eurlex/` directory with schema, per-type files, combined file, and index
- 33,242 EU legal acts in Estonian (18,784 regulations, 3,114 directives, 11,344 decisions)
- SPARQL examples for EU legislation queries in SCHEMA_REFERENCE

### Changed
- Updated README.md with EU legislation section, data sources, API details
- Updated SCHEMA_REFERENCE.md with EU legislation schema documentation
- Updated docs/README.md with EU legislation coverage
- Updated validation script to exclude EURLEX_INDEX from validation

## [0.6.0] - 2026-03-03

### Added
- **Supreme Court Decisions (Riigikohtu lahendid)** — new ontology class `estleg:CourtDecision`
- New classes: `estleg:CaseType`, `estleg:DecisionType`
- Case types: Criminal, Civil, Administrative, Misdemeanor, ConstitutionalReview
- Decision types: Judgment, Ruling, Resolution
- Properties: `caseNumber`, `caseType`, `decisionType`, `decisionDate`, `decisionLink`, `rikObjectId`, `referencedLaw`, `interpretsLaw`
- `scripts/generate_court_decisions.py` — fetches all decisions from RIK (rikos.rik.ee)
- `krr_outputs/riigikohus/` directory with schema, per-year files (1993-2026), and index
- 12,137 Supreme Court decisions across 34 years
- SHACL shapes for CourtDecision, CaseType, DecisionType
- Data sources documentation in README and SCHEMA_REFERENCE

### Changed
- Updated README.md with court decisions section, data sources table, and API details
- Updated SCHEMA_REFERENCE.md with court decision schema, SPARQL examples
- Updated validation script to cover riigikohus/ subdirectory
- Updated docs/README.md with court decisions coverage

## [0.5.0] - 2026-03-03

### Added
- **Draft Legislation (eelnõud)** — new ontology class `estleg:DraftLegislation` for laws in the legislative pipeline
- New classes: `estleg:LegislativePhase`, `estleg:DraftType`
- Legislative phases: PublicConsultation, Review, Submission
- Draft types: Bill, AmendmentBill, GovernmentRegulation, MinisterialRegulation, GovernmentOrder, EUPosition, DraftIntent, ActionPlan, Other
- Properties: `eisNumber`, `eisLink`, `initiator`, `publicationDate`, `affectedLawName`, `amendsLaw`, `legislativePhase`, `draftType`, `phaseOrder`
- `scripts/generate_draft_legislation.py` — fetches all drafts from EIS RSS feeds
- `krr_outputs/eelnoud/` directory with schema, phase files, combined file, and index
- 22,810 draft legislation entries from EIS (Eelnõude infosüsteem)
- SHACL shapes for DraftLegislation, LegislativePhase, DraftType
- Updated schema reference with draft legislation documentation and SPARQL examples

### Changed
- Updated README.md with draft legislation section and updated stats
- Updated SCHEMA_REFERENCE.md with complete draft legislation documentation

## [0.4.0] - 2026-03-03

### Added
- **Mass law generation** — automated generation of 563 new law files from Riigi Teataja API via `scripts/generate_all_laws.py`
- `scripts/generate_missing_parts.py` — fills in missing parts/chapters for partially mapped laws
- Total coverage expanded from 52 manually mapped laws to 615+ enacted laws (29,800+ provision nodes)

## [0.3.0] - 2026-03-02

### Fixed
- Migrated all files from `example.org` placeholder namespace to production `https://data.riik.ee/ontology/estleg#`
- Normalized `@type` to always use arrays across all 98 JSON-LD files
- Normalized multi-valued properties (`coversConcept`, `hasSection`, etc.) to always use arrays
- Normalized `dc:source` to consistent string type
- Normalized `estleg:sectionNumber` to consistent string type
- Fixed Asjaõigusseadus (AÕS) file naming — renamed `asjaigusseadus_*` (typo) to `asjaoigusseadus_*`
- Fixed `notari_seadus_peep.json` → `notariaadiseadus_peep.json` naming mismatch
- Fixed intra-file duplicate `@id` value (`estleg:AOS_Par_260`) in AÕS consolidated file
- Fixed namespace in `generate_kars_eriosa_jsonld.py` script

### Added
- `.gitignore` file for Python/IDE/OS artifacts
- `LICENSE` file (MIT)
- `CHANGELOG.md`
- `krr_outputs/INDEX.json` — master registry mapping law names to output files
- `krr_outputs/combined_ontology.jsonld` — unified file for graph database loading (2973 nodes)
- `docs/DUPLICATE_IDS_REPORT.md` — audit of cross-file @id collisions
- `shacl/estonian_legal_shapes.ttl` — SHACL validation shapes
- `.github/workflows/validate.yml` — CI pipeline for automated validation
- `scripts/validate_all.py` — comprehensive validation script
- Consolidated review request files into `reviews/` directory

### Changed
- Expanded root `README.md` with project description, quick-start, and coverage table
- Updated `docs/VALIDATION_REPORT.md` to reflect current file count (97+ files)
- Updated `docs/README.md` namespace references

## [0.2.0] - 2026-03-01

### Added
- Schema unification across all mapped laws
- KarS Eriosa automated generation script
- Marta review request files for 25 laws

## [0.1.0] - 2026-02-28

### Added
- Initial mapping of 44 Estonian laws to JSON-LD
- Core ontology schema (LegalProvision, TopicCluster, LegalConcept)
- Documentation (API Guide, Schema Reference, Validation Report)
