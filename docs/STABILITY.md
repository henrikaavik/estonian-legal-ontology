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
The project has released v1.0.0. Shortening amendment-family IDs
(`Amendment_<ABBREV>_…`) is therefore also a MAJOR change.

## Empty results

Unknown target → `{note}` / `[{note}]`; known target with zero hits → `[]`.
These are the MCP no-match conventions. Source URLs may be empty when the
corpus lacks a verified citation; empty results do not prove absence in law.
Input, loading, and server failures remain errors.
