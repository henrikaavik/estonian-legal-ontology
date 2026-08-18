#!/usr/bin/env python3
"""Link CURIA decisions to EU legislation named in their labels (#418).

Deterministic and offline: CELEX ids are parsed from Estonian
``rdfs:label`` / ``dcterms:title`` text and kept only when the matching
``estleg:EU_*`` node already exists in the eurlex peeps. No network.

    estleg:interpretsEULaw -> {"@id": "estleg:EU_<CELEX>"}

The property is declared in ``controlled_vocabulary.jsonld`` with no
``rdfs:range`` so the curia SHACL bucket does not phantom-type bare
``estleg:EU_*`` stubs under ``inference=rdfs``.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from estleg_common import REPO_ROOT, save_json
from eurlex_common import sanitize_celex

CURIA_FILES = (
    "curia/curia_judgments_peep.json",
    "curia/curia_orders_peep.json",
    "curia/curia_ag_opinions_peep.json",
    "curia/curia_court_opinions_peep.json",
    "curia/curia_other_peep.json",
    "curia/curia_combined.jsonld",
)

EURLEX_PEEPS = (
    "eurlex/eurlex_regulations_peep.json",
    "eurlex/eurlex_directives_peep.json",
    "eurlex/eurlex_decisions_peep.json",
)

_BARE_CELEX = re.compile(r"\b([1-5]\d{4}[LRD]\d{4})\b", re.IGNORECASE)

# Direktiiv (EL) 2019/790  /  Direktiiv 2008/98/EÜ  /  Direktiiv 80/987/EMÜ
_DIRECTIVE = re.compile(
    r"direktiiv(?:i)?"
    r"(?:\s*\((?:EL|EÜ|EMÜ)\))?"
    r"\s+(\d{2,4})\s*/\s*(\d{1,4})"
    r"(?:\s*/\s*(?:EL|EÜ|EMÜ))?",
    re.IGNORECASE,
)

# Määrus (EL) 2016/679  /  Määrus (EÜ) nr 44/2001  /  nr 1408/71
_REGULATION = re.compile(
    r"m[äa]{1,2}rus(?:e)?"
    r"(?:\s*\((?:EL|EÜ|EMÜ)\))?"
    r"(?:\s+nr\.?)?"
    r"\s+(\d{2,4})\s*/\s*(\d{1,4})",
    re.IGNORECASE,
)


def literal_text(value: object) -> str:
    """Unwrap a JSON-LD literal to a plain string."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("@value")
        return text if isinstance(text, str) else ""
    return ""


def _expand_year(token: str) -> int | None:
    if len(token) == 4 and token.isdigit():
        year = int(token)
        return year if 1900 <= year <= 2099 else None
    if len(token) == 2 and token.isdigit():
        n = int(token)
        return 1900 + n if n >= 50 else 2000 + n
    return None


def _pair_year_number(first: str, second: str) -> tuple[int, int] | None:
    """Resolve (year, act-number) from a citation pair.

    Classic directives are year/number (80/987, 2008/98). Regulations
    often write number/year (44/2001, 1408/71). A 4-digit 19xx/20xx
    token is always the year.
    """
    y1, y2 = _expand_year(first), _expand_year(second)
    if y1 is not None and len(first) == 4:
        return y1, int(second)
    if y2 is not None and len(second) == 4:
        return y2, int(first)
    if y2 is not None and len(second) == 2 and len(first) >= 3:
        return y2, int(first)
    if y1 is not None:
        return y1, int(second)
    return None


def _celex(sector_type: str, year: int, number: int) -> str:
    return f"3{year}{sector_type}{number:04d}"


def parse_eu_citations(label: str) -> list[str]:
    """Return unique CELEX ids cited in ``label``, first-seen order."""
    if not label:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def _add(celex: str) -> None:
        celex = celex.upper()
        if celex not in seen:
            seen.add(celex)
            out.append(celex)

    for match in _BARE_CELEX.finditer(label):
        _add(match.group(1))
    for match in _DIRECTIVE.finditer(label):
        pair = _pair_year_number(match.group(1), match.group(2))
        if pair is not None:
            _add(_celex("L", pair[0], pair[1]))
    for match in _REGULATION.finditer(label):
        pair = _pair_year_number(match.group(1), match.group(2))
        if pair is not None:
            _add(_celex("R", pair[0], pair[1]))
    return out


def celex_to_iri(celex: str) -> str:
    return f"estleg:EU_{sanitize_celex(celex)}"


def _node_types(node: dict) -> list[str]:
    raw = node.get("@type", [])
    return [raw] if isinstance(raw, str) else list(raw)


