# API Guide for Estonian Legal Ontology

## Overview

The Estonian Legal Ontology encodes 1,145 enacted laws (1,190 law files), 22,832 draft legislation entries, 3,812 domestic (state) regulations, 11,059 municipal (KOV) regulations, 12,137 Supreme Court decisions, 33,242 EU legal acts, and 22,290 EU court decisions as JSON-LD. All files live under `krr_outputs/`.

> **Maintenance note:** The counts in this guide are sourced from `krr_outputs/INDEX.json`, the per-pipeline reports under `krr_outputs/` (e.g. `amendment_history_report.json`, `institutional_competence_report.json`, `sanctions_report.json`), and `metadata.jsonld` (`estleg:statistics`). Update them from those canonical files when the corpus is regenerated.

## Directory Structure

```
krr_outputs/
  *.json              # 1,190 enacted law files (*_peep.json)
  amendments/         # 5,647 amendment chain files
  concepts/           # Legal concept graph + report
  curia/              # EU court decisions (CJEU/CURIA)
  eelnoud/            # Draft legislation (EIS)
  eurlex/             # EU legislation (EUR-Lex)
  institutions/       # 113 institutional competence files
  regulations/        # Domestic regulations (maarused)
    riik/                              # State-level (~3,820 files)
      *_peep.json                      # One file per regulation
      REGULATIONS_RIIK_INDEX.json      # State regulation registry (byIssuer counts)
    kov/                               # KOV/municipal (opt-in via --kov)
      <issuer_slug>/
        *_peep.json
      REGULATIONS_KOV_INDEX.json
  riigikohus/         # Supreme Court decisions (1993-2026)
  sanctions/          # 291 sanction cross-reference files
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

# INDEX.json is an object; the enacted-law entries live under index["laws"].
# Each entry has a "name" and a "files" LIST (not a scalar "file" key).
with open("krr_outputs/INDEX.json") as f:
    index = json.load(f)

# Load a specific law by name
for entry in index["laws"]:
    if "perekonnaseadus" in entry["name"]:
        for filename in entry["files"]:
            with open(f"krr_outputs/{filename}") as fh:
                law = json.load(fh)
            print(f"{filename}: {len(law.get('@graph', []))} nodes")
```

### Domestic Regulations

Domestic regulations (`maarused`) are state-level acts issued by Vabariigi Valitsus or by individual ministers. Each regulation file follows the same `@context` + `@graph` shape as a law file. The act-level node is typed `["owl:Ontology", "estleg:NationalRegulation", "estleg:GovernmentRegulation" | "estleg:MinisterialRegulation"]` and carries metadata such as `estleg:documentType` (`"maarus"`), `estleg:issuer`, `estleg:actNumber`, `estleg:globalId`, `estleg:terviktekstId`, `estleg:isKov`, `estleg:preambleText`, and `estleg:hasAnnex`.

```python
import json
from pathlib import Path

# Load all state-level regulations
regulations_dir = Path("krr_outputs/regulations/riik")
total = 0
for path in sorted(regulations_dir.glob("*_peep.json")):
    with open(path) as fh:
        data = json.load(fh)
    total += len(data.get("@graph", []))
print(f"Total state-regulation nodes: {total}")
```

```python
import json

# Load the state-regulation index for byIssuer counts
with open("krr_outputs/regulations/riik/REGULATIONS_RIIK_INDEX.json") as f:
    index = json.load(f)

print(f"Total state regulations: {index.get('total')}")
for issuer, count in index.get("byIssuer", {}).items():
    print(f"  {issuer}: {count}")
```

```python
import json
from pathlib import Path

# Find every regulation issued by a specific ministry/authority
target_issuer = "Sotsiaalminister"
matches = []
for path in sorted(Path("krr_outputs/regulations/riik").glob("*_peep.json")):
    with open(path) as fh:
        data = json.load(fh)
    for node in data.get("@graph", []):
        if "estleg:NationalRegulation" in (node.get("@type") or []):
            if node.get("estleg:issuer") == target_issuer:
                matches.append(path.name)
            break
print(f"{target_issuer}: {len(matches)} regulations")
```

```python
import json
from pathlib import Path

# Read a regulation's enabling-law preamble (legal basis)
sample = next(Path("krr_outputs/regulations/riik").glob("*_peep.json"))
with open(sample) as fh:
    data = json.load(fh)
for node in data.get("@graph", []):
    if "estleg:NationalRegulation" in (node.get("@type") or []):
        print("Issuer:", node.get("estleg:issuer"))
        print("Act number:", node.get("estleg:actNumber"))
        print("Preamble:", node.get("estleg:preambleText"))
        break
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
    print(node.get("rdfs:label"), node.get("estleg:eisNumber"))
```

### Supreme Court Decisions

```python
import json

# Load decisions for a specific year
with open("krr_outputs/riigikohus/riigikohus_2025_peep.json") as f:
    decisions = json.load(f)

for node in decisions["@graph"][:3]:
    print(node.get("estleg:caseNumber"), node.get("rdfs:label"))
```

### EU Legislation (EUR-Lex)

