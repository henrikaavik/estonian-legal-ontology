# KRR Ontology - Legal Accuracy Review Report
**Date:** 2026-02-27  
**Reviewer:** Tambet  
**Status:** ✅ COMPLETED

---

## Executive Summary

All ontology files have been reviewed for legal accuracy against the Tsiviilseadustiku üldosa seadus (TsÜS). The mapping accurately reflects Estonian civil law structure and content.

| File | Legal Accuracy | Completeness | Notes |
|------|---------------|--------------|-------|
| civillaw_ontology_skeleton.ttl | ✅ PASS | Schema | Valid class structure |
| civillaw_article_instance_template.ttl | ✅ PASS | Template | Instance pattern correct |
| tsiviilseadustik_initial_graph.ttl | ✅ PASS | Articles 1-5 | Content matches source |
| ontology-template-v0.1.ttl | ✅ PASS | SMEE aligned | Interface contract OK |
| tsiviilseadustik_enhanced_1-7.json | ✅ PASS | Enhanced norms | Classification accurate |
| tsiviilseadustik_osa5_peep.json | ✅ PASS | Osa 5 structure | Tähtajad concepts OK |
| Marta's TsÜS draft v1 | ✅ PASS | Osa 1-7 detailed | Comprehensive mapping |

---

## Detailed Legal Review

### 1. Articles 1-5 (Initial Graph) ✅

**Article 1 - Seaduse ülesanne**
- Source text: "Käesolevas seaduses sätestatakse tsiviilõiguse üldpõhimõtted."
- Legal analysis: Correctly identified as declarative/preambular norm
- Subject: seadusandja (legislator)
- Status: ✅ Accurate

**Article 2 - Tsiviilõiguse allikad**
- §2(1): Correctly identifies "seadus ja tava" as sources
- §2(2): Correctly captures tava formation conditions and hierarchy (tava ≠ seadus)
- Status: ✅ Accurate

**Article 3 - Seaduse tõlgendamine**
- Captures methodology: sõnastus + mõte + eesmärk
- Correctly identified as imperative methodological norm
- Status: ✅ Accurate

**Article 4 - Analoogia**
- Correctly structured as conditional permissive norm
- Captures subsidiary nature (only when no direct provision)
- Status: ✅ Accurate

**Article 5 - Tsiviilõiguste tekkimise alused**
- Correctly lists: tehingud, sündmused, toimingud, õigusvastased teod
- Exhaustive enumeration accurately captured
- Status: ✅ Accurate

---

### 2. Osa 5 (Tähtajad ja Tähtpäev) - Peep's JSON ✅

**Concepts defined:**
- `TimeLimit` (Tähtaeg) - correctly defined as ajavahemik
- `DueDate` (Tähtpäev) - correctly as saabumiskuupäev
- `Limitation` (Aegumine) - correctly captured
- `LimitationTerm` (Aegumistähtaeg) - accurate
- `InterruptionOfLimitation` (Aegumise katkemine) - correct triggers
- `SuspensionOfLimitation` (Aegumise peatumine) - correct triggers

**Legal accuracy notes:**
- §134-137 (tähtaja arvutus) concepts present
- §142-169 (aegumine) structure mapped
- Katkemise vs peatamise eristus korrektne
- Status: ✅ Legally accurate

---

### 3. Osa 4 (Tehingud) - Marta's Draft ✅

**Comprehensive mapping verified:**

**Tahteavaldus (§67-76)**
- Express vs implied declaration - correct distinction
- Receipt rule (§69) - accurately captured
- Silence as declaration - exceptions correctly noted

**Tehingu vorm (§77-83)**
- Written form, electronic form, notarial - all present
- Consequence: tühisus - correctly identified

**Tühine vs Tühistatav (§84-110)**
- Void ab initio (tühine) vs voidable (tühistatav) - distinction clear
- Grounds: heade kommete vastuolu, seaduse vastuolu, näilik tehing
- Tühistamise alused: eksimus, pettus, ähvardus - all present

**Esindus (§115-131)**
- Seadusjärgne vs tehinguline esindus - correct
- Volitus lifecycle - documented
- Unauthorized representation - ratification rule present
- Self-dealing restriction (§131) - included

**Status:** ✅ Comprehensive and legally accurate

---

### 4. Ontology Schema Validation ✅

**civillaw_ontology_skeleton.ttl:**
- 9 core classes: LegalDocument, Section, Article, Norm, Subject, LegalAction, Condition, Exception, Sanction, Reference
- 13 properties covering structural, semantic, and metadata needs
- OWL compliance: ✅
- Legal domain coverage: ✅

**SMEE Template (Peep):**
- Aligned with interface-contract-v0.1
- Classes: LegalDocument, Provision, Reference, ProcedureStep, Actor, DateInterval, SourceMeta
- Properties cover provenance, intervals, actors
- Status: ✅ Compatible with KRR pipeline

---

## Issues Identified (Non-blocking)

### 1. Namespace Placeholders ⚠️
- Current: `example.org`, `example.ee`
- Should be: Production domain (e.g., `ontoloogia.just.ee`)
- Impact: Development only - must change before production

### 2. Enhanced Norm Classification 📝
Current types used:
- declarative
- definition  
- imperative_methodological
- conditional_permissive
- declarative_exhaustive
- conditional_declarative
- declarative_temporal

All classifications are legally sound and internally consistent.

### 3. Missing Cross-References 📝
- Some internal references between articles not yet formalized
- Example: §2(2) tava vs seadus hierarchy could reference §4 analogia
- Impact: Low - can be added in v0.2

---

## Recommendations

### Immediate (v0.1)
1. ✅ Current state is legally accurate - no blocking issues
2. GitHub repo is up to date with validated files

### Next Phase (v0.2)
1. Add cross-reference properties between related articles
2. Expand subject classification (füüsiline/juriidiline isik distinction)
3. Add condition_type and exception_type semantics

### Before Production
1. Replace example.org with official domain
2. Add SHACL validation rules
3. Create golden test set with lawyer verification

---

## Conclusion

**ALL ONTOLOGY FILES PASS LEGAL ACCURACY REVIEW.**

The KRR ontology correctly represents:
- ✅ TsÜS structure (Osa 1-7 mapped)
- ✅ Legal norm types and classifications
- ✅ Estonian civil law concepts
- ✅ SMEE interface compatibility
- ✅ JSON-LD/TTL technical validity

**The ontology is ready for:**
- Henrik's review
- Continued expansion (VÕS, AÕS)
- Integration with SMEE pipeline

---

**Reviewer:** Tambet  
**Review Date:** 2026-02-27  
**Next Review:** When VÕS mapping begins
