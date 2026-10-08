# Data protection: record of processing for court-decision data (GDPR)

> **Status (2026-10-08): processing record complete as a record; four
> decisions are still open.** Everything the project can establish by itself
> (what data, where it lives, how it got there, how much, how it is removed) is
> recorded below with evidence. Decisions that only the maintainer or a Data
> Protection Officer (DPO) can take are explicit, dated fields marked
> **PENDING**; see the [register of pending decisions](#register-of-pending-decisions).
> The legal analysis is the project's working position, not legal advice and
> not a DPO determination.
>
> Related: [`DATA_RIGHTS.md`](DATA_RIGHTS.md) (copyright / database-right
> layers), [`../SECURITY.md`](../SECURITY.md) (private contact route),
> [`../GOVERNANCE.md`](../GOVERNANCE.md) (who runs the project), top-level
> [`NOTICE`](../NOTICE). Tracking issue: **#720**.

## 1. Record of processing (Article 30-style)

| Field | Entry |
|---|---|
| Record version / date | 2026-10-08 |
| Controller | **PENDING** (opened 2026-10-08, owner: maintainer). Factual position today: see [section 2](#2-controller). |
| Contact for data subjects | [`SECURITY.md`](../SECURITY.md), private GitHub channel. No personal mailbox is published. See [section 6](#6-data-subject-contact-route). |
| DPO | None appointed. **PENDING** (opened 2026-10-08, owner: maintainer). |
| Purposes | Open, machine-readable access to Estonian and EU case-law linked to the legislation it interprets; legal research; feeding the Seadusloome drafting tool and the estleg MCP query endpoint. |
| Legal basis | Working position: Art. 6(1)(f) legitimate interests; Art. 6(1)(e) assessed as unlikely to be available. Art. 10 condition for criminal content **PENDING DPO**. See [section 4](#4-purpose-and-legal-basis). |
| Data subjects | Parties, accused and convicted persons, victims, witnesses, third persons named in reasoning; judges; advocates, prosecutors and other officials acting in role; EU-court parties. |
| Personal data | Names (full or initialled) in decision abstracts and full text; judge names; CURIA party names in titles; facts of the case, including criminal charges and convictions. Personal identification codes are masked (see [section 3.2](#32-implemented-control-personal-identification-codes-are-masked-683)). |
| Special categories | Criminal convictions and offences (Art. 10). Case facts can reveal Art. 9 data (health, ethnicity, beliefs) in context. |
| Sources | Riigikohus decisions from the official RIK court-information system (`rikos.rik.ee`); lower-court sample from the Riigi Teataja kohtulahendid search API; CURIA from the EU Publications Office / EUR-Lex. No other source is joined. |
| Recipients | See [section 5](#5-recipients-and-transfers). |
| Third-country transfers | GitHub, Inc. (United States) hosts the repository, Git LFS objects and release downloads. The transfer mechanism has not been assessed. **PENDING DPO** (opened 2026-10-08). |
| Retention | See [section 10](#10-retention). |
| Security / organisational measures | Personal-code masking with a release-blocking gate (#683); lower-court sweep sign-off gate (#720); re-identification check tooling (#720); private reporting route (`SECURITY.md`); no personal mailbox in documentation; local scrape caches are git-ignored. |

## 2. Controller

**Factual position (2026-10-08).** The Estonian Legal Ontology is a personal
open-source project operated by one maintainer (see `GOVERNANCE.md`). It has no
institutional home yet; finding one is tracked in **#730**. The project is not
a public authority and has no statutory task.

**What that means legally (working position).** The natural person who decides
what is collected and published determines the purposes and means of the
processing, so that person is the controller until an institution takes the
project over. The household exemption does not apply, because the data is
published to everyone. The controller's identity field above stays **PENDING**
until the maintainer decides how the controller is named (personally, through a
legal entity, or through the future institutional home). No personal name is
written into this repository in the meantime.

**Re-publishers are independent controllers.** If you download and republish
`krr_outputs/riigikohus/`, `krr_outputs/curia/` or `krr_outputs/kohtud/`, or a
release asset built from them, you are **not** a mere conduit. You become an
**independent data controller** for that personal data, with your own lawful
basis, your own transparency duties, your own handling of data-subject rights
(access, erasure, objection) and your own liability. The project's position
does **not** transfer to you. Consider whether your use case needs the names at
all. A removal applied here reaches you only if you re-download.

## 3. What personal data the corpus contains

| Subcorpus | Location | Records | Where the personal data sits |
|---|---|---|---|
| Estonian Supreme Court decisions (Riigikohus) | `krr_outputs/riigikohus/` | 12,104 | names in `estleg:summary` and `estleg:legalText`; panel names in `estleg:judge`; `rdfs:label` is `RK <case number>` and holds no names (measured, [section 7](#7-re-identification-check)) |
| First/second-instance decisions (kohtud) | `krr_outputs/kohtud/` | 1 (sample) | The committed corpus is a **one-decision sample**, flagged `estleg:isSampleData: true` on the graph header and `"sample": true` in `KOHTUD_INDEX.json` (#689). It holds search metadata only. A live sweep may copy `kokkuvote` summaries that name persons, and is gated ([section 9](#9-lower-court-sweep-gate)). |
| EU Court of Justice decisions (CURIA) | `krr_outputs/curia/` | ~22,290 | **party names** in `rdfs:label` |

### 3.1 Criminal-offence data (Article 10)

Supreme Court criminal decisions attach **Penal Code (KarS) charges to
identifiable persons**. Data about criminal convictions and offences falls
under **Article 10 GDPR**, and case facts may reveal Article 9 special-category
data. Measured on the committed corpus, 1,392 of the 3,686 criminal decisions
and 79 of the 107 misdemeanour decisions carry at least one full-name candidate
that is not a judge, advocate, prosecutor or official
([section 7](#7-re-identification-check)).

### 3.2 Implemented control: personal identification codes are masked (#683)

Estonian personal identification codes (`isikukood`) are **direct identifiers**.
They are screened out of published court text, and the screening is enforced by
a release gate.

- `estleg_common.screen_personal_data(text)` is the single screening helper.
  It replaces every isolated, checksum-valid, date-plausible 11-digit run with
  the placeholder `[isikukood eemaldatud]` and returns only the **masked** form
  of what it removed (e.g. `344****38`). The full code is never returned,
  logged, or persisted anywhere in the repository.
- It is applied to Riigikohus summaries and full text, and to lower-court
  summaries. Both summary writers screen before their 800-character limit, so
  truncation cannot publish the beginning of a personal code.
- The committed corpus was backfilled by the offline pass
  `python3 -m estleg.screen_court_personal_data` (shim:
  `scripts/screen_court_personal_data.py`).
- Every Riigikohus and lower-court decision node carries
  `estleg:personalDataScreened: true` and `estleg:personalDataMaskedCount`.
- `scripts/validate_all.py` carries the gate `validate_no_personal_codes`: a
  surviving code in `estleg:summary` / `estleg:legalText` is a release-blocking
  error.

**Carve-out.** A run introduced by a label that states the number is *not* a
person (`registrikood` / `reg. kood`, `otsuse nr`) is left intact unless a
personal-code label also precedes it.

| Recorded screening result (September 2026 corpus) | Count |
|---|---|
| Decision nodes scanned | 12,104 |
| Nodes with a masked code | 21 |
| Codes masked | 27 |
| Of those, labelled `isikukood` | 27 (all) |

The per-node record is `krr_outputs/reports/personal_data_mask_report.json`,
which stores masked display forms only. Screening covers **codes**, not
**names**; names are the subject of sections 4 to 7.

## 4. Purpose and legal basis

**Purpose.** Republishing case-law that courts have already made public, in a
form linked to the legislation it interprets, for legal research, legislative
drafting (Seadusloome) and question answering (MCP endpoint). Names are not
needed for any of these purposes except judge names, which identify the bench.

**Article 6(1)(e) (public task).** Art. 6(3) requires the task to be laid down
by Union or Member State law. The project is a private open-source effort with
no task conferred on it by law, so this basis is assessed as **unlikely to be
available** unless an institution with such a task adopts the project (#730).

**Article 6(1)(f) (legitimate interests).** This is the working basis. The
interests are open access to case-law and legal research. The balancing test
weighs against the project where a private party's full name, combined with
criminal or family-law facts, is republished in bulk, searchable form beyond
what the court itself shows today. A written balancing test is part of the DPO
decision. **PENDING DPO** (opened 2026-10-08).

**Article 10 (criminal convictions and offences).** Processing of this data is
allowed only under the control of official authority, or where Union or Member
State law authorises it with appropriate safeguards. The court published the
decisions under official authority. Whether a private mirror's onward
republication stays within that control is doubtful. The project has not
identified a provision of Estonian law that authorises it. The Estonian
Personal Data Protection Act (Isikuandmete kaitse seadus) contains derogations
for journalistic and academic expression and for scientific research and
statistics. Whether the project qualifies under any of them is a DPO question.
**PENDING DPO** (opened 2026-10-08). Until it is answered, the
[names policy](#names-policy-decision) is the control that bounds the risk.

**Court-publication rules.** Estonian court decisions are published under the
Courts Act and the procedural codes (civil, criminal, administrative and
misdemeanour procedure). Those rules decide when a person's name is replaced by
initials or characters in the published decision, and they let a person ask
the court to restrict publication of their data. An earlier draft of this
document attributed the anonymisation rules to the Data Protection Inspectorate
(Andmekaitse Inspektsioon). The Inspectorate is the supervisory authority; the
rules themselves sit in the procedural legislation. The exact provisions are
to be cited by the DPO, not guessed here.

### Names policy decision

| Field | Value |
|---|---|
| Question | How are names of natural persons who are not judges, advocates, prosecutors or officials published? |
| Opened | 2026-10-08 |
| Decision | **PENDING DPO** |
| Decided on | not decided |
| Decided by (role) | not decided |
| Record | to be linked here and in #720 |

Options, with what each costs and leaves open:

1. **Keep names as published by Riigikohus.** No pipeline change. Risk: the
   mirror goes stale against the official feed. A person whose name the court
   later removes or initials stays named here until someone notices. Requires
   the live re-identification check on every release, plus the erasure SOP.
2. **Pseudonymise.** Replace unattributed natural-person names in
   `estleg:summary` and `estleg:legalText` with initials, the same form the
   court uses. Keep judge names and named advocates, prosecutors and officials
   acting in role. Uses the candidate detector from
   [section 7](#7-re-identification-check) as the starting point and needs a
   reviewed allow/deny list, because the detector is a heuristic. Removes most
   of the Art. 10 exposure and keeps the text usable.
3. **Drop.** Publish no free text for Riigikohus: keep case number, date, type,
   chamber, judges and links to the official source. Lowest risk; loses the
   research value of the text and breaks the MCP full-text tools.

**Non-binding recommendation from the tooling author:** option 2 for all
decisions, at minimum for criminal and misdemeanour cases, because 6,703
decisions carry full-name candidates outside an official role and 1,392 of
them are criminal cases. The decision is the DPO's.

## 5. Recipients and transfers

| Recipient | What it receives | Notes |
|---|---|---|
| GitHub (repository and Git LFS) | Everything in `krr_outputs/` | Public repository; US provider. |
| GitHub release downloads | `curia_combined.jsonld.gz` (CURIA names); `estleg_all.nq.gz` (named graphs including Riigikohus and CURIA); `chunks.jsonl.gz` (retrieval projection over court text); `DATA_PROTECTION.md` | Downloaders become independent controllers ([section 2](#2-controller)). Attaching this file to every release is #684. |
| estleg MCP endpoint (`estleg.sixtyfour.ee`) | Court-decision lookup and full-text tools read the Riigikohus and CURIA peeps | Serves names on request. A removal reaches it after a redeploy. |
| Seadusloome (`seadusloome.sixtyfour.ee`) | Loads the `riigikohus` and `curia` subcorpora | Downstream consumer; same redeploy rule. |
| Archive deposits (planned) | Release snapshots | A deposited snapshot cannot be edited; a removal needs a new version and a request to restrict the old one. |

## 6. Data-subject contact route

Data subjects use the private route in [`SECURITY.md`](../SECURITY.md):
GitHub private vulnerability reporting on the repository's Security tab, or,
while that setting is off, a content-free public issue that the maintainer
moves into a private security advisory. Requests are never discussed in a
public issue. There is no personal mailbox. A shared, non-personal contact
alias is a **PENDING** maintainer decision (#721); when it exists it is listed
in `SECURITY.md` only. `SECURITY.md` sets the response target for a confirmed
personal-data removal at 10 working days on `main`.

## 7. Re-identification check

**Question.** Do the names stored in `krr_outputs/riigikohus/` match what the
official RIK feed publishes, or does the corpus identify people the court's
anonymisation hides?

**Pipeline provenance (answered from the code).** The project did not join any
other source. `generate_court_decisions.py` copies `estleg:summary` from the
RIK search table and `estleg:legalText` from the RIK detail page, and the only
transformation is personal-code masking. Every name in the corpus was
therefore served by RIK at scrape time. The remaining risk is **drift**: RIK
can initial or remove a name after the scrape, for example on a person's
request, and the mirror keeps the old form.

**Offline check (run 2026-10-08).**
`python3 -m estleg.check_reidentification --offline` scans `rdfs:label`,
`estleg:summary`, `estleg:legalText` and `estleg:judge`. It finds runs of two
or three capitalised words, splits them at organisation, place and header
words, drops runs next to a legal-form marker (OÜ, AS, MTÜ...), and attributes
each survivor to a role from its context: judge, professional or official
(advocate, prosecutor, representative, notary and similar), or unattributed.
The unattributed bucket is where parties, victims and witnesses fall. On a
manual review of a random 80-candidate sample, about 9 in 10 were person names;
precision is otherwise unmeasured, so treat the counts as an upper bound. The
report, `krr_outputs/reports/reidentification_check_report.json`, holds only
aggregates, year histograms, field names and SHA-256 fingerprints of the
sorted candidate sets; a test asserts that no fixture name reaches it.

| Measure (heuristic v2, September 2026 corpus) | Value |
|---|---|
| Decisions scanned | 12,104 |
| Decisions with at least one person-name candidate, any role | 10,900 |
| Decisions with at least one unattributed candidate | 6,703 |
| Median unattributed candidates per such decision | 5 |
| 90th percentile | 13 |
| Unique unattributed candidate strings | 17,928 |
| Decisions with initials-style markers (court anonymisation form) | 3,587 |
| Decisions with unattributed candidates in `estleg:summary` | 4,234 |
| Decisions with unattributed candidates in `estleg:legalText` | 6,206 |
| Candidates in `rdfs:label` | 0 |

Both full names and initials occur in every year from 1994 to 2026. This
contradicts the assumption that the court's anonymisation reliably initials
party names. Many of the full names may be lawful as published, for example
parties in civil cases where no restriction was requested. The live check is
what tells the two apart.

**Live check (designed and scripted, NOT YET RUN: maintainer action).**
`ESTLEG_LIVE_CANARY=1 python3 -m estleg.check_reidentification --live --sample 100`

- **Refusal.** The script refuses (exit 2) unless `ESTLEG_LIVE_CANARY=1` is
  set and `rikos.rik.ee` is on `ALLOWED_HTTP_HOSTS`. It is.
- **Query shape.** One GET per sampled decision to
  `https://rikos.rik.ee/?asjaNr=<case number>`, through the same
  `fetch_decision_text` extractor that produced the stored text, with the
  cache bypassed so the current official text is compared like for like.
- **Rate limit.** At most one request every 2 seconds, sequential, with the
  pipeline's identifying User-Agent and its bounded retry.
- **Sample.** 100 decisions drawn from those with unattributed `estleg:legalText` candidates,
  stratified by year in proportion to each year's share and at least one per
  year, ordered by SHA-256 of the case number. No random generator, so the
  sample is reproducible.
- **Field compared.** Only candidates from `estleg:legalText`, which is
  scraped from the same detail page. `estleg:summary` comes from the search
  table's abstract, which the detail page does not repeat, so comparing it
  there would report false absences. Summary drift needs a second pass over
  the search table and is not covered by this design.
- **Operational definition.** Each unattributed candidate is `present`
  (verbatim in the official text), `initialised` (only its initials appear)
  or `absent`. A decision is **re-identified** when any candidate is not
  `present`; **consistent** when all are; **fetch_failed** when no official
  text came back.
- **Output.** `krr_outputs/reports/reidentification_live_report.json`:
  sample size requested and checked, verdict counts overall and per year,
  candidate-status counts, the SHA-256 of the sorted sampled case numbers, and
  the case numbers of re-identified decisions so the SOP can act on them. No
  names.
- **Recorded result:** not yet run. Owner: maintainer. Opened 2026-10-08.
  If any decision is re-identified, the names policy must at least bring those
  decisions to the official form before the next release.

## 8. Erasure and objection procedure (SOP)

Applies to requests under Art. 17 (erasure), Art. 21 (objection) and Art. 18
(restriction) concerning a name in the corpus.

1. **Intake.** The request arrives through the [contact route](#6-data-subject-contact-route).
   The maintainer acknowledges within the `SECURITY.md` target and opens a
   private advisory. Nothing about the request goes into a public issue, a
   commit message or a pull-request description.
2. **Verification.** Ask only for what links the requester to the named
   person in the specific decision: the case number and the name as it appears.
   Do not collect identity documents into the repository. A representative
   shows their authority in the private advisory.
3. **Assessment.** Check the request against the working legal basis. Under
   Art. 6(1)(f) an objection succeeds unless compelling legitimate grounds
   override it. Art. 17(3) exceptions (freedom of expression, archiving and
   research) are weighed by the DPO once appointed; until then the default is
   to remove the name. Record the outcome as: request id, date received, date
   decided, outcome, case numbers. Never record the name.
4. **Locate.** Names live in these places:

   | Place | Field / file | Written by |
   |---|---|---|
   | Riigikohus peeps | `estleg:summary` | `generate_court_decisions.decision_to_node` (from the RIK search table) |
   | Riigikohus peeps | `estleg:legalText` | `generate_court_decisions.enrich_full_text` (from the RIK detail page) |
   | Riigikohus peeps | `estleg:judge` | `generate_court_decisions.extract_judges` / `backfill_rk_identity` (judges only) |
   | Local scrape cache | `krr_outputs/.cache/court_decisions/*.txt` | `fetch_decision_text`; git-ignored, delete the case's entry |
   | Lower-court peeps | `estleg:summary` | `generate_lower_court_decisions.decision_node` |
   | CURIA peeps | `rdfs:label` | `generate_eu_court_decisions` |
   | Derived artefacts | named graphs (`serialize_named_graphs`), retrieval chunks (`generate_retrieval_projection`), tabular export (`serialize_tabular`), release assets (`build_release_assets`) | rebuilt from the peeps |
   | Consumers | MCP endpoint, Seadusloome | redeploy after the rebuild |

5. **Apply through a suppression list that survives regeneration.** A
   hand edit of a peep is lost on the next scrape, so a removal must be data
   the generators read. **Proposed, not yet created:**
   `data/court_name_suppressions.json`. It is not created in this change
   because the generator that has to read it, `generate_court_decisions.py`,
   is outside this change's scope; a suppression file nothing reads would look
   like a control without being one. Design:

   ```json
   {
     "_comment": "Name suppressions (#720). Stores SHA-256 hashes of surface forms, never the names.",
     "suppressions": [
       {
         "id": "SUP-0001",
         "corpus": "riigikohus",
         "case_number": "<case number>",
         "fields": ["estleg:summary", "estleg:legalText"],
         "surface_form_sha256": ["<sha256 of each casefolded inflected form>"],
         "replacement": "initials",
         "request_ref": "<private advisory id>",
         "decided_on": "YYYY-MM-DD"
       }
     ]
   }
   ```

   Wiring, in the follow-up: (a) `decision_to_node` and `enrich_full_text`
   run the suppression pass right after `screen_personal_data`, hashing every
   candidate token run from the `check_reidentification` detector and
   replacing a matching run with its initials; (b) an offline backfill in the
   style of `screen_court_personal_data` applies new entries to the committed
   peeps; (c) a `validate_all` gate fails a release if any suppressed hash
   still matches a run in its case. Hashing every inflected form keeps the
   suppression list itself free of personal data.

6. **Rebuild and redeploy** the derived artefacts in the table above, then
   redeploy the MCP endpoint and notify the Seadusloome operator.
7. **Releases.** Publish the next release with the removal. Edit the release
   notes of earlier releases to say an erasure was applied, without naming
   anyone, and remove or replace the affected assets where the host allows it.
8. **Respond** to the requester in the private advisory, then close it.

## 9. Lower-court sweep gate

`python3 -m estleg.generate_lower_court_decisions --fetch --apply` writes
live first- and second-instance decisions whose summaries can name private
persons. It now **refuses with exit code 2** unless
`--signoff data/lower_court_signoff.json` (or another path) is given and the
record validates:

| Key | Rule |
|---|---|
| `approved_by` | A role, never a name: `maintainer`, `data_protection_officer` or `controller_representative` |
| `approved_on` | ISO date, not in the future |
| `expires_on` | ISO date, not before `approved_on`, not in the past |
| `dpo_reference` | Non-empty free text pointing at the decision record |
| `scope.courts` | Non-empty subset of `county`, `administrative`, `circuit` |
| `scope.date_from` / `scope.date_to` | ISO dates; a `--year` run must fall inside |
| `scope.max_decisions` | Positive integer; `--limit` may not exceed it |

Rows the API returns outside the approved courts or dates are dropped before
writing. A dry run (`--fetch` without `--apply`) and the offline
`--from-fixture` path stay allowed. The repository ships
`data/lower_court_signoff.json` as a **fail-closed template**: `approved_on`,
`expires_on` and `dpo_reference` are `null`, so every live `--apply` is refused
until the controller fills it in. Tests: `tests/test_lower_court_signoff.py`.

## 10. Retention

- The corpus mirrors public case-law and is kept for as long as the project
  publishes it. That indefinite retention is justified only while the stored
  form matches the official publication. The live re-identification check is
  the control, and the working position is to run it before every release.
  **PENDING DPO confirmation** (opened 2026-10-08).
- Local scrape caches under `krr_outputs/.cache/` are git-ignored, never
  published, and expire on the pipeline's cache TTL. Delete a case's entry when
  applying a removal.
- Erasure-request records keep request id, dates, outcome and case numbers
  only, for as long as the corpus is published.

## 11. Verification checklist (#720)

- [ ] **Re-identification check (critical).** *Partly done.* Done: pipeline
      provenance shows no source joins
      (`src/estleg/generate_court_decisions.py`); offline scan run 2026-10-08
      (`krr_outputs/reports/reidentification_check_report.json`, numbers in
      section 7). **Pending:** the live comparison against `rikos.rik.ee`.
      Owner: maintainer. Opened 2026-10-08.
- [ ] **CURIA party names.** **Pending.** Confirm that CURIA party names as
      stored match CURIA's published form, including CURIA's own anonymisation
      of natural persons in recent case-law. The offline detector covers
      Riigikohus only; extending it to CURIA labels is the next step. Owner:
      maintainer. Opened 2026-10-08.
- [ ] **Article 10 basis** for the KarS / criminal-conviction content.
      **Pending DPO.** Analysis in section 4. Owner: DPO, once the maintainer
      appoints one. Opened 2026-10-08.
- [ ] **Lawful-basis confirmation** under Art. 6(1)(e)/(f) for a non-authority
      republisher. **Pending DPO.** Working position in section 4: 6(1)(e)
      unlikely, 6(1)(f) with a written balancing test. Owner: DPO. Opened
      2026-10-08.
- [x] **Retention and data-subject rights process.** Done 2026-10-08:
      contact route (section 6), erasure and objection SOP (section 8),
      retention (section 10). Residual items in the register below: the
      suppression list wiring and the contact alias.

## Register of pending decisions

| Decision | Owner (role) | Opened | Status |
|---|---|---|---|
| Name the controller | maintainer | 2026-10-08 | pending |
| Appoint or engage a DPO | maintainer | 2026-10-08 | pending |
| Names policy (keep / pseudonymise / drop) | DPO | 2026-10-08 | pending |
| Art. 6 basis and balancing test; Art. 10 position | DPO | 2026-10-08 | pending |
| Transfer mechanism for GitHub hosting | DPO | 2026-10-08 | pending |
| Run the live re-identification check | maintainer | 2026-10-08 | pending |
| Sign off a lower-court sweep (`data/lower_court_signoff.json`) | maintainer / DPO | 2026-10-08 | pending; template fails closed |
| Shared contact alias (#721) | maintainer | 2026-10-08 | pending |
| Wire `data/court_name_suppressions.json` into the generator | maintainer | 2026-10-08 | proposed |

## Machine-readable flags

`metadata.jsonld`:

- the Riigikohus distribution carries `estleg:containsPersonalData: true` plus a
  `dcterms:rights` note;
- the CURIA distribution carries `estleg:containsPersonalData: true` plus a
  `dcterms:rights` note;
- first/second-instance ingest (`krr_outputs/kohtud/`) is covered by this
  notice even without a separate `dcat:distribution` row. The committed sample
  is additionally flagged `estleg:isSampleData: true` on its graph header;
- `estleg:containsPersonalData` is declared as an `owl:DatatypeProperty` in
  `krr_outputs/controlled_vocabulary.jsonld`; `metadata.jsonld` no longer
  embeds a T-Box `@graph` (#433).

In-band flag on the Dataset head of each release aggregate (#720): pending in
this release cycle. The CURIA dump's head will carry
`estleg:containsPersonalData: true`; the other aggregates `false`.

Per court-decision node (`krr_outputs/riigikohus/` and `krr_outputs/kohtud/`):

- `estleg:personalDataScreened: true`: the node passed through
  `screen_personal_data`;
- `estleg:personalDataMaskedCount`: how many codes were removed from that node.

Both terms are declared in `krr_outputs/controlled_vocabulary.jsonld`.
