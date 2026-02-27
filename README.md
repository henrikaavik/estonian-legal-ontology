# Estonian Legal Ontology (KRR)

## Project description
This repository contains outputs for building an **Estonian legal ontology** from Estonian legislation (starting with *Tsiviilseadustiku üldosa seadus*).  
Goal: transform legal text into machine-readable, semantically structured knowledge (JSON/TTL + review notes) for KRR workflows.

## Team members
- Henrik Aavik (project owner)
- Peep (technical lead / implementation)
- Marta (research & content)
- Kiira (coordination & operations)
- Tambet (review & communications)

## Current status
- ✅ Part 1 done: Source extraction and initial snapshot
- ✅ Part 2 done: Initial structural/legal analysis
- ✅ Part 3 done: Review-enhanced output package
- 🔄 Part 4 in progress: GitHub packaging, publication, and final cleanup

## How to use the ontology
1. Start from `krr_outputs/tsiviilseadustik_source_snapshot.json` to identify source metadata.
2. Use `krr_outputs/tsiviilseadustik_structural_analysis_20p.json` for paragraph-level structural analysis.
3. Use `krr_outputs/tsiviilseadustik_enhanced_1-7.json` for reviewed/enhanced semantic fields.
4. Use `krr_outputs/tsiviilseadustik_initial_graph.ttl` as RDF/Turtle seed graph.
5. Read the markdown notes (`*_analysis.md`, `*_review_*.md`) for assumptions, caveats, and review context.

## Notes
- Language of legal content and annotations is primarily Estonian.
- Current package is prepared as a base for versioned GitHub publication and further ontology expansion.
