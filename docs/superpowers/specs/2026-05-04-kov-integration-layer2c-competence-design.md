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

The Issuer registry becomes the canonical anchor for "municipal
bodies" across the graph: Layer 1's `enactedBy` and Layer 2c PR #2's
`competentAuthority` both target IRIs from the SAME registry when
the act is municipality-scoped. The two IRIs coincide on Path 1
(same body type — the act's enactedBy IS the competent authority,
e.g. a vallavolikogu act citing "vallavolikogu kehtestab korra").
On Path 2 they intentionally differ but stay within the same place's
issuer family — e.g. a vallavolikogu act citing "vallavalitsus"
emits `competentAuthority → Issuer_<place>_vallavalitsus` (the
paired executive body of the same municipality), not the source's
own enactedBy IRI.

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
   the source act has `estleg:enactedByMunicipality` set AND the
   resolver returns exactly one Issuer match (Path 1 same-body-and-suffix,
   Path 2 paired-body-and-family, or Path 3 unique fallback), the
   `competentAuthority` triple targets that `estleg:Issuer_*` IRI.
   Lookup re-uses Layer 2b's `build_issuer_registry`. The resolver
   intentionally abstains in several cases (suffix mismatch,
   cross-family swap, paired-body absent, Path 3 ambiguous or empty);
   abstain accounting is described in invariant #9.
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
   that abstained for any of the following reasons:
   (a) source act has no `enactedByMunicipality` (state-law side);
   (b) source_issuer authoritative + Path 1 suffix mismatch
       (e.g. vallavolikogu source + `linnavolikogu` body word);
   (c) source_issuer authoritative + Path 2 paired-body issuer
       absent or in different municipality;
   (d) source_issuer authoritative + Path 2 cross-family swap
       rejected (e.g. linnavalitsus source + vallavolikogu body word);
   (e) Path 3 fallback finds zero matches under
       (source_municipality, body_type, suffix);
   (f) Path 3 fallback finds more than one matching issuer
       (ambiguous; abstains rather than guesses).
   `fallback_hits` reports the count of SUCCESSFUL Path 3
   resolutions — cases where source_issuer was missing, unknown
   to the registry, or had a `currentMunicipality` inconsistent
   with the act's `enactedByMunicipality`, and Path 3 nonetheless
   resolved to a unique suffix-compatible Issuer in the act's
   stated municipality. A high `fallback_hits` count is a
   diagnostic signal worth investigating in a Layer 1 follow-up.
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
(re.compile(r"\b(?:linna|valla)valitsus(?:e(?:le|sse)?|es|se|t)?\b", re.IGNORECASE),
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
    match doesn't fit one of the known stems.
    """
    s = matched.lower()
    # Strip trailing case suffixes greedily from longest to shortest
    # so 'vallavalitsusele' → 'vallavalitsus' (matches inner 'le' off
    # 'e' tail) and 'vallavalitsuses' → 'vallavalitsus' (matches 'es'
    # directly off the stem).
    #
    # Order matters: longer suffixes must be tried first, otherwise
    # 'vallavalitsusele' would match 'le' first and stop at
    # 'vallavalitsuse', which then matches 'e' and yields
    # 'vallavalitsus' — same end state but indirect. Direct
    # ordering is just clearer.
    for suffix in ("esse", "ele", "sse", "es", "le", "se", "ses", "s", "t", "e"):
        if s.endswith(suffix) and len(s) > len(suffix) + 4:
            stripped = s[: -len(suffix)]
            if stripped.endswith(("volikogu", "valitsus")):
                s = stripped
                break
    # Validate against the FIVE supported canonical forms
    # (_CANONICAL_BODY_SLUGS, defined above this function).
    # `alevivalitsus` is intentionally excluded: small towns
    # (alevi) commonly have a volikogu but rarely a separately-
    # named executive; the GENERIC_PATTERNS regex doesn't match
    # `alevivalitsus` and the resolver's body_type_map doesn't
    # include it. Listing only the five reachable slugs keeps
    # the helper consistent with the rest of the pipeline (so
    # direct unit calls return None for `alevivalitsus` rather
    # than emitting an unreachable slug).
    if s in _CANONICAL_BODY_SLUGS:
        return s
    return None
```

The slug returned by `_canonical_body_slug` is what the resolver
keys on. If `None`, the detection is skipped entirely (defensive
— keeps regex over-match noise out of the institutions output).

### Resolution — `_resolve_kov_authority`

New module-level helper, called from `main()` after detection but
before the fall-through `Institution_*` emission. **Critical:** a
naive `(municipality, body_type)` lookup abstains on the majority
of real KOV cases — the corpus has 77 ambiguous buckets covering
276 of 357 issuers (77%), mostly because of pre-2017 haldusreform
mergers where multiple historical issuers map to the same
`currentMunicipality`. The resolver MUST use the source act's
`enactedBy` issuer as the primary disambiguator and only fall
back to municipality-wide uniqueness when the source-issuer path
yields no answer.

```python
def _resolve_kov_authority(
    body_slug: str,
    source_municipality: str | None,
    source_issuer: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
) -> str | None:
    """Resolve a KOV body-word detection to the matching
    estleg:Issuer_* IRI for the source act.

    Resolution strategy (in priority order):

    1. **Same body type AND slug suffix match source_issuer** —
       if the body word's body type equals the source issuer's
       body type AND `source_issuer.endswith("_" + body_slug)`,
       return source_issuer directly. Trivial; no registry walk
       needed. Example: source act enacted by
       `Issuer_abja_vallavolikogu` (body=volikogu); body word
       `vallavolikogu` (body=volikogu, slug matches the issuer's
       trailing `_vallavolikogu`) → return
       `Issuer_abja_vallavolikogu`.
       Counter-example: same source act, but body word
       `linnavolikogu`. Same body type (volikogu), but suffix
       mismatch (`_vallavolikogu` ≠ `_linnavolikogu`) — the body
       word literally says "town/city council" which doesn't
       apply to a rural municipality act. Resolver abstains
       (returns None) WITHOUT consulting Path 3.

    2. **Paired body in the same source-issuer slug family** — if
       the body type DIFFERS from source_issuer's, derive the
       paired issuer slug by swapping the body suffix in
       source_issuer's name (e.g. `Issuer_abja_vallavolikogu`
       → `Issuer_abja_vallavalitsus`). Look up the paired slug in
       the registry. If present AND its municipality matches the
       source act, return it. If the paired entry is absent or
       has a different municipality, abstain (returns None)
       WITHOUT consulting Path 3 — source identity is
       authoritative.

    3. **Municipality-wide uniqueness fallback** — reached ONLY
       when source_issuer is missing, unknown to the registry,
       or has a `currentMunicipality` that disagrees with the
       act's `enactedByMunicipality` (Layer 1 data inconsistency).
       Look up by (source_municipality, body_type, suffix) and
       return the only match. Abstain on zero or ambiguous matches.

    Args:
        body_slug: canonical body word from _canonical_body_slug.
            Expected: "linnavolikogu", "vallavolikogu",
            "linnavalitsus", "vallavalitsus", "alevivolikogu".
        source_municipality: the @id STRING of the source act's
            estleg:enactedByMunicipality. The caller MUST unwrap
            the JSON-LD object form via _id_ref. None when the
            source is a Law or state regulation — the resolver
            returns None (abstain rule).
        source_issuer: the @id STRING of the source act's
            estleg:enactedBy. The caller MUST unwrap with _id_ref.
            None when the source act is non-KOV; the resolver then
            falls through to step 3 only.
        issuer_registry: issuer @id → (label_normalized,
            municipality_iri, body_type) from Layer 2b's
            build_issuer_registry.

    Returns:
        The resolved Issuer @id, or None when no path applies.
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

    # Path 1+2: source_issuer-based resolution.
    #
    # When source_issuer is KNOWN AND VALID (present in the
    # registry, with municipality matching the act's stated
    # municipality), the source-issuer-based paths are AUTHORITATIVE.
    # If they don't yield a binding (suffix mismatch on Path 1,
    # paired-body absent on Path 2), the resolver MUST ABSTAIN —
    # NOT fall through to Path 3.
    #
    # Why: Path 3's "unique suffix-compatible issuer in the same
    # current municipality" rule conflates pre-haldusreform
    # historical issuers under their merged successor. A rural
    # `Issuer_ridala_vallavolikogu` source act detecting
    # `linnavalitsus` body word MUST NOT bind to
    # `Issuer_haapsalu_linnavalitsus` just because both currently
    # share the post-2017 Haapsalu municipality. The source
    # issuer's identity says this is a rural-municipality act;
    # `linnavalitsus` (city executive) doesn't apply. Abstain.
    #
    # Path 3 is reserved for cases where source_issuer is missing,
    # unknown to the registry, or inconsistent with the act's
    # municipality (Layer 1 data bug). In those cases Path 3 acts
    # as a best-effort rescue using the act's stated municipality.
    source_issuer_authoritative = False
    if source_issuer is not None:
        source_entry = issuer_registry.get(source_issuer)
        if source_entry is not None:
            _label, source_mun, source_body_type = source_entry
            if source_mun == source_municipality:
                # Source issuer is known and consistent with the
                # act's enactedByMunicipality. Treat its identity
                # as authoritative — Path 1 or Path 2 may resolve;
                # if neither does, abstain WITHOUT consulting Path 3.
                source_issuer_authoritative = True

                if (source_body_type == target_body_type
                        and source_issuer.endswith("_" + body_slug)):
                    # Path 1: same body type AND slug suffix matches.
                    return source_issuer

                if source_body_type != target_body_type:
                    # Path 2: opposite body type → derive paired slug.
                    # _swap_issuer_body_suffix produces an IRI whose
                    # slug ends with "_<body_slug>" by construction,
                    # so suffix compatibility is guaranteed.
                    paired_iri = _swap_issuer_body_suffix(source_issuer, body_slug)
                    if paired_iri is not None and paired_iri in issuer_registry:
                        paired_entry = issuer_registry.get(paired_iri)
                        if paired_entry is not None and paired_entry[1] == source_municipality:
                            return paired_iri
                    # Paired issuer absent or in different municipality.
                    # source_issuer is authoritative — abstain.
                    return None

                # Same body type but suffix mismatch
                # (e.g. linnavolikogu source + vallavolikogu body word).
                # Source identity says no — abstain.
                return None
            # else: source_mun != source_municipality. Layer 1
            # mapping inconsistency. source_issuer NOT authoritative;
            # let Path 3 try with the act's stated municipality.
        # else: source_issuer not in registry. Stale or unknown
        # entry; let Path 3 try.

    # Path 3: municipality-wide uniqueness with suffix compatibility.
    # Reached only when source_issuer is absent / unknown / has
    # inconsistent municipality. Filter by both body_type AND
    # suffix so the resolver can't conflate vallavolikogu and
    # linnavolikogu in a same-municipality scan.
    if source_issuer_authoritative:
        # Defensive: should be unreachable, but guards against
        # someone reordering the function later.
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
    return None  # zero or ambiguous


