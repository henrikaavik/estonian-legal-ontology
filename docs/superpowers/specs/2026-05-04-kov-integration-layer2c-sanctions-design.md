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
6. Re-runs are idempotent: a provision that previously had
   `hasSanction` + `enforcedAtLevel` and no longer matches the
   sanction regex loses BOTH triples on the next run.
7. Targeted SHACL: zero new violations on `enforcedAtLevel`.

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
- No new SHACL shapes — `enforcedAtLevel` was declared in Layer 2a
  with `Domain: estleg:LegalProvision, Range: xsd:string`.

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
loop. When `sanction_refs` is non-empty for a provision (i.e., at
least one sanction was extracted from its summary text), set both:

```python
node["estleg:hasSanction"] = sanction_refs
node["estleg:enforcedAtLevel"] = _classify_enforcement_level(act_node)
```

The classification value is recomputed on every run — no caching, no
partial preservation. This is what makes the property idempotent
under act-type changes (extremely rare in practice but possible in
principle if a corpus correction reclassifies an act).

### Idempotency

Same shape as Layer 2b's preamble pass:

1. Detect existing output BEFORE clearing — track `had_existing` flag
   (any of: `estleg:hasSanction`, `estleg:enforcedAtLevel` on the
   provision; existing `Sanction_*` nodes in the law's sanction file).
2. Clear unconditionally: `pop("estleg:hasSanction", None)`,
   `pop("estleg:enforcedAtLevel", None)`.
3. Run extraction.
4. Save when EITHER fresh sanction output exists OR `had_existing`
   was true (so a provision that lost its sanctions persists the
   cleared state to disk).

Without step 4, a provision whose source text changes such that the
regex no longer matches would keep stale `hasSanction` /
`enforcedAtLevel` triples forever.

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
| `scripts/extract_sanctions.py` | Remove `include_kov=False` pin (line ~510); add `_classify_enforcement_level` helper; emit `enforcedAtLevel` alongside `hasSanction`; extend idempotency clear to include `enforcedAtLevel`; add coverage instrumentation. |
| `tests/test_extract_sanctions.py` | **New file.** ~12 tests across 4 classes (unit + integration + idempotency + coverage). |
| `docs/SCHEMA_REFERENCE.md` | Mark `estleg:enforcedAtLevel` as populated; add a short example block in the existing Sanctions section AND in the Layer 2 schema table. |
| `CHANGELOG.md` | Layer 2c sub-PR-1 entry — sanctions unpinned, coverage numbers, KOV impact. |

### NOT in this PR

- `extract_institutional_competence` — Layer 2c PR #2.
- `extract_court_provision_links` — Layer 2c PR #3.
- New sanction regex patterns — the existing extraction logic just
  runs over more files. If KOV-specific phrasings surface as
  systematic gaps, that's a follow-up.
- New SHACL shapes — `enforcedAtLevel` was declared in Layer 2a.

## Test plan

`tests/test_extract_sanctions.py` is created as a new file with 4
test classes, ~12 tests total.

### `TestClassifyEnforcementLevel` (unit, 4 tests)

- `test_municipal_regulation_returns_municipality` — `@type` list with
  `estleg:MunicipalRegulation` → `"municipality"`.
- `test_law_returns_state` — `@type` list with `estleg:Law` →
  `"state"`.
- `test_government_regulation_returns_state` — `estleg:GovernmentRegulation`
  → `"state"`.
- `test_string_type_normalised` — `@type` as bare `str` (not list) is
  handled correctly.

### `TestSanctionExtractionWithEnforcementStamp` (integration, 3 tests)

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

### `TestSanctionsIdempotency` (integration, 3 tests)

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

### `TestSanctionsCoverageReport` (integration, 2 tests)

- `test_coverage_report_written_with_kov_split` — invoke `main()`
  over a tmp dir with 1 law + 1 KOV act, both with sanction text;
  read the JSON; assert `files_with_output_kov >= 1`,
  `triples_emitted_kov >= 1`.
- `test_gate_fails_when_kov_input_nontrivial_but_no_output` — invoke
  `main()` with mocked `iter_peep_files` returning ≥11,000 KOV paths
  but the regex never matches; assert exit code is non-zero.

## Implementation order

Eight commits, TDD shape (matches Layer 2b convention):

1. Add `_classify_enforcement_level` helper + unit tests
   (`TestClassifyEnforcementLevel`, 4 tests).
2. Wire `enforcedAtLevel` emission into the existing sanction
   extraction pass + integration tests
   (`TestSanctionExtractionWithEnforcementStamp`, 3 tests).
3. Add idempotency: clear stale `enforcedAtLevel`, save-even-when-empty
   pattern + idempotency tests (`TestSanctionsIdempotency`, 3 tests).
4. Add coverage instrumentation reusing `kov_pipeline_coverage` +
   coverage tests (`TestSanctionsCoverageReport`, 2 tests).
5. Unpin: remove `include_kov=False` from `iter_peep_files` call
   (one-line change). Re-run the test suite.
6. Run full corpus + commit output (~82 KOV files modified,
   `extract_sanctions_coverage.json` first run).
7. Documentation commit (SCHEMA_REFERENCE.md, CHANGELOG.md).
8. Final verification + PR body — `pytest -q`, `validate_all`,
   gate-check one-liner, generate PR body, stop short of pushing.

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

Mitigation: Layer 2a's `LegalProvisionShape` constrains
`enforcedAtLevel` only by `xsd:string` datatype. The classification
function returns `"state"` or `"municipality"` (string literals), so
this is zero-risk.

**Risk 4: Re-running on the corpus replaces 82 KOV files plus
sanctions/ regeneration — large diff in the PR.**

Mitigation: same as Layer 2b — stage in chunks of 500 via the
INDEX-driven helper. Reviewer focuses on code/tests/docs; corpus
output is a mechanical artifact.

## Out-of-scope reminders

This PR does **not**:

- Modify `extract_institutional_competence` or `extract_court_provision_links` (subsequent Layer 2c PRs).
- Add new sanction patterns or extraction logic.
- Add new SHACL shapes.
- Touch the `iter_peep_files` default flip (already done in Layer 2a).
- Refactor the sanction file output format (`krr_outputs/sanctions/*.json`
  remains unchanged).

## Gate B umbrella status (after this PR lands)

- 2a: COMPLETE (#98)
- 2b: COMPLETE (#99)
- 2c PR #1 sanctions: this PR
- 2c PR #2 institutional-competence: TBD
- 2c PR #3 court-provision-links: TBD

Gate B closes when all three Layer 2c sub-PRs land.
