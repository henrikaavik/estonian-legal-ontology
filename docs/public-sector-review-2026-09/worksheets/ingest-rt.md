<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Riigi Teataja ingest — public-sector review

## Scope & method

Files read in full: `src/estleg/generate_all_laws.py` (2207 l), `src/estleg/generate_regulations.py`
(relevant halves), `src/estleg/riigiteataja_common.py` (530 l), `src/estleg/law_structure.py` (1075 l),
`src/estleg/check_rt_staleness.py`, `src/estleg/generate_rt_act_kinds.py`,
`src/estleg/generate_missing_parts.py`, `tests/test_rt_schema_canary.py`, test-name inventory of
`tests/test_generate_all_laws.py` (185 tests), README "Refresh SLA" / "Data Sources" / "API Details" /
"Refreshing Data", `AGENTS.md`, `docs/ARCHITECTURE.md`.

Beyond reading, I validated claims against the **committed corpus and the one committed RT XML**:

* structural census of `data/riigiteataja/karistusseadustik.xml` (the only cached source file in the repo);
* provenance-field census over all 1,195 root `krr_outputs/*_peep.json`;
* provenance census over 3,812 `krr_outputs/regulations/riik/*_peep.json`;
* a corruption scan over `estleg:legalText` in every law peep;
* subsection / `estleg:itemNumber` census over 111,973 `estleg:Subsection` nodes.

No repository file was modified.

---

## Strengths

1. **Source-list completeness is accounted for, not assumed.** `fetch_acts` maintains per-page
   `pagesFetchedOk` / `pagesFailed` / `pagesRetried` counters and a `pageLimitHit` flag
   (`riigiteataja_common.py:211-272`), and `get_all_laws` turns a pagination-cap hit into a fatal
   `SourceListFetchError` unless `--allow-partial` is explicit (`generate_all_laws.py:238-247`).
   Truncation cannot silently be reported as a complete corpus. This is exactly the discipline a
   public publisher needs and it is rare in scraper code.

2. **Network egress is allow-listed and redirect-pinned.** `allowed_get` rejects any host outside
   `ALLOWED_HTTP_HOSTS`, defaults `allow_redirects=False`, and re-checks every hop's `Location`
   when redirects are opted into (`estleg_common.py:2035-2057`). An RT redirect cannot bounce the
   fetch to a third-party host. Good answer to "is it legally safe".

3. **Amendment/publication marker subtrees are pruned out of citable text.** `_MARKER_TAGS` covers
   `muutmismarge` / `avaldamismarge` / `joustumismarge`, and `_iter_text_nodes` /
   `_marker_pruned_text` (`law_structure.py:26-66`) prune whole subtrees rather than filtering
   afterwards. The committed KarS XML has 530 `avaldamismarge` and 528 `muutmismarge` elements
   nested inside the body, so without this every provision would carry "RT I, 2014, …" noise. Six
   dedicated tests cover it.

4. **Identifiers are designed to survive a redaction.** Paragraph, chapter and division IRI suffixes
   are superscript-aware and order-independent (`law_structure.py:228-287`), the per-osa act IRI
   dropped the volatile §-range (`generate_all_laws.py:812-817`), and `build_law_slug_map` freezes
   already-committed collision suffixes and orders new colliders by `(globaalId, title)`
   (`generate_all_laws.py:106-190`). This directly protects downstream enrichment keyed on old IRIs.

5. **A real anti-laundering guard on degraded parses.** When a fetch yields zero paragraphs but the
   root still carries `loige`/`peatykk`/`jagu`/`jaotis`, or a structured peep already exists on disk,
   the run records `failed` instead of writing a sticky body-less stub
   (`generate_all_laws.py:1841-1878`, `xml_root_has_structure` at `:950-959`,
   `existing_peep_is_structured` at `:962-991`). A future RT tag rename degrades loudly.

6. **Writes are atomic and resumable.** `save_regen_state` writes to `.tmp` then `os.replace`
   (`generate_all_laws.py:1607-1621`); `--regen-state` records per-act completion keyed on
   `(slug, title, kehtiv, terviktekstId)` and `prune_completed_regen_state` records *why* each stale
   entry was dropped (`:1531-1588`). A 3-hour run survives a Ctrl-C.

7. **Regeneration is non-destructive to enrichment.** `merge_existing_enrichments`
   (`generate_all_laws.py:1021-1067`) pulls back only keys the generator did not emit, with an
   explicit `_MERGE_BLOCKED_FIELDS` for the one structural field that must not survive
   (`estleg:requestedCluster`). Acceptance test: two consecutive runs over an enriched corpus are
   a no-op.

