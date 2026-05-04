# Estonian Regulations Integration Plan

> **Status (2026-05-01):** Phase 1 implemented and merged. 3,792 state-level
> regulations are generated under `krr_outputs/regulations/riik/`. Cross-
> reference, inverse-reference, deontic, and temporal pipelines now treat
> regulations as first-class participants via `estleg_common.iter_peep_files()`.
> Phase 2 (KOV regulations) remains opt-in via `--kov`.

> **Status (2026-05-02):** KOV integration Layer 1 (Municipality + Issuer
> entity model) implemented. Layers 2 (pipelines) and 3 (similarity) follow
> as separate PRs. See
> `docs/superpowers/specs/2026-05-02-kov-integration-design.md` and
> `docs/superpowers/plans/2026-05-02-kov-integration-layer1.md`.

> **Status (2026-05-03):** KOV integration Layer 2a (foundation +
> discovery fixes) implemented. 5 pipelines (deontic, EuroVoc, temporal,
> legal-concepts, amendment-history) now process KOV files; the iterator
> default flips to `include_kov=True`; 9 deferred/irrelevant pipelines pin
> `include_kov=False`. Layer 2b (cross-references + inverse) and 2c
> (semantic resolvers) follow as separate PRs. Gate B = umbrella milestone
> reached when 2a + 2b + 2c have all landed. See
> `docs/superpowers/plans/2026-05-03-kov-integration-layer2a.md`.

> **Status (2026-05-04):** KOV integration Layer 2b (cross-references +
> inverse projection) implemented. `extract_cross_references` and
> `generate_inverse_references` run KOV by default; the three remaining
> `include_kov=False` pins are removed. Preamble citations resolve into
> `estleg:issuedUnder` + reified `estleg:Citation` (with
> `citationTarget` / `citationDetail` / `citationText`); KOV body-text
> act references are scoped by `enactedByMunicipality`;
> `estleg:implementedBy` / `estleg:implementedByCount` are emitted as a
> filtered inverse projection (preamble-only — body-text refs continue
> to use `estleg:referencedBy`). Gate B umbrella status: **2/3 sub-PRs
> merged** (2a + 2b). Layer 2c (sanctions, competence,
> court-provision-links) remains. See
> `docs/superpowers/plans/2026-05-03-kov-integration-layer2b.md`.

### Gate B — Layer 2b: Cross-references + Inverse (COMPLETE)

- **Date completed:** 2026-05-04
- **PR:** (created in Task 14)
- **Branch:** `feature/kov-layer2b`

**Deliverables:**
- `extract_cross_references.py` emits `estleg:issuedUnder`,
  `estleg:implementsCitation`, and reified `estleg:Citation` nodes from
  every parseable preamble (laws, state regulations, KOV acts).
- `extract_cross_references.py` resolves KOV body-text act references
  scoped to the source municipality — cross-municipality false
  positives are explicitly skipped via `enactedByMunicipality`.
- `generate_inverse_references.py` emits
  `estleg:implementedBy` / `estleg:implementedByCount` as a filtered
  inverse projection (preamble citations only — body-text references
  are excluded; the existing `estleg:referencedBy` inverse continues to
  cover those).
- `generate_inverse_references.py` indexes act-level nodes in
  `iri_to_file` so KOV body-text references targeting act IRIs gain
  `referencedBy` correctly (a Layer 2a-era latent bug surfaced once
  KOV joined).
- Both pipelines run KOV by default (3 `include_kov=False` pins
  removed in Tasks 8 and 11).
- Per-pipeline coverage reports under `krr_outputs/reports/kov/`.

**Coverage (2026-05-04 corpus run, post chained-§ + idempotency fix):**

| Metric | extract_cross_references | generate_inverse_references |
| --- | --- | --- |
| Input files (total / KOV) | 15,508 / 11,059 | — |
| Files with output (total / KOV) | 9,648 / 8,837 (80% of KOV) | 921 / 511 (KOV) |
| Triples emitted (total / KOV) | 32,413 / 24,020 | 4,057 / 512 |
| Preamble citations resolved | 13,618 / 27,299 found (50%) | — |
| Preamble triples (total / KOV) | 23,618 / 22,783 | — |
| `implementedBy` edges | — | 21,256 source links across 882 target nodes in 604 files |
| `referencedBy` edges | — | 3,175 across 346 files |

Inverse-pass invariants: 0 stale `implementedBy` left after the
idempotency clear; 0 symmetry mismatches; 0 unresolved target IRIs.

The fix-rerun improved over the initial run by +1,918 chained-paragraph
citations (`<law> § 6 lg 3 p 2 ja § 22 lg 1 p 34` style references the
v1 parser missed) — see PR #99 commits `b6614e75` (code) and
`6d9e533f` (corpus).

**Gate B umbrella status:** 2/3 sub-PRs merged (Layer 2a + Layer 2b).
Layer 2c (sanctions, competence, court-provision-links) is the
remaining piece.

## Finding

The missing domestic **maarus / maarused** are in Riigi Teataja, not in a separate source. The current ontology pipeline already uses the right Riigi Teataja API, but `scripts/generate_all_laws.py` hard-codes `dokument=seadus`. The same endpoint supports `dokument=määrus`.

Official sources:

- Riigi Teataja API FAQ: <https://www.riigiteataja.ee/kkk.html>
- Riigi Teataja bulk XML open data: <https://www.riigiteataja.ee/avaandmed/ERT/>
- KOV regulation classification: <https://www.riigiteataja.ee/jaotused.html?jaotus=KOV>

Useful API shapes:

