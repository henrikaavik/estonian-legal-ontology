# SHACL Severity Policy

The release validators treat every SHACL result as a gate failure. This includes
both violations and warnings.

Severity is intentional:

- Omitted `sh:severity` means the SHACL default, `sh:Violation`. Use this for
  required cardinality, datatype, node-kind, and structural IRI constraints.
- `sh:severity sh:Warning` is for quality or coverage constraints that should be
  reported distinctly before they are promoted to hard structural requirements.

Examples:

- `estleg:paragrahv`, `estleg:summary`, and typed dates are violations.
- `dcterms:subject` is optional act-level EuroVoc classification. Missing
  values are allowed, but present values are warnings unless they point to
  `http://eurovoc.europa.eu/{id}` IRIs.
