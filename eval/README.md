# Fitness-for-purpose evaluation (#617)

"Done" in this repo was *green CI*, not *fit for purpose*. The heuristic layers
are openly approximate, but nothing measured whether a real legal question
returns a complete, correct answer. This directory holds that measurement.

## What's here

- **`fitness_report.json` / `FITNESS_REPORT.md`** — the committed baseline,
  produced by `scripts/eval_harness.py`. **Objective, reproducible** metrics over
  the indexed root-law corpus (no hand-adjudication, so they can't drift on
  opinion): per-vertical coverage, the **vertical co-occurrence** "complete law"
  question + the **retrievability gap**, cross-reference edge resolution,
  point-in-time, and inline-sanction completeness.
- **`gold_sets/`** — hand-verified ground truth for the *accuracy* dimension
  (precision/recall per heuristic layer). Ships templated; see below.

## Refreshing the baseline

```bash
python3 scripts/eval_harness.py          # rewrites fitness_report.json + FITNESS_REPORT.md
```

The report's `generated` stamp is pinned to `estleg_common.BUILD_EVALUATION_DATE`
(deterministic, no wall-clock churn — issue #295). Regenerate after any change
that alters the corpus, and commit the diff so the baseline trends over time.

This is a **report, not a gate** — it never fails on a low number, and it is
**not wired into the blocking CI gates** (the user's guidance on #617: keep the
fast golden-fact tests blocking, keep the broad vertical evaluation non-blocking
until a baseline is accepted). Run it on demand.

## Accuracy (precision/recall) — the gold-set path

The objective metrics above answer "is the data *there* and *retrievable*". They
do **not** answer "is the heuristic *right*" — Cycle 2 measured several layers
31–35 % wrong (#576/#577) but the project never instrumented it. That needs a
**hand-verified gold set**, which is a legal-judgement artifact and is accepted
separately (non-blocking until a baseline is agreed).

A gold set is a JSON file:

```json
{
  "layer": "targetGroup",
  "property": "estleg:targetGroup",
  "source_sample": "seed(7), 150 provisions — see issue #576",
  "items": [
    { "node": "estleg:KARIST_2_Osa2_Par_121", "gold": ["citizen"], "note": "addressee is the offender" }
  ]
}
```

Run it through the harness:

```bash
python3 scripts/eval_harness.py --gold-set eval/gold_sets/targetGroup.json
```

The harness looks up each `node`'s predicted value in the corpus, scores
exact-match, and adds an `accuracy` block (precision/recall) to the report.
Gold items must cite their adjudication source (the Riigi Teataja text, or the
adjudicated sample in #576/#577) — we do not bless an unverified label as truth.
The `needs-legal-review` paths in [CODEOWNERS](../.github/CODEOWNERS) apply here.
