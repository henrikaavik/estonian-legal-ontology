<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Policy-facing enrichment layers — review

Reviewer scope: sanctions, institutional competence, transposition, harmonisation,
draft impact, Õiguskantsler annotations, analytical overlay, eval harness.
Lens: usefulness for the Estonian public sector (JM / Riigikogu / ministries).

---

## Scope & method

Read end-to-end: `src/estleg/extract_sanctions.py` (1452 L),
`flag_unverified_sanctions_586.py` (395 L), `generate_transposition_mapping.py`
(1353 L). Four parallel sub-reviews covered
`extract_institutional_competence.py` (1887 L),
`generate_harmonisation_links.py` (1203 L), `extract_draft_impact.py` +
`generate_annotations.py` + `clean_annotation_boilerplate_619.py` (2643 L), and
`generate_analytical_overlay.py` + `eval_harness.py` + `eval/` (785 L). Orientation
from `AGENTS.md`, `docs/ARCHITECTURE.md`, `.github/CODEOWNERS`,
`eval/FITNESS_REPORT.md`.

Beyond reading, I ran empirical checks against the **shipped corpus**, not just the
code:

- Aggregated all 2,550 `estleg:Sanction` nodes across 294 files in
  `krr_outputs/sanctions/` (types, units, flags, property inventory).
- Re-ran the *current* `extract_sanctions()` over the *current* provision
  `legalText` for all 1,666 sanctioned provisions and diffed against the shipped
  sidecar.
- Hand-adjudicated six KarS provisions (§113 tapmine, §114 mõrv, §141 vägistamine,
  §118, §400, §44) against the real statutory text.
- Computed transposition gap-report arithmetic over the 3,114 directive nodes in
  `krr_outputs/eurlex/eurlex_directives_peep.json`.
- Counted `hasNoTransposition` / `hasNoCompetentAuthority` in the shipped
  `analytical_overlay.jsonld` and `combined_ontology.jsonld`.
- Inspected `krr_outputs/exports/*.csv` (the entire "policy analyst" surface).

No repository file was edited.

---

## Strengths

**S1. The sanction extractor is unusually disciplined regex engineering.** Nearly
every heuristic carries an issue number and a written rationale for why the naive
version was wrong. `extract_sanctions.py:398-411` rejects a euro amount whose
capture bridged a newline (`"500\n2024 EUR"` was a fabricated figure);
`:414-434` treats an Estonian comma as a decimal, not a thousands separator,
after a `"1 000,50"` → `100050 EUR` 100× inflation; `:373-390` sorts a
high-to-low year range and logs the swap; `:661-687` refuses to fire the 30-day
statutory arrest default when `aresti` sits in a past-passive condition clause
(`kohaldatud aresti`), which is a genuinely subtle Estonian-grammar guard. This is
better than most legal-NLP code.

**S2. Statutory fallbacks are explicitly marked.** `estleg:isStatutoryDefault`
(`extract_sanctions.py:1218-1219`) distinguishes "read from the provision" from
"filled in from the KarS general part". 433 of 2,550 nodes (17.0%) carry it. That
is the right instinct and the only working confidence marker anywhere in my scope.

**S3. Structured penalty fields make sanctions machine-comparable.**
`_parse_penalty_to_structured` (`:869-936`) maps free text onto a controlled unit
set (`years`/`months`/`days`/`daily_rates`/`fine_units`/`monetary`) with an
`xsd:decimal` amount and a currency. 2,345 of 2,550 nodes carry the structured
trio. `"life"` correctly returns `None` rather than a fake number.

**S4. Transposition mapping is built on the official EUR-Lex notification data,
not a heuristic.** `generate_transposition_mapping.py` queries the CELLAR SPARQL
endpoint for Estonia's national implementing measures
(`report source: https://publications.europa.eu/webapi/rdf/sparql`). This is the
authoritative MNE/NIM record. 720 measures fetched, 206 unique directives, and
2,599 of 3,114 directives (83.5%) carry `estleg:transpositionDeadline`. The raw
material for a real gap report is present.

