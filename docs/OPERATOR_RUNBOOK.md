# Operator runbook

How to refresh the corpus from its sources, rebuild the derived layers, run
the gates and package a release. These commands change `krr_outputs/` and
most of them call external services. Readers who only load or query the data
need the [root README](../README.md), [API_GUIDE.md](API_GUIDE.md) or the
[MCP server](../mcp_server/README.md) instead.

The step DAG, the release manifest and the asset policy are specified in
[RELEASE.md](RELEASE.md). This runbook is the operator's order of work; when
the two disagree, RELEASE.md and the code win.

## 0. Before you start

```bash
python3 -m pip install -e ".[dev]" -c constraints.txt   # Python 3.11+
git lfs pull                                            # combined aggregates are LFS
python3 scripts/check_rt_staleness.py                   # which corpora are behind budget
```

Every `python3 scripts/<name>.py` command below is a shim onto
`python3 -m estleg.<name>`. The installed console scripts are
`estleg-generate-laws`, `estleg-run-pipeline` and `estleg-validate`.

Choose one snapshot date (`--kehtiv YYYY-MM-DD`) for the whole refresh and
record it in the commit message. A zero exit status is not proof of a complete
refresh: read the manifest or index each generator writes.

## 1. Refresh the source corpora (network)

Riigi Teataja is read through its search API
(`/api/oigusakt_otsing/1/otsi`) and, since the RT relaunch on 2026-06-01,
the public API for act XML and metadata
(`/public-api/api/v1/akt/{id}/xml` and `/public-api/api/v1/akt/{id}`). Both
are wrapped by `src/estleg/riigiteataja_common.py`. An HTML answer where XML
was expected raises `RTFormatError` and stops the run.

```bash
# Enacted laws. Default mode is --missing-only, which also re-fetches a file
# whose stored estleg:kehtiv no longer matches --kehtiv.
python3 scripts/generate_all_laws.py --missing-only --kehtiv 2026-05-01
python3 scripts/generate_all_laws.py --refresh --kehtiv 2026-05-01
python3 scripts/generate_all_laws.py --force --kehtiv 2026-05-01
# Resume a long run after an interruption:
python3 scripts/generate_all_laws.py --refresh --kehtiv 2026-05-01 --regen-state
# Replay the law list of an earlier run (per-act XML is still fetched):
python3 scripts/generate_all_laws.py --from-manifest krr_outputs/generation_manifest_laws.json
# The run writes counts, the unchanged-vs-refreshed split and source-removed
# files (reported, never deleted) to krr_outputs/generation_manifest_laws.json.

# State and municipal regulations. Source-list failures are fatal unless
# --allow-partial is given.
python3 scripts/generate_regulations.py --refresh --kehtiv 2026-05-01
python3 scripts/generate_regulations.py --refresh --kehtiv 2026-05-01 --kov

# Municipal issuer registry (after a KOV refresh)
python3 scripts/build_kov_registry.py
python3 scripts/enrich_kov_layer1.py

# Riigikogu otsused and presidential seadlused (title index + capped bodies)
python3 scripts/generate_rt_act_kinds.py --ingest-limit 12

# Draft legislation from the EIS RSS feeds
python3 scripts/generate_draft_legislation.py

# Riigikohus decisions from rikos.rik.ee, then full text (cached, resumable)
python3 scripts/generate_court_decisions.py
python3 scripts/generate_court_decisions.py --fetch-full-text --full-text-limit 0

# Lower-court sample (never a full corpus; flagged estleg:isSampleData)
python3 -m estleg.generate_lower_court_decisions --fetch --year 2026 --limit 50 --apply

# EU legislation and EU court decisions from the CELLAR SPARQL endpoint
python3 scripts/generate_eu_legislation.py
python3 scripts/generate_eu_court_decisions.py
```

After a Riigikohus refresh, run its personal-code screening before committing.
This command targets the Riigikohus decision type and directory; it is not a
screening pass for CURIA or the lower-court corpus. Run the validation gates
below and follow each corpus's policy in DATA_PROTECTION.md.

```bash
python3 scripts/screen_court_personal_data.py
```

Personal-data handling is described in [DATA_PROTECTION.md](DATA_PROTECTION.md).

## 2. Rebuild the derived layers

`scripts/run_all_integration.py` runs the 29-step DAG in
`src/estleg/run_all_integration.py`: ingest-tier sidecars, cross-references,
EU transposition, enrichment classifiers, the combined build
(`build_release_artifacts.py`), the analytical overlay and the release assets
(`build_release_assets.py`). It snapshots `krr_outputs/` and restores it if a
step fails.

```bash
python3 scripts/run_all_integration.py --dry-run          # print the topo order
python3 scripts/run_all_integration.py --validate-each    # serial, validate after each step
python3 scripts/run_all_integration.py --with-ingest      # also fetch the ingest-tier sidecars
python3 scripts/run_all_integration.py --resume-from classify_eurovoc.py
```

Ingest-tier steps (`generate_provision_versions.py`, the regulation version
sidecars, `generate_annotations.py`) are skipped unless `--with-ingest` is
given; their committed outputs are used instead. `--parallel N` with N > 1 is
rejected for the current DAG because steps share `*_peep.json` writes.

A single step can be run on its own, for example
`python3 scripts/extract_cross_references.py`. Run its DAG dependencies first.
The full list and dependencies are in [RELEASE.md](RELEASE.md#the-step-dag).
Reviewed corrections to classifier output go in
`data/heuristic_overrides.jsonl` ([HEURISTIC_OVERRIDES.md](HEURISTIC_OVERRIDES.md)),
never into the generated files.

## 3. Run the gates

```bash
python3 -m ruff check scripts/ src/estleg/ tests/ mcp_server/
python3 -m pytest -q                    # default tier, no LFS needed
python3 -m pytest -q -m corpus          # LFS / whole-corpus gates
python3 scripts/validate_all.py
python3 scripts/shacl_validate_all.py --all
python3 scripts/validate_seadusloome_sync.py
python3 scripts/check_rt_staleness.py
```

Then regenerate the measured reports and confirm them. Never edit their
numbers by hand.

```bash
python3 scripts/generate_validation_report.py
python3 scripts/generate_duplicate_ids_report.py
python3 scripts/generate_validation_report.py --check
python3 scripts/generate_duplicate_ids_report.py --check
```

If headline counts changed, update `metadata.jsonld` (`estleg:statistics`),
the README status line and the counts block in [ANDMED.et.md](ANDMED.et.md).
Tests compare all three against the corpus.

## 4. Release build and assets

```bash
python3 scripts/run_all_integration.py --release                  # full DAG + three validators
python3 scripts/run_all_integration.py --release --validate-only  # validators only
python3 scripts/build_release_assets.py                           # release/ + SHA256SUMS
```

`--release` writes `krr_outputs/reports/integration/release_manifest.json` and
exits 0 only when `release_ok` is true. `build_release_assets.py` writes the
downloadable files to the git-ignored `release/` directory and refuses
unstamped combined heads unless `--allow-unstamped` is given. Uploading
`release/` to the GitHub Release stays a manual step. Versioning rules are in
[RELEASE.md](RELEASE.md#versioning-policy).

## 5. Freshness budgets

`check_rt_staleness.py` compares each corpus stamp against today. The budgets
(`CORPUS_BUDGETS`) are 45 days for laws, 60 days for regulations and drafts,
and 120 days for court and EU corpora. `--fetch` additionally asks the RT
public API whether a newer consolidation exists; `--schema-canary` checks the
live XML schema. Both are network calls. See
[RELEASE.md](RELEASE.md#refresh-sla).
