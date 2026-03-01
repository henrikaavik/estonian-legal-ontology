# Schema Reference

## Complete Schema Documentation

### Classes
1. **LegalProvision (`estleg:LegalProvision`)**
   - Represents a specific article, section, or paragraph in a law.
2. **TopicCluster (`estleg:TopicCluster`)**
   - Represents a semantic cluster or theme that legal provisions belong to.
3. **LegalConcept (`estleg:LegalConcept`)**
   - Represents a defined legal concept or term used within the legislation.

### Properties
* `estleg:identifier`: A unique identifier for the provision.
* `schema:name`: The title or heading of the provision.
* `schema:text`: The actual text of the law.
* `estleg:topicCluster`: Associates a provision with a TopicCluster.
* `estleg:references`: Defines cross-references to other legal provisions or laws.
* `schema:isPartOf`: Indicates the hierarchical structure (e.g., paragraph is part of a section).
* `skos:prefLabel`: The preferred label for a LegalConcept or TopicCluster.

## JSON-LD Structure Examples

```json
{
  "@context": {
    "schema": "http://schema.org/",
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "skos": "http://www.w3.org/2004/02/skos/core#"
  },
  "@graph": [
    {
      "@id": "https://data.riik.ee/legislation/TsUS/art1",
      "@type": "estleg:LegalProvision",
      "estleg:identifier": "TsÜS § 1",
      "schema:name": "Seaduse eesmärk",
      "schema:text": "Käesolev seadus sätestab tsiviilõiguse üldpõhimõtted.",
      "estleg:topicCluster": {
        "@id": "https://data.riik.ee/taxonomy/topic/Uldosa"
      }
    }
  ]
}
```
