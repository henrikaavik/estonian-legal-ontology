# Proposal: provision-level draft impact and HÕNTE impact areas (#724)

| Field | Value |
| --- | --- |
| Status | **Proposed** |
| Date | 2026-10-08 |
| Ticket | #724 (review item 3.1, P3, Tier 3) |
| Parent | Epic #676 |
| Decision owner | Maintainer (Henrik Aavik) |
| Accountable owner | *open — see "Open decisions"* |
| External counterpart | *open — see "Open decisions"* |
| Supersedes / superseded by | none |

A design proposal and bounded-pilot definition, not a build ticket: no parser,
property, shape or data change ships with it. Follow-ups (§8) go on the
Tier 0–2 backlog only after owner, counterpart and attachment rights are settled.

## 1. Context and problem

The draft layer (`krr_outputs/eelnoud/`) answers *"which pending drafts name
this act"*, not *"what would change in the law if this draft passed"* — the
question JDM's Eesti.ai project 5 needs. Impact is recorded at **act level
only** (`estleg:amendsLaw`), never at the § / lõige level where amending
formulae operate (`§ 14 lõiget 2 muudetakse ja sõnastatakse järgmiselt`), and
the **impact areas** a seletuskiri assesses under HÕNTE (Hea õigusloome ja
normitehnika eeskiri) are not represented.

## 2. Feasibility study (measured on committed data)

Measured on `krr_outputs/eelnoud/eelnoud_combined.jsonld` (index generated
2026-03-07) at commit `c0140d497e`, with ad-hoc scripts outside the repository.

### 2.1 What the ingest keeps

Three EIS RSS feeds (`RSS_FEEDS` in
[`generate_draft_legislation.py`](../../src/estleg/generate_draft_legislation.py));
`fetch_rss` keeps only `title`, `link`, `pubDate` (any `<description>` is
dropped). No seletuskiri, draft text or attachment is fetched.

| Field on `estleg:DraftLegislation` | Drafts carrying it |
| --- | ---: |
| all drafts (`rdfs:label`, `legislativePhase`, `draftType`, `eisLink`) | 22,832 |
| `eisNumber`, `initiator`, `publicationDate` | 21,781 |
| `changeType` (title regex: amends 8,647 / enacts 501 / repeals 224 / supplements 110) | 9,482 |
| `affectedLawName` | 2,344 |
| `amendsLaw` | 1,162 |
| `enactedAs` | 133 |
| any body text, summary or description | **0** |

The title is the only text: median 90 characters, p90 199, max 1,292.

### 2.2 `amendsLaw` is act-level and partly mis-linked

- 1,162 drafts carry `amendsLaw` with 1,165 objects (3 multi-valued). **0**
  objects are `_Par_` / `_Lg_` provision IRIs — every object is an act
  (`*_Map`) IRI, chosen by `prefer_act_iri` in
  [`extract_draft_impact.py`](../../src/estleg/extract_draft_impact.py).
- By type: AmendmentBill 874, Bill 203, DraftIntent 85; by phase: Review 741,
  Submission 415, PublicConsultation 6.
- **Multi-act titles resolve to the wrong act.** All 4 `amendsLaw` links on
  drafts whose title names KarS or TsÜS point elsewhere, e.g. *"Karistusseadustiku
  ja tervishoiuteenuste korraldamise seaduse muutmise … VTK"* →
  `estleg:TTKS_Map`. Fuzzy matching of the coordinated phrase lands on its last
  member; 460 "muutmise seadus" titles name ≥ 3 seaduse/seadustiku tokens.
- 2,630 titles contain "muutmise seadus"; 1,706 of them carry no `amendsLaw`.
- `enactedAs` (only for `changeType=enacts`) never co-occurs with `amendsLaw`;
  spot checks show mis-resolutions (a käskkiri draft → a treaty-protocol `_Map`).

### 2.3 How much provision-level impact is in the feed text

Counts over all 22,832 titles, and over the 1,162 `amendsLaw` drafts:

| Pattern (case-insensitive) | All titles | `amendsLaw` drafts |
| --- | ---: | ---: |
| `§ N` anywhere | 116 | 0 |
| lõige / lg reference | 22 | 3 |
| `§ N … lõi… N … muudetakse` (full amending formula) | **0** | 0 |
| `muudetakse` | 132 | 0 |
| `täiendatakse` | 2 | 0 |
| `tunnistatakse kehtetuks` | 34 | 0 |
| `sõnastatakse järgmiselt` | **0** | 0 |
| `muutmise seadus` | 2,630 | 924 |
| `kehtetuks tunnistamise` | 224 | 2 |

