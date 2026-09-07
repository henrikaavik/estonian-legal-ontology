<!-- Reviewer worksheet from the 2026-09-03 public-sector readiness review. Raw evidence with file:line citations; the consolidated position is docs/PUBLIC_SECTOR_REVIEW_2026-09.md, which supersedes this file where they disagree (e.g. the w3id PURL resolves since 2026-08-19). Tree reviewed: main @ c96577d50c. -->

# Structural enrichment layers — public-sector fitness review

Scope owner: enrich-structure. Reviewed against the lens "can Justiitsministeerium,
Riigikohus, Riigi Teataja/RIK, Õiguskantsler, a KOV, or an X-tee/Bürokratt
integrator TRUST, OPERATE, and legally RELY on this?"

---

## Scope & method

**Files read line by line**

| File | Lines |
|---|---|
| `src/estleg/extract_cross_references.py` | 2703 |
| `src/estleg/generate_provision_versions.py` | 2444 |
| `src/estleg/generate_amendment_history.py` | 1803 |
| `src/estleg/generate_inverse_references.py` | 1384 |
| `src/estleg/extract_court_provision_links.py` | 1314 |
| `src/estleg/extract_temporal_data.py` | 924 |
| `src/estleg/materialize_combined_inverses.py` | 371 |
| `src/estleg/derive_act_temporal_status.py` | 330 |
| `src/estleg/derive_court_interpretation_staleness.py` | 275 |
| corresponding `tests/test_*.py` | ~7700 |

**Method.** Static reading plus eight measurements executed against the shipped
corpus, not against fixtures:

1. Executed the compiled citation patterns on synthetic and corpus text to test
   the abbreviation boundary and punkt capture.
2. Counted `_PAT_ABBREV` / `_PAT_SELF` / `_PAT_FULLNAME` matches over all 1,195
   root peeps' `legalText` + `summary`.
3. Classified all 29,825 emitted `estleg:references` edges in root peeps as
   self-act vs cross-act.
4. Audited `estleg:temporalStatus` across all 1,146 root-peep `Act`/`Law` nodes.
5. Audited all 176,605 `ProvisionVersion` nodes across 4,422 sidecars for chain
   heads carrying `versionValidTo`.
6. Sampled 120 sidecars for history depth, `rtUrl`, and `assertionConfidence`.
7. Resolved every `pipeline_version` stamp in `krr_outputs/reports/kov/*coverage.json`
   against this repository's git history.
8. Counted typed-reference-family triples in both peeps and
   `combined_ontology.jsonld` (304 MB).

Subagents covered `generate_amendment_history.py`, the inverse-reference pair,
the court-link pair, and the test files; their findings are folded in and
attributed inline.

---

## Strengths

These are real and worth defending in any ministry review.

**S1 — Superscript § handling is correct end-to-end.**
`_SUPERSCRIPT_DIGIT_MAP` / `_SUP_TOKEN` / `_normalize_par_number`
(`extract_cross_references.py:1010-1058`), `_xml_paragraph_key`
(`:1735-1772`), and `generate_all_laws._paragraph_id_suffix` (used at
`generate_provision_versions.py:1468`) agree on one key shape, so `§ 158` and
`§ 158²` never collide. Estonian law superscripts amended sections constantly;
this is the detail that breaks naive systems and it is handled, with the
reasoning written down at `:997-1023`.

**S2 — Explicit anti-fabrication rules in range expansion.**
`_expand_par_range` (`:1622-1661`) refuses to invent a provision key: it uses
`isdecimal()` not `isdigit()` (because `'62¹'.isdigit()` is True but `int()`
rejects it), returns `[]` for reversed or over-wide ranges, and returns `[]`
rather than concatenating `'4-59'` into the false key `'459'`. The comment at
`:1644-1657` states the rule as a rule. This is exactly the posture a legal
dataset needs.

**S3 — Multi-part law plumbing is genuinely correct.**
`source_act_to_prefix` is list-valued so an eight-osa act keeps all eight
provision prefixes reachable (`:191-198`, `:1706-1717`); each provision is keyed
under its *own* prefix so prefix-mixing files do not misroute (`:179-182`); and
`act_prefix_from_iri` (`:99-101`, used at `:805`) handles the 34 acts whose
prefix itself contains an underscore. Naive `split("_")[0]` would break all of
them.

**S4 — Deprecated act families are excluded from the resolver index.**
`extract_cross_references.py:147-154` skips `owl:deprecated` +
`dcterms:isReplacedBy` acts so retained provisions on a replaced act cannot
shadow the canonical replacement. `extract_court_provision_links.py:272-276`
does the same.

**S5 — The point-in-time interval algebra is right.**
`versionValidTo` of version *k* is the day *before* version *k+1*'s
`versionValidFrom` (`_day_before`, `:1487-1498`, applied `:1648`), so the
canonical inclusive as-of query returns exactly one version on a transition day.
Same-entry-into-force redactions are collapsed before chaining
(`_dedup_same_valid_from`, `:1506-1541`) so no zero-duration version exists. A
monotonicity guard refuses to emit an inverted interval (`:1658-1659`), and the
chain is re-sorted defensively (`:935`). SHACL enforces it
(`shacl/estonian_legal_shapes.ttl:2236-2247`, `sh:lessThan estleg:versionValidTo`),
and the as-of query is documented for consumers
(`docs/SCHEMA_REFERENCE.md:835`).

**S6 — The Estonian encoding problem is solved properly.**
`decode_rt_xml_bytes` + `_decode_score` (`generate_provision_versions.py:976-1000`)
score candidate decodes and penalise U+FFFD and UTF-8-as-latin-1 mojibake,
instead of forcing UTF-8 on pre-2010 windows-1257 RT XML. `š`/`ž` misread as
`ð`/`þ` is specifically penalised.

**S7 — Date honesty in `extract_temporal_data`.**
A plausibility band rejects RT digit-transpositions and EUR-Lex null sentinels
(`:58-59`, `:119-120`, `:138-140`). A cross-field guard returns `unknown` rather
than `repealed` when a repeal date precedes entry into force (`:352-362`). Most
importantly, `:254-266` and `:802-808` refuse to fabricate `{year}-01-01` when
only the RT year is known and emit `estleg:publicationYear` at `xsd:gYear`
precision instead. That is precision-marking done right, and it is the pattern
the rest of the stack should copy.

