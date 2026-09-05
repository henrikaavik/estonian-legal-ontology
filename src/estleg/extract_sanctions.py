#!/usr/bin/env python3
"""
Extract penalties and sanctions from law text.

Scans provision legalText (falling back to summary) for Estonian sanction
patterns — imprisonment, pecuniary punishment (including turnover-share
fines), fines, arrest, coercive payments, confiscation and compulsory
dissolution — and creates estleg:Sanction nodes linked to the originating
provision.

Scope of extraction (issue #681)
--------------------------------
Two gates keep the extractors on provisions that actually IMPOSE a penalty:

* A code's **general part** (``üldosa``) is skipped outright. It defines the
  sanction system rather than any offence penalty, so extracting from it
  manufactures phantom sanctions. See ``_is_general_part``.
* The life-imprisonment pattern additionally requires a **sentencing
  formula** (``karistatakse``) in the provision text, which suppresses the
  same error wherever else a statute merely refers to life imprisonment
  (register-retention rules, use-of-force provisions, sentencing rules).

Text is read once whole and once per **lõige** (subsection), merging into a
single dedup, so a section that punishes its subsections differently
contributes all of their penalties rather than only the first. See
``extract_sanctions`` and ``split_loiked``.

Outputs:
  - krr_outputs/sanctions/  (per-law sanction JSON-LD files)
  - krr_outputs/reports/sanctions_report.json

``sanctions_report.json`` (including the EUR-normalized ``severity_index``)
is a **non-graph application artifact** (issue #462). Severity scores are
not RDF properties; query ``estleg:hasSanction`` for the graph layer.

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
from decimal import Decimal, InvalidOperation
from functools import partial
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    CONTEXT,
    classifier_text,
    iter_peep_files,
    save_json,
)
from estleg.estleg_common import (
    sanitize_id as _shared_sanitize_id,
)
from estleg.kov_pipeline_coverage import (
    PINNED_RUN_TIMESTAMP,
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
SANCTION_DIR = KRR_DIR / "sanctions"
SANCTION_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://w3id.org/estleg/"

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


# Severity index: lower = less severe.
#
# Issue #681 adds the two KarS non-custodial "other punishments":
#
# * ``confiscation`` (konfiskeerimine, KarS §§ 83–85) scores **2**, the
#   same tier as ``fine``. Confiscation removes the instrument or proceeds
#   of the offence rather than imposing a new burden on the offender, so
#   it sits with the monetary sanctions and below ``pecuniary_punishment``.
# * ``compulsory_dissolution`` (sundlõpetamine, KarS § 46) scores **4**,
#   the same tier as ``arrest`` — i.e. between the monetary sanctions and
#   ``imprisonment``, as issue #681 specifies. It is the harshest sanction
#   available against a legal person (it ends the entity's existence), so
#   it must outrank every fine, but it is not a deprivation of liberty and
#   so must not outrank ``imprisonment``.
#
# Ties are intentional: severity is an ordinal *tier*, not a total order.
# ``main()`` resolves ``max_severity_type`` from the sanction that actually
# set the maximum, so a tie no longer mislabels the tier (see there).
SEVERITY = {
    "coercive_payment": 1,
    "fine": 2,
    "confiscation": 2,
    "pecuniary_punishment": 3,
    "arrest": 4,
    "compulsory_dissolution": 4,
    "imprisonment": 5,
}

# Human-readable labels per sanction type (English)
SANCTION_TYPE_LABELS = {
    "imprisonment": "Imprisonment",
    "pecuniary_punishment": "Pecuniary punishment",
    "fine": "Fine",
    "arrest": "Arrest",
    "coercive_payment": "Coercive payment",
    "confiscation": "Confiscation",
    "compulsory_dissolution": "Compulsory dissolution",
}


# Issue #376: the local non-atomic ``save_json`` (a bare
# ``open(filepath, "w")`` that truncates to 0 bytes before writing) is
# replaced by the atomic ``estleg_common.save_json`` (tempfile +
# ``os.replace``), imported at module top. A SIGINT/OOM/disk-full mid-write
# no longer leaves a zero-byte/partial JSON that ``load_json`` would
# silently swallow on the next run. The imported name still lives in this
# module's namespace, so existing ``monkeypatch.setattr(mod, "save_json",
# ...)`` test stubs continue to work.


def load_json(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"  WARN: cannot load {filepath.name}: {exc}")
        return None


sanitize_id = partial(_shared_sanitize_id, max_len=80, replace_dash=True)


def _provision_ref(node: dict) -> str:
    """Build a short human-readable provision reference like 'KarS § 121'.

    Returns an empty string when no usable reference can be derived.
    Previously the fallback emitted the literal ``'unknown'`` which
    polluted ``_build_label`` output (e.g. ``"Imprisonment, max 5 years (unknown)"``).
    ``_build_label`` already suppresses the parenthetical when the ref
    is empty, so returning ``""`` is the cleaner contract.
    """
    par = node.get("estleg:paragrahv", "")
    # Issue #302: ``estleg:paragrahv`` is stored WITH the section sign
    # (e.g. ``"\u00a7 53."``). The label template below already prepends a
    # ``\u00a7``, so without stripping we emit a doubled ``"AS \u00a7 \u00a7 53."``.
    # Strip a leading ``\u00a7`` (and surrounding whitespace) so the single
    # template ``\u00a7`` is the only one that survives.
    par = par.lstrip("\u00a7").strip()
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


def _node_types(node: dict) -> list[str]:
    """Return a node's ``@type`` as a list, tolerating the string form."""
    types = node.get("@type") or []
    if isinstance(types, str):
        return [types]
    return list(types)


