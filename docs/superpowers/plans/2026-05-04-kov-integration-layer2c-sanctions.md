# KOV Integration Layer 2c — Sanctions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unpin `extract_sanctions.py` so it processes the full corpus (laws + state regulations + KOV acts), and stamp `estleg:enforcedAtLevel` on every provision that carries a sanction — classified purely by parent act type (`MunicipalRegulation` → `"municipality"`, everything else → `"state"`).

**Architecture:** A small classification helper (`_classify_enforcement_level`) keyed off the act node's `@type` field; a `_find_act_node(doc)` helper that returns the `estleg:Act` + `owl:Ontology`-typed node from the file's `@graph`. The existing global pre-clear pass is replaced with per-file in-memory processing that detects pre-existing output, clears unconditionally, runs extraction, and saves when EITHER fresh output exists OR pre-existing output existed — including DELETING the corresponding `krr_outputs/sanctions/sanctions_<law>.json` when an act loses all its sanctions between runs. Coverage instrumentation reuses the Layer 2a `kov_pipeline_coverage.CoverageReport` helper.

**Tech Stack:** Python 3.11+, pytest, JSON-LD, SHACL (pyshacl, rdflib). Builds on Layer 2a's `kov_pipeline_coverage` helper and Layer 2b's per-file idempotency convention (`_process_preamble_for_act`-style helper from `extract_cross_references.py`).

