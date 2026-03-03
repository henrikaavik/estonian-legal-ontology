# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] - 2026-03-02

### Fixed
- Migrated all files from `example.org` placeholder namespace to production `https://data.riik.ee/ontology/estleg#`
- Normalized `@type` to always use arrays across all 98 JSON-LD files
- Normalized multi-valued properties (`coversConcept`, `hasSection`, etc.) to always use arrays
- Normalized `dc:source` to consistent string type
- Normalized `estleg:sectionNumber` to consistent string type
- Fixed Asjaõigusseadus (AÕS) file naming — renamed `asjaigusseadus_*` (typo) to `asjaoigusseadus_*`
- Fixed `notari_seadus_peep.json` → `notariaadiseadus_peep.json` naming mismatch
- Fixed intra-file duplicate `@id` value (`estleg:AOS_Par_260`) in AÕS consolidated file
- Fixed namespace in `generate_kars_eriosa_jsonld.py` script

### Added
- `.gitignore` file for Python/IDE/OS artifacts
- `LICENSE` file (MIT)
- `CHANGELOG.md`
- `krr_outputs/INDEX.json` — master registry mapping law names to output files
- `krr_outputs/combined_ontology.jsonld` — unified file for graph database loading (2973 nodes)
- `docs/DUPLICATE_IDS_REPORT.md` — audit of cross-file @id collisions
- `shacl/estonian_legal_shapes.ttl` — SHACL validation shapes
- `.github/workflows/validate.yml` — CI pipeline for automated validation
- `scripts/validate_all.py` — comprehensive validation script
- Consolidated review request files into `reviews/` directory

### Changed
- Expanded root `README.md` with project description, quick-start, and coverage table
- Updated `docs/VALIDATION_REPORT.md` to reflect current file count (97+ files)
- Updated `docs/README.md` namespace references

## [0.2.0] - 2026-03-01

### Added
- Schema unification across all mapped laws
- KarS Eriosa automated generation script
- Marta review request files for 25 laws

## [0.1.0] - 2026-02-28

### Added
- Initial mapping of 44 Estonian laws to JSON-LD
- Core ontology schema (LegalProvision, TopicCluster, LegalConcept)
- Documentation (API Guide, Schema Reference, Validation Report)
