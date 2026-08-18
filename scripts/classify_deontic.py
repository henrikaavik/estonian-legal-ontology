#!/usr/bin/env python3
"""
Classify legal provisions by normative type (obligation, right, permission, prohibition).

Analyses the estleg:summary text of each provision node in the JSON-LD law files
and assigns an estleg:normativeType based on Estonian deontic language patterns.
Also attempts to extract estleg:dutyHolder where possible.

Outputs:
  - Updated *_peep.json files with estleg:normativeType and estleg:dutyHolder
  - krr_outputs/deontic_classification_report.json  (statistics)
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

from estleg_common import (
    BUILD_EVALUATION_DATE,
    iter_peep_files,
    jsonld_text,
    save_json,  # #376: atomic tempfile+os.replace writer (replaces local non-atomic def)
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

NS = "https://w3id.org/estleg/"

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

# ---------- deontic pattern definitions ----------

# A cue's *kind* controls extra disambiguation in ``_cue_hits`` (#276):
#   * "plain"   — an ordinary affirmative cue. It is skipped when negated
#                 (preceded by ei/pole/ega within a small token window).
#   * "modal"   — the polysemous modals ``peab`` / ``tuleb``. ``peab`` is also
#                 3sg of *pidama* ("keeps", e.g. "peab registrit"); ``tuleb``
#                 is also "comes". They only count as obligation when a nearby
#                 infinitive (-ma / -da / -ta, e.g. "peab … esitama",
#                 "tuleb … tasuda") confirms the deontic reading. They are also
#                 negation-guarded like "plain" cues.
#   * "negative"— a cue that *already* encodes negation (``ei tohi`` etc.). The
#                 negation guard must NOT cancel these, so they are exempt.
KIND_PLAIN = "plain"
KIND_MODAL = "modal"
KIND_NEGATIVE = "negative"

# Negation particles. A "plain"/"modal" cue occurrence is ignored when one of
# these appears within ``_NEGATION_WINDOW`` tokens immediately before the cue.
_NEGATION_WORDS = {"ei", "pole", "ega"}
_NEGATION_WINDOW = 3
# How many tokens on EITHER side of a "modal" cue to scan for a confirming
# infinitive. The window is widened (was 6) so that (a) SOV order, where the
# infinitive precedes the modal ("Esitada tuleb dokument"), and (b) trailing
# infinitives pushed back by intervening citations ("tuleb … §-s 951 …
# määrata") are both still recognised as obligations (#329).
_INFINITIVE_WINDOW = 10
# An Estonian -ma / -da / -ta infinitive: a multi-letter word ending in one of
# those suffixes (e.g. esitama, tagama, maksma; tasuda, esitada; maksta).
_INFINITIVE_RE = re.compile(
    r"[A-Za-zÄÖÜÕŠŽäöüõšž]{2,}(?:ma|da|ta)\b", re.IGNORECASE | re.UNICODE
)
_TOKEN_RE = re.compile(r"[A-Za-zÄÖÜÕŠŽäöüõšž]+", re.UNICODE)

# Each entry: (compiled regex, weight, kind)
# Higher weight = stronger signal for that normative type.
OBLIGATION_PATTERNS = [
    (re.compile(r"\bon kohustatud\b", re.IGNORECASE), 3, KIND_PLAIN),
    (re.compile(r"\bpeab\b", re.IGNORECASE), 2, KIND_MODAL),
    # #584: ``peavad`` is the plural ("must", 3pl of *pidama*) counterpart of
    # ``peab`` — equally polysemous ("they keep …"), so it is a MODAL cue
    # guarded by the same nearby-infinitive requirement. 3,012 provisions use
    # it; only the singular ``peab`` was listed before, so plural obligations
    # ("Töötajad peavad … esitama") were missed.
    (re.compile(r"\bpeavad\b", re.IGNORECASE), 2, KIND_MODAL),
    (re.compile(r"\btuleb\b", re.IGNORECASE), 2, KIND_MODAL),
    (re.compile(r"\bon kohustus\b", re.IGNORECASE), 3, KIND_PLAIN),
    (re.compile(r"\bkohustub\b", re.IGNORECASE), 3, KIND_PLAIN),
    (re.compile(r"\bon sunnitud\b", re.IGNORECASE), 2, KIND_PLAIN),
    # #381: predicative mandatory cues with no modal polysemy.
    # "X on kohustuslik" ("is mandatory"); "on nõutav"/"on nõutud" ("is
    # required").
    (re.compile(r"\bon kohustuslik\b", re.IGNORECASE), 3, KIND_PLAIN),
    (re.compile(r"\bon nõutav\b", re.IGNORECASE), 2, KIND_PLAIN),
    (re.compile(r"\bon nõutud\b", re.IGNORECASE), 2, KIND_PLAIN),
]

RIGHT_PATTERNS = [
    (re.compile(r"\bon õigus\b", re.IGNORECASE), 3, KIND_PLAIN),
    (re.compile(r"\bõigus on\b", re.IGNORECASE), 3, KIND_PLAIN),
    (re.compile(r"\bon õigustatud\b", re.IGNORECASE), 3, KIND_PLAIN),
    (re.compile(r"\bon lubatud nõuda\b", re.IGNORECASE), 3, KIND_PLAIN),
]

# #351: the weight-1 ``võib`` permissive cue is suppressed in penal
# provisions (see ``_KARISTATAKSE_RE`` below), so it is captured as a named
# constant to identify it precisely in ``score_text``.
_VOIB_RE = re.compile(r"\bvõib\b", re.IGNORECASE)

# #584: the split ``on … keelatud`` form, where a subject sits between the
# copula and the participle ("on isikul keelatud", "on … vööndis keelatud").
# Strict adjacency (``\bon keelatud\b``) missed 400 such provisions. The gap is
# capped at 1–6 non-period word tokens so the match never crosses a sentence
# boundary (``[^.\s]+`` forbids the period that ends a clause). Like ``ei
# tohi`` it is a NEGATIVE cue (its prohibition force is intrinsic, not subject
# to the ei/pole/ega guard).
_ON_KEELATUD_SPLIT_RE = re.compile(
    r"\bon\b(?:\s+[^.\s]+){1,6}\s+keelatud\b", re.IGNORECASE
)

PERMISSION_PATTERNS = [
    (re.compile(r"\bon lubatud\b", re.IGNORECASE), 3, KIND_PLAIN),
    (re.compile(r"\btohib\b", re.IGNORECASE), 3, KIND_PLAIN),
    (re.compile(r"\bon vaba\b", re.IGNORECASE), 2, KIND_PLAIN),
    (_VOIB_RE, 1, KIND_PLAIN),  # permissive context
]

PROHIBITION_PATTERNS = [
    # #584: ``on keelatud`` ("is forbidden") — the strict-adjacency form. The
    # split form ("on … keelatud", subject between copula and participle,
    # e.g. "on isikul keelatud") is covered by _ON_KEELATUD_SPLIT_RE below;
    # 634 provisions carry the split form that strict adjacency missed.
    (re.compile(r"\bon keelatud\b", re.IGNORECASE), 4, KIND_PLAIN),
    # #584: the split copula…participle form (see _ON_KEELATUD_SPLIT_RE). It is
    # NEGATIVE-kind so the ei/pole/ega guard does not cancel it, but it does
    # not duplicate-score with the strict ``on keelatud`` cue above because the
    # strict form's ``on keelatud`` substring contains no intervening token and
    # so cannot also satisfy this {1,6}-gap pattern.
    (_ON_KEELATUD_SPLIT_RE, 4, KIND_NEGATIVE),
    (re.compile(r"\bei tohi\b", re.IGNORECASE), 4, KIND_NEGATIVE),
    # #584: ``ei või`` ("may not") is the canonical negated-permission =
    # prohibition form. The affirmative ``võib`` loses its ``-b`` under
    # negation (``ei või``), so the weight-1 permissive ``_VOIB_RE`` (\bvõib\b)
    # never matched it: 1,433 provisions said "X ei või …" yet none scored as
    # Prohibition (most fell through to Permission/Obligation). It is a
    # NEGATIVE cue (already encodes its negation, so the ei/pole/ega guard
    # must not cancel it) and outweighs the bare permissive ``võib``.
    (re.compile(r"\bei või\b", re.IGNORECASE), 4, KIND_NEGATIVE),
    (re.compile(r"\bei ole lubatud\b", re.IGNORECASE), 3, KIND_NEGATIVE),
    (re.compile(r"\bpole lubatud\b", re.IGNORECASE), 3, KIND_NEGATIVE),
    (re.compile(r"\bon karistatav\b", re.IGNORECASE), 3, KIND_PLAIN),
    # #351: the canonical Penal Code passive ("… eest karistatakse …
    # vangistusega"). Strong prohibition signal; outweighs the polysemous
    # judicial-discretion ``võib`` that otherwise mislabels penal provisions
    # as Permission.
    (re.compile(r"\bkaristatakse\b", re.IGNORECASE), 4, KIND_PLAIN),
]

# #351: ``karistatakse`` (penal passive) frequently co-occurs with a
# judicial-discretion ``võib`` ("kohus võib määrata …"). That weight-1
# Permission cue must be suppressed in penal provisions so they are not
# mislabelled NormType_Permission instead of NormType_Prohibition.
_KARISTATAKSE_RE = re.compile(r"\bkaristatakse\b", re.IGNORECASE)

NORM_TYPES = {
    "obligation": ("estleg:NormType_Obligation", OBLIGATION_PATTERNS),
    "right": ("estleg:NormType_Right", RIGHT_PATTERNS),
    "permission": ("estleg:NormType_Permission", PERMISSION_PATTERNS),
    "prohibition": ("estleg:NormType_Prohibition", PROHIBITION_PATTERNS),
}
NORM_TYPE_DEFINITION = "estleg:NormType_Definition"
NORM_TIE_PRIORITY = {
    "prohibition": 4,
    "obligation": 3,
    "right": 2,
    "permission": 1,
}

# ---------- duty-holder extraction ----------

# Pattern: "<noun phrase> peab / on kohustatud / kohustub / ..."
# The first word must be Capital-initial. Subsequent words must be either
# Capital-initial themselves (e.g. "Vastutav Töötleja") or one of a small
# whitelist of Estonian connectors ("ja", "või", "ning", "ega") so that
# adverbials such as "Igal aastal …" do not get absorbed into the
# duty-holder phrase. Hyphenation inside a single word (e.g. "Tervise-")
# is supported via the trailing ``-`` in the letter character class.
# Digits are deliberately excluded (we use an explicit Estonian letter
# class instead of ``\w``).
_LETTER = r"[A-Za-zÄÖÜÕŠŽäöüõšž-]"
_CAPITAL_WORD = rf"[A-ZÄÖÜÕŠŽ]{_LETTER}+"
_CONNECTOR = r"(?:ja|või|ning|ega)\b"
DUTY_HOLDER_RE = re.compile(
    rf"(\b{_CAPITAL_WORD}"
    rf"(?:\s+(?:{_CAPITAL_WORD}|{_CONNECTOR})){{0,4}})"
    r"\s+(?:peab|on kohustatud|kohustub|on sunnitud|tuleb)",
    re.UNICODE,
)
GENERIC_DUTY_HOLDER_STARTS = {"Käesolev", "Käesoleva", "Paragrahv", "Seadus", "Lõige", "Punkt"}


def load_json(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"  WARN: cannot load {filepath.name}: {exc}")
        return None


def _preceding_tokens(text: str, start: int, window: int) -> list[str]:
    """Lower-cased word tokens in the ``window`` slots before offset ``start``."""
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text[:start])][-window:]


def _is_negated(text: str, start: int) -> bool:
    """True when a negation particle sits just before the cue at ``start``."""
    return any(
        tok in _NEGATION_WORDS
        for tok in _preceding_tokens(text, start, _NEGATION_WINDOW)
    )


def _has_following_infinitive(text: str, end: int) -> bool:
    """True when a -ma/-da/-ta infinitive appears within the forward window."""
    tail = text[end:]
    cutoff = 0
    for _ in range(_INFINITIVE_WINDOW):
        nxt = _TOKEN_RE.search(tail, cutoff)
        if nxt is None:
            break
        cutoff = nxt.end()
    return _INFINITIVE_RE.search(tail[:cutoff]) is not None


def _has_preceding_infinitive(text: str, start: int) -> bool:
    """True when a -ma/-da/-ta infinitive appears within the backward window.

    Covers SOV order where the infinitive comes *before* the modal cue
    ("Esitada tuleb dokument", "Tasuda peab töötaja") — #329.
    """
    head = text[:start]
    cutoff = len(head)
    for _ in range(_INFINITIVE_WINDOW):
        prev = None
        for m in _TOKEN_RE.finditer(head, 0, cutoff):
            prev = m
        if prev is None:
            break
        cutoff = prev.start()
    return _INFINITIVE_RE.search(head[cutoff:]) is not None


def _has_nearby_infinitive(text: str, start: int, end: int) -> bool:
    """True when a confirming infinitive sits in the window on either side of
    the modal cue spanning ``[start, end)`` (forward OR backward) — #329."""
    return _has_following_infinitive(text, end) or _has_preceding_infinitive(
        text, start
    )