All 132 `muudetakse` and 34 `tunnistatakse kehtetuks` hits are EU-act or
treaty titles ("…määrus, millega muudetakse määrust (EL) …"); the Estonian
amending formula lives in the draft text, never in the title. Titles yield only
§-in-title bill names (`<law> seaduse|seadustiku § N [lõike M]
muutmise|täiendamise seadus`):

| §-in-title bills | Count |
| --- | ---: |
| matched | 98 (muutmise 68, täiendamise 30) |
| of which name a lõige | 2 |
| of which carry `amendsLaw` | **0** |
| of which carry `affectedLawName` | 60 — all junk tails such as `"32 1 muutmise seadus"` |

**Conclusion.** Feed text yields provision-level impact for at most 98 of
22,832 drafts (0.43%), almost all at § level only. Everything else needs the
draft text and/or seletuskiri attachments, so the work is gated on attachment
access and rights, as the ticket's re-validation says.

## 3. Reuse the enacted-law amendment model

The T-Box already separates *effected* and *proposed* change; this proposal
adds the provision-level edge to that model rather than creating a parallel one.

| Existing term | Role today | Measured |
| --- | --- | ---: |
| `estleg:AmendmentEvent` | effected change from RT `muutmismarge`; `owl:disjointWith ProposedAmendment` | 20,937 nodes |
| `estleg:amends` / `amendedBy` | AmendmentEvent ↔ act (effected only) | 20,937 |
| `estleg:resultedInVersion` | AmendmentEvent → `ProvisionVersion` (provision level) | 4,954 events |
| `estleg:ProposedAmendment` | draft-derived proposal (#423) | 786 nodes |
| `estleg:proposesToAmend` / `hasProposedAmendment` | ProposedAmendment ↔ act | 786 |
| `estleg:amendingDraft` | ProposedAmendment → Draft | 786 |
| `estleg:amendsLaw` | Draft → act; the stale deprecation was removed in #709 | 1,162 drafts |
| `estleg:changeType` | Draft-level literal from title regex | 9,482 |
| `estleg:LegalProvision` ⊃ `estleg:Subsection` | § node `…_Par_157` (`partOfAct` → act); lõige node `…_Par_157_Lg_1` (`parentProvision` → §) | — |

Provision versions live in `krr_outputs/provision_versions/` (4,422 sidecars,
`estleg:ProvisionVersion` with `versionOf`, `versionValidFrom`, `versionText`).

### 3.1 New property: `estleg:amendsProvision`

```turtle
estleg:amendsProvision a owl:ObjectProperty ;
    rdfs:label "muudab sätet"@et , "amends provision"@en ;
    rdfs:domain estleg:DraftLegislation ;
    rdfs:range estleg:LegalProvision ;          # Subsection ⊑ LegalProvision
    rdfs:comment "Links a draft to an existing § or lõige its text proposes to change, add to or repeal. Proposal-scoped: never asserts an effected change."@en .
```

- **Object granularity.** The most specific existing node: a `Subsection`
  (`…_Par_<n>_Lg_<m>`) when the formula names a lõige and that node exists,
  otherwise the `_Par_<n>` node. Punkt-level references collapse to the lõige.
  A new lõige that does not yet exist attaches to its existing parent §,
  with an `insert` operation recording the intended new citation. A new §
  (`täiendatakse §-ga 14¹`) has only an act as its existing parent: emit
  `amendsLaw` and the operation's `proposesToAmend` act edge, and omit
  `amendsProvision` and `proposesToAmendProvision`. Never put an act IRI in
  either provision-only property or invent a provision that is not yet law.
- **Relation to `amendsLaw`.** Not `rdfs:subPropertyOf` (that would make a
  provision the object of an act-level property) but two property chains,
  materialised by the pipeline so every edge implies `amendsLaw` to the
  provision's act (which also repairs the §2.2 mis-links for parsed drafts):

  ```turtle
  estleg:amendsLaw owl:propertyChainAxiom ( estleg:amendsProvision estleg:partOfAct ) ,
      ( estleg:amendsProvision estleg:parentProvision estleg:partOfAct ) .
  ```

  The stale CV `owl:deprecated` flag on `amendsLaw` was already removed in
  #709; no further deprecation change is a prerequisite.
- **Operation detail** goes on `ProposedAmendment` nodes, not on the draft:
  one node per parsed instruction (sibling of today's act-level node, same
  `amendingDraft`), so each operation pairs with exactly one target. Add
  `estleg:proposesToAmendProvision` (ProposedAmendment → LegalProvision, the
  provision-level counterpart of `proposesToAmend`, `sh:maxCount 1`) and
  `estleg:amendmentOperation` with a closed value set: `replace`
  (muudetakse / sõnastatakse), `insert` (täiendatakse), `repeal`
  (tunnistatakse kehtetuks), `substituteWords` (asendatakse sõna).
  `amendsProvision` on the draft is the flat query surface. `ProposedAmendment`
  stays disjoint from `AmendmentEvent` and never carries `amends` or
  `amendmentDate`.
  For insertions, add `estleg:proposedProvisionCitation` (`xsd:string`) to
  the operation node to preserve the intended new § / lõige citation.
  Every operation also retains its target act through `proposesToAmend`;
  an act-level insertion has zero `proposesToAmendProvision` values.

### 3.2 SHACL

Added to `estleg:DraftLegislationShape`:

```turtle
sh:property [
    sh:path estleg:amendsProvision ;
    sh:nodeKind sh:IRI ;
    sh:pattern "_Par_[0-9]+(_[0-9]+)?(_Lg_[0-9]+(_[0-9]+)?)?$" ;
    sh:name "amendsProvision" ;
    sh:description "Existing § or lõige the draft proposes to change (#724). Provision IRIs only; act-level targets belong on amendsLaw." ;
] ;
```

No `sh:class`, matching `enactedAs`: the drafts SHACL bucket holds only stubs of
cross-bucket targets. On `estleg:ProposedAmendmentShape`,
`proposesToAmendProvision` is `sh:nodeKind sh:IRI`, with the same provision-IRI
pattern and `sh:maxCount 1` but no minimum (new § insertions are act-level).
`proposedProvisionCitation` is an optional, single `xsd:string`, required for
insertions. `amendmentOperation`
is `sh:in ( "replace" "insert" "repeal" "substituteWords" )`. A SPARQL-based
consistency check asserts that each `amendsProvision` object's act (via
`partOfAct`, through `parentProvision` for a lõige) is among the draft's `amendsLaw` objects.

