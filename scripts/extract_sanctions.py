#!/usr/bin/env python3
"""
Extract penalties and sanctions from law text.

Scans estleg:summary text of each provision for Estonian sanction patterns
(imprisonment, fines, arrest, coercive payments) and creates estleg:Sanction
nodes linked to the originating provision.

Outputs:
  - krr_outputs/sanctions/  (per-law sanction JSON-LD files)
  - krr_outputs/sanctions_report.json

Estonian numerals
-----------------
Numeral parsing is intentionally **lemma-based** — only the forms listed
in ``_ESTONIAN_NUMBERS`` are recognised. Compound numerals such as
``kahekümneühe`` (21) are not synthesised from constituents; they must
be added to ``_ESTONIAN_NUMBERS`` explicitly. ``estonian_numerals_supported()``
returns the canonical set as a frozenset for tests/diagnostics.

Supported forms (current set):

* Partitive/genitive base forms: ``ühe`` (1) – ``kümne`` (10).
* Teen forms: ``üheteist`` (11) – ``üheksateist`` (19).
* Tens: ``kahekümne`` (20), ``kahekümneviie`` (25), ``kolmkümmend`` (30).
* Hundreds/thousands: ``sada``/``kakssada``/``kolmsada``/``kolmesada``/``viissada``,
  ``tuhat``.

Unrepresented compounds raise no error — the extractor simply emits no
``max_penalty`` for that match. Add lemmas as the corpus surfaces them.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from estleg_common import iter_peep_files, jsonld_text
from kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
SANCTION_DIR = KRR_DIR / "sanctions"
SANCTION_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://data.riik.ee/ontology/estleg#"

# ---------- Estonian number word parsing ----------

# Base number words used in Estonian penalty texts (partitive/genitive forms)
_ESTONIAN_NUMBERS: dict[str, int] = {
    "ühe": 1, "kahe": 2, "kolme": 3, "nelja": 4, "viie": 5,
    "kuue": 6, "seitsme": 7, "kaheksa": 8, "üheksa": 9,
    "kümne": 10,
    "üheteist": 11, "kaheteist": 12, "kolmeteist": 13,
    "neljateist": 14, "viieteist": 15, "kuueteist": 16,
    "seitsmeteist": 17, "kaheksateist": 18, "üheksateist": 19,
    "kahekümne": 20, "kahekümneviie": 25,
    "kolmkümmend": 30,
    # Nominative/other forms that appear in fine-unit contexts
    "kolmsada": 300, "kolmesada": 300, "kakssada": 200, "viissada": 500,
    "sada": 100,
    "tuhat": 1000,
}


def _estonian_word_to_int(word: str) -> int | None:
    """Convert an Estonian number word to an integer, or return None."""
    word_lower = word.lower()
    return _ESTONIAN_NUMBERS.get(word_lower)


def estonian_numerals_supported() -> frozenset[str]:
    """Return the canonical set of supported Estonian numeral lemmas.

    Used by tests and diagnostic tooling to assert the supported
    surface area without depending on the internal mutable dict.
    """
    return frozenset(_ESTONIAN_NUMBERS.keys())

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

# Severity index: lower = less severe
SEVERITY = {
    "coercive_payment": 1,
    "fine": 2,
    "pecuniary_punishment": 3,
    "arrest": 4,
    "imprisonment": 5,
}

# Human-readable labels per sanction type (English)
SANCTION_TYPE_LABELS = {
    "imprisonment": "Imprisonment",
    "pecuniary_punishment": "Pecuniary punishment",
    "fine": "Fine",
    "arrest": "Arrest",
    "coercive_payment": "Coercive payment",
}


def save_json(filepath: Path, doc: dict) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_json(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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


def _provision_ref(node: dict) -> str:
    """Build a short human-readable provision reference like 'KarS § 121'.

    Returns an empty string when no usable reference can be derived.
    Previously the fallback emitted the literal ``'unknown'`` which
    polluted ``_build_label`` output (e.g. ``"Imprisonment, max 5 years (unknown)"``).
    ``_build_label`` already suppresses the parenthetical when the ref
    is empty, so returning ``""`` is the cleaner contract.
    """
    par = node.get("estleg:paragrahv", "")
    law_abbr = node.get("estleg:lawAbbreviation", "")
    if not law_abbr:
        # Try to extract from @id  e.g. "estleg:KarS_p121"
        node_id = node.get("@id", "")
        parts = node_id.replace("estleg:", "").split("_")
        if parts:
            law_abbr = parts[0]
    if par:
        return f"{law_abbr} \u00a7 {par}".strip()
    return law_abbr or ""


def _find_act_node(doc: dict) -> dict | None:
    """Return the act node from a peep file's @graph, or None if no
    act node is present.

    Match criteria: the node must be typed BOTH `estleg:Act` and
    `owl:Ontology`. Some peep files contain auxiliary `owl:Ontology`
    wrappers (concept-cluster manifests etc.) that aren't acts; the
    `estleg:Act` constraint prevents misclassifying those as
    sanction-bearing acts. Layer 1 generators stamp every real act
    node with both types.

    Callers must handle a None return \u2014 the per-file processing
    treats it as a malformed-input skip (logged to the coverage
    report's failure_samples + skip_reasons['missing_act_node']).
    """
    for n in doc.get("@graph", []):
        types = n.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "estleg:Act" in types and "owl:Ontology" in types:
            return n
    return None


def _classify_enforcement_level(act_node: dict) -> str:
    """Classify the enforcement level of an act node by its @type.

    Returns "municipality" iff the act is typed
    estleg:MunicipalRegulation; otherwise "state".

    Handles @type as either a string or a list of strings.
    """
    types = act_node.get("@type") or []
    if isinstance(types, str):
        types = [types]
    if "estleg:MunicipalRegulation" in types:
        return "municipality"
    return "state"


# ---------- sanction pattern definitions ----------

# Each extractor returns a list of dicts with keys:
#   sanction_type, max_penalty, min_penalty (optional)

def _parse_number(s: str) -> int | None:
    """Parse a number from either a digit string or an Estonian word."""
    s = s.strip().rstrip("-")
    if s.isdigit():
        return int(s)
    return _estonian_word_to_int(s)


def _ordered_year_range(lo_raw: int, hi_raw: int) -> tuple[str, str]:
    """Issue #273: return ``(min_penalty, max_penalty)`` year strings with
    ``min <= max`` guaranteed.

    The range parsers read group 1 as the lower and group 2 as the upper
    bound, but a source range can be written high-to-low (e.g. "kümne kuni
    viieaastase vangistusega" = ten down to five years). Without sorting,
    that yielded ``min 10 years / max 5 years``, propagating an inverted
    ``minPenaltyAmount > maxPenaltyAmount`` into the structured fields. We
    sort the two bounds and log when a swap was required, since a high-to-
    low range usually signals an OCR/source anomaly worth inspecting.
    """
    lo, hi = sorted((lo_raw, hi_raw))
    if (lo_raw, hi_raw) != (lo, hi):
        print(
            f"  WARN: imprisonment range written high-to-low "
            f"({lo_raw}–{hi_raw} years); swapped to min {lo} / max {hi}"
        )
    return f"{lo} years", f"{hi} years"


def extract_imprisonment(text: str) -> list[dict]:
    """Detect imprisonment sentences.

    Handles both digit-based ("kuni 5 aastase vangistusega") and
    Estonian word-based ("kuni viieaastase vangistusega") patterns.

    Word-based patterns are anchored to the closed set of supported
    Estonian numerals (``_ESTONIAN_NUMBERS``). The previous ``\\w+``
    over-matched arbitrary tokens before ``aasta``, which produced
    spurious empty matches and hid genuine extraction failures.
    """
    results: list[dict] = []

    def _already(penalty_str: str, field: str = "max_penalty") -> bool:
        return any(r.get(field) == penalty_str for r in results) or any(
            r.get("min_penalty") == penalty_str for r in results
        )

    # Life imprisonment: "eluaegne/eluaegse vangistus"
    if re.search(r"eluaeg\w*\s+vangistus", text, re.IGNORECASE):
        results.append({
            "sanction_type": "imprisonment",
            "max_penalty": "life",
        })

    # --- Word-based patterns (most common in KarS) ---
    _NUMBER_ALT = "|".join(
        sorted((re.escape(k) for k in _ESTONIAN_NUMBERS), key=len, reverse=True)
    )

    # Range: "kuue- kuni viieteistaastase vangistusega"
    range_pat = (
        rf"({_NUMBER_ALT})-?\s+kuni\s+({_NUMBER_ALT})aasta(?:se)?\s+vangistus"
    )
    for m in re.finditer(range_pat, text, re.IGNORECASE):
        min_val = _parse_number(m.group(1))
        max_val = _parse_number(m.group(2))
        if min_val is not None and max_val is not None:
            # Issue #273: enforce min <= max (source range may be high-to-low).
            min_pen, max_pen = _ordered_year_range(min_val, max_val)
            results.append({
                "sanction_type": "imprisonment",
                "min_penalty": min_pen,
                "max_penalty": max_pen,
            })

    # Max only (word): "kuni viieaastase vangistusega"
    max_pat = rf"kuni\s+({_NUMBER_ALT})aasta(?:se)?\s+vangistus"
    for m in re.finditer(max_pat, text, re.IGNORECASE):
        val = _parse_number(m.group(1))
        if val is not None and not _already(f"{val} years"):
            results.append({
                "sanction_type": "imprisonment",
                "max_penalty": f"{val} years",
            })

    # --- Digit-based patterns (less common but possible) ---

    # Range: "N kuni M aastase vangistusega"
    for m in re.finditer(
        r"(\d+)\s*[\-\u2013\u2014]?\s*kuni\s+(\d+)\s*[\-\s]*aasta(?:se)?\s+vangistus",
        text, re.IGNORECASE,
    ):
        # Issue #273: enforce min <= max (source range may be high-to-low).
        min_s, max_s = _ordered_year_range(int(m.group(1)), int(m.group(2)))
        if not _already(max_s):
            results.append({
                "sanction_type": "imprisonment",
                "min_penalty": min_s,
                "max_penalty": max_s,
            })

    # Max only (digit): "kuni N aastase vangistusega"
    for m in re.finditer(
        r"kuni\s+(\d+)\s*[\-\s]*aasta(?:se)?\s+vangistus",
        text, re.IGNORECASE,
    ):
        penalty = f"{m.group(1)} years"
        if not _already(penalty):
            results.append({
                "sanction_type": "imprisonment",
                "max_penalty": penalty,
            })

    # "karistatakse ... N ... aastase vangistusega" (digit fallback)
    for m in re.finditer(
        r"karistatakse.*?(\d+).*?aasta(?:se)?\s+vangistus",
        text, re.IGNORECASE,
    ):
        val = m.group(1)
        if not _already(f"{val} years"):
            results.append({
                "sanction_type": "imprisonment",
                "max_penalty": f"{val} years",
            })

    # Fallback: "vangistus(ega) N <unit>" — REQUIRES an explicit unit
    # token to interpret the number. The previous heuristic guessed
    # ``years`` for N <= 20 and ``months`` otherwise, which silently
    # mis-attributed sentences (e.g. "vangistus 24 päeva" → "24 months").
    # If no unit token follows, no max_penalty is emitted.
    fallback_pat = (
        r"vangistus(?:ega)?\s+(\d+)\s*"
        r"(aasta(?:se|t|ne|id)?|kuu(?:d|s|line)?|päev(?:a|ad)?)"
    )
    _UNIT_TO_NORMAL = {
        "aasta": "years",
        "kuu": "months",
        "päev": "days",
    }
    for m in re.finditer(fallback_pat, text, re.IGNORECASE):
        val = m.group(1)
        unit_raw = m.group(2).lower()
        normalised = None
        for prefix, mapped in _UNIT_TO_NORMAL.items():
            if unit_raw.startswith(prefix):
                normalised = mapped
                break
        if normalised is None:
            continue
        penalty_str = f"{val} {normalised}"
        if not _already(penalty_str):
            results.append({
                "sanction_type": "imprisonment",
                "max_penalty": penalty_str,
            })

    return results


def extract_pecuniary(text: str) -> list[dict]:
    """Detect pecuniary punishment (rahaline karistus).

    Per KarS § 44, pecuniary punishment is 30–500 daily rates for natural
    persons.  The specific offence provision almost never states the amount;
    it just says "karistatakse rahalise karistusega" (optionally followed by
    "või kuni N-aastase vangistusega").  When the value comes from the
    statutory ceiling rather than the provision text we mark the record
    with ``is_statutory_default=True`` so downstream consumers can
    distinguish "extracted from text" from "statutory fallback".
    """
    results: list[dict] = []
    if re.search(r"rahalise?\s+karistus", text, re.IGNORECASE):
        results.append({
            "sanction_type": "pecuniary_punishment",
            "max_penalty": "500 daily rates",
            "is_statutory_default": True,
        })
    return results


def extract_fine_units(text: str) -> list[dict]:
    """Detect fines in trahviühikut (fine units)."""
    results: list[dict] = []

    def _add(amount_str: str) -> None:
        if not any(r.get("max_penalty") == f"{amount_str} fine units" for r in results):
            results.append({
                "sanction_type": "fine",
                "max_penalty": f"{amount_str} fine units",
            })

    # Digit-based: "trahv(iga) (kuni) N trahviühikut"
    for m in re.finditer(
        r"(?:raha)?trahv(?:iga)?\s+(?:kuni\s+)?(\d+)\s+trahviühiku", text, re.IGNORECASE
    ):
        _add(m.group(1))

    # Word-based: "rahatrahviga kuni kolmsada trahviühikut"
    for m in re.finditer(
        r"(?:raha)?trahv(?:iga)?\s+(?:kuni\s+)?(\w+)\s+trahviühiku", text, re.IGNORECASE
    ):
        word = m.group(1)
        if word.isdigit():
            continue  # already handled above
        val = _estonian_word_to_int(word)
        if val is not None:
            _add(str(val))

    return results


def extract_fine_euros(text: str) -> list[dict]:
    """Detect monetary fines in euros."""
    results: list[dict] = []
    # "rahatrahv(iga) (kuni) N eurot"
    for m in re.finditer(
        r"rahatrahv(?:iga)?\s+(?:kuni\s+)?(\d[\d\s]*)\s*eurot", text, re.IGNORECASE
    ):
        amount = m.group(1).replace(" ", "")
        already = any(
            r.get("max_penalty") == f"{amount} EUR" for r in results
        )
        if not already:
            results.append({
                "sanction_type": "fine",
                "max_penalty": f"{amount} EUR",
            })
    return results


def extract_coercive(text: str) -> list[dict]:
    """Detect coercive payment (sunniraha)."""
    results: list[dict] = []
    for m in re.finditer(
        r"sunniraha\s+kuni\s+(\d[\d\s]*)\s*eurot", text, re.IGNORECASE
    ):
        amount = m.group(1).replace(" ", "")
        results.append({
            "sanction_type": "coercive_payment",
            "max_penalty": f"{amount} EUR",
        })
    # Generic mention without amount
    if not results and re.search(r"\bsunniraha\b", text, re.IGNORECASE):
        results.append({"sanction_type": "coercive_payment"})
    return results


def extract_arrest(text: str) -> list[dict]:
    """Detect arrest (arest).

    Per KarS § 48, the maximum arrest term for misdemeanours is 30 days.
    Provision text typically says "karistatakse ... või arestiga" without
    specifying the number of days.  We try to extract an explicit day count
    first (e.g. "aresti kuni kolmkümmend päeva") and fall back to the
    statutory maximum of 30 days **only** when the surrounding text
    confirms a punishment context. The bare ``\\barest`` regex used to
    fire on words like "arestima" outside sentencing language; the
    fallback now requires both:

    * a ``arest``-prefix word ending with a punishment-context suffix
      (``arestiga``, ``aresti``, ``arestiks``);
    * a sentencing co-occurrence (``karistatakse``, ``kohaldatakse``)
      somewhere in the text.

    Statutory-default emissions are tagged with ``is_statutory_default=True``.
    """
    results: list[dict] = []

    # Explicit day count: "aresti kuni N päeva" or "aresti kuni <word> päeva"
    for m in re.finditer(
        r"aresti?\w*\s+(?:kuni\s+)?(\d+)\s*(?:päeva|ööpäeva)",
        text, re.IGNORECASE,
    ):
        results.append({
            "sanction_type": "arrest",
            "max_penalty": f"{m.group(1)} days",
        })

    # Word-based day count: "aresti kuni kolmkümmend päeva"
    if not results:
        for m in re.finditer(
            r"aresti?\w*\s+(?:kuni\s+)?(\w+)\s+(?:päeva|ööpäeva)",
            text, re.IGNORECASE,
        ):
            val = _estonian_word_to_int(m.group(1))
            if val is not None:
                results.append({
                    "sanction_type": "arrest",
                    "max_penalty": f"{val} days",
                })

    # Generic arrest mention without explicit days → statutory max 30 days,
    # gated on (a) punishment-context suffix and (b) sentencing verb.
    if not results:
        punishment_suffix = re.search(
            r"\barest(?:i|iga|iks|ile)\b", text, re.IGNORECASE
        )
        sentencing_verb = re.search(
            r"\b(?:karistatakse|kohaldatakse)\b", text, re.IGNORECASE
        )
        if punishment_suffix and sentencing_verb:
            results.append({
                "sanction_type": "arrest",
                "max_penalty": "30 days",
                "is_statutory_default": True,
            })

    return results


ALL_EXTRACTORS = [
    extract_imprisonment,
    extract_pecuniary,
    extract_fine_units,
    extract_fine_euros,
    extract_coercive,
    extract_arrest,
]


def extract_sanctions(text: str) -> list[dict]:
    """Run all extractors and return deduplicated sanction records.

    The ``is_statutory_default`` flag (when present) is preserved
    through dedup so callers can distinguish a fallback emission
    from a text-derived one. If both flagged and unflagged variants
    of the same logical key appear, the unflagged variant wins (a
    text-derived value is more specific than a statutory default).
    """
    all_sanctions: list[dict] = []
    seen: dict[str, dict] = {}
    for extractor in ALL_EXTRACTORS:
        for s in extractor(text):
            key = f"{s['sanction_type']}|{s.get('max_penalty', '')}|{s.get('min_penalty', '')}"
            existing = seen.get(key)
            if existing is None:
                seen[key] = s
                all_sanctions.append(s)
            elif existing.get("is_statutory_default") and not s.get(
                "is_statutory_default"
            ):
                # Replace the statutory-default record with the
                # text-derived one in-place so list order is stable.
                idx = all_sanctions.index(existing)
                all_sanctions[idx] = s
                seen[key] = s
    return all_sanctions


def _try_extract_penalty_from_summary(text: str, sanction_type: str) -> str | None:
    """Attempt to extract a numeric penalty value from summary text for a given type.

    Used as a fallback when the extractor did not capture a maxPenalty.
    """
    if sanction_type == "imprisonment":
        # Try digit-based first
        m = re.search(r"(\d+)\s*aasta", text, re.IGNORECASE)
        if m:
            return f"{m.group(1)} years"
        # Try word-based: "viieaastase"
        m = re.search(r"(\w+)aasta", text, re.IGNORECASE)
        if m:
            val = _estonian_word_to_int(m.group(1))
            if val is not None:
                return f"{val} years"
    elif sanction_type == "pecuniary_punishment":
        # Statutory max per KarS § 44: 500 daily rates
        return "500 daily rates"
    elif sanction_type == "fine":
        m = re.search(r"(\d[\d\s]*)\s*(?:eurot|trahviühiku)", text, re.IGNORECASE)
        if m:
            val = m.group(1).replace(" ", "")
            if "eurot" in text.lower():
                return f"{val} EUR"
            return f"{val} fine units"
    elif sanction_type == "arrest":
        # Try explicit day count first
        m = re.search(r"arest\w*\s+(?:kuni\s+)?(\d+)\s*(?:päeva|ööpäeva)", text, re.IGNORECASE)
        if m:
            return f"{m.group(1)} days"
        # Statutory max per KarS § 48: 30 days
        return "30 days"
    elif sanction_type == "coercive_payment":
        m = re.search(r"sunniraha\w*\s+(?:kuni\s+)?(\d[\d\s]*)\s*eurot", text, re.IGNORECASE)
        if m:
            return m.group(1).replace(" ", "") + " EUR"
    return None


def _normalise_penalty_text(penalty: str) -> str:
    """Fix singular/plural in penalty strings like '1 years' -> '1 year'."""
    m = re.match(r"^1\s+(years|days|daily rates|fine units)$", penalty)
    if m:
        unit = m.group(1)
        singular = unit.rstrip("s") if unit != "daily rates" else "daily rate"
        return f"1 {singular}"
    return penalty


# ---------- structured penalty normalisation (issue #133) ----------

# Controlled set of structured penalty units. The free-text strings the
# extractors emit map onto these so downstream consumers can compare /
# filter / aggregate penalties numerically:
#   "years"/"months"/"days" — imprisonment & arrest durations
#   "daily_rates"           — KarS päevamäär (pecuniary punishment)
#   "fine_units"            — väärteo trahviühik (misdemeanour fine)
#   "monetary"              — a fixed monetary sum (paired with a currency)
PENALTY_UNIT_YEARS = "years"
PENALTY_UNIT_MONTHS = "months"
PENALTY_UNIT_DAYS = "days"
PENALTY_UNIT_DAILY_RATES = "daily_rates"
PENALTY_UNIT_FINE_UNITS = "fine_units"
PENALTY_UNIT_MONETARY = "monetary"

PENALTY_UNITS: frozenset[str] = frozenset({
    PENALTY_UNIT_YEARS,
    PENALTY_UNIT_MONTHS,
    PENALTY_UNIT_DAYS,
    PENALTY_UNIT_DAILY_RATES,
    PENALTY_UNIT_FINE_UNITS,
    PENALTY_UNIT_MONETARY,
})

# Maps the trailing free-text unit token (already normalised by the
# extractors / `_normalise_penalty_text`) onto a structured unit. Both
# singular and plural forms are covered because `_build_label` /
# `_normalise_penalty_text` may have singularised a "1 <unit>" string.
_FREE_TEXT_UNIT_TO_STRUCTURED: dict[str, tuple[str, str | None]] = {
    "year": (PENALTY_UNIT_YEARS, None),
    "years": (PENALTY_UNIT_YEARS, None),
    "month": (PENALTY_UNIT_MONTHS, None),
    "months": (PENALTY_UNIT_MONTHS, None),
    "day": (PENALTY_UNIT_DAYS, None),
    "days": (PENALTY_UNIT_DAYS, None),
    "daily rate": (PENALTY_UNIT_DAILY_RATES, None),
    "daily rates": (PENALTY_UNIT_DAILY_RATES, None),
    "fine unit": (PENALTY_UNIT_FINE_UNITS, None),
    "fine units": (PENALTY_UNIT_FINE_UNITS, None),
    "eur": (PENALTY_UNIT_MONETARY, "EUR"),
}

# "<amount> <unit-words>" — amount is digits (optionally with a decimal
# point); the unit is whatever follows, matched case-insensitively
# against `_FREE_TEXT_UNIT_TO_STRUCTURED`.
_PENALTY_STRUCTURED_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s+(.+?)\s*$"
)


def _parse_penalty_to_structured(
    penalty_str: str,
) -> tuple[Decimal, str, str | None] | None:
    """Map a free-text penalty string onto a structured (amount, unit, currency) triple.

    Recognises exactly the strings ``extract_sanctions`` already produces:

      * ``"<N> years"`` / ``"<N> months"`` / ``"<N> days"`` (also the
        singular ``"1 year"`` etc. that ``_normalise_penalty_text``
        emits) → ``(Decimal(N), "years"|"months"|"days", None)``
      * ``"<N> daily rates"`` (KarS päevamäär) →
        ``(Decimal(N), "daily_rates", None)``
      * ``"<N> fine units"`` (väärteo trahviühik) →
        ``(Decimal(N), "fine_units", None)``
      * ``"<N> EUR"`` (a fixed monetary sum) →
        ``(Decimal(N), "monetary", "EUR")``

    Returns ``None`` when the string can't be confidently parsed into
    ``amount + unit`` — notably ``"life"`` (life imprisonment has no
    finite amount), the empty string, or any unit token outside the
    controlled set. Callers emit only the free-text field in that case
    and bump the ``penalty_unparsed`` counter.
    """
    if not penalty_str:
        return None
    text = penalty_str.strip()
    if not text:
        return None

    m = _PENALTY_STRUCTURED_RE.match(text)
    if m is None:
        # No "<number> <unit>" shape — e.g. "life".
        return None

    amount_raw, unit_raw = m.group(1), m.group(2).strip().lower()
    mapped = _FREE_TEXT_UNIT_TO_STRUCTURED.get(unit_raw)
    if mapped is None:
        return None
    unit, currency = mapped

    try:
        amount = Decimal(amount_raw)
    except (InvalidOperation, ValueError):
        return None
    # Guard against absurd / non-positive values slipping through.
    if amount <= 0:
        return None

    return amount, unit, currency


def _decimal_literal(value: Decimal) -> str:
    """Render a Decimal as a clean JSON-LD xsd:decimal lexical form.

    ``Decimal("5")`` → ``"5"`` (not ``"5.0"``); ``Decimal("15000")`` →
    ``"15000"``. A genuine fractional value is preserved as-is.
    """
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _structured_penalty_fields(
    penalty_str: str, prefix: str,
) -> tuple[dict, int]:
    """Build the structured property block for one penalty string.

    ``prefix`` is ``"max"`` or ``"min"`` — the returned dict keys are
    ``estleg:{prefix}PenaltyAmount`` (xsd:decimal literal),
    ``estleg:{prefix}PenaltyUnit`` (a value from ``PENALTY_UNITS``) and,
    only when the unit is ``"monetary"``, ``estleg:{prefix}PenaltyCurrency``.

    Returns ``({}, 1)`` when the string can't be parsed (caller bumps
    the ``penalty_unparsed`` counter); ``({...}, 0)`` on success.
    """
    parsed = _parse_penalty_to_structured(penalty_str)
    if parsed is None:
        return {}, 1
    amount, unit, currency = parsed
    fields: dict = {
        f"estleg:{prefix}PenaltyAmount": {
            "@value": _decimal_literal(amount),
            "@type": "xsd:decimal",
        },
        f"estleg:{prefix}PenaltyUnit": unit,
    }
    if unit == PENALTY_UNIT_MONETARY and currency:
        fields[f"estleg:{prefix}PenaltyCurrency"] = currency
    return fields, 0


def _attach_structured_penalties(sanction_node: dict, sanction_data: dict) -> int:
    """Add the structured penalty trio(s) to a Sanction node in place.

    Mirrors whatever free-text ``estleg:maxPenalty`` / ``estleg:minPenalty``
    the node already carries. Returns the number of penalty strings that
    could *not* be parsed into ``amount + unit`` (so ``main()`` can bump
    its ``penalty_unparsed`` tally). The ``estleg:isStatutoryDefault``
    flag — when present — applies to these structured fields too; it is
    written once on the node by the caller.
    """
    unparsed = 0
    for prefix, key in (("max", "max_penalty"), ("min", "min_penalty")):
        raw = sanction_data.get(key)
        if not raw:
            continue
        fields, miss = _structured_penalty_fields(str(raw), prefix)
        unparsed += miss
        sanction_node.update(fields)
    return unparsed


def _build_label(sanction_type: str, sanction_data: dict, provision_ref: str) -> str:
    """Build a descriptive rdfs:label for a sanction node.

    Example: "Imprisonment, max 5 years (KarS § 121)"
    """
    type_label = SANCTION_TYPE_LABELS.get(sanction_type, sanction_type)

    max_p = _normalise_penalty_text(sanction_data.get("max_penalty", ""))
    min_p = _normalise_penalty_text(sanction_data.get("min_penalty", ""))

    if max_p == "life":
        desc = f"{type_label}, life"
    elif min_p and max_p:
        desc = f"{type_label}, {min_p} to {max_p}"
    elif max_p:
        desc = f"{type_label}, max {max_p}"
    else:
        desc = type_label

    if provision_ref:
        desc = f"{desc} ({provision_ref})"

    return desc


def main() -> int:
    print("=" * 70)
    print("Estonian Legal Ontology - Sanctions Extraction")
    print("=" * 70)

    _start = time.perf_counter()
    _files_processed: set[Path] = set()
    _files_processed_kov: set[Path] = set()
    _files_with_output: set[Path] = set()
    _files_with_output_kov: set[Path] = set()
    _triples = 0
    _triples_kov = 0
    _files_skipped = 0
    _skip_reasons: dict[str, int] = {}
    _failures: list[str] = []
    _unresolved = 0  # always 0 for this pipeline; field required by helper

    law_files = iter_peep_files()

    print("\n[1/4] Processing law files (per-file idempotent)...")

    # Collect sanctions per law
    all_law_sanctions: dict[str, list[dict]] = {}
    stats_per_type: dict[str, int] = defaultdict(int)
    total_provisions = 0
    provisions_with_sanctions = 0
    total_sanction_count = 0
    # Penalty normalisation tallies (issue #133): how many free-text
    # penalty strings were turned into a structured amount+unit triple
    # vs. left as free-text only because they couldn't be parsed.
    penalty_structured = 0
    penalty_unparsed = 0

    for idx, filepath in enumerate(law_files, 1):
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue

        act_node = _find_act_node(doc)
        if act_node is None:
            # Distinguish aggregate-registry peeps (issuers_kov_peep.json,
            # municipalities_peep.json, etc.) from malformed act peeps.
            #
            # Aggregate peeps have no provision nodes — they're
            # catalogues of Issuer/Municipality/etc. entities.
            # Silently skip those (no counter, no failure log) because
            # they're correctly-formed non-act peeps that just don't
            # carry sanctions.
            #
            # Malformed act peeps DO have provision nodes but no
            # estleg:Act + owl:Ontology typing on their root node —
            # that's a Layer 1 data bug worth surfacing in the
            # coverage report's failure_samples.
            #
            # Use `estleg:paragrahv` as the provision signal rather
            # than @type membership: it's universal across all
            # provision class shapes (estleg:LegalProvision,
            # estleg:KovProvision, estleg:LegalProvision_<prefix>,
            # estleg:Regulation_<id>, etc.). @type-based detection
            # would silently miss state-regulation provisions typed
            # only as estleg:Regulation_<id>.
            has_provisions = any(
                "estleg:paragrahv" in n
                for n in doc.get("@graph", [])
            )
            if has_provisions:
                _files_skipped += 1
                _skip_reasons["missing_act_node"] = (
                    _skip_reasons.get("missing_act_node", 0) + 1
                )
                _failures.append(
                    f"{filepath.name}: malformed peep — has provisions "
                    f"but missing estleg:Act + owl:Ontology root node"
                )
            # Either way, the file has no act to classify — skip extraction.
            continue
        enforcement_level = _classify_enforcement_level(act_node)

        is_kov = "regulations/kov/" in str(filepath)
        _files_processed.add(filepath)
        if is_kov:
            _files_processed_kov.add(filepath)

        # Step 1: detect prior peep-side output BEFORE any mutation.
        had_existing_peep = any(
            ("estleg:hasSanction" in n) or ("estleg:enforcedAtLevel" in n)
            for n in doc["@graph"]
        )

        # Step 2: clear peep-side state unconditionally (idempotency).
        for n in doc["@graph"]:
            n.pop("estleg:hasSanction", None)
            n.pop("estleg:enforcedAtLevel", None)

        law_name = filepath.stem.replace("_peep", "")
        law_sanctions: list[dict] = []

        # Detect pre-existing sanctions JSON file (path computed
        # the same way the writer uses).
        sanctions_filename = f"sanctions_{sanitize_id(law_name)}.json"
        sanctions_path = SANCTION_DIR / sanctions_filename
        had_existing_sanctions_file = sanctions_path.exists()

        iri_counts: dict[str, int] = defaultdict(int)

        # Step 3: run extraction over the cleared graph.
        for node in doc["@graph"]:
            summary = jsonld_text(node.get("estleg:summary", ""))
            if not summary:
                continue

            total_provisions += 1
            sanctions = extract_sanctions(summary)
            if not sanctions:
                continue

            provisions_with_sanctions += 1
            provision_iri = node.get("@id", "")
            provision_ref = _provision_ref(node)
            provision_par = (
                provision_iri.replace("estleg:", "")
                if provision_iri
                else sanitize_id(node.get("estleg:paragrahv", "unknown"))
            )

            # Sort sanctions deterministically before assigning IRI
            # suffixes. The previous order depended on extractor
            # iteration / regex match order, which produced different
            # ``estleg:Sanction_<par>_<type>_2`` suffixes across runs
            # whenever a provision triggered the same sanction type
            # twice.
            sanctions = sorted(
                sanctions,
                key=lambda s: (
                    s.get("sanction_type", ""),
                    s.get("max_penalty", "") or "",
                    s.get("min_penalty", "") or "",
                ),
            )

            sanction_refs: list[dict] = []
            for s in sanctions:
                total_sanction_count += 1
                stype = s["sanction_type"]

                if "max_penalty" not in s:
                    fallback = _try_extract_penalty_from_summary(summary, stype)
                    if fallback:
                        s["max_penalty"] = fallback
                        # Mark statutory-default emission so consumers
                        # can distinguish text-extracted from fallback.
                        s.setdefault("is_statutory_default", True)

                base_iri = f"estleg:Sanction_{provision_par}_{stype}"
                iri_counts[base_iri] += 1
                if iri_counts[base_iri] == 1:
                    sanction_iri = base_iri
                else:
                    sanction_iri = f"{base_iri}_{iri_counts[base_iri]}"

                sanction_node: dict = {
                    "@id": sanction_iri,
                    "@type": ["owl:NamedIndividual", "estleg:Sanction"],
                    "estleg:sanctionType": stype,
                    "estleg:applicableProvision": {"@id": provision_iri},
                }
                if "max_penalty" in s:
                    sanction_node["estleg:maxPenalty"] = s["max_penalty"]
                if "min_penalty" in s:
                    sanction_node["estleg:minPenalty"] = s["min_penalty"]
                # Structured penalty fields (issue #133): emitted only
                # when the free-text string parses confidently into
                # amount+unit; otherwise bump the unparsed tally.
                _unparsed = _attach_structured_penalties(sanction_node, s)
                _emitted = sum(
                    1 for k in ("max_penalty", "min_penalty") if s.get(k)
                ) - _unparsed
                penalty_structured += _emitted
                penalty_unparsed += _unparsed
                if s.get("is_statutory_default"):
                    sanction_node["estleg:isStatutoryDefault"] = True
                sanction_node["rdfs:label"] = _build_label(stype, s, provision_ref)

                law_sanctions.append(sanction_node)
                sanction_refs.append({"@id": sanction_iri})
                stats_per_type[stype] += 1

            if sanction_refs:
                node["estleg:hasSanction"] = sanction_refs
                node["estleg:enforcedAtLevel"] = enforcement_level

        # Step 4: save when EITHER fresh output exists OR pre-existing
        # output existed (so cleared-but-empty files persist the
        # cleared state to disk).
        if law_sanctions or had_existing_peep:
            save_json(filepath, doc)
        if law_sanctions:
            all_law_sanctions[law_name] = law_sanctions
            _files_with_output.add(filepath)
            if is_kov:
                _files_with_output_kov.add(filepath)
            _triples += len(law_sanctions)  # SANCTION RECORDS, not RDF triples
            if is_kov:
                _triples_kov += len(law_sanctions)

        # Sanctions-file lifecycle:
        # - If the act produced fresh sanctions, the writer block
        #   below regenerates the file (Step 2 of main()).
        # - If the act produced NO fresh sanctions but a stale file
        #   exists on disk, delete it.
        if not law_sanctions and had_existing_sanctions_file:
            try:
                sanctions_path.unlink()
            except OSError:
                pass  # tolerate concurrent removal

        if idx % 100 == 0 or idx == len(law_files):
            print(f"  [{idx}/{len(law_files)}] processed \u2013 {total_sanction_count} sanctions found")

    # ---------- generate sanction files ----------
    print(f"\n[2/4] Generating sanction files ({len(all_law_sanctions)} laws with sanctions)...")

    for law_name, sanctions in sorted(all_law_sanctions.items()):
        graph: list[dict] = [
            {
                "@id": f"estleg:Sanctions_{sanitize_id(law_name)}_Map",
                "@type": ["owl:Ontology"],
                "rdfs:label": f"Sanctions \u2013 {law_name}",
                "dc:source": law_name,
            },
        ]
        graph.extend(sanctions)

        doc = {"@context": CONTEXT, "@graph": graph}
        filename = f"sanctions_{sanitize_id(law_name)}.json"
        save_json(SANCTION_DIR / filename, doc)

    # ---------- severity index ----------
    severity_index: list[dict] = []
    for law_name, sanctions in sorted(all_law_sanctions.items()):
        max_severity = 0
        for s in sanctions:
            stype = s.get("estleg:sanctionType", "")
            sev = SEVERITY.get(stype, 0)
            if sev > max_severity:
                max_severity = sev
        severity_index.append({
            "law": law_name,
            "sanction_count": len(sanctions),
            "max_severity": max_severity,
            "max_severity_type": next(
                (k for k, v in SEVERITY.items() if v == max_severity), "unknown"
            ),
        })

    severity_index.sort(key=lambda x: (-x["max_severity"], -x["sanction_count"]))

    # ---------- report ----------
    print("\n[3/4] Generating report...")

    report = {
        "generated": datetime.now(timezone.utc).date().isoformat(),
        "summary": {
            "total_law_files": len(law_files),
            "total_provisions_with_text": total_provisions,
            "provisions_with_sanctions": provisions_with_sanctions,
            "total_sanction_records": total_sanction_count,
            "laws_with_sanctions": len(all_law_sanctions),
        },
        # Penalty normalisation coverage (issue #133): free-text penalty
        # strings that were turned into a structured amount+unit triple
        # vs. left as free-text only because they couldn't be parsed
        # (e.g. "life", or any unit token outside the controlled set).
        "penalty_normalisation": {
            "penalty_structured": penalty_structured,
            "penalty_unparsed": penalty_unparsed,
        },
        "by_sanction_type": dict(sorted(stats_per_type.items(), key=lambda x: -x[1])),
        "severity_index": severity_index[:50],
        "laws_by_sanction_count": {
            law: len(sanctions)
            for law, sanctions in sorted(
                all_law_sanctions.items(), key=lambda x: -len(x[1])
            )
        },
    }

    report_path = KRR_DIR / "sanctions_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # ---------- summary ----------
    print("\n[4/4] SUMMARY")
    print("=" * 70)
    print(f"  Provisions analysed:       {total_provisions}")
    print(f"  With sanctions:            {provisions_with_sanctions}")
    print(f"  Total sanction records:    {total_sanction_count}")
    print(f"  Laws with sanctions:       {len(all_law_sanctions)}")
    print()
    print("  By sanction type:")
    for stype, count in sorted(stats_per_type.items(), key=lambda x: -x[1]):
        print(f"    {stype:25s}  {count}")
    print()
    print("  Penalty normalisation (issue #133):")
    print(f"    structured (amount+unit):  {penalty_structured}")
    print(f"    penalty_unparsed:          {penalty_unparsed}")
    print()
    print("  Top 5 laws by severity:")
    for entry in severity_index[:5]:
        print(f"    {entry['law']:45s}  severity={entry['max_severity']}  count={entry['sanction_count']}")
    print("=" * 70)

    # ----- coverage report -----
    # Note: triples_emitted counts SANCTION RECORDS (not RDF triples).
    # Each Sanction_* node typically projects 4-7 RDF triples
    # (@type, label, sanctionType, applicableProvision, optional
    # min/maxPenalty). Counting records matches the analytical unit
    # (cf. total_sanction_count in the existing sanctions_report.json).
    # extract_cross_references counts actual triples; sanctions counts
    # records; the difference is intentional.
    all_input_files = list(iter_peep_files())
    _kov_files = [p for p in all_input_files
                  if "regulations/kov/" in str(p)]

    _wall, _rate, _peak_mb = measure_runtime(_start, len(_files_processed))
    out_path = KRR_DIR / "reports" / "kov" / "extract_sanctions_coverage.json"
    write_coverage_report(
        CoverageReport(
            pipeline="extract_sanctions",
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(all_input_files),
            input_files_kov=len(_kov_files),
            files_processed=len(_files_processed),
            files_processed_kov=len(_files_processed_kov),
            files_with_output=len(_files_with_output),
            files_with_output_kov=len(_files_with_output_kov),
            files_skipped=_files_skipped,
            skip_reasons=_skip_reasons,
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        out_path,
    )
    print(f"\nCoverage report: {out_path}")
    print(f"  input_files_total: {len(all_input_files)} (KOV: {len(_kov_files)})")
    print(f"  files_with_output: {len(_files_with_output)} (KOV: {len(_files_with_output_kov)})")
    print(f"  triples_emitted (sanction records): {_triples} (KOV: {_triples_kov})")

    # Gate check (matches Layer 2a/2b convention).
    if len(_kov_files) >= 11000 and len(_files_with_output_kov) == 0:
        print("\nGATE FAIL: KOV files were processed but none produced sanction output.")
        return 1
    if _triples_kov == 0 and len(_kov_files) >= 11000:
        print("\nGATE FAIL: zero KOV sanction records emitted.")
        return 1
    print("\nGATE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
