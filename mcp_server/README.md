# estleg-mcp

An [MCP](https://modelcontextprotocol.io) server (stdio for local IDE clients,
or streamable HTTP for a shared remote endpoint) that exposes the **Estonian
Legal Ontology** corpus as a set of natural-language tools, so you can ask
plain-language legal questions from Claude, Cursor, or Copilot and get answers
**grounded in real riigiteataja.ee citations**.

Every result that maps to a source carries its canonical URL
(riigiteataja.ee for laws/provisions, riigikohus.ee for court decisions,
eelnoud.valitsus.ee for drafts, EUR-Lex for EU directives). Precision over
fuzziness: the server reads the JSON-LD corpus under `krr_outputs/` directly,
per-law, and never guesses a citation.

## What it queries

The corpus is a JSON-LD ontology of Estonian + EU law: ~1,122 enacted laws,
22,832 draft-legislation entries, 12,000+ Supreme Court decisions, EU
legislation, sanctions, institutional competence, and EU-transposition
mappings. This server surfaces the slices a lawmaker most often needs.

## Install

From the repository root (the directory containing `krr_outputs/`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e mcp_server
```

This installs the `estleg-mcp` console script.

## Register with Claude Code

```bash
claude mcp add estleg -- estleg-mcp
```

Or add it to a project-local `.mcp.json`:

```json
{
  "mcpServers": {
    "estleg": {
      "command": "estleg-mcp",
      "env": {
        "ESTLEG_CORPUS": "/absolute/path/to/estonian-legal-ontology"
      }
    }
  }
}
```

### Corpus location (`ESTLEG_CORPUS`)

The server finds the corpus in this order:

1. The `ESTLEG_CORPUS` environment variable, if set, pointing at the directory
   that contains `krr_outputs/INDEX.json`.
2. Otherwise it walks up from the installed package to find that directory.

Set `ESTLEG_CORPUS` explicitly when the server is installed somewhere other
than inside the corpus checkout (recommended for the `.mcp.json` form above).

## Use it from other local clients

MCP is an open standard, so the same `estleg-mcp` binary works in any MCP
client; just point each at the console script.

**Cursor** — `~/.cursor/mcp.json` (global) or `<project>/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "estleg": { "command": "/abs/path/to/mcp_server/.venv/bin/estleg-mcp" }
  }
}
```

On Windows the command is `C:\\...\\mcp_server\\.venv\\Scripts\\estleg-mcp.exe`.

**VS Code / Visual Studio (GitHub Copilot)** use an `mcp.json` with a top-level
`servers` key (not `mcpServers`), and the tools appear in Copilot **Agent mode**.

## Remote deployment (HTTP, e.g. Coolify)

To serve every device from one endpoint instead of installing the ~1.5 GB
corpus on each machine, run the server over streamable HTTP behind TLS.

### Configuration (environment)

| Var | Default | Purpose |
|-----|---------|---------|
| `ESTLEG_TRANSPORT` | `stdio` | set to `http` for the remote transport |
| `ESTLEG_HOST` | `127.0.0.1` | bind address (`0.0.0.0` in a container) |
| `ESTLEG_PORT` | `8000` | port |
| `ESTLEG_TOKEN` | (unset) | bearer token; when set, every path except `/healthz` requires `Authorization: Bearer <token>` |
| `ESTLEG_ALLOWED_HOSTS` | (unset) | comma-separated `Host` allow-list for MCP's DNS-rebinding protection (`host` or `host:port`; `host:*` = any port). **Unset = protection off**, so a request through a reverse proxy is not rejected. Set it to your public host(s) to re-enable the check. |
| `ESTLEG_ALLOWED_ORIGINS` | (unset) | comma-separated `Origin` allow-list (only used when `ESTLEG_ALLOWED_HOSTS` is set; for browser clients) |
| `ESTLEG_CORPUS` | auto | corpus dir (the one holding `krr_outputs/INDEX.json`) |
| `ESTLEG_CORPUS_REPO` / `ESTLEG_CORPUS_BRANCH` | public repo / `main` | used by the container entrypoint to clone/update the corpus |

The MCP endpoint is at `/mcp`; `/healthz` is an unauthenticated health check.

> **Behind a proxy:** MCP's streamable-HTTP transport validates the `Host`
> header and answers a mismatch with `421 Invalid Host header`. The server
> therefore leaves that protection **off by default** (the bearer token is the
> access gate); set `ESTLEG_ALLOWED_HOSTS=your.domain` to turn it back on.

### Docker / Coolify

`docker/Dockerfile` builds a self-contained image (python:3.13-slim + uv,
mirroring Seadusloome). The corpus is **not** baked in; `docker/entrypoint.sh`
clones/updates it onto a persistent volume at boot (LFS skipped — only the
per-file JSON is read). Coolify service settings:

- **Base Directory:** `mcp_server` · **Dockerfile:** `docker/Dockerfile`
- **Persistent volume** mounted at `/data` (keeps the corpus across redeploys)
- **Environment:** `ESTLEG_TOKEN=<long random secret>` (transport/host/port are
  already defaulted in the image). Optionally `ESTLEG_ALLOWED_HOSTS=estleg.sixtyfour.ee`
  to enable DNS-rebinding protection scoped to the public domain.
- **Domain:** e.g. `estleg.sixtyfour.ee` · **Port:** `8000` · **Health path:** `/healthz`

### Connect a client to the remote endpoint

```json
{
  "mcpServers": {
    "estleg": {
      "url": "https://estleg.sixtyfour.ee/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

This `url` form works in Cursor and Claude, and is what the cloud Copilots
require (they cannot launch a local binary). For Claude Code:

```bash
claude mcp add --transport http estleg https://estleg.sixtyfour.ee/mcp \
  --header "Authorization: Bearer <token>"
```

## Tools

Each tool takes a plain-language law reference (title, official abbreviation
such as `KarS` / `VÕS` / `PKS`, or corpus slug). Lists are capped by `limit`
where noted; long legal text is truncated to stay chat-sized. A query that
matches nothing returns an empty list (or a `note`), never an error.
Empty lists on `references_of`, `who_references`, `court_decisions_for_law`,
and `competent_authority_for_law` are usually **domain sparsity**, not a
tool bug: most of the ~1,100 statutes have never been cited by a Supreme
Court judgment, never name a competent authority, and never cite another
act. Inverse edges live on provision nodes and are also rolled up onto
the act root (`estleg:references` / `referencedBy` / `interpretedBy` /
`competentAuthority`, issue #508).

| Tool | What it answers | Example question |
|------|-----------------|------------------|
| `search_laws(query, limit=10)` | Find laws by title/abbreviation/slug | "Which laws mention 'töölepingu' / employment?" |
| `get_law(law, as_of=None)` | One-law overview + status + EuroVoc subjects; `as_of` adds a point-in-time section count | "Give me an overview of the Penal Code (KarS)." |
| `get_provision(law, paragraph, as_of=None)` | Read one § in full — current text, or as it stood on a past date | "What did § 13 of KarS say on 2010-06-15?" |
| `provision_history(law, paragraph)` | Full redaction timeline of one § (each window + text) | "How has § 13 of KarS changed over time?" |
| `who_references(law, paragraph=None)` | Incoming references (impact: what cites this) | "Which provisions reference § 60 of KarS?" |
| `references_of(law, paragraph=None)` | Outgoing references (what this cites) | "What does § 13 of KarS reference?" |
| `drafts_affecting_law(law, limit=20)` | Pending bills that would change the law | "What pending bills affect the Health Services Organisation Act?" |
| `court_decisions_for_law(law, limit=20)` | Riigikohus decisions interpreting the law | "Which Supreme Court cases interpret KarS?" |
| `sanctions_for_law(law)` | Penalties the law defines | "What penalties does KarS define?" |
| `competent_authority_for_law(law)` | Which institutions enforce/administer it | "Which authority enforces the Personal Data Protection Act?" |
| `transposition(query)` | EU directive ↔ Estonian law, both directions | "Which Estonian law transposes EU directive 31990L0314?" (or pass a law name to go the other way) |
| `eu_case_law_for_directive(celex, limit=20)` | CURIA decisions that mention a directive CELEX | "Which CURIA judgments interpret 32000L0060?" |
| `define_term(term, limit=10)` | Look up a legal term in the concepts overlay | "What does 'elatis' mean in the ontology?" |
| `harmonisation_for_directive(celex, limit=20)` | Cross-border measures sharing a directive CELEX | "Which neighbouring states also transposed 32000L0060?" |
| `layers_available()` | Which sidecars MCP reads vs excludes | "Does MCP load the harmonisation / similarity overlays?" |
| `regulations_for_law(law, limit=50)` | Regulations (määrused) issued under / implementing a statute | "Which regulations are issued under the Local Government Organisation Act (KOKS)?" |
| `get_regulation(name)` | One-regulation overview (issuer, parent statute, status, citation) | "Give me an overview of regulation t302269." |
| `regulations_by_issuer(institution, limit=50)` | All regulations enacted by a body (ministry, government, municipality) | "Which regulations has the Minister of Finance (Rahandusminister) issued?" |

### Citation derivation

- **Law / provision** — from the act's `dcterms:source` IRI
  (`…/akt/<id>.xml`), with the trailing `.xml` stripped to the human URL.
- **Regulation (määrus)** — same derivation from the regulation's own
  `dcterms:source`; `regulations_for_law` / `get_regulation` also resolve the
  parent statute (via `estleg:issuedUnder`) to its riigiteataja URL.
- **Court decision** — the decision's `estleg:decisionLink` (riigikohus.ee).
- **Draft** — the draft's EIS link (eelnoud.valitsus.ee).
- **EU** — CELEX → `https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:<celex>`.
- **CURIA** — the decision's `estleg:curiaLink` (EUR-Lex ET TXT), else the same CELEX URL.

### Overlay coverage (#540)

MCP reads **per-file sidecars**, never `combined_ontology.jsonld`. `layers_available()`
returns this table at runtime (`status` = `wired` / `loadable` / `excluded`).

| Sidecar | Status | How MCP reaches it |
|---------|--------|--------------------|
| Law peeps + `INDEX.json` | wired | `search_laws`, `get_law`, `get_provision`, references |
| `concepts/concepts_combined.jsonld` | wired | `define_term` (overlay loader; not the law peeps) |
| `harmonisation/…/harm_<celex>.json` | loadable | `harmonisation_for_directive` opens **one** file when asked |
| `sanctions/`, `amendments/`, `regulations/` | wired | `sanctions_for_law`, `amendment_history`, regulation tools |
| `provision_versions/` | wired | `get_provision(as_of=…)`, `provision_history` |
| `riigikohus/`, `eelnoud/`, `institutions/` | wired | court / draft / authority tools |
| `curia/curia_*_peep.json` | wired | `eu_case_law_for_directive` (CELEX substring; no `#418` interprets edges) |
| `transposition_mapping.json` | wired | `transposition` |
| `eurlex/*_peep.json` | excluded | CELEX/EUR-Lex URLs only; peeps are not scanned |
| `annotations/` | excluded | no MCP reader |
| `concepts/concept_crossref_report.json` | excluded | build report only |
| `similarity_index.json`, `combined_ontology.jsonld` | excluded | Git LFS; not loaded (see semantic-search note below) |

Harmonisation is **not** a full-layer index: only the matching `harm_<celex>.json`
is opened. Concepts **are** fully wired through `define_term`.

## Development

```bash
pip install -e "mcp_server[dev]"
ruff check mcp_server
pytest mcp_server
```

The data-access layer (`estleg_mcp/data.py`) has no MCP imports and is tested
directly against the real corpus in `tests/test_data.py`.

## Follow-up: semantic search (v1 limitation)

This version does **not** offer semantic / similarity search. The corpus's
`krr_outputs/similarity_index.json` and `krr_outputs/combined_ontology.jsonld`
ship as Git LFS pointers and are not real JSON in a plain clone, so the server
reads per-file and never loads the combined graph. A semantic-search tool is a
natural follow-up, gated on `git lfs pull` to materialise those artifacts (and
an embedding/index step over the provision summaries).
