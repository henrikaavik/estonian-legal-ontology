# Fitness-for-purpose evaluation

_Generated (pinned): 2026-06-01T00:00:00+00:00 · ontology 1.0.0_

Objective, reproducible metrics over the indexed root-law corpus — no hand-adjudication. Regenerate with `python3 scripts/eval_harness.py`.

**Population:** 1122 indexed laws (1157 files), 39,545 provisions.

## Vertical coverage (per law)

| Vertical | Laws with | % |
| --- | --: | --: |
| sanctions | 239 | 21.3% |
| competentAuthority | 560 | 49.9% |
| interpretedBy | 84 | 7.5% |
| euDirective | 99 | 8.8% |
| pointInTime | 737 | 65.7% |

## Vertical co-occurrence (the 'complete vertical' question)

Laws carrying **all 5 verticals as edges**: **29**.

| Verticals present (referenced) | # laws |
| --: | --: |
| 0 | 378 |
| 1 | 183 |
| 2 | 278 |
| 3 | 181 |
| 4 | 73 |
| 5 | 29 |

Most-complete laws (proof-of-purpose candidates to materialise inline):

- `ariseadustik` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `avaliku_teabe_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `elektrituruseadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `elektroonilise_side_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `finantsinspektsiooni_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `hadaolukorra_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `isikuandmete_kaitse_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `jaatmeseadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `karistusseadustik` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `kaugkutteseadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions

### Retrievability gap (the #617 finding, refined)

The verticals are present as edges on 29 laws, and on the full load surface (combined + version sidecars) most resolve — sanctions are merged into combined as overlay nodes (#561) and act-level `temporalStatus` is now known on **65.6%** of laws (derived from version data, #617). The residual gap is per-PEEP self-containment: a single `*_peep.json` still carries 0 inline sanction definitions (they live in the `sanctions/` sidecar, merged only into combined). **The consumable answer surface is combined, not the individual peep.**

## Other fitness metrics

- **Cross-reference edge resolution:** 35,671 / 35,671 (100.0%) existing citation edges resolve to an in-corpus node (reference integrity, not semantic precision or extraction recall).
- **Point-in-time (two layers, #128):** act-level `temporalStatus` known on 65.6% of laws (736); provision-level validity via version sidecars on 65.7% (737 laws). Provisions never carry `temporalStatus`.
- **Inline sanctions (per peep):** 0 inline definitions vs 7145 `hasSanction` edges — sanctions are in combined via overlay (#561), not inline in the peep.
