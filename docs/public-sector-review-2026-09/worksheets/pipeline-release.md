<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Pipeline & Release — public-sector readiness review

## Scope & method

Files read in full or in the cited regions:

- `src/estleg/run_all_integration.py` (1,775 lines — DAG, validators, snapshot, manifest, main)
- `src/estleg/fix_all_issues.py` (constants, `generate_index`, `canonical_combined_inputs`, `_merge_node_properties`, `_emit_closure_stubs`, `generate_combined_jsonld`)
- `src/estleg/build_release_artifacts.py`, `write_build_manifest.py`, `emit_release_changes.py`, `stamp_combined_dataset_heads.py`
- `src/estleg/serialize_named_graphs.py`, `serialize_corpus.py`, `serialize_tabular.py`
- `src/estleg/generate_retrieval_projection.py` + `krr_outputs/retrieval/{README.md,manifest.json,llms.txt,chunks.sample.jsonl}`
- `docker-compose.yml`, `krr_outputs/dataset_build_manifest.json`, `krr_outputs/changes-0.11.0.jsonld`, `krr_outputs/INDEX.json` (structure), `metadata.jsonld` (distributions only)
- `docs/RELEASE.md`, `docs/ARCHITECTURE.md`, `AGENTS.md`, `scripts/README.md`, README "Validation and release order" / "Canonical artifacts" / "RDF serializations" / "Tabular export"
- Supporting: `.github/workflows/validate.yml`, `.gitattributes`, `pyproject.toml`, `estleg_common.py` (header/stamp/save_json helpers)

Read-only commands actually run (no repository file was modified):

- `python3 scripts/run_all_integration.py --release --dry-run` and `--release --validate-only --dry-run`
- `python3 scripts/run_all_integration.py --release --parallel 2` (confirms the exit-2 guard fires)
- `python3 -c "from estleg.serialize_named_graphs import SLOTS, _resolve_source; ..."` (slot resolution)
- `du`, `stat`, `wc -l`, and small `python3 -c` inspections of committed artifacts

`generate_index()` was deliberately **not** executed: it takes no `krr_dir` argument and writes straight to `krr_outputs/INDEX.json` (`fix_all_issues.py:735`), so running it would have mutated the repo.

---

## Strengths (with file:line)

1. **The DAG is a real, validated DAG, not a shell script.** `validate_dag()` (`run_all_integration.py:486`) checks name uniqueness, dangling and self dependencies, runs Kahn's algorithm for cycles (`:524-546`), and produces a **deterministic** topo order by breaking ties on source index (`:523`, `:534`, `:541`). The historical phase order is therefore reproducible, not incidental.

2. **The parallel-unsafety guard is honest and it actually fires.** `_check_parallel_write_disjointness()` (`run_all_integration.py:660`) computes the transitive-dependents closure and rejects `--parallel > 1` when two concurrent-eligible steps declare overlapping `writes` globs. Verified live:
   ```
   FATAL: invalid step DAG: --parallel > 1 is unsafe for this DAG: steps
   'extract_cross_references.py' and 'generate_transposition_mapping.py' have
   no dependency relation but both write overlapping corpus target *_peep.json
   ```
   This is a rare thing to find: a project that built the parallel runner, measured that its own data model forbids it, and wired the refusal into startup rather than shipping a footgun.

3. **Crash-safety of the rollback snapshot is genuinely well engineered.** `snapshot_outputs()` (`run_all_integration.py:801`) does rename-aside to `.bak.<pid>`, then copies into a `.new.<pid>` staging dir and `os.replace`s it onto `KRR_DIR`, so a kill mid-copy can only ever leave a partial tree at `.new.<pid>`. `restore_outputs()` (`:837`) moves the partial run aside before restoring and rolls back the rollback if the second rename fails. `main()` catches `BaseException` (not `Exception`) around `run_dag` so Ctrl-C restores rather than orphaning (`:1637-1652`).

4. **Determinism is treated as a first-class constraint in most places.** The evaluation date is pinned and passed explicitly to the temporal step (`run_all_integration.py:150-165`, step args at `:261`) precisely to stop `date.today()` churn. `combined_ontology_header()` deliberately carries **no wall-clock date** (`estleg_common.py:738-741`). Closure stubs are emitted `@id`-sorted (`fix_all_issues.py:1163`). N-Triples and N-Quads output is line-sorted for byte-stability (`serialize_corpus.py:_sorted_line_dump`, applied at `:196-197`). The retrieval projection sorts laws, provisions and versions and uses atomic `os.replace` tree swaps (`generate_retrieval_projection.py:975-996`).

5. **The `--validate-only` staleness backstop closes a real hole.** `_combined_staleness_error()` (`run_all_integration.py:1080`) refuses to stamp `releaseOk: true` when `combined_ontology.jsonld` / `INDEX.json` are older than the newest `*_peep.json`, with a well-argued `<` vs `<=` choice.

6. **The release build fails closed on a missing release-surface artifact.** `releaseArtifacts.missing` is folded into `releaseOk` (`run_all_integration.py:1516-1524`), explicitly because `validate_all.py` only warns on an absent `metadata.jsonld`.

7. **Git-LFS pointer stubs are guarded where it matters most.** `_require_combined_build_sources()` (`fix_all_issues.py:957`) refuses to build a degraded combined artifact from pointers, `serialize_corpus.load_jsonld()` raises on a pointer (`:114-118`), and CI pulls each LFS path and greps for the pointer header before every heavy gate (`.github/workflows/validate.yml:236-244, 320-326, 355-364`). This is the correct pattern and it is applied consistently in three places.

