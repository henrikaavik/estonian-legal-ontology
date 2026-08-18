#!/usr/bin/env python3
"""Load the Estonian Legal Ontology and print three answered SPARQL sentences.

Prefers ``krr_outputs/combined_ontology.jsonld`` when it is real JSON-LD (not
an un-materialised Git LFS pointer). Otherwise falls back to
``perekonnaseadus_peep.json`` plus the KarS sanctions sidecar so the script
still answers without the 265 MB LFS object.

Usage::

    python3 examples/quickstart.py
    python3 examples/quickstart.py --graph krr_outputs/perekonnaseadus_peep.json
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

from rdflib import Graph

REPO_ROOT = Path(__file__).resolve().parent.parent
COMBINED_RELPATH = Path("krr_outputs") / "combined_ontology.jsonld"
FALLBACK_PEEP = Path("krr_outputs") / "perekonnaseadus_peep.json"
FALLBACK_KARS_PEEP = Path("krr_outputs") / "karistusseadustik_osa2_peep.json"
SANCTIONS_DIR = Path("krr_outputs") / "sanctions"
PREFERRED_SANCTION = SANCTIONS_DIR / "sanctions_karistusseadustik_osa2.json"

ESTLEG_NS = "https://w3id.org/estleg/"
LFS_POINTER_PREFIX = "version https://git-lfs"

PREFIXES = """
PREFIX estleg: <https://w3id.org/estleg/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""

QUERY_ACT_COUNT = PREFIXES + """
SELECT (COUNT(DISTINCT ?act) AS ?n) WHERE {
  ?act a estleg:Act .
  FILTER NOT EXISTS { ?act estleg:isStubNode true }
}
"""

QUERY_PART_OF_ACT = PREFIXES + """
SELECT ?part ?partLabel ?act ?actLabel WHERE {
  ?part estleg:partOfAct ?act .
  OPTIONAL { ?part rdfs:label ?partLabel }
  OPTIONAL { ?act rdfs:label ?actLabel }
}
ORDER BY DESC(BOUND(?partLabel) && CONTAINS(STR(?partLabel), "§"))
LIMIT 1
"""

QUERY_SANCTION = PREFIXES + """
SELECT ?provision ?provLabel ?type ?min ?max ?minAmt ?maxAmt
       ?minUnit ?maxUnit ?sLabel WHERE {
  {
    ?provision estleg:hasSanction ?sanction .
    ?sanction estleg:sanctionType ?type .
  }
  UNION
  {
    ?sanction a estleg:Sanction ;
              estleg:sanctionType ?type ;
              estleg:applicableProvision ?provision .
  }
  OPTIONAL { ?provision rdfs:label ?provLabel }
  OPTIONAL { ?sanction rdfs:label ?sLabel }
  OPTIONAL { ?sanction estleg:minPenalty ?min }
  OPTIONAL { ?sanction estleg:maxPenalty ?max }
  OPTIONAL { ?sanction estleg:minPenaltyAmount ?minAmt }
  OPTIONAL { ?sanction estleg:maxPenaltyAmount ?maxAmt }
  OPTIONAL { ?sanction estleg:minPenaltyUnit ?minUnit }
  OPTIONAL { ?sanction estleg:maxPenaltyUnit ?maxUnit }
  FILTER(LCASE(STR(?type)) = "imprisonment")
}
ORDER BY DESC(CONTAINS(STR(?provision), "Par_113")) ?provision
LIMIT 1
"""

_PAR_IN_IRI = re.compile(r"Par_(\d+(?:_\d+)*)")
_SECTION_IN_LABEL = re.compile(r"§\s*([\d¹²³⁴⁵⁶⁷⁸⁹0-9]+(?:\s*[¹-⁹])?)")
_FIRST_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def is_lfs_pointer(path: Path) -> bool:
    """True when ``path`` is an un-materialised Git LFS pointer."""
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().startswith(LFS_POINTER_PREFIX)
    except OSError:
        return False


def is_real_jsonld(path: Path) -> bool:
    return path.is_file() and not is_lfs_pointer(path)


def _resolve_user_path(path: Path, repo_root: Path) -> Path:
    if path.is_absolute():
        return path
    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate
    return repo_root / path


