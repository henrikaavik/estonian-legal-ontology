#!/usr/bin/env python3
"""
Extract which institutions are responsible for what from law text.

Scans estleg:summary text in every provision for references to Estonian
state institutions and competence-assigning language, then creates
estleg:Institution nodes and links provisions to competent authorities.

Outputs:
  - krr_outputs/institutions/  (per-institution JSON-LD files)
  - krr_outputs/institutional_competence_report.json
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from estleg_common import iter_peep_files
from extract_sanctions import _find_act_node
from extract_cross_references import build_issuer_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
INST_DIR = KRR_DIR / "institutions"
INST_DIR.mkdir(parents=True, exist_ok=True)
# INSTIT_DIR is the monkeypatch-friendly alias used by tests (and Task 4+).
# main() writes institution files via INSTIT_DIR so tests can redirect to tmp_path.
INSTIT_DIR = INST_DIR


def _id_ref(value: object) -> str | None:
    """Unwrap a JSON-LD {"@id": "..."} object to a plain IRI string.
    Returns the string directly if *value* is already a string, or
    None when *value* is None or lacks an "@id" key.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("@id")
    return None


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


def save_json(filepath: Path, doc: dict) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_json(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"  WARN: cannot load {filepath.name}: {exc}")
        return None


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    s = value.replace(" ", "_").replace("-", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s[:80] or "Unknown"


def normalize_iri_suffix(raw_suffix: str) -> str:
    """
    Normalize an IRI suffix to lowercase convention (matching institution
    definition files) and map known abbreviations/inflections to canonical forms.
    """
    # Map known abbreviations to canonical full-name suffixes (lowercase)
    ABBREVIATION_MAP: dict[str, str] = {
        "mta": "maksu__ja_tolliamet",
        "ppa": "politsei__ja_piirivalveamet",
        "ttja": "tarbijakaitse_ja_tehnilise_jarelevalve_amet",
        "harno": "haridus__ja_noorteamet",
    }

    # Map known Estonian inflected forms to nominative (lowercase)
    INFLECTION_MAP: dict[str, str] = {
        "kohaliku_omavalitsus": "kohalik_omavalitsus",
        "kohaliku_omavalitsuse": "kohalik_omavalitsus",
        "kohalik_omavalitsuse": "kohalik_omavalitsus",
        # Typo variant: missing 'e' in Andmekaitse
        "andmekaitsinspektsioon": "andmekaitseinspektsioon",
    }

    lower = raw_suffix.lower()

    # Check abbreviation map first
    if lower in ABBREVIATION_MAP:
        return ABBREVIATION_MAP[lower]

    # Check inflection map
    if lower in INFLECTION_MAP:
        return INFLECTION_MAP[lower]

    # Default: lowercase the entire suffix
    return lower


# owl:sameAs aliases: abbreviation IRI → canonical IRI (both lowercase)
# These are emitted as owl:sameAs triples in institution definition files so
# that consumers can resolve either form.
SAMEAS_ALIASES: dict[str, str] = {
    "mta": "maksu__ja_tolliamet",
    "ppa": "politsei__ja_piirivalveamet",
    "ttja": "tarbijakaitse_ja_tehnilise_jarelevalve_amet",
    "harno": "haridus__ja_noorteamet",
}


_CANONICAL_BODY_SLUGS = frozenset({
    "linnavolikogu", "vallavolikogu", "alevivolikogu",
    "linnavalitsus", "vallavalitsus",
})


def _canonical_body_slug(matched: str) -> str | None:
    """Strip Estonian case suffixes from a KOV body-word match
    and return the canonical slug (linnavolikogu, vallavolikogu,
    alevivolikogu, linnavalitsus, vallavalitsus) or None if the
    match doesn't fit one of the five reachable stems.
    """
    s = matched.lower()
    for suffix in ("esse", "ele", "sse", "es", "le", "se", "s", "t", "e"):
        if s.endswith(suffix) and len(s) > len(suffix) + 4:
            stripped = s[: -len(suffix)]
            if stripped.endswith(("volikogu", "valitsus")):
                s = stripped
                break
    if s in _CANONICAL_BODY_SLUGS:
        return s
    return None


# ---------- institution catalogue ----------

# Named institutions: (canonical name, IRI suffix, institution type)
# IRI suffixes are stored in raw form here; normalize_iri_suffix() is applied
# when building the actual IRI.
NAMED_INSTITUTIONS: list[tuple[str, str, str]] = [
    # Government / Parliament / President
    ("Vabariigi Valitsus", "vabariigivalitsus", "government"),
    ("Riigikogu", "riigikogu", "parliament"),
    ("Vabariigi President", "vabariigipresident", "head_of_state"),
    # Specific agencies (order matters: longer names first)
    # Abbreviations map to canonical full-name suffixes via normalize_iri_suffix()
    ("Andmekaitse Inspektsioon", "andmekaitseinspektsioon", "agency"),
    ("Tarbijakaitse ja Tehnilise Järelevalve Amet", "ttja", "agency"),
    ("Maksu- ja Tolliamet", "maksu__ja_tolliamet", "agency"),
    ("Politsei- ja Piirivalveamet", "politsei__ja_piirivalveamet", "agency"),
    ("Keskkonnaamet", "keskkonnaamet", "agency"),
    ("Terviseamet", "terviseamet", "agency"),
    ("Haridus- ja Noorteamet", "haridus__ja_noorteamet", "agency"),
    # Abbreviation-only matches (text may say "MTA" or "PPA" without full name)
    ("MTA", "mta", "agency"),
    ("PPA", "ppa", "agency"),
    # Courts
    ("Riigikohus", "riigikohus", "court"),
    ("ringkonnakohus", "ringkonnakohus", "court"),
    ("halduskohus", "halduskohus", "court"),
    ("maakohus", "maakohus", "court"),
]

# Generic patterns: regex → (label template, IRI template, inst type)
GENERIC_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # Ministries: "Xministeerium" or "Xminister"
    (re.compile(r"\b([A-ZÄÖÜÕŠŽ][a-zäöüõšž]+(?:\s*-\s*ja\s+[A-ZÄÖÜÕŠŽ]?[a-zäöüõšž]+)*ministeerium)\b", re.UNICODE),
     "ministry", "ministry"),
    (re.compile(r"\b([a-zäöüõšž]+minister)\b", re.IGNORECASE | re.UNICODE),
     "ministry", "ministry"),
    # Agencies: "Xamet", "Xinspektsioon"
    (re.compile(r"\b([A-ZÄÖÜÕŠŽ][a-zäöüõšž]+(?:\s*-\s*ja\s+[A-ZÄÖÜÕŠŽ]?[a-zäöüõšž]+)*amet)\b", re.UNICODE),
     "agency", "agency"),
    (re.compile(r"\b([A-ZÄÖÜÕŠŽ][a-zäöüõšž]+inspektsioon)\b", re.UNICODE),
     "agency", "agency"),
    # Courts (generic)
    (re.compile(r"\b(kohus)\b", re.IGNORECASE), "court", "court"),
    # Local government
    (re.compile(r"\bkohalik(?:u)?\s+omavalitsus(?:e)?\b", re.IGNORECASE),
     "local_government", "local_government"),
    (re.compile(r"\b(?:linna|valla|alevi)volikogu(?:le|sse|s)?\b", re.IGNORECASE),
     "local_government_council", "local_government_body"),
    (re.compile(r"\b(?:linna|valla)valitsus(?:e(?:le|sse)?|es|se|t)?\b", re.IGNORECASE),
     "local_government_executive", "local_government_body"),
    (re.compile(r"\b(vald|linn)\b", re.IGNORECASE),
     "local_government", "local_government"),
]

