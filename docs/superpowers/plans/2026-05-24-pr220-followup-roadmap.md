# PR #220 Follow-up Roadmap

Date: 2026-05-24
Repository: `henrikaavik/estonian-legal-ontology`
Source PR: [#220](https://github.com/henrikaavik/estonian-legal-ontology/pull/220) — Implement open issues remediation groundwork
Anchor commits: `bca698209` (original PR), `45a6bea59` (fix commit addressing review blockers)
Related plan: [2026-05-24-open-issues-remediation.md](2026-05-24-open-issues-remediation.md)

## Status Summary

Updated 2026-05-25 after PR #221 and PR #222.

PR #220 review identified 5 blockers and ~15 majors. The fix commit
`45a6bea59` closed all 5 blockers with code + test evidence and addressed most
majors. Re-review identified 9 new minor issues (N1–N9) and 15
still-outstanding items (O1–O15).

Current delivery status:

- PR #220 is merged.
- Phase 1.1 was delivered before the Phase 1 closeout; Phase 1.2–1.4 were
  delivered in PR #221.
- Phase 2.1–2.7 were delivered in PR #222 (`c3becc72b`).
- Phase 2.10/O5, O8, O9, and `jsonld_texts` test coverage were delivered in
  PR #222 follow-up commit `26960fb40`.
- Phase 2.8 and 2.9 remain deferred until the first live PDF probe yields real
  distribution data; tracked in #227.
- Remaining follow-up before Phase 3.4 is tracked in #227.

Baseline validation as of PR #222 follow-up commit `26960fb40`:

- `python3 -m ruff check scripts/ tests/` — pass
- `python3 -m pytest -q` — `1477 passed, 2 skipped`
- `python3 scripts/validate_all.py` — pass (expected missing-manifest warning)
- `python3 scripts/shacl_validate_all.py --all` — pass
- `python3 scripts/validate_seadusloome_sync.py --report seadusloome-shacl-report.ttl` — pass

PR #222 is ready to merge after this roadmap status update.

## Phase 1 — Pre-merge for PR #220

Historical plan for the `codex/open-issues-remediation` closeout before PR
#220 merged. Phase 1 is now complete.

### 1.1 Add `tests/test_generate_missing_parts.py` (blocking)

**Status:** Delivered before PR #221; Phase 1 closeout no longer blocks PR
#220.

The fix commit added cross-module imports of private helpers from
`generate_all_laws.py` (notably `_paragraph_id_suffix`) and new subsection-emission
paths, but did not add a test file. This is the only code change in the fix
commit without test coverage.

Implement ~50 lines, three tests:

1. `test_vos_part_emits_subsections_with_dedup_guard` — feed a `volaigusseadus_osa11`
   fixture with `loige` children; assert correct `estleg:hasSubsection` order; assert
   `seen_subsection_ids` raises on duplicate IRI.
2. `test_tsus_part_subsection_emission_uses_ascii_iri_prefix` — assert subsection
   IRIs use ASCII `TsUS_Osa1_*`, not `TsÜS_`.
3. `test_helpers_resolve_from_generate_all_laws` — assert
   `from scripts.generate_missing_parts import _paragraph_id_suffix, build_subsections, _iter_loiked, _loige_numbers, collect_full_text`
   succeeds and all five are callable.

### 1.2 Court cache-orphan cleanup (strongly recommended)

**Status:** Delivered in PR #221.

`_court_text_cache_path` in `scripts/generate_court_decisions.py:748-749`
changed filename shape from `{sanitize_id}.txt` to `{sanitize_id}_{digest}.txt`.
The 104 existing cache files (~2.2 MB) at `krr_outputs/.cache/court_decisions/`
no longer match the new shape, so the next `--fetch-full-text` run will re-issue
104 polite-rate-limited HTTP calls while orphans remain on disk forever.

Implement a one-time migration in `scripts/generate_court_decisions.py`. When the
cache directory is initialised:

```python
def _migrate_legacy_cache(cache_dir: Path) -> int:
    legacy = [
        p
        for p in cache_dir.glob("*.txt")
        if "_" not in p.stem or len(p.stem.rsplit("_", 1)[-1]) != 10
    ]
    for p in legacy:
        p.unlink()
    return len(legacy)
```

Print `cleaned N legacy court cache entries` on startup if N > 0.

Test: `test_legacy_cache_files_are_purged` writes two legacy-format files and one
new-format file in a tmp dir, asserts only the new-format file survives and the
return value is 2.

### 1.3 Warn on corrupt regen-state load (strongly recommended)

**Status:** Delivered in PR #221.

`scripts/generate_all_laws.py:1826-1834` currently catches
`json.JSONDecodeError` / `OSError` and returns empty state with no warning. An
overnight operator cannot tell the ledger was lost.

Implement:

```python
except (json.JSONDecodeError, OSError) as exc:
    print(
        f"WARNING: regen state {path} unreadable; starting fresh: {exc}",
        file=sys.stderr,
    )
    return {
        "schemaVersion": REGEN_STATE_SCHEMA_VERSION,
        "completed": {},
        "failed": {},
        # ... preserve other default fields
    }
```

Test: `test_corrupt_regen_state_warns_and_resets` writes `b"{not json"` to a tmp
file, calls `load_regen_state`, captures stderr via `capsys`, asserts the
warning string is present and an empty state dict is returned.

### 1.4 Complete `docs/VALIDATION_REPORT.md` cleanup table (strongly recommended)

**Status:** Delivered in PR #221.

The "Draft Reference Cleanup Notes" table at `docs/VALIDATION_REPORT.md:118-127`
lists 7–8 removed targets but the actual deletion set between `main` and
`45a6bea59` in `krr_outputs/eelnoud/eelnoud_combined.jsonld` is 10. Two
discrepancies:

- `estleg:TKS_Map_2026` — had 7 references; fix has 0, plus 6 `TarbKS_Map_2026`
  adds. One duplicate-array entry on draft "Ülikooliseaduse ja Tartu Ülikooli
  seaduse muutmise seadus" (which had `amendsLaw: [TKS_Map_2026, TKS_Map_2026]`)
  was stripped without replacement. The target now resolves as
  `TarbKS_Map_2026`.
- `estleg:TS_Map_2026` → `TõS_Map_2026` was a clean 1→0 / 0→1 rename, not a
  dead-reference removal.

Action: either add a row for `TKS_Map_2026` and a separate "Renamed targets"
sub-table for the `TKS_Map_2026 → TarbKS_Map_2026` and `TS_Map_2026 →
TõS_Map_2026` rewrites, or rewrite the prose to clarify that the table is
deletions-only and that array-de-duplication and renames happened separately.

### 1.5 Validate Phase 1

**Status:** Completed; PR #220 and PR #221 are merged.

```bash
python3 -m ruff check scripts/ tests/
python3 -m pytest -q
python3 scripts/validate_all.py
git push origin codex/open-issues-remediation
```

Then merge PR #220.

## Phase 2 — Hardening before live data runs

Separate PR(s) after #220 merges. Operational gaps that only bite during the
deferred multi-hour network jobs in Phase 3.

### 2.1 ProvisionVersion resume (O2 — blocks #208 live run)

**Status:** Interrupted-run resume delivered in PR #222. Schema,
freshness, and max-redactions hardening delivered in PR #229 (#223).

Delivered behavior:

- `--all` resumes from `provision_versions_report.json` by default.
- A row is skipped only when it has a supported `schemaVersion`, emitted output
  without errors or warnings, used a redaction cap compatible with the current
  run, and still matches the current Riigi Teataja redaction id / valid-from
  pair.
- `--max-redactions 0` is full-history mode; prior sample rows do not satisfy
  it.
- `--no-resume` / `--force` bypasses prior report state and processes every
  selected law.
- Freshness checks use the same polite fetch delay as the main ingestion loop.

### 2.2 `annotationText` boundary-aware truncation (O4 — affects #210 quality)

**Status:** Sentence-aware truncation delivered in PR #222. Estonian
citation-abbreviation false-boundary handling delivered in PR #230 (#224).

Delivered behavior:

- Long annotation text is truncated at a late sentence boundary where practical.
- Early sentence boundaries still fall back to a word boundary to preserve
  useful content.
- Period-space matches after Estonian legal citation / abbreviation tokens
  (`lg`, `p`, `vt`, `nt`, `jt`, `nn`) are not treated as sentence boundaries.
- Regression fixtures cover realistic fragments such as `§ 47 lg 1. p 3
  kohaselt` so truncation cannot produce dangling `§ 47 lg 1.` annotations.

### 2.3 Missing tests for regen-state edge cases (O11)

**Status:** Delivered in PR #222. Lower-risk code-hygiene and test-depth
follow-ups delivered in PR #231 (#228).

Add to `tests/test_generate_all_laws.py`:

- `test_corrupt_state_warns_and_resets` (also satisfies 1.3 above)
- `test_multipart_partial_write_records_failed_entry` — monkey-patch
  `write_law_output` to raise on 2nd osa, assert `state["failed"]` carries
  partial `outputPaths`
- `test_schema_version_mismatch_triggers_full_reset` — write state with
  `schemaVersion: 0`, verify all entries dropped
- `test_zero_subsection_warning_appended` — fixture XML with `<loige>` elements
  but generator emits 0 Subsections, assert the warning string

### 2.4 Court extraction test depth (O12)

**Status:** Delivered in PR #222.

Add to `tests/test_extract_court_provision_links.py`:

- `test_legalText_value_object_form` — node with
  `"estleg:legalText": {"@value": "...", "@language": "et"}` → extraction
  produces same citations as plain-string case
- `test_mixed_file_full_text_and_summary_only` — graph with one decision
  having full text and one with summary only → per-file stats split
  `legalText_citations_found` and `summary_fallback_citations_found` correctly

### 2.5 Recall-lift correctness (N5, N6)

**Status:** Delivered in PR #222. `docs/RELEASE.md` now documents
`full_text_recall_lift / decisions_with_summary_baseline` and the zero
denominator case.

`scripts/extract_court_provision_links.py:705-708`:

- Move the per-node recall-lift assignment OUT of the inner per-citation loop
  to after the `for node in graph` loop ends. Currently computes correct final
  value but does N redundant subtractions per file.
- Add `decisions_with_summary_baseline` counter, incremented only when summary
  is non-empty. Document the comparable metric in `docs/RELEASE.md` as
  `full_text_recall_lift / decisions_with_summary_baseline` so summary-less
  decisions do not skew the per-decision lift.

### 2.6 Negative max-redactions validation (N4)

**Status:** Delivered in PR #222.

`scripts/generate_provision_versions.py:798-802` `effective_max_redactions`
accepts negative values silently. `--max-redactions -1` would propagate to
`process_law` with surprising slice semantics.

Implement before `effective_max_redactions` is called:

```python
if args.max_redactions is not None and args.max_redactions < 0:
    parser.error("--max-redactions must be non-negative (0 = unbounded)")
```

### 2.7 `write_law_report` skip-on-no-op (O15)

**Status:** Delivered in PR #222.

`scripts/generate_provision_versions.py:711-740` overwrites the report
unconditionally. A `--limit 0` no-op or quick test run can clobber a prior
`--all` report.

Implement: skip the write when `results` is empty AND the file already exists.

### 2.8 Stratified PDF probe sampling (O13)

**Status:** Deferred until the first live PDF probe; tracked in #227.

`scripts/generate_annotations.py:115` `PDF_PROBE_SAMPLE_SIZE = 10` always picks
the most-recent 10 opinions. 2003-era scanned PDFs are never sampled.

Either implement page-stratified sampling (N from page 0, N from page 50, N
from page 200) or document the recency bias explicitly. Decision can be
deferred until after the first live probe — needs real data.

### 2.9 PDF quality-ratio re-tune (O14)

**Status:** Deferred until the first live PDF probe; tracked in #227.

`scripts/generate_annotations.py:117` `MIN_PDF_TEXT_VALID_CHAR_RATIO = 0.30`
is too loose — accepts text with 70% punctuation. For PDF body-text reference
resolution, 0.50–0.60 is closer to mojibake-vs-clean discrimination.

Defer until the first live probe yields real distribution data, then pick from
observed values.

### 2.10 Other latent items

**Status:** Delivered across PR #222 and PR #231 (#225/#228).

- **O5 — delivered in PR #222.** `jsonld_text()` and `jsonld_texts()` now
  accept `prefer_language`, and draft-impact title, affected-law, and initiator
  aggregation route through `prefer_language="et"`.
- **`jsonld_texts` unit tests — delivered in PR #222.** Tests cover untagged
  value passthrough, mixed-language lists, no-preference legacy behavior, and
  non-matching language filtering.
- **O8 — delivered in PR #222.** SHACL comments now describe the sidecar model
  and optional back-links accurately.
- **O9 — delivered in PR #222.** `docs/SCHEMA_REFERENCE.md` now separates the
  ProvisionVersion sidecar JSON example from the stable provision JSON example
  with lead-in prose.
- **N3 — delivered in PR #231.** `completed_regen_slugs` is a pure getter;
  `prune_completed_regen_state` performs the intentional mutation.
- **N8 — delivered in PR #231.** `state["droppedCompleted"]` is append-only
  history.
- **O6 — delivered in PR #231.** Bare `--regen-state` resolves lazily against
  the current `KRR_DIR` inside `main()`.
- **O7 — delivered in PR #231.** `--reset-regen-state` intentionally discards
  prior state and requires `--regen-state`.
- **O10 — delivered in PR #231.** The Seadusloome graph-closure gate derives
  exempt predicates from SHACL property shapes marked
  `estleg:graphClosureExempt true`.
- **N1 follow-up — retired in PR #231.** The cache filename shape introduced
  in PR #221 already includes the original sanitized case id plus a 10-hex SHA1
  suffix; no collision or parsing need has appeared. Revisit only if future
  cache tooling needs delimiter-preserving filenames.

## Phase 3 — Live data ingestion jobs

The originally-deferred network-heavy work. Each phase is a separate PR. Order
matters because of IRI churn — later phases consume identities established
earlier.

### 3.1 #213 — Full law corpus refresh

**Prerequisite:** Phase 1 complete (atomic state + tid checks landed).

**Run:**

```bash
python3 scripts/generate_all_laws.py \
    --refresh \
    --kehtiv 2026-05-24 \
    --regen-state krr_outputs/.regen_state.json
```

Per-law batches; resumable across crashes. Record progress per plan §#213.7:
`(law slug, fetched OK, source ID, output path, Subsection count emitted,
warnings)`.

**Post-run** — rerun dependent enrichments per plan §#213.8:

```bash
python3 scripts/generate_inverse_references.py
python3 scripts/extract_legal_concepts.py
python3 scripts/extract_cross_references.py
python3 scripts/extract_sanctions.py
python3 scripts/extract_court_provision_links.py
python3 scripts/generate_similarity_index.py
python3 scripts/extract_eurovoc.py
python3 scripts/generate_transposition_mapping.py
python3 scripts/extract_harmonisation.py
python3 scripts/fix_all_issues.py  # regenerate combined_ontology.jsonld
```

**Acceptance:**

- `python3 scripts/generate_all_laws.py --missing-only` is a no-op
- Root `*_peep.json` files contain materialised `estleg:Subsection` nodes
- `python3 scripts/shacl_validate_all.py --all` passes
- `python3 scripts/validate_seadusloome_sync.py` passes
- Idempotence audit shows no duplicate extractor-owned nodes; no stale
  `coversConcept` or cross-reference targets from pre-regen provision IRIs

### 3.2 #207 — Full Riigikohus full-text ingestion

**Prerequisite:** Phase 1.2 (cache-orphan cleanup) complete; Phase 3.1 done so
IRI churn is settled.

**Run:**

```bash
python3 scripts/generate_court_decisions.py --fetch-full-text --full-text-limit 0
python3 scripts/extract_court_provision_links.py
python3 scripts/generate_inverse_references.py
python3 scripts/shacl_validate_all.py --bucket riigikohus
```

**Acceptance:**

- Full-text coverage populated for every public decision page yielding usable
  text (currently 99 of 12,137 → target close to full corpus)
- `full_text_recall_lift` report shows new links found only because full text
  exists
- Stub pages and mojibake rejected per quality thresholds
- File counts stable; only file contents grow

### 3.3 #208 — Full ProvisionVersion ingestion

**Prerequisite:** Phase 2.1 resume hardening complete (#223 / PR #229); Phase
3.1 done.

**Run:**

```bash
python3 scripts/generate_provision_versions.py --all --max-redactions 0
```

Use Riigi Teataja cache; conservative throttling. Classify laws per plan
§#208.4 as snapshot-only, failed, or no-non-trivial-history.

**Validate:**

```bash
python3 scripts/shacl_validate_all.py --bucket sidecars
python3 scripts/validate_all.py
python3 scripts/validate_seadusloome_sync.py
```

**Acceptance:**

- Every law with non-trivial redaction history has ProvisionVersion output, or
  a documented failure / snapshot-only reason
- All `estleg:versionOf` and `estleg:supersededByVersion` references resolve in
  the public load surface
- `docs/SCHEMA_REFERENCE.md` SPARQL query pattern remains accurate
- Coverage report committed

### 3.4 #210 — Live Õiguskantsler probe + ingestion

**Prerequisite:** Phase 2.2 boundary-aware truncation complete in PR #230
(#224). Use the probe output to close #227 before committing to the live PDF
strategy.

**Step A — Probe** (small, fast):

```bash
python3 scripts/generate_annotations.py --probe-pdfs --pdf-probe-sample-size 10
```

Read `krr_outputs/reports/pdf_probe_report.json`. Decide pdfminer-only vs OCR
per plan §#210.1. If fewer than 80% produce usable text, evaluate `pytesseract`
or equivalent before committing to scrape.

**Step B — Full ingest** (multi-hour):

```bash
python3 scripts/generate_annotations.py --scrape --limit 0
python3 scripts/shacl_validate_all.py --bucket sidecars
```

**Acceptance:**

- Full Õiguskantsler archive scraped and cached under
  `krr_outputs/.cache/annotations/`
- Annotation links based on PDF body text when available
- Chosen PDF / OCR dependency justified by the 10-PDF probe
- `AnnotationShape` passes
- Reports surface low recall or unresolved references

## Phase 4 — Post-ingestion long-tail

### 4.1 #203 — Item / punkt modelling (soft-blocked on Phase 3.1)

Re-evaluate after Phase 3.1 lands. Per plan §#203 Decision:

> Do not model `alapunkt` in this issue. Implement `estleg:Item` only after
> Subsections exist in the corpus and a real consumer, such as an obligation
> extractor, needs punkt-level nodes. If no such consumer exists, close or mark
> the issue as deferred.

If a consumer emerges:

1. Add vocab: `estleg:Item`, `estleg:itemNumber`, `estleg:hasItem`,
   `estleg:parentSubsection`.
2. Add `estleg:ItemShape` with exactly one parent path (`parentSubsection` or
   `parentProvision`).
3. IRI scheme per plan §#203.4:
   - Item under subsection:
     `estleg:<ABBREV>[_Osa<N>]_Par_<par>_Lg_<lg>_P_<punkt>`
   - Item directly under provision:
     `estleg:<ABBREV>[_Osa<N>]_Par_<par>_Pp_<punkt>`
4. Add `build_items` in `scripts/generate_all_laws.py`.
5. Tests covering numeric / letter / superscript punkt IDs; punkt under
   `loige` vs directly under `paragrahv`; no-`alapunkt` emission.
6. Benchmark SHACL cost on KARIST sample; if `--bucket laws` runtime exceeds
   2× baseline, split Items into a separate validation bucket before full
   emission.

If no consumer: close #203 as deferred.

### 4.2 Final data-release sync

After Phases 3.1–3.4 land, designate one sync PR to update:

- `krr_outputs/metadata.jsonld` totals (`estleg:fileCount`,
  `estleg:totalFiles`)
- `README.md` corpus statistics (the `21,973` count and downstream numbers)
- `docs/VALIDATION_REPORT.md` metrics with one final benchmark run
- `docs/RELEASE.md` cumulative changelog entry

Per plan §"Release Strategy": "If an intermediate PR must touch counts to pass
a gate, record the expected delta so the final sync can reconcile it
deliberately."

### 4.3 Process improvements

- **Commit hygiene.** Future remediation PRs split code-only from data-regen
  commits. This PR's 13,048-file size violated plan §"Release Strategy"; the
  next remediation series should land code in one commit and bulk data
  regeneration in a follow-up commit on the same branch.
- **Per-issue commits within a PR.** A reviewer should be able to run
  `git log --oneline` and see "issue #X", "issue #Y" as separable commits,
  not a single squashed message with eight bullets.
- **Follow-up GitHub issues filed from PR #222 review:**
  - #223 — ProvisionVersion resume schema/freshness/max-redactions hardening
    (delivered in PR #229)
  - #224 — Estonian citation-abbreviation-aware annotation truncation
    (delivered in PR #230)
  - #225 — Remaining Phase 2 latent cleanup items (O6, O7, O10, N1, N3, N8)
    (delivered in PR #231)
  - #226 — Roadmap status update after Phase 2 (delivered in PR #231)
  - #227 — Annotation PDF probe sampling and quality threshold tuning
  - #228 — Lower-risk PR #222 code-hygiene/test-depth follow-ups
    (delivered in PR #231)

## Summary Matrix

| Phase | Items | Effort | Blocks |
|---|---|---|---|
| 1. Pre-merge | 1.1 tests, 1.2 cache cleanup, 1.3 warning, 1.4 doc table | Done (#220/#221) | Complete |
| 2. Hardening | 2.1–2.7 + 2.10/O5/O8/O9 done; #223 delivered in PR #229; #224 delivered in PR #230; #225/#226/#228 delivered in PR #231; 2.8–2.9 deferred to #227 | Mostly done (#222/#229/#230/#231) | Phase 3 live runs |
| 3. Live ingestion | #213, #207, #208, #210 (sequential, IRI-churn-ordered) | multi-day, network-bound | Phase 4 long-tail |
| 4. Long-tail | #203 (conditional), final sync, process | ad-hoc | — |

## Cross-Reference Index

### Findings IDs and origin

- **B1–B5** — Original PR #220 review blockers; all verified fixed in
  `45a6bea59`.
- **O1–O15** — Outstanding items from re-review after `45a6bea59`. Mapped to
  Phase 1 (O1, parts of O3) and Phase 2 (most others).
- **N1–N9** — New issues introduced by `45a6bea59`. Mapped to Phase 1 (N1,
  N2), Phase 2 (N3–N9), and the cleanup follow-ups in §4.3.
- **#203, #204, #207, #208, #210, #213, #217, #218, #219** — Original GitHub
  open-issues set from
  [2026-05-24-open-issues-remediation.md](2026-05-24-open-issues-remediation.md).
  All except #203 and the deferred-data jobs (#207, #208, #210, #213) closed
  in `45a6bea59` groundwork.
- **#223–#228** — Follow-up issues filed from PR #222 review. #223 is
  delivered in PR #229, #224 in PR #230, and #225/#226/#228 in PR #231. #227
  remains open for the live PDF probe sampling / threshold decision before
  Phase 3.4.

### Acceptance gates that must pass at end of each phase

After Phase 1:

```bash
python3 -m ruff check scripts/ tests/
python3 -m pytest -q
python3 scripts/validate_all.py
```

After each Phase 3 sub-phase (3.1, 3.2, 3.3, 3.4):

```bash
python3 -m ruff check scripts/ tests/
python3 -m pytest -q
python3 scripts/validate_all.py
python3 scripts/shacl_validate_all.py --all
python3 scripts/validate_seadusloome_sync.py --report seadusloome-shacl-report.ttl
```

After Phase 4.2 (final sync), additionally:

- Document the runtime of `validate_seadusloome_sync.py` against the expanded
  public surface in `docs/VALIDATION_REPORT.md` for future regression tracking.
- Confirm `metadata.jsonld`, `README.md`, and `docs/VALIDATION_REPORT.md` agree
  on every reported corpus count.