def _fallback_paths(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for rel in (FALLBACK_PEEP, FALLBACK_KARS_PEEP):
        candidate = repo_root / rel
        if is_real_jsonld(candidate):
            paths.append(candidate)
    preferred = repo_root / PREFERRED_SANCTION
    if is_real_jsonld(preferred):
        paths.append(preferred)
        return paths
    sanctions = repo_root / SANCTIONS_DIR
    if sanctions.is_dir():
        paths.extend(p for p in sorted(sanctions.glob("*.json")) if is_real_jsonld(p))
    return paths


def resolve_load_paths(
    graph: Path | None = None, *, repo_root: Path | None = None
) -> list[Path]:
    """Return JSON-LD files to parse: ``--graph``, else combined, else fallback."""
    root = repo_root or REPO_ROOT
    if graph is not None:
        path = _resolve_user_path(graph, root)
        return [path] if is_real_jsonld(path) else []
    combined = root / COMBINED_RELPATH
    if is_real_jsonld(combined):
        return [combined]
    return _fallback_paths(root)


def load_graph(paths: Sequence[Path]) -> Graph:
    graph = Graph()
    for path in paths:
        graph.parse(source=str(path), format="json-ld")
    return graph


def _text(term: object | None) -> str:
    if term is None:
        return ""
    return str(term)


def _compact(iri: str) -> str:
    if iri.startswith(ESTLEG_NS):
        return "estleg:" + iri[len(ESTLEG_NS) :]
    return iri


def query_act_count(graph: Graph) -> int:
    rows = list(graph.query(QUERY_ACT_COUNT))
    if not rows:
        return 0
    return int(rows[0][0])


def query_part_of_act(graph: Graph) -> dict[str, str] | None:
    rows = list(graph.query(QUERY_PART_OF_ACT))
    if not rows:
        return None
    row = rows[0]
    return {
        "part": _text(row.part),
        "part_label": _text(row.partLabel),
        "act": _text(row.act),
        "act_label": _text(row.actLabel),
    }


def query_sanction(graph: Graph) -> dict[str, str] | None:
    rows = list(graph.query(QUERY_SANCTION))
    if not rows:
        return None
    row = rows[0]
    return {
        "provision": _text(row.provision),
        "provision_label": _text(row.provLabel),
        "type": _text(row.type),
        "min": _text(row.min),
        "max": _text(row.max),
        "min_amt": _text(row.minAmt),
        "max_amt": _text(row.maxAmt),
        "min_unit": _text(row.minUnit),
        "max_unit": _text(row.maxUnit),
        "sanction_label": _text(row.sLabel),
    }


def format_act_count(count: int) -> str:
    noun = "Act" if count == 1 else "Acts"
    return f"The loaded graph contains {count} {noun}."


def format_part_of_act(fact: dict[str, str] | None) -> str:
    if fact is None:
        return "No partOfAct edge was found on the loaded graph."
    part = fact["part_label"] or _compact(fact["part"])
    act = fact["act_label"] or _compact(fact["act"])
    return f"{part} is part of {act}."


def _section_number(label: str, iri: str) -> str:
    match = _SECTION_IN_LABEL.search(label)
    if match:
        return match.group(1).strip()
    match = _PAR_IN_IRI.search(iri)
    if match:
        return match.group(1).replace("_", " ")
    return ""


def _title_from_label(label: str) -> str:
    if "." in label and "§" in label:
        return label.split(".", 1)[1].strip()
    return ""


def _numeric_span(minimum: str, maximum: str, unit: str) -> str:
    lo = _FIRST_NUMBER.search(minimum)
    hi = _FIRST_NUMBER.search(maximum)
    if lo and hi:
        span = f"{lo.group(1)}–{hi.group(1)}"
        return f"{span} {unit}".strip() if unit else span
    if minimum and maximum:
        return f"{minimum} to {maximum}"
    return maximum or minimum


def _imprisonment_span(fact: dict[str, str]) -> str:
    unit = fact["min_unit"] or fact["max_unit"]
    if unit == "years" or "year" in (fact["min"] + fact["max"]).lower():
        unit = "years"
    elif not unit:
        unit = ""
    minimum = fact["min"] or fact["min_amt"]
    maximum = fact["max"] or fact["max_amt"]
    if minimum and maximum:
        return _numeric_span(minimum, maximum, unit)
    if maximum:
        return f"up to {maximum}"
    if minimum:
        return f"at least {minimum}"
    label = fact["sanction_label"]
    if label:
        head = label.split("(", 1)[0]
        head = re.sub(r"^Imprisonment,?\s*", "", head, flags=re.I).strip(" ,")
        return head
    return ""


def format_sanction(fact: dict[str, str] | None) -> str:
    if fact is None:
        return "No KarS / sanction fact was found on the loaded graph."
    iri = fact["provision"]
    label = fact["provision_label"]
    kars = "KARIST" in iri.upper() or "KARS" in iri.upper()
    section = _section_number(label, iri)
    title = _title_from_label(label)
    head = "KarS " if kars else ""
    if section:
        head += f"§ {section}"
        if title:
            head += f" ({title})"
    else:
        head += label or _compact(iri)
    span = _imprisonment_span(fact)
    if span:
        return f"{head} carries imprisonment of {span}."
    return f"{head} carries an imprisonment sanction."


def answered_sentences(graph: Graph) -> list[str]:
    """Run the three SPARQL helpers and return printable answer sentences."""
    return [
        format_act_count(query_act_count(graph)),
        format_part_of_act(query_part_of_act(graph)),
        format_sanction(query_sanction(graph)),
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--graph",
        type=Path,
        default=None,
        help="JSON-LD file to load instead of combined / fallback files.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_load_paths(args.graph)
    if not paths:
        if args.graph is not None:
            print(
                f"{args.graph} is missing or a Git LFS pointer; "
                "omit --graph to use the fallback files."
            )
        else:
            print(
                "No loadable JSON-LD graph was found "
                "(combined is missing or a Git LFS pointer, and fallback files are absent)."
            )
        return 0
    try:
        graph = load_graph(paths)
    except Exception as exc:
        print(f"Could not load a graph: {exc}")
        return 0
    for sentence in answered_sentences(graph):
        print(sentence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
