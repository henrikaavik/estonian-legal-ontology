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
