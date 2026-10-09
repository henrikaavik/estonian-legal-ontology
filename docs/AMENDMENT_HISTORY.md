# Amendment history and release deltas

This page describes how the corpus records *what changed in a law* (the
amendment chains) and *what changed between two ontology releases* (the
release delta). Issues: #423, #429, #587, #704, #713.

## The amendment model

Every enacted law with amendments has one chain file,
`krr_outputs/amendments/amendments_<base_slug>.json`. Multipart laws
(`…_osa1` … `…_osaN`) share one chain under the base slug. The chain holds
three kinds of nodes.

| Node | `@id` shape | Source |
|---|---|---|
| Chain header (`owl:Ontology`) | `estleg:AmendmentChain_<ABBREV>` | generator |
| `estleg:AmendmentEvent` (RT-derived) | `estleg:Amendment_<ABBREV>_<md5-10>` | RT XML `<muutmismarge>` |
| `estleg:AmendmentEvent` (version layer) | `estleg:Amendment_<ABBREV>_vf_YYYYMMDD` | `provision_versions/<base>.jsonld` |
| `estleg:ProposedAmendment` | `estleg:AmendmentLink_<draft>_<ABBREV>` | EIS drafts, never enacted (#423) |

An **RT-derived event** is one amending act. Riigi Teataja repeats the same
act on every provision it touched, so markers are deduplicated by the RT
publication reference (`RT I, 2004, 46, 329`), or by the
`(aktikuupaev, joustumine, aktViide)` tuple when the reference is incomplete
(#263). The hash suffix of the `@id` is computed from that identity, so a
regeneration keeps every `@id` stable. Each event carries:

- `estleg:amends`, the act root or roots plus, since #713, the provisions
  (described in the next section);
- `estleg:amendmentDate` (`aktikuupaev`), `estleg:entryIntoForce`
  (`joustumine`), `estleg:rtReference`, `estleg:amendingAct` (#587);
- `estleg:publicationDate`, the RT `avaldamineKuupaev`, when the marker
  has one (#713);
- `estleg:isCurrentAmendment` on the latest validly dated event (#389, #587);
- `estleg:resultedInVersion`, the `estleg:ProvisionVersion` nodes that took
  effect on the event's date (#429).

A **version-layer event** (`_vf_`) is minted for each `versionValidFrom` date
in the law's provision-version sidecar that no RT-derived event covers. It
carries the act-root `amends`, `entryIntoForce` and `resultedInVersion`. The
chain header's `estleg:totalAmendments` is then the number of distinct
version dates.

The act root of every part gets `estleg:amendedBy`, pointing at the
RT-derived events only. It also gets `estleg:lastAmendmentDate`, the latest
`versionValidFrom`.

### Provision-level `estleg:amends` (#713)

A `<muutmismarge>` nested under a `paragrahv` is provision-level. The marker
can sit directly in the `paragrahv`, or inside its `loige`, `alampunkt` or
`sisuTekst`. The generator records the paragraph and lõige suffixes with the
same `estleg.law_structure` helpers the law builder uses
(`_paragraph_id_suffix`, `build_subsections`). It then resolves them against
the provision IRIs that actually exist in the law's peep(s):

- a lõige marker resolves to the `estleg:Subsection`
  (`…_Par_<n>_Lg_<m>`); an `alampunkt` resolves to its lõige, because items
  have no IRI of their own;
- a paragraph marker, or a marker on a lõige with no Subsection node (a
  repealed lõige has no text), resolves to the `estleg:LegalProvision`
  (`…_Par_<n>`);
- a suffix that matches zero or several nodes, such as a duplicate `§`
  number with the `_x2` suffix, is dropped and counted as `unresolved`.
  The generator never hand-rolls an IRI.

The act-root edge is kept and always comes first. Without provision markers,
the payload keeps the old shape exactly: one object for a single-part law, a
list of part roots for a multipart law. With provision markers it becomes a
list of the act root(s) followed by the provision IRIs:

```json
"estleg:amends": [
  {"@id": "estleg:KARIST_2_Osa1"}, {"@id": "estleg:KARIST_2_Osa2"},
  {"@id": "estleg:KARIST_2_Osa1_Par_7"}, {"@id": "estleg:KARIST_2_Osa1_Par_7_Lg_1"}
]
```

A post-2010 marker often has no `aktikuupaev` and no `RTaasta`/`RTnr`, only
`avaldamineKuupaev`, `RTartikkel`, `aktViide` and `joustumine`. Such a marker
cannot be an event of its own. Its provision and kind are folded into the
event of the same amending act, matched by a unique `aktViide`, without
adding or re-keying any event. With `modern_rt_citations=True`, the extractor
instead keys such markers on the modern citation `RT I, 05.07.2013, 2`. That
option is off by default because it re-keys the events those acts already
have (25 on KarS).

### Amendment kind

The RT XML has no structured *muudetud / täiendatud / kehtetuks tunnistatud*
field; on KarS these tokens appear only as free text. The generator infers a
kind only where a token is present: in the marker's own `tavatekst` (for
example `Kehtetu -`, or a Riigikohus invalidation note) or in the first words
of the provision that carries the marker. The values reuse the
`estleg:changeType` vocabulary: `repeals`, `supplements` and `amends`.
`jõustumisaeg muudetud` is an entry-into-force change, so it is never counted.

`estleg:changeType` is draft-scoped (`rdfs:domain estleg:DraftLegislation`),
so it cannot be reused on events. The controlled vocabulary, the SHACL
`AmendmentEventShape` and `SCHEMA_REFERENCE` now declare
`estleg:amendmentKind`. The kind is computed and counted in the report, and
`--emit-amendment-kind` emits it. It stays opt-in until the operator chain
refresh: regenerating the committed chains already changes them beyond the new
key (`@context` drift on every chain, and the pending provision-level `amends`
on KarS), so flipping the default would not be a key-only change. On the
three fixed-point chains the flag adds the key and nothing else (11 KarS
events). Whether RT can
supply the kind as a structured field is an open question for RIK/RT (#713).

### KarS measurements (committed `data/riigiteataja/karistusseadustik.xml`)

| Measure | Value |
|---|---|
| `<muutmismarge>` markers | 528 |
| provision-level markers (under a `paragrahv`) | 424 |
| RT-derived events after dedupe | 87 |
| events with provision IRIs in `amends` | 72 |
| provision `amends` edges | 421 (364 Subsection, 57 LegalProvision, 0 unresolved) |
| events with `publicationDate` | 25 |
| markers with a kind token | 36 (all `repeals`), 11 events |
| `_vf_` events kept on regeneration | 45 of 45, all `@id`s unchanged |

## Regenerating

```bash
python3 scripts/generate_amendment_history.py          # chains + version join
python3 scripts/generate_amendment_history.py --emit-amendment-kind
python3 scripts/link_amendment_versions.py --check     # fixed-point gate
python3 scripts/link_amendment_versions.py             # repair after a sidecar-only rerun
```

`generate_amendment_history.main()` runs the version join itself through
`apply_version_join`. It joins every enacted-law group, but never regulations
(#431), that has a `provision_versions/<base>.jsonld` sidecar. A regeneration
therefore can no longer drop `_vf_` events or `resultedInVersion` stamps.
`link_amendment_versions` re-runs the same function. On a corpus produced by
the canonical path it changes nothing: 5,647 chain files, 179 joined chains,
0 changes. `--check` exits 1 when that is not true.

**Operator refresh.** The only RT law XML in the repository is KarS. The
generator pairs every other law with its XML from `data/riigiteataja/`, and a
run without the full XML cache rewrites those chains with no RT-derived
events. Before running it corpus-wide, fetch the act XML through the RT
public API (`/public-api/api/v1/akt/{id}/xml`, #691) for every law in INDEX.
Then run `generate_provision_versions.py`, `generate_amendment_history.py`
and `link_amendment_versions.py --check`. That refresh is the step that
publishes provision-level `amends` corpus-wide. The committed chains keep
act-level `amends` until then.

## Release delta

```bash
python3 scripts/emit_release_changes.py                         # latest v* tag → working tree
python3 scripts/emit_release_changes.py --from-ref v1.0.0 --to-ref HEAD
python3 scripts/emit_release_changes.py --mode index-deprecated  # the #549 snapshot
```

The default mode compares every law peep listed in `INDEX.json` at the
previous release tag (`git tag --sort=-v:refname`, read with
`git cat-file --batch`) with the same law in the working tree. Git LFS
aggregates are not needed. The comparison covers provision nodes
(`estleg:LegalProvision`, `estleg:Subsection`) keyed on `@id`:

- **added** and **removed** mean the IRI exists on one side only;
- **changed** means `estleg:legalText`, `estleg:summary` or a temporal field
  (`entryIntoForce`, `repealDate`, `temporalStatus`, `validFrom`,
  `validUntil`, `owl:deprecated`) differs. Other enrichments, such as
  citations, similarity links and classifier output, are not provision
  changes;
- a removed IRI and an added IRI of one law that share the `_Par_…` tail and
  every tracked field are the same provision under a new IRI. They are linked
  by `replacedBy` and `replaces`, and their counts are not merged;
- laws are classified as added, removed or deprecated (`deprecated_laws` in
  INDEX);
- added and changed provisions carry `versionValidFrom`, the latest version
  date in the working-tree sidecar, when there is one.

Outputs:

| File | Content |
|---|---|
| `krr_outputs/changes-<version>.jsonld` | `dcat:Dataset` + `estleg:ReleaseDelta`: `comparedFrom`, `comparedTo`, the `added`/`removed`/`changed` counts and law lists. The IRI lists are inline and uncapped while their total is at most 10,000 (`JSONL_THRESHOLD`). |
| `krr_outputs/changes-<version>.jsonl` | Written only above the threshold. One JSON object per change, with keys `change`, `law`, `iri`, `fields`, `versionValidFrom`, `replacedBy` and `replaces`. Above the threshold the JSON-LD carries `estleg:listedInline false` and a `dcat:distribution` pointing at the JSONL file. |
| `krr_outputs/reports/release_changes_report.json` | Counts, the refs and commits compared, field-level counts, top laws. Stamped `generated` = `BUILD_EVALUATION_DATE` (#295). |

The committed `changes-1.0.0.jsonld` compares v1.0.0 (f018cf05f2) with the
wave-3 tree (c0140d497e). It lists 579 added, 441 removed and 463 changed
provisions in 8 laws, and runs in about 5 s. 429 of the removals are Tier-1
IRI-collision re-prefixes, for example `ROS_Par_1` to `ROS_2_Par_1`. All 463
changes are `estleg:summary`. The file is named after `ONTOLOGY_VERSION`.
Re-run it with `--version <next>` when the version is bumped.

## What remains

- Default `--emit-amendment-kind` on at the operator chain refresh.
- RIK/RT must confirm whether amendment kind is a structured XML field.
- The operator XML refresh described above.
