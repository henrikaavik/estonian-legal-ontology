# SHACL Severity and Inference Policy

Two release validators run the same shapes (`estonian_legal_shapes.ttl`)
against different load surfaces and inference modes. This is a documented
**two-surface policy**, not a drift bug.

## Surfaces

| Gate | Command | Inference | Failure rule |
|---|---|---|---|
| Bucket | `scripts/shacl_validate_all.py` | pyshacl `inference="rdfs"` | Fails on any non-conforming result |
| Sync | `scripts/validate_seadusloome_sync.py` | pyshacl `inference="none"` | Counts `Violation` + `Warning` (`--max-warnings` default 0) |

The bucket gate derives RDFS class membership before checking `sh:targetClass`
(needed for per-law `rdfs:subClassOf estleg:LegalProvision` nodes). The sync
gate mirrors the Seadusloome load path, which applies no inference.

Shapes **must** be inference-safe under both modes. Do not put `rdfs:range` or
`rdfs:domain` on properties whose objects are often bare cross-bucket stubs
(`estleg:hasVersion`, `estleg:coversConcept`, `estleg:hasSection`,
`estleg:interpretsEULaw`, …). Under `inference="rdfs"` a range axiom
phantom-types those stubs as the range class, then that class's NodeShape
`sh:minCount` constraints fire (PR #400: `hasVersion` → `ProvisionVersion` →
missing `versionValidFrom`). Prefer `sh:nodeKind sh:IRI` and omit `sh:class`
on those objects.

The bucket validator also loads `controlled_vocabulary.jsonld` into the data
graph, so T-Box `rdfs:range`/`rdfs:domain` axioms are live under RDFS
inference. Keep those axioms off the stub-valued properties listed above.

The same trap applies to **`rdfs:domain` on a property that more than one
class uses**. A domain axiom types every *subject* of the property into
that class, so a shared property must omit a narrow domain or use a common
superclass such as `owl:Thing`. `estleg:applicableProvision` is the worked example: it is used by
both `estleg:CourtDecision` and `estleg:Sanction`, and a
`rdfs:domain estleg:CourtDecision` on it phantom-types every Sanction as a
court decision under `inference="rdfs"`, after which
`estleg:CourtDecisionShape` demands `estleg:caseType` and
`estleg:caseNumber` of it — two violations per sanction node, none of them
real. Before adding a domain axiom, check every class that writes the
predicate. #702 applies `owl:Thing` to `celexNumber`, `eurLexLink`,
`documentDate`, and `ecliIdentifier` in both the vocabulary and combined graph;
the CURIA bucket now passes. Other buckets retain failures documented in
[VALIDATION_REPORT.md](../docs/VALIDATION_REPORT.md).

### Checking an axiom before pyshacl does

`scripts/check_phantom_typing.py` reads the JSON-LD directly and reports every
`rdfs:domain` / `rdfs:range` axiom that types a node into a shaped class which
no file in the same bucket declares it to be. It covers all seven buckets in
about half a minute and names the axiom; the pyshacl run needs up to half an
hour per bucket and reports only the downstream `sh:minCount` symptoms. CI
runs it ahead of each bucket's SHACL step, and
`tests/test_issue_709_phantom_typing.py` runs it under `pytest -m corpus`.

```bash
python3 scripts/check_phantom_typing.py --all
python3 scripts/check_phantom_typing.py --bucket riigikohus
```

It is not a substitute for SHACL: it is silent about a node that carries a
type honestly and still breaks that type's shape.

One entailment is tolerated rather than reported. A multipart act is split into
one file per osa, each rooted in an `estleg:Part` node that points at the act
with `estleg:isPartOf` and repeats the act's metadata, so the `estleg:Act`-domain
properties type 34 part roots as acts. All 34 pass the Act shapes. The repair is
the `Part` / `LegalPart` modelling still open under #709, not an axiom, and any
other `Part` that picks up an Act-domain property is still reported.

### Repairing an axiom (#709)

The vocabulary is its own build input, so a corrected row in
`consolidate_tbox.DOMAIN_RANGE` never reaches it: that table only backfills an
axiom that is absent, and the stale one is already there. Put the correction in
`OVERWRITE_DOMAIN` / `OVERWRITE_RANGE`, keep the `DOMAIN_RANGE` row in
agreement (a test enforces it), and re-run `scripts/consolidate_tbox.py`.

Choose the replacement from what the corpus and the owning shape show:

- **One subject class** — name it. `estleg:changeType` sits in
  `DraftLegislationShape` and is written only by drafts; its guessed
  `ProposedAmendment` domain was the whole `drafts` bucket failure.
- **Several subject classes** — `owl:Thing`, as for `estleg:enactedBy`, which
  municipal acts and their provisions both carry.
- **Objects declared on another load surface** — `rdfs:Resource`.
  `estleg:interpretsVersion` points from `riigikohus/` into
  `provision_versions/`; every target is a complete `ProvisionVersion` there
  and a bare reference here.

