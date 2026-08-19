# KOV Integration Layer 2c — Institutional Competence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unpin `extract_institutional_competence.py`, add KOV body-word detection patterns, and bind matched bodies to the existing Layer 1 Issuer registry when the source act is a `MunicipalRegulation` with `enactedByMunicipality`.

**Architecture:** A canonical-body-slug helper (`_canonical_body_slug`) normalises Estonian inflections (linnavolikogu/vallavolikogu/alevivolikogu/linnavalitsus/vallavalitsus). A 3-priority resolver (`_resolve_kov_authority`) prefers the source act's own `enactedBy` issuer when its body-type and slug suffix match (Path 1), then derives a paired-body issuer from the source slug for cross-body references (Path 2), then falls back to municipality-wide uniqueness (Path 3) only when source_issuer is missing/unknown/inconsistent. Path 1 and Path 2 are AUTHORITATIVE — when source_issuer is known and consistent but neither path resolves, the resolver abstains rather than fall through to Path 3.

**Tech Stack:** Python 3.11+, pytest, JSON-LD, SHACL (pyshacl, rdflib). Builds on Layer 1's Issuer registry, Layer 2a's `kov_pipeline_coverage` helper, Layer 2b's `build_issuer_registry`, and Layer 2c PR #1's per-file idempotency convention (`_find_act_node`, `_id_ref`).