def _cue_hits(text: str, pat: re.Pattern, kind: str) -> bool:
    """Whether ``pat`` has at least one *valid* occurrence in ``text``.

    "negative" cues (which already encode negation, e.g. ``ei tohi``) are taken
    verbatim. "plain"/"modal" cues skip any occurrence that is negated by a
    preceding ei/pole/ega; "modal" cues (``peab``/``tuleb``) additionally
    require a nearby infinitive — in EITHER direction (#329) — so the
    polysemous "keeps"/"comes" readings do not score as obligations.
    """
    if kind == KIND_NEGATIVE:
        return pat.search(text) is not None
    for m in pat.finditer(text):
        if _is_negated(text, m.start()):
            continue
        if kind == KIND_MODAL and not _has_nearby_infinitive(
            text, m.start(), m.end()
        ):
            continue
        return True
    return False


def score_text(text: str, patterns: list[tuple[re.Pattern, int, str]]) -> int:
    """Sum weights of all patterns with a valid match in *text*."""
    # #351: in penal provisions ("… karistatakse …") the weight-1 ``võib``
    # permissive cue is a judicial-discretion phrase ("kohus võib määrata"),
    # not a grant of permission. Suppress it so penal provisions classify as
    # Prohibition rather than Permission.
    suppress_voib = _KARISTATAKSE_RE.search(text) is not None
    total = 0
    for pat, weight, kind in patterns:
        if suppress_voib and pat is _VOIB_RE:
            continue
        if _cue_hits(text, pat, kind):
            total += weight
    return total


