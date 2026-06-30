# Namespace migration — `data.riik.ee/ontology/estleg#` → `w3id.org/estleg/` (#516)

> This document deliberately contains the **legacy** namespace string (in the
> replacement specs below). It is therefore **excluded** from the migration swap
> AND from the `data.riik.ee == 0` CI guard (alongside `CHANGELOG.md` and
> `docs/superpowers/plans/**`).

## Why

**EstLeg is an independent open-source project and is not affiliated with,
endorsed by, or published by the Estonian government (or any government).** The
legacy `data.riik.ee/ontology/estleg#` namespace was used in pre-release builds
by mistake: that domain was **never assigned to or controlled by this project**.
This is an identifier-**authority/provenance** error, not merely a broken URL —
the IRIs implied official government publication, do not dereference, and cannot
be maintained or redirected by the project. It is corrected here, before any
release, by adopting a project-controlled persistent identifier.

The ontology minted ~170k node IRIs under the legacy namespace. That host is
**NXDOMAIN and owned by the Estonian state (RIA)** — this is an independent
project (seadusloome.sixtyfour.ee), so minting there both fails to dereference
**and falsely implies official government provenance** (related to the
data-rights work in #545). The owner decided (recorded on #516) to move to a
**`https://w3id.org/estleg/`** w3id.org PURL in **slash** form, shipping it as a
*stable identifier now* with the 303 content-negotiating resolver to follow.
This is the **hard predecessor of the v1.0.0 freeze** (#473/#547/#548): the IRI
scheme is the hardest thing to change after release, so it lands first.

**Resolver traceability:** the 303 / content-negotiation resolver is the
*second half* of #516's decision ("resolver later"). It is **not** #550 (which
is the separate "documented REST API is vaporware" access-honesty issue). This
migration delivers only the **namespace swap**; a **dedicated resolver
follow-up ticket must be filed** (and #516 kept open until it lands, or closed
on the swap with the resolver tracked by that new ticket).

Because node `@id`s are stored **compact** (`estleg:Local`), this is a
`@context`-prefix + path-IRI **string swap**, not a 170k-IRI rewrite. It is *not*
a "change-constant-and-regenerate" job — only ~4 generators import the shared
`estleg_common.CONTEXT`/`NS`; the rest hardcode their own literal and the
committed `*_peep.json` are stale regardless. So the mechanism is a deterministic
**string-swap migration over committed files + targeted source edits**, then a
regenerate of the LFS combined aggregate.

## Scale (verified on this checkout)

- **16,114** tracked `*_peep.json` carry the legacy `@context` line (plus several
  thousand more JSON-LD overlays/aggregates — total JSON-LD files with the
  namespace are greater).
- **30** `scripts/*.py` contain the literal (≈26 declare a namespace constant;
  the rest reference it in a guard/comment).
- **44** test files (`tests/` **recursive** — incl. subdirectories) hold the
  literal. Drive the walk off `git ls-files` (recursive), **not** a `tests/*.py`
  glob, or the subdirectory tests are missed.
- **≈2,797** expanded `@id`/`rootIri` literals (after the doc/historical
  exclusions; exact count is grep-pattern dependent, ~2,795–2,797).

## The three ordered replacements

1. `https://data.riik.ee/ontology/estleg#` → `https://w3id.org/estleg/`
   — namespace; hash→slash; covers `@context` values **and** the ≈2,797 expanded
   `@id`/`rootIri` literals.
2. `https://data.riik.ee/ontology/estleg` → `https://w3id.org/estleg`
   — run **after** #1; catches the bare ontology IRI and the version IRI
   `…/estleg/0.11.0`.
3. `https://data.riik.ee/dataset/` → `https://w3id.org/estleg/dataset/`
   — dataset IRI + the `#compilation` sub-resource.

Resulting IRI shapes: term `https://w3id.org/estleg/<Local>`, ontology
`https://w3id.org/estleg`, version `https://w3id.org/estleg/0.11.0`, dataset
`https://w3id.org/estleg/dataset/estonian-legal-ontology(#compilation)`.

## How

### 1. `scripts/migrate_namespace.py`
Reuses the host-agnostic scaffolding from `scripts/migrate_uris.py`
(`_atomic_write_text` `:110`; the dry-run→report→apply skeleton) and the
value-walk precedent `fix_all_issues.migrate_namespace_in_value` (`:319`).
- Drives off **`git ls-files`** (recursive — covers `data/**`, `metadata.jsonld`,
  `README.md`, and all `tests/` subdirs).
- Copies the `_is_lfs_pointer` guard and **skips pointer stubs** (so a CI/fresh
  checkout never half-swaps an un-pulled LFS file — see step 3).
- **Excludes** from the swap: `CHANGELOG.md`, `docs/superpowers/plans/**`, **and
  this file `docs/NAMESPACE_MIGRATION.md`** (all hold legacy strings on purpose).
- Applies the three replacements to file **content** (byte-preserving). Dry-run
  is the default; `--apply` writes; idempotent (re-run = 0 changes).
- **Separate state/report:** writes its sentinel + dry-run report to
  **`data/namespace_migration_state.json`** (NOT `data/migration_state.json`,
  which is the #83/#192 URI-migration sentinel — must not be clobbered). The
  state/report stores only the **new** namespace + counts + a corpus hash —
  **never the legacy literal** (a dry-run report that echoed old values would
  itself trip the `data.riik.ee == 0` guard). As belt-and-braces, the state/report
  file is also added to the guard's exclusion list.

### 2. Targeted code-logic edits (the swap can't do these mechanically) — **two confirmed sites**
- `scripts/estleg_common.py` — `ONTOLOGY_IRI = NS.rstrip("#")` → `NS.rstrip("#/")`
  (else the slash NS yields a double-slash version IRI `…/estleg//0.11.0`).
- `scripts/generate_act_expressions_608.py` — `act_local_fragment()` (`:78-86`)
  extracts the local name with `act_iri.rsplit("#", 1)[1]` under an
  `if "#" in act_iri` guard. On a slash IRI it **falls through that guard to the
  final `return act_iri`** and returns the WHOLE IRI (NOT an `IndexError`) →
  malformed compact IDs like `estleg:https://w3id.org/estleg/VKVS_Map_2026_Expr_…`.
  Make it separator-agnostic (e.g. split on the final `#` **or** `/`, or strip the
  `NS` prefix) and update its docstring + add a unit test for the slash form.
- Sweep result (`split('#')`/`rsplit('#')` across `scripts/` + `tests/`): only
  these matter. `generate_eu_court_decisions.py:257` splits a *title*, and
  `test_tbox_conformance_cycle2.py:275` splits SHACL `ttl` on a `# Shape …`
  comment marker — both unrelated. `estleg_common.canonical_estleg_ref` /
  `iter_node_estleg_refs` use `startswith(NS)`/`ref[len(NS):]` (separator-agnostic
  → auto-correct).
- All other literal sites (the `EXPECTED_NS` guard in `validate_all.py`, the ~26
  generator `NS`/`CONTEXT` constants, `fix_duplicate_ids.py` class-IRI lists,
  `shacl/estonian_legal_shapes.ttl`, `metadata.jsonld`, `docs/RELEASE.md`,
  `README.md`, the 44 `tests/` fixtures) are value-only → handled by the swap.
- **Two bare-prose residuals** the three structured replacements do NOT catch (a
  simulated swap confirms these are the *only* two): stale docstrings in
  `scripts/fix_all_issues.py:24-26` and `tests/test_fix_all_issues.py:1189` that
  bare-mention `data.riik.ee` while describing the historical
  `example.org → data.riik.ee` repair pass. **Manually reword** them to the
  current `w3id.org/estleg` namespace (dropping the bare `data.riik.ee`) so the
  guard reaches zero — no 4th blind replacement, since bare `data.riik.ee` has no
  single safe target.

### 3. LFS — pull **before** the swap, then regenerate combined
- `git lfs pull` the namespace-bearing LFS files **first** so the swap operates
  on real content, not pointer stubs:
  `krr_outputs/combined_ontology.jsonld` (1,394 hits — regenerated, not swapped),
  and the three single-line sidecars
  `krr_outputs/curia/curia_combined.jsonld`,
  `krr_outputs/eurlex/eurlex_combined.jsonld`,
  `krr_outputs/annotations/oiguskantsler_seisukohad.jsonld` (1 `@context` line
  each — swapped by the migration once materialised).
- Then rebuild `krr_outputs/combined_ontology.jsonld` via
  `generate_combined_jsonld()` (imported directly — NOT by running
  `fix_all_issues.py`, which runs all repair passes). Run the migration **after**
  the LFS pull, or re-run it after pulling, so no un-materialised sidecar is left
  on the legacy namespace.

### 4. CI guard — enforce completeness
Add a corpus-wide `git grep 'data.riik.ee' == 0` assertion, **excluding**
`CHANGELOG.md`, `docs/superpowers/plans/**`, `docs/NAMESPACE_MIGRATION.md`, and
`data/namespace_migration_state.json`.
Place it in the LFS-materialised `json-validation` job of
`.github/workflows/validate.yml` (after its `git lfs pull`) **and** as a
`@pytest.mark.corpus` test `tests/test_no_legacy_namespace.py` (runs in the
LFS-materialised `-m corpus` step). NB: the default `pytest` job has no LFS pull
→ a guard there would false-pass the combined pointer; it MUST run LFS-materialised.

### 5. w3id registration prep (external action by owner)
Add `w3id/estleg/{.htaccess,README.md}` to be copied into a PR against
`perma-id/w3id.org` (the README there asks for exactly a directory with
`.htaccess` + `README.md`, then a PR). Resolver deferred → the `.htaccess`
303-redirects to the GitHub artifacts for now. **The owner submits that PR**
(needs their GitHub identity + maintainer contact). Note: `https://w3id.org/estleg/`
currently 404s — registration is not yet done.

## Verification
- Idempotency/completeness: re-run the migration → 0 changes;
  `git grep -c data.riik.ee` (minus the excluded paths) == 0.
- Gates: `ruff`, full `pytest -q` (incl. the `NS == NEW_NS` drift gate, the new
  `act_local_fragment` slash test, and the 44 swapped fixtures), `validate_all.py`,
  `validate_combined_standalone.py`, `validate_seadusloome_sync.py`,
  `shacl_validate_all.py --all`.
- Spot-check: a sampled peep `@context.estleg` == `https://w3id.org/estleg/`;
  combined header `owl:versionIRI` == `https://w3id.org/estleg/0.11.0`
  (single slash); a README SPARQL example still returns rows.

## Scope
This change is **#516 (namespace swap) only** — one atomic PR (a half-migrated
corpus fails the closure / namespace gates); diff is large (~16k peeps + overlays)
but mechanical, gated by `data.riik.ee == 0`. **Deferred** to later freeze-track
PRs: the 303 resolver (new ticket — not #550); keeping version `0.11.0` (the
v1.0.0 bump is #473); centralizing the ~26 generators onto `estleg_common.NS`/
`CONTEXT` (a follow-up refactor, orthogonal to this swap); and `#547`
(CITATION.cff/DOI) + `#548` (reproducible release) + `#473` (v1.0.0 tag).
