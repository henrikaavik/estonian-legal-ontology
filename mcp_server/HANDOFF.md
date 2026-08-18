# estleg-mcp — handoff / where this stands

_Snapshot: 2026-08-18. Matches `main` (`estleg-mcp` 0.1.0) and the live remote._

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

## Next steps

1. Point remaining local clients (Cursor on Mac + Windows, Copilot) at
   `https://estleg.sixtyfour.ee/mcp` with the bearer token (README →
   "Connect a client to the remote endpoint").
2. Keep the Coolify `/data` volume's corpus clone current with `main`
   (entrypoint already `git fetch --depth 1` + `reset --hard origin/main`
   on boot; redeploy after corpus releases).

## Known gaps / follow-ups

- **Semantic search** is not in v1: `similarity_index.json` and
  `combined_ontology.jsonld` ship as Git-LFS pointers, so a semantic tool needs
  `git lfs pull` plus an embedding/index step over provision summaries.
- **`temporalStatus` = "unknown"** for some acts (e.g. VÕS, PS) is an upstream
  ontology data gap, surfaced honestly; `get_law` also returns
  `consolidated_as_of` (the consolidated-text date) for context.
- Seadusloome already clones this ontology and runs a Jena/Fuseki SPARQL
  endpoint on the same box, so a future estleg could query Fuseki instead of
  re-cloning the corpus.