8. **The regulation act node is a genuinely good provenance record.** `build_regulation_jsonld`
   emits `estleg:globalId`, `estleg:terviktekstId`, `estleg:issuer`, `estleg:actNumber`,
   `estleg:entryIntoForce`, `estleg:repealDate`, `estleg:lastAmendmentDate`, `estleg:preambleText`
   and `estleg:parseMode` (`generate_regulations.py:545-592`). Verified on disk.

9. **Repealed acts are tombstoned rather than published.** `_should_tombstone_regulation` /
   `_repealed_before_snapshot` (`generate_regulations.py:380-441`, `:506-529`) suppress the body of
   an act that was already void at the snapshot date, keeping the act node so the index can exclude
   it. Publishing the text of a repealed act as current law is a real liability; this avoids it.

10. **Date sanity guard.** `_strip_offset` in `parse_act_metadata` rejects years outside 1900–2100
    (`riigiteataja_common.py:477-490`), so a typo'd `2918-10-17` cannot win the
    `lastAmendmentDate` string comparison.

---

## Weaknesses / risks

### CRITICAL

**W1. Inline `<sup>` elements are flattened, producing factually wrong statutory cross-references.**
`law_structure.py:47-66` (`_marker_pruned_text`) concatenates an element's text with each child's
text. RT stores superscripts in body text as a *real XML element*:

```xml
<tavatekst>… käesoleva seadustiku §-s 209, 210, 211, 212, 213, 217<sup>2</sup>,
 280, 281, 295, 296, 297, 298, 298<sup>1</sup> või 400 …</tavatekst>
```

`_sup_to_unicode` (`law_structure.py:162-173`) only rewrites the *escaped literal* form
`&lt;sup&gt;N&lt;/sup&gt;`. The committed `data/riigiteataja/karistusseadustik.xml` contains
**167 real `<sup>` elements and 0 escaped ones**, so that function is dead code against real RT XML.

Verified in the published corpus — `krr_outputs/karistusseadustik_osa1_peep.json`,
`estleg:KARIST_2_Osa1_Par_49_1` (§ 49¹ Ettevõtluskeeld):

```
… käesoleva seadustiku §-s 209, 210, 211, 212, 213, 2172, 280, 281, 295, 296, 297, 298, 2981 …
```

`§ 217²` became `§ 2172`; `§ 298¹` became `§ 2981`. Corpus-wide scan (excluding VÕS and TsMS, which
legitimately have four-digit sections):

| Measure | Count |
|---|---|
| Law peep files with corrupted §-references | 146 |
| Provision / subsection nodes affected | 2,342 |

Other confirmed instances: `autoveoseadus_peep.json` renders MKS `§ 153¹` as `1531`, LS `§ 210²` as
`2102`, and `§ 261⁶–261⁹` as `2616–2619`. **Severity: critical.** A court, ministry or Bürokratt
reading this graph is handed a statutory reference to a section that does not exist. Structural
numbering is unaffected (`§ 49¹.` is correct) because RT uses `ylaIndeks` plus a Unicode glyph in
`kuvatavNr`, which the code does handle — so the defect is invisible in labels and only appears
inside `estleg:legalText`.

**W2. `alampunkt` enumeration markers are destroyed.**
`_loige_body_text` (`law_structure.py:429-448`) collects only `lauseOsa` / `lause` / `tavatekst`.
RT nests each sub-point as `<alampunkt><alampunktNr>1</alampunktNr><kuvatavNr>1) </kuvatavNr>
<sisuTekst><tavatekst>…`. The `kuvatavNr` markers are dropped and the sub-point bodies are joined
into one run-on string. KarS § 7 lg 1 (three alternative jurisdiction grounds) is published as:

```
(1) Eesti karistusseadus kehtib … ning: tegu on toime pandud Eesti kodaniku … vastu või
teo toimepanija oli … Eesti kodanik … või välismaalane, kes on Eestis kinni peetud …
```

The `1)` `2)` `3)` markers are gone. `_loige_item_numbers` (`law_structure.py:654-662`) searches for
`punktNr`, a tag the KarS XML never emits (358 `alampunktNr`, 0 `punktNr`), then falls back to a
regex over the already-marker-less text.

| Measure | Count |
|---|---|
| `estleg:Subsection` nodes in corpus | 111,973 |
| …carrying `estleg:itemNumber` | 4,629 (4.1%) |
| …enumerated-looking but with no `itemNumber` | 11,222 |

**Severity: critical.** `KarS § 7 lg 1 p 2` is the normal Estonian citation unit and it is not
addressable in this graph. Worse, the run-on rendering changes the legal reading: three alternative
conditions read as one continuous sentence.

**W3. There is no audit trail from a published node back to official source bytes.**
`estleg:contentHash` (#558) is stamped from an in-process dict (`_CONTENT_HASHES`,
`generate_all_laws.py:518-522`) that is only populated for acts fetched or cache-read in the current
run — which in `--missing-only` is almost nothing.