# #584: the boundary that ends a leading main clause and opens a trailing
# ``kui`` / colon-list condition. A permission stated in the main clause
# ("X võib … kehtetuks tunnistada") is the operative norm; an obligation cue
# that appears ONLY inside the trailing condition ("…, kui isik on esitanud …")
# describes when the permission applies, not a competing obligation. The split
# point is the first ``kui`` / ``:`` / ``;`` marker.
_SUBORDINATE_BOUNDARY_RE = re.compile(r"\bkui\b|:|;", re.IGNORECASE)
# How far into the main clause a permission cue may start and still count as
# the operative "leading" verb. Provision summaries open with a "(N) " marker
# plus a short subject, so this keeps real leading permissions in scope.
_LEADING_PERMISSION_WINDOW = 80
# The position weight added to a structurally-leading permission so it wins the
# tie against an obligation/right cue confined to the trailing condition. Two
# points clear the +1 NORM_TIE_PRIORITY gap that previously handed these to
# Obligation, plus the obligation's own weight (cues are weight 2-3).
_LEADING_PERMISSION_BOOST = 3


def _valid_permission_in(text: str, segment: str, *, penal: bool) -> bool:
    """True iff ``segment`` carries a valid (non-negated) permission cue.

    Reuses ``score_text``'s rules: a penal judicial-discretion ``võib`` is
    excluded, and an occurrence negated by a preceding ei/pole/ega (so the
    ``ei või`` prohibition form) does not count as permission.
    """
    for pat, _weight, _kind in PERMISSION_PATTERNS:
        if penal and pat is _VOIB_RE:
            continue
        for m in pat.finditer(segment):
            if not _is_negated(segment, m.start()):
                return True
    return False


