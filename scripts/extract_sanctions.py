#!/usr/bin/env python3
"""
Extract penalties and sanctions from law text.

Scans estleg:summary text of each provision for Estonian sanction patterns
(imprisonment, fines, arrest, coercive payments) and creates estleg:Sanction
nodes linked to the originating provision.

Outputs:
  - krr_outputs/sanctions/  (per-law sanction JSON-LD files)
  - krr_outputs/sanctions_report.json
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
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
    """Build a short human-readable provision reference like 'KarS § 121'."""
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
    return law_abbr or node.get("@id", "unknown")


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


def extract_imprisonment(text: str) -> list[dict]:
    """Detect imprisonment sentences.

    Handles both digit-based ("kuni 5 aastase vangistusega") and
    Estonian word-based ("kuni viieaastase vangistusega") patterns.
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

    # Range: "kuue- kuni viieteistaastase vangistusega"
    for m in re.finditer(
        r"(\w+)-?\s+kuni\s+(\w+)aasta(?:se)?\s+vangistus",
        text, re.IGNORECASE,
    ):
        min_val = _parse_number(m.group(1))
        max_val = _parse_number(m.group(2))
        if min_val is not None and max_val is not None:
            results.append({
                "sanction_type": "imprisonment",
                "min_penalty": f"{min_val} years",
                "max_penalty": f"{max_val} years",
            })

    # Max only (word): "kuni viieaastase vangistusega"
    for m in re.finditer(
        r"kuni\s+(\w+)aasta(?:se)?\s+vangistus",
        text, re.IGNORECASE,
    ):
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
        min_s = f"{m.group(1)} years"
        max_s = f"{m.group(2)} years"
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

    # Fallback: "vangistus(ega) N"
    for m in re.finditer(
        r"vangistus(?:ega)?\s+(\d+)", text, re.IGNORECASE,
    ):
        val = m.group(1)
        penalty_str = f"{val} years" if int(val) <= 20 else f"{val} months"
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
    "või kuni N-aastase vangistusega").  We set maxPenalty to the statutory
    ceiling of "500 daily rates" so the field is never empty.
    """
    results: list[dict] = []
    if re.search(r"rahalise?\s+karistus", text, re.IGNORECASE):
        results.append({
            "sanction_type": "pecuniary_punishment",
            "max_penalty": "500 daily rates",
        })
    if not results and re.search(
        r"karistatakse\s+rahalise\s+karistusega", text, re.IGNORECASE
    ):
        results.append({
            "sanction_type": "pecuniary_punishment",
            "max_penalty": "500 daily rates",
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
    statutory maximum of 30 days.
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

    # Generic arrest mention without explicit days → statutory max 30 days
    if not results and re.search(r"\barest", text, re.IGNORECASE):
        results.append({
            "sanction_type": "arrest",
            "max_penalty": "30 days",
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
    """Run all extractors and return deduplicated sanction records."""
    all_sanctions: list[dict] = []
    seen: set[str] = set()
    for extractor in ALL_EXTRACTORS:
        for s in extractor(text):
            key = f"{s['sanction_type']}|{s.get('max_penalty', '')}|{s.get('min_penalty', '')}"
            if key not in seen:
                seen.add(key)
                all_sanctions.append(s)
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

            sanction_refs: list[dict] = []
            for s in sanctions:
                total_sanction_count += 1
                stype = s["sanction_type"]

                if "max_penalty" not in s:
                    fallback = _try_extract_penalty_from_summary(summary, stype)
                    if fallback:
                        s["max_penalty"] = fallback

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
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "summary": {
            "total_law_files": len(law_files),
            "total_provisions_with_text": total_provisions,
            "provisions_with_sanctions": provisions_with_sanctions,
            "total_sanction_records": total_sanction_count,
            "laws_with_sanctions": len(all_law_sanctions),
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
