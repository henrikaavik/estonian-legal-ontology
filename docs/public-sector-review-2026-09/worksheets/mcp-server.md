<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# estleg-mcp review — public-sector usefulness lens

## Scope & method

Read in full: `mcp_server/estleg_mcp/data.py` (2,054 lines), `mcp_server/estleg_mcp/server.py`
(1,022 lines, 20 registered tools — README/HANDOFF still say 14), all six test modules,
`mcp_server/docker/{Dockerfile,entrypoint.sh}`, `mcp_server/pyproject.toml`,
`mcp_server/.dockerignore`, `mcp_server/README.md`, `mcp_server/HANDOFF.md`, plus
`docs/ARCHITECTURE.md` "Query paths" and `.github/workflows/validate.yml`.

Beyond reading, I executed against the real corpus at `main` (working tree clean):

- `python3 -m pytest -q mcp_server` → **19 failed, 69 passed, 1 skipped** (8.8s).
- `python3 -m ruff check mcp_server/` (ruff 0.16.3) → 7 findings (5 RUF023, 2 FURB188).
- A probe script calling all 20 tools end-to-end and recording result shape + latency.
- Corpus statistics, `rt_url` correctness across all 1,120 laws, provision-length
  distribution, and RSS/cold-start measurement of every `@lru_cache` index.

The corpus on disk: `krr_outputs/` is 3.3 GB (regulations 14,874 files / 609 MB;
provision_versions 4,422 files / 407 MB; riigikohus 203 MB; curia 74 MB; concepts 14 MB;
1,195 root peeps). Full working tree 16 GB, `.git` 12 GB.

---

## Tool inventory

Status column reflects **observed behaviour against the corpus on `main` today**, not the
documented contract. "BROKEN" means the tool returns an empty list or a "not found" note for
every law, silently.