**S8 — Determinism discipline in the tracked reports.**
`BUILD_EVALUATION_DATE` pinning (`extract_cross_references.py:2620`),
`PINNED_RUN_TIMESTAMP`, and casefold-sorted `iter_peep_files`
(`estleg_common.py:1737`) keep report bodies free of wall-clock churn. The
amendment generator has no bare `except` in 1,803 lines and routes every error
to a failures sink with a `[P1]` stderr banner (`generate_amendment_history.py:1697-1703`).

**S9 — Effected vs proposed amendments are correctly separated.**
RT-derived changes are `estleg:AmendmentEvent`; never-enacted EIS bills are
`estleg:ProposedAmendment` linked by `estleg:hasProposedAmendment`, never
`amendedBy` (`generate_amendment_history.py:987`, `:1533-1594`), with SHACL
backing. A ministry will never see a pending bill published as enacted law.

**S10 — Inverse symmetry is closed on the shipped JSON-LD.**
A full streaming diff over all 259,950 combined nodes found zero forward edges
without an inverse for `references`/`referencedBy` (62,338 each),
`issuedUnder`/`implementedBy` (39,695), `interpretsLaw`/`interpretedBy`
(116,971), `transposesDirective`/`transposedBy` (307),
`harmonisedWith`/`harmonises` (259), and `parentProvision`/`hasSubsection`
(111,911), with zero dangling targets.

---

## Weaknesses / risks

### W1 — Every act in the corpus asserts `inForce`, including 27 explicitly deprecated ones. Severity: CRITICAL

Measured across all 1,146 `estleg:Act` / `estleg:Law` nodes in root peeps:

| `estleg:temporalStatus` | count |
|---|---|
| `inForce` | 1146 |
| `repealed` | 0 |
| `notYetEffective` | 0 |
| `unknown` | 0 |

27 of those nodes carry `owl:deprecated: true` and a `dcterms:isReplacedBy`
successor, and all 27 also say `inForce`. Zero acts carry `estleg:repealDate`.

This is structural, not accidental. Three mechanisms combine:

1. `derive_act_temporal_status._provision_status` (`:69-81`) can only return
   `repealed` when the chain head carries `versionValidTo`. **Zero of 90,218
   chain heads across all 4,422 sidecars carry one** — by construction, the head
   is the version nothing supersedes and `synthesise_versions` leaves it open
   (`:1642`). So `repealed` is unreachable from the version layer.
2. `residual_status` (`:142-153`) returns `"inForce"` unconditionally for any
   non-deprecated act present in `INDEX.json`. The docstring calls this an
   inference about "current published consolidations"; it is an assumption
   applied corpus-wide.
3. `process_deprecated_copy` (`:195-223`) copies the *replacement* act's status
   onto the *replaced* act. Since replacements are all `inForce`, every
   deprecated act inherits `inForce`. The semantics are backwards: a replaced
   act is precisely the one that is not in force.

**Why it matters.** `estleg:temporalStatus` is the field a KOV clerk, a court
clerk, or Bürokratt would use to decide whether a rule still binds. A graph in
which 100% of acts, including ones the graph itself marks deprecated, say
`inForce` is not merely low-recall — it asserts a falsehood about Estonian law
with no confidence marker. `eval/FITNESS_REPORT.md` presents this as coverage
progress ("act-level temporalStatus known on 65.6% of laws"), which reads as a
win rather than as a uniform default.

### W2 — Shipped artefacts do not match shipped code, and three provenance stamps point at commits that do not exist. Severity: CRITICAL

Resolving every `pipeline_version` in `krr_outputs/reports/kov/*coverage.json`:

| pipeline | pipeline_version | resolvable? | commits behind HEAD |
|---|---|---|---|
| `extract_cross_references` | `2074016e2` | **no** | — |
| `generate_inverse_references` | `2074016e2` | **no** | — |
| `extract_legal_concepts` | `2074016e2` | **no** | — |
| `extract_sanctions` | `2074016e2` | **no** | — |
| `extract_temporal_data` | `96abdac2f0` | **no** | — |
| `extract_annotations` | `2d46eb74b` | **no** | — |
| `generate_amendment_history` | `a9b2fac149` | yes | 84 |
| `classify_deontic` | `8938e998f4` | yes | 92 |
| `extract_institutional_competence` | `d24a048162` | yes | 96 |
| `court_provision_links` | `5c730d6acb` | yes | 118 |
| `classify_eurovoc` | `8a8803d545` | yes | 120 |
| `extract_provision_versions` | `b43d9900a` | yes | 144 |

`git cat-file -e` fails on the first six. The two layers under review here
(citation extraction and its inverse) are both stamped with the unresolvable
`2074016e2`. Eight of twelve reports also carry `run_timestamp: 1970-01-01`,
the epoch sentinel, so there is no fallback signal for when the data was built.

Independent confirmations that code and data have diverged:

- Commit `c96577d50c` (HEAD) registers VTMS/HKMS so court citations stop falling
  through to TMS/KMS. Rebuilding the index live yields `VTMS → {VTMS}` and
  `HKMS → {HALDUS}`, but the shipped corpus contains **zero** `VTMS_*` or
  `HALDUS_*` `interpretsLaw` edges (court-links reviewer).
- `docs/SCHEMA_REFERENCE.md:58` states the typed reference family is "emitted
  when the Estonian verb governing the citation is clear". The classifier works
  when tested directly, yet the whole corpus contains **one**
  `estleg:repeals` triple and **zero** `isLegalBasisFor` / `exceptionTo` /
  `derogatesFrom` instance triples (see W9).
- `combined_ontology.nt` and `.ttl` contain zero `issuedUnder`, `interpretsLaw`,
  `governs`, `transposedBy`, `harmonises` triples and only 34,458 `references`
  against the JSON-LD's 62,338 — the N-Triples and Turtle dumps are a
  generation behind the JSON-LD that `README.md:96-98` points bulk-load
  consumers at (inverse-refs reviewer).