def _leading_permission_over_condition(text: str) -> bool:
    """True for the #584 "leading permission, trailing ``kui`` obligation" shape.

    The mislabel only occurs when ALL hold:
      * there is a subordinate-clause boundary (``kui`` / ``:`` / ``;``) — the
        pattern is specifically a main clause + a *condition*, so with no
        boundary there is no condition to discount and the boost never fires;
      * a valid permission cue sits in the main clause, starting within
        ``_LEADING_PERMISSION_WINDOW`` chars (the operative leading verb);
      * the main clause carries NO competing obligation/right cue of its own
        (those would be genuine, not condition-bound).

    Requiring the boundary keeps the boost from firing on two independent
    sentences (e.g. "Isikul on õigus …. Tegevus on lubatud …"), where the
    permission is not leading and tie-priority must still decide.
    """
    boundary = _SUBORDINATE_BOUNDARY_RE.search(text)
    if boundary is None:
        return False
    main_clause = text[: boundary.start()]
    penal = _KARISTATAKSE_RE.search(text) is not None
    # The permission must lead the main clause (start within the window).
    window = main_clause[:_LEADING_PERMISSION_WINDOW]
    if not _valid_permission_in(text, window, penal=penal):
        return False
    # If the MAIN clause already states an obligation/right, that is a genuine
    # competing norm (not condition-bound) — leave tie-priority to decide.
    if score_text(main_clause, OBLIGATION_PATTERNS) > 0:
        return False
    if score_text(main_clause, RIGHT_PATTERNS) > 0:
        return False
    return True


