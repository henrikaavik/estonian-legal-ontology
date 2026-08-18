#!/usr/bin/env python3
"""
Map Estonian laws to the EU directives they transpose, using EUR-Lex SPARQL.

Data source: EUR-Lex SPARQL endpoint — national transposition measures for Estonia.
Matches transposition titles against existing Estonian law ontology entries.

Generates:
  - krr_outputs/reports/transposition_mapping.json   (report of all matches)
  - krr_outputs/transposition_schema.json             (OWL property definitions)
  - Updates existing law JSON-LD files with estleg:transposesDirective
  - Updates EU directive entries with estleg:transposedBy
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from estleg_common import (
    CONTEXT,
    BUILD_EVALUATION_DATE,
    act_deprecation,
    iter_peep_files,
    jsonld_text,
    save_json as _save_json,
)
from eurlex_common import (
    SPARQL_ENDPOINT,
    sparql_query,
    sparql_query_with_retry as _sparql_query_with_retry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EURLEX_DIR = KRR_DIR / "eurlex"

NS = "https://w3id.org/estleg/"

PAGE_SIZE = 5000
RATE_DELAY = 1.5  # seconds between SPARQL requests



def save_json(filepath: Path, doc: dict):
    """Write a JSON document to disk with UTF-8 encoding."""
    _save_json(filepath, doc)


def load_json(filepath: Path) -> dict:
    """Load a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching.

    Lowercase; NFKD-decompose and *drop* combining marks so Estonian
    diacritics collapse the same way the filename-derived law names do
    (``õ``→``o``, ``ä``→``a`` …); strip a trailing run of "consolidation"
    digits that EUR-Lex appends to NIM titles (``"AUDIITORTEGEVUSE
    SEADUS1"`` → ``audiitortegevuse seadus``); collapse whitespace.
    """
    text = text.lower().strip()
    # Decompose and drop combining marks (diacritics) — keeps matching
    # consistent with the underscore→space filename-derived index keys.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Drop a trailing consolidation digit run (``seadus1``, ``seadus2`` …).
    text = re.sub(r"\d+$", "", text).strip()
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text


def extract_law_name(title: str) -> str | None:
    """
    Try to extract an Estonian law name from a transposition measure title.

    Patterns like:
      - "Alkoholiseadus"
      - "Isikuandmete kaitse seadus"
      - Things ending in "seadus", "seadustik", "määrus"
    """
    # Strip a trailing consolidation digit run that EUR-Lex appends to
    # some NIM titles (``"Tubakaseadus1"`` → ``"Tubakaseadus"``).
    title_norm = re.sub(r"\d+$", "", title.strip()).strip()

    # Try to find explicit law name patterns
    # Pattern: title IS the law name (short titles)
    if re.search(r"seadus(?:tik)?$", title_norm, re.IGNORECASE):
        return title_norm

    # Pattern: "... seadus ..." — extract up to "seadus/seadustik"
    m = re.search(r"^(.+?\s*seadus(?:tik)?)\b", title_norm, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    return None


def sparql_query_with_retry(
    query: str, *, retries: int = 3, backoff: float = 2.0
) -> list[dict]:
    # Forwards to the shared helper; resolves ``sparql_query`` from this
    # module so tests that monkeypatch it on this module still take effect.
    return _sparql_query_with_retry(
        query, query_fn=sparql_query, retries=retries, backoff=backoff
    )


# Authority URI for Estonia in the Publications Office country vocabulary.
ESTONIA_COUNTRY_URI = "http://publications.europa.eu/resource/authority/country/EST"


def fetch_transposition_measures(
    *, allow_partial: bool = False
) -> tuple[list[dict], bool]:
    """
    Fetch national transposition measures for Estonia from EUR-Lex.

    The CDM models a National Implementing Measure (NIM) as a ``work``
    typed (effectively) ``cdm:measure_national_implementing`` that carries:
      * ``cdm:measure_national_implementing_implemented_by_country`` → the
        country authority URI (we filter for Estonia);
      * ``cdm:measure_national_implementing_implements_directive`` → the
        directive ``work`` URI it transposes;
      * ``cdm:work_title`` → the (national-language) title — for Estonian
        NIMs this is already the Estonian act title (e.g.
        ``"TÖÖLEPINGU SEADUS"``), so no per-language expression join is
        needed.
    The earlier query used ``cdm:resource_legal_measures_transposition_for``
    and required the directive to be ``a cdm:directive`` with a
    ``"7"``-prefixed national CELEX in an EST *expression* — none of which
    holds in CELLAR today, so it returned zero rows (#129).

    Returns ``(items, partial)`` where each item is a dict with
    ``celex_dir``, ``directive_uri``, ``title_nat`` and ``partial`` is
    ``True`` iff the sweep stopped early due to a terminal SPARQL
    failure under ``allow_partial``. Without ``allow_partial``, terminal
    failures propagate as ``RuntimeError`` so the run exits non-zero
    rather than silently truncating the dataset (#129).

    Note: ``ORDER BY ?nim`` makes OFFSET pagination stable across
    pages — without it EUR-Lex may reorder rows between requests and
    drop/duplicate measures (#183).
    """
    all_items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    offset = 0
    partial = False

    while True:
        query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?nim ?directive ?celex_dir ?title_nat WHERE {{
  ?nim cdm:measure_national_implementing_implemented_by_country <{ESTONIA_COUNTRY_URI}> .
  ?nim cdm:measure_national_implementing_implements_directive ?directive .
  ?directive cdm:resource_legal_id_celex ?celex_dir .
  OPTIONAL {{
    ?nim cdm:work_title ?title_nat .
    FILTER(lang(?title_nat) = 'et' || lang(?title_nat) = '')
  }}
}} ORDER BY ?nim LIMIT {PAGE_SIZE} OFFSET {offset}
"""
        print(f"  Fetching transposition measures, offset {offset}...")
        try:
            bindings = sparql_query_with_retry(query)
        except Exception as e:
            if allow_partial:
                print(f"  ERROR at offset {offset} (partial run): {e}")
                partial = True
                break
            raise

        if not bindings:
            break

        for b in bindings:
            celex_dir = b.get("celex_dir", {}).get("value", "")
            title_binding = b.get("title_nat", {})
            title_nat = title_binding.get("value", "")
            directive_uri = b.get("directive", {}).get("value", "")

            if not title_nat:
                # A NIM without a title cannot be matched to an Estonian
                # act by name — skip it (it still counts in EUR-Lex's NIM
                # tally but not in ours).
                continue

            # Post-fetch language guard (#318): CELLAR carries the same NIM
            # title in several languages (et/fr/en/…); the dedup key
            # ``(celex_dir, title_nat)`` differs per language, so without this
            # guard one NIM is processed once per language — inflating
            # ``total_measures_fetched``/``total_unmatched`` and risking a
            # false ``transposesDirective`` link when a short non-Estonian
            # title slips under the fuzzy floor. The SPARQL ``FILTER`` above
            # already requests only ``et``/untagged titles; this mirrors that
            # constraint defensively for any endpoint that returns a foreign
            # language tag through the OPTIONAL anyway. An untagged literal
            # (empty ``xml:lang``) is kept — Estonian NIM titles are the
            # untagged case in CELLAR today.
            title_lang = title_binding.get("xml:lang", "")
            if title_lang and title_lang.lower() != "et":
                continue

            key = (celex_dir, title_nat)
            if key in seen:
                continue
            seen.add(key)

            all_items.append({
                "celex_dir": celex_dir,
                "directive_uri": directive_uri,
                "title_nat": title_nat,
            })

        if len(bindings) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(RATE_DELAY)

    return all_items, partial


def fetch_directive_deadlines(*, allow_partial: bool = False) -> tuple[dict[str, str], bool]:
    """Fetch the transposition deadline (``cdm:directive_date_transposition``)
    for every directive that declares one (#96).

    Returns ``({celex: "YYYY-MM-DD"}, partial)``. A directive may carry
    several deadline values (corrigenda); we keep the earliest so the
    resulting node has one deterministic ``estleg:transpositionDeadline``.
    This is a single, cheap query (no pagination needed — the property is
    sparse), so a terminal failure under ``allow_partial`` just yields an
    empty map and ``partial=True`` (deadlines are optional, #96), while
    without ``allow_partial`` it propagates like every other SPARQL
    failure in this script (#129).
    """
    query = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT ?celex (MIN(?d) AS ?deadline) WHERE {
  ?work a cdm:directive .
  ?work cdm:resource_legal_id_celex ?celex .
  ?work cdm:directive_date_transposition ?d .
} GROUP BY ?celex
"""
    print("  Fetching directive transposition deadlines...")
    try:
        bindings = sparql_query_with_retry(query)
    except Exception as e:
        if allow_partial:
            print(f"  ERROR fetching directive deadlines (partial run): {e}")
            return {}, True
        raise

    deadlines: dict[str, str] = {}
    for b in bindings:
        celex = b.get("celex", {}).get("value", "")
        deadline = b.get("deadline", {}).get("value", "")
        if not celex or not deadline:
            continue
        # The SPARQL ``MIN`` already picks the earliest; defend anyway.
        cur = deadlines.get(celex)
        if cur is None or deadline < cur:
            deadlines[celex] = deadline
    return deadlines, False


def update_directive_deadlines(deadlines: dict[str, str]) -> int:
    """Add ``estleg:transpositionDeadline`` (xsd:date) to directive nodes in
    ``eurlex_directives_peep.json`` for every CELEX with a known deadline (#96).

    Emits the property only when a deadline is present (it is optional).
    Returns the count of directive nodes updated. This is the low-risk
    path for #96 — it patches the already-generated directives peep file
    instead of forcing a network-heavy ``generate_eu_legislation.py``
    rerun; ``generate_eu_legislation.py`` also emits the property on a
    full regen (see its ``OPTIONAL ?deadline`` projection).
    """
    directives_file = EURLEX_DIR / "eurlex_directives_peep.json"
    if not directives_file.exists() or not deadlines:
        return 0
    try:
        data = load_json(directives_file)
    except Exception as e:
        print(f"  ERROR loading directives file for deadlines: {e}")
        return 0

    updated = 0
    for node in data.get("@graph", []):
        celex = node.get("estleg:celexNumber", "")
        deadline = deadlines.get(celex)
        if not deadline:
            continue
        new_val = {"@value": deadline, "@type": "xsd:date"}
        if node.get("estleg:transpositionDeadline") == new_val:
            continue
        node["estleg:transpositionDeadline"] = new_val
        updated += 1

    if updated > 0:
        save_json(directives_file, data)
    return updated


def clear_directive_deadlines() -> int:
    """Strip ``estleg:transpositionDeadline`` from every directive node in
    ``eurlex_directives_peep.json`` (mirrors the other clear-then-rebuild
    steps so a deadline that disappeared upstream does not linger)."""
    directives_file = EURLEX_DIR / "eurlex_directives_peep.json"
    if not directives_file.exists():
        return 0
    try:
        data = load_json(directives_file)
    except Exception:
        return 0
    cleared = 0
    for node in data.get("@graph", []):
        if "estleg:transpositionDeadline" in node:
            del node["estleg:transpositionDeadline"]
            cleared += 1
    if cleared > 0:
        save_json(directives_file, data)
    return cleared


def build_law_index(index_data: dict) -> dict[str, dict]:
    """
    Build a lookup from normalized law names to their file info.
    Returns: {normalized_name: {"name": ..., "files": [...], "source_act": ...}}
    """
    law_index: dict[str, dict] = {}

    for law in index_data.get("laws", []):
        name = law.get("name", "")
        files = [
            file
            for file in law.get("files", [])
            if isinstance(file, str) and (KRR_DIR / file).exists()
        ]
        if not name or not files:
            continue

        # Load the first file to get the sourceAct name. The file is
        # guaranteed to exist (the comprehension above filtered on
        # ``.exists()``), but the read can still fail on malformed/unreadable
        # JSON or an absent ``@graph``/``sourceAct`` — in which case we fall
        # back to the filename-derived key below.
        first_file = KRR_DIR / files[0]
        source_act = ""
        try:
            data = load_json(first_file)
            # #578: skip deprecated/replaced acts (the corrupt ``volaigusseadus``
            # slug, the retired ``VOS_*`` family) so transposition matching can't
            # fan a directive onto a retired VÕS decomposition alongside the
            # canonical ``volaoigusseadus_*``. Follows dcterms:isReplacedBy.
            if act_deprecation(data)[0]:
                continue
            for node in data.get("@graph", []):
                sa = jsonld_text(node.get("estleg:sourceAct", ""))
                if sa:
                    source_act = sa
                    break
        except Exception:
            pass

        # Index by several normalized forms
        if source_act:
            key = normalize_text(source_act)
            law_index[key] = {
                "name": name,
                "files": files,
                "source_act": source_act,
            }

        # Also index by the filename-derived name (underscores → spaces)
        name_spaced = name.replace("_", " ")
        key2 = normalize_text(name_spaced)
        if key2 not in law_index:
            law_index[key2] = {
                "name": name,
                "files": files,
                "source_act": source_act or name_spaced,
            }

    return law_index


def build_directive_index() -> dict[str, str]:
    """
    Build a lookup from CELEX number to estleg IRI for EU directives.
    Returns: {celex: "estleg:EU_XXXXX"}
    """
    directive_index: dict[str, str] = {}
    directives_file = EURLEX_DIR / "eurlex_directives_peep.json"

    if not directives_file.exists():
        print(f"  WARNING: {directives_file} not found, directive linking will be limited")
        return directive_index

    data = load_json(directives_file)
    for node in data.get("@graph", []):
        celex = node.get("estleg:celexNumber", "")
        node_id = node.get("@id", "")
        if celex and node_id:
            directive_index[celex] = node_id

    return directive_index


def resolve_directive_iri(celex: str, directive_index: dict[str, str]) -> str | None:
    """Return the known ontology IRI for a directive CELEX, if present."""
    return directive_index.get(celex) or None


def build_directive_subject_index() -> dict[str, str]:
    """Build a lookup from CELEX to the directive's normalized subject text (#388).

    The subject text is the directive's ``rdfs:label`` (its official title,
    e.g. ``"… ühenduse raudteede ohutuse kohta …"``), ``normalize_text``-ed so
    it can be substring-matched against a law's domain root with the same
    diacritic folding the rest of this module uses. Missing/empty labels map to
    ``""`` so the caller treats them as "no subject signal" and fails open
    (keeps every co-amended law) rather than dropping legitimate links.
    """
    subject_index: dict[str, str] = {}
    directives_file = EURLEX_DIR / "eurlex_directives_peep.json"
    if not directives_file.exists():
        return subject_index

    data = load_json(directives_file)
    for node in data.get("@graph", []):
        celex = node.get("estleg:celexNumber", "")
        if not celex:
            continue
        label = node.get("rdfs:label", "")
        # ``rdfs:label`` is normally a plain string here, but tolerate the
        # JSON-LD ``{"@value": "...", "@language": "et"}`` object form too.
        if isinstance(label, dict):
            label = label.get("@value", "")
        if isinstance(label, list):  # pragma: no cover - defensive
            label = " ".join(
                (item.get("@value", "") if isinstance(item, dict) else str(item))
                for item in label
            )
        subject_index[celex] = normalize_text(label) if label else ""

    return subject_index


# A law name's domain root (its name minus the ``seadus``/``seadustik`` suffix)
# shorter than this many characters is too generic for a *bare substring* test
# against a directive subject (root ``vee`` would hit inside ``veebruar``). Such
# a short root is therefore matched against the subject with a stricter
# WHOLE-WORD anchor instead of a plain substring (see
# ``_law_matches_directive_subject``), so a genuine short-root transposer that is
# explicitly named in a non-primary clause (``tolliseadus`` ↔ a customs
# directive, #597) is still recovered while an incidental mid-word fragment is
# not. Longer roots stay on the permissive plain-substring test.
_MIN_DOMAIN_ROOT_LEN = 6

# Estonian coordinating conjunctions and the comma that separate the laws
# enumerated in a combined amending-act title. Everything up to (but not
# including) the FIRST of these markers is the title's *primary clause* — the
# law a combined bill is principally named for (#388).
_CLAUSE_SEPARATOR_RE = re.compile(r"\bja\b|\bning\b|,")


def _primary_clause(title_norm: str) -> str:
    """Return the primary clause of a (normalized) combined amending-act title.

    A title such as ``"raudteeseaduse ja riigiloivuseaduse muutmise seadus"``
    is principally about the first-named law (``raudteeseadus``); the laws after
    the first ``ja``/``ning``/comma are co-amended secondaries. This returns the
    substring before the first such separator (the whole title if there is
    none), so the caller can tell whether a matched law sits in that primary
    clause (#388).
    """
    match = _CLAUSE_SEPARATOR_RE.search(title_norm)
    return title_norm[: match.start()] if match else title_norm


def _domain_root(name_norm: str) -> str:
    """Strip the trailing ``seadus``/``seadustik`` from a normalized law name.

    ``"raudteeseadus"`` → ``"raudtee"``; ``"halduskohtumenetluse seadustik"`` →
    ``"halduskohtumenetluse"``. The remaining root is the law's domain keyword,
    used to test whether a co-amended law actually belongs to the directive's
    subject area (#388). A name that is *only* the generic word ``seadus`` (no
    domain prefix) yields an empty root.
    """
    root = re.sub(r"\s*seadus(?:tik)?\s*$", "", name_norm).strip()
    return root


def _law_matches_directive_subject(name_norm: str, subject_norm: str) -> bool:
    """Return ``True`` iff a law plausibly shares the directive's domain (#388).

    Both arguments are already ``normalize_text``-ed. The law's domain root (its
    name minus the ``seadus`` suffix) must appear in the directive subject — e.g.
    root ``raudtee`` is inside the railway directive's ``"… raudteede ohutuse …"``.

    A root of at least ``_MIN_DOMAIN_ROOT_LEN`` characters is specific enough to
    test as a plain substring. A *short* root (``tolli``, ``relva`` …) is too
    generic for a bare substring (root ``vee`` ⊂ ``veebruar``) and used to be
    rejected outright — which silently dropped a genuine short-root transposer
    that is explicitly named in a non-primary clause of the NIM title, e.g.
    ``tolliseadus`` (root ``tolli``, len 5) in ``"Maksukorralduse seaduse,
    tolliseaduse muutmise seadus"`` transposing a customs directive (#597). Such
    a short root is instead matched with a stricter WHOLE-WORD anchor (the same
    left-boundary rule the rest of this module uses): ``tolli`` anchoring
    ``tolliseadustiku`` in the directive subject counts, but the same fragment
    buried mid-word does not — recovering the real transposer without re-admitting
    the coincidental substring hits the length floor was protecting against.

    An empty subject means the directive carries no title to discriminate on, so
    the caller must NOT rely on this guard (it fails open elsewhere) — here it
    conservatively returns ``False``.
    """
    if not subject_norm:
        return False
    root = _domain_root(name_norm)
    if not root:
        return False
    if len(root) < _MIN_DOMAIN_ROOT_LEN:
        # Short root: require a whole-word anchor in the subject, not a bare
        # substring, so a genuine named transposer (``tolli`` ⊂
        # ``tolliseadustiku``) is recovered while an incidental mid-word
        # fragment stays out (#597).
        return _contains_whole(root, subject_norm)
    return root in subject_norm


# Minimum length for a fuzzy substring/word-boundary match. A law name
# shorter than this is too generic to safely anchor a directive↔act link.
_MIN_FUZZY_MATCH_LEN = 10


def _contains_whole(needle: str, haystack: str) -> bool:
    """Return ``True`` iff ``needle`` starts a word in ``haystack``.

    Both arguments are already ``normalize_text``-ed (lowercased, ASCII-folded,
    whitespace-collapsed). The match is anchored on a LEFT word boundary only:
    ``needle`` must begin at a word start (preceded by a non-word char or the
    string start) but MAY be followed by more word characters.

    Why left-anchored, not both sides: Estonian law names are concatenated
    nouns inflected by case, so two things are true at once —
      * The killer false-positive class is a short name being the *suffix* of a
        longer compound: ``teeseadus`` sits at the tail of ``raudteeseadus``,
        which a left boundary correctly rejects (the char before ``teeseadus``
        is the word char ``d``) — fixing the #265 ``Raudteeseadus``→``Teeseadus``
        bug.
      * Legitimate references appear in the genitive, where the nominative key
        ``liiklusseadus`` is a *prefix* of the inflected ``liiklusseaduse`` in
        the title. Anchoring the right side too (``(?!\\w)``) would wrongly
        reject these, so we only require the left boundary.
    """
    if not needle or not haystack:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}", haystack) is not None


def _best_fuzzy_match(
    title_norm: str, law_index: dict[str, dict]
) -> dict | None:
    """Pick the single best whole-word match for ``title_norm`` from the index.

    Mirrors a single deterministic policy across BOTH the name-key and the
    ``source_act`` passes (#265): track the LONGEST whole-word match, apply the
    ``_MIN_FUZZY_MATCH_LEN`` floor, and break ties deterministically (longest
    key, then lexicographically smallest law name) so the result never depends
    on ``dict`` iteration order. The earlier code accepted the *first* match in
    dict order with only a length-5 floor on the ``source_act`` pass, which was
    both order-dependent (nondeterministic) and produced false links.
    """
    best_info: dict | None = None
    best_len = 0
    best_name = ""

    def _consider(token: str, info: dict) -> None:
        nonlocal best_info, best_len, best_name
        if len(token) < _MIN_FUZZY_MATCH_LEN:
            return
        if not _contains_whole(token, title_norm):
            return
        name = info.get("name", "")
        # Deterministic order: longer token wins; on a tie, the
        # lexicographically smaller law name wins.
        if best_info is None or len(token) > best_len or (
            len(token) == best_len and name < best_name
        ):
            best_info = info
            best_len = len(token)
            best_name = name

    for key, info in law_index.items():
        _consider(key, info)
        source_norm = normalize_text(info.get("source_act", ""))
        if source_norm and source_norm != key:
            _consider(source_norm, info)

    return best_info


def match_all_titles_to_laws(
    title: str,
    law_index: dict[str, dict],
    directive_subject: str | None = None,
) -> list[dict]:
    """Return the Estonian law(s) a transposition measure title transposes into.

    A combined amending act such as
    ``"A seaduse ja B seaduse muutmise seadus"`` may transpose a directive into
    *both* ``A seadus`` and ``B seadus``; returning only one of them silently
    drops the secondary link (#288). This walks the index and collects every
    law whose name/``source_act`` key appears in the title as a whole word
    (length-floored, like the single-match path), de-duplicated by law name and
    returned in a deterministic order (name length desc, then name asc) so the
    emitted links — and the report — are byte-stable across runs.

    Co-amendment guard (#388): an omnibus bill often amends laws that have
    nothing to do with the directive (a fee-schedule tweak rides along with a
    sector reform), and the bare #288 logic linked all of them — e.g.
    ``meresoiduohutuse_seadus`` (Maritime Safety) got tied to ``32004L0049``
    (Railway Safety) because both appear in
    ``"Lennunduseaduse, meresõiduohutuse seaduse ja raudteeseaduse muutmise
    seadus"``. When ``directive_subject`` (the directive's normalized title) is
    supplied, a matched law is kept only if EITHER it sits in the title's
    *primary clause* (before the first ``ja``/``ning``/comma — the law the bill
    is principally named for) OR its domain root matches the directive subject
    (``raudtee`` ⊂ ``"… raudteede ohutuse …"``). With no subject available the
    function fails open and keeps every match, preserving the #288 behaviour for
    callers that cannot supply one.

    A direct/extracted full-title match still short-circuits to that single law
    (it is the strongest signal and avoids spuriously pulling in shorter
    embedded names).
    """
    title_norm = normalize_text(title)

    # Strongest signal: the whole (normalized) title, or the extracted law
    # name, is itself an index key — return exactly that law. A single-law
    # title needs no co-amendment guard.
    if title_norm in law_index:
        return [law_index[title_norm]]
    law_name = extract_law_name(title)
    if law_name:
        law_name_norm = normalize_text(law_name)
        if law_name_norm in law_index:
            return [law_index[law_name_norm]]

    # Otherwise collect every whole-word match (handles multi-law titles).
    primary_clause = _primary_clause(title_norm)
    matches: list[dict] = []
    seen_names: set[str] = set()
    for key, info in law_index.items():
        tokens = [key]
        source_norm = normalize_text(info.get("source_act", ""))
        if source_norm and source_norm != key:
            tokens.append(source_norm)
        matched_tokens = [
            tok
            for tok in tokens
            if len(tok) >= _MIN_FUZZY_MATCH_LEN and _contains_whole(tok, title_norm)
        ]
        if not matched_tokens:
            continue
        name = info.get("name", "")
        if name in seen_names:
            continue

        # Co-amendment guard (#388): only filter when we have a directive
        # subject to discriminate on; otherwise fail open (keep the match).
        if directive_subject:
            in_primary = any(
                _contains_whole(tok, primary_clause) for tok in matched_tokens
            )
            subject_hit = any(
                _law_matches_directive_subject(tok, directive_subject)
                for tok in matched_tokens
            )
            if not (in_primary or subject_hit):
                # A secondary, off-subject co-amendment — drop the spurious
                # link (Maritime Safety ↔ Railway Safety, fees ↔ sector, …).
                continue

        seen_names.add(name)
        matches.append(info)

    # Deterministic order independent of dict iteration: longer (more
    # specific) names first, then lexicographic.
    matches.sort(key=lambda info: (-len(info.get("name", "")), info.get("name", "")))
    return matches


def match_title_to_law(title: str, law_index: dict[str, dict]) -> dict | None:
    """
    Try to match a transposition measure title to a single Estonian law.

    Uses progressively looser matching, but every fuzzy step now requires a
    whole-word match (so ``teeseadus`` does not match inside ``raudteeseadus``)
    with a length floor and a deterministic tie-break, making the result
    independent of ``law_index`` iteration order (#265). For combined
    amending-act titles that reference several laws, prefer
    ``match_all_titles_to_laws`` — this helper returns only the single best
    (longest) match.
    """
    title_norm = normalize_text(title)

    # Direct full match
    if title_norm in law_index:
        return law_index[title_norm]

    # Extract law name from title and try to match
    law_name = extract_law_name(title)
    if law_name:
        law_name_norm = normalize_text(law_name)
        if law_name_norm in law_index:
            return law_index[law_name_norm]

    # Fuzzy fallback: single best whole-word match, deterministic + floored.
    return _best_fuzzy_match(title_norm, law_index)


def generate_schema() -> dict:
    """Generate OWL schema definitions for transposition properties."""
    schema_nodes: list[dict] = [
        # ObjectProperty: transposesDirective
        {
            "@id": "estleg:transposesDirective",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "võtab üle direktiivi (transposes directive)",
            "rdfs:comment": "Links an Estonian act to the EU directive it transposes.",
            "rdfs:domain": {"@id": "estleg:Act"},
            "rdfs:range": {"@id": "estleg:EULegislation"},
        },
        # ObjectProperty: transposedBy (inverse)
        {
            "@id": "estleg:transposedBy",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "üle võetud (transposed by)",
            "rdfs:comment": "Inverse of transposesDirective — links an EU directive to the national law that transposes it.",
            "rdfs:domain": {"@id": "estleg:EULegislation"},
            "rdfs:range": {"@id": "estleg:Act"},
            "owl:inverseOf": {"@id": "estleg:transposesDirective"},
        },
        # DatatypeProperty: transpositionStatus
        {
            "@id": "estleg:transpositionStatus",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "ülevõtmise staatus (transposition status)",
            "rdfs:comment": "Status of directive transposition: full, partial, or unknown.",
            "rdfs:domain": {"@id": "estleg:Act"},
            "rdfs:range": {"@id": "xsd:string"},
        },
        # NOTE: ``estleg:transpositionDeadline`` (the directive's transposition
        # deadline, #96) is intentionally NOT declared here. It is a corpus-wide
        # reusable property and lives in ``krr_outputs/controlled_vocabulary.jsonld``;
        # declaring it again in this per-layer schema would trip
        # ``validate_all.validate_id_uniqueness`` (same @id in two files).
    ]

    return {"@context": CONTEXT, "@graph": schema_nodes}


def find_law_transposition_target(data: dict) -> dict | None:
    """Find the act-level node that receives transposition links."""
    graph = data.get("@graph", [])
    for node in graph:
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" in types:
            return node
    return graph[0] if graph else None


def get_law_transposition_target_iri(filepath: Path) -> str | None:
    """Load a law file and return the real node IRI used for transposition links."""
    try:
        data = load_json(filepath)
    except Exception as e:
        print(f"    ERROR loading {filepath.name}: {e}")
        return None

    target_node = find_law_transposition_target(data)
    if target_node is None:
        return None

    node_id = target_node.get("@id")
    return node_id if isinstance(node_id, str) and node_id else None


def collect_transposition_file_links(
    law_files: list[str],
    *,
    directive_iri: str,
    directive_celex: str,
    matched_law_name: str,
    law_file_directives: dict[str, list[str]],
    directive_celex_to_law_iris: dict[str, list[str]],
    missing_law_iris: list[dict],
    krr_dir: Path | None = None,
) -> None:
    """Record forward and inverse transposition links for one matched law.

    A file is queued for ``estleg:transposesDirective`` only when its
    act-level IRI resolves, so the inverse ``estleg:transposedBy`` write
    stays paired (#319). ``law_iri`` is resolved once per file.
    """
    base = KRR_DIR if krr_dir is None else krr_dir
    for law_file in law_files:
        filepath = base / law_file
        law_iri = get_law_transposition_target_iri(filepath)
        if law_iri is None:
            missing_law_iris.append({
                "directive_celex": directive_celex,
                "law_file": law_file,
                "matched_law_name": matched_law_name,
            })
            continue
        filepath_str = str(filepath)
        if filepath_str not in law_file_directives:
            law_file_directives[filepath_str] = []
        if directive_iri not in law_file_directives[filepath_str]:
            law_file_directives[filepath_str].append(directive_iri)
        if directive_celex not in directive_celex_to_law_iris:
            directive_celex_to_law_iris[directive_celex] = []
        if law_iri not in directive_celex_to_law_iris[directive_celex]:
            directive_celex_to_law_iris[directive_celex].append(law_iri)


def update_law_file(filepath: Path, directive_ids: list[str]) -> bool:
    """
    Add estleg:transposesDirective to the act-level node in a law file.
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

    target_node = find_law_transposition_target(data)
    if target_node is None:
        return False

    # Build directive references
    dir_refs = [{"@id": did} for did in directive_ids]
    existing = target_node.get("estleg:transposesDirective", [])
    if isinstance(existing, dict):
        existing = [existing]

    existing_ids = {ref.get("@id") for ref in existing if isinstance(ref, dict)}
    new_refs = [ref for ref in dir_refs if ref["@id"] not in existing_ids]

    if not new_refs:
        return False

    all_refs = existing + new_refs
    target_node["estleg:transposesDirective"] = all_refs

    # Set transposition status as unknown (EUR-Lex doesn't tell us full vs partial)
    if "estleg:transpositionStatus" not in target_node:
        target_node["estleg:transpositionStatus"] = "unknown"

    save_json(filepath, data)
    return True


def update_directive_file(directive_celex_to_laws: dict[str, list[str]]) -> int:
    """
    Add estleg:transposedBy to EU directive entries in the directives file.
    Returns count of updated directive nodes.
    """
    directives_file = EURLEX_DIR / "eurlex_directives_peep.json"
    if not directives_file.exists():
        print("  WARNING: Directives file not found, skipping inverse links")
        return 0

    try:
        data = load_json(directives_file)
    except Exception as e:
        print(f"  ERROR loading directives file: {e}")
        return 0

    updated = 0
    graph = data.get("@graph", [])

    for node in graph:
        celex = node.get("estleg:celexNumber", "")
        if celex not in directive_celex_to_laws:
            continue

        law_ids = directive_celex_to_laws[celex]
        law_refs = [{"@id": lid} for lid in law_ids]

        existing = node.get("estleg:transposedBy", [])
        if isinstance(existing, dict):
            existing = [existing]

        existing_ids = {ref.get("@id") for ref in existing if isinstance(ref, dict)}
        new_refs = [ref for ref in law_refs if ref["@id"] not in existing_ids]

        if not new_refs:
            continue

        all_refs = existing + new_refs
        node["estleg:transposedBy"] = all_refs

        updated += 1

    if updated > 0:
        save_json(directives_file, data)

    return updated


def clear_transposition_from_file(filepath: Path) -> bool:
    """
    Remove estleg:transposesDirective and estleg:transpositionStatus from all
    nodes in a law JSON-LD file. Returns True if the file was modified.
    """
    try:
        data = load_json(filepath)
    except Exception:
        return False

    modified = False
    for node in data.get("@graph", []):
        if "estleg:transposesDirective" in node:
            del node["estleg:transposesDirective"]
            modified = True
        if "estleg:transpositionStatus" in node:
            del node["estleg:transpositionStatus"]
            modified = True

    if modified:
        save_json(filepath, data)
    return modified


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Record an intentional empty snapshot (with ``documented_empty: "
            "true``) instead of failing when EUR-Lex returns zero "
            "transposition measures for Estonia. Without this flag a "
            "zero-measure result is treated as an error and the run exits "
            "non-zero (#129)."
        ),
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Continue and write partial output (with ``partial: true`` in "
            "the report) if a SPARQL pagination request fails after retries, "
            "rather than aborting the run."
        ),
    )
    return parser.parse_args()


def _write_documented_empty_report(*, partial: bool, reason: str) -> Path:
    """Write the current-shape report for an intentionally empty layer."""
    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "source": SPARQL_ENDPOINT,
        "country": "EST",
        "documented_empty": True,
        "documented_empty_reason": reason,
        "partial": partial,
        "total_measures_fetched": 0,
        "total_matched": 0,
        "total_unmatched": 0,
        "total_skipped_missing_directives": 0,
        "total_skipped_missing_law_iris": 0,
        "unique_directives": 0,
        "unique_laws": 0,
        "law_files_updated": 0,
        "directive_nodes_updated": 0,
        "mappings": [],
        "unmatched_sample": [],
        "missing_directives_sample": [],
        "missing_law_iris_sample": [],
    }
    report_path = KRR_DIR / "reports" / "transposition_mapping.json"
    save_json(report_path, report)
    return report_path


def main():
    args = parse_args()
    print("=" * 60)
    print("Generate transposition mapping: Estonian laws ↔ EU directives")
    print(f"Endpoint: {SPARQL_ENDPOINT}")
    print("=" * 60)

    # --- Step 0: Clear existing transposition data ---
    print("\n--- Clearing existing transposition data ---")
    cleared_count = 0
    for peep_file in iter_peep_files(include_kov=False):  # KOV does not apply
        if peep_file.parent != KRR_DIR:
            continue
        if clear_transposition_from_file(peep_file):
            cleared_count += 1
    print(f"  Cleared transposition data from {cleared_count} files")

    # Also clear transposedBy from directive files
    directives_path = EURLEX_DIR / "eurlex_directives_peep.json"
    if directives_path.exists():
        try:
            with open(directives_path, "r", encoding="utf-8") as df:
                dir_doc = json.load(df)
            modified = False
            for node in dir_doc.get("@graph", []):
                if "estleg:transposedBy" in node:
                    del node["estleg:transposedBy"]
                    modified = True
            if modified:
                save_json(directives_path, dir_doc)
                print("  Cleared transposedBy from directives file")
        except Exception as e:
            print(f"  Warning: could not clear directives file: {e}")

    # And clear transpositionDeadline so a deadline removed upstream
    # does not linger on the directive node (#96).
    cleared_deadlines = clear_directive_deadlines()
    if cleared_deadlines:
        print(f"  Cleared transpositionDeadline from {cleared_deadlines} directive node(s)")

    # --- Step 1: Load existing indexes ---
    print("\n--- Loading existing indexes ---")

    index_path = KRR_DIR / "INDEX.json"
    if not index_path.exists():
        print(f"ERROR: {index_path} not found. Run generate_all_laws.py first.")
        sys.exit(1)
    index_data = load_json(index_path)
    print(f"  Loaded INDEX.json: {index_data.get('total_laws', 0)} laws")

    # Build law lookup index
    print("  Building law name index...")
    law_index = build_law_index(index_data)
    print(f"  Law index entries: {len(law_index)}")

    # Build directive lookup
    print("  Building directive CELEX index...")
    directive_index = build_directive_index()
    print(f"  Directive index entries: {len(directive_index)}")

    # Build directive subject (title) lookup so a combined amending-act title
    # only links the laws whose domain matches the directive — not every law
    # co-amended in the same omnibus bill (#388).
    directive_subject_index = build_directive_subject_index()

    # --- Step 2: Generate schema ---
    print("\n--- Generating transposition schema ---")
    schema_doc = generate_schema()
    schema_path = KRR_DIR / "transposition_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name}")

    # --- Step 3: Fetch transposition measures from EUR-Lex ---
    print("\n--- Fetching transposition measures for Estonia ---")
    measures, was_partial = fetch_transposition_measures(
        allow_partial=args.allow_partial
    )
    print(f"  Total transposition measures found: {len(measures)}")

    if not measures:
        if args.allow_empty:
            report_path = _write_documented_empty_report(
                partial=was_partial,
                reason=(
                    "EUR-Lex returned zero Estonian transposition measures; "
                    "recorded as an intentional empty snapshot via --allow-empty."
                ),
            )
            print(f"  Saved documented-empty {report_path.name}")
            if was_partial:
                sys.exit(2)
            return
        # Zero-fetch is NOT a success: the transposition layer is
        # advertised in README, so an empty + unflagged layer is a
        # defect (#129). Leave the existing report untouched and exit
        # non-zero so callers/CI notice.
        print(
            "  ERROR: zero transposition measures fetched from EUR-Lex. "
            "The endpoint may be unavailable or the query may need updating. "
            "Pass --allow-empty to record an intentional empty snapshot, "
            "or --allow-partial if a mid-sweep SPARQL error truncated results."
        )
        sys.exit(1)

    # --- Step 4: Match measures to Estonian laws ---
    print("\n--- Matching measures to Estonian law ontology entries ---")
    matched_mappings: list[dict] = []
    unmatched_titles: list[str] = []

    # Track which law files need which directives
    law_file_directives: dict[str, list[str]] = {}  # filepath → [directive IRI, ...]
    # Track which directives are transposed by which law IRIs
    directive_celex_to_law_iris: dict[str, list[str]] = {}  # celex → [law ontology node IRI, ...]
    missing_directives: list[dict] = []
    missing_law_iris: list[dict] = []

    for measure in measures:
        celex_dir = measure["celex_dir"]
        title_nat = measure["title_nat"]

        # A combined amending act ("A seaduse ja B seaduse muutmise seadus")
        # transposes the directive into BOTH laws — emit a link for each so the
        # secondary law is not silently dropped (#288) — but exclude laws that
        # are merely co-amended in the same omnibus bill and do not belong to
        # the directive's subject area (#388).
        directive_subject = directive_subject_index.get(celex_dir, "")
        law_matches = match_all_titles_to_laws(
            title_nat, law_index, directive_subject=directive_subject
        )
        if not law_matches:
            unmatched_titles.append(title_nat)
            continue

        # Determine the directive IRI in our ontology
        directive_iri = resolve_directive_iri(celex_dir, directive_index)
        if not directive_iri:
            missing_directives.append({
                "directive_celex": celex_dir,
                "national_title": title_nat,
                "matched_law_name": law_matches[0]["name"],
            })
            continue

        for law_match in law_matches:
            # Track the mapping
            mapping_entry = {
                "directive_celex": celex_dir,
                "directive_iri": directive_iri,
                "national_title": title_nat,
                "matched_law_name": law_match["name"],
                "matched_source_act": law_match.get("source_act", ""),
                "law_files": law_match["files"],
            }
            matched_mappings.append(mapping_entry)

            collect_transposition_file_links(
                law_match["files"],
                directive_iri=directive_iri,
                directive_celex=celex_dir,
                matched_law_name=law_match["name"],
                law_file_directives=law_file_directives,
                directive_celex_to_law_iris=directive_celex_to_law_iris,
                missing_law_iris=missing_law_iris,
            )

    print(f"  Matched: {len(matched_mappings)}")
    print(f"  Unmatched: {len(unmatched_titles)}")
    print(f"  Skipped missing directives: {len(missing_directives)}")
    print(f"  Skipped missing law IRIs: {len(missing_law_iris)}")

    # Deduplicate matched mappings (same law + same directive)
    seen_pairs: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for m in matched_mappings:
        key = (m["directive_celex"], m["matched_law_name"])
        if key not in seen_pairs:
            seen_pairs.add(key)
            deduped.append(m)
    matched_mappings = deduped
    print(f"  Unique law-directive pairs: {len(matched_mappings)}")

    # --- Step 5: Update Estonian law JSON-LD files ---
    print("\n--- Updating Estonian law JSON-LD files ---")
    files_updated = 0
    for filepath_str, dir_iris in law_file_directives.items():
        filepath = Path(filepath_str)
        if not filepath.exists():
            print(f"    SKIP (not found): {filepath.name}")
            continue
        if update_law_file(filepath, dir_iris):
            files_updated += 1
            print(f"    Updated: {filepath.name} ({len(dir_iris)} directive(s))")

    print(f"  Total law files updated: {files_updated}")

    # --- Step 6: Update EU directive file with inverse links ---
    print("\n--- Adding inverse transposedBy links to EU directives ---")
    directives_updated = update_directive_file(directive_celex_to_law_iris)
    print(f"  Directive nodes updated: {directives_updated}")

    # --- Step 6b: Add transposition deadlines to EU directive nodes (#96) ---
    print("\n--- Adding transposition deadlines to EU directives ---")
    deadlines, deadlines_partial = fetch_directive_deadlines(
        allow_partial=args.allow_partial
    )
    if deadlines_partial:
        was_partial = True
    deadline_nodes_updated = update_directive_deadlines(deadlines)
    print(
        f"  Directive deadlines fetched: {len(deadlines)}; "
        f"directive nodes annotated: {deadline_nodes_updated}"
    )

    # --- Step 7: Generate report ---
    print("\n--- Generating transposition mapping report ---")

    # Count unique directives and laws
    unique_directives = set(m["directive_celex"] for m in matched_mappings)
    unique_laws = set(m["matched_law_name"] for m in matched_mappings)

    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "source": SPARQL_ENDPOINT,
        "country": "EST",
        "documented_empty": False,
        "partial": was_partial,
        "total_measures_fetched": len(measures),
        "total_matched": len(matched_mappings),
        "total_unmatched": len(unmatched_titles),
        "total_skipped_missing_directives": len(missing_directives),
        "total_skipped_missing_law_iris": len(missing_law_iris),
        "unique_directives": len(unique_directives),
        "unique_laws": len(unique_laws),
        "law_files_updated": files_updated,
        "directive_nodes_updated": directives_updated,
        "directive_deadlines_fetched": len(deadlines),
        "directive_deadline_nodes_updated": deadline_nodes_updated,
        "mappings": sorted(matched_mappings, key=lambda m: m["directive_celex"]),
        "unmatched_sample": unmatched_titles[:50],
        "missing_directives_sample": missing_directives[:50],
        "missing_law_iris_sample": missing_law_iris[:50],
    }

    report_path = KRR_DIR / "reports" / "transposition_mapping.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("Transposition mapping complete!")
    print(f"  Measures fetched from EUR-Lex:  {len(measures)}")
    print(f"  Matched to Estonian laws:       {len(matched_mappings)}")
    print(f"  Unmatched:                      {len(unmatched_titles)}")
    print(f"  Skipped missing directives:     {len(missing_directives)}")
    print(f"  Skipped missing law IRIs:       {len(missing_law_iris)}")
    print(f"  Unique EU directives:           {len(unique_directives)}")
    print(f"  Unique Estonian laws:           {len(unique_laws)}")
    print(f"  Law files updated:              {files_updated}")
    print(f"  Directive nodes updated:        {directives_updated}")
    print(f"  Directive deadlines fetched:    {len(deadlines)}")
    print(f"  Directive nodes w/ deadline:    {deadline_nodes_updated}")
    if was_partial:
        print("  NOTE: run was PARTIAL — re-run without --allow-partial when "
              "EUR-Lex is healthy to refresh the layer.")
    print("\nOutputs:")
    print(f"  {report_path.relative_to(REPO_ROOT)}")
    print(f"  {schema_path.relative_to(REPO_ROOT)}")
    print("=" * 60)

    # #417: combined is the consumer entry point; rebuild it from the
    # now-enriched peeps so transposedBy / transpositionDeadline survive.
    from generate_eu_legislation import rebuild_eurlex_combined_from_peeps

    combined_stats = rebuild_eurlex_combined_from_peeps(EURLEX_DIR)
    print(
        f"  Rebuilt eurlex_combined.jsonld from peeps "
        f"({combined_stats['nodes']} nodes)"
    )

    if was_partial:
        # ``--allow-partial`` was set (otherwise the run would have raised
        # before reaching here). Non-zero exit signals downstream that the
        # report/peep files should be refreshed once a clean run is
        # possible.
        sys.exit(2)


if __name__ == "__main__":
    main()