- `interpretsLaw` (89,712) and `interpretedBy` (117,020) disagree by 27,308
  edges; `estleg:eesti_vabariigi_pohiseadus_Par_24` reports 214 interpreting
  decisions in one direction and 127 in the other (court-links reviewer).

**Why it matters.** An official register must be able to say "this artefact was
produced by commit X from inputs Y". Here the artefact says it was produced by a
commit that is not in the repository. That alone would fail a Riigi Teataja or
avaandmed.eesti.ee onboarding review, independent of data quality.

### W3 — The citation resolver has no left word boundary, so unregistered abbreviations resolve to the wrong law. Severity: HIGH

`_PAT_ABBREV` (`extract_cross_references.py:1088-1091`) is

```python
_PAT_ABBREV = re.compile(
    rf"({_ABBREV_ALTERNATION})\s*{PAR_SUFFIX}\s*({_PAR_NUMBER}){_LG_TAIL}",
    re.UNICODE,
)
```

There is no `\b` and no `(?<![A-Za-zÕÄÖÜŠŽ])`. Verified by running the module:

```
'MTÜS § 12 alusel'  ->  law_ref='TÜS',  paragraphs=['12']
'ELS § 7'           ->  law_ref='LS',   paragraphs=['7']
'xxKarS § 121'      ->  law_ref='KarS', paragraphs=['121']
```

MTÜS (mittetulundusühingute seadus) silently resolves to TÜS
(tulundusühistuseadus); ELS resolves to LS (liiklusseadus). Both are wrong-law
citations, emitted as plain `estleg:references` with no uncertainty marker.

Ten latent suffix pairs exist inside the registered table alone —
`KMS⊂HKMS`, `TMS⊂VTMS`, `KVS⊂RKVS`, `LS⊂{KELS,KLS,RLS,TLS}`,
`PS⊂{KoPS,RaamatPS}`, `TKS⊂TTKS` — and every one of the ~1,000 Estonian act
abbreviations *not* in the 95-entry table is a candidate whenever it ends in a
registered one.

The sibling module `extract_court_provision_links.py:465-470` **does** carry the
guard (`\b({abbrevs})\s*{PAR_SUFFIX}`), verified: `MTÜS § 12` → `[]` there. So
the project knows the fix; it was simply never applied to this module. Commit
`c96577d50c` states "pat_abbrev already has a leading word boundary" — true of
the court module, false of this one — and the incident was closed by registering
two more abbreviations rather than by fixing the boundary. The bug class
survives.

Two aggravating factors from the court-links reviewer: the hand-curated
`KNOWN_ABBREVIATIONS` also contains outright wrong mappings —
`TTKS → TTOS` (real TTKS is Tervishoiuteenuste korraldamise seadus),
`KELS → KHaS` (real KELS is Koolieelse lasteasutuse seadus),
`TKS → Tolliseadus` (real TKS is Tarbijakaitseseadus) — while
`data/law_abbreviations.json` (601 keys, many sourced from the official RT
`lyhend`) is the registry AGENTS.md declares of record and is not consulted.

### W4 — Cross-law recall is capped by two hand-maintained tables; the 1,000-entry corpus-title map exists but is wired only into the preamble path. Severity: HIGH

Measured over all 1,195 root peeps' `legalText` + `summary`:

| pattern | matches |
|---|---|
| `_PAT_SELF` (käesoleva seaduse §) | 47,124 |
| `_PAT_FULLNAME` (genitive law name §) | 10,063 |
| `_PAT_ABBREV` (KarS § 121) | 3 |

and the edges actually emitted:

| edge kind | count |
|---|---|
| self-act (target in the same act) | 24,280 |
| cross-act | 5,545 |

Two consequences.

First, the abbreviation route is effectively dead on the distributed text —
Estonian consolidated law spells the law name out in the genitive rather than
abbreviating it. So cross-law recall rests on `_PAT_FULLNAME`, which is built
from `FULLNAME_GENITIVE` — **74 entries against 1,122 indexed laws**. Roughly
93% of Estonian acts cannot be named by any in-law citation pattern.

Second, the fix is already in the file but not connected.
`build_law_title_to_iri` (`:365-392`) builds a nominative-title → act-IRI map
from every act's `estleg:sourceAct` (the run log prints ~1,000 titles), and
`_genitive_law_ref_to_title` (`:350-362`) folds a genitive onto that key. Both
are passed only to `resolve_preamble_citation` (`:2560-2568`). The in-law pass
(`:2198-2204`) receives `abbrev_to_prefix` and `prefix_to_provisions` only.

**Why it matters.** "Which provisions depend on § X" is the question a
legal department actually asks, and the answer here is dominated 81% by
intra-act self-references. The cross-act dependency graph a ministry would use
for impact assessment has 5,545 edges over 1,120 laws — under five per act.
Neither the report nor the schema documentation states this ceiling.

### W5 — Precision and recall are measured nowhere, and the one headline number is a tautology. Severity: HIGH

`eval/FITNESS_REPORT.md` reports:

> Cross-reference edge resolution: 28,207 / 28,207 (100.0%) existing citation
> edges resolve to an in-corpus node (edge precision, not extraction recall).

That 100% is unfalsifiable: `resolve_citation` (`:1706-1718`) only appends a
target when it finds one in the index, so every emitted edge resolves by
construction. It is further inflated by the placeholder mechanism in W10 — 59
of those edges point at manufactured `estleg:VOS_Par_*` nodes that are not real
provisions. The parenthetical is honest, but the number sits in a
"fitness-for-purpose" report where a reader will read 100% as accuracy.

The only gold set on disk, `eval/gold_sets/targetGroup.json`, is a template with
`"items": []` — zero entries, and it is for a different layer. `eval/README.md`
is candid that "Cycle 2 measured several layers 31–35% wrong (#576/#577) but the
project never instrumented it".

The tests reinforce this. The 20 corpus-derived preamble golden samples
(`tests/fixtures/kov_layer2b/preamble_samples.json`) all assert
`expected_count >= 1`; there are **zero negative samples**, so a change that
doubled false positives on non-citation text would leave all 20 green (tests
reviewer).

