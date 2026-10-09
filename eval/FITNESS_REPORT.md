# Fitness-for-purpose evaluation

_Generated (pinned): 2026-10-09T00:00:00+00:00 · ontology 1.0.0_

Generated from numbers by `python3 scripts/eval_harness.py --report eval/FITNESS_REPORT.md`; `--check` fails CI-style when this file is stale. Coverage metrics are objective counts over the indexed root-law corpus; the accuracy block comes from the adjudicated items of the legal gold sets (`eval/gold_sets/`, #698).

**Corpus fingerprint:** `0ae499664a44` (sha256 of `krr_outputs/INDEX.json`, first 12 hex).

**Population:** 1127 indexed laws (1162 files), 40,206 provisions.

## Vertical coverage (per law)

| Vertical | Laws with | % |
| --- | --: | --: |
| sanctions | 243 | 21.6% |
| competentAuthority | 446 | 39.6% |
| interpretedBy | 196 | 17.4% |
| euDirective | 99 | 8.8% |
| pointInTime | 737 | 65.4% |

## Vertical co-occurrence (the 'complete vertical' question)

Laws carrying **all 5 verticals as edges**: **44**.

| Verticals present (referenced) | # laws |
| --: | --: |
| 0 | 385 |
| 1 | 270 |
| 2 | 148 |
| 3 | 185 |
| 4 | 95 |
| 5 | 44 |

Most-complete laws (proof-of-purpose candidates to materialise inline):

- `ariseadustik` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `asjaoigusseaduse_rakendamise_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `avaliku_teabe_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `eesti_vaartpaberite_keskregistri_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `elektrituruseadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `elektroonilise_side_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `finantsinspektsiooni_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `hadaolukorra_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `halduskohtumenetluse_seadustik` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions
- `isikuandmete_kaitse_seadus` — 5/5: competentAuthority, euDirective, interpretedBy, pointInTime, sanctions

### Retrievability gap (the #617 finding, refined)

The verticals are present as edges on 44 laws, and on the full load surface (combined + version sidecars) most resolve — sanctions are merged into combined as overlay nodes (#561) and act-level `temporalStatus` is now known on **98.3%** of laws (derived from version data, #617). The residual gap is per-PEEP self-containment: a single `*_peep.json` still carries 0 inline sanction definitions (they live in the `sanctions/` sidecar, merged only into combined). **The consumable answer surface is combined, not the individual peep.**

## Other fitness metrics

- **Cross-reference edge resolution:** 48,397 / 48,397 (100.0%) existing citation edges resolve to an in-corpus node (reference integrity, not semantic precision or extraction recall).
- **Point-in-time (two layers, #128):** act-level `temporalStatus` known on 98.3% of laws (1108); provision-level validity via version sidecars on 65.4% (737 laws). Provisions never carry `temporalStatus`.
- **Inline sanctions (per peep):** 0 inline definitions vs 7555 `hasSanction` edges — sanctions are in combined via overlay (#561), not inline in the peep.

## Accuracy (legal gold sets, #698)

Precision / recall / F1 over **adjudicated** items only (value-level; negatives confirmed empty are true negatives, negatives with a missing value are false negatives). Pending items are counted, never scored. Mechanical verdicts are pre-filled only where a documented rule decides the item from the quoted evidence (see each file's `mechanical_rules`); they cover the easy cases, so these numbers are an upper bound until a legal reviewer adjudicates the pending items.

Gold sets sampled at corpus commit(s): `c3dd2ff7cf`.

| Layer | Items | Adjudicated (mech / reviewer) | Pending | Negatives | TP | FP | FN | TN | Precision | P 95% low | Recall | F1 | Gate |
| --- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --- |
| altLabelFolds | 50 | 28 (28 / 0) | 22 | 12 | 28 | 0 | 0 | 0 | 1.000 | 0.879 | 1.000 | 1.000 | pass |
| competence | 300 | 83 (83 / 0) | 217 | 60 | 83 | 0 | 0 | 0 | 1.000 | 0.956 | 1.000 | 1.000 | pass |
| courtLinks | 300 | 155 (155 / 0) | 145 | 60 | 127 | 0 | 0 | 28 | 1.000 | 0.971 | 1.000 | 1.000 | pass |
| crossReferences | 300 | 227 (227 / 0) | 73 | 60 | 195 | 0 | 0 | 32 | 1.000 | 0.981 | 1.000 | 1.000 | pass |
| deontic | 300 | 156 (156 / 0) | 144 | 60 | 150 | 5 | 0 | 1 | 0.968 | 0.927 | 1.000 | 0.984 | pass |
| eurovoc | 300 | 4 (4 / 0) | 296 | 60 | 4 | 0 | 0 | 0 | 1.000 | 0.510 | 1.000 | 1.000 | not enough adjudicated items |
| sanctions | 300 | 273 (273 / 0) | 27 | 60 | 237 | 0 | 0 | 36 | 1.000 | 0.984 | 1.000 | 1.000 | pass |
| targetGroup | 300 | 3 (3 / 0) | 297 | 60 | 0 | 0 | 0 | 3 | — | — | — | — | not enough adjudicated items |

Floors (`eval/accuracy_floors.json`):

- `altLabelFolds`: precision ≥ 0.6, recall not gated; gated from 25 adjudicated items — pass
- `competence`: precision ≥ 0.6, recall not gated; gated from 30 adjudicated items — pass
- `courtLinks`: precision ≥ 0.6, recall not gated; gated from 30 adjudicated items — pass
- `crossReferences`: precision ≥ 0.6, recall not gated; gated from 30 adjudicated items — pass
- `deontic`: precision ≥ 0.6, recall not gated; gated from 30 adjudicated items — pass
- `eurovoc`: precision ≥ 0.6, recall not gated; gated from 30 adjudicated items — insufficient (not enough adjudicated items (4 < 30))
- `sanctions`: precision ≥ 0.6, recall not gated; gated from 30 adjudicated items — pass
- `targetGroup`: precision ≥ 0.6, recall not gated; gated from 30 adjudicated items — insufficient (not enough adjudicated items (3 < 30))
