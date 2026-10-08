# Estonian legal-reasoning benchmark (`estleg-legal-reasoning` 0.1.0)

> **Status: repo side complete, deposit NOT cleared.** The builder, JSONL
> splits, schema, CMDI stub and rights manifest are in place (issue #727).
> The rights and personal-data positions are still DRAFT
> ([`DATA_RIGHTS.md`](DATA_RIGHTS.md), [`DATA_PROTECTION.md`](DATA_PROTECTION.md)),
> and the people-side steps in the [deposit checklist](#clarin-ee-deposit-checklist)
> belong to the maintainer. Do not deposit or advertise the set as cleared
> until those are closed.

The benchmark packages three things the corpus already holds — point-in-time
provision versions, Riigikohus → provision links and statutory cross-references —
as an Estonian-language evaluation set for LLMs (EstLLM has no legal data). It
targets the EKT 2027 open round and a CLARIN-EE deposit; the EKT 2026 targeted
round (28 Sep 2026) passed without an applicant.

## Files

| Path | What |
|---|---|
| `src/estleg/build_legal_benchmark.py` | Builder, validator and reference scorer (`score_predictions`) |
| `scripts/build_legal_benchmark.py` | CLI shim |
| `eval/benchmark/item.schema.json` | JSON Schema (draft 2020-12) for one JSONL line |
| `eval/benchmark/sample/` | Committed 200-items-per-task sample + `manifest.json` |
| `eval/benchmark/rights_provenance.json` | Per-task inputs, fields read / never read, rights layers, open items |
| `eval/benchmark/cmdi.xml` | CMDI 1.2 metadata stub for CLARIN-EE (profile id TO-VERIFY) |
| `tests/test_build_legal_benchmark.py` | Default-tier tests on a trimmed real fixture; `corpus` tests on the real build |

The full build is ~26 MB, above the ~5 MB budget for committed generated data,
so only the sample is committed. Rebuild the full set (≈35 s, no network, no
Git LFS inputs):

```bash
python3 scripts/build_legal_benchmark.py --out eval/benchmark/full
python3 scripts/build_legal_benchmark.py --sample-per-task 200 --out eval/benchmark/sample
```

Output is byte-identical for an unchanged corpus (verified by two builds and by
the `corpus` test that rebuilds the committed sample).

## Task definitions

Every item has: `id`, `task`, `split`, `group` (split key), Estonian `prompt`,
`answer_type`, `answers`, `distractors`, `source_iris`, `rt_citation`,
`source_url`, `snapshot` (version/date info) and `licence`. Choice tasks add
`choices` + `answer_index`; IRI tasks add human-readable `answer_labels`.

### (a) `point_in_time` — what did § n say on a date?

*Mis oli „Abieluvararegistri seadus“ § 1 tekst seisuga 31.03.2004?*

- **Source:** `estleg:ProvisionVersion` nodes in `krr_outputs/provision_versions/`
  (`versionText`, `versionValidFrom`, `versionValidTo`, `versionRedactionId`, `rtUrl`).
- **Eligible:** a law provision (in `INDEX.json`) with ≥ 2 redactions whose texts
  differ after whitespace/case normalisation. Regulation sidecars are out of scope.
- **Query date:** the midpoint of the chosen redaction's validity window (its
  start date if still in force). The item is kept only if **exactly one**
  redaction covers that date.
- **Answer:** that redaction's text. **Distractors:** up to 3 other redactions
  with a different normalised text, closest in time first.
- **Which redaction:** one per provision, chosen by salted hash.
- **Limits:** texts over 1,500 characters are skipped as answers and distractors.
- **RT citation:** the law root's `dcterms:source` (the consolidated text the
  corpus was built from). If the law root has none, the redaction's own `rtUrl`
  is used. The redaction URL is always in `source_url`, and
  `snapshot.law_kehtiv` records the consolidation date.

### (b) `court_interpretation` — which provisions does a decision interpret?

*Milliseid õigusakti sätteid tõlgendab Riigikohtu 14.06.2017 lahend kohtuasjas nr
3-2-1-20-17 (Tsiviilkolleegium, ECLI ECLI:EE:RK:2017:3.2.1.20.17)?*

- **Source:** `estleg:interpretsLaw` on Riigikohus `estleg:CourtDecision` nodes.
- **Answer:** the set of provision IRIs, with `answer_labels`. There are no
  distractors. Decisions with more than 25 targets are skipped.