**Why it matters.** A public body cannot use unknown-precision links for impact
analysis. "How often is this wrong?" has no answer in this repository, and the
document that looks like it answers it does not.

### W6 — The point-in-time layer cannot answer the court's actual question. Severity: HIGH

Measured across all 4,422 sidecars (176,605 `ProvisionVersion` nodes) and a
120-file random sample:

| finding | value |
|---|---|
| sidecars total | 4,422 |
| of which from the law path (real redaction history) | 742 with output, of 1,145 attempted |
| sidecars that are current-snapshot only (every provision has exactly one version) | 107 of 120 sampled |
| chain heads carrying `versionValidTo` | **0 of 90,218** |
| versions carrying `estleg:assertionConfidence` | **0** |
| versions carrying `estleg:rtUrl` | 6,573 of 6,573 sampled |

Five distinct problems:

**(a) 83% of the 407 MB is not point-in-time data.** 3,680 of 4,422 sidecars come
from `process_regulation_snapshot` (`:364-452`), which emits one version of the
*current* text with `valid_to=None`. Nothing on the sidecar distinguishes
"complete redaction history" from "today's snapshot dressed as a version". A
consumer seeing `estleg:hasVersion` on a regulation provision would reasonably
infer history exists.

**(b) No provision is ever marked as ended.** Because zero heads carry
`versionValidTo`, every provision in the graph reads as currently in force. A
provision repealed in 2018 whose act is still live presents an open-ended head.

**(c) Repealed provisions are entirely invisible.** `target.provisions` is read
from the peep (`_load_provision_map`, `:204-252`), i.e. today's consolidated
text. A § that existed in 2005 and was repealed in 2010 is not in the peep, so
it gets no version chain at all. A court asking "what did § 12 say on the date
of the facts" gets nothing when § 12 has since been removed — which is precisely
the case where the question is asked.

**(d) Renumbering is undetected and produces wrong text under a real IRI.**
`synthesise_versions` (`:1578-1633`) diffs redactions by paragraph suffix key
alone. Estonian amending acts renumber sections. If old § 14 became § 15, the
old redaction's `"15"` entry is a different provision's text, yet it is emitted
as a `ProvisionVersion` of today's § 15 with an authoritative `estleg:rtUrl` and
`estleg:versionValidFrom`. There is no guard, no heuristic, and no counter. This
is the most legally dangerous defect in the layer: it attributes wrong statutory
text to a named provision on a named date, with full-looking provenance.

**(e) Future-dated redactions are excluded by design** (`:856-857`,
`fetch_redaction_chain`). A drafting ministry cannot ask "what will § X say from
2026-07-01", and a redaction that entered force after the last build is absent.

Two lesser issues: the chain is keyed on an exact act-title string match
(`:821`, `:876`), so a retitled act loses its pre-rename history with no
distinguishing counter against "genuinely no history"; and the search uses
`leht: 1, limiit: 500` with no pagination — the corpus maximum is 233 redactions
(riigilõivuseadus), so the cap is not currently binding, but nothing warns if it
becomes so.

### W7 — Not reproducible: gitignored inputs, network dependence, and four layers outside the documented DAG. Severity: HIGH

`.gitignore:24-25` excludes `data/riigiteataja/*.xml` and `**/*.xml`.
`git ls-files data/riigiteataja` returns exactly `english_eli.json` and
`karistusseadustik.xml`. There is no committed fetch script.

Consequences, per layer:

- `extract_cross_references.collect_text_from_xml` (`:1775-1800`) is the primary
  text source for the in-law pass. On a fresh clone it returns `{}` for every law
  but one, so the pass falls back to `summary`/`legalText` — which is why my
  scan found 3 `_PAT_ABBREV` matches where the committed report claims 46,666
  citations found.
- `build_state_regulation_lookup` (`:453-513`) and `build_kov_act_lookup`
  (`:516-601`) read adoption dates from `<vastuvoetud><aktikuupaev>` in the same
  XML and `return lookup` empty when the pairing fails, so on a clone **all**
  preamble state- and KOV-regulation citations go unresolved.
- `extract_temporal_data` reads its dates from the same XML — consistent with
  the measured zero `estleg:repealDate` fields in the corpus.
- `generate_amendment_history` Step 0 clears `estleg:amendedBy` from every peep
  *before* building the XML lookup, with no guard on an empty corpus. On a fresh
  clone it strips `amendedBy` from ~5,654 enriched act roots and re-emits
  nothing, orphaning 5,647 chain files (amendment reviewer).
- `generate_provision_versions` requires live `riigiteataja.ee` calls. Shipped
  run: 6,355 s wall (1h 46m), 0.18 laws/s, 10,930 redactions, 354.7 MB peak. A
  cold rebuild adds 10,930 XML fetches plus 1,145 search calls at a 0.3 s sleep
  floor.

Four of the layers under review are **not in the 16-step integration DAG** —
`generate_provision_versions.py`, `derive_act_temporal_status.py`,
`derive_court_interpretation_staleness.py`, and
`materialize_combined_inverses.py` all return `NOT IN DAG` against
`run_all_integration.STEPS`. `docs/ARCHITECTURE.md` describes the DAG as the
"enrich + combine + validate" path, so the 407 MB point-in-time product, the
act-status derivation, and the combined-inverse materialization have no
documented regeneration path at all.

No runtime figure appears in `README.md` or any file under `docs/`.

### W8 — Punkt granularity is silently dropped in the in-law path while the docstring advertises it. Severity: MEDIUM

The module docstring (`:13-15`) lists `KarS § 121 lg 2 p 3   (with point)` among
detected patterns. `_LG_TAIL` (`:1084`) captures only lõige:

```python
_LG_AFTER_PAR = r"(?:lõike(?:st|s)?|lõigete|lg)"
_LG_TAIL = rf"(?:\s+{_LG_AFTER_PAR}\s+(\d+))?"
```

Verified:

