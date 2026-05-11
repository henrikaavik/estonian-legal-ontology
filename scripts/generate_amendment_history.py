#!/usr/bin/env python3
"""
Build amendment chain relationships between Estonian laws and their amending acts.

This script:
1. Loads all existing law JSON-LD files from krr_outputs/*_peep.json
2. Loads draft legislation from krr_outputs/eelnoud/ for amendment relationships
3. Parses cached law XML files for amendment references (muutmismarge blocks)
4. Adds estleg:amendedBy / estleg:amends / estleg:amendmentDate to JSON-LD
5. Creates krr_outputs/amendments/ directory with amendment chain data
6. Generates amendment_history_report.json
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

from estleg_common import (
    build_globalid_xml_lookup,
    iter_peep_files,
    pair_peep_with_xml,
)
from kov_pipeline_coverage import (
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"
AMENDMENTS_DIR = KRR_DIR / "amendments"
EELNOUD_DIR = KRR_DIR / "eelnoud"

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


def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    """Get child element text by local tag name."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


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


def slugify(text: str) -> str:
    """Convert Estonian text to a filename-safe slug."""
    replacements = {
        "ä": "a", "ö": "o", "ü": "u", "õ": "o",
        "Ä": "A", "Ö": "O", "Ü": "U", "Õ": "O",
        "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:80]


def parse_date(value: str) -> str | None:
    """Parse date from XML, stripping timezone offsets."""
    if not value:
        return None
    from datetime import datetime
    value = value.strip()
    value = re.sub(r"\+\d{2}:\d{2}", "", value)
    value = re.sub(r"-\d{2}:\d{2}$", "", value)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def make_xsd_date(iso_date: str) -> dict:
    """Create an xsd:date typed value for JSON-LD."""
    return {"@value": iso_date, "@type": "xsd:date"}


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_law_files(*, failures: list[str] | None = None) -> dict[str, dict]:
    """
    Load all law JSON-LD files.
    Returns dict keyed by slug (stem without _peep).
    Records JSON / IO errors to the optional `failures` sink instead of
    swallowing them silently.
    """
    laws = {}
    for f in iter_peep_files():
        slug = f.stem.replace("_peep", "")
        try:
            with open(f, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            laws[slug] = {
                "path": f,
                "doc": doc,
                "title": extract_title(doc),
            }
        except (json.JSONDecodeError, OSError) as exc:
            if failures is not None:
                failures.append(
                    f"load_law_files | {f.name} | "
                    f"{type(exc).__name__}: {str(exc)[:80]}"
                )
    return laws


def extract_title(doc: dict) -> str:
    """Extract the law title from the ontology node's dc:source."""
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if "owl:Ontology" in types:
            return node.get("dc:source", node.get("rdfs:label", ""))
    return ""


def build_title_to_slug_map(laws: dict[str, dict]) -> dict[str, str]:
    """Build a mapping from law title (lowercased) to slug for matching."""
    mapping = {}
    for slug, info in laws.items():
        title = info["title"]
        if title:
            mapping[title.lower()] = slug
            # Also map partial name (without "seadus" suffix form variations)
            # e.g., "Alkoholiseadus" -> alkoholiseadus
            mapping[slugify(title)] = slug
    return mapping


def extract_amendments_from_xml(
    xml_path: Path,
    *,
    failures: list[str] | None = None,
) -> list[dict]:
    """
    Extract amendment references (muutmismarge blocks) from a law XML file.
    Each muutmismarge contains info about an amending act.
    """
    amendments: list[dict] = []
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError) as exc:
        if failures is not None:
            failures.append(
                f"extract_amendments_from_xml | {xml_path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
        return amendments

    for el in root.iter():
        if ln(el.tag) != "muutmismarge":
            continue

        amendment: dict = {
            "date": None,
            "rt_reference": None,
            "entry_into_force": None,
            "akt_viide": None,
        }

        # aktikuupaev — date of the amending act
        val = ct(el, "aktikuupaev")
        if val:
            amendment["date"] = parse_date(val)

        # joustumine — when the amendment entered into force
        val = ct(el, "joustumine")
        if val:
            amendment["entry_into_force"] = parse_date(val)

        # Build RT reference from avaldamismarge child
        avaldamismarge = None
        for child in el:
            if ln(child.tag) == "avaldamismarge":
                avaldamismarge = child
                break

        if avaldamismarge is not None:
            rt_osa = ct(avaldamismarge, "RTosa") or ""
            rt_aasta = ct(avaldamismarge, "RTaasta") or ""
            rt_nr = ct(avaldamismarge, "RTnr") or ""
            rt_artikkel = ct(avaldamismarge, "RTartikkel") or ""
            akt_viide = ct(avaldamismarge, "aktViide")

            if rt_osa or rt_aasta:
                amendment["rt_reference"] = f"{rt_osa}, {rt_aasta}, {rt_nr}, {rt_artikkel}".strip(", ")
            if akt_viide:
                amendment["akt_viide"] = akt_viide

        if amendment["date"] or amendment["rt_reference"]:
            amendments.append(amendment)

    return amendments


def extract_rt_references_from_text(
    xml_path: Path,
    *,
    failures: list[str] | None = None,
) -> list[str]:
    """
    Scan law text for inline RT publication references like "RT I, 28.06.2023, 10".
    These often indicate amendment acts referenced in the body text.
    """
    references: list[str] = []
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError) as exc:
        if failures is not None:
            failures.append(
                f"extract_rt_references_from_text | {xml_path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
        return references

    full_text = " ".join(root.itertext())
    # Pattern: RT I, YYYY, NR, ART or RT I, DD.MM.YYYY, NR
    # Roman numeral group is anchored with a word boundary so we accept
    # I/II/III but not stray "IIII" runs. Article portion is allowed to
    # include hyphenated lisa numbers ("RT III, 2009, 14, 89-90").
    pattern = (
        r"RT\s+(?:I{1,3})\b\s*,\s*"
        r"(?:\d{2}\.\d{2}\.)?\d{4}\s*,\s*"
        r"\d+(?:\s*[-,]\s*\d+)*"
    )
    matches = re.findall(pattern, full_text)
    # Deduplicate while preserving order
    seen = set()
    for m in matches:
        normalized = re.sub(r"\s+", " ", m.strip())
        if normalized not in seen:
            seen.add(normalized)
            references.append(normalized)
    return references


def load_draft_amendments(*, failures: list[str] | None = None) -> list[dict]:
    """
    Load draft legislation that are amendment bills from eelnoud directory.
    Returns list of dicts with draft_id, affected_law_name, etc.
    """
    drafts: list[dict] = []

    # Try the combined file first
    combined_path = EELNOUD_DIR / "eelnoud_combined.jsonld"
    if not combined_path.exists():
        return drafts

    try:
        with open(combined_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        if failures is not None:
            failures.append(
                f"load_draft_amendments | {combined_path.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
        return drafts

    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if "estleg:DraftLegislation" not in types:
            continue

        # Check if it is an amendment bill
        draft_type = node.get("estleg:draftType", {})
        if isinstance(draft_type, dict):
            type_id = draft_type.get("@id", "")
        else:
            type_id = str(draft_type)

        if type_id != "estleg:DraftType_AmendmentBill":
            continue

        affected = node.get("estleg:affectedLawName")
        if not affected:
            continue

        # Normalize to list
        if isinstance(affected, str):
            affected = [affected]

        draft_id = node.get("@id", "")
        label = node.get("rdfs:label", "")
        pub_date = node.get("estleg:publicationDate")
        if isinstance(pub_date, dict):
            pub_date = pub_date.get("@value")

        for law_name in affected:
            drafts.append({
                "draft_id": draft_id,
                "draft_label": label,
                "affected_law_name": law_name,
                "publication_date": pub_date,
            })

    return drafts


def _normalize_title(text: str) -> str:
    """Normalise a title for exact comparison (lower, collapse ws)."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def _title_tokens(text: str) -> set[str]:
    """Tokenise a title for Jaccard similarity (alnum tokens, lowered)."""
    return {t for t in re.findall(r"[\wäöüõšž]+", text.lower()) if t}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersect = a & b
    union = a | b
    return len(intersect) / len(union) if union else 0.0


def match_law_name_to_slug(
    law_name: str,
    title_map: dict[str, str],
    laws: dict[str, dict],
    *,
    failures: list[str] | None = None,
) -> str | None:
    """Match a law name (from a draft's affectedLawName) to an existing law slug.

    Strategy (strict-first):
      1. Exact normalised title match.
      2. Slugified exact match.
      3. Direct slug match.
      4. Suffix-normalised match ("X seaduse" -> "X seadus").
      5. Token-level Jaccard ≥ 0.9 — exactly one candidate wins.
         Multiple candidates above the threshold are AMBIGUOUS — bail
         out and surface to the failures sink rather than picking one.
    """
    norm_name = _normalize_title(law_name)
    if not norm_name:
        return None

    # 1. Exact normalised title
    if norm_name in title_map:
        return title_map[norm_name]

    # 2. Slugified exact match
    slug_name = slugify(law_name)
    if slug_name and slug_name in title_map:
        return title_map[slug_name]

    # 3. Direct slug match against laws keyed by slug
    if slug_name and slug_name in laws:
        return slug_name

    # 4. Suffix normalisation: "X seaduse" -> "X seadus"
    normalized = re.sub(r"seaduse$", "seadus", norm_name)
    normalized = re.sub(r"seadustiku$", "seadustik", normalized)
    if normalized in title_map:
        return title_map[normalized]
    slug_norm = slugify(normalized)
    if slug_norm and slug_norm in title_map:
        return title_map[slug_norm]
    if slug_norm and slug_norm in laws:
        return slug_norm

    # 5. Token Jaccard fallback — strict, NO substring fallthrough.
    name_tokens = _title_tokens(law_name)
    if not name_tokens:
        return None
    candidates: list[tuple[float, str, str]] = []
    for title_lower, slug in title_map.items():
        score = _jaccard(name_tokens, _title_tokens(title_lower))
        if score >= 0.9:
            candidates.append((score, title_lower, slug))

    if not candidates:
        return None

    # Distinct slugs above threshold => ambiguous match.
    distinct_slugs = {slug for _, _, slug in candidates}
    if len(distinct_slugs) > 1:
        if failures is not None:
            top = sorted(candidates, key=lambda x: -x[0])[:3]
            failures.append(
                "match_law_name_to_slug | ambiguous | "
                f"input={law_name!r} | candidates="
                f"{[(s, slug) for s, _, slug in top]}"
            )
        return None

    # Exactly one slug at >= 0.9 wins.
    return candidates[0][2]


def clear_amended_by_from_file(
    filepath: Path,
    *,
    failures: list[str] | None = None,
) -> bool:
    """
    Remove estleg:amendedBy from all nodes in a law JSON-LD file.
    Returns True if the file was modified.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        if failures is not None:
            failures.append(
                f"clear_amended_by_from_file | {filepath.name} | "
                f"{type(exc).__name__}: {str(exc)[:80]}"
            )
        return False

    modified = False
    for node in data.get("@graph", []):
        if "estleg:amendedBy" in node:
            del node["estleg:amendedBy"]
            modified = True

    if modified:
        save_json(filepath, data)
    return modified


def _stable_amend_suffix(amend: dict) -> str:
    """Compute a stable IRI suffix for an XML amendment record.

    Hashes ``rt_reference`` (preferred — unique RT publication ref) or,
    when missing, the (date, entry_into_force) pair so the resulting
    Amendment IRI doesn't shift between regenerations just because one
    earlier amendment was inserted upstream.
    """
    payload = amend.get("rt_reference") or "|".join(
        [
            amend.get("date") or "",
            amend.get("entry_into_force") or "",
            amend.get("akt_viide") or "",
        ]
    )
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()
    return digest[:10]


def _amend_sort_key(amend: dict) -> str:
    """Return a YYYY-MM-DD-shaped string for chronological sorting.

    Prefer ``entry_into_force`` (when the amendment took legal effect),
    fall back to the act's signing date. Empty strings sort first; we
    push them to the end with a sentinel.
    """
    eif = amend.get("entry_into_force") or ""
    sig = amend.get("date") or ""
    primary = eif or sig
    return primary or "9999-99-99"


def main() -> int:
    AMENDMENTS_DIR.mkdir(parents=True, exist_ok=True)
    EELNOUD_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Estonian Legal Ontology - Generate Amendment History")
    print("=" * 70)

    _start = time.perf_counter()

    # Coverage counters — initialised early so all downstream steps
    # can update them. Amendment-history is a hybrid pipeline: per-act
    # estleg:amendedBy writes (in-place) PLUS aggregate report files.
    _files_processed = 0
    _files_processed_kov = 0
    _files_with_output = 0
    _files_with_output_kov = 0
    # Coverage triples are split into the two surfaces this script
    # writes so the totals don't lie:
    #   - amendedBy_link_total: estleg:amendedBy refs stamped on each
    #     act's ontology node (one per chain entry).
    #   - amendment_event_triples: properties (@type, amends,
    #     amendmentDate, entryIntoForce, rtReference, label, etc.)
    #     emitted on each Amendment / AmendmentLink node.
    _amendedBy_link_total = 0
    _amendment_event_triples = 0
    _amendedBy_link_total_kov = 0
    _amendment_event_triples_kov = 0
    _fallback_hits = 0
    _unresolved = 0
    _failures: list[str] = []
    _skip_reasons: dict[str, int] = {}

    # Step 0: Clear existing amendedBy references
    print("\n[0/5] Clearing existing estleg:amendedBy from all law files...")
    cleared_count = 0
    for peep_file in iter_peep_files():
        if clear_amended_by_from_file(peep_file, failures=_failures):
            cleared_count += 1
    print(f"  Cleared estleg:amendedBy from {cleared_count} files")

    # Step 1: Load all law files
    print("\n[1/5] Loading law JSON-LD files...")
    laws = load_law_files(failures=_failures)
    print(f"  Loaded {len(laws)} law files")

    title_map = build_title_to_slug_map(laws)
    print(f"  Built title-to-slug mapping with {len(title_map)} entries")

    # Step 2: Extract amendments from XML files (paired per-peep via
    # globalId, with a slug fallback for legacy laws that have no
    # estleg:globalId on their act node).
    print("\n[2/5] Extracting amendment references from XML files...")
    xml_lookup = build_globalid_xml_lookup(DATA_DIR)
    print(f"  Indexed {len(xml_lookup)} XML files by globalId")

    amendments_by_slug: dict[str, list[dict]] = {}
    rt_refs_by_slug: dict[str, list[str]] = {}
    total_amendments = 0
    paired_count = 0
    unpaired_count = 0
    # Track which slugs were paired+extracted (regardless of whether
    # the XML had any muutmismarge blocks) — feeds _files_processed
    # in the coverage report.
    paired_slugs: set[str] = set()
    # Set of canonical XML paths that came via the globalId lookup —
    # used to detect when the slug fallback fired.
    _lookup_paths = {str(p) for p in xml_lookup.values()}

    for slug, info in laws.items():
        # info["path"] is the peep file path; pair via globalId, with
        # slug fallback for laws that don't carry estleg:globalId.
        xml_path = pair_peep_with_xml(
            info["path"], xml_lookup, data_dir=DATA_DIR
        )
        if xml_path is None:
            unpaired_count += 1
            continue
        paired_count += 1
        paired_slugs.add(slug)
        if str(xml_path) not in _lookup_paths:
            _fallback_hits += 1
        amendments = extract_amendments_from_xml(xml_path, failures=_failures)
        if amendments:
            amendments_by_slug[slug] = amendments
            total_amendments += len(amendments)
        rt_refs = extract_rt_references_from_text(xml_path, failures=_failures)
        if rt_refs:
            rt_refs_by_slug[slug] = rt_refs

    _unresolved = unpaired_count

    print(f"  Paired XML for {paired_count} of {len(laws)} laws "
          f"({unpaired_count} unpaired — see coverage report).")
    print(f"  Found {total_amendments} amendment references across {len(amendments_by_slug)} laws")
    print(f"  Found RT references in {len(rt_refs_by_slug)} laws")

    # Step 3: Load draft amendments
    print("\n[3/5] Loading draft legislation amendment relationships...")
    draft_amendments = load_draft_amendments(failures=_failures)
    print(f"  Found {len(draft_amendments)} amendment bill entries")

    # Match drafts to existing laws.
    #
    # Drafts often list the affected law multiple times (e.g. once in
    # genitive form "X seaduse" and once with the full title "X seaduse
    # muutmise ja sellega seonduvalt teiste seaduste muutmise seadus").
    # Both forms can resolve to the same target slug, which would
    # produce duplicate AmendmentLink @ids when chain_entries are built.
    # Dedupe by draft_id within each target slug to avoid that.
    draft_matches: dict[str, list[dict]] = {}  # target law slug -> list of amending drafts
    seen_per_target: dict[str, set[str]] = {}
    matched_drafts = 0
    unmatched_drafts = 0

    for da in draft_amendments:
        target_slug = match_law_name_to_slug(
            da["affected_law_name"], title_map, laws, failures=_failures
        )
        if target_slug:
            seen = seen_per_target.setdefault(target_slug, set())
            if da["draft_id"] in seen:
                # Duplicate (same draft → same target via a second
                # affected_law_name spelling). Skip the duplicate.
                continue
            seen.add(da["draft_id"])
            draft_matches.setdefault(target_slug, []).append(da)
            matched_drafts += 1
        else:
            unmatched_drafts += 1

    print(f"  Matched {matched_drafts} drafts to existing laws")
    print(f"  Unmatched: {unmatched_drafts} (law not in ontology)")

    # Step 4: Build amendment chain data and enrich JSON-LD files.
    #
    # Multipart laws (e.g. võlaõigusseadus_osa1..osa9) all share the
    # same canonical XML; they amend "Võlaõigusseadus" as a whole.
    # Group by base_slug (= slug with trailing ``_osaN`` stripped) so
    # one chain document covers every part — separate per-part files
    # would race-write the same path and lose data.
    print("\n[4/5] Building amendment chains and enriching JSON-LD files...")
    enriched = 0
    amendment_chains: list[dict] = []

    # Coverage: every paired peep counts as processed regardless of
    # whether it ends up grouped under a base_slug.
    for slug, info in sorted(laws.items()):
        is_kov = "regulations/kov" in str(info["path"])
        if slug in paired_slugs:
            _files_processed += 1
            if is_kov:
                _files_processed_kov += 1

    # Group paired laws by base_slug (drop trailing _osaN suffix).
    groups: dict[str, list[tuple[str, dict]]] = {}
    for slug, info in laws.items():
        base_slug = re.sub(r"_osa\d+$", "", slug)
        groups.setdefault(base_slug, []).append((slug, info))

    for base_slug in sorted(groups):
        members = groups[base_slug]

        # Pull XML amendments / drafts for the group. All parts of a
        # multipart law share the same canonical XML so any member's
        # entry is canonical; pick the first non-empty one.
        xml_amendments: list[dict] = []
        for slug, _info in members:
            cand = amendments_by_slug.get(slug)
            if cand:
                xml_amendments = cand
                break
        if not xml_amendments:
            xml_amendments = amendments_by_slug.get(base_slug, [])

        drafts: list[dict] = []
        seen_draft_ids: set[str] = set()
        for slug, _info in members:
            for da in draft_matches.get(slug, []):
                if da["draft_id"] in seen_draft_ids:
                    continue
                seen_draft_ids.add(da["draft_id"])
                drafts.append(da)
        for da in draft_matches.get(base_slug, []):
            if da["draft_id"] in seen_draft_ids:
                continue
            seen_draft_ids.add(da["draft_id"])
            drafts.append(da)

        if not xml_amendments and not drafts:
            for slug, _info in members:
                if slug in paired_slugs:
                    _skip_reasons["no_amendments_found"] = (
                        _skip_reasons.get("no_amendments_found", 0) + 1
                    )
            continue

        # Sort xml_amendments chronologically (entry_into_force first,
        # falling back to signing date) so chain index numbers track
        # legal-effect order, not arbitrary XML document order.
        xml_amendments_sorted = sorted(xml_amendments, key=_amend_sort_key)

        # Build chain entries ONCE for the whole base_slug.
        # Each Amendment node gets a stable IRI suffix derived from
        # its rt_reference so regenerations don't reshuffle IDs.
        chain_entries: list[dict] = []
        # Use the first member's ontology IRI as the canonical "amends"
        # target for the chain document. (Each part still gets its own
        # estleg:amendedBy — see the per-member loop below.)
        canonical_ontology_id = ""
        canonical_title = members[0][1]["title"]
        for _slug, _info in members:
            for node in _info["doc"].get("@graph", []):
                if "owl:Ontology" in (node.get("@type") or []):
                    canonical_ontology_id = node.get("@id", "")
                    if canonical_title is None or not canonical_title:
                        canonical_title = (
                            node.get("dc:source") or node.get("rdfs:label") or ""
                        )
                    break
            if canonical_ontology_id:
                break

        seen_amend_ids: set[str] = set()
        for amend in xml_amendments_sorted:
            suffix = _stable_amend_suffix(amend)
            amend_id = f"estleg:Amendment_{sanitize_id(base_slug)}_{suffix}"
            # Collisions on hash prefix are extremely unlikely but
            # defended against here so re-runs remain idempotent.
            disambig = 1
            base_id = amend_id
            while amend_id in seen_amend_ids:
                disambig += 1
                amend_id = f"{base_id}_{disambig}"
            seen_amend_ids.add(amend_id)

            amend_node: dict = {
                "@id": amend_id,
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
            }
            if canonical_ontology_id:
                amend_node["estleg:amends"] = {"@id": canonical_ontology_id}

            if amend.get("date"):
                amend_node["estleg:amendmentDate"] = make_xsd_date(amend["date"])
            if amend.get("entry_into_force"):
                amend_node["estleg:entryIntoForce"] = make_xsd_date(amend["entry_into_force"])
            if amend.get("rt_reference"):
                amend_node["estleg:rtReference"] = amend["rt_reference"]
                amend_node["rdfs:label"] = f"Muudatus: {amend['rt_reference']}"
            else:
                amend_node["rdfs:label"] = f"Muudatus {amend.get('date', 'unknown')}"
            chain_entries.append(amend_node)

        # Build draft entries (no chronological ordering — drafts use
        # publication_date if available; we keep their input order so
        # IDs remain stable across reruns of the same input set).
        for da in drafts:
            draft_id = da["draft_id"]
            link_node: dict = {
                "@id": (
                    f"estleg:AmendmentLink_"
                    f"{sanitize_id(draft_id.replace('estleg:', ''))}_"
                    f"{sanitize_id(base_slug)}"
                ),
                "@type": ["owl:NamedIndividual", "estleg:AmendmentEvent"],
                "rdfs:label": f"Eelnõu muudatus: {da['draft_label']}",
                "estleg:amendingDraft": {"@id": draft_id},
            }
            if canonical_ontology_id:
                link_node["estleg:amends"] = {"@id": canonical_ontology_id}
            if da.get("publication_date"):
                link_node["estleg:amendmentDate"] = make_xsd_date(da["publication_date"])
            chain_entries.append(link_node)

        # Stamp estleg:amendedBy on every member's ontology node so
        # each part's peep file references the shared chain. Track
        # per-member flags so coverage counts each member fairly.
        amended_by_refs = [{"@id": e["@id"]} for e in chain_entries]
        # AmendmentLink nodes for drafts also count as amendedBy.
        # (They're already in chain_entries.)
        # Note: amended_by_refs index parallels chain_entries.

        members_enriched_for_kov = 0
        members_enriched = 0
        for slug, info in members:
            doc = info["doc"]
            graph = doc.get("@graph", [])
            ontology_node = None
            for node in graph:
                if "owl:Ontology" in (node.get("@type") or []):
                    ontology_node = node
                    break
            if ontology_node is None and graph:
                ontology_node = graph[0]
            if ontology_node is None:
                continue

            ctx = doc.get("@context", {})
            if isinstance(ctx, dict) and "dcterms" not in ctx:
                ctx["dcterms"] = "http://purl.org/dc/terms/"
                doc["@context"] = ctx

            ontology_node["estleg:amendedBy"] = amended_by_refs
            save_json(info["path"], doc)
            enriched += 1
            members_enriched += 1

            is_member_kov = "regulations/kov" in str(info["path"])
            n_links = len(amended_by_refs)
            _amendedBy_link_total += n_links
            _files_with_output += 1
            if is_member_kov:
                _amendedBy_link_total_kov += n_links
                _files_with_output_kov += 1
                members_enriched_for_kov += 1

        # Amendment event triples: count properties on each chain node.
        if chain_entries:
            event_triples = sum(
                # @id + each property key (excluding @id itself counted once via key list)
                len(node) for node in chain_entries
            )
            _amendment_event_triples += event_triples
            if members_enriched_for_kov > 0:
                # Attribute event triples proportionally to KOV — for a
                # purely KOV chain, all event triples count toward KOV.
                if members_enriched > 0:
                    kov_share = int(
                        round(event_triples * members_enriched_for_kov / members_enriched)
                    )
                else:
                    kov_share = 0
                _amendment_event_triples_kov += kov_share

        # Save ONE chain doc per base_slug.
        if chain_entries:
            chain_doc = {
                "@context": CONTEXT,
                "@graph": [
                    {
                        "@id": f"estleg:AmendmentChain_{sanitize_id(base_slug)}",
                        "@type": ["owl:Ontology"],
                        "rdfs:label": f"Muudatuste ahel: {canonical_title}",
                        "dc:source": canonical_title,
                        "estleg:totalAmendments": {
                            "@value": str(len(chain_entries)),
                            "@type": "xsd:integer",
                        },
                    },
                    *chain_entries,
                ],
            }
            chain_path = AMENDMENTS_DIR / f"amendments_{base_slug}.json"
            save_json(chain_path, chain_doc)
            amendment_chains.append({
                "law": canonical_title,
                "base_slug": base_slug,
                "parts": [s for s, _ in members],
                "xml_amendments": len(xml_amendments_sorted),
                "draft_amendments": len(drafts),
                "total": len(chain_entries),
                "file": chain_path.name,
            })

    print(f"  Enriched {enriched} law files with amendment references")
    print(f"  Created {len(amendment_chains)} amendment chain files")

    # Step 5: Generate report
    print("\n[5/5] Generating amendment_history_report.json...")

    # Find most amended laws
    amendment_chains_sorted = sorted(amendment_chains, key=lambda x: x["total"], reverse=True)

    # Multipart laws are now already aggregated into one chain doc per
    # base_slug, so this block just maps law title -> stats. Kept for
    # backwards-compatible report shape.
    deduped_most_amended: dict[str, dict] = {}
    for c in amendment_chains_sorted:
        canon_name = c["law"]
        if canon_name in deduped_most_amended:
            entry = deduped_most_amended[canon_name]
            entry["total_amendments"] += c["total"]
            entry["xml_amendments"] += c["xml_amendments"]
            entry["draft_amendments"] += c["draft_amendments"]
            entry["_part_count"] += 1
        else:
            deduped_most_amended[canon_name] = {
                "law": canon_name,
                "total_amendments": c["total"],
                "xml_amendments": c["xml_amendments"],
                "draft_amendments": c["draft_amendments"],
                "_part_count": 1,
            }
    most_amended_list = sorted(
        deduped_most_amended.values(), key=lambda x: x["total_amendments"], reverse=True
    )
    for entry in most_amended_list:
        del entry["_part_count"]

    total_triples = _amendedBy_link_total + _amendment_event_triples
    total_triples_kov = _amendedBy_link_total_kov + _amendment_event_triples_kov

    report = {
        "generated": date.today().isoformat(),
        "summary": {
            "total_laws_analyzed": len(laws),
            "laws_with_amendments": len(amendment_chains),
            "total_amendment_references": total_amendments,
            "draft_amendments_matched": matched_drafts,
            "draft_amendments_unmatched": unmatched_drafts,
            "laws_enriched": enriched,
            # Split coverage triples — see comment near the counter
            # initialisations for why both surfaces are exposed.
            "amendedBy_link_total": _amendedBy_link_total,
            "amendment_event_triples": _amendment_event_triples,
            "triples_total": total_triples,
            "failures": len(_failures),
        },
        "most_amended_laws": most_amended_list[:30],
        "amendment_chains": amendment_chains_sorted,
    }

    report_path = KRR_DIR / "amendment_history_report.json"
    save_json(report_path, report)

    # Surface failures aggressively — silent swallowing was the root
    # cause of issue #173's bare-except ladder.
    if _failures:
        print(
            f"\n[P1] generate_amendment_history: {len(_failures)} failure(s) "
            "logged during run — see coverage report for samples.",
            file=sys.stderr,
        )

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Laws analyzed:            {len(laws)}")
    print(f"  Laws with amendments:     {len(amendment_chains)}")
    print(f"  Total amendment refs:     {total_amendments}")
    print(f"  Draft amendments matched: {matched_drafts}")
    print(f"  Law files enriched:       {enriched}")
    print(f"  Amendment chain files:    {len(amendment_chains)}")
    print(f"  amendedBy link triples:   {_amendedBy_link_total}")
    print(f"  Amendment event triples:  {_amendment_event_triples}")
    if most_amended_list:
        print("\n  Top 5 most amended laws:")
        for c in most_amended_list[:5]:
            print(f"    {c['law']}: {c['total_amendments']} amendments")
    print(f"\n  Report: {report_path.relative_to(REPO_ROOT)}")
    print(f"  Chains: {AMENDMENTS_DIR.relative_to(REPO_ROOT)}/")
    print("=" * 70)

    # ---------- coverage report ----------
    # Hybrid pipeline: per-act in-place amendedBy writes PLUS aggregate
    # report files. Counters track per-peep activity (paired +
    # extracted) and per-peep output (amendedBy triples written).
    all_peep_files = iter_peep_files()
    kov_files = [f for f in all_peep_files if "regulations/kov" in str(f)]
    _wall, _rate, _peak_mb = measure_runtime(_start, _files_processed)
    write_coverage_report(
        CoverageReport(
            pipeline="generate_amendment_history",
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(all_peep_files),
            input_files_kov=len(kov_files),
            files_processed=_files_processed,
            files_processed_kov=_files_processed_kov,
            files_with_output=_files_with_output,
            files_with_output_kov=_files_with_output_kov,
            files_skipped=sum(_skip_reasons.values()),
            skip_reasons=_skip_reasons,
            triples_emitted=total_triples,
            triples_emitted_kov=total_triples_kov,
            fallback_hits=_fallback_hits,
            unresolved_references=_unresolved,
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        REPO_ROOT / "krr_outputs" / "reports" / "kov"
        / "generate_amendment_history_coverage.json",
    )
    return 0


if __name__ == "__main__":
    main()
