# Ontology Review Remediation Plan

Date: 2026-05-07
Owner: Peep
Scope: Full ontology quality, validation architecture, CI coverage, and integration consistency.

## Context

The full ontology review found that the corpus is structurally useful and most pipeline-level tests pass, but the current validation signal is too shallow for production-grade legal data. The immediate goal is to make drift and semantic defects visible in CI before adding more ontology layers.

Observed checks:

- `python3 scripts/validate_all.py` passed over 21,829 files with 0 errors.
- `python3 -m pytest -q` passed with 320 tests and 12 warnings.
- A typed-date scan found 473 invalid `xsd:date` literals, mostly KOV `estleg:publicationDate` values like `2012+02:00-01-01`.
- Full-corpus SHACL loaded 15,510 files and 2,725,168 triples, printed RDFLib date lexical errors, and was too slow for practical CI use in its current single-graph form.

## Findings To Fix

### P1: Registry Drift Is Not Validated

Problem:
`scripts/validate_all.py` excludes indexes and registries, so stale `krr_outputs/INDEX.json` references can pass green. Current examples include missing indexed files and indexed artifacts with no provision nodes.

Plan:

1. Add registry validation to `validate_all.py`.
2. Validate every `INDEX.json` `laws[].files[]` path exists.
3. Validate each indexed law file has one act-level node and either provision nodes or an explicit documented exception category.
4. Report missing files, zero-provision files, and malformed registry entries as errors.
5. Add tests with a temporary `krr_outputs` tree covering missing files, valid multipart files, and allowed exceptions.

Acceptance:

- `validate_all.py` fails on stale index references.
- Current `INDEX.json` is either fixed or exceptions are explicitly encoded.
- The validation report distinguishes data defects from intentional aggregate/schema artifacts.

### P1: Invalid `xsd:date` Literals Are Emitted

Problem:
`extract_temporal_data.py` builds publication-date fallback values by concatenating raw `RTaasta` into `YYYY-01-01`. Some RT XML values include timezone offsets inside the string, producing invalid values such as `2012+02:00-01-01`.

Plan:

1. Normalize `RTaasta` before constructing fallback dates.
2. Prefer a strict four-digit year extraction for fallback publication dates.
3. Add a typed-date validator to `validate_all.py` for every JSON-LD object shaped as `{"@value": "...", "@type": "xsd:date"}`.
4. Add regression tests for `2012+02:00`, `2012+03:00`, plain `2012`, and malformed year values.
5. Re-run `extract_temporal_data.py` or targeted repair over affected KOV files.

Acceptance:

- No invalid `xsd:date` literals remain in `krr_outputs`.
- RDFLib no longer prints date lexical conversion errors during parse.
- The bug cannot recur without failing validation.

### P1: CI Does Not Exercise Most Code Changes

Problem:
`.github/workflows/validate.yml` only triggers for `krr_outputs/**` and `scripts/validate_all.py`, and only runs `validate_all.py`. Generator, extractor, SHACL, docs, and test changes can merge without pytest or semantic checks.

Plan:

1. Expand workflow path triggers to include `scripts/**`, `tests/**`, `shacl/**`, `metadata.jsonld`, `.github/workflows/**`, and relevant docs.
2. Add a pytest job.
3. Keep JSON hygiene validation as its own job.
4. Add a fast semantic-validation job using targeted SHACL buckets rather than the current full single-graph run.
5. Add dependency setup through a committed dependency file.

Acceptance:

- PRs touching scripts or tests run pytest.
- PRs touching ontology outputs run JSON hygiene validation.
- PRs touching SHACL or generated semantic data run at least targeted SHACL validation.

### P2: SHACL Omits Major Ontology Segments

Problem:
`scripts/shacl_validate_all.py` loads indexed laws and domestic regulations only. It does not load draft legislation, Supreme Court decisions, EU legislation, or EU court decisions, despite shapes existing for those classes.

Plan:

1. Split SHACL validation into buckets:
   - laws and domestic regulations
   - KOV registries and KOV regulations
   - Riigikohus decisions
   - EIS drafts
   - EUR-Lex acts
   - CURIA decisions
