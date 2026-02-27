# OpenClaw Action Plan for Henrik - Combined Team Analysis
**Date:** 2026-02-23
**Team:** Peep (Technical), Marta (Content/Strategy), Kiira (Operations), Tambet (Coordination)
**Location:** `~/.openclaw/shared/` (accessible to all agents)

---

## Individual Agent Contributions

### Peep - Technical Architecture Analysis
**File:** `openclaw-action-plan-henrik.md` (technical focus)

**Key Technical Recommendations:**
- Shared core platform with project-specific views (not separate stacks)
- Normalized entity model: Person, Org, Project, Interaction, Task, Deal, Document
- Per-project permission boundaries
- Reusable agent workflows

**Technical Priority Order:**
1. Data foundation (contact model + project taxonomy)
2. CRM ingestion + meeting briefing
3. Task extraction pipeline
4. RAG workspace (partitioned)
5. Business analysis agents
6. Sales NL layer
7. Memory synthesis + security

---

### Marta - Content & Strategy Analysis
**Focus:** Research/content strategy perspective

**Key Strategic Recommendations:**
- **Parempoolsed (Political):** Knowledge Base (RAG) "Campaign Brain" is CRITICAL
  - Feed party manifestos, opponent positions, media monitoring
  - Draft talking points, rebuttals, social posts aligned with messaging
  
- **Amperly (Agency):** Task Automation from transcripts
  - Convert vague client promises into structured action items
  - Prevent scope creep and forgotten deliverables

**Context Segmentation Strategy:**
- RAG structured by project: `/knowledge/Parempoolsed/`, `/knowledge/Amperly/`
- Meta-tagging with Project ID prevents cross-contamination
- "Friday Review" weekly synthesis across all 10 projects

**Marta's Phase Priorities:**
1. **Phase 1 (Weeks 1-2):** Long-Term Memory + Meeting Prep
2. **Phase 2 (Weeks 3-4):** Parempoolsed Knowledge Base + Personal CRM
3. **Phase 3 (Weeks 5-6):** Amperly Task Generation + Business Analysis
4. **Phase 4 (Ongoing):** Backups + Security

---

### Kiira - Operations & Implementation Roadmap
**File:** `OpenClaw_Action_Plan_Operations.md`

**Key Operational Findings:**
- **Highest daily workflow impact:** Meeting Prep, Task Generation, Knowledge Base
- **Critical cross-project coordination:**
  - Tendly → Sixty Four (tender pipeline)
  - Amperly ↔ Ministry (shared CodePlanner engine)
  - Parempoolsed ↔ ISOC (political/networking overlap)
  - Single point of failure: Henrik's calendar

**Kiira's Implementation Phases:**

| Phase | Timeline | Focus | Use Cases |
|-------|----------|-------|-----------|
| **1** | Weeks 1-4 | Immediate relief | Meeting prep, Task generation, Knowledge Base |
| **2** | Weeks 5-8 | Data unification | Personal CRM, Business analysis, Sales pipeline |
| **3** | Weeks 9-12 | Infrastructure | Backups, Long-term memory, Security |

**Critical Implementation Note:**
Contact tagging must be **cross-project from Day 1** — a TalTech contact might be an Amperly client, Parempoolsed supporter, AND ISOC member.

---

## Team Consensus: Unified Action Plan

### Immediate Priorities (All Agents Agree)

**1. Meeting Prep + Daily Briefings (#2)**
- **Why:** Reduces context-switching fatigue across all 10 projects
- **Peep:** High feasibility, calendar + CRM integration
- **Marta:** Essential for political campaign (briefings on attendees)
- **Kiira:** Highest daily workflow impact

**2. Task Generation from Transcripts (#6)**
- **Why:** Prevents action items from falling through cracks
- **Peep:** Extraction prompt + task sink
- **Marta:** Critical for Amperly client work (convert vague promises)
- **Kiira:** Immediate relief, prevents lost follow-ups

**3. Knowledge Base / RAG (#3)**
- **Why:** Stops re-reading same research across projects
- **Peep:** Per-project collections with citation
- **Marta:** "Campaign Brain" for Parempoolsed is CRITICAL
- **Kiira:** Reduces research duplication

**4. Long-Term Memory / Weekly Synthesis (#8)**
- **Why:** Henrik needs continuity managing 10 projects
- **Peep:** Weekly synthesis with confidence/source links
- **Marta:** "Friday Review" — State of the Union for portfolio
- **Kiira:** Prevents forgetting Monday by Friday

### Project-Specific Focus (Team Consensus)

| Project | Primary Use Case | Secondary Use Case |
|---------|------------------|-------------------|
| **Parempoolsed** | Knowledge Base (RAG) | Meeting Prep |
| **Amperly** | Task Generation | Sales Pipeline |
| **Tendly** | Business Analysis | Knowledge Base |
| **Gas Trade Service** | Sales Pipeline | Business Analysis |
| **Ministry of Social Affairs** | Meeting Prep | Task Generation |
| **Sixty Four OÜ** | Tender RAG + Pipeline | Task Generation |
| **ISOC Estonia** | Contact/Event CRM | Meeting Prep |
| **Youth Jobs Portal** | RAG + CRM (from day 1) | Task System |

### Shared Infrastructure (All Agents)

**Data Foundation:**
- Unified contact model with cross-project tagging
- Project taxonomy: Parempoolsed, Amperly, Tendly, Gas Trade, VoxPoll, Easy.ee, Ministry, Sixty Four, ISOC, Youth Jobs
- One ingestion bus, reusable workflows

**Security & Compliance:**
- Critical due to political + public-sector + client data mix
- Nightly security checks + backup validation
- Per-project permission boundaries

---

## Next Steps (Team Action Items)

1. **Tambet:** Consolidate this document, share with Henrik
2. **Peep:** Design data foundation schema (contact model, project taxonomy)
3. **Marta:** Draft Parempoolsed Knowledge Base content requirements
4. **Kiira:** Create implementation timeline with weekly milestones
5. **All:** Review and refine during next retrospective

---

## File Locations

All team documents saved to `~/.openclaw/shared/`:
- `openclaw-action-plan-henrik.md` (Peep's technical analysis)
- `OpenClaw_Action_Plan_Operations.md` (Kiira's operations roadmap)
- `OpenClaw_Action_Plan_Combined.md` (this combined document)

**Accessible to:** Peep, Marta, Kiira, Tambet (all agents)
