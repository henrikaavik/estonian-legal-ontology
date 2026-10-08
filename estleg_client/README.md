# estleg-client

Read-only Python client for the [Estonian Legal Ontology](https://github.com/henrikaavik/estonian-legal-ontology):
enacted laws, state and municipal (KOV) regulations, Riigikohus decisions,
EIS drafts and EUR-Lex acts as `rdflib` graphs and as typed rows ready for CSV,
pandas or a BI tool. Its only dependency is `rdflib`.

```bash
pip install estleg-client
```

## Getting the corpus

The client reads a local copy of the corpus. It looks for one in this order:

1. `root=` passed to any loader.
2. `$ESTLEG_CORPUS_ROOT` (the older `$ESTLEG_CORPUS` also works).
3. A git checkout containing the installed package or the current directory.
4. The newest release that `fetch_corpus()` placed in the user cache
   (`~/Library/Caches/estleg`, `~/.cache/estleg`, `%LOCALAPPDATA%\estleg\Cache`,
   or `$ESTLEG_CACHE_DIR`).

If none is found, `corpus_root()` raises `CorpusNotFoundError` naming
`fetch_corpus`. That class subclasses `FileNotFoundError`.

```py
from estleg_client import fetch_corpus

root = fetch_corpus()                  # v1.0.0 into the user cache; verified with SHA256SUMS
root = fetch_corpus("./estleg-data", version="v1.0.0")   # or anywhere; then set ESTLEG_CORPUS_ROOT
```

`fetch_corpus` downloads `SHA256SUMS` and the assets from the GitHub Release.
It checks every SHA-256 and fails closed if a file is unlisted or mismatched.
It then unpacks the gzipped JSON-LD aggregates into the `krr_outputs/` layout
and splits `combined_ontology.jsonld` into one shard per law in a single
streaming pass. Last, it writes an `estleg_corpus.json` manifest. Downloads
already present with the right hash are skipped.

| Asset | Default | Gives you |
|---|---|---|
| `combined_ontology.jsonld.gz` | required | enacted laws + sanctions (`load_law`, rows) |
| `eelnoud_combined.jsonld.gz` | required | drafts (`load_draft`, `iter_drafts`) |
| `eurlex_combined.jsonld.gz` | required | EU acts (`load_eu_act`, `iter_eu_acts`) |
| `INDEX.json`, `LICENSE`, `NOTICE`, `DATA_RIGHTS.md`, `DATA_PROTECTION.md`, `controlled_vocabulary.jsonld` | fetched when the release lists them | slug lookup, licence and personal-data notices |

A release carries regulations and Riigikohus decisions only inside the
`estleg_all.nq.gz` N-Quads dump. Their loaders need a git checkout, which has
the per-act peep files, and raise `CorpusUnavailableError` on a release
download. The same CLI command is `estleg-load download [--dest DIR] [--version v1.0.0]`.

## The five corpora

Each corpus has one `load_*` function that returns an `rdflib.Graph` for a
record. It also has one `iter_*` function that yields typed rows straight
from the JSON files, without parsing through rdflib.

```py
from estleg_client import (
    load_law, load_regulation, load_court_decision, load_court_decisions,
    load_draft, load_eu_act, iter_regulations, iter_court_decisions,
    iter_drafts, iter_eu_acts, provisions_of, sanctions_of,
)

law = load_law("ABIPOL")                         # INDEX slug, abbreviation or title substring
print(len(provisions_of(law)), len(sanctions_of(law)))

reg = load_regulation("1057801")                 # RT terviktekst id
reg = load_regulation("119032025005")            # RT global id (riigiteataja.ee/akt/<id>)
kov = load_regulation("1039736", kov=True)       # municipal regulation
print(len(provisions_of(kov)))                   # KovProvision paragraphs

decision = load_court_decision("3-18-1432/93")   # case number, ECLI or RK_... IRI
year_2020 = load_court_decisions(2020)
draft = load_draft("JDM/26-0214")                # EIS number, Draft_... IRI or title
eu = load_eu_act("31981L0643")                   # CELEX
print(len(decision), len(year_2020), len(draft), len(eu))

tallinn = list(iter_regulations(kov=True, issuer="Tallinna Linnavalitsus"))
rulings = list(iter_court_decisions(year=2020))
consultations = list(iter_drafts(phase="PublicConsultation"))
transposed = list(iter_eu_acts(estonia_relevant=True))
print(len(tallinn), len(rulings), len(consultations), len(transposed))
```

`resolve_iri("estleg:Reg_1057801_Par_1")` loads whichever record owns an IRI.
It handles the `Reg_`, `RK_`, `Draft_` and `EU_` families and law-abbreviation
prefixes, then reports whether the node exists.

## Rows

Rows are frozen dataclasses. Every row has an `iri` field, which is the full
`https://w3id.org/estleg/...` IRI as a `str`. `as_dict()` returns CSV- and
JSON-ready values. `Row.columns()` gives the header.

| Row | Built from | Key fields |
|---|---|---|
| `ProvisionRow` | `LegalProvision` / `KovProvision` (and `Subsection` on request) | `act_prefix`, `paragraph`, `subsection`, `label`, `summary`, `legal_text`, `is_kov`, `temporal_status`, `valid_from`, `valid_to`, `source_url` |
| `SanctionRow` | `Sanction` | `sanction_type`, `min_amount`, `max_amount`, `unit`, `currency`, `amount_eur`, `min_amount_eur`, `max_amount_eur`, `subject`, `subject_source`, `provision_iri`, `source_url` |
| `CitationRow` | `Citation` | `source_iri`, `target_iri`, `text`, `detail` |
| `RegulationRow` | `NationalRegulation` / `MunicipalRegulation` | `rt_id`, `global_id`, `title`, `issuer`, `is_kov`, `municipality_iri`, `temporal_status`, `valid_from`, `valid_to`, `provision_count`, `source_url` |
| `DecisionRow` | `CourtDecision` | `case_number`, `ecli`, `decision_date`, `year`, `case_type`, `decision_type`, `chamber`, `judges`, `summary`, `legal_text`, `interprets`, `source_url` |
| `DraftRow` | `DraftLegislation` | `eis_number`, `title`, `phase`, `draft_type`, `initiator`, `publication_date`, `amends_law_iris`, `source_url` |
| `EuActRow` | `EULegislation` | `celex`, `title`, `doc_type`, `document_date`, `in_force`, `eli`, `transposition_deadline`, `transposed_by`, `estonia_relevant`, `source_url` |

```py
import csv, sys
from estleg_client import load_law, iter_rows, provision_rows, SanctionRow

law = load_law("abipolitseiniku_seadus")
writer = csv.DictWriter(sys.stdout, fieldnames=SanctionRow.columns())
writer.writeheader()
for row in iter_rows(law, "sanctions"):
    writer.writerow(row.as_dict())

first = provision_rows(law)[0]
print(first.paragraph, first.label, first.source_url)
```

The `iter_rows(graph, kind)` kinds are `provisions`, `subsections`,
`sanctions`, `citations`, `regulations`, `decisions`, `drafts` and `eu_acts`.
`provision_rows`, `sanction_rows`, `citation_rows`, `regulation_rows`,
`decision_rows`, `draft_rows` and `eu_act_rows` return lists of the same rows.

Temporal fields on a `ProvisionRow` come from its act. The corpus records
`temporalStatus`, `entryIntoForce` and `repealDate` per act, not per paragraph.

### Sanction amounts in euros

* `monetary` amounts in `EUR` are used unchanged. Amounts in `EEK` are divided
  by the fixed rate 15.6466.
* `fine_units` (trahviühik) are multiplied by 4 EUR, per KarS § 47 lg 1.
* `daily_rates`, `percent_of_turnover`, `years` and `days` are not money
  amounts, so their `*_eur` fields are `None`.
* `amount_eur` is the upper bound, the same value as `max_amount_eur`.

The corpus has no explicit subject property, so `subject` is inferred and
`subject_source` is `"inferred"`. Fine units, daily rates, imprisonment and
arrest give `natural_person`. Fines or pecuniary punishments in euros or as a
share of turnover, and compulsory dissolution, give `legal_person`. Coercive
payments and confiscation give `None`.

## Exact type matching

`has_type(node, *types)` matches `rdf:type` by exact IRI. It works on a JSON-LD
dict, whose `@type` may be a string or a list. It also works on an IRI with
`graph=`. A type can be written as `estleg:Sanction`, as `Sanction` or as a
full IRI. `provisions_of` returns `LegalProvision` and `KovProvision` nodes
and leaves out `Subsection` nodes. The release aggregate also types each
subsection as `LegalProvision`, and this rule keeps a checkout and a release
download in agreement. Pass `include_subsections=True` to get both levels. `sanctions_of` returns only
`Sanction` nodes and never a `SanctionType`. Before 1.0 a substring test was used.

```py
from estleg_client import has_type

node = {"@id": "estleg:Reg_1_Par_1", "@type": ["owl:NamedIndividual", "estleg:KovProvision"]}
print(has_type(node, "LegalProvision", "KovProvision"), has_type(node, "Provision"))  # True False
```

## Command line

```bash
estleg-load ABIPOL                                     # triples / provisions (pre-1.0 form)
estleg-load law ABIPOL --rows sanctions > sanctions.csv
estleg-load regulation 1039736 --kov --rows provisions --format jsonl
estleg-load decisions --year 2020 > riigikohus_2020.csv
estleg-load drafts --phase PublicConsultation
estleg-load eu-acts --estonia-relevant
estleg-load download --version v1.0.0
estleg-load where
```

## Typing and stability

* The package ships `py.typed`. Every public function and row field is
  annotated, IRIs are `str`, and dates are ISO `YYYY-MM-DD` strings.
* The public API is the set of names in `estleg_client.__all__` plus the row
  field names. It follows semantic versioning from 1.0.0 on.
* Removing or renaming a public name, a row field or a CLI flag, or changing
  a field's type, requires a new **major** version.
* New loaders, row fields, filters and CLI sub-commands come in **minor**
  versions. New row fields are always appended after the existing ones.
* Bug fixes come in **patch** versions.
* Modules and names that start with `_` are private.
* The client version is independent of the corpus version. The corpus follows
  its own semver and `docs/STABILITY.md`, and you pick the corpus with
  `fetch_corpus(version=...)`.
* Releases are tagged `client-v<version>`. They are published to PyPI by the
  `publish-client.yml` workflow through trusted publishing.