| Measure | Count |
|---|---|
| Root law peeps in corpus | 1,195 |
| …with `estleg:contentHash` | **1** |
| Entries in `krr_outputs/fetch_content_hashes.json` | **1** |
| RT XML files retained in `data/riigiteataja/` | **1** (`karistusseadustik.xml`) |

The single hash entry records `"source": "data/riigiteataja/karistusseadustik.xml"` — a local path,
not a riigiteataja.ee URL, so it attests to a local file rather than to a fetch. **Severity:
critical for adoption.** A ministry cannot verify that any node matches the official consolidated
text of 2026-05-24, and the corpus cannot be rebuilt — a rerun would fetch whatever RT serves today.

### HIGH

**W4. `estleg:terviktekstId` is stamped nowhere, so edition-based staleness is dead.**
`_stamp_terviktekst_id` (`generate_all_laws.py:507-515`) is called under `if kehtiv_value is not
None` at `:678-680`, `:754-757`, `:834-836`, and `existing_law_is_stale` (`:917-920`) compares it.
`grep -l terviktekstId krr_outputs/*_peep.json` returns **0 files**. Consequence: if RT publishes a
new consolidated edition and the operator reruns with the same `--kehtiv`, `--missing-only` skips
every act. The freshness model then rests entirely on the operator remembering to bump `--kehtiv`.

**W5. State regulations carry no snapshot date at all.** `estleg:kehtiv` is absent from **0/3,812**
`regulations/riik/*_peep.json` root nodes, and `estleg:parseMode` is absent from all 3,812 too even
though `build_regulation_jsonld` always emits it (`generate_regulations.py:553`). The committed
regulation corpus therefore did not come from the current code path.
`REGULATIONS_RIIK_INDEX.json` records `run: {"rebuiltFromExistingFiles": true}` — the index is a
post-hoc reconstruction, not a fetch record, and reports `htmlFallbackCount: 0` and
`noStructuredBodyCount: 0` across 14,871 regulations, which is implausible given the documented
pre-2010 `HTMLKonteiner` path. `--missing-only` can never refresh a regulation, because the
staleness comparison has nothing to compare.

**W6. "Newest redaction wins" is false; `globaalID` is not chronological.**
`get_all_laws` keeps the row with the lexicographically largest `globaalID`
(`generate_all_laws.py:229`), as does `generate_rt_act_kinds.list_kind:88`.
`generate_regulations._gid_rank` (`:644-664`) upgrades string to integer compare and documents this
as the fix — but the underlying premise is wrong for both. RT 12-digit ids are `DDMMYYYYNNN`:
observed in the corpus, `107052025017` = 07.05.2025, `122122025002` = 22.12.2025,
`131122024048` = 31.12.2024, `231052021002` = 23.05.2021. Day-of-month dominates the ordering, so a
2021 redaction outranks a 2025 one. The corpus mixes 226 five-digit, 108 six-digit, 85 eight-digit
and 565 twelve-digit ids, so ordering is not even within one family. The correct newest signal is
the `kehtivus` block or `kehtivuseAlgus`, not the id.

**W7. The freshness SLA gate is pinned and cannot fail.** `check_rt_staleness` compares committed
`estleg:kehtiv` against `BUILD_EVALUATION_DATE` (`check_rt_staleness.py:79-104`), which defaults to
the hard-coded string `"2026-06-01"` (`estleg_common.py:1549-1550`).

| | |
|---|---|
| Corpus `estleg:kehtiv` (all 1,018 dated peeps) | 2026-05-24 |
| SLA evaluation date used by the gate | 2026-06-01 |
| Lag the gate measures | 7 days |
| `SLA_MAX_LAG_DAYS` | 45 |
| Actual lag today (2026-09-03) | 102 days |

CI reports SLA PASS on a corpus that is 57 days past its own published SLA. The docstring
("*fail if any snapshot is more than SLA_MAX_LAG_DAYS behind*") describes a check that time cannot
trip. `GENERATOR_KEHTIV_PIN` (`:44`) and `DEFAULT_KEHTIV` (`generate_all_laws.py:76`) both say
`2026-05-01`, which does not match the corpus's `2026-05-24` either.

**W8. The "RT schema canary" cannot observe RT.** `tests/test_rt_schema_canary.py` asserts that a
*committed local fixture* still contains `oigusakt` / `paragrahv` / `paragrahvNr` / `peatykk`. RT
schema drift happens on RT's servers; this test only fails if someone edits the repo. It also does
not assert the schema identity RT itself publishes — the fixture's `metaandmed` carries
`skeemiNimi = tyviseadus_1_10.02.2010.xsd`, which `parse_act_metadata` does not extract at all.