8. **The step ledger is complete even on failure.** The serial path drains every unreached step into the ledger as `not_reached` (`run_all_integration.py:1327-1343`), so `succeeded + failed + skipped == totalSteps` and a manifest reader can see exactly where the run stopped.

9. **`--resume-from` has a precondition check.** `_missing_resume_writes()` (`run_all_integration.py:1150`) refuses to treat a skipped step as done when its distinctive (non-peep) outputs are absent.

---

## Weaknesses / risks (with file:line, severity)

### W1 — `INDEX.json` stamps a wall-clock date into the hashed release surface. **Severity: HIGH**

`fix_all_issues.py:795-797`:
```python
index = {
    "generated": _dt.date.today().isoformat(),
```
`INDEX.json` is in `RELEASE_ARTIFACT_INDEX_GLOBS` (`run_all_integration.py:441`), so it feeds `releaseArtifacts.contentHash`. Two builds of a byte-identical corpus on different days therefore produce **different release content hashes**. The whole stated purpose of `contentHash` — "compare two release manifests to explain corpus/artifact drift" (`docs/RELEASE.md:391-393`) — is defeated, and this is the exact "timestamp-only churn" `AGENTS.md` forbids. The in-code justification ("so consumers can detect when the registry actually changed") does not hold: the field changes when nothing changed.

Observed drift already in the tree: `INDEX.json` `generated` is `2026-08-19`, while `dataset_build_manifest.json` `generated` is `2026-06-01`. The release's own artifacts disagree about when the release was built.

Answer to "byte-identical outputs on a fresh machine": **no**, and this is the single cheapest thing to fix.

### W2 — The four release assets the DCAT catalog cites are produced by no code in the repository. **Severity: HIGH**

`metadata.jsonld` advertises the consumer path as tagged GitHub Release assets:
`combined_ontology.jsonld.gz`, `eelnoud_combined.jsonld.gz`, `eurlex_combined.jsonld.gz`, `curia_combined.jsonld.gz` (`metadata.jsonld` `dcat:distribution`, and `docs/ARCHITECTURE.md:12-16`).

A repository-wide search for `jsonld.gz` finds only two prose mentions (`docs/ARCHITECTURE.md:14`, `README.md:277`) and no producer. The only module that gzips anything is `serialize_named_graphs.py`. So:

- there is no single release command that builds the release assets;
- `hash_release_artifacts()` (`run_all_integration.py:1042`) hashes the **committed tree**, never the shipped `.gz` files;
- no checksum or byte size is recorded for any asset anywhere;
- the assets are not reproducible from source by any documented step.

Answer to "are the release assets reproducible from source / complete / checksummed": **no, no, no**.

### W3 — The full named-graph SPARQL dump silently drops two of seven corpora. **Severity: HIGH**

`serialize_named_graphs.SLOTS` (`:63-81`) declares sources `regulations/REGULATIONS_COMBINED.jsonld` and `riigikohus/riigikohus_combined.jsonld`. **Neither file exists in the tree.** `_resolve_source()` (`:111`) returns `None` for a missing *or* LFS-pointer source, `iter_slot_nquads()` (`:117`) then `return`s an empty generator, and `main()` prints `skipped (no source): ...` and **exits 0** (`:281-285`).

Verified live:
```
laws              resolved=True  -> combined_ontology.nq
regulations       resolved=False -> NONE (slot silently omitted)
riigikohus        resolved=False -> NONE (slot silently omitted)
eurlex/curia/drafts/enrichment-layers resolved=True
```
A ministry following the README quickstart (`README.md:126-131`: `python3 -m estleg.serialize_named_graphs --write` then `ESTLEG_DUMP=./krr_outputs/estleg_all.nq.gz docker compose up`) gets a SPARQL endpoint missing **609 MB of state and municipal regulations** and **203 MB of Riigikohus decisions**, with a zero exit code and only an easily-missed stdout line. The combined build has a hard LFS guard; this path has none, and there is no `--require-all` / `--strict` flag.

### W4 — The committed N-Triples / N-Quads / Turtle dumps are stale and ungated. **Severity: HIGH**

Nothing in the 18-step DAG regenerates `combined_ontology.{nt,nq,ttl}`. They are not in `RELEASE_ARTIFACTS` (`run_all_integration.py:435-439`) and not in `_combined_staleness_error()`'s derived set (`:1108-1110`), so `releaseOk: true` can be stamped on a tree where they are arbitrarily old.

They are stale right now. mtimes:

| Artifact | mtime |
|---|---|
| `combined_ontology.jsonld` | 2026-08-19 09:11 |
| `combined_ontology.ttl` | 2026-08-19 08:30 |
| `combined_ontology.nt` | 2026-08-19 00:41 |
| `combined_ontology.nq` | 2026-08-19 00:42 |

And the counts do not reconcile. `README.md:77-83` states both facts side by side: rdflib loading `combined_ontology.jsonld` yields **2,247,778** triples; the committed `.nt` dump is **2,664,215** triples (`wc -l` confirms 2,664,215 lines in both `.nt` and `.nq`). Same nominal artifact, **416,437 triples apart (18.5%)**. A triplestore team loading the `.nt` and a team loading the JSON-LD get materially different graphs from the same release, and the README presents both numbers without explaining the gap.

### W5 — The "release delta" is not an inter-release delta. **Severity: HIGH for the public-sector use case**

`emit_release_changes.py:167` — `build_index_deprecated_vs_live_record()` diffs INDEX `deprecated_laws` against INDEX `laws` **inside a single INDEX.json**. The committed output confirms what that means:

