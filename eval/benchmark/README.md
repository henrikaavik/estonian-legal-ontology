# Estonian legal-reasoning benchmark (#727)

An Estonian-language evaluation set with three task families. All of them are
derived deterministically from the committed corpus:

- **point_in_time** asks what a law's § said on a given date. The answer is the
  Riigi Teataja redaction in force on that date, and the other redactions are
  the distractors.
- **court_interpretation** asks which provisions a Riigikohus decision
  interprets. The answer is a set of provision IRIs, and only case metadata is
  used.
- **cross_reference** asks which provision a citation in a law points to. The
  answer is one provision IRI.

Full documentation is in [`docs/BENCHMARK.md`](../../docs/BENCHMARK.md). It covers
the task definitions, the measured counts, how to evaluate, the rights position
and the CLARIN-EE deposit checklist.

| Path | What |
|---|---|
| `sample/{train,dev,test}.jsonl` | Committed sample, 200 items per task |
| `sample/manifest.json` | Seed, caps, counts, drop reasons, full-build totals |
| `item.schema.json` | JSON Schema for one line |
| `rights_provenance.json` | Inputs, fields read and never read, rights layers, open items |
| `cmdi.xml` | CMDI metadata stub for CLARIN-EE, marked TO-VERIFY |

The full build is about 26 MB, so it is not committed. Rebuild it locally:

```bash
python3 scripts/build_legal_benchmark.py --out eval/benchmark/full
python3 scripts/build_legal_benchmark.py --sample-per-task 200 --out eval/benchmark/sample   # refresh the sample
```

**The rights position is a DRAFT.** Do not deposit or redistribute the set as
cleared until the open items in `docs/BENCHMARK.md` are closed.
