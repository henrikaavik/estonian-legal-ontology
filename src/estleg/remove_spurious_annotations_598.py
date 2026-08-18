#!/usr/bin/env python3
"""Remove the data residual of the pre-#651 ``find_in_title`` substring bug (#598).

Offline, idempotent, surgical pass over
``krr_outputs/annotations/oiguskantsler_seisukohad.jsonld``.

Background
----------
Before #651, ``generate_annotations._LawIndex.find_in_title`` matched law names
against an opinion *title* with a raw ``if name in hay`` substring test (no token
boundaries). When an opinion title named a LONG act whose registered name has a
SHORTER act's title as a substring, BOTH acts were emitted — the shorter one a
*spurious* cross-act leak. ``find_in_body`` was already token-bounded (#233), so
the dedup union of (buggy title) ∪ (correct body) carried the extra edge:

    title "… kriminaalmenetluse seadustiku rakendamise seadus …"
        OLD find_in_title → {KrMSRS, KRIMIN}     (KRIMIN ⊂ KrMSRS leaks)
        NEW find_in_title → {KrMSRS}             (longest-token wins)

#651 routed ``find_in_title`` through the same token-bounded leftmost-longest
engine as ``find_in_body`` (``_scan_bounded``); the SHIPPED data, generated
earlier, still carries the leaked shorter-act edges.

What is spurious (the exact bug signature)
------------------------------------------
An ``estleg:Annotation`` node is spurious iff, **re-deriving with the FIXED
matcher** (``generate_annotations.build_law_index`` →
``_LawIndex.find_in_title`` / ``find_in_body``), the node's annotated act would
no longer be produced *and* a co-annotated longer act explains the leak:

  1. the act's normalized title is NOT a standalone token match of the opinion
     title (the fixed ``find_in_title`` drops it), AND
  2. it is NOT a standalone token match of the opinion body / ``annotationText``
     either (the fixed ``find_in_body`` would not re-introduce it — this
     *body-guard* protects a law that the opinion genuinely discusses by bare
     name, e.g. an opinion that cites both an act and its implementation act), AND
  3. the SAME opinion annotates a strictly longer act whose normalized title
     CONTAINS this act's normalized title AND which the fixed ``find_in_title``
     DOES still match in the opinion title (the longer act that consumed the
     leaked span — the "kept" act of the audit triple).

Comparisons are made on the corpus *act title* (act IRI → its ``owl:Ontology``
``dc:source``, normalized) rather than on the IRI, so the pass is robust to
``_Map`` / osa IRI drift between the shipped data and the current corpus. Because
``estleg:AnnotationShape`` requires exactly one ``estleg:annotates`` per node, a
spurious edge has no standalone meaning: the whole node is dropped.

This is deliberately NARROW — it removes only cross-act substring leaks where the
longer act is itself present, never a legitimate multi-act annotation (where
neither title contains the other), never a body-evidenced bare-name reference,
and never the partitive-only ``…seadust`` self-inflection case (no longer act).
The corpus has ~41 title-substring PAIRS, but the leak only manifests where the
longer act's title actually appears in an opinion TITLE, so the live residual is
a handful of edges, not 41.

Idempotent: once the leaked nodes are gone, the surviving longer act still
token-matches the title (kept by rule 1), so a re-run removes nothing.

Run (offline, idempotent; dry-run by default)::

    python3 scripts/remove_spurious_annotations_598.py            # report only
    python3 scripts/remove_spurious_annotations_598.py --apply    # write changes
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from estleg import generate_annotations as ga
from estleg.estleg_common import KRR_DIR, save_json

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

ANNOTATION_FILE = KRR_DIR / "annotations" / "oiguskantsler_seisukohad.jsonld"
ANNOTATION_TYPE = "estleg:Annotation"
HEADER_ID = "estleg:Annotations_Oiguskantsler_Map"

LABEL_KEY = "rdfs:label"
ANNOTATES_KEY = "estleg:annotates"
TEXT_KEY = "estleg:annotationText"
SOURCE_URL_KEY = "estleg:annotationSourceUrl"

# rdfs:label prefix prepended to the opinion title by the generator.
_LABEL_PREFIX = "Õiguskantsleri seisukoht: "
# "(N annotatsiooni)" count carried in the Map header node's label.
_HEADER_COUNT_RE = re.compile(r"\(\d+ annotatsiooni\)")


# ---------------------------------------------------------------------------
# Node helpers (annotationText / rdfs:label / sourceUrl may be {@value} or str)
# ---------------------------------------------------------------------------

def _scalar(value: object) -> str:
    """Pull a plain string from a JSON-LD value that may be ``{"@value": …}``."""
    if isinstance(value, dict):
        inner = value.get("@value", "")
        return inner if isinstance(inner, str) else ""
    return value if isinstance(value, str) else ""


def _is_annotation(node: object) -> bool:
    if not isinstance(node, dict):
        return False
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return ANNOTATION_TYPE in node_type
    return node_type == ANNOTATION_TYPE


def _annotates_iri(node: dict) -> str | None:
    target = node.get(ANNOTATES_KEY)
    if isinstance(target, dict):
        iri = target.get("@id")
        return iri if isinstance(iri, str) else None
    return target if isinstance(target, str) else None


def _opinion_title(node: dict) -> str:
    label = _scalar(node.get(LABEL_KEY))
    return label[len(_LABEL_PREFIX):].strip() if label.startswith(_LABEL_PREFIX) else label


def _opinion_key(node: dict) -> str:
    """Stable per-opinion grouping key: the source URL, else the (full) label."""
    url = _scalar(node.get(SOURCE_URL_KEY))
    if url:
        return "url::" + url
    return "label::" + _scalar(node.get(LABEL_KEY))


# ---------------------------------------------------------------------------
# Corpus act-title index (act IRI -> normalized dc:source title)
# ---------------------------------------------------------------------------

def build_iri_title_map(krr_dir: Path = KRR_DIR) -> dict[str, str]:
    """Map every root act node IRI to its normalized ``dc:source`` title.

    Mirrors :func:`generate_annotations.build_law_index`'s scope (root
    ``*_peep.json`` only) so the IRIs line up with the ``find_in_title`` /
    ``find_in_body`` results. Reuses the same act-node and title helpers.
    """
    iri_title: dict[str, str] = {}
    for path in sorted(krr_dir.glob("*_peep.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        graph = doc.get("@graph", [])
        iri = ga._ontology_node_iri(graph)
        if not iri:
            continue
        for node in graph:
            if isinstance(node, dict) and "owl:Ontology" in (node.get("@type", []) or []):
                title = ga._clean_dc_source(node.get("dc:source"))
                if title:
                    iri_title.setdefault(iri, ga._norm_name(title))
                break
    return iri_title


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Spurious:
    """One spurious annotation slated for removal, with its audit justification."""

    opinion_title: str
    node_id: str | None
    dropped_iri: str | None
    dropped_title: str
    kept_iri: str | None
    kept_title: str


class _MatchCache:
    """Caches title/body -> set-of-act-titles the FIXED matcher finds (by string)."""

    def __init__(self, index, iri_title: dict[str, str]) -> None:
        self._index = index
        self._iri_title = iri_title
        self._title: dict[str, set[str]] = {}
        self._body: dict[str, set[str]] = {}

    def _titles_of(self, iris) -> set[str]:
        return {self._iri_title[i] for i in iris if i in self._iri_title}

    def in_title(self, title: str) -> set[str]:
        if title not in self._title:
            self._title[title] = self._titles_of(self._index.find_in_title(title))
        return self._title[title]

    def in_body(self, text: str) -> set[str]:
        if text not in self._body:
            self._body[text] = self._titles_of(self._index.find_in_body(text))
        return self._body[text]


def detect_spurious(graph: list, index, iri_title: dict[str, str]) -> list[Spurious]:
    """Return the spurious cross-act annotation nodes in ``graph`` (see module doc).

    ``index`` is any object exposing ``find_in_title(str) -> list[str]`` and
    ``find_in_body(str) -> list[str]`` (the FIXED ``generate_annotations._LawIndex``
    in production; a stub in tests). ``iri_title`` maps act IRI -> normalized title.
    """
    opinions: dict[str, list[dict]] = {}
    for node in graph:
        if _is_annotation(node):
            opinions.setdefault(_opinion_key(node), []).append(node)

    cache = _MatchCache(index, iri_title)
    spurious: list[Spurious] = []
    for nodes in opinions.values():
        title = _opinion_title(nodes[0])
        title_titles = cache.in_title(title)
        # Each annotated act's normalized title, and a title -> IRI back-map for audit.
        sib_title_to_iri: dict[str, str | None] = {}
        for node in nodes:
            iri = _annotates_iri(node)
            ta = iri_title.get(iri) if iri else None
            if ta is not None:
                sib_title_to_iri.setdefault(ta, iri)
        sib_titles = set(sib_title_to_iri)

        for node in nodes:
            iri = _annotates_iri(node)
            ta = iri_title.get(iri) if iri else None
            if ta is None:
                continue                       # unknown act title -> never touch
            if ta in title_titles:
                continue                       # fixed find_in_title still yields it
            if ta in cache.in_body(_scalar(node.get(TEXT_KEY))):
                continue                       # legit standalone body token (kept)
            # A strictly longer co-annotated act that the fixed title scan keeps.
            longer = [
                tb for tb in sib_titles
                if tb != ta and ta in tb and tb in title_titles
            ]
            if not longer:
                continue                       # no cross-act leak -> keep
            kept_title = max(longer, key=len)
            spurious.append(
                Spurious(
                    opinion_title=title,
                    node_id=node.get("@id"),
                    dropped_iri=iri,
                    dropped_title=ta,
                    kept_iri=sib_title_to_iri.get(kept_title),
                    kept_title=kept_title,
                )
            )
    return spurious


# ---------------------------------------------------------------------------
# Document rewriting
# ---------------------------------------------------------------------------

def _update_header_count(graph: list, annotation_count: int) -> None:
    """Refresh the Map header node's ``(N annotatsiooni)`` label after removal."""
    for node in graph:
        if isinstance(node, dict) and node.get("@id") == HEADER_ID:
            label = node.get(LABEL_KEY)
            if isinstance(label, dict) and isinstance(label.get("@value"), str):
                label["@value"] = _HEADER_COUNT_RE.sub(
                    f"({annotation_count} annotatsiooni)", label["@value"]
                )
            return


