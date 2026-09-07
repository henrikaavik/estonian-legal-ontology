<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Validation gates review — public-sector usefulness

Reviewer scope: `src/estleg/validate_all.py`, `shacl_validate_all.py`,
`validate_seadusloome_sync.py`, `validate_combined_standalone.py`,
`check_tbox_consistency.py`, `consolidate_tbox.py`,
`check_numeric_identity_strings.py`, `shacl/estonian_legal_shapes.ttl`,
`shacl/README.md`, `docs/VALIDATION_REPORT.md`,
`docs/COMBINED_SHACL_STUB_REMEDIATION_TICKETS.md`,
`docs/DUPLICATE_IDS_REPORT.md`, and the `json-validation` /
`semantic-validation` / `seadusloome-zero-warning` jobs in
`.github/workflows/validate.yml`.

---

## Scope & method

1. Read `AGENTS.md`, `docs/ARCHITECTURE.md`, `shacl/README.md` for the declared
   gate policy, then read every file in scope end to end.
2. Enumerated every check wired into `validate_all.main()`
   (`src/estleg/validate_all.py:3571-3672`) and every NodeShape in the shapes
   file.
3. **Executed** `python3 scripts/validate_all.py` once on a clean tree
   (`git status` showed only an untracked `work-overview.html`; HEAD `c96577d50c`).
   Result below — it fails.
4. **Executed** `python3 scripts/shacl_validate_all.py --bucket curia` (the
   smallest bucket, 6 files) to get a real runtime/memory/outcome data point
   without running `--all`.
5. Cross-checked against the actual GitHub Actions history via `gh run list`.
6. Sampled 60 random root law peeps to measure what the gates *do not* assert
   (text fidelity, provenance, snapshot dates).

### Measured outcomes (this machine, Apple Silicon, 3.3 GB corpus on disk)

| Run | Files | Wall | Result |
|---|---|---|---|
| `scripts/validate_all.py` | 26,791 | 1m 49s | **FAIL — 3,557 errors, 2 warnings** |
| `scripts/shacl_validate_all.py --bucket curia` | 6 (319,453 triples) | 1m 26s | **FAIL — 66,740 violations** |

Raw logs: `.../scratchpad/validate_all.log`, `.../scratchpad/shacl_curia.log`.

### CI reality (`gh run list --workflow=validate.yml`)

The **last 8 workflow runs on `main` all failed**, including the `v1.0.0`
release commit (`f018cf05f2`) and the weekly `schedule` cron. The most recent
run has **12 of 16 jobs red**:

```
failure lint                         failure pytest
failure estleg-mcp tests             failure json-validation
failure Seadusloome zero-warning gate
failure semantic-validation (laws, kov, curia, drafts, riigikohus, sidecars)
success semantic-validation (eurlex) success Integration DAG dry-run
success Generator smoke              success RT content-staleness canary
success Docs lint
```

Everything below should be read against that fact: the gate design is
unusually thorough, and the gate is not currently passing.

---

## Inventory of what is validated

### `validate_all.py` — 30 checks, all wired in `main()` (`:3571-3672`)

