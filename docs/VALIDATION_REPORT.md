# Validation Report

**Last updated:** 2026-09-05 (gate run on the Tier 0 tree of epic #676)
**Primary validator:** `scripts/validate_all.py`

## Summary

| Metric | Count |
|--------|-------|
| Files validated | 26,961 |
| Errors | 3,546 |
| Warnings | 2 |
| Result | **FAILED** — see [What the errors are](#what-the-errors-are) |

> **Correction.** Until 2026-09-05 this report said `Errors: 0 / PASSED`
> against a run dated 2026-05-26. That statement was false for every committed
> tree since at least v1.0.0 (2026-08-19): the `json-validation` CI job has
> been red on every `main` run in that period. The numbers above come from a
> local run of the same command on 2026-09-05; the baseline on the pre-Tier-0
> tree (`c96577d50c`) was 26,791 files / 3,558 errors / 2 warnings.

The repository advertises 27,008 generated JSON/JSON-LD files (`metadata.jsonld`
`estleg:totalFiles`). `validate_all.py` excludes generated reports, indexes,
manifests, and probe outputs that are not corpus inputs, which is why it
validates 26,961.

## What the errors are

Every error is itemised below with its status. **3,426 of the 3,546 (96.6%)
are stale validator rules, not data defects**; fixing the validator is Tier 1
ticket #702. The review rerun found 3,546 errors versus 3,547 immediately
before the review fixes. The rebuilt combined file removed its stale-file
finding; the cross-file collision count fell from 66,110 to 66,108. No new
validation error category appeared.

| Count | Finding | Status |
|---:|---|---|
| 3,021 | `dcterms:subject is not an array` on Chapter nodes | Stale rule: chapters carry a single Cluster object by design since the concept layer landed. **#702.** |
| 405 | `dcterms:title must be a string or language-tagged value` on act roots | Stale rule: bilingual title lists are the #437 language-tag policy. **#702.** |
| 38 | Duplicate `@id` within file (37 in `analytical_overlay.jsonld`, 1 in `annotations/oiguskantsler_seisukohad.jsonld`) | Pre-existing; **#702 / #709**. |
| 34 + 5 | `INDEX.json` registry drift on the split codes (AÕS, KarS, TsMS, TsÜS, VÕS): `_osaN` files report 0 act-level nodes; `_map` files have no provision nodes and no registry exception | Pre-existing; the multipart-code registry rules predate `estleg:Part` roots. **#702 / #704.** |
| 27 | `@type is not an array` (26 controlled-vocabulary nodes, 1 in `analytical_overlay.jsonld`) | Pre-existing T-Box shape issue. **#709.** |
| 5 | `skos:exactMatch is not an array` (`NormType_*`) | Pre-existing. **#709.** |
| 3 + 1 + 1 | `eurlex` / `curia` / `eelnoud` combined files: missing source graph IDs in each, one stale CURIA ID, and the draft aggregate older than a canonical source | Stale LFS aggregates that were not rebuilt with the sources. **#705.** |
| 2 | `combined_ontology.jsonld` and `eelnoud_combined.jsonld`: shared provision IDs drift from source on SHACL-sensitive fields (21,802 in the flagship file after the Tier 0 rebuild, 24,792 before; most on split-code `_OsaN` nodes and on classifier fields) | Aggregate-artifact drift; pre-existing (24,792 on the pre-Tier-0 tree). **#705.** |
| 1 | `combined_ontology.jsonld`: 2,409 stub nodes carry `estleg:` object refs outside the shaped closure edges | Pre-existing builder finding. **#416 / #705.** |
| 1 | 66,108 `@id` values duplicated across files | The semantic-collision check counts the same node in an aggregate and in its source; the rule needs the aggregate exemption. **#702.** |
| 1 | Undefined reusable vocabulary terms: 5 predicates | Pre-existing. **#709.** |
| 1 | 78 act-level temporal properties on non-Act nodes | Pre-existing. **#702.** |

Findings removed by Tier 0: the six `metadata.jsonld` count mismatches
(#686), the merged-overlay-absent-from-combined finding and one
stale-aggregate finding on each of the `eurlex` / `curia` / `eelnoud` files
(combined rebuild, #681/#682). `dataset_build_manifest.json` was regenerated so its
`catalogModified` matches the new `dcterms:modified` (#686).

Warnings (2): `generation_manifest_laws.json` is not committed (Tier 1
#692/#704), and 9 amendment chains carry 1.0% duplicate `AmendmentEvent`
nodes (re-run `generate_amendment_history.py`).

## Load surfaces and validation gates

The repository exposes **three distinct load surfaces**, and the validation gate
differs per surface. A reader must never conflate a finding from one surface with
a finding from another: a combined-only SHACL violation is *aggregate-artifact
drift*, not genuine source-data loss, and the correct fix differs accordingly.

`krr_outputs/combined_ontology.jsonld` is the flagship aggregate artifact. It is
designed to be **semantically complete on its own** for every shaped node it
contains: it carries graph-closure stub nodes (marked `estleg:isStubNode`) that
already include the SHACL-required semantic edges for their type, so an app that
loads only the combined file does **not** need to merge in any subdirectory to
satisfy the shapes. An app that follows the broader Seadusloome surface instead
loads the combined file **plus the public subdirectories** and merges nodes by
`@id` using RDF graph-merge semantics (a thin stub in combined is filled in by
its full source node from a subdir).

| Surface | What an app loads | Invariant it guarantees | Gate command | A failure means |
|---------|-------------------|-------------------------|--------------|-----------------|
| **Combined-only** | `krr_outputs/combined_ontology.jsonld` **alone** | The file is semantically complete on its own for every shaped node it contains (graph-closure stubs carry the required semantic edges; no subdir merge needed). | `scripts/validate_combined_standalone.py` | **Aggregate-artifact** drift — the *builder* produced an incomplete combined file. Fix by regenerating combined via `scripts/fix_all_issues.py` (the `generate_combined_jsonld` builder). **Never** relax SHACL. |
| **Source subcorpora alone** | The per-bucket source files (laws, kov, riigikohus, eurlex, curia, drafts, sidecars) | Each source bucket conforms to the shapes on its own (with RDFS inference deriving class membership). | `scripts/shacl_validate_all.py --bucket <name>` (INDEX-driven; `inference='rdfs'`) | Genuine **source-data** missing data in that bucket — the source generator must emit the field. |
| **Seadusloome public load surface** | `combined_ontology.jsonld` **plus** the public subdirectories, merged by `@id` | The published union graph conforms with no SHACL warnings, and sidecar object references resolve (graph closure) — as seen by a consumer that applies **no** inference. | `scripts/validate_seadusloome_sync.py` (union SHACL; `inference='none'`; plus a graph-closure check) | **Public-load-graph** missing data — a field missing from the merged combined-plus-subdirs union as the downstream consumer sees it. |

`scripts/validate_combined_standalone.py` validates the combined artifact
**ALONE** (combined-only surface). It deliberately **skips** the
`estleg:graphClosureExempt` enum predicates — `estleg:caseType`,
`estleg:draftType`, `estleg:legislativePhase`, and `estleg:euDocumentType` —
because those are controlled-vocabulary classifiers published in
`krr_outputs/controlled_vocabulary.jsonld`, not graph edges that must close
inside the combined file. `scripts/validate_seadusloome_sync.py` validates
**"combined plus the public subdirectories"** (the Seadusloome surface), not the
combined file alone; see "Seadusloome Zero-Warning Gate" below for its load set
and CI wiring. The public subdirectories are defined once in
`scripts/estleg_common.py` as `PUBLIC_LOAD_SUBDIRS`: `eelnoud`, `riigikohus`,
`kohtud`, `curia`, `eurlex`, `concepts`, `sanctions`, `amendments`, `institutions`,
`provision_versions`, `annotations`, `harmonisation`, and `regulations`.

> **These semantic fields are not optional noise.** `estleg:partOfAct`, the KOV
> issuer/municipality edges (`estleg:enactedBy`, `estleg:enactedByMunicipality`),
> regulation metadata (`estleg:documentType`, `estleg:terviktekstId`,
> `estleg:titleNormalized`), and `estleg:amendingDraft` are SEMANTIC TRAVERSAL &
> PROVENANCE fields used by the app — they are NOT optional noise, and the
> correct response to a combined-only finding on them is to regenerate combined,
> never to suppress/relax SHACL or downgrade to warnings.

## Checks Performed

1. JSON syntax validity
2. `@context` namespace consistency (`estleg:` -> `https://w3id.org/estleg/`)
3. `@type` is always an array
4. Multi-valued properties are arrays
5. `sectionNumber` is always a string
6. `dc:source` is not an array
7. `xsd:date` value objects use strict `YYYY-MM-DD` literals
8. `estleg:affectedLawName` uses the canonical array-of-strings shape
9. `@id` uniqueness within and across files, excluding known shared class IDs
10. `dcterms:source` uses a resolvable IRI object when present
11. Internal `estleg:` object references resolve to corpus nodes
12. `krr_outputs/INDEX.json` registry drift: indexed files exist, counts match, act/provision shape is valid unless explicitly excepted
13. State and KOV regulation indexes match their output trees
14. `metadata.jsonld` advertised counts match repository counts
15. Institution labels have no duplicate normalized canonical form
16. No Estonian personal identification code survives in `estleg:summary` / `estleg:legalText` (`validate_no_personal_codes`, #683)

## Data Coverage

Counts as of the 2026-09-05 Tier 0 tree (`metadata.jsonld` `dcterms:modified`
2026-09-04). `validate_all.py` cross-checks the catalogue counts against the
tree, so a stale row here fails the gate.

| Category | Files | Indexed records |
|----------|------:|-----------------|
| Enacted law peep files | 1,195 | 1,122 law index entries |
| State regulations | 3,812 | 3,812 in the regulation index |
| KOV regulations | 11,059 | 11,059 |
| Draft legislation | 6 | 22,832 drafts |
| Supreme Court decisions | 35 | 12,104 decisions (Civil 4,988 · Criminal 3,686 · Administrative 2,434 · Constitutional Review 800 · Misdemeanor 107 · Other 89) |
| Lower-court decisions (`kohtud/`) | 1 | 1 decision — a labelled **sample** (`estleg:isSampleData`), not a corpus (#689) |
| EU legislation | 6 | 33,242 acts |
| EU court decisions | 9 | 22,290 decisions |
| Sanction sidecars | 464 | 7,264 sanction records (#681) |
| ProvisionVersion sidecars | 4,422 | version history for laws and state regulations |
| Amendment sidecars | 5,647 | 20,937 `AmendmentEvent` nodes |
| Õiguskantsler annotation sidecar | 1 | 13,402 annotation nodes from 4,052 scraped opinions |
| Institutions | 117 | institution files |
| Controlled vocabulary | 1 | 417 vocabulary and fallback nodes |

## SHACL Bucket Checks

Per-bucket source validation is `scripts/shacl_validate_all.py --bucket <name>`
(RDFS inference). The table records the original Tier 0 run before the review
fixes and, for buckets not re-run locally, the CI result at `c96577d50c`
(`semantic-validation (<bucket>)` jobs of the 2026-08-31 scheduled run).

| Bucket | Files | Result | What the violations are |
|---|---:|---|---|
| `sidecars` | 10,873 | **FAIL** — 309,422 violations (314,522 on `c96577d50c`) | Tier 0 removed the 5,100 phantom `caseType` / `caseNumber` violations: `estleg:applicableProvision` carried `rdfs:domain estleg:CourtDecision`, which typed every Sanction as a court decision under inference. What remains is pre-existing: 99,981 provision IRIs referenced from the version, concept and sanction sidecars are phantom-typed `estleg:LegalProvision` by `rdfs:range` axioms and then fail `paragrahv` / `summary` / `partOfAct` (3 × 99,981); 4,841 `AmendmentEvent` nodes lack `estleg:amends`; 4,433 referenced `Reg_*` and act-map IRIs lack `rdfs:label`; 179 `versionValidFrom`; 24 `institutionType`; 2 annotation fields. **#702 / #709.** |
| `riigikohus` | 35 | **FAIL** (CI) | ~30k `versionOf` / `versionText` / `versionValidFrom` violations from the same range-axiom phantom typing of ProvisionVersion stubs. **#702 / #709.** |
| `laws`, `kov`, `drafts`, `curia` | — | **FAIL** (CI) | Not re-run locally on 2026-09-05; red in CI at `c96577d50c`. Triage is **#702**. |
| `eurlex` | 163 | PASS (CI) | |
| `--all` | — | not run on 2026-09-05 | |

The "phantom typing" pattern is documented in `shacl/README.md`: a class
`rdfs:domain` / `rdfs:range` on a predicate shared across classes types every
subject / object into that class under `inference="rdfs"`, after which the
class's shape demands fields the node was never meant to carry. Narrowing the
remaining axioms is **#709**; making the validator report only the genuine
findings is **#702**.

### Review checks on regenerated data

The review removed 755 false confiscation/dissolution records and 220 empty
sanction sidecars. The remaining 7,264 sanctions in 464 files and the deprecated act
roots conform to the affected shapes (`SanctionShape`, the three sanction
ceiling shapes, and `DeprecatedActNotInForceShape`) under both `inference="none"`
and `inference="rdfs"`. Range ordering is checked only for matching units and
currencies: 30 days to 1 year must not be rejected as 30 > 1.

The full source-bucket and standalone SHACL counts below/above remain the
original Tier 0 measurements; the review reran the affected shapes, JSON-LD
validation/parity, and the Seadusloome load gate.

The default suite passes (4,282 passed, 67 skipped); MCP passes (110 passed,
1 skipped). With materialised LFS inputs, `pytest -q -m corpus` reports
62 passed, 1 skipped and four failures that also occur on the pre-review inputs:
the reverse EuroVoc example in `API_GUIDE.md`, the 2,409-stub closure-policy
finding, a test requiring obsolete `LegalProvision_<slug>` instances, and a
test requiring a direct `NationalRegulation → Act` axiom rather than the
current `NationalRegulation → DomesticRegulation → Act` hierarchy. These remain
outside the review fixes; the stale test/validator expectations belong with
#702 and the closure-policy finding with #705.

### Combined-only gate (`scripts/validate_combined_standalone.py`)

2026-09-05, original Tier 0 tree before the review fixes: **FAIL — 251,273 violations**.
The findings are aggregate-artifact findings and fall into three groups, none
introduced by Tier 0: `estleg:Subsection` nodes typed `estleg:LegalProvision`
fail `paragrahv` / `summary` (the lõige nodes carry `subsectionNumber` and
`legalText` instead — the shape predates #514 lõige minting; **#702 / #709**);
regulation-provision closure stubs (`Reg_*`) fail `paragrahv` / `summary` /
`partOfAct` because the stub keeps the type but not those fields (**#416 /
#705**); 4,841 `AmendmentEvent` nodes lack `estleg:amends` (**#702**). The
482 label-less orphan Sanction nodes that the pre-rebuild artifact carried are
gone (the extractor now purges stale inline anchors, #681).

### Seadusloome zero-warning gate

`scripts/validate_seadusloome_sync.py` fails at graph closure on
`eurlex/eurlex_combined.jsonld` (`owl:imports estleg:EURlex_Schema_2026`, a
stale LFS aggregate) before SHACL runs. Rebuilding every aggregate in one DAG
step is **#705**; until then this gate is red for a known reason and is not a
statement about the source data.

## Similarity Coverage

_Figures from the 2026-05-26 run; not re-measured on 2026-09-05._

`scripts/generate_similarity_index.py` now includes state regulations while still deferring KOV similarity to Layer 3.

| Type | Files | Provisions analyzed |
|------|-------|---------------------|
| Laws | 1,186 | 36,165 |
| State regulations | 3,812 | 48,362 |
| KOV regulations | 0 | 0 |

Similarity output now contains 84,527 analyzed provisions, 108,684 pairs, and updates 3,659 JSON-LD files.

## Õiguskantsler Annotation Ingestion

_Figures from the Phase 3.4 run (2026-05); not re-measured on 2026-09-05._

Phase 3.4 used `scripts/generate_annotations.py --probe-pdfs --pdf-probe-sample-size 10`
as the go/no-go probe before the full live scrape. The probe found usable PDF
text layers in 10 of 10 sampled opinions (`usable_text_layer_ratio = 1.0`,
observed valid-character ratios `0.955`–`0.975`), so OCR was not introduced.

The full cached scrape then processed the archive with PDF body text enabled:

| Metric | Count |
|--------|-------|
| Opinions processed | 4,052 |
| PDF text layers accepted | 4,045 |
| PDF fetch failures | 1 |
| Unusable/scanned PDFs | 6 |
| Annotation nodes emitted | 13,402 |
| Opinions with output | 3,727 |
| Unresolved law references surfaced in coverage report | 325 |

The retained `MIN_PDF_TEXT_VALID_CHAR_RATIO = 0.30` remains conservative for
this corpus: clean sampled text was far above the threshold, and the full run
identified only six unusable/scanned PDFs. The coverage report at
`krr_outputs/reports/kov/extract_annotations_coverage.json` records the low
recall/unresolved-reference surface for follow-up triage.

## Seadusloome Zero-Warning Gate

This gate covers the **Seadusloome public load surface** — combined **plus** the
public subdirectories. See "Load surfaces and validation gates" above for how it
relates to the combined-only gate (`scripts/validate_combined_standalone.py`) and
the per-bucket source gate (`scripts/shacl_validate_all.py --bucket <name>`).

**Status 2026-09-05: red** — see [Seadusloome zero-warning gate](#seadusloome-zero-warning-gate-1) above for the reason (#705).

`scripts/validate_seadusloome_sync.py` mirrors the Seadusloome `main` sync load path and enforces a zero-warning policy on the published ontology.

- **Load set:** `krr_outputs/combined_ontology.jsonld` plus the public
  load-surface directories defined in `scripts/estleg_common.py`:
  `eelnoud/`, `riigikohus/`, `curia/`, `eurlex/`, `concepts/`,
  `sanctions/`, `amendments/`, `institutions/`, `provision_versions/`,
  `annotations/`, `harmonisation/`, and `regulations/`. This is the same
  set that Seadusloome ingests when it clones the ontology repository on
  `main`, and it is the graph over which sidecar object references must
  resolve.
- **Validator:** pyshacl with `inference="none"` against
  `shacl/estonian_legal_shapes.ttl`. The Seadusloome consumer does not
  apply RDFS inference, so the gate intentionally diverges from the
  bucket validator (`scripts/shacl_validate_all.py`), which uses
  `inference="rdfs"` to derive class memberships before checking shape
  targets.
- **Expectation:** zero SHACL warnings and zero SHACL violations.
  `--max-warnings` defaults to `0`. Exit codes: `0` clean, `1` warnings
  or violations exceed the threshold, `2` parse failures.
- **Relation to bucket SHACL:** the bucket validator catches semantic
  defects per class with inferred types; the Seadusloome gate catches
  defects that are visible to a downstream consumer that does not apply
  inference, including stale aggregate drift in
  `combined_ontology.jsonld`. Both gates must pass on release.
- **CI wiring:** the `Seadusloome zero-warning gate` job in
  `.github/workflows/validate.yml` runs on every pull request and `main`
  push. The job uploads the rdflib turtle SHACL report as
  `seadusloome-shacl-report` on failure for offline triage. The 2026-05-26
  local benchmark loaded 21,874 JSON-LD inputs / 7,127,720 data triples and
  completed in 8:38 wall time (`real 518.46`); if corpus growth makes the gate too slow for
  per-PR execution, split the gate by bucket or demote it to release-only via
  `.github/workflows/validate.yml`.

### Draft Reference Cleanup Notes

The #217 graph-closure pass removed a small set of `estleg:amendsLaw`
targets from `krr_outputs/eelnoud/eelnoud_combined.jsonld` because the
referenced act nodes are not emitted anywhere in the public load surface.
The table below is limited to dead-reference removals; successful target
renames and array de-duplication are listed separately. These dead references
were not treated as successful resolutions:

| Removed target | Root cause |
|----------------|------------|
| `estleg:Vlaigusseadus_Osa10_1005_1067` | Draft law-name resolver lost the leading `Võ` during slug generation; should resolve to the VõS corpus node family. |
| `estleg:hinenud_Rahvaste_Organisatsio_Map` | Draft law-name resolver lost the leading `Ü` and truncated the treaty title; no such map node is emitted. |
| `estleg:Eesti_Vabariigi_valitsuse_ja_A_Map` | Treaty `_Map` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |
| `estleg:Eesti_Vabariigi_valitsuse_ja_L_Map` | Treaty `_Map` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |
| `estleg:Eesti_Vabariigi_ja_Euroopa_Inv_Map` | Treaty `_Map` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |
| `estleg:Isikuandmete_automatiseeritud__Map` | Treaty `_Map` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |
| `estleg:Maailma_Terviseorganisatsiooni_Map` | Treaty `_Map` target was inferred from a title that no current generator materialises as an act-map node. |
| `estleg:Merinuete_korral_vastutuse_pi_Map` | Treaty `_Map` target was inferred from a truncated title but no act-map node is emitted by the current pipeline. |

Successful target rewrites in the same diff:

| Removed target | Replacement target | Notes |
|----------------|--------------------|-------|
| `estleg:TKS_Map` | `estleg:TarbKS_Map` | Six references were rewritten to the emitted TarbKS map node. One duplicate-array entry on the draft "Ülikooliseaduse ja Tartu Ülikooli seaduse muutmise seadus" was removed without replacement because the array contained `TKS_Map` twice. |
| `estleg:TS_Map` | `estleg:ToS_Map` | Clean 1-to-1 rename to the emitted target. |

Before the draft-impact enrichment is rerun against live data, fix the
resolver path used by `scripts/extract_draft_impact.py` and
`scripts/generate_draft_legislation.py` so leading Estonian diacritics
and treaty titles resolve to existing registry abbreviations instead of
re-emitting these dead references.

## Known Remaining Issues

- **Validator rules (#702):** 3,426 of the 3,546 `validate_all.py` errors are
  the two stale rules on `dcterms:subject` and `dcterms:title`; the
  semantic-collision and registry-drift checks also need the aggregate and
  `estleg:Part` exemptions. Until #702 lands, `json-validation` stays red and
  cannot be a required check.
- **T-Box axioms (#709):** `rdfs:range` / `rdfs:domain` on shared predicates
  phantom-type referenced nodes under RDFS inference, which is what keeps the
  `sidecars` and `riigikohus` SHACL buckets red.
- **Aggregates (#705):** `eurlex` / `curia` / `eelnoud` combined files and
  `combined_ontology.{nt,nq,ttl}` are stale relative to their sources; the
  Seadusloome gate fails at graph closure on `eurlex_combined.jsonld`.
- **Draft resolver:** the dead-reference cleanup documented above remains a
  required follow-up before the next live draft-impact ingestion run.
- **Personal names (#720):** codes are screened (#683); names in Riigikohus
  summaries and CURIA party labels are a DPO decision, not a validator gap.
