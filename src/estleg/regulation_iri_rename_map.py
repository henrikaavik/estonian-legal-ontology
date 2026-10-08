#!/usr/bin/env python3
"""Dry-run old->new provision IRI map for the committed regulations (#722).

Regulation provisions were minted with a positional suffix
(``sanitize_id(nr)`` + ``_{len(seen_ids)}`` on a collision). The law pipeline
mints them with ``law_structure._paragraph_id_suffix`` +
``_dedupe_paragraph_suffix``. ``generate_regulations --iri-scheme law`` now
uses the law scheme, but moving the ~168k *published* regulation provision
IRIs to it is a MAJOR change (docs/STABILITY.md). This tool computes the
rename map WITHOUT applying it, so the rewrite can be scheduled, announced
and shipped with an ``owl:sameAs``/redirect table.

Two sources:

* ``--source peep`` (default, offline): derive each law-scheme suffix from
  the committed peep's ``estleg:paragrahv`` display (``§ 9¹.`` -> ``9_1``;
  HTML-path ``§ 4′1`` -> ``4_1``) by feeding it through the SAME law helper
  the generator uses. A display whose base number does not match the legacy
  IRI is reported as ``unresolved`` and mapped to itself. Caveat: a
  superscript carried only by an ``ylaIndeks`` attribute (with no glyph in
  ``kuvatavNr``) is invisible here; use ``--source xml`` for the
  authoritative map.
* ``--source xml``: rebuild each act from the cached RT XML
  (``data/riigiteataja/maarus[_kov]/reg_<gid>.xml``) under both schemes and
  pair the provision (and lõige) IRIs in document order. Acts with no cached
  XML are counted as ``xmlMissing``.

The report is deterministic (sorted keys, no wall-clock stamp). Nothing under
``krr_outputs/`` is written.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from estleg import riigiteataja_common
from estleg.estleg_common import KRR_DIR, act_root_node, sanitize_id
from estleg.generate_regulations import (
    IRI_SCHEME_LAW,
    IRI_SCHEME_LEGACY,
    build_regulation_jsonld,
    html_paragraph_id_suffix,
    regulation_files,
)
from estleg.law_structure import _dedupe_paragraph_suffix, _paragraph_id_suffix

REGULATIONS_DIR = KRR_DIR / "regulations"
SCHEMA_VERSION = 1

_SUP_GLYPHS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_HTML_SUP_SEP_RE = re.compile(r"\d\s*[′'·]\s*\d")
_DISPLAY_BASE_RE = re.compile(r"§\s*([0-9]+[A-Za-z]?)")
_REG_PROVISION_IRI_RE = re.compile(r"estleg:Reg_\d+_Par_[0-9A-Za-z_]+")


def _display_base(display: str) -> str:
    """Base § number from a display string (``§ 9¹.`` -> ``9``)."""
    text = re.sub(r"<sup>\s*\d+\s*</sup>", "", display or "")
    text = text.translate({ord(ch): None for ch in _SUP_GLYPHS})
    m = _DISPLAY_BASE_RE.search(text)
    return m.group(1) if m else ""


def law_suffix_from_display(display: str) -> tuple[str, str] | None:
    """Return ``(raw_law_suffix, legacy_base)`` for a provision display.

    ``legacy_base`` is the number the legacy scheme fed to ``sanitize_id``
    (the committed IRI must start with it). Structured displays go through
    ``law_structure._paragraph_id_suffix`` on a synthetic ``<paragrahv>``
    (``paragrahvNr`` = base, ``kuvatavNr`` = display), so the superscript
    regex is literally the generator's. HTML prime forms (``§ 4′1``) go
    through ``html_paragraph_id_suffix``; their legacy base is the glued
    ``4′1`` (``sanitize_id`` -> ``41``). ``None`` when no § number is
    recognisable.
    """
    if _HTML_SUP_SEP_RE.search(display or ""):
        m = re.search(r"§\s*(\d+\s*[′'·]\s*\d+)", display)
        if not m:
            return None
        nr = re.sub(r"\s+", "", m.group(1))
        return html_paragraph_id_suffix(nr), nr
    base = _display_base(display)
    if not base:
        return None
    par = ET.Element("paragrahv")
    ET.SubElement(par, "paragrahvNr").text = base
    ET.SubElement(par, "kuvatavNr").text = display
    return _paragraph_id_suffix(par), base


def _legacy_base_matches(old_suffix: str, base: str) -> bool:
    clean = sanitize_id(base)
    return bool(clean) and (old_suffix == clean or old_suffix.startswith(clean + "_"))


def act_pairs_from_peep(doc: dict) -> tuple[list[tuple[str, str]], int]:
    """Return ``([(old_iri, new_iri), ...], unresolved_count)`` for one peep.

    Provisions are taken in graph order (the generator's document order);
    duplicate law suffixes are deduped with the generator's counter. Any
    ``…_Par_<old>_Lg_…`` subsection IRI is carried along under its parent's
    new suffix.
    """
    graph = doc.get("@graph") or []
    root = act_root_node(doc) or {}
    tid = root.get("estleg:terviktekstId")
    if isinstance(tid, dict):
        tid = tid.get("@value")
    if not tid:
        return [], 0
    stem = f"estleg:Reg_{tid}_Par_"
    counts: Counter[str] = Counter()
    pairs: list[tuple[str, str]] = []
    par_map: dict[str, str] = {}
    unresolved = 0
    for node in graph:
        if not isinstance(node, dict) or "estleg:paragrahv" not in node:
            continue
        old = node.get("@id") or ""
        if not old.startswith(stem):
            continue
        old_suffix = old[len(stem):]
        derived = law_suffix_from_display(str(node.get("estleg:paragrahv") or ""))
        if derived is None or not _legacy_base_matches(old_suffix, derived[1]):
            unresolved += 1
            # Map to itself; reserve the suffix so a later law-scheme
            # duplicate still dedupes against it.
            counts[old_suffix] += 1
            new_suffix = old_suffix
        else:
            new_suffix = _dedupe_paragraph_suffix(derived[0], counts)
        new = stem + new_suffix
        par_map[old] = new
        pairs.append((old, new))
    for node in graph:
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id") or ""
        if "_Lg_" not in node_id:
            continue
        parent, _, lg = node_id.rpartition("_Lg_")
        if parent in par_map:
            pairs.append((node_id, f"{par_map[parent]}_Lg_{lg}"))
    return pairs, unresolved


def _provision_ids(doc: dict) -> list[str]:
    return [
        n["@id"]
        for n in doc.get("@graph", [])
        if isinstance(n, dict)
        and isinstance(n.get("@id"), str)
        and ("estleg:paragrahv" in n or "_Lg_" in n["@id"])
    ]


def act_pairs_from_xml(
    root: ET.Element, *, title: str, info: dict, is_kov: bool, kehtiv: str | None
) -> list[tuple[str, str]]:
    """Authoritative pairs: build the act under both schemes and zip."""
    legacy, _ = build_regulation_jsonld(
        title, info, root, is_kov=is_kov, kehtiv=kehtiv, iri_scheme=IRI_SCHEME_LEGACY
    )
    law, _ = build_regulation_jsonld(
        title, info, root, is_kov=is_kov, kehtiv=kehtiv, iri_scheme=IRI_SCHEME_LAW
    )
    old_ids, new_ids = _provision_ids(legacy), _provision_ids(law)
    if len(old_ids) != len(new_ids):
        raise ValueError(f"scheme node counts differ: {len(old_ids)} vs {len(new_ids)}")
    return list(zip(old_ids, new_ids))


def _xml_for_doc(doc: dict, is_kov: bool) -> ET.Element | None:
    root = act_root_node(doc) or {}
    gid = root.get("estleg:globalId") or root.get("estleg:terviktekstId")
    if not gid:
        return None
    subdir = "maarus_kov" if is_kov else "maarus"
    path = riigiteataja_common.DATA_DIR / subdir / f"reg_{gid}.xml"
    if not path.is_file():
        return None
    try:
        return riigiteataja_common.parse_xml_file(path)
    except (ET.ParseError, ValueError):
        return None


def _scan_references(paths: Iterable[Path], changed: set[str], owner: dict[str, str]) -> Counter[str]:
    """Count references to changed provision IRIs from files that do not own them."""
    refs: Counter[str] = Counter()
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path)
        for token in _REG_PROVISION_IRI_RE.findall(text):
            if token in changed and owner.get(token) != rel:
                refs[token] += 1
    return refs


def compute_rename_map(
    regulations_dir: Path = REGULATIONS_DIR,
    *,
    source: str = "peep",
    include_map: bool = True,
    scan_references: bool = False,
) -> dict:
    """Compute the deterministic dry-run report for ``regulations_dir``."""
    if source not in ("peep", "xml"):
        raise ValueError(f"unknown source {source!r}")
    totals: Counter[str] = Counter()
    rename: dict[str, str] = {}
    owner: dict[str, str] = {}
    duplicate_new: list[str] = []
    chain_conflicts: list[str] = []
    tid_files: dict[str, list[str]] = {}
    by_corpus: dict[str, Counter[str]] = {}

    for corpus in ("riik", "kov"):
        corpus_dir = regulations_dir / corpus
        if not corpus_dir.is_dir():
            continue
        corpus_counts = by_corpus.setdefault(corpus, Counter())
        for path in regulation_files(corpus_dir):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except (json.JSONDecodeError, OSError):
                totals["unreadableFiles"] += 1
                continue
            root = act_root_node(doc) or {}
            tid = root.get("estleg:terviktekstId")
            if isinstance(tid, dict):
                tid = tid.get("@value")
            rel = str(path.relative_to(regulations_dir))
            if tid:
                tid_files.setdefault(str(tid), []).append(rel)
            corpus_counts["acts"] += 1
            unresolved = 0
            if source == "xml":
                xml_root = _xml_for_doc(doc, corpus == "kov")
                if xml_root is None:
                    corpus_counts["xmlMissing"] += 1
                    continue
                title = str(root.get("dcterms:title") or root.get("dc:source") or "")
                kehtiv = root.get("estleg:kehtiv")
                if isinstance(kehtiv, dict):
                    kehtiv = kehtiv.get("@value")
                try:
                    pairs = act_pairs_from_xml(
                        xml_root, title=title, info={"tid": tid},
                        is_kov=corpus == "kov", kehtiv=kehtiv,
                    )
                except ValueError:
                    corpus_counts["xmlBuildFailed"] += 1
                    continue
            else:
                pairs, unresolved = act_pairs_from_peep(doc)
            corpus_counts["unresolved"] += unresolved
            act_changed = 0
            old_ids = {old for old, _ in pairs}
            seen_new: set[str] = set()
            for old, new in pairs:
                is_sub = "_Lg_" in old
                corpus_counts["subsections" if is_sub else "provisions"] += 1
                owner[old] = str(path)
                if new in seen_new:
                    duplicate_new.append(new)
                seen_new.add(new)
                if old != new:
                    act_changed += 1
                    corpus_counts["changedSubsections" if is_sub else "changedProvisions"] += 1
                    rename[old] = new
                    if new in old_ids:
                        # The target is another node's CURRENT IRI in the same
                        # act: an in-place apply must rewrite simultaneously.
                        chain_conflicts.append(new)
            if act_changed:
                corpus_counts["actsWithChanges"] += 1

    for counts in by_corpus.values():
        for key, value in counts.items():
            totals[key] += value
    duplicate_tids = {tid: sorted(files) for tid, files in tid_files.items() if len(files) > 1}

    report: dict = {
        "schemaVersion": SCHEMA_VERSION,
        "issue": "#722",
        "source": source,
        "applied": False,
        "scheme": {"from": IRI_SCHEME_LEGACY, "to": IRI_SCHEME_LAW},
        "totals": {
            "acts": totals["acts"],
            "provisions": totals["provisions"],
            "subsections": totals["subsections"],
            "changedProvisions": totals["changedProvisions"],
            "changedSubsections": totals["changedSubsections"],
            "actsWithChanges": totals["actsWithChanges"],
            "unresolved": totals["unresolved"],
            "xmlMissing": totals["xmlMissing"],
            "xmlBuildFailed": totals["xmlBuildFailed"],
            "unreadableFiles": totals["unreadableFiles"],
        },
        "byCorpus": {k: dict(sorted(v.items())) for k, v in sorted(by_corpus.items())},
        "collisions": {
            "duplicateNewIris": len(duplicate_new),
            "duplicateNewIriExamples": sorted(set(duplicate_new))[:20],
            "chainConflicts": len(chain_conflicts),
            "chainConflictExamples": sorted(set(chain_conflicts))[:20],
            "duplicateTerviktekstIds": len(duplicate_tids),
            "duplicateTerviktekstIdExamples": dict(sorted(duplicate_tids.items())[:20]),
        },
        "examples": dict(sorted(rename.items())[:25]),
    }
    if scan_references and rename:
        refs = _scan_references(
            sorted(regulations_dir.glob("**/*.json")), set(rename), owner
        )
        report["externalReferences"] = {
            "scope": str(regulations_dir.name),
            "referencesToChangedIris": sum(refs.values()),
            "distinctChangedIrisReferenced": len(refs),
        }
    if include_map:
        report["map"] = dict(sorted(rename.items()))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regulations-dir", type=Path, default=REGULATIONS_DIR)
    parser.add_argument("--source", choices=("peep", "xml"), default="peep")
    parser.add_argument("--output", type=Path, default=None, help="Write the full report + map as JSON here (never under krr_outputs/ by default).")
    parser.add_argument("--scan-references", action="store_true", help="Also count references to changed IRIs from other regulation files.")
    args = parser.parse_args(argv)

    report = compute_rename_map(
        args.regulations_dir,
        source=args.source,
        include_map=args.output is not None,
        scan_references=args.scan_references,
    )
    summary = {k: v for k, v in report.items() if k not in ("map", "examples")}
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
