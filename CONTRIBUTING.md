# Contributing

Thanks for helping improve the Estonian Legal Ontology. This file covers the
contribution flow and the **validation gates** every change must pass. For the
deeper working conventions (the node `@id` scheme, the integration pipeline, the
SHACL policy) read [AGENTS.md](AGENTS.md) — it is the source of truth.

## Ground rules

- **Do not hand-edit generated artifacts.** `krr_outputs/combined_ontology.jsonld`,
  `INDEX.json`, the `*_peep.json` corpus and the report files are produced by the
  builders in `scripts/`. Change the generator (or use a documented surgical
  post-processor), then regenerate — never patch the output by hand.
- **Reuse the shared helpers** in `src/estleg/estleg_common.py` /
  `src/estleg/riigiteataja_common.py` rather than duplicating parsing or filesystem
  logic.
- **Large artifacts are Git LFS** (`combined_ontology.jsonld`,
  `reports/similarity_index.json`, and the other paths in `.gitattributes`). Run `git lfs pull` if you need the
  real bytes; CI pulls them per-job.

## Workflow

1. Branch off `main`. The convention is `fix/<short-slug>-<issue-number>` (e.g.
   `fix/vos-harmonisation-data-631`) or `feat/<slug>-<issue-number>`.
2. Make the change. Keep new code in the style of the code around it.
3. Run the relevant checks below. Explain existing corpus failures and show
   that your change introduces no new failures; a data release requires the
   full release gates to pass.
4. Open a pull request. Put `Fixes #<n>` (or `Refs #<n>`) in the body so the
   issue is linked/closed on merge. Fill in the pull-request template.

## Validation gates

Install Python 3.11+ tooling from the committed package metadata:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]" -e "mcp_server[dev,http]"
```

The required merge checks are `lint`, `pytest`, and `estleg-mcp tests`.
Run the relevant local checks before pushing (see `.github/workflows/validate.yml`):

```bash
python3 -m ruff check scripts/ src/estleg/ tests/ mcp_server/   # lint
python3 -m pytest -q                                # default suite; corpus marker is opt-in
python3 -m pytest -q mcp_server/tests                # separate MCP package suite
```

For corpus, generator, or validation changes, also run:

```bash
python3 scripts/validate_all.py                     # JSON-LD hygiene + graph closure
python3 scripts/shacl_validate_all.py --all         # SHACL conformance (all buckets)
python3 scripts/validate_seadusloome_sync.py        # consumer load-path, zero-warning gate
```

If your change regenerates `combined_ontology.jsonld`, also run the
`@pytest.mark.corpus` real-artifact tests against the materialised graph:

```bash
python3 -m pytest -q -m corpus
```

The current JSON, corpus-invariant, and broader SHACL failures are recorded in
[docs/VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md). Keep those failures
visible; green required merge checks do not certify a release.

When corpus measurements change, regenerate and verify both reports:

```bash
python3 scripts/generate_validation_report.py
python3 scripts/generate_duplicate_ids_report.py
python3 scripts/generate_validation_report.py --check
python3 scripts/generate_duplicate_ids_report.py --check
```

Documentation changes should pass the CI-pinned Markdown check:

```bash
npx --yes markdownlint-cli@0.41.0 'docs/*.md' README.md
```

## Mandatory legal-correctness review for safety-critical data

This corpus encodes **criminal sanctions, GDPR/penalty ceilings, court holdings,
and EU-directive transposition** — facts a downstream consumer may rely on in a
legal context. A heuristic extraction error here is not a cosmetic bug.

Changes that touch any of the following **require sign-off from a legal-domain
reviewer** in addition to the normal code review, and are routed to the
[CODEOWNERS](.github/CODEOWNERS) for that path:

- **Sanctions** — `krr_outputs/sanctions/**`, `src/estleg/extract_sanctions.py`
- **Deontic classification** (`estleg:normativeType`) — `src/estleg/classify_deontic.py`
- **Court decisions** — `krr_outputs/riigikohus/**`, `krr_outputs/curia/**`,
  `src/estleg/generate_court_decisions.py`,
  `src/estleg/generate_eu_court_decisions.py`,
  `src/estleg/extract_court_provision_links.py`
- **Transposition & harmonisation** — `krr_outputs/reports/transposition_mapping.json`,
  `krr_outputs/harmonisation/**`, `src/estleg/generate_transposition_mapping.py`,
  `src/estleg/generate_harmonisation_links.py`
- **Institutional competence** — `krr_outputs/institutions/**`,
  `src/estleg/extract_institutional_competence.py`

The extraction heuristics live in `src/estleg/`; the same-named entry points in
`scripts/` are thin `runpy` shims, so CODEOWNERS routes the modules rather than
the shims. `tests/test_codeowners.py` enforces that CODEOWNERS and this list
stay in sync.

When a PR changes these paths, flag it in the pull-request template, label it
`needs-legal-review`, and do not merge until the legal-domain reviewer has
confirmed the asserted facts. When the correct value cannot be authoritatively
sourced, prefer **removing the wrong assertion or marking it low-confidence**
over substituting a plausible-but-unverified one.

## Versioning

The published version lives in one place — `estleg_common.ONTOLOGY_VERSION` —
kept in lockstep with `pyproject.toml` (a test enforces this) and stamped onto
the ontology headers. See the **Versioning Policy** in
[docs/RELEASE.md](docs/RELEASE.md) for when and how to bump it and cut a release.