```
@id            https://w3id.org/estleg/dataset/changes-0.11.0
addedCount     1121      # i.e. essentially every law in the corpus
removedCount   23
listedIriCap   50        # estleg:added is truncated to 50 of 1121
```

Problems, in order of importance for a subscribing ministry:

- It answers "which slugs are live and which were deprecated", **not** "what changed since the last release". There is no previous-release baseline anywhere in the repo to diff against.
- Version mismatch: the dataset is 1.0.0, the only published delta is `changes-0.11.0.jsonld`, and `DEFAULT_OUTPUT` (`:24`) would now write `changes-1.0.0.jsonld`, which does not exist. `metadata.jsonld` still points at the 0.11.0 file.
- Granularity is act-level IRIs only. A ministry needs **provision-level** change ("which § of which act changed text, and in which redaction"), which the corpus can express — `provision_versions/` carries `versionRedactionId` / `versionValidFrom` — but the delta does not use.
- `estleg:added` / `estleg:removed` are capped at 50 (`:22`), so the machine-readable list is unusable as a feed even for what it does cover.
- `emit_release_changes.py` is not a DAG step; it is run by hand.
- The `dcat:accessURL` for this distribution is the relative string `krr_outputs/changes-0.11.0.jsonld`, not an IRI — a DCAT-AP conformance defect.

Answer to "is there a release delta a ministry could subscribe to": **not yet**.

### W6 — There is no published retrieval feed; the projection is stale and off-version. **Severity: HIGH for the Bürokratt use case**

`krr_outputs/retrieval/manifest.json` says `"ontology_version": "0.11.0"` and the whole directory was last written **2026-06-30**, against a corpus refreshed 2026-08-19. `llms.txt` likewise advertises "ontology version 0.11.0".

The payload is not published at all. Per `krr_outputs/retrieval/README.md` (Artifacts table), `chunks.jsonl` (181 MB), `outlines/`, and `context_packs/` are **git-ignored** and there is no `dcat:distribution` for any of them in `metadata.jsonld`. `llms.txt` links `chunks.jsonl`, `outlines/`, `context_packs/` — all dead relative to any published tree. A state assistant would have to clone 2.4 GB, `git lfs pull`, run the 37-minute DAG, then run the projection to obtain the feed.

It is also not in the DAG by design — see the `TODO(#523)` at `generate_retrieval_projection.py:53-58`: wiring it in would break the `test_rebuild_step_topo_sorts_strictly_last` invariant.

### W7 — Retrieval chunks carry citations and dates but not provenance. **Severity: MEDIUM-HIGH**

`build_chunk_record()` (`generate_retrieval_projection.py:365-395`) emits exactly ten fields, confirmed against all 200 sample records:

`provision_iri, redaction_id, paragraph, act_title, abbrev, rt_url, valid_from, valid_to, in_force, text`

Good: the record is independently citeable and carries a validity window. Missing, for a chunk that has been copied into a vector store and later has to be audited:

- **No `ontology_version` and no `evaluation_date`.** Both live only in `manifest.json`, which does not travel with the chunk. An auditor holding a chunk cannot say which release it came from.
- **`in_force` is a bare boolean with no as-of date.** It is computed against `BUILD_EVALUATION_DATE` (`compute_in_force`, `:347`) but that date is not in the record. "Is this in force?" answered `true` with no anchor date is exactly the claim a legal AI assistant must not make unqualified.
- **`rt_url` is act-level, not provision-level.** Sample: `https://www.riigiteataja.ee/akt/210122020005` for `§ 1`. Citing a 400-paragraph act as the source of one paragraph is weak audit evidence.
- **No `act_iri`**, so joining a chunk back to the act node requires the `abbrev` heuristic.
- **No language tag** on `text`, and no `estleg:kehtiv` snapshot date (the RT consolidation the text reflects).
- **No `chunk_id`.** The natural key is `(provision_iri, redaction_id)` but it is not materialised, and `redaction_id` is `null` for the 738 consolidated records (`manifest.json`).
- **No size bound.** `text` is the whole §; observed max in the 200-record sample is 6,545 characters, and no splitting logic exists in the module. A consumer must re-chunk, which breaks the "self-contained citeable unit" promise for long provisions.

Answer to "does the projection carry citations, dates, and provenance per chunk so an AI answer can be audited": **citations yes, dates partly, provenance no**.

### W8 — Sibling combined dumps carry no version stamp; one carries a wrong one. **Severity: MEDIUM-HIGH**

Observed heads:

| File | `owl:versionInfo` |
|---|---|
| `combined_ontology.jsonld` | `1.0.0` |
| `act_expressions_combined.jsonld` | `0.11.0` |
| `eurlex/eurlex_combined.jsonld` | *absent* |
| `curia/curia_combined.jsonld` | *absent* |
| `eelnoud/eelnoud_combined.jsonld` | *absent* |
| `concepts/concepts_combined.jsonld` | *absent* |

Three of those absent-version files are shipped as v1.0.0 release assets (W2). `act_expressions_combined.jsonld` is worse: it is in `COMBINED_ALLOWED_JSONLD` (`fix_all_issues.py:95-104`), so the shipped v1.0.0 combined graph **contains a node asserting `owl:versionInfo "0.11.0"`**.

Root cause is precise. `stamp_combined_dataset_head()` (`estleg_common.py:919`) only reaches `combined_dataset_header()` — which does carry a version — when there is **no** existing head (`:951-955`). These files always have a generator-written head, so they take the `apply_inband_dataset_fields()` branch (`:900`), whose backfill loop is:
```python
for key in ("dcterms:publisher", "dcterms:license", "void:uriSpace"):
```
`owl:versionInfo` and `owl:versionIRI` are simply not in that list, so a non-flagship combined dump can never acquire a version stamp.