# #461: definition titles / drafting formulas. Applied to heading / label /
# first line only — a mid-text ``mõiste`` in an operative body must not
# steal the deontic label.
_MOISTE_WORD_RE = re.compile(r"\bmõiste\b", re.IGNORECASE | re.UNICODE)
_SECTION_MOISTE_HEADING_RE = re.compile(
    r"^§?\s*\d+.*mõiste", re.IGNORECASE | re.UNICODE
)
# Ticket #461 named these as the definition pre-filter (X on Y is too
# broad for mixed §§; ``tähendab`` / ``mõistetakse`` are the legal
# definition verbs).
# First-line-only verbs. ``mõiste`` stays heading-only so a mid-text
# mention in an operative first line does not become Definition.
DEFINITION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\btähendab\b", re.IGNORECASE | re.UNICODE),
    re.compile(r"\bmõistetakse\b", re.IGNORECASE | re.UNICODE),
]


def _is_definition_heading(heading: str) -> bool:
    """True when *heading* is a definition title (whole-word ``mõiste``)."""
    title = heading.splitlines()[0].strip() if heading else ""
    if not title:
        return False
    if _MOISTE_WORD_RE.search(title):
        return True
    if _SECTION_MOISTE_HEADING_RE.search(title):
        return True
    if title.casefold().endswith("mõiste"):
        return True
    return False


