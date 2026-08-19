# Architecture — Estonian Legal Ontology

This is the consumer-facing system document for `estleg` (issue #475).
It names the load surfaces, the write vs derived artifacts, and the
query paths. It does not replace `AGENTS.md` (working conventions) or
`docs/SCHEMA_REFERENCE.md` (T-Box / SPARQL).

## Code / data distribution (#480)

**Decision: release-asset-first (option b).** Combined JSON-LD, the
eurlex/curia/eelnoud combined files, SHACL, INDEX, and the controlled
vocabulary ship as GitHub Release assets (`v1.0.0`). That is the
consumer download path (`#473`). The git tree still carries the same
files via Git LFS so CI and `estleg_client.load_law` keep working.
Dropping regenerable LFS blobs from git is a follow-up after consumers
move to the release. Option (a) (separate data repo) and option (c)
(`git lfs migrate` history rewrite) are deferred: a rewrite invalidates
existing clones, and a second remote is not required once the release
exists.

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
| Full public RDF | combined + `PUBLIC_LOAD_SUBDIRS` (`eelnoud`, `riigikohus`, `kohtud`, `curia`, `eurlex`, `concepts`, `sanctions`, `amendments`, `institutions`, `provision_versions`, `annotations`, `harmonisation`, `regulations`) + `data/ehak/` | Full bodies, point-in-time, KOV provisions. Seadusloome / Jena path. |
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

Heuristic layers (EuroVoc, deontic, target group, similarity scores)
currently still mutate peeps. Combined already merges some overlay
directories at publish time.

## Pipeline

`scripts/run_all_integration.py` is the **enrich + combine + validate**
DAG (16 steps, serial). It does **not** ingest from Riigi Teataja /
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
| MCP (`mcp_server/`) | Per-file peeps + sidecars. Never combined / LFS. | Chat / IDE. Live: `https://estleg.sixtyfour.ee/mcp` |
| Seadusloome SPARQL | Full public RDF, `inference=none` | Product site |
| Retrieval JSONL | Flattened provision+version+act | RAG |

They are **not interchangeable**. MCP truncates legal text; combined
cannot answer `as_of` provision text; SPARQL can join corpora MCP does
not expose as tools.

## v1 residuals (accepted, not blocking close)

These remaining tickets are recorded as **accepted residuals** for 0.11.x:
identity remint (`#444`/`#445`/`#447`), overlay extraction (`#463`),
lower-court ingest (`#525`), Zenodo/DOI (`#473`), SPARQL compose (`#474`),
and other `#406`–`#414` / `#494` children not shipped in this run.
They are not silently dropped: they live here and in
`eval/FITNESS_REPORT.md`. New work should not invent a sixth load surface.

## What not to change without a MAJOR version

- Slash namespace and underscore local names.
- `estleg:partOfAct` as the act⇄provision join (not `sourceAct` literals).
- `MunicipalRegulation` as a sibling of `NationalRegulation`, not a subclass.
- Drafts as `ProposedAmendment`, not effected `AmendmentEvent`.
- Combined stub-closure + overlay merge; version edges stay on the full surface.
