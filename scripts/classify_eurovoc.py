#!/usr/bin/env python3
"""
Classify Estonian laws with EuroVoc subject taxonomy using keyword matching.

Scans existing law JSON-LD files for Estonian legal terminology and assigns
EuroVoc concept URIs (http://eurovoc.europa.eu/{id}) via dcterms:subject.
All ids are verified real EuroVoc descriptor ids (#421); the old→new audit
trail lives in data/eurovoc_domain_mapping.json.

Generates:
  - krr_outputs/eurovoc_classification.json    (report of all classifications)
  - Updates existing law JSON-LD files with dcterms:subject
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
import unicodedata
from pathlib import Path

from estleg_common import (
    AGGREGATE_REGISTRY_PREFIXES,
    BUILD_EVALUATION_DATE,
    iter_peep_files,
    jsonld_text,
    save_json as _save_json,
)
from kov_pipeline_coverage import (
    PINNED_RUN_TIMESTAMP,
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"

NS = "https://data.riik.ee/ontology/estleg#"

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

EUROVOC_URI_BASE = "http://eurovoc.europa.eu/"

# EuroVoc domain mapping: descriptor id → (slug, label_et, label_en, [keywords])
# Keywords are Estonian stems/substrings matched against law text after the
# corpus has been normalised (NFC + casefold). Both plain keywords and
# ``r:``-prefixed regex patterns are normalised the same way, so authors
# write keywords/patterns in their natural Estonian spelling — case and
# combining-mark differences are absorbed by normalisation.
#
# #421: every key is a VERIFIED real EuroVoc descriptor id — the minted
# ``http://eurovoc.europa.eu/{id}`` IRI dereferences to a Publications
# Office concept whose official et/en prefLabels are exactly the labels
# stored here. The pre-#421 table used fabricated codes whose ids meant
# entirely different things in real EuroVoc (e.g. 2446 = "food policy",
# not constitutional law). The old→new id audit trail, per-entry evidence
# URLs, and notes on the handful of nearest-descriptor choices (real
# EuroVoc has no plain "transport"/"energy"/"taxation" descriptors —
# those exist only as microthesauri/domains) live in
# data/eurovoc_domain_mapping.json.
EUROVOC_DOMAINS: dict[str, tuple[str, str, str, list[str]]] = {
    # The tautological generic "Law" domain stays removed (609/615 laws
    # matched it pre-removal — meaningless for a legal ontology).
    "573": (
        "criminal-law", "kriminaalõigus", "criminal law",
        ["karistus", "kuritegu", "süüdi", "kriminaal", "vangist", "väärtegu", "kahtlust"],
    ),
    "523": (
        "civil-law", "tsiviilõigus", "civil law",
        ["tsiviil", "leping", "võlg", "kahju", "nõue", "hagi", "kostja", "hageja"],
    ),
    "517": (
        "administrative-law", "haldusõigus", "administrative law",
        ["haldus", "haldusmenetl", "järelevalve", "ettekirjut", "haldusakt"],
    ),
    "527": (
        "constitutional-law", "riigiõigus", "constitutional law",
        ["põhiseadus", "riigikogu", "president", "valitsus", "vabariig", "riigikohus"],
    ),
    "2836": (
        "consumer-protection", "tarbijakaitse", "consumer protection",
        ["tarbija", "tarbijakaits", "tooteohutus", "garantii"],
    ),
    "75": (
        "competition", "konkurents", "competition",
        ["konkurents", "monopol", "koondum", "turgu valitsev", "riigiabi"],
    ),
    # #421: was "employment/tööhõive" — real EuroVoc has no plain
    # 'employment' descriptor (MT 4406); the keywords target
    # employment-contract law, so descriptor 557 'labour law' was chosen.
    "557": (
        "labour-law", "tööõigus", "labour law",
        ["töö", "tööandja", "töötaja", "palk", "puhkus", "töölepingu", "töösuhte",
         "tööaeg", "töötasu", "koondami"],
    ),
    # #421: 'social protection' is only a microthesaurus (MT 2836) in real
    # EuroVoc; nearest descriptor is 4050 'social security'.
    "4050": (
        "social-security", "sotsiaalkindlustus", "social security",
        ["sotsiaal", "pension", "toetus", "hüvitis", "ravikindlust", "töötuskindlust",
         "puue", "hooldus"],
    ),
    # #421: 'taxation' is only a microthesaurus (MT 2446) in real EuroVoc;
    # nearest descriptor is 1021 'tax system' (et 'maksusüsteem').
    "1021": (
        "tax-system", "maksusüsteem", "tax system",
        ["maks", "tulumaks", "käibemaks", "aktsiis", "maksuhaldu", "maksukohust",
         "sotsiaalmaks", "topeltmaksustamise"],
    ),
    "5050": (
        "budget", "eelarve", "budget",
        ["eelarve", "riigieelarve", "kohalik eelarve"],
    ),
    # #421: TRANSPORT is a EuroVoc top-level domain (48), not a descriptor;
    # nearest descriptor is 2494 'transport policy'.
    "2494": (
        "transport-policy", "transpordipoliitika", "transport policy",
        ["liiklusseadu", "liikluskindlust", "transpordiseadu", "raudteeseadu",
         "lennundus", "meresõit", "maanteeseadu", "teeseadu",
         "autoparkla", "ühistransport", "sadamaseadu",
         "autoveoseadu", "kaubavedu", "reisijatevedu",
         "raskeveokimaks"],
    ),
    "2149": (
        "banking", "pangandus", "banking",
        ["pank", "krediit", "finants", "pangand", "laenu", "hoius", "investeeri",
         "väärtpaber", "fond"],
    ),
    # #421: 'building and public works' is only a microthesaurus (MT 6831);
    # nearest descriptor is 2475 'construction policy'.
    "2475": (
        "construction-policy", "ehituspoliitika", "construction policy",
        ["ehit", "planeeri", "kinnisvara", "hoone", "detailplaneering", "üldplaneering"],
    ),
    "502": (
        "customs", "toll", "customs",
        ["toll", "import", "eksport", "tollikontroll"],
    ),
    "5181": (
        "data-protection", "andmekaitse", "data protection",
        ["andmekaitse", "isikuandm", "privaatsus", "andmetöötl"],
    ),
    "2817": (
        "intellectual-property", "intellektuaalomand", "intellectual property",
        ["autoriõigus", "patent", "kaubamärk", "leiutis", "tööstusdisain", "intellektuaalomand"],
    ),
    "217": (
        "judicial-cooperation", "kohtualane koostöö", "judicial cooperation",
        ["õigusabi", "väljaandmi", "loovutami", "vastastikune tunnustami"],
    ),
    # #421: 'trade' is only a microthesaurus (MT 2016); the keywords target
    # retail/wholesale commerce, so descriptor 10 'domestic trade' was chosen.
    "10": (
        "domestic-trade", "sisekaubandus", "domestic trade",
        ["kauband", "müük", "ost", "jaekauband", "hulgikauband"],
    ),
    "538": (
        "fundamental-rights", "põhiõigused", "fundamental rights",
        ["inimõig", "põhiõig", "vabadus", "võrdsus", "diskrimineeri", "soolise võrdõiguslikkuse"],
    ),
    # #421: ENERGY is a EuroVoc top-level domain (66), not a descriptor;
    # nearest descriptor is 2498 'energy policy'.
    "2498": (
        "energy-policy", "energiapoliitika", "energy policy",
        ["energ", "elektr", "gaas", "küte", "taastuvenerg", "tuumaenerg", "kütus"],
    ),
    # #421: ENVIRONMENT is a EuroVoc top-level domain (52); the keywords are
    # protective-regulation terms, so descriptor 2825 'environmental
    # protection' was chosen.
    "2825": (
        "environmental-protection", "keskkonnakaitse", "environmental protection",
        ["keskkond", "saaste", "jäätm", "loodus", "vesi", "looduskaits",
         "keskkonnamõj", "heitm", "kliima", "bioloogiline mitmekesisus"],
    ),
    # #421: 'communications' is only a microthesaurus (MT 3226); descriptor
    # 2473 'communications policy' covers the telecom+broadcasting+media
    # keyword spread.
    "2473": (
        "communications-policy", "kommunikatsioonipoliitika", "communications policy",
        ["side", "telekommunikatsioon", "ringhääling", "meedia", "elektrooniline side"],
    ),
    # #421: AGRICULTURE, FORESTRY AND FISHERIES is a EuroVoc top-level
    # domain (56); nearest descriptor is 2442 'agricultural policy'.
    "2442": (
        "agricultural-policy", "põllumajanduspoliitika", "agricultural policy",
        ["põllumajandus", "maaelu", "mets", "kalandu", "taimekait", "loomakait",
         "mahepõllumajandus", "sööt"],
    ),
    "5899": (
        "health-care", "tervishoid", "health care",
        ["tervis", "ravim", "haigla", "arst", "meditsiini", "patsien", "tervisekaits",
         "nakkushaig", "vaktsineer"],
    ),
    "668": (
        "education", "haridus", "education",
        ["haridus", "kool", "ülikool", "õpe", "õppekav", "kutseharidus", "teaduskraad",
         "stipendium"],
    ),
    # #421: real EuroVoc has no 'defence'/'national defence' descriptor;
    # nearest descriptor is 2464 'defence policy'.
    "2464": (
        "defence-policy", "kaitsepoliitika", "defence policy",
        ["sõjaväe", "kaitseväe", "riigikaitse", "mobilisatsioon", "rahvusvaheline sõjaline"],
    ),
    "1909": (
        "migration", "migratsioon", "migration",
        ["välismaalane", "pagulane", "varjupaig", "ränne", "kodakondsus", "elamisluba",
         "viisa"],
    ),
    "695": (
        "election", "valimised", "election",
        ["valim", "häälet", "valimisnimekiri", "valimiskom", "kandidaat", "referendum"],
    ),
    # #421: real EuroVoc has no plain 'science' descriptor; nearest
    # descriptor is 2924 'scientific research'.
    "2924": (
        "scientific-research", "teadusuuringud", "scientific research",
        ["teadus", "uurim", "innovatsioon", "teadusasutus"],
    ),
    "3474": (
        "international-affairs", "rahvusvaheline poliitika", "international affairs",
        ["rahvusvaheli", "välislepingu", "konventsioon", "protokoll",
         "ratifitseerimise"],
    ),
    "525": (
        "eu-law", "ELi õigus", "EU law",
        ["euroopa liidu", "euroopa ühendus", "direktiiv", "ülevõtmi"],
    ),
    "68": (
        "local-government", "kohalik omavalitsus", "local government",
        ["kohalik omavalitsus", "vald", "linn", "volikogu", "linnavalitsus",
         "vallavalitsus", "omavalitsusüksus"],
    ),
    "4522": (
        "maritime-transport", "meretransport", "maritime transport",
        ["meresõit", "laev", "sadam", "merendus"],
    ),
    "2258": (
        "political-parties", "poliitilised parteid", "political parties",
        ["erakond", "partei"],
    ),
    "1018": (
        "public-finance", "riigirahandus", "public finance",
        ["riigikass", "avaliku sektori", "riigi raamatupidami"],
    ),
    "3151": (
        "insurance", "kindlustus", "insurance",
        ["kindlust", "liikluskindlust", "elukindlust"],
    ),
    "5188": (
        "information-technology", "infotehnoloogia", "information technology",
        ["infotehnoloog", "infosüsteem", "küberturv", "digitaal", "e-teenus"],
    ),
    # #421: was "public order and safety" — 2162 'public order' (et exactly
    # 'avalik kord') chosen; the safety half is the separate descriptor
    # 'public safety' (4045).
    "2162": (
        "public-order", "avalik kord", "public order",
        ["politsei", "päästeteenius", "hädaolukord", "avalik kord", "turvalisus"],
    ),
    "54": (
        "accounting", "raamatupidamine", "accounting",
        ["raamatupidami", "aruandlus", "audiitor", "majandusaasta aruann"],
    ),
    "4630": (
        "land-use", "maakasutus", "land use",
        ["maakatast", "maareform", "maakorraldus", "kinnistusraamat"],
    ),
    "317": (
        "culture", "kultuur", "culture",
        ["kultuur", "muuseum", "raamatukogu", "kunst", "pärandkaits"],
    ),
    "2862": (
        "advertising", "reklaam", "advertising",
        ["reklaam"],
    ),
    "4505": (
        "air-transport", "õhutransport", "air transport",
        ["lennundus", "lennuk", "lennujaam", "õhusõiduk"],
    ),
}

# Maximum number of EuroVoc domains to assign per law
MAX_DOMAINS_PER_LAW = 5
# Minimum *total* keyword occurrences to assign a domain (default).
MIN_HITS_THRESHOLD = 1
# Per-domain overrides for minimum total keyword occurrences (descriptor id →
# threshold). Counts every match of every keyword, so "töö töö töö" == 3.
MIN_HITS_OVERRIDES: dict[str, int] = {
    "2494": 3,  # transport policy (pre-#421 code "5616"): require 3+ keyword
                # hits to reduce over-classification
}

# Default number of *distinct* keywords from a domain's list that must each
# match at least once. ``1`` keeps the historical behaviour for domains whose
# keyword lists are made of long, distinctive Estonian terms.
MIN_DISTINCT_KEYWORDS_DEFAULT = 1
# Per-domain overrides for the distinct-keyword gate. These are the domains
# whose keyword lists contain short Estonian stems (≤4 chars, or substrings of
# common unrelated words) that the original substring-matching review (#153)
# flagged as over-matching — e.g. 'töö' fires inside 'töötukassa', 'maks'
# inside 'maksimaalne', 'side' inside 'asjasse', 'pank'/'fond' inside
# placenames, 'vald'/'linn' inside 'väljakuulutamine'/'tallinn', 'kool' inside
# 'koolitus', 'arst' inside 'tarvastu', 'ost' inside 'postitus'. Requiring TWO
# distinct keyword hits forces a corroborating term before the domain is
# assigned, trading a little recall for materially better precision on these
# noisy domains. Domains built from only long/distinctive keywords are left at
# the default (1); ``2862`` advertising has a single keyword ('reklaam') and so
# must stay at 1 (it could never reach 2).
#
# Plain keywords are matched as raw substrings (no word boundary), so a short
# stem fires mid-word in an unrelated term. Review #275 verified more of
# these: 'nõue' (civil-law 523) fires inside 'nõuetele'/'nõuetekohastele'
# ("requirements", ubiquitous in technical acts); 'kunst' (culture 317)
# inside 'kunstlik' ("artificial"); 'vesi' (environmental-protection 2825)
# inside 'vesinik' ("hydrogen"); and 'hagi'/'võlg' (523), 'toll' (502), 'laev'
# (maritime 4522), 'ehit' (construction 2475), 'puue' (social-security 4050)
# are all short substring-prone stems. Bumping these domains to a
# distinct-gate of 2 forces a second, corroborating keyword before the domain
# is assigned, so a lone mid-word substring hit no longer tags the act. Every
# listed domain has ≥2 keywords, so the gate is reachable.
#
# NB (#421): the keys below were re-keyed from the pre-#421 fabricated codes
# to the verified EuroVoc descriptor ids (data/eurovoc_domain_mapping.json
# has the old→new table); the tuned thresholds themselves are unchanged.
MIN_DISTINCT_KEYWORDS_OVERRIDES: dict[str, int] = {
    "557": 2,   # labour-law (was employment "3606") — 'töö', 'palk'
    "2473": 2,  # communications-policy (was "2821") — 'side'
    "1021": 2,  # tax-system (was taxation "5206") — 'maks'
    "2149": 2,  # banking (was "5231") — 'pank', 'fond', 'laenu'
    "68": 2,    # local-government (was "1221") — 'vald', 'linn'
    "668": 2,   # education (was "5636") — 'kool', 'õpe'
    "5899": 2,  # health-care (was health "5631") — 'arst'
    "2498": 2,  # energy-policy (was "5606") — 'gaas', 'küte', 'energ'
    "2442": 2,  # agricultural-policy (was "5621") — 'mets', 'sööt'
    "10": 2,    # domestic-trade (was trade "5226") — 'ost', 'müük'
    "2258": 2,  # political-parties (was "2031") — 'partei'
    # --- #275: short substring-prone stems that misfire mid-word ---
    "523": 2,   # civil-law (was "2431") — 'nõue' (→ 'nõuetele'), 'hagi', 'võlg'
    "317": 2,   # culture (was "3221") — 'kunst' (→ 'kunstlik')
    "502": 2,   # customs (was "5216") — 'toll'
    "2825": 2,  # environmental-protection (was environment "5611") — 'vesi' (→ 'vesinik')
    "4522": 2,  # maritime-transport (was "4421") — 'laev'
    "2475": 2,  # construction-policy (was building "6411") — 'ehit'
    "4050": 2,  # social-security (was social-protection "3611") — 'puue'
    # --- #331 / #360: single-keyword over-matches on long but
    # context-ambiguous terms (not short stems). 3474 (international-affairs,
    # pre-#421 code "0411") fires on a lone 'protokoll' inside domestic
    # 'protokollitakse' (meeting minutes / court transcript, #331) and on a
    # lone 'rahvusvaheli' in 234 purely domestic functional laws that merely
    # cite an international standard once (#360); 2836 (consumer-protection,
    # pre-#421 code "2806") fires on a lone 'tarbija' in 93 district-heating
    # (kaugkütt) ordinances where it means "heat-subscriber" (#360).
    # Requiring a second corroborating keyword reconciles both
    # international-affairs vectors into one gate and clears the
    # consumer-protection noise. 3474 has 5 keywords and 2836 has 4, so the
    # gate is reachable.
    "3474": 2,  # international-affairs — 'protokoll' (#331), 'rahvusvaheli' (#360)
    "2836": 2,  # consumer-protection — 'tarbija' on kaugkütt regs (#360)
}


class PeepMetadataParseError(ValueError):
    """Raised when a peep file cannot be parsed for act metadata."""


def save_json(filepath: Path, doc: dict | list):
    """Write a JSON document to disk with UTF-8 encoding."""
    _save_json(filepath, doc)


def load_json(filepath: Path) -> dict:
    """Load a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def read_act_metadata_from_peep(path: Path) -> dict | None:
    """Pull act-level metadata from a peep file without consulting
    INDEX.json. Returns dict with @id, title, source — or None if no
    act-typed node is present.

    Recognises any node typed as one of the concrete legal-act
    classes. Importantly, ``owl:Ontology`` ALONE is NOT enough: the
    Layer 1 KOV registry files (``municipalities_peep.json``,
    ``issuers_kov_peep.json``) use ``owl:Ontology`` on their top
    metadata node, but they are not legal acts and must not get
    EuroVoc tags. The node must also carry one of the concrete act
    types.
    """
    ACT_TYPES = {
        "estleg:Act", "estleg:Law",
        "estleg:NationalRegulation", "estleg:GovernmentRegulation",
        "estleg:MinisterialRegulation", "estleg:MunicipalRegulation",
    }
    # Registry files identified by stable IRI prefixes — even if Layer 2
    # later stamps them with new types, these map files remain
    # ineligible for EuroVoc. Source: estleg_common.AGGREGATE_REGISTRY_PREFIXES.
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (ValueError, OSError) as exc:
        raise PeepMetadataParseError(f"{path}: {type(exc).__name__}: {exc}") from exc
    for node in doc.get("@graph", []):
        node_id = node.get("@id", "")
        if any(node_id.startswith(p) for p in AGGREGATE_REGISTRY_PREFIXES):
            return None  # registry map — not classifiable
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if not ACT_TYPES.intersection(types):
            continue
        # rdfs:label / dc:source may be plain strings or
        # {"@value": ..., "@language": "et"} objects depending on the
        # generator; jsonld_text normalises both to a plain string.
        title = jsonld_text(node.get("rdfs:label", ""))
        source = jsonld_text(node.get("dc:source", "")) or title
        return {
            "@id": node_id,
            "title": title,
            "source": source,
        }
    return None