| # | Check (function) | Guarantee it gives a public body | Blocking? |
|---|---|---|---|
| 1 | `validate_json_syntax` `:330` | Every corpus file parses as JSON | error |
| 2 | `validate_context` `:361` | `estleg:` binds to `https://w3id.org/estleg/`; a remote `@context` is warned, not verified | error / warn |
| 3 | `validate_bare_namespace_act_ids` `:403` | No act reuses the bare namespace IRI as `@id` | error |
| 4 | `validate_types` `:419` | `@type` is always an array | error |
| 5 | `validate_multi_valued` `:427` | 30 listed predicates are arrays | error |
| 6 | `validate_section_numbers` `:438` | `sectionNumber` is a string | error |
| 7 | `validate_dc_source` `:447` | `dc:source` is string or string array | error |
| 8 | `validate_source_provenance` `:527` | `dcterms:source` is an IRI object; `dcterms:title` is str-or-dict | error |
| 9 | `validate_act_xml_sameas` `:475` | No `owl:sameAs` to a `.xml` manifestation | error |
| 10 | `validate_per_document_classes` `:494` | No mechanically minted per-file provision classes (#434) | error |
| 11 | `validate_xsd_dates` `:567` | Every `xsd:date` literal is strict `YYYY-MM-DD` | error |
| 12 | `validate_affected_law_names` `:579` | `affectedLawName` is array-of-strings | error |
| 13 | `validate_id_uniqueness` `:3439` | No cross-file `@id` collisions outside a curated shared-TBox allowlist | error |
| 14 | `validate_internal_references` `:683` | Every internal `estleg:` object ref resolves somewhere in the corpus | error |
| 15 | `validate_vocabulary_coverage` `:1655` | Every `estleg:` predicate/class used is declared in the CV | error |
| 16 | `validate_canonical_tbox` `:1685` | CV has an `owl:Ontology` header + version; ≥95% of properties have domain+range; no placeholders/junk; `metadata.jsonld` is DCAT-only and `conformsTo` the CV | error |
| 17 | `validate_temporal_property_targets` `:620` | Act-level temporal props live only on `estleg:Act` nodes | error |
| 18 | `validate_transposition_mapping` `:1512` | Transposition report is in current shape, non-empty or flagged, header counts match body | error (warn if file absent) |
| 19 | `validate_registry_index` `:763` | Every `INDEX.json` file exists, has a `@graph`, exactly 1 act node, ≥1 provision node; `total_laws`/`total_files` match | error |
| 20 | `validate_multipart_coverage` `:847` | Multipart osa gaps are explicitly marked | error |
| 21 | `validate_index_body_coverage` `:882` | No `summaryOnly` law; empty non-treaty count ≤ `EMPTY_SUBSTANTIVE_BASELINE=2` | error |
| 22 | `validate_regulation_indexes` `:943` | Regulation index counts + per-file existence + `acts` ledger | error |
| 23 | `validate_act_coverage_reconciliation` `:1026` | Manifest↔disk act reconciliation | **warn only, and currently a no-op** (see W6) |
| 24 | `validate_legacy_deprecations` `:3545` | Deprecated legacy roots carry `owl:deprecated` + `isReplacedBy` | error |
| 25 | `validate_combined_ontology` `:2134` | Combined has every source `@id`, no stale extras, no drift on 13 SHACL-sensitive fields | error |
| 26 | `validate_combined_graph_closure` `:2176` | Combined loads standalone with 0 dangling refs, no orphan/leaky stubs, full overlay census | error |
| 27 | `validate_provision_version_monotonicity` `:2546` | Version chains monotone, exclusive-end contract, no zero-duration redactions | error |
| 28 | `validate_provision_version_encoding` `:2624` | No U+FFFD in `versionText` | error |
| 29 | `validate_version_layer_freshness` `:2835` / `validate_last_amendment_matches_versions` `:2800` / `validate_regulation_version_coverage` `:2788` | Version interval covers peep `kehtiv`; `lastAmendmentDate` == max `versionValidFrom`; ≥90% regulation version coverage | error |
| 30 | `validate_provision_text_quality` `:2483` | No literal `<sup>` markup; no `summary == legalText` doubled | error |
| — | `validate_harmonisation_symmetry` `:2325` | Every `harmonises`→act edge has a backing `harmonisedWith`, backed by a transposition row | error |
| — | `validate_subcorpus_combined_ontologies` `:2890` / `validate_subcorpus_index_counts` `:1441` | Subcorpus combined parity; subcorpus `total_*` == node counts | error |
| — | `validate_metadata_catalog` `:1259` + `validate_metadata_repro_pins` `:1327` | Advertised DCAT counts == measured counts; no mutable `/main` URLs; manifest/version/modified agree | error |
| — | `validate_institution_duplicates` `:2946` / `validate_institution_registry_consistency` `:3018` | Canonical institution labels; alias targets resolve | error |
| — | `validate_dupn_iri_baseline` `:3410` | `_DupN` collision-suffix IRI count must not grow past 0 | error |
| — | `validate_tbox_inverse_parity` `:3162`, `validate_eu_provenance_fields` `:3227`, `validate_amendment_duplicate_bloat` `:3288` | Three known-stale artifacts | **warn only, by design** (`:3095-3107`) |

### SHACL

| Gate | Load surface | Inference | Failure rule |
|---|---|---|---|
| `shacl_validate_all.py --bucket X` | 7 buckets of source peeps + CV | `rdfs` | any non-conforming result → exit 1 |
| `validate_combined_standalone.py` | combined alone | `none` | any result (any severity) + `AmendmentEvent ≥ 1000` floor |
| `validate_seadusloome_sync.py` | combined + 14 public subdirs | `none` | graph closure first, then `Violation+Warning > --max-warnings` (default 0) |

Shapes: 44 NodeShapes, 112 `sh:minCount 1`, 10 `sh:pattern`, 37
`sh:languageIn`/`sh:uniqueLang`, **1** `sh:severity sh:Warning`, 0 `sh:sparql`,
0 `sh:closed`.

---

## Strengths

These are genuinely above the norm for a public-sector data product and should
be said plainly in any conformance statement.

- **The gates guard the *artifact*, not just the schema.**
  `validate_combined_graph_closure` (`validate_all.py:2176-2320`) asserts the
  shipped 304 MB combined file has zero dangling `estleg:` references, zero
  orphan stubs, that every stub carries only whitelisted closure edges, *and*
  runs an overlay census proving every merged sidecar node actually landed. A
  consumer can load one file and know it is closed.
- **Count drift is designed against, not merely documented.**
  `validate_metadata_catalog` (`:1259`) pins every `estleg:*Count` in
  `metadata.jsonld` — including the per-`dcat:distribution` keys — to a freshly
  measured count, and `validate_subcorpus_index_counts` (`:1441`) does the same
  for each subcorpus `total_*` ledger. The mechanism is exactly what a data
  steward wants. (It is currently *reporting* drift rather than preventing it —
  see W2.)
- **Reproducibility pins are enforced.** `validate_metadata_repro_pins` (`:1327`)
  rejects a catalogue URL pinned to the mutable `main` branch and requires a
  content SHA or a tagged release asset. That is a real archival guarantee.
- **Point-in-time correctness is a first-class gate.**
  `validate_provision_version_monotonicity` (`:2546`) walks
  `supersededByVersion` edges rather than sorting on `validFrom`, and enforces
  an exclusive-end contract so an as-of query cannot return two active versions
  on a transition day (`:2603-2610`). This is the check a legal-information
  system actually needs and most corpora skip.
- **The two-surface SHACL policy is deliberate and written down.**
  `shacl/README.md` explains why the bucket gate uses RDFS and the sync gate
  does not, and `validate_seadusloome_sync.main` prints which surface it is
  validating before it runs (`validate_seadusloome_sync.py:518-527`) so a finding
  cannot be misattributed. `validate_combined_standalone.py`'s module docstring
  (`:1-47`) is the clearest triage guide in the repo.
- **A false-green cannot come from an empty corpus.** Three separate guards:
  `validate_all.main` `:3639` (0 files → hard error),
  `shacl_validate_all.main` `:186-196` (a bucket that collapses to only
  `controlled_vocabulary.jsonld` → exit 2), and
  `validate_combined_standalone`'s `EXPECTED_CLASS_FLOORS` (`:88`), which exists
  precisely because `sh:minCount` reports clean when a class has vanished.
- **Anti-drift single-sourcing is real.** `estleg_common.is_non_data_file` /
  `iter_krr_jsonld_files` / `PUBLIC_LOAD_SUBDIRS` are shared by validate_all,
  the SHACL buckets and the public load surface, with the #240/#452 rationale
  in comments. The `_materialize_supertypes` reuse in `_parity_field_drift`
  (`:1896-1917`) is the right call: the parity check reuses the *builder's own*
  entailment rule instead of re-deriving it.

---

## Weaknesses / gaps

### C — Critical (blocks a public body from certifying the product)

**C1. The published conformance statement is false.**
`docs/VALIDATION_REPORT.md:7-14` states "Files validated 23,069 / Errors 0 /
Warnings 0 / **PASSED**", and `:100-111` lists all 7 SHACL buckets plus `--all`
as PASS. Measured today: 26,791 files, **3,557 errors**, and 6 of 7 buckets red
in CI. The report is dated `2026-05-26` (`:3`) — 3+ months stale — and there is
no mechanism binding it to a build. A procurement officer or data steward
reading this document would be materially misled. This is the single highest-
impact finding in my scope.

**C2. `v1.0.0` was cut on a red gate, and every run since has stayed red.**
`gh run list` shows failures at `f018cf05f2` ("Cut v1.0.0…") and at every
subsequent push and cron. `docs/ARCHITECTURE.md:9-15` names the GitHub Release
as *the* consumer path. There is no release-blocking link between the gates and
the tag: `.github/workflows/validate.yml` has no `release:` trigger and no
required-status enforcement visible in the workflow.

**C3. `validate_source_provenance` rejects valid multilingual titles — 405
self-inflicted errors.**
`validate_all.py:538-544` accepts `dcterms:title` only as `str` or `dict`. The
corpus correctly emits the JSON-LD array form for bilingual titles:

```json
"dcterms:title": [{"@value":"Abieluvararegistri seadus","@language":"et"},
                  {"@value":"Marital Property Register Act","@language":"en"}]
```

The shapes explicitly bless this construct (`estonian_legal_shapes.ttl:135-144`,
issue #509: `sh:or` over `xsd:string`/`rdf:langString` with `sh:uniqueLang`).
So the JSON-shape validator and the SHACL contract disagree, and the validator
is the one that is wrong. Fixing this is a one-line change that removes 405 of
the 3,557 errors.

**C4. `dcterms:subject` is overloaded and the gate is on the wrong side of it —
3,021 errors (85% of the total).**
`MULTI_VALUED_PROPS` (`validate_all.py:225`) requires `dcterms:subject` to be an
array. On `estleg:Chapter` nodes the corpus uses it single-valued for the topic
cluster: `{"@id": "estleg:Cluster_AVRS_1"}`. Meanwhile
`ActTemporalShape` (`estonian_legal_shapes.ttl:25-32`) treats `dcterms:subject`
as an *EuroVoc* IRI with a `sh:pattern` and `sh:Warning`. One predicate, two
incompatible meanings, and the gate enforces neither consistently. Either
Chapters should use a distinct predicate (`estleg:topicCluster` already exists,
`:257`) or `dcterms:subject` must leave the array-required set.

**C5. RDFS inference manufactures 66,740 false violations in one bucket.**
Measured `--bucket curia`: 6 files, 319k triples, **66,740** results, all from
three paths:

| Path | Count |
|---|---|
| `estleg:euDocumentType` | 22,290 |
| `estleg:caseType` | 22,225 |
| `estleg:caseNumber` | 22,225 |

Root cause, confirmed against `controlled_vocabulary.jsonld`:

| Property | `rdfs:domain` |
|---|---|
| `estleg:celexNumber` | `estleg:EULegislation` |
| `estleg:decisionDate` | `estleg:CourtDecision` |

Every `estleg:EUCourtDecision` carries both predicates, so under
`inference="rdfs"` each one is entailed to be *also* an `EULegislation` and a
`CourtDecision`, which activates `EULegislationShape` (`:983`) and
`CourtDecisionShape` (`:798`) and their `sh:minCount 1` requirements. The node
itself is typed only `["owl:NamedIndividual","estleg:EUCourtDecision"]` — the
data is fine.

`shacl/README.md:26-35` warns about exactly this mechanism but only for
`rdfs:range` on stub-valued properties. It says nothing about `rdfs:domain` on a
predicate shared by sibling classes, which is what actually broke. An outside
auditor cannot distinguish these 66,740 artifacts from real defects, and neither
gate output labels them.

### H — High

**H1. The gates guarantee structural hygiene, not legal-content fidelity.**
Nothing anywhere compares a provision's text to the Riigi Teataja source.
`LegalProvisionShape` (`:207-256`) requires the *derived* `estleg:summary`
(`sh:minCount 1`, `:220`) and leaves the *authoritative* `estleg:legalText`
optional (`:229-236`, "most provisions only carry the summary"). Measured on 60
random law peeps / 1,021 provisions:

| Field | Present |
|---|---|
| `estleg:summary` | 100.0% |
| `estleg:legalText` | 98.7% |
| `dcterms:source` or `dc:source` | 0.0% |

A regeneration that dropped `legalText` from 98.7% to 0% would pass every gate
in this repo. That inverts what a public body cares about.

**H2. No shape requires provenance or a snapshot date.**
`dcterms:source` carries `sh:minCount 1` on **no** shape (the only occurrence,
`estonian_legal_shapes.ttl:1925`, is optional on `AnnexShape`).
`estleg:kehtiv` is `sh:maxCount 1` + `xsd:date` (`:69-74`) but never required.
Measured on the same 60 files: **9 of 60 act roots (15%)** carry neither
`dcterms:source` nor `estleg:kehtiv` — e.g. `estleg:MTRS_Map`,
`estleg:VOVS_Map`, `estleg:volaoigusseadus_Map`. A consumer cannot prove from
the shapes that every act traces to an official source or has a "text valid as
of" date.

**H3. `estleg:consistencyChecked: true` is asserted in `metadata.jsonld` but
nothing runs the checker.**
`check_tbox_consistency.py` — the module `docs/SCHEMA_REFERENCE.md:60` names as
the justification for that stamp — is referenced only by
`tests/test_issue_522_consistency.py`. It appears in no CI job and in no
`validate_all` call. Same for `check_numeric_identity_strings.py` (#569's
executable decision guard) and `consolidate_tbox.py`. Three checkers exist, are
tested against fixtures, and never touch the corpus in any gate. This is an
unearned conformance claim in a published DCAT record.

**H4. `docs/DUPLICATE_IDS_REPORT.md` is generated from a pytest fixture, not the
corpus.** It reports one duplicate, `estleg:Shared`, across files including
`root_a_peep.json` — which does not exist in `krr_outputs/` and is created by
`tests/test_fix_all_issues.py:842` in a `tmp_path`. The real corpus has ~50
in-file duplicate `@id`s right now (`analytical_overlay.jsonld` alone has ~40:
`estleg:REOS_Map`, `estleg:TsUS_Osa2`, many `estleg:Similarity_*`). Shipping a
fixture artefact in `docs/` as a corpus report is worse than shipping nothing.

**H5. `krr_outputs/kohtud/` and `krr_outputs/analytical/` have no SHACL bucket.**
`PUBLIC_LOAD_SUBDIRS` (`estleg_common.py:262-277`) lists 14 dirs including
`kohtud` and `analytical`; `shacl_validate_all.BUCKETS` (`:120-128`) has 7 and
covers neither. So the lower-court corpus and the analytical overlay are checked
only by the no-inference sync gate, never by the RDFS bucket gate. Compounding
this, `validate_seadusloome_sync.py`'s own module docstring (`:9-14`) lists 12
subdirs and omits both — the docstring has drifted from the constant it uses.

**H6. Combined is never validated by the bucket gate, and deprecated laws are
never SHACL-validated at all.** `collect_laws` (`shacl_validate_all.py:29-36`)
drives off `INDEX.json`. `generate_index()` excludes deprecated legacy statutes
by decisions-file lookup, but those files still ship inside
`combined_ontology.jsonld` (that is the stated premise of
`validate_legacy_deprecations`, `validate_all.py:3545-3552`). Result: a class of
published nodes that no SHACL bucket ever sees.

### M — Medium

**M1. `validate_seadusloome_sync` can return 0 on a non-conforming graph.**
`validate_seadusloome_sync.py:596-616`:

```python
if actionable == 0 and conforms:      return 0
if actionable > args.max_warnings:    ... return 1
...
return 0        # <- reached when conforms is False and actionable == 0
```

`actionable` sums only `Violation` + `Warning`. A result at any other severity —
`sh:Info`, or an unrecognised severity IRI whose `_local_name` is neither —
makes `conforms` False while `actionable` stays 0, and the gate exits **0**. Not
live today (0 `sh:Info` in the shapes) but it is a one-shape-edit away, and it
silently contradicts `shacl/README.md`'s stated policy.
`validate_combined_standalone.evaluate` (`:279-283`) does the opposite: it
ignores `_conforms` and counts every result regardless of severity. Two gates,
two different definitions of failure.

**M2. The severity policy documented is not the severity policy implemented.**
`shacl/README.md:37-56` describes a two-tier scheme where `sh:Warning` is
"reserved for quality and coverage checks that reports should distinguish". The
shapes file contains exactly **one** `sh:Warning` (`:29`, the EuroVoc
`dcterms:subject` pattern) out of 44 NodeShapes. An auditor reading the policy
will expect a meaningful quality tier and find a single rule.

**M3. A graph-closure failure hides every SHACL finding.**
`validate_seadusloome_sync.main:566-568` returns 1 on closure failure *before*
loading the data graph. One dangling reference means an operator gets zero SHACL
signal and needs a second run after fixing it. For a 8m38s gate (the benchmark
recorded in `docs/VALIDATION_REPORT.md:196-200`) that is an expensive serial
dependency.

**M4. Parity covers 13 fields; SHACL-required fields are missing from the list.**
`PROVISION_PARITY_FIELDS` (`validate_all.py:80-94`) omits `estleg:partOfAct`
(`sh:minCount 1` on every provision, `estonian_legal_shapes.ttl:251`),
`rdfs:label` (`sh:minCount 1` on every Act, `:143`), `estleg:kehtiv`,
`estleg:temporalStatus`, and `estleg:hasSubsection`. The comment at `:75-79`
acknowledges the list is a "fast structural-equality precheck", but the fields
chosen are not the ones SHACL requires.

**M5. Runtime and memory make the release gate hard for a ministry to run.**
Observed on `--bucket curia`: 6 files, 319,453 triples, 1m 26s, **1.17 GB RSS**
(≈3.9 KB/triple). `AGENTS.md:104` lists `shacl_validate_all.py --all` as a
release gate; it loads ~23k files / ~7.1M triples. Naive extrapolation of the
observed per-triple cost gives **~28 GB RSS** — above a standard GitHub runner.
Consistent with this, CI never runs `--all`; it runs 7 separate bucket jobs
under a 30-minute cap (`validate.yml:286`). So the gate `AGENTS.md` tells a
contributor to run before finishing is the one gate CI does not run and that may
not fit on commodity hardware. This should be measured, not extrapolated, and
then published.

**M6. Corpus growth has already broken the "docs stay in sync" rule.**
Four sources now disagree:

| Statistic | README `:8` | `metadata.jsonld` | `VALIDATION_REPORT.md` | Measured |
|---|---|---|---|---|
| Law peep files | 1,190 | 1,190 | 1,190 | 1,195 |
| Total JSON/JSON-LD files | 23,118 | 23,118 | 23,118 | 26,837 |
| Court decisions | 12,137 | 12,137 | 12,137 | 12,104 |

`validate_metadata_catalog` catches the `metadata.jsonld` half and reports it as
6 of the 3,557 errors. Nothing checks README or `VALIDATION_REPORT.md`.

**M7. The shapes file is a release asset but is undiscoverable from the
catalogue and unversioned.** `estonian_legal_shapes.ttl` **is** attached to the
`v1.0.0` release (verified via `gh release view`). But: it has **0**
`owl:Ontology` headers and no version IRI, so there is no stable identifier to
cite; `metadata.jsonld` `dcterms:conformsTo` points only at
`https://w3id.org/estleg/vocabulary`; and no `dcat:distribution` entry describes
it. Seadusloome or RIK harvesting the DCAT record cannot find the contract they
are supposed to validate against.

**M8. No validation evidence is published per release.** Release assets are
data + shapes + `SHA256SUMS` + `dataset_build_manifest.json`. There is no SHACL
report, no `validate_all` output, no DQV/`prov:` quality record. The
`--report seadusloome-shacl-report.ttl` artifact is uploaded by CI **only on
failure** (`validate.yml:376-382`), so a green run leaves no evidence at all.

**M9. `dcterms:license` is absent and `dcterms:rights` says "DRAFT … pending
legal sign-off".** Outside my core scope but it is the first field an EU open-
data harvester reads, and no gate requires it. A dataset whose own rights
statement is marked DRAFT cannot be procured.

### L — Low

**L1.** `validate_act_coverage_reconciliation` (`:1026`) is the only check that
proves every peep on disk came from a supervised generator run — and
`krr_outputs/generation_manifest_laws.json` is in `.gitignore:30`, so on every
clone it emits one warning and returns (`:1112-1118`). The strongest provenance
check in the file never runs.

**L2.** `_is_lfs_pointer` is copy-pasted into ~20 modules
(`validate_all.py:314`, `check_tbox_consistency.py:165`,
`fix_all_issues.py:948`, …) despite `AGENTS.md` requiring shared helpers in
`estleg_common.py`. Each copy is a place a future pointer-format change silently
turns a gate green.

**L3.** `validate_provision_version_monotonicity` type-tests with
`"ProvisionVersion" not in str(v.get("@type", ""))` (`:2590`) — a substring match
on a stringified list. Works today; a class named `NotAProvisionVersion` would
slip through.

**L4.** `pyshacl.validate` is called with default `allow_warnings` /
`allow_infos` in all three gates. The "Warning ≡ Violation" policy in
`shacl/README.md:48-52` depends entirely on that default and is not asserted
anywhere.

**L5.** `EU_PROVENANCE_SAMPLE_SIZE = 50` (`:3133`) samples a deterministic
*prefix*, not a random sample — file-ordering-dependent and not defensible as
evidence.

---

## Improvement ideas

1. **Generate `docs/VALIDATION_REPORT.md` from a build instead of by hand.**
   Have `validate_all.py` and the SHACL gates emit machine-readable JSON, and
   render the report from it in CI with the commit SHA and timestamp.
   *Why it matters:* the conformance statement is the artefact a procurement
   officer reads; today it asserts PASS while the gate returns 3,557 errors
   (C1). A generated report cannot lie.
   *Effort:* M. *Impact:* H.
   *Files:* `src/estleg/validate_all.py`, `src/estleg/shacl_validate_all.py`,
   `.github/workflows/validate.yml`, `docs/VALIDATION_REPORT.md`.

2. **Fix the two self-inflicted validator bugs first — they are 96% of the
   errors.** (a) Accept the JSON-LD array form for `dcterms:title` in
   `validate_source_provenance` (`validate_all.py:538-544`) — the shapes already
   bless it (C3, 405 errors). (b) Decide whether `dcterms:subject` on a Chapter
   is a topic cluster or a EuroVoc subject and make the array rule and
   `ActTemporalShape` agree (C4, 3,021 errors).
   *Why it matters:* the gate is unreadable while 3,426 of 3,557 errors are
   noise; nobody can see the 131 real ones.
   *Effort:* S (C3) + M (C4). *Impact:* H.
   *Files:* `src/estleg/validate_all.py`, `shacl/estonian_legal_shapes.ttl`,
   `tests/test_validate_all.py`.

3. **Narrow the two `rdfs:domain` axioms that manufacture 66,740 phantom
   violations.** Drop or widen `estleg:celexNumber` → `EULegislation` and
   `estleg:decisionDate` → `CourtDecision` in `controlled_vocabulary.jsonld`
   (an `owl:unionOf` domain, or no domain at all, mirroring the project's
   existing "prefer `sh:nodeKind sh:IRI`, omit `sh:class`" convention). Then
   extend `shacl/README.md`'s inference-safety rule from "no `rdfs:range` on
   stub-valued properties" to "no narrow `rdfs:domain` on a predicate shared by
   sibling classes".
   *Why it matters:* an auditor running the documented bucket gate gets 66,740
   violations that are not data defects and has no way to tell (C5). This is the
   whole credibility of the two-mode policy.
   *Effort:* M. *Impact:* H.
   *Files:* `krr_outputs/controlled_vocabulary.jsonld`, `shacl/README.md`,
   `shacl/estonian_legal_shapes.ttl`.

4. **Add a text-fidelity gate and make `legalText` non-optional where it exists
   today.** Two parts: (a) a `sh:minCount 1` on `estleg:legalText` for
   provisions of acts whose `contentStatus` is `structuredBody`, gated behind a
   measured coverage baseline (98.7% observed) so it cannot regress; (b) a
   sampling gate that re-fetches N provisions from riigiteataja.ee per release
   and diffs the text, recording the sample size and pass rate in the report.
   *Why it matters:* today the gate protects the machine-generated summary and
   leaves the authoritative statutory text optional (H1). No public body can
   certify a legal corpus on that basis.
   *Effort:* S for (a), L for (b). *Impact:* H.
   *Files:* `shacl/estonian_legal_shapes.ttl`, `src/estleg/validate_all.py`,
   a new `src/estleg/check_text_fidelity.py`.

5. **Require provenance and a snapshot date on every act root.** Add
   `sh:minCount 1` + `sh:nodeKind sh:IRI` for `dcterms:source` and
   `sh:minCount 1` for `estleg:kehtiv` on `ActTemporalShape`, after backfilling
   the ~15% of act roots that lack them.
   *Why it matters:* "which official document is this, and as of when" is the
   first question in any legal-data due-diligence checklist (H2).
   *Effort:* M (backfill dominates). *Impact:* H.
   *Files:* `shacl/estonian_legal_shapes.ttl`, `src/estleg/generate_all_laws.py`.

6. **Wire the three orphan checkers into the gate, or stop claiming their
   result.** Either call `check_tbox_consistency.py` and
   `check_numeric_identity_strings.py` from `validate_all.main()` (both are
   cheap and read-only), or remove `estleg:consistencyChecked: true` from
   `metadata.jsonld`.
   *Why it matters:* a published DCAT record asserting a check that nothing runs
   is the kind of finding that ends a certification (H3).
   *Effort:* S. *Impact:* M.
   *Files:* `src/estleg/validate_all.py`, `metadata.jsonld`,
   `docs/SCHEMA_REFERENCE.md`.

7. **Delete `docs/DUPLICATE_IDS_REPORT.md` or regenerate it from the corpus.**
   It currently reports a pytest fixture (H4) while the real corpus has ~50
   in-file duplicates.
   *Why it matters:* a wrong report is worse than no report; an auditor who
   spots this discounts every other document in `docs/`.
   *Effort:* S. *Impact:* M.
   *Files:* `docs/DUPLICATE_IDS_REPORT.md`.

8. **Add `kohtud` and `analytical` SHACL buckets and derive `BUCKETS` from
   `PUBLIC_LOAD_SUBDIRS`.** Also SHACL-validate deprecated legacy statutes,
   which ship in combined but leave `INDEX.json` and therefore leave
   `collect_laws`.
   *Why it matters:* two published subcorpora and one published node class have
   no RDFS-mode validation path at all (H5, H6), which is exactly the
   `#106`-class gap the sidecars bucket was created to close.
   *Effort:* M. *Impact:* M.
   *Files:* `src/estleg/shacl_validate_all.py`,
   `src/estleg/validate_seadusloome_sync.py` (docstring),
   `.github/workflows/validate.yml`.

9. **Make the two SHACL gates agree on what "fail" means.** Replace the
   fall-through `return 0` in `validate_seadusloome_sync.main` (`:614-616`) with
   an explicit `return 1` whenever `conforms` is False, and pass
   `allow_warnings=False, allow_infos=False` explicitly in all three gates so
   the documented "Warning ≡ Violation" policy is asserted in code, not
   inherited from a library default.
   *Why it matters:* a gate that can exit 0 on a non-conforming graph (M1) is
   not a gate, and the divergence from `validate_combined_standalone` makes the
   published policy unverifiable.
   *Effort:* S. *Impact:* M.
   *Files:* `src/estleg/validate_seadusloome_sync.py`,
   `src/estleg/shacl_validate_all.py`,
   `src/estleg/validate_combined_standalone.py`.

10. **Publish the shapes as a versioned, citable contract.** Give
    `estonian_legal_shapes.ttl` an `owl:Ontology` header with a
    `https://w3id.org/estleg/shapes/<version>` IRI and `owl:versionInfo`; add it
    as a `dcat:distribution`; add a second `dcterms:conformsTo` on
    `metadata.jsonld` pointing at it; and pin the version to
    `estleg_common.ONTOLOGY_VERSION` the way the CV header already is
    (`validate_all.py:1712-1719`).
    *Why it matters:* Seadusloome and RIK are supposed to treat the shapes as an
    interface contract, and today they cannot discover or pin one (M7).
    *Effort:* S. *Impact:* M.
    *Files:* `shacl/estonian_legal_shapes.ttl`, `metadata.jsonld`,
    `src/estleg/validate_all.py`.

11. **Attach validation evidence to every release, not just to failures.**
    Upload the SHACL report and the `validate_all` JSON summary
    unconditionally, and add a DQV (`dqv:hasQualityMeasurement`) or `prov:`
    block to `metadata.jsonld` recording gate name, version, timestamp and
    result. Change `validate.yml:377` from `if: failure()` to `if: always()`.
    *Why it matters:* "show me the validation report for the version I am
    ingesting" is a standard procurement question with no answer today (M8).
    *Effort:* S for the CI change, M for DQV. *Impact:* M.
    *Files:* `.github/workflows/validate.yml`, `metadata.jsonld`.

12. **Measure and publish the release-gate cost envelope, then make `--all`
    runnable.** Record wall time and peak RSS per bucket and for the full graph;
    if `--all` does not fit in ~16 GB, stream per-bucket and merge the reports
    rather than building one 7.1M-triple graph. Publish the numbers so a
    ministry can size a runner.
    *Why it matters:* `AGENTS.md:104` tells contributors to run a gate that CI
    avoids and that may need ~28 GB (M5); reproducing the vendor's validation is
    a normal acceptance condition.
    *Effort:* M. *Impact:* M.
    *Files:* `src/estleg/shacl_validate_all.py`, `.github/workflows/validate.yml`,
    `docs/VALIDATION_REPORT.md`.

13. **Extend `PROVISION_PARITY_FIELDS` to cover every SHACL-required field.**
    Add `estleg:partOfAct`, `rdfs:label`, `estleg:kehtiv`,
    `estleg:temporalStatus`, `estleg:hasSubsection`; better, derive the list by
    parsing `sh:minCount 1` paths out of the shapes graph so it cannot drift.
    *Why it matters:* the parity check advertises itself as a precheck for
    SHACL-sensitive drift and omits the fields SHACL actually requires (M4).
    *Effort:* M. *Impact:* M.
    *Files:* `src/estleg/validate_all.py`.

14. **Commit the generation manifest (or synthesise it) so
    `validate_act_coverage_reconciliation` runs.** Today `.gitignore:30`
    guarantees it never does (L1).
    *Why it matters:* it is the only check proving every published act traces to
    a supervised generator run — a provenance guarantee, not hygiene.
    *Effort:* S. *Impact:* M.
    *Files:* `.gitignore`, `src/estleg/generate_all_laws.py`.

15. **Move `_is_lfs_pointer` into `estleg_common` and delete the ~20 copies.**
    *Why it matters:* every copy is a place where a pointer-detection change
    turns a gate silently green (L2), and `AGENTS.md` already forbids the
    duplication.
    *Effort:* S. *Impact:* L.
    *Files:* `src/estleg/estleg_common.py` and ~20 callers.

---

## Open questions

1. **Is the red CI known and accepted, or has it gone unnoticed?** Eight
   consecutive failures including the release tag and the weekly cron suggests
   the signal is being ignored. If it is accepted, the accepted-defect set needs
   to be written down and pinned (the repo already has the right pattern —
   `EMPTY_SUBSTANTIVE_BASELINE = 2`, `DUPN_IRI_BASELINE = 0`,
   `EXPECTED_CLASS_FLOORS`) rather than left as an unexplained red badge.
2. **Was `docs/VALIDATION_REPORT.md`'s 2026-05-26 PASS real at the time?** If so,
   what regressed between then and the v1.0.0 cut? A `git log` bisect over
   `krr_outputs/` and the validator would answer this and is the fastest route to
   the 131 non-noise errors.
3. **Are the three warn-only staleness guards (`#366`, `#348`, `#384`) still
   deferred deliberately?** The `# TODO: promote to error()` markers at
   `validate_all.py:3095-3107` predate v1.0.0. The amendment-bloat warning fired
   in my run (9 chains, 20,937 events vs 20,721 unique). Shipping v1.0 with three
   known-stale artifact classes should be a stated release note, not a comment.
4. **Who is the intended reader of `shacl/README.md`?** It reads as a
   contributor note. If Seadusloome/RIK are meant to use the shapes as a
   contract, the severity policy, the inference caveat, and the per-surface
   failure semantics need a consumer-facing version.
5. **Is monthly accrual (`dcterms:accrualPeriodicity` = monthly) actually met?**
   `estleg:regulationSnapshotDate` is `2026-05-01` and `dcterms:modified` is
   `2026-08-19`, against today's `2026-09-03`. The RT staleness canary is green
   for acts; nothing checks the regulation snapshot against the advertised
   cadence.
6. **`estleg:semanticNodeCount: 170000` and `estleg:integrationLayerCount: 15`
   in `metadata.jsonld` are not in `DISTRIBUTION_COUNT_KEYS` or
   `metadata_stats()`.** Are they measured anywhere, or advertised only?