### 3.3 When a draft becomes law

`enactedAs` covers only new acts. For amendment bills the effected change
already exists as RT-derived `AmendmentEvent`s with `resultedInVersion`. Keep
the classes disjoint and add `estleg:realisedBy` (ProposedAmendment →
AmendmentEvent), matched on the amending act's title and RT reference. The
provisions reachable via `realisedBy / resultedInVersion / versionOf` are what
actually changed: a **free silver standard** for recall, and a drift check
between draft and enacted text.

## 4. HÕNTE impact areas as a SKOS scheme

HÕNTE (Vabariigi Valitsuse määrus) requires the seletuskiri to analyse the
draft's impacts by area. The scheme below is built from the impact-area list in
HÕNTE's mõjude analüüs provisions. The § number is deliberately not cited
here; the scheme's `dcterms:source` must cite the exact § and redaction once a
legal reviewer confirms it.

| Concept `@id` | `skos:prefLabel` @et | `skos:prefLabel` @en |
| --- | --- | --- |
| `estleg:HonteImpactArea_Social` | sotsiaalne, sh demograafiline mõju | social, incl. demographic impact |
| `estleg:HonteImpactArea_SecurityForeignRelations` | mõju riigi julgeolekule ja välissuhetele | impact on national security and international relations |
| `estleg:HonteImpactArea_Economy` | mõju majandusele | economic impact |
| `estleg:HonteImpactArea_Environment` | mõju elu- ja looduskeskkonnale | impact on the living and natural environment |
| `estleg:HonteImpactArea_RegionalDevelopment` | mõju regionaalarengule | impact on regional development |
| `estleg:HonteImpactArea_PublicAdministration` | mõju riigiasutuste ja kohaliku omavalitsuse korraldusele | impact on the organisation of state and local-government bodies |
| `estleg:HonteImpactArea_PublicFinance` | riigi ja KOV kulud ja tulud | public-sector costs and revenues |

