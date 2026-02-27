# Ontology Format/Stack Draft (v0) — 2026-02-26

## Goal
Define a practical, extensible ontology approach for Estonian legislation data that supports:
- structured ingestion
- traceable legal references
- change/version handling
- queryability for downstream applications

## Recommendation (v0)
Use a **hybrid approach**:
1. **Canonical model:** JSON-LD (developer-friendly, web-native semantics)
2. **Semantic grounding:** RDF-compatible vocabulary (so we can export to triples if needed)
3. **Operational storage:** PostgreSQL + JSONB initially
4. **Search/retrieval:** OpenSearch/Elasticsearch optional for full-text later
5. **Validation:** JSON Schema + lightweight SHACL-style rules later if RDF export becomes core

Why this now:
- Fast to implement and iterate
- Keeps semantic interoperability path open
- Avoids premature complexity of full triplestore at day 1

---

## Core Entity Model (Minimal v0)

### 1) LegalAct
Represents law/regulation/order-level act.

Key fields:
- `id` (stable URI)
- `type` (Law, Regulation, Order, etc.)
- `title`
- `jurisdiction` (EE)
- `issuer` (institution)
- `riigiTeatajaId` (official identifier)
- `publicationDate`
- `effectiveDate`
- `status` (active/repealed/draft if applicable)
- `version`
- `sourceUrl`

### 2) Provision
Represents section/paragraph/clause units.

Key fields:
- `id` (URI)
- `actId` (parent LegalAct)
- `label` (e.g., § 12, lg 3)
- `text`
- `effectiveDate`
- `version`

### 3) Institution
Issuer/authority entities.

Key fields:
- `id`
- `name`
- `type` (Parliament, Ministry, Government, etc.)

### 4) AmendmentEvent
Tracks legal changes.

Key fields:
- `id`
- `targetActId`
- `amendingActId`
- `changeType` (insert/update/repeal)
- `effectiveDate`
- `notes`

### 5) Reference
Cross-reference between acts/provisions.

Key fields:
- `id`
- `sourceProvisionId`
- `targetId` (act/provision)
- `referenceType` (cites/implements/modifies)

---

## Core Relations (v0)
- `LegalAct HAS_PROVISION Provision`
- `LegalAct ISSUED_BY Institution`
- `AmendmentEvent AMENDS LegalAct|Provision`
- `Provision REFERENCES Provision|LegalAct`
- `LegalAct REPEALS/AMENDS LegalAct` (derived or explicit)

---

## Example JSON-LD Skeleton
```json
{
  "@context": {
    "id": "@id",
    "type": "@type",
    "title": "http://purl.org/dc/terms/title",
    "effectiveDate": "http://example.org/legal/effectiveDate",
    "issuedBy": {"@id": "http://example.org/legal/issuedBy", "@type": "@id"},
    "hasProvision": {"@id": "http://example.org/legal/hasProvision", "@type": "@id"}
  },
  "id": "https://example.org/act/RT-I-2026-001",
  "type": "LegalAct",
  "title": "Example Act",
  "effectiveDate": "2026-01-01",
  "issuedBy": "https://example.org/institution/riigikogu",
  "hasProvision": [
    "https://example.org/provision/RT-I-2026-001/par-1"
  ]
}
```

---

## Data/Tech Stack (first implementation)
- **Language:** Python (ingestion + transformation)
- **Storage:** PostgreSQL 16 + JSONB
- **API layer:** FastAPI
- **Schema validation:** JSON Schema
- **ETL scheduling:** simple cron/worker initially
- **Versioning:** append-only snapshots + `valid_from` / `valid_to`

---

## Decision Needed at 30-min Sync
1. Confirm JSON-LD canonical model (yes/no)
2. Confirm Postgres-first storage (yes/no)
3. Confirm v0 entity set (LegalAct, Provision, Institution, AmendmentEvent, Reference)

## Next Immediate Build Steps (after sync)
1. Define JSON Schema files for each entity
2. Build sample ingestion pipeline for 5 legal acts
3. Implement reference extraction placeholder
4. Produce first query endpoints (`/acts`, `/acts/{id}`, `/provisions/{id}`)
