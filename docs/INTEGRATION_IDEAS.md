# Ontology Integration & Improvement Ideas

Ideas for making the Estonian Legal Ontology more interconnected, discoverable, and useful for automated legal analysis.

---

## 1. Cross-Law Reference Extraction

**Status: IMPLEMENTED** (script: `extract_cross_references.py`)

**Problem:** Laws constantly reference each other in their text (e.g., "vastavalt TsÜS §-le 67", "käesoleva seaduse § 12 lõikes 3 sätestatud"), but these references exist only as plain text — not as semantic links.

**Solution:** Build a reference parser that:
- Scans law text for citation patterns (regex + NLP)
- Resolves citations to existing `@id` IRIs in the ontology
- Populates `estleg:references` with IRI-valued links between provisions
- Handles both internal references (within the same law) and external references (to other laws)

**Common Estonian citation patterns to parse:**
- `TsÜS § 67` — direct paragraph reference
- `VÕS § 208 lg 1` — paragraph + subsection
- `käesoleva seaduse § 12 lõikes 3` — self-referencing within same law
- `Euroopa Parlamendi ja nõukogu määrus (EL) nr 2016/679` — EU regulation reference

**Impact:** This alone would add tens of thousands of semantic links across the graph.

---

## 2. Bidirectional Reference Links

**Status: IMPLEMENTED** (script: `generate_inverse_references.py`)

**Problem:** If Law A references Law B, Law B has no awareness of this. You can query "what does this law reference?" but not "what references this law?"

**Solution:** Add an inverse property `estleg:referencedBy` (or use OWL `owl:inverseOf`):
- When `estleg:references` is populated, auto-generate the inverse
- Enables queries like "show me all laws that cite KarS § 121"
- Critical for impact analysis — understanding which parts of the legal system depend on a given provision

---

## 3. EU Directive → Estonian Transposition Mapping

**Status: IMPLEMENTED** (script: `generate_transposition_mapping.py`)

**Problem:** Estonia transposes EU directives into national law, but the ontology doesn't track which Estonian laws implement which EU directives.

**Solution:** Add `estleg:transposesDirective` property:
- Map Estonian laws to the EU directives they implement
- Source data: EUR-Lex national transposition measures database (available via SPARQL)
- Enables queries like "which Estonian laws implement GDPR?" or "is Directive 2019/1937 fully transposed?"

**Secondary benefit:** Identify transposition gaps — EU directives without corresponding Estonian legislation.

---

## 4. Court Decision → Specific Provision Links

**Status: IMPLEMENTED** (script: `extract_court_provision_links.py`)

**Problem:** Court decisions currently reference laws by name only (e.g., `referencedLaw: "KarS"` as a string). This is too coarse — a decision interpreting KarS § 121 lg 2 p 2 should link to that exact provision.

**Solution:**
- Parse court decision text to extract specific paragraph-level citations
- Resolve to existing provision IRIs
- Upgrade `estleg:referencedLaw` from `xsd:string` to IRI references
- Populate `estleg:interpretsLaw` with granular links

**Impact:** Transforms court decisions from isolated documents into a rich interpretive layer over the statute text. A lawyer looking at VÕS § 208 could instantly see all Supreme Court decisions interpreting that provision.

---

## 5. Amendment Chain / Legislative History Graph

**Status: IMPLEMENTED** (script: `generate_amendment_history.py`)

**Problem:** Laws are amended frequently, but the ontology captures only the current state. There's no way to trace how a provision evolved over time.

**Solution:** Model amendment history:
- `estleg:amendedBy` — links a provision to the amending act
- `estleg:previousVersion` — links to the prior version of the provision
- `estleg:effectiveDate` / `estleg:expiryDate` — temporal validity
- `estleg:consolidatedVersion` — link to Riigi Teataja redaction history

**Data source:** Riigi Teataja provides version history and amendment metadata through their API.

**Enables:**
- "How did this provision change over the last 5 years?"
- "Which provisions were affected by the 2024 VÕS reform?"
- Timeline visualisation of legislative evolution

---