### W9 — The release build snapshots the whole 3.3 GB corpus by `copytree` on every run. **Severity: MEDIUM**

`snapshot_outputs()` (`run_all_integration.py:822`) does `shutil.copytree(backup, staging)` over `krr_outputs/`: **3.3 GB across 26,876 files** (measured). This runs unconditionally unless `--no-restore-on-failure` is passed (`:1630-1634`), including for a `--resume-from` re-run, and is `rmtree`d on success (`cleanup_snapshot`, `:865`). Consequences for an operator:

- a release build needs ~2× corpus free disk (~7 GB) beyond the checkout;
- several minutes of pure I/O before step 1 starts, on top of the ~37 minutes of real work;
- on macOS `shutil.copyfile` is a real byte copy, not an APFS clone, so there is no cheap path today.

A `--snapshot none|copy|reflink` option, or simply skipping the snapshot when the tree is a clean git working directory (git is already the rollback), would remove this.

### W10 — Documentation understates the DAG by three steps. **Severity: MEDIUM**

Actual `STEPS` length is **18**, confirmed by `--release --dry-run` printing `[1/18] … [18/18]`. Documented as:

- `docs/RELEASE.md:4` "The 15 enrichment steps", `:262` "Runs all 15 steps", `:270`, `:344` `"totalSteps": 15`
- `docs/RELEASE.md` step table lists 16 rows
- `docs/ARCHITECTURE.md:74` "16 steps, serial"
- `run_all_integration.py:7` "the 15 enrichment scripts", `:580` "the DAG is tiny — 15 steps"

The two undocumented steps are `rebuild_eurlex_combined` (`:202`) and `link_curia_eu_legislation.py` (`:212`); the 16th, `build_release_artifacts.py`, replaced the ledger's historical `fix_all_issues.py`. The `docs/RELEASE.md` step table is also wrong about `classify_eurovoc.py`: it claims `writes: *_peep.json`, whereas the code declares `eurovoc/eurovoc_overlay.jsonld`, `reports/eurovoc_classification.json`, `eurovoc_concept_scheme.jsonld` (`:245-253`). No test pins `len(STEPS)`, so this will drift again.

### W11 — The `reads` half of DAG validation is close to vacuous. **Severity: MEDIUM**

`validate_dag()`'s reads check (`run_all_integration.py:551-559`) seeds `produced` with `COMMITTED_INPUTS`, which includes `*_peep.json`, `regulations/**/*_peep.json`, `riigikohus/*_peep.json`, `eelnoud/*_peep.json`, `curia/*_peep.json`, `eurlex/*_peep.json` (`:118-134`). Almost every step's `reads` is one of those patterns, so the check passes trivially. Because the peep corpus is *both* a committed input *and* the write target of nearly every step, the validator cannot detect a **missing ordering edge** — the failure mode that actually matters here. It catches typos in glob names, nothing more. `_pattern_covered()` is documented as "intentionally lenient" (`:711-719`), which is honest, but the resulting guarantee is weaker than `docs/RELEASE.md:57-60` implies.

Concrete instance the check cannot see: `generate_similarity_index.py`'s `depends_on` (`:339-354`) omits `rebuild_eurlex_combined` and `link_curia_eu_legislation.py`, so it is only ordered after them by source-order tie-breaking, not by a declared edge.

### W12 — `generate_index()` is not a pure function of the corpus. **Severity: MEDIUM**

`generate_index()` reads the **existing** `INDEX.json` and carries forward `registry_exceptions` and per-law multipart annotations (`fix_all_issues.py:736-747`, and `preserve_multipart_annotations(entry, existing_laws_by_name.get(base_name))` at `:817`). `INDEX.json` is documented as derived (`docs/ARCHITECTURE.md`, "Derived: combined_ontology.jsonld, INDEX.json … Do not hand-edit"). Deleting a derived artifact and rebuilding it is a normal operator action, and here it **silently loses** the 6 `registry_exceptions` entries and the multipart annotations on 6 laws. A derived artifact that seeds itself is not reproducible from source.

Related: `generate_index()` takes no `krr_dir` parameter (unlike `generate_combined_jsonld(krr_dir=…)`, `:1279`) and writes to the module-level `KRR_DIR`, so it cannot be exercised against a fixture tree.

### W13 — No dependency lockfile; RDF output is not pinned to a serializer version. **Severity: MEDIUM**

`pyproject.toml:8` declares `rdflib>=7.1,<8` and there is no lockfile, constraints file, or `requirements*.txt` in the repo. `combined_ontology.ttl` is committed under LFS but `serialize_graph()` does **not** sort Turtle (only `nt`/`nq` go through `_sorted_line_dump`, `serialize_corpus.py:196-197`), so Turtle output ordering, prefix emission, and blank-node labelling are at the mercy of whichever rdflib 7.x resolves at install time. "Byte-identical outputs on a fresh machine" cannot be claimed while the serializer version floats.

### W14 — Serializing combined to `.nt`/`.nq` has a much larger memory footprint than the documented 3 GB. **Severity: MEDIUM**

The README's 3,089 MB peak RSS figure is for **loading** JSON-LD. Producing the dumps stacks several full copies on top:

- `serialize_graph()` calls `target.serialize(...)` which materialises the entire output as one Python string — 444 MB for `.nt`, 549 MB for `.nq`;
- `_sorted_line_dump()` (`serialize_corpus.py:88`) then builds a list of **2.66 million** line strings, sorts it, and `"\n".join`s it — a second and third full copy;
- for `nq`, `dataset_from_graph()` (`:155`) copies every triple into a *second* in-memory rdflib store before serialising.

