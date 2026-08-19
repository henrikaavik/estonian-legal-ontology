#!/usr/bin/env python3
"""Stamp ``estleg:officialEnglishText`` from RT ``tolkeSeosId`` (#510).

Riigi Teataja publishes official English consolidations. The English ELI
slug is **not** the current Estonian ``/akt/{id}.xml`` globaalID — it is
the translation's own id, returned as ``tolkeSeosId`` on
``GET https://www.riigiteataja.ee/api/v1/akt/{id}``. English ids fall in
the ``5xxxxxxxxxxx`` band the RT UI uses to route ``/en/eli/{id}``.

This script:

* walks law peeps (``iter_peep_files(include_regulations=False)``)
* reads the akt id from ``dcterms:source``
* looks up / fetches ``tolkeSeosId``
* writes ``estleg:officialEnglishText`` →
  ``https://www.riigiteataja.ee/en/eli/{tolkeSeosId}``
* caches the mapping in ``data/riigiteataja/english_eli.json`` so later
  remints are offline

DRY-RUN is the default. Pass ``--apply`` to write. ``--fetch`` contacts
RT for akt ids missing from the cache; without it, only cached
translations are applied.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from estleg.estleg_common import (
    REPO_ROOT,
    allowed_get,
    iter_peep_files,
    jsonld_id_values,
    merge_title_langstring,
    save_json,
)

RT_AKT_META_URL = "https://www.riigiteataja.ee/api/v1/akt/{akt_id}"
RT_EN_AKT_META_URL = "https://www.riigiteataja.ee/api/v1/en/akt/{eli_id}"
RT_ENGLISH_ELI_PREFIX = "https://www.riigiteataja.ee/en/eli/"
MAPPING_PATH = REPO_ROOT / "data" / "riigiteataja" / "english_eli.json"

# RT's own test for an English-site globaalID (main.js ``onIngliskeelne``).
_ENGLISH_ELI_MIN = 500_000_000_000
_ENGLISH_ELI_MAX = 599_999_999_999

_AKT_ID_RE = re.compile(
    r"(?:riigiteataja\.ee)?/akt/(\d+)(?:\.xml)?(?:[?#].*)?$",
    re.IGNORECASE,
)


def akt_id_from_source(value: object) -> str | None:
    """Extract the numeric RT globaalID from a ``dcterms:source`` value."""
    for iri in jsonld_id_values(value):
        match = _AKT_ID_RE.search(iri)
        if match:
            return match.group(1)
    if isinstance(value, str):
        match = _AKT_ID_RE.search(value)
        if match:
            return match.group(1)
    return None


def is_english_eli_id(eli_id: str | int) -> bool:
    """True when ``eli_id`` is in RT's English globaalID band."""
    try:
        number = int(str(eli_id).strip())
    except (TypeError, ValueError):
        return False
    return _ENGLISH_ELI_MIN <= number <= _ENGLISH_ELI_MAX


def official_english_url(eli_id: str | int) -> str:
    """Ticket form: ``riigiteataja.ee/en/eli/{eli}`` (no scrape)."""
    return f"{RT_ENGLISH_ELI_PREFIX}{str(eli_id).strip()}"


def english_eli_from_meta(meta: dict) -> str | None:
    """Return ``tolkeSeosId`` when it is an English ELI globaalID."""
    raw = meta.get("tolkeSeosId")
    if raw is None or raw == "":
        return None
    eli_id = str(raw).strip()
    if not is_english_eli_id(eli_id):
        return None
    return eli_id


def attach_official_english(node: dict, eli_id: str) -> bool:
    """Set ``estleg:officialEnglishText`` to the English ELI IRI.

    Returns True when the node changed.
    """
    if not is_english_eli_id(eli_id):
        return False
    iri = official_english_url(eli_id)
    current = node.get("estleg:officialEnglishText")
    if isinstance(current, dict) and current.get("@id") == iri:
        return False
    if current == iri:
        return False
    node["estleg:officialEnglishText"] = {"@id": iri}
    return True


def load_mapping(path: Path = MAPPING_PATH) -> dict:
    if not path.exists():
        return {"acts": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"acts": {}}
    acts = data.get("acts")
    if not isinstance(acts, dict):
        data["acts"] = {}
    return data


