# Ontology Improvement Plan (2026-06-10 full review)

**Source:** 11-agent full review of the ontology, structure, and architecture, executed 2026-06-10.
Review scopes: T-Box, law instance corpus, court/EU corpora, temporal model, enrichment layers,
SHACL/validators, generation pipeline, enrichment pipeline, repo/distribution, external standards
research (ELI/ECLI/CELLAR/ELI-DL/Akoma Ntoso/LegalRuleML), hands-on usability testing, and a
test/lint/validation baseline. Every finding below was verified empirically against the repo state
at commit `c81a8a164a` (main).

**Tracking:** all work items are GitHub issues. Ten epics (#405–#414) group 67 new tickets
(#415–#481) plus pre-existing issues. Filter: label `review-2026-06`.

---

## 1. Verdict

The corpus is large, well-engineered operationally, and fully green at baseline (ruff 0 violations;
pytest 2,399 passed / 2 skipped; `validate_all.py` PASS with one pre-existing amendment-bloat
warning; `validate_seadusloome_sync.py` PASS in 10m42s; sampled SHACL buckets PASS). The
orchestrator DAG, atomic-write commons, resume ledger, and count-sync validators are
production-grade. The provision-version layer is excellent: 124,901 versions across 38,512 chains,
zero interval gaps/overlaps, working point-in-time queries for 742 laws.

But **engineering rigor exceeds ontological rigor**. The system validates *form* exhaustively while
several layers are semantically wrong (fabricated EuroVoc IRIs, mis-attached similarity scores,
draft amendments asserted as effected), identity is unstable by construction (`_Map` IRIs,
duplicate statutes, `_DupN` patches), the act⇄provision graph is disconnected, the T-Box is
fragmented across 7+ files, and the project has never been released (no tag, no DOI, no endpoint;
consumption = 2.4 GB clone). The corpus reads like a per-law topic-mapping project that grew into a
knowledge graph without revisiting its founding modeling decisions.

## 2. What is working well (preserve; do not "simplify" away)

- `krr_outputs/provision_versions/` — delta-encoded, RT-`terviktekstID`-anchored version chains;
  the most consumer-ready layer.
- Materialized inverse links (`referencedBy`, `interpretedBy`, `transposedBy`, `hasVersion`) —
  answer cross-corpus questions from 2 file loads instead of 1,190.
- Riigikohus→provision linking: 82,508 provision-level `interpretsLaw` edges; 100% caseType
  coverage after the #342 backfill.
- Cross-reference extraction (full Estonian citation grammar incl. superscripts, ranges,
  genitives, preambles; unresolved citations counted, not dropped) and the sanctions layer
  (structured min/max amounts; spot-checks correct against KarS §113/§199/§424).
- `run_all_integration.py` (DAG validation, parallel-write refusal, snapshot/restore),
  the `--regen-state` ledger, `OPERATIONAL_STATE_FILES` anti-drift enumerator, atomic
  `estleg_common.save_json`.
- All README Quick Start blocks run verbatim; the 179 MB combined graph loads in ~23 s / ~2 GB RAM.

## 3. Findings by theme (condensed; full evidence lives in the epic/ticket bodies)

### 3.1 Actively wrong data — Epic #405
- **EuroVoc IRIs are fabricated** (#421): `classify_eurovoc.py` mints `http://eurovoc.europa.eu/{code}`
  from a 43-entry hand table whose codes mean different things in real EuroVoc (2446 = "food
  policy", used here as "Constitutional law" on 9,167 acts). ~37k wrong public `dcterms:subject`
  triples.
- Similarity scores attach to the *target node*, not the pair; 1,281 nodes already carry
  contradictory scores (#422).
- 786 draft-based AmendmentEvents assert never-enacted amendments as effected, with
  `isCurrentAmendment: true` (#423).
- `MunicipalRegulation rdfs:subClassOf NationalRegulation` — all 11,059 municipal regulations are
  state regulations under RDFS entailment (#424).
- `estleg:harmonisedWith` used in both directions across the 158 harm files (#425).
- ~169 legacy duplicate statute files (toolepinguseadus vs toolepingu_seadus, põhiseadus ×2,
  äriseadustik ×2, alkoholi ×2) give contradictory answers (#426); Constitution case-law is split
  across two IRI families, 1,370 vs 2,949 edges (#427).

### 3.2 Graph connectivity — Epic #406
- Act roots have **zero inbound links** from their own provisions; membership exists only as the
  string `estleg:sourceAct` (#415) — the single highest-value/lowest-cost fix in the corpus.
- `combined_ontology.jsonld` is laws-only: ~84k dangling court/EU/draft references; sanction/
  institution/concept/annotation overlays unmerged (#416); `eurlex_combined.jsonld` lacks the whole
  transposition enrichment (#417).
- CURIA: 22,290 decisions, zero outbound edges (#418). Drafts never link to the act they became
  (#419). 926 prefix-less relative IRIs on `requestedCluster` parse into `file:///` junk (#420).

### 3.3 Identity & IRIs — Epic #407
- `_Map` year-suffixed act IRIs minted by f-strings in 10+ scripts, reverse-parsed by 5
  independent regexes — no central mint/parse function (#444); four naming generations, order-
  dependent collision counters (`KARIST` = Criminal Records Act, `KARIST_2` = Penal Code), § ranges
  embedded in multipart root IRIs (#445).
- Every act `owl:sameAs` a dated Riigi Teataja **XML file**; all 8 AÕS parts sameAs the *same*
  file ⇒ OWL merges them into one individual (#447).
- 584 `_DupN` IRIs patched post-hoc instead of fixed at the source (#448); four diverged
  `sanitize_id` implementations produce different IRIs from identical input (#449).

### 3.4 T-Box — Epic #408
- "Canonical" `controlled_vocabulary.jsonld`: 0/126 properties with `rdfs:domain`, zero subclass
  axioms, 55% placeholder comments, 61 ABox placeholders inside the vocab; the only class
  hierarchy hides in a `metadata.jsonld` **named graph** that default-graph parsers drop (#433,
  executes #291/#337).
- ~16,000 mechanically-minted per-file classes (#434); 1,190 act roots typed `owl:Ontology`
  (OWL Full) (#435); `Chapter owl:sameAs TopicCluster` conflates structure with subject (#436);
  zero language tags (#437); hand-built KarS/TsÜS modules misaligned (#438); no disjointness or
  functional axioms anywhere (#439).

### 3.5 Temporal — Epic #409
- `temporalStatus` = "unknown" on 1,184/1,186 law roots — the extractor only ever paired with
  regulation XMLs; repealed laws unmarked (#428).
- Three contradictory amendment representations; nothing links AmendmentEvents to the
  ProvisionVersions they produced; `lastAmendmentDate` faithfully stale (KarS: 2014 vs versions
  through 2026) (#429).
- 394 laws reported "no peep" by the version generator mostly *do* have peeps — pairing bug
  (#430); regulations have no version layer (#431); `estleg:kehtiv` (uniformly 2026-05-24) is
  undocumented snapshot semantics with a second, different anchor date in the temporal report
  (#432). Boundary-date double-match remains tracked in pre-existing #306.

### 3.6 Standards — Epic #410
- **Estonia is not in the EUR-Lex ELI register** — minting our own IRIs is correct; align the
  *model* to ELI 1.5 instead (subclass/subproperty bridges, `eli:id_local`, `eli:in_force`) (#440).
- CURIA should `owl:sameAs` the live Publications Office PSIs (`…/resource/ecli/{ECLI}`,
  `…/resource/celex/{CELEX}`) (#441). Riigikohus ECLI (0% coverage) requires the rikos.rik.ee
  scrape — non-derivable control number; fix the `ecliIdentifier` domain first (#442, executes the
  ECLI item of #392).
- ELI-DL v3 fits the EIS drafts corpus (#443); LegalRuleML deontic metamodel matches + `eli:is_about`
  for EuroVoc (#446). Akoma Ntoso: skip (no full-text markup need; RT XML is not AKN).

### 3.7 Enrichment & provenance — Epic #411
- **Zero PROV** anywhere; scraped facts and keyword guesses carry equal weight; method/thresholds
  live only in sidecar JSON (#456).
- Institutions: inflected labels, minister≠ministry, merged-agency conflation, 4 dangling aliases,
  no Wikidata/registry identity (#457). Concepts: 126 "kehtetu" noise terms, no ConceptScheme
  (#458). Annotations: full text duplicated up to 40× (#459). targetGroup: bare strings, citizen
  on 56% (#460). Deontic: definition provisions misclassified (#461). KOV similarity (107,549
  pairs) and the sanction severity index exist only as plain JSON (#462).
- Strategic: enrichment mutates the source-of-record peeps in place; move heuristic layers to
  overlay graphs (#463).

### 3.8 Validation — Epic #412
- `LegalProvisionShape` targeting is tautological (`sh:targetSubjectsOf estleg:paragrahv` —
  provisions missing `paragrahv` escape everything; proven with synthetic pyshacl) (#450).
- `temporalStatus` accepts any string; `ActTemporalShape` requires nothing (#451).
- Three divergent file-enumeration implementations (#452) — the `_schema` divergence is the real
  cause of the `graphClosureExempt` hack (retire it via pre-existing #270); two independent
  closure checkers + double parsing in the 10m42s sync gate (#453); no `_DupN` baseline guard and
  a 13×-accumulating report (#454); the two SHACL gates disagree on inference and severity (#455).

### 3.9 Pipeline — Epic #413
- Three corpus generators shadow the atomic `save_json` with non-atomic copies (#464); `@context`
  pasted in 23 scripts, already drifted (#465); `generate_all_laws.py` ignores
  `riigiteataja_common` (still carries the cache bug #296 fixed) and duplicates ~400 lines across
  its two emission paths (#466); `fix_all_issues.py` mixes the 2 real release builders with 8
  obsolete repair passes (#467); spent migrations linger in `scripts/` (#468); **no schema-drift
  canary** — an RT format change would silently degrade the corpus to stubs (#469); residual
  `date.today()` default + no standalone precondition guards (#470); 14 root-level report
  sidecars force exclusion-list gymnastics (#471); `py-modules = []` / sys.path hacks → `src/`
  layout migration (#472).

### 3.10 Product — Epic #414
- **No release has ever been cut** (empty `gh release list`, no DOI, no endpoint) (#473); no
  one-command load — ship a named-graph N-Quads dump + docker-compose SPARQL endpoint (#474); no
  ARCHITECTURE.md (#475); no vocabulary cheat-sheet (usability testing lost ~30% of time to
  predicate guessing; README's draft example uses a 57.5 s literal join where `amendsLaw` is
  instant) (#476); `docs/README.md` says 615 laws vs root README 1,145, four different file
  totals circulate (#477); agent-process scaffolding committed to the public repo (#478); CI
  never runs a generator and has no scheduled drift check (#479); 2.4 GB repo / code-data
  separation decision (#480); Dependabot/CodeQL absent (#481).

## 4. Roadmap

### Tier 0 — stop publishing wrong data (do first; independent, small-to-medium)

- #421 (EuroVoc remap, P0) · #422 (similarity scores) · #423 (draft amendments)
- #424 (DomesticRegulation) · #425 (harmonisedWith) · #426 (duplicate statutes)
- #427 (Constitution IRI split; after #426)

### Tier 1 — make the graph a graph

- #415 (act⇄provision links) · #444 → #445 → #447/#448 (identity, in that order)
- #416 + #417 (combined completeness + parity gate)
- #270 + #452 (enum load surface + one enumeration module) · #450 (shape tautology)

### Tier 2 — make it logical

- #433 → #434/#435/#436/#439 (T-Box) · #428 → #429/#430 (temporal)
- #440/#441/#442 (standards) · #456/#457 (provenance + institutions)
- #451/#453/#454/#455 (validation) · #437/#438 · #458–#462

### Tier 3 — make it a product

- #473 (v1.0.0 + DOI; after #416) · #474 (dump + endpoint) · #475/#476/#477/#478 (docs)
- #479 (CI) · #463 (overlay architecture) · #464–#472 (pipeline health; #464/#465 are quick
  wins that can land any time) · #480/#481 · #431/#443/#446/#448/#449 remainder

### Key dependencies
- #421 before #446 (eli:is_about should point at *correct* EuroVoc IRIs).
- #444 before #445; #445 coordinates with #426/#427 (one IRI migration window).
- #433 before #439/#440/#443/#446 (bridges need a canonical T-Box to live in).
- #434 + #450 together (class-targeted shapes become exact once per-file classes go).
- #416 before #473 (don't ship a laws-only "combined" as v1.0.0).
- #463 (overlay architecture) is the strategic umbrella for #456 and reduces future regen churn;
  pilot one layer (EuroVoc, post-#421) before committing.

## 5. Baseline recorded 2026-06-10

| Gate | Result |
|---|---|
| ruff check scripts tests | 0 violations |
| pytest -q | 2,399 passed, 2 skipped (98 rdflib ConjunctiveGraph deprecation warnings) |
| validate_all.py | PASS — 23,070 files, 0 errors, 1 warning (amendment duplicate bloat, #384) |
| validate_seadusloome_sync.py | PASS — 7,776,300 data triples, zero warnings; 10m42s wall |
| shacl_validate_all.py --bucket drafts / curia | PASS / PASS |
| git status after all runs | clean |

## 6. Appendix — validation of pre-existing open issues

A review-time validation pass over all pre-existing open issues (#270–#404) is recorded in
`docs/OPEN_ISSUE_VALIDATION_2026-06.md` (generated together with this plan): per-issue verdict
(still valid / already fixed / partially fixed / superseded), evidence, and epic mapping.
