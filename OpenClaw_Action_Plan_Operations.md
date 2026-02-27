# OpenClaw Action Plan: Operations & Implementation Roadmap

**Prepared by:** Kiira (Operations/PM)  
**Date:** 2026-02-23  
**Context:** Peep's technical analysis already provided

---

## Executive Summary

Henrik manages 10 active projects spanning politics, 5+ business ventures, government consulting, and NGO work. The OpenClaw use cases can reduce daily cognitive load by ~40% through automation of repetitive coordination tasks and improve cross-project resource allocation.

**Recommended Phasing:**
- **Phase 1 (Weeks 1-4):** Immediate workflow relief — Meeting prep, Task generation, Knowledge Base
- **Phase 2 (Weeks 5-8):** Data & coordination — Personal CRM, Business analysis, Sales pipeline
- **Phase 3 (Weeks 9-12):** Infrastructure & memory — Automated backups, Long-term memory, Security checks

---

## 1. Project Portfolio Analysis

### Project Categories & Coordination Needs

| Category | Projects | Coordination Intensity | Key Dependencies |
|----------|----------|------------------------|------------------|
| **Political** | Parempoolsed | HIGH | Daily news monitoring, donor CRM, event logistics |
| **AI Agency** | Amperly | HIGH | Client pipeline, content calendar, project handoffs |
| **Marketplaces** | Tendly, Gas Trade, Easy.ee | MEDIUM | Tender/grant alerts, transaction monitoring |
| **Gov Consulting** | Ministry of Social Affairs, Sixty Four | MEDIUM | Compliance tracking, tender deadlines |
| **Pre-launch** | VoxPoll, Youth Jobs Portal | LOW → HIGH | Sales activities, development sprints |
| **NGO** | ISOC Estonia | LOW | Event planning, member communications |

### Cross-Project Resource Conflicts

**Critical Observation:** Henrik's projects share overlapping stakeholders:
- **Tendly → Sixty Four:** Tendly identifies tenders, Sixty Four bids on them
- **Amperly → Ministry:** Shared neurosymbolic engine (CodePlanner component)
- **Parempoolsed → ISOC:** Political networking overlaps with tech community
- **All projects → Henrik's calendar:** Single point of coordination failure

**Recommendation:** Implement cross-project tagging in Personal CRM from Day 1.

---

## 2. Use Case Prioritization Matrix

