# estleg-mcp

An [MCP](https://modelcontextprotocol.io) server (stdio for local IDE clients,
or streamable HTTP for a shared remote endpoint) that exposes the **Estonian
Legal Ontology** corpus as a set of natural-language tools, so you can ask
plain-language legal questions from Claude, Cursor, or Copilot and get answers
**linked to source citations when the corpus supplies them**.

Every result row that maps to a source carries its canonical URL
(riigiteataja.ee for laws/provisions/regulations, riigikohus.ee for court
decisions, eelnoud.valitsus.ee for drafts, EUR-Lex for EU directives). A missing
riigiteataja citation is an empty string, never another host. Precision over
fuzziness: the server reads the JSON-LD corpus under `krr_outputs/` directly,
per-law, and never guesses a citation.

Every result also carries a **`snapshot`** (corpus commit / release tag,
ontology version, evaluation date, server version), and every call writes one
**JSON audit line**. Tool descriptions are **Estonian-first**, and each tool
takes a `language` argument (`et` default, `en`). See
[Results: snapshot, language, truncation](#results-snapshot-language-truncation-714)
and [Security model](#security-model-714).

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
      "command": "/absolute/path/to/estonian-legal-ontology/.venv/bin/estleg-mcp",
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
    "estleg": { "command": "/abs/path/to/estonian-legal-ontology/.venv/bin/estleg-mcp" }
  }
}
```

On Windows the command is `C:\\...\\estonian-legal-ontology\\.venv\\Scripts\\estleg-mcp.exe`.

**VS Code / Visual Studio (GitHub Copilot)** use an `mcp.json` with a top-level
`servers` key (not `mcpServers`), and the tools appear in Copilot **Agent mode**.

## Remote deployment (HTTP, e.g. Coolify)

To serve every device from one endpoint instead of installing the multi-gigabyte
corpus on each machine, run the server over streamable HTTP behind TLS.

### Configuration (environment)

| Var | Default | Purpose |
|-----|---------|---------|
| `ESTLEG_TRANSPORT` | `stdio` | set to `http` for the remote transport |
| `ESTLEG_HOST` | `127.0.0.1` | bind address (`0.0.0.0` in a container) |
| `ESTLEG_PORT` | `8000` | port |
| `ESTLEG_TOKENS` | (unset) | per-consumer bearer tokens: inline `name=token,name2=token2`, or the path of a JSON file `{"name": "token"}`. The consumer name appears in the audit line. **HTTP refuses to start unless this or `ESTLEG_TOKEN` is set.** |
| `ESTLEG_TOKEN` | (unset) | legacy single bearer token; the consumer `default`. Can be combined with `ESTLEG_TOKENS`. |
| `ESTLEG_ALLOW_ANONYMOUS_HTTP` | (unset) | `1` lets HTTP start with no tokens (local development only; prints a warning; consumer `anonymous`) |
| `ESTLEG_RATE_LIMIT` | (off) | per-consumer token bucket: `60/minute`, `5/second`, `1000/hour`, or a bare per-minute number. Over-limit requests get `429` + `Retry-After`. |
| `ESTLEG_RATE_BURST` | the per-period count | bucket size for `ESTLEG_RATE_LIMIT` |
| `ESTLEG_AUDIT` | on | `off` disables the audit log |
| `ESTLEG_AUDIT_LOG` | stderr | file to append audit lines to (`-` = stderr; never stdout, which is the stdio protocol channel). An unwritable path fails at boot. |
| `ESTLEG_AUDIT_HASH_KEY` | (unset) | HMAC key for `args_sha256`, so short queries cannot be dictionary-reversed from the log |
| `ESTLEG_ALLOWED_HOSTS` | (unset) | comma-separated `Host` allow-list for MCP's DNS-rebinding protection (`host` or `host:port`; `host:*` = any port). **Unset = protection off**, so a request through a reverse proxy is not rejected. Set it to your public host(s) to re-enable the check. |
| `ESTLEG_ALLOWED_ORIGINS` | (unset) | comma-separated `Origin` allow-list (only used when `ESTLEG_ALLOWED_HOSTS` is set; for browser clients) |
| `ESTLEG_CORPUS` | auto | corpus dir (the one holding `krr_outputs/INDEX.json`) |
| `ESTLEG_CORPUS_REPO` | public repo | container entrypoint: where to clone the corpus from |
| `ESTLEG_CORPUS_REF` | `v1.0.0` | container entrypoint: the corpus **release tag** to check out (a branch works but is not reproducible). Also stamped on every `snapshot` / audit line. `ESTLEG_CORPUS_BRANCH` is a deprecated alias. |
| `ESTLEG_CORPUS_COMMIT` | (from `.git`) | set by the entrypoint to the checked-out commit; otherwise the server reads `HEAD` from the corpus checkout |
| `ESTLEG_ALLOW_EMPTY_PROVISIONS` | (unset) | set to `1` to start even when the startup provision check fails (see below). Applies to **both** transports. |

The MCP endpoint is at `/mcp`; `/healthz` is an unauthenticated health check.

### Startup check: provision detection (#678)

Before either transport starts, the server counts the Penal Code's sections
(`data.provision_detection_check()`). If it finds none, the § nodes have been
retyped upstream and every provision-backed tool — `get_provision`,
`provision_history`, `who_references`, `references_of`,
`court_decisions_for_law`, `competent_authority_for_law`, and the
`num_provisions` counts — would answer "nothing found" instead of failing.
The server prints what broke and exits `1`. Set
`ESTLEG_ALLOW_EMPTY_PROVISIONS=1` to downgrade that to a warning and boot
anyway (the remaining tools still work). The same check is reported live by
`layers_available()` as the `provision_detection` row, whose `note` carries the
current § count.

> **Behind a proxy:** MCP's streamable-HTTP transport validates the `Host`
> header and answers a mismatch with `421 Invalid Host header`. The server
> therefore leaves that protection **off by default** (the bearer tokens are
> the access gate); set `ESTLEG_ALLOWED_HOSTS=your.domain` to turn it back on.

### Security model (#714)

- **stdio is open, HTTP fails closed.** stdio is a local process owned by the
  IDE user and needs no credentials (its audit consumer is `local`). The HTTP
  transport refuses to start without `ESTLEG_TOKENS` / `ESTLEG_TOKEN` (exit
  code `1`), unless `ESTLEG_ALLOW_ANONYMOUS_HTTP=1` is set for development. A
  malformed token map or rate-limit setting also fails at boot.
- **Per-consumer tokens.** Each consumer (a ministry, a Bürokratt
  integration, a developer) gets its own token, so access can be revoked one
  consumer at a time and every audit line names who asked. Tokens are compared
  in constant time; a duplicate name or a token shared by two consumers is a
  configuration error. Keep the JSON token file outside the image and mount it
  read-only.
- **Only `/healthz` is unauthenticated.** Everything else answers `401` with
  `WWW-Authenticate: Bearer` without a valid token.
- **Rate limit** (optional, per consumer) answers `429` with `Retry-After`
  and writes a `rate_limited` audit line.
- The ontology is public law: the gate exists to attribute and bound use of a
  shared endpoint, not to protect secret data. TLS is the reverse proxy's job.

### Audit log (#714)

One JSON line per tool call, on stderr or `ESTLEG_AUDIT_LOG`:

```json
{"ts":"2026-10-08T16:35:11.896Z","event":"tool_call","consumer":"rahandus","transport":"http","tool":"get_provision","args_sha256":"4f4abb16e264c78d8b6abc4631658ccd1a43700a1f14d59c1d3a9e6c3db7cab4","status":"ok","result_bytes":2971,"truncated":true,"latency_ms":15.3,"corpus_commit":"c0140d497e74f65bc4cd5a87487bac6786fc5792","corpus_ref":"v1.0.0","ontology_version":"1.0.0","server_version":"0.2.0"}
```

| Field | Meaning |
|-------|---------|
| `ts` | UTC timestamp, millisecond precision |
| `event` | `tool_call`, or `rate_limited` (HTTP middleware; carries `retry_after_seconds` instead of tool fields) |
| `consumer` | token-map name on HTTP (`default` for `ESTLEG_TOKEN`, `anonymous` with the dev opt-in), `local` on stdio |
| `transport` | `http` or `stdio` |
| `tool` | tool name |
| `args_sha256` | SHA-256 (or HMAC-SHA256 with `ESTLEG_AUDIT_HASH_KEY`) of the canonical JSON arguments, including `language`. **Raw arguments are never logged.** |
| `status` | `ok` or `error` (then `error` holds the exception class name only) |
| `result_bytes` | size of the JSON result, envelope included |
| `truncated` | true when any text was cut or a list overflowed its `limit` |
| `latency_ms` | wall time of the call |
| `corpus_commit`, `corpus_ref`, `ontology_version`, `server_version` | the same identity as the result's `snapshot` |

A call the SDK rejects during input-schema validation (a missing argument, a
`language` other than `et` / `en`) never reaches the tool and writes no audit
line; requests refused by the token gate are answered `401` and are not
tool calls either. Use the reverse proxy's access log for those.

### Docker / Coolify

`docker/Dockerfile` builds a self-contained image (python:3.13-slim + uv,
mirroring Seadusloome). The server runs as the **non-root user `estleg`
(uid/gid 10001)**. The corpus is **not** baked in: `docker/entrypoint.sh`
checks out the **release tag** `ESTLEG_CORPUS_REF` (default `v1.0.0`) onto a
persistent volume at boot, exports the commit it checked out as
`ESTLEG_CORPUS_COMMIT`, and `exec`s the server. Raise `ESTLEG_CORPUS_REF` to a
newer tag to serve a newer corpus; answers stay reproducible because every
result names the tag and commit.

Volume and LFS expectations:

- `/data` must be a persistent volume writable by uid 10001. A fresh named
  volume inherits that ownership from the image. A volume created by the old
  root-run image (or a root-owned bind mount) needs a one-off
  `docker run --rm -u 0 -v <volume>:/data <image> chown -R 10001:10001 /data`;
  the entrypoint detects an unwritable `/data` and prints exactly that.
- The clone is shallow (`--depth 1`) and skips Git LFS
  (`GIT_LFS_SKIP_SMUDGE=1`). The server reads only regular git blobs: the
  per-law `*_peep.json` files and the sidecars listed by `layers_available()`.
  The LFS artifacts (`combined_ontology.jsonld`, `similarity_index.json`,
  `analytical/analytical_overlay.jsonld`, `eurlex/eurlex_combined.jsonld`)
  stay pointers and are never needed; budget about 3.5 GB for the checkout.
- If the fetch of the tag fails at boot, the existing checkout is served and
  its commit is still reported in every `snapshot`.
- The new tools need the corpus layers they read (`provision_versions/`,
  `amendments/` with `resultedInVersion`, `eurlex/eurlex_directives_peep.json`,
  KOV `implementsCitation` targets). An older tag that predates a layer makes
  the corresponding tool answer empty or with a `note`; check
  `layers_available()` after changing `ESTLEG_CORPUS_REF`.

Coolify service settings:

- **Base Directory:** `mcp_server` · **Dockerfile:** `docker/Dockerfile`
- **Persistent volume** mounted at `/data` (keeps the corpus across redeploys)
- **Environment:** `ESTLEG_TOKENS=<consumer>=<long random secret>,...` (or the
  legacy `ESTLEG_TOKEN`); without credentials the container exits at boot.
  Optionally `ESTLEG_CORPUS_REF=<release tag>`, `ESTLEG_RATE_LIMIT=120/minute`,
  `ESTLEG_AUDIT_HASH_KEY=<secret>`, and `ESTLEG_ALLOWED_HOSTS=estleg.sixtyfour.ee`
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

The server registers **24 tools**. Law tools accept a title, official
abbreviation such as `KarS` / `VÕS` / `PKS`, or corpus slug; other tools accept
the term, EuroVoc subject, CELEX, or issuer described below. Lists are capped
by `limit` where noted, and long legal text is truncated explicitly
(`truncated` / `full_length`, lifted by `full_text=True`). Every tool also takes
`language` (`et` default, `en`).

An empty list means the loaded corpus returned no matches. It does not
establish that no real-world citation, authority, or legal obligation exists.
Unknown targets return a `note`. The [startup check](#startup-check-provision-detection-678)
catches the known provision-type regression using KarS, but does not certify
every law or overlay. Inspect `layers_available()` and source coverage when
assessing an empty result.

| Tool | What it answers | Example question |
|------|-----------------|------------------|
| `search_laws(query, limit=10)` | Find laws by title/abbreviation/slug | "Which laws mention 'töölepingu' / employment?" |
| `get_law(law, as_of=None)` | One-law overview + status + EuroVoc subjects; `as_of` adds a point-in-time section count | "Give me an overview of the Penal Code (KarS)." |
| `get_provision(law, paragraph, as_of=None, full_text=False)` | Read one § — current text, or as it stood on a past date | "What did § 13 of KarS say on 2010-06-15?" |
| `provision_history(law, paragraph, full_text=False)` | Full redaction timeline of one § (each window + text + the redaction's RT URL) | "How has § 13 of KarS changed over time?" |
| `who_references(law, paragraph=None)` | Incoming references (impact: what cites this) | "Which provisions reference § 60 of KarS?" |
| `references_of(law, paragraph=None)` | Outgoing references (what this cites) | "What does § 13 of KarS reference?" |
| `drafts_affecting_law(law, limit=20)` | Pending bills that would change the law | "What pending bills affect the Health Services Organisation Act?" |
| `court_decisions_for_law(law, limit=20)` | Riigikohus decisions interpreting the law | "Which Supreme Court cases interpret KarS?" |
| `sanctions_for_law(law, limit=50)` | Recorded penalties for the law | "What penalties does KarS define?" |
| `competent_authority_for_law(law)` | Which institutions enforce/administer it | "Which authority enforces the Personal Data Protection Act?" |
| `transposition(query)` | EU directive ↔ Estonian law, both directions | "Which Estonian law transposes EU directive 31990L0314?" (or pass a law name to go the other way) |
| `eu_case_law_for_directive(celex, limit=20)` | CURIA decisions that mention a directive CELEX | "Which CURIA judgments interpret 32000L0060?" |
| `define_term(term, limit=10)` | Look up a legal term in the concepts overlay | "What does 'elatis' mean in the ontology?" |
| `laws_for_subject(subject, limit=20)` | Find laws by EuroVoc subject IRI or keyword | "Which laws are tagged with social security?" |
| `amendment_history(law, limit=50)` | Recorded effected amendment events | "What amendments has KarS already received?" |
| `harmonisation_for_directive(celex, limit=20)` | Cross-border measures sharing a directive CELEX | "Which neighbouring states also transposed 32000L0060?" |
| `layers_available()` | Which sidecars MCP reads vs excludes | "Does MCP load the harmonisation / similarity overlays?" |
| `regulations_for_law(law, limit=50)` | Regulations (määrused) issued under / implementing a statute | "Which regulations are issued under the Local Government Organisation Act (KOKS)?" |
| `get_regulation(name)` | One-regulation overview (issuer, parent statute, status, citation) | "Give me an overview of regulation t302269." |
| `regulations_by_issuer(institution, limit=50)` | All regulations enacted by a body (ministry, government, municipality) | "Which regulations has the Minister of Finance (Rahandusminister) issued?" |
| `what_changed(act_or_provision, since, until=None, limit=100)` | Provision redactions that took effect in a window (amended / added / ceased) + the effected amendment events | "What changed in KarS during 2014?" |
| `transposition_gaps(directive=None, limit=50)` | In-force directives with no transposition edge in the corpus (#701 flag), or one directive's status | "Which directives in force have no transposing law in the corpus?" |
| `kov_regulations_citing(law_or_provision, paragraph=None, limit=50)` | Municipal regulations citing a law / § as their basis (`implementsCitation`) or issued under it | "Which municipal regulations rely on KOKS § 22?" |
| `explain_provision(iri, full_text=False)` | One § in one answer: text, history, references in/out, court decisions, authorities, sanctions, KOV citations, a short explanation | "Explain KarS § 424." |

### New tools (#714)

- **`what_changed`** reads the `provision_versions/` timelines and links each
  change to the amendment event whose `estleg:resultedInVersion` produced it.
  `change` is `amended`, `added` (first redaction after the law's recorded
  history began), `first_recorded` (the baseline at the start of the recorded
  history, not a legislative change) or `ceased` (`date` is the first day it
  was no longer in force). `until` defaults to today (UTC); both bounds are
  inclusive. Accepts a law, `"KarS § 424"`, or a provision IRI (also one no
  longer in the current graph).
- **`transposition_gaps`** applies the #701 `noTranspositionEdgeInCorpus`
  rule from the regular-blob sources (`eurlex/eurlex_directives_peep.json`
  `inForce` + `transposedBy`, plus `reports/transposition_mapping.json`)
  instead of the LFS analytical overlay. It is a **corpus-coverage fact, not a
  legal finding**, and every answer says so in `caveat`. Directives whose
  force is unknown are never reported as gaps.
- **`kov_regulations_citing`** resolves each municipal regulation's
  `implementsCitation` target (`estleg:KOKS_Par_22_Lg_1`) to a law and §. At
  law level it also includes regulations only `issuedUnder` the law
  (`matched_by: "issuedUnder"`). Citation targets whose law prefix the
  resolver cannot map (for example `KOFS`, `KalmS`) are not attributed.
- **`explain_provision`** composes the existing lenses for one §. Lists are
  capped at 10 rows; `counts` carries the totals.

### Results: snapshot, language, truncation (#714)

**Snapshot envelope.** A dict result gains a top-level `snapshot`; a list
result is returned as `{"result": [...], "snapshot": {...}}` (the SDK already
put list results under `result`, so structured consumers keep reading it):

```json
"snapshot": {
  "corpus_commit": "c0140d497e74f65bc4cd5a87487bac6786fc5792",
  "corpus_ref": "v1.0.0",
  "ontology_version": "1.0.0",
  "evaluation_date": "2026-10-08",
  "server_version": "0.2.0",
  "language": "et"
}
```

`corpus_commit` comes from `ESTLEG_CORPUS_COMMIT` or the checkout's `.git`,
`corpus_ref` from `ESTLEG_CORPUS_REF` ("" for a local checkout), and
`evaluation_date` is the UTC day the call was answered (status words such as
"in force" are relative to it). The Python functions (`server.get_law` ...)
return the bare payload; the envelope is added by the MCP wrapper.

**Language.** Tool descriptions are Estonian first, followed by the English
contract. `language` (`et` default, `en`) selects the human text inside a
result: `note`s, overflow hints, the `transposition_gaps` caveat and the
`explain_provision` explanation. Field names, enum values, IRIs and URLs are
never translated.

**Truncation.** Legal text is cut at about 2000 characters, always
explicitly: `get_provision` (current and `as_of`), `provision_history` rows and
`explain_provision` carry `truncated` and `full_length`, and `full_text=True`
lifts the cap on all of them. Capped lists say so with `truncated` or a
trailing `{note, overflow, total_available}` row.

### Citation derivation

- **Law / provision** — from the act's `dcterms:source` IRI
  (`…/akt/<id>.xml`), with the trailing `.xml` stripped to the human URL.
  An `owl:sameAs` is used only as a fallback.
- **`rt_url` is host-guarded** (issue #680): it is a `riigiteataja.ee` URL (or
  a subdomain) or it is `""` — never another host. A handful of acts, KarS and
  VÕS among them, carry no `dcterms:source` at all and only an `owl:sameAs`
  pointing at Wikidata; that used to be returned under a field every tool
  documents as *the official riigiteataja.ee URL*. An empty string is the
  honest answer. Restoring those sources is a producer-side ticket, so until
  it lands `get_law("KarS")["rt_url"]` and the `rt_url` on every
  `sanctions_for_law("KarS")` row are `""`. The presence of sanction records
  does not establish complete extraction coverage.
- **`external_ids`** — `get_law` and `search_laws` return the non-riigiteataja
  identifiers the act does link to, keyed by host family, e.g.
  `{"wikidata": "http://www.wikidata.org/entity/Q2352833"}`. Nothing is lost
  by the host guard; it just stops being labelled a riigiteataja citation. No
  ELI identifier is emitted: act nodes carry no `estleg:eli` predicate, and
  `eli:is_about` holds EuroVoc subject IRIs (already returned as
  `eurovoc_subjects`), not an identifier for the act.
- **Regulation (määrus)** — same derivation and the same host guard, from the
  regulation's own `dcterms:source`; `regulations_for_law` / `get_regulation`
  also resolve the parent statute (via `estleg:issuedUnder`) to its
  riigiteataja URL.
- **Historical redaction** — the version's own `estleg:rtUrl` (host-guarded):
  `provision_history` rows' `rt_url`, `get_provision(as_of=…)`'s
  `redaction_rt_url`, and `what_changed` rows.
- **Citation on every row (#714)** — `define_term` (`defined_in`,
  `source_act`, `rt_url` of the defining act or regulation),
  `laws_for_subject` (`rt_url`), `amendment_history` (`rt_reference` as
  recorded, `rt_url` of the amending act when that reference is an RT URL,
  else of the amended act), `competent_authority_for_law` (`institution_id`,
  `rt_url` of the law), `harmonisation_for_directive` (`eurlex_url` of the
  directive, `rt_url` of the Estonian act harmonised).
- **Court decision** — the decision's `estleg:decisionLink` (riigikohus.ee).
- **Draft** — the draft's EIS link (eelnoud.valitsus.ee).
- **EU** — CELEX → `https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:<celex>`.
- **CURIA** — the decision's `estleg:eurLexLink` (EUR-Lex ET TXT; falls back to `estleg:curiaLink`), else the same CELEX URL.

### Overlay coverage (#540)

MCP reads **per-file sidecars**, never `combined_ontology.jsonld`. `layers_available()`
returns this table at runtime (`status` = `wired` / `loadable` / `excluded`).

| Sidecar | Status | How MCP reaches it |
|---------|--------|--------------------|
| Law peeps + `INDEX.json` | wired | `search_laws`, `get_law`, `get_provision`, references |
| § node `@type` (`provision_detection`) | wired | not a sidecar: the live § count behind every provision-backed tool (#678) |
| `concepts/concepts_combined.jsonld` | wired | `define_term` (overlay loader; not the law peeps) |
| `harmonisation/…/harm_<celex>.json` | loadable | `harmonisation_for_directive` opens **one** file when asked |
| `sanctions/`, `amendments/`, `regulations/` | wired | `sanctions_for_law`, `amendment_history`, `what_changed`, regulation tools, `kov_regulations_citing` |
| `provision_versions/` | wired | `get_provision(as_of=…)`, `provision_history`, `what_changed`, `explain_provision` |
| `riigikohus/`, `eelnoud/`, `institutions/` | wired | court / draft / authority tools |
| `curia/curia_*_peep.json` | wired | `eu_case_law_for_directive` (CELEX substring; no `#418` interprets edges) |
| `transposition_mapping.json` | wired | `transposition`, `transposition_gaps` |
| `eurlex/eurlex_directives_peep.json` | wired | `transposition_gaps` (`inForce` + `transposedBy`) |
| other `eurlex/` peeps, `eurlex_combined.jsonld` | excluded | CELEX/EUR-Lex URLs only; the combined file is Git LFS |
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
directly against the real corpus in `tests/test_data.py`. The other modules:
`server.py` (tools + the wire wrapper), `i18n.py` (Estonian descriptions and
result text), `provenance.py` (snapshot), `audit.py` (audit line),
`security.py` (token map, rate limit, HTTP middleware). `tests/test_hardening.py`
covers the security / audit / envelope contract without the corpus;
`tests/test_new_tools.py` covers the four new tools on fixtures and on the
corpus.

## Follow-up: semantic search (v1 limitation)

This version does **not** offer semantic / similarity search. The corpus's
`krr_outputs/reports/similarity_index.json` and `krr_outputs/combined_ontology.jsonld`
ship as Git LFS pointers and are not real JSON in a plain clone, so the server
reads per-file and never loads the combined graph. A semantic-search tool is a
natural follow-up, gated on `git lfs pull` to materialise those artifacts (and
an embedding/index step over the provision summaries).
