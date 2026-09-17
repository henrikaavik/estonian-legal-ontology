# Open Issues Remediation Plan

Date: 2026-05-24
Repository: `henrikaavik/estonian-legal-ontology`
Validated against: local HEAD `951001e53`
Scope: all 9 currently open GitHub issues: #203, #204, #207, #208, #210, #213, #217, #218, #219.

## Validation Summary

I read each open issue body and its comments, then re-checked the comments'
claims against the local checkout. The issue comments are mostly accurate and
useful for sequencing. Local checks confirmed these key points:

- Root `*_peep.json` files currently contain `0` `estleg:Subsection` nodes,
  even though `generate_all_laws.py` has `_loige_numbers`, `_iter_loiked`,
  `build_subsections`, and `merge_existing_enrichments`.
- `data/riigiteataja/karistusseadustik.xml` is still the only root cached law
  XML, so the full Subsection materialisation remains a network-heavy run.
- `generate_missing_parts.py` still emits flat provisions through
  `collect_text(..., max_len=800)` and has no `Subsection`, `hasSubsection`,
  or `_Lg_` path.
- The public Seadusloome load path closure audit reproduces exactly:
  `29,986` dangling `estleg:` object references over
  `combined_ontology.jsonld + eelnoud + riigikohus + curia + eurlex`.
- Expanding that load surface to include `concepts`, `sanctions`,
  `amendments`, `institutions`, `provision_versions`, `annotations`,
  `harmonisation`, and `regulations` reduces unresolved references to `44`,
  all under `estleg:amendsLaw`. This confirms that `regulations/` must be part
  of the published section 2 graph, not just the named sidecar directories.
- The bare namespace act-node collision is broader than issue #218's body:
  seven root `*_peep.json` files plus two allowlisted `*_owl.jsonld` files use
  `https://data.riik.ee/ontology/estleg#` as an `estleg:Act` or
  `estleg:Law` node.
- Court full-text support exists and currently has `99` decisions with
  `estleg:legalText` out of `12,137` decision nodes. Link extraction is still
  summary-only. The text extractor already has a minimal length floor
  (`MIN_DECISION_TEXT_LEN = 40`), but it should be tightened or made explicit
  before the full run because the issue comment's stub-page concern remains
  valid at scale.
- Provision-version and annotation samples match the issue comments:
  `213` `estleg:ProvisionVersion` nodes across 2 files, and `6`
  `estleg:Annotation` nodes from 5 seed inputs.
- `extract_draft_impact.py` still reads `rdfs:label` and `estleg:initiator`
  raw, while `generate_draft_legislation.py` emits both as JSON-LD value
  objects. Issue #219 is ready to implement.

## Dependency Order

The safest order is:

1. Fix #219 first. It is small, code-only, and protects the eelnõu workflow.
2. Fix #217 next. It defines the public graph load surface, fixes the residual
   44-reference resolver/dead-reference defects, and unblocks the
   sidecar/back-link decisions in #208 and #210.
3. Fix #218 before any multipart or supplementary-law regeneration. It prevents
   further act-node collision churn.
4. Fix #204 after #218, because `generate_missing_parts.py` emits some of the
   affected bare-namespace act nodes.
5. Fix #213 after #218 and alongside the post-regen extraction reruns. This is
   the main law-corpus network refresh.
6. Fix #207, #208, and #210 as separate data-ingestion tracks after the public
   graph surface from #217 is settled.
7. Fix #203 last. It is valid, but it is soft-blocked until Subsections exist
   in the corpus from #204/#213 and needs a SHACL/runtime benchmark.

## Issue Plans

### #219: Make Draft Impact Extraction Robust To JSON-LD Value Objects

Status: valid, ready to implement.

Plan:

1. In `scripts/extract_draft_impact.py`, wrap every consumed
   `rdfs:label` and `estleg:initiator` value with
   `estleg_common.jsonld_text()`.
2. Preserve current behavior for plain strings and empty values.
3. Add tests in `tests/test_extract_draft_impact.py` with generated-style
   draft nodes where both fields are value objects.
