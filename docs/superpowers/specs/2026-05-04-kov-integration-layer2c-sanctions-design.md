# KOV Integration Layer 2c — Sanctions (PR #1 of 3)

**Date:** 2026-05-04
**Status:** Design approved by user. Awaiting written-spec review.
**Parent spec:** `docs/superpowers/specs/2026-05-02-kov-integration-design.md` (Layer 2c — Sanctions section, lines 510-516).
**Predecessors:** Layer 2a (#98, merged), Layer 2b (#99, merged 2026-05-04).

## Goal

Unpin `extract_sanctions.py` so it processes the full corpus (laws +
state regulations + KOV acts) by default, and stamp
`estleg:enforcedAtLevel` on every provision that carries a sanction.
Classification is purely by parent act type — `MunicipalRegulation`
maps to `"municipality"`; everything else maps to `"state"`.

## Crisp invariant

After the PR lands, all of the following hold:

1. `extract_sanctions.py` calls `iter_peep_files()` with no
   `include_kov=False` argument. The single `# DEFERRED to Layer 2c`
   pin (line ~510) is removed.
2. Every provision that gains an `estleg:hasSanction` triple in this
   run ALSO gains exactly one `estleg:enforcedAtLevel` literal.
3. `enforcedAtLevel` value is `"municipality"` iff the parent act has
   `estleg:MunicipalRegulation` in its `@type`; otherwise `"state"`.
4. Sanction extraction on KOV acts runs on the same regex bank used
   for state laws — no KOV-specific extraction code added in this PR.
5. Coverage report `krr_outputs/reports/kov/extract_sanctions_coverage.json`
   shows `files_with_output_kov > 0` and `triples_emitted_kov > 0`.
6. Re-runs are idempotent at BOTH layers:
   - Provision-level: a provision that previously had `hasSanction` +
     `enforcedAtLevel` and no longer matches the sanction regex loses
     BOTH triples on the next run.
   - Sanction-file-level: when the act loses all its sanction matches
     between runs, the corresponding `krr_outputs/sanctions/sanctions_<law>.json`
     file is **deleted** so stale `Sanction_*` nodes don't linger
     pointing at provisions that no longer carry `hasSanction`.
     Deletion (not empty-graph overwrite) keeps the directory
     listing semantically accurate: the presence of a sanctions file
     means the act has at least one sanction.
7. Targeted SHACL: `LegalProvisionShape` gains an explicit property
   constraint for `enforcedAtLevel` in this PR (datatype `xsd:string`,
   `sh:maxCount 1`, value set `"state"` ∪ `"municipality"`). Zero new
   violations against this constraint.

**Real-world impact estimate** (read-only dry count over the current
corpus): about 82 KOV files gain sanction triples, with ~85 sanction
records total. Existing state-side sanction count is unchanged.

## Why this is the smallest of three Layer 2c PRs

The parent spec defers three pipelines to Layer 2c:
`extract_sanctions`, `extract_institutional_competence`, and
`extract_court_provision_links`. Sanctions has the smallest diff
because:

- The classification rule (`enforcedAtLevel`) is a pure function of
  the act node's `@type` field — no parser change, no resolver, no
  registry build.
- The existing sanction extraction regex bank already handles the
  Estonian patterns KOV regulations use (`väärtegu, mille eest on ette
  nähtud rahatrahv kuni X trahviühikut` matches the state-law form).
- No new SHACL **shapes** — `LegalProvisionShape` already exists.
  This PR adds a single property constraint to that shape for
  `enforcedAtLevel`. Layer 2a declared the property in
  `metadata.jsonld` but the SHACL TTL did not yet reference it; this
  PR closes that gap.

## Architecture

### Where `enforcedAtLevel` lands

On the **LegalProvision** node, not on the Sanction node. This honors
the Layer 2a SHACL declaration. Analytics that want sanction-side
filtering can join via `?sanction estleg:applicableProvision/estleg:enforcedAtLevel`.

### Classification rule

Pure act-type rule:

```python
def _classify_enforcement_level(act_node: dict) -> str:
    """Return 'municipality' if the act node is typed
    estleg:MunicipalRegulation, otherwise 'state'.

    Handles @type as either a string or a list of strings.
    """
    types = act_node.get("@type") or []
    if isinstance(types, str):
        types = [types]
    if "estleg:MunicipalRegulation" in types:
        return "municipality"
    return "state"
```

State-law provisions that delegate enforcement to local government
(e.g. KOKS § 66 lg 4 fine ceilings) classify as `"state"` under this
rule because the parent act is `estleg:Law`. This is intentional:
`enforcedAtLevel` answers "what kind of act emits this sanction," not
"who actually enforces in practice." If the latter is needed later, a
separate property (`estleg:enforcementContext`) can be introduced
without colliding with this one.

### Where the rule fires

In `extract_sanctions.py` itself, inside the existing per-provision
loop. The current script doesn't carry an `act_node` reference at
that point; this PR adds a small helper:

```python
def _find_act_node(doc: dict) -> dict | None:
    """Return the act node from a peep file's @graph, or None if no
    act node is present.

    Match criteria: the node must be typed BOTH `estleg:Act` and
    `owl:Ontology`. Some peep files contain auxiliary `owl:Ontology`
    wrappers (e.g. concept-cluster manifests) that aren't acts; the
    `estleg:Act` constraint prevents misclassifying those as
    sanction-bearing acts. The upstream Layer 1 generators stamp
    every real act node with both types, so this is a tight match
    in practice.

    The returned node is the source of truth for @type-based
    classification; the helper centralises the lookup so the per-
    provision loop doesn't repeat the same scan. Callers must
    handle a None return — see the malformed-input contract below.
    """
    for n in doc.get("@graph", []):
        types = n.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:Act" in types and "owl:Ontology" in types:
            return n
    return None
```

**Malformed-input contract.** If `_find_act_node(doc)` returns
`None`, the per-file processing must:

1. Log a warning to the failure-samples list of the coverage
   report.
2. Increment `_files_skipped` and add a `"missing_act_node"` entry
   to `_skip_reasons`.
3. Skip extraction on that file (do not classify the missing act as
   `"state"` by default — that would produce a false-positive
   `enforcedAtLevel` stamp on whatever provisions are still in the
   `@graph`).
4. Continue to the next file.

This is defensive: every legitimately-generated peep carries an
`estleg:Act` + `owl:Ontology` node, but a malformed/partial fixture
shouldn't crash the pipeline OR silently get a misleading
classification.
```

When `sanction_refs` is non-empty for a provision:

```python
node["estleg:hasSanction"] = sanction_refs
node["estleg:enforcedAtLevel"] = _classify_enforcement_level(act_node)
```

The classification value is recomputed on every run — no caching, no
partial preservation. This is what makes the property idempotent
under act-type changes (extremely rare in practice but possible in
principle if a corpus correction reclassifies an act).

**One literal per provision, regardless of sanction count.** A
provision that emits multiple `Sanction_*` records still carries
exactly one `enforcedAtLevel` literal. The integration tests assert
this explicitly.

### Idempotency

Same shape as Layer 2b's preamble pass — but covering BOTH the peep
file mutations AND the sibling sanctions JSON file. Replace the
current global pre-clear pass (which scans every law file once to
strip prior sanctions before the extraction loop) with per-file
in-memory processing inside the per-law iteration:

1. Load the peep file's `doc` into memory.
2. Find the act node via `_find_act_node(doc)`.
3. Compute the path of the corresponding sanctions JSON file (e.g.
   `krr_outputs/sanctions/sanctions_<law_slug>.json`).
4. Detect existing output BEFORE any mutation:
   - **Peep-side `had_existing`:** True if ANY provision in `doc`
     carries `estleg:hasSanction` OR `estleg:enforcedAtLevel`.
   - **Sanctions-file-side `had_sanction_file`:** True if the
     sanctions file exists on disk.
5. Clear peep-side unconditionally: walk every provision and
   `pop("estleg:hasSanction", None)` + `pop("estleg:enforcedAtLevel", None)`.
6. Run sanction extraction over the in-memory `doc`. If any
   provision matches, build `sanction_refs` and set the two triples
   on the provision (one literal per provision per the rule above).
7. Save the peep file when EITHER fresh sanction output exists OR
   `had_existing` was true.
8. Handle the sanctions file:
   - If the act produced fresh sanction output, write/overwrite the
     sanctions JSON normally (existing logic).
   - If the act produced no fresh sanction output AND
     `had_sanction_file` was true, **delete** the stale sanctions
     file. Otherwise stale `Sanction_*` nodes pointing at provisions
     that no longer carry `hasSanction` would outlive the inverse
     pass, leaving dangling `estleg:applicableProvision` triples and
     misleading the downstream sanctions analytics queries.

Without steps 7-8, a provision (or a whole act) whose source text
changes such that the regex no longer matches would keep stale
triples forever and leak orphan `Sanction_*` nodes.

The per-file iteration replaces the existing global pre-clear so
that `had_existing` / `had_sanction_file` can be computed per file
without losing race-safety.

### Coverage instrumentation

Reuses `scripts/kov_pipeline_coverage.py` — same idiom as Layer 2b's
`extract_cross_references` and `generate_inverse_references`. Local
`set[Path]` accumulators at the call site:

```python
_files_processed: set[Path] = set()
_files_processed_kov: set[Path] = set()
_files_with_output: set[Path] = set()
_files_with_output_kov: set[Path] = set()
_triples = 0
_triples_kov = 0
```

`set[Path]` length feeds the `int` fields the helper expects. KOV
attribution is by source act path (the file we're scanning), not the
output target's path.

**`triples_emitted` definition (be explicit):** counts EXTRACTED
SANCTION RECORDS, NOT the underlying RDF triple count. Each
`Sanction_*` node typically projects 4-7 RDF triples (`@type`, label,
`sanctionType`, optional `maxPenalty`/`minPenalty`,
`applicableProvision`); `triples_emitted` reports the record count
because that's the analytically meaningful unit and matches how the
existing `sanctions_report.json` `total_sanction_count` is computed.
A coverage report saying `triples_emitted: 85` means "85 sanction
records were extracted," not "85 RDF triples were written." Document
this in the coverage instrumentation block of `extract_sanctions.py`
so readers don't misinterpret across pipelines (the cross-references
pipeline counts actual triples, since "preamble triples" are the
analytical unit there).

Gate check (matches Layer 2a/2b convention):

```python
if len(_kov_files) >= 11000 and len(_files_with_output_kov) == 0:
    print("\nGATE FAIL: KOV files were processed but none produced output.")
    return 1
if _triples_kov == 0 and len(_kov_files) >= 11000:
    print("\nGATE FAIL: zero KOV triples emitted.")
    return 1
print("\nGATE OK")
return 0
```

Coverage report writes to
`krr_outputs/reports/kov/extract_sanctions_coverage.json`.

## Components

### Modified files

| Path | Change |
| --- | --- |
| `scripts/extract_sanctions.py` | Remove `include_kov=False` pin (line ~510); add `_find_act_node` + `_classify_enforcement_level` helpers; replace global pre-clear with per-file in-memory processing; emit `enforcedAtLevel` alongside `hasSanction` (one literal per provision); extend idempotency to delete stale sanctions JSON files when fresh output is empty; change `main()` to return `int` and use `raise SystemExit(main())`; add coverage instrumentation reusing `kov_pipeline_coverage.CoverageReport`. |
| `tests/test_extract_sanctions.py` | **New file.** 15 tests across 5 classes (unit + integration + idempotency + coverage + corpus-level invariant). |
| `shacl/estonian_legal_shapes.ttl` | Add a property constraint inside the existing `LegalProvisionShape`: `sh:path estleg:enforcedAtLevel ; sh:datatype xsd:string ; sh:maxCount 1 ; sh:in ("state" "municipality") ; sh:name "enforcedAtLevel" ; sh:description "..."` . Closes the gap between `metadata.jsonld` (declares the property) and the SHACL TTL (didn't reference it). |
| `docs/SCHEMA_REFERENCE.md` | Mark `estleg:enforcedAtLevel` as populated; add a short example block in the existing Sanctions section AND in the Layer 2 schema table; document the `sh:in ("state" "municipality")` constraint addition. |
| `CHANGELOG.md` | Layer 2c sub-PR-1 entry — sanctions unpinned, SHACL property constraint added, coverage numbers, KOV impact. |

### NOT in this PR

- `extract_institutional_competence` — Layer 2c PR #2.
- `extract_court_provision_links` — Layer 2c PR #3.
- New sanction regex patterns — the existing extraction logic just
  runs over more files. If KOV-specific phrasings surface as
  systematic gaps, that's a follow-up.
- New SHACL shapes — `enforcedAtLevel` was declared in Layer 2a.

## Test plan

`tests/test_extract_sanctions.py` is created as a new file with 5
test classes, 15 tests total: 4 unit + 4 integration + 4 idempotency
+ 2 coverage + 1 corpus-invariant.

### `TestClassifyEnforcementLevel` (unit, 4 tests)

- `test_municipal_regulation_returns_municipality` — `@type` list with
  `estleg:MunicipalRegulation` → `"municipality"`.
- `test_law_returns_state` — `@type` list with `estleg:Law` →
  `"state"`.
- `test_government_regulation_returns_state` — `estleg:GovernmentRegulation`
  → `"state"`.
- `test_string_type_normalised` — `@type` as bare `str` (not list) is
  handled correctly.

### `TestSanctionExtractionWithEnforcementStamp` (integration, 4 tests)

- `test_kov_act_sanctions_get_municipality_stamp` — stage a tmp KOV
  peep with a parking-fine sanction; run the extraction; assert
  provision gains BOTH `estleg:hasSanction` AND
  `estleg:enforcedAtLevel = "municipality"`.
- `test_state_law_sanctions_get_state_stamp` — same shape with a
  `Law`-typed act; assert `"state"` stamp.
- `test_provision_with_no_sanction_gets_no_enforcement_stamp` —
  provision whose summary text contains no matching sanction pattern
  does NOT gain `enforcedAtLevel` (preserves the "stamps only on
  sanction-bearing provisions" rule).
- `test_provision_with_multiple_sanctions_gets_one_enforcement_literal` —
  stage a provision whose summary matches BOTH an imprisonment and a
  fine pattern (e.g. KarS-style "kuni kolm aastat vangistust või
  rahatrahv kuni 300 trahviühikut"); assert `estleg:hasSanction` is a
  list of two refs, AND `estleg:enforcedAtLevel` is a single bare
  string literal (NOT a list). Locks in the "one literal per
  provision regardless of sanction count" rule.

### `TestSanctionsIdempotency` (integration, 4 tests)

- `test_stale_enforcement_cleared_when_sanctions_removed` — provision
  starts with prior `hasSanction` + `enforcedAtLevel`; the act's
  summary text changes so no sanction matches now; rerun strips BOTH
  properties and saves the file.
- `test_stale_enforcement_cleared_when_sanction_text_unchanged_but_act_type_flipped` —
  edge case: classification is stateless. If a peep's `@type`
  changes between runs, the recomputed `enforcedAtLevel` reflects
  the new type, not the prior emission.
- `test_no_op_when_no_existing_no_fresh` — provision with no
  sanctions, no prior stamps; rerun doesn't touch the file.
- `test_stale_sanctions_json_deleted_when_act_loses_all_sanctions` —
  stage a peep with a prior sanctions JSON file at
  `krr_outputs/sanctions/sanctions_<law>.json` containing a
  `Sanction_*` node; the act's summary text changes so no sanction
  matches now; rerun deletes the sanctions JSON file from disk so
  the orphan `Sanction_*` node and its `applicableProvision` triple
  don't survive.

### `TestSanctionsCoverageReport` (integration, 2 tests)

- `test_coverage_report_written_with_kov_split` — invoke `main()`
  over a tmp dir with 1 law + 1 KOV act, both with sanction text;
  read the JSON; assert `files_with_output_kov >= 1`,
  `triples_emitted_kov >= 1`.
- `test_gate_fails_when_kov_input_nontrivial_but_no_output` — invoke
  `main()` with mocked `iter_peep_files` returning ≥11,000 KOV paths
  but the regex never matches; assert `main()` returns 1 (the
  `raise SystemExit(main())` line then propagates the exit code).

### `TestCorpusInvariant` (integration, 1 test)

- `test_every_has_sanction_provision_has_exactly_one_enforced_at_level` —
  scan ALL files in `krr_outputs/` (recursive, including
  `regulations/kov/**/*_peep.json`). For every node carrying
  `estleg:hasSanction`, assert `estleg:enforcedAtLevel` is a single
  bare string (not a list, not absent), AND the value is one of
  `"state"` or `"municipality"`. This is a corpus-level invariant
  check: a regression where one sanction-bearing provision lost its
  stamp would surface here even if the unit tests pass. Marked
  `@pytest.mark.slow` so CI can skip it on every commit and run only
  in the integration suite. Skipped if `krr_outputs/` is empty
  (clean checkout).

## Implementation order

Ten commits, TDD shape (matches Layer 2b convention):

1. Add `_find_act_node` + `_classify_enforcement_level` helpers +
   unit tests (`TestClassifyEnforcementLevel`, 4 tests).
2. Wire `enforcedAtLevel` emission into the existing sanction
   extraction pass + integration tests
   (`TestSanctionExtractionWithEnforcementStamp`, 4 tests). Includes
   the multiple-sanctions-one-literal regression test.
3. Refactor to per-file in-memory processing + clear stale
   `enforcedAtLevel` + save-even-when-empty pattern. Adds
   `TestSanctionsIdempotency` provision-level tests (3 of 4).
4. Extend idempotency to delete stale sanctions JSON files when
   fresh output is empty. Adds `TestSanctionsIdempotency`
   sanctions-file test (4th of 4).
5. Add SHACL property constraint to `LegalProvisionShape` for
   `enforcedAtLevel` (`xsd:string`, `sh:maxCount 1`,
   `sh:in ("state" "municipality")`). Run targeted SHACL to
   confirm zero new violations against the constraint.
6. Add coverage instrumentation reusing `kov_pipeline_coverage`;
   change `main()` to return `int`; add
   `raise SystemExit(main())`. Coverage tests
   (`TestSanctionsCoverageReport`, 2 tests).
7. Unpin: remove `include_kov=False` from the `iter_peep_files` call
   (one-line change). Re-run the test suite.
8. Run full corpus + commit output (~82 KOV files modified plus
   any state-side regenerations; `extract_sanctions_coverage.json`
   first run). Add the corpus-invariant test
   (`TestCorpusInvariant`, 1 test) to the test suite — it runs after
   the corpus output is in place, validating the global property.
9. Documentation commit (SCHEMA_REFERENCE.md, CHANGELOG.md).
10. Final verification + PR body — `pytest -q`, `validate_all`,
    targeted SHACL on the new constraint, gate-check one-liner,
    generate PR body, stop short of pushing.

## Risks and mitigations

**Risk 1: KOV sanctions use different phrasing than state-law sanctions
and the existing regex misses systematic patterns.**

Mitigation: dry-count established that the existing regex catches ~85
KOV sanction records — non-zero, justifying the unpinning. If
post-merge analysis shows systematic under-extraction, that's a
follow-up regex-bank PR, not a blocker for this one.

**Risk 2: Idempotency bug — provisions losing their stamps mid-run.**

Mitigation: `TestSanctionsIdempotency::test_stale_enforcement_cleared_when_sanctions_removed`
locks this in. The implementation pattern mirrors Layer 2b's preamble
pass (`_process_preamble_for_act` helper) which had the exact same
class of bug fixed in PR #99 review round 2.

**Risk 3: SHACL violations from the new `enforcedAtLevel` triples.**

Mitigation: Layer 2a's `metadata.jsonld` declared
`enforcedAtLevel` but the SHACL TTL never referenced it — meaning
the previous spec's "zero new violations" claim was vacuous. This
PR closes that gap by ADDING the property constraint to
`LegalProvisionShape` in commit 5. The classification function
returns exactly `"state"` or `"municipality"`, both of which are
in the new `sh:in` value set, so the constraint validates clean by
construction. The test suite locks in the contract; the corpus run
in commit 8 validates against real data.

**Risk 4: Re-running on the corpus replaces 82 KOV files plus
sanctions/ regeneration — large diff in the PR.**

Mitigation: same as Layer 2b — stage in chunks of 500 via the
INDEX-driven helper. Reviewer focuses on code/tests/docs; corpus
output is a mechanical artifact.

## Out-of-scope reminders

This PR does **not**:

- Modify `extract_institutional_competence` or `extract_court_provision_links` (subsequent Layer 2c PRs).
- Add new sanction patterns or extraction logic.
- Add new SHACL **shapes** — only adds a property constraint to
  the existing `LegalProvisionShape`.
- Touch the `iter_peep_files` default flip (already done in Layer 2a).
- Refactor the sanction file output format (`krr_outputs/sanctions/sanctions_*.json`
  remains unchanged in shape; only the deletion-on-empty behaviour is new).

## Gate B umbrella status (after this PR lands)

- 2a: COMPLETE (#98)
- 2b: COMPLETE (#99)
- 2c PR #1 sanctions: this PR
- 2c PR #2 institutional-competence: TBD
- 2c PR #3 court-provision-links: TBD

Gate B closes when all three Layer 2c sub-PRs land.
