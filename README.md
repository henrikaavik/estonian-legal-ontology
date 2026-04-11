# Estonian Legal Ontology

A comprehensive, machine-readable ontology of Estonian and EU legislation in JSON-LD format. Maps **enacted laws**, **draft legislation**, **Supreme Court decisions**, **EU legal acts**, and **EU court decisions** into a semantic knowledge graph suitable for advanced search, cross-referencing, and automated legal analysis.

**Status: 615 enacted laws + 22,832 drafts + 12,137 court decisions + 33,242 EU acts + 22,290 EU court decisions** | **700+ JSON-LD files** | **120,000+ semantic nodes**

**Integration features:** Cross-law reference links | Court decision → provision links | EU directive transposition mapping | EuroVoc taxonomy | Amendment history | Legal concept graph | Deontic classification | Institutional competence | Sanction index | Semantic similarity | Temporal validity

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

### Load EU court decisions

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/curia/curia_combined.jsonld", format="json-ld")
print(f"EU court decision triples: {len(g)}")
```

### Query cross-law references

```python
from rdflib import Graph, Namespace

ESTLEG = Namespace("https://data.riik.ee/ontology/estleg#")
g = Graph()
g.parse("krr_outputs/karistusseadustik_peep.json", format="json-ld")

# Find all provisions this law references in other laws
for s, p, o in g.triples((None, ESTLEG.references, None)):
    print(f"{s} references {o}")
```

### Query court decision → provision links

```python
from rdflib import Graph, Namespace

ESTLEG = Namespace("https://data.riik.ee/ontology/estleg#")
g = Graph()
g.parse("krr_outputs/riigikohus/riigikohus_2025_peep.json", format="json-ld")

# Find all provision-level links
for s, p, o in g.triples((None, ESTLEG.interpretsLaw, None)):
    print(f"Decision {s} interprets {o}")