**W9. The law manifest the README relies on is not in the repository.**
`krr_outputs/generation_manifest_laws.json` does not exist. Three documented capabilities are
therefore inert: `load_committed_slug_map` returns `{}` so the #238 slug freeze
(`generate_all_laws.py:1731-1734`) is inactive and filenames/IRIs can drift on the next run;
`--from-manifest` replay (README "Refreshing Data") cannot run; and the per-act `status` ledger the
README tells consumers to "consult" for stub-vs-failure discrimination is unavailable.

**W10. Law act nodes carry almost no official metadata.** `parse_act_metadata` extracts `globalId`,
`terviktekstId`, `documentType`, `issuer`, `actNumber`, `entryIntoForce`, `repealDate` and
`lastAmendmentDate` (`riigiteataja_common.py:453-530`) — and `generate_all_laws.py` never calls it
(only `generate_regulations.py:488` and `check_rt_staleness.py:180` do). A law act node holds
`@id`, two labels, `dc:source`, `dcterms:title`, `estleg:contentStatus`, `dcterms:source` and
`estleg:kehtiv`, and nothing else. **The flagship product has thinner provenance than the
secondary one**, using data already parsed from the same XML tree.

**W11. No ELI, and no official identifier as a property.** `eli:` appears in 1,121 contexts but is
used only as `eli:is_about` for EuroVoc subjects. There is no `eli:id_local`, no
`eli:LegalResource`/`eli:LegalExpression` work-expression split, no `eli:version_date`, no
`eli:date_publication`, and no `owl:sameAs` to an RT identifier. `docs/ARCHITECTURE.md` states
"Official RT / CELEX / ECLI identifiers live on properties and `owl:sameAs`" — for laws this is
aspirational, not implemented. This is the single biggest barrier to the Publications Office,
EUR-Lex, avaandmed.eesti.ee (DCAT-AP) and RT/RIK itself.

### MEDIUM

**W12. `dcterms:source` points at the XML, not the citable page.** Stored values are
`https://www.riigiteataja.ee/akt/107052025017.xml` (`generate_all_laws.py:664-665`). A civil servant
following the provenance link downloads XML rather than landing on the official RT reading view.
Both should be present.

**W13. Regulation provision IRIs are order-dependent and superscript-blind.**
`collect_structured_paragraphs` builds `estleg:{prefix}_Par_{sanitize_id(nr)}` and disambiguates a
collision with `f"{p_id}_{len(seen_ids)}"` (`generate_regulations.py:255-258`) — the insertion-order
scheme that #156/#165 removed from laws. `§ 5` and `§ 5¹` collide and their IRIs swap on
re-ingest. Same in `collect_html_paragraphs:318-321`. This affects 14,871 acts and ~168k provisions.

**W14. Regulations have no subsection granularity.** `generate_regulations.py` never calls
`build_subsections`; zero `estleg:Subsection` nodes exist under `krr_outputs/regulations/`. A KOV or
ministerial regulation cannot be cited at `lg` level, and its `estleg:legalText` is a flat blob.

**W15. The README materially understates stub coverage.** README (~line 386) states "At present the
committed corpus contains no `noStructuredBody` stubs — every act that survived the source `kehtiv`
filter parsed into provisions." The corpus contains **365** such stubs out of 1,023 acts with a
`contentStatus` — 36% of dated acts are act-level only, mostly treaties and ratification acts. The
mechanism (contentStatus + contentStatusReason) is sound and honest; the prose is not.

**W16. `generate_missing_parts.py` produces provenance-free and orphaned artifacts.** The VÕS/TsÜS
part builders (`:221-529`) emit no `estleg:kehtiv`, no `dcterms:source`, no `terviktekstId` and no
`contentHash`. `DEFAULT_KEHTIV = _date_cls.today().isoformat()` is evaluated at import
(`:53`), making the default non-deterministic. On disk, `tsiviilseadustik_osa1_peep.json` is a
2-node shell with an act root and one cluster and no provisions; `tsiviilseadustik_osa6_peep.json`
and `_osa7_` have no act root at all. The slug `tsiviilseadustik` matches no law title, so
`source_removed_law_files` (`generate_all_laws.py:1182-1209`) will flag all eight files as
source-removed on every run. The module also writes `volaoigusseadus_osa{2,6,10}_peep.json`, the
same filenames `generate_multipart_law` writes — last writer wins, with different node shapes.

**W17. `generate_rt_act_kinds.py` publishes fetch failures as content.** `generate_kind_peep:104-106`
does `if root is None: root = ET.Element("akt")`, so a network failure yields a stub peep
indistinguishable from a genuinely body-less resolution, with no `failed` record anywhere. The index
reports `rt_total` (`:139`) from a `max_pages=5`, `allow_partial=True` listing with no `stats` dict,
so a truncated or partially-failed listing is published as the total (currently `otsus: 253`,
`seadlus: 123`). `generated` is set to `BUILD_EVALUATION_DATE` (`:123`), a pinned constant, not the
run date — `RESOLUTIONS_INDEX.json` claims `2026-06-01`. Slugs are `slugify(title)[:80]` with no
collision handling (`:103`, `:189`), and Riigikogu otsus titles routinely share long prefixes.

