# Changelog

All notable changes to this project will be documented in this file.

## [0.7.0] - 2026-03-03

### Added
- **EU Legislation (EUR-Lex)** — new ontology class `estleg:EULegislation`
- New classes: `estleg:EUDocumentType`, `estleg:EUInstitution`
- Document types: Regulation, Directive, Decision
- EU institutions: European Commission, Council, Parliament, ECB, and DGs
- Properties: `celexNumber`, `euDocumentType`, `eurLexLink`, `eliIdentifier`, `documentDate`, `inForce`, `euInstitution`
- `scripts/generate_eu_legislation.py` — fetches all EU acts from EUR-Lex SPARQL endpoint
- `krr_outputs/eurlex/` directory with schema, per-type files, combined file, and index
- 33,242 EU legal acts in Estonian (18,784 regulations, 3,114 directives, 11,344 decisions)
- SPARQL examples for EU legislation queries in SCHEMA_REFERENCE

### Changed
- Updated README.md with EU legislation section, data sources, API details
- Updated SCHEMA_REFERENCE.md with EU legislation schema documentation
- Updated docs/README.md with EU legislation coverage
- Updated validation script to exclude EURLEX_INDEX from validation

## [0.6.0] - 2026-03-03

### Added
- **Supreme Court Decisions (Riigikohtu lahendid)** — new ontology class `estleg:CourtDecision`
- New classes: `estleg:CaseType`, `estleg:DecisionType`
- Case types: Criminal, Civil, Administrative, Misdemeanor, ConstitutionalReview
- Decision types: Judgment, Ruling, Resolution
- Properties: `caseNumber`, `caseType`, `decisionType`, `decisionDate`, `decisionLink`, `rikObjectId`, `referencedLaw`, `interpretsLaw`
- `scripts/generate_court_decisions.py` — fetches all decisions from RIK (rikos.rik.ee)
- `krr_outputs/riigikohus/` directory with schema, per-year files (1993-2026), and index
- 12,137 Supreme Court decisions across 34 years
- SHACL shapes for CourtDecision, CaseType, DecisionType
- Data sources documentation in README and SCHEMA_REFERENCE

### Changed
- Updated README.md with court decisions section, data sources table, and API details
- Updated SCHEMA_REFERENCE.md with court decision schema, SPARQL examples
- Updated validation script to cover riigikohus/ subdirectory
- Updated docs/README.md with court decisions coverage

## [0.5.0] - 2026-03-03

### Added
- **Draft Legislation (eelnõud)** — new ontology class `estleg:DraftLegislation` for laws in the legislative pipeline
- New classes: `estleg:LegislativePhase`, `estleg:DraftType`
- Legislative phases: PublicConsultation, Review, Submission
- Draft types: Bill, AmendmentBill, GovernmentRegulation, MinisterialRegulation, GovernmentOrder, EUPosition, DraftIntent, ActionPlan, Other
- Properties: `eisNumber`, `eisLink`, `initiator`, `publicationDate`, `affectedLawName`, `amendsLaw`, `legislativePhase`, `draftType`, `phaseOrder`
- `scripts/generate_draft_legislation.py` — fetches all drafts from EIS RSS feeds
- `krr_outputs/eelnoud/` directory with schema, phase files, combined file, and index
- 22,810 draft legislation entries from EIS (Eelnõude infosüsteem)
- SHACL shapes for DraftLegislation, LegislativePhase, DraftType
- Updated schema reference with draft legislation documentation and SPARQL examples

### Changed
- Updated README.md with draft legislation section and updated stats
- Updated SCHEMA_REFERENCE.md with complete draft legislation documentation

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
