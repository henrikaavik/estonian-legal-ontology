#!/usr/bin/env python3
"""Consolidate the canonical T-Box into ``controlled_vocabulary.jsonld`` (#433).

Moves the metadata.jsonld named-graph T-Box and the reusable subcorpus
schema axioms into the CV default graph, deletes junk terms, relocates
unresolved-reference individuals, rewrites placeholder comments, and
backfills ``rdfs:domain`` / ``rdfs:range`` so ≥95% of properties carry both.

    python3 scripts/consolidate_tbox.py
    python3 scripts/generate_schemas_from_cv.py --check
"""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from estleg.estleg_common import KRR_DIR, REPO_ROOT

VOCAB_PATH = KRR_DIR / "controlled_vocabulary.jsonld"
METADATA_PATH = REPO_ROOT / "metadata.jsonld"
UNRESOLVED_PATH = KRR_DIR / "unresolved_references.jsonld"
COMBINED_PATH = KRR_DIR / "combined_ontology.jsonld"

VOCABULARY_IRI = "https://w3id.org/estleg/vocabulary"
ONTOLOGY_VERSION = "0.11.0"

JUNK_TERMS = frozenset(
    {
        "estleg:jsonld",
        "estleg:counts",
        "estleg:note",
        "estleg:scope",
        "estleg:title",
    }
)

# #377: these three mint types under SHACL inference=rdfs if axiomatised.
FORBIDDEN_NO_AXIOM = frozenset(
    {
        "estleg:hasSection",
        "estleg:hasProvision",
        "estleg:coversConcept",
    }
)

# Cross-bucket object properties: a concrete rdfs:range would type bare
# stubs as shaped classes and false-fail SHACL (#418 / #563).
FORBIDDEN_NO_RANGE = frozenset(
    {
        "estleg:transposesDirective",
        "estleg:transposedBy",
        "estleg:harmonisedWith",
        "estleg:harmonises",
        "estleg:sharedDirective",
        "estleg:interpretsEULaw",
        # Bare hasVersion objects live in provision_versions/ sidecars; a range
        # types those stubs as ProvisionVersion in the laws SHACL bucket.
        "estleg:hasVersion",
    }
)

PROP_TYPES = frozenset(
    {
        "owl:ObjectProperty",
        "owl:DatatypeProperty",
        "owl:AnnotationProperty",
        "rdf:Property",
        "owl:FunctionalProperty",
    }
)
CLASS_TYPES = frozenset({"owl:Class", "rdfs:Class"})
PLACEHOLDER_NEEDLE = "materialized for vocabulary coverage"

SCHEMA_RELPATHS: dict[str, str] = {
    "eelnoud": "eelnoud/eelnoud_schema.json",
    "riigikohus": "riigikohus/riigikohus_schema.json",
    "eurlex": "eurlex/eurlex_schema.json",
    "curia": "curia/curia_schema.json",
}

PREFERRED_KEYS = (
    "@id",
    "@type",
    "owl:versionInfo",
    "owl:versionIRI",
    "rdfs:subClassOf",
    "rdfs:subPropertyOf",
    "owl:disjointWith",
    "owl:equivalentClass",
    "owl:deprecated",
    "rdfs:label",
    "skos:prefLabel",
    "rdfs:comment",
    "dc:description",
    "rdfs:domain",
    "rdfs:range",
    "owl:inverseOf",
    "owl:FunctionalProperty",
)

