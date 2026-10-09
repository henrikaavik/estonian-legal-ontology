# Fitness-for-purpose and accuracy evaluation (#617, #698)

"Done" in this repo used to mean green CI. This directory measures whether the
data is fit for purpose. It answers two questions. Coverage asks whether a layer
is present and retrievable. Accuracy asks how often a heuristic layer is wrong.

## What's here

- **`FITNESS_REPORT.md` / `fitness_report.json`**: the committed report,
  generated from numbers by `scripts/eval_harness.py`. It has a coverage part
  (per-vertical coverage, vertical co-occurrence, cross-reference edge
  resolution, point-in-time, inline sanctions) and an accuracy part
  (precision, recall and F1 per layer from the gold sets).
- **`gold_sets/`**: one legal gold set per heuristic layer, all following
  `gold_sets/item.schema.json`. These are built by `scripts/build_gold_sets.py`
  and adjudicated by a legal reviewer.
- **`accuracy_floors.json`**: the per-layer precision and recall floors that
  CI enforces.
- **`benchmark/`**: the Estonian legal-reasoning benchmark for LLMs (#727). See
  [`docs/BENCHMARK.md`](../docs/BENCHMARK.md).

## Commands

```bash
python3 scripts/eval_harness.py --report eval/FITNESS_REPORT.md          # regenerate the report
python3 scripts/eval_harness.py --report eval/FITNESS_REPORT.md --check  # exit 1 if the committed report is stale
python3 scripts/eval_harness.py --gold-set eval/gold_sets \
    --floors eval/accuracy_floors.json --current-corpus --gate           # the CI gate (offline)
python3 scripts/build_gold_sets.py --krr-dir krr_outputs --corpus-commit "$(git rev-parse HEAD)"
```

The report's `generated` stamp is pinned to `estleg_common.BUILD_EVALUATION_DATE`
so that regenerating it does not churn on the wall clock (#295). The report
names the corpus by a fingerprint of `krr_outputs/INDEX.json` and the gold sets
by the commit they were sampled at. Regenerate the report after any change to
the corpus or to a gold set.

## The gold sets

Each gold set has about 300 items: crossReferences, sanctions, eurovoc, deontic,
targetGroup, competence and courtLinks. `altLabelFolds` has 50 items. It covers
the #699 orthographic `skos:altLabel` folds, which replaced the removed
Levenshtein `skos:closeMatch` pairs. Items are sampled deterministically. The
seed and corpus commit are stored in each file under `sampling`. Sampling is
stratified by act kind (law, state regulation, municipal regulation) and by
predicate value. About 20 % of items are deliberate **negatives**: a node where
the system asserts nothing. Provisions with no Riigi Teataja URL cannot be cited,
so they are excluded and counted under `sampling.excluded_without_citation`.

Every item carries the following fields:

- the node IRI, the predicate, and the system value(s), with readable labels;
- a citation URL to Riigi Teataja, or to Riigikohus for court links;
- the quoted evidence span, so the reviewer can judge without opening the
  source;
- `gold` (empty until adjudicated), `verdict`, `verdict_source`, `reviewer`,
  `adjudicated_on` and `note`.

Court-link negatives also carry a `probe`, the decision whose link is absent.
Fold negatives carry the near-identical concept that was not folded.

### Mechanical pre-fills

A verdict is pre-filled only when a rule decides the item from the evidence
alone. Examples: the source text says "käesoleva seaduse § 2 lõike 1" and the
edge points at § 2 lõige 1; the sanction amount and unit appear in the
provision text; the provision text is only "Kehtetu". Each file lists its rules
under `mechanical_rules`, and each pre-filled item names its rule and has
`verdict_source: "mechanical"`. Mechanical rules only confirm easy cases. The
pre-filled precision is therefore an upper bound, not the layer's accuracy.
A reviewer may overturn a mechanical verdict.

## How to adjudicate

1. Open a gold file and work through the items whose `verdict` is `"pending"`.
   Read `evidence.text`, and follow `citation` only when the span is not enough.
2. Set `verdict`:
   - `correct`: the system value is right. For a negative it means there is
     indeed nothing to assert. Leave `gold` empty, or set it equal to `system`.
   - `incorrect`: the system value is wrong. Put the right value(s) in `gold`,
     or leave it empty when nothing is right. For a negative, `incorrect` means
     a value is missing; name it in `gold`.
   - `partial`: some values are right and some are wrong or missing. `gold` is
     required.
3. Set `verdict_source: "reviewer"`, `reviewer` (your name),
   `adjudicated_on` (YYYY-MM-DD), and a `note` when the call is not obvious.
4. Run the gate command above. It validates the schema and the consistency
   rules, for example that a reviewer verdict names the reviewer.
5. Regenerate `FITNESS_REPORT.md` and commit both files.

**Who signs.** Reviewer verdicts are a legal judgement. They are accepted from
a reviewer with Estonian legal training and are signed by name in `reviewer`.
The `needs-legal-review` paths in [CODEOWNERS](../.github/CODEOWNERS) apply to
`eval/`. Never record an unverified label as gold.

**Re-sampling keeps adjudications.** Re-running `scripts/build_gold_sets.py`
against a newer corpus keeps every reviewer verdict whose item id is still
sampled. The id is a hash of the layer, node, predicate, system values and
probe. A reviewer verdict is carried forward only when the evidence, context
and citations are unchanged too. Changed evidence requires a fresh verdict.

## Scoring and the gate

The harness scores only adjudicated items and reports pending items separately.
Scoring is value-level:

- For a positive item, TP = |system ∩ gold|, FP = |system − gold| and
  FN = |gold − system|. A `correct` verdict with empty gold means gold equals
  system.
- A confirmed negative is a true negative.
- A negative marked `incorrect` counts at least one false negative.

The report also gives the 95 % Wilson lower bound of precision.

By default scores describe the saved sampling snapshot. CI uses
`--current-corpus` to read today's assertions for the same fixed probes.
Positive edge samples test their reviewed values; whole-node negatives test
all current values, and paired negatives test their named probe. A removed
correct assertion counts as a recall miss and a new assertion on a confirmed
negative counts as a false positive. Missing sampled subjects and changed
sanction amounts fail validation and require renewed adjudication.

`--gate` exits 1 in three cases:

- a gold file is invalid;
- a floor names a layer that has no gold set;
- a layer with at least `min_adjudicated` adjudicated items has precision or
  recall below its floor, or the required metric is unmeasurable.

The same gate applies with a single schema-versioned gold file, `--report`,
and `--report --check`; report generation does not bypass failing floors.

A layer with fewer adjudicated items is reported as "not enough adjudicated
items" and passes. A null floor is not gated.

**How floors rise.** The initial floors (2026-10-09) come from the mechanical
pre-fills only. Each precision floor is min(0.60, the 95 % Wilson lower bound
rounded down to 0.05). Recall floors are null because the mechanical subset
contains no recall misses by construction. After each signed-off adjudication
round, each floor becomes max(current floor, the new Wilson lower bound rounded
down to 0.05). Recall floors start once reviewer-adjudicated negatives exist.
`min_adjudicated` rises towards 100. Floors only rise. Record each round
below.

## Accuracy status

Cycle 2 (#576/#577) measured several heuristic layers 31–35 % wrong, but the
project never instrumented that figure. The current numbers are in the accuracy
block of [FITNESS_REPORT.md](FITNESS_REPORT.md). As of 2026-10-09 every
non-pending verdict is mechanical. No reviewer has signed off yet, so
**reviewer-measured accuracy is pending adjudication for every layer**. The two
layers with almost no mechanical coverage are targetGroup and eurovoc. Their
precision is unmeasured until a reviewer adjudicates them.

| Round | Date | Reviewer | Layers | Floors after |
| --- | --- | --- | --- | --- |
| 0 (mechanical pre-fill) | 2026-10-09 | none | all | precision 0.60, recall not gated |

## Coverage is not accuracy

The coverage metrics answer whether the data is there and retrievable. The
"100 % cross-reference edge resolution" figure measures reference integrity:
edges are checked against node ids collected from the same files. It does not
measure semantic precision or extraction recall. Precision and recall come only
from the gold sets.