```

### SPARQL query examples

```sparql
# Find all Supreme Court decisions referencing KarS
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
# Find drafts amending karistusseadustik
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
# Find EU directives currently in force
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?act ?title ?date WHERE {
  ?act a estleg:EULegislation ;
       rdfs:label ?title ;
       estleg:euDocumentType estleg:EUDocType_Directive ;
       estleg:inForce "true"^^xsd:boolean ;
       estleg:documentDate ?date .
} ORDER BY DESC(?date) LIMIT 20
```

```sparql
# Find EU Court of Justice judgments
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?decision ?title ?ecli ?date WHERE {
  ?decision a estleg:EUCourtDecision ;
            rdfs:label ?title ;
            estleg:euCourtDecisionType estleg:EUDecType_Judgment ;
            estleg:ecliIdentifier ?ecli ;
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
| Inter-ministerial Review | Kooskolastamine | 13,670 |
| Submitted to Government | Esitatud VV-le | 9,040 |

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

### EU Court Decisions (CURIA)

| Type | Estonian | Count |
|------|----------|-------|
| AG Opinion | Kohtujuristi ettepanek | 9,952 |
| Order | Kohtumaarus | 6,619 |
| Judgment | Kohtuotsus | 5,641 |
| Court Opinion | Kohtu arvamus | 17 |

| Court | Estonian | Count |
|-------|----------|-------|
| Court of Justice | Euroopa Kohus | 17,720 |
| General Court | Uldkohus | 4,036 |
| Civil Service Tribunal | Avaliku Teenistuse Kohus | 534 |

Source: EUR-Lex SPARQL endpoint (22,290 decisions with Estonian translations)

## Data Sources

| Source | URL | Data | Script |
|--------|-----|------|--------|
| **Riigi Teataja** | https://www.riigiteataja.ee | Enacted laws (XML API) | `scripts/generate_all_laws.py` |
| **EIS** | https://eelnoud.valitsus.ee | Draft legislation (RSS feeds) | `scripts/generate_draft_legislation.py` |
| **RIK / Riigikohus** | https://rikos.rik.ee | Supreme Court decisions (HTML search) | `scripts/generate_court_decisions.py` |
| **EUR-Lex** | https://eur-lex.europa.eu | EU legislation (SPARQL) | `scripts/generate_eu_legislation.py` |
| **EUR-Lex / CURIA** | https://eur-lex.europa.eu | EU court decisions (SPARQL) | `scripts/generate_eu_court_decisions.py` |

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

## Integration & Cross-Linking

The ontology includes 14 integration layers that connect laws, court decisions, drafts, and EU legislation:

| Feature | Script | Description |
|---------|--------|-------------|
| Cross-law references | `extract_cross_references.py` | Parses citation patterns (KarS § 121, VÕS § 208 lg 1) and adds IRI links |
| Bidirectional links | `generate_inverse_references.py` | Auto-generates `referencedBy` inverses |
| Court → provision links | `extract_court_provision_links.py` | Links court decisions to specific provisions they interpret |
| EU transposition | `generate_transposition_mapping.py` | Maps Estonian laws to EU directives via EUR-Lex |
| EuroVoc taxonomy | `classify_eurovoc.py` | Classifies laws with standardised EU subject taxonomy |
| Amendment history | `generate_amendment_history.py` | Tracks amendment chains between laws |
| Legal concepts | `extract_legal_concepts.py` | Extracts defined terms and links across laws |
| Draft impact | `extract_draft_impact.py` | Provision-level impact analysis for pending drafts |
| Temporal validity | `extract_temporal_data.py` | Entry-into-force and repeal dates |
| Cross-border harmonisation | `generate_harmonisation_links.py` | Links to parallel EU transpositions |
| Deontic classification | `classify_deontic.py` | Classifies provisions as obligations/rights/permissions/prohibitions |
| Institutional competence | `extract_institutional_competence.py` | Maps which institution enforces what |
| Sanctions | `extract_sanctions.py` | Penalty and sanction cross-reference index |
| Semantic similarity | `generate_similarity_index.py` | Keyword-based similarity between provisions |

### Running integration scripts

```bash
# Cross-law references (run first — other scripts depend on this)
python3 scripts/extract_cross_references.py
python3 scripts/generate_inverse_references.py

# Court decision links
python3 scripts/extract_court_provision_links.py

# EU integration
python3 scripts/generate_transposition_mapping.py
python3 scripts/classify_eurovoc.py
python3 scripts/generate_harmonisation_links.py

# Temporal and amendment data
python3 scripts/extract_temporal_data.py
python3 scripts/generate_amendment_history.py

# Legal concepts and classification
python3 scripts/extract_legal_concepts.py
python3 scripts/classify_deontic.py
python3 scripts/extract_institutional_competence.py
python3 scripts/extract_sanctions.py

# Draft impact and similarity
python3 scripts/extract_draft_impact.py
python3 scripts/generate_similarity_index.py
```

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
│   ├── eurlex/               # EU legislation
│   │   ├── eurlex_schema.json            # Schema definitions
│   │   ├── eurlex_regulations_peep.json  # EU regulations
│   │   ├── eurlex_directives_peep.json   # EU directives
│   │   ├── eurlex_decisions_peep.json    # EU decisions
│   │   ├── eurlex_combined.jsonld        # All EU acts combined
│   │   └── EURLEX_INDEX.json             # EU legislation registry
│   ├── curia/                # EU court decisions
│   │   ├── curia_schema.json             # Schema definitions
│   │   ├── curia_judgments_peep.json      # CJEU judgments
│   │   ├── curia_orders_peep.json        # Court orders
│   │   ├── curia_ag_opinions_peep.json   # AG opinions
│   │   ├── curia_court_opinions_peep.json # Court opinions
│   │   ├── curia_other_peep.json         # Other decision types
│   │   ├── curia_combined.jsonld         # All EU decisions combined
│   │   └── CURIA_INDEX.json              # EU court decision registry
│   ├── concepts/             # Legal concept cross-reference graph
│   ├── institutions/         # Institutional competence mappings
│   ├── sanctions/            # Penalty and sanction index
│   ├── amendments/           # Amendment chain data
│   ├── cross_references_report.json   # Cross-law reference index
│   ├── inverse_references_report.json # Bidirectional reference index
│   ├── court_provision_links_report.json # Court → provision link index
│   ├── transposition_mapping.json     # EU directive transposition map
│   ├── transposition_schema.json      # Transposition schema definitions
│   ├── eurovoc_classification.json    # EuroVoc topic classification
│   ├── amendment_history_report.json  # Amendment history index
│   ├── deontic_classification_report.json # Deontic classification index
│   ├── draft_impact_report.json       # Draft impact analysis index
│   ├── temporal_data_report.json      # Temporal validity index
│   ├── similarity_index.json          # Semantic similarity index
│   └── similarity_report.json         # Similarity analysis report
├── docs/                     # Documentation
│   ├── README.md             # Full project documentation
│   ├── API_GUIDE.md          # SPARQL and API usage guide
│   ├── SCHEMA_REFERENCE.md   # Complete schema reference
│   ├── VALIDATION_REPORT.md  # Quality report
│   ├── INTEGRATION_IDEAS.md  # Integration improvement ideas
│   └── DUPLICATE_IDS_REPORT.md # Cross-file @id collision audit
├── shacl/                    # SHACL validation shapes
├── scripts/                  # Generation and validation scripts (25 scripts)
│   ├── generate_all_laws.py           # Enacted laws generator
│   ├── generate_draft_legislation.py  # Draft legislation generator
│   ├── generate_court_decisions.py    # Court decisions generator
│   ├── generate_eu_legislation.py     # EU legislation generator
│   ├── generate_eu_court_decisions.py # EU court decisions generator
│   ├── generate_kars_eriosa_jsonld.py # KarS special parts
│   ├── generate_missing_parts.py     # Generate missing law parts
│   ├── extract_cross_references.py   # Cross-law citation links
│   ├── generate_inverse_references.py # Bidirectional reference links
│   ├── extract_court_provision_links.py # Court → provision links
│   ├── generate_transposition_mapping.py # EU directive transposition
│   ├── classify_eurovoc.py           # EuroVoc taxonomy classification
│   ├── generate_amendment_history.py  # Amendment chain tracking
│   ├── extract_legal_concepts.py     # Legal concept extraction
│   ├── extract_draft_impact.py       # Draft impact analysis
│   ├── extract_temporal_data.py      # Temporal validity dates
│   ├── generate_harmonisation_links.py # EU harmonisation links
│   ├── classify_deontic.py           # Deontic classification
│   ├── extract_institutional_competence.py # Institutional competence
│   ├── extract_sanctions.py          # Sanctions extraction
│   ├── generate_similarity_index.py  # Semantic similarity index
│   ├── fix_all_issues.py             # Batch issue fixer
│   ├── run_all_integration.py        # Master integration orchestrator
│   ├── validate_all.py               # Comprehensive validation
│   └── estleg_common.py              # Shared utilities and abbreviations
├── reviews/                  # Law review request files
├── .github/workflows/        # CI pipeline
├── CHANGELOG.md              # Version history
└── LICENSE                   # MIT License
```

## Schema

The ontology uses the `estleg` namespace (`https://data.riik.ee/ontology/estleg#`) with 18 core classes:

**Enacted Law:**
- **`estleg:LegalProvision`** -- Individual legal provisions (paragraphs, sections)
- **`estleg:TopicCluster`** -- Thematic groupings of provisions
- **`estleg:LegalConcept`** -- Legal concepts and definitions (with SKOS cross-references)

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

**EU Court Decisions:**
- **`estleg:EUCourtDecision`** -- CJEU decisions (judgments, orders, AG opinions)
- **`estleg:EUCourtDecisionType`** -- Judgment, Order, AG Opinion, Court Opinion
- **`estleg:EUCourt`** -- Court of Justice, General Court, Civil Service Tribunal

**Integration & Analysis:**
- **`estleg:Sanction`** -- Penalties and sanctions extracted from law text
- **`estleg:Institution`** -- State institutions with legal competences
- **`estleg:NormativeType`** -- Deontic classification (Obligation, Right, Permission, Prohibition)

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

# Re-fetch EU court decisions from EUR-Lex
python3 scripts/generate_eu_court_decisions.py

# Run all integration scripts in dependency order (recommended):
python3 scripts/run_all_integration.py

# Or run individually:
python3 scripts/extract_cross_references.py
python3 scripts/generate_inverse_references.py
python3 scripts/extract_court_provision_links.py
python3 scripts/generate_transposition_mapping.py
python3 scripts/classify_eurovoc.py
python3 scripts/extract_temporal_data.py
python3 scripts/generate_amendment_history.py
python3 scripts/extract_legal_concepts.py
python3 scripts/classify_deontic.py
python3 scripts/extract_institutional_competence.py
python3 scripts/extract_sanctions.py
python3 scripts/extract_draft_impact.py
python3 scripts/generate_similarity_index.py
python3 scripts/generate_harmonisation_links.py
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Ensure `python3 scripts/validate_all.py` passes
4. Submit a pull request

## License

MIT License -- see [LICENSE](LICENSE) for details.