```text
# Current state-level / non-KOV regulations
https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi?leht=1&limiit=500&dokument=m%C3%A4%C3%A4rus&tekst=terviktekst&kehtiv=YYYY-MM-DD&kehtivKehtetus=false&mitteJoustunud=false&kov=false

# Current KOV regulations only
https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi?leht=1&limiit=500&dokument=m%C3%A4%C3%A4rus&tekst=terviktekst&kehtiv=YYYY-MM-DD&kehtivKehtetus=false&mitteJoustunud=false&kov=true

# All current regulations, including KOV
# Omit the kov parameter.
https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi?leht=1&limiit=500&dokument=m%C3%A4%C3%A4rus&tekst=terviktekst&kehtiv=YYYY-MM-DD&kehtivKehtetus=false&mitteJoustunud=false
```

The search result includes `globaalID`, `terviktekstID`, `url`, `pealkiri`, `liik`, `valjaandja`, and `kehtivus`. Fetch the XML from `https://www.riigiteataja.ee{url}`, for example `https://www.riigiteataja.ee/akt/191725.xml`.

Control query run for `kehtiv=2026-05-01`:

| Scope | Count |
| --- | ---: |
| State / non-KOV regulations (`kov=false`) | 3,820 |
| KOV regulations (`kov=true`) | 11,087 |
| All current regulations, `kov` omitted | 14,907 |

## Scope

Phase 1 should add **state-level regulations**: Vabariigi Valitsus, ministers, Eesti Pank, and other central issuers. This is the highest-value missing legal layer and a manageable first batch.

Phase 2 should add **KOV regulations**. They are legally real and available from the same API, but they add many near-duplicate titles and local administrative acts, so they need stricter IDs, partitioned output, and optional loading.

Historical redactions should stay out of the first pass. Generate the current snapshot using an explicit `kehtiv=YYYY-MM-DD` date for reproducibility. Add historical redaction support later if the ontology needs temporal version graphs.

## Implementation Plan

1. Generalize the Riigi Teataja generator.
   - Extract shared code from `generate_all_laws.py` into `scripts/riigiteataja_common.py`.
   - Add `scripts/generate_regulations.py`, or replace both with `scripts/generate_riigiteataja_acts.py --document seadus|määrus --kov false|true|all`.
   - Keep API pagination at `limiit=500` and cache XML under `data/riigiteataja/maarus/`.

2. Use stable regulation identifiers.
   - Use `terviktekstID` as the stable act/group key.
   - Use `globaalID` as the concrete Riigi Teataja text/redaction key.
   - Avoid title-only IDs because KOV regulations frequently share names like `põhimäärus`, `jäätmehoolduseeskiri`, and `kord`.
   - Suggested provision IRI pattern: `estleg:Reg_{terviktekstID}_Par_{N}`.

3. Extend the schema without breaking current consumers.
   - Add `estleg:NationalRegulation`, `estleg:GovernmentRegulation`, `estleg:MinisterialRegulation`, and `estleg:MunicipalRegulation` classes.
   - Keep regulation provisions as subclasses of `estleg:LegalProvision` so existing SHACL and query patterns still work.
   - Add act-level metadata: `estleg:documentType`, `estleg:issuer`, `estleg:actNumber`, `estleg:globalId`, `estleg:terviktekstId`, `estleg:isKov`, `estleg:entryIntoForce`, `dcterms:source`.

4. Parse both structured and legacy XML.
   - Structured regulation XML uses the same useful tags as laws: `preambul`, `peatykk`, `jagu`, `paragrahv`, `loige`, `tavatekst`.
   - Some older regulation XML stores body text in `HTMLKonteiner` instead of structured `paragrahv` elements. Add an HTML fallback parser that splits sections by `§ N.` headings.
   - Preserve annexes (`lisa`, `lisaViide`, `fail`) as `estleg:Annex` nodes with source links. Do not try to normalize annex tables in the first pass.

5. Model delegation/legal basis.
   - Regulations usually start with a preamble citing the enabling law provision.
   - Extract that preamble as `estleg:preambleText`.
   - Reuse and extend cross-reference parsing to link regulation provisions and preambles to law provisions.
   - Add `estleg:issuedUnder` / `estleg:implementsProvision` from regulation to the enabling law provision, and inverse `estleg:implementedBy`.

6. Keep output partitioned.
   - State regulations: `krr_outputs/regulations/riik/*.json`.
   - KOV regulations: `krr_outputs/regulations/kov/{issuer_slug}/*.json` or a combined shard per issuer.
   - Add indexes: `REGULATIONS_INDEX.json`, `REGULATIONS_RIIK_INDEX.json`, `REGULATIONS_KOV_INDEX.json`.
   - Update integration scripts that currently use only `KRR_DIR.glob("*_peep.json")` to call a shared file iterator when regulations should be included.

7. Integrate in dependency order.
   - Generate regulation JSON-LD.
   - Run validation.
   - Run cross-reference extraction and inverse references with regulations included.
   - Run deontic classification, institutional competence, sanctions, EuroVoc, temporal extraction, and similarity only after file discovery supports nested regulation directories.
   - Add regulation-specific reports: legal-basis coverage, parse-fallback count, annex count, and unresolved references.

8. Test before full import.
   - Add fixtures for one structured state regulation, one legacy `HTMLKonteiner` regulation, and one KOV regulation.
   - Unit-test metadata extraction, paragraph extraction, ID stability, and duplicate-title handling.
   - Dry-run first 20 state regulations, then all 3,820 state regulations.
   - Only then run KOV import.

## Acceptance Criteria

- All current state-level regulations are represented as JSON-LD.
- Every regulation has act-level metadata and provision nodes where the text has paragraphs.
- Legacy HTML-only regulations are not silently skipped.
- No duplicated `@id` values across the ontology.
- At least the regulation preamble legal basis is linked when the enabling law/provision is resolvable.
- README, schema reference, metadata counts, and API guide mention domestic regulations separately from EU regulations.
