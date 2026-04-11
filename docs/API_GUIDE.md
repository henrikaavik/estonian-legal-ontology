# API Guide for Estonian Legal Ontology

## Overview

The Estonian Legal Ontology encodes 635 enacted laws, 22,832 draft legislation entries, 12,137 Supreme Court decisions, 33,242 EU legal acts, and 22,290 EU court decisions as JSON-LD. All files live under `krr_outputs/`.

## Directory Structure

```
krr_outputs/
  *.json              # 635 enacted law files (*_peep.json)
  amendments/         # 376 amendment chain files
  concepts/           # Legal concept graph + report
  curia/              # EU court decisions (CJEU/CURIA)
  eelnoud/            # Draft legislation (EIS)
  eurlex/             # EU legislation (EUR-Lex)
  institutions/       # 85 institutional competence files
  riigikohus/         # Supreme Court decisions (1993-2026)
  sanctions/          # 152 sanction cross-reference files
  INDEX.json          # Master registry of all enacted laws
```

## Loading Data with Python

### Enacted Laws

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/perekonnaseadus_peep.json", format="json-ld")

for s, p, o in g:
    print(f"Subject: {s}, Predicate: {p}, Object: {o}")
```

### Loading Multiple Files

```python
import json
from pathlib import Path

# Load the master index to discover all enacted law files
with open("krr_outputs/INDEX.json") as f:
    index = json.load(f)

# Load a specific law by name
for entry in index:
    if "perekonnaseadus" in entry.get("file", ""):
        with open(f"krr_outputs/{entry['file']}") as f:
            law = json.load(f)
        print(f"Nodes: {len(law.get('@graph', []))}")
```

### Draft Legislation

```python
import json

# Load the draft legislation index
with open("krr_outputs/eelnoud/EELNOUD_INDEX.json") as f:
    index = json.load(f)
print(f"Total drafts: {index['total_drafts']}")

# Load drafts by legislative phase
with open("krr_outputs/eelnoud/eelnoud_submission_peep.json") as f:
    submitted = json.load(f)
for node in submitted["@graph"][:3]:
    print(node.get("schema:name"), node.get("estleg:eisNumber"))
```

### Supreme Court Decisions

```python
import json

# Load decisions for a specific year
with open("krr_outputs/riigikohus/riigikohus_2025_peep.json") as f:
    decisions = json.load(f)

for node in decisions["@graph"][:3]:
    print(node.get("estleg:caseNumber"), node.get("estleg:caseType"))
```

### EU Legislation (EUR-Lex)

```python
import json

# Load EU directives
with open("krr_outputs/eurlex/eurlex_directives_peep.json") as f:
    directives = json.load(f)

for node in directives["@graph"][:3]:
    print(node.get("estleg:celexNumber"), node.get("schema:name"))
```

### EU Court Decisions (CURIA)

```python
import json

# Load CJEU judgments
with open("krr_outputs/curia/curia_judgments_peep.json") as f:
    judgments = json.load(f)

for node in judgments["@graph"][:3]:
    print(node.get("estleg:ecliIdentifier"), node.get("estleg:euCaseNumber"))
```

### Sanctions

```python
import json
from pathlib import Path

# Load all sanction files for a specific law
sanctions_dir = Path("krr_outputs/sanctions")
for f in sorted(sanctions_dir.glob("*_peep.json"))[:3]:
    with open(f) as fh:
        data = json.load(fh)
    print(f"{f.name}: {len(data.get('@graph', []))} sanctions")
```

### Institutions

```python
import json
from pathlib import Path

# Load institutional competence mappings
institutions_dir = Path("krr_outputs/institutions")
for f in sorted(institutions_dir.glob("*.json"))[:3]:
    with open(f) as fh:
        data = json.load(fh)
    print(f"{f.name}: {len(data.get('@graph', []))} nodes")
```

### Amendment Chains

```python
import json
from pathlib import Path

