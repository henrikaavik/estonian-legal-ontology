<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# T-Box & standards alignment review — estleg

Reviewer scope: `krr_outputs/controlled_vocabulary.jsonld` (the canonical T-Box), the
FRBR/ActExpression layer, the hand-built OWL modules, and a sample of instance data
across every subcorpus. Lens: interoperability with the standards Estonian and EU
public bodies already run on (ELI, ELI-DL, ECLI, EuroVoc, SKOS, DCAT-AP, CPOV/Core
Vocabularies, Akoma Ntoso, PROV-O, schema.org).

---

## Scope & method

Files read deeply:

| File | What I did |
|---|---|
| `krr_outputs/controlled_vocabulary.jsonld` (8.6k lines, 417 nodes) | Enumerated every class, property and named individual with Python; audited `rdfs:subClassOf` / `rdfs:subPropertyOf` / `owl:equivalentProperty` / `skos:exactMatch` targets, domain/range coverage, language tags, prefix declarations. |
| `docs/SCHEMA_REFERENCE.md`, `docs/NAMESPACE_MIGRATION.md`, `docs/EUROVOC_OVERLAY.md`, `docs/ARCHITECTURE.md`, `AGENTS.md` | Read in full for the documented intent behind each modelling decision, so I do not flag deliberate trade-offs as bugs. |
| `src/estleg/generate_act_expressions_608.py`, `align_owl_modules.py`, `link_owl_modules_565.py`, `generate_schemas_from_cv.py` | Read the FRBR-expression derivation and the module-bridging logic. |
| `krr_outputs/act_expressions_combined.jsonld` (10,310 nodes), `karistusseadustik_eriosa_owl.jsonld`, `tsus_osa7_138_169_owl.jsonld`, `transposition_schema.json`, `eurovoc_concept_scheme.jsonld`, `void.ttl`, `metadata.jsonld` | Head + full property census. |
| Instance samples: `abipolitseiniku_seadus_peep.json`, `regulations/riik/2024_2025_…_t1057801_peep.json`, `riigikohus/riigikohus_1994_peep.json` + `_2016_`, `kohtud/kohtud_sample_peep.json`, `eurlex/eurlex_decisions_peep.json`, `eelnoud/eelnoud_publicconsultation_peep.json`, `institutions/*.json`, `issuers_kov_peep.json`, `municipalities_peep.json` | Property census per subcorpus; compared what the T-Box declares against what the A-Box actually emits. |
| `shacl/estonian_legal_shapes.ttl` (44 node shapes) | Counted constrained property paths by namespace. |

Corpus-wide measurements (scripted, not sampled unless noted):

| Measure | Value |
|---|---|
| Classes in CV | 56 |
| Classes with any external `rdfs:subClassOf` | 7 |
| Properties in CV | 226 |
| Properties with any external mapping | 6 (all `rdfs:subPropertyOf`) |
| `owl:equivalentClass` / `owl:equivalentProperty` / `skos:exactMatch` in CV | 0 |
| `rdfs:comment` that are language-tagged | 14 of 298 present; 119 terms have no comment at all |
| SHACL property paths in the `eli:` namespace | 0 of 241 |
| `eli:` predicates used anywhere in the A-Box | 2 (`eli:is_about` ×1,121, `eli:id_local` on EU acts) |
| Root law `estleg:Act` nodes | 1,146 |
| …of which carry a Riigi Teataja globaalID (in `dcterms:source`) | 984 (85.9%) |
| Regulation `estleg:Act` nodes carrying a globaalID | 3,812 of 3,812 (100%) |
| Acts with a `riigiteataja.ee/eli/…` link | 0 |
| Acts with `estleg:officialEnglishText` → `riigiteataja.ee/en/eli/{id}` | 405 |
| Riigikohus `CourtDecision` nodes | 12,104; 2,903 (24%) carry `estleg:ecliIdentifier` |
| `kohtud/` (maakohus/halduskohus/ringkonnakohus) decisions with ECLI | 0 |
| `dcterms:language` anywhere in the A-Box | 0 |
| Act roots typed `schema:Legislation` in the A-Box | 0 |
| `estleg:targetGroup` (declared `owl:DatatypeProperty`, range `xsd:string`) used with an IRI object | 45,546 occurrences in a 600-file sample |
| `estleg:` predicates used in the A-Box but absent from the CV | 2 (`estleg:citationSource` ×3,191, `estleg:itemNumber` ×1,672) |

---

## Standards mapping table

Legend for "Mapped?": **Y** = external mapping present in the CV; **P** = partial
(mapped but not populated in the A-Box, or mapped only one way); **N** = no mapping.

### Work / Expression / Manifestation layer

| estleg term | Closest standard term | Mapped? | Recommendation |
|---|---|---|---|
| `estleg:Act` (CV:37) | `eli:LegalResource`, `schema:Legislation` | **Y** | Correct. But add `eli:LegalResourceSubdivision`-free, and start emitting the two extra `@type`s in the A-Box (currently 0 acts carry `schema:Legislation`). |
| `estleg:Law` | `eli:LegalResource` + `eli:type_document` = `<…/authority/resource-type/ACT_LEG>` | **P** (via Act) | Reify the act-kind distinction with `eli:type_document` into the EU Publications Office resource-type authority list instead of the class split alone. |
| `estleg:ActExpression` | `eli:LegalExpression` | **Y** | Add `eli:realizes` and `eli:language` **in the A-Box** (see risk W1). |
| `estleg:ProvisionVersion` | `eli:LegalExpression` | **Y** | Same. Also `eli:version_date`. |
| — (no class) | `eli:Format` / `eli:LegalResourceManifestation` | **N** | The RT XML at `dcterms:source` **is** the Manifestation. Model it as an `eli:Format` node with `eli:media_type "application/xml"` rather than a bare `dcterms:source` URL. |
| `estleg:expressionOf` (CV:3045) | `eli:realizes` | **Y** (`rdfs:subPropertyOf`) | Assert `eli:realizes` directly too, because the production SPARQL surface runs with inference disabled. |
| `estleg:hasExpression` | `eli:is_realized_by` | **N** | Add `rdfs:subPropertyOf eli:is_realized_by`. Trivial. |
| `estleg:consolidationDate` (CV:2037) | `eli:version_date` | **N** | Add `rdfs:subPropertyOf eli:version_date`. |
| `estleg:versionValidFrom` / `versionValidTo` | `eli:first_date_entry_in_force` / `eli:date_no_longer_in_force` | **N** | Map both. |
| `estleg:supersedesExpression` / `supersededByExpression` | `eli:consolidates` / `eli:consolidated_by` (or `dcterms:replaces` / `isReplacedBy`) | **N** | Map to `dcterms:replaces` / `dcterms:isReplacedBy` at minimum — those are already in the CV's context. |