4. Grep the file for any other raw access to those fields before closing.
5. Run:
   - `python3 -m pytest tests/test_extract_draft_impact.py -q`
   - `python3 -m ruff check scripts/extract_draft_impact.py tests/test_extract_draft_impact.py`

Acceptance:

- Fresh `generate_draft_legislation.py` output can pass through
  `extract_draft_impact.py` without `TypeError`.
- Existing plain-string corpus data still produces identical impact output.

### #217: Close Published Graph Over Sidecar Targets

Status: valid, reproduced exactly.

Decision:

Adopt the load-path model, not the "fold every sidecar into
`combined_ontology.jsonld`" model. The published section 2 graph should be
documented and validated as:

`combined_ontology.jsonld` plus `eelnoud`, `riigikohus`, `curia`, `eurlex`,
`concepts`, `sanctions`, `amendments`, `institutions`,
`provision_versions`, `annotations`, `harmonisation`, and `regulations`.

Plan:

1. Add a shared public-load-surface helper so
   `validate_seadusloome_sync.py`, docs, and graph-closure validation use the
   same directory list.
2. Extend `SEADUSLOOME_SUBDIRS` to include the sidecar directories and
   `regulations/`.
3. Add a graph-closure validator that:
   - loads the documented public surface;
   - records every graph node `@id`;
   - walks object references with `@id`;
   - fails on unresolved internal `estleg:` targets;
   - emits a per-predicate count and representative sample.
4. Add a focused regression test with a tiny combined graph and a sidecar
   target to prove that missing sidecars fail and loaded sidecars pass.
5. Split the remaining `44` unresolved `estleg:amendsLaw` targets into two
   explicit work tracks:
   - fix resolver/slug bugs that produce malformed targets such as
     `estleg:Vlaigusseadus_Osa10_1005_1067` and
     `estleg:hinenud_Rahvaste_Organisatsio_Map`;
   - delete or regenerate dead `_Map` references whose target act-map
     nodes are never emitted, such as `estleg:TKS_Map` and
     `estleg:Maailma_Terviseorganisatsiooni_Map`.
6. Keep the residual `44` fix under #217, not #218. It is a public graph
   closure/name-resolution problem; #218 should stay focused on bare namespace
   act-node migration.
7. Benchmark `validate_seadusloome_sync.py` before and after expanding the load
   surface. If the runtime grows beyond CI budget, keep graph-closure checks
   over the full public surface but split SHACL validation by bucket.
8. Update `docs/SCHEMA_REFERENCE.md`, `docs/API_GUIDE.md`, or a validation doc
   to state clearly that section 2 consumers must load the public surface, not
   only `combined_ontology.jsonld`.
9. Update `scripts/run_all_integration.py` output metadata so sidecar writers
   are explicit and skipped sidecar-producing steps are visible.

Acceptance:

- The current `29,986` unresolved public-surface references drop to zero.
- `validate_seadusloome_sync.py` and the docs agree on the files/directories
  consumers load.
- The remaining `44` `amendsLaw` failures are either resolvable after a
  resolver fix or intentionally removed with a documented reason.
- The expanded validation surface has a recorded before/after runtime.
- `competentAuthority`, `hasSanction`, `amendedBy`, `harmonisedWith`,
  `semanticallySimilarTo`, concept-definition, regulation, and cross-reference
  links can be followed in the documented graph.

### #218: Fix Multipart Law Act Nodes Using Bare Namespace IRI

Status: valid, broader than issue body.

Affected data confirmed locally:

- `krr_outputs/volaigusseadus_osa5_peep.json`
- `krr_outputs/volaigusseadus_osa8_peep.json`
- `krr_outputs/volaigusseadus_osa9_peep.json`
- `krr_outputs/volaigusseadus_osa11_peep.json`
- `krr_outputs/volaigusseadus_osa12_peep.json`
- `krr_outputs/volaigusseadus_osa13_peep.json`
- `krr_outputs/tsiviilseadustik_osa8_peep.json`
- `krr_outputs/karistusseadustik_eriosa_owl.jsonld`
- `krr_outputs/tsus_osa7_138_169_owl.jsonld`

