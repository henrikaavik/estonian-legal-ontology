<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Commons / Identity / KOV — public-sector usefulness review

Reviewer scope: `src/estleg/estleg_common.py`, the KOV layer
(`kov_registry.py`, `build_kov_registry.py`, `enrich_kov_layer1.py`,
`kov_pipeline_coverage.py`, `verify_layer1.py`,
`backfill_kov_regulation_typing.py`), the four migration scripts
(`migrate_uris.py`, `migrate_namespace.py`, `remint_act_iri_v2.py`,
`migrate_malformed_iris.py`), `data/ehak/**`, `data/law_abbreviations.json`,
`data/institution_aliases.json`, `data/wikidata_*.json`,
`data/act_iri_v2_sameas.jsonld`, `data/migration_state.json`,
`w3id/estleg/{.htaccess,README.md}`, one KOV regulation peep, and the two
README KOV sections.

---

## Scope & method

Read `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/NAMESPACE_MIGRATION.md`, then
read every scope file end to end. Every quantitative claim below was measured
against the checked-out corpus, not inferred. The measurements I ran:

- identity-field census over all 1,195 root law peeps and over
  `regulations/riik` + `regulations/kov`;
- full KOV enrichment census over all 11,059 `estleg:MunicipalRegulation` roots;
- the "Tartu linn regulations under KOKS § 22" query executed end to end;
- a diff of `estleg_common.KNOWN_ABBREVIATIONS` against
  `data/law_abbreviations.json`;
- `git grep` reachability for every module-level name in `estleg_common.py`;
- the CI legacy-namespace guard's exact grep, re-run locally.

No repository file was modified. `ruff` passes on every file in scope.

---

## Strengths (with file:line)

**S1. The KOV enabling-act layer is real and dense, not a demo.** Census over
all 11,059 `estleg:MunicipalRegulation` roots:

| Field | Coverage |
|---|---|
| `estleg:enactedBy` / `enactedByMunicipality` / `municipalityStatus` / `temporalStatus` / `regulationTypeBucket` | 11,059 (100%) |
| `estleg:preambleText` | 10,939 (98.9%) |
| `estleg:issuedUnder` | 10,078 (91.1%) |
| `estleg:implementsCitation` | 9,823 (88.8%) |

672 distinct enabling acts; 6,704 regulations issued under KOKS. This is the
project's most defensible novelty claim.

**S2. The headline municipal query works today.** Executed against the corpus:
Tartu linn (EHAK 0793) has 345 regulations; 205 carry
`estleg:issuedUnder → estleg:KOKS_Map`; 117 reach `estleg:KOKS_Par_22`
through a `estleg:Citation` node carrying `estleg:citationDetail` at
lõige/punkt granularity ("lg 1 p 6", "lg 2"). The reverse direction is
materialised too: `estleg:KOKS_Par_22` in
`krr_outputs/kohaliku_omavalitsuse_korralduse_seadus_peep.json` carries
`estleg:implementedBy` pointing back at the regulation roots.

**S3. The EHAK registry is correct and defended.**
`data/ehak/municipalities.json` holds exactly 79 municipalities (64 vald +
15 linn) across 15 counties — the correct post-2017 count.
`kov_registry.load_municipalities` (`src/estleg/kov_registry.py:24-40`)
rejects duplicate codes, non-`linn`/`vald` types, and any code that is not a
4-digit string.

**S4. The issuer → municipality mapping fails loud rather than guessing.**
`auto_match_municipality` (`kov_registry.py:130-176`) returns `None` on zero
or multiple matches; `build_issuer_registry` (`kov_registry.py:377-476`)
raises with a sorted list of every unmapped slug. `load_curated_map`
(`kov_registry.py:477-506`) rejects any curated row with empty
`mapping_evidence`. The genitive accommodation in
`_matches_municipality_root` (`kov_registry.py:96-127`) is gated to
consonant-final nominatives so `tartua` cannot match `Tartu`.

**S5. Curated rows beat the heuristic, audibly.** `build_issuer_registry`
feeds the curated CSV into `auto_match_municipality`'s `overrides` channel and
prints a `WARN:` to stderr whenever a curated code diverges from what
auto-match would have produced (`kov_registry.py:436-448`). Finding #283 in
that docstring records that the previous precedence order silently discarded
curator corrections.

**S6. Conflicting-successor detection.** `extract_historical_municipalities`
(`kov_registry.py:582-668`) raises when two issuers claim different successors
for one pre-merger EHAK code, and validates every `succeededByCode` against
the current registry. The output (151 nodes in
`data/ehak/historical_municipalities.jsonld`) carries `formerEhakCode`,
`formerName`, `succeededBy`, `mergedAt` 2017-10-15, and the verbatim
`mergerEvidence` citation.

**S7. Regulations carry their Riigi Teataja identifiers as first-class
properties.** Both `regulations/riik` and `regulations/kov` are 100% covered
for `estleg:globalId`, `estleg:terviktekstId`, and `dcterms:source`. Example:
`krr_outputs/regulations/kov/laekvere_vallavalitsus/sotsiaaltranspordi_teenustasude_osaline_kehtestamine_t1017738_peep.json`
carries `globalId 426102013029` and `terviktekstId 1017738`.

