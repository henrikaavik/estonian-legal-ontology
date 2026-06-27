#!/usr/bin/env python3
"""Offline, conservative strip of Õiguskantsler letter-boilerplate from the
stale annotation layer (issue #619).

``krr_outputs/annotations/oiguskantsler_seisukohad.jsonld`` is a pre-#324 build:
~85% of ``estleg:annotationText`` values carry the standard Õiguskantsler
office-letter header, which the stale PDF extraction *collapsed onto a single
line* (so the #324 line-anchored ``_strip_letter_header`` never matched it). A
full re-scrape is ~20 h and infeasible here, so this script does a CONSERVATIVE
*offline* strip of that header from the EXISTING text.

Each text is ``"<title>\\n\\n<header><body>"``. The collapsed header is, in order:

  1. an optional recipient/letterhead block (addressee names, org, e-mails);
  2. a reference block — ``[Teie <date> nr ...] Meie <date> nr <ref-id>`` —
     whose ``<ref-id>`` (e.g. ``6-1/261230/2604420``) is the unmistakable anchor;
  3. an optional REPEATED copy of the document title;
  4. an optional salutation — ``Lugupeetud|Lugupeetav|Austatud|Auväärt`` +
     recipient (``avaldaja`` / ``volikogu esimees`` / ``Jaanus Õun`` / ``[ ]`` …).

Strategy (per annotation, all anchored at the body head):

  * STEP 1 — strip a bounded recipient lead-in + the reference block, cutting
    through the distinctive ``nr <ref-id>`` token.
  * STEP 2+3 — locate the salutation keyword in the head zone, wipe the header
    remainder (recipient + repeated title) that sits between the ref-id and the
    salutation, then drop the salutation keyword plus its *recipient run*. The
    recipient run only consumes redactions, honorifics, connectors and lowercase
    role-words (``avaldaja``, ``…minister``, ``volikogu``, …); it STOPS at the
    first Capitalised or unknown token, so a Capitalised name is left dangling
    rather than risk eating the real body (whose sentences are Capitalised, and
    whose lowercase openers such as ``pöördusite``/``tänan`` are *not* role-words).
  * Fallback — when a letter has a reference block but no salutation, strip a
    leading VERBATIM repeated title (whitespace-flexible) if present.

It is CONSERVATIVE (only strips on a clear ref-block and/or salutation match,
never empties a body, leaves un-matching nodes untouched) and IDEMPOTENT (a
cleaned body no longer opens with a ref-id or a salutation, so a second pass is
a no-op). ``annotationType`` is NOT touched (hardcoded by design, per #619).

Run (offline, idempotent; dry-run by default):

    .venv/bin/python scripts/clean_annotation_boilerplate_619.py            # report
    .venv/bin/python scripts/clean_annotation_boilerplate_619.py --apply    # write
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from estleg_common import KRR_DIR, save_json

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

ANNOTATION_FILE = KRR_DIR / "annotations" / "oiguskantsler_seisukohad.jsonld"

TEXT_KEY = "estleg:annotationText"
LABEL_KEY = "rdfs:label"
ANNOTATION_TYPE = "estleg:Annotation"

# Label prefix to strip when deriving a title from rdfs:label (longest first).
_LABEL_TITLE_PREFIXES = ("Õiguskantsleri seisukoht:", "Õiguskantsleri seisukoht")

# ---------------------------------------------------------------------------
# Boilerplate regexes (the body is a single physical line — 0 newlines).
# ---------------------------------------------------------------------------

# The Õiguskantsler reference id: ``<chapter>-<sub>/<serial>/<doc-id>``, e.g.
# ``6-1/261230/2604420``, ``7-7/252405/2508574``, ``6-10/221608/2206035``. The
# mandatory ``/<5-9 digit serial>`` is what makes it distinctive: it deliberately
# does NOT match Riigikohus court-case numbers (``5-25-49``) or law/regulation
# numbers (``nr 3``) that legitimately occur in opinion bodies — matching those
# would both eat real content and break idempotency. Single optional spaces and a
# doubled separator (``10-2//090780/…``) are tolerated (stale-extraction artefacts).
_REF_ID = r"\d{1,3}-\s?\d{1,3}\s?/+\s?\d{5,9}(?:\s?[/\-]+\s?\d{4,9}){0,2}"

# STEP 1: a bounded recipient/letterhead lead-in, then the reference block cut
# through the ``nr <ref-id>`` token. The block is matched STRUCTURALLY — an optional
# ``Teie`` clause then a ``Teie``/``Meie`` clause, each ``<keyword> [date] nr`` — so
# the keyword must introduce a real reference. That rejects the pronoun ``Teie``
# (``… pöördus Teie poole 21.06.2012 kirjaga nr 7-4/120880/1203032 …``), which would
# otherwise be cut and break idempotency. The keyword tolerates the stale
# extraction's missing spaces (``Teienr``, ``Meie17.11.2022``). The captured lead-in
# is additionally vetted by ``_is_letterhead_leadin`` so a properly-formed reference
# QUOTED inside prose (``Pöördusin Teie … Meie … nr <id> …``) is never cut. The
# lead-in is capped at 400 chars (recipient blocks reach ~240 chars before the ref).
# Reference keywords: ``Teie`` (incoming), ``Meie`` (outgoing, current) and
# ``Õiguskantsler`` (outgoing, 2009–2013 format). The ``Õiguskantsler`` lookahead
# rejects the genitive ``Õiguskantsleri …`` that opens many bodies; ``Teie``/``Meie``
# need no lookahead (they tolerate the glued ``Teienr`` / ``Meie17.11.2022`` forms).
_KW = r"\b(?:Teie|Meie|Õiguskantsler(?![^\W\d]))"
# Filler permitted between the keyword and the final ``nr <ref-id>``: only
# reference-ish tokens — dates (incl. day-less ``.05.2009``), the word ``kuupäev``,
# more keywords (``Teie Meie`` templates), an intermediate ``nr``/``Nr``, ``[Seosviit]``
# / ``[Viit]`` placeholders, a short incoming ref, and separators. It deliberately
# matches NO prose word, so ``Teie poole 21.06.2012 kirjaga nr 7-4/120880/1203032``
# (the pronoun ``Teie``) is rejected — ``poole`` is not reference-ish.
_DATEISH = r"\d{0,2}\.?\d{1,2}\.\d{4}"
_REF_FILLER = (
    r"(?:kuupäev|Teie|Meie|Õiguskantsler|[Nn]r|" + _DATEISH
    + r"|\[[^\]\n]{0,20}\]|\d{1,3}-\d{1,3}/\d{1,4}|[ \t.,/])"
)
REF_BLOCK_RE = re.compile(
    r"^(?P<lead>.{0,400}?)" + _KW + r"[ \t]*" + _REF_FILLER + r"{0,18}?"
    + r"[Nn]r(?![^\W\d])[ \t]*" + _REF_ID
)

# A body-opener verb (Estonian 1st/2nd person past: ``Pöördusin``, ``Pöördusite``,
# ``Kirjutasite`` …). Its presence in a lead-in means the "reference block" is quoted
# in prose, not a letterhead.
_PROSE_VERB_RE = re.compile(r"\b\w{3,}(?:site|sin)\b")
_LC_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
# Access-restriction confidentiality stamp that heads many letters (``ASUTUSESISESEKS
# KASUTAMISEKS … Juurdepääsupiirang kehtib kuni …``). It is fixed letterhead metadata
# and never appears in opinion prose, so its lowercase words must not be read as prose.
_ACCESS_MARK_RE = re.compile(r"ASUTUSESISESEKS|KASUTAMISEKS|Juurdep[äa]{1,2}supiirang")


def _is_letterhead_leadin(prefix: str) -> bool:
    """True when the text before a ``Teie``/``Meie`` reference is letterhead, not prose.

    Letterhead lead-ins are empty (the body opens with ``Teie``/``Meie``), a recipient
    block (names/orgs, usually with an e-mail), or an access-restriction stamp. Prose
    lead-ins carry a body-opener verb or several lowercase content words — never cut.
    """
    if not prefix.strip():
        return True
    if "@" in prefix or _ACCESS_MARK_RE.search(prefix):
        return True
    if _PROSE_VERB_RE.search(prefix):
        return False
    lowercase_words = sum(1 for w in _LC_WORD_RE.findall(prefix) if w[:1].islower())
    return lowercase_words < 3

# Salutation keyword (Capitalised — always the start of a greeting line).
_SAL_WORDS = r"(?:Lugupeetud|Lugupeetav|Austatud|Auv[äa]{1,2}rt)"
SAL_KEYWORD_RE = re.compile(r"\b" + _SAL_WORDS + r"\b")

# A single recipient-run token consumed after the salutation keyword. SAFE by
# construction: only redactions, honorifics, connectors and lowercase role-words —
# never a Capitalised token (= a name or a Capitalised body opener) and never an
# unknown lowercase word (= a lowercase body opener like ``pöördusite``/``tänan``).
_RECIP_TOKEN = r"""(?:
      \[[^\]\n]{0,80}\]                                   # redaction:  [ ]  [kooli]
    | (?:[Hh][äa]rra|[Pp]roua|[Hh]ra|[Hh]r|[Pp]rl|[Pp]r|[Pp]rof|[Dd]r)\.?   # honorific
    | (?:ja|ning|&)                                       # connector
    | [a-zäöüõšž]+-                                        # hyphen fragment: energeetika-
    | [a-zäöüõšž]*(?:minister(?:id)?|vanem|kantsler|president|direktor
          |juhataja|esimees|aseesimees|esindaja|liige|n[õo]unik|sekret[äa]r
          |prokur[öo]r|[üu]lem|volikogu|kogu|komisjoni?|linnapea|avaldaja[d]?)  # role
    )"""
RECIP_RUN_RE = re.compile(
    r"^(?:[ \t]+" + _RECIP_TOKEN + r"(?=[ \t]|$|[.,:;]))*[ \t]*",
    re.VERBOSE,
)

# Windows (chars into the post-ref body) within which the salutation is treated as
# part of the header. Salutation depth is median 162 / p99 320 / max 762 chars in
# the raw bodies, and much shallower once the ref block is removed.
_SAL_WINDOW = 600
_HEADER_CAP = 500

# Multi-recipient letters stack consecutive salutations (``Lugupeetud minister
# Lugupeetud Maa- ja Ruumiameti peadirektor …``), separated only by a dangling
# name/role. A follow-on salutation is stripped only when it starts within this
# many chars and everything before it is a pure recipient run (no body prose).
_MULTI_SAL_GAP = 60

# A "pure recipient" gap between two stacked salutations: Capitalised name tokens,
# lowercase role tokens, connectors, redactions and commas — nothing else. If this
# fails to match the gap in full, the text after it is real body and the loop stops.
_RECIP_BETWEEN_RE = re.compile(
    r"^(?:\s|,|" + _RECIP_TOKEN + r"|[A-ZÄÖÜÕŠŽ][\wäöüõšž.\-]*)*$",
    re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Residue detectors (for before/after reporting — mirror issue #619's metric).
# ---------------------------------------------------------------------------

RESIDUE_SAL_RE = SAL_KEYWORD_RE
RESIDUE_REF_RE = re.compile(
    r"\bTeie\b.{0,80}?\bnr\b.{0,160}?\bMeie\b.{0,40}?\bnr\b", re.S
)


def has_salutation_residue(text: str) -> bool:
    """True when an office-letter salutation keyword survives in ``text``."""
    return RESIDUE_SAL_RE.search(text) is not None


def has_reference_residue(text: str) -> bool:
    """True when a ``Teie … nr … Meie … nr`` reference block survives in ``text``."""
    return RESIDUE_REF_RE.search(text) is not None


# ---------------------------------------------------------------------------
# Title derivation + repeated-title stripping
# ---------------------------------------------------------------------------

def derive_title(head: str, label: str | None = None) -> str:
    """Return the document title: the ``\\n\\n``-leading head, else label-derived.

    The head (verbatim, possibly an abbreviated human label) is preferred. As a
    fallback it derives from ``rdfs:label`` by stripping a leading
    ``"Õiguskantsleri seisukoht: "`` prefix.
    """
    head = (head or "").strip()
    if head:
        return head
    lab = (label or "").strip()
    for prefix in _LABEL_TITLE_PREFIXES:
        if lab.startswith(prefix):
            return lab[len(prefix):].strip(" :")
    return lab


def _title_to_regex(title: str) -> str:
    """Escape ``title`` into a whitespace-flexible regex fragment.

    Each non-whitespace token is escaped individually and re-joined with ``\\s+``
    so the body's title copy matches across any run of whitespace, regardless of
    how the stale extraction spaced it.
    """
    return r"\s+".join(re.escape(token) for token in title.split())


def _strip_repeated_title(body: str, title: str) -> str:
    """Strip a leading (optionally recipient-prefixed) VERBATIM repeated title.

    Only used when a reference block was removed but no salutation followed.
    Requires a reasonably long, exact (whitespace-flexible) title match within the
    head zone, so it never touches a body that merely paraphrases its title.
    """
    title = title.strip()
    if len(title) < 12:
        return body
    pattern = re.compile(r"^.{0,350}?" + _title_to_regex(title), re.S)
    m = pattern.match(body)
    if m:
        return body[m.end():].lstrip()
    return body


def _consume_salutation_recipient(rest: str) -> str:
    """Drop the recipient run after a salutation keyword; return the lstripped tail."""
    run = RECIP_RUN_RE.match(rest)
    if run:
        rest = rest[run.end():]
    return rest.lstrip()


def _strip_header_and_salutation(body: str, *, ref_removed: bool) -> tuple[str, bool]:
    """Wipe the header remainder + salutation(s) at the start of ``body``.

    Finds the first salutation keyword in the head zone, removes any header
    remainder (recipient + repeated title) preceding it, then drops the salutation
    keyword and its recipient run. Repeats across stacked salutations in
    multi-recipient letters. Returns ``(new_body, changed)``.
    """
    m = SAL_KEYWORD_RE.search(body[:_SAL_WINDOW])
    if not m:
        return body, False

    start = m.start()
    prefix = body[:start]
    if prefix.strip():
        # There is header text between the body head and the salutation. Only wipe
        # it when we are confidently inside a letter header.
        if not ref_removed:
            # No letterhead reference was cut, so only wipe the lead-in when it
            # carries an e-mail address — an unmistakable recipient/letterhead
            # signal that never appears in opinion prose. (A bare ``nr <id>`` is
            # NOT trusted here: it may be a body citation of a prior letter.)
            if "@" not in prefix:
                return body, False
        if start > _HEADER_CAP:
            # Salutation too deep to safely treat the lead-in as header.
            return body, False

    body = _consume_salutation_recipient(body[m.end():])

    # Stacked salutations (multi-recipient letters): strip a follow-on salutation
    # only when it starts within a short, pure-recipient gap — never across prose.
    while True:
        nxt = SAL_KEYWORD_RE.search(body[: _MULTI_SAL_GAP + 30])
        if nxt is None or nxt.start() > _MULTI_SAL_GAP:
            break
        if _RECIP_BETWEEN_RE.match(body[: nxt.start()]) is None:
            break
        body = _consume_salutation_recipient(body[nxt.end():])

    return body, True


def strip_boilerplate(text: str, label: str | None = None) -> tuple[str, bool]:
    """Conservatively strip the Õiguskantsler letter header from ``text``.

    Returns ``(new_text, changed)``. ``text`` is returned unchanged when no clear
    boilerplate is present, when the body has no ``\\n\\n`` title break, or when a
    strip would empty the body.
    """
    if "\n\n" not in text:
        return text, False

    head, body = text.split("\n\n", 1)
    title = derive_title(head, label)
    original_body = body

    # STEP 1 — recipient lead-in + reference block (through the ref-id), but only
    # when the lead-in is a real letterhead (not a reference quoted in prose).
    ref_removed = False
    m = REF_BLOCK_RE.match(body)
    if m and _is_letterhead_leadin(m.group("lead")):
        body = body[m.end():].lstrip()
        ref_removed = True

    # STEP 2+3 — header remainder + salutation (recipient run).
    body, sal_removed = _strip_header_and_salutation(body, ref_removed=ref_removed)

    # Fallback — reference block but no salutation: drop a verbatim repeated title.
    if ref_removed and not sal_removed:
        body = _strip_repeated_title(body, title)

    if not (ref_removed or sal_removed):
        return text, False

    cleaned = body.lstrip()
    if not cleaned.strip():
        # Safety: a strip that empties the body is a mis-match — leave untouched.
        return text, False

    new_text = f"{title}\n\n{cleaned}"
    if new_text == text or cleaned == original_body.strip():
        return text, False
    return new_text, True


# ---------------------------------------------------------------------------
# Node helpers (annotationText / rdfs:label may be a plain string or {@value}).
# ---------------------------------------------------------------------------

def _get_text(node: dict) -> str | None:
    value = node.get(TEXT_KEY)
    if isinstance(value, dict):
        return value.get("@value")
    return value


def _set_text(node: dict, new_text: str) -> None:
    value = node.get(TEXT_KEY)
    if isinstance(value, dict):
        value["@value"] = new_text
    else:
        node[TEXT_KEY] = new_text


def _get_label(node: dict) -> str:
    value = node.get(LABEL_KEY)
    if isinstance(value, dict):
        return value.get("@value", "") or ""
    return value or ""


def _is_annotation(node: dict) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return ANNOTATION_TYPE in node_type
    return node_type == ANNOTATION_TYPE


def iter_annotations(doc: dict):
    """Yield every ``estleg:Annotation`` node in a loaded JSON-LD document."""
    graph = doc.get("@graph", [])
    for node in graph:
        if isinstance(node, dict) and _is_annotation(node):
            yield node


# ---------------------------------------------------------------------------
# File processing + reporting
# ---------------------------------------------------------------------------

def _is_lfs_pointer(path: Path) -> bool:
    """Return True when ``path`` is an un-materialised Git-LFS pointer stub."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith(LFS_POINTER_PREFIX)


