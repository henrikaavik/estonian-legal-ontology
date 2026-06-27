#!/usr/bin/env python3
"""Repair wrong EUR-Lex ``documentDate`` + Council/Commission swaps (#582).

Offline, idempotent, surgical post-process over the EU-legislation nodes in
``krr_outputs/eurlex/eurlex_*_peep.json``. It fixes the two provenance/temporal
defects verified for issue #582 **without** re-running the CELLAR-backed
(network) ``generate_eu_legislation.py`` pipeline. The Estonian ``rdfs:label``
title is treated as the AUTHORITATIVE source for both signals.

(a) ``estleg:documentDate`` contradicts the date in the title.
    The title carries the act date in Estonian as ``"<day>. <month> <year>"``
    (e.g. ``"Komisjoni otsus, 26. juuli 2010 , …"``), almost always in the
    NOMINATIVE month form (``juuli``, ``märts``, ``oktoober``). The CELEX
    (``estleg:celexNumber`` / the ``estleg:EU_<celex>`` ``@id``) encodes the
    act year in the four digits after the sector digit (``32010D0415`` →
    ``2010``). To stay high-confidence we correct the ``documentDate`` **only**
    when the parsed title-year EQUALS the CELEX-year AND differs from the
    current ``documentDate`` year. That guard deliberately skips title typos:
    ``EU_31997D0245`` (title "…20. märts 1977") and ``EU_32003D0912`` (title
    "…17. detsember 1999") have a wrong YEAR in the *title* while CELEX +
    ``documentDate`` agree, so the CELEX cross-check leaves them alone. It does
    fire on ``EU_32010D0415`` (title ``2010``, CELEX ``2010``, but
    ``documentDate`` ``2007-07-04``) → ``2010-07-26``. When it fires the
    ``documentDate`` is set to the FULL parsed title date (``YYYY-MM-DD``).

(b) ``estleg:euInstitution`` attributes the wrong issuing body.
    A title beginning ``"Nõukogu …"`` is a Council act; one beginning
    ``"Komisjon…"`` is a Commission act. Only the Council↔Commission confusion
    is repaired (the issue's 39 verified single-issuer swaps): a ``Nõukogu``
    title carrying ``EUInst_EuropeanCommission`` is flipped to
    ``EUInst_CouncilOfEU`` and vice-versa. Titles whose ``euInstitution`` is a
    more-specific THIRD body (e.g. ``EUInst_CONSILSG`` for a "Nõukogu
    peasekretäri … otsus", ``EUInst_EuropeanCentralBank``) are left untouched —
    reclassifying those to the generic Council/Commission would be a
    regression, and #582 scoped the swap to the Commission/Council pair.

Scope / safety:
  * Only ``@id estleg:EU_*`` EU-legislation nodes are touched; the per-file
    ``owl:Ontology`` header nodes are skipped.
  * Corrigenda are EXCLUDED from BOTH repairs. A corrigendum's title describes
    its PARENT act ("Komisjoni 26. juuni 2009 . aasta määruse … parandus"), so
    its leading institution word and date refer to the corrected act, not the
    corrigendum node itself. A corrigendum is detected by the canonical CELEX
    marker — a trailing ``R(<n>)`` on ``estleg:celexNumber`` (equivalent to the
    issue's "``@id`` ending ``R0``+digit" but also catching the few ``R(10)+``).
  * The generated ``eurlex_combined.jsonld`` is NOT rewritten here (it is a
    build artifact rebuilt by the canonical builder); only the ``*_peep.json``
    sources named by #582 are repaired.
  * Idempotent: the year guard (a) and the swap-only-from-the-opposite-body
    guard (b) both make a second run a no-op (0 changes).
  * Atomic writes via :func:`estleg_common.save_json` (tempfile + os.replace);
    git-LFS pointer stubs are skipped before ``json.loads``.
  * Dry-run is the DEFAULT (report only); pass ``--apply`` to write.

Run (offline, idempotent)::

    python3 scripts/fix_eurlex_metadata_582.py            # dry-run (default)
    python3 scripts/fix_eurlex_metadata_582.py --apply    # write changes
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from estleg_common import ESTONIAN_MONTHS_GENITIVE, REPO_ROOT, save_json

EURLEX_DIR = REPO_ROOT / "krr_outputs" / "eurlex"
PEEP_GLOB = "eurlex_*_peep.json"

LFS_POINTER_PREFIX = "version https://git-lfs"

EU_ID_PREFIX = "estleg:EU_"
LABEL_KEY = "rdfs:label"
CELEX_KEY = "estleg:celexNumber"
DOCDATE_KEY = "estleg:documentDate"
INST_KEY = "estleg:euInstitution"

# The two issuing-body individuals #582 swaps between (verified IRIs in use).
COMMISSION_IRI = "estleg:EUInst_EuropeanCommission"
COUNCIL_IRI = "estleg:EUInst_CouncilOfEU"

# EUR-Lex ET titles carry the act date in the NOMINATIVE month form
# ("26. juuli 2010", "20. märts 2013", "9. august 2011"); a genitive tail
# ("… detsembri 2006") also occurs. Reuse the shared genitive table
# (estleg_common.ESTONIAN_MONTHS_GENITIVE, the single source of truth) as a
# base and merge the nominative forms on top so either inflection parses.
_NOMINATIVE_MONTHS: dict[str, str] = {
    "jaanuar": "01", "veebruar": "02", "märts": "03", "aprill": "04",
    "mai": "05", "juuni": "06", "juuli": "07", "august": "08",
    "september": "09", "oktoober": "10", "november": "11", "detsember": "12",
}
TITLE_MONTHS: dict[str, str] = {**ESTONIAN_MONTHS_GENITIVE, **_NOMINATIVE_MONTHS}

# Longest-first alternation so "märtsi" wins over "märts" (the trailing-year
# anchor already disambiguates, but ordering is belt-and-suspenders).
_MONTH_ALT = "|".join(
    re.escape(name) for name in sorted(TITLE_MONTHS, key=len, reverse=True)
)
# "<day>. <month> <year>" — the separators allow the NO-BREAK SPACE (U+00A0)
# that EUR-Lex routinely emits ("26. juuli", "24. oktoober 1980").
_SEP = r"[\s\u00a0]+"
_TITLE_DATE_RE = re.compile(
    r"(\d{1,2})\." + _SEP + r"(" + _MONTH_ALT + r")" + _SEP + r"(\d{4})"
)

# Canonical CELEX corrigendum marker: a trailing "R(<n>)" descriptor
# ("32008R1256R(01)", "31995D0408R(02)"). A plain regulation type letter
# ("32008R1256") or a decision OJ-sequence ("32013D0005(01)") has no such
# trailing "R(…)" and is therefore NOT a corrigendum.
_CORRIGENDUM_RE = re.compile(r"R\(\d+\)$")

# Sector digit + four-digit year at the head of a CELEX ("32010D0415" → 2010).
_CELEX_YEAR_RE = re.compile(r"^\d(\d{4})")


def parse_title_date(label: str) -> str | None:
    """Parse the act date from an EUR-Lex ET title into ``YYYY-MM-DD``.

    Matches the first ``"<day>. <month> <year>"`` group with a known Estonian
    month name (nominative or genitive). Returns the ISO date string, or
    ``None`` when no such date is present. ``"… 26. juuli 2010 , …"`` →
    ``"2010-07-26"``; ``"… 1. juuli 1999, …"`` → ``"1999-07-01"``.
    """
    match = _TITLE_DATE_RE.search(label or "")
    if match is None:
        return None
    day, month, year = match.group(1), match.group(2).lower(), match.group(3)
    month_num = TITLE_MONTHS.get(month)
    if month_num is None:  # pragma: no cover - alternation only emits known keys
        return None
    return f"{year}-{month_num}-{int(day):02d}"


def institution_from_title(label: str) -> str | None:
    """Return the issuing-body IRI implied by the title's leading word.

    ``"Nõukogu …"`` → Council, ``"Komisjon…"`` → Commission, else ``None``.
    The match is on the RAW label start (no OJ-number prefix stripping), which
    is what makes "Euroopa Parlamendi ja nõukogu …" (co-decision) and prefixed
    decisions ("2010/415/: Komisjoni …") correctly fall through to ``None`` —
    reproducing the issue's unambiguous single-issuer scope.
    """
    text = (label or "").lstrip()
    if text.startswith("Nõukogu"):
        return COUNCIL_IRI
    if text.startswith("Komisjon"):
        return COMMISSION_IRI
    return None


def celex_year(celex: str) -> str | None:
    """Return the four-digit act year encoded after the CELEX sector digit."""
    match = _CELEX_YEAR_RE.match(celex or "")
    return match.group(1) if match else None


def is_corrigendum(celex: str) -> bool:
    """True when the CELEX carries the trailing ``R(<n>)`` corrigendum marker."""
    return bool(celex) and _CORRIGENDUM_RE.search(celex) is not None


def _label(node: dict) -> str:
    """Best-effort plain string for ``rdfs:label`` (str / ``{@value}`` / list)."""
    value = node.get(LABEL_KEY, "")
    if isinstance(value, dict):
        value = value.get("@value", "")
    elif isinstance(value, list):
        value = value[0] if value else ""
        if isinstance(value, dict):
            value = value.get("@value", "")
    return value if isinstance(value, str) else ""


def _node_celex(node: dict) -> str:
    """CELEX for ``node`` from ``estleg:celexNumber`` or the ``estleg:EU_`` id."""
    celex = node.get(CELEX_KEY)
    if isinstance(celex, str) and celex:
        return celex
    nid = node.get("@id", "")
    if isinstance(nid, str) and nid.startswith(EU_ID_PREFIX):
        return nid[len(EU_ID_PREFIX):]
    return ""


def _docdate_value(node: dict) -> str | None:
    """Current ``estleg:documentDate`` ``@value`` string, or ``None``."""
    obj = node.get(DOCDATE_KEY)
    if isinstance(obj, dict):
        val = obj.get("@value")
        return val if isinstance(val, str) and val else None
    return obj if isinstance(obj, str) and obj else None


def _set_docdate(node: dict, iso: str) -> None:
    """Set ``estleg:documentDate`` to ``iso`` preserving the typed-literal shape."""
    obj = node.get(DOCDATE_KEY)
    if isinstance(obj, dict):
        obj["@value"] = iso
        obj.setdefault("@type", "xsd:date")
    else:
        node[DOCDATE_KEY] = {"@value": iso, "@type": "xsd:date"}


def _institution_id(node: dict) -> str | None:
    """Current ``estleg:euInstitution`` ``@id``, or ``None``."""
    obj = node.get(INST_KEY)
    if isinstance(obj, dict):
        iri = obj.get("@id")
        return iri if isinstance(iri, str) else None
    return obj if isinstance(obj, str) else None


def fix_node(node: dict) -> tuple[bool, bool]:
    """Apply #582 (a)+(b) to one EU node in place.

    Returns ``(documentdate_fixed, institution_fixed)``. Corrigenda are skipped
    for both repairs (their title describes the parent act).
    """
    celex = _node_celex(node)
    if is_corrigendum(node.get(CELEX_KEY) or celex):
        return False, False

    label = _label(node)

    # (a) documentDate — correct ONLY when the title year is corroborated by
    #     the CELEX year and disagrees with the current documentDate year.
    date_fixed = False
    iso = parse_title_date(label)
    if iso is not None:
        title_year = iso[:4]
        cyear = celex_year(celex)
        current = _docdate_value(node)
        if (
            cyear is not None
            and cyear == title_year
            and current is not None
            and current[:4] != title_year
        ):
            _set_docdate(node, iso)
            date_fixed = True

    # (b) euInstitution — flip ONLY between the Council/Commission pair.
    inst_fixed = False
    target = institution_from_title(label)
    if target is not None:
        opposite = COMMISSION_IRI if target == COUNCIL_IRI else COUNCIL_IRI
        if _institution_id(node) == opposite:
            node[INST_KEY] = {"@id": target}
            inst_fixed = True

    return date_fixed, inst_fixed


class FixStats:
    """Mutable per-run tally."""

    __slots__ = (
        "files_scanned",
        "files_changed",
        "files_skipped_lfs",
        "nodes_scanned",
        "corrigenda_skipped",
        "documentdate_fixed",
        "institution_fixed",
    )

    def __init__(self) -> None:
        self.files_scanned = 0
        self.files_changed = 0
        self.files_skipped_lfs = 0
        self.nodes_scanned = 0
        self.corrigenda_skipped = 0
        self.documentdate_fixed = 0
        self.institution_fixed = 0


def migrate_doc(doc: dict, stats: FixStats) -> tuple[int, int]:
    """Fix every EU node in ``doc`` in place. Returns this doc's ``(dates, insts)``.

    Mutates ``stats`` with running totals (nodes scanned, corrigenda skipped).
    """
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return 0, 0
    dates = 0
    insts = 0
    for node in graph:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id", "")
        if not (isinstance(nid, str) and nid.startswith(EU_ID_PREFIX)):
            continue
        stats.nodes_scanned += 1
        if is_corrigendum(node.get(CELEX_KEY) or _node_celex(node)):
            stats.corrigenda_skipped += 1
            continue
        date_fixed, inst_fixed = fix_node(node)
        dates += 1 if date_fixed else 0
        insts += 1 if inst_fixed else 0
    return dates, insts


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return fh.read(60).startswith(LFS_POINTER_PREFIX)
    except OSError:
        return True


def process_file(path: Path, stats: FixStats, *, dry_run: bool) -> tuple[int, int]:
    """Migrate one (non-pointer) eurlex peep file. Returns ``(dates, insts)``.

    Updates the doc-level tallies on ``stats``; per-file iteration, the LFS
    guard and ``files_scanned`` are owned by :func:`main`.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    dates, insts = migrate_doc(doc, stats)
    stats.documentdate_fixed += dates
    stats.institution_fixed += insts
    if dates or insts:
        stats.files_changed += 1
        if not dry_run:
            save_json(path, doc)
    return dates, insts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eurlex-dir",
        type=Path,
        default=EURLEX_DIR,
        help="Directory holding the eurlex_*_peep.json files.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Write the repairs to disk (default: dry-run, report only).",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing (this is the default).",
    )
    args = parser.parse_args(argv)
    dry_run = not args.apply

    eurlex_dir: Path = args.eurlex_dir
    if not eurlex_dir.is_dir():
        print(f"ERROR: not a directory: {eurlex_dir}")
        return 1

    print("=" * 70)
    print("Fix EUR-Lex documentDate vs title + Council/Commission swaps (#582)")
    print(f"  Directory: {eurlex_dir}")
    print(f"  MODE: {'dry-run (no writes)' if dry_run else 'apply (writing)'}")
    print("=" * 70)

    stats = FixStats()
    verb = "would fix" if dry_run else "fixed"
    for path in sorted(eurlex_dir.glob(PEEP_GLOB)):
        stats.files_scanned += 1
        if _is_lfs_pointer(path):
            print(f"  (skip, git-LFS pointer — run `git lfs pull`) {path.name}")
            stats.files_skipped_lfs += 1
            continue
        dates, insts = process_file(path, stats, dry_run=dry_run)
        print(
            f"  {path.name}: documentDate {verb}={dates}, "
            f"euInstitution {verb}={insts}"
        )

    print(f"\n  Files scanned:             {stats.files_scanned}")
    print(f"  Files skipped (LFS ptr):   {stats.files_skipped_lfs}")
    print(f"  Files {verb}:            {stats.files_changed}")
    print(f"  Nodes scanned:             {stats.nodes_scanned}")
    print(f"  Corrigenda excluded:       {stats.corrigenda_skipped}")
    print(f"  documentDate corrected:    {stats.documentdate_fixed}")
    print(f"  euInstitution corrected:   {stats.institution_fixed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
