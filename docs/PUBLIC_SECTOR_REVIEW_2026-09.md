# Public-sector readiness review — Estonian Legal Ontology

> **Historical baseline, not current status.** This review and its worksheets
> describe the September 3 tree below. Tier 0 (#677–#690) and #702 were merged
> on September 7. Findings and original measurements are preserved for the
> roadmap; use [project status](README.md#project-status) and the generated
> [validation report](VALIDATION_REPORT.md) for the reviewed current tree.

| | |
|---|---|
| Review date | 2026-09-03 |
| Tree reviewed | `main` @ `c96577d50c`, ontology `1.0.0`, working tree clean except an untracked `work-overview.html` |
| Lens | Usefulness to the Estonian public sector: Justiits- ja Digiministeerium (JDM), other ministries' legal departments, Riigikogu Kantselei, courts, Õiguskantsler, municipalities (KOV) and their supervisors, Riigi Teataja / RIK, RIA (Bürokratt, andmed.eesti.ee, X-tee), EU-level consumers (ELI, ECLI, EuroVoc, Publications Office) |
| Method | Fourteen parallel reviewers, each owning a non-overlapping file scope, reading code line by line and measuring the shipped corpus; one test/lint baseline; one landscape research pass with live sources; the ten highest-impact claims re-verified independently by the lead reviewer against the checkout, GitHub Actions and live riigiteataja.ee |
| Evidence | Per-area worksheets with `file:line` citations are in `docs/public-sector-review-2026-09/worksheets/` |

## Kokkuvõte

Eesti õigusontoloogia on arhitektuuriliselt tõsiseltvõetav: rikastuskonveier on
deklaratiivne ja valideeritud DAG, väljalaskeväravad on läbimõeldud, õiguste
mudel on juriidiliselt kirjaoskaja, ja KOV määruste sidumine volitusnormidega
(91% määrustest kannab `issuedUnder`-seost) on midagi, mida ükski teine Eesti
andmestik ei paku. Just see on avaliku sektori jaoks kõige väärtuslikum osa.

Samas ei ole avaldatud korpus praegu avaliku sektori jaoks usaldusväärne.
Kõik 1 146 seadust väidavad `temporalStatus = inForce`, kaasa arvatud 27
kehtetuks tunnistatud akti. Sanktsioonide kiht on juriidiliselt vale (KarS
§ 141 lg 2 maksimum 6–15 aastat on graafis 5 aastat). Ülaindeksitega
paragrahviviited on tekstis moondunud (§ 217² → "2172"). Ühelgi heuristilisel
kihil (EuroVoc, deontiline, sihtrühm, mõisted) ei ole mõõdetud täpsust.
Riigi Teataja uus platvorm (1. juuni 2026) lõpetas vana `/akt/{id}.xml`
liidese, mida generaatorid kasutavad, ja korpus on 102 päeva vana 45-päevase
SLA vastu. CI on olnud punane igal käivitusel alates v1.0.0 väljalaskest;
MCP-serveri 20 tööriistast 8 tagastab iga seaduse kohta tühja vastuse.

Kõige olulisem avaliku sektori jaoks: ELI ja Riigi Teataja identifikaatorid
puuduvad seaduste sõlmedel (kuigi andmed on kettal olemas), DCAT-kirje ei
vasta andmekirjelduse standardile 3.0.1 ega kanna masinloetavat litsentsi, ja
Riigikohtu lahendites on 42 otsuses avatekstis isikukood.

Soovitatud järjekord: (0) parandada CI, MCP tüübifilter, sanktsioonide
kiht, `temporalStatus` ja isikukoodid — päevad; (1) viia sisselaadimine üle
RT uuele avalikule API-le, lisada allika-XML ja sisuhäš igale aktile, luua
kuldandmestikud ja täpsusvärav — nädalad; (2) ELI-identiteet, DCAT-AP kirje
andmed.eesti.ee jaoks, ülevõtu seireraport, KOV volitusnormi aegumise vaade,
MCP auditilogid ja eestikeelne andmeleht — 1–3 kuud; (3) Sätla ja Bürokratt
pilootid, EKT taotlus (tähtaeg 28.09.2026), institutsionaalne kodu.

## 1. Verdict

The project has the right architecture and the wrong current state.

**What is genuinely good.** The enrichment pipeline is a validated, deterministic DAG with crash-safe rollback. CI checks Git-LFS pointers before every heavy gate. Outbound HTTP is allow-listed and redirect-pinned. The Estonian deontic classifier is real linguistics, not a keyword list. EU-side identity (CELEX + ELI `owl:sameAs`) is exemplary. The KOV layer is the strongest public-sector asset: 11,059 municipal regulations, 672 enabling acts, and the headline query "all Tartu linn regulations issued under KOKS § 22" works end to end today. The rights and data-protection documents name the hard problems instead of hiding them.

**What blocks adoption today.** Ten findings, each independently verified or measured against the shipped corpus:

1. **The Riigi Teataja ingest path is dead.** RT relaunched on 1 June 2026. The per-act URL the generators build (`/akt/{id}.xml`, `src/estleg/riigiteataja_common.py:278-291`) now returns the HTML app shell. The new `public-api/api/v1/akt/{id}/xml` returns XML. The corpus snapshot is 2026-05-24, 102 days old against a published 45-day SLA, and the staleness gate compares against a hard-coded 2026-06-01 so it cannot fail.
2. **CI has failed on every `main` run since the v1.0.0 release commit** (19 August), while `docs/VALIDATION_REPORT.md` still says "0 errors / PASSED". Three root causes: a missing ruff `select` pin in `mcp_server/pyproject.toml`, five tests that read LFS artifacts without the `corpus` marker, and the MCP suite failing on the retyped corpus.
3. **Eight of the twenty MCP tools return empty for every law.** Commit `c5625a748b` retyped provisions to bare `estleg:LegalProvision`; `mcp_server/estleg_mcp/data.py:758` still requires the `estleg:LegalProvision_` prefix. `get_provision`, `provision_history`, `who_references`, `references_of`, `court_decisions_for_law`, `competent_authority_for_law` and both `num_provisions` counters are dead. The README explains empty lists as "domain sparsity", so an evaluator would read the silence as expected.
4. **The sanctions layer states wrong penalties.** The sidecar predates the fix that moved extraction from 500-character summaries to full text. Verified: KarS § 141 (rape) is published with max 5 years; lg 2 carries 6 to 15. KarS § 114 (murder) has no minimum; the statute says 8 to 20. All 14 KarS general-part nodes are false positives, so a "life imprisonment" query returns six wrong provisions. 548 records the current extractor finds are absent from the shipped sidecar.
5. **Every act asserts `inForce`.** All 1,146 root `Act`/`Law` nodes, including 27 marked `owl:deprecated` with a successor. Structural cause: no version chain head carries `versionValidTo`, so `repealed` is unreachable, and `residual_status` returns `inForce` unconditionally (`src/estleg/derive_act_temporal_status.py:142-153`).
6. **Statutory text is corrupted in two ways.** Inline `<sup>` elements are flattened, so `§ 217²` is published as `§ 2172` and `§ 298¹` as `§ 2981` (146 law files, 2,342 nodes). Sub-point (`alampunkt`) markers are dropped, so `§ 7 lg 1 p 2` is not addressable and alternative conditions read as one sentence; only 4.1% of 111,973 subsections carry `itemNumber`.
7. **No heuristic layer has a measured precision.** The only gold set is an empty template. EuroVoc tags 66% of acts as "constitutional law" because "Vabariigi Valitsus" appears in every preamble; a road-signs regulation is labelled defence policy. `skos:closeMatch` is Levenshtein distance ≤ 2 and asserts "kolmas isik" (third party) ≈ "kolmas riik" (third country). 845 directives carry `hasNoTransposition` and 604 acts carry `hasNoCompetentAuthority` as bare booleans that read as legal findings but mean "not extracted".
8. **Shipped data does not match shipped code.** Six of twelve `pipeline_version` stamps in `krr_outputs/reports/kov/` name commits that do not exist in this repository. Re-running the Riigikohus generator would remint 11,983 of 12,104 decision IRIs. Every ingest generator is full-overwrite and would delete the court→provision, transposition and CURIA citation edges. Four enrichment layers are outside the documented DAG. The RT source XML the citation, concept, temporal and amendment layers need is git-ignored with no committed fetch script.
9. **Identity and standards are half-built.** Zero statutes carry an ELI URI or a structured RT identifier, although 984 acts embed the RT global id in `dcterms:source` and 376 ELI ids sit in `data/riigiteataja/english_eli.json`. Every ELI class/property mapping is `rdfs:subClassOf`/`subPropertyOf` only, invisible on the documented `inference=none` query surface. `estleg:targetGroup` is declared a datatype property and used with IRIs 45,546 times in a 600-file sample. `metadata.jsonld` has no `dcterms:license` on any of nine distributions, while `void.ttl` and `CITATION.cff` assert CC BY 4.0 over the whole dataset, which the project's own NOTICE retracts. The w3id PURL now resolves (merged 19 August, HTTP 302) but three documents still say 404 and there is no content negotiation.
10. **Legal safety and governance.** 42 Riigikohus decisions carry a plaintext 11-digit `isikukood` in `legalText`, 21 explicitly labelled. `curia_combined.jsonld.gz` with party names is a live release asset while the re-identification check in `docs/DATA_PROTECTION.md:71-77` is unticked and no rights or privacy notice is in the release bundle. Every safety-critical path in `.github/CODEOWNERS` points at a 9-line shim in `scripts/`, so the mandatory legal-correctness review never fires. One committer for all 352 commits, no branch protection, Dependabot security alerts disabled.

**Where the opportunity is.** The 2026 landscape has moved toward this project: RT's minister has publicly asked for "all norms for my scenario" search; JDM's Eesti.ai project 5 funds AI conflict and gap detection in drafts; the regulations revision needs "which regulations rest on a repealed enabling provision"; EIS is being replaced by Sätla on 1 October 2026 with a data-centric framing; Bürokratt runs LLM+RAG over institution-supplied documents; andmed.eesti.ee accepts non-government publishers on a DCAT-AP 3.0 profile; and Estonia is ELI pillar 1 only, leaving pillars 2 and 3 open. Section 6 maps each of these to a concrete artefact this repository can produce.

## 2. Method and evidence

Fourteen reviewers ran in parallel, each on a disjoint file scope, with the instruction to read every file in scope end to end and to measure the committed corpus rather than trust documentation. Their worksheets (about 8,400 lines, every claim with `file:line` or a counted measurement) are preserved under `docs/public-sector-review-2026-09/worksheets/`. The lead reviewer then re-verified the highest-impact claims directly:

| Claim | How verified | Result |
|---|---|---|
| CI red since release | `gh run list --branch main` | Validate Ontology: failure on 2026-08-19, 08-19, 08-24, 08-31; CodeQL passes |
| MCP provision filter | Read `_is_provision`; counted types in KarS osa2 | Filter requires `LegalProvision_`; corpus has 430 bare `LegalProvision` |
| KarS § 141 sanction | Compared sidecar node with provision `legalText` | Sidecar max 5 years; text says 6–15 in lg 2 |
| `temporalStatus` | Counted all root Act/Law nodes | 1,146 `inForce`, 0 other; 27 deprecated-and-inForce |
| Superscript corruption | Grepped KarS osa1 `legalText` | `213, 2172, 280` and `298, 2981` present |
| Old RT XML URL | `curl` live | HTTP 200 `text/html`, Angular shell; new public API returns XML |
| w3id PURL | `curl -I`, `gh pr view 6575` | Merged 2026-08-19; 302 to GitHub; version IRI 302 to the release |
| CODEOWNERS targets | `wc -l` | 9-line `runpy` shims |
| Ruff | `ruff check` (0.16.3) | 7 findings, all in `mcp_server/estleg_mcp/data.py` |
| Tests | full suite, corpus tier, MCP tier | 4,033 passed / 62 skipped locally; corpus tier 5 failed / 56 passed; MCP 19 failed / 69 passed |

Runs performed during the review, all read-only: `validate_all.py` (1 m 49 s, 26,791 files, 3,557 errors), `shacl_validate_all.py --bucket curia` (1 m 26 s, 66,740 violations, 1.17 GB RSS), the integration DAG dry run, the eval harness, and the full pytest tiers. No repository file was modified by the review.

## 3. Architecture as built

**Components.** Producer code lives in `src/estleg/` (about 70k lines across 103 modules); `scripts/` holds 102 nine-line shims plus `scripts/archive/`. `mcp_server/` is a separate package (`estleg-mcp`, FastMCP, 20 tools, stdio and streamable-HTTP transports, Docker image that clones the corpus at boot). `estleg_client/` is a 411-line read-only loader for enacted laws only. Tests: 192 modules, 74k lines, 4,095 collected. SHACL shapes: 44 node shapes. The corpus under `krr_outputs/` is 3.3 GB on disk (regulations 609 MB, provision versions 407 MB, Riigikohus 203 MB, EUR-Lex 107 MB, CURIA 74 MB); the clone is about 2.4 GB with LFS.

**Data flow.** Six operator-run ingest generators (laws, regulations, courts, lower courts, drafts, EU legislation, EU case law) write per-document `*_peep.json` files. `run_all_integration.py` then runs an 18-step serial DAG (documented as 15 or 16) of enrichment passes that mutate the peeps in place and write sidecars (`sanctions/`, `concepts/`, `institutions/`, `amendments/`, `provision_versions/`, `harmonisation/`, `annotations/`, `analytical/`), followed by `build_release_artifacts.py` which produces `INDEX.json` and the stub-closed `combined_ontology.jsonld`. Four further layers (`generate_provision_versions`, `derive_act_temporal_status`, `derive_court_interpretation_staleness`, `materialize_combined_inverses`) run outside the DAG.

**Load surfaces.** Three are documented: combined-only (one 290 MB JSON-LD file, stubs for cross-corpus entities), full public RDF (combined plus 14 subdirectories, the Seadusloome SPARQL path with inference disabled), and the retrieval JSONL projection for RAG (git-ignored, stamped 0.11.0, last built 2026-06-30). Bulk-load dumps (`.nt`, `.nq`, `.ttl`) are committed under LFS but are not produced by any DAG step and are a generation behind the JSON-LD (2,664,215 vs 2,247,778 triples for the same artefact).

**Consumer paths.** MCP (per-file peeps, never combined), Seadusloome SPARQL, retrieval JSONL, the Python client, five demo CSVs, and a Docker Compose Oxigraph quickstart that loads a 14-quad sample.

**Identity.** Minted `https://w3id.org/estleg/<ABBREV>_Par_<n>` IRIs as primary keys; official RT, CELEX and ECLI identifiers intended to live on properties and `owl:sameAs`. The T-Box (`controlled_vocabulary.jsonld`) has 56 classes and 226 properties with bilingual labels; 7 classes and 6 properties carry an external mapping.

**Gates.** `ruff` → `pytest` → `validate_all.py` (30 checks) → `shacl_validate_all.py` (7 buckets, RDFS inference) → `validate_combined_standalone.py` and `validate_seadusloome_sync.py` (no inference). CI runs the buckets as a matrix with LFS-pointer guards; `--all` is never run in CI and would need roughly 28 GB at the measured 3.9 KB per triple.

## 4. What is strong and should be defended

- **Pipeline orchestration.** `validate_dag()` checks cycles and dangling dependencies with a deterministic topological order; `_check_parallel_write_disjointness()` refuses unsafe `--parallel`; `snapshot_outputs()` is rename-aside and crash-safe; the ledger records every unreached step (`src/estleg/run_all_integration.py:486-568, 660, 801-868, 1327-1343`).
- **LFS discipline.** Three independent pointer guards (`fix_all_issues.py:957`, `serialize_corpus.py:114-118`, CI) so a degraded artefact cannot be built from pointers.
- **Fetch security.** Host allow-list, redirect re-check per hop, `<!DOCTYPE>`/`<!ENTITY>` rejection and payload caps (`src/estleg/estleg_common.py:1966-2057`).
- **Source-list completeness accounting.** `fetch_acts` counts pages fetched, failed and retried and turns a pagination cap into a fatal error unless `--allow-partial` is explicit (`riigiteataja_common.py:211-272`, `generate_all_laws.py:238-247`).
- **Anti-laundering guard on degraded parses.** A fetch that yields zero paragraphs while the root still has structure is recorded as `failed`, not written as a stub (`generate_all_laws.py:1841-1878`).
- **EU ingest.** POST-not-GET with a 202 guard, bounded backoff, stable `ORDER BY ?work` pagination, `partial: true` + exit 2 on truncation, CELLAR null-date sentinels rejected, `owl:sameAs` to both CELEX and ELI (`eurlex_common.py:27-94`, `generate_eu_legislation.py:495-610`).
- **Deontic classifier.** Modal disambiguation via nearby infinitives, negation windows, penal-provision suppression of `võib`, definition diversion (`classify_deontic.py:224-427`).
- **Point-in-time interval algebra.** Exclusive-end contract, same-date redaction collapsing, monotonicity guard, SHACL-enforced (`generate_provision_versions.py:1487-1659`).
- **Date honesty.** `extract_temporal_data` refuses to fabricate `{year}-01-01` and emits `xsd:gYear` when only the year is known (`:254-266, 802-808`).
- **KOV layer.** 100% of municipal regulations carry issuer, municipality, status and type bucket; 91.1% `issuedUnder`; 88.8% `implementsCitation` with lõige/punkt detail; the EHAK registry and issuer→municipality mapping fail loud on ambiguity (`kov_registry.py:24-176, 377-506`).
- **Rights and privacy literacy.** `NOTICE` and `docs/DATA_RIGHTS.md` separate copyright-free statutory text from the RT consolidated product and the sui generis database right; `docs/DATA_PROTECTION.md` names the Article 10 problem and the independent-controller consequence for reusers.
- **Consumer contract.** `docs/STABILITY.md` tiers predicates into Stable / Additive / Heuristic / Build-marker; `docs/ARCHITECTURE.md` names the load surfaces and the stub filter; `layers_available()` in the MCP server enumerates its own dead layers.
- **Determinism.** Pinned build dates, sorted iteration, atomic writes, no wall-clock reads in the classification path; reruns are byte-identical.

## 5. Findings by theme

Severity: **C** critical (blocks a public body from relying on the product), **H** high, **M** medium, **L** low. Worksheet references point to the detailed evidence.

### A. Fidelity to the official text

| # | Finding | Sev | Evidence |
|---|---|---|---|
| A1 | Inline `<sup>` flattened: `§ 217²` → `2172`, `§ 298¹` → `2981`; 146 files, 2,342 nodes. `_sup_to_unicode` only handles the escaped form; the committed KarS XML has 167 real `<sup>` and 0 escaped | C | `law_structure.py:47-66, 162-173`; verified in `karistusseadustik_osa1_peep.json` |
| A2 | `alampunkt` markers dropped; `_loige_item_numbers` looks for `punktNr`, which RT never emits; 4.1% of subsections carry `itemNumber`; `§ N lg M p K` not addressable | C | `law_structure.py:429-448, 654-662` |
| A3 | Sanctions sidecar built from truncated summaries: KarS § 141 max 5 y (actual 6–15), § 400 max 1 y (actual 1–3, plus 5–10% turnover), § 114 no minimum (actual 8–20); 14 KarS general-part nodes are false positives; 548 records missing, 26 unreproducible | C | `extract_sanctions.py:734-753, 1039-1053`; `krr_outputs/sanctions/sanctions_karistusseadustik_osa*.json` |
| A4 | `temporalStatus = inForce` on all 1,146 acts including 27 deprecated; 0 of 90,218 chain heads carry `versionValidTo`; `process_deprecated_copy` copies the successor's status onto the replaced act | C | `derive_act_temporal_status.py:69-81, 142-153, 195-223` |
| A5 | No audit trail: `estleg:contentHash` on 1 of 1,195 law peeps, `terviktekstId` on 0, one RT XML retained, and its recorded source is a local path | C | `generate_all_laws.py:507-522`; `krr_outputs/fetch_content_hashes.json` |
| A6 | Citation resolver has no left word boundary: `MTÜS § 12` → TÜS, `ELS § 7` → LS; the sibling court module has the guard | H | `extract_cross_references.py:1088-1091` vs `extract_court_provision_links.py:465` |
| A7 | Cross-law recall capped at 74 hand-listed laws; the ~1,000-title map exists but is wired only into the preamble pass; 24,280 self-act vs 5,545 cross-act edges | H | `extract_cross_references.py:365-392, 2198-2204, 2560-2568` |
| A8 | Point-in-time layer: 3,680 of 4,422 sidecars are current-snapshot only with no marker; repealed provisions are invisible; renumbering undetected, so old text can be attributed to a renamed § under a real `rtUrl` | H | `generate_provision_versions.py:364-452, 1578-1633` |
| A9 | Õiguskantsler annotations apply every cited § to every named act (mean 6.7 targets, max 208); default run emits author paraphrase under `annotationSource "Õiguskantsler"`; ~98% of bodies truncated at 2,000 chars with no marker | H | `generate_annotations.py:1145-1169, 1201-1264` |
| A10 | Competence is "mentioned near", not "competent": 80% of 30,360 bindings are type `general`; consultation (`kooskõlastatult`) asserted as competence by a test; 77% of institution→provision edges cut by a cap of 50 | H | `extract_institutional_competence.py:1115, 1275-1345`; `tests/test_extract_institutional_competence.py:2352` |
| A11 | Laws carry almost no official metadata (issuer, act number, entry into force, repeal date) although `parse_act_metadata` extracts them and regulations emit them | H | `riigiteataja_common.py:453-530`; `generate_regulations.py:545-592` |
| A12 | "Newest redaction wins" ranks by `globaalID`, which is `DDMMYYYYNNN`, so day-of-month dominates | H | `generate_all_laws.py:229`; `generate_regulations.py:644-664` |
| A13 | Law annexes, tables and the `normtehnmarkus` (the act's own transposition statement) are not extracted | M | `law_structure.py`; `generate_regulations.py:136-197` |

### B. Heuristic layers

| # | Finding | Sev | Evidence |
|---|---|---|---|
| B1 | No gold set with items; `eval/README.md` concedes 31–35% error on measured layers; "100% edge resolution" is true by construction | C | `eval/gold_sets/targetGroup.json`; `eval_harness.py:117-165` |
| B2 | EuroVoc: 9,049 of 13,747 acts tagged constitutional-law at a distinct-keyword gate of 1; 47.7% hit the 5-domain cap; road-signs regulation → defence policy, transport absent; EU acts (3,115) carry no subject while official EuroVoc is one SPARQL query away with a client already in the repo | C | `classify_eurovoc.py:93-96, 302-311`; `eurlex_common.py:22` |
| B3 | `skos:closeMatch` from Levenshtein ≤ 2: laev↔laps, arst↔arve, kolmas isik↔kolmas riik; not listed in the Heuristic tier | C | `extract_legal_concepts.py:587-664, 1701-1718`; `docs/STABILITY.md:11-14` |
| B4 | No curation loop: classifiers delete the whole layer before every run; a ministry correction cannot survive regeneration | C | `classify_deontic.py:509-524`; `classify_target_group.py:471-480` |
| B5 | `hasNoTransposition` (845) and `hasNoCompetentAuthority` (604) published as bare booleans; the disclaimer is not written on the `--patch-combined` path; a naive gap report would allege 2,368 false infringements | C | `generate_analytical_overlay.py:139-189, 279-287` |
| B6 | 405 of 720 official EUR-Lex national measures unmatched; 48 of 50 sampled are ministerial regulations the law-only index cannot see, although regulations are in the corpus | H | `generate_transposition_mapping.py:731-757` |
| B7 | `assertionConfidence` is a per-layer constant (0.55 / 0.65 / 0.70), the node takes the minimum, and 0 of 176,605 provision versions carry one | H | `estleg_common.py:1502-1533` |
| B8 | Target-group cap keeps declaration order, not evidence; bare `isik` tags 56% of provisions as citizen | H | `classify_target_group.py:29-35, 123-126, 414-419` |
| B9 | Published quality reports disagree with the integration logs of the same run; `FITNESS_REPORT.md` is two releases stale on every headline number | H | `krr_outputs/reports/*.json` vs `reports/integration/logs/*.log` |
| B10 | Embedding surface is a 64-dim hashing placeholder; KOV↔state similarity is unanswerable; `eurovoc_overlay.jsonld` and `kov_similarity_index.json` are declared but absent | M | `generate_embedding_index.py:41-58, 186-190`; `generate_similarity_index.py:38-53` |

### C. Data/code divergence and reproducibility

| # | Finding | Sev | Evidence |
|---|---|---|---|
| C1 | Six of twelve `pipeline_version` stamps unresolvable or stale; eight reports carry `run_timestamp: 1970-01-01` | C | `krr_outputs/reports/kov/*coverage.json` |
| C2 | Riigikohus generator would remint 11,983 of 12,104 IRIs and change literal types; every ingest generator is full-overwrite and would destroy `legalText`, `interpretsLaw`, `transposedBy`, `interpretsEULaw` | C | `generate_court_decisions.py:988-1009, 1867-1903`; `generate_eu_legislation.py:1084-1092` |
| C3 | RT XML inputs git-ignored, no fetch script; on a fresh clone the citation, concept, temporal and amendment layers regenerate nearly empty and `generate_amendment_history` strips `amendedBy` from ~5,654 acts first | H | `.gitignore:24-25`; `extract_cross_references.py:1775-1800`; `generate_amendment_history.py` Step 0 |
| C4 | Four layers outside `run_all_integration.STEPS`; docs say 15/16 steps, actual 18 | H | `run_all_integration.py:165`; `docs/RELEASE.md:4` |
| C5 | Release assets (four `.jsonld.gz`) have no producer in the repo, no checksum, no byte size; `hash_release_artifacts` hashes the committed tree | H | `run_all_integration.py:1042`; `metadata.jsonld` distributions |
| C6 | Named-graph dump silently skips `regulations` and `riigikohus` (slot sources do not exist) and exits 0; the SPARQL quickstart loses 812 MB of data | H | `serialize_named_graphs.py:63-81, 111-133, 281-285` |
| C7 | `.nt`/`.nq`/`.ttl` dumps regenerated by nothing, 18% off the JSON-LD, missing every inverse predicate | H | `README.md:77-83`; mtimes |
| C8 | `INDEX.json` stamps `date.today()` into the hashed release surface; `generate_index()` reads its own previous output for `registry_exceptions` | H | `fix_all_issues.py:736-747, 795-797` |
| C9 | No lockfile; Turtle output unsorted; release contract never executed in CI; no `release_manifest.json` exists | M | `pyproject.toml:8`; `serialize_corpus.py:196-197`; `validate.yml:166-167` |
| C10 | Release delta is a deprecated-vs-live diff inside one INDEX, capped at 50 IRIs, one version behind, relative `dcat:accessURL` | H | `emit_release_changes.py:22, 167`; `krr_outputs/changes-0.11.0.jsonld` |
| C11 | Retrieval projection git-ignored, seven weeks stale, stamped 0.11.0; chunks carry no `ontology_version`, no as-of date behind `in_force`, act-level `rt_url` | H | `generate_retrieval_projection.py:365-395`; `krr_outputs/retrieval/manifest.json` |

### D. Operability

| # | Finding | Sev | Evidence |
|---|---|---|---|
| D1 | Per-act XML fetch targets `/akt/{id}.xml`, which RT now serves as HTML; search API still works; new public API works | C | `riigiteataja_common.py:278-291`; live `curl` 2026-09-03 |
| D2 | Freshness gate compares against hard-coded `BUILD_EVALUATION_DATE = 2026-06-01`; measures 7 days lag where the real lag is 102 | H | `check_rt_staleness.py:79-104`; `estleg_common.py:1549-1550` |
| D3 | State regulations carry no `kehtiv`/`parseMode` (0 of 3,812), so `--missing-only` can never refresh them; provision IRIs order-dependent; no subsection nodes | H | `generate_regulations.py:255-258, 553` |
| D4 | `fetch_year` for Riigikohus fails a whole year silently and exits 0; `end_year = 2026` hard-coded | H | `generate_court_decisions.py:313-347, 1836-1860` |
| D5 | Nothing schedules any ingest; no cadence statement or staleness canary for courts, drafts, EU corpora | H | `validate.yml`; `metadata.jsonld:261` |
| D6 | Full refresh is serial with 0.3 s sleeps and no resume for regulations; enrichment DAG 36.6 min floor plus a 3.3 GB `copytree` snapshot per run; `.nt` serialisation needs 5–7 GB; none documented | M | `generate_regulations.py:1349`; `run_all_integration.py:822` |
| D7 | `generation_manifest_laws.json` is git-ignored, so slug freeze, `--from-manifest` replay and the per-act status ledger are inert | H | `.gitignore:30`; `generate_all_laws.py:1731-1734` |
| D8 | `generate_missing_parts.py` writes the same filenames as `generate_multipart_law` with different node shapes and left eight orphan `tsiviilseadustik_osa*` files | M | `generate_missing_parts.py:53, 221-529` |

### E. Validation and conformance claims

| # | Finding | Sev | Evidence |
|---|---|---|---|
| E1 | `docs/VALIDATION_REPORT.md` claims 23,069 files / 0 errors / PASSED (dated 2026-05-26); measured 26,791 files / 3,557 errors; 12 of 16 CI jobs red | C | `docs/VALIDATION_REPORT.md:7-14, 100-111` |
| E2 | 96% of those errors are validator bugs: `dcterms:title` array form rejected (405) although SHACL blesses it; `dcterms:subject` forced to array while Chapter nodes legitimately use it single-valued (3,021) | H | `validate_all.py:225, 538-544`; `shacl/estonian_legal_shapes.ttl:135-144` |
| E3 | RDFS inference manufactures 66,740 phantom violations in the curia bucket via `rdfs:domain` on `celexNumber` and `decisionDate`; `shacl/README.md` warns only about `rdfs:range` | H | `controlled_vocabulary.jsonld`; `shacl/README.md:26-35` |
| E4 | No gate checks text fidelity; `LegalProvisionShape` requires the derived `summary` and leaves `legalText` optional; no shape requires `dcterms:source` or `kehtiv`; 15% of sampled act roots have neither | H | `shacl/estonian_legal_shapes.ttl:207-256` |
| E5 | `estleg:consistencyChecked: true` asserted but `check_tbox_consistency.py` runs in no gate; `docs/DUPLICATE_IDS_REPORT.md` was generated from a pytest fixture while the corpus has ~50 real in-file duplicates | H | `metadata.jsonld`; `tests/test_fix_all_issues.py:842` |
| E6 | `validate_seadusloome_sync` can return 0 on a non-conforming graph (fall-through when severity is neither Violation nor Warning) | M | `validate_seadusloome_sync.py:596-616` |
| E7 | `kohtud/` and `analytical/` have no SHACL bucket; deprecated statutes in combined are never SHACL-validated; shapes have no version IRI and are not cited by `dcterms:conformsTo` | M | `shacl_validate_all.py:120-128`; `estleg_common.py:262-277` |
| E8 | No validation evidence per release: SHACL report uploaded only on failure | M | `validate.yml:376-382` |
| E9 | 25 test files read LFS artifacts without a guard; two default-tier tests run migration scripts against the real corpus without `--dry-run`; `scripts/archive` on the test path; the `isolated_krr` fixture is unused | H | `tests/test_normalize_sup_markup_subcorpus.py:116`; `pyproject.toml` pythonpath |

### F. Identity and standards

| # | Finding | Sev | Evidence |
|---|---|---|---|
| F1 | No ELI URI and no structured RT id on any statute; 0 of 1,195 law roots carry `globalId`/`terviktekstId` while regulations are 100% covered; 211 law roots have no RT link at all | C | worksheet commons-identity-kov, W1–W2 |
| F2 | ELI/schema.org mappings are subclass/subproperty only; the documented production surface runs `inference=none`; two `eli:` predicates exist in the A-Box | C | `controlled_vocabulary.jsonld`; `docs/ARCHITECTURE.md` |
| F3 | `estleg:targetGroup` declared `owl:DatatypeProperty` range `xsd:string`, SHACL says IRI, corpus uses IRIs; `targetGroupConcept` is a byte-identical stale duplicate placed in the Additive tier | H | CV:5942; `shacl/estonian_legal_shapes.ttl:371-382` |
| F4 | Structural classes contradict their own labels (`Division` "Jagu"/"jaotis", `Section` "Jaotis"/"jagu", `Section ⊑ LegalProvision`); no punkt class; `itemNumber` and `citationSource` undeclared; `dcat:` and `rdf:` prefixes undeclared in the CV | H | CV:266, 927, 964, 2060, 5088 |
| F5 | Institutions have no organisation superclass, free-string `institutionType`, no registrikood or X-tee code; ten Wikidata QIDs duplicated (`keskkonnaministeerium ≡ kliimaministeerium`); four slugs `sameAs` concepts (`Institution_linn ≡ wd:Q515`) | H | `extract_institutional_competence.py:1487-1493`; `data/wikidata_institutions.json` |
| F6 | Two abbreviation registries disagree on 50 of 95 shared laws (`KarS` vs `KARIST_2`); 454 of 1,146 act roots are raw slugs; `estleg:HMS_Map` is a tombstone while the live act sits at a raw slug | H | `estleg_common.py:28`; `data/law_abbreviations.json` |
| F7 | Project asserts `skos:prefLabel` and `skos:inScheme` onto 43 EuroVoc IRIs it does not own | H | `krr_outputs/eurovoc_concept_scheme.jsonld` |
| F8 | DCAT-AP: no `dcterms:license` on any distribution; `void.ttl:29` and `CITATION.cff:21` assert CC BY 4.0 over the whole dataset; non-authority `accrualPeriodicity`; no `accessRights`, `identifier`, `byteSize`; one relative `accessURL`; no `dcat:Catalog`; contact has no email; eight pinned tree URLs point at a pre-release commit | H | `metadata.jsonld:76, 233-263, 337-473` |
| F9 | w3id resolves since 2026-08-19 (302 to the repo page, version IRI to the release) but `docs/ARCHITECTURE.md:101-103`, `w3id/estleg/README.md:28-30` and `CITATION.cff:26-27` still say 404; conneg block commented out; `dcterms:conformsTo` dereferences to HTML | M | `w3id/estleg/.htaccess:22-27` |
| F10 | `act_iri_v2_sameas.jsonld` (14,440 bridges from pre-#445 IRIs) is not on any load surface | M | `estleg_common.py:262` |
| F11 | No `dcterms:language` anywhere; `legalText` untagged; 119 T-Box terms have no comment; 38 `@en` labels are Estonian | M | `controlled_vocabulary.jsonld` |

### G. Consumer surfaces

| # | Finding | Sev | Evidence |
|---|---|---|---|
| G1 | Eight MCP tools dead (see verdict item 3); `pytest mcp_server` 19 failed | C | `data.py:758-759, 1481` |
| G2 | `rt_url` falls back to `owl:sameAs`, returning a Wikidata IRI for KarS and VÕS; 134 laws return an empty URL | H | `data.py:809-823` |
| G3 | No audit log, no rate limit, no per-tool telemetry; one shared bearer token; unset `ESTLEG_TOKEN` fails open | H | `server.py:979-994` |
| G4 | 9% of provisions exceed the 2,000-char cap with no flag; `full_text=True` ignored on the `as_of` path | H | `server.py:29, 220-224, 257` |
| G5 | Four tools return no citation (`define_term`, `laws_for_subject`, `harmonisation_for_directive`, `amendment_history`) | H | `data.py:653-690, 1126-1134, 1729-1737` |
| G6 | 933 MB resident with unbounded `lru_cache`; first regulations call parses 14,874 files; README says corpus 1.5 GB, actual 3.3 GB; health-check start period 30 s cannot cover a first clone | M | `data.py:994-1587`; `Dockerfile:39` |
| G7 | Corpus tracked from `main` HEAD at boot, no tag pin; no `USER` in Dockerfile; tests excluded from image | M | `entrypoint.sh:15-22` |
| G8 | Python client covers enacted laws only (about 5% of the corpus); substring type matching; no PyPI publish; no data download helper | H | `estleg_client/load.py:20-22, 247-281` |
| G9 | DCAT statistics wrong (`totalFiles` 23,118 vs 26,837; court decisions 12,137 vs 12,104; law files 1,190 vs 1,195), propagated to nine files; the catalogue gate that should catch it is red | C | `metadata.jsonld:353, 367-368, 440` |
| G10 | Both published Riigikohus case-type tables are fabricated and contradict each other; actual split Civil 4,988 / Criminal 3,686 / Administrative 2,434 / Constitutional 800 / Misdemeanour 107 / Other 89 | H | `README.md:481-490`; `docs/README.md:121-128` |
| G11 | Estonian ministry overview (the only Estonian document) has no licence, no personal-data warning, no citation guidance, no version, is dated 8 May 2026, and is served through `htmlpreview.github.io` | H | `docs/eesti-oigusontoloogia-ulevaade.html`; `README.md:5` |
| G12 | No browse surface for a non-technical reader; every advertised fast path is a stub (`laws.csv` one row, Compose sample 14 quads, `llms.txt` links three git-ignored files, two API_GUIDE examples glob zero files behind a green test) | H | `krr_outputs/exports/`; `docs/API_GUIDE.md:194, 222, 440-454` |
| G13 | Documentation contradictions: 15 vs 14 vs 20 tools; "no REST API" vs eleven `/api` endpoints listed; CURIA table sums to 22,229 not 22,290 | M | `README.md:14`; `mcp_server/HANDOFF.md:26` |

### H. Legal safety and governance

| # | Finding | Sev | Evidence |
|---|---|---|---|
| H1 | 42 Riigikohus decisions carry a plaintext `isikukood`, 21 labelled; ~405 full names after süüdistatav/hageja/kaebaja; no screening code anywhere | C | worksheet ingest-courts-drafts-eu, W3 |
| H2 | `curia_combined.jsonld.gz` (party names in `rdfs:label`, ~22,290 records) is a live release asset; re-identification check unticked; no NOTICE/LICENSE/DATA_PROTECTION in the bundle | C | `docs/DATA_PROTECTION.md:20, 71-77`; `gh release view v1.0.0` |
| H3 | `.github/CODEOWNERS` safety-critical paths are 9-line shims; the legal-correctness gate fires on nothing; no branch protection | C | `.github/CODEOWNERS:14-38` |
| H4 | Bus factor 1 (352/352 commits), zero commits in July, no GOVERNANCE.md, no SECURITY.md, no institutional home; `TEAM_COLLABORATION.md` references a private local path | C | `git log`; `TEAM_COLLABORATION.md:5` |
| H5 | `CITATION.cff:21` asserts `license: CC-BY-4.0` over the dataset; GitHub detects the licence as "Other" because of the scope note in `LICENSE:1-19`; five rights VERIFY items open for 66 days while `docs/DATA_RIGHTS.md:3-7` says do not rely on it | H | `CITATION.cff`; `docs/DATA_RIGHTS.md:121-125` |
| H6 | Dependabot security alerts and automated security fixes disabled; two CodeQL alerts (one high) open 16 days; eight Dependabot PRs jammed by red CI; Actions pinned to mutable tags | H | `gh api` results in worksheet governance-rights-ci |
| H7 | Legacy-namespace CI guard excludes `scripts/migrate_namespace.py` (now a shim) while `src/estleg/migrate_namespace.py` holds 12 hits; the migration's own exclusion list has the same stale path | M | `validate.yml:258`; `src/estleg/migrate_namespace.py:58` |
| H8 | No accuracy disclaimer anywhere; `CHANGELOG.md` is an engineering log, not consumer release notes; no deprecation window | M | `README.md`; `docs/STABILITY.md` |
| H9 | Python 3.11 declared, 3.12 tested, 3.14 used locally; validation command spelled four different ways across `validate.yml`, `CONTRIBUTING.md`, the PR template and `CLAUDE.md` | M | `pyproject.toml:5`; `CLAUDE.md:23` |

### I. The KOV layer

| # | Finding | Sev | Evidence |
|---|---|---|---|
| I1 | The 151 `HistoricalMunicipality` nodes are orphans; the issuer→historical edge is computed and discarded | H | `enrich_kov_layer1.py:236` |
| I2 | `enactedByMunicipality` always names the current successor, so predecessor output is silently folded in (477 of 4,000 sampled regulations are from abolished units) | H | `enrich_kov_layer1.py:415` |
| I3 | KOV→KOV delegation citations (volikogu regulation → valitsus regulation) produce no node | M | worksheet commons-identity-kov, W17 |
| I4 | County is a label string, not an EHAK code; `regulationTypeBucket` is a free title slice | M | `enrich_kov_layer1.py:155` |
| I5 | The compliance query "which municipal regulations rest on a since-amended enabling provision" is one script away: `derive_court_interpretation_staleness.py` already implements the shape, KOKS § 22 has a 26-link version chain, and 9,823 regulations carry `implementsCitation` | opportunity | `derive_court_interpretation_staleness.py:170-186` |

## 6. The 2026 public-sector landscape and where this project fits

Facts below were checked against live sources on 2026-09-03; URLs are in the landscape worksheet.

| Counterpart | What changed in 2026 | Question they have | What estleg has today | Gap to close |
|---|---|---|---|---|
| JDM Riigi Teataja unit + RIK | New RT platform 1 June 2026 with public XML/JSON API and yearly bulk zips (regenerated 30 Aug 2026); minister's stated next step is "all norms for my scenario" AI search; Estonia is ELI pillar 1 only | Machine-readable provision-level metadata, ELI pillars 2–3 | FRBR spine, provision versions, court links | Ingest on the new API; ELI `owl:sameAs` per act; materialised ELI triples |
| JDM Eesti.ai project 5 | Funded AI for draft conflict and gap detection (approved April 2026) | For a draft: which provisions it touches, what cites them, their history, transposition, case law | `who_references`, `provision_history`, `transposition`, court links | Eight of those MCP tools are dead; provision-level draft impact absent (5.1% of drafts carry `amendsLaw`) |
| Sätla (EIS replacement, go-live 1 Oct 2026; JDM + Riigikantselei + Riigikogu Kantselei) | Data-centric legislative process from initiative to publication; enabling act passed 10 June 2026 | Reference resolution (act + § + lõige → identifier), ELI-DL-aligned draft identifiers | Minted IRIs, `eli-dl:ProcessStage` individuals | Zero `eli-dl:` predicates in the A-Box; draft phase is "first feed seen", not current stage; no Riigikogu stages (`api.riigikogu.ee` unused) |
| JDM regulations revision | All 4,009 state regulations reviewed; ~750 to repeal or simplify; some blocked on repealed enabling provisions | Which regulations rest on a repealed or amended volitusnorm | `issuedUnder`, `implementsCitation`, provision version chains | The staleness derivation (I5) and a CSV per ministry |
| HÕNTE amendment (VTK Oct 2025) | Transposition tables proposed also for EU regulations; AI use named | Asserted vs notified transposition per directive and regulation | 2,599 deadlines, 720 CELLAR national measures, 206 matched | Regulations in the matcher; `normtehnmarkus` parsing; three-valued status; gap CSV |
| Riigikogu Kantselei | Open API (drafts, votings, EuroVoc descriptors; CC BY-SA 3.0; 1 req/s); April 2026 hearing on AI in the public sector | Bill impact view: provisions, regulations, decisions touched | Draft nodes with `affectedBy` | Key drafts on Riigikogu UUID/mark; track share-alike licence |
| RIA Bürokratt | LLM+RAG over institution-supplied documents (2025); agent network (2026+); no MCP in official material; Aruait trust registry for AI agents | A RAG source with citations and as-of dates | Retrieval JSONL projection | Projection is git-ignored, stale, missing version/as-of per chunk; MCP later via Aruait |
| RIA andmed.eesti.ee | Non-government publishers accepted; Andmekirjelduse standard 3.0.1 (DCAT-AP 3.0); RIA recommends CC0; forwarded to data.europa.eu | A conformant DCAT record with licence, contact, byte size, access rights | `metadata.jsonld` with bilingual title/description/keywords | F8 items; publisher as an organisation |
| ELVL IKT centre, REM KOV department, Õiguskantsler | No KOV IT agency; Õiguskantsler exercises norm control over KOV regulations; no official statistic on regulations citing repealed enabling acts | Per-municipality legality view | The KOV layer (section 5.I) | I1, I2, I5; CSV per KOV |
| EKI / TartuNLP (EKT programme) | Two funding rounds close 28 Sep 2026; EstLLM has no legal training data | An Estonian legal evaluation set | Provisions, versions, court→provision links | Package as a benchmark; CLARIN-EE deposit for a persistent identifier |

Comparators a public body will benchmark against: Semantic Finlex (Finland, RDF + SPARQL with ELI/ECLI), Lovdata's free API opened 3 Nov 2025 explicitly for AI use (Norway), LiDO (Netherlands), Legilux ELI (Luxembourg), legislation.gov.uk. Estleg is closest in ambition to Semantic Finlex and LiDO and ahead of all of them on municipal regulations.

## 7. Improvement roadmap

Ideas are grouped by tier. Effort: S under a day, M days to two weeks, L longer. Impact: H/M/L for public-sector adoption. Where a worksheet has a fuller specification, it is named.

### Tier 0 — stop the bleeding (days)

| # | What | Why | Effort | Impact | Files |
|---|---|---|---|---|---|
| 0.1 | Add `[tool.ruff.lint] select = ["E4","E7","E9","F"]` to `mcp_server/pyproject.toml`; pin `ruff` to a minor range | Lint red on toolchain drift alone | S | H | `mcp_server/pyproject.toml`, `pyproject.toml` |
| 0.2 | Accept bare `estleg:LegalProvision` in `_is_provision` and the regulation counter; add a boot assertion that KarS yields >0 provisions, surfaced in `layers_available()` | Restores eight tools; prevents the next silent retype | S | H | `mcp_server/estleg_mcp/data.py:758, 1481`, `server.py` |
| 0.3 | Mark the five LFS-reading tests `corpus` (or give the default job LFS); fix the MCP job's corpus location; fix the order-dependent `test_migrate_uris` case; then require `lint`, `pytest`, `mcp-server`, `json-validation` via branch protection | CI must gate again before anything else is credible | S | H | five `tests/test_issue_*.py`, `.github/workflows/validate.yml` |
| 0.4 | `rt_url` accepts `dcterms:source` or a riigiteataja.ee `owl:sameAs` only; separate `external_ids` field | A Wikidata URL under an official label is worse than none | S | H | `data.py:809-823` |
| 0.5 | Regenerate the sanctions sidecar from current `legalText`; suppress general-part (Üldosa) extraction; add the turnover-percentage pattern and the missing sanction types; add SHACL bounds (`min ≤ max`, imprisonment ≤ 20 y, arrest ≤ 30 d, daily rates ≤ 500) | Removes the published claim that rape maxes at five years | S–M | H | `src/estleg/extract_sanctions.py`, `krr_outputs/sanctions/`, `shacl/` |
| 0.6 | `residual_status` returns `None` (act keeps `unknown`); deprecated act with a successor becomes `repealed`; SHACL rejects `owl:deprecated true` + `inForce` | Honest unknown is usable; uniform false `inForce` is not | S | H | `derive_act_temporal_status.py:142-153, 195-223`, `shacl/` |
| 0.7 | Screen 11-digit Estonian ID codes (with checksum) out of `summary`/`legalText` before writing; stamp `personalDataScreened`; publish the mask report | The item most likely to stop JDM, Riigikohus or AKI | S | H | new helper in `estleg_common.py`, `generate_court_decisions.py:1229, 1701` |
| 0.8 | Attach `NOTICE`, `LICENSE`, `docs/DATA_RIGHTS.md`, `docs/DATA_PROTECTION.md` to the release; fix `CITATION.cff:21` and `void.ttl:29` to the compilation-layer scope | Personal data and third-party text currently ship with no notice; the only machine-readable licence overclaims | S | H | release assets, `CITATION.cff`, `krr_outputs/void.ttl` |
| 0.9 | Point every `.github/CODEOWNERS` safety-critical entry at `src/estleg/…`; add a CI check that each path resolves to a file over 1 KB | The legal-review gate protects nothing today | S | H | `.github/CODEOWNERS` |
| 0.10 | Regenerate `metadata.jsonld` counts and propagate to the nine files; replace both Riigikohus case-type tables with the index-derived split and add a test | A steward's first spot check fails today | S | H | `metadata.jsonld`, `README.md`, `docs/README.md`, `tests/test_validate_all.py` |
| 0.11 | Fix the legacy-namespace exclusion path in three places and add a test that the three lists agree | The CI guard is red and a migration re-run would disarm itself | S | M | `validate.yml:258`, `tests/test_no_legacy_namespace.py:26`, `src/estleg/migrate_namespace.py:58` |
| 0.12 | Enable Dependabot security alerts and automated fixes; triage the two CodeQL alerts; merge the jammed Dependabot PRs once CI is green | Literal checkboxes on a supplier questionnaire | S | M | repo settings |
| 0.13 | Label `krr_outputs/kohtud/` as a sample (`(näidis)`) unconditionally or remove it from `PUBLIC_LOAD_SUBDIRS`; delete the two synthetic `2000000xx` rows | Three rows, two synthetic, are published as a load surface | S | H | `generate_lower_court_decisions.py:233-238`, `docs/ARCHITECTURE.md:37` |
| 0.14 | Update the three documents that say w3id is 404; generalise the version rule in `.htaccess` | The PURL resolves; the docs undersell it | S | L | `docs/ARCHITECTURE.md:101-103`, `w3id/estleg/README.md`, `CITATION.cff:26-27`, `w3id/estleg/.htaccess:19` |

### Tier 1 — make it trustworthy (weeks)

| # | What | Why | Effort | Impact | Files |
|---|---|---|---|---|---|
| 1.1 | Move per-act fetch to `https://www.riigiteataja.ee/public-api/api/v1/akt/{id}/xml` and the JSON metadata sibling; keep the search API; pin the post-June schema in the canary and make the canary fetch one live act | The current path returns HTML; the corpus cannot be refreshed | M | H | `riigiteataja_common.py:278-291`, `tests/test_rt_schema_canary.py`, `check_rt_staleness.py` |
| 1.2 | Retain fetched RT XML as a dated release asset (or LFS); stamp `contentHash`, `terviktekstId`, `skeemiNimi` on every act; treat a missing stored tid as stale; commit `generation_manifest_laws.json` | First question from any reviewer: prove this node matches the official text on that date | M | H | `generate_all_laws.py:507-522, 917-920`, `.gitignore:30` |
| 1.3 | Un-pin the freshness gate to `date.today()` (keep an override for tests), cover regulations and the five other corpora with per-corpus lag budgets, publish them as `dcterms:accrualPeriodicity` per distribution; replace `end_year = 2026`; make Riigikohus partial years loud (retry + `partial: true` + exit 2) | A freshness guarantee that cannot fail is worse than none | S | H | `check_rt_staleness.py`, `generate_court_decisions.py:313-347, 1836`, `metadata.jsonld` |
| 1.4 | Handle real `<sup>` children in `_marker_pruned_text`/`_loige_body_text`; model `alampunkt` as a citable node (`…_Par_n_Lg_m_P_k`) or at least keep the `k)` marker in the lõige text; repair the 146 affected files | Wrong section numbers and lost sub-points change the legal reading | M | H | `law_structure.py:47-66, 162-173, 429-448, 654-662` |
| 1.5 | Give laws the metadata block regulations already emit (`globalId`, `issuer`, `actNumber`, `entryIntoForce`, `repealDate`, `lastAmendmentDate`); fix redaction ranking to use `kehtivuseAlgus`, not `globaalID` | The flagship product has thinner provenance than the secondary one | S | H | `generate_all_laws.py`, `generate_regulations.py:644-664` |
| 1.6 | Add the left word boundary to `_PAT_ABBREV`; derive `KNOWN_ABBREVIATIONS` from `data/law_abbreviations.json` (RT `lyhend` first) with a drift test; wire `law_title_to_iri` into the in-law citation pass; keep unresolved court citations instead of deleting them | Wrong-statute links and a 74-law recall ceiling | M | H | `extract_cross_references.py:1088, 2198-2204`, `estleg_common.py:28`, `normalize_court_referenced_law.py:70-131` |
| 1.7 | Split ingest (raw layer) from enrichment (overlay layer) so a re-scrape cannot delete edges; freeze the Riigikohus IRI scheme and add it to the MAJOR-version list; add fixture-based regeneration tests that assert byte-equality with committed files | The refresh step currently destroys the most valuable content | L | H | all six ingest generators, `docs/ARCHITECTURE.md` |
| 1.8 | Adjudicate gold sets (about 300 items per layer: cross-references, sanctions, EuroVoc, deontic, target group, competence, court links, 50 closeMatch pairs) with a legal reviewer, RT citation per item, negative cases included; make `eval_harness --gold-set` a CI gate with floors; regenerate `FITNESS_REPORT.md` from numbers, not prose | No number in the repository currently answers "how often is this wrong" | L | H | `eval/gold_sets/`, `src/estleg/eval_harness.py`, `eval/FITNESS_REPORT.md` |
| 1.9 | Fetch official EuroVoc for all EU acts from CELLAR (`cdm:work_is_about_concept_eurovoc`) with the existing client; drop `constitutional-law` and rank Estonian-side domains by hits per 1,000 tokens with a cap of 3; stop emitting edit-distance `closeMatch` (merge orthographic variants as `altLabel`) | Turns a third of subject metadata authoritative at zero editorial cost; removes actively wrong assertions | S–M | H | new `fetch_eurovoc_official.py`, `classify_eurovoc.py:93-96, 302-311`, `extract_legal_concepts.py:587-664` |
| 1.10 | `data/heuristic_overrides.jsonl` consulted by every classifier before clearing and writing; `prov:wasAttributedTo` and confidence 1.0 on overridden nodes | A ministry correction must survive regeneration | M | H | `estleg_common.py`, the four classifiers, `shacl/` |
| 1.11 | Rename `hasNoTransposition`/`hasNoCompetentAuthority` to coverage vocabulary (`noTranspositionEdgeInCorpus`, `competentAuthorityNotExtracted`) and stamp method + date on the `--patch-combined` path | Extraction gaps must not read as legal findings | M | H | `generate_analytical_overlay.py:139-189, 279-287`, `shacl/`, `docs/SCHEMA_REFERENCE.md` |
| 1.12 | Fix the two validator bugs (`dcterms:title` array; `dcterms:subject` on Chapters), narrow the two `rdfs:domain` axioms, wire `check_tbox_consistency` and `check_numeric_identity_strings` into `validate_all`, delete or regenerate `DUPLICATE_IDS_REPORT.md`, and generate `VALIDATION_REPORT.md` from a build with SHA and timestamp | 96% of gate errors are noise; the conformance statement is false | M | H | `validate_all.py:225, 538-544`, `controlled_vocabulary.jsonld`, `docs/VALIDATION_REPORT.md`, `validate.yml` |
| 1.13 | Require `legalText` on provisions of `structuredBody` acts (coverage-baselined) and `dcterms:source` + `kehtiv` on act roots; add a per-release sampling gate that re-fetches N provisions from RT and diffs | Gates must protect the authoritative text, not the derived summary | M | H | `shacl/estonian_legal_shapes.ttl`, new `check_text_fidelity.py` |
| 1.14 | Add the four missing layers to `STEPS`; add a CI gate that every `pipeline_version` resolves to a commit; commit the RT XML fetch script; replace `date.today()` in `INDEX.json` with `BUILD_EVALUATION_DATE`; move `registry_exceptions` to a committed `data/` file; add a `constraints.txt` | An artefact must be rebuildable from a stated commit and stated inputs | M | H | `run_all_integration.py:165`, `fix_all_issues.py:736-797`, `.github/workflows/validate.yml`, `pyproject.toml` |
| 1.15 | One `build_release_assets` DAG step: gzip the combined dumps, regenerate `.nt/.nq/.ttl` and `estleg_all.nq.gz`, build `chunks.jsonl`, write `SHA256SUMS` and per-asset `dcat:byteSize`/checksum; make the named-graph dump exit non-zero on a missing slot and give the two dead slots real sources; stamp `owl:versionInfo` on all combined heads | Consumer artefacts currently have no producer, no hash, and silently omit two corpora | M | H | new `build_release_assets.py`, `serialize_named_graphs.py:63-133`, `estleg_common.py:900-917` |
| 1.16 | Test hygiene: single LFS-aware accessor fixture; never run migrations against the real corpus from a test; explicit unit/committed/corpus tiers with `pytest-xdist` and `pytest-timeout`; drop `scripts/archive` and `examples` from `pythonpath`; source-XML→peep golden pairs for 8–10 documents; a network guard | Local green hides CI red; the suite cannot see text-fidelity regressions | M | H | `tests/conftest.py`, `pyproject.toml`, `tests/fixtures/` |

### Tier 2 — make it public-sector ready (one to three months)

| # | What | Why | Effort | Impact | Files |
|---|---|---|---|---|---|
| 2.1 | Mint `owl:sameAs` to the RT ELI URI and `eli:id_local` for every act from the global id already in `dcterms:source` (confirm the Estonian-language ELI template with RT first); promote `officialEnglishText` to an `eli:LegalExpression` with `eli:language`; point `dcterms:source` at the human RT page and keep the XML as a manifestation link; publish the `act_iri_v2_sameas` bridge on the load surface | The first join any Estonian public body will try; fills ELI pillars 2–3 that Estonia lacks | M | H | new `backfill_rt_eli.py`, `generate_all_laws.py`, `krr_outputs/void.ttl`, `docs/SCHEMA_REFERENCE.md` |
| 2.2 | Materialise ELI and schema.org triples at combined-build time (`eli:LegalResource`, `eli:LegalExpression`, `eli:realizes`, `eli:is_part_of`, `eli:date_entry_in_force`, `schema:Legislation`) with SHACL coverage shapes | The production SPARQL surface runs without inference; the mappings are invisible today | M | H | combined builder in `estleg_common.py`, `shacl/` |
| 2.3 | T-Box repairs: `targetGroup` → ObjectProperty and retire `targetGroupConcept`; one class per Estonian structural level plus a punkt class; drop `Section ⊑ LegalProvision`; declare `itemNumber`, `citationSource`, `dcat:` and `rdf:`; SKOS-type the eight controlled-value families; `org:Organization` on `Institution`, SKOS `institutionType`, `owl:sameAs` from the 66 EU institutions to the Publications Office corporate-body list; stop asserting labels into EuroVoc's namespace; deprecate the string halves of six duplicated fact families; language tags on expressions and `legalText` | An external ontologist must be able to load this into GraphDB with OWL2-RL and read the `@en` projection | M | H | `controlled_vocabulary.jsonld`, `shacl/`, generators, `eurovoc_concept_scheme.jsonld` |
| 2.4 | Rewrite `metadata.jsonld` to Andmekirjelduse standard 3.0.1: licence IRI per distribution (compilation layer CC BY 4.0; RIA recommends CC0), `accessRights`, `identifier`, `byteSize`, `format`, absolute `accessURL`s, EU frequency authority, `vcard:hasEmail`, `dcat:Catalog` wrapper, organisation publisher, `conformsTo` the shapes; request a non-government publisher account on andmed.eesti.ee | Listing is the cheapest official visibility and is forwarded to data.europa.eu | M | H | `metadata.jsonld`, `krr_outputs/void.ttl` |
| 2.5 | Transposition monitoring product: index state and ministerial regulations in the matcher; promote "no measure required" rows to `no_measure_required`; parse the `normtehnmarkus` block into `transposesDirectiveAsserted`; derive a three-valued `transpositionStatus` (transposed / no measure required / no evidence in corpus, never "not transposed"); ship `transposition_gap.csv`; query CELLAR `cdm:measure_national_implementing` to lift the 206-directive coverage | The artefact JDM's HÕNTE and transposition work actually want, and the one that would otherwise allege 2,368 false infringements | M | H | `generate_transposition_mapping.py`, new `extract_ntm_directives.py`, `generate_eu_legislation.py` |
| 2.6 | KOV legality view: `derive_kov_enabling_staleness.py` (walk `implementsCitation` → provision version in force at the regulation's entry into force → stamp `enablingProvisionOutdated` + `earliestSupersedingDate`); add the historical-issuer dimension (`historicalMunicipality` on issuers, `enactedByHistoricalMunicipality` on acts, diacritic-correct names); EHAK county codes; CSV per municipality; pilot with three to five KOVs via ELVL | The one query REM, ELVL and the Õiguskantsler cannot run anywhere else, and the project's strongest public-sector claim | M | H | new module, `enrich_kov_layer1.py:155, 236, 415`, `serialize_tabular.py` |
| 2.7 | "What changed" product: wire the existing `collect_versions_by_date` / `link_amendments_to_versions` into the amendment generator, parse amendment type from `muutmismarge`, point `amends` at provision IRIs, then a provision-level release delta against the previous tag (uncapped, JSONL if large) as a DAG step and DCAT distribution, and a `what_changed(law, since, until)` MCP tool | The most common question from a ministry legal department and Riigikogu Kantselei; the substrate exists | L | H | `generate_amendment_history.py:241-337`, `emit_release_changes.py`, `mcp_server/` |
| 2.8 | MCP hardening: JSON audit line per call with corpus commit SHA; snapshot envelope on every tool; fail closed when `ESTLEG_TOKEN` is unset; per-consumer token map; explicit `truncated`/`full_length` and `full_text` honoured everywhere; citation on every row; SQLite index instead of 933 MB of cached JSON; corpus pinned to a release tag; non-root container; Estonian-first tool descriptions with a `language` parameter; a REST/OpenAPI facade over `data.py` for non-MCP consumers and X-tee registration; the four missing tools (`what_changed`, `transposition_gaps`, `kov_regulations_citing`, `explain_provision`) | The entry ticket for any ministry or Bürokratt deployment | L | H | `mcp_server/estleg_mcp/server.py`, `data.py`, `docker/` |
| 2.9 | Estonian data statement `docs/ANDMED.et.md` (coverage, sources, freshness, derived vs official, personal-data position, contact) and a refreshed ministry overview with licence, GDPR, citation and version sections, hosted on GitHub Pages; an audience router at the top of `README.md`; move the operator runbook to `docs/` | Adoption decisions are made in Estonian by lawyers | M | H | `docs/`, `README.md` |
| 2.10 | Extend `estleg_client` to regulations, court decisions, drafts and EU acts with exact type matching and row helpers; publish to PyPI with a corpus download helper; real CSV exports (sanctions with subject and EUR-normalised amounts, institutions, competences, drafts, regulations) and a Compose default that loads a real named-graph dump | CSV and a live SPARQL box are what a KOV IT shop and a ministry analyst can consume in an afternoon | M | H | `estleg_client/`, `serialize_tabular.py`, `docker-compose.yml` |
| 2.11 | Drafts: record each feed observation as an `eli-dl:ProcessStep` with a date and derive the current phase; ingest Riigikogu stages from `api.riigikogu.ee` (key on draft UUID and mark, reuse its EuroVoc descriptors, track CC BY-SA 3.0); `initiatedBy` as an IRI into `institutions/`; stamp `derivationMethod` on minted ECLIs, retyped case types and title-parsed EU citations; use CELLAR `cdm:case-law_cites_legal_resource` instead of title regex | "Follow this bill from ministry to Riigikogu to Riigi Teataja" is impossible today | L | H | `generate_draft_legislation.py:488-620`, new `generate_riigikogu_proceedings.py`, `generate_eu_court_decisions.py:498-523` |
| 2.12 | Competence: bind only when the institution is the syntactic subject of a competence verb; emit everything else as `mentionsInstitution`; consultation blocklist; passive `kehtestatakse …ministri määrusega` as regulation-making power; registrikood / X-tee member code on institutions; fix the duplicate and concept-level QIDs; model institution temporal validity | A wrong competent authority sends a citizen to the wrong regulator; joins need registrikood | L | H | `extract_institutional_competence.py`, `data/wikidata_institutions.json`, `data/institution_aliases.json` |
| 2.13 | Annotations: pair each cited § with its own act by proximity; quarantine paraphrase and templated prose into `editorialNote`; `isExcerpt` and source length; SHACL requiring a source URL for anything attributed to the Õiguskantsler | Misattribution to a named constitutional body | M | H | `generate_annotations.py:1123-1270`, `data/annotations/seed_annotations.json` |
| 2.14 | GDPR: run and record the re-identification check against RIK's current feed; decide the names policy with a DPO; add a named controller, a data-subject contact route and an erasure procedure; add `containsPersonalData` to the in-band heads of the release assets; gate any lower-court sweep on sign-off | The only finding that can become a regulator complaint | M | H | `docs/DATA_PROTECTION.md`, release process |
| 2.15 | Governance: `GOVERNANCE.md` with succession and archival plan; `SECURITY.md`; a project contact that is not a personal mailbox; one legal-domain reviewer on CODEOWNERS; restore bare MIT for detectability with the scope note in NOTICE and a REUSE `LICENSES/` directory; close or date the five rights VERIFY items; consumer release notes; deprecation window in `docs/STABILITY.md`; Python matrix 3.11–3.13; SHA-pinned Actions; one `make check` used by CI and contributors | Bus factor 1 and self-review are what a funding or procurement committee will lead on | M | H | new files, `.github/`, `LICENSE`, `NOTICE`, `docs/STABILITY.md` |
| 2.16 | Regulations pipeline parity: `_paragraph_id_suffix`-based IRIs, `build_subsections`, `kehtiv` on every regulation, `--regen-state` resume, bounded concurrency; a scheduled monthly refresh runner that opens a PR with the manifest diff; document runtime, RAM and disk in `docs/RELEASE.md`; `--snapshot auto` that skips the 3.3 GB copy on a clean tree; stream the `.nt`/`.nq` serialisation | "Could RIK run this" currently answers "as a multi-hour babysat run with no resume for the larger half" | M | H | `generate_regulations.py`, `run_all_integration.py:801-868`, `serialize_corpus.py:88-200`, `.github/workflows/` |
| 2.17 | Retrieval projection for Bürokratt: `ontology_version`, `evaluation_date`, `act_iri`, `kehtiv`, `language`, stable `chunk_id`, provision-level `rt_url`, optional splitting of long §; publish `chunks.jsonl.gz` as a release asset with a DCAT distribution; fix `llms.txt` | A RAG source must be auditable months later from the chunk alone | M | H | `generate_retrieval_projection.py:365-395`, `krr_outputs/retrieval/` |

### Tier 3 — strategic (quarters)

| # | What | Why | Effort | Impact |
|---|---|---|---|---|
| 3.1 | Ingest EIS/Sätla seletuskiri attachments and parse the amending formula (`§ 14 lõiget 2 muudetakse ja sõnastatakse järgmiselt`) into `amendsProvision`; layer HÕNTE impact areas as a SKOS scheme | Makes the draft layer answer "what would change in the law if this passed" for JDM's Eesti.ai project 5 | L | H |
| 3.2 | Propose a read-only reference-resolution and identifier service to the Sätla project (act + § + lõige → estleg IRI → RT ELI URI) for the 1 October 2026 first stage | Sätla is the data-centric replacement for EIS and needs exactly this | L | H |
| 3.3 | One-institution Bürokratt pilot with the retrieval projection as a RAG source ("what does the law require of me", with citations); later register estleg-mcp in the Aruait trust registry | Bürokratt consumes institution-supplied documents today, MCP later | M | H |
| 3.4 | EKT 2026 targeted round (deadline 28 Sep 2026): an Estonian legal-reasoning evaluation set from provisions, point-in-time versions and court→provision links, with TartuNLP/EKI and a law faculty; CLARIN-EE deposit for a persistent identifier, which also closes the Zenodo DOI item | Funding plus an academic home; EstLLM has no legal data | M | H |
| 3.5 | Content-negotiating 303 resolver behind `w3id.org/estleg/` serving Turtle/JSON-LD per node and an HTML description page; serve the T-Box at `/vocabulary` | Turns "stable identifier" from a claim into a fact | M | M |
| 3.6 | Incremental build (`--only-changed` from a per-file hash manifest) and a KOV↔state topical similarity index | Monthly refresh a ministry can operate; the cross-municipality "how do others regulate this" question | L | M |
| 3.7 | Institutional home: RIK/JDM, Tartu Ülikool, or the open-data programme; transfer the namespace and the maintainer contact | A namespace is a permanent commitment one person cannot make | L | H |

## 8. Corrected numbers

| Statistic | Published | Measured | Where published |
|---|---|---|---|
| Total JSON/JSON-LD files | 23,118 | 26,837 | `README.md:8`, `metadata.jsonld`, `docs/VALIDATION_REPORT.md` |
| Enacted-law files | 1,190 | 1,195 | same |
| Riigikohus decisions | 12,137 | 12,104 | nine files |
| Riigikohus case types | two contradictory tables | Civil 4,988 / Criminal 3,686 / Administrative 2,434 / Constitutional 800 / Misdemeanour 107 / Other 89 | `README.md:481-490`, `docs/README.md:121-128` |
| `noStructuredBody` stubs | "none" | 365 of 1,023 dated acts | `README.md` coverage section |
| MCP tools | 15 / 14 | 20 | `README.md:14`, `mcp_server/HANDOFF.md:26` |
| DAG steps | 15 / 16 | 18 | `docs/RELEASE.md:4`, `docs/ARCHITECTURE.md:74` |
| Corpus size for the MCP volume | ~1.5 GB | 3.3 GB | `mcp_server/README.md:88` |
| Combined triples | 2,247,778 (JSON-LD) and 2,664,215 (`.nt`) | same artefact, 18% apart | `README.md:77-83` |
| Sanctions coverage | 18.1% | 21.2% | `eval/FITNESS_REPORT.md` |
| Competent-authority coverage | 55.5% | 49.9% | `eval/FITNESS_REPORT.md` |
| Fitness-report ontology version | 0.11.0 | 1.0.0 | `eval/FITNESS_REPORT.md` |
| w3id PURL | "still 404" | 302 since 2026-08-19 | `docs/ARCHITECTURE.md:101-103`, `w3id/estleg/README.md:28-30` |

## 9. Open questions for the maintainer

1. Was the committed corpus produced from this repository? Three `pipeline_version` stamps do not resolve here, the regulation peeps lack fields the current generator always emits, and the Riigikohus IRIs do not match the current generator. If the producing checkout is elsewhere, is its history recoverable, or should the corpus be regenerated from HEAD before any public-body conversation?
2. Has a full cold rebuild ever been executed end to end, and what did it cost? The version layer alone is 1 h 46 m of live RT traffic; no runtime figure appears in any document.
3. Was `main` known to be red since the release? If CI is treated as advisory, that should be stated, but then it cannot also be the reproducibility story.
4. Which Riigikohus IRI scheme is canonical (`RK_<case>` as shipped or `RK_<case>_<oid>` as coded)? This decides whether the fix is a code change or a corpus migration.
5. What is Riigi Teataja's registered Estonian-language ELI template? The project's `/en/eli/{id}` usage proves ELI-shaped URIs exist; the `/eli/ee/{id}/consolide` form was observed live. Confirm before minting `owl:sameAs`.
6. Has the re-identification check against RIK's anonymised feed ever been run informally? If the names came straight from RIK and were never joined against another source, writing that down converts the worst-looking finding into a resolved one.
7. Is `inference=none` on the Seadusloome endpoint a correctness constraint or a cost choice? If RDFS entailment is possible on the combined graph, materialising ELI triples becomes optional.
8. Is there a candidate institutional home (RIK, JDM, Tartu Ülikool, the open-data programme), and has any been approached?
9. Does anyone downstream load `combined_ontology.nt`? If Seadusloome loads the JSON-LD, the stale dumps are a documentation bug; if a Jena instance loads the N-Triples, it is serving a graph with no inverse edges.
10. Should the KOV corpus share a gate with enacted law? The citation extractor's pass/fail signal is the KOV output, so a regression in the 1,120 enacted laws cannot fail it.

## Appendix — worksheets

Detailed evidence, one file per scope, in `docs/public-sector-review-2026-09/worksheets/`:

| File | Scope |
|---|---|
| `ingest-rt.md` | Riigi Teataja law and regulation ingest, staleness canary |
| `ingest-courts-drafts-eu.md` | Riigikohus, lower courts, EIS drafts, EUR-Lex, CURIA |
| `enrich-structure.md` | cross-references, court→provision links, inverses, temporal, amendments, provision versions |
| `enrich-classifiers.md` | EuroVoc, deontic, target group, concepts, similarity, embeddings |
| `enrich-policy-layers.md` | sanctions, competence, transposition, harmonisation, draft impact, annotations, analytical overlay, eval harness |
| `pipeline-release.md` | integration DAG, combined builder, release assets, serialisations, retrieval projection |
| `validation-gates.md` | `validate_all`, SHACL gates, shapes, conformance documents |
| `commons-identity-kov.md` | `estleg_common`, abbreviation registries, IRI migrations, EHAK/KOV layer, w3id |
| `mcp-server.md` | estleg-mcp data layer, tools, transport, Docker |
| `consumer-client-docs.md` | Python client, README, consumer docs, DCAT record, exports |
| `tbox-standards.md` | T-Box and ELI/ELI-DL/ECLI/EuroVoc/DCAT-AP/CPOV/Akoma Ntoso alignment |
| `governance-rights-ci.md` | CI, security, licence, rights, GDPR, governance, repo hygiene |
| `tests-baseline.md` | ruff/pytest baselines, test architecture |
| `public-sector-landscape.md` | 2026 Estonian and EU landscape with sources, adoption pathways |

Where a worksheet and this document disagree, this document is the corrected position (notably: the w3id PURL resolves; the review lead re-verified the items in section 2).