**Spec reference:** `docs/superpowers/specs/2026-05-04-kov-integration-layer2c-sanctions-design.md`.
**Predecessors:** Layer 2a (#98, merged), Layer 2b (#99, merged 2026-05-04).

---

## Crisp Layer 2c PR #1 invariant

After this PR lands:

1. `extract_sanctions.py` calls `iter_peep_files()` with no `include_kov=False` argument. The single `# DEFERRED to Layer 2c` pin (line 510) is removed.
2. Every provision that gains an `estleg:hasSanction` triple in this run ALSO gains exactly ONE `estleg:enforcedAtLevel` literal.
3. `enforcedAtLevel` value is `"municipality"` iff the parent act has `estleg:MunicipalRegulation` in its `@type` list; otherwise `"state"`.
4. `LegalProvisionShape` in `shacl/estonian_legal_shapes.ttl` gains a property constraint for `enforcedAtLevel` (`xsd:string`, `sh:maxCount 1`, `sh:in ("state" "municipality")`).
5. Re-runs are idempotent at BOTH layers:
   - Provision-level: `hasSanction` + `enforcedAtLevel` are recomputed each run.
   - Sanctions-file-level: when an act loses all matches, the corresponding `krr_outputs/sanctions/sanctions_<law>.json` file is **deleted**.
6. Coverage report `krr_outputs/reports/kov/extract_sanctions_coverage.json` shows `files_with_output_kov > 0` and `triples_emitted_kov > 0`.
7. `main()` returns `int`; `raise SystemExit(main())` propagates the gate's exit code.
8. Targeted SHACL: zero violations against the new `enforcedAtLevel` constraint.

**Real-world impact estimate** (read-only dry count over the current corpus): ~82 KOV files gain sanction triples, ~85 sanction records.

**Not in this PR:** `extract_institutional_competence` (Layer 2c PR #2), `extract_court_provision_links` (Layer 2c PR #3).

---

## Worktree setup (do this first)

This plan executes in an isolated git worktree. From the main checkout `/Users/henrikaavik/progemoge/law-ontology`:

```bash
git worktree add .worktrees/kov-layer2c-sanctions -b feature/kov-layer2c-sanctions
cd .worktrees/kov-layer2c-sanctions
```

The XML data directory `data/riigiteataja/` is gitignored (8.6 GB). The worktree gets a stub copy. Replace with a symlink to the main checkout:

```bash
rm -rf data/riigiteataja
ln -s /Users/henrikaavik/progemoge/law-ontology/data/riigiteataja data/riigiteataja
```

(Same workaround Layer 2b used. The symlink is fragile across destructive worktree operations — re-create if it disappears.)

Confirm baseline tests pass:

```bash
pytest -q
```

Expected: 191 passed (Layer 2b end-state).

---

## File Structure

### New files

| Path | Responsibility |
| --- | --- |
| `tests/test_extract_sanctions.py` | 15 tests across 5 classes covering classification, integration, idempotency (provision + sanctions-file), coverage report, corpus-wide invariant. |

### Modified files

| Path | Change |
| --- | --- |
| `scripts/extract_sanctions.py` | Add `_find_act_node` + `_classify_enforcement_level` helpers. Replace global pre-clear (`_clear_existing_sanctions`) with per-file in-memory processing. Emit `enforcedAtLevel` alongside `hasSanction` (one literal per provision). Delete stale sanctions JSON when fresh output is empty. Add coverage instrumentation reusing `kov_pipeline_coverage.CoverageReport`. Change `main()` to return `int`. Use `raise SystemExit(main())`. Remove `include_kov=False` pin. |
| `shacl/estonian_legal_shapes.ttl` | Add property constraint inside the existing `LegalProvisionShape`: `sh:path estleg:enforcedAtLevel ; sh:datatype xsd:string ; sh:maxCount 1 ; sh:in ("state" "municipality") ; sh:name "enforcedAtLevel" ; sh:description "..."`. |
| `docs/SCHEMA_REFERENCE.md` | Mark `estleg:enforcedAtLevel` populated; add example block in the existing Sanctions section AND in the Layer 2 schema table; document the `sh:in` constraint. |
| `CHANGELOG.md` | Layer 2c sub-PR-1 entry: sanctions unpinned, SHACL constraint added, coverage numbers, KOV impact. |

---

## Schema reference (already declared in Layer 2a; this PR populates and constrains)

| Property | Domain | Range | Cardinality | New SHACL constraint |
| --- | --- | --- | --- | --- |
| `estleg:enforcedAtLevel` | `LegalProvision` | `xsd:string` | exactly 1 when `hasSanction` is present | `sh:in ("state" "municipality")` |

**Value semantics:**
- `"municipality"` — parent act is `estleg:MunicipalRegulation`.
- `"state"` — parent act is anything else (`Law`, `NationalRegulation`, `GovernmentRegulation`, `MinisterialRegulation`).

---

## Task 1: Add `_find_act_node` + `_classify_enforcement_level` helpers (TDD)

**Files:**
- Modify: `scripts/extract_sanctions.py` (add helpers near top of module, after the imports section, before the existing constants).
- Create: `tests/test_extract_sanctions.py` (new file with `TestClassifyEnforcementLevel` class).

- [ ] **Step 1: Create `tests/test_extract_sanctions.py` with the 4 unit tests for `TestClassifyEnforcementLevel`**

```python
"""Tests for scripts/extract_sanctions.py — Layer 2c additions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


class TestClassifyEnforcementLevel:
    """Unit tests for the act-type → enforcement-level classification.

    The rule: estleg:MunicipalRegulation → "municipality"; anything
    else → "state". @type may be either str or list — both must be
    handled.
    """

    def test_municipal_regulation_returns_municipality(self):
        from extract_sanctions import _classify_enforcement_level
        act = {
            "@id": "estleg:Reg_TLN_15_Map_2020",
            "@type": ["owl:Ontology", "estleg:Act",
                       "estleg:MunicipalRegulation"],
        }
        assert _classify_enforcement_level(act) == "municipality"

    def test_law_returns_state(self):
        from extract_sanctions import _classify_enforcement_level
        act = {
            "@id": "estleg:KOKS_Map_2026",
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
        }
        assert _classify_enforcement_level(act) == "state"

    def test_government_regulation_returns_state(self):
        from extract_sanctions import _classify_enforcement_level
        act = {
            "@id": "estleg:Reg_VV251_Map_2007",
            "@type": ["owl:Ontology", "estleg:Act",
                       "estleg:GovernmentRegulation"],
        }
        assert _classify_enforcement_level(act) == "state"

    def test_string_type_normalised(self):
        """@type as a bare string (not list) must be handled."""
        from extract_sanctions import _classify_enforcement_level
        # Edge case — Layer 1 generators always emit lists, but the
        # JSON-LD spec allows strings, so the helper must be robust.
        act_string_type = {
            "@id": "estleg:Reg_X",
            "@type": "estleg:MunicipalRegulation",
        }
        assert _classify_enforcement_level(act_string_type) == "municipality"
```

- [ ] **Step 2: Run the test (expect ImportError)**

Run: `pytest tests/test_extract_sanctions.py::TestClassifyEnforcementLevel -v`
Expected: FAIL with `ImportError: cannot import name '_classify_enforcement_level' from 'extract_sanctions'`.

- [ ] **Step 3: Add `_find_act_node` and `_classify_enforcement_level` to `scripts/extract_sanctions.py`**

Place these helpers immediately after the existing `_provision_ref` function (around line 130, before `# ---------- sanction pattern definitions ----------`). Add this code:

```python
def _find_act_node(doc: dict) -> dict | None:
    """Return the act node from a peep file's @graph, or None if no
    act node is present.

    Match criteria: the node must be typed BOTH `estleg:Act` and
    `owl:Ontology`. Some peep files contain auxiliary `owl:Ontology`
    wrappers (concept-cluster manifests etc.) that aren't acts; the
    `estleg:Act` constraint prevents misclassifying those as
    sanction-bearing acts. Layer 1 generators stamp every real act
    node with both types.

    Callers must handle a None return — the per-file processing
    treats it as a malformed-input skip (logged to the coverage
    report's failure_samples + skip_reasons['missing_act_node']).
    """
    for n in doc.get("@graph", []):
        types = n.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:Act" in types and "owl:Ontology" in types:
            return n
    return None


def _classify_enforcement_level(act_node: dict) -> str:
    """Classify the enforcement level of an act node by its @type.

    Returns "municipality" iff the act is typed
    estleg:MunicipalRegulation; otherwise "state".

    Handles @type as either a string or a list of strings.
    """
    types = act_node.get("@type") or []
    if isinstance(types, str):
        types = [types]
    if "estleg:MunicipalRegulation" in types:
        return "municipality"
    return "state"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extract_sanctions.py::TestClassifyEnforcementLevel -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `pytest -q`
Expected: 191 + 4 = 195 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_sanctions.py tests/test_extract_sanctions.py
git commit -m "Add act-type classification helpers for Layer 2c sanctions

_find_act_node finds the estleg:Act + owl:Ontology-typed node in a
peep file's @graph (rejects auxiliary owl:Ontology wrappers).
_classify_enforcement_level returns 'municipality' for
MunicipalRegulation acts and 'state' for everything else. Both
handle @type as either str or list per JSON-LD spec.

Pure functions; no side effects; no caller wired up yet."
```

---

## Task 2: Wire `enforcedAtLevel` emission into the sanction extraction pass (TDD)

**Files:**
- Modify: `scripts/extract_sanctions.py` lines ~537-600 (the per-provision loop inside `main()`).
- Modify: `tests/test_extract_sanctions.py` (add `TestSanctionExtractionWithEnforcementStamp` class).

The existing per-provision loop already calls `extract_sanctions(summary)` and assembles `sanction_refs`. This task adds two changes inside that loop:
1. Find the act node ONCE per file (immediately after loading the doc) using `_find_act_node`.
2. When `sanction_refs` is non-empty for a provision, also set `node["estleg:enforcedAtLevel"]`.

- [ ] **Step 1: Append `TestSanctionExtractionWithEnforcementStamp` to `tests/test_extract_sanctions.py`**

```python
def _iter_kov_inclusive(*args, **kwargs):
    """Wrapper that forces include_kov=True regardless of caller args.

    Tasks 2-5 are scheduled BEFORE the unpin in Task 6, so production
    main() still calls iter_peep_files(include_kov=False) at this
    point. Tests that stage KOV fixtures must monkeypatch
    `mod.iter_peep_files` with this wrapper so KOV-staged peeps are
    visible regardless of whether the production unpin has landed yet.
    After Task 6, production defaults to include_kov=True and the
    wrapper becomes a no-op — but keep it in the tests so they remain
    self-contained and independent of task-execution order.
    """
    from estleg_common import iter_peep_files as _real_iter
    kwargs.pop("include_kov", None)
    return _real_iter(include_kov=True, **kwargs)


class TestSanctionExtractionWithEnforcementStamp:
    """Integration tests verifying enforcedAtLevel lands on every
    provision that gains hasSanction, with one literal per provision
    regardless of sanction count."""

    @pytest.fixture
    def sample_kov_peep(self, tmp_path):
        """A KOV act with one provision that triggers a fine sanction.

        Uses Estonian text the existing extract_sanctions regex
        already recognises ('väärtegu, mille eest on ette nähtud
        rahatrahv kuni X trahviühikut').
        """
        krr = tmp_path / "krr_outputs"
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        peep = kov / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_15_Map_2020",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "rdfs:label": "Tallinna parkimismaks"},
                {"@id": "estleg:Reg_TLN_15_Map_2020_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Parkimisreeglite rikkumise eest, mis on "
                     "väärtegu, mille eest on ette nähtud rahatrahv "
                     "kuni 100 trahviühikut."},
            ],
        }), encoding="utf-8")
        return peep

    @pytest.fixture
    def sample_state_peep(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        peep = krr / "alkoholiseadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:AS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Alkoholiseadus"},
                {"@id": "estleg:AS_Par_42",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 42",
                 "estleg:summary":
                     "Alkoholi tarvitamise piirangute rikkumise eest, "
                     "mis on väärtegu, mille eest on ette nähtud "
                     "rahatrahv kuni 200 trahviühikut."},
            ],
        }), encoding="utf-8")
        return peep

    def test_kov_act_sanctions_get_municipality_stamp(
        self, sample_kov_peep, tmp_path, monkeypatch
    ):
        """KOV act with a fine-bearing provision → provision gains
        BOTH estleg:hasSanction AND
        estleg:enforcedAtLevel = 'municipality'."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir()
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        # Production main() still calls iter_peep_files(include_kov=False)
        # at this point in the task order; force KOV inclusion so the
        # staged KOV fixture is visible.
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(sample_kov_peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_1"))
        assert "estleg:hasSanction" in prov
        assert prov.get("estleg:enforcedAtLevel") == "municipality"

    def test_state_law_sanctions_get_state_stamp(
        self, sample_state_peep, tmp_path, monkeypatch
    ):
        """Law-typed act → provision gains 'state' stamp."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir()
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        # State-only fixture, but use the wrapper for consistency
        # with the other tests in this class.
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(sample_state_peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_42"))
        assert "estleg:hasSanction" in prov
        assert prov.get("estleg:enforcedAtLevel") == "state"

    def test_provision_with_no_sanction_gets_no_enforcement_stamp(
        self, tmp_path, monkeypatch
    ):
        """Provision whose summary has no sanction pattern does NOT
        gain estleg:enforcedAtLevel. Preserves the 'only on
        sanction-bearing provisions' rule."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir()
        peep = krr / "ametiuhingute_seadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:AmS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:AmS_Par_5",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 5",
                 "estleg:summary":
                     "Ametiühingu eesmärk on töötajate huvide "
                     "kaitsmine ning töötingimuste parandamine."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_5"))
        assert "estleg:hasSanction" not in prov
        assert "estleg:enforcedAtLevel" not in prov

    def test_provision_with_multiple_sanctions_gets_one_enforcement_literal(
        self, tmp_path, monkeypatch
    ):
        """A provision that emits MULTIPLE Sanction_* records still
        gains exactly ONE estleg:enforcedAtLevel literal (a bare
        string, NOT a list of repeated values)."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir()
        peep = krr / "karistusseadustik_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:KARIST_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:KARIST_Par_113",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 113",
                 "estleg:summary":
                     "Tervisekahjustuse tekitamise eest karistatakse "
                     "rahalise karistusega või kuni üheaastase "
                     "vangistusega."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id", "").endswith("_Par_113"))
        # hasSanction is a list of multiple sanction refs
        sanctions = prov.get("estleg:hasSanction", [])
        if isinstance(sanctions, dict):
            sanctions = [sanctions]
        assert len(sanctions) >= 2, f"expected ≥2 sanctions, got {sanctions}"
        # enforcedAtLevel is ONE bare literal — not a list
        level = prov.get("estleg:enforcedAtLevel")
        assert isinstance(level, str), (
            f"expected str literal, got {type(level).__name__}: {level!r}"
        )
        assert level == "state"
```

- [ ] **Step 2: Run the new tests (expect 3 failures + 1 pass — emission isn't wired yet)**

Run: `pytest tests/test_extract_sanctions.py::TestSanctionExtractionWithEnforcementStamp -v`
Expected: 3 failures + 1 pass:
- `test_kov_act_sanctions_get_municipality_stamp` — FAIL (no `enforcedAtLevel` emitted yet)
- `test_state_law_sanctions_get_state_stamp` — FAIL (no `enforcedAtLevel` emitted yet)
- `test_provision_with_no_sanction_gets_no_enforcement_stamp` — **PASS** (the assertion is "should NOT have enforcedAtLevel"; before emission is wired, it's correctly absent for free)
- `test_provision_with_multiple_sanctions_gets_one_enforcement_literal` — FAIL (no `enforcedAtLevel` emitted yet)

Don't waste time hunting for the fourth failure — the negative-assertion test is well-formed and just happens to be vacuously satisfied by the pre-emission state. After Step 3 lands, all four will pass.

- [ ] **Step 3: Modify the per-provision loop in `scripts/extract_sanctions.py` to emit `enforcedAtLevel`**

Find the existing loop in `main()` around lines 526-600. The structure is:

```python
for idx, filepath in enumerate(law_files, 1):
    doc = load_json(filepath)
    if doc is None or "@graph" not in doc:
        continue
    # ... per-provision loop that builds sanction_refs ...
    if law_sanctions:
        save_json(filepath, doc)
        all_law_sanctions[law_name] = law_sanctions
```

Modify it to:
1. Find the act node ONCE per file via `_find_act_node`.
2. Pre-classify the file's enforcement level.
3. Stamp `enforcedAtLevel` whenever `sanction_refs` is non-empty.

Specifically, locate the block at lines 526-540 (after `for idx, filepath in enumerate(law_files, 1):` and before the per-provision loop). Insert:

```python
        act_node = _find_act_node(doc)
        if act_node is None:
            # Malformed peep — no act node typed both estleg:Act
            # AND owl:Ontology. Skip with a coverage note (the full
            # skip-reason wiring lands in Task 7).
            continue
        enforcement_level = _classify_enforcement_level(act_node)
```

Then locate the existing line that sets `node["estleg:hasSanction"] = sanction_refs` (around line 596) and ADD the enforcement stamp:

```python
            # Add hasSanction link to provision
            if sanction_refs:
                node["estleg:hasSanction"] = sanction_refs
                node["estleg:enforcedAtLevel"] = enforcement_level
```

The classification value is recomputed from the ALWAYS-fresh `act_node["@type"]` on every run — no preservation, no caching.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/test_extract_sanctions.py::TestSanctionExtractionWithEnforcementStamp -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: 195 + 4 = 199 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_sanctions.py tests/test_extract_sanctions.py
git commit -m "Emit estleg:enforcedAtLevel on sanction-bearing provisions

extract_sanctions.main() now finds the act node once per peep file
(via _find_act_node) and classifies the file's enforcement level
once. Each provision that gains estleg:hasSanction also gains
exactly ONE estleg:enforcedAtLevel literal — bare string, not a
list, regardless of how many Sanction_* records the provision
emits.

Provisions with no sanctions gain no enforcement stamp (preserves
the 'only on sanction-bearing provisions' rule).

Idempotency / sanctions-file deletion / coverage are subsequent
tasks — this commit just wires the emission."
```

---

## Task 3: Refactor to per-file in-memory processing + provision-level idempotency (TDD)

**Files:**
- Modify: `scripts/extract_sanctions.py` (replace `_clear_existing_sanctions` global pre-clear with per-file in-memory processing).
- Modify: `tests/test_extract_sanctions.py` (add `TestSanctionsIdempotency` class — 3 of 4 tests).

The current script has a global pre-clear pass at line 481 (`_clear_existing_sanctions`) that walks every law file once stripping prior `estleg:hasSanction`. This must be replaced with per-file in-memory processing so we can:
1. Detect existing output (`hasSanction` OR `enforcedAtLevel`) BEFORE clearing.
2. Clear unconditionally.
3. Run extraction.
4. Save when EITHER fresh output exists OR pre-existing output existed.

The sanctions-file deletion is Task 4; this task handles only the peep-side mutation.

- [ ] **Step 1: Append idempotency tests to `tests/test_extract_sanctions.py`**

```python
class TestSanctionsIdempotency:
    """Idempotency tests for both peep-side mutations and the
    sanctions JSON file lifecycle. Each test invokes main() twice
    over the same fixture; the second run must converge to the
    correct steady state regardless of the first run's output.
    """

    def test_stale_enforcement_cleared_when_sanctions_removed(
        self, tmp_path, monkeypatch
    ):
        """Provision starts with prior hasSanction + enforcedAtLevel.
        The act's summary is updated to text the regex won't match.
        Second run strips BOTH triples (peep-side idempotency)."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "stale_peep.json"
        # Stage with PRIOR triples already present.
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Stale_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Stale_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 # Summary now contains NO sanction language.
                 "estleg:summary":
                     "See paragrahv kirjeldab üldist eesmärki ja "
                     "ei sätesta karistusi.",
                 # Stale triples from a prior run:
                 "estleg:hasSanction": [{"@id": "estleg:Sanction_Stale_Par_1_fine"}],
                 "estleg:enforcedAtLevel": "state"},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Stale_Par_1")
        # Stale triples must be GONE.
        assert "estleg:hasSanction" not in prov
        assert "estleg:enforcedAtLevel" not in prov

    def test_classification_recomputed_when_act_type_flips(
        self, tmp_path, monkeypatch
    ):
        """Edge case: classification is stateless. If a peep's @type
        is changed by an upstream corpus correction (Law → Municipal
        Regulation or vice versa), the recomputed enforcedAtLevel
        reflects the new type, not the prior emission."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "regulations" / "kov" / "test"
        peep.mkdir(parents=True)
        peep_file = peep / "act_peep.json"
        # @type was Law (some prior corpus state); now correctly
        # MunicipalRegulation. Prior emission was 'state'; this run
        # must produce 'municipality'.
        peep_file.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_Test_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_Test_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Parkimisrikkumise eest, mis on väärtegu, mille "
                     "eest on ette nähtud rahatrahv kuni 50 "
                     "trahviühikut.",
                 # Stale (wrong) classification from a prior run.
                 "estleg:enforcedAtLevel": "state"},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        # KOV-staged fixture; force include_kov=True since Task 6
        # hasn't unpinned yet.
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep_file, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Reg_Test_Par_1")
        # Recomputed from current @type — 'municipality', not the
        # stale 'state'.
        assert prov.get("estleg:enforcedAtLevel") == "municipality"

    def test_no_op_when_no_existing_no_fresh(
        self, tmp_path, monkeypatch
    ):
        """A peep with no sanctions and no prior stamps shouldn't
        change between runs (no spurious writes)."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)
        peep = krr / "boring_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Boring_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Boring_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Käesolev seadus reguleerib üldisi suhteid."},
            ],
        }), encoding="utf-8")
        before_mtime = peep.stat().st_mtime
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        # File untouched (mtime unchanged) — no spurious save.
        after_mtime = peep.stat().st_mtime
        assert before_mtime == after_mtime, (
            "no-op file must not be re-saved when nothing changes"
        )
```

- [ ] **Step 2: Run the idempotency tests (expect 1 failure + 2 passes — the missing `enforcedAtLevel` clear is the real gap)**

Run: `pytest tests/test_extract_sanctions.py::TestSanctionsIdempotency -v`
Expected:
- `test_stale_enforcement_cleared_when_sanctions_removed` — **FAIL.** The current `_clear_existing_sanctions` pass only strips `estleg:hasSanction`; the stale `estleg:enforcedAtLevel` survives. This is the test the refactor must satisfy.
- `test_classification_recomputed_when_act_type_flips` — likely PASS already, because Task 2's emission code overwrites `enforcedAtLevel` on every run regardless of prior state (classification is stateless by design). The test still has value as a regression lock.
- `test_no_op_when_no_existing_no_fresh` — likely PASS already. The current `_clear_existing_sanctions` only saves files where it actually removed a triple (`if changed: save_json(...)`); a peep with no sanctions has nothing to remove and isn't re-saved, so mtime is preserved.

Don't try to reproduce 3 failures — the refactor's value comes from preserving the 2-pass behaviour AND fixing the 1 real gap (stale `enforcedAtLevel` not being cleared). After Step 3 lands, all 3 must pass.

- [ ] **Step 3: Replace `_clear_existing_sanctions` with per-file in-memory processing**

In `scripts/extract_sanctions.py`:

(a) DELETE the entire `_clear_existing_sanctions` function (lines 481-502) AND its call site at lines 511-515:

```python
# REMOVE these lines:
print(f"\n[0/4] Clearing existing sanctions for idempotency...")
cleared = _clear_existing_sanctions(law_files)
print(f"  Cleared estleg:hasSanction from {cleared} file(s)")
```

(b) Modify the per-file loop (lines 526-600) to handle existing-output detection and conditional save in-memory. Replace the existing per-file block with this structure:

```python
    for idx, filepath in enumerate(law_files, 1):
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue

        act_node = _find_act_node(doc)
        if act_node is None:
            # Malformed peep — coverage skip handled in Task 7.
            continue
        enforcement_level = _classify_enforcement_level(act_node)

        # Step 1: detect prior peep-side output BEFORE any mutation.
        had_existing_peep = any(
            ("estleg:hasSanction" in n) or ("estleg:enforcedAtLevel" in n)
            for n in doc["@graph"]
        )

        # Step 2: clear peep-side state unconditionally (idempotency).
        for n in doc["@graph"]:
            n.pop("estleg:hasSanction", None)
            n.pop("estleg:enforcedAtLevel", None)

        law_name = filepath.stem.replace("_peep", "")
        law_sanctions: list[dict] = []

        iri_counts: dict[str, int] = defaultdict(int)

        # Step 3: run extraction over the cleared graph.
        for node in doc["@graph"]:
            summary = node.get("estleg:summary", "")
            if not summary:
                continue

            total_provisions += 1
            sanctions = extract_sanctions(summary)
            if not sanctions:
                continue

            provisions_with_sanctions += 1
            provision_iri = node.get("@id", "")
            provision_ref = _provision_ref(node)
            provision_par = (
                provision_iri.replace("estleg:", "")
                if provision_iri
                else sanitize_id(node.get("estleg:paragrahv", "unknown"))
            )

            sanction_refs: list[dict] = []
            for s in sanctions:
                total_sanction_count += 1
                stype = s["sanction_type"]

                if "max_penalty" not in s:
                    fallback = _try_extract_penalty_from_summary(summary, stype)
                    if fallback:
                        s["max_penalty"] = fallback

                base_iri = f"estleg:Sanction_{provision_par}_{stype}"
                iri_counts[base_iri] += 1
                if iri_counts[base_iri] == 1:
                    sanction_iri = base_iri
                else:
                    sanction_iri = f"{base_iri}_{iri_counts[base_iri]}"

                sanction_node: dict = {
                    "@id": sanction_iri,
                    "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                    "estleg:sanctionType": stype,
                    "estleg:applicableProvision": {"@id": provision_iri},
                }
                if "max_penalty" in s:
                    sanction_node["estleg:maxPenalty"] = s["max_penalty"]
                if "min_penalty" in s:
                    sanction_node["estleg:minPenalty"] = s["min_penalty"]
                sanction_node["rdfs:label"] = _build_label(stype, s, provision_ref)

                law_sanctions.append(sanction_node)
                sanction_refs.append({"@id": sanction_iri})
                stats_per_type[stype] += 1

            if sanction_refs:
                node["estleg:hasSanction"] = sanction_refs
                node["estleg:enforcedAtLevel"] = enforcement_level

        # Step 4: save when EITHER fresh output exists OR pre-existing
        # output existed (so cleared-but-empty files persist the
        # cleared state to disk).
        if law_sanctions or had_existing_peep:
            save_json(filepath, doc)
        if law_sanctions:
            all_law_sanctions[law_name] = law_sanctions

        if idx % 100 == 0 or idx == len(law_files):
            print(f"  [{idx}/{len(law_files)}] processed – {total_sanction_count} sanctions found")
```

The print at line 511 (`Clearing existing sanctions for idempotency`) can be removed entirely or replaced with a one-line log: `print("\n[1/4] Processing law files (per-file idempotent)...")`. Pick the latter — it preserves the [N/4] progress structure used elsewhere in the script.

Renumber the remaining stage prints from `[1/4] Found N law files...` → `[1/4] Processing...` (since the explicit pre-clear stage is gone).

- [ ] **Step 4: Run the idempotency tests to verify they pass**

Run: `pytest tests/test_extract_sanctions.py::TestSanctionsIdempotency -v`
Expected: 3 passed (the 4th sanctions-file-deletion test lands in Task 4).

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `pytest -q`
Expected: 199 + 3 = 202 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_sanctions.py tests/test_extract_sanctions.py
git commit -m "Refactor sanctions extraction to per-file idempotent processing

Replaces the global _clear_existing_sanctions pre-clear pass with
per-file in-memory processing. Each peep is loaded once; the
per-file flow detects pre-existing peep-side output (hasSanction or
enforcedAtLevel anywhere in the graph) BEFORE clearing, runs
extraction over the cleared graph, and saves the file when EITHER
fresh sanction output exists OR pre-existing output existed.

Without the conditional save, a peep that loses all its sanctions
between runs would carry stale triples on disk forever. Three new
TestSanctionsIdempotency tests lock in the contract:
- Stale hasSanction + enforcedAtLevel cleared when text changes
- Classification is stateless (recomputed every run)
- No-op when neither fresh nor prior output exists (no spurious writes)

Sanctions-JSON-file deletion is Task 4."
```

---

## Task 4: Sanctions-file deletion when act loses all matches (TDD)

**Files:**
- Modify: `scripts/extract_sanctions.py` (per-file flow gains sanctions-file lifecycle).
- Modify: `tests/test_extract_sanctions.py` (4th test in `TestSanctionsIdempotency`).

The existing flow writes `krr_outputs/sanctions/sanctions_<law>.json` only when `all_law_sanctions[law_name]` is non-empty (line 622). It NEVER deletes a sanctions file that should no longer exist (e.g. an act lost its only sanction between runs). This task closes that gap.

- [ ] **Step 1: Append the 4th idempotency test to `tests/test_extract_sanctions.py::TestSanctionsIdempotency`**

```python
    def test_stale_sanctions_json_deleted_when_act_loses_all_sanctions(
        self, tmp_path, monkeypatch
    ):
        """When an act loses ALL its sanction matches between runs,
        the corresponding krr_outputs/sanctions/sanctions_<law>.json
        file is DELETED so orphan Sanction_* nodes don't outlive the
        inverse pass."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        sanctions_dir.mkdir(parents=True)

        # Stage a peep whose summary has NO sanction language.
        peep = krr / "lostsanc_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:LostSanc_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
                 "rdfs:label": "Lost sanctions law"},
                {"@id": "estleg:LostSanc_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "See seadus käsitleb üldisi põhimõtteid."},
            ],
        }), encoding="utf-8")

        # Stage a STALE sanctions file from a prior run, containing
        # a Sanction_* node + applicableProvision pointing at the
        # provision that no longer carries hasSanction.
        # The filename uses sanitize_id of the law slug — match the
        # script's filename convention exactly.
        from extract_sanctions import sanitize_id
        stale_filename = f"sanctions_{sanitize_id('lostsanc')}.json"
        stale_path = sanctions_dir / stale_filename
        stale_path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Sanctions_lostsanc_Map",
                 "@type": ["owl:Ontology"],
                 "rdfs:label": "Sanctions – lostsanc",
                 "dc:source": "lostsanc"},
                {"@id": "estleg:Sanction_LostSanc_Par_1_fine",
                 "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                 "estleg:sanctionType": "fine",
                 "estleg:applicableProvision": {"@id": "estleg:LostSanc_Par_1"}},
            ],
        }), encoding="utf-8")
        assert stale_path.exists()

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc in (0, None)

        # The stale sanctions JSON file MUST be gone.
        assert not stale_path.exists(), (
            "stale sanctions JSON must be deleted when act loses "
            "all matches"
        )
