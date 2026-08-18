#!/usr/bin/env python3
"""Riigi Teataja content-staleness canary (#531).

Default mode is offline and CI-safe: read committed ``estleg:kehtiv`` from a
small allowlist of high-traffic peeps (PKS, KarS osa 1, PS) and fail if any
snapshot is more than ``SLA_MAX_LAG_DAYS`` behind
``estleg_common.BUILD_EVALUATION_DATE`` (the date part of
``PINNED_RUN_TIMESTAMP``).

The corpus target is monthly RT consolidation. ``estleg:kehtiv`` is the
per-act snapshot date. ``generate_all_laws.DEFAULT_KEHTIV`` (``2026-05-01``)
is the last full-generator pin, documented here as ``GENERATOR_KEHTIV_PIN``.

``--fetch`` is operator-run (not used in CI). It HEADs/GETs the peep's
Riigi Teataja akt URL via ``allowed_get`` and, when the body is RT XML,
parses ``parse_act_metadata`` dates as a live consolidation hint.

    python3 scripts/check_rt_staleness.py
    python3 scripts/check_rt_staleness.py --fetch   # operator-run; network
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from estleg_common import (
    BUILD_EVALUATION_DATE,
    KRR_DIR,
    PINNED_RUN_TIMESTAMP,
    allowed_get,
)
from riigiteataja_common import build_xml_url, parse_act_metadata, parse_xml

# Published refresh SLA: monthly RT consolidation, with a 45-day lag
# budget so a late-month snapshot still passes against a pinned
# evaluation date (BUILD_EVALUATION_DATE is currently 2026-06-01).
SLA_MAX_LAG_DAYS = 45
GENERATOR_KEHTIV_PIN = "2026-05-01"  # generate_all_laws.DEFAULT_KEHTIV

# High-traffic sample (issue #531). Paths are repo-relative under krr_outputs/.
STALE_SAMPLE: tuple[tuple[str, str], ...] = (
    ("PKS", "perekonnaseadus_peep.json"),
    ("KarS", "karistusseadustik_osa1_peep.json"),
    ("PS", "eesti_vabariigi_pohiseadus_peep.json"),
)

_ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


@dataclass(frozen=True)
class SampleKehtiv:
    abbrev: str
    path: Path
    kehtiv: date | None
    rt_url: str | None


def parse_iso_date(value: str | date | None) -> date | None:
    """Parse a ``YYYY-MM-DD`` string (or pass a ``date`` through)."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def evaluation_date(explicit: str | date | None = None) -> date:
    """SLA reference date: explicit, else BUILD_EVALUATION_DATE / pin."""
    parsed = parse_iso_date(explicit)
    if parsed is not None:
        return parsed
    pinned = parse_iso_date(BUILD_EVALUATION_DATE)
    if pinned is not None:
        return pinned
    from_ts = parse_iso_date(PINNED_RUN_TIMESTAMP)
    if from_ts is not None:
        return from_ts
    raise ValueError("BUILD_EVALUATION_DATE / PINNED_RUN_TIMESTAMP is not an ISO date")


def kehtiv_lags_past_sla(
    kehtiv: str | date | None,
    evaluation: str | date | None = None,
    *,
    max_lag_days: int = SLA_MAX_LAG_DAYS,
) -> bool:
    """True when *kehtiv* is more than *max_lag_days* behind *evaluation*."""
    kehtiv_date = parse_iso_date(kehtiv)
    if kehtiv_date is None:
        return True
    as_of = evaluation_date(evaluation)
    return (as_of - kehtiv_date) > timedelta(days=max_lag_days)


def extract_kehtiv(doc: dict) -> str | None:
    """Return the first ``estleg:kehtiv`` literal on a peep ``@graph``."""
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return None
    for node in graph:
        if not isinstance(node, dict) or "estleg:kehtiv" not in node:
            continue
        raw = node["estleg:kehtiv"]
        if isinstance(raw, dict):
            value = raw.get("@value")
            if isinstance(value, str) and value:
                return value
        elif isinstance(raw, str) and raw:
            return raw
    return None


def extract_rt_url(doc: dict) -> str | None:
    """Return a Riigi Teataja akt URL from ``dcterms:source`` / ``owl:sameAs``."""
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return None
    for node in graph:
        if not isinstance(node, dict):
            continue
        for key in ("dcterms:source", "owl:sameAs"):
            raw = node.get(key)
            candidates = raw if isinstance(raw, list) else [raw]
            for item in candidates:
                url: str | None
                if isinstance(item, dict):
                    url = item.get("@id") if isinstance(item.get("@id"), str) else None
                elif isinstance(item, str):
                    url = item
                else:
                    url = None
                if url and "riigiteataja.ee" in url:
                    return url
    return None


