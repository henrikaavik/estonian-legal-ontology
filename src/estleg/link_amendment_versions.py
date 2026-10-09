#!/usr/bin/env python3
"""Re-run the amendment/version join as a verified no-op DAG step (#429, #704, #713).

Since #713 ``generate_amendment_history.main()`` runs the #429 version join
itself (``apply_version_join``): a chain regeneration mints one
``estleg:AmendmentEvent`` per ``versionValidFrom`` date with no RT-derived
event (``…_vf_YYYYMMDD``), stamps ``estleg:resultedInVersion`` on every
event and sets the act root's ``estleg:lastAmendmentDate`` before it saves.

This step re-runs THE SAME function (no second implementation) after the
chains and the provision-version sidecars exist, so it is

* a no-op on a corpus produced by the canonical path (``--check`` exits 1
  if it is not, naming the files that would change), and
* the repair path when only the sidecars were regenerated
  (``generate_provision_versions``) and the chains were not.

It touches only enacted-law chains: for every root law base slug (multipart
``_osaN`` parts grouped by ``base_slug_of``) with a
``provision_versions/<base>.jsonld`` sidecar it joins
``amendments/amendments_<base>.json`` when the chain exists and stamps
``lastAmendmentDate`` on each part's act root. State-regulation sidecars
(#431) are current-snapshot only and are never joined. No chain is created
for a law that has none.

Offline and idempotent. Writes are atomic (``save_json``); ``--dry-run``
reports without writing.

    python3 scripts/link_amendment_versions.py
    python3 scripts/link_amendment_versions.py --dry-run
    python3 scripts/link_amendment_versions.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from estleg.estleg_common import KRR_DIR, save_json
from estleg.generate_amendment_history import (
    PROVISION_VERSIONS_DIRNAME,
    _osa_order_key,
    apply_version_join,
    base_slug_of,
    load_versions_by_date,
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


def _load(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"unreadable amendment/version input: {path}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("@graph"), list):
        raise ValueError(f"{path}: expected an object with an @graph array")
    return doc


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
    """Run the shared #429 join over every enacted law with a version sidecar."""
    stats = JoinStats()
    versions_dir = krr_dir / PROVISION_VERSIONS_DIRNAME
    amendments_dir = krr_dir / "amendments"
    for base, members in sorted(law_groups(krr_dir).items()):
        by_date = load_versions_by_date(versions_dir, base)
        if not by_date:
            continue
        stats.laws_with_versions += 1
        loaded = [(slug, path, _load(path)) for slug, path in members]
        member_docs = [(slug, {"doc": doc}) for slug, _path, doc in loaded if doc is not None]
        paths = {slug: path for slug, path, doc in loaded if doc is not None}

        chain_path = amendments_dir / f"{CHAIN_PREFIX}{base}.json"
        chain = _load(chain_path) if chain_path.is_file() else None
        if chain is None:
            stats.chains_absent += 1
        else:
            stats.chains_seen += 1
        chain_changed, changed_slugs = apply_version_join(chain, member_docs, by_date)
        if chain_changed:
            stats.chains_changed += 1
            stats.changed_files.append(chain_path.name)
            if not dry_run:
                save_json(chain_path, chain)
        docs = dict(member_docs)
        for slug in changed_slugs:
            stats.peeps_changed += 1
            stats.changed_files.append(paths[slug].name)
            if not dry_run:
                save_json(paths[slug], docs[slug]["doc"])
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fixed-point gate: write nothing, exit 1 if the join would change any file.",
    )
    parser.add_argument("--krr-dir", type=Path, default=KRR_DIR)
    args = parser.parse_args(argv)
    read_only = args.dry_run or args.check
    stats = join_version_layer(args.krr_dir, dry_run=read_only)
    print(json.dumps(stats.as_dict(), indent=2))
    for name in stats.changed_files[:20]:
        print(f"  {'would change' if read_only else 'changed'}: {name}")
    if args.check and (stats.chains_changed or stats.peeps_changed):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