Realistic peak is 5–7 GB for `.nt` and higher for `.nq`. No document states this. A ministry sizing a build VM from the README's "3 GB" will OOM.

`generate_retrieval_projection.load_version_map()` (`:311`) has the same shape: it loads **every** file in `provision_versions/` (407 MB on disk, 211k version nodes) into one global dict before emitting a single chunk.

### W15 — `write_build_manifest.py` and `stamp_combined_dataset_heads.py` are outside the DAG and carry hand-maintained constants. **Severity: MEDIUM**

Neither script appears in `STEPS`. `dataset_build_manifest.json` therefore drifts freely from the tree it claims to describe — it currently records `generated: 2026-06-01` for a corpus written 2026-08-19. `DATASET_CONTENT_SHA` (`write_build_manifest.py:38`) is a hardcoded commit SHA that must be bumped by hand, and `git_sha()` (`:60`) records `HEAD` with no dirty-tree check, so a manifest can attest a clean SHA for a modified tree.

### W16 — The "complete dataset" DCAT distribution is a GitHub source archive of LFS pointers. **Severity: MEDIUM**

`metadata.jsonld`'s first distribution, "JSON-LD ontology files (complete dataset)", has `dcat:downloadURL` = `…/archive/2676a1f8….zip` (built by `github_archive_url()`, `write_build_manifest.py:163`). GitHub's source archives do **not** resolve Git LFS objects, and `.gitattributes` puts `combined_ontology.{jsonld,nt,nq,ttl}`, `reports/similarity_index.json`, `reports/eurovoc_classification.json`, `analytical/analytical_overlay.jsonld`, `annotations/oiguskantsler_seisukohad.jsonld`, `eurlex/eurlex_combined.jsonld`, and `curia/curia_combined.jsonld` under LFS. A public body downloading the advertised "complete dataset" receives ~130-byte pointer stubs in place of every large artifact, with no error.

### W17 — No DCAT distribution carries `dcat:byteSize` or a checksum. **Severity: MEDIUM**

Verified across all nine `dcat:distribution` entries in `metadata.jsonld`: `byteSize` is absent everywhere, `spdx:checksum` / `dcat:checksum` absent everywhere, and `dcterms:format` is present on only four of nine. DCAT-AP consumers (and any ministry procurement checklist) expect size and checksum on a published distribution so a download can be verified. This compounds W2: nothing anywhere in the repo records a hash of a shipped asset.

### W18 — `serialize_tabular.py` silently truncates legal text. **Severity: MEDIUM**

`truncate_legal_text()` (`serialize_tabular.py:172-176`) cuts at `LEGAL_TEXT_MAX = 2000` with **no ellipsis and no companion `text_truncated` column**, and is applied to every provision row (`:275`). In the committed 200-row `provisions.csv` sample, 4 rows are already clipped. An analyst loading the CSV in pandas or R has no way to know which cells are complete. Separately, `_write_parquet()` (`:629`) types every column as `pa.string()`, so dates, booleans, and numeric sanction bounds arrive untyped.

The committed "tabular export" is also a shape demo rather than a product: 1 law, 200 provisions, 133 citations, 281 court decisions, 2 sanctions. No full-corpus CSV is published as a release asset, and `README.md:159` explicitly says not to commit one.

### W19 — There is no incremental build; every step is a full-corpus pass. **Severity: MEDIUM**

No step exposes a `--since`, `--changed`, `--only`, or manifest-diff flag (checked across `extract_cross_references`, `classify_eurovoc`, `extract_temporal_data`, `generate_amendment_history`, `extract_sanctions`, `generate_similarity_index`). A one-law correction costs the full pipeline. Combined with W9 (3.3 GB snapshot) and the ~37-minute enrichment run, the cheapest possible fix-and-republish cycle is roughly an hour of wall time.

### W20 — Partial failure discards the whole run. **Severity: MEDIUM**

On any step failure the serial loop breaks (`run_all_integration.py:1310-1316`), every later step is drained as `not_reached`, and `restore_outputs()` rolls the entire tree back (`:1687-1692`). A failure at step 17 throws away 16 successful steps and ~35 minutes. Recovery requires re-running with `--no-restore-on-failure` and then `--resume-from`, and `--resume-from`'s precondition check deliberately ignores peep-file patterns (`_missing_resume_writes`, `:1155-1157`), so it cannot verify that the peep corpus is actually at the right stage. There is no per-step checkpoint that survives a rollback.

Answer to "what happens on partial failure": **the run is atomic all-or-nothing by default, with a manual, weakly-verified resume path.**

### W21 — The release contract is never exercised in CI. **Severity: MEDIUM**

`.github/workflows/validate.yml:166-167` runs only:
```yaml
- name: Dry-run release validate-only
  run: python3 scripts/run_all_integration.py --dry-run --release --validate-only
```
The individual gates do run for real in separate jobs, but `releaseOk`, `contentHash`, and the `releaseArtifacts.missing` gate are never computed on CI. Consistent with that, **`krr_outputs/reports/integration/release_manifest.json` does not exist in this tree** — only `latest_pipeline_manifest.json` from a plain enrichment run. There is no evidence the documented unified release command has ever completed on this corpus.

### W22 — Per-step timeout has thin margin on slower hardware. **Severity: LOW-MEDIUM**