# ---------- competence-assigning patterns ----------

COMPETENCE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"järelevalvet\s+teostab", re.IGNORECASE), "supervision"),
    (re.compile(r"teostab\s+järelevalvet", re.IGNORECASE), "supervision"),
    (re.compile(r"järelevalve", re.IGNORECASE), "supervision"),
    (re.compile(r"\bon\s+pädev\b", re.IGNORECASE), "general"),
    (re.compile(r"\bannab\s+loa\b", re.IGNORECASE), "licensing"),
    (re.compile(r"\bväljastab\s+luba\b", re.IGNORECASE), "licensing"),
    (re.compile(r"\bkehtestab\b", re.IGNORECASE), "regulation"),
    (re.compile(r"\bkontrollib\b", re.IGNORECASE), "enforcement"),
    (re.compile(r"\bkorraldab\b", re.IGNORECASE), "enforcement"),
    (re.compile(r"\bteostab\b", re.IGNORECASE), "enforcement"),
]


def detect_institutions(text: str) -> list[tuple[str, str, str]]:
    """
    Return list of (canonical_name, normalized_iri_suffix, inst_type) found in *text*.
    Named institutions are checked first; generic patterns fill in the rest.
    All IRI suffixes are normalized to lowercase convention via normalize_iri_suffix().
    """
    found: dict[str, tuple[str, str, str]] = {}
    text_lower = text.lower()

    # 1. Named institutions (first match for a given norm_suffix wins,
    #    so full-name entries should precede abbreviation-only entries
    #    in NAMED_INSTITUTIONS to keep the better canonical label)
    for name, raw_suffix, itype in NAMED_INSTITUTIONS:
        if name.lower() in text_lower:
            norm_suffix = normalize_iri_suffix(raw_suffix)
            if norm_suffix not in found:
                found[norm_suffix] = (name, norm_suffix, itype)

    # 2. Generic patterns (only if not already captured by a named entry)
    for pat, default_label, itype in GENERIC_PATTERNS:
        for m in pat.finditer(text):
            matched = m.group(1) if m.lastindex else m.group(0)
            raw_key = sanitize_id(matched)
            norm_key = normalize_iri_suffix(raw_key)
            if norm_key and norm_key not in found:
                # Skip if this is just the generic "kohus" and we already have
                # a specific court
                if norm_key == "kohus" and any(
                    t == "court" for _, _, t in found.values()
                ):
                    continue
                # Use canonical label for local government (avoid inflected forms)
                if norm_key == "kohalik_omavalitsus":
                    matched = "Kohalik omavalitsus"
                found[norm_key] = (matched, norm_key, itype)

    return list(found.values())