### Identifiers

| estleg term | Closest standard term | Mapped? | Recommendation |
|---|---|---|---|
| Act identity (no property) | Riigi Teataja ELI URI `https://www.riigiteataja.ee/eli/{globaalID}` | **N** | **Highest-value gap.** 984 laws + 3,812 regulations already carry the globaalID in `dcterms:source`. Mint `owl:sameAs` (or `eli:id_local` + `rdfs:seeAlso`) to the RT ELI URI. The project already uses this exact URI shape for 405 English translations. |
| `estleg:officialEnglishText` (⊑ `rdfs:seeAlso`) | The English `eli:LegalExpression` | **P** | The value **is** an ELI URI (`riigiteataja.ee/en/eli/…`). Demoting it to `rdfs:seeAlso` throws away the FRBR fact: it is the `eli:LegalExpression` of the same Work with `eli:language <…/authority/language/ENG>`. |
| `estleg:eliIdentifier` (CV:2676, EU acts only) | `eli:id_local` / the ELI URI itself | **P** | Works on EU acts (`owl:sameAs` to both CELEX and ELI is already emitted — good). Extend the same pattern to Estonian acts. |
| `estleg:celexNumber` | `eli:id_local` on the CELEX manifestation | **Y**-ish (via `owl:sameAs` to `publications.europa.eu/resource/celex/…`) | Fine as-is. |
| `estleg:ecliIdentifier` (CV:2557) | ECLI (Council 2011/C 127/01) | **P** | SHACL enforces the syntax (`shacl/estonian_legal_shapes.ttl:890`) and `rdfs:seeAlso` to the e-Justice resolver is emitted. But only 24% of Riigikohus decisions carry it and `kohtud/` carries none. |
| `estleg:ehakCode` | Statistics Estonia EHAK classification | **P** | `rdfs:seeAlso` to `metaweb.stat.ee` is emitted on `Municipality` nodes. Good. Consider `dcterms:spatial` too. |
| `estleg:eisNumber` / `eisLink` | ELI-DL draft identifier | **N** | No standard identifier exists for EIS; keep, but add `eli-dl` typing (below). |
| `estleg:globalId` (CV) | RT globaalID | **N** | Declared but **never emitted on root law acts** (0 of 1,146); only regulations use it. Inconsistent. |

### Structure (Akoma Ntoso / ELI subdivisions)

| estleg term | Closest standard term | Mapped? | Recommendation |
|---|---|---|---|
| `estleg:LegalProvision` (§ / paragrahv) | `eli:LegalResourceSubdivision`; AKN `<article>` | **Y** | Correct. |
| `estleg:Subsection` (lõige) | `eli:LegalResourceSubdivision`; AKN `<paragraph>` | **Y** (via LegalProvision) | Correct. |
| `estleg:Part` (CV:764) / `estleg:LegalPart` (CV:610) (osa) | `eli:LegalResourceSubdivision`; AKN `<part>` | **N** | Two classes for one concept — `Part` is "multipart act **file** split", `LegalPart` is the real osa. A file-partitioning artefact has leaked into the published vocabulary. |
| `estleg:Chapter` (peatükk) | `eli:LegalResourceSubdivision`; AKN `<chapter>` | **N** | Add `rdfs:subClassOf eli:LegalResourceSubdivision`. |
| `estleg:Division` (CV:266, label "Jagu") | AKN `<section>` | **N** | Label says *Jagu*, comment says "(jagu / jaotis)" — conflates two levels. |
| `estleg:Section` (CV:927, label "Jaotis") | AKN `<subsection>`? | **N** | Label says *Jaotis*, comment says "A structural section (**jagu**)". Contradicts `Division`. And it is `rdfs:subClassOf estleg:LegalProvision`, so a *container* is entailed to be a *provision*. |
| `estleg:Subdivision` (CV:964, label "Alljaotis") | — | **N** | "Alljaotis" is not a standard Riigi Teataja drafting level. |
| punkt / alapunkt | AKN `<point>` / `<subpoint>` | **N** | No class. `estleg:itemNumber` is emitted 1,672 times on `Subsection` nodes but is **not declared in the CV at all**. Estonian citations routinely go to punkt level ("§ 14 lg 1 p 7"). |
| `estleg:isPartOf` / `estleg:partOfAct` (CV:4098, 4867) | `eli:is_part_of` / `eli:has_part` | **N** | Add `rdfs:subPropertyOf eli:is_part_of`. `partOfAct` is a `FunctionalProperty` — the transitive `isPartOf` should map, the functional shortcut can stay estleg-local. |
| `estleg:Annex` | AKN `<attachment>`; `eli:LegalResourceSubdivision` | **N** | Map. |

### Drafts (ELI-DL)