```python
import json

# Load EU directives
with open("krr_outputs/eurlex/eurlex_directives_peep.json") as f:
    directives = json.load(f)

for node in directives["@graph"][:3]:
    print(node.get("estleg:celexNumber"), node.get("rdfs:label"))
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

> **Which graph to query.** Parent-class membership such as `?x a estleg:LegalProvision`
> or `?x a estleg:Act` is materialised in the shipped `krr_outputs/combined_ontology.jsonld`,
> where a build-time type rollup (issue #519) stamps every instance with its entailed
> superclasses. A single `*_peep.json` only carries the **leaf** type
> (`estleg:LegalProvision_<law>`), so a bare `a estleg:LegalProvision` query against one peep
> returns nothing — query the combined graph for those. Types stamped directly on instances
> (`estleg:CourtDecision`, `estleg:Sanction`, `estleg:Institution`, `estleg:EULegislation`,
> the `*Regulation` act classes, and `estleg:Act` on act roots) match without the combined
> graph, but each lives in its own subdirectory — load that subcorpus. Several examples below
> join across subcorpora; the prerequisite files are noted inline.

### Find All Provisions for a Specific Topic Cluster
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

# Topic clusters are per-law concept nodes (estleg:Cluster_<ABBREV>_<n>); a
# provision links to one via estleg:requestedCluster. The bare type assertion
# needs the combined graph (see the note above).
SELECT ?provision ?text WHERE {
  ?provision a estleg:LegalProvision ;
             estleg:requestedCluster estleg:Cluster_PKS_1 ;
             estleg:summary ?text .
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

### Government Regulations Currently in Force
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?regulation ?label ?actNumber ?entry WHERE {
  ?regulation a estleg:GovernmentRegulation ;
              rdfs:label ?label ;
              estleg:issuer "Vabariigi Valitsus" ;
              estleg:actNumber ?actNumber ;
              estleg:entryIntoForce ?entry .
  FILTER NOT EXISTS { ?regulation estleg:repealDate ?repeal . }
} ORDER BY DESC(?entry)
```

### Draft Legislation Impacting a Specific Law

Drafts record the affected law as a string (`estleg:affectedLawName`), not as an
IRI edge, so match on it. Load `eelnoud/*_peep.json` plus
`controlled_vocabulary.jsonld` (for the phase label):

```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?draft ?phase ?name WHERE {
  ?draft a estleg:DraftLegislation ;
         estleg:affectedLawName ?affected ;
         estleg:legislativePhase ?phaseNode ;
         rdfs:label ?name .
  ?phaseNode rdfs:label ?phase .
  FILTER(CONTAINS(LCASE(STR(?affected)), "perekonnaseadus"))
}
```

### EU Directives Transposed into Estonian Law

Load the enacted-law peeps (which carry `estleg:transposesDirective`) together
with `eurlex/*_peep.json` (which type each directive `estleg:EULegislation`):

```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

SELECT ?directive ?estonianLaw WHERE {
  ?estonianLaw estleg:transposesDirective ?directive .
  ?directive a estleg:EULegislation .
}
```

### Sanctions by Penalty Type

The `sanctions/*.json` files anchor on the `estleg:Sanction` node and point to
the provision via `estleg:applicableProvision` (the inverse of the
`estleg:hasSanction` edge carried by the law peeps). Query that direction so the
example resolves from the `sanctions/` subcorpus alone:

```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

SELECT ?provision ?sanctionType ?maxPenalty WHERE {
  ?sanction a estleg:Sanction ;
            estleg:applicableProvision ?provision ;
            estleg:sanctionType ?sanctionType ;
            estleg:maxPenalty ?maxPenalty .
}
```

### Institutional Competence

`estleg:competentAuthority` is asserted on provisions in the enacted-law peeps,
while the `estleg:Institution` nodes live in `institutions/*.json`. Load both:

```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

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

Effected amendment events live in `amendments/amendments_<law>.json`. Query them
directly through `estleg:amends` (the populated direction; the `estleg:amendedBy`
back-link on act roots is not materialised across the corpus):

```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>

SELECT ?amendment ?amended ?date WHERE {
  ?amendment a estleg:AmendmentEvent ;
             estleg:amends ?amended ;
             estleg:amendmentDate ?date .
} ORDER BY DESC(?date)
```

### EuroVoc Subject Classification

EuroVoc subjects are classified at the **act level** — `dcterms:subject` is
attached to the act's metadata node (the one typed `estleg:Act`), not to
individual provisions. To list all acts tagged with a given EuroVoc
descriptor (here `4050` = "social security"; see
`data/eurovoc_domain_mapping.json` for the full verified id table):

```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?act ?label WHERE {
  ?act a estleg:Act ;
       dcterms:subject <http://eurovoc.europa.eu/4050> ;
       rdfs:label ?label .
}
```

To go the other way — list every EuroVoc subject assigned to a specific act.
Match the act by its clean title in `dcterms:title` (a plain string); `rdfs:label`
on an act root is the longer "… teemakaardistus" map label, not the bare title:

```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?subject WHERE {
  ?act a estleg:Act ;
       dcterms:title "Töölepingu seadus" ;
       dcterms:subject ?subject .
  FILTER(STRSTARTS(STR(?subject), "http://eurovoc.europa.eu/"))
}
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
