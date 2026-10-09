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
ONTOLOGY_VERSION = "1.0.0"

JUNK_TERMS = frozenset(
    {
        "estleg:jsonld",
        "estleg:counts",
        "estleg:note",
        "estleg:scope",
        "estleg:title",
    }
)

# #438 / #709: estleg:Section is the paragrahv (§) class -- every one of its
# 601 instances (the two OWL modules and the TsÜS / VÕS osa peeps) is a § node
# that also carries estleg:LegalProvision explicitly. Section ⊑ LegalProvision
# is therefore true, mirrors Subsection ⊑ LegalProvision, and entails no type a
# node lacks. LegalPart stays distinct from multipart-file Part (#566).
CLASS_ALIGNMENT_AXIOMS: dict[str, dict[str, Any]] = {
    "estleg:Section": {"rdfs:subClassOf": {"@id": "estleg:LegalProvision"}},
}

# Superclasses added to a class on top of whatever it already declares (#709).
# Additive and idempotent: an existing subClassOf value is kept.
EXTRA_SUPERCLASSES: dict[str, tuple[str, ...]] = {
    # SKOS-typed controlled-value families (mirrors TargetGroup / TemporalStatus).
    "estleg:NormativeType": ("skos:Concept",),
    "estleg:CaseType": ("skos:Concept",),
    "estleg:DecisionType": ("skos:Concept",),
    "estleg:DraftType": ("skos:Concept",),
    "estleg:ReferenceType": ("skos:Concept",),
    "estleg:EUDocumentType": ("skos:Concept",),
    "estleg:EUCourtDecisionType": ("skos:Concept",),
    "estleg:LegislativePhase": ("skos:Concept",),
    "estleg:InstitutionType": ("skos:Concept",),
    # Concept layers whose A-Box nodes are already dual-typed skos:Concept.
    "estleg:Concept": ("skos:Concept",),
    "estleg:LegalConcept": ("skos:Concept",),
    "estleg:TopicCluster": ("skos:Concept",),
    "estleg:GeneralPartConcept": ("skos:Concept",),
    # W3C Organization Ontology. No CPOV cpov:PublicOrganisation: the class also
    # holds minister offices (#457), which are posts, not organisations.
    "estleg:Institution": ("org:Organization",),
}

# Superproperties added on top of whatever a property already declares.
# #708: the containment edges the combined build copies onto eli:is_part_of
# (fix_all_issues._ELI_PROPERTY_ALIGNMENTS); the T-Box states the entailment.
# Extra axioms stamped verbatim on a CV node (#718).
EXTRA_AXIOMS: dict[str, dict[str, Any]] = {
    "estleg:predecessorInstitution": {"owl:inverseOf": {"@id": "estleg:successorInstitution"}},
    "estleg:successorInstitution": {"owl:inverseOf": {"@id": "estleg:predecessorInstitution"}},
}

EXTRA_SUPERPROPERTIES: dict[str, tuple[str, ...]] = {
    # #707: the act text was parsed from this RT XML manifestation.
    "estleg:sourceXml": ("prov:wasDerivedFrom",),
    "estleg:partOfAct": ("eli:is_part_of",),
    "estleg:isPartOf": ("eli:is_part_of",),
    "estleg:parentProvision": ("eli:is_part_of",),
}


# Prefixes the CV uses in axioms but did not declare (#709): without them
# dcat:Distribution expanded as a relative IRI.
REQUIRED_PREFIXES: dict[str, str] = {
    "dcat": "http://www.w3.org/ns/dcat#",
    "org": "http://www.w3.org/ns/org#",
    "eli": "http://data.europa.eu/eli/ontology#",
    "schema": "https://schema.org/",
}


# rdf: is the one namespace the CV must not declare: the #392 gate keeps the
# "rdf" context line out of every shipped JSON-LD file. Its few CV uses
# (rdf:Statement in the referenceType domain) are written as full IRIs, which
# is what the undeclared CURIE failed to expand to (#709).
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


def expand_rdf_curies(value: Any) -> Any:
    """Rewrite every ``{"@id": "rdf:X"}`` inside ``value`` to the full IRI."""
    if isinstance(value, list):
        return [expand_rdf_curies(item) for item in value]
    if isinstance(value, dict):
        out = {key: expand_rdf_curies(item) for key, item in value.items()}
        ref = out.get("@id")
        if isinstance(ref, str) and ref.startswith("rdf:"):
            out["@id"] = RDF_NS + ref[len("rdf:"):]
        return out
    return value


def _add_super(node: dict, key: str, iri: str) -> None:
    current = as_list(node.get(key))
    if any(isinstance(item, dict) and item.get("@id") == iri for item in current):
        return
    current.append({"@id": iri})
    node[key] = current[0] if len(current) == 1 else current


def apply_class_alignment(node: dict) -> None:
    """Stamp Section⊑LegalProvision and the #708/#709 super-axioms on a CV node."""
    nid = node.get("@id")
    if not isinstance(nid, str):
        return
    extra = CLASS_ALIGNMENT_AXIOMS.get(nid)
    if extra:
        node.update(extra)
    node.update(copy.deepcopy(EXTRA_AXIOMS.get(nid, {})))
    for iri in EXTRA_SUPERCLASSES.get(nid, ()):
        _add_super(node, "rdfs:subClassOf", iri)
    for iri in EXTRA_SUPERPROPERTIES.get(nid, ()):
        _add_super(node, "rdfs:subPropertyOf", iri)


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
        "estleg:transposesDirectiveAsserted",
        "estleg:transposedBy",
        "estleg:initiatedBy",
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
    "dcterms:isReplacedBy",
    "rdfs:label",
    "skos:prefLabel",
    "rdfs:comment",
    "dc:description",
    "rdfs:domain",
    "schema:domainIncludes",
    "rdfs:range",
    "schema:rangeIncludes",
    "owl:inverseOf",
    "owl:FunctionalProperty",
)

# Keys the corpus convention keeps as JSON arrays even when they hold one value.
# Mirrors ``validate_all.validate_types`` (@type) and the SKOS mapping links in
# ``validate_all.MULTI_VALUED_PROPS``; a scalar copied from metadata.jsonld or a
# subcorpus schema used to reach the CV unchanged and fail both checks.
ARRAY_VALUED_KEYS = (
    "@type",
    "skos:exactMatch",
    "skos:closeMatch",
    "skos:broadMatch",
    "skos:narrowMatch",
    "skos:relatedMatch",
)

# Terms the corpus uses that no merge source declares. Seeded only when absent,
# so the CV copy (with its backfilled comment and axioms) wins on every rerun.
# Value: (types, Estonian label, English label).
DECLARED_TERMS: dict[str, tuple[tuple[str, ...], str, str]] = {
    # #692 / #707: act-level Riigi Teataja provenance on law roots.
    "estleg:skeemiNimi": (("owl:DatatypeProperty",), "skeemi nimi", "RT XML schema name"),
    "estleg:sourceXml": (("owl:ObjectProperty",), "lähte-XML", "source XML"),
    "estleg:citationSource": (("owl:ObjectProperty",), "viitav säte", "Citation Source"),
    "estleg:itemNumber": (("owl:DatatypeProperty",), "punkti number", "Item Number"),
    "estleg:provisionRef": (("owl:DatatypeProperty",), "sätteviide", "Provision Reference"),
    "estleg:resultedInVersion": (
        ("owl:ObjectProperty",),
        "tulemuseks redaktsioon",
        "resulted in version",
    ),
    "estleg:rtUrl": (("owl:DatatypeProperty",), "Riigi Teataja URL", "Riigi Teataja URL"),
    # #722: how a regulation body was parsed (structured XML, HTML fallback, none).
    "estleg:parseMode": (("owl:DatatypeProperty",), "parsimisviis", "parse mode"),
    # #701: corpus-coverage flags replacing hasNoTransposition /
    # hasNoCompetentAuthority, plus the provenance stamped beside them.
    "estleg:noTranspositionEdgeInCorpus": (
        ("owl:DatatypeProperty",),
        "korpuses puudub ülevõtuseos",
        "no transposition edge in corpus",
    ),
    "estleg:competentAuthorityNotExtracted": (
        ("owl:DatatypeProperty",),
        "pädevat asutust pole eraldatud",
        "competent authority not extracted",
    ),
    # #711: the act's own RT normitehniline märkus, beside the CELLAR-notified
    # estleg:transposesDirective (extract_ntm_directives.py).
    "estleg:transposesDirectiveAsserted": (
        ("owl:ObjectProperty",),
        "võtab üle direktiivi (normitehnilise märkuse järgi)",
        "transposes directive (asserted in the act)",
    ),
    "estleg:coverageFlagMethod": (
        ("owl:DatatypeProperty",),
        "kattelipu meetod",
        "coverage flag method",
    ),
    "estleg:coverageFlagAsOf": (
        ("owl:DatatypeProperty",),
        "kattelipu seisuga kuupäev",
        "coverage flag as-of date",
    ),
    # #718 institution identity, lineage and mentions.
    "estleg:registrikood": (("owl:DatatypeProperty",), "registrikood", "registry code"),
    "estleg:xteeMemberCode": (("owl:DatatypeProperty",), "X-tee liikmekood", "X-tee member code"),
    "estleg:validFrom": (("owl:DatatypeProperty",), "kehtib alates", "valid from"),
    "estleg:validTo": (("owl:DatatypeProperty",), "kehtib kuni", "valid to"),
    "estleg:predecessorInstitution": (("owl:ObjectProperty",), "eelkäija asutus", "predecessor institution"),
    "estleg:successorInstitution": (("owl:ObjectProperty",), "järglasasutus", "successor institution"),
    "estleg:mentionsInstitution": (("owl:ObjectProperty",), "mainib asutust", "mentions institution"),
    # #719 annotation provenance and #699 EuroVoc subject provenance.
    "estleg:isExcerpt": (("owl:DatatypeProperty",), "katkend", "is excerpt"),
    "estleg:sourceTextLength": (("owl:DatatypeProperty",), "allikateksti pikkus", "source text length"),
    "estleg:editorialNote": (("owl:DatatypeProperty",), "toimetuse märkus", "editorial note"),
    "estleg:editorialSource": (("owl:DatatypeProperty",), "toimetuse märkuse autor", "editorial source"),
    "estleg:subjectSource": (("owl:DatatypeProperty",), "teema allikas", "subject source"),
    # #717: draft lifecycle (generate_draft_legislation.lifecycle_schema_nodes).
    "estleg:hasProcessStep": (("owl:ObjectProperty",), "menetlussamm", "has process step"),
    "estleg:processStepOf": (("owl:ObjectProperty",), "menetlussammu eelnõu", "process step of"),
    "estleg:processStage": (("owl:ObjectProperty",), "menetlusetapp", "process stage"),
    "estleg:stepOrder": (("owl:DatatypeProperty",), "sammu järjekord", "step order"),
    "estleg:riigikoguStatus": (("owl:DatatypeProperty",), "Riigikogu menetlusolek", "Riigikogu status"),
    "estleg:initiatedBy": (("owl:ObjectProperty",), "algataja", "initiated by"),
    "estleg:lifecycleStale": (("owl:DatatypeProperty",), "menetluselu aegunud", "lifecycle stale"),
    "estleg:riigikoguMark": (("owl:DatatypeProperty",), "Riigikogu eelnõu tähis", "Riigikogu mark"),
    "estleg:riigikoguUuid": (("owl:DatatypeProperty",), "Riigikogu UUID", "Riigikogu UUID"),
    "estleg:riigikoguMembership": (("owl:DatatypeProperty",), "Riigikogu koosseis", "Riigikogu membership"),
    "estleg:derivationMethod": (("owl:DatatypeProperty",), "tuletusmeetod", "derivation method"),
    # #712: KOV layer-1 enrichment (enrich_kov_layer1.py).
    "estleg:enactedByHistoricalMunicipality": (
        ("owl:ObjectProperty",),
        "kehtestanud endine omavalitsus",
        "enacted by historical municipality",
    ),
    "estleg:historicalMunicipality": (
        ("owl:ObjectProperty",),
        "endine omavalitsus",
        "historical municipality",
    ),
    "estleg:countyCode": (
        ("owl:DatatypeProperty",),
        "maakonna EHAK-kood",
        "county EHAK code",
    ),
    # #712: KOV enabling-provision staleness (derive_kov_enabling_staleness.py).
    "estleg:enablingProvisionOutdated": (
        ("owl:DatatypeProperty",),
        "volitusnorm muutunud",
        "enabling provision outdated",
    ),
    # #713: inferred kind of an effected amendment (generate_amendment_history.py).
    "estleg:amendmentKind": (
        ("owl:DatatypeProperty",),
        "muudatuse liik (muutmismärge)",
        "amendment kind",
    ),
    # #549 / #713: estleg:ReleaseDelta record terms (emit_release_changes.py).
    "estleg:comparedFrom": (("owl:DatatypeProperty",), "võrreldud alates", "compared from"),
    "estleg:comparedTo": (("owl:DatatypeProperty",), "võrreldud kuni", "compared to"),
    "estleg:listedIriCap": (
        ("owl:DatatypeProperty",),
        "loetletud IRI-de ülempiir",
        "listed IRI cap",
    ),
    "estleg:changed": (("owl:DatatypeProperty",), "muudetud", "changed"),
    "estleg:changedCount": (("owl:DatatypeProperty",), "muudetute arv", "changed count"),
    "estleg:addedLaw": (("owl:DatatypeProperty",), "lisatud seadus", "added law"),
    "estleg:removedLaw": (("owl:DatatypeProperty",), "eemaldatud seadus", "removed law"),
    "estleg:deprecatedLaw": (("owl:DatatypeProperty",), "aegunud seadus", "deprecated law"),
    "estleg:addedLawCount": (
        ("owl:DatatypeProperty",),
        "lisatud seaduste arv",
        "added law count",
    ),
    "estleg:removedLawCount": (
        ("owl:DatatypeProperty",),
        "eemaldatud seaduste arv",
        "removed law count",
    ),
    "estleg:deprecatedLawCount": (
        ("owl:DatatypeProperty",),
        "aegunud seaduste arv",
        "deprecated law count",
    ),
    "estleg:listedInline": (
        ("owl:DatatypeProperty",),
        "loetletud kirjes",
        "listed inline",
    ),
}

