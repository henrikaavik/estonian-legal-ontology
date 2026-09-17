<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Heuristic classifier layers — public-sector fitness review

## Scope & method

Read in full or in the load-bearing parts:

- `src/estleg/classify_eurovoc.py` (1,141 lines), `data/eurovoc_domain_mapping.json`, `docs/EUROVOC_OVERLAY.md`
- `src/estleg/classify_deontic.py` (675), `src/estleg/classify_target_group.py` (845)
- `src/estleg/extract_legal_concepts.py` (1,910, focused on the cross-match logic)
- `src/estleg/generate_similarity_index.py` (2,006, focused on scoring + KOV pass), `src/estleg/generate_embedding_index.py` (227)
- `src/estleg/tag_vocabulary_labels.py`, `src/estleg/skos_concept_scheme_609.py`, `src/estleg/materialize_target_group_concepts_609.py`
- `eval/README.md`, `eval/FITNESS_REPORT.md`, `eval/gold_sets/targetGroup.json`, `src/estleg/eval_harness.py`
- `docs/STABILITY.md`, `docs/ARCHITECTURE.md`, `AGENTS.md`, `shacl/estonian_legal_shapes.ttl`

Beyond reading code I measured the **shipped corpus**, because several defects are only visible in the data:
`krr_outputs/reports/eurovoc_classification.json`, `..._sample.json`, `deontic_classification_report.json`,
`target_group_report.json`, `similarity_report.json`, `krr_outputs/concepts/concepts_combined.jsonld`,
`krr_outputs/reports/kov/*_coverage.json`, `krr_outputs/reports/integration/logs/*.log`, and the peeps themselves.

**Method for each layer:** keyword/regex matching over concatenated JSON-LD text fields. No TF-IDF, no
embeddings, no LLM, no statistical model anywhere in the four classification layers. Only the KOV act-level
similarity pass uses TF-IDF cosine. Answers to the direct questions are in **Open questions** at the end.

---

## Strengths