def detect_competence_type(text: str) -> str:
    """Return the most specific competence type found in *text*."""
    for pat, ctype in COMPETENCE_PATTERNS:
        if pat.search(text):
            return ctype
    return "general"


def _swap_issuer_body_suffix(
    source_issuer: str, target_body_slug: str
) -> str | None:
    """Given an Issuer @id like 'estleg:Issuer_abja_vallavolikogu'
    and a target body slug like 'vallavalitsus', return the paired
    Issuer @id 'estleg:Issuer_abja_vallavalitsus'.

    Returns None when:
    - The source IRI doesn't match the expected
      `Issuer_<place>_<body>` shape.
    - The source body and target body belong to DIFFERENT
      municipal families (linna* / valla* / alevi* are mutually
      exclusive). A linnavalitsus source must NOT pair with
      vallavolikogu target — same historical-conflation problem
      Path 1's suffix check rejects.
    """
    if not source_issuer.startswith("estleg:Issuer_"):
        return None
    suffix = source_issuer[len("estleg:Issuer_"):]
    source_body = None
    place = None
    for body in ("vallavolikogu", "vallavalitsus", "linnavolikogu",
                  "linnavalitsus", "alevivolikogu"):
        if suffix.endswith("_" + body):
            source_body = body
            place = suffix[: -len("_" + body)]
            break
    if source_body is None or place is None:
        return None

    family_prefixes = ("linna", "valla", "alevi")
    source_family = next(
        (p for p in family_prefixes if source_body.startswith(p)), None)
    target_family = next(
        (p for p in family_prefixes if target_body_slug.startswith(p)), None)
    if source_family is None or target_family is None:
        return None
    if source_family != target_family:
        return None

    return f"estleg:Issuer_{place}_{target_body_slug}"