# Terms kept declared so old queries still parse, but marked owl:deprecated
# with dcterms:isReplacedBy. Value: (replacement, comment that overwrites).
DEPRECATED_TERMS: dict[str, tuple[str, str]] = {
    "estleg:targetGroupConcept": (
        "estleg:targetGroup",
        "Deprecated (issue #709): a partial duplicate of estleg:targetGroup, "
        "materialised by #609 when targetGroup still carried enum strings. "
        "Since #460 estleg:targetGroup itself holds the estleg:TargetGroup_* "
        "IRIs and is an owl:ObjectProperty with range estleg:TargetGroup; query "
        "that instead. Retained so old queries parse; no longer emitted, and "
        "scripts/retire_target_group_concept.py removes existing edges.",
    ),
    "estleg:hasNoTransposition": (
        "estleg:noTranspositionEdgeInCorpus",
        "Deprecated (issue #701): the name read as a legal finding but the flag "
        "only recorded a missing transposition edge in this corpus. Use "
        "estleg:noTranspositionEdgeInCorpus. Retained so old queries parse; "
        "no longer emitted.",
    ),
    "estleg:hasNoCompetentAuthority": (
        "estleg:competentAuthorityNotExtracted",
        "Deprecated (issue #701): the name read as a legal finding but the flag "
        "only recorded that no competent-authority edge was extracted. Use "
        "estleg:competentAuthorityNotExtracted. Retained so old queries parse; "
        "no longer emitted.",
    ),
}

# One skos:ConceptScheme per controlled-value family (#709). Every
# owl:NamedIndividual typed with the class gets skos:Concept, skos:inScheme and
# skos:topConceptOf; the scheme lists them as skos:hasTopConcept.
# Value: (scheme @id, Estonian label, English label).
SKOS_SCHEMES: dict[str, tuple[str, str, str]] = {
    "estleg:NormativeType": (
        "estleg:NormativeTypeScheme", "Normatiivsete liikide skeem", "Normative type scheme",
    ),
    "estleg:CaseType": ("estleg:CaseTypeScheme", "Kohtuasja liikide skeem", "Case type scheme"),
    "estleg:DecisionType": (
        "estleg:DecisionTypeScheme", "Kohtulahendi liikide skeem", "Decision type scheme",
    ),
    "estleg:DraftType": ("estleg:DraftTypeScheme", "Eelnõu liikide skeem", "Draft type scheme"),
    "estleg:ReferenceType": (
        "estleg:ReferenceTypeScheme", "Viite liikide skeem", "Reference type scheme",
    ),
    "estleg:EUDocumentType": (
        "estleg:EUDocumentTypeScheme", "EL-i dokumendiliikide skeem", "EU document type scheme",
    ),
    "estleg:EUCourtDecisionType": (
        "estleg:EUCourtDecisionTypeScheme",
        "EL-i kohtulahendi liikide skeem",
        "EU court decision type scheme",
    ),
    "estleg:LegislativePhase": (
        "estleg:LegislativePhaseScheme",
        "Menetlusetappide skeem",
        "Legislative phase scheme",
    ),
    "estleg:InstitutionType": (
        "estleg:InstitutionTypeScheme",
        "Institutsiooni liikide skeem",
        "Institution type scheme",
    ),
}

# The SKOS value set behind the estleg:institutionType string tokens (#709).
# The ABox keeps the xsd:string token (the #522 temporalStatus precedent); each
# individual carries the token as skos:notation. Tokens match the sh:in list of
# InstitutionShape. Value: (individual @id, Estonian label, English label).
INSTITUTION_TYPES: dict[str, tuple[str, str, str]] = {
    "ministry": ("estleg:InstitutionType_Ministry", "ministeerium", "ministry"),
    "minister": ("estleg:InstitutionType_Minister", "minister", "minister"),
    "agency": ("estleg:InstitutionType_Agency", "amet või asutus", "agency"),
    "court": ("estleg:InstitutionType_Court", "kohus", "court"),
    "local_government": (
        "estleg:InstitutionType_LocalGovernment", "kohalik omavalitsus", "local government",
    ),
    "parliament": ("estleg:InstitutionType_Parliament", "parlament", "parliament"),
    "head_of_state": ("estleg:InstitutionType_HeadOfState", "riigipea", "head of state"),
    "government": ("estleg:InstitutionType_Government", "valitsus", "government"),
}
INSTITUTION_TYPE_CLASS = "estleg:InstitutionType"

# Labels the corpus usage contradicts (#709). One class per Estonian structural
# level, with the Riigi Teataja English-translation terms: osa = Part,
# peatükk = Chapter, jagu = Division, jaotis = Subdivision, paragrahv (§) =
# Section, lõige = subsection, punkt = clause.
OVERWRITE_LABEL: dict[str, list[dict[str, str]]] = {
    "estleg:Section": [
        {"@value": "Paragrahv", "@language": "et"},
        {"@value": "Section (§)", "@language": "en"},
    ],
    "estleg:Subdivision": [
        {"@value": "Jaotis", "@language": "et"},
        {"@value": "Subdivision", "@language": "en"},
    ],
    "estleg:LegalPart": [
        {"@value": "Osa (struktuuriüksus)", "@language": "et"},
        {"@value": "Part (structural unit)", "@language": "en"},
    ],
    "estleg:Part": [
        {"@value": "Osa (mitmeosalise akti juur)", "@language": "et"},
        {"@value": "Part (multipart act root)", "@language": "en"},
    ],
}

# The Publications Office corporate-body authority cache (#709).
EU_CORPORATE_BODY_PATH = REPO_ROOT / "data" / "eu_corporate_body_authority.json"


def load_eu_corporate_bodies(path: Path = EU_CORPORATE_BODY_PATH) -> dict[str, str]:
    """EUInstitution @id -> authority IRI, for codes found in the authority table."""
    if not path.is_file():
        return {}
    doc = load_jsonld(path)
    return {
        row["individual"]: row["authorityIri"]
        for row in doc.get("institutions", [])
        if row.get("inAuthorityTable") and row.get("authorityIri")
    }


# Fallback placeholders: individuals an old closure pass materialised in the CV
# so a dangling reference would resolve. Once a real instance file declares the
# same @id, the CV copy is a cross-file duplicate (validate_all) that shadows the
# real node's labels, so it is dropped. Only instance-data overlays count here;
# an id declared by a merge source (a subcorpus *_schema.json) is a shared
# T-Box id and must stay in the CV the schemas are projected from.
FALLBACK_STATUS = "fallbackMaterialized"
INSTANCE_DATA_SUBDIRS = ("institutions",)