def extract_text_from_law(data: dict) -> str:
    """
    Extract all searchable text from a law JSON-LD file.
    Combines rdfs:label, estleg:sourceAct, and estleg:summary fields.

    Each of these fields may arrive as a plain string, a
    ``{"@value": ..., "@language": "et"}`` value object, or a list of
    either, depending on whether the corpus came straight off a
    generator or through a normalisation pass. ``jsonld_text`` collapses
    all of those shapes to a plain string so the keyword search blob is
    text, never a stringified dict.
    """
    parts: list[str] = []

    for node in data.get("@graph", []):
        for key in (
            "rdfs:label",
            "estleg:sourceAct",
            "estleg:summary",
            "skos:prefLabel",
            "dc:description",
        ):
            text = jsonld_text(node.get(key, ""))
            if text:
                parts.append(text)

    return unicodedata.normalize("NFC", " ".join(parts)).casefold()


def classify_text(
    text: str,
) -> list[tuple[str, str, str, str, int, list[str]]]:
    """Classify ``text`` against EuroVoc domains by keyword matching.

    Returns a list of
    ``(code, slug, label_et, label_en, hit_count, matched_keywords)``
    sorted by ``hit_count`` descending then ``code``. ``hit_count`` is the
    total number of keyword occurrences; ``matched_keywords`` is the list of
    distinct keywords (in the domain's declared order) that each matched at
    least once — used both for the precision gate and the review sample.

    A domain is assigned only when BOTH gates pass:
      * total occurrences ≥ ``MIN_HITS_OVERRIDES[code]`` (default 1), and
      * distinct matched keywords ≥ ``MIN_DISTINCT_KEYWORDS_OVERRIDES[code]``
        (default 1).
    """
    results: list[tuple[str, str, str, str, int, list[str]]] = []

    for code, (slug, label_et, label_en, keywords) in EUROVOC_DOMAINS.items():
        hit_count = 0
        matched_keywords: list[str] = []
        for kw in keywords:
            if kw.startswith("r:"):
                # Regex pattern (e.g. for word-boundary-aware matching).
                # Normalise the pattern with NFC + casefold so it lines up
                # with the corpus, which extract_text_from_law already
                # normalised the same way.
                pattern = unicodedata.normalize("NFC", kw[2:]).casefold()
                kw_hits = len(re.findall(pattern, text))
            else:
                normalized_kw = unicodedata.normalize("NFC", kw).casefold()
                kw_hits = len(re.findall(re.escape(normalized_kw), text))
            if kw_hits:
                hit_count += kw_hits
                matched_keywords.append(kw)

        hits_threshold = MIN_HITS_OVERRIDES.get(code, MIN_HITS_THRESHOLD)
        distinct_threshold = MIN_DISTINCT_KEYWORDS_OVERRIDES.get(
            code, MIN_DISTINCT_KEYWORDS_DEFAULT
        )
        if hit_count >= hits_threshold and len(matched_keywords) >= distinct_threshold:
            results.append(
                (code, slug, label_et, label_en, hit_count, matched_keywords)
            )

    # Sort by hit count descending, then by code
    results.sort(key=lambda r: (-r[4], r[0]))

    # Limit to top domains
    return results[:MAX_DOMAINS_PER_LAW]


