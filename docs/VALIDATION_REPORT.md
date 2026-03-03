# Validation Report

*Last updated: 2026-03-02*

## Summary

- **Total JSON-LD files:** 97 (`_peep.json`) + 2 (`.jsonld`)
- **Combined nodes:** 2973 unique @id entries
- **Validation status:** All files pass JSON syntax, namespace, and type consistency checks

## Checks Performed

| Check | Status | Details |
|-------|--------|---------|
| JSON syntax validity | PASS | All 99 files parse correctly |
| Namespace consistency | PASS | All files use `https://data.riik.ee/ontology/estleg#` |
| @type normalization | PASS | All @type values are arrays |
| Multi-valued properties | PASS | coversConcept, hasSection, etc. always arrays |
| dc:source type | PASS | Always string |
| sectionNumber type | PASS | Always string |
| Intra-file @id uniqueness | PASS | No duplicate @id within any file |
| Cross-file @id uniqueness | WARN | 23 shared IDs (see DUPLICATE_IDS_REPORT.md) |

## Cross-File @id Notes

23 @id values appear in multiple files. Most are shared ontology class definitions (e.g., `owl:Class` nodes for `LegalProvision`, `TopicCluster`) that appear in every file by design. See `docs/DUPLICATE_IDS_REPORT.md` for the full list.

## Schema Consistency

The overall schema (`estleg:LegalProvision`, `estleg:TopicCluster`, `estleg:LegalConcept`) is consistently applied across all mapped laws. Key properties:

- `estleg:identifier` — provision identifier
- `rdfs:label` — human-readable label
- `schema:text` / `estleg:legalText` — legal text content
- `estleg:topicCluster` — topic cluster reference
- `estleg:references` — cross-references to other provisions

## Automated Validation

Run locally:
```bash
python3 scripts/validate_all.py
```

CI validates automatically on pushes to `main` via GitHub Actions.

## Known Remaining Issues

1. **Cross-file @id overlaps** — 23 shared IDs, mostly intentional class definitions
2. **Missing law parts** — VÕS parts 2, 6, 10 and TsÜS part 1 not yet mapped
3. **Cross-law references** — Not yet encoded in the ontology