def _find_act_node(doc: dict) -> dict | None:
    """Return the act (or act-part) root node from a peep file's @graph,
    or None if no such node is present.

    Match criteria: the node must be typed ``estleg:Act`` or
    ``estleg:Part``. Auxiliary ``owl:Ontology`` graph headers
    (concept-cluster manifests, combined dataset heads) are not acts and
    are ignored. #435 dropped ``owl:Ontology`` from domain individuals.

    Issue #681: multi-part laws are split into ``<law>_osaN_peep.json``
    files whose root node is typed ``estleg:Part`` **only** \u2014 commits
    ``c5625a748b`` / ``2676a1f81b`` retyped provisions and dropped the
    per-document act classes, leaving these roots without ``estleg:Act``.
    An ``estleg:Act``-only match therefore returned None for 37 root
    peeps (both Karistusseadustik parts, all eight Asja\u00f5igusseadus
    parts, V\u00f5la\u00f5igusseadus, Tsiviilseadustiku \u00fcldosa seadus, \u2026), so the
    entire Penal Code \u2014 the corpus's densest source of sanctions \u2014 was
    silently skipped as ``missing_act_node``. Accepting an
    ``estleg:Part`` root restores them. ``estleg:Act`` still wins when
    both typings are present, so single-file acts are unaffected.

    Callers must handle a None return \u2014 the per-file processing
    treats it as a malformed-input skip (logged to the coverage
    report's failure_samples + skip_reasons['missing_act_node']).
    """
    part_node: dict | None = None
    for n in doc.get("@graph", []):
        types = _node_types(n)
        if "estleg:Act" in types:
            return n
        if part_node is None and "estleg:Part" in types:
            part_node = n
    return part_node