- **Personal data:** the builder reads only `@id`, case number, ECLI, decision
  date, chamber, case type (as a filter only) and `interpretsLaw`. It never reads
  the summary, decision text, judges or `rdfs:label`. A test plants sentinel
  strings in those fields of the fixture and fails if any of them leaks.
- **Criminal and misdemeanour cases are excluded by default.** A case number
  linked to Penal Code provisions is offence-related data under Art. 10 GDPR.
  Pass `--include-criminal` only after DPO sign-off.
- `rt_citation` is null. `source_url` is the riigikohus.ee case-number search URL.

### (c) `cross_reference` — what does a citation point to?

*Millisele sättele viitab viide „käesoleva seaduse § 88“, mis sisaldub
„Veeseadus“ sättes § 284⁶?*

- **Source:** `estleg:references` edges on law provisions. These edges carry no
  citation text in the corpus, and the existing `estleg:Citation` nodes are the
  *unresolved* ones. So the builder re-extracts citation strings from the
  provision's `estleg:legalText` with `extract_citations_from_text`, the same
  function that produced the edges.
- **Kept only when** the citation names a single § and exactly one of the
  provision's edges matches it. A match means the same § (and lõige, when one
  is cited), inside the citing law for *käesoleva seaduse* citations and outside
  it otherwise.
- **Answer:** that IRI. **Distractors:** the provision's other edges, then the
  target's neighbouring lõiked and §§ that exist in the corpus, up to 3.
- **RT citation:** the citing law's `dcterms:source`. Items from laws without
  one are skipped.

### Sampling, caps and splits

| Setting | Value |
|---|---|
| Seed (hash salt) | `estleg-bench-727-v1` |
| Cap per task | 3,000 |
| Cap per law, tasks (a) and (c) | 30 |
| Distractors per item | ≤ 3 |
| Max point-in-time text length | 1,500 chars |
| Max court targets per decision | 25 |
| Split rule | SHA-256(seed, group) mod 100: < 80 train, < 90 dev, else test |
| Split key | (a), (c): source law name; (b): decision IRI |

Candidates are ranked by a salted hash of the item id. The per-law cap is
applied first, then the task cap. No law and no decision appears in more than
one split; a test checks this.

## Measured counts (corpus at wave-3 commit `c0140d497e`, evaluation date 2026-06-01)

Source substrate, re-measured. The ticket's figures are out of date.

| Measure | Value |
|---|---|
| Provision-version sidecars | 4,422 (736 laws + 3,686 regulations) |
| Sidecars with a multi-redaction history | 487 |
| Provisions with ≥ 2 distinct redaction texts | 24,674 (24,964 with exact string compare) |
| Riigikohus decisions scanned / with interpreted targets | 12,104 / 9,339 |
| Resolved court → provision citations | 111,224 (ticket: 82,508) |
| Distinct interpreted targets | 15,718 (ticket: 5,602) |
| Law provisions with `estleg:references` and text | 24,168 (32,614 edges) |

The ticket's "487 provisions" is the number of *laws/sidecars* with a
multi-redaction history, not provisions.

Candidates and drop reasons:

| Task | Candidates | Main drops |
|---|---|---|
| point_in_time | 20,882 | 3,608 with no unambiguous redaction; 184 regulation provisions |
| court_interpretation | 6,196 | 2,505 criminal/misdemeanour; 638 with > 25 targets |
| cross_reference | 25,376 | 5,516 ambiguous/unmatched citations; 512 from laws without an RT citation |

Full build (3,000 per task, 9,000 items):

| Task | train | dev | test | Laws/decisions (train/dev/test) |
|---|---|---|---|---|
| point_in_time | 2,380 | 268 | 352 | 337 / 32 / 34 laws |
| court_interpretation | 2,443 | 267 | 290 | one decision per item |
| cross_reference | 2,451 | 193 | 356 | 274 / 21 / 31 laws |

The committed sample has 600 items in 1.8 MB. Its per-split counts are in
`eval/benchmark/sample/manifest.json`.

## How to evaluate

Predictions are a JSON object `{item id: prediction}`. A prediction is a choice
index or answer text for `point_in_time`, an IRI or choice index for
`cross_reference`, and a list of IRIs for `court_interpretation`. IRIs may be
compact (`estleg:X`) or full (`https://w3id.org/estleg/X`). Texts compare
whitespace- and case-insensitively.

