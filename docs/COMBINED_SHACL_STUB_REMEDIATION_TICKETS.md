# Combined SHACL Stub Remediation Tickets

## Ticket 1: Materialize Required Semantic Edges for Shaped Combined Stubs

### Problem

`krr_outputs/combined_ontology.jsonld` currently emits graph-closure stubs for
cross-corpus references. Some of those stubs keep SHACL-targeted `@type`s such as
`estleg:NationalRegulation`, `estleg:MunicipalRegulation`,
`estleg:KovProvision`, and `estleg:ProposedAmendment`, but omit required
semantic properties.

Observed combined-only SHACL failures include:

- `13,289` missing `estleg:documentType`
- `13,289` missing `estleg:terviktekstId`
- `14,211` missing `estleg:enactedBy`
- `14,211` missing `estleg:enactedByMunicipality`
- `9,831` missing `estleg:titleNormalized`
- `4,380` missing `estleg:partOfAct`
- `786` missing `estleg:amendingDraft`

The source subcorpora already contain these values. The bug is in the aggregate
artifact: the combined builder creates typed leaf stubs instead of carrying the
minimum graph edges needed by those typed resources.

### Required Fix

Update `scripts/fix_all_issues.py` so graph-closure output never publishes an
incomplete SHACL-targeted node.

For referenced nodes that are typed with shaped classes, the combined builder
must either:

1. include the full source node from the sibling corpus, or
2. emit a semantically complete closure node containing every required SHACL
   property for its type, including IRI-valued graph edges.

This must cover at least:

- `estleg:NationalRegulation` and `estleg:MunicipalRegulation`:
  `rdfs:label`, `estleg:documentType`, `estleg:terviktekstId`
- `estleg:MunicipalRegulation`:
  `estleg:enactedBy`, `estleg:enactedByMunicipality`,
  `estleg:titleNormalized`
- `estleg:KovProvision`:
  `estleg:enactedBy`, `estleg:enactedByMunicipality`, `estleg:partOfAct`
- `estleg:ProposedAmendment`:
  `rdfs:label`, `estleg:amendingDraft`

IRI targets introduced by those required properties must also resolve in the
combined graph with enough typing to satisfy `sh:class` constraints. Do not
strip these edges from shaped nodes merely to preserve leaf-stub behavior.

### Out of Scope

- Do not relax SHACL shapes.
- Do not downgrade these constraints to warnings.
- Do not add validator-side suppressions for `estleg:isStubNode`.
- Do not remove useful `@type`s from stubs as a way to avoid validation.

### Definition of Done

- `combined_ontology.jsonld` no longer contains `estleg:isStubNode` nodes with
  shaped types that are missing required fields.
- The cited examples are complete in the combined artifact:
  - `estleg:Reg_1001517_Map_2026`
  - `estleg:Reg_1001519_Par_16`
  - `estleg:Reg_1001524_Map_2026`
  - `estleg:Reg_1000010_Map_2026`
  - `estleg:AmendmentLink_Draft_HTM13_1561_VKT`
- Combined graph closure still passes: every internal `estleg:` object target
  introduced by the fix resolves inside `combined_ontology.jsonld`.
- Add focused regression tests in `tests/test_fix_all_issues.py` for KOV act,
  KOV provision, state regulation, and proposed-amendment closure nodes.

## Ticket 2: Add a Combined-Only SHACL Regression Gate

### Problem

The current public-load validation can pass because it loads
`combined_ontology.jsonld` plus the sibling subcorpora, allowing RDF graph merge
semantics to fill fields from the source files. That is correct for the
Seadusloome load surface, but it does not catch a stale or semantically thin
standalone combined artifact.

The aggregate file is a public artifact and should not contain typed nodes that
fail the project's own SHACL contract.

### Required Fix

Add a narrow validator or test path that validates `combined_ontology.jsonld`
alone against the SHACL shapes and fails on violations for shaped stub nodes.

The gate should report grouped findings by:

- focus node type
- missing path
- whether the focus node has `estleg:isStubNode`
- first few example IDs

### Out of Scope

- Do not replace the existing Seadusloome-compatible validation path.
- Do not hide combined-only findings because the full public load graph can
  repair them by merging sibling files.

### Definition of Done

- A regression test fails on the current incomplete combined stubs.
- The same test passes after Ticket 1's builder fix.
- CI can distinguish:
  - source-subcorpus missing data,
  - public-load graph missing data,
  - combined-only aggregate drift.
- The failure message points to rebuilding/fixing `combined_ontology.jsonld`,
  not to relaxing SHACL.

## Ticket 3: Regenerate and Release a Clean Combined Artifact

### Problem

The source files already contain the missing fields, but
`krr_outputs/combined_ontology.jsonld` was generated with incomplete closure
stubs. Consumers that inspect or load the combined artifact alone see false
semantic gaps.

### Required Fix

After the combined builder is fixed, regenerate the aggregate artifact through
the canonical builder:

```bash
python3 scripts/fix_all_issues.py
```

Then run the relevant release gates.

### Definition of Done

- `krr_outputs/combined_ontology.jsonld` is regenerated, not hand-edited.
- The following checks pass:

```bash
python3 -m pytest -q tests/test_fix_all_issues.py tests/test_validate_seadusloome_sync.py
python3 scripts/validate_all.py
python3 scripts/validate_seadusloome_sync.py --report seadusloome-shacl-report.ttl
```

- A direct combined-only count for these missing paths returns zero:
  - `estleg:documentType`
  - `estleg:terviktekstId`
  - `estleg:enactedBy`
  - `estleg:enactedByMunicipality`
  - `estleg:titleNormalized`
  - `estleg:partOfAct`
  - `estleg:amendingDraft`
- `seadusloome-shacl-report.ttl` is updated only if the validator actually
  produced a fresh conforming report.

## Ticket 4: Clarify the Consumer Load Contract and Diagnostics

### Problem

Different checks can produce different results depending on whether they load:

1. `combined_ontology.jsonld` alone,
2. the source subcorpora alone,
3. the Seadusloome public load surface: combined plus public subdirectories.

The current warning summary can be misread as source data loss even when the
source files contain the fields. Consumers and diagnostics need to name the load
surface explicitly.

### Required Fix

Document and enforce the intended load contract for applications:

- If an app loads only `combined_ontology.jsonld`, that file must be
  semantically complete for the shaped nodes it contains.
- If an app follows the Seadusloome load surface, it must load the combined file
  plus public subdirectories and merge nodes by `@id` using RDF graph semantics.
- Diagnostic tools must state which load surface was validated.

### Definition of Done

- `docs/SCHEMA_REFERENCE.md` or `docs/VALIDATION_REPORT.md` documents the three
  load surfaces and when each gate should be used.
- `scripts/validate_seadusloome_sync.py` output clearly says it validates
  combined plus public subdirectories.
- Any combined-only diagnostic labels findings as aggregate-artifact findings.
- No diagnostic suggests suppressing SHACL for `partOfAct`, KOV
  issuer/municipality, regulation metadata, or `amendingDraft`.
