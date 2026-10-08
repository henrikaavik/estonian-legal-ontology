# Proposal: a read-only legal reference-resolution service for Sätla

| ADR field | Value |
|---|---|
| Status | **Proposed** — 2026-10-08 |
| Ticket | #725 (review item 3.2), epic #676 |
| Deciders | Maintainer; accountable owner and external counterpart **not yet named** (see [Open decisions](#open-decisions-maintainer-only)) |
| Depends on | #707 (review 2.1, RT ELI identity), #708 (review 2.2, materialised ELI triples), #728 (content-negotiated w3id), #730 (institutional home) |
| Measured against | `tier1/wave5` at `c0140d497e`; dataset `owl:versionInfo` 1.0.0 |

**Kokkuvõte.** Ettepanek Sätla projektile (JDM, Riigikantselei, Riigikogu
Kantselei): ainult lugemiseks mõeldud viidete lahendamise teenus, mis teisendab
viite „seadus + § + lõige" (nt `KarS § 141 lg 1`) estlegi püsiidentifikaatoriks
ja edasi Riigi Teataja aadressiks, ning tagastab selgesõnaliselt vastuse
„tundmatu" või „mitmetähenduslik", kui kindlat vastavust pole. Ametlik allikas
jääb Riigi Teatajaks. Enne ehitamist tuleb kokku leppida omanik, vastaspool ja
haldusmudel; esimene samm on piiratud ja mõõdetud piloot.

## Context

### Audience and landscape

Sätla is the data-centric replacement for the EIS draft-legislation system
(JDM with Riigikantselei and Riigikogu Kantselei). Its enabling act passed on
10 June 2026, and government amendment bills are authored in Sätla from
1 October 2026 (`docs/PUBLIC_SECTOR_REVIEW_2026-09.md:265`). That date is a
landscape fact, not this project's deadline: an unsolicited third-party service
cannot become a first-stage dependency, and whether Sätla will consume an
external identifier API is unconfirmed. Drafting needs one capability this
corpus already half-has: turn a written reference into a stable identifier for
the act, the section (paragrahv, §) and the subsection (lõige), and from there
into the official Riigi Teataja (RT) address.

### Identifiers the repository mints today

- **Act IRIs** are `estleg:<ABBREV>_Map`, minted by `mint_act_iri`
  (`src/estleg/estleg_common.py:797-812`); the namespace is
  `https://w3id.org/estleg/`. The yearless form replaced `_Map_2026`; the
  14,440 old→new `owl:sameAs` rows live in `data/act_iri_v2_sameas.jsonld`,
  which is not on the public load surface (#707).
- **`<ABBREV>`** comes from `data/law_abbreviations.json` (AGENTS.md:43-50).
  Collisions are resolved by tier, and the loser takes a `_2`, `_3` suffix
  (`src/estleg/migrate_uris.py:520-531`). The IRI prefix is therefore **not**
  the citation abbreviation: Karistusregistri seadus holds `KARIST`, so the
  Penal Code (KarS) is `KARIST_2`. `registry_cited_abbrev` strips the suffix
  for display (`estleg_common.py:150-152`).
- **§ IRIs** are `estleg:<ABBREV>[_Osa<N>]_Par_<n>`; a superscript becomes a
  trailing `_<n>`, so `§ 22¹` is `_Par_22_1` (`src/estleg/law_structure.py:290-303`).
- **Lõige IRIs** are `…_Par_<n>_Lg_<m>`, with `lg 2¹` as `_Lg_2_1`
  (`law_structure.py:638-678`). AGENTS.md:54 still writes `_Lg<n>`; the code
  and corpus use `_Lg_<n>`.
- **Version IRIs** are `…_Par_<n>_v<redactionId>` (`estleg:ProvisionVersion`)
  in `krr_outputs/provision_versions/`. Each carries `versionValidFrom`,
  `versionValidTo`, `supersededByVersion` and `estleg:rtUrl`
  (`src/estleg/consolidate_tbox.py:403-409`). Versions exist at § level only.
- **Stability.** Law and provision local names are frozen for MINOR and PATCH
  releases; a rename is MAJOR (`docs/STABILITY.md:23-30`).

Two consequences matter for a resolver. A § IRI cannot be computed from the
citation, because the `_Osa<N>` segment and the registry prefix are both
corpus facts. KarS § 141 lives in `estleg:KARIST_2_Osa2_Par_141`. And legacy
families keep raw-slug prefixes: the constitution (PS) resolves to
`estleg:eesti_vabariigi_pohiseadus_Par_12` and VÕS to
`estleg:volaoigusseadus_Osa10_Par_1043`. A lookup service is the right shape,
and a URI template is not.

### Identity to Riigi Teataja today

- The generator stamps `dcterms:source` `https://www.riigiteataja.ee/akt/<id>.xml`
  and `estleg:kehtiv` on act roots (`src/estleg/generate_all_laws.py:660-680`).
  The `<id>` belongs to the consolidated text the snapshot was taken from. It is
  redaction-specific and changes when RT publishes a new consolidation. The
  generator can also stamp `estleg:terviktekstId` (`generate_all_laws.py:507-515`),
  but no committed law peep carries it yet.
- Since the RT relaunch on 2026-06-01 the `.xml` path returns the HTML app
  shell (`src/estleg/riigiteataja_common.py:310-317`). The MCP server already
  emits the human form `/akt/<id>` (`mcp_server/estleg_mcp/data.py:931-953`).
- No Estonian ELI exists in the corpus. `docs/SCHEMA_REFERENCE.md:34` records
  that Estonia has no registered ELI URI template. The only ELI-shaped values
  are `estleg:officialEnglishText` links to `/en/eli/{tolkeSeosId}`, which
  name an English translation, not the Estonian work
  (`SCHEMA_REFERENCE.md:62`).
- `owl:sameAs` on law roots points only at Wikidata (KarS:
  `wikidata.org/entity/Q2352833`).
- Multi-part codes carry the RT source on the part, not the root:
  `estleg:KARIST_2_Map` has none, while `estleg:KARIST_2_Osa2` has
  `/akt/122122025002.xml`. MCP therefore returns `rt_url: ""` for KarS
  (`mcp_server/README.md:200-212`).

### Lookup surfaces that already exist

- **MCP server** (`mcp_server/`, 20 tools). `get_provision(law, paragraph, as_of)`
  resolves a law by title, abbreviation or slug (`data.py:441-456`) and a §
  by number (`data.py:1073-1094`). It returns `id`, `rt_url` and, with
  `as_of`, the redaction window (`mcp_server/estleg_mcp/server.py:193-240`).
  It has no lõige parameter. The `_is_provision` defect named in the ticket is
  **fixed**: `data.py:796-815` accepts bare `estleg:LegalProvision`, and a
  startup check guards it (#678).
- **Python client.** `estleg_client.resolve_iri` confirms that an IRI exists by
  loading the law whose registry prefix matches (`estleg_client/load.py:324-352`).
  It works in the IRI→node direction only.
- **w3id PURL.** `w3id/estleg/.htaccess:26-34` sends every term IRI with a
  302 to the GitHub repository. Content negotiation is commented out and is
  #728. The PURL itself is live (#516 and #494 are closed).
- **Citation parser.** `extract_citations_from_text`
  (`src/estleg/extract_cross_references.py:1378`) parses abbreviation,
  genitive full-title and self references. `build_abbreviation_to_prefix`
  resolves registry-first and falls back to the title (`:320-375`, #696).
  `resolve_citation` searches every `_Osa` prefix of the law and prefers a
  lõige IRI (`:1959-2031`). The service would reuse this code, not fork it.

### Measured coverage

Measured on 2026-10-08 by an ad-hoc script over the law peeps listed in
`krr_outputs/INDEX.json`. Act roots are `estleg:Act` or `estleg:Law` nodes
whose `@id` ends in `_Map`.

| Quantity | Count |
|---|--:|
| Laws in `INDEX.json` (with provisions / treaty stubs / empty) | 1,122 (756 / 365 / 1) |
| Act roots found | 1,119 |
| Roots with `dcterms:source` on riigiteataja.ee (all `/akt/<id>.xml`) | 984 (87.9 %) |
| Roots with `owl:sameAs` or `skos:exactMatch` to an RT ELI | 0 |
| Roots with `eli:id_local` / `estleg:globalId` or `terviktekstId` | 0 / 0 |
| Roots with an ELI-shaped value (all English `/en/eli/`) | 371 |
| § nodes (`estleg:LegalProvision`) on law peeps | 39,545 |
| § nodes with an `_Osa<N>` segment / a raw-slug prefix | 3,015 / 3,075 |
| Lõige nodes (`estleg:Subsection`) | 112,043 |
| § nodes with at least one lõige node | 31,063 (78.6 %) |
| § nodes with at least one `ProvisionVersion` (all carry `rtUrl`) | 38,274 (96.8 %) |
| Registry keys (`rt_api` / `existing` / `auto`) | 601 (216 / 68 / 317) |
| Registry abbreviations with a collision suffix | 31 |
| INDEX laws present in the registry | 578 of 1,122 |
| Citation abbreviations known / mapped to corpus prefixes | 287 / 282 |
| Abbreviations with more than one registry candidate | 4 (TKS, IKS, KAVS, PGS) |

End-to-end probe through `build_provision_index` and `resolve_citation`:

| Input | Result today | Service answer should be |
|---|---|---|
| `KarS § 141 lg 1` | `KARIST_2_Osa2_Par_141_Lg_1` | resolved, lõige |
| `KarS § 141 lg 9` (no such lõige) | `KARIST_2_Osa2_Par_141`, silently | degraded, § only, with a reason |
| `TsÜS § 22 lg 2` | `TsUS_Osa2_Par_22` (no lõige nodes) | degraded, § only |
| `KarS § 9999` | `[]` | unknown provision |
| `KarS § 141 lõige 1` / `lõiget 1` / `lõikega 1` / `lg-s 1` | lõige dropped by the parser | resolved, lõige (parser fix) |
| `KarS-i § 141` | not parsed | resolved, § (parser fix) |

## Decision (proposed)

Offer Sätla a **read-only reference-resolution and identifier service**. It
holds no legal text of its own and never asserts legal effect. Riigi Teataja
remains the only authentic source.

**In scope.**

1. Three resolution levels: act, §, lõige.
2. Directions: citation string → estleg IRI → RT address; estleg IRI →
   canonical citation and RT address; RT act id → estleg IRI. The ELI
   direction is enabled per act only once #707 establishes the identity.
3. Explicit outcomes on every answer: `resolved`, `degraded` (answered at a
   coarser level than asked, with a reason), `ambiguous` (with candidates),
   `unknown_act`, `unknown_provision`, `unparsed`.
4. A point-in-time variant through the provision-version layer: the §
   redaction in force on a date, with its window and RT redaction address.

**Out of scope.** Writing to Sätla or RT; full-text search; amendment-formula
parsing (review item 3.1); court data; legal advice. The service resolves
references to legislation only. It neither stores nor returns court decisions
or personal data, so the court-data personal-data position
(`docs/DATA_PROTECTION.md`) does not apply. Queries may contain unpublished
draft wording, so query bodies are not logged (see [Risks](#risks)).

## API sketch

Base path `/resolve/v1`. JSON over HTTPS, UTF-8 in and out, `GET` for single
lookups and `POST` for batches.

| Endpoint | Purpose |
|---|---|
| `GET /resolve/v1/reference?q=<citation>[&as_of=<date>]` | Parse and resolve one citation string |
| `POST /resolve/v1/references` | Batch of up to 200 citation strings (one draft's references) |
| `GET /resolve/v1/iri/<local-name>` | estleg IRI → level, canonical citation, RT address |
| `GET /resolve/v1/rt/<rt-id>` | RT act or redaction id → estleg act or version IRI |
| `GET /resolve/v1/meta` | Dataset version, build commit, snapshot dates, coverage figures |

Example: `GET /resolve/v1/reference?q=KarS%20§%20141%20lg%201`, using real corpus
values.

```json
{
  "query": "KarS § 141 lg 1",
  "status": "resolved",
  "level": "loige",
  "parsed": {"act": "KarS", "paragraph": "141", "loige": "1"},
  "act": {
    "iri": "https://w3id.org/estleg/KARIST_2_Map",
    "title": "Karistusseadustik",
    "citation_abbrev": "KarS",
    "rt_url": "https://www.riigiteataja.ee/akt/122122025002",
    "rt_url_basis": "dcterms:source of estleg:KARIST_2_Osa2 (part node)",
    "snapshot_date": "2026-05-24",
    "eli": null,
    "eli_status": "not_established"
  },
  "paragraph": {"iri": "https://w3id.org/estleg/KARIST_2_Osa2_Par_141", "label": "§ 141. Vägistamine"},
  "loige": {"iri": "https://w3id.org/estleg/KARIST_2_Osa2_Par_141_Lg_1", "label": "§ 141 lg 1"},
  "provenance": {"official": ["numbering", "rt_url"], "derived": ["iri", "citation_abbrev", "parsed"]},
  "dataset": {"version": "1.0.0", "build": "<commit>"}
}
```

With `&as_of=2015-06-01` the answer adds the § redaction:

```json
"version": {
  "iri": "https://w3id.org/estleg/KARIST_2_Osa2_Par_141_v123122014016",
  "valid_from": "2015-01-01",
  "valid_to": "2015-09-22",
  "rt_url": "https://www.riigiteataja.ee/akt/123122014016"
},
"status": "degraded",
"degraded_reason": "point_in_time_is_paragraph_level"
```

The status is `degraded` because versions are §-level; a lõige-level
point-in-time answer would claim more than the data holds. An ambiguous
abbreviation returns `"status": "ambiguous"` with a `candidates` array of act
IRIs and titles, and never a guess.

**Error semantics.** Every resolution outcome, including `unknown_*`, is HTTP
200 with a `status`. HTTP errors are reserved for request faults (400
malformed input, 413 batch too large, 429 rate limit with `Retry-After`) and
service faults (503 while the corpus index is loading). This matches the MCP
convention that an unknown target is a `note` and not an error
(`docs/STABILITY.md:31-36`).

**Versioning.** The path carries the API major version and each response the
dataset version. A breaking change ships as `/v2` with at least six months of
overlap. IRIs follow the `@id` policy above.

**Rate limits.** Proposed: 20 requests per second and 200 references per batch
per authenticated client, 2 per second anonymous; final numbers depend on the
operating model.

**MCP equivalent.** One new tool, `resolve_reference(citation, as_of=None)`,
returns the same JSON. It would lift `get_provision`'s § limit and expose
lõige, ambiguity and degradation, so LLM clients and Sätla share one contract.

## Data contract and quality statement

| Element | Class | Source |
|---|---|---|
| Legal text, § and lõige numbering, redaction windows | Official, copied | RT XML; copied, not authenticated |
| RT act and redaction ids, `rt_url` | Official identifier, derived address | `dcterms:source`, `estleg:rtUrl` |
| estleg IRIs | Derived, project-minted | Registry and generators above |
| Citation abbreviation → act | Derived; 216 of 601 entries are RT `lühend` | `data/law_abbreviations.json` |
| Citation parsing | Derived, heuristic | `extract_cross_references.py` |
| RT URL inherited from a part node | Derived, flagged in `rt_url_basis` | KarS-style multi-part codes |
| Estonian ELI | Not available | Blocked on #707 |

The service publishes its coverage figures at `/meta` and states three limits
plainly. The corpus is a snapshot (`estleg:kehtiv`, mostly 2026-05-24), not a
live mirror of RT. An empty answer does not prove that a provision does not
exist. The data licence is a draft pending sign-off (`docs/DATA_RIGHTS.md:3-7`).

## Prerequisites and dependencies

- **#707 (review 2.1).** RT must confirm the Estonian ELI template; then
  `eli:id_local`, the ELI `owl:sameAs`, and the legacy-IRI bridge on the load
  surface. Until then the ELI field is `null` with `eli_status`. This proposal
  does not fork that work.
- **#708 (review 2.2).** Materialised ELI triples serve SPARQL consumers with
  inference off; the JSON API does not need them.
- **#728.** Until content-negotiated w3id lands, the IRIs do not dereference
  to a node description.
- **#730.** A government system should not depend on a namespace one person
  maintains.
- **New follow-ups**: see [Feedback to the backlog](#feedback-to-the-backlog).

## Operating model options

| Option | What it is | For | Against |
|---|---|---|---|
| A. Project-hosted endpoint | Add `/resolve/v1` beside the existing estleg-mcp HTTP deployment | Fastest; reuses the MCP transport, token and health check | Bus factor 1; personal hosting; no service level a ministry can rely on |
| B. Static release tables | Publish versioned lookup tables (citation → act, act → § → lõige, version windows, RT ids) with each release, plus the parser in `estleg_client` | No servers; cacheable; Sätla can embed or mirror them; auditable | No server-side parsing for non-Python clients; freshness only per release |
| C. Hosted by Sätla or RIK | The institution runs the service, or absorbs the mapping into RT | Durable; matches the authority of the data | Needs #730 and a counterpart who says yes |

**Recommendation.** Use B as the contract artefact and pilot basis. Run A only
as a demonstration endpoint for the pilot, labelled without a service level.
Treat C as the target if Sätla confirms demand. In the long run the
act-level identity belongs to RT through ELI. This project's lasting value is
the provision-level mapping, the citation parser and the version windows.

## Bounded pilot

**Build.** Do this only after an owner is named.

1. The option B static tables, generated from a tagged release.
2. A `resolve_reference` function over the existing parser with the outcome
   statuses above, replacing silent § fallback with `degraded`.
3. The parser fixes from the probe table, and a thin option A HTTP wrapper.

Time-box: four weeks of effort.

**Gold set.** 300 hand-labelled references: 100 from EIS draft explanatory
memoranda (seletuskiri) already in the corpus, 100 from in-law
cross-references, and 100 from citation forms a Sätla counterpart supplies
(RT text if none). Each is labelled with the expected act, §, lõige and
outcome. Court decision text is not used.

**Metrics and pass thresholds.**

| Metric | Threshold |
|---|---|
| Precision of `resolved` answers, at the level returned | ≥ 0.98 |
| Recall at § level for references whose act is in the corpus | ≥ 0.90 |
| Silent degradation (coarser answer without `degraded`) | 0 |
| `ambiguous` answers that list the correct candidate | 1.00 |
| Act answers that carry an RT address | ≥ 0.95 |
| Batch of 200 references, p95 latency on the demonstration host | ≤ 2 s |

The measured results are appended to this document as an amendment. That
appendix satisfies the ticket criterion "deliver a bounded pilot with
measured results".

## Success measures beyond the pilot

- The Sätla counterpart confirms in writing whether they would consume the
  service, and through which option.
- No unflagged wrong resolution is reported within three months of use.
- Tables are regenerated within one release cycle of an RT consolidation that
  changes a resolved act.
- The ELI direction is enabled for every act that #707 covers.

## Risks

- **No consumer.** Sätla may build resolution in-house or rely on RT. Asking
  before building limits that loss to this document.
- **Bus factor and namespace.** One maintainer controls both the code and the
  w3id entry (#730).
- **Identifier surprises.** `KARIST_2` is not `KarS`, and raw-slug prefixes
  persist. They are frozen by the `@id` policy, so the service must return
  them as they are and must not rename them.
- **Staleness and premature ELI.** Snapshot dates lag RT, so every answer
  carries its snapshot date. The ELI field stays `null` until #707, because
  minting before RT confirms the template would create wrong identifiers.
- **Confidential drafts.** Unpublished draft wording may reach the service.
  Log only counters and status codes, never query bodies. If a third party
  hosts it, this needs a written agreement.
- **Upstream change and licence.** The 2026-06-01 RT relaunch already broke
  the `.xml` addresses, so the service emits and tests the human `/akt/<id>`
  form. The data-rights position is a draft, so it returns identifiers and
  short labels, not full legal text.

## Open decisions (maintainer only)

1. **Owner.** Name the accountable owner of this proposal and the pilot.
2. **External counterpart.** The ticket names a landscape candidate: the Sätla
   project manager at JDM (Karmen Vilms), with Riigikantselei and Riigikogu
   Kantselei. Decide whether, when and through whom to send this document.
   The JDM project page was stale when last checked.
3. **Operating model.** Accept or change the recommendation above.
4. **Licence.** Sign off the data-rights election before any external use.
5. **Service level and access.** Decide between a public anonymous tier and
   Sätla-only tokens, and what availability the project can promise.
6. **Pilot go/no-go.** Decide whether to run the pilot before or after the
   counterpart replies.

## Feedback to the backlog

These follow-ups are proposed for the Tier 0–2 backlog whatever Sätla decides:

- Parser: accept nominative `lõige`, `lõiget`, `lõikega`, `lg-s` and the
  inflected abbreviation `KarS-i`.
- `resolve_citation`: return the level actually resolved, so callers can tell
  a lõige hit from a § fallback.
- Act roots of multi-part codes (KarS, VÕS) should carry the RT source that
  their part nodes already have.
- Regenerate the abbreviation registry against the current INDEX (578 of
  1,122 laws are covered).
- Correct `_Lg<n>` to `_Lg_<n>` in AGENTS.md.