def update_law_file_eurovoc(
    filepath: Path,
    domains: list[tuple[str, str, str, str, int, list[str]]],
) -> bool:
    """
    Add dcterms:subject with EuroVoc concept URIs to a law JSON-LD file.
    Targets the owl:Ontology metadata node.
    Returns True if the file was modified.
    """
    try:
        data = load_json(filepath)
    except Exception as e:
        print(f"    ERROR loading {filepath.name}: {e}")
        return False

    # Ensure dcterms is in context
    ctx = data.get("@context", {})
    if "dcterms" not in ctx:
        ctx["dcterms"] = "http://purl.org/dc/terms/"
        data["@context"] = ctx

    graph = data.get("@graph", [])

    # Find the ontology metadata node
    target_node = None
    for node in graph:
        types = node.get("@type", [])
        if "owl:Ontology" in types:
            target_node = node
            break

    if target_node is None and graph:
        target_node = graph[0]

    if target_node is None:
        return False

    # Build subject references — bare IRI refs only (SHACL expects sh:nodeKind sh:IRI)
    subject_refs = []
    for domain in domains:
        code = domain[0]
        subject_refs.append({
            "@id": f"{EUROVOC_URI_BASE}{code}",
        })

    # Check for existing dcterms:subject (non-EuroVoc entries preserved)
    existing = target_node.get("dcterms:subject", [])
    if isinstance(existing, dict):
        existing = [existing]

    # Normalize existing refs to bare IRIs (strip any embedded labels)
    existing_clean = []
    for ref in existing:
        if isinstance(ref, dict) and "@id" in ref:
            existing_clean.append({"@id": ref["@id"]})
        elif isinstance(ref, dict):
            existing_clean.append(ref)

    existing_ids = {ref.get("@id", "") for ref in existing_clean if isinstance(ref, dict)}

    new_refs = [ref for ref in subject_refs if ref["@id"] not in existing_ids]
    if not new_refs:
        return False

    all_refs = existing_clean + new_refs
    target_node["dcterms:subject"] = all_refs

    save_json(filepath, data)
    return True


