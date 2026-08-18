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