`--per-script-timeout` defaults to 1800 s and applies to validators as well as steps (`run_all_integration.py:761-766`, used at `:1005`). Measured on the author's machine: `validate_seadusloome_sync.py` at `real 518.46` (`docs/RELEASE.md`), `extract_cross_references.py` at 429 s, `generate_amendment_history.py` at 336 s (`latest_pipeline_manifest.json`). A ministry VM 3× slower puts the Seadusloome gate at ~1,550 s — inside the cap, but only just, and a timeout is recorded as exit 124 and fails the release. The default deserves a documented headroom rationale or a higher value for validators.

### W23 — No documented runtime, cost, or hardware requirement anywhere. **Severity: MEDIUM**

`docs/RELEASE.md` describes what the release build does but never says how long it takes, how much RAM or disk it needs, or what a partial failure costs. The only numbers that exist are buried in `latest_pipeline_manifest.json`. Reconstructed from that file plus `docs/RELEASE.md`'s validator timings:

| Phase | Measured |
|---|---|
| Enrichment DAG (16 steps, 2026-06-02 run) | 36.6 min |
| `validate_seadusloome_sync.py` | 8.6 min |
| `shacl_validate_all.py --all` + `validate_all.py` | multi-minute, unrecorded |
| 3.3 GB snapshot copy + cleanup | unrecorded |
| Disk headroom needed | ~7 GB beyond a 2.4 GB checkout |

The 36.6 min figure predates two added DAG steps and so is a floor. A supplier cannot bid against this, and a ministry cannot size a runner.

---

## Improvement ideas

Ordered by value for a public-sector adopter.

1. **Publish a real provision-level release delta and make it a DAG step.**
   *What:* replace `emit_release_changes.py`'s deprecated-vs-live diff with a two-snapshot diff against the previous tagged release, emitting per-act and per-provision change classes (added, removed, text changed with old/new `versionRedactionId`, validity window changed) with **no cap on the listed IRIs** — page it or ship it as JSONL if it is large. Add it as a post-`build_release_artifacts` step and a `dcat:distribution` on `metadata.jsonld`. Keep a small `changes-latest.jsonld` pointer alongside the versioned file so a subscriber can poll one URL.
   *Why it matters:* "what changed in Estonian law since the release my system ingested" is the single question a ministry integration exists to answer. Today the answer is a capped list of 50 IRIs from a diff that does not compare releases, published under the wrong version number. This is the highest-value gap in the whole release surface.
   *Effort:* M. *Impact:* H.
   *Files:* `src/estleg/emit_release_changes.py`, `src/estleg/run_all_integration.py` (STEPS), `metadata.jsonld`, `docs/RELEASE.md`.

2. **Add one `release-assets` build step that packages, checksums, and manifests every shipped asset.**
   *What:* a `src/estleg/build_release_assets.py` that gzips the four combined dumps, regenerates `combined_ontology.{nt,nq,ttl}` and `estleg_all.nq.gz`, builds the retrieval `chunks.jsonl`, writes a `SHA256SUMS` file plus per-asset `dcat:byteSize` / `spdx:checksum` back into `metadata.jsonld`, and extends `RELEASE_ARTIFACTS` so `releaseOk` covers them. Then `run_all_integration.py --release` really is the single release command.
   *Why it matters:* fixes W2, W4, W17 and half of W6 in one place. A public body must be able to verify a download and reproduce it; right now the primary consumer artifacts are hand-made by an unrecorded process and have no published hash. It also removes the "release assets are octet-stream / stale" class of consumer bug.
   *Effort:* M. *Impact:* H.
   *Files:* new `src/estleg/build_release_assets.py`, `src/estleg/run_all_integration.py:435-450`, `metadata.jsonld`, `docs/RELEASE.md`.

3. **Make the named-graph dump fail loudly on a missing corpus, and fix the two dead slot sources.**
   *What:* have `write_full_dump()` return the missing slot names and make `main()` exit non-zero unless `--allow-partial` is passed; distinguish "file absent" from "LFS pointer" in the message and reuse the `_require_combined_build_sources` wording. Then either generate `regulations/REGULATIONS_COMBINED.jsonld` and `riigikohus/riigikohus_combined.jsonld` in the DAG, or point the slots at sources that exist.
   *Why it matters:* today the documented SPARQL quickstart hands a ministry an endpoint that is silently missing municipal regulations and Supreme Court decisions. Wrong answers with a zero exit code are the worst possible failure mode for a legal knowledge graph.
   *Effort:* S (the guard) + M (the two missing aggregates). *Impact:* H.
   *Files:* `src/estleg/serialize_named_graphs.py:63-81,111-133,262-286`, `README.md:111-137`.

4. **Remove the wall-clock date from `INDEX.json` and make the index a pure function of the corpus.**
   *What:* replace `_dt.date.today().isoformat()` with `BUILD_EVALUATION_DATE` (or drop the field and let `contentHash` be the change signal), move `registry_exceptions` and the multipart annotations into a committed `data/` source file instead of reading them back out of the derived `INDEX.json`, and give `generate_index()` a `krr_dir` parameter.
   *Why it matters:* makes `contentHash` mean what `docs/RELEASE.md` says it means, satisfies AGENTS.md's no-timestamp-churn rule, and makes "delete the derived artifact and rebuild" a safe operator action. This is the cheapest item on the list and it unblocks any reproducibility claim.
   *Effort:* S. *Impact:* H.
   *Files:* `src/estleg/fix_all_issues.py:720-800`, new `data/index_registry_exceptions.json`, `tests/`.