def clear_eurovoc_subjects_from_file(filepath: Path) -> bool:
    """
    Remove all dcterms:subject entries whose @id starts with the EuroVoc URI base.
    Preserves any non-EuroVoc subjects. Returns True if the file was modified.
    """
    try:
        data = load_json(filepath)
    except Exception:
        return False

    modified = False
    for node in data.get("@graph", []):
        existing = node.get("dcterms:subject")
        if existing is None:
            continue

        if isinstance(existing, dict):
            existing = [existing]
        elif not isinstance(existing, list):
            continue

        kept = [
            ref for ref in existing
            if not (isinstance(ref, dict) and ref.get("@id", "").startswith(EUROVOC_URI_BASE))
        ]

        if len(kept) != len(existing):
            modified = True
            if not kept:
                del node["dcterms:subject"]
            else:
                node["dcterms:subject"] = kept

    if modified:
        save_json(filepath, data)
    return modified


def _display_path(path: Path) -> str:
    """Repo-relative display string, falling back to the absolute path.

    ``Path.relative_to(REPO_ROOT)`` raises when ``KRR_DIR`` has been
    re-pointed outside the repo (e.g. by tests), so guard against it.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _write_classification_sample(
    pool: list[dict],
    sample_size: int,
    seed: int | None,
) -> Path:
    """Write a random precision-review sample of (act, subjects, keywords).

    ``pool`` is the list of every classified act this run produced (each item
    is ``{"act": ..., "file": ..., "assigned_subjects": [...],
    "matched_keywords": {code: [...]}}``). We sample ``sample_size`` of them
    (or all of them if the pool is smaller) and write the result to
    ``krr_outputs/reports/eurovoc_classification_sample.json`` so a human can
    eyeball whether the high-volume domains are meaningful.
    """
    rng = random.Random(seed)
    chosen = pool if sample_size >= len(pool) else rng.sample(pool, sample_size)
    chosen = sorted(chosen, key=lambda r: r["act"])
    sample_doc = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "method": "keyword-matching",
        "classification_level": "act",
        "purpose": (
            "Random sample of act → EuroVoc subject assignments for manual "
            "precision review. Each entry lists the matched keywords per "
            "domain so a reviewer can judge whether the assignment is "
            "meaningful or keyword noise."
        ),
        "random_seed": seed,
        "population_size": len(pool),
        "sample_size": len(chosen),
        "samples": chosen,
    }
    out_dir = KRR_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "eurovoc_classification_sample.json"
    save_json(out_path, sample_doc)
    return out_path


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Classify Estonian acts with EuroVoc subject taxonomy.",
    )
    parser.add_argument(
        "--emit-sample",
        type=int,
        metavar="N",
        default=0,
        help=(
            "After classifying, write a random N-act precision-review sample "
            "to krr_outputs/reports/eurovoc_classification_sample.json "
            "(0 = disabled)."
        ),
    )
    parser.add_argument(
        "--sample-seed",
        type=int,
        default=None,
        help="Optional RNG seed for --emit-sample (for reproducible samples).",
    )
    args = parser.parse_args(argv)

    print("=" * 60)
    print("Classify Estonian laws with EuroVoc subject taxonomy")
    print(f"EuroVoc domains defined: {len(EUROVOC_DOMAINS)}")
    print("=" * 60)

    _start = time.perf_counter()

    # --- Step 0: Clear existing EuroVoc subjects ---
    print("\n--- Clearing existing EuroVoc subjects ---")
    cleared_count = 0
    for peep_file in iter_peep_files():
        if clear_eurovoc_subjects_from_file(peep_file):
            cleared_count += 1
    print(f"  Cleared EuroVoc subjects from {cleared_count} files")

    # --- Step 1: Discover peep files ---
    print("\n--- Discovering peep files ---")
    peep_files = iter_peep_files()
    _kov_files = [f for f in peep_files if "regulations/kov" in str(f)]
    print(f"  Discovered {len(peep_files)} peep files")
    laws_meta: list[dict] = []
    # Coverage skip-reasons for files dropped at metadata-read time
    _skip_reasons: dict[str, int] = {}
    for f in peep_files:
        try:
            meta = read_act_metadata_from_peep(f)
        except PeepMetadataParseError as exc:
            _skip_reasons["parse_error"] = _skip_reasons.get("parse_error", 0) + 1
            print(f"    ERROR reading metadata: {exc}")
            continue
        if meta is None:
            _skip_reasons["no_act_node"] = _skip_reasons.get("no_act_node", 0) + 1
            continue
        # Use the @id as the canonical "name" for the report — for
        # backwards-compat with the report shape, also store a
        # human-readable "name" derived from rdfs:label or @id.
        meta["path"] = f
        meta["name"] = meta.get("title") or meta.get("@id") or f.stem
        laws_meta.append(meta)
    print(f"  {len(laws_meta)} have a recognized act node "
          f"(skipped {len(peep_files) - len(laws_meta)} non-act files)")

    # --- Step 2: Classify each act ---
    print("\n--- Classifying acts ---")

    classifications: list[dict] = []
    domain_counts: dict[str, int] = {}  # code → number of acts assigned
    # Per-act sample rows: built for every classified act regardless of the
    # --emit-sample flag, so a sample can be written cheaply at the end.
    sample_pool: list[dict] = []
    files_updated = 0
    files_skipped = 0
    files_error = 0
    unclassified: list[str] = []

    # Coverage counters
    _files_processed = 0
    _files_processed_kov = 0
    _files_with_output = 0
    _files_with_output_kov = 0
    _triples = 0
    _triples_kov = 0
    _fallback_hits = 0
    _unresolved = 0
    _failures: list[str] = []

    for i, meta in enumerate(laws_meta):
        name = meta["name"]
        path = meta["path"]
        is_kov = "regulations/kov" in str(path)

        try:
            try:
                data = load_json(path)
            except (json.JSONDecodeError, OSError) as e:
                print(f"    ERROR loading {path.name}: {e}")
                files_error += 1
                files_skipped += 1
                _skip_reasons["json_decode_error"] = (
                    _skip_reasons.get("json_decode_error", 0) + 1
                )
                continue
            if data is None:
                files_error += 1
                files_skipped += 1
                _skip_reasons["json_decode_error"] = (
                    _skip_reasons.get("json_decode_error", 0) + 1
                )
                continue

            # Keep extract_text_from_law(data) — it walks the @graph and
            # gathers rdfs:label, dc:source, summaries, defined terms.
            # The new metadata helper is only for path/identity
            # bookkeeping, not classification text.
            text = extract_text_from_law(data)
            domains = classify_text(text)

            _files_processed += 1
            if is_kov:
                _files_processed_kov += 1

            if not domains:
                unclassified.append(name)
                files_skipped += 1
                _skip_reasons["unclassified"] = (
                    _skip_reasons.get("unclassified", 0) + 1
                )
                continue

            rel_file = (
                str(path.relative_to(KRR_DIR))
                if KRR_DIR in path.parents
                else str(path)
            )
            entry = {
                "law_name": name,
                "file": rel_file,
                "domains": [
                    {
                        "code": code,
                        "slug": slug,
                        "label_et": label_et,
                        "label_en": label_en,
                        "eurovoc_uri": f"{EUROVOC_URI_BASE}{code}",
                        "keyword_hits": hits,
                        "matched_keywords": matched_kws,
                    }
                    for code, slug, label_et, label_en, hits, matched_kws in domains
                ],
            }
            classifications.append(entry)
            sample_pool.append({
                "act": meta.get("@id") or name,
                "act_name": name,
                "file": rel_file,
                "assigned_subjects": [
                    {
                        "code": code,
                        "eurovoc_uri": f"{EUROVOC_URI_BASE}{code}",
                        "slug": slug,
                    }
                    for code, slug, _label_et, _label_en, _hits, _kws in domains
                ],
                "matched_keywords": {
                    code: matched_kws
                    for code, _slug, _label_et, _label_en, _hits, matched_kws in domains
                },
            })

            for code, *_ in domains:
                domain_counts[code] = domain_counts.get(code, 0) + 1

            # Write back to the source path (not KRR_DIR / filename) — the
            # KOV files live under regulations/kov/<issuer>/, state regs
            # under regulations/riik/, and laws at root. The helper takes
            # the real path so all three cases work.
            if update_law_file_eurovoc(path, domains):
                files_updated += 1
                _files_with_output += 1
                if is_kov:
                    _files_with_output_kov += 1
                # Each successful update adds the selected domains as
                # subject IRIs on the act node. Count one triple per
                # domain entry assigned.
                _triples += len(domains)
                if is_kov:
                    _triples_kov += len(domains)
        except Exception as exc:  # noqa: BLE001
            _failures.append(f"{path.name}: {exc!r}")

        if (i + 1) % 500 == 0:
            print(f"  Processed {i + 1}/{len(laws_meta)} acts "
                  f"({files_updated} files updated)...")

    print(f"  Processed all {len(laws_meta)} acts")

    # --- Step 3: Generate domain statistics ---
    # Surface a per-domain row for EVERY defined domain (including domains that
    # got 0 acts) so coverage skew — and the effect of the precision gates — is
    # visible in the report. Sorted by act count descending then code.
    print("\n--- Domain statistics ---")
    domain_stats: list[dict] = []
    for code, (slug, label_et, label_en, keywords) in sorted(
        EUROVOC_DOMAINS.items(),
        key=lambda kv: (-domain_counts.get(kv[0], 0), kv[0]),
    ):
        count = domain_counts.get(code, 0)
        domain_stats.append({
            "code": code,
            "slug": slug,
            "label_et": label_et,
            "label_en": label_en,
            "eurovoc_uri": f"{EUROVOC_URI_BASE}{code}",
            "laws_count": count,
            "keyword_count": len(keywords),
            "min_total_hits": MIN_HITS_OVERRIDES.get(code, MIN_HITS_THRESHOLD),
            "min_distinct_keywords": MIN_DISTINCT_KEYWORDS_OVERRIDES.get(
                code, MIN_DISTINCT_KEYWORDS_DEFAULT
            ),
        })
        print(f"    {code} {label_en:35s} ({label_et:30s}): {count:4d} laws")

    # --- Optional precision-review sample ---
    sample_path: Path | None = None
    if args.emit_sample and args.emit_sample > 0:
        sample_path = _write_classification_sample(
            sample_pool, args.emit_sample, args.sample_seed
        )
        print(f"  Wrote precision-review sample: {_display_path(sample_path)}")

    # --- Step 4: Generate report ---
    print("\n--- Generating classification report ---")
    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "method": "keyword-matching",
        "classification_level": "act",
        "quality_evaluation": {
            "status": "tooling_available",
            "note": (
                "Keyword matches are candidate act-level EuroVoc subjects. "
                "Domain assignments are gated by a total-hit threshold and a "
                "distinct-keyword threshold (see min_total_hits / "
                "min_distinct_keywords per domain in domain_statistics). Run "
                "`python3 scripts/classify_eurovoc.py --emit-sample N` to "
                "produce krr_outputs/reports/eurovoc_classification_sample.json "
                "for manual precision review."
            ),
            "precision_gates": {
                "min_total_hits_default": MIN_HITS_THRESHOLD,
                "min_distinct_keywords_default": MIN_DISTINCT_KEYWORDS_DEFAULT,
                "min_total_hits_overrides": dict(sorted(MIN_HITS_OVERRIDES.items())),
                "min_distinct_keywords_overrides": dict(
                    sorted(MIN_DISTINCT_KEYWORDS_OVERRIDES.items())
                ),
            },
            "sample_emitted": (
                _display_path(sample_path) if sample_path is not None else None
            ),
        },
        "eurovoc_domains_defined": len(EUROVOC_DOMAINS),
        "total_laws_processed": len(laws_meta),
        "total_classified": len(classifications),
        "total_unclassified": len(unclassified),
        "files_updated": files_updated,
        "files_error": files_error,
        "domain_statistics": domain_stats,
        "classifications": sorted(classifications, key=lambda c: c["law_name"]),
        "unclassified_laws": sorted(unclassified),
    }

    report_path = KRR_DIR / "eurovoc_classification.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("EuroVoc classification complete!")
    print(f"  Acts processed:       {len(laws_meta)}")
    print(f"  Laws classified:      {len(classifications)}")
    print(f"  Laws unclassified:    {len(unclassified)}")
    print(f"  Law files updated:    {files_updated}")
    print(f"  File errors:          {files_error}")
    print(f"  Domains used:         {len(domain_counts)}/{len(EUROVOC_DOMAINS)}")
    print(f"\nOutput: {_display_path(report_path)}")
    print("=" * 60)

    # ---------- coverage report ----------
    _wall, _rate, _peak_mb = measure_runtime(_start, _files_processed)
    write_coverage_report(
        CoverageReport(
            pipeline="classify_eurovoc",
            run_timestamp=PINNED_RUN_TIMESTAMP,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(peep_files),
            input_files_kov=len(_kov_files),
            files_processed=_files_processed,
            files_processed_kov=_files_processed_kov,
            files_with_output=_files_with_output,
            files_with_output_kov=_files_with_output_kov,
            files_skipped=files_skipped,
            skip_reasons=_skip_reasons,
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            fallback_hits=_fallback_hits,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        REPO_ROOT / "krr_outputs" / "reports" / "kov"
        / "classify_eurovoc_coverage.json",
    )


if __name__ == "__main__":
    main()