**S5. The law-name matcher is precision-tuned with documented adversarial cases.**
`_contains_whole` (`:577-596`) is left-anchored only, with a written explanation
of why: it kills `teeseadus` ⊂ `raudteeseadus` (#265) while keeping the genitive
`liiklusseadus` ⊂ `liiklusseaduse`. `_law_matches_directive_subject` (`:530-560`)
blocks omnibus co-amendment noise (Maritime Safety wrongly tied to the Railway
Safety directive, #388). `_best_fuzzy_match` (`:598-640`) has a deterministic
tie-break so results never depend on dict order.

**S6. Estonian morphology in the competence extractor is real linguistic work.**
`_CASE_SUFFIXES_LONGEST_FIRST` (`extract_institutional_competence.py:204-217`)
covers twelve grammatical cases plus plural stems; `_strip_estonian_case`
(`:268-308`) only commits a strip when the residue ends in a known institutional
root, so it cannot truncate ordinary words; `_root_inflection_group` (`:637-644`)
blocks the `ametnik`/`ametlik`/`ametkond` derivational trap.

**S7. Abstention over fabrication, in three places.** `_select_granted_by`
(`extract_institutional_competence.py:925-963`) returns `None` on a tie rather
than picking a winner; `_resolve_kov_authority` (`:1000-1090`) refuses
cross-family municipality pairings and leaves 1,245 references unresolved and
counted; `flag_unverified_sanctions_586.py:44-47` enriches a label with the
issuing municipality "never fabricated — if the act node is not present, the
label is left unchanged".

**S8. Harmonisation scope honesty is enforced by a test, not just prose.**
`generate_harmonisation_links.py:2-14` states the layer is a comparative
neighbour-state index (LV/LT/FI/SE), explicitly *not* Estonian transposition, and
`tests/test_issue_557_harmonisation_scope.py:20-40` fails if Estonia is added to
`TARGET_COUNTRIES`. Encoding a scope limitation as a gate is good practice.

**S9. The eval harness names its own limits.** `eval_harness.py:206-213` labels
cross-reference resolution "edge precision, NOT extraction recall";
`eval/README.md:31-33` says outright it is "a report, not a gate";
`evaluate_gold_set` (`:400-412`) correctly counts a confident-wrong prediction as
both a false positive and a false negative.

**S10. Determinism is engineered throughout.** Pinned `BUILD_EVALUATION_DATE` /
`PINNED_RUN_TIMESTAMP` instead of wall clock, atomic `save_json` (tempfile +
`os.replace`, #376), sorted iteration before IRI-suffix assignment
(`extract_sanctions.py:1191-1199`, with a note that the previous order produced
different `_2` suffixes across runs), sorted output writers. The *code* is
deterministic even where the *shipped data* is not (see W1).

---

## Weaknesses / risks

### W1. CRITICAL — the shipped sanctions corpus is not reproducible from the code that is in the tree

I re-ran the current `extract_sanctions()` over the current `estleg:legalText` for
all 1,666 sanctioned provisions and diffed against `krr_outputs/sanctions/`:

| Comparison | Result |
| --- | --- |
| Provisions compared | 1,666 |
| Shipped set reproduced from full `legalText` | 1,178 (70.7%) |
| Shipped set reproduced from the 500-char `summary` | 1,263 (75.8%) |
| Sanction records the current code finds that are **absent** from shipped | 548 |
| Shipped records **not reproducible** from any current text | 26 |

Neither hypothesis fits cleanly, so the sidecar was built by an older code path
against older text. `classifier_text` (`estleg_common.py:1162-1172`) now prefers
`legalText` "so a 500-character `estleg:summary` preview cannot hide the rest of
the provision" (#368) — that fix was never re-run over the corpus.

The 548 missing records are not random. They are systematically the **legal-person
penalties**, which in Estonian drafting always sit in the last lõige of the §, i.e.
past the 500-character summary cut:

```
estleg:ESS_Par_159    shipped: fine 300 fine units   MISSING: fine 3200 EUR
estleg:ESS_Par_185    shipped: fine 200 fine units   MISSING: fine 2600 EUR
estleg:ERAKON_Par_12_17  shipped: fine 300 fine units  MISSING: fine 20000 EUR
```

Hand-adjudicated against the real statute, three of six sampled KarS provisions
are wrong in the shipped graph:

| Provision | Shipped graph | Actual statute |
| --- | --- | --- |
| KarS §141 vägistamine | max **5 years** | lg 2: **6–15 years** |
| KarS §400 konkurentsikuritegu | max **1 year** | lg 2: **1–3 years**; lg 3: 5–10% of turnover |
| KarS §118 | imprisonment only | lg 2: pecuniary punishment / sundlõpetamine |
| KarS §114 mõrv | max life, **no minimum** | **8–20 years** or life |

A published graph that states the maximum sentence for rape is five years is a
legal-safety failure regardless of how good the regex is. Severity: **critical**.

### W2. CRITICAL — `hasNoTransposition` / `hasNoCompetentAuthority` publish extraction gaps as findings about the world

Measured in the shipped artifacts:

| Flag | analytical_overlay.jsonld | combined_ontology.jsonld |
| --- | --: | --: |
| `estleg:hasNoTransposition` | 845 | 0 |
| `estleg:hasNoCompetentAuthority` | 604 | 604 |

These are bare booleans. The only caveat is an `rdfs:comment` on a single
`estleg:AnalyticalOverlay` node (`generate_analytical_overlay.py:244-249`), and
that comment is **not written on the `--patch-combined` path**
(`patch_combined_analytical:279-287`), so the 604 flags that reached
`combined_ontology.jsonld` — the primary consumer surface per
`docs/ARCHITECTURE.md` — carry no method, date, or provenance at all.

Meanwhile `eval/FITNESS_REPORT.md` records that only 55.5% of laws carry a
`competentAuthority` edge and 8.8% carry a `euDirective` edge. So the
overwhelming majority of these flags mean "we did not extract it", while the
predicate name asserts "there is none". `hasNoTransposition` on an EU directive
reads, to any ministry consumer, as an allegation that Estonia failed to
transpose. Severity: **critical**.

### W3. CRITICAL — a naive transposition gap report over this data would allege 2,368 false infringements

Computed over `krr_outputs/eurlex/eurlex_directives_peep.json`:

| Quantity | Count |
| --- | --: |
| Directive nodes | 3,114 |
| With `estleg:transpositionDeadline` | 2,599 (83.5%) |
| With `estleg:transposedBy` (an Estonian measure) | 206 (6.6%) |
| Deadline already past | 2,565 |
| Deadline past **and** no `transposedBy` | **2,368** |

The gap between 206 and 3,114 is not Estonian non-compliance. It is three
coverage holes stacked: 405 of 720 NIMs never matched a law; the 3,114 set
includes repealed and superseded directives (`estleg:temporalStatus` is `None` on
all 3,114 — only `estleg:inForce` is present, on 2,973); and Estonia transposes
heavily through subordinate legislation the matcher cannot see (W4). The layer is
one `FILTER NOT EXISTS` away from producing a politically explosive and wrong
number. Severity: **critical**.

### W4. HIGH — 405 of 720 official NIMs are unmatched, and they are overwhelmingly regulations, not laws

`total_measures_fetched: 720`, `total_matched: 294`, `total_unmatched: 405` in
`krr_outputs/reports/transposition_mapping.json`. Categorising the 50-row
`unmatched_sample`:

| Category | Count |
| --- | --: |
| Ministerial / government regulation (määrus, kord, nõuded, põhimäärus, vorm) | 48 |
| EUR-Lex administrative note ("MS does not consider NEM necessary") | 2 |
| Law that should have matched | 0 |

Examples: *"Elamislubade ja töölubade registri pidamise põhimäärus"*, *"Ohtlike
kemikaalide identifitseerimise, klassifitseerimise, pakendamise ja märgistamise
nõuded ning kord"*, *"Tava- ja kiirraudteesüsteemi koostalitluse tehniliste
kirjelduste kohaldamise kord"*.

The law index is built from root `*_peep.json` law files only, so every directive
transposed by a Vabariigi Valitsuse or ministri määrus is invisible — even though
`krr_outputs/regulations/` exists in the corpus. This is the single largest recall
hole in the transposition layer and it is fixable with existing data.

Separately, the two `"EM estime MNE non nécessaire"` rows are not failures at all:
that is Estonia formally notifying that no national measure is required. Counting
them as unmatched discards exactly the signal a gap report needs.

### W5. HIGH — the Õiguskantsler §-to-act cross-product

`provision_iris_for_acts` (`generate_annotations.py:1145-1169`) applies *every*
section number cited anywhere in an opinion to *every* act named anywhere in that
opinion. 19,913 of 24,844 annotation targets are `_Par_` nodes; mean 6.73 targets
per annotation, maximum 208. One shipped node asserts §112, §15 and §24 of the
Administrative Court Procedure Code **and** the Constitution **and** the
Imprisonment Act **and** the State Fees Act simultaneously. Most provision-level
annotation links in the corpus are wrong.

Compounding it, `run()` calls `build_annotations_for_opinion(op, law_index)`
**without** `provision_ids` (`:1618`), so a normal regeneration silently drops all
19,913 provision links; they exist only via the `--remint-sidecar` path
(`:1744-1748`).

### W6. HIGH — templated prose attributed to the Chancellor of Justice

`generate_annotations.py:1203` writes a fabricated Estonian sentence:

```python
parts.append("Õiguskantsleri seisukoht. Teemad: " + ", ".join(opinion.tags) + ".")
```

into `estleg:annotationText` under `estleg:annotationSource: "Õiguskantsler"`. It
does not appear in the 3,689 shipped nodes, but it is the live fallback for any
opinion without a usable PDF body.

Worse, `data/annotations/seed_annotations.json` is the **default** source
(`--scrape` is opt-in). Its own `_about` field says its `summary` is "a short,
substantive paraphrase of the opinion's interpretive position" — author-written
prose. It is emitted verbatim as `annotationText` (`:1201`) with
`annotationSource "Õiguskantsler"` (`:1263`) and
`rdfs:label "Õiguskantsleri seisukoht: …"` (`:1264`). Nothing in the node
distinguishes paraphrase from quotation. Running the script with no flags produces
exactly this. `docs/SCHEMA_REFERENCE.md:957` documents an **invented** quote as
the worked example.

Also: ~98% of shipped opinion bodies are silently truncated at 2,000 characters
(`_truncate_to_sentence`, `:1095-1104`, `:1205`) with only 20 carrying an ellipsis
and no `isExcerpt` or original-length predicate. A consumer sees a mid-document
cut as the whole opinion.

### W7. HIGH — competence is "mentioned near", not "is competent"

`detect_competence_type` runs once per provision
(`extract_institutional_competence.py:1275`) and the resulting type is stamped on
*every* institution named in that provision (`:1339-1340`). No proximity, no
syntactic role, no agent-verb binding. **24,371 of 30,360 bindings (80%) are type
`general`** — meaning no competence verb matched at all and the institution was
merely named.

Verified false positives: `estleg:EHS_Par_26_1` binds the municipality from the
purely descriptive genitive *"riigi või kohaliku omavalitsuse eriplaneeringu
alusel"*; `estleg:ARHIIV_Par_1`, a pure scope clause, likewise. The consultation
case is not merely unhandled — it is *asserted as correct* by a test:
`("kaitseministriga kooskõlastatult", "kaitseminister")` at
`tests/test_extract_institutional_competence.py:2352`. Being consulted is not
being competent.

Conversely the standard Estonian passive delegation formula
`kehtestatakse sotsiaalministri määrusega` (a genuine regulation-making power)
types as `general` because only the active `kehtestab` is in the pattern list.

### W8. HIGH — institution identity does not join to any official state register

`data/wikidata_institutions.json` carries only `qid`, `label`, `sitelinks` (77
entries). A repo-wide grep finds no `registrikood`, no X-tee member code, no
Riigi- ja KOV asutuste register identifier anywhere in code or data. The output
joins to Wikipedia, not to state data — so budget, personnel or X-tee joins are
impossible.

Worse, ten QIDs are duplicated across distinct slugs and `owl:sameAs` (`:1487-1493`)
makes those OWL-identical: `Institution_keskkonnaministeerium ≡
Institution_kliimaministeerium` (both `wd:Q16408655`);
`justiitsministeerium ≡ justiits_ja_digiministeerium`; three agriculture
ministries collapse onto one QID. Four slugs point at *concepts*:
`Institution_linn owl:sameAs wd:Q515` ("city"), `Institution_kohus → Q41487`
("court"), `peaminister → Q14212`.

### W9. HIGH — 77% of institution→provision edges are dropped

`_APPLIES_TO_PROVISION_CAP = 50` (`:1115`, applied `:1507`). Measured: 84 of 340
Competence nodes are truncated, discarding **23,498 of 30,360**
`appliesToProvision` edges. `appliesToProvisionCount` preserves the number but not
the links, so "which provisions does this ministry enforce?" is answerable for 23%
of them.

### W10. HIGH — `eval/FITNESS_REPORT.md` is two releases stale and now contradicts itself

Re-running the harness produces different numbers on every headline metric:

| Metric | Committed report | Regenerated |
| --- | --: | --: |
| sanctions coverage | 18.1% | 21.2% |
| competentAuthority | 55.5% | 49.9% |
| act temporalStatus | 65.6% | 99.9% |
| provisions | 39,082 | 39,545 |
| crossref edges | 28,207 | 35,671 |
| inline sanction definitions | 0 | 3,583 |
| ontology version | 0.11.0 | 1.0.0 |

The report claims to be "Objective, reproducible" (`FITNESS_REPORT.md:5`). The
narrative is hardcoded around the number (`render_markdown:310-315`), so the
regenerated file literally reads "a single `*_peep.json` still carries **3583**
inline sanction definitions (they live in the sidecar…)" — asserting a gap the
number says is closed. **The coverage figures the review brief was given as ground
truth are wrong.** The harness is not in `.github/workflows/validate.yml`, so
nothing detects the drift.

### W11. HIGH — nothing in this scope has a measured precision, and there is no gold set

Grepping `eval/`, `tests/`, `data/` for `gold|ground_truth|manual_review|verified`
turns up exactly one gold file: `eval/gold_sets/targetGroup.json`, a 260-byte
template with `"items": []`. `eval/README.md:47-49` concedes that Cycle 2 measured
layers were "31–35% wrong (#576/#577)" and it was never instrumented. Every
accuracy claim in these layers is unmeasured. `_has` is
`return bool(node.get(prop))` (`eval_harness.py:78-79`) — coverage, not
correctness.

The "100.0% cross-reference resolution" headline is tautological: `known_ids` is
built from the same files that yield `crossref_targets` (`:117-121` vs `:161-165`)
and `extract_cross_references.py:737` states unresolved citations are emitted
*without* `citationTarget`. The metric can only ever be ~100%.

### W12. MEDIUM-HIGH — `provenanceUnverified` means something much narrower than it reads

`flag_unverified_sanctions_586.py` stamps `estleg:provenanceUnverified` only when
a **municipal-regulation** sanction targets a provision that carries no
`legalText` anywhere in the corpus (`:29-40`). It is not a general "we are not
confident in this penalty" marker. Its shipped count is **0 of 2,550** — so every
sanction in the published graph reads as verified, including the §141 error in W1.
A policy consumer will read absence of the flag as verification.

### W13. MEDIUM-HIGH — sanctions carry no provenance, no confidence, no subject, and no evidence span

Property inventory across all 2,550 shipped `estleg:Sanction` nodes:

```
@id, @type, estleg:sanctionType, estleg:applicableProvision, rdfs:label,
estleg:maxPenalty / minPenalty, estleg:{max,min}PenaltyAmount / Unit / Currency,
estleg:isStatutoryDefault
```

Absent: `dcterms:source` to riigiteataja.ee, `prov:wasDerivedFrom`, the matched
text span, an extraction-method marker, a confidence score, and — most
consequentially for legal safety — **any indication of whom the sanction applies
to**. Estonian penalty provisions routinely set one penalty for a natural person
and a different one for a legal person; both land on the same § node as two
undifferentiated `fine` records with no `estleg:sanctionSubject`. The corpus is
built for a §-level graph while Estonian sanctions are lõige-level, so
sub-provision attribution is lost across the board.

### W14. MEDIUM-HIGH — sanction amounts are not comparable across laws

988 of 2,550 sanctions are in `fine_units` (trahviühik), 588 in `monetary` (EUR),
297 in `daily_rates` (päevamäär), 333 in `years`, 139 in `days`. There is no
trahviühik→EUR or päevamäär→EUR conversion anywhere in the codebase. Since
`_monetary_score` (`extract_sanctions.py:1039-1053`) only scores `monetary`
sanctions, the severity index assigns a monetary score of **0.0 to every one of
the 988 misdemeanour fines**. "Which laws impose the heaviest fines" — a core
public-sector question — is not answerable.

### W15. MEDIUM-HIGH — the entire CSV surface is a one-law demo

`krr_outputs/exports/` is the only tabular output in the repo:

| File | Data rows |
| --- | --: |
| `laws.csv` | 1 |
| `sanctions.csv` | 2 |
| `provisions.csv` | 200 |
| `citations.csv` | 133 |
| `court_decisions.csv` | 281 |

Every row is from `abipolitseiniku_seadus`. `serialize_tabular.py:19` confirms
this is deliberate ("so a bare `--out krr_outputs/exports` run stays [small]").
There is no institutions CSV, no competences CSV, no transposition CSV, no gap
report, no per-ministry view, and no dashboard artifact. A policy analyst's only
options today are a 64 MB LFS JSON-LD file, a SPARQL endpoint, or an MCP tool.

### W16. MEDIUM-HIGH — CODEOWNERS does not protect any file that exists

Every safety-critical path in `.github/CODEOWNERS` points at `/scripts/`:

```
/scripts/extract_sanctions.py                       @henrikaavik
/scripts/generate_transposition_mapping.py          @henrikaavik
/scripts/extract_institutional_competence.py        @henrikaavik
```

Those paths exist, but they are 256-byte compatibility shims:
`scripts/extract_sanctions.py` is `runpy.run_module("estleg.extract_sanctions")`
(#472). The 58 KB implementation is `src/estleg/extract_sanctions.py`, which
matches only the default `*` rule. The "mandatory legal-correctness review" gate
described in the CODEOWNERS header **does not fire on any change to the actual
sanction, competence, transposition or harmonisation logic.** Trivial to fix,
material in effect.

### W17. MEDIUM — general-part definitions are emitted as imposed sanctions

All 14 sanction nodes in `sanctions_karistusseadustik_osa1.json` (KarS üldosa) are
false positives. KarS §45 defines the *term* of imprisonment, §44 defines
pecuniary punishment, §48 defines arrest, §47 defines the fine unit, §66/68/70/73
concern probation and parole. None is an offence. The graph asserts:

```
Sanction_KARIST_2_Osa1_Par_45_imprisonment  max = life
Sanction_KARIST_2_Osa1_Par_4_imprisonment   max = life
Sanction_KARIST_2_Osa1_Par_47_fine          max = 32000 EUR
```

A query for "which provisions carry life imprisonment" returns §4, §22¹, §45,
§53, §77 and §83² — every one of them wrong.

### W18. MEDIUM — the turnover-percentage fine is silently replaced by a wrong default

KarS §400 lg 3 reads *"karistatakse rahalise karistusega 5 kuni 10 protsenti
juriidilise isiku käibest"*. The extractor has no turnover-percentage pattern, so
`extract_pecuniary` (`:734-753`) fires on the words `rahalise karistus` and stamps
the KarS §44 natural-person ceiling of **500 daily rates**. For a large
undertaking the real maximum is tens of millions of euros. The
`isStatutoryDefault` flag is set, which is honest, but the emitted number is wrong
by orders of magnitude rather than merely absent. The same gap applies to
`sundlõpetamine`, `konfiskeerimine`, `väljasaatmine`, `ärikeeld` and
`tegevusloa kehtetuks tunnistamine` — none is in the extractor's vocabulary.

### W19. MEDIUM — draft impact cannot name a single amended provision

`extract_draft_impact.py` resolves only to an act node via `prefer_act_iri`
(`:470-489`); there is no `§`/`lg` parsing in its 863 lines. Everything is derived
from the EIS **RSS title string** — no seletuskiri, no draft body, no attachment
is ever fetched. `classify_change_type` (`:191-198`) is pure regex over that
title. Coverage: **1,162 of 22,832 drafts (5.1%)** carry any `amendsLaw`. Grep for
`HÕNTE|mõjude hindamine|impactAssessment` across `src`, `scripts`, `shacl`, `docs`
returns nothing. There is no mõjuvaldkond vocabulary, no target group, no cost
estimate. The layer answers "which pending bills name this act" — agenda tracking,
not impact assessment. `resolve_law_name` (`:399-467`) has three silently
collapsing match tiers and records which one fired nowhere.

### W20. MEDIUM — harmonisation has a live crash-and-destroy path

`pick_harmonisation_law_file` returns `None` for an empty `law_files`
(`generate_harmonisation_links.py:588-589`), but the drop-guard at `:943` reads
`if law_entry["files"] and anchor_file is None` — false when `files == []`. The row
falls through, is never recorded in `skipped_laws`, and reaches `:1058` where
`if not law_iri:` discards **the whole directive** even though other acts resolved
fine, then exits 1 at `:1198`. The current `transposition_mapping.json` contains
exactly two such rows. Because Step 0 (`:869-898`) has already `rmtree`'d the
sidecar directory and stripped `harmonisedWith` from every peep before the network
sweep, that run destroys committed data. Additionally `harmonises` is written from
the anchor file only (`:942-950`) while `harmonisedWith` is written to every file
(`:1112-1118`), so the two directions disagree — and Seadusloome runs
`inference=none`, so no reasoner repairs it.

### W21. MEDIUM — institution temporal validity is collapsed, not modelled

`data/institution_aliases.json` silently rewrites predecessors into successors at
`extract_institutional_competence.py:375-378`: a 2005 provision naming
Maanteeamet becomes an assertion about Transpordiamet, a body created in 2021, and
the original mention is discarded. Only 1 of 117 institution files carries any
temporal relation. Kliimaministeerium (created 2023),
Justiits- ja Digiministeerium and the MKM reorganisation carry no `validFrom` /
`validTo` / `replaces`. "Who was competent in 2015?" is unanswerable, and
"who is competent now?" is answered from text that may predate the reorganisation.

### W22. MEDIUM — 90% of the analytical overlay is unevaluated similarity candidates

`krr_outputs/reports/similarity_index.json` declares
`relation_semantics: "candidate"`, `quality_evaluation.status: "not_evaluated"`,
`threshold: 0.3`, and `pairs_truncated_by_cap: 1,476,327` (92% of candidates
discarded by a top-5 cap). `similarity_node`
(`generate_analytical_overlay.py:124-136`) forwards only a hardcoded
`SIMILARITY_MODEL = "keyword_jaccard"` and the score. 130,459 of the overlay's
144,596 nodes ship in the public RDF as scored facts with none of that context.
`inboundCitationCount` is raw in-degree with no denominator or age control, so
KarS and TsÜS will top any "importance" ranking for structural reasons.

### W23. MEDIUM — competence extraction has a self-reinforcing registry and an out-of-order pipeline hazard

`_load_canonical_institutions` (`:1108-1153`) builds its allowlist *from the files
the previous run wrote*. `juhtministeerium` ("lead ministry", a role word) and
`uhisamet` are permanently blessed while 369 genuinely new institutions were
dropped this run with no record of which. `GENERIC_INSTITUTION_SLUGS`
(`:416`) is declared but never used in the extractor, so `Institution_vald` (3,046
provisions) and `Institution_kohus` (1,627) ship as if they were authorities.
Separately, `process_law_file` unconditionally pops `competentAuthority` from
**every** node including act roots (`:1406`), destroying the #508 act-root
rollups written at step 2 of `run_all_integration.py` when competence runs at
step 12.

### W24. MEDIUM — SHACL does not constrain what matters

`estleg:SanctionShape` (`shacl/estonian_legal_shapes.ttl:1485-1530`) requires only
a label; all penalty constraints are optional (`:1530`, "All optional: a Sanction
whose free-text penalty couldn't be [parsed]"). Nothing checks that
`minPenaltyAmount <= maxPenaltyAmount`, that a `monetary` unit has a currency, or
that an amount is plausible for its unit. Meanwhile the `institutionType` `sh:in`
list (`:1614-1618`) omits `"minister"`, which
`preferred_institution_type` (`extract_institutional_competence.py:434-438`) emits
for 24 nodes — a live violation confirmed with pyshacl, in a directory listed in
`SIDECAR_DIRS`.

### W25. LOW-MEDIUM — no accuracy disclaimer anywhere

Grepping `README.md`, `docs/*.md` and `LICENSE*` for
`disclaimer|not legal advice|no warranty|for information only` returns nothing.
The project publishes criminal penalties, competent-authority assignments and
transposition status with no statement that they are machine-extracted and
unverified. `CONTRIBUTING.md` promises legal-correctness review; W16 shows the
gate does not fire.

### W26. LOW — miscellaneous

- `krr_outputs/reports/kov/extract_institutional_competence_coverage.json` is
  git-tracked and contains `wall_time_seconds`, `items_per_second`,
  `peak_memory_mb` — churn on every run, against `AGENTS.md`.
- `institutions_by_provision_count` in the public report keys on the raw inflected
  match (`:1705`): `"teadusministrile": 1395`, `"Finantsinspektsioonilt": 960`.
  `institution_types` (`:1711-1714`) is a dict comprehension keyed on type, so it
  reports one arbitrary name per type, currently
  `"ministry": "Transpordi- ja Sideministeeriumiks"`.
- `docs/VALIDATION_REPORT.md:95` claims 13,402 annotation nodes from 4,052
  opinions; the shipped count is 3,689.
- `_CELEX_ALLOWED` (`generate_harmonisation_links.py:87`) is a character
  allowlist, not a grammar — `"foo"` passes. A real CELEX regex already exists at
  `generate_eu_legislation.py:114` and is not reused.
- 307 `*_peep.json` files still carry stale `estleg:affectedBy` because
  `--drafts-only` skips both the clearing and the write pass
  (`extract_draft_impact.py:564`, `:741`), while the report claims
  `law_files_with_inverse_links: 0`.

---

## Improvement ideas

Ordered by public-sector value per unit of effort.

### 1. Regenerate the sanctions sidecar and add a reproducibility gate
**What.** Re-run `extract_sanctions.py` over the current corpus and commit the
result; the #368 `legalText` fix has never been applied to shipped data. Then add
a CI check that re-extracts a sample of provisions and fails if the sidecar
disagrees with the code.
**Why it matters.** This alone corrects 548 missing sanction records, including
the legal-person penalties, and removes the published claim that the maximum
sentence for rape is five years. Nothing else in this list matters if the shipped
data does not match the code.
**Effort S / Impact H.** Files: `krr_outputs/sanctions/*`, `.github/workflows/validate.yml`, `tests/`.

### 2. Rename the gap flags to coverage vocabulary and carry the caveat into combined
**What.** Rename `estleg:hasNoTransposition` → `estleg:noTranspositionEdgeInCorpus`
and `estleg:hasNoCompetentAuthority` → `estleg:competentAuthorityNotExtracted`.
Stamp `estleg:derivedBy` and `estleg:analyticalMethod` on each flagged node,
including on the `--patch-combined` path.
**Why it matters.** 845 directives and 604 acts currently carry what reads as a
finding of legal fact. A ministry quoting `hasNoTransposition` is alleging
infringement on the basis of an extraction gap. The rename makes the claim true
without losing the signal.
**Effort M / Impact H.** Files: `src/estleg/generate_analytical_overlay.py:139-189,226-238,279-287`, `shacl/estonian_legal_shapes.ttl`, `docs/SCHEMA_REFERENCE.md`.

### 3. Index state and ministerial regulations in the transposition matcher
**What.** Extend the law index in `generate_transposition_mapping.py:731-757` to
cover `krr_outputs/regulations/` (Vabariigi Valitsuse and ministri määrused), and
promote the `"MS does not consider NEM necessary"` rows to an explicit
`estleg:transpositionStatus "no_measure_required"` rather than counting them as
unmatched.
**Why it matters.** 48 of the 50 sampled unmatched NIMs are regulations. This is
the largest recoverable recall gain in the transposition layer and it uses data
already in the repo. It moves the match rate materially above 41% and shrinks the
false gap in idea 4.
**Effort M / Impact H.** Files: `src/estleg/generate_transposition_mapping.py`, `krr_outputs/reports/transposition_mapping.json`.

### 4. Ship a transposition gap CSV with an explicit unknown column
**What.** One row per directive: CELEX, title, `transpositionDeadline`, in-force
flag, EE measure yes/no, matched act, source (`eurlex-nim` / `ntm-asserted` /
`none`), and a three-valued status — `transposed` / `no_measure_required` /
`no_evidence_in_corpus` — never a bare `not_transposed`. Add the LV/LT/FI/SE
counts already computed by the harmonisation layer.
**Why it matters.** This is the artefact Justiitsministeerium actually wants, and
2,599 deadlines plus 206 confirmed measures are already on disk. The three-valued
status is what stops it becoming the 2,368-false-infringement report of W3.
**Effort S / Impact H.** Files: `src/estleg/generate_transposition_mapping.py:1146-1179`, `krr_outputs/reports/`.

### 5. Parse the normitehniline märkus to get the authoritative transposition statement
**What.** New extractor over the in-text `direktiiv NN/NN/EÜ` citations in the NTM
block at the end of each act (measured: 135 root peeps, 223 distinct directive
numbers, currently entirely unused). Convert `92/43/EMÜ` → `31992L0043` and emit
`estleg:transposesDirectiveAsserted` with `prov:wasDerivedFrom` the § node.
**Why it matters.** The NTM is what an Estonian lawyer cites — it is the law's own
statement of what it transposes. Having both it and the EUR-Lex notification lets
you *diff* asserted against notified, which is the single most valuable
transposition-monitoring product this corpus could ship, and it is a real check on
Estonia's own notifications.
**Effort M / Impact H.** Files: new `src/estleg/extract_ntm_directives.py`, `shacl/`, `docs/SCHEMA_REFERENCE.md`.

### 6. Add sanction subject, evidence span, and source provenance
**What.** Emit `estleg:sanctionSubject` (`natural_person` / `legal_person`,
detected from `juriidiline isik` in the governing lõige),
`estleg:matchedText` with character offsets, `prov:wasDerivedFrom` the provision,
and `dcterms:source` the riigiteataja.ee URL. Attach to the lõige node where one
exists rather than flattening to the §.
**Why it matters.** Today a §-level node carries a 300-fine-unit natural-person
penalty and a 3,200 EUR company penalty as two indistinguishable `fine` records.
Anyone advising on corporate exposure gets the wrong number, and nobody can check
the extraction against the source without leaving the graph.
**Effort M / Impact H.** Files: `src/estleg/extract_sanctions.py:1206-1240`, `shacl/estonian_legal_shapes.ttl:1485`, tests.

### 7. Split "mentioned" from "competent" and add the missing delegation forms
**What.** Bind competence only when the institution is the syntactic subject of a
competence verb within a clause window; emit everything else as
`estleg:mentionsInstitution`. Add an explicit consultation blocklist
(`kooskõlastatult`, `arvamuse`, `nõusolekul`, `ettepanekul`) and retire the test
assertion at `tests/test_extract_institutional_competence.py:2352`. Add the
passive `kehtestatakse …ministri määrusega` form as a `regulation` competence.
**Why it matters.** 80% of current bindings are bare mentions published under a
predicate named "competent authority". A wrong competent authority sends a citizen
or a business to the wrong regulator. This also fixes the inverse error, where a
genuine regulation-making delegation is downgraded to `general`.
**Effort L / Impact H.** Files: `src/estleg/extract_institutional_competence.py:1265-1345`, `shacl/`, tests.

### 8. Attach registrikood to institutions and fix the sameAs collisions
**What.** Add `registrikood` (and X-tee member code where applicable) to
`data/wikidata_institutions.json`, emit as `estleg:registryCode`. Drop the four
concept-level QIDs (`linn`, `kohus`, `peaminister`, `vald`), de-duplicate the ten
shared QIDs, and downgrade genuinely ambiguous ones from `owl:sameAs` to
`skos:closeMatch`. Add a duplicate-QID gate to `validate_all.py`.
**Why it matters.** Registrikood is what makes this joinable to the state budget,
personnel registers and X-tee. Without it the layer is an interesting graph; with
it, it is infrastructure. The `keskkonnaministeerium ≡ kliimaministeerium`
identity is also simply false and will confuse any reasoner.
**Effort M / Impact H.** Files: `data/wikidata_institutions.json`, `src/estleg/extract_institutional_competence.py:400-434,1487-1493`, `src/estleg/validate_all.py`.

### 9. Fix CODEOWNERS to point at the implementation
**What.** Change every `/scripts/<name>.py` entry to `/src/estleg/<name>.py`, or
add the `src/estleg/` paths alongside. Optionally add a CI check that every
CODEOWNERS path resolves to a file over 1 KB, so the shim trap cannot recur.
**Why it matters.** The mandatory legal-correctness review described in the
CODEOWNERS header currently fires on nothing. Every finding in this report sits in
a file that no reviewer is auto-assigned to. This is a two-line change protecting
the highest-risk code in the repository.
**Effort S / Impact H.** Files: `.github/CODEOWNERS`.

### 10. Quarantine synthetic prose and mark excerpts in the annotations
**What.** Move the seed `summary` and the `"Õiguskantsleri seisukoht. Teemad: …"`
template out of `estleg:annotationText` into `estleg:editorialNote` with
`annotationSource "estleg (paraphrase)"`. Add `estleg:isExcerpt` and
`estleg:sourceTextLength`, capture the office reference number in
`clean_annotation_boilerplate_619.py:107-110` before it is stripped, and add a
SHACL rule that `annotationSource "Õiguskantsler"` requires
`annotationSourceUrl`. Fix the invented example at `docs/SCHEMA_REFERENCE.md:957`.
**Why it matters.** Attributing paraphrased or templated Estonian to the
constitutional review body is a misattribution risk with a named institution
attached, and the default no-flag invocation produces exactly that.
**Effort S / Impact H.** Files: `src/estleg/generate_annotations.py:1201-1270`, `data/annotations/seed_annotations.json`, `shacl/`, `docs/SCHEMA_REFERENCE.md`.

### 11. Pair each cited § with its own act in the annotations
**What.** Replace `extract_section_numbers` + `provision_iris_for_acts` with a
single span regex `(<inflected law name>|<abbrev>)\s*§\s*(\d+…)` plus a proximity
window; fall back to act-level when no act anchors the citation. Wire
`provision_ids` into the normal `run()` path (`:1618`), not just
`--remint-sidecar`.
**Why it matters.** 19,913 provision-level annotation targets, mean 6.73 and max
208 per annotation, are mostly wrong. "What has the Õiguskantsler said about this
provision" is one of the highest-value queries in the corpus and it currently
returns noise.
**Effort M / Impact H.** Files: `src/estleg/generate_annotations.py:1123-1169,1618`, tests.

### 12. Build one real gold set and publish a measured precision
**What.** Seeded stratified sample (n≈300 per layer, powered for ±5pp at 95%), two
independent annotators, Cohen's κ reported, adjudication of disagreements, a
riigiteataja.ee citation per item. Extend `evaluate_gold_set` to `rglob` (it
currently globs only root peeps, `eval_harness.py:390`), report per-label
precision/recall/F1 with Wilson intervals, and record the sampling seed.
**Why it matters.** `eval/README.md:47-49` already concedes that measured layers
ran 31–35% wrong. Until one gold set exists, every number this project publishes
is coverage, and no ministry can adopt an unmeasured layer for anything that
matters. Start with sanctions — it is the smallest population (2,550) and the
highest consequence.
**Effort L / Impact H.** Files: `eval/gold_sets/`, `src/estleg/eval_harness.py:375-425`, `eval/README.md`.

### 13. Ship a real tabular export and per-ministry views
**What.** Extend `serialize_tabular.py` beyond the single-law demo: full
`sanctions.csv` (2,550 rows with subject, unit, EUR-normalised amount, RT URL),
`institutions.csv`, `competences.csv`, `transposition_gap.csv`, `drafts.csv`.
Add a per-ministry rollup keyed on the institution registrikood from idea 8.
**Why it matters.** The current exports directory contains one law and two
sanctions. A policy analyst cannot use JSON-LD, a 64 MB LFS file, or SPARQL.
Everything else in this list is invisible to the intended audience until there is
a table.
**Effort M / Impact H.** Files: `src/estleg/serialize_tabular.py`, `krr_outputs/exports/`, tests.

### 14. Normalise fine units and daily rates to EUR
**What.** Add a dated conversion table (trahviühik and päevamäär values with
`validFrom`) and emit `estleg:maxPenaltyEurEquivalent` alongside the native unit,
never replacing it. Feed it into `_monetary_score`.
**Why it matters.** 988 misdemeanour fines currently score 0.0 on the severity
index because only EUR sanctions are scored. Cross-law sanction comparison — the
main reason a ministry would open this layer — is impossible without it.
**Effort S / Impact M.** Files: `src/estleg/extract_sanctions.py:1039-1053`, `data/`.

### 15. Suppress general-part sanction extraction and add the missing sanction types
**What.** Skip provisions in a `Üldosa` structural parent for sanction extraction,
or require a sentencing frame (`… eest – karistatakse …`) rather than a bare
keyword. Add `sundlõpetamine`, `konfiskeerimine`, `väljasaatmine`, `ärikeeld`,
`tegevusloa kehtetuks tunnistamine`, and a turnover-percentage fine pattern
(`N kuni M protsenti … käibest`) so KarS §400 lg 3 is not stamped with the
natural-person default.
**Why it matters.** All 14 KarS general-part sanction nodes are false positives,
and a life-imprisonment query returns six wrong provisions. The turnover fine is a
wrong number rather than a missing one, which is worse.
**Effort M / Impact M.** Files: `src/estleg/extract_sanctions.py:461-753,1163-1180`, tests.

### 16. Regenerate the fitness report, derive its prose from its numbers, and put it in CI
**What.** Re-run the harness and commit; replace the hardcoded retrievability-gap
narrative (`render_markdown:310-315`) with text conditional on the measured value;
rename the cross-reference metric to `dangling_edge_rate` with the disclosure that
the extractor drops unresolved targets by construction; add a `--check` mode that
regenerates to a temp dir and diffs, annotating in CI before it blocks.
**Why it matters.** The committed report is two releases stale on every headline
number and its conclusion now contradicts its own figure. It is the document a
prospective public-sector adopter reads first.
**Effort S / Impact H.** Files: `src/estleg/eval_harness.py:266-336`, `eval/FITNESS_REPORT.md`, `.github/workflows/validate.yml`.

### 17. Fix the harmonisation crash-and-destroy path and the asymmetric inverse
**What.** Pick the primary law as the first entry *with* an IRI rather than
`estonian_laws[0]`, log zero-file rows into `skipped_laws`, drive the
`harmonisedWith` write from the anchor file so both directions agree, and build
Step 0 into a temp directory swapped on success instead of `rmtree`-then-refetch.
**Why it matters.** A re-run today discards a directive, exits 1, and has already
destroyed the committed sidecar and twelve peep edges before failing. The
asymmetric inverse is visible on the published `inference=none` surface.
**Effort S / Impact M.** Files: `src/estleg/generate_harmonisation_links.py:869-898,943-972,1056-1067,1112-1118`, tests.

### 18. Ingest the seletuskiri for provision-level draft impact
**What.** Fetch the EIS per-draft attachments and parse the canonical Estonian
amending formula `"§ 14 lõiget 2 muudetakse ja sõnastatakse järgmiselt"` into
`estleg:amendsProvision → estleg:<ABBREV>_Par_14_Lg2`. Layer the HÕNTE
mõjuvaldkonnad on top as a SKOS scheme populated from the mandated
"Mõjude analüüs" chapter headings.
**Why it matters.** This is the only change that makes the draft layer answer
"what would change in the law if this passed", which is the question a ministry
asks. Today it answers "which bills mention this act", for 5.1% of drafts.
**Effort L / Impact H.** Files: new ingester, `src/estleg/extract_draft_impact.py`, `shacl/`, `docs/`.

### 19. Add an accuracy disclaimer and a per-layer method note
**What.** A short statement in `README.md` and in `metadata.jsonld` that these
layers are machine-extracted, unverified, and not a substitute for the Riigi
Teataja text, with a per-layer note giving the extraction method and any measured
precision once idea 12 lands.
**Why it matters.** The project publishes criminal penalties and competent-authority
assignments with no such statement anywhere. It costs nothing and it is the
difference between a research corpus and an implied authority.
**Effort S / Impact M.** Files: `README.md`, `krr_outputs/metadata.jsonld`, `docs/`.

### 20. Tighten SHACL on the values that carry legal consequence
**What.** Add `minPenaltyAmount <= maxPenaltyAmount`, require a currency when the
unit is `monetary`, add plausibility ranges per unit (imprisonment ≤ 20 years
determinate per KarS §45, arrest ≤ 30 days per §48, daily rates ≤ 500 per §44),
and add `"minister"` to the `institutionType` `sh:in` list so the 24 current
violations are either legalised or fixed.
**Why it matters.** The SanctionShape currently requires only a label. A shape
that encodes the KarS general-part ceilings would have caught the §45 life-
imprisonment false positive and any future inverted range automatically.
**Effort S / Impact M.** Files: `shacl/estonian_legal_shapes.ttl:1485-1530,1614-1618`, tests.

---

## Open questions

1. **Was `krr_outputs/sanctions/` ever regenerated after #368?** The diff says no.
   If the sidecar predates the `legalText` fix, the same question applies to
   `institutions/`, `harmonisation/` and `annotations/` — how many shipped
   sidecars are older than the code that claims to produce them, and is there a
   provenance stamp anywhere that would reveal it?

2. **Is `shacl_validate_all.py --all` currently green?** The `institutionType
   "minister"` violation is live on 24 nodes in a directory listed in
   `SIDECAR_DIRS`. Either the gate is not running over that directory or it is
   red and tolerated.

3. **Are the 405 unmatched NIMs genuinely regulation-level, corpus-wide?** I could
   only categorise the 50-row sample. If the full list is ~95% regulations, idea 3
   is the highest-value single change in the transposition layer; if it is 50%,
   the ceiling is lower and the NTM route (idea 5) matters more.

4. **Is 720 EE NIMs plausible for 20 years of membership?** That looks low. Is the
   CELLAR query missing measures attached via a different CDM predicate, or is
   Estonia's notification record genuinely that thin? This caps the recall of both
   the transposition and harmonisation layers, and it is worth knowing which.

5. **Who signs off legal correctness?** `CONTRIBUTING.md` promises a mandatory
   review, `.github/CODEOWNERS` routes it to paths that are shims, and no gold set
   exists. Is there budget for a legal-domain reviewer, and would a smaller
   verified subset (say the 203 laws with sanctions) be a more credible v1 than
   full unverified coverage?

6. **Is the analytical overlay's public exposure intentional?** `analytical` is in
   `PUBLIC_LOAD_SUBDIRS` (`estleg_common.py:262-277`) and serialised as a named
   graph, but `docs/ARCHITECTURE.md:42-46` omits it from the three documented load
   surfaces. One of the two is wrong, and it determines whether the 845
   `hasNoTransposition` flags are published or internal.

7. **Republishing Õiguskantsler letters.** The extracted bodies retain redacted-but-
   real complainant context ("Pöördusite xx.2012. a minu poole avaldusega… Teie
   laste"). Is that covered by `docs/DATA_PROTECTION.md`?