def save_mapping(mapping: dict, path: Path = MAPPING_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    acts = mapping.get("acts") or {}
    mapping["acts"] = dict(sorted(acts.items(), key=lambda item: item[0]))
    save_json(path, mapping)


def fetch_act_meta(akt_id: str, *, timeout: int = 20) -> dict:
    """GET RT act metadata. Raises on HTTP/JSON failure."""
    url = RT_AKT_META_URL.format(akt_id=akt_id)
    response = allowed_get(
        url,
        timeout=timeout,
        headers={"Accept": "application/json", "User-Agent": "estleg-ontology/0.1"},
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"RT metadata for {akt_id} is not an object")
    return data


def fetch_english_title(eli_id: str, *, timeout: int = 20) -> str | None:
    """GET the official English pealkiri for an English ELI globaalID."""
    url = RT_EN_AKT_META_URL.format(eli_id=eli_id)
    response = allowed_get(
        url,
        timeout=timeout,
        headers={"Accept": "application/json", "User-Agent": "estleg-ontology/0.1"},
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        return None
    params = data.get("aktiParameetrid") or {}
    if not isinstance(params, dict):
        return None
    title = params.get("pealkiri")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def resolve_english_eli(
    akt_id: str,
    mapping: dict,
    *,
    fetch: bool,
    timeout: int = 20,
) -> dict | None:
    """Return the cached/fetched mapping row for ``akt_id``, or None."""
    acts: dict = mapping.setdefault("acts", {})
    cached = acts.get(akt_id)
    if cached == {}:
        return None
    row = dict(cached) if isinstance(cached, dict) else None
    fetched = False
    if row is None or not row.get("eli_id"):
        if not fetch:
            return row
        meta = fetch_act_meta(akt_id, timeout=timeout)
        fetched = True
        params = meta.get("aktiParameetrid") or {}
        title_et = params.get("pealkiri") if isinstance(params, dict) else None
        eli_id = english_eli_from_meta(meta)
        if eli_id is None:
            acts[akt_id] = {}
            return None
        row = {
            "eli_id": eli_id,
            "title_et": title_et,
            "url": official_english_url(eli_id),
        }
    if fetch and row.get("eli_id") and not row.get("title_en"):
        title_en = fetch_english_title(str(row["eli_id"]), timeout=timeout)
        fetched = True
        if title_en:
            row["title_en"] = title_en
    if row is not None:
        acts[akt_id] = row
    if fetched:
        # Caller counts fetches from before/after identity; keep a marker.
        row = dict(row)
        row["_fetched"] = True
    return row


def apply_row_to_node(node: dict, row: dict) -> bool:
    """Stamp officialEnglishText (and optional EN title) from a mapping row."""
    eli_id = row.get("eli_id")
    if not eli_id:
        return False
    changed = attach_official_english(node, str(eli_id))
    title_en = row.get("title_en")
    if isinstance(title_en, str) and title_en.strip():
        if merge_title_langstring(node, "en", title_en.strip()):
            changed = True
    return changed


def remint_peep(
    path: Path,
    mapping: dict,
    *,
    fetch: bool,
    dry_run: bool,
    timeout: int = 20,
    delay: float = 0.05,
) -> dict:
    """Remint one peep. Returns a small stats dict."""
    stats = {"files": 1, "acts": 0, "stamped": 0, "changed": 0, "fetched": 0}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return stats
    if not isinstance(doc, dict):
        return stats
    graph = doc.get("@graph") or []
    changed = False
    seen_ids: set[str] = set()
    for node in graph:
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        # Multipart osa files share one RT akt id across several Act nodes.
        if "estleg:Act" not in types and "estleg:Law" not in types:
            continue
        stats["acts"] += 1
        akt_id = akt_id_from_source(node.get("dcterms:source"))
        if not akt_id:
            continue
        before = (mapping.get("acts") or {}).get(akt_id)
        row = resolve_english_eli(akt_id, mapping, fetch=fetch, timeout=timeout)
        after = (mapping.get("acts") or {}).get(akt_id)
        if before != after and fetch:
            stats["fetched"] += 1
            if delay:
                time.sleep(delay)
        if not row or not row.get("eli_id"):
            continue
        if akt_id not in seen_ids:
            stats["stamped"] += 1
            seen_ids.add(akt_id)
        if apply_row_to_node(node, row):
            changed = True
            stats["changed"] += 1
    if changed and not dry_run:
        save_json(path, doc)
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write peeps and the mapping file (default is dry-run)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Contact RT for akt ids missing from the mapping cache",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=MAPPING_PATH,
        help="Path to english_eli.json",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Seconds to sleep after each live RT fetch",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dry_run = not args.apply
    mapping = load_mapping(args.mapping)
    files = iter_peep_files(include_regulations=False, include_kov=False)
    totals = {"files": 0, "acts": 0, "stamped": 0, "changed": 0, "fetched": 0}
    print("=" * 70)
    print("Backfill estleg:officialEnglishText from RT tolkeSeosId (#510)")
    print(f"  {'DRY RUN' if dry_run else 'APPLY'}  fetch={args.fetch}  peeps={len(files)}")
    print("=" * 70)
    for path in files:
        stats = remint_peep(
            path,
            mapping,
            fetch=args.fetch,
            dry_run=dry_run,
            delay=args.delay,
        )
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + value
        if stats["changed"] and not args.fetch:
            print(f"  {path.name}: {stats['changed']} node(s)")
    if args.apply:
        mapping["_comment"] = (
            "Official English RT consolidations. eli_id is tolkeSeosId from "
            "GET /api/v1/akt/{estonian_globaalID} (#510)."
        )
        save_mapping(mapping, args.mapping)
    print(
        f"\n  files={totals['files']} acts={totals['acts']} "
        f"with_english={totals['stamped']} nodes_"
        f"{'would_change' if dry_run else 'changed'}={totals['changed']} "
        f"fetched={totals['fetched']}"
    )
    if not dry_run and totals["changed"]:
        print(
            "\n  NOTE: rebuild combined_ontology.jsonld so the load surface "
            "carries officialEnglishText (fix_all_issues.generate_combined_jsonld)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
