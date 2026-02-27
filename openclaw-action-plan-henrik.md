# OpenClaw Action Plan for Henrik
**Date:** 2026-02-23
**Source:** Inspiration document + Team analysis (Peep, Tambet)
**Status:** Draft

---

## Overview

This plan adapts 9 OpenClaw use cases from an external inspiration document to Henrik's specific project portfolio. The goal is maximum time savings across all ventures through a shared "operating system" layer.

---

## Henrik's Projects

1. **Parempoolsed** - Political party, MP campaign (Tallinn districts)
2. **Amperly** - AI agency, clients, podcast
3. **Tendly** - Tender matching startup (expanding internationally)
4. **Gas Trade Service** - B2B LPG marketplace
5. **VoxPoll** - AI polling surveys
6. **Easy.ee** - Grant matching platform
7. **Ministry of Social Affairs** - AI automation consulting
8. **Sixty Four OÜ** - IT tenders (zero employees model)
9. **ISOC Estonia** - NGO, internet community events
10. **Youth Jobs Portal** - New project (underage job seekers)

---

## Phase 1 (Weeks 1-2): Immediate ROI / Low Complexity

### 1) Unified CRM + Relationship Graph [TOP PRIORITY]

**Why:** Henrik works across politics, consulting, startup, NGO, and sales. Context switching is costly.

**Feasibility:** High (Google APIs + lightweight DB)

**Implement:**
- Ingest from Gmail (senders/threads), Calendar attendees, manual notes
- Contact schema: person, org, project tags, "where we met", last interaction, next step, influence level
- Auto-link interaction history

**Integrations:** Google Workspace APIs, Postgres/Supabase

---

### 2) Daily Meeting Prep Briefings

**Why:** Meeting prep repeats daily; huge cognitive load reduction

**Implement:**
- Morning digest + pre-meeting just-in-time brief
- Shows: who, org, project context, last email/meeting summary, open tasks, suggested agenda

**Integrations:** Calendar + Gmail + CRM DB

---

### 3) Transcript/Notes → Task Generation

**Why:** Follow-ups get lost across many calls/ventures

**Implement:**
- Input: meeting transcript, voice memo, brainstorm doc
- Output: tasks with owner, deadline, project, confidence
- Push to task system with approval step

**Integrations:** Transcript source (Whisper), Task manager API, CRM link-back

---

## Phase 2 (Weeks 3-6): Strategic Compounding Layer

### 4) Portfolio Knowledge Base (RAG)

**Why:** Retrieval speed is major bottleneck across many domains

**Implement:**
- Per-project collections + cross-project "portfolio" collection
- Sources: websites, proposals, decks, docs, legal/policy, tenders, podcast notes
- Features: cite sources, freshness metadata, access controls

**Integrations:** Google Drive/Notion/GitHub scraper, Vector DB (pgvector/Weaviate)

---

### 5) Nightly Business Analysis Agent Team

**Why:** Strategic clarity after data pipelines exist

**Start with:** Amperly, Tendly, Gas Trade Service (commercially measurable)

**Agent roles:** operator, skeptic, growth strategist, risk/compliance

**Output:** anomalies, opportunities, next-day decisions

**Integrations:** KPI sources (CRM, finance, analytics), scheduled workflow

---

## Phase 3 (Weeks 6-10): Vertical Optimizations

### 6) Sales Pipeline NL Query

**Best for:** Amperly, Gas Trade Service, Easy.ee, Sixty Four OÜ

**Examples:**
- "Which deals are stalled >14 days?"
- "Show top 10 warm leads in public-sector"

**Integrations:** HubSpot/Pipedrive/Close, data hygiene jobs

---

### 7) Long-term Memory Synthesis (Weekly)

**Why:** Decision continuity, compounding benefit

**Implement:**
- Weekly "what changed" synthesis
- Preferences, partner dynamics, strategic bets, recurring blockers
- Write to durable memory files with confidence/source links

---

### 8) Security Checks + Backup Maturity

**Why:** Critical with political + public-sector + client data mix

**Implement:**
- Nightly checks: secret leaks, repo permissions, stale tokens, dependency vulns
- Backup validation: restore tests, retention policy audit

**Integrations:** GitHub API, secret scanning, dependency scanner

---

## Project-by-Project Focus

| Project | Priority Use Cases |
|---------|-------------------|
| **Parempoolsed** | CRM + meeting prep + secure knowledge base (privacy controls) |
| **Amperly** | CRM, sales pipeline, task generation, RAG, nightly analysis |
| **Tendly** | RAG (tenders/regulations), business analysis, task generation |
| **Gas Trade Service** | Sales pipeline + business analysis + CRM |
| **VoxPoll** | RAG for research history + analysis agents |
| **Easy.ee** | RAG for grants + task generation for workflows |
| **Ministry of Social Affairs** | Meeting prep + secure task gen + audit logs |
| **Sixty Four OÜ** | Tender RAG + pipeline tracker + reminders |
| **ISOC Estonia** | Contact/event CRM + meeting prep + task extraction |
| **Youth Jobs Portal** | RAG + CRM + task system (clean architecture from day one) |

---

## Implementation Order

1. **Data foundation:** identity/contact model + project tagging taxonomy
2. **CRM ingestion + meeting briefing**
3. **Task extraction pipeline**
4. **RAG workspace (partitioned by project)**
5. **Business analysis agents (pilot on 2-3 projects)**
6. **Sales NL layer for sales-heavy entities**
7. **Memory synthesis + security/audit automation**

---

## Architecture Recommendation

Use a **shared core platform** with project-specific views:
- One ingestion bus
- One normalized entity model (Person, Org, Project, Interaction, Task, Deal, Document)
- Per-project permission boundaries
- Reusable agent workflows

**Benefits:** Fastest delivery, lowest maintenance burden

---

## Key Insights

- **Most immediate wins:** CRM + Meeting Prep + Task Generation (high feasibility, high time savings)
- **RAG** is next major multiplier, needs strict project partitioning
- **Business analysis agents** should wait until KPI feeds are stable
- **Sales NL querying** valuable only where pipeline data is mature
- **Security/compliance** matters more than usual due to data mix (political + public-sector + client data)

---

## Next Steps

1. Review and prioritize Phase 1 items with Henrik
2. Set up data foundation (contact model, project taxonomy)
3. Begin CRM ingestion implementation
4. Schedule check-in after Phase 1 completion