```
'KarS § 121 lg 2 p 3'  ->  lg='2', citationText='KarS § 121 lg 2'   # 'p 3' lost
'KarS § 121 p 3'       ->  lg=None, citationText='KarS § 121'        # 'p 3' lost
```

The preamble path *does* capture punkt (`_P_TOKEN` `:1252`,
`_build_citation_detail` `:1424`) and is tested. The in-law path drops it, and
`citationText` — the field a consumer would quote back to a lawyer — is
truncated mid-citation to a narrower scope than the source text stated.

### W9 — The typed reference family is documented but effectively absent. Severity: MEDIUM

`docs/SCHEMA_REFERENCE.md:58` and `docs/API_GUIDE.md:247` both state the typed
sub-properties are emitted. Counted in the corpus:

| property | peeps | combined (incl. T-Box declaration) |
|---|---|---|
| `estleg:repeals` | 1 | 2 |
| `estleg:isLegalBasisFor` | 0 | 1 |
| `estleg:exceptionTo` | 0 | 1 |
| `estleg:derogatesFrom` | 0 | 1 |

The combined counts are the T-Box axioms. There is essentially no instance data.
`classify_citation_relation` (`:1198-1220`) works correctly when invoked
directly — I confirmed all four verbs classify — so this is a
data-not-regenerated symptom of W2, not a broken classifier.

Two design issues remain for when it is regenerated. First, the
`isLegalBasisFor` direction is inverted: the edge is written on the *citing*
node (`:1992-1995`), so "käesoleva seaduse § 5 alusel kehtestab minister
määruse" asserts *citing* `isLegalBasisFor` *§ 5*, when § 5 is the basis and the
citing provision is what rests on it. The other three verbs (`repeals`,
`exceptionTo`, `derogatesFrom`) are directionally correct. Second, one verb
match in a 120-character window is applied to **every** target resolved from
that citation (`:1985-1986`), so a chained citation inherits a relation stated
about only its first member. `_REL_BASIS` matching a bare `\balusel\b` is very
loose for a word that is ubiquitous in Estonian legal prose.

### W10 — Unresolved references are handled three incompatible ways, and the placeholder nodes evade the documented stub filter. Severity: MEDIUM

Three mechanisms coexist:

1. **In-law unresolved** become `estleg:Citation` nodes inside the peep with
   `citationText` + `citationSource` and no `citationTarget`
   (`:1969-1981`). Report: 7,548 unresolved.
2. **Preamble unresolved** become the same node shape on the act
   (`:2354-2366`). Coverage report: 3,086.
3. **`krr_outputs/unresolved_references.jsonld`** holds something different
   again: 61 `estleg:UnresolvedReferencePlaceholder` nodes with
   `estleg:referenceStatus: "fallbackMaterialized"`.

The third is the problem. Inspecting it:

- 59 of 61 use the prefix `VOS_` (`estleg:VOS_Par_276` … `VOS_Par_334`). **No
  real provision in the corpus uses that prefix** — actual VÕS provisions are
  `estleg:volaoigusseadus_OsaN_Par_M`. These are residue from a pre-migration
  IRI scheme, one is the mangled `Vlaigusseadus_Osa9`.
- They are merged into `combined_ontology.jsonld` (64 mentions), and 59
  `estleg:references` edges point at them (118 total `"@id": "estleg:VOS_Par_N"`
  occurrences = 59 definitions + 59 edge targets).
- **None carries `estleg:isStubNode`.** `docs/ARCHITECTURE.md` instructs
  combined-only consumers to write `FILTER NOT EXISTS { ?x estleg:isStubNode
  true }`. That filter does not exclude these, so a ministry following the
  documented guidance still traverses into a node with no `legalText`, no
  `paragrahv`, and a mangled label `"V O S  Par 276"` — while the IRI shape says
  "provision 276 of an act".

The file name is also misleading: it holds materialized placeholder *targets*,
not the 10,634 unresolved citations, which live scattered across the peeps.

### W11 — No `estleg:assertionConfidence` anywhere in this layer. Severity: MEDIUM

`estleg_common.py:1508-1517` provides `stamp_assertion_confidence`, and
`docs/SCHEMA_REFERENCE.md:22` states that keyword-derived assertions carry it.
Grepping the source, the only caller is `classify_target_group.py:650`. Nothing
in the citation resolver, the court-link extractor, the amendment generator, the
version synthesiser, or either temporal-status deriver stamps a confidence.

Measured: **0 of 176,605 `ProvisionVersion` nodes** carry it. Yet several of
these assertions are demonstrably heuristic — a regex-matched cross-reference, a
Jaccard ≥ 0.9 draft-to-act match (`generate_amendment_history.py:946-970`), a
`residual_status` guess of `inForce`, a `versionText` diffed by paragraph-number
key. All ship as bare facts, indistinguishable from RT-sourced ground truth.

### W12 — The court-interpretation staleness signal fires on 89% of decisions and cannot distinguish "current" from "unknown". Severity: MEDIUM

From the court-links reviewer, verified against the corpus:
`derive_court_interpretation_staleness.py:170-186` sets `outdated = True` if
**any** interpreted provision gained a later `validFrom`. At an average 9.8
provisions per decision, it fires for 8,122 of 9,156 version-backed decisions
(88.7%). It carries no per-provision breakdown and no coverage marker:
`versioned = [p for p in … if p in index]` (`:164`) silently drops unversioned
provisions, so `interpretationOutdated: false` means "no evidence found", not
"still current" — and nothing on the node distinguishes the two. 1,410 nodes
carry an empty `estleg:interpretsVersion: []` alongside `outdated: true`.
`estleg:interpretsVersion` is stripped from combined
(`estleg_common.py:416-428`), so the flagship surface ships the verdict without
the evidence.

Also: `--apply` will happily strip all 9,156 stamps and exit 0 if
`provision_versions/` is missing, because `build_version_index` globs an absent
directory to `{}` without error.

### W13 — Point-in-time court links target today's text with no as-of caveat. Severity: MEDIUM

`extract_court_provision_links.resolve_citations` resolves against
`prefix_to_provisions`, i.e. today's consolidated text. A 2011 judgment citing
KarS § 121 links to the provision as it reads now.
`docs/SCHEMA_REFERENCE.md:270,765` documents `interpretsLaw` with no as-of
caveat. A lawyer reading the graph would reasonably conclude the judgment
construes today's wording.

