# Release build DAG

`scripts/run_all_integration.py` owns the enrichment pipeline **and** the
release build. The 14 enrichment steps are declared as an explicit,
declarative directed acyclic graph (DAG); the runner topologically sorts it,
runs it (serially by default), then — in `--release` mode — runs the three
release validators and writes a release-wide manifest aggregating everything.

This document covers:

1. [The step DAG](#the-step-dag)
2. [Running a release build](#running-a-release-build) — `--release`
3. [Validating without rebuilding](#validating-without-rebuilding) — `--release --validate-only`
4. [The `release_manifest.json` schema](#the-release_manifestjson-schema)
5. [Committed-vs-release-asset policy](#committed-vs-release-asset-policy) — which files live in git, which are regenerable build artifacts

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
- the dependency graph has a cycle (Kahn's algorithm), or
- a step's `reads` glob is neither a [committed input](#committed-vs-release-asset-policy)
  (`run_all_integration.py:COMMITTED_INPUTS`) nor covered by some *prior*
  step's `writes`.

The topological order ties are broken by source order, so the historical
phase order is preserved exactly.

### Steps, dependencies, and outputs

| # | Step (`scripts/…`) | `depends_on` | Key `writes` (under `krr_outputs/`) |
|---|---|---|---|
| 1 | `extract_cross_references.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `cross_references_report.json` |
| 2 | `generate_inverse_references.py` | `extract_cross_references.py` | `*_peep.json`, `regulations/**/*_peep.json`, `inverse_references_report.json` |
| 3 | `generate_transposition_mapping.py` | — | `*_peep.json`, `transposition_mapping.json` |
| 4 | `generate_harmonisation_links.py` | `generate_transposition_mapping.py` | `*_peep.json`, `harmonisation/harmonisation_report.json` |
| 5 | `extract_court_provision_links.py` | — | `riigikohus/*_peep.json`, `*_peep.json`, `court_provision_links_report.json` |
| 6 | `classify_eurovoc.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `eurovoc_classification.json` |
| 7 | `extract_temporal_data.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `temporal_data_report.json` |
| 8 | `generate_amendment_history.py` | — | `amendments/**/*.json`, `*_peep.json`, `amendment_history_report.json` |
| 9 | `extract_legal_concepts.py` | — | `concepts/**/*.json`, `*_peep.json` |
| 10 | `classify_deontic.py` | — | `*_peep.json`, `regulations/**/*_peep.json`, `deontic_classification_report.json` |
| 11 | `extract_institutional_competence.py` | — | `institutions/**/*.json`, `*_peep.json`, `institutional_competence_report.json` |
| 12 | `extract_sanctions.py` | — | `sanctions/**/*.json`, `*_peep.json`, `sanctions_report.json` |
| 13 | `extract_draft_impact.py` | — | `*_peep.json`, `draft_impact_report.json` |
| 14 | `generate_similarity_index.py` | steps 1–13 (all) | `similarity_index.json`, `similarity_report.json` |

ASCII view of the dependency edges (everything not shown is a no-dependency
root that the topo sort places in source order; step 14 fans in from all
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
satisfied), but because the steps share files it is genuinely useful only
when the corpus is split per-step in future work — the declarative DAG is
the deliverable here, parallelism is gravy. `--parallel` is mutually
exclusive with `--validate-each` (the validator reads the whole corpus
mid-flight).

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
3. Runs all 14 steps in topo order. A failed step skips its dependents; the
   first hard failure stops the run and the snapshot is restored.
4. If — and only if — every step succeeded, runs the three release
   validators in order:
   - `python3 scripts/validate_all.py` — per-file corpus + aggregate parity
   - `python3 scripts/shacl_validate_all.py --all` — full-corpus SHACL conformance
   - `python3 scripts/validate_seadusloome_sync.py` — Seadusloome zero-warning gate
5. Writes `krr_outputs/reports/integration/release_manifest.json`.
6. Exits **0 only if `release_ok`** — i.e. all 14 steps succeeded **and**
   all three validators passed. Otherwise exit 1.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Print the DAG topo order + validators; run nothing; exit 0. |
| `--resume-from <step>` | Skip steps before `<step>` in topo order (treated as already done so dependents are not blocked). |
| `--no-restore-on-failure` | Leave a partial `krr_outputs/` tree in place on failure instead of rolling back. |
| `--validate-each` | Run `validate_all.py` after each successful step. Incompatible with `--parallel`. |
| `--per-script-timeout N` | Per-step (and per-validator) timeout in seconds (default 1800; a timeout is recorded as exit code 124). |
| `--parallel N` | Run up to N dependency-ready steps concurrently (default 1 = serial). |

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
  "stepSummary": { "totalSteps": 14, "succeeded": 14, "failed": 0,
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
    "missing": [],                              // any expected artifact not found
    "contentHash": "…64 hex…"                   // sha256 over sorted "path\nsha256\n" lines
  },

  "releaseOk": true                             // steps all OK AND validators all passed;
                                                // null in --dry-run
}
```

`contentHash` is a single stable digest of the whole **committed release
surface** — compare two release manifests to explain corpus/artifact drift
(per-file hashes pinpoint exactly what changed).

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
  `krr_outputs/institutions/` (the shaped per-item JSON-LD)
- the cross-corpus indexes/maps `krr_outputs/similarity_index.json`,
  `krr_outputs/eurovoc_classification.json`,
  `krr_outputs/transposition_mapping.json`
- the per-domain `*_report.json` summaries at the `krr_outputs/` root
  (`cross_references_report.json`, `inverse_references_report.json`,
  `court_provision_links_report.json`, `temporal_data_report.json`,
  `amendment_history_report.json`, `deontic_classification_report.json`,
  `institutional_competence_report.json`, `sanctions_report.json`,
  `draft_impact_report.json`, `similarity_report.json`) and the per-bucket
  coverage reports under `krr_outputs/reports/kov/*_coverage.json`
- the source-fetch manifests `krr_outputs/generation_manifest_*.json`

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
> `generate_*` / `fix_all_issues.py` steps) reproduces a file from committed
> inputs, it is a **build artifact** — commit it on release for downstream
> consumers and reviewers, but it is reconstructible. If you *type into* a
> file by hand, it is **source** and must be committed. Anything written only
> under `krr_outputs/reports/integration/` or a `.cache/`/`.bak.`/`.failed.`
> path is **transient** and need not be committed at all.
