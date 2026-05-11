# Schema Reference

## Complete Schema Documentation

> **Note on terminology.** "Regulation" is overloaded across legal systems. In this ontology **domestic regulations** (Estonian *määrused*, modelled as `estleg:NationalRegulation` and its subclasses) are kept strictly separate from **EU regulations** (modelled as `estleg:EULegislation` with `estleg:euDocumentType estleg:EUDocType_Regulation`). Domestic regulations are issued under an enabling Estonian law by Vabariigi Valitsus, a minister, or a municipal council; EU regulations are EU-level legal acts. Use the dedicated classes for each — see "Domestic Regulation Classes" and "EU Legislation Classes" below.

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
* `rdfs:label`: The title or heading of the provision (language-tagged Estonian/English literal). Replaces the legacy `schema:name`; current generators emit `rdfs:label` and SHACL validates it via `LegalProvisionShape`.
* `estleg:summary`: Free-text summary of the provision (language-tagged Estonian/English literal). Replaces the legacy `schema:text`; current generators emit `estleg:summary`.
* `estleg:topicCluster`: Associates a provision with a TopicCluster.
* `estleg:references`: Defines cross-references to other legal provisions or laws.
* `dcterms:isPartOf`: Indicates the hierarchical structure (e.g., paragraph is part of a section). Replaces the legacy `schema:isPartOf`; pipelines that reference parent acts use `estleg:partOfAct` (KOV) or `estleg:sourceAct` (state laws).
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
* `estleg:interpretsLaw`: Object property linking to a `LegalProvision` (state-law provision) OR a `MunicipalRegulation` / `Act` (KOV act-level citation) interpreted by this decision — `owl:ObjectProperty`. Range is `owl:unionOf (LegalProvision Act)` since Layer 2c PR #3.

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

## Domestic Regulation Classes

Estonian domestic regulations (*määrused*) are subordinate legal acts issued under an enabling law. They are produced by `scripts/generate_regulations.py` from Riigi Teataja and stored under `krr_outputs/regulations/riik/` (state-level) and, in Phase 2, `krr_outputs/regulations/kov/` (municipal). Provisions of regulations are subclasses of `estleg:LegalProvision` and therefore reuse the existing provision-level shapes, queries, and integrations.

### NationalRegulation (`estleg:NationalRegulation`)
Base class for any Estonian domestic regulation. Every concrete regulation instance carries this type plus exactly one of the more specific subclasses below.

### GovernmentRegulation (`estleg:GovernmentRegulation`)
Regulation issued by Vabariigi Valitsus (the Government of the Republic). Subclass of `NationalRegulation`.

### MinisterialRegulation (`estleg:MinisterialRegulation`)
Regulation issued by an individual minister (e.g. *Sotsiaalminister*, *Justiitsminister*). Subclass of `NationalRegulation`.

### MunicipalRegulation (`estleg:MunicipalRegulation`)
Regulation issued by a local government council or government (KOV). Subclass of `NationalRegulation`; generated KOV regulation act nodes carry both `NationalRegulation` and `MunicipalRegulation` so common domestic-regulation SHACL constraints apply without OWL inference.

### Annex (`estleg:Annex`)
Represents an annex (*lisa*) attached to a regulation. Annexes are emitted as separate named individuals and linked from the regulation via `estleg:hasAnnex`. Annex tables are not normalised in the first pass — the node carries the title, number, and a link to the original document on riigiteataja.ee.