2. Add file discovery helpers per bucket.
3. Provide a CLI option such as `--bucket` and `--all`.
4. Keep full-corpus validation available for local deep checks, but make bucket checks CI-friendly.
5. Add tests for bucket file discovery.

Acceptance:

- Every class with a SHACL shape has at least one validation path.
- CI can run targeted SHACL without loading the whole corpus into one graph.
- Full validation remains available for release checks.

### P2: Similarity Skips State Regulations

Problem:
`generate_similarity_index.py` calls `iter_peep_files(include_kov=False)`, which includes state regulations, but then filters out every file whose parent is not `KRR_DIR`. This skips state regulations and contradicts documentation that domestic regulations participate in similarity.

Plan:

1. Remove the root-directory-only filter.
2. Use act-node detection instead of parent-path filtering.
3. Preserve the current KOV deferral unless Layer 3 is explicitly started.
4. Add tests proving state regulation files contribute candidates.
5. Update coverage/report output to show laws vs state regulations vs KOV counts.

Acceptance:

- State regulations are included in similarity candidate extraction.
- Similarity report includes per-act-type counts.
- README statements match actual script behavior.

## Second Review Findings (Non-Overlapping)

The second review pass focused on architecture, reproducibility, catalog integrity, and pipeline safety. These findings intentionally do not replace or conflict with the five first-pass findings above.

### P1: Corpus Generation Is Not Reproducible Or Truly Refreshing

Problem:
The README describes the generator commands as "Re-fetch" operations, but enacted-law generation currently queries the live Riigi Teataja search endpoint without a snapshot date, chooses the largest `globaalID` string per title, and skips already existing output files, including loose prefix matches. A rerun therefore cannot recreate the same corpus from a declared legal snapshot, cannot reliably refresh changed acts, and can silently skip distinct acts whose slugs share the same first 20 characters.

Evidence:

- `README.md:534-542` presents `generate_all_laws.py` and `generate_regulations.py` as re-fetch commands.
- `scripts/generate_all_laws.py:124-127` sends no `kehtiv` or snapshot parameter for laws.
- `scripts/generate_all_laws.py:143-145` treats the largest `globaalID` string as the newest law redaction.
- `scripts/generate_all_laws.py:800-815` skips exact existing slugs and prefix matches instead of refreshing them.

Plan:

1. Add explicit generation modes: `--missing-only`, `--refresh`, and `--force`.
2. Add a law snapshot parameter equivalent to regulation `--kehtiv`, and record it in each generated corpus index.
3. Replace prefix-based skip detection with exact identity mapping based on stable RT identifiers.
4. Persist an input manifest containing query parameters, source IDs, source URLs, and generated file paths.
5. Add regression tests for rerun behavior, including changed existing files and slug-prefix collisions.

Acceptance:

- A declared snapshot can be regenerated deterministically from source IDs.
- `--refresh` updates existing files instead of silently skipping them.
- Generation logs and indexes distinguish skipped, refreshed, newly generated, and intentionally excluded acts.

### P2: Regulation Index Counts Only Newly Generated Files

Problem:
`generate_regulations.py` skips existing output files before accumulating totals, then writes `REGULATIONS_RIIK_INDEX.json` or `REGULATIONS_KOV_INDEX.json` from only the files generated during that run. On a normal rerun, the index can report `totalRegulations: 0` even when thousands of regulation files are present.

Evidence:

- `scripts/generate_regulations.py:453-455` skips existing files before parsing or counting them.
- `scripts/generate_regulations.py:480-484` increments totals only for newly saved files.
- `scripts/generate_regulations.py:491-502` writes `totalRegulations: generated` and derived totals from that partial run.

Plan:

1. Build regulation indexes from the complete output directory after generation, not from the current run's newly generated subset.
2. Keep separate counters for discovered source acts, existing files, newly generated files, refreshed files, skipped files, and failed fetches.
3. Validate index totals against the filesystem in `validate_all.py` once registry validation exists.
4. Add a test where all output files already exist and the index still reports the full corpus count.

Acceptance:

