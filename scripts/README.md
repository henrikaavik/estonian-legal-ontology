# `scripts/` inventory (#535)

| Bucket | Role | Examples |
|---|---|---|
| **live-DAG** | `run_all_integration.py` steps | `extract_cross_references.py`, `classify_eurovoc.py`, `build_release_artifacts.py` |
| **live-DAG / live infra** | Release + DAG; do not archive | `deprecate_legacy_statutes.py` |
| **generator / ingest** | Fetch RT/EUR-Lex/EIS and emit peeps | `generate_all_laws.py`, `generate_regulations.py`, `generate_court_decisions.py`, `generate_draft_legislation.py`, `generate_eu_legislation.py`, `generate_eu_court_decisions.py` |
| **sidecar** | Versions, annotations, expressions, retrieval | `generate_provision_versions.py`, `generate_annotations.py`, `generate_act_expressions_608.py`, `generate_retrieval_projection.py` |
| **validator** | Release gates | `validate_all.py`, `shacl_validate_all.py`, `validate_seadusloome_sync.py`, `validate_combined_standalone.py` |
| **shared** | Commons (live in `src/estleg/`; `scripts/` keeps shims, #472) | `estleg_common.py`, `riigiteataja_common.py`, `eurlex_common.py`, `kov_registry.py` |
| **spent-migration / one-shot** | Run once, keep for regen | `migrate_uris.py` (stays in `scripts/`), `migrate_namespace.py`, `backfill_*`, `fix_*_NNN.py` |
| **archived** | Spent one-shots; do not run on the live corpus | `scripts/archive/migrate_multipart_iri_scheme.py`, `scripts/archive/fix_duplicate_ids.py`, `scripts/archive/rederive_court_case_types.py`, `scripts/archive/backfill_eu_provenance.py`, `scripts/archive/legacy_repairs.py` |
| **manual-ops** | Fitness / live coverage infra | `eval_harness.py`, `kov_pipeline_coverage.py`, `verify_layer1.py` |

New corpus truth belongs in a generator or DAG step, not a new `fix_*_<issue>.py`.

`deprecate_legacy_statutes.py` is **live-DAG / live infra** for #426 (`fix_all_issues.py` release step + `validate_all.py` gate), not a spent one-shot. `kov_pipeline_coverage.py` is live coverage infra imported by DAG enrichers — it stays under **manual-ops**; the name is not a spent-KOV leftover.
