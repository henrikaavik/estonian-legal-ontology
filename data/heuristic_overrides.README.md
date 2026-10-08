# heuristic_overrides.jsonl

Durable human overrides for the heuristic classifiers (#700). The file is
JSON Lines: one override object per line and no comments. An empty file means
no overrides.

See [docs/HEURISTIC_OVERRIDES.md](../docs/HEURISTIC_OVERRIDES.md) for the
record format, the review workflow, and how regeneration honours each line.
Validate before committing:

```bash
python3 scripts/heuristic_overrides.py validate
```
