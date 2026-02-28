# Estonian Legal Ontology — Comprehensive Analysis

**Analyst:** Claude (automated review)
**Date:** 2026-02-28
**Scope:** Full repository analysis — schema, instance data, documentation, and architecture

---

## 1. Executive Summary

This ontology is a serious, well-structured effort to formalize the Estonian Civil Code General Part (TsÜS) into machine-readable semantic data. It covers 32 of 170 articles with varying depth across 7 output files, using a hybrid JSON-LD / RDF-Turtle approach. The schema design is sound, the legal accuracy has been validated, and the documentation is thorough. However, there are meaningful consistency issues across files, coverage gaps in the middle sections of TsÜS, and several technical decisions that should be resolved before the ontology scales to additional laws.

**Overall Assessment:** Strong foundation with clear path to production. Needs namespace unification, schema consolidation, and expanded coverage before v1.0.

---

## 2. Scope and Coverage

### 2.1 What Is Modeled

| TsÜS Part | Articles | Coverage | File(s) |
|------------|----------|----------|---------|
| Osa 1 — Üldsätted | §1–6 | **Full** (§1–5 with source text, §6 via enhanced norms) | `initial_graph.ttl`, `source_snapshot.json`, `enhanced_1-7.json` |
| Osa 2 — Isikud | §7–47 | **Partial** (§7 referenced in enhanced norms, §8–47 unmapped) | `enhanced_1-7.json` |
| Osa 3 — Esemed | §48–66 | **None** | — |
| Osa 4 — Tehingud | §67–131 | **None** in repo files (Marta's draft referenced in review but not committed) | — |
| Osa 5 — Vastutus | §132–133 | **None** | — |
| Osa 6 — Tähtaeg ja tähtpäev | §134–137 | **Full** (concept-level) | `osa6_peep.json` |
| Osa 7 — Tsiviilõiguste teostamine | §138–169 | **Full** (detailed OWL with provisions) | `tsus_osa7_138_169_owl.jsonld` |
| Osa 8 — Rakendamine | §170 | **None** | — |

**Note:** The file labeled `osa5_peep.json` actually covers Osa 5 *and* the limitation/prescription concepts from §142–169 (Osa 7), not Osa 5 (§132–133 Vastutus). This is a labeling inconsistency — the file's own `scope` field reads "Osa 5: tähtajad, tähtpäevad, aegumine (fookus §163-169)" but TsÜS Osa 5 is actually "Vastutus teise isiku eest" (§132–133). The content matches Osa 6 + Osa 7, not Osa 5.

### 2.2 Coverage Gaps

- **Osa 2 (§8–47):** Persons — 40 articles unmapped. This is the second-largest part of TsÜS and contains critical concepts (legal capacity, residence, legal persons, reorganization).
- **Osa 3 (§48–66):** Things — 19 articles unmapped. Property classification concepts important for downstream AÕS integration.
- **Osa 4 (§67–131):** Transactions — 65 articles unmapped in committed files. The legal review references Marta's draft covering this, but it is not in the repository.
- **Osa 5 (§132–133):** Liability for others — 2 articles unmapped.
- **Osa 8 (§170):** Application — 1 article unmapped.

**Coverage rate:** ~46 of 170 articles have some representation (27%), but only ~37 have substantive semantic analysis.

---

## 3. Technical Architecture Assessment

### 3.1 Schema Design

The ontology uses two parallel schema layers:

**Layer 1 — Turtle skeleton** (`civillaw_ontology_skeleton.ttl`)
- 10 classes: `LegalDocument`, `Section`, `Article`, `Norm`, `Subject`, `LegalAction`, `Condition`, `Exception`, `Sanction`, `Reference`
- 14 properties covering structural hierarchy, norm semantics, and metadata
- Clean OWL declarations with proper `rdfs:domain` and `rdfs:range`

**Layer 2 — Osa 7 OWL model** (`tsus_osa7_138_169_owl.jsonld`)
- 6 classes: `LegalPart`, `Chapter`, `Division`, `Section`, `Provision`, `LegalConcept`
- 6 object properties: `hasChapter`, `hasDivision`, `hasSection`, `hasProvision`, `coversConcept`, `belongsToChapter`/`inDivision`/`inChapter`

**Issue:** These two schemas model the same domain differently. The skeleton uses `LegalDocument → Section → Article → Norm` while Osa 7 uses `LegalPart → Chapter → Division → Section → Provision`. Neither imports or references the other. This must be reconciled before the ontology scales.

### 3.2 Namespace Fragmentation

The ontology uses multiple namespace prefixes for the same domain:

| File | Prefix | Namespace URI |
|------|--------|---------------|
| `civillaw_ontology_skeleton.ttl` | `cl:` | `https://example.org/civillaw#` |
| `civillaw_article_instance_template.ttl` | `cl:` + `ex:` | `https://example.org/civillaw#` + `https://example.org/resource/` |
| `tsiviilseadustik_initial_graph.ttl` | `legal:` | `http://example.org/estonian-legal#` |
| JSON-LD files | `estleg:` / `legal:` | `http://example.org/estonian-legal#` |
| Osa 7 JSON-LD | `legal:` | `http://example.org/estonian-legal#` |

This creates three problems:
1. The skeleton (`cl:`) and the instance data (`legal:`) are in different namespaces — they cannot be merged into a single graph without mapping.
2. `https://` vs `http://` protocol inconsistency between skeleton and instance data.
3. All are placeholder domains (`example.org`), which is appropriate for development but must be replaced.

### 3.3 Format Consistency

| File | Format | Semantic Fidelity |
|------|--------|-------------------|
| `initial_graph.ttl` | Turtle/RDF | High — proper OWL classes, instances, and properties |
| `source_snapshot.json` | JSON-LD (wrapped) | Medium — uses `original_data` wrapper pattern, instances typed as `owl:Class` instead of named individuals |
| `structural_analysis_20p.json` | JSON-LD (wrapped) | Low — minimal actual data, mostly skeleton |
| `enhanced_1-7.json` | JSON-LD (wrapped) | Medium — norm types captured but nested in `original_data` |
| `osa5_peep.json` | JSON-LD (wrapped) | Medium — rich concept descriptions but non-standard structure |
| `osa6_peep.json` | JSON-LD (wrapped) | Medium — same pattern as osa5 |
| `osa7_owl.jsonld` | JSON-LD (native OWL) | **High** — proper OWL individuals, properties, and hierarchy |

**Key issue:** The JSON-LD files (except Osa 7) use a pattern where instances are typed as `owl:Class` with their real data buried in an `original_data` property. This is semantically incorrect — `legal:article_1` is an *instance* (individual), not a class. The Osa 7 file correctly uses `owl:NamedIndividual`. This inconsistency means the earlier files cannot be directly loaded into an OWL reasoner.

### 3.4 Data Modeling Patterns

**Norm decomposition** (enhanced files) follows a consistent and linguistically sound pattern:
- `normId` — article_subsection identifier
- `enhancedType` — norm classification (declarative, definition, imperative_methodological, etc.)
- `subject_guess` / `subject.primary` — who the norm applies to
- `action` — what the norm requires/grants/prohibits
- `condition` / `exception` — conditional elements

This decomposition is well-suited for legal reasoning and aligns with standard deontic logic categories.

---

## 4. Strengths

### 4.1 Legal Accuracy
The legal content has been validated by a domain reviewer (Tambet) and confirmed accurate. The source texts are preserved verbatim from Riigi Teataja, norm classifications are sound, and the structural analysis correctly reflects TsÜS hierarchy.

### 4.2 Norm Type Taxonomy
The enhanced norm classification system is well-designed:
- `declarative` — statement of law
- `definition` — concept definition
- `imperative_methodological` — prescribes method
- `conditional_permissive` — conditional permission
- `declarative_exhaustive` — exhaustive enumeration
- `declarative_temporal` — time-bound declaration
- `conditional_declarative` — conditional statement
- `imperative_procedural` — prescribes procedure
- `declarative_hierarchy` — hierarchy statement

This taxonomy captures meaningful distinctions for legal reasoning.

### 4.3 Osa 7 Depth
The `tsus_osa7_138_169_owl.jsonld` file (67KB) is the most mature artifact. It models:
- Full structural hierarchy (Part → Chapter → Division → Section → Provision)
- Complete provision text for all 32 sections (§138–169)
- Legal concept individuals (Õiguste Kaitsmine, Õiguste Ennetamine, Aegumine)
- Proper OWL typing with NamedIndividuals
- Bilingual labels (Estonian primary)

### 4.4 Documentation Quality
The project has thorough documentation:
- Structural analysis of all 8 parts of TsÜS
- Usage guide with SPARQL and Python examples
- Legal accuracy review with article-level validation
- Technology stack draft with clear architectural rationale
- Team collaboration protocols

### 4.5 Architectural Pragmatism
The hybrid JSON-LD + PostgreSQL approach is a sound choice for an early-stage legal ontology. It avoids the overhead of a full triplestore while keeping the RDF export path open.

---

## 5. Issues and Risks

### 5.1 Critical Issues

| # | Issue | Impact | Files Affected |
|---|-------|--------|----------------|
| C1 | **Namespace fragmentation** — `cl:` vs `legal:` vs `estleg:` with different URI schemes | Cannot merge files into a unified graph | All |
| C2 | **Schema divergence** — two incompatible class hierarchies | Ambiguity about which schema is canonical | `skeleton.ttl` vs `osa7.jsonld` |
| C3 | **Incorrect OWL typing** — instances declared as `owl:Class` instead of `owl:NamedIndividual` | OWL reasoners will misinterpret the data | `source_snapshot.json`, `structural_analysis_20p.json`, `enhanced_1-7.json`, `osa5_peep.json`, `osa6_peep.json` |

### 5.2 Moderate Issues

| # | Issue | Impact |
|---|-------|--------|
| M1 | **File mislabeling** — `osa5_peep.json` actually covers Osa 6/7 content, not Osa 5 (§132–133) | Confusion for consumers |
| M2 | **Missing committed files** — Marta's Osa 4 draft (65 articles) referenced in review but absent from repo | Coverage gap |
| M3 | **No validation tooling** — No SHACL shapes, JSON Schema files, or automated validation | Quality assurance risk as ontology grows |
| M4 | **Sparse structural analysis** — `structural_analysis_20p.json` claims to cover §1–20 but only contains §1 and §20 entries | Incomplete data |

### 5.3 Minor Issues

| # | Issue | Impact |
|---|-------|--------|
| L1 | **Placeholder URIs** — all `example.org` | Must change before production |
| L2 | **No inverse properties** — `hasChapter` exists but `isChapterOf` is missing | Query inconvenience |
| L3 | **Mixed language metadata** — some `rdfs:label` in English, others in Estonian, no consistent `@en`/`@et` tagging | Multilingual access |
| L4 | **No versioning metadata** — ontology files lack `owl:versionInfo` or `dc:modified` | Change tracking |

---

## 6. Comparison with Legal Ontology Standards

### 6.1 ELI (European Legislation Identifier)
The EU's ELI standard provides a URI scheme and vocabulary for legislation. This ontology does not reference ELI but has compatible concepts:
- `LegalAct` ≈ `eli:LegalResource`
- `Provision` ≈ `eli:LegalExpression`
- `effectiveDate` ≈ `eli:date_applicability`

**Recommendation:** Align with ELI vocabulary where possible to enable cross-border interoperability.

### 6.2 LKIF (Legal Knowledge Interchange Format)
LKIF provides a more detailed norm-level ontology. This ontology's norm decomposition (subject/action/condition/exception) partially overlaps with LKIF's deontic components but uses a simpler, more practical model.

### 6.3 Akoma Ntoso
The OASIS standard for legislative XML. The structural hierarchy (Part → Chapter → Division → Section) aligns well with Akoma Ntoso's document model.

---

## 7. Metrics Summary

| Metric | Value |
|--------|-------|
| Total files | 7 ontology files (2 TTL, 5 JSON-LD) |
| Total size | ~87 KB of ontology data |
| Classes defined | 16 unique (across all schemas) |
| Properties defined | 20 unique (across all schemas) |
| Named individuals | ~80+ (articles, norms, provisions, concepts) |
| Articles with source text | 37 (§1–5 full text, §138–169 full text) |
| Articles with semantic analysis | 12 (§1–7 enhanced norms, §134–137 concepts, §163–169 concepts) |
| Norm types defined | 9 distinct types |
| TsÜS coverage | 27% of articles, ~40% of structural parts |
| Legal accuracy | Validated (Tambet review, 2026-02-27) |

---

## 8. Recommendations

### 8.1 Immediate (before expanding to VÕS)

1. **Unify the namespace** — Choose one canonical namespace (e.g., `http://ontology.riigiteataja.ee/legal#`) and migrate all files.
2. **Consolidate the schema** — Merge the Turtle skeleton and Osa 7 class hierarchy into a single canonical OWL ontology file. The Osa 7 hierarchy (`LegalPart → Chapter → Division → Section → Provision`) is more granular and should be the base, with `Norm`, `Subject`, `LegalAction`, `Condition`, `Exception`, `Sanction` from the skeleton integrated as norm-level classes.
3. **Fix OWL typing** — Change all instance declarations from `owl:Class` to `owl:NamedIndividual` in the JSON-LD files.
4. **Rename `osa5_peep.json`** — Rename to `tsiviilseadustik_osa5-7_aegumine_peep.json` or split into separate files matching the actual TsÜS structure.
5. **Commit Marta's Osa 4 draft** — The legal review references it as validated; it should be in the repository.

### 8.2 Short-term (v0.2)

6. **Add SHACL shapes** — Define validation constraints for each class (required properties, cardinality, value ranges).
7. **Create a unified instance file** — Consolidate all article/norm instances into a single Turtle or JSON-LD file that can be loaded as one graph.
8. **Add cross-references** — Formalize internal references between articles (e.g., §4 references §2, §6 references §5).
9. **Add `owl:versionInfo`** — Track ontology version in the OWL metadata.
10. **Standardize language tags** — Use `@et` for Estonian, `@en` for English consistently on all `rdfs:label` and `rdfs:comment`.

### 8.3 Before Production

11. **Replace `example.org`** — Assign a permanent URI base.
12. **Align with ELI** — Map core classes to European Legislation Identifier vocabulary.
13. **Build automated validation** — CI pipeline that validates all files against SHACL shapes and JSON Schema.
14. **Publish as Linked Data** — Serve the ontology at its canonical URI with content negotiation (HTML for humans, Turtle/JSON-LD for machines).

---

## 9. Conclusion

The Estonian Legal Ontology is a well-conceived project with solid legal grounding and a pragmatic technical approach. The Osa 7 file demonstrates the project's full potential — detailed, properly typed OWL individuals with complete provision text and structural hierarchy. Bringing the earlier files up to that standard of quality, consolidating the schema, and unifying the namespace will position this ontology for successful expansion to VÕS and beyond.

The most pressing work is not adding more articles, but rather **consolidating what exists** into a single, consistent, well-typed ontology graph. Once the foundation is unified, expanding coverage becomes a matter of following the established Osa 7 pattern.
