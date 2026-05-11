# AGENTS.md - Estonian Legal Ontology

This repository builds, enriches, and validates JSON-LD outputs for the Estonian
Legal Ontology. Keep changes focused on ontology data quality, generator
correctness, validation gates, and project documentation.

## Repo Layout

- `scripts/` - corpus generators, enrichment scripts, validation commands, and
  integration orchestration.
- `tests/` - unit and regression tests for generator behavior and validators.
- `shacl/` - SHACL shapes used by local validation and downstream sync gates.
- `krr_outputs/` - generated JSON-LD corpus and aggregate artifacts.
- `docs/` - schema references, validation notes, and release documentation.
- `.github/workflows/validate.yml` - CI validation entry point.

## Generated Artifacts

- Do not edit `krr_outputs/combined_ontology.jsonld` by hand. Regenerate it
  through the canonical builder in `scripts/fix_all_issues.py`.
- Treat root `*_peep.json` files and the documented allowlisted JSON-LD files as
  canonical combined inputs.
- If generated data changes, run the parity and SHACL gates before considering
  the artifact release-ready.
- Avoid timestamp-only churn and nondeterministic ordering in generated outputs.

## Node ID naming convention

The adopted shape for ontology node `@id`s under the `estleg:` prefix is the
**underscore-separated, abbreviation-prefixed** form
`estleg:<ABBREV>_<segment>_<segment>…`. This was migrated in commits
`3880cb688` and `c5c0f88b2` (issue #83); the slash-hierarchy proposal in the
original ticket (`estleg:provision/PKS/par-12`) was a sketch, not a
requirement — underscores are SPARQL-friendlier (no escaping needed in
`PREFIX`-shortened names) and are already shipped, so underscores are the
convention.

- **`<ABBREV>`** is the law's compact abbreviation from
  `data/law_abbreviations.json` (≤ 12 chars). Preference order, highest first:
  the official Riigi Teataja `lyhend` (e.g. `PKS`, `KarS`, `TsÜS` — Estonian
  letters are allowed); else a short prefix already used in the corpus; else an
  acronym auto-derived from the title (stop words dropped, treaty years
  appended). Collisions are resolved by suffixing `_2`, `_3`, … The registry,
  the old→new rename map, and the apply/idempotency machinery all live in
  `scripts/migrate_uris.py`; see also `data/migration_state.json` (the sentinel
  recording the registry+corpus hashes the migration was last applied at) and
  `data/uri_migration_report.json` (the last dry-run preview).
- **Segments** encode the law's structure, e.g. `_Par_<n>` (section §),
  `_Lg<n>` (lõige / subsection), `_Osa<n>` (osa / part), `_Chapter_<n>`,
  `_Division_<n>`, `_Map_2026` (act-level topic map node), `_TopicScheme`,
  `Cluster_<ABBREV>_<Label>` (concept cluster). Superscripted section numbers
  become a trailing `_<n>`: `§ 22¹` → `…_Par_22_1`. Bare-number forms such as
  `_Par271` are normalised to `_Par_271` by the migration tool.
- **Non-law node families are intentionally not abbreviation-prefixed.**
  `Amendment_*`, `AmendmentChain_*`, `AmendmentLink_*`, `Citation_*`,
  `Sanction_*` / `Sanctions_*`, `Draft_*`, `Institution_*` etc. use a
  `<Type>_<key-or-hash>` shape (for amendments/drafts the key is the Riigi
  Teataja document id plus an ordinal, e.g.
  `estleg:Amendment_<base_slug>_t322297_1`,
  `estleg:Draft_SOM23_0398`). These are out of scope for `migrate_uris.py`'s
  prefix remap, which only rewrites the law/regulation
  `<prefix>_Par_/_Lg/_Osa/_Map_/Cluster_/LegalProvision_/LegalConcept_`
  families.
- **The "≤ 60 chars" goal is met for the law/provision/cluster families but
  not corpus-wide.** ~78k `@id`s still exceed 60 chars, almost all
  `Amendment_*` / `AmendmentChain_*` / `AmendmentLink_*` where the length comes
  from a long `<base_slug>`, plus a small tail of raw-slug provision nodes
  (`estleg:rakkude_…`, `estleg:narkootiliste_…`). This is a length artefact of
  `base_slug`, not a naming-scheme defect; a future cleanup could shorten
  amendment ids by substituting the law abbreviation for `base_slug`
  (`estleg:Amendment_<ABBREV>_t322297_1`) — track that as a follow-up rather
  than blocking on it.
- **Registry coverage is ~601 of the ~608 laws in `krr_outputs/INDEX.json`.**
  The handful not in `data/law_abbreviations.json` are entries whose
  `owl:Ontology` node either has no `dc:source`/`dcterms:title` (so
  `load_peep_prefixes` skips them even though the `@id` already carries a
  short prefix — e.g. `estleg:KONKS_Map_2026`, `estleg:PANKR_Map_2026`,
  `estleg:TLS_Map_2026`), or uses an ontology-`@id` shape outside the
  `_Map_2026` / `_OsaN` patterns (`estleg:ARIS_Ontology_v1`,
  `estleg:KrMS_ProcedureMap_2026` — the latter is also flagged in
  `INDEX.json`'s `registry_exceptions`), plus two `*_owl.jsonld` artifacts
  (`karistusseadustik_eriosa_owl`, `tsus_osa7_138_169_owl`) that aren't
  `*_peep.json` files. None of these need a corpus rename — they already use
  compact prefixes — they're just absent from the registry file. Adding them
  would mean broadening `load_peep_prefixes` (accept `rdfs:label` as a title
  fallback; parse `_ProcedureMap_2026` / `*_Ontology_v1`; also scan
  `*_owl.jsonld`) and regenerating the registry; treat that as a follow-up.
- **When generating new nodes**, reuse the registry abbreviation for the law
  rather than re-slugifying the title, and keep the human-readable name in
  `rdfs:label`, not in the `@id`.

## Validation Commands

Use the narrowest relevant command while developing, then broaden before
finishing data-quality work:

```bash
python3 -m ruff check scripts/ tests/
python3 -m pytest -q
python3 scripts/validate_all.py
python3 scripts/shacl_validate_all.py --all
python3 scripts/validate_seadusloome_sync.py --report seadusloome-shacl-report.ttl
```

`scripts/validate_seadusloome_sync.py` mirrors the downstream Seadusloome load
path: `combined_ontology.jsonld` plus the `eelnoud`, `riigikohus`, `curia`, and
`eurlex` subcorpora, with SHACL inference disabled.

## Dependency Setup

Install project tooling from the committed Python metadata:

```bash
python3 -m pip install -e ".[dev]"
```

Supported Python versions are declared in `pyproject.toml`.

## Integration Pipeline

`scripts/run_all_integration.py` documents the intended enrichment order. When
changing one phase, check downstream phases that consume its outputs. In
particular:

- transposition mapping feeds harmonisation links,
- EuroVoc subjects feed act-level discovery and metadata,
- KOV issuer and citation enrichment feed KOV validation and coverage reports,
- combined ontology regeneration must happen before consumer sync validation.

## SHACL Policy

See `shacl/README.md` for severity policy. Omitted severity is a violation by
default; explicit `sh:Warning` is reserved for quality and coverage checks that
the zero-warning gate still reports distinctly.

## Working Practice

- Prefer existing helpers in `scripts/estleg_common.py` and
  `scripts/riigiteataja_common.py` over duplicating parsing or filesystem
  logic.
- Add focused regression tests for every bug fix.
- Keep public metadata counts and validation documentation in sync with corpus
  discovery.
- Do not hide missing corpus inputs behind `pytest.skip` when a test is meant to
  be a release or invariant gate.
- Never commit credentials, private data, local memory files, or machine-specific
  state.
