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

### Retrievability gap (the #617 finding, refined)

The verticals are present as edges on 26 laws, and on the full load surface (combined + version sidecars) most resolve — sanctions are merged into combined as overlay nodes (#561) and act-level `temporalStatus` is now known on **65.6%** of laws (derived from version data, #617). The residual gap is per-PEEP self-containment: a single `*_peep.json` still carries 0 inline sanction definitions (they live in the `sanctions/` sidecar, merged only into combined). **The consumable answer surface is combined, not the individual peep.**

## Other fitness metrics

- **Cross-reference edge resolution:** 28,207 / 28,207 (100.0%) existing citation edges resolve to an in-corpus node (edge precision, not extraction recall).
- **Point-in-time (two layers, #128):** act-level `temporalStatus` known on 65.6% of laws (736); provision-level validity via version sidecars on 65.7% (737 laws). Provisions never carry `temporalStatus`.
- **Inline sanctions (per peep):** 0 inline definitions vs 2455 `hasSanction` edges — sanctions are in combined via overlay (#561), not inline in the peep.