# Real comments replacing the "Reusable … materialized" placeholders.
REAL_COMMENTS: dict[str, str] = {
    "estleg:Annex": (
        "An annex (lisa) attached to an act. Structural sibling of Chapter / "
        "Division, not a LegalProvision."
    ),
    "estleg:Chapter": (
        "A chapter (peatükk) grouping provisions inside an act."
    ),
    "estleg:Competence": (
        "An institutional competence assertion: an institution is competent "
        "for a provision or subject area."
    ),
    "estleg:Division": (
        "A division (jagu / jaotis) grouping provisions inside a chapter."
    ),
    "estleg:Institution": (
        "A public institution that issues, enforces, or is competent for law. "
        "Superclass of estleg:Issuer and estleg:EUInstitution."
    ),
    "estleg:NormativeType": (
        "Closed deontic class of a provision or lõige (obligation, permission, "
        "prohibition, competence, or other)."
    ),
    "estleg:Sanction": (
        "A sanction or penalty attached to a provision or lõige."
    ),
    "estleg:Subdivision": (
        "A finer subdivision (alljaotis) under a Division."
    ),
    "estleg:UnresolvedReferencePlaceholder": (
        "Placeholder individual for a cited provision IRI that is not in the "
        "corpus. Kept so dangling citation targets stay dereferenceable."
    ),
    "estleg:Municipality": (
        "A current Estonian municipality (KOV unit), identified by EHAK code."
    ),
    "estleg:actNumber": (
        "Official act number or RT identifier string on an act node."
    ),
    "estleg:affectedBy": (
        "Links an act or provision to an AmendmentEvent that changes it."
    ),
    "estleg:amendedBy": (
        "Links an act to an AmendmentEvent that amended it. Inverse of "
        "estleg:amends."
    ),
    "estleg:amendingDraft": (
        "Links an enacted act to a draft bill that proposes to amend it."
    ),
    "estleg:amendmentDate": (
        "Date an AmendmentEvent took effect, as xsd:date."
    ),
    "estleg:amends": (
        "Links an AmendmentEvent to the act it amends. Inverse of "
        "estleg:amendedBy."
    ),
    "estleg:annexNumber": (
        "Ordinal or official number of an Annex."
    ),
    "estleg:applicableProvision": (
        "Provision that a court decision or annotation treats as applicable."
    ),
    "estleg:appliesToProvision": (
        "Links a competence, sanction, or annotation to the provision it "
        "applies to."
    ),
    "estleg:belongsToCluster": (
        "Topic-cluster key a concept or provision was assigned to."
    ),
    "estleg:caseTypeCode": (
        "Short code of a CaseType individual (e.g. criminal, civil)."
    ),
    "estleg:coversConcept": (
        "Links a structural part to a LegalConcept it covers. Unaxiomatised "
        "(#377) so SHACL rdfs inference does not mint types on stubs."
    ),
    "estleg:documentType": (
        "Free-text or coded document-type label on an act or draft."
    ),
    "estleg:euCourtCode": (
        "Short code of an EUCourt individual."
    ),
    "estleg:euInstitutionCode": (
        "Short CELLAR / authority code of an EUInstitution individual."
    ),
    "estleg:globalId": (
        "Riigi Teataja globaalID of the source document."
    ),
    "estleg:hasAnnex": (
        "Links an act to an Annex it contains."
    ),
    "estleg:hasProvision": (
        "Links a structural container to a LegalProvision. Unaxiomatised "
        "(#377) so SHACL rdfs inference does not mint types on stubs."
    ),
    "estleg:hasProposedAmendment": (
        "Links an act to a ProposedAmendment (non-enacted draft proposal)."
    ),
    "estleg:hasSanction": (
        "Links a provision or lõige to a Sanction node."
    ),
    "estleg:inChapter": (
        "Links a provision or division to the Chapter that contains it."
    ),
    "estleg:inDivision": (
        "Links a provision to the Division that contains it."
    ),
    "estleg:inPart": (
        "Links a provision or chapter to the Part (osa) that contains it."
    ),
    "estleg:institution": (
        "Links a competence node to the Institution it names."
    ),
    "estleg:institutionType": (
        "Kind of institution (ministry, board, court, municipality, …)."
    ),
    "estleg:interpretedBy": (
        "Links a provision or act to a CourtDecision that interprets it. "
        "Inverse of estleg:interpretsLaw."
    ),
    "estleg:isCurrentAmendment": (
        "Boolean: this AmendmentEvent is the latest applied change."
    ),
    "estleg:isKov": (
        "Boolean marker that an act or issuer is municipal (KOV)."
    ),
    "estleg:issuer": (
        "Literal name of the issuing body when a structured Issuer node is "
        "not available."
    ),
    "estleg:jurisdiction": (
        "Jurisdiction label (Estonia, EU, municipality name)."
    ),
    "estleg:lastAmendmentDate": (
        "Most recent amendment date on an act, derived from version dates."
    ),
    "estleg:legalText": (
        "Verbatim provision or lõige text from the source consolidation."
    ),
    "estleg:mappedPart": (
        "Human-readable part label on a topic-map or summary node."
    ),
    "estleg:mapsToGeneralPart": (
        "Boolean or IRI indicating a mapping into a general-part concept."
    ),
    "estleg:normativeType": (
        "Deontic classification of a provision or lõige (NormativeType)."
    ),
    "estleg:paragrahv": (
        "Section number (§) of a LegalProvision, as a string (e.g. '12' or "
        "'22_1' for § 22¹)."
    ),
    "estleg:phaseOrder": (
        "Integer order of a LegislativePhase individual in the EIS pipeline."
    ),
    "estleg:preambleText": (
        "Preamble / enacting-clause text of an act or regulation."
    ),
    "estleg:proposesToAmend": (
        "Links a ProposedAmendment to the act it would amend."
    ),
    "estleg:referencedBy": (
        "Inverse of estleg:references: provisions that cite this one."
    ),
    "estleg:references": (
        "Citation from one provision (or Citation node) to another provision."
    ),
    "estleg:relatesToConcept": (
        "Loose topical link from a provision or act to a LegalConcept."
    ),
    "estleg:requestedCluster": (
        "Requested topic-cluster assignment used during concept extraction."
    ),
    "estleg:rtReference": (
        "Riigi Teataja reference string on an AmendmentEvent."
    ),
    "estleg:sanctionType": (
        "Kind of sanction (fine, imprisonment, withdrawal of right, …)."
    ),
    "estleg:schemaVersion": (
        "Generator schema version token stamped on a dataset or ontology node."
    ),
    "estleg:sectionNumber": (
        "Number of a Section / jagu structural node."
    ),
    "estleg:semanticallySimilarTo": (
        "Heuristic similarity link between two acts or provisions."
    ),
    "estleg:sourceAct": (
        "Literal title of the parent act. Not a graph join — use partOfAct."
    ),
    "estleg:sourceGlobaalID": (
        "Riigi Teataja globaalID copied onto a derived or summary node."
    ),
    "estleg:sourceStructure": (
        "Free-text description of the source document's structure."
    ),
    "estleg:sourceUrl": (
        "HTTP(S) URL of the source document or HTML consolidation."
    ),
    "estleg:summary": (
        "Short prose summary of an act, provision, or court decision."
    ),
    "estleg:terviktekstId": (
        "Riigi Teataja terviktekst identifier of a consolidation."
    ),
    "estleg:totalAmendments": (
        "Count of AmendmentEvent nodes recorded for an act."
    ),
    "estleg:totalConcepts": (
        "Count of LegalConcept nodes extracted from an act."
    ),
}

