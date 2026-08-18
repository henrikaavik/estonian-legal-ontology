#!/usr/bin/env python3
"""
Fetch draft legislation (eelnõud) from Estonia's EIS (Eelnõude infosüsteem)
and generate JSON-LD ontology files.

Data sources:
  - Public consultation RSS: eelnoud.valitsus.ee/main/mount/rss/home/publicConsult.rss
  - Review/coordination RSS: eelnoud.valitsus.ee/main/mount/rss/home/review.rss
  - Submission RSS: eelnoud.valitsus.ee/main/mount/rss/home/submission.rss

Generates:
  - krr_outputs/eelnoud/eelnou_*.json  (individual draft files)
  - krr_outputs/eelnoud/EELNOUD_INDEX.json  (registry of all drafts)
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from estleg_common import save_json  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EELNOUD_DIR = KRR_DIR / "eelnoud"
EELNOUD_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://w3id.org/estleg/"

# EIS RSS feed URLs
RSS_FEEDS = {
    "publicConsultation": {
        "url": "https://eelnoud.valitsus.ee/main/mount/rss/home/publicConsult.rss",
        "phase": "PublicConsultation",
        "label_et": "Avalik konsultatsioon",
        "label_en": "Public Consultation",
    },
    "review": {
        "url": "https://eelnoud.valitsus.ee/main/mount/rss/home/review.rss",
        "phase": "Review",
        "label_et": "Kooskõlastamine",
        "label_en": "Inter-ministerial Review",
    },
    "submission": {
        "url": "https://eelnoud.valitsus.ee/main/mount/rss/home/submission.rss",
        "phase": "Submission",
        "label_et": "Esitatud Vabariigi Valitsusele",
        "label_en": "Submitted to Government",
    },
}

# Ministry code mapping
MINISTRY_CODES = {
    "JDM": "Justiitsministeerium",
    "HTM": "Haridus- ja Teadusministeerium",
    "SIM": "Siseministeerium",
    "VÄM": "Välisministeerium",
    "REM": "Regionaalminister",
    "RAM": "Rahandusministeerium",
    "SOM": "Sotsiaalministeerium",
    "MKM": "Majandus- ja Kommunikatsiooniministeerium",
    "KLIM": "Kliimaministeerium",
    "KAM": "Kaitseministeerium",
    "KUM": "Kultuuriministeerium",
    "RK": "Riigikantselei",
    "RIIGIKOGU": "Riigikogu",
}

# JSON-LD context (same as existing ontology files + new draft properties)
CONTEXT = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
}


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    """Create a safe ID from a string."""
    s = value.replace(" ", "_").replace("-", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s[:80] or "Unknown"


def parse_eis_number(title: str) -> tuple[str, str, str]:
    """
    Parse EIS number and date from RSS title.
    Format: "Title text - MINISTRY/YY-NNNN (DD.MM.YYYY)"
    Returns: (eis_number, ministry_code, date_str)
    """
    match = re.search(r"-\s*([A-ZÄÖÜÕa-z]+/\d{2}-\d{4})\s*\((\d{2}\.\d{2}\.\d{4})\)\s*$", title)
    if match:
        eis_number = match.group(1)
        date_str = match.group(2)
        ministry_code = eis_number.split("/")[0]
        return eis_number, ministry_code, date_str
    return "", "", ""


def parse_draft_title(title: str) -> str:
    """Extract the actual title without EIS number and date suffix."""
    cleaned = re.sub(r"\s*-\s*[A-ZÄÖÜÕa-z]+/\d{2}-\d{4}\s*\(\d{2}\.\d{2}\.\d{4}\)\s*$", "", title)
    return cleaned.strip()


def extract_uuid(link: str) -> str:
    """Extract UUID from EIS link."""
    match = re.search(r"/docList/([0-9a-f-]{36})", link)
    return match.group(1) if match else ""


def classify_draft_type(title: str) -> tuple[str, str]:
    """
    Classify the draft type from its title.
    Returns: (type_id, type_label)
    """
    title_lower = title.lower()

    # A väljatöötamiskavatsus (pre-draft intention) is checked FIRST: its
    # title routinely embeds a law name, e.g.
    # "Kliimaseaduse eelnõu väljatöötamise kavatsus", which would otherwise
    # match the broader ``seadus`` arm below and be mis-typed as a Bill
    # (issue #304). Keep this guard above the ``seadus`` check.
    if "kavatsus" in title_lower or "väljatöötamis" in title_lower:
        return "DraftIntent", "Väljatöötamiskavatsus"
    # A regulation (määrus) / order (korraldus) draft is checked BEFORE the
    # broad ``seadus`` substring (issue #599). A ministerial/government
    # regulation title routinely embeds its enabling law name, e.g.
    # "Sotsiaalministri määrus X seaduse alusel" or
    # "Vabariigi Valitsuse määruse ja Y seaduse muutmine", which would
    # otherwise match the ``seadus`` arm and be mis-typed as a Bill /
    # AmendmentBill. A regulation is legally not a bill, and mis-typing also
    # routes it past bill-only impact analysis. #304 fixed only the
    # kavatsus-above-seadus ordering; this seadus-above-määrus ordering is
    # separate.
    elif "määrus" in title_lower:
        if "vabariigi valitsuse" in title_lower:
            return "GovernmentRegulation", "VV määruse eelnõu"
        elif "ministri" in title_lower:
            return "MinisterialRegulation", "Ministri määruse eelnõu"
        return "Regulation", "Määruse eelnõu"
    elif "korraldus" in title_lower:
        return "GovernmentOrder", "Korralduse eelnõu"
    elif "seaduse eelnõu" in title_lower or "seadus" in title_lower:
        if "muutmi" in title_lower:
            return "AmendmentBill", "Seaduse muutmise eelnõu"
        return "Bill", "Seaduseelnõu"
    elif "seisukoht" in title_lower or "euroopa liidu" in title_lower:
        return "EUPosition", "EL seisukoha eelnõu"
    elif "ülevaade" in title_lower:
        return "Report", "Ülevaade"
    elif "kodakondsus" in title_lower:
        return "CitizenshipDecision", "Kodakondsuse otsus"
    elif "tegevuskava" in title_lower or "strateegia" in title_lower:
        return "ActionPlan", "Tegevuskava"
    else:
        return "Other", "Muu eelnõu"


# Change-verb stems that mark a candidate as (part of) the *bill's own*
# title rather than the existing law it targets. A draft titled
# "X seaduse muutmise seadus" must yield only "X seaduse" — the trailing
# "...muutmise seadus" matches the bare ``...seadus`` pattern and would
# otherwise be returned as a phantom affected law that resolves to the
# very same IRI (issue #266: duplicate ``amendsLaw`` IRIs downstream).
_CHANGE_VERB_STEMS = ("muutmi", "täiendami", "kehtetuks", "kehtestami")


def detect_affected_laws(title: str) -> list[str]:
    """
    Try to detect which existing laws this draft would amend.
    Returns list of law names mentioned in the title.
    """
    affected = []
    # Common patterns: "X seaduse muutmine", "X seadustiku muutmine".
    # The leading token is [\w.]+ (not \w+) so a year prefix like "2016."
    # is captured as part of the law name (issue #380). Without it the
    # match starts at "aasta", dropping the year, and the year-less
    # "aasta riigieelarve seaduse" mis-resolves to the only budget law in
    # the corpus (the 2026 one).
    patterns = [
        r"([\w.]+(?:\s+\w+)*?\s+seaduse)\s+(?:muutmi|täiendami)",
        r"([\w.]+(?:\s+\w+)*?\s+seadustiku)\s+(?:muutmi|täiendami)",
        r"([\w.]+(?:\s+\w+)*?\s+seadus)\b",
        r"([\w.]+(?:\s+\w+)*?\s+seadustik)\b",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, title, re.IGNORECASE)
        for m in matches:
            cleaned = m.strip()
            cleaned_lower = cleaned.lower()
            # Skip the draft itself references.
            if "eelnõu" in cleaned_lower or len(cleaned) <= 5:
                continue
            # Skip candidates that still carry a change-verb stem: those
            # are the bill's own title (e.g. "X seaduse muutmise seadus"),
            # not the existing law being amended (issue #266).
            if any(stem in cleaned_lower for stem in _CHANGE_VERB_STEMS):
                continue
            affected.append(cleaned)
    return list(dict.fromkeys(affected))  # deduplicate preserving order


def fetch_rss(url: str) -> list[dict]:
    """Fetch and parse an RSS feed, returning list of items."""
    print(f"  Fetching {url}...")
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except Exception as e:
        print(f"  ERROR: {e}")
        return []

    root = ET.fromstring(resp.text)
    items = []

    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_date_el = item.find("pubDate")

        if title_el is None or title_el.text is None:
            continue

        raw_title = title_el.text.strip()
        # Normalize whitespace (some titles have embedded newlines)
        raw_title = re.sub(r"\s+", " ", raw_title)

        items.append({
            "raw_title": raw_title,
            "title": parse_draft_title(raw_title),
            "link": link_el.text.strip() if link_el is not None and link_el.text else "",
            "pub_date": pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else "",
        })

    print(f"  Found {len(items)} items")
    return items


def generate_schema_nodes() -> list[dict]:
    """Generate the ontology schema nodes for DraftLegislation."""
    return [
        # DraftLegislation class
        {
            "@id": "estleg:DraftLegislation",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Eelnõu (Draft Legislation)", "@language": "et"},
            "rdfs:comment": {"@value": "Õigusakt, mis ei ole veel jõustunud, kuid on seadusandlikus menetluses.", "@language": "et"},
            "dc:description": {"@value": "A legislative draft that has not yet been enacted into law but is in the legislative process.", "@language": "en"},
        },
        # LegislativePhase class
        {
            "@id": "estleg:LegislativePhase",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Seadusandlik etapp (Legislative Phase)", "@language": "et"},
            "rdfs:comment": {"@value": "Eelnõu menetlusetapp EIS süsteemis.", "@language": "et"},
        },
        # DraftType class
        {
            "@id": "estleg:DraftType",
            "@type": ["owl:Class"],
            "rdfs:label": {"@value": "Eelnõu liik (Draft Type)", "@language": "et"},
            "rdfs:comment": {"@value": "Eelnõu tüüp: seaduseelnõu, määruse eelnõu, korralduse eelnõu jne.", "@language": "et"},
        },
        # Phase individuals
        {
            "@id": "estleg:Phase_PublicConsultation",
            "@type": ["owl:NamedIndividual", "estleg:LegislativePhase"],
            "rdfs:label": {"@value": "Avalik konsultatsioon", "@language": "et"},
            "skos:prefLabel": {"@value": "Public Consultation", "@language": "en"},
            "rdfs:comment": {"@value": "Eelnõu on avalikul konsultatsioonil – üldsus saab arvamust avaldada.", "@language": "et"},
            "estleg:phaseOrder": {"@value": "1", "@type": "xsd:integer"},
        },
        {
            "@id": "estleg:Phase_Review",
            "@type": ["owl:NamedIndividual", "estleg:LegislativePhase"],
            "rdfs:label": {"@value": "Kooskõlastamine", "@language": "et"},
            "skos:prefLabel": {"@value": "Inter-ministerial Review", "@language": "en"},
            "rdfs:comment": {"@value": "Eelnõu on ministeeriumidevahelisel kooskõlastamisel.", "@language": "et"},
            "estleg:phaseOrder": {"@value": "2", "@type": "xsd:integer"},
        },
        {
            "@id": "estleg:Phase_Submission",
            "@type": ["owl:NamedIndividual", "estleg:LegislativePhase"],
            "rdfs:label": {"@value": "Esitatud Vabariigi Valitsusele", "@language": "et"},
            "skos:prefLabel": {"@value": "Submitted to Government", "@language": "en"},
            "rdfs:comment": {"@value": "Eelnõu on esitatud Vabariigi Valitsusele otsustamiseks.", "@language": "et"},
            "estleg:phaseOrder": {"@value": "3", "@type": "xsd:integer"},
        },
        # Draft type individuals
        {
            "@id": "estleg:DraftType_Bill",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Seaduseelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Bill", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_AmendmentBill",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Seaduse muutmise eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Amendment Bill", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_GovernmentRegulation",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "VV määruse eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Government Regulation Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_MinisterialRegulation",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Ministri määruse eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Ministerial Regulation Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_GovernmentOrder",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Korralduse eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Government Order Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_EUPosition",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "EL seisukoha eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "EU Position Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_DraftIntent",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Väljatöötamiskavatsus", "@language": "et"},
            "skos:prefLabel": {"@value": "Draft Intent / Pre-draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_Regulation",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Määruse eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Regulation Draft", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_ActionPlan",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Tegevuskava", "@language": "et"},
            "skos:prefLabel": {"@value": "Action Plan", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_Report",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Ülevaade", "@language": "et"},
            "skos:prefLabel": {"@value": "Report", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_CitizenshipDecision",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Kodakondsuse otsus", "@language": "et"},
            "skos:prefLabel": {"@value": "Citizenship Decision", "@language": "en"},
        },
        {
            "@id": "estleg:DraftType_Other",
            "@type": ["owl:NamedIndividual", "estleg:DraftType"],
            "rdfs:label": {"@value": "Muu eelnõu", "@language": "et"},
            "skos:prefLabel": {"@value": "Other Draft", "@language": "en"},
        },
        # Object properties
        {
            "@id": "estleg:legislativePhase",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "seadusandlik etapp", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "estleg:LegislativePhase"},
            "rdfs:comment": {"@value": "The current legislative phase of the draft.", "@language": "en"},
        },
        {
            "@id": "estleg:draftType",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "eelnõu liik", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "estleg:DraftType"},
            "rdfs:comment": {"@value": "The type/category of the draft.", "@language": "en"},
        },
        {
            "@id": "estleg:amendsLaw",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": {"@value": "muudab seadust", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "estleg:LegalProvision"},
            "rdfs:comment": {"@value": "Links a draft to the existing law it proposes to amend.", "@language": "en"},
        },
        # Datatype properties
        {
            "@id": "estleg:eisNumber",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "EIS number",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "The EIS (Eelnõude infosüsteem) reference number.", "@language": "en"},
        },
        {
            "@id": "estleg:eisLink",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "EIS link",
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:anyURI"},
            "rdfs:comment": {"@value": "Direct link to the draft in EIS.", "@language": "en"},
        },
        {
            "@id": "estleg:initiator",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "algataja", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "The ministry or institution that initiated the draft.", "@language": "en"},
        },
        {
            "@id": "estleg:publicationDate",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "avaldamiskuupäev", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:date"},
            "rdfs:comment": {"@value": "Date the draft was published/registered in EIS.", "@language": "en"},
        },
        {
            "@id": "estleg:affectedLawName",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": {"@value": "mõjutatud seadus", "@language": "et"},
            "rdfs:domain": {"@id": "estleg:DraftLegislation"},
            "rdfs:range": {"@id": "xsd:string"},
            "rdfs:comment": {"@value": "Name of existing law this draft proposes to amend.", "@language": "en"},
        },
    ]


def generate_draft_node(
    item: dict,
    phase_id: str,
    eis_number: str,
    ministry_code: str,
    date_str: str,
) -> dict:
    """Generate a JSON-LD node for a single draft."""
    uuid = extract_uuid(item["link"])
    draft_type_id, draft_type_label = classify_draft_type(item["title"])
    affected_laws = detect_affected_laws(item["title"])

    # Create stable ID from EIS number or UUID
    if eis_number:
        safe_id = sanitize_id(eis_number)
    elif uuid:
        safe_id = uuid.replace("-", "")[:16]
    else:
        safe_id = sanitize_id(item["title"][:40])

    node: dict = {
        "@id": f"estleg:Draft_{safe_id}",
        "@type": ["owl:NamedIndividual", "estleg:DraftLegislation"],
        "rdfs:label": {"@value": item["title"], "@language": "et"},
        "estleg:legislativePhase": {"@id": f"estleg:Phase_{phase_id}"},
        "estleg:draftType": {"@id": f"estleg:DraftType_{draft_type_id}"},
    }

    if eis_number:
        node["estleg:eisNumber"] = eis_number

    if item["link"]:
        node["estleg:eisLink"] = {"@value": item["link"], "@type": "xsd:anyURI"}
        node["dcterms:source"] = {"@id": item["link"]}

    # Issue #606 (item 9): only emit estleg:initiator for a KNOWN ministry
    # code. After the 2023 ministry reorg an unmapped code (e.g. a newly
    # created ministry) must not be passed through verbatim as the initiator
    # literal — that pollutes the data with a raw code instead of a name.
    # Surface the raw code under a marker property plus a stderr warning.
    if ministry_code in MINISTRY_CODES:
        # Plain xsd:string (issue #382): estleg:initiator has rdfs:range
        # xsd:string and the SHACL DraftLegislationShape constrains it to
        # sh:datatype xsd:string. A {@value,@language} value-object is an
        # rdf:langString and would fail SHACL conformance.
        node["estleg:initiator"] = MINISTRY_CODES[ministry_code]
    elif ministry_code:
        print(
            f"  WARNING: unmapped EIS ministry code {ministry_code!r}; "
            "emitting estleg:initiatorCodeUnmapped instead of estleg:initiator",
            file=sys.stderr,
        )
        # Plain xsd:string too, mirroring the estleg:initiator shape note.
        node["estleg:initiatorCodeUnmapped"] = ministry_code

    if date_str:
        try:
            parsed = datetime.strptime(date_str, "%d.%m.%Y")
            # Issue #606 (item 13b): strptime validates FORMAT, not RANGE, so
            # a spurious "01.01.1900"/"31.12.2099" would otherwise be accepted.
            # EIS (eelnoud.valitsus.ee) drafts are recent (the system dates to
            # the early 2010s); bound the year to a sane EIS-era window and
            # only emit publicationDate when in range. The upper bound is
            # now()+1 (not a hardcoded future year) to tolerate near-future
            # dates without admitting far-future garbage.
            if 2000 <= parsed.year <= datetime.now().year + 1:
                node["estleg:publicationDate"] = {
                    "@value": parsed.strftime("%Y-%m-%d"),
                    "@type": "xsd:date",
                }
        except ValueError:
            pass

    if affected_laws:
        node["estleg:affectedLawName"] = affected_laws

    return node


def main():
    print("=" * 60)
    print("Fetching draft legislation from EIS")
    print("=" * 60)

    all_drafts: list[dict] = []
    seen_ids: set[str] = set()

    # Fetch all RSS feeds
    for feed_key, feed_info in RSS_FEEDS.items():
        print(f"\n--- {feed_info['label_et']} ({feed_info['label_en']}) ---")
        items = fetch_rss(feed_info["url"])

        for item in items:
            eis_number, ministry_code, date_str = parse_eis_number(item["raw_title"])
            uuid = extract_uuid(item["link"])

            # Deduplicate by EIS number or UUID
            dedup_key = eis_number or uuid or item["title"][:60]
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            draft_node = generate_draft_node(
                item,
                phase_id=feed_info["phase"],
                eis_number=eis_number,
                ministry_code=ministry_code,
                date_str=date_str,
            )

            all_drafts.append({
                "node": draft_node,
                "feed": feed_key,
                "eis_number": eis_number,
                "title": item["title"],
                "link": item["link"],
                "phase": feed_info["phase"],
            })

    print(f"\n--- Total unique drafts: {len(all_drafts)} ---")

    # Sort drafts by node @id so every downstream artifact (per-phase peeps,
    # the combined .jsonld, and EELNOUD_INDEX.json) is written in a stable,
    # deterministic order. RSS feeds return items in arrival order, which
    # churns the whole output between runs even when content is unchanged
    # (issue #341; AGENTS.md no-nondeterministic-ordering rule).
    all_drafts.sort(key=lambda d: d["node"]["@id"])

    # Generate schema file
    print("\n--- Generating schema file ---")
    schema_doc = {
        "@context": CONTEXT,
        "@graph": generate_schema_nodes(),
    }
    schema_path = EELNOUD_DIR / "eelnoud_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name} ({len(schema_doc['@graph'])} nodes)")

    # Generate individual draft files grouped by phase
    for phase_key, feed_info in RSS_FEEDS.items():
        phase_drafts = [d for d in all_drafts if d["feed"] == phase_key]
        if not phase_drafts:
            continue

        phase_id = feed_info["phase"]
        print(f"\n--- Generating {phase_id} file ({len(phase_drafts)} drafts) ---")

        graph: list[dict] = [
            {
                "@id": f"estleg:Eelnoud_{phase_id}_Map_2026",
                "@type": ["owl:Ontology"],
                "rdfs:label": {"@value": f"EIS eelnõud – {feed_info['label_et']}", "@language": "et"},
                "dc:description": {"@value": f"Eelnõud, mis on hetkel etapis: {feed_info['label_et']}", "@language": "et"},
                "dc:source": "Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee",
            },
        ]

        for d in phase_drafts:
            graph.append(d["node"])

        doc = {"@context": CONTEXT, "@graph": graph}
        out_path = EELNOUD_DIR / f"eelnoud_{phase_id.lower()}_peep.json"
        save_json(out_path, doc)
        print(f"  Saved: {out_path.name} ({len(graph)} nodes)")

    # Generate combined file with all drafts
    print("\n--- Generating combined drafts file ---")
    combined_graph: list[dict] = [
        {
            "@id": "estleg:Eelnoud_Combined_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": {"@value": "EIS eelnõud – kõik etapid (Combined)", "@language": "et"},
            "dc:description": {"@value": "Kõik EIS eelnõud kõigist menetlusetappidest.", "@language": "et"},
            "dc:source": "Eelnõude infosüsteem (EIS) – eelnoud.valitsus.ee",
        },
    ]
    combined_graph.extend(generate_schema_nodes())
    for d in all_drafts:
        combined_graph.append(d["node"])

    combined_doc = {"@context": CONTEXT, "@graph": combined_graph}
    combined_path = EELNOUD_DIR / "eelnoud_combined.jsonld"
    save_json(combined_path, combined_doc)
    print(f"  Saved: {combined_path.name} ({len(combined_graph)} nodes)")

    # Generate index
    print("\n--- Generating drafts index ---")
    # NOTE (issue #295): no wall-clock ``generated`` field. EELNOUD_INDEX.json
    # is git-tracked; embedding ``datetime.now()`` made it re-diff on every run
    # regardless of data changes (timestamp-only churn, banned by AGENTS.md).
    # Every value below is fully determined by the fetched corpus, so the index
    # is byte-stable across reruns of the same inputs.
    index = {
        "total_drafts": len(all_drafts),
        "source": "https://eelnoud.valitsus.ee",
        "phases": {},
        "drafts": [],
    }

    for phase_key, feed_info in RSS_FEEDS.items():
        phase_drafts = [d for d in all_drafts if d["feed"] == phase_key]
        index["phases"][feed_info["phase"]] = {
            "label_et": feed_info["label_et"],
            "label_en": feed_info["label_en"],
            "count": len(phase_drafts),
            "file": f"eelnoud_{feed_info['phase'].lower()}_peep.json",
        }

    for d in all_drafts:
        index["drafts"].append({
            "title": d["title"],
            "eis_number": d["eis_number"],
            "phase": d["phase"],
            "link": d["link"],
        })

    index_path = EELNOUD_DIR / "EELNOUD_INDEX.json"
    save_json(index_path, index)
    print(f"  Saved: {index_path.name}")

    # Summary
    print("\n" + "=" * 60)
    print(f"Done! Generated {len(all_drafts)} draft legislation entries.")
    print(f"Files saved to: {EELNOUD_DIR.relative_to(REPO_ROOT)}")
    print()
    for phase_key, feed_info in RSS_FEEDS.items():
        phase_count = sum(1 for d in all_drafts if d["feed"] == phase_key)
        print(f"  {feed_info['label_et']}: {phase_count} drafts")
    print("=" * 60)


if __name__ == "__main__":
    main()
