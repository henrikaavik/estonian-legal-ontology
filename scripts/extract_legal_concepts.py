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
from pathlib import Path

from functools import partial

from estleg_common import (
    CONTEXT,
    BUILD_EVALUATION_DATE,
    iter_peep_files,
    jsonld_text,
    save_json,
    sanitize_id as _shared_sanitize_id,
    stamp_combined_dataset_head,
)
from kov_pipeline_coverage import (
    CoverageReport,
    PINNED_RUN_TIMESTAMP,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"
CONCEPTS_DIR = KRR_DIR / "concepts"
CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://w3id.org/estleg/"

# T-Box class/property IRIs owned by controlled_vocabulary.jsonld (#359).
# This extractor must not emit declaration nodes for these; instance
# estleg:Concept_* / estleg:LegalConcept_* individuals are still emitted.
CV_OWNED_TBOX_IDS = frozenset({
    "estleg:Concept",
    "estleg:LegalConcept",
    "estleg:definedIn",
    "estleg:definesConcept",
    "estleg:definitionCount",
    "estleg:definitionVariantCount",
    "estleg:hasDefinitionNode",
})


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
#
# Finding 1 (#171): the previous body fragment ``(.+?)(?:;|$)`` truncated
# multi-clause definitions at the FIRST semicolon, which silently
# discarded long-form definitions like
# ``KMS § 2 1) imporditud kaup — kaup, mille kohta on koostatud
# ekspordideklaratsioon; sealhulgas...``. The body now anchors on the
# next *definition-item* marker (lookahead) instead, then strips a
# trailing ``;`` in extract_definitions_from_text. End-of-string is
# preserved so the last definition in a list still terminates cleanly.
#
# #260: hyphenated first-token terms (``tee-ehitus``, ``e-arve``) were
# corrupted/dropped because the first term word excluded ``-`` *and* the
# separator allowed an un-spaced dash, so ``tee-ehitus`` parsed as
# term=``tee`` / sep=``-``. The term's words now permit internal hyphens
# (``tee-ehitus``, ``e-arve``) and the separator dash is required to be
# space-surrounded so an in-term hyphen is never mistaken for it.
#
# #261: the body lookahead stopped at the FIRST inline ``\d+\)`` even
# when that paren was a cross-reference (``käesoleva seaduse 2) punktis``)
# rather than a real list item, truncating the definition. The stop is
# now anchored on the next *definition-item start* — a ``\d+\)`` that is
# itself followed by ``term <space-dash-space>`` — so inline numbered
# cross-references no longer cut the definition short.

# A term word: letters (incl. Estonian diacritics / digits via \w) with
# optional internal hyphens (``tee-ehitus``, ``e-arve``).
_TERM_WORD = r"[\wäöüõšžÄÖÜÕŠŽ]+(?:-[\wäöüõšžÄÖÜÕŠŽ]+)*"
# The full term: one or more such words, lazily (``imporditud kaup``).
_TERM = rf"{_TERM_WORD}(?:\s+{_TERM_WORD})*?"
# Separator dash: space-surrounded en/em/hyphen dash. The mandatory
# surrounding whitespace is what keeps an in-term hyphen out of the
# separator role (#260).
_DEF_SEP = r"\s+[–—-]\s+"
# A definition-item start (used only in the stop lookahead, #261): a
# numbered marker that actually introduces ``term – definition``. An
# inline cross-reference like ``2) punktis`` is NOT followed by a
# space-surrounded dash, so it does not match and does not truncate.
_ITEM_START = rf"\d+\)\s*{_TERM}{_DEF_SEP}"

DEFINITION_PATTERN = re.compile(
    r"(\d+)\)\s*"                     # number and closing paren
    rf"({_TERM})"                     # term (one or more hyphen-aware words)
    rf"{_DEF_SEP}"                    # space-surrounded dash separator
    rf"(.+?)(?=\s*{_ITEM_START}|$)",  # definition until next item start or end
    re.UNICODE | re.DOTALL,
)

# #580: a spliced Riigi Teataja amendment-citation block. The RT publication
# mark (``<avaldamismarge>``) serialises as ``RT I``/``RT IV``/``RT Õ`` (the
# ``V`` variant exists too) followed by an ISO date, then the issue / global-id /
# publication-date run — and sometimes text bled in from the next list item. It
# is dropped at the structural source by ``_itertext_filtered`` (below), but this
# tail-strip is the defensive backstop: any value still carrying ``RT … <date>``
# is cut at that marker (the real definition always precedes it). Anchored on the
# date so a bare "RT" inside prose without a following date never triggers a cut.
RT_CITATION_TAIL = re.compile(r"\s*RT\s+[IÕ]+V?\s+\d{4}-\d{2}-\d{2}.*$", re.DOTALL)


def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def ct(el: ET.Element, name: str) -> str | None:
    """Get child element text by local tag name."""
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


# #303: pure-numbering elements whose text is a bare subsection/item
# COUNTER (``<loigeNr>1</loigeNr>``, ``<alampunktNr>2</alampunktNr>``,
# ``<paragrahvNr>2</paragrahvNr>``). The Riigi Teataja serialiser emits
# these as their own text nodes, so ``itertext()`` joins a bare ``2``
# between the end of one definition and the start of the next — which the
# DEFINITION_PATTERN lookahead then consumes as a trailing ``; 2`` (or the
# ``(N)`` header bleeds in). They carry no definitional content (the human-
# readable marker lives in ``<kuvatavNr>``), so we drop them at the source.
_NUMBERING_ONLY_TAGS = frozenset({
    "paragrahvNr",
    "loigeNr",
    "alampunktNr",
    "punktNr",
    "lisaNr",
})

# #580: publication-mark subtrees whose text is a Riigi Teataja amendment
# citation (``RT IV 2020-10-01 2 401102020002 2020-10-04``). The serialiser
# emits these as inline nodes *inside* a definition's running text, so
# ``itertext()`` splices the citation onto the end of the legal definition (and
# the lazy DEFINITION_PATTERN then captures it, sometimes dragging in the next
# item too). They carry no definitional content, so the whole subtree — text AND
# children — is dropped at the structural source. The element's TAIL is kept so
# surrounding running text is not glued together.
_CITATION_SUBTREE_TAGS = frozenset({
    "avaldamismarge",
})

# A ``<kuvatavNr>`` text that is a real list-item marker — ``1)``, ``2)`` —
# (digit(s) + close paren, no leading paren). These DRIVE the
# DEFINITION_PATTERN and must be kept. The header forms (``(1)``, ``§ 2.``)
# carry no item boundary and are dropped so they cannot bleed into a
# neighbouring definition.
_LIST_ITEM_MARKER = re.compile(r"^\d+\)$")


def _itertext_filtered(el: ET.Element) -> list[str]:
    """Yield text fragments of ``el`` and its descendants, but:

    * skip pure-numbering counter elements (``_NUMBERING_ONLY_TAGS``)
      entirely — both their text and their tail; and
    * for ``<kuvatavNr>``, keep only real ``N)`` list-item markers,
      dropping ``(N)`` / ``§ N.`` header forms.

    This removes the #303 inline-counter contamination at the structural
    source, so digits that are genuine definitional content (``§ 4 lõikes
    3``, ``kakskümmend (20) meetrit``) are never touched.
    """
    parts: list[str] = []
    tag = ln(el.tag)
    if tag in _CITATION_SUBTREE_TAGS:
        # #580: drop the RT amendment-citation subtree entirely (text AND
        # descendants) so it cannot splice onto a definition; keep only the
        # tail (running text that follows the citation node).
        if el.tail:
            parts.append(el.tail)
        return parts
    if tag in _NUMBERING_ONLY_TAGS:
        # Drop the counter's own text; preserve the tail so surrounding
        # running text is not glued together.
        if el.tail:
            parts.append(el.tail)
        return parts
    if tag == "kuvatavNr":
        marker = (el.text or "").strip()
        if _LIST_ITEM_MARKER.match(marker):
            parts.append(el.text or "")
        # else: header form (``(1)`` / ``§ 2.``) — dropped.
        if el.tail:
            parts.append(el.tail)
        return parts
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.extend(_itertext_filtered(child))
    if el.tail:
        parts.append(el.tail)
    return parts


def collect_full_text(el: ET.Element) -> str:
    """Collect all text content from an element and its children.

    #303: pure-numbering counter elements (``loigeNr`` / ``alampunktNr`` /
    ``paragrahvNr``) and ``(N)`` / ``§ N.`` header ``kuvatavNr`` forms are
    dropped at the structural source via ``_itertext_filtered`` so the
    inline subsection-prefix digit can no longer bleed into a definition as
    ``; N``. Real ``N)`` list-item markers are preserved so the
    DEFINITION_PATTERN still sees its items.
    """
    text = " ".join(_itertext_filtered(el))
    return re.sub(r"\s+", " ", text).strip()


sanitize_id = partial(_shared_sanitize_id, max_len=80, replace_dash=True)


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
        # #303 (defensive): collect_full_text now drops the standalone
        # subsection-prefix counters at the source, but a direct caller of
        # extract_definitions_from_text (or an unforeseen serialiser quirk)
        # may still leave a trailing ``; N`` artefact (the bare counter of
        # the NEXT list item that bled past the lookahead). Strip it. The
        # ``(N) header`` bleed is removed at source by collect_full_text;
        # we deliberately do NOT strip a trailing ``(N)`` here because it
        # occurs legitimately inside definitions (``kakskümmend (20)
        # meetrit``).
        definition = re.sub(r";\s*\d+\s*$", "", definition)
        definition = definition.rstrip("; \t\r\n").strip()
        # #580 (defensive backstop): _itertext_filtered now drops the RT
        # publication-mark subtree at the source, but a direct caller (or an
        # unforeseen serialiser quirk that emits the citation outside an
        # <avaldamismarge>) may still leave a spliced ``… RT IV 2020-10-01 …``
        # tail. Cut it — the real definition always precedes the marker — and
        # re-tidy the dangling separator the cut leaves behind.
        definition = RT_CITATION_TAIL.sub("", definition)
        definition = definition.rstrip("; \t\r\n–—-").strip()

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
    cap was the dominant runtime cost on growing corpora, so candidate
    pairs are co-located by a bucketing signature and Levenshtein is
    only computed within each bucket.

    #278: the old signature was ``(first_2_chars, length)``, which only
    ever compared terms sharing their leading two characters. Every
    edit-distance ≤ 2 pair that differs in a *leading* character
    (``tasu``/``kasu``) or transposes the first two characters
    (``liige``/``ilige``) landed in different buckets and was silently
    missed. We now index each term under its set of *deletion variants*
    — every string obtained by deleting up to ``MAX_DIST`` characters
    (SymSpell-style). Two strings within Levenshtein distance ``d`` share
    a common subsequence reachable by deleting ≤ ``d`` characters from
    each, so any pair with ``d ≤ MAX_DIST`` is guaranteed to collide in
    at least one variant bucket regardless of *where* the edits fall.
    Generating the variants is O(n · L^MAX_DIST) in the (small, bounded)
    term length ``L`` — i.e. O(n · k), never O(n²) across the corpus.

    Pairs are emitted with the term order canonicalised (``a < b``) and
    deduped, so the result is stable across runs.
    """
    if not unique_terms:
        return []

    # Edit-distance threshold: we keep pairs with 0 < d < 3, i.e. d ≤ 2.
    MAX_DIST = 2

    def _deletion_variants(word: str) -> set[str]:
        """All strings reachable by deleting 0..MAX_DIST chars from ``word``."""
        variants = {word}
        frontier = {word}
        for _ in range(MAX_DIST):
            nxt: set[str] = set()
            for w in frontier:
                for i in range(len(w)):
                    nxt.add(w[:i] + w[i + 1:])
            variants |= nxt
            frontier = nxt
        return variants

    # Index every (sufficiently long) term under each of its deletion
    # variants. Terms that collide under a shared variant are the only
    # candidates that can be within MAX_DIST edits of each other.
    index: dict[str, list[str]] = {}
    for term in unique_terms:
        if len(term) < min_len:
            continue
        for variant in _deletion_variants(term):
            index.setdefault(variant, []).append(term)

    seen_pairs: set[tuple[str, str]] = set()
    results: list[tuple[str, str, int]] = []
    for candidates in index.values():
        if len(candidates) < 2:
            continue
        for i, t1 in enumerate(candidates):
            for t2 in candidates[i + 1:]:
                if t1 == t2:
                    continue
                pair = (t1, t2) if t1 < t2 else (t2, t1)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                if abs(len(pair[0]) - len(pair[1])) > MAX_DIST:
                    continue
                dist = edit_distance(pair[0], pair[1])
                if 0 < dist <= MAX_DIST:
                    results.append((pair[0], pair[1], dist))
    return results


# ---------------------------------------------------------------------------
# Issue #134 — canonical estleg:Concept nodes + quality / noise filter
# ---------------------------------------------------------------------------
#
# The old model emitted one provision-local ``estleg:LegalConcept`` node per
# defined term and then connected every node defining the same term with a
# clique of ``skos:exactMatch`` arcs (O(k²) per term). The new model adds one
# canonical ``estleg:Concept`` node per distinct *normalised* term; each
# provision-local definition node points at it once via ``estleg:definesConcept``
# (a hub link — O(k) total) plus a single ``skos:exactMatch`` to the canonical
# node (the proper SKOS relation), and the fuzzy ``skos:closeMatch`` relation
# now connects ``estleg:Concept`` nodes to each other rather than the
# provision-local nodes.

# Maximum number of distinct ``skos:definition`` values emitted on a canonical
# Concept node. When a term has more than this many *distinct* (normalised)
# definitions across laws the node records ``estleg:definitionVariantCount``
# with the true total instead of bloating the graph.
_MAX_CONCEPT_DEFINITIONS = 5

# Placeholder / repealed-marker "terms" that must never get a canonical
# Concept node. Compared case-insensitively against the normalised term after
# stripping surrounding brackets/parentheses.
_NOISE_TERM_TOKENS = {
    "kehtetu",
    "kehtetud",
    "kehtetuks tunnistatud",
    "valja jaetud",
    "väljajäetud",
    "väljajaetud",
    "valjajaetud",
    "reserveeritud",
    "...",
    "…",
}

# Estonian (and a few stray English) stopwords. A "term" consisting solely of
# stopwords is noise, not a concept.
_STOPWORD_TERMS = {
    "ja", "või", "voi", "ning", "ka", "kui", "et", "see", "selle", "sel",
    "need", "ta", "tema", "nad", "nende", "on", "ei", "kes", "mis", "kus",
    "millal", "kuidas", "ehk", "siis", "kuid", "aga", "vaid", "nii",
    "the", "of", "in", "and", "or", "a", "an", "to", "for",
}

# Definition strings that signal "no real definition here" (a repealed or
# omitted definition slot). Compared after lowercasing and stripping trailing
# punctuation; also matched as a prefix so e.g. ``"kehtetu - RT I, ..."`` is
# recognised.
_NOISE_DEFINITION_PREFIXES = (
    "kehtetu",
    "kehtetuks tunnistatud",
    "valja jaetud",
    "välja jäetud",
    "väljajäetud",
)
_NOISE_DEFINITION_EXACT = {
    "...",
    "…",
    "-",
    "–",
    "—",
}


def _strip_term_brackets(term_lower: str) -> str:
    """Strip a single layer of surrounding ``[]`` / ``()`` from a normalised
    term so ``"[kehtetu]"`` and ``"(kehtetu)"`` match the noise token set."""
    t = term_lower.strip()
    while len(t) >= 2 and (
        (t[0] == "[" and t[-1] == "]") or (t[0] == "(" and t[-1] == ")")
    ):
        t = t[1:-1].strip()
    return t


# #323: copula / definitional-intro markers that indicate the lazy _TERM
# regex over-captured a multi-word intro phrase (``avalik koht on määratlemata
# isikute ringile …``) as the term instead of stopping at the term boundary.
# A real legal term never contains these connective phrases, so their presence
# is a reliable signal that the "term" is actually term+copula+definition glued
# together. Matched as space-surrounded substrings of the lowercased term.
_COPULA_INTRO_MARKERS = (
    " on ",
    " tähendab ",
    " mõistetakse ",
)


def is_noise_term(term_lower: str, original_term: str | None = None) -> bool:
    """Return True if ``term_lower`` is junk that should not become a
    canonical Concept node.

    Filtered: empty / single-character "terms"; "terms" that are entirely
    digits or punctuation; repealed-marker placeholders (``kehtetu`` and
    variants, with or without surrounding brackets); "terms" composed solely
    of stopwords; and (``#323``) multi-word intro phrases the lazy ``_TERM``
    regex over-captured via a copula (`` on ``/`` tähendab ``/
    `` mõistetakse ``).

    ``original_term`` (``#392``): the surface form before case-folding. When
    it is supplied AND is entirely uppercase, the single-token stopword test
    is skipped — uppercase legal abbreviations like ``TA``
    (teadus-arendustöötaja) and ``ET`` (elutähtis teenus) case-fold onto the
    stopwords ``ta``/``et`` and would otherwise be wrongly dropped.
    """
    t = _strip_term_brackets(term_lower)
    if len(t) < 2:
        return True
    # Entirely digits / whitespace / punctuation (no letters at all).
    if not re.search(r"[^\W\d_]", t, re.UNICODE):
        return True
    if t in _NOISE_TERM_TOKENS:
        return True
    # #323: a term carrying a copula / definitional-intro connective is an
    # over-captured intro phrase, not a real term.
    padded = f" {t} "
    if any(marker in padded for marker in _COPULA_INTRO_MARKERS):
        return True
    # #392: uppercase abbreviations (``TA``, ``ET``) collide with the
    # stopwords ``ta``/``et`` after case-folding. When the ORIGINAL surface
    # form is all-uppercase, skip the stopword tests so the abbreviation
    # survives and gets its canonical concept node.
    is_uppercase_abbrev = bool(
        original_term is not None
        and original_term.strip()
        and original_term.strip().isupper()
    )
    if not is_uppercase_abbrev:
        if t in _STOPWORD_TERMS:
            return True
        words = [w for w in re.split(r"\s+", t) if w]
        if words and all(w in _STOPWORD_TERMS for w in words):
            return True
    return False


def is_noise_definition(definition: str) -> bool:
    """Return True if ``definition`` carries no real definitional content
    (empty, a bare dash, or a ``kehtetu`` / ``välja jäetud`` placeholder)."""
    if not definition:
        return True
    d = definition.strip().lower().rstrip(".;,…- \t\r\n–—")
    if not d:
        return True
    if definition.strip().lower() in _NOISE_DEFINITION_EXACT:
        return True
    return any(d.startswith(prefix) for prefix in _NOISE_DEFINITION_PREFIXES)


def _normalize_definition_for_dedup(definition: str) -> str:
    """Normalise a definition string for near-duplicate detection: collapse
    whitespace, lowercase, strip trailing punctuation.

    #391: this dedup-normalisation path is SEPARATE from the emission-layer
    cleanup (#303). The inline subsection-number contamination (``…vesi;
    17``, ``…vesi; 22 (``) is not removed by the plain ``rstrip`` below —
    the trailing ``(`` isn't in the strip set and the number survives — so
    one underlying definition is counted as N near-duplicate variants (one
    per subsection number), inflating ``definitionVariantCount`` ~2× and
    filling the 5-slot ``skos:definition`` cap with near-duplicates. Pre-
    strip the ``; N (…`` / ``; N`` suffix BEFORE the rstrip so the variants
    collapse to the one true wording.
    """
    d = re.sub(r"\s+", " ", definition).strip().lower()
    # Strip a trailing ``; N`` subsection counter, optionally followed by the
    # start of the next ``(N) …`` header (``…vesi; 17`` and ``…vesi; 22 (``).
    # The ``\(?.*`` tail also absorbs the case where the counter bled a
    # partial header in, so all per-subsection variants collapse to the one
    # true wording (#391; matches the issue's own evidence pattern).
    d = re.sub(r";\s*\d+\s*\(?.*$", "", d)
    return d.rstrip(".;,…- \t\r\n–—")


def _most_common(values: list[str]) -> str:
    """Return the most frequent value in ``values``; ties broken by sorting
    the surface form ascending so the result is deterministic."""
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    # Sort by (-count, value) — most frequent first, then alphabetical.
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def canonical_concept_id(term_lower: str, claimed: set[str]) -> str:
    """Return a fresh ``estleg:Concept_<sanitized term>`` IRI.

    ``sanitize_id`` lossily transliterates diacritics and truncates to 80
    chars, so two distinct normalised terms can map to the same sanitized
    form (e.g. ``"tee kaitsevöönd"`` and ``"tee-kaitsevöönd"`` both → ``…
    _tee_kaitsevoond``). On collision append a monotonic ``_2``, ``_3`` …
    suffix. Caller must add the returned id to ``claimed`` before the next
    call so the sequence stays stable.
    """
    base = f"estleg:Concept_{sanitize_id(term_lower)}"
    if base not in claimed:
        return base
    n = 2
    while f"{base}_{n}" in claimed:
        n += 1
    return f"{base}_{n}"


def generate_schema_nodes(*, total_concepts: int) -> list[dict]:
    """Ontology header plus local-only schema nodes.

    The seven T-Box IRIs in ``CV_OWNED_TBOX_IDS`` belong in
    ``controlled_vocabulary.jsonld`` only (#359) and are not emitted here.
    """
    return [
        {
            "@id": "estleg:LegalConcepts_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "Eesti õiguse mõisted (Estonian Legal Concepts)",
            "dc:description": "Seaduses defineeritud õigusmõisted ja nende ristviited.",
            "dc:source": "Riigi Teataja XML",
            "estleg:totalConcepts": {
                "@value": str(total_concepts),
                "@type": "xsd:integer",
            },
        },
        # definesTerm is not in the CV; keep the local declaration.
        {
            "@id": "estleg:definesTerm",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "defineerib mõiste",
            "rdfs:comment": "Links a legal provision to a concept it defines.",
        },
    ]


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

    # Step 3: Build cross-references and the canonical Concept registry
    print("\n[3/5] Building cross-reference graph...")

    # Group provision-local definitions by normalised (lowercased,
    # whitespace-stripped) term — this is the canonical-concept grouping key.
    term_index: dict[str, list[dict]] = {}
    for concept in all_concepts:
        key = concept["term_lower"].strip()
        concept["term_norm"] = key
        term_index.setdefault(key, []).append(concept)

    unique_terms = sorted(term_index.keys())

    # ---- Quality / noise filter (#134) ----
    # A normalised term gets NO canonical Concept node when it is obvious junk
    # (``is_noise_term``) OR every definition collected for it is itself a
    # placeholder (``is_noise_definition``). Provision-local definition nodes
    # for such terms are still emitted (they remain part of each law's graph)
    # but carry no ``estleg:definesConcept`` / ``skos:exactMatch`` cross-link.
    concept_terms: list[str] = []
    noise_terms: list[str] = []
    for term_norm in unique_terms:
        entries = term_index[term_norm]
        # #392: pass the most common ORIGINAL surface form so is_noise_term
        # can recognise all-uppercase abbreviations (``TA``/``ET``) and skip
        # the stopword test for them.
        original_term = _most_common([e["term"] for e in entries])
        if is_noise_term(term_norm, original_term) or all(
            is_noise_definition(e["definition"]) for e in entries
        ):
            noise_terms.append(term_norm)
        else:
            concept_terms.append(term_norm)
    _concepts_filtered_noise = len(noise_terms)
    print(f"  Canonical concepts: {len(concept_terms)}  "
          f"(filtered noise terms: {_concepts_filtered_noise})")

    # Concept terms that appear in more than one law — kept for the report's
    # ``cross_referenced_terms`` section. The equivalence itself is expressed
    # by a shared ``estleg:definesConcept`` target rather than an O(k²)
    # ``skos:exactMatch`` clique, so this list is reporting-only. Noise terms
    # are excluded — they are surfaced separately in ``noisy_candidate_terms``.
    exact_matches: list[dict] = []
    for term_norm in concept_terms:
        entries = term_index[term_norm]
        law_slugs = sorted({e["law_slug"] for e in entries})
        if len(law_slugs) > 1:
            exact_matches.append({
                "term": _most_common([e["term"] for e in entries]),
                "term_lower": term_norm,
                "laws": [
                    {"title": e["law_title"], "slug": e["law_slug"], "paragraph": e["paragraph"]}
                    for e in entries
                ],
                "count": len(law_slugs),
            })

    print(f"  Concept terms appearing in multiple laws: {len(exact_matches)}")

    # Close matches (edit distance < 3) — now computed over the *Concept* term
    # set only (far fewer comparisons; the result connects Concept nodes, not
    # provision-local nodes). Sorted for deterministic graph ordering.
    bucketed_pairs = _bucketed_close_match_pairs(concept_terms)
    bucketed_pairs.sort()
    close_matches: list[dict] = []
    for t1, t2, dist in bucketed_pairs:
        close_matches.append({
            "term_a": _most_common([e["term"] for e in term_index[t1]]),
            "term_b": _most_common([e["term"] for e in term_index[t2]]),
            "term_a_lower": t1,
            "term_b_lower": t2,
            "distance": dist,
        })

    print(f"  Close matches (edit distance < 3): {len(close_matches)}")

    # Step 4: Generate JSON-LD output
    print("\n[4/5] Generating JSON-LD concept files...")

    # Build @graph for combined file. CV-owned T-Box class/property
    # declarations are omitted (#359); only the ontology header and the
    # local-only definesTerm property are emitted as schema nodes.
    graph: list[dict] = generate_schema_nodes(total_concepts=len(all_concepts))

    # ---- Provision-local definition (LegalConcept) nodes ----
    # Track concept IDs to avoid duplicates in graph. Finding 2 (#171):
    # concept_id_by_index maps the original ``all_concepts`` position to
    # the disambiguated graph @id.
    seen_concept_ids: set[str] = set()
    concept_id_by_index: list[str] = [""] * len(all_concepts)

    # Finding 6 (#171): _triples is incremented INSIDE the emission loop,
    # gated on the concept actually being added. Each provision-local node
    # emits: definedIn (→ provision) + definesConcept (→ Concept) when the
    # term is non-noise, else just definedIn.
    legal_concept_nodes_by_term: dict[str, list[str]] = {}
    kov_legal_concept_ids: set[str] = set()
    for idx, concept in enumerate(all_concepts):
        cid = _disambiguate_concept_id(concept, seen_concept_ids)
        seen_concept_ids.add(cid)
        concept_id_by_index[idx] = cid
        legal_concept_nodes_by_term.setdefault(
            concept["term_norm"], []
        ).append(cid)
        is_kov = idx < len(is_kov_by_concept_index) and is_kov_by_concept_index[idx]
        if is_kov:
            kov_legal_concept_ids.add(cid)

        node: dict = {
            "@id": cid,
            "@type": ["owl:NamedIndividual", "estleg:LegalConcept"],
            # #325: emit language-tagged literals (Estonian) for the
            # provision-local label/definition, matching the canonical
            # Concept nodes. Bare strings were untagged xsd:string, so
            # FILTER(lang(?label)='et') silently missed every provision-
            # local concept and SKOS per-language prefLabel uniqueness broke.
            "skos:prefLabel": {"@value": concept["term"], "@language": "et"},
            "skos:definition": {
                "@value": concept["definition"], "@language": "et",
            },
            "estleg:definedIn": [{"@id": concept["provision_id"]}],
            "estleg:sourceAct": concept["law_title"],
            "rdfs:label": f"{concept['term']} ({concept['law_slug']})",
        }
        graph.append(node)
        _triples += 1  # definedIn — counted post-emission.
        if is_kov:
            _triples_kov += 1

    # ---- Canonical Concept nodes (#134) ----
    # Emitted AFTER the LegalConcept nodes so canonical IRIs are disambiguated
    # against the ids already claimed. Iterating ``concept_terms`` in sorted
    # order keeps the disambiguation sequence deterministic.
    concept_id_by_term: dict[str, str] = {}
    for term_norm in concept_terms:
        entries = term_index[term_norm]
        cc_id = canonical_concept_id(term_norm, seen_concept_ids)
        seen_concept_ids.add(cc_id)
        concept_id_by_term[term_norm] = cc_id

        surface_forms = [e["term"] for e in entries]
        pref = _most_common(surface_forms)
        alt = sorted({s for s in surface_forms if s != pref})

        # Distinct definitions, ranked by frequency (desc) then alphabetically.
        # Group by the normalised wording; keep the most common surface form
        # of each group.
        def_groups: dict[str, list[str]] = {}
        for e in entries:
            if is_noise_definition(e["definition"]):
                continue
            def_groups.setdefault(
                _normalize_definition_for_dedup(e["definition"]), []
            ).append(e["definition"])
        ranked_defs = [
            _most_common(forms)
            for _norm, forms in sorted(
                def_groups.items(), key=lambda kv: (-len(kv[1]), kv[0])
            )
        ]
        emitted_defs = ranked_defs[:_MAX_CONCEPT_DEFINITIONS]

        node = {
            "@id": cc_id,
            "@type": ["owl:NamedIndividual", "estleg:Concept"],
            "skos:prefLabel": {"@value": pref, "@language": "et"},
            # F3 (#134): point at the provision-local definition nodes via
            # estleg:hasDefinitionNode — NOT estleg:definedIn, whose
            # owl:inverseOf estleg:definesTerm axiom is reserved for the
            # provision-local node → provision direction.
            "estleg:hasDefinitionNode": [
                {"@id": lc_id} for lc_id in legal_concept_nodes_by_term[term_norm]
            ],
            "estleg:definitionCount": {
                "@value": str(len(entries)),
                "@type": "xsd:integer",
            },
            "rdfs:label": pref,
        }
        if alt:
            node["skos:altLabel"] = [{"@value": a, "@language": "et"} for a in alt]
        if emitted_defs:
            node["skos:definition"] = [
                {"@value": d, "@language": "et"} for d in emitted_defs
            ]
        if len(ranked_defs) > _MAX_CONCEPT_DEFINITIONS:
            node["estleg:definitionVariantCount"] = {
                "@value": str(len(ranked_defs)),
                "@type": "xsd:integer",
            }
        graph.append(node)
        # Triples roughly: prefLabel + definitionCount + hasDefinitionNode
        # (k) + altLabel (|alt|) + definition (|emitted_defs|).
        _n_tr = 2 + len(legal_concept_nodes_by_term[term_norm]) + len(alt) + len(emitted_defs)
        if len(ranked_defs) > _MAX_CONCEPT_DEFINITIONS:
            _n_tr += 1
        _triples += _n_tr

    # ---- Hub links: provision-local definition node → canonical Concept ----
    # ``estleg:definesConcept`` (hub, O(k) total) is the sole link from each
    # provision-local definition node to its canonical Concept. This replaces
    # the O(k²) exactMatch clique between provision-local nodes.
    #
    # #326: the previous ``skos:exactMatch LegalConcept → Concept`` arc was
    # removed. SKOS ``exactMatch`` is for interchangeable PEER concepts across
    # schemes, not an instance→aggregator membership relation — and the
    # ``definesConcept`` / ``hasDefinitionNode`` pair already encodes that
    # membership. The bogus arc also let SKOS closure propagate the canonical
    # node's labels/definitions back onto every provision-local node.
    graph_node_by_id: dict[str, dict] = {}
    for node in graph:
        nid = node.get("@id", "")
        if nid:
            graph_node_by_id[nid] = node

    for term_norm, lc_ids in legal_concept_nodes_by_term.items():
        cc_id = concept_id_by_term.get(term_norm)
        if cc_id is None:  # noise term — no canonical Concept, no cross-link.
            continue
        for lc_id in lc_ids:
            lc_node = graph_node_by_id.get(lc_id)
            if lc_node is None:
                continue
            lc_node["estleg:definesConcept"] = {"@id": cc_id}
            _triples += 1
            if lc_id in kov_legal_concept_ids:
                _triples_kov += 1

    # ---- skos:closeMatch: Concept ↔ Concept (#134) ----
    # The fuzzy near-match relation now connects canonical Concept nodes to
    # each other (bidirectional), not the provision-local nodes.
    for cm in close_matches:
        id_a = concept_id_by_term.get(cm["term_a_lower"])
        id_b = concept_id_by_term.get(cm["term_b_lower"])
        if not id_a or not id_b or id_a == id_b:
            continue
        for src, dst in ((id_a, id_b), (id_b, id_a)):
            node = graph_node_by_id.get(src)
            if node is None:
                continue
            existing = node.get("skos:closeMatch", [])
            if isinstance(existing, dict):
                existing = [existing]
            existing.append({"@id": dst})
            node["skos:closeMatch"] = existing
            _triples += 1

    # Every estleg:definesConcept / skos:exactMatch / skos:closeMatch target
    # MUST point at a node we actually emitted. Trim any straggler (defensive;
    # the construction above makes this a no-op, but it catches regressions).
    for node in graph:
        for prop in ("skos:exactMatch", "skos:closeMatch", "estleg:definesConcept"):
            refs = node.get(prop)
            if not refs:
                continue
            single = isinstance(refs, dict)
            ref_list = [refs] if single else refs
            valid = [
                r for r in ref_list
                if isinstance(r, dict) and r.get("@id") in seen_concept_ids
            ]
            if not valid:
                node.pop(prop, None)
            elif single:
                node[prop] = valid[0]
            else:
                node[prop] = valid

    # Save combined concepts file
    combined_doc = {"@context": CONTEXT, "@graph": graph}
    stamp_combined_dataset_head(
        combined_doc,
        label="Estonian Legal Ontology — concepts combined",
    )
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

    # Canonical concepts ranked by how many provisions define them — a
    # reviewer can eyeball the head of this list for over-broad fragments
    # ("vee", "reovesi", …) that the automatic noise filter does not catch.
    concepts_ranked = sorted(
        ((t, len(term_index[t])) for t in concept_terms),
        key=lambda x: (-x[1], x[0]),
    )

    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "summary": {
            "total_laws_analyzed": len(laws),
            "laws_with_definitions": laws_with_concepts,
            "total_defined_terms": len(all_concepts),
            "unique_terms": len(unique_terms),
            "canonical_concepts": len(concept_terms),
            "concepts_filtered_noise": _concepts_filtered_noise,
            "terms_in_multiple_laws": len(exact_matches),
            "close_match_pairs": len(close_matches),
        },
        "top_laws_by_definitions": [
            {"slug": slug, "count": count}
            for slug, count in laws_ranked[:30]
        ],
        "most_referenced_concepts": [
            {"term": _most_common([e["term"] for e in term_index[t]]),
             "defined_in_provisions": count}
            for t, count in concepts_ranked[:50]
        ],
        # Noisy candidate labels surfaced for review instead of being treated
        # as normal concepts (kehtetu / placeholder / stopword-only terms).
        "noisy_candidate_terms": [
            {"term": _most_common([e["term"] for e in term_index[t]]),
             "occurrences": len(term_index[t])}
            for t in sorted(
                noise_terms, key=lambda x: (-len(term_index[x]), x)
            )
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
    print(f"  Canonical concepts:         {len(concept_terms)}")
    print(f"  Filtered noise terms:       {_concepts_filtered_noise}")
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
    # #134: surface the count of normalised terms dropped by the noise filter
    # (kehtetu / placeholder / stopword-only / pure-punctuation "terms"). It
    # is not a per-file skip, but skip_reasons is the only free-form counter
    # bag in the coverage schema.
    skip_reasons_for_report["concepts_filtered_noise"] = _concepts_filtered_noise
    write_coverage_report(
        CoverageReport(
            pipeline="extract_legal_concepts",
            run_timestamp=PINNED_RUN_TIMESTAMP,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
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
