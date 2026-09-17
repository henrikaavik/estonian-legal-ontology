# Validation Report

**Last updated:** 2026-09-07 (Tier 1 #702 merged; documentation status refreshed)
**Primary validator:** `scripts/validate_all.py`

## Summary

<!-- BEGIN GENERATED: validation-summary -->

*Measured by `scripts/generate_validation_report.py` at commit `00c8e6471330e55737c798e22a4026d30930b773`, 2026-09-07 13:16 UTC. Do not hand-edit this block.*

| Metric | Count |
|--------|------:|
| Files validated | 26,961 |
| Errors | 122 |
| Warnings | 2 |
| Result | **FAILED** |

| Count | Error category |
|------:|----------------|
| 38 | Duplicate @id within file |
| 34 | indexed file has <n> act-level nodes (expected <n>) |
| 27 | @type is not an array |
| 5 | skos:exactMatch is not an array |
| 5 | indexed file has no provision nodes and no registry exception |
| 3 | missing <n> source graph IDs |
| 3 | older than at least one canonical source file |
| 2 | <n> shared provision IDs drift from source on SHACL-sensitive fields |
| 1 | <n> @id values are duplicated across files (semantic collisions) |
| 1 | <n> predicates, <n> classes |
| 1 | <n> act-level temporal properties on non-Act nodes |
| 1 | <n> stub node(s) carry disallowed estleg: object refs — a stub may carry only the shaped closure edges ['<iri>', '<iri>', '<iri>', '<iri>', '<iri>', '<iri>', '<iri>', '<iri>', '<iri>', '<iri>'] |
| 1 | <n> stale extra IDs not present in any canonical source |

<!-- END GENERATED: validation-summary -->

> **Correction.** Until 2026-09-05 this report said `Errors: 0 / PASSED`
> against a run dated 2026-05-26. That statement was false for every committed
> tree since at least v1.0.0 (2026-08-19): the `json-validation` CI job has
> been red on every `main` run in that period. The table above is no longer
> hand-maintained — `scripts/generate_validation_report.py` measures it from a
> real run and stamps the commit SHA, and `--check` fails CI if the committed
> numbers drift from the corpus. Baselines: 26,791 files / 3,558 errors on the
> pre-Tier-0 tree (`c96577d50c`), 26,961 / 3,549 after Tier 0, and 26,961 /
> 122-123 after the #702 validator repair. The total moves by one because the
> `older than at least one canonical source file` rule counts filesystem
> mtimes, not content: regenerating a T-Box artifact makes it newer than the
> aggregates embedding it, and a fresh checkout assigns mtimes in arbitrary
> order. That rule is excluded from the `--check` comparison and belongs with
> the stale-aggregate work (#705).

Both report checks fail when the input file count differs from the recorded
count. Materialise missing LFS inputs before checking; when the corpus has
changed, regenerate the reports. A count difference cannot establish that the
report is current.

The repository advertises 27,008 generated JSON/JSON-LD files (`metadata.jsonld`
`estleg:totalFiles`). `validate_all.py` excludes generated reports, indexes,
manifests, and probe outputs that are not corpus inputs, which is why it
validates 26,961.

## What the errors are

Every error is itemised below with its status. **#702 removed 3,426 of the
3,549 errors (96.5%) by repairing two stale validator rules** — they were
validator bugs, not data defects, and they buried the ~122 findings that
remain (the total moves by one with the mtime-based freshness rule; see the
Correction note above).
No new validation error category appeared; all internal object references
resolve.

The two repaired rules were:

- `dcterms:subject is not an array` (3,021) — an `estleg:Chapter` maps to
  exactly one cluster and carries a single IRI object by design. The rule now
  exempts that type only; Acts and Parts still require an array.
- `dcterms:title must be a string or language-tagged value` (405) — bilingual
  title lists are the #437 language-tag policy. The rule now accepts a list of
  language-tagged values, while still rejecting empty lists and non-literal
  members. Value objects must contain a string `@value`; IRI objects, empty
  objects, and numeric literals are rejected both alone and inside lists.

| Count | Finding | Status |
|---:|---|---|
| 38 | Duplicate `@id` within file (37 in `analytical_overlay.jsonld`, 1 in `annotations/oiguskantsler_seisukohad.jsonld`) | Pre-existing; **#702 / #709**. |
| 34 + 5 | `INDEX.json` registry drift on the split codes (AÕS, KarS, TsMS, TsÜS, VÕS): `_osaN` files report 0 act-level nodes; `_map` files have no provision nodes and no registry exception | Pre-existing; the multipart-code registry rules predate `estleg:Part` roots. **#702 / #704.** |
| 27 | `@type is not an array` (26 controlled-vocabulary nodes, 1 in `analytical_overlay.jsonld`) | Pre-existing T-Box shape issue. **#709.** |
| 5 | `skos:exactMatch is not an array` (`NormType_*`) | Pre-existing. **#709.** |
| 3 + 1 + 1 | `eurlex` / `curia` / `eelnoud` combined files: missing source graph IDs in each, one stale CURIA ID, and the draft aggregate older than a canonical source | Stale LFS aggregates that were not rebuilt with the sources. **#705.** |
| 2 | `combined_ontology.jsonld` and `eelnoud_combined.jsonld`: shared provision IDs drift from source on SHACL-sensitive fields (21,802 in the flagship file after the Tier 0 rebuild, 24,792 before; most on split-code `_OsaN` nodes and on classifier fields) | Aggregate-artifact drift; pre-existing (24,792 on the pre-Tier-0 tree). **#705.** |
| 1 | `combined_ontology.jsonld`: 2,409 stub nodes carry `estleg:` object refs outside the shaped closure edges | Pre-existing builder finding. **#416 / #705.** |
| 1 | `@id` values duplicated across files | The semantic-collision check counts the same node in an aggregate and in its source; the rule still needs the aggregate exemption. **#702 follow-up.** |
| 1 | Undefined reusable vocabulary terms: 5 predicates | Pre-existing. **#709.** |
| 1 | 78 act-level temporal properties on non-Act nodes | Pre-existing. **#702.** |

Findings removed by Tier 0: the six `metadata.jsonld` count mismatches
(#686), the merged-overlay-absent-from-combined finding and one
stale-aggregate finding on each of the `eurlex` / `curia` / `eelnoud` files
(combined rebuild, #681/#682). `dataset_build_manifest.json` was regenerated so its
`catalogModified` matches the new `dcterms:modified` (#686).

Warnings (2): `generation_manifest_laws.json` is not committed
(Tier 1 #692/#704), and 9 amendment chains carry 1.0% duplicate `AmendmentEvent`
nodes (re-run `generate_amendment_history.py`).

## Load surfaces and validation gates

The repository exposes **three distinct load surfaces**, and the validation gate
differs per surface. Diagnose findings against the relevant source, builder,
and vocabulary: a combined-only failure does not by itself establish source-data
loss, and an inferred type can expose an incorrect domain/range axiom.

`krr_outputs/combined_ontology.jsonld` is the flagship aggregate artifact. It is
designed to be **semantically complete on its own** for every shaped node it
contains. Graph-closure stub nodes (marked `estleg:isStubNode`) are intended to
include the SHACL-required semantic edges for their type. The failures below
show that this contract is not yet met everywhere. An app that follows the broader Seadusloome surface instead
loads the combined file **plus the public subdirectories** and merges nodes by
`@id` using RDF graph-merge semantics (a thin stub in combined is filled in by
its full source node from a subdir).

| Surface | What an app loads | Target invariant | Gate command | Where to investigate |
|---------|-------------------|-------------------------|--------------|-----------------|
| **Combined-only** | `krr_outputs/combined_ontology.jsonld` **alone** | The file is semantically complete on its own for every shaped node it contains (graph-closure stubs carry the required semantic edges; no subdir merge needed). | `scripts/validate_combined_standalone.py` | **Aggregate-artifact** drift — the *builder* produced an incomplete combined file. Fix by regenerating combined via `scripts/fix_all_issues.py` (the `generate_combined_jsonld` builder). **Never** relax SHACL. |
| **Source subcorpora alone** | The per-bucket source files (laws, kov, riigikohus, eurlex, curia, drafts, sidecars) | Each source bucket conforms to the shapes on its own (with RDFS inference deriving class membership). | `scripts/shacl_validate_all.py --bucket <name>` (INDEX-driven; `inference='rdfs'`) | Check source fields and the vocabulary's inferred types; repair the generator or incorrect axiom as appropriate. |
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
`src/estleg/estleg_common.py` as `PUBLIC_LOAD_SUBDIRS`: `eelnoud`, `riigikohus`,
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
4. Multi-valued properties use their declared shapes (including the Chapter subject exception)
5. `sectionNumber` is always a string
6. `dc:source` is a string or an array of strings
7. `xsd:date` value objects use strict `YYYY-MM-DD` literals
8. `estleg:affectedLawName` uses the canonical array-of-strings shape
9. `@id` uniqueness within and across files, excluding known shared class IDs
10. `dcterms:source` uses an IRI object when present (no live URL-availability check)
11. Internal `estleg:` object references resolve to corpus nodes
12. `krr_outputs/INDEX.json` registry drift: indexed files exist, counts match, act/provision shape is valid unless explicitly excepted
13. State and KOV regulation indexes match their output trees
14. `metadata.jsonld` advertised counts match repository counts
15. Institution labels have no duplicate normalized canonical form
16. No Estonian personal identification code survives in `estleg:summary` / `estleg:legalText` (`validate_no_personal_codes`, #683)

## Data Coverage

Counts re-derived on the 2026-09-07 reviewed Tier 0 tree (`metadata.jsonld`
`dcterms:modified` 2026-09-07). `validate_all.py` cross-checks the catalogue
counts against the tree, so a stale catalogue fails the gate.

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
| Sanction sidecars | 464 | 7,392 sanction records (#681) |
| ProvisionVersion sidecars | 4,422 | version history for laws and state regulations |
| Amendment sidecars | 5,647 | 20,937 `AmendmentEvent` nodes |
| Õiguskantsler annotation sidecar | 1 | 13,402 annotation nodes from 4,052 scraped opinions |
| Institutions | 117 | institution files |
| Controlled vocabulary | 1 | 417 vocabulary and fallback nodes |

## SHACL Bucket Checks

Per-bucket source validation is `scripts/shacl_validate_all.py --bucket <name>`
(RDFS inference). File counts come from the discovery code on the `884c853967`
tree. "Before" is [CI run 34837816050](https://github.com/henrikaavik/estonian-legal-ontology/actions/runs/34837816050)
on `0cb9ac91bc`; "after" is a local pyshacl 0.31.0 run on 2026-09-17 with
the vocabulary repaired under #709. CI re-measures every bucket on each push.

| Bucket | Files | Before #709 | After #709 | Status |
|---|---:|---:|---:|---|
| `riigikohus` | 35 | 30,426 | **0 — PASS** | All of it was `interpretsVersion` typing `provision_versions/` nodes as bare `ProvisionVersion`. |
| `drafts` | 4 | 9,482 | **0 — PASS** | All of it was `changeType` typing every draft as a `ProposedAmendment`. |
| `kov` | 11,063 | 36,511 | **6** | Real: six `Reg_*_Map` acts carry `contentStatus "repealedBeforeSnapshot"`, which `ActTemporalShape`'s value list omits. |
| `sidecars` | 10,653 | 309,422 | **5,046** | Real: see the list below. |
| `laws` | 4,970 | 338,909 | **1,602** | 337,335 after the axiom repair; 1,602 once the §-level minimums excuse lõiked. Real: see below. |
| `eurlex` | 162 | PASS | **PASS** | Source-bucket conformance does not establish aggregate parity. |
| `curia` | 6 | PASS | **PASS** | #702 removed the shared-predicate domains that typed EU court nodes. |
| `--all` | 26,887 | — | No completed local result | The 2026-09-07 attempt stopped during graph loading at 6 GiB process RSS on a 16 GiB host. |

### What #709 removed

The "phantom typing" pattern is documented in `shacl/README.md`: an
`rdfs:domain` / `rdfs:range` that names a shaped class types every subject /
object of the property into that class under `inference="rdfs"`, after which
the class's shape demands fields the node was never meant to carry.
`scripts/check_phantom_typing.py` finds it from the JSON-LD in about half a
minute for all seven buckets, and against the pre-#709 vocabulary it reproduces
the pyshacl focus-node counts below exactly. It now reports none.

| Bucket | Axiom | Nodes typed | As |
|---|---|---:|---|
| `riigikohus` | range of `interpretsVersion` | 10,142 | `ProvisionVersion` |
| `drafts` | domain of `changeType` | 9,482 | `ProposedAmendment` |
| `kov` | domain of `municipalityStatus` / `municipalityType` | 11,566 | `Municipality` |
| `kov` | range of `citationTarget` / `similarTarget` | 582 / 61 | `LegalProvision` / `Act` |
| `kov` | domain of `enactedBy` / `enactedByMunicipality` | 116,708 | `Act` (no violation: `ActShape` happened to pass) |
| `sidecars` | range of `versionOf`, domain of `definesConcept` | 90,104 + 9,877 | `LegalProvision` |
| `sidecars` | range of `partOfAct` / `proposesToAmend` | 4,433 | `Act` |
| `laws` | range of `hasProposedAmendment` | 763 | `ProposedAmendment` |
| `laws` | domain of `semanticallySimilarTo` | 35,579 | `Act` (no violation) |

Every node the `riigikohus` axiom typed is a complete, correctly typed
`ProvisionVersion` in `provision_versions/`; none was dangling.

### What remains, and is real

- **`sidecars`, 5,046.** 4,841 version-layer `AmendmentEvent` nodes
  (`Amendment_*_vf_*`) carry `entryIntoForce` and `resultedInVersion` but no
  `estleg:amends`, which `AmendmentEventShape` requires. 179 `ProvisionVersion`
  nodes fail `versionValidFrom` < `versionValidTo`. 24 `Institution` nodes carry
  `institutionType "minister"`, absent from the shape's value list. Two fields
  are missing on one Õiguskantsler annotation.
- **`kov`, 6.** `contentStatus "repealedBeforeSnapshot"`, as in the table.
- **`laws`, 1,602.** 533 § nodes in the two hand-modelled OWL modules
  (`karistusseadustik_eriosa_owl.jsonld` 430, `tsus_osa7_138_169_owl.jsonld`
  103), typed `estleg:Section` / `estleg:LegalProvision`, lack `paragrahv`,
  `summary` and `partOfAct` — 533 × 3, each reported by the shape named for
  the field — plus three duplicate-value findings on `REOS_Map` and `ROS_Map`.

### The lõige question (#709)

After the axiom repair `laws` still stood at 337,335, and 335,733 of that was
one thing: 111,911 `estleg:Subsection` (lõige) nodes each failing
`LegalProvisionShape`'s `paragrahv`, `summary` and `partOfAct` minimums. It was
not an axiom defect. #519 declared `Subsection rdfs:subClassOf LegalProvision`
while that shape was reached only through `sh:targetSubjectsOf
estleg:paragrahv`, so no lõige was a focus node; #450 later added
`sh:targetClass estleg:LegalProvision`, which reaches every one. The same
nodes were about 89% of the combined-only gate's findings, because pyshacl
resolves class targets through `rdfs:subClassOf` in the data graph even with
inference off.

A lõige carries its own `legalText` and exactly one `parentProvision` by
design (#132, `SubsectionShape`); its § reference, summary and act live on the
parent. The three minimums now sit in `ProvisionRequiresParagrahvShape`,
`ProvisionRequiresSummaryShape` and `ProvisionRequiresPartOfActShape`, each
excusing nodes typed `estleg:Subsection`. `LegalProvisionShape` still
constrains the values of every provision, lõiked included, and a § that lacks
a field still fails — the 533 above are exactly those. 337,335 − 335,733 =
1,602.

### Review checks on regenerated data

The earlier review removed 755 false confiscation/dissolution records and
220 empty sanction sidecars. The 2026-09-07 review additionally keeps natural-
and legal-person penalties distinct, preserves superscripted subsections such
as `(1¹)`, and requires the operative life-imprisonment wording to stay within
the same sentence/subsection. A corporate penalty with no stated amount carries
no natural-person daily-rate default. Company board members and unrelated
company mentions do not trigger the corporate rule.

The final sanctions and deprecated act roots conform to the affected shapes
(`SanctionShape`, the three sanction ceiling shapes, and
`DeprecatedActNotInForceShape`) under both `inference="none"` and
`inference="rdfs"`, with zero validation results. Range ordering is checked
only for matching units and currencies: 30 days to 1 year must not be rejected
as 30 > 1. Combined was regenerated with the canonical builder after the full
sanctions pass: 268,836 nodes (268,835 unique IDs plus the dataset header).

The full source-bucket and standalone SHACL counts below/above remain
historical measurements. The 2026-09-07 full `--all` and standalone attempts
were stopped at 6 GiB process RSS each to keep the 16 GiB workstation
responsive; neither produced a completed SHACL result. These attempts do not
establish full SHACL conformance. The affected shapes, JSON-LD
validation/parity, and Seadusloome load gate were rerun.

The Tier 0 review's default suite passed (4,293 passed, 67 skipped); MCP passed
(113 passed, 1 skipped). Ruff, Docs lint, and the release/validate-only
integration DAG dry-run passed. The subsequent #702 review passed 4,385 default
tests with 68 skipped. These are software checks, not full-corpus conformance.

The documentation refresh reran `pytest -q -m corpus` with materialised LFS
inputs: **64 passed, 1 skipped, 3 failed**. All 49 executable documentation
examples pass, including the repaired reverse EuroVoc query and sidecar
loaders. The namespace guard also passes after historical worksheets link to
the migration record instead of repeating the retired hostname.
The three remaining failures are the 2,409-stub closure-policy finding (#705),
a test requiring obsolete `LegalProvision_<slug>` instances, and a test
requiring a direct `NationalRegulation → Act` axiom rather than the current
`NationalRegulation → DomesticRegulation → Act` hierarchy. The latter two
are stale test expectations not addressed by the merged #702 PR.

The CI corpus-test step now runs after JSON hygiene fails, provided LFS
materialisation succeeded. The 2026-09-07 #731 CI run verifies this behavior:
the baseline corpus failures are reported instead of silently skipped.

### Combined-only gate (`scripts/validate_combined_standalone.py`)

2026-09-05, original Tier 0 tree before the review fixes: **FAIL — 251,273 violations**.
The findings are aggregate-artifact findings and fall into three groups, none
introduced by Tier 0: `estleg:Subsection` nodes typed `estleg:LegalProvision`
fail `paragrahv` / `summary` (the lõige nodes carry `subsectionNumber` and
`legalText` instead — the shape predates #514 lõige minting; **#702 / #709**);
regulation-provision closure stubs (`Reg_*`) fail `paragrahv` / `summary` /
`partOfAct` because the stub keeps the type but not those fields
(**#416 / #705**); 4,841 `AmendmentEvent` nodes lack `estleg:amends` (**#702**). The
482 label-less orphan Sanction nodes that the pre-rebuild artifact carried are
gone (the extractor now purges stale inline anchors, #681).

2026-09-17, with the §-level minimums excusing lõiked (#709): **FAIL — 26,840
violations**, down from 250,662 on `0cb9ac91bc`. The difference is exactly the
first group: 111,911 lõiked × `paragrahv` and `summary` = 223,822 (combined
already materialises `partOfAct` on them, #520). What remains is the other two
groups plus the legacy modules: 3,735 regulation-provision closure stubs × 3
and 4,446 KOV-provision stubs × 2 (**#705**); 4,841 `AmendmentEvent` nodes
without `estleg:amends`; 1,878 results on the § nodes of the hand-modelled
VÕS / KarS / TsÜS modules; and 24 `institutionType "minister"`. The gate now
groups its summary by reporting shape as well as result path, because an
`sh:or` node constraint carries no `sh:resultPath`.

### Seadusloome zero-warning gate

The 2026-09-07 rerun of `scripts/validate_seadusloome_sync.py` fails at graph
closure on
`eurlex/eurlex_combined.jsonld` (`owl:imports estleg:EURlex_Schema_2026`, a
stale LFS aggregate) before SHACL runs. Rebuilding every aggregate in one DAG
step is **#705**; until then this gate is red for a known reason and is not a
statement about the source data.

## Similarity Coverage

_Figures from the 2026-05-26 run; not re-measured on 2026-09-07._

`scripts/generate_similarity_index.py` now includes state regulations while still deferring KOV similarity to Layer 3.

| Type | Files | Provisions analyzed |
|------|-------|---------------------|
| Laws | 1,186 | 36,165 |
| State regulations | 3,812 | 48,362 |
| KOV regulations | 0 | 0 |

Similarity output now contains 84,527 analyzed provisions, 108,684 pairs, and updates 3,659 JSON-LD files.

## Õiguskantsler Annotation Ingestion

_Figures from the Phase 3.4 run (2026-05); not re-measured on 2026-09-07._

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

**Status 2026-09-07: red** — see [Seadusloome zero-warning gate](#seadusloome-zero-warning-gate) above for the reason (#705).

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

- **Validator rules (#702 — repaired):** the two stale rules on
  `dcterms:subject` and `dcterms:title` are fixed (3,549 → ~122 errors). The
  semantic-collision and registry-drift checks still need the aggregate and
  `estleg:Part` exemptions, so `json-validation` stays red on the remaining ~122
  and is not yet a required check.
- **T-Box axioms (#709, part 1 — repaired):** `rdfs:range` / `rdfs:domain` on
  shared predicates phantom-typed referenced nodes under RDFS inference. #702
  narrowed the four axioms behind the `curia` bucket (66,740 → **0**). #709
  repaired the rest: `riigikohus` and `drafts` now pass, `kov` and `sidecars`
  are down to their real findings, and `scripts/check_phantom_typing.py` keeps
  the pattern from returning. The combined artifact was rebuilt; it differs
  from the previous one in exactly the 36 property declarations concerned.
  The lõige question that owned `laws` is settled above. Still open under
  #709: the ticket's standards work (SKOS-typed value families, `targetGroup`
  as an object property, a punkt class, language tags) and the
  `estleg:Section` / `Part` / `LegalPart` modelling in the legacy OWL modules.
- **Aggregates (#705):** `eurlex` / `curia` / `eelnoud` combined files and
  `combined_ontology.{nt,nq,ttl}` are stale relative to their sources; the
  Seadusloome gate fails at graph closure on `eurlex_combined.jsonld`.
- **Draft resolver:** the dead-reference cleanup documented above remains a
  required follow-up before the next live draft-impact ingestion run.
- **Personal names (#720):** codes are screened (#683); names in Riigikohus
  summaries and CURIA party labels are a DPO decision, not a validator gap.
