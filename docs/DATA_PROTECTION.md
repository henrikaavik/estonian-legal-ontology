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
| Estonian Supreme Court decisions (Riigikohus) | `krr_outputs/riigikohus/` | ~12,137 | full personal **names** appear in `estleg:summary` |
| First/second-instance decisions (kohtud) | `krr_outputs/kohtud/` | sample first cut | search-metadata only in the committed sample (court name, case number, date). Live `--fetch` may copy `kokkuvote` summaries that name persons — treat the directory as personal-data-bearing. |
| EU Court of Justice decisions (CURIA) | `krr_outputs/curia/` | ~22,290 | **party names** appear in `rdfs:label` |

These are flagged in `metadata.jsonld` on the corresponding `dcat:distribution`
entries with `estleg:containsPersonalData: true`.

### Special-category / sensitive implications
Estonian Supreme Court criminal decisions attach **Penal Code (KarS) charges to
identifiable persons**. Data about criminal convictions and offences is subject
to the heightened regime of **Article 10 GDPR** (and may, in context, reveal
Article 9 special-category data). This raises the sensitivity well above
ordinary personal data and is a primary reason this document exists.

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

## Verification TODO (for the human / DPO — DO NOT redact yet, FLAG only)

> The flags below are intentionally **not** acted on in this change. They are
> for the data owner / DPO to resolve. If item 1 reveals re-identification
> beyond the official feed, **redaction is required** before any publication.

- [ ] **Re-identification check (critical).** Confirm whether `rikos.rik.ee`
      (and the Riigikohus feed) **served these names**, i.e. the stored names
      match the official **anonymised** publication — OR whether the project
      **re-identified** persons beyond RIK's anonymised feed (e.g. by joining
      other sources, or by ingesting a non-anonymised channel). If the latter,
      the data must be **redacted** to match the official anonymisation before
      any republication. (Flag only — not redacted in this change.)
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
  Riigikohus if summaries are stored;
- `estleg:containsPersonalData` is declared as an `owl:DatatypeProperty` in the
  metadata `@graph`.
