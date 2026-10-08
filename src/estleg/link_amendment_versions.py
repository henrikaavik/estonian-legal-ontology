#!/usr/bin/env python3
"""Join amendment chains to the provision version layer (#429, #704).

``generate_amendment_history.py`` rebuilds ``amendments/amendments_<law>.json``
from Riigi Teataja amendment markers, but its ``main()`` never runs the #429
version join. The join (``link_amendments_to_versions``) mints one
``estleg:AmendmentEvent`` per ``versionValidFrom`` date that has no RT-derived
event (``…_vf_YYYYMMDD``), stamps ``estleg:resultedInVersion`` on every
event, and ``stamp_last_amendment_from_versions`` sets the act root's
``estleg:lastAmendmentDate`` to the latest version date. Without this step a
chain regeneration silently drops those version-layer events.

This module is the DAG step for that join. It runs after the chains and the
version sidecars exist, and touches only enacted-law chains: for every root
law base slug (multipart ``_osaN`` parts grouped by ``base_slug_of``) with a
``provision_versions/<base>.jsonld`` sidecar it

* joins ``amendments/amendments_<base>.json`` when the chain exists, passing
  the act-root ``estleg:amends`` target exactly as ``relink_version_events``
  resolves it, and
* stamps ``lastAmendmentDate`` on each part's act root.

State-regulation sidecars (``--regulations-riik``, #431) are current-snapshot
only and were never joined, so regulation chains are left alone. No chain is
created for a law that has none.

Offline and idempotent: on the committed corpus the join is a no-op (179
chains, 0 peeps change). Writes are atomic (``save_json``); ``--dry-run``
reports without writing.

    python3 scripts/link_amendment_versions.py
    python3 scripts/link_amendment_versions.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from estleg.estleg_common import KRR_DIR, save_json
from estleg.generate_amendment_history import (
    _osa_order_key,
    act_root_ids_for_members,
    amends_value_from_ids,
    base_slug_of,
    collect_versions_by_date,
    link_amendments_to_versions,
    stamp_last_amendment_from_versions,
)

CHAIN_PREFIX = "amendments_"


@dataclass
class JoinStats:
    laws_with_versions: int = 0
    chains_seen: int = 0
    chains_changed: int = 0
    chains_absent: int = 0
    peeps_changed: int = 0
    changed_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "laws_with_versions": self.laws_with_versions,
            "chains_seen": self.chains_seen,
            "chains_changed": self.chains_changed,
            "chains_absent": self.chains_absent,
            "peeps_changed": self.peeps_changed,
        }


def _load(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def law_groups(krr_dir: Path) -> dict[str, list[tuple[str, Path]]]:
    """Root law peeps grouped by base slug, parts in ``_osaN`` order."""
    groups: dict[str, list[tuple[str, Path]]] = {}
    for path in sorted(krr_dir.glob("*_peep.json")):
        slug = path.stem.removesuffix("_peep")
        groups.setdefault(base_slug_of(slug), []).append((slug, path))
    for members in groups.values():
        members.sort(key=lambda member: _osa_order_key(member[0]))
    return groups


def join_version_layer(krr_dir: Path = KRR_DIR, *, dry_run: bool = False) -> JoinStats:
    """Run the #429 join over every enacted law with a version sidecar."""
    stats = JoinStats()
    versions_dir = krr_dir / "provision_versions"
    amendments_dir = krr_dir / "amendments"
    for base, members in sorted(law_groups(krr_dir).items()):
        sidecar = versions_dir / f"{base}.jsonld"
        if not sidecar.is_file():
            continue
        version_doc = _load(sidecar)
        if version_doc is None:
            continue
        by_date = collect_versions_by_date(version_doc)
        if not by_date:
            continue
        stats.laws_with_versions += 1
        docs = [(path, _load(path)) for _slug, path in members]
        member_docs = [
            (slug, {"doc": doc})
            for (slug, _path), (_p, doc) in zip(members, docs, strict=True)
            if doc is not None
        ]

        chain_path = amendments_dir / f"{CHAIN_PREFIX}{base}.json"
        chain = _load(chain_path) if chain_path.is_file() else None
        if chain is None:
            stats.chains_absent += 1
        else:
            stats.chains_seen += 1
            before = json.dumps(chain, sort_keys=True)
            link_amendments_to_versions(
                chain,
                by_date,
                amends_target=amends_value_from_ids(act_root_ids_for_members(member_docs)),
            )
            if json.dumps(chain, sort_keys=True) != before:
                stats.chains_changed += 1
                stats.changed_files.append(chain_path.name)
                if not dry_run:
                    save_json(chain_path, chain)

        for path, doc in docs:
            if doc is not None and stamp_last_amendment_from_versions(doc, by_date):
                stats.peeps_changed += 1
                stats.changed_files.append(path.name)
                if not dry_run:
                    save_json(path, doc)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    args = parser.parse_args(argv)
    stats = join_version_layer(args.krr_dir, dry_run=args.dry_run)
    print(json.dumps(stats.as_dict(), indent=2))
    for name in stats.changed_files[:20]:
        print(f"  {'would change' if args.dry_run else 'changed'}: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
