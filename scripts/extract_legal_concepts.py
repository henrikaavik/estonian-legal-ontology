#!/usr/bin/env python3
"""
Extract defined legal terms from Estonian laws and build a concept cross-reference graph.

This script:
1. Loads all law JSON-LD files and their source XML from data/riigiteataja/
2. Detects definition sections ("Mõisted", "Põhimõisted", etc.)
3. Extracts term-definition pairs from numbered definition paragraphs
4. Creates estleg:LegalConcept nodes with SKOS labels and definitions
5. Matches identical/similar terms across laws with skos:exactMatch / skos:closeMatch
6. Outputs krr_outputs/concepts/ with combined and cross-reference data
"""

from __future__ import annotations

import json
import re
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
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
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"
CONCEPTS_DIR = KRR_DIR / "concepts"
CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)

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

# Definition section titles to look for (case-insensitive matching)
DEFINITION_TITLES = {
    "mõisted",
    "seaduses kasutatavad mõisted",
    "põhimõisted",
    "mõistete selgitus",
    "terminid",
    "terminid ja mõisted",
    "mõisted ja lühendid",
}

# Text patterns that introduce definitions within a paragraph
DEFINITION_INTRO_PATTERNS = [
    r"käesolevas\s+seaduses\s+kasutatakse\s+järgmises\s+tähenduses",
    r"käesoleva\s+seaduse\s+tähenduses",
    r"käesolevas\s+seaduses\s+tähendab",
    r"käesolevas\s+seaduses\s+mõistetakse",
    r"käesolevas\s+seadustikus\s+kasutatakse",
    r"käesoleva\s+seadustiku\s+tähenduses",
    r"käesoleva\s+seaduse\s+mõistes",
]

# Pattern for extracting numbered definitions: "1) term – definition;".
# Finding 1 (#171): the previous body fragment ``(.+?)(?:;|$)`` truncated
# multi-clause definitions at the FIRST semicolon, which silently
# discarded long-form definitions like
# ``KMS § 2 1) imporditud kaup — kaup, mille kohta on koostatud
# ekspordideklaratsioon; sealhulgas...``.
# Anchor on the next ``\d+\)`` numbered marker (lookahead) instead, then
# strip a trailing ``;`` in extract_definitions_from_text. End-of-string
# is preserved so the last definition in a list still terminates cleanly.
DEFINITION_PATTERN = re.compile(
    r"(\d+)\)\s*"                     # number and closing paren
    r"([\wäöüõšžÄÖÜÕŠŽ]+(?:\s+[\wäöüõšžÄÖÜÕŠŽ-]+)*?)"  # term (one or more words)
    r"\s*[–\-—]\s*"                   # dash separator
    r"(.+?)(?=\s*\d+\)|$)",           # definition text until next numbered marker or end
    re.UNICODE | re.DOTALL,
)


