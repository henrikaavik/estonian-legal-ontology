<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Test and lint baseline — estleg-w3id (main @ c96577d50c, 2026-09-03)

Environment: repo `.venv`, Python 3.14.3, pytest 9.1.1, ruff 0.16.3, mcp 1.29.0,
pyshacl 0.31.0, rdflib 7.6.0, pytest-cov 7.1.0. No xdist / timeout / randomly
plugins installed. CI (`.github/workflows/validate.yml`) runs Python 3.12.
All LFS artifacts are materialised locally (`combined_ontology.jsonld` = 304 MB
real JSON, `reports/similarity_index.json` = 62 MB), so local results are the
*best case*; CI's default `pytest` job runs **without** LFS.

Raw logs: `../pytest-full.log`, `../pytest-corpus.log`, `../pytest-mcp.log`,
`../pytest-cov.log`, `../coverage.json` (same scratchpad dir).

## Baseline results

### ruff — 7 findings (all in `mcp_server/estleg_mcp/data.py`, all auto-fixable)

```
mcp_server/estleg_mcp/data.py:297:17: RUF023 [*] `LawRecord.__slots__` is not sorted
mcp_server/estleg_mcp/data.py:821:5: FURB188 [*] Prefer `str.removesuffix()` over conditionally replacing with slice.
mcp_server/estleg_mcp/data.py:982:12: FURB188 [*] Prefer `str.removeprefix()` over conditionally replacing with slice.
mcp_server/estleg_mcp/data.py:1175:16: FURB188 [*] Prefer `str.removesuffix()` over conditionally replacing with slice.
mcp_server/estleg_mcp/data.py:1398:17: RUF023 [*] `RegulationRecord.__slots__` is not sorted
mcp_server/estleg_mcp/data.py:1451:12: FURB188 [*] Prefer `str.removesuffix()` over conditionally replacing with slice.
mcp_server/estleg_mcp/data.py:1576:12: FURB188 [*] Prefer `str.removeprefix()` over conditionally replacing with slice.
Found 7 errors.
```

Cause confirmed: `mcp_server/pyproject.toml` has a `[tool.ruff]` table
(`line-length = 100`, `target-version = "py311"`) but **no `[tool.ruff.lint]
select`**. Ruff resolves the nearest `pyproject.toml` per file, so files under
`mcp_server/` never see the root pin `select = ["E4","E7","E9","F"]` (root
`pyproject.toml` even carries a comment saying ruff 0.16 broadened the defaults).
The CI `lint` job fails with the same "Found 7 errors" (run 33391090500,
2026-08-31).

### pytest (default tier) — green locally, red in CI

| metric | local (this run) | CI `pytest` job, main, 2026-08-31 |
|---|---|---|
| collected | 4,095 | 4,095 |
| passed | **4,033** | 4,017 |
| failed | **0** | **6** |
| skipped | **62** (= exactly the 62 `corpus`-marked items) | 72 |
| warnings | 173 | 173 |
| pytest time | 121.91 s | 152.03 s |
| wall (`time`) | **2:02.64** (93.6 s user, 18.2 s sys, 91 % CPU → single-core bound) | — |

Command: `time python3 -m pytest -q -p no:cacheprovider --durations=15`, exit 0.

CI failures on main that do **not** reproduce locally (all environment-coupled):

- `test_issue_379_map_preference.py::test_committed_annotations_do_not_target_osa_parts`,
  `test_issue_417_eurlex_combined.py::test_committed_eurlex_combined_matches_directive_peep_transposition`,
  `test_issue_459_annotations.py::test_committed_sidecar_is_one_node_per_document`
  — `JSONDecodeError: Expecting value: line 1 column 1` = they `json.load` an
  LFS **pointer** (`annotations/oiguskantsler_seisukohad.jsonld`,
  `eurlex/eurlex_combined.jsonld`) without a pointer guard or `corpus` marker.
- `test_issue_435_ontology_typing.py::test_combined_has_no_ontology_act_dual_types` — `assert 0 > 0`
  (scans `combined_ontology.jsonld` pointer, finds no `@type` arrays).
