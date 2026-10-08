# Release notes

These notes are for people who **use** the Estonian Legal Ontology data, its
vocabulary or its client package. They say what changed in terms of what you
load, query or store, and what you may need to do about it. The engineering
detail behind each change is in [CHANGELOG.md](../CHANGELOG.md), under the
issue numbers given here.

## How to read these notes

- **Pin a release, not `main`.** Load a tagged release and record its
  `owl:versionInfo` / `owl:versionIRI` (`https://w3id.org/estleg/<version>`).
  Untagged `main` changes without notice and is not a supported release.
- **Version numbers follow the consumer contract** in
  [STABILITY.md](STABILITY.md). A MAJOR release may break stored IRIs or
  queries. A MINOR release adds coverage or properties and may deprecate
  terms. A PATCH release corrects data without changing the schema.
- **Deprecation before removal.** A renamed or retired term stays declared
  with `owl:deprecated true` and `dcterms:isReplacedBy` for the deprecation
  window set out in STABILITY.md, section "Deprecation and support", and is
  removed only in a MAJOR release. Each entry below that deprecates something
  says what replaces it.
- **Heuristic layers move.** Values that STABILITY.md classes as Heuristic,
  such as EuroVoc subjects, deontic type and target group, can change on any
  regeneration. Re-read them after each upgrade instead of caching them as
  facts.
- Each entry is tagged **Action** when you may need to change a query or a
  stored value, and **Info** otherwise.

## Unreleased

Changes on `main` since 1.0.0 that the next release will contain. Nothing
here is in a tagged release yet.

### Vocabulary and query changes

