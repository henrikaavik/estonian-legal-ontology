# Estonian Legal Ontology

A comprehensive, machine-readable ontology of Estonian and EU legislation in JSON-LD format. Maps **enacted laws**, **draft legislation**, **domestic regulations (määrused)**, **Supreme Court decisions**, **EU legal acts**, and **EU court decisions** into a semantic knowledge graph suitable for advanced search, cross-referencing, and automated legal analysis.

**Eestikeelne ülevaade:** [loe ontoloogia ülevaadet veebina](https://htmlpreview.github.io/?https://github.com/henrikaavik/estonian-legal-ontology/blob/main/docs/eesti-oigusontoloogia-ulevaade.html) — mis see on, kuidas see töötab, kust andmed pärinevad, kuidas seda uuendada ning kuidas ministeeriumid seda kasutada saaksid.

<!-- counts: keep in sync with metadata.jsonld estleg:statistics — validate_all.py::validate_metadata_catalog enforces metadata.jsonld vs the corpus, and tests/test_validate_all.py::test_readme_counts_match_metadata enforces README vs metadata.jsonld -->
**Status: 1,122 enacted laws (1,190 law files) + 22,832 drafts + 3,812 state regulations + 11,059 municipal regulations (opt-in) + 12,137 court decisions + 33,242 EU acts + 22,290 EU court decisions** | **23,116 JSON/JSON-LD files** | **170,000+ semantic nodes**

The headline file count includes generated reports, indexes, and metadata that
release validators intentionally skip. `validate_all.py` currently validates
23,069 corpus JSON/JSON-LD files; full SHACL loads 23,064 shape-relevant files.

**Query layer:** [`mcp_server/`](mcp_server/) (`estleg-mcp`, 15 tools). Local stdio or `https://estleg.sixtyfour.ee/mcp`. See [`mcp_server/README.md`](mcp_server/README.md). There is no REST `/api` surface; [`docs/API_GUIDE.md`](docs/API_GUIDE.md) is SPARQL/load guidance.

**Integration features:** Cross-law reference links | Court decision → provision links | EU directive transposition mapping | EuroVoc taxonomy | Amendment history | Legal concept graph | Deontic classification | Institutional competence | Sanction index | Semantic similarity | Temporal validity

Domestic regulations participate in the same cross-reference, EuroVoc, deontic, sanction, and similarity passes as enacted laws — the integration scripts iterate via `iter_peep_files()` from `estleg_common`, so any pipeline that runs over `*_peep.json` automatically covers laws and regulations alike.

## Prerequisites

Install Git LFS before cloning or validating the full repository:

```bash
git lfs install
```

Large generated artifacts such as `krr_outputs/combined_ontology.jsonld` are
stored through LFS; without it, validation commands will see pointer files
instead of JSON-LD.

## Quick Start

### 5-minute start

Three commands — then answered sentences, not a triple dump:

```bash
git lfs pull -I krr_outputs/combined_ontology.jsonld
python3 -m pip install 'rdflib>=7.1,<8'
python3 examples/quickstart.py
```

`examples/quickstart.py` prefers `krr_outputs/combined_ontology.jsonld` when
Git LFS has materialised it. Without LFS it falls back to
`perekonnaseadus_peep.json` plus `krr_outputs/sanctions/` and still prints
an answer. Optional: `python3 examples/quickstart.py --graph PATH`.

### Python client

```python
from estleg_client import load_law

g = load_law("abipolitseiniku_seadus")  # or "ABIPOL"
```

`pip install -e .` also installs console scripts: `estleg-load`
(`estleg-load abipolitseiniku_seadus` prints triple and provision counts),
`estleg-generate-laws`, `estleg-run-pipeline`, and `estleg-validate`.
Producer modules live in `src/estleg/`; `scripts/` keeps one-release shims.

### Load surfaces

The corpus is published as two nested load surfaces — pick the one your query needs:

- **Combined-only surface** — `krr_outputs/combined_ontology.jsonld` loaded alone. A self-contained graph (zero dangling `estleg:` references): all enacted-law nodes, the fully-merged enrichment overlays (sanctions, legal concepts, institutions, annotations, amendments), and the curated TBox. Every cross-corpus entity it references — court decisions, EU acts, drafts, municipal (KOV) regulations, the EHAK municipality registry, and per-provision version history — is present as a **resolvable stub** (label + identifier + the SHACL-required structural edges), *not* its full body. Best for law-centric queries and a quick, single-file load.
- **Full public load surface** — `combined_ontology.jsonld` **plus** the sidecar directories under `krr_outputs/` (`riigikohus/`, `curia/`, `eurlex/`, `eelnoud/`, `concepts/`, `sanctions/`, `amendments/`, `institutions/`, `provision_versions/`, `annotations/`, `harmonisation/`, `regulations/`) and `data/ehak/`. This is where the **full bodies** live — court-decision and EU-act text, the ~116k municipal `estleg:KovProvision` bodies, the version-history (point-in-time) layer, and the municipality/successor registry. Load this surface for full-text, point-in-time, or municipal-provision queries.

A SPARQL example that joins onto a court/EU/KOV/version body needs the full surface; one that stays within laws + overlays works on combined alone.

### RDF serializations

JSON-LD is the source format. Triplestore bulk loaders (Jena `tdbloader`,
Blazegraph DataLoader, Virtuoso, GraphDB) prefer N-Triples or named-graph
N-Quads.

**Measured load budget** (`combined_ontology.jsonld`, rdflib, ticket #541):
2,247,778 triples, 44.4 s, peak RSS 3,089 MB (~12.4× the ~250 MB file).
The combined file is a single top-level `@graph` array (not
line-streamable), so JSON-LD load is all-or-nothing.

Prefer `.nt` / N-Quads for bulk load. The committed N-Triples dump is
2,664,215 triples. Generate with `scripts/serialize_corpus.py`:

```bash
python3 scripts/serialize_corpus.py \
  --input krr_outputs/abipolitseiniku_seadus_peep.json \
  --format nt \
  --output krr_outputs/exports/abipolitseiniku_seadus.nt
```

A small committed proof dump is
`krr_outputs/exports/abipolitseiniku_seadus.nt`. Combined dumps are
LFS-tracked next to the JSON-LD source:

- `krr_outputs/combined_ontology.nt` — N-Triples (bulk load)
- `krr_outputs/combined_ontology.nq` — named-graph N-Quads
- `krr_outputs/combined_ontology.ttl` — Turtle (regenerate if absent)

```bash
python3 scripts/serialize_corpus.py \
  --input krr_outputs/combined_ontology.jsonld \
  --format nq \
  --output krr_outputs/combined_ontology.nq
```

N-Quads wrap triples in a named graph
(`https://w3id.org/estleg/graph/combined` for combined, or
`https://w3id.org/estleg/graph/<filename-stem>` otherwise).

### Tabular export

A star-schema projection for pandas/R lives in
[`krr_outputs/exports/`](krr_outputs/exports/) (`laws.csv`,
`provisions.csv`, `citations.csv`, `sanctions.csv`,
`court_decisions.csv`). The committed CSVs are a small real sample —
one enacted act, its sanctions sidecar, and one Riigikohus year —
flattened from those peeps, not from `combined_ontology.jsonld`.

```bash
# regenerate the committed sample (default globs)
python3 scripts/serialize_tabular.py --out krr_outputs/exports

# fuller dump (all enacted-law peeps + sanction sidecars + all Riigikohus years)
python3 scripts/serialize_tabular.py --out /tmp/estleg-tabular \
  --laws-glob '*_peep.json' \
  --sanctions-glob 'sanctions/sanctions_*.json' \
  --court-glob 'riigikohus/riigikohus_*_peep.json'
```

CSV is always written (stdlib `csv`). Parquet is written when `pyarrow`
is importable; otherwise the exporter prints a one-line note and
continues. Do not commit a full-corpus dump.

### Load a single file with Python (rdflib)

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/perekonnaseadus_peep.json", format="json-ld")

for s, p, o in g:
    print(f"{s} -> {p} -> {o}")
```

### Load draft legislation

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/eelnoud/eelnoud_combined.jsonld", format="json-ld")
print(f"Draft triples: {len(g)}")
```

### Load domestic regulations

```python
from pathlib import Path
from rdflib import Graph, Namespace, RDF

ESTLEG = Namespace("https://w3id.org/estleg/")

g = Graph()
for path in sorted(Path("krr_outputs/regulations/riik").glob("*_peep.json")):
    g.parse(path, format="json-ld")

count = sum(1 for _ in g.triples((None, RDF.type, ESTLEG.NationalRegulation)))
print(f"State regulations loaded: {count}")
```

### Load court decisions

```python
from rdflib import Graph

g = Graph()
for year in range(2020, 2027):
    g.parse(f"krr_outputs/riigikohus/riigikohus_{year}_peep.json", format="json-ld")
print(f"Court decision triples: {len(g)}")
```

### Load EU legislation

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/eurlex/eurlex_combined.jsonld", format="json-ld")
print(f"EU legislation triples: {len(g)}")
```

### Load EU court decisions

```python
from rdflib import Graph

g = Graph()
g.parse("krr_outputs/curia/curia_combined.jsonld", format="json-ld")
print(f"EU court decision triples: {len(g)}")
```

### Query cross-law references

```python
from rdflib import Graph, Namespace

ESTLEG = Namespace("https://w3id.org/estleg/")
g = Graph()
g.parse("krr_outputs/karistusseadustik_peep.json", format="json-ld")

# Find all provisions this law references in other laws
for s, p, o in g.triples((None, ESTLEG.references, None)):
    print(f"{s} references {o}")
```

### Query court decision → provision links

```python
from rdflib import Graph, Namespace

ESTLEG = Namespace("https://w3id.org/estleg/")
g = Graph()
g.parse("krr_outputs/riigikohus/riigikohus_2025_peep.json", format="json-ld")

# Find all provision-level links
for s, p, o in g.triples((None, ESTLEG.interpretsLaw, None)):
    print(f"Decision {s} interprets {o}")
```

### How to cite

Aavik, Henrik. (2026). *Estonian Legal Ontology* (Version 0.11.0) [Data set].
https://w3id.org/estleg/0.11.0

```bibtex
@misc{aavik_estonian_legal_ontology_2026,
  author  = {Aavik, Henrik},
  title   = {Estonian Legal Ontology},
  year    = {2026},
  version = {0.11.0},
  url     = {https://w3id.org/estleg/0.11.0},
  note    = {Dataset}
}
```

See [`CITATION.cff`](CITATION.cff) for the machine-readable record. Pin the
graph by `owl:versionIRI` (`https://w3id.org/estleg/0.11.0`), not an undated
clone of `main`. A Zenodo DOI is tracked as #473 and is not yet minted.
Consumer contract: [`docs/STABILITY.md`](docs/STABILITY.md). Architecture:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Refresh SLA

Corpus target is monthly Riigi Teataja consolidation.
`estleg:kehtiv` is the snapshot date the committed act text is valid as of.
`dcterms:accrualPeriodicity` is monthly. The content-staleness canary is
`python3 scripts/check_rt_staleness.py` (offline; `--fetch` is operator-run).
Inter-release IRI deltas ship as `krr_outputs/changes-0.11.0.jsonld`.

### Vocabulary cheat-sheet

| Need | Predicate / class |
|---|---|
| This § belongs to which act? | `estleg:partOfAct` (IRI) — never `estleg:sourceAct` |
| Incoming / outgoing cites | `estleg:referencedBy` / `estleg:references` |
| Court interprets this § | `estleg:interpretedBy` / `estleg:interpretsLaw` |
| Draft would change this act | `estleg:hasProposedAmendment` / `estleg:affectedLawName` |
| Effected amendment | `estleg:AmendmentEvent` + `estleg:amends` (not drafts) |
| Point-in-time text | `estleg:hasVersion` → `estleg:ProvisionVersion` (full surface only) |
| Penalty | `estleg:hasSanction` → sidecar / combined overlay |
| Combined stub? | `estleg:isStubNode` — filter it out of class counts |

### SPARQL query examples

```sparql
# Find all Supreme Court decisions referencing KarS
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?decision ?label ?date WHERE {
  ?decision a estleg:CourtDecision ;
            rdfs:label ?label ;
            estleg:referencedLaw ?law ;
            estleg:decisionDate ?date .
  FILTER(CONTAINS(?law, "KarS"))
} ORDER BY DESC(?date) LIMIT 20
```

```sparql
# Find drafts amending karistusseadustik
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?draft ?title ?phase WHERE {
  ?draft a estleg:DraftLegislation ;
         rdfs:label ?title ;
         estleg:legislativePhase ?phaseNode ;
         estleg:affectedLawName ?law .
  ?phaseNode rdfs:label ?phase .
  FILTER(CONTAINS(LCASE(?law), "karistusseadustik"))
}
```

```sparql
# Find EU directives currently in force
PREFIX estleg: <https://w3id.org/estleg/>
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

```sparql
# Find EU Court of Justice judgments
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?decision ?title ?ecli ?date WHERE {
  ?decision a estleg:EUCourtDecision ;
            rdfs:label ?title ;
            estleg:euCourtDecisionType estleg:EUDecType_Judgment ;
            estleg:ecliIdentifier ?ecli ;
            estleg:documentDate ?date .
} ORDER BY DESC(?date) LIMIT 20
```

## Coverage

Coverage is reported at two granularities and they should not be conflated:

- **Provision-level coverage** — acts whose `<paragrahv>` structure was
  parsed into per-section `estleg:LegalProvision` nodes (the bulk of the
  enacted-law and domestic-regulation corpus).
- **Act-level coverage** — acts that are *modelled* but have **no
  structured body**: treaties and ratification acts, framework/enabling
  acts, repealed shells, and procedural-only instruments that carry no
  `<paragrahv>` nodes in the Riigi Teataja XML. Rather than dropping
  these (which would be indistinguishable from a source-fetch failure),
  `scripts/generate_all_laws.py` and `scripts/generate_regulations.py`
  emit an act-level stub node with `estleg:contentStatus =
  "noStructuredBody"` (plus an `estleg:contentStatusReason`). The number
  of such stubs is recorded in `krr_outputs/generation_manifest_laws.json`
  (`counts.stubActs`, and a per-act `status` on each `outputsAll` entry)
  and in `krr_outputs/regulations/*/REGULATIONS_*_INDEX.json`
  (`noStructuredBodyCount` / the per-act `acts` ledger). A consumer can
  therefore tell a no-body act apart from a parse failure (`status:
  "failed"`) and from an act that simply wasn't selected for
  regeneration this run (`status: "skipped"`). At present the committed
  corpus contains no `noStructuredBody` stubs — every act that survived
  the source `kehtiv` filter parsed into provisions — but the path is
  exercised on every supervised generator run; consult the manifest for
  the live count.

### Enacted Laws (Riigi Teataja)

| Category | Laws | Examples |
|----------|------|----------|
| Civil Law | 7 | TsUS, VOS, AOS, PKS |
| Commercial & Economic | 6 | AS, PankrS, MKS |
| Criminal Law | 2 | KarS, KrMS |
| Administrative Law | 8 | HMS, KOKS, IKS |
| Procedural Law | 3 | TsMS, TMS |
| Constitutional | 4 | PS, RVastS |
| Environmental | 4 | KeUS, JaatS, VeeS |
| Other | 574+ | PPVS, TLS, AUS, ... |

Counts above are provision-level (acts with a parsed `<paragrahv>`
body); add the manifest's `counts.stubActs` for the act-level total.

### Draft Legislation (EIS)

| Phase | Estonian | Count |
|-------|----------|-------|
| Public Consultation | Avalik konsultatsioon | 122 |
| Inter-ministerial Review | Kooskolastamine | 13,670 |
| Submitted to Government | Esitatud VV-le | 9,040 |

### Domestic Regulations (Riigi Teataja, määrused)

| Level | Estonian | Count | Schema class |
|-------|----------|-------|--------------|
| State (government + ministerial) | Riigi tasandi maarused | 3,812 | `estleg:NationalRegulation` |
| Municipal (KOV) | KOV maarused | 11,059 (opt-in) | `estleg:MunicipalRegulation` |

State regulations are split between `estleg:GovernmentRegulation` (Vabariigi Valitsus) and `estleg:MinisterialRegulation` (issued by individual ministers). Source: Riigi Teataja `dokument=määrus`. KOV regulations are not generated by default — opt in via `python scripts/generate_regulations.py --kov`.

#### KOV (Municipal) Entity Model — Layer 1

KOV regulations are a **first-class entity layer on the full load surface**: every act and provision is queryable by territorial unit and issuing body **once the `krr_outputs/regulations/kov/` files are loaded alongside `combined_ontology.jsonld`** (see [Load surfaces](#load-surfaces) below). The 11,059 municipal regulations (~116k `estleg:KovProvision` bodies) and the EHAK municipality/successor layer are **not** folded into `combined_ontology.jsonld` itself — that flagship file is kept lean and carries KOV regulations only as resolvable cross-corpus *stubs* (label + identifier + `estleg:partOfAct`/`enactedBy`/`enactedByMunicipality` links). Load the KOV surface for the full provision text and municipality registry.

- **79 Municipality nodes** (`estleg:Municipality`, IRI pattern `estleg:Municipality_EHAK_<4-digit>`) — one per current Estonian KOV unit, keyed by EHAK code.
- **357 Issuer nodes** (`estleg:Issuer`, subclass of `estleg:Institution`) — one per issuing body (volikogu, valitsus), each linked to its current Municipality.
- Every KOV act carries `estleg:enactedBy` (Issuer), `estleg:enactedByMunicipality` (Municipality), and `estleg:titleNormalized` for cross-municipality bucketing.
- Every KOV provision is typed `estleg:KovProvision` and carries `estleg:partOfAct` plus the same Issuer/Municipality links.
- New parent classes `estleg:Act` (top-level) and `estleg:Law` (statute) are stamped on existing law nodes for uniform act-level querying.

See [docs/SCHEMA_REFERENCE.md#kov-entity-model-layer-1](docs/SCHEMA_REFERENCE.md#kov-entity-model-layer-1) for the full schema, IRI patterns, and SPARQL examples.

#### Layer 2 — KOV pipeline status

- Layer 2a: Discovery + 5 pipelines — **COMPLETE** (deontic, EuroVoc,
  temporal, legal-concepts, amendment-history)
- Layer 2b: Cross-references + Inverse — **COMPLETE** (`issuedUnder`,
  `implementsCitation`, `implementedBy`, KOV body-text scope)
- Layer 2c: Semantic resolvers — **COMPLETE** (sanctions, institutional
  competence, court-provision-links; shipped in #250)

Layer 2a (five pipelines) processes the 11,059 KOV act files end-to-end:

- `classify_deontic` — modal classifications over KOV provisions
- `classify_eurovoc` — EuroVoc tags over KOV acts (driven by `iter_peep_files()` instead of `INDEX.json`)
- `extract_temporal_data` — entry-into-force / amendment dates from KOV XML (via `globaalID` pairing)
- `extract_legal_concepts` — concept definitions from KOV provision text (provision IRIs sourced from actual `@id`)
- `generate_amendment_history` — amendment chains for KOV regulations

Layer 2b adds two more pipelines:

- `extract_cross_references` — preamble citations resolved into
  `estleg:issuedUnder` and reified `estleg:Citation` nodes; KOV body-
  text act references scoped by `enactedByMunicipality`. 13,618
  preamble citations resolved (50% of 27,299 found) and 32,413 triples
  emitted across 9,648 files in the 2026-05-04 corpus run.
- `generate_inverse_references` — `estleg:implementedBy` /
  `estleg:implementedByCount` filtered inverse projection (preamble-
  only — body-text refs continue to use `estleg:referencedBy`). 882
  target nodes carrying `implementedBy` across 604 files (21,256
  total source-link edges) in the 2026-05-04 corpus run.

Per-pipeline coverage reports at `krr_outputs/reports/kov/`. See the
[Layer 2a plan](docs/superpowers/plans/2026-05-03-kov-integration-layer2a.md)
and [Layer 2b plan](docs/superpowers/plans/2026-05-03-kov-integration-layer2b.md)
for details.

Layer 2c (sanctions, institutional competence, court-provision-links)
shipped in #250 and is now **COMPLETE**. The 4 KOV-not-applicable
pipelines (similarity, draft-impact, harmonisation-links,
transposition-mapping) remain laws-and-state-only by design.

### Supreme Court Decisions (Riigikohus)

| Case Type | Estonian | Count |
|-----------|----------|-------|
| Administrative | Haldusasi | 9,561 |
| Civil | Tsiviilasi | 970 |
| Criminal | Kriminaalasi | 484 |
| Constitutional Review | Pohiseaduslikkuse jarelevalve | 336 |
| Misdemeanor | Vaarteoasi | 107 |
| Other | Muu | 679 |

Years covered: 1993-2026 (12,137 decisions total)

### EU Legislation (EUR-Lex)

| Type | Estonian | Total | In Force |
|------|----------|-------|----------|
| Regulation | EL maarus | 18,784 | 5,095 |
| Directive | EL direktiiv | 3,114 | 939 |
| Decision | EL otsus | 11,344 | 6,097 |

Source: EUR-Lex SPARQL endpoint (33,242 acts with Estonian translations)

### EU Court Decisions (CURIA)

| Type | Estonian | Count |
|------|----------|-------|
| AG Opinion | Kohtujuristi ettepanek | 9,952 |
| Order | Kohtumaarus | 6,619 |
| Judgment | Kohtuotsus | 5,641 |
| Court Opinion | Kohtu arvamus | 17 |

| Court | Estonian | Count |
|-------|----------|-------|
| Court of Justice | Euroopa Kohus | 17,720 |
| General Court | Uldkohus | 4,036 |
| Civil Service Tribunal | Avaliku Teenistuse Kohus | 534 |

Source: EUR-Lex SPARQL endpoint (22,290 decisions with Estonian translations)

## Data Sources

| Source | URL | Data | Script |
|--------|-----|------|--------|
| **Riigi Teataja** | https://www.riigiteataja.ee | Enacted laws (XML API) | `scripts/generate_all_laws.py` |
| **Riigi Teataja** | https://www.riigiteataja.ee | Domestic regulations (XML API) | `scripts/generate_regulations.py` |
| **EIS** | https://eelnoud.valitsus.ee | Draft legislation (RSS feeds) | `scripts/generate_draft_legislation.py` |
| **RIK / Riigikohus** | https://rikos.rik.ee | Supreme Court decisions (HTML search) | `scripts/generate_court_decisions.py` |
| **EUR-Lex** | https://eur-lex.europa.eu | EU legislation (SPARQL) | `scripts/generate_eu_legislation.py` |
| **EUR-Lex / CURIA** | https://eur-lex.europa.eu | EU court decisions (SPARQL) | `scripts/generate_eu_court_decisions.py` |

### API Details

**Riigi Teataja** (enacted laws):
- Search: `GET https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi?leht=N&dokument=seadus`
- XML: `GET https://www.riigiteataja.ee/akt/GLOBALID.xml`

**Riigi Teataja** (domestic regulations):
- Search: `GET https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi?leht=N&dokument=määrus`
- XML: `GET https://www.riigiteataja.ee/akt/GLOBALID.xml` (same XML schema as laws; pre-2010 acts may use `<HTMLKonteiner>` rather than structured XML)

**EIS** (draft legislation):
- Public consultation: `GET https://eelnoud.valitsus.ee/main/mount/rss/home/publicConsult.rss`
- Review: `GET https://eelnoud.valitsus.ee/main/mount/rss/home/review.rss`
- Submission: `GET https://eelnoud.valitsus.ee/main/mount/rss/home/submission.rss`

**RIK** (court decisions):
- Search: `GET https://rikos.rik.ee/?aasta=YYYY&pageSize=100&lk=N`
- Individual: `https://www.riigikohus.ee/et/lahendid/?asjaNr=CASE_NR`

**EUR-Lex** (EU legislation):
- SPARQL endpoint: `https://publications.europa.eu/webapi/rdf/sparql` (open, no auth)
- CDM ontology classes: `cdm:regulation`, `cdm:directive`, `cdm:decision`
- Estonian language filter: `<http://publications.europa.eu/resource/authority/language/EST>`
- EUR-Lex page: `https://eur-lex.europa.eu/legal-content/ET/TXT/?uri=CELEX:{CELEX_NR}`

## Integration & Cross-Linking

The ontology includes 15 integration layers that connect laws, court decisions, drafts, and EU legislation:

| Feature | Script | Description |
|---------|--------|-------------|
| Cross-law references | `extract_cross_references.py` | Parses citation patterns (KarS § 121, VÕS § 208 lg 1) and adds IRI links |
| Bidirectional links | `generate_inverse_references.py` | Auto-generates `referencedBy` inverses |
| Court → provision links | `extract_court_provision_links.py` | Links court decisions to specific provisions they interpret |
| EU transposition | `generate_transposition_mapping.py` | Maps Estonian laws to EU directives via EUR-Lex |
| EuroVoc taxonomy | `classify_eurovoc.py` | Classifies laws with standardised EU subject taxonomy |
| Amendment history | `generate_amendment_history.py` | Tracks amendment chains between laws |
| Legal concepts | `extract_legal_concepts.py` | Extracts defined terms and links across laws |
| Draft impact | `extract_draft_impact.py` | Provision-level impact analysis for pending drafts |
| Temporal validity | `extract_temporal_data.py` | Entry-into-force and repeal dates |
| Cross-border harmonisation | `generate_harmonisation_links.py` | Links to parallel EU transpositions |
| Deontic classification | `classify_deontic.py` | Classifies provisions as obligations/rights/permissions/prohibitions |
| Target groups | `classify_target_group.py` | Classifies affected groups for obligations, rights, permissions, and prohibitions |
| Institutional competence | `extract_institutional_competence.py` | Maps which institution enforces what |
| Sanctions | `extract_sanctions.py` | Penalty and sanction cross-reference index |
| Semantic similarity | `generate_similarity_index.py` | Keyword-based similarity between provisions |

Harmonisation sidecars (`krr_outputs/harmonisation/`) are neighbour-state comparative NIM measures (LV/LT/FI/SE); Estonian transposition is `estleg:transposesDirective` / the transposition mapping, not the hollow HarmonisationLink stubs.

`krr_outputs/reports/similarity_index.json` / `similarity_report.json` and the EUR-normalized severity scores in `krr_outputs/reports/sanctions_report.json` are **non-graph** application indexes (issue #462); SPARQL does not see those pairs or scores.

### Running integration scripts

```bash
# Cross-law references (run first — other scripts depend on this)
python3 scripts/extract_cross_references.py
python3 scripts/generate_inverse_references.py

# Court decision links
python3 scripts/extract_court_provision_links.py

# EU integration
python3 scripts/generate_transposition_mapping.py
python3 scripts/classify_eurovoc.py
python3 scripts/generate_harmonisation_links.py

# Temporal and amendment data
python3 scripts/extract_temporal_data.py
python3 scripts/generate_amendment_history.py

# Legal concepts and classification
python3 scripts/extract_legal_concepts.py
python3 scripts/classify_deontic.py
python3 scripts/extract_institutional_competence.py
python3 scripts/extract_sanctions.py

# Draft impact and similarity
python3 scripts/extract_draft_impact.py
python3 scripts/generate_similarity_index.py
```

## Repository Structure

```
.
├── krr_outputs/              # JSON/JSON-LD ontology files (23,116 files)
│   ├── *_peep.json           # Individual enacted law mappings
│   ├── combined_ontology.jsonld  # Self-contained graph: laws + overlays + cross-corpus stubs
│   ├── INDEX.json            # Enacted law registry
│   ├── eelnoud/              # Draft legislation
│   │   ├── eelnoud_schema.json           # Schema definitions
│   │   ├── eelnoud_*_peep.json           # Phase-grouped drafts
│   │   ├── eelnoud_combined.jsonld       # All drafts combined
│   │   └── EELNOUD_INDEX.json            # Draft registry
│   ├── regulations/          # Domestic regulations (maarused)
│   │   ├── riik/                          # State-level (3,812 files)
│   │   │   ├── *_peep.json                # One file per regulation
│   │   │   └── REGULATIONS_RIIK_INDEX.json # State regulation registry (byIssuer counts)
│   │   └── kov/                           # KOV/municipal (opt-in via --kov)
│   │       ├── <issuer_slug>/             # Per-municipality subdirectories
│   │       │   └── *_peep.json
│   │       └── REGULATIONS_KOV_INDEX.json
│   ├── riigikohus/           # Supreme Court decisions
│   │   ├── riigikohus_schema.json        # Schema definitions
│   │   ├── riigikohus_YYYY_peep.json     # Per-year decisions (1993-2026)
│   │   └── RIIGIKOHUS_INDEX.json         # Court decision registry
│   ├── eurlex/               # EU legislation
│   │   ├── eurlex_schema.json            # Schema definitions
│   │   ├── eurlex_regulations_peep.json  # EU regulations
│   │   ├── eurlex_directives_peep.json   # EU directives
│   │   ├── eurlex_decisions_peep.json    # EU decisions
│   │   ├── eurlex_combined.jsonld        # All EU acts combined
│   │   └── EURLEX_INDEX.json             # EU legislation registry
│   ├── curia/                # EU court decisions
│   │   ├── curia_schema.json             # Schema definitions
│   │   ├── curia_judgments_peep.json      # CJEU judgments
│   │   ├── curia_orders_peep.json        # Court orders
│   │   ├── curia_ag_opinions_peep.json   # AG opinions
│   │   ├── curia_court_opinions_peep.json # Court opinions
│   │   ├── curia_other_peep.json         # Other decision types
│   │   ├── curia_combined.jsonld         # All EU decisions combined
│   │   └── CURIA_INDEX.json              # EU court decision registry
│   ├── concepts/             # Legal concept cross-reference graph
│   ├── institutions/         # Institutional competence mappings
│   ├── sanctions/            # Penalty and sanction index
│   ├── amendments/           # Amendment chain data
│   ├── transposition_schema.json      # Transposition schema definitions
│   ├── harmonisation/                 # Cross-border harmonisation links (LV/LT/FI/SE parallel transpositions)
│   └── reports/              # Report/index/mapping/classification sidecars (#471)
│       ├── cross_references_report.json
│       ├── inverse_references_report.json
│       ├── court_provision_links_report.json
│       ├── transposition_mapping.json
│       ├── eurovoc_classification.json
│       ├── amendment_history_report.json
│       ├── deontic_classification_report.json
│       ├── draft_impact_report.json
│       ├── temporal_data_report.json
│       ├── similarity_index.json
│       └── similarity_report.json
├── docs/                     # Documentation
│   ├── README.md             # Full project documentation
│   ├── API_GUIDE.md          # SPARQL and API usage guide
│   ├── SCHEMA_REFERENCE.md   # Complete schema reference
│   ├── VALIDATION_REPORT.md  # Quality report
│   ├── INTEGRATION_IDEAS.md  # Integration improvement ideas
│   └── DUPLICATE_IDS_REPORT.md # Cross-file @id collision audit
├── shacl/                    # SHACL validation shapes
├── scripts/                  # Generation and validation scripts (41 total; representative subset shown below)
│   ├── generate_all_laws.py           # Enacted laws generator
│   ├── generate_draft_legislation.py  # Draft legislation generator
│   ├── generate_court_decisions.py    # Court decisions generator
│   ├── generate_eu_legislation.py     # EU legislation generator
│   ├── generate_eu_court_decisions.py # EU court decisions generator
│   ├── generate_kars_eriosa_jsonld.py # KarS special parts
│   ├── generate_missing_parts.py     # Generate missing law parts
│   ├── extract_cross_references.py   # Cross-law citation links
│   ├── generate_inverse_references.py # Bidirectional reference links
│   ├── extract_court_provision_links.py # Court → provision links
│   ├── generate_transposition_mapping.py # EU directive transposition
│   ├── classify_eurovoc.py           # EuroVoc taxonomy classification
│   ├── generate_amendment_history.py  # Amendment chain tracking
│   ├── extract_legal_concepts.py     # Legal concept extraction
│   ├── extract_draft_impact.py       # Draft impact analysis
│   ├── extract_temporal_data.py      # Temporal validity dates
│   ├── generate_harmonisation_links.py # EU harmonisation links
│   ├── classify_deontic.py           # Deontic classification
│   ├── extract_institutional_competence.py # Institutional competence
│   ├── extract_sanctions.py          # Sanctions extraction
│   ├── generate_similarity_index.py  # Semantic similarity index
│   ├── fix_all_issues.py             # Batch issue fixer
│   ├── run_all_integration.py        # Master integration orchestrator
│   ├── validate_all.py               # Comprehensive validation
│   └── estleg_common.py              # Shared utilities and abbreviations
├── reviews/                  # Law review request files
├── .github/workflows/        # CI pipeline
├── CHANGELOG.md              # Version history
└── LICENSE                   # MIT License
```

## Schema

The ontology uses the `estleg` namespace (`https://w3id.org/estleg/`) with 28 core classes:

**Enacted Law:**
- **`estleg:LegalProvision`** -- Individual legal provisions (paragraphs, sections)
- **`estleg:TopicCluster`** -- Thematic groupings of provisions
- **`estleg:LegalConcept`** -- Legal concepts and definitions (with SKOS cross-references)

**Draft Legislation:**
- **`estleg:DraftLegislation`** -- Legislative drafts not yet enacted (eelnoud)
- **`estleg:LegislativePhase`** -- PublicConsultation -> Review -> Submission
- **`estleg:DraftType`** -- Bill, AmendmentBill, GovernmentRegulation, etc.

**Domestic Regulations (maarused):**
- **`estleg:Act`** -- Top-level "any enacted Estonian legal act" (parent class)
- **`estleg:Law`** -- Statute (seadus); stamped onto every existing law node
- **`estleg:NationalRegulation`** -- Estonian regulations (umbrella class for state and KOV)
- **`estleg:GovernmentRegulation`** -- Regulations issued by Vabariigi Valitsus
- **`estleg:MinisterialRegulation`** -- Regulations issued by individual ministers
- **`estleg:MunicipalRegulation`** -- KOV (municipal) regulations
- **`estleg:KovProvision`** -- Provision of a municipal regulation (subclass of LegalProvision)
- **`estleg:Municipality`** -- KOV territorial unit (79 nodes, keyed by EHAK code)
- **`estleg:Issuer`** -- KOV issuing body (357 nodes; subclass of Institution)
- **`estleg:Annex`** -- Regulation annexes (lisad)

**Court Decisions:**
- **`estleg:CourtDecision`** -- Supreme Court decisions (judgments, rulings)
- **`estleg:CaseType`** -- Criminal, Civil, Administrative, Constitutional Review, Misdemeanor
- **`estleg:DecisionType`** -- Judgment, Ruling, Resolution

**EU Legislation:**
- **`estleg:EULegislation`** -- EU legal acts (regulations, directives, decisions)
- **`estleg:EUDocumentType`** -- Regulation, Directive, Decision
- **`estleg:EUInstitution`** -- European Commission, Council, Parliament, etc.

**EU Court Decisions:**
- **`estleg:EUCourtDecision`** -- CJEU decisions (judgments, orders, AG opinions)
- **`estleg:EUCourtDecisionType`** -- Judgment, Order, AG Opinion, Court Opinion
- **`estleg:EUCourt`** -- Court of Justice, General Court, Civil Service Tribunal

**Integration & Analysis:**
- **`estleg:Sanction`** -- Penalties and sanctions extracted from law text
- **`estleg:Institution`** -- State institutions with legal competences
- **`estleg:NormativeType`** -- Deontic classification (Obligation, Right, Permission, Prohibition)

See [docs/SCHEMA_REFERENCE.md](docs/SCHEMA_REFERENCE.md) for full schema documentation.

## Setup

```bash
python3 -m pip install -e ".[dev]"
```

That install puts `src/estleg/` on the package path and exposes
`estleg-generate-laws`, `estleg-run-pipeline`, and `estleg-validate`.
`python3 scripts/<name>.py` remains a shim onto the same modules.

## Validation

```bash
python3 -m pytest -q
estleg-validate
# or: python3 scripts/validate_all.py
```

CI runs tests and validation for changes to scripts, tests, SHACL, metadata,
workflow files, docs, and ontology outputs.

### Canonical artifacts

The following artifacts are authoritative for downstream consumers and the
release pipeline:

- **Source files (edit here):** every root `krr_outputs/*_peep.json` plus
  `krr_outputs/controlled_vocabulary.jsonld`,
  `krr_outputs/karistusseadustik_eriosa_owl.jsonld`, and
  `krr_outputs/tsus_osa7_138_169_owl.jsonld`. These are the canonical
  per-act mappings.
- **Public load-surface directories (edit per generator):**
  `krr_outputs/eelnoud/`, `riigikohus/`, `curia/`, `eurlex/`,
  `concepts/`, `sanctions/`, `amendments/`, `institutions/`,
  `provision_versions/`, `annotations/`, `harmonisation/`, and
  `regulations/`. Seadusloome consumes these alongside the combined
  aggregate; the `provision_versions/` sidecar (`estleg:hasVersion`) and
  the provisional draft `estleg:amendedBy` events are expected to resolve
  within this full surface, not inside `combined_ontology.jsonld` alone.
- **Generated aggregate (do not edit by hand):**
  `krr_outputs/combined_ontology.jsonld`. Since #416 this is a
  **self-contained graph**: it fully merges the sanctions/institutions/
  concepts/annotations overlays and mints lightweight stub nodes
  (`estleg:isStubNode`) for every remaining cross-corpus reference
  (court/EU/draft/regulation/amendment/harmonisation), so loading it
  alone yields zero dangling `estleg:` object IRIs apart from the two
  documented exempt predicates above. `validate_combined_graph_closure`
  enforces that invariant. `estleg:isStubNode` is a build/serialisation
  marker, not a semantic claim: when you also load the sibling subcorpus
  the complete node takes precedence and the flag should be disregarded.
  Regenerate it via `scripts/fix_all_issues.py`
  (the `generate_combined_jsonld()` step, which now reads the LFS
  annotations + eurlex sources) so it stays in lockstep with the source
  files. Editing it directly will reintroduce the drift the Seadusloome
  zero-warning gate exists to catch.

### Validation and release order

Run these steps in order before publishing or merging release-grade
ontology changes:

1. Build or regenerate canonical artifacts
   (`python3 scripts/fix_all_issues.py` or the targeted generators).
2. Validate sources and combined parity
   (`python3 scripts/validate_all.py`).
3. Run full bucket SHACL
   (`python3 scripts/shacl_validate_all.py --all`, or per bucket via
   `--bucket <name>`).
4. Run the Seadusloome zero-warning gate
   (`python3 scripts/validate_seadusloome_sync.py`).
5. Refresh metadata and documentation counts
   (`docs/VALIDATION_REPORT.md`, `metadata.jsonld`) if the release
   artifacts changed.

### Seadusloome zero-warning policy

Seadusloome consumers must see zero SHACL warnings on the published
branch. `scripts/validate_seadusloome_sync.py` mirrors the Seadusloome
load path (`combined_ontology.jsonld` plus the public load-surface
directories listed above) and exits non-zero if any warning, violation,
or unresolved internal `estleg:` object reference is observed. The gate
runs on every pull request and on `main` pushes via the
`Seadusloome zero-warning gate` job in
`.github/workflows/validate.yml`. The bucket SHACL job remains
authoritative for class-by-class semantic checks; the new gate is the
release contract for downstream sync.

## Refreshing Data

```bash
# Refresh enacted laws from a declared Riigi Teataja snapshot.
# Default is --missing-only against --kehtiv 2026-05-01. In --missing-only
# mode an existing file is *also* re-fetched when its stored
# estleg:kehtiv snapshot date no longer matches --kehtiv (stale-snapshot
# refresh); files already at the current snapshot are skipped.
python3 scripts/generate_all_laws.py --missing-only
python3 scripts/generate_all_laws.py --refresh --kehtiv 2026-05-01
python3 scripts/generate_all_laws.py --force --kehtiv 2026-05-01

# Re-generate the SAME law list a previous run captured, without
# re-querying the live search (the per-act XML is still fetched, keyed on
# the recorded terviktekstId). The manifest's outputsAll block is the
# source of truth.
python3 scripts/generate_all_laws.py --from-manifest krr_outputs/generation_manifest_laws.json

# The run records counts (full / stub / failed / skipped acts), an
# unchanged-vs-refreshed split, and source-removed peep files (acts no
# longer in the live snapshot — reported, not deleted) in
# krr_outputs/generation_manifest_laws.json.

# Refresh state-level domestic regulations from Riigi Teataja.
# Default is --missing-only; use --refresh to re-fetch XML and rewrite changed files.
# Source-list fetch failures are fatal unless --allow-partial is explicit.
python3 scripts/generate_regulations.py --missing-only
python3 scripts/generate_regulations.py --refresh --kehtiv 2026-05-01
# (add --kov to refresh municipal regulations)

# Re-fetch draft legislation from EIS
python3 scripts/generate_draft_legislation.py

# Re-fetch Supreme Court decisions from RIK
python3 scripts/generate_court_decisions.py

# Re-fetch EU legislation from EUR-Lex
python3 scripts/generate_eu_legislation.py

# Re-fetch EU court decisions from EUR-Lex
python3 scripts/generate_eu_court_decisions.py

# Run all integration scripts in dependency order with rollback on failure:
python3 scripts/run_all_integration.py --validate-each

# Or run individually:
python3 scripts/extract_cross_references.py
python3 scripts/generate_inverse_references.py
python3 scripts/extract_court_provision_links.py
python3 scripts/generate_transposition_mapping.py
python3 scripts/classify_eurovoc.py
python3 scripts/extract_temporal_data.py
python3 scripts/generate_amendment_history.py
python3 scripts/extract_legal_concepts.py
python3 scripts/classify_deontic.py
python3 scripts/extract_institutional_competence.py
python3 scripts/extract_sanctions.py
python3 scripts/extract_draft_impact.py
python3 scripts/generate_similarity_index.py
python3 scripts/generate_harmonisation_links.py
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Ensure `python3 -m pytest -q` and `python3 scripts/validate_all.py` pass
4. Submit a pull request

## License

MIT License -- see [LICENSE](LICENSE) for details.