**S8. The point-in-time layer is genuinely usable.**
`krr_outputs/provision_versions/kohaliku_omavalitsuse_korralduse_seadus.jsonld`
holds 26 `estleg:ProvisionVersion` nodes for `estleg:KOKS_Par_22` alone, each
with `versionValidFrom`, `versionValidTo`, `supersededByVersion`,
`versionRedactionId`, and a resolvable `estleg:rtUrl`. This is the raw
material for the compliance feature discussed in I1 below.

**S9. Fetch security is handled properly.**
`estleg_common.assert_allowed_http_url` (`src/estleg/estleg_common.py:1966+`)
does exact-hostname matching with a trailing-dot strip; `allowed_get` defaults
`allow_redirects=False` and re-checks each hop's `Location` through
`_reject_off_host_redirect`. `parse_xml` rejects `<!DOCTYPE`/`<!ENTITY` in the
first 64 KiB and caps payload size.

**S10. Determinism discipline.** `BUILD_EVALUATION_DATE` /
`PINNED_RUN_TIMESTAMP` (`estleg_common.py:1544-1560`) and
`build_kov_registry.canonical_issuers_json` (`src/estleg/build_kov_registry.py:22-45`)
separate the hashed canonical form from the on-disk form so a field reorder
does not churn the digest. `save_json` (`estleg_common.py:1666+`) is a
tempfile + `os.replace` with best-effort cleanup.

**S11. Municipalities link out to the official classifier.**
`enrich_kov_layer1.municipality_identity_links` emits
`rdfs:seeAlso → https://metaweb.stat.ee/klassifikaator_avalik?id=EHAK&code=<code>`
on all 79 nodes.

**S12. The dual-typing defect is actually fixed.** I verified all 11,059 KOV
roots: zero carry `estleg:NationalRegulation` / `GovernmentRegulation` /
`MinisterialRegulation`. The `#267`/`#424` backfill landed.

---

## Weaknesses / risks (with file:line, severity)

### W1 — HIGH — No ELI anywhere on Estonian acts or provisions, although the project already holds the ELI ids

`eli` is declared in the shared context (`estleg_common.py:793`) but the only
ELI property used across the whole Estonian corpus is `eli:is_about` (the
EuroVoc subject alias) — 371 occurrences in a 400-file sample of root law
peeps. Zero files anywhere in `krr_outputs/` reference `riigiteataja.ee/eli/`.

The project *has* the data. `data/riigiteataja/english_eli.json` maps 989 RT
act ids, 376 of them to an `eli_id` plus a
`https://www.riigiteataja.ee/en/eli/<id>` URL. Those 376 are emitted only as
`estleg:officialEnglishText` (405 files), i.e. as an English-translation link,
never as an identifier. `data/riigiteataja/english_eli.json` is read by exactly
one consumer, `src/estleg/backfill_official_english.py:46`.

Impact: a public body's join key to Estonian legislation is ELI or the RT id.
Neither is on a law node as a property. Estonia publishes ELI at
riigiteataja.ee; the ontology does not consume it.

### W2 — HIGH — Law act roots carry no structured Riigi Teataja identifier at all

Census over all 1,195 root `*_peep.json` law files:

| Field on the act root | Count |
|---|---|
| `estleg:globalId` | 0 |
| `estleg:terviktekstId` | 0 |
| `dcterms:source` (an `.../akt/<id>.xml` URL) | 984 |
| `owl:sameAs` | 3 |
| files with no recognisable act root | 49 |

So 211 law peeps carry no Riigi Teataja link on their root at all, and the
other 984 hide the id inside a URL string. Regulations are 100% covered for
the same two fields (S7) — this is an unexplained asymmetry, not a design
choice. `estleg_common.pair_peep_with_xml` (`estleg_common.py:1793-1870`)
documents the cause: the ~615 legacy laws predate `estleg:globalId` and are
paired by filename slug instead.

Consequence: `SELECT ?act WHERE { ?act estleg:globalId "122122023007" }`
returns nothing for any statute.

### W3 — HIGH — Two abbreviation registries that disagree on 50 of 95 shared laws

`estleg_common.KNOWN_ABBREVIATIONS` (`estleg_common.py:28`, 95 entries, used by
the citation resolver and 11 other modules) and `data/law_abbreviations.json`
(601 entries, used by `migrate_uris.py` to mint IRI local names) are
independent tables. Matching them on the act title:

| Outcome | Count |
|---|---|
| agree | 17 |
| conflict | 50 |
| title absent from the IRI registry | 28 |

Representative conflicts (citation abbrev → actual IRI prefix):
`KarS → KARIST_2`, `TsÜS → TsUS`, `KrMS → KRIMIN_2`, `HKMS → HALDUS`,
`SHS → SOTSIA`, `RHS → RIIGIH`, `IKS → ISIKUA`, `KVS → KORRUP`,
`RahaPTS → RTRT`. Absent entirely: `VÕS`, `AÕS`, `ÄS`, `HMS`, `TsMS`,
`PankrS`, `LKS`, `EhS`.

