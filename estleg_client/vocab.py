"""Exact ``rdf:type`` IRIs the client matches on (measured against the corpus).

Measured 2026-10-08 on the committed tree: every node writes ``@type`` as a
list of ``estleg:``/``owl:``/``skos:`` CURIEs; there is no ``SanctionType``
class and every ``estleg:KovProvision`` also carries ``estleg:LegalProvision``.
The release aggregate ``combined_ontology.jsonld`` additionally types every
``estleg:Subsection`` as ``estleg:LegalProvision`` (the per-law peeps do not),
so paragraph-level selection excludes ``Subsection`` explicitly. Matching is
by exact IRI (``has_type``), never by substring.
"""

from __future__ import annotations

from typing import Final

ESTLEG: Final = "https://w3id.org/estleg/"

LEGAL_PROVISION: Final = ESTLEG + "LegalProvision"
KOV_PROVISION: Final = ESTLEG + "KovProvision"
SUBSECTION: Final = ESTLEG + "Subsection"
SANCTION: Final = ESTLEG + "Sanction"
CITATION: Final = ESTLEG + "Citation"
LAW: Final = ESTLEG + "Law"
ACT: Final = ESTLEG + "Act"
NATIONAL_REGULATION: Final = ESTLEG + "NationalRegulation"
MUNICIPAL_REGULATION: Final = ESTLEG + "MunicipalRegulation"
COURT_DECISION: Final = ESTLEG + "CourtDecision"
DRAFT_LEGISLATION: Final = ESTLEG + "DraftLegislation"
EU_LEGISLATION: Final = ESTLEG + "EULegislation"

#: Paragraph-level provision nodes (laws, state and municipal regulations).
PROVISION_TYPES: Final = (LEGAL_PROVISION, KOV_PROVISION)
#: Subsection (``lõige``) nodes; opt in with ``include_subsections=True``.
SUBSECTION_TYPES: Final = (SUBSECTION,)
SANCTION_TYPES: Final = (SANCTION,)
CITATION_TYPES: Final = (CITATION,)
REGULATION_TYPES: Final = (NATIONAL_REGULATION, MUNICIPAL_REGULATION)
DECISION_TYPES: Final = (COURT_DECISION,)
DRAFT_TYPES: Final = (DRAFT_LEGISLATION,)
EU_ACT_TYPES: Final = (EU_LEGISLATION,)