def _swap_issuer_body_suffix(
    source_issuer: str, target_body_slug: str
) -> str | None:
    """Given an Issuer @id like 'estleg:Issuer_abja_vallavolikogu'
    and a target body slug like 'vallavalitsus', return the
    paired Issuer @id 'estleg:Issuer_abja_vallavalitsus'.

    Returns None when:
    - The source IRI doesn't match the expected
      `Issuer_<place>_<body>` shape.
    - The source body and target body belong to DIFFERENT
      municipal families. Estonian KOV bodies group into three
      mutually-exclusive families:
          linna*  (city / town body — linnavolikogu, linnavalitsus)
          valla*  (rural municipality body — vallavolikogu, vallavalitsus)
          alevi*  (small-town body — alevivolikogu)
      A `linnavalitsus` source must NOT pair with `vallavolikogu`
      target even if `Issuer_<place>_vallavolikogu` exists in the
      registry — that's the same historical-conflation problem
      Path 1's suffix check rejects. Cross-family swaps abstain.
    """
    if not source_issuer.startswith("estleg:Issuer_"):
        return None
    suffix = source_issuer[len("estleg:Issuer_"):]
    # The body slug is the last "_<body>" segment.
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

    # Family-prefix compatibility check.
    family_prefixes = ("linna", "valla", "alevi")
    source_family = next(
        (p for p in family_prefixes if source_body.startswith(p)), None)
    target_family = next(
        (p for p in family_prefixes if target_body_slug.startswith(p)), None)
    if source_family is None or target_family is None:
        return None
    if source_family != target_family:
        # Cross-family swap (e.g. linnavalitsus → vallavolikogu).
        # Reject — the body word's municipal family doesn't match
        # the source act's institutional family.
        return None

    return f"estleg:Issuer_{place}_{target_body_slug}"
