# Estonian Legal Ontology

A comprehensive, machine-readable ontology of Estonian legislation in JSON-LD format. Maps legal provisions, topic clusters, and legal concepts from ALL Estonian laws available in Riigi Teataja into a semantic knowledge graph suitable for advanced search, cross-referencing, and automated legal analysis.

**Status: 615 laws mapped** | **650+ JSON-LD files** | **29,800+ semantic nodes**

## Quick Start

### Load a single file with Python (rdflib)

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/perekonnaseadus_peep.json", format="json-ld")

for s, p, o in g:
    print(f"{s} → {p} → {o}")
```

### Load the combined ontology

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/combined_ontology.jsonld", format="json-ld")
print(f"Total triples: {len(g)}")
```

### SPARQL query example

```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?provision ?label WHERE {
  ?provision a estleg:LegalProvision ;
             rdfs:label ?label .
} LIMIT 20
```

## Coverage

| Category | Laws | Examples |
|----------|------|----------|
| Civil Law | 7 | TsÜS, VÕS, AÕS, PKS |
| Commercial & Economic | 6 | ÄS, PankrS, MKS |
| Criminal Law | 2 | KarS, KrMS |
| Administrative Law | 8 | HMS, KOKS, IKS |
| Procedural Law | 3 | TsMS, TMS |
| Constitutional | 4 | PS, RVastS |
| Environmental | 4 | KeÜS, JäätS, VeeS |
| Education | 2 | PGS, ÜKS |
| Healthcare | 2 | TTKS, RavS |
| Other | 6+ | PPVS, TLS, AÜS |

See [docs/README.md](docs/README.md) for the full list of mapped laws.

## Repository Structure

```
.
├── krr_outputs/              # JSON-LD ontology files (97+ files)
│   ├── *_peep.json           # Individual law mappings
│   ├── combined_ontology.jsonld  # All laws in one file
│   └── INDEX.json            # Master registry of all files
├── docs/                     # Documentation
│   ├── README.md             # Full project documentation
│   ├── API_GUIDE.md          # SPARQL and API usage guide
│   ├── SCHEMA_REFERENCE.md   # Complete schema reference
│   └── VALIDATION_REPORT.md  # Quality report
├── shacl/                    # SHACL validation shapes
├── scripts/                  # Generation and validation scripts
│   ├── validate_all.py       # Comprehensive validation
│   └── generate_kars_eriosa_jsonld.py  # KarS generator
├── reviews/                  # Law review request files
├── .github/workflows/        # CI pipeline
├── CHANGELOG.md              # Version history
└── LICENSE                   # MIT License
```

## Schema

The ontology uses the `estleg` namespace (`https://data.riik.ee/ontology/estleg#`) with three core classes:

- **`estleg:LegalProvision`** — Individual legal provisions (paragraphs, sections)
- **`estleg:TopicCluster`** — Thematic groupings of provisions
- **`estleg:LegalConcept`** — Legal concepts and definitions

See [docs/SCHEMA_REFERENCE.md](docs/SCHEMA_REFERENCE.md) for full schema documentation.

## Validation

Run the validation script locally:

```bash
python3 scripts/validate_all.py
```

CI runs automatically on every push to `main` that modifies `krr_outputs/`.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Ensure `python3 scripts/validate_all.py` passes
4. Submit a pull request

## License

MIT License — see [LICENSE](LICENSE) for details.
