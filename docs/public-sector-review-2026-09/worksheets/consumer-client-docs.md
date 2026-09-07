<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Consumer surface review — client library, docs, DCAT metadata, exports

Review lens: usefulness for the Estonian public sector. Three readers:
(a) ministry data steward evaluating an avaandmed.eesti.ee listing or internal ingest;
(b) RIK / Riigikogu Kantselei / KOV shared-service developer with one week;
(c) legal adviser at Justiitsministeerium or Õiguskantsler who will never write SPARQL.

## Scope & method

Read in full: `estleg_client/__init__.py`, `estleg_client/load.py`, `estleg_client/cli.py`,
`examples/quickstart.py`, `tests/test_issue_551_client.py`, `README.md` (931 lines),
`docs/README.md`, `docs/API_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/STABILITY.md`,
`metadata.jsonld`, `krr_outputs/void.ttl`, `CITATION.cff`, `krr_outputs/retrieval/llms.txt`
and its `README.md` / `manifest.json`, `w3id/estleg/.htaccess`, `docker-compose.yml`,
`pyproject.toml`. Skimmed `docs/RELEASE.md`, `docs/SCHEMA_REFERENCE.md` headings,
`docs/VALIDATION_REPORT.md`, `docs/DATA_PROTECTION.md`.
Extracted the Estonian overview `docs/eesti-oigusontoloogia-ulevaade.html` to plain text
and keyword-scanned it.

Verification performed (not just reading):

- Ran `estleg.validate_all.metadata_stats()` and `validate_metadata_catalog()` against the
  working tree at HEAD `c96577d50c` with `krr_outputs/` and `metadata.jsonld` **clean**.
- Counted `estleg:CourtDecision` nodes across `krr_outputs/riigikohus/riigikohus_*_peep.json`
  with the validator's own predicate.
- Resolved `https://w3id.org/estleg/`, `/1.0.0`, `/vocabulary`, `/KarS_Par_1` and the dataset
  IRI live, with and without RDF `Accept` headers.
- Checked every documented `Path.glob(...)` in `docs/API_GUIDE.md` against real filenames.
- Decompressed and counted `krr_outputs/exports/estleg_all_sample.nq.gz`.
- Read headers and row counts of all five `krr_outputs/exports/*.csv`.
- Resolved `2676a1f81b4f…` against tag `v1.0.0`.

---

## Reader-journey assessment

### (a) Ministry data steward — **blocked**

The steward's job is to answer three questions: what is the licence, who do I contact,
and do the published numbers match the data. All three fail.

**Licence is not machine-readable anywhere.** Not one of the nine `dcat:Distribution`
entries in `metadata.jsonld:337-473` carries `dcterms:license`, and the dataset itself has
no dataset-level `dcterms:license` either. What exists is a 700-word English prose blob at
`metadata.jsonld:76` beginning "DRAFT layered rights model — pending legal sign-off", plus
per-distribution prose `dcterms:rights` strings that each also begin "DRAFT". A DCAT
harvester on avaandmed.eesti.ee reads the triple, not the prose, and will render
"licence unknown". A steward who reads the prose sees the word DRAFT nine times and stops.

