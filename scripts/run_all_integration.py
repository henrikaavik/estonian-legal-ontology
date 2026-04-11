#!/usr/bin/env python3
"""
Master orchestration script for all integration pipelines.

Runs all 14 integration scripts SEQUENTIALLY in strict dependency order.
All scripts modify the same *_peep.json files in krr_outputs/, so concurrent
execution would silently clobber changes.  Never run these scripts in parallel.

Dependency chains (A must complete before B):
  extract_cross_references.py  ->  generate_inverse_references.py
      (writes estleg:references)    (reads references, writes referencedBy)

  generate_transposition_mapping.py  ->  generate_harmonisation_links.py
      (writes transposesDirective)       (reads transposition_mapping.json)

All other scripts are standalone but still modify *_peep.json files, so they
must run one at a time.  generate_similarity_index.py runs last because it
reads the fully-enriched data from all prior steps.

Execution order:
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
    11. extract_institutional_competence.py
    12. extract_sanctions.py
    13. extract_draft_impact.py
  Phase 4 — Aggregation (benefits from all prior data)
    14. generate_similarity_index.py

If a dependency fails, its dependents are automatically skipped.
"""

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# ---------------------------------------------------------------------------
# Pipeline definition
#
# Each entry is (script_filename, description, set_of_prerequisite_filenames).
# A script is skipped when any of its prerequisites failed or was skipped.
# ---------------------------------------------------------------------------

PIPELINE = [
    # -- Phase 1: Cross-references ------------------------------------------
    ("extract_cross_references.py",
     "Cross-law reference extraction",
     set()),
    ("generate_inverse_references.py",
     "Bidirectional reference links",
     {"extract_cross_references.py"}),  # reads estleg:references written by step 1

    # -- Phase 2: EU transposition ------------------------------------------
    ("generate_transposition_mapping.py",
     "EU directive transposition mapping",
     set()),
    ("generate_harmonisation_links.py",
     "Cross-border harmonisation links",
     {"generate_transposition_mapping.py"}),  # reads transposition_mapping.json

    # -- Phase 3: Independent enrichment (no inter-dependencies) -------------
    ("extract_court_provision_links.py",
     "Court decision -> provision links",
     set()),
    ("classify_eurovoc.py",
     "EuroVoc subject classification",
     set()),
    ("extract_temporal_data.py",
     "Temporal validity data",
     set()),
    ("generate_amendment_history.py",
     "Amendment history chains",
     set()),
    ("extract_legal_concepts.py",
     "Legal concept extraction",
     set()),
    ("classify_deontic.py",
     "Deontic classification",
     set()),
    ("extract_institutional_competence.py",
     "Institutional competence mapping",
     set()),
    ("extract_sanctions.py",
     "Sanction extraction",
     set()),
    ("extract_draft_impact.py",
     "Draft impact analysis",
     set()),

    # -- Phase 4: Aggregation (reads fully-enriched data) --------------------
    ("generate_similarity_index.py",
     "Semantic similarity index",
     set()),
]


def main():
    print("=" * 70)
    print("Estonian Legal Ontology — Integration Pipeline")
    print("Scripts run SEQUENTIALLY — do not run them in parallel.")
    print("=" * 70)

    failed = set()
    skipped = []
    succeeded = []

    for i, (script, description, deps) in enumerate(PIPELINE, 1):
        script_path = SCRIPTS_DIR / script
        if not script_path.exists():
            print(f"\n[{i}/{len(PIPELINE)}] SKIP: {script} (file not found)")
            skipped.append(script)
            continue

        # Check that all prerequisites succeeded
        blocked_by = deps & failed
        if blocked_by:
            print(f"\n[{i}/{len(PIPELINE)}] SKIP: {script}")
            print(f"  Prerequisite(s) failed: {', '.join(sorted(blocked_by))}")
            skipped.append(script)
            failed.add(script)  # treat as failed so transitive deps are skipped
            continue

        print(f"\n{'=' * 70}")
        print(f"[{i}/{len(PIPELINE)}] {description}")
        print(f"  Script: {script}")
        if deps:
            print(f"  Depends on: {', '.join(sorted(deps))}")
        print("=" * 70)

        start = time.time()
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(REPO_ROOT),
        )
        elapsed = time.time() - start

        if result.returncode != 0:
            print(f"  FAILED (exit code {result.returncode}, {elapsed:.1f}s)")
            failed.add(script)
        else:
            print(f"  OK ({elapsed:.1f}s)")
            succeeded.append(script)

    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE")
    print("=" * 70)
    print(f"  Total scripts: {len(PIPELINE)}")
    print(f"  Succeeded: {len(succeeded)}")
    print(f"  Skipped:   {len(skipped)}")
    print(f"  Failed:    {len(failed)}")
    if failed:
        print(f"  Failed scripts: {', '.join(sorted(failed))}")
    if skipped:
        print(f"  Skipped scripts: {', '.join(skipped)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