# Explicit domain/range for properties that lack one or both. Forbidden
# properties are omitted. Cross-bucket object properties use rdfs:Resource
# when a shaped class would type stubs under RDFS inference.
DOMAIN_RANGE: dict[str, tuple[str, str]] = {
    "estleg:hasExpression": ("estleg:Act", "estleg:ActExpression"),
    "estleg:actNumber": ("estleg:Act", "xsd:string"),
    "estleg:affectedBy": ("estleg:Act", "rdfs:Resource"),
    "estleg:amendedBy": ("estleg:Act", "rdfs:Resource"),
    "estleg:amendingDraft": ("estleg:Act", "rdfs:Resource"),
    "estleg:amendmentDate": ("estleg:AmendmentEvent", "xsd:date"),
    "estleg:amends": ("estleg:AmendmentEvent", "rdfs:Resource"),
    "estleg:annexNumber": ("estleg:Annex", "xsd:string"),
    "estleg:applicableProvision": ("estleg:CourtDecision", "rdfs:Resource"),
    "estleg:appliesToProvision": ("owl:Thing", "rdfs:Resource"),
    "estleg:appliesToProvisionCount": ("owl:Thing", "xsd:integer"),
    "estleg:assertionConfidence": ("owl:Thing", "xsd:decimal"),
    "estleg:belongsToCluster": ("owl:Thing", "xsd:string"),
    "estleg:caseTypeCode": ("estleg:CaseType", "xsd:string"),
    "estleg:chapterNumber": ("estleg:Chapter", "xsd:string"),
    "estleg:competenceArea": ("owl:Thing", "xsd:string"),
    "estleg:competenceType": ("owl:Thing", "xsd:string"),
    "estleg:competentAuthority": ("owl:Thing", "rdfs:Resource"),
    "estleg:contentStatus": ("estleg:Act", "xsd:string"),
    "estleg:contentStatusReason": ("estleg:Act", "xsd:string"),
    "estleg:changeType": ("estleg:ProposedAmendment", "xsd:string"),
    "estleg:definedIn": ("estleg:LegalConcept", "rdfs:Resource"),
    "estleg:definesConcept": ("estleg:LegalProvision", "estleg:LegalConcept"),
    "estleg:definitionCount": ("estleg:Act", "xsd:integer"),
    "estleg:definitionVariantCount": ("estleg:LegalConcept", "xsd:integer"),
    "estleg:documentType": ("owl:Thing", "xsd:string"),
    "estleg:entryIntoForce": ("owl:Thing", "xsd:date"),
    "estleg:euCourtCode": ("estleg:EUCourt", "xsd:string"),
    "estleg:euInstitutionCode": ("estleg:EUInstitution", "xsd:string"),
    "estleg:globalId": ("owl:Thing", "xsd:string"),
    "estleg:grantedBy": ("owl:Thing", "rdfs:Resource"),
    "estleg:hasAnnex": ("estleg:Act", "estleg:Annex"),
    "estleg:hasDefinitionNode": ("estleg:LegalConcept", "rdfs:Resource"),
    "estleg:hasProposedAmendment": ("estleg:Act", "estleg:ProposedAmendment"),
    "estleg:hasPart": ("owl:Thing", "rdfs:Resource"),
    "estleg:hasSanction": ("owl:Thing", "rdfs:Resource"),
    "estleg:inChapter": ("owl:Thing", "estleg:Chapter"),
    "estleg:inDivision": ("owl:Thing", "estleg:Division"),
    "estleg:inPart": ("owl:Thing", "estleg:Part"),
    "estleg:institution": ("owl:Thing", "rdfs:Resource"),
    "estleg:institutionType": ("estleg:Institution", "xsd:string"),
    "estleg:interpretedBy": ("owl:Thing", "rdfs:Resource"),
    "estleg:interpretsLaw": ("estleg:CourtDecision", "rdfs:Resource"),
    "estleg:interpretsVersion": ("estleg:CourtDecision", "estleg:ProvisionVersion"),
    "estleg:isCurrentAmendment": ("estleg:AmendmentEvent", "xsd:boolean"),
    "estleg:isKov": ("owl:Thing", "xsd:boolean"),
    "estleg:isPartOf": ("owl:Thing", "rdfs:Resource"),
    "estleg:isStatutoryDefault": ("owl:Thing", "xsd:boolean"),
    "estleg:isStubNode": ("owl:Thing", "xsd:boolean"),
    "estleg:issuer": ("estleg:Act", "xsd:string"),
    "estleg:jurisdiction": ("owl:Thing", "xsd:string"),
    "estleg:kehtiv": ("estleg:Act", "xsd:date"),
    "estleg:lastAmendmentDate": ("estleg:Act", "xsd:date"),
    "estleg:legalText": ("owl:Thing", "xsd:string"),
    "estleg:mappedPart": ("owl:Thing", "xsd:string"),
    "estleg:mapsToGeneralPart": ("owl:Thing", "xsd:string"),
    "estleg:maxPenalty": ("estleg:Sanction", "xsd:string"),
    "estleg:maxPenaltyAmount": ("estleg:Sanction", "xsd:decimal"),
    "estleg:maxPenaltyCurrency": ("estleg:Sanction", "xsd:string"),
    "estleg:maxPenaltyUnit": ("estleg:Sanction", "xsd:string"),
    "estleg:minPenalty": ("estleg:Sanction", "xsd:string"),
    "estleg:minPenaltyAmount": ("estleg:Sanction", "xsd:decimal"),
    "estleg:minPenaltyCurrency": ("estleg:Sanction", "xsd:string"),
    "estleg:minPenaltyUnit": ("estleg:Sanction", "xsd:string"),
    "estleg:normativeType": ("owl:Thing", "estleg:NormativeType"),
    "estleg:paragrahv": ("estleg:LegalProvision", "xsd:string"),
    "estleg:phaseOrder": ("estleg:LegislativePhase", "xsd:integer"),
    "estleg:preambleText": ("estleg:Act", "xsd:string"),
    "estleg:proposesToAmend": ("estleg:ProposedAmendment", "estleg:Act"),
    "estleg:provisionCount": ("estleg:Act", "xsd:integer"),
    "estleg:referencedBy": ("owl:Thing", "rdfs:Resource"),
    "estleg:references": ("owl:Thing", "rdfs:Resource"),
    "estleg:relatesToConcept": ("owl:Thing", "xsd:string"),
    "estleg:repealDate": ("estleg:Act", "xsd:date"),
    "estleg:requestedCluster": ("owl:Thing", "rdfs:Resource"),
    "estleg:rtReference": ("estleg:AmendmentEvent", "xsd:string"),
    "estleg:sanctionType": ("estleg:Sanction", "xsd:string"),
    "estleg:schemaVersion": ("owl:Thing", "xsd:string"),
    "estleg:sectionNumber": ("owl:Thing", "xsd:string"),
    "estleg:semanticallySimilarTo": ("estleg:Act", "rdfs:Resource"),
    "estleg:sourceAct": ("owl:Thing", "xsd:string"),
    "estleg:sourceGlobaalID": ("owl:Thing", "xsd:string"),
    "estleg:sourceStructure": ("owl:Thing", "xsd:string"),
    "estleg:sourceUrl": ("owl:Thing", "xsd:anyURI"),
    "estleg:summary": ("owl:Thing", "xsd:string"),
    "estleg:temporalStatus": ("estleg:Act", "xsd:string"),
    "estleg:consistencyChecked": ("owl:Thing", "xsd:boolean"),
    "estleg:targetGroup": ("owl:Thing", "xsd:string"),
    "estleg:terviktekstId": ("owl:Thing", "xsd:string"),
    "estleg:totalAmendments": ("estleg:Act", "xsd:integer"),
    "estleg:totalConcepts": ("estleg:Act", "xsd:integer"),
    "estleg:transpositionDeadline": ("estleg:Act", "xsd:date"),
    "estleg:referenceStatus": ("owl:Thing", "xsd:string"),
    "estleg:similarityScore": ("estleg:Similarity", "xsd:decimal"),
    "estleg:similarityStatus": ("estleg:Similarity", "xsd:string"),
    "estleg:similarAct": ("estleg:Act", "estleg:Similarity"),
    "estleg:similarTarget": ("estleg:Similarity", "rdfs:Resource"),
    "estleg:similarityModel": ("estleg:Similarity", "xsd:string"),
    "estleg:regulationTypeBucket": ("estleg:MunicipalRegulation", "xsd:string"),
    "estleg:versionOf": ("estleg:ProvisionVersion", "estleg:LegalProvision"),
    "estleg:versionValidFrom": ("estleg:ProvisionVersion", "xsd:date"),
    "estleg:versionValidTo": ("estleg:ProvisionVersion", "xsd:date"),
    "estleg:versionText": ("estleg:ProvisionVersion", "xsd:string"),
    "estleg:versionRedactionId": ("estleg:ProvisionVersion", "xsd:string"),
    "estleg:supersededByVersion": (
        "estleg:ProvisionVersion",
        "estleg:ProvisionVersion",
    ),
    "estleg:hasVersion": ("estleg:LegalProvision", ""),
    "estleg:currentVersion": ("estleg:LegalProvision", "estleg:ProvisionVersion"),
    "estleg:subsectionNumber": ("estleg:Subsection", "xsd:string"),
    "estleg:hasSubsection": ("estleg:LegalProvision", "estleg:Subsection"),
    "estleg:parentProvision": ("estleg:Subsection", "estleg:LegalProvision"),
    "estleg:annotates": ("estleg:Annotation", "rdfs:Resource"),
    "estleg:annotationText": ("estleg:Annotation", "xsd:string"),
    "estleg:annotationSource": ("estleg:Annotation", "xsd:string"),
    "estleg:annotationSourceUrl": ("estleg:Annotation", "xsd:anyURI"),
    "estleg:annotationDate": ("estleg:Annotation", "xsd:date"),
    "estleg:annotationType": ("estleg:Annotation", "xsd:string"),
    "estleg:formerEhakCode": ("estleg:HistoricalMunicipality", "xsd:string"),
    "estleg:formerName": ("estleg:HistoricalMunicipality", "xsd:string"),
    "estleg:succeededBy": (
        "estleg:HistoricalMunicipality",
        "estleg:Municipality",
    ),
    "estleg:mergedAt": ("estleg:HistoricalMunicipality", "xsd:date"),
    "estleg:mergerEvidence": ("estleg:HistoricalMunicipality", "xsd:string"),
    "estleg:municipalityType": ("estleg:Municipality", "xsd:string"),
    "estleg:publicationYear": ("owl:Thing", "xsd:gYear"),
    "estleg:enactedAs": ("estleg:DraftLegislation", "rdfs:Resource"),
    "estleg:municipalityStatus": ("estleg:Municipality", "xsd:string"),
    "estleg:repeals": ("owl:Thing", "rdfs:Resource"),
    "estleg:isLegalBasisFor": ("owl:Thing", "rdfs:Resource"),
    "estleg:exceptionTo": ("owl:Thing", "rdfs:Resource"),
    "estleg:derogatesFrom": ("owl:Thing", "rdfs:Resource"),
    "estleg:enactedBy": ("estleg:Act", "estleg:Issuer"),
    "estleg:enactedByMunicipality": ("estleg:Act", "estleg:Municipality"),
    "estleg:titleNormalized": ("estleg:Act", "xsd:string"),
    "estleg:ehakCode": ("estleg:Municipality", "xsd:string"),
    "estleg:county": ("estleg:Municipality", "xsd:string"),
    "estleg:bodyType": ("estleg:Issuer", "xsd:string"),
    "estleg:currentMunicipality": ("estleg:Issuer", "estleg:Municipality"),
    "estleg:historicalMunicipalityName": ("estleg:Issuer", "xsd:string"),
    "estleg:mappingSource": ("estleg:Issuer", "xsd:string"),
    "estleg:mappingEvidence": ("estleg:Issuer", "xsd:string"),
    "estleg:citationTarget": ("estleg:Citation", "rdfs:Resource"),
    "estleg:citationDetail": ("estleg:Citation", "xsd:string"),
    "estleg:citationText": ("estleg:Citation", "xsd:string"),
    "estleg:issuedUnder": ("estleg:Act", "rdfs:Resource"),
    "estleg:implementsCitation": ("estleg:Act", "estleg:Citation"),
    "estleg:implementedBy": ("estleg:Act", "rdfs:Resource"),
    "estleg:implementedByCount": ("estleg:Act", "xsd:integer"),
    "estleg:enforcedAtLevel": ("owl:Thing", "xsd:string"),
    "estleg:added": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:removed": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:addedCount": ("estleg:ReleaseDelta", "xsd:integer"),
    "estleg:removedCount": ("estleg:ReleaseDelta", "xsd:integer"),
    "estleg:containsPersonalData": ("dcat:Distribution", "xsd:boolean"),
    "estleg:legislativePhase": (
        "estleg:DraftLegislation",
        "estleg:LegislativePhase",
    ),
    "estleg:draftType": ("estleg:DraftLegislation", "estleg:DraftType"),
    "estleg:amendsLaw": ("estleg:DraftLegislation", "rdfs:Resource"),
    "estleg:eisNumber": ("estleg:DraftLegislation", "xsd:string"),
    "estleg:eisLink": ("estleg:DraftLegislation", "xsd:anyURI"),
    "estleg:initiator": ("estleg:DraftLegislation", "xsd:string"),
    "estleg:publicationDate": ("owl:Thing", "xsd:date"),
    "estleg:affectedLawName": ("estleg:DraftLegislation", "xsd:string"),
    "estleg:caseType": ("estleg:CourtDecision", "estleg:CaseType"),
    "estleg:decisionType": ("estleg:CourtDecision", "estleg:DecisionType"),
    "estleg:caseNumber": ("estleg:CourtDecision", "xsd:string"),
    "estleg:decisionDate": ("estleg:CourtDecision", "xsd:date"),
    "estleg:rikObjectId": ("estleg:CourtDecision", "xsd:string"),
    "estleg:rikosUrl": ("estleg:CourtDecision", "xsd:anyURI"),
    "estleg:chamber": ("estleg:CourtDecision", "xsd:string"),
    "estleg:decisionLink": ("estleg:CourtDecision", "xsd:anyURI"),
    "estleg:ecliIdentifier": ("owl:Thing", "xsd:string"),
    "estleg:referencedLaw": ("estleg:CourtDecision", "xsd:string"),
    "estleg:euDocumentType": (
        "estleg:EULegislation",
        "estleg:EUDocumentType",
    ),
    "estleg:euInstitution": ("estleg:EULegislation", "estleg:EUInstitution"),
    "estleg:celexNumber": ("estleg:EULegislation", "xsd:string"),
    "estleg:eliIdentifier": ("owl:Thing", "xsd:string"),
    "estleg:eurLexLink": ("owl:Thing", "xsd:anyURI"),
    "estleg:documentDate": ("owl:Thing", "xsd:date"),
    "estleg:inForce": ("estleg:EULegislation", "xsd:boolean"),
    "estleg:euCourtDecisionType": (
        "estleg:EUCourtDecision",
        "estleg:EUCourtDecisionType",
    ),
    "estleg:euCourt": ("estleg:EUCourtDecision", "estleg:EUCourt"),
    "estleg:euCaseNumber": ("estleg:EUCourtDecision", "xsd:string"),
}