Worse, the two machine-readable licence assertions that *do* exist state something the
project explicitly denies. `krr_outputs/void.ttl:29` asserts
`dcterms:license <https://creativecommons.org/licenses/by/4.0/>` on the whole
`void:Dataset`, and `CITATION.cff:21` declares `license: CC-BY-4.0` for the dataset.
Both contradict `metadata.jsonld:76` ("This dataset is NOT licensed as a whole … the
compilation layer … CC BY 4.0"). `void.ttl:30` tries to narrow it in an adjacent
`dcterms:rights` string, but a harvester never sees that. The corpus is therefore
*over-licensed in machine-readable form* — the opposite of the careful position the prose
takes, and the exact failure mode a Justiitsministeerium reviewer would catch.

**No contactable contact point.** `metadata.jsonld:233-239` gives `vcard:fn` and
`vcard:hasURL` pointing at a GitHub profile. There is no `vcard:hasEmail`. The maintainer
email is present in the repository, at `w3id/estleg/.htaccess:6`, but not in the catalogue
record. `dcterms:publisher` (`metadata.jsonld:47-54`) is a personal GitHub account, not an
asutus; avaandmed.eesti.ee organises listings by publishing institution.

**The published statistics are wrong, and the gate that should catch it is red.**
Running the repo's own catalogue validator at HEAD on a clean tree:

| Statistic | Published | Actual corpus |
|---|---|---|
| `estleg:totalFiles` | 23,118 | 26,837 |
| `estleg:courtDecisionCount` | 12,137 | 12,104 |
| `estleg:enactedLawFileCount` | 1,190 | 1,195 |

Six errors, including the mirrored `dcat:distribution` count keys. `README.md:7` advertises
this gate as the thing that keeps the numbers honest. It is currently failing, so the
front-door headline (`README.md:8`), the DCAT record, the Estonian ministry overview, the
API guide and the retrieval manifest all publish numbers the data does not support.

### (b) Integrator with one week — **slow to first value, then capable**

The good news: once past the front door, the material is genuinely strong. Load surfaces
are explicitly named and distinguished (`README.md:62-69`, `docs/ARCHITECTURE.md:40-56`),
the stub-closure invariant is documented with the exact SPARQL filter to apply, the
measured rdflib load budget is published (`README.md:77-80`: 2,247,778 triples, 44.4 s,
3,089 MB peak RSS), and there is a vocabulary cheat-sheet (`README.md:294-306`) that
answers the five questions a new integrator actually has.

The bad news is that every advertised fast path is a stub:

- **`docker compose up`** (`README.md:111-136`, `docs/API_GUIDE.md:242-247`) is presented as
  a one-command SPARQL endpoint. It loads `krr_outputs/exports/estleg_all_sample.nq.gz`,
  which decompresses to **14 quads**. The README does say "fixture-sized", but the
  integrator who then runs the advertised `SELECT ?g (COUNT(*))` query gets a toy result.
  Real data requires `serialize_named_graphs --write` over a 2.4 GB clone with LFS
  materialised.
- **Tabular export** (`README.md:138-160`) is described as "a star-schema projection for
  pandas/R". `laws.csv` contains **one data row**. There is no CSV for regulations, drafts,
  EU acts or EU court decisions at all — only laws, provisions, citations, sanctions and
  court decisions. `provisions.csv` ships `temporalStatus`, `valid_from` and `valid_to`
  columns that are empty in every sampled row. This is the format a KOV IT shop or a
  ministry analyst is most likely to want, and it is the least developed.
- **The Python client** covers roughly 5% of the corpus (see below).
- **`llms.txt`** (`krr_outputs/retrieval/llms.txt:21-27`) links `chunks.jsonl`, `outlines/`
  and `context_packs/` as its three headline artifacts. All three are git-ignored by design
  (`krr_outputs/retrieval/README.md:29-31`) and are not release assets either. Every link
  in the AI-facing entry point is dead.

Two documented Python examples are silent no-ops. `docs/API_GUIDE.md:194` globs
`sanctions_dir.glob("*_peep.json")` but all 294 files are named `sanctions_*.json`;
`docs/API_GUIDE.md:222` globs `amendments_dir.glob("*_peep.json")` but all 5,647 are
`amendments_*.json`. Both loops iterate zero times and print nothing. There *is* a gate,
`tests/test_documented_examples.py:492-510`, which execs every API_GUIDE Python block — but
it only asserts the block does not raise. A zero-match glob does not raise, so the gate is
green while the example is useless. The developer concludes the sanctions and amendments
data is missing.

`docs/API_GUIDE.md:440-454` offers a "REST API Design Suggestions" section listing eleven
`/api/...` endpoints, directly contradicting `README.md:14` ("There is no REST `/api`
surface"). An integrator skimming the API guide reasonably concludes an API exists.

`README.md:14` also claims the MCP query layer has **15 tools**; `mcp_server/HANDOFF.md:26`
says **14 tools**.

### (c) Legal adviser who will never write SPARQL — **not served**

There is **no browse or explore surface of any kind**. No GitHub Pages site, no `index.html`,
no hosted HTML view of the corpus, no faceted search. The only non-technical artefact is
`docs/eesti-oigusontoloogia-ulevaade.html`, and `README.md:5` links it through
`htmlpreview.github.io` — a third-party rendering proxy that many ministry networks block
and that will silently fail if the repository ever goes private.

That Estonian overview is also the only Estonian-language document in the repository, and it
is materially incomplete for its stated audience (section 7 is titled
"Kasutusjuhud Ministeeriumitele"). Keyword-scanning its extracted text:

| Topic the reader needs | Present? |
|---|---|
| Litsents / CC BY / autoriõigus | **no** |
| Isikuandmed / GDPR | **no** |
| Viitamine / tsiteerimine | **no** |
| Version number (1.0.0) | **no** |

A Õiguskantsler or Justiitsministeerium adviser reading the Estonian document is given
eight ministry use cases and is never told that the Riigikohus subcorpus contains full
personal names attached to KarS charges — Article 10 GDPR criminal-conviction data, per
`metadata.jsonld:438` and `docs/DATA_PROTECTION.md:17` — or that republishing makes them an
independent controller. That warning exists only in English, only in files a non-technical
reader will never open.

The Estonian document is also stale against the 1.0.0 release. Its own status line reads
"Seis: 8. mai 2026", four months before the 2026-08-19 release. Its statistics band claims
"1 145 / 1 190 seaduse registrikirjet / seadusefaili" against a current 1,122 / 1,195, and
"Valideeritud failid 23 113" against a README claim of 23,069 and an actual 26,837. Section 6
("Tulevikuvisioon") still lists a SPARQL endpoint and versioned provision history as future
work, though `docker-compose.yml` and the `provision_versions/` layer both shipped.

---

## DCAT-AP compliance checklist

Assessed against DCAT-AP 2.1/3.0 mandatory (M), recommended (R) and optional (O) classes,
with the Estonian open-data profile in mind.

### `dcat:Dataset`

| Property | Level | Present | Note |
|---|---|---|---|
| `dcterms:title` | M | yes | `metadata.jsonld:20-29`, bilingual en/et. Good. |
| `dcterms:description` | M | yes | `:30-39`, bilingual. Good. |
| `dcat:contactPoint` | R | partial | `:233-239` — `vcard:Individual` with `fn` + `hasURL`, **no `vcard:hasEmail`**. Not actionable. |
| `dcat:distribution` | R | yes | 9 entries, `:337-473`. |
| `dcat:keyword` | R | yes | `:152-229`, 19 keywords, en + et. Good. |
| `dcterms:publisher` | R | yes | `:47-54` as `foaf:Agent`. Correct type, but a personal GitHub account, not an organisation. |
| `dcat:theme` | R | yes | `:136-151`, EU `data-theme` authority (JUST, GOVE). Correct authority. |
| `dcterms:spatial` | R | yes | `:240-246`, EU `country/EST` authority. Correct. |
| `dcterms:temporal` | R | yes | `:247-257`, 1993-01-01 → 2026-05-01 with `dcat:startDate`/`endDate`. Correct shape. |
| `dcterms:accrualPeriodicity` | R | **non-conformant** | `:261-263` uses `http://purl.org/cld/freq/monthly`. DCAT-AP requires the EU authority `http://publications.europa.eu/resource/authority/frequency/MONTHLY` (verified live, HTTP 200). SEMIC SHACL shapes flag this. |
| `dcterms:language` | R | inconsistent | `:71-75` lists only `language/EST`, but titles, descriptions and keywords are en+et and `schema:inLanguage` (`:290`) says `["et","en"]`. `ENG` is missing. |
| `dcterms:identifier` | R | **absent** | Only `schema:identifier` (`:285`). No `dcterms:identifier`. |
| `dcterms:issued` / `dcterms:modified` | R | yes | `:55-62`. |
| `dcterms:accessRights` | R | **absent** | No `PUBLIC`/`RESTRICTED` from the EU `access-right` authority. For a dataset flagged `estleg:containsPersonalData` twice (`:421`, `:439`) this is the single field a steward looks for. |
| `dcterms:conformsTo` | O | present but broken | `:258-260` points at `https://w3id.org/estleg/vocabulary`, which 302-redirects to the GitHub repository homepage. No vocabulary document is served. |
| `dcterms:license` (dataset) | R | **absent** | See below. |
| `dcat:landingPage` | O | yes | `:230-232`. |
| `owl:versionInfo` / `versionIRI` | — | yes | `:63-66`. |
| `dcat:Catalog` wrapper | M for harvest | **absent** | The file is a bare `dcat:Dataset`. Most DCAT-AP harvesters expect a `dcat:Catalog` with `dcat:dataset`. |
| `prov:wasGeneratedBy` | O | absent here | Present in `void.ttl:35` but not in `metadata.jsonld`. |

### `dcat:Distribution` (all nine)

| Property | Level | Present | Note |
|---|---|---|---|
| `dcat:accessURL` | **M** | 9/9 | But one is a **relative IRI**: `metadata.jsonld:464` gives `"krr_outputs/changes-0.11.0.jsonld"`, which is not a valid absolute IRI for DCAT and will resolve against whatever base the harvester uses. |
| `dcterms:license` | R | **0/9** | No distribution carries a licence IRI. Prose `dcterms:rights` only. |
| `dcterms:format` | R | **4/9** | Missing on "Combined enacted laws", "Domestic regulations (state-level)", "Combined draft legislation", "Combined EU legislation", "Combined EU court decisions". |
| `dcat:mediaType` | R | 9/9 | All `application/ld+json`. |
| `dcat:downloadURL` | R | **8/9** | Missing on "Domestic regulations (state-level)" (`metadata.jsonld:371-380`) — access only, no download. |
| `dcat:byteSize` | R | **0/9** | Absent everywhere. A steward cannot size an ingest; the largest distribution is a ~250 MB gzip. |
| `dcterms:title` / `description` | R | 9/9 | Descriptions are unusually good — they name the exact gotchas (e.g. `:365` warns that court-decision enum individuals live in `riigikohus_schema.json` and are not merged into the combined file). |

### Additional integrity issue

All eight GitHub `tree/` and `archive/` URLs pin commit `2676a1f81b4f3a583825239656dc7665b9e3294b`
(`metadata.jsonld:343, 346, 375, 429, 432, 447, 450, 467`). That commit is *an ancestor of*
tag `v1.0.0` (`f018cf05f2`), not the release commit — its subject is
"Drop `_Map_year` from act IRIs and ASCII-transliterate keys". A steward following
`dcat:accessURL` therefore browses a pre-release tree state, not the 1.0.0 content the
record describes. `validate_all` checks only that the URLs are pinned to *that* SHA or a
tagged asset, so it will not catch the drift.

### w3id resolution

`docs/ARCHITECTURE.md:101-103` and `w3id/estleg/README.md:28-30` both state that
`https://w3id.org/estleg/` "is still 404". **This is stale — it resolves.** Verified live:

```
https://w3id.org/estleg/            302 -> https://github.com/henrikaavik/estonian-legal-ontology
https://w3id.org/estleg/1.0.0       302 -> .../releases/tag/v1.0.0
https://w3id.org/estleg/vocabulary  302 -> https://github.com/henrikaavik/estonian-legal-ontology
https://w3id.org/estleg/KarS_Par_1  302 -> https://github.com/henrikaavik/estonian-legal-ontology
```

The redirect ignores `Accept: text/turtle` and `Accept: application/ld+json` — the
content-negotiation block in `w3id/estleg/.htaccess:22-26` is commented out. So every
`estleg:` term IRI, the `void:uriSpace` (`void.ttl:38`), the `void:exampleResource`
`estleg:KarS_Par_1` (`void.ttl:39`) and `dcterms:conformsTo` all dereference to one HTML
repository page. The persistent-identifier story is "stable ID now, resolver later", which
is honest, but the docs claiming 404 undersell what already works while the missing conneg
oversells what a linked-data client gets.

---

## Strengths (with file:line)

1. **Load surfaces are named, distinguished, and honest about their limits.**
   `README.md:62-69` and `docs/ARCHITECTURE.md:40-56` define combined-only vs full-public vs
   retrieval, say exactly which queries need which, and state that
   `combined_ontology.jsonld` strips `hasVersion` rather than leaving it dangling. Most
   published legal graphs do not tell you this at all.

2. **The stub-closure contract is explicit and query-safe.** `estleg:isStubNode` is defined
   as a build marker, not a semantic claim, in three places
   (`README.md:305`, `docs/STABILITY.md:13-17`, `docs/ARCHITECTURE.md:48-53`), each with the
   `FILTER NOT EXISTS` clause to apply. `examples/quickstart.py:39-44` actually uses it.

3. **A real predicate stability tier table.** `docs/STABILITY.md:6-13` splits predicates into
   Stable / Additive / Heuristic / Build-marker and names which are classifier output that
   may be rewritten. This is the single most valuable page for an integrator and is rare in
   comparable datasets.

4. **Measured, not guessed, load cost.** `README.md:77-80` publishes triple count, wall time
   and peak RSS for the combined file, and warns it is a single top-level `@graph` array and
   therefore not line-streamable. That lets a steward size infrastructure before downloading.

5. **The rights analysis is legally serious even though the encoding is wrong.**
   `metadata.jsonld:76` and the per-distribution rights strings correctly separate statutory
   text (§5 Autoriõiguse seadus), EU material (Commission Decision 2011/833/EU), the sui
   generis database right, and the original compilation layer, and flag the personal-data
   subcorpora individually. The thinking is right; only the machine-readable expression is
   missing.

6. **The bilingual DCAT title/description/keyword coverage is genuinely done.**
   `metadata.jsonld:20-39` and `:152-229` carry `@language`-tagged en and et values, which is
   more than most Estonian open-data listings manage.

7. **`quickstart.py` degrades gracefully.** `examples/quickstart.py:88-137` detects an
   un-materialised Git LFS pointer and falls back to two committed peeps plus a sanctions
   sidecar, so the three-command start prints answered sentences even without `git lfs pull`.
   `main()` returns 0 on every failure path rather than a traceback (`:301-323`).

8. **The client's error messages teach.** `estleg_client/load.py:152-166` distinguishes
   "ambiguous, here are five candidates" from "not found, here are the three name forms you
   can use", with a worked example of each.

9. **VoID linksets are real and specific.** `krr_outputs/void.ttl:41-117` declares eight
   linksets with correct `void:linkPredicate` / `void:target` pairs to EuroVoc, CELLAR,
   Riigi Teataja, Wikidata (three distinct ones), the EU e-Justice ECLI resolver and EHAK.
   Federation engines can consume this standalone.

---

## Weaknesses / gaps (with file:line, severity)

**S1 — CRITICAL. Published DCAT statistics do not match the corpus; the catalogue gate is
red at HEAD.** `metadata.jsonld:353, 367-368, 440, 476-484` versus the live corpus. Six
validator errors on a clean tree. The wrong `12,137` alone propagates to nine files:
`README.md:8`, `README.md:490`, `docs/README.md:8`, `docs/README.md:119`,
`docs/API_GUIDE.md:5`, `docs/DATA_PROTECTION.md:17`, `docs/VALIDATION_REPORT.md:91`,
`krr_outputs/retrieval/README.md:17`, `metadata.jsonld:440` and `:481`.
Note the gate topology: `tests/test_validate_all.py:1475` enforces README ↔ metadata, and
`validate_metadata_catalog` enforces metadata ↔ corpus. The first passes because both sides
are equally wrong; the second fails and is apparently not blocking.

**S2 — CRITICAL. No machine-readable licence, and the two that exist are wrong.**
Zero `dcterms:license` on 9/9 distributions and none at dataset level in `metadata.jsonld`.
Meanwhile `krr_outputs/void.ttl:29` and `CITATION.cff:21` both assert CC BY 4.0 over the
whole dataset, contradicting `metadata.jsonld:76`. This simultaneously blocks an
avaandmed.eesti.ee listing and misstates the rights position in the two files most likely to
be machine-harvested.

**S3 — HIGH. Both published Riigikohus case-type tables are fabricated.**
`README.md:481-490` gives Administrative 9,561 / Civil 970 / Criminal 484 / Constitutional
336 / Misdemeanor 107 / Other 679. `docs/README.md:121-128` gives Civil 4,745 / Criminal
3,422 / Administrative 2,392 / Constitutional 792 / Other 679 / Misdemeanor 107. They
contradict each other, both sum to the (wrong) 12,137, and neither matches the corpus.
Counting `estleg:CourtDecision` nodes directly across all 34 year files gives Civil 4,988 /
Criminal 3,686 / Administrative 2,434 / ConstitutionalReview 800 / Misdemeanor 107 /
Other 89 = 12,104 — exactly `krr_outputs/riigikohus/RIIGIKOHUS_INDEX.json`
`case_type_counts`. `docs/README.md:219` claims that table is sourced from that index; it is
not. The README version inverts the largest and third-largest chambers, which a legal
adviser would notice immediately and which would destroy trust in every other number.

**S4 — HIGH. The Estonian ministry document has no licence, no personal-data warning, no
citation guidance, and no version.** `docs/eesti-oigusontoloogia-ulevaade.html` — the only
Estonian-language artefact and the one explicitly addressed to ministries — contains none of
"litsents", "CC BY", "autoriõigus", "isikuandmed", "GDPR", "1.0.0" or any citation
instruction. Its status line (`:490`) reads 8 May 2026; its statistics band claims 1,145
registry records against a current 1,122. It is reachable only via `htmlpreview.github.io`
(`README.md:5`).

**S5 — HIGH. No non-technical browse surface exists.** No GitHub Pages, no `index.html`,
no hosted viewer. Reader (c) has no entry point at all. The only public runtime is the MCP
endpoint at `https://estleg.sixtyfour.ee/mcp` (`README.md:14`), which requires an MCP client.

**S6 — HIGH. The Python client covers roughly 5% of the corpus.**
`estleg_client/load.py:20-22` hard-codes `krr_outputs/INDEX.json` and
`data/law_abbreviations.json`; `load_law` (`:247-263`) parses only the INDEX-listed enacted-law
peeps plus a `sanctions/sanctions_<stem>.json` sidecar. There is no path to the 3,812 state
regulations, 11,059 KOV regulations, 12,104 court decisions, 22,832 drafts, 33,242 EU acts or
22,290 EU court decisions. For the public sector this is close to inverted priorities: a KOV
shared-service wants municipal regulations; a ministry lawyer wants court decisions and
transposition; the client serves neither.

Secondary client issues:
- `provisions_of` / `sanctions_of` (`:266-281`) match by **substring** on the type IRI, so
  `sanctions_of` would also return anything typed `estleg:SanctionType`, and
  `provisions_of` will never see `estleg:KovProvision`.
- `resolve_iri` without a graph (`:324-352`) guesses the owning law from the compact-id
  prefix and then parses that entire law; it silently returns `None` for any court, EU or
  draft IRI.
- Both list helpers return bare IRI strings — no label, no text, no §-number. To print
  "§ 1 and its text" the consumer must drop to rdflib anyway, which defeats the client.
- No `py.typed` marker despite full annotations, so typed consumers get `Any`.
- Not published to PyPI (no publish workflow in `.github/workflows/`), and the distribution
  is `estonian-legal-ontology` (`pyproject.toml:2`) which bundles the producer package and
  pulls `requests`, `pyshacl` and `beautifulsoup4` (`:6-15`) for a read-only consumer.
- The package ships **no data**: `corpus_root()` (`:43-82`) walks the filesystem for
  `krr_outputs/INDEX.json`. A hypothetical `pip install` yields a client that raises
  `FileNotFoundError` until the user clones 2.4 GB. There is no download helper.

**S7 — MEDIUM. Two documented API_GUIDE examples are silent no-ops, and the gate cannot see
it.** `docs/API_GUIDE.md:194` (`sanctions/*_peep.json` — actual files are `sanctions_*.json`)
and `:222` (`amendments/*_peep.json` — actual files are `amendments_*.json`) match zero files
each. `tests/test_documented_examples.py:492-510` execs the blocks and asserts only that
they do not raise.

**S8 — MEDIUM. The two "fast path" exports are near-empty.**
`krr_outputs/exports/laws.csv` has one data row. `estleg_all_sample.nq.gz` decompresses to
14 quads and is the default for `docker compose up`. `provisions.csv` has empty
`temporalStatus` / `valid_from` / `valid_to` columns. No CSV exists for regulations, drafts
or EU material.

**S9 — MEDIUM. `llms.txt` links three artifacts that do not exist in the repo or in any
release.** `krr_outputs/retrieval/llms.txt:21-27` points at `chunks.jsonl`, `outlines/` and
`context_packs/`; `krr_outputs/retrieval/README.md:29-31` confirms all three are git-ignored.
The whole projection is also pinned at ontology **0.11.0** / evaluation date 2026-06-01
(`llms.txt:16`, `manifest.json`, `README.md:14`), two releases behind.

**S10 — MEDIUM. Contradictions and staleness in the front door.**
- `README.md:14` says the MCP layer has 15 tools; `mcp_server/HANDOFF.md:26` says 14.
- `README.md:14` says "There is no REST `/api` surface"; `docs/API_GUIDE.md:440-454` lists
  eleven `/api/...` endpoints.
- `README.md:504-509` CURIA table sums to 22,229, not the stated 22,290 — it omits the
  "Other / 61" row that `docs/README.md:151` has.
- `docs/ARCHITECTURE.md:101-103` and `w3id/estleg/README.md:28-30` say w3id returns 404; it
  returns 302.
- `docs/API_GUIDE.md:19` says 113 institutional competence files (actual 117); `:29` says 291
  sanction files (actual 294).
- `docs/README.md:90-96` lists `estleg:DomesticRegulation` as a core class and omits
  `NationalRegulation`, `MunicipalRegulation`, `Municipality`, `Issuer` and `KovProvision`
  that `README.md:734-743` documents. `DomesticRegulation` exists only as an abstract
  superclass in `krr_outputs/controlled_vocabulary.jsonld`; no instance carries it.

**S11 — MEDIUM. `docs/STABILITY.md` is a good contract with three holes.** It has no
deprecation window, no erratum or retraction procedure (needed: this corpus contains personal
data that may have to be withdrawn), and no support horizon or next-release date.
`:24` still reads "Amendment-family IDs may still be shortened … before v1.0; after v1.0 that
is also MAJOR" — v1.0.0 has shipped, so the sentence needs resolving to a statement of fact.

**S12 — LOW. `README.md` is 931 lines with no audience routing.** It mixes the consumer
5-minute start, the schema reference, the full ingest-operator runbook
(`:854-914`) and the KOV pipeline internals (`:439-477`) in one scroll. None of the three
target readers has a labelled path.

**S13 — LOW. `dcterms:rights` prose says "DRAFT — pending legal sign-off" ten times.**
Whatever the internal status, a ministry steward reading the catalogue record sees a dataset
whose own publisher says its rights are unresolved, and will not list it.

---

## Improvement ideas

**1. Turn the catalogue gate red-to-green and make it blocking.**
*What:* Regenerate `metadata.jsonld` statistics and all nine distribution count keys from
`metadata_stats()`; propagate the corrected 12,104 / 1,195 / 26,837 to `README.md:8`,
`README.md:490`, `docs/README.md:8,119`, `docs/API_GUIDE.md:5`,
`docs/DATA_PROTECTION.md:17`, `docs/VALIDATION_REPORT.md:91`,
`krr_outputs/retrieval/README.md:17`. Add `validate_metadata_catalog` to the required CI
checks so it cannot go red again.
*Why:* A steward's first act is to spot-check one number. Today every headline number fails
that check, which invalidates the dataset regardless of its actual quality.
*Effort:* S. *Impact:* H.
*Files:* `metadata.jsonld`, `README.md`, `docs/README.md`, `docs/API_GUIDE.md`,
`docs/DATA_PROTECTION.md`, `docs/VALIDATION_REPORT.md`, `krr_outputs/retrieval/README.md`,
`.github/workflows/validate.yml`.

**2. Add `dcterms:license` to every distribution and fix the two over-broad assertions.**
*What:* Give each of the nine distributions a licence IRI matching its actual source
(compilation-layer CC BY 4.0 where that is the operative offer; a `NON_PUBLIC`/custom
statement for the two personal-data distributions). Remove or narrow the whole-dataset
`dcterms:license` in `krr_outputs/void.ttl:29`. Change `CITATION.cff:21` from
`license: CC-BY-4.0` to the compilation-layer-only wording, or drop the field and rely on
`abstract`.
*Why:* Without a licence triple no DCAT harvester will list the dataset, and with the wrong
one the project is publicly over-claiming rights over third-party legal text — the exact
thing `metadata.jsonld:76` is careful to disclaim.
*Effort:* S. *Impact:* H.
*Files:* `metadata.jsonld`, `krr_outputs/void.ttl`, `CITATION.cff`.

**3. Fix the remaining DCAT-AP conformance gaps in one pass.**
*What:* Add `vcard:hasEmail` to `dcat:contactPoint`; switch `dcterms:accrualPeriodicity` to
`http://publications.europa.eu/resource/authority/frequency/MONTHLY`; add
`dcterms:accessRights` (`PUBLIC` for the non-personal distributions, `RESTRICTED` for the two
flagged ones); add `dcterms:identifier`; add `ENG` to `dcterms:language`; add
`dcterms:format` to the five distributions missing it; add `dcat:downloadURL` to the
state-regulations distribution; add `dcat:byteSize` to all nine; make
`metadata.jsonld:464` an absolute IRI; wrap the dataset in a `dcat:Catalog`; repoint the
eight pinned tree/archive URLs from `2676a1f81b` to the `v1.0.0` tag commit.
*Why:* These are the exact properties the SEMIC DCAT-AP SHACL validator and
avaandmed.eesti.ee harvesting check. Each is a one-line fix; together they are the
difference between a listable and an unlistable record.
*Effort:* M. *Impact:* H.
*Files:* `metadata.jsonld`.

**4. Rewrite the Estonian overview for the 1.0.0 release and host it properly.**
*What:* Refresh the status line, snapshot date and statistics band; add four short Estonian
sections — *Litsents ja õigused*, *Isikuandmed ja GDPR* (naming the Riigikohus and CURIA
subcorpora explicitly), *Kuidas viidata* (with the `owl:versionIRI` pin), and *Versioon
1.0.0*; move section 6's shipped items out of "Tulevikuvisioon". Publish it via GitHub Pages
at a stable URL instead of `htmlpreview.github.io`.
*Why:* This is the only document a Justiitsministeerium or Õiguskantsler adviser will read,
and it currently omits the two things that most affect whether they may use the data. The
`htmlpreview` proxy is also a plausible blocked domain on a ministry network.
*Effort:* M. *Impact:* H.
*Files:* `docs/eesti-oigusontoloogia-ulevaade.html`, `README.md:5`,
`.github/workflows/` (new Pages workflow).

**5. Correct the Riigikohus case-type tables and generate them from the index.**
*What:* Replace both tables with the real split (Civil 4,988 / Criminal 3,686 /
Administrative 2,434 / ConstitutionalReview 800 / Misdemeanor 107 / Other 89) and add a test
asserting each README table row equals `RIIGIKOHUS_INDEX.json` `case_type_counts`. Add the
missing "Other / 61" row to the CURIA table so it sums to 22,290. Do the same for the EU
legislation and draft-phase tables.
*Why:* A legal adviser knows that administrative cases do not outnumber civil cases eight to
one at the Riigikohus. One implausible table costs the whole dataset its credibility with
exactly the reader the project most wants.
*Effort:* S. *Impact:* H.
*Files:* `README.md`, `docs/README.md`, `tests/test_validate_all.py`.

**6. Extend `estleg_client` to the corpora the public sector actually asks for.**
*What:* Add `load_regulation(slug, *, kov=False)`, `load_court_decisions(year)`,
`load_drafts(phase=None)` and `load_eu_act(celex)` driven by the existing
`REGULATIONS_*_INDEX.json`, `RIIGIKOHUS_INDEX.json`, `EELNOUD_INDEX.json` and
`EURLEX_INDEX.json` registries. Change `provisions_of` / `sanctions_of` from substring to
exact type matching over a known type set (including `estleg:KovProvision`), and add
`provision_rows(graph)` returning `(iri, paragrahv, label, text, in_force)` tuples so the
common case needs no rdflib. Add `estleg_client/py.typed`.
*Why:* The three named integrator profiles map to municipal regulations (KOV shared-service),
court decisions and transposition (ministry), and drafts (Riigikogu Kantselei). None is
reachable today.
*Effort:* M. *Impact:* H.
*Files:* `estleg_client/load.py`, `estleg_client/__init__.py`, `estleg_client/cli.py`,
new `estleg_client/py.typed`, `tests/test_issue_551_client.py`.

**7. Make the two advertised fast paths real.**
*What:* Ship a genuinely useful named-graph dump as a `v1.0.x` release asset (not LFS) and
point `docker-compose.yml` at it by default, or at minimum grow
`estleg_all_sample.nq.gz` from 14 quads to a few thousand covering all seven graphs. Extend
`scripts/serialize_tabular.py` to emit `regulations.csv`, `drafts.csv`, `eu_acts.csv` and
`transposition.csv`, populate the temporal columns, and commit a sample with more than one
law in `laws.csv`.
*Why:* CSV and a live SPARQL box are what a ministry analyst and a KOV IT shop can consume in
an afternoon. Both are advertised prominently and both currently return almost nothing.
*Effort:* M. *Impact:* H.
*Files:* `docker-compose.yml`, `krr_outputs/exports/*`, `scripts/serialize_tabular.py`,
`README.md:138-160`, `README.md:111-136`.

**8. Strengthen the documented-example gate from "does not raise" to "produces output".**
*What:* Extend `tests/test_documented_examples.py` so each Python block declares an expected
minimum stdout line count or a non-empty result, then fix `docs/API_GUIDE.md:194` and `:222`
to the real `sanctions_*.json` / `amendments_*.json` patterns.
*Why:* The gate exists and is well-built; it just measures the wrong thing, so two loader
examples have shipped broken behind a green check.
*Effort:* S. *Impact:* M.
*Files:* `tests/test_documented_examples.py`, `docs/API_GUIDE.md`.

**9. Add an audience router to the top of `README.md`.**
*What:* Three links above the status line: "Ma tahan andmeid vaadata" → the Estonian
overview / Pages site; "I need to integrate in a week" → `docs/API_GUIDE.md` +
`docs/STABILITY.md` + the client; "I am assessing this for a catalogue" →
`metadata.jsonld` + `NOTICE` + `docs/DATA_PROTECTION.md` + `docs/STABILITY.md`. Move the
ingest-operator runbook (`README.md:854-914`) and the KOV pipeline log
(`README.md:439-477`) into `docs/`.
*Why:* 931 lines with no routing means all three readers hit the same wall of maintainer
detail. This is the cheapest single improvement to first-value time.
*Effort:* S. *Impact:* M.
*Files:* `README.md`, new `docs/OPERATOR_RUNBOOK.md`.

**10. Resolve the "DRAFT — pending legal sign-off" status or explain it.**
*What:* Either complete the sign-off and drop the DRAFT prefix from the ten
`dcterms:rights` strings, or add one sentence naming what specifically is unresolved
(most likely: whether stored Riigikohus names exceed RIK's anonymised feed, per
`metadata.jsonld:438`) and by when.
*Why:* A steward cannot list a dataset whose publisher says its rights position is a draft.
An explicit, bounded open question is listable; an unbounded DRAFT is not.
*Effort:* S (documentation) / L (actual legal review). *Impact:* H.
*Files:* `metadata.jsonld`, `NOTICE`, `docs/DATA_RIGHTS.md`.

**11. Fix `llms.txt` and refresh the retrieval projection to 1.0.0.**
*What:* Either publish `chunks.jsonl`, `outlines/` and `context_packs/` as release assets and
point `llms.txt` at those absolute URLs, or rewrite `llms.txt` to link only the committed
samples and say plainly that the full projection is generated locally. Regenerate the
projection at ontology 1.0.0.
*Why:* Every link in the AI-facing entry point is currently dead, and the manifest advertises
a two-release-old build.
*Effort:* S. *Impact:* M.
*Files:* `krr_outputs/retrieval/llms.txt`, `krr_outputs/retrieval/README.md`,
`krr_outputs/retrieval/manifest.json`, `scripts/generate_retrieval_projection.py`.

**12. Stand up the w3id content negotiation, and correct the docs that say it is 404.**
*What:* Uncomment and adapt the conneg block in `w3id/estleg/.htaccess:22-26`, submit the
follow-up PR to `perma-id/w3id.org`, and serve a small `vocabulary.ttl` (the TBox) at
`https://w3id.org/estleg/vocabulary` so `dcterms:conformsTo` resolves.
Update `docs/ARCHITECTURE.md:101-103` and `w3id/estleg/README.md:28-30` — the namespace
resolves today.
*Why:* `dcterms:conformsTo`, `void:uriSpace` and `void:exampleResource` all currently
dereference to an HTML repository page, which fails the linked-data expectation the rest of
the project meets carefully.
*Effort:* M. *Impact:* M.
*Files:* `w3id/estleg/.htaccess`, `w3id/estleg/README.md`, `docs/ARCHITECTURE.md`,
new `krr_outputs/vocabulary.ttl`.

**13. Fill the three holes in `docs/STABILITY.md`.**
*What:* Add a deprecation window (how many MINOR releases a predicate survives after being
marked deprecated), an erratum/retraction procedure covering personal-data withdrawal, and a
support horizon or next-release date. Resolve the now-stale pre-v1.0 sentence at `:24`.
*Why:* A ministry integrating this into a production knowledge base needs to know what
happens when data must be withdrawn and how much notice a breaking change gets. Those two
questions decide whether it can be a dependency.
*Effort:* S. *Impact:* M.
*Files:* `docs/STABILITY.md`.

**14. Reconcile the small contradictions.**
*What:* `README.md:14` 15→14 tools; delete or reframe `docs/API_GUIDE.md:440-454` as
"if you build an API" so it does not read as an existing surface; correct
`docs/API_GUIDE.md:19` (117) and `:29` (294); align `docs/README.md:90-96` class list with
`README.md:734-743`.
*Effort:* S. *Impact:* M.
*Files:* `README.md`, `docs/API_GUIDE.md`, `docs/README.md`.

---

## Open questions

1. **Is `validate_metadata_catalog` currently blocking in CI?** It fails on a clean HEAD.
   Either it is not in the required-checks set, or the 1.0.0 release was cut before the
   corpus grew. Which is it, and did `v1.0.0` ship with matching numbers?
   (`.github/workflows/validate.yml` is outside my scope; flagging for the validation-gates
   reviewer, with whom this finding overlaps.)

2. **Why is `estleg:totalFiles` off by 3,719 (23,118 vs 26,837)?** That is far larger than the
   other two drifts and suggests a whole subdirectory entered or left
   `is_operational_state_file`'s exclusion set. Which one, and is 26,837 the intended number
   or a counting regression?

3. **Is the 12,137 figure a historical artefact or a real superset?** The corpus has 12,104
   Riigikohus decisions plus 3 in `krr_outputs/kohtud/` = 12,107. Where did 33 go, and should
   the headline count lower-court decisions at all now that `kohtud/` exists?

4. **Was `2676a1f81b` deliberately pinned, or is it a stale constant?** It is an ancestor of
   `v1.0.0` (`f018cf05f2`), and `validate_all` asserts that exact SHA, so the pin looks
   intentional. If so, what does a steward browsing that tree see that the release does not
   contain?

5. **Who is the intended `dcterms:publisher` for an avaandmed.eesti.ee listing?** A personal
   GitHub account will not satisfy an Estonian open-data profile that organises by asutus.
   Is there a hosting institution, or should the listing route through a ministry?

6. **What exactly is blocking the "DRAFT — pending legal sign-off" rights model?**
   `metadata.jsonld:438` names one concrete open item (whether stored Riigikohus names exceed
   RIK's anonymised feed). Are there others, and is anyone assigned?

7. **Is `seadusloome.sixtyfour.ee` intended as the public browse surface?** `CLAUDE.md`
   describes this repository as its data backend, but no consumer-facing document links it —
   only `docs/NAMESPACE_MIGRATION.md:21` mentions it in passing. If it is the answer to
   reader (c), it should be on line 5 of the README.