# Real comments replacing the "Reusable … materialized" placeholders.
REAL_COMMENTS: dict[str, str] = {
    "estleg:skeemiNimi": (
        "Riigi Teataja <skeemiNimi> of the act XML the node was generated "
        "from, e.g. tyviseadus_1_10.02.2010.xsd: the XSD the source text "
        "follows (#692)."
    ),
    "estleg:sourceXml": (
        "The Riigi Teataja XML manifestation the act root was parsed from "
        "(public API /akt/{globaalID}/xml). dcterms:source names the human "
        "Riigi Teataja page of the same redaction; estleg:contentHash is the "
        "SHA-256 of this XML as fetched (#707)."
    ),
    "estleg:InstitutionType": (
        "Closed SKOS value set behind the estleg:institutionType string tokens "
        "(#709). Members live in estleg:InstitutionTypeScheme and carry the "
        "token as skos:notation."
    ),
    "estleg:Annex": (
        "An annex (lisa) attached to an act. Structural sibling of Chapter / "
        "Division, not a LegalProvision."
    ),
    "estleg:Competence": (
        "An institutional competence assertion: an institution is competent "
        "for a provision or subject area."
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
    "estleg:annexNumber": (
        "Ordinal or official number of an Annex."
    ),
    "estleg:applicableProvision": (
        "Provision that a court decision, sanction, or annotation treats as "
        "applicable. Domain is owl:Thing on purpose: the predicate is shared by "
        "CourtDecision and Sanction nodes, and a CourtDecision domain axiom would "
        "phantom-type every Sanction as a court decision under RDFS inference "
        "(#681; see shacl/README.md)."
    ),
    "estleg:appliesToProvision": (
        "Links a competence, sanction, or annotation to the provision it "
        "applies to."
    ),
    "estleg:belongsToCluster": (
        "Topic-cluster key a concept or provision was assigned to."
    ),
    "estleg:citationSource": (
        "Links a reified estleg:Citation to the provision (a § or a lõige) whose "
        "text contains the citation: the citing side, where citationTarget is "
        "the cited side. Written by extract_cross_references.py; an unresolved "
        "law citation keeps citationSource and citationText and omits "
        "citationTarget (#514)."
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
    "estleg:provisionRef": (
        "Human-readable citation of the provision a ProvisionVersion is a "
        "version of, e.g. \"PKS § 1\" or \"KARIST_2 § 88 lg 1\", derived from "
        "the versionOf IRI. A denormalised display string, not a graph join: "
        "use versionOf. Written by generate_provision_versions.py (#524)."
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
    "estleg:resultedInVersion": (
        "Links an AmendmentEvent to each estleg:ProvisionVersion that took "
        "effect on the event's date (#429). Written by "
        "generate_amendment_history.py. The versions live in the "
        "provision_versions/ sidecars, so the term carries no rdfs:range "
        "(schema:rangeIncludes only) and is stripped from "
        "combined_ontology.jsonld via estleg_common.COMBINED_STRIPPED_PREDICATES "
        "(#681); it resolves on the full load surface."
    ),
    "estleg:rtReference": (
        "Riigi Teataja reference string on an AmendmentEvent."
    ),
    "estleg:rtUrl": (
        "Riigi Teataja URL of the redaction (terviktekst) that produced a "
        "ProvisionVersion, built from its versionRedactionId. Written by "
        "generate_provision_versions.py as a plain string literal; "
        "ProvisionVersionShape also accepts xsd:anyURI (#524)."
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
    "estleg:parseMode": (
        "How the generator obtained a regulation's body from Riigi Teataja: "
        "'structured' (the XML sisu tree), 'html_fallback' (the HTML rendering) "
        "or 'no_paragraphs' (metadata-only XML with no body). Stamped on every regulation "
        "act node (#722)."
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
    "estleg:skeemiNimi": ("estleg:Act", "xsd:string"),
    "estleg:sourceXml": ("estleg:Act", "rdfs:Resource"),
    "estleg:hasExpression": ("estleg:Act", "estleg:ActExpression"),
    # #711: range dropped by FORBIDDEN_NO_RANGE (cross-bucket directive stubs).
    "estleg:transposesDirectiveAsserted": ("estleg:Act", "estleg:EULegislation"),
    # #717 draft lifecycle.
    "estleg:hasProcessStep": ("estleg:DraftLegislation", "estleg:ProcessStep"),
    "estleg:processStepOf": ("estleg:ProcessStep", "estleg:DraftLegislation"),
    "estleg:processStage": ("estleg:ProcessStep", "estleg:LegislativePhase"),
    "estleg:stepOrder": ("estleg:ProcessStep", "xsd:integer"),
    "estleg:riigikoguStatus": ("estleg:ProcessStep", "xsd:string"),
    # Range dropped by FORBIDDEN_NO_RANGE: bare Institution IRIs in the drafts bucket.
    "estleg:initiatedBy": ("estleg:DraftLegislation", "estleg:Institution"),
    "estleg:lifecycleStale": ("estleg:DraftLegislation", "xsd:boolean"),
    "estleg:riigikoguMark": ("estleg:DraftLegislation", "xsd:string"),
    "estleg:riigikoguUuid": ("estleg:DraftLegislation", "xsd:string"),
    "estleg:riigikoguMembership": ("estleg:DraftLegislation", "xsd:integer"),
    # Domain is owl:Thing via OVERWRITE_DOMAIN (schema:domainIncludes hints).
    "estleg:derivationMethod": ("owl:Thing", "xsd:string"),
    "estleg:actNumber": ("estleg:Act", "xsd:string"),
    "estleg:affectedBy": ("estleg:Act", "rdfs:Resource"),
    "estleg:amendedBy": ("estleg:Act", "rdfs:Resource"),
    "estleg:amendingDraft": ("estleg:ProposedAmendment", "rdfs:Resource"),
    "estleg:amendmentDate": ("estleg:AmendmentEvent", "xsd:date"),
    "estleg:amends": ("estleg:AmendmentEvent", "rdfs:Resource"),
    "estleg:annexNumber": ("estleg:Annex", "xsd:string"),
    # Shared by CourtDecision and Sanction: a CourtDecision domain would
    # phantom-type every Sanction under RDFS inference (#681).
    "estleg:applicableProvision": ("owl:Thing", "rdfs:Resource"),
    "estleg:appliesToProvision": ("owl:Thing", "rdfs:Resource"),
    "estleg:appliesToProvisionCount": ("owl:Thing", "xsd:integer"),
    "estleg:assertionConfidence": ("owl:Thing", "xsd:decimal"),
    "estleg:belongsToCluster": ("owl:Thing", "xsd:string"),
    "estleg:caseTypeCode": ("estleg:CaseType", "xsd:string"),
    "estleg:chapterNumber": ("estleg:Chapter", "xsd:string"),
    "estleg:competenceArea": ("owl:Thing", "xsd:string"),
    "estleg:competenceType": ("owl:Thing", "xsd:string"),
    "estleg:competentAuthority": ("owl:Thing", "rdfs:Resource"),
    "estleg:courtKind": ("estleg:CourtDecision", "xsd:string"),
    "estleg:courtLevel": ("estleg:CourtDecision", "xsd:string"),
    "estleg:courtName": ("estleg:CourtDecision", "xsd:string"),
    "estleg:rtObjektId": ("estleg:CourtDecision", "xsd:string"),
    "estleg:competentAuthorityCount": ("estleg:Act", "xsd:integer"),
    "estleg:governs": ("owl:Thing", "rdfs:Resource"),
    "estleg:hasNoCompetentAuthority": ("estleg:Act", "xsd:boolean"),
    "estleg:hasNoTransposition": ("owl:Thing", "xsd:boolean"),
    "estleg:competentAuthorityNotExtracted": ("estleg:Act", "xsd:boolean"),
    # owl:Thing: an estleg:EULegislation domain would type EUR-Lex stubs in the
    # laws SHACL bucket under RDFS inference (same reason as the old flag).
    "estleg:noTranspositionEdgeInCorpus": ("owl:Thing", "xsd:boolean"),
    "estleg:coverageFlagMethod": ("owl:Thing", "xsd:string"),
    "estleg:coverageFlagAsOf": ("owl:Thing", "xsd:date"),
    "estleg:enablingProvisionOutdated": ("owl:Thing", "xsd:boolean"),
    "estleg:enactedByHistoricalMunicipality": ("owl:Thing", "estleg:HistoricalMunicipality"),
    "estleg:historicalMunicipality": ("owl:Thing", "estleg:HistoricalMunicipality"),
    "estleg:countyCode": ("owl:Thing", "xsd:string"),
    "estleg:registrikood": ("owl:Thing", "xsd:string"),
    "estleg:xteeMemberCode": ("owl:Thing", "xsd:string"),
    "estleg:validFrom": ("owl:Thing", "xsd:date"),
    "estleg:validTo": ("owl:Thing", "xsd:date"),
    "estleg:predecessorInstitution": ("owl:Thing", "estleg:Institution"),
    "estleg:successorInstitution": ("owl:Thing", "estleg:Institution"),
    "estleg:mentionsInstitution": ("owl:Thing", "rdfs:Resource"),
    "estleg:isExcerpt": ("owl:Thing", "xsd:boolean"),
    "estleg:sourceTextLength": ("owl:Thing", "xsd:integer"),
    "estleg:editorialNote": ("owl:Thing", "xsd:string"),
    "estleg:editorialSource": ("owl:Thing", "xsd:string"),
    "estleg:subjectSource": ("owl:Thing", "xsd:string"),
    "estleg:inboundCitationCount": ("owl:Thing", "xsd:integer"),
    "estleg:interpretationCount": ("owl:Thing", "xsd:integer"),
    "estleg:similarFrom": ("estleg:Similarity", "rdfs:Resource"),
    "estleg:contentStatus": ("estleg:Act", "xsd:string"),
    "estleg:contentStatusReason": ("estleg:Act", "xsd:string"),
    "estleg:isRatificationShell": ("estleg:Act", "xsd:boolean"),
    "estleg:changeType": ("estleg:DraftLegislation", "xsd:string"),
    "estleg:definedIn": ("estleg:LegalConcept", "rdfs:Resource"),
    "estleg:definesConcept": ("estleg:LegalConcept", "rdfs:Resource"),
    "estleg:definitionCount": ("estleg:Concept", "xsd:integer"),
    "estleg:definitionVariantCount": ("estleg:Concept", "xsd:integer"),
    "estleg:documentType": ("owl:Thing", "xsd:string"),
    "estleg:estoniaRelevant": ("estleg:EULegislation", "xsd:boolean"),
    "estleg:entryIntoForce": ("owl:Thing", "xsd:date"),
    "estleg:euCourtCode": ("estleg:EUCourt", "xsd:string"),
    "estleg:euInstitutionCode": ("estleg:EUInstitution", "xsd:string"),
    "estleg:globalId": ("owl:Thing", "xsd:string"),
    "estleg:grantedBy": ("owl:Thing", "rdfs:Resource"),
    "estleg:hasAnnex": ("estleg:Act", "estleg:Annex"),
    "estleg:hasDefinitionNode": ("estleg:Concept", "rdfs:Resource"),
    "estleg:hasProposedAmendment": ("estleg:Act", "rdfs:Resource"),
    "estleg:hasPart": ("owl:Thing", "rdfs:Resource"),
    "estleg:hasSanction": ("owl:Thing", "rdfs:Resource"),
    "estleg:inChapter": ("owl:Thing", "estleg:Chapter"),
    "estleg:inDivision": ("owl:Thing", "estleg:Division"),
    "estleg:inPart": ("owl:Thing", "estleg:Part"),
    "estleg:institution": ("owl:Thing", "rdfs:Resource"),
    "estleg:institutionType": ("estleg:Institution", "xsd:string"),
    "estleg:interpretedBy": ("owl:Thing", "rdfs:Resource"),
    "estleg:interpretsLaw": ("estleg:CourtDecision", "rdfs:Resource"),
    "estleg:interpretsVersion": ("estleg:CourtDecision", "rdfs:Resource"),
    "estleg:isCurrentAmendment": ("estleg:AmendmentEvent", "xsd:boolean"),
    "estleg:isKov": ("owl:Thing", "xsd:boolean"),
    "estleg:isPartOf": ("owl:Thing", "rdfs:Resource"),
    "estleg:isStatutoryDefault": ("owl:Thing", "xsd:boolean"),
    "estleg:isStubNode": ("owl:Thing", "xsd:boolean"),
    "estleg:issuer": ("estleg:Act", "xsd:string"),
    "estleg:jurisdiction": ("owl:Thing", "xsd:string"),
    "estleg:kehtiv": ("estleg:Act", "xsd:date"),
    "estleg:parseMode": ("estleg:Act", "xsd:string"),
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
    "estleg:officialEnglishText": ("estleg:Act", "rdfs:Resource"),
    "estleg:paragrahv": ("estleg:LegalProvision", "xsd:string"),
    "estleg:phaseOrder": ("estleg:LegislativePhase", "xsd:integer"),
    "estleg:preambleText": ("estleg:Act", "xsd:string"),
    "estleg:proposesToAmend": ("estleg:ProposedAmendment", "rdfs:Resource"),
    "estleg:provisionCount": ("estleg:TopicCluster", "xsd:integer"),
    "estleg:referencedBy": ("owl:Thing", "rdfs:Resource"),
    "estleg:references": ("owl:Thing", "rdfs:Resource"),
    "estleg:relatesToConcept": ("owl:Thing", "xsd:string"),
    "estleg:repealDate": ("estleg:Act", "xsd:date"),
    "estleg:requestedCluster": ("owl:Thing", "rdfs:Resource"),
    "estleg:rtReference": ("estleg:AmendmentEvent", "xsd:string"),
    "estleg:sanctionType": ("estleg:Sanction", "xsd:string"),
    "estleg:schemaVersion": ("owl:Thing", "xsd:string"),
    "estleg:sectionNumber": ("owl:Thing", "xsd:string"),
    "estleg:semanticallySimilarTo": ("owl:Thing", "rdfs:Resource"),
    "estleg:sourceAct": ("owl:Thing", "xsd:string"),
    "estleg:sourceGlobaalID": ("owl:Thing", "xsd:string"),
    "estleg:sourceStructure": ("owl:Thing", "xsd:string"),
    "estleg:sourceUrl": ("owl:Thing", "xsd:anyURI"),
    "estleg:summary": ("owl:Thing", "xsd:string"),
    "estleg:temporalStatus": ("estleg:Act", "xsd:string"),
    "estleg:consistencyChecked": ("owl:Thing", "xsd:boolean"),
    "estleg:targetGroup": ("owl:Thing", "estleg:TargetGroup"),
    "estleg:terviktekstId": ("owl:Thing", "xsd:string"),
    "estleg:totalAmendments": ("owl:Thing", "xsd:integer"),
    "estleg:totalConcepts": ("owl:Thing", "xsd:integer"),
    "estleg:transpositionDeadline": ("estleg:EULegislation", "xsd:date"),
    "estleg:referenceStatus": ("owl:Thing", "xsd:string"),
    "estleg:similarityScore": ("estleg:Similarity", "xsd:decimal"),
    "estleg:similarityStatus": ("estleg:Similarity", "xsd:string"),
    "estleg:similarAct": ("estleg:Act", "estleg:Similarity"),
    "estleg:similarTarget": ("estleg:Similarity", "rdfs:Resource"),
    "estleg:similarityModel": ("estleg:Similarity", "xsd:string"),
    "estleg:regulationTypeBucket": ("estleg:MunicipalRegulation", "xsd:string"),
    "estleg:versionOf": ("estleg:ProvisionVersion", "rdfs:Resource"),
    "estleg:versionValidFrom": ("estleg:ProvisionVersion", "xsd:date"),
    "estleg:versionValidTo": ("estleg:ProvisionVersion", "xsd:date"),
    "estleg:versionText": ("estleg:ProvisionVersion", "xsd:string"),
    "estleg:versionRedactionId": ("estleg:ProvisionVersion", "xsd:string"),
    "estleg:supersededByVersion": (
        "estleg:ProvisionVersion",
        "estleg:ProvisionVersion",
    ),
    "estleg:hasVersion": ("owl:Thing", ""),
    "estleg:currentVersion": ("owl:Thing", "estleg:ProvisionVersion"),
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
    "estleg:municipalityType": ("estleg:HistoricalMunicipality", "xsd:string"),
    "estleg:publicationYear": ("owl:Thing", "xsd:gYear"),
    "estleg:enactedAs": ("estleg:DraftLegislation", "rdfs:Resource"),
    "estleg:municipalityStatus": ("owl:Thing", "xsd:string"),
    "estleg:repeals": ("owl:Thing", "rdfs:Resource"),
    "estleg:isLegalBasisFor": ("owl:Thing", "rdfs:Resource"),
    "estleg:exceptionTo": ("owl:Thing", "rdfs:Resource"),
    "estleg:derogatesFrom": ("owl:Thing", "rdfs:Resource"),
    "estleg:enactedBy": ("owl:Thing", "estleg:Issuer"),
    "estleg:enactedByMunicipality": ("owl:Thing", "estleg:Municipality"),
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
    "estleg:citationSource": ("owl:Thing", "rdfs:Resource"),
    "estleg:itemNumber": ("owl:Thing", "xsd:string"),
    "estleg:provisionRef": ("owl:Thing", "xsd:string"),
    "estleg:resultedInVersion": ("owl:Thing", "rdfs:Resource"),
    # ProvisionVersionShape owns rtUrl, targets exactly ProvisionVersion, and
    # every one of its subjects is a ProvisionVersion: the domain types nothing new.
    "estleg:rtUrl": ("estleg:ProvisionVersion", "xsd:string"),
    "estleg:issuedUnder": ("estleg:Act", "rdfs:Resource"),
    "estleg:implementsCitation": ("estleg:Act", "estleg:Citation"),
    "estleg:implementedBy": ("owl:Thing", "rdfs:Resource"),
    "estleg:implementedByCount": ("owl:Thing", "xsd:integer"),
    "estleg:enforcedAtLevel": ("owl:Thing", "xsd:string"),
    "estleg:added": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:removed": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:addedCount": ("estleg:ReleaseDelta", "xsd:integer"),
    "estleg:removedCount": ("estleg:ReleaseDelta", "xsd:integer"),
    # #549 / #713 release-delta record terms.
    "estleg:comparedFrom": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:comparedTo": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:listedIriCap": ("estleg:ReleaseDelta", "xsd:integer"),
    "estleg:changed": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:changedCount": ("estleg:ReleaseDelta", "xsd:integer"),
    "estleg:addedLaw": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:removedLaw": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:deprecatedLaw": ("estleg:ReleaseDelta", "xsd:string"),
    "estleg:addedLawCount": ("estleg:ReleaseDelta", "xsd:integer"),
    "estleg:removedLawCount": ("estleg:ReleaseDelta", "xsd:integer"),
    "estleg:deprecatedLawCount": ("estleg:ReleaseDelta", "xsd:integer"),
    "estleg:listedInline": ("estleg:ReleaseDelta", "xsd:boolean"),
    "estleg:amendmentKind": ("estleg:AmendmentEvent", "xsd:string"),
    "estleg:containsPersonalData": ("owl:Thing", "xsd:boolean"),
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
    "estleg:judge": ("estleg:CourtDecision", "xsd:string"),
    "estleg:referencedLaw": ("estleg:CourtDecision", "xsd:string"),
    "estleg:euDocumentType": (
        "estleg:EULegislation",
        "estleg:EUDocumentType",
    ),
    "estleg:euInstitution": ("estleg:EULegislation", "estleg:EUInstitution"),
    # Domain is owl:Thing on purpose: see OVERWRITE_DOMAIN (#702).
    "estleg:celexNumber": ("owl:Thing", "xsd:string"),
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
    # #711: the three-valued status is stamped on the directive, not the act
    # (the act-level "unknown" placeholder is no longer emitted).
    "estleg:transpositionStatus": "estleg:EULegislation",
    # #717: shared by ProcessStep, CourtDecision and EUCourtDecision.
    "estleg:derivationMethod": "owl:Thing",
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
    # Shared by EULegislation and EUCourtDecision. An EULegislation domain
    # phantom-types all 22,290 EU court decisions as legislation under RDFS
    # inference, after which the EULegislation shape demands euDocumentType
    # they were never meant to carry (#702; review finding E3).
    "estleg:celexNumber": "owl:Thing",
    "estleg:eurLexLink": "owl:Thing",
    "estleg:documentDate": "owl:Thing",
    # Shared by CourtDecision and EUCourtDecision. A CourtDecision domain
    # phantom-types EU court decisions as Estonian ones, which then fail
    # caseType / caseNumber -- they carry euCaseNumber instead (#702).
    "estleg:ecliIdentifier": "owl:Thing",
    # #709: domains the #433 backfill guessed. The shipped data contradicts each
    # one -- and so does the SHACL shape that owns the property, wherever a
    # shape does -- so under RDFS inference every real subject was typed as a
    # sibling shaped class and failed that shape. One true subject class ->
    # name it.
    # 9,482 drafts typed ProposedAmendment: the whole `drafts` bucket failure.
    "estleg:changeType": "estleg:DraftLegislation",
    "estleg:transpositionDeadline": "estleg:EULegislation",
    "estleg:provisionCount": "estleg:TopicCluster",
    "estleg:amendingDraft": "estleg:ProposedAmendment",
    # The definition layer runs LegalConcept -definesConcept-> Concept and
    # back via hasDefinitionNode; 9,877 definition nodes were typed
    # LegalProvision and 2,955 umbrella concepts typed Act.
    "estleg:definesConcept": "estleg:LegalConcept",
    "estleg:hasDefinitionNode": "estleg:Concept",
    "estleg:definitionCount": "estleg:Concept",
    "estleg:definitionVariantCount": "estleg:Concept",
    # Several subject classes -> owl:Thing, with the classes in DOMAIN_INCLUDES.
    # KOV provisions repeat their act's issuer: 116,708 provisions typed Act.
    "estleg:enactedBy": "owl:Thing",
    "estleg:enactedByMunicipality": "owl:Thing",
    "estleg:implementedBy": "owl:Thing",
    "estleg:implementedByCount": "owl:Thing",
    "estleg:semanticallySimilarTo": "owl:Thing",
    # Neither is ever written by a Municipality: municipalityStatus sits on
    # municipal acts and issuers, municipalityType on historical
    # municipalities. 11,566 nodes were typed Municipality and then failed
    # ehakCode / county -- nearly all of the `kov` bucket failure.
    "estleg:municipalityStatus": "owl:Thing",
    "estleg:municipalityType": "estleg:HistoricalMunicipality",
    # True of LegalProvision, but provision_versions/ sidecars carry it on a
    # bare provision stub, as they do hasVersion above.
    "estleg:currentVersion": "owl:Thing",
    # Carried by dataset / amendment-chain header nodes, not acts.
    "estleg:totalAmendments": "owl:Thing",
    "estleg:totalConcepts": "owl:Thing",
    # Terms first declared for the vocabulary-coverage gate. No shape carries
    # sh:path for any of them, so a named domain could only add types; the
    # measured subject class goes in DOMAIN_INCLUDES instead.
    "estleg:citationSource": "owl:Thing",
    "estleg:itemNumber": "owl:Thing",
    "estleg:provisionRef": "owl:Thing",
    "estleg:resultedInVersion": "owl:Thing",
    # Shared by the court-interpretation and KOV enabling-provision staleness
    # derivers: a CourtDecision domain types every flagged municipal act as a
    # court decision under RDFS inference (#709).
    "estleg:earliestSupersedingDate": "owl:Thing",
    "estleg:enablingProvisionOutdated": "owl:Thing",
    # #712: KOV layer-1 terms; the measured subject class is in DOMAIN_INCLUDES.
    "estleg:enactedByHistoricalMunicipality": "owl:Thing",
    "estleg:historicalMunicipality": "owl:Thing",
    "estleg:countyCode": "owl:Thing",
    # #719 / #699: one measured subject class each, in DOMAIN_INCLUDES.
    "estleg:registrikood": "owl:Thing",
    "estleg:xteeMemberCode": "owl:Thing",
    "estleg:validFrom": "owl:Thing",
    "estleg:validTo": "owl:Thing",
    "estleg:predecessorInstitution": "owl:Thing",
    "estleg:successorInstitution": "owl:Thing",
    "estleg:mentionsInstitution": "owl:Thing",
    # #720: also stamped on the in-band Dataset heads of the combined
    # aggregates; a dcat:Distribution domain would type them as distributions.
    "estleg:containsPersonalData": "owl:Thing",
    "estleg:isExcerpt": "owl:Thing",
    "estleg:sourceTextLength": "owl:Thing",
    "estleg:editorialNote": "owl:Thing",
    "estleg:editorialSource": "owl:Thing",
    "estleg:subjectSource": "owl:Thing",
}
OVERWRITE_RANGE: dict[str, str] = {
    # #718: Institution_* or KOV Issuer_* objects; neither may be entailed.
    "estleg:mentionsInstitution": "rdfs:Resource",
    # #709: every one of the 120,421 law-peep objects is a TargetGroup_* IRI.
    "estleg:targetGroup": "estleg:TargetGroup",
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
    # #709: edges whose objects are declared on another load surface. A shaped
    # range types the bare reference in the bucket that holds only the edge,
    # and the stub then fails a shape its real declaration satisfies.
    # riigikohus -> provision_versions/: all 30,426 `riigikohus` violations.
    # The term's own comment has promised "no rdfs:range ProvisionVersion"
    # since #618; the #433 backfill put it back.
    "estleg:interpretsVersion": "rdfs:Resource",
    # provision_versions/ -> law peeps: 90,104 stubs typed LegalProvision.
    "estleg:versionOf": "rdfs:Resource",
    "estleg:partOfAct": "rdfs:Resource",
    "estleg:proposesToAmend": "rdfs:Resource",
    "estleg:hasProposedAmendment": "rdfs:Resource",
    # DOMAIN_RANGE has always said rdfs:Resource for these two; the narrower
    # range on the merged CV copy won because a backfill never overwrites.
    "estleg:citationTarget": "rdfs:Resource",
    "estleg:similarTarget": "rdfs:Resource",
    "estleg:definesConcept": "rdfs:Resource",
    # Already open, by backfill; pinned so RANGE_INCLUDES may describe them.
    "estleg:enactedAs": "rdfs:Resource",
    "estleg:interpretsLaw": "rdfs:Resource",
    # Objects are a § or a lõige; neither class may be entailed from the edge.
    "estleg:citationSource": "rdfs:Resource",
    # amendments/ -> provision_versions/: the interpretsVersion pattern again.
    "estleg:resultedInVersion": "rdfs:Resource",
    # #711: three string tokens (sh:in in the EULegislation shape).
    "estleg:transpositionStatus": "xsd:string",
}

# Comments the corpus contradicts. REAL_COMMENTS only replaces a placeholder,
# so like DOMAIN_RANGE it never reaches a term whose comment is already there.
OVERWRITE_COMMENT: dict[str, str | list[dict[str, str]]] = {
    # #722: the stamped values are structured / html_fallback / no_paragraphs.
    "estleg:parseMode": (
        "How the generator obtained a regulation's body from Riigi Teataja: "
        "'structured' (the XML sisu tree), 'html_fallback' (the HTML rendering) "
        "or 'no_paragraphs' (metadata-only XML with no body). Stamped on every "
        "regulation act node (#722)."
    ),
    # #707: the English consolidation is also a first-class expression node.
    "estleg:officialEnglishText": (
        "IRI of the official English Riigi Teataja consolidation "
        "(https://www.riigiteataja.ee/en/eli/{tolkeSeosId}). rdfs:subPropertyOf "
        "rdfs:seeAlso (#510). The same IRI is an eli:LegalExpression node with "
        "eli:language English that eli:realizes the act; the act root links it "
        "with eli:is_realized_by (#707)."
    ),
    # #713: provision-level amendment history and the provision-level release
    # delta (generate_amendment_history.py, emit_release_changes.py).
    "estleg:amends": (
        "Links an AmendmentEvent to the act it amends (act root(s) first) and, "
        "since #713, to each estleg:LegalProvision / estleg:Subsection the "
        "amending act touched (from the muutmismarge parent nesting). Inverse "
        "of estleg:amendedBy."
    ),
    "estleg:publicationDate": (
        "Publication date: of a draft in EIS, or, on an AmendmentEvent, of the "
        "amending act in Riigi Teataja (avaldamineKuupaev, #713)."
    ),
    "estleg:amendmentKind": (
        "Kind of an effected amendment, inferred from Riigi Teataja "
        "muutmismarge text only where a token is present (Kehtetu / "
        "täiendatud / muudetud / sõnastatud): repeals, supplements or amends. "
        "Absent when no token is present. Values mirror estleg:changeType "
        "(drafts). Issue #713."
    ),
    "estleg:ReleaseDelta": (
        "Machine-readable inter-release delta: provision-level "
        "added/removed/changed IRIs plus law-level changes (#549, #713)."
    ),
    "estleg:added": (
        "IRI added between compared snapshots: a provision IRI (#713) or, in "
        "the legacy #549 record, a law IRI. See estleg:addedCount."
    ),
    "estleg:removed": (
        "IRI removed between compared snapshots: a provision IRI (#713) or, in "
        "the legacy #549 record, a law IRI. See estleg:removedCount."
    ),
    "estleg:comparedFrom": (
        "Label of the older snapshot a ReleaseDelta compares, e.g. "
        "'git v1.0.0 (f018cf05f2)' (#549, #713)."
    ),
    "estleg:comparedTo": (
        "Label of the newer snapshot a ReleaseDelta compares (#549, #713)."
    ),
    "estleg:listedIriCap": (
        "Legacy #549 cap on the number of IRIs listed in a ReleaseDelta; the "
        "counts stay complete. The #713 record is uncapped."
    ),
    "estleg:changed": (
        "Provision IRI whose legalText, summary or temporal fields changed "
        "between the compared snapshots (#713). See estleg:changedCount."
    ),
    "estleg:changedCount": (
        "Full count of estleg:changed provision IRIs in a ReleaseDelta (#713)."
    ),
    "estleg:addedLaw": (
        "Act-root IRI of a law added to INDEX between the compared snapshots (#713)."
    ),
    "estleg:removedLaw": (
        "Act-root IRI of a law removed from INDEX between the compared snapshots (#713)."
    ),
    "estleg:deprecatedLaw": (
        "Act-root IRI of a law newly listed as deprecated in INDEX between the "
        "compared snapshots (#713)."
    ),
    "estleg:addedLawCount": (
        "Count of estleg:addedLaw IRIs in a ReleaseDelta (#713)."
    ),
    "estleg:removedLawCount": (
        "Count of estleg:removedLaw IRIs in a ReleaseDelta (#713)."
    ),
    "estleg:deprecatedLawCount": (
        "Count of estleg:deprecatedLaw IRIs in a ReleaseDelta (#713)."
    ),
    "estleg:listedInline": (
        "False when a ReleaseDelta's IRI lists are in the changes-<version>.jsonl "
        "sibling instead of inline (over 10,000 listed IRIs, #713)."
    ),
    # The eelnoud generator's text (#443); the CV copy predated it, so a
    # projection of the eelnoud schema from the CV dropped the ELI-DL note.
    "estleg:DraftLegislation": (
        "Õigusakt, mis ei ole veel jõustunud, kuid on seadusandlikus "
        "menetluses. ELI-DL v3: rdfs:subClassOf eli-dl:DraftLegislationWork (#443)."
    ),
    # #709: one class per Estonian structural level, labels and comments agreeing.
    "estleg:Part": (
        "The root node of one osa (part) of a multipart act that ships as one "
        "file per osa (#566), e.g. the TsÜS / VÕS osa peeps. It repeats the "
        "act's metadata. The osa as a structural unit inside an act's hierarchy "
        "is estleg:LegalPart."
    ),
    "estleg:LegalPart": (
        "An osa (part) as a structural unit of an act's hierarchy, above "
        "estleg:Chapter (peatükk), e.g. \"2. osa – ERIOSA\" of the KarS special "
        "part module. Distinct from estleg:Part, the per-file root of a "
        "multipart act (#566)."
    ),
    "estleg:Chapter": (
        "A peatükk (chapter) grouping provisions inside an act or an osa "
        "(estleg:LegalPart)."
    ),
    "estleg:Division": (
        "A jagu (division) grouping provisions inside a peatükk "
        "(estleg:Chapter). The next level down is jaotis (estleg:Subdivision)."
    ),
    "estleg:Subdivision": (
        "A jaotis (subdivision) grouping provisions inside a jagu "
        "(estleg:Division), e.g. \"1. jaotis – Tervist kahjustavad süüteod\" "
        "in the KarS special part."
    ),
    "estleg:Section": (
        "A paragrahv (§, \"section\" in Riigi Teataja English translations): "
        "the numbered provision level directly above lõige "
        "(estleg:Subsection). Used by the KarS special-part and TsÜS osa 7 OWL "
        "modules and the TsÜS / VÕS osa peeps; every instance also carries "
        "estleg:LegalProvision, which is the class the main law generator "
        "emits for the same level. Not a container: the jagu and jaotis levels "
        "are estleg:Division and estleg:Subdivision (#709)."
    ),
    "estleg:Subsection": (
        "A numbered subsection (lõige) of an estleg:LegalProvision — the level "
        "Estonian legal citations actually reference (\"TsÜS § 14 lg 2\"). One "
        "node per lõige of a paragrahv that has lõige structure; it carries the "
        "lõige number (estleg:subsectionNumber), the lõige's own text "
        "(estleg:legalText, including the inline (N) punkt markers, mirroring "
        "the provision-level legalText), and an estleg:parentProvision link "
        "back to the § node. The parent provision keeps its full concatenated "
        "estleg:legalText for backward compatibility — the subsections sum to "
        "it. Defined for issue #132. The punkt (clause) level below lõige has "
        "no class: the parser keeps sub-points as inline markers plus "
        "estleg:itemNumber on the lõige (#694, #709)."
    ),
    "estleg:targetGroup": (
        "Closed target-group classification for LegalProvision deontic "
        "effects. Values are estleg:TargetGroup individuals (skos:Concepts in "
        "estleg:TargetGroupScheme), written as IRIs since #460; #709 declares "
        "the property an owl:ObjectProperty to match. HEURISTIC: derived by "
        "keyword/dutyHolder matching over the provision text (#576), "
        "preferring the dutyHolder's subject and capping an unanchored "
        "body-text union; treat as an advisory addressee indicator, not a "
        "definitive legal determination. Replaces the deprecated "
        "estleg:targetGroupConcept."
    ),
    "estleg:enablingProvisionOutdated": (
        "Boolean on a municipal regulation root: true when at least one "
        "national enabling provision it was issued under (via "
        "estleg:implementsCitation / estleg:citationTarget) has a redaction "
        "with different text that took effect after the regulation's "
        "estleg:entryIntoForce. Stamped only when a cited provision resolved "
        "to a version in force on that date; see "
        "estleg:earliestSupersedingDate. Written by "
        "derive_kov_enabling_staleness.py (#712)."
    ),
    "estleg:enactedByHistoricalMunicipality": (
        "Links a municipal regulation issued by a body of a municipality "
        "abolished by territorial reform (chiefly the 2017 haldusreform) to the "
        "pre-merger estleg:HistoricalMunicipality. estleg:enactedByMunicipality "
        "keeps pointing at the current successor municipality. Set only on acts "
        "whose estleg:municipalityStatus is 'abolished'. Written by "
        "enrich_kov_layer1.py (#712)."
    ),
    "estleg:historicalMunicipality": (
        "Links a KOV issuing body (volikogu or valitsus) of an abolished "
        "municipality to the pre-merger estleg:HistoricalMunicipality it "
        "belonged to. It is the IRI counterpart of the slug-derived "
        "estleg:historicalMunicipalityName literal. The correctly accented name "
        "is the target's estleg:formerName. Written by enrich_kov_layer1.py "
        "(#712)."
    ),
    "estleg:countyCode": (
        "The 4-character EHAK (Statistics Estonia administrative classifier) "
        "code of the county (maakond) the municipality belongs to, e.g. '0037' "
        "for Harju maakond. estleg:county keeps the label. The codes come from "
        "data/ehak/counties.json and equal the ISO 3166-2:EE county numbers "
        "(2020 recode). Written by enrich_kov_layer1.py (#712)."
    ),
    "estleg:isExcerpt": "True unless annotationText carries the complete source body; a title-only text is an excerpt (#719).",
    "estleg:sourceTextLength": "Character length of the full extracted source body; present only when known (#719).",
    "estleg:editorialNote": "Project-authored paraphrase or note; never source text (#719).",
    "estleg:editorialSource": "Author of editorialNote (this project), never the cited authority (#719).",
    "estleg:subjectSource": "Provenance of dcterms:subject on this node: 'cellar' = official EuroVoc indexing from the Publications Office (cdm:work_is_about_concept_eurovoc), #699; 'riigikogu' = EuroVoc descriptors of a draft from api.riigikogu.ee (CC BY-SA 3.0), #717.",
    "estleg:registrikood": "The Estonian registry code of the legal person; a rename predecessor shares its successor's code (#718).",
    "estleg:xteeMemberCode": "The X-tee member id EE/GOV/<registrikood>, only on current institutions (#718).",
    "estleg:validFrom": "Inclusive start of the node's identity under this name; an open start is omitted (#718).",
    "estleg:validTo": "Inclusive end of the node's identity under this name; an open end is omitted (#718).",
    "estleg:predecessorInstitution": "Links an institution to the institution it replaced (rename, merger or split). Inverse of estleg:successorInstitution (#718).",
    "estleg:successorInstitution": "Links an institution to the institution that replaced it (rename, merger or split). Inverse of estleg:predecessorInstitution (#718).",
    "estleg:mentionsInstitution": "An institution the provision names without being bound as its competentAuthority: consultation partner, addressee, descriptive genitive or predecessor name (#718). Objects are estleg:Institution_* or KOV estleg:Issuer_* nodes, so the range stays open.",
    "estleg:containsPersonalData": (
        "Boolean flag asserting that a resource contains personal data about "
        "identifiable natural persons within the meaning of the GDPR (e.g. "
        "names and case details of parties in court decisions). It applies to "
        "catalogue distributions (dcat:Distribution) and to the in-band dataset "
        "heads of the combined aggregates (dcat:Dataset), so the domain is left "
        "open (#720). When true, republication is governed by data-protection "
        "law, not merely copyright — see docs/DATA_PROTECTION.md. Introduced "
        "for tickets #545/#546."
    ),
    "estleg:appliesToProvisionCount": (
        "Number of provisions a Competence node applies to, recorded alongside "
        "its estleg:appliesToProvision list on the per-institution files. The "
        "list is no longer capped (#718), so the count equals the list length; "
        "it is kept for consumers that read the total without walking the list "
        "(see src/estleg/extract_institutional_competence.py)."
    ),
    # #724: populated since #379, so neither "never populated" nor deprecated.
    "estleg:amendsLaw": (
        "Links a draft bill (estleg:DraftLegislation) to the enacted act it "
        "proposes to amend. The object is always an act root IRI, never a "
        "provision: the law's _Map node, or the lowest _OsaN root of a "
        "multipart act without one (extract_draft_impact.prefer_act_iri, "
        "#379). Populated on the drafts whose affected law resolves to a corpus "
        "act; estleg:affectedLawName keeps the law's name as a literal on every "
        "draft. Provision-level impact is not resolved."
    ),
    "estleg:institutionType": (
        "Coarse kind of institution as an xsd:string token: ministry, "
        "minister, agency, court, local_government, parliament, "
        "head_of_state, or government. The ABox keeps the token (as "
        "estleg:temporalStatus does, #522); each token is the skos:notation of "
        "an estleg:InstitutionType individual in estleg:InstitutionTypeScheme "
        "(e.g. \"ministry\" = estleg:InstitutionType_Ministry), so the value "
        "set is a SKOS concept scheme with Estonian and English labels (#709)."
    ),
    # All 786 subjects are ProposedAmendment nodes pointing at a DraftLegislation.
    "estleg:amendingDraft": (
        "Links a ProposedAmendment to the draft bill that proposes it."
    ),
    # The parser reads alampunktNr (#694), not the legacy punktNr the old
    # REAL_COMMENTS text named; the CV comment was already set, so overwrite.
    "estleg:itemNumber": (
        "Number of an enumerated sub-point (punkt) inside a lõige, as a display "
        "string such as \"3\" or \"1¹\"; repeated when the lõige lists several "
        "sub-points. Written by law_structure.py from the Riigi Teataja "
        "alampunkt elements' alampunktNr, with the ylaIndeks superscript "
        "rendered as a Unicode superscript (#694); the legacy punktNr tag is "
        "still honoured. Falls back to the punkt numbers cited in the lõige "
        "text only when the lõige has no structural sub-points (#514)."
    ),
    # #711: three corpus-status values; the pre-#711 "full, partial, or
    # unknown" wording is retired (only "unknown" was ever emitted, on acts).
    "estleg:transpositionStatus": [
        {
            "@value": (
                "Corpus transposition status of an EU directive (#711), one of "
                "\"transposed\" (the corpus holds a transposing act: a CELLAR "
                "national implementing measure matched to an Estonian law or "
                "state regulation, estleg:transposedBy, or an act whose Riigi "
                "Teataja normitehniline märkus names the directive, "
                "estleg:transposesDirectiveAsserted), \"no_measure_required\" "
                "(no transposing act; Estonia notified CELLAR that no national "
                "measure is necessary) or \"no_evidence_in_corpus\" (neither). "
                "NOT a legal finding: there is deliberately no \"not "
                "transposed\" value, and \"transposed\" records evidence of a "
                "measure, not complete or correct transposition. Stamped on "
                "directives with a transposition deadline or any evidence. "
                "Deprecated usage: the pre-#711 act-level values \"full\", "
                "\"partial\" and \"unknown\" are retired and no longer emitted."
            ),
            "@language": "en",
        },
        {
            "@value": (
                "EL-i direktiivi ülevõtmise staatus selles korpuses (#711): "
                "\"transposed\" (korpuses on ülevõttev akt — CELLAR-i "
                "riikliku rakendusmeetme vaste Eesti seadusele või määrusele "
                "või akt, mille normitehniline märkus direktiivi nimetab), "
                "\"no_measure_required\" (Eesti teatas, et riiklikku meedet "
                "pole vaja) või \"no_evidence_in_corpus\" (kumbagi pole). "
                "MITTE õiguslik järeldus: väärtust \"üle võtmata\" ei ole."
            ),
            "@language": "et",
        },
    ],
    "estleg:hasProcessStep": "A dated lifecycle step of the draft (#717).",
    "estleg:processStepOf": "Inverse of estleg:hasProcessStep (#717).",
    "estleg:processStage": (
        "The estleg:LegislativePhase (eli-dl:ProcessStage) the step observed (#717)."
    ),
    "estleg:stepOrder": "Stable ordinal of the step within its draft; ids only append (#717).",
    "estleg:riigikoguStatus": (
        "Raw Riigikogu proceeding status code of the event the step records, "
        "e.g. VASTU_VOETUD (#717)."
    ),
    "estleg:initiatedBy": (
        "The estleg:Institution that owns the draft in EIS, dated along the "
        "same-legal-person rename chain. No rdfs:range so bare institution IRIs "
        "are not phantom-typed in the drafts bucket (#717)."
    ),
    "estleg:lifecycleStale": (
        "True when the draft is still at public consultation with no evidence "
        "newer than a year before the EIS snapshot; its outcome is unknown, not "
        "invented (#717)."
    ),
    "estleg:riigikoguMark": (
        "Riigikogu registration mark with draft type code, e.g. '897 SE' (#717)."
    ),
    "estleg:riigikoguUuid": (
        "UUID of the Riigikogu draft volume (api.riigikogu.ee "
        "/api/volumes/drafts/{uuid}), #717."
    ),
    "estleg:riigikoguMembership": (
        "Riigikogu membership (koosseis) number the draft was proceeded in (#717)."
    ),
    "estleg:derivationMethod": (
        "How a value was derived (closed vocabulary, docs/DRAFT_LIFECYCLE.md): "
        "eis-feed, riigikogu-eis-number, riigikogu-mark, riigikogu-title-date "
        "(on estleg:ProcessStep); minted-ecli, rederived-case-type (on a "
        "Riigikohus estleg:CourtDecision); title-regex, cellar-interprets (on "
        "an estleg:EUCourtDecision, describing its estleg:interpretsEULaw), #717."
    ),
    "estleg:transposesDirectiveAsserted": (
        "Links an Estonian act to an EU directive that the act's own Riigi "
        "Teataja normitehniline märkus (<normtehnmarkus>) says it transposes "
        "(#711, extract_ntm_directives.py). Independent of the CELLAR-notified "
        "estleg:transposesDirective, so the two can be diffed (asserted but "
        "not notified, notified but not asserted). Deliberately carries no "
        "rdfs:range (cross-bucket directive stubs, #563/#570)."
    ),
    # #701: bilingual, because the point is that neither reading is a finding.
    "estleg:noTranspositionEdgeInCorpus": [
        {
            "@value": (
                "Corpus-coverage flag, NOT a legal finding: true when an "
                "in-force EU directive has no estleg:transposesDirective / "
                "estleg:transposedBy edge in this corpus. Absence of an edge "
                "does not mean Estonia has not transposed the directive. "
                "Stamped with estleg:coverageFlagMethod and "
                "estleg:coverageFlagAsOf (#701, replaces "
                "estleg:hasNoTransposition)."
            ),
            "@language": "en",
        },
        {
            "@value": (
                "Korpuse katvuse lipp, MITTE õiguslik järeldus: tõene, kui "
                "kehtival EL-i direktiivil puudub selles korpuses "
                "ülevõtuseos (estleg:transposesDirective / estleg:transposedBy). "
                "Seose puudumine ei tähenda, et Eesti pole direktiivi üle "
                "võtnud."
            ),
            "@language": "et",
        },
    ],
    "estleg:competentAuthorityNotExtracted": [
        {
            "@value": (
                "Corpus-coverage flag, NOT a legal finding: true when no "
                "estleg:competentAuthority edge was extracted for this statute "
                "root (competentAuthorityCount is 0). It does not mean the act "
                "names no competent authority. Stamped with "
                "estleg:coverageFlagMethod and estleg:coverageFlagAsOf (#701, "
                "replaces estleg:hasNoCompetentAuthority)."
            ),
            "@language": "en",
        },
        {
            "@value": (
                "Korpuse katvuse lipp, MITTE õiguslik järeldus: tõene, kui "
                "seaduse juurtipule pole eraldatud ühtegi "
                "estleg:competentAuthority seost. See ei tähenda, et seadus ei "
                "nimeta pädevat asutust."
            ),
            "@language": "et",
        },
    ],
    "estleg:coverageFlagMethod": (
        "Method marker (generator and rule revision) behind the coverage-gap "
        "flags on this node, e.g. "
        "\"analytical-overlay/generate_analytical_overlay@1\" (#701)."
    ),
    "estleg:coverageFlagAsOf": (
        "Pinned build evaluation date (BUILD_EVALUATION_DATE, not the wall "
        "clock) at which the coverage-gap flags on this node were derived (#701)."
    ),
}

# What an owl:Thing domain or an open range stands in for. schema:domainIncludes
# and schema:rangeIncludes carry no RDFS or OWL 2 RL semantics, so they keep the
# T-Box readable without typing anything. Each entry is the class set measured
# across the whole shipped corpus, reading a node's type from wherever it is
# declared (a provision_versions/ stub is a LegalProvision in its law peep). The
# estleg:Part roots of a multipart act (#566) repeat the act's metadata; they
# are counted under the Act they belong to, not listed as a class of their own.
DOMAIN_INCLUDES: dict[str, tuple[str, ...]] = {
    "estleg:citationSource": ("estleg:Citation",),
    "estleg:currentVersion": ("estleg:LegalProvision",),
    "estleg:earliestSupersedingDate": (
        "estleg:CourtDecision",
        "estleg:MunicipalRegulation",
    ),
    "estleg:enablingProvisionOutdated": ("estleg:MunicipalRegulation",),
    "estleg:enactedByHistoricalMunicipality": ("estleg:MunicipalRegulation",),
    "estleg:historicalMunicipality": ("estleg:Issuer",),
    "estleg:countyCode": ("estleg:Municipality",),
    "estleg:registrikood": ("estleg:Institution",),
    "estleg:xteeMemberCode": ("estleg:Institution",),
    "estleg:validFrom": ("estleg:Institution",),
    "estleg:validTo": ("estleg:Institution",),
    "estleg:predecessorInstitution": ("estleg:Institution",),
    "estleg:successorInstitution": ("estleg:Institution",),
    "estleg:mentionsInstitution": ("estleg:LegalProvision",),
    "estleg:containsPersonalData": ("dcat:Distribution", "dcat:Dataset"),
    "estleg:isExcerpt": ("estleg:Annotation",),
    "estleg:sourceTextLength": ("estleg:Annotation",),
    "estleg:editorialNote": ("estleg:Annotation",),
    "estleg:editorialSource": ("estleg:Annotation",),
    "estleg:subjectSource": ("estleg:EULegislation", "estleg:DraftLegislation"),
    "estleg:derivationMethod": (
        "estleg:ProcessStep",
        "estleg:CourtDecision",
        "estleg:EUCourtDecision",
    ),
    "estleg:enactedBy": ("estleg:Act", "estleg:LegalProvision"),
    "estleg:enactedByMunicipality": ("estleg:Act", "estleg:LegalProvision"),
    "estleg:hasVersion": ("estleg:LegalProvision",),
    "estleg:implementedBy": ("estleg:Act", "estleg:LegalProvision"),
    "estleg:implementedByCount": ("estleg:Act", "estleg:LegalProvision"),
    "estleg:itemNumber": ("estleg:Subsection",),
    "estleg:municipalityStatus": ("estleg:MunicipalRegulation", "estleg:Issuer"),
    "estleg:provisionRef": ("estleg:ProvisionVersion",),
    "estleg:resultedInVersion": ("estleg:AmendmentEvent",),
    "estleg:semanticallySimilarTo": ("estleg:LegalProvision",),
    # Amendment-chain and concept-map header nodes.
    "estleg:totalAmendments": ("owl:Ontology",),
    "estleg:totalConcepts": ("owl:Ontology",),
}
RANGE_INCLUDES: dict[str, tuple[str, ...]] = {
    "estleg:mentionsInstitution": ("estleg:Institution", "estleg:Issuer"),
    "estleg:amendingDraft": ("estleg:DraftLegislation",),
    "estleg:amendsLaw": ("estleg:Act",),
    "estleg:citationSource": ("estleg:LegalProvision", "estleg:Subsection"),
    "estleg:citationTarget": ("estleg:LegalProvision", "estleg:Act"),
    "estleg:definesConcept": ("estleg:Concept",),
    "estleg:enactedAs": ("estleg:Act",),
    "estleg:harmonisedWith": ("estleg:HarmonisationLink",),
    "estleg:harmonises": ("estleg:Act",),
    "estleg:hasProposedAmendment": ("estleg:ProposedAmendment",),
    "estleg:hasVersion": ("estleg:ProvisionVersion",),
    "estleg:implementedBy": ("estleg:Act",),
    "estleg:interpretsEULaw": ("estleg:EULegislation",),
    "estleg:interpretsLaw": ("estleg:LegalProvision", "estleg:Act"),
    "estleg:interpretsVersion": ("estleg:ProvisionVersion",),
    "estleg:issuedUnder": ("estleg:Act",),
    "estleg:partOfAct": ("estleg:Act",),
    "estleg:proposesToAmend": ("estleg:Act",),
    "estleg:resultedInVersion": ("estleg:ProvisionVersion",),
    "estleg:sharedDirective": ("estleg:EULegislation",),
    "estleg:similarTarget": ("estleg:LegalProvision", "estleg:Act"),
    "estleg:transposedBy": ("estleg:Act",),
    "estleg:transposesDirective": ("estleg:EULegislation",),
    "estleg:transposesDirectiveAsserted": ("estleg:EULegislation",),
    "estleg:initiatedBy": ("estleg:Institution",),
    "estleg:versionOf": ("estleg:LegalProvision",),
}


# Property kinds the corpus contradicts (#709). Replaces the whole @type list.
OVERWRITE_TYPES: dict[str, list[str]] = {
    "estleg:targetGroup": ["owl:ObjectProperty"],
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
    """Order keys canonically and wrap array-valued keys holding a scalar."""
    ordered: dict[str, Any] = {}
    for key in PREFERRED_KEYS:
        if key in node:
            ordered[key] = node[key]
    for key, value in node.items():
        if key not in ordered:
            ordered[key] = value
    for key in ARRAY_VALUED_KEYS:
        if key in ordered and not isinstance(ordered[key], list):
            ordered[key] = [ordered[key]]
    return ordered


# #717: the draft-lifecycle class and the Riigikogu-stage LegislativePhase
# individuals (generate_draft_legislation.PHASES orders 7-12). Seeded only when
# absent, like DECLARED_TERMS; apply_skos_schemes adds the SKOS scheme links.
_RIIGIKOGU_PHASES: tuple[tuple[str, int, str, str, str], ...] = (
    ("RiigikoguProceeding", 7, "Riigikogu menetluses", "In Riigikogu proceedings",
     "Eelnõu on Riigikogus algatatud või menetlusse võetud."),
    ("FirstReading", 8, "Esimene lugemine", "First reading",
     "Eelnõu on Riigikogus esimesel lugemisel."),
    ("SecondReading", 9, "Teine lugemine", "Second reading",
     "Eelnõu on Riigikogus teisel lugemisel."),
    ("ThirdReading", 10, "Kolmas lugemine", "Third reading",
     "Eelnõu on Riigikogus kolmandal lugemisel."),
    ("Reconsideration", 11, "Uuesti arutamine", "Reconsideration",
     "Vabariigi President jättis seaduse välja kuulutamata; Riigikogu arutab uuesti."),
    ("Lapsed", 12, "Menetlusest välja langenud", "Lapsed",
     "Eelnõu langes menetlusest välja (koosseisu lõppemine, ühendamine, tagastamine vms)."),
)


def seeded_lifecycle_nodes() -> list[dict]:
    """estleg:ProcessStep and the six Riigikogu-stage phases (#717)."""
    nodes: list[dict] = [
        {
            "@id": "estleg:ProcessStep",
            "@type": ["owl:Class"],
            "rdfs:subClassOf": {"@id": "eli-dl:ProcessStep"},
            "rdfs:label": [
                {"@value": "Menetlussamm", "@language": "et"},
                {"@value": "Process Step", "@language": "en"},
            ],
            "rdfs:comment": (
                "One dated observation of a draft at a legislative stage (EIS "
                "feed or Riigikogu proceeding event), #717."
            ),
        }
    ]
    for name, order, label_et, label_en, comment in _RIIGIKOGU_PHASES:
        nodes.append(
            {
                "@id": f"estleg:Phase_{name}",
                "@type": ["owl:NamedIndividual", "estleg:LegislativePhase", "eli-dl:ProcessStage"],
                "rdfs:label": [
                    {"@value": label_et, "@language": "et"},
                    {"@value": label_en, "@language": "en"},
                ],
                "skos:prefLabel": label_en,
                "rdfs:comment": comment,
                "estleg:phaseOrder": {"@value": str(order), "@type": "xsd:integer"},
            }
        )
    return nodes


def declared_term_node(nid: str) -> dict:
    """A fresh node for a DECLARED_TERMS entry; comment and axioms are applied later."""
    types, label_et, label_en = DECLARED_TERMS[nid]
    return {
        "@id": nid,
        "@type": list(types),
        "rdfs:label": [
            {"@value": label_et, "@language": "et"},
            {"@value": label_en, "@language": "en"},
        ],
    }


def ontology_header() -> dict:
    return {
        "@id": VOCABULARY_IRI,
        "@type": ["owl:Ontology"],
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
    if not is_class_node(merged) and not is_property_node(merged):
        # #709: an individual's facts (a code, a prefLabel) exist only on the
        # source that declares it; a CV placeholder must not shadow them, or
        # the schema projection drops them.
        for key, value in incoming.items():
            if key not in merged:
                merged[key] = copy.deepcopy(value)
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
    if nid in OVERWRITE_COMMENT:
        node["rdfs:comment"] = OVERWRITE_COMMENT[nid]
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


def apply_type_overwrite(node: dict) -> None:
    nid = node.get("@id")
    if isinstance(nid, str) and nid in OVERWRITE_TYPES:
        node["@type"] = list(OVERWRITE_TYPES[nid])


def apply_label_overwrite(node: dict) -> None:
    nid = node.get("@id")
    if isinstance(nid, str) and nid in OVERWRITE_LABEL:
        node["rdfs:label"] = copy.deepcopy(OVERWRITE_LABEL[nid])


def institution_type_nodes() -> list[dict]:
    """The InstitutionType class and its eight individuals (#709)."""
    nodes: list[dict] = [
        {
            "@id": INSTITUTION_TYPE_CLASS,
            "@type": ["owl:Class"],
            "rdfs:label": [
                {"@value": "Institutsiooni liik", "@language": "et"},
                {"@value": "Institution Type", "@language": "en"},
            ],
        }
    ]
    for token, (nid, label_et, label_en) in INSTITUTION_TYPES.items():
        labels = [
            {"@value": label_et, "@language": "et"},
            {"@value": label_en, "@language": "en"},
        ]
        nodes.append(
            {
                "@id": nid,
                "@type": ["owl:NamedIndividual", INSTITUTION_TYPE_CLASS],
                "rdfs:label": labels,
                "skos:prefLabel": copy.deepcopy(labels),
                "skos:notation": token,
            }
        )
    return nodes


def apply_skos_schemes(index: dict[str, dict]) -> None:
    """Type each family's individuals as skos:Concepts in one scheme per family."""
    for family, (scheme_id, label_et, label_en) in SKOS_SCHEMES.items():
        members = sorted(
            nid
            for nid, node in index.items()
            if family in node_types(node)
            and "owl:NamedIndividual" in node_types(node)
        )
        for nid in members:
            node = index[nid]
            if "skos:Concept" not in node_types(node):
                node["@type"] = [*node_types(node), "skos:Concept"]
            node["skos:inScheme"] = iri_ref(scheme_id)
            node["skos:topConceptOf"] = iri_ref(scheme_id)
        labels = [
            {"@value": label_et, "@language": "et"},
            {"@value": label_en, "@language": "en"},
        ]
        index[scheme_id] = {
            "@id": scheme_id,
            "@type": ["skos:ConceptScheme"],
            "rdfs:label": labels,
            "skos:prefLabel": copy.deepcopy(labels),
            "rdfs:comment": (
                f"Closed value set of {family} individuals as SKOS concepts (#709)."
            ),
            "skos:hasTopConcept": [iri_ref(nid) for nid in members],
        }


def apply_eu_corporate_bodies(index: dict[str, dict], links: dict[str, str]) -> None:
    """owl:sameAs from each EUInstitution to its Publications Office authority IRI."""
    for nid, iri in links.items():
        node = index.get(nid)
        if node is not None and "estleg:EUInstitution" in node_types(node):
            node["owl:sameAs"] = iri_ref(iri)


# Terms an earlier pass deprecated that the corpus populates again (#724).
UNDEPRECATED_TERMS = frozenset({"estleg:amendsLaw"})


def apply_deprecation(node: dict) -> None:
    nid = node.get("@id")
    if nid in UNDEPRECATED_TERMS:
        node.pop("owl:deprecated", None)
        node.pop("dcterms:isReplacedBy", None)
        return
    if not isinstance(nid, str) or nid not in DEPRECATED_TERMS:
        return
    replacement, comment = DEPRECATED_TERMS[nid]
    node["owl:deprecated"] = True
    node["dcterms:isReplacedBy"] = iri_ref(replacement)
    node["rdfs:comment"] = comment


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


def apply_includes(node: dict) -> None:
    """Rebuild the schema.org hints from the tables so a dropped entry goes away."""
    nid = node.get("@id")
    if not isinstance(nid, str) or not is_property_node(node):
        return
    for key, table in (
        ("schema:domainIncludes", DOMAIN_INCLUDES),
        ("schema:rangeIncludes", RANGE_INCLUDES),
    ):
        node.pop(key, None)
        if nid in table:
            node[key] = [iri_ref(iri) for iri in table[nid]]


def is_fallback_placeholder(node: dict) -> bool:
    return (
        node.get("estleg:referenceStatus") == FALLBACK_STATUS
        and not is_class_node(node)
        and not is_property_node(node)
    )


def instance_data_ids(krr_dir: Path = KRR_DIR) -> set[str]:
    """Every @id declared by a node in the INSTANCE_DATA_SUBDIRS overlays."""
    ids: set[str] = set()
    for sub in INSTANCE_DATA_SUBDIRS:
        directory = krr_dir / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix not in {".json", ".jsonld"} or not path.is_file():
                continue
            ids.update(
                node["@id"]
                for node in graph_nodes(load_jsonld(path))
                if isinstance(node.get("@id"), str)
            )
    return ids


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
    instance_ids: Iterable[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (vocabulary nodes, unresolved individuals).

    ``instance_ids`` are the @ids real instance files declare (default:
    :func:`instance_data_ids`); a fallback placeholder with one of them is dropped.
    """
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

    for nid in DECLARED_TERMS:
        if nid not in index:
            index[nid] = declared_term_node(nid)
    for seeded in [*institution_type_nodes(), *seeded_lifecycle_nodes()]:
        if seeded["@id"] not in index:
            index[seeded["@id"]] = seeded

    for junk in JUNK_TERMS:
        index.pop(junk, None)

    declared = set(instance_ids if instance_ids is not None else instance_data_ids())
    for nid in [nid for nid, node in index.items() if nid in declared]:
        if is_fallback_placeholder(index[nid]):
            del index[nid]

    unresolved: list[dict] = []
    for nid, node in list(index.items()):
        if is_unresolved_individual(node):
            unresolved.append(normalize_node(node))
            del index[nid]

    index[VOCABULARY_IRI] = ontology_header()

    apply_skos_schemes(index)
    apply_eu_corporate_bodies(index, load_eu_corporate_bodies())
    for nid, node in list(index.items()):
        index[nid] = node = {
            key: (value if key in ("@id", "@type") else expand_rdf_curies(value))
            for key, value in node.items()
        }
        apply_type_overwrite(node)
        apply_label_overwrite(node)
        apply_comment(node)
        apply_deprecation(node)
        apply_domain_range(node)
        apply_includes(node)
        apply_class_alignment(node)

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
    """Write the relocated placeholders, preserving any already on disk (#702).

    ``nodes`` holds only the individuals this run moved out of the T-Box, so a
    second run computes an empty list. Truncating on that empty list destroyed
    the placeholders an earlier run had relocated, which left every reference
    that depended on them dangling (59 `hasSection` edges on `VOS_Part11`
    alone). Merging by ``@id`` makes the builder idempotent; genuinely stale
    entries are caught by validate_all's "stale extra IDs" check rather than by
    silent deletion here. Unreadable or malformed saved placeholders must stop
    consolidation before any of them are overwritten.
    """
    merged: dict[str, dict] = {}
    try:
        existing = load_jsonld(path)
    except FileNotFoundError:
        existing = {"@graph": []}
    if not isinstance(existing, dict) or not isinstance(existing.get("@graph"), list):
        raise ValueError(f"{path}: saved placeholders must contain an @graph array")
    for node in existing["@graph"]:
        if not isinstance(node, dict) or not isinstance(node.get("@id"), str) or not node["@id"]:
            raise ValueError(f"{path}: saved placeholder must have a non-empty string @id")
        merged[node["@id"]] = node
    for node in nodes:
        if isinstance(node.get("@id"), str):
            merged[node["@id"]] = node
    nodes = [merged[key] for key in sorted(merged)]
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
    for prefix, namespace in REQUIRED_PREFIXES.items():
        new_vocab["@context"].setdefault(prefix, namespace)
    new_vocab["@context"].pop("rdf", None)

    # Save the destination before removing relocated individuals from the source.
    # A read/parse/write failure here must leave the vocabulary intact for retry.
    write_unresolved(unresolved, krr_dir / "unresolved_references.jsonld")
    dump_jsonld(krr_dir / "controlled_vocabulary.jsonld", new_vocab)

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