### Per-Regulation Provision Classes (`estleg:Regulation_<terviktekstId>`)
For each regulation, the generator emits one provision class named `estleg:Regulation_<terviktekstId>` (e.g. `estleg:Regulation_160748`) that is declared as `rdfs:subClassOf estleg:LegalProvision`. Provision instances of that regulation carry both the per-regulation class and `estleg:paragrahv`, so they are validated by the existing `LegalProvisionShape`.

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
    "estleg": "https://data.riik.ee/ontology/estleg#",
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
      "estleg:issuer": {"@value": "Vabariigi Valitsus", "@language": "et"},
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
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?reg ?title ?actNumber ?date WHERE {
  ?reg a estleg:GovernmentRegulation ;
       rdfs:label ?title ;
       estleg:issuer "Vabariigi Valitsus"@et ;
       estleg:actNumber ?actNumber ;
       estleg:entryIntoForce ?date .
} ORDER BY DESC(?date)
```

### SPARQL: Find regulations whose preamble cites a specific law

```sparql
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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
| `estleg:interpretsLaw` | CourtDecision | LegalProvision \| MunicipalRegulation/Act (IRI) | Specific provision (state law) or whole act (KOV) interpreted. `owl:unionOf` since Layer 2c PR #3. |
| `estleg:interpretedBy` | LegalProvision \| MunicipalRegulation (informal) | CourtDecision (IRI) | Inverse of `interpretsLaw`. The object is always a `CourtDecision`; the subject is a `LegalProvision` for state-law citations, and may also be a `MunicipalRegulation` act node for KOV act-level citations (since Layer 2c PR #3). No formal `rdfs:range` is declared (range stays unconstrained beyond `sh:nodeKind sh:IRI`); SHACL `MunicipalRegulationShape` adds a parallel `sh:nodeKind sh:IRI` constraint to document the new subject usage. |

### EU Transposition
| Property | Domain | Range | Description |
|----------|--------|-------|-------------|
| `estleg:transposesDirective` | Act | EULegislation (IRI) | EU directive transposed by this law |
| `estleg:transposedBy` | EULegislation | Act (IRI) | Inverse: Estonian law transposing this directive |
| `estleg:harmonisedWith` | LegalProvision | EULegislation (IRI) | EU harmonisation requirement |

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
| `estleg:temporalStatus` | Act | `xsd:string` | Status evaluated against the build's declared temporal evaluation date: inForce, repealed, notYetEffective |

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
| `estleg:competentAuthority` | LegalProvision | Institution or Issuer (IRI) | Responsible authority — `Institution_*` for state/general detections, `Issuer_*` for KOV-bound matches (see KOV subsection below) |
| `estleg:competenceType` | Institution | `xsd:string` | Type: supervision, licensing, enforcement |

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
| Riigi Teataja (määrused) | https://www.riigiteataja.ee | XML API | `generate_regulations.py` |
| EIS | https://eelnoud.valitsus.ee | RSS 2.0 | `generate_draft_legislation.py` |
| RIK | https://rikos.rik.ee | HTML scrape | `generate_court_decisions.py` |
| EUR-Lex | https://eur-lex.europa.eu | SPARQL | `generate_eu_legislation.py` |
| EUR-Lex / CURIA | https://eur-lex.europa.eu | SPARQL | `generate_eu_court_decisions.py` |
| EuroVoc | https://op.europa.eu/en/web/eu-vocabularies | SKOS | `classify_eurovoc.py` |

## KOV Entity Model (Layer 1)

The KOV (kohaliku omavalitsuse) layer adds a Municipality + Issuer entity
model so every municipal regulation and provision is queryable by
territorial unit and issuing body.

### Class hierarchy

- `estleg:Act` — top-level "any enacted Estonian legal act"
  - `estleg:Law` — statute (seadus); stamped onto every existing law node
  - `estleg:NationalRegulation`
    - `estleg:GovernmentRegulation`
    - `estleg:MinisterialRegulation`
  - `estleg:MunicipalRegulation`
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
| `estleg:ehakCode` | `Municipality` | `xsd:string` | Four-digit EHAK code |
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
fields), and `KovProvision`. Layer 2 will add a `Citation` shape;
Layer 3 will add a `Similarity` shape.

### Example queries

Find all Tallinn-issued regulations:

```sparql
SELECT ?reg ?title WHERE {
  ?reg a estleg:MunicipalRegulation ;
       estleg:enactedByMunicipality estleg:Municipality_EHAK_0784 ;
       rdfs:label ?title .
}
```

Compare a normalized title across municipalities:

```sparql
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
references. Layer 2c (sanctions enforcement level) remains pending.

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
  "@type": ["owl:Ontology", "estleg:Act", "estleg:MunicipalRegulation"],
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
