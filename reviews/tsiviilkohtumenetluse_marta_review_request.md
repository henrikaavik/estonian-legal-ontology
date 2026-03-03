# Marta review request: Tsiviilkohtumenetluse seadustik mapping (TsMS)

Hi Marta,

I mapped TsMS into unified JSON-LD schema and saved it to:
- `krr_outputs/tsiviilkohtumenetluse_peep.json`

Please review legal phrasing and scope for required clusters:
1. Hagimenetlus (kohtumenetluse käik)
2. Tõendamine
3. Apellatsioonimenetlus

Notes:
- Mapping follows the same canonical schema used across KRR outputs (`estleg/owl/rdf/rdfs/xsd/dc/skos`).
- I kept references at normiblokk level (some entries cite parts/chapters instead of exact § ranges) to avoid false precision.
- Please flag any place where we should tighten wording on admissibility of evidence, appellate review scope, or first-instance vs second-instance court powers.

Thanks!
