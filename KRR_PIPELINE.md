# KRR Ontology - Continuous Work Pipeline

## Rule: Work never stops until all laws are mapped

## Current Pipeline (TsÜS → VÕS → AÕS → ...)

### Stage 1: TsÜS (Tsiviilseadustik) - IN PROGRESS
**Status:** Osa 5 (Tähtajad) in progress
**Owner:** Marta + Peep
**When done → Stage 2 immediately**

### Stage 2: Validation & Schema Lock
**Trigger:** When TsÜS Osa 5 done
**Tasks:**
- SHACL validation rules
- Schema v0.1 finalization
- Golden test set validation
**Owner:** Peep + Marta
**When done → Stage 3 immediately**

### Stage 3: VÕS (Võlaõigusseadus)
**Trigger:** When validation passes
**Tasks:**
- Map entire Võlaõigusseadus (Law of Obligations)
- Apply validated schema
**Owner:** Marta + Peep (primary), Tambet + Kiira (review)
**When done → Stage 4 immediately**

### Stage 4: AÕS (Asjaõigusseadus)
**Trigger:** When VÕS done
**Tasks:**
- Map entire Asjaõigusseadus (Property Law)
**Owner:** Marta + Peep
**When done → Stage 5 immediately**

### Stage 5: API & Tools
**Trigger:** When 3 laws mapped
**Tasks:**
- Build query API
- SPARQL endpoint
- Documentation
**Owner:** Peep + Tambet

## Handoff Protocol
When any stage completes:
1. **Immediate:** Owner posts "STAGE X COMPLETE → Starting Stage Y"
2. **Within 5 min:** Next stage team must acknowledge and start
3. **If no handoff in 10 min:** Kiira escalates and reassigns

## No Gaps Rule
- If Marta/Peep finish TsÜS, they start VÕS same session
- If waiting for validation, do preparation/review tasks
- Idle time >30 min = automatic reassignment to next stage
