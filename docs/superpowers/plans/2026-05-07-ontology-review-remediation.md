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
