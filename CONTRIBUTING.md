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
- **Reuse the shared helpers** in `scripts/estleg_common.py` /
  `scripts/riigiteataja_common.py` rather than duplicating parsing or filesystem
  logic.
- **Large artifacts are Git LFS** (`combined_ontology.jsonld`,
  `reports/similarity_index.json`, and four others). Run `git lfs pull` if you need the
  real bytes; CI pulls them per-job.

## Workflow

1. Branch off `main`. The convention is `fix/<short-slug>-<issue-number>` (e.g.
   `fix/vos-harmonisation-data-631`) or `feat/<slug>-<issue-number>`.
2. Make the change. Keep new code in the style of the code around it.
3. Run the gates below — all must be green.
4. Open a pull request. Put `Fixes #<n>` (or `Refs #<n>`) in the body so the
   issue is linked/closed on merge. Fill in the pull-request template.

## Validation gates

Run these locally before pushing; CI runs the same set (see
`.github/workflows/validate.yml`):

```bash
python3 -m ruff check scripts/ src/estleg/ tests/ mcp_server/   # lint
python3 -m pytest -q                                # unit + real-corpus tests
python3 scripts/validate_all.py                     # JSON-LD hygiene + graph closure
python3 scripts/shacl_validate_all.py --all         # SHACL conformance (all buckets)
python3 scripts/validate_seadusloome_sync.py        # consumer load-path, zero-warning gate
```

If your change regenerates `combined_ontology.jsonld`, also run the
`@pytest.mark.corpus` real-artifact tests against the materialised graph:

```bash
python3 -m pytest -q -m corpus
```

## Mandatory legal-correctness review for safety-critical data

This corpus encodes **criminal sanctions, GDPR/penalty ceilings, court holdings,
and EU-directive transposition** — facts a downstream consumer may rely on in a
legal context. A heuristic extraction error here is not a cosmetic bug.

Changes that touch any of the following **require sign-off from a legal-domain
reviewer** in addition to the normal code review, and are routed to the
[CODEOWNERS](.github/CODEOWNERS) for that path:

- **Sanctions** — `krr_outputs/sanctions/**`, `scripts/extract_sanctions.py`
- **Deontic classification** (`estleg:normativeType`) — `scripts/classify_deontic.py`
- **Court decisions** — `krr_outputs/riigikohus/**`, `krr_outputs/curia/**`,
  `scripts/generate_court_decisions.py`, `scripts/extract_court_provision_links.py`
- **Transposition & harmonisation** — `krr_outputs/reports/transposition_mapping.json`,
  `krr_outputs/harmonisation/**`, `scripts/generate_transposition_mapping.py`,
  `scripts/generate_harmonisation_links.py`
- **Institutional competence** — `krr_outputs/institutions/**`,
  `scripts/extract_institutional_competence.py`

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