`AGENTS.md` states the preference order is "the official Riigi Teataja
`lyhend` (e.g. `PKS`, `KarS`, `TsÜS`)". Measured against
`data/law_abbreviations.json`, only 216 of 601 entries have
`source: "rt_api"`; 317 are auto-derived acronyms and 68 are legacy
"existing" prefixes. So roughly **216 of 1,122 laws (19%) have an IRI local
name that matches the abbreviation an Estonian lawyer actually uses.**

### W4 — HIGH — The abbreviation IRI a consumer would guess is a tombstone

`krr_outputs/haldusmenetluse_peep.json` defines `estleg:HMS_Map` with
`owl:deprecated: true` and
`dcterms:isReplacedBy → estleg:haldusmenetluse_seadus_Map`. The live
Administrative Procedure Act therefore lives at a 27-character raw slug while
the guessable `HMS` IRI is a deprecation stub. The same shape holds for
`estleg:IKS_Map`, `estleg:ALKS_Map`, `estleg:KESK_Map`, `estleg:JAHI_Map` —
27 deprecated act IRIs in total (`krr_outputs/INDEX.json` `deprecated_laws`,
24 entries / 27 IRIs).

### W5 — HIGH — The zero-legacy-namespace CI guard is stale after the #472 package move and currently returns a hit

`.github/workflows/validate.yml:251-261` and `tests/test_no_legacy_namespace.py:20-30`
both exclude `scripts/migrate_namespace.py`. That file is now a 9-line
`runpy` shim containing no legacy string. The implementation moved to
`src/estleg/migrate_namespace.py`, which holds 12 occurrences of
the retired hostname (see [NAMESPACE_MIGRATION.md](../../NAMESPACE_MIGRATION.md))
and is **not** excluded. Running the workflow's exact grep in
this checkout returns:

```
src/estleg/migrate_namespace.py
```

The pytest guard is `@pytest.mark.corpus` so it skips locally, but the shell
step in the `json-validation` job runs unconditionally.

The same staleness is inside the migration itself:
`src/estleg/migrate_namespace.py:58` self-excludes `"scripts/migrate_namespace.py"`.
A future `--apply` run would therefore rewrite its own `REPLACEMENTS` table
(`migrate_namespace.py:44-48`), collapsing replacement #1 into a
`https://w3id.org/estleg/ → https://w3id.org/estleg/` no-op and silently
disarming the migration.

### W6 — MEDIUM-HIGH — The 151 HistoricalMunicipality nodes are orphans

Nothing in the graph points at `estleg:HistoricalMunicipality_0105`. I grepped
the whole corpus: the IRI family appears only in its own source file, in
`controlled_vocabulary.jsonld`, and in `combined_ontology.jsonld` — never as
an object of a property.

The link that would connect them is deliberately dropped.
`extract_historical_municipalities` computes `issuerSlugs` per historical unit,
and `build_historical_municipality_doc` (`src/estleg/enrich_kov_layer1.py:236`)
records that they "are used for coverage reporting only and are not serialised
onto the nodes — the issuer→historical relationship remains via the issuer
node's `estleg:historicalMunicipalityName` literal".

That literal is weak: `estleg:Issuer_abja_vallavolikogu` carries
`"estleg:historicalMunicipalityName": "Abja"` while the historical node's
`formerName` is `"Abja vald"`. `_slug_to_display_name` /
`_historical_municipality_name` (`kov_registry.py:296-320`) derive it by
title-casing the slug, which drops Estonian diacritics — the file documents
this as an accepted Layer 1 limitation, but it means the two ends cannot even
be string-joined reliably (`kohtla_jarve` → "Kohtla Jarve", never
"Kohtla-Järve").

So "which regulations did the abolished Abja vald issue, and what became of
it" cannot be answered by following edges.

### W7 — MEDIUM-HIGH — `enactedByMunicipality` always names the current successor; the issuing unit is unrecoverable

`enrich_kov_layer1._build_enriched_act_doc:415` sets
`municipality_ref = {"@id": municipality_iri(issuer["currentMunicipalityCode"])}`
and stamps it on the act and every provision. The Laekvere Vallavalitsus 2011
regulation therefore reports `estleg:Municipality_EHAK_0901` — Vinni vald, a
body that did not exist when the regulation was made. There is no
`estleg:enactedByHistoricalMunicipality` counterpart.

Defensible as "who is bound today", but the two readings are not separable.
Concretely: the 345 regulations my query attributed to Tartu linn silently
include the output of pre-2017 predecessor units. A municipality auditing "our
own regulations" and a historian asking "what did Laekvere vald enact" get the
same, differently-wrong answer. 477 of a 4,000-regulation sample carry
`municipalityStatus: "abolished"`, so the affected share is material.

### W8 — MEDIUM — 40% of law act roots still carry raw-slug IRIs, including flagship acts

