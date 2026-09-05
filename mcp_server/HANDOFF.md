# estleg-mcp — handoff / where this stands

_Snapshot: 2026-09-04. Matches `main` (`estleg-mcp` 0.1.0); the live remote
still runs the pre-#678 build until it is redeployed._

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
(`#499` point-in-time history + `#500` regulations).

- `estleg_mcp/data.py` — data-access layer over the per-file `*_peep.json`
  (never the LFS-only combined graph). Resolves laws by title / official
  abbreviation (KarS, VÕS, PS, ...) / slug, accent-insensitive.
- `estleg_mcp/server.py` — FastMCP server, **14 tools**:
  `search_laws`, `get_law`, `get_provision`, `who_references`, `references_of`,
  `drafts_affecting_law`, `court_decisions_for_law`, `sanctions_for_law`,
  `competent_authority_for_law`, `transposition`,
  `provision_history`, `regulations_for_law`, `get_regulation`,
  `regulations_by_issuer`.
  `get_law` / `get_provision` accept optional `as_of` for a historical redaction.
- Transports: **stdio** (local IDEs) and **streamable-HTTP** with an
  `ESTLEG_TOKEN` bearer gate (remote). `/healthz` is unauthenticated.
- `docker/` — Coolify-style image (python:3.13-slim + uv, mirrors Seadusloome);
  the entrypoint clones the corpus to a `/data` volume at boot (LFS skipped).
- Tests: `mcp_server/tests/` — data-layer unit tests, 14-tool contract tests,
  and HTTP host/`/healthz` transport tests.

## Status

- On `main` as `mcp_server/` (install: `pip install -e mcp_server`).
- Remote HTTP endpoint is live: `https://estleg.sixtyfour.ee/mcp`
  (`GET /healthz` → `ok`).
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

Suite: 19 failing tests before, 0 after (104 passed, 1 skipped).

## Next steps

1. Point remaining local clients (Cursor on Mac + Windows, Copilot) at
   `https://estleg.sixtyfour.ee/mcp` with the bearer token (README →
   "Connect a client to the remote endpoint").
2. Keep the Coolify `/data` volume's corpus clone current with `main`
   (entrypoint already `git fetch --depth 1` + `reset --hard origin/main`
   on boot; redeploy after corpus releases).

## Known gaps / follow-ups

- **KarS / VÕS have no riigiteataja source** in the ontology, so `get_law`,
  `get_provision` and `sanctions_for_law` return `rt_url: ""` for them (their
  Wikidata IRI comes back under `external_ids`). Restoring `dcterms:source`
  on those act nodes is a **producer-side** ticket, not an MCP one; 134 acts
  in total have no source. The MCP contract tests pin PS / LS for the
  riigiteataja citation assertions until then.
- **371 act nodes carry an official English-text ELI** under
  `estleg:officialEnglishText` (e.g. `https://www.riigiteataja.ee/en/eli/…`).
  No tool surfaces it today; `external_ids` deliberately covers only non-RT
  identifiers. Cheap to add to `get_law` if an English citation is wanted.

- **Semantic search** is not in v1: `similarity_index.json` and
  `combined_ontology.jsonld` ship as Git-LFS pointers, so a semantic tool needs
  `git lfs pull` plus an embedding/index step over provision summaries.
- **`temporalStatus` = "unknown"** for some acts (e.g. VÕS, PS) is an upstream
  ontology data gap, surfaced honestly; `get_law` also returns
  `consolidated_as_of` (the consolidated-text date) for context.
- Seadusloome already clones this ontology and runs a Jena/Fuseki SPARQL
  endpoint on the same box, so a future estleg could query Fuseki instead of
  re-cloning the corpus.
