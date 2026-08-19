# Schema Reference

## Complete Schema Documentation

> **Note on terminology.** "Regulation" is overloaded across legal systems. In this ontology **domestic regulations** (Estonian *määrused*, modelled under the common superclass `estleg:DomesticRegulation` with the state-level `estleg:NationalRegulation` and municipal `estleg:MunicipalRegulation` branches) are kept strictly separate from **EU regulations** (modelled as `estleg:EULegislation` with `estleg:euDocumentType estleg:EUDocType_Regulation`). Domestic regulations are issued under an enabling Estonian law by Vabariigi Valitsus, a minister, or a municipal council; EU regulations are EU-level legal acts. Use the dedicated classes for each — see "Domestic Regulation Classes" and "EU Legislation Classes" below.

> **Canonical vocabulary (T-Box).** `krr_outputs/controlled_vocabulary.jsonld` is the canonical default-graph T-Box (issue #433): classes, class hierarchy, and properties, with an `owl:Ontology` header at `https://w3id.org/estleg/vocabulary`. `metadata.jsonld` is DCAT catalog only (`dcterms:conformsTo` that IRI). Subcorpus `*_schema.json` files (eelnoud, riigikohus, eurlex, curia) are regenerable projections of the CV (`python3 scripts/generate_schemas_from_cv.py --check`). New reusable terms MUST be defined in the CV. Unresolved citation placeholders live in `krr_outputs/unresolved_references.jsonld`, not in the T-Box.

### Language-tag policy

Corpus default language is Estonian (issue #437).

- **T-Box.** Every `rdfs:label` in `krr_outputs/controlled_vocabulary.jsonld` is bilingual `@et` + `@en`. Remint with `python3 scripts/tag_vocabulary_labels.py`.
- **New ABox.** Law and regulation generators emit `rdfs:label` / `estleg:summary` as `{"@value": "…", "@language": "et"}` via `estleg_common.et_literal`.
- **Existing peeps.** Committed instance literals may stay plain `xsd:string` until a dedicated remint. `FILTER(lang(?x) = "et")` will not match those.
- **No default `@language` on `CONTEXT`.** A context-wide `"@language": "et"` would turn SHACL `xsd:string` fields into `rdf:langString`. Human-language properties already accept either (`sh:or` xsd:string / rdf:langString, issue #509 / #305).

Subcorpus `*_schema.json` files also tag `@et`/`@en` on their schema labels.

### Provenance (dataset-level)

Issue #456 is a **dataset-level** PROV-O layer plus per-node classifier confidence. `krr_outputs/void.ttl` links the published dataset to `estleg:Activity_CorpusBuild_0_11_0` via `prov:wasGeneratedBy`. `estleg_common.combined_ontology_header()` emits the same edge on combined builds. Keyword-derived assertions (deontic `normativeType`, `targetGroup`, EuroVoc `dcterms:subject`) carry `estleg:assertionConfidence` (`xsd:decimal` in `[0, 1]`). Scraped `estleg:legalText` does not. The RT law generator does not stamp confidence.

### Classes

#### Enacted Law Classes
1. **LegalProvision (`estleg:LegalProvision`)**
   - Represents a specific article, section, or paragraph in an enacted law.
2. **TopicCluster (`estleg:TopicCluster`)**
   - Represents a semantic cluster or theme that legal provisions belong to.
3. **LegalConcept (`estleg:LegalConcept`)**
   - Represents a defined legal concept or term used within the legislation.

> **ELI 1.5 (issue #440).** Estonia has no registered ELI URI template, so instance IRIs stay under `estleg:`. The T-Box maps `estleg:Act` ⊑ `eli:LegalResource`, `estleg:LegalProvision` ⊑ `eli:LegalResourceSubdivision`, and `estleg:ActExpression` / `estleg:ProvisionVersion` ⊑ `eli:LegalExpression`. Dates: `estleg:entryIntoForce` ⊑ `eli:date_entry_in_force`, `estleg:repealDate` ⊑ `eli:date_no_longer_in_force`. `kehtiv` is a snapshot date, not `eli:date_publication`; `temporalStatus` is not mapped to `eli:in_force`.
>
> **schema.org (issue #543).** `estleg:Act` is also `rdfs:subClassOf schema:Legislation`; `estleg:legalText` ⊑ `schema:text`; `estleg:references` ⊑ `dcterms:references`. Newly generated act roots are also typed `schema:Legislation`. Existing peeps are not rewritten — RDFS clients load the CV. `entryIntoForce` is not also `schema:legislationDate` (would conflict with the ELI bridge).

#### Draft Legislation Classes (EIS)
4. **DraftLegislation (`estleg:DraftLegislation`)**
   - Represents a legislative draft (eelnõu) that is in the legislative process but not yet enacted.
   - Source: [EIS – Eelnõude infosüsteem](https://eelnoud.valitsus.ee)
   - ELI-DL v3: `rdfs:subClassOf eli-dl:DraftLegislationWork` (`http://data.europa.eu/eli/eli-draft-legislation-ontology#`). Phase individuals are additionally typed `eli-dl:ProcessStage` (issue #443). `estleg:amends` / `estleg:enactedAs` are not `eli-dl:foresees_change_of` (effected change and enactment, not a draft-impact forecast).
5. **LegislativePhase (`estleg:LegislativePhase`)**
   - Represents the current stage of a draft in the legislative pipeline.
   - Phases: `Phase_PublicConsultation`, `Phase_Review`, `Phase_Submission`
6. **DraftType (`estleg:DraftType`)**
   - Classifies the type of draft: bill, regulation, order, etc.
   - Types: `Bill`, `AmendmentBill`, `GovernmentRegulation`, `MinisterialRegulation`, `Regulation`, `GovernmentOrder`, `EUPosition`, `DraftIntent`, `ActionPlan`, `CitizenshipDecision`, `Report`, `Other`

### Properties

#### Enacted Law Properties
* `rdfs:label`: The title or heading of the provision (Estonian plain string, `xsd:string`, no language tag). Replaces the legacy `schema:name`; current generators emit `rdfs:label` and SHACL validates it via `LegalProvisionShape`.
* `estleg:summary`: Free-text summary of the provision (Estonian plain string, `xsd:string`, no language tag; SHACL constrains it with `sh:datatype xsd:string`). Replaces the legacy `schema:text`; current generators emit `estleg:summary`.

> **Note:** Instance-node labels and summaries are Estonian-only and carry no language tag — `FILTER(lang(?x) = "en")` returns no rows. Bilingual (Estonian/English) labelling is a possible future goal; it is not part of the current corpus and is not enforced by SHACL or CI.
* `estleg:topicCluster`: Associates a provision with a TopicCluster.
* `estleg:references`: Defines cross-references to other legal provisions or laws. Typed sub-properties (`estleg:repeals`, `estleg:isLegalBasisFor`, `estleg:exceptionTo`, `estleg:derogatesFrom`) are emitted when the Estonian verb governing the citation is clear (issue #513); untyped `references` remains so existing queries still work.
* `dcterms:isPartOf` / `estleg:isPartOf`: Indicates the hierarchical structure (e.g., paragraph is part of a Chapter/Division). Replaces the legacy `schema:isPartOf`.
* `estleg:partOfAct`: IRI link from a provision (and Chapter) up to its parent **act root** — the structural join SPARQL traverses to answer "all provisions of act X" / "which act does this § belong to". Emitted by every generator (state laws, regulations, KOV, and the VÕS/TsÜS multipart parts) and **required on every provision** by `LegalProvisionShape` (`sh:minCount 1`, `sh:nodeKind sh:IRI`) since issue #415. Do **not** join parent acts by the literal `estleg:sourceAct` title — that is a human-readable string only, not a graph edge. Declared in `controlled_vocabulary.jsonld` as `owl:ObjectProperty` + `owl:FunctionalProperty` (domain `LegalProvision` ∪ `Chapter`, range `Act`): a provision belongs to at most one act (issue #522). ABox values stay IRI links; `scripts/check_tbox_consistency.py` flags a node with two distinct `partOfAct` IRIs or a list-valued `temporalStatus` that is both `inForce` and `repealed`. Dataset nodes (`krr_outputs/void.ttl`, `metadata.jsonld`, and future `combined_ontology.jsonld` headers) carry `estleg:consistencyChecked true` (`xsd:boolean`) as that checker stamp — not an owlrl run over the 265 MB combined graph. T-Box individuals `estleg:TemporalStatus_InForce` `owl:disjointWith` `estleg:TemporalStatus_Repealed`; `estleg:temporalStatus` remains a `DatatypeProperty` whose ABox tokens are `inForce` / `repealed` / `unknown`.
* `estleg:kehtiv`: Snapshot date (`xsd:date`) the **committed act text** is valid as of — the Riigi Teataja `--kehtiv` argument used when the peep was generated (issue #432). This is **not** `temporalStatus` (in-force / repealed), **not** `eli:date_publication`, and **not** `BUILD_EVALUATION_DATE` (fitness / temporal derivation pin, currently `2026-06-01`). Default generator snapshot is `2026-05-01`; many committed peeps still stamp `2026-05-24` from the last full refresh. Point-in-time provision text lives on `estleg:ProvisionVersion` / `estleg:hasVersion`, not on `kehtiv`.
* `skos:prefLabel`: The preferred label for a LegalConcept or TopicCluster.

#### Draft Legislation Properties
* `estleg:legislativePhase`: Links a draft to its current LegislativePhase.
* `estleg:draftType`: Links a draft to its DraftType classification.
* `estleg:eisNumber`: EIS reference number (e.g., "JDM/26-0268").
* `estleg:eisLink`: Direct URL to the draft in EIS (`xsd:anyURI`).
* `estleg:initiator`: Ministry or institution that initiated the draft.
* `estleg:publicationDate`: Date the draft was published in EIS (`xsd:date`).
* `estleg:affectedLawName`: Name of existing law the draft proposes to amend.
* `estleg:amendsLaw`: Object property linking a draft to the existing LegalProvision it amends.
* `estleg:phaseOrder`: Integer indicating the phase ordering (1=consultation, 2=review, 3=submission).

### Legislative Phases

| Phase | Estonian | English | Description |
|-------|----------|---------|-------------|
| `Phase_PublicConsultation` | Avalik konsultatsioon | Public Consultation | Draft is open for public comment |
| `Phase_Review` | Kooskõlastamine | Inter-ministerial Review | Draft is being coordinated between ministries |
| `Phase_Submission` | Esitatud Vabariigi Valitsusele | Submitted to Government | Draft has been submitted to the Government for decision |

### Draft Types

| Type | Estonian | English |
|------|----------|---------|
| `DraftType_Bill` | Seaduseelnõu | Bill |
| `DraftType_AmendmentBill` | Seaduse muutmise eelnõu | Amendment Bill |
| `DraftType_GovernmentRegulation` | VV määruse eelnõu | Government Regulation Draft |
| `DraftType_MinisterialRegulation` | Ministri määruse eelnõu | Ministerial Regulation Draft |
| `DraftType_Regulation` | Määruse eelnõu | Regulation Draft |
| `DraftType_GovernmentOrder` | Korralduse eelnõu | Government Order Draft |
| `DraftType_EUPosition` | EL seisukoha eelnõu | EU Position Draft |
| `DraftType_DraftIntent` | Väljatöötamiskavatsus | Draft Intent / Pre-draft |
| `DraftType_ActionPlan` | Tegevuskava | Action Plan |
| `DraftType_CitizenshipDecision` | Kodakondsuse otsus | Citizenship Decision |
| `DraftType_Report` | Ülevaade | Report |
| `DraftType_Other` | Muu eelnõu | Other Draft |

### Deprecated duplicate statutes (issue #426)

37 early-generation legacy law peeps duplicated statutes that also exist as full
modern files (slug/orthography variants, e.g. `pohiseadus_peep.json` vs
`eesti_vabariigi_pohiseadus_peep.json`). Their act roots are deprecated **in
place**: the root carries `owl:deprecated: true` plus
`dcterms:isReplacedBy: {"@id": <canonical root IRI>}`, the files stay on disk,
and all inbound references to the legacy root IRI were re-pointed to the
canonical root corpus-wide. Deprecated entries are excluded from
`INDEX.json` `total_laws`/`total_files` and listed in its `deprecated_laws`
section instead. The per-file audit trail (match basis, confidence, the 132
kept sole/succession legacy roots) lives in
`data/legacy_statute_decisions.json`. Consumers should filter act roots on
`owl:deprecated` when counting or aggregating statutes.

## JSON-LD Structure Examples

### Enacted Law Example

```json
{
  "@context": {
    "estleg": "https://w3id.org/estleg/",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#"
  },
  "@graph": [
    {
      "@id": "estleg:PS_Par_8",
      "@type": ["owl:NamedIndividual", "estleg:LegalProvision_PS"],
      "estleg:paragrahv": "§ 8",
      "rdfs:label": "§ 8 Kodakondsus",
      "estleg:sourceAct": "Eesti Vabariigi põhiseadus",
      "estleg:summary": "Iga lapsel, kelle vanematest üks on Eesti kodanik, on õigus Eesti kodakondsusele sünni järgi."
    }
  ]
}
```

> **Note on `schema:*` properties.** Earlier versions of this document
> referenced `schema:name`, `schema:text`, and `schema:isPartOf`. The
> current generators DO NOT emit those predicates, and the SHACL
> shapes do not constrain them. Use `rdfs:label` for titles and
> `estleg:summary` for textual content. The migration plan for the
> `metadata.jsonld` description-prose vs hard counts (608/637/11,059)
> is tracked in #162: the hard numbers should move into a structured
> `estleg:statistics` block and the descriptions should become
> qualitative. That metadata change is out of this PR's scope; the
> note here serves as a forward-pointer so future readers know the
> drift is intentional, not undocumented.

#### Law Generation Resume State

`scripts/generate_all_laws.py --regen-state [PATH]` persists per-law progress
for long refreshes. The bare `--regen-state` form writes
`krr_outputs/.regen_state.json`; an explicit path writes there instead. Use
`--reset-regen-state` together with `--regen-state` when the prior ledger is
known stale or intentionally should not drive resume skips. Reset failures are
fatal so operators do not accidentally continue with an old state file.

### Draft Legislation Example

```json
{
  "@context": {
    "estleg": "https://w3id.org/estleg/",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#"
  },
  "@graph": [
    {
      "@id": "estleg:Draft_JDM26_0268",
      "@type": ["owl:NamedIndividual", "estleg:DraftLegislation"],
      "rdfs:label": "Kohtute seaduse ja teiste seaduste muutmise seadus (kohtumenetluse kiirendamine)",
      "estleg:legislativePhase": {"@id": "estleg:Phase_Submission"},
      "estleg:draftType": {"@id": "estleg:DraftType_AmendmentBill"},
      "estleg:eisNumber": "JDM/26-0268",
      "estleg:eisLink": {"@value": "https://eelnoud.valitsus.ee/main/mount/docList/...", "@type": "xsd:anyURI"},
      "estleg:initiator": "Justiitsministeerium",
      "estleg:publicationDate": {"@value": "2026-02-27", "@type": "xsd:date"},
      "estleg:affectedLawName": "Kohtute seaduse"
    }
  ]
}
```

### SPARQL: Find all drafts amending a specific law

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?draft ?title ?phase ?initiator WHERE {
  ?draft a estleg:DraftLegislation ;
         rdfs:label ?title ;
         estleg:legislativePhase ?phaseNode ;
         estleg:initiator ?initiator ;
         estleg:affectedLawName ?lawName .
  ?phaseNode rdfs:label ?phase .
  FILTER(CONTAINS(LCASE(?lawName), "karistusseadustik"))
}
```

### SPARQL: Find all drafts in public consultation

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?draft ?title ?initiator WHERE {
  ?draft a estleg:DraftLegislation ;
         rdfs:label ?title ;
         estleg:legislativePhase estleg:Phase_PublicConsultation ;
         estleg:initiator ?initiator .
}
```

## Court Decision Classes

### CourtDecision (`estleg:CourtDecision`)
Represents a Supreme Court (Riigikohus) decision. Source: [rikos.rik.ee](https://rikos.rik.ee)

### CaseType (`estleg:CaseType`)
Classifies the type of court case.

| Type | Estonian | Case Number Prefix |
|------|----------|--------------------|
| `CaseType_Criminal` | Kriminaalasi | 1-XX-NNNN |
| `CaseType_Civil` | Tsiviilasi | 2-XX-NNNN |
| `CaseType_Administrative` | Haldusasi | 3-XX-NNNN |
| `CaseType_Misdemeanor` | Väärteoasi | 4-XX-NNNN |
| `CaseType_ConstitutionalReview` | Põhiseaduslikkuse järelevalve | 5-XX-NNNN |
| `CaseType_Other` | Muu / määramata | (fallback for decisions with no recognised case-type prefix) |

### DecisionType (`estleg:DecisionType`)

| Type | Estonian |
|------|----------|
| `DecisionType_Judgment` | Kohtuotsus |
| `DecisionType_Ruling` | Kohtumäärus |
| `DecisionType_OrderRuling` | Kohtumäärus (määrusmenetlus) |

### Court Decision Properties

* `estleg:caseNumber`: Case number (e.g., "3-21-2176/52") — `xsd:string`
* `estleg:caseType`: Links to CaseType individual — `owl:ObjectProperty`
* `estleg:decisionType`: Links to DecisionType individual — `owl:ObjectProperty`
* `estleg:decisionDate`: Date of the decision — `xsd:date`
* `estleg:decisionLink`: URL to full decision on riigikohus.ee — `xsd:anyURI`
* `estleg:rikObjectId`: Internal RIK database object ID — `xsd:string`
* `estleg:rikosUrl`: Direct rikos.rik.ee document URL from `rikObjectId` — `xsd:anyURI`
* `estleg:ecliIdentifier`: Official Estonian ECLI (`ECLI:EE:RK:YYYY:` + case number with `-`/`/` as `.`). e-Justice documents this ordinal as the case number (`1-16-2798/84` → `ECLI:EE:RK:2016:1.16.2798.84`). Assigned from H2 2016; **pre-2016 Riigikohus decisions have no official ECLI** (structural gap, not a scrape miss).
* `estleg:chamber`: Deciding chamber from the `legalText` header — `xsd:string`
* `estleg:judge`: Panel member names from the header (presiding first) — `xsd:string` (repeatable)
* `estleg:referencedLaw`: Law abbreviation referenced in the decision — `xsd:string`
* `estleg:interpretsLaw`: Object property linking to a `LegalProvision` (state-law provision) OR a `MunicipalRegulation` / `Act` (KOV act-level citation) interpreted by this decision — `owl:ObjectProperty`. Range is `owl:unionOf (LegalProvision Act)` since Layer 2c PR #3.

`estleg:caseType` and `estleg:decisionType` intentionally point at controlled
enum IRIs whose individuals are not always in the Seadusloome public load graph.
When adding a new enum-like IRI predicate to SHACL, mark its property shape with
`estleg:graphClosureExempt true` so
`scripts/validate_seadusloome_sync.py` derives the graph-closure exemption from
the shape instead of a hardcoded list.

> **Load surfaces.** The repository has three distinct load surfaces —
> combined-only (`krr_outputs/combined_ontology.jsonld` alone), the source
> subcorpora alone, and the Seadusloome public load surface (combined plus the
> public subdirectories) — and a different validation gate covers each. A
> combined-only finding is aggregate-artifact drift (regenerate combined), not
> source-data loss. The canonical description of the surfaces, their invariants,
> and the per-surface gate commands lives in "Load surfaces and validation gates"
> in `docs/VALIDATION_REPORT.md`.

### Court Decision Example

```json
{
  "@id": "estleg:RK_3_21_2176_52",
  "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
  "rdfs:label": "RK 3-21-2176/52",
  "estleg:caseNumber": "3-21-2176/52",
  "estleg:caseType": {"@id": "estleg:CaseType_Administrative"},
  "estleg:decisionType": {"@id": "estleg:DecisionType_Judgment"},
  "estleg:decisionDate": {"@value": "2026-02-26", "@type": "xsd:date"},
  "estleg:decisionLink": {"@value": "https://www.riigikohus.ee/et/lahendid/?asjaNr=3-21-2176/52", "@type": "xsd:anyURI"},
  "estleg:summary": "Keskkonnaameti kassatsioonkaebus..."
}
```

### SPARQL: Find court decisions by case type and year

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?decision ?label ?date WHERE {
  ?decision a estleg:CourtDecision ;
            rdfs:label ?label ;
            estleg:caseType estleg:CaseType_ConstitutionalReview ;
            estleg:decisionDate ?date .
  FILTER(?date >= "2025-01-01"^^xsd:date)
} ORDER BY DESC(?date)
```

## Domestic Regulation Classes

Estonian domestic regulations (*määrused*) are subordinate legal acts issued under an enabling law. They are produced by `scripts/generate_regulations.py` from Riigi Teataja and stored under `krr_outputs/regulations/riik/` (state-level) and, in Phase 2, `krr_outputs/regulations/kov/` (municipal). Provisions of regulations are subclasses of `estleg:LegalProvision` and therefore reuse the existing provision-level shapes, queries, and integrations.

### DomesticRegulation (`estleg:DomesticRegulation`)
Common superclass for any Estonian domestic regulation, regardless of level. `rdfs:subClassOf estleg:Act`. Its two branches — the state-level `estleg:NationalRegulation` and the municipal `estleg:MunicipalRegulation` — are **sibling** subclasses, so no entailment path makes a municipal regulation a state regulation (issue #424). The controlled vocabulary also asserts `estleg:MunicipalRegulation owl:disjointWith estleg:NationalRegulation` (issue #439). `estleg:DomesticRegulation` is intentionally **never stamped on instances**: membership is left to RDFS subclass entailment, and the shared domestic-regulation SHACL constraints are applied by listing `estleg:NationalRegulation` and `estleg:MunicipalRegulation` as explicit `sh:targetClass` values rather than targeting the superclass.

### NationalRegulation (`estleg:NationalRegulation`)
State-level Estonian regulation (*riigi tasandi määrus*) issued by a state organ. `rdfs:subClassOf estleg:DomesticRegulation` and superclass of the government and ministerial regulation classes below. **Not** a superclass of `estleg:MunicipalRegulation` — the two are siblings (issue #424) and `owl:disjointWith` each other (issue #439). Concrete state-regulation instances carry this type plus exactly one of the more specific subclasses below.

### GovernmentRegulation (`estleg:GovernmentRegulation`)
Regulation issued by Vabariigi Valitsus (the Government of the Republic). Subclass of `NationalRegulation`.

### MinisterialRegulation (`estleg:MinisterialRegulation`)
Regulation issued by an individual minister (e.g. *Sotsiaalminister*, *Justiitsminister*). Subclass of `NationalRegulation`.

### MunicipalRegulation (`estleg:MunicipalRegulation`)
Regulation issued by a local government council or government (KOV). `rdfs:subClassOf estleg:DomesticRegulation` — a sibling of `NationalRegulation`, **not** a subclass of it (issue #424; the earlier `subClassOf NationalRegulation` axiom was a logical error that entailed every municipal regulation as state-level). The T-Box also asserts `owl:disjointWith estleg:NationalRegulation` (issue #439). Generated KOV regulation act nodes are typed `estleg:MunicipalRegulation` (plus `estleg:Act`) **only** — the former dual-typing with `estleg:NationalRegulation` was removed and a backfill strips the stray type from all 11,059 KOV files. Shared domestic-regulation SHACL constraints still apply because `MunicipalRegulationShape` names `estleg:MunicipalRegulation` as an explicit `sh:targetClass`, so no OWL/RDFS inference is required at validation time.

### Annex (`estleg:Annex`)
Represents an annex (*lisa*) attached to a regulation. Annexes are emitted as separate named individuals and linked from the regulation via `estleg:hasAnnex`. Annex tables are not normalised in the first pass — the node carries the title, number, and a link to the original document on riigiteataja.ee.

### Provision typing (issue #434)
Law and regulation provisions are typed `estleg:LegalProvision` directly. KOV provisions also carry `estleg:KovProvision`. Per-document classes (`estleg:LegalProvision_<slug>`, `estleg:Regulation_<terviktekstId>`) are no longer minted — membership is `estleg:partOfAct` / `estleg:sourceAct`.

### Domestic Regulation Properties

| Property | Type | Description |
|----------|------|-------------|
| `estleg:documentType` | `xsd:string` | Document type label, typically `"määrus"` |
| `estleg:issuer` | `xsd:string` | Issuing authority (e.g. `"Vabariigi Valitsus"`, `"Sotsiaalminister"`, KOV name) |
| `estleg:actNumber` | `xsd:string` | Act number assigned by the issuer (e.g. `"104"`, `"199"`) |
| `estleg:globalId` | `xsd:string` | Riigi Teataja `globaalID` for the concrete text/redaction (e.g. `"610920"`) |
| `estleg:terviktekstId` | `xsd:string` | Riigi Teataja `terviktekstID` — stable consolidated-text group id (e.g. `"160748"`) |
| `estleg:isKov` | `xsd:boolean` | `true` for municipal (KOV) regulations, `false` for state-level |
| `estleg:preambleText` | `xsd:string` (with `@language`) | Preamble text citing the enabling law/provision |
| `estleg:hasAnnex` | `owl:ObjectProperty` (multi-valued) | IRIs of attached `estleg:Annex` nodes |
| `estleg:entryIntoForce` | `xsd:date` | Date when the regulation entered into force |
| `estleg:repealDate` | `xsd:date` | Date when the regulation was repealed |
| `estleg:lastAmendmentDate` | `xsd:date` | Date of the most recent amendment |
| `estleg:annexNumber` | `xsd:string` | Annex number as printed in the regulation (`Annex` only) |
| `dcterms:source` | IRI | Link to the original act XML on riigiteataja.ee |

### Source provenance

This is the **single normative contract** for how generated ontology
nodes (act-level and provision-level alike) carry "where this came
from" and "what this is called" metadata. It supersedes the looser
guidance that appeared in earlier revisions. The contract is
intentional and stable: `dc:source` is **not** scheduled for removal —
it is the legacy, human-readable descriptor and downstream consumers
still depend on it. The two `dc*` predicates are *complementary*, not
alternatives, and they carry **different kinds of value**:

| Property | Type | Carries | Stability |
|----------|------|---------|-----------|
| `dcterms:source` | IRI object `{"@id": "https://..."}` | The **canonical, resolvable source IRI/URL** for the concrete source record (e.g. the Riigi Teataja act XML at `https://www.riigiteataja.ee/akt/<globalId>.xml`, an EUR-Lex CELEX URL, an EIS document URL, a riigikohus.ee decision URL). Reserved for IRI objects only — never a bare string. Enforced by `validate_source_provenance` in `validate_all.py`. | Canonical; new data SHOULD emit this whenever an authoritative IRI exists. |
| `owl:sameAs` | IRI object `{"@id": "https://..."}` | The same source IRI as `dcterms:source` **when that URL identifies the act text itself** (i.e. it is not merely "about" the act but *is* the act). Optional; only emitted for sources that are the canonical text. | Stable. |
| `dcterms:title` | language-tagged string (or plain string) | The **canonical source title** of the act/document as published (e.g. `"Karistusseadustik"@et`). This is the preferred field for a clean, structured title. | Stable; preferred for titles. |
| `dc:source` | plain string (or array of plain strings) | **Legacy — the human-readable source descriptor.** Depending on the document type this is the RT/source citation string, the act title, or the originating-database name (e.g. `"Karistusseadustik"`, `"Riigi Teataja XML"`, `"Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee"`, `"Riigikohus – rikos.rik.ee"`, `"EUR-Lex – eur-lex.europa.eu"`). It is **semantically overloaded by design** — it is whatever a human would read as "the source" for that document type. Multi-valued arrays of strings are permitted and are preserved losslessly (issue #159); a single-element array is collapsed back to a scalar by `fix_all_issues.normalize_dc_source`. Typing is enforced by `validate_dc_source` in `validate_all.py`. | **Stable but legacy.** New data SHOULD ALSO emit `dcterms:source` (IRI) and/or `dcterms:title` (structured title) where an authoritative value exists. `dc:source` is **not to be removed** — consumers rely on it as the human-readable fallback. |
| `estleg:contentStatus` | string | `structuredBody`, `noStructuredBody`, `controlledVocabulary`, or a more specific documented status. (Not a provenance field per se, but emitted alongside the above on act nodes.) | Stable. |

#### Consumer fallback chain

When a downstream enricher needs **"the resolvable source URL"**, use:

> `dcterms:source` IRI → (if absent) parse a URL out of `dc:source` only if it is actually a URL → (if absent) no resolvable source.

When a downstream enricher needs **"the title / display name of the act"**, use:

> `dcterms:title` → (if absent) `dc:source` string → (if absent) `rdfs:label`.

`estleg_common.source_provenance(rt_url=None, db_name=None, label=None)`
returns the canonical `dc:source` / `dcterms:source` / `dcterms:title`
field pair built to this contract; see that helper's docstring. Note
that most generators construct these fields inline today — the helper
documents and centralises the shape, it is not (yet) wired through
every generator.

#### Why `dc:source` stays overloaded (and that is fine)

A corpus-wide migration to split `dc:source` into one-meaning-per-field
was considered and **deliberately not done**: (1) `dcterms:source`
already carries the unambiguous resolvable IRI, (2) `dcterms:title`
already carries the structured title, (3) `dc:source` is now documented
as exactly "the human-readable source descriptor for this document
type", and (4) breaking the existing `dc:source` shape would churn
every consumer for no semantic gain. The overload is therefore a
**documented, intentional contract**, not an outstanding defect.

### Domestic Regulation Example

```json
{
  "@context": {
    "estleg": "https://w3id.org/estleg/",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dcterms": "http://purl.org/dc/terms/"
  },
  "@graph": [
    {
      "@id": "estleg:Reg_160748_Map_2026",
      "@type": [
        "owl:NamedIndividual",
        "estleg:NationalRegulation",
        "estleg:GovernmentRegulation"
      ],
      "rdfs:label": {"@value": "Volitatud asutuste määramine (määrus)", "@language": "et"},
      "estleg:documentType": "määrus",
      "estleg:issuer": "Vabariigi Valitsus",
      "estleg:actNumber": "199",
      "estleg:globalId": "610920",
      "estleg:terviktekstId": "160748",
      "estleg:isKov": {"@value": "false", "@type": "xsd:boolean"},
      "estleg:entryIntoForce": {"@value": "2003-07-19", "@type": "xsd:date"},
      "estleg:lastAmendmentDate": {"@value": "2003-07-08", "@type": "xsd:date"},
      "estleg:preambleText": {
        "@value": "Määrus kehtestatakse ... seaduse § 12 alusel.",
        "@language": "et"
      },
      "dcterms:source": {"@id": "https://www.riigiteataja.ee/akt/610920.xml"}
    },
    {
      "@id": "estleg:Regulation_160748",
      "@type": ["owl:Class"],
      "rdfs:label": {"@value": "Õigusnorm (paragrahv)", "@language": "et"},
      "rdfs:subClassOf": {"@id": "estleg:LegalProvision"}
    },
    {
      "@id": "estleg:Reg_160748_Par_1",
      "@type": ["owl:NamedIndividual", "estleg:Regulation_160748"],
      "estleg:paragrahv": "§ 1.",
      "rdfs:label": {"@value": "§ 1. [Käesoleva määrusega kehtestatakse ...]", "@language": "et"},
      "estleg:sourceAct": {"@value": "Volitatud asutuste määramine", "@language": "et"},
      "estleg:summary": {"@value": "Käesoleva määrusega kehtestatakse ...", "@language": "et"}
    }
  ]
}
```

### SPARQL: Find all regulations issued by Vabariigi Valitsus

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?reg ?title ?actNumber ?date WHERE {
  ?reg a estleg:GovernmentRegulation ;
       rdfs:label ?title ;
       estleg:issuer "Vabariigi Valitsus" ;
       estleg:actNumber ?actNumber ;
       estleg:entryIntoForce ?date .
} ORDER BY DESC(?date)
```

### SPARQL: Find regulations whose preamble cites a specific law

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?reg ?title ?preamble WHERE {
  ?reg a estleg:NationalRegulation ;
       rdfs:label ?title ;
       estleg:preambleText ?preamble .
  FILTER(CONTAINS(LCASE(STR(?preamble)), "töölepingu seaduse"))
}
```

## EU Legislation Classes

### EULegislation (`estleg:EULegislation`)
Represents a European Union legal act available in Estonian. Source: [EUR-Lex](https://eur-lex.europa.eu)

### EUDocumentType (`estleg:EUDocumentType`)
Classifies the type of EU legal act.

| Type | Estonian | English |
|------|----------|---------|
| `EUDocType_Regulation` | EL maarus | EU Regulation |
| `EUDocType_Directive` | EL direktiiv | EU Directive |
| `EUDocType_Decision` | EL otsus | EU Decision |

### EUInstitution (`estleg:EUInstitution`)
EU institution or body that authored the legal act.

| Institution | Estonian | Code |
|-------------|----------|------|
| `EUInst_EuropeanCommission` | Euroopa Komisjon | COM |
| `EUInst_CouncilOfEU` | Euroopa Liidu Noukogu | CONSIL |
| `EUInst_EuropeanParliament` | Euroopa Parlament | EP |
| `EUInst_EuropeanCentralBank` | Euroopa Keskpank | ECB |

The four rows above are the most common institutions; `estleg:EUInst_*` is an **open set**. The corpus materialises one `estleg:EUInstitution` individual per authoring body actually seen in EUR-Lex (currently ~66 distinct `EUInst_*` IRIs, e.g. agencies, joint bodies, and committees), so consumers should treat any `estleg:EUInst_*` IRI as a valid institution rather than filtering to a fixed list.

### EU Legislation Properties

* `estleg:celexNumber`: CELEX identifier (e.g., "32016R0679") -- `xsd:string`
* `estleg:euDocumentType`: Links to EUDocumentType individual -- `owl:ObjectProperty`
* `estleg:euInstitution`: Links to EUInstitution individual(s) -- `owl:ObjectProperty`
* `estleg:eurLexLink`: URL to Estonian version in EUR-Lex -- `xsd:anyURI`
* `estleg:eliIdentifier`: European Legislation Identifier URI -- `xsd:anyURI`
* `estleg:documentDate`: Date of the legal act -- `xsd:date`
* `estleg:inForce`: Whether the act is currently in force -- `xsd:boolean`

### EU Legislation Example

```json
{
  "@id": "estleg:EU_32016R0679",
  "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
  "rdfs:label": "Euroopa Parlamendi ja noukogu maarus (EL) 2016/679 (isikuandmete kaitse uldmaarus)",
  "estleg:celexNumber": "32016R0679",
  "estleg:euDocumentType": {"@id": "estleg:EUDocType_Regulation"},
  "estleg:eurLexLink": {"@value": "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:32016R0679", "@type": "xsd:anyURI"},
  "estleg:eliIdentifier": {"@value": "http://data.europa.eu/eli/reg/2016/679/oj", "@type": "xsd:anyURI"},
  "estleg:documentDate": {"@value": "2016-04-27", "@type": "xsd:date"},
  "estleg:inForce": {"@value": "true", "@type": "xsd:boolean"},
  "estleg:euInstitution": [
    {"@id": "estleg:EUInst_EuropeanParliament"},
    {"@id": "estleg:EUInst_CouncilOfEU"}
  ]
}
```

### SPARQL: Find EU directives in force

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?act ?title ?date WHERE {
  ?act a estleg:EULegislation ;
       rdfs:label ?title ;
       estleg:euDocumentType estleg:EUDocType_Directive ;
       estleg:inForce "true"^^xsd:boolean ;
       estleg:documentDate ?date .
} ORDER BY DESC(?date) LIMIT 20
```

### SPARQL: Find EU regulations by institution

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?act ?title ?date WHERE {
  ?act a estleg:EULegislation ;
       rdfs:label ?title ;
       estleg:euDocumentType estleg:EUDocType_Regulation ;
       estleg:euInstitution estleg:EUInst_EuropeanParliament ;
       estleg:documentDate ?date .
} ORDER BY DESC(?date) LIMIT 20
```

## EU Court Decision Classes

### EUCourtDecision (`estleg:EUCourtDecision`)
Represents a CJEU decision available in Estonian. Source: [EUR-Lex](https://eur-lex.europa.eu) / [CURIA](https://curia.europa.eu)

### EUCourtDecisionType (`estleg:EUCourtDecisionType`)

| Type | Estonian | English |
|------|----------|---------|
| `EUDecType_Judgment` | Kohtuotsus | Judgment |
| `EUDecType_Order` | Kohtumaarus | Order |
| `EUDecType_AGOpinion` | Kohtujuristi ettepanek | Advocate General Opinion |
| `EUDecType_CourtOpinion` | Kohtu arvamus | Court Opinion |
| `EUDecType_Other` | Muu / määramata | Other (fallback for decisions with no recognised CURIA document type) |

### EUCourt (`estleg:EUCourt`)

| Court | Estonian | Code |
|-------|----------|------|
| `EUCourt_CourtOfJustice` | Euroopa Kohus | CJ |
| `EUCourt_GeneralCourt` | Uldkohus | GCEU |
| `EUCourt_CivilServiceTribunal` | Avaliku Teenistuse Kohus | CST |

### EU Court Decision Properties

* `estleg:celexNumber`: CELEX identifier (e.g., "62014CJ0438") -- `xsd:string`
* `estleg:euCourtDecisionType`: Links to EUCourtDecisionType -- `owl:ObjectProperty`
* `estleg:euCourt`: Links to EUCourt -- `owl:ObjectProperty`
* `estleg:ecliIdentifier`: ECLI identifier (e.g., "ECLI:EU:C:2016:758") -- `xsd:string`
* `estleg:euCaseNumber`: Case number (e.g., "C-438/14") -- `xsd:string`
* `estleg:curiaLink`: URL to Estonian version in EUR-Lex -- `xsd:anyURI`
* `estleg:documentDate`: Date of the decision -- `xsd:date`

### EU Court Decision Example

```json
{
  "@id": "estleg:EUCJ_62014CJ0438",
  "@type": ["owl:NamedIndividual", "estleg:EUCourtDecision"],
  "rdfs:label": "Euroopa Kohtu otsus — Bogendorff von Wolffersdorff — Kohtuasi C-438/14",
  "estleg:celexNumber": "62014CJ0438",
  "estleg:euCourtDecisionType": {"@id": "estleg:EUDecType_Judgment"},
  "estleg:euCourt": {"@id": "estleg:EUCourt_CourtOfJustice"},
  "estleg:ecliIdentifier": "ECLI:EU:C:2016:758",
  "estleg:euCaseNumber": "C-438/14",
  "estleg:curiaLink": {"@value": "https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:62014CJ0438", "@type": "xsd:anyURI"},
  "estleg:documentDate": {"@value": "2016-10-06", "@type": "xsd:date"}
}
```

### SPARQL: Find EU court judgments by date

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?decision ?title ?ecli ?date WHERE {
  ?decision a estleg:EUCourtDecision ;
            rdfs:label ?title ;
            estleg:euCourtDecisionType estleg:EUDecType_Judgment ;
            estleg:ecliIdentifier ?ecli ;
            estleg:documentDate ?date .
} ORDER BY DESC(?date) LIMIT 20
```

## Integration & Cross-Linking Classes

### Sanction (`estleg:Sanction`)
Represents a penalty or sanction defined by a legal provision.

| Property | Type | Description |
|----------|------|-------------|
| `estleg:sanctionType` | `xsd:string` | Type: imprisonment, pecuniary_punishment, fine, arrest, coercive_payment |
| `estleg:maxPenalty` | `xsd:string` | Maximum penalty (e.g., "5 years", "300 fine units") |
| `estleg:minPenalty` | `xsd:string` | Minimum penalty if specified |
| `estleg:applicableProvision` | `owl:ObjectProperty` | IRI of the provision defining this sanction |

### Institution (`estleg:Institution`)
Represents a state institution with legal competences.

| Property | Type | Description |
|----------|------|-------------|
| `estleg:competenceType` | `xsd:string` | Type: supervision, licensing, enforcement, regulation |
| `estleg:hasCompetence` | `owl:ObjectProperty` | Institution → reified `estleg:Competence` node(s). Inverse of `estleg:institution` on Competence nodes. |

Institution → Competence links use `estleg:hasCompetence` (inverse of
`estleg:institution` on Competence nodes). Competence remains the reified
aggregate (see below) that carries `estleg:grantedBy` /
`estleg:appliesToProvision`; individual provisions point back to the institution
via `estleg:competentAuthority`. There is no direct Institution → provision
competence property.

### Competence (`estleg:Competence`)
Reified institutional competence sidecar node generated under
`krr_outputs/institutions/`.

| Property | Type | Description |
|----------|------|-------------|
| `estleg:institution` | `owl:ObjectProperty` | Institution whose competence is being described |
| `estleg:competenceType` | `xsd:string` | Type: `supervision`, `licensing`, `enforcement`, `regulation`, `advisory`, or `general` |
| `estleg:appliesToProvision` | `owl:ObjectProperty` | Provision(s) to which this competence applies |
| `estleg:appliesToProvisionCount` | `xsd:integer` | Full provision count when the sidecar list is capped |
| `estleg:grantedBy` | `owl:ObjectProperty` | Act IRI that most directly grants the competence, derived from the source act distribution |
| `estleg:competenceArea` | `xsd:string` | Coarse thematic area for overlap/gap analysis across institutions |

### NormativeType (`estleg:NormativeType`)
Deontic classification of provisions. Individuals: `NormType_Obligation`, `NormType_Right`, `NormType_Permission`, `NormType_Prohibition`, `NormType_Definition` (heading *mõiste* or first-line *tähendab*/*mõistetakse*; issue #461).

### Section (`estleg:Section`)
Represents a section (jagu/peatükk) in the KarS special parts structure, generated by `generate_kars_eriosa_jsonld.py`.

| Property | Type | Description |
|----------|------|-------------|
| `estleg:sectionNumber` | `xsd:string` | Section number |

> **Important — Section is validated by predicate presence, not class
> typing.** `SectionShape` in `shacl/estonian_legal_shapes.ttl`
> uses `sh:targetSubjectsOf estleg:sectionNumber` rather than
> `sh:targetClass estleg:Section`. The practical consequence:
> any node that emits `estleg:sectionNumber` is treated as a
> Section for SHACL validation, regardless of the `@type` array.
> Conversely, a node typed `estleg:Section` but missing
> `estleg:sectionNumber` will NOT be validated. SPARQL queries
> over the corpus should follow the same convention and select on
> the predicate, e.g.:
>
> ```sparql
> SELECT ?s ?n WHERE { ?s estleg:sectionNumber ?n }
> ```
>
> rather than `?s a estleg:Section`. The same predicate-targeting
> pattern is used by `LegalProvisionShape`
> (`sh:targetSubjectsOf estleg:paragrahv`) and avoids requiring
> RDFS subclass inference at validation time.

### AmendmentEvent (`estleg:AmendmentEvent`)
Represents an **effected** amendment event — a change actually applied to an act, derived from Riigi Teataja `<muutmismarge>` markers. Generated by `generate_amendment_history.py` and stored in `krr_outputs/amendments/`. Effected events carry `estleg:amends`, `estleg:amendmentDate` / `estleg:entryIntoForce`, and the latest one per act may carry `estleg:isCurrentAmendment`; act roots link to them via `estleg:amendedBy`. Never-enacted draft amendments are **not** modelled with this class — see `estleg:ProposedAmendment` below (issue #423). The controlled vocabulary asserts `estleg:AmendmentEvent owl:disjointWith estleg:ProposedAmendment` (issue #439).

### ProposedAmendment (`estleg:ProposedAmendment`)
Represents a **proposed, not-yet-enacted** amendment derived from a draft amendment bill in EIS (Review / Submission / PublicConsultation phase). Because the referenced draft has not been adopted, the node MUST NOT be typed `estleg:AmendmentEvent` and MUST NOT assert effected semantics (issue #423). The T-Box formalises this as `estleg:ProposedAmendment owl:disjointWith estleg:AmendmentEvent` (issue #439). A `ProposedAmendment` carries proposal-scoped predicates only:

* `estleg:proposesToAmend` → the target act/provision (proposal scope; **not** `estleg:amends`, which asserts an effected change).
* `estleg:publicationDate` → the draft's EIS publication date (**not** `estleg:amendmentDate`, which is reserved for adoption dates).
* `estleg:amendingDraft` → the `estleg:DraftLegislation` (`Draft_*`) node behind the proposal.

A `ProposedAmendment` **never** carries `estleg:isCurrentAmendment`. Act roots link to these nodes via `estleg:hasProposedAmendment` (the inverse of `estleg:proposesToAmend`) — never via `estleg:amendedBy`, which is reserved for effected events.

### LegalConcept (`estleg:LegalConcept`) — Extended
Extended with SKOS vocabulary for cross-law concept linking.

| Property | Type | Description |
|----------|------|-------------|
| `skos:prefLabel` | `xsd:string` | Preferred term label (Estonian) |
| `skos:definition` | `xsd:string` | Definition text from source provision |
| `skos:exactMatch` | `owl:ObjectProperty` | Same concept defined in another law |
| `skos:closeMatch` | `owl:ObjectProperty` | Similar concept in another law |
| `estleg:definedIn` | `owl:ObjectProperty` | Provision(s) that define this concept |

## Integration Properties (Cross-Linking)

These properties enable cross-referencing between different parts of the legal system.

### Cross-Law References
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:references` | LegalProvision | LegalProvision (IRI) | Cross-reference to another provision. **Untyped** in the current corpus: extractors emit a flat `estleg:references` list and do not yet attach `estleg:referenceType`. A later emission pass is required before repeal / legal-basis / exception / derogation queries can distinguish those edges (issue #513; T-Box only in this slice). |
| `estleg:referencedBy` | LegalProvision | LegalProvision (IRI) | Inverse: provisions referencing this one |
| `estleg:referenceType` | `Citation` ∪ `rdf:Statement` | `ReferenceType` (`RefType_*` individual) | T-Box qualifier for a reified `estleg:Citation` or an `rdf:Statement` of an `estleg:references` edge (issue #513). Closed individuals: `RefType_Citation`, `RefType_Repeal`, `RefType_LegalBasis`, `RefType_Exception`, `RefType_Derogation`. **Not emitted on existing `estleg:references` edges** until a later pass. |
| `estleg:semanticallySimilarTo` | LegalProvision | LegalProvision (IRI) | Provisions with similar content from other laws |
| `estleg:definesTerm` | LegalProvision | LegalConcept (IRI) | Legal concept defined by this provision |

#### Reference types (`estleg:ReferenceType`, issue #513 T-Box)

The citation graph is still a single flat `estleg:references` / `estleg:referencedBy` pair. The controlled vocabulary now declares `estleg:referenceType` and the five `estleg:RefType_*` individuals so a later extractor can classify an edge without inventing new predicates. Until that emission pass, **do not assume** a `references` triple is a repeal, legal basis, exception, or derogation — it is only an untyped mention.

| Individual | Estonian | English |
|------------|----------|---------|
| `estleg:RefType_Citation` | Viide | Citation (unspecified mention) |
| `estleg:RefType_Repeal` | Kehtetuks tunnistamine | Repeal |
| `estleg:RefType_LegalBasis` | Õiguslik alus | Legal basis |
| `estleg:RefType_Exception` | Erand | Exception |
| `estleg:RefType_Derogation` | Derogatsioon | Derogation |

### Court Decision Links
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:interpretsLaw` | CourtDecision | LegalProvision \| MunicipalRegulation/Act (IRI) | Specific provision (state law) or whole act (KOV) interpreted. `owl:unionOf` since Layer 2c PR #3. |
| `estleg:interpretedBy` | LegalProvision \| MunicipalRegulation (informal) | CourtDecision (IRI) | Inverse of `interpretsLaw`. The object is always a `CourtDecision`; the subject is a `LegalProvision` for state-law citations, and may also be a `MunicipalRegulation` act node for KOV act-level citations (since Layer 2c PR #3). No formal `rdfs:range` is declared (range stays unconstrained beyond `sh:nodeKind sh:IRI`); SHACL `MunicipalRegulationShape` adds a parallel `sh:nodeKind sh:IRI` constraint to document the new subject usage. |

### EU Transposition
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:transposesDirective` | Act | EULegislation (IRI) | EU directive transposed by this law. **This** (and `krr_outputs/reports/transposition_mapping.json`) is Estonian transposition — not the HarmonisationLink layer. |
| `estleg:transposedBy` | EULegislation | Act (IRI) | Inverse: Estonian law transposing this directive |
| `estleg:harmonisedWith` | Act | HarmonisationLink (IRI) | Act → neighbour-state comparative NIM record (LV/LT/FI/SE) for the same EU directive. Not Estonian article-level transposition. Emitted **only** on law peeps (act-level). Inverse of `estleg:harmonises` (issue #425). Combined `estleg:HarmonisationLink` objects may be hollow `isStubNode` closure stubs; real neighbour measures live in `krr_outputs/harmonisation/`. |
| `estleg:harmonises` | HarmonisationLink | Act (IRI) | Inverse: comparative NIM record → the Estonian act(s) that transpose the shared directive. Emitted **only** on the aggregate `estleg:Harmonisation_<celex>` nodes in `krr_outputs/harmonisation/harmonisation_by_directive/`. Inverse of `estleg:harmonisedWith` (issue #425). |

> **Scope (issue #557).** Harmonisation sidecars (`krr_outputs/harmonisation/`) are neighbour-state **comparative** NIM measures for LV/LT/FI/SE. They are **not** Estonian article-level transposition. Estonian transposition is `estleg:transposesDirective` / `krr_outputs/reports/transposition_mapping.json`. Combined `estleg:HarmonisationLink` nodes may be hollow `isStubNode` closure stubs; real neighbour measures live in the sidecar.

### Subject Classification
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `dcterms:subject` | Act | IRI | Optional act-level EuroVoc concept URI (e.g., `http://eurovoc.europa.eu/2411`). Current classifier is keyword-based and reports quality status separately. When present, SHACL expects a EuroVoc IRI. |

### Temporal Properties
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:entryIntoForce` | Act | `xsd:date` | Date when the selected act snapshot became effective |
| `estleg:repealDate` | Act | `xsd:date` | Date when the selected act snapshot was repealed |
| `estleg:lastAmendmentDate` | Act | `xsd:date` | Most recent amendment date for the selected act snapshot |
| `estleg:publicationDate` | Act | `xsd:date` | Publication date for the selected act snapshot |
| `estleg:temporalStatus` | Act | `xsd:string` | Status evaluated against the build's declared temporal evaluation date. ABox tokens: `inForce`, `repealed`, `unknown` (SHACL also allows `notYetEffective`). Corresponding T-Box individuals `estleg:TemporalStatus_*`; the property is not an ObjectProperty (#522). |

### Amendment Properties

Effected amendments (from Riigi Teataja) and proposed amendments (from draft bills) use **separate** property sets so a never-enacted draft is never published as an effected legal change (issue #423). Effected: `estleg:amendedBy` ⇄ `estleg:amends`. Proposed: `estleg:hasProposedAmendment` ⇄ `estleg:proposesToAmend`.

| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:amendedBy` | Act | AmendmentEvent (IRI) | Act-root → **effected** amendment events only (from Riigi Teataja). Inverse of `estleg:amends`. |
| `estleg:amends` | AmendmentEvent | LegalProvision / Act (IRI) | What this effected amendment event changed. Inverse of `estleg:amendedBy`. |
| `estleg:amendmentDate` | AmendmentEvent | `xsd:date` | Adoption / legal-effect date of an effected amendment. Reserved for effected events — proposals use `estleg:publicationDate`. |
| `estleg:isCurrentAmendment` | AmendmentEvent | `xsd:boolean` | Marks the latest **effected** event per act. Never emitted on a `ProposedAmendment`. |
| `estleg:hasProposedAmendment` | Act | ProposedAmendment (IRI) | Act-root → **proposed** (not-yet-enacted) amendment nodes. Inverse of `estleg:proposesToAmend`. |
| `estleg:proposesToAmend` | ProposedAmendment | LegalProvision / Act (IRI) | Act/provision a draft amendment bill proposes to change. Inverse of `estleg:hasProposedAmendment`. |
| `estleg:amendingDraft` | ProposedAmendment | DraftLegislation (IRI) | The `Draft_*` node behind a proposed amendment. |
| `estleg:changeType` | DraftLegislation | `xsd:string` | Type of change: amends, repeals, supplements, enacts |
| `estleg:affectedBy` | LegalProvision | DraftLegislation (IRI) | Pending drafts affecting this provision |

### Provision versioning (historical redactions)

**Status:** model defined (issue #131); sidecar population is handled by
`scripts/generate_provision_versions.py`. The first full ingestion keeps
`estleg:versionOf` as the canonical link from version nodes to stable provision
nodes; `estleg:hasVersion` / `estleg:currentVersion` back-links on law peeps are
deferred so `combined_ontology.jsonld` does not point at sidecar-only nodes.

The enacted-law corpus is *current snapshot first*: each `estleg:LegalProvision`
carries the text from one selected Riigi Teataja consolidated text (`terviktekst`), and
`estleg:globalId` on the act node points at that one redaction. Historical text
versions live in `krr_outputs/provision_versions/*.jsonld` and must be loaded
with the public graph surface when historical queries are needed.

The `estleg:ProvisionVersion` model adds that capability. It keeps the **stable provision
identity** (the `estleg:LegalProvision` IRI) deliberately separate from the
**redaction/version identity** (the `estleg:ProvisionVersion` IRI), and records a
non-overlapping validity interval per version.

#### ProvisionVersion (`estleg:ProvisionVersion`)
A redaction-specific version of a provision's text, with the interval over which that text
was in force.

| Property | Domain | Range | Cardinality | Description |
|----------|--------|-------|-------------|-------------|
| `estleg:versionOf` | ProvisionVersion | LegalProvision (IRI) | exactly 1 | The provision this version belongs to. Inverse of `estleg:hasVersion`. |
| `estleg:versionValidFrom` | ProvisionVersion | `xsd:date` | exactly 1 | When this text became effective. |
| `estleg:versionValidTo` | ProvisionVersion | `xsd:date` | 0–1 | Inclusive last in-force day. Absent ⇒ still current. An as-of query for the peep `kehtiv` date uses interval containment (`validFrom <= kehtiv` and no `validTo` or `kehtiv <= validTo`). A current redaction that started before the snapshot (e.g. 2026-05-22 vs kehtiv 2026-05-24) is not a lag (#532). |
| `estleg:versionText` | ProvisionVersion | `xsd:string` | 1+ | The provision text at this redaction, verbatim. |
| `estleg:versionRedactionId` | ProvisionVersion | `xsd:string` | 0–1 | The Riigi Teataja `terviktekstID` of the redaction that produced this text. |
| `estleg:supersededByVersion` | ProvisionVersion | ProvisionVersion (IRI) | 0–1 | The chronologically next version of the same provision. Absent on the current version. |

| Property | Domain | Range | Cardinality | Description |
|----------|--------|-------|-------------|-------------|
| `estleg:hasVersion` | LegalProvision | ProvisionVersion (IRI) | 0+ | Optional inverse of `estleg:versionOf`. Deferred in the current sidecar population; consumers should query `?version estleg:versionOf ?provision` instead. |
| `estleg:currentVersion` | LegalProvision | ProvisionVersion (IRI) | 0–1 | Optional convenience pointer to the latest version. Deferred in the current sidecar population. |

SHACL: `estleg:ProvisionVersionShape` (`sh:targetClass estleg:ProvisionVersion`) enforces the
cardinalities above; `estleg:LegalProvisionShape` gains optional `estleg:hasVersion` /
`estleg:currentVersion` property shapes (`sh:nodeKind sh:IRI`, `sh:minCount 0`) so existing
provisions still conform.

##### Example

Version sidecar node:

```json
{
  "@id": "estleg:LegalProvision_TsÜS_40_v1",
  "@type": ["owl:NamedIndividual", "estleg:ProvisionVersion"],
  "estleg:versionOf": {"@id": "estleg:LegalProvision_TsÜS_40"},
  "estleg:versionValidFrom": {"@type": "xsd:date", "@value": "2011-07-01"},
  "estleg:versionValidTo": {"@type": "xsd:date", "@value": "2018-12-31"},
  "estleg:versionText": "§ 40. Tehing on toiming või omavahel seotud toimingute kogum, milles sisaldub kindla õigusliku tagajärje kaasatoomisele suunatud tahteavaldus.",
  "estleg:versionRedactionId": "13335775",
  "estleg:supersededByVersion": {"@id": "estleg:LegalProvision_TsÜS_40_v2"}
}
```

Stable provision node in the law peep. Back-links such as `estleg:hasVersion`
and `estleg:currentVersion` are optional; consumers can join from the sidecar
node through `estleg:versionOf`.

```json
{
  "@id": "estleg:LegalProvision_TsÜS_40",
  "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
  "estleg:paragrahv": "TsÜS § 40",
  "estleg:summary": "Defines what counts as a transaction (tehing)."
}
```

##### SPARQL — "what did § X say as of date D?"

Load the law peep and its `krr_outputs/provision_versions/<law>.jsonld` sidecar,
then query from version nodes back to the stable provision IRI. The worked
example below uses the Kohaliku omavalitsuse volikogu valimise seadus (KOVVS):

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>

SELECT ?versionText WHERE {
  ?provision estleg:paragrahv "§ 1." .
  ?v estleg:versionOf        ?provision ;
     estleg:versionText      ?versionText ;
     estleg:versionValidFrom ?from .
  OPTIONAL { ?v estleg:versionValidTo ?to . }
  FILTER ( ?from <= "2015-06-01"^^xsd:date
           && ( !BOUND(?to) || ?to >= "2015-06-01"^^xsd:date ) )
}
```

`scripts/generate_provision_versions.py --all --max-redactions 0` is the full
ingestion mode. It writes sidecars under `krr_outputs/provision_versions/`,
coverage metrics under `krr_outputs/reports/kov/`, and per-law progress rows in
`krr_outputs/reports/provision_versions_report.json`.

Full ingestion resumes by default from `provision_versions_report.json`. A law
is skipped only when the prior row uses the supported report schema, emitted at
least one version without errors or warnings, used a redaction cap that covers
the current run, and still matches the current Riigi Teataja redaction id and
valid-from date. `--max-redactions 0` means unbounded history, so rows from a
sample run do not satisfy a full-history run. Reports written before
`schemaVersion` was added are intentionally ignored once and the affected laws
are reprocessed.

Use `--no-resume` (or its compatibility alias `--force`) to bypass the prior
report and process every selected law from scratch. Use `--no-sleep` only for
warm-cache or controlled runs where the polite inter-fetch delay is not needed.

### Annotations (practitioner layer)

**Status:** model defined (issue #40) and populated from the Õiguskantsler
archive (issue #199). The annotation sidecar is generated from the live listing
plus usable PDF body text; see the Õiguskantsler ingestion section in
`docs/VALIDATION_REPORT.md` for the current node count, coverage, and probe
metrics.

The corpus captures the law *as written*, not how it is applied or interpreted in practice.
The `estleg:Annotation` model adds a separate layer of practitioner-facing notes — guidance,
commentary, practice notes, warnings, interpretations — attached to legal entities, without
mixing them into the law-as-written nodes.

#### Annotation (`estleg:Annotation`)
A practitioner-facing note about a `Law`, an `estleg:LegalProvision`, a `MunicipalRegulation`,
a `CourtDecision`, an EU instrument, etc.

| Property | Domain | Range | Cardinality | Description |
|----------|--------|-------|-------------|-------------|
| `estleg:annotates` | Annotation | `owl:Thing` (IRI) | exactly 1 | The legal entity this annotation is about. Range is intentionally unconstrained — an annotation may attach to any legal entity. |
| `estleg:annotationText` | Annotation | `xsd:string` | 1+ | The body of the note. |
| `estleg:annotationType` | Annotation | `xsd:string` | exactly 1 | One of `guidance`, `commentary`, `practice_note`, `warning`, `interpretation` (SHACL `sh:in` enum). |
| `estleg:annotationSource` | Annotation | `xsd:string` | 0–1 | Where the note comes from, as a string (e.g. `"Õiguskantsler"`, `"RT kommentaar"`, a publication name). String for now; a future iteration may make it an IRI to a source node. |
| `estleg:annotationSourceUrl` | Annotation | `xsd:anyURI` | 0–1 | Optional link to the source document. |
| `estleg:annotationDate` | Annotation | `xsd:date` | 0–1 | When the annotation was authored / published. |

SHACL: `estleg:AnnotationShape` (`sh:targetClass estleg:Annotation`) requires
`estleg:annotates` (exactly 1, IRI), `estleg:annotationText` (1+, string), and
`estleg:annotationType` (exactly 1, string, `sh:in` the enum above); `estleg:annotationSource`,
`estleg:annotationSourceUrl`, and `estleg:annotationDate` are optional with the datatypes above.

##### Example

```json
{
  "@id": "estleg:Annotation_OK_2020_TsÜS_40",
  "@type": ["owl:NamedIndividual", "estleg:Annotation"],
  "estleg:annotates": {"@id": "estleg:LegalProvision_TsÜS_40"},
  "estleg:annotationText": "Õiguskantsler on rõhutanud, et tahteavalduse tuvastamisel tuleb arvestada poolte tegelikku tahet, mitte üksnes sõnastust.",
  "estleg:annotationType": "interpretation",
  "estleg:annotationSource": "Õiguskantsler",
  "estleg:annotationSourceUrl": {"@type": "xsd:anyURI", "@value": "https://www.oiguskantsler.ee/et/seisukohad"},
  "estleg:annotationDate": {"@type": "xsd:date", "@value": "2020-03-15"}
}
```

##### SPARQL — annotations about a legal entity

The current Õiguskantsler ingestion attaches annotations at the **act** level
(`estleg:annotates` targets the act root). Load
`krr_outputs/annotations/*.jsonld` and anchor on the act IRI — here the Kohaliku
omavalitsuse korralduse seadus (KOKS) act node:

```sparql
PREFIX estleg: <https://w3id.org/estleg/>

SELECT ?text ?type ?source WHERE {
  ?ann estleg:annotates       estleg:KOKS_Map_2026 ;
       estleg:annotationText  ?text ;
       estleg:annotationType  ?type .
  OPTIONAL { ?ann estleg:annotationSource ?source . }
}
```

### Normative Classification
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:normativeType` | LegalProvision | NormativeType (IRI) | Obligation, right, permission, prohibition, definition |
| `estleg:dutyHolder` | LegalProvision | TargetGroup (IRI) | Who must comply. Same closed enum as `targetGroup` (`estleg:TargetGroup_*`). Unmapped sentence-initial phrases are not stored (#460). |
| `estleg:targetGroup` | LegalProvision | TargetGroup (IRI), multi-valued | Affected group(s): `estleg:TargetGroup_Citizen`, `_Business`, `_PublicBody`, `_Official`, `_NGO` |

Citizen share (#460 sample review): `targetGroup` is not assigned by
absence-of-others. Weak generic cues (`isik` / `kasutaja` / `valdaja` /
`omanik`) are dropped when a business or public-body cue is also present.
Committed samples: `estleg:AS_Par_17` (Alkoholiseadus) is business-only
(alcohol handler); `estleg:TLS_Par_2` (Töölepingu seadus) is citizen
(employee). Remaining citizen assignments require an explicit addressee
cue such as `töötaja` or `füüsiline isik`.

### Institutional Competence
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:hasCompetence` | Institution | Competence (IRI) | Institution → its reified Competence aggregates. Inverse of `estleg:institution`. |
| `estleg:competentAuthority` | LegalProvision | Institution or Issuer (IRI) | Responsible authority — `Institution_*` for state/general detections, `Issuer_*` for KOV-bound matches (see KOV subsection below) |
| `estleg:competenceType` | Institution | `xsd:string` | Type: supervision, licensing, enforcement |
| `estleg:grantedBy` | Competence | Act (IRI) | The act under which this competence is granted when a primary source act is derivable |
| `estleg:competenceArea` | Competence | `xsd:string` | Coarse thematic area used for cross-institutional grouping |

### Layer 2c PR #2 — KOV Issuer-binding for `competentAuthority`

KOV regulations frequently delegate competence via generic body
words: "linnavolikogu kehtestab korra", "vallavalitsus väljastab
loa". Layer 2c PR #2 detects these body words and binds them to
the existing Layer 1 Issuer registry when the source act is a
`MunicipalRegulation` with `enactedByMunicipality` scope.

Resolution strategy (3 priority paths):

1. **Path 1 — same body type + suffix match.** When the body
   word's body type AND slug suffix match the source act's
   `enactedBy` issuer, the `competentAuthority` triple targets
   the source issuer directly. Example: `Issuer_abja_vallavolikogu`
   act + `vallavolikogu` body word → `Issuer_abja_vallavolikogu`.
2. **Path 2 — paired body in the same place family.** Body type
   differs from source issuer's; derive paired slug by swapping
   the body suffix. Family-prefix compatibility required
   (`linna*` and `valla*` cannot pair). Example:
   `Issuer_abja_vallavolikogu` act + `vallavalitsus` body word
   → `Issuer_abja_vallavalitsus` (if registered).
3. **Path 3 — municipality-wide unique match.** Reached only
   when source_issuer is missing, unknown to the registry, or
   has municipality inconsistent with the act's stated
   `enactedByMunicipality`. Best-effort rescue with the act's
   stated municipality + body type + suffix compatibility.
   Successful Path 3 resolutions are counted in
   `fallback_hits` as a diagnostic signal for Layer 1 follow-up.

Abstain conditions (the resolver returns `None`):

- Source act is a Law / state regulation (no `enactedByMunicipality`).
- Path 1 suffix mismatch
  (e.g. `Issuer_abja_vallavolikogu` + `linnavolikogu`).
- Path 2 paired body absent in registry OR in different
  municipality.
- Path 2 cross-family swap rejected
  (e.g. `Issuer_elva_linnavalitsus` + `vallavolikogu`).
- Path 3 finds zero or more than one suffix-compatible match.

Example KOV-bound provision:

```json
{
  "@id": "estleg:Reg_TLN_15_Map_2020_Par_1",
  "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
  "estleg:paragrahv": "§ 1",
  "estleg:summary": "Linnavolikogu kehtestab maksu määra.",
  "estleg:competentAuthority": [
    {"@id": "estleg:Issuer_tallinna_linnavolikogu"}
  ],
  "estleg:competenceType": "regulation"
}
```

The Issuer entity is the canonical anchor for "this municipal
body" across the graph. Layer 1's `enactedBy` and Layer 2c PR #2's
`competentAuthority` BOTH target IRIs from the same registry.
On Path 1 the two IRIs coincide (the act's enactedBy IS the
competent authority). On Path 2 they intentionally differ but
stay within the same place's issuer family.

SHACL: the existing `competentAuthority` constraint
(`sh:nodeKind sh:IRI`) accepts both `Institution_*` and `Issuer_*`
IRI forms. The constraint description is updated in this PR to
mention the broader value range; no new constraint block is
added.

### Sanctions
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:hasSanction` | LegalProvision | Sanction (IRI) | Sanction defined by this provision |
| `estleg:sanctionType` | Sanction | `xsd:string` | imprisonment, fine, etc. |
| `estleg:maxPenalty` | Sanction | `xsd:string` | Maximum penalty value |

### Layer 2c — `enforcedAtLevel`

Every provision that carries `estleg:hasSanction` ALSO carries exactly
one `estleg:enforcedAtLevel` literal classifying the parent act type:

- `"municipality"` — provisions in `MunicipalRegulation` acts.
- `"state"` — provisions in any other act type (`Law`,
  `NationalRegulation`, `GovernmentRegulation`, `MinisterialRegulation`).

The classification is purely structural (act `@type` field) — it does
NOT analyse delegation language or sanction-text context. State laws
that delegate enforcement to local government (e.g. KOKS § 66 lg 4
parking-fine ceilings) classify as `"state"` because the parent act is
`estleg:Law`. If a future analytics query needs delegation-aware
classification, a separate property (`estleg:enforcementContext`) can
be introduced without colliding.

Example:

```json
{
  "@id": "estleg:Reg_TLN_15_Map_2020_Par_1",
  "@type": ["owl:NamedIndividual", "estleg:LegalProvision"],
  "estleg:paragrahv": "§ 1",
  "estleg:hasSanction": [{"@id": "estleg:Sanction_Reg_TLN_15_Map_2020_Par_1_fine"}],
  "estleg:enforcedAtLevel": "municipality"
}
```

SHACL: `LegalProvisionShape` constrains `enforcedAtLevel` with
`sh:datatype xsd:string`, `sh:maxCount 1`, and a closed value set
`sh:in ("state" "municipality")`.

## Integration SPARQL Examples

### Find all provisions that reference a specific paragraph

Karistusseadustik is abbreviated `KARIST_2` in the corpus (its special part is
`KARIST_2_Osa2`), so its § 279 is `estleg:KARIST_2_Osa2_Par_279`:

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?provision ?label WHERE {
  ?provision estleg:references estleg:KARIST_2_Osa2_Par_279 ;
             rdfs:label ?label .
}
```

### Find court decisions interpreting a specific provision
```sparql
PREFIX estleg: <https://w3id.org/estleg/>
SELECT ?decision ?date WHERE {
  ?decision estleg:interpretsLaw estleg:KARIST_2_Osa1_Par_12 ;
            estleg:decisionDate ?date .
} ORDER BY DESC(?date)
```

### Find Estonian laws transposing a specific EU directive (Renewable Energy Directive)
```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?law ?title WHERE {
  ?law estleg:transposesDirective estleg:EU_32009L0028 ;
       rdfs:label ?title .
}
```

### Find all obligations in a specific law
```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?provision ?label WHERE {
  ?provision estleg:normativeType estleg:NormType_Obligation ;
             estleg:sourceAct "Töölepingu seadus" ;
             rdfs:label ?label .
}
```

### Find all acts on a specific EuroVoc topic

`dcterms:subject` is attached at the **act** level (see the Subject
Classification table above), not on individual provisions, so the query
binds `?act a estleg:Act`:

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dcterms: <http://purl.org/dc/terms/>
SELECT ?act ?label WHERE {
  ?act a estleg:Act ;
       dcterms:subject <http://eurovoc.europa.eu/4050> ;
       rdfs:label ?label .
}
```

### Find sanctions for a specific type of conduct

Co-load the enacted-law peep (which carries `estleg:hasSanction` and the
provision's `rdfs:label`) with its `krr_outputs/sanctions/sanctions_<law>.json`
file (which defines the `estleg:Sanction` nodes and their
`sanctionType`/`maxPenalty`):

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?provision ?label ?type ?maxPenalty WHERE {
  ?provision estleg:hasSanction ?sanction ;
             rdfs:label ?label .
  ?sanction estleg:sanctionType ?type ;
            estleg:maxPenalty ?maxPenalty .
} ORDER BY ?type
```

## Integration Scripts

| Script | Purpose | Issues |
|--------|---------|--------|
| `extract_cross_references.py` | Parse law text for cross-law citation links | #29 |
| `generate_inverse_references.py` | Generate bidirectional `referencedBy` links | #30 |
| `generate_transposition_mapping.py` | Map Estonian laws to EU directives they transpose | #31 |
| `extract_court_provision_links.py` | Link court decisions to specific provision IRIs | #32 |
| `generate_amendment_history.py` | Build amendment chain relationships | #33 |
| `generate_similarity_index.py` | Keyword-based semantic similarity between provisions | #34 |
| `extract_legal_concepts.py` | Extract defined terms and build concept graph | #35 |
| `extract_draft_impact.py` | Provision-level draft impact analysis | #36 |
| `classify_eurovoc.py` | Classify laws with EuroVoc subject taxonomy | #37 |
| `extract_temporal_data.py` | Extract entry-into-force and repeal dates | #38 |
| `generate_harmonisation_links.py` | Neighbour-state comparative NIM measures (LV/LT/FI/SE); not Estonian article-level transposition | #39 |
| `classify_deontic.py` | Classify provisions as obligations/rights/permissions/prohibitions | #43 |
| `extract_institutional_competence.py` | Map institutions to their legal competences | #42 |
| `extract_sanctions.py` | Extract penalties and sanctions from law text | #41 |

## Non-graph application indexes

Two computed indexes are **non-graph** application artifacts (issue #462).
They are not RDF; SPARQL over the published graph will not see them.

- **KOV / law similarity JSON** (`krr_outputs/reports/similarity_index.json`,
  `krr_outputs/reports/similarity_report.json`) is a **tf-idf / application index**
  (provision-level keyword-Jaccard in the sidecar; the optional KOV
  act-level pass uses bucketed TF-IDF cosine). SPARQL will not see those
  pairs unless they were also emitted as `estleg:similarAct` (KOV act-level
  peers and directed KOV→enabling-act edges) or, for provision-level
  law/state pairs, as `estleg:semanticallySimilarTo`. Pair scores and the
  bulk of KOV pairs live only in the JSON sidecars.
- **EUR-normalized sanction severity scores** in
  `krr_outputs/reports/sanctions_report.json` (`severity_index`, including
  `monetary_score` / `max_monetary_eur`) are **not** graph properties.
  Query `estleg:hasSanction` for the RDF sanctions layer
  (`estleg:Sanction` nodes with `sanctionType` / `maxPenalty`).

## Data Sources

| Source | URL | Format | Script |
|--------|-----|--------|--------|
| Riigi Teataja | https://www.riigiteataja.ee | XML API | `generate_all_laws.py` |
| Riigi Teataja (määrused) | https://www.riigiteataja.ee | XML API | `generate_regulations.py` |
| EIS | https://eelnoud.valitsus.ee | RSS 2.0 | `generate_draft_legislation.py` |
| RIK | https://rikos.rik.ee | HTML scrape | `generate_court_decisions.py` |
| EUR-Lex | https://eur-lex.europa.eu | SPARQL | `generate_eu_legislation.py` |
| EUR-Lex / CURIA | https://eur-lex.europa.eu | SPARQL | `generate_eu_court_decisions.py` |
| EuroVoc | https://op.europa.eu/en/web/eu-vocabularies | SKOS | `classify_eurovoc.py` |

## KOV Entity Model (Layer 1)

The KOV (kohaliku omavalitsuse) layer adds a Municipality + Issuer entity
model so every municipal regulation and provision is queryable by
territorial unit and issuing body **on the full public load surface** —
i.e. when the `krr_outputs/regulations/kov/` files (and `data/ehak/` for the
municipality/successor registry) are loaded alongside `combined_ontology.jsonld`.
The flagship `combined_ontology.jsonld` does **not** inline the ~116k municipal
provision bodies; it carries KOV regulations only as resolvable cross-corpus
stubs. See the README "Load surfaces" section.

### Class hierarchy

- `estleg:Act` — top-level "any enacted Estonian legal act"
  - `estleg:Law` — statute (seadus); stamped onto every existing law node
  - `estleg:DomesticRegulation` — common superclass for any Estonian *määrus* (never stamped on instances)
    - `estleg:NationalRegulation` — state-level
      - `estleg:GovernmentRegulation`
      - `estleg:MinisterialRegulation`
    - `estleg:MunicipalRegulation` — municipal (KOV); sibling of `NationalRegulation`, not a subclass; `owl:disjointWith NationalRegulation` (#439)
- `estleg:Municipality` — territorial KOV unit (top-level)
- `estleg:Issuer` — `rdfs:subClassOf estleg:Institution`
- `estleg:KovProvision` — `rdfs:subClassOf estleg:LegalProvision`
- `estleg:Citation` — reified preamble citation (Layer 2)
- `estleg:Similarity` — reified act-level similarity link (Layer 3)

### Properties

| Property | Domain | Range | Purpose |
| --- | --- | --- | --- |
| `estleg:enactedBy` | `MunicipalRegulation`, `KovProvision` | `Issuer` | Which body enacted this |
| `estleg:enactedByMunicipality` | `MunicipalRegulation`, `KovProvision` | `Municipality` | Which KOV unit |
| `estleg:titleNormalized` | `MunicipalRegulation` | `xsd:string` | Lowercased, transliterated, prefix-stripped title |
| `estleg:partOfAct` | `KovProvision` | `Act` | IRI link from provision to parent act |
| `estleg:ehakCode` | `Municipality` | `xsd:string` | Four-digit EHAK code; every Municipality also has `rdfs:seeAlso` to the EHAK classifier and curated `owl:sameAs` Wikidata (`data/ehak/municipality_wikidata.json`, #518) |
| `estleg:county` | `Municipality` | `xsd:string` | Maakond |
| `estleg:bodyType` | `Issuer` | `xsd:string` | `"volikogu"` \| `"valitsus"` |
| `estleg:currentMunicipality` | `Issuer` | `Municipality` | Today's successor for legacy issuers |
| `estleg:historicalMunicipalityName` | `Issuer` | `xsd:string` | Issuing-time municipality name |
| `estleg:mappingSource` | `Issuer` | `xsd:string` | How the mapping was derived |
| `estleg:mappingEvidence` | `Issuer` | `xsd:string` | Source citation for curated mappings |

### IRI patterns

- `estleg:Municipality_EHAK_<4-digit>` — Municipality
- `estleg:Issuer_<slug>` — Issuer; slug matches the directory name under `krr_outputs/regulations/kov/`

### Generated files

- `krr_outputs/municipalities_peep.json` — 79 Municipality nodes
- `krr_outputs/issuers_kov_peep.json` — 357 Issuer nodes

### Direct-typing in enriched data

Every act node carries `estleg:Act` rdf:type **directly**, in
addition to its specific subtype (`Law`, `MunicipalRegulation`,
`NationalRegulation`, etc.). Same pattern as `KovProvision` on
provisions. This keeps the data self-describing and means SHACL
constraints on `estleg:Act` (e.g. `partOfAct`'s range) work without
requiring RDFS subclass inference at validation time.

### SHACL shapes

`shacl/estonian_legal_shapes.ttl` enforces required fields and IRI
patterns on `Municipality`, `Issuer`, `MunicipalRegulation` (Layer-1
fields), and `KovProvision`. Layer 2 added a `Citation` shape;
Layer 3 added a `Similarity` shape.

### Example queries

Find all Tallinn-issued regulations (these queries need the opt-in
`regulations/kov/` corpus loaded):

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?reg ?title WHERE {
  ?reg a estleg:MunicipalRegulation ;
       estleg:enactedByMunicipality estleg:Municipality_EHAK_0784 ;
       rdfs:label ?title .
}
```

Compare a normalized title across municipalities:

```sparql
PREFIX estleg: <https://w3id.org/estleg/>

SELECT ?mun ?reg WHERE {
  ?reg a estleg:MunicipalRegulation ;
       estleg:titleNormalized "jaatmehoolduseeskiri" ;
       estleg:enactedByMunicipality ?mun .
}
```

## KOV Layer 2 — Cross-references and Inverse Projection

Layer 2a declared the schema (Citation class, 8 new properties, SHACL
shape, opt-in flag flips). Layer 2b populates the cross-reference and
inverse-projection properties from real preamble text and KOV body-text
references. Layer 2c (sanctions, institutional competence, and
court-provision-links, including the `estleg:enforcedAtLevel` resolver)
shipped in #250 and is **COMPLETE**.

### Classes

- `estleg:Citation` — reified preamble citation. **POPULATED** by Layer
  2b for every act with a parseable preamble.

### Properties

| Property | Domain | Range | Layer | Status | Purpose |
| --- | --- | --- | --- | --- | --- |
| `estleg:citationTarget` | `Citation` | `Act` ∪ `LegalProvision` | 2b | populated | Resolved target (provision when § present, act otherwise) |
| `estleg:citationDetail` | `Citation` | `xsd:string` | 2b | populated | Sub-paragraph specificity (`"lg 1 p 3"`) |
| `estleg:citationText` | `Citation` | `xsd:string` | 2b | populated | Original preamble substring |
| `estleg:issuedUnder` | `Act` | `Act` | 2b | populated | Act → enabling act (act-level only) |
| `estleg:implementsCitation` | `Act` | `Citation` | 2b | populated | Act → reified Citation node |
| `estleg:implementedBy` | `Act` ∪ `LegalProvision` | `Act` | 2b | populated | Filtered inverse — acts citing this as enabling authority |
| `estleg:implementedByCount` | `Act` ∪ `LegalProvision` | `xsd:integer` | 2b | populated | Aggregate count of distinct sources |
| `estleg:enforcedAtLevel` | `LegalProvision` | `xsd:string` | 2c | populated | `"state"` \| `"municipality"` |

### Property semantics and examples

#### estleg:issuedUnder

**Domain:** `estleg:Act` (Law, NationalRegulation, GovernmentRegulation,
MinisterialRegulation, MunicipalRegulation)
**Range:** `estleg:Act` (act-level only — never a `LegalProvision`)
**Cardinality:** many-per-act

Identifies the ACT under whose authority this act was issued. For
municipal regulations, the most common targets are KOKS
(`estleg:KOKS_Map_2026`), state-regulation acts, or other KOV acts.
Provision-level granularity (e.g. "issued under § 22 lg 1 p 34") is
captured separately on `Citation.citationTarget` — see
`estleg:implementsCitation` below.

```json
{
  "@id": "estleg:Reg_1014955_Map_2026",
  "@type": ["estleg:Act", "estleg:MunicipalRegulation"],
  "estleg:issuedUnder": [
    {"@id": "estleg:KOKS_Map_2026"},
    {"@id": "estleg:RaamatPS_Map_2026"}
  ]
}
```

#### estleg:implementsCitation

**Domain:** `estleg:Act`
**Range:** `estleg:Citation`
**Cardinality:** many-per-act

Reified citation node carrying the original preamble substring, the
paragraph/lõige/punkti detail, and the resolved target (provision
when § is present, act otherwise). See `estleg:Citation` below for an
example.

#### estleg:Citation (class)

A reified citation node. Always co-occurs with `estleg:implementsCitation`
on the source act.

**Properties:**
- `estleg:citationTarget` — provision OR act IRI (cardinality 1)
- `estleg:citationDetail` — `"lg 1 p 34"` style literal (optional)
- `estleg:citationText` — original input substring (optional, recommended)
- `estleg:referenceType` — optional `estleg:RefType_*` individual (issue #513 T-Box). Declared so a later emission pass can type preamble citations; **not** populated on existing `Citation` nodes or on unreified `estleg:references` edges yet.

**IRI pattern:** `estleg:Citation_<source-act-shortid>_<seq>` where
`<source-act-shortid>` is the source act's @id minus `estleg:` and
`<seq>` is a 1-based per-act counter.

```json
{
  "@id": "estleg:Citation_Reg_1014955_Map_2026_1",
  "@type": ["owl:NamedIndividual", "estleg:Citation"],
  "estleg:citationTarget": {"@id": "estleg:KOKS_Par_22"},
  "estleg:citationDetail": "lg 1 p 34",
  "estleg:citationText": "kohaliku omavalitsuse korralduse seaduse § 22 lõike 1 punkti 34"
}
```

#### estleg:implementedBy

**Domain:** `estleg:Act` ∪ `estleg:LegalProvision`
**Range:** `estleg:Act` (the implementing act — typically a regulation)
**Cardinality:** many-per-target

Filtered inverse projection of `issuedUnder` and `Citation.citationTarget`.
Body-text references (`estleg:references`) do NOT contribute. The
existing `estleg:referencedBy` continues to serve as the inverse for
body-text references — `implementedBy` is strictly the "what acts cite
me as their enabling authority" view.

```json
{
  "@id": "estleg:KOKS_Par_22",
  "estleg:implementedBy": [
    {"@id": "estleg:Reg_1014955_Map_2026"},
    {"@id": "estleg:Reg_2034567_Map_2024"}
  ],
  "estleg:implementedByCount": 2
}
```

#### estleg:implementedByCount

**Domain:** `estleg:Act` ∪ `estleg:LegalProvision`
**Range:** `xsd:integer`
**Cardinality:** exactly one (when `implementedBy` is present)

Aggregate count of distinct sources, useful for SPARQL ranking queries.

### SHACL

`shacl/estonian_legal_shapes.ttl` enforces `estleg:CitationShape` on
every reified `Citation` node (target Citation; required `citationTarget`;
optional `citationDetail`/`citationText`; IRI pattern).

### Pipeline coverage reports

Each KOV-relevant pipeline emits `krr_outputs/reports/kov/<pipeline>_coverage.json`
with per-run metrics. See `krr_outputs/reports/kov/README.md`. Layer 2b
adds coverage outputs for `extract_cross_references` (preamble + body-text
KOV scope) and `generate_inverse_references` (`implementedBy` filtered
projection).