- `test_issue_557_harmonisation_drafts.py::test_combined_harmonisation_stub_has_ee_edge` — `assert -1 >= 0` (same: pointer).
- `test_migrate_uris.py::TestRealMigrationStateFile::test_apply_command_recognises_already_migrated_when_in_sync`
  — "Dry-run report not found". Passes locally **only** because the git-ignored
  `data/uri_migration_report.json` exists on this machine.

Warnings (173): rdflib `ConjunctiveGraph`/`Dataset.default_context` deprecations
(bulk), and `PytestRemovedIn10Warning: Class-scoped fixture defined as instance
method` at `tests/test_historical_municipalities.py:162` and `:292` (will break on
pytest 10).

### Coverage (extra run, `--cov=estleg`) — 75.6 % lines

30,715 statements / 23,209 covered across 103 modules in `src/estleg`; suite
took 2:08 with coverage. Four modules at 0 % (`generate_schemas_from_cv`,
`stamp_combined_dataset_heads`, `migrate_namespace`,
`retarget_vos_harmonisation_578b`); core generators 83–89 %
(`generate_all_laws` 88.5, `generate_court_decisions` 86.2,
`generate_regulations` 83.5), `validate_all` 78.2 % with 406 uncovered
statements (the largest gap), `extract_cross_references` 73.3 %,
`generate_eu_legislation` 66.6 %, `generate_harmonisation_links` 44.3 %.
No coverage gate exists in CI.

### `pytest -m corpus` (opt-in real-corpus tier) — 5 failed / 56 passed / 1 skipped

Command: `time python3 -m pytest -q -p no:cacheprovider -m corpus -rs --durations=10`;
61.30 s pytest, **1:06.56 wall**, exit 1. LFS artifacts were real files (not pointers),
so these are genuine data/test disagreements, not environment gaps:

1. `tests/test_documented_examples.py::test_documented_sparql_returns_rows[API_GUIDE.md:EuroVoc subject classification (reverse)]`
   — `API_GUIDE.md L426 returned 0 rows — the documented query no longer matches the shipped corpus (#506)`.
2. `tests/test_no_legacy_namespace.py::test_no_legacy_data_riik_ee_namespace`
   — reported the retired namespace in `src/estleg/migrate_namespace.py`
   (hostname documented in [NAMESPACE_MIGRATION.md](../../NAMESPACE_MIGRATION.md))
   (the migration script itself contains the literal; CI `json-validation` job fails on the same check).
3. `tests/test_real_corpus_invariants.py::test_combined_graph_is_closed`
   — `combined_ontology.jsonld: 2409 stub node(s) carry disallowed estleg: object refs …
   node(s) absent from combined across 3 subdir(s) — the overlay layer did not fully merge into combined (rebuild combined)`.
4. `tests/test_type_entailment.py::test_combined_materializes_parent_types`
   — `no LegalProvision_<slug> instance found in combined`.
5. `tests/test_type_entailment.py::test_tbox_axioms_back_the_materialized_types`
   — `estleg:NationalRegulation rdfs:subClassOf estleg:DomesticRegulation != estleg:Act`
   (T-Box gained an intermediate `DomesticRegulation` class; test expectation stale).

Skipped: `test_fix_similarity_perfect_edges_581.py:323` (kov_similarity_index.json not present).

### `pytest mcp_server` — runnable; 19 failed / 69 passed / 1 skipped

`mcp` 1.29.0 imports fine. Command: `time python3 -m pytest -q -p no:cacheprovider mcp_server -rs --durations=10`;
7.81 s pytest, 8.79 s wall, exit 1. **Identical failure set in CI** (`estleg-mcp tests` job, run 33391090500).

All 19 failures are provision lookups on KarS (the fixture law of the contract tests):

