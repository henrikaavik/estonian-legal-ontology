# Estonian Legal Ontology Project

## Project Overview and Purpose
This project provides a comprehensive, machine-readable ontology of Estonian and EU legislation using JSON-LD. It creates a semantic graph of **enacted laws** (from Riigi Teataja), **draft legislation** (from EIS), **Supreme Court decisions** (from RIK), and **EU legal acts** (from EUR-Lex), enabling advanced search, cross-referencing, and automated legal analysis.

**Status: 615 enacted laws + 22,832 drafts + 12,137 court decisions + 33,242 EU acts + 22,290 EU court decisions** (as of March 7, 2026)

## Enacted Laws (615 total)

All laws from [Riigi Teataja](https://www.riigiteataja.ee) have been mapped, including:

### Civil Law (7)
1. Tsiviilseadustiku üldosa seadus (TsÜS) - General Part of Civil Code
2. Võlaõigusseadus (VÕS) - Law of Obligations
3. Asjaõigusseadus (AÕS) - Property Law
4. Perekonnaseadus (PKS) - Family Law
5. Notari seadus (NotS) - Notaries Act
6. Tõestamisseadus (TõS) - Authentication Act
7. Pärimisseadus - Inheritance Law

### Commercial & Economic Law (6)
8. Äriseadustik (ÄS) - Commercial Code
9. Konkurentsiseadus - Competition Act
10. Pankrotiseadus (PankrS) - Bankruptcy Act
11. Saneerimisseadus (SanS) - Reorganisation Act
12. Füüsilise isiku maksejõuetuse seadus (FIMS) - Personal Insolvency Act
13. Maksukorralduse seadus (MKS) - Taxation Act

### Labor & Social Law (3)
14. Töölepinguseadus (TLS) - Employment Contracts Act
15. Ametiühingute seadus (AÜS) - Trade Unions Act
16. Töötervishoiu ja tööohutuse seadus

### Criminal Law (2)
17. Karistusseadustik (KarS) - Penal Code
18. Kriminaalmenetluse seadustik (KrMS) - Code of Criminal Procedure

### + 577 more laws
See `krr_outputs/INDEX.json` for the complete list.

## Draft Legislation (EIS)

Draft legislation is sourced from [EIS – Eelnõude infosüsteem](https://eelnoud.valitsus.ee), Estonia's official legislative drafting system.

### Coverage

| Phase | Estonian | English | Count |
|-------|----------|---------|-------|
| Public Consultation | Avalik konsultatsioon | Open for public comment | 122 |
| Review | Kooskõlastamine | Inter-ministerial coordination | 13,670 |
| Submission | Esitatud VV-le | Submitted to Government | 9,040 |

### Draft Types

| Type | Estonian | Description |
|------|----------|-------------|
| Bill | Seaduseelnõu | New law proposal |
| AmendmentBill | Seaduse muutmise eelnõu | Proposal to amend existing law |
| GovernmentRegulation | VV määruse eelnõu | Government regulation draft |
| MinisterialRegulation | Ministri määruse eelnõu | Minister's regulation draft |
| GovernmentOrder | Korralduse eelnõu | Government order draft |
| EUPosition | EL seisukoha eelnõu | Estonian position on EU matters |
| DraftIntent | Väljatöötamiskavatsus | Pre-draft intent document |
| ActionPlan | Tegevuskava | Action plan / strategy |

### Integration with Enacted Laws

Drafts link to existing laws via `estleg:affectedLawName`, enabling queries like:
- "Which drafts propose changes to Karistusseadustik?"
- "What new laws are in public consultation?"
- "Which ministry has the most active drafts?"

## Schema

The ontology uses the `estleg` namespace (`https://data.riik.ee/ontology/estleg#`):

### Core Classes
- `estleg:LegalProvision` — Enacted legal provisions (paragraphs, sections)
- `estleg:TopicCluster` — Thematic groupings of provisions
- `estleg:LegalConcept` — Legal concepts and definitions
- `estleg:DraftLegislation` — Legislative drafts (eelnõud)
- `estleg:LegislativePhase` — Draft processing stages
- `estleg:DraftType` — Draft classification

- `estleg:CourtDecision` — Supreme Court decisions (Riigikohtu lahendid)
- `estleg:CaseType` — Case type classification
- `estleg:DecisionType` — Decision type classification
- `estleg:EULegislation` — EU legal acts (regulations, directives, decisions)
- `estleg:EUDocumentType` — EU document type classification
- `estleg:EUInstitution` — EU institution classification
- `estleg:EUCourtDecision` — EU court decisions (CJEU)
- `estleg:EUCourtDecisionType` — EU court decision type classification
- `estleg:EUCourt` — EU court classification

**Integration & Analysis:**
- `estleg:Sanction` — Penalties and sanctions extracted from law text
- `estleg:Institution` — State institutions with legal competences
- `estleg:NormativeType` — Deontic classification (Obligation, Right, Permission, Prohibition)
- `estleg:Section` — Section structure in KarS special parts
- `estleg:AmendmentEvent` — Amendment events linking provisions to amending acts

See [SCHEMA_REFERENCE.md](SCHEMA_REFERENCE.md) for complete documentation.

## Supreme Court Decisions (Riigikohus)

12,137 decisions from 1993-2026, sourced from [rikos.rik.ee](https://rikos.rik.ee).

| Case Type | Count |
|-----------|-------|
| Administrative (Haldusasi) | 9,561 |
| Civil (Tsiviilasi) | 970 |
| Criminal (Kriminaalasi) | 484 |
| Constitutional Review | 336 |
| Misdemeanor (Vaarteoasi) | 107 |
| Other | 679 |

## EU Legislation (EUR-Lex)

33,242 EU legal acts available in Estonian, sourced from [EUR-Lex](https://eur-lex.europa.eu) via SPARQL.

| Type | Estonian | Total | In Force |
|------|----------|-------|----------|
| Regulation | EL maarus | 18,784 | 5,095 |
| Directive | EL direktiiv | 3,114 | 939 |
| Decision | EL otsus | 11,344 | 6,097 |

Each act includes CELEX number, Estonian title, document date, in-force status, ELI identifier, and EUR-Lex link.

## EU Court Decisions (CURIA)

22,290 CJEU decisions available in Estonian, sourced from [EUR-Lex](https://eur-lex.europa.eu) via SPARQL.

| Type | Estonian | Count |
|------|----------|-------|
| AG Opinion | Kohtujuristi ettepanek | 9,952 |
| Order | Kohtumaarus | 6,619 |
| Judgment | Kohtuotsus | 5,641 |
| Court Opinion | Kohtu arvamus | 17 |

Courts: Court of Justice (17,720), General Court (4,036), Civil Service Tribunal (534).

## How to Use

1. Download JSON-LD files from `krr_outputs/` (enacted), `krr_outputs/eelnoud/` (drafts), `krr_outputs/riigikohus/` (court), `krr_outputs/eurlex/` (EU)
2. Load into a graph database (GraphDB, Neo4j with RDF plugin, Apache Jena)
3. Parse with RDF/JSON-LD libraries (Python: rdflib, JavaScript: jsonld.js)

## Repository Structure
```
.
├── krr_outputs/            # JSON-LD ontology files (700+ files)
│   ├── *_peep.json         # Individual enacted law mappings
│   ├── combined_ontology.jsonld  # All enacted laws combined
│   ├── INDEX.json          # Enacted law registry
│   ├── eelnoud/            # Draft legislation
│   │   ├── eelnoud_schema.json           # Schema definitions
│   │   ├── eelnoud_*_peep.json           # Phase-grouped drafts
│   │   ├── eelnoud_combined.jsonld       # All drafts combined
│   │   └── EELNOUD_INDEX.json            # Draft registry
│   ├── riigikohus/         # Supreme Court decisions
│   │   ├── riigikohus_schema.json        # Schema definitions
│   │   ├── riigikohus_YYYY_peep.json     # Per-year files (1993-2026)
│   │   └── RIIGIKOHUS_INDEX.json         # Decision registry
│   ├── eurlex/             # EU legislation
│   │   ├── eurlex_schema.json            # Schema definitions
│   │   ├── eurlex_*_peep.json            # Per-type EU acts
│   │   ├── eurlex_combined.jsonld        # All EU acts combined
│   │   └── EURLEX_INDEX.json             # EU legislation registry
│   ├── curia/              # EU court decisions
│   │   ├── curia_schema.json             # Schema definitions
│   │   ├── curia_*_peep.json             # Per-type decisions
│   │   ├── curia_combined.jsonld         # All EU decisions combined
│   │   └── CURIA_INDEX.json              # EU court decision registry
│   ├── concepts/           # Legal concept cross-reference graph
│   ├── institutions/       # Institutional competence mappings
│   ├── sanctions/          # Penalty and sanction index
│   └── amendments/         # Amendment chain data
├── docs/                   # Documentation
├── shacl/                  # SHACL validation shapes
├── scripts/                # Generation and validation scripts
├── reviews/                # Law review request files
├── .github/workflows/      # CI pipeline
└── README.md               # Main project readme
```

## Repository
https://github.com/henrikaavik/estonian-legal-ontology

## Contribution Guidelines
Please submit pull requests with improvements. Ensure all JSON-LD files pass validation:
- Valid JSON syntax
- Consistent @context
- No duplicate @id values within files
- Proper estleg: namespace usage

## License
MIT License - See LICENSE file for details

## Data Sources

| Source | URL | Data | Format |
|--------|-----|------|--------|
| Riigi Teataja | https://www.riigiteataja.ee | Enacted laws | XML API |
| EIS | https://eelnoud.valitsus.ee | Draft legislation | RSS 2.0 |
| RIK / Riigikohus | https://rikos.rik.ee | Supreme Court decisions | HTML search |
| EUR-Lex | https://eur-lex.europa.eu | EU legislation | SPARQL |
| EUR-Lex / CURIA | https://eur-lex.europa.eu | EU court decisions | SPARQL |

---
*Last updated: March 7, 2026 (615 laws + 22,832 drafts + 12,137 court decisions + 33,242 EU acts + 22,290 EU court decisions)*
