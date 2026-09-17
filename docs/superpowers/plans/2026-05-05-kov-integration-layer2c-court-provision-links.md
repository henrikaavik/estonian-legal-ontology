# KOV Integration Layer 2c — Court-Provision Links Implementation Plan (v4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **v4 deltas (third-pass review):** Fixed seven findings — early-return tuple now matches the 7-value contract (Task 10); legacy report `"generated"` field removed for idempotency (Task 11 + Task 13 simplified); validation commands use fail-fast `|| { tail; exit 1; }` (Task 17 step 3); Task 11's full-suite count corrected to 250; PR body separates JSON hygiene (`validate_all.py`) from real SHACL validation (Task 20); CHANGELOG/PR wording for `interpretedBy` reworded to "subject usage" not "range" (Task 19 + Task 20); `git add` of generated outputs narrowed to a precise file list derived from `git diff --name-only` (Task 18).
>
> **v3 deltas (second-pass review):** Generated-output commit task added; SHACL validation actually validates court decisions; smoke-tests use `pipefail`; test counts corrected to 255; destructive `rm -rf` replaced with backup-then-symlink; raw-vs-unique KOV citation counters separated; `interpretedBy` SHACL description reworded for subject-vs-object semantics.
>
> **v2 deltas (first-pass review):** Schema regeneration narrowed to `generate_schema_nodes()`; `_RunCounters` threaded through `build_provision_index`; `import json` added to test file; SHACL test fixture gains required `caseNumber`/`caseType`; SHACL test gains direct shapes-graph assertion; `kov_link_count` deduplicates before counting; SCHEMA_REFERENCE update broadened.

**Goal:** Add an act-level KOV citation resolver to `extract_court_provision_links.py`, widen `interpretsLaw`/`interpretedBy` to admit `MunicipalRegulation` (Act) IRIs alongside `LegalProvision` IRIs, and emit the per-pipeline coverage report mandated by Gate B.

**Architecture:** A titlecase-anchored regex (`PAT_KOV_ACT`) extracts `{Municipality} {Body} {date} määrus nr {N}` citations from Riigikohus court summaries. A separate index (`build_kov_act_index`) keyed on `(issuer_norm, entryIntoForce-year, actNumber)` resolves matches to `MunicipalRegulation` act IRIs. A new `resolve_kov_citation` checks a `known_issuer_norms` precondition, then primary-year, then `+1` alternate, with `kov_collision_keys` short-circuiting on either step. Resolved IRIs merge with state-law provision IRIs into a single deduplicated `interpretsLaw` array. The `_RunCounters` helper threaded through every read site guarantees that malformed inputs increment four bookkeeping counters atomically.

**Tech Stack:** Python 3.11+, pytest, JSON-LD, SHACL (pyshacl, rdflib). Builds on Layer 1's Issuer registry, Layer 2a's `kov_pipeline_coverage` helper, Layer 2c PR #1's per-file idempotency convention, Layer 2c PR #2's coverage-report shape.