| Tool | Public-sector question | Citation quality | Status / gaps |
|---|---|---|---|
| `search_laws` | "What is the act called?" | riigiteataja.ee act URL; good | Works. Titles come back as ET+EN concatenated ("Karistusregistri seadus Criminal Records Database Act") — no language selection |
| `get_law` | "Overview of act X" | rt_url + `ontology_version` + `consolidated_as_of` | **Partly broken**: `num_provisions` is always 0. For KarS/VÕS `rt_url` is a **Wikidata** IRI, not riigiteataja |
| `get_provision` | "What does § 13 say?" | act-level rt_url only, never a §-anchor | **BROKEN** — returns "No § matching '13' found" for every law |
| `provision_history` | "How has § X changed?" | none (no rt_url on rows) | **BROKEN** — always `[]` |
| `who_references` | "What cites § X?" (impact analysis) | rt_url per referencing act | **BROKEN** — always `[]` |
| `references_of` | "What does § X cite?" | rt_url per target | **BROKEN** — always `[]` |
| `drafts_affecting_law` | "Which bills would change act X?" | eelnoud.valitsus.ee EIS link; good | Works (does not read provision nodes) |
| `court_decisions_for_law` | "Which Riigikohus cases interpret X?" | riigikohus.ee decisionLink; good | **BROKEN** — always `[]`; edges exist (303 on KarS) but are unreachable |
| `sanctions_for_law` | "What penalties does X impose?" | act rt_url (inherits the Wikidata bug for KarS) | Works. Provision field is a bare IRI, not a § label |
| `competent_authority_for_law` | "Who enforces X?" | none (institution name only, no URL) | **BROKEN** — always `[]`; 77 edges exist on KarS |
| `transposition` | "Which act transposes directive Y?" | EUR-Lex CELEX URL; good | Works. No transposition-**gap** direction (untransposed directives) |
| `eu_case_law_for_directive` | "Which CURIA rulings interpret Y?" | EUR-Lex/CURIA link | Works. Honest note that it is a CELEX substring match, not a graph walk (#418) |
| `define_term` | "What does term T mean in law?" | **none** — no source act, no rt_url | Works mechanically. Corpus is dominated by municipal-regulation terms; the README's own example `define_term("elatis")` returns `[]` |
| `harmonisation_for_directive` | "Who else transposed Y?" | **none** — no EUR-Lex URL on rows | Works |
| `layers_available` | "What does this server actually read?" | n/a | Works; genuinely good transparency |
| `regulations_for_law` | "Which määrused sit under act X?" | regulation rt_url + citation text; good | Works. 4.4s warm, and forces a 14,874-file scan |
| `get_regulation` | "Overview of määrus Z" | rt_url + parent-statute rt_url; good | Works except `num_provisions` is always 0 |
| `regulations_by_issuer` | "What has ministry M issued?" | rt_url; good | Works |
| `laws_for_subject` | "Which laws are about topic T?" | **none** — no rt_url on rows | Works but scans all 1,120 law graphs per call |
| `amendment_history` | "What amendments has X received?" | **none** — no rt_url | Works, but on KarS every `amendment_date`, `entry_into_force` and `amends` field came back empty |

**Missing tools that map to real public-sector questions**, none of which exist today:
diff between two dates ("what changed in act X since D" — the version layer supports it),
reverse KOV lookup ("which municipal regulations rely on § X" — `regulations_for_law`
resolves only act-level `issuedUnder`), transposition-gap listing (directives with no
Estonian measure), plain-Estonian explanation of a §, full-text search over provision text,
and a "cite this answer" tool returning a stable §-level permalink.

---

## Strengths

1. **Clean layering.** `data.py` has no MCP imports (`estleg_mcp/__init__.py:5-9`), so the
   whole query surface is testable without a transport. This is what let me diagnose the
   breakage below in minutes.

2. **Honest degradation is designed in, not accidental.** `_load_json` returns `None` on any
   `OSError`/`ValueError` (`data.py:115-127`) specifically so Git-LFS pointer files do not
   crash the server; `_graph_of` (`data.py:130-137`) filters non-dict nodes.

3. **`layers_available()` (`data.py:1743-1910`, `server.py:905-919`) is unusually good
   practice.** It enumerates 17 sidecars with `wired`/`loadable`/`excluded` status *and* a
   live on-disk `present` check. A public body evaluating the service can ask the service
   itself what it does not read. Very few RAG-style systems expose their own dead layers.

4. **Point-in-time logic is correct and refuses to guess.** `version_in_force_on`
   (`data.py:1312-1333`) treats `valid_to` as inclusive and open-ended-null as current;
   `_law_as_of` (`server.py:134-167`) and `_provision_as_of` (`server.py:227-266`) return a
   `{note}` naming the actual coverage span rather than silently falling back to current
   text. `currently_in_force` is explicitly documented (`server.py:262-265`) as "still live
   today", not "in force on the queried date" — exactly the distinction that misleads.

5. **Constant-time bearer comparison** via `hmac.compare_digest` (`server.py:990`), and an
   unauthenticated `/healthz` carved out before the gate (`server.py:985-986`).

6. **The DNS-rebinding trade-off is documented rather than hidden** (`server.py:922-950`,
   README:106-109), including *why* protection is off by default behind a proxy.

7. **Empty-vs-error contract is tested corpus-free.** `tests/test_empty_and_search.py:19-51`
   pins `[{note}]` for unknown law and `[]` for known-law-zero-hits across nine tools.

8. **Overflow is signalled, not silent.** `_capped` (`server.py:636-658`) appends
   `{note, overflow, total_available}` so a client knows a regulation list was cut.

---

## Weaknesses / risks

### W1 — CRITICAL: eight tools are silently broken on `main`; provision typing migrated away underneath the server

`data.py:758-759`:

```python
def _is_provision(node: Node) -> bool:
    return any(t.startswith("estleg:LegalProvision_") for t in _types_of(node))
```

Commit `c5625a748b` "Type provisions as LegalProvision and drop per-document classes"
(2026-08-19 04:23) retyped every provision to the **bare** `estleg:LegalProvision`.
`data.py` was last touched at 2026-08-19 00:20 — four hours earlier — and never updated.
`"estleg:LegalProvision"` does not start with `"estleg:LegalProvision_"`, so
`provision_nodes()` returns `[]` for every law in the corpus.

Measured: in a random sample of 40 laws, **0 have a subclass type**, 28 have the bare type,
12 are single-node ratification acts with no provisions at all. KarS carries 532
`estleg:LegalProvision` nodes that the server cannot see.

Downstream, all of these return empty or "not found" for every law: `get_provision`,
`provision_history`, `who_references`, `references_of`, `court_decisions_for_law`,
`competent_authority_for_law`, and `get_law`'s `num_provisions`. The underlying edges are
intact — KarS provisions carry 303 `interpretedBy`, 116 `referencedBy`, 77
`competentAuthority`, 61 `references`. Nothing is missing from the data; only the type
predicate is stale.

The failure mode is the worst kind for a public body: no error, no warning, just
`[]`. And the README (line 152-158) pre-emptively explains empty lists as "domain
sparsity, not a tool bug" — so a ministry evaluating the service would read the empty
result as *expected corpus behaviour* and conclude Estonian law has no cross-references
and no Supreme Court interpretation.

The 19 failing tests in `mcp_server/tests/` all trace to this. CI job `mcp-server`
(`.github/workflows/validate.yml:123-145`) does run `pytest -q mcp_server` on a plain
checkout, so **`main` is red**, or the corpus regeneration was merged without it passing.

Same migration also broke `data.py:1481`:

```python
if any(t.startswith("estleg:Regulation_") for t in types):
    num_provisions += 1
```

Regulations now use `estleg:LegalProvision` too, so `get_regulation(...)["num_provisions"]`
is always `0` (verified on `t1057801`, which has 4 provisions).

Concrete self-contradiction a lawmaker would see today:

```json
{"title": "Töölepingu seadus Employment Contracts Act", "abbrev": "TLS",
 "num_provisions": 0, "as_of": "2020-01-01", "num_provisions_as_of": 149}
```

### W2 — HIGH: `rt_url` can return a Wikidata IRI under a field named `rt_url`

`data.py:809-823`:

```python
src = _id_of(node.get("dcterms:source")) or _id_of(node.get("owl:sameAs"))
```

Act roots lost `dcterms:source` (commit `43a67b5d55` "Stop identifying acts with dated Riigi
Teataja XML files"). Where an act still has an `owl:sameAs`, the fallback fires. Across all
1,120 laws:

| rt_url resolves to | count |
|---|---|
| riigiteataja.ee | 984 |
| empty string | 134 |
| **wikidata.org** | **2** |

Only two — but they are **Karistusseadustik and Võlaõigusseadus**, the Penal Code and the
Law of Obligations Act: the two most-queried statutes in Estonian law and the two headline
examples in the README. `get_law("KarS")` today returns
`"rt_url": "http://www.wikidata.org/entity/Q2352833"`, and `sanctions_for_law("KarS")`
stamps that same Wikidata URL onto every penalty row.

A field named `rt_url`, documented as "the canonical riigiteataja.ee URL", returning a
third-party wiki IRI is worse than returning nothing: an LLM will present it as the official
source. This directly contradicts README:9-13 ("never guesses a citation"). The 134 empty
cases (12% of the corpus) are the softer half of the same claim.

### W3 — HIGH: truncation is silent and `full_text` is ignored on the historical path

`_MAX_LEGAL_TEXT = 2000` (`server.py:29`). Measured over 1,526 provisions from 60 random
laws: **9.0% exceed 2,000 characters** (p50 500, p90 1,878, p99 5,733, max 12,842).

The returned object carries no `truncated` flag and no original length — only a trailing
`…` inside the text (`server.py:32-35`). A model summarising a cut-off § can state a rule
whose exception lived in the discarded remainder. For legal advice inside a ministry that is
a material correctness risk, not a formatting one.

Worse, the escape hatch does not work where it matters. `server.py:220-224`:

```python
if as_of is None:
    raw = data.clean_display(data._text(node.get("estleg:legalText")))
    result["legal_text"] = raw if full_text else _truncate(raw)
    return result
return _provision_as_of(rec, node, result, as_of)
```

`full_text` is never passed into `_provision_as_of`, which unconditionally truncates at
`server.py:257`. Verified: a 5,000-character historical redaction requested with
`full_text=True` comes back at exactly 2,000 characters. `provision_history` (`server.py:627`)
has no `full_text` parameter at all. And `full_text` appears nowhere in README or HANDOFF, so
no client knows to ask for it.

### W4 — HIGH: four tools return no citation at all

The README's central promise is "every result that maps to a source carries its canonical
URL". These do not:

- `define_term` (`data.py:684-690`) → `{id, label, definition}`. No source act, no URL —
  even though the concept IRI encodes its origin document
  (`estleg:Concept_abja_valla_..._t1003627_tee_kaitsevoond`).
- `laws_for_subject` (`data.py:653-660`) → `{name, title, abbrev, subjects}`, no rt_url,
  though `rt_url_for_slug` is one call away.
- `harmonisation_for_directive` (`data.py:1729-1737`) → no `eurlex_url`, though
  `eurlex_url()` sits in the same module.
- `amendment_history` (`data.py:1126-1134`) → no rt_url, and on KarS every
  `amendment_date`, `entry_into_force` and `amends` field came back empty.

A definition with no source is precisely the ungrounded output an AI legal assistant must
not produce.

### W5 — HIGH (operate): no audit logging, no rate limiting, no per-tool telemetry

`grep -rn "logging\|logger\|audit\|rate_limit" mcp_server/estleg_mcp/ mcp_server/docker/`
returns nothing. There is no record of who asked what.

For Bürokratt or a ministry deployment this is disqualifying before any functional review.
A public body needs, at minimum: which tool was called, with which arguments, by which
identity, at what time, and what the corpus snapshot was. Estonian public-sector practice
(and the AVTS/ISKE expectations around state information systems) assumes queryable audit
trails. There is also no rate limiting, so a single misbehaving agent loop can pin the
process — and each cold tool call can trigger a multi-hundred-megabyte index build (W7).

### W6 — HIGH (operate): single shared bearer token, defaulting to open

`server.py:979-994`. One process-wide secret; every caller is the same principal.
No TARA/eIDAS identity, no OAuth, no X-tee. Consequences: rotation means redeploying and
reconfiguring every client at once; per-ministry access cannot be granted or revoked; and
audit (W5) could not attribute a query even if it existed.

`if token:` at `server.py:980` means an **unset `ESTLEG_TOKEN` silently serves the endpoint
wide open**. The default fails open, not closed. Combined with
`TransportSecuritySettings(enable_dns_rebinding_protection=False)` at `server.py:950`
(also the default), a misconfigured deploy is a fully anonymous public MCP endpoint with no
log of who used it.

### W7 — MEDIUM/HIGH (operate): ~933 MB resident, unbounded, never released

Measured RSS on a warm page cache, building each `@lru_cache` index in turn:

| After building | RSS | wall |
|---|---|---|
| baseline | 18 MB | — |
| `_court_decision_index()` (12,104 decisions, `data.py:994`) | 602 MB | 0.8s |
| `_draft_index()` (22,832 drafts, `data.py:1063`) | 672 MB | 0.1s |
| `_curia_decisions()` (22,290, `data.py:1924`) | 834 MB | 0.2s |
| `overlay_graph("concepts")` (12,835, `data.py:1704`) | 868 MB | 0.1s |
| `_regulation_records()` (14,871 files, `data.py:1508`) | 913 MB | 3.0s |
| `_regulations_by_law()` (`data.py:1587`) | 933 MB | 1.0s |

These are `functools.lru_cache(maxsize=1)` — no eviction, no TTL, no release. The first
`regulations_for_law` call parses all 14,874 regulation files (609 MB of JSON). Those
timings are with the OS page cache already warm from earlier runs; on a cold container
volume the first call will be far slower, and the `Dockerfile:39` `HEALTHCHECK` has only
`--start-period=30s`, which a first-boot `git clone` of a multi-gigabyte repo
(`entrypoint.sh:21`) will not finish inside — the container can be killed and restarted
mid-clone in a loop.

Also `laws_for_subject` (`data.py:640-663`) loads **every** law graph on every call with no
caching, and `_law_map_prefix_to_slug` (`data.py:1539`) does the same once.

### W8 — MEDIUM: no reproducibility or pinning of the corpus snapshot

`entrypoint.sh:15-22` always does `fetch --depth 1` + `reset --hard origin/$BRANCH`. There
is no way to pin a release tag, so the same question can get different answers on different
days with no way to reconstruct what the service said last week. `get_law` does return
`ontology_version` (`server.py:123`, from `metadata.jsonld`), which is good, but it is the
only tool that does, and `consolidated_as_of` came back `""` for KarS. For a body that must
justify a decision after the fact, "which version of the law did the assistant read" must be
answerable for every tool, not one.

Repeated shallow `fetch`/`reset` on a persistent volume also accumulates objects with no
`git gc`. README:88 says the corpus is "~1.5 GB"; `krr_outputs/` alone is **3.3 GB** and the
tree is 16 GB. That understates the `/data` volume a deployer must provision by roughly 2x.

### W9 — MEDIUM: prompt-injection surface is unmarked

Corpus text flows verbatim into model context with no provenance framing: `legal_text` and
`summary` (`server.py:215-222`), concept `definition` (`data.py:688`), draft titles
(`server.py:427`), court labels, regulation titles. The eelnõud subcorpus is 22,832
ministry-submitted drafts and the KOV tree is 11,060 municipal files — many upstream writers,
none of them the operator. A crafted sentence inside a draft explanatory memorandum reaches
the model as ordinary tool output. Nothing marks it as untrusted data rather than
instruction. This is not hypothetical for a system meant to ingest *pending* legislation.

Personal data is handled better than I expected: `court_decisions_for_law`
(`server.py:472-478`) returns only `case_number`, `label` and `decision_link`, even though
the underlying nodes carry `estleg:judge`, `estleg:legalText` and `estleg:summary`. That is
a good implicit minimisation. It is not documented as a deliberate choice anywhere, so
nothing stops a future tool from returning decision bodies.

### W10 — MEDIUM: English-only tool descriptions

Every docstring in `server.py` is English, including the example questions that the model
reads when choosing a tool (`server.py:9-10` says so explicitly). Estonian law references
inside them are parenthetical glosses. A Bürokratt-style Estonian-first assistant would be
selecting tools from English descriptions to answer Estonian questions — a measurable
routing-accuracy loss, and an odd look for a state service.

### W11 — LOW/MEDIUM: ruff config gap (as flagged in the brief — confirmed)

Root `pyproject.toml:48-51` pins `[tool.ruff.lint] select = ["E4","E7","E9","F"]` with the
comment "Ruff 0.16 broadened implicit defaults; this repo is not on that ruleset".
`mcp_server/pyproject.toml:40-42` has `[tool.ruff]` with only `line-length` and
`target-version` and **no `[tool.ruff.lint] select`**. Ruff resolves config hierarchically
per file, so `mcp_server/` files take the nested config and fall through to ruff 0.16's
broadened defaults. Hence the 7 findings (5 RUF023 unsorted `__slots__`, 2 FURB188
`removeprefix`/`removesuffix`) that do not appear elsewhere in the repo. CI
(`validate.yml:100-103`) explicitly relies on this hierarchical behaviour, so as soon as CI
picks up ruff ≥0.16 the lint job fails on style rules the rest of the repo does not enforce.
Adding the same three-line `[tool.ruff.lint] select` block to `mcp_server/pyproject.toml`
closes it.

### W12 — LOW: assorted correctness and shape issues

- `_strip_map_suffix` (`data.py:1457`) matches `_Map_\d+`, but commit `2676a1f81b` dropped
  the year: act IRIs are now `estleg:KARIST_2_Map` and `issuedUnder` targets are
  `estleg:POHIKO_Map`. The regex no longer strips anything. It happens to still resolve
  because `_law_map_prefix_to_slug` builds its keys through the same unstripped path, so
  both sides agree by accident — but the `resolve_law(prefix)` fallback at `data.py:1583`
  now receives `"KOKS_Map"` and cannot match. Fragile.
- Bilingual titles are glued: `_text` joins a list (`data.py:166-168`), so `dcterms:title`
  with ET and EN values yields `"Karistusregistri seadus Criminal Records Database Act"`.
  No language parameter exists on any tool.
- `search_laws` reads `data._text` (a private helper) directly from `server.py:79` while
  every sibling field uses a public accessor.
- Note-objects are mixed into typed result lists (`server.py:50`, `648-657`). Convenient for
  an LLM, hostile to any typed/OpenAPI consumer, which must treat `{note}` as a member of
  every row type.
- `.dockerignore:6` excludes `tests/`, so the built image cannot self-verify against the
  corpus it just cloned — which is exactly the check that would have caught W1 at boot.
- `Dockerfile` has no `USER` directive; the server runs as root.
- README/HANDOFF both say "14 tools"; `server.py` registers **20**. `define_term`,
  `laws_for_subject`, `amendment_history`, `eu_case_law_for_directive`,
  `harmonisation_for_directive` and `layers_available` are in the README table but not the
  HANDOFF count, and `laws_for_subject` is in neither.

---

## Improvement ideas

**1. Accept both provision typings and add a startup sanity check.**
*What:* change `_is_provision` (`data.py:758-759`) to
`any(t == "estleg:LegalProvision" or t.startswith("estleg:LegalProvision_") for t in ...)`,
and the same at `data.py:1481` for regulations. Then add a boot-time assertion that a known
act (KarS) yields >0 provisions, logged loudly and surfaced in `layers_available()`.
*Why:* restores eight dead tools and stops the next corpus retype from silently emptying the
service. A public body cannot audit an answer that is confidently empty.
*Effort:* S (two predicates) + S (the guard). *Impact:* H.
*Files:* `mcp_server/estleg_mcp/data.py`, `mcp_server/estleg_mcp/server.py`.

**2. Make `rt_url` refuse to return a non-riigiteataja URL.**
*What:* in `rt_url` (`data.py:809-823`) only accept `dcterms:source`, and accept `owl:sameAs`
only when its host is `riigiteataja.ee`. Return `""` otherwise, and add a separate
`external_ids` field for Wikidata/ELI. Add a corpus test asserting no law's `rt_url` is
off-domain.
*Why:* the whole value proposition is verifiable citation. One wrong URL under an official
label costs more trust than 134 empty ones. Fixing the 134 empties (act roots that lost
`dcterms:source`) is the follow-on producer-side ticket.
*Effort:* S for the guard, M with the producer-side backfill. *Impact:* H.
*Files:* `mcp_server/estleg_mcp/data.py`, `mcp_server/tests/test_data.py`.

**3. Make truncation explicit and honour `full_text` everywhere.**
*What:* return `{legal_text, truncated: bool, full_length: int, full_text_available: true}`;
thread `full_text` through `_provision_as_of` (`server.py:227-266`) and add it to
`provision_history`; document it in the README tool table.
*Why:* 9% of provisions are cut today with no marker. A ministry drafting note built on a
silently truncated § is a defect that surfaces only in review. An explicit flag lets the
client re-fetch or cite the riigiteataja text instead.
*Effort:* S. *Impact:* H.
*Files:* `mcp_server/estleg_mcp/server.py`, `mcp_server/README.md`,
`mcp_server/tests/test_tool_contracts.py`.

**4. Put a citation on every row.**
*What:* add `rt_url` to `laws_for_subject` and `amendment_history` (via `rt_url_for_slug`),
`eurlex_url` to `harmonisation_for_directive`, and `source_act` + `rt_url` to `define_term`
by parsing the source document out of the concept IRI. Add one test asserting every list
tool emits at least one citation-bearing key.
*Why:* closes the gap between README:9-13 and behaviour. An unsourced definition is the
single most dangerous output shape for a legal assistant.
*Effort:* M. *Impact:* H.
*Files:* `mcp_server/estleg_mcp/data.py`, `mcp_server/tests/test_tool_contracts.py`.

**5. Structured audit logging with a corpus-snapshot stamp on every response.**
*What:* one JSON line per tool call — timestamp, tool, arguments, caller identity, result
count, latency, corpus commit SHA. Add `snapshot: {ontology_version, corpus_commit, as_of}`
to every tool's return envelope, not just `get_law`. Read the SHA once at boot via
`git -C $ESTLEG_CORPUS rev-parse HEAD`.
*Why:* this is the entry ticket for any Estonian public-sector deployment, and it makes
answers reconstructible. "Which version of the law did the assistant read on 3 September"
must have an answer.
*Effort:* M. *Impact:* H.
*Files:* `mcp_server/estleg_mcp/server.py`, `mcp_server/estleg_mcp/data.py`,
`mcp_server/docker/entrypoint.sh`.

**6. Fail closed, and support per-consumer credentials.**
*What:* require `ESTLEG_TOKEN` (or an explicit `ESTLEG_ALLOW_ANONYMOUS=1`) when
`ESTLEG_TRANSPORT=http`; refuse to start otherwise. Accept a map of token→consumer-name so
audit can attribute calls and one ministry's access can be revoked alone. Leave TARA/OAuth
as a later step behind the same seam.
*Why:* today an unset env var silently publishes an anonymous endpoint. Named consumers are
the minimum precondition for both audit (idea 5) and rate limiting.
*Effort:* S for fail-closed, M for the token map. *Impact:* H.
*Files:* `mcp_server/estleg_mcp/server.py`, `mcp_server/README.md`,
`mcp_server/tests/test_transport.py`.

**7. Replace the in-memory regulation/court/curia scans with an on-disk index.**
*What:* build a SQLite index (regulation → issuer/parent-law/rt_url; decision IRI → row;
CELEX → decision rows) at image build or first boot, and query it instead of holding 933 MB
of parsed JSON. Keep the JSON files as the source of record.
*Why:* drops the container from ~1 GB to a small constant, removes the multi-second first
call, and makes cold-start predictable enough to size a health check. It also unblocks
full-text search (idea 9) for free.
*Effort:* L. *Impact:* H.
*Files:* `mcp_server/estleg_mcp/data.py`, `mcp_server/docker/entrypoint.sh`,
`mcp_server/docker/Dockerfile`.

**8. Pin the corpus to a release tag and separate the clone from the health check.**
*What:* add `ESTLEG_CORPUS_TAG` honoured by `entrypoint.sh`, defaulting to the latest
release rather than `main` HEAD. Do the clone in a pre-start step (or serve `/healthz` as
"warming" until `INDEX.json` is present) and raise `--start-period` to match a real
multi-gigabyte clone. Correct README:88 from "~1.5 GB" to the measured 3.3 GB.
*Why:* reproducible answers are a legal requirement, not a nicety, and the current 30s start
period can restart-loop a first boot.
*Effort:* S. *Impact:* M.
*Files:* `mcp_server/docker/entrypoint.sh`, `mcp_server/docker/Dockerfile`,
`mcp_server/README.md`.

**9. Add the four missing high-value tools.**
*What:* `what_changed(law, since, until)` (diff over `provision_versions`, which already has
the data); `transposition_gaps(policy_area)` (directives with no matched Estonian measure —
`transposition_mapping.json` supports the anti-join); `kov_regulations_citing(law,
paragraph)` (§-level reverse lookup over `estleg:implementsCitation`, today only act-level);
`explain_provision(law, paragraph, language="et")` returning summary + defined terms +
sanctions + interpreting cases in one call.
*Why:* these are the four questions a Riigikogu drafter or ministry lawyer actually asks, and
three of them need no new data — only a new join. `what_changed` in particular is the single
highest-value tool the corpus can support and does not.
*Effort:* M each. *Impact:* H.
*Files:* `mcp_server/estleg_mcp/data.py`, `mcp_server/estleg_mcp/server.py`.

**10. Bilingual tool descriptions and a `language` parameter.**
*What:* Estonian-first docstrings with English after, and a `language: "et"|"en"` parameter
that selects from bilingual `dcterms:title` instead of concatenating (`data.py:166-168`).
*Why:* Bürokratt and any ministry deployment operate in Estonian. Today the model picks tools
from English prose and gets back titles with two languages glued together.
*Effort:* M. *Impact:* M.
*Files:* `mcp_server/estleg_mcp/server.py`, `mcp_server/estleg_mcp/data.py`.

**11. Add a REST/OpenAPI facade over the same data layer.**
*What:* a thin FastAPI/Starlette router calling `data.py` directly, mounted beside `/mcp` on
the existing app (`server.py:953-995`), with Pydantic response models. Move `{note}` out of
the result arrays into an envelope field so rows are uniformly typed.
*Why:* `data.py` has no MCP imports, so this is genuinely cheap. It is what makes the service
consumable by non-MCP clients — an X-tee adapter, a Riigikogu drafting tool, a plain
dashboard — none of which speak MCP. It also produces the machine-readable service
description an X-tee registration needs.
*Effort:* M. *Impact:* H.
*Files:* new `mcp_server/estleg_mcp/rest.py`, `mcp_server/estleg_mcp/server.py`,
`mcp_server/pyproject.toml`.

**12. Mark corpus text as untrusted data, and document the PII minimisation.**
*What:* wrap returned free text in an explicit `{"source": "...", "content": "..."}` shape
whose field names signal quotation rather than instruction, and state in the README that
court-decision bodies, `estleg:judge` and decision summaries are deliberately never returned.
*Why:* the eelnõud and KOV subcorpora have thousands of upstream writers who are not the
operator. The minimisation already implemented (`server.py:472-478`) is good and should be a
stated contract so a future tool does not quietly undo it.
*Effort:* S for the doc, M for the envelope. *Impact:* M.
*Files:* `mcp_server/estleg_mcp/server.py`, `mcp_server/README.md`.

**13. Close the ruff config gap and re-sync the docs.**
*What:* add `[tool.ruff.lint] select = ["E4","E7","E9","F"]` to `mcp_server/pyproject.toml`
to match root `pyproject.toml:48-51`. Update README and HANDOFF from "14 tools" to 20 and add
the missing rows.
*Why:* CI (`validate.yml:100-103`) relies on hierarchical resolution and will fail on style
rules the rest of the repo does not enforce. Doc drift on tool count is what a public-sector
evaluator reads first.
*Effort:* S. *Impact:* M.
*Files:* `mcp_server/pyproject.toml`, `mcp_server/README.md`, `mcp_server/HANDOFF.md`.

---

## Open questions

1. **Is `main` currently red in CI, or was the corpus regeneration merged without the
   `mcp-server` job passing?** The job exists and would fail. Knowing which tells you whether
   this is a process gap or a one-off override.
2. **Was dropping the per-document `LegalProvision_<slug>` classes intended to be a breaking
   change for consumers?** `docs/ARCHITECTURE.md:107-113` lists what needs a MAJOR version and
   does not mention provision typing. If MCP is a supported consumer, that list looks
   incomplete.
3. **Why do KarS and VÕS have no `dcterms:source`, and why do 134 acts resolve to no URL at
   all?** Producer-side question, but it caps what the MCP layer can honestly promise.
4. **Is `define_term`'s concept corpus meant to be dominated by municipal-regulation terms?**
   The README's own example (`elatis`) returns nothing, which suggests the statutory-term
   layer either is not in `concepts_combined.jsonld` or is not typed as expected.
5. **Which identity system is the target — TARA, X-tee, or a ministry's own IdP?** That
   choice determines whether the token map (idea 6) is a stepping stone or a dead end.
6. **Is the deployment expected to track `main`, or to serve a pinned release?** The
   entrypoint assumes the former; reproducibility requirements usually demand the latter.
7. **The HANDOFF notes Seadusloome already runs Jena/Fuseki on the same box.** Would routing
   the heavy joins (regulations, court decisions, CURIA) through SPARQL be preferable to
   idea 7's SQLite index, given the corpus is already loaded there?
