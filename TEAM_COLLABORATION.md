# Team Collaboration Protocol v1 (Accepted)

Status: **Accepted and active**

Reference (shared): `~/.openclaw/shared/TEAM_COLLABORATION_PROTOCOL_V2.md`

## 1) Roles per project
- **Owner (single):** accountable for final outcome and deadline.
- **Driver (current executor):** does the active implementation work.
- **Reviewer (mandatory second pair of eyes):** validates and improves before done.

Rule: **Owner cannot be the final Reviewer**.

## 2) Decision flow (who executes)
1. Quick kickoff: objective, constraints, done-criteria.
2. Choose Driver by best-fit skill for this work slice.
3. If disagreement >10 min or no consensus, **Owner decides**.
4. If high-risk/ambiguous requirement, escalate to Henrik with options.

## 3) Mandatory review gate
No output is sent as final without independent review.
Reviewer must:
- check correctness vs request,
- improve weak spots directly (not only comments),
- confirm ship/no-ship.

## 4) Escalation
Escalate when:
- blocked after 2 focused attempts,
- requirements conflict,
- potential security/data risk appears,
- scope drift exceeds ~20% from original ask.

Escalation format:
- Problem
- What was tried
- Options (A/B/C)
- Recommendation

## 5) Handoff standard (always include)
- Objective
- Work completed
- Open issues
- Risks
- Exact next action
- Done criteria

## 6) Non-negotiables
1. One accountable Owner per project.
2. Peer review is mandatory (except explicit emergency override).
3. Full context in delegation/handoffs.
4. No silent stalls; raise blockers early.
5. Decisions that matter are written to files.

## 7) Recurring retrospectives (mandatory)
Cadence: **every Monday and Thursday**.

Retro agenda:
1. Review all activities completed since the previous retrospective.
2. Review a rolling summary of the **last 30 days** (patterns, recurring blockers, wins).
3. Evaluate collaboration quality and output quality (what worked, what failed, where quality slipped).
4. Agree concrete improvements and apply them to process/docs immediately (no Henrik approval required for internal workflow changes).

Execution rules:
- Record each retro outcome in `memory/YYYY-MM-DD.md`.
- If process changes are agreed, update `TEAM_COLLABORATION.md`/`AGENTS.md` in the same cycle.
- Changes become active immediately after update and commit.