# Load amendment history for a specific law
amendments_dir = Path("krr_outputs/amendments")
for f in sorted(amendments_dir.glob("*_peep.json"))[:3]:
    with open(f) as fh:
        data = json.load(fh)
    print(f"{f.name}: {len(data.get('@graph', []))} amendment events")
```

### Legal Concepts

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/concepts/concepts_combined.jsonld", format="json-ld")
print(f"Total triples: {len(g)}")
```

## SPARQL Queries

The most powerful way to query this dataset is loading files into a semantic graph database (Apache Jena, Blazegraph, Oxigraph, etc.) and using SPARQL.

### Find All Provisions for a Specific Topic Cluster
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX schema: <http://schema.org/>

SELECT ?provision ?text WHERE {
  ?provision a estleg:LegalProvision ;
             estleg:topicCluster <https://data.riik.ee/taxonomy/topic/OiguslikAlus> ;
             schema:text ?text .
}
```

### Trace Cross-References Between Laws
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

SELECT ?source ?target WHERE {
  ?source a estleg:LegalProvision ;
          estleg:references ?target .
  ?target estleg:referencedBy ?source .
}
```

### Court Decision to Provision Links
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

SELECT ?decision ?provision WHERE {
  ?decision a estleg:CourtDecision ;
            estleg:interpretsLaw ?provision .
}
```

### Draft Legislation Impacting a Specific Law
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX schema: <http://schema.org/>

SELECT ?draft ?phase ?name WHERE {
  ?draft a estleg:DraftLegislation ;
         estleg:amendsLaw ?law ;
         estleg:legislativePhase ?phase ;
         schema:name ?name .
  ?law schema:name "Perekonnaseadus" .
}
```

### EU Directives Transposed into Estonian Law
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

SELECT ?directive ?estonianLaw WHERE {
  ?estonianLaw estleg:transposesDirective ?directive .
  ?directive a estleg:EULegislation .
}
```

### Sanctions by Penalty Type
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX schema: <http://schema.org/>

SELECT ?provision ?sanctionText WHERE {
  ?provision estleg:hasSanction ?sanction .
  ?sanction a estleg:Sanction ;
            schema:text ?sanctionText .
}
```

### Institutional Competence
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX schema: <http://schema.org/>

SELECT ?institution ?provision WHERE {
  ?provision estleg:competentAuthority ?institution .
  ?institution a estleg:Institution .
}
```

### Deontic Classification
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?provision ?label WHERE {
  ?provision estleg:normativeType estleg:NormType_Obligation ;
             rdfs:label ?label .
}
```

### Amendment History
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

SELECT ?law ?amendment ?date WHERE {
  ?law estleg:amendedBy ?amendment .
  ?amendment a estleg:AmendmentEvent ;
             estleg:amendmentDate ?date .
} ORDER BY ?date
```

For the full list of classes, properties, and SPARQL examples, see [SCHEMA_REFERENCE.md](SCHEMA_REFERENCE.md).

## REST API Design Suggestions

If exposing via an API, typical endpoints might include:

- `GET /api/provisions/:id` - Returns a specific legal provision and its full text.
- `GET /api/provisions/:id/references` - Returns cross-references from a provision.
- `GET /api/clusters/:id/provisions` - Returns all provisions mapped to a cluster.
- `GET /api/concepts/:id/definitions` - Retrieves definitions associated with a legal concept.
- `GET /api/laws/:id/amendments` - Returns amendment history for a law.
- `GET /api/laws/:id/sanctions` - Returns sanctions defined in a law.
- `GET /api/courts/decisions?year=2025` - Returns Supreme Court decisions by year.
- `GET /api/drafts?phase=submission` - Returns draft legislation by phase.
- `GET /api/eu/legislation?type=directive` - Returns EU directives.
- `GET /api/eu/decisions?court=CourtOfJustice` - Returns CJEU decisions.
- `GET /api/institutions/:id/provisions` - Returns provisions under an institution's competence.