def _is_definition_text(text: str) -> bool:
    """True when the first line uses a definition verb (#461)."""
    first = text.splitlines()[0].strip() if text else ""
    if not first:
        return False
    return any(pat.search(first) for pat in DEFINITION_PATTERNS)


def classify_provision(text: str, heading: str | None = None) -> str | None:
    """Return the dominant normative-type IRI, or None if nothing matched.

    Definition headings and first-line definition verbs are tagged
    ``estleg:NormType_Definition`` so deontic verbs inside the definition
    body cannot assign Permission/Obligation (#461).
    """
    # Classify as Definition BEFORE scoring. Prefer an explicit heading;
    # otherwise only the first line of *text* (never the rest of the body).
    candidate = heading.strip() if heading else ""
    if not candidate and isinstance(text, str) and text:
        candidate = text.splitlines()[0]
    if _is_definition_heading(candidate) or (
        isinstance(text, str) and _is_definition_text(text)
    ):
        return NORM_TYPE_DEFINITION

    scores: dict[str, int] = {}
    for norm_key, (iri, patterns) in NORM_TYPES.items():
        s = score_text(text, patterns)
        if s > 0:
            scores[norm_key] = s

    if not scores:
        return None

    # #584: a permission in the leading main clause is the operative norm; an
    # obligation/right cue that only appears in the trailing ``kui`` condition
    # must not steal the label via tie-priority. Boost a structurally-leading
    # permission so it wins. Only applied when permission is actually a
    # candidate and competes with obligation/right — a prohibition cue (e.g.
    # ``ei või``) is stronger and is left to win on its own weight.
    if (
        "permission" in scores
        and ("obligation" in scores or "right" in scores)
        and "prohibition" not in scores
        and _leading_permission_over_condition(text)
    ):
        scores["permission"] += _LEADING_PERMISSION_BOOST

    best = max(scores, key=lambda k: (scores[k], NORM_TIE_PRIORITY[k]))
    return NORM_TYPES[best][0]


def extract_duty_holder(text: str) -> str | None:
    """Try to extract who must comply from the provision text."""
    for m in DUTY_HOLDER_RE.finditer(text):
        holder = m.group(1).strip()
        if holder.split()[0] in GENERIC_DUTY_HOLDER_STARTS:
            continue
        return holder
    return None