def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    """Get child element text by local tag name."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


def collect_full_text(el: ET.Element) -> str:
    """Collect all text content from an element and its children."""
    text = " ".join(el.itertext())
    return re.sub(r"\s+", " ", text).strip()


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


def edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return edit_distance(b, a)
    if len(b) == 0:
        return len(a)

    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr_row = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr_row.append(min(
                curr_row[j] + 1,       # insertion
                prev_row[j + 1] + 1,   # deletion
                prev_row[j] + cost,    # substitution
            ))
        prev_row = curr_row
    return prev_row[len(b)]


def save_json(filepath: Path, doc: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def is_definition_paragraph(par_el: ET.Element) -> bool:
    """Check if a paragraph is a definition section based on its title."""
    title = ct(par_el, "paragrahvPealkiri")
    if title:
        title_lower = title.lower().strip()
        for def_title in DEFINITION_TITLES:
            if def_title in title_lower:
                return True
    return False


def has_definition_intro(text: str) -> bool:
    """Check if text contains a definition introduction pattern."""
    text_lower = text.lower()
    for pattern in DEFINITION_INTRO_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def extract_definitions_from_text(text: str) -> list[tuple[str, str, str]]:
    """Extract term-definition pairs from text.

    Returns list of (number, term, definition). Trailing ``;`` and
    whitespace are stripped from the definition (Finding 1 #171: the
    pattern now anchors on the next numbered marker instead of stopping
    at the first ``;``, so the trailing-semicolon cleanup happens here).
    """
    results = []
    for match in DEFINITION_PATTERN.finditer(text):
        number = match.group(1)
        term = match.group(2).strip()
        definition = match.group(3).strip()
        # Finding 1 (#171): collapse internal whitespace runs (the regex
        # is now DOTALL, so newlines inside multi-line definitions are
        # captured); strip trailing punctuation that originally
        # terminated the clause.
        definition = re.sub(r"\s+", " ", definition)
        definition = definition.rstrip("; \t\r\n")

        # Skip overly short terms or definitions
        if len(term) < 2 or len(definition) < 5:
            continue
        # Skip if term looks like a number or single letter
        if re.match(r"^\d+$", term):
            continue

        results.append((number, term, definition))

    return results


def build_par_to_iri_lookup(doc: dict) -> dict[str, str | list[str]]:
    """Build a (par_nr | par_display) → provision @id lookup from a
    loaded peep file's @graph.

    Used to bridge XML-extracted concepts (which know paragraphs by
    their numeric par_nr or display form like "§ 1.") to the
    JSON-LD provision IRIs (which use Reg_<terviktekstId>_Par_<n>
    for KOV files — the actual @id is decoupled from the filename
    slug).

    The lookup keys both forms so the XML extractor can use whichever
    field it has: paragrahvNr ("1") OR kuvatavNr ("§ 1.").

    Finding 7 (#171): the previous version silently overwrote on
    duplicate par_nr (KOV regs with annexed lisad reuse paragraph
    numbers across different chapters). Collisions are now flagged by
    storing the value as a list of @ids; downstream callers can pick by
    chapter/section context or refuse to bridge — see
    ``resolve_provision_id`` for the resolution logic.
    """
    lookup: dict[str, str | list[str]] = {}

    def _set(key: str, value: str) -> None:
        existing = lookup.get(key)
        if existing is None:
            lookup[key] = value
        elif isinstance(existing, list):
            if value not in existing:
                existing.append(value)
        elif existing != value:
            # Promote single @id to multi-@id list. Preserves insertion
            # order so consumers can still pick the first when context
            # disambiguation isn't available.
            lookup[key] = [existing, value]

    for node in doc.get("@graph", []):
        par_display = node.get("estleg:paragrahv")
        if not par_display:
            continue
        node_id = node.get("@id")
        if not node_id:
            continue
        # Display form: "§ 1." → also key under "1"
        _set(par_display, node_id)
        # Strip "§ " prefix and trailing "." to get the bare number
        bare = par_display.lstrip("§").strip().rstrip(".")
        if bare:
            _set(bare, node_id)
    return lookup


def resolve_provision_id(
    par_to_iri: dict[str, str | list[str]] | None,
    par_nr: str,
    par_display: str,
) -> tuple[str | None, bool]:
    """Resolve an XML par_nr/par_display to a peep @id.

    Returns ``(@id, ambiguous_flag)``. ``ambiguous_flag`` is True when
    the lookup hit a multi-value entry — caller can then decide whether
    to refuse the bridge or pick the first.

    Finding 7 (#171): the lookup may now store a list of @ids on
    collision. We pick the FIRST entry (preserves insertion order — i.e.
    the first chapter's provision with that par_nr) and surface the
    ambiguity to the caller via the flag.
    """
    if par_to_iri is None:
        return None, False
    raw = par_to_iri.get(par_nr) or par_to_iri.get(par_display)
    if raw is None:
        return None, False
    if isinstance(raw, list):
        return (raw[0] if raw else None), len(raw) > 1
    return raw, False


def extract_concepts_from_xml(
    xml_path: Path,
    law_title: str,
    law_slug: str,
    par_to_iri: dict[str, str | list[str]] | None = None,
) -> list[dict]:
    """
    Extract legal concept definitions from a law's XML file.
    Returns list of concept dicts.

    When ``par_to_iri`` is provided, ``provision_id`` is sourced from
    the lookup (bridging XML par_nr/par_display to the peep file's
    actual @id). Falls back to the slug-derived form when no lookup
    is provided OR a particular par_nr/par_display is unmapped — this
    keeps backwards-compat with laws-only callers.

    Finding 7 (#171): each emitted concept now carries an
    ``ambiguous_par_iri`` flag set to True when the lookup hit a
    multi-value entry (par_nr collision across chapters / lisad). Main
    surfaces the count in the coverage report.
    """
    concepts = []

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, OSError):
        return concepts

    # Find paragraphs that contain definitions
    for el in root.iter():
        if ln(el.tag) != "paragrahv":
            continue

        par_nr = ct(el, "paragrahvNr") or ""
        par_display = ct(el, "kuvatavNr") or f"§ {par_nr}"

        # Check if this paragraph is a definition section
        is_def_section = is_definition_paragraph(el)
        full_text = collect_full_text(el)

        if not is_def_section:
            # Also check if the text contains definition intro patterns
            if not has_definition_intro(full_text):
                continue

        # Extract definitions from the paragraph text
        defs = extract_definitions_from_text(full_text)

        provision_id, ambiguous = resolve_provision_id(
            par_to_iri, par_nr, par_display,
        )
        if provision_id is None:
            # Bridge XML par_nr to peep @id when a lookup is provided.
            # Falls back to the slug-derived form so existing law-only
            # callers still work — Layer 2a callers should always pass
            # par_to_iri.
            provision_id = f"estleg:{sanitize_id(law_slug)}_Par_{sanitize_id(par_nr)}"

        for number, term, definition in defs:
            concept_id = f"estleg:Concept_{sanitize_id(law_slug)}_{sanitize_id(term)}"
            concepts.append({
                "concept_id": concept_id,
                "term": term,
                "term_lower": term.lower(),
                "definition": definition,
                "law_title": law_title,
                "law_slug": law_slug,
                "paragraph": par_display,
                "par_nr": par_nr,
                "provision_id": provision_id,
                "def_number": number,
                "ambiguous_par_iri": ambiguous,
            })

    return concepts


def load_law_files() -> list[dict]:
    """Load every peep JSON-LD file (laws + state regs + KOV) and return their metadata.

    Layer 2a: the historic root-only filter (``f.parent != KRR_DIR``) was
    removed so KOV peeps under ``regulations/kov/<issuer>/`` and state
    regulations under ``regulations/riik/`` are now included alongside
    top-level laws.
    """
    laws = []
    for f in iter_peep_files():
        slug = f.stem.replace("_peep", "")
        try:
            with open(f, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        title = ""
        for node in doc.get("@graph", []):
            types = node.get("@type", [])
            if "owl:Ontology" in types:
                title = jsonld_text(node.get("dc:source", node.get("rdfs:label", "")))
                break

        laws.append({
            "slug": slug,
            "title": title,
            "path": f,
        })
    return laws


# Issue #171 Finding 8: failure-rate threshold above which main() returns
# a non-zero exit code. Tunable; the default mirrors the implicit "if any
# files failed, treat the run as red" policy that the previous bare
# ``except Exception`` swallowed silently.
_FAILURE_EXIT_THRESHOLD = 50


def _disambiguate_concept_id(
    concept: dict,
    seen_concept_ids: set[str],
) -> str:
    """Disambiguate a concept id against ``seen_concept_ids`` using a
    monotonic suffix.

    Finding 2 (#171): the previous code used the within-paragraph
    ``def_number`` as a disambiguator, so two laws each defining 'isik'
    in their § 2 1) clauses would collide AND get the same ``_1``
    suffix, then the second was silently dropped. The new strategy:

      1. If the base id is fresh, return it.
      2. Otherwise compose ``base_<law_slug>_<par_nr>_<def_number>`` —
         this triple is unique per (law, paragraph, definition number).
      3. If THAT also collides, append ``_2``, ``_3``, ... until the id
         is fresh.
    """
    base = concept["concept_id"]
    if base not in seen_concept_ids:
        return base

    # Compose the discriminator. Even within one law, two different
    # paragraphs can define the same term — par_nr is needed to tell
    # them apart. def_number disambiguates within one paragraph.
    qualifier_parts = [
        concept.get("law_slug") or "",
        concept.get("par_nr") or "",
        concept.get("def_number") or "",
    ]
    qualifier = "_".join(p for p in qualifier_parts if p)
    candidate = f"{base}_{qualifier}" if qualifier else base
    if candidate not in seen_concept_ids:
        return candidate

    # Final monotonic counter — guarantees uniqueness even when (law,
    # par, def_number) has already been claimed (e.g. duplicate laws on
    # disk).
    n = 2
    while f"{candidate}_{n}" in seen_concept_ids:
        n += 1
    return f"{candidate}_{n}"


def _bucketed_close_match_pairs(
    unique_terms: list[str],
    *,
    min_len: int = 4,
) -> list[tuple[str, str, int]]:
    """Return list of (term_a, term_b, edit_distance) for pairs with
    distance ``0 < d < 3``.

    Finding 5 (#171): the previous all-pairs O(N²) loop with a 5000
    cap was the dominant runtime cost on growing corpora. We now bucket
    candidate terms by ``(first_2_chars, length)`` and only compare
    within each bucket and its (length ± 1, length ± 2) neighbours —
    the per-bucket quadratic is dramatically smaller on real corpora
    because a vocabulary spread across thousands of bucket cells
    flattens the input cardinality of every inner Levenshtein call.

    Pairs are emitted with the term order canonicalised (``a < b``) and
    deduped, so the result is stable across runs.
    """
    if not unique_terms:
        return []

    # Bucket by (first two chars, length). Lower-bound length at min_len
    # so we don't burn time on terms that are already excluded.
    buckets: dict[tuple[str, int], list[str]] = {}
    for term in unique_terms:
        if len(term) < min_len:
            continue
        prefix = term[:2]
        buckets.setdefault((prefix, len(term)), []).append(term)

    seen_pairs: set[tuple[str, str]] = set()
    results: list[tuple[str, str, int]] = []
    for (prefix, length), bucket in buckets.items():
        # Build the candidate set: this bucket's terms plus its (length
        # ±1, ±2) neighbours sharing the prefix. Two terms with edit
        # distance ≤ 2 differ in length by ≤ 2 and share at least their
        # leading 2-character prefix in the very common case where the
        # difference is on a trailing case suffix.
        candidates: list[str] = list(bucket)
        for delta in (-2, -1, 1, 2):
            adj = buckets.get((prefix, length + delta))
            if adj:
                candidates.extend(adj)
        # Pairwise loop within this candidate cluster only.
        for i, t1 in enumerate(candidates):
            for t2 in candidates[i + 1:]:
                if t1 == t2:
                    continue
                pair = (t1, t2) if t1 < t2 else (t2, t1)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                if abs(len(pair[0]) - len(pair[1])) >= 3:
                    continue
                dist = edit_distance(pair[0], pair[1])
                if 0 < dist < 3:
                    results.append((pair[0], pair[1], dist))
    return results


def main():
    print("=" * 70)
    print("Estonian Legal Ontology - Extract Legal Concepts")
    print("=" * 70)

    _start = time.perf_counter()

    # Layer 2a: pair peep files to XML via globalId (the canonical path
    # for KOV + state regs). The slug fallback is preserved via the
    # ``data_dir=DATA_DIR`` kwarg for laws-without-globalId.
    from estleg_common import (
        build_globalid_xml_lookup,
        pair_peep_with_xml,
    )

    # Step 1: Load law files
    print("\n[1/5] Loading peep JSON-LD files...")
    laws = load_law_files()
    print(f"  Found {len(laws)} peep files")
    _kov_files = [law["path"] for law in laws if "regulations/kov" in str(law["path"])]
    print(f"  Indexed {len(_kov_files)} KOV peep files")

    # Build globalId → XML path lookup once
    xml_lookup = build_globalid_xml_lookup(DATA_DIR)
    print(f"  Indexed {len(xml_lookup)} XML files by globalId")

    # Precompute set of canonical-pairing paths for O(1) fallback
    # detection. Mirrors the pattern in generate_amendment_history /
    # extract_temporal_data — _fallback_hits semantics is "XML resolved
    # via slug fallback", consistent across all three pipelines.
    _lookup_paths = {str(p) for p in xml_lookup.values()}

    # Step 2: Extract concepts from XML
    print("\n[2/5] Extracting defined terms from XML files...")
    all_concepts: list[dict] = []
    laws_with_concepts = 0

    # Coverage counters (aggregate-output semantics)
    _files_processed = 0
    _files_processed_kov = 0
    _files_with_output = 0
    _files_with_output_kov = 0
    _triples = 0  # populated post-graph-emission (Finding 6 #171)
    _triples_kov = 0
    _unresolved = 0
    _fallback_hits = 0
    _failures: list[str] = []
    _skip_reasons: dict[str, int] = {}
    # Finding 7 (#171): count concepts whose par_nr/par_display lookup
    # was ambiguous (multiple peep @ids matched).
    _ambiguous_par_iri = 0

    # Track is_kov per concept so the post-emission triple count can
    # split by KOV without re-deriving the path string.
    is_kov_by_concept_index: list[bool] = []

    for i, law in enumerate(laws):
        slug = law["slug"]
        title = law["title"]
        path = law["path"]
        is_kov = "regulations/kov" in str(path)

        # Finding 8 (#171): catch only the specific exceptions raised by
        # XML/JSON parsing and key/path lookups. Anything else (e.g.
        # KeyboardInterrupt, MemoryError, programming bugs) propagates so
        # the caller can fail loudly. Each caught failure logs a
        # traceback into _failures so post-mortem diagnosis is possible.
        try:
            # pair_peep_with_xml: globalId-based pairing for KOV + state
            # regs, slug-fallback for laws without globalId. The fallback
            # path runs only if globalId resolution misses AND data_dir
            # is supplied.
            xml_path = pair_peep_with_xml(
                path, xml_lookup, data_dir=DATA_DIR
            )
            if xml_path is None:
                _skip_reasons["no_xml"] = _skip_reasons.get("no_xml", 0) + 1
                continue

            # Track slug-based fallback hits — _fallback_hits semantics
            # are "XML resolved via slug fallback" (path not in the
            # canonical globalId lookup), aligned with temporal /
            # amendment-history.
            if str(xml_path) not in _lookup_paths:
                _fallback_hits += 1

            try:
                with open(path, "r", encoding="utf-8") as fh:
                    peep_doc = json.load(fh)
            except (json.JSONDecodeError, OSError):
                peep_doc = {}
                _skip_reasons["json_decode_error"] = (
                    _skip_reasons.get("json_decode_error", 0) + 1
                )

            par_to_iri = build_par_to_iri_lookup(peep_doc)

            concepts = extract_concepts_from_xml(
                xml_path, title, slug, par_to_iri=par_to_iri
            )

            _files_processed += 1
            if is_kov:
                _files_processed_kov += 1

            if concepts:
                all_concepts.extend(concepts)
                is_kov_by_concept_index.extend([is_kov] * len(concepts))
                laws_with_concepts += 1
                _files_with_output += 1
                if is_kov:
                    _files_with_output_kov += 1
                # Per-concept diagnostics: track concepts that didn't
                # bridge cleanly (slug-derived IRI fell through) and
                # concepts whose par_nr/par_display lookup was
                # ambiguous (Finding 7).
                for c in concepts:
                    bridged = (
                        par_to_iri
                        and (
                            c["par_nr"] in par_to_iri
                            or c["paragraph"] in par_to_iri
                        )
                    )
                    if not bridged:
                        _unresolved += 1
                    if c.get("ambiguous_par_iri"):
                        _ambiguous_par_iri += 1
                if len(concepts) >= 3:
                    print(f"  {title}: {len(concepts)} defined terms")
        except (ET.ParseError, OSError, KeyError, json.JSONDecodeError) as exc:
            tb = traceback.format_exc()
            _failures.append(f"{path.name}: {exc!r}\n{tb}")

    print(f"\n  Total: {len(all_concepts)} defined terms from {laws_with_concepts} laws")

    # Step 3: Build cross-references
    print("\n[3/5] Building cross-reference graph...")

    # Group concepts by lowercased term
    term_index: dict[str, list[dict]] = {}
    for concept in all_concepts:
        key = concept["term_lower"]
        term_index.setdefault(key, []).append(concept)

    # Find terms that appear in multiple laws (exact matches).
    # Finding 3 (#171): use ``sorted({...})`` instead of ``list({...})``
    # so the law list is deterministic across runs.
    exact_matches: list[dict] = []
    for term_lower, entries in sorted(term_index.items()):
        law_slugs = sorted({e["law_slug"] for e in entries})
        if len(law_slugs) > 1:
            exact_matches.append({
                "term": entries[0]["term"],
                "term_lower": term_lower,
                "laws": [
                    {"title": e["law_title"], "slug": e["law_slug"], "paragraph": e["paragraph"]}
                    for e in entries
                ],
                "count": len(law_slugs),
            })

    print(f"  Exact matches (same term in multiple laws): {len(exact_matches)}")

    # Find close matches (edit distance < 3) between unique terms.
    # Finding 5 (#171): bucketing strategy avoids the all-pairs O(N²)
    # blowup so we no longer need the 5000-term cap. Sort the bucket
    # output for deterministic graph ordering (Finding 3).
    unique_terms = sorted(term_index.keys())
    bucketed_pairs = _bucketed_close_match_pairs(unique_terms)
    bucketed_pairs.sort()
    close_matches: list[dict] = []
    for t1, t2, dist in bucketed_pairs:
        close_matches.append({
            "term_a": term_index[t1][0]["term"],
            "term_b": term_index[t2][0]["term"],
            "term_a_lower": t1,
            "term_b_lower": t2,
            "distance": dist,
            "laws_a": sorted({e["law_slug"] for e in term_index[t1]}),
            "laws_b": sorted({e["law_slug"] for e in term_index[t2]}),
        })

    print(f"  Close matches (edit distance < 3): {len(close_matches)}")

    # Step 4: Generate JSON-LD output
    print("\n[4/5] Generating JSON-LD concept files...")

    # Build @graph for combined file
    graph: list[dict] = [
        {
            "@id": "estleg:LegalConcepts_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "Eesti õiguse mõisted (Estonian Legal Concepts)",
            "dc:description": "Seaduses defineeritud õigusmõisted ja nende ristviited.",
            "dc:source": "Riigi Teataja XML",
            "estleg:totalConcepts": {
                "@value": str(len(all_concepts)),
                "@type": "xsd:integer",
            },
        },
        # LegalConcept class definition
        {
            "@id": "estleg:LegalConcept",
            "@type": ["owl:Class"],
            "rdfs:label": "Õigusmõiste (Legal Concept)",
            "rdfs:comment": "Seaduses defineeritud mõiste.",
        },
        # definesTerm property
        {
            "@id": "estleg:definesTerm",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "defineerib mõiste",
            "rdfs:comment": "Links a legal provision to a concept it defines.",
        },
        # definedIn property
        {
            "@id": "estleg:definedIn",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "defineeritud aktis",
            "rdfs:comment": "Links a concept to the provision where it is defined.",
            "owl:inverseOf": {"@id": "estleg:definesTerm"},
        },
    ]

    # Track concept IDs to avoid duplicates in graph. Finding 2 (#171):
    # concept_id_by_index maps the original ``all_concepts`` position to
    # the disambiguated graph @id, so the exactMatch / closeMatch loops
    # below can resolve the actual emitted id even after collisions.
    seen_concept_ids: set[str] = set()
    concept_id_by_index: list[str] = [""] * len(all_concepts)

    # Finding 6 (#171): _triples is now incremented INSIDE the emission
    # loop, gated on the concept actually being added. Previous code
    # incremented _triples += 2 BEFORE the add, so collided drops caused
    # an overcount.
    for idx, concept in enumerate(all_concepts):
        cid = _disambiguate_concept_id(concept, seen_concept_ids)
        seen_concept_ids.add(cid)
        concept_id_by_index[idx] = cid

        node: dict = {
            "@id": cid,
            "@type": ["owl:NamedIndividual", "estleg:LegalConcept"],
            "skos:prefLabel": concept["term"],
            "skos:definition": concept["definition"],
            "estleg:definedIn": [{"@id": concept["provision_id"]}],
            "estleg:sourceAct": concept["law_title"],
            "rdfs:label": f"{concept['term']} ({concept['law_slug']})",
        }
        graph.append(node)
        _triples += 2  # definesTerm + definedIn — counted post-emission.
        if idx < len(is_kov_by_concept_index) and is_kov_by_concept_index[idx]:
            _triples_kov += 2

    # Build a lookup from @id to graph node for in-place updates
    graph_node_by_id: dict[str, dict] = {}
    for node in graph:
        nid = node.get("@id", "")
        if nid:
            graph_node_by_id[nid] = node

    # Build a parallel index: term_lower → list of (concept_index,
    # disambiguated_id). Used by exactMatch and closeMatch below to
    # resolve the same disambiguated id that the emission loop chose.
    concept_index_by_term: dict[str, list[tuple[int, str]]] = {}
    for idx, concept in enumerate(all_concepts):
        cid = concept_id_by_index[idx]
        if cid:
            concept_index_by_term.setdefault(
                concept["term_lower"], []
            ).append((idx, cid))

    # Add skos:exactMatch links for terms appearing in multiple laws.
    # Finding 2 (#171): every target id is the disambiguated post-emit
    # id. This guarantees the exactMatch reference points at a real
    # node instead of a dangling stub.
    for em in exact_matches:
        entries = concept_index_by_term.get(em["term_lower"], [])
        concept_ids = [cid for _idx, cid in entries if cid in seen_concept_ids]

        # Link each pair with skos:exactMatch — merge into existing nodes
        for i in range(len(concept_ids)):
            for j in range(i + 1, len(concept_ids)):
                target_node = graph_node_by_id.get(concept_ids[i])
                if target_node is not None:
                    existing = target_node.get("skos:exactMatch", [])
                    if isinstance(existing, dict):
                        existing = [existing]
                    existing.append({"@id": concept_ids[j]})
                    target_node["skos:exactMatch"] = existing

    # Add skos:closeMatch links — Finding 4 (#171): iterate the FULL
    # entries_a × entries_b cross-product and write bidirectional
    # arcs (a → b AND b → a). The previous code only linked the FIRST
    # concept of each side, so if disambiguation moved the canonical
    # id, the closeMatch arc could dangle.
    for cm in close_matches:
        t_a = cm["term_a_lower"]
        t_b = cm["term_b_lower"]
        ids_a = [cid for _idx, cid in concept_index_by_term.get(t_a, [])
                 if cid in seen_concept_ids]
        ids_b = [cid for _idx, cid in concept_index_by_term.get(t_b, [])
                 if cid in seen_concept_ids]
        for id_a in ids_a:
            target_a = graph_node_by_id.get(id_a)
            if target_a is None:
                continue
            for id_b in ids_b:
                if id_a == id_b:
                    continue
                existing = target_a.get("skos:closeMatch", [])
                if isinstance(existing, dict):
                    existing = [existing]
                existing.append({"@id": id_b})
                target_a["skos:closeMatch"] = existing

    # Finding 2 (#171): every skos:exactMatch / skos:closeMatch target
    # MUST point at a node we actually emitted. Verify and trim any
    # stragglers — should be a no-op given the disambiguation logic
    # above, but the assertion catches future regressions early.
    for node in graph:
        for prop in ("skos:exactMatch", "skos:closeMatch"):
            refs = node.get(prop)
            if not refs:
                continue
            if isinstance(refs, dict):
                refs = [refs]
            valid = [r for r in refs
                     if isinstance(r, dict)
                     and r.get("@id") in seen_concept_ids]
            if valid:
                node[prop] = valid
            else:
                node.pop(prop, None)

    # Save combined concepts file
    combined_doc = {"@context": CONTEXT, "@graph": graph}
    combined_path = CONCEPTS_DIR / "concepts_combined.jsonld"
    save_json(combined_path, combined_doc)
    print(f"  Saved: {combined_path.name} ({len(graph)} nodes)")

    # Step 5: Generate cross-reference report
    print("\n[5/5] Generating concept_crossref_report.json...")

    # Statistics by law
    concepts_per_law: dict[str, int] = {}
    for c in all_concepts:
        concepts_per_law[c["law_slug"]] = concepts_per_law.get(c["law_slug"], 0) + 1
    laws_ranked = sorted(concepts_per_law.items(), key=lambda x: x[1], reverse=True)

    report = {
        "generated": date.today().isoformat(),
        "summary": {
            "total_laws_analyzed": len(laws),
            "laws_with_definitions": laws_with_concepts,
            "total_defined_terms": len(all_concepts),
            "unique_terms": len(unique_terms),
            "terms_in_multiple_laws": len(exact_matches),
            "close_match_pairs": len(close_matches),
        },
        "top_laws_by_definitions": [
            {"slug": slug, "count": count}
            for slug, count in laws_ranked[:30]
        ],
        "cross_referenced_terms": [
            {
                "term": em["term"],
                "appears_in": em["count"],
                "laws": [
                    {"title": law["title"], "paragraph": law["paragraph"]}
                    for law in em["laws"]
                ],
            }
            for em in sorted(exact_matches, key=lambda x: x["count"], reverse=True)
        ],
        "close_matches": [
            {
                "term_a": cm["term_a"],
                "term_b": cm["term_b"],
                "edit_distance": cm["distance"],
            }
            for cm in close_matches[:100]  # Limit to top 100
        ],
    }

    report_path = CONCEPTS_DIR / "concept_crossref_report.json"
    save_json(report_path, report)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Laws analyzed:              {len(laws)}")
    print(f"  Laws with definitions:      {laws_with_concepts}")
    print(f"  Total defined terms:        {len(all_concepts)}")
    print(f"  Unique terms:               {len(unique_terms)}")
    print(f"  Terms in multiple laws:     {len(exact_matches)}")
    print(f"  Close-match pairs:          {len(close_matches)}")
    if laws_ranked:
        print("\n  Top 5 laws by definitions:")
        for slug, count in laws_ranked[:5]:
            print(f"    {slug}: {count} terms")
    if exact_matches:
        top_crossref = sorted(exact_matches, key=lambda x: x["count"], reverse=True)[:5]
        print("\n  Top 5 cross-referenced terms:")
        for em in top_crossref:
            print(f"    \"{em['term']}\" appears in {em['count']} laws")
    print(f"\n  Output: {CONCEPTS_DIR.relative_to(REPO_ROOT)}/")
    print("  Combined: concepts_combined.jsonld")
    print("  Report:   concept_crossref_report.json")
    print("=" * 70)

    # ---------- coverage report ----------
    # Aggregate-output pipeline: legal-concepts writes one combined
    # JSON-LD plus a report — counters track how many peep files
    # contributed (vs. the per-act in-place pipelines).
    _wall, _rate, _peak_mb = measure_runtime(_start, _files_processed)
    skip_reasons_for_report = dict(_skip_reasons)
    if _ambiguous_par_iri:
        skip_reasons_for_report["ambiguous_par_iri"] = _ambiguous_par_iri
    write_coverage_report(
        CoverageReport(
            pipeline="extract_legal_concepts",
            run_timestamp=datetime.now(timezone.utc).isoformat(),
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(laws),
            input_files_kov=len(_kov_files),
            files_processed=_files_processed,
            files_processed_kov=_files_processed_kov,
            files_with_output=_files_with_output,
            files_with_output_kov=_files_with_output_kov,
            files_skipped=sum(_skip_reasons.values()),
            skip_reasons=skip_reasons_for_report,
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
        / "extract_legal_concepts_coverage.json",
    )

    # Finding 8 (#171): non-zero exit when failures exceed the threshold.
    if len(_failures) > _FAILURE_EXIT_THRESHOLD:
        print(
            f"\nFAIL: {len(_failures)} files failed during processing — "
            f"exceeds threshold {_FAILURE_EXIT_THRESHOLD}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
