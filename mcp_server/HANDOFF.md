# estleg-mcp — handoff / where this stands

_Source status checked 2026-10-08 on branch `tier1/wave4` (`estleg-mcp`
0.2.0, issue #714 hardening). This repository check does not establish the
revision deployed at the remote endpoint._

## Goal

Make the Estonian Legal Ontology queryable in natural language from AI clients
(Claude Code, Cursor, GitHub Copilot) with every answer grounded in real
riigiteataja.ee / riigikohus.ee / eelnoud.valitsus.ee / EUR-Lex citations. This
is the first of three wedges; the other two (not started) are folding these
tools into a seadusloome "lawmaker cockpit", and mining the ontology for
policy/political content (transposition gaps, conflicts, regulatory burden).

## What's built (on `main`)

Shipped via PR [#493](https://github.com/henrikaavik/estonian-legal-ontology/pull/493)
(merged 2026-06-16), then PR [#660](https://github.com/henrikaavik/estonian-legal-ontology/pull/660)
(issue [#495](https://github.com/henrikaavik/estonian-legal-ontology/issues/495):
tool-contract tests / OWL exclusion) and
PR [#666](https://github.com/henrikaavik/estonian-legal-ontology/pull/666)
(`#499` point-in-time history + `#500` regulations), followed by
[#732](https://github.com/henrikaavik/estonian-legal-ontology/pull/732)
(#678/#680 provision detection and source-URL fixes; merged 2026-09-07).

- `estleg_mcp/data.py` — data-access layer over the per-file `*_peep.json`
  (never the LFS-only combined graph). Resolves laws by title / official
  abbreviation (KarS, VÕS, PS, ...) / slug, accent-insensitive.
- `estleg_mcp/server.py` — FastMCP server, **24 tools**:
  `search_laws`, `get_law`, `get_provision`, `who_references`, `references_of`,
  `drafts_affecting_law`, `court_decisions_for_law`, `sanctions_for_law`,
  `competent_authority_for_law`, `transposition`,
  `provision_history`, `regulations_for_law`, `get_regulation`,
  `regulations_by_issuer`, `define_term`, `laws_for_subject`,
  `amendment_history`, `eu_case_law_for_directive`,
  `harmonisation_for_directive`, `layers_available`, and (#714)
  `what_changed`, `transposition_gaps`, `kov_regulations_citing`,
  `explain_provision`.
  `get_law` / `get_provision` accept optional `as_of` for a historical redaction.
  Every MCP call goes through one wrapper (`server._wire`) that adds the
  `language` argument, the `snapshot` envelope and the audit line.
- `estleg_mcp/i18n.py` (Estonian-first descriptions, ET/EN result text),
  `provenance.py` (snapshot), `audit.py` (JSON audit line), `security.py`
  (per-consumer tokens, fail-closed check, rate limit, ASGI middleware).
- Transports: **stdio** (local IDEs, no credentials) and **streamable-HTTP**
  (remote), which **fails closed** without `ESTLEG_TOKENS` / `ESTLEG_TOKEN`.
  `/healthz` is unauthenticated.
- `docker/` — Coolify-style image (python:3.13-slim + uv, mirrors Seadusloome),
  running as non-root uid 10001; the entrypoint checks out the corpus release
  tag `ESTLEG_CORPUS_REF` (default `v1.0.0`) on a `/data` volume at boot (LFS
  skipped) and exports the commit for the snapshot.
- Tests: `mcp_server/tests/` — data-layer and tool-contract tests, HTTP
  host/`/healthz` transport tests, `test_hardening.py` (security, audit,
  envelope, language; no corpus needed) and `test_new_tools.py` (the four new
  tools on fixtures and on the corpus).

## Status

- On `main` as `mcp_server/` (install: `pip install -e mcp_server`).
- Configured remote endpoint: `https://estleg.sixtyfour.ee/mcp`.
  Check `/healthz` for availability and verify the deployed revision separately;
  a health response alone does not prove it includes the merged fixes.
- Client config is in [README.md](README.md) (stdio and the remote `url` form).

### 2026-09-04 — corpus drift repaired (#678, #680)

Three regressions caused by upstream changes to the corpus and the scripts
tree, all of which failed **silently** (no error, just empty or wrong output):

- **#678 — provision detection was dark.** `data._is_provision` still required
  the per-law `estleg:LegalProvision_<abbrev>` subclass, but since issue #434
  the generators stamp the bare `estleg:LegalProvision` (KarS: 532 bare, 0
  prefixed). `get_provision`, `provision_history`, `who_references`,
  `references_of`, `court_decisions_for_law`, `competent_authority_for_law`
  and every `num_provisions` count returned nothing. Same bug in the
  regulation section counter (`estleg:Regulation_` prefix), which reported 0
  sections for all ~15k määrused. Detection now accepts the bare class, the
  municipal `estleg:KovProvision`, and the legacy prefixes. To stop it
  recurring quietly: `data.provision_detection_check()` counts KarS's sections,
  `server.main()` aborts on a zero count (opt out with
  `ESTLEG_ALLOW_EMPTY_PROVISIONS=1`), and `layers_available()` carries a
  `provision_detection` row with the live count.
- **#680 — `rt_url` could return a foreign host.** It fell back to
  `owl:sameAs` with no host check, so KarS and VÕS (no `dcterms:source`, a
  Wikidata `owl:sameAs`) served a `wikidata.org` URL under a field documented
  as the official riigiteataja.ee URL. `rt_url` is now guarded to
  riigiteataja.ee and its subdomains, returning `""` otherwise, and
  `data.external_ids()` surfaces the Wikidata IRI instead (wired into
  `get_law` and `search_laws`). Census over the 1,120 INDEX laws: 984 have an
  RT `dcterms:source`, 134 have no source at all, 2 (KarS, VÕS) previously
  leaked Wikidata.
- **EuroVoc keyword expansion was empty.** `_eurovoc_keywords_by_id` read the
  `EUROVOC_DOMAINS` table from `scripts/classify_eurovoc.py`, which issue #472
  reduced to a runpy shim; the table lives in `src/estleg/classify_eurovoc.py`.
  It now tries the package path first and falls back to `scripts/`, still
  parsing with `ast` rather than importing.

Historical fix-run result: 19 failing tests before, 0 after (104 passed,
1 skipped). Use current CI for the present suite result.

### 2026-10-08 — ministry / Bürokratt hardening (#714)

Implemented the self-contained sub-items of #714; the SQLite index and the
REST/OpenAPI + X-tee facade are deliberately **not** done (they wait for the
authority conversation the owner asked for).

- **Fail-closed HTTP + per-consumer tokens.** `ESTLEG_TOKENS`
  (`name=token,…` or a JSON file) names each consumer; the legacy
  `ESTLEG_TOKEN` is consumer `default`. HTTP exits `1` without credentials
  unless `ESTLEG_ALLOW_ANONYMOUS_HTTP=1`. stdio stays credential-free.
- **Audit line** per call (stderr or `ESTLEG_AUDIT_LOG`): timestamp,
  consumer, transport, tool, `args_sha256` (optionally HMAC-keyed; raw
  arguments never logged), status, result size, truncated flag, latency,
  corpus commit / tag, ontology and server version. Format in README.
- **Rate limit**: optional per-consumer token bucket (`ESTLEG_RATE_LIMIT`),
  `429` + `Retry-After`, audited as `rate_limited`.
- **Snapshot envelope** on every result: `corpus_commit`, `corpus_ref`,
  `ontology_version`, `evaluation_date`, `server_version`, `language`. Dict
  results gain a `snapshot` key; list results become `{result, snapshot}`.
- **Truncation**: `truncated` + `full_length` on every cut text;
  `full_text=True` now honoured by `get_provision(as_of=…)` and
  `provision_history` too.
- **Citations on every row**: `define_term`, `laws_for_subject`,
  `amendment_history`, `competent_authority_for_law`,
  `harmonisation_for_directive`, `provision_history` (redaction RT URL);
  `get_provision(as_of=…)` adds `redaction_rt_url`. Missing RT citations stay
  `""`.
- **Estonian-first** descriptions; `language` (`et` default, `en`) on every
  tool selects the text of notes, caveats and explanations. **Breaking for
  callers that matched English note text**: notes are Estonian by default.
- **Container**: non-root `USER estleg` (10001), corpus pinned to a release
  tag. A `/data` volume created by the old root image needs a one-off
  `chown -R 10001:10001` (the entrypoint says so).
- **Four new tools** over the per-file corpus: `what_changed`
  (provision_versions + `resultedInVersion`), `transposition_gaps` (#701 rule
  from `eurlex_directives_peep.json` + `transposition_mapping.json`, not the
  LFS overlay), `kov_regulations_citing` (KOV `implementsCitation` targets +
  `issuedUnder`), `explain_provision`.

Measured on this branch's corpus: 845 of 939 in-force directives carry no
transposition edge; 6,704 KOV regulations cite or are issued under KOKS;
740 KOV citation targets use law prefixes the resolver cannot map (largest:
`KOFS` 291, `KalmS` 128, `KOVVS` 92) and are not attributed to a law.

## Next steps

1. **Before redeploying 0.2.0**: set `ESTLEG_TOKENS` (one entry per client)
   on the Coolify service, or keep `ESTLEG_TOKEN` (consumer `default`);
   without either the new image exits at boot. `chown -R 10001:10001` the
   existing `/data` volume once (it was written by the root-run image).
2. Point remaining local clients (Cursor on Mac + Windows, Copilot) at
   `https://estleg.sixtyfour.ee/mcp` with their own bearer token (README →
   "Connect a client to the remote endpoint").
3. Raise `ESTLEG_CORPUS_REF` when a new corpus release is tagged (the image
   defaults to `v1.0.0`); redeploy to pick it up. Every answer names the tag
   and commit it was served from.
4. Ship the audit log somewhere durable (`ESTLEG_AUDIT_LOG` on the volume, or
   stderr into the platform's log drain) and set `ESTLEG_AUDIT_HASH_KEY`.
5. #714 remainder, after the authority conversation: SQLite index, REST /
   OpenAPI facade over `data.py`, X-tee registration.

## Known gaps / follow-ups

- **KarS / VÕS have no riigiteataja source** in the ontology, so `get_law`,
  `get_provision` and `sanctions_for_law` return `rt_url: ""` for them (their
  Wikidata IRI comes back under `external_ids`). Restoring `dcterms:source`
  on those act nodes is producer-side work (#692/#695). The September 4
  census above recorded 134 acts without a source. The MCP contract tests pin PS / LS for the
  riigiteataja citation assertions until then.
- **371 act nodes carry an official English-text ELI** under
  `estleg:officialEnglishText` (e.g. `https://www.riigiteataja.ee/en/eli/…`).
  No tool surfaces it today; `external_ids` deliberately covers only non-RT
  identifiers. Cheap to add to `get_law` if an English citation is wanted.

- **KOV citation targets with unmapped law prefixes** (`KOFS`, `KalmS`,
  `KOVVS`, … 740 targets) are skipped by `kov_regulations_citing`. Teaching
  `_HUMAN_ABBREVIATIONS` (or the abbreviation registry) those prefixes would
  attribute them.
- **The container image was not built in the #714 session** (no Docker
  daemon); the entrypoint was exercised against a local test repo (clone,
  tag update, branch alias). Build and boot it once before the redeploy.
- **Semantic search** is not in v1: `similarity_index.json` and
  `combined_ontology.jsonld` ship as Git-LFS pointers, so a semantic tool needs
  `git lfs pull` plus an embedding/index step over provision summaries.
- **`temporalStatus` = "unknown"** means the corpus lacks sufficient status
  evidence or the IRI is retired (#682); it must not be read as `repealed`.
  `get_law` also returns
  `consolidated_as_of` (the consolidated-text date) for context.
- Seadusloome already clones this ontology and runs a Jena/Fuseki SPARQL
  endpoint on the same box, so a future estleg could query Fuseki instead of
  re-cloning the corpus.
