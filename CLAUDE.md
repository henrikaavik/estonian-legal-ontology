# CLAUDE.md

Guidance for Claude Code working in this repository. **Read [AGENTS.md](AGENTS.md)
first** — it is the source of truth for working conventions (validation gates,
the node `@id` naming scheme, the integration pipeline, SHACL policy). This file
exists mainly so Claude Code auto-loads that context, since Claude Code reads
`CLAUDE.md`, not `AGENTS.md`.

## What this repo is

A machine-readable JSON-LD/RDF ontology of Estonian + EU law (~1,120 enacted
laws, 22.8k drafts, state + municipal regulations, Riigikohus decisions, EU acts
and EU court decisions) under `krr_outputs/`, plus enrichment/validation scripts
in `scripts/`. It is the data backend behind seadusloome.sixtyfour.ee.

## Working rules (see AGENTS.md for the detail)

- Do not hand-edit generated artifacts such as `krr_outputs/combined_ontology.jsonld`;
  regenerate via the canonical builders.
- Before finishing data-quality work, run the gates:
  `python3 -m ruff check scripts/ tests/`, `python3 -m pytest -q`,
  `python3 scripts/validate_all.py`, `python3 scripts/shacl_validate_all.py --all`.
- Reuse helpers in `scripts/estleg_common.py` / `scripts/riigiteataja_common.py`
  rather than duplicating parsing or filesystem logic.
- Large artifacts (`combined_ontology.jsonld`, `similarity_index.json`) are Git
  LFS; run `git lfs pull` if you actually need them.

## mcp_server/

`mcp_server/` is **estleg-mcp**: a natural-language MCP query layer over this
corpus (10 tools, each returning real riigiteataja.ee / riigikohus.ee /
eelnoud.valitsus.ee / EUR-Lex citations), with a stdio transport for local IDE
clients and a streamable-HTTP transport for a shared remote endpoint. See
[mcp_server/README.md](mcp_server/README.md). Current status and next steps are
in [mcp_server/HANDOFF.md](mcp_server/HANDOFF.md).
