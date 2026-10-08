# EU transposition monitoring (issue #711)

This page is for two readers: the **consumer** who wants to know which EU
directives Estonia has transposed, and the **operator** who refreshes the
layer.

## What the corpus says, and what it does not

Every EU directive with a transposition deadline (2,599 today) carries
`estleg:transpositionStatus` with exactly one of three values:

| Value | Meaning |
|---|---|
| `transposed` | The corpus holds a transposing act. Either a CELLAR national implementing measure (NIM) for Estonia was matched to an Estonian law or state regulation (`estleg:transposedBy`), or an act's own Riigi Teataja *normitehniline märkus* names the directive (`estleg:transposesDirectiveAsserted`). |
| `no_measure_required` | No transposing act in the corpus, but Estonia notified CELLAR that no national measure is necessary ("MS does not consider NEM necessary"). |
| `no_evidence_in_corpus` | Neither of the above. |

There is **no "not transposed" value**, on purpose. `no_evidence_in_corpus`
is a statement about this corpus. The usual causes are a NIM title the
matcher could not resolve, a transposing act outside the corpus (repealed
regulations, KOV acts, a NIM that names an amending act), or a directive that
needs no Estonian measure and has no notification for it.

`transposed` likewise records *evidence of a measure*. It does not mean the
transposition is complete or correct.

The query "deadline past and no `transposedBy`" returned 2,368 directives on
2026-09-04. Reading those as infringements is the mistake this product exists
to prevent.

## Consumer surfaces

- **`krr_outputs/exports/transposition_gap.csv`.** One row per stamped
  directive, sorted by CELEX. The columns are `celex`, `directive_iri`,
  `title`, `in_force`, `transposition_deadline`, `deadline_passed`,
  `status_as_of`, `transposition_status`, `evidence`, `notified_acts`,
  `asserted_acts` and `eurlex_url`. `evidence` is a `;`-list of `cellar_nim`,
  `rt_ntm` and `cellar_no_measure_required`. `deadline_passed` is evaluated at
  the pinned build date in `status_as_of`, not at the day you read the file.
- **`krr_outputs/eurlex/eurlex_directives_peep.json`.** The same status as
  `estleg:transpositionStatus` on each directive node. It also appears in
  `eurlex_combined.jsonld`.
- **`krr_outputs/reports/transposition_mapping.json`.** The NIM-to-act
  mapping. Each row carries `matched_act_kind` (`law` or `regulation`) and
  `match_method` (`law`, `regulation_exact_title` or
  `regulation_near_title`). The report also lists the `no_measure_required`
  directives and a `transposition_status` block of counts.
- **`krr_outputs/reports/ntm_directives.json`.** Per act, the directives its
  normitehniline märkus asserts, diffed against the CELLAR-notified set
  (`asserted_not_notified`, `notified_not_asserted`).

SPARQL, using the asserted and notified edges together:

```sparql
PREFIX estleg: <https://w3id.org/estleg/>
SELECT ?directive ?status ?deadline WHERE {
  ?directive estleg:transpositionStatus ?status ;
             estleg:transpositionDeadline ?deadline .
  FILTER(?status = "no_evidence_in_corpus")
} ORDER BY ?deadline
```

## How matching works

1. **NIM rows** come from CELLAR `cdm:measure_national_implementing` for
   Estonia (`fetch_transposition_measures`). The raw rows and the directive
   deadlines are cached in `krr_outputs/reports/transposition_measures.json`
   on every online run.
2. **"No measure required" rows** are classified before matching. They are
   status evidence, not unmatched titles.
3. **Laws first.** Rows are matched against the `INDEX.json` law index (the
   #265/#288/#388 rules), so a law link never changes because regulations
   were added.
4. **Then state regulations.** Unmatched rows are matched against the
   `regulations/riik/` titles. An exact normalized title match wins. A near
   match is accepted only when one title's words contain the other's, the
   difference is at most 2 words or a fifth of the longer title, at least 3
   words are shared, and the character ratio is at least 0.85. A title shared
   by several regulations, or a tie, is `ambiguous` and gets no link.
   - **Caveat.** A NIM titled like a regulation that RT later re-issued under
     a new terviktekst ID links to the in-force regulation of that title. Most
     NIM titles name an older redaction or predecessor.
5. **NTM assertions** (`extract_ntm_directives.py`) parse the
   `<normtehnmarkus>` block of cached RT XML. Only the first instrument of each
   `;`-separated item counts, and only if it is a directive. So "…, millega
   muudetakse direktiivi 2005/35/EÜ" is not a second assertion.
   `92/43/EMÜ` becomes `31992L0043` and `(EL) 2015/849` becomes `32015L0849`.

## Operator runbook

Offline, from the corpus as it is (re-derives the status, the CSV and the
report's status block):

```bash
python3 scripts/extract_ntm_directives.py              # cached RT XML in data/riigiteataja/
python3 scripts/generate_transposition_mapping.py --status-only
```

Full refresh (network: CELLAR SPARQL and the Riigi Teataja public API). Run
the two commands in this order:

```bash
python3 scripts/extract_ntm_directives.py --fetch      # current XML of every law + state regulation
python3 scripts/generate_transposition_mapping.py      # NIMs + deadlines, match, write, status, CSV
```

After a full refresh, later reruns need no network:

```bash
python3 scripts/generate_transposition_mapping.py --offline
```

Every mode is byte-idempotent on unchanged inputs. Afterwards run
`scripts/check_phantom_typing.py --all`,
`scripts/shacl_validate_all.py --bucket eurlex` and `--bucket laws`, and
rebuild `combined_ontology.jsonld` with the canonical builder.

## Coverage as shipped (build date 2026-06-01)

The NIM cache did not exist before #711 and the matcher was not re-run
online. The NIM-to-act pairs are therefore still the 2026-06-01 CELLAR
snapshot: 294 pairs over 206 directives. The regulation index and the "no
measure required" classification take effect on the next online run. On the
15 unique titles in the old unmatched sample, the regulation index resolves 5
(including Reg_1032308) and classifies 1 as "no measure required".

Only one RT XML is cached in the repo (`karistusseadustik.xml`), so the NTM
assertions cover KarS alone until `--fetch` runs.