An open axiom still says what it stands in for. `DOMAIN_INCLUDES` and
`RANGE_INCLUDES` emit `schema:domainIncludes` / `schema:rangeIncludes`, which
carry no RDFS or OWL 2 RL semantics and therefore type nothing. List only the
classes measured on the shipped corpus.

## What a § must carry, and a lõige need not (#709)

`estleg:Subsection` is a subclass of `estleg:LegalProvision` (#519), so every
lõige is a focus node of any shape that targets `estleg:LegalProvision` — on
both surfaces, because pyshacl resolves class targets through
`rdfs:subClassOf` in the data graph even with inference off. A lõige carries
its own `estleg:legalText` and exactly one `estleg:parentProvision` (#132,
`SubsectionShape`); its § reference, summary and act live on that parent.

`estleg:LegalProvisionShape` therefore constrains **values** only, for every
provision including lõiked. The three fields a § must carry are required by
`estleg:ProvisionRequiresParagrahvShape`, `estleg:ProvisionRequiresSummaryShape`
and `estleg:ProvisionRequiresPartOfActShape`. Each has the same two targets as
`LegalProvisionShape` and a single
`sh:or ( [ sh:class estleg:Subsection ] [ sh:path … ; sh:minCount 1 ] )`.
A node that is not a lõige and lacks a field still fails (#450), now under
the shape named for that field.

`sh:class` reads the asserted type, and every lõige is typed
`estleg:Subsection` both in its peep and in combined. Under RDFS inference it
also reads an entailed one: `estleg:parentProvision` and
`estleg:subsectionNumber` (domain) and `estleg:hasSubsection` (range) entail
`estleg:Subsection`. A § that wrongly carried one of those would be excused in
its bucket and not by the no-inference gates. All 111,973 nodes they touch are
asserted Subsections, so the surfaces agree on the shipped corpus, and
`scripts/check_phantom_typing.py` reports any that is not, since
`estleg:Subsection` is a shaped class. A test pins the divergence.

Before this, #450's class target held all 111,911 lõiked to the §-level
minimums: 335,733 of the `laws` bucket's violations and about 89% of the
Seadusloome gate's. When a new shape targets `estleg:LegalProvision`, decide
whether it is meant for lõiked too.

## One constraint per shape when the message matters

`sh:message` attaches to a *shape*, not to a constraint, so every
constraint on a shape reports the same message. A shape that needs to
explain **which** rule was broken therefore gets one constraint of its
own rather than several.

The statutory ceilings on `estleg:Sanction` (issue #681) are the worked
example: `SanctionImprisonmentMaxYearsShape` (KarS § 45, 20 years),
`SanctionArrestMaxDaysShape` (KarS § 48, 30 days) and
`SanctionDailyRatesMaxShape` (KarS § 44, 500 daily rates) are three
NodeShapes over the same `sh:targetClass estleg:Sanction`, each holding
a single `sh:or` and its own citation. Folded into one shape they would
all report whichever message that shape carried.

Each ceiling reads as `NOT(this sanction type) OR NOT(this unit) OR
amount within the ceiling`, built from `sh:or`, `sh:not`, `sh:hasValue`
and `sh:maxInclusive`. Keep them **core SHACL** — no `sh:sparql` — for
the same reason the inference note above gives: both validator surfaces
must agree, and the two-surface policy is only checkable when the shapes
mean the same thing under `inference="rdfs"` and `inference="none"`.

Note that a constraint of this shape is *vacuously satisfied* by a node
that lacks the property: a Sanction carrying only `estleg:maxPenalty
"life"` and no `estleg:maxPenaltyAmount` conforms. That is deliberate —
the structured penalty fields are optional, so a ceiling must constrain
the amount when it is present without requiring it.

Penalty range ordering compares amounts only when `minPenaltyUnit` and
`maxPenaltyUnit` agree, and any currencies agree. Mixed-unit ranges such as
30 days to 1 year require normalisation; comparing their bare numbers would
produce a false violation. This guard uses core SHACL (`sh:equals` / `sh:not`).

## Severity

Omitted `sh:severity` is the SHACL default, `sh:Violation`. Use it for
required cardinality, datatype, node-kind, and structural IRI constraints.

`sh:severity sh:Warning` is for quality or coverage checks that reports
should distinguish from structural violations. It is **not** a non-blocking
severity. Operationally both gates fail the release on `sh:Warning`
(Warning ≡ Violation as a release gate): the sync gate counts warnings
explicitly, and a warning on the published graph is a release failure.

Examples:

- `estleg:paragrahv`, `estleg:summary`, and typed dates are violations.
- `dcterms:subject` is optional act-level EuroVoc classification. Missing
  values are allowed, but present values that are not
  `http://eurovoc.europa.eu/{id}` IRIs are `sh:Warning` — still a release
  failure, reported as a quality check rather than a structural violation.