| estleg term | Closest standard term | Mapped? | Recommendation |
|---|---|---|---|
| `estleg:DraftLegislation` | `eli-dl:DraftLegislationWork` | **Y** | Correct — but the A-Box emits **zero** `eli-dl:` predicates. |
| `estleg:LegislativePhase` individuals | `eli-dl:ProcessStage` | **Y** | Individuals are dual-typed `eli-dl:ProcessStage` — good. But `estleg:legislativePhase` (the property linking draft→phase) has no mapping to `eli-dl:` process vocabulary. |
| `estleg:draftType` | `eli-dl:DraftLegislationWork` subtypes | **N** | 12 local `DraftType` individuals with no external anchor. |
| `estleg:initiator` (`xsd:string`) | `eli-dl:has_initiator` → an organisation IRI | **N** | Currently a bare ministry name string ("Justiitsministeerium"). The corpus already has `estleg:Institution_*` nodes for ministries — join them. |
| `estleg:publicationDate` on drafts | `eli-dl:date_publication` / consultation dates | **N** | No consultation start/end dates at all. |

### Institutions / organisations

| estleg term | Closest standard term | Mapped? | Recommendation |
|---|---|---|---|
| `estleg:Institution` (CV:516) | `cpov:PublicOrganisation` (`http://data.europa.eu/m8g/PublicOrganisation`), `org:Organization`, `foaf:Organization` | **N** | Not typed as any standard organisation class. A ministry consuming this cannot join it to the Core Public Organisation Vocabulary. |
| `estleg:Issuer` | `org:Organization` / CPOV | **N** | Same. |
| `estleg:institutionType` (CV:3852, `xsd:string`) | A SKOS scheme; CPOV `org:classification` | **N** | 8 free-string values (`ministry`, `agency`, `minister`, `court`, `local_government`, `parliament`, `head_of_state`, `government`). Should be SKOS concepts. |
| Institution `owl:sameAs` | Wikidata | **Y** | 77 of 117 (66%) linked to Wikidata. Genuinely good. No link to any Estonian state institution register or registrikood. |
| `estleg:EUInstitution` (66 individuals) | Publications Office corporate-body authority list (`http://publications.europa.eu/resource/authority/corporate-body/*`) | **N** | The `estleg:euInstitutionCode` values (`ESTAT`, `EUROPOL`, `TAXUD`, `COR`, `EESC`…) **are** the authority-list codes. A one-line `owl:sameAs` per individual would link 66 nodes to the canonical EU register. |
| `estleg:Municipality` | `dcterms:Location`, EHAK | **P** | `rdfs:seeAlso` to `metaweb.stat.ee`. Good. |
| `estleg:enactedBy` (OP → Issuer) / `estleg:issuer` (CV:4215, `xsd:string`) | `eli:passed_by` / `eli:responsibility_of` | **N**, and duplicated | Two properties for one fact; the string form is what regulations actually emit. Map the object form to `eli:passed_by` and deprecate the string. |
| `estleg:competentAuthority` / `estleg:governs` | `eli:responsibility_of`; CPSV-AP `cv:hasCompetentAuthority` | **N** | This is the join a public-service catalogue (CPSV-AP) would use. Worth mapping explicitly. |

### Concepts / thesauri

| estleg term | Closest standard term | Mapped? | Recommendation |
|---|---|---|---|
| `estleg:TargetGroup`, `estleg:TemporalStatus` | `skos:Concept` | **Y** | Correct, with `skos:notation`, `inScheme`, `topConceptOf`. Model citizens of the T-Box. |
| `estleg:Concept` (CV:205), `estleg:LegalConcept`, `estleg:TopicCluster` (CV:1041), `estleg:GeneralPartConcept` | `skos:Concept` | **N** | Instances **are** dual-typed `skos:Concept` in the A-Box, but the classes are not `rdfs:subClassOf skos:Concept`. Inconsistent with how TargetGroup is done. |
| `estleg:NormativeType`, `estleg:CaseType`, `estleg:DecisionType`, `estleg:DraftType`, `estleg:ReferenceType`, `estleg:EUDocumentType`, `estleg:EUCourtDecisionType`, `estleg:LegislativePhase` | `skos:Concept` + `skos:ConceptScheme` | **N** | 8 controlled-value classes with named individuals but no SKOS typing and no scheme. Two of the three existing schemes are done properly — extend the pattern. |
| `dcterms:subject` → EuroVoc | EuroVoc | **Y** | 1,121 acts subject-indexed against real EuroVoc IRIs, plus `eli:is_about`. Genuinely strong. |
| `estleg:EuroVocDomainScheme` | — | **risk** | Asserts `skos:inScheme` / `skos:hasTopConcept` / `skos:prefLabel` **onto 43 EuroVoc concept IRIs** the project does not own (`krr_outputs/eurovoc_concept_scheme.jsonld`). See W5. |
| `estleg:targetGroup` vs `targetGroupConcept`, `estleg:belongsToCluster` vs `topicCluster`, `estleg:relatesToConcept` vs `coversConcept`, `estleg:temporalStatus` vs `TemporalStatus_*` | — | — | Four string/IRI pairs for the same fact. Doubles the query surface. |

### Provenance / catalogue

