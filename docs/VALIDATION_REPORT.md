# Validation Report

**Last updated:** 2026-05-24
**Primary validator:** `scripts/validate_all.py`

## Summary

| Metric | Count |
|--------|-------|
| Files validated | 21,932 |
| Errors | 0 |
| Warnings | 1 |
| Result | **PASSED** |

The remaining warning is the expected absence of
`krr_outputs/generation_manifest_laws.json` in this checkout; it does not
block the validation gate.

## Checks Performed

1. JSON syntax validity
2. `@context` namespace consistency (`estleg:` -> `https://data.riik.ee/ontology/estleg#`)
3. `@type` is always an array
4. Multi-valued properties are arrays
5. `sectionNumber` is always a string
6. `dc:source` is not an array
7. `xsd:date` value objects use strict `YYYY-MM-DD` literals
8. `estleg:affectedLawName` uses the canonical array-of-strings shape
9. `@id` uniqueness within and across files, excluding known shared class IDs
10. `dcterms:source` uses a resolvable IRI object when present
11. Internal `estleg:` object references resolve to corpus nodes
12. `krr_outputs/INDEX.json` registry drift: indexed files exist, counts match, act/provision shape is valid unless explicitly excepted
13. State and KOV regulation indexes match their output trees
14. `metadata.jsonld` advertised counts match repository counts
15. Institution labels have no duplicate normalized canonical form

## Data Coverage

| Category | Files | Indexed records |
|----------|-------|-----------------|
| Enacted law peep files | 637 | 608 law index entries / 637 indexed files |
| State regulations | 3,812 | 3,812 in current regulation index |
| KOV regulations | 11,059 | 11,059 |
| Draft legislation | 3 phase files | 22,832 drafts |
| Supreme Court decisions | 34 | 12,137 decisions |
| EU legislation | 3 | 33,242 acts |
| EU court decisions | 5 | 22,290 decisions |
| Institutions | 126 | institution files |
| Controlled vocabulary | 1 | 134 vocabulary and fallback nodes |

## SHACL Bucket Checks

| Bucket | Files | Result |
|--------|-------|--------|
| `riigikohus` | 35 | PASS |
| `drafts` | 4 | PASS |
| `eurlex` | 4 | PASS |
| `curia` | 6 | PASS |
| `laws` | 4,450 | PASS |
| `kov` | 11,062 | PASS |

Every SHACL bucket includes the controlled vocabulary/fallback-node graph so cross-file references and shared classification nodes are validated consistently.

## Similarity Coverage

`scripts/generate_similarity_index.py` now includes state regulations while still deferring KOV similarity to Layer 3.

| Type | Files | Provisions analyzed |
|------|-------|---------------------|
| Laws | 633 | 23,036 |
| State regulations | 3,812 | 48,385 |
| KOV regulations | 0 | 0 |

Similarity output now contains 71,421 analyzed provisions, 101,506 pairs, and updates 3,475 JSON-LD files.

## Seadusloome Zero-Warning Gate

`scripts/validate_seadusloome_sync.py` mirrors the Seadusloome `main` sync load path and enforces a zero-warning policy on the published ontology.

- **Load set:** `krr_outputs/combined_ontology.jsonld` plus the public
  load-surface directories defined in `scripts/estleg_common.py`:
  `eelnoud/`, `riigikohus/`, `curia/`, `eurlex/`, `concepts/`,
  `sanctions/`, `amendments/`, `institutions/`, `provision_versions/`,
  `annotations/`, `harmonisation/`, and `regulations/`. This is the same
  set that Seadusloome ingests when it clones the ontology repository on
  `main`, and it is the graph over which sidecar object references must
  resolve.
- **Validator:** pyshacl with `inference="none"` against
  `shacl/estonian_legal_shapes.ttl`. The Seadusloome consumer does not
  apply RDFS inference, so the gate intentionally diverges from the
  bucket validator (`scripts/shacl_validate_all.py`), which uses
  `inference="rdfs"` to derive class memberships before checking shape
  targets.
- **Expectation:** zero SHACL warnings and zero SHACL violations.
  `--max-warnings` defaults to `0`. Exit codes: `0` clean, `1` warnings
  or violations exceed the threshold, `2` parse failures.
- **Relation to bucket SHACL:** the bucket validator catches semantic
  defects per class with inferred types; the Seadusloome gate catches
  defects that are visible to a downstream consumer that does not apply
  inference, including stale aggregate drift in
  `combined_ontology.jsonld`. Both gates must pass on release.
- **CI wiring:** the `Seadusloome zero-warning gate` job in
  `.github/workflows/validate.yml` runs on every pull request and `main`
  push. The job uploads the rdflib turtle SHACL report as
  `seadusloome-shacl-report` on failure for offline triage. The 2026-05-24
  local benchmark loaded 21,290 JSON-LD inputs / 4,845,510 data triples and
  completed in 5:17 wall time; if corpus growth makes the gate too slow for
  per-PR execution, split the gate by bucket or demote it to release-only via
  `.github/workflows/validate.yml`.

### Draft Reference Cleanup Notes

The #217 graph-closure pass removed a small set of `estleg:amendsLaw`
targets from `krr_outputs/eelnoud/eelnoud_combined.jsonld` because the
referenced act nodes are not emitted anywhere in the public load surface.
The table below is limited to dead-reference removals; successful target
renames and array de-duplication are listed separately. These dead references
were not treated as successful resolutions:

| Removed target | Root cause |
|----------------|------------|
| `estleg:Vlaigusseadus_Osa10_1005_1067` | Draft law-name resolver lost the leading `Võ` during slug generation; should resolve to the VõS corpus node family. |
| `estleg:hinenud_Rahvaste_Organisatsio_Map_2026` | Draft law-name resolver lost the leading `Ü` and truncated the treaty title; no such map node is emitted. |
| `estleg:Eesti_Vabariigi_valitsuse_ja_A_Map_2026` | Treaty `_Map_2026` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |
| `estleg:Eesti_Vabariigi_valitsuse_ja_L_Map_2026` | Treaty `_Map_2026` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |
| `estleg:Eesti_Vabariigi_ja_Euroopa_Inv_Map_2026` | Treaty `_Map_2026` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |
| `estleg:Isikuandmete_automatiseeritud__Map_2026` | Treaty `_Map_2026` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |
| `estleg:Maailma_Terviseorganisatsiooni_Map_2026` | Treaty `_Map_2026` target was inferred from a title that no current generator materialises as an act-map node. |
| `estleg:Merinuete_korral_vastutuse_pi_Map_2026` | Treaty `_Map_2026` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |

Successful target rewrites in the same diff:

| Removed target | Replacement target | Notes |
|----------------|--------------------|-------|
| `estleg:TKS_Map_2026` | `estleg:TarbKS_Map_2026` | Six references were rewritten to the emitted TarbKS map node. One duplicate-array entry on the draft "Ülikooliseaduse ja Tartu Ülikooli seaduse muutmise seadus" was removed without replacement because the array contained `TKS_Map_2026` twice. |
| `estleg:TS_Map_2026` | `estleg:TõS_Map_2026` | Clean 1-to-1 rename to the emitted target. |

Before the draft-impact enrichment is rerun against live data, fix the
resolver path used by `scripts/extract_draft_impact.py` and
`scripts/generate_draft_legislation.py` so leading Estonian diacritics
and treaty titles resolve to existing registry abbreviations instead of
re-emitting these dead references.

## Known Remaining Issues

No validation-blocking issues are known in the remediated scope. The draft
resolver cleanup documented above remains a required follow-up before the
next live draft-impact ingestion run.
