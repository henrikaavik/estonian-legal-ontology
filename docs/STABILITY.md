# Consumer stability contract

Pin a release by `owl:versionInfo` / `owl:versionIRI`
(`https://w3id.org/estleg/<version>`), not by a clone of `main`.

## Predicate tiers

| Tier | Meaning | Examples |
|---|---|---|
| Stable | Safe to store and query across MINOR releases | `@id`, `@type`, `estleg:partOfAct`, `estleg:paragrahv`, `rdfs:label`, `dcterms:source` |
| Additive | New properties may appear; existing ones stay | `estleg:hasExpression`, `estleg:coverageFlagMethod` |
| Heuristic | Keyword / classifier output; may be rewritten on regen | `dcterms:subject` (EuroVoc) on Estonian acts, `estleg:normativeType`, `estleg:targetGroup`, `estleg:semanticallySimilarTo`, concept `skos:altLabel` |
| Build marker | Not a legal claim | `estleg:isStubNode` |

EuroVoc subjects have two sources. On EU acts (`estleg:EULegislation`),
`dcterms:subject` / `eli:is_about` are the Publications Office's own
indexing from CELLAR (`cdm:work_is_about_concept_eurovoc`) and carry
`estleg:subjectSource "cellar"`. They change only when CELLAR re-indexes an
act. On Estonian acts they come from the keyword classifier: at most three
domains, ranked by keyword hits per 1,000 tokens.

Concept `skos:altLabel` values record spelling variants of the same term
(`teenuse pakkuja` / `teenusepakkuja`) folded into one `estleg:Concept`.
The concepts layer emits no `skos:closeMatch`. The edit-distance matcher
that produced it was removed because it linked unrelated words such as
`laev` / `laps` (#699).

A Heuristic value that a reviewer has corrected is pinned in
`data/heuristic_overrides.jsonl`. Regeneration never rewrites it, and the
node carries `prov:wasAttributedTo` with `estleg:assertionConfidence` 1.0 for
the reviewed layer. See [HEURISTIC_OVERRIDES.md](HEURISTIC_OVERRIDES.md).

Do **not** persist `estleg:isStubNode` as a fact about the real-world
entity. On the full load surface the complete node wins; ignore the flag.

## `@id` policy

Law/provision local names are frozen for MINOR/PATCH. A rename is MAJOR.
Do not treat an unversioned `estleg:` IRI as a permanent foreign key
across untagged `main` clones — pin `owl:versionIRI` first.
The project has released v1.0.0. Shortening amendment-family IDs
(`Amendment_<ABBREV>_…`) is therefore also a MAJOR change.

Riigikohus decision IRIs are frozen as `estleg:RK_<sanitize(caseNumber)>`
(#697), the form 11,983 of 12,104 committed decisions carry. The other 121
are second documents of a `caseNumber` whose short IRI another document
already holds. They keep `estleg:RK_<sanitize(caseNumber)>_<sanitize(rikObjectId)>`,
listed per document in `data/rk_iri_collisions.json`. The generator consults
that allowlist, so regeneration reproduces every committed court IRI. A new
collision gets the long form and is appended with a logged warning. Changing
the formula, re-minting a listed IRI or removing an entry is MAJOR.
`tests/test_ingest_regeneration_697.py` checks every committed decision
against the formula (`pytest -m corpus`).

Regulation provision IRIs (`estleg:Reg_<tid>_Par_*`) move from the positional
legacy suffix to the law-pipeline suffix (`_paragraph_id_suffix` +
`_dedupe_paragraph_suffix`) in a MAJOR release: 2,549 of 168,420 IRIs change
and 64 of them are reused for a different provision, so the switch must be one
simultaneous pass. A rename map (`scripts/regulation_iri_rename_map.py`) and
an `owl:sameAs` table ship with that release. Until then the generator keeps
the legacy IRIs for every committed act (`--iri-scheme auto`). New
`estleg:Subsection` nodes on regulations are an additive MINOR change (#722).

## Deprecation and support

**Deprecation window.** A term in the Stable or Additive tier is never
removed or renamed without warning. It is first marked in
`controlled_vocabulary.jsonld` with `owl:deprecated true` and
`dcterms:isReplacedBy` naming its replacement, and the change is listed in
[RELEASE_NOTES.md](RELEASE_NOTES.md) as an **Action** item. The deprecated
term then stays declared, so existing queries keep parsing, for at least
**one MINOR release and at least six months**, whichever is longer. It is
removed only in a MAJOR release. A deprecated term may stop being *emitted*
before it is removed; the release notes say when that happens (for example
the #701 coverage flags).

Heuristic-tier values and Build markers are not covered by the window: they
can change on any regeneration, as the tier table says.

**`@id` renames.** A law or provision local name is frozen within a MAJOR
line. The exception is an identifier that was wrong when minted, such as one
IRI denoting two acts; correcting it is a PATCH or MINOR fix and is listed as
an **Action** item in the release notes.

**Support horizon.**

| Line | Receives |
|---|---|
| Latest MINOR of the current MAJOR | Data corrections, fixes, security fixes |
| Earlier MINORs of the current MAJOR | Nothing; upgrade to the latest MINOR (MINOR is backwards-compatible) |
| Previous MAJOR | Security fixes for six months after the new MAJOR is published |
| Untagged `main` | Nothing; not a release |

How to report a security or personal-data issue, and the supported-versions
table, are in [SECURITY.md](../SECURITY.md). Who decides releases is in
[GOVERNANCE.md](../GOVERNANCE.md).

**Currently deprecated** (declared with `owl:deprecated true` and
`dcterms:isReplacedBy` in `controlled_vocabulary.jsonld`):

| Term | Replaced by | Since | Still emitted? |
|---|---|---|---|
| `estleg:targetGroupConcept` | `estleg:targetGroup` (now an `owl:ObjectProperty` with range `estleg:TargetGroup`) | 1.1.0 (#709) | No — removed from the root law and regulation peeps; `materialize_target_group_concepts_609.py` refuses to run |
| `estleg:hasNoTransposition` | `estleg:noTranspositionEdgeInCorpus` | 1.1.0 (#701) | No |
| `estleg:hasNoCompetentAuthority` | `estleg:competentAuthorityNotExtracted` | 1.1.0 (#701) | No |

## Empty results

Unknown target → `{note}` / `[{note}]`; known target with zero hits → `[]`.
These are the MCP no-match conventions. Source URLs may be empty when the
corpus lacks a verified citation; empty results do not prove absence in law.
Input, loading, and server failures remain errors.
