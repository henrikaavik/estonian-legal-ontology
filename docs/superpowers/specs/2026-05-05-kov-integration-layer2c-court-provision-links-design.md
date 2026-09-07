# KOV Integration Layer 2c — Court-Provision Links (PR #3 of 3)

**Date:** 2026-05-05 (v4)
**Status:** Spec v4: address two consistency findings on the fixture example citation form (must include a structured date the regex matches) and `skip_reasons` zero-key pre-seeding for stable report shape.
**Parent spec:** `docs/superpowers/specs/2026-05-02-kov-integration-design.md` (Layer 2c — `extract_court_provision_links` section, lines 550–573).
**Predecessors:** Layer 2a (#98, merged), Layer 2b (#99, merged), Layer 2c PR #1 sanctions (#100, merged), Layer 2c PR #2 institutional-competence (#101, merged).
**Closes:** Gate B umbrella.

## Goal

Make `extract_court_provision_links.py` resolve KOV act-level citations in Riigikohus court-decision summaries to KOV `MunicipalRegulation` IRIs, alongside the existing state-law provision-level resolution. Emit `estleg:interpretsLaw` (court → act) and `estleg:interpretedBy` (act → court) at act granularity for KOV; state-law links stay at provision granularity. Add the per-pipeline KOV coverage report mandated by the umbrella spec.

## Empirical baseline (from full data, 2026-05-05)

Measured by running the case-fixed `PAT_KOV_ACT` (Section "Citation regex" below) over all 34 `riigikohus_*_peep.json` files and resolving against the proposed `(issuer_norm, entryIntoForce-year, actNumber)` index over all 11,059 KOV peep files:

- 12,137 court decisions scanned.
- KOV act index: 10,808 unique keys, 122 collisions (collision keys tracked but not resolved).
- All 357 known issuers in the index.
- **35 matched** KOV act-form citations.
- **5 resolved** to KOV act IRIs (verified examples below).
- **29 unresolved** (`issuer_year_num_unmatched`) — predominantly historical citations from 1993–1999 where the cited act either entered force years before the citation year or is no longer represented in the current consolidated KOV peep set.
- **1 ambiguous** (`ambiguous_key`) — same `(issuer, year, actNumber)` resolves to two distinct IRIs.
- **0 RT IV-shape citations** (`RT IV, YYYY-MM-DD, N`) observed.

**Verified resolving examples** (these will land as new `interpretsLaw` edges after the PR):

| Court decision text                                                | Resolved act IRI                  |
|--------------------------------------------------------------------|-----------------------------------|
| Narva Linnavolikogu 2006 #30                                       | `estleg:Reg_1013909_Map`     |
| Tallinna Linnavalitsus 2009 #75 (entered force 2010 — `+1` alt)    | `estleg:Reg_1014396_Map`     |
| Viimsi Vallavolikogu 2009 #22                                      | `estleg:Reg_1024484_Map`     |
| Tallinna Linnavolikogu 2008 #30                                    | `estleg:Reg_1014955_Map`     |
| Kohtla-Järve Linnavolikogu 2018 #24 (entered force 2019 — `+1` alt) | `estleg:Reg_1044438_Map`    |

**Realistic expected output:** **5 new `interpretsLaw` edges** to KOV act IRIs (the table above), **29 unresolved entries** in `failure_samples`, **1 ambiguous entry**, **0 RT IV citations** counted.

The PR's value isn't the edge count — it's: (a) the pipeline runs cleanly over KOV with a coverage report, satisfying Gate B; (b) the few resolvable edges actually land; (c) unresolved historical residue is visible in the report so future enrichment passes can target it.

## Scope decision: act-level resolver, RT IV deferred

The umbrella spec called for both a KOV act-level resolver and an RT IV reference resolver. We ship only the act-level resolver in this PR because:
- 0 RT IV-shape citations are observed in current court summaries.
- KOV peep act nodes carry no explicit parsed `RT IV` reference field. (KOV peeps do carry `estleg:globalId` and `dcterms:source` URLs that *encode* RT identity, so a resolver could in principle be built by parsing those URLs — but parsing them is itself the work the deferred RT IV resolver represents. Today there's no preprocessed RT IV → IRI table available to look up against.)
- Building an RT IV resolver against zero observed input is dead code.

The coverage report adds an explicit `rtiv_form_citation` counter so a future ingest that surfaces RT IV citations triggers the follow-up PR by becoming non-zero.

## Crisp invariants

After the PR lands, all of the following hold:

1. `extract_court_provision_links.py` builds two parallel indexes: an unchanged state-law provision index (still via `iter_peep_files(include_kov=False)`) and a new KOV act index (via `iter_peep_files()` filtered to KOV peeps).
2. The two `iter_peep_files(include_kov=False)` callsites at lines 53 and 365 stay pinned. The state-law provision index correctness depends on KOV peeps being excluded.
3. `build_kov_act_index()` keys each KOV act by `(issuer_norm, year, act_number)`. On collision (two acts mapping to the same key), the key is recorded in `kov_collision_keys` and never resolves.
4. `normalize_issuer_name()` lives in `scripts/estleg_common.py` and is called from both KOV index build and citation resolution. Issuer-string compare is on normalized form (lowercase, diacritic-transliterated, whitespace-collapsed).
5. `resolve_kov_citation()` first checks the `known_issuer_norms` precondition (returning `"unknown_issuer"` if not in set), then checks `kov_collision_keys` before primary-year lookup AND before `year + 1` alternate lookup. Ambiguous on either step short-circuits with reason `"ambiguous_key"`.
6. `estleg:interpretsLaw` arrays may now contain a mix of `LegalProvision` IRIs (state law) and `MunicipalRegulation` Act IRIs (KOV). Same JSON-LD shape (`{"@id": iri}` per entry).
7. KOV act files receive `estleg:interpretedBy` triples for any act IRI a court decision resolves to. The same predicate name is used as for state-law provisions.
8. Stale `estleg:interpretedBy` cleanup walks ALL KOV peep files each run (not just current-run targets), so a peep that was a target last run but isn't this run loses its stale triples.
9. SHACL `estonian_legal_shapes.ttl`: `interpretedBy` and `interpretsLaw` descriptions widened (triple-quoted Turtle literals); a new `interpretedBy` property shape attached to `MunicipalRegulationShape` (same `sh:nodeKind sh:IRI`, no `sh:class`, no `sh:or`).
10. Formal RDFS range on `interpretsLaw` widened to `owl:unionOf (LegalProvision Act)` in `scripts/generate_court_decisions.py:251` and the regenerated `krr_outputs/riigikohus/riigikohus_schema.json:159–161`. `interpretedBy` has no existing RDFS range and gains none in this PR.
11. Coverage report `krr_outputs/reports/kov/court_provision_links_coverage.json` exists; `files_processed_kov` is the count of KOV peeps scanned for the index; `triples_emitted_kov` is exactly the count of new `interpretsLaw` arcs whose target IRI is a KOV act.
12. Re-runs are idempotent: two consecutive runs over the same input produce byte-identical peep files and a coverage report identical except for `run_timestamp`, `wall_time_seconds`, `items_per_second`, `peak_memory_mb`.
13. Real-data invariant: `triples_emitted_kov ≥ 1` after the run on the full corpus. The empirical baseline (Section "Empirical baseline" above) lists 5 currently-resolving citations; the invariant is set to `≥ 1` to remain robust to corpus changes between PR drafting and merge.

## Why this PR closes Gate B

Per the parent spec at lines 906–915, Gate B requires "all ten KOV-relevant pipelines run cleanly over the full KOV set" with per-pipeline coverage reports. Court-provision-links was the last pipeline still pinned `include_kov=False` (lines 53, 365) with no coverage report. After this PR:

- All ten KOV-relevant pipelines (cross-ref, inverse, deontic, EuroVoc, temporal, sanctions, competence, legal-concepts, court-provision-links, amendment-history) emit per-pipeline coverage reports under `krr_outputs/reports/kov/`.
- Per-caller `iter_peep_files()` audit is complete (Layer 2a established the audit; Gate B sub-PRs each enforced it for their script).
- The `generate_similarity_index` `include_kov=False` pin established by Layer 2a (per parent spec lines 913–915) remains in place — it's lifted only by Gate C's act-level KOV similarity work.

## Files touched

- `scripts/extract_court_provision_links.py` — new `build_kov_act_index()`, new `PAT_KOV_ACT` regex, new `resolve_kov_citation()`, accumulator returning `(state_citations, kov_citations)`, widened resolution merge, KOV-side stale-link cleanup, integrated `kov_pipeline_coverage.CoverageReport`.
- `scripts/estleg_common.py` — new `normalize_issuer_name()` helper. New `BODY_CANON` mapping for genitive-to-nominative body-word canonicalization.
- `scripts/generate_court_decisions.py` — `rdfs:range` on `interpretsLaw` widened to `owl:unionOf (LegalProvision Act)`; `rdfs:comment` updated.
- `krr_outputs/riigikohus/riigikohus_schema.json` — regenerated from the updated generator. Same range widening.
- `shacl/estonian_legal_shapes.ttl` — three description updates (triple-quoted), one new property shape on `MunicipalRegulationShape`.
- `tests/test_extract_court_provision_links.py` — NEW file. Four test groups (regex, resolver, end-to-end, idempotency + SHACL).
- `tests/fixtures/kov_court_provision_links/` — NEW fixture dir (riigikohus, state_laws, kov subdirs).

No edits to `scripts/run_all_integration.py` (the integrator's invocation signature is unchanged).

## Module-level design

### Citation regex

A new pattern `PAT_KOV_ACT` is added alongside the existing `pat_abbrev` and `pat_fullname`. **Critical: `re.IGNORECASE` is NOT applied globally** — under global IGNORECASE, the municipality character class `[A-ZÕÄÖÜŠŽ]` would also accept lowercase letters and the `{1,3}` quantifier would consume preceding lowercase words like *"Õiguskantsleri taotlus Tallinna ..."* or *"tunnistada kehtetuks Keila ..."*, polluting `issuer_norm` and breaking resolution. Case-insensitivity is scoped via inline `(?i:...)` only to the body words, month names, and act-marker keywords.

```python
PAT_KOV_ACT = re.compile(
    # Municipality: 1-3 titlecase words (uppercase initial, lowercase rest).
    # Case-SENSITIVE — relies on Estonian convention that proper-noun
    # municipality names are titlecased while surrounding prose is not.
    rf"(?P<municipality>(?:[A-ZÕÄÖÜŠŽ][a-zõäöüšž][\wõäöüšž-]*\s+){{1,3}})"
    # Body word: case-insensitive scope only. Genitive forms first so
    # alternation longest-match is well-defined.
    rf"(?P<body>(?i:linnavalitsuse|vallavalitsuse|"
    rf"linnavalitsus|vallavalitsus|"
    rf"linnavolikogu|vallavolikogu))\s+"
    # Date: numeric or long form with case-insensitive Estonian month name.
    rf"(?P<date>\d{{1,2}}\.\d{{1,2}}\.(?:\d{{2}}|\d{{4}})|"
    rf"\d{{1,2}}\.\s*(?i:jaanuari|veebruari|märtsi|aprilli|mai|juuni|juuli|"
    rf"augusti|septembri|oktoobri|novembri|detsembri)\s+\d{{4}})"
    # Optional " . a. " / " . aasta " between date and act marker.
    rf"\.?\s*(?i:a\.?|aasta)?\s+"
    # Act marker, case-insensitive scope.
    rf"(?i:määrus(?:e|t|ega)?)\s*nr\s*\.?\s*(?P<num>\d+)",
    re.UNICODE,
)
```

Coverage notes:
- The municipality character class `[A-ZÕÄÖÜŠŽ][a-zõäöüšž]...` enforces titlecase. Verified empirically: yields exactly 35 matches across the full court corpus, with no over-capture of preceding lowercase prose.
- Both numeric (`21.06.99`) and Estonian-month (`18. juuni 2020`) date forms.
- Genitive bodies (`Linnavalitsuse`, `Vallavalitsuse`) listed before nominative (`Linnavalitsus`, `Vallavalitsus`) inside each pair so longest-match-wins under alternation is well-defined cross-engine.
- Optional `.` / `a.` / `aasta` token after date (`18. juuni 2020. a määruse nr 15`).
- Instrumental case via `määrus(?:e|t|ega)?` (covers `määrus`, `määruse`, `määrust`, `määrusega`).
- Plural vague references (`määruste peale`) intentionally not matched — no number to resolve.
- `actNumber` is `\d+` (verified empirically: all `estleg:actNumber` values are bare integers).

### Two-digit year disambiguation

```python
def expand_two_digit_year(date_str: str) -> int:
    # extract the year component
    if "." in date_str and date_str.split(".")[-1].isdigit():
        y = int(date_str.split(".")[-1])
    else:
        # long form ends in a 4-digit year
        y = int(date_str.split()[-1])
    if y < 100:
        return 1900 + y if y > 50 else 2000 + y
    return y
```

Boundary semantics: `y > 50` means `y` in `{0..50}` resolves to `2000..2050` and `y` in `{51..99}` resolves to `1951..1999`. KOV regulations don't predate the 1990s republic, and `"21.06.50"`-form citations don't appear in current court summaries — the 51-year split is unambiguous in practice. (See test list in Group 1: `99 → 1999`, `51 → 1951`, `50 → 2050`, `25 → 2025`.)

### Body canonicalization

```python
BODY_CANON = {
    "linnavalitsuse": "linnavalitsus",
    "vallavalitsuse": "vallavalitsus",
    "linnavalitsus":  "linnavalitsus",
    "vallavalitsus":  "vallavalitsus",
    "linnavolikogu":  "linnavolikogu",
    "vallavolikogu":  "vallavolikogu",
}
```

Court text uses the genitive *Linnavalitsuse / Vallavalitsuse*; KOV peep `estleg:issuer` field uses the nominative *Linnavalitsus / Vallavalitsus*. The volikogu forms are identical in both cases. Lookup keys are built using `BODY_CANON[match['body'].lower()]` so both sides normalize to the same string before issuer-name compare.

### Issuer normalization

```python
def normalize_issuer_name(s: str) -> str:
    """Lowercase + Estonian diacritic transliteration + whitespace collapse."""
    s = s.lower()
    s = (s.replace("õ", "o").replace("ä", "a").replace("ö", "o")
           .replace("ü", "u").replace("š", "s").replace("ž", "z"))
    return " ".join(s.split())
```

Same approach as the Layer 1 `titleNormalized` precedent. Applied to both:
- The reconstructed display name from a regex match: `f"{match['municipality']} {BODY_CANON[match['body'].lower()]}"` → normalize.
- The KOV peep `estleg:issuer` field at index-build time → normalize.

### Resolver index keying: deliberate `entryIntoForce` approximation

The KOV act index is keyed on `(issuer_norm, entryIntoForce.year, actNumber)`, while court citations refer to the act by *adoption* date (the date the body passed the act, not when it took effect). These coincide in many cases but not all — acts adopted in late Q4 of year Y often enter force in year Y+1, and historical acts may have adoption-vs-entry-into-force gaps of multiple months. Adoption date (`<vastuvoetud><aktikuupaev>` in source XML, exposed as `estleg:adoptionDate` if extracted) is not currently a field on KOV peep act nodes — adding that extraction is its own enrichment task.

Two consequences for this PR:

1. **Approximate resolver, deliberate.** The `+1 year` alternate (Section "Citation resolver" below) handles the common adoption-Y / entry-into-force-Y+1 case in one direction. The empirical "Tallinna Linnavalitsus 2009 #75" → `Reg_1014396_Map` (entered force 2010) resolves precisely because of this.
2. **Documented miss profile.** The reverse direction — adoption in Y, entry-into-force in Y-1 — is not covered. So is the case where the consolidated KOV peep represents a current redaction whose `entryIntoForce` reflects a later amendment year, not the original adoption year. Of the 29 unresolved citations in the empirical baseline, the dominant cause is this: most are 1990s citations whose original act either no longer exists in the consolidated peep set, or has been re-enacted with a later `entryIntoForce` year. Surfacing them in `failure_samples` is the right outcome — they become the input for a future adoption-date enrichment PR.

The PR explicitly does NOT extract adoption date from XML. That work is out of scope (see Out of scope).

### Run-counters plumbing

To make the failure-bookkeeping requirement (every malformed input increments `files_skipped` + `skip_reasons["json_decode_error"]` + `error_count` + one `failure_samples` entry) trivially testable, all functions that read JSON files share a single mutable accumulator:

```python
@dataclass
class _RunCounters:
    file_skips: int = 0
    error_count: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    # De-dup set for malformed-input paths. The same file may be opened
    # by several walks (cleanup, index build, processing); we count each
    # *unique* malformed path exactly once across the whole run so the
    # report's files_skipped reflects unique files, not failed reads.
    seen_error_paths: set[Path] = field(default_factory=set)

    def bump_skip(self, reason: str, sample: str) -> None:
        self.file_skips += 1
        self.error_count += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        if len(self.failures) < 200:   # over-cap; helper enforces 20 final
            self.failures.append(sample)


def _safe_load(path: Path, counters: _RunCounters) -> dict | None:
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

Every read site (`build_provision_index`, `build_kov_act_index`, `process_court_files`, `apply_interpreted_by`, `clear_existing_court_links`) takes `counters: _RunCounters` and uses `_safe_load`. When `_safe_load` returns `None`, the caller continues to the next file. The helper guarantees the four side effects atomically — and `seen_error_paths` ensures `files_skipped` counts unique malformed *files*, not the number of failed read attempts (the same file may be revisited by cleanup, index-build, and processing walks).

`_RunCounters.skip_reasons` is initialized as an empty dict; `main()` pre-seeds the four expected pipeline-specific bucket keys (`kov_citation_ambiguous`, `kov_citation_unknown_issuer`, `rtiv_form_citation`, `json_decode_error`) to `0` before any work runs (see `main()` snippet below). This guarantees a stable JSON report shape — every coverage report carries those four keys whether or not any hit incremented them — so tests and downstream consumers can index `skip_reasons["rtiv_form_citation"]` directly without `dict.get(..., 0)`.

### KOV act index build

```python
def build_kov_act_index(counters: _RunCounters) -> tuple[
    dict[tuple[str, int, str], str],   # kov_index: key → IRI
    set[tuple[str, int, str]],         # kov_collision_keys
    dict[str, Path],                    # kov_iri_to_file
    set[str],                           # known_issuer_norms
]:
    kov_index: dict = {}
    kov_collision_keys: set = set()
    kov_iri_to_file: dict[str, Path] = {}
    known_issuer_norms: set[str] = set()

    for f in iter_peep_files():            # all peeps; filter to KOV below
        doc = _safe_load(f, counters)
        if doc is None:
            continue
        for node in doc.get("@graph", []):
            types = node.get("@type", [])
            if "estleg:MunicipalRegulation" not in types:
                continue
            iri = node.get("@id", "")
            issuer = node.get("estleg:issuer", "")
            entry_force = node.get("estleg:entryIntoForce", {}).get("@value", "")
            act_number = str(node.get("estleg:actNumber", ""))
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
                pass  # already marked ambiguous
            elif key in kov_index and kov_index[key] != iri:
                kov_collision_keys.add(key)
                del kov_index[key]
            else:
                kov_index[key] = iri
            kov_iri_to_file[iri] = f
            break  # one act node per peep
    return kov_index, kov_collision_keys, kov_iri_to_file, known_issuer_norms
```

`known_issuer_norms` is the set of every normalized `estleg:issuer` string seen across all KOV peeps (357 in current data). The resolver uses it as a defensive precondition (Section "Citation resolver" below).

Note: collision detection here removes the conflicting key from `kov_index` and adds it to `kov_collision_keys`. `kov_iri_to_file` retains every KOV act IRI seen during the walk; the resolver consults `kov_index` and `kov_collision_keys`, while the inverse-link writer consults `kov_iri_to_file` to find the file owning a given IRI. The stale-cleanup pass (walk #3 in the cleanup section) walks all KOV peeps independently — it does NOT rely on `kov_iri_to_file`, because a peep that was a target last run but had its single matching peep removed from the corpus this run wouldn't appear in this run's `kov_iri_to_file` and would otherwise retain stale `interpretedBy`.

### Citation resolver

```python
def resolve_kov_citation(
    match: dict,
    kov_index: dict,
    kov_collision_keys: set,
    known_issuer_norms: set,
) -> tuple[str | None, str | None]:
    issuer_norm = normalize_issuer_name(
        f"{match['municipality'].strip()} {BODY_CANON[match['body'].lower()]}"
    )
    if issuer_norm not in known_issuer_norms:
        return None, "unknown_issuer"     # defensive: regex over-fired or defunct issuer
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

Strict policy:
1. **Known-issuer precondition.** If `issuer_norm` is not in `known_issuer_norms` (357 issuers from current data), return `unknown_issuer` immediately. Defensive insurance against future regex drift; expected zero hits today (verified — all 35 empirical matches have `issuer_norm ∈ known_issuer_norms`).
2. **Primary-year strict match.** Direct `(issuer, year, num)` lookup against `kov_index`, with `kov_collision_keys` short-circuit.
3. **Alternate `+1 year`.** Absorbs the common adoption-Y / entry-into-force-Y+1 case (one-way only; see "Resolver index keying" rationale above). Ambiguous on alternate also returns `ambiguous_key` — never silently falls through to `issuer_year_num_unmatched` when the alternate key is collision-tracked.

Failure reasons feed `skip_reasons` and `failure_samples` separately:
- `"unknown_issuer"` → `skip_reasons["kov_citation_unknown_issuer"]`.
- `"ambiguous_key"` → `skip_reasons["kov_citation_ambiguous"]`.
- `"issuer_year_num_unmatched"` → `unresolved_references` (top-level field, not a `skip_reasons` entry — it's the natural fit per the helper's semantics).

### Citation extraction

`extract_citations_from_text(text)` is widened to return a tuple:

```python
def extract_citations_from_text(text: str) -> tuple[
    list[dict],   # state_citations: existing {law_ref, paragraphs} shape
    list[dict],   # kov_citations:  new {municipality, body, date, num, raw_text}
]:
    state_citations = [...]   # existing logic; unchanged
    kov_citations = []
    for m in PAT_KOV_ACT.finditer(text or ""):
        kov_citations.append({
            "municipality": m.group("municipality"),
            "body":         m.group("body"),
            "date":         m.group("date"),
            "num":          m.group("num"),
            "raw_text":     m.group(0),
        })
    return state_citations, kov_citations
```

Also, an `rtiv_form_citation` counter is incremented separately by a small `count_rtiv_form()` helper that runs the same regex against `text` and returns a count. The counter feeds `skip_reasons["rtiv_form_citation"]` in the coverage report.

### Resolution merge

In `process_court_files()`:

```python
state_citations, kov_citations = extract_citations_from_text(summary)
state_iris = resolve_citations(state_citations, abbrev_to_prefix, prefix_to_provisions)
kov_iris = []
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
        f"{reason:<18s} | {node_id} | "
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
    else:  # issuer_year_num_unmatched
        kov_unresolved += 1   # feeds unresolved_references
        if len(counters.failures) < 200:
            counters.failures.append(sample)

resolved = list(dict.fromkeys(state_iris + kov_iris))    # dedupe, preserve order
```

The merge happens AFTER both resolvers run. The deduplicated list is assigned to `node["estleg:interpretsLaw"]` exactly as today. `kov_unresolved` accumulates across all decisions and feeds the report's top-level `unresolved_references` field.

Separately, RT IV citations are counted but not resolved:

```python
for _ in PAT_RTIV.finditer(summary or ""):
    counters.skip_reasons["rtiv_form_citation"] = (
        counters.skip_reasons.get("rtiv_form_citation", 0) + 1
    )
    # No failure_samples entry per RT IV hit — they're a known-deferred
    # bucket counted only at aggregate level.
```

### Inverse application

The existing `interpreted_by` accumulator keys are renamed `interpreted_by_target` (any IRI), and the lookup table is the merged `iri_to_file | kov_iri_to_file`. `apply_interpreted_by()` is unchanged structurally — it walks each target file, finds the node with the matching `@id`, and writes `estleg:interpretedBy`. Whether that node is a `LegalProvision` or a `MunicipalRegulation` is invisible to the writer.

### Stale-link cleanup

`clear_existing_court_links()` runs three walks (was two):

1. **Court files** — `RK_DIR/riigikohus_*_peep.json` → strip `estleg:interpretsLaw`. Unchanged.
2. **State-law files** — `iter_peep_files(include_kov=False)` → strip `estleg:interpretedBy`. Unchanged (pinned).
3. **KOV files** — `iter_peep_files()` filtered to KOV → strip `estleg:interpretedBy` from any node that carries it. NEW.

Walk #3 walks all KOV peeps each run (not just current-run targets) so that a peep that was a target in a prior run but isn't in this run loses its stale `interpretedBy`. The walk only writes when a node actually carries `interpretedBy`, so the cost is dominated by reads — same shape as walk #2.

### Coverage report integration

```python
import time
from kov_pipeline_coverage import (
    CoverageReport, write_coverage_report,
    measure_runtime, resolve_pipeline_version,
)

def main() -> None:
    start = time.perf_counter()
    counters = _RunCounters()
    # Pre-seed every expected skip_reasons bucket to 0 so the report
    # shape is stable across runs — tests and downstream consumers
    # can index counters.skip_reasons["rtiv_form_citation"] directly
    # without get(..., 0). bump_skip() preserves existing values via
    # dict.get with default 0, so pre-seeding is purely additive.
    for _key in (
        "kov_citation_ambiguous",
        "kov_citation_unknown_issuer",
        "rtiv_form_citation",
        "json_decode_error",
    ):
        counters.skip_reasons[_key] = 0
    # All read sites and resolver paths bump counters via _RunCounters;
    # main() does not maintain its own per-reason locals. kov_unresolved
    # accumulates separately because it feeds the top-level
    # unresolved_references field, not skip_reasons.
    kov_unresolved = 0
    # ... existing processing ...

    # Final coverage emission after all writes complete:
    wall, rate, peak_mb = measure_runtime(start, total_decisions)
    report = CoverageReport(
        pipeline="extract_court_provision_links",
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        pipeline_version=resolve_pipeline_version(),
        input_files_total=(rk_file_count + state_peep_count + kov_peep_count),
        input_files_kov=kov_peep_count,
        files_processed=(rk_files_read + state_peeps_scanned + kov_peeps_scanned),
        files_processed_kov=kov_peeps_scanned,
        # files_with_output counts ALL files that received any new triple
        # this run: court files (interpretsLaw), state-law files (interpretedBy),
        # AND KOV files (interpretedBy). files_with_output_kov is the KOV
        # subset of that count.
        files_with_output=court_files_written + law_files_written + kov_files_written,
        files_with_output_kov=kov_files_written,
        files_skipped=counters.file_skips,
        skip_reasons=counters.skip_reasons,
        # triples_emitted counts forward interpretsLaw arcs only (one per
        # resolved citation, regardless of how many inverse interpretedBy
        # writes that fans out to). Aligned with invariant #11.
        # triples_emitted_kov is the subset whose target IRI is a KOV act.
        # state_link_count = forward arcs to LegalProvision targets.
        # kov_link_count   = forward arcs to MunicipalRegulation targets.
        # Inverse interpretedBy edges are mechanically derived (1 inverse
        # per forward, deduplicated per target file) and not separately
        # counted to avoid double-counting the same logical citation.
        triples_emitted=state_link_count + kov_link_count,
        triples_emitted_kov=kov_link_count,
        fallback_hits=0,
        unresolved_references=kov_unresolved,
        wall_time_seconds=wall,
        items_per_second=rate,
        peak_memory_mb=peak_mb,
        error_count=counters.error_count,
        failure_samples=counters.failures,   # helper caps at 20
    )
    out_path = KRR_DIR / "reports" / "kov" / "court_provision_links_coverage.json"
    write_coverage_report(report, out_path)
```

`failure_samples` strings carry self-contained context. Two reason codes plus the unknown-issuer case all use the same `reason` slot (matching the resolver implementation in the merge section above):

```
issuer_year_num_unmatched   | <decision_id> | issuer=<issuer_norm> year=<Y> num=<N> | reason=issuer_year_num_unmatched
ambiguous_key               | <decision_id> | issuer=<issuer_norm> year=<Y> num=<N> | reason=ambiguous_key
unknown_issuer              | <decision_id> | issuer=<issuer_norm> year=<Y> num=<N> | reason=unknown_issuer
json_decode_error           | <filename>    | <ExceptionClass>: <msg first 80 chars>
```

The helper caps the list at 20.

RT IV-form citations do **NOT** produce `failure_samples` entries (they're a known-deferred bucket counted at aggregate level via `skip_reasons["rtiv_form_citation"]` only). This keeps the 20-sample cap focused on actionable failures.

### Diagnostic logging

After the existing `[1/5]`...`[5/5]` step prints, add:

```
[6/6] KOV act citation summary:
  KOV acts in index:              <N>     (collisions excluded: <K>)
  Known issuers:                  <I>
  KOV citations matched:          <M>
  KOV citations resolved:         <R>     (where R = M - U - A - X)
  KOV citations unresolved:       <U>
  KOV citations ambiguous-key:    <A>
  KOV citations unknown-issuer:   <X>     (regex fired on non-issuer string)
  RT IV-form citations seen:      <T>     (not resolved this PR)
```

These are the same counts emitted into the coverage report.

## SHACL changes

`shacl/estonian_legal_shapes.ttl`:

```turtle
# LegalProvisionShape — UPDATE description only
sh:property [
    sh:path estleg:interpretedBy ;
    sh:nodeKind sh:IRI ;
    sh:name "interpretedBy" ;
    sh:description """Court decisions that interpret this provision (or, for KOV act-level citations, the act node).""" ;
] ;

# CourtDecisionShape — UPDATE description only
sh:property [
    sh:path estleg:interpretsLaw ;
    sh:nodeKind sh:IRI ;
    sh:name "interpretsLaw" ;
    sh:description """Provisions interpreted by this decision; for KOV citations the target may be a MunicipalRegulation act node when provision-level resolution is unavailable.""" ;
] ;

# MunicipalRegulationShape — NEW property shape
sh:property [
    sh:path estleg:interpretedBy ;
    sh:nodeKind sh:IRI ;
    sh:name "interpretedBy" ;
    sh:description """Court decisions that interpret this municipal act (act-level KOV citation).""" ;
] ;
```

No `sh:class`, no `sh:or`. The existing `sh:nodeKind sh:IRI`-only constraints don't restrict target type — the property simply requires the value be an IRI. Widening is achieved by leaving the absence of class restriction in place; the `sh:description` updates and the new symmetric property shape on `MunicipalRegulationShape` complete the documentation and CI coverage.

**Turtle-syntax implementation note.** `MunicipalRegulationShape` in `shacl/estonian_legal_shapes.ttl` currently terminates with a single property shape and a final `] .` (period-terminator). Adding the new `interpretedBy` property shape requires changing that final `] .` to `] ;` (semicolon) and then appending the new `sh:property [ ... ] .` block as the new shape terminator. Failing to do this produces a Turtle parse error rather than a SHACL semantics error, so the test suite would catch it on the next validator invocation — but flagging here so the implementer doesn't merge a syntactically broken TTL.

## RDFS range widening

`scripts/generate_court_decisions.py` line ~247–253:

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

`krr_outputs/riigikohus/riigikohus_schema.json` is regenerated from this updated source. The PR commit includes both files.

`interpretedBy` has no existing `rdfs:range` declaration in either file (verified by grep). It gets none in this PR — adding one would invent a constraint that didn't exist before. A future PR introducing formal ranges throughout the ontology can add both directions symmetrically.

## Tests

New file: `tests/test_extract_court_provision_links.py`. Four groups:

### Group 1 — Regex unit tests

- `test_pat_kov_act_numeric_date_genitive_body`: `"Keila Vallavolikogu 21.06.99 määrus nr 55"` matches with municipality=`Keila `, body=`Vallavolikogu`, date=`21.06.99`, num=`55`.
- `test_pat_kov_act_long_date_form`: `"Tallinna Linnavolikogu 18. juuni 2020. a määruse nr 15"` matches.
- `test_pat_kov_act_instrumental`: `"...Tallinna Linnavalitsuse 14.05.2009 määrusega nr 23..."` matches.
- `test_pat_kov_act_nominative_body`: `"Tallinna Linnavalitsus 14.05.2009 määrus nr 23"` matches.
- `test_pat_kov_act_no_match_vague`: `"Pärnu Linnavalitsuse määruste peale"` produces no match.
- `test_pat_kov_act_no_match_case_name`: `"Tallinna Linna määruskaebus Tallinna Ringkonnakohtu..."` produces no match.
- `test_pat_kov_act_no_overcapture_lowercase_prefix_long`: `"Õiguskantsleri taotlus Tallinna Linnavolikogu 19.02.2009 määruse nr 3 ..."` matches with `municipality.strip() == "Tallinna"` (NOT `"Õiguskantsleri taotlus Tallinna"`). Pins the case-sensitive municipality match against IGNORECASE-driven regression.
- `test_pat_kov_act_no_overcapture_lowercase_prefix_short`: `"tunnistada kehtetuks Keila Vallavolikogu 21.06.99 määrus nr 55"` matches with `municipality.strip() == "Keila"` (NOT `"tunnistada kehtetuks Keila"`).
- `test_two_digit_year_disambiguation`: `99` → 1999, `51` → 1951, `50` → 2050 (boundary; `y > 50` is the threshold), `25` → 2025, `00` → 2000.

### Group 2 — Resolver unit tests

- `test_resolver_strict_match`: index `{("tallinna linnavolikogu", 2009, "3"): "estleg:Reg_X"}` resolves matching query.
- `test_resolver_year_plus_one_alternate`: index has 2010 entry; query year=2009 resolves via `+1` fallback.
- `test_resolver_ambiguous_key_short_circuits`: primary-year key in `kov_collision_keys` returns `(None, "ambiguous_key")` without falling through to alternate.
- `test_resolver_alternate_ambiguous_short_circuits`: primary missing, alternate-year key in `kov_collision_keys` returns `(None, "ambiguous_key")`.
- `test_resolver_unmatched`: neither year present, issuer IS in `known_issuer_norms` → `(None, "issuer_year_num_unmatched")`.
- `test_resolver_unknown_issuer`: issuer NOT in `known_issuer_norms` → `(None, "unknown_issuer")` short-circuits before any index lookup. Pins the defensive precondition.
- `test_normalize_issuer_diacritic_and_case`: `"Põlva Vallavalitsus"`, `"polva vallavalitsus"`, `"PÕLVA VALLAVALITSUS"` normalize identically.
- `test_body_canon_genitive_to_nominative`: `BODY_CANON["linnavalitsuse"] == BODY_CANON["linnavalitsus"] == "linnavalitsus"`.

### Group 3 — End-to-end fixture

Fixture dir `tests/fixtures/kov_court_provision_links/`:

```
riigikohus/
  riigikohus_2009_peep.json     # CourtDecision citing "Viimsi Vallavolikogu 13. oktoobri 2009. a määruse nr 22" (resolves)
  riigikohus_1999_peep.json     # CourtDecision citing "Keila Vallavolikogu 21.06.99 määrus nr 55" (unresolved)
  riigikohus_2016_peep.json     # CourtDecision citing an issuer/year/num that maps to ambiguous_key
state_laws/
  tsiviilkohtumenetluse_seadustik_peep.json   # for state-law regression check (TsMS § 208)
kov/
  viimsi_vallavolikogu/maarus_22_t<id>_peep.json    # resolves Viimsi Vallavolikogu 2009 #22
  collision_pair/
    act_a_peep.json              # same (issuer, year, num) as act_b → ambiguous on index build
    act_b_peep.json
```

Fixture content uses a verified-resolving real example with a citation form the regex actually matches — e.g. *"Viimsi Vallavolikogu 13. oktoobri 2009. a määruse nr 22"* (long Estonian-month date form) or the numeric equivalent *"Viimsi Vallavolikogu 13.10.2009 määruse nr 22"* — both resolve to `estleg:Reg_1024484_Map`. (A bare-year form like *"... 2009 määrus nr 22"* does NOT match `PAT_KOV_ACT`; the regex requires either numeric `d.m.y` or long-form `d. <month-genitive> yyyy`. Implementers copying this fixture note must use one of the structured date forms.) The Keila 1999 #55 case is preserved as the unresolved-by-design example (its issuer IS in `known_issuer_norms` per Layer 1, but no peep matches the year+number — same shape as the empirical 29-citation residue).

Test asserts all of:
1. Court decision's `estleg:interpretsLaw` contains the resolved KOV act IRI (Viimsi 2009 #22).
2. KOV act's `estleg:interpretedBy` contains the court decision IRI.
3. State-law TsMS provision's `estleg:interpretedBy` is intact (regression: widening didn't break the existing path).
4. Keila citation lands in `failure_samples` with reason `issuer_year_num_unmatched`.
5. Ambiguous citation lands in `failure_samples` with reason `ambiguous_key`.
6. `triples_emitted_kov == 1` (Viimsi only).
7. `unresolved_references == 1` (Keila).
8. `skip_reasons["kov_citation_ambiguous"] == 1` (collision case).
9. `skip_reasons["rtiv_form_citation"] == 0`.
10. `skip_reasons.get("kov_citation_unknown_issuer", 0) == 0` (Keila Vallavolikogu IS a known issuer).
11. `files_processed_kov ⊆ files_processed`; `files_with_output_kov ⊆ files_with_output`.

### Group 4 — Idempotency + SHACL

- `test_two_consecutive_runs_byte_identical`: runs the script twice in a tmp dir over the Group 3 fixture; asserts every touched peep file is byte-identical between runs and `_stable(report1) == _stable(report2)` where `_stable()` strips `run_timestamp`, `wall_time_seconds`, `items_per_second`, `peak_memory_mb`.
- `test_shacl_widened_range_validates`: a fixture graph carrying (a) one `CourtDecision` whose `interpretsLaw` array contains both a `LegalProvision` IRI and a `MunicipalRegulation` IRI, (b) the `LegalProvision` carrying `interpretedBy` to the decision, (c) the `MunicipalRegulation` carrying `interpretedBy` to the decision. Run the project's SHACL validator entry point; assert zero violations on `LegalProvisionShape`, `CourtDecisionShape`, and `MunicipalRegulationShape`.

## Run integration & failure resilience

- `scripts/run_all_integration.py` already invokes `extract_court_provision_links`. Its CLI is unchanged — the integrator picks up widened behavior with no edit.
- A single malformed input (riigikohus or KOV peep) must not abort the run. Existing `try/except (json.JSONDecodeError, OSError)` on every read is preserved; the KOV index builder uses the same pattern.
- A malformed input increments **all four** of: `files_skipped`, `skip_reasons["json_decode_error"]`, `error_count`, and one capped `failure_samples` entry: `"json_decode_error | <filename> | <exception summary>"`. Matches the sanctions precedent.

## Acceptance criteria

For PR review:

1. Both `iter_peep_files(include_kov=False)` callsites at lines 53 and 365 stay pinned (state-law index correctness depends on it).
2. `build_kov_act_index()` walks all KOV peeps; returns `(kov_index, kov_collision_keys, kov_iri_to_file, known_issuer_norms)`. Never silent-overwrites; collisions are detected and removed from `kov_index` while the key is added to `kov_collision_keys`. `known_issuer_norms` is the set of every normalized `estleg:issuer` string seen.
3. `normalize_issuer_name()` lives in `estleg_common.py` and is called from both index-build and citation-resolve paths.
4. `resolve_kov_citation()` checks `kov_collision_keys` on both primary and `year + 1` alternate; ambiguous on alternate returns `ambiguous_key`.
5. KOV-side stale `interpretedBy` cleanup walks all KOV peeps each run.
6. SHACL: descriptions widened to triple-quoted Turtle literals; new `interpretedBy` property shape on `MunicipalRegulationShape`. No `sh:class` or `sh:or` added.
7. `scripts/generate_court_decisions.py` and the regenerated `krr_outputs/riigikohus/riigikohus_schema.json` carry the `owl:unionOf` widened range on `interpretsLaw`. `interpretedBy` gains no new RDFS range.
8. Coverage report at `krr_outputs/reports/kov/court_provision_links_coverage.json` matches the field shape: `files_processed_kov ⊆ files_processed`, `files_with_output_kov ⊆ files_with_output`, `triples_emitted_kov` counts only KOV act IRI targets.
9. RT IV citations counted under `skip_reasons["rtiv_form_citation"]`; expected zero on current data.
10. Two-runs-byte-identical test passes; SHACL targeted test passes; `pytest tests/test_extract_court_provision_links.py` passes in full.
11. Full integration run on real data succeeds end-to-end. Coverage report shows `triples_emitted_kov ≥ 1` (the empirical baseline guarantees 5 resolvable citations per the table in the "Empirical baseline" section). The PR is acceptable as long as `triples_emitted_kov ≥ 1`; specific named examples are illustrative, not normative — corpus changes between PR drafting and merge could shift exact counts.
12. `validate_all.py` (or project SHACL validator entry point) clean over the full integrated graph.

## Risk and mitigations

- **Risk: regex over-fires on case names.** Mitigated by structured form requirement (`{Body} {date} määrus nr {N}`); tested in `test_pat_kov_act_no_match_case_name`. Empirical false-positive rate on full corpus: 0 (verified).
- **Risk: false-positive resolution from year-tolerance.** Year tolerance is asymmetric (`+1` only) and bounded. Strict `(issuer, year, num)` match means three independent dimensions must align. Empirical risk surface: 35 citations × 2 candidate years (primary + `+1` alternate) over an index keyed on a 357-issuer × ~30-year × small-act-number space. Combined with the `known_issuer_norms` precondition, accidental collision with a current peep is vanishingly unlikely; verified empirically (1 ambiguous case, 5 confidently-resolved cases, no observed false positives).
- **Risk: SHACL widening breaks existing CI.** Description-only updates plus a single new property shape on a class that already exists. No type-class additions. Existing law-only court decisions unchanged. Targeted SHACL test pins the new behavior.
- **Risk: KOV cleanup walk slows runs.** Walk #3 reads all 11k KOV peeps but writes only on hit. Existing walk #2 reads all state peeps with the same shape. Wall-time delta expected: a few seconds added to a multi-minute integration run.
- **Risk: collision detection misses a collision.** `build_kov_act_index()` checks `key in kov_collision_keys` before any other action, then `key in kov_index` and compares IRIs. Two acts with the same key in the same input set always collide; collisions across runs (where one act was deleted between runs) aren't a real concern because index is rebuilt per run.

## Out of scope

This PR does NOT:

- Add an RT IV reference resolver. Counted but not resolved (zero observed in current court summaries; no explicit parsed RT IV reference field on KOV peeps and no prebuilt RT IV → IRI lookup table). A future PR can build the lookup table by parsing `dcterms:source` URLs / `estleg:globalId` values on KOV act nodes; that work plus the resolver itself is its own PR.
- Resolve KOV citations to provision granularity. KOV provision IRIs (`KovProvision` nodes) are currently not exposed via stable abbreviation prefixes. Provision-level KOV resolution requires a separate design (likely needing `p 4.6` sub-point parsing and per-act point indexing).
- Touch the `iter_peep_files()` default flip (already done in Layer 2a).
- Change `generate_similarity_index`'s `include_kov=False` pin (Gate C territory).
- Add `rdfs:range` to `estleg:interpretedBy`. (Today none; adding now would invent a constraint not previously present.)
- Add per-authority court-decision attribution (which court level, which jurisdiction) — separate concern.
- Backfill historical KOV Issuer entities (Layer 1 deliberately keeps `historicalMunicipalityName` as a string; defunct issuers don't get IRIs).

## Gate B umbrella status (after this PR lands)

- 2a COMPLETE (#98)
- 2b COMPLETE (#99)
- 2c PR #1 sanctions: COMPLETE (#100)
- 2c PR #2 institutional-competence: COMPLETE (#101)
- 2c PR #3 court-provision-links: this PR

**Gate B closes when this PR lands.** All ten KOV-relevant pipelines run cleanly over the full KOV set with per-pipeline coverage reports.
