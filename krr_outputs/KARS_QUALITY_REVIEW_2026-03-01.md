# KarS Quality Review (Peep + Marta scope)

Date: 2026-03-01
Files reviewed:
- `scripts/generate_kars_eriosa_jsonld.py`
- `krr_outputs/karistusseadustik_eriosa_owl.jsonld`
- `data/riigiteataja/karistusseadustik.xml`

## Checks requested

1. **All 430 sections mapped correctly** ✅
   - XML Eriosa sections: **430**
   - JSON-LD `Section` individuals: **430**
   - Missing section numbers: none
   - Extra section numbers: none

2. **Legal concepts accurate (Süütegu, Kuritegu, Väärtegu)** ✅ (fixed)
   - Corrected concept definitions and references in generator:
     - Süütegu → `KarS § 3 lg 1`
     - Kuritegu → `KarS § 3 lg 3` (added juriidilise isiku *sundlõpetamine*)
     - Väärtegu → `KarS § 3 lg 4` (removed incorrect "sõiduki juhtimise õiguse äravõtmine")
     - Tahtlus → `KarS § 16`
     - Ettevaatamatus → `KarS § 18 lg 1`

3. **Cross-references to TsÜS / VÕS / AÕS** ⚠️
   - No explicit occurrences in generated KarS Eriosa `legalText` previews.
   - Current model does not encode dedicated cross-law references for these acts.
   - Not an internal inconsistency, but if cross-law linking is a hard requirement, a dedicated extractor/property should be added.

4. **JSON-LD structure consistent** ✅
   - Link graph integrity check passed (`hasChapter` / `hasDivision` / `hasSubdivision` / `hasSection` all resolve)
   - Broken links: 0
   - Counts are structurally consistent with summary (18 chapters, 54 divisions, 10 subdivisions, 430 sections).

5. **No missing critical offences** ✅
   - Full Eriosa section coverage confirmed by count parity and zero missing section numbers.

## Fixes applied

- Updated `LEGAL_DEFINITIONS` in `scripts/generate_kars_eriosa_jsonld.py`
- Regenerated `krr_outputs/karistusseadustik_eriosa_owl.jsonld`

## Conclusion

KarS Eriosa mapping is **high quality** after concept-definition corrections.
Main remaining enhancement (optional): explicit cross-law reference extraction/linking for TsÜS/VÕS/AÕS.
