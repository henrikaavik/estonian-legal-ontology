# Estonian Legal Ontology Project

## Project Overview and Purpose
This project provides a comprehensive, machine-readable ontology of Estonian legislation using JSON-LD. It aims to create a semantic graph of legal provisions, topic clusters, and legal concepts, enabling advanced search, cross-referencing, and automated legal analysis.

**Status: 44 laws mapped** (as of March 1, 2026)

## Mapped Laws (44 total)

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

### Administrative Law (8)
19. Haldusmenetluse seadustik (HMS) - Administrative Procedure Act
20. Halduskohtumenetluse seadustik (HKMS) - Administrative Court Procedure Act
21. Kohaliku omavalitsuse korralduse seadus (KOKS) - Local Government Organisation Act
22. Riigikogu kodu- ja töökorra seadus (RKKS) - Riigikogu Rules of Procedure
23. E-riigi seadus - Digital State Act
24. Isikuandmete kaitse seadus (IKS) - Personal Data Protection Act

### Procedural Law (3)
25. Tsiviilkohtumenetluse seadustik (TsMS) - Code of Civil Procedure
26. Täitemenetluse seadustik (TMS) - Enforcement Procedure Act
27. Kohtutäituri seadus (KTS) - Bailiffs Act

### Constitutional & State Structure (4)
28. Eesti Vabariigi põhiseadus (PS) - Constitution of Estonia
29. Riigivastutuse seadus (RVastS) - State Liability Act
30. Kaitseväeteenistuse seadus (KVTS) - Military Service Act
31. Advokatuuriseadus - Bar Association Act

### Environmental Law (4)
32. Keskkonnaseadustiku üldosa seadus (KeÜS) - Environmental Code
33. Päästeteenistuse seadus - Rescue Service Act
34. Jäätmeseadus - Waste Act
35. Veeseadus - Water Act

### Education Law (2)
36. Põhikooli- ja gümnaasiumiseadus (PGS) - Basic and Upper Secondary Schools Act
37. Ülikooliseadus (UKS) - Universities Act

### Police & Public Order (1)
38. Politsei- ja piirivalve seadus (PPVS) - Police and Border Guard Act

### Healthcare Law (2)
39. Tervishoiuteenuste korraldamise seadus (TTKS) - Health Services Organisation Act
40. Ravimiseadus - Medicinal Products Act

### Additional Files (4)
41-44. Asjaõigusseadus osad 1-5 (separate files)

## Schema Explanation
The ontology uses a custom `estleg` namespace (`https://example.org/estonian-legal#`) and is structured using JSON-LD:
- `@context`: Defines the vocabulary and namespaces (estleg, owl, rdf, rdfs, xsd, dc, skos)
- `@graph`: Contains the actual data nodes (Legal Provisions, Topic Clusters, Legal Concepts)
- `estleg`: The custom namespace for Estonian legal specific terms

### Core Classes
- `estleg:LegalProvision` - Individual legal provisions (paragraphs, sections)
- `estleg:TopicCluster` - Thematic groupings of provisions
- `estleg:LegalConcept` - Legal concepts and definitions

## How to Use the JSON-LD Files
1. Download the JSON-LD files from the `krr_outputs/` directory
2. Load them into a graph database (e.g., GraphDB, Neo4j with RDF plugin)
3. Parse them using RDF/JSON-LD libraries (Python: rdflib, JavaScript: jsonld.js)

## Example Queries/Usage
See `API_GUIDE.md` for SPARQL examples and REST API design patterns.

## GitHub Repo Structure
```
.
├── docs/                    # Documentation
│   ├── README.md           # This file
│   ├── API_GUIDE.md        # API usage guide
│   ├── SCHEMA_REFERENCE.md # Complete schema docs
│   └── VALIDATION_REPORT.md # Quality report
├── krr_outputs/            # JSON-LD ontology files (44 files)
├── shacl/                  # SHACL validation shapes
└── README.md               # Main project readme
```

## Repository
https://github.com/henrikaavik/estonian-legal-ontology

## Contribution Guidelines
Please submit pull requests with improvements. Ensure all JSON-LD files pass validation:
- Valid JSON syntax
- Consistent @context
- No duplicate @id values
- Proper estleg: namespace usage

## License
MIT License - See LICENSE file for details

---
*Last updated: March 1, 2026*
