# Draft lifecycle: EIS to Riigikogu (#717)

This page is the operator and consumer contract for the draft lifecycle layer
in `krr_outputs/eelnoud/`. It covers the dated process steps, how the current
phase is derived, the Riigikogu join, the `estleg:derivationMethod`
provenance stamps, and the licence of the Riigikogu layer.

## What a draft looks like

Every `estleg:DraftLegislation` node carries one or more process steps:

```turtle
estleg:Draft_JDM26_0214
    estleg:hasProcessStep estleg:Draft_JDM26_0214_Step_1 ;
    estleg:legislativePhase estleg:Phase_PublicConsultation ;   # derived
    estleg:initiator "Justiitsministeerium" ;                    # literal, unchanged
    estleg:initiatedBy estleg:Institution_justiits_ja_digiministeerium .

estleg:Draft_JDM26_0214_Step_1 a eli-dl:ProcessStep, estleg:ProcessStep ;
    estleg:processStepOf estleg:Draft_JDM26_0214 ;
    estleg:processStage estleg:Phase_PublicConsultation ;
    dcterms:date "2026-02-17"^^xsd:date ;
    estleg:stepOrder 1 ;
    estleg:derivationMethod "eis-feed" ;
    dcterms:source <https://eelnoud.valitsus.ee/main/mount/rss/home/publicConsult.rss> .
```

A draft matched to a Riigikogu proceeding also carries
`estleg:riigikoguMark` (e.g. `"897 SE"`), `estleg:riigikoguUuid`,
`estleg:riigikoguMembership`, Riigikogu steps, and the Riigikogu EuroVoc
descriptors as `dcterms:subject` with `estleg:subjectSource "riigikogu"`.

### Step identifiers

Step IRIs are `<draft IRI>_Step_<n>`. The ordinal `n` is stable: a step keeps
its id across reruns (it is keyed on method, stage, date and Riigikogu status)
and a new step takes the next free ordinal. Ids are never renumbered, so `n`
is an identifier, not a chronological rank. Use `dcterms:date` and
`estleg:stepOrder` to sort.

### Step dates

* EIS steps use the date EIS printed in the feed item title (`(DD.MM.YYYY)`),
  which is also the draft's `estleg:publicationDate`. About 1,050 Submission
  items carry no date; their EIS step has no `dcterms:date`.
* Riigikogu steps use the date of the proceeding event (`readings[].proceedingEvents[].date`).

## Phases

| Phase individual | Source | Meaning |
| --- | --- | --- |
| `Phase_PublicConsultation` | EIS feed | Public consultation |
| `Phase_Review` | EIS feed | Inter-ministerial review |
| `Phase_Submission` | EIS feed | Submitted to the Government |
| `Phase_RiigikoguProceeding` | Riigikogu | Initiated / taken into proceedings |
| `Phase_FirstReading` | Riigikogu | First reading |
| `Phase_SecondReading` | Riigikogu | Second reading |
| `Phase_ThirdReading` | Riigikogu | Third reading |
| `Phase_Reconsideration` | Riigikogu | Not proclaimed by the President; deliberated again |
| `Phase_Enacted` | Riigikogu / `enactedAs` | Adopted (and later proclaimed and published) |
| `Phase_Rejected` | Riigikogu | Rejected |
| `Phase_Withdrawn` | Riigikogu | Withdrawn by the initiator |
| `Phase_Lapsed` | Riigikogu | Dropped: end of membership, merged, excluded or returned |

The raw Riigikogu status code of every Riigikogu step is kept in
`estleg:riigikoguStatus`. The status to phase mapping is
`STATUS_PHASES` / `READING_PHASES` in `src/estleg/generate_riigikogu_proceedings.py`.

### Derivation rule

`estleg:legislativePhase` is derived, never asserted by a feed:

1. Take the latest step by `dcterms:date` (undated steps sort first; same-day
   ties sort a terminal outcome after the stage it ends).
2. Its `estleg:processStage` is the current phase.
3. A resolved `estleg:enactedAs` makes the phase `Phase_Enacted`, unless the
   latest step is already terminal.

Before #717 the phase was "first RSS feed wins": the feeds were read in the
order publicConsultation, review, submission and a draft kept the phase of the
first feed that listed it.