class Report:
    """Accumulated per-run statistics."""

    def __init__(self) -> None:
        self.scanned = 0
        self.changed = 0
        self.sal_before = 0
        self.sal_after = 0
        self.ref_before = 0
        self.ref_after = 0
        self.overstrip_70 = 0          # changed nodes that lost > 70% of body length
        self.min_cleaned_len = None    # shortest resulting body among changed nodes
        self.samples: list[tuple[str, str]] = []
        self.overstrip_samples: list[tuple[float, str, str]] = []
        self.first_token_after_clean: Counter[str] = Counter()
        self._sample_titles: set[str] = set()


def _classify_first_token(body: str) -> str:
    token = body.split(" ", 1)[0] if body else ""
    if not token:
        return "(empty)"
    if token.startswith("["):
        return "redaction["
    if token[:1].isupper():
        return "Capital"
    if token[:1].islower():
        return "lower"
    return "other"


def analyse_doc(doc: dict, report: Report, *, sample_target: int = 5) -> dict:
    """Scan/strip every annotation, fill ``report``, return the (possibly) new doc."""
    for node in iter_annotations(doc):
        text = _get_text(node)
        report.scanned += 1
        if not isinstance(text, str):
            continue

        if has_salutation_residue(text):
            report.sal_before += 1
        if has_reference_residue(text):
            report.ref_before += 1

        new_text, changed = strip_boilerplate(text, _get_label(node))

        if has_salutation_residue(new_text):
            report.sal_after += 1
        if has_reference_residue(new_text):
            report.ref_after += 1

        if not changed:
            continue

        report.changed += 1
        _set_text(node, new_text)

        old_body = text.split("\n\n", 1)[1]
        new_body = new_text.split("\n\n", 1)[1]
        report.first_token_after_clean[_classify_first_token(new_body)] += 1
        if report.min_cleaned_len is None or len(new_body) < report.min_cleaned_len:
            report.min_cleaned_len = len(new_body)
        loss = 1.0 - (len(new_body) / len(old_body)) if old_body else 0.0
        if loss > 0.70:
            report.overstrip_70 += 1
            if len(report.overstrip_samples) < 5:
                report.overstrip_samples.append((loss, old_body, new_body))
        title = new_text.split("\n\n", 1)[0]
        if (
            len(report.samples) < sample_target
            and loss <= 0.70
            and title not in report._sample_titles
        ):
            report._sample_titles.add(title)
            report.samples.append((text, new_text))

    return doc