```

- [ ] **Step 2: Run the test (expect FAIL — deletion logic doesn't exist yet)**

Run: `pytest tests/test_extract_sanctions.py::TestSanctionsIdempotency::test_stale_sanctions_json_deleted_when_act_loses_all_sanctions -v`
Expected: FAIL — the stale sanctions file persists because the script never deletes it.

- [ ] **Step 3: Add sanctions-file deletion to the per-file flow in `scripts/extract_sanctions.py`**

Modify the per-file loop body. Just before the `if law_sanctions or had_existing_peep: save_json(...)` block from Task 3, ADD detection of the existing sanctions JSON file. Inside the per-file block, BEFORE the extraction loop runs, capture:

```python
        # Detect pre-existing sanctions JSON file (path computed
        # the same way the writer uses).
        sanctions_filename = f"sanctions_{sanitize_id(law_name)}.json"
        sanctions_path = SANCTION_DIR / sanctions_filename
        had_existing_sanctions_file = sanctions_path.exists()
```

Then AFTER the extraction loop and the `if law_sanctions or had_existing_peep:` save, add the deletion branch:

```python
        # Sanctions-file lifecycle:
        # - If the act produced fresh sanctions, the writer block
        #   below regenerates the file (Step 2 of main()).
        # - If the act produced NO fresh sanctions but a stale file
        #   exists on disk, delete it.
        if not law_sanctions and had_existing_sanctions_file:
            try:
                sanctions_path.unlink()
            except OSError:
                pass  # tolerate concurrent removal