| test | first error line |
|---|---|
| `test_data.py::test_search_eurovoc_english_label_hits_estonian_keyword` | `assert 'karistusseadustik' in {...}` |
| `test_data.py::test_provision_nodes_nonempty` | `assert 0 > 10` |
| `test_data.py::test_find_provision_by_paragraph` | `assert None is not None` |
| `test_data.py::test_find_ordinal_provision_in_kars` | `assert None is not None` |
| `test_data.py::test_rt_url_strips_xml_and_is_riigiteataja` | `assert False` |
| `test_tool_contracts.py::test_search_laws_contract_fields_and_recall` | `assert False` |
| `test_tool_contracts.py::test_get_law_contract_fields` | `assert False` |
| `test_tool_contracts.py::test_get_provision_contract_fields` | `missing documented fields {...} in {'note': "No § matching '§ 13' found in Karistusseadustik…"}` |
| `test_tool_contracts.py::test_get_provision_truncates_long_text` | `KeyError: 'legal_text'` |
| `test_tool_contracts.py::test_get_provision_full_text_flag_skips_cap` | `KeyError: 'legal_text'` |
| `test_tool_contracts.py::test_who_references_contract_fields` | `KarS has incoming references in the corpus` |
| `test_tool_contracts.py::test_references_of_contract_fields` | `KarS references something` |
| `test_tool_contracts.py::test_court_decisions_contract_fields_and_citation` | `KarS is interpreted by Riigikohus decisions in the corpus` |
| `test_tool_contracts.py::test_court_decisions_limit_cap_and_empty` | `assert 0 == 3` |
| `test_tool_contracts.py::test_sanctions_for_law_contract_fields_and_citation` | `assert False` |
| `test_tool_contracts.py::test_competent_authority_contract_fields_and_ranking` | `KarS names competent authorities in the corpus` |
| `test_tool_contracts.py::test_get_provision_as_of_selects_historical_redaction` | `KarS §13 must have multiple recorded redactions` |
| `test_tool_contracts.py::test_get_provision_without_as_of_is_unchanged` | `missing documented fields {...}` (same note) |
| `test_tool_contracts.py::test_provision_history_ordered_timeline_and_fields` | `KarS §13 has recorded redactions in the corpus` |

Probable root cause (evidence, not a fix): commit **c5625a748b (2026-08-19)
"Type provisions as LegalProvision and drop per-document classes"** retyped every
provision in the committed peeps to bare `estleg:LegalProvision` (KarS osa1: 102
such nodes, 0 `LegalProvision_*`; PKS: 227 / 0). `mcp_server/estleg_mcp/data.py:758`
`_is_provision()` still accepts only types starting with `estleg:LegalProvision_`
(its docstring says the bare class "is never stamped on instances"), so
`provision_nodes()` returns `[]`. Corpus-tier failure 4 above is the same drift
seen from the test side. The MCP tests were last touched 2026-08-18, one day before
the retyping. Likely affects every law, not just KarS (unverified; only KarS is exercised).

### CI status of `main` (read-only `gh run list`)

All 20 most recent `Validate Ontology` runs on `main` (2026-08-19 → 2026-08-31)
are **failure**; no green run is visible in that window. Latest run 33391090500:

- failed: `lint`, `pytest`, `estleg-mcp tests`, `json-validation`,
  `semantic-validation (curia | sidecars | riigikohus | drafts | kov | laws)`,
  `Seadusloome zero-warning gate`
- passed: `RT content-staleness canary`, `Docs lint`, `Generator smoke`,
  `semantic-validation (eurlex)`, `Integration DAG dry-run`

`git status krr_outputs` is clean after all local runs (the corpus-mutating tests
noted below did not write this time).

## Slowest tests

Default tier (`--durations=15`):

