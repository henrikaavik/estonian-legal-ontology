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

## Load surfaces (three products)

| Surface | What you load | Use for |
|---|---|---|
| Combined-only | `krr_outputs/combined_ontology.jsonld` | Law graph + overlay nodes + typed stubs. **No** provision-version text (`hasVersion` is stripped). |
| Full public RDF | combined + `PUBLIC_LOAD_SUBDIRS` (`eelnoud`, `riigikohus`, `kohtud` (sample, `estleg:isSampleData`), `curia`, `eurlex`, `concepts`, `sanctions`, `amendments`, `institutions`, `provision_versions`, `annotations`, `harmonisation`, `regulations`) + `data/ehak/historical_municipalities.jsonld` | Full bodies, point-in-time, KOV provisions. Seadusloome / Jena path. |
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
  JSONL. Do not hand-edit. Rebuild with `scripts/fix_all_issues.py`
  (combined + INDEX) after enrichment.

EuroVoc writes `eurovoc/eurovoc_overlay.jsonld` by default; `--write-peeps`
opts into the legacy in-place path. Deontic, target-group, and some similarity
passes still mutate peeps. Combined merges selected overlay directories at
build time; full separation and DAG input coverage remain open (#697/#704).

## Pipeline

`scripts/run_all_integration.py` is the **enrich + combine + validate**
DAG (18 declared steps, serial by default). It does **not** ingest from Riigi Teataja /
EUR-Lex. Ingest generators (`generate_all_laws.py`, regulations, courts,
drafts, EU) are a prior stage. `--release` validates whatever peeps are
on disk.

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
  is **not** live — that is `#728`.

Keep new consumer paths aligned with the three load surfaces above.

## What not to change without a MAJOR version

- Slash namespace and underscore local names.
- `estleg:partOfAct` as the act⇄provision join (not `sourceAct` literals).
- `MunicipalRegulation` as a sibling of `NationalRegulation`, not a subclass.
- Drafts as `ProposedAmendment`, not effected `AmendmentEvent`.
- Combined stub-closure + overlay merge; version edges stay on the full surface.