def apply_removal(doc: dict, spurious: list[Spurious]) -> int:
    """Drop the spurious nodes from ``doc['@graph']`` in place; return remaining count."""
    remove_ids = {s.node_id for s in spurious}
    graph = doc.get("@graph", [])
    kept = [n for n in graph if not (_is_annotation(n) and n.get("@id") in remove_ids)]
    doc["@graph"] = kept
    remaining = sum(1 for n in kept if _is_annotation(n))
    _update_header_count(kept, remaining)
    return remaining


# ---------------------------------------------------------------------------
# File processing + reporting
# ---------------------------------------------------------------------------

def _is_lfs_pointer(path: Path) -> bool:
    """Return True when ``path`` is an un-materialised Git-LFS pointer stub."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.readline().startswith(LFS_POINTER_PREFIX)
    except OSError:
        return False


@dataclass
class Report:
    scanned: int = 0
    opinions: int = 0
    removed: int = 0
    remaining: int = 0
    spurious: list[Spurious] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.spurious is None:
            self.spurious = []


def process_file(
    path: Path,
    index,
    iri_title: dict[str, str],
    *,
    dry_run: bool,
) -> Report:
    """Load, detect, (optionally) rewrite, and report on one annotation file."""
    if _is_lfs_pointer(path):
        raise SystemExit(
            f"ERROR: {path} is an un-materialised Git-LFS pointer.\n"
            f"       Run: git lfs pull --include={path}"
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    graph = doc.get("@graph", [])

    report = Report()
    report.scanned = sum(1 for n in graph if _is_annotation(n))
    report.opinions = len({_opinion_key(n) for n in graph if _is_annotation(n)})
    report.spurious = detect_spurious(graph, index, iri_title)
    report.removed = len(report.spurious)
    report.remaining = report.scanned - report.removed

    if report.removed and not dry_run:
        apply_removal(doc, report.spurious)
        save_json(path, doc)
    return report


def print_report(report: Report, *, dry_run: bool) -> None:
    verb = "would remove" if dry_run else "removed"
    print("=" * 72)
    print("Spurious cross-act annotation removal (#598)")
    print(f"  MODE: {'dry-run (no writes)' if dry_run else 'APPLY (wrote file)'}")
    print("=" * 72)
    print(f"  Annotation nodes scanned:   {report.scanned}")
    print(f"  Distinct opinions:          {report.opinions}")
    print(f"  Spurious nodes {verb}:  {report.removed}")
    print(f"  Annotation nodes remaining: {report.remaining}")

    if not report.spurious:
        print("\n  No spurious cross-act annotations found.")
        print("=" * 72)
        return

    pairs = sorted({(s.dropped_title, s.kept_title) for s in report.spurious})
    print(f"\n  Distinct (removed-shorter ⊂ kept-longer) act-title pairs: {len(pairs)}")
    for shorter, longer in pairs:
        print(f"    '{shorter}'  ⊂  '{longer}'")

    print("\n  Audit (opinion | DROP shorter-act | KEEP longer-act):")
    for s in sorted(report.spurious, key=lambda x: x.opinion_title):
        print(f"    [{s.opinion_title[:58]}]")
        print(f"        DROP {s.dropped_iri}  '{s.dropped_title}'")
        print(f"        KEEP {s.kept_iri}  '{s.kept_title}'")
    print("=" * 72)


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
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="corpus root for the act-title index (default: krr_outputs/)",
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
        help="persist the cleaned annotation file atomically",
    )
    args = parser.parse_args(argv)

    path: Path = args.file
    if not path.is_file():
        print(f"ERROR: file not found: {path}")
        return 1
    if not args.krr_dir.is_dir():
        print(f"ERROR: not a directory: {args.krr_dir}")
        return 1

    dry_run = not args.apply
    index = ga.build_law_index(args.krr_dir)
    iri_title = build_iri_title_map(args.krr_dir)
    report = process_file(path, index, iri_title, dry_run=dry_run)
    print_report(report, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