### Scoring Criteria (1-5 scale)
- **Daily Impact:** How much does this reduce daily cognitive load?
- **Cross-Project Value:** Does it benefit multiple projects simultaneously?
- **Implementation Ease:** Technical complexity vs. value (Peep's input)
- **Risk if Delayed:** What breaks without this?

| Use Case | Daily Impact | Cross-Project | Ease | Risk | **Priority Score** | Phase |
|----------|-------------|---------------|------|------|-------------------|-------|
| 2. Meeting prep | 5 | 5 | 4 | 3 | **17** | 1 |
| 6. Task generation | 5 | 4 | 4 | 4 | **17** | 1 |
| 3. Knowledge Base | 4 | 5 | 3 | 3 | **15** | 1 |
| 1. Personal CRM | 4 | 5 | 3 | 4 | **16** | 2 |
| 4. Business analysis | 3 | 4 | 3 | 2 | **12** | 2 |
| 5. Sales pipeline | 4 | 3 | 3 | 3 | **13** | 2 |
| 7. Automated backups | 2 | 3 | 5 | 5 | **15** | 3 |
| 8. Long-term memory | 3 | 4 | 4 | 2 | **13** | 3 |
| 9. Security checks | 2 | 3 | 4 | 4 | **13** | 3 |

---

## 3. Detailed Implementation Roadmap

### PHASE 1: Immediate Workflow Relief (Weeks 1-4)
**Goal:** Reduce daily decision fatigue and capture action items

#### 2. Meeting Prep (Daily Briefings)
**Why First:** Every project has meetings; this has immediate ROI

**Implementation:**
- **Week 1:** Connect Google Calendar API
- **Week 2:** Create briefing template per project type:
  - Parempoolsed: Yesterday's news mentions, donor activity, upcoming events
  - Amperly: Client status, proposal deadlines, content calendar
  - Tendly: New tenders in target markets, bid deadlines
  - Ministry: Process mapping milestones, compliance items
- **Week 3:** Add contact context from Personal CRM (see Phase 2)
- **Week 4:** Add news/pricing alerts for Gas Trade Service

**Daily Output:**
```
📅 BRIEFING: Monday, Feb 23, 2026

⏰ TODAY'S MEETINGS (4)
├─ 09:00 - Ministry of Social Affairs (Project review)
│   └─ Context: Process mapping Phase 2, deadline March 15
├─ 11:30 - Amperly client: TalTech (Proposal follow-up)
│   └─ Context: AI training contract, €12k value, 80% close probability
├─ 14:00 - Parempoolsed strategy (Campaign planning)
│   └─ Context: District polling data, volunteer coordination
└─ 16:00 - Tendly team sync (Expansion review)
   └─ Context: Finland launch prep, Latvia performance

⚠️ ACTION ITEMS FROM YESTERDAY (3 pending)
├─ [Parempoolsed] Confirm venue for March town hall
├─ [Amperly] Send revised proposal to FoodStudio
└─ [Ministry] Submit interim report draft

📰 RELEVANT NEWS (filtered)
├─ [Political] Reform Party announces tax policy shift
├─ [Tenders] 3 new IT tenders matching Sixty Four profile
└─ [Gas] LPG price movement +2.3% this week
```

**Cross-Project Coordination:**
- Flag when same attendee appears in multiple project meetings
- Warn about scheduling conflicts across projects
- Surface opportunities (e.g., Amperly client → Parempoolsed supporter)

---

#### 6. Task Generation (from Transcripts/Notes)
**Why First:** Captures action items before they're lost

**Implementation:**
- **Week 1:** Set up transcription pipeline (Otter/Zoom/Meet integration)
- **Week 2:** Create task extraction rules per project:
  - Parempoolsed: Campaign tasks → @Campaign team
  - Amperly: Client deliverables → Project tracker
  - Tendly: Tender deadlines → Bid calendar
  - All: Personal action items → Henrik's task list
- **Week 3:** Auto-assign based on keywords ("Henrik will..." → Henrik; "We should..." → discussion)
- **Week 4:** Connect to existing tools (Notion/Trello/Linear per project)

**Project-Specific Task Routing:**
| Source | Auto-Tag | Destination |
|--------|----------|-------------|
| Parempoolsed meetings | #campaign | Campaign notion |
| Amperly client calls | #client-work | Amperly CRM |
| Tendly/Gas Trade/Easy | #platform | Internal trackers |
| Ministry consulting | #gov-project | Confidential docs |
| Sixty Four tender calls | #bidding | Tendly-linked |
| ISOC events | #community | Event planning doc |

---

#### 3. Knowledge Base (RAG for Articles/Docs)
**Why First:** Prevents re-reading the same research across projects

**Implementation:**
- **Week 1:** Set up document ingestion (Drive, Notion, local files)
- **Week 2:** Organize by project + cross-cutting topics:
  ```
  KB Structure:
  ├── Projects/
  │   ├── Parempoolsed/
  │   ├── Amperly/
  │   ├── Tendly/
  │   ├── Gas Trade/
  │   ├── Easy.ee/
  │   ├── Ministry/
  │   ├── Sixty Four/
  │   ├── ISOC/
  │   └── Youth Jobs/
  ├── Cross-Cutting/
  │   ├── AI/ML Research (shared across Amperly/Ministry)
  │   ├── Estonian Politics (Parempoolsed/ISOC)
  │   ├── Tender Processes (Tendly/Sixty Four/Ministry)
  │   └── Grant Programs (Easy.ee/ISOC/Youth Jobs)
  └── Sources/
      ├── Email archives
      ├── Meeting transcripts
      ├── Web articles
      └── Legal documents
  ```
- **Week 3:** Add query interface (natural language search)
- **Week 4:** Integrate into Meeting Prep (auto-suggest relevant docs)

**Cross-Project Knowledge Sharing:**
- **CodePlanner engine** (Amperly/Ministry): Share research papers, architecture decisions
- **Tender processes** (Tendly/Sixty Four): Document bidding strategies, pricing intel
- **Political messaging** (Parempoolsed/ISOC): Speech templates, policy briefs

---

### PHASE 2: Data & Coordination (Weeks 5-8)
**Goal:** Unify contact management and business intelligence

#### 1. Personal CRM (Gmail/Calendar Contacts)
**Why Second:** Builds on Phase 1 data, enables relationship intelligence

**Implementation:**
- **Week 5:** Import Gmail/Calendar history, deduplicate contacts
- **Week 6:** Add project tagging:
  - Contact can have multiple project tags
  - Priority levels per project (donor > volunteer > supporter)
  - Last interaction tracking across all channels
- **Week 7:** Relationship health scoring:
  - Time since last contact
  - Interaction frequency trends
  - Opportunity signals (replies quickly, attends events)
- **Week 8:** Integration points:
    - Meeting prep: "You haven't spoken to [key donor] in 45 days"
    - Task generation: "Follow up with [prospect] mentioned in transcript"
    - Business analysis: "Client X also connected to Project Y"

**Cross-Project Contact Mapping:**
```
Contact: Jane Smith (TalTech)
├─ Amperly: Client (AI training contract)
├─ Parempoolsed: Potential supporter (academic network)
├─ ISOC: Member (internet governance interest)
└─ Last interaction: 3 days ago (Amperly proposal call)
→ SUGGESTION: Invite to Parempoolsed tech policy event
```

---

#### 4. Business Analysis (Agent Team Reviews Data Nightly)
**Why Second:** Requires Phase 1-2 data pipelines to be running

**Implementation:**
- **Week 5:** Define metrics per project:
  - Parempoolsed: Polling trends, donation velocity, event attendance
  - Amperly: Pipeline value, client churn risk, project margins
  - Tendly: Match rate, international expansion metrics, user growth
  - Gas Trade: Transaction volume, bid success rate, price trends
  - Easy.ee: Grant match success, user engagement
  - Ministry: Milestone adherence, deliverable status
  - Sixty Four: Win rate, tender pipeline value
- **Week 6:** Set up nightly data aggregation (Peep to implement collectors)
- **Week 7:** Create agent review prompts (anomaly detection, trends)
- **Week 8:** Morning briefings with insights + recommended actions

**Cross-Project Insights:**
- "Client [X] from Amperly is also a decision maker in [Tender Y]"
- "Polling dip in Põhja-Tallinn coincides with reduced ISOC event attendance"
- "Sixty Four bid success rate improves when Tendly alerts arrive <24h before deadline"

---

#### 5. Sales Pipeline (Natural Language Queries)
**Why Second:** Depends on Personal CRM and Business Analysis data

**Implementation:**
- **Week 6:** Aggregate pipeline data from:
  - Amperly: Active proposals, client negotiations
  - Sixty Four: Tender bids in progress
  - VoxPoll: Pre-launch prospect conversations
  - Youth Jobs: Employer recruitment pipeline
- **Week 7:** Natural language interface:
  - "What's our total pipeline value?"
  - "Which deals are at risk this month?"
  - "Show me all prospects I haven't contacted in 2 weeks"
- **Week 8:** Predictive scoring + next-action suggestions

---

### PHASE 3: Infrastructure & Memory (Weeks 9-12)
**Goal:** Ensure durability, security, and institutional memory

#### 7. Automated Backups (GitHub + Drive)
**Critical for:** Sixty Four (tender docs), Ministry (compliance), All (code/repos)

**Implementation:**
- **Week 9:** Audit all data repositories
- **Week 10:** Configure automated backups with versioning
- **Week 11:** Test restore procedures
- **Week 12:** Compliance documentation for Ministry work

---

#### 8. Long-Term Memory (Weekly Synthesis)
**Why Third:** Requires sufficient history from Phases 1-2

**Implementation:**
- **Week 9:** Weekly pattern detection:
  - Recurring meeting topics
  - Decision trends
  - Relationship evolution
- **Week 10:** Synthesis reports:
  - "Week of Feb 23: 3 new tender opportunities identified, 2 client proposals sent, 1 political event planned"
  - Pattern recognition: "You typically lose bids when response time >5 days"
- **Week 11:** Quarterly retrospectives with actionable insights
- **Week 12:** Integration with Knowledge Base (auto-tag recurring themes)

---

#### 9. Security Checks (Nightly Audits)
**Critical for:** Ministry (government data), All (API keys, access tokens)

**Implementation:**
- **Week 9:** Define security checklist per project sensitivity
- **Week 10:** Automated scanning (exposed credentials, access logs)
- **Week 11:** Alert thresholds and escalation
- **Week 12:** Monthly security reports

---

## 4. Cross-Project Coordination Framework

### Weekly Rhythm

| Day | Activity | Projects Covered |
|-----|----------|------------------|
| Monday | Pipeline review | All commercial projects |
| Tuesday | Political coordination | Parempoolsed + ISOC |
| Wednesday | Technical sync | Amperly + Ministry (CodePlanner) |
| Thursday | Marketplace ops | Tendly + Gas Trade + Easy.ee |
| Friday | Weekly synthesis | All (Long-term memory) |

### Shared Resources to Manage

1. **Henrik's Calendar** — Single point of failure
   - Color-code by project
   - Block focus time (no meetings)
   - Automatic conflict detection across projects

2. **Contact Database** — Cross-pollination opportunities
   - Tag all contacts with project affiliations
   - Flag "bridge" contacts (active in multiple projects)
   - Track relationship depth per project

3. **Content/Reuse** — Amperly content can support Parempoolsed
   - YouTube podcast episodes → Political messaging
   - AI research → Ministry consulting
   - Tender insights → Sixty Four bidding

4. **Team/Agent Capacity** — 4 agents across 10 projects
   - Weekly capacity planning
   - Clear ownership per project
   - Escalation paths for conflicts

---

## 5. Risk Mitigation

| Risk | Mitigation |
|------|------------|
| **Data silos** | Cross-project tagging from Day 1 |
| **Context switching fatigue** | Meeting prep reduces cognitive load |
| **Single point of failure (Henrik)** | Task generation + automated follow-ups |
| **Missed deadlines** | Automated alerts + Business analysis |
| **Security breach** | Phase 3 security checks |
| **Knowledge loss** | Phase 3 long-term memory + backups |

---

## 6. Success Metrics

### Phase 1 (4 weeks)
- [ ] Meeting prep delivered daily without manual intervention
- [ ] 90%+ of action items captured from transcripts
- [ ] Knowledge Base searchable with <3 second response time

### Phase 2 (8 weeks)
- [ ] Personal CRM shows cross-project relationships
- [ ] Business analysis identifies 1+ insight/week not obvious manually
- [ ] Sales pipeline queryable in natural language

### Phase 3 (12 weeks)
- [ ] 100% automated backup coverage
- [ ] Weekly synthesis reports delivered and reviewed
- [ ] Security checks running nightly with zero critical alerts outstanding

---

## 7. Immediate Next Steps

**This Week (Henrik + Peep):**
1. Approve Phase 1 scope and priorities
2. Provide API access for Calendar, Gmail, Drive
3. Clarify which existing tools to integrate (Notion, Trello, CRM, etc.)

**Next Week (Peep + Kiira):**
1. Set up document ingestion for Knowledge Base
2. Create meeting prep templates per project
3. Configure transcription pipeline

**Week 3-4 (Full Team):**
1. Test meeting prep quality daily
2. Refine task extraction accuracy
3. Build cross-project contact tagging

---

**Document prepared by Kiira**  
*Ready for Peep's technical review and Henrik's approval*