**The deontic classifier is genuinely good Estonian NLP, not a keyword list.**
`classify_deontic.py:264-292` guards the polysemous modals `peab`/`tuleb`/`peavad` by requiring a nearby
`-ma/-da/-ta` infinitive in *either* direction (`classify_deontic.py:238-262`, issue #329), so "peab
registrit" ("keeps a register") does not score as an obligation. Negation is handled with a 3-token
backward window (`classify_deontic.py:224-228`), and cues that already encode negation (`ei tohi`,
`ei või`) are exempted from that guard via an explicit `KIND_NEGATIVE` kind (`classify_deontic.py:52-57`).
Penal provisions suppress the judicial-discretion `võib` (`classify_deontic.py:296-300`), and a leading
main-clause permission is boosted over an obligation cue trapped in a trailing `kui` condition
(`classify_deontic.py:341-380`). Definition sections are diverted to `NormType_Definition` before scoring
(`classify_deontic.py:413-427`) so deontic verbs inside a definition body cannot steal the label. This is
the one layer whose error analysis is visibly iterative and linguistically informed.

**Precision gates on EuroVoc are documented with their evidence.**
`classify_eurovoc.py:334-390` records, per domain, *which* Estonian stem misfired and inside *which*
unrelated word (`töö` inside `töötukassa`, `vesi` inside `vesinik`, `kunst` inside `kunstlik`, `nõue`
inside `nõuetele`), and raises that domain's distinct-keyword gate to 2. The per-domain gates are then
republished in the report (`classify_eurovoc.py:1013-1025`) so a consumer sees the threshold that produced
each assignment. That is better provenance discipline than most heuristic layers ship with.

**The EuroVoc descriptor ids are real and independently verified.**
Issue #421 replaced fabricated codes with verified Publications Office descriptors;
`data/eurovoc_domain_mapping.json` carries 43 entries with an `evidenceUrl` and a note recording the
verification method ("verified 2026-06-10 via Publications Office SPARQL endpoint … rdf:type thesaurus
ThesaurusConcept"). Where real EuroVoc has only a microthesaurus and no descriptor, the nearest-descriptor
substitution is named in a code comment (`classify_eurovoc.py:127-129`, `:139-141`, `:150-152`). A ministry
can audit every minted IRI.

**Determinism is taken seriously and is real.**
Every report stamps `BUILD_EVALUATION_DATE` rather than a wall clock (`estleg_common.py:1549`), the
coverage sidecars stamp `PINNED_RUN_TIMESTAMP` (`estleg_common.py:1555`), the similarity sample seed is a
module constant (`generate_similarity_index.py:114`), and results are sorted by explicit keys
(`classify_eurovoc.py:566`, `classify_target_group.py:331`). I found no `datetime.now()`, no unseeded
shuffling in the classification path, and no set-iteration order leaking into output. Reruns over the same
corpus produce byte-identical artifacts.

**Runtime is not a constraint.** From the coverage sidecars: EuroVoc 102 s over 16,057 files
(`krr_outputs/reports/kov/classify_eurovoc_coverage.json`), deontic 70 s, concepts 173 s. The whole
heuristic tier is minutes, not hours. Any accuracy work has ample compute budget.

**`estleg:assertionConfidence` is applied consistently where it is applied.**
`estleg_common.py:1521-1533` takes the *minimum* of the applicable layer confidences, and I confirmed
100% coverage on a 300-file KOV sample (2,696 heuristic nodes, 2,696 carrying confidence) and on
1,125 of 1,195 root peeps. The separation of heuristic from official facts is real, even if the value is
too coarse (see below).

**The KOV similarity bucketing is a sound engineering answer to a scale problem.**
`generate_similarity_index.py:587-656` derives a regulation-type bucket from `estleg:titleNormalized`,
stripping trailing action nouns so "kooli põhimääruse muutmine" and "kooli põhimäärus" land in the same
bucket (#317), which collapses ~61M naive act pairs to ~380k intra-bucket comparisons.

---

## Weaknesses / risks

### 1. No precision or recall has ever been measured, for any layer. **Severity: critical**

`eval/gold_sets/targetGroup.json` is a template with `"items": []`. It is the **only** gold set in the
repository (`eval/gold_sets/` contains one file). The harness path works — `eval_harness.py:375-425`
computes precision/recall against a gold set — but it has nothing to score.

`eval/README.md:44-46` states the position plainly: "Cycle 2 measured several layers **31–35 % wrong**
(#576/#577) but the project never instrumented it." `krr_outputs/reports/similarity_report.json` says
`"quality_evaluation": {"status": "not_evaluated"}`. `krr_outputs/reports/eurovoc_classification.json`
says `"status": "tooling_available"` — tooling, not a result.

So the answer to "is precision/recall measured, against what gold set, published per layer" is: **no, none,
none.** A ministry cannot state an error bar for any label in this graph. Everything below is downstream
of this one gap.

### 2. EuroVoc precision is visibly poor, and the shipped sample proves it. **Severity: critical**

From `krr_outputs/reports/eurovoc_classification.json` over 13,747 classified acts:

| Domain | Acts tagged | Share | Distinct-keyword gate |
|---|--:|--:|--:|
| 527 constitutional-law | 9,049 | 65.8% | 1 |
| 68 local-government | 8,321 | 60.5% | 2 |
| 517 administrative-law | 4,356 | 31.7% | 1 |
| 557 labour-law | 3,410 | 24.8% | 2 |

`constitutional-law` fires on `["põhiseadus", "riigikogu", "president", "valitsus", "vabariig",
"riigikohus"]` at a gate of 1 (`classify_eurovoc.py:93-96`). "Vabariigi Valitsus" appears in the preamble
or citation line of nearly every Estonian regulation, so two thirds of the corpus is asserted to be about
constitutional law. That is not a subject classification; it is a detector for the phrase "the Government
of the Republic".

Two thirds of assignments are also cap-bound rather than evidence-bound: 6,552 of 13,747 acts (47.7%) hit
`MAX_DOMAINS_PER_LAW = 5` exactly (`classify_eurovoc.py:302`), so the top-5 cut by raw hit count — which
favours long documents and frequent short stems — is doing the discrimination.

The committed precision-review sample contains a clean worked example of failure.
`krr_outputs/reports/eurovoc_classification_sample.json`, act `estleg:Reg_1000668_Map`, "Liiklusmärkide ja
teemärgiste tähendused ning nõuded fooridele" — the road-signs and traffic-lights regulation. Its assigned
subjects, in rank order:

```
2464 defence-policy          matched: sõjaväe, kaitseväe
2162 public-order            matched: politsei
5899 health-care             matched: haigla, meditsiini
2924 scientific-research     matched: uurim
```

The top subject on a traffic-signs regulation is **defence policy**, because the regulation defines road
signs for military and hospital destinations. `transport-policy` (2494) is absent — it is the one domain
carrying `MIN_HITS_OVERRIDES = 3` (`classify_eurovoc.py:308-311`), so the correct label was gated out while
four wrong ones passed. A second sample entry, `estleg:PLR_Map` (Patent Law Treaty ratification act), gets
only `constitutional-law` and no `intellectual-property`.

For Bürokratt or a policy analyst filtering "show me transport regulations", this layer is worse than no
layer, because a wrong subject is silently wrong while a missing subject is visibly missing.

### 3. `skos:closeMatch` between legal concepts is edit-distance, and the results are wrong in ways that matter. **Severity: critical**

`extract_legal_concepts.py:587-664` pairs concept terms by Levenshtein distance ≤ 2
(`MAX_DIST = 2` at `:621`), via a SymSpell deletion-variant index, then
`extract_legal_concepts.py:1701-1718` writes each surviving pair as a bidirectional `skos:closeMatch`.
Edit distance is orthographic. SKOS `closeMatch` asserts that two concepts are similar enough to be used
interchangeably in retrieval. Those are unrelated properties.

`krr_outputs/concepts/concepts_combined.jsonld` ships 279 unique pairs / 558 triples. A random sample of
30 (seed 3) includes:

```
laev (ship)          <-> laps (child)
arst (doctor)        <-> arve (invoice)
kana (chicken)       <-> küla (village)
vald (municipality)  <-> varud (stocks)
pere (family)        <-> tera (grain)
muru (lawn)          <-> tulu (income)
meede (measure)      <-> teade (notice)
kolmas isik (third party) <-> kolmas riik (third country)
teenuse saaja        <-> toetuse saaja   (service recipient / benefit recipient)
teenuse taotleja     <-> toetuse taotleja
biogaas              <-> biomass
```

`kolmas isik` ↔ `kolmas riik` is the one that should stop a release: "third party" and "third country" are
both defined terms in Estonian data-protection law with entirely different legal consequences, and the
graph now asserts they are interchangeable. `teenuse saaja` ↔ `toetuse saaja` is the same failure in social
law.

The pairs that *are* correct are all orthographic variants of one concept — `wi_fi` ↔ `wifi`,
`teenuse_osutaja` ↔ `teenuseosutaja`, `sademevee_kanalisatsioon` ↔ `sademeveekanalisatsioon`,
`r_eklaamikandja` ↔ `reklaamikandja`, `projekti_meeskond` ↔ `projektimeeskond`. Those are not near-matches
between two concepts; they are one concept that the extractor failed to deduplicate. The layer conflates
deduplication (which should merge nodes and add `skos:altLabel`) with semantic near-equivalence (which
edit distance cannot establish).

`skos:closeMatch` is also **not listed in the Heuristic tier** in `docs/STABILITY.md:11-14` — only
`dcterms:subject`, `estleg:normativeType`, `estleg:targetGroup` and `estleg:semanticallySimilarTo` are.
A consumer reading the stability contract has no warning that these arcs are generated.

### 4. There is no curation loop. Every regeneration destroys every correction. **Severity: critical**

`classify_deontic.py:509-524` opens with an unconditional clearing pass that deletes
`estleg:normativeType` and `estleg:dutyHolder` from **every node in every peep** before reclassifying.
`classify_target_group.py:471-480` overwrites `estleg:targetGroup` whenever the classifier's opinion
differs from what is stored, and deletes it outright when the classifier finds nothing.
`classify_eurovoc.py:606-616` does the same under `--write-peeps` via
`clear_eurovoc_subjects_from_file`.

So if a Justiitsministeerium lawyer corrects a `NormType_Permission` to `NormType_Prohibition`, the next
`run_all_integration.py` erases it with no record that a human ever disagreed. There is no override file,
no `prov:wasAttributedTo` on a corrected node, no exclusion list, no "human-reviewed, do not overwrite"
flag anywhere in the four classifiers. This is the single largest barrier to a public body adopting the
layers, because it means the only way to fix a wrong label is to patch the keyword table and re-run the
whole corpus.

### 5. Two heuristic outputs promised by the docs are not in the shipped corpus, and one is a stale duplicate. **Severity: high**

**The EuroVoc overlay does not exist.** `docs/EUROVOC_OVERLAY.md:15-19` states "`classify_eurovoc` writes
the overlay by default", and `run_all_integration.py:249-252` declares the step writes
`eurovoc/eurovoc_overlay.jsonld`. On disk `krr_outputs/eurovoc/` does not exist, and `git ls-files` shows
no overlay file. EuroVoc subjects live on the peeps instead (`krr_outputs/kodakondsuse_seadus_peep.json`,
node `estleg:KodS_Map`). Since the pipeline does **not** pass `--write-peeps`, the clearing pass never
runs: the next pipeline run will write a fresh overlay while leaving the stale peep subjects in place,
producing two disagreeing sources for `dcterms:subject` that both merge into combined
(`estleg_common.py:384`). The same is true of `krr_outputs/similarity/kov_similarity_index.json`, declared
at `run_all_integration.py:359-361` and absent from disk and from git.

**`estleg:targetGroupConcept` is a stale duplicate of `estleg:targetGroup`.**
`materialize_target_group_concepts_609.py:47-53` maps the five enum tokens to the same IRIs that
`classify_target_group.py:38-44` now emits directly since #460. I checked 200 root peeps: 8,220 nodes carry
`estleg:targetGroup`, 2,924 carry `estleg:targetGroupConcept`, and on all 2,924 nodes carrying both the
values are **identical** — 0 differences. So the predicate adds nothing and is present on only 36% of
eligible nodes. Neither #609 script is wired into `run_all_integration.py` (grep for "609" returns
nothing), so `classify_target_group` at step 11 rewrites `targetGroup` without touching
`targetGroupConcept`, and the two can drift apart on the next run. Worse, `docs/STABILITY.md:11-13` places
`estleg:targetGroupConcept` in the **Additive** tier ("New properties may appear; existing ones stay")
while `estleg:targetGroup` is **Heuristic**. A consumer who trusts the contract and queries the Additive
predicate is querying a partial, stale mirror of keyword output.

### 6. `estleg:assertionConfidence` is a per-layer constant, not a confidence. **Severity: high**

`estleg_common.py:1502-1504` hard-codes `DEONTIC = "0.70"`, `TARGET_GROUP = "0.65"`, `EUROVOC = "0.55"`.
`heuristic_confidence_for_node` (`:1521-1533`) then stamps the **minimum** applicable constant on the node.

Three consequences. (a) The value carries no information about the individual assertion: on
`estleg:KodS_Map` the confident `migration` tag (three distinct keywords: `välismaalane`, `kodakondsus`,
`elamisluba`) and the spurious `education` tag sit under one `0.55`. (b) Taking the minimum conflates
layers: a provision with both a deontic label and a subject reports 0.55, so a consumer filtering
`> 0.6` silently drops good deontic labels. (c) The numbers are not calibrated against anything — no gold
set exists to calibrate them against (finding 1), and `eval/README.md:44` says the true error rate on some
layers was 31–35%, i.e. accuracy ~0.65–0.69, which does not match the assigned 0.55/0.65/0.70 ordering.

`shacl/estonian_legal_shapes.ttl` constrains the *shape* of the heuristic predicates
(`:300-306` normativeType nodeKind, `:370-382` targetGroup `sh:in` the closed enum, `:26-32` the EuroVoc
IRI pattern) but never requires `estleg:assertionConfidence` to be present. A consumer cannot rely on its
presence to filter heuristics out.

### 7. Published quality reports do not match any traceable run. **Severity: high**

The committed reports disagree with the committed integration logs of the same layers:

| Metric | `reports/deontic_classification_report.json` | `reports/integration/logs/classify_deontic.log` |
|---|--:|--:|
| Obligation | 23,673 | 21,738 |
| Prohibition | 6,843 | 4,912 |

| Metric | `reports/target_group_report.json` | `.../classify_target_group.log` |
|---|--:|--:|
| provisions_classified | 147,432 | 147,879 |
| files_changed | 7,448 | 5,329 |
| dutyHolder_coverage | 1.0 | 0.8618 |

`reports/similarity_report.json` advertises
`"generic_keyword_doc_frequency_cap": 0.45` while the code has been 0.05 since #606
(`generate_similarity_index.py:189`), and reports `"generic_keywords_dropped": []`, confirming the filter
never fired in the committed run. `similarity_index.json` (Aug 19) and `similarity_report.json` (Aug 18)
were written by different runs. A public body doing due diligence cannot cite these reports as describing
the data it downloaded.

### 8. Target-group multi-label capping is arbitrary, and `isik` swamps the citizen class. **Severity: high**

`classify_target_group.py:414-419`: when the body-text fallback yields ≥3 groups, it keeps
"the two highest-priority addressees" — but priority is `TARGET_GROUP_ORDER`
(`classify_target_group.py:29-35`), a fixed declaration order `(citizen, business, public_body, official,
ngo)`, not evidence strength. `citizen` is first, so it survives essentially every cap. A provision
matching public_body + official + ngo keeps public_body and official and silently loses ngo, regardless of
how many cues each had.

Meanwhile the `citizen` list contains a bare `isik` pattern (`classify_target_group.py:123-126`) — one of
the most frequent nouns in Estonian legal text — and `public_body` contains bare `valitsus`
(`:165`), `asutus` (`:186`) and `kool` (`:179`). The #460 weak-cue rule (`:229-247`) drops `citizen` only
when a business or public_body cue is *also* present and no strong citizen cue exists; it does nothing
when `isik` is the only match. Result: `citizen` 82,795 and `public_body` 81,415 across 147,432 classified
provisions (`krr_outputs/reports/target_group_report.json`), 78,698 of them multi-valued. For an
administrative-burden analysis — the stated purpose in the module docstring — labelling 56% of all
provisions as addressing citizens is not a usable signal.

### 9. The embedding surface is a placeholder, and the default embedder is not semantic. **Severity: medium**

`generate_embedding_index.py` defaults to `HashingEmbedder` (`:186-190`), a 64-dimensional signed
bag-of-tokens hashing trick (`:41-58`) over whitespace-split casefolded tokens (`:87-88`). There is no
stemming, no lemmatisation, no subword handling. Estonian has fourteen cases; `töötaja` and `töötajale`
hash to different buckets with no relation. As a *semantic* index this is strictly worse than the TF-IDF it
was meant to complement. The real path (`sentence_transformer_embedder`, `:63-84`) requires the
`embeddings` extra (`pyproject.toml:26-28`) and is never invoked by any pipeline step
(no reference in `run_all_integration.py`). The default input is `chunks.sample.jsonl` — the committed
sample, not the full corpus (`:180-184`) — and both outputs are gitignored (`.gitignore:44-45`). Nothing
ships. The module is honest about this in its docstring ("Optional semantic search … Tests inject a
deterministic hashing embedder"), but `pyproject`'s `embeddings` extra and the CHANGELOG entry read as a
shipped capability. **The embedding surface is a placeholder.**

### 10. The similarity index does not answer the KOV question, and 92% of its candidates are discarded arbitrarily. **Severity: medium**

Asked directly: *can it find KOV regulations on the same topic as a given state regulation?* **No.**

- The provision-level pass covers **zero** KOV: `similarity_report.json` reports
  `provisions_by_type.kov: 0` and `candidate_files_by_type.kov: 0`.
- The act-level KOV pass compares KOV acts only **within a title-derived bucket**
  (`generate_similarity_index.py:587-656`), so it finds KOV↔KOV peers, not KOV↔state peers.
- The only cross-layer edge is derived from each KOV act's existing `estleg:issuedUnder` link
  (`generate_similarity_index.py:1201`), i.e. it restates a fact already in the graph rather than
  discovering a topical match. The docstring is explicit that the reciprocal state→KOV edge was
  **deliberately not written** (`generate_similarity_index.py:38-53`), so there is no edge to traverse from
  the state regulation outward.
- And the output file `krr_outputs/similarity/kov_similarity_index.json` is not on disk (finding 5).

Separately, the provision pass keeps 130,459 pairs and truncates 1,476,327 by
`MAX_SIMILAR_PER_PROVISION = 5` (`generate_similarity_index.py:167`) — 91.9% of candidates discarded by an
arbitrary cap. 23,682 of the survivors (18.2%) score exactly 1.0, i.e. identical keyword sets; the
integration log shows what these are: `§ 224. Vahetu sunni kasutamine <-> § 43³. Vahetu sunni kasutamine`
and treaty-accession boilerplate ("§ 1. Protokolliga ühinemine"). Scoring runs on `estleg:summary` only
(`similarity_report.json` `algorithm.source_fields`), not `legalText`, despite #368 having moved the other
layers off the 500-char summary — which is why 118,122 law provisions are excluded against 36,181 analysed.

### 11. The concept layer is not reproducible from a clone. **Severity: medium**

`extract_legal_concepts.py:48` reads source XML from `data/riigiteataja/`, but
`.gitignore:24-25` excludes `data/riigiteataja/*.xml` and `**/*.xml`. The working tree has **one** XML file
left (`karistusseadustik.xml`), and even the author's own coverage sidecar records
`"skip_reasons": {"no_xml": 1188}` (`krr_outputs/reports/kov/extract_legal_concepts_coverage.json`). On a
fresh clone the layer regenerates almost nothing. The committed `concepts_combined.jsonld` is therefore an
artifact no external party can reproduce or verify — which for a public body is the difference between an
auditable dataset and an opaque one.

### 12. EU acts carry no subject at all, while official EuroVoc is one query away. **Severity: high (as an opportunity)**

`iter_peep_files` (`estleg_common.py:1708-1737`) covers root laws, `regulations/riik/` and
`regulations/kov/` only. It does not include `eurlex/`, so `classify_eurovoc` never sees EU acts. I
confirmed the consequence: all 3,115 nodes in `krr_outputs/eurlex/eurlex_directives_peep.json` carry
`celexNumber`, `eliIdentifier`, `owl:sameAs` into `publications.europa.eu` — and **no** `dcterms:subject`,
no `eli:is_about`, no EuroVoc, in any node.

Meanwhile `src/estleg/eurlex_common.py:22` already holds a working, hardened POST client for the
Publications Office SPARQL endpoint (the one used to verify the descriptor ids in #421). Every CELEX
document in Cellar carries its **official, editorially-assigned** EuroVoc subjects. For the EU half of the
corpus the authoritative answer is free, and the repository already has the client to fetch it.

---

## Improvement ideas

**1. Adjudicate a 300-item gold set covering four layers, and gate on it.**
*What:* 300 items — 100 EuroVoc act-subject, 100 deontic provision, 100 targetGroup provision — adjudicated
against Riigi Teataja text by a lawyer, plus a 50-pair concept `closeMatch` yes/no set. Extend
`eval_harness.evaluate_gold_set` to take a directory of gold sets and emit a per-layer precision/recall
table into `eval/FITNESS_REPORT.md`. Publish that table in the dataset's DCAT description.
*Why it matters:* this is the precondition for a ministry to use any label in a decision, for
`assertionConfidence` to mean anything, and for tuning to be anything other than anecdote-driven. Right now
the project's own README concedes 31–35% error on some layers and cannot say which. It is also the only way
to know whether the fixes below actually help.
*Effort:* M (harness change is S; the adjudication is the cost, and it is the point).
*Impact:* H.
*Files:* `eval/gold_sets/*.json` (new), `src/estleg/eval_harness.py`, `eval/FITNESS_REPORT.md`,
`eval/README.md`.

**2. Take official EuroVoc from EUR-Lex for all EU acts, and stop heuristic-classifying anything with a CELEX id.**
*What:* a new step querying `publications.europa.eu` for `eurovoc:` subjects per CELEX via the existing
`eurlex_common.sparql_query`, writing `dcterms:subject` + `eli:is_about` onto the 3,115 `eurlex/` nodes with
`estleg:assertionConfidence` **omitted** (it is an official fact, not an assertion) and a
`dcterms:source` pointing at Cellar.
*Why it matters:* it converts a third of the corpus's subject metadata from unmeasured guesswork to
authoritative Publications Office data, at zero editorial cost. It makes the graph interoperable with how
EUR-Lex and the Publications Office already classify the same acts — the precondition for EU-level reuse
and for a credible EuroVoc claim in the DCAT-AP record. It also gives the *only* available ground truth for
evaluating the Estonian-side heuristic: any Estonian act transposing a directive should share subjects with
it, which is a free consistency check.
*Effort:* S (the SPARQL client, retry wrapper and CELEX sanitiser already exist).
*Impact:* H.
*Files:* new `src/estleg/fetch_eurovoc_official.py`, `src/estleg/run_all_integration.py`,
`src/estleg/eurlex_common.py`, `docs/EUROVOC_OVERLAY.md`.

**3. Cut the EuroVoc domain table to the domains that survive measurement, and drop the tautological ones.**
*What:* delete `527 constitutional-law` (65.8% of the corpus — the same reasoning that already removed the
generic "Law" domain, per the comment at `classify_eurovoc.py:87-89`), and raise `68 local-government`,
`517 administrative-law` and `557 labour-law` to a hit-count gate proportional to document length rather
than the current absolute count. Replace `MAX_DOMAINS_PER_LAW = 5` ranking-by-raw-hits with ranking by
hits-per-1000-tokens, and cap at 3.
*Why it matters:* a subject that applies to two thirds of everything cannot filter anything, and 47.7% of
acts currently hit the top-5 cap, so the cap — not the evidence — is choosing the labels. Fewer,
better-measured domains is exactly what a ministry needs: eight subjects with published precision beat 43
with none. This directly answers "would a public body be better served by fewer, better-measured layers" —
yes, and this layer is the clearest case.
*Effort:* S to change, M including the re-measurement from idea 1.
*Impact:* H.
*Files:* `src/estleg/classify_eurovoc.py`, `data/eurovoc_domain_mapping.json`,
`tests/test_classify_eurovoc.py`, `krr_outputs/reports/eurovoc_classification.json`.

**4. Replace edit-distance `skos:closeMatch` with orthographic-variant merging.**
*What:* stop emitting `skos:closeMatch` from `_bucketed_close_match_pairs`. Keep the pairing machinery, but
route it to a *deduplication* decision: when two terms differ only by whitespace, hyphenation or an
underscore artefact (`teenuse osutaja` / `teenuseosutaja`, `wi-fi` / `wifi`, `r eklaamikandja` /
`reklaamikandja`), merge them into one `estleg:Concept` and keep the loser as `skos:altLabel`. Emit nothing
at all for the remaining pairs. If a genuine near-match relation is wanted later, derive it from shared
definition text or shared defining acts, not from string distance.
*Why it matters:* the graph currently asserts that "third party" and "third country" are interchangeable,
and that a doctor is close to an invoice. For an AI assistant doing query expansion these arcs actively
generate wrong answers, and they are invisible to a consumer because `skos:closeMatch` is not even listed
in the Heuristic tier. Deleting 279 pairs costs nothing real: the correct ones are spelling variants that
belong on one node anyway.
*Effort:* S.
*Impact:* H.
*Files:* `src/estleg/extract_legal_concepts.py`, `tests/test_extract_legal_concepts.py`,
`krr_outputs/concepts/concepts_combined.jsonld`, `docs/STABILITY.md`.

**5. Build a correction-survives-regeneration loop.**
*What:* a single tracked `data/heuristic_overrides.jsonl` of
`{node, property, value, reviewer, source_url, date, issue}` records. Each classifier consults it before
writing: an overridden `(node, property)` is skipped by the clearing pass and by the write, and the node
gets `prov:wasAttributedTo` plus `estleg:assertionConfidence 1.0`. Add a SHACL warning shape for an
override whose cited `source_url` is absent.
*Why it matters:* this is the difference between a dataset a ministry can *use* and one it can only *read*.
Today a subject-matter expert who spots a wrong `NormType_Prohibition` has no way to fix it that survives
`run_all_integration.py` — `classify_deontic.py:509-524` deletes the whole layer on every run. An override
file also converts expert review into a growing, citable asset that doubles as gold-set input for idea 1,
and it gives Riigi Teataja / RIK a concrete contribution channel.
*Effort:* M.
*Impact:* H.
*Files:* `data/heuristic_overrides.jsonl` (new), `src/estleg/estleg_common.py`,
`src/estleg/classify_deontic.py`, `src/estleg/classify_target_group.py`,
`src/estleg/classify_eurovoc.py`, `shacl/estonian_legal_shapes.ttl`, `docs/STABILITY.md`.

**6. Make `assertionConfidence` per-assertion and derived from the evidence the classifiers already compute.**
*What:* stop stamping a node-level minimum. For EuroVoc, reify each subject as a small overlay node
carrying its own confidence derived from `matched_keywords` count and hit density (the classifier already
computes both — `classify_eurovoc.py:534-556`). For deontic, derive it from the winning cue's weight and
margin over the runner-up (both already in `score_text`). Calibrate the mapping against the gold set from
idea 1. Add a SHACL shape requiring `estleg:assertionConfidence` on any node carrying a Heuristic-tier
predicate.
*Why it matters:* a flat 0.55 on every subject tells a consumer nothing, and taking the minimum across
layers means a filter at 0.6 silently drops good deontic labels because the act also has a weak subject.
Per-assertion confidence is what lets Bürokratt show only high-confidence labels and lets an analyst sort a
review queue — it turns an unusable layer into a usable one without improving the classifier at all.
*Effort:* M.
*Impact:* H.
*Files:* `src/estleg/estleg_common.py`, `src/estleg/classify_eurovoc.py`,
`src/estleg/classify_deontic.py`, `shacl/estonian_legal_shapes.ttl`, `docs/SCHEMA_REFERENCE.md`.

**7. Resolve the overlay contradiction: one writer per predicate.**
*What:* decide whether `dcterms:subject` lives on peeps or in `krr_outputs/eurovoc/eurovoc_overlay.jsonld`,
then enforce it. If overlay: run `classify_eurovoc --write-peeps` once with a clear-only mode to strip the
stale peep subjects, commit the overlay, and add a `validate_all` check that no peep carries a EuroVoc
`dcterms:subject`. Same for `krr_outputs/similarity/kov_similarity_index.json`. Delete
`materialize_target_group_concepts_609.py` and `estleg:targetGroupConcept` (a proven byte-identical
duplicate of `estleg:targetGroup` on all 2,924 nodes carrying both), or wire the script into the pipeline
after step 11 — but do not ship a predicate in the Additive tier whose values are a partial stale mirror of
a Heuristic one.
*Why it matters:* `docs/EUROVOC_OVERLAY.md` and `run_all_integration.py` both describe files that are not
in the repository, and the next pipeline run will create a second, disagreeing source of truth for
`dcterms:subject` that both merge into combined. A consumer pinning `owl:versionIRI` and reading the
stability contract is being told something untrue. This is a credibility problem more than a data problem,
and credibility is what a public-sector adopter is actually buying.
*Effort:* S.
*Impact:* M.
*Files:* `src/estleg/classify_eurovoc.py`, `src/estleg/run_all_integration.py`,
`docs/EUROVOC_OVERLAY.md`, `docs/STABILITY.md`, `src/estleg/validate_all.py`,
`src/estleg/materialize_target_group_concepts_609.py`.

**8. Regenerate the layer reports from the shipped corpus, and add a staleness gate.**
*What:* a `validate_all` check that each heuristic report's headline counts match a recount over the corpus
on disk, failing on drift. Regenerate `deontic_classification_report.json`, `target_group_report.json` and
`similarity_report.json` from the current corpus so the published numbers match, and fix
`similarity_report.json`'s advertised `generic_keyword_doc_frequency_cap` (0.45 in the report, 0.05 in the
code since #606).
*Why it matters:* the reports are the only public evidence about layer behaviour, and today they disagree
with both the integration logs and each other. A ministry evaluating the corpus reads the report, not the
code. Given `BUILD_EVALUATION_DATE` already makes reruns byte-stable, this gate is cheap and permanent.
*Effort:* S.
*Impact:* M.
*Files:* `src/estleg/validate_all.py`, `krr_outputs/reports/*.json`,
`src/estleg/generate_similarity_index.py`.

**9. Fix target-group multi-label capping and the `isik` over-match.**
*What:* replace the declaration-order cap at `classify_target_group.py:414-419` with a cue-count ranking
(keep the two groups with the most distinct pattern matches, ties broken by `TARGET_GROUP_ORDER` for
determinism). Extend the #460 weak-cue rule so a lone bare `isik` never assigns `citizen` on its own,
mirroring what the EuroVoc layer already does with `MIN_DISTINCT_KEYWORDS_OVERRIDES`.
*Why it matters:* the module's stated purpose is administrative-burden analysis. Tagging 56% of provisions
as addressing citizens makes "how many obligations fall on businesses" unanswerable, which is the exact
question a ministry impact assessment asks. The cap is currently arbitrary in a way that systematically
favours `citizen` because it happens to be declared first.
*Effort:* S.
*Impact:* M.
*Files:* `src/estleg/classify_target_group.py`, `tests/test_classify_target_group.py`,
`tests/test_issue_460_target_group.py`.

**10. Either drop the embedding module or make it real.**
*What:* if it stays, default `--model` to `intfloat/multilingual-e5-small`, point `--chunks` at the full
`chunks.jsonl`, wire it into `run_all_integration.py`, and publish the vectors as a release asset. If it
stays a demo, say so in the README and remove the `embeddings` extra from `pyproject.toml` so it does not
read as a shipped feature.
*Why it matters:* a 64-dimensional hashing embedder over whitespace tokens is not semantic search in a
language with fourteen cases; it is worse than the TF-IDF it sits beside. Advertising an "embeddings" extra
that produces this invites an integrator to build on it and discover the problem in production.
*Effort:* S to remove, M to make real.
*Impact:* M.
*Files:* `src/estleg/generate_embedding_index.py`, `pyproject.toml`,
`krr_outputs/retrieval/README.md`, `.gitignore`.

**11. Make the KOV↔state topical query answerable.**
*What:* run the act-level TF-IDF pass across the `MunicipalRegulation` × `NationalRegulation`/`Law` product
(bounded by the same bucketing) and emit a symmetric `estleg:similarAct`, so a state regulation can be
traversed *outward* to comparable KOV regulations. Ship the resulting
`krr_outputs/similarity/kov_similarity_index.json`.
*Why it matters:* "which municipalities have a regulation on this topic, and how do they differ" is the
single highest-value question this corpus is uniquely positioned to answer, for both KOV associations and
ministries writing model regulations. Today it cannot be answered: the provision pass has zero KOV
coverage, and the only cross-layer edge restates the `issuedUnder` fact already in the graph. The docstring
records that the state-side inverse was rejected as risky; that trade-off is worth revisiting now that the
KOV clear/regen path is established.
*Effort:* M.
*Impact:* H.
*Files:* `src/estleg/generate_similarity_index.py`, `src/estleg/run_all_integration.py`,
`docs/ARCHITECTURE.md`.

**12. Commit or vendor the Riigi Teataja source XML the concept layer needs.**
*What:* either track `data/riigiteataja/*.xml` via LFS (consistent with the executed keep-LFS decision in
`docs/ARCHITECTURE.md`), or publish it as a release asset and have `extract_legal_concepts.py` fetch it,
or fall back to the peep `legalText` when XML is absent.
*Why it matters:* the layer currently skips 1,188 files as `no_xml` even in the author's own tree, and on a
fresh clone regenerates essentially nothing. An external reviewer cannot verify or reproduce
`concepts_combined.jsonld`, which for a public-sector dataset is the difference between auditable and
opaque. It also blocks anyone else from improving the layer.
*Effort:* M.
*Impact:* M.
*Files:* `.gitignore`, `.gitattributes`, `src/estleg/extract_legal_concepts.py`,
`docs/ARCHITECTURE.md`.

---

## Open questions

**What is the actual classification method?** Keyword and regex matching in all four classification layers,
with no statistical model. EuroVoc: substring/regex counting over 43 hand-written Estonian stem lists, with
a total-hit gate and a distinct-keyword gate (`classify_eurovoc.py:517-568`). Deontic: weighted regex cue
scoring with negation, infinitive and clause-position disambiguation (`classify_deontic.py:79-160`,
`:264-300`). Target group: regex lexicons per addressee class with a weak-cue demotion rule
(`classify_target_group.py:52-247`). Concepts: XML section detection plus Levenshtein pairing
(`extract_legal_concepts.py:587-664`). Only the KOV act-level similarity pass uses TF-IDF cosine
(`generate_similarity_index.py:874-941`). No LLM is used anywhere.

**Is there a gold set beyond targetGroup?** No, and `targetGroup` itself is an empty template
(`eval/gold_sets/targetGroup.json` — `"items": []`). No layer has measured precision or recall.

**How would a ministry's subject-matter expert review and override a label?** They cannot, today. There is
no override mechanism, and `classify_deontic.py:509-524` deletes the entire deontic layer at the start of
every run. See finding 4 and idea 5.

**Is the EuroVoc mapping compatible with how Riigi Teataja / EUR-Lex classify the same acts?** Unknown and
untested — no cross-check exists. For EU acts the question is moot in the best possible way: official
EuroVoc is available from the Publications Office SPARQL endpoint that this repository already queries
(`src/estleg/eurlex_common.py:22`), and the 3,115 EU acts currently carry no subject at all. **Yes, official
EuroVoc should replace heuristics for EU acts** (idea 2). For Estonian acts, Riigi Teataja does not publish
a EuroVoc mapping, so the heuristic is the only option there — which makes measuring it (idea 1) and
shrinking it (idea 3) more important, not less.

**Is the similarity index useful for "find KOV regulations on the same topic as this state regulation"?**
No. Zero KOV coverage in the provision pass, KOV↔KOV only in the act pass, no state→KOV edge by design, and
the KOV output file is not in the repository. See finding 10 and idea 11.

**Is the embedding surface real or a placeholder?** A placeholder. Default embedder is a 64-dimensional
hashing trick, default input is the committed sample, output is gitignored, and no pipeline step invokes
it. See finding 9.

**What is the runtime and determinism?** Runtime is a non-issue: EuroVoc 102 s / 16,057 files, deontic 70 s,
concepts 173 s, all from the committed coverage sidecars. Determinism is genuinely good — pinned build
dates, pinned run timestamps, explicit sort keys, a seeded sample constant, and no wall-clock or unseeded
randomness in the classification path. Reruns over the same corpus are byte-identical. The caveat is that
`classify_eurovoc --emit-sample` defaults `--sample-seed` to `None` (`classify_eurovoc.py:805-810`), so an
ad-hoc sample write into the git-tracked `eurovoc_classification_sample.json` is not reproducible; the
committed one records `random_seed: 42`.

**Questions I could not answer from the repository:**

- Was there ever an adjudicated sample behind the "31–35% wrong" figure in `eval/README.md:44`, and does it
  survive in issues #576/#577? If so it is the fastest path to idea 1 — it would only need transcribing
  into the gold-set format.
- Why is the EuroVoc overlay declared in three places but present in none? Was the corpus last built from a
  pre-#463 checkout, or was the overlay deliberately not committed?
- Is the 0.55/0.65/0.70 confidence ordering based on any observation, or is it a guess? Nothing in the
  repository records a derivation.
