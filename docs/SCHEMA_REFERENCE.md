# Schema Reference

## Complete Schema Documentation

### Classes

#### Enacted Law Classes
1. **LegalProvision (`estleg:LegalProvision`)**
   - Represents a specific article, section, or paragraph in an enacted law.
2. **TopicCluster (`estleg:TopicCluster`)**
   - Represents a semantic cluster or theme that legal provisions belong to.
3. **LegalConcept (`estleg:LegalConcept`)**
   - Represents a defined legal concept or term used within the legislation.

#### Draft Legislation Classes (EIS)
4. **DraftLegislation (`estleg:DraftLegislation`)**
   - Represents a legislative draft (eelnõu) that is in the legislative process but not yet enacted.
   - Source: [EIS – Eelnõude infosüsteem](https://eelnoud.valitsus.ee)
5. **LegislativePhase (`estleg:LegislativePhase`)**
   - Represents the current stage of a draft in the legislative pipeline.
   - Phases: `Phase_PublicConsultation`, `Phase_Review`, `Phase_Submission`
6. **DraftType (`estleg:DraftType`)**
   - Classifies the type of draft: bill, regulation, order, etc.
   - Types: `Bill`, `AmendmentBill`, `GovernmentRegulation`, `MinisterialRegulation`, `GovernmentOrder`, `EUPosition`, `DraftIntent`, `ActionPlan`, `Other`

### Properties

#### Enacted Law Properties
* `estleg:identifier`: A unique identifier for the provision.
* `schema:name`: The title or heading of the provision.
* `schema:text`: The actual text of the law.
* `estleg:topicCluster`: Associates a provision with a TopicCluster.
* `estleg:references`: Defines cross-references to other legal provisions or laws.
* `schema:isPartOf`: Indicates the hierarchical structure (e.g., paragraph is part of a section).
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
| `DraftType_GovernmentOrder` | Korralduse eelnõu | Government Order Draft |
| `DraftType_EUPosition` | EL seisukoha eelnõu | EU Position Draft |
| `DraftType_DraftIntent` | Väljatöötamiskavatsus | Draft Intent / Pre-draft |
| `DraftType_ActionPlan` | Tegevuskava | Action Plan |
| `DraftType_Other` | Muu eelnõu | Other Draft |

## JSON-LD Structure Examples

### Enacted Law Example

```json
{
  "@context": {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#"
  },
  "@graph": [
    {
      "@id": "estleg:PS_Par_8",
      "@type": ["owl:NamedIndividual", "estleg:LegalProvision_PS"],
      "estleg:paragrahv": "§ 8",
      "rdfs:label": "§ 8 Kodakondsus",
      "estleg:sourceAct": "Eesti Vabariigi põhiseadus"
    }
  ]
}
```

### Draft Legislation Example

```json
{
  "@context": {
    "estleg": "https://data.riik.ee/ontology/estleg#",
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
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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

### DecisionType (`estleg:DecisionType`)

| Type | Estonian |
|------|----------|
| `DecisionType_Judgment` | Kohtuotsus |
| `DecisionType_Ruling` | Kohtumäärus |
| `DecisionType_Resolution` | Kohtu resolutsioon |

### Court Decision Properties

* `estleg:caseNumber`: Case number (e.g., "3-21-2176/52") — `xsd:string`
* `estleg:caseType`: Links to CaseType individual — `owl:ObjectProperty`
* `estleg:decisionType`: Links to DecisionType individual — `owl:ObjectProperty`
* `estleg:decisionDate`: Date of the decision — `xsd:date`
* `estleg:decisionLink`: URL to full decision on riigikohus.ee — `xsd:anyURI`
* `estleg:rikObjectId`: Internal RIK database object ID — `xsd:string`
* `estleg:referencedLaw`: Law abbreviation referenced in the decision — `xsd:string`
* `estleg:interpretsLaw`: Object property linking to LegalProvision being interpreted — `owl:ObjectProperty`

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
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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
| `estleg:hasCompetence` | `owl:ObjectProperty` | Provisions assigning competence to this institution |

### NormativeType (`estleg:NormativeType`)
Deontic classification of provisions. Individuals: `NormType_Obligation`, `NormType_Right`, `NormType_Permission`, `NormType_Prohibition`.

### Section (`estleg:Section`)
Represents a section (jagu/peatükk) in the KarS special parts structure, generated by `generate_kars_eriosa_jsonld.py`.

| Property | Type | Description |
|----------|------|-------------|
| `estleg:sectionNumber` | `xsd:string` | Section number |

### AmendmentEvent (`estleg:AmendmentEvent`)
Represents an amendment event linking a provision to its amending act or draft. Generated by `generate_amendment_history.py` and stored in `krr_outputs/amendments/`.

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
| `estleg:references` | LegalProvision | LegalProvision (IRI) | Cross-reference to another provision |
| `estleg:referencedBy` | LegalProvision | LegalProvision (IRI) | Inverse: provisions referencing this one |
| `estleg:semanticallySimilarTo` | LegalProvision | LegalProvision (IRI) | Provisions with similar content from other laws |
| `estleg:definesTerm` | LegalProvision | LegalConcept (IRI) | Legal concept defined by this provision |

### Court Decision Links
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:interpretsLaw` | CourtDecision | LegalProvision (IRI) | Specific provision interpreted |
| `estleg:interpretedBy` | LegalProvision | CourtDecision (IRI) | Inverse: court decisions interpreting this provision |

### EU Transposition
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:transposesDirective` | LegalProvision | EULegislation (IRI) | EU directive transposed by this law |
| `estleg:transposedBy` | EULegislation | LegalProvision (IRI) | Inverse: Estonian law transposing this directive |
| `estleg:harmonisedWith` | LegalProvision | EULegislation (IRI) | EU harmonisation requirement |

### Subject Classification
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `dcterms:subject` | LegalProvision | IRI | EuroVoc concept URI (e.g., `http://eurovoc.europa.eu/2411`) |

### Temporal Properties
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:entryIntoForce` | LegalProvision | `xsd:date` | Date when provision became effective |
| `estleg:repealDate` | LegalProvision | `xsd:date` | Date when provision was repealed |
| `estleg:lastAmendmentDate` | LegalProvision | `xsd:date` | Most recent amendment date |
| `estleg:temporalStatus` | LegalProvision | `xsd:string` | Status: inForce, repealed, notYetEffective |

### Amendment Properties
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:amendedBy` | LegalProvision | IRI | Amending act or draft |
| `estleg:amends` | DraftLegislation | LegalProvision (IRI) | Inverse: what this draft amends |
| `estleg:changeType` | DraftLegislation | `xsd:string` | Type of change: amends, repeals, supplements, enacts |
| `estleg:affectedBy` | LegalProvision | DraftLegislation (IRI) | Pending drafts affecting this provision |

### Normative Classification
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:normativeType` | LegalProvision | NormativeType (IRI) | Obligation, right, permission, prohibition |
| `estleg:dutyHolder` | LegalProvision | `xsd:string` | Who must comply (e.g., "tööandja") |

### Institutional Competence
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:competentAuthority` | LegalProvision | Institution (IRI) | Responsible institution |
| `estleg:competenceType` | Institution | `xsd:string` | Type: supervision, licensing, enforcement |

### Sanctions
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:hasSanction` | LegalProvision | Sanction (IRI) | Sanction defined by this provision |
| `estleg:sanctionType` | Sanction | `xsd:string` | imprisonment, fine, etc. |
| `estleg:maxPenalty` | Sanction | `xsd:string` | Maximum penalty value |

## Integration SPARQL Examples

### Find all provisions that reference a specific paragraph
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?provision ?label WHERE {
  ?provision estleg:references estleg:KarS_Par_121 ;
             rdfs:label ?label .
}
```

### Find court decisions interpreting a specific provision
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
SELECT ?decision ?date WHERE {
  ?decision estleg:interpretsLaw estleg:VOS_Par_208 ;
            estleg:decisionDate ?date .
} ORDER BY DESC(?date)
```

### Find Estonian laws transposing a specific EU directive (Whistleblower Directive)
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?law ?title WHERE {
  ?law estleg:transposesDirective estleg:EU_32019L1937 ;
       rdfs:label ?title .
}
```

### Find all obligations in a specific law
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?provision ?label WHERE {
  ?provision estleg:normativeType estleg:NormType_Obligation ;
             estleg:sourceAct "Töölepingu seadus" ;
             rdfs:label ?label .
}
```

### Find all provisions on a specific EuroVoc topic across all laws
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dcterms: <http://purl.org/dc/terms/>
SELECT ?provision ?law ?label WHERE {
  ?provision dcterms:subject <http://eurovoc.europa.eu/2826> ;
             estleg:sourceAct ?law ;
             rdfs:label ?label .
}
```

### Find sanctions for a specific type of conduct
```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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
| `generate_harmonisation_links.py` | Cross-border EU harmonisation links | #39 |
| `classify_deontic.py` | Classify provisions as obligations/rights/permissions/prohibitions | #43 |
| `extract_institutional_competence.py` | Map institutions to their legal competences | #42 |
| `extract_sanctions.py` | Extract penalties and sanctions from law text | #41 |

## Data Sources

| Source | URL | Format | Script |
|--------|-----|--------|--------|
| Riigi Teataja | https://www.riigiteataja.ee | XML API | `generate_all_laws.py` |
| EIS | https://eelnoud.valitsus.ee | RSS 2.0 | `generate_draft_legislation.py` |
| RIK | https://rikos.rik.ee | HTML scrape | `generate_court_decisions.py` |
| EUR-Lex | https://eur-lex.europa.eu | SPARQL | `generate_eu_legislation.py` |
| EUR-Lex / CURIA | https://eur-lex.europa.eu | SPARQL | `generate_eu_court_decisions.py` |
| EuroVoc | https://op.europa.eu/en/web/eu-vocabularies | SKOS | `classify_eurovoc.py` |
