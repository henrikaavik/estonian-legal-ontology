# Architecture — Estonian Legal Ontology

This is the consumer-facing system document for `estleg` (issue #475).
It names the load surfaces, the write vs derived artifacts, and the
query paths. It does not replace `AGENTS.md` (working conventions) or
`docs/SCHEMA_REFERENCE.md` (T-Box / SPARQL).

## Code / data distribution (#480)

**Decision: keep-LFS, release-asset-first.** This is the executed
choice, not a follow-up.

- **Consumer path:** GitHub Release `v1.0.0` assets
  (`combined_ontology.jsonld.gz` and the other combined dumps). That is
  what `metadata.jsonld` `dcat:downloadURL`s cite.
- **Contributor / CI path:** the same files stay in this git tree via
  Git LFS (`.gitattributes`) so `pytest`, `validate_all`, and
  `estleg_client.load_law` work on a clone.
- **Rejected:** (a) a second data remote — extra operational surface
  once the release exists. (c) `git lfs migrate` history rewrite —
  invalidates every existing clone and does not shrink already-pushed
  blobs. (b-prune) dropping regenerable LFS blobs from git — would
  break CI and `load_law` on a fresh clone.

Clone size (~2.4 GB) is an accepted cost of keeping CI and the Python
client on the same tree. The ticket's "measurably smaller clone" goal
is declined.

## Identity

- **Namespace:** `https://w3id.org/estleg/` (slash). Compact CURIE
  `estleg:<LocalName>`. Law nodes use
  `estleg:<ABBREV>_<segment>…` (`AGENTS.md`).
- **Graph primary keys are minted `estleg:` `@id`s.** Official RT / CELEX
  / ECLI identifiers live on properties and `owl:sameAs`, not as `@id`.
- **Version:** `estleg_common.ONTOLOGY_VERSION` (lockstep with
  `pyproject.toml` and `metadata.jsonld` `owl:versionInfo`).
  `versionIRI` is `https://w3id.org/estleg/<version>`.

### IRI resolution (#728, pilot)

`w3id/estleg/.htaccess` stages content negotiation: a term IRI asked for as
RDF or HTML `303`s to `https://estleg.sixtyfour.ee/id/<local>`, and
`/estleg/vocabulary` to `/vocabulary`. Those are public, read-only routes of
the MCP HTTP app (`mcp_server/estleg_mcp/resolver.py`, `resolver_web.py`):
they find the one file that defines the node from its `@id` family and return
JSON-LD, Turtle or an HTML page. They read the same per-file load surface as
the MCP tools, never the combined graph. The rules are staged, not live, until
the maintainer re-submits them to perma-id; the operator question is #730
(`docs/proposals/2026-10-w3id-content-negotiation.md`).

## Load surfaces (three products)

| Surface | What you load | Use for |
|---|---|---|
| Combined-only | `krr_outputs/combined_ontology.jsonld` | Law graph + overlay nodes + typed stubs. **No** provision-version text (`hasVersion` is stripped). |
| Full public RDF | combined + `PUBLIC_LOAD_SUBDIRS` (`eelnoud`, `riigikohus`, `kohtud` (sample, `estleg:isSampleData`), `curia`, `eurlex`, `concepts`, `sanctions`, `amendments`, `institutions`, `provision_versions`, `annotations`, `harmonisation`, `regulations`, `analytical`, `eurovoc`) + `data/ehak/historical_municipalities.jsonld` | Full bodies, point-in-time, KOV provisions. Seadusloome / Jena path. |
| Retrieval projection | `krr_outputs/retrieval/` JSONL (derived, not SHACL) | RAG / untruncated § text as of a date. |

Combined is **closed via stubs** (`estleg:isStubNode`). Class queries on
combined-only must exclude stubs:

```sparql
FILTER NOT EXISTS { ?x estleg:isStubNode true }
```

Amendment nodes are **merged** into combined. Version forward-edges are
**removed**, not left dangling.

## Sources of record vs derived

- **Writable sources:** root `*_peep.json`, subcorpus peeps, overlay
  sidecars (`sanctions/`, `institutions/`, `concepts/`, `annotations/`,
  `amendments/`, `provision_versions/`).
- **Derived:** `combined_ontology.jsonld`, `INDEX.json`, retrieval
  JSONL, the RDF dumps and the release assets. Do not hand-edit. Rebuild with
  `scripts/build_release_artifacts.py` (combined + INDEX) after enrichment and
  `scripts/build_release_assets.py` for the downloads.

EuroVoc writes `eurovoc/eurovoc_overlay.jsonld` by default; `--write-peeps`
opts into the legacy in-place path. Deontic, target-group, and some similarity
passes still mutate peeps. Combined merges selected overlay directories at
build time; full separation of the peep-mutating passes remains open (#697).

## Pipeline

`scripts/run_all_integration.py` is the **enrich + combine + package +
validate** DAG (#704, #705): 29 declared steps in four tiers, serial by
default. Every layer that ships has a declared producer step with its reads and
writes, and `validate_dag` rejects a derived read whose writer is not one of
the reader's dependencies.

- **Ingest** (3 steps): the provision-version layer from Riigi Teataja
  redactions, the state-regulation version snapshots and the oiguskantsler
  annotations. They need the network, so the runner records them and uses
  the committed outputs unless `--with-ingest` is given. The bulk ingest
  generators (`generate_all_laws.py`, regulations, courts, drafts, EU) remain
  a prior stage outside the DAG.
- **Enrichment** (23 steps): the offline corpus passes, the sub-corpus
  aggregate rebuilds, and the version-derived layers. These are the #429
  amendment/version join, act `temporalStatus`, act expressions and court
  interpretation staleness.
- **Build** (1 step): `build_release_artifacts.py` rebuilds
  `combined_ontology.jsonld` and `INDEX.json`. The #520 inverse/closure edges
  and #521 analytical stamps run inside it. It is the last enrichment-side
  step.
- **Package** (2 steps): the analytical overlay file, then
  `build_release_assets.py`. It writes the gzipped combined dumps, the
  streamed `.nt`/`.nq`/`.ttl`, the seven-graph `estleg_all.nq.gz`,
  `chunks.jsonl.gz`, `SHA256SUMS` and the catalogue's per-asset
  `dcat:byteSize` / `spdx:checksum` into the repo-root `release/` directory.

`--release` validates whatever peeps are on disk after the DAG runs, and the
release manifest re-hashes every asset in `release/SHA256SUMS`. CI installs
with `constraints.txt` and checks that every coverage report's
`pipeline_version` is a commit (`--check-pipeline-versions`). See
[RELEASE.md](RELEASE.md).

Gates: `ruff` → `pytest` → `validate_all.py` →
`shacl_validate_all.py --all` (RDFS) →
`validate_seadusloome_sync.py` (no inference). Those two SHACL modes
are a documented two-surface policy, not a bug.

## Query paths

| Path | Loads | Audience |
|---|---|---|
| MCP (`mcp_server/`) | Per-file peeps + sidecars. Never the flagship combined graph. | Chat / IDE. Configured endpoint: `https://estleg.sixtyfour.ee/mcp`; deployment revision must be checked separately. |
| Seadusloome SPARQL | Full public RDF, `inference=none` | Product site |
| Retrieval JSONL | Flattened provision+version+act | RAG |

They are **not interchangeable**. MCP truncates legal text; combined
cannot answer `as_of` provision text; SPARQL can join corpora MCP does
not expose as tools.

## Release status and follow-ups

`v1.0.0` was published on 2026-08-19. September Tier 0 and #702 fixes are
merged to `main`, but are not a new tagged release. Required checks pass on
the reviewed PRs; full corpus/SHACL conformance remains unresolved. See
[project status](README.md#project-status) and [validation evidence](VALIDATION_REPORT.md).

- `#473` Zenodo DOI — GitHub Release exists; no DOI yet.
- `#516` w3id.org PURL — **done**. PR
  <https://github.com/perma-id/w3id.org/pull/6575> merged 2026-08-19:
  `https://w3id.org/estleg/` 302-redirects to the repository and
  `https://w3id.org/estleg/1.0.0` 302-redirects to the tagged release
  (`releases/tag/v1.0.0`). Content negotiation (RDF vs HTML per `Accept`)
  is staged and piloted but **not** live — that is `#728` (see
  [IRI resolution](#iri-resolution-728-pilot)).

Keep new consumer paths aligned with the three load surfaces above.

## What not to change without a MAJOR version

- Slash namespace and underscore local names.
- `estleg:partOfAct` as the act⇄provision join (not `sourceAct` literals).
- `MunicipalRegulation` as a sibling of `NationalRegulation`, not a subclass.
- Drafts as `ProposedAmendment`, not effected `AmendmentEvent`.
- Combined stub-closure + overlay merge; version edges stay on the full surface.