## 6. Semantic Similarity Clustering

**Status: IMPLEMENTED** (script: `generate_similarity_index.py`)

**Problem:** Laws addressing related topics (e.g., consumer protection scattered across VÕS, TKS, RekS, TPTS) aren't linked unless they explicitly reference each other.

**Solution:** Use embeddings (e.g., OpenAI, multilingual-e5) to:
- Compute vector embeddings for each provision's summary/text
- Cluster semantically similar provisions across different laws
- Add `estleg:semanticallySimilarTo` links above a confidence threshold
- Enhance existing `estleg:topicCluster` with data-driven clusters rather than manually assigned ones

**Use cases:**
- "Find all provisions across Estonian law dealing with data protection"
- "What other laws have provisions similar to VÕS § 14 (good faith principle)?"
- Detect regulatory overlap or inconsistency

---

## 7. Legal Concept Cross-Reference Graph

**Status: IMPLEMENTED** (script: `extract_legal_concepts.py`)

**Problem:** Many laws define the same or overlapping terms (e.g., "tarbija" / consumer is defined in VÕS, TKS, TPTS, etc.). These definitions aren't linked.

**Solution:** Build a legal concept graph:
- Extract defined terms from each law (§ 2, § 3 style definition sections)
- Link identical/overlapping concepts across laws via `owl:sameAs` or `skos:closeMatch`
- Add `estleg:definesTerm` linking provisions to the concepts they define
- Use SKOS vocabulary for broader/narrower term relationships

**Enables:**
- "How does the definition of 'tarbija' differ between VÕS and TKS?"
- Concept disambiguation across the legal system
- Glossary generation

---

## 8. Draft Legislation Impact Analysis

**Status: IMPLEMENTED** (script: `extract_draft_impact.py`)

**Problem:** The ontology has 22,832 drafts with `affectedLawName` as a string, but no structured link showing exactly which provisions a draft would modify.

**Solution:**
- Parse draft explanatory memoranda (seletuskiri) for specific section references
- Resolve `estleg:amendsLaw` to specific provision IRIs (not just law-level)
- Add `estleg:proposedChange` with structured diff information
- Link drafts to the public consultation responses they received

**Enables:**
- "Show me all pending drafts that would change KarS provisions on cybercrime"
- Legislative radar — track how incoming legislation affects your area of practice

---

## 9. Subject Matter Taxonomy (EuroVoc / Estonian Classification)

**Status: IMPLEMENTED** (script: `classify_eurovoc.py`)

**Problem:** `estleg:topicCluster` exists but uses ad-hoc topic labels without a controlled vocabulary.

**Solution:** Align with established legal taxonomies:
- **EuroVoc** — the EU's multilingual thesaurus (already used in EUR-Lex data)
- **Estonian legal domain classification** — Riigi Teataja's own subject categories
- Map each provision to one or more EuroVoc concepts via `dcterms:subject`

**Benefits:**
- Interoperable with EU legal data systems
- Standardised subject search across enacted laws, drafts, and court decisions
- Multilingual discoverability (EuroVoc has Estonian labels)

---

## 10. Temporal Validity & Applicability Windows

**Status: IMPLEMENTED** (script: `extract_temporal_data.py`)

**Problem:** The ontology represents law as static, but provisions have effective dates, expiry dates, and transitional periods.

**Solution:** Add temporal metadata:
- `estleg:entryIntoForce` — when the provision became effective
- `estleg:repealDate` — when it was repealed
- `estleg:transitionalPeriod` — grace period details
- `estleg:applicableFrom` / `estleg:applicableTo` — for provisions with sunset clauses

**Data source:** Riigi Teataja publishes jõustumise kuupäev (entry into force dates) in their XML.

**Enables:**
- "What was the law on X as of 2023-06-15?" (point-in-time queries)
- Identify provisions that will expire soon
- Historical legal research

---

## 11. Cross-Border Legal Harmonisation Links

**Status: IMPLEMENTED** (script: `generate_harmonisation_links.py`)

**Problem:** Estonian law implements EU law, but there's no link to how other EU member states implemented the same directive.