Plan:

1. Extend `scripts/migrate_multipart_iri_scheme.py` or add a narrow migration
   helper for act-level `@id` changes.
2. Derive compact, abbreviation-backed act IDs from the file's contained
   provision range: `<ABBREV>_Osa<N>_<minPar>_<maxPar>`, using the registry
   abbreviation and the minimum/maximum `estleg:paragrahv` values in that
   part. Single-section parts still repeat the range, for example
   `estleg:TsUS_Osa8_170_170`.
3. Rewrite references to the old bare namespace act node where any exist.
   Before and after the migration, run:
   `rg '"@id": "https://data.riik.ee/ontology/estleg#"' krr_outputs/`
   and audit every occurrence as either legitimate ontology-document use or a
   migrated act/reference site.
4. Preserve all act-level enrichment fields (`dcterms:subject`,
   `estleg:affectedBy`, `estleg:transposesDirective`,
   `estleg:temporalStatus`, etc.) on the new distinct act nodes.
5. Tighten `validate_all.validate_id_uniqueness()`:
   - keep legitimate ontology-document use of
     `https://data.riik.ee/ontology/estleg#`;
   - fail if that IRI is used on any `estleg:Act` or `estleg:Law` node;
   - avoid a blanket shared-ID allowlist for this IRI.
6. Rerun inverse/reference maintenance affected by IRI churn, especially
   `generate_inverse_references.py`, so `amendedBy`, `referencedBy`, and other
   `*By` predicates do not retain stale targets.
7. Regenerate `krr_outputs/combined_ontology.jsonld` through
   `scripts/fix_all_issues.py`.
8. Add tests covering the validator rule, act-ID derivation, downstream
   reference sweep, and combined dedup behavior.

Acceptance:

- No act/law node uses the bare namespace IRI as its `@id`.
- Combined output contains a distinct act node for every affected multipart
  file.
- Namespace-IRI use remains valid only for ontology/vocabulary document nodes.
- No `*By` inverse reference points at the old bare namespace act node.

### #204: Add Subsection Support To `generate_missing_parts.py`

Status: valid, sequence after #218.

Plan:

1. Import and reuse `generate_all_laws.py` helpers:
   `_iter_loiked`, `_loige_numbers`, `_loige_body_text`, and
   `build_subsections`.
2. Add Subsection emission to each supplementary provision-building path in
   `generate_missing_parts.py`.
3. Add `estleg:hasSubsection` to provision nodes in document order.
4. Emit full `estleg:legalText` on provision nodes for parity with
   `generate_all_laws.py`.
5. Add a fail-fast uniqueness assertion for Subsection `@id`s within each
   generated output file.
6. Add tests with:
   - a provision with no `loige` children;
   - a provision with ordered `loige` children;
   - superscript-like numbering such as `1`, `1¹`, `2`, `2¹`.
7. Regenerate the affected supplementary artifacts and then regenerate
   `combined_ontology.jsonld`.

Acceptance:

- Supplementary law-part files use the same Subsection IRI and text model as
  `generate_all_laws.py`.
- No duplicate Subsection IDs are emitted.
- `validate_all.py`, `validate_seadusloome_sync.py`, and the relevant SHACL
  bucket pass.

### #213: Run Full Corpus Regen To Materialise Subsection Nodes

Status: valid, blocked on network and post-regen enrichment order.

Plan:

1. Re-run the cheap KARIST sample first using the local XML cache.
2. Confirm whether the duplicate `estleg:Division_KARIST_2_1_4_2` generator
   collision still reproduces. Fix it only if the sample confirms it still
   exists during regeneration.
3. Before bulk regeneration, audit idempotence for
   `extract_legal_concepts.py` and `extract_cross_references.py` on the KARIST
   sample. Compare rerun output for duplicate concept/cross-reference nodes,
   stale `coversConcept` targets, and references derived from old provision
   IRIs.