def load_sample(krr_dir: Path = KRR_DIR) -> list[SampleKehtiv]:
    samples: list[SampleKehtiv] = []
    for abbrev, relpath in STALE_SAMPLE:
        path = krr_dir / relpath
        kehtiv: date | None = None
        rt_url: str | None = None
        if path.is_file():
            with path.open(encoding="utf-8") as handle:
                doc = json.load(handle)
            if isinstance(doc, dict):
                kehtiv = parse_iso_date(extract_kehtiv(doc))
                rt_url = extract_rt_url(doc)
        samples.append(SampleKehtiv(abbrev, path, kehtiv, rt_url))
    return samples


def parse_rt_consolidation_date(body: str) -> str | None:
    """Best-effort ISO date from an RT akt HTML/XML body.

    Prefers ``parse_act_metadata`` (existing XML helper) when the body is
    RT XML. Otherwise scans for a ``kehtiv=YYYY-MM-DD`` query param, then
    the first plausible ISO date. There is no dedicated HTML consolidation
    parser in ``riigiteataja_common``.
    """
    text = body.lstrip()
    if text.startswith("<") and "oigusakt" in text[:4000]:
        try:
            root = parse_xml(text)
        except (ET.ParseError, ValueError, TypeError):
            root = None
        if root is not None:
            meta = parse_act_metadata(root)
            for key in ("lastAmendmentDate", "entryIntoForce"):
                value = meta.get(key)
                if isinstance(value, str) and parse_iso_date(value):
                    return value[:10]
    match = re.search(r"[?&]kehtiv=(\d{4}-\d{2}-\d{2})", body)
    if match:
        return match.group(1)
    found = _ISO_DATE.search(body)
    return found.group(1) if found else None


def fetch_rt_consolidation_date(url: str, *, timeout: int = 30) -> str | None:
    """GET an RT akt URL (via ``allowed_get``) and parse a consolidation date."""
    xml_url = build_xml_url(url)
    response = allowed_get(xml_url, timeout=timeout)
    response.raise_for_status()
    return parse_rt_consolidation_date(response.text)


def check_samples(
    samples: list[SampleKehtiv],
    *,
    as_of: date,
    max_lag_days: int = SLA_MAX_LAG_DAYS,
    fetch: bool = False,
) -> list[str]:
    """Return human-readable failure lines (empty = SLA met)."""
    failures: list[str] = []
    for sample in samples:
        if not sample.path.is_file():
            failures.append(f"{sample.abbrev}: missing peep {sample.path}")
            continue
        if sample.kehtiv is None:
            failures.append(f"{sample.abbrev}: {sample.path.name} has no estleg:kehtiv")
            continue
        if kehtiv_lags_past_sla(sample.kehtiv, as_of, max_lag_days=max_lag_days):
            lag = (as_of - sample.kehtiv).days
            failures.append(
                f"{sample.abbrev}: estleg:kehtiv={sample.kehtiv.isoformat()} "
                f"is {lag}d behind {as_of.isoformat()} "
                f"(SLA_MAX_LAG_DAYS={max_lag_days})"
            )
        if fetch:
            if not sample.rt_url:
                failures.append(
                    f"{sample.abbrev}: --fetch requested but no Riigi Teataja URL"
                )
                continue
            live = fetch_rt_consolidation_date(sample.rt_url)
            live_date = parse_iso_date(live)
            if live_date is None:
                failures.append(
                    f"{sample.abbrev}: --fetch could not parse a consolidation date"
                )
                continue
            if live_date > sample.kehtiv:
                failures.append(
                    f"{sample.abbrev}: live RT date {live_date.isoformat()} "
                    f"is newer than committed kehtiv {sample.kehtiv.isoformat()}"
                )
    return failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Operator-run: GET live RT akt XML and compare consolidation date.",
    )
    parser.add_argument(
        "--evaluation-date",
        default=None,
        help=f"Override SLA as-of date (default: {BUILD_EVALUATION_DATE}).",
    )
    parser.add_argument(
        "--max-lag-days",
        type=int,
        default=SLA_MAX_LAG_DAYS,
        help=f"Maximum allowed kehtiv lag (default: {SLA_MAX_LAG_DAYS}).",
    )
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="Corpus directory (default: krr_outputs/).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    as_of = evaluation_date(args.evaluation_date)
    samples = load_sample(args.krr_dir)
    print(
        f"RT staleness canary (#531): SLA_MAX_LAG_DAYS={args.max_lag_days}, "
        f"evaluation_date={as_of.isoformat()}, "
        f"generator_kehtiv_pin={GENERATOR_KEHTIV_PIN}, fetch={args.fetch}"
    )
    for sample in samples:
        kehtiv = sample.kehtiv.isoformat() if sample.kehtiv else "missing"
        print(f"  {sample.abbrev}: kehtiv={kehtiv} ({sample.path.name})")
    failures = check_samples(
        samples,
        as_of=as_of,
        max_lag_days=args.max_lag_days,
        fetch=args.fetch,
    )
    if failures:
        print("SLA FAIL:")
        for line in failures:
            print(f"  {line}")
        return 1
    print("SLA PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