**W18. The abbreviation registry misses the flagship codes.** `data/law_abbreviations.json` has 601
entries against 1,122 laws. `Võlaõigusseadus` is **absent**, so `PrefixAllocator.allocate` falls
through to `sanitize_id(slug[:40])` and the act IRI is `estleg:volaoigusseadus_Osa2`.
`Karistusseadustik` maps to `{"abbrev": "KARIST_2", "source": "auto"}`, giving
`estleg:KARIST_2_Osa1_Par_49_1`. AGENTS.md states the preference order is "the official Riigi Teataja
`lyhend` (e.g. `PKS`, `KarS`, `TsÜS`)" — the two most-cited Estonian codes violate it. A ministry
lawyer will not recognise `KARIST_2` as KarS, and these IRIs are the graph's primary keys.

**W19. Refresh cost and bus factor.** Both generators are serial with a 0.3 s inter-act sleep
(`generate_all_laws.py:2049`, `generate_regulations.py:1349`). At ~1,120 laws + 14,871 regulations
and realistic RT latency, a full refresh is several hours of wall time. Laws have `--regen-state`
resume; **regulations do not**, so a failure at hour three restarts from zero. There is no
containerised or scheduled runner for ingest, and `run_all_integration.py` explicitly excludes
ingest (`docs/ARCHITECTURE.md`, "Pipeline").

**W20. No Estonian-language operator documentation.** Every docstring, README section, CLI `--help`
string and manifest key is English. `estleg:contentStatusReason` values are English sentences
embedded in the published Estonian-law graph. Riigi Teataja, RIK and KOV operators work in Estonian.

### LOW

**W21. Non-text structure is silently dropped from laws.** The KarS XML has 712 `reavahetus` line
breaks, collapsed by `re.sub(r"\s+", " ", …)`. Laws have **no annex extraction at all** —
`extract_annexes` exists only in `generate_regulations.py:136-197` — so `lisa` schedules attached to
laws (tax rates, fee tables, forms) are absent. No `tabel` handling anywhere. `normtehnmarkus`
(the normitehniline märkus recording which EU directives an act transposes; 2 in KarS) is not
extracted for laws, even though EU-transposition linkage is a headline use case.

**W22. `ct()` reads only `child.text`.** `riigiteataja_common.py:75-80` returns the first direct
child's `.text`, ignoring nested markup. Any `paragrahvPealkiri` or `osaPealkiri` containing an
inline element is silently truncated at the first child boundary. Same root cause as W1.

**W23. `PAGE_SCAN_LIMIT = 250` with `limiit=None`.** `get_all_laws` passes `limiit=None` and
`stop_on_short_page=False` (`generate_all_laws.py:214-223`), so the RT server default page size
governs and the cap is 250 pages. The cap raises rather than truncates (good), but the effective
row ceiling is undocumented and untestable from the manifest.

---

## Improvement ideas

1. **Handle the `<sup>` element in text extraction.** *What:* teach `_marker_pruned_text` and
   `_loige_body_text` to map a `<sup>` child's digit content to the Unicode superscript before
   concatenation, mirroring what `_sup_to_unicode` already does for the escaped form; add a fixture
   with a real `<sup>` child inside `tavatekst`; then re-emit the 146 affected files or add an
   offline repair pass beside `scripts/normalize_sup_markup_subcorpus`. *Why:* the corpus currently
   publishes statutory cross-references to sections that do not exist. No public body can adopt a
   legal graph that misquotes section numbers, and this defect is invisible in labels so it will not
   be caught by eyeballing. *Effort:* S for the fix, M with the corpus repair. *Impact:* H.
   *Files:* `src/estleg/law_structure.py`, `src/estleg/riigiteataja_common.py`,
   `tests/test_generate_all_laws.py`.

2. **Model `alampunkt` as a citable unit.** *What:* emit an `estleg:Item` (or `estleg:Point`) node per
   `alampunkt` with IRI `…_Par_<n>_Lg_<m>_P_<k>`, its own `estleg:legalText`, and
   `estleg:parentSubsection`; at minimum prefix each sub-point with its `kuvatavNr` marker inside the
   lõige's `legalText` so `1)` `2)` survive. Point `_loige_item_numbers` at `alampunktNr`, not the
   absent `punktNr`. *Why:* `§ N lg M p K` is the standard Estonian citation unit — it is how courts,
   Riigikohus decisions and ministry drafting instructions refer to law. Today 96% of subsections
   have no item numbers and the run-on rendering changes the legal reading of alternative
   conditions. *Effort:* M. *Impact:* H. *Files:* `src/estleg/law_structure.py`, `shacl/`,
   `tests/test_generate_all_laws.py`.

