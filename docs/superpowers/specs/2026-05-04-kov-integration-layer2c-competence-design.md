# KOV Integration Layer 2c — Institutional Competence (PR #2 of 3)

**Date:** 2026-05-04
**Status:** Design approved by user. Awaiting written-spec review.
**Parent spec:** `docs/superpowers/specs/2026-05-02-kov-integration-design.md` (Layer 2c — `extract_institutional_competence` section, lines 518-528).
**Predecessors:** Layer 2a (#98), Layer 2b (#99), Layer 2c PR #1 (#100), all merged.
**Sibling spec:** `docs/superpowers/specs/2026-05-04-kov-integration-layer2c-sanctions-design.md` (PR #1 design — same Layer 2c family).

## Goal

Unpin `extract_institutional_competence.py` so it processes the full
corpus (laws + state regulations + KOV acts) by default, ADD KOV
body-word detection patterns the script currently lacks
(`linnavolikogu` / `vallavolikogu` / `linnavalitsus` /
`vallavalitsus` / `alevivolikogu`), AND bind those matches to the
existing Layer 1 Issuer registry when the source act is a
`MunicipalRegulation` with `enactedByMunicipality`.

The Issuer entity becomes the canonical anchor for "this municipal
body" across the graph: Layer 1's `enactedBy` and Layer 2c PR #2's
`competentAuthority` both point at the same `estleg:Issuer_*` IRI
when the act is municipality-scoped.

## Crisp invariant

After the PR lands, all of the following hold:

1. `extract_institutional_competence.py` calls `iter_peep_files()`
   with no `include_kov=False` argument. The single
   `# DEFERRED to Layer 2c` pin (line 239) is removed.
2. `GENERIC_PATTERNS` gains TWO new regex entries that match
   `linnavolikogu` / `vallavolikogu` / `alevivolikogu` (volikogu
   bodies) and `linnavalitsus` / `vallavalitsus` (executive
   bodies), in all common Estonian case suffixes (nominative,
   genitive, partitive, allative, inessive, illative). The
   matched text is then NORMALISED to the canonical body slug
   (one of `linnavolikogu`, `vallavolikogu`, `alevivolikogu`,
   `linnavalitsus`, `vallavalitsus`) via a helper that strips
   case suffixes BEFORE the resolver receives the slug. The new
   entries are placed BEFORE the existing `\b(vald|linn)\b` so
   more-specific matches take precedence.
3. KOV-scoped Issuer-binding: when a generic body word matches AND
   the source act has `estleg:enactedByMunicipality` set, the
   `competentAuthority` triple targets the matching `estleg:Issuer_*`
   IRI from the existing Issuer registry. Lookup re-uses Layer 2b's
   `build_issuer_registry`.
4. Non-KOV abstain: when a generic body word matches in a Law / state
   regulation (no `enactedByMunicipality`), the script does NOT emit
   a `competentAuthority` triple for that body. The detection counts
   against `unresolved_references` in the coverage report.
5. The Issuer registry stays canonical: KOV-bound `competentAuthority`
   IRIs are the same IRIs Layer 1 emits as `enactedBy` targets. NO
   parallel `institutions/institution_<body_slug>.json` files are
   written for KOV-bound resolutions (e.g. when a Tallinn KOV act
   resolves "linnavolikogu" to `estleg:Issuer_tallinna_linnavolikogu`,
   no `institutions/institution_linnavolikogu.json` is created from
   the body slug — the only side effect is the `competentAuthority`
   triple pointing at the existing Issuer registry IRI).
6. Existing non-KOV institution detection (Riigikohus, ministries,
   `kohalik_omavalitsus`, etc.) is unchanged: same `Institution_*`
   IRIs and same per-institution provenance files emitted.
7. Re-runs are idempotent at TWO layers:
   - Provision-level: `competentAuthority` and `competenceType`
     cleared and recomputed every run; saves the file when EITHER
     fresh output exists OR pre-existing peep-side output existed.
   - Institutions-file-level: when a previously-cited institution
     loses ALL its provision references between runs, the
     corresponding `krr_outputs/institutions/institution_<slug>.json`
     file is **deleted**.
8. `LegalProvisionShape` in `shacl/estonian_legal_shapes.ttl`
   already has a `competentAuthority` constraint at lines 140-145
   (`sh:nodeKind sh:IRI` only). This PR REVIEWS the constraint —
   no new block. The existing description string ("Institution
   responsible for enforcing or implementing this provision") is
   updated to reflect that the value range now includes
   `Issuer_*` IRIs in addition to the original `Institution_*`
   form. A new test asserts EXACTLY one constraint exists for the
   path (regression lock against an accidental duplicate block).
9. Coverage report
   `krr_outputs/reports/kov/extract_institutional_competence_coverage.json`
   shows `files_with_output_kov > 0` and `triples_emitted_kov > 0`.
   `unresolved_references` reports the count of detected body words
   that abstained (state-side hits + ambiguous KOV registry).
10. `main()` returns `int`; `raise SystemExit(main())` propagates
    the gate's exit code.

**Real-world impact estimate** (read-only dry count): ~8,000 KOV
peeps (72% of 11,059) gain `competentAuthority` triples binding to
Issuer entities; ~34,615 individual body-word matches across KOV
provisions. Roughly 400× the volume of PR #1's sanctions output.

## Why this is the second of three Layer 2c PRs

The parent spec defers three pipelines to Layer 2c:
`extract_sanctions` (PR #1, merged), `extract_institutional_competence`
(this PR), and `extract_court_provision_links` (PR #3 — third).

This PR is structurally heavier than PR #1 because:

- Detection patterns must be ADDED (the existing `GENERIC_PATTERNS`
  doesn't match KOV body words at all). PR #1 was unpin-only on the
  detection side; PR #2 is unpin + new patterns + binding logic.
- Resolution requires the existing Layer 2b `build_issuer_registry`
  helper. PR #1 had no cross-layer dependency.
- Volume scales: ~34,615 body-word matches vs PR #1's ~85 sanction
  records. The Issuer registry lookup happens 34k+ times per
  corpus run.

This PR is structurally LIGHTER than PR #1 in one dimension:

- The competence-pattern detection itself is unchanged — PR #2 only
  adds NEW body-word patterns; the existing imprecisions in
  `competenceType` (provision-level, one literal per provision)
  remain. PR #1's idempotency and coverage scaffolding can be
  copied with minor adjustments.

## Architecture

### Detection — new GENERIC_PATTERNS entries

Two new regex entries, placed BEFORE the existing `\b(vald|linn)\b`
in `GENERIC_PATTERNS` so more-specific matches take precedence:

```python
# KOV body words (Layer 2c PR #2). Both regexes match across all
# common Estonian case suffixes:
#   nominative   linnavolikogu, vallavalitsus
#   genitive     linnavolikogu (same), vallavalitsuse (-e)
#   partitive    linnavolikogu (same), vallavalitsust (-t)
#   allative     linnavolikogule (-le), vallavalitsusele (-ele)
#   inessive     linnavolikogus (-s), vallavalitsuses (-ses)
#   illative     linnavolikogusse (-sse), vallavalitsusesse (-sse)
#
# The volikogu stem is invariant across cases, but the SUFFIX -le
# / -s / -sse can append; the regex permits all three.
# The valitsus stem mutates with -e in genitive, then composite
# suffixes -ele/-ses/-sse stack on top of the genitive form.
(re.compile(r"\b(?:linna|valla|alevi)volikogu(?:le|sse|s)?\b", re.IGNORECASE),
 "local_government_council", "local_government_body"),
(re.compile(r"\b(?:linna|valla)valitsus(?:e(?:le|ses|sse)?|t)?\b", re.IGNORECASE),
 "local_government_executive", "local_government_body"),
```

After the regex match, the captured text passes through a NEW
canonicalisation helper `_canonical_body_slug(matched_text)` that
strips case suffixes before the existing `sanitize_id` →
`normalize_iri_suffix` chain runs. This is what guarantees the
resolver receives one of the five canonical body slugs:

```python
# Body-slug canonicalisation: trim Estonian case suffixes from
# the matched text so the resolver's body_type_map lookup hits.
# Without this, "vallavalitsusele" becomes the slug
# "vallavalitsusele" (no map entry → resolver abstains).
_BODY_SLUG_CANONICAL = {
    "volikogu", "valitsus",
}

def _canonical_body_slug(matched: str) -> str | None:
    """Strip Estonian case suffixes from a KOV body-word match
    and return the canonical slug (linnavolikogu, vallavolikogu,
    alevivolikogu, linnavalitsus, vallavalitsus) or None if the
    match doesn't fit one of the known stems.
    """
    s = matched.lower()
    # Strip trailing case suffixes greedily from longest to shortest
    # so 'vallavalitsusele' → 'vallavalitsuse' → 'vallavalitsus'.
    for suffix in ("esse", "ele", "ses", "sse", "le", "s", "t", "e"):
        if s.endswith(suffix) and len(s) > len(suffix) + 4:
            stripped = s[: -len(suffix)]
            if stripped.endswith(("volikogu", "valitsus")):
                s = stripped
                break
    # Validate the canonical form
    for prefix in ("linna", "valla", "alevi"):
        for stem in ("volikogu", "valitsus"):
            if s == prefix + stem:
                return s
    return None
```

The slug returned by `_canonical_body_slug` is what the resolver
keys on. If `None`, the detection is skipped entirely (defensive
— keeps regex over-match noise out of the institutions output).

### Resolution — `_resolve_kov_authority`

New module-level helper, called from `main()` after detection but
before the fall-through `Institution_*` emission:

```python
def _resolve_kov_authority(
    body_slug: str,
    source_municipality: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
) -> str | None:
    """Resolve a KOV body-word detection (linnavolikogu, vallavolikogu,
    linnavalitsus, vallavalitsus, alevivolikogu) to the matching
    estleg:Issuer_* IRI for the source act's municipality.

    Args:
        body_slug: canonical body word from _canonical_body_slug.
            Expected: "linnavolikogu", "vallavolikogu",
            "linnavalitsus", "vallavalitsus", "alevivolikogu".
        source_municipality: the @id STRING of the source act's
            estleg:enactedByMunicipality. The caller MUST unwrap
            the JSON-LD object form (`{"@id": "estleg:Municipality_..."}`)
            before passing — see _id_ref helper below. None when
            the source is a Law or state regulation — the resolver
            returns None (abstain rule).
        issuer_registry: issuer @id → (label_normalized,
            municipality_iri, body_type) from Layer 2b's
            build_issuer_registry. `municipality_iri` is a plain
            STRING IRI (e.g. "estleg:Municipality_tallinn"), so
            the resolver compares strings, not dicts.

    Returns:
        The matching Issuer @id when EXACTLY one issuer in the
        source municipality has the matching body type. None when:
        - source_municipality is None (non-KOV act)
        - no issuer in the registry matches
        - more than one issuer in the same municipality has the
          same body type (rare; abstain rather than guess)
    """
    if source_municipality is None:
        return None

    # Map body_slug → bodyType in the registry's vocabulary.
    # Layer 1 stamps issuers with bodyType "volikogu" or "valitsus".
    body_type_map = {
        "linnavolikogu": "volikogu",
        "vallavolikogu": "volikogu",
        "alevivolikogu": "volikogu",
        "linnavalitsus": "valitsus",
        "vallavalitsus": "valitsus",
    }
    body_type = body_type_map.get(body_slug)
    if body_type is None:
        return None

    matches = [
        issuer_iri
        for issuer_iri, (_label, mun_iri, btype) in issuer_registry.items()
        if mun_iri == source_municipality and btype == body_type
    ]
    if len(matches) == 1:
        return matches[0]
    return None  # zero or ambiguous
```

The "exactly one match" rule is consistent with Layer 2b's KOV
body-text resolver in `extract_cross_references.py`. Ambiguity
abstains.

### Wiring into `main()`

The existing per-provision loop currently does:

```python
authority_refs = []
for canon_name, iri_suffix, itype in institutions:
    inst_iri = f"estleg:Institution_{iri_suffix}"
    if inst_iri not in inst_data:
        inst_data[inst_iri] = {...}
    inst_provisions[inst_iri].append((provision_iri, competence_type, law_name))
    authority_refs.append({"@id": inst_iri})

if authority_refs:
    node["estleg:competentAuthority"] = authority_refs
    node["estleg:competenceType"] = competence_type
```

The new shape:

```python
authority_refs = []
for canon_name, iri_suffix, itype in institutions:
    # Layer 2c PR #2: try Issuer-binding for KOV body-word
    # detections before falling back to Institution_*.
    if itype == "local_government_body":
        issuer_iri = _resolve_kov_authority(
            body_slug=iri_suffix,
            source_municipality=source_municipality,
            issuer_registry=issuer_registry,
        )
        if issuer_iri is not None:
            # Resolved — point at the canonical Issuer entity.
            # Do NOT add an Institution_<slug> entry to inst_data
            # (the Issuer registry already represents this body).
            authority_refs.append({"@id": issuer_iri})
            continue
        # Unresolved — abstain. Increment unresolved counter; do
        # NOT emit an Institution_linnavolikogu fallback.
        unresolved_count += 1
        continue
    # All other detections (named institutions, ministries,
    # ministries, courts, generic local_government, kohalik_omavalitsus)
    # follow the existing Institution_* path unchanged.
    inst_iri = f"estleg:Institution_{iri_suffix}"
    if inst_iri not in inst_data:
        inst_data[inst_iri] = {...}
    inst_provisions[inst_iri].append((provision_iri, competence_type, law_name))
    authority_refs.append({"@id": inst_iri})

if authority_refs:
    node["estleg:competentAuthority"] = authority_refs
    node["estleg:competenceType"] = competence_type
```

`source_municipality` is read once per peep file (immediately after
loading the doc) via the same `_find_act_node` helper added in
PR #1, then unwrapped to a plain string IRI via a new
`_id_ref(value)` helper:

```python
def _id_ref(value) -> str | None:
    """Unwrap a JSON-LD object form `{"@id": "..."}` to a plain
    string IRI. Returns the string as-is if already a string, or
    None when the value is missing or malformed.

    KOV peeps store estleg:enactedByMunicipality as
    `{"@id": "estleg:Municipality_..."}` (JSON-LD object form),
    while build_issuer_registry stores municipality_iri as a
    plain string. Without this unwrap, the resolver would compare
    a dict against a string and ALWAYS return None — silently
    breaking every KOV Issuer resolution.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("@id")
    return None
```

Per-peep usage in `main()`:

```python
act_node = _find_act_node(doc)
if act_node is None:
    # ... aggregate-vs-malformed handling per PR #1 ...
    continue
source_municipality = _id_ref(act_node.get("estleg:enactedByMunicipality"))
# source_municipality is now a STRING IRI or None.
```

`issuer_registry` is built once at startup using
`build_issuer_registry(KRR_DIR / "issuers_kov_peep.json")` from
Layer 2b. Its `municipality_iri` field is already a plain string
(see `extract_cross_references.py:_id_ref`-style normalisation in
the registry builder), so the resolver's `mun_iri == source_municipality`
comparison is string-to-string.

### Idempotency

Same shape as PR #1 — but covering BOTH the peep file mutations
AND the sibling `institutions/<slug>.json` file lifecycle.

**Peep-side:**
1. Load doc; find act node via `_find_act_node`.
2. Detect existing peep-side output BEFORE mutation
   (`had_existing_peep` = any provision carries `competentAuthority`
   OR `competenceType`).
3. Clear unconditionally: `pop("estleg:competentAuthority", None)`,
   `pop("estleg:competenceType", None)`.
4. Run detection + resolution + emission.
5. Save when EITHER fresh output exists OR `had_existing_peep` was
   true.

**Institutions-file-level:**

The script writes `krr_outputs/institutions/institution_<slug>.json`
per detected institution. This needs a different idempotency model
than PR #1's sanctions-file deletion: a single institution can be
referenced by hundreds of provisions across many laws, so the file
shouldn't be deleted just because ONE law stops citing it. The
file should be deleted when NO provisions in the entire corpus
cite that institution after the re-run.

The script already aggregates `inst_data` across the whole run
(builds the per-institution file at the end after walking every
peep). We can extend that flow:

1. At the start of `main()`, scan `krr_outputs/institutions/` for
   existing files; track them as `pre_existing_institution_files`.
2. After the per-peep loop completes, the writer block iterates
   `inst_data.items()` to emit per-institution files. Track which
   slugs got written this run as `written_slugs: set[str]` (just
   the slug — e.g. `"riigikohus"`, NOT `"institution_riigikohus"`).
3. At the end:
   ```python
   for path in pre_existing_institution_files:
       # path.stem is "institution_riigikohus"; the slug-only form
       # is the part after the "institution_" prefix.
       slug = path.stem.removeprefix("institution_")
       if slug not in written_slugs:
           try:
               path.unlink()
           except OSError:
               pass  # tolerate concurrent removal
   ```

   The `removeprefix` is critical: comparing `path.stem` directly
   against `written_slugs` would compare `"institution_riigikohus"`
   against `"riigikohus"` and ALWAYS report not-found, deleting
   every legitimate file. Test
   `TestStaleInstitutionFileDeletion::test_living_institution_files_preserved`
   locks this in.

The resolver-bound Issuer IRIs do NOT add to `inst_data` (per
"identity stays canonical" decision), so they don't trigger
spurious institution-file writes.

### Coverage instrumentation

Reuses `kov_pipeline_coverage.CoverageReport` — same idiom as
Layer 2b and PR #1. `set[Path]` accumulators, `len()` into int
fields. Gate check: `len(_kov_files) >= 11000 and
len(_files_with_output_kov) == 0` → return 1.

`triples_emitted` definition for this pipeline: counts
`competentAuthority` REFERENCES (the elements of the
`competentAuthority` list across all provisions). One provision
delegating to two bodies counts as 2. This matches the analytical
question "how many provision-to-authority bindings did we
produce?" and is the cardinality the inverse pass cares about.

`unresolved_references` reports body-word detections that
abstained (source non-KOV OR registry ambiguous OR no matching
issuer). Useful for sniffing under-extraction.

### SHACL

The `competentAuthority` property already has a constraint in
`shacl/estonian_legal_shapes.ttl` (lines 140-145):

```turtle
sh:property [
    sh:path estleg:competentAuthority ;
    sh:nodeKind sh:IRI ;
    sh:name "competentAuthority" ;
    sh:description "Institution responsible for enforcing or implementing this provision" ;
] ;
```

The existing `sh:nodeKind sh:IRI` constraint is exactly what this
PR needs — both `Institution_*` and `Issuer_*` IRI values pass.
No class constraint is desired (Estonian-language judicial
structure is still evolving; e.g. `estleg:Court` subclass paths
may land in a future PR; locking the value to one of two specific
classes today would create churn).

This PR's only TTL change is to UPDATE the description string to
reflect the broader value range:

```turtle
    sh:description "Institution or Issuer responsible for enforcing or implementing this provision. May be an estleg:Institution_* IRI (named institutions, ministries, courts, generic local-government references) or an estleg:Issuer_* IRI (when the source act is a MunicipalRegulation with enactedByMunicipality scope and the body word resolves to a registered Issuer)." ;
```

No new constraint block. The `TestExtractCompetenceWithIssuerBinding`
suite includes a regression assertion that exactly ONE
`competentAuthority` constraint exists in the SHACL graph (catches
accidental duplicate blocks).

## Components

### Modified files

| Path | Change |
| --- | --- |
| `scripts/extract_institutional_competence.py` | Remove `include_kov=False` pin (line 239); add 2 new `GENERIC_PATTERNS` entries for KOV body words (placed before `vald|linn`); add `_find_act_node` helper (or import from `extract_sanctions` — verify during implementation); add `_resolve_kov_authority` resolver; add `issuer_registry` build at startup (re-uses Layer 2b's `build_issuer_registry`); replace global `_clear_existing_institutions` with per-file in-memory processing; wire body-word matches through resolver before falling back to `Institution_*`; skip `inst_data` entry when resolver succeeds; track `pre_existing_institution_files` for end-of-run deletion of orphaned files; add coverage instrumentation reusing `kov_pipeline_coverage.CoverageReport`; change `main()` to return `int`; use `raise SystemExit(main())`. |
| `tests/test_extract_institutional_competence.py` | **New file.** 17 tests across 7 classes (3 detection unit + 4 resolver unit + 3 integration + 3 idempotency + 1 institutions-file deletion + 2 coverage + 1 corpus-wide invariant). |
| `shacl/estonian_legal_shapes.ttl` | UPDATE the existing `competentAuthority` constraint's `sh:description` to mention the broader Issuer/Institution range (the constraint itself — `sh:nodeKind sh:IRI` — already exists at lines 140-145; do NOT add a duplicate). |
| `docs/SCHEMA_REFERENCE.md` | Mark `competentAuthority` populated in the Layer 2 schema table; add a new subsection in the existing Institutional Competence section with KOV Issuer-binding semantics, a worked KOV example, and the SHACL constraint summary. |
| `CHANGELOG.md` | Layer 2c PR #2 entry under `## [Unreleased]` with Added/Changed/Coverage/Internal subsections. |

### NOT in this PR

- `extract_court_provision_links` — Layer 2c PR #3.
- Per-authority `competenceType` reification (would require a
  `Competence` reified node — Layer 3-class work).
- Cleanup of the legacy `\b(vald|linn)\b` pattern (separate PR;
  changes the existing institutions output across many state-law
  peeps).
- New SHACL **shapes** — only adds a property constraint to the
  existing `LegalProvisionShape`.

## Test plan

**7 new test classes, 17 tests total.** Files: `tests/test_extract_institutional_competence.py`.

Class breakdown (3 + 4 + 3 + 3 + 1 + 2 + 1 = 17):
- `TestKovBodyWordDetection` — 3 unit tests
- `TestResolveKovAuthority` — 4 unit tests
- `TestExtractCompetenceWithIssuerBinding` — 3 integration tests
- `TestCompetenceIdempotency` — 3 integration tests
- `TestStaleInstitutionFileDeletion` — 1 integration test
- `TestCompetenceCoverageReport` — 2 integration tests
- `TestCorpusInvariant` — 1 corpus-wide test (`@pytest.mark.slow`)

### `TestKovBodyWordDetection` (unit, 3 tests)

- `test_linnavolikogu_matches_basic_form`
- `test_vallavalitsus_inflections` (parametrized over 5 Estonian
  case forms)
- `test_named_institutions_still_win` (regression — ensure new
  patterns don't override existing named-institution detections)

### `TestResolveKovAuthority` (unit, 4 tests)

- `test_kov_scoped_body_resolves_to_issuer`
- `test_no_municipality_returns_none`
- `test_municipality_with_no_matching_issuer_returns_none`
- `test_ambiguous_returns_none` (two volikogu issuers in same
  municipality)

### `TestExtractCompetenceWithIssuerBinding` (integration, 3 tests)

- `test_kov_act_competence_binds_to_issuer` (provision text
  `"linnavolikogu kehtestab korra"` in a Tallinn KOV act →
  `competentAuthority` contains
  `estleg:Issuer_tallinna_linnavolikogu`; provision's
  `competentAuthority` list does NOT contain
  `estleg:Institution_linnavolikogu` (the body-slug fallback that
  would land if the resolver silently failed); the
  `krr_outputs/institutions/` directory does NOT contain
  `institution_linnavolikogu.json` — the body-slug name, not the
  issuer-slug name. This is what catches the regression where
  the resolver falls through and the original `Institution_*`
  path takes over.)
- `test_state_law_competence_with_kov_body_word_abstains` (Law-
  typed peep with same text → no `competentAuthority` for
  linnavolikogu; counts as unresolved)
- `test_kov_act_with_named_authority_still_emits_institution`
  ("Riigikohus otsustab" in a KOV peep → `competentAuthority`
  contains `Institution_riigikohus` AND a sibling
  `institutions/institution_riigikohus.json` is created — named
  path unchanged)

### `TestCompetenceIdempotency` (integration, 3 tests)

- `test_stale_competentauthority_cleared_when_text_changes`
- `test_classification_recomputed_from_act_type` (peep's
  `enactedByMunicipality` flips between runs; resolution
  recomputed from current value)
- `test_no_op_when_no_existing_no_fresh` (mtime preserved when
  nothing detected and nothing prior)

### `TestStaleInstitutionFileDeletion` (integration, 1 test)

- `test_stale_institutions_json_deleted_when_no_provisions_cite_it`
  (fixture stages a stale `institutions/institution_xyz.json`; no
  provisions in the run cite Institution_xyz; rerun deletes the
  file)

### `TestCompetenceCoverageReport` (integration, 2 tests)

- `test_coverage_report_written_with_kov_split` (1 KOV act
  + 1 state law → JSON has expected splits)
- `test_gate_fails_when_kov_input_nontrivial_but_no_output` (same
  triple-monkeypatch trick from PR #1 v5 — `iter_peep_files` +
  `load_json` + `save_json`; 11,001 synthetic KOV paths with
  non-detecting content; `rc == 1`)

### `TestCorpusInvariant` (corpus-wide, 1 test, `@pytest.mark.slow`)

- `test_kov_competentauthority_is_issuer_iri` (scan all KOV peeps;
  every `competentAuthority` IRI whose normalised slug is a body
  word — volikogu/valitsus body type — MUST be an `Issuer_*` IRI;
  no `Institution_<body_slug>` for KOV-scoped detections)

## Implementation order

Ten commits, TDD shape (matches PR #1 convention with unpin
scheduled BEFORE coverage):

1. Add 2 new `GENERIC_PATTERNS` entries + `_resolve_kov_authority`
   helper + unit tests (`TestKovBodyWordDetection` 3 +
   `TestResolveKovAuthority` 4 = 7 tests).
2. Wire `_resolve_kov_authority` into `main()`'s per-provision
   loop + integration tests
   (`TestExtractCompetenceWithIssuerBinding`, 3 tests).
3. Refactor to per-file in-memory processing + provision-level
   idempotency (`TestCompetenceIdempotency`, 3 tests).
4. Stale `institutions/<slug>.json` deletion when no provisions
   cite the slug after re-run (`TestStaleInstitutionFileDeletion`,
   1 test).
5. UPDATE the existing SHACL `competentAuthority` constraint's
   description (the `sh:nodeKind sh:IRI` shape already exists at
   lines 140-145; this commit only refreshes the `sh:description`
   to reflect the broader value range).
6. Unpin: remove `include_kov=False` from `iter_peep_files` call.
7. Coverage instrumentation reusing `kov_pipeline_coverage` +
   `main() -> int` + `SystemExit` (`TestCompetenceCoverageReport`,
   2 tests).
8. Run full corpus + commit output (~8,000 peep modifications +
   institutions/ regenerations; `extract_institutional_competence_coverage.json`
   first run). Add `TestCorpusInvariant` (1 test).
9. Documentation: `SCHEMA_REFERENCE.md` + `CHANGELOG.md`.
10. Final verification + PR body. Targeted SHACL via
    `results_graph.subjects(SH.resultPath, URIRef(ESTLEG_NS +
    "competentAuthority"))`.

## Test count cumulative arithmetic

PR #1 ended at 206 passing.

| Task | New tests | Cumulative |
| --- | --- | --- |
| 1 | +7 | 213 |
| 2 | +3 | 216 |
| 3 | +3 | 219 |
| 4 | +1 | 220 |
| 5 | +0 (SHACL) | 220 |
| 6 | +0 (unpin) | 220 |
| 7 | +2 | 222 |
| 8 | +1 | 223 |
| 9 | +0 (docs) | 223 |
| 10 | +0 (verification) | 223 |

**Final expected count: 223 tests (+17 vs Layer 2c PR #1
end-state).**

## Risks and mitigations

**Risk 1: Issuer registry has municipalities that historical KOV
acts referenced under their pre-2017-haldusreform names.**

The Layer 1 `currentMunicipality` field maps historical municipalities
to their successor entities (per the parent spec's Layer 1 design).
A 2010 Tallinn KOV act whose `enactedByMunicipality` is the modern
Tallinn municipality entity should still resolve cleanly. If the
upstream Layer 1 mapping is wrong for a particular act, the
resolver returns None (abstain) — a benign failure mode that
surfaces in `unresolved_references`.

**Risk 2: Detection regex over-matches on word boundaries.**

The new patterns use `\b...\b` boundaries and require the full
body word (no partial matching). The inflection group
`(?:e|t|ele|ses|sse)?` for `valitsus` is greedy; it might match
nominative `valitsus` followed by a hyphen and another word
(e.g. `vallavalitsus-eelnõu` — though that's not real Estonian).
Mitigation: test corpus-derived inflections in
`test_vallavalitsus_inflections`. If post-merge analysis shows
unexpected matches, tighten in a follow-up.

**Risk 3: 8,000 KOV peep modifications produce a large diff.**

Stage in chunks of 500 via the v5 staging script PR #1 used.
Reviewer focuses on the 4 source-code commits + 1 corpus run +
the SHACL/docs/PR-body commits; the corpus output is mechanical
artifact.

**Risk 4: Resolver silently failing → fallthrough to body-slug
`Institution_*` path.**

The wiring change skips `inst_data[inst_iri] = ...` when the
resolver succeeds. The DANGER is a bug where the resolver
silently returns None (e.g. `_id_ref` mis-unwrap, registry build
empty, body-slug normalisation failing) — the code would
fallthrough to the original `Institution_*` path and emit
`estleg:Institution_linnavolikogu` (the BODY slug — not the
issuer slug, because at fallthrough we only have the body word).
Mitigation: `test_kov_act_competence_binds_to_issuer` asserts:

1. The provision's `competentAuthority` list contains the
   expected Issuer IRI (e.g. `estleg:Issuer_tallinna_linnavolikogu`).
2. The same list does NOT contain `estleg:Institution_linnavolikogu`
   or `estleg:Institution_vallavolikogu` etc. (body-slug fallback).
3. The `krr_outputs/institutions/` directory does NOT contain
   `institution_linnavolikogu.json` etc. (body-slug per-institution
   file that would only exist on a fallthrough emission).

The corpus invariant test (`TestCorpusInvariant`) extends this to
a corpus-wide check: no KOV peep's `competentAuthority` may
contain a body-slug `Institution_*` IRI, and no body-slug
`institution_<body_slug>.json` may exist after the run.

**Risk 5: Stale-institution-file deletion deletes a file that's
still cited by a peep that errored out and didn't get processed.**

The deletion runs at the end of `main()` over the full
`pre_existing_institution_files` set, scoped against the FULL run's
`written_slugs` aggregate. If a peep file errors mid-iteration,
the institution it would have cited is not in `written_slugs`, and
a stale file CAN be deleted incorrectly. Mitigation: only delete
when zero per-peep errors occurred. Documented in code; the
per-file flow naturally rolls forward from a few failures, but
stale-file deletion is a more destructive op that should require
a clean run.

**Implementation-order note:** the per-peep error counter must
exist BEFORE Task 4's deletion logic. Task 7 (coverage
instrumentation) wires the full `_files_skipped` / `_failures` /
`_skip_reasons` accumulators per the kov_pipeline_coverage helper,
but Task 4 lands earlier. Resolution: Task 4 introduces a MINIMAL
local counter (`_per_peep_errors: int = 0`, incremented inside the
existing `try / except (json.JSONDecodeError, OSError)` block
around `load_json`) used solely to guard the deletion:

```python
# At the end of main(), AFTER the per-peep loop and AFTER the
# per-institution writer block:
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
          f"institution files to avoid deleting valid entries "
          f"whose owning peep failed to load.")
```

Task 7 then EXTENDS this counter into the full `_failures` list +
`error_count` reporting; the deletion guard uses
`len(_failures) == 0` after Task 7 lands. The per-peep counter
introduced in Task 4 is the minimal scaffolding to keep the
guard meaningful at every commit point.

## Out-of-scope reminders

This PR does **not**:

- Modify `extract_court_provision_links` (Layer 2c PR #3).
- Reify `competenceType` per-authority (Layer 3-class work).
- Touch the legacy `\b(vald|linn)\b` pattern.
- Add new SHACL **shapes** — only adds a property constraint to
  the existing `LegalProvisionShape`.
- Touch the `iter_peep_files` default flip (already done in
  Layer 2a).
- Refactor the `institutions/` output format (per-institution
  filename convention `institution_<slug>.json` remains
  unchanged).

## Gate B umbrella status (after this PR lands)

- 2a COMPLETE (#98)
- 2b COMPLETE (#99)
- 2c PR #1 sanctions: COMPLETE (#100)
- 2c PR #2 institutional-competence: this PR
- 2c PR #3 court-provision-links: pending

Gate B closes when Layer 2c PR #3 lands.
