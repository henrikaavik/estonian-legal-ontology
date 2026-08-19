# Release build DAG

`scripts/run_all_integration.py` owns the enrichment pipeline **and** the
release build. The 15 enrichment steps are declared as an explicit,
declarative directed acyclic graph (DAG); the runner topologically sorts it,
runs it (serially by default), then — in `--release` mode — runs the three
release validators and writes a release-wide manifest aggregating everything.

This document covers:

1. [The step DAG](#the-step-dag)
2. [Running a release build](#running-a-release-build) — `--release`
3. [Validating without rebuilding](#validating-without-rebuilding) — `--release --validate-only`
4. [The `release_manifest.json` schema](#the-release_manifestjson-schema)
5. [Committed-vs-release-asset policy](#committed-vs-release-asset-policy) — which files live in git, which are regenerable build artifacts
6. [Versioning policy](#versioning-policy) — how the ontology version is set, stamped, and released

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
  (committed; bump by hand when you bump the constant).
- `combined_ontology.jsonld` — a dataset-level `owl:Ontology` /
  `void:Dataset` / `dcat:Dataset` node at `@graph[0]`, re-emitted from
  `estleg_common.combined_ontology_header()` every time
  `build_release_artifacts.generate_combined_jsonld()` runs (so it survives every
  rebuild). Other combined `*.jsonld` files get the same in-band license /
  publisher stamp via `stamp_combined_dataset_head()`.

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
A SPARQL endpoint is not claimed (that is #474).

### Refresh SLA

Corpus target is **monthly Riigi Teataja consolidation**. `estleg:kehtiv`
on each act is the snapshot date the committed text is valid as of (not
`temporalStatus`, not `BUILD_EVALUATION_DATE`). `metadata.jsonld`
`dcterms:accrualPeriodicity` is
[`http://purl.org/cld/freq/monthly`](http://purl.org/cld/freq/monthly).
The content-staleness canary is `python3 scripts/check_rt_staleness.py`
(offline; compares committed `estleg:kehtiv` on PKS / KarS osa 1 / PS
against `BUILD_EVALUATION_DATE` with a 45-day lag budget). `--fetch`
(GET live RT akt XML) is operator-run and is not used in CI. Inter-release
IRI deltas are published as `krr_outputs/changes-<version>.jsonld` and
linked from `metadata.jsonld` as a `dcat:distribution`.

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
   deliberately, not from CI).

---

## Current Release Snapshot

The current release indexes 1,122 enacted laws (1,190 law files) and
advertises 23,118 JSON/JSON-LD files overall, matching
`krr_outputs/INDEX.json` (`total_laws`) and the root README /
`metadata.jsonld` headline. Sequential live jobs in the 2026-05-26
refresh completed the full law corpus refresh, Riigikohus full-text
ingestion, full-history ProvisionVersion sidecars, and Õiguskantsler
PDF-body annotation ingestion. Final local gates passed:

- `python3 -m ruff check scripts/ src/estleg/ tests/`
- `python3 -m pytest -q` (`1568 passed, 2 skipped`)
- `python3 scripts/validate_all.py` (`23,069 files`, zero errors/warnings)
- `python3 scripts/shacl_validate_all.py --all` (`23,064 files`,
  `7,117,928` triples, PASS)
- `python3 scripts/validate_seadusloome_sync.py --report seadusloome-shacl-report.ttl`
  (`21,874` JSON-LD inputs, `7,127,720` data triples, PASS, `real 518.46`)

Õiguskantsler PDF handling remains pdfminer-only for this release: the 10-PDF
probe accepted 10/10 text layers, and the full scrape accepted 4,045/4,052 PDF
text layers with six unusable/scanned PDFs and one fetch failure. OCR is not
part of the release dependency set.

Operator-facing behavior changes in this refresh:

- `scripts/generate_annotations.py --scrape --limit 0` now means full archive
  scrape. Use a positive `--limit` for a bounded recent-opinion sample.
- Repeated subsection suffixes are now emitted with stable `_DupN` IRIs instead
  of aborting generation, so duplicated source lõige numbering stays citable.

---

## The step DAG

Each step in `run_all_integration.py:STEPS` is a declarative record:

```python
{
  "name":        "<script filename>",      # unique step id
  "description": "<human label>",
  "script":      "<script filename>",       # under scripts/
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
  step's `writes`, or
- `--parallel > 1` was requested **and** two steps that could run
  concurrently (neither is a transitive dependency of the other) declare
  overlapping `writes` globs — concurrent writes to a shared corpus target
  are last-writer-wins and would clobber enrichments (see
  [Why serial by default](#why-serial-by-default)). `--parallel 1` (serial,
  the default) skips this check.

The topological order ties are broken by source order, so the historical
phase order is preserved exactly.

### Steps, dependencies, and outputs

| # | Step (`scripts/…`) | `depends_on` | Key `writes` (under `krr_outputs/`) |
|---|---|---|---|
| 1 | `extract_cross_references.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `reports/cross_references_report.json` |
| 2 | `generate_inverse_references.py` | `extract_cross_references.py` | `*_peep.json`, `regulations/**/*_peep.json`, `reports/inverse_references_report.json` |
| 3 | `generate_transposition_mapping.py` | — | `*_peep.json`, `reports/transposition_mapping.json` |
| 4 | `generate_harmonisation_links.py` | `generate_transposition_mapping.py` | `*_peep.json`, `harmonisation/harmonisation_report.json` |
| 5 | `extract_court_provision_links.py` | — | `riigikohus/*_peep.json`, `*_peep.json`, `reports/court_provision_links_report.json` |
| 6 | `classify_eurovoc.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `reports/eurovoc_classification.json` |
| 7 | `extract_temporal_data.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `reports/temporal_data_report.json` |
| 8 | `generate_amendment_history.py` | — | `amendments/**/*.json`, `*_peep.json`, `reports/amendment_history_report.json` |
| 9 | `extract_legal_concepts.py` | — | `concepts/**/*.json`, `*_peep.json` |
| 10 | `classify_deontic.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `reports/deontic_classification_report.json` |
| 11 | `classify_target_group.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `reports/target_group_report.json` |
| 12 | `extract_institutional_competence.py` | — | `institutions/**/*.json`, `*_peep.json`, `reports/institutional_competence_report.json` |
| 13 | `extract_sanctions.py` | — | `sanctions/**/*.json`, `*_peep.json`, `reports/sanctions_report.json` |
| 14 | `extract_draft_impact.py` | — | `*_peep.json`, `reports/draft_impact_report.json` |
| 15 | `generate_similarity_index.py` | steps 1–14 (all) | `reports/similarity_index.json`, `reports/similarity_report.json` |
| 16 | `build_release_artifacts.py` | steps 1–15 (all) | `combined_ontology.jsonld`, `INDEX.json` |

`court_provision_links_report.json` includes both raw recall lift and its
comparable denominator: use
`full_text_recall_lift / decisions_with_summary_baseline` when comparing runs,
so decisions without summaries do not skew the per-decision lift. If
`decisions_with_summary_baseline` is zero, report the ratio as not applicable.

ASCII view of the dependency edges (everything not shown is a no-dependency
root that the topo sort places in source order; step 15 fans in from all
prior steps):

```
extract_cross_references.py ──▶ generate_inverse_references.py ──┐
generate_transposition_mapping.py ──▶ generate_harmonisation_links.py ──┤
extract_court_provision_links.py ───────────────────────────────────────┤
classify_eurovoc.py ────────────────────────────────────────────────────┤
extract_temporal_data.py ───────────────────────────────────────────────┤
generate_amendment_history.py ──────────────────────────────────────────┼─▶ generate_similarity_index.py
extract_legal_concepts.py ──────────────────────────────────────────────┤
classify_deontic.py ────────────────────────────────────────────────────┤
classify_target_group.py ────────────────────────────────────────────────┤
extract_institutional_competence.py ────────────────────────────────────┤
extract_sanctions.py ───────────────────────────────────────────────────┤
extract_draft_impact.py ────────────────────────────────────────────────┘
```

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
   `--no-restore-on-failure`).
3. Runs all 15 steps in topo order. A failed step skips its dependents; the
   first hard failure stops the run and the snapshot is restored.
4. If — and only if — every step succeeded, runs the three release
   validators in order:
   - `python3 scripts/validate_all.py` — per-file corpus + aggregate parity
   - `python3 scripts/shacl_validate_all.py --all` — full-corpus SHACL conformance
   - `python3 scripts/validate_seadusloome_sync.py` — Seadusloome zero-warning gate
5. Writes `krr_outputs/reports/integration/release_manifest.json`.
6. Exits **0 only if `release_ok`** — i.e. all 15 steps succeeded **and**
   all three validators passed **and** no release-surface artifact is
   missing (`releaseArtifacts.missing` is empty; see
   [the manifest schema](#the-release_manifestjson-schema)). Otherwise exit 1.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Print the DAG topo order + validators; run nothing; exit 0. |
| `--resume-from <step>` | Skip steps before `<step>` in topo order (treated as already done so dependents are not blocked). |
| `--no-restore-on-failure` | Leave a partial `krr_outputs/` tree in place on failure instead of rolling back. |
| `--validate-each` | Run `validate_all.py` after each successful step. Incompatible with `--parallel`. |
| `--per-script-timeout N` | Per-step (and per-validator) timeout in seconds (default 1800; a timeout is recorded as exit code 124). |
| `--parallel N` | Run up to N dependency-ready steps concurrently (default 1 = serial). **N > 1 is rejected (exit 2) for the current DAG** — independent steps share `*_peep.json` writes; see [Why serial by default](#why-serial-by-default). |

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
                                                // validation_failed
    ...
  ],
  "stepSummary": { "totalSteps": 15, "succeeded": 15, "failed": 0,
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
      "metadata.jsonld": "…"
    },
    "missing": [],                              // any expected RELEASE_ARTIFACTS / INDEX
                                                // glob entry not found on disk; releaseOk
                                                // is False whenever this is non-empty
    "contentHash": "…64 hex…"                   // sha256 over sorted "path\nsha256\n" lines
  },

  "releaseOk": true                             // steps all OK AND validators all passed
                                                // AND releaseArtifacts.missing is empty;
                                                // null in --dry-run
}
```

`contentHash` is a single stable digest of the whole **committed release
surface** — compare two release manifests to explain corpus/artifact drift
(per-file hashes pinpoint exactly what changed).

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
— the artifacts downstream consumers treat as the release surface.

### Regenerable build artifacts (need **not** be committed)

These are produced by the orchestrator/validators and are safe to delete and
rebuild; they are not part of the release contract:

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