3. **Make the audit trail real: retain sources and stamp every act.** *What:* (a) always compute
   `estleg:contentHash` on the act node even when the peep is not rewritten, by hashing the accepted
   cache file during the skip path; (b) commit or publish the fetched RT XML as a dated release
   asset (gzipped, ~1.3 MB/act × 1.1k acts is tractable) or at minimum ship
   `fetch_content_hashes.json` covering all acts with the real riigiteataja.ee URL as `source`;
   (c) record `skeemiNimi` per act. *Why:* this is the first question a Justiitsministeerium or
   Riigikohus reviewer asks — "prove this node matches the official text on that date". Right now
   the answer exists for 1 of 1,195 acts. It is also the precondition for reproducible rebuilds.
   *Effort:* M. *Impact:* H. *Files:* `src/estleg/generate_all_laws.py`,
   `src/estleg/estleg_common.py`, `data/riigiteataja/`, release tooling.

4. **Stamp `terviktekstId` and fix edition-based staleness.** *What:* diagnose why
   `_stamp_terviktekst_id` produced nothing across the corpus (likely the corpus predates it) and
   add a corpus invariant test asserting every dated law peep carries `estleg:terviktekstId`; make
   `existing_law_is_stale` treat a *missing* stored tid as stale when a current tid is known.
   *Why:* today an RT re-consolidation under an unchanged `--kehtiv` is invisible to
   `--missing-only`, so freshness depends on operator memory. *Effort:* S. *Impact:* H.
   *Files:* `src/estleg/generate_all_laws.py`, `tests/test_generate_all_laws.py`,
   `scripts/validate_all.py`.

5. **Un-pin the freshness SLA.** *What:* have `check_rt_staleness` default its evaluation date to
   `date.today()` (keeping `--evaluation-date` and the env var for deterministic tests), broaden the
   3-act sample to a percentile over the whole corpus, and include regulations. *Why:* the gate
   currently reports PASS on a corpus 102 days old against a published 45-day SLA. A freshness
   guarantee that cannot fail is worse than none, because it is cited in the README as an assurance.
   *Effort:* S. *Impact:* H. *Files:* `src/estleg/check_rt_staleness.py`, `README.md`.

6. **Give laws the metadata block regulations already have.** *What:* call `parse_act_metadata` in
   `generate_law_jsonld` / `generate_law_stub_jsonld` / `generate_multipart_law` and stamp
   `estleg:globalId`, `estleg:issuer`, `estleg:actNumber`, `estleg:entryIntoForce`,
   `estleg:repealDate`, `estleg:lastAmendmentDate` exactly as `build_regulation_jsonld:559-582`
   does. *Why:* "which minister issued this, when did it enter into force, when was it last
   amended" are the questions a legal department actually asks, the data is already parsed, and the
   asymmetry between laws and regulations is arbitrary. *Effort:* S. *Impact:* H.
   *Files:* `src/estleg/generate_all_laws.py`, `shacl/`, `tests/test_generate_all_laws.py`.

7. **Add ELI identity to act and provision nodes.** *What:* emit `eli:id_local` (the RT
   terviktekstiGrupiID), `eli:date_document`, `eli:version_date`, `eli:is_realized_by` between the
   act work and the dated consolidated expression, and an `owl:sameAs` to the RT act page. Model
   `estleg:kehtiv` as the expression's `eli:version_date`. *Why:* ELI is the interchange contract for
   the Publications Office, EUR-Lex, DCAT-AP on avaandmed.eesti.ee, and for RT/RIK's own direction of
   travel. Without it the graph is a private vocabulary that EU-level consumers must map by hand.
   *Effort:* M. *Impact:* H. *Files:* `src/estleg/generate_all_laws.py`,
   `src/estleg/generate_regulations.py`, `src/estleg/estleg_common.py` (CONTEXT), `shacl/`,
   `docs/SCHEMA_REFERENCE.md`.

