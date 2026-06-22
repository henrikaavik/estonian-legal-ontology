# Fitness-for-purpose evaluation

_Generated (pinned): 2026-06-01T00:00:00+00:00 · ontology 0.11.0_

Objective, reproducible metrics over the indexed root-law corpus — no hand-adjudication. Regenerate with `python3 scripts/eval_harness.py`.

**Population:** 1122 indexed laws (1152 files), 39,082 provisions.

## Vertical coverage (per law)

| Vertical | Laws with | % |
| --- | --: | --: |
| sanctions | 203 | 18.1% |
| competentAuthority | 623 | 55.5% |
| interpretedBy | 84 | 7.5% |
| euDirective | 99 | 8.8% |
| pointInTime | 737 | 65.7% |

## Vertical co-occurrence (the 'complete vertical' question)

Laws carrying **all 5 verticals as edges**: **26**.

| Verticals present (referenced) | # laws |
| --: | --: |
| 0 | 378 |
| 1 | 118 |
| 2 | 369 |
| 3 | 164 |
| 4 | 67 |
| 5 | 26 |

Most-complete laws (proof-of-purpose candidates to materialise inline):

- `ariseadustik` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `avaliku_teabe_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `elektrituruseadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `elektroonilise_side_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `hadaolukorra_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `isikuandmete_kaitse_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `jaatmeseadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `karistusseadustik` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `kaugkutteseadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `kindlustustegevuse_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions

### Retrievability gap (the #617 finding, quantified)

All verticals are present as edges on 26 laws, but the answer is **not inline-retrievable** on **0** of them: sanctions resolve to an unmerged sidecar (0 inline definitions) and only 0.0% of provisions carry a known `temporalStatus`. So no single law yet answers "what does it require, who enforces it, what are the sanctions as of today" from its own nodes.

## Other fitness metrics

- **Cross-reference edge resolution:** 28,207 / 28,207 (100.0%) existing citation edges resolve to an in-corpus node (edge precision, not extraction recall).
- **Point-in-time:** 0.0% of provisions carry a known `temporalStatus`; 737 laws have a version sidecar.
- **Inline sanctions:** 0 sanction definitions inline in peeps vs 2455 `hasSanction` edges (inline: False).