# Domains that schema files over-narrow (used on acts/amendments too).
# Applied after merge so they win over schema copies.
OVERWRITE_DOMAIN: dict[str, str] = {
    "estleg:publicationDate": "owl:Thing",
    "estleg:competentAuthority": "owl:Thing",
    "estleg:legalText": "owl:Thing",
    "estleg:normativeType": "owl:Thing",
    "estleg:targetGroup": "owl:Thing",
    "estleg:hasSanction": "owl:Thing",
    "estleg:hasVersion": "owl:Thing",
    "estleg:competenceType": "owl:Thing",
    "estleg:competenceArea": "owl:Thing",
    "estleg:institution": "owl:Thing",
    "estleg:grantedBy": "owl:Thing",
    "estleg:enforcedAtLevel": "owl:Thing",
}
OVERWRITE_RANGE: dict[str, str] = {
    "estleg:amendsLaw": "rdfs:Resource",
    "estleg:interpretedBy": "rdfs:Resource",
    "estleg:amendedBy": "rdfs:Resource",
    "estleg:amends": "rdfs:Resource",
    "estleg:amendingDraft": "rdfs:Resource",
    "estleg:competentAuthority": "rdfs:Resource",
    "estleg:hasSanction": "rdfs:Resource",
    "estleg:institution": "rdfs:Resource",
    "estleg:grantedBy": "rdfs:Resource",
    "estleg:implementedBy": "rdfs:Resource",
    "estleg:issuedUnder": "rdfs:Resource",
}


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def node_types(node: dict) -> list[str]:
    return [item for item in as_list(node.get("@type")) if isinstance(item, str)]