```

Why this matters: the registry has 77 ambiguous
`(currentMunicipality, body_type)` buckets covering 276 of 357
issuers — pre-2017 haldusreform mergers where multiple historical
issuers all map to the same successor municipality. Without the
source-issuer path, the resolver would abstain on 77% of KOV
cases. The source-issuer path uses the act's own historical
identity (which Layer 1 already resolved correctly) as the
primary anchor.

The "exactly one match" rule on Path 3 is consistent with Layer 2b's
KOV body-text resolver in `extract_cross_references.py`. Ambiguity
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
    # detections; on resolver None, ABSTAIN (no Institution_*
    # fallback for body-word detections). Other institution
    # types (named institutions, ministries, courts, generic
    # local_government / kohalik_omavalitsus) follow the
    # original Institution_* path unchanged.
    if itype == "local_government_body":
        issuer_iri = _resolve_kov_authority(
            body_slug=iri_suffix,
            source_municipality=source_municipality,
            source_issuer=source_issuer,
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
source_issuer = _id_ref(act_node.get("estleg:enactedBy"))
# source_municipality and source_issuer are STRING IRIs or None.
# Both are passed to the resolver; source_issuer enables the
# 1+2 priority paths (same-body or paired-body lookup) that
# work in ambiguous-bucket cases. source_municipality is required
# for ALL resolver paths: Path 1 uses it to verify the source
# issuer's currentMunicipality is consistent with the act's
# enactedByMunicipality; Path 2 uses it to verify the paired
# issuer is in the same municipality; Path 3 uses it as the
# scope filter for the unique-suffix-compatible-match rule.
# When source_municipality is None (Law / state regulation),
# the resolver returns None across every path — abstain.
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
abstained for any reason: source non-KOV (no
`enactedByMunicipality`); source_issuer authoritative but
suffix-mismatched on Path 1 (e.g. `vallavolikogu` source +
`linnavolikogu` body word); source_issuer authoritative but
paired-body issuer absent or in different municipality on
Path 2; source_issuer authoritative but cross-family swap on
Path 2 (e.g. `linnavalitsus` source + `vallavolikogu` body word);
Path 3 fallback found zero matches; Path 3 fallback found more
than one match (ambiguous). Useful for sniffing under-extraction.

`fallback_hits` reports SUCCESSFUL Path 3 fallback resolutions —
cases where source_issuer was missing, unknown to the registry,
or had a `currentMunicipality` inconsistent with the act's
`enactedByMunicipality`, and Path 3 nonetheless found a unique
suffix-compatible match in the act's stated municipality. A high
`fallback_hits` count is a diagnostic signal: it surfaces real
Layer 1 mapping inconsistencies that would otherwise be invisible
(the resolver still produces output for these acts, but a
Layer 1 follow-up may be warranted).

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
| `scripts/extract_institutional_competence.py` | Remove `include_kov=False` pin (line 239); add 2 new `GENERIC_PATTERNS` entries for KOV body words (placed before `vald|linn`); add `_find_act_node` helper (or import from `extract_sanctions` — verify during implementation); add `_resolve_kov_authority` resolver; add `issuer_registry` build at startup (re-uses Layer 2b's `build_issuer_registry`); replace global `_clear_existing_institutions` with per-file in-memory processing; wire body-word matches through the resolver, ABSTAINING when the resolver returns None (no `Institution_*` fallback for `local_government_body` detections); other institution types (named institutions, ministries, courts, generic `local_government` / `kohalik_omavalitsus`) keep the existing `Institution_*` path unchanged; skip `inst_data` entry when resolver succeeds; track `pre_existing_institution_files` for end-of-run deletion of orphaned files; add coverage instrumentation reusing `kov_pipeline_coverage.CoverageReport`; change `main()` to return `int`; use `raise SystemExit(main())`. |
| `tests/test_extract_institutional_competence.py` | **New file.** 30 tests across 7 classes (3 detection unit + 13 resolver unit + 3 integration + 3 idempotency + 3 institutions-file deletion + 4 coverage + 1 corpus-wide invariant). |
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
- New SHACL **shapes** OR new property constraints — the
  `competentAuthority` constraint already exists at lines 140-145
  of `shacl/estonian_legal_shapes.ttl` with `sh:nodeKind sh:IRI`;
  this PR only updates the existing constraint's `sh:description`
  to mention the broader Issuer/Institution range.

## Test plan

**7 new test classes, 30 tests total.** Files: `tests/test_extract_institutional_competence.py`.

Class breakdown (3 + 13 + 3 + 3 + 3 + 4 + 1 = 30):
- `TestKovBodyWordDetection` — 3 unit tests
- `TestResolveKovAuthority` — 13 unit tests (covers all 3 resolver priority paths + every abstain condition including the haldusreform-merger safety property and family-prefix compatibility; plus the `_is_path3_case` predicate AND a parity test against the resolver to prevent future drift)
- `TestExtractCompetenceWithIssuerBinding` — 3 integration tests
- `TestCompetenceIdempotency` — 3 integration tests
- `TestStaleInstitutionFileDeletion` — 3 integration tests
- `TestCompetenceCoverageReport` — 4 integration tests (includes the `fallback_hits` Path 3 diagnostic test)
- `TestCorpusInvariant` — 1 corpus-wide test (`@pytest.mark.slow`)

### `TestKovBodyWordDetection` (unit, 3 tests)

- `test_linnavolikogu_matches_basic_form` — text `"linnavolikogu kehtestab korra"` detected with normalised slug `linnavolikogu`. Parametrized cases include `linnavolikogus` (inessive), `linnavolikogule` (allative), `linnavolikogusse` (illative).
- `test_vallavalitsus_inflections` — parametrized over seven singular case forms each: `["vallavalitsus", "vallavalitsuse", "vallavalitsust", "vallavalitsusele", "vallavalitsuses", "vallavalitsusesse", "vallavalitsusse"]` and `["linnavalitsus", "linnavalitsuse", "linnavalitsust", "linnavalitsusele", "linnavalitsuses", "linnavalitsusesse", "linnavalitsusse"]`. Each case asserts canonicalisation to `vallavalitsus` / `linnavalitsus` respectively. **Critical:** the inessive forms `vallavalitsuses` and `linnavalitsuses` AND the short-illative forms `vallavalitsusse` and `linnavalitsusse` MUST be in the parametrization — earlier-version regex missed them; the regex+canonicalizer combination must catch them now. **Out of scope:** plural forms (`linnavalitsuste`, `valitsustele`, etc.) — rare in legal text and not handled by this PR's regex/canonicalizer; tracked as a follow-up if corpus data shows they matter.
- `test_named_institutions_still_win` (regression — ensure new patterns don't override existing named-institution detections)

### `TestResolveKovAuthority` (unit, 13 tests)

The resolver has three priority paths (same-body source-issuer
direct, paired-body slug swap, municipality-wide uniqueness
fallback). The test set covers each path's success case AND the
abstain conditions added per Round 2 review (suffix mismatch,
municipality mismatch, ambiguous fallback).

- `test_path1_source_issuer_same_body_resolves_directly` — source
  act enacted by `Issuer_abja_vallavolikogu` (body=volikogu);
  detected body word `vallavolikogu` (body=volikogu). Resolver
  returns `Issuer_abja_vallavolikogu` directly (Path 1; no
  registry walk needed). Critical even when the
  `(municipality, body_type)` bucket is ambiguous.
- `test_path2_paired_body_slug_swap` — source act enacted by
  `Issuer_abja_vallavolikogu`; detected body word
  `vallavalitsus`. Registry contains the paired
  `Issuer_abja_vallavalitsus` under the same municipality.
  Resolver returns `Issuer_abja_vallavalitsus` (Path 2 succeeds
  via `_swap_issuer_body_suffix`).
- `test_path2_paired_body_missing_abstains_without_path3` —
  source act enacted by `Issuer_xyz_vallavolikogu`; detected
  body word `vallavalitsus` (SAME municipal family — valla*).
  Registry has NO `Issuer_xyz_vallavalitsus` (the paired rural
  executive doesn't exist) BUT has another
  `Issuer_other_vallavalitsus` under the same modern
  municipality. Resolver returns None — source identity is
  authoritative on Path 2; Path 3 must NOT bind to a conflated
  historical issuer in the same successor municipality. This
  locks in the haldusreform-merger safety property and is a
  TRUE Path-2-paired-body-missing test (same family — passes
  the `_swap_issuer_body_suffix` family-prefix gate, then fails
  the registry-presence check).
- `test_path2_cross_family_linna_to_valla_abstains` — source
  act enacted by `Issuer_elva_linnavalitsus` (urban executive);
  detected body word `vallavolikogu` (rural council). Even
  if `Issuer_elva_vallavolikogu` were registered (rare but
  possible in the corpus), the swap MUST be rejected:
  `linna*` and `valla*` are mutually exclusive municipal
  families. Resolver returns None.
- `test_path2_cross_family_valla_to_linna_abstains` — source
  act enacted by `Issuer_abja_vallavalitsus` (rural executive);
  detected body word `linnavolikogu` (urban council). Same
  family-prefix rejection. Resolver returns None.
- `test_path1_suffix_mismatch_abstains` — source act enacted by
  `Issuer_abja_vallavolikogu` (body=volikogu); detected body
  word `linnavolikogu` (body=volikogu — same body_type, but
  literally "town/city council" which doesn't apply to a rural
  municipality act). Resolver returns None (Path 1's suffix
  check rejects).
- `test_municipality_mismatch_falls_through_to_path3` — source
  act `enactedByMunicipality: estleg:Municipality_tartu`; source
  issuer's registry entry says it belongs to
  `estleg:Municipality_tallinn` (Layer 1 mapping inconsistency).
  source_issuer is NOT authoritative (municipality mismatch);
  Path 3 then runs against `Municipality_tartu`. The fixture
  registry has exactly one issuer matching the body slug under
  `Municipality_tartu`, so the resolver returns that issuer's
  IRI. Locks in the "Path 1+2 abstain when municipality
  mismatches; Path 3 takes over with the act's stated municipality"
  contract.
- `test_municipality_mismatch_abstains_when_path3_also_empty` —
  same setup but the fixture registry has zero matches under
  `Municipality_tartu`. Resolver returns None.
- `test_path3_unique_fallback_resolves` — source has no
  enactedBy (None); source municipality has exactly one issuer
  matching the body slug. Path 3 returns it.
- `test_path3_ambiguous_returns_none` — Path 1+2 both fall
  through (e.g. source_issuer absent); the source municipality
  has TWO issuers matching the same body slug. Resolver returns
  None.
- `test_no_municipality_returns_none` — `source_municipality=None`;
  every path requires it; resolver returns None.
- `test_is_path3_case_predicate` — companion `_is_path3_case`
  helper (used by `main()` to detect Path 3 firings for
  `fallback_hits` accounting). Parametrized:
  - `source_issuer=None, source_municipality=valid` → True
  - `source_issuer=unknown_to_registry, source_municipality=valid` → True
  - `source_issuer=registered_with_matching_mun` → False
  - `source_issuer=registered_with_mismatched_mun` → True
  - `source_municipality=None` → False (nothing resolves anyway)
- `test_is_path3_case_stays_in_sync_with_resolver` — parity test
  guarding against future drift. The predicate duplicates the
  resolver's `source_issuer_authoritative` check; if either
  diverges, `fallback_hits` accounting silently rots. The test
  iterates a small fixture of synthesised
  `(source_issuer, source_municipality, body_slug, registry)`
  cases covering each authoritative/non-authoritative branch
  AND asserts an invariant: when `_is_path3_case` returns
  False AND the resolver returns a non-None IRI, the IRI MUST
  be either source_issuer (Path 1) or `_swap_issuer_body_suffix`
  output (Path 2) — never a registry-wide unique match. When
  `_is_path3_case` returns True AND the resolver returns a
  non-None IRI, the IRI MUST come from the registry-wide unique
  match (Path 3). This bound on observable behaviour keeps the
  two functions semantically aligned even if either is later
  refactored.

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
- `test_state_law_competence_with_kov_body_word_abstains` —
  Law-typed peep (no `enactedByMunicipality`) with provision text
  `"linnavolikogu kehtestab korra"`. After main(), provision has
  NO `competentAuthority` triple containing a `linnavolikogu`
  reference (neither `Institution_linnavolikogu` nor `Issuer_*`).
  This test only asserts the absence of the binding; the
  unresolved-counter assertion lives in Task 7's
  `TestCompetenceCoverageReport::test_unresolved_count_includes_state_side_kov_body_words`
  (added below) — which DOES have access to the coverage report.
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

### `TestStaleInstitutionFileDeletion` (integration, 3 tests)

- `test_stale_institutions_json_deleted_when_no_provisions_cite_it`
  (fixture stages a stale `institutions/institution_xyz.json`; no
  provisions in the run cite Institution_xyz; rerun deletes the
  file).
- `test_living_institution_files_preserved` — fixture stages a
  pre-existing `institutions/institution_riigikohus.json` AND a
  peep whose summary contains "Riigikohus otsustab"; rerun MUST
  PRESERVE the file (not delete it). Locks in the
  `path.stem.removeprefix("institution_")` comparison: a naive
  `path.stem not in written_slugs` would compare
  `"institution_riigikohus"` to `"riigikohus"` and incorrectly
  delete the legitimate file.
- `test_deletion_skipped_when_per_peep_errors_present` — fixture
  stages a pre-existing institution file + a peep with malformed
  JSON (loader returns `None`). Rerun completes but does NOT
  delete the institution file (deletion is gated on
  `_per_peep_errors == 0`). Asserts both: file still present,
  log line "[skip stale-deletion]" emitted.

### `TestCompetenceCoverageReport` (integration, 4 tests)

- `test_coverage_report_written_with_kov_split` (1 KOV act
  + 1 state law → JSON has expected splits).
- `test_gate_fails_when_kov_input_nontrivial_but_no_output` (same
  triple-monkeypatch trick from PR #1 v5 — `iter_peep_files` +
  `load_json` + `save_json`; 11,001 synthetic KOV paths with
  non-detecting content; `rc == 1`).
- `test_unresolved_count_includes_state_side_kov_body_words` —
  fixture has 1 KOV act with body-word + 1 state law with the
  SAME body-word "linnavolikogu kehtestab korra". After main(),
  coverage JSON's `unresolved_references >= 1` (the state-side
  abstain counts). The assertion that Task 2's integration test
  deferred to here.
- `test_fallback_hits_counts_path3_resolutions` — fixture stages
  a KOV peep where the source act's `enactedBy` is missing OR
  references an issuer NOT in the registry, while the act's
  `enactedByMunicipality` IS valid and the registry has exactly
  one suffix-compatible match in that municipality (Path 3
  rescue case). The provision text includes a body-word match.
  After main(), the coverage JSON's `fallback_hits >= 1`. Locks
  in the diagnostic counter so Layer 1 mapping problems surface
  in coverage reports rather than going silent.

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
   `TestResolveKovAuthority` 13 = 16 tests).
2. Wire `_resolve_kov_authority` into `main()`'s per-provision
   loop + integration tests
   (`TestExtractCompetenceWithIssuerBinding`, 3 tests). Introduce
   TWO minimal local counters:
   - `_unresolved_count: int = 0` incremented when the resolver
     returns None for a `local_government_body` detection.
   - `_fallback_hits: int = 0` incremented when the resolver
     returns a non-None result AND Path 3 is the path that
     produced it.

   To track Path 3 firings without changing the resolver's
   `str | None` signature, add a small predicate helper:

   ```python
   def _is_path3_case(
       source_issuer: str | None,
       source_municipality: str | None,
       issuer_registry: dict[str, tuple[str, str, str]],
   ) -> bool:
       """Return True iff the resolver's Path 1+2 are not
       authoritative for this case — i.e. Path 3 is what would run.
       Mirrors the resolver's source_issuer_authoritative check."""
       if source_municipality is None:
           return False  # nothing resolves anyway
       if source_issuer is None:
           return True
       entry = issuer_registry.get(source_issuer)
       if entry is None:
           return True
       _label, source_mun, _btype = entry
       return source_mun != source_municipality
   ```

   Caller pattern in `main()`:

   ```python
   issuer_iri = _resolve_kov_authority(...)
   if issuer_iri is None:
       _unresolved_count += 1
       continue  # body-word abstain
   if _is_path3_case(source_issuer, source_municipality, issuer_registry):
       _fallback_hits += 1
   authority_refs.append({"@id": issuer_iri})
   ```

   Both counters have no observable surface yet (no coverage
   report); Task 7 wires `_unresolved_count` into
   `unresolved_references` and `_fallback_hits` into
   `fallback_hits` when CoverageReport is added. The
   `_is_path3_case` predicate becomes another testable unit
   (one test in TestResolveKovAuthority — see below).
3. Refactor to per-file in-memory processing + provision-level
   idempotency (`TestCompetenceIdempotency`, 3 tests).
4. Stale `institutions/<slug>.json` deletion when no provisions
   cite the slug after re-run + minimal `_per_peep_errors`
   counter for the deletion guard (`TestStaleInstitutionFileDeletion`,
   3 tests: deletion-fires, living-files-preserved, deletion-skipped-on-error).
5. UPDATE the existing SHACL `competentAuthority` constraint's
   description (the `sh:nodeKind sh:IRI` shape already exists at
   lines 140-145; this commit only refreshes the `sh:description`
   to reflect the broader value range).
6. Unpin: remove `include_kov=False` from `iter_peep_files` call.
7. Coverage instrumentation reusing `kov_pipeline_coverage` +
   `main() -> int` + `SystemExit` (`TestCompetenceCoverageReport`,
   4 tests: `test_coverage_report_written_with_kov_split`,
   `test_gate_fails_when_kov_input_nontrivial_but_no_output`,
   `test_unresolved_count_includes_state_side_kov_body_words`,
   `test_fallback_hits_counts_path3_resolutions`). The
   `_unresolved_count` and `_fallback_hits` counters introduced
   in Task 2 are wired into `unresolved_references` and
   `fallback_hits` respectively when the CoverageReport is
   constructed.
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
| 1 | +16 (3 detection + 13 resolver) | 222 |
| 2 | +3 | 225 |
| 3 | +3 | 228 |
| 4 | +3 | 231 |
| 5 | +0 (SHACL) | 231 |
| 6 | +0 (unpin) | 231 |
| 7 | +4 | 235 |
| 8 | +1 | 236 |
| 9 | +0 (docs) | 236 |
| 10 | +0 (verification) | 236 |

**Final expected count: 236 tests (+30 vs Layer 2c PR #1
end-state).**

## Risks and mitigations

**Risk 1: Issuer registry has municipalities that historical KOV
acts referenced under their pre-2017-haldusreform names.**

The Layer 1 `currentMunicipality` field maps historical municipalities
to their successor entities (per the parent spec's Layer 1 design).
A 2010 Tallinn KOV act whose `enactedByMunicipality` is the modern
Tallinn municipality entity should still resolve cleanly through
Path 1 or Path 2 because source_issuer and the act's
enactedByMunicipality both come from Layer 1's coordinated mapping.

Behavior when Layer 1 itself has an inconsistent entry (the act's
`enactedByMunicipality` disagrees with the source issuer's
`currentMunicipality` in the registry):

- source_issuer is NOT treated as authoritative for this act
  (the consistency check fails).
- The resolver falls through to Path 3, which scans the registry
  for any issuer matching `(act.enactedByMunicipality, body_type,
  body_slug)`. If exactly one matches, the resolver returns it
  (best-effort rescue using the act's stated municipality);
  ambiguous or zero matches abstain.
- Path 3 successes are counted in `CoverageReport.fallback_hits`,
  giving operators visibility into how often the rescue fires.
  A high `fallback_hits` count signals a real Layer 1 mapping
  problem worth investigating in a follow-up; the pipeline still
  produces useful output during that investigation.

This is a documented divergence from "abstain on bad mapping" —
chosen because the act's stated municipality plus the body slug
plus suffix compatibility plus uniqueness is, in practice, a
strong-enough signal to bind correctly. See `TestResolveKovAuthority::test_municipality_mismatch_falls_through_to_path3`
for the success case and `test_municipality_mismatch_abstains_when_path3_also_empty`
for the abstain case.

**Risk 2: Detection regex over-matches on word boundaries.**

The new patterns use `\b...\b` boundaries and require the full
body word (no partial matching). The inflection group
`(?:e(?:le|sse)?|es|t)?` for `valitsus` is the actual proposed
form; it greedily tries longer alternatives first
(`esse` > `ele` > `sse` > `es` > `le` > `e` > `t`) and might
match nominative `valitsus` followed by a hyphen and another
word (e.g. `vallavalitsus-eelnõu` — though that's not real
Estonian).
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
local counter (`_per_peep_errors: int = 0`) used solely to guard
the deletion. **Critical:** the existing `load_json` helper
already swallows `json.JSONDecodeError` and `OSError` internally,
returning `None` for unreadable / malformed peeps without raising.
Wrapping the call in `try/except` around `load_json` would catch
NOTHING and leave `doc is None` cases uncounted. The counter must
therefore increment BOTH on the (effectively-never-raised)
exception path AND when `doc is None` or `"@graph" not in doc`:

```python
# Inside the per-peep loop:
doc = load_json(filepath)  # returns None on JSONDecodeError/OSError
if doc is None or "@graph" not in doc:
    _per_peep_errors += 1
    continue

# ... act_node lookup, extraction, save ...
# (Task 7's coverage code adds skip_reasons / failure_samples on
# top of this; the _per_peep_errors counter remains valid as a
# coarse gate.)

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
- Add new SHACL **shapes** or new property constraints — the
  `competentAuthority` constraint already exists at lines 140-145
  with `sh:nodeKind sh:IRI`; this PR only updates the existing
  property constraint's `sh:description`.
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