def _fmt_snip(value: str, length: int = 220) -> str:
    snippet = value[:length].replace("\n", "\\n")
    return snippet + ("…" if len(value) > length else "")


def print_report(report: Report, *, dry_run: bool) -> None:
    verb = "would change" if dry_run else "changed"
    print("\n" + "=" * 72)
    print("Õiguskantsler annotation boilerplate strip (#619)")
    print(f"  MODE: {'dry-run (no writes)' if dry_run else 'APPLY (wrote file)'}")
    print("=" * 72)
    print(f"  Annotation nodes scanned:        {report.scanned}")
    print(f"  Nodes {verb}:{'':<11}{report.changed}")
    print("\n  Boilerplate residue (issue #619 metric):")
    print(f"    salutation (Lugupeetud/Austatud…)  before: {report.sal_before:>6}"
          f"   after: {report.sal_after:>6}")
    print(f"    Teie…nr…Meie…nr reference block    before: {report.ref_before:>6}"
          f"   after: {report.ref_after:>6}")

    print("\n  Over-strip guard (content-preservation):")
    pct = (100.0 * report.overstrip_70 / report.changed) if report.changed else 0.0
    print(f"    changed nodes losing > 70% of body length: {report.overstrip_70}"
          f"  ({pct:.2f}% of changed)")
    print(f"    shortest resulting body length:            {report.min_cleaned_len}")
    print("    first token of cleaned body (distribution):"
          f" {dict(report.first_token_after_clean.most_common())}")
    if report.overstrip_samples:
        print("    largest-loss examples (inspect for false positives):")
        for loss, old_body, new_body in report.overstrip_samples:
            print(f"      -{loss * 100:.0f}%  OLD: {_fmt_snip(old_body, 120)}")
            print(f"            NEW: {_fmt_snip(new_body, 120)}")

    print("\n  Before/after spot-check (content preserved — body kept intact):")
    for i, (before, after) in enumerate(report.samples, 1):
        print(f"\n   [{i}] BEFORE: {_fmt_snip(before)}")
        print(f"       AFTER : {_fmt_snip(after)}")
    print("=" * 72)


def process_file(path: Path, *, dry_run: bool) -> Report:
    """Load, strip, (optionally) write, and report on one annotation file."""
    report = Report()
    if _is_lfs_pointer(path):
        raise SystemExit(
            f"ERROR: {path} is an un-materialised Git-LFS pointer.\n"
            f"       Run: git lfs pull --include={path}"
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    analyse_doc(doc, report)
    if not dry_run and report.changed:
        save_json(path, doc)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=ANNOTATION_FILE,
        help="annotation JSON-LD to clean (default: %(default)s)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and report without writing (DEFAULT)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="persist the cleaned annotation text atomically",
    )
    args = parser.parse_args(argv)

    path: Path = args.file
    if not path.is_file():
        print(f"ERROR: file not found: {path}")
        return 1

    dry_run = not args.apply
    report = process_file(path, dry_run=dry_run)
    print_report(report, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