| estleg term | Closest standard term | Mapped? | Recommendation |
|---|---|---|---|
| Dataset node | DCAT-AP, VoID | **Y** | `metadata.jsonld` + `krr_outputs/void.ttl` are well built: `dcat:theme`, `dcat:keyword`, `dcat:contactPoint`, `dcterms:accrualPeriodicity`, `dcterms:spatial`, EU language authority URI, eight declared `void:Linkset`s. Above average for a project this size. |
| `prov:wasGeneratedBy` | PROV-O | **P** | Exactly one triple, dataset-level only. Deliberate (documented as #456) and reasonable. |
| `estleg:assertionConfidence` | PROV-O qualified attribution | **P** | Per-node confidence exists but is not tied to a `prov:Activity`, so a consumer cannot tell *which* classifier produced a given `targetGroup`. |
| `dcterms:conformsTo` | DCAT-AP profile IRI | **P** | Points at `https://w3id.org/estleg/vocabulary` only. No `dcterms:conformsTo` for DCAT-AP itself or the Estonian open-data profile. |

---

## Strengths

1. **The FRBR spine is right, and it is documented.** `Act` ⊑ `eli:LegalResource`;
   `ActExpression` and `ProvisionVersion` ⊑ `eli:LegalExpression`; `LegalProvision` ⊑
   `eli:LegalResourceSubdivision`. That is the correct ELI layering, and the
   `estleg:expressionOf` ⊑ `eli:realizes` edge points the right way (Expression →
   Work). `generate_act_expressions_608.py` explains in its docstring exactly why the
   act-level Expression node was needed, and it derives 10,309 of them
   deterministically from real Riigi Teataja redaction dates.

2. **EU-side linking is genuinely good.** Every `estleg:EULegislation` node carries
   `owl:sameAs` to both the CELEX Cellar resource and the ELI URI, plus `eli:id_local`,
   `dcterms:title` and a EUR-Lex link. This is exactly what a Publications Office
   consumer needs. The Estonian side should be brought up to this standard.

3. **EuroVoc subject indexing is real, not decorative.** 1,121 acts carry
   `dcterms:subject` **and** `eli:is_about` against canonical `eurovoc.europa.eu`
   concept IRIs, and `docs/EUROVOC_OVERLAY.md` describes a clean overlay contract that
   survives peep regeneration.

4. **The disjointness and hierarchy decisions are careful and justified.**
   `MunicipalRegulation` as a **sibling** of `NationalRegulation` under
   `DomesticRegulation` (not a subclass) is the legally correct call and is documented
   as a corrected error. `AmendmentEvent` `owl:disjointWith` `ProposedAmendment` is
   exactly the distinction ELI-DL cares about. `HistoricalMunicipality` deliberately
   *not* being a subclass of `Municipality`, with the reason recorded in the
   `rdfs:comment`, is the kind of thing external ontologists rarely see done well.

5. **Catalogue and provenance metadata are above the norm.** `metadata.jsonld` and
   `void.ttl` carry bilingual titles and descriptions, the EU language authority URI,
   an explicit rights statement separating the compilation licence from the underlying
   legal texts, and eight typed `void:Linkset` declarations. A ministry data steward
   can assess this dataset without reading the code.

6. **Bilingual labels on the T-Box.** Every one of the 56 classes and 226 properties
   carries `@et` + `@en` `rdfs:label`, minted by a dedicated script. That is the single
   most important thing for an external Estonian reviewer, and it is done.

---

## Weaknesses / risks (severity)

**W1 — CRITICAL: every ELI mapping is invisible on the production query surface.**
All six external property mappings and all seven external class mappings are
`rdfs:subPropertyOf` / `rdfs:subClassOf`. `docs/ARCHITECTURE.md` states that the
Seadusloome SPARQL path loads the full public RDF with **`inference=none`**, and
`scripts/validate_seadusloome_sync.py` mirrors that. Without RDFS entailment,
`?x a eli:LegalResource` returns **zero rows**, `?act eli:realizes ?w` returns zero
rows, and `eli:date_entry_in_force` does not exist. Corpus-wide there are exactly two
`eli:` predicates asserted in the A-Box (`eli:is_about`, `eli:id_local`). An ELI-aware
public-sector client sees an entirely `estleg:`-shaped graph. The mapping layer is real
in the T-Box and inert in practice.

**W2 — CRITICAL: no Estonian ELI identifier anywhere, despite the data being present.**
Zero `riigiteataja.ee/eli/` links in `krr_outputs/`. Yet 984 of 1,146 root laws and
3,812 of 3,812 regulations carry the RT globaalID inside their `dcterms:source` URL
(`https://www.riigiteataja.ee/akt/106072023009.xml`), and the project **already emits**
`https://www.riigiteataja.ee/en/eli/{tolkeSeosId}` for 405 English translations via
`estleg:officialEnglishText`. `docs/SCHEMA_REFERENCE.md` justifies the gap with
"Estonia has no registered ELI URI template" — that claim is contradicted by the
project's own English-translation property. Consequence: nothing joins estleg to Riigi
Teataja's own identifiers, which is the first thing any Estonian public body will try.

**W3 — HIGH: `estleg:targetGroup` is an OWL DL violation at 45k+ occurrences.**
CV:5942 declares it `owl:DatatypeProperty` with `rdfs:range xsd:string`, while
`shacl/estonian_legal_shapes.ttl:371-382` declares `sh:nodeKind sh:IRI` with an
`sh:in` list of `estleg:TargetGroup_*` individuals — and the A-Box follows SHACL. I
counted 45,546 IRI-valued uses in a 600-file sample. The T-Box and the SHACL layer
directly contradict each other. Any OWL reasoner rejects the graph; any consumer that
trusts `rdfs:range` will mis-parse it. The parallel `estleg:targetGroupConcept` (the
"reified SKOS counterpart") makes this worse, not better: the same fact is asserted
twice under two names, one of which is mistyped.

**W4 — HIGH: structural-unit modelling does not survive contact with Akoma Ntoso.**
Four classes cover roughly two Estonian levels, with contradictory labels:
`estleg:Division` is labelled *Jagu* but commented "(jagu / jaotis)" (CV:266);
`estleg:Section` is labelled *Jaotis* but commented "A structural section (**jagu**)"
(CV:927); `estleg:Subdivision` is labelled *Alljaotis*, which is not a Riigi Teataja
drafting level (CV:964). `estleg:Section` is additionally `rdfs:subClassOf
estleg:LegalProvision`, so a structural container is entailed to be a provision (and
thus an `eli:LegalResourceSubdivision` carrying normative text). Meanwhile the punkt
level, which Estonian citations use constantly ("§ 14 lg 1 p 7"), has **no class**,
even though `estleg:itemNumber` is emitted 1,672 times. `estleg:Part` vs
`estleg:LegalPart` splits one concept into a real osa and a file-partitioning artefact.

**W5 — HIGH: the project asserts into EuroVoc's namespace.**
`krr_outputs/eurovoc_concept_scheme.jsonld` declares `skos:hasTopConcept` from
`estleg:EuroVocDomainScheme` onto 43 `http://eurovoc.europa.eu/*` IRIs, and asserts
`skos:inScheme estleg:EuroVocDomainScheme` plus a local bilingual `skos:prefLabel` on
each. `krr_outputs/tsus_osa7_138_169_owl.jsonld` does the same inline with *untagged*
English prefLabels (`{"@id": "http://eurovoc.europa.eu/523", "skos:prefLabel": "civil
law"}`). SKOS requires at most one `skos:prefLabel` per concept per language tag —
merging this graph with real EuroVoc produces either duplicate untagged prefLabels or,
where a local label has drifted from the authoritative one, a SKOS integrity violation
on a Publications Office resource. A ministry that federates this into a EuroVoc-backed
triplestore will see the conflict, not the project.

**W6 — HIGH: institutions cannot join to Core Public Organisation or any state register.**
`estleg:Institution` (CV:516) has no `rdfs:subClassOf` at all. It is not
`cpov:PublicOrganisation`, not `org:Organization`, not `foaf:Organization`.
`estleg:institutionType` is a free `xsd:string` over 8 values. There is no registrikood,
no state-institution-register identifier, no `foaf:homepage`, no `dcterms:spatial`. The
66 `estleg:EUInstitution` individuals carry codes that **are** Publications Office
corporate-body authority codes (`ESTAT`, `EUROPOL`, `TAXUD`, `COR`, `EESC`) but no
`owl:sameAs`. Wikidata linking on 77 of 117 Estonian institutions is good, but Wikidata
is not what a ministry's master-data team joins on.

**W7 — MEDIUM: no language tags where they matter most.**
`dcterms:language` appears **zero** times in the A-Box. `estleg:legalText` is
`xsd:string` with no tag, on a bilingual corpus that already knows which acts have
English translations. ELI treats language as a defining property of an Expression, and
DCAT-AP requires it. The T-Box is also weaker than its own policy claims: 284 of 298
`rdfs:comment` values are untagged plain literals and 119 terms have **no comment at
all** (`docs/SCHEMA_REFERENCE.md` promises bilingual labels, and delivers them, but
says nothing about comments). Separately, 38 T-Box terms have an `@en` label whose
value is Estonian text or an identical bilingual string — e.g. `estleg:EUCourtDecision`
carries `"EL kohtulahend (EU Court Decision)"@en`, `estleg:paragrahv` carries
`"paragrahv"@en`, `estleg:TemporalStatusScheme` carries an all-Estonian `@en` label. A
ministry ontologist reading the `@en` projection gets Estonian.

**W8 — MEDIUM: two undeclared predicates and two undeclared prefixes in the T-Box.**
`estleg:citationSource` (3,191 uses) and `estleg:itemNumber` (1,672 uses) are emitted
by the A-Box but absent from the canonical CV — so
`generate_schemas_from_cv.py --check`, which only validates the four subcorpus schema
projections, passes while the vocabulary is incomplete. Inside the CV itself,
`estleg:containsPersonalData` (CV:2060) declares `rdfs:domain dcat:Distribution` and
`estleg:referenceType` (CV:5088) declares `rdfs:domain rdf:Statement`, but **neither
`dcat:` nor `rdf:` is declared in the CV `@context`** — those two CURIEs expand as
relative IRIs and silently resolve against the document base or get dropped.

**W9 — MEDIUM: ECLI coverage is thin and absent below Riigikohus.**
2,903 of 12,104 Riigikohus decisions (24%) carry an ECLI. The `rdfs:seeAlso` to the
e-Justice resolver is correctly minted where the ECLI exists, and the SHACL pattern
constraint is exactly the Council 2011/C 127/01 syntax — good work. But the `kohtud/`
subcorpus (maakohus, halduskohus, ringkonnakohus) carries **zero** ECLIs, and the CV
declares `estleg:ecliIdentifier` as `xsd:string` with no `owl:sameAs` to the ECLI URI.
ECLI is how the Ministry of Justice and e-Justice portal address case law.

**W10 — MEDIUM: string/IRI duplication across five fact families.**
`targetGroup`/`targetGroupConcept`, `temporalStatus` (string) / `TemporalStatus_*`
(SKOS), `belongsToCluster` (string) / `topicCluster` (IRI), `relatesToConcept` (string) /
`coversConcept` (IRI), `sourceAct` (string) / `partOfAct` (IRI), `issuer` (string) /
`enactedBy` (IRI). `docs/ARCHITECTURE.md` correctly warns consumers not to join on
`sourceAct`, but the string form is still published and still emitted. Every duplicate
pair doubles the surface a new consumer has to learn and get right.

**W11 — LOW: namespace and version drift.**
`metadata.jsonld:9` declares `"schema": "http://schema.org/"` while
`krr_outputs/controlled_vocabulary.jsonld:13` declares `"schema": "https://schema.org/"`.
These are distinct IRIs in RDF, so `estleg:Act rdfs:subClassOf <https://schema.org/Legislation>`
and `metadata.jsonld`'s `<http://schema.org/Dataset>` land in two disjoint schema.org
vocabularies on merge. Separately, `krr_outputs/act_expressions_combined.jsonld` stamps
`owl:versionInfo "0.11.0"` and `owl:versionIRI https://w3id.org/estleg/0.11.0` while the
CV and `metadata.jsonld` say `1.0.0`.

**W12 — LOW: `estleg:kehtiv` is an Estonian-named property in an English vocabulary,
carrying a build artefact.** CV:4261 gives it range `xsd:date` on `estleg:Act`, and the
value on `abipolitseiniku_seadus_peep.json` is `2026-05-24` — the generator's RT
`--kehtiv` snapshot argument. `docs/SCHEMA_REFERENCE.md` explains this carefully and at
length, which is exactly the sign it should not be a published property: it is pipeline
state, not a legal fact. An external reader will read "kehtiv" as "in force" and join on
it. `estleg:paragrahv` has the same Estonian-name problem but is at least a real legal
concept.

---

## Improvement ideas

**1. Mint the Riigi Teataja ELI URI for every act, from data already on disk.**
*What:* Derive `https://www.riigiteataja.ee/eli/{globaalID}` from the globaalID already
embedded in each act's `dcterms:source` (`/akt/{globaalID}.xml`) and emit it as
`owl:sameAs` on the act root, plus `eli:id_local "{globaalID}"`. Promote
`estleg:officialEnglishText` from `rdfs:seeAlso` to a proper `eli:LegalExpression` node
with `eli:language <http://publications.europa.eu/resource/authority/language/ENG>` and
`eli:realizes` back to the Work. Declare a new `void:Linkset` for it in `void.ttl`.
*Why it matters:* This is the single join a Riigi Teataja, Ministry of Justice, or RIA
consumer will try first. Without it estleg is a parallel universe of identifiers; with
it, estleg becomes an enrichment layer over identifiers the state already publishes. It
also unblocks federation with any EU service that resolves ELI.
*Effort:* S (offline derivation, 4,796 acts, no network — the pattern of
`src/estleg/backfill_official_english.py` applies directly).
*Impact:* H.
*Files:* `src/estleg/estleg_common.py`, a new `src/estleg/backfill_rt_eli.py`,
`krr_outputs/*_peep.json`, `krr_outputs/regulations/**`, `krr_outputs/void.ttl`,
`docs/SCHEMA_REFERENCE.md` (correct the "no registered ELI URI template" claim).

**2. Materialise the ELI/schema.org triples instead of relying on RDFS entailment.**
*What:* At combined-build time, assert `eli:LegalResource` / `eli:LegalExpression` /
`eli:LegalResourceSubdivision` as additional `@type`s, and `eli:realizes`,
`eli:is_realized_by`, `eli:date_entry_in_force`, `eli:date_no_longer_in_force`,
`eli:is_part_of`, `schema:Legislation` as additional predicates alongside the `estleg:`
originals. Add SHACL coverage shapes so the gate notices if they stop being emitted.
*Why it matters:* `docs/ARCHITECTURE.md` fixes the production query path at
`inference=none`. Every ELI mapping in the T-Box is therefore currently unreachable from
the endpoint the product site actually queries. A ministry evaluating "does this speak
ELI?" will run one SPARQL query, get zero rows, and stop. Materialisation is also what
lets a triplestore user write portable queries that work against both estleg and
EUR-Lex.
*Effort:* M (a projection pass in `generate_combined_jsonld`, plus SHACL shapes;
increases graph size but not the number of nodes).
*Impact:* H.
*Files:* `src/estleg/estleg_common.py` (combined builder), `shacl/estonian_legal_shapes.ttl`,
`docs/ARCHITECTURE.md`, `docs/SCHEMA_REFERENCE.md`.

**3. Fix `estleg:targetGroup` to be an ObjectProperty and retire the duplicate.**
*What:* Change CV:5942 to `owl:ObjectProperty` with `rdfs:range estleg:TargetGroup`,
add `owl:equivalentProperty` between `targetGroup` and `targetGroupConcept` (or
deprecate the latter with `owl:deprecated true` and `dcterms:isReplacedBy`). Add
`rdfs:subClassOf skos:Concept` to `estleg:Concept`, `estleg:LegalConcept`,
`estleg:TopicCluster`, `estleg:GeneralPartConcept` so the T-Box matches the A-Box's
existing dual typing. Do the same string→SKOS promotion for `estleg:temporalStatus`,
whose SHACL `sh:in` list (`shacl/estonian_legal_shapes.ttl:65`) also carries a fourth
value `notYetEffective` that has no `TemporalStatus_*` individual.
*Why it matters:* A DatatypeProperty used with 45,546 IRI objects makes the graph
OWL-DL-invalid. Any ministry that loads it into a reasoning triplestore (GraphDB with
OWL2-RL, the usual choice) gets an inconsistency, not a warning. This is a one-word fix
that removes the single biggest technical objection an external reviewer can raise.
*Effort:* S for the T-Box change; M if the duplicate property is actually removed from
16k peeps.
*Impact:* H.
*Files:* `krr_outputs/controlled_vocabulary.jsonld`, `shacl/estonian_legal_shapes.ttl`,
`src/estleg/classify_target_group.py`, `src/estleg/derive_act_temporal_status.py`,
`docs/SCHEMA_REFERENCE.md`.

**4. Rationalise the structural hierarchy against Riigi Teataja's actual drafting levels
and Akoma Ntoso.**
*What:* Fix the `Division`/`Section`/`Subdivision` label-vs-comment contradictions;
decide one class per Estonian level (osa, peatükk, jagu, jaotis) and deprecate the
extras; remove `estleg:Section rdfs:subClassOf estleg:LegalProvision` so containers stop
being entailed as provisions; add `rdfs:subClassOf eli:LegalResourceSubdivision` to
every container class; declare `estleg:itemNumber` in the CV and add a punkt class (AKN
`<point>`); merge or clearly separate `estleg:Part` (file artefact) from
`estleg:LegalPart` (osa), preferably by removing the file-split class from the published
vocabulary.
*Why it matters:* Structural addressing is the whole point of a legal ontology for a
ministry — it is what lets a service reference "§ 14 lg 1 p 7" and get a resolvable
node. Right now the punkt level exists in the data but not in the vocabulary, and the
three container levels are mutually contradictory in their own labels. An external
ontologist will read `Section` = "Jaotis" and the comment "(jagu)" and lose confidence
in the whole T-Box.
*Effort:* M (T-Box changes are small; deprecating `Part` and adding a punkt class
touches generators and SHACL).
*Impact:* H.
*Files:* `krr_outputs/controlled_vocabulary.jsonld`, `shacl/estonian_legal_shapes.ttl`,
`src/estleg/align_owl_modules.py` (its `DROP_CLASS_FRAGMENTS` already lists all four),
the law generators, `docs/SCHEMA_REFERENCE.md`.

**5. Stop asserting into EuroVoc's namespace.**
*What:* In `krr_outputs/eurovoc_concept_scheme.jsonld`, replace `skos:inScheme
estleg:EuroVocDomainScheme` and the local `skos:prefLabel` with either (a) nothing —
just keep the `dcterms:subject` links and let consumers dereference EuroVoc — or (b)
`skos:exactMatch` from a locally-minted `estleg:EuroVocDomain_*` node to the EuroVoc
IRI, with the cached label on the local node. Replace `skos:hasTopConcept` pointing at
EuroVoc IRIs with `skos:relatedMatch` or drop it. Strip the inline untagged English
`skos:prefLabel` assertions from `krr_outputs/tsus_osa7_138_169_owl.jsonld`.
*Why it matters:* Public bodies federate. The moment this graph is loaded next to the
authoritative EuroVoc, the duplicate and untagged `skos:prefLabel` assertions on
Publications Office resources are a visible SKOS integrity violation attributable to
estleg. Caching labels locally is fine; asserting them on someone else's IRI is not.
*Effort:* S.
*Impact:* M.
*Files:* `krr_outputs/eurovoc_concept_scheme.jsonld`,
`krr_outputs/tsus_osa7_138_169_owl.jsonld`, `src/estleg/classify_eurovoc.py`,
`docs/EUROVOC_OVERLAY.md`.

**6. Make institutions joinable: CPOV typing plus the EU corporate-body authority list.**
*What:* Add `rdfs:subClassOf org:Organization` (and note `cpov:PublicOrganisation`
equivalence for the public bodies) to `estleg:Institution`; turn
`estleg:institutionType` into a SKOS scheme of 8 concepts; mint `owl:sameAs` from each
of the 66 `estleg:EUInstitution` individuals to
`http://publications.europa.eu/resource/authority/corporate-body/{code}` using the
`estleg:euInstitutionCode` already present; add a registrikood property for Estonian
bodies where available.
*Why it matters:* "Which authority enforces this statute" is the query a ministry
actually wants, and the answer has to join to their own organisation master data.
CPOV is the adopted Core Vocabulary for exactly this, and CPSV-AP public-service
descriptions point at competent authorities the same way. The EU authority-list link is
nearly free — the codes are already there.
*Effort:* S for the EU `owl:sameAs` and the class mapping; M for the SKOS scheme and
registrikood.
*Impact:* H.
*Files:* `krr_outputs/controlled_vocabulary.jsonld`,
`krr_outputs/institutions/*.json`, `data/wikidata_institutions.json`,
`shacl/estonian_legal_shapes.ttl`, `krr_outputs/void.ttl`.

**7. Add language to expressions and legal text.**
*What:* Emit `dcterms:language` / `eli:language` on every act, ActExpression and
ProvisionVersion using the EU language authority URIs (`…/authority/language/EST`,
`/ENG`); language-tag `estleg:legalText` as `@et` on new generation.
*Why it matters:* ELI treats language as constitutive of an Expression — an untyped
Expression is not a conformant ELI expression. DCAT-AP requires it. And the corpus
already knows which acts have English versions (405 of them), so the distinction is
available for free.
*Effort:* M (the `@language` on `legalText` is the part that touches many files;
`docs/SCHEMA_REFERENCE.md` already flags the `sh:or xsd:string / rdf:langString`
groundwork as done).
*Impact:* M.
*Files:* `src/estleg/estleg_common.py`, `src/estleg/generate_act_expressions_608.py`,
the law/regulation generators, `shacl/estonian_legal_shapes.ttl`.

**8. Bring the T-Box up to review-grade documentation.**
*What:* Add `rdfs:comment` to the 119 terms that have none; language-tag the 284
untagged comments as `@en` and add `@et` for the terms a ministry reviewer will read;
fix the 38 terms whose `@en` label is Estonian text or a duplicated bilingual string;
add `rdfs:domain`/`rdfs:range` to the 3 properties missing a domain
(`coversConcept`, `hasProvision`, `hasSection`) and the 10 missing a range; declare the
`dcat:` and `rdf:` prefixes in the CV `@context` so `estleg:containsPersonalData` and
`estleg:referenceType` stop expanding to relative IRIs; declare `estleg:citationSource`
and `estleg:itemNumber`; extend `generate_schemas_from_cv.py --check` (or a new gate) to
assert that every `estleg:` predicate appearing in the corpus is declared in the CV.
*Why it matters:* The brief asks whether an external ministry ontologist can review
this. Today they can read the class names bilingually but 40% of terms carry no
definition at all, and the `@en` projection sometimes returns Estonian. The undeclared-
prefix bug means two `rdfs:domain` assertions are silently broken in the published
vocabulary.
*Effort:* M (mechanical but 119 comments need real prose; the drift gate is S).
*Impact:* H — this is what turns "an interesting dataset" into "a vocabulary a ministry
can adopt".
*Files:* `krr_outputs/controlled_vocabulary.jsonld`,
`src/estleg/tag_vocabulary_labels.py`, `src/estleg/generate_schemas_from_cv.py`,
`src/estleg/consolidate_tbox.py`, `tests/`.

**9. Close the ECLI gap below Riigikohus and add the ECLI URI.**
*What:* Derive ECLIs for `krr_outputs/kohtud/` (first and second instance) where the RT
source provides them; add `owl:sameAs` (not only `rdfs:seeAlso`) to the e-Justice ECLI
resolver URI; consider `estleg:ecliIdentifier` range `xsd:string` plus a separate IRI
property, since ECLI is both a notation and a resolvable identifier.
*Why it matters:* ECLI is the adopted EU identifier for case law and the key the
e-Justice portal and the Ministry of Justice use. The Riigikohus implementation is
already correct (syntax-validated, resolver-linked) — the gap is coverage, not design.
*Effort:* M (depends on whether the RT kohtulahendid API exposes ECLI).
*Impact:* M.
*Files:* the `kohtud` generator, `krr_outputs/kohtud/*`,
`shacl/estonian_legal_shapes.ttl`, `krr_outputs/void.ttl`.

**10. Give the ELI-DL draft layer real content.**
*What:* Emit `eli-dl:` predicates in the A-Box, not just the class mapping: link the
draft to its `eli-dl:ProcessStage` with an ELI-DL property, replace the
`estleg:initiator` string with an IRI into the existing `estleg:Institution_*` nodes,
and add consultation start/end dates. Make the 12 `DraftType` individuals SKOS concepts
in a scheme.
*Why it matters:* ELI-DL is the EU's answer to exactly this dataset — 22.8k drafts with
process stages. The class mapping to `eli-dl:DraftLegislationWork` claims conformance
that the instance data does not deliver: zero `eli-dl:` predicates are emitted anywhere.
For a Riigikogu or ministry consumer tracking legislative pipeline, initiator-as-IRI is
the difference between a joinable graph and a spreadsheet.
*Effort:* M.
*Impact:* M.
*Files:* the eelnoud generator, `krr_outputs/eelnoud/*`,
`krr_outputs/controlled_vocabulary.jsonld`, `shacl/estonian_legal_shapes.ttl`.

**11. Deprecate the string half of the six duplicated fact families, and retire
`estleg:kehtiv` from the published vocabulary.**
*What:* Mark `estleg:sourceAct`, `estleg:issuer`, `estleg:belongsToCluster`,
`estleg:relatesToConcept`, `estleg:targetGroup`-as-string and `estleg:temporalStatus`-as-
string with `owl:deprecated true` + `dcterms:isReplacedBy` pointing at the IRI form.
Move `estleg:kehtiv` (a generator snapshot argument) onto the dataset/provenance layer
where it belongs, or rename it to something no reader will mistake for "in force".
*Why it matters:* `docs/ARCHITECTURE.md` already tells consumers not to join on
`sourceAct` — but prose in a repo does not reach a SPARQL user. `owl:deprecated` does.
`estleg:kehtiv` is the worst case: an Estonian word meaning "valid/in force", typed
`xsd:date`, carrying build state, on the act node, requiring a full paragraph of
documentation to explain what it is not.
*Effort:* S for the deprecation annotations; M if values are actually removed.
*Impact:* M.
*Files:* `krr_outputs/controlled_vocabulary.jsonld`, `docs/SCHEMA_REFERENCE.md`,
`src/estleg/estleg_common.py`.

**12. Fix the two namespace/version drifts.**
*What:* Align the `schema` prefix on `https://schema.org/` in `metadata.jsonld:9`;
regenerate `krr_outputs/act_expressions_combined.jsonld` so its `owl:versionInfo` /
`owl:versionIRI` read `1.0.0` rather than `0.11.0`.
*Why it matters:* Two schema.org namespaces in one published dataset means
`schema:Legislation` and `schema:Dataset` land in disjoint vocabularies on merge; a
stale versionIRI on a release artifact undermines the v1.0.0 freeze the project just
executed.
*Effort:* S.
*Impact:* L (but cheap and visible).
*Files:* `metadata.jsonld`, `krr_outputs/act_expressions_combined.jsonld`,
`src/estleg/generate_act_expressions_608.py`.

---

## Open questions

1. **What exactly is Riigi Teataja's registered ELI template?** I confirmed the project
   already uses `https://www.riigiteataja.ee/en/eli/{tolkeSeosId}` successfully for 405
   acts, so RT clearly serves ELI-shaped URIs. I could not verify the Estonian-language
   template by HTTP probing: `riigiteataja.ee` is an Angular single-page app that
   returns HTTP 200 for **every** path including deliberately bogus ones, so status
   codes prove nothing and there is no RDFa in the served HTML. Before implementing
   idea 1, someone should confirm the canonical form against RT's published ELI
   documentation or the ELI register — likely
   `https://www.riigiteataja.ee/eli/ee/{agent}/{subtype}/{globaalID}/consolide` with the
   short `/eli/{globaalID}` as an alias. This is the one factual dependency in my top
   recommendation.

2. **Is `inference=none` on the Seadusloome endpoint a hard constraint or a performance
   choice?** If RDFS entailment could be enabled on the 265 MB combined graph, W1
   largely dissolves and idea 2 becomes unnecessary. `docs/ARCHITECTURE.md` presents the
   two-surface SHACL policy as deliberate but does not say whether the no-inference
   choice is about correctness or cost.

3. **Does the RT kohtulahendid API expose ECLI for first- and second-instance
   decisions?** Idea 9's effort estimate depends entirely on this. If not, the gap is a
   source-data limitation to document rather than a modelling defect.

4. **Is Akoma Ntoso actually in scope, or is ELI subdivision addressing sufficient?**
   Estonia does not publish AKN today. If the goal is EU interoperability rather than
   document-format exchange, mapping structural classes to
   `eli:LegalResourceSubdivision` (idea 4) is enough and full AKN alignment would be
   over-engineering. I have assumed the former.

5. **Was `estleg:Section rdfs:subClassOf estleg:LegalProvision` a deliberate choice?**
   `src/estleg/align_owl_modules.py:78` actively adds `estleg:LegalProvision` to any
   node typed `estleg:Section` in the hand-built OWL modules, which suggests it is
   intentional there (the module "sections" really are §-level). But the CV comment on
   `estleg:Section` (CV:927) describes it as a structural hierarchy node. If the same
   class is doing both jobs, splitting it is prerequisite to idea 4.

6. **Is there a target consumer whose requirements should drive prioritisation?** The
   ordering above assumes a generic Estonian public-sector consumer. If the concrete
   near-term consumer is, say, the Ministry of Justice's e-Justice work, ideas 1 and 9
   dominate; if it is an open-data portal harvest, ideas 7 and 8 and the DCAT-AP
   `conformsTo` gap matter more.