```turtle
estleg:HonteImpactAreaScheme a skos:ConceptScheme ;
    skos:prefLabel "HÕNTE mõjuvaldkonnad"@et , "HÕNTE impact areas"@en ;
    dcterms:source <RT URL of HÕNTE, pinned redaction> .
estleg:HonteImpactArea_Economy a skos:Concept ;
    skos:inScheme estleg:HonteImpactAreaScheme ; skos:topConceptOf estleg:HonteImpactAreaScheme ;
    skos:prefLabel "mõju majandusele"@et , "economic impact"@en .
estleg:impactArea a owl:ObjectProperty ;
    rdfs:domain estleg:DraftLegislation ; rdfs:range skos:Concept .
```

- **Naming** follows the CV's `TargetGroupScheme` / `TargetGroup_*` pattern.
- **PublicFinance** is a separate seletuskiri section (the costs and revenues
  of implementation) rather than one of the listed areas. It carries a
  `skos:scopeNote` saying so; governance may instead model it as a sibling
  property. Whether HÕNTE also lists an "other direct or indirect impact"
  residual must be checked against the text before the scheme is published.
- **Provenance.** `estleg:impactArea` is asserted only when the seletuskiri's
  impact section names the area *as affected*. "Not relevant" findings are not
  asserted. Each assertion carries `estleg:assertionConfidence` and
  `prov:wasDerivedFrom` (the attachment URL), matching the #456 / #700 pattern.
- **Source.** The seletuskiri's impact-analysis section headings, which follow
  the HÕNTE list. Titles are no substitute: 68 mention "mõju" as free-text
  topic, 1 says "mõjude analüüs/hinnang", none assigns an area.

## 5. Ingesting EIS attachments

**Today.** Every draft keeps `estleg:eisLink` (also `dcterms:source`), e.g.
`https://eelnoud.valitsus.ee/main/mount/docList/<uuid>?activity=2`. That page
lists the draft's documents (eelnõu, seletuskiri, kooskõlastustabel); none is
stored.

**Rights first.** No fetcher or parser is written until EIS (and Sätla, if it
becomes the source) agree in writing on bulk retrieval and rate, on what may be
redistributed, on the licence of derived data
([`DATA_RIGHTS.md`](../DATA_RIGHTS.md)), and on excluding personal data in
kooskõlastus material ([`DATA_PROTECTION.md`](../DATA_PROTECTION.md)). Default:
**publish derived edges only** (`amendsProvision`, `impactArea`, with
`prov:wasDerivedFrom` back to EIS), never attachment text.

**Allowlist.** `eelnoud.valitsus.ee` (and `www.`) is already in
`ALLOWED_HTTP_HOSTS` ([`estleg_common.py`](../../src/estleg/estleg_common.py)).
A different download host (document-store subdomain, Sätla) needs an explicit
entry in the fetcher PR; fetches go through `allowed_get` and are cached.

**Parser design** (sketch; runs on the eelnõu text, which carries the
formulae, not on the seletuskiri).

1. Segment into target-act blocks (`§ 1. Karistusseadustikus tehakse järgmised
   muudatused:`); resolve the act name once per block with the registry-first
   resolver in
   [`extract_cross_references.py`](../../src/estleg/extract_cross_references.py).
2. Split each block into numbered instructions (`1)`, `2)`, …) and parse:

   ```text
   instruction := target operation
   target      := ("paragrahvi" | "§") PAR [ ("lõiget" | "lõike" | "lõikes" | "lõige") LG ]
                  [ ("punkti" | "punktis" | "punkt") P ]
                | "paragrahvi" PAR | "seadust"
   PAR, LG, P  := NUMBER [ SUPERSCRIPT ]            # 14¹ → 14_1
   operation   := "muudetakse ja sõnastatakse järgmiselt" ":"           -> replace
                | "sõnastatakse järgmiselt" ":"                        -> replace
                | "täiendatakse" ( "lõikega" | "punktiga" | "§-ga" ) N ... -> insert
                | "tunnistatakse kehtetuks"                             -> repeal
                | "asendatakse sõna(d)" QUOTE "sõna(ga|dega)" QUOTE     -> substituteWords
   ```

   Lists (`lõikeid 2 ja 3`) expand to one edge each. Partitive (`lõiget`,
   `lõikeid`, `punkti`) and nominative (`lõige … tunnistatakse kehtetuks`)
   forms must be **added**: `_LG_TOKEN` covers only `lõike`, `lõikest`,
   `lõikes`, `lõigete`, `lg`.
3. Resolve with the existing `resolve_citation` / `_provision_lookup_keys`
   (lõige-first, all osa prefixes): `Karistusseadustiku § 121 lõiget 2` →
   `estleg:KARIST_2_Osa2_Par_121_Lg_2`, else `…_Par_121`. Unresolved targets
   go to a report, never into the graph.