- Re-running `generate_regulations.py` without new acts preserves accurate index totals.
- Index fields clearly distinguish corpus totals from run-local generation stats.
- State and KOV regulation index counts match their output trees.

### P2: Published Catalog Metadata Is Stale And Unchecked

Problem:
`metadata.jsonld` hard-codes dataset distribution counts that no longer match the repository. It advertises 5,124 JSON-LD files and 3,792 state-level regulations, while the current tree contains 21,864 JSON or JSON-LD files under `krr_outputs/`, 637 top-level enacted-law peep files, and 3,812 state regulation peep files. External consumers using the DCAT metadata get an incorrect picture of corpus size and coverage.

Evidence:

- `metadata.jsonld:261-294` hard-codes the distribution descriptions and counts.
- Current file discovery reports 21,864 JSON/JSON-LD files under `krr_outputs/`.
- Current file discovery reports 3,812 state regulation peep files under `krr_outputs/regulations/riik/`.

Plan:

1. Generate catalog statistics from the same discovery helpers used by validation.
2. Store machine-readable counts in `metadata.jsonld` rather than only prose descriptions.
3. Add a metadata validation check comparing advertised counts with actual file counts.
4. Update README and `docs/VALIDATION_REPORT.md` from the generated statistics to avoid independent drift.

Acceptance:

- DCAT metadata counts match the repository at validation time.
- Count drift fails validation or is explicitly marked as an intentional release snapshot.
- Public metadata exposes enough structured counts for downstream catalog consumers.

### P2: Integration Pipeline Mutates Production Outputs Without Transactional Safety

Problem:
`run_all_integration.py` correctly documents that all enrichment scripts mutate the same `*_peep.json` files and must run sequentially, but the orchestration only invokes subprocesses against the production output tree. If a later enrichment fails, earlier scripts may already have partially rewritten the corpus, leaving the repository in a mixed pipeline state with no generated manifest, rollback point, or post-step invariant check.

Evidence:

- `scripts/run_all_integration.py:5-18` states that the scripts modify the same output files and can clobber each other.
- `scripts/run_all_integration.py:121-156` runs each script directly against the repository and records only process success or failure.
- `scripts/run_all_integration.py:158-170` exits after failures without rollback, staged output promotion, or semantic validation between phases.

Plan:

1. Add a staging output mode for enrichment scripts or run the pipeline against a copied corpus tree.
2. Write a pipeline manifest with script versions, input counts, output counts, changed file counts, and failure details.
3. Run lightweight validation after each dependency phase before promoting changes.
4. Promote staged outputs atomically only after the full selected pipeline passes.
5. Add `--dry-run` and `--resume-from` support for safe operation on large corpora.

Acceptance:

- Failed enrichment runs do not leave production outputs half-updated.
- Each pipeline run produces a manifest sufficient to audit what changed.
- Phase-level validation catches malformed intermediate states before later scripts consume them.

### P2: Reproducible Environment Is Not Defined

Problem:
The repository has Python scripts and tests that require third-party packages, but there is no committed dependency manifest such as `pyproject.toml`, `requirements.txt`, `poetry.lock`, or `uv.lock`. This makes local review, CI expansion, and long-running corpus regeneration dependent on whatever happens to be installed in a developer machine or runner image.

Evidence:

- Repository-root dependency manifest discovery returned no Python dependency files.
- `scripts/generate_all_laws.py` imports `requests`.
- `scripts/shacl_validate_all.py:39-41` imports `rdflib` and `pyshacl`.
- `tests/conftest.py:4` imports `pytest`.

Plan:

1. Add a minimal `pyproject.toml` or requirements set covering runtime and dev dependencies.
2. Pin or constrain versions for RDF/SHACL libraries whose parsing behavior can affect validation results.
3. Document supported Python versions.
4. Update CI to install from the committed dependency source.
5. Add an onboarding command for local validation.

Acceptance:

- A fresh clone can install dependencies with one documented command.
- CI and local runs use the same dependency source.
- RDF parser and SHACL validator versions are controlled enough for repeatable validation output.

### P3: Source Provenance Fields Are Inconsistent Across Generators