```

`law_name` and `sanitize_id` are both already in scope at this point (defined at the top of the per-file block).

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_extract_sanctions.py::TestSanctionsIdempotency::test_stale_sanctions_json_deleted_when_act_loses_all_sanctions -v`
Expected: PASS.

- [ ] **Step 5: Run the full TestSanctionsIdempotency class + full suite**

Run: `pytest tests/test_extract_sanctions.py::TestSanctionsIdempotency -v`
Expected: 4 passed.

Run: `pytest -q`
Expected: 202 + 1 = 203 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_sanctions.py tests/test_extract_sanctions.py
git commit -m "Delete stale sanctions JSON when act loses all matches

Per-file flow now detects existing sanctions/sanctions_<law>.json
files BEFORE extraction. After extraction, if the act produced no
fresh sanctions but a stale file exists on disk, the stale file is
deleted (unlink). Without this, orphan Sanction_* nodes survive in
the krr_outputs/sanctions/ directory pointing at provisions that no
longer carry hasSanction, misleading the inverse pass and downstream
analytics.

Deletion (not empty-graph overwrite) keeps the directory listing
semantically meaningful: the presence of a sanctions file =
the act has at least one sanction."
```

---

## Task 5: Add SHACL property constraint for `enforcedAtLevel`

**Files:**
- Modify: `shacl/estonian_legal_shapes.ttl` (add property block to `LegalProvisionShape`).

The Layer 2a spec declared `estleg:enforcedAtLevel` in `metadata.jsonld` but never added a SHACL property constraint. This task closes the gap.

- [ ] **Step 1: Locate the existing `LegalProvisionShape` in `shacl/estonian_legal_shapes.ttl`**

The shape starts around line 34. It contains multiple `sh:property [ ... ]` blocks for `paragrahv`, `summary`, `legalText`, `entryIntoForce`, etc. Read the file to confirm the exact location and surrounding style.

```bash
grep -n "LegalProvisionShape\|sh:property" shacl/estonian_legal_shapes.ttl | head -10
```

- [ ] **Step 2: Add the new property constraint**

Inside the `LegalProvisionShape` definition, add a new `sh:property` block. Place it AFTER the existing `entryIntoForce` / `repealDate` / `temporalStatus` blocks (around line 119, before the next shape begins) so the schema reads bottom-to-top from "structural" to "Layer 2c-introduced." Insert:

```turtle
    sh:property [
        sh:path estleg:enforcedAtLevel ;
        sh:datatype xsd:string ;
        sh:maxCount 1 ;
        sh:in ( "state" "municipality" ) ;
        sh:name "enforcedAtLevel" ;
        sh:description "Layer 2c — coarse classification of where this provision's sanctions are enforced. Set only on provisions that also carry estleg:hasSanction. 'state' for provisions in Law, NationalRegulation, GovernmentRegulation, or MinisterialRegulation acts; 'municipality' for provisions in MunicipalRegulation acts." ;
    ] ;
```

The trailing `;` chains it onto the existing `sh:property` chain in the shape definition. Make sure the previous `sh:property` block's terminator was a `;` (not a `.`), and the new block's terminator is whatever the LAST property block in the shape uses (`;` if more follow, `.` if it's last).

- [ ] **Step 3: Sanity-parse the TTL**

```bash
python3 -c "
from rdflib import Graph
g = Graph()
g.parse('shacl/estonian_legal_shapes.ttl', format='turtle')
print(f'OK — {len(g)} triples loaded')
"
```

Expected: `OK — <some number> triples loaded`. If the parse fails, the new block has a syntax error.

- [ ] **Step 4: Verify the constraint is targeting the right property**

```bash
python3 -c "
from rdflib import Graph
SHACL = 'http://www.w3.org/ns/shacl#'
ESTLEG = 'https://data.riik.ee/ontology/estleg#'
g = Graph()
g.parse('shacl/estonian_legal_shapes.ttl', format='turtle')
q = '''
SELECT ?path ?in_list WHERE {
  ?shape <%spath> <%senforcedAtLevel> ;
         <%sin> ?in_list .
}
''' % (SHACL, ESTLEG, SHACL)
results = list(g.query(q))
assert len(results) >= 1, 'Constraint missing'
print('Constraint present:', results[0])
"
```

Expected: `Constraint present: ...` — the SHACL constraint is wired correctly.

- [ ] **Step 5: Run the existing SHACL-aware tests**

Run: `pytest tests/test_shacl_kov_layer1.py -v`
Expected: all existing SHACL tests still pass (the new constraint shouldn't break anything that didn't already carry `enforcedAtLevel`).

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: 203 passed (no test count change — the SHACL change is structural).

- [ ] **Step 7: Commit**

```bash
git add shacl/estonian_legal_shapes.ttl
git commit -m "Add SHACL property constraint for estleg:enforcedAtLevel

LegalProvisionShape gains a property block for enforcedAtLevel:
xsd:string datatype, sh:maxCount 1, sh:in ('state' 'municipality').
Closes the Layer 2a gap where metadata.jsonld declared the property
but the SHACL TTL didn't reference it (so the previous spec's
'zero new violations' claim was vacuous).

