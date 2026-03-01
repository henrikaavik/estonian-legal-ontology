# Estonian Legal Ontology Project

## Project Overview and Purpose
This project provides a comprehensive, machine-readable ontology of Estonian legislation using JSON-LD. It aims to create a semantic graph of legal provisions, topic clusters, and legal concepts, enabling advanced search, cross-referencing, and automated legal analysis.

## Mapped Laws
*Note: This is a placeholder list. 26 laws have been mapped.*
1. Tsiviilseadustiku üldosa seadus (TsÜS)
2. Võlaõigusseadus (VÕS)
3. Asjaõigusseadus (AÕS)
4. Karistusseadustik (KarS)
5. Kriminaalmenetluse seadustik (KrMS)
6. ... and 21 others.

## Schema Explanation
The ontology uses a custom `estleg` namespace and is structured using JSON-LD:
- `@context`: Defines the vocabulary and namespaces used (e.g., `schema`, `skos`, `estleg`).
- `@graph`: Contains the actual data nodes (Legal Provisions, Topic Clusters, Legal Concepts).
- `estleg`: The custom namespace for Estonian legal specific terms.

## How to Use the JSON-LD Files
1. Download the JSON-LD files from the `data/` directory (or wherever they are stored).
2. Load them into a graph database (e.g., GraphDB, Neo4j with RDF plugin) or parse them using an RDF/JSON-LD library in your preferred language (Python, Java, JS).

## Example Queries/Usage
You can query the data to find all provisions related to a specific topic cluster, or trace cross-references between different laws.
(See API_GUIDE.md for SPARQL examples).

## GitHub Repo Structure
- `docs/`: Documentation (this folder)
- `scripts/`: Python scripts for validation and processing
- `data/`: The generated JSON-LD ontology files (if applicable)

## Contribution Guidelines
Please submit pull requests with improvements to the mapping or schema. Ensure all JSON-LD files pass validation before submitting.
