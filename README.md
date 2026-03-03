# Estonian Legal Ontology

A comprehensive, machine-readable ontology of Estonian and EU legislation in JSON-LD format. Maps **enacted laws**, **draft legislation**, **Supreme Court decisions**, and **EU legal acts** into a semantic knowledge graph suitable for advanced search, cross-referencing, and automated legal analysis.

**Status: 615 enacted laws + 22,800+ drafts + 12,100+ court decisions + 33,200+ EU legal acts** | **700+ JSON-LD files** | **98,000+ semantic nodes**

## Quick Start

### Load a single file with Python (rdflib)

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/perekonnaseadus_peep.json", format="json-ld")

for s, p, o in g:
    print(f"{s} -> {p} -> {o}")
```

### Load draft legislation

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/eelnoud/eelnoud_combined.jsonld", format="json-ld")
print(f"Draft triples: {len(g)}")
```

### Load court decisions

```python
from rdflib import Graph

g = Graph()
for year in range(2020, 2027):
    g.parse(f"krr_outputs/riigikohus/riigikohus_{year}_peep.json", format="json-ld")
print(f"Court decision triples: {len(g)}")
```

### Load EU legislation

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/eurlex/eurlex_combined.jsonld", format="json-ld")
print(f"EU legislation triples: {len(g)}")
```

### SPARQL query examples

```sparql
-- Find all Supreme Court decisions referencing KarS
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?decision ?label ?date WHERE {
  ?decision a estleg:CourtDecision ;
            rdfs:label ?label ;
            estleg:referencedLaw ?law ;
            estleg:decisionDate ?date .
  FILTER(CONTAINS(?law, "KarS"))
} ORDER BY DESC(?date) LIMIT 20
```

```sparql
-- Find drafts amending karistusseadustik
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?draft ?title ?phase WHERE {
  ?draft a estleg:DraftLegislation ;
         rdfs:label ?title ;
         estleg:legislativePhase ?phaseNode ;
         estleg:affectedLawName ?law .
  ?phaseNode rdfs:label ?phase .
  FILTER(CONTAINS(LCASE(?law), "karistusseadustik"))
}
```

```sparql
-- Find EU directives currently in force
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?act ?title ?date WHERE {
  ?act a estleg:EULegislation ;
       rdfs:label ?title ;
       estleg:euDocumentType estleg:EUDocType_Directive ;
       estleg:inForce "true"^^xsd:boolean ;
       estleg:documentDate ?date .
} ORDER BY DESC(?date) LIMIT 20
```

## Coverage

### Enacted Laws (Riigi Teataja)

| Category | Laws | Examples |
|----------|------|----------|
| Civil Law | 7 | TsUS, VOS, AOS, PKS |
| Commercial & Economic | 6 | AS, PankrS, MKS |
| Criminal Law | 2 | KarS, KrMS |
| Administrative Law | 8 | HMS, KOKS, IKS |
| Procedural Law | 3 | TsMS, TMS |
| Constitutional | 4 | PS, RVastS |
| Environmental | 4 | KeUS, JaatS, VeeS |
| Other | 581+ | PPVS, TLS, AUS, ... |

### Draft Legislation (EIS)

| Phase | Estonian | Count |
|-------|----------|-------|
| Public Consultation | Avalik konsultatsioon | 122 |
| Inter-ministerial Review | Kooskolastamine | 13,652 |
| Submitted to Government | Esitatud VV-le | 9,036 |

### Supreme Court Decisions (Riigikohus)

| Case Type | Estonian | Count |
|-----------|----------|-------|
| Administrative | Haldusasi | 9,561 |
| Civil | Tsiviilasi | 970 |
| Criminal | Kriminaalasi | 484 |
| Constitutional Review | Pohiseaduslikkuse jarelevalve | 336 |
| Misdemeanor | Vaarteoasi | 107 |
| Other | Muu | 679 |

Years covered: 1993-2026 (12,137 decisions total)

### EU Legislation (EUR-Lex)

| Type | Estonian | Total | In Force |
|------|----------|-------|----------|
| Regulation | EL maarus | 18,784 | 5,095 |
| Directive | EL direktiiv | 3,114 | 939 |
| Decision | EL otsus | 11,344 | 6,097 |

Source: EUR-Lex SPARQL endpoint (33,242 acts with Estonian translations)

## Data Sources

| Source | URL | Data | Script |
|--------|-----|------|--------|
| **Riigi Teataja** | https://www.riigiteataja.ee | Enacted laws (XML API) | `scripts/generate_all_laws.py` |
| **EIS** | https://eelnoud.valitsus.ee | Draft legislation (RSS feeds) | `scripts/generate_draft_legislation.py` |
| **RIK / Riigikohus** | https://rikos.rik.ee | Supreme Court decisions (HTML search) | `scripts/generate_court_decisions.py` |
| **EUR-Lex** | https://eur-lex.europa.eu | EU legislation (SPARQL) | `scripts/generate_eu_legislation.py` |

### API Details

**Riigi Teataja** (enacted laws):
- Search: `GET https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi?leht=N&dokument=seadus`
- XML: `GET https://www.riigiteataja.ee/akt/GLOBALID.xml`