**Spec reference:** `docs/superpowers/specs/2026-05-04-kov-integration-layer2c-competence-design.md`.
**Predecessors:** Layer 2a (#98), Layer 2b (#99), Layer 2c PR #1 (#100), all merged to main.

---

## Crisp invariant

After this PR lands:

1. `extract_institutional_competence.py` calls `iter_peep_files()` with no `include_kov=False` argument. The single `# DEFERRED to Layer 2c` pin (line 239) is removed.
2. `GENERIC_PATTERNS` gains TWO new regex entries (KOV body words: `linna|valla|alevi`-prefixed `volikogu`; `linna|valla`-prefixed `valitsus` with all common Estonian case suffixes). Placed BEFORE `\b(vald|linn)\b` so more-specific matches take precedence.
3. KOV-scoped Issuer-binding fires when a generic body word matches AND the source act has `estleg:enactedByMunicipality` AND the resolver returns exactly one Issuer match. Otherwise the body-word detection abstains (NO `Institution_*` fallback for body-word detections).
4. Existing non-KOV institution detection (Riigikohus, ministries, courts, generic `local_government` / `kohalik_omavalitsus`) is unchanged.
5. The Issuer registry stays canonical: KOV-bound `competentAuthority` IRIs come from the same registry Layer 1 emits as `enactedBy` targets. NO parallel `institutions/institution_<body_slug>.json` files for KOV-bound resolutions.
6. Re-runs are idempotent at TWO layers: (a) per-file peep-side `competentAuthority` + `competenceType` cleared and recomputed every run; saves when EITHER fresh output OR pre-existing existed; (b) stale `institutions/institution_<slug>.json` files DELETED at end-of-run when no provisions cited the slug AND no per-peep errors occurred.
7. SHACL: `LegalProvisionShape`'s existing `competentAuthority` constraint at lines 140-145 (`sh:nodeKind sh:IRI`) is unchanged structurally; only the `sh:description` is updated to mention the broader Issuer/Institution range.
8. `main()` returns `int`; `raise SystemExit(main())` propagates the gate's exit code.
9. Coverage report `krr_outputs/reports/kov/extract_institutional_competence_coverage.json`: `files_with_output_kov > 0`, `triples_emitted_kov > 0`, `unresolved_references` reports body-word abstains, `fallback_hits` reports successful Path 3 resolutions.

**Real-world impact estimate** (read-only dry count): ~8,000 KOV peeps (72% of 11,059) gain `competentAuthority` triples binding to Issuer entities; ~34,615 individual body-word matches across KOV provisions.

**Not in this PR:** `extract_court_provision_links` (Layer 2c PR #3); per-authority `competenceType` reification; cleanup of the legacy `\b(vald|linn)\b` pattern.

---

## Worktree setup (do this first)

From the main checkout `/Users/henrikaavik/progemoge/law-ontology`:

```bash
git worktree add .worktrees/kov-layer2c-competence -b feature/kov-layer2c-competence
cd .worktrees/kov-layer2c-competence
```

Apply the guarded XML symlink restore (PR #1 convention):

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
        rm -rf data/riigiteataja
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

Expected: 206 passed (Layer 2c PR #1 end-state).

---

## File Structure

### New files

| Path | Responsibility |
| --- | --- |
| `tests/test_extract_institutional_competence.py` | 30 tests across 7 classes covering KOV body-word detection, the 3-priority resolver, KOV-vs-state integration, idempotency, institutions-file deletion, coverage report, and corpus-wide invariant. |

### Modified files

| Path | Change |
| --- | --- |
| `scripts/extract_institutional_competence.py` | Add `_CANONICAL_BODY_SLUGS` constant + `_canonical_body_slug` helper. Add 2 new `GENERIC_PATTERNS` entries (placed before `vald|linn`). Add `_swap_issuer_body_suffix` + `_resolve_kov_authority` + `_is_path3_case` resolvers. Import `_find_act_node` and `_id_ref` from `extract_sanctions` (Layer 2c PR #1 helpers). Build `issuer_registry` once at startup using Layer 2b's `build_issuer_registry`. Replace inline global pre-clear pass with per-file in-memory processing. Wire body-word matches through resolver; abstain when resolver returns None (no `Institution_*` fallback for body-word detections). Skip `inst_data` entry when resolver succeeds. Track `pre_existing_institution_files` + `written_slugs` + `_per_peep_errors` for end-of-run deletion of orphaned files. Add coverage instrumentation reusing `kov_pipeline_coverage.CoverageReport`. Change `main()` to return `int`. Use `raise SystemExit(main())`. Remove `include_kov=False` pin (line 239). |
| `shacl/estonian_legal_shapes.ttl` | Update the existing `competentAuthority` constraint's `sh:description` (lines 140-145) to mention the broader Issuer/Institution range. No new constraint block. |
| `docs/SCHEMA_REFERENCE.md` | Mark `competentAuthority` populated in the Layer 2 schema table; add a new subsection in the Institutional Competence section with KOV Issuer-binding semantics, a worked KOV example, and the SHACL constraint summary. |
| `CHANGELOG.md` | Layer 2c PR #2 entry under `## [Unreleased]` with Added/Changed/Coverage/Internal subsections. |

---

## Task 1: Detection — body-word patterns + canonicalizer + resolver helpers (TDD)

**Files:**
- Modify: `scripts/extract_institutional_competence.py`
- Create: `tests/test_extract_institutional_competence.py`

This task adds the pure functions: detection patterns, body-slug canonicalization, paired-issuer slug swap, the 3-priority resolver, and the `_is_path3_case` predicate. No `main()` integration yet.

- [ ] **Step 1: Create `tests/test_extract_institutional_competence.py` with 16 unit tests**

Create the file with the test scaffolding + `TestKovBodyWordDetection` (3 tests) + `TestResolveKovAuthority` (13 tests):

```python
"""Tests for scripts/extract_institutional_competence.py — Layer 2c PR #2."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


def _iter_kov_inclusive(*args, **kwargs):
    """Wrapper that forces include_kov=True regardless of caller args.

    Tasks 2-5 are scheduled BEFORE the unpin in Task 6, so production
    main() still calls iter_peep_files(include_kov=False) at this
    point. Tests that stage KOV fixtures must monkeypatch
    `mod.iter_peep_files` with this wrapper so KOV-staged peeps are
    visible regardless of whether the production unpin has landed yet.
    """
    from estleg_common import iter_peep_files as _real_iter
    kwargs.pop("include_kov", None)
    return _real_iter(include_kov=True, **kwargs)


class TestKovBodyWordDetection:
    """The new GENERIC_PATTERNS entries detect KOV body words and
    _canonical_body_slug normalises them across Estonian case
    inflections. These tests exercise the regex + canonicalizer as
    a unit (the regex match feeds directly into _canonical_body_slug
    in the production wiring)."""

    @pytest.mark.parametrize("text,expected", [
        ("linnavolikogu", "linnavolikogu"),
        ("linnavolikogus", "linnavolikogu"),
        ("linnavolikogule", "linnavolikogu"),
        ("linnavolikogusse", "linnavolikogu"),
    ])
    def test_linnavolikogu_matches_basic_form(self, text, expected):
        from extract_institutional_competence import (
            _canonical_body_slug,
            GENERIC_PATTERNS,
        )
        # Find the KOV volikogu pattern
        match = None
        for pat, _label, _itype in GENERIC_PATTERNS:
            m = pat.search(f"see {text} kehtestab korra")
            if m and m.group(0).lower() == text.lower():
                match = m
                break
        assert match is not None, f"no GENERIC_PATTERNS regex matched {text!r}"
        slug = _canonical_body_slug(match.group(0))
        assert slug == expected

    @pytest.mark.parametrize("text,expected", [
        # Six valitsus singular case forms each
        ("vallavalitsus", "vallavalitsus"),
        ("vallavalitsuse", "vallavalitsus"),
        ("vallavalitsust", "vallavalitsus"),
        ("vallavalitsusele", "vallavalitsus"),
        ("vallavalitsuses", "vallavalitsus"),
        ("vallavalitsusesse", "vallavalitsus"),
        ("vallavalitsusse", "vallavalitsus"),
        ("linnavalitsus", "linnavalitsus"),
        ("linnavalitsuse", "linnavalitsus"),
        ("linnavalitsust", "linnavalitsus"),
        ("linnavalitsusele", "linnavalitsus"),
        ("linnavalitsuses", "linnavalitsus"),
        ("linnavalitsusesse", "linnavalitsus"),
        ("linnavalitsusse", "linnavalitsus"),
    ])
    def test_vallavalitsus_inflections(self, text, expected):
        from extract_institutional_competence import (
            _canonical_body_slug,
            GENERIC_PATTERNS,
        )
        match = None
        for pat, _label, _itype in GENERIC_PATTERNS:
            m = pat.search(f"see {text} otsustab")
            if m and m.group(0).lower() == text.lower():
                match = m
                break
        assert match is not None, f"no regex matched {text!r}"
        slug = _canonical_body_slug(match.group(0))
        assert slug == expected

    def test_named_institutions_still_win(self):
        """Adding the new patterns must not override existing
        named-institution detection. detect_institutions returns
        Riigikohus first when both 'Riigikohus' and 'linnavolikogu'
        appear in the same provision text."""
        from extract_institutional_competence import detect_institutions
        results = detect_institutions(
            "Riigikohus otsustab; linnavolikogu kehtestab korra"
        )
        slugs = {iri_suffix for _name, iri_suffix, _t in results}
        assert "riigikohus" in slugs


class TestResolveKovAuthority:
    """Unit tests for the 3-priority resolver and the
    _is_path3_case predicate."""

    @pytest.fixture
    def reg(self):
        """Synthetic issuer registry: place name → (label, mun, body_type)."""
        return {
            "estleg:Issuer_abja_vallavolikogu": (
                "abja vallavolikogu",
                "estleg:Municipality_mulgi", "volikogu",
            ),
            "estleg:Issuer_abja_vallavalitsus": (
                "abja vallavalitsus",
                "estleg:Municipality_mulgi", "valitsus",
            ),
            "estleg:Issuer_xyz_vallavolikogu": (
                "xyz vallavolikogu",
                "estleg:Municipality_xyz", "volikogu",
            ),
            "estleg:Issuer_other_vallavalitsus": (
                "other vallavalitsus",
                "estleg:Municipality_xyz", "valitsus",
            ),
            "estleg:Issuer_elva_linnavalitsus": (
                "elva linnavalitsus",
                "estleg:Municipality_elva", "valitsus",
            ),
            "estleg:Issuer_elva_vallavolikogu": (
                "elva vallavolikogu",
                "estleg:Municipality_elva", "volikogu",
            ),
            "estleg:Issuer_singleton_vallavolikogu": (
                "singleton vallavolikogu",
                "estleg:Municipality_singleton", "volikogu",
            ),
            "estleg:Issuer_dup1_vallavolikogu": (
                "dup1 vallavolikogu",
                "estleg:Municipality_dup", "volikogu",
            ),
            "estleg:Issuer_dup2_vallavolikogu": (
                "dup2 vallavolikogu",
                "estleg:Municipality_dup", "volikogu",
            ),
        }

    def test_path1_source_issuer_same_body_resolves_directly(self, reg):
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_mulgi",
            source_issuer="estleg:Issuer_abja_vallavolikogu",
            issuer_registry=reg,
        )
        assert result == "estleg:Issuer_abja_vallavolikogu"

    def test_path2_paired_body_slug_swap(self, reg):
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavalitsus",
            source_municipality="estleg:Municipality_mulgi",
            source_issuer="estleg:Issuer_abja_vallavolikogu",
            issuer_registry=reg,
        )
        assert result == "estleg:Issuer_abja_vallavalitsus"

    def test_path1_suffix_mismatch_abstains(self, reg):
        """Source act enacted by a vallavolikogu; detected
        linnavolikogu — same body type but slug suffix mismatch.
        Resolver abstains."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="linnavolikogu",
            source_municipality="estleg:Municipality_mulgi",
            source_issuer="estleg:Issuer_abja_vallavolikogu",
            issuer_registry=reg,
        )
        assert result is None

    def test_path2_paired_body_missing_abstains_without_path3(self, reg):
        """Source act enacted by Issuer_xyz_vallavolikogu; detected
        body word vallavalitsus (SAME municipal family). Registry
        has NO Issuer_xyz_vallavalitsus but does have
        Issuer_other_vallavalitsus in the same modern municipality.
        Resolver MUST abstain — Path 3 must NOT bind to a conflated
        historical issuer."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavalitsus",
            source_municipality="estleg:Municipality_xyz",
            source_issuer="estleg:Issuer_xyz_vallavolikogu",
            issuer_registry=reg,
        )
        assert result is None

    def test_path2_cross_family_linna_to_valla_abstains(self, reg):
        """Source act enacted by Issuer_elva_linnavalitsus (urban
        executive); detected vallavolikogu (rural council). Even
        though Issuer_elva_vallavolikogu IS in the registry, the
        cross-family swap (linna* → valla*) must be rejected."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_elva",
            source_issuer="estleg:Issuer_elva_linnavalitsus",
            issuer_registry=reg,
        )
        assert result is None

    def test_path2_cross_family_valla_to_linna_abstains(self, reg):
        """Mirror of the prior test in the other direction."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="linnavolikogu",
            source_municipality="estleg:Municipality_mulgi",
            source_issuer="estleg:Issuer_abja_vallavalitsus",
            issuer_registry=reg,
        )
        assert result is None

    def test_municipality_mismatch_falls_through_to_path3(self, reg):
        """Layer 1 inconsistency: source act says
        Municipality_singleton but source_issuer's registry entry
        says Municipality_mulgi. Path 1+2 abstain; Path 3 then runs
        against the act's stated municipality."""
        from extract_institutional_competence import _resolve_kov_authority
        # Add a deliberately-inconsistent issuer to the registry
        reg_with_bad = dict(reg)
        reg_with_bad["estleg:Issuer_consistent_act_issuer"] = (
            "consistent act issuer",
            "estleg:Municipality_mulgi", "volikogu",  # registry says mulgi
        )
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_singleton",  # act says singleton
            source_issuer="estleg:Issuer_consistent_act_issuer",
            issuer_registry=reg_with_bad,
        )
        # Path 3 finds Issuer_singleton_vallavolikogu uniquely
        assert result == "estleg:Issuer_singleton_vallavolikogu"

    def test_municipality_mismatch_abstains_when_path3_also_empty(self, reg):
        """Same Layer 1 inconsistency, but the act's stated
        municipality has zero suffix-compatible matches. Resolver
        abstains entirely."""
        from extract_institutional_competence import _resolve_kov_authority
        reg_with_bad = dict(reg)
        reg_with_bad["estleg:Issuer_consistent_act_issuer"] = (
            "consistent act issuer",
            "estleg:Municipality_mulgi", "volikogu",
        )
        result = _resolve_kov_authority(
            body_slug="alevivolikogu",  # no alevi issuers in any test fixture
            source_municipality="estleg:Municipality_singleton",
            source_issuer="estleg:Issuer_consistent_act_issuer",
            issuer_registry=reg_with_bad,
        )
        assert result is None

    def test_path3_unique_fallback_resolves(self, reg):
        """source_issuer is None; Path 3 finds the unique
        suffix-compatible issuer in the source municipality."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_singleton",
            source_issuer=None,
            issuer_registry=reg,
        )
        assert result == "estleg:Issuer_singleton_vallavolikogu"

    def test_path3_ambiguous_returns_none(self, reg):
        """Path 3 finds two suffix-compatible issuers. Abstain."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality="estleg:Municipality_dup",
            source_issuer=None,
            issuer_registry=reg,
        )
        assert result is None

    def test_no_municipality_returns_none(self, reg):
        """source_municipality=None — every path requires it; abstain."""
        from extract_institutional_competence import _resolve_kov_authority
        result = _resolve_kov_authority(
            body_slug="vallavolikogu",
            source_municipality=None,
            source_issuer="estleg:Issuer_abja_vallavolikogu",
            issuer_registry=reg,
        )
        assert result is None

    @pytest.mark.parametrize("source_issuer,source_municipality,expected", [
        (None, "estleg:Municipality_singleton", True),
        ("estleg:Issuer_unknown_to_registry",
         "estleg:Municipality_singleton", True),
        ("estleg:Issuer_abja_vallavolikogu",
         "estleg:Municipality_mulgi", False),  # consistent
        ("estleg:Issuer_abja_vallavolikogu",
         "estleg:Municipality_singleton", True),  # mismatch
        (None, None, False),  # no municipality → nothing resolves anyway
    ])
    def test_is_path3_case_predicate(
        self, reg, source_issuer, source_municipality, expected
    ):
        from extract_institutional_competence import _is_path3_case
        assert _is_path3_case(
            source_issuer=source_issuer,
            source_municipality=source_municipality,
            issuer_registry=reg,
        ) is expected

    def test_is_path3_case_stays_in_sync_with_resolver(self, reg):
        """Parity test: when _is_path3_case returns False AND the
        resolver returns a non-None IRI, the IRI must come from
        Path 1 or Path 2 (source_issuer or its slug-swap output);
        when _is_path3_case returns True AND the resolver returns
        a non-None IRI, the IRI must come from Path 3 (registry
        scan in source_municipality)."""
        from extract_institutional_competence import (
            _resolve_kov_authority,
            _is_path3_case,
            _swap_issuer_body_suffix,
        )
        cases = [
            # (source_issuer, source_mun, body_slug, registry)
            ("estleg:Issuer_abja_vallavolikogu",
             "estleg:Municipality_mulgi", "vallavolikogu", reg),
            ("estleg:Issuer_abja_vallavolikogu",
             "estleg:Municipality_mulgi", "vallavalitsus", reg),
            (None, "estleg:Municipality_singleton", "vallavolikogu", reg),
            ("estleg:Issuer_unknown",
             "estleg:Municipality_singleton", "vallavolikogu", reg),
        ]
        for source_issuer, source_mun, body_slug, registry in cases:
            result = _resolve_kov_authority(
                body_slug=body_slug,
                source_municipality=source_mun,
                source_issuer=source_issuer,
                issuer_registry=registry,
            )
            is_p3 = _is_path3_case(
                source_issuer=source_issuer,
                source_municipality=source_mun,
                issuer_registry=registry,
            )
            if result is None:
                continue  # no observable parity bound on abstain
            if is_p3:
                # Path 3 result: must be a registry-wide unique match
                # (NOT source_issuer itself, NOT a slug-swap output).
                assert result != source_issuer
                if source_issuer is not None:
                    swapped = _swap_issuer_body_suffix(source_issuer, body_slug)
                    assert result != swapped
            else:
                # Path 1 or 2 result: must be source_issuer itself
                # OR the slug-swap output of source_issuer.
                if result == source_issuer:
                    continue  # Path 1
                swapped = _swap_issuer_body_suffix(source_issuer or "", body_slug)
                assert result == swapped, (
                    f"non-Path3 result {result!r} doesn't match Path 1 or 2"
                )
```

- [ ] **Step 2: Run the new tests (expect ImportError)**

Run: `pytest tests/test_extract_institutional_competence.py -v`
Expected: FAIL with `ImportError: cannot import name '_canonical_body_slug' from 'extract_institutional_competence'` (or similar — the module doesn't have any of these helpers yet).

- [ ] **Step 3: Add the new GENERIC_PATTERNS entries + helpers to `scripts/extract_institutional_competence.py`**

In `scripts/extract_institutional_competence.py`:

(a) Add the canonical-slug constant and helper near the top of the module (after the existing imports, before `NAMED_INSTITUTIONS`):

```python
# Five canonical body slugs the resolver and detection both accept.
# Re-used by _canonical_body_slug for validation.
_CANONICAL_BODY_SLUGS = frozenset({
    "linnavolikogu", "vallavolikogu", "alevivolikogu",
    "linnavalitsus", "vallavalitsus",
})


def _canonical_body_slug(matched: str) -> str | None:
    """Strip Estonian case suffixes from a KOV body-word match
    and return the canonical slug (linnavolikogu, vallavolikogu,
    alevivolikogu, linnavalitsus, vallavalitsus) or None if the
    match doesn't fit one of the five reachable stems.
    """
    s = matched.lower()
    # Strip trailing case suffixes greedily from longest to shortest
    # so 'vallavalitsusele' → 'vallavalitsus' (matches inner 'le' off
    # 'e' tail) and 'vallavalitsuses' → 'vallavalitsus' (matches 'es'
    # directly off the stem).
    for suffix in ("esse", "ele", "sse", "es", "le", "se", "ses", "s", "t", "e"):
        if s.endswith(suffix) and len(s) > len(suffix) + 4:
            stripped = s[: -len(suffix)]
            if stripped.endswith(("volikogu", "valitsus")):
                s = stripped
                break
    # Validate against the FIVE supported canonical forms.
    # alevivalitsus is intentionally excluded: small towns commonly
    # have a volikogu but rarely a separately-named executive.
    if s in _CANONICAL_BODY_SLUGS:
        return s
    return None
```

(b) Find the `GENERIC_PATTERNS` list (around line 145). Insert the two new entries BEFORE the `\b(vald|linn)\b` entry (around line 168):

```python
    # KOV body words (Layer 2c PR #2). Both regexes match across
    # all common Estonian case suffixes:
    #   nominative   linnavolikogu, vallavalitsus
    #   genitive     linnavolikogu (same), vallavalitsuse (-e)
    #   partitive    linnavolikogu (same), vallavalitsust (-t)
    #   allative     linnavolikogule (-le), vallavalitsusele (-ele)
    #   inessive     linnavolikogus (-s), vallavalitsuses (-ses)
    #   illative     linnavolikogusse (-sse), vallavalitsusse (-sse short),
    #                vallavalitsusesse (-esse)
    (re.compile(r"\b(?:linna|valla|alevi)volikogu(?:le|sse|s)?\b", re.IGNORECASE),
     "local_government_council", "local_government_body"),
    (re.compile(r"\b(?:linna|valla)valitsus(?:e(?:le|sse)?|es|se|t)?\b", re.IGNORECASE),
     "local_government_executive", "local_government_body"),
```

(c) Add the resolver helpers (place BEFORE `def main()`, after `detect_competence_type`):

```python
def _swap_issuer_body_suffix(
    source_issuer: str, target_body_slug: str
) -> str | None:
    """Given an Issuer @id like 'estleg:Issuer_abja_vallavolikogu'
    and a target body slug like 'vallavalitsus', return the paired
    Issuer @id 'estleg:Issuer_abja_vallavalitsus'.

    Returns None when:
    - The source IRI doesn't match the expected
      `Issuer_<place>_<body>` shape.
    - The source body and target body belong to DIFFERENT
      municipal families (linna* / valla* / alevi* are mutually
      exclusive). A linnavalitsus source must NOT pair with
      vallavolikogu target — same historical-conflation problem
      Path 1's suffix check rejects.
    """
    if not source_issuer.startswith("estleg:Issuer_"):
        return None
    suffix = source_issuer[len("estleg:Issuer_"):]
    source_body = None
    place = None
    for body in ("vallavolikogu", "vallavalitsus", "linnavolikogu",
                  "linnavalitsus", "alevivolikogu"):
        if suffix.endswith("_" + body):
            source_body = body
            place = suffix[: -len("_" + body)]
            break
    if source_body is None or place is None:
        return None

    family_prefixes = ("linna", "valla", "alevi")
    source_family = next(
        (p for p in family_prefixes if source_body.startswith(p)), None)
    target_family = next(
        (p for p in family_prefixes if target_body_slug.startswith(p)), None)
    if source_family is None or target_family is None:
        return None
    if source_family != target_family:
        return None

    return f"estleg:Issuer_{place}_{target_body_slug}"


def _resolve_kov_authority(
    body_slug: str,
    source_municipality: str | None,
    source_issuer: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
) -> str | None:
    """Resolve a KOV body-word detection to an Issuer @id.

    Three priority paths (in order):
      1. Same body type AND slug suffix match source_issuer →
         return source_issuer.
      2. Opposite body type → derive paired issuer slug; return
         it if registered AND in same municipality.
      3. Path 3 fallback: registry-wide unique match in
         source_municipality. Reached ONLY when source_issuer is
         missing, unknown to the registry, or has municipality
         inconsistent with the act's stated enactedByMunicipality.

    When source_issuer is known and consistent (Path 1+2
    authoritative), Path 3 is NOT consulted — abstain instead.
    """
    if source_municipality is None:
        return None

    body_type_map = {
        "linnavolikogu": "volikogu",
        "vallavolikogu": "volikogu",
        "alevivolikogu": "volikogu",
        "linnavalitsus": "valitsus",
        "vallavalitsus": "valitsus",
    }
    target_body_type = body_type_map.get(body_slug)
    if target_body_type is None:
        return None

    source_issuer_authoritative = False
    if source_issuer is not None:
        source_entry = issuer_registry.get(source_issuer)
        if source_entry is not None:
            _label, source_mun, source_body_type = source_entry
            if source_mun == source_municipality:
                source_issuer_authoritative = True

                if (source_body_type == target_body_type
                        and source_issuer.endswith("_" + body_slug)):
                    return source_issuer

                if source_body_type != target_body_type:
                    paired_iri = _swap_issuer_body_suffix(source_issuer, body_slug)
                    if paired_iri is not None and paired_iri in issuer_registry:
                        paired_entry = issuer_registry.get(paired_iri)
                        if (paired_entry is not None
                                and paired_entry[1] == source_municipality):
                            return paired_iri
                    return None

                return None

    if source_issuer_authoritative:
        return None
    suffix = "_" + body_slug
    matches = [
        issuer_iri
        for issuer_iri, (_label, mun_iri, btype) in issuer_registry.items()
        if mun_iri == source_municipality
        and btype == target_body_type
        and issuer_iri.endswith(suffix)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _is_path3_case(
    source_issuer: str | None,
    source_municipality: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
) -> bool:
    """Return True iff the resolver's Path 1+2 are not
    authoritative for this case (i.e. Path 3 is what would run).
    Mirrors the resolver's source_issuer_authoritative check;
    used by main() to count fallback_hits."""
    if source_municipality is None:
        return False
    if source_issuer is None:
        return True
    entry = issuer_registry.get(source_issuer)
    if entry is None:
        return True
    _label, source_mun, _btype = entry
    return source_mun != source_municipality
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `pytest tests/test_extract_institutional_competence.py -v`
Expected: 16 passed (3 detection + 13 resolver).

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `pytest -q`
Expected: 222 passed (206 baseline + 16 new).

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_institutional_competence.py tests/test_extract_institutional_competence.py
git commit -m "Add KOV body-word detection + 3-priority resolver helpers

GENERIC_PATTERNS gains two new entries for KOV body words
(linnavolikogu / vallavolikogu / alevivolikogu / linnavalitsus
/ vallavalitsus) covering all common Estonian case inflections.

_canonical_body_slug strips suffixes greedily and validates
against the five reachable canonical slugs (alevivalitsus
intentionally excluded — not in GENERIC_PATTERNS).

_resolve_kov_authority implements the 3-priority resolution
strategy: Path 1 (same-body source_issuer direct return);
Path 2 (paired-body slug swap with municipal-family
compatibility); Path 3 (registry-wide unique match in
source_municipality, reached only when source_issuer is
missing/unknown/inconsistent).

_is_path3_case mirrors the resolver's source_issuer_authoritative
branch logic; used by main() to count fallback_hits without
changing the resolver signature.

Pure functions; no side effects; no caller wired up yet."
```

---

## Task 2: Wire the resolver into `main()` (TDD)

**Files:**
- Modify: `scripts/extract_institutional_competence.py`
- Modify: `tests/test_extract_institutional_competence.py` (add `TestExtractCompetenceWithIssuerBinding`)

The current per-provision loop builds `authority_refs` from `detect_institutions` output. This task threads the resolver between detection and emission: for `local_government_body` detections, call `_resolve_kov_authority`; on success, append the Issuer IRI; on None, abstain (no fallback to `Institution_*`). Other institution types keep the existing path unchanged. Introduces minimal `_unresolved_count` and `_fallback_hits` counters in `main()` for forward compatibility with Task 7's coverage instrumentation.

- [ ] **Step 1: Append `TestExtractCompetenceWithIssuerBinding` to the test file**

```python
class TestExtractCompetenceWithIssuerBinding:
    """End-to-end via main(). Same _iter_kov_inclusive monkeypatch
    pattern as Layer 2c PR #1's tests."""

    @pytest.fixture
    def issuers_registry_file(self, tmp_path):
        """Stage a minimal issuers_kov_peep.json in tmp_path/krr_outputs/
        so build_issuer_registry returns a usable mapping."""
        krr = tmp_path / "krr_outputs"
        krr.mkdir(exist_ok=True)
        path = krr / "issuers_kov_peep.json"
        path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_tallinna_linnavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tallinna Linnavolikogu",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Issuer_tallinna_linnavalitsus",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tallinna Linnavalitsus",
                 "estleg:bodyType": "valitsus",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
            ],
        }), encoding="utf-8")
        return krr

    def test_kov_act_competence_binds_to_issuer(
        self, issuers_registry_file, monkeypatch
    ):
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        # Stage a Tallinn KOV act with a provision citing linnavolikogu
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        peep = kov / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_X_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_TLN_X_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Linnavolikogu kehtestab maksu määra."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Reg_TLN_X_Par_1")
        # competentAuthority list contains the Issuer IRI
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        assert "estleg:Issuer_tallinna_linnavolikogu" in ids
        # No body-slug Institution_linnavolikogu fallback
        assert not any(i and i.endswith("Institution_linnavolikogu") for i in ids)
        # No body-slug institution file written
        assert not (institutions_dir / "institution_linnavolikogu.json").exists()

    def test_state_law_competence_with_kov_body_word_abstains(
        self, issuers_registry_file, monkeypatch
    ):
        """Law-typed peep (no enactedByMunicipality). Detected
        linnavolikogu must NOT bind."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        peep = krr / "kov_seadus_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:KOKS_Map",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:KOKS_Par_22",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 22",
                 "estleg:summary":
                     "Linnavolikogu kehtestab kohaliku elu küsimuste "
                     "lahendamise korra."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:KOKS_Par_22")
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        # Neither Issuer_* nor Institution_linnavolikogu
        assert not any(i and "linnavolikogu" in (i or "") for i in ids)

    def test_kov_act_with_named_authority_still_emits_institution(
        self, issuers_registry_file, monkeypatch
    ):
        """Named institutions (Riigikohus etc.) follow the existing
        Institution_* path even in KOV peeps."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        peep = kov / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_Y_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_TLN_Y_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Riigikohus otsustab vaidlused selle määruse alusel."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Reg_TLN_Y_Par_1")
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        assert "estleg:Institution_riigikohus" in ids
        # Sibling per-institution file emitted
        assert (institutions_dir / "institution_riigikohus.json").exists()
```

- [ ] **Step 2: Run the new tests (expect 3 failures — wiring not done yet)**

Run: `pytest tests/test_extract_institutional_competence.py::TestExtractCompetenceWithIssuerBinding -v`
Expected: 3 failures.

- [ ] **Step 3: Wire the resolver into `main()`**

In `scripts/extract_institutional_competence.py`:

(a) Add an import line near the top with the other imports:

```python
from extract_sanctions import _find_act_node, _id_ref
```

These helpers were added in Layer 2c PR #1. They are in `scripts/extract_sanctions.py` (`_find_act_node` matches act nodes typed both `estleg:Act` AND `owl:Ontology`; `_id_ref` unwraps JSON-LD `@id` object form).

If the import raises (e.g. circular-import quirk), inline copies of both helpers are acceptable — they're 5 lines each. Verify by running `python3 -c "from extract_sanctions import _find_act_node, _id_ref; print('OK')"` from the worktree root.

(b) Inside `main()`, immediately AFTER `law_files = iter_peep_files(include_kov=False)` (line 239 — DO NOT remove the pin yet, that's Task 6), build the issuer registry:

```python
    # Layer 2c PR #2: build issuer registry once at startup.
    # Mirrors Layer 2b's build_issuer_registry shape.
    from extract_cross_references import build_issuer_registry
    issuer_registry = build_issuer_registry(
        KRR_DIR / "issuers_kov_peep.json"
    )
```

(c) At the top of `main()` near the other counters, initialize:

```python
    _unresolved_count = 0
    _fallback_hits = 0
```

(d) In the per-provision loop (around lines 280-310 — find via `grep -n "authority_refs = \[\]"`), modify the `for canon_name, iri_suffix, itype in institutions:` block. Replace the existing body with the resolver-aware version:

```python
            # Per-peep act node + scope (read once outside the inner loop)
            # — this should be hoisted to right after `doc = load_json(filepath)`
            # at the top of the outer `for idx, filepath in enumerate(law_files, 1):`
            # loop. See (e) below.
            for canon_name, iri_suffix, itype in institutions:
                if itype == "local_government_body":
                    # KOV body-word path: resolve via Issuer registry.
                    canonical = _canonical_body_slug(canon_name)
                    if canonical is None:
                        # Detection produced an unsupported slug (regex
                        # over-match etc.) — skip silently.
                        continue
                    issuer_iri = _resolve_kov_authority(
                        body_slug=canonical,
                        source_municipality=source_municipality,
                        source_issuer=source_issuer,
                        issuer_registry=issuer_registry,
                    )
                    if issuer_iri is None:
                        _unresolved_count += 1
                        continue  # abstain — no Institution_* fallback
                    # Path 3 firings count as fallback hits
                    if _is_path3_case(
                        source_issuer=source_issuer,
                        source_municipality=source_municipality,
                        issuer_registry=issuer_registry,
                    ):
                        _fallback_hits += 1
                    authority_refs.append({"@id": issuer_iri})
                    # Do NOT add to inst_data — Issuer entity already lives
                    # in the registry; no parallel Institution file needed.
                    continue

                # Original Institution_* path for everything else
                inst_iri = f"estleg:Institution_{iri_suffix}"
                if inst_iri not in inst_data:
                    inst_data[inst_iri] = {
                        "name": canon_name,
                        "iri_suffix": iri_suffix,
                        "type": itype,
                    }
                inst_provisions[inst_iri].append(
                    (provision_iri, competence_type, law_name)
                )
                authority_refs.append({"@id": inst_iri})
```

(e) Hoist the act-node lookup to OUTSIDE the per-provision inner loop. Find the start of the outer per-file loop (`for idx, filepath in enumerate(law_files, 1):` around line 269) and add right after `doc = load_json(filepath)` + the early-return:

```python
        act_node = _find_act_node(doc)
        if act_node is None:
            # Aggregate-registry peep or malformed; Task 7 adds the
            # full coverage skip-reason wiring. For Task 2, just skip.
            continue
        source_municipality = _id_ref(act_node.get("estleg:enactedByMunicipality"))
        source_issuer = _id_ref(act_node.get("estleg:enactedBy"))
```

This block must run BEFORE the per-node `for node in doc["@graph"]:` loop so `source_municipality` and `source_issuer` are in scope when the resolver fires.

(f) Update the `INSTIT_DIR` reference. Look for the existing module-level constant (e.g. `INSTIT_DIR = KRR_DIR / "institutions"`). If it doesn't exist, add it near `KRR_DIR`:

```python
INSTIT_DIR = KRR_DIR / "institutions"
```

If `institutions/` directory creation is currently inline in `main()`, make sure it stays — Task 4's stale-file-deletion logic relies on the directory existing.

- [ ] **Step 4: Run the integration tests to verify they pass**

Run: `pytest tests/test_extract_institutional_competence.py::TestExtractCompetenceWithIssuerBinding -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: 225 passed (222 + 3).

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_institutional_competence.py tests/test_extract_institutional_competence.py
git commit -m "Wire KOV body-word resolver into extract_institutional_competence main()

main() now finds the source act node once per peep file (via
_find_act_node imported from extract_sanctions), reads
source_municipality and source_issuer (via _id_ref), and threads
both into _resolve_kov_authority for every local_government_body
detection. On resolver success, the resolved estleg:Issuer_* IRI
goes into competentAuthority directly — NO parallel Institution_*
entry, no per-institution file. On resolver None, the body-word
detection abstains (no Institution_<body_slug> fallback).

Other institution types (named institutions, ministries, courts,
generic local_government / kohalik_omavalitsus) follow the
existing Institution_* path unchanged.

issuer_registry built once at startup via Layer 2b's
build_issuer_registry. Minimal _unresolved_count and
_fallback_hits counters introduced for forward compatibility
with Task 7's coverage instrumentation."
```

---

## Task 3: Refactor to per-file in-memory idempotency (TDD)

**Files:**
- Modify: `scripts/extract_institutional_competence.py`
- Modify: `tests/test_extract_institutional_competence.py` (add `TestCompetenceIdempotency`)

The current `main()` has an inline global pre-clear pass at lines ~243-258 (walks every file, strips `competentAuthority` and `competenceType`, saves). Replace with per-file in-memory processing: detect prior peep-side output BEFORE clearing, run extraction, save when EITHER fresh output exists OR pre-existing existed.

- [ ] **Step 1: Append `TestCompetenceIdempotency` to the test file**

```python
class TestCompetenceIdempotency:
    """Idempotency at the peep level: clear → extract → save iff
    either fresh output OR prior output existed."""

    @pytest.fixture
    def issuers_registry_file(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        path = krr / "issuers_kov_peep.json"
        path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_tallinna_linnavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tallinna Linnavolikogu",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
            ],
        }), encoding="utf-8")
        return krr

    def test_stale_competentauthority_cleared_when_text_changes(
        self, issuers_registry_file, monkeypatch
    ):
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        # Stage a peep with PRIOR competentAuthority + competenceType
        # but text that doesn't match any current detection.
        peep = krr / "stale_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Stale_Map",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Stale_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Käesolev säte kirjeldab üldist eesmärki.",
                 "estleg:competentAuthority": [
                     {"@id": "estleg:Institution_riigikohus"}],
                 "estleg:competenceType": "supervision"},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Stale_Par_1")
        assert "estleg:competentAuthority" not in prov
        assert "estleg:competenceType" not in prov

    def test_classification_recomputed_from_act_type(
        self, issuers_registry_file, monkeypatch
    ):
        """A peep's enactedByMunicipality flips between runs;
        recomputed resolution reflects the new value, not the
        stale prior emission."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        peep = kov / "act_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_Z_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_TLN_Z_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Linnavolikogu kehtestab maksu.",
                 "estleg:competentAuthority": [
                     {"@id": "estleg:Institution_some_stale_value"}]},
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        with open(peep, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        prov = next(n for n in saved["@graph"]
                    if n.get("@id") == "estleg:Reg_TLN_Z_Par_1")
        refs = prov.get("estleg:competentAuthority", [])
        if isinstance(refs, dict):
            refs = [refs]
        ids = [r.get("@id") for r in refs]
        # Recomputed; stale Institution_some_stale_value is GONE.
        assert "estleg:Issuer_tallinna_linnavolikogu" in ids
        assert "estleg:Institution_some_stale_value" not in ids

    def test_no_op_when_no_existing_no_fresh(
        self, issuers_registry_file, monkeypatch
    ):
        """Peep with no detections AND no prior triples — mtime
        preserved (no spurious write)."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)
        peep = krr / "boring_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Boring_Map",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Boring_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary":
                     "Käesolev seadus reguleerib üldisi suhteid."},
            ],
        }), encoding="utf-8")
        before = peep.stat().st_mtime
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        after = peep.stat().st_mtime
        assert before == after, "no-op file must not be re-saved"
```

- [ ] **Step 2: Run the idempotency tests (expect at least one failure)**

Run: `pytest tests/test_extract_institutional_competence.py::TestCompetenceIdempotency -v`
Expected: At least `test_no_op_when_no_existing_no_fresh` fails — current code mtime-touches every file via the global pre-clear pass.

- [ ] **Step 3: Replace the global pre-clear with per-file in-memory processing**

In `scripts/extract_institutional_competence.py`:

(a) Find lines ~243-258 — the inline global pre-clear pass:

```python
    print("  Clearing old competence data from all files...")
    for filepath in law_files:
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue
        cleared = False
        for node in doc["@graph"]:
            if "estleg:competentAuthority" in node:
                del node["estleg:competentAuthority"]
                cleared = True
            if "estleg:competenceType" in node:
                del node["estleg:competenceType"]
                cleared = True
        if cleared:
            save_json(filepath, doc)
    print("  Done clearing.")
```

DELETE this entire block. Replace with:

```python
    print("\n[1/4] Processing law files (per-file idempotent)...")
```

(b) In the per-file loop body (the outer `for idx, filepath in enumerate(law_files, 1):` block — should now start around line 248 after the deletion), modify the in-memory processing.

Find the existing structure:

```python
    for idx, filepath in enumerate(law_files, 1):
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue
        # ... act_node + source_municipality + source_issuer (added in Task 2) ...
        law_name = filepath.stem.replace("_peep", "")
        modified = False
        for node in doc["@graph"]:
            # ... per-node processing ...
        if modified:
            save_json(filepath, doc)
```

Wrap the per-file processing with prior-output detection and conditional save. The MODIFIED structure:

```python
    for idx, filepath in enumerate(law_files, 1):
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue

        act_node = _find_act_node(doc)
        if act_node is None:
            continue
        source_municipality = _id_ref(act_node.get("estleg:enactedByMunicipality"))
        source_issuer = _id_ref(act_node.get("estleg:enactedBy"))

        # Detect prior peep-side output BEFORE clearing.
        had_existing_peep = any(
            ("estleg:competentAuthority" in n)
            or ("estleg:competenceType" in n)
            for n in doc["@graph"]
        )

        # Clear unconditionally — the per-provision loop below
        # re-emits when detection succeeds.
        for n in doc["@graph"]:
            n.pop("estleg:competentAuthority", None)
            n.pop("estleg:competenceType", None)

        law_name = filepath.stem.replace("_peep", "")
        modified = False

        for node in doc["@graph"]:
            # ... existing per-provision processing, including the
            # Task 2 resolver wiring ...

        # Save when EITHER fresh output OR pre-existing output existed.
        if modified or had_existing_peep:
            save_json(filepath, doc)
```

The `modified = True` flag should already be set inside the per-provision loop whenever `authority_refs` becomes non-empty (existing pattern). Don't change that logic.

- [ ] **Step 4: Run the idempotency tests to verify they pass**

Run: `pytest tests/test_extract_institutional_competence.py::TestCompetenceIdempotency -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: 228 passed (225 + 3).

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_institutional_competence.py tests/test_extract_institutional_competence.py
git commit -m "Refactor competence extraction to per-file idempotent processing

Replaces the inline global pre-clear pass (which mtime-touched
every file) with per-file in-memory processing. Each peep is
loaded once; the per-file flow detects pre-existing peep-side
output (competentAuthority or competenceType anywhere in the
graph) BEFORE clearing, runs detection + resolver, and saves
the file when EITHER fresh output exists OR pre-existing output
existed.

Without the conditional save, a peep that loses all its
detections between runs would carry stale triples on disk
forever. Three new TestCompetenceIdempotency tests lock in the
contract: stale clear when text changes, stateless recomputation
on @type / enactedByMunicipality flips, no-op preservation when
neither fresh nor prior output exists.

Stale institutions/<slug>.json file deletion is Task 4."
```

---

## Task 4: Stale `institutions/<slug>.json` deletion + minimal error counter (TDD)

**Files:**
- Modify: `scripts/extract_institutional_competence.py`
- Modify: `tests/test_extract_institutional_competence.py` (add `TestStaleInstitutionFileDeletion`)

The script's existing per-institution writer block (around lines 320-330) emits `institutions/institution_<slug>.json` for every institution that gathered any provision references this run. It NEVER deletes a file whose institution has no provisions referencing it after re-run. This task adds end-of-run pruning: track `pre_existing_institution_files`, build `written_slugs` during the writer pass, and unlink the orphaned files when no per-peep errors occurred.

- [ ] **Step 1: Append `TestStaleInstitutionFileDeletion` to the test file**

```python
class TestStaleInstitutionFileDeletion:
    """End-of-run institution-file pruning. Stale files are unlinked
    only when no per-peep errors occurred."""

    @pytest.fixture
    def issuers_registry_file(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        path = krr / "issuers_kov_peep.json"
        path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_dummy",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Dummy",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_x"}},
            ],
        }), encoding="utf-8")
        return krr

    def test_stale_institutions_json_deleted_when_no_provisions_cite_it(
        self, issuers_registry_file, monkeypatch
    ):
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        # Stage a STALE institution file from a prior run
        stale = institutions_dir / "institution_xyz.json"
        stale.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [{"@id": "estleg:Institution_xyz",
                        "@type": ["estleg:Institution"]}],
        }), encoding="utf-8")
        # Stage a peep that cites NOTHING relevant
        peep = krr / "boring_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Boring_Map",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Boring_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Käesolev seadus on üldine."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        assert not stale.exists(), "stale institution file must be unlinked"

    def test_living_institution_files_preserved(
        self, issuers_registry_file, monkeypatch
    ):
        """A pre-existing institutions/institution_riigikohus.json
        AND a peep that triggers Riigikohus detection — the file
        MUST be preserved (NOT deleted via incorrect stem-prefix
        comparison)."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        living = institutions_dir / "institution_riigikohus.json"
        living.write_text(json.dumps({
            "@context": {"estleg": "https://data.riik.ee/ontology/estleg#"},
            "@graph": [{"@id": "estleg:Institution_riigikohus",
                        "@type": ["estleg:Institution"],
                        "rdfs:label": "old label"}],
        }), encoding="utf-8")
        peep = krr / "active_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Active_Map",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:Active_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Riigikohus otsustab vaidlused."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        # File must still exist (regenerated, not deleted)
        assert living.exists()

    def test_deletion_skipped_when_per_peep_errors_present(
        self, issuers_registry_file, monkeypatch
    ):
        """When at least one peep returns load_json None or no @graph,
        stale-file deletion is skipped (preserves files whose owning
        peeps failed to load)."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        institutions_dir.mkdir(parents=True, exist_ok=True)

        stale = institutions_dir / "institution_unused.json"
        stale.write_text("{}", encoding="utf-8")
        # Stage a malformed peep so load_json returns None
        broken = krr / "broken_peep.json"
        broken.write_text("not json", encoding="utf-8")
        # Stage a normal peep with no relevant detection
        peep = krr / "ok_peep.json"
        peep.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:OK_Map",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:OK_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Üldnorm."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        # File preserved because the run had per-peep errors
        assert stale.exists()
```

- [ ] **Step 2: Run the new tests (expect failures)**

Run: `pytest tests/test_extract_institutional_competence.py::TestStaleInstitutionFileDeletion -v`
Expected: at least 2 failures (deletion logic doesn't exist yet).

- [ ] **Step 3: Add stale-file deletion + per-peep error counter to `main()`**

In `scripts/extract_institutional_competence.py`:

(a) At the top of `main()` near the other counters, add:

```python
    _per_peep_errors = 0
    pre_existing_institution_files: list[Path] = []
    if INSTIT_DIR.exists():
        pre_existing_institution_files = list(INSTIT_DIR.glob("institution_*.json"))
    written_slugs: set[str] = set()
```

(b) Inside the per-file outer loop, increment `_per_peep_errors` when load fails:

```python
    for idx, filepath in enumerate(law_files, 1):
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            _per_peep_errors += 1
            continue
        # ... rest unchanged ...
```

(c) Inside the per-institution writer loop (find via `grep -n "for inst_iri, info in sorted"`), capture written slugs. The writer block looks like:

```python
    for inst_iri, info in sorted(inst_data.items()):
        provisions = inst_provisions[inst_iri]
        # ... writes institutions/institution_<iri_suffix>.json ...
```

After the file is written, add:

```python
        written_slugs.add(info["iri_suffix"])
```

(d) AFTER the writer loop and AFTER the report generation, add the deletion guard:

```python
    # Layer 2c PR #2: stale-file pruning. Only delete when the run
    # was clean (no per-peep load errors); otherwise we might delete
    # a file whose owning peep failed to load.
    if _per_peep_errors == 0:
        for path in pre_existing_institution_files:
            slug = path.stem.removeprefix("institution_")
            if slug not in written_slugs:
                try:
                    path.unlink()
                except OSError:
                    pass
    else:
        print(f"[skip stale-deletion] {_per_peep_errors} per-peep "
              f"errors during this run; preserving "
              f"{len(pre_existing_institution_files)} pre-existing "
              f"institution files.")
```

- [ ] **Step 4: Run the deletion tests to verify they pass**

Run: `pytest tests/test_extract_institutional_competence.py::TestStaleInstitutionFileDeletion -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: 231 passed (228 + 3).

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_institutional_competence.py tests/test_extract_institutional_competence.py
git commit -m "Delete stale institution files at end of run

main() now snapshots existing institutions/institution_*.json
files at startup, tracks the slug set written during the
per-institution writer pass, and at end-of-run unlinks any
pre-existing files whose slug wasn't written. Without this,
orphan Institution_* nodes survive in krr_outputs/institutions/
pointing at provisions that no longer cite them.

Deletion is gated on _per_peep_errors == 0 — if any peep
returned None from load_json (malformed JSON / OSError), the
deletion is skipped to avoid removing a file whose owning peep
just happened to fail to load. Three new tests lock in the
contract: deletion fires for orphans, living files preserved
across the path.stem.removeprefix('institution_') comparison,
deletion skipped on per-peep errors."
```

---

## Task 5: Update SHACL `competentAuthority` description

**Files:**
- Modify: `shacl/estonian_legal_shapes.ttl`

The constraint already exists at lines 140-145. This task ONLY refreshes the `sh:description` to mention the broader Issuer/Institution range.

- [ ] **Step 1: Read the existing constraint**

```bash
grep -nB1 -A4 "competentAuthority" shacl/estonian_legal_shapes.ttl
```

Expected output:

```
140-    sh:property [
141:        sh:path estleg:competentAuthority ;
142-        sh:nodeKind sh:IRI ;
143-        sh:name "competentAuthority" ;
144-        sh:description "Institution responsible for enforcing or implementing this provision" ;
145-    ] ;
```

- [ ] **Step 2: Edit only the description line**

In `shacl/estonian_legal_shapes.ttl`, replace:

```turtle
        sh:description "Institution responsible for enforcing or implementing this provision" ;
```

with:

```turtle
        sh:description "Institution or Issuer responsible for enforcing or implementing this provision. May be an estleg:Institution_* IRI (named institutions, ministries, courts, generic local-government references) or an estleg:Issuer_* IRI (when the source act is a MunicipalRegulation with enactedByMunicipality scope and the body word resolves to a registered Issuer)." ;
```

Do NOT add a new `sh:property` block. Do NOT change `sh:nodeKind` or any other field.

- [ ] **Step 3: Sanity-parse the TTL**

```bash
python3 -c "
from rdflib import Graph
g = Graph()
g.parse('shacl/estonian_legal_shapes.ttl', format='turtle')
print(f'OK — {len(g)} triples loaded')
"
```

Expected: `OK — <some number> triples loaded`. If the parse fails, the description string had a syntax error.

- [ ] **Step 4: Verify exactly one constraint exists for `competentAuthority`**

```bash
python3 -c "
from rdflib import Graph
SHACL = 'http://www.w3.org/ns/shacl#'
ESTLEG = 'https://data.riik.ee/ontology/estleg#'
g = Graph()
g.parse('shacl/estonian_legal_shapes.ttl', format='turtle')
q = '''
SELECT (COUNT(?prop) AS ?n) WHERE {
  ?prop <%spath> <%scompetentAuthority> .
}
''' % (SHACL, ESTLEG)
result = list(g.query(q))[0]
n = int(result[0])
assert n == 1, f'expected 1 competentAuthority constraint, got {n}'
print(f'Constraint count: {n}')
"
```

Expected: `Constraint count: 1`.

- [ ] **Step 5: Run the existing SHACL tests**

Run: `pytest tests/test_shacl_kov_layer1.py -v`
Expected: all existing SHACL tests still pass.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: 231 passed (no test count change).

- [ ] **Step 7: Commit**

```bash
git add shacl/estonian_legal_shapes.ttl
git commit -m "Update competentAuthority SHACL description for Issuer range

Layer 2c PR #2 broadens estleg:competentAuthority's value range
to include estleg:Issuer_* IRIs in addition to the existing
estleg:Institution_* form. The constraint structure
(sh:nodeKind sh:IRI) already accepts both; this commit only
refreshes the sh:description to document the new semantics
and references the resolver's binding rules.

No new constraint block. The existing constraint at lines
140-145 of LegalProvisionShape gains the updated description."
```

---

## Task 6: Unpin — remove `include_kov=False`

**Files:**
- Modify: `scripts/extract_institutional_competence.py` (line 239 only).

- [ ] **Step 1: Confirm the pin location**

```bash
grep -n "include_kov" scripts/extract_institutional_competence.py
```

Expected: one line — `law_files = iter_peep_files(include_kov=False)  # DEFERRED to Layer 2c`.

- [ ] **Step 2: Edit the pin**

Open `scripts/extract_institutional_competence.py`. Find:

```python
    law_files = iter_peep_files(include_kov=False)  # DEFERRED to Layer 2c
```

Replace with:

```python
    law_files = iter_peep_files()
```

- [ ] **Step 3: Confirm the pin is gone**

```bash
grep -n "include_kov" scripts/extract_institutional_competence.py
```

Expected: empty output.

- [ ] **Step 4: AST-parse the file**

```bash
python3 -c "import ast; ast.parse(open('scripts/extract_institutional_competence.py').read())" && echo OK
```

Expected: `OK`.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: 231 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_institutional_competence.py
git commit -m "Unpin extract_institutional_competence — runs KOV by default

Removes the include_kov=False argument and the trailing 'DEFERRED
to Layer 2c' comment. iter_peep_files() now defaults to
include_kov=True (the Layer 2a default), so the script processes
laws + state regulations + KOV acts in one pass.

Real-world impact (read-only dry count): ~8,000 KOV peeps
(72% of 11,059) gain competentAuthority triples binding to
Issuer entities; ~34,615 individual body-word matches across
KOV provisions.

Coverage gate verification on the corpus run lands in Task 8."
```

---

## Task 7: Coverage instrumentation + `main() -> int` (TDD)

**Files:**
- Modify: `scripts/extract_institutional_competence.py`
- Modify: `tests/test_extract_institutional_competence.py` (add `TestCompetenceCoverageReport`)

Reuses `kov_pipeline_coverage.CoverageReport` (Layer 2a). `set[Path]` accumulators at the call site; `len()` feeds the helper's int fields. Wires the `_unresolved_count` and `_fallback_hits` counters introduced in Task 2 into `unresolved_references` and `fallback_hits`. Adds the aggregate-vs-malformed peep distinction (uses `estleg:paragrahv` as provision signal).

- [ ] **Step 1: Append `TestCompetenceCoverageReport` (4 tests)**

```python
class TestCompetenceCoverageReport:
    """Coverage report shape + gate behavior."""

    @pytest.fixture
    def issuers_registry_file(self, tmp_path):
        krr = tmp_path / "krr_outputs"
        krr.mkdir()
        path = krr / "issuers_kov_peep.json"
        path.write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_tallinna_linnavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tallinna Linnavolikogu",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
            ],
        }), encoding="utf-8")
        return krr

    def test_coverage_report_written_with_kov_split(
        self, issuers_registry_file, monkeypatch
    ):
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        institutions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        # 1 KOV act with body-word + 1 state law with named authority
        kov = krr / "regulations" / "kov" / "tallinna_linnavolikogu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_TLN_K_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_tallinna_linnavolikogu"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_TLN_K_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Linnavolikogu kehtestab maksu."},
            ],
        }), encoding="utf-8")
        (krr / "state_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:State_Map",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:State_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Riigikohus otsustab vaidlused."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc == 0

        cov_path = reports_dir / "extract_institutional_competence_coverage.json"
        assert cov_path.exists()
        with open(cov_path, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        assert cov["pipeline"] == "extract_institutional_competence"
        assert cov["files_processed_kov"] >= 1
        assert cov["files_with_output_kov"] >= 1
        assert cov["triples_emitted_kov"] >= 1
        assert cov["files_with_output"] >= 2

    def test_gate_fails_when_kov_input_nontrivial_but_no_output(
        self, issuers_registry_file, monkeypatch
    ):
        """Triple-monkeypatch trick (iter_peep_files + load_json +
        save_json) — 11,001 synthetic KOV paths with no detection."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        institutions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        kov_marker = krr / "regulations" / "kov" / "no_match"
        synthetic_kov_paths = [
            kov_marker / f"act_{i}_peep.json" for i in range(11001)
        ]

        canned_doc = {
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_NoMatch_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tallinn"}},
                {"@id": "estleg:Reg_NoMatch_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Käesolev määrus ei viita ühelegi institutsioonile."},
            ],
        }

        def fake_iter(*args, **kwargs):
            return synthetic_kov_paths

        def fake_load(filepath):
            import copy
            return copy.deepcopy(canned_doc)

        def fake_save(filepath, doc):
            return None

        monkeypatch.setattr(mod, "iter_peep_files", fake_iter)
        monkeypatch.setattr(mod, "load_json", fake_load)
        monkeypatch.setattr(mod, "save_json", fake_save)
        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)

        rc = mod.main()
        assert rc == 1, f"gate must fail when KOV ≥11k but no output; got rc={rc}"

    def test_unresolved_count_includes_state_side_kov_body_words(
        self, issuers_registry_file, monkeypatch
    ):
        """State law with KOV body word counts toward
        unresolved_references."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = issuers_registry_file
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        institutions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        # State law with body word
        (krr / "kov_seadus_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:KOKS_Map",
                 "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"]},
                {"@id": "estleg:KOKS_Par_22",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 22",
                 "estleg:summary": "Linnavolikogu kehtestab korra."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        cov_path = reports_dir / "extract_institutional_competence_coverage.json"
        with open(cov_path, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        assert cov["unresolved_references"] >= 1

    def test_fallback_hits_counts_path3_resolutions(
        self, tmp_path, monkeypatch
    ):
        """KOV act whose enactedBy is unknown to the registry; act's
        enactedByMunicipality has exactly one suffix-compatible
        Issuer match. Resolver uses Path 3 → fallback_hits >= 1."""
        import extract_institutional_competence as mod
        import estleg_common
        krr = tmp_path / "krr_outputs"
        institutions_dir = krr / "institutions"
        reports_dir = krr / "reports" / "kov"
        institutions_dir.mkdir(parents=True)
        reports_dir.mkdir(parents=True)

        # Registry with one matching issuer
        (krr / "issuers_kov_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@graph": [
                {"@id": "estleg:Issuer_tartu_linnavolikogu",
                 "@type": ["owl:NamedIndividual", "estleg:Issuer"],
                 "rdfs:label": "Tartu Linnavolikogu",
                 "estleg:bodyType": "volikogu",
                 "estleg:currentMunicipality": {
                     "@id": "estleg:Municipality_tartu"}},
            ],
        }), encoding="utf-8")

        # KOV act referencing an unknown enactedBy issuer
        kov = krr / "regulations" / "kov" / "tartu"
        kov.mkdir(parents=True)
        (kov / "act_peep.json").write_text(json.dumps({
            "@context": {
                "estleg": "https://data.riik.ee/ontology/estleg#",
                "owl": "http://www.w3.org/2002/07/owl#",
            },
            "@graph": [
                {"@id": "estleg:Reg_Tartu_X_Map_2024",
                 "@type": ["owl:Ontology", "estleg:Act",
                           "estleg:MunicipalRegulation"],
                 "estleg:enactedBy": {"@id": "estleg:Issuer_unknown"},
                 "estleg:enactedByMunicipality": {
                     "@id": "estleg:Municipality_tartu"}},
                {"@id": "estleg:Reg_Tartu_X_Par_1",
                 "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
                 "estleg:paragrahv": "§ 1",
                 "estleg:summary": "Linnavolikogu kehtestab maksu."},
            ],
        }), encoding="utf-8")

        monkeypatch.setattr(mod, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "INSTIT_DIR", institutions_dir)
        monkeypatch.setattr(estleg_common, "KRR_DIR", krr)
        monkeypatch.setattr(mod, "iter_peep_files", _iter_kov_inclusive)

        rc = mod.main()
        assert rc in (0, None)

        cov_path = reports_dir / "extract_institutional_competence_coverage.json"
        with open(cov_path, "r", encoding="utf-8") as fh:
            cov = json.load(fh)
        assert cov["fallback_hits"] >= 1
```

- [ ] **Step 2: Run the new tests (expect failures)**

Run: `pytest tests/test_extract_institutional_competence.py::TestCompetenceCoverageReport -v`
Expected: 4 failures.

- [ ] **Step 3: Add coverage instrumentation to `scripts/extract_institutional_competence.py`**

(a) Add imports near the top (with other imports):

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

(b) Change `main()` signature: `def main() -> int:` (was `-> None`).

(c) At the top of `main()`, near the other counters, initialize accumulators:

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
```

(d) Replace the simple `act_node = _find_act_node(doc); if act_node is None: continue` from Task 2 with the aggregate-vs-malformed distinction:

```python
        act_node = _find_act_node(doc)
        if act_node is None:
            # Aggregate-registry peeps (issuers_kov_peep.json,
            # municipalities_peep.json, etc.) have no provisions —
            # silently skip (no counter, no failure log).
            # Malformed act peeps DO have provisions but no
            # estleg:Act + owl:Ontology root — that's a Layer 1
            # data bug worth surfacing.
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
            continue
```

(e) Right after `enforcement_level`-style classification (in this case, after `source_municipality` / `source_issuer` extraction), add file-level bookkeeping:

```python
        is_kov = "regulations/kov/" in str(filepath)
        _files_processed.add(filepath)
        if is_kov:
            _files_processed_kov.add(filepath)
```

(f) After the per-provision loop (where `modified` would be set to True on output), update the with-output set + triples counter:

```python
        if modified:
            _files_with_output.add(filepath)
            if is_kov:
                _files_with_output_kov.add(filepath)
            # Count emitted authority refs as triples
            n_refs = sum(
                len(node.get("estleg:competentAuthority", []))
                if isinstance(node.get("estleg:competentAuthority"), list)
                else (1 if "estleg:competentAuthority" in node else 0)
                for node in doc.get("@graph", [])
            )
            _triples += n_refs
            if is_kov:
                _triples_kov += n_refs
```

(g) At the end of `main()` (after the report-write block, before any `return`), add the coverage report and gate check:

```python
    # ----- coverage report -----
    all_input_files = list(iter_peep_files())
    _kov_files = [p for p in all_input_files
                  if "regulations/kov/" in str(p)]

    _wall, _rate, _peak_mb = measure_runtime(_start, len(_files_processed))
    out_path = (KRR_DIR / "reports" / "kov"
                / "extract_institutional_competence_coverage.json")
    write_coverage_report(
        CoverageReport(
            pipeline="extract_institutional_competence",
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
            unresolved_references=_unresolved_count,
            fallback_hits=_fallback_hits,
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
    print(f"  triples_emitted: {_triples} (KOV: {_triples_kov})")
    print(f"  unresolved_references: {_unresolved_count}; fallback_hits: {_fallback_hits}")

    if len(_kov_files) >= 11000 and len(_files_with_output_kov) == 0:
        print("\nGATE FAIL: KOV files were processed but none produced output.")
        return 1
    if _triples_kov == 0 and len(_kov_files) >= 11000:
        print("\nGATE FAIL: zero KOV triples emitted.")
        return 1
    print("\nGATE OK")
    return 0
```

(h) At the very bottom of the file, change:

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

Run: `pytest tests/test_extract_institutional_competence.py::TestCompetenceCoverageReport -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: 235 passed (231 + 4).

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_institutional_competence.py tests/test_extract_institutional_competence.py
git commit -m "Add coverage instrumentation to extract_institutional_competence

Reuses kov_pipeline_coverage.CoverageReport (same idiom as Layer 2b
and Layer 2c PR #1). main() now returns int; raise SystemExit(main())
propagates the gate's exit code.

set[Path] accumulators for files_processed/files_with_output;
len() feeds the helper's int fields. Aggregate-registry peeps
(issuers_kov_peep.json etc.) are silently filtered (no provisions);
malformed act peeps (provisions present but Act+Ontology root
missing) are flagged via skip_reasons['missing_act_node'].

unresolved_references counts body-word abstains across all six
abstain reasons. fallback_hits counts SUCCESSFUL Path 3
resolutions — diagnostic for surfacing Layer 1 mapping
inconsistencies.

triples_emitted counts COMPETENT-AUTHORITY REFERENCES
(elements of competentAuthority lists across all provisions);
matches the 'how many provision-to-authority bindings did we
produce?' analytical question.

Gate check: fails when KOV input ≥11,000 files but produces
zero KOV output, or when ≥11k input but zero KOV triples
emitted."
```

---

## Task 8: Run the full corpus + add corpus-invariant test

**Files:**
- Modify: many files under `krr_outputs/` (peep updates + institutions/ regenerations + new coverage report).
- Modify: `tests/test_extract_institutional_competence.py` (add `TestCorpusInvariant`).

- [ ] **Step 1: Pre-flight — confirm the symlink + baseline**

```bash
ls -la data/riigiteataja | head -3
```

If not a symlink to the main checkout's `data/riigiteataja`, re-run the guarded restore from the worktree-setup section.

```bash
pytest -q
```

Expected: 235 passed.

- [ ] **Step 2: Run the script**

```bash
PYTHONUNBUFFERED=1 python3 scripts/extract_institutional_competence.py 2>&1 | tee /tmp/competence_run.log
```

Expected end of output:

```
Coverage report: <abspath>/krr_outputs/reports/kov/extract_institutional_competence_coverage.json
  input_files_total: 15508 (KOV: 11059)
  files_with_output: <some_int> (KOV: ~8000)
  triples_emitted: <some_int> (KOV: ~34000)
  unresolved_references: <some_int>; fallback_hits: <some_int>

GATE OK
```

The exact KOV output should be near the dry-count estimates (~8,000 KOV files, ~34,615 KOV body-word matches — though the resolver's abstain rules will reduce the bound triples below 34,615; expect 20,000-30,000 KOV triples). If output is zero or wildly off, STOP and investigate.

- [ ] **Step 3: Inspect the coverage report**

```bash
cat krr_outputs/reports/kov/extract_institutional_competence_coverage.json
```

Memorise the exact `files_with_output_kov`, `triples_emitted_kov`, `unresolved_references`, `fallback_hits`, `files_skipped` values — the docs and PR body reference them.

- [ ] **Step 4: Append `TestCorpusInvariant` to the test file**

```python
class TestCorpusInvariant:
    """Corpus-wide invariant: every KOV provision carrying
    competentAuthority targeting a body word (volikogu/valitsus
    body type) MUST resolve to an Issuer_* IRI, never an
    Institution_<body_slug> fallback."""

    @pytest.mark.slow
    def test_kov_competentauthority_is_issuer_iri(self):
        from estleg_common import iter_peep_files, KRR_DIR
        if not KRR_DIR.exists() or not list(KRR_DIR.glob("*_peep.json")):
            pytest.skip("krr_outputs/ empty — clean checkout")

        body_slug_institutions = {
            "Institution_linnavolikogu", "Institution_vallavolikogu",
            "Institution_alevivolikogu", "Institution_linnavalitsus",
            "Institution_vallavalitsus",
        }

        violations = []
        for peep in iter_peep_files():
            if "regulations/kov/" not in str(peep):
                continue
            try:
                with open(peep, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            for node in doc.get("@graph", []):
                refs = node.get("estleg:competentAuthority")
                if not refs:
                    continue
                if isinstance(refs, dict):
                    refs = [refs]
                for ref in refs:
                    iri = ref.get("@id") if isinstance(ref, dict) else None
                    if iri is None:
                        continue
                    suffix = iri.removeprefix("estleg:")
                    if suffix in body_slug_institutions:
                        violations.append(
                            f"{peep.name}: {node.get('@id')} has body-slug "
                            f"Institution_* fallback: {iri}"
                        )
        assert not violations, (
            f"Corpus invariant violated by {len(violations)} provisions:\n  "
            + "\n  ".join(violations[:20])
            + (f"\n  ... and {len(violations) - 20} more"
               if len(violations) > 20 else "")
        )
```

- [ ] **Step 5: Run the corpus-invariant test against the freshly-generated output**

Run: `pytest tests/test_extract_institutional_competence.py::TestCorpusInvariant -v`
Expected: 1 passed.

If it fails, the resolver wiring has a bug — STOP, investigate, and fix BEFORE staging the corpus output.

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: 236 passed (235 + 1).

- [ ] **Step 7: Stage the corpus diff in chunks**

```bash
python3 - <<'PY'
import subprocess
status_lines = subprocess.check_output(
    ["git", "status", "--porcelain"], text=True
).splitlines()
to_stage: list[str] = []
for line in status_lines:
    if not line:
        continue
    status = line[:2]
    path = line[3:]
    if not path.startswith("krr_outputs/"):
        continue
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
git add tests/test_extract_institutional_competence.py
```

- [ ] **Step 9: Verify staging is clean**

```bash
git status --short | grep -v "^M  krr_outputs\|^A  krr_outputs\|^D  krr_outputs\|^M  tests" | head -10
```

Expected: only the worktree-symlink artifact (`D data/riigiteataja/karistusseadustik.xml` and untracked `?? data/riigiteataja`). Anything else = scope creep.

The `^D krr_outputs` exemption is intentional: Task 4's stale-deletion logic produces deletions of orphaned `institutions/<slug>.json` files.

- [ ] **Step 10: Commit**

Use this template, but substitute the EXACT real numbers from the coverage report:

```
Run extract_institutional_competence on full corpus (Layer 2c PR #2)

First corpus run with KOV included. Real numbers:
- input_files_total: 15,508 (KOV: 11,059)
- files_processed: <X> (<Y> aggregate registry peeps silently skipped)
- files_with_output: <Z> (KOV: <KOV_Z>)
- triples_emitted (competentAuthority refs): <T> (KOV: <KOV_T>)
- unresolved_references: <U> (body-word abstains across all reasons)
- fallback_hits: <F> (Path 3 successful resolutions)
- files_skipped: <S>
- error_count: <E>
- wall_time: <W>s at <R> items/s, <M> MB peak
- GATE OK

Adds corpus-invariant test (TestCorpusInvariant, marked
@pytest.mark.slow): every KOV provision carrying competentAuthority
that names a body slug MUST point at an Issuer_* IRI, never an
Institution_<body_slug> fallback. Catches regressions where the
resolver silently fails and the original Institution_* path
sneaks back in.

Stages: ~<N> peep modifications + institutions/institution_*.json
regenerations (some new, some deleted via Task 4's stale-pruning) +
the new coverage report at krr_outputs/reports/kov/
extract_institutional_competence_coverage.json + the existing
report file with KOV statistics folded in.
```

---

## Task 9: Documentation updates

**Files:**
- Modify: `docs/SCHEMA_REFERENCE.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update `docs/SCHEMA_REFERENCE.md`**

(a) Find the Layer 2 schema table. The row for `estleg:competentAuthority` (if present — it may be in the Layer 1 section instead). Update Status to `populated` and reference the new Issuer-binding behavior.

(b) Find the Institutional Competence section (locate via `grep -n "Institutional Competence\|institutional competence" docs/SCHEMA_REFERENCE.md`). Add a new subsection at the end:

```markdown
### Layer 2c PR #2 — KOV Issuer-binding for `competentAuthority`

KOV regulations frequently delegate competence via generic body
words: "linnavolikogu kehtestab korra", "vallavalitsus väljastab
loa". Layer 2c PR #2 detects these body words and binds them to
the existing Layer 1 Issuer registry when the source act is a
`MunicipalRegulation` with `enactedByMunicipality` scope.

Resolution strategy (3 priority paths):

1. **Path 1 — same body type + suffix match.** When the body
   word's body type AND slug suffix match the source act's
   `enactedBy` issuer, the `competentAuthority` triple targets
   the source issuer directly. Example: `Issuer_abja_vallavolikogu`
   act + `vallavolikogu` body word → `Issuer_abja_vallavolikogu`.
2. **Path 2 — paired body in the same place family.** Body type
   differs from source issuer's; derive paired slug by swapping
   the body suffix. Family-prefix compatibility required
   (`linna*` and `valla*` cannot pair). Example:
   `Issuer_abja_vallavolikogu` act + `vallavalitsus` body word
   → `Issuer_abja_vallavalitsus` (if registered).
3. **Path 3 — municipality-wide unique match.** Reached only
   when source_issuer is missing, unknown to the registry, or
   has municipality inconsistent with the act's stated
   `enactedByMunicipality`. Best-effort rescue with the act's
   stated municipality + body type + suffix compatibility.
   Successful Path 3 resolutions are counted in
   `fallback_hits` as a diagnostic signal for Layer 1 follow-up.

Abstain conditions (the resolver returns `None`):

- Source act is a Law / state regulation (no `enactedByMunicipality`).
- Path 1 suffix mismatch
  (e.g. `Issuer_abja_vallavolikogu` + `linnavolikogu`).
- Path 2 paired body absent in registry OR in different
  municipality.
- Path 2 cross-family swap rejected
  (e.g. `Issuer_elva_linnavalitsus` + `vallavolikogu`).
- Path 3 finds zero or more than one suffix-compatible match.

Example KOV-bound provision:

```json
{
  "@id": "estleg:Reg_TLN_15_Map_2020_Par_1",
  "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
  "estleg:paragrahv": "§ 1",
  "estleg:summary": "Linnavolikogu kehtestab maksu määra.",
  "estleg:competentAuthority": [
    {"@id": "estleg:Issuer_tallinna_linnavolikogu"}
  ],
  "estleg:competenceType": "regulation"
}
```

The Issuer entity is the canonical anchor for "this municipal
body" across the graph. Layer 1's `enactedBy` and Layer 2c PR #2's
`competentAuthority` BOTH target IRIs from the same registry.
On Path 1 the two IRIs coincide (the act's enactedBy IS the
competent authority). On Path 2 they intentionally differ but
stay within the same place's issuer family.

SHACL: the existing `competentAuthority` constraint
(`sh:nodeKind sh:IRI`) accepts both `Institution_*` and `Issuer_*`
IRI forms. The constraint description is updated in this PR to
mention the broader value range; no new constraint block is
added.
```

- [ ] **Step 2: Update `CHANGELOG.md`**

Find the `## [Unreleased]` section. Add a new subsection BEFORE the existing Layer 2c PR #1 entries (newest first):

```markdown
### Added (Layer 2c PR #2 — Institutional Competence)
- `extract_institutional_competence.py` runs the full corpus
  including KOV by default (single `include_kov=False` pin
  removed).
- `GENERIC_PATTERNS` gains two new regex entries for KOV body
  words: `linnavolikogu` / `vallavolikogu` / `alevivolikogu`
  (volikogu bodies) and `linnavalitsus` / `vallavalitsus`
  (executive bodies), in all common Estonian case suffixes
  including the short illative (`vallavalitsusse`).
- KOV-scoped `competentAuthority` Issuer-binding via a
  3-priority resolver: Path 1 (same-body source_issuer),
  Path 2 (paired-body slug swap with municipal-family
  compatibility), Path 3 (municipality-wide unique fallback
  for inconsistent Layer 1 mappings).
- `_canonical_body_slug` strips Estonian case inflections;
  `_swap_issuer_body_suffix` derives paired-body Issuer IRIs;
  `_resolve_kov_authority` orchestrates the 3-priority
  resolution; `_is_path3_case` predicate counts Path 3
  diagnostic firings.
- The Issuer registry stays canonical: KOV-bound
  `competentAuthority` IRIs come from the same registry Layer 1
  emits as `enactedBy` targets. NO parallel
  `institutions/institution_<body_slug>.json` files are written
  for KOV-bound resolutions.

### Changed (Layer 2c PR #2)
- `extract_institutional_competence.main()` returns `int`;
  `raise SystemExit(main())` propagates the gate's exit code.
- Per-file in-memory processing replaces the inline global
  pre-clear pass. The per-file flow detects pre-existing
  peep-side output BEFORE clearing and saves the file when
  EITHER fresh sanction output exists OR pre-existing output
  existed (so cleared-but-empty files persist the cleared
  state to disk).
- Stale `krr_outputs/institutions/institution_<slug>.json`
  files are DELETED at end-of-run when no provisions cite the
  slug AND no per-peep load errors occurred. Without this,
  orphan `Institution_*` nodes survive pointing at provisions
  that no longer carry `competentAuthority`.
- Coverage instrumentation reuses
  `kov_pipeline_coverage.CoverageReport` (same schema as Layer 2a
  / Layer 2b / Layer 2c PR #1).
- SHACL: `LegalProvisionShape`'s existing `competentAuthority`
  constraint (`sh:nodeKind sh:IRI`) is unchanged structurally;
  only the `sh:description` is updated to mention the broader
  Issuer/Institution value range.

### Coverage (Layer 2c PR #2, 2026-05-04 corpus run)
- `extract_institutional_competence`: 15,508 input files
  (11,059 KOV); ~8,000 KOV files with output (~72% of KOV acts);
  ~25,000-30,000 KOV competentAuthority refs (estimate;
  exact in coverage report); GATE OK.

### Internal (Layer 2c PR #2)
- New `tests/test_extract_institutional_competence.py` with
  30 tests across 7 classes (3 detection + 13 resolver +
  3 integration + 3 idempotency + 3 institution-file
  deletion + 4 coverage + 1 corpus-wide invariant marked
  `@pytest.mark.slow`).
- `_unresolved_count` and `_fallback_hits` counters track
  body-word abstains and successful Path 3 resolutions
  respectively. `fallback_hits` is a diagnostic signal: high
  values surface Layer 1 mapping inconsistencies for
  follow-up investigation.
- 77 of 158 `(currentMunicipality, bodyType)` registry buckets
  are ambiguous (276 of 357 issuers — 77%, mostly post-2017
  haldusreform mergers). The source-issuer-based Path 1+2
  authoritative rule resolves these correctly using the act's
  own enactedBy identity rather than collapsing to a
  same-modern-municipality conflated issuer.

### Gate B umbrella status (after this PR)
- 2a COMPLETE (#98)
- 2b COMPLETE (#99)
- 2c PR #1 sanctions: COMPLETE (#100)
- 2c PR #2 institutional-competence: this PR
- 2c PR #3 court-provision-links: pending

Gate B closes when Layer 2c PR #3 lands.
```

(Update the Coverage block with the EXACT numbers from Task 8 before committing.)

- [ ] **Step 3: Verify**

Run: `pytest -q`
Expected: 236 passed (no test count change — markdown only).

- [ ] **Step 4: Commit**

```bash
git add docs/SCHEMA_REFERENCE.md CHANGELOG.md
git commit -m "Document Layer 2c PR #2 (institutional competence) deliverables

SCHEMA_REFERENCE.md: adds a new Layer 2c PR #2 subsection in the
Institutional Competence section with the 3-priority resolver
strategy, abstain conditions, a worked KOV example showing an
Issuer_* binding, and the SHACL description-update note.

CHANGELOG.md: Layer 2c PR #2 entry under [Unreleased] with
Added/Changed/Coverage/Internal/Gate-B-umbrella subsections.
Coverage numbers reference the actual corpus run from Task 8."
```

(Update the Coverage line in the CHANGELOG with the exact numbers from Task 8's coverage report before committing.)

---

## Task 10: Final verification + PR body

**Files:** None (verification + PR body file only).

- [ ] **Step 1: Final test run**

```bash
pytest -q
```

Expected: 236 passed.

- [ ] **Step 2: Run validate_all**

```bash
python3 scripts/validate_all.py 2>&1 | tail -10
```

Expected: `VALIDATION PASSED` with 0 errors / 0 warnings.

- [ ] **Step 3: Targeted SHACL on `competentAuthority`**

```bash
python3 - <<'PY'
from rdflib import Graph, URIRef
from rdflib.namespace import SH
from pyshacl import validate
import json
from pathlib import Path

ESTLEG_NS = "https://data.riik.ee/ontology/estleg#"
COMPETENT_AUTHORITY = URIRef(ESTLEG_NS + "competentAuthority")

# Sample: KOV peeps + state-law peeps that carry competentAuthority
data = Graph()
sample_count = 0
for peep in Path("krr_outputs").glob("*_peep.json"):
    with open(peep, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if any("estleg:competentAuthority" in n for n in doc.get("@graph", [])):
        data.parse(data=json.dumps(doc), format="json-ld")
        sample_count += 1
    if sample_count >= 50:
        break
for peep in Path("krr_outputs/regulations/kov").rglob("*_peep.json"):
    with open(peep, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if any("estleg:competentAuthority" in n for n in doc.get("@graph", [])):
        data.parse(data=json.dumps(doc), format="json-ld")
        sample_count += 1
    if sample_count >= 100:
        break
print(f"Sampled {sample_count} files with competentAuthority")

shapes = Graph()
shapes.parse("shacl/estonian_legal_shapes.ttl", format="turtle")
conforms, results_graph, _ = validate(
    data, shacl_graph=shapes, inference="rdfs"
)
new_violations = list(
    results_graph.subjects(SH.resultPath, COMPETENT_AUTHORITY)
)
print(f"competentAuthority violations on sample: {len(new_violations)}")
if new_violations:
    print("First 5 violation focus nodes:")
    for v in new_violations[:5]:
        focus = list(results_graph.objects(v, SH.focusNode))
        value = list(results_graph.objects(v, SH.value))
        print(f"  focus={focus} value={value}")
assert len(new_violations) == 0, (
    f"Found {len(new_violations)} SHACL violations on competentAuthority."
)
print("Targeted SHACL: 0 violations on competentAuthority — OK")
PY
```

Expected: `Targeted SHACL: 0 violations on competentAuthority — OK`.

- [ ] **Step 4: Coverage gate one-liner**

```bash
python3 - <<'PY'
import json
p = "krr_outputs/reports/kov/extract_institutional_competence_coverage.json"
with open(p) as fh:
    r = json.load(fh)
assert r["files_with_output_kov"] > 0, "zero KOV output files"
assert r["triples_emitted_kov"] > 0, "zero KOV competentAuthority refs"
print(f"extract_institutional_competence: GATE OK — "
      f"{r['files_with_output_kov']} KOV files, "
      f"{r['triples_emitted_kov']} KOV refs, "
      f"unresolved={r['unresolved_references']}, "
      f"fallback_hits={r['fallback_hits']}, "
      f"skipped={r['files_skipped']}")
PY
```

Expected: `extract_institutional_competence: GATE OK — <N> KOV files, <M> KOV refs, ...`.

- [ ] **Step 5: Generate the PR body at `/tmp/pr-body-2c-competence.md`**

(Substitute exact numbers from the coverage report.)

```markdown
## Summary

Layer 2c PR #2 of 3 — Institutional Competence. Unpins
`extract_institutional_competence` so it processes the full
corpus (laws + state regulations + KOV acts), adds KOV body-word
detection patterns the script previously lacked
(`linnavolikogu` / `vallavolikogu` / `alevivolikogu` /
`linnavalitsus` / `vallavalitsus`), and binds those matches to
the existing Layer 1 Issuer registry when the source act is a
`MunicipalRegulation` with `enactedByMunicipality` scope.

- **Detection:** two new `GENERIC_PATTERNS` entries cover all
  common Estonian case inflections, including the short
  illative `vallavalitsusse`. `_canonical_body_slug` strips
  suffixes and validates against the five reachable canonical
  slugs.
- **Resolution:** 3-priority strategy. Path 1 returns the
  source act's own `enactedBy` issuer when its body type AND
  slug suffix match the body word. Path 2 derives a paired-body
  Issuer slug for cross-body references (with mandatory
  `linna*`/`valla*`/`alevi*` family-prefix compatibility).
  Path 3 falls back to municipality-wide uniqueness ONLY when
  source_issuer is missing, unknown, or has Layer 1
  municipality inconsistency.
- **Identity:** the Issuer registry stays canonical. KOV-bound
  `competentAuthority` IRIs come from the same registry Layer 1
  emits as `enactedBy` targets. No parallel
  `institutions/institution_<body_slug>.json` files for
  KOV-bound resolutions.
- **Idempotency at two layers:** peep-side `competentAuthority`
  + `competenceType` cleared and recomputed every run; stale
  `institutions/institution_<slug>.json` files DELETED at
  end-of-run when no provisions cite the slug AND no per-peep
  errors occurred.

## Coverage (corpus run, 2026-05-04)

| Metric | Value |
|---|---|
| Input files (total / KOV) | 15,508 / 11,059 |
| Files with output (total / KOV) | <X> / <Y> |
| `competentAuthority` refs (total / KOV) | <T> / <KOV_T> |
| `unresolved_references` | <U> |
| `fallback_hits` (Path 3 successes) | <F> |
| Files skipped (`missing_act_node`) | <S> |
| Wall-time / throughput | <W>s / <R> items/s |

(Exact numbers in `krr_outputs/reports/kov/extract_institutional_competence_coverage.json`.)

## SHACL

Targeted SHACL on `competentAuthority`: zero violations on a
sample of 100 KOV + state-law peeps. The constraint
(`sh:nodeKind sh:IRI`) already accepted both `Institution_*` and
`Issuer_*` IRI forms; this PR only updates the `sh:description`
to document the broader range.

Pre-existing violations (`requestedCluster` IRI-as-literal,
missing `summary`, VOS_Par_* `rdfs:label`/`sectionNumber`)
predate this PR.

## Test plan

- [x] Full pytest suite passing (236 tests, +30 vs Layer 2c PR #1)
- [x] validate_all reports zero errors / warnings
- [x] Targeted SHACL on `competentAuthority`: zero violations
- [x] extract_institutional_competence_coverage.json gate OK
- [x] Corpus-wide invariant: every KOV body-word
      `competentAuthority` is an `Issuer_*` IRI, never an
      `Institution_<body_slug>` fallback
- [ ] Spot-check 3 KOV peeps for `Issuer_*` binding
- [ ] Spot-check 3 state-law peeps with `linnavolikogu` text
      for absence of body-word binding
- [ ] Spot-check `fallback_hits` value for plausibility
      (high values may indicate Layer 1 mapping issues
      worth a follow-up)

## Gate B umbrella status

- 2a COMPLETE (#98)
- 2b COMPLETE (#99)
- 2c PR #1 sanctions: COMPLETE (#100)
- 2c PR #2 institutional-competence: **this PR**
- 2c PR #3 court-provision-links: next

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

- [ ] **Step 6: Print summary**

```bash
echo ""
echo "========================================================================"
echo "Layer 2c PR #2 (Institutional Competence) execution COMPLETE"
echo "========================================================================"
echo ""
echo "Branch: feature/kov-layer2c-competence ($(git rev-parse --short HEAD))"
echo "Commits ahead of origin/main: $(git rev-list --count origin/main..HEAD 2>/dev/null || git rev-list --count main..HEAD)"
echo ""
echo "PR body ready at /tmp/pr-body-2c-competence.md"
echo ""
echo "When ready to push:"
echo "  git push -u origin feature/kov-layer2c-competence"
echo "  gh pr create --title 'KOV integration Layer 2c PR #2 — Institutional Competence' --body-file /tmp/pr-body-2c-competence.md"
echo ""
echo "DO NOT push without human approval."
```

- [ ] **Step 7: Stop short of pushing**

Per the plan convention: do NOT run `git push` or `gh pr create`. The push + PR creation is left for the human reviewer's go-ahead.

---

## Self-Review Checklist

After all 10 tasks complete:

- [ ] **Spec coverage.** Each spec invariant maps to a task:
  - Inv 1 (pin removal): Task 6
  - Inv 2 (new GENERIC_PATTERNS entries): Task 1
  - Inv 3 (KOV-scoped Issuer-binding when resolver returns one match): Tasks 1 + 2
  - Inv 4 (named-institution path unchanged): Task 2 + corpus-invariant test in Task 8
  - Inv 5 (no parallel Institution_* file for KOV-bound): Task 2 + Task 4
  - Inv 6 (peep + institutions-file idempotency): Tasks 3 + 4
  - Inv 7 (SHACL description update): Task 5
  - Inv 8 (`main() -> int` + `SystemExit`): Task 7
  - Inv 9 (coverage gate + unresolved + fallback_hits): Task 7

- [ ] **Test coverage matches the design.** 7 classes, 30 tests:
  - `TestKovBodyWordDetection` (3 unit, Task 1)
  - `TestResolveKovAuthority` (13 unit, Task 1)
  - `TestExtractCompetenceWithIssuerBinding` (3 integration, Task 2)
  - `TestCompetenceIdempotency` (3 idempotency, Task 3)
  - `TestStaleInstitutionFileDeletion` (3 idempotency, Task 4)
  - `TestCompetenceCoverageReport` (4 coverage, Task 7)
  - `TestCorpusInvariant` (1 corpus-wide, Task 8, `@pytest.mark.slow`)

- [ ] **No actionable gaps.** No "TODO", "TBD", "implement later", "code omitted for brevity" anywhere in the plan.

- [ ] **Type consistency.** `_resolve_kov_authority(body_slug, source_municipality, source_issuer, issuer_registry) -> str | None` and `_is_path3_case(source_issuer, source_municipality, issuer_registry) -> bool` consistent across Tasks 1, 2, 7. `main() -> int` consistent across Task 7 onwards.

- [ ] **Path 1+2 authoritative + abstain.** When source_issuer is known and consistent, Path 1+2 abstain (return None) does NOT fall through to Path 3. Locked by `test_path1_suffix_mismatch_abstains` and `test_path2_paired_body_missing_abstains_without_path3`.

- [ ] **Path 2 family-prefix compatibility.** `linna*`/`valla*`/`alevi*` mutually exclusive on swap. Locked by `test_path2_cross_family_*_abstains`.

- [ ] **All 10 tasks have clear commit messages.**
