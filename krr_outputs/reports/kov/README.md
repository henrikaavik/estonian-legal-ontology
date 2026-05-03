# KOV pipeline coverage reports

Each KOV-relevant pipeline emits a per-run JSON report here at the end
of its `main()`. Reports include input file counts (total + KOV-only),
triples emitted, wall time, errors, and a capped sample of failures
for diagnostics.

## Format

```json
{
  "pipeline": "classify_eurovoc",
  "run_timestamp": "2026-05-03T12:00:00Z",
  "pipeline_version": "abc1234",
  "input_files_total": 15508,
  "input_files_kov": 11059,
  "files_processed": 15500,
  "files_processed_kov": 11055,
  "files_with_output": 14800,
  "files_with_output_kov": 10500,
  "files_skipped": 8,
  "skip_reasons": {"no_act_node": 6, "json_decode_error": 2},
  "triples_emitted": 42000,
  "triples_emitted_kov": 28000,
  "fallback_hits": 0,
  "unresolved_references": 0,
  "wall_time_seconds": 125.4,
  "items_per_second": 94.0,
  "peak_memory_mb": 312.5,
  "error_count": 0,
  "failure_samples": []
}
```

Field reference:

| Field | Meaning |
| --- | --- |
| `pipeline` | Script name (without `.py` and `scripts/` prefix). |
| `run_timestamp` | ISO-8601 UTC start time. |
| `pipeline_version` | Git short SHA at run time. |
| `input_files_total` | Total files iterated by `iter_peep_files()`. |
| `input_files_kov` | Subset under `regulations/kov/`. |
| `files_processed` | Files that classified/extracted successfully. |
| `files_processed_kov` | KOV subset of `files_processed`. |
| `files_with_output` | Files that produced ≥1 output triple. |
| `files_with_output_kov` | KOV subset of `files_with_output`. **The proof that KOV actually contributed; ≥1 ≪ files_processed_kov is fine, but 0 means KOV produced nothing.** |
| `files_skipped` | Files skipped, total. |
| `skip_reasons` | Skip-reason → count, one per category. |
| `triples_emitted` | Total output triples written back (laws + state-regs + KOV). |
| `triples_emitted_kov` | Subset attributed to KOV inputs. The KOV-output sanity check. |
| `fallback_hits` | Times a fallback path fired (slug-based XML resolution, etc.). |
| `unresolved_references` | References that couldn't pair (e.g. KOV peep with no XML). |
| `wall_time_seconds` | Total run wall time. |
| `items_per_second` | `files_processed / wall_time_seconds`. |
| `peak_memory_mb` | Peak RSS in MB (0.0 on Windows where `resource` is unavailable). |
| `error_count` | Number of exceptions captured. |
| `failure_samples` | Up to 20 failure messages for diagnosis. |

## Baseline-by-construction (the "before" state)

Pre-Layer-2a, none of these pipelines processed KOV files — `iter_peep_files()`
defaulted to `include_kov=False`, so the KOV-on-pipelines input universe
was empty by construction. The coverage helper itself didn't exist on
`main`, so a literal "checkout main and run the report" doesn't work
(no helper to invoke).

The "before" baseline is therefore implicit:

| Field | Pre-2a value (by construction) |
| --- | --- |
| `input_files_kov` | 0 |
| triples emitted from KOV files | 0 |

The "after" values come from this PR's coverage reports, committed at
`krr_outputs/reports/kov/<pipeline>_coverage.json`.

## Reviewer verification

Verify the absolute values are reasonable, not a literal diff:

1. `input_files_kov ≈ 11059` for each of the 5 fixed pipelines (full
   KOV corpus participated).
2. `input_files_total ≈ 15508` (current corpus size with KOV enabled).
3. **`files_with_output_kov > 0`** — the actual KOV-contributed-output
   check. Total `triples_emitted > 0` is not enough because laws/state
   regs alone could account for it while every KOV file is skipped.
   Likewise check `triples_emitted_kov > 0`.
4. `error_count` — ideally 0; small positive numbers OK if `failure_samples`
   shows the failures are KOV-specific edge cases rather than systemic.
5. `items_per_second` — sanity: classify_deontic ≫ extract_temporal_data
   in throughput because deontic is text-only and temporal does XML parsing.

If the implementer wants a true before/after diff later (e.g., when 2b
runs over a corpus that already has 2a's output), they can copy the 2a
report aside before re-running and diff manually. That is not a 2a
deliverable; 2a establishes the baseline.