- **Action: coverage-gap flags renamed (#701).** `estleg:hasNoCompetentAuthority`
  is now `estleg:competentAuthorityNotExtracted`, and `estleg:hasNoTransposition`
  is now `estleg:noTranspositionEdgeInCorpus`. The new names say what the flag
  means: an edge is missing *in this corpus*, which is not a legal finding.
  Every flagged node also carries `estleg:coverageFlagMethod` and
  `estleg:coverageFlagAsOf`. The old names stay declared as deprecated, so
  old queries still parse, but they return nothing because the old flags are
  no longer emitted. Update queries to the new names.
- **Action: fewer inferred types (#702, #709).** Several `rdfs:domain` and
  `rdfs:range` axioms named a specific class and made RDFS reasoners type
  unrelated nodes as that class. They are now `owl:Thing` / `rdfs:Resource`,
  with the intended classes recorded as `schema:domainIncludes` /
  `schema:rangeIncludes`. If you load with RDFS or OWL RL inference, nodes
  that were wrongly typed as `estleg:EULegislation`, `estleg:CourtDecision`,
  `estleg:ProvisionVersion` or `estleg:Act` no longer are. Select by the
  asserted `@type`.
- **Info: a lõige is not held to §-level requirements (#709).** An
  `estleg:Subsection` is still an `estleg:LegalProvision`, but the shapes no
  longer require it to carry `estleg:paragrahv`, `estleg:summary` or
  `estleg:partOfAct`; those live on its parent §. To list § nodes only,
  filter out `estleg:Subsection`.
- **Info: version intervals are inclusive.** `estleg:versionValidTo` is the
  last day a version applies, so a one-day version has equal start and end
  dates.
- **Info: amendment events link to their act.** Version-layer
  `AmendmentEvent` nodes carry `estleg:amends`.

### Identifier changes

- **Action: five law IRIs disambiguated.** In five cases two law files
  minted the same abbreviation IRI after transliteration, so one IRI denoted
  two acts. The established law keeps the abbreviation and the other file
  moved to a `_2` IRI. `REOS_2` and `ROS_2` are distinct laws that collided
  with REÕS and ROS. `UKS_2`, `TsUS_2` and `TsMS_2` are deprecated legacy
  duplicates (#426). If you stored one of the five base IRIs, check which
  law it denoted.

### Data content

- **Action: citation links changed (#696).** Abbreviation citations are now
  resolved against the full law registry, so they match about three times as
  many abbreviations as before, and they no longer match inside other words.
  Full-name references in the genitive case are resolved too. Expect new
  `estleg:references` edges and some removed false ones. Court citations that
  cannot be resolved are kept as unresolved citations instead of being
  dropped.
- **Action: `estleg:temporalStatus` is honest (#682).** An act with no
  version evidence is `unknown`, not `inForce`, and a retired IRI is never
  `inForce`. Do not read `unknown` as "in force".
- **Info: statutory text is more faithful (#694).** Superscripted section and
  subsection numbers and sub-points (alampunkt) survive in the text, and
  titles are no longer truncated.
- **Info: reviewed corrections are pinned (#700).** A heuristic value a
  reviewer has corrected is never rewritten by regeneration. Such nodes carry
  `prov:wasAttributedTo` and an `estleg:assertionConfidence` of 1.0 for the
  reviewed layer.
- **Info: sanctions layer rebuilt (#681).** 7,392 sanction records across
  464 laws, up from 2,550 across 294, with false confiscation and
  dissolution records removed and SHACL limits on amounts and terms.
- **Info: personal identification codes are masked (#683).** Court decision
  text is screened for Estonian personal identification codes. Each decision
  node carries `estleg:personalDataScreened` and
  `estleg:personalDataMaskedCount`.
- **Info: the lower-court corpus is a labelled sample (#689).** It holds one
  decision and its graph header carries `estleg:isSampleData true`. Exclude
  sample graphs from coverage claims.
- **Info: data currency.** The statute snapshot is dated 2026-05-24 and is
  behind its 45-day refresh target. Each distribution in `metadata.jsonld`
  now states its `dcterms:accrualPeriodicity`. The refresh path from Riigi
  Teataja works again (#691, #693).

### Downloads and licensing

- **Info: release assets are checksummed (#704, #705).** A release ships
  gzipped N-Triples, N-Quads and Turtle dumps, the named-graph dump
  `estleg_all.nq.gz`, the retrieval chunks, the combined JSON-LD files and a
  `SHA256SUMS` file. Each downloadable distribution in `metadata.jsonld`
  carries `dcat:byteSize` and an SHA-256 `spdx:checksum`. Every combined file
  header carries `owl:versionInfo`, so you can check what you loaded.
- **Info: rights notices ship with every release (#684).** `NOTICE`,
  `LICENSE`, `DATA_RIGHTS.md` and `DATA_PROTECTION.md` are release assets.
  CC BY 4.0 applies to the project's compilation layer only.
- **Info: licence files are machine-readable (#721).** `LICENSE` is the plain
  MIT text, so scanners detect it, and it covers the code only. The scope
  statement that used to sit in `LICENSE` is now in `NOTICE`. `REUSE.toml`
  and the `LICENSES/` directory give the licence of every path.

## 1.0.0 (2026-08-19)

The first public release. It replaces every earlier pre-release build.

### What you get

- The combined JSON-LD graph and the EUR-Lex, CURIA and draft-legislation
  aggregates as immutable GitHub Release assets, with a `SHA256SUMS` file.
- `metadata.jsonld` (DCAT) and `krr_outputs/void.ttl` (VoID) catalogue
  entries that point at the tagged release, not at mutable `main` (#548).
- The controlled vocabulary as the canonical T-Box, with bilingual Estonian
  and English labels (#433, #437), and the SHACL shapes.
- The `estleg_client` Python package and the `estleg-load` command for
  loading a single law (#551).
- An inter-release IRI delta, `krr_outputs/changes-0.11.0.jsonld` (#549).

### What changed from the pre-release builds

- **Action: new namespace (#516).** All IRIs are under
  `https://w3id.org/estleg/`. The earlier `data.riik.ee` namespace was never
  the project's to use, is not bridged with `owl:sameAs`, and must not be
  used. The project is independent and is not published by the Estonian
  government.
- **Action: act and provision identity.** Act work IRIs are yearless ASCII
  (#445). Provisions are typed `estleg:LegalProvision` (municipal ones also
  `estleg:KovProvision`), with no per-document classes (#434). Unnumbered
  lõiked are `_Lg_<n>` (#514). §-ranges are `_to_` (#346).
- **Action: act roots are acts, not ontologies or files.** Act roots are no
  longer `owl:Ontology` (#435) and no longer `owl:sameAs` the dated Riigi
  Teataja XML file; that URL is in `dcterms:source` (#447).
- **Action: renamed and retyped properties.** `curiaLink` is now
  `eurLexLink` (#441). `estleg:dutyHolder` is a target-group IRI, not a
  string (#460). Chapters point at their topic cluster with `dcterms:subject`,
  not `owl:sameAs` (#436).
- **Info: citations.** Citations that name a lõige point at that lõige (#512).
  Citations that cannot be resolved become `estleg:Citation` nodes with no
  target (#514).
- **Info: confidence.** Keyword-derived values carry
  `estleg:assertionConfidence` (#456).
- **Info: bridges.** `estleg:Act` is a subclass of `schema:Legislation`, and
  drafts align with ELI-DL (#543, #443).

### Rights

MIT covers the software only. The data has layered rights: third-party legal
texts keep their source terms, and the project's compilation layer is offered
under CC BY 4.0, which is still a draft election. Court decisions contain
personal data. Read `NOTICE`, `docs/DATA_RIGHTS.md` and
`docs/DATA_PROTECTION.md` before republishing. No Zenodo DOI was minted for
this release (#473).
