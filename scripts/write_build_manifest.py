#!/usr/bin/env python3
"""Emit ``krr_outputs/dataset_build_manifest.json`` for the committed tree (#548).

Records dataset version, git SHA, pinned generation/evaluation dates, sample
``estleg:kehtiv`` snapshot dates, and catalog counts from ``metadata.jsonld``.
Immutable GitHub Release assets are #473; this file describes the committed
tree, not a tagged download.

    python3 scripts/write_build_manifest.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    ONTOLOGY_VERSION,
    PINNED_RUN_TIMESTAMP,
    REPO_ROOT,
    save_json,
)

DEFAULT_OUTPUT = KRR_DIR / "dataset_build_manifest.json"
METADATA_PATH = REPO_ROOT / "metadata.jsonld"

# High-traffic sample (same acts as the #531 staleness canary).
SAMPLE_PEEPS: tuple[str, ...] = (
    "perekonnaseadus",
    "karistusseadustik_osa1",
    "eesti_vabariigi_pohiseadus",
)

NOTE = (
    "Immutable GitHub Release assets are #473. "
    "This manifest records the committed tree."
)

_ONTOLOGY_OR_ACT_TYPES = frozenset({"owl:Ontology", "estleg:Act"})


def git_sha(repo: Path | None = None) -> str:
    """Return the full HEAD SHA, or ``unknown`` if git is unavailable."""
    root = repo or REPO_ROOT
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"
    sha = (completed.stdout or "").strip()
    if completed.returncode != 0 or not sha:
        return "unknown"
    return sha


def _node_types(node: dict[str, Any]) -> set[str]:
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {item for item in raw if isinstance(item, str)}
    return set()


def _literal_value(raw: Any) -> str | None:
    if isinstance(raw, dict):
        value = raw.get("@value")
        return value if isinstance(value, str) and value else None
    if isinstance(raw, str) and raw:
        return raw
    return None


def extract_ontology_or_act_kehtiv(doc: dict[str, Any]) -> str | None:
    """Return ``estleg:kehtiv`` from an ``owl:Ontology`` / Act node."""
    graph = doc.get("@graph")
    nodes: list[Any]
    if isinstance(graph, list):
        nodes = graph
    else:
        nodes = [doc]
    for node in nodes:
        if not isinstance(node, dict) or "estleg:kehtiv" not in node:
            continue
        if not (_node_types(node) & _ONTOLOGY_OR_ACT_TYPES):
            continue
        value = _literal_value(node["estleg:kehtiv"])
        if value:
            return value
    return None


def sample_kehtiv(krr_dir: Path = KRR_DIR) -> dict[str, str]:
    """Read ``estleg:kehtiv`` from the sample peeps' ontology / Act nodes."""
    out: dict[str, str] = {}
    for slug in SAMPLE_PEEPS:
        path = krr_dir / f"{slug}_peep.json"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            doc = json.load(handle)
        if not isinstance(doc, dict):
            continue
        kehtiv = extract_ontology_or_act_kehtiv(doc)
        if kehtiv:
            out[slug] = kehtiv
    return out


def catalog_counts(metadata_path: Path = METADATA_PATH) -> dict[str, int]:
    """Return catalog counts from ``metadata.jsonld`` when present."""
    counts: dict[str, int] = {}
    if not metadata_path.is_file():
        return counts
    with metadata_path.open(encoding="utf-8") as handle:
        meta = json.load(handle)
    if not isinstance(meta, dict):
        return counts
    stats = meta.get("estleg:statistics")
    if not isinstance(stats, dict):
        return counts
    total = stats.get("estleg:totalFiles")
    enacted = stats.get("estleg:enactedLawCount")
    if isinstance(total, int):
        counts["totalFiles"] = total
    elif isinstance(total, str) and total.isdigit():
        counts["totalFiles"] = int(total)
    if isinstance(enacted, int):
        counts["enactedLaws"] = enacted
    elif isinstance(enacted, str) and enacted.isdigit():
        counts["enactedLaws"] = int(enacted)
    return counts


def generated_timestamp() -> str:
    """ISO-8601 stamp from ``PINNED_RUN_TIMESTAMP``, else UTC now."""
    if PINNED_RUN_TIMESTAMP:
        return PINNED_RUN_TIMESTAMP
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_manifest(
    krr_dir: Path = KRR_DIR,
    metadata_path: Path = METADATA_PATH,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Build the committed-tree dataset manifest (#548)."""
    return {
        "datasetVersion": ONTOLOGY_VERSION,
        "gitSha": git_sha(repo),
        "generated": generated_timestamp(),
        "evaluationDate": BUILD_EVALUATION_DATE,
        "kehtivBySample": sample_kehtiv(krr_dir),
        "counts": catalog_counts(metadata_path),
        "note": NOTE,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination JSON (default: {DEFAULT_OUTPUT}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_manifest()
    save_json(args.output, manifest)
    print(
        f"Wrote {args.output} "
        f"(datasetVersion={manifest['datasetVersion']}, "
        f"gitSha={manifest['gitSha']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