4. Use preservation strategy (b): rerun the extraction scripts after the base
   law refresh instead of preserving whole stale extraction-generated nodes.
   This keeps the generator canonical for base law structure and keeps
   extractor-owned nodes refreshable.
5. Make `run_all_integration.py` dependencies explicit enough that
   `extract_legal_concepts.py` and `extract_cross_references.py` run after the
   law refresh when Subsection IRIs change.
6. Run `generate_all_laws.py --refresh` over the corpus in per-law batches so a
   single network failure does not discard progress. Define progress explicitly:
   either commit/review per batch, or write a resumable run manifest such as
   `.regen_state.json` with completed law slugs, source IDs, output paths,
   Subsection counts, and warnings.
7. Record a progress table while running:
   `(law slug, fetched OK, source ID, output path, Subsection count emitted,
   warnings)`.
8. Rerun dependent enrichment scripts whose output can reference refreshed law
   nodes:
   - cross-references and inverse references;
   - legal concepts;
   - sanctions;
   - court provision links;
   - similarity index;
   - EuroVoc, transposition, and harmonisation if act-level enrichment changed.
9. Regenerate `combined_ontology.jsonld`.
10. Sync `metadata.jsonld`, README counts, and validation documentation only
   after deterministic post-processing is complete.

Acceptance:

- Root law peep files contain materialised `estleg:Subsection` nodes.
- `generate_all_laws.py --missing-only` is a no-op after the refresh.
- Extractor-owned concept/cross-reference nodes are regenerated, not silently
  lost.
- The idempotence audit shows no duplicate extractor-owned nodes and no stale
  `coversConcept` or cross-reference targets from pre-regen provision IRIs.
- Full release gates pass:
  - `python3 -m ruff check scripts/ tests/`
  - `python3 -m pytest -q`
  - `python3 scripts/validate_all.py`
  - `python3 scripts/validate_seadusloome_sync.py`
  - `python3 scripts/shacl_validate_all.py --all`

### #207: Run Full Supreme Court Full-Text Ingestion And Re-Extract Links

Status: valid, data-heavy.

Plan:

1. Before the full fetch, tighten the full-text quality guard:
   - keep the decision-document marker checks;
   - raise or parameterize the current `MIN_DECISION_TEXT_LEN = 40` threshold
     so search-form stubs and tiny pages are rejected;
   - add a minimal Estonian text-quality check, for example requiring a
     reasonable ratio of `[a-zõäöü]` characters, so mojibake pages do not pass
     just because they have enough bytes;
   - add tests for short legacy stub pages.
2. Confirm cache filenames are safe for case numbers containing `/` or other
   path separators. The current path uses `sanitize_id(case_nr)`; add a test if
   missing.
3. Run the full ingestion:
   `python3 scripts/generate_court_decisions.py --fetch-full-text --full-text-limit 0`.
   Treat it as a resumable overnight/network job.
4. Update `detect_referenced_laws` and `extract_court_provision_links.py` to
   use `estleg:legalText` when present, falling back to `estleg:summary`.
5. Update `court_provision_links_report.json` to distinguish:
   - full-text-derived hits;
   - summary fallback hits;
   - new links found only because full text exists;
   - decisions without usable full text.
6. Emit a `full_text_recall_lift` metric: new law/provision links compared to
   the summary-only baseline.
7. Re-extract links, rerun inverse-reference maintenance for
   `interpretedBy`/related `*By` predicates, and rerun the `riigikohus` SHACL
   bucket.
8. Consider splitting large `riigikohus_*_peep.json` files by decade only if
   the post-ingestion file size becomes operationally painful. Do not make that
   part of the first fix unless necessary.

Acceptance:

- Full-text coverage is populated for every public decision page that yields
  usable text.
- Usable text excludes stubs and mojibake according to explicit quality
  thresholds.
- Summary-only extraction remains a fallback, not the primary path.
- Reports show the recall gain from full text.
- File counts remain stable; only file contents grow.

