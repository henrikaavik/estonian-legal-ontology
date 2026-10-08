# Data rights — layered rights model

> **Status: DRAFT, pending legal / data-owner sign-off.** This document explains
> the rights position the project intends to operate under. Items marked
> **VERIFY** depend on upstream source terms or on legal review that is not yet
> complete. Do not present the VERIFY items as settled law, and do not rely on
> this document for redistribution decisions until it has been signed off.
>
> Related: top-level [`NOTICE`](../NOTICE) (the machine-adjacent attribution
> statements and the scope of the MIT licence), [`LICENSE`](../LICENSE) (the
> bare MIT text, code/scripts only), [`REUSE.toml`](../REUSE.toml) (the
> licence of each path, machine-readable), and
> [`docs/DATA_PROTECTION.md`](DATA_PROTECTION.md) (personal data / GDPR).

## Why the code licence is not the data licence

The repository's `LICENSE` is the MIT License. MIT is a **software** licence and
covers only the repository software (`src/estleg/`, `estleg_client/`, `scripts/`, `mcp_server/`, `tests/`, and
build/tooling configuration). It was never
capable of granting rights over the **data** corpus under `krr_outputs/`,
because the corpus contains **third-party legal material** that
the project did not author and does not own:

- Estonian statutes and regulations from **Riigi Teataja**;
- **EU legal acts** from EUR-Lex;
- **EU Court of Justice** (CURIA) decisions;
- **Estonian Supreme Court** (RIK / Riigikohus) decisions;
- **draft legislation** from EIS (eelnoud.valitsus.ee).

Applying a blanket `dcterms:license = MIT` to the whole dataset (as the metadata
previously did) was therefore incorrect in three ways: it purported to license
texts the project does not own, it ignored the **EU sui generis database right**
(Directive 96/9/EC), and it omitted the source-acknowledgement that EU material
requires under **Commission Decision 2011/833/EU** and the EuroVoc reuse terms.

## The two layers

The dataset is a **compilation**, and rights attach at two different layers.

### Layer (a) — third-party texts
The legal texts themselves. The project did not author them; each retains its
own source rights. A reuser must comply with the **source** terms, not with any
licence the project might offer. See the per-source breakdown below.

### Layer (b) — the original compilation (the project's contribution)
The genuine work product of this project: the **selection and arrangement**, the
**cross-reference / harmonisation links**, the **minted `estleg:` IRIs**, the
**ontology / TBox structure**, the **controlled vocabularies**, and the
**derived classifications**. This is the layer in which the project itself may
hold a database right, and it is the only layer the project is in a position to
license. It is offered under **CC BY 4.0** (a draft election — CC0 1.0 is an
acceptable alternative the data owner may pick instead).

A reuser must satisfy **both** layers. Layer (b)'s CC BY 4.0 offer does **not**
unlock layer (a).

## Per-source rights (layer (a))

### European Union material — EUR-Lex acts, CURIA decisions, EuroVoc
> © European Union, 1998–2026 — reused under **Commission Decision 2011/833/EU**.

Reuse of EU documents is authorised provided the **source is acknowledged** and
the meaning is **not distorted**. Where a third party holds rights in a specific
document, those are reserved. **EuroVoc** identifiers and labels are
© European Union / Publications Office and are reused under the Publications
Office's EuroVoc reuse terms.

- **VERIFY:** the exact current EuroVoc licence wording and required attribution
  string before publishing.
- **VERIFY:** that no "third-party rights reserved" EU documents were ingested
  verbatim.

### Estonian statutes & regulations — Riigi Teataja
The **text** of legislation is **excluded from copyright** under **§ 5 of the
Autoriõiguse seadus** (the Estonian Copyright Act): legislation and
administrative documents are not objects of copyright. So the raw statutory text
is itself free of copyright.

That is **not** the whole picture. Two things may still carry terms:

1. the **consolidated, machine-readable product** Riigi Teataja serves (its
   specific consolidation, structure, and presentation); and
2. a **sui generis database right** in the Riigi Teataja database as a whole.

- **VERIFY:** Riigi Teataja's live terms of use / reuse conditions and any
  API/bulk-extraction terms before redistributing the consolidated texts. This
  document does **not** assert that such redistribution is cleared — only that
  the raw statutory text is copyright-free.

