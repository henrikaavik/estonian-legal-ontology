# KRR Ontology Usage Guide

**Project:** Estonian Legislation Ontology (KRR - Knowledge Representation and Reasoning)  
**Last Updated:** 2026-02-27  
**Status:** Active Development

---

## Table of Contents

1. [What Is This Ontology For](#1-what-is-this-ontology-for)
2. [How to Read JSON-LD Output](#2-how-to-read-json-ld-output)
3. [How to Use RDF/Turtle Files](#3-how-to-use-rdfturtle-files)
4. [Example Queries and Use Cases](#4-example-queries-and-use-cases)
5. [File Locations and Structure](#5-file-locations-and-structure)
6. [Glossary](#6-glossary)

---

## 1. What Is This Ontology For

The Estonian Legislation Ontology is a **semantic representation system** for Estonian laws. It transforms legal texts into structured, machine-readable data that computers can understand, query, and reason about.

### Purpose

| Goal | Description |
|------|-------------|
| **Structured Ingestion** | Convert unstructured legal texts (from Riigi Teataja) into structured data |
| **Traceable References** | Track cross-references between laws, articles, and provisions |
| **Change/Version Handling** | Monitor amendments and track legal changes over time |
| **Queryability** | Enable complex searches across legal corpora |
| **Legal Reasoning** | Support automated analysis of legal norms (rights, obligations, conditions) |

### Target Laws

Initially focused on **Tsiviilseadustiku üldosa seadus (TsÜS)** — the Estonian Civil Code General Part — with plans to extend to other legislation (VÕS, AÕS, PärS, etc.).

### Who Uses This

- **Legal researchers** analyzing legal structures
- **Developers** building legal tech applications
- **Policy analysts** tracking legislative changes
- **AI systems** performing legal reasoning

---

## 2. How to Read JSON-LD Output

### What Is JSON-LD?

**JSON-LD** (JavaScript Object Notation for Linked Data) is a lightweight format that:
- Looks like regular JSON (easy for developers)
- Contains semantic meaning (understandable by machines)
- Can be converted to RDF triples for graph databases

### File Structure

JSON-LD output files are located in `~/.openclaw/shared/krr_outputs/`:

| File | Purpose |
|------|---------|
| `*_structural_analysis_*.json` | Raw structural analysis of legal provisions |
| `*_enhanced_*.json` | Semantically enriched norm analysis |
| `*_source_snapshot.json` | Original text from Riigi Teataja |

### Key Sections Explained

#### 2.1 Selection Metadata
```json
{
  "selection": {
    "globaalID": 254359,
    "pealkiri": "Tsiviilseadustiku üldosa seadus",
    "lyhend": "TsÜS",
    "kehtivus": {
      "algus": "2003-07-01",
      "lopp": "2003-12-26"
    },
    "staatus": "avaldatud",
    "url": "/akt/254359.xml"
  }
}
```
**Fields:**
- `globaalID` — Unique identifier in Riigi Teataja
- `pealkiri` — Full title of the act
- `lyhend` — Short abbreviation
- `kehtivus` — Validity period (start/end dates)
- `staatus` — Publication status

#### 2.2 Norm Analysis
```json
{
  "paragrahvNr": "7",
  "pealkiri": "Füüsilise isiku õigusvõime",
  "norms": [
    {
      "id": "7_1",
      "type": "other",
      "subject_guess": "kohus",
      "condition_present": "ei",
      "exception_present": "ei",
      "source": "Füüsilise isiku (inimese) õigusvõime on võime omada tsiviilõigusi..."
    }
  ]
}
```
**Fields:**
- `paragrahvNr` — Article/section number (§)
- `pealkiri` — Article title
- `id` — Unique norm identifier (format: `{article}_{subsection}`)
- `type` — Norm type: `right`, `obligation`, `other`
- `subject_guess` — Who the norm applies to
- `condition_present` — Whether conditions exist (jah/ei)
- `exception_present` — Whether exceptions exist (jah/ei)
- `source` — Original legal text

#### 2.3 Enhanced Semantic Analysis
```json
{
  "id": "7_1",
  "original_type": "other",
  "enhanced_type": "definition",
  "norm_category": "legal_capacity",
  "subject": {
    "primary": "füüsiline_isik",
    "semantics": "iga inimene (sünnist surmani)"
  },
  "action": {
    "type": "omamine_kandmine",
    "semantics": "õiguste omamine ja kohustuste kandmine"
  },
  "conditions": [],
  "exceptions": [],
  "key_attributes": {
    "ühetaoline": "võrdne kõigile",
    "piiramatu": "ei ole piiranguid"
  },
  "confidence": "kõrge",
  "notes": "Õigusvõime definitsioon + universaalsuse printsiip"
}
```
**Enhanced Fields:**
- `enhanced_type` — Semantic classification (declarative, imperative, permissive, etc.)
- `norm_category` — Legal domain category
- `subject.primary` — Main entity the norm concerns
- `subject.semantics` — Interpreted meaning
- `action.type` — Type of legal action required/granted
- `conditions` — Array of applicable conditions with types and descriptions
- `exceptions` — Array of exceptions with trigger conditions
- `confidence` — Analysis confidence level (kõrge/keskmine/madal)
- `notes` — Human-readable interpretation

### Norm Types Reference

| Type | Description | Example |
|------|-------------|---------|
| `declarative` | States facts or principles | "Õigusvõime algab sündimisega" |
| `imperative` | Commands/mandates action | "Kohus peab otsustama..." |
| `permissive` | Grants permission/right | "Isik võib nõuda..." |
| `prohibitive` | Forbids action | "Tehing on keelatud..." |
| `definition` | Defines concepts | "Füüsilise isiku teovõime on..." |
| `conditional_*` | Contains conditions | "Kui X, siis Y" |

---

## 3. How to Use RDF/Turtle Files

### What Is RDF/Turtle?

**RDF** (Resource Description Framework) represents data as **triples**: Subject → Predicate → Object  
**Turtle** is a human-readable syntax for RDF, using prefixes to shorten URIs.

### File Locations

RDF/Turtle files are in `~/.openclaw/shared/krr_templates/` and `~/.openclaw/shared/krr_outputs/`:

| File | Purpose |
|------|---------|
| `civillaw_ontology_skeleton.ttl` | Schema definition (classes and properties) |
| `civillaw_article_instance_template.ttl` | Template for creating new article instances |
| `*_initial_graph.ttl` | Actual data graph with legal norms |

### Understanding the Schema (Ontology Skeleton)

```turtle
@prefix cl: <https://example.org/civillaw#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
```

**Prefixes** (shortcuts for long URIs):
- `cl:` — Civil law ontology namespace
- `rdf:` — Core RDF vocabulary
- `rdfs:` — RDF Schema vocabulary

### Classes (Types of Things)

```turtle
cl:LegalDocument a rdfs:Class .
cl:Article a rdfs:Class .
cl:Norm a rdfs:Class .
cl:Subject a rdfs:Class .
cl:LegalAction a rdfs:Class .
cl:Condition a rdfs:Class .
cl:Exception a rdfs:Class .
```

| Class | Represents |
|-------|------------|
| `cl:LegalDocument` | A law or regulation (e.g., TsÜS) |
| `cl:Section` | A major division/part of a law |
| `cl:Article` | Individual articles (§ 1, § 2, etc.) |
| `cl:Norm` | Specific legal norms within articles |
| `cl:Subject` | Legal subjects (persons, entities) |
| `cl:LegalAction` | Actions required/granted/prohibited |
| `cl:Condition` | Preconditions for norm applicability |
| `cl:Exception` | Exceptions to norms |

### Properties (Relationships)

#### Structural Properties
```turtle
cl:hasSection a rdf:Property ;
  rdfs:domain cl:LegalDocument ;
  rdfs:range cl:Section .

cl:hasArticle a rdf:Property ;
  rdfs:domain cl:Section ;
  rdfs:range cl:Article .

cl:containsNorm a rdf:Property ;
  rdfs:domain cl:Article ;
  rdfs:range cl:Norm .
```

#### Semantic Properties
```turtle
cl:appliesToSubject a rdf:Property ;
  rdfs:domain cl:Norm ;
  rdfs:range cl:Subject .

cl:requiresAction a rdf:Property ;
  rdfs:domain cl:Norm ;
  rdfs:range cl:LegalAction .

cl:hasCondition a rdf:Property ;
  rdfs:domain cl:Norm ;
  rdfs:range cl:Condition .
```

| Property | Domain | Range | Meaning |
|----------|--------|-------|---------|
| `cl:hasSection` | LegalDocument | Section | Document contains section |
| `cl:hasArticle` | Section | Article | Section contains article |
| `cl:containsNorm` | Article | Norm | Article contains norm |
| `cl:appliesToSubject` | Norm | Subject | Norm applies to subject |
| `cl:requiresAction` | Norm | LegalAction | Norm requires action |
| `cl:grantsRight` | Norm | LegalAction | Norm grants right |
| `cl:hasCondition` | Norm | Condition | Norm has precondition |
| `cl:hasException` | Norm | Exception | Norm has exception |

### Reading Instance Data

```turtle
@prefix cl: <https://example.org/civillaw#> .
@prefix ex: <https://example.org/resource/> .

ex:tsiviilseadustik a cl:LegalDocument .

ex:article_1 a cl:Article ;
  cl:articleNumber "1" ;
  cl:sourceText "Seaduse ülesanne" .

ex:norm_1_1 a cl:Norm ;
  cl:sourceText "Käesolevas seaduses sätestatakse tsiviilõiguse üldpõhimõtted." .

ex:article_1 cl:containsNorm ex:norm_1_1 .
```

**Reading this:**
1. `ex:tsiviilseadustik` is a `LegalDocument`
2. `ex:article_1` is an `Article` with number "1" and title "Seaduse ülesanne"
3. `ex:norm_1_1` is a `Norm` with specific legal text
4. `ex:article_1` contains `ex:norm_1_1`

### Creating New Instances

Use the template file as a starting point:

```turtle
@prefix cl: <https://example.org/civillaw#> .
@prefix ex: <https://example.org/resource/> .

# Create new article instance
ex:article_42 a cl:Article ;
  cl:articleNumber "42" ;
  cl:sourceText "New Article Title" .

# Create associated norm
ex:norm_42_1 a cl:Norm ;
  cl:appliesToSubject ex:subject_person ;
  cl:requiresAction ex:action_report ;
  cl:hasCondition ex:condition_deadline ;
  cl:sourceText "Person must report within 30 days." .

ex:article_42 cl:containsNorm ex:norm_42_1 .

# Define referenced entities
ex:subject_person a cl:Subject .
ex:action_report a cl:LegalAction .
ex:condition_deadline a cl:Condition .
```

---

## 4. Example Queries and Use Cases

### Use Case 1: Find All Norms with Conditions

**SPARQL Query:**
```sparql
PREFIX cl: <https://example.org/civillaw#>

SELECT ?norm ?text ?condition
WHERE {
  ?norm a cl:Norm ;
        cl:sourceText ?text ;
        cl:hasCondition ?condition .
}
```

**JSON Analysis (Python):**
```python
import json

with open('tsiviilseadustik_structural_analysis_20p.json') as f:
    data = json.load(f)

norms_with_conditions = []
for para in data['analysis']:
    for norm in para['norms']:
        if norm['condition_present'] == 'jah':
            norms_with_conditions.append({
                'id': norm['id'],
                'text': norm['source'],
                'article': para['paragrahvNr']
            })

print(f"Found {len(norms_with_conditions)} norms with conditions")
```

### Use Case 2: Find All Rights Granted

**SPARQL Query:**
```sparql
PREFIX cl: <https://example.org/civillaw#>

SELECT ?norm ?text
WHERE {
  ?norm a cl:Norm ;
        cl:grantsRight ?action ;
        cl:sourceText ?text .
}
```

**JSON Analysis:**
```python
rights = []
for para in data['analysis']:
    for norm in para['norms']:
        if norm['type'] == 'right':
            rights.append({
                'id': norm['id'],
                'text': norm['source'],
                'article': para['paragrahvNr']
            })
```

### Use Case 3: Find Norms by Category

**Enhanced JSON Analysis:**
```python
def find_norms_by_category(data, category):
    """Find all norms in a specific legal category"""
    results = []
    for norm in data.get('enhanced_norms', []):
        if norm.get('norm_category') == category:
            results.append({
                'id': norm['id'],
                'type': norm['enhanced_type'],
                'subject': norm['subject']['primary'],
                'confidence': norm['confidence']
            })
    return results

# Example: Find all legal capacity norms
capacity_norms = find_norms_by_category(data, 'legal_capacity')
```

### Use Case 4: Check Temporal Conditions

```python
def find_temporal_norms(data):
    """Find norms with temporal aspects (time-based conditions)"""
    temporal_norms = []
    for norm in data.get('enhanced_norms', []):
        for condition in norm.get('conditions', []):
            if condition.get('temporal_aspect'):
                temporal_norms.append({
                    'id': norm['id'],
                    'temporal_aspect': condition['temporal_aspect'],
                    'description': condition['description']
                })
    return temporal_norms
```

### Use Case 5: Validate Legal Completeness

```python
def check_confidence_levels(data):
    """Check which norms need human review"""
    review_needed = []
    for norm in data.get('enhanced_norms', []):
        if norm.get('confidence') in ['keskmine', 'madal'] or norm.get('review_needed'):
            review_needed.append({
                'id': norm['id'],
                'confidence': norm['confidence'],
                'notes': norm.get('notes', '')
            })
    return review_needed
```

### Use Case 6: Extract Subject-Action Relationships

```python
def extract_subject_action_matrix(data):
    """Create matrix of subjects and their legal actions"""
    matrix = {}
    for norm in data.get('enhanced_norms', []):
        subject = norm.get('subject', {}).get('primary', 'unknown')
        action = norm.get('action', {}).get('type', 'none')
        
        if subject not in matrix:
            matrix[subject] = []
        matrix[subject].append({
            'action': action,
            'norm_id': norm['id'],
            'type': norm['enhanced_type']
        })
    return matrix
```

---

## 5. File Locations and Structure

### Directory Layout

```
~/.openclaw/shared/
├── krr_templates/
│   ├── civillaw_ontology_skeleton.ttl    # Schema definitions
│   └── civillaw_article_instance_template.ttl  # Instance template
├── krr_outputs/
│   ├── tsiviilseadustik_source_snapshot.json       # Original text
│   ├── tsiviilseadustik_structural_analysis_20p.json  # Structure analysis
│   ├── tsiviilseadustik_enhanced_1-7.json          # Semantic enhancement
│   ├── tsiviilseadustik_initial_graph.ttl          # RDF graph
│   └── tsiviilseadustik_initial_analysis.md        # Human summary
├── estonian-legislation-ontology-project.md        # Project overview
├── ONTOLOGY_STACK_DRAFT.md                         # Technical specifications
└── USAGE_GUIDE.md                                  # This file
```

### File Naming Conventions

| Pattern | Meaning |
|---------|---------|
| `*_source_snapshot.json` | Raw data from Riigi Teataja API |
| `*_structural_analysis_*.json` | Structural decomposition |
| `*_enhanced_*.json` | Semantic enrichment |
| `*_initial_graph.ttl` | RDF triples |
| `*_review_*.md` | Human review notes |

### Data Flow

1. **Source** → `*_source_snapshot.json` (raw XML converted to JSON)
2. **Structure** → `*_structural_analysis_*.json` (paragraphs → norms)
3. **Enhance** → `*_enhanced_*.json` (semantic analysis)
4. **Graph** → `*_initial_graph.ttl` (RDF conversion)

---

## 6. Glossary

| Term | Definition |
|------|------------|
| **TsÜS** | Tsiviilseadustiku üldosa seadus (Estonian Civil Code General Part) |
| **Norm** | A single rule or provision within a legal text |
| **Article** | A numbered section (§) of a law containing one or more norms |
| **Provision** | Synonym for norm or legal rule |
| **JSON-LD** | JSON for Linked Data — semantic JSON format |
| **RDF** | Resource Description Framework — graph data model |
| **Turtle** | Terse RDF Triple Language — human-readable RDF syntax |
| **Ontology** | Formal specification of concepts and relationships in a domain |
| **Triple** | Subject-Predicate-Object statement (e.g., "Article-1 contains Norm-1-1") |
| **URI** | Uniform Resource Identifier — unique identifier for resources |
| **Namespace** | Prefix for URIs to avoid naming conflicts |
| **Semantic** | Related to meaning and interpretation |
| **Declarative** | Norm type that states facts |
| **Imperative** | Norm type that commands action |
| **Condition** | Prerequisite that must be met for norm to apply |
| **Exception** | Circumstance where norm does not apply |
| **Riigi Teataja** | Official Estonian legal gazette |
| **KRR** | Knowledge Representation and Reasoning |
| **SPARQL** | Query language for RDF databases |

---

## Quick Reference Card

### Convert JSON-LD to Python Dict
```python
import json
with open('file.json') as f:
    data = json.load(f)
```

### Load RDF/Turtle in Python
```python
from rdflib import Graph
g = Graph()
g.parse('file.ttl', format='turtle')
```

### Basic SPARQL Query Template
```sparql
PREFIX cl: <https://example.org/civillaw#>
SELECT ?s ?p ?o
WHERE { ?s ?p ?o }
LIMIT 10
```

### Filter Norms by Type (Python)
```python
norms = [n for n in data['enhanced_norms'] 
         if n['enhanced_type'] == 'imperative']
```

---

**Questions?** Contact the ontology team or check the project documentation in `~/.openclaw/shared/`.