### #208: Run Full ProvisionVersion Ingestion

Status: valid, gated on #217 for the public sidecar model.

Decision:

Keep `generate_provision_versions.py` out of `run_all_integration.py` for now.
It is a multi-hour network ingestion, not a quick deterministic local
enrichment step.

For back-links, use the #217 public-load-surface decision. The first full
ingestion should keep `estleg:versionOf` as the canonical link and document the
query pattern. Add `estleg:hasVersion` / `estleg:currentVersion` only if the
team accepts that the published graph is always loaded with
`provision_versions/`; do not add sidecar patch nodes with duplicate provision
`@id`s.

Plan:

1. Add or verify a true `--all` mode that processes every law slug without
   needing a hand-built command list.
2. Add per-law persisted coverage rows:
   `(slug, redactions_processed, redactions_failed, versions_emitted,
   warnings)`.
3. Run the full ingestion with the Riigi Teataja cache enabled and conservative
   throttling.
4. Classify laws with no emitted versions as snapshot-only, failed, or no
   non-trivial redaction history.
5. Add a `docs/SCHEMA_REFERENCE.md` section showing the canonical SPARQL query
   pattern for "what did this provision say on date X?" using
   `?version estleg:versionOf ?provision`, `versionValidFrom`, and
   `versionValidTo`.
6. Regenerate coverage reports.
7. Record the expected file-count delta, but leave the canonical
   `metadata.jsonld`/README count sync to the final data-release sync unless
   this PR is the last data-heavy one to land.
8. Validate:
   - `python3 scripts/shacl_validate_all.py --bucket sidecars`
   - `python3 scripts/validate_all.py`
   - `python3 scripts/validate_seadusloome_sync.py`

Acceptance:

- Every law with a non-trivial redaction history has ProvisionVersion output,
  or a documented failure/snapshot-only reason.
- All `versionOf` and `supersededByVersion` references resolve in the public
  load surface.
- The backlink decision is documented and validation reflects it.
- Documentation includes the no-backlink SPARQL query pattern consumers should
  use.

### #210: Run Full Õiguskantsler Annotation Ingestion

Status: valid, needs a PDF text dependency.

Plan:

1. Before choosing a PDF dependency, sample 10 representative Õiguskantsler PDF
   files and measure usable text-layer extraction. If fewer than 80% produce
   usable text, evaluate an OCR path (`pytesseract` or equivalent) instead of
   assuming `pdfminer.six` is sufficient.
2. Add `pdfminer.six` as the PDF extraction dependency only after the probe
   shows text-layer extraction is adequate; otherwise document the OCR
   dependency and operational cost first.
3. Probe for RSS, Atom, JSON, or sitemap endpoints on `oiguskantsler.ee` before
   committing to HTML-only pagination.
4. Extend the scrape path to fetch and cache PDF bodies under
   `krr_outputs/.cache/annotations/`.
5. Extract bounded body summaries for `estleg:annotationText`; do not dump full
   PDF text into the graph.
6. Use body text, not only titles, for law and provision reference resolution.
7. Reuse the existing law-name index and cross-reference citation parser for
   provision-level targets where possible.
8. Deduplicate by `(opinion_id, annotated_entity)`.
9. Add coverage metrics:
   - listing pages fetched;
   - PDFs fetched;
   - cache hits;
   - PDFs with usable text;
   - PDFs requiring OCR or skipped as scans;
   - opinions parsed;
   - law/provision links resolved;
   - unresolved references.
10. Defer `estleg:annotatedBy` unless #217's public-load-surface validation
   explicitly supports reciprocal sidecar links without duplicate patch nodes.
11. Keep other sources (RT commentaries, ministry circulars, bar association
    guidance) out of this issue; open separate issues after Õiguskantsler is
    complete.

Acceptance:

- The full Õiguskantsler archive is scraped and cached.
- Annotation links are based on PDF body text when available.
- The chosen PDF/OCR dependency is justified by the 10-PDF extraction probe.
- `AnnotationShape` passes in the sidecar SHACL bucket.
- Reports make low recall or unresolved references visible.

