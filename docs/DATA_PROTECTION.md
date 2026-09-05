# Data protection — personal data in court decisions (GDPR)

> **Status: DRAFT, pending Data Protection Officer (DPO) / legal confirmation.**
> The legal bases below are the bases the project **relies upon pending DPO
> confirmation**; they are not a settled legal determination. Do not treat this
> as legal advice and do not publish on the strength of it until signed off.
>
> Related: [`DATA_RIGHTS.md`](DATA_RIGHTS.md) (copyright / DB-right layers),
> top-level [`NOTICE`](../NOTICE).

## What personal data the corpus contains

Two subcorpora carry personal data about identifiable natural persons:

| Subcorpus | Location | Records | Where the personal data sits |
|---|---|---|---|
| Estonian Supreme Court decisions (Riigikohus) | `krr_outputs/riigikohus/` | 12,104 | full personal **names** appear in `estleg:summary`; personal-identification codes are masked at write time (see below) |
| First/second-instance decisions (kohtud) | `krr_outputs/kohtud/` | 1 (sample) | The committed corpus is a **one-decision sample**, flagged `estleg:isSampleData: true` on the graph header and `"sample": true` in `KOHTUD_INDEX.json` (#689). It holds search metadata only (court name, case number, date). Live `--fetch` may copy `kokkuvote` summaries that name persons — treat the directory as personal-data-bearing. |
| EU Court of Justice decisions (CURIA) | `krr_outputs/curia/` | ~22,290 | **party names** appear in `rdfs:label` |

These are flagged in `metadata.jsonld` on the corresponding `dcat:distribution`
entries with `estleg:containsPersonalData: true`.

### Special-category / sensitive implications
Estonian Supreme Court criminal decisions attach **Penal Code (KarS) charges to
identifiable persons**. Data about criminal convictions and offences is subject
to the heightened regime of **Article 10 GDPR** (and may, in context, reveal
Article 9 special-category data). This raises the sensitivity well above
ordinary personal data and is a primary reason this document exists.

## Implemented control — personal identification codes are masked (#683)

Estonian personal identification codes (`isikukood`) are **direct identifiers**
under the GDPR. Unlike the names question below, this one is no longer a flag:
the codes are screened out of published court text, and the screening is
enforced by a release gate.

**How it works**

- `estleg_common.screen_personal_data(text)` is the single screening helper.
  It replaces every isolated, checksum-valid, date-plausible 11-digit run with
  the placeholder `[isikukood eemaldatud]` and returns only the **masked** form
  of what it removed (e.g. `344****38`). The full code is never returned,
  logged, or persisted anywhere in the repository.
- It is applied at **both court write sites**, so newly generated Riigikohus
  decisions are screened before they ever reach disk.
- The committed corpus was backfilled by the offline pass
  `python3 -m estleg.screen_court_personal_data` (shim:
  `scripts/screen_court_personal_data.py`).
- Every Riigikohus decision node now carries the stamps
  `estleg:personalDataScreened: true` and `estleg:personalDataMaskedCount`, so a
  consumer can tell a screened node from an unscreened one without re-running
  the detector.
- `scripts/validate_all.py` carries the gate `validate_no_personal_codes`: a
  surviving code anywhere in `estleg:summary` / `estleg:legalText` is a
  release-blocking error. The gate calls the same detection routine the screener
  rewrites from, so the two cannot disagree, and it reports only masked forms.

**Carve-out.** A run introduced by a label that states the number is *not* a
person — `registrikood` / `reg. kood` (a Latvian company registry code is also
11 digits) or `otsuse nr` — is left intact unless a personal-code label also
precedes it.

**Live result over the committed corpus**

| Metric | Count |
|---|---|
| Decision nodes scanned | 12,104 |
| Nodes with a masked code | 21 |
| Codes masked | 27 |
| Of those, labelled `isikukood` | 27 (all) |

The per-node record is `krr_outputs/reports/personal_data_mask_report.json`. It
stores the masked display forms only.

## Still open — personal names (#720)

Screening covers **codes**, not **names**. Full personal names still appear in
`estleg:summary` on the Riigikohus subcorpus and party names still appear in
`rdfs:label` on CURIA. The name question is tracked as **#720** and remains
subject to the DPO items below; nothing in this section resolves it.

## Legal basis relied upon (pending DPO confirmation)

The basis the project relies upon, **pending DPO confirmation**, is:

- **Article 6(1)(e) GDPR** — processing necessary for the performance of a task
  carried out in the public interest (public access to, and transparency of,
  case-law), and/or **Article 6(1)(f)** — legitimate interests (legal research,
  building an open legal knowledge graph), balanced against data-subject rights;
- read together with the **Kohtute seadus (Courts Act) court-decision
  publication regime**, under which Estonian court decisions are published; and
- the **Andmekaitse Inspektsioon (AKI) court-anonymisation rules** that govern
  how and which decisions are published with or without names.

For the **criminal-conviction** content, the additional **Article 10 GDPR**
condition (processing under the control of official authority / authorised by
law) must be satisfied — **VERIFY** that the project's processing falls within
an authorised basis rather than merely mirroring a public feed.

> **This is a draft position.** The DPO must confirm: (a) that a 6(1)(e)/(f)
> basis is actually available to a non-authority project, (b) the Article 10
> position for the criminal material, and (c) whether any data-subject
> information / objection handling is required.

## Reusers become independent controllers — warning

If you download and **republish** `krr_outputs/riigikohus/` and/or
`krr_outputs/curia/`, you are **not** a mere conduit. You become an
**independent data controller** for that personal data, with your own GDPR
obligations: your own lawful basis, your own transparency duties, your own
handling of data-subject rights (access, erasure, objection), and your own
liability. The project's position (above) does **not** transfer to you and does
**not** cover your processing. Assess your basis before republishing, and
consider whether you need the names at all for your use case.

## Verification TODO (for the human / DPO)

> These items concern **names**, not codes: personal identification codes are
> already masked and gated (see "Implemented control" above). The items below
> are for the data owner / DPO to resolve, and are tracked as **#720**. If item
> 1 reveals re-identification beyond the official feed, **redaction of names is
> required** before any publication.

- [ ] **Re-identification check (critical).** Confirm whether `rikos.rik.ee`
      (and the Riigikohus feed) **served these names**, i.e. the stored names
      match the official **anonymised** publication — OR whether the project
      **re-identified** persons beyond RIK's anonymised feed (e.g. by joining
      other sources, or by ingesting a non-anonymised channel). If the latter,
      the data must be **redacted** to match the official anonymisation before
      any republication. (Names only — codes are already masked, #683.)
- [ ] **CURIA party names.** Confirm CURIA party names as stored match CURIA's
      published form, including any anonymisation CURIA itself applies to
      natural persons in recent case-law.
- [ ] **Article 10 basis** for the KarS / criminal-conviction content.
- [ ] **Lawful-basis confirmation** under Art. 6(1)(e)/(f) for a non-authority
      republisher, with the Kohtute seadus / AKI regime.
- [ ] **Retention & data-subject rights** process: how an erasure/objection
      request against a name in the corpus would be handled.

## Machine-readable flags

`metadata.jsonld`:

- the Riigikohus distribution carries `estleg:containsPersonalData: true` plus a
  `dcterms:rights` note;
- the CURIA distribution carries `estleg:containsPersonalData: true` plus a
  `dcterms:rights` note;
- first/second-instance ingest (`krr_outputs/kohtud/`) is covered by this
  notice even without a separate `dcat:distribution` row — treat it like
  Riigikohus if summaries are stored. The committed sample is additionally
  flagged `estleg:isSampleData: true` on its graph header;
- `estleg:containsPersonalData` is declared as an `owl:DatatypeProperty` in the
  metadata `@graph`.

Per court-decision node (`krr_outputs/riigikohus/`):

- `estleg:personalDataScreened: true` — the node passed through
  `screen_personal_data`;
- `estleg:personalDataMaskedCount` — how many codes were removed from that node.

Both terms are declared in `krr_outputs/controlled_vocabulary.jsonld`.
