# ONTOLOGY_ANALYSIS.md

Updated: 2026-02-28 08:30 (EET)
Owner: Peep

## 1) Current state snapshot

Prepared ontology/data outputs in `krr_outputs/`:
- `tsiviilseadustik_osa5_peep.json`
- `tsiviilseadustik_osa6_peep.json`
- `tsus_osa7_138_169_owl.jsonld` (+ checkpoints)
- `volaigusseadus_osa1_peep.json`

Interpretation:
- TsÜS parts 5–7 have mapping artifacts ready (JSON/JSON-LD).
- VÕS part 1 initial large mapping artifact is generated.

## 2) Structural quality check (quick)

What looks good:
- Output files exist and are timestamped through latest work cycle.
- JSON-LD artifact for TsÜS part 7 is present in finalized + checkpoint form.
- File naming is consistent enough for pipeline continuation.

What still needs hard validation:
- JSON Schema pass/fail report for each file.
- SHACL/ontology constraint conformance report.
- Cross-reference integrity (from/to links, unresolved pointers).
- Duplicate concept detection and class/property normalization.

## 3) Main risks

1. **Schema drift risk**
   - Early parts may not fully match latest ontology contract without re-validation.
2. **Reference integrity risk**
   - Legal references may parse but remain unresolved semantically.
3. **Enum vocabulary instability**
   - `documentType/referenceType/stepType/actorRole` still need freezing.
4. **Large-file quality risk (VÕS Osa 1)**
   - Big output can hide silent structural inconsistencies without automated checks.

## 4) Immediate next actions

Priority P0:
1. Run deterministic validation pass on all current outputs.
2. Produce one compact validation report (errors/warnings by file).
3. Freeze enum vocabulary set v0.1 and re-check mapped values.
4. Mark "ready-for-review" subset for Marta/Tambet semantic review.

Priority P1:
1. Add SHACL constraints for top critical classes/properties.
2. Generate unresolved-reference list for legal expert review.

## 5) Ready-to-receive status for Marta input

Status: **READY**

Prepared to ingest Marta structured civil-code analysis and map it against existing class/property skeleton, then run validation + reconciliation pass.