### #203: Model Punkt/Item Level Under Subsection

Status: valid, soft-blocked on #204 and #213.

Decision:

Do not model `alapunkt` in this issue. Implement `estleg:Item` only after
Subsections exist in the corpus and a real consumer, such as an obligation
extractor, needs punkt-level nodes. If no such consumer exists, close or mark
the issue as deferred instead of keeping it as an unbounded open task.

Plan:

1. Benchmark SHACL cost before committing the shape. Use a KARIST or small
   multi-law sample to estimate the triple and runtime increase from Items.
2. Add vocabulary terms:
   - `estleg:Item`
   - `estleg:itemNumber`
   - `estleg:hasItem`
   - `estleg:parentSubsection`
3. Add `estleg:ItemShape` with exactly one parent path:
   `parentSubsection` or `parentProvision`.
4. Use the issue comment's safer direct-provision suffix:
   - item under subsection:
     `estleg:<ABBREV>[_Osa<N>]_Par_<par>_Lg_<lg>_P_<punkt>`
   - item directly under provision:
     `estleg:<ABBREV>[_Osa<N>]_Par_<par>_Pp_<punkt>`
5. Add `build_items` in `scripts/generate_all_laws.py` and wire it into
   provision/subsection generation in document order.
6. Add tests for:
   - numeric punkt IDs;
   - letter punkt IDs;
   - superscript punkt IDs;
   - punkt under `loige`;
   - punkt directly under `paragrahv`;
   - no `alapunkt` emission.
7. Only then run a targeted corpus sample and measure:
   - Item node count;
   - graph triple delta;
   - `shacl_validate_all.py --bucket laws` runtime.

Acceptance:

- Items do not create dangling `parentSubsection` references.
- SHACL validation stays under 2x the current `--bucket laws` wall time; if it
  exceeds that threshold, split Items into a separate validation bucket before
  full emission.
- The corpus size impact is documented before full emission.

## Release Strategy

Use separate PRs/commits for code fixes and data-heavy regeneration:

1. Code-only fixes: #219, #217 validation/load surface, #218 migration logic.
2. Targeted generated-data fixes: #218 migrated files, #204 supplementary
   Subsections.
3. Network-heavy corpus jobs: #213, #207, #208, #210.
4. Ontology expansion after corpus readiness: #203.

For every data-heavy PR, keep generated artifacts separate from deterministic
post-processing where practical. Designate one final data-release sync after
#213, #207, #208, and #210 land to update `metadata.jsonld`, README counts, and
validation documentation. If an intermediate PR must touch counts to pass a
gate, record the expected delta so the final sync can reconcile it deliberately.

IRI churn checklist for #218, #213, and #207:

- rerun the relevant forward extractor;
- rerun `generate_inverse_references.py` or the matching inverse maintainer;
- run graph closure over the public surface;
- check that no `amendedBy`, `referencedBy`, `interpretedBy`, `coversConcept`,
  or other `*By`/concept target points at a pre-migration IRI.

## Final Validation Gate

Before closing the full issue set, run:

```bash
python3 -m ruff check scripts/ tests/
python3 -m pytest -q
python3 scripts/validate_all.py
python3 scripts/shacl_validate_all.py --all
python3 scripts/validate_seadusloome_sync.py --report seadusloome-shacl-report.ttl
```

Benchmark the expanded #217 public-surface validation separately before making
it a hard CI gate. If it is too slow for CI, keep the closure check as the hard
gate and run SHACL by bucket.

Also run targeted gates after each issue:

- #219: `tests/test_extract_draft_impact.py`
- #204/#213/#203: `tests/test_generate_all_laws.py` and relevant generator
  tests
- #207: court decision and court provision-link tests
- #208: provision-version tests and `--bucket sidecars`
- #210: annotation tests and `--bucket sidecars`
- #217: new public graph-closure tests
- #218: ID uniqueness and combined generation tests