### W14 — Act-root inverse aggregates grow monotonically and are never cleared. Severity: MEDIUM

`materialize_act_root_aggregates` (`generate_inverse_references.py:1021`,
`:1070`) merges existing act-root values with newly collected ones. Unlike
`referencedBy`, the props `references`, `interpretedBy` and `competentAuthority`
have no clear pass. Deleting a provision-level citation leaves the act root
asserting it forever. 5,858 combined `references` edges have an act-root
subject; all are unfalsifiable once written (inverse-refs reviewer).

Related: `governs` is materialized only from act-like subjects
(`materialize_combined_inverses.py:128`), so 10,191 of 12,373
`competentAuthority` edges have no inverse. `verify_symmetry` walks only
`estleg:references` (`:876`), one of eight pairs, and `main()` returns 0
regardless — so the report's headline `symmetry_mismatches: 0` verifies an
eighth of the layer.

### W15 — Volatile fields churn tracked artefacts. Severity: LOW-MEDIUM

`wall_time_seconds`, `items_per_second`, `peak_memory_mb` and `pipeline_version`
are written into git-tracked coverage reports
(`extract_cross_references.py:2676-2679` and equivalents). AGENTS.md says
"Avoid timestamp-only churn". `extract_provision_versions_coverage.json` goes
further and carries a real wall clock, `2026-05-26T03:37:14.241023+00:00`, where
every sibling report pins.

### W16 — Test gaps that would have caught the above. Severity: MEDIUM

312 tests across the four in-scope test files pass in 2.0 s with zero skips in
this environment. What they do not guarantee (tests reviewer):

- No test probes the abbreviation left boundary. `grep MTÜS|boundary` finds
  nothing.
- No test asserts punkt capture in the in-law path.
- No test runs `extract_cross_references.main()` twice and diffs bytes, and no
  test asserts array ordering — tests at `:381` and `:1850` wrap results in
  `sorted(...)`, deliberately normalising order away. A refactor that reordered
  `estleg:references` would produce a multi-megabyte no-op diff in combined with
  a fully green suite.
- Chain integrity (one tail, acyclicity, monotonic `validFrom`) is asserted only
  against the `_three_redaction_chain()` fixture. Nothing validates the 4,422
  committed sidecars. No cycle test exists.
