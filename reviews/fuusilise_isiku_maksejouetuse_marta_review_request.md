# Marta review request: Füüsilise isiku maksejõuetuse seadus mapping (FIMS)

Hi Marta,

I mapped FIMS into unified JSON-LD schema and saved it to:
- `krr_outputs/fuusilise_isiku_maksejouetuse_peep.json`

Please review legal phrasing and scope for required clusters:
1. Võlgade ümberkujundamine
2. Kohustustest vabastamine
3. Kaitstav sissetulek

Notes:
- Mapping is in the same canonical schema used across KRR outputs (`estleg/owl/rdf/rdfs/xsd/dc/skos`).
- I grouped norms at "normiblokk" level because RT web fetch was unavailable during this run.
- Please flag if any summary overstates legal effect or if key exceptions (especially debt relief exclusions/protected income limits) need tighter wording.

Thanks!