def main() -> None:
    print("=" * 70)
    print("Estonian Legal Ontology - Deontic Classification")
    print("=" * 70)

    _start = time.perf_counter()
    law_files = iter_peep_files()  # uses new include_kov=True default
    _kov_files = [f for f in law_files if "regulations/kov" in str(f)]
    print(f"\n[1/3] Found {len(law_files)} law files to process")

    # Coverage counters
    _files_processed = 0
    _files_processed_kov = 0
    _files_with_output = 0
    _files_with_output_kov = 0
    _files_skipped = 0
    _skip_reasons: dict[str, int] = {}
    _triples = 0
    _triples_kov = 0
    _fallback_hits = 0
    _unresolved = 0
    _failures: list[str] = []

    # --- Clearing pass: remove old deontic data from all files ---
    print("  Clearing old deontic classification data from all files...")
    for filepath in law_files:
        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue
        cleared = False
        for node in doc["@graph"]:
            if "estleg:normativeType" in node:
                del node["estleg:normativeType"]
                cleared = True
            if "estleg:dutyHolder" in node:
                del node["estleg:dutyHolder"]
                cleared = True
        if cleared:
            save_json(filepath, doc)
    print("  Done clearing.")

    # Per-law and per-type statistics
    stats_per_law: dict[str, dict[str, int]] = {}
    stats_per_type: dict[str, int] = defaultdict(int)
    total_provisions = 0
    total_classified = 0
    total_duty_holders = 0
    files_modified = 0

    for idx, filepath in enumerate(law_files, 1):
        is_kov = "regulations/kov" in str(filepath)
        try:
            doc = load_json(filepath)
            if doc is None:
                _files_skipped += 1
                _skip_reasons["json_decode_error"] = (
                    _skip_reasons.get("json_decode_error", 0) + 1
                )
                continue
            if "@graph" not in doc:
                _files_skipped += 1
                _skip_reasons["no_graph"] = _skip_reasons.get("no_graph", 0) + 1
                continue

            _triples_before = _triples
            law_name = filepath.stem.replace("_peep", "")
            law_stats: dict[str, int] = defaultdict(int)
            modified = False

            for node in doc["@graph"]:
                # Generators may emit estleg:summary as a plain string or as a
                # {"@value": ..., "@language": "et"} value object; jsonld_text
                # normalises both before the regex-based classifier runs.
                summary = jsonld_text(node.get("estleg:summary", ""))
                if not summary:
                    continue

                total_provisions += 1

                heading = jsonld_text(node.get("rdfs:label", ""))
                norm_iri = classify_provision(summary, heading=heading)
                if norm_iri:
                    node["estleg:normativeType"] = {"@id": norm_iri}
                    _triples += 1
                    if is_kov:
                        _triples_kov += 1
                    total_classified += 1
                    # Readable key for stats
                    short = norm_iri.split("_", 1)[1] if "_" in norm_iri else norm_iri
                    law_stats[short] += 1
                    stats_per_type[short] += 1
                    modified = True

                holder = extract_duty_holder(summary)
                if holder:
                    node["estleg:dutyHolder"] = holder
                    _triples += 1
                    if is_kov:
                        _triples_kov += 1
                    total_duty_holders += 1
                    modified = True

            if modified:
                save_json(filepath, doc)
                files_modified += 1

            if law_stats:
                stats_per_law[law_name] = dict(law_stats)

            _files_processed += 1
            if is_kov:
                _files_processed_kov += 1
            if _triples > _triples_before:
                _files_with_output += 1
                if is_kov:
                    _files_with_output_kov += 1
        except Exception as exc:  # noqa: BLE001
            _failures.append(f"{filepath.name}: {exc!r}")

        if idx % 100 == 0 or idx == len(law_files):
            print(f"  [{idx}/{len(law_files)}] processed – {total_classified} classified so far")

    # ---------- report ----------
    print("\n[2/3] Generating report...")

    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "summary": {
            "total_law_files": len(law_files),
            "files_modified": files_modified,
            "total_provisions_with_text": total_provisions,
            "total_classified": total_classified,
            "total_duty_holders_extracted": total_duty_holders,
            "classification_rate": f"{total_classified / max(total_provisions, 1) * 100:.1f}%",
        },
        "by_normative_type": dict(sorted(stats_per_type.items(), key=lambda x: -x[1])),
        "by_law": {
            k: v for k, v in sorted(stats_per_law.items(), key=lambda x: -sum(x[1].values()))
        },
    }

    report_path = KRR_DIR / "deontic_classification_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # ---------- summary ----------
    print("\n[3/3] SUMMARY")
    print("=" * 70)
    print(f"  Provisions analysed:    {total_provisions}")
    print(f"  Classified:             {total_classified}")
    print(f"  Duty holders extracted: {total_duty_holders}")
    print(f"  Files modified:         {files_modified}")
    print()
    for ntype, count in sorted(stats_per_type.items(), key=lambda x: -x[1]):
        print(f"    {ntype:20s}  {count}")
    print("=" * 70)

    # ---------- coverage report ----------
    _wall, _rate, _peak_mb = measure_runtime(_start, _files_processed)
    write_coverage_report(
        CoverageReport(
            pipeline="classify_deontic",
            run_timestamp=PINNED_RUN_TIMESTAMP,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(law_files),
            input_files_kov=len(_kov_files),
            files_processed=_files_processed,
            files_processed_kov=_files_processed_kov,
            files_with_output=_files_with_output,
            files_with_output_kov=_files_with_output_kov,
            files_skipped=_files_skipped,
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
        / "classify_deontic_coverage.json",
    )


if __name__ == "__main__":
    main()
