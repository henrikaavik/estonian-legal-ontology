# Test tiers and guards

`tests/conftest.py` defines five markers. A test without a tier marker is
`unit` automatically.

| Marker      | What it may touch                                      | Default run | How to select |
|-------------|--------------------------------------------------------|-------------|---------------|
| `unit`      | `tmp_path`, `tests/fixtures/`, no network              | yes         | `-m unit` |
| `committed` | committed, non-LFS data in the checkout (read only)    | yes         | `-m committed` |
| `corpus`    | Git LFS artifacts and whole-corpus invariants (read only) | **no**   | `-m corpus` (after `git lfs pull`) |
| `live`      | the network (Riigi Teataja)                            | skipped     | `ESTLEG_LIVE_CANARY=1 pytest -m live` |
| `slow`      | orthogonal; long corpus-iterating checks               | yes         | `-m "not slow"` to skip |

`pyproject.toml` sets `addopts = ["-m", "not corpus"]`. Any `-m` on the
command line replaces it, so `pytest -m corpus` runs only the corpus gates
and `pytest -m "corpus or not corpus"` runs everything.

```bash
python3 -m pytest -q -n auto                     # default: unit + committed (+ slow)
python3 -m pytest -q -m corpus                   # LFS / invariant gates
ESTLEG_LIVE_CANARY=1 python3 -m pytest -q -m live  # network canaries
```

## Guards

- **No network.** Every test except `live` runs with sockets disabled
  (pytest-socket). Unix-domain sockets stay allowed.
- **Timeouts.** pytest-timeout gives each test 300 s. `corpus` and `slow`
  tests get 1800 s unless they set their own `@pytest.mark.timeout`.
- **Never write the real corpus.** The session fails if `krr_outputs/`
  gained uncommitted changes while it ran. The check compares the end of
  the session with its start, so a tree that was already dirty is fine.
  Set `ESTLEG_ALLOW_KRR_WRITES=1` only for intentional data work, or while
  another process is rewriting the corpus at the same time.

The conftest refuses to start without pytest-socket and pytest-timeout.
Install the dev extras with `python3 -m pip install -e ".[dev]"`.

## Fixtures

- **`isolated_krr`** stages an empty `krr_outputs` under `tmp_path`.
  `isolated_krr.bind(module)` rebinds the module's `KRR_DIR` and every other
  `*_DIR` path under it. `isolated_krr.write_json(rel, data)` stages input.
  Use it for any test that runs a writing script or migration.
- **`corpus_krr`** reads the real `krr_outputs`. `corpus_krr.path(rel)` and
  `corpus_krr.read_json(rel)` check the input first. A missing file, an
  empty directory or an un-pulled LFS pointer **fails** a `corpus` test and
  **skips** any other test with the reason. Tests that use it without
  `corpus` are tagged `committed`.

## Archived scripts and examples

`scripts/archive/` and `examples/` are not on the pytest `pythonpath`. Load
them by path:

```python
from tests._script_loader import load_script

fdi = load_script("scripts/archive/fix_duplicate_ids.py")
```

## Source-XML to peep goldens

`tests/fixtures/golden/` holds nine Riigi Teataja acts. Each one has its
source XML (`<id>.xml.gz`) and the expected generator output
(`<id>.peep.json`). `manifest.json` pins every generator input.
`tests/test_golden_peeps.py` regenerates each peep offline and diffs it
against the golden. Only keys in `VOLATILE_KEYS` and
`ENRICHMENT_ONLY_PREDICATES` are dropped before the diff.

```bash
# After an intended generator change: rewrite the golden peeps (offline).
.venv/bin/python -m tests.test_golden_peeps regen
# Re-download the source XML through riigiteataja_common.fetch_xml, then regen.
.venv/bin/python -m tests.test_golden_peeps refetch [--only <id> ...]
```

Review the golden diff like code: every changed line is a change in what
the generator emits for real legal text.
