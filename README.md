# Estonian Legal Ontology

A comprehensive, machine-readable ontology of Estonian and EU legislation in JSON-LD format. Maps **enacted laws**, **draft legislation**, **domestic regulations (määrused)**, **Supreme Court decisions**, **EU legal acts**, and **EU court decisions** into a semantic knowledge graph suitable for advanced search, cross-referencing, and automated legal analysis.

**Eestikeelne ülevaade:** [loe ontoloogia ülevaadet](docs/eesti-oigusontoloogia-ulevaade.html) — mis see on, kuidas see töötab, kust andmed pärinevad, kuidas seda uuendada ning kuidas ministeeriumid seda kasutada saaksid. PDF-versioon: [docs/eesti-oigusontoloogia-ulevaade.pdf](docs/eesti-oigusontoloogia-ulevaade.pdf).

<!-- counts: keep in sync with metadata.jsonld estleg:statistics — validate_all.py::validate_metadata_catalog enforces metadata.jsonld vs the corpus, and tests/test_validate_all.py::test_readme_counts_match_metadata enforces README vs metadata.jsonld -->
**Status: 608 enacted laws (637 law files) + 22,832 drafts + 3,812 state regulations + 11,059 municipal regulations (opt-in) + 12,137 court decisions + 33,242 EU acts + 22,290 EU court decisions** | **21,971 JSON/JSON-LD files** | **170,000+ semantic nodes**

**Integration features:** Cross-law reference links | Court decision → provision links | EU directive transposition mapping | EuroVoc taxonomy | Amendment history | Legal concept graph | Deontic classification | Institutional competence | Sanction index | Semantic similarity | Temporal validity

Domestic regulations participate in the same cross-reference, EuroVoc, deontic, sanction, and similarity passes as enacted laws — the integration scripts iterate via `iter_peep_files()` from `estleg_common`, so any pipeline that runs over `*_peep.json` automatically covers laws and regulations alike.

## Quick Start

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

ESTLEG = Namespace("https://data.riik.ee/ontology/estleg#")

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

ESTLEG = Namespace("https://data.riik.ee/ontology/estleg#")
g = Graph()
g.parse("krr_outputs/karistusseadustik_peep.json", format="json-ld")

# Find all provisions this law references in other laws
for s, p, o in g.triples((None, ESTLEG.references, None)):
    print(f"{s} references {o}")
```

### Query court decision → provision links

```python
from rdflib import Graph, Namespace

ESTLEG = Namespace("https://data.riik.ee/ontology/estleg#")
g = Graph()
g.parse("krr_outputs/riigikohus/riigikohus_2025_peep.json", format="json-ld")

# Find all provision-level links
for s, p, o in g.triples((None, ESTLEG.interpretsLaw, None)):
    print(f"Decision {s} interprets {o}")
```

### SPARQL query examples

```sparql
# Find all Supreme Court decisions referencing KarS
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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
PREFIX estleg: <https://data.riik.ee/ontology/estleg#>
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

```sparql
# Find EU Court of Justice judgments
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

KOV regulations are now a **first-class entity layer**: every act and provision is queryable by territorial unit and issuing body.

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
- Layer 2c: Semantic resolvers — **PENDING** (sanctions, institutional
  competence, court-provision-links)

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

Layer 2c (sanctions, institutional competence, court-provision-links) is
the remaining piece. The 4 KOV-not-applicable pipelines (similarity,
draft-impact, harmonisation-links, transposition-mapping) remain
laws-and-state-only by design.

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

The ontology includes 14 integration layers that connect laws, court decisions, drafts, and EU legislation:

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
| Institutional competence | `extract_institutional_competence.py` | Maps which institution enforces what |
| Sanctions | `extract_sanctions.py` | Penalty and sanction cross-reference index |
| Semantic similarity | `generate_similarity_index.py` | Keyword-based similarity between provisions |

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
├── krr_outputs/              # JSON/JSON-LD ontology files (21,971 files)
│   ├── *_peep.json           # Individual enacted law mappings
│   ├── combined_ontology.jsonld  # All enacted laws in one file
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
│   ├── cross_references_report.json   # Cross-law reference index
│   ├── inverse_references_report.json # Bidirectional reference index
│   ├── court_provision_links_report.json # Court → provision link index
│   ├── transposition_mapping.json     # EU directive transposition map
│   ├── transposition_schema.json      # Transposition schema definitions
│   ├── harmonisation/                 # Cross-border harmonisation links (LV/LT/FI/SE parallel transpositions)
│   ├── eurovoc_classification.json    # EuroVoc topic classification
│   ├── amendment_history_report.json  # Amendment history index
│   ├── deontic_classification_report.json # Deontic classification index
│   ├── draft_impact_report.json       # Draft impact analysis index
│   ├── temporal_data_report.json      # Temporal validity index
│   ├── similarity_index.json          # Semantic similarity index
│   └── similarity_report.json         # Similarity analysis report
├── docs/                     # Documentation
│   ├── README.md             # Full project documentation
│   ├── API_GUIDE.md          # SPARQL and API usage guide
│   ├── SCHEMA_REFERENCE.md   # Complete schema reference
│   ├── VALIDATION_REPORT.md  # Quality report
│   ├── INTEGRATION_IDEAS.md  # Integration improvement ideas
│   └── DUPLICATE_IDS_REPORT.md # Cross-file @id collision audit
├── shacl/                    # SHACL validation shapes
├── scripts/                  # Generation and validation scripts (25 scripts)
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

The ontology uses the `estleg` namespace (`https://data.riik.ee/ontology/estleg#`) with 23 core classes:

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

## Validation

```bash
python3 -m pytest -q
python3 scripts/validate_all.py
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
- **Sub-corpora (edit per generator):** `krr_outputs/eelnoud/`,
  `krr_outputs/riigikohus/`, `krr_outputs/curia/`, and
  `krr_outputs/eurlex/`. Seadusloome consumes these alongside the
  combined aggregate.
- **Generated aggregate (do not edit by hand):**
  `krr_outputs/combined_ontology.jsonld`. Regenerate it via
  `scripts/fix_all_issues.py` (the `generate_combined_jsonld()` step) so
  it stays in lockstep with the source files. Editing it directly will
  reintroduce the drift the Seadusloome zero-warning gate exists to
  catch.

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
load path (root `*_peep.json` files, the listed JSON-LD vocabularies,
the four sub-corpora, and `combined_ontology.jsonld`) and exits non-zero
if any warning or violation is observed. The gate runs on every pull
request and on `main` pushes via the
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