4. Impact areas: a separate pass over seletuskiri headings, matched to
   `skos:prefLabel` / `skos:altLabel`.

## 6. Bounded pilot

The lead's suggested pool — 50 drafts with `amendsLaw` to KarS or TsÜS whose
formula appears in feed text — does not exist. 0 drafts carry the formula in
feed text, and the 4 KarS/TsÜS `amendsLaw` links point at other acts (§2.2).
The pilot is redefined on what the data does hold.

| Track | Pool (measured) | Sample | Needs rights? |
| --- | --- | --- | --- |
| A. Title-only § bills | 98 §-in-title bills | all 98 | no |
| B. KarS / TsÜS amendment bills | 79 titles naming KarS (69 AmendmentBill), 7 naming TsÜS (6 AmendmentBill) | 50, stratified by phase | **yes** (draft text) |
| C. Impact areas | seletuskirjad of the track-B sample | same 50 | **yes** |

**Gold set.** Two annotators hand-mark every `amendsProvision` edge and every
operation for the track-B sample against the draft text. Disagreements are
adjudicated, and agreement (Cohen's κ) is reported. For B drafts already
enacted, the `realisedBy` silver set (§3.3) is a second, independent check.

**Targets (go / no-go).**

| Measure | Go threshold |
| --- | --- |
| Track A edge precision | ≥ 0.98 |
| Track B edge precision (§ or lõige correct) | ≥ 0.95 |
| Track B edge recall | ≥ 0.85 |
| Track B resolution rate (edge lands on an existing node) | ≥ 0.90 |
| Track C impact-area F1 | ≥ 0.80 |
| SHACL | 0 new violations; existing gates stay green |

**Deliverable.** For the pilot drafts: `amendsProvision` edges,
`ProposedAmendment` operation detail, and `impactArea` assertions, shipped as
an opt-in sidecar (not merged into `eelnoud_combined.jsonld`). It comes with a
SPARQL example and an MCP-tool sketch answering *"if draft X passed, which §§
and lõiked of which acts would change, how, and in which HÕNTE impact areas"*.
That answer is what Eesti.ai project 5 needs to evaluate.

**Decision rule.** Go: file §8 follow-ups on the Tier 0–2 backlog. No-go on B:
keep track A and re-scope. A resolution-rate miss is a provision-layer gap
(missing `_Lg_` nodes), filed as such, not a parser failure.

## 7. Open decisions (maintainer)

1. **Accountable owner** for the capability.
2. **External counterpart**, with demand and success measures. The ticket's
   landscape candidate: JDM Eesti.ai project 5 / õigusloome korralduse osakond
   (Margit Juhkam).
3. **Attachment rights** with EIS and, if relevant, Sätla (§5).
4. **Scheme governance:** owner of `HonteImpactAreaScheme`, HÕNTE redaction
   pinning, PublicFinance as concept or property.
5. **Insertion representation:** confirm the act-only target and intended
   citation literal for new § insertions (§3.1).

## 8. Technical follow-ups (to file only after §7.1–7.3 are settled)

Rights-free and fileable now at the maintainer's discretion:

- **F1.** `detect_affected_laws` keeps the post-`§` fragment as the law name,
  so 98 §-in-title bills get no `amendsLaw` (bug, independent of #724).
- **F2.** Multi-act titles resolve to the last-named act (4/4 KarS/TsÜS wrong;
  460 omnibus titles at risk); split the coordinated phrase before resolving.

Gated on the open decisions:

- **F3.** T-Box terms of §3 (incl. `realisedBy`, property chains) + §3.2 SHACL.
- **F4.** EIS attachment fetcher (allowlist entry if needed, cache, rate limit).
- **F5.** Amending-formula parser + partitive/nominative lõige tokens.
- **F6.** `HonteImpactAreaScheme` in the CV + seletuskiri heading pass.

## 9. Risks

- **Rights refused:** only track A remains (0.43% of drafts); the goal is unmet.
- **Formats:** DOCX/PDF/scans; OCR lowers precision. Measure the mix in the pilot.
- **Phase drift:** draft text changes between phases; tie edges to a document
  version and phase, not only to the draft.
- **Provision gaps:** missing `_Lg_` nodes lower resolution; the `_Par_`
  fallback stays correct but coarser.
- **Over-claiming:** a proposal must never read as law; the
  `ProposedAmendment` / `AmendmentEvent` disjointness and the SHACL ban on
  `amends` on proposals stay.