### Estonian draft legislation — EIS
Drafts are Estonian public-sector legislative material; the underlying drafts
are not objects of copyright under § 5 Autoriõiguse seadus. **VERIFY** EIS reuse
terms for accompanying explanatory memoranda or third-party annexes before bulk
redistribution.

### Estonian Supreme Court decisions — RIK / Riigikohus
Court judgments are official documents, **not objects of copyright** under § 5
Autoriõiguse seadus. **But copyright-free is not republication-free:** these
records contain **personal data**, so GDPR and the Estonian court-publication /
anonymisation regime govern reuse. See [`DATA_PROTECTION.md`](DATA_PROTECTION.md).

## What this means for a reuser (checklist)

1. **Code** (`src/estleg/`, `estleg_client/`, `scripts/`, `mcp_server/`, `tests/`): MIT.
2. **Compilation layer** (links, IRIs, structure): CC BY 4.0 — attribute the
   project (draft).
3. **EU texts**: acknowledge "© European Union, [year]" + Decision 2011/833/EU;
   keep meaning intact; carry EuroVoc attribution. (VERIFY exact strings.)
4. **Estonian texts**: statutory/judgment text is copyright-free, but check
   Riigi Teataja's live DB/reuse terms for the consolidated product. (VERIFY.)
5. **Court decisions** (riigikohus/, curia/): you become an **independent GDPR
   controller** on republication — read `DATA_PROTECTION.md` first.

## Machine-readable reflection

`metadata.jsonld` reflects this model: the blanket dataset-level MIT licence has
been removed; dataset-level `dcterms:rights` now points to this document and the
`NOTICE`; and each `dcat:distribution` carries a per-source `dcterms:rights`
statement (and, for the court subcorpora, `estleg:containsPersonalData`).

## Open verification items (for the human / DPO / legal)

Reviewed on 2026-10-08 (#721). An item is ticked only where evidence in this
repository closes it; the others stay open with the maintainer as owner and
the evidence that would close them.

- [ ] Confirm the EuroVoc licence + attribution string (Publications Office).
  Owner: maintainer. Open as of 2026-10-08. Closes with the Publications
  Office's current EuroVoc reuse statement, cited with its URL and retrieval
  date, and the attribution string copied from it into `NOTICE`.
- [ ] Confirm Riigi Teataja's live reuse/DB terms for consolidated texts and API.
  Owner: maintainer. Open as of 2026-10-08. The 2026-09 public-sector review
  found no published terms of use for the RT public API
  (`docs/public-sector-review-2026-09/worksheets/public-sector-landscape.md`,
  "Things I could not confirm"). Closes with RT's published terms, or a
  written answer from Riigi Teataja, covering the consolidated texts and bulk
  extraction through the API.
- [ ] Confirm EIS reuse terms for memoranda / annexes.
  Owner: maintainer. Open as of 2026-10-08. Nothing in the repository records
  EIS terms. Closes with the eelnoud.valitsus.ee terms of use, or a written
  answer from its operator, covering explanatory memoranda and annexes.
- [ ] Confirm the data-owner's election for layer (b): **CC BY 4.0** vs **CC0**.
  Owner: maintainer. Open as of 2026-10-08. The repository already publishes
  CC BY 4.0 for the compilation layer (`CITATION.cff`, `krr_outputs/void.ttl`,
  the combined-file headers and `REUSE.toml`), but `NOTICE` still calls the
  election a DRAFT. Closes when the maintainer signs off and the DRAFT marker
  is removed from `NOTICE` and this document.
- [x] Confirm no third-party-rights-reserved EU documents were ingested verbatim.
  Verified 2026-10-08 against the committed `krr_outputs/eurlex/*_peep.json`
  and `krr_outputs/curia/*_peep.json`, from which the EU aggregates are
  rebuilt (#705). Their 55,542 nodes carry bibliographic metadata only:
  CELEX and ECLI identifiers, titles, dates, document types and EUR-Lex
  links. None carries `estleg:legalText` or `estleg:summary`, so no EU
  document body is reproduced. Re-check if a future ingest adds EU body text.
