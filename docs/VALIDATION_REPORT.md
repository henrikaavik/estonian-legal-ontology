# Validation Report

## Summary of All 26 Files
- Total mapped laws: 26
- Total successfully validated files: 26
- Minor consistency issues identified: 2
- Major structural errors: None

## Schema Consistency Status
- The overall schema (`estleg:LegalProvision`, `estleg:TopicCluster`, `estleg:LegalConcept`) is consistently applied across all mapped laws.
- Namespaces are correctly instantiated in the `@context`.
- `estleg:references` arrays are properly formatted as objects or lists of objects.

## Known Issues (Cluster Reference Inconsistency)
- Several `estleg:topicCluster` references currently point to raw string IRIs or objects lacking definitions within the `@graph`.
- In some older mapped files (e.g., AÕS), `estleg:topicCluster` is simply an array of strings instead of proper IRI references.
- In cross-references (`estleg:references`), some target IDs might not resolve if the target law hasn't been fully mapped or if the ID pattern slightly differs between mapping passes.

## Recommendations for Fixes
1. Normalize `estleg:topicCluster` references to consistently be objects with `@id` (e.g., `{"@id": "https://data.riik.ee/taxonomy/..."}`).
2. Run `fix_json_ld.py` or similar normalization scripts across all 26 files to ensure uniformity.
3. Validate cross-reference targets against a master registry of known legal provisions to ensure referential integrity.
