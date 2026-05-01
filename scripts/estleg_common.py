"""
Shared constants, regex patterns, and helpers for Estonian Legal Ontology scripts.

This module centralises abbreviation mappings, JSON-LD context, and utility
functions used by extract_cross_references.py and
extract_court_provision_links.py so they stay in sync.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Well-known abbreviation -> full law name mappings (union of both scripts)
# ---------------------------------------------------------------------------
KNOWN_ABBREVIATIONS: dict[str, str] = {
    "KarS": "Karistusseadustik",
    "VÕS": "Võlaõigusseadus",
    "TsÜS": "Tsiviilseadustiku üldosa seadus",
    "AÕS": "Asjaõigusseadus",
    "PKS": "Perekonnaseadus",
    "ÄS": "Äriseadustik",
    "HMS": "Haldusmenetluse seadus",
    "TsMS": "Tsiviilkohtumenetluse seadustik",
    "KrMS": "Kriminaalmenetluse seadustik",
    "TMS": "Täitemenetluse seadustik",
    "KOKS": "Kohaliku omavalitsuse korralduse seadus",
    "PS": "Põhiseadus",
    "PankrS": "Pankrotiseadus",
    "MKS": "Maksukorralduse seadus",
    "TLS": "Töölepingu seadus",
    "RLS": "Riigilõivuseadus",
    "TTKS": "Töötervishoiu ja tööohutuse seadus",
    "TKS": "Tolliseadus",
    "TPTS": "Tööturuteenuste ja -toetuste seadus",
    "IKS": "Isikuandmete kaitse seadus",
    "RahaPTS": "Rahapesu ja terrorismi rahastamise tõkestamise seadus",
    "KELS": "Kõrgharidusseadus",
    "PPVS": "Planeerimisseadus",
    "AVVKHS": "Avaliku teenistuse seadus",
    "RHS": "Riigihangete seadus",
    "SHS": "Sotsiaalhoolekande seadus",
    "ELTS": "Elektrituruseadus",
    "EhS": "Ehitusseadus",
    "KVS": "Korruptsioonivastane seadus",
    "MSVS": "Meresõiduvahendite seadus",
    "RSVS": "Relvaseadus",
    "EKS": "Elektroonilise side seadus",
    "KES": "Keskkonnaseadustiku eriosa seadus",
    "LKS": "Looduskaitseseadus",
    "KeÜS": "Keskkonnaseadustiku üldosa seadus",
    "MaaRS": "Maareformi seadus",
    "KOS": "Kinnisasja omandamise kitsendamise seadus",
    "RavS": "Ravimiseadus",
    "AVTS": "Avaliku teabe seadus",
    "KLS": "Kalapüügiseadus",
    "KAVS": "Kaitseväeteenistuse seadus",
    "TTÜKS": "Tööstusomandi kaitse seadus",
    "KMS": "Käibemaksuseadus",
    "TuMS": "Tulumaksuseadus",
    "SMMS": "Sotsiaalmaksuseadus",
    "KindlTS": "Kindlustustegevuse seadus",
}

# ---------------------------------------------------------------------------
# Genitive / partitive full-name forms -> abbreviation
# ---------------------------------------------------------------------------
FULLNAME_GENITIVE: dict[str, str] = {
    "karistusseadustiku": "KarS",
    "võlaõigusseaduse": "VÕS",
    "tsiviilseadustiku üldosa seaduse": "TsÜS",
    "asjaõigusseaduse": "AÕS",
    "perekonnaseaduse": "PKS",
    "äriseadustiku": "ÄS",
    "haldusmenetluse seaduse": "HMS",
    "tsiviilkohtumenetluse seadustiku": "TsMS",
    "kriminaalmenetluse seadustiku": "KrMS",
    "täitemenetluse seadustiku": "TMS",
    "kohaliku omavalitsuse korralduse seaduse": "KOKS",
    "põhiseaduse": "PS",
    "pankrotiseaduse": "PankrS",
    "maksukorralduse seaduse": "MKS",
    "töölepingu seaduse": "TLS",
    "riigilõivuseaduse": "RLS",
}

# ---------------------------------------------------------------------------
# JSON-LD shared context
# ---------------------------------------------------------------------------
NS = "https://data.riik.ee/ontology/estleg#"

CONTEXT: dict[str, str] = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
}

# ---------------------------------------------------------------------------
# Paragraph citation regex fragment
#
# Handles all Estonian grammatical cases for the § symbol:
#   §, §§, §-de, §-des, §-d, §-s, §-st, §-le, §-i, §-ga
# Also accepts non-breaking hyphen (\u2011).
# ---------------------------------------------------------------------------
PAR_SUFFIX = r"§(?:§|[\-\u2011](?:de(?:s)?|d|s|st|le|i|ga))?"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def save_json(filepath: Path, doc: dict) -> None:
    """Write JSON-LD document to file with consistent formatting."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def iter_peep_files(
    *,
    include_laws: bool = True,
    include_regulations: bool = True,
    include_kov: bool = False,
) -> list[Path]:
    """Return every ``*_peep.json`` ontology file managed by the pipeline.

    Pipeline-wide file iterator that includes both top-level laws and the
    state-level regulations under ``regulations/riik/``. KOV regulations
    (``regulations/kov/``) are opt-in to keep ~11k near-duplicate municipal
    acts out of routine cross-reference / similarity passes until KOV
    integration is explicitly ramped in.
    """
    files: list[Path] = []
    if include_laws:
        files.extend(KRR_DIR.glob("*_peep.json"))
    if include_regulations:
        riik_dir = KRR_DIR / "regulations" / "riik"
        if riik_dir.exists():
            files.extend(riik_dir.glob("*_peep.json"))
        if include_kov:
            kov_dir = KRR_DIR / "regulations" / "kov"
            if kov_dir.exists():
                files.extend(kov_dir.glob("**/*_peep.json"))
    return sorted(files)


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    """Create a safe ID component from a string."""
    s = value.replace(" ", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s or "Unknown"
