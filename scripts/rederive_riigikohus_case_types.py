#!/usr/bin/env python3
"""Re-derive case types for legacy Riigikohus decisions dumped as Other (#579).

679 Riigikohus decisions (all filed 1993-1996) carry
``estleg:caseType = estleg:CaseType_Other``. They predate the modern
``N-yy-…`` numbering and use Roman-numeral case numbers
(``III-<chamber>/<sub>-<seq>/<yy>``), which ``classify_case`` in
``generate_court_decisions.py`` did not understand, so they fell through to
the ``Other`` placeholder — even though their *deciding chamber* (and hence
case type) is plainly stated in the decision text.

This OFFLINE post-process recovers the case type from the **deciding-chamber
token** in ``estleg:legalText`` — the single authoritative signal:

  * ``kriminaal[kohtu]kolleegium``                  -> CaseType_Criminal
  * ``tsiviil[kohtu]kolleegium``                    -> CaseType_Civil
  * ``halduskolleegium`` / ``halduskohtukolleegium``-> CaseType_Administrative
  * ``põhiseaduslikkuse järelevalve kolleegium``    -> CaseType_ConstitutionalReview

(The OCR'd legacy texts occasionally drop a letter — ``kollegium`` — so the
matcher tolerates ``koll?eegium``.) An explicit ``"<Type>asi nr."`` declaration
in the **document head** (first 220 chars) is used only as a tiebreak, never a
body match: a body cross-reference such as ``"Kriminaalasi nr. 94740005"`` in
a civil judgment refers to a *different* case and must not retype this one.

Conservative by design (per #579): a node is retyped ONLY when exactly one
chamber is named. Decisions with no chamber token (empty/illegible text) or
with several conflicting chamber tokens stay ``CaseType_Other`` — a genuine
last resort, not a guess. On the current corpus this retypes 589 and leaves
90 as Other.

The generator (``classify_case``) is patched separately to recognise the
Roman ``III-1/``/``III-2/`` chambers from the case number alone (the only
signal available before the legalText-enrichment pass), so a regen no longer
re-dumps the unambiguous Criminal/Civil cases.

Safety / scope:
  * Only nodes currently typed ``estleg:CaseType_Other`` are considered;
    every other decision is left untouched.
  * Idempotent: a node already retyped is no longer ``CaseType_Other`` and is
    skipped, so a re-run reports 0 changes.
  * git-LFS pointer files (first line ``version https://git-lfs...``) are
    skipped — they are not JSON.
  * Atomic writes via ``estleg_common.save_json``; ``--dry-run`` reports only.
  * Operates on the per-year ``riigikohus_*_peep.json`` files; the generated
    combined graph is rebuilt once by the maintainer afterwards.

Run (offline, idempotent, expected 589 retyped on the current corpus):

    .venv/bin/python scripts/rederive_riigikohus_case_types.py
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from estleg_common import REPO_ROOT, save_json

CASE_TYPE_PREDICATE = "estleg:caseType"
LEGAL_TEXT_PREDICATE = "estleg:legalText"
CASE_TYPE_OTHER = "estleg:CaseType_Other"

# Deciding-chamber token -> case-type individual. Ordered most-specific first
# so constitutional review and the chamber names cannot shadow one another.
# ``koll?e+gium`` tolerates the OCR spelling variants seen in the legacy texts
# — ``kolleegium`` (canonical), ``kollegium`` (dropped ``e``) and an optional
# missing second ``l`` — without ever matching across an unrelated word.
CHAMBER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "estleg:CaseType_ConstitutionalReview",
        re.compile(r"p[õo]hiseaduslikkuse\s+j[äa]relevalve\s+koll?e+gium", re.IGNORECASE),
    ),
    (
        "estleg:CaseType_Criminal",
        re.compile(r"kriminaal(?:kohtu)?koll?e+gium", re.IGNORECASE),
    ),
    (
        "estleg:CaseType_Civil",
        re.compile(r"tsiviil(?:kohtu)?koll?e+gium", re.IGNORECASE),
    ),
    (
        "estleg:CaseType_Administrative",
        re.compile(r"halduskoll?e+gium|halduskohtukoll?e+gium", re.IGNORECASE),
    ),
]

# Tiebreak only: an explicit "<Type>asi nr" declaration in the document HEAD.
# Restricted to the first ``HEAD_CHARS`` characters so body cross-references
# to other cases ("Kriminaalasi nr. 94740005 on peatatud …") never fire.
HEAD_CHARS = 220
HEAD_DECL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "estleg:CaseType_ConstitutionalReview",
        re.compile(r"p[õo]hiseaduslikkuse\s+j[äa]relevalve\s+asi", re.IGNORECASE),
    ),
    ("estleg:CaseType_Criminal", re.compile(r"kriminaalasi", re.IGNORECASE)),
    ("estleg:CaseType_Civil", re.compile(r"tsiviilasi", re.IGNORECASE)),
    (
        "estleg:CaseType_Administrative",
        re.compile(r"haldusasi|haldus[õo]igusrikkumise\s+asi", re.IGNORECASE),
    ),
]

DEFAULT_RK_DIR = REPO_ROOT / "krr_outputs" / "riigikohus"
PEEP_GLOB = "riigikohus_*_peep.json"

_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


def is_lfs_pointer(path: Path) -> bool:
    """True if ``path`` is an unmaterialised git-LFS pointer (not real JSON)."""
    try:
        with path.open(encoding="utf-8") as fh:
            return fh.readline().startswith(_LFS_POINTER_PREFIX)
    except OSError:
        return False


def _legal_text(node: dict) -> str:
    v = node.get(LEGAL_TEXT_PREDICATE)
    if isinstance(v, dict):
        v = v.get("@value")
    return v if isinstance(v, str) else ""


def _matches(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    """Distinct case-type ids whose pattern matches ``text`` (order-stable)."""
    out: list[str] = []
    for case_type, rx in patterns:
        if rx.search(text) and case_type not in out:
            out.append(case_type)
    return out


def derive_case_type(legal_text: str) -> str | None:
    """Return the case-type IRI implied by ``legal_text``, or ``None``.

    Primary signal: a single named deciding chamber. If the chamber is
    ambiguous (none or several), fall back to a single explicit
    ``"<Type>asi nr"`` declaration in the document head. Returns ``None``
    when neither yields exactly one type — the caller leaves such a node as
    ``CaseType_Other``.
    """
    if not legal_text:
        return None
    chambers = _matches(legal_text, CHAMBER_PATTERNS)
    if len(chambers) == 1:
        return chambers[0]
    head_decls = _matches(legal_text[:HEAD_CHARS], HEAD_DECL_PATTERNS)
    if len(head_decls) == 1:
        # If a (multiple) chamber set exists, only accept a head declaration
        # that is consistent with it — never contradict a named chamber.
        if not chambers or head_decls[0] in chambers:
            return head_decls[0]
    return None


def _case_type_id(node: dict) -> str | None:
    ct = node.get(CASE_TYPE_PREDICATE)
    if isinstance(ct, dict):
        return ct.get("@id")
    return ct if isinstance(ct, str) else None


def retype_node(node: dict) -> str | None:
    """Retype one ``CaseType_Other`` node from its legal text, in place.

    Returns the new case-type IRI if the node was changed, else ``None``
    (not an Other node, or the signal was inconclusive). Idempotent: a node
    that is no longer ``CaseType_Other`` is never touched.
    """
    if _case_type_id(node) != CASE_TYPE_OTHER:
        return None
    derived = derive_case_type(_legal_text(node))
    if derived is None or derived == CASE_TYPE_OTHER:
        return None
    node[CASE_TYPE_PREDICATE] = {"@id": derived}
    return derived


def process_file(path: Path, dry_run: bool) -> tuple[int, dict[str, int]]:
    """Retype legacy Other nodes in one peep file.

    Returns ``(changed_count, per_type_counts)``.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    graph = doc.get("@graph", [])
    per_type: dict[str, int] = {}
    if not isinstance(graph, list):
        return 0, per_type
    changed = 0
    for node in graph:
        if not isinstance(node, dict):
            continue
        new_type = retype_node(node)
        if new_type is not None:
            changed += 1
            per_type[new_type] = per_type.get(new_type, 0) + 1
    if changed and not dry_run:
        save_json(path, doc)
    return changed, per_type


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rk-dir",
        type=Path,
        default=DEFAULT_RK_DIR,
        help="Directory of riigikohus_*_peep.json files (default: krr_outputs/riigikohus).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    args = parser.parse_args(argv)

    rk_dir: Path = args.rk_dir
    if not rk_dir.is_dir():
        print(f"ERROR: not a directory: {rk_dir}")
        return 1

    print("=" * 70)
    print("Re-derive Riigikohus case types from CaseType_Other (#579)")
    print(f"  Directory: {rk_dir}")
    print("=" * 70)

    files = sorted(rk_dir.glob(PEEP_GLOB))
    files_changed = 0
    total_changed = 0
    total_by_type: dict[str, int] = {}
    for path in files:
        if is_lfs_pointer(path):
            print(f"  SKIP (git-LFS pointer): {path.name}")
            continue
        changed, per_type = process_file(path, args.dry_run)
        if changed:
            files_changed += 1
            total_changed += changed
            for k, v in per_type.items():
                total_by_type[k] = total_by_type.get(k, 0) + v
            print(f"  {path.name}: {changed} retyped")

    verb = "would retype" if args.dry_run else "retyped"
    print(f"\n  Files scanned: {len(files)}")
    print(f"  Files {verb}: {files_changed}")
    print(f"  Decisions {verb}: {total_changed}")
    for case_type, count in sorted(total_by_type.items()):
        print(f"    {case_type}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
