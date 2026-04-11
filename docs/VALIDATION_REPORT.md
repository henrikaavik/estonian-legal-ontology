# Validation Report

**Last updated:** 2026-04-11
**Validator:** `scripts/validate_all.py`

## Summary

| Metric | Count |
|--------|-------|
| Files validated | 1,302 |
| Errors | 0 |
| Warnings | 1 |
| Result | **PASSED** |

## Checks Performed

1. JSON syntax validity
2. @context namespace consistency (`estleg:` → `https://data.riik.ee/ontology/estleg#`)
3. @type is always an array
4. Multi-valued properties are arrays (estleg:references, estleg:referencedBy, etc.)
5. sectionNumber is always a string
6. dc:source is always a string
7. @id uniqueness within files

## Warnings

- 495 @id values appear in multiple files (intentional: shared schema class definitions)

## Data Coverage

| Category | Files | Nodes |
|----------|-------|-------|
| Enacted laws | 635 | 28,598 provisions |
| Draft legislation | 6 | 22,832 drafts |
| Supreme Court decisions | 34 | 12,137 decisions |
| EU legislation | 6 | 33,242 acts |
| EU court decisions | 8 | 22,290 decisions |
| Amendment chains | 376 | 18,068 amendment events |
| Legal concepts | 2 | 812 concept nodes + report |
| Institutions | 85 | 315 institutional competence nodes |
| Sanctions | 152 | 1,340 sanction records |
| Reports/indexes | 23 | metadata (excluded from validation) |

## Known Remaining Issues

1. **Ontology IRI collisions** — some abbreviated law prefixes collide across files (tracked in #46)
