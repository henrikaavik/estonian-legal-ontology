# API Guide for Estonian Legal Ontology

## How to Query the Ontology
The most common way to query this dataset is using a semantic graph database and SPARQL.

## Example SPARQL Queries

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

### Trace Cross-References
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

SELECT ?source ?target WHERE {
  ?source a estleg:LegalProvision ;
          estleg:references ?target .
}
```

## How to Integrate with Applications
Developers can load the `.jsonld` files into Python using `rdflib`:

```python
from rdflib import Graph

g = Graph()
g.parse("data/law1.jsonld", format="json-ld")

for s, p, o in g:
    print(f"Subject: {s}, Predicate: {p}, Object: {o}")
```

## REST API Design Suggestions
If exposing via an API, typical endpoints might include:
- `GET /api/provisions/:id` - Returns a specific legal provision and its full text.
- `GET /api/clusters/:id/provisions` - Returns all provisions mapped to a cluster.
- `GET /api/concepts/:id/definitions` - Retrieves definitions associated with a legal concept.