Problem:
The ontology uses `dc:source` and `dcterms:source` inconsistently across generated segments. Enacted laws emit `dc:source` as a language-tagged JSON-LD value, regulations emit it as a plain string, some generated artifacts use it for system labels, and validation documentation says `dc:source` is always a string. Downstream scripts already compensate by falling back between `dc:source`, `rdfs:label`, and `dcterms:title`, which is a sign that the provenance contract is not stable.

Evidence:

- `scripts/generate_all_laws.py:294-302` emits `dc:source` as a language-tagged value and `dcterms:source` as the RT URL.
- `scripts/generate_regulations.py:315-324` emits `dc:source` as a plain title string and `dcterms:source` as the RT URL.
- `docs/VALIDATION_REPORT.md:22` states that `dc:source` is always a string.
- Multiple enrichment scripts read source/title fields with fallbacks rather than one canonical contract.

Plan:

1. Define a source/provenance contract in `docs/SCHEMA_REFERENCE.md`.
2. Reserve `dcterms:source` for resolvable source IRIs.
3. Use a dedicated canonical title field for act titles instead of overloading `dc:source`.
4. Normalize generated `dc:source` shapes or deprecate the field with a migration path.
5. Add validation for the chosen contract.

Acceptance:

- Every generator emits the same provenance shape for equivalent document types.
- Validation documentation matches actual JSON-LD output.
- Downstream enrichers can rely on one canonical source/title field without ad hoc fallbacks.

## Third Review Findings (Non-Overlapping)

The third review pass focused on cross-script contracts, graph integrity, source-fetch failure handling, and identity quality. These findings intentionally avoid the first and second review findings.

### P1: Source Fetch Failures Can Produce Partial Successful Corpora

Problem:
Riigi Teataja page-fetch failures are treated as the end of pagination rather than a failed generation run. If a middle page times out or returns invalid data, the generator can continue with a truncated source list and still write outputs or indexes as if the corpus was complete.

Evidence:

- `scripts/generate_all_laws.py:124-133` catches any law-list API exception, prints it, and breaks out of pagination.
- `scripts/riigiteataja_common.py:152-158` catches regulation-list API exceptions, prints them, and breaks the iterator.
- `scripts/generate_regulations.py:398-417` consumes that iterator and returns whatever partial dictionary was accumulated.
- `scripts/generate_regulations.py:491-502` then writes the regulation index from the run state.

Plan:

1. Treat source-list page failures as fatal by default.
2. Add an explicit `--allow-partial` mode only for exploratory runs.
3. Record requested pages, successful pages, failed pages, row counts, and completion status in generation manifests.
4. Add pagination tests where page 2 fails and the generator exits non-zero without writing final indexes.
5. Add retry/backoff around transient API failures before aborting.

Acceptance:

- Network or API failure cannot silently masquerade as a complete corpus refresh.
- Partial exploratory runs are visibly marked partial in logs and manifests.
- Final indexes are written only for completed source enumerations.

### P1: Internal RDF References Are Not Checked For Resolvability

Problem:
The ontology currently validates local `@id` uniqueness, but it does not validate whether object references resolve to a node in the corpus or to an explicitly external vocabulary. A corpus-wide scan found dangling internal `estleg:` references, including 47,713 `estleg:normativeType` references to missing `NormType_*` individuals, 679 `estleg:caseType` references to missing `CaseType_Other`, 486 EU institution references to undefined fallback `EUInst_*` nodes, 363 amendment `amends` links to missing targets, and 40 dangling similarity links.

Evidence:

- `scripts/classify_deontic.py:83-88` emits `estleg:NormType_*` IRIs, while `docs/SCHEMA_REFERENCE.md:510-511` documents those individuals as part of the model.
- `shacl/estonian_legal_shapes.ttl:134-139` checks only that `normativeType` is an IRI, not that it exists.
- `scripts/generate_court_decisions.py:84-88` can emit `CaseType_Other`, but `scripts/generate_court_decisions.py:211-219` only materializes `CASE_TYPE_MAP` entries.
- `scripts/generate_eu_legislation.py:337-345` creates fallback `EUInst_<author_code>` references for unknown author codes, while `scripts/generate_eu_legislation.py:223-231` only materializes known `EU_INSTITUTIONS`.