def _resolve_kov_authority(
    body_slug: str,
    source_municipality: str | None,
    source_issuer: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
) -> str | None:
    """Resolve a KOV body-word detection to an Issuer @id.

    Three priority paths (in order):
      1. Same body type AND slug suffix match source_issuer →
         return source_issuer.
      2. Opposite body type → derive paired issuer slug; return
         it if registered AND in same municipality.
      3. Path 3 fallback: registry-wide unique match in
         source_municipality. Reached ONLY when source_issuer is
         missing, unknown to the registry, or has municipality
         inconsistent with the act's stated enactedByMunicipality.

    When source_issuer is known and consistent (Path 1+2
    authoritative), Path 3 is NOT consulted — abstain instead.

    Args:
        body_slug: Canonical body slug from _canonical_body_slug().
            Must be one of: linnavolikogu, vallavolikogu, alevivolikogu,
            linnavalitsus, vallavalitsus. Inflected forms are not
            accepted (body_type_map returns None → resolver abstains).
        source_municipality: Plain-string IRI of estleg:enactedByMunicipality,
            unwrapped from JSON-LD {"@id": "..."} form via _id_ref().
            None when the source is a Law or state regulation.
        source_issuer: Plain-string IRI of estleg:enactedBy, unwrapped via
            _id_ref(). None when absent or non-KOV.
        issuer_registry: issuer @id → (label, municipality_iri, body_type)
            from build_issuer_registry().
    """
    if source_municipality is None:
        return None

    body_type_map = {
        "linnavolikogu": "volikogu",
        "vallavolikogu": "volikogu",
        "alevivolikogu": "volikogu",
        "linnavalitsus": "valitsus",
        "vallavalitsus": "valitsus",
    }
    target_body_type = body_type_map.get(body_slug)
    if target_body_type is None:
        return None

    source_issuer_authoritative = False
    if source_issuer is not None:
        source_entry = issuer_registry.get(source_issuer)
        if source_entry is not None:
            _label, source_mun, source_body_type = source_entry
            if source_mun == source_municipality:
                source_issuer_authoritative = True

                if (source_body_type == target_body_type
                        and source_issuer.endswith("_" + body_slug)):
                    return source_issuer

                if source_body_type != target_body_type:
                    paired_iri = _swap_issuer_body_suffix(source_issuer, body_slug)
                    if paired_iri is not None and paired_iri in issuer_registry:
                        paired_entry = issuer_registry.get(paired_iri)
                        if (paired_entry is not None
                                and paired_entry[1] == source_municipality):
                            return paired_iri
                    return None

                return None

    if source_issuer_authoritative:
        return None
    suffix = "_" + body_slug
    matches = [
        issuer_iri
        for issuer_iri, (_label, mun_iri, btype) in issuer_registry.items()
        if mun_iri == source_municipality
        and btype == target_body_type
        and issuer_iri.endswith(suffix)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _is_path3_case(
    source_issuer: str | None,
    source_municipality: str | None,
    issuer_registry: dict[str, tuple[str, str, str]],
) -> bool:
    """Return True iff the resolver's Path 1+2 are not
    authoritative for this case (i.e. Path 3 is what would run).
    Mirrors the resolver's source_issuer_authoritative check;
    used by main() to count fallback_hits."""
    if source_municipality is None:
        return False
    if source_issuer is None:
        return True
    entry = issuer_registry.get(source_issuer)
    if entry is None:
        return True
    _label, source_mun, _btype = entry
    return source_mun != source_municipality


def main() -> None:
    print("=" * 70)
    print("Estonian Legal Ontology - Institutional Competence Extraction")
    print("=" * 70)

    law_files = iter_peep_files(include_kov=False)  # DEFERRED to Layer 2c
    print(f"\n  Found {len(law_files)} law files to process")

    # Layer 2c PR #2: build issuer registry once at startup.
    issuer_registry = build_issuer_registry(
        KRR_DIR / "issuers_kov_peep.json"
    )

    print("\n[1/4] Processing law files (per-file idempotent)...")

    # institution IRI → collected data
    inst_data: dict[str, dict] = {}
    # institution IRI → list of (provision IRI, competence_type, law_name)
    inst_provisions: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

    total_provisions = 0
    provisions_with_institutions = 0
    _unresolved_count = 0
    _fallback_hits = 0
    _per_peep_errors = 0
    pre_existing_institution_files: list[Path] = []
    if INSTIT_DIR.exists():
        pre_existing_institution_files = list(INSTIT_DIR.glob("institution_*.json"))
    written_slugs: set[str] = set()

    for idx, filepath in enumerate(law_files, 1):
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            _per_peep_errors += 1
            continue

        act_node = _find_act_node(doc)
        if act_node is None:
            # Aggregate-registry peep or malformed; Task 7 adds the
            # full coverage skip-reason wiring.
            continue
        source_municipality = _id_ref(act_node.get("estleg:enactedByMunicipality"))
        source_issuer = _id_ref(act_node.get("estleg:enactedBy"))

        # Detect prior peep-side output BEFORE clearing.
        had_existing_peep = any(
            ("estleg:competentAuthority" in n)
            or ("estleg:competenceType" in n)
            for n in doc["@graph"]
        )

        # Clear unconditionally — the per-provision loop below
        # re-emits when detection succeeds.
        for n in doc["@graph"]:
            n.pop("estleg:competentAuthority", None)
            n.pop("estleg:competenceType", None)

        law_name = filepath.stem.replace("_peep", "")
        modified = False

        for node in doc["@graph"]:
            summary = node.get("estleg:summary", "")
            if not summary:
                continue

            total_provisions += 1
            institutions = detect_institutions(summary)
            if not institutions:
                continue

            provisions_with_institutions += 1
            competence_type = detect_competence_type(summary)
            provision_iri = node.get("@id", "")

            authority_refs = []
            for canon_name, iri_suffix, itype in institutions:
                if itype == "local_government_body":
                    canonical = _canonical_body_slug(canon_name)
                    if canonical is None:
                        continue
                    issuer_iri = _resolve_kov_authority(
                        body_slug=canonical,
                        source_municipality=source_municipality,
                        source_issuer=source_issuer,
                        issuer_registry=issuer_registry,
                    )
                    if issuer_iri is None:
                        _unresolved_count += 1
                        continue
                    if _is_path3_case(
                        source_issuer=source_issuer,
                        source_municipality=source_municipality,
                        issuer_registry=issuer_registry,
                    ):
                        _fallback_hits += 1
                    authority_refs.append({"@id": issuer_iri})
                    continue

                # Existing Institution_* path for everything else
                inst_iri = f"estleg:Institution_{iri_suffix}"

                if inst_iri not in inst_data:
                    inst_data[inst_iri] = {
                        "name": canon_name,
                        "iri_suffix": iri_suffix,
                        "type": itype,
                    }

                inst_provisions[inst_iri].append(
                    (provision_iri, competence_type, law_name)
                )
                authority_refs.append({"@id": inst_iri})

            # Add competent-authority link to provision node
            if authority_refs:
                node["estleg:competentAuthority"] = authority_refs
                node["estleg:competenceType"] = competence_type
                modified = True

        # Save when EITHER fresh output OR pre-existing output existed.
        if modified or had_existing_peep:
            save_json(filepath, doc)

        if idx % 100 == 0 or idx == len(law_files):
            print(f"  [{idx}/{len(law_files)}] processed – {len(inst_data)} institutions found")

    # ---------- generate institution files ----------
    print(f"\n[2/4] Generating institution files ({len(inst_data)} institutions)...")

    # Build reverse map: canonical suffix → list of abbreviation aliases
    canonical_aliases: dict[str, list[str]] = defaultdict(list)
    for alias, canonical in SAMEAS_ALIASES.items():
        canonical_aliases[canonical].append(alias)

    for inst_iri, info in sorted(inst_data.items()):
        provisions = inst_provisions[inst_iri]
        inst_node: dict = {
            "@id": inst_iri,
            "@type": ["owl:NamedIndividual", "estleg:Institution"],
            "rdfs:label": info["name"],
            "estleg:institutionType": info["type"],
        }

        # Add owl:sameAs links for abbreviation aliases of this institution
        suffix = info["iri_suffix"]
        if suffix in canonical_aliases:
            same_as = [
                {"@id": f"estleg:Institution_{a}"}
                for a in canonical_aliases[suffix]
            ]
            if len(same_as) == 1:
                inst_node["owl:sameAs"] = same_as[0]
            else:
                inst_node["owl:sameAs"] = same_as

        graph: list[dict] = [inst_node]

        # Group provisions by competence type
        by_competence: dict[str, list[str]] = defaultdict(list)
        for prov_iri, ctype, _law in provisions:
            by_competence[ctype].append(prov_iri)

        for ctype, prov_iris in sorted(by_competence.items()):
            graph.append({
                "@id": f"{inst_iri}_competence_{ctype}",
                "@type": ["owl:NamedIndividual", "estleg:Competence"],
                "rdfs:label": f"{info['name']} – {ctype}",
                "estleg:competenceType": ctype,
                "estleg:institution": {"@id": inst_iri},
                "estleg:appliesToProvision": [{"@id": p} for p in prov_iris[:50]],
            })

        doc = {"@context": CONTEXT, "@graph": graph}
        filename = f"institution_{info['iri_suffix']}.json"
        save_json(INSTIT_DIR / filename, doc)
        written_slugs.add(info["iri_suffix"])

    # ---------- report ----------
    print(f"\n[3/4] Generating report...")

    # Competence-type breakdown
    competence_counts: dict[str, int] = defaultdict(int)
    for provisions in inst_provisions.values():
        for _, ctype, _ in provisions:
            competence_counts[ctype] += 1

    # Laws per institution
    inst_law_counts: dict[str, int] = {}
    for inst_iri, provisions in inst_provisions.items():
        laws = {law for _, _, law in provisions}
        inst_law_counts[inst_data[inst_iri]["name"]] = len(laws)

    report = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "summary": {
            "total_law_files": len(law_files),
            "total_provisions_with_text": total_provisions,
            "provisions_with_institutions": provisions_with_institutions,
            "unique_institutions": len(inst_data),
        },
        "by_competence_type": dict(sorted(competence_counts.items(), key=lambda x: -x[1])),
        "institutions_by_provision_count": {
            inst_data[k]["name"]: len(v)
            for k, v in sorted(inst_provisions.items(), key=lambda x: -len(x[1]))
        },
        "institutions_by_law_count": dict(
            sorted(inst_law_counts.items(), key=lambda x: -x[1])
        ),
        "institution_types": {
            info["type"]: info["name"]
            for info in sorted(inst_data.values(), key=lambda x: x["type"])
        },
    }

    report_path = KRR_DIR / "institutional_competence_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # ---------- summary ----------
    print(f"\n[4/4] SUMMARY")
    print("=" * 70)
    print(f"  Total provisions analysed: {total_provisions}")
    print(f"  With institution refs:     {provisions_with_institutions}")
    print(f"  Unique institutions:       {len(inst_data)}")
    print(f"  Institution files:         {len(inst_data)}")
    print()
    print("  Top institutions by provision count:")
    top = sorted(inst_provisions.items(), key=lambda x: -len(x[1]))[:10]
    for inst_iri, provs in top:
        print(f"    {inst_data[inst_iri]['name']:45s}  {len(provs)} provisions")
    print("=" * 70)

    # Layer 2c PR #2: stale-file pruning. Only delete when the run
    # was clean (no per-peep load errors); otherwise we might delete
    # a file whose owning peep failed to load.
    if _per_peep_errors == 0:
        for path in pre_existing_institution_files:
            slug = path.stem.removeprefix("institution_")
            if slug not in written_slugs:
                try:
                    path.unlink()
                except OSError as exc:
                    print(f"  WARN: could not delete stale file {path.name}: {exc}")
    else:
        print(f"[skip stale-deletion] {_per_peep_errors} per-peep "
              f"errors during this run; preserving "
              f"{len(pre_existing_institution_files)} pre-existing "
              f"institution files.")


if __name__ == "__main__":
    main()