### Stale drafts

The EIS feeds show which drafts had an activity of each kind, not their
outcome. A draft whose derived phase is still `Phase_PublicConsultation` and
whose latest evidence is more than 365 days older than the EIS snapshot is
flagged `estleg:lifecycleStale true`. The six 2011 and eighteen 2012 drafts the
ticket names are among them. Their outcome is unknown to every source read
here, so no outcome is invented. A later Riigikogu join or a newer EIS
observation clears the flag.

### Peep files

`eelnoud_<feed>_peep.json` groups drafts by the EIS feed they were **first**
observed in. A draft's steps live in the same file. The file name is not the
current phase. `EELNOUD_INDEX.json` reports both: `phases` counts drafts by
derived phase, and `files` counts drafts per peep file.

## Sources and the EIS snapshot

* **EIS RSS feeds** (`eelnoud.valitsus.ee`, three feeds). They have answered
  HTTP 403 since the Sätla switch-over on 2026-10-01. The committed layer
  reflects the last live refresh of **2026-03-07**. The live generator merges
  new observations into the committed steps, so a returning feed extends the
  history instead of replacing it.
* **Riigikogu open data** (`api.riigikogu.ee`).

## Riigikogu join

`scripts/generate_riigikogu_proceedings.py` joins EIS drafts to Riigikogu
draft volumes. The `estleg:derivationMethod` of each Riigikogu step records
how the join was made:

| Value | Evidence |
| --- | --- |
| `riigikogu-mark` | The EIS title quotes the mark, e.g. `(835 SE)`. The mark must be unique within the Riigikogu membership in force on the EIS date. The titles must also be similar: token Jaccard ≥ 0.5, or the mark is the title's final parenthetical. Documents about a bill are skipped. These are opinions, implementing acts, amendment proposals and reading briefings. This join wins any conflict. |
| `riigikogu-eis-number` | Exactly one Riigikogu volume quotes the draft's EIS number in an "[EIS]" notice file name. It overrides a title-date join but not a mark join. In 11 cases a notice file quoted the EIS number of a neighbouring bill, while the mark in the EIS title was right. |
| `riigikogu-title-date` | An EIS Bill or AmendmentBill whose normalised title equals one Riigikogu SE bill. That bill must be initiated 0–366 days after the EIS date and initiated by the Government. Ambiguous titles are skipped. |

A draft without a confident join keeps its EIS steps only.

### Running it

```bash
.venv/bin/python scripts/generate_riigikogu_proceedings.py            # uses the cache, fetches misses
.venv/bin/python scripts/generate_riigikogu_proceedings.py --offline  # never touches the network
.venv/bin/python scripts/generate_riigikogu_proceedings.py --refresh  # re-fetch listing + memberships
.venv/bin/python scripts/rebuild_subcorpus_combined.py --subcorpus eelnoud
```

Pass `--refresh-details` as well to re-fetch every matched draft detail.

The client keeps to **1 request per second**. It caches a trimmed projection
of every response under `data/riigikogu/` (`drafts_list/`, `drafts/<uuid>.json`,
`eurovoc/<edid>.json`, `memberships.json`), so reruns are offline. A listing
page the API answers with HTTP 404 is split down to single rows. One row
(offset 5049, sorted by mark then UUID) cannot be served at all and is
recorded as skipped.

The pipeline order is:

1. `generate_draft_legislation.py`, live (ingest).
2. `generate_draft_legislation.py --lifecycle-from-peeps`.
3. `generate_riigikogu_proceedings.py`.
4. `rebuild_subcorpus_combined.py --subcorpus eelnoud`.
5. `extract_draft_impact.py`, which re-derives the phase after `enactedAs`.

### EuroVoc subjects

Riigikogu descriptors carry an `edid`. A classic EuroVoc descriptor has
`edid` equal to its EuroVoc id. A newer concept has a `c_<hex>` code, which
only `/api/eurovoc/descriptor?edid=` returns, so those codes are looked up once
each and cached. Subjects are emitted as bare `http://eurovoc.europa.eu/<id>`
IRIs, the same as the CELLAR subjects of #699, with
`estleg:subjectSource "riigikogu"`. They are only set on drafts that have no
other `dcterms:subject`.

