# Schema Unification Report (2026-03-01)

## Scope
Unified JSON-LD schema across the six mapped laws:
- TsÜS (tsiviilseadustik + tsus_osa7_138_169)
- VÕS
- AÕS
- ÄS
- KarS
- TLS

## Differences found before migration
1. **Context mismatch**
   - Some files used minimal context (`estleg`, `owl`, `rdfs`, `xsd`)
   - Some used extended context with ad-hoc local terms (`hasSection`, `legalText`, `sectionNumber`, etc.)
2. **Type namespace mismatch**
   - Mix of `estleg:*` and full IRI types (`https://example.org/estonian-legal#Chapter`, etc.)
3. **Property naming mismatch**
   - `label` vs `rdfs:label`
   - unprefixed structural properties (`hasChapter`, `inPart`, `coversConcept`, etc.)
   - unprefixed `legalText`, `sectionNumber`
4. **Top-level structure mismatch**
   - Most files: `{"@context","@graph"}`
   - one file also had top-level `@id`, `@type`, `label`

## Canonical schema selected
Top-level shape for all files:
- `@context`
- `@graph`

Canonical context prefixes:
- `estleg`, `owl`, `rdf`, `rdfs`, `xsd`, `dc`, `skos`

Canonical naming conventions:
- Labels: `rdfs:label`
- Structural/object properties: `estleg:*` (e.g., `estleg:hasSection`, `estleg:inPart`)
- Text/number fields: `estleg:legalText`, `estleg:sectionNumber`
- Keep standard vocab fields where present: `dc:*`, `skos:*`, `rdfs:*`

Type normalization:
- Converted full IRI legal types to `estleg:*` compact form
- Normalized `estleg:Provision` → `estleg:LegalProvision`
- Normalized `estleg:Concept` → `estleg:LegalConcept`

## Files migrated
- `krr_outputs/ariseadustik_peep.json`
- `krr_outputs/asjaoigusseadus_osa1_peep.json`
- `krr_outputs/asjaoigusseadus_osa2_peep.json`
- `krr_outputs/asjaoigusseadus_osa3_peep.json`
- `krr_outputs/asjaoigusseadus_osa4_peep.json`
- `krr_outputs/asjaoigusseadus_osa5_peep.json`
- `krr_outputs/asjaoigusseadus_osa6-13_peep.json`
- `krr_outputs/karistusseadustik_eriosa_owl.jsonld`
- `krr_outputs/toolepinguseadus_peep.json`
- `krr_outputs/tsiviilseadustik_osa2_peep.json`
- `krr_outputs/tsiviilseadustik_osa3_peep.json`
- `krr_outputs/tsiviilseadustik_osa5_peep.json`
- `krr_outputs/tsiviilseadustik_osa6_peep.json`
- `krr_outputs/tsiviilseadustik_osa8_peep.json`
- `krr_outputs/tsus_osa7_138_169_owl.jsonld`
- `krr_outputs/volaigusseadus_osa1_peep.json`

## Validation summary
- All migrated target files now share **exactly one** `@context` variant.
- All migrated files now use same top-level structure: `@context` + `@graph`.
- Property and type naming normalized to canonical form.

## Note for Marta legal validation
This pass changes **schema and naming only** (serialization/modeling layer). Legal text content was preserved and not re-authored.