**Spec reference:** `docs/superpowers/specs/2026-05-05-kov-integration-layer2c-court-provision-links-design.md`.
**Predecessors:** Layer 2a (#98), Layer 2b (#99), Layer 2c PR #1 sanctions (#100), Layer 2c PR #2 institutional-competence (#101) — all merged.
**Closes:** Gate B umbrella.

---

## Crisp invariants

After this PR lands:

1. `extract_court_provision_links.py` builds two parallel indexes: an unchanged state-law provision index (still via `iter_peep_files(include_kov=False)` at lines 53 and 365) and a new KOV act index (via `iter_peep_files()` filtered to `MunicipalRegulation` nodes).
2. The two `iter_peep_files(include_kov=False)` callsites stay pinned. State-law index correctness depends on KOV peeps being excluded.
3. `build_kov_act_index()` returns `(kov_index, kov_collision_keys, kov_iri_to_file, known_issuer_norms)`. Never silent-overwrites; collisions removed from `kov_index` and added to `kov_collision_keys`.
4. `resolve_kov_citation()` checks `known_issuer_norms` first (`unknown_issuer` short-circuit), then `kov_collision_keys` before primary lookup, then `kov_collision_keys` before `+1` alternate. Ambiguous on alternate also returns `ambiguous_key`.
5. `estleg:interpretsLaw` arrays may now contain `LegalProvision` IRIs (state law) and/or `MunicipalRegulation` Act IRIs (KOV). Same JSON-LD shape per entry.
6. KOV act files receive `estleg:interpretedBy` triples for any act IRI a court decision resolves to. Stale `interpretedBy` cleanup walks ALL KOV peeps each run.
7. SHACL `estonian_legal_shapes.ttl`: `interpretedBy` and `interpretsLaw` descriptions widened (triple-quoted); a new `interpretedBy` property shape attached to `MunicipalRegulationShape` (same `sh:nodeKind sh:IRI`, no `sh:class`, no `sh:or`).
8. Formal RDFS range on `interpretsLaw` widened to `owl:unionOf (LegalProvision Act)` in `scripts/generate_court_decisions.py` and the regenerated `krr_outputs/riigikohus/riigikohus_schema.json`.
9. Coverage report `krr_outputs/reports/kov/court_provision_links_coverage.json` exists with stable shape (`skip_reasons` keys pre-seeded to zero); `triples_emitted_kov ≥ 1` on real data.
10. Re-runs are idempotent: two consecutive runs produce byte-identical peep files and a coverage report identical except for `run_timestamp`, `wall_time_seconds`, `items_per_second`, `peak_memory_mb`.

**Empirical baseline (2026-05-05):** 35 matches, 5 resolved, 29 unresolved, 1 ambiguous, 0 RT IV.

**Not in this PR:** RT IV reference resolver (counted only); KOV provision-level resolution; adoption-date extraction from XML; defunct/historical Issuer backfill; `rdfs:range` on `estleg:interpretedBy`.

---

## Worktree setup (do this first)

From the main checkout `/Users/henrikaavik/progemoge/law-ontology`:

```bash
git worktree add .worktrees/kov-layer2c-court-links -b feature/kov-layer2c-court-links
cd .worktrees/kov-layer2c-court-links
```

Apply the guarded XML symlink restore (Layer 2c convention):

```bash
TARGET=/Users/henrikaavik/progemoge/law-ontology/data/riigiteataja
if [ -L data/riigiteataja ]; then
    current=$(readlink data/riigiteataja)
    if [ "$current" != "$TARGET" ]; then
        unlink data/riigiteataja
        ln -s "$TARGET" data/riigiteataja
    fi
elif [ -d data/riigiteataja ]; then
    extras=$(ls -A data/riigiteataja 2>/dev/null | grep -v "^karistusseadustik\.xml$" || true)
    if [ -z "$extras" ]; then
        # Recoverable: rename the directory to a timestamped backup before
        # creating the symlink. The backup lives inside the worktree so it
        # vanishes on `git worktree remove`, but if you need to recover
        # before then it's a `mv` away.
        BACKUP="data/riigiteataja.backup-$(date +%Y%m%d%H%M%S)"
        mv data/riigiteataja "$BACKUP"
        echo "  Backed up empty placeholder to $BACKUP (only had karistusseadustik.xml)"
        ln -s "$TARGET" data/riigiteataja
    else
        echo "REFUSING — unexpected content in data/riigiteataja:"
        ls -la data/riigiteataja | head -10
        exit 1
    fi
else
    ln -s "$TARGET" data/riigiteataja
fi
```

Confirm baseline tests pass:

```bash
pytest -q
```

Expected: `206 passed` (Layer 2c PR #2 end-state, post-merge of #101).

---

## File Structure

| File | Disposition | Purpose |
|------|-------------|---------|
| `scripts/estleg_common.py` | modify | Add `BODY_CANON` mapping, `normalize_issuer_name()`, `_RunCounters` dataclass, `_safe_load()` helper. |
| `scripts/extract_court_provision_links.py` | modify | New regex `PAT_KOV_ACT` + `PAT_RTIV`; new helpers `expand_two_digit_year`, `build_kov_act_index`, `resolve_kov_citation`; widened `extract_citations_from_text` returning a tuple; `process_court_files` merges KOV resolutions; `clear_existing_court_links` gains walk #3 over KOV peeps; `main()` integrates `_RunCounters` and emits `CoverageReport`. |
| `scripts/generate_court_decisions.py` | modify | Widen `rdfs:range` on `estleg:interpretsLaw` to `owl:unionOf (LegalProvision Act)`. |
| `krr_outputs/riigikohus/riigikohus_schema.json` | regenerate | Output of running the updated generator. |
| `shacl/estonian_legal_shapes.ttl` | modify | Update `interpretedBy` description on `LegalProvisionShape`; update `interpretsLaw` description on `CourtDecisionShape`; add new `interpretedBy` property shape on `MunicipalRegulationShape`. |
| `tests/test_extract_court_provision_links.py` | create | Four test groups: regex, resolver, end-to-end, idempotency + SHACL. |
| `tests/fixtures/kov_court_provision_links/` | create | Riigikohus / state-law / KOV fixture peep files. |
| `CHANGELOG.md` | modify | One entry under Unreleased section: KOV integration Layer 2c PR #3 — Court-Provision Links (Gate B closed). |
| `docs/SCHEMA_REFERENCE.md` | modify | Note the widened `interpretsLaw` range. |

---

## Task 1 — `BODY_CANON` and `normalize_issuer_name` in `estleg_common.py`

**Files:**
- Modify: `scripts/estleg_common.py` (insert after the `KNOWN_ABBREVIATIONS` / `FULLNAME_GENITIVE` / `CONTEXT` blocks; before `iter_peep_files`)
- Test: `tests/test_estleg_common_normalize.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_estleg_common_normalize.py`:

```python
"""Tests for normalize_issuer_name and BODY_CANON helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import BODY_CANON, normalize_issuer_name


def test_normalize_issuer_lowercase() -> None:
    assert normalize_issuer_name("Põlva Vallavalitsus") == "polva vallavalitsus"


def test_normalize_issuer_uppercase() -> None:
    assert normalize_issuer_name("PÕLVA VALLAVALITSUS") == "polva vallavalitsus"


def test_normalize_issuer_diacritic_transliteration() -> None:
    # Õ→o, Ä→a, Ö→o, Ü→u, Š→s, Ž→z
    assert normalize_issuer_name("Tõrva Linnavolikogu") == "torva linnavolikogu"
    assert normalize_issuer_name("Häädemeeste Vallavolikogu") == "haademeeste vallavolikogu"


def test_normalize_issuer_collapses_whitespace() -> None:
    assert normalize_issuer_name("  Tartu   Linnavolikogu  ") == "tartu linnavolikogu"


def test_body_canon_genitive_to_nominative() -> None:
    assert BODY_CANON["linnavalitsuse"] == "linnavalitsus"
    assert BODY_CANON["vallavalitsuse"] == "vallavalitsus"
    assert BODY_CANON["linnavalitsus"] == "linnavalitsus"
    assert BODY_CANON["vallavalitsus"] == "vallavalitsus"


def test_body_canon_volikogu_passthrough() -> None:
    # Volikogu forms are identical in nominative and the genitive used in court text.
    assert BODY_CANON["linnavolikogu"] == "linnavolikogu"
    assert BODY_CANON["vallavolikogu"] == "vallavolikogu"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_estleg_common_normalize.py -v
```

Expected: `ImportError: cannot import name 'BODY_CANON' from 'estleg_common'` (or similar).

- [ ] **Step 3: Implement `BODY_CANON` and `normalize_issuer_name`**

In `scripts/estleg_common.py`, find the section header `# Well-known abbreviation -> full law name mappings` (line ~17). After the closing of `CONTEXT` (around line 197) and before the comment block above `save_json`, add:

```python
# ---------------------------------------------------------------------------
# KOV body-word canonicalization and issuer-name normalization (Layer 2c PR #3)
# ---------------------------------------------------------------------------
# Court summaries cite KOV bodies using genitive forms (Linnavalitsuse,
# Vallavalitsuse); KOV peep estleg:issuer fields use nominative (Linnavalitsus,
# Vallavalitsus). The volikogu forms are identical in both cases. BODY_CANON
# normalizes either side's body word to the nominative form so issuer-string
# compare works after normalize_issuer_name().
BODY_CANON: dict[str, str] = {
    "linnavalitsuse": "linnavalitsus",
    "vallavalitsuse": "vallavalitsus",
    "linnavalitsus":  "linnavalitsus",
    "vallavalitsus":  "vallavalitsus",
    "linnavolikogu":  "linnavolikogu",
    "vallavolikogu":  "vallavolikogu",
}


def normalize_issuer_name(s: str) -> str:
    """Lowercase + Estonian diacritic transliteration + whitespace collapse.

    Same approach as Layer 1 ``titleNormalized``. Used at both index-build
    time (over KOV peep ``estleg:issuer`` strings) and citation-resolve
    time (over reconstructed display name from a regex match).
    """
    s = s.lower()
    for src, dst in (
        ("õ", "o"), ("ä", "a"), ("ö", "o"),
        ("ü", "u"), ("š", "s"), ("ž", "z"),
    ):
        s = s.replace(src, dst)
    return " ".join(s.split())
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_estleg_common_normalize.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/estleg_common.py tests/test_estleg_common_normalize.py
git commit -m "Add BODY_CANON and normalize_issuer_name helpers for KOV citation resolution"
```

---

## Task 2 — `_RunCounters` and `_safe_load` in `estleg_common.py`

**Files:**
- Modify: `scripts/estleg_common.py` (add after the BODY_CANON section)
- Test: `tests/test_estleg_common_run_counters.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_estleg_common_run_counters.py`:

```python
"""Tests for _RunCounters and _safe_load in estleg_common."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import _RunCounters, _safe_load


def test_run_counters_defaults() -> None:
    c = _RunCounters()
    assert c.file_skips == 0
    assert c.error_count == 0
    assert c.skip_reasons == {}
    assert c.failures == []
    assert c.seen_error_paths == set()


def test_run_counters_bump_skip() -> None:
    c = _RunCounters()
    c.bump_skip("json_decode_error", "json_decode_error | foo.json | ValueError: bad")
    assert c.file_skips == 1
    assert c.error_count == 1
    assert c.skip_reasons["json_decode_error"] == 1
    assert c.failures == ["json_decode_error | foo.json | ValueError: bad"]


def test_run_counters_failures_capped_at_200_in_memory() -> None:
    c = _RunCounters()
    for i in range(250):
        c.bump_skip("x", f"sample_{i}")
    assert c.skip_reasons["x"] == 250    # counter increments every call
    assert len(c.failures) == 200         # in-memory cap before final 20-cap


def test_safe_load_success(tmp_path: Path) -> None:
    p = tmp_path / "good.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    c = _RunCounters()
    doc = _safe_load(p, c)
    assert doc == {"a": 1}
    assert c.file_skips == 0


def test_safe_load_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = _RunCounters()
    doc = _safe_load(p, c)
    assert doc is None
    assert c.file_skips == 1
    assert c.error_count == 1
    assert c.skip_reasons["json_decode_error"] == 1
    assert len(c.failures) == 1
    assert "bad.json" in c.failures[0]


def test_safe_load_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    c = _RunCounters()
    doc = _safe_load(p, c)
    assert doc is None
    assert c.file_skips == 1


def test_safe_load_dedup_same_path(tmp_path: Path) -> None:
    """Same malformed file revisited by multiple walks counts ONCE."""
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    c = _RunCounters()
    _safe_load(p, c)
    _safe_load(p, c)   # second walk
    _safe_load(p, c)   # third walk
    assert c.file_skips == 1
    assert c.skip_reasons["json_decode_error"] == 1
    assert len(c.failures) == 1
    assert p in c.seen_error_paths
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_estleg_common_run_counters.py -v
```

Expected: `ImportError: cannot import name '_RunCounters'`.

- [ ] **Step 3: Implement `_RunCounters` and `_safe_load`**

Add to `scripts/estleg_common.py` (after the `normalize_issuer_name` function, before `save_json`):

```python
# ---------------------------------------------------------------------------
# Run-counters helper (Layer 2c PR #3)
# ---------------------------------------------------------------------------
# Pipelines that read many JSON files and need to record malformed inputs
# under coverage-report fields share this accumulator so failure
# bookkeeping (files_skipped + skip_reasons + error_count + failure_samples)
# stays atomic. _safe_load wraps json.load with the four bumps; callers
# treat None as "skip this file."
from dataclasses import dataclass, field


@dataclass
class _RunCounters:
    """Mutable accumulator threaded through every read site of a pipeline.

    De-dup set ``seen_error_paths`` ensures the same malformed file
    encountered by multiple walks (cleanup, index-build, processing)
    counts once toward ``file_skips`` rather than once per walk.
    """

    file_skips: int = 0
    error_count: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    seen_error_paths: set[Path] = field(default_factory=set)

    def bump_skip(self, reason: str, sample: str) -> None:
        self.file_skips += 1
        self.error_count += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        if len(self.failures) < 200:   # over-cap; helper writer enforces 20 final
            self.failures.append(sample)


def _safe_load(path: Path, counters: _RunCounters) -> dict | None:
    """Load JSON from ``path``; return ``None`` and bump counters on failure.

    Same path counted only once across the whole run via
    ``counters.seen_error_paths``.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        if path not in counters.seen_error_paths:
            counters.seen_error_paths.add(path)
            counters.bump_skip(
                "json_decode_error",
                f"json_decode_error | {path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}",
            )
        return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_estleg_common_run_counters.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/estleg_common.py tests/test_estleg_common_run_counters.py
git commit -m "Add _RunCounters and _safe_load helpers for atomic skip bookkeeping"
```

---

## Task 3 — `expand_two_digit_year` helper in extractor

**Files:**
- Modify: `scripts/extract_court_provision_links.py` (add helper near top, after imports)
- Test: `tests/test_extract_court_provision_links.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract_court_provision_links.py`:

```python
"""Tests for extract_court_provision_links.py — Layer 2c PR #3."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from extract_court_provision_links import expand_two_digit_year


# ---------------------------------------------------------------------------
# Group 1a — expand_two_digit_year
# ---------------------------------------------------------------------------

def test_year_99_to_1999() -> None:
    assert expand_two_digit_year("21.06.99") == 1999


def test_year_51_to_1951() -> None:
    assert expand_two_digit_year("01.01.51") == 1951


def test_year_50_to_2050_boundary() -> None:
    # y > 50 means y == 50 falls into the 20xx bucket
    assert expand_two_digit_year("01.01.50") == 2050


def test_year_25_to_2025() -> None:
    assert expand_two_digit_year("13.10.25") == 2025


def test_year_00_to_2000() -> None:
    assert expand_two_digit_year("13.10.00") == 2000


def test_year_four_digit_passthrough() -> None:
    assert expand_two_digit_year("13.10.2009") == 2009


def test_year_long_form_estonian_month() -> None:
    assert expand_two_digit_year("18. juuni 2020") == 2020
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_extract_court_provision_links.py -v
```

Expected: `ImportError: cannot import name 'expand_two_digit_year'`.

- [ ] **Step 3: Implement `expand_two_digit_year`**

In `scripts/extract_court_provision_links.py`, after the `NS = "..."` line (around line 35) and before `def build_provision_index`, add:

```python
def expand_two_digit_year(date_str: str) -> int:
    """Resolve the year component of a citation date to a 4-digit year.

    Two-digit years use the boundary ``y > 50``: ``00..50`` → ``2000..2050``,
    ``51..99`` → ``1951..1999``. Long-form Estonian-month dates already
    carry a 4-digit year; numeric ``d.m.y`` dates may be 2- or 4-digit.
    """
    parts = date_str.split()
    last_token = parts[-1] if parts else ""
    if last_token.isdigit() and len(last_token) == 4:
        return int(last_token)
    # numeric d.m.y form
    pieces = date_str.split(".")
    if len(pieces) >= 3:
        tail = pieces[2].split()[0]   # strip optional trailing ". a." etc.
        digits = "".join(ch for ch in tail if ch.isdigit())
        if not digits:
            return 0
        y = int(digits)
        if y < 100:
            return 1900 + y if y > 50 else 2000 + y
        return y
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_extract_court_provision_links.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_court_provision_links.py tests/test_extract_court_provision_links.py
git commit -m "Add expand_two_digit_year helper for KOV citation date parsing"
```

---

## Task 4 — `PAT_KOV_ACT` regex with case-sensitive municipality

**Files:**
- Modify: `scripts/extract_court_provision_links.py` (add `PAT_KOV_ACT` near top)
- Test: `tests/test_extract_court_provision_links.py` (extend Group 1)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_extract_court_provision_links.py`:

```python
from extract_court_provision_links import PAT_KOV_ACT


# ---------------------------------------------------------------------------
# Group 1b — PAT_KOV_ACT regex
# ---------------------------------------------------------------------------

def _first_match(text: str):
    return PAT_KOV_ACT.search(text)


def test_pat_numeric_date_genitive_body() -> None:
    m = _first_match("Keila Vallavolikogu 21.06.99 määrus nr 55")
    assert m is not None
    assert m.group("municipality").strip() == "Keila"
    assert m.group("body").lower() == "vallavolikogu"
    assert m.group("date") == "21.06.99"
    assert m.group("num") == "55"


def test_pat_long_date_form() -> None:
    m = _first_match("Tallinna Linnavolikogu 18. juuni 2020. a määruse nr 15")
    assert m is not None
    assert m.group("municipality").strip() == "Tallinna"
    assert m.group("body").lower() == "linnavolikogu"
    assert m.group("date") == "18. juuni 2020"
    assert m.group("num") == "15"


def test_pat_instrumental() -> None:
    m = _first_match("Tallinna Linnavalitsuse 14.05.2009 määrusega nr 23")
    assert m is not None
    assert m.group("num") == "23"


def test_pat_nominative_body() -> None:
    m = _first_match("Tallinna Linnavalitsus 14.05.2009 määrus nr 23")
    assert m is not None
    assert m.group("body").lower() == "linnavalitsus"


def test_pat_no_match_vague_plural() -> None:
    assert _first_match("Pärnu Linnavalitsuse määruste peale") is None


def test_pat_no_match_case_name() -> None:
    # "Tallinna Linna määruskaebus" is a case-name pattern, not an act citation.
    assert _first_match("Tallinna Linna määruskaebus Tallinna Ringkonnakohtu otsuse peale") is None


def test_pat_no_overcapture_lowercase_prefix_long() -> None:
    # Pins the case-sensitive municipality match against IGNORECASE-driven regression.
    m = _first_match("Õiguskantsleri taotlus Tallinna Linnavolikogu 19.02.2009 määruse nr 3 muutmise kohta")
    assert m is not None
    assert m.group("municipality").strip() == "Tallinna"


def test_pat_no_overcapture_lowercase_prefix_short() -> None:
    m = _first_match("tunnistada kehtetuks Keila Vallavolikogu 21.06.99 määrus nr 55")
    assert m is not None
    assert m.group("municipality").strip() == "Keila"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_extract_court_provision_links.py -v
```

Expected: 8 new tests fail with `ImportError: cannot import name 'PAT_KOV_ACT'`.

- [ ] **Step 3: Add the `PAT_KOV_ACT` regex**

In `scripts/extract_court_provision_links.py`, after the `expand_two_digit_year` function and before `build_provision_index`, add:

```python
# ---------------------------------------------------------------------------
# KOV act citation regex (Layer 2c PR #3)
# ---------------------------------------------------------------------------
# CRITICAL: do NOT apply re.IGNORECASE globally. Under global IGNORECASE the
# municipality character class [A-ZÕÄÖÜŠŽ] would also accept lowercase
# letters, and the {1,3} quantifier would consume preceding lowercase
# words like "Õiguskantsleri taotlus" or "tunnistada kehtetuks" before
# the municipality name, polluting issuer_norm and breaking resolution.
# Case-insensitivity is scoped via inline (?i:...) only to the body word,
# month names, and act-marker keyword.
PAT_KOV_ACT = re.compile(
    # Municipality: 1-3 titlecase words. The character class enforces
    # uppercase initial + lowercase continuation (case-SENSITIVE).
    r"(?P<municipality>(?:[A-ZÕÄÖÜŠŽ][a-zõäöüšž][\wõäöüšž-]*\s+){1,3})"
    # Body word: case-insensitive scope. Genitive forms first inside each
    # pair so longest-match-wins under alternation is well-defined.
    r"(?P<body>(?i:linnavalitsuse|vallavalitsuse|"
    r"linnavalitsus|vallavalitsus|"
    r"linnavolikogu|vallavolikogu))\s+"
    # Date: numeric d.m.y or long form with case-insensitive Estonian month.
    r"(?P<date>\d{1,2}\.\d{1,2}\.(?:\d{2}|\d{4})|"
    r"\d{1,2}\.\s*(?i:jaanuari|veebruari|märtsi|aprilli|mai|juuni|juuli|"
    r"augusti|septembri|oktoobri|novembri|detsembri)\s+\d{4})"
    # Optional " . " / " . a. " / " . aasta " between date and act marker.
    r"\.?\s*(?i:a\.?|aasta)?\s+"
    # Act marker, case-insensitive scope. Covers määrus / määruse / määrust / määrusega.
    r"(?i:määrus(?:e|t|ega)?)\s*nr\s*\.?\s*(?P<num>\d+)",
    re.UNICODE,
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_extract_court_provision_links.py -v
```

Expected: 15 passed (7 from Task 3 + 8 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_court_provision_links.py tests/test_extract_court_provision_links.py
git commit -m "Add PAT_KOV_ACT regex with titlecase municipality anchor"
```

---

## Task 5 — `PAT_RTIV` regex (counted-only)

**Files:**
- Modify: `scripts/extract_court_provision_links.py`
- Test: `tests/test_extract_court_provision_links.py` (extend Group 1)

- [ ] **Step 1: Append failing tests**

```python
from extract_court_provision_links import PAT_RTIV


def test_pat_rtiv_canonical_form() -> None:
    assert PAT_RTIV.search("vt RT IV, 2020-04-15, 7") is not None


def test_pat_rtiv_alternate_form() -> None:
    assert PAT_RTIV.search("avaldatud RT IV 2018, 3, 5") is not None


def test_pat_rtiv_no_match_plain_text() -> None:
    assert PAT_RTIV.search("KarS § 121") is None
```

- [ ] **Step 2: Run** — Expected: 3 fail.

```bash
pytest tests/test_extract_court_provision_links.py -v
```

- [ ] **Step 3: Add the regex**

After `PAT_KOV_ACT`:

```python
# Counted-only RT IV reference detector. We do not resolve these (no
# prebuilt RT IV → IRI lookup table); they're tracked under
# skip_reasons["rtiv_form_citation"] so a future PR notices when this
# bucket becomes nonzero.
PAT_RTIV = re.compile(
    r"\bRT\s+IV[,\s]+\d{2,4}(?:[\.\-/]\d{1,2}[\.\-/]\d{2,4}|[,\s]+\d+)"
    r"(?:[,\s]+\d+)?",
    re.IGNORECASE,
)
```

- [ ] **Step 4: Run** — Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_court_provision_links.py tests/test_extract_court_provision_links.py
git commit -m "Add PAT_RTIV detector for counted-only RT IV citation tracking"
```

---

## Task 6 — Widen `extract_citations_from_text` to return `(state, kov)` tuple

**Files:**
- Modify: `scripts/extract_court_provision_links.py:119-162` (replace `extract_citations_from_text`)
- Test: `tests/test_extract_court_provision_links.py`

- [ ] **Step 1: Append failing tests**

```python
from extract_court_provision_links import extract_citations_from_text


# ---------------------------------------------------------------------------
# Group 1c — extract_citations_from_text returns (state, kov) tuple
# ---------------------------------------------------------------------------

def test_extract_returns_tuple() -> None:
    result = extract_citations_from_text("KarS § 121")
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_extract_state_only_text() -> None:
    state, kov = extract_citations_from_text("KarS § 121")
    assert state                      # at least one state-law citation
    assert kov == []


def test_extract_kov_only_text() -> None:
    state, kov = extract_citations_from_text(
        "Viimsi Vallavolikogu 13.10.2009 määruse nr 22"
    )
    assert state == []
    assert len(kov) == 1
    assert kov[0]["municipality"].strip() == "Viimsi"
    assert kov[0]["body"].lower() == "vallavolikogu"
    assert kov[0]["num"] == "22"
    assert kov[0]["raw_text"].startswith("Viimsi Vallavolikogu")


def test_extract_mixed_text() -> None:
    state, kov = extract_citations_from_text(
        "Vt KarS § 121 ja Viimsi Vallavolikogu 13.10.2009 määruse nr 22"
    )
    assert len(state) >= 1
    assert len(kov) == 1


def test_extract_empty_text() -> None:
    assert extract_citations_from_text("") == ([], [])
    assert extract_citations_from_text(None) == ([], [])
```

- [ ] **Step 2: Run** — Expected: 5 fail (current return is `list[dict]`, not tuple).

- [ ] **Step 3: Replace `extract_citations_from_text`**

In `scripts/extract_court_provision_links.py`, replace lines 119–162 (the entire `extract_citations_from_text` function) with:

```python
def extract_citations_from_text(text: str | None) -> tuple[list[dict], list[dict]]:
    """Parse text for Estonian legal citation patterns.

    Returns ``(state_citations, kov_citations)``:

    - ``state_citations``: list of ``{"law_ref": <abbrev>, "paragraphs": [<p>, ...]}``
      for state-law citations matching the abbreviation+§ or genitive+§ patterns.
    - ``kov_citations``:   list of ``{"municipality", "body", "date", "num", "raw_text"}``
      for KOV act-form citations matching ``PAT_KOV_ACT``.
    """
    state_citations: list[dict] = []
    kov_citations: list[dict] = []
    if not text:
        return state_citations, kov_citations

    abbrevs = "|".join(
        re.escape(a) for a in sorted(KNOWN_ABBREVIATIONS.keys(), key=len, reverse=True)
    )

    # Pattern 1: Abbreviation + § + number(s)
    pat_abbrev = re.compile(
        rf"({abbrevs})\s*{PAR_SUFFIX}\s*(\d+(?:\s*[\-–]\s*\d+)?)",
        re.UNICODE,
    )
    for m in pat_abbrev.finditer(text):
        abbrev = m.group(1)
        par_range = m.group(2).strip()
        paragraphs = _expand_par_range(par_range)
        if paragraphs:
            state_citations.append({"law_ref": abbrev, "paragraphs": paragraphs})

    # Pattern 2: Full name in genitive + § + number
    genitive_names = "|".join(
        re.escape(g) for g in sorted(FULLNAME_GENITIVE.keys(), key=len, reverse=True)
    )
    if genitive_names:
        pat_fullname = re.compile(
            rf"({genitive_names})\s*{PAR_SUFFIX}\s*(\d+(?:\s*[\-–]\s*\d+)?)",
            re.UNICODE | re.IGNORECASE,
        )
        for m in pat_fullname.finditer(text):
            gen_name = m.group(1).lower()
            par_range = m.group(2).strip()
            paragraphs = _expand_par_range(par_range)
            abbrev = FULLNAME_GENITIVE.get(gen_name)
            if abbrev and paragraphs:
                state_citations.append({"law_ref": abbrev, "paragraphs": paragraphs})

    # Pattern 3: KOV act-level citation (Layer 2c PR #3)
    for m in PAT_KOV_ACT.finditer(text):
        kov_citations.append({
            "municipality": m.group("municipality"),
            "body":         m.group("body"),
            "date":         m.group("date"),
            "num":          m.group("num"),
            "raw_text":     m.group(0),
        })

    return state_citations, kov_citations
```

- [ ] **Step 4: Run** — Expected: 23 passed.

```bash
pytest tests/test_extract_court_provision_links.py -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_court_provision_links.py tests/test_extract_court_provision_links.py
git commit -m "Widen extract_citations_from_text to return (state, kov) tuple"
```

---

## Task 7 — `build_kov_act_index` with collision detection

**Files:**
- Modify: `scripts/extract_court_provision_links.py` (add new function after `build_abbreviation_to_prefix`)
- Test: `tests/test_extract_court_provision_links.py`
- Test fixture: `tests/fixtures/kov_court_provision_links/` (create)

- [ ] **Step 1: Create fixture peep files**

```bash
mkdir -p tests/fixtures/kov_court_provision_links/kov/viimsi_vallavolikogu
mkdir -p tests/fixtures/kov_court_provision_links/kov/collision_pair
```

Create `tests/fixtures/kov_court_provision_links/kov/viimsi_vallavolikogu/maarus_22_t1024484_peep.json`:

```json
{
  "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
  "@graph": [
    {
      "@id": "estleg:Reg_1024484_Map",
      "@type": ["owl:Ontology", "estleg:MunicipalRegulation", "estleg:Act"],
      "estleg:actNumber": "22",
      "estleg:documentType": "määrus",
      "estleg:enactedBy": {"@id": "estleg:Issuer_viimsi_vallavolikogu"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_0890"},
      "estleg:entryIntoForce": {"@value": "2009-10-13", "@type": "xsd:date"},
      "estleg:globalId": "424102009022",
      "estleg:isKov": {"@value": "true", "@type": "xsd:boolean"},
      "estleg:issuer": "Viimsi Vallavolikogu",
      "estleg:terviktekstId": "1024484",
      "estleg:titleNormalized": "viimsi valla valimiskomisjoni moodustamine"
    }
  ]
}
```

Create `tests/fixtures/kov_court_provision_links/kov/collision_pair/act_a_peep.json`:

```json
{
  "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
  "@graph": [
    {
      "@id": "estleg:Reg_2000001_Map",
      "@type": ["owl:Ontology", "estleg:MunicipalRegulation", "estleg:Act"],
      "estleg:actNumber": "5",
      "estleg:enactedBy": {"@id": "estleg:Issuer_test_vallavolikogu"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_9999"},
      "estleg:entryIntoForce": {"@value": "2016-03-15", "@type": "xsd:date"},
      "estleg:isKov": {"@value": "true", "@type": "xsd:boolean"},
      "estleg:issuer": "Test Vallavolikogu",
      "estleg:terviktekstId": "2000001",
      "estleg:titleNormalized": "test act a"
    }
  ]
}
```

Create `tests/fixtures/kov_court_provision_links/kov/collision_pair/act_b_peep.json` (same key, different IRI):

```json
{
  "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
  "@graph": [
    {
      "@id": "estleg:Reg_2000002_Map",
      "@type": ["owl:Ontology", "estleg:MunicipalRegulation", "estleg:Act"],
      "estleg:actNumber": "5",
      "estleg:enactedBy": {"@id": "estleg:Issuer_test_vallavolikogu"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_9999"},
      "estleg:entryIntoForce": {"@value": "2016-03-15", "@type": "xsd:date"},
      "estleg:isKov": {"@value": "true", "@type": "xsd:boolean"},
      "estleg:issuer": "Test Vallavolikogu",
      "estleg:terviktekstId": "2000002",
      "estleg:titleNormalized": "test act b (duplicate key)"
    }
  ]
}
```

- [ ] **Step 2: Append failing tests**

```python
from estleg_common import _RunCounters
from extract_court_provision_links import build_kov_act_index


# ---------------------------------------------------------------------------
# Group 2 — build_kov_act_index
# ---------------------------------------------------------------------------

FIXTURE_KOV = Path(__file__).parent / "fixtures" / "kov_court_provision_links" / "kov"


def test_build_kov_index_resolves_viimsi(monkeypatch) -> None:
    """Index walks fixture KOV peeps and registers the Viimsi 2009 #22 act."""
    from extract_court_provision_links import iter_peep_files as real_iter

    fixture_files = list((FIXTURE_KOV / "viimsi_vallavolikogu").glob("*_peep.json"))

    def fake_iter(*, include_kov: bool = True):
        return iter(fixture_files)

    monkeypatch.setattr(
        "extract_court_provision_links.iter_peep_files", fake_iter
    )

    counters = _RunCounters()
    kov_index, collisions, iri_to_file, known_issuers = build_kov_act_index(counters)

    assert ("viimsi vallavolikogu", 2009, "22") in kov_index
    assert kov_index[("viimsi vallavolikogu", 2009, "22")] == "estleg:Reg_1024484_Map"
    assert "viimsi vallavolikogu" in known_issuers
    assert collisions == set()


def test_build_kov_index_detects_collisions(monkeypatch) -> None:
    fixture_files = list((FIXTURE_KOV / "collision_pair").glob("*_peep.json"))

    def fake_iter(*, include_kov: bool = True):
        return iter(fixture_files)

    monkeypatch.setattr(
        "extract_court_provision_links.iter_peep_files", fake_iter
    )

    counters = _RunCounters()
    kov_index, collisions, iri_to_file, known_issuers = build_kov_act_index(counters)

    key = ("test vallavolikogu", 2016, "5")
    assert key in collisions
    assert key not in kov_index            # removed on collision detection
    # Both files still recorded in iri_to_file (cleanup pass needs them)
    assert "estleg:Reg_2000001_Map" in iri_to_file
    assert "estleg:Reg_2000002_Map" in iri_to_file
```

- [ ] **Step 3: Run** — Expected: 2 fail (`build_kov_act_index` not defined).

- [ ] **Step 4: Implement `build_kov_act_index`**

After `build_abbreviation_to_prefix` (around line 100), add:

```python
def build_kov_act_index(
    counters: "_RunCounters",
) -> tuple[
    dict[tuple[str, int, str], str],
    set[tuple[str, int, str]],
    dict[str, Path],
    set[str],
]:
    """Build the KOV act index over MunicipalRegulation peeps.

    Returns ``(kov_index, kov_collision_keys, kov_iri_to_file, known_issuer_norms)``.
    Keys are ``(issuer_norm, entryIntoForce.year, actNumber)``. On collision
    (two acts mapping to the same key) the key is removed from kov_index and
    added to kov_collision_keys; never silent-overwrites.
    """
    kov_index: dict[tuple[str, int, str], str] = {}
    kov_collision_keys: set[tuple[str, int, str]] = set()
    kov_iri_to_file: dict[str, Path] = {}
    known_issuer_norms: set[str] = set()

    for f in iter_peep_files():
        doc = _safe_load(f, counters)
        if doc is None:
            continue
        for node in doc.get("@graph", []):
            types = node.get("@type", [])
            if "estleg:MunicipalRegulation" not in types:
                continue
            iri = node.get("@id", "")
            issuer = node.get("estleg:issuer", "")
            entry_force = ""
            eif = node.get("estleg:entryIntoForce")
            if isinstance(eif, dict):
                entry_force = eif.get("@value", "")
            act_number = str(node.get("estleg:actNumber", "") or "")
            if not (iri and issuer and entry_force and act_number):
                break
            issuer_norm = normalize_issuer_name(issuer)
            known_issuer_norms.add(issuer_norm)
            try:
                year = int(entry_force[:4])
            except ValueError:
                break
            key = (issuer_norm, year, act_number)
            if key in kov_collision_keys:
                pass   # already marked ambiguous; subsequent dupes are silent
            elif key in kov_index and kov_index[key] != iri:
                kov_collision_keys.add(key)
                del kov_index[key]
            else:
                kov_index[key] = iri
            kov_iri_to_file[iri] = f
            break    # one act node per peep
    return kov_index, kov_collision_keys, kov_iri_to_file, known_issuer_norms
```

Add the imports at the top of the file (find the existing `from estleg_common import (...)` block around line 27 and extend it):

```python
from estleg_common import (
    CONTEXT,
    FULLNAME_GENITIVE,
    KNOWN_ABBREVIATIONS,
    PAR_SUFFIX,
    BODY_CANON,                 # NEW
    _RunCounters,               # NEW
    _safe_load,                 # NEW
    iter_peep_files,
    normalize_issuer_name,      # NEW
    save_json,
    sanitize_id,
)
```

- [ ] **Step 5: Run** — Expected: 25 passed.

```bash
pytest tests/test_extract_court_provision_links.py -v
```

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_court_provision_links.py tests/test_extract_court_provision_links.py tests/fixtures/kov_court_provision_links/
git commit -m "Add build_kov_act_index with collision detection and known_issuer set"
```

---

## Task 8 — `resolve_kov_citation`

**Files:**
- Modify: `scripts/extract_court_provision_links.py` (add after `resolve_citations`)
- Test: `tests/test_extract_court_provision_links.py`

- [ ] **Step 1: Append failing tests**

```python
from extract_court_provision_links import resolve_kov_citation


# ---------------------------------------------------------------------------
# Group 2b — resolve_kov_citation
# ---------------------------------------------------------------------------

def _kc(municipality="Viimsi ", body="Vallavolikogu", date="13.10.2009", num="22"):
    return {
        "municipality": municipality,
        "body": body,
        "date": date,
        "num": num,
        "raw_text": f"{municipality}{body} {date} määruse nr {num}",
    }


def test_resolver_strict_match() -> None:
    idx = {("viimsi vallavolikogu", 2009, "22"): "estleg:Reg_1024484_Map"}
    iri, reason = resolve_kov_citation(_kc(), idx, set(), {"viimsi vallavolikogu"})
    assert iri == "estleg:Reg_1024484_Map"
    assert reason is None


def test_resolver_year_plus_one_alternate() -> None:
    # Index has 2010 entry; query year 2009 resolves via +1 alternate.
    idx = {("tallinna linnavalitsus", 2010, "75"): "estleg:Reg_1014396_Map"}
    iri, reason = resolve_kov_citation(
        _kc(municipality="Tallinna ", body="Linnavalitsus", date="14.05.2009", num="75"),
        idx, set(), {"tallinna linnavalitsus"},
    )
    assert iri == "estleg:Reg_1014396_Map"
    assert reason is None


def test_resolver_ambiguous_primary_short_circuits() -> None:
    collisions = {("viimsi vallavolikogu", 2009, "22")}
    iri, reason = resolve_kov_citation(_kc(), {}, collisions, {"viimsi vallavolikogu"})
    assert iri is None
    assert reason == "ambiguous_key"


def test_resolver_ambiguous_alternate_short_circuits() -> None:
    # Primary 2009 missing; alternate 2010 collision-tracked.
    collisions = {("viimsi vallavolikogu", 2010, "22")}
    iri, reason = resolve_kov_citation(_kc(), {}, collisions, {"viimsi vallavolikogu"})
    assert iri is None
    assert reason == "ambiguous_key"


def test_resolver_unmatched() -> None:
    iri, reason = resolve_kov_citation(_kc(), {}, set(), {"viimsi vallavolikogu"})
    assert iri is None
    assert reason == "issuer_year_num_unmatched"


def test_resolver_unknown_issuer_short_circuits() -> None:
    # Issuer NOT in known_issuer_norms → unknown_issuer, no index lookup.
    iri, reason = resolve_kov_citation(_kc(), {}, set(), set())
    assert iri is None
    assert reason == "unknown_issuer"
```

- [ ] **Step 2: Run** — Expected: 6 fail.

- [ ] **Step 3: Implement `resolve_kov_citation`**

In `scripts/extract_court_provision_links.py`, after `resolve_citations` (around line 187), add:

```python
def resolve_kov_citation(
    match: dict,
    kov_index: dict[tuple[str, int, str], str],
    kov_collision_keys: set[tuple[str, int, str]],
    known_issuer_norms: set[str],
) -> tuple[str | None, str | None]:
    """Resolve a single KOV citation match to an Act IRI.

    Returns ``(act_iri, None)`` on success, or ``(None, reason)`` where
    ``reason`` is one of:

    - ``"unknown_issuer"`` — issuer string not in known_issuer_norms (defensive).
    - ``"ambiguous_key"`` — primary or +1 alternate key in kov_collision_keys.
    - ``"issuer_year_num_unmatched"`` — issuer is known but no peep matches.
    """
    issuer_norm = normalize_issuer_name(
        f"{match['municipality'].strip()} {BODY_CANON[match['body'].lower()]}"
    )
    if issuer_norm not in known_issuer_norms:
        return None, "unknown_issuer"
    primary_year = expand_two_digit_year(match["date"])
    num = match["num"]
    for year in (primary_year, primary_year + 1):
        key = (issuer_norm, year, num)
        if key in kov_collision_keys:
            return None, "ambiguous_key"
        if key in kov_index:
            return kov_index[key], None
    return None, "issuer_year_num_unmatched"
```

- [ ] **Step 4: Run** — Expected: 31 passed.

```bash
pytest tests/test_extract_court_provision_links.py -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_court_provision_links.py tests/test_extract_court_provision_links.py
git commit -m "Add resolve_kov_citation with known-issuer precondition and +1 alternate"
```

---

## Task 9 — Thread `_RunCounters` through readers + KOV-side stale cleanup (walk #3)

**Files:**
- Modify: `scripts/extract_court_provision_links.py:40-89` (extend `build_provision_index` to accept `counters` and use `_safe_load`)
- Modify: `scripts/extract_court_provision_links.py:338-384` (extend `clear_existing_court_links`)

The plan's atomicity invariant ("malformed inputs increment four counters") requires every JSON read site in the pipeline to use `_safe_load`. Today `build_provision_index` silently `continue`s on `(json.JSONDecodeError, OSError)`. This task threads counters through both that function and the cleanup function, and adds the KOV cleanup walk.

- [ ] **Step 1a: Update `build_provision_index` to accept `counters` and use `_safe_load`**

Find the signature at line 40:

```python
def build_provision_index() -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, Path]]:
```

Replace with:

```python
def build_provision_index(
    counters: "_RunCounters",
) -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, Path]]:
```

Then find the inner read at lines 53–58:

```python
    for json_file in iter_peep_files(include_kov=False):  # DEFERRED to Layer 2c
        # Skip court decision files
        if json_file.name.startswith("riigikohus"):
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
```

Replace with:

```python
    for json_file in iter_peep_files(include_kov=False):  # DEFERRED to Layer 2c
        # Skip court decision files
        if json_file.name.startswith("riigikohus"):
            continue
        doc = _safe_load(json_file, counters)
        if doc is None:
            continue
```

The `# DEFERRED to Layer 2c` comment is preserved verbatim — line 53 callsite stays pinned per invariant #2.

- [ ] **Step 1b: Replace `clear_existing_court_links`**

Replace the entire function body at lines 338–384 with:

```python
def clear_existing_court_links(counters: "_RunCounters") -> int:
    """Clear stale court-provision link triples for idempotent re-run.

    Three walks:
      1. Court files (RK_DIR/riigikohus_*_peep.json) → strip estleg:interpretsLaw.
      2. State-law peep files (iter_peep_files(include_kov=False)) → strip
         estleg:interpretedBy. Pinned: KOV peeps must NOT enter the state-law
         provision index (line ~53 callsite); the same exclusion applies here
         to keep the cleanup symmetric with the index build.
      3. KOV peep files (iter_peep_files() filtered to MunicipalRegulation)
         → strip estleg:interpretedBy. Walks ALL KOV peeps each run so that
         a peep that was a target last run but isn't this run loses its
         stale triples.
    """
    cleaned = 0

    # Walk 1: court files
    if RK_DIR.exists():
        for rk_file in sorted(RK_DIR.glob("riigikohus_*_peep.json")):
            doc = _safe_load(rk_file, counters)
            if doc is None:
                continue
            modified = False
            for node in doc.get("@graph", []):
                if "estleg:interpretsLaw" in node:
                    del node["estleg:interpretsLaw"]
                    modified = True
            if modified:
                save_json(rk_file, doc)
                cleaned += 1

    # Walk 2: state-law peeps (pinned: include_kov=False)
    for law_file in iter_peep_files(include_kov=False):     # DEFERRED to Layer 2c
        if law_file.name.startswith("riigikohus"):
            continue
        doc = _safe_load(law_file, counters)
        if doc is None:
            continue
        modified = False
        for node in doc.get("@graph", []):
            if "estleg:interpretedBy" in node:
                del node["estleg:interpretedBy"]
                modified = True
        if modified:
            save_json(law_file, doc)
            cleaned += 1

    # Walk 3 (NEW, Layer 2c PR #3): KOV peeps
    for kov_file in iter_peep_files():
        doc = _safe_load(kov_file, counters)
        if doc is None:
            continue
        is_kov = any(
            "estleg:MunicipalRegulation" in node.get("@type", [])
            for node in doc.get("@graph", [])
        )
        if not is_kov:
            continue
        modified = False
        for node in doc.get("@graph", []):
            if "estleg:interpretedBy" in node:
                del node["estleg:interpretedBy"]
                modified = True
        if modified:
            save_json(kov_file, doc)
            cleaned += 1

    return cleaned
```

Note the `# DEFERRED to Layer 2c` comment on the line-365-callsite (now part of walk 2) is preserved verbatim — it stays pinned.

- [ ] **Step 2: Run existing tests to ensure refactor didn't regress**

```bash
pytest tests/test_extract_court_provision_links.py -v
```

Expected: 31 passed (no new tests yet; the new behavior is exercised by the end-to-end test in Task 12).

- [ ] **Step 3: Commit**

```bash
git add scripts/extract_court_provision_links.py
git commit -m "Thread _RunCounters through readers; add KOV-side stale interpretedBy cleanup"
```

---

## Task 10 — Integrate KOV resolution in `process_court_files` and `apply_interpreted_by`

**Files:**
- Modify: `scripts/extract_court_provision_links.py:189-335`

This is the big integration step. `process_court_files` learns to merge state + KOV resolutions, and `apply_interpreted_by` learns to write to either kind of target file via the merged IRI→file map.

- [ ] **Step 1: Replace `process_court_files`**

Replace lines 189–276 with:

```python
def process_court_files(
    abbrev_to_prefix: dict[str, str],
    prefix_to_provisions: dict[str, dict[str, str]],
    kov_index: dict[tuple[str, int, str], str],
    kov_collision_keys: set[tuple[str, int, str]],
    known_issuer_norms: set[str],
    counters: "_RunCounters",
) -> tuple[list[dict], dict[str, list[str]], int, int, int, int, int]:
    """Process all riigikohus_YYYY_peep.json files.

    Returns:
      - per_file_stats:              list of stat dicts (existing shape).
      - interpreted_by:              {target_iri: [court_decision_iri, ...]} — target_iri
                                     may be either a LegalProvision or a MunicipalRegulation.
      - state_link_count:            number of forward arcs to state-law provisions.
      - kov_link_count:              UNIQUE forward arcs to KOV act IRIs (post per-decision
                                     dedup). Feeds triples_emitted_kov in the coverage report.
      - kov_unresolved:              number of KOV citations failing with
                                     reason=issuer_year_num_unmatched. Feeds top-level
                                     unresolved_references in the coverage report.
      - kov_citations_matched:       RAW regex hit count across all decisions (pre-resolve).
                                     Diagnostic only — not a coverage-report field.
      - kov_citations_resolved_raw:  RAW count of resolver successes BEFORE per-decision
                                     dedup. Diagnostic only — kov_link_count is the
                                     unique-arcs version.
    """
    per_file_stats: list[dict] = []
    interpreted_by: dict[str, list[str]] = defaultdict(list)
    state_link_count = 0
    kov_link_count = 0           # unique forward arcs after per-decision dedup
    kov_citations_matched = 0    # raw regex hits across all decisions
    kov_citations_resolved_raw = 0   # raw resolved (pre-dedup) for diagnostics
    kov_unresolved = 0

    rk_files = sorted(RK_DIR.glob("riigikohus_*_peep.json"))
    if not rk_files:
        print("  No riigikohus files found!")
        return per_file_stats, dict(interpreted_by), 0, 0, 0, 0, 0

    for rk_file in rk_files:
        stats = {
            "file": rk_file.name,
            "decisions_scanned": 0,
            "decisions_with_citations": 0,
            "citations_found": 0,
            "citations_resolved": 0,
        }

        doc = _safe_load(rk_file, counters)
        if doc is None:
            stats["error"] = "load_failed"
            per_file_stats.append(stats)
            continue

        graph = doc.get("@graph", [])
        modified = False

        for node in graph:
            node_id = node.get("@id", "")
            node_types = node.get("@type", [])

            if "estleg:CourtDecision" not in node_types:
                continue

            stats["decisions_scanned"] += 1

            summary = node.get("estleg:summary", "")
            if not summary:
                continue

            # RT IV detector — counted only.
            for _ in PAT_RTIV.finditer(summary):
                counters.skip_reasons["rtiv_form_citation"] = (
                    counters.skip_reasons.get("rtiv_form_citation", 0) + 1
                )

            state_citations, kov_citations = extract_citations_from_text(summary)
            if not (state_citations or kov_citations):
                continue

            stats["citations_found"] += len(state_citations) + len(kov_citations)
            stats["decisions_with_citations"] += 1
            kov_citations_matched += len(kov_citations)

            # State-law resolver (existing, unchanged).
            state_iris = resolve_citations(
                state_citations, abbrev_to_prefix, prefix_to_provisions
            )
            state_link_count += len(state_iris)

            # KOV act resolver (new).
            kov_iris: list[str] = []
            for kc in kov_citations:
                iri, reason = resolve_kov_citation(
                    kc, kov_index, kov_collision_keys, known_issuer_norms,
                )
                issuer_norm_for_log = normalize_issuer_name(
                    f"{kc['municipality'].strip()} {BODY_CANON[kc['body'].lower()]}"
                )
                primary_year = expand_two_digit_year(kc["date"])
                if iri:
                    kov_iris.append(iri)
                    continue
                sample = (
                    f"{reason:<25s} | {node_id} | "
                    f"issuer={issuer_norm_for_log} year={primary_year} num={kc['num']} | "
                    f"reason={reason}"
                )
                if reason == "ambiguous_key":
                    counters.skip_reasons["kov_citation_ambiguous"] = (
                        counters.skip_reasons.get("kov_citation_ambiguous", 0) + 1
                    )
                    if len(counters.failures) < 200:
                        counters.failures.append(sample)
                elif reason == "unknown_issuer":
                    counters.skip_reasons["kov_citation_unknown_issuer"] = (
                        counters.skip_reasons.get("kov_citation_unknown_issuer", 0) + 1
                    )
                    if len(counters.failures) < 200:
                        counters.failures.append(sample)
                else:    # issuer_year_num_unmatched
                    kov_unresolved += 1
                    if len(counters.failures) < 200:
                        counters.failures.append(sample)

            # Dedupe kov_iris per-decision before counting so triples_emitted_kov
            # matches the number of unique forward arcs actually written
            # (a decision repeating the same KOV citation emits one arc, not N).
            # state_iris is already deduped by resolve_citations() at return time.
            kov_citations_resolved_raw += len(kov_iris)
            unique_kov_iris = list(dict.fromkeys(kov_iris))
            kov_link_count += len(unique_kov_iris)
            stats["citations_resolved"] += len(state_iris) + len(unique_kov_iris)

            resolved = list(dict.fromkeys(state_iris + unique_kov_iris))
            if not resolved:
                continue

            ref_iris = [{"@id": r} for r in resolved]
            node["estleg:interpretsLaw"] = ref_iris
            modified = True

            for target_iri in resolved:
                interpreted_by[target_iri].append(node_id)

        if modified:
            save_json(rk_file, doc)

        per_file_stats.append(stats)

    return (
        per_file_stats,
        dict(interpreted_by),
        state_link_count,
        kov_link_count,
        kov_unresolved,
        kov_citations_matched,
        kov_citations_resolved_raw,
    )
```

- [ ] **Step 2: Update `apply_interpreted_by` to accept the merged IRI→file map**

Replace the signature and unresolved branch in `apply_interpreted_by` (around lines 279–335). Find:

```python
def apply_interpreted_by(
    interpreted_by: dict[str, list[str]],
    iri_to_file: dict[str, Path],
) -> dict[str, int]:
```

Replace the function header with:

```python
def apply_interpreted_by(
    interpreted_by: dict[str, list[str]],
    iri_to_file: dict[str, Path],
    counters: "_RunCounters",
) -> dict[str, int]:
```

And replace the inner `try/except` JSON read with `_safe_load`. Find:

```python
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Error reading {target_file.name}: {e}")
            continue
```

Replace with:

```python
        doc = _safe_load(target_file, counters)
        if doc is None:
            continue
```

- [ ] **Step 3: Run existing tests**

```bash
pytest tests/test_extract_court_provision_links.py -v
```

Expected: 31 passed (signatures changed but unit tests don't call these directly; the end-to-end test in Task 12 exercises the new flow).

- [ ] **Step 4: Commit**

```bash
git add scripts/extract_court_provision_links.py
git commit -m "Integrate KOV citation resolver into process_court_files; widen apply_interpreted_by"
```

---

## Task 11 — Coverage report integration in `main()` + diagnostic logging

**Files:**
- Modify: `scripts/extract_court_provision_links.py:387-499` (`main()`)

- [ ] **Step 1: Replace `main()`**

Replace `def main() -> None:` and its body (lines 387–end) with:

```python
def main() -> None:
    import time
    from datetime import datetime, timezone

    from kov_pipeline_coverage import (
        CoverageReport,
        measure_runtime,
        resolve_pipeline_version,
        write_coverage_report,
    )

    start = time.perf_counter()
    counters = _RunCounters()
    # Pre-seed expected skip_reasons buckets to 0 so the report shape
    # is stable across runs (tests and consumers can index directly).
    for _key in (
        "kov_citation_ambiguous",
        "kov_citation_unknown_issuer",
        "rtiv_form_citation",
        "json_decode_error",
    ):
        counters.skip_reasons[_key] = 0

    print("=" * 70)
    print("Estonian Legal Ontology - Extract Court Decision Provision Links")
    print("=" * 70)

    # Step 1: Clear existing links for idempotent re-run.
    print("\n[1/6] Clearing existing court-provision links...")
    cleaned = clear_existing_court_links(counters)
    print(f"  Cleaned {cleaned} files")

    # Step 2: Build state-law provision index.
    print("\n[2/6] Building provision index from law JSON-LD files...")
    prefix_to_provisions, source_act_to_prefix, iri_to_file = build_provision_index(counters)
    total_provisions = sum(len(v) for v in prefix_to_provisions.values())
    print(f"  Found {len(prefix_to_provisions)} law prefixes with {total_provisions} provisions")

    # Step 3: Build abbreviation mapping.
    print("\n[3/6] Building abbreviation-to-prefix mapping...")
    abbrev_to_prefix = build_abbreviation_to_prefix(source_act_to_prefix)
    print(f"  Mapped {len(abbrev_to_prefix)} abbreviations to IRI prefixes")
    for abbrev, prefix in sorted(abbrev_to_prefix.items()):
        prov_count = len(prefix_to_provisions.get(prefix, {}))
        print(f"    {abbrev} -> {prefix} ({prov_count} provisions)")

    # Step 4 (NEW): Build KOV act index.
    print("\n[4/6] Building KOV act index...")
    kov_index, kov_collision_keys, kov_iri_to_file, known_issuer_norms = (
        build_kov_act_index(counters)
    )
    print(
        f"  KOV acts in index: {len(kov_index)} "
        f"(collisions excluded: {len(kov_collision_keys)}); "
        f"known issuers: {len(known_issuer_norms)}"
    )

    # Step 5: Process court decision files.
    print("\n[5/6] Processing court decision files...")
    (
        per_file_stats,
        interpreted_by,
        state_link_count,
        kov_link_count,
        kov_unresolved,
        kov_citations_matched,
        kov_citations_resolved_raw,
    ) = process_court_files(
        abbrev_to_prefix,
        prefix_to_provisions,
        kov_index,
        kov_collision_keys,
        known_issuer_norms,
        counters,
    )

    total_decisions = sum(s.get("decisions_scanned", 0) for s in per_file_stats)
    total_with_citations = sum(s.get("decisions_with_citations", 0) for s in per_file_stats)
    total_citations = sum(s.get("citations_found", 0) for s in per_file_stats)
    total_resolved = sum(s.get("citations_resolved", 0) for s in per_file_stats)

    for s in per_file_stats:
        resolved = s.get("citations_resolved", 0)
        if resolved > 0:
            print(f"  {s['file']}: {s['decisions_scanned']} decisions, "
                  f"{s['decisions_with_citations']} with citations, "
                  f"{resolved} resolved")

    # Step 6: Apply inverse interpretedBy on target files (state-law + KOV).
    print("\n[6/6] Applying estleg:interpretedBy to target files...")
    total_interpreted_targets = len(interpreted_by)
    total_inverse_edges = sum(len(v) for v in interpreted_by.values())
    print(f"  {total_interpreted_targets} targets referenced by court decisions")
    print(f"  {total_inverse_edges} total court-target inverse links")

    merged_iri_to_file = {**iri_to_file, **kov_iri_to_file}
    update_counts = apply_interpreted_by(interpreted_by, merged_iri_to_file, counters)
    print(f"  Updated {len(update_counts)} target files with interpretedBy links")

    # KOV citation summary. Distinguishes RAW citations (every regex hit) from
    # UNIQUE emitted forward arcs (kov_link_count, after per-decision dedup
    # of repeated identical citations within one decision summary).
    print("\nKOV act citation summary:")
    print(f"  KOV citations matched (raw):       {kov_citations_matched}")
    print(f"  KOV citations resolved (raw):      {kov_citations_resolved_raw}")
    print(f"  KOV unique emitted arcs:           {kov_link_count}")
    print(f"  KOV citations unresolved:          {kov_unresolved}")
    print(f"  KOV citations ambiguous-key:       {counters.skip_reasons.get('kov_citation_ambiguous', 0)}")
    print(f"  KOV citations unknown-issuer:      {counters.skip_reasons.get('kov_citation_unknown_issuer', 0)}")
    print(f"  RT IV-form citations seen:         {counters.skip_reasons.get('rtiv_form_citation', 0)}")
    # Sanity invariant: matched = resolved + unresolved + ambiguous + unknown.
    expected_matched = (
        kov_citations_resolved_raw
        + kov_unresolved
        + counters.skip_reasons.get("kov_citation_ambiguous", 0)
        + counters.skip_reasons.get("kov_citation_unknown_issuer", 0)
    )
    if expected_matched != kov_citations_matched:
        print(
            f"  WARN: counter drift — matched={kov_citations_matched} "
            f"but resolved+unresolved+ambiguous+unknown={expected_matched}"
        )

    # Generate report (existing per-file shape).
    # The "generated" timestamp field is intentionally OMITTED so this report
    # is byte-identical across runs over the same input. Run-time metadata
    # (timestamps, wall-time, memory) lives in the KOV coverage report below
    # and is excluded from idempotency comparison via VOLATILE_KEYS in tests.
    print("\n--- Generating report ---")
    report = {
        "summary": {
            "court_files_processed": len(per_file_stats),
            "total_decisions_scanned": total_decisions,
            "decisions_with_citations": total_with_citations,
            "total_citations_found": total_citations,
            "total_citations_resolved": total_resolved,
            "kov_citations_resolved": kov_link_count,
            "kov_citations_unresolved": kov_unresolved,
            "targets_interpreted": total_interpreted_targets,
            "inverse_link_edges": total_inverse_edges,
            "law_files_updated": len(update_counts),
            "abbreviations_mapped": len(abbrev_to_prefix),
            "kov_acts_in_index": len(kov_index),
            "kov_collision_keys": len(kov_collision_keys),
        },
        "abbreviation_mapping": {
            abbrev: {
                "iri_prefix": prefix,
                "provision_count": len(prefix_to_provisions.get(prefix, {})),
            }
            for abbrev, prefix in sorted(abbrev_to_prefix.items())
        },
        "top_interpreted_targets": sorted(
            [
                {
                    "target_iri": iri,
                    "court_decision_count": len(decisions),
                }
                for iri, decisions in interpreted_by.items()
            ],
            key=lambda x: x["court_decision_count"],
            reverse=True,
        )[:100],
        "per_file_stats": per_file_stats,
    }

    report_path = KRR_DIR / "court_provision_links_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # ----- KOV coverage report -----
    state_peep_count = 0
    state_peeps_scanned = 0
    for _ in iter_peep_files(include_kov=False):
        state_peep_count += 1
        state_peeps_scanned += 1
    kov_peep_count = 0
    kov_peeps_scanned = 0
    for _ in iter_peep_files():
        kov_peep_count += 1
        kov_peeps_scanned += 1
    kov_peep_count -= state_peep_count       # subtract the state subset
    kov_peeps_scanned -= state_peeps_scanned

    rk_file_count = len(per_file_stats)
    rk_files_read = rk_file_count    # _safe_load failures already in counters

    court_files_written = sum(1 for s in per_file_stats if s.get("citations_resolved", 0) > 0)
    # Discriminate KOV vs state-law output files using the kov_iri_to_file
    # filename set built during build_kov_act_index. KOV peep filenames have
    # no reliable substring marker, so set membership is the correct test.
    kov_filenames = {p.name for p in kov_iri_to_file.values()}
    kov_files_written = sum(1 for f in update_counts if f in kov_filenames)
    law_files_written = len(update_counts) - kov_files_written

    wall, rate, peak_mb = measure_runtime(start, total_decisions)
    cov = CoverageReport(
        pipeline="extract_court_provision_links",
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        pipeline_version=resolve_pipeline_version(),
        input_files_total=rk_file_count + state_peep_count + kov_peep_count,
        input_files_kov=kov_peep_count,
        files_processed=rk_files_read + state_peeps_scanned + kov_peeps_scanned,
        files_processed_kov=kov_peeps_scanned,
        files_with_output=court_files_written + law_files_written + kov_files_written,
        files_with_output_kov=kov_files_written,
        files_skipped=counters.file_skips,
        skip_reasons=dict(counters.skip_reasons),
        triples_emitted=state_link_count + kov_link_count,
        triples_emitted_kov=kov_link_count,
        fallback_hits=0,
        unresolved_references=kov_unresolved,
        wall_time_seconds=wall,
        items_per_second=rate,
        peak_memory_mb=peak_mb,
        error_count=counters.error_count,
        failure_samples=list(counters.failures),
    )
    cov_out = KRR_DIR / "reports" / "kov" / "court_provision_links_coverage.json"
    write_coverage_report(cov, cov_out)
    print(f"  KOV coverage report: {cov_out}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Court files processed:          {len(per_file_stats)}")
    print(f"  Decisions scanned:              {total_decisions}")
    print(f"  Decisions with citations:       {total_with_citations}")
    print(f"  Citations found:                {total_citations}")
    print(f"  Citations resolved (total):     {total_resolved}")
    print(f"  → state-law provisions:         {state_link_count}")
    print(f"  → KOV acts (NEW):               {kov_link_count}")
    print(f"  Targets with interpretedBy:     {total_interpreted_targets}")
    print(f"  Target files updated:           {len(update_counts)}")
    print(f"  Report:                         {report_path}")
    print(f"  KOV coverage:                   {cov_out}")
    print("=" * 70)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run all tests**

```bash
pytest -q
```

Expected: `250 passed` (206 baseline + 13 helper-module tests from Tasks 1–2 [`test_estleg_common_normalize.py` 6 + `test_estleg_common_run_counters.py` 7] + 31 extract-pipeline tests so far from Tasks 3–10 in `test_extract_court_provision_links.py`). If any fail, fix before commit. End-to-end / idempotency / SHACL tests are added in Tasks 12, 13, 16 — those bump the final-suite count to 255 (see Task 17 step 4).

- [ ] **Step 3: Smoke-test the script**

```bash
set -o pipefail   # surface a non-zero exit from the script even after `tail`
python scripts/extract_court_provision_links.py 2>&1 | tail -40
echo "exit=$?"
```

Without `pipefail`, a script crash could be masked by `tail` returning 0. The trailing `echo "exit=$?"` confirms the pipeline's overall status (must be `0`).

Expected output ends with the SUMMARY block; KOV acts in index ~10808 (matches empirical baseline); KOV citations resolved ≥ 1; KOV coverage report path printed; `exit=0`.

- [ ] **Step 4: Verify the coverage report shape**

```bash
python -c "
import json
p = 'krr_outputs/reports/kov/court_provision_links_coverage.json'
d = json.load(open(p))
required = {'pipeline','run_timestamp','pipeline_version','input_files_total',
            'input_files_kov','files_processed','files_processed_kov',
            'files_with_output','files_with_output_kov','files_skipped',
            'skip_reasons','triples_emitted','triples_emitted_kov',
            'fallback_hits','unresolved_references','wall_time_seconds',
            'items_per_second','peak_memory_mb','error_count','failure_samples'}
missing = required - d.keys()
assert not missing, f'Missing fields: {missing}'
for k in ('kov_citation_ambiguous','kov_citation_unknown_issuer',
         'rtiv_form_citation','json_decode_error'):
    assert k in d['skip_reasons'], f'skip_reasons missing pre-seeded key: {k}'
assert d['triples_emitted_kov'] >= 1, f'triples_emitted_kov={d[\"triples_emitted_kov\"]}'
assert d['files_processed_kov'] <= d['files_processed']
print('OK: coverage report shape valid; triples_emitted_kov =', d['triples_emitted_kov'])
"
```

Expected: `OK: coverage report shape valid; triples_emitted_kov = <n>` where `n ≥ 1`.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract_court_provision_links.py
git commit -m "Integrate KOV coverage report and KOV citation summary in main()"
```

---

## Task 12 — End-to-end fixture test (Group 3)

**Files:**
- Create: `tests/fixtures/kov_court_provision_links/riigikohus/riigikohus_2009_peep.json`
- Create: `tests/fixtures/kov_court_provision_links/riigikohus/riigikohus_1999_peep.json`
- Create: `tests/fixtures/kov_court_provision_links/riigikohus/riigikohus_2016_peep.json`
- Create: `tests/fixtures/kov_court_provision_links/state_laws/tsiviilkohtumenetluse_seadustik_peep.json`
- Modify: `tests/test_extract_court_provision_links.py`

- [ ] **Step 1: Create the fixture files**

```bash
mkdir -p tests/fixtures/kov_court_provision_links/riigikohus
mkdir -p tests/fixtures/kov_court_provision_links/state_laws
```

`tests/fixtures/kov_court_provision_links/riigikohus/riigikohus_2009_peep.json`:

```json
{
  "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
  "@graph": [
    {
      "@id": "estleg:RK_FIXT_VIIMSI_2009",
      "@type": ["estleg:CourtDecision"],
      "estleg:summary": "Õiguskantsleri taotlus Viimsi Vallavolikogu 13. oktoobri 2009. a määruse nr 22 muutmise kohta. Vt ka TsMS § 208."
    }
  ]
}
```

`tests/fixtures/kov_court_provision_links/riigikohus/riigikohus_1999_peep.json`:

```json
{
  "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
  "@graph": [
    {
      "@id": "estleg:RK_FIXT_KEILA_1999",
      "@type": ["estleg:CourtDecision"],
      "estleg:summary": "tunnistada kehtetuks Keila Vallavolikogu 21.06.99 määrus nr.55 punkt 4."
    }
  ]
}
```

`tests/fixtures/kov_court_provision_links/riigikohus/riigikohus_2016_peep.json`:

```json
{
  "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
  "@graph": [
    {
      "@id": "estleg:RK_FIXT_COLLISION_2016",
      "@type": ["estleg:CourtDecision"],
      "estleg:summary": "Test Vallavolikogu 15.03.2016 määrus nr 5 ei ole kohaldatav."
    }
  ]
}
```

`tests/fixtures/kov_court_provision_links/state_laws/tsiviilkohtumenetluse_seadustik_peep.json`:

```json
{
  "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
  "@graph": [
    {
      "@id": "estleg:Tsiviilkohtumenetluse_seadustik",
      "@type": ["estleg:Act", "estleg:Law"],
      "estleg:sourceAct": "Tsiviilkohtumenetluse seadustik"
    },
    {
      "@id": "estleg:Tsiviilkohtumenetluse_seadustik_Par_208",
      "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
      "estleg:sourceAct": "Tsiviilkohtumenetluse seadustik"
    }
  ]
}
```

Add a Keila Vallavolikogu issuer-only KOV peep so its issuer is in `known_issuer_norms` even though no peep matches the (issuer, year, num) key (this mirrors the empirical "issuer is known but year/num miss"):

```bash
mkdir -p tests/fixtures/kov_court_provision_links/kov/keila_vallavolikogu
```

`tests/fixtures/kov_court_provision_links/kov/keila_vallavolikogu/maarus_other_peep.json`:

```json
{
  "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
  "@graph": [
    {
      "@id": "estleg:Reg_9999991_Map",
      "@type": ["owl:Ontology", "estleg:MunicipalRegulation", "estleg:Act"],
      "estleg:actNumber": "12",
      "estleg:enactedBy": {"@id": "estleg:Issuer_keila_vallavolikogu"},
      "estleg:enactedByMunicipality": {"@id": "estleg:Municipality_EHAK_4321"},
      "estleg:entryIntoForce": {"@value": "2018-05-01", "@type": "xsd:date"},
      "estleg:isKov": {"@value": "true", "@type": "xsd:boolean"},
      "estleg:issuer": "Keila Vallavolikogu",
      "estleg:terviktekstId": "9999991",
      "estleg:titleNormalized": "keila some other act"
    }
  ]
}
```

- [ ] **Step 2: Append the end-to-end test**

(`json`, `shutil`, and `pytest` are already imported at the top of the file from Task 3.)

```python
# ---------------------------------------------------------------------------
# Group 3 — end-to-end fixture run
# ---------------------------------------------------------------------------

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "kov_court_provision_links"


def _stage_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Stage fixture into tmp_path so the script writes to a copy.

    Layout under tmp_path:
        krr_outputs/riigikohus/riigikohus_*_peep.json
        krr_outputs/regulations/<state-laws>
        krr_outputs/regulations/kov/<KOV peeps>
        krr_outputs/reports/kov/  (for coverage report output)
    """
    krr = tmp_path / "krr_outputs"
    rk = krr / "riigikohus"
    laws = krr / "regulations"
    kov = krr / "regulations" / "kov"
    (krr / "reports" / "kov").mkdir(parents=True, exist_ok=True)
    rk.mkdir(parents=True, exist_ok=True)
    laws.mkdir(parents=True, exist_ok=True)
    kov.mkdir(parents=True, exist_ok=True)

    for f in (FIXTURE_ROOT / "riigikohus").glob("*_peep.json"):
        shutil.copy(f, rk / f.name)
    for f in (FIXTURE_ROOT / "state_laws").glob("*_peep.json"):
        shutil.copy(f, laws / f.name)
    for sub in (FIXTURE_ROOT / "kov").iterdir():
        if sub.is_dir():
            dst = kov / sub.name
            dst.mkdir(parents=True, exist_ok=True)
            for f in sub.glob("*_peep.json"):
                shutil.copy(f, dst / f.name)

    return krr, rk, kov


def _run_script_against(krr: Path, monkeypatch) -> dict:
    """Run main() with the fixture as the active KRR root; return coverage dict."""
    import extract_court_provision_links as ecpl
    import importlib

    monkeypatch.setattr(ecpl, "KRR_DIR", krr)
    monkeypatch.setattr(ecpl, "RK_DIR", krr / "riigikohus")

    # Override iter_peep_files to walk the fixture's regulations directory.
    def fake_iter(*, include_kov: bool = True):
        files = list((krr / "regulations").rglob("*_peep.json"))
        if not include_kov:
            files = [f for f in files if "/kov/" not in str(f) and "\\kov\\" not in str(f)]
        return iter(files)

    monkeypatch.setattr(ecpl, "iter_peep_files", fake_iter)

    ecpl.main()
    cov_path = krr / "reports" / "kov" / "court_provision_links_coverage.json"
    return json.loads(cov_path.read_text(encoding="utf-8"))


def test_end_to_end_resolves_viimsi_unresolved_keila(tmp_path, monkeypatch) -> None:
    krr, rk, kov = _stage_fixture(tmp_path)
    cov = _run_script_against(krr, monkeypatch)

    # Stable report shape: pre-seeded keys present.
    for key in (
        "kov_citation_ambiguous",
        "kov_citation_unknown_issuer",
        "rtiv_form_citation",
        "json_decode_error",
    ):
        assert key in cov["skip_reasons"], f"missing pre-seeded key: {key}"

    # Resolved exactly 1 KOV citation (Viimsi 2009 #22).
    assert cov["triples_emitted_kov"] == 1

    # Keila citation lands in unresolved (issuer known via fixture peep, year/num miss).
    assert cov["unresolved_references"] == 1

    # Test Vallavolikogu 2016 #5 → ambiguous (collision pair).
    assert cov["skip_reasons"]["kov_citation_ambiguous"] == 1

    # No RT IV in fixture summaries.
    assert cov["skip_reasons"]["rtiv_form_citation"] == 0

    # Subset invariants.
    assert cov["files_processed_kov"] <= cov["files_processed"]
    assert cov["files_with_output_kov"] <= cov["files_with_output"]


def test_end_to_end_writes_interpretsLaw_and_interpretedBy(tmp_path, monkeypatch) -> None:
    krr, rk, kov = _stage_fixture(tmp_path)
    _run_script_against(krr, monkeypatch)

    # Court decision file: interpretsLaw includes the Viimsi act IRI.
    rk_doc = json.loads((rk / "riigikohus_2009_peep.json").read_text(encoding="utf-8"))
    decision = next(
        n for n in rk_doc["@graph"]
        if n["@id"] == "estleg:RK_FIXT_VIIMSI_2009"
    )
    iris = [v["@id"] for v in decision.get("estleg:interpretsLaw", [])]
    assert "estleg:Reg_1024484_Map" in iris
    # State-law TsMS provision regression check.
    assert "estleg:Tsiviilkohtumenetluse_seadustik_Par_208" in iris

    # KOV act file: interpretedBy points back to the court decision.
    viimsi_doc = json.loads(
        (kov / "viimsi_vallavolikogu" / "maarus_22_t1024484_peep.json").read_text(encoding="utf-8")
    )
    viimsi_act = next(
        n for n in viimsi_doc["@graph"]
        if n["@id"] == "estleg:Reg_1024484_Map"
    )
    by = [v["@id"] for v in viimsi_act.get("estleg:interpretedBy", [])]
    assert "estleg:RK_FIXT_VIIMSI_2009" in by

    # State-law provision file: interpretedBy regression check.
    tsms_doc = json.loads(
        (krr / "regulations" / "tsiviilkohtumenetluse_seadustik_peep.json").read_text(encoding="utf-8")
    )
    tsms_par = next(
        n for n in tsms_doc["@graph"]
        if n["@id"] == "estleg:Tsiviilkohtumenetluse_seadustik_Par_208"
    )
    assert any(
        v["@id"] == "estleg:RK_FIXT_VIIMSI_2009"
        for v in tsms_par.get("estleg:interpretedBy", [])
    )
```

- [ ] **Step 3: Run** — Expected: 33 passed (31 existing + 2 new).

```bash
pytest tests/test_extract_court_provision_links.py -v
```

If failures, debug and fix before continuing.

- [ ] **Step 4: Commit**

```bash
git add tests/test_extract_court_provision_links.py tests/fixtures/kov_court_provision_links/
git commit -m "Add end-to-end fixture test for KOV court-provision-links pipeline"
```

---

## Task 13 — Idempotency test (Group 4a)

**Files:**
- Modify: `tests/test_extract_court_provision_links.py`

- [ ] **Step 1: Append the idempotency test**

```python
VOLATILE_KEYS = (
    "run_timestamp",
    "wall_time_seconds",
    "items_per_second",
    "peak_memory_mb",
)


def _stable(cov: dict) -> dict:
    return {k: v for k, v in cov.items() if k not in VOLATILE_KEYS}


def test_two_runs_byte_identical(tmp_path, monkeypatch) -> None:
    krr, rk, kov = _stage_fixture(tmp_path)
    cov1 = _run_script_against(krr, monkeypatch)
    # Snapshot every touched file's bytes after run 1.
    bytes1 = {p: p.read_bytes() for p in krr.rglob("*_peep.json")}

    cov2 = _run_script_against(krr, monkeypatch)
    bytes2 = {p: p.read_bytes() for p in krr.rglob("*_peep.json")}

    assert _stable(cov1) == _stable(cov2), \
        "coverage report differs across runs (modulo volatile fields)"
    assert bytes1 == bytes2, \
        "peep files differ across runs (idempotency violated)"
```

- [ ] **Step 2: Run** — Run the whole test file and confirm the new test passes alongside everything else from Tasks 3–12.

```bash
pytest tests/test_extract_court_provision_links.py -v
```

Expected: 34 passed (33 from Task 12 + 1 new idempotency test).

- [ ] **Step 3: Commit**

```bash
git add tests/test_extract_court_provision_links.py
git commit -m "Add idempotency test: two consecutive runs are byte-identical"
```

---

## Task 14 — SHACL widening in `estonian_legal_shapes.ttl`

**Files:**
- Modify: `shacl/estonian_legal_shapes.ttl:81-86` (LegalProvisionShape's `interpretedBy` description)
- Modify: `shacl/estonian_legal_shapes.ttl:390-395` (CourtDecisionShape's `interpretsLaw` description)
- Modify: `shacl/estonian_legal_shapes.ttl:820-828` (MunicipalRegulationShape — add `interpretedBy` property shape)

- [ ] **Step 1: Update `LegalProvisionShape.interpretedBy` description**

Replace lines 81–86:

```turtle
    sh:property [
        sh:path estleg:interpretedBy ;
        sh:nodeKind sh:IRI ;
        sh:name "interpretedBy" ;
        sh:description "Court decisions that interpret this provision" ;
    ] ;
```

With:

```turtle
    sh:property [
        sh:path estleg:interpretedBy ;
        sh:nodeKind sh:IRI ;
        sh:name "interpretedBy" ;
        sh:description """Court decisions that interpret this provision. The same predicate is also used on MunicipalRegulation act nodes for KOV act-level citations — the object remains a CourtDecision in both cases; the subject differs.""" ;
    ] ;
```

- [ ] **Step 2: Update `CourtDecisionShape.interpretsLaw` description**

Replace lines 390–395:

```turtle
    sh:property [
        sh:path estleg:interpretsLaw ;
        sh:nodeKind sh:IRI ;
        sh:name "interpretsLaw" ;
        sh:description "Specific legal provisions interpreted by this decision" ;
    ] ;
```

With:

```turtle
    sh:property [
        sh:path estleg:interpretsLaw ;
        sh:nodeKind sh:IRI ;
        sh:name "interpretsLaw" ;
        sh:description """Provisions interpreted by this decision; for KOV citations the target may be a MunicipalRegulation act node when provision-level resolution is unavailable.""" ;
    ] ;
```

- [ ] **Step 3: Add `interpretedBy` property shape on `MunicipalRegulationShape`**

Replace lines 820–828:

```turtle
estleg:MunicipalRegulationShape
    a sh:NodeShape ;
    sh:targetClass estleg:MunicipalRegulation ;
    sh:property [
        sh:path rdfs:label ;
        sh:minCount 1 ;
        sh:name "label" ;
        sh:description "Every MunicipalRegulation must have a label" ;
    ] .
```

With:

```turtle
estleg:MunicipalRegulationShape
    a sh:NodeShape ;
    sh:targetClass estleg:MunicipalRegulation ;
    sh:property [
        sh:path rdfs:label ;
        sh:minCount 1 ;
        sh:name "label" ;
        sh:description "Every MunicipalRegulation must have a label" ;
    ] ;
    sh:property [
        sh:path estleg:interpretedBy ;
        sh:nodeKind sh:IRI ;
        sh:name "interpretedBy" ;
        sh:description """Court decisions that interpret this municipal act (act-level KOV citation).""" ;
    ] .
```

(Note the `] .` on line 828 became `] ;` and a new `sh:property [ ... ] .` block follows. Failing to do this produces a Turtle parse error.)

- [ ] **Step 4: Verify Turtle parses**

```bash
python -c "
import rdflib
g = rdflib.Graph()
g.parse('shacl/estonian_legal_shapes.ttl', format='turtle')
print(f'OK: {len(g)} triples')
"
```

Expected: `OK: <N> triples` with no parse error.

- [ ] **Step 5: Run existing SHACL tests**

```bash
pytest tests/test_shacl_kov_layer1.py -v
```

Expected: all existing SHACL tests still pass.

- [ ] **Step 6: Commit**

```bash
git add shacl/estonian_legal_shapes.ttl
git commit -m "Widen SHACL interpretsLaw/interpretedBy and add MunicipalRegulationShape property"
```

---

## Task 15 — RDFS range widening + regenerate schema

**Files:**
- Modify: `scripts/generate_court_decisions.py:247-253`
- Regenerate: `krr_outputs/riigikohus/riigikohus_schema.json`

- [ ] **Step 1: Update the generator**

In `scripts/generate_court_decisions.py`, replace lines 246–253:

```python
        {
            "@id": "estleg:interpretsLaw",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "tõlgendab seadust", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {"@id": "estleg:LegalProvision"},
            "rdfs:comment": {"@value": "Links a court decision to the legal provision it interprets or applies.", "@language": "en"},
        },
```

With:

```python
        {
            "@id": "estleg:interpretsLaw",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "tõlgendab seadust", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:CourtDecision"},
            "rdfs:range": {
                "@type": "owl:Class",
                "owl:unionOf": {
                    "@list": [
                        {"@id": "estleg:LegalProvision"},
                        {"@id": "estleg:Act"},
                    ],
                },
            },
            "rdfs:comment": {
                "@value": "Links a court decision to the legal provision or whole act it interprets or applies.",
                "@language": "en",
            },
        },
```

- [ ] **Step 2: Regenerate ONLY the schema file**

`generate_court_decisions.py main()` does a lot more than write the schema — it fetches 1993–2026 court decisions from RIK and rewrites all `riigikohus_*_peep.json` files plus the index. We only need the schema. Call `generate_schema_nodes()` directly and write only the schema JSON:

```bash
python -c "
import json, sys
sys.path.insert(0, 'scripts')
from generate_court_decisions import generate_schema_nodes, CONTEXT
schema_doc = {'@context': CONTEXT, '@graph': generate_schema_nodes()}
with open('krr_outputs/riigikohus/riigikohus_schema.json', 'w', encoding='utf-8') as fh:
    json.dump(schema_doc, fh, ensure_ascii=False, indent=2)
    fh.write('\n')
print('Schema regenerated.')
"
```

Both `CONTEXT` and `generate_schema_nodes` are module-level in `scripts/generate_court_decisions.py` (lines 38 and 184). This invocation does not touch any `riigikohus_*_peep.json` data file — it's schema-only.

Inspect:

```bash
python -c "
import json
d = json.load(open('krr_outputs/riigikohus/riigikohus_schema.json'))
ip = next(n for n in d['@graph'] if n.get('@id') == 'estleg:interpretsLaw')
assert 'owl:unionOf' in str(ip['rdfs:range']), f'range not widened: {ip[\"rdfs:range\"]}'
print('OK: rdfs:range widened to owl:unionOf')
"
```

Expected: `OK: rdfs:range widened to owl:unionOf`.

- [ ] **Step 3: Commit**

```bash
git add scripts/generate_court_decisions.py krr_outputs/riigikohus/riigikohus_schema.json
git commit -m "Widen rdfs:range on estleg:interpretsLaw to owl:unionOf (LegalProvision Act)"
```

---

## Task 16 — Targeted SHACL test (Group 4b)

**Files:**
- Modify: `tests/test_extract_court_provision_links.py`

- [ ] **Step 1: Append the SHACL test**

```python
# ---------------------------------------------------------------------------
# Group 4b — targeted SHACL widening test
# ---------------------------------------------------------------------------

def test_shacl_widened_range_validates() -> None:
    pyshacl = pytest.importorskip("pyshacl")
    rdflib = pytest.importorskip("rdflib")

    repo_root = Path(__file__).resolve().parents[1]
    shapes = rdflib.Graph()
    shapes.parse(str(repo_root / "shacl" / "estonian_legal_shapes.ttl"), format="turtle")

    # CourtDecisionShape requires caseNumber (xsd:string, minCount 1) and
    # caseType (IRI, minCount 1). Provide both so we're exercising the
    # widening, not falling over on unrelated mandatory fields.
    data_jsonld = {
        "@context": {"estleg": "https://data.riik.ee/ontology/estleg#",
                     "rdfs":   "http://www.w3.org/2000/01/rdf-schema#"},
        "@graph": [
            {
                "@id": "estleg:RK_TEST_WIDEN_001",
                "@type": ["estleg:CourtDecision"],
                "rdfs:label": "Test decision (widened range)",
                "estleg:caseNumber": "TEST-WIDEN-001",
                "estleg:caseType": {"@id": "estleg:CaseType_Other"},
                "estleg:interpretsLaw": [
                    {"@id": "estleg:Some_Provision_Par_5"},
                    {"@id": "estleg:Reg_1024484_Map"},
                ],
            },
            {
                "@id": "estleg:Some_Provision_Par_5",
                "@type": ["estleg:LegalProvision"],
                "rdfs:label": "Test provision",
                "estleg:interpretedBy": [{"@id": "estleg:RK_TEST_WIDEN_001"}],
            },
            {
                "@id": "estleg:Reg_1024484_Map",
                "@type": ["estleg:MunicipalRegulation", "estleg:Act"],
                "rdfs:label": "Viimsi 2009 #22 (test)",
                "estleg:interpretedBy": [{"@id": "estleg:RK_TEST_WIDEN_001"}],
            },
        ],
    }
    data = rdflib.Graph()
    data.parse(data=json.dumps(data_jsonld), format="json-ld")

    conforms, _, msg = pyshacl.validate(
        data, shacl_graph=shapes, inference="rdfs", abort_on_first=False,
    )
    assert conforms, f"SHACL validation failed:\n{msg}"


def test_shacl_municipal_regulation_shape_has_interpreted_by_property() -> None:
    """Direct shapes-graph assertion that pins the new SHACL change.

    SHACL allows extra properties by default — a CourtDecision pointing at
    a MunicipalRegulation passes validation even WITHOUT the new
    MunicipalRegulationShape interpretedBy property. To pin the new shape
    explicitly, query the loaded shapes graph for a property shape on
    MunicipalRegulationShape with sh:path estleg:interpretedBy.
    """
    rdflib = pytest.importorskip("rdflib")
    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    ESTLEG = rdflib.Namespace("https://data.riik.ee/ontology/estleg#")

    repo_root = Path(__file__).resolve().parents[1]
    shapes = rdflib.Graph()
    shapes.parse(str(repo_root / "shacl" / "estonian_legal_shapes.ttl"), format="turtle")

    matched = False
    for prop_shape in shapes.objects(ESTLEG.MunicipalRegulationShape, SH.property):
        for path in shapes.objects(prop_shape, SH.path):
            if path == ESTLEG.interpretedBy:
                matched = True
                break
        if matched:
            break

    assert matched, (
        "MunicipalRegulationShape is missing the sh:path estleg:interpretedBy "
        "property shape introduced in Layer 2c PR #3."
    )
```

(`pytest` is already imported at the top of the file from Task 3.)

- [ ] **Step 2: Run** — Run the full file so the count reflects the cumulative state.

```bash
pytest tests/test_extract_court_provision_links.py -v
```

Expected: 36 passed (34 from Task 13 + 2 new SHACL tests).

If pyshacl/rdflib aren't installed, the SHACL tests skip via `importorskip` (the count drops to 34 + skips, not failures). The second test (`test_shacl_municipal_regulation_shape_has_interpreted_by_property`) does NOT require pyshacl — it only inspects the parsed shapes graph — so it runs whenever rdflib is available.

- [ ] **Step 3: Commit**

```bash
git add tests/test_extract_court_provision_links.py
git commit -m "Add targeted SHACL test pinning widened interpretsLaw range and MunicipalRegulation interpretedBy"
```

---

## Task 17 — Real-data validation run

This is a hands-on verification task — no new tests, no commit.

- [ ] **Step 1: Run the full pipeline against real data**

```bash
# Capture full output to a log file FIRST, then tail. Don't pipe directly
# to tail/tee — under `set -o pipefail` it's fine, but capturing first
# means we can rerun analysis on the log without rerunning the script.
python scripts/extract_court_provision_links.py >/tmp/cpl_run.log 2>&1
echo "exit=$?"
tail -40 /tmp/cpl_run.log
```

The `exit=$?` line MUST print `exit=0`. If the script exited non-zero, debug from the log before continuing.

Expected output ends with the SUMMARY block and the KOV citation summary, with `KOV citations resolved: 5` (give or take, depending on corpus drift).

- [ ] **Step 2: Inspect the coverage report**

```bash
python -c "
import json
d = json.load(open('krr_outputs/reports/kov/court_provision_links_coverage.json'))
print('triples_emitted_kov:           ', d['triples_emitted_kov'])
print('unresolved_references:         ', d['unresolved_references'])
print('skip_reasons.kov_ambiguous:    ', d['skip_reasons']['kov_citation_ambiguous'])
print('skip_reasons.unknown_issuer:   ', d['skip_reasons']['kov_citation_unknown_issuer'])
print('skip_reasons.rtiv_form:        ', d['skip_reasons']['rtiv_form_citation'])
print('files_processed:               ', d['files_processed'])
print('files_processed_kov:           ', d['files_processed_kov'])
assert d['triples_emitted_kov'] >= 1
assert d['files_processed_kov'] <= d['files_processed']
print('OK')
"
```

Expected: `triples_emitted_kov >= 1` (empirical baseline = 5).

- [ ] **Step 3: Run JSON hygiene + a targeted SHACL validation over modified outputs**

`scripts/validate_all.py` only checks JSON hygiene (id uniqueness, type-array invariants), NOT SHACL. The existing `scripts/shacl_validate_all.py` runs pyshacl, but its `collect_files()` doesn't include `krr_outputs/riigikohus/riigikohus_*_peep.json` — so neither built-in script validates the data this PR actually changes. Run JSON hygiene as a regression check, then run a targeted pyshacl invocation that loads the SHACL shapes plus the changed Riigikohus peeps and a sample of the touched KOV peeps.

```bash
# Regression: JSON hygiene must still be clean. Fail-fast: if validate_all
# exits non-zero, print the tail of the log and bail with non-zero status
# so a CI step / pasted shell block surfaces the failure.
python scripts/validate_all.py >/tmp/cpl_validate_all.log 2>&1 || {
    tail -30 /tmp/cpl_validate_all.log
    echo "FAIL: validate_all.py reported issues"
    exit 1
}
tail -10 /tmp/cpl_validate_all.log
echo "OK: validate_all.py clean"
```

Then run a one-off SHACL check over actual modified data:

```bash
python -c "
import json, sys
import pyshacl, rdflib
from pathlib import Path

shapes = rdflib.Graph()
shapes.parse('shacl/estonian_legal_shapes.ttl', format='turtle')

data = rdflib.Graph()
# Load every Riigikohus peep — this PR may have written interpretsLaw to any of them.
for f in sorted(Path('krr_outputs/riigikohus').glob('riigikohus_*_peep.json')):
    data.parse(str(f), format='json-ld')

# Load every KOV peep that received an interpretedBy in this run (read the
# coverage report's failure_samples + run-time logs to know which IRIs;
# simplest is just to load all KOV peeps that carry interpretedBy).
for f in Path('krr_outputs/regulations/kov').rglob('*_peep.json'):
    try:
        doc = json.load(open(f, encoding='utf-8'))
    except Exception:
        continue
    if any('estleg:interpretedBy' in n for n in doc.get('@graph', [])):
        data.parse(str(f), format='json-ld')

# State-law peeps with interpretedBy added in this run
for f in Path('krr_outputs/regulations').glob('*_peep.json'):
    try:
        doc = json.load(open(f, encoding='utf-8'))
    except Exception:
        continue
    if any('estleg:interpretedBy' in n for n in doc.get('@graph', [])):
        data.parse(str(f), format='json-ld')

print(f'Data graph: {len(data)} triples')
conforms, _, msg = pyshacl.validate(
    data, shacl_graph=shapes, inference='rdfs', abort_on_first=False,
)
if not conforms:
    print('FAIL: SHACL violations:')
    print(msg)
    sys.exit(1)
print('OK: SHACL validation passed over modified court decisions and KOV/state targets.')
" >/tmp/cpl_shacl.log 2>&1 || {
    tail -40 /tmp/cpl_shacl.log
    echo "FAIL: SHACL validation reported violations"
    exit 1
}
tail -10 /tmp/cpl_shacl.log
echo "OK: pyshacl validation passed"
```

Expected: both blocks end with `OK: ...`. Either failing causes the shell block to exit non-zero, surfacing the issue rather than silently continuing.

- [ ] **Step 4: Run the full test suite**

```bash
pytest -q
```

Expected: 255 passed (206 baseline + 49 new across three test files: 6 in `test_estleg_common_normalize.py` + 7 in `test_estleg_common_run_counters.py` + 36 in `test_extract_court_provision_links.py`). If anything fails, debug and fix.

---

## Task 18 — Stage and commit generated outputs

**Files:**
- Add: `krr_outputs/reports/kov/court_provision_links_coverage.json` (NEW per-pipeline report).
- Modify: `krr_outputs/court_provision_links_report.json` (existing per-file report; gains KOV summary fields).
- Modify: `krr_outputs/riigikohus/riigikohus_*_peep.json` (5 files actually written; ~34 visited and idempotency-cleared).
- Modify: subset of `krr_outputs/regulations/*_peep.json` (state-law provisions gaining `interpretedBy`).
- Modify: subset of `krr_outputs/regulations/kov/**/*_peep.json` (KOV act peeps gaining `interpretedBy`).

The PR includes the generated data because the predecessor PRs (#100 sanctions, #101 competence) committed their generated outputs and coverage reports — reviewers compare the diff against those baselines.

- [ ] **Step 1: Inspect what changed**

```bash
git status -s | head -40
echo "---"
git status -s | wc -l
```

Expected: a relatively small number of changed files (the 5 actually-resolved citations land on 5 court files + 5 KOV act files + a handful of state-law provision files; cleanup walks visit thousands of files but only modify those that were already written or are now being written).

- [ ] **Step 2: Sanity-check the diff**

```bash
git diff --stat krr_outputs/ | tail -20
```

Expected: small diff sizes per file (`interpretsLaw` / `interpretedBy` arrays added or pruned). No surprising deletions of unrelated triples.

Spot-check one resolved court decision:

```bash
git diff krr_outputs/riigikohus/ | head -60
```

Expected: the diff shows new `"estleg:interpretsLaw"` arrays containing one or more `MunicipalRegulation` IRIs.

- [ ] **Step 3: Stage the generated outputs (precise file list)**

Don't `git add` whole trees — the worktree may carry unrelated dirty generated outputs from a prior run. Instead, derive an explicit file list from `git diff --name-only` for the two output trees, sanity-check it, then stage exactly those paths plus the two top-level reports.

```bash
# 1) Capture the changed-files list scoped to the two output trees this PR touches.
git diff --name-only krr_outputs/riigikohus krr_outputs/regulations \
    > /tmp/cpl_changed.txt
echo "Changed-file count under krr_outputs/{riigikohus,regulations}:"
wc -l /tmp/cpl_changed.txt
echo "First 15 entries (sanity-check none look unrelated):"
head -15 /tmp/cpl_changed.txt

# 2) Stage exactly that file list (xargs -a reads from file; -r skips empty).
xargs -a /tmp/cpl_changed.txt -r git add --

# 3) Stage the two top-level reports explicitly. The coverage report is
#    new (under reports/kov/) so `git add` registers it; the legacy
#    report is a tracked file with diffs.
git add krr_outputs/reports/kov/court_provision_links_coverage.json
git add krr_outputs/court_provision_links_report.json
```

If `head -15 /tmp/cpl_changed.txt` shows ANY file that isn't a court decision peep, a state-law peep gaining `interpretedBy`, or a KOV act peep gaining `interpretedBy`, STOP — investigate before staging. Likely culprits: another pipeline left dirty state, or an editor created backup files.

- [ ] **Step 4: Confirm the staged set is what you expect**

```bash
git diff --cached --stat | tail -20
```

Reviewer should see: 1 new coverage report, 1 modified umbrella report, ≤ 5 modified court peep files (the count of decisions that actually got `interpretsLaw` writes), ≤ 5 KOV act files (the resolutions), and a handful of state-law provision files (the existing-link refresh).

- [ ] **Step 5: Commit the generated outputs**

```bash
git commit -m "Update generated outputs: KOV act-level interpretsLaw + coverage report

Real-data outputs from running extract_court_provision_links.py against
the full corpus after Layer 2c PR #3:
- krr_outputs/reports/kov/court_provision_links_coverage.json (NEW)
- krr_outputs/court_provision_links_report.json (KOV summary fields added)
- 5 court decisions gain interpretsLaw arcs to KOV act IRIs
- Corresponding KOV act peeps gain interpretedBy
- State-law provision interpretedBy refreshed (idempotent re-run)"
```

---

## Task 19 — Documentation deltas

**Files:**
- Modify: `CHANGELOG.md` (add Unreleased entry)
- Modify: `docs/SCHEMA_REFERENCE.md` (note widened range)

- [ ] **Step 1: CHANGELOG entry**

In `CHANGELOG.md`, under the Unreleased section, add:

```markdown
### KOV integration Layer 2c PR #3 — Court-Provision Links (closes Gate B)

- `extract_court_provision_links.py` now resolves KOV act-level citations
  in Riigikohus court decision summaries to `MunicipalRegulation` IRIs,
  alongside the existing state-law provision-level resolution.
- `estleg:interpretsLaw` formal range widened to
  `owl:unionOf (LegalProvision Act)` so it admits `MunicipalRegulation`
  (Act) IRIs in addition to `LegalProvision`.
- `estleg:interpretedBy` usage broadens: the predicate now also appears
  on `MunicipalRegulation` act nodes (subject side); the object remains
  a `CourtDecision` IRI in all cases. No formal `rdfs:range` is declared
  for `interpretedBy` in this PR. SHACL `MunicipalRegulationShape` gains
  a parallel `interpretedBy` property shape (`sh:nodeKind sh:IRI`).
- Formal RDFS range on `estleg:interpretsLaw` widened to
  `owl:unionOf (LegalProvision Act)` in `riigikohus_schema.json`.
- New per-pipeline coverage report at
  `krr_outputs/reports/kov/court_provision_links_coverage.json`.
- New helpers in `estleg_common.py`: `BODY_CANON`, `normalize_issuer_name`,
  `_RunCounters`, `_safe_load`.
- Empirical impact: ~5 new `interpretsLaw` edges with KOV act IRI targets;
  ~29 unresolved historical citations surfaced in `failure_samples`.
- RT IV citation resolver deferred — counted only under
  `skip_reasons["rtiv_form_citation"]`.

**Gate B closes.** All ten KOV-relevant pipelines now run cleanly over
the full KOV set with per-pipeline coverage reports.
```

- [ ] **Step 2: SCHEMA_REFERENCE entries (three places)**

In `docs/SCHEMA_REFERENCE.md`:

**(a)** Replace the bullet at line 186:

```markdown
* `estleg:interpretsLaw`: Object property linking to LegalProvision being interpreted — `owl:ObjectProperty`
```

with:

```markdown
* `estleg:interpretsLaw`: Object property linking to a `LegalProvision` (state-law provision) OR a `MunicipalRegulation` / `Act` (KOV act-level citation) interpreted by this decision — `owl:ObjectProperty`. Range is `owl:unionOf (LegalProvision Act)` since Layer 2c PR #3.
```

**(b)** Replace the table row at line 549:

```markdown
| `estleg:interpretsLaw` | CourtDecision | LegalProvision (IRI) | Specific provision interpreted |
```

with:

```markdown
| `estleg:interpretsLaw` | CourtDecision | LegalProvision \| MunicipalRegulation/Act (IRI) | Specific provision (state law) or whole act (KOV) interpreted. `owl:unionOf` since Layer 2c PR #3. |
```

**(c)** Replace the table row at line 550:

```markdown
| `estleg:interpretedBy` | LegalProvision | CourtDecision (IRI) | Inverse: court decisions interpreting this provision |
```

with:

```markdown
| `estleg:interpretedBy` | LegalProvision \| MunicipalRegulation (informal) | CourtDecision (IRI) | Inverse of `interpretsLaw`. The object is always a `CourtDecision`; the subject is a `LegalProvision` for state-law citations, and may also be a `MunicipalRegulation` act node for KOV act-level citations (since Layer 2c PR #3). No formal `rdfs:range` is declared (range stays unconstrained beyond `sh:nodeKind sh:IRI`); SHACL `MunicipalRegulationShape` adds a parallel `sh:nodeKind sh:IRI` constraint to document the new subject usage. |
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/SCHEMA_REFERENCE.md
git commit -m "Document Layer 2c PR #3 (court-provision-links) and Gate B closure"
```

---

## Task 20 — Open the pull request

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feature/kov-layer2c-court-links
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main --title "KOV integration Layer 2c PR #3 — Court-Provision Links (closes Gate B)" --body "$(cat <<'EOF'
## Summary

- Adds an act-level KOV citation resolver to `extract_court_provision_links.py`. Court decisions citing municipal regulations in the form `{Municipality} {Body} {date} määrus nr {N}` now resolve to `MunicipalRegulation` IRIs alongside the existing state-law provision-level resolution.
- Widens `estleg:interpretsLaw` formal `rdfs:range` to `owl:unionOf (LegalProvision Act)` so court decisions can point at either a `LegalProvision` or a `MunicipalRegulation` act node. Broadens `estleg:interpretedBy` *subject* usage so the predicate now also appears on `MunicipalRegulation` nodes (object remains a `CourtDecision` IRI; no `rdfs:range` added for `interpretedBy`). SHACL stays `sh:nodeKind sh:IRI` (no `sh:class`); `MunicipalRegulationShape` gains a parallel `interpretedBy` property shape.
- New per-pipeline coverage report `krr_outputs/reports/kov/court_provision_links_coverage.json`.

**Closes Gate B.** All ten KOV-relevant pipelines now run cleanly over the full KOV set with per-pipeline coverage reports.

**Empirical impact:** 35 KOV-form citations matched, 5 resolved, 29 unresolved (historical/defunct), 1 ambiguous, 0 RT IV.

**Spec:** `docs/superpowers/specs/2026-05-05-kov-integration-layer2c-court-provision-links-design.md` (v4).
**Plan:** `docs/superpowers/plans/2026-05-05-kov-integration-layer2c-court-provision-links.md`.

## Test plan

- [x] `pytest tests/test_estleg_common_normalize.py` — BODY_CANON + normalize_issuer_name.
- [x] `pytest tests/test_estleg_common_run_counters.py` — _RunCounters + _safe_load (incl. dedup).
- [x] `pytest tests/test_extract_court_provision_links.py` — regex (incl. over-capture cases), resolver, end-to-end fixture, idempotency, SHACL widening.
- [x] `python scripts/extract_court_provision_links.py` on full corpus — `triples_emitted_kov ≥ 1`, coverage report shape stable.
- [x] `python scripts/validate_all.py` — JSON hygiene clean (id uniqueness, type-array invariants). Note: this script does NOT run SHACL.
- [x] Targeted pyshacl validation over the modified court-decision peeps and the state-law/KOV peeps that gained `interpretedBy` — zero SHACL violations on `LegalProvisionShape`, `CourtDecisionShape`, and the new `MunicipalRegulationShape` `interpretedBy` property. (Run-recipe in Task 17 step 3 of the plan.)
- [x] `pytest -q` — full test suite green at 255 passing.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Verify CI**

Watch the PR's CI checks. Address any failures before requesting review.

---

## End-state expectations

After all tasks complete and the PR is merged:

- 255 tests passing (206 baseline + 49 new across `test_estleg_common_normalize.py`, `test_estleg_common_run_counters.py`, and `test_extract_court_provision_links.py`).
- `triples_emitted_kov ≥ 1` in production coverage report.
- Gate B closed: every KOV-relevant pipeline emits a per-pipeline coverage report at `krr_outputs/reports/kov/<pipeline>_coverage.json`.
- The `iter_peep_files(include_kov=False)` pin on `generate_similarity_index` (established in Layer 2a) remains in place; it's lifted only by Gate C's act-level KOV similarity work.