8. **Fix redaction selection.** *What:* stop ranking by `globaalID`. Rank by the search row's
   `kehtivus.kehtivuseAlgus` (or the XML's `kehtivuseAlgus`), with `terviktekstID` as the identity
   key and `globaalID` only as a final tiebreak. Apply in `get_all_laws:229`,
   `generate_rt_act_kinds.list_kind:88` and `generate_regulations._gid_rank`. Add a regression test
   using the real observed id shapes (`131122024048` vs `107052025017`). *Why:* "newest redaction
   wins" is currently false; day-of-month dominates the ordering, so the pipeline can silently pin an
   older consolidated text. *Effort:* S. *Impact:* H. *Files:* `src/estleg/generate_all_laws.py`,
   `src/estleg/generate_regulations.py`, `src/estleg/generate_rt_act_kinds.py`, `tests/`.

9. **Make the schema canary observe RT.** *What:* extract `skeemiNimi` in `parse_act_metadata`,
   stamp it on the act node, and add an operator-run `--fetch` mode to the canary that fetches one
   live act and compares `skeemiNimi` plus the required local-names against the committed fixture.
   Rename the current fixture test to what it is (a fixture-integrity test). *Why:* the code
   comments repeatedly anticipate "a future RT schema rename of the paragraph tag"
   (`generate_all_laws.py:939-947`) but nothing can detect one before a full run degrades.
   *Effort:* S. *Impact:* M. *Files:* `tests/test_rt_schema_canary.py`,
   `src/estleg/riigiteataja_common.py`, `src/estleg/check_rt_staleness.py`.

10. **Commit the law manifest, or stop documenting it.** *What:* either commit
    `krr_outputs/generation_manifest_laws.json` from the run that produced the corpus (restoring the
    slug freeze, `--from-manifest` replay and the per-act status ledger) or remove those three
    promises from the README until it exists. Add a `validate_all.py` check that the manifest's
    `outputsAll` slugs match the peeps on disk. *Why:* three documented reproducibility features are
    currently inert, and IRI stability across the next run is unprotected. *Effort:* S.
    *Impact:* H. *Files:* `krr_outputs/`, `README.md`, `scripts/validate_all.py`.

11. **Bring regulations up to the law pipeline's IRI and structure standard.** *What:* replace
    `sanitize_id(nr)` + `_{len(seen_ids)}` with `_paragraph_id_suffix` and
    `_dedupe_paragraph_suffix` from `law_structure`; call `build_subsections` so regulations gain
    lõige-level nodes; stamp `estleg:kehtiv` on every regulation. *Why:* KOV regulations are the
    corpus's largest act population and municipalities are a named adopter. Order-dependent IRIs
    break downstream enrichment on every re-ingest, and no lõige granularity means a KOV regulation
    cannot be cited the way it is cited in practice. *Effort:* M. *Impact:* H.
    *Files:* `src/estleg/generate_regulations.py`, `tests/test_generate_regulations.py`.

12. **Register official abbreviations for the uncovered laws.** *What:* re-derive
    `data/law_abbreviations.json` against the current `INDEX.json` using the RT `lyhend` field the
    search API already returns (it is read into `all_laws[title]["lyhend"]` at
    `generate_all_laws.py:235` and passed to the allocator), replacing `KARIST_2` with `KarS` and
    adding `VÕS`. Run the existing `scripts/migrate_uris.py` rename machinery. *Why:* IRIs are the
    graph's primary keys and its public face; `estleg:KARIST_2_Osa1_Par_49_1` is not recognisable to
    an Estonian lawyer, and AGENTS.md already declares the intended rule. *Effort:* M (mechanical,
    but it is an IRI migration). *Impact:* M. *Files:* `data/law_abbreviations.json`,
    `scripts/migrate_uris.py`, `krr_outputs/`.

13. **Retire or fix `generate_missing_parts.py`.** *What:* it duplicates
    `generate_multipart_law` for VÕS and TsÜS, writes to the same filenames without the provenance
    stamps, and left eight orphaned `tsiviilseadustik_osa*` artifacts (two with no act root). Delete
    the module and the orphan files, or make it call the shared builders with `kehtiv` / `tid` /
    `rt_url` threaded through and fix `DEFAULT_KEHTIV` to stop evaluating `today()` at import.
    *Why:* two generators writing the same path with different node shapes is a silent
    last-writer-wins hazard, and the orphans will be reported as source-removed on every run.
    *Effort:* S. *Impact:* M. *Files:* `src/estleg/generate_missing_parts.py`, `krr_outputs/`.

14. **Stop publishing fetch failures as content in `generate_rt_act_kinds.py`.** *What:* on
    `root is None`, record a `failed` status and skip the write instead of substituting
    `ET.Element("akt")`; pass a `stats` dict into `fetch_acts` and rename `rt_total` to
    `rt_listed` with an explicit `listingComplete` flag; set `generated` to the actual run timestamp;
    reuse `build_law_slug_map` for collision-safe slugs. *Why:* an empty stub that looks like a
    genuine body-less resolution is exactly the "indistinguishable from a source-fetch failure"
    problem the law pipeline solved with `contentStatus`/`status`. *Effort:* S. *Impact:* M.
    *Files:* `src/estleg/generate_rt_act_kinds.py`.

15. **Correct the README's coverage and freshness claims.** *What:* replace "the committed corpus
    contains no `noStructuredBody` stubs" with the real figure (365 of 1,023 dated acts, mostly
    treaties and ratification acts), reconcile `DEFAULT_KEHTIV` / `GENERATOR_KEHTIV_PIN`
    (`2026-05-01`) with the corpus (`2026-05-24`), and state plainly that regulations sit at a
    different snapshot and carry no `kehtiv`. *Why:* a public body evaluating adoption reads exactly
    these paragraphs, and a 365-act discrepancy in coverage is the kind of error that ends a
    procurement conversation. *Effort:* S. *Impact:* M. *Files:* `README.md`,
    `src/estleg/check_rt_staleness.py`, `src/estleg/generate_all_laws.py`.

