# Proposal: content-negotiated node descriptions behind `w3id.org/estleg/`

| ADR field | Value |
|---|---|
| Status | **Proposed**, with a built and measured repository-side pilot — 2026-10-08 |
| Ticket | #728 (review item 3.5), epic #676 |
| Deciders | Maintainer; accountable owner and operator **not yet named** (see [Open decisions](#open-decisions-maintainer-only)) |
| Depends on | #730 (institutional home: who operates the host), #707 / #708 (RT ELI identity, for the official identifier on a page) |
| Related | #725 Sätla reference resolution (`2026-10-satla-reference-resolution.md`), which needs dereferenceable IRIs |
| Measured against | `tier1/wave5b` at `ff33ada958`; dataset `owl:versionInfo` 1.0.0; estleg-mcp 0.2.0 |

**Kokkuvõte.** `https://w3id.org/estleg/<nimi>` on iga sõlme püsiidentifikaator,
kuid täna suunab see kõik päringud GitHubi hoidlasse. See ettepanek ja selle
juurde ehitatud piloot lisavad sisu läbirääkimise: masin saab sama aadressi
alt JSON-LD või Turtle kirjelduse, inimene HTML-lehe. Lahendaja töötab
olemasoleva estleg-mcp HTTP-teenuse avaliku, ainult lugemiseks mõeldud osana.
Repositooriumi pool on valmis ja testitud. Avalikuks muutub see alles siis, kui
hooldaja esitab w3id.org-le muudatuse ja otsustab, kes teenust käitab (#730).

## Context

### What is live

The namespace was registered in perma-id/w3id.org PR #6575 (merged
2026-08-19). Every IRI 302s to the repository whatever the `Accept` header, and
`/estleg/1.0.0` 302s to the release page. Before this work the staged
`.htaccess` held one commented-out content-negotiation block that would have
sent **every** term to the whole 23.7 MB dump, and no `/vocabulary` rule. The
ticket's own re-validation (2026-09-04) says the real work is "hosting + a
per-node lookup + a perma-id PR", blocked on naming an operator.

### What exists to build on

- The **estleg-mcp streamable-HTTP transport** is the only operated HTTP
  surface the project has (`https://estleg.sixtyfour.ee/mcp`). It is
  fail-closed behind per-consumer tokens with an audit line and an optional
  per-consumer rate limit (#714).
- Its **data layer** reads per-file peeps and sidecars, never the combined
  graph, and already holds indexes for drafts, Riigikohus decisions,
  regulations and the law-abbreviation registry.
- `krr_outputs/controlled_vocabulary.jsonld` describes every class, property
  and controlled-scheme member (472 nodes).
- An earlier closeout draft (2026-09-03) verified that GitHub Release assets
  are served as `application/octet-stream`, which rdflib refuses, so RDF
  redirect targets must be raw blobs at a tag or the resolver itself.

## Decision (proposed)

1. **Resolve at the existing HTTP app, not at w3id.** w3id.org cannot do a
   per-node lookup: the combined graph alone has about 260,000 instance nodes
   in 933 prefix families (closeout count, 2026-09-03), and `RewriteMap` is
   unavailable there. w3id only `303`s a
   one-segment local name to `https://<host>/id/<local>`; the client re-sends
   its own `Accept` and the host negotiates.
2. **The routes are public, read-only and anonymous**: `GET /id/{local}` and
   `GET /vocabulary`. They bypass the token gate on exactly those paths and
   methods; everything else stays fail-closed. All anonymous hits share one
   rate-limit bucket and write the normal audit line (`consumer: anonymous`,
   `tool: resolve`).
3. **Representations:** compact JSON-LD (the node as stored, its `estleg:`
   references as `@id`, with labels where cheaply known), Turtle, N-Triples
   and RDF/XML through rdflib, and a dependency-free HTML page with the
   official links, every property, outgoing `/id/…` anchors and
   `<link rel="alternate">` to the RDF forms. `Vary: Accept`, a weak `ETag`
   per corpus commit and representation, `Cache-Control: public, max-age=3600`
   and `Access-Control-Allow-Origin: *` on every answer.
4. **The w3id rule set** (`w3id/estleg/.htaccess`) keeps the namespace root
   and anything without an RDF or HTML `Accept` on the existing `302`, so
   switching on content negotiation changes nothing for those clients.
5. **Do not open the perma-id PR until an operator is named** (#730). A
   persistent namespace that `303`s to a host is a promise to keep that host
   answering.

## What the pilot built

| Piece | Where |
|---|---|
| Local-name → node lookup by `@id` family (laws and their structure, regulations, provision versions, sanctions, amendments, Riigikohus, eelnõud, EUR-Lex, CURIA, institutions, vocabulary) plus `KarS_Par_141`-style aliases that `303` to the canonical name | `mcp_server/estleg_mcp/resolver.py` |
| Content negotiation, JSON-LD / RDF / HTML rendering, `/id/{local}`, `/id/`, `/vocabulary`, audit line, ETag | `mcp_server/estleg_mcp/resolver_web.py` |
| Token-gate exemption for exactly those paths and `GET`/`HEAD`, shared anonymous bucket, `ESTLEG_RESOLVER=off` switch | `mcp_server/estleg_mcp/security.py`, `server.py` |
| Real 303 rule set with the host as one variable | `w3id/estleg/.htaccess` |
| The exact perma-id PR (title, body, diff) and post-merge checks | `w3id/estleg/README.md` |
| Tests: routes per format and family, 404/406, anonymous access while `/mcp` stays `401`, audit line, both rate-limit paths, the `.htaccess` rule classes | `mcp_server/tests/test_resolver.py`, `tests/test_w3id_htaccess.py` |

## Measured results (repository-side pilot)

Measured on 2026-10-08 in-process through the Starlette test client against
the full corpus (no network, no TLS, one worker). The sample is drawn at
random (seed 728) and stratified: half law nodes from `INDEX.json` files, then
regulations, provision versions, Riigikohus, eelnõud, EU acts and CURIA,
institutions and vocabulary terms in fixed shares. "Cold" is the first pass
in a fresh process; the per-file cache holds 48 files, so a diverse sample
keeps missing it.

| Sample | Pass | Median | p95 | Max | Misses |
|---|---|--:|--:|--:|--:|
| 200 IRIs | cold, Turtle | 2.3 ms | 98 ms | 1,205 ms | 0 |
| 200 IRIs | warm, Turtle / JSON-LD / HTML | 2.2 / 1.3 / 2.7 ms | 69 / 53 / 59 ms | 350 ms | 0 |
| 1,000 IRIs | cold, Turtle | 2.2 ms | 73 ms | 1,045 ms | 3 (0.3 %) |
| 1,000 IRIs | warm, Turtle / JSON-LD / HTML | 2.2 / 1.2 / 2.6 ms | 67 / 67 / 64 ms | 351 ms | 3 |

- **Misses.** The first 200-IRI run missed 54 of 200: 53 of the 100 law nodes
  and one provision version, all of laws whose act root is named `<prefix>_Map` without a year (treaty laws with truncated-slug
  prefixes, and `KortTS`, `MooteS`, `PandiKS` and others). The data layer's
  act-prefix index strips only `_Map_<year>`; the resolver now keeps its own
  prefix index and finds them all. Of the three misses left in 1,000, one
  `Concept_osa6_time` is defined inside a law peep without any law prefix,
  sampled twice. The other was a regulation filed under a different
  `_t<id>` than its own `terviktekstId` (21 of 14,871 files); the record-index
  fallback added after this run resolves it.
- **Cold costs** are one-time per process: the first Riigikohus decision
  builds the decision index (about 0.8 s), the first unrecognised law prefix
  builds the act-prefix index (about 1 s), and the first EUR-Lex or CURIA hit
  parses a 20–70 MB file (about 0.3 s).
- **Sizes** over the 200 sample (median / max): HTML 4.4 / 107 KB, JSON-LD
  2.2 / 103 KB, Turtle 1.3 / 100 KB.
- **Official links on the HTML page** over the 200 sample: a Riigi Teataja link
  on 89 of 100 law nodes, 25 of 25 regulation nodes and 14 of 15 provision
  versions; every draft, EU act and CURIA decision links EIS or EUR-Lex.
  Overall 174 of 200 pages carry at least one official link.
- **Memory.** Peak RSS of the 1,000-IRI run was 2.1 GB, including the sampling
  script's own file reads. The cache is bounded by file count, not bytes:
  the 48 MB EUR-Lex regulations file alone raised peak RSS by 384 MB when
  parsed.
- **Headers** on every 200: `Vary: Accept`, `Cache-Control: public,
  max-age=3600`, a weak `ETag` such as `W/"ff33ada95886-0.2.0-…"`,
  `Access-Control-Allow-Origin: *`, `Link: <w3id IRI>; rel="canonical"`.

## Pilot plan for the live phase

Run only after the operator decision. All measurements against the public
host, through w3id once the PR is merged:

1. **Sample.** 1,000 real IRIs: 500 from `INDEX.json` law files with § nodes
   over-weighted, the rest from regulations, provision versions, Riigikohus,
   eelnõud, EU acts, institutions and vocabulary, as above.
2. **Resolution latency.** Median and p95 of the full chain (w3id `303` plus
   the resolver `200`) for Turtle, JSON-LD and HTML, cold and warm.
   Target: p95 under 500 ms warm.
3. **404 rate.** Target under 1 % on the sample. Every miss is classified as
   a naming-scheme defect or a resolver gap.
4. **Cache headers.** Confirm that `Vary`, `ETag` and `Cache-Control` survive
   the reverse proxy and that a conditional `GET` gets `304`.
5. **Access.** Confirm that `/mcp` still answers `401` without a token, and
   watch the anonymous bucket's `rate_limited` audit lines for a week.
6. **Validation.** `rdflib.Graph().parse("https://w3id.org/estleg/<local>")`
   works for every family.

## Identity prerequisite (#707 / #708)

The HTML page links to Riigi Teataja when the corpus records an RT source, but
it cannot cite the **official Estonian identifier**. The Sätla proposal
measured the act roots on 2026-10-08: 984 of 1,119 roots have a riigiteataja.ee
`dcterms:source` (87.9 %), and **0** carry an Estonian ELI. Until #707
establishes the RT ELI identity join, and #708 materialises it, a page can say
"source: Riigi Teataja act 123…", but not "this node is ELI …". The pilot
therefore labels the link "Riigi Teataja", never "ELI", and the EU acts' ELI
links come straight from EUR-Lex.

## Operating model options

| Option | What it is | For | Against |
|---|---|---|---|
| A. Project-hosted on the MCP host | The pilot as built: `/id` and `/vocabulary` on `estleg.sixtyfour.ee` | Built and tested; one deployment; reuses audit, limits and health check | Bus factor 1; personal domain; no service level |
| B. Static pages | Pre-render per-node JSON-LD/Turtle/HTML at release time and publish them (GitHub Pages or object storage); w3id `303`s to the static path | No server; cacheable forever per release; mirrorable | More than 260,000 nodes × 3 formats per release; GitHub Pages size limits; no aliases or `406`, extension-based negotiation only |
| C. Institutional host | The institution chosen under #730 runs the same app or the static set under its own domain | Durable; matches public-sector expectations for persistent IRIs | Needs #730 and a counterpart |

**Recommendation.** Run A as the bounded pilot, labelled as having no service
level, and only after the maintainer accepts that role explicitly. Plan for C.
The `RESOLVER=` variable makes the move from A to C one line in a w3id PR,
because the IRIs never contain the host.

## Risks

- **A persistent namespace pointing at a personal host.** If the host lapses,
  every term IRI `303`s into an error. Mitigation: the rollback in
  `w3id/estleg/README.md` restores the `302` in one PR. A second resolver host
  would remove the single point of failure.
- **Anonymous load on a shared server.** Crawlers now reach the same process
  that serves token holders. Mitigation: one shared anonymous bucket, and
  `ESTLEG_RESOLVER=off` (which then needs the w3id rollback too, or IRIs
  `303` into `401`).
- **Memory.** The per-file cache is bounded by count, not size; a crawler
  walking EUR-Lex and CURIA can hold several large files at once. Measure RSS
  in the live pilot and cap the cache by bytes if needed.
- **Representation drift.** The JSON-LD is the stored node, so a generator
  change changes the description. The `ETag` carries the corpus commit, and
  clients can see which snapshot they got.
- **Implied authority.** An HTML page under a stable IRI can be mistaken for
  the official text. Every page links the RT or EUR-Lex source, and the
  footer names the corpus commit.
- **rdflib in production.** The container installs only the `http` extra,
  which does not include rdflib yet. Until it does, RDF answers `406` while
  JSON-LD and HTML work.

## Open decisions (maintainer only)

1. **Owner and counterpart.** Name the accountable owner of the resolver.
   The ticket also asks for an external counterpart; none is identified yet.
2. **Operator (#730).** Accept option A for the pilot, or wait for C.
3. **Open the perma-id PR.** Only the listed maintainer can. The exact text is
   in `w3id/estleg/README.md`. Its prerequisites are a deployed build with
   rdflib in the image and the curl checks there.
4. **Anonymous tier.** Accept a public, unauthenticated surface on the MCP
   host and set `ESTLEG_RESOLVER_RATE_LIMIT`. The pilot defaults to the
   routes being on.
5. **Contact address.** The `.htaccess` maintainer contact is a personal
   address; decide whether that changes with #730.

## Feedback to the backlog

- **Data layer.** `_strip_map_suffix` in `mcp_server/estleg_mcp/data.py`
  leaves a bare `_Map`, so `law_slug_for_iri` cannot place provisions of
  laws whose act root is `<prefix>_Map` (53 of 100 random law nodes).
  `citation_url_for_iri` then returns no RT link for them in the MCP tools
  too. The resolver works around it; fixing it in the data layer changes tool
  output, so it needs its own ticket and tests.
- **Naming scheme.** Some `Concept_*` nodes are defined inside law peeps with
  no law prefix (`Concept_osa6_time` in `tsiviilseadustik_osa6_peep.json`).
  They cannot be placed from the IRI alone.
- **Regulation file names.** 21 regulation peeps carry a `_t<id>` that is not
  their `terviktekstId`.
- **Packaging.** Add rdflib to the estleg-mcp `http` extra.
- **Static T-Box serialisations.** `controlled_vocabulary.ttl` and `.nt` would
  let the version-IRI rule serve Turtle at a tag without the resolver.