The classification function in extract_sanctions.py returns exactly
'state' or 'municipality', both of which are in the new sh:in value
set, so the constraint validates clean by construction.

Targeted SHACL verification on the corpus run lands in Task 8."
```

---

## Task 6: Unpin — remove `include_kov=False`

**Files:**
- Modify: `scripts/extract_sanctions.py` (line 510 only).

- [ ] **Step 1: Confirm the pin location**

Run: `grep -n "include_kov" scripts/extract_sanctions.py`
Expected: one line — `law_files = iter_peep_files(include_kov=False)  # DEFERRED to Layer 2c`.

- [ ] **Step 2: Edit the pin**

Open `scripts/extract_sanctions.py`. Find:

```python
    law_files = iter_peep_files(include_kov=False)  # DEFERRED to Layer 2c
```

Replace with:

```python
    law_files = iter_peep_files()
```

(Keep the variable name and the surrounding code unchanged; only the argument and the trailing comment go.)

- [ ] **Step 3: Confirm the pin is gone**

Run: `grep -n "include_kov" scripts/extract_sanctions.py`
Expected: empty output.

- [ ] **Step 4: AST-parse the file**

Run: `python3 -c "import ast; ast.parse(open('scripts/extract_sanctions.py').read())" && echo OK`
Expected: `OK`.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: 203 passed (no test count change — the unpin is structural; the existing tests run on tmp fixtures + the `_iter_kov_inclusive` wrapper so they're insensitive to the production default).

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_sanctions.py
git commit -m "Unpin extract_sanctions — runs KOV by default

Removes the include_kov=False argument and the trailing 'DEFERRED
to Layer 2c' comment. iter_peep_files() now defaults to
include_kov=True (the Layer 2a default), so the script processes
laws + state regulations + KOV acts in one pass.

Real-world impact (read-only dry count): ~82 KOV files gain
sanction triples, ~85 sanction records.

Coverage gate verification on the corpus run lands in Task 8."
```

---

## Task 7: Coverage instrumentation + `main() -> int` (TDD)

**Files:**
- Modify: `scripts/extract_sanctions.py` (add coverage instrumentation, change `main()` signature, add gate check, change `if __name__` block).
- Modify: `tests/test_extract_sanctions.py` (add `TestSanctionsCoverageReport` class).

Reuse the existing `kov_pipeline_coverage.py` helper — same idiom Layer 2b's two pipelines used. `set[Path]` accumulators at the call site; `len()` feeds the helper's int fields.

`triples_emitted` counts SANCTION RECORDS (not RDF triples). Document this in code so readers don't misinterpret across pipelines.

- [ ] **Step 1: Append `TestSanctionsCoverageReport` to `tests/test_extract_sanctions.py`**

```python
class TestSanctionsCoverageReport:
    """The script must write a kov_pipeline_coverage CoverageReport
    JSON to krr_outputs/reports/kov/extract_sanctions_coverage.json,
    and main() must return non-zero when the gate fails."""

    def test_coverage_report_written_with_kov_split(
        self, tmp_path, monkeypatch
    ):
        """Stage 1 KOV act + 1 state law, both with sanction text.
        Run main(). Assert the coverage JSON exists with files_with_output_kov >= 1
        and triples_emitted_kov >= 1."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        reports_dir = krr / "reports" / "kov"
        sanctions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        # KOV act with sanction
        kov = krr / "regulations" / "kov" / "test"
        kov.mkdir(parents=True)
        (kov / "kov_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TST_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_TST_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Parkimisrikkumise eest, mis on väärtegu, mille "
                     "eest on ette nähtud rahatrahv kuni 50 trahviühikut."},
            ],
        }), encoding="utf-8")

        # State law with sanction
        (krr / "alko_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:AS_Map_2026",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:AS_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Alkoholi keelu rikkumise eest, mis on väärtegu, "
                     "mille eest on ette nähtud rahatrahv kuni 100 "
                     "trahviühikut."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc == 0, f"main() should return 0 on success, got {rc}"

        coverage_path = reports_dir / "extract_sanctions_coverage.json"
        assert coverage_path.exists()
        with open(coverage_path, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        assert cov["files_processed_kov"] >= 1
        assert cov["files_with_output_kov"] >= 1
        assert cov["triples_emitted_kov"] >= 1
        # Total counters include KOV + non-KOV
        assert cov["files_with_output"] >= 2
        assert cov["triples_emitted"] >= 2

    def test_gate_fails_when_kov_input_nontrivial_but_no_output(
        self, tmp_path, monkeypatch
    ):
        """Mock iter_peep_files to return ≥11,000 KOV paths whose
        summaries contain no sanction language. main() must return 1
        (gate fail)."""
        import extract_sanctions as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        sanctions_dir = krr / "sanctions"
        reports_dir = krr / "reports" / "kov"
        sanctions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        # Generate 11,001 KOV peeps whose summaries have NO sanction
        # text. In practice, regenerating that many tmp files is
        # expensive — mock iter_peep_files to return synthetic Path
        # objects pointing at one shared peep file replicated under
        # 11,001 distinct kov subpaths.
        kov = krr / "regulations" / "kov" / "no_sanctions"
        kov.mkdir(parents=True)
        # Single shared file with NO sanction text:
        shared = kov / "boring_peep.json"
        shared.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_Boring_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"]},
                {"@id": "estleg:Reg_Boring_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Käesolev määrus reguleerib administratiivseid "
                     "küsimusi ega sätesta karistusi."},
            ],
        }), encoding="utf-8")

        # Build a synthetic list of 11,001 paths all pointing at the
        # same file. The script reads the file content per call;
        # path identity is what KOV-attribution checks.
        synthetic_kov_paths = [
            kov / f"act_{i}_peep.json" for i in range(11001)
        ]
        # Map all synthetic paths to the shared file's contents so
        # load_json can read them. Simpler: write hard links.
        for p in synthetic_kov_paths:
            p.write_text(shared.read_text(encoding="utf-8"), encoding="utf-8")

        # Monkeypatch iter_peep_files to return ALL synthetic paths.
        def fake_iter(*args, **kwargs):
            return synthetic_kov_paths
        monkeypatch.setattr(mod, "iter_peep_files", fake_iter)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "SANCTION_DIR", sanctions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc == 1, (
            f"gate must fail (return 1) when KOV input ≥11k but "
            f"no output; got rc={rc}"
        )
```

- [ ] **Step 2: Run the new tests (expect failures — coverage code doesn't exist yet)**

Run: `pytest tests/test_extract_sanctions.py::TestSanctionsCoverageReport -v`
Expected: 2 failures.

- [ ] **Step 3: Add coverage instrumentation to `scripts/extract_sanctions.py`**

(a) ADD imports near the top (around line 14 with the existing imports):

```python
import time
from datetime import datetime, timezone

from kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)
```

(`datetime` may already be imported; if so, just add `timezone` to the existing import line.)

(b) Change `main()` signature from `def main() -> None:` to `def main() -> int:`.

(c) At the TOP of `main()` (just after the print-banner block), initialize the accumulators:

```python
    _start = time.perf_counter()
    _files_processed: set[Path] = set()
    _files_processed_kov: set[Path] = set()
    _files_with_output: set[Path] = set()
    _files_with_output_kov: set[Path] = set()
    _triples = 0
    _triples_kov = 0
    _files_skipped = 0
    _skip_reasons: dict[str, int] = {}
    _failures: list[str] = []
    _unresolved = 0  # always 0 for this pipeline; field required by helper
```

(d) Inside the per-file loop, AFTER `act_node = _find_act_node(doc)` but BEFORE the early-return on `act_node is None`, add bookkeeping that distinguishes aggregate-registry peeps from malformed act peeps:

```python
        if act_node is None:
            # Distinguish aggregate-registry peeps (issuers_kov_peep.json,
            # municipalities_peep.json, etc.) from malformed act peeps.
            #
            # Aggregate peeps have no provision nodes — they're
            # catalogues of Issuer/Municipality/etc. entities.
            # Silently skip those (no counter, no failure log) because
            # they're correctly-formed non-act peeps that just don't
            # carry sanctions.
            #
            # Malformed act peeps DO have provision nodes but no
            # estleg:Act + owl:Ontology typing on their root node —
            # that's a Layer 1 data bug worth surfacing in the
            # coverage report's failure_samples.
            #
            # Use `estleg:paragrahv` as the provision signal rather
            # than @type membership: it's universal across all
            # provision class shapes (estleg:LegalProvision,
            # estleg:KovProvision, estleg:LegalProvision_<prefix>,
            # estleg:Regulation_<id>, etc.). @type-based detection
            # would silently miss state-regulation provisions typed
            # only as estleg:Regulation_<id>.
            has_provisions = any(
                "estleg:paragrahv" in n
                for n in doc.get("@graph", [])
            )
            if has_provisions:
                _files_skipped += 1
                _skip_reasons["missing_act_node"] = (
                    _skip_reasons.get("missing_act_node", 0) + 1
                )
                _failures.append(
                    f"{filepath.name}: malformed peep — has provisions "
                    f"but missing estleg:Act + owl:Ontology root node"
                )
            # Either way, the file has no act to classify — skip extraction.
            continue
```

After `enforcement_level = _classify_enforcement_level(act_node)` and BEFORE the per-provision loop, add file-level bookkeeping:

```python
        is_kov = "regulations/kov/" in str(filepath)
        _files_processed.add(filepath)
        if is_kov:
            _files_processed_kov.add(filepath)
```

After the per-provision loop and the conditional save (just after the `if law_sanctions or had_existing_peep:` block), update the with-output set:

```python
        if law_sanctions:
            _files_with_output.add(filepath)
            if is_kov:
                _files_with_output_kov.add(filepath)
            _triples += len(law_sanctions)  # SANCTION RECORDS, not RDF triples
            if is_kov:
                _triples_kov += len(law_sanctions)
```

(e) At the END of `main()` (replace the existing summary-print block, or place after it), write the coverage report and gate check:

```python
    # ----- coverage report -----
    # Note: triples_emitted counts SANCTION RECORDS (not RDF triples).
    # Each Sanction_* node typically projects 4-7 RDF triples
    # (@type, label, sanctionType, applicableProvision, optional
    # min/maxPenalty). Counting records matches the analytical unit
    # (cf. total_sanction_count in the existing sanctions_report.json).
    # extract_cross_references counts actual triples; sanctions counts
    # records; the difference is intentional.
    all_input_files = list(iter_peep_files())
    _kov_files = [p for p in all_input_files
                  if "regulations/kov/" in str(p)]

    _wall, _rate, _peak_mb = measure_runtime(_start, len(_files_processed))
    out_path = KRR_DIR / "reports" / "kov" / "extract_sanctions_coverage.json"
    write_coverage_report(
        CoverageReport(
            pipeline="extract_sanctions",
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(all_input_files),
            input_files_kov=len(_kov_files),
            files_processed=len(_files_processed),
            files_processed_kov=len(_files_processed_kov),
            files_with_output=len(_files_with_output),
            files_with_output_kov=len(_files_with_output_kov),
            files_skipped=_files_skipped,
            skip_reasons=_skip_reasons,
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        out_path,
    )
    print(f"\nCoverage report: {out_path}")
    print(f"  input_files_total: {len(all_input_files)} (KOV: {len(_kov_files)})")
    print(f"  files_with_output: {len(_files_with_output)} (KOV: {len(_files_with_output_kov)})")
    print(f"  triples_emitted (sanction records): {_triples} (KOV: {_triples_kov})")

    # Gate check (matches Layer 2a/2b convention).
    if len(_kov_files) >= 11000 and len(_files_with_output_kov) == 0:
        print("\nGATE FAIL: KOV files were processed but none produced sanction output.")
        return 1
    if _triples_kov == 0 and len(_kov_files) >= 11000:
        print("\nGATE FAIL: zero KOV sanction records emitted.")
        return 1
    print("\nGATE OK")
    return 0
```

(f) At the very bottom of the file, change the `if __name__ == "__main__":` block from:

```python
if __name__ == "__main__":
    main()
```

to:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/test_extract_sanctions.py::TestSanctionsCoverageReport -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full TestSanctionsIdempotency + Coverage classes + full suite**

Run: `pytest tests/test_extract_sanctions.py -v`
Expected: 4 + 4 + 4 + 2 = 14 passed (corpus-invariant test lands after the corpus run in Task 8).

Run: `pytest -q`
Expected: 205 passed. Cumulative count from baseline 191: Task 1 +4 = 195; Task 2 +4 = 199; Task 3 +3 = 202; Task 4 +1 = 203; Task 5 +0 (SHACL only) = 203; Task 6 +0 (unpin only) = 203; Task 7 +2 = 205. Task 8's corpus-invariant test will bring the total to 206.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_sanctions.py tests/test_extract_sanctions.py
git commit -m "Add coverage instrumentation to extract_sanctions

Reuses kov_pipeline_coverage helper (same idiom as Layer 2b's two
pipelines). main() now returns int; the if __name__ block uses
raise SystemExit(main()) so the gate's exit code reaches the OS.

set[Path] accumulators at the call site; len() feeds the helper's
int fields. Malformed peep files (no estleg:Act + owl:Ontology
node) skip with skip_reasons['missing_act_node'] rather than
crashing or being misclassified.

triples_emitted counts SANCTION RECORDS, not RDF triples — matches
the existing total_sanction_count semantics from sanctions_report.json
(extract_cross_references counts actual triples; the difference is
intentional and documented in code).

Gate check: fails when KOV input ≥11,000 files but produces zero
KOV output, or when ≥11k input but zero KOV triples emitted."
```

---

## Task 8: Run the full corpus + add corpus-invariant test

**Files:**
- Modify: many files under `krr_outputs/` (sanctioned-act peeps + sanctions/sanctions_*.json regenerations).
- Create/modify: `krr_outputs/reports/kov/extract_sanctions_coverage.json` (first run).
- Modify: `krr_outputs/sanctions_report.json` (existing summary report).
- Modify: `tests/test_extract_sanctions.py` (add `TestCorpusInvariant` class).

This task runs the unpinned script over the full corpus and stages the resulting diff. Then it adds the corpus-wide invariant test to lock in the global property.

- [ ] **Step 1: Pre-flight — confirm the XML symlink is present**

Run: `ls -la data/riigiteataja | head -3`
Expected: `data/riigiteataja -> /Users/henrikaavik/progemoge/law-ontology/data/riigiteataja` (symlink).

If it's a real directory instead of a symlink, restore:

```bash
rm -rf data/riigiteataja
ln -s /Users/henrikaavik/progemoge/law-ontology/data/riigiteataja data/riigiteataja
```

(Note: the sanctions pipeline doesn't actually USE the XML — it reads `estleg:summary` from peep files. The symlink is precautionary; the previous worktree dance left it fragile.)

- [ ] **Step 2: Run the script**

```bash
PYTHONUNBUFFERED=1 python3 scripts/extract_sanctions.py 2>&1 | tee /tmp/sanctions_run.log
```

Expected end of output:
```
Coverage report: <abspath>/krr_outputs/reports/kov/extract_sanctions_coverage.json
  input_files_total: 15508 (KOV: 11059)
  files_with_output: <some int> (KOV: ~82)
  triples_emitted (sanction records): <some int> (KOV: ~85)

GATE OK
```

The exact KOV numbers should be near the dry-count estimate (~82 KOV files, ~85 records). If they're zero or wildly off, STOP and investigate before staging.

- [ ] **Step 3: Inspect the coverage report**

Run: `cat krr_outputs/reports/kov/extract_sanctions_coverage.json`
Expected: a JSON object matching the `kov_pipeline_coverage.CoverageReport` schema (same shape as Layer 2a/2b reports). Required field values for this run:

- `pipeline == "extract_sanctions"`
- `input_files_total == 15508`, `input_files_kov == 11059`
- `files_with_output_kov >= 50` (dry count predicts ~82)
- `triples_emitted_kov >= 50` (dry count predicts ~85; field counts SANCTION RECORDS)
- `files_skipped` is small (0 if no malformed peeps; otherwise inspect `failure_samples` and `skip_reasons["missing_act_node"]`)
- `error_count == 0`

There are no `gate_*` fields in `CoverageReport` — the gate decision is reflected in `main()`'s exit code (`0` for OK, `1` for fail), not in the JSON. Memorise the exact `files_with_output_kov` and `triples_emitted_kov` values — the docs and PR body reference them.

- [ ] **Step 4: Append the corpus-invariant test to `tests/test_extract_sanctions.py`**

```python
class TestCorpusInvariant:
    """Corpus-wide invariant: every provision carrying
    estleg:hasSanction has exactly ONE estleg:enforcedAtLevel
    literal in {"state", "municipality"}.

    Skipped when krr_outputs/ is empty (clean checkout). Marked
    @pytest.mark.slow so CI can opt out on the fast path; runs in
    the integration suite.
    """

    @pytest.mark.slow
    def test_every_has_sanction_provision_has_exactly_one_enforced_at_level(self):
        from estleg_common import iter_peep_files, KRR_DIR
        if not KRR_DIR.exists() or not list(KRR_DIR.glob("*_peep.json")):
            pytest.skip("krr_outputs/ empty — clean checkout")

        violations = []
        for peep in iter_peep_files():
            try:
                with open(peep, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            for node in doc.get("@graph", []):
                if "estleg:hasSanction" not in node:
                    continue
                level = node.get("estleg:enforcedAtLevel")
                if level is None:
                    violations.append(
                        f"{peep.name}: {node.get('@id')} has hasSanction "
                        f"but no enforcedAtLevel"
                    )
                elif isinstance(level, list):
                    violations.append(
                        f"{peep.name}: {node.get('@id')} has list-valued "
                        f"enforcedAtLevel: {level}"
                    )
                elif not isinstance(level, str):
                    violations.append(
                        f"{peep.name}: {node.get('@id')} has non-string "
                        f"enforcedAtLevel ({type(level).__name__}): {level!r}"
                    )
                elif level not in {"state", "municipality"}:
                    violations.append(
                        f"{peep.name}: {node.get('@id')} has unexpected "
                        f"enforcedAtLevel value: {level!r}"
                    )
        assert not violations, (
            f"Corpus invariant violated by {len(violations)} provisions:\n  "
            + "\n  ".join(violations[:20])
            + (f"\n  ... and {len(violations) - 20} more"
               if len(violations) > 20 else "")
        )
```

(Imports `iter_peep_files` and `KRR_DIR` from `estleg_common`; `pytest` is already imported in the test file.)

- [ ] **Step 5: Run the corpus-invariant test on the freshly-generated corpus output**

Run: `pytest tests/test_extract_sanctions.py::TestCorpusInvariant -v`
Expected: 1 passed (the invariant holds — every `hasSanction` provision in the corpus has exactly one `enforcedAtLevel` literal in the value set).

If it fails, the implementation has a bug — investigate and fix BEFORE staging the corpus output.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: 205 + 1 = 206 passed.

- [ ] **Step 7: Stage the corpus diff in chunks**

```bash
python3 - <<'PY'
import subprocess
# git diff --name-only only lists modified files (not deleted or
# untracked). Use git status --porcelain to capture every status
# letter (M / A / D / ??) under krr_outputs/.
status_lines = subprocess.check_output(
    ["git", "status", "--porcelain"], text=True
).splitlines()
to_stage: list[str] = []
for line in status_lines:
    if not line:
        continue
    # Porcelain format: "XY path" where XY is two status chars.
    status = line[:2]
    path = line[3:]
    if not path.startswith("krr_outputs/"):
        continue
    # Stage modified, added, deleted, AND untracked files. Deletions
    # are EXPECTED when an act loses all its sanctions between runs
    # (the script unlinks the corresponding sanctions JSON in Task 4's
    # idempotency contract).
    if status[1] in ("M", "D") or status[0] in ("M", "A", "D", "?"):
        to_stage.append(path)

print(f"krr_outputs paths to stage (M/A/D/??): {len(to_stage)}")
chunk = 500
for i in range(0, len(to_stage), chunk):
    subprocess.check_call(["git", "add", "--"] + to_stage[i:i+chunk])
print("staged")
PY
```

- [ ] **Step 8: Stage the new test class**

```bash
git add tests/test_extract_sanctions.py
```

- [ ] **Step 9: Verify staging is clean**

Run: `git status --short | grep -v "^M  krr_outputs\|^A  krr_outputs\|^D  krr_outputs\|^M  tests" | head -10`
Expected: only the worktree-symlink artifact (`D data/riigiteataja/karistusseadustik.xml` and untracked `?? data/riigiteataja`) — anything else is unintended scope creep.

The `^D  krr_outputs` exemption is intentional: Task 4 introduced sanctions-file deletion (when an act loses all matches between runs). Any deleted `krr_outputs/sanctions/sanctions_<law>.json` paths under that path are EXPECTED and must be staged.

- [ ] **Step 10: Commit**

```bash
git commit -m "Run extract_sanctions on full corpus (Layer 2c PR #1)

First corpus run with KOV included. Real numbers:
- input_files_total: 15,508 (KOV: 11,059)
- files_with_output_kov: ~82
- triples_emitted_kov (sanction records): ~85
- GATE OK

Adds corpus-invariant test (TestCorpusInvariant, marked
@pytest.mark.slow): every provision carrying estleg:hasSanction
must have exactly one estleg:enforcedAtLevel literal in
{'state', 'municipality'}. Catches regressions where one
sanction-bearing provision loses its stamp even when unit tests
pass.

Stages: ~82 KOV peep modifications + sanctions/sanctions_*.json
regenerations + the new coverage report at
krr_outputs/reports/kov/extract_sanctions_coverage.json + the
existing sanctions_report.json with KOV statistics folded in.

Update the commit message body with the EXACT numbers from the
coverage report when committing — the body above is a template."
```

(Replace the `~82` / `~85` / "EXACT numbers" placeholders in the commit message body with the real values from the coverage report. The commit message is the only place the plan tolerates approximate numbers — every other reference must be exact.)

---

## Task 9: Documentation updates

**Files:**
- Modify: `docs/SCHEMA_REFERENCE.md` (mark `enforcedAtLevel` populated; add example in Sanctions section + Layer 2 schema table; document the new SHACL `sh:in` constraint).
- Modify: `CHANGELOG.md` (Layer 2c sub-PR-1 entry under `## [Unreleased]`).

- [ ] **Step 1: Update `docs/SCHEMA_REFERENCE.md`**

(a) Find the Layer 2 schema table (around line 803). The table has 6 columns: `Property | Domain | Range | Layer | Status | Purpose`. The row for `estleg:enforcedAtLevel` currently reads:

```
| `estleg:enforcedAtLevel` | `LegalProvision` | `xsd:string` | 2c | declared | `"state"` \| `"municipality"` |
```

Change the `Status` column from `declared` to `populated`. Leave the other 5 columns untouched. After the edit, the row should read:

```
| `estleg:enforcedAtLevel` | `LegalProvision` | `xsd:string` | 2c | populated | `"state"` \| `"municipality"` |
```

(b) Find the Sanctions section (locate via `grep -n "## Sanctions\|## .*Sanction" docs/SCHEMA_REFERENCE.md`). Add a new subsection at the end:

```markdown
### Layer 2c — `enforcedAtLevel`

Every provision that carries `estleg:hasSanction` ALSO carries exactly
one `estleg:enforcedAtLevel` literal classifying the parent act type:

- `"municipality"` — provisions in `MunicipalRegulation` acts.
- `"state"` — provisions in any other act type (`Law`,
  `NationalRegulation`, `GovernmentRegulation`, `MinisterialRegulation`).

The classification is purely structural (act `@type` field) — it does
NOT analyse delegation language or sanction-text context. State laws
that delegate enforcement to local government (e.g. KOKS § 66 lg 4
parking-fine ceilings) classify as `"state"` because the parent act is
`estleg:Law`. If a future analytics query needs delegation-aware
classification, a separate property (`estleg:enforcementContext`) can
be introduced without colliding.

Example:

```json
{
  "@id": "estleg:Reg_TLN_15_Map_2020_Par_1",
  "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
  "estleg:paragrahv": "§ 1",
  "estleg:hasSanction": [{"@id": "estleg:Sanction_Reg_TLN_15_Map_2020_Par_1_fine"}],
  "estleg:enforcedAtLevel": "municipality"
}
```

SHACL: `LegalProvisionShape` constrains `enforcedAtLevel` with
`sh:datatype xsd:string`, `sh:maxCount 1`, and a closed value set
`sh:in ("state" "municipality")`.
```

- [ ] **Step 2: Update `CHANGELOG.md`**

Find the `## [Unreleased]` section and add a new subsection under it (BEFORE the existing Layer 2b entries — newest first):

```markdown
### Added (Layer 2c PR #1 — Sanctions)
- `extract_sanctions.py` runs the full corpus including KOV by
  default (single `include_kov=False` pin removed).
- `estleg:enforcedAtLevel` populated on every provision that
  carries `estleg:hasSanction`. Value is `"municipality"` for
  provisions in `MunicipalRegulation` acts, `"state"` otherwise.
- SHACL: `LegalProvisionShape` gains a property constraint for
  `enforcedAtLevel` (`sh:datatype xsd:string`, `sh:maxCount 1`,
  `sh:in ("state" "municipality")`). Closes the Layer 2a gap
  where the property was declared in `metadata.jsonld` but not
  referenced in `shacl/estonian_legal_shapes.ttl`.

### Changed (Layer 2c PR #1)
- `extract_sanctions.main()` returns `int`; `raise SystemExit(main())`
  propagates the gate's exit code.
- Per-file in-memory processing replaces the global `_clear_existing_sanctions`
  pre-clear pass. Each peep is processed independently; the per-file
  flow detects pre-existing peep-side output BEFORE clearing and
  saves the file when EITHER fresh sanction output exists OR
  pre-existing output existed (so cleared-but-empty files persist
  the cleared state to disk).
- Stale `krr_outputs/sanctions/sanctions_<law>.json` files are
  DELETED when an act loses all its sanction matches between runs.
  Without this, orphan `Sanction_*` nodes survive pointing at
  provisions that no longer carry `hasSanction`.
- Coverage instrumentation reuses `kov_pipeline_coverage.CoverageReport`
  (same schema as the 5 Layer 2a pipelines + 2 Layer 2b pipelines).

### Coverage (Layer 2c PR #1, 2026-05-04 corpus run)
- `extract_sanctions`: 15,508 input files (11,059 KOV); ~82 KOV
  files with output; ~85 KOV sanction records; GATE OK.

### Internal (Layer 2c PR #1)
- New `tests/test_extract_sanctions.py` with 15 tests across 5
  classes (4 unit + 4 integration + 4 idempotency + 2 coverage +
  1 corpus-wide invariant marked `@pytest.mark.slow`).
- `_classify_enforcement_level(act_node) -> str` and
  `_find_act_node(doc) -> dict | None` helpers added to the
  module.
- `triples_emitted` counts SANCTION RECORDS (not RDF triples) —
  matches the existing `total_sanction_count` semantics from
  `sanctions_report.json`. Documented inline so readers don't
  misinterpret across pipelines (cross-references counts actual
  triples; sanctions counts records — intentional).

### Gate B umbrella status (after this PR)
- 2a COMPLETE (#98)
- 2b COMPLETE (#99)
- 2c PR #1 sanctions: this PR
- 2c PR #2 institutional-competence: pending
- 2c PR #3 court-provision-links: pending

Gate B closes when all three Layer 2c sub-PRs land.
```

- [ ] **Step 3: Verify**

Run: `pytest -q`
Expected: 206 passed (no test count change — the doc edits are markdown only).

- [ ] **Step 4: Commit**

```bash
git add docs/SCHEMA_REFERENCE.md CHANGELOG.md
git commit -m "Document Layer 2c PR #1 (sanctions) deliverables

SCHEMA_REFERENCE.md: marks estleg:enforcedAtLevel populated; adds
a worked example in the Sanctions section showing the property on
a KOV provision; documents the new sh:in ('state' 'municipality')
SHACL constraint.

CHANGELOG.md: Layer 2c PR #1 entry under [Unreleased] with
Added/Changed/Coverage/Internal/Gate-B-umbrella subsections.
Coverage numbers reference the actual corpus run (Task 8 commit)."
```

(Update the coverage line in the CHANGELOG with the exact numbers from Task 8's coverage report before committing.)

---

## Task 10: Final verification + PR body

**Files:** None (verification + PR body file only).

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: 206 passed.

- [ ] **Step 2: Run validate_all**

Run: `python3 scripts/validate_all.py 2>&1 | tail -10`
Expected: `VALIDATION PASSED` with 0 errors / 0 warnings.

- [ ] **Step 3: Run targeted SHACL on the new constraint**

The verification queries the SHACL results GRAPH directly via `sh:resultPath` rather than text-grepping the prose report. Text-grep is fragile because pyshacl puts `sh:resultPath` and other violation fields on separate lines — a per-line grep can return zero matches even when violations exist.

```bash
python3 - <<'PY'
from rdflib import Graph, URIRef
from rdflib.namespace import SH
from pyshacl import validate
import json
from pathlib import Path

ESTLEG_NS = "https://data.riik.ee/ontology/estleg#"
ENFORCED_AT_LEVEL = URIRef(ESTLEG_NS + "enforcedAtLevel")

# Build a data graph from a sample of corpus peeps that carry
# enforcedAtLevel triples (the ones whose constraint behaviour
# matters for this verification).
data = Graph()
sample_count = 0
for peep in Path("krr_outputs").glob("*_peep.json"):
    with open(peep, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if any("estleg:enforcedAtLevel" in n for n in doc.get("@graph", [])):
        data.parse(data=json.dumps(doc), format="json-ld")
        sample_count += 1
    if sample_count >= 50:
        break
for peep in Path("krr_outputs/regulations/kov").rglob("*_peep.json"):
    with open(peep, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if any("estleg:enforcedAtLevel" in n for n in doc.get("@graph", [])):
        data.parse(data=json.dumps(doc), format="json-ld")
        sample_count += 1
    if sample_count >= 100:
        break
print(f"Sampled {sample_count} files with enforcedAtLevel")

shapes = Graph()
shapes.parse("shacl/estonian_legal_shapes.ttl", format="turtle")
conforms, results_graph, _ = validate(
    data, shacl_graph=shapes, inference="rdfs"
)

# Query the results graph for ValidationResult nodes whose
# sh:resultPath is exactly estleg:enforcedAtLevel. This catches
# every violation that pyshacl flagged against our new constraint,
# regardless of whitespace/line-break formatting in the textual
# report.
new_violations = list(
    results_graph.subjects(SH.resultPath, ENFORCED_AT_LEVEL)
)
print(f"enforcedAtLevel violations on sample: {len(new_violations)}")
if new_violations:
    print("First 5 violation focus nodes:")
    for v in new_violations[:5]:
        focus = list(results_graph.objects(v, SH.focusNode))
        value = list(results_graph.objects(v, SH.value))
        print(f"  focus={focus} value={value}")
assert len(new_violations) == 0, (
    f"Found {len(new_violations)} SHACL violations on the new "
    f"enforcedAtLevel constraint."
)
print("Targeted SHACL: 0 violations on enforcedAtLevel — OK")
PY
```

Expected: `Targeted SHACL: 0 violations on enforcedAtLevel — OK`.

- [ ] **Step 4: Coverage gate one-liner**

```bash
python3 - <<'PY'
import json
p = "krr_outputs/reports/kov/extract_sanctions_coverage.json"
with open(p) as fh:
    r = json.load(fh)
assert r["files_with_output_kov"] > 0, "zero KOV output files"
assert r["triples_emitted_kov"] > 0, "zero KOV sanction records"
print(f"extract_sanctions: GATE OK — "
      f"{r['files_with_output_kov']} KOV files, "
      f"{r['triples_emitted_kov']} KOV sanction records, "
      f"{r['files_skipped']} skipped")
PY
```

Expected: `extract_sanctions: GATE OK — <N> KOV files, <M> KOV sanction records, <K> skipped`.

`<K>` should be 0 if no peep files have provisions but missing `estleg:Act + owl:Ontology` typing. Aggregate-registry peeps (`issuers_kov_peep.json`, `municipalities_peep.json`, etc.) are filtered silently and do NOT contribute to `<K>`. If `<K>` is non-zero, inspect `failure_samples` in the coverage report — those are real Layer 1 data bugs to surface in a follow-up.

- [ ] **Step 5: Generate the PR body at `/tmp/pr-body-2c-sanctions.md`**

Write this content (substitute the actual numbers from the coverage report — they should match the CHANGELOG):

```markdown
## Summary

Layer 2c PR #1 of 3 — Sanctions. Closes the first of three pending
sub-PRs in the Gate B umbrella. Unpins `extract_sanctions` so it
processes the full corpus (laws + state regulations + KOV acts) and
stamps `estleg:enforcedAtLevel` on every provision that carries a
sanction.

- **Classification:** `"municipality"` for `MunicipalRegulation`
  provisions; `"state"` for everything else. Pure act-type rule —
  no delegation analysis, no sanction-text context.
- **One literal per provision** regardless of how many `Sanction_*`
  records the provision emits.
- **SHACL constraint** added to `LegalProvisionShape`:
  `sh:datatype xsd:string`, `sh:maxCount 1`, closed value set
  `sh:in ("state" "municipality")`. Closes the Layer 2a gap where
  the property was declared in `metadata.jsonld` but not referenced
  in the SHACL TTL.
- **Idempotency at two layers:** peep-side `hasSanction` /
  `enforcedAtLevel` cleared and recomputed every run; sanctions
  JSON files DELETED when an act loses all matches.

## Coverage (corpus run, 2026-05-04)

| Metric | Value |
|---|---|
| Input files (total / KOV) | 15,508 / 11,059 |
| Files with output (total / KOV) | <N> / ~82 |
| Sanction records emitted (total / KOV) | <N> / ~85 |
| Files skipped (`missing_act_node`) | `<K>` (substitute exact value from coverage report; aggregate-registry peeps are silently filtered and do NOT count) |

(Exact numbers in `krr_outputs/reports/kov/extract_sanctions_coverage.json`.)

## SHACL

Targeted SHACL on the new `enforcedAtLevel` constraint: zero
violations. The classification function returns exactly `"state"`
or `"municipality"`, both in the new `sh:in` value set, so the
constraint validates clean by construction.

Pre-existing violations (`requestedCluster` IRI-as-literal, missing
`summary`, VOS_Par_* `rdfs:label`/`sectionNumber`) predate this PR
and are tracked in CHANGELOG known issues.

## Test plan

- [x] Full pytest suite passing (206 tests, +15 vs Layer 2b end-state)
- [x] validate_all reports zero errors / warnings
- [x] Targeted SHACL on `enforcedAtLevel`: zero violations
- [x] extract_sanctions_coverage.json gate OK
- [x] Corpus-wide invariant: every `hasSanction` provision has
      exactly one `enforcedAtLevel` literal in the value set
- [ ] Spot-check 3 KOV peeps for `enforcedAtLevel = "municipality"`
- [ ] Spot-check 3 law peeps for `enforcedAtLevel = "state"`

## Gate B umbrella status

- 2a COMPLETE (#98)
- 2b COMPLETE (#99)
- 2c PR #1 sanctions: **this PR**
- 2c PR #2 institutional-competence: next
- 2c PR #3 court-provision-links: third

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

- [ ] **Step 6: Print summary + PR body path**

```bash
echo ""
echo "========================================================================"
echo "Layer 2c PR #1 (Sanctions) execution COMPLETE"
echo "========================================================================"
echo ""
echo "Branch: feature/kov-layer2c-sanctions ($(git rev-parse --short HEAD))"
echo "Commits ahead of origin/main: $(git rev-list --count origin/main..HEAD 2>/dev/null || git rev-list --count main..HEAD)"
echo ""
echo "PR body ready at /tmp/pr-body-2c-sanctions.md"
echo ""
echo "When ready to push:"
echo "  git push -u origin feature/kov-layer2c-sanctions"
echo "  gh pr create --title 'KOV integration Layer 2c PR #1 — Sanctions' --body-file /tmp/pr-body-2c-sanctions.md"
echo ""
echo "DO NOT push without human approval."
```

- [ ] **Step 7: Verify the PR body file exists and is well-formed**

```bash
ls -la /tmp/pr-body-2c-sanctions.md
head -10 /tmp/pr-body-2c-sanctions.md
```

- [ ] **Step 8: Stop short of pushing**

Per the plan convention: do NOT run `git push`. Do NOT run `gh pr create`. The push + PR creation is left for the human reviewer's go-ahead.

---

## Self-Review Checklist

After all 10 tasks complete:

- [ ] **Spec coverage.** Each spec invariant maps to a task:
  - Inv 1 (pin removal): Task 6
  - Inv 2 (one literal per sanction-bearing provision): Tasks 2 + corpus-invariant test in Task 8
  - Inv 3 (act-type classification rule): Task 1 + Task 2
  - Inv 4 (SHACL property constraint): Task 5
  - Inv 5 (provision + file-level idempotency): Tasks 3 + 4
  - Inv 6 (coverage gate): Task 7
  - Inv 7 (`main() -> int` + `SystemExit`): Task 7
  - Inv 8 (zero new SHACL violations on `enforcedAtLevel`): Tasks 5 + 10

- [ ] **Test coverage matches the design.** 5 classes, 15 tests:
  - `TestClassifyEnforcementLevel` (4 unit, Task 1)
  - `TestSanctionExtractionWithEnforcementStamp` (4 integration, Task 2)
  - `TestSanctionsIdempotency` (4 idempotency, Tasks 3 + 4)
  - `TestSanctionsCoverageReport` (2 coverage, Task 7)
  - `TestCorpusInvariant` (1 corpus-wide, Task 8, marked `@pytest.mark.slow`)

- [ ] **No actionable gaps.** No "TODO", "TBD", "implement later", "code omitted for brevity" anywhere in the plan.

- [ ] **Type consistency.** `_classify_enforcement_level(act_node: dict) -> str` and `_find_act_node(doc: dict) -> dict | None` signatures are consistent across Tasks 1, 2, 3, 7. `main() -> int` consistent across Task 7 and the test files.

- [ ] **Idempotency invariants hold.** Three states tested: (peep_existing, fresh) ∈ {(yes, yes), (yes, no), (no, yes), (no, no)}. The (no, no) case must NOT mtime-touch the file.

- [ ] **One literal per provision.** Locked in by `test_provision_with_multiple_sanctions_gets_one_enforcement_literal` (Task 2) AND the corpus-wide invariant test (Task 8).

- [ ] **All 10 tasks have clear commit messages.**