5. **Put provenance on every retrieval chunk and publish the projection.**
   *What:* add `ontology_version`, `evaluation_date` (the anchor for `in_force`), `act_iri`, `kehtiv`, `language`, and a stable `chunk_id` to `build_chunk_record()`; make `rt_url` provision-level where the source supports an anchor; add an optional `--max-chars` split that emits `part_index` / `part_count` for very long §. Then ship `chunks.jsonl.gz` as a release asset with a `dcat:distribution`, and regenerate at v1.0.0.
   *Why it matters:* an AI answer from Bürokratt has to be auditable months later from the chunk alone. `in_force: true` with no as-of date is a legally unsafe assertion. And a feed that is git-ignored, seven weeks stale, and stamped 0.11.0 is not a feed a state assistant can adopt.
   *Effort:* S (fields) + M (publishing + the DAG-ordering question in `TODO(#523)`). *Impact:* H.
   *Files:* `src/estleg/generate_retrieval_projection.py:365-395,849-1028`, `krr_outputs/retrieval/README.md`, `metadata.jsonld`, `tests/test_run_all_integration.py` (the strictly-last invariant).

6. **Stamp the version on non-flagship combined heads.**
   *What:* add `owl:versionInfo` and `owl:versionIRI` to the backfill list in `apply_inband_dataset_fields()` and overwrite them unconditionally (they are build-derived, not authored), then re-run `stamp_combined_dataset_heads.py` and add it as a DAG step after `link_curia_eu_legislation.py`. Add a test asserting every entry in `COMBINED_JSONLD_TARGETS` carries `ONTOLOGY_VERSION`.
   *Why it matters:* three shipped v1.0.0 release assets currently carry no version at all, and the flagship graph embeds a node claiming 0.11.0. A consumer who pins a version cannot verify what they loaded. One-line fix, whole class of drift closed.
   *Effort:* S. *Impact:* M-H.
   *Files:* `src/estleg/estleg_common.py:900-917`, `src/estleg/run_all_integration.py` (STEPS), `tests/`.

7. **Document the operating envelope, and record it from the manifest.**
   *What:* add a "Runtime, memory, and disk" section to `docs/RELEASE.md` with the measured per-step timings, peak RSS for the combined build and for `.nt`/`.nq` serialization, the ~7 GB disk headroom, and the recovery procedure after a partial failure. Have `--release` emit a `resources` block (wall time per step, peak RSS via `resource.getrusage`, free disk before/after) into `release_manifest.json` so the numbers refresh themselves.
   *Why it matters:* "could RIK run this end to end, and what does it cost" is currently unanswerable from the documentation. A supplier cannot bid, and a ministry cannot size a runner or a maintenance window. The data already exists in the ledger; it is just not surfaced or explained.
   *Effort:* S. *Impact:* H (this is a procurement blocker, not a code defect).
   *Files:* `docs/RELEASE.md`, `src/estleg/run_all_integration.py:1195-1215,1476-1540`.

8. **Make the snapshot optional and cheap.**
   *What:* add `--snapshot {auto,copy,none}` defaulting to `auto`: skip the copy when `git status --porcelain krr_outputs/` is clean (git already is the rollback) and fall back to `copy` otherwise. Try `shutil.copytree(..., copy_function=os.link)` for a hardlink snapshot where the filesystem allows it.
   *Why it matters:* removes 3.3 GB of I/O and ~7 GB of disk requirement from every run, including `--resume-from` re-runs. On a shared CI runner or a modest ministry VM this is the difference between a build that fits and one that does not.
   *Effort:* S. *Impact:* M.
   *Files:* `src/estleg/run_all_integration.py:801-868,1628-1636`.

9. **Add a change-detected incremental mode.**
   *What:* record a per-input-file sha256 map in `release_manifest.json`, and give the enrichment steps a shared `--only-changed <manifest>` that processes just the peeps whose hash moved plus their declared dependents. Keep the full pass as the default and as the release gate.
   *Why it matters:* a single-law correction currently costs an hour. Monthly Riigi Teataja consolidation (the stated SLA) touches a small fraction of 1,122 acts, so incremental is the difference between a monthly refresh a ministry can operate and one it outsources. The `writes`/`reads` declarations needed to scope it already exist.
   *Effort:* L. *Impact:* M-H.
   *Files:* `src/estleg/run_all_integration.py`, each `src/estleg/{extract,classify,generate}_*.py`.

10. **Strengthen the DAG `reads` check so it can catch a missing ordering edge.**
    *What:* split `COMMITTED_INPUTS` into "pristine committed inputs" and "corpus targets that steps mutate". For a pattern in the second class, require that a step reading it either declares `depends_on` covering every prior writer or carries an explicit `"reads_pre_enrichment": true` opt-out. Add a test pinning `len(STEPS)` and cross-checking the `docs/RELEASE.md` step table against `STEPS`.
    *Why it matters:* the check currently passes trivially for the peep corpus, which is exactly where an ordering mistake would be silent and would produce a subtly wrong release. It would also have caught the three-step documentation drift.
    *Effort:* M. *Impact:* M.
    *Files:* `src/estleg/run_all_integration.py:118-134,486-568`, `docs/RELEASE.md:229-259`, `tests/test_run_all_integration.py`.

11. **Run the real release-validate gate in CI, not just its dry run.**
    *What:* on `main` pushes and the weekly schedule, run `python3 scripts/run_all_integration.py --release --validate-only` for real and upload `release_manifest.json` as a build artifact. Assert `releaseOk == true` and diff `contentHash` against the previous run to surface unexplained artifact drift.
    *Why it matters:* the release contract (`releaseOk`, `contentHash`, the missing-artifact gate) is the mechanism a ministry would trust, and it is currently never executed. Depends on idea 4 landing first, or the `contentHash` diff will fire every day on the date stamp alone.
    *Effort:* S. *Impact:* M.
    *Files:* `.github/workflows/validate.yml:150-170`.