def is_property_node(node: dict) -> bool:
    return bool(set(node_types(node)) & PROP_TYPES)


def is_class_node(node: dict) -> bool:
    return bool(set(node_types(node)) & CLASS_TYPES)


def is_unresolved_individual(node: dict) -> bool:
    return any(
        item.endswith("UnresolvedReferencePlaceholder") for item in node_types(node)
    ) and "owl:Class" not in node_types(node)


def comment_text(node: dict) -> str:
    comment = node.get("rdfs:comment", "")
    if isinstance(comment, list):
        parts: list[str] = []
        for item in comment:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("@value") is not None:
                parts.append(str(item["@value"]))
        return " ".join(parts)
    if isinstance(comment, dict):
        return str(comment.get("@value", ""))
    return str(comment)


def has_placeholder_comment(node: dict) -> bool:
    return PLACEHOLDER_NEEDLE in comment_text(node)


def iri_ref(iri: str) -> dict[str, str]:
    return {"@id": iri}


def load_jsonld(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def graph_nodes(doc: dict) -> list[dict]:
    graph = doc.get("@graph")
    if isinstance(graph, list):
        return [item for item in graph if isinstance(item, dict)]
    return []


def index_by_id(nodes: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for node in nodes:
        nid = node.get("@id")
        if isinstance(nid, str) and nid:
            out[nid] = node
    return out


def normalize_node(node: dict) -> dict:
    ordered: dict[str, Any] = {}
    for key in PREFERRED_KEYS:
        if key in node:
            ordered[key] = node[key]
    for key, value in node.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def ontology_header() -> dict:
    return {
        "@id": VOCABULARY_IRI,
        "@type": "owl:Ontology",
        "owl:versionInfo": ONTOLOGY_VERSION,
        "rdfs:label": [
            {"@value": "Estonian Legal Ontology vocabulary", "@language": "en"},
            {"@value": "Eesti õigusontoloogia sõnavara", "@language": "et"},
        ],
        "rdfs:comment": (
            "Canonical T-Box for https://w3id.org/estleg/ — classes, class "
            "hierarchy, and properties in the default graph (#433)."
        ),
        "dcterms:title": {
            "@value": "Estonian Legal Ontology vocabulary",
            "@language": "en",
        },
    }


def merge_node(existing: dict, incoming: dict) -> dict:
    """Fill gaps on ``existing`` from ``incoming`` without clobbering axioms."""
    merged = copy.deepcopy(existing)
    if has_placeholder_comment(merged) and incoming.get("rdfs:comment"):
        if PLACEHOLDER_NEEDLE not in comment_text(incoming):
            merged["rdfs:comment"] = incoming["rdfs:comment"]
    elif not merged.get("rdfs:comment") and incoming.get("rdfs:comment"):
        merged["rdfs:comment"] = incoming["rdfs:comment"]
    if not merged.get("rdfs:label") and incoming.get("rdfs:label"):
        merged["rdfs:label"] = incoming["rdfs:label"]
    if not merged.get("rdfs:subClassOf") and incoming.get("rdfs:subClassOf"):
        merged["rdfs:subClassOf"] = incoming["rdfs:subClassOf"]
    nid = merged.get("@id")
    if nid not in FORBIDDEN_NO_AXIOM:
        if "rdfs:domain" not in merged and "rdfs:domain" in incoming:
            merged["rdfs:domain"] = incoming["rdfs:domain"]
        if (
            nid not in FORBIDDEN_NO_RANGE
            and "rdfs:range" not in merged
            and "rdfs:range" in incoming
        ):
            merged["rdfs:range"] = incoming["rdfs:range"]
    incoming_types = node_types(incoming)
    if incoming_types:
        current = node_types(merged)
        extra = [item for item in incoming_types if item not in current]
        if extra:
            merged["@type"] = current + extra if current else incoming_types
    return merged


def apply_comment(node: dict) -> None:
    nid = node.get("@id")
    if not isinstance(nid, str):
        return
    if nid in REAL_COMMENTS and (
        has_placeholder_comment(node) or not comment_text(node)
    ):
        node["rdfs:comment"] = REAL_COMMENTS[nid]
        return
    if has_placeholder_comment(node):
        label = node.get("rdfs:label", nid)
        if isinstance(label, list):
            label = next(
                (
                    item.get("@value", nid)
                    if isinstance(item, dict)
                    else str(item)
                    for item in label
                ),
                nid,
            )
        elif isinstance(label, dict):
            label = label.get("@value", nid)
        kind = "class" if is_class_node(node) else "property"
        node["rdfs:comment"] = (
            f"{label}: reusable {kind} in the Estonian Legal Ontology T-Box."
        )


def apply_domain_range(node: dict) -> None:
    nid = node.get("@id")
    if not isinstance(nid, str) or not is_property_node(node):
        return
    if nid in FORBIDDEN_NO_AXIOM:
        node.pop("rdfs:domain", None)
        node.pop("rdfs:range", None)
        return
    if nid in OVERWRITE_DOMAIN:
        node["rdfs:domain"] = iri_ref(OVERWRITE_DOMAIN[nid])
    if nid in OVERWRITE_RANGE:
        node["rdfs:range"] = iri_ref(OVERWRITE_RANGE[nid])
    domain, range_iri = DOMAIN_RANGE.get(nid, (None, None))
    if domain and "rdfs:domain" not in node:
        node["rdfs:domain"] = iri_ref(domain)
    if nid in FORBIDDEN_NO_RANGE:
        node.pop("rdfs:range", None)
        return
    if range_iri and "rdfs:range" not in node:
        node["rdfs:range"] = iri_ref(range_iri)
    if "rdfs:domain" not in node:
        node["rdfs:domain"] = iri_ref("owl:Thing")
    if "rdfs:range" not in node:
        types = set(node_types(node))
        if "owl:ObjectProperty" in types:
            node["rdfs:range"] = iri_ref("rdfs:Resource")
        else:
            node["rdfs:range"] = iri_ref("xsd:string")


def iter_merge_sources(krr_dir: Path = KRR_DIR) -> Iterator[dict]:
    metadata = load_jsonld(METADATA_PATH)
    yield from graph_nodes(metadata)
    for rel in SCHEMA_RELPATHS.values():
        path = krr_dir / rel
        if not path.is_file():
            continue
        yield from graph_nodes(load_jsonld(path))


def build_consolidated_graph(
    vocab_doc: dict,
    extra_sources: Iterable[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (vocabulary nodes, unresolved individuals)."""
    index = index_by_id(copy.deepcopy(node) for node in graph_nodes(vocab_doc))
    sources = extra_sources if extra_sources is not None else iter_merge_sources()
    for incoming in sources:
        nid = incoming.get("@id")
        if not isinstance(nid, str) or not nid:
            continue
        if nid in JUNK_TERMS:
            continue
        if nid in index:
            index[nid] = merge_node(index[nid], incoming)
        else:
            index[nid] = copy.deepcopy(incoming)

    for junk in JUNK_TERMS:
        index.pop(junk, None)

    unresolved: list[dict] = []
    for nid, node in list(index.items()):
        if is_unresolved_individual(node):
            unresolved.append(normalize_node(node))
            del index[nid]

    index[VOCABULARY_IRI] = ontology_header()

    for node in index.values():
        apply_comment(node)
        apply_domain_range(node)

    classes: list[dict] = []
    props: list[dict] = []
    other: list[dict] = []
    header = normalize_node(index.pop(VOCABULARY_IRI))
    for nid, node in sorted(index.items()):
        node = normalize_node(node)
        if is_class_node(node):
            classes.append(node)
        elif is_property_node(node):
            props.append(node)
        else:
            other.append(node)
    graph = [header, *classes, *props, *other]
    unresolved.sort(key=lambda node: str(node.get("@id", "")))
    return graph, unresolved


def dump_jsonld(path: Path, doc: dict) -> None:
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_unresolved(nodes: list[dict], path: Path = UNRESOLVED_PATH) -> None:
    dump_jsonld(
        path,
        {
            "@context": {
                "estleg": "https://w3id.org/estleg/",
                "owl": "http://www.w3.org/2002/07/owl#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            },
            "@id": "https://w3id.org/estleg/unresolved-references",
            "@type": "owl:Ontology",
            "rdfs:label": "Unresolved reference placeholders",
            "rdfs:comment": (
                "ABox individuals typed estleg:UnresolvedReferencePlaceholder, "
                "moved out of the canonical T-Box (#433)."
            ),
            "@graph": nodes,
        },
    )


def strip_metadata_tbox(metadata_doc: dict) -> dict:
    cleaned = {key: value for key, value in metadata_doc.items() if key != "@graph"}
    cleaned["dcterms:conformsTo"] = {"@id": VOCABULARY_IRI}
    return cleaned


def remap_junk_predicates(node: dict) -> bool:
    """Rewrite junk estleg: predicates onto standard terms. Returns changed."""
    changed = False
    if "estleg:note" in node:
        value = node.pop("estleg:note")
        if "rdfs:comment" in node:
            node["dcterms:description"] = value
        else:
            node["rdfs:comment"] = value
        changed = True
    if "estleg:scope" in node:
        node["dcterms:abstract"] = node.pop("estleg:scope")
        changed = True
    if "estleg:title" in node:
        value = node.pop("estleg:title")
        node.setdefault("dcterms:title", value)
        changed = True
    if "estleg:jsonld" in node:
        node["rdfs:seeAlso"] = node.pop("estleg:jsonld")
        changed = True
    if "estleg:counts" in node:
        counts = node.pop("estleg:counts")
        if isinstance(counts, dict):
            node["dcterms:description"] = ", ".join(
                f"{key}: {value}" for key, value in counts.items()
            )
        else:
            node["dcterms:description"] = counts
        changed = True
    return changed


def remap_junk_document(doc: dict) -> bool:
    changed = False
    if remap_junk_predicates(doc):
        changed = True
    for node in graph_nodes(doc):
        if remap_junk_predicates(node):
            changed = True
    return changed


def remap_junk_files(krr_dir: Path = KRR_DIR) -> list[Path]:
    touched: list[Path] = []
    for path in sorted(krr_dir.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonld"}:
            continue
        if path.name in {
            "controlled_vocabulary.jsonld",
            "combined_ontology.jsonld",
            "unresolved_references.jsonld",
        }:
            continue
        if "combined" in path.name:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(term in raw for term in JUNK_TERMS):
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, dict):
            continue
        if remap_junk_document(doc):
            dump_jsonld(path, doc)
            touched.append(path)
    return touched


def schema_paths(krr_dir: Path = KRR_DIR) -> dict[str, Path]:
    return {name: krr_dir / rel for name, rel in SCHEMA_RELPATHS.items()}


def schema_term_ids(path: Path) -> list[str]:
    doc = load_jsonld(path)
    return [
        node["@id"]
        for node in graph_nodes(doc)
        if isinstance(node.get("@id"), str)
    ]


def project_schema_nodes(vocab_index: dict[str, dict], term_ids: Iterable[str]) -> list[dict]:
    nodes: list[dict] = []
    for nid in term_ids:
        node = vocab_index.get(nid)
        if node is not None:
            nodes.append(copy.deepcopy(node))
    return nodes


def schema_ids_missing_from_vocab(
    vocab_index: dict[str, dict], krr_dir: Path = KRR_DIR
) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for name, path in schema_paths(krr_dir).items():
        if not path.is_file():
            missing[name] = [f"<missing file {path}>"]
            continue
        absent = [nid for nid in schema_term_ids(path) if nid not in vocab_index]
        if absent:
            missing[name] = absent
    return missing


def write_schemas_from_cv(
    vocab_doc: dict,
    krr_dir: Path = KRR_DIR,
    *,
    check_only: bool = False,
) -> dict[str, list[str]]:
    index = index_by_id(graph_nodes(vocab_doc))
    missing = schema_ids_missing_from_vocab(index, krr_dir)
    if check_only or missing:
        return missing
    for _name, path in schema_paths(krr_dir).items():
        original = load_jsonld(path)
        term_ids = schema_term_ids(path)
        original["@graph"] = project_schema_nodes(index, term_ids)
        dump_jsonld(path, original)
    return {}


def property_coverage(nodes: Iterable[dict]) -> tuple[int, int, list[str]]:
    props = [node for node in nodes if is_property_node(node)]
    both: list[str] = []
    missing: list[str] = []
    for node in props:
        nid = str(node.get("@id"))
        if "rdfs:domain" in node and "rdfs:range" in node:
            both.append(nid)
        else:
            missing.append(nid)
    return len(both), len(props), missing


def placeholder_ids(nodes: Iterable[dict]) -> list[str]:
    return [
        str(node.get("@id"))
        for node in nodes
        if has_placeholder_comment(node)
    ]


def _skip_ws_comma(text: str, index: int) -> int:
    length = len(text)
    while index < length and text[index] in " \t\r\n,":
        index += 1
    return index


def patch_combined_graph(
    path: Path,
    *,
    drop_ids: set[str],
    extra_nodes: list[dict],
) -> tuple[int, int]:
    """Drop and append T-Box nodes in combined_ontology.jsonld without a full parse.

    Returns (dropped, appended).
    """
    text = path.read_text(encoding="utf-8")
    marker = text.find('"@graph"')
    if marker < 0:
        raise ValueError(f"{path}: no @graph")
    bracket = text.find("[", marker)
    if bracket < 0:
        raise ValueError(f"{path}: @graph is not an array")
    decoder = json.JSONDecoder()
    index = bracket + 1
    length = len(text)
    tmp = path.with_suffix(path.suffix + ".tmp")
    dropped = 0
    appended = 0
    seen: set[str] = set()
    first = True
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text[: bracket + 1])
        # Keep `[` and the first object on separate lines so head-parsers
        # that count braces per line still see @graph[0] (#517).
        if not text[bracket + 1 : bracket + 2].startswith("\n"):
            handle.write("\n")
        while True:
            index = _skip_ws_comma(text, index)
            if index >= length:
                raise ValueError(f"{path}: unterminated @graph")
            if text[index] == "]":
                break
            _obj, end = decoder.raw_decode(text, index)
            nid = _obj.get("@id") if isinstance(_obj, dict) else None
            if isinstance(nid, str) and nid in drop_ids:
                dropped += 1
                index = end
                continue
            if isinstance(nid, str):
                seen.add(nid)
            if not first:
                handle.write(",\n")
            else:
                first = False
            handle.write(text[index:end])
            index = end
        for node in extra_nodes:
            nid = node.get("@id")
            if not isinstance(nid, str) or nid in seen:
                continue
            dumped = json.dumps(node, ensure_ascii=False, indent=2)
            indented = "\n".join(
                f"    {line}" if line else line for line in dumped.splitlines()
            )
            handle.write(",\n")
            handle.write(indented)
            appended += 1
            seen.add(nid)
        handle.write(text[index:])
    tmp.replace(path)
    return dropped, appended


def consolidate(
    *,
    krr_dir: Path = KRR_DIR,
    patch_combined: bool = True,
    remap_peeps: bool = True,
) -> dict[str, Any]:
    vocab_doc = load_jsonld(krr_dir / "controlled_vocabulary.jsonld")
    old_ids = {
        node["@id"]
        for node in graph_nodes(vocab_doc)
        if isinstance(node.get("@id"), str)
    }
    graph, unresolved = build_consolidated_graph(vocab_doc)
    new_vocab = {
        "@context": vocab_doc.get("@context", {}),
        "@graph": graph,
    }
    if "dcterms" not in new_vocab["@context"]:
        new_vocab["@context"]["dcterms"] = "http://purl.org/dc/terms/"

    dump_jsonld(krr_dir / "controlled_vocabulary.jsonld", new_vocab)
    write_unresolved(unresolved, krr_dir / "unresolved_references.jsonld")

    metadata = strip_metadata_tbox(load_jsonld(METADATA_PATH))
    dump_jsonld(METADATA_PATH, metadata)

    remapped: list[Path] = []
    if remap_peeps:
        remapped = remap_junk_files(krr_dir)

    new_ids = {node["@id"] for node in graph if isinstance(node.get("@id"), str)}
    extras = [
        node
        for node in graph
        if isinstance(node.get("@id"), str) and node["@id"] not in old_ids
    ]
    dropped_combined = appended_combined = 0
    combined_path = krr_dir / "combined_ontology.jsonld"
    if patch_combined and combined_path.is_file():
        first = combined_path.read_text(encoding="utf-8", errors="replace")[:80]
        if not first.startswith("version https://git-lfs.github.com/spec/v1"):
            dropped_combined, appended_combined = patch_combined_graph(
                combined_path,
                drop_ids=set(JUNK_TERMS),
                extra_nodes=extras,
            )

    both, total, missing = property_coverage(graph)
    return {
        "classes": sum(1 for node in graph if is_class_node(node)),
        "properties": total,
        "properties_with_both": both,
        "coverage": (both / total) if total else 0.0,
        "missing_both": missing,
        "placeholders": placeholder_ids(graph),
        "unresolved": len(unresolved),
        "junk_remaining": sorted(JUNK_TERMS & new_ids),
        "remapped_files": [str(path) for path in remapped],
        "combined_dropped": dropped_combined,
        "combined_appended": appended_combined,
        "new_tbox_ids": [node["@id"] for node in extras],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the graph in memory and print coverage; do not write",
    )
    parser.add_argument(
        "--skip-combined",
        action="store_true",
        help="Do not patch combined_ontology.jsonld",
    )
    parser.add_argument(
        "--skip-remap",
        action="store_true",
        help="Do not rewrite junk predicates on peeps",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        vocab_doc = load_jsonld(VOCAB_PATH)
        graph, unresolved = build_consolidated_graph(vocab_doc)
        both, total, missing = property_coverage(graph)
        print(
            f"dry-run: {len(graph)} nodes, {total} properties, "
            f"{both}/{total} with domain+range "
            f"({(both / total if total else 0):.1%}), "
            f"{len(unresolved)} unresolved individuals, "
            f"{len(placeholder_ids(graph))} placeholders, "
            f"{len(missing)} missing both"
        )
        for nid in missing[:20]:
            print(f"  missing both: {nid}")
        return 0
    stats = consolidate(
        patch_combined=not args.skip_combined,
        remap_peeps=not args.skip_remap,
    )
    print(
        f"consolidated T-Box: {stats['classes']} classes, "
        f"{stats['properties_with_both']}/{stats['properties']} properties "
        f"with domain+range ({stats['coverage']:.1%}), "
        f"{stats['unresolved']} unresolved individuals moved, "
        f"{len(stats['remapped_files'])} peeps remapped, "
        f"combined dropped={stats['combined_dropped']} "
        f"appended={stats['combined_appended']}"
    )
    if stats["placeholders"]:
        print(f"  leftover placeholders: {stats['placeholders']}")
        return 1
    if stats["junk_remaining"]:
        print(f"  leftover junk: {stats['junk_remaining']}")
        return 1
    if stats["coverage"] < 0.95:
        print(f"  coverage below 95%: {stats['missing_both']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
