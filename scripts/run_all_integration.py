#!/usr/bin/env python3
"""
Master orchestration script for the enrichment pipeline and release builds.

This module owns two things:

1. A declarative **step DAG** for the 15 enrichment scripts. Each step
   declares its ``command``, ``depends_on`` (other step names), ``writes``
   (glob patterns it produces under ``krr_outputs/``) and ``reads`` (glob
   patterns it consumes — either produced by an earlier step or a committed
   input). The runner topologically sorts the DAG and runs in dependency
   order (which is the historical phase order). The DAG is validated at
   startup: no cycles, every ``depends_on`` names a real step, and every
   ``reads`` pattern is either a committed input or covered by some prior
   step's ``writes``.

   All scripts modify the same ``*_peep.json`` files in ``krr_outputs/``,
   so the default execution mode is **serial** (determinism). ``--parallel
   N`` runs steps with no unmet dependency concurrently — but because most
   "independent" steps still rewrite the shared ``*_peep.json`` corpus,
   ``validate_dag()`` *rejects* ``--parallel > 1`` (exit 2) whenever two
   steps that could run concurrently declare overlapping ``writes`` globs;
   the current 15-step DAG has such overlaps, so ``--parallel`` is only
   safe once per-step corpus writes are made disjoint. ``--parallel 1``
   (serial, the default) is unaffected.

2. A **release build**: ``--release`` runs the full DAG, then the three
   release validators (``validate_all.py``, ``shacl_validate_all.py --all``,
   ``validate_seadusloome_sync.py``), then writes
   ``<MANIFEST_DIR>/release_manifest.json`` aggregating the per-step ledger,
   the topo-order used, each validator's pass/fail + headline metrics, a
   content hash of the committed release artifacts, and ``release_ok``.
   ``--release --validate-only`` skips generation and just runs the three
   validators against the current corpus. Exit code is 0 only if
   ``release_ok`` (all steps succeeded AND all validators passed AND no
   release-surface artifact is missing).

Dependency chains (A must complete before B):
  extract_cross_references.py  ->  generate_inverse_references.py
      (writes estleg:references)    (reads references, writes referencedBy)

  generate_transposition_mapping.py  ->  generate_harmonisation_links.py
      (writes transposition_mapping.json) (reads transposition_mapping.json)

All other steps are standalone but still mutate ``*_peep.json``, so under
serial execution they run one at a time. ``generate_similarity_index.py``
runs last because it reads the fully-enriched data from all prior steps.

Execution order (topological — equals the historical phase order):
  Phase 1 — Cross-references
    1.  extract_cross_references.py      (standalone)
    2.  generate_inverse_references.py   (depends on step 1)
  Phase 2 — EU transposition
    3.  generate_transposition_mapping.py (standalone)
    4.  generate_harmonisation_links.py   (depends on step 3)
  Phase 3 — Independent enrichment (no ordering constraints among these)
    5.  extract_court_provision_links.py
    6.  classify_eurovoc.py
    7.  extract_temporal_data.py
    8.  generate_amendment_history.py
    9.  extract_legal_concepts.py
    10. classify_deontic.py
    11. classify_target_group.py
    12. extract_institutional_competence.py
    13. extract_sanctions.py
    14. extract_draft_impact.py
  Phase 4 — Aggregation (benefits from all prior data)
    15. generate_similarity_index.py

If a dependency fails, its dependents are automatically skipped.

See ``docs/RELEASE.md`` for the DAG table, how to run a release build, how
to validate without rebuilding, and the committed-vs-release-asset policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
KRR_DIR = REPO_ROOT / "krr_outputs"
MANIFEST_DIR = KRR_DIR / "reports" / "integration"

# ---------------------------------------------------------------------------
# Step DAG
#
# Each step is a dict:
#   name        — unique step id (the script filename)
#   description — human-readable label
#   command     — argv list to execute (``[sys.executable, "<script path>"]``
#                 is filled in lazily via ``step_command()`` so the literal
#                 stays a pure data structure)
#   script      — the script filename under ``scripts/``
#   args        — optional extra argv appended after the script path (e.g.
#                 ``["--evaluation-date", BUILD_EVALUATION_DATE]``); ignored
#                 when an explicit ``command`` is supplied
#   depends_on  — list of step names that must finish first
#   writes      — glob patterns (relative to ``krr_outputs/``) the step
#                 produces / mutates
#   reads       — glob patterns the step consumes; each must be a committed
#                 input (see ``COMMITTED_INPUTS``) or covered by some prior
#                 step's ``writes``
#
# The historical phase order is preserved by the ``depends_on`` edges plus
# the source order of this list (topological sort is stable over it).
# ---------------------------------------------------------------------------

# Committed inputs every enrichment step is allowed to read without a
# producing step. These are the canonical source files plus the index
# manifests the generators commit. Patterns are relative to ``krr_outputs/``.
COMMITTED_INPUTS: tuple[str, ...] = (
    "*_peep.json",
    "controlled_vocabulary.jsonld",
    "*_owl.jsonld",
    "INDEX.json",
    "regulations/**/*_peep.json",
    "regulations/**/REGULATIONS_*_INDEX.json",
    "eelnoud/*_peep.json",
    "eelnoud/EELNOUD_INDEX.json",
    "riigikohus/*_peep.json",
    "riigikohus/RIIGIKOHUS_INDEX.json",
    "curia/*_peep.json",
    "curia/CURIA_INDEX.json",
    "eurlex/*_peep.json",
    "eurlex/EURLEX_INDEX.json",
)

# ---------------------------------------------------------------------------
# Declared build evaluation date (issue #262).
#
# ``extract_temporal_data.py`` derives ``estleg:temporalStatus`` (and gates
# the repealDate / entryIntoForce writes) relative to an evaluation date.
# Left unset it falls back to ``date.today()``, which makes the committed
# ``*_peep.json`` corpus nondeterministic: any law whose entry/repeal date
# straddles the run date flips status between runs with no underlying data
# change, so two CI runs on different days diff. AGENTS.md forbids exactly
# this kind of timestamp churn / nondeterministic output.
#
# The orchestrator therefore pins a fixed, declared evaluation date and
# passes it explicitly (``--evaluation-date``) to the temporal step. The
# value can be overridden per-run via the ``ESTLEG_BUILD_EVALUATION_DATE``
# environment variable (YYYY-MM-DD) when a release is cut for a specific
# effective date; the default below is the committed-corpus baseline and
# MUST stay in sync with the date the corpus was last regenerated. Keep this
# default identical to ``estleg_common.BUILD_EVALUATION_DATE`` (#295), which
# generators stamp into git-tracked ``generated`` fields for the same
# determinism reason — the two are a matched pair, and drift between them
# desynchronises the corpus from its report/index dates.
BUILD_EVALUATION_DATE: str = os.environ.get(
    "ESTLEG_BUILD_EVALUATION_DATE", "2026-06-01"
)

STEPS: list[dict] = [
    # -- Phase 1: Cross-references ------------------------------------------
    {
        "name": "extract_cross_references.py",
        "description": "Cross-law reference extraction",
        "script": "extract_cross_references.py",
        "depends_on": [],
        "reads": ["*_peep.json", "regulations/**/*_peep.json",
                  "riigikohus/*_peep.json", "eelnoud/*_peep.json"],
        "writes": ["*_peep.json", "regulations/**/*_peep.json",
                   "cross_references_report.json"],
    },
    {
        "name": "generate_inverse_references.py",
        "description": "Bidirectional reference links",
        "script": "generate_inverse_references.py",
        # reads estleg:references written by extract_cross_references.py
        "depends_on": ["extract_cross_references.py"],
        "reads": ["*_peep.json", "regulations/**/*_peep.json",
                  "cross_references_report.json"],
        "writes": ["*_peep.json", "regulations/**/*_peep.json",
                   "inverse_references_report.json"],
    },

    # -- Phase 2: EU transposition -----------------------------------------
    {
        "name": "generate_transposition_mapping.py",
        "description": "EU directive transposition mapping",
        "script": "generate_transposition_mapping.py",
        "depends_on": [],
        "reads": ["*_peep.json", "eurlex/*_peep.json"],
        "writes": ["*_peep.json", "transposition_mapping.json"],
    },
    {
        "name": "generate_harmonisation_links.py",
        "description": "Cross-border harmonisation links",
        "script": "generate_harmonisation_links.py",
        # reads transposition_mapping.json
        "depends_on": ["generate_transposition_mapping.py"],
        "reads": ["transposition_mapping.json", "*_peep.json"],
        "writes": ["*_peep.json", "harmonisation/harmonisation_report.json"],
    },

    # -- Phase 3: Independent enrichment (no inter-dependencies) -----------
    {
        "name": "extract_court_provision_links.py",
        "description": "Court decision -> provision links",
        "script": "extract_court_provision_links.py",
        "depends_on": [],
        "reads": ["*_peep.json", "riigikohus/*_peep.json"],
        "writes": ["riigikohus/*_peep.json", "*_peep.json",
                   "court_provision_links_report.json"],
    },
    {
        "name": "classify_eurovoc.py",
        "description": "EuroVoc subject classification",
        "script": "classify_eurovoc.py",
        "depends_on": [],
        "reads": ["*_peep.json", "regulations/**/*_peep.json"],
        "writes": ["*_peep.json", "regulations/**/*_peep.json",
                   "eurovoc_classification.json"],
    },
    {
        "name": "extract_temporal_data.py",
        "description": "Temporal validity data",
        "script": "extract_temporal_data.py",
        # Pin a declared evaluation date so temporalStatus is deterministic
        # across runs/dates (issue #262); without this the script falls back
        # to date.today() and the committed corpus churns at date boundaries.
        "args": ["--evaluation-date", BUILD_EVALUATION_DATE],
        "depends_on": [],
        "reads": ["*_peep.json", "regulations/**/*_peep.json"],
        "writes": ["*_peep.json", "regulations/**/*_peep.json",
                   "temporal_data_report.json"],
    },
    {
        "name": "generate_amendment_history.py",
        "description": "Amendment history chains",
        "script": "generate_amendment_history.py",
        "depends_on": [],
        "reads": ["*_peep.json", "regulations/**/*_peep.json"],
        "writes": ["amendments/**/*.json", "*_peep.json",
                   "amendment_history_report.json"],
    },
    {
        "name": "extract_legal_concepts.py",
        "description": "Legal concept extraction",
        "script": "extract_legal_concepts.py",
        "depends_on": [],
        "reads": ["*_peep.json", "regulations/**/*_peep.json"],
        "writes": ["concepts/**/*.json", "*_peep.json"],
    },
    {
        "name": "classify_deontic.py",
        "description": "Deontic classification",
        "script": "classify_deontic.py",
        "depends_on": [],
        "reads": ["*_peep.json", "regulations/**/*_peep.json"],
        "writes": ["*_peep.json", "regulations/**/*_peep.json",
                   "deontic_classification_report.json"],
    },
    {
        "name": "classify_target_group.py",
        "description": "Target-group classification",
        "script": "classify_target_group.py",
        "depends_on": [],
        "reads": ["*_peep.json", "regulations/**/*_peep.json"],
        "writes": ["*_peep.json", "regulations/**/*_peep.json",
                   "target_group_report.json"],
    },
    {
        "name": "extract_institutional_competence.py",
        "description": "Institutional competence mapping",
        "script": "extract_institutional_competence.py",
        "depends_on": [],
        "reads": ["*_peep.json", "regulations/**/*_peep.json"],
        "writes": ["institutions/**/*.json", "*_peep.json",
                   "institutional_competence_report.json"],
    },
    {
        "name": "extract_sanctions.py",
        "description": "Sanction extraction",
        "script": "extract_sanctions.py",
        "depends_on": [],
        "reads": ["*_peep.json", "regulations/**/*_peep.json"],
        "writes": ["sanctions/**/*.json", "*_peep.json",
                   "sanctions_report.json"],
    },
    {
        "name": "extract_draft_impact.py",
        "description": "Draft impact analysis",
        "script": "extract_draft_impact.py",
        "depends_on": [],
        "reads": ["*_peep.json", "eelnoud/*_peep.json"],
        "writes": ["*_peep.json", "draft_impact_report.json"],
    },

    # -- Phase 4: Aggregation (reads fully-enriched data) -----------------
    {
        "name": "generate_similarity_index.py",
        "description": "Semantic similarity index",
        "script": "generate_similarity_index.py",
        # logically depends on all prior enrichment having landed; the
        # explicit edges below pin the same total order topo-sort would
        # otherwise leave to source order.
        "depends_on": [
            "extract_cross_references.py",
            "generate_inverse_references.py",
            "generate_transposition_mapping.py",
            "generate_harmonisation_links.py",
            "extract_court_provision_links.py",
            "classify_eurovoc.py",
            "extract_temporal_data.py",
            "generate_amendment_history.py",
            "extract_legal_concepts.py",
            "classify_deontic.py",
            "classify_target_group.py",
            "extract_institutional_competence.py",
            "extract_sanctions.py",
            "extract_draft_impact.py",
        ],
        "reads": ["*_peep.json", "regulations/**/*_peep.json",
                  "eelnoud/*_peep.json", "riigikohus/*_peep.json"],
        # similarity_index.json + report are the provision-level pass; the
        # KOV act-level pass writes one consolidated kov_similarity_index.json
        # plus idempotent back-links into the KOV act nodes themselves.
        "writes": ["similarity_index.json", "similarity_report.json",
                   "similarity/kov_similarity_index.json",
                   "regulations/**/*_peep.json"],
    },

    # -- Phase 5: Combined-ontology rebuild (issue #252) ------------------
    # Every enrichment step above rewrites the source ``*_peep.json`` files.
    # ``combined_ontology.jsonld`` (the most-consumed release artifact and the
    # one the Seadusloome SHACL sync gate parses) and ``INDEX.json`` are
    # *derived* from those peeps by ``scripts/fix_all_issues.py``
    # (``generate_combined_jsonld`` / ``generate_index``) — nothing else
    # builds them. This step MUST run after all enrichment and BEFORE the
    # release validators, otherwise the artifact reflects the pre-enrichment
    # corpus and ``validate_seadusloome_sync`` validates stale data while
    # reporting PASS. ``depends_on`` lists every enrichment step so topo-sort
    # always places this node strictly last; ``main()`` runs the full DAG
    # before invoking the release validators, so the rebuild is guaranteed to
    # precede them and a failure here blocks the validators automatically.
    {
        "name": "fix_all_issues.py",
        "description": "Rebuild combined_ontology.jsonld + INDEX.json",
        "script": "fix_all_issues.py",
        "depends_on": [
            "extract_cross_references.py",
            "generate_inverse_references.py",
            "generate_transposition_mapping.py",
            "generate_harmonisation_links.py",
            "extract_court_provision_links.py",
            "classify_eurovoc.py",
            "extract_temporal_data.py",
            "generate_amendment_history.py",
            "extract_legal_concepts.py",
            "classify_deontic.py",
            "classify_target_group.py",
            "extract_institutional_competence.py",
            "extract_sanctions.py",
            "extract_draft_impact.py",
            "generate_similarity_index.py",
        ],
        # Reads every root *_peep.json + the allowlisted JSON-LD inputs (the
        # canonical combined inputs) and the existing INDEX.json it refreshes.
        "reads": ["*_peep.json", "controlled_vocabulary.jsonld",
                  "*_owl.jsonld", "INDEX.json"],
        "writes": ["combined_ontology.jsonld", "INDEX.json"],
    },
]

# Back-compat alias: external callers (and the historical tests) imported
# ``PIPELINE`` as a list of ``(script, description, deps_set)`` triples.
PIPELINE: list[tuple[str, str, set[str]]] = [
    (s["script"], s["description"], set(s["depends_on"])) for s in STEPS
]

# ---------------------------------------------------------------------------
# Release validators (the three release-gate commands).
# ``args`` is the extra argv beyond ``[python, script]``.
# ``metric_keys`` documents which headline metrics ``parse_validator_output``
# tries to extract from the captured log.
# ---------------------------------------------------------------------------
RELEASE_VALIDATORS: list[dict] = [
    {
        "name": "validate_all",
        "script": "validate_all.py",
        "args": [],
        "description": "Per-file corpus + aggregate parity validation",
    },
    {
        "name": "shacl_validate_all",
        "script": "shacl_validate_all.py",
        "args": ["--all"],
        "description": "Full-corpus SHACL conformance (all buckets)",
    },
    {
        "name": "validate_seadusloome_sync",
        "script": "validate_seadusloome_sync.py",
        "args": [],
        "description": "Seadusloome zero-warning sync gate",
    },
]

# ---------------------------------------------------------------------------
# Committed release artifacts whose content hash goes into the release
# manifest. ``INDEX_GLOBS`` are expanded against ``KRR_DIR`` at hash time.
# ---------------------------------------------------------------------------
RELEASE_ARTIFACTS: tuple[str, ...] = (
    "krr_outputs/combined_ontology.jsonld",
    "krr_outputs/controlled_vocabulary.jsonld",
    "metadata.jsonld",
)
RELEASE_ARTIFACT_INDEX_GLOBS: tuple[str, ...] = (
    "INDEX.json",
    "regulations/**/REGULATIONS_*_INDEX.json",
    "eelnoud/EELNOUD_INDEX.json",
    "riigikohus/RIIGIKOHUS_INDEX.json",
    "curia/CURIA_INDEX.json",
    "eurlex/EURLEX_INDEX.json",
)


# ===========================================================================
# DAG validation + topological sort
# ===========================================================================


class DAGError(ValueError):
    """Raised when the step DAG is structurally invalid."""


def step_command(step: dict) -> list[str]:
    """Resolve the argv list for a step (filled lazily so STEPS stays data).

    An explicit ``command`` wins outright. Otherwise the command is
    ``[sys.executable, <script path>, *args]`` where ``args`` is the
    step's optional declarative extra argv (e.g. ``--evaluation-date``).
    """
    cmd = step.get("command")
    if cmd:
        return list(cmd)
    return [
        sys.executable,
        str(SCRIPTS_DIR / step["script"]),
        *step.get("args", []),
    ]


def validate_dag(
    steps: list[dict],
    committed_inputs: tuple[str, ...],
    parallel: int = 1,
) -> list[str]:
    """Validate the DAG and return the topological order (list of step names).

    Checks, in order:
      * step names are unique
      * every ``depends_on`` entry names a real step
      * the dependency graph is acyclic (Kahn's algorithm)
      * every step's ``reads`` patterns are covered by a committed input or
        by some *prior* step's ``writes`` (prior == earlier in topo order)
      * if ``parallel > 1``: no two steps that could run concurrently (i.e.
        neither is a transitive dependency of the other) declare overlapping
        ``writes`` globs — concurrent writes to a shared corpus target are
        last-writer-wins and would silently clobber enrichments. ``parallel
        <= 1`` (serial — the default) skips this check entirely.

    Raises :class:`DAGError` on the first problem found.
    """
    names = [s["name"] for s in steps]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise DAGError(f"duplicate step name(s): {', '.join(dupes)}")
    name_set = set(names)
    by_name = {s["name"]: s for s in steps}

    # Dangling dependency check.
    for s in steps:
        for dep in s.get("depends_on", []):
            if dep not in name_set:
                raise DAGError(
                    f"step {s['name']!r} depends on unknown step {dep!r}"
                )
            if dep == s["name"]:
                raise DAGError(f"step {s['name']!r} depends on itself")

    # Kahn's algorithm — detect cycles + produce a deterministic topo order.
    # Ties are broken by the source order of ``steps`` so the historical
    # phase order is preserved.
    source_index = {name: i for i, name in enumerate(names)}
    indeg = {name: 0 for name in names}
    adj: dict[str, list[str]] = {name: [] for name in names}
    for s in steps:
        for dep in s.get("depends_on", []):
            adj[dep].append(s["name"])
            indeg[s["name"]] += 1
    ready = sorted((n for n in names if indeg[n] == 0), key=source_index.get)
    topo: list[str] = []
    while ready:
        node = ready.pop(0)
        topo.append(node)
        for nxt in adj[node]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                # Insert keeping source-order determinism.
                ready.append(nxt)
                ready.sort(key=source_index.get)
    if len(topo) != len(names):
        remaining = sorted(set(names) - set(topo))
        raise DAGError(
            f"cycle detected in step DAG involving: {', '.join(remaining)}"
        )

    # reads/writes consistency: walk in topo order, accumulate produced
    # patterns, and require each step's reads to be covered.
    produced: set[str] = set(committed_inputs)
    for name in topo:
        s = by_name[name]
        for pattern in s.get("reads", []):
            if not _pattern_covered(pattern, produced):
                raise DAGError(
                    f"step {name!r} reads {pattern!r} which is neither a "
                    f"committed input nor produced by any prior step"
                )
        produced.update(s.get("writes", []))

    # Concurrency safety: with --parallel, two steps with no dependency
    # relation in either direction can be in flight at the same time. If
    # both write a glob that can match a common path the corpus is at risk
    # of last-writer-wins clobbering.
    if parallel > 1:
        _check_parallel_write_disjointness(steps, adj)
    return topo


def _transitive_dependents(adj: dict[str, list[str]]) -> dict[str, set[str]]:
    """Map each step name -> set of all steps reachable from it via ``adj``.

    ``adj[x]`` lists the immediate successors of ``x`` (steps that declare
    ``x`` in ``depends_on``). The returned set is the *transitive* closure
    of that relation, i.e. every step that runs strictly after ``x`` because
    of a dependency chain. Computed with a simple DFS per node (the DAG is
    tiny — 15 steps).
    """
    closure: dict[str, set[str]] = {}

    def _dfs(node: str) -> set[str]:
        if node in closure:
            return closure[node]
        # Mark in-progress with an empty set to tolerate (already-rejected)
        # cycles without infinite recursion.
        closure[node] = set()
        reachable: set[str] = set()
        for succ in adj.get(node, []):
            reachable.add(succ)
            reachable |= _dfs(succ)
        closure[node] = reachable
        return reachable

    for n in adj:
        _dfs(n)
    return closure


def _globs_can_overlap(a: str, b: str) -> bool:
    """Conservatively decide whether two glob patterns can match a common path.

    Used to detect unsafe concurrent corpus writes. The check errs on the
    side of *overlapping* (false positives are safe — they only forbid an
    unsafe-looking ``--parallel`` config; false negatives would let a
    clobber through):

      * identical patterns overlap;
      * if either pattern has no wildcard, it overlaps the other iff
        ``fnmatch`` matches it against the other (a concrete path under a
        glob, or two identical concrete paths);
      * if both have wildcards, they overlap when their fixed directory
        prefixes are nested or equal (so ``a/**/x`` and ``a/b/*`` overlap),
        OR when both basenames are wildcard patterns that ``fnmatch`` can
        satisfy in either direction (so ``*_peep.json`` overlaps
        ``foo_peep.json`` and two ``**/*_peep.json``-style globs overlap).
      * when in doubt, treat as overlapping.
    """
    if a == b:
        return True
    import fnmatch

    def _has_wild(p: str) -> bool:
        return any(ch in p for ch in "*?[")

    a_wild, b_wild = _has_wild(a), _has_wild(b)
    if not a_wild or not b_wild:
        # At least one is a concrete path: it overlaps iff the glob matches
        # it (fnmatch is exact when both sides lack wildcards).
        return fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)

    # Both are globs. Compare fixed directory prefixes (everything before the
    # first wildcard segment).
    def _fixed_dir(p: str) -> str:
        parts = p.split("/")
        fixed: list[str] = []
        for seg in parts[:-1]:  # the basename segment is handled separately
            if _has_wild(seg):
                break
            fixed.append(seg)
        return "/".join(fixed)

    da, db = _fixed_dir(a), _fixed_dir(b)
    # Directory-prefix containment (treat "" as the repo-root prefix, which
    # contains everything).
    if not (da == db or da == "" or db == "" or da.startswith(db + "/")
            or db.startswith(da + "/")):
        return False
    # Prefixes are compatible — now require the basename patterns to be
    # mutually satisfiable (e.g. ``*_peep.json`` vs ``foo_peep.json``, or two
    # ``*_peep.json``). This is still conservative: ``*.json`` vs ``*.txt``
    # correctly does NOT overlap, while ``*_peep.json`` vs ``*_peep.json``
    # does.
    ba, bb = a.rsplit("/", 1)[-1], b.rsplit("/", 1)[-1]
    return fnmatch.fnmatch(ba, bb) or fnmatch.fnmatch(bb, ba)


def _check_parallel_write_disjointness(
    steps: list[dict], adj: dict[str, list[str]]
) -> None:
    """Reject ``--parallel > 1`` if two concurrent-eligible steps can clobber.

    Two steps are "concurrent-eligible" when neither is a transitive
    dependency of the other (so the runner may dispatch them at the same
    time). For every such pair, if any ``writes`` glob of one can overlap
    (per :func:`_globs_can_overlap`) any ``writes`` glob of the other, the
    DAG is not safe to run in parallel — raise :class:`DAGError` naming the
    two steps and the overlapping write target.
    """
    closure = _transitive_dependents(adj)
    ordered = [s["name"] for s in steps]
    by_name = {s["name"]: s for s in steps}
    for i, a_name in enumerate(ordered):
        a_writes = by_name[a_name].get("writes", []) or []
        if not a_writes:
            continue
        a_deps_closure = closure.get(a_name, set())
        for b_name in ordered[i + 1:]:
            # Skip pairs with a dependency relation in either direction.
            if b_name in a_deps_closure or a_name in closure.get(b_name, set()):
                continue
            b_writes = by_name[b_name].get("writes", []) or []
            for wa in a_writes:
                for wb in b_writes:
                    if _globs_can_overlap(wa, wb):
                        target = wa if wa == wb else f"{wa!r} / {wb!r}"
                        raise DAGError(
                            f"--parallel > 1 is unsafe for this DAG: steps "
                            f"{a_name!r} and {b_name!r} have no dependency "
                            f"relation but both write overlapping corpus "
                            f"target {target} — concurrent execution would "
                            f"clobber enrichments (last-writer-wins). Re-run "
                            f"with --parallel 1 (serial) until per-step "
                            f"corpus writes are disjoint (or an explicit "
                            f"dependency serializes these two steps)."
                        )


def _pattern_covered(pattern: str, produced: set[str]) -> bool:
    """Return True if ``pattern`` is satisfied by the ``produced`` set.

    Coverage is intentionally lenient: an exact match, or a produced
    pattern whose directory prefix subsumes ``pattern`` (e.g. produced
    ``concepts/**/*.json`` covers a read of ``concepts/index.json``), or a
    glob match in either direction (so ``*_peep.json`` covers a specific
    ``foo_peep.json`` and vice versa). This is a *static* sanity gate, not
    a filesystem check.
    """
    if pattern in produced:
        return True
    import fnmatch

    for prod in produced:
        if fnmatch.fnmatch(pattern, prod) or fnmatch.fnmatch(prod, pattern):
            return True
        # Directory-prefix subsumption: ``a/b/**/...`` covers ``a/b/...``.
        prod_dir = prod.split("**", 1)[0].rstrip("/")
        pat_dir = pattern.split("**", 1)[0].rstrip("/")
        if prod_dir and pat_dir and (
            pat_dir == prod_dir
            or pat_dir.startswith(prod_dir + "/")
            or prod_dir.startswith(pat_dir + "/")
        ):
            # Only treat as covered when both reference the same top-level
            # area; the leniency here is bounded and explicit.
            if pat_dir.split("/", 1)[0] == prod_dir.split("/", 1)[0]:
                return True
    return False


# ===========================================================================
# CLI
# ===========================================================================


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the execution plan (DAG topo-order, and in --release the "
        "validators that would run) without running anything.",
    )
    parser.add_argument(
        "--resume-from",
        choices=[s["name"] for s in STEPS],
        help="Start at this step and skip earlier steps in topo order.",
    )
    parser.add_argument(
        "--no-restore-on-failure",
        action="store_true",
        help="Leave partial output changes in place if a later step fails.",
    )
    parser.add_argument(
        "--validate-each",
        action="store_true",
        help="Run scripts/validate_all.py after each successful step.",
    )
    parser.add_argument(
        "--per-script-timeout",
        type=int,
        default=1800,
        help="Maximum seconds to allow each step (and validator) to run.",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        metavar="N",
        default=1,
        help="Run up to N steps with no unmet dependency concurrently "
        "(default 1 = serial; serial is the deterministic default). "
        "N > 1 is REJECTED (exit 2) when two steps that could run "
        "concurrently both write overlapping corpus targets (e.g. the "
        "shared *_peep.json files) — concurrent writes would clobber "
        "enrichments; the current DAG has such overlaps, so --parallel is "
        "only safe once per-step corpus writes are made disjoint.",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="Release build: run the full DAG, then the three release "
        "validators, then write release_manifest.json. Exit 0 only if "
        "release_ok (all steps + all validators passed).",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="With --release: skip the generation steps and just run the "
        "three release validators against the current corpus, then write "
        "the validator section of release_manifest.json.",
    )
    return parser.parse_args(argv)


# ===========================================================================
# Snapshot / restore (unchanged semantics — atomic rename-aside)
# ===========================================================================


def snapshot_outputs() -> Path:
    """Take a sibling-directory backup of KRR_DIR using rename-aside semantics.

    The atomic anchor is the rename of the current KRR_DIR to a sibling
    ``<KRR_DIR>.bak.<pid>`` path; on the same filesystem this is an atomic
    directory move. After renaming, we copytree the backup back into
    KRR_DIR so the run can mutate the live tree while the original is
    preserved untouched at the backup path.

    Invariant: at any SIGINT point, EITHER the original tree exists at
    KRR_DIR OR it exists at the returned backup path. It is never both
    gone.
    """
    backup = KRR_DIR.parent / f"{KRR_DIR.name}.bak.{os.getpid()}"
    if backup.exists():
        shutil.rmtree(backup)
    # Atomic rename-aside (same filesystem, sibling directory).
    shutil.move(str(KRR_DIR), str(backup))
    # Re-create KRR_DIR by copying from the backup so the run has a
    # writable tree while the original is preserved at `backup`.
    shutil.copytree(backup, KRR_DIR)
    return backup


def restore_outputs(backup: Path) -> None:
    """Atomically restore KRR_DIR from the rename-aside backup.

    Strategy: move the (potentially partial) KRR_DIR aside before
    renaming the backup back into place. If the second rename fails we
    roll back the rollback so the partial run is recoverable. On
    success the partial-run sibling is rmtreed.
    """
    if not backup.exists():
        return
    if KRR_DIR.exists():
        failed_run = KRR_DIR.parent / f"{KRR_DIR.name}.failed.{os.getpid()}"
        if failed_run.exists():
            shutil.rmtree(failed_run)
        shutil.move(str(KRR_DIR), str(failed_run))
        try:
            shutil.move(str(backup), str(KRR_DIR))
        except Exception:
            # Roll back the rollback so the user retains *something* at
            # the canonical path.
            shutil.move(str(failed_run), str(KRR_DIR))
            raise
        else:
            shutil.rmtree(failed_run, ignore_errors=True)
    else:
        shutil.move(str(backup), str(KRR_DIR))


def cleanup_snapshot(backup: Path) -> None:
    """Remove a rename-aside snapshot after a successful run."""
    if backup is None:
        return
    shutil.rmtree(backup, ignore_errors=True)


# ===========================================================================
# Logging helpers
# ===========================================================================


def _logs_dir() -> Path:
    logs_dir = MANIFEST_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def validator_log_path(stem: str) -> Path:
    return _logs_dir() / f"validate_{stem}.log"


def script_log_path(script: str) -> Path:
    return _logs_dir() / f"{Path(script).stem}.log"


def _rel(path: Path) -> str:
    """Best-effort path relative to REPO_ROOT (falls back to abs string)."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ===========================================================================
# Subprocess runners
# ===========================================================================


def _run_logged(cmd: list[str], log_path: Path, timeout: int, banner: str) -> int:
    """Run ``cmd`` capturing stdout+stderr into ``log_path``.

    Returns the exit code; a timeout surfaces as 124 (the project-wide
    convention).
    """
    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write(f"# {banner} {datetime.now(timezone.utc).isoformat()}\n")
        log_file.flush()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            return result.returncode
        except subprocess.TimeoutExpired:
            log_file.write(
                f"\nTIMEOUT after {timeout} seconds "
                f"at {datetime.now(timezone.utc).isoformat()}\n"
            )
            return 124


def run_validator(timeout: int, after_script: str | None = None) -> tuple[int, Path]:
    """Run ``scripts/validate_all.py`` with timeout + tee'd output.

    Returns (exit_code, log_path). Kept for back-compat with ``--validate-each``
    and the existing tests; the release path uses :func:`run_release_validator`.
    """
    stem = Path(after_script).stem if after_script else "phase"
    log_path = validator_log_path(stem)
    exit_code = _run_logged(
        [sys.executable, str(SCRIPTS_DIR / "validate_all.py")],
        log_path,
        timeout,
        f"validate_all.py started (after={after_script or '<none>'})",
    )
    return exit_code, log_path


def run_release_validator(spec: dict, timeout: int) -> dict:
    """Run one release validator and return its result record.

    The record has: ``name``, ``command``, ``status`` (``passed`` /
    ``failed`` / ``timeout``), ``exitCode``, ``elapsedSeconds``,
    ``logPath``, and ``metrics`` (headline numbers parsed from the log).
    """
    cmd = [sys.executable, str(SCRIPTS_DIR / spec["script"]), *spec.get("args", [])]
    log_path = validator_log_path(spec["name"])
    start = time.time()
    exit_code = _run_logged(
        cmd, log_path, timeout, f"{spec['script']} {' '.join(spec.get('args', []))} started"
    )
    elapsed = time.time() - start
    status = "timeout" if exit_code == 124 else ("passed" if exit_code == 0 else "failed")
    metrics = parse_validator_output(spec["name"], log_path)
    return {
        "name": spec["name"],
        "description": spec.get("description", ""),
        "command": cmd,
        "status": status,
        "passed": exit_code == 0,
        "exitCode": exit_code,
        "elapsedSeconds": round(elapsed, 2),
        "logPath": _rel(log_path),
        "metrics": metrics,
    }


def parse_validator_output(name: str, log_path: Path) -> dict:
    """Extract headline metrics from a validator's captured log.

    Best-effort regex scraping — never raises. Recognised metrics:
      validate_all:               filesValidated, errors, warnings
      shacl_validate_all:         filesLoaded, triples, shaclConforms
      validate_seadusloome_sync:  dataTriples, shapeTriples, shaclConforms,
                                  shaclTotalResults, violations, warnings
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    metrics: dict = {}

    def _int(pattern: str) -> int | None:
        m = re.search(pattern, text)
        return int(m.group(1)) if m else None

    if name == "validate_all":
        for key, pat in (
            ("filesValidated", r"Files validated:\s*(\d+)"),
            ("errors", r"Errors:\s*(\d+)"),
            ("warnings", r"Warnings:\s*(\d+)"),
        ):
            val = _int(pat)
            if val is not None:
                metrics[key] = val
    elif name == "shacl_validate_all":
        val = _int(r"Loading\s+(\d+)\s+files? from")
        if val is not None:
            metrics["filesLoaded"] = val
        val = _int(r"Loaded\s+(\d+)\s+triples")
        if val is not None:
            metrics["triples"] = val
        m = re.search(r"SHACL\s+(PASS|FAIL)", text)
        if m:
            metrics["shaclConforms"] = m.group(1) == "PASS"
    elif name == "validate_seadusloome_sync":
        m = re.search(r"Loaded\s+(\d+)\s+data triples;\s+(\d+)\s+shape triples", text)
        if m:
            metrics["dataTriples"] = int(m.group(1))
            metrics["shapeTriples"] = int(m.group(2))
        m = re.search(r"SHACL conformity:\s*(PASS|FAIL)", text)
        if m:
            metrics["shaclConforms"] = m.group(1) == "PASS"
        # "Severity totals: Violation=N, Warning=M" (parts optional).
        m = re.search(r"Violation=(\d+)", text)
        if m:
            metrics["violations"] = int(m.group(1))
        m = re.search(r"Warning=(\d+)", text)
        if m:
            metrics["warnings"] = int(m.group(1))
    return metrics


# ===========================================================================
# Release artifact hashing
# ===========================================================================


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_release_artifacts() -> dict:
    """Compute a content hash over the committed release artifacts.

    Returns a dict with:
      ``files``      — {repo-relative path: sha256} for every artifact found
      ``missing``    — list of expected artifacts that were not found
      ``contentHash``— sha256 over ``"<path>\\n<sha256>\\n"`` lines sorted by
                       path (a stable digest of the whole release surface)
    """
    found: dict[str, str] = {}
    missing: list[str] = []
    for rel in RELEASE_ARTIFACTS:
        p = REPO_ROOT / rel
        if p.is_file():
            found[rel] = _sha256_file(p)
        else:
            missing.append(rel)
    for pattern in RELEASE_ARTIFACT_INDEX_GLOBS:
        matches = sorted(KRR_DIR.glob(pattern))
        if not matches:
            missing.append(f"krr_outputs/{pattern}")
            continue
        for m in matches:
            found[_rel(m)] = _sha256_file(m)
    digest = hashlib.sha256()
    for rel in sorted(found):
        digest.update(f"{rel}\n{found[rel]}\n".encode("utf-8"))
    return {
        "files": dict(sorted(found.items())),
        "missing": sorted(set(missing)),
        "contentHash": digest.hexdigest(),
    }


# ===========================================================================
# Manifest writers
# ===========================================================================


def write_manifest(manifest: dict) -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / "latest_pipeline_manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_release_manifest(manifest: dict) -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / "release_manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


# ===========================================================================
# Step execution
# ===========================================================================


def _print_banner(lines: list[str]) -> None:
    print("=" * 70)
    for line in lines:
        print(line)
    print("=" * 70)


def _run_one_step(step: dict, timeout: int) -> dict:
    """Execute a single step subprocess; return its ledger record."""
    script = step["script"]
    log_path = script_log_path(script)
    start = time.time()
    exit_code = _run_logged(
        step_command(step),
        log_path,
        timeout,
        f"{script} started",
    )
    elapsed = time.time() - start
    rec = {
        "name": step["name"],
        "script": script,
        "description": step["description"],
        "dependsOn": list(step.get("depends_on", [])),
        "command": step_command(step),
        "elapsedSeconds": round(elapsed, 2),
        "logPath": _rel(log_path),
        "exitCode": exit_code,
    }
    if exit_code == 0:
        rec["status"] = "succeeded"
    elif exit_code == 124:
        rec["status"] = "timeout"
    else:
        rec["status"] = "failed"
    return rec


def run_dag(
    steps: list[dict],
    topo: list[str],
    *,
    dry_run: bool,
    resume_from: str | None,
    validate_each: bool,
    per_script_timeout: int,
    parallel: int,
) -> dict:
    """Execute the DAG in topo order (serial) or with bounded parallelism.

    Returns a dict: ``{"ledger": [...], "succeeded": set, "failed": set,
    "skipped": set, "planned": [...], "topoOrder": [...]}``.

    ``parallel > 1`` runs steps whose dependencies have all succeeded
    concurrently (best-effort). Serial (``parallel <= 1``) is the
    deterministic default and preserves the historical behaviour exactly.
    """
    by_name = {s["name"]: s for s in steps}
    total = len(topo)
    ledger: list[dict] = []
    succeeded: set[str] = set()
    failed: set[str] = set()
    skipped: set[str] = set()
    planned: list[str] = []

    # Honour --resume-from: everything strictly before it in topo order is
    # marked skipped-before-resume and treated as if it had succeeded so
    # dependents are not blocked.
    resume_reached = resume_from is None
    pre_resume: set[str] = set()
    if resume_from is not None:
        for name in topo:
            if name == resume_from:
                break
            pre_resume.add(name)

    def _missing_script(name: str) -> bool:
        return not (SCRIPTS_DIR / by_name[name]["script"]).exists()

    if parallel <= 1 or dry_run:
        # ---- Serial path (the deterministic default) --------------------
        for i, name in enumerate(topo, 1):
            step = by_name[name]
            if name in pre_resume:
                skipped.add(name)
                succeeded.add(name)  # don't block dependents
                ledger.append({"name": name, "script": step["script"],
                               "status": "skipped_before_resume_point"})
                continue
            if not resume_reached:
                resume_reached = name == resume_from
            if _missing_script(name):
                print(f"\n[{i}/{total}] SKIP: {name} (file not found)")
                skipped.add(name)
                failed.add(name)
                ledger.append({"name": name, "script": step["script"],
                               "status": "missing"})
                continue
            blocked_by = set(step.get("depends_on", [])) & failed
            if blocked_by:
                print(f"\n[{i}/{total}] SKIP: {name}")
                print(f"  Prerequisite(s) failed: {', '.join(sorted(blocked_by))}")
                skipped.add(name)
                failed.add(name)  # transitive skip
                ledger.append({"name": name, "script": step["script"],
                               "status": "blocked",
                               "blockedBy": sorted(blocked_by)})
                continue
            _print_banner([
                f"[{i}/{total}] {step['description']}",
                f"  Step: {name}",
                *([f"  Depends on: {', '.join(step['depends_on'])}"]
                  if step.get("depends_on") else []),
            ])
            if dry_run:
                planned.append(name)
                ledger.append({"name": name, "script": step["script"],
                               "status": "planned",
                               "description": step["description"]})
                continue
            rec = _run_one_step(step, per_script_timeout)
            ledger.append(rec)
            if rec["status"] != "succeeded":
                print(f"  FAILED (exit code {rec['exitCode']}, "
                      f"{rec['elapsedSeconds']:.1f}s)")
                print(f"  Log: {rec['logPath']}")
                failed.add(name)
                break
            print(f"  OK ({rec['elapsedSeconds']:.1f}s)")
            print(f"  Log: {rec['logPath']}")
            succeeded.add(name)
            if validate_each:
                print("  Running phase validation...")
                v_exit, v_log = run_validator(per_script_timeout, after_script=name)
                rec["validationExitCode"] = v_exit
                rec["validationLogPath"] = _rel(v_log)
                print(f"  Validation log: {rec['validationLogPath']}")
                if v_exit != 0:
                    print(f"  VALIDATION FAILED after {name} (exit code {v_exit})")
                    rec["status"] = "validation_failed"
                    failed.add(name)
                    break
        return {"ledger": ledger, "succeeded": succeeded, "failed": failed,
                "skipped": skipped, "planned": planned, "topoOrder": topo}

    # ---- Parallel path (best-effort) ------------------------------------
    # validate_each is incompatible with --parallel (validator reads the
    # whole corpus mid-flight); callers are warned at the call site.
    remaining = [n for n in topo if n not in pre_resume]
    for n in pre_resume:
        skipped.add(n)
        succeeded.add(n)
        ledger.append({"name": n, "script": by_name[n]["script"],
                       "status": "skipped_before_resume_point"})
    done: set[str] = set(succeeded)
    idx_by_name = {n: i for i, n in enumerate(topo, 1)}
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures: dict = {}
        stop_dispatch = False
        while remaining or futures:
            # Dispatch every step whose deps are all done (and not failed).
            progressed = True
            while progressed and not stop_dispatch:
                progressed = False
                for name in list(remaining):
                    step = by_name[name]
                    deps = set(step.get("depends_on", []))
                    if deps & failed:
                        remaining.remove(name)
                        skipped.add(name)
                        failed.add(name)
                        ledger.append({"name": name, "script": step["script"],
                                       "status": "blocked",
                                       "blockedBy": sorted(deps & failed)})
                        progressed = True
                        continue
                    if not deps.issubset(done):
                        continue
                    if _missing_script(name):
                        remaining.remove(name)
                        skipped.add(name)
                        failed.add(name)
                        ledger.append({"name": name, "script": step["script"],
                                       "status": "missing"})
                        progressed = True
                        continue
                    if len(futures) >= parallel:
                        break
                    remaining.remove(name)
                    print(f"[{idx_by_name[name]}/{total}] START {name} "
                          f"({step['description']})")
                    futures[pool.submit(
                        _run_one_step, step, per_script_timeout)] = name
                    progressed = True
            if not futures:
                break
            completed, _ = wait(futures, return_when=FIRST_COMPLETED)
            for fut in completed:
                name = futures.pop(fut)
                rec = fut.result()
                ledger.append(rec)
                if rec["status"] == "succeeded":
                    succeeded.add(name)
                    done.add(name)
                    print(f"[{idx_by_name[name]}/{total}] OK {name} "
                          f"({rec['elapsedSeconds']:.1f}s)")
                else:
                    failed.add(name)
                    print(f"[{idx_by_name[name]}/{total}] FAILED {name} "
                          f"(exit {rec['exitCode']}) — log {rec['logPath']}")
                    stop_dispatch = True
            if stop_dispatch and not futures:
                # Drain: anything still pending is skipped.
                for name in remaining:
                    skipped.add(name)
                    failed.add(name)
                    ledger.append({"name": name, "script": by_name[name]["script"],
                                   "status": "blocked",
                                   "blockedBy": sorted(failed)})
                remaining = []
    return {"ledger": ledger, "succeeded": succeeded, "failed": failed,
            "skipped": skipped, "planned": planned, "topoOrder": topo}


# ===========================================================================
# Release orchestration
# ===========================================================================


def run_release_validators(timeout: int, *, dry_run: bool) -> tuple[list[dict], bool]:
    """Run (or, in dry-run, just describe) the three release validators.

    Returns ``(records, all_passed)``. In dry-run nothing executes, records
    carry ``status == "planned"`` and ``all_passed`` is True (nothing to
    fail).
    """
    records: list[dict] = []
    all_passed = True
    for spec in RELEASE_VALIDATORS:
        cmd = [sys.executable, str(SCRIPTS_DIR / spec["script"]), *spec.get("args", [])]
        human_cmd = " ".join(["python3", spec["script"], *spec.get("args", [])])
        if dry_run:
            print(f"  [DRY-RUN] would run: {human_cmd}")
            records.append({
                "name": spec["name"],
                "description": spec.get("description", ""),
                "command": cmd,
                "status": "planned",
                "passed": None,
            })
            continue
        print(f"\n--- release validator: {spec['name']} ({human_cmd}) ---")
        rec = run_release_validator(spec, timeout)
        print(f"  {rec['status'].upper()} (exit {rec['exitCode']}, "
              f"{rec['elapsedSeconds']:.1f}s) — log {rec['logPath']}")
        if rec.get("metrics"):
            print(f"  metrics: {rec['metrics']}")
        records.append(rec)
        if not rec["passed"]:
            all_passed = False
    return records, all_passed


def build_release_manifest(
    *,
    dag_result: dict | None,
    validator_records: list[dict],
    validators_passed: bool,
    artifact_hash: dict,
    dry_run: bool,
    validate_only: bool,
    resume_from: str | None,
    parallel: int,
) -> dict:
    """Assemble the release_manifest.json payload."""
    steps_ok = True
    step_ledger: list[dict] = []
    topo_order: list[str] = [s["name"] for s in STEPS]
    if dag_result is not None:
        step_ledger = dag_result["ledger"]
        topo_order = dag_result.get("topoOrder", topo_order)
        steps_ok = not dag_result["failed"]
    summary = {
        "totalSteps": len(STEPS),
        "succeeded": len(dag_result["succeeded"]) if dag_result else 0,
        "failed": len(dag_result["failed"]) if dag_result else 0,
        "skipped": len(dag_result["skipped"]) if dag_result else 0,
        "planned": len(dag_result["planned"]) if dag_result else 0,
    }
    # In validate-only mode there are no generation steps, so step success
    # is vacuously true; release_ok then turns purely on the validators.
    if validate_only:
        steps_ok = True
    # The release surface must be complete: every artifact in
    # RELEASE_ARTIFACTS (+ the index globs) has to be present on disk.
    # ``validate_all.py`` only *warns* on a missing metadata.jsonld, so this
    # is the gate that actually fails a release whose release-surface
    # artifacts are absent. The ``missing`` list is always recorded in the
    # manifest under ``releaseArtifacts`` so a False verdict is diagnosable.
    artifacts_complete = not bool(artifact_hash.get("missing"))
    # A dry-run executed nothing, so release_ok is undefined (null) rather
    # than False — there is no failure, just no result.
    release_ok: bool | None = (
        None if dry_run
        else bool(steps_ok and validators_passed and artifacts_complete)
    )
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mode": ("release-validate-only" if validate_only else "release-build"),
        "dryRun": dry_run,
        "resumeFrom": resume_from,
        "parallel": parallel,
        "dag": {
            "topoOrder": topo_order,
            "steps": [
                {
                    "name": s["name"],
                    "dependsOn": list(s.get("depends_on", [])),
                    "writes": list(s.get("writes", [])),
                    "reads": list(s.get("reads", [])),
                }
                for s in STEPS
            ],
        },
        "stepLedger": step_ledger,
        "stepSummary": summary,
        "validators": validator_records,
        "validatorsPassed": validators_passed,
        "releaseArtifacts": artifact_hash,
        "releaseOk": release_ok,
    }


# ===========================================================================
# main
# ===========================================================================


def _print_dag_plan(topo: list[str], *, release: bool, validate_only: bool) -> None:
    by_name = {s["name"]: s for s in STEPS}
    print("\nStep DAG (topological order):")
    if validate_only:
        print("  (none — --validate-only skips all generation steps)")
    else:
        for i, name in enumerate(topo, 1):
            deps = by_name[name].get("depends_on", [])
            dep_str = f"  <- {', '.join(deps)}" if deps else ""
            print(f"  {i:2d}. {name}{dep_str}")
    if release:
        print("\nRelease validators that would run:")
        for spec in RELEASE_VALIDATORS:
            argv = " ".join(["python3", spec["script"], *spec.get("args", [])])
            print(f"  - {argv}  ({spec['description']})")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Validate the DAG before doing anything else. With --parallel > 1 this
    # also rejects DAGs where two concurrent-eligible steps would clobber a
    # shared corpus write target.
    try:
        topo = validate_dag(STEPS, COMMITTED_INPUTS, parallel=args.parallel)
    except DAGError as exc:
        print(f"FATAL: invalid step DAG: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.validate_only and not args.release:
        print("FATAL: --validate-only requires --release.", file=sys.stderr)
        sys.exit(2)
    if args.parallel > 1 and args.validate_each:
        print("FATAL: --parallel is incompatible with --validate-each.",
              file=sys.stderr)
        sys.exit(2)

    _print_banner([
        "Estonian Legal Ontology — Integration / Release Pipeline",
        ("Release build" if args.release else "Enrichment pipeline")
        + (" (validate-only)" if args.validate_only else "")
        + (f" — up to {args.parallel} steps in parallel"
           if args.parallel > 1 else " — serial execution"),
        *(["[DRY-RUN] No scripts will be executed."] if args.dry_run else []),
    ])

    if args.dry_run:
        _print_dag_plan(topo, release=args.release,
                        validate_only=args.validate_only)

    # ---- Release: validate-only ----------------------------------------
    if args.release and args.validate_only:
        validator_records, validators_passed = run_release_validators(
            args.per_script_timeout, dry_run=args.dry_run)
        artifact_hash = hash_release_artifacts() if not args.dry_run else {
            "files": {}, "missing": [], "contentHash": None}
        manifest = build_release_manifest(
            dag_result=None,
            validator_records=validator_records,
            validators_passed=validators_passed,
            artifact_hash=artifact_hash,
            dry_run=args.dry_run,
            validate_only=True,
            resume_from=None,
            parallel=args.parallel,
        )
        if not args.dry_run:
            path = write_release_manifest(manifest)
            print(f"\nWrote release manifest: {_rel(path)}")
        _print_banner([
            ("[DRY-RUN] " if args.dry_run else "") + "RELEASE VALIDATION COMPLETE",
            f"  releaseOk: {manifest['releaseOk']}",
        ])
        # A dry-run is a successful preview — never fail on it.
        sys.exit(0 if (args.dry_run or manifest["releaseOk"]) else 1)

    # ---- Pipeline / release build --------------------------------------
    restore_on_failure = not args.no_restore_on_failure
    snapshot: Path | None = None
    if restore_on_failure and not args.dry_run:
        print("\nCreating rollback snapshot of krr_outputs/ ...")
        snapshot = snapshot_outputs()
        print(f"  Snapshot ready at {snapshot}.")

    dag_result = run_dag(
        STEPS,
        topo,
        dry_run=args.dry_run,
        resume_from=args.resume_from,
        validate_each=args.validate_each,
        per_script_timeout=args.per_script_timeout,
        parallel=args.parallel,
    )

    failed = dag_result["failed"]
    succeeded = dag_result["succeeded"]
    skipped = dag_result["skipped"]
    planned = dag_result["planned"]

    _print_banner([
        ("[DRY-RUN] PIPELINE PLAN COMPLETE" if args.dry_run else "PIPELINE COMPLETE"),
    ])
    print(f"  Total steps: {len(STEPS)}")
    if args.dry_run:
        print(f"  Planned:   {len(planned)}")
        print(f"  Skipped:   {len(skipped)}")
    else:
        print(f"  Succeeded: {len(succeeded - set(s for s in skipped))}")
        print(f"  Skipped:   {len(skipped)}")
        print(f"  Failed:    {len(failed)}")
    if failed:
        print(f"  Failed steps: {', '.join(sorted(failed))}")
    if skipped:
        print(f"  Skipped steps: {', '.join(sorted(skipped))}")
    if args.dry_run:
        print("[DRY-RUN] No scripts were executed.")

    # Roll back on failure (pipeline mode parity with the historical script).
    if failed and not args.dry_run:
        if restore_on_failure and snapshot is not None:
            print("  Restoring krr_outputs/ from rollback snapshot...")
            restore_outputs(snapshot)
            print("  Restore complete.")
            snapshot = None

    # ---- Release build: run the three validators + write release manifest
    if args.release:
        if args.dry_run:
            validator_records, validators_passed = run_release_validators(
                args.per_script_timeout, dry_run=True)
            artifact_hash = {"files": {}, "missing": [], "contentHash": None}
        elif failed:
            # Generation failed and was rolled back — don't run release
            # validators against a half-restored tree; record them as
            # skipped and let release_ok be False.
            print("\nSkipping release validators — generation steps failed.")
            validator_records = [
                {"name": spec["name"], "description": spec.get("description", ""),
                 "command": [sys.executable, str(SCRIPTS_DIR / spec["script"]),
                             *spec.get("args", [])],
                 "status": "skipped_steps_failed", "passed": False}
                for spec in RELEASE_VALIDATORS
            ]
            validators_passed = False
            artifact_hash = hash_release_artifacts()
        else:
            validator_records, validators_passed = run_release_validators(
                args.per_script_timeout, dry_run=False)
            artifact_hash = hash_release_artifacts()
        manifest = build_release_manifest(
            dag_result=dag_result,
            validator_records=validator_records,
            validators_passed=validators_passed,
            artifact_hash=artifact_hash,
            dry_run=args.dry_run,
            validate_only=False,
            resume_from=args.resume_from,
            parallel=args.parallel,
        )
        if not args.dry_run:
            path = write_release_manifest(manifest)
            print(f"\nWrote release manifest: {_rel(path)}")
        _print_banner([
            ("[DRY-RUN] " if args.dry_run else "") + "RELEASE BUILD COMPLETE",
            f"  releaseOk: {manifest['releaseOk']}",
        ])
        if snapshot is not None:
            cleanup_snapshot(snapshot)
        # A dry-run is a successful preview — never fail on it.
        sys.exit(0 if (args.dry_run or manifest["releaseOk"]) else 1)

    # ---- Plain enrichment pipeline: write the per-run manifest ----------
    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "restoreOnFailure": restore_on_failure,
        "dryRun": args.dry_run,
        "resumeFrom": args.resume_from,
        "parallel": args.parallel,
        "topoOrder": dag_result.get("topoOrder", [s["name"] for s in STEPS]),
        "summary": {
            "totalScripts": len(STEPS),
            "succeeded": len(succeeded - set(skipped)),
            "planned": len(planned),
            "skipped": len(skipped),
            "failed": len(failed),
        },
        "phases": dag_result["ledger"],
    }
    if not args.dry_run:
        write_manifest(manifest)
    if snapshot is not None:
        cleanup_snapshot(snapshot)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