Measured over all law peeps: **454 of 1,146 act roots use a lowercase
raw-slug local name**, 692 use an abbreviation. Affected acts are not only
treaties — `estleg:haldusmenetluse_seadus_Map`, `estleg:kohanimeseadus_Map`,
`estleg:alusharidusseadus_Map` are all raw slugs, and the latter two are among
the top enabling acts for municipal regulations (822 and 417 KOV regulations
respectively). Corpus-wide, **1,440 of 15,916 KOV `issuedUnder` edges (9%)
point at a raw-slug act IRI.**

`AGENTS.md` frames the 601/1,122 registry coverage gap as "not a rename
blocker". That is true of the migration, but the shipped effect is a two-tier
identifier scheme in which a consumer cannot predict an act's IRI shape.

### W9 — MEDIUM — Opaque `_N` collision suffixes land on the most-cited acts

26 registry abbreviations carry a numeric disambiguation suffix, assigned by
alphabetical arrival rather than importance:

| IRI prefix | Act |
|---|---|
| `KARIST` | Karistusregistri seadus |
| `KARIST_2` | Karistusseadustik (the Penal Code) |
| `HALDUS` | Halduskohtumenetluse seadustik |
| `HALDUS_2` | Haldusreformi seadus |
| `LIIKLU` | Liikluskindlustuse seadus |
| `LIIKLU_2` | Liiklusseadus |

Auto-derived abbreviations also produce unusable strings such as
`AVBKHKLSMKPV` and `RKIOMPU1974`
(`src/estleg/migrate_uris.py:197 auto_derive_abbreviation`).

### W10 — MEDIUM — The w3id resolver dereferences every term IRI to a GitHub repo homepage

`w3id/estleg/.htaccess:27` is `RewriteRule ^(.*)$ https://github.com/…/estonian-legal-ontology [R=302,L]`.
The content-negotiation block that would return RDF is commented out at lines
22-24. The only specific rule is line 19, hardcoded to `^1\.0\.0/?$` — every
future release needs a new hand-written rule. And the PURL itself is still 404
(PR `perma-id/w3id.org#6575` pending), so today **no estleg IRI resolves to
anything**, and after the merge `https://w3id.org/estleg/KarS_Par_141` will
resolve to an HTML repository landing page, not to a description of § 141.

### W11 — MEDIUM — The v2 act-IRI `owl:sameAs` bridge is not published on any load surface

`data/act_iri_v2_sameas.jsonld` holds 14,440 `owl:sameAs` statements bridging
the yearless v2 act IRIs to the retired `_Map_2026` forms. It is referenced by
exactly two files: `src/estleg/remint_act_iri_v2.py` and
`tests/test_issue_445_act_iri_v2.py`. It is not in `PUBLIC_LOAD_SUBDIRS`
(`estleg_common.py:262`), not merged into `combined_ontology.jsonld`
(grep count 0), and not listed in `docs/ARCHITECTURE.md`'s load-surface table.

The contrast is instructive: `data/ehak/historical_municipalities.jsonld` *is*
explicitly wired in, at `src/estleg/validate_seadusloome_sync.py:101` and
`src/estleg/shacl_validate_all.py:49`. The sameAs bridge was simply not given
the same treatment, so any consumer holding a pre-#445 IRI has no published
redirect.

### W12 — MEDIUM — Provisions carry no official identifier of any kind

`estleg:Reg_1017738_Par_1` in the sampled KOV peep carries `estleg:paragrahv`,
`estleg:partOfAct`, `enactedBy`, `enactedByMunicipality`, text, and target
group — and no `dcterms:source`, no RT deep link, no ELI. Same for
`estleg:KOKS_Par_22`. The one place a provision-level RT URL exists is
`estleg:rtUrl` on `ProvisionVersion` nodes, which live in the separate
211k-node `provision_versions/` surface that
`COMBINED_STRIPPED_PREDICATES` (`estleg_common.py:~420`) deliberately keeps out
of the flagship file.

So a public body that wants to cite "KOKS § 22 as we relied on it" must load a
second, large surface to obtain a URL.

### W13 — MEDIUM — `dcterms:source` points at the XML manifestation, not the human page