16. **Publish both RT URLs and add Estonian operator docs.** *What:* emit the citable page
    (`https://www.riigiteataja.ee/akt/<gid>`) as `owl:sameAs` alongside the `.xml` in
    `dcterms:source`; translate the "Refreshing Data" and "Refresh SLA" sections and the ingest CLI
    help into Estonian. *Why:* RIK, KOV and ministry operators work in Estonian, and the provenance
    link should land a civil servant on the official reading view, not an XML download.
    *Effort:* S. *Impact:* M. *Files:* `src/estleg/generate_all_laws.py`,
    `src/estleg/generate_regulations.py`, `README.md`, `docs/`.

17. **Add annex, table and transposition-note extraction for laws.** *What:* port
    `extract_annexes` from `generate_regulations.py` to the law path, add a `tabel` handler that
    preserves cell structure (or at least emits `estleg:hasTable` with a source link rather than a
    collapsed blob), and extract `normtehnmarkus` into an explicit EU-transposition property.
    *Why:* law annexes carry rates, fees, forms and classification lists that ministries query
    directly, and the normitehniline märkus is the authoritative statement of which directives an
    act transposes — currently inferred by a separate heuristic layer instead of read from source.
    *Effort:* M. *Impact:* M. *Files:* `src/estleg/law_structure.py`,
    `src/estleg/generate_all_laws.py`, `shacl/`.

18. **Make a full refresh operable.** *What:* add `--regen-state` resume to
    `generate_regulations.py`, allow bounded concurrency (a small worker pool with the same polite
    delay per worker), and add a documented scheduled runner (GitHub Actions cron or a container)
    that performs the monthly RT consolidation refresh and opens a PR with the manifest diff.
    *Why:* "could RIK itself run this" currently answers "yes, but only as a multi-hour babysat
    manual run with no resume for the larger half". A scheduled, resumable pipeline is what makes
    the monthly SLA in the README credible and lowers the bus factor.
    *Effort:* M. *Impact:* H. *Files:* `src/estleg/generate_regulations.py`,
    `.github/workflows/`, `docs/`.

---

## Open questions

1. **Which run produced the committed corpus?** The regulation peeps lack `estleg:parseMode` and
   `estleg:kehtiv` even though the current `build_regulation_jsonld` always emits both, and no law
   peep carries `estleg:terviktekstId` even though the current code stamps it. Is the committed
   corpus older than the current generators, and if so is any of the generator behaviour reviewed
   here actually exercised on the shipped data?

2. **Was the KarS `<sup>` corruption ever seen, or only its escaped cousin?** `scripts/
   normalize_sup_markup_subcorpus.py` and several tests handle the escaped `&lt;sup&gt;` form. Is
   there an RT delivery mode that escapes it (the `kuvatavNr` CDATA path suggests yes), and does it
   vary by act or by API endpoint? That determines whether fix #1 needs to handle both.

3. **What is the intended relationship between `generate_missing_parts.py` and
   `generate_multipart_law`?** Both write `volaoigusseadus_osa{N}_peep.json`. Is the former a
   deprecated one-shot that should move to `scripts/archive/`, or is it still in the refresh runbook?

4. **Is `data/riigiteataja/karistusseadustik.xml` a real consolidated KarS or a schema sample?**
   Its `metaandmed` says `eesmark = "Tüviseaduse raamskeem XML struktuuri koostamiseks"` and
   `globaalID = 105022014002` (2014), while `karistusseadustik_osa1_peep.json` cites
   `/akt/122122025002.xml` (2025). Because it sits under the legacy slug-only cache name it is a
   valid `fetch_xml` fallback (`generate_all_laws.py:295-319`), so a `--missing-only` run could serve
   2014-era content under 2025 provenance. Is that file deliberately committed as a fixture only?

5. **Is `BUILD_EVALUATION_DATE` meant to be a reproducibility pin or a clock?** It is used both as
   the deterministic build date for temporal derivation and as the SLA "now". Those two roles
   conflict; which one should win, and should the SLA gate get its own unpinned date?

6. **Does RT publish a machine-readable consolidation feed?** The current freshness model polls
   per-act. If RT exposes a changed-since endpoint or a consolidation calendar, the refresh could
   become incremental and cheap rather than a full multi-hour sweep.

7. **What is the licensing position on redistributing RT XML?** Improvement #3 proposes retaining
   source XML as release assets. `NOTICE` disclaims MIT over the data corpus; does that analysis
   cover verbatim RT XML redistribution, and has RIK been consulted?
