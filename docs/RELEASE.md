# Release build DAG

`scripts/run_all_integration.py` owns the enrichment pipeline **and** the
release build. Its 30 steps form an explicit, declarative directed acyclic
graph (DAG) in four tiers: ingest (network fetches), enrichment (offline
corpus passes and aggregate rebuilds), build (the combined/INDEX rebuild) and
package (release assets). The runner topologically sorts it, runs it
(serially by default), then — in `--release` mode — runs the three release
validators and writes a release-wide manifest aggregating everything.

This document covers:

1. [The step DAG](#the-step-dag)
2. [Running a release build](#running-a-release-build) — `--release`
3. [Validating without rebuilding](#validating-without-rebuilding) — `--release --validate-only`
4. [The `release_manifest.json` schema](#the-release_manifestjson-schema)
5. [Release assets](#release-assets) — `build_release_assets.py`, `SHA256SUMS`, catalogue checksums
6. [Reproducibility gates](#reproducibility-gates) — `constraints.txt` and the `pipeline_version` gate
7. [Committed-vs-release-asset policy](#committed-vs-release-asset-policy) — which files live in git, which are regenerable build artifacts
8. [Versioning policy](#versioning-policy) — how the ontology version is set, stamped, and released

---

## Versioning policy

The published ontology is **versioned with SemVer** (`MAJOR.MINOR.PATCH`),
continuing the history in [CHANGELOG.md](../CHANGELOG.md) (Keep a Changelog
format). The version exists in **one source of truth** —
`estleg_common.ONTOLOGY_VERSION` — and `pyproject.toml`'s `[project].version`
is kept identical to it; `tests/test_ontology_version.py` fails CI if they
drift.

**Where the version is stamped.** On every build the version is written as
`owl:versionInfo` + `owl:versionIRI` onto two headers, so the shipped graph is
self-describing and a consumer can pin/cite it:

- `metadata.jsonld` — the `dcat:Dataset` / `owl:Ontology` dataset header
  (committed; bump by hand when you bump the constant). It is a DCAT-AP
  3.0.1 catalogue record; see [DCAT_CATALOGUE.md](DCAT_CATALOGUE.md).
- `combined_ontology.jsonld` — a dataset-level `owl:Ontology` /
  `void:Dataset` / `dcat:Dataset` node at `@graph[0]`, re-emitted from
  `estleg_common.combined_ontology_header()` every time
  `build_release_artifacts.generate_combined_jsonld()` runs (so it survives every
  rebuild). Other combined `*.jsonld` files get the same in-band license /
  publisher stamp via `stamp_combined_dataset_head()`.
- Every `estleg_common.COMBINED_JSONLD_TARGETS` head must carry
  `owl:versionInfo` = `ONTOLOGY_VERSION` before release (#705).
  `build_release_assets.py` checks this read-only and fails unless
  `--allow-unstamped`; it cannot stamp them itself, because the combined
  build has already read those files. `scripts/stamp_combined_dataset_heads.py`
  stamps `owl:versionInfo` / `owl:versionIRI` on every head as a hand-run
  repair. Run it before the combined rebuild.

The `versionIRI` is `https://w3id.org/estleg/<version>` — each
release is an independently dereferenceable IRI.

A standalone VoID + DCAT descriptor is committed at
`krr_outputs/void.ttl` (dataset IRI
`https://w3id.org/estleg/dataset/estonian-legal-ontology`). It
advertises `void:uriSpace`, an example resource, the GitHub-raw
combined ontology dump, and linksets to EuroVoc, EUR-Lex/CELLAR, and
Riigi Teataja. Combined JSON-LD artifacts also carry an in-band Dataset
head (`dcterms:title` / `publisher` / `license`) so a consumer who
loads only the graph still sees the compilation-layer CC BY 4.0 offer.
This descriptor does not advertise a public SPARQL service. The local
Oxigraph sample setup from #474 is documented in [API_GUIDE.md](API_GUIDE.md).

### Refresh SLA

Corpus target is **monthly Riigi Teataja consolidation**. `estleg:kehtiv`
on each act is the snapshot date the committed text is valid as of (not
`temporalStatus`, not `BUILD_EVALUATION_DATE`). `metadata.jsonld`
`dcterms:accrualPeriodicity` is
[`http://publications.europa.eu/resource/authority/frequency/MONTHLY`](http://publications.europa.eu/resource/authority/frequency/MONTHLY)
(the EU Publications Office frequency authority, as used by the
`check_rt_staleness.py` `FREQ_*` constants) at
dataset level, and every `dcat:distribution` carries its own value (#693).

The freshness gate is `python3 scripts/check_rt_staleness.py`. It is offline
and compares each corpus's committed snapshot stamp against **today**
(#693). `--evaluation-date YYYY-MM-DD` reproduces a past run. The gate never
uses `BUILD_EVALUATION_DATE`, which stays the byte-stable `generated` stamp
of tracked artifacts (#295). Lag budgets are the `CORPUS_BUDGETS` table in
`src/estleg/check_rt_staleness.py`. A test keeps them equal to the published
periodicities:

| Corpus | Snapshot stamp | Budget | `accrualPeriodicity` |
| --- | --- | ---: | --- |
| Laws | `estleg:kehtiv` on PKS / KarS osa 1 / PS | 45 d | monthly |
| State regulations | `REGULATIONS_RIIK_INDEX.json` `kehtiv` | 60 d | monthly |
| KOV regulations | `REGULATIONS_KOV_INDEX.json` `kehtiv` | 60 d | monthly |
| Draft legislation | `EELNOUD_INDEX.json` `fetched` / `generated` | 60 d | monthly |
| Riigikohus | `RIIGIKOHUS_INDEX.json` `fetched` / `generated` | 120 d | quarterly |
| Lower-court sample | `KOHTUD_INDEX.json` `fetched` / `generated` | 120 d | (not a distribution) |
| EUR-Lex | `EURLEX_INDEX.json` `fetched` / `generated` | 120 d | quarterly |
| CURIA | `CURIA_INDEX.json` `fetched` / `generated` | 120 d | quarterly |
| Retrieval chunks | release asset `chunks.jsonl.gz` | not gated | monthly |
| Complete dataset | aggregate of the rows above | — | monthly |
| Change record | frozen per release | — | irregular |

A budget is the publishing cadence plus at most one month of grace. When the
gate fails, refresh the stale corpora through their generators and commit the
new snapshot. Laws and regulations fetch act XML from
`/public-api/api/v1/akt/{id}/xml` (#691). The legacy `/akt/{id}.xml` path
returns the RT app shell since 2026-06-01. The `rt-staleness` CI job also runs
`check_rt_staleness.py --schema-canary`, which GETs one live act. "RT
unreachable" is a warning there, and "RT answered with HTML or another schema"
fails the job. `--fetch` compares the law sample with live RT metadata and
XML and stays operator-run. Inter-release IRI deltas are published as
`krr_outputs/changes-<version>.jsonld` and linked from `metadata.jsonld` as a
`dcat:distribution`. The committed IRI delta is for 0.11.0, not every
subsequent commit.

The committed tree is recorded in
`krr_outputs/dataset_build_manifest.json` (dataset version, git SHA,
pinned `generated` / `evaluationDate`, sample `estleg:kehtiv` dates,
catalog counts). Regenerate with
`python3 scripts/write_build_manifest.py`. Immutable GitHub Release
assets (tagged downloads that replace mutable `/main` distribution
URLs) are #473 and are not produced by this in-repo record.

**When to bump:**

- **MAJOR** — a breaking schema change (a removed/renamed property or class, an
  `@id` scheme change) that would break an existing consumer query.
- **MINOR** — new corpus coverage, a new enrichment layer, or new properties
  that are additive.
- **PATCH** — data corrections and bug fixes that don't change the schema.

**Cutting a release:**

1. Bump `estleg_common.ONTOLOGY_VERSION` **and** `pyproject.toml` to the new
   version, and update `metadata.jsonld` (`owl:versionInfo`, `owl:versionIRI`,
   `dcterms:modified`).
2. Move the `## [Unreleased]` entries in `CHANGELOG.md` under a new
   `## [<version>] - <date>` heading.
3. Regenerate `combined_ontology.jsonld` so its header carries the new version,
   and run the [release build](#running-a-release-build) + all gates.
4. After merge, tag the release: `git tag v<version> && git push origin v<version>`,
   and create the GitHub release (this is an outward-facing publish step — do it
   deliberately, not from CI). After tagging, rerun the retrieval generator
   (`generate_retrieval_projection.py`) so `llms.txt` and `manifest.json` point
   at `releases/download/v<ONTOLOGY_VERSION>/chunks.jsonl.gz` (#723). Today the
   v1.0.0 link returns 404, because that release has no chunks asset.
5. Attach `NOTICE`, `LICENSE`, `docs/DATA_RIGHTS.md`, and
   `docs/DATA_PROTECTION.md` as **release assets** (#684). A downloader who takes
   only the release tarball must get the layered-rights and personal-data notices
   with it — CC BY 4.0 covers the compilation layer only, and the court
   subcorpora carry personal data.
6. Before relying on a new version IRI, submit the generalised SemVer rule in
   `w3id/estleg/.htaccess` to `perma-id/w3id.org` and verify it is deployed
   (#690). This repository contains a staging copy; merging it here does not
   change the live resolver. Once that upstream change is deployed, a new
   `https://w3id.org/estleg/<version>` will redirect to `releases/tag/v<version>`
   without another w3id submission. Verify the redirect for each release.

---

## Current Release Snapshot

As of **2026-09-07**, the latest published tag is
[v1.0.0](https://github.com/henrikaavik/estonian-legal-ontology/releases/tag/v1.0.0)
(2026-08-19). The September Tier 0 and #702 fixes are merged to `main`
at `0cb9ac91bc`; there is no newer tagged release containing those fixes.

The reviewed tree indexes 1,122 enacted laws (1,195 law files) and advertises
27,008 JSON/JSON-LD files. Its generated JSON validation report records
**26,961 files / 122 errors / 2 warnings**. CURIA and EUR-Lex pass their
bucket checks, while the broader corpus and consumer-sync gates still fail.
See [VALIDATION_REPORT.md](VALIDATION_REPORT.md) for measured evidence and
remaining work. The old 2026-05-26 all-green statement is not a conformance
claim for the current corpus.

The required merge checks are `lint`, `pytest`, and `estleg-mcp tests`.
They are distinct from the full release gates below. Passing them permits
reviewed incremental fixes; it does not make a data release SHACL-conformant.
Bulk `.nt`/`.nq`/`.ttl` dumps and the other release assets are rebuilt by
the last DAG step, `build_release_assets.py` (#705). See
[Release assets](#release-assets).

Õiguskantsler PDF extraction uses pdfminer; OCR is not in the dependency set.
`generate_annotations.py --scrape --limit 0` means a full archive scrape.
Use a positive limit for a bounded sample.

---

## The step DAG

Each step in `run_all_integration.py:STEPS` is a declarative record:

```python
{
  "name":        "<script filename>",      # unique step id
  "description": "<human label>",
  "script":      "<script filename>",       # under scripts/
  "args":        ["--flag", ...],           # optional extra argv
  "tier":        "enrichment",              # ingest | enrichment | build | package
  "embeds":      ["<pass>", ...],           # optional: passes run inside this step
  "depends_on":  ["<step name>", ...],      # must finish first
  "writes":      ["<glob>", ...],           # produced/mutated under krr_outputs/
  "reads":       ["<glob>", ...],           # consumed (prior write or committed input)
}
```

At startup the runner calls `validate_dag()`, which **fails fast** (exit
code 2) if:

- two steps share a name,
- a `depends_on` entry names a step that does not exist (or names itself),
- the dependency graph has a cycle (Kahn's algorithm),
- a step's `reads` glob is neither a [committed input](#committed-vs-release-asset-policy)
  (`run_all_integration.py:COMMITTED_INPUTS`) nor covered by some *prior*
  step's `writes`,
- another step writes a derived (non-committed) file that a step reads, but
  is not one of that reader's transitive dependencies (#704). An unordered
  writer makes the read depend on source order. A writer that runs after the
  reader leaves the reader's output stale. The shared corpus inputs in
  `COMMITTED_INPUTS` are exempt, because every enrichment step rewrites them
  in place, or
- `--parallel > 1` was requested **and** two steps that could run
  concurrently (neither is a transitive dependency of the other) declare
  overlapping `writes` globs — concurrent writes to a shared corpus target
  are last-writer-wins and would clobber enrichments (see
  [Why serial by default](#why-serial-by-default)). `--parallel 1` (serial,
  the default) skips this check.

The topological order ties are broken by source order, so the historical
phase order is preserved exactly.

### Steps, dependencies, and outputs

| # | Tier | Step (`STEPS` name) | `depends_on` | Declared `writes` (under `krr_outputs/`) |
|---|---|---|---|---|
| 1 | ingest | `generate_provision_versions.py` (`--all --today <BUILD_EVALUATION_DATE>`) | — | `provision_versions/*.jsonld`, `reports/provision_versions_report.json`, `reports/kov/extract_provision_versions_coverage.json` |
| 2 | ingest | `generate_provision_versions_regulations` (`--regulations-riik`) | 1 | `provision_versions/*.jsonld`, `reports/regulation_versions_report.json`, `reports/kov/extract_provision_versions_coverage.json` |
| 3 | ingest | `generate_annotations.py` (`--scrape --limit 0`) | — | `annotations/oiguskantsler_seisukohad.jsonld`, `reports/kov/extract_annotations_coverage.json` |
| 4 | enrichment | `extract_cross_references.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `reports/cross_references_report.json` |
| 5 | enrichment | `generate_inverse_references.py` | 4 | `*_peep.json`, `regulations/**/*_peep.json`, `reports/inverse_references_report.json` |
| 6 | enrichment | `generate_transposition_mapping.py` | — | `*_peep.json`, `reports/transposition_mapping.json`, `eurlex/eurlex_combined.jsonld` |
| 7 | enrichment | `rebuild_eurlex_combined` | 6 | `eurlex/eurlex_combined.jsonld` |
| 8 | enrichment | `link_curia_eu_legislation.py` | 7 | `curia/*_peep.json`, `curia/curia_combined.jsonld`, `curia/curia_eu_link_report.json` |
| 9 | enrichment | `rebuild_curia_combined` | 8 | `curia/curia_combined.jsonld` |
| 10 | enrichment | `rebuild_eelnoud_combined` | — | `eelnoud/eelnoud_combined.jsonld` |
| 11 | enrichment | `generate_harmonisation_links.py` | 6 | `*_peep.json`, `harmonisation/harmonisation_report.json` |
| 12 | enrichment | `extract_court_provision_links.py` | — | `riigikohus/*_peep.json`, `*_peep.json`, `reports/court_provision_links_report.json` |
| 13 | enrichment | `classify_eurovoc.py` | — | `eurovoc/eurovoc_overlay.jsonld`, `reports/eurovoc_classification.json`, `eurovoc_concept_scheme.jsonld` |
| 14 | enrichment | `extract_temporal_data.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `reports/temporal_data_report.json` |
| 15 | enrichment | `generate_amendment_history.py` | — | `amendments/**/*.json`, `*_peep.json`, `reports/amendment_history_report.json` |
| 16 | enrichment | `link_amendment_versions.py` | 15, 1, 2 | `amendments/**/*.json`, `*_peep.json` |
| 17 | enrichment | `derive_act_temporal_status.py` (`--all --recompute --evaluation-date …`) | 14, 1, 2 | `*_peep.json` |
| 18 | enrichment | `generate_act_expressions_608.py` (`--apply`) | 1, 2 | `act_expressions_combined.jsonld` |
| 19 | enrichment | `extract_legal_concepts.py` | — | `concepts/**/*.json`, `*_peep.json` |
| 20 | enrichment | `classify_deontic.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `reports/deontic_classification_report.json` |
| 21 | enrichment | `classify_target_group.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `reports/target_group_report.json` |
| 22 | enrichment | `extract_institutional_competence.py` | — | `institutions/**/*.json`, `*_peep.json`, `reports/institutional_competence_report.json` |
| 23 | enrichment | `extract_sanctions.py` | — | `sanctions/**/*.json`, `*_peep.json`, `reports/sanctions_report.json` |
| 24 | enrichment | `extract_draft_impact.py` | — | `*_peep.json`, `reports/draft_impact_report.json` |
| 25 | enrichment | `derive_court_interpretation_staleness.py` (`--apply`) | 12, 1, 2 | `riigikohus/*_peep.json` |
| 26 | enrichment | `derive_kov_enabling_staleness.py` (`--apply`, #712) | 4, 1, 2 | `regulations/kov/*/*_peep.json`, `reports/kov/derive_kov_enabling_staleness_coverage.json` |
| 27 | enrichment | `generate_similarity_index.py` | the 19 enrichment steps listed in `STEPS` | `reports/similarity_index.json`, `reports/similarity_report.json`, `similarity/kov_similarity_index.json`, `regulations/**/*_peep.json` |
| 28 | build | `build_release_artifacts.py` (embeds `materialize_combined_inverses` #520 and the #521 analytical stamps) | every non-package step | `combined_ontology.jsonld`, `INDEX.json` |
| 29 | package | `generate_analytical_overlay.py` (`--write`) | 28, 27 | `analytical/analytical_overlay.jsonld` |
| 30 | package | `build_release_assets.py` | 28, 29 | `../metadata.jsonld`, `../release/*` (incl. `release/rdf/combined_ontology.{nt,nq,ttl}`) |

**Ingest tier.** Steps 1-3 fetch from Riigi Teataja or oiguskantsler.ee.
They are declared so every produced layer has a producer and declared inputs.
The runner records them as `skipped_ingest` and uses their committed outputs,
unless `--with-ingest` is given. Step 2 is offline, but it rewrites the same
coverage report as step 1 (`generate_provision_versions.COVERAGE_PATH`), so it
stays with the law run until the generator writes its own report.

**Version layer.** `generate_amendment_history.py` rebuilds the chains
without the #429 version join. Step 16 runs that join after the chains and the
version sidecars exist. It re-mints the `_vf_` events and the
`resultedInVersion` links, so a chain rerun cannot lose them. On the committed
corpus the join is a no-op: 179 chains and 0 peeps change. Steps 17, 18, 25
and 26 derive act `temporalStatus`, the act expressions, court staleness and
KOV enabling-provision staleness (#712) from the same sidecars.

**Combined is the last enrichment step, not the last step.** Step 28 depends
on every ingest and enrichment step. Only package-tier steps may follow it.
They read the built corpus and must never write a file the build read; the
ordering check above enforces this. `materialize_combined_inverses` (#520)
and the analytical counts/flags (#521) run inside step 28 on the in-memory
graph, which `embeds` declares. They are never scheduled as separate passes.

`rebuild_eurlex_combined` invokes `generate_eu_legislation.py` with
`--rebuild-combined-from-peeps`, which delegates to
`scripts/rebuild_subcorpus_combined.py`; `rebuild_curia_combined` and
`rebuild_eelnoud_combined` call that script directly (`--subcorpus curia` /
`--subcorpus eelnoud`). The module assembles each sub-corpus aggregate offline
from its schema file plus its `*_peep.json` files, taking the file lists from
the parity gate's own definition (`validate_all.SUBCORPUS_COMBINED_TARGETS`),
so the rebuild and the check cannot disagree. This table reflects the declarations in
`src/estleg/run_all_integration.py:STEPS`.

`court_provision_links_report.json` includes both raw recall lift and its
comparable denominator: use
`full_text_recall_lift / decisions_with_summary_baseline` when comparing runs,
so decisions without summaries do not skew the per-decision lift. If
`decisions_with_summary_baseline` is zero, report the ratio as not applicable.

The EU branch rebuilds EUR-Lex after transposition before linking CURIA.
The final release builder depends on every preceding step. Use the dry-run
commands below to inspect the complete topological order.

### Why serial by default

Every enrichment step mutates the shared `*_peep.json` corpus in
`krr_outputs/`, so concurrent writes would silently clobber each other.
Serial execution is therefore the **deterministic default**.

`--parallel N` is available and *is* implemented (a bounded
`ThreadPoolExecutor` that dispatches any step whose `depends_on` are all
satisfied) — but it is **rejected for the current DAG**. At startup,
`validate_dag()` exits **2** if any two steps that could run concurrently
(i.e. neither is a transitive dependency of the other) declare overlapping
`writes` globs; since most "independent" enrichment steps still rewrite the
shared `*_peep.json` / `regulations/**/*_peep.json` corpus targets, that
condition holds today. Concretely, `python3 scripts/run_all_integration.py
--release --parallel 2` exits 2 with a message naming the two offending
steps and the overlapping write target, and stating that `--parallel` is
unsafe until per-step corpus writes are made disjoint (or an explicit
dependency serializes the steps). `--parallel 1` (serial — the default) is
never affected. `--parallel` is also mutually exclusive with
`--validate-each` (the validator reads the whole corpus mid-flight).

> So: `--parallel > 1` only becomes usable once the corpus is split per
> step in future work. The declarative DAG is the deliverable here; the
> parallel runner is wired up and guarded, but the current step set has
> overlapping corpus writes so it stays serial.

### Preview the DAG

```bash
python3 scripts/run_all_integration.py --dry-run
python3 scripts/run_all_integration.py --release --dry-run
python3 scripts/run_all_integration.py --release --validate-only --dry-run
```

`--dry-run` prints the topo order (and, with `--release`, the validators
that would run) and executes nothing.

---

## Running a release build

```bash
python3 scripts/run_all_integration.py --release
```

This is the **unified release command**. It:

1. Validates the DAG (exit 2 on a structural problem).
2. Takes an atomic rename-aside snapshot of `krr_outputs/` (unless
   `--no-restore-on-failure` or `--snapshot none`). With `--snapshot auto`
   and a clean `git status --porcelain krr_outputs`, the copy is skipped
   and a failure rolls back to git HEAD instead (#722).
3. Runs all 30 steps in topo order. Ingest-tier steps are recorded as
   `skipped_ingest` unless `--with-ingest` is given. A failed step skips its
   dependents; the first hard failure stops the run and the snapshot is
   restored.
4. If — and only if — every step succeeded, runs the three release
   validators in order:
   - `python3 scripts/validate_all.py` — per-file corpus + aggregate parity
   - `python3 scripts/shacl_validate_all.py --all` — full-corpus SHACL conformance
   - `python3 scripts/validate_seadusloome_sync.py` — Seadusloome zero-warning gate
5. Writes `krr_outputs/reports/integration/release_manifest.json`.
6. Exits **0 only if `release_ok`** — i.e. every step succeeded **and**
   all three validators passed **and** no release-surface artifact is
   missing (`releaseArtifacts.missing` is empty; see
   [the manifest schema](#the-release_manifestjson-schema)). Otherwise exit 1.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Print the DAG topo order + validators; run nothing; exit 0. |
| `--resume-from <step>` | Skip steps before `<step>` in topo order (treated as already done so dependents are not blocked). |
| `--no-restore-on-failure` | Leave a partial `krr_outputs/` tree in place on failure instead of rolling back. Same as `--snapshot none`. |
| `--snapshot {copy,auto,none}` | Rollback strategy (#722). `copy` (default) renames `krr_outputs/` aside and copies it back, about 3.8 GB and 29k files. `auto` checks `git status --porcelain krr_outputs`. On a clean tree it skips the copy and restores from HEAD on failure (`git restore` + `git clean -fd`). On a dirty tree, or without git, it falls back to `copy`. Ignored files such as `krr_outputs/.cache/` are not restored. `none` does no rollback. |
| `--validate-each` | Run `validate_all.py` after each successful step. Incompatible with `--parallel`. |
| `--per-script-timeout N` | Per-step (and per-validator) timeout in seconds (default 1800; a timeout is recorded as exit code 124). |
| `--parallel N` | Run up to N dependency-ready steps concurrently (default 1 = serial). **N > 1 is rejected (exit 2) for the current DAG** — independent steps share `*_peep.json` writes; see [Why serial by default](#why-serial-by-default). |
| `--with-ingest` | Also run the ingest-tier steps (network). Without it they are recorded as `skipped_ingest`. |
| `--check-pipeline-versions` | Run only the [`pipeline_version` gate](#reproducibility-gates) and exit. |

---

## Validating without rebuilding

```bash
python3 scripts/run_all_integration.py --release --validate-only
```

This skips **all generation steps** and just runs the three release
validators against the current corpus, then writes the validator section of
`release_manifest.json` (with `mode: "release-validate-only"` and an empty
`stepLedger`). Exit code is 0 only if all three validators passed.

This is the "unified release-validation command" — use it in CI / on the
release branch to confirm the committed corpus is internally consistent and
SHACL-clean without spending the (much longer) generation time.

---

## The `release_manifest.json` schema

Written to `krr_outputs/reports/integration/release_manifest.json` by every
`--release` invocation (except `--dry-run`). Shape:

```jsonc
{
  "generated": "2026-05-11T12:34:56+00:00",   // ISO-8601 UTC
  "mode": "release-build",                      // or "release-validate-only"
  "dryRun": false,
  "resumeFrom": null,                           // or a step name
  "parallel": 1,

  "dag": {
    "topoOrder": ["extract_cross_references.py", ..., "generate_similarity_index.py"],
    "steps": [
      { "name": "extract_cross_references.py",
        "dependsOn": [],
        "writes": ["*_peep.json", "..."],
        "reads":  ["*_peep.json", "..."] },
      ...                                       // one per declared step
    ]
  },

  "stepLedger": [                               // [] in --validate-only mode
    { "name": "extract_cross_references.py",
      "script": "extract_cross_references.py",
      "description": "Cross-law reference extraction",
      "dependsOn": [],
      "command": ["/usr/bin/python3", ".../extract_cross_references.py"],
      "elapsedSeconds": 12.3,
      "logPath": "krr_outputs/reports/integration/logs/extract_cross_references.log",
      "exitCode": 0,
      "status": "succeeded" },                  // succeeded | failed | timeout |
                                                // blocked | missing |
                                                // skipped_before_resume_point |
                                                // skipped_ingest |
                                                // validation_failed
    ...
  ],
  "stepSummary": { "totalSteps": 18, "succeeded": 18, "failed": 0,
                   "skipped": 0, "planned": 0 },

  "validators": [
    { "name": "validate_all",
      "description": "Per-file corpus + aggregate parity validation",
      "command": ["/usr/bin/python3", ".../validate_all.py"],
      "status": "passed",                       // passed | failed | timeout |
                                                // planned (dry-run) |
                                                // skipped_steps_failed
      "passed": true,
      "exitCode": 0,
      "elapsedSeconds": 8.1,
      "logPath": "krr_outputs/reports/integration/logs/validate_validate_all.log",
      "metrics": { "filesValidated": 712, "errors": 0, "warnings": 0 } },
    { "name": "shacl_validate_all",
      ...
      "metrics": { "filesLoaded": 4123, "triples": 298831, "shaclConforms": true } },
    { "name": "validate_seadusloome_sync",
      ...
      "metrics": { "dataTriples": 301204, "shapeTriples": 2014,
                   "shaclConforms": true, "violations": 0, "warnings": 0 } }
  ],
  "validatorsPassed": true,

  "releaseArtifacts": {
    "files": {                                  // repo-relative path -> sha256
      "krr_outputs/combined_ontology.jsonld": "…64 hex…",
      "krr_outputs/controlled_vocabulary.jsonld": "…",
      "krr_outputs/INDEX.json": "…",
      "krr_outputs/regulations/riik/REGULATIONS_RIIK_INDEX.json": "…",
      "krr_outputs/regulations/kov/REGULATIONS_KOV_INDEX.json": "…",
      "krr_outputs/eelnoud/EELNOUD_INDEX.json": "…",
      "krr_outputs/riigikohus/RIIGIKOHUS_INDEX.json": "…",
      "krr_outputs/curia/CURIA_INDEX.json": "…",
      "krr_outputs/eurlex/EURLEX_INDEX.json": "…",
      "metadata.jsonld": "…",
      "release/SHA256SUMS": "…",
      "release/combined_ontology.jsonld.gz": "…" // + every asset SHA256SUMS lists
    },
    "missing": [],                              // any expected RELEASE_ARTIFACTS / INDEX
                                                // glob entry or SHA256SUMS asset not
                                                // found on disk, or "<path> (sha256
                                                // mismatch)"; releaseOk is False
                                                // whenever this is non-empty
    "contentHash": "…64 hex…"                   // sha256 over sorted "path\nsha256\n" lines
  },

  "releaseOk": true                             // steps all OK AND validators all passed
                                                // AND releaseArtifacts.missing is empty;
                                                // null in --dry-run
}
```

`contentHash` is a single stable digest of the committed release surface plus
every asset in `release/SHA256SUMS`. Compare two release manifests to explain
corpus or artifact drift; per-file hashes pinpoint exactly what changed. Each
asset is re-hashed against `SHA256SUMS`, so a stale or edited asset fails the
release. `--release --validate-only` therefore needs a built `release/`
directory.

`releaseArtifacts.missing` is a *hard* gate, not just a diagnostic:
`validate_all.py` only **warns** when `metadata.jsonld` is absent, so
`releaseOk` would otherwise be `true` for a release whose release-surface
artifact is gone. Folding `missing == []` into `releaseOk` closes that hole
while still recording exactly which artifact was missing.

The per-step (and per-validator) subprocess logs live under
`krr_outputs/reports/integration/logs/`; each ledger/validator entry carries
its `logPath`.

> The plain enrichment run (no `--release`) still writes its per-run manifest
> to `krr_outputs/reports/integration/latest_pipeline_manifest.json` as
> before, now also recording the `topoOrder`.

---

## Release assets

`python3 scripts/build_release_assets.py` is the last DAG step (#705). It
writes every downloadable file into the repo-root `release/` directory
(gitignored). The directory sits outside `krr_outputs/` because every corpus
file walker counts each `*.json` / `*.jsonld` under `krr_outputs/`. Copies of
`INDEX.json` or `metadata.jsonld` there would be counted and validated as
corpus files. The step runs these parts in order:

1. **Version check.** Every combined head must carry `owl:versionInfo` =
   `ONTOLOGY_VERSION`. The step fails otherwise, unless `--allow-unstamped`
   is given; the missing heads are then recorded in `release_assets.json`.
2. **RDF dumps.** `combined_ontology.{nt,nq,ttl}` are regenerated into
   `release/rdf/` in one streamed pass (`serialize_corpus.py --stream`) and
   gzipped from there; the committed `krr_outputs/combined_ontology.{nt,nq,ttl}`
   (LFS) are never overwritten by the step — refreshing those is a separate,
   deliberate commit, because every rebuild would otherwise add ~1.4 GB of LFS. N-Triples and N-Quads are byte-sorted and de-duplicated with an
   external `sort -u`, so peak memory stays far below the 10-14 GB an
   in-memory `Graph` needs. Blank-node labels differ between runs, as they
   always have with rdflib. Turtle uses N-Triples syntax (a Turtle subset)
   so blank-node references retain their identity across batches.
3. **Named graphs.** `estleg_all.nq.gz` holds the seven #474 graphs. The
   dump fails when a slot has no source; `serialize_named_graphs --write
   --allow-partial` is the escape hatch. The regulations and riigikohus
   slots read their peep trees, because neither corpus has a combined file.
4. **Chunks.** `chunks.jsonl.gz` (catalogued as its own distribution in
   `metadata.jsonld`; see [DCAT_CATALOGUE.md](DCAT_CATALOGUE.md)) comes from `generate_retrieval_projection
   --chunks-only`, run into `release/retrieval/`. `build_release_assets.py`
   builds it this way on every release. It uses chunk schema 2.0.0 and is a
   single unsplit file (#723).
5. **Combined dumps.** The four combined JSON-LD files the catalogue
   advertises, plus the annotations layer, are gzipped with `mtime=0` and no
   embedded filename. The output is byte-stable.
6. **Small assets.** `INDEX.json`, the controlled vocabulary, the SHACL
   shapes, `void.ttl`, `dataset_build_manifest.json`, `LICENSE`, `NOTICE`,
   `DATA_RIGHTS.md` and `DATA_PROTECTION.md` are copied verbatim.
7. **Catalogue.** Each `metadata.jsonld` distribution whose `dcat:downloadURL`
   names a built asset gets `dcat:byteSize` and an `spdx:checksum`
   (SHA-256). A release-download URL whose tag is not `v<ONTOLOGY_VERSION>`
   produces a warning and retains its existing metadata. Checksums are only
   updated for the current release tag. The updated `metadata.jsonld` is then
   copied in as an asset.
8. **`SHA256SUMS`** uses the format of the v1.0.0 file: `<sha256>  <name>`,
   byte-sorted by name, covering every top-level asset. `release_assets.json`
   records each asset's source, producer, size and hash, plus anything
   skipped.

Required inputs are checked before known generated files are removed;
unrelated files are preserved. `SHA256SUMS` lists only assets built by this
run. `--skip-rdf-dumps` (which also skips
`estleg_all.nq.gz`), `--skip-named-graphs` and `--skip-chunks` shorten a
local run, but the release gate rejects packages with skipped assets or
unstamped heads. Upload the assets listed in `SHA256SUMS`, together with
`SHA256SUMS` and `release_assets.json`, to the GitHub Release by hand;
that publish step stays manual.

---

## Runtime, memory and disk envelope

Measured on the `tier1/wave4` branch (Apple Silicon laptop, SSD, Python 3.14)
unless marked as an estimate. Use these figures to size a runner. They are
not guarantees.

| Job | Wall time | Peak RAM | Disk |
|---|---|---|---|
| `build_release_assets.py` (the release step, streamed dumps, #705) | ~6 min | ~3.4 GB | `release/` plus the external `sort -u` temp files |
| `run_all_integration.py --release --validate-only` | dominated by the three validators | validator-bound | none extra |
| Rollback snapshot, `--snapshot copy` | one full copy of `krr_outputs/` | low | +3.8 GB transient, 29k files |
| Rollback snapshot, `--snapshot auto` on a clean tree | `git status` ≈ 0.2 s | low | none |
| Regulations refresh, serial (`--workers 1`), estimate | ≈ 2.6 h for 14,871 acts (KOV ≈ 2.0 h, riik ≈ 0.7 h) | ≈ 1.3 GB (KOV) | see below |
| Regulations refresh, `--workers 4 --max-rps 4` (default), estimate | ≈ 62 min (KOV ≈ 46 min, riik ≈ 16 min) | ≈ 1.3 GB (KOV) | see below |

How the regulations estimates were derived:

- **Fetch rate.** Five sequential public-API XML fetches averaged 0.34 s.
  Serial ingest adds the 0.3 s politeness sleep, so one act costs ≈ 0.64 s.
  Four workers would reach ≈ 6 requests/s, so the default 4 req/s cap
  governs, giving ≈ 0.25 s per act. JSON-LD building is milliseconds per act.
- **RAM.** The generator keeps every built document in memory for the index
  pass. Holding all 11,059 committed KOV peeps took 1.0 GB RSS. Lõige nodes
  add about 30% to that.
- **Disk.** The XML cache under `data/riigiteataja/maarus{,_kov}/` is
  git-ignored. A five-act sample averaged 148 KB per act, which projects to
  ≈ 2 GB for a cold cache. The regulation peeps are 619 MB today. An
  eight-act sample grew 1.7× once `estleg:Subsection` nodes were added, so
  expect ≈ 1.0-1.1 GB after the first full refresh.
- **Resume.** A `--regen-state` run that is interrupted loses at most 50 acts
  of progress, because the ledger is checkpointed every 50 acts and on exit.
  A resumed run replays cached XML at disk speed.

---

## Refreshing the regulations corpus

`generate_regulations.py` refreshes the state (`riik`) and municipal
(`--kov`) regulation peeps from the Riigi Teataja public API. Run it once
per corpus:

```bash
python3 scripts/generate_regulations.py --kehtiv YYYY-MM-DD --refresh --regen-state
python3 scripts/generate_regulations.py --kov --kehtiv YYYY-MM-DD --refresh --regen-state
```

- **`--regen-state [PATH]`** writes a per-act ledger. By default it goes to
  `krr_outputs/.cache/regen_state_regulations_{riik,kov}.json`, which is
  git-ignored. A rerun skips acts already completed for the same `kehtiv`
  and `globalId` whose output file still exists. Acts that failed are
  retried. `--reset-regen-state` discards the ledger.
- **`--workers N`** (default 4) sets how many XML fetches run at once.
  `--max-rps` (default 4) caps request starts per second across all
  workers. `--sleep` (default 0.3 s) is each worker's pause after a
  network fetch. Retries on 429 and 5xx, with linear backoff, stay in
  `riigiteataja_common.fetch_xml`. Results are written in source-list
  order whatever the completion order, so the output is deterministic.
- **Every act now carries `estleg:kehtiv`**, the snapshot date its redaction
  was listed under, and `estleg:parseMode`. Structured acts gain one
  `estleg:Subsection` per lõige, built by the same `build_subsections` the
  laws use. The first full refresh therefore rewrites every peep. Re-run the
  enrichment pipeline (`run_all_integration.py`) afterwards, because a
  regenerated peep carries no enrichment layers.
- **The index `run` block** records `regenStateSkipped`, `networkRequests`,
  `workers`, `maxRequestsPerSecond`, `provisionIriScheme` and
  `subsectionsGenerated`. Review it alongside the peep diff.

The monthly workflow `.github/workflows/refresh-regulations.yml` runs both
commands and opens a pull request containing the data and the index diff.
It is disabled until the repository variable `ESTLEG_REFRESH_ENABLED` is set
to `true`. Two decisions belong to the maintainer before it is turned on:

- whether a scheduled job may call the public RT API monthly, and at what
  rate;
- which identity opens the pull request. The default `GITHUB_TOKEN` works,
  but its pull requests do not trigger other workflows, so `validate.yml`
  will not run on them automatically.

### Provision IRIs and the pending MAJOR rename

Regulation provision IRIs used to be minted by position: `sanitize_id(nr)`
plus `_{len(seen_ids)}` on a collision. Under that scheme `§ 7¹` became
`…_Par_7_6`, and a duplicate `§ 2` became `…_Par_2_4`. Laws use
`law_structure._paragraph_id_suffix` with `_dedupe_paragraph_suffix`
instead, which gives `…_Par_7_1` and `…_Par_2_x2`. The generator has three
modes, selected with `--iri-scheme`:

- `law` uses the law helpers.
- `legacy` reproduces the committed IRIs byte for byte. This was verified on
  three acts fetched from the public API.
- `auto` is the default. Acts with no committed peep get `law`; acts that
  already have one keep `legacy`. A refresh therefore never renames a
  published IRI.

Moving the committed corpus to `law` is a **MAJOR** change under
`docs/STABILITY.md`. It must be scheduled with a rename map, not run as a
silent regeneration. Compute the map without applying it:

```bash
python3 scripts/regulation_iri_rename_map.py --scan-references --output /tmp/reg_iri_map.json
python3 scripts/regulation_iri_rename_map.py --source xml   # authoritative, needs the XML cache
```

The dry run on this branch (offline, peep-derived) gave these totals:

| | riik | KOV | total |
|---|---|---|---|
| acts | 3,812 | 11,059 | 14,871 |
| provision IRIs | 51,712 | 116,708 | 168,420 |
| IRIs that change | 1,767 | 782 | 2,549 (1.5%) |
| acts with a change | 533 | 353 | 886 |
| unresolved, kept as is | 8 | 0 | 8 |
| new IRIs duplicated within an act | | | 0 |
| new IRI equal to another node's current IRI | | | 64 |
| references to changed IRIs from other regulation peeps | | | 1,200 |

The 64 reused IRIs are the hazard. For example, `…_Par_7_7` is `§ 7¹` today
and would become `§ 7⁷`. A redirect cannot express that, so the release
notes must call it out. Apply the rename in one simultaneous pass. Then
regenerate with `--iri-scheme law`, and publish the map as an `owl:sameAs` /
redirect table.

---

## Reproducibility gates

**Pinned dependencies.** `pyproject.toml` declares compatible ranges.
`constraints.txt` pins the exact versions the corpus was last built and
validated with: the runtime and dev dependencies and their whole closure,
including rdflib and pyshacl. Every CI install uses it:

```bash
python3 -m pip install -e ".[dev]" -c constraints.txt
```

The N-Triples, N-Quads and Turtle dumps and the SHACL results depend on the
rdflib and pyshacl versions. Bump a pin deliberately: change it, rebuild,
run the gates, and commit the result together.

**`pipeline_version` gate.** Every `krr_outputs/reports/kov/*coverage.json`
records the commit its generator ran at. The gate resolves each one with
`git rev-parse <sha>^{commit}` and fails on any value that is not a commit in
this repository:

```bash
python3 scripts/run_all_integration.py --check-pipeline-versions
```

The gate needs full history, so the CI job checks out with
`fetch-depth: 0`. In a shallow clone it exits 2. Three reports are frozen
corpus written from commits that no longer exist. They are listed with their
exact SHA in `data/pipeline_version_baseline.json` and stay exempt only while
they carry that SHA. The list may only shrink: regenerate a report from a
pushed commit and delete its entry. A test pins the allowed set. A report
regenerated from an unpushed or squashed commit fails the gate once that
commit is gone.

**Corpus test tier (#706).** The default `pytest` run (`-n auto` in CI)
excludes the `corpus`-marked tests. Run `python3 -m pytest -q -m corpus`
after `git lfs pull`. Corpus gates fail, not skip, on missing LFS inputs, and
the default run excludes them. The session fails if a test modified
`krr_outputs/`; set `ESTLEG_ALLOW_KRR_WRITES=1` only for intentional data
work.

---

## Committed-vs-release-asset policy

The corpus and its derived artifacts split into **committed source/release
files** (authoritative; live in git; reviewed) and **regenerable build
artifacts** (reproduced by the pipeline; need not be committed; safe to
delete and rebuild).

### Committed to git (authoritative)

**Canonical source (edit these directly):**

- every root `krr_outputs/*_peep.json` (the per-act mappings)
- `krr_outputs/controlled_vocabulary.jsonld`
- `krr_outputs/karistusseadustik_eriosa_owl.jsonld`,
  `krr_outputs/tsus_osa7_138_169_owl.jsonld` (the `*_owl.jsonld` modules)
- the sub-corpora `krr_outputs/eelnoud/*_peep.json`,
  `krr_outputs/riigikohus/*_peep.json`, `krr_outputs/curia/*_peep.json`,
  `krr_outputs/eurlex/*_peep.json` (edit per their generator)
- the SHACL shapes `shacl/estonian_legal_shapes.ttl` (+ `shacl/README.md`)
- `metadata.jsonld` (the DCAT dataset descriptor; refresh counts on release)

**Generated but committed (regenerate via the pipeline, then commit):**

- the published aggregate `krr_outputs/combined_ontology.jsonld` and the
  per-sub-corpus aggregates
  `krr_outputs/concepts/concepts_combined.jsonld`,
  `krr_outputs/curia/curia_combined.jsonld`,
  `krr_outputs/eelnoud/eelnoud_combined.jsonld`,
  `krr_outputs/eurlex/eurlex_combined.jsonld`
- the index manifests `krr_outputs/INDEX.json`,
  `krr_outputs/regulations/riik/REGULATIONS_RIIK_INDEX.json`,
  `krr_outputs/regulations/kov/REGULATIONS_KOV_INDEX.json`,
  `krr_outputs/eelnoud/EELNOUD_INDEX.json`,
  `krr_outputs/riigikohus/RIIGIKOHUS_INDEX.json`,
  `krr_outputs/curia/CURIA_INDEX.json`, `krr_outputs/eurlex/EURLEX_INDEX.json`
- the enrichment sidecar trees `krr_outputs/concepts/`,
  `krr_outputs/sanctions/`, `krr_outputs/amendments/`,
  `krr_outputs/institutions/`, `krr_outputs/provision_versions/`,
  `krr_outputs/annotations/`, `krr_outputs/harmonisation/`, and
  `krr_outputs/regulations/` (the shaped per-item JSON-LD)
- the cross-corpus indexes/maps `krr_outputs/reports/similarity_index.json`,
  `krr_outputs/reports/eurovoc_classification.json`,
  `krr_outputs/reports/transposition_mapping.json`
- the per-domain `*_report.json` summaries under `krr_outputs/reports/`
  (`cross_references_report.json`, `inverse_references_report.json`,
  `court_provision_links_report.json`, `temporal_data_report.json`,
  `amendment_history_report.json`, `deontic_classification_report.json`,
  `institutional_competence_report.json`, `sanctions_report.json`,
  `draft_impact_report.json`, `similarity_report.json`) and the per-bucket
  coverage reports under `krr_outputs/reports/kov/*_coverage.json`, plus
  probe reports such as `krr_outputs/reports/annotations_pdf_probe.json`
- the source-fetch manifests `krr_outputs/generation_manifest_*.json`
- the committed-tree record `krr_outputs/dataset_build_manifest.json`
  (issue #548; tagged GitHub Release assets are #473)

The largest generated artifacts are committed through Git LFS:
`combined_ontology.jsonld`, `reports/similarity_index.json`,
`reports/eurovoc_classification.json`, `annotations/oiguskantsler_seisukohad.jsonld`,
`curia/curia_combined.jsonld`, and `eurlex/eurlex_combined.jsonld`. Run
`git lfs install` before cloning or validating the full release surface.

This release introduces Git LFS for the repository; earlier commits still
contain these artifacts as normal Git blobs. `#480` is **keep-LFS**: we
are not dropping these blobs from git, not opening a second data remote,
and not running a destructive `git lfs migrate import --everything`
history rewrite. Clone size for historical revisions is unchanged;
operators checking out old commits should treat those files as regular
Git-tracked JSON/JSON-LD rather than LFS-managed artifacts. See
`docs/ARCHITECTURE.md`.

The release `contentHash` in `release_manifest.json` is computed over the
subset highlighted in the manifest (`combined_ontology.jsonld`,
`controlled_vocabulary.jsonld`, the `*_INDEX.json` files, `metadata.jsonld`)
and every release asset listed in `release/SHA256SUMS`.

`INDEX.json`'s `generated` field is `BUILD_EVALUATION_DATE`, not the build
day (#704). The file is hashed, and a wall-clock stamp churned it on every
rebuild. The `registry_exceptions` seed lives in
`data/registry_exceptions.json`. `generate_index()` starts from it and unions
the exceptions already in `INDEX.json`.

### Regenerable build artifacts (need **not** be committed)

These are produced by the orchestrator/validators and are safe to delete and
rebuild; they are not part of the release contract:

- the repo-root `release/` directory written by `build_release_assets.py`
  (gzipped dumps, `estleg_all.nq.gz`, `chunks.jsonl.gz`, `SHA256SUMS`,
  `release_assets.json`); it is uploaded to the GitHub Release, not committed
- everything under `krr_outputs/reports/integration/` —
  `release_manifest.json`, `latest_pipeline_manifest.json`, and the
  `logs/*.log` per-step/per-validator capture files
- on-disk caches: `data/.cache/` and `krr_outputs/.cache/` (if present)
- the optional Seadusloome SHACL results dump (e.g.
  `seadusloome-shacl-report.ttl`) written only when
  `validate_seadusloome_sync.py --report <path>` is passed
- the rename-aside rollback snapshots `krr_outputs.bak.<pid>/` and
  `krr_outputs.failed.<pid>/` (transient; cleaned up on success)
- raw upstream downloads under `data/riigiteataja/**/*.xml` (already in
  `.gitignore`) and any other `data/` scratch fetched by the `generate_*`
  scripts

### Operational rule of thumb

> If `python3 scripts/run_all_integration.py --release` (or the targeted
> `generate_*` / `build_release_artifacts.py` steps) reproduces a file from committed
> inputs, it is a **build artifact** — commit it on release for downstream
> consumers and reviewers, but it is reconstructible. If you *type into* a
> file by hand, it is **source** and must be committed. Anything written only
> under `krr_outputs/reports/integration/` or a `.cache/`/`.bak.`/`.failed.`
> path is **transient** and need not be committed at all.

### From-scratch regen (zero backfill)

A from-scratch `generate_*` run plus `python3 scripts/build_release_artifacts.py`
must reproduce a validator-clean corpus **without** running
`scripts/archive/` spent one-shots (`rederive_court_case_types.py`,
`backfill_eu_provenance.py`, `legacy_repairs.py`, or the IRI migrations).
Case-type classification and EU CELEX provenance are emitted by the
generators themselves (#468). `migrate_uris.py` stays live as the URI
registry owner, not as a post-hoc backfill.
