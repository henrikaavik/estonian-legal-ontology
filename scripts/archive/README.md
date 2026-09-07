# Spent one-shot scripts

These migrations already ran against the corpus. Do **not** run them on the
live tree.

- `migrate_multipart_iri_scheme.py` — multipart `_OsaN` IRI rewrite (spent).
- `fix_duplicate_ids.py` — cross-file `@id` deduplicator (spent).
- `rederive_court_case_types.py` — #342 caseType peep rewrite (spent;
  `generate_court_decisions.classify_case` emits the correct type).
- `backfill_eu_provenance.py` — #348 CELEX provenance peep rewrite (spent;
  `generate_eu_legislation` / `generate_eu_court_decisions` emit the fields).
- `legacy_repairs.py` — emergency-only `normalize_*` / duplicate-id passes
  that used to run inside `fix_all_issues.main()`. Requires `--yes`. The
  release builder is `scripts/build_release_artifacts.py`.

`migrate_uris.py` stays in `scripts/`. It still owns the URI registry and
must not be archived with this directory.
