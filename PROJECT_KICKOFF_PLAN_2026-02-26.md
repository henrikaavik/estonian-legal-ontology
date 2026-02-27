# Project Kickoff Plan (Immediate) — 2026-02-26

## Decision (agreed now)
We **agree with the proposed immediate action** and are starting execution immediately with a 30-minute sprint.

## Team Operating Model (from prior alignment)
- **Coordinator:** Kiira (task orchestration, timeline, unblockers)
- **Tech Lead:** Peep (architecture, ontology, implementation decisions)
- **Domain/Research:** Marta (legal source mapping, domain model input)
- **Comms/Docs:** Tambet (documentation structure, communication artifacts)

Cadence:
- **Daily standup** (short status + blockers)
- **Deep-dives 2x/week** (architecture + domain decisions)
- Work flow: **content → tech → review**

---

## 30-Minute Sprint Plan (starts now)

### Kiira (Coordinator) — START NOW
1. Create and maintain shared task board in this file.
2. Track owners, deadlines, and blockers.
3. Schedule 30-minute sync check.

### Marta (Research) — START NOW
1. Map **Riigi Teataja** structure:
   - legal act hierarchy (law / regulation / order / etc.)
   - metadata fields (identifier, issuer, effective date, amendments, references)
   - publication/update/versioning logic
2. Output: concise structured research note for ontology input.

### Peep (Tech Lead) — START NOW
1. Define ontology representation and stack proposal:
   - format candidates (JSON-LD / RDF / property graph mapping)
   - minimal entity schema and relation set
   - storage/query direction and validation approach
2. Output: technical draft with recommended default stack.

### Tambet (Documentation) — START NOW
1. Create documentation template set:
   - decision log template
   - research note template
   - technical spec template
   - weekly progress update template
2. Ensure naming conventions and folder structure are clear.

---

## First Task Board (live)

| Task | Owner | Status | Due | Deliverable |
|---|---|---|---|---|
| Shared kickoff plan + task board | Kiira | IN PROGRESS | +10 min | This file updated |
| Riigi Teataja structure research note | Marta | TODO/STARTING | +30 min | `RIIGI_TEATAJA_STRUCTURE.md` |
| Ontology format/stack draft | Peep | IN PROGRESS | +30 min | `ONTOLOGY_STACK_DRAFT.md` |
| Documentation templates pack | Tambet | TODO/STARTING | +30 min | `DOC_TEMPLATES.md` |

---

## Sync in 30 Minutes
Agenda:
1. 2-minute updates each (what done / blocker / next)
2. Decide v0 ontology direction from Marta + Peep outputs
3. Lock next 2-hour execution block

## Success Criteria for this sprint
- All four deliverables created in shared folder
- One clear ontology direction selected
- Next execution block assigned with owners