| s | test |
|---|---|
| 10.28 | `test_normalize_sup_markup_subcorpus.py::test_main_idempotent_on_real_corpus` |
| 9.83 | `test_issue_557_harmonisation_drafts.py::test_committed_draft_law_naming_coverage` |
| 8.62 | `test_migrate_uris.py::TestRealMigrationStateFile::test_apply_command_recognises_already_migrated_when_in_sync` |
| 6.91 | `test_issue_532_version_freshness.py::test_committed_sidecars_cover_their_peep_kehtiv` |
| 4.15 | `test_migrate_uris.py::TestRealMigrationStateFile::test_corpus_hash_is_consistent_with_apply_fast_path` |
| 3.59 | `test_extract_sanctions.py::TestCorpusInvariant::test_every_has_sanction_provision_has_exactly_one_enforced_at_level` (`slow`) |
| 3.28 | `test_strip_unknown_subsection_numbers.py::test_main_runs_against_real_corpus_idempotently` |
| 2.95 | `test_issue_392_riigikohus_ecli.py::test_committed_jsonld_has_no_unused_rdf_context_line` |
| 2.19 | `test_issue_507_index_coverage.py::test_unmarked_empty_matches_classify_and_baseline` |
| 2.15 | `test_issue_520_521.py::test_committed_combined_has_520_521_edges` |
| 2.10 | `test_generate_inverse_references.py::TestActLevelIndex::test_act_level_iri_resolves_in_iri_to_file` |
| 2.09 | `test_enrich_kov_layer1.py::TestParallelKovEnrichment::test_workers_two_produces_same_result_as_serial` |
| 2.08 | `test_enrich_kov_layer1.py::TestLoadLawPathsFailThreshold::test_at_or_below_threshold_passes` |
| 1.91 | `test_validate_all.py::test_subcorpus_index_counts_real_corpus_matches` |
| 1.88 | `test_issue_490_combined.py::test_combined_stub_required_paths_are_present` |

13 of the 15 are full sweeps of the real `krr_outputs/` tree (1,195 root peeps,
11,059 KOV + 3,812 riik regulation peeps, 26,876 files, 3.3 GB) sitting in what is
nominally the unit tier; together they account for roughly 50 s of the 122 s.

Corpus tier top: `test_check_numeric_identity_strings::test_main_passes_against_real_corpus` 5.1 s,
documented/README SPARQL examples 2–5 s each (rdflib parse of subcorpora),
`test_provision_version_intervals_do_not_overlap` 4.7 s, `test_combined_graph_is_closed` 4.1 s.
MCP top: `test_regulations_for_law_fields_and_citation` 4.66 s (cold corpus load), rest < 1.4 s.

## Test architecture assessment

**Shape.** `tests/`: 192 test modules + `conftest.py` + `__init__.py`, 74,205 lines,
4,095 collected tests. Naming: **82** files are `test_issue_NNN_*.py`, a further
**20** are feature-named with a trailing issue number (`test_fix_amendment_dates_587.py`),
~90 are behaviour/feature-named (`test_generate_all_laws.py`, 4,714 lines / 185 tests;
`test_validate_all.py`, 3,020 lines / 150 tests). `mcp_server/tests/`: 6 modules,
1,364 lines, 89 tests, no conftest; every corpus-backed module self-skips via
`pytestmark = skipif(not corpus_root())`.

**conftest.py (160 lines).** Registers `slow` (3 uses; run *by default*, each a
full-corpus sweep that `pytest.fail`s if `krr_outputs/` is empty) and `corpus`
(15 decorators in 13 files → 62 items; auto-skipped unless `-m` contains the
substring `"corpus"`). Provides `isolated_krr` (69 lines, rebinding every
`*_DIR` attribute of a script module under `tmp_path`) — **used by zero tests**;
every module hand-rolls `tmp_path / "krr_outputs"` + `monkeypatch.setattr` instead
(103 files use `tmp_path`). An autouse fixture clears `validate_all.errors/warnings`
around every test, which is the only systematic protection against module-global
state leakage (there are 51 other `module.global.clear()` calls and 58 references
to `generate_all_laws._used_prefixes` scattered through tests).

**Fixture strategy: mostly synthetic, with an unlabelled real-corpus fringe.**
The overwhelming majority of tests build one-to-five-node JSON-LD graphs or
inline `ET.fromstring` XML in `tmp_path`. `tests/fixtures/` is 76 KB (19 files):
three ~2 KB RT `määrus` XML samples, small KOV/RK peeps, one collision pair. On top of
that, **29 test files anchor `Path(__file__)…/krr_outputs` directly** and read the
real committed peeps, `controlled_vocabulary.jsonld`, `INDEX.json`, institutions,
provision-version sidecars — without the `corpus` marker. That is the tier the
`test_real_corpus_invariants.py` docstring calls "the first real-artifact
assertions the default suite has ever carried", and it is where the CI red comes
from: 14 of those files read one of the 10 git-LFS artifacts with **no pointer
guard** (see Weaknesses). The default "unit" tier therefore has three implicit
sub-tiers (synthetic / committed-non-LFS / LFS) that nothing labels.