12. **Pin the dependency set and sort Turtle output.**
    *What:* commit a `requirements.lock` / `constraints.txt` (or narrow `rdflib` to an exact version) and install from it in CI and in the documented setup; add deterministic ordering for the `ttl` path or stop committing `combined_ontology.ttl` and generate it on demand.
    *Why it matters:* "byte-identical on a fresh machine" cannot be claimed while the RDF serializer floats across a minor-version range. A public body reproducing a build to verify it needs the exact toolchain.
    *Effort:* S. *Impact:* M.
    *Files:* `pyproject.toml`, new `constraints.txt`, `.github/workflows/validate.yml`, `src/estleg/serialize_corpus.py:180-200`.

13. **Stream the `.nt`/`.nq` serialization and document its footprint.**
    *What:* write triples to a temp file line by line, then external-sort with `sort` (or `sorted()` over a chunked merge) instead of holding the whole document, the whole line list, and a second rdflib store in memory. For `nq`, append the graph term during writing rather than copying into a `Dataset`.
    *Why it matters:* drops the peak from 5–7 GB to roughly the graph size, which is what makes regenerating the bulk-load dumps possible on an 8 GB runner. Prerequisite for idea 2 running in CI.
    *Effort:* M. *Impact:* M.
    *Files:* `src/estleg/serialize_corpus.py:88-92,155-200`.

14. **Fix the tabular export's silent truncation and the two catalog defects.**
    *What:* add a `legal_text_truncated` boolean column (or a `--full-text` flag) to `serialize_tabular.py`; give Parquet real types for dates, booleans, and numeric sanction bounds. Separately, change the `changes-*.jsonld` `dcat:accessURL` from the relative string `krr_outputs/changes-0.11.0.jsonld` to an IRI, and either resolve LFS in the "complete dataset" archive or relabel that distribution so it does not promise data it delivers as pointer stubs.
    *Why it matters:* an analyst cannot tell which CSV cells are clipped, and a DCAT-AP harvester will reject a relative `accessURL`. The archive-of-pointers issue is the one most likely to produce a support ticket from a first-time public-sector consumer.
    *Effort:* S. *Impact:* M.
    *Files:* `src/estleg/serialize_tabular.py:47-48,172-176,275,629-640`, `metadata.jsonld`, `src/estleg/write_build_manifest.py:163`.

15. **Wire `write_build_manifest.py` into the release and add a dirty-tree check.**
    *What:* run it as the final `--release` step, have `git_sha()` append `-dirty` when `git status --porcelain` is non-empty, and derive `DATASET_CONTENT_SHA` from the tag being released rather than a hand-edited constant.
    *Why it matters:* `dataset_build_manifest.json` is the file that tells a consumer which tree they have; it currently attests `2026-06-01` for a corpus written `2026-08-19` and can attest a clean SHA for a modified tree.
    *Effort:* S. *Impact:* M.
    *Files:* `src/estleg/write_build_manifest.py:38,60-75`, `src/estleg/run_all_integration.py` (STEPS).

---

## Open questions

1. **Why do `combined_ontology.jsonld` and `combined_ontology.nt` disagree by 416,437 triples?** `README.md:77-83` publishes both numbers (2,247,778 vs 2,664,215) as facts about the same artifact. Is the `.nt` from an older build, or does the JSON-LD load lose triples that the `.nt` retains (`@list` handling, the `#519` type rollup, duplicate `@id` merging)? Whichever it is, a triplestore team and an rdflib team currently get different graphs from one release. This needs a definitive answer before idea 2.

2. **Was v1.0.0 ever cut with `run_all_integration.py --release`?** No `release_manifest.json` exists in the tree, CI only dry-runs the release path, and the four `.gz` release assets have no producer in the repo. Knowing the actual release procedure that was used would say how much of `docs/RELEASE.md` describes intent versus practice.

3. **What is the intended baseline for a release delta?** There is no previous-release snapshot in the repo to diff against. Should idea 1 diff against the previous git tag (requires fetching a 2.4 GB LFS tree at that tag), against a committed IRI-set digest per release, or against the prior release's `chunks.jsonl`? The cheapest durable option is probably a small committed `krr_outputs/release_iri_digest-<version>.json`, but that is a design call.

4. **Is `--parallel` worth keeping?** It is fully implemented, permanently rejected for the current DAG, and adds a non-trivial code path (`run_dag`'s parallel branch, `run_all_integration.py:1346-1436`) that cannot be exercised end to end. Splitting the peep corpus per step is the stated unblocker — is that actually on the roadmap, or should the runner be simplified to serial and the parallel branch removed?

5. **Should `krr_outputs/` be snapshot at all, given git?** Idea 8 assumes git is an adequate rollback for a clean tree. That holds only if every mutated path is tracked and LFS-materialised. Are there untracked-but-needed files under `krr_outputs/` (caches, `data/ehak/`) that a git-based restore would miss?

6. **What is the intended relationship between `BUILD_EVALUATION_DATE` (2026-06-01) and the actual corpus snapshot (`estleg:kehtiv` = 2026-05-24)?** The retrieval projection's `in_force` flag is computed against the former while the text reflects the latter. For a one-week gap this is probably immaterial, but the rule should be stated, because it is the assumption an audited AI answer rests on.

7. **Who is expected to run the retrieval projection and the `.gz` packaging — the maintainer, CI, or the adopting ministry?** The answer determines whether ideas 2 and 5 need to fit in a GitHub-hosted runner's disk and memory budget, or whether a self-hosted release runner is assumed.
