# DCAT-AP 3.0.1 SHACL shapes (cached)

Verbatim copies of the SEMIC DCAT-AP 3.0.1 release shapes, used by
`tests/test_issue_710_dcat_ap.py` to validate the catalogue record
`metadata.jsonld` offline (#710).

| File | Source | SHA-256 |
|------|--------|---------|
| `dcat-ap-SHACL.ttl` | https://raw.githubusercontent.com/SEMICeu/DCAT-AP/master/releases/3.0.1/shacl/dcat-ap-SHACL.ttl | `990d3e42721de6a4be8cc338a7171559f195e62dea89c0b56531356b78cc026f` |
| `ranges.ttl` | https://raw.githubusercontent.com/SEMICeu/DCAT-AP/master/releases/3.0.1/shacl/ranges.ttl | `a6eed0fae8d0f5ca977fe2098ca12081ac60b0efe1ddce802d5c08e49505ebcc` |

Fetched 2026-10-08. The upstream repository (SEMICeu/DCAT-AP) is licensed
CC BY 4.0; attribution: SEMIC, European Commission.

Upstream defect: the release references seven `sh:property` IRIs it never
defines (five in `ranges.ttl`, two in `dcat-ap-SHACL.ttl`). pyshacl rejects
them as malformed, so the test drops those dangling links before validating.
Nothing else is altered.

`ranges.ttl` checks the class of every referenced node (for example that a
licence IRI is a `dcterms:LicenseDocument`). `metadata.jsonld` therefore types
each authority IRI inline. The record does not rely on the EU vocabularies
being loaded.
