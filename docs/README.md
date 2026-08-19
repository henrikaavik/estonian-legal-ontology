# Estonian Legal Ontology Project

## Project Overview and Purpose
This project provides a comprehensive, machine-readable ontology of Estonian and EU legislation using JSON-LD. It creates a semantic graph of **enacted laws** (from Riigi Teataja), **domestic regulations (määrused)**, **draft legislation** (from EIS), **Supreme Court decisions** (from RIK), **EU legal acts** (from EUR-Lex), and **EU court decisions**, enabling advanced search, cross-referencing, and automated legal analysis.

Canonical headline counts live in the root [README.md](../README.md) and `metadata.jsonld` (`estleg:statistics`). Do not edit those two independently.

**Status: 1,122 enacted laws (1,190 law files) + 22,832 drafts + 3,812 state regulations + 11,059 municipal regulations (opt-in) + 12,137 court decisions + 33,242 EU acts + 22,290 EU court decisions** | **23,118 JSON/JSON-LD files** | ontology **1.0.0** (2026-08-19)

## Enacted Laws (1,122 total)

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

### + 1,104 more laws
See `krr_outputs/INDEX.json` for the complete list.

## Domestic regulations (määrused)

State and municipal regulations sit under `krr_outputs/regulations/` and take
part in the same `iter_peep_files()` enrichment passes as enacted laws.

| Level | Path | Count |
|-------|------|-------|
| State (`estleg:NationalRegulation`) | `krr_outputs/regulations/riik/` | 3,812 |
| Municipal (`estleg:MunicipalRegulation`) | `krr_outputs/regulations/kov/` | 11,059 |

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

The ontology uses the `estleg` namespace (`https://w3id.org/estleg/`):

### Core Classes
- `estleg:LegalProvision` — Enacted legal provisions (paragraphs, sections)
- `estleg:DomesticRegulation` — Domestic määrus (state or municipal)
- `estleg:TopicCluster` — Thematic groupings of provisions
- `estleg:LegalConcept` — Legal concepts and definitions
- `estleg:DraftLegislation` — Legislative drafts (eelnõud)
- `estleg:LegislativePhase` — Draft processing stages
- `estleg:DraftType` — Draft classification

- `estleg:CourtDecision` — Estonian court decisions (Riigikohus + first/second instance)
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
| Civil (Tsiviilasi) | 4,745 |
| Criminal (Kriminaalasi) | 3,422 |
| Administrative (Haldusasi) | 2,392 |
| Constitutional Review | 792 |
| Other | 679 |
| Misdemeanor (Vaarteoasi) | 107 |

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
| Other | — | 61 |
| Court Opinion | Kohtu arvamus | 17 |

Courts: Court of Justice (17,720), General Court (4,036), Civil Service Tribunal (534).

## How to Use

1. Download JSON-LD files from `krr_outputs/` (enacted), `krr_outputs/regulations/` (määrused), `krr_outputs/eelnoud/` (drafts), `krr_outputs/riigikohus/` (Supreme Court), `krr_outputs/kohtud/` (first/second-instance courts), `krr_outputs/eurlex/` (EU), `krr_outputs/curia/` (EU court)
2. Load into a graph database (GraphDB, Neo4j with RDF plugin, Apache Jena)
3. Parse with RDF/JSON-LD libraries (Python: rdflib, JavaScript: jsonld.js)

## Repository Structure
```
.
├── krr_outputs/            # JSON-LD corpus (~23k files)
│   ├── *_peep.json         # Individual enacted law mappings
│   ├── combined_ontology.jsonld  # Combined load surface (Git LFS)
│   ├── INDEX.json          # Enacted law registry
│   ├── eelnoud/            # Draft legislation
│   ├── riigikohus/         # Supreme Court decisions
│   ├── kohtud/             # First/second-instance decisions (#525)
│   ├── eurlex/             # EU legislation
│   ├── curia/              # EU court decisions
│   ├── regulations/        # State (riik/) + municipal (kov/) määrused
│   ├── concepts/           # Legal concept cross-reference graph
│   ├── institutions/       # Institutional competence mappings
│   ├── sanctions/          # Penalty and sanction index
│   ├── amendments/         # Amendment chain data
│   └── provision_versions/ # Point-in-time provision redactions
├── data/                   # Registries (abbreviations, EHAK, migrations)
├── mcp_server/             # estleg-mcp natural-language query layer
├── docs/                   # Documentation
├── shacl/                  # SHACL validation shapes
├── scripts/                # Generation and validation scripts
├── tests/                  # Unit + corpus invariant tests
├── reviews/                # Law review request files
├── .github/workflows/      # CI pipeline
└── README.md               # Main project readme (canonical counts)
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

The MIT `LICENSE` covers repository **software** only (`scripts/`,
`mcp_server/`, `tests/`). The legal-text corpus keeps its source rights;
the project's compilation layer is offered as draft CC BY 4.0. See the
top-level `NOTICE` and [DATA_RIGHTS.md](DATA_RIGHTS.md).

## Data Sources

| Source | URL | Data | Format |
|--------|-----|------|--------|
| Riigi Teataja | https://www.riigiteataja.ee | Enacted laws | XML API |
| EIS | https://eelnoud.valitsus.ee | Draft legislation | RSS 2.0 |
| RIK / Riigikohus | https://rikos.rik.ee | Supreme Court decisions | HTML search |
| EUR-Lex | https://eur-lex.europa.eu | EU legislation | SPARQL |
| EUR-Lex / CURIA | https://eur-lex.europa.eu | EU court decisions | SPARQL |

---
*Last updated: 2026-08-19 to match `metadata.jsonld` / ontology 1.0.0. Court case-type split from `krr_outputs/riigikohus/RIIGIKOHUS_INDEX.json`.*