## Licence of the Riigikogu layer

The Riigikogu open data is licensed **CC BY-SA 3.0**
(<https://creativecommons.org/licenses/by-sa/3.0/>). Every Riigikogu step
carries `dcterms:license <https://creativecommons.org/licenses/by-sa/3.0/>`.
`data/riigikogu/LICENSE.json` records the licence of the cache. The
`dcterms:subject` values with `estleg:subjectSource "riigikogu"` and the
`riigikogu*` draft properties come from the same source and are under the same
licence. Redistributors must attribute Riigikogu and share alike.

**Open item for the maintainer:** confirm the licence and the 1 request/s rate
limit with Riigikogu Kantselei. Until that is confirmed, these are the values
published on the API documentation and assumed here.

## `initiatedBy`

The EIS number prefix, e.g. `JDM/26-0214`, names the ministry that owns the
draft in EIS at snapshot time. `estleg:initiatedBy` maps it to the
`krr_outputs/institutions/` node valid on the draft's `publicationDate`. The
mapping walks the **same legal person** rename chain in
`data/institution_identity.json`. A predecessor is followed only when it has
the same registrikood. For example, KLIM 2012 resolves to
`Institution_keskkonnaministeerium`, JDM 2024 to
`Institution_justiitsministeerium`, and REM 2014 to
`Institution_pollumajandusministeerium`.

Mergers are not followed. RK (Riigikantselei) has no institution node, so its
246 drafts get no `initiatedBy`. The literal `estleg:initiator` is kept. REM now
reads "Regionaal- ja Põllumajandusministeerium" instead of the office title
"Regionaalminister".

## `estleg:derivationMethod`

`estleg:derivationMethod` is a closed value set. It is defined in
`estleg_common.DERIVATION_METHODS`. Each value names both the method and the
property it qualifies, so a node may carry several values.

| Value | On | Qualifies |
| --- | --- | --- |
| `eis-feed` | ProcessStep | Observed in an EIS RSS feed |
| `riigikogu-eis-number`, `riigikogu-mark`, `riigikogu-title-date` | ProcessStep | Riigikogu join (see above) |
| `minted-ecli` | CourtDecision | `ecliIdentifier` minted locally (`ECLI:EE:RK:YYYY:…`), not read from a publisher |
| `rederived-case-type` | CourtDecision | `caseType` re-derived from the deciding-chamber text (#579), where the case number alone yields `Other` |
| `cellar-interprets` | EUCourtDecision | `interpretsEULaw` from CELLAR `cdm:case-law_interpretes_resource_legal` |
| `title-regex` | EUCourtDecision | `interpretsEULaw` parsed from the decision title (#418 fallback) |

### CELLAR "interprets" edges

The ticket names `cdm:case-law_cites_legal_resource`. CELLAR has no such
predicate: it has 0 triples, probed 2026-10-08. The CDM property for "this
decision interprets that legal resource" is
`cdm:case-law_interpretes_resource_legal`. It is not added to the paginated
listing SELECT in `generate_eu_court_decisions.py`, because a multi-valued
OPTIONAL multiplies that query's rows, which is the #699 cost warning.

Instead it is fetched in VALUES batches of 500 CELEX:

```bash
.venv/bin/python scripts/generate_eu_court_decisions.py --fetch-interprets
```

That run makes 45 requests and takes about 75 s. The result is cached in
`data/curia/cellar_interprets.json`. `link_curia_eu_legislation.py` reads the
cache offline. When CELLAR yields at least one act present in the eurlex
peeps, those edges replace the title-parsed ones.

## Acceptance query

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX dcterms: <http://purl.org/dc/terms/>
SELECT ?draft ?step ?stage ?date ?method WHERE {
  ?draft estleg:riigikoguMark "897 SE" ;
         estleg:hasProcessStep ?step .
  ?step estleg:processStage ?stage ;
        estleg:derivationMethod ?method .
  OPTIONAL { ?step dcterms:date ?date }
} ORDER BY ?date
```

The contract tests are `tests/test_issue_717_lifecycle.py` and
`tests/test_generate_riigikogu_proceedings.py`.