Plan:

1. Add a referential-integrity validator for internal `{"@id": "estleg:..."}` object values.
2. Maintain an allowlist for intentionally external compact IRIs, if any.
3. Materialize controlled-vocabulary individuals for deontic norm types, fallback case types, EU court decision fallback types, and unknown EU institutions.
4. Repair dangling amendment and similarity targets by remapping or dropping links with report entries.
5. Add tests that unresolved internal references fail validation unless explicitly exempted.

Acceptance:

- Internal object references either resolve to corpus nodes or are reported as validation errors.
- Controlled-vocabulary individuals documented in `SCHEMA_REFERENCE.md` exist in generated JSON-LD.
- Dangling amendment and similarity edges are repaired or explicitly classified as unresolved.

### P2: Repair Scripts Normalize Data That Generators Would Re-break

Problem:
Some generated artifacts are valid only because repair scripts normalized them after the fact. The source generator can still emit shapes that the validator rejects, so rerunning the generator can recreate old invalid JSON-LD even though the checked-in output is currently normalized.

Evidence:

- `scripts/generate_kars_eriosa_jsonld.py:141-154` emits class and ontology nodes with string `@type` values.
- `scripts/generate_kars_eriosa_jsonld.py:159-164` uses unprefixed properties such as `hasChapter`.
- `scripts/generate_kars_eriosa_jsonld.py:186-197` emits unprefixed `sectionNumber` and `legalText`.
- `scripts/fix_all_issues.py:52-82` contains repair-only normalizers for `@type`, multi-valued properties, and source shapes.
- `scripts/fix_all_issues.py:117-128` applies those repairs to generated documents after loading them.

Plan:

1. Move JSON-LD shape normalization into the generators, not post-hoc repair scripts.
2. Add generator tests that validate fresh in-memory output before writing files.
3. Retire or clearly label one-off repair scripts that should not be part of the normal pipeline.
4. Add a check that every documented generator can run on a fixture and produce validator-clean output.

Acceptance:

- Fresh generator output passes `validate_all.py` without needing `fix_all_issues.py`.
- Repair scripts are not required for ordinary regeneration.
- Deprecated one-off scripts are documented or removed from the active workflow.

### P2: Draft Impact Pipeline Has A Field-Shape Contract Mismatch

Problem:
The draft-legislation generator writes `estleg:affectedLawName` as language-tagged JSON-LD value objects, but the impact extractor treats each affected-law entry as a plain string and passes it to string normalization. The checked-in combined draft file currently contains plain strings, so the mismatch is latent until `generate_draft_legislation.py` is rerun before `extract_draft_impact.py`.

Evidence:

- `scripts/generate_draft_legislation.py:464-465` writes affected law names as `{"@value": law, "@language": "et"}` objects.
- `scripts/extract_draft_impact.py:94-100` normalizes affected names by calling `.lower()` on the input.
- `scripts/extract_draft_impact.py:322-332` forwards list entries directly to `resolve_law_name()` without unwrapping `@value`.
- Current `krr_outputs/eelnoud/eelnoud_combined.jsonld` uses list-of-string values, which masks the current generator/extractor mismatch.

Plan:

1. Pick one canonical `estleg:affectedLawName` shape.
2. Update both generator and extractor to use that shape.
3. Add a small fixture test that runs generated draft nodes through impact extraction.
4. Add validation for `affectedLawName` shape.
5. If language maps are retained, add a helper to unwrap language-tagged values consistently.

Acceptance:

- Running `generate_draft_legislation.py` followed by `extract_draft_impact.py` works without manual normalization.
- The checked-in draft combined file matches the generator's documented output shape.
- Tests cover both single and multiple affected-law names.

### P2: Institution Canonicalization Splits The Same Authority Into Multiple Nodes

Problem:
Institution extraction creates separate institution entities for punctuation and casing variants of the same authority. This fragments competence counts and makes downstream queries miss equivalent institutions unless they know every spelling variant.

Evidence:

- A normalized-label scan of `krr_outputs/institutions/` found duplicate groups for `politsei_ja_piirivalveamet`, `keskkonna_ja_kommunaalamet`, and `kultuuri_ja_spordiamet`.
- Current files include `institution_politsei___ja_piirivalveamet.json`, `institution_politsei__ja_piirivalveamet.json`, and `institution_politsei_ja_piirivalveamet.json`.
- `scripts/extract_institutional_competence.py:93-98` turns spaces and hyphens into underscores but does not collapse repeated separators.
- `scripts/extract_institutional_competence.py:101-134` only canonicalizes a small abbreviation/inflection map and otherwise lowercases the raw suffix.

Plan:

1. Normalize institution labels through a stronger canonicalization step that collapses punctuation, repeated separators, casing, and whitespace.
2. Add an authority alias table for known variants.
3. Merge existing duplicate institution files and update `estleg:competentAuthority` references.
4. Add a validation check for duplicate normalized institution labels.
5. Add tests for hyphen/space variants such as `Politsei- ja Piirivalveamet`.

Acceptance:

- One canonical node exists for each real-world institution.
- Existing competence links point to the canonical node or explicit aliases.
- Duplicate normalized institution labels fail validation.

### P2: Procedural And No-Body Acts Are Dropped Instead Of Modeled

Problem:
The ontology claims broad coverage of enacted laws and domestic regulations, but generators skip laws with no paragraphs and regulations with no parseable body. Ratification, procedural, amendment-only, and other no-body acts can still be legally meaningful, especially for provenance, temporal, and treaty coverage. Dropping them entirely creates a coverage gap that cannot be distinguished from source unavailability.

Evidence:

- `README.md:3-5` describes comprehensive coverage of enacted laws and domestic regulations.
- `metadata.jsonld:280-285` describes all enacted Estonian laws in the combined distribution.
- `scripts/generate_all_laws.py:841-846` skips no-paragraph laws as likely ratification or procedural laws.
- `scripts/generate_regulations.py:474-478` skips pure procedural or amendment-only regulations with no body.

Plan:

1. Generate act-level stub nodes for no-body legal acts instead of skipping them.
2. Add an explicit `estleg:contentStatus` or similar field for `noStructuredBody`, `proceduralOnly`, or `amendmentOnly`.
3. Include skipped/stubbed act lists in indexes and generation manifests.
4. Add validation that source acts are either modeled with provisions, modeled as documented no-body stubs, or listed as failed fetches.
5. Update public coverage statements to distinguish provision-level coverage from act-level coverage.

Acceptance:

- No legally valid source act disappears solely because it lacks paragraph nodes.
- Consumers can tell the difference between no-body acts, parse failures, and omitted sources.
- Coverage counts align with the stated ontology scope.

## Recommended Order

1. Fix invalid `xsd:date` generation and add typed-date validation.
2. Add registry drift validation and repair or explicitly classify current stale index entries.
3. Expand CI to run pytest and JSON hygiene for script changes.
4. Split SHACL into buckets and wire a fast subset into CI.
5. Fix similarity state-regulation participation.

This order turns silent data corruption into failing checks before widening semantic validation. It also avoids making the current slow full-corpus SHACL run a blocking CI path before it is decomposed.

## Verification Plan

Run after implementation:

```bash
python3 -m pytest -q
python3 scripts/validate_all.py
python3 scripts/shacl_validate_all.py --bucket laws
python3 scripts/shacl_validate_all.py --bucket kov
python3 scripts/shacl_validate_all.py --bucket riigikohus
python3 scripts/shacl_validate_all.py --bucket drafts
python3 scripts/shacl_validate_all.py --bucket eurlex
python3 scripts/shacl_validate_all.py --bucket curia
```

For release-level verification, run the full corpus SHACL command locally with an explicit timeout and capture the report artifact.

## Deliverables

- Updated validators and tests.
- Repaired temporal literals in generated KOV files.
- CI workflow that covers scripts, tests, SHACL, metadata, and output changes.
- SHACL bucket validation entry point.
- Similarity pipeline correction and coverage counters.
- Updated `docs/VALIDATION_REPORT.md` with current counts and known residual risks.
