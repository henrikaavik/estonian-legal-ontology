#!/usr/bin/env python3
"""Tag every CV ``rdfs:label`` with ``@et`` + ``@en`` (#437).

    python3 scripts/tag_vocabulary_labels.py
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from estleg.consolidate_tbox import SCHEMA_RELPATHS, graph_nodes, load_jsonld
from estleg.estleg_common import KRR_DIR, bilingual_label, jsonld_text

VOCAB_PATH = KRR_DIR / "controlled_vocabulary.jsonld"
PAREN = re.compile(r"^(?P<head>.+?)\s*\((?P<paren>[^)]+)\)\s*$")
EST_CHARS = re.compile(r"[äöüõšžÄÖÜÕŠŽ]")
EST_WORDS = re.compile(
    r"\b(seadus|eelnõu|määrus|kohus|akti|osa|redaktsioon|kehtiv|"
    r"viide|sihtrühm|õigus|kohtu|paragrahv|lõige|muudat|jõus)\b",
    re.IGNORECASE,
)

# Explicit Estonian (or English) when heuristics would be ugly.
ET_BY_LOCAL: dict[str, str] = {
    "AmendmentEvent": "Muudatussündmus",
    "Annex": "Lisa",
    "Annotation": "Annotatsioon",
    "Citation": "Viide",
    "Competence": "Pädevus",
    "Division": "Jagu",
    "DomesticRegulation": "Siseriiklik määrus",
    "GeneralPartConcept": "Üldosa mõiste",
    "GovernmentRegulation": "Vabariigi Valitsuse määrus",
    "HistoricalMunicipality": "Ajalooline omavalitsus",
    "Institution": "Institutsioon",
    "Issuer": "Väljaandja",
    "KovProvision": "KOV säte",
    "Law": "Seadus",
    "LegalPart": "Õiguslik osa",
    "MinisterialRegulation": "Ministri määrus",
    "MunicipalRegulation": "KOV määrus",
    "Municipality": "Omavalitsus",
    "NationalRegulation": "Riigi määrus",
    "NormativeType": "Normatiivne liik",
    "Part": "Osa",
    "ParliamentaryResolution": "Riigikogu otsus",
    "PresidentialDecree": "Vabariigi Presidendi seadlus",
    "isRatificationShell": "on ratifitseerimiskest",
    "ProcedureStage": "Menetlusetapp",
    "ProposedAmendment": "Kavandatud muudatus",
    "ProvisionVersion": "Sätte redaktsioon",
    "ReleaseDelta": "Väljalaske muutus",
    "Sanction": "Sanktsioon",
    "Section": "Jaotis",
    "Similarity": "Sarnasus",
    "Subdivision": "Alljaotis",
    "Subsection": "Lõige",
    "UnresolvedReferencePlaceholder": "Lahendamata viite kohatäide",
    "actNumber": "akti number",
    "added": "lisatud",
    "addedCount": "lisatud arv",
    "affectedBy": "mõjutatud",
    "amendedBy": "muudetud",
    "amendingDraft": "muutev eelnõu",
    "amendmentDate": "muudatuse kuupäev",
    "amends": "muudab",
    "annexNumber": "lisa number",
    "annotates": "annoteerib",
    "annotationDate": "annotatsiooni kuupäev",
    "annotationSource": "annotatsiooni allikas",
    "annotationSourceUrl": "annotatsiooni allika URL",
    "annotationText": "annotatsiooni tekst",
    "annotationType": "annotatsiooni liik",
    "applicableProvision": "kohaldatav säte",
    "appliesToProvision": "kohaldub sättele",
    "appliesToProvisionCount": "sätete arv",
    "belongsToCluster": "kuulub klastrisse",
    "bodyType": "organi liik",
    "caseTypeCode": "asja liigi kood",
    "celexNumber": "CELEX-number",
    "changeType": "muudatuse liik",
    "chapterNumber": "peatüki number",
    "citationDetail": "viite täpsustus",
    "citationTarget": "viite sihtmärk",
    "citationText": "viite tekst",
    "competenceArea": "pädevusvaldkond",
    "competenceType": "pädevuse liik",
    "competentAuthority": "pädev asutus",
    "competentAuthorityCount": "pädevate asutuste arv",
    "governs": "kohaldab",
    "hasNoCompetentAuthority": "puudub pädev asutus",
    "hasNoTransposition": "puudub ülevõtt",
    "inboundCitationCount": "sissetulevate viidete arv",
    "interpretationCount": "tõlgenduste arv",
    "similarFrom": "sarnasuse lähtesäte",
    "consistencyChecked": "järjepidevus kontrollitud",
    "containsPersonalData": "sisaldab isikuandmeid",
    "contentStatus": "sisu staatus",
    "contentStatusReason": "sisu staatuse põhjus",
    "county": "maakond",
    "coversConcept": "katab mõiste",
    "currentMunicipality": "kehtiv omavalitsus",
    "currentVersion": "kehtiv redaktsioon",
    "documentType": "dokumendi liik",
    "ehakCode": "EHAK kood",
    "entryIntoForce": "jõustumine",
    "euCourtCode": "EL kohtu kood",
    "euInstitutionCode": "EL institutsiooni kood",
    "formerEhakCode": "endine EHAK kood",
    "formerName": "endine nimi",
    "globalId": "globaalne identifikaator",
    "grantedBy": "andnud",
    "hasAnnex": "on lisa",
    "hasChapter": "on peatükk",
    "hasCompetence": "on pädevus",
    "hasDivision": "on jagu",
    "hasPart": "on osa",
    "hasProposedAmendment": "on kavandatud muudatus",
    "hasProvision": "on säte",
    "hasSanction": "on sanktsioon",
    "hasSection": "on jaotis",
    "hasSubdivision": "on alljaotis",
    "hasSubsection": "on lõige",
    "hasVersion": "on redaktsioon",
    "historicalMunicipalityName": "ajalooline omavalitsuse nimi",
    "implementedBy": "rakendatud",
    "implementedByCount": "rakendajate arv",
    "implementsCitation": "rakendab viidet",
    "inChapter": "peatükis",
    "inDivision": "jaos",
    "inPart": "osas",
    "institution": "institutsioon",
    "institutionType": "institutsiooni liik",
    "interpretedBy": "tõlgendatud",
    "interpretsEULaw": "tõlgendab EL õigust",
    "interpretsLaw": "tõlgendab seadust",
    "isCurrentAmendment": "on kehtiv muudatus",
    "isKov": "on KOV",
    "isPartOf": "on osa",
    "isStatutoryDefault": "on seaduslik vaikimisi",
    "isStubNode": "on tüvi",
    "issuedUnder": "antud alusel",
    "issuer": "väljaandja",
    "jurisdiction": "jurisdiktsioon",
    "lastAmendmentDate": "viimase muudatuse kuupäev",
    "legalText": "õigustekst",
    "mappedPart": "kaardistatud osa",
    "mappingEvidence": "kaardistuse tõend",
    "mappingSource": "kaardistuse allikas",
    "mapsToGeneralPart": "vastab üldosale",
    "maxPenalty": "maksimaalne karistus",
    "maxPenaltyAmount": "maksimaalne karistussumma",
    "maxPenaltyCurrency": "maksimaalse karistuse valuuta",
    "maxPenaltyUnit": "maksimaalse karistuse ühik",
    "mergedAt": "ühendatud",
    "mergerEvidence": "ühendamise tõend",
    "minPenalty": "minimaalne karistus",
    "minPenaltyAmount": "minimaalne karistussumma",
    "minPenaltyCurrency": "minimaalse karistuse valuuta",
    "minPenaltyUnit": "minimaalse karistuse ühik",
    "municipalityStatus": "omavalitsuse staatus",
    "municipalityType": "omavalitsuse liik",
    "normativeType": "normatiivne liik",
    "officialEnglishText": "ametlik ingliskeelne tekst",
    "estoniaRelevant": "Eestile asjakohane",
    "paragrahv": "paragrahv",
    "parentProvision": "ülemsäte",
    "phaseOrder": "etapi järjekord",
    "preambleText": "preambuli tekst",
    "proposesToAmend": "teeb ettepaneku muuta",
    "provenanceUnverified": "pärinemine kinnitamata",
    "provisionCount": "sätete arv",
    "publicationYear": "avaldamisaasta",
    "referenceStatus": "viite staatus",
    "referencedBy": "viidatud",
    "references": "viitab",
    "regulationTypeBucket": "määruse tüübikopp",
    "relatesToConcept": "seotud mõistega",
    "removed": "eemaldatud",
    "removedCount": "eemaldatud arv",
    "repealDate": "kehtetuks tunnistamise kuupäev",
    "requestedCluster": "nõutud klaster",
    "rtReference": "RT viide",
    "sanctionType": "sanktsiooni liik",
    "schemaVersion": "skeemi versioon",
    "sectionNumber": "jaotise number",
    "semanticallySimilarTo": "semantiliselt sarnane",
    "similarAct": "sarnane akt",
    "similarTarget": "sarnasuse sihtmärk",
    "similarityModel": "sarnasuse mudel",
    "similarityScore": "sarnasuse skoor",
    "similarityStatus": "sarnasuse staatus",
    "sourceAct": "lähteakt",
    "sourceGlobaalID": "lähte globaalID",
    "sourceStructure": "lähte struktuur",
    "sourceUrl": "lähte URL",
    "subsectionNumber": "lõike number",
    "succeededBy": "järglane",
    "summary": "kokkuvõte",
    "supersededByVersion": "asendatud redaktsiooniga",
    "targetGroup": "sihtrühm",
    "temporalStatus": "ajaline staatus",
    "terviktekstId": "tervikteksti ID",
    "titleNormalized": "normaliseeritud pealkiri",
    "topicCluster": "teemaklaster",
    "totalAmendments": "muudatuste koguarv",
    "totalConcepts": "mõistete koguarv",
    "transpositionDeadline": "ülevõtmise tähtaeg",
    "versionOf": "redaktsioon millest",
    "versionRedactionId": "redaktsiooni ID",
    "versionText": "redaktsiooni tekst",
    "versionValidFrom": "redaktsioon kehtib alates",
    "versionValidTo": "redaktsioon kehtib kuni",
    "CaseType_Other": "Muu asja liik",
    "DraftType_ActionPlan": "Tegevuskava",
    "DraftType_AmendmentBill": "Seaduse muutmise eelnõu",
    "DraftType_Bill": "Seaduseelnõu",
    "DraftType_CitizenshipDecision": "Kodakondsuse otsus",
    "DraftType_DraftIntent": "Väljatöötamiskavatsus",
    "DraftType_EUPosition": "EL seisukoha eelnõu",
    "DraftType_GovernmentOrder": "Korralduse eelnõu",
    "DraftType_GovernmentRegulation": "VV määruse eelnõu",
    "DraftType_MinisterialRegulation": "Ministri määruse eelnõu",
    "DraftType_Other": "Muu eelnõu",
    "DraftType_Regulation": "Määruse eelnõu",
    "DraftType_Report": "Ülevaade",
    "EUCourt_CivilServiceTribunal": "Avaliku Teenistuse Kohus",
    "EUCourt_CourtOfJustice": "Euroopa Kohus",
    "EUCourt_GeneralCourt": "Üldkohus",
    "EUDecType_AGOpinion": "Kohtujuristi ettepanek",
    "EUDecType_CourtOpinion": "Kohtu arvamus",
    "EUDecType_Judgment": "Kohtuotsus",
    "EUDecType_Order": "Kohtumäärus",
    "EUDocType_Decision": "EL otsus",
    "EUDocType_Directive": "EL direktiiv",
    "EUDocType_Regulation": "EL määrus",
    "NormType_Definition": "Definitsioon",
    "NormType_Obligation": "Kohustus",
    "NormType_Permission": "Luba",
    "NormType_Prohibition": "Keeld",
    "NormType_Right": "Õigus",
    "Phase_Enacted": "Vastu võetud",
    "Phase_PublicConsultation": "Avalik konsultatsioon",
    "Phase_Rejected": "Tagasi lükatud",
    "Phase_Review": "Kooskõlastamine",
    "Phase_Submission": "Esitatud Vabariigi Valitsusele",
    "Phase_Withdrawn": "Tagasi võetud",
    "TargetGroup_Business": "Ettevõtja",
    "TargetGroup_Citizen": "Kodanik",
    "TargetGroup_NGO": "Vabaühendus",
    "TargetGroup_Official": "Ametnik",
    "TargetGroup_PublicBody": "Avalik-õiguslik asutus",
    "TemporalStatus_InForce": "Jõus",
    "TemporalStatus_Repealed": "Kehtetu",
    "TemporalStatus_Unknown": "Teadmata",
}

EN_BY_LOCAL: dict[str, str] = {
    "officialEnglishText": "official English text",
    "isRatificationShell": "is ratification shell",
    "ParliamentaryResolution": "Parliamentary resolution",
    "PresidentialDecree": "Presidential decree",
    "estoniaRelevant": "Estonia-relevant",
    "CaseType_Administrative": "Administrative case",
    "CaseType_Civil": "Civil case",
    "CaseType_ConstitutionalReview": "Constitutional review",
    "CaseType_Criminal": "Criminal case",
    "CaseType_Misdemeanor": "Misdemeanour case",
    "DecisionType_Judgment": "Judgment",
    "DecisionType_OrderRuling": "Order",
    "DecisionType_Resolution": "Resolution",
    "DecisionType_Ruling": "Ruling",
    "DraftType_ActionPlan": "Action plan",
    "DraftType_AmendmentBill": "Amendment bill",
    "DraftType_Bill": "Bill",
    "DraftType_CitizenshipDecision": "Citizenship decision",
    "DraftType_DraftIntent": "Drafting intent",
    "DraftType_EUPosition": "EU position draft",
    "DraftType_GovernmentOrder": "Government order draft",
    "DraftType_GovernmentRegulation": "Government regulation draft",
    "DraftType_MinisterialRegulation": "Ministerial regulation draft",
    "DraftType_Other": "Other draft",
    "DraftType_Regulation": "Regulation draft",
    "DraftType_Report": "Report",
    "EUCourt_CivilServiceTribunal": "Civil Service Tribunal",
    "EUCourt_CourtOfJustice": "Court of Justice",
    "EUCourt_EFTACourt": "EFTA Court",
    "EUCourt_GeneralCourt": "General Court",
    "EUCourt_NationalCourt": "National court",
    "EUDecType_AGOpinion": "Advocate General opinion",
    "EUDecType_CourtOpinion": "Court opinion",
    "EUDecType_Judgment": "Judgment",
    "EUDecType_Order": "Order",
    "EUDecType_Other": "Other",
    "EUDocType_Decision": "EU decision",
    "EUDocType_Directive": "EU directive",
    "EUDocType_InternationalAgreement": "International agreement",
    "EUDocType_ParliamentPosition": "EP position",
    "EUDocType_Regulation": "EU regulation",
    "EUInst_CouncilOfEU": "Council of the EU",
    "EUInst_EuropeanCentralBank": "European Central Bank",
    "EUInst_EuropeanCommission": "European Commission",
    "EUInst_EuropeanParliament": "European Parliament",
    "Phase_Enacted": "Enacted",
    "Phase_PublicConsultation": "Public consultation",
    "Phase_Rejected": "Rejected",
    "Phase_Review": "Inter-ministerial review",
    "Phase_Submission": "Submitted to Government",
    "Phase_Withdrawn": "Withdrawn",
    "RefType_Citation": "Citation",
    "RefType_Derogation": "Derogation",
    "RefType_Exception": "Exception",
    "RefType_LegalBasis": "Legal basis",
    "RefType_Repeal": "Repeal",
    "kehtiv": "in force as of",
    "inForce": "in force",
    "initiator": "initiator",
    "publicationDate": "publication date",
    "draftType": "draft type",
    "legislativePhase": "legislative phase",
    "enactedAs": "enacted as",
    "amendsLaw": "amends law",
    "affectedLawName": "affected law name",
    "documentDate": "document date",
    "euCaseNumber": "EU case number",
    "euCourt": "EU court",
    "euCourtDecisionType": "EU court decision type",
    "euDocumentType": "EU document type",
    "euInstitution": "EU institution",
    "eliIdentifier": "ELI identifier",
    "eisLink": "EIS link",
    "eisNumber": "EIS number",
    "rikObjectId": "RIK object ID",
    "rikosUrl": "RIKOS URL",
    "ecliIdentifier": "ECLI",
}


def local_name(nid: str) -> str:
    return nid.split(":")[-1]


def looks_estonian(text: str) -> bool:
    return bool(EST_CHARS.search(text) or EST_WORDS.search(text))


def spaced_local(local: str) -> str:
    if local.startswith("EUInst_"):
        return local.split("_", 1)[1].replace("_", " ")
    text = local.replace("_", " ")
    text = re.sub(r"(?<!^)(?=[A-Z])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def langs_of(label: object) -> dict[str, str]:
    found: dict[str, str] = {}
    items = label if isinstance(label, list) else [label]
    for item in items:
        if isinstance(item, dict) and item.get("@language") and item.get("@value"):
            found[str(item["@language"])] = str(item["@value"])
        elif isinstance(item, str) and item:
            found.setdefault("", item)
    return found


def schema_label_map(krr_dir: Path = KRR_DIR) -> dict[str, dict[str, str]]:
    mapped: dict[str, dict[str, str]] = {}
    for rel in SCHEMA_RELPATHS.values():
        path = krr_dir / rel
        if not path.is_file():
            continue
        for node in graph_nodes(load_jsonld(path)):
            nid = node.get("@id")
            if not isinstance(nid, str):
                continue
            found = langs_of(node.get("rdfs:label"))
            pref = node.get("skos:prefLabel")
            if isinstance(pref, dict) and pref.get("@language") and pref.get("@value"):
                found.setdefault(str(pref["@language"]), str(pref["@value"]))
            if found:
                mapped.setdefault(nid, {}).update(found)
    return mapped


def pair_for(nid: str, label: object, schema: dict[str, dict[str, str]]) -> tuple[str, str]:
    local = local_name(nid)
    found = langs_of(label)
    schema_found = schema.get(nid, {})
    et = found.get("et") or schema_found.get("et") or ET_BY_LOCAL.get(local)
    en = found.get("en") or schema_found.get("en") or EN_BY_LOCAL.get(local)
    plain = found.get("") or jsonld_text(label)
    if plain:
        match = PAREN.match(plain)
        if match:
            head, paren = match.group("head"), match.group("paren")
            if looks_estonian(head) and not looks_estonian(paren):
                et = et or head
                en = en or paren
            elif looks_estonian(paren) and not looks_estonian(head):
                en = en or head
                et = et or paren
        if et is None and looks_estonian(plain):
            et = plain
        if en is None and not looks_estonian(plain):
            en = plain
    et = et or ET_BY_LOCAL.get(local) or plain or spaced_local(local)
    en = en or EN_BY_LOCAL.get(local) or spaced_local(local)
    if et.startswith("E U Inst"):
        et = local.replace("EUInst_", "")
    if en.startswith("E U Inst") or en.startswith("EUInst "):
        en = local.replace("EUInst_", "")
    return et, en


def tag_document(doc: dict, schema: dict[str, dict[str, str]]) -> int:
    changed = 0
    for node in graph_nodes(doc):
        if "rdfs:label" not in node:
            continue
        nid = str(node.get("@id", ""))
        et, en = pair_for(nid, node["rdfs:label"], schema)
        new = bilingual_label(et, en)
        if node["rdfs:label"] != new:
            node["rdfs:label"] = new
            changed += 1
    return changed


def missing_bilingual(doc: dict) -> list[str]:
    missing: list[str] = []
    for node in graph_nodes(doc):
        if "rdfs:label" not in node:
            continue
        langs = set(langs_of(node["rdfs:label"]))
        if langs != {"et", "en"}:
            missing.append(str(node.get("@id")))
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    doc = load_jsonld(VOCAB_PATH)
    schema = schema_label_map()
    changed = tag_document(doc, schema)
    leftover = missing_bilingual(doc)
    print(f"tagged {changed} labels; leftover {len(leftover)}")
    for nid in leftover[:20]:
        print(f"  missing bilingual: {nid}")
    if leftover:
        return 1
    if not args.dry_run:
        VOCAB_PATH.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