| Task | Primary metric | Also reported |
|---|---|---|
| point_in_time | exact match (accuracy) | — |
| cross_reference | exact match (accuracy) | — |
| court_interpretation | mean per-item set F1 | exact set match |

A missing prediction scores 0. The overall score is the macro average over tasks.

```bash
python3 scripts/build_legal_benchmark.py --out eval/benchmark/full --score predictions.json --split test
```

```python
from pathlib import Path
from estleg.build_legal_benchmark import read_items, score_predictions
items = read_items([Path("eval/benchmark/full/test.jsonl")])
print(score_predictions(items, predictions))
```

Chance level on the full test split is 0.349 for `point_in_time` and 0.285 for
`cross_reference`, from the mean 1/|choices|. Court items have a median of 8
gold provisions.

Report on `test`, tune on `dev`, and state the benchmark version and seed.

## Label quality — read before publishing numbers

- **point_in_time** labels are structural. They come from Riigi Teataja
  redaction validity windows and are as good as the sidecars.
- **court_interpretation** labels are *silver*. `interpretsLaw` comes from the
  regex citation resolver over the decision text, and a cited provision counts
  as "interpreted".
- **cross_reference** labels are *silver*. They are the corpus's own heuristic
  edges, filtered to unambiguous matches.

A hand-adjudicated gold subset is a law-faculty task (see the checklist). It
should be a stratified sample of the `test` split, with each item checked
against the Riigi Teataja text, following the `needs-legal-review` convention in
[`eval/README.md`](../eval/README.md).

## Rights per item (`licence` field)

Rights follow [`NOTICE`](../NOTICE) and [`DATA_RIGHTS.md`](DATA_RIGHTS.md). They
are **not** "CC BY 4.0 via Riigi Teataja".

- **Statutory text** in (a) and (c): legislation is not an object of copyright
  under Autoriõiguse seadus § 5. Riigi Teataja's reuse and database terms for
  the consolidated product are still **VERIFY**.
- **Court items** in (b): judgments are not objects of copyright either. No
  judgment text is reproduced. The personal-data position is a DRAFT pending the
  DPO (#720), and a republisher becomes an independent GDPR controller.
- **Compilation layer**, meaning the item construction, IRIs and links:
  **CC BY 4.0**. This is a draft election; CC0 1.0 is the alternative.

## CLARIN-EE deposit checklist

Repo side (done in #727):

- [x] Deterministic builder, JSONL splits, JSON Schema, reference scorer, tests
- [x] Rights / provenance manifest (`eval/benchmark/rights_provenance.json`)
- [x] CMDI 1.2 metadata stub (`eval/benchmark/cmdi.xml`)
- [x] Personal-data controls for court items: allow-listed fields, sentinel test,
      criminal-case exclusion

Repo side, still open:

- [ ] Confirm the CMDI profile id in the CLARIN Component Registry. The stub
      follows the OLAC-DcmiTerms shape. Check whether CLARIN-EE wants its own
      corpus profile.
- [ ] Confirm the MIME type CLARIN-EE accepts for JSON Lines. The stub uses
      `application/jsonl`.
- [ ] Map `eval/benchmark/**` in `REUSE.toml` to the data licence instead of the
      MIT catch-all.
- [ ] Add `jsonschema` to the `dev` extra. The schema test skips without it.

Maintainer (Henrik) only:

- [ ] Name the applicant organisation and PI, and email EKI (Käbi Laan) about
      the 2027 open round.
- [ ] Confirm the partners: TartuNLP and/or EKI, plus a law faculty for gold
      adjudication.
- [ ] **Rights transfer.** Sign the CLARIN-EE deposit licence agreement. This
      needs the open [`DATA_RIGHTS.md`](DATA_RIGHTS.md) items closed first: the
      Riigi Teataja terms and the CC BY 4.0 vs CC0 election.
- [ ] **DPO sign-off** for case-number items, and decide whether criminal cases
      can ever be included (#720).
- [ ] **Persistent identifier.** Get a CLARIN-EE handle and decide whether it
      also closes the Zenodo DOI item (#473). Then fill `MdSelfLink`,
      `identifier` and the `ResourceRef`s in `cmdi.xml`.
- [ ] Choose the access category at CLARIN-EE: public (PUB) or academic (ACA).
- [ ] Build the full set from the release commit and deposit `full/` together
      with the schema, the rights manifest and this document.