- `tests/test_generate_provision_versions.py:894` calls
  `pytest.skip("provision_versions_report.json not committed")` inside a test
  that is explicitly a release invariant (#430). The file is git-tracked and not
  LFS-filtered, so the only way it vanishes is a broken checkout — exactly when
  a failure is wanted. This violates AGENTS.md's rule against hiding missing
  corpus inputs behind `pytest.skip`.
- Cross-act IRI collisions in the amendment layer are untested, and one exists in
  the shipped corpus: `estleg:AmendmentChain_ROS` is emitted by both
  `amendments_rahvusooperi_seadus.json` and `amendments_riigi_oigusabi_seadus.json`
  (amendment reviewer).

---

## Improvement ideas

### 1. Stop asserting `inForce` without evidence; add a fourth honest state

**What.** Delete the unconditional `return "inForce"` in
`derive_act_temporal_status.residual_status` (`:142-153`) and have it return
`None` so the act keeps `unknown`. Reverse `process_deprecated_copy` (`:195-223`)
so a deprecated act with a `dcterms:isReplacedBy` successor is stamped
`repealed`, not the successor's status. Make `synthesise_versions` close the head
interval when the provision disappears from the newest redaction, so
`_provision_status` can actually reach `repealed`. Add a SHACL constraint
rejecting `owl:deprecated true` together with `temporalStatus "inForce"`.

**Why it matters for the public sector.** This is the field a KOV clerk or
Bürokratt uses to decide whether a rule binds. Today it says every one of 1,146
acts is in force, including 27 the graph itself marks superseded. Honest
`unknown` is usable; uniform false `inForce` is not, and it is the finding most
likely to end an adoption conversation.

**Effort:** S. **Impact:** H.
**Files:** `src/estleg/derive_act_temporal_status.py`,
`src/estleg/generate_provision_versions.py`, `shacl/estonian_legal_shapes.ttl`,
`tests/test_derive_act_temporal_status.py`.

### 2. Fix the abbreviation boundary as a class, and source the registry from RT

**What.** Add `(?<![A-Za-zÕÄÖÜŠŽõäöüšž0-9])` (or `\b`) to `_PAT_ABBREV`
(`extract_cross_references.py:1088-1091`), copying the guard already present at
`extract_court_provision_links.py:465`. Then derive `KNOWN_ABBREVIATIONS` from
`data/law_abbreviations.json`, preferring entries with `"source": "rt_api"`
(the official RT `lyhend`), keeping hand entries only as an explicitly annotated
alias table. Add a test asserting no key maps to a different law's official
`lyhend` — that test fails today on `TTKS`, `KELS` and `TKS`.

**Why it matters.** `MTÜS § 12 → TÜS` and `ELS § 7 → LS` are wrong-statute
citations shipped as fact. For a court or Õiguskantsler, a citation to the wrong
act is worse than no citation. Fixing it per-abbreviation, as commit `c96577d50c`
did, leaves ~1,000 unregistered abbreviations as live candidates.

**Effort:** S for the boundary, M for the registry migration. **Impact:** H.
**Files:** `src/estleg/extract_cross_references.py`,
`src/estleg/estleg_common.py`, `data/law_abbreviations.json`,
`tests/test_extract_cross_references.py`.

### 3. Wire the corpus-title map into the in-law citation pass

**What.** Thread `law_title_to_iri` (already built at `:2460-2463`) into
`_run_inlaw_citation_pass` and add a genitive pattern that matches any
`<words> seaduse|seadustiku|koodeksi §` and folds it through
`_genitive_law_ref_to_title` (`:350-362`), exactly as the preamble path does.
Report the resulting recall delta in `cross_references_report.json`.

**Why it matters.** Cross-act recall is currently ceilinged at the 74 laws in
`FULLNAME_GENITIVE` — 6.6% of the corpus — which is why the dependency graph has
only 5,545 cross-act edges. The infrastructure for ~1,000 laws is already built
and tested on the preamble side; it is one parameter away from the pass that
matters for "which provisions depend on § X".

**Effort:** M. **Impact:** H.
**Files:** `src/estleg/extract_cross_references.py`,
`tests/test_extract_cross_references.py`.

### 4. Ship a hand-adjudicated citation gold set and make precision a reported number

**What.** Populate a `eval/gold_sets/crossReferences.json` with 200–300
hand-verified citations sampled across enacted laws, KOV regulations and
Riigikohus judgments, each citing its Riigi Teataja basis, including negative
items (text that must produce no citation). Score it in
`scripts/eval_harness.py` and publish `precision` / `recall` per pattern family.
Replace the "100.0% edge resolution" line in `eval/FITNESS_REPORT.md`, which is
true by construction and inflated by the W10 placeholders, with the measured
numbers.

**Why it matters.** Question 1 of the review lens — "can a public body trust it"
— currently has no answer. Justiitsministeerium cannot base an impact assessment
on links of unknown precision, and the number that looks like an answer is a
tautology. This is also the artefact that makes every other fix measurable.

**Effort:** M for the harness, L for the adjudication (a legal-judgement
artifact, and `.github/CODEOWNERS` already routes `needs-legal-review`).
**Impact:** H.
**Files:** `eval/gold_sets/`, `scripts/eval_harness.py`,
`eval/FITNESS_REPORT.md`, `eval/README.md`.

### 5. Make the corpus reproducible and self-describing

**What.** Three parts. (a) Commit the RT XML fetch script (the API client
already exists in `src/estleg/riigiteataja_common.py`) so
`data/riigiteataja/*.xml` can be rebuilt, and add a guard that
`generate_amendment_history` Step 0 and `extract_cross_references` abort
non-zero when the XML lookup is below a threshold rather than silently emitting
an empty layer. (b) Add `generate_provision_versions.py`,
`derive_act_temporal_status.py`, `derive_court_interpretation_staleness.py` and
`materialize_combined_inverses.py` to `run_all_integration.STEPS` (or document
them as a named second stage in `docs/ARCHITECTURE.md`). (c) Add a CI gate that
every `pipeline_version` in `krr_outputs/reports/` resolves to a commit in this
repository — it fails today on six of twelve reports, three of them
unresolvable.

**Why it matters.** An official publisher or the state open-data portal must be
able to reconstruct an artefact from a stated commit and stated inputs. Today the
citation graph's provenance stamp names a commit that does not exist, and
`run_all_integration.py` on a fresh clone would silently produce a much smaller
citation layer while reporting success.

**Effort:** M. **Impact:** H.
**Files:** `scripts/` (new fetch script),
`src/estleg/run_all_integration.py`, `src/estleg/generate_amendment_history.py`,
`src/estleg/extract_cross_references.py`, `.github/workflows/validate.yml`,
`docs/ARCHITECTURE.md`.

### 6. Make the point-in-time layer say what it does not know

**What.** Four markers. (a) Stamp each sidecar's ontology header with
`estleg:versionHistoryKind` = `fullRedactionChain` | `currentSnapshotOnly`, so
the 3,680 snapshot sidecars are distinguishable from the 742 real histories.
(b) Close the head interval and emit `estleg:versionValidTo` when a provision
disappears from the newest redaction, so repealed provisions stop reading as
current. (c) Add a renumbering guard: when a paragraph key's text similarity
between consecutive redactions falls below a threshold *and* the neighbouring
key matches better, record `estleg:renumberingSuspected` rather than emitting a
version. (d) Stamp `estleg:assertionConfidence` via the existing
`estleg_common.stamp_assertion_confidence` on every diff-derived version.

**Why it matters.** A court needs the text in force on the date of the facts. It
can get that today only for 742 laws, only for provisions that still exist, and
with no signal when the paragraph was renumbered — which would hand it wrong
statutory text under a correct-looking `estleg:rtUrl` and date. Marking the
limits costs nothing and converts an unsafe artefact into a usable one.

**Effort:** M for (a), (b), (d); L for (c). **Impact:** H.
**Files:** `src/estleg/generate_provision_versions.py`,
`shacl/estonian_legal_shapes.ttl`, `docs/SCHEMA_REFERENCE.md`,
`tests/test_generate_provision_versions.py`.

### 7. Build the diff-able "what changed" product a ministry actually asks for

**What.** The substrate exists: `provision_versions/*.jsonld` carries
`versionText`, `versionValidFrom`/`ValidTo`, `supersededByVersion`, `rtUrl`. The
join functions `collect_versions_by_date` (`generate_amendment_history.py:241`),
`link_amendments_to_versions` (`:262-320`) and
`stamp_last_amendment_from_versions` (`:325-337`) exist but are **never called
from `main()`** — the only callers are a test and `validate_all.py:2810`. Wire
them into the per-`base_slug` loop, fix `totalAmendments` to count the final
graph (`:313-316`), then add a small CLI that answers "what changed in act X
between date A and date B" as provision-level old-text/new-text pairs with the
amending act's RT citation. Also parse the amendment TYPE from
`<muutmismarge>` (muudetud / täiendatud / kehtetuks tunnistatud / sõnastatud),
point `estleg:amends` at provision IRIs rather than only act roots, and emit the
publication date, which is currently read and discarded (`:524-540`).

**Why it matters.** "What changed in this act since 2020" is the single most
common question from a ministry legal department and from Riigikogu Kantselei,
and it is the one the current amendment layer cannot answer — it says when an act
was amended and by which RT publication, but not which § changed or how. Worse,
4,888 `_vf_` version-linked nodes already sit in 179 chain files that the
canonical builder cannot reproduce and will delete on the next full run
(`:1637-1657`).

**Effort:** L. **Impact:** H.
**Files:** `src/estleg/generate_amendment_history.py`,
`tests/test_generate_amendment_history.py`, new CLI under `scripts/`,
`docs/SCHEMA_REFERENCE.md`.

### 8. Close the inverse layer's gaps and make symmetry a gate

**What.** Generalise `verify_symmetry` (`generate_inverse_references.py:876`) to
loop `INVERSE_PAIRS` and exit non-zero on a genuine missing back-link. Add
`amends`/`amendedBy` (16,136 vs 174 today) and
`proposesToAmend`/`hasProposedAmendment` to `INVERSE_PAIRS`. Add a clear pass
for act-root aggregates before `materialize_act_root_aggregates` (`:1021`).
Re-serialize `combined_ontology.nt`/`.ttl`/`.nq` from the current `.jsonld` and
add a CI parity check on per-predicate triple counts across the four dumps.
Finally, correct `docs/API_GUIDE.md:247`, which claims the typed reference
family is emitted, to match `docs/SCHEMA_REFERENCE.md:752`.

**Why it matters.** The inverse layer is the one that answers "which provisions
depend on § X". Today a Jena consumer loading the N-Triples dump that
`README.md:96-98` recommends gets a citation graph missing every inverse
edge, `interpretsLaw` and `interpretedBy` disagree by 27,308 edges, and a legal
department following `API_GUIDE.md` will write a `repeals` query that silently
returns zero rows.

**Effort:** S for the parity gate and dumps, M for the new pairs and clear pass.
**Impact:** H.
**Files:** `src/estleg/generate_inverse_references.py`,
`src/estleg/materialize_combined_inverses.py`, `docs/API_GUIDE.md`,
`.github/workflows/validate.yml`, `krr_outputs/combined_ontology.{nt,ttl,nq}`.

### 9. Regularise unresolved-reference handling into one queryable product

**What.** Emit one `krr_outputs/unresolved_references.jsonld` that holds all
10,634 unresolved citations (7,548 in-law + 3,086 preamble) as reified
`estleg:Citation` nodes with `citationText`, `citationSource`, the pattern that
matched, and the reason resolution failed. Retire the 59 `estleg:VOS_Par_*`
`fallbackMaterialized` placeholders, which use a prefix no real provision uses
and evade the `estleg:isStubNode` filter that `docs/ARCHITECTURE.md` tells
consumers to apply; if they must stay, type them `estleg:isStubNode true`.

**Why it matters.** Unresolved citations are the highest-value work queue in the
project — each one is a real link a lawyer could add. Today they are scattered
across 16,061 peep files with no aggregate view, while the file named for them
holds 61 orphan placeholders that lead consumers into dead ends the documented
stub filter does not catch.

**Effort:** S. **Impact:** M.
**Files:** `src/estleg/extract_cross_references.py`,
`krr_outputs/unresolved_references.jsonld`, `docs/ARCHITECTURE.md`.

### 10. Close the specific test gaps that let these ship

**What.** Add: a parametrised left-boundary regression over all ten registered
suffix pairs plus `MTÜS § 12 → no TÜS match`; a punkt-capture assertion for the
in-law path; a corpus-level chain-integrity gate over all
`krr_outputs/provision_versions/*.jsonld` (exactly one tail per `versionOf`
group, no cycles, strictly increasing `validFrom`, `validTo[k] < validFrom[k+1]`);
a run-twice byte-identity test for `extract_cross_references.main()` with a
declared sort order for `estleg:references`; ~6 negative preamble samples with
`expected_count: 0`; and replace the `pytest.skip` at
`tests/test_generate_provision_versions.py:894` with a hard assertion.

**Why it matters.** All ten shipped defects above passed 312 green tests. The
suite is unusually thorough on parser mechanics and unusually thin on
corpus-level invariants and precision, which is exactly the inversion a
public-sector consumer cares about.

**Effort:** M. **Impact:** M–H.
**Files:** `tests/test_extract_cross_references.py`,
`tests/test_generate_provision_versions.py`,
`tests/fixtures/kov_layer2b/preamble_samples.json`.

---

## Open questions

1. **Was the shipped corpus produced from this repository at all?** Three
   `pipeline_version` stamps (`2074016e2`, `96abdac2f0`, `2d46eb74b`) do not
   resolve here. Were they produced in the `estonian-legal-ontology` checkout
   that `~/.claude.json` still points at, and if so, is that tree's history
   recoverable, or should the whole corpus be regenerated from HEAD before any
   public-body conversation?

2. **Is a full regeneration currently possible?** It needs the RT XML cache
   (gitignored, no committed fetch script) plus ~1h 46m of live
   riigiteataja.ee traffic for the version layer alone. Has a full cold rebuild
   ever been executed end to end, and what is the total wall time?

3. **What is the intended semantics of `estleg:temporalStatus` on a deprecated
   act?** `repealed` seems right, but `dcterms:isReplacedBy` plus
   `owl:deprecated` may already be the intended encoding, with `temporalStatus`
   reserved for RT `kehtivus`. If so, `process_deprecated_copy` should emit
   nothing rather than copying the successor's value.

4. **Should the KOV regulation corpus be in the same graph as enacted law?**
   11,059 of 16,061 peeps are KOV. The gates in
   `extract_cross_references.main()` (`:2692-2697`) treat KOV output as the
   pass/fail signal for the whole run, which means a regression in the 1,120
   enacted laws cannot fail the gate.

5. **Who owns the abbreviation registry?** `data/law_abbreviations.json` (601
   keys, RT-sourced) and `estleg_common.KNOWN_ABBREVIATIONS` (95 keys,
   hand-curated, three demonstrably wrong) disagree. AGENTS.md names the former
   as the registry of record but no code path consults it for citation
   resolution.

6. **Is there an appetite for a `estleg:assertionConfidence` policy across the
   whole enrichment stack**, or is the current position that only the three
   keyword classifiers carry it? `docs/SCHEMA_REFERENCE.md:22` reads as a
   general policy but is implemented in exactly one script.

7. **Does anyone downstream consume `combined_ontology.nt` / `.ttl`?** If the
   Seadusloome SPARQL path loads the JSON-LD, the stale dumps are a
   documentation bug; if a Jena instance loads the N-Triples, it is serving a
   citation graph with no inverse edges today.
