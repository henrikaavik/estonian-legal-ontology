# Consumer stability contract

Pin a release by `owl:versionInfo` / `owl:versionIRI`
(`https://w3id.org/estleg/<version>`), not by a clone of `main`.

## Predicate tiers

| Tier | Meaning | Examples |
|---|---|---|
| Stable | Safe to store and query across MINOR releases | `@id`, `@type`, `estleg:partOfAct`, `estleg:paragrahv`, `rdfs:label`, `dcterms:source` |
| Additive | New properties may appear; existing ones stay | `estleg:targetGroupConcept`, `estleg:hasExpression` |
| Heuristic | Keyword / classifier output; may be rewritten on regen | `dcterms:subject` (EuroVoc), `estleg:normativeType`, `estleg:targetGroup`, `estleg:semanticallySimilarTo` |
| Build marker | Not a legal claim | `estleg:isStubNode` |

Do **not** persist `estleg:isStubNode` as a fact about the real-world
entity. On the full load surface the complete node wins; ignore the flag.

## `@id` policy

Law/provision local names are frozen for MINOR/PATCH. A rename is MAJOR.
Do not treat an unversioned `estleg:` IRI as a permanent foreign key
across untagged `main` clones — pin `owl:versionIRI` first.
Amendment-family IDs may still be shortened (`Amendment_<ABBREV>_…`)
before v1.0; after v1.0 that is also MAJOR.

## Empty results

MCP tools return `[]` or `{note: ...}` on a miss, never an exception and
never a guessed citation.
