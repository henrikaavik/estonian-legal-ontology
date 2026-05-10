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
import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
KRR_DIR = REPO_ROOT / "krr_outputs"
MANIFEST_DIR = KRR_DIR / "reports" / "integration"

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the execution plan without running scripts.")
    parser.add_argument("--resume-from", choices=[p[0] for p in PIPELINE], help="Start at this script and skip earlier phases.")
    parser.add_argument(
        "--no-restore-on-failure",
        action="store_true",
        help="Leave partial output changes in place if a later phase fails.",
    )
    parser.add_argument(
        "--validate-each",
        action="store_true",
        help="Run scripts/validate_all.py after each successful phase before continuing.",
    )
    parser.add_argument(
        "--per-script-timeout",
        type=int,
        default=1800,
        help="Maximum seconds to allow each integration script to run.",
    )
    return parser.parse_args()


def snapshot_outputs() -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory(prefix="estleg_integration_")
    backup = Path(tmp.name) / "krr_outputs"
    shutil.copytree(KRR_DIR, backup)
    return tmp


def restore_outputs(snapshot: tempfile.TemporaryDirectory) -> None:
    backup = Path(snapshot.name) / "krr_outputs"
    if not backup.exists():
        return
    if KRR_DIR.exists():
        shutil.rmtree(KRR_DIR)
    shutil.copytree(backup, KRR_DIR)


def run_validator() -> int:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "validate_all.py")],
        cwd=str(REPO_ROOT),
    ).returncode


def write_manifest(manifest: dict) -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / "latest_pipeline_manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")


def script_log_path(script: str) -> Path:
    logs_dir = MANIFEST_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / f"{Path(script).stem}.log"


def main():
    args = parse_args()
    print("=" * 70)
    print("Estonian Legal Ontology — Integration Pipeline")
    print("Scripts run SEQUENTIALLY — do not run them in parallel.")
    if args.dry_run:
        print("DRY-RUN — no scripts will be executed.")
    print("=" * 70)

    failed = set()
    skipped = []
    succeeded = []
    phase_results: list[dict] = []
    restore_on_failure = not args.no_restore_on_failure
    snapshot = None
    if restore_on_failure and not args.dry_run:
        print("\nCreating rollback snapshot of krr_outputs/ ...")
        snapshot = snapshot_outputs()
        print("  Snapshot ready.")

    started = args.resume_from is None

    for i, (script, description, deps) in enumerate(PIPELINE, 1):
        if not started:
            if script == args.resume_from:
                started = True
            else:
                skipped.append(script)
                phase_results.append({
                    "script": script,
                    "status": "skipped_before_resume_point",
                })
                continue

        script_path = SCRIPTS_DIR / script
        if not script_path.exists():
            print(f"\n[{i}/{len(PIPELINE)}] SKIP: {script} (file not found)")
            skipped.append(script)
            phase_results.append({"script": script, "status": "missing"})
            continue

        # Check that all prerequisites succeeded
        blocked_by = deps & failed
        if blocked_by:
            print(f"\n[{i}/{len(PIPELINE)}] SKIP: {script}")
            print(f"  Prerequisite(s) failed: {', '.join(sorted(blocked_by))}")
            skipped.append(script)
            failed.add(script)  # treat as failed so transitive deps are skipped
            phase_results.append({
                "script": script,
                "status": "blocked",
                "blockedBy": sorted(blocked_by),
            })
            continue

        print(f"\n{'=' * 70}")
        print(f"[{i}/{len(PIPELINE)}] {description}")
        print(f"  Script: {script}")
        if deps:
            print(f"  Depends on: {', '.join(sorted(deps))}")
        print("=" * 70)

        if args.dry_run:
            phase_results.append({
                "script": script,
                "status": "planned",
                "description": description,
            })
            continue

        start = time.time()
        log_path = script_log_path(script)
        with open(log_path, "w", encoding="utf-8") as log_file:
            log_file.write(f"# {script} started {datetime.now(timezone.utc).isoformat()}\n")
            try:
                result = subprocess.run(
                    [sys.executable, str(script_path)],
                    cwd=str(REPO_ROOT),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    timeout=args.per_script_timeout,
                )
                exit_code = result.returncode
            except subprocess.TimeoutExpired:
                exit_code = 124
                log_file.write(
                    f"\nTIMEOUT after {args.per_script_timeout} seconds "
                    f"at {datetime.now(timezone.utc).isoformat()}\n"
                )
        elapsed = time.time() - start

        if exit_code != 0:
            print(f"  FAILED (exit code {exit_code}, {elapsed:.1f}s)")
            print(f"  Log: {log_path.relative_to(REPO_ROOT)}")
            failed.add(script)
            phase_results.append({
                "script": script,
                "status": "failed",
                "exitCode": exit_code,
                "elapsedSeconds": round(elapsed, 1),
                "logPath": str(log_path.relative_to(REPO_ROOT)),
            })
            break
        else:
            print(f"  OK ({elapsed:.1f}s)")
            print(f"  Log: {log_path.relative_to(REPO_ROOT)}")
            succeeded.append(script)
            validation_exit = None
            if args.validate_each:
                print("  Running phase validation...")
                validation_exit = run_validator()
                if validation_exit != 0:
                    print(f"  VALIDATION FAILED after {script} (exit code {validation_exit})")
                    failed.add(script)
                    phase_results.append({
                        "script": script,
                        "status": "validation_failed",
                        "exitCode": validation_exit,
                        "elapsedSeconds": round(elapsed, 1),
                        "logPath": str(log_path.relative_to(REPO_ROOT)),
                    })
                    break
            phase_results.append({
                "script": script,
                "status": "succeeded",
                "elapsedSeconds": round(elapsed, 1),
                "validationExitCode": validation_exit,
                "logPath": str(log_path.relative_to(REPO_ROOT)),
            })

    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE")
    if args.dry_run:
        print("DRY-RUN — no scripts were executed.")
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
        if restore_on_failure and snapshot is not None:
            print("  Restoring krr_outputs/ from rollback snapshot...")
            restore_outputs(snapshot)
            print("  Restore complete.")
    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "restoreOnFailure": restore_on_failure,
        "dryRun": args.dry_run,
        "resumeFrom": args.resume_from,
        "summary": {
            "totalScripts": len(PIPELINE),
            "succeeded": len(succeeded),
            "skipped": len(skipped),
            "failed": len(failed),
        },
        "phases": phase_results,
    }
    if not args.dry_run:
        write_manifest(manifest)
    if snapshot is not None:
        snapshot.cleanup()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