**Solution:**
- Link Estonian transposition measures to the EU directive they implement
- Via EUR-Lex, connect to other member states' national measures
- Add `estleg:harmonisedWith` for provisions that exist due to EU harmonisation

**Enables:**
- Comparative law research: "How did Latvia and Finland transpose the same directive?"
- Identify where Estonian implementation differs from EU peers

---

## 12. Practitioner Annotation Layer

**Status: NOT IMPLEMENTED** -- schema defined, no extraction script yet

**Problem:** The ontology captures the law as written, but not how it's applied in practice.

**Solution:** Add an annotation layer:
- `estleg:practiceNote` — notes from legal practitioners about how a provision is applied
- `estleg:commonDispute` — frequent litigation topics around a provision
- `estleg:regulatoryGuidance` — links to relevant ministry guidelines or Riigikohtu practice directions
- Link to Õiguskantsleri (Chancellor of Justice) opinions interpreting provisions

---

## 13. Penalty & Sanction Cross-Reference

**Status: IMPLEMENTED** (script: `extract_sanctions.py`)

**Problem:** Sanctions are scattered across KarS, VTMS, and individual administrative laws without a unified view.

**Solution:**
- Extract penalty provisions (trahv, vangistus, etc.) from all laws
- Create `estleg:Sanction` class with severity, type, and applicable provision
- Link sanctions to the conduct they punish
- Cross-reference KarS penalties with VTMS misdemeanour penalties for the same conduct

**Enables:**
- "What's the penalty for X?" across the entire legal system
- Compare criminal vs. administrative penalties for similar conduct

---

## 14. Institutional Competence Mapping

**Status: IMPLEMENTED** (script: `extract_institutional_competence.py`)

**Problem:** Laws assign powers and responsibilities to institutions (ministries, agencies, courts) but these assignments aren't captured.

**Solution:**
- Extract institutional references from law text
- Map `estleg:competentAuthority` for each provision
- Build an authority graph: which institution is responsible for what
- Link to the Register of State and Local Government Organisations

**Enables:**
- "Which authority supervises compliance with VeeS?"
- Identify jurisdictional overlaps between agencies
- Organisational restructuring impact analysis

---

## 15. Obligation / Right / Permission Classification

**Status: IMPLEMENTED** (script: `classify_deontic.py`)

**Problem:** Legal provisions create different normative effects (obligations, rights, permissions, prohibitions) but these aren't classified.

**Solution:** Add deontic classification:
- `estleg:normativeType` — `obligation`, `right`, `permission`, `prohibition`
- `estleg:dutyHolder` — who must comply
- `estleg:beneficiary` — who benefits from a right
- Tag each provision with its normative character

**Enables:**
- "What obligations does this law impose on data controllers?"
- Compliance checklists: extract all obligations from a set of applicable laws
- Regulatory burden analysis

---

## Priority Ranking

| # | Idea | Effort | Impact | Priority |
|---|------|--------|--------|----------|
| 1 | Cross-law reference extraction | Medium | Very High | P0 |
| 4 | Court decision → provision links | Medium | Very High | P0 |
| 2 | Bidirectional reference links | Low | High | P1 |
| 3 | EU directive transposition mapping | Medium | High | P1 |
| 9 | EuroVoc subject taxonomy | Medium | High | P1 |
| 5 | Amendment chain / legislative history | High | High | P2 |
| 7 | Legal concept cross-reference graph | Medium | Medium | P2 |
| 10 | Temporal validity | Medium | Medium | P2 |
| 6 | Semantic similarity clustering | Medium | Medium | P2 |
| 8 | Draft legislation impact analysis | Medium | Medium | P2 |
| 15 | Obligation/right/permission classification | High | High | P3 |
| 14 | Institutional competence mapping | Medium | Medium | P3 |
| 13 | Penalty & sanction cross-reference | Medium | Medium | P3 |
| 12 | Practitioner annotation layer | High | Medium | P3 |
| 11 | Cross-border harmonisation links | High | Low | P3 |