def _existing_iris(value: object) -> list[str]:
    items = value if isinstance(value, list) else ([value] if value else [])
    out: list[str] = []
    for item in items:
        iri = item.get("@id") if isinstance(item, dict) else item
        if isinstance(iri, str):
            out.append(iri)
    return out


def link_decision_node(node: dict, known_iris: set[str]) -> bool:
    """Append ``estleg:interpretsEULaw`` edges. Returns True if changed."""
    if "estleg:EUCourtDecision" not in _node_types(node):
        return False
    text = " ".join(
        part
        for part in (
            literal_text(node.get("rdfs:label")),
            literal_text(node.get("dcterms:title")),
        )
        if part
    )
    targets = [
        celex_to_iri(celex)
        for celex in parse_eu_citations(text)
        if celex_to_iri(celex) in known_iris
    ]
    if not targets:
        return False
    existing = _existing_iris(node.get("estleg:interpretsEULaw"))
    have = set(existing)
    added = [iri for iri in targets if iri not in have]
    if not added:
        return False
    merged = existing + added
    node["estleg:interpretsEULaw"] = (
        {"@id": merged[0]} if len(merged) == 1 else [{"@id": iri} for iri in merged]
    )
    return True


def load_known_eu_iris(krr_dir: Path) -> set[str]:
    """Collect ``estleg:EULegislation`` @ids from committed eurlex peeps."""
    known: set[str] = set()
    for rel in EURLEX_PEEPS:
        path = krr_dir / rel
        if not path.exists():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            if "estleg:EULegislation" not in _node_types(node):
                continue
            iri = node.get("@id")
            if isinstance(iri, str):
                known.add(iri)
    return known


def process_file(
    path: Path, known_iris: set[str], dry_run: bool
) -> tuple[int, int, int]:
    """Backfill one CURIA file. Returns (changed, decisions, edges_added)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    decisions = 0
    edges = 0
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        if "estleg:EUCourtDecision" not in _node_types(node):
            continue
        decisions += 1
        before = len(_existing_iris(node.get("estleg:interpretsEULaw")))
        if link_decision_node(node, known_iris):
            changed += 1
            edges += len(_existing_iris(node.get("estleg:interpretsEULaw"))) - before
    if changed and not dry_run:
        save_json(path, doc)
    return changed, decisions, edges


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=REPO_ROOT / "krr_outputs",
        help="Corpus root (default: <repo>/krr_outputs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any file.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write a deterministic JSON report (default: krr_outputs/curia/curia_eu_link_report.json).",
    )
    args = parser.parse_args(argv)
    report_path = args.report or (args.krr_dir / "curia" / "curia_eu_link_report.json")

    known = load_known_eu_iris(args.krr_dir)
    print("=" * 70)
    print("Link CURIA decisions to EU legislation (#418)")
    print("=" * 70)
    print(f"  known EU act IRIs: {len(known)}")

    total_changed = 0
    total_decisions = 0
    total_edges = 0
    labeled_with_cite = 0
    labeled_linked = 0
    samples: list[str] = []

    for rel in CURIA_FILES:
        path = args.krr_dir / rel
        if not path.exists():
            print(f"  SKIP (missing): {rel}")
            continue
        # Count label-cite match rate from the file *before* write so
        # the report is independent of dry-run.
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            if "estleg:EUCourtDecision" not in _node_types(node):
                continue
            text = " ".join(
                part
                for part in (
                    literal_text(node.get("rdfs:label")),
                    literal_text(node.get("dcterms:title")),
                )
                if part
            )
            cites = parse_eu_citations(text)
            if not cites:
                continue
            labeled_with_cite += 1
            if any(celex_to_iri(c) in known for c in cites):
                labeled_linked += 1
                iri = node.get("@id")
                if isinstance(iri, str) and len(samples) < 10:
                    samples.append(iri)
        changed, decisions, edges = process_file(path, known, args.dry_run)
        total_changed += changed
        total_decisions += decisions
        total_edges += edges
        print(f"  {rel}: {changed}/{decisions} decisions, {edges} edges")

    match_rate = (
        round(labeled_linked / labeled_with_cite, 4) if labeled_with_cite else 0.0
    )
    report = {
        "known_eu_iris": len(known),
        "decisions_scanned": total_decisions,
        "decisions_changed": total_changed,
        "edges_added": total_edges,
        "decisions_with_parsed_citation": labeled_with_cite,
        "decisions_with_parsed_citation_linked": labeled_linked,
        "match_rate_among_parsed": match_rate,
        "sample_linked_ids": samples,
    }
    if not args.dry_run:
        save_json(report_path, report)
    print(f"\nmatch rate (parsed citations): {match_rate} "
          f"({labeled_linked}/{labeled_with_cite})")
    verb = "would add" if args.dry_run else "added"
    print(f"{verb} {total_edges} interpretsEULaw edges "
          f"on {total_changed} decisions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