**EIS** (draft legislation):
- Public consultation: `GET https://eelnoud.valitsus.ee/main/mount/rss/home/publicConsult.rss`
- Review: `GET https://eelnoud.valitsus.ee/main/mount/rss/home/review.rss`
- Submission: `GET https://eelnoud.valitsus.ee/main/mount/rss/home/submission.rss`

**RIK** (court decisions):
- Search: `GET https://rikos.rik.ee/?aasta=YYYY&pageSize=100&lk=N`
- Individual: `https://www.riigikohus.ee/et/lahendid/?asjaNr=CASE_NR`

**EUR-Lex** (EU legislation):
- SPARQL endpoint: `https://publications.europa.eu/webapi/rdf/sparql` (open, no auth)
- CDM ontology classes: `cdm:regulation`, `cdm:directive`, `cdm:decision`
- Estonian language filter: `<http://publications.europa.eu/resource/authority/language/EST>`
- EUR-Lex page: `https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:{CELEX_NR}`

## Repository Structure

```
.
├── krr_outputs/              # JSON-LD ontology files (700+ files)
│   ├── *_peep.json           # Individual enacted law mappings
│   ├── combined_ontology.jsonld  # All enacted laws in one file
│   ├── INDEX.json            # Enacted law registry
│   ├── eelnoud/              # Draft legislation
│   │   ├── eelnoud_schema.json           # Schema definitions
│   │   ├── eelnoud_*_peep.json           # Phase-grouped drafts
│   │   ├── eelnoud_combined.jsonld       # All drafts combined
│   │   └── EELNOUD_INDEX.json            # Draft registry
│   ├── riigikohus/           # Supreme Court decisions
│   │   ├── riigikohus_schema.json        # Schema definitions
│   │   ├── riigikohus_YYYY_peep.json     # Per-year decisions (1993-2026)
│   │   └── RIIGIKOHUS_INDEX.json         # Court decision registry
│   └── eurlex/               # EU legislation
│       ├── eurlex_schema.json            # Schema definitions
│       ├── eurlex_regulations_peep.json  # EU regulations
│       ├── eurlex_directives_peep.json   # EU directives
│       ├── eurlex_decisions_peep.json    # EU decisions
│       ├── eurlex_combined.jsonld        # All EU acts combined
│       └── EURLEX_INDEX.json             # EU legislation registry
├── docs/                     # Documentation
│   ├── README.md             # Full project documentation
│   ├── API_GUIDE.md          # SPARQL and API usage guide
│   ├── SCHEMA_REFERENCE.md   # Complete schema reference
│   └── VALIDATION_REPORT.md  # Quality report
├── shacl/                    # SHACL validation shapes
├── scripts/                  # Generation and validation scripts
│   ├── validate_all.py                # Comprehensive validation
│   ├── generate_all_laws.py           # Enacted laws generator
│   ├── generate_draft_legislation.py  # Draft legislation generator
│   ├── generate_court_decisions.py    # Court decisions generator
│   ├── generate_eu_legislation.py    # EU legislation generator
│   └── generate_kars_eriosa_jsonld.py # KarS special parts
├── reviews/                  # Law review request files
├── .github/workflows/        # CI pipeline
├── CHANGELOG.md              # Version history
└── LICENSE                   # MIT License
```

## Schema

The ontology uses the `estleg` namespace (`https://data.riik.ee/ontology/estleg#`) with twelve core classes:

**Enacted Law:**
- **`estleg:LegalProvision`** -- Individual legal provisions (paragraphs, sections)
- **`estleg:TopicCluster`** -- Thematic groupings of provisions
- **`estleg:LegalConcept`** -- Legal concepts and definitions

**Draft Legislation:**
- **`estleg:DraftLegislation`** -- Legislative drafts not yet enacted (eelnoud)
- **`estleg:LegislativePhase`** -- PublicConsultation -> Review -> Submission
- **`estleg:DraftType`** -- Bill, AmendmentBill, GovernmentRegulation, etc.

**Court Decisions:**
- **`estleg:CourtDecision`** -- Supreme Court decisions (judgments, rulings)
- **`estleg:CaseType`** -- Criminal, Civil, Administrative, Constitutional Review, Misdemeanor
- **`estleg:DecisionType`** -- Judgment, Ruling, Resolution

**EU Legislation:**
- **`estleg:EULegislation`** -- EU legal acts (regulations, directives, decisions)
- **`estleg:EUDocumentType`** -- Regulation, Directive, Decision
- **`estleg:EUInstitution`** -- European Commission, Council, Parliament, etc.

See [docs/SCHEMA_REFERENCE.md](docs/SCHEMA_REFERENCE.md) for full schema documentation.

## Validation

```bash
python3 scripts/validate_all.py
```

CI runs automatically on every push to `main` that modifies `krr_outputs/`.

## Refreshing Data

```bash
# Re-fetch all enacted laws from Riigi Teataja
python3 scripts/generate_all_laws.py

# Re-fetch draft legislation from EIS
python3 scripts/generate_draft_legislation.py

# Re-fetch Supreme Court decisions from RIK
python3 scripts/generate_court_decisions.py

# Re-fetch EU legislation from EUR-Lex
python3 scripts/generate_eu_legislation.py
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Ensure `python3 scripts/validate_all.py` passes
4. Submit a pull request

## License

MIT License -- see [LICENSE](LICENSE) for details.