Every act node's `dcterms:source` is `https://www.riigiteataja.ee/akt/<id>.xml`.
`estleg_common.strip_xml_sameas:1451` deliberately scrubs `.xml` targets from
`owl:sameAs` (#447) but leaves `dcterms:source` untouched — documented in the
`source_provenance` docstring (`estleg_common.py:1179`). The result is that the
single citable link a consumer follows returns raw XML rather than the RT page
an official would expect.

### W14 — LOW-MEDIUM — County is a label string, not an EHAK county code

`enrich_kov_layer1.build_municipality_doc:155` emits
`"estleg:county": "Lääne-Viru maakond"`. EHAK assigns 4-digit codes to
maakonnad as well. A county-level user (maavalitsuse successor, regional
development body) can only aggregate by exact string match, and cannot join to
any other EHAK-keyed dataset at county level.

### W15 — LOW-MEDIUM — External identity linking is token-scale, and the state registers are absent

- `data/wikidata_acts.json`: 3 acts, out of 1,122.
- `data/ehak/municipality_wikidata.json`: 16 QIDs, out of 79 municipalities.
- `data/wikidata_institutions.json`: a short curated list.
- `data/institution_aliases.json`: a slug → slug alias table
  (maksuamet → maksu_ja_tolliamet) with evidence prose but **no registry
  code** — no state institution register id, no X-tee member code, no
  registrikood.

For a public body, the join key to an institution is its registrikood or
X-tee member code. Neither appears anywhere in scope.

### W16 — LOW-MEDIUM — `regulationTypeBucket` is populated but uncontrolled

100% of KOV roots carry it, but the sampled value is
`"teenustasude osaline"` — a token slice of the normalised title, not a term
from a controlled vocabulary. Cross-municipality bucketing ("show me every
municipality's jäätmehoolduseeskiri") is therefore unreliable, even though
`estleg:titleNormalized` was built for exactly that purpose.

### W17 — LOW-MEDIUM — KOV → KOV citations are dropped

The sampled preamble reads: *"Määrus kehtestatakse sotsiaalhoolekande seaduse
§ 8 punkti 2, § 26 lõike 1 punkti 4 ja § 45 lõike 1, **Laekvere Vallavolikogu
8. veebruari 2011 määruse nr 21 "Sotsiaaltransporditeenuse osutamise kord"
§ 5 lõike 1** alusel."* Three `estleg:Citation` nodes were emitted, all three
pointing at the state act (`SOTSIA_Par_8/26/45`). The municipal delegation —
volikogu regulation → valitsus regulation — produced no node.

That intra-municipal chain is precisely what a KOV lawyer needs to trace
delegated authority, and it is the part no other dataset has.

### W18 — LOW — Dead parameters, four transliteration tables, and ~21 unreachable names in `estleg_common.py`

- `mint_act_iri(prefix, *, year=None)` at `estleg_common.py:662` discards the
  argument (`_ = year`, line 668) and no caller in the repo passes it.
- `strip_unused_jsonld_context_prefixes(text, *, drop_schema=False)` at
  `estleg_common.py:773` likewise ignores `drop_schema`.
- Four Estonian transliteration implementations, three of them inside this one
  module: `_ESTONIAN_TRANSLIT` (line 634, feeds `ascii_iri_key`),
  `_ESTONIAN_TRANSLITERATION` / `_TRANSLIT_TABLE` (line 1872, feeds
  `sanitize_id` and `slugify`), and an inline replacement loop in
  `normalize_issuer_name` (line 975). A fourth verbatim copy lives at
  `src/estleg/migrate_uris.py:42-47`, despite `AGENTS.md`'s explicit
  "reuse existing helpers rather than duplicating" rule. Seven modules in
  `src/estleg/` carry a copy of the table.
- `_walk_object_refs = walk_object_refs` (line 579) is a back-compat alias.
- `source_provenance` (line 1179) documents the canonical provenance contract
  and its own docstring concedes "most generators currently build these fields
  inline; this helper centralises and documents the shape rather than being
  wired through every generator" — 3 external users.
- A `git grep -w` reachability sweep over every module-level name found 21 with
  no user outside the module (`apply_inband_dataset_fields`,
  `merge_dataset_context`, `is_ontology_or_dataset_head`,
  `emit_assertion_confidence`, `node_type_list`, `MAP_IRI_SUFFIX`,
  `ONTOLOGY_VERSION_IRI`, the three `*_ASSERTION_CONFIDENCE` constants,
  `FETCH_HASH_FILENAME`, `SHAPE_REQUIRED_CLOSURE_PROPS`, …) and 16 more used
  only by a single test.

At 2,098 lines the module is a grab-bag: JSON-LD context, act-IRI algebra,
abbreviation tables, KOV name normalisation, HTTP fetch policy, XML security,
file enumeration, run counters, and Estonian month names.

### W19 — LOW — A spent one-shot and its now-unreachable verifier branch

I verified all 11,059 KOV roots are free of stray state-regulation types, so
`src/estleg/backfill_kov_regulation_typing.py` is a permanent no-op.
`AGENTS.md` says spent one-shots belong in `scripts/archive/`. Its counterpart,
the `contradictory` WARN branch in `verify_layer1.check_kov_acts`
(`src/estleg/verify_layer1.py:84-115`), is now unreachable, and its comment
still describes the corpus as "still dual-typed pre-regen".

### W20 — LOW — Stale invocation path in a docstring

`src/estleg/build_kov_registry.py:3` says `Run: python scripts/build_kov_registry.py`.
That path is a `runpy` shim now; the module is
`python3 -m estleg.build_kov_registry`.

---

## Improvement ideas

### I1. Derive a KOV enabling-provision staleness flag — the compliance feature is one script away

**What.** For each `estleg:MunicipalRegulation`, walk
`estleg:implementsCitation → estleg:citationTarget` to the state provision,
find the `estleg:ProvisionVersion` in force at the regulation's
`estleg:entryIntoForce`, and stamp `estleg:enablingProvisionOutdated`
(xsd:boolean) plus `estleg:earliestSupersedingDate` (xsd:date) on the
regulation.

**Why this is nearly free.** `src/estleg/derive_court_interpretation_staleness.py`
already implements exactly this shape for court decisions (#618): it walks
`estleg:interpretsLaw`, picks the redaction in force at the decision date, and
stamps `estleg:interpretationOutdated` + `estleg:earliestSupersedingDate`. The
inputs on the KOV side are all present: 9,823 regulations carry
`implementsCitation`; `estleg:KOKS_Par_22` alone has a 26-link version chain
with `versionValidFrom` / `versionValidTo` / `supersededByVersion`; 100% of
KOV roots carry `estleg:entryIntoForce` and `estleg:temporalStatus`.

**Why it matters for the public sector.** This is the one query
Rahandusministeerium (which supervises KOV legality) cannot run anywhere else:
*"which municipal regulations rest on a version of their enabling provision
that has since been amended or repealed?"* It converts the corpus from a
reference dataset into a supervision tool, and it is the strongest available
argument for the project's public-sector value.

- Effort: **M** (one new module, close in shape to an existing one).
- Impact: **H**.
- Files: new `src/estleg/derive_kov_enabling_staleness.py`;
  `src/estleg/run_all_integration.py` (add a step);
  `shacl/estonian_legal_shapes.ttl`; a new test.

### I2. Emit ELI and the Riigi Teataja id as first-class properties on every act

**What.** Add `estleg:globalId` / `estleg:terviktekstId` to law act roots (the
regulation generators already do this), and emit
`owl:sameAs → https://www.riigiteataja.ee/eli/<eli_id>` (plus
`eli:id_local`, `eli:date_publication`) from the mapping already sitting in
`data/riigiteataja/english_eli.json`. Backfill the 211 law roots that today
carry no RT link at all.

**Why it matters.** ELI is the identifier the EU Publications Office, national
gazettes, and the Estonian state itself already publish. Until an act node
carries it, no public body can join estleg to its own records without writing a
URL-parsing shim, and the project's claim to standards alignment rests on a
single `eli:is_about` alias.

- Effort: **M** (a backfill plus a generator change; 376 ELI ids are on disk,
  the remaining ~750 need one RT fetch pass through `allowed_get`).
- Impact: **H**.
- Files: `src/estleg/generate_all_laws.py`,
  `src/estleg/backfill_official_english.py` (reuse its resolver),
  a new backfill module, `data/riigiteataja/english_eli.json`,
  `docs/SCHEMA_REFERENCE.md`, `shacl/estonian_legal_shapes.ttl`.

### I3. Do not mint under ELI — keep `w3id.org/estleg/` and make it resolve

**What.** Reject a move to `https://www.riigiteataja.ee/eli/...` or a
retired government-owned namespace as the primary key. Instead: (a) finish the w3id PURL
registration; (b) replace the catch-all in `w3id/estleg/.htaccess:27` with
content negotiation that serves Turtle/JSON-LD to machine clients and an HTML
description page to browsers; (c) generalise the version rule from the
hardcoded `^1\.0\.0/?$` to `^([0-9]+\.[0-9]+\.[0-9]+)/?$`.

**Why.** `docs/NAMESPACE_MIGRATION.md` already establishes the correct
principle: minting under a namespace the project does not control is an
identifier-*authority* error, and that reasoning applies to
`riigiteataja.ee/eli/` exactly as it applied to the retired namespace. The project
cannot maintain redirects on either. The right architecture is the one
`docs/ARCHITECTURE.md` already states — minted `estleg:` primary keys, with
RT/ELI/CELEX/ECLI on `owl:sameAs` and properties — which is only half-built
(see W1/W2). Migration cost of switching to ELI would also be severe: 454 acts
have no reliable RT id today (W2, W8), so the migration would have to invent
identifiers for exactly the acts that are least well identified.

The honest caveat to record: as long as the PURL 404s and the interim rule
sends every term IRI to a GitHub landing page, "stable resolvable identifier"
is a claim the project cannot yet make. Fixing the resolver is what converts
the argument above from theory into fact.

- Effort: **S** for the `.htaccess` work, **M** for a real 303 resolver.
- Impact: **H**.
- Files: `w3id/estleg/.htaccess`, `w3id/estleg/README.md`,
  `docs/ARCHITECTURE.md`.

### I4. Merge the two abbreviation registries into one RT-sourced source of truth

**What.** Make `data/law_abbreviations.json` the single table, expand its
`rt_api` coverage from 216 to as close to 1,122 as the RT API allows, and
reduce `estleg_common.KNOWN_ABBREVIATIONS` to a generated view over it (or a
citation-form alias map keyed by the registry's abbrev). Add a test asserting
that every `KNOWN_ABBREVIATIONS` entry resolves to a registry row with a
matching abbrev.

**Why it matters.** Today the citation resolver believes the Penal Code is
`KarS` while the IRI says `KARIST_2`; 50 of 95 shared laws disagree, and 28
have no registry entry at all. For a public-sector user the abbreviation is the
handle — `estleg:KarS_Par_141` is self-documenting to any Estonian lawyer,
`estleg:KARIST_2_Par_141` is not. The 50 conflicts are also a live
citation-resolution risk: a genitive form that resolves through
`KNOWN_ABBREVIATIONS` to `SHS` cannot find `estleg:SOTSIA_Map`.

**Sequencing caveat.** Renaming act IRIs is a MAJOR-version change per
`docs/ARCHITECTURE.md`. Land the registry unification and the drift test now;
schedule the IRI rename for v2 with an `owl:sameAs` bridge written the way
`data/act_iri_v2_sameas.jsonld` already was — and publish that bridge this time
(I6).

- Effort: **M** for the registry + test; **L** for the corpus rename.
- Impact: **H**.
- Files: `src/estleg/estleg_common.py:28-243`, `data/law_abbreviations.json`,
  `src/estleg/migrate_uris.py:230 load_peep_prefixes`, `AGENTS.md`, new test.

### I5. Give the KOV layer a historical-issuer dimension

**What.** Three linked changes:
1. Emit `estleg:historicalMunicipality → estleg:HistoricalMunicipality_<code>`
   on issuer nodes. `extract_historical_municipalities` already computes
   `issuerSlugs` per historical unit and throws them away
   (`enrich_kov_layer1.py:236`) — invert that map instead.
2. Stamp `estleg:enactedByHistoricalMunicipality` on acts whose issuer has one,
   alongside the existing successor-valued `estleg:enactedByMunicipality`.
3. Source `estleg:historicalMunicipalityName` from the historical node's
   diacritic-correct `formerName` rather than from title-casing the slug, so
   "Kohtla Jarve" becomes "Kohtla-Järve".

**Why it matters.** Right now 151 curated historical-municipality nodes with
EHAK codes, merger dates, and RT citations are unreachable by any edge, and a
municipality asking "which of these 345 Tartu linn regulations did *we*
actually enact" gets no answer. Both readings — territorial successor and
historical issuer — are legitimate and a supervising ministry needs to tell
them apart.

- Effort: **S** (the data is computed; the edges are not written).
- Impact: **H**.
- Files: `src/estleg/kov_registry.py:582-668`,
  `src/estleg/enrich_kov_layer1.py:170-275, 400-430`,
  `shacl/estonian_legal_shapes.ttl`, `tests/test_enrich_kov_layer1.py`.

### I6. Publish the act-IRI `owl:sameAs` bridge on the public load surface

**What.** Add `data/act_iri_v2_sameas.jsonld` to the seadusloome load surface
the same way `historical_municipalities.jsonld` was added at
`validate_seadusloome_sync.py:101` and `shacl_validate_all.py:49`, list it in
`docs/ARCHITECTURE.md`'s load-surface table, and mention it in the release
notes.

**Why it matters.** 14,440 act IRIs changed shape in #445. Any public body that
bookmarked, cited, or stored a pre-#445 `_Map_2026` IRI has no published way to
follow it forward. Identifier stability is the promise the whole `w3id`
migration was made to keep; an unpublished redirect table does not keep it.

- Effort: **S**.
- Impact: **M**.
- Files: `src/estleg/validate_seadusloome_sync.py`,
  `src/estleg/shacl_validate_all.py`, `docs/ARCHITECTURE.md`, `README.md`.

### I7. Fix the legacy-namespace guard's exclusion list (three places, one edit each)

**What.** Replace `scripts/migrate_namespace.py` with
`src/estleg/migrate_namespace.py` in all three exclusion lists:
`.github/workflows/validate.yml:258`, `tests/test_no_legacy_namespace.py:26`,
and `src/estleg/migrate_namespace.py:58`. Add a test asserting the three lists
are identical, so the next module move cannot silently disarm the guard again.

**Why it matters.** The CI step is red today (verified by running its exact
grep), and the third instance is worse than a false alarm: re-running
`migrate_namespace --apply` would rewrite the module's own replacement table
and turn the migration into a no-op.

- Effort: **S**.
- Impact: **M**.
- Files: `.github/workflows/validate.yml`,
  `tests/test_no_legacy_namespace.py`, `src/estleg/migrate_namespace.py`.

### I8. Carry EHAK county codes and widen the identity links

**What.** Add `estleg:countyEhakCode` beside the existing county label
(`enrich_kov_layer1.py:155`), and extend
`municipality_identity_links` with the county classifier IRI. Separately, add a
`registrikood` / X-tee member code column to `data/institution_aliases.json`
and emit it as `estleg:registryCode` on institution nodes.

**Why it matters.** A county-level or ministry user aggregating across
municipalities joins on codes, not on the string "Lääne-Viru maakond". And for
institutions, the registrikood is the identifier every Estonian public register
uses; without it the institution layer cannot be joined to anything the state
already runs.

- Effort: **S** for county codes, **M** for institution registry codes
  (needs curation).
- Impact: **M**.
- Files: `data/ehak/municipalities.json`, `src/estleg/enrich_kov_layer1.py`,
  `data/institution_aliases.json`, `src/estleg/extract_institutional_competence.py`.

### I9. Point `dcterms:source` at the human RT page and keep the XML as a manifestation link

**What.** Emit `dcterms:source → https://www.riigiteataja.ee/akt/<id>` and move
the `.xml` URL to a distinct property (or `dcat:downloadURL`), consistent with
the treatment `strip_xml_sameas` already gives `owl:sameAs`.

**Why it matters.** The citable link is what an official pastes into a memo. A
raw XML document is the wrong artefact for that audience, and the project
already decided XML manifestations do not belong on `owl:sameAs` — the same
reasoning applies here.

- Effort: **S**.
- Impact: **M**.
- Files: `src/estleg/estleg_common.py:1179 source_provenance`,
  the act generators, a backfill, `docs/SCHEMA_REFERENCE.md`.

### I10. Capture KOV → KOV preamble citations

**What.** Extend the preamble citation parser to recognise the
`<Issuer> <date> määruse nr <n> "<title>" § <n> lõike <n> alusel` shape and
emit a `estleg:Citation` whose target is the municipal regulation, resolved
through `estleg:enactedByMunicipality` + `estleg:titleNormalized` + the actNumber.

**Why it matters.** Delegation inside a municipality (volikogu authorising
valitsus) is the chain a KOV lawyer traces daily and the one nobody else has
modelled. The parser already scopes body-text act references by
`enactedByMunicipality` (README, Layer 2b), so the scoping machinery exists.

- Effort: **M**.
- Impact: **M**.
- Files: `src/estleg/extract_cross_references.py`,
  `src/estleg/estleg_common.py` (the `ESTONIAN_MONTHS_GENITIVE` table is
  already hoisted for this), tests.

### I11. Split `estleg_common.py` and delete the dead surface

**What.** Extract four modules with clear owners — `estleg_iri.py` (NS,
CONTEXT, mint/parse, `sanitize_id`, `slugify`, one transliteration table),
`estleg_loadsurface.py` (the `PUBLIC_LOAD_*` / `COMBINED_*` / closure
constants), `estleg_http.py` (allow-list, `allowed_get`, `parse_xml`,
`sha256_hex`), and `estleg_abbrev.py` (the two abbreviation tables from I4).
Delete the ignored `year` and `drop_schema` parameters, the
`_walk_object_refs` alias, and the ~21 names with no external user; import the
shared transliteration table in the seven modules that copy it, including
`migrate_uris.py:42`.

**Why it matters.** Ranked below the identity work deliberately — it is
maintainability, not public-sector capability. But at 2,098 lines with four
transliteration tables and two conflicting abbreviation registries in one file,
this module is where the identity defects in W3 and W18 were able to hide.

- Effort: **M**.
- Impact: **M** (maintainability).
- Files: `src/estleg/estleg_common.py`, ~50 importers, `AGENTS.md`.

### I12. Retire the spent one-shot and the unreachable verifier branch

**What.** Move `src/estleg/backfill_kov_regulation_typing.py` to
`scripts/archive/` per `AGENTS.md`, convert the `contradictory` WARN branch in
`verify_layer1.check_kov_acts:84-115` into a hard failure (the corpus is now
clean, so it can be a gate), and fix the stale invocation path at
`build_kov_registry.py:3`.

- Effort: **S**.
- Impact: **L**.
- Files: `src/estleg/backfill_kov_regulation_typing.py`,
  `src/estleg/verify_layer1.py`, `src/estleg/build_kov_registry.py`.

---

## Open questions

1. **Was the ELI omission deliberate?** `data/riigiteataja/english_eli.json`
   proves the ELI ids were fetched and cached, yet they surface only as an
   English-translation link. If there is a recorded decision against publishing
   ELI, it is not in `AGENTS.md`, `ARCHITECTURE.md`, or
   `NAMESPACE_MIGRATION.md`. If there is not, I2 is the highest-value item in
   this review.

2. **Why do law acts lack `globalId` while regulations have it at 100%?**
   `pair_peep_with_xml` attributes this to the ~615 pre-globalId legacy laws,
   but the corpus now has 1,195 law peeps and 0 with the field. Was the
   backfill ever attempted, or did the slug-fallback path make it feel
   unnecessary?

3. **Is `enactedByMunicipality`'s successor semantics a recorded decision?**
   `ARCHITECTURE.md`'s "what not to change without a MAJOR version" list does
   not mention it. Adding `enactedByHistoricalMunicipality` (I5) is additive
   and safe either way, but I want to know whether the successor reading was
   chosen or defaulted.

4. **Is the CI legacy-namespace step currently green?** I verified the grep
   returns a hit locally. If CI is passing, the step is either not running or
   is being skipped for a reason I could not see from the workflow file.

5. **What is the plan for the 542 unregistered law slugs?** `AGENTS.md`
   proposes broadening `load_peep_prefixes` and regenerating the registry.
   Is that scheduled, and is it gated behind the v2 IRI rename (I4) or
   independent of it?

6. **Does anyone outside the project hold pre-#445 `_Map_2026` IRIs?** If yes,
   I6 is urgent. If the corpus was never distributed before v1.0.0, the bridge
   file could be archived instead of published — but that should be an explicit
   decision, not the current silence.

7. **Is `estleg:regulationTypeBucket` intended to become a controlled
   vocabulary?** It is 100% populated with free text derived from titles. As a
   cross-municipality facet it is the natural companion to
   `estleg:titleNormalized`, but only if the values are curated.
