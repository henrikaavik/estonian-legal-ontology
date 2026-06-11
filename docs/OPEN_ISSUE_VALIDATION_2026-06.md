# Open-issue validation audit — 2026-06-10/11

Companion to `docs/ONTOLOGY_IMPROVEMENT_PLAN.md`. All 102 issues open at the start of the
2026-06-10 full review (#270–#404) were re-validated against the repo at `c81a8a164a`:
each issue's core claim was spot-verified (in-code issue-number comments, targeted greps,
artifact checks). Verdicts:

- **FIXED** — no longer reproduces; evidence cited; closed with a comment referencing this audit.
- **PARTIAL** — part of the ask landed (usually: generator/code fixed, committed data awaiting
  regen); left open with a scope comment.
- **VALID** — still reproduces unchanged; left open, mapped to a 2026-06 epic.
- **SUPERSEDED** — fully contained in a new ticket; closed in its favor.

**Totals: 58 FIXED · 30 PARTIAL · 12 VALID · 2 SUPERSEDED.**
(Corrected 2026-06-11: #344 re-graded VALID→PARTIAL — its `transpositionDeadline` claim was
stale; the #356 XXE site count corrected 4→5; and #364 mapped to epic #406.)

## Headline patterns

1. **Fixes land without closing their issues.** 58 open issues were already resolved, almost all
   carrying in-code comments citing the issue number (e.g. `validate_all.py` "Issue #314") plus
   regenerated artifacts. Process fix: PRs should say `Fixes #N` so merges close issues, and the
   hourly review loop should verify open issues against code before re-reporting.
2. **The dominant PARTIAL cause is regen lag, not missing code.** The 2026-06-03 "full enrichment
   regen" (PR #402) did not cover several artifact families; these carry fixed generators but
   stale committed data: law peeps (#298, #368, #371, #375), regulations corpus (#374),
   `annotations/` last regen 05-30 (#379), `provision_versions/` 05-27 (#306, #393),
   curia (#343, #355, #383, #392), eurlex document types (#394), `eelnoud_combined` (#304, #380).
   This is exactly the staleness-gate theme of epics #406/#414 (parity checks #416, release
   cadence #473).
3. **One regression worth attention:** #353's guard landed *before* the 06-03 regen yet the same
   6 impossible date pairs are still emitted — the fix is ineffective for those records; fold the
   root-cause into #429.

## Verdict table

| # | Verdict | Epic | Evidence / remaining scope |
|---|---------|------|----------------------------|
| 270 | VALID | #412 | Enum individuals still only in `riigikohus_schema.json`; CV has only `CaseType_Other`; `_schema` still excluded from the public surface; `graphClosureExempt` still in shapes. Formal child of epic #412. |
| 291 | SUPERSEDED | #408 | Fully executed by #433 (ticket self-describes "executes #291/#337"). |
| 292 | FIXED | — | Directionality documented as by-design (`generate_similarity_index.py:35-45`); `bucket_key` de-inflection fixed + regression test. |
| 296 | PARTIAL | #413 | 7/10 items fixed (version-IRI guards, `_strip_offset`, EFTA/CELLAR dates, ReDoS bound, cache threshold, dup-id tests); 2 documented by-design. Remaining: draft `@id` fallback `title[:40]` vs dedup `title[:60]` mismatch (`generate_draft_legislation.py:458/:523`). |
| 298 | PARTIAL | #413 | Generator fixed (+tests); corpus stale — KarS osa1: 56/71 sampled multi-lõige summaries still doubled. Regen pending; relates #466/#445. |
| 299 | FIXED | — | Multi-osa list-valued prefix maps throughout `extract_cross_references` (+#403 follow-up); KarS osa1 now carries references/referencedBy. |
| 300 | VALID | #406 | `build_provision_index` still lacks a LegalPart fallback for legacy VOS files (0 `interpretedBy` in osa5/osa8). Relates #426. |
| 301 | FIXED | — | `karistatakse` fallback bounded (`\bkuni` + sentence bound); corpus max imprisonment now 20 y. |
| 302 | FIXED | — | "§ §" double-prefix gone (0 sanction files; was 290). |
| 303 | FIXED | — | Definition contamination 73.6% → 0 in `concepts_combined.jsonld`. |
| 304 | PARTIAL | — | Classifier fixed (kavatsus checked before seadus); `eelnoud_combined` stale: 54 kavatsus nodes still `DraftType_Bill`. Regen pending. |
| 305 | FIXED | — | `initiator` plain `xsd:string` in code, SHACL, and data (0 lang-tagged / 21,781 plain). |
| 306 | PARTIAL | #409 | Generator now emits day-before `versionValidTo`; committed sidecars stale (e.g. 92/92 shared transition dates) and the doc recipe is still inclusive. Relates #432. |
| 307 | FIXED | — | `issuers.json` regenerated (0/158 spurious historical names) + round-trip CI guard test. |
| 308 | FIXED | — | `dc` → elements/1.1 in code, committed artifact, and regression test. Relates #438. |
| 309 | PARTIAL | #407 | Generator emits stable `Osa{N}` IRIs; committed data still range-encoded. Migration subsumed by #445. |
| 310 | FIXED | — | `data/**` present in both CI path filters (`validate.yml:12/:25`). |
| 311 | VALID | #412 | `verify_layer1.check_laws/check_state_regulations` still untested and absent from CI. Relates #454/#455. |
| 312 | FIXED | — | Provisions keyed by their own prefix + regression test. |
| 313 | FIXED | — | `validate_subcorpus_index_counts` added, wired into main, tested. |
| 314 | FIXED | — | Per-entry regulation-index existence check (`validate_all.py:803-818`, cites #314). |
| 315 | FIXED | — | Missing INDEX/metadata now hard errors unless `--allow-missing-index`. |
| 316 | FIXED | — | Zero-file discovery now `error` + `sys.exit(1)` (`validate_all.py:2229-2240`). |
| 317 | FIXED | — | `_BUCKET_STEM_MAP` de-inflection in `bucket_key` + tests. Relates #462. |
| 318 | FIXED | — | SPARQL `lang(?title_nat)` filter added to the transposition fetch. |
| 319 | VALID | #406 | Forward-only transposition edge still latent (inverse guarded on None, forward unconditional). Distinct from #425. |
| 320 | FIXED | — | Hyphenated/numeral-word imprisonment patterns (cites #320/#372). |
| 321 | FIXED | — | Minister oblique forms + `_collapse_minister_stem`. |
| 322 | FIXED | — | Highest-specificity competence type wins (licensing > supervision). |
| 323 | FIXED | — | Copula intro markers rejected in `is_noise_term`; 0 copula-glued concepts. |
| 324 | FIXED | — | `_strip_letter_header` applied first in PDF normalization. Relates #459. |
| 325 | FIXED | — | Concept prefLabel/definition now `@et`-tagged (10,004 tagged, 0 plain). Relates #437. |
| 326 | FIXED | — | LegalConcept→Concept `skos:exactMatch` arc removed (0 in artifact). Relates #458. |
| 327 | FIXED | — | `estleg:amends` now lists every osa IRI. Relates #429/#445. |
| 328 | FIXED | — | Same-day redaction boundary now inclusive (`_date_before(..., inclusive=True)`). Relates #430. |
| 329 | FIXED | — | Preceding-infinitive window scan handles SOV order. |
| 330 | FIXED | — | `kool` inflection enumeration excludes `koolitus*`. |
| 331 | FIXED | — | `0411` min-distinct-keywords gate (comment covers #331+#360). |
| 332 | FIXED | — | `fix_duplicate_ids` spans regulations via `iter_peep_files()`. Relates #448. |
| 333 | VALID | #411 | Dead coverage WARN still mathematically unreachable (`enrich_kov_layer1.py:616-640`). Relates #463. |
| 334 | FIXED | — | Harmonisation aggregates list all transposing laws (verified `harm_32000L0060.json`). |
| 335 | PARTIAL | #405 | Schema/emitted domain now `estleg:Act` (+tests); `SCHEMA_REFERENCE.md:680` still says LegalProvision. Relates #425. |
| 336 | FIXED | — | `EUDecType_Other` properly typed `EUCourtDecisionType` in CV; placeholder typing gone. |
| 337 | SUPERSEDED | #408 | Still reproduces, but #433 fully contains the fix steps (move named-graph TBox, type classes, update validators). |
| 338 | FIXED | — | Bucket zero-file guard returns 2 (cites #338) + regression tests. |
| 339 | PARTIAL | — | `build_directive_node` now tested; `update_law_file_harmonisation` still has 0 tests. |
| 340 | FIXED | — | CI comment rewritten: combined is validated by the seadusloome job, not the laws bucket. |
| 341 | PARTIAL | #413 | Items 2,3,5,7,12 fixed; item 1 half-done; items 4,6,8,9,10,11 remain (item 6 — draft amendmentDate — now also #423). |
| 342 | FIXED | — | `rederive_court_case_types.py` + tests + backfilled data (0 chamber mismatches in 1998 file). |
| 343 | PARTIAL | #405 | Generator fixed (EFTA + sector-8 routing); curia data stale: 47+11 nodes still `EUCourt_CourtOfJustice` (regen needs network). |
| 344 | PARTIAL | #408 | Resolved since filing: `estleg:transpositionDeadline` is declared in `controlled_vocabulary.jsonld`, SHACL-constrained, and test-asserted to live in the CV. Remaining: `CaseType_Other` still absent from riigikohus_schema (enum set split across files) and dead `DecisionType_Resolution` still declared; the #344-cited comment in eelnoud_schema claiming `amendsLaw` is unpopulated is itself stale (1,333 uses in data). Relates #433/#270. |
| 345 | FIXED | — | `hasVersion` materialized in 773 law peeps (cites #345). |
| 346 | PARTIAL | #407 | `slugify` rstrip fixed; 167 double-underscore IRIs still committed (rename migration pending). Relates #449/#445. |
| 347 | FIXED | — | `heading_re` ReDoS fix + regression test. |
| 348 | PARTIAL | #406 | sameAs/dcterms:source/eli:id_local backfilled 100%; eelnoud dup amendsLaw = 0. Residue: `dcterms:title` still 0/22,290 on EU court decisions. (#417 tracks a different eurlex staleness.) |
| 349 | FIXED | — | All 4 batch items verified (curiaLink comment, `build_xml_url` urlsplit, date guard, rdflib pin). |
| 350 | VALID | #405 | No word boundary in either citation regex; VTMS/HKMS unregistered; ~5,000 wrong `interpretsLaw` links stand. Relates #427. |
| 351 | FIXED | — | `karistatakse` prohibition pattern + regenerated data (385 prohibitions in KarS osa2). |
| 352 | PARTIAL | #409 | `extract_temporal_data.parse_date` guarded; `generate_amendment_history` + `riigiteataja_common` + EUR-Lex sentinel dates unguarded; bad dates still in data. |
| 353 | VALID | #409 | Guard landed but post-fix regen still contains the same 6 impossible adopt/eif pairs — guard ineffective; root-cause under #429. |
| 354 | PARTIAL | #407 | Code fixed (range canonicalization, rstrip); committed data unchanged (TsK_Par_194; 3 trailing-underscore Divisions; whitespace cluster ids). Relates #449. |
| 355 | PARTIAL | — | NBSP: code fixed, curia data stale (1,537 lines). U+FFFD half untouched (82 provision_versions files, no detection). |
| 356 | PARTIAL | #414 | SPARQL injection fixed (allowlist + tests); XXE unfixed (5 raw `ET.fromstring` sites, no defusedxml: generate_all_laws, generate_draft_legislation, generate_provision_versions, generate_kars_eriosa_jsonld, riigiteataja_common); court-parser ReDoS remains. Relates #481/#466. |
| 357 | FIXED | #412 | All 5 specific asks landed in shapes (+tests). The broader residual coverage gap is the new #450/#451. |
| 358 | FIXED | — | Quantified sunniraha regex + regenerated sanction (15,000 EUR present). |
| 359 | VALID | #408 | 5/7 T-Box label conflicts (CV vs concepts_combined) remain. Relates #433. |
| 360 | FIXED | #405 | Keyword gates landed + data reclassified (234→0, 93→0). Layer is rebuilt anyway by #421. |
| 361 | FIXED | — | All 7 docs-drift items verified fixed (API_GUIDE counts, L2c status, layer/script/class counts, EuroVoc query). |
| 362 | FIXED | — | `legalText` fallback in citation extraction + regenerated data (subsection refs resolve). |
| 363 | FIXED | — | 25 genitives + `koodeksi?` self-refs added + regenerated. |
| 364 | VALID | #406 | Pure regen ask never executed: KrMS still has 698 Division-contained cluster-orphans; `KRIMIN_2_Par_18` requestedCluster=None. Distinct from its epic sibling #420; would also be addressed as a side effect of the cluster-ID rework + regen (#448). |
| 365 | VALID | #411 | `hasCompetence` still undefined anywhere; 317 Competence aggregates remain island nodes. Relates #457. |
| 366 | FIXED | — | Combined rebuilt with all 3 `owl:inverseOf` axioms + new T-Box parity/staleness guard in validate_all (+tests). |
| 367 | FIXED | — | Boilerplate patterns + KOV symmetry repair; index regenerated (score-1.0 pairs 4,226→0). |
| 368 | PARTIAL | #413 | Boundary-aware truncation in all 3 generators + commons; committed peeps stale (`ATKE_Par_24` still cut mid-word). Relates #466. |
| 369 | FIXED | — | metadata.jsonld DCAT: 8 distributions, accessURL, publisher, byteSize, RK+KOV distributions, modified bumped. Relates #477. |
| 370 | FIXED | — | Legacy Section similarity targets gone post-regen; `generate_missing_parts` emits `@id` objects. |
| 371 | PARTIAL | #406 | Both generator gaps fixed (+tests); data stale (REÕS still 31/69 `isPartOf=None`). Relates #415 (distinct scope). |
| 372 | FIXED | — | Hyphen ranges, fine inflections, mõjutustrahv extractor + regenerated sanctions (KarS §144 8 y present). |
| 373 | FIXED | — | `annab/väljastab tegevusloa` patterns + regenerated competence (licensing → KINDLU_Par_15). |
| 374 | PARTIAL | — | Tombstones + index exclusion + similarity skip in code; committed regulations corpus/indexes still include repealed acts with full text. |
| 375 | PARTIAL | #406 | Code + tests landed; data stale (`Chapter_RavS_5` still missing direct-child §§87-98). Relates #415. |
| 376 | PARTIAL | #413 | Atomic-write half done (all 8 cited scripts now import commons `save_json`); `--resume-from` precondition half remains → #470; the 3 generator copies → #464. |
| 377 | VALID | #408 | Wrong full-IRI domain/range axioms persist in combined; bucket gate still `inference="rdfs"`. Relates #433/#439/#455. |
| 378 | FIXED | — | All 4 HTML-overview drift items fixed (commit cites #378). Relates #477. |
| 379 | PARTIAL | #411 | Annotations half code-fixed but `annotations/` stale (690 wrong osa targets committed); draft-impact half unfixed (6 TsMS drafts → arbitrary osa). Relates #459/#445. |
| 380 | PARTIAL | #405 | Regex + test landed; eelnoud data stale (now 20 non-2026 drafts → 2026 budget law). Re-verify resolver on next regen. |
| 381 | FIXED | — | `on kohustuslik/nõutav/nõutud` obligation patterns + regenerated data. Relates #461. |
| 382 | PARTIAL | #408 | SCHEMA_REFERENCE contract corrected (documents Estonian-only, no-`en`); residual untagged enum/CV labels fold into #437. |
| 383 | PARTIAL | #410 | Item 1 fixed (ratification keywords; 50/72 coverage); item 2 code-fixed/data-stale (TT mistyping); item 3 not fixed (21 non-ASCII IRIs). Relates #449/#441. |
| 384 | FIXED | — | Regen done: 16,835 events vs 16,013 unique pairs (~60k duplicates gone). Residual 4.9% warning is a validator pair-key over-count — refine under #429/#454. |
| 385 | FIXED | — | `jsonld_text` unwrap + test; 0 dict-repr labels in regenerated amendments. |
| 386 | PARTIAL | #413 | Regex hoisting done (a); KOV full re-parse in similarity (b) and linear issuer scans (c) remain. |
| 387 | FIXED | — | Publication-date sort (IRI-stable) + test; regenerated chains strictly ascending. Orthogonal to #423. |
| 388 | FIXED | — | Primary-clause + directive-subject matching rewrite; spurious co-amendment links absent from regenerated data. |
| 389 | PARTIAL | #413 | `isCurrentAmendment` emitted (item 2); KOV failure-log raw municipality value remains (item 1). |
| 390 | FIXED | — | `issuedUnder` recall 11.4% → 90.7% (4 sub-fixes verified; 0 self-references). |
| 391 | FIXED | — | Dedup pre-strip of `; N (` suffix + test (Concept_reovesi 67→27 variants). |
| 392 | PARTIAL | #410 | Item 2 (Riigikohus ECLI) superseded by #442; items 4,5 fixed; item 1 (unused context prefixes → #465) and item 3 residue (committed dup decision pair) remain. |
| 393 | PARTIAL | #409 | Items 1–2 fixed + regenerated; item 3 code-fixed but `provision_versions/` stale (2 zero-day nodes committed; no from<to SHACL). |
| 394 | PARTIAL | #410 | Item 1 code-fixed/data-stale (31 mistyped sector-4/5 CELEX remain); item 2 report self-inconsistency remains; item 3 fixed. Relates #441/#417. |
| 404 | VALID | #413 | All 4 hardening sites un-addressed. Relates #469/#470. |

## Action taken (2026-06-11)

- **Closed as completed (58):** #292 #299 #301 #302 #303 #305 #307 #308 #310 #312 #313 #314
  #315 #316 #317 #318 #320 #321 #322 #323 #324 #325 #326 #327 #328 #329 #330 #331 #332 #334
  #336 #338 #340 #342 #345 #347 #349 #351 #357 #358 #360 #361 #362 #363 #366 #367 #369 #370
  #372 #373 #378 #381 #384 #385 #387 #388 #390 #391 — each with an evidence comment.
- **Closed as superseded (2):** #291, #337 → #433.
- **Commented with remaining scope (30 PARTIAL):** #296 #298 #304 #306 #309 #335 #339 #341 #343
  #344 #346 #348 #352 #354 #355 #356 #368 #371 #374 #375 #376 #379 #380 #382 #383 #386 #389
  #392 #393 #394.
- **Left open unchanged (12 VALID):** #270 #300 #311 #319 #333 #350 #353 #359 #364 #365
  #377 #404 — epic mapping recorded above.