**Legal-content fidelity vs. structure.** Structural assertions dominate (types,
IRI shapes, field presence, closed value sets, counts, SHACL conformance of
hand-built graphs). Legal-content checks exist but are thin:

- `tests/golden_facts/published_facts.json`: **25** hand-checked (file, node,
  field, expected) rows — KarS §121 "Kehaline väärkohtlemine" + its two sanctions,
  KarS §1/§2, PKS §1, HMS §95 as a `NormType_Definition`, KLIM23-1259 draft as
  `DraftIntent`, one repealed KOV act, one `contentHash` for KarS osa1, one T-Box
  subclass axiom.
- `test_real_corpus_invariants.py`: 16 default + 2 corpus tests, mixing the same
  golden facts with cross-artifact invariants (INDEX consistency, transposition
  inverse, version-interval non-overlap).
- `test_documented_examples.py` / `test_readme_sparql_examples.py` (corpus tier):
  every fenced SPARQL/Python block in `docs/API_GUIDE.md`, `docs/SCHEMA_REFERENCE.md`
  and README must return >0 rows / not raise — a good "docs don't rot" gate,
  currently failing on one query.
- `test_rt_schema_canary.py`: committed KarS RT XML cache still has the local-names
  the generator keys on.

No test takes a real Riigi Teataja XML for a real law and checks the generated
peep against it end-to-end (section titles, § numbering, subsection text,
amendment dates). Generator tests feed synthetic XML snippets, so parser
regressions that keep the *shape* but change the *text* would pass.

**Golden-file / snapshot tests for generator output: effectively none.** The
closest are `test_historical_municipalities::test_matches_regenerated_doc`
(regenerate one small artifact and compare) and the single `contentHash` golden
fact. There is no committed "expected peep" for any law, regulation, court
decision, draft, or EU act, and no normalised-diff harness. The `test_generate_*`
modules (13 of them, ~17k lines) are regression tests keyed to individual issues.

