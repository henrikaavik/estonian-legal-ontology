# Marta review request: Isikuandmete kaitse seadus mapping (IKS)

Hi Marta,

I mapped IKS into unified JSON-LD schema and saved it to:
- `krr_outputs/isikuandmete_kaitse_peep.json`

Please review legal phrasing and scope for these clusters:
1. Andmesubjekti õigused
2. Andmetöötlus
3. Andmekaitse Inspektsioon

Key section groupings used:
- rights: §22–§28
- processing: §4–§21, §29–§39, §43–§50
- DPA: §2¹, §51–§61, §69–§70

Notes:
- Source basis: RT I, 12.07.2025, 14 (in force from 01.10.2025)
- Schema follows canonical context (`estleg/owl/rdf/rdfs/xsd/dc/skos`) and `@graph` structure.

Please flag anything that needs legal tightening or better conceptual grouping.