def _label_texts(value: object) -> list[str]:
    """Flatten a JSON-LD label/title value into a list of plain strings.

    Accepts the three shapes the corpus uses interchangeably: a bare
    string, a langString object (``{"@value": \u2026, "@language": \u2026}``), and
    a list mixing both.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        inner = value.get("@value")
        return [str(inner)] if isinstance(inner, (str, int, float)) else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_label_texts(item))
        return out
    return []


# A code's *general part* is marked by a parenthesised part designation
# in the root node's label: "Karistusseadustik Osa 1 (\u00dcLDOSA) \u00a71\u201387
# kaardistus", "V\u00d5S Osa 1 (\u00dcldosa) \u00a71\u2013207 kaardistus", "Asja\u00f5igusseadus
# Osa 1 (\u00dcLDOSA) \u00a71\u201331 kaardistus".
#
# Issue #681 describes the rule as "label contains \u00fcldosa", but a bare
# substring test is too broad: several *standalone acts* carry the word
# in their own name \u2014 Keskkonnaseadustiku \u00fcldosa seadus, Majandustegevuse
# seadustiku \u00fcldosa seadus, Sotsiaalseadustiku \u00fcldosa seadus,
# Tsiviilseadustiku \u00fcldosa seadus (and its ``Osa 2 (ISIKUD)`` /
# ``Osa 4 (TEHINGUD)`` / ``Osa 7`` parts). Those are ordinary acts with
# their own penalty provisions \u2014 two of them ship sanction sidecars
# today \u2014 so a substring rule would delete legitimate sanctions.
# Requiring the parentheses selects exactly the general-part files of a
# split code and leaves the standalone \u00fcldosa acts alone.
_GENERAL_PART_LABEL_RE = re.compile(r"\(\s*\u00fcldosa\s*\)", re.IGNORECASE)


def _is_general_part(act_node: dict) -> bool:
    """Return True iff the act/part root node denotes a code's general part.

    The general part (\u00fcldosa) of a code defines the *sanction system* \u2014
    what imprisonment, a fine or confiscation ARE, and the statutory
    ranges that the special part's offence provisions draw on. It states
    no offence penalties of its own, so running the sanction extractors
    over it manufactures phantom sanctions: on KarS \u00dcldosa it produced 14
    records, six of them false ``life`` hits on \u00a7\u00a7 4, 22\u00b9, 45, 53, 77 and
    83\u00b2 \u2014 sections that merely *describe* life imprisonment (issue #681).
    """
    for key in ("rdfs:label", "dcterms:title", "dc:title"):
        for text in _label_texts(act_node.get(key)):
            if _GENERAL_PART_LABEL_RE.search(text):
                return True
    return False


def _is_inline_sanction_node(node: object) -> bool:
    """True for a peep-side ``estleg:Sanction`` node (issue #681).

    Canonical Sanction nodes live in the ``sanctions/`` sidecar; a peep must
    carry only the ``estleg:hasSanction`` edge. Older passes left minimal
    inline anchor nodes (``@id`` / ``@type`` / ``estleg:sanctionType``) in
    the peeps, and because nothing cleared them a regeneration that stopped
    emitting a sanction left its anchor behind as an orphan — 482 of them
    reached ``combined_ontology.jsonld`` as label-less, provision-less
    Sanction nodes. ``main`` now removes every inline Sanction node before
    it rewrites a peep (Step 2).
    """
    if not isinstance(node, dict):
        return False
    types = node.get("@type")
    if isinstance(types, str):
        types = [types]
    return isinstance(types, list) and "estleg:Sanction" in types


def _classify_enforcement_level(act_node: dict) -> str:
    """Classify the enforcement level of an act node by its @type.

    Returns "municipality" iff the act is typed
    estleg:MunicipalRegulation; otherwise "state".

    Handles @type as either a string or a list of strings.
    """
    if "estleg:MunicipalRegulation" in _node_types(act_node):
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


def _clean_euro_amount(raw: str) -> str | None:
    """Normalize a captured euro amount to a bare integer string, or None.

    Issue #602: the euro-amount captures previously did ``.replace(" ", "")``
    only, which does NOT strip ``\\n``/``\\t``. When a bounded sentence-window
    regex bridged a line break (e.g. ``rahatrahv 500\\n2024 eurot``), the raw
    capture (``"500\\n2024"``) was stored verbatim as ``"500\\n2024 EUR"`` — a
    fabricated, un-parseable amount. This helper strips ordinary thousands
    separators (ASCII space + non-breaking space) and then accepts the result
    ONLY if every remaining character is a digit; anything containing a
    newline/tab/stray glyph (i.e. a window that spanned two numbers) is
    rejected so the caller can skip it rather than emit junk.
    """
    cleaned = raw.replace(" ", "").replace(" ", "")
    return cleaned if cleaned.isdigit() else None


def _clean_decimal_euro_amount(raw: str) -> str | None:
    """Like ``_clean_euro_amount`` but tolerant of an Estonian comma-decimal.

    Issue #602: the ``sunniraha`` capture class admits a comma, so a
    comma-decimal amount such as ``"1 000,50"`` previously had its comma
    *stripped* (``re.sub(r"[\\s,]","")`` -> ``"100050"``), which passed
    ``.isdigit()`` and was stored as ``"100050 EUR"`` -- a 100x inflation.
    Estonian statutory coercive-payment ceilings are whole euros, so a comma
    must be read as a decimal separator, not a thousands separator. We parse
    via ``Decimal`` (comma -> dot) and accept the value ONLY if it is integral
    (no genuine cents component, e.g. ``"1000,00"`` -> ``"1000"``); a real
    fractional amount (``"1000,50"``) signals a malformed/bridged capture and
    is rejected.
    """
    cleaned = raw.replace(" ", "").replace("\xa0", "")
    if cleaned.isdigit():
        return cleaned
    if "," in cleaned:
        try:
            value = Decimal(cleaned.replace(",", "."))
        except InvalidOperation:
            return None
        if value == value.to_integral_value():
            return str(int(value))
    return None


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


# A sentencing formula. Estonian offence provisions state their penalty
# as "…, – karistatakse <sanction>". Its presence separates a provision
# that IMPOSES a penalty from one that merely describes the sanction
# system (issue #681).
_SENTENCING_FORMULA_RE = re.compile(r"\bkaristatakse\b", re.IGNORECASE)

# An imprisonment range or ceiling may be followed by an alternative life
# term before the noun: "kaheksa- kuni kahekümneaastase või eluaegse
# vangistusega" (KarS § 114). Without tolerating that interposed clause
# the patterns below never reach ``vangistus``, so § 114's 8–20 year
# range was lost and only the separate ``life`` record survived (#681).
_OR_LIFE = r"(?:või\s+eluaeg\w*\s+)?"

# KarS § 45 lg 1: a determinate term of imprisonment runs from 30 days to
# 20 years. Beyond that the only sentence available is life imprisonment,
# which is recorded as ``max_penalty "life"`` with no amount. Any larger
# year value is therefore a mis-parse (a citation year, a cross-referenced
# section number, a bridged line), not a sentence. Kept equal to the bound
# in ``estleg:SanctionImprisonmentMaxYearsShape`` so the extractor can
# never emit a record the SHACL gate rejects (issue #681).
_MAX_DETERMINATE_YEARS = 20

_YEARS_RE = re.compile(r"^(\d+)\s+years$")


def _years_value(penalty: str | None) -> int | None:
    """Return the year count of a ``"<N> years"`` penalty string, else None.

    ``"life"``, ``"30 days"``, ``"18 months"`` and ``None`` all return
    None — only a determinate term in years is subject to the KarS § 45
    ceiling.
    """
    if not penalty:
        return None
    match = _YEARS_RE.match(penalty)
    return int(match.group(1)) if match else None


def _spans_overlap(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    """Return True iff ``span`` intersects any span in ``spans``."""
    start, end = span
    return any(
        start < other_end and other_start < end
        for other_start, other_end in spans
    )


def extract_imprisonment(text: str) -> list[dict]:
    """Detect imprisonment sentences.

    Handles both digit-based ("kuni 5 aastase vangistusega") and
    Estonian word-based ("kuni viieaastase vangistusega") patterns.

    Word-based patterns are anchored to the closed set of supported
    Estonian numerals (``_ESTONIAN_NUMBERS``). The previous ``\\w+``
    over-matched arbitrary tokens before ``aasta``, which produced
    spurious empty matches and hid genuine extraction failures.

    Deduplication (issue #681)
    --------------------------
    Records are deduplicated on the **exact ``(min, max)`` pair**. The
    previous ``_already()`` helper compared a candidate against both the
    ``max_penalty`` *and* the ``min_penalty`` of every record already
    collected, so a max-only ceiling was swallowed whenever some range
    happened to share that value as its *lower* bound. KarS § 400 lg 1
    ("kuni üheaastase vangistusega" — max 1 year) therefore vanished
    because lg 2's 1–3 year range starts at 1 year, two genuinely
    different penalties for two different subsections.

    A max-only match that merely re-reads the upper bound of a range
    already captured (§ 114's "kuni kahekümneaastase …" sitting inside
    "kaheksa- kuni kahekümneaastase …") is suppressed by **span
    overlap** instead, which is precise where value comparison was not.
    """
    results: list[dict] = []
    seen: set[tuple[str, str]] = set()
    # Character spans consumed by a range match; a ceiling match landing
    # inside one is that range's own upper bound, not a separate penalty.
    range_spans: list[tuple[int, int]] = []

    def _add(max_penalty: str, min_penalty: str | None = None) -> None:
        key = (min_penalty or "", max_penalty)
        if key in seen:
            return
        # Issue #681: enforce the KarS § 45 lg 1 ceiling at the single
        # point every imprisonment record passes through. Capping only
        # the ``karistatakse`` fallback left the ceiling unenforced on
        # every other branch — "karistatakse kuni 25 aastase
        # vangistusega" was emitted as 25 years by the uncapped digit
        # max-only pattern, and "25 kuni 30 aastase" as a 25–30 range —
        # which ``estleg:SanctionImprisonmentMaxYearsShape`` then
        # rejects, turning a parse artefact into a release-gate failure.
        for penalty in (max_penalty, min_penalty):
            years = _years_value(penalty)
            if years is not None and years > _MAX_DETERMINATE_YEARS:
                print(
                    f"  WARN: discarding implausible imprisonment term "
                    f"{penalty!r}; KarS § 45 lg 1 caps a determinate "
                    f"sentence at {_MAX_DETERMINATE_YEARS} years "
                    f"(beyond that only life imprisonment exists)"
                )
                return
        seen.add(key)
        rec: dict = {"sanction_type": "imprisonment"}
        if min_penalty is not None:
            rec["min_penalty"] = min_penalty
        rec["max_penalty"] = max_penalty
        results.append(rec)

    # Life imprisonment: "eluaegne/eluaegse vangistus".
    #
    # Issue #681: gated on a sentencing formula. KarS Üldosa §§ 4, 22¹,
    # 45, 53, 77 and 83² each mention life imprisonment while *defining*
    # the sanction system, and each produced a phantom ``life`` sanction.
    # This is defence in depth behind the general-part file skip in
    # ``main()``; it also protects any other act that refers to life
    # imprisonment without imposing it.
    if (
        _SENTENCING_FORMULA_RE.search(text)
        and re.search(r"eluaeg\w*\s+vangistus", text, re.IGNORECASE)
    ):
        _add("life")

    # --- Word-based patterns (most common in KarS) ---
    _NUMBER_ALT = "|".join(
        sorted((re.escape(k) for k in _ESTONIAN_NUMBERS), key=len, reverse=True)
    )

    # Range: "kuue- kuni viieteistaastase vangistusega"
    # Issue #320 (split numeral + space: "kümne aastase") and #372
    # (hyphenated numeral: "kahe- kuni kaheksa-aastase"): allow an
    # optional hyphen and optional whitespace between the upper-bound
    # numeral and ``aasta`` (``-?\s*``), so compound, split, and
    # hyphenated surface forms all match.
    range_pat = (
        rf"({_NUMBER_ALT})-?\s+kuni\s+({_NUMBER_ALT})-?\s*aasta(?:se)?"
        rf"\s+{_OR_LIFE}vangistus"
    )
    for m in re.finditer(range_pat, text, re.IGNORECASE):
        min_val = _parse_number(m.group(1))
        max_val = _parse_number(m.group(2))
        if min_val is not None and max_val is not None:
            # Issue #273: enforce min <= max (source range may be high-to-low).
            min_pen, max_pen = _ordered_year_range(min_val, max_val)
            range_spans.append(m.span())
            _add(max_pen, min_pen)

    # --- Digit-based patterns (less common but possible) ---

    # Range: "N kuni M aastase vangistusega"
    for m in re.finditer(
        rf"(\d+)\s*[\-–—]?\s*kuni\s+(\d+)\s*[\-\s]*aasta(?:se)?"
        rf"\s+{_OR_LIFE}vangistus",
        text, re.IGNORECASE,
    ):
        # Issue #273: enforce min <= max (source range may be high-to-low).
        min_s, max_s = _ordered_year_range(int(m.group(1)), int(m.group(2)))
        range_spans.append(m.span())
        _add(max_s, min_s)

    # Max only (word): "kuni viieaastase vangistusega"
    # Issue #320/#372: allow an optional hyphen and optional whitespace
    # between the numeral and ``aasta`` (e.g. "kümne aastase",
    # "kaheksateist aastase", "kaheksa-aastase").
    max_pat = rf"kuni\s+({_NUMBER_ALT})-?\s*aasta(?:se)?\s+{_OR_LIFE}vangistus"
    for m in re.finditer(max_pat, text, re.IGNORECASE):
        val = _parse_number(m.group(1))
        if val is not None and not _spans_overlap(m.span(), range_spans):
            _add(f"{val} years")

    # Max only (digit): "kuni N aastase vangistusega"
    for m in re.finditer(
        rf"kuni\s+(\d+)\s*[\-\s]*aasta(?:se)?\s+{_OR_LIFE}vangistus",
        text, re.IGNORECASE,
    ):
        if not _spans_overlap(m.span(), range_spans):
            _add(f"{m.group(1)} years")

    # "karistatakse ... kuni N aastase vangistusega" (digit fallback)
    #
    # Issue #301: the previous pattern ``karistatakse.*?(\d+).*?aasta``
    # used greedy ``.*?`` that crossed sentence boundaries and grabbed
    # the first digit before any ``aasta`` — typically an RT citation
    # year ("RT I 2002 56 350" → "2002 years") or a cross-ref paragraph
    # number ("§-s 118" → "118 years"). Two guards now scope it:
    #   (a) require ``kuni`` adjacent to the number and stay within the
    #       sentence (``[^.]*?`` instead of ``.*?``); and
    #   (b) a plausibility cap — KarS § 45 lg 1 sets the maximum
    #       determinate prison term at 20 years (beyond that only life
    #       imprisonment exists), so any value > 20 is discarded as a
    #       mis-captured citation/cross-reference.
    #
    # Issue #681 lowered this cap from 25 to 20 to match the statute and
    # ``estleg:SanctionImprisonmentMaxYearsShape``. While the cap sat at
    # 25 the extractor could emit a 21–25 year term that the SHACL bound
    # then rejected, turning a parser artefact into a release-gate
    # failure. Discarding it here keeps the two in agreement; no corpus
    # provision falls in that window.
    for m in re.finditer(
        rf"karistatakse[^.]*?\bkuni\s+(\d+)\s*[-\s]*aasta(?:se)?"
        rf"\s+{_OR_LIFE}vangistus",
        text, re.IGNORECASE,
    ):
        val = m.group(1)
        if int(val) > 20:
            continue
        if not _spans_overlap(m.span(), range_spans):
            _add(f"{val} years")

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
        if _spans_overlap(m.span(), range_spans):
            continue
        _add(f"{val} {normalised}")

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
    turnover = extract_turnover_percentage(text)
    if turnover:
        # Issue #681: the lõige states its own ceiling as a share of the
        # legal person's turnover, so the KarS § 44 natural-person
        # fallback is not merely redundant here — it is wrong. KarS § 400
        # lg 3/lg 4 were stamped "500 daily rates" instead of the 5 % /
        # 5–10 % turnover fines the text actually imposes. Extraction runs
        # per lõige (see ``extract_sanctions``), so a sibling lõige that
        # says a bare "rahalise karistusega" still gets the default.
        return turnover
    results: list[dict] = []
    if re.search(r"rahalise?\s+karistus", text, re.IGNORECASE):
        results.append({
            "sanction_type": "pecuniary_punishment",
            "max_penalty": "500 daily rates",
            "is_statutory_default": True,
        })
    return results


# "rahalise karistusega kuni 5 protsenti juriidilise isiku käibest" and
# "rahalise karistusega 5 kuni 10 protsenti juriidilise isiku käibest"
# (KarS § 400 lg 3 / lg 4). The bounded ``[^.]{0,40}`` gap keeps the
# match inside one sentence while tolerating the "juriidilise isiku"
# qualifier between the amount and ``käibest``.
_TURNOVER_RANGE_RE = re.compile(
    r"rahalise?\s+karistus\w*\s+(\d+(?:[.,]\d+)?)\s+kuni\s+"
    r"(\d+(?:[.,]\d+)?)\s*(?:%|protsenti)[^.]{0,40}?käibest",
    re.IGNORECASE,
)
_TURNOVER_MAX_RE = re.compile(
    r"rahalise?\s+karistus\w*\s+kuni\s+(\d+(?:[.,]\d+)?)\s*"
    r"(?:%|protsenti)[^.]{0,40}?käibest",
    re.IGNORECASE,
)


def _turnover_amount(raw: str) -> str:
    """Render a captured turnover percentage as a penalty string.

    Estonian writes a decimal with a comma; the structured parser wants a
    dot. ``"5"`` → ``"5 % of turnover"``, ``"2,5"`` → ``"2.5 % of turnover"``.
    """
    return f"{raw.replace(',', '.')} % of turnover"


def extract_turnover_percentage(text: str) -> list[dict]:
    """Detect a pecuniary punishment expressed as a share of turnover.

    Issue #681: KarS § 400 lg 3/lg 4 fine a legal person "kuni 5
    protsenti" / "5 kuni 10 protsenti juriidilise isiku käibest". No
    pattern recognised that shape, so ``extract_pecuniary`` fell through
    to the KarS § 44 natural-person default of 500 daily rates — a fine
    the provision does not impose, on a person it does not apply to.

    Emits ``pecuniary_punishment`` records carrying the new
    ``percent_of_turnover`` structured unit. A turnover share is a
    *relative* ceiling, so it has no currency.
    """
    results: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(max_raw: str, min_raw: str | None = None) -> None:
        max_pen = _turnover_amount(max_raw)
        min_pen = _turnover_amount(min_raw) if min_raw is not None else None
        key = (min_pen or "", max_pen)
        if key in seen:
            return
        seen.add(key)
        rec: dict = {"sanction_type": "pecuniary_punishment"}
        if min_pen is not None:
            rec["min_penalty"] = min_pen
        rec["max_penalty"] = max_pen
        results.append(rec)

    # Range first (most specific): "5 kuni 10 protsenti ... käibest".
    range_spans: list[tuple[int, int]] = []
    for m in _TURNOVER_RANGE_RE.finditer(text):
        range_spans.append(m.span())
        _add(m.group(2), m.group(1))

    # Ceiling: "kuni 5 protsenti ... käibest".
    for m in _TURNOVER_MAX_RE.finditer(text):
        if _spans_overlap(m.span(), range_spans):
            continue
        _add(m.group(1))

    return results


def extract_dissolution(text: str) -> list[dict]:
    """Detect compulsory dissolution of a legal person (sundlõpetamine).

    Issue #681: KarS § 46 lets a court dissolve a legal person, and 28
    Eriosa provisions impose it ("karistatakse … sundlõpetamisega").
    No extractor recognised it, so the harshest sanction available
    against a legal person was absent from the graph entirely.

    Dissolution has no magnitude — an entity is dissolved or it is not —
    so the record carries no ``max_penalty`` and no structured amount.
    """
    if re.search(r"sundlõpeta\w*", text, re.IGNORECASE):
        return [{"sanction_type": "compulsory_dissolution"}]
    return []


def extract_confiscation(text: str) -> list[dict]:
    """Detect confiscation (konfiskeerimine).

    Issue #681: KarS §§ 83–85 govern confiscation of the instrument, the
    object and the proceeds of an offence; 55 Eriosa provisions invoke
    it. Like dissolution it states no ceiling in the offence provision —
    the amount is whatever the offence yielded — so no ``max_penalty``
    is emitted.
    """
    if re.search(r"konfiskeeri\w*", text, re.IGNORECASE):
        return [{"sanction_type": "confiscation"}]
    return []


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
    """Detect monetary fines in euros.

    Issue #372: broadened beyond the original ``rahatrahv(iga)`` +
    immediate ``(kuni) N eurot`` form to also capture:

    * genitive / intervening-word phrasings — ``rahatrahvi kuni N eurot``,
      ``Rahatrahv määratakse suuruses kuni N eurot`` — via a broadened
      inflection (``rahatrahv\\w{0,3}``) and a bounded same-sentence gap
      before ``kuni``;
    * dash ranges — ``rahatrahv 64–16 000 eurot`` → ``min 64 / max 16000``.

    Branch order is most-specific-first (dash range, then ``kuni`` max,
    then bare ``N eurot``) and an internal dedup keeps a value from being
    emitted twice across branches.
    """
    results: list[dict] = []

    def _add(max_amount: str, min_amount: str | None = None) -> None:
        min_pen = f"{min_amount} EUR" if min_amount else ""
        for r in results:
            if (
                r.get("max_penalty") == f"{max_amount} EUR"
                and r.get("min_penalty", "") == min_pen
            ):
                return
        rec: dict = {
            "sanction_type": "fine",
            "max_penalty": f"{max_amount} EUR",
        }
        if min_amount is not None:
            rec["min_penalty"] = f"{min_amount} EUR"
        results.append(rec)

    # Dash range FIRST (most specific): "rahatrahv 64–16 000 eurot".
    # Non-greedy lower bound so it doesn't swallow the dash. #602: amount
    # classes are digit + space + non-breaking space only (not ``\s``) so a
    # bound can't span a line break; the int() wrap remains the final guard.
    for m in re.finditer(
        r"rahatrahv\w{0,3}\s+(\d[\d  ]*?)\s*[–—\-]\s*(\d[\d  ]*)\s*eurot",
        text, re.IGNORECASE,
    ):
        lo = m.group(1).replace(" ", "").replace(" ", "")
        hi = m.group(2).replace(" ", "").replace(" ", "")
        try:
            lo_i, hi_i = sorted((int(lo), int(hi)))
        except ValueError:
            continue
        _add(str(hi_i), str(lo_i))

    # "rahatrahv(i/iga/...) [intervening words] kuni N eurot" — genitive
    # and "suuruses kuni" phrasings, bounded to the same sentence.
    # #602: the amount class is digit + space + non-breaking space only (not
    # ``\s``) so a line break can't be captured into the amount, and the
    # capture is re-validated via ``_clean_euro_amount``.
    for m in re.finditer(
        r"rahatrahv\w{0,3}[^.]{0,40}?\bkuni\s+(\d[\d  ]*)\s*eurot",
        text, re.IGNORECASE,
    ):
        amount = _clean_euro_amount(m.group(1))
        if amount is not None:
            _add(amount)

    # Bare "rahatrahv(iga) N eurot" (no kuni, no dash).
    for m in re.finditer(
        r"rahatrahv\w{0,3}\s+(\d[\d  ]*)\s*eurot", text, re.IGNORECASE
    ):
        amount = _clean_euro_amount(m.group(1))
        if amount is not None:
            _add(amount)

    return results


def extract_mojutustrahv(text: str) -> list[dict]:
    """Detect ``mõjutustrahv`` (simplified-procedure influence fine).

    Issue #372: ``mõjutustrahv`` is a distinct sanction surface form
    (ühistransport / tobacco / traffic simplified procedures) not matched
    by ``extract_fine_euros`` (which anchors on ``rahatrahv``). Emits a
    ``fine`` record with the euro amount when present, else a bare
    ``fine`` mention.
    """
    results: list[dict] = []
    for m in re.finditer(
        r"mõjutustrahv\w*[^.]{0,40}?(\d[\d  ]*)\s*eurot", text, re.IGNORECASE
    ):
        # #602: validate the capture (digit/space/nbsp only + isdigit guard)
        # so a window that bridged a line break is skipped, not stored.
        amount = _clean_euro_amount(m.group(1))
        if amount is None:
            continue
        if not any(r.get("max_penalty") == f"{amount} EUR" for r in results):
            results.append({
                "sanction_type": "fine",
                "max_penalty": f"{amount} EUR",
            })
    if not results and re.search(r"\bmõjutustrahv\w*", text, re.IGNORECASE):
        results.append({"sanction_type": "fine"})
    return results


def extract_coercive(text: str) -> list[dict]:
    """Detect coercive payment (sunniraha).

    Issue #358: the standard ``asendustäitmise`` formula inserts
    boilerplate between ``sunniraha`` and the amount —
    ``sunniraha asendustäitmise ja sunniraha seaduses sätestatud korras
    kuni N eurot`` — which the original tight ``sunniraha\\s+kuni`` regex
    could not bridge, so the quantified amount (incl. Estonia's
    20,000,000 EUR GDPR ceiling) was dropped and only a bare
    ``coercive_payment`` mention survived. The pattern now tolerates up to
    150 same-sentence characters between ``sunniraha`` and ``kuni N
    eurot``.
    """
    results: list[dict] = []
    for m in re.finditer(
        r"sunniraha[^.]{0,150}kuni\s+([\d\s,]+)\s*eurot", text, re.IGNORECASE
    ):
        amount = _clean_decimal_euro_amount(m.group(1))
        if amount is None:
            continue
        if not any(r.get("max_penalty") == f"{amount} EUR" for r in results):
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
    # gated on (a) punishment-context suffix, (b) sentencing verb, and
    # (c) the arrest token NOT sitting in a past-passive condition clause.
    #
    # Issue #393: in KarS §331¹ ``aresti`` is the object of a past-passive
    # condition ("…on selle eest täitemenetluses kohaldatud aresti, –
    # karistatakse … vangistusega"), i.e. a precondition for the *real*
    # imprisonment penalty, not an imposed arrest. The bare suffix+verb
    # gate fired anyway and emitted a spurious 30-day arrest alongside the
    # imprisonment node. We now require at least one punishment-suffix
    # ``arest`` occurrence that is NOT immediately (within ~10 tokens)
    # preceded by a past-passive verb (kohaldatud|rakendatud|määratud).
    if not results:
        sentencing_verb = re.search(
            r"\b(?:karistatakse|kohaldatakse)\b", text, re.IGNORECASE
        )
        if sentencing_verb and _has_imposed_arrest(text):
            results.append({
                "sanction_type": "arrest",
                "max_penalty": "30 days",
                "is_statutory_default": True,
            })

    return results


# Past-passive verbs that mark a condition clause (issue #393): when an
# ``arest`` token immediately follows one of these, the arrest is a
# precondition, not the imposed sanction.
_ARREST_CONDITION_VERB = r"(?:kohaldatud|rakendatud|määratud)"
_ARREST_PUNISHMENT_SUFFIX = r"\barest(?:i|iga|iks|ile)\b"


def _has_imposed_arrest(text: str) -> bool:
    """Return True iff some punishment-suffix ``arest`` token is NOT part
    of a past-passive condition clause.

    A condition clause = a past-passive verb (kohaldatud|rakendatud|
    määratud) standing within ~10 tokens immediately before the ``arest``
    token. If *every* punishment-suffix occurrence is preceded that way,
    the statutory-default fallback must not fire (issue #393).
    """
    for m in re.finditer(_ARREST_PUNISHMENT_SUFFIX, text, re.IGNORECASE):
        preceding = text[: m.start()]
        condition = re.search(
            rf"\b{_ARREST_CONDITION_VERB}\b(?:\s+\S+){{0,10}}?\s*$",
            preceding,
            re.IGNORECASE,
        )
        if condition is None:
            return True
    return False


ALL_EXTRACTORS = [
    extract_imprisonment,
    extract_pecuniary,
    extract_fine_units,
    extract_fine_euros,
    extract_mojutustrahv,
    extract_coercive,
    extract_arrest,
    extract_dissolution,
    extract_confiscation,
]


# A lõige (subsection) marker: "(1)", "(2)", … at the head of a
# subsection. Bounded to two digits so a bare parenthesised year
# ("(2003)") or a long numeric citation cannot masquerade as one.
_LOIGE_MARKER_RE = re.compile(r"(\(\d{1,2}\))")


def split_loiked(text: str) -> list[str]:
    """Split provision text into its lõige (subsection) chunks.

    Returns ``[text]`` unchanged when the provision has no lõige
    markers. Otherwise each chunk starts at its ``(n)`` marker; any
    prose standing before the first marker is returned as its own
    leading chunk so nothing is dropped.
    """
    parts = _LOIGE_MARKER_RE.split(text)
    if len(parts) == 1:
        return [text]
    chunks: list[str] = []
    preamble = parts[0].strip()
    if preamble:
        chunks.append(preamble)
    for i in range(1, len(parts), 2):
        body = parts[i + 1] if i + 1 < len(parts) else ""
        chunks.append(parts[i] + body)
    return chunks or [text]


def extract_sanctions(text: str) -> list[dict]:
    """Run all extractors and return deduplicated sanction records.

    The ``is_statutory_default`` flag (when present) is preserved
    through dedup so callers can distinguish a fallback emission
    from a text-derived one. If both flagged and unflagged variants
    of the same logical key appear, the unflagged variant wins (a
    text-derived value is more specific than a statutory default).

    Per-lõige extraction (issue #681)
    ---------------------------------
    A section that punishes several subsections differently was read as
    one blob, so the subsections' penalties collided and all but one
    were lost — KarS § 141 reported only lg 1's 1–5 years, dropping
    lg 2's 6–15 years and lg 3's pecuniary punishment. The extractors
    are therefore run once over the whole text and again over each
    lõige chunk, merging into the same dedup.

    Running the whole text as well as the chunks is deliberate: it keeps
    every record the single-pass reading produced (some extractors need
    context that a chunk boundary would cut, e.g. ``extract_arrest``
    wants a sentencing verb co-occurring with the arrest token), while
    the per-chunk pass adds the subsection detail. Because both passes
    feed one dedup keyed on type + penalties, the result is a superset
    with no duplicates.
    """
    all_sanctions: list[dict] = []
    seen: dict[str, dict] = {}

    def _merge(s: dict) -> None:
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

    chunks = split_loiked(text)
    passes = [text] if len(chunks) == 1 else [text, *chunks]
    for chunk in passes:
        for extractor in ALL_EXTRACTORS:
            for s in extractor(chunk):
                _merge(s)
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
        m = re.search(r"(\d[\d  ]*)\s*(?:eurot|trahviühiku)", text, re.IGNORECASE)
        if m:
            # #602: validate via _clean_euro_amount so a newline-spanning
            # capture is rejected rather than returned as a junk amount.
            val = _clean_euro_amount(m.group(1))
            if val is not None:
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
        m = re.search(r"sunniraha\w*\s+(?:kuni\s+)?(\d[\d  ]*)\s*eurot", text, re.IGNORECASE)
        if m:
            # #602: validate via _clean_euro_amount (reject line-spanning).
            val = _clean_euro_amount(m.group(1))
            if val is not None:
                return val + " EUR"
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
#   "percent_of_turnover"   — a share of a legal person's turnover
PENALTY_UNIT_YEARS = "years"
PENALTY_UNIT_MONTHS = "months"
PENALTY_UNIT_DAYS = "days"
PENALTY_UNIT_DAILY_RATES = "daily_rates"
PENALTY_UNIT_FINE_UNITS = "fine_units"
PENALTY_UNIT_MONETARY = "monetary"
# Issue #681: KarS § 400 lg 3/lg 4 cap a legal person's fine at a share
# of its turnover. The ceiling is relative, so unlike "monetary" it
# carries NO currency — the amount is a percentage, not a sum.
PENALTY_UNIT_PERCENT_OF_TURNOVER = "percent_of_turnover"

PENALTY_UNITS: frozenset[str] = frozenset({
    PENALTY_UNIT_YEARS,
    PENALTY_UNIT_MONTHS,
    PENALTY_UNIT_DAYS,
    PENALTY_UNIT_DAILY_RATES,
    PENALTY_UNIT_FINE_UNITS,
    PENALTY_UNIT_MONETARY,
    PENALTY_UNIT_PERCENT_OF_TURNOVER,
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
    # Issue #681: a turnover-share ceiling. No currency — the amount is
    # a percentage, so a currency code would be meaningless.
    "% of turnover": (PENALTY_UNIT_PERCENT_OF_TURNOVER, None),
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


# Reference ceiling for the normalized monetary score (issue #393):
# Estonia's GDPR enforcement maximum (ISIKUA §60) is 20,000,000 EUR, the
# largest euro sanction in the corpus. Used only to scale the 0–1
# ``monetary_score`` in the severity index; it does not affect amounts.
_MONETARY_SCORE_CEILING_EUR = Decimal(20000000)


def _monetary_amount_eur(sanction_node: dict) -> Decimal | None:
    """Return the EUR amount of a Sanction node, or None.

    Reads the structured ``estleg:maxPenaltyAmount`` only when the node's
    ``estleg:maxPenaltyUnit`` is ``"monetary"`` (issue #393). Returns None
    for non-monetary sanctions (imprisonment/arrest/fine-units/daily-rates)
    and for monetary sanctions whose amount can't be parsed.
    """
    if sanction_node.get("estleg:maxPenaltyUnit") != PENALTY_UNIT_MONETARY:
        return None
    raw = sanction_node.get("estleg:maxPenaltyAmount")
    if isinstance(raw, dict):
        raw = raw.get("@value")
    if raw is None:
        return None
    try:
        amount = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 else None


def _monetary_score(max_eur: Decimal | None) -> float:
    """Normalize an EUR amount to a 0–1 log-scaled score (issue #393).

    Log scaling keeps small and large fines distinguishable across the
    corpus's wide range (64 EUR … 20,000,000 EUR). Returns 0.0 when there
    is no euro amount. Clamped to [0.0, 1.0].
    """
    if max_eur is None or max_eur <= 0:
        return 0.0
    import math

    score = math.log10(float(max_eur) + 1.0) / math.log10(
        float(_MONETARY_SCORE_CEILING_EUR) + 1.0
    )
    return round(max(0.0, min(1.0, score)), 4)


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
            # estleg:Act typing on their root node —
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
                    f"but missing estleg:Act root node"
                )
            # Either way, the file has no act to classify — skip extraction.
            continue
        enforcement_level = _classify_enforcement_level(act_node)

        # Issue #681: a code's general part (üldosa) defines the sanction
        # SYSTEM, not offence penalties. Extraction is suppressed for it,
        # but the file still runs through the clear/save lifecycle below
        # so a regeneration RETRACTS whatever a previous run wrote — the
        # 14 phantom KarS Üldosa records (six false ``life`` hits) are
        # deleted from the peep and their sidecar removed, rather than
        # merely stopping being regenerated.
        general_part = _is_general_part(act_node)
        if general_part:
            _files_skipped += 1
            _skip_reasons["general_part"] = (
                _skip_reasons.get("general_part", 0) + 1
            )

        is_kov = "regulations/kov/" in str(filepath)
        _files_processed.add(filepath)
        if is_kov:
            _files_processed_kov.add(filepath)

        # Step 1: detect prior peep-side output BEFORE any mutation. Inline
        # Sanction anchor nodes count as prior output too, so a peep that
        # carries only stale anchors is still rewritten (and cleaned).
        had_existing_peep = any(
            ("estleg:hasSanction" in n)
            or ("estleg:enforcedAtLevel" in n)
            or _is_inline_sanction_node(n)
            for n in doc["@graph"]
        )

        # Step 2: clear peep-side state unconditionally (idempotency): the
        # edges on every node, and every inline estleg:Sanction node — the
        # canonical Sanction nodes are (re)written to the sidecar below.
        for n in doc["@graph"]:
            n.pop("estleg:hasSanction", None)
            n.pop("estleg:enforcedAtLevel", None)
        doc["@graph"] = [
            n for n in doc["@graph"] if not _is_inline_sanction_node(n)
        ]

        law_name = filepath.stem.replace("_peep", "")
        law_sanctions: list[dict] = []

        # Detect pre-existing sanctions JSON file (path computed
        # the same way the writer uses).
        sanctions_filename = f"sanctions_{sanitize_id(law_name)}.json"
        sanctions_path = SANCTION_DIR / sanctions_filename
        had_existing_sanctions_file = sanctions_path.exists()

        iri_counts: dict[str, int] = defaultdict(int)

        # Step 3: run extraction over the cleared graph. A general-part
        # file contributes no provisions, so the loop body never runs and
        # the file falls through to Step 4 with an empty result set.
        for node in ([] if general_part else doc["@graph"]):
            summary = classifier_text(node)
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
        max_severity_type = "unknown"
        max_monetary_eur: Decimal | None = None
        for s in sanctions:
            stype = s.get("estleg:sanctionType", "")
            sev = SEVERITY.get(stype, 0)
            if sev > max_severity:
                max_severity = sev
                max_severity_type = stype
            # Issue #393: expose the largest monetary penalty (EUR) so
            # consumers can rank WITHIN the fine tier — a 20,000,000 EUR
            # fine and a 64 EUR fine both score type-severity 2, leaving
            # them indistinguishable from the type ordinal alone. The
            # legal type-first ordering (custodial > fine) is unchanged;
            # this is an additive amount field only.
            amt = _monetary_amount_eur(s)
            if amt is not None and (max_monetary_eur is None or amt > max_monetary_eur):
                max_monetary_eur = amt
        entry: dict = {
            "law": law_name,
            "sanction_count": len(sanctions),
            "max_severity": max_severity,
            # Issue #681: report the type that actually SET the maximum.
            # The previous reverse lookup returned the first key in
            # SEVERITY holding that score, which was unambiguous only
            # while every score was unique. ``confiscation`` now ties
            # with ``fine`` at 2 and ``compulsory_dissolution`` with
            # ``arrest`` at 4, so a law sanctioned only by confiscation
            # would have been mislabelled "fine".
            "max_severity_type": max_severity_type,
            # Largest EUR amount on any sanction for this law (None when
            # the law has no euro-denominated sanction). A float for JSON
            # readability; the authoritative xsd:decimal lives on the node.
            "max_monetary_eur": (
                float(max_monetary_eur) if max_monetary_eur is not None else None
            ),
            # Normalized 0–1 score within the fine tier, log-scaled against
            # a 20,000,000 EUR reference ceiling, so consumers can sort
            # large-fine laws without re-deriving amounts. 0.0 when no
            # euro amount is present.
            "monetary_score": _monetary_score(max_monetary_eur),
        }
        severity_index.append(entry)

    # Type ordering stays primary (legal convention); ties break first on
    # monetary magnitude (issue #393), then sanction count.
    severity_index.sort(
        key=lambda x: (
            -x["max_severity"],
            -(x["max_monetary_eur"] or 0.0),
            -x["sanction_count"],
        )
    )

    # ---------- report ----------
    print("\n[3/4] Generating report...")

    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
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

    report_path = KRR_DIR / "reports" / "sanctions_report.json"
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
            run_timestamp=PINNED_RUN_TIMESTAMP,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
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