**Precision/recall of heuristic layers: infrastructure exists, data does not.**
`src/estleg/eval_harness.py` (#617) computes coverage / co-occurrence /
cross-ref resolution and has an `evaluate_gold_set` path; `test_eval_harness.py`
verifies the P/R arithmetic on a 2-item synthetic gold set. The only real gold
set, `eval/gold_sets/targetGroup.json`, has **0 items**, and the harness "never
fails on a low number — it is a report, not a gate". So the openly approximate
layers (cross-references "~50 % resolve", EuroVoc, deontic/normativeType,
targetGroup, sanctions, institutional competence, court→provision links) ship
with no measured accuracy and no floor.

**Runtime.** 2:02 wall for the default tier at 91 % of one core; ~50 s is real-corpus
iteration; no `pytest-xdist`, no `pytest-timeout`. Corpus tier +1:06, MCP +9 s,
coverage run 2:08. CI `pytest` job took 2:32.

**Flakiness risks.** Network: no global socket guard; each test that touches
`requests.get` monkeypatches it individually (good discipline, no safety net —
`test_http_allowlist_558.py`, `test_riigiteataja_common.py`, generator tests).
Time: `date.today()`/`datetime.now()` appear in ~9 tests, mostly as year bounds
(`test_fix_amendment_dates_587.py:270`, `test_generate_amendment_history.py:1275`)
and one equality on today's date (`test_fix_all_issues.py:525`) — low risk but
midnight-crossing sensitive. Ordering: module-global mutation (above) and
`os.chdir` in `test_documented_examples.py:506` (try/finally, acceptable).
Environment: `TestRealMigrationStateFile` depends on a git-ignored local report.
Data mutation: see W1.

**pythonpath smell.** `pyproject.toml` sets
`pythonpath = [".", "src", "scripts/archive", "examples"]`. Five test modules import
archived one-shots by bare name (`backfill_eu_provenance`, `fix_duplicate_ids` ×2,
`migrate_multipart_iri_scheme`, `rederive_court_case_types`) and one imports
`examples/quickstart`. Yes, this is a smell: "archive" scripts are kept alive as
tested API, bare-name imports bypass the `estleg` package namespace (`test_issue_472`
even polices `sys.path` hacks elsewhere), and `scripts/archive/` can shadow or
duplicate `src/estleg/` module names. Either the scripts are live (move back under
`src/estleg`) or they are archived (drop the tests with them).

**Would a public body gain confidence that a regenerated corpus is correct?**
Only partially. In its favour: a large regression suite, an explicit real-corpus
tier with LFS-aware CI jobs, SHACL shapes with closed value sets, 25 published
golden facts, a docs-examples gate, and a fitness harness. Against it:
(1) `main` has been red for its entire visible CI history (20 runs, ≥12 days),
so the gates are not currently gating anything; (2) the 2026-08-19 provision
retyping shipped while the MCP contract tests and the entailment tests failed;
(3) "green pytest" is weak by design — real-data checks are opt-in and the default
tier admits it "has never said anything about the shipped graph"; (4) legal
correctness is sampled at 25 facts with no provenance of who verified them and
no source-to-output fidelity test; (5) heuristic-layer accuracy is unmeasured.
A regenerated corpus that dropped every § title, halved cross-reference recall,
or mis-tagged target groups would pass the default tier.

## Weaknesses / risks

Severity: H = blocks trustworthy release gating; M = correctness/maintenance risk; L = hygiene.

| # | where | issue | sev |
|---|---|---|---|
| W1 | `tests/test_normalize_sup_markup_subcorpus.py:116`, `tests/test_strip_unknown_subsection_numbers.py:129` | Default-tier tests call `mod.main([])` — the migration scripts with `--dry-run` **off** — against the real `krr_outputs/` (both scripts default `KRR_DIR` to the repo tree, no monkeypatch). A test can rewrite committed legal data as a side effect; it then fails ("normalized: 0" expected), but the tree is already mutated. | H |
| W2 | `tests/test_issue_379_map_preference.py`, `test_issue_417_eurlex_combined.py`, `test_issue_459_annotations.py`, `test_issue_435_ontology_typing.py:120`, `test_issue_557_harmonisation_drafts.py`, plus `test_deprecate_legacy_statutes.py`, `test_fix_all_issues.py`, `test_issue_463_eurovoc_overlay.py`, `test_issue_543_bridges.py`, `test_issue_548_manifest.py`, `test_issue_559_tabular.py`, `test_issue_docs_and_pins.py`, `test_migrate_multipart_iri_scheme.py`, `test_ontology_version.py`, `test_release.py`, `test_run_all_integration.py`, `test_validate_combined_standalone.py`, `test_validate_seadusloome_sync.py`, `test_issue_474_sparql.py`, `test_classify_eurovoc.py`, `test_issue_471_reports.py`, `test_issue_544_eurovoc_skos.py`, `test_generate_similarity_index.py`, `test_issue_462_demote.py`, `test_repo_hygiene_538_539.py` | Reference one of the 10 LFS artifacts with **no** `_is_lfs_pointer` / `corpus` guard. Five of them are exactly the CI `pytest` failures on main. Local green ≠ CI green. | H |
| W3 | `mcp_server/estleg_mcp/data.py:758` vs corpus commit c5625a748b; `tests/test_type_entailment.py:221,256` | Corpus/consumer contract drift: provisions retyped to bare `estleg:LegalProvision`; MCP layer and entailment tests still require `LegalProvision_<slug>`. 19 MCP + 2 corpus failures; the public MCP endpoint's provision tools are presumably broken for every law. | H |
| W4 | CI, `main` | Every visible run (20, 2026-08-19 → 2026-08-31) failed across lint / pytest / mcp / json-validation / 6 semantic-validation buckets / seadusloome gate. Red-main normalises ignoring the gates. | H |
| W5 | `eval/gold_sets/targetGroup.json` (0 items); no gold sets for other layers; `eval_harness` non-blocking | Heuristic layers have no measured precision/recall and no floor. | H |
| W6 | `tests/golden_facts/published_facts.json` (25 rows) | Only legal-content fidelity check; tiny, no provenance (who verified, against which RT redaction/date), no negative cases. | M |
| W7 | all `test_generate_*` | No committed source-XML → expected-peep golden pairs; no normalised diff harness; generator text fidelity untested end-to-end. | M |
| W8 | `tests/test_migrate_uris.py:1475–` (`TestRealMigrationStateFile`) | Passes only with git-ignored `data/uri_migration_report.json` present; fails in CI. Hidden environment dependence. | M |
| W9 | `tests/conftest.py:52` | `if "corpus" in selected_marks` is a substring test (`-m "not corpus"` also matches; harmless today, wrong by construction). | L |
| W10 | `tests/conftest.py:65–133` | `isolated_krr` fixture is dead code (0 users); 103 modules re-implement it ad hoc. | L |
| W11 | `pyproject.toml` `pythonpath` incl. `scripts/archive`, `examples` | Archived one-shots kept on the import path and under test; bare-name imports. | M |
| W12 | ~13 of the 15 slowest tests | Full-corpus sweeps in the default tier (~50 s of 122 s); `slow` marker on only 3 of them. Suite single-process. | M |
| W13 | `tests/*` (no autouse socket guard) | No safety net against accidental live calls to riigiteataja.ee / EUR-Lex / SPARQL endpoints. | M |
| W14 | `tests/test_historical_municipalities.py:162,292` | Class-scoped fixtures as instance methods — `PytestRemovedIn10Warning`; will error on pytest 10. | L |
| W15 | `mcp_server/pyproject.toml` `[tool.ruff]` | No `lint.select` → ruff 0.16 default rules; drifts from the root policy; CI lint red. | L |
| W16 | 82 + 20 issue-numbered test modules | Test intent keyed to GitHub issue numbers; opaque to an external auditor without the tracker. | L |
| W17 | `src/estleg` coverage 75.6 %; 4 modules at 0 %; `validate_all` 406 uncovered statements | No coverage gate; the validator (the public body's main assurance tool) is the biggest gap. | M |

## Improvement ideas

1. **Get `main` green and keep it green.** Fix the 7 ruff findings and add
   `[tool.ruff.lint] select = ["E4","E7","E9","F"]` to `mcp_server/pyproject.toml`
   (or `extend = "../pyproject.toml"`); decide W3 (update `_is_provision` to accept
   bare `estleg:LegalProvision`, or revert the retyping) and refresh the entailment
   expectations; rebuild `combined_ontology.jsonld` (closure failure); fix the one
   rotted API_GUIDE query; make PR checks required. *Why:* a public body cannot cite
   a red pipeline as evidence of anything. Effort **M**, impact **H**.
2. **Single LFS-aware corpus accessor.** Add a `krr_artifact(rel)` helper/fixture that
   returns the path or `pytest.skip`s (and, for LFS files, requires the `corpus`
   marker); apply to the ~25 files in W2. Add a collection-time check that fails any
   unmarked test whose source mentions an LFS basename. *Why:* removes the
   local-green/CI-red split that hid the current failures. Effort **S**, impact **H**.
3. **Never mutate the real corpus from a test.** Change W1 to `main(["--dry-run"])`
   or run against a `tmp_path` copy; add a conftest guard that snapshots
   `git status --porcelain krr_outputs` before/after the session and fails on
   drift. *Why:* test runs must not alter the legal record. Effort **S**, impact **H**.
4. **Explicit three-tier layout with markers and CI budget.** `unit` (synthetic,
   target <60 s, run everywhere), `committed` (non-LFS peeps; today's unmarked
   real-corpus tests), `corpus` (LFS). Move the 13 slow real-corpus sweeps out of
   `unit`; run `unit` with `pytest-xdist -n auto` and `pytest-timeout`. *Why:*
   fast feedback plus a clearly named release gate. Effort **M**, impact **H**.
5. **Hand-adjudicated gold sets with a floor.** For each heuristic layer
   (targetGroup, normativeType, competentAuthority, cross-reference targets,
   EuroVoc, sanctions, court→provision links) sample ≥200 nodes with a documented
   sampling protocol, have a legal reviewer label them, commit under
   `eval/gold_sets/`, and make `eval_harness --gold-set` a CI gate with minimum
   precision/recall thresholds. *Why:* the layers are the product; today their
   accuracy is a guess. Effort **L**, impact **H**.
6. **Source-to-output fidelity goldens.** For 8–10 representative documents (KarS
   osa1, PKS, VÕS osa1, HMS, one riik regulation, one KOV regulation, one RK
   decision, one draft, one EU directive) commit the raw RT/EUR-Lex XML input and
   the expected peep; regenerate in test and compare with a normalised diff
   (ignore timestamps/hashes). *Why:* proves the parser preserves the legal text,
   not just the graph shape. Effort **M**, impact **H**.
7. **Grow and govern `published_facts.json`.** Target several hundred rows across
   all subcorpora, each with `verified_by`, `verified_on`, `source_url` and the RT
   redaction date; include negative facts (repealed §, removed sanction). *Why:*
   an auditable fidelity sample a ministry can sign off. Effort **M**, impact **M**.
8. **Determinism test.** Regenerate one law twice in `tmp_path` and assert
   byte-identical output; regenerate from the committed XML cache and compare
   `contentHash` with the committed peep. *Why:* reproducibility is a basic public
   data-quality requirement. Effort **S**, impact **M**.
9. **Network guard.** Autouse fixture that patches `socket.socket.connect` (or
   `pytest-socket`) so any un-mocked HTTP call fails loudly. Effort **S**, impact **M**.
10. **Coverage gate.** Publish `--cov` in CI, fail below 75 % now and ratchet;
    prioritise `validate_all` (406 uncovered statements) and the four 0 % modules
    (delete or test). Effort **S**, impact **M**.
11. **Retire the archive from the test path.** Remove `scripts/archive` and
    `examples` from `pythonpath`; either move the five archived scripts back into
    `src/estleg` or delete their tests. Adopt or delete `isolated_krr`. Effort **S**,
    impact **M**.
12. **Fix env-coupled tests.** `TestRealMigrationStateFile` must generate its own
    dry-run report in `tmp_path` rather than rely on a local git-ignored file.
    Effort **S**, impact **M**.
13. **pytest 10 readiness.** Convert the two class-scoped instance-method fixtures
    in `test_historical_municipalities.py`; fix the substring `-m` check in
    conftest. Effort **S**, impact **L**.
14. **Auditor-readable test map.** Keep issue numbers in docstrings but rename
    modules by behaviour, and generate a `docs/TEST_MAP.md` (marker, tier, what
    legal property it protects). Effort **M**, impact **L–M**.

## Open questions

- When was `main` last green? Only 20 runs are visible and all fail; is red-main
  an accepted interim state during the 2026-08-19 release burst, or unnoticed?
- Is the c5625a748b retyping (bare `estleg:LegalProvision`) the intended contract
  going forward? If so, the MCP data layer, `test_type_entailment.py`, and the
  `SCHEMA_REFERENCE` docs need to follow; if not, the corpus needs re-minting.
- Which CI job is *the* release gate for the public endpoint — is `-m corpus` on a
  tagged release mandatory, and who signs off on golden-fact changes?
- Are the three RT XML fixtures under `tests/fixtures/regulations/` verbatim
  Riigi Teataja documents (provenance/licence) or hand-edited?
- Is `scripts/archive/` meant to stay runnable and tested, or is it dead code
  awaiting deletion?
- Should the default tier include *any* real-corpus reads, or should "committed
  peeps" become its own marker so the unit tier is hermetic?
- Who owns the empty `eval/gold_sets/targetGroup.json` and is legal review
  capacity available to populate gold sets?
