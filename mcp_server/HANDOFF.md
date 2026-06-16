# estleg-mcp — handoff / where this stands

_Snapshot: 2026-06-15. Update or delete once the remote deploy is live._

## Goal

Make the Estonian Legal Ontology queryable in natural language from AI clients
(Claude Code, Cursor, GitHub Copilot) with every answer grounded in real
riigiteataja.ee / riigikohus.ee / eelnoud.valitsus.ee / EUR-Lex citations. This
is the first of three wedges; the other two (not started) are folding these
tools into a seadusloome "lawmaker cockpit", and mining the ontology for
policy/political content (transposition gaps, conflicts, regulatory burden).

## What's built (branch `feat/estleg-mcp-server`, PR #493)

- `estleg_mcp/data.py` — data-access layer over the per-file `*_peep.json`
  (never the LFS-only combined graph). Resolves laws by title / official
  abbreviation (KarS, VÕS, PS, ...) / slug, accent-insensitive.
- `estleg_mcp/server.py` — FastMCP server, 10 tools: `search_laws`, `get_law`,
  `get_provision`, `who_references` (impact), `references_of`,
  `drafts_affecting_law`, `court_decisions_for_law`, `sanctions_for_law`,
  `competent_authority_for_law`, `transposition`.
- Transports: **stdio** (local IDEs) and **streamable-HTTP** with an
  `ESTLEG_TOKEN` bearer gate (remote).
- `docker/` — Coolify-style image (python:3.13-slim + uv, mirrors Seadusloome);
  the entrypoint clones the corpus to a `/data` volume at boot (LFS skipped).
- ruff clean; 24 data-layer tests pass; all 10 tools exercised over both the
  stdio and streamable-HTTP protocols (including a 401 on a missing token).

## Status

- Registered locally as MCP server `estleg` at user scope (`~/.claude.json`) on
  the Mac, so it already works in any local Claude Code session there.
- Pushed as **PR #493** (`feat/estleg-mcp-server`); **not merged**.

## Next steps

1. Review + merge PR #493.
2. Deploy remote (host once for all devices): a new Coolify service on the same
   Hostinger VPS as seadusloome.sixtyfour.ee. Base directory `mcp_server`,
   Dockerfile `docker/Dockerfile`, persistent volume at `/data`, env
   `ESTLEG_TOKEN=$(openssl rand -hex 32)`, domain e.g. `estleg.sixtyfour.ee`,
   port `8000`, health path `/healthz`; point DNS at the VPS. (README →
   "Remote deployment (HTTP, e.g. Coolify)".)
3. Point Cursor (Mac + the Windows PC) and any other client at
   `https://estleg.sixtyfour.ee/mcp` with the bearer token (config in README).

## Known gaps / follow-ups

- **Semantic search** is not in v1: `similarity_index.json` and
  `combined_ontology.jsonld` ship as Git-LFS pointers, so a semantic tool needs
  `git lfs pull` plus an embedding/index step over provision summaries.
- **`temporalStatus` = "unknown"** for some acts (e.g. VÕS, PS) is an upstream
  ontology data gap, surfaced honestly; `get_law` also returns
  `consolidated_as_of` (the consolidated-text date) for context.
- Seadusloome already clones this ontology and runs a Jena/Fuseki SPARQL
  endpoint on the same box, so a future estleg could query Fuseki instead of
  re-cloning the corpus, worth considering when the remote service is set up.
