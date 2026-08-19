#!/usr/bin/env python3
"""Analytical overlay: citation counts, coverage-gap flags, scored similarity (#521).

Counts and gap flags are stamped onto the same IRIs as the published graph.
Similarity scores are reified as ``estleg:Similarity`` nodes (the pair-level
shape KOV already uses) because a nested score on ``semanticallySimilarTo``
mis-attaches to the target (#422).

    python3 -m estleg.generate_analytical_overlay --write --patch-combined
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    CONTEXT,
    KRR_DIR,
    save_json,
)
from estleg.materialize_combined_inverses import (
    apply_updates,
    iri_values,
    iter_combined_objects,
    node_types,
    rewrite_combined,
)

STATUTE_TYPES = frozenset({"estleg:Act", "estleg:Law"})
REGULATION_TYPES = frozenset(
    {
        "estleg:NationalRegulation",
        "estleg:MunicipalRegulation",
        "estleg:GovernmentRegulation",
        "estleg:MinisterialRegulation",
        "estleg:DomesticRegulation",
    }
)

OVERLAY_DIR = KRR_DIR / "analytical"
OVERLAY_NAME = "analytical_overlay.jsonld"
SIMILARITY_INDEX = KRR_DIR / "reports" / "similarity_index.json"
EURLEX_DIRECTIVES = KRR_DIR / "eurlex" / "eurlex_directives_peep.json"
COMBINED_NAME = "combined_ontology.jsonld"
DIRECTIVE_ID = re.compile(r"estleg:EU_3\d{3}L")
BOOL_TRUE = {"@value": "true", "@type": "xsd:boolean"}
SIMILARITY_MODEL = "keyword_jaccard"


def is_statute(types: list[str]) -> bool:
    if not any(item in STATUTE_TYPES for item in types):
        return False
    return not any(item in REGULATION_TYPES for item in types)


def json_bool(value: object) -> bool | None:
    if value is True:
        return True
    if value is False:
        return False
    if isinstance(value, dict):
        raw = value.get("@value", value)
        if raw is True:
            return True
        if raw is False:
            return False
        if isinstance(raw, str):
            return raw.lower() == "true"
    if isinstance(value, str):
        return value.lower() == "true"
    return None


def is_directive_id(nid: str) -> bool:
    return bool(DIRECTIVE_ID.match(nid))


def is_eu_legislation(types: list[str]) -> bool:
    return "estleg:EULegislation" in types


def load_similarity_pairs(path: Path | None = None) -> list[dict]:
    source = path or SIMILARITY_INDEX
    if not source.exists():
        return []
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    pairs = data.get("pairs") or []
    return [pair for pair in pairs if isinstance(pair, dict)]


def load_in_force_directives(path: Path | None = None) -> set[str]:
    """``estleg:EU_<celex>`` ids of in-force directives from the EUR-Lex peep."""
    source = path or EURLEX_DIRECTIVES
    found: set[str] = set()
    if not source.exists():
        return found
    try:
        doc = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return found
    for node in doc.get("@graph") or []:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        if not isinstance(nid, str):
            continue
        if json_bool(node.get("estleg:inForce")) is not True:
            continue
        if is_directive_id(nid) or is_eu_legislation(node_types(node)):
            found.add(nid)
    return found


def similarity_node(source: str, target: str, score: float) -> dict:
    src = source.removeprefix("estleg:")
    tgt = target.removeprefix("estleg:")
    return {
        "@id": f"estleg:Similarity_{src}_{tgt}",
        "@type": ["owl:NamedIndividual", "estleg:Similarity"],
        "estleg:similarFrom": {"@id": source},
        "estleg:similarTarget": {"@id": target},
        "estleg:similarityScore": {
            "@value": f"{round(float(score), 3)}",
            "@type": "xsd:decimal",
        },
        "estleg:similarityModel": SIMILARITY_MODEL,
    }


def plan_analytical_updates(
    nodes: list[dict],
    *,
    in_force_directives: set[str] | None = None,
) -> dict[str, dict]:
    """Counts + gap flags for existing IRIs. Omits zeros and false flags."""
    transposed_targets: set[str] = set()
    planned: dict[str, dict] = defaultdict(dict)
    known_force = in_force_directives if in_force_directives is not None else set()

    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        if not isinstance(nid, str):
            continue
        types = node_types(node)
        for target in iri_values(node.get("estleg:transposesDirective")):
            transposed_targets.add(target)
        for target in iri_values(node.get("estleg:transposedBy")):
            # already-transposed directive
            transposed_targets.add(nid)

        inbound = len(iri_values(node.get("estleg:referencedBy")))
        if inbound:
            planned[nid]["estleg:inboundCitationCount"] = inbound
        interpreted = len(iri_values(node.get("estleg:interpretedBy")))
        if interpreted:
            planned[nid]["estleg:interpretationCount"] = interpreted

        if is_statute(types):
            authorities = len(iri_values(node.get("estleg:competentAuthority")))
            planned[nid]["estleg:competentAuthorityCount"] = authorities
            if authorities == 0:
                planned[nid]["estleg:hasNoCompetentAuthority"] = BOOL_TRUE

    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        if not isinstance(nid, str):
            continue
        types = node_types(node)
        directive = is_directive_id(nid) or is_eu_legislation(types)
        if not directive:
            continue
        in_force = json_bool(node.get("estleg:inForce"))
        if in_force is False:
            continue
        if in_force is None and known_force and nid not in known_force:
            continue
        if nid in transposed_targets:
            continue
        planned[nid]["estleg:hasNoTransposition"] = BOOL_TRUE

    return {nid: props for nid, props in planned.items() if props}


def stamp_analytical_properties(
    nodes: list[dict],
    *,
    in_force_directives: set[str] | None = None,
) -> dict[str, int]:
    planned = plan_analytical_updates(
        nodes, in_force_directives=in_force_directives
    )
    updated = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        updates = planned.get(nid) if isinstance(nid, str) else None
        if updates and apply_updates(node, updates):
            updated += 1
    return {"nodes_updated": updated}


def build_analytical_overlay(
    nodes: list[dict],
    *,
    similarity_pairs: list[dict] | None = None,
    in_force_directives: set[str] | None = None,
) -> dict:
    """JSON-LD document: count/flag projections + reified similarity nodes."""
    planned = plan_analytical_updates(
        nodes, in_force_directives=in_force_directives
    )
    known_force = in_force_directives or set()
    transposed: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        if isinstance(nid, str) and node.get("estleg:transposedBy"):
            transposed.add(nid)
        for target in iri_values(node.get("estleg:transposesDirective")):
            transposed.add(target)
    for nid in sorted(known_force):
        if nid not in transposed:
            planned.setdefault(nid, {})["estleg:hasNoTransposition"] = BOOL_TRUE
    graph: list[dict] = [
        {
            "@id": "estleg:AnalyticalOverlay",
            "@type": "owl:Ontology",
            "owl:versionInfo": BUILD_EVALUATION_DATE,
            "rdfs:comment": (
                "Joinable analytical overlay (#521): inboundCitationCount / "
                "interpretationCount / competentAuthorityCount and coverage-gap "
                "booleans on the same IRIs as combined_ontology.jsonld. "
                "Similarity scores are reified estleg:Similarity nodes."
            ),
        }
    ]
    for nid, props in sorted(planned.items()):
        graph.append({"@id": nid, **props})

    pairs = similarity_pairs if similarity_pairs is not None else []
    seen: set[str] = set()
    for pair in pairs:
        source = pair.get("source")
        target = pair.get("target")
        score = pair.get("similarity")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        if not isinstance(score, (int, float)):
            continue
        node = similarity_node(source, target, float(score))
        if node["@id"] in seen:
            continue
        seen.add(node["@id"])
        graph.append(node)

    return {"@context": dict(CONTEXT), "@graph": graph}


def patch_combined_analytical(
    combined_path: Path,
    *,
    in_force_directives: set[str] | None = None,
) -> int:
    nodes = [obj for _, _, obj in iter_combined_objects(combined_path)]
    planned = plan_analytical_updates(
        nodes, in_force_directives=in_force_directives
    )
    return rewrite_combined(combined_path, planned)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write krr_outputs/analytical/analytical_overlay.jsonld.",
    )
    parser.add_argument(
        "--patch-combined",
        action="store_true",
        help="Stamp counts/flags onto combined_ontology.jsonld (no Similarity nodes).",
    )
    parser.add_argument(
        "--combined",
        type=Path,
        default=KRR_DIR / COMBINED_NAME,
        help="Path to combined_ontology.jsonld.",
    )
    parser.add_argument(
        "--overlay",
        type=Path,
        default=OVERLAY_DIR / OVERLAY_NAME,
        help="Overlay output path.",
    )
    parser.add_argument(
        "--max-similarity-pairs",
        type=int,
        default=0,
        help="Cap reified similarity nodes (0 = all pairs in the index).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.write and not args.patch_combined:
        print("specify --write and/or --patch-combined", file=sys.stderr)
        return 2

    nodes: list[dict] = []
    if args.combined.exists():
        nodes = [obj for _, _, obj in iter_combined_objects(args.combined)]
    in_force = load_in_force_directives()
    pairs = load_similarity_pairs()
    if args.max_similarity_pairs > 0:
        pairs = pairs[: args.max_similarity_pairs]

    if args.write:
        doc = build_analytical_overlay(
            nodes, similarity_pairs=pairs, in_force_directives=in_force
        )
        save_json(args.overlay, doc)
        print(
            f"wrote {args.overlay} with {len(doc['@graph'])} nodes "
            f"({len(pairs)} similarity pairs)"
        )

    if args.patch_combined:
        if not args.combined.exists():
            print(f"ERROR: missing {args.combined}", file=sys.stderr)
            return 1
        written = patch_combined_analytical(
            args.combined, in_force_directives=in_force
        )
        print(f"stamped analytical flags on {written} combined nodes (#521)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
