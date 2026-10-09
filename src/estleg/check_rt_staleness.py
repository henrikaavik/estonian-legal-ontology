#!/usr/bin/env python3
"""Corpus freshness gate: RT content staleness (#531) + per-corpus lag budgets (#693).

Default mode is offline and CI-safe. It compares each advertised corpus's
committed snapshot stamp against the evaluation date — **today** unless
``--evaluation-date`` pins it (#693; tests and reproducible CI reruns pass an
explicit date). ``BUILD_EVALUATION_DATE`` stays the byte-stable ``generated``
stamp of tracked artifacts (#295) and is deliberately *not* used here: a
freshness gate measured against a pinned date can never fail.

Laws use committed ``estleg:kehtiv`` on a small allowlist of high-traffic
peeps (PKS, KarS osa 1, PS). The other corpora use their index stamp (see
``CORPUS_BUDGETS``). Each corpus has a lag budget = publishing cadence +
grace; the cadence is published per ``dcat:distribution`` in
``metadata.jsonld`` as ``dcterms:accrualPeriodicity`` and a test keeps the
two in sync.

``--fetch`` is operator-run (not used in CI). For each law sample it GETs the
public-API JSON metadata (``fetch_act_metadata``, #691) — a ``kehtivId``
different from the committed act id means RT has a newer consolidation — and
the act XML, whose ``parse_act_metadata`` dates are a live consolidation hint.

``--schema-canary`` (CI, separate step) GETs one live act's XML through
``fetch_xml`` and checks the pinned schema. Exit 0 = OK or RT unreachable
(warning), exit 1 = RT answered but the format changed.

    python3 scripts/check_rt_staleness.py
    python3 scripts/check_rt_staleness.py --evaluation-date 2026-06-01
    python3 scripts/check_rt_staleness.py --fetch          # operator-run; network
    python3 scripts/check_rt_staleness.py --schema-canary  # network
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from estleg.estleg_common import KRR_DIR, allowed_get
from estleg.riigiteataja_common import (
    CANARY_OK,
    CANARY_UNREACHABLE,
    RTFormatError,
    build_xml_url,
    fetch_act_metadata,
    is_html_payload,
    parse_act_metadata,
    parse_xml,
    rt_act_id,
    run_live_schema_canary,
)

# Published refresh SLA for laws: monthly RT consolidation (30 d) plus a
# 15-day grace so a late-month snapshot still passes.
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


def evaluation_date(
    explicit: str | date | None = None, *, today: date | None = None
) -> date:
    """SLA reference date: *explicit* if given, else today (#693).

    ``today`` is injectable for tests; production callers leave it unset.
    An unparseable *explicit* value is an error, not a silent fallback.
    """
    if explicit is not None and str(explicit).strip():
        parsed = parse_iso_date(explicit)
        if parsed is None:
            raise ValueError(f"evaluation date is not an ISO date: {explicit!r}")
        return parsed
    return today if today is not None else date.today()


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
    """GET an act's public-API XML (#691) and parse a consolidation date.

    An HTML body (the post-2026-06-01 app shell) raises ``RTFormatError``
    instead of being scanned for stray ISO dates.
    """
    xml_url = build_xml_url(url)
    response = allowed_get(xml_url, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    body = response.text
    content_type = str((getattr(response, "headers", None) or {}).get("Content-Type", ""))
    if is_html_payload(body, content_type):
        raise RTFormatError(f"Riigi Teataja returned HTML instead of act XML for {xml_url}")
    return parse_rt_consolidation_date(body)


def _fetch_failures(sample: SampleKehtiv) -> list[str]:
    """Operator ``--fetch`` comparison of one law sample against live RT."""
    assert sample.kehtiv is not None
    if not sample.rt_url:
        return [f"{sample.abbrev}: --fetch requested but no Riigi Teataja URL"]
    failures: list[str] = []
    try:
        meta = fetch_act_metadata(sample.rt_url)
    except RTFormatError:
        raise
    except Exception as e:  # noqa: BLE001 - report, keep checking other samples
        failures.append(f"{sample.abbrev}: --fetch metadata GET failed: {e}")
    else:
        current = meta.get("currentId")
        committed = rt_act_id(sample.rt_url)
        if current and current != committed:
            failures.append(
                f"{sample.abbrev}: RT has a newer consolidation (kehtivId={current}) "
                f"than the committed act {committed}"
            )
    live = fetch_rt_consolidation_date(sample.rt_url)
    live_date = parse_iso_date(live)
    if live_date is None:
        failures.append(f"{sample.abbrev}: --fetch could not parse a consolidation date")
    elif live_date > sample.kehtiv:
        failures.append(
            f"{sample.abbrev}: live RT date {live_date.isoformat()} "
            f"is newer than committed kehtiv {sample.kehtiv.isoformat()}"
        )
    return failures


def check_samples(
    samples: list[SampleKehtiv],
    *,
    as_of: date,
    max_lag_days: int = SLA_MAX_LAG_DAYS,
    fetch: bool = False,
) -> list[str]:
    """Return human-readable failure lines for the law sample (empty = SLA met)."""
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
            failures.extend(_fetch_failures(sample))
    return failures


# ---------------------------------------------------------------------------
# Per-corpus lag budgets (#693)
# ---------------------------------------------------------------------------

FREQ_MONTHLY = "http://purl.org/cld/freq/monthly"
FREQ_QUARTERLY = "http://purl.org/cld/freq/quarterly"
FREQ_IRREGULAR = "http://purl.org/cld/freq/irregular"

# Nominal cadence in days for each Dublin Core Collection Description
# frequency IRI we publish. A lag budget must cover its cadence and allow
# at most one more month of grace, so the published periodicity and the
# enforced budget cannot drift apart silently.
FREQ_NOMINAL_DAYS: dict[str, int] = {FREQ_MONTHLY: 30, FREQ_QUARTERLY: 91}
MAX_GRACE_DAYS = 31


@dataclass(frozen=True)
class CorpusBudget:
    """Freshness contract for one advertised corpus.

    ``stamp_file`` / ``stamp_fields`` name where the committed snapshot date
    lives (first non-empty field wins); ``stamp_file=None`` means the
    corpus is measured by the law ``estleg:kehtiv`` sample instead.
    ``max_lag_days=None`` means not gated (aggregate or frozen artifact).
    ``distribution_title`` ties the row to the ``dcat:distribution`` whose
    ``dcterms:accrualPeriodicity`` must equal ``accrual_periodicity``; it
    is ``None`` for corpora that are not advertised as a distribution.
    """

    key: str
    label: str
    distribution_title: str | None
    accrual_periodicity: str
    max_lag_days: int | None
    stamp_file: str | None = None
    stamp_fields: tuple[str, ...] = ()
    rationale: str = ""


CORPUS_BUDGETS: tuple[CorpusBudget, ...] = (
    CorpusBudget(
        key="dataset",
        label="Complete dataset",
        distribution_title="JSON-LD ontology files (complete dataset)",
        accrual_periodicity=FREQ_MONTHLY,
        max_lag_days=None,
        rationale="Aggregate; freshness is the per-corpus rows below. "
        "Monthly is its fastest-moving part (laws).",
    ),
    CorpusBudget(
        key="laws",
        label="Enacted laws (RT)",
        distribution_title="Combined enacted laws ontology",
        accrual_periodicity=FREQ_MONTHLY,
        max_lag_days=SLA_MAX_LAG_DAYS,
        rationale="Monthly RT consolidation + 15 d grace; laws are the "
        "core product and RT consolidates continuously.",
    ),
    CorpusBudget(
        key="regulations-riik",
        label="State regulations (RT)",
        distribution_title="Domestic regulations (state-level)",
        accrual_periodicity=FREQ_MONTHLY,
        max_lag_days=60,
        stamp_file="regulations/riik/REGULATIONS_RIIK_INDEX.json",
        stamp_fields=("kehtiv",),
        rationale="Monthly cadence + 30 d grace: same RT source as laws, "
        "but the ~3.8k-act fetch runs after the law refresh.",
    ),
    CorpusBudget(
        key="regulations-kov",
        label="Municipal regulations (KOV, RT)",
        distribution_title="Municipal regulations ontology (KOV)",
        accrual_periodicity=FREQ_MONTHLY,
        max_lag_days=60,
        stamp_file="regulations/kov/REGULATIONS_KOV_INDEX.json",
        stamp_fields=("kehtiv",),
        rationale="Same pipeline and snapshot date as state regulations "
        "(~11k acts).",
    ),
    CorpusBudget(
        key="eelnoud",
        label="Draft legislation (EIS)",
        distribution_title="Combined draft legislation ontology",
        accrual_periodicity=FREQ_MONTHLY,
        max_lag_days=60,
        stamp_file="eelnoud/EELNOUD_INDEX.json",
        stamp_fields=("fetched", "generated"),
        rationale="Drafts move weekly and feed the drafting tool, so they "
        "share the regulations' monthly + 30 d budget; a tighter one is "
        "not realistic for a 22.8k-draft scrape.",
    ),
    CorpusBudget(
        key="riigikohus",
        label="Supreme Court decisions (Riigikohus)",
        distribution_title="Combined Supreme Court decisions ontology (Riigikohus)",
        accrual_periodicity=FREQ_QUARTERLY,
        max_lag_days=120,
        stamp_file="riigikohus/RIIGIKOHUS_INDEX.json",
        stamp_fields=("fetched", "generated"),
        rationale="~25 decisions/month; case law is cited, not tracked "
        "daily. Quarterly + 29 d grace.",
    ),
    CorpusBudget(
        key="kohtud",
        label="Lower-court decisions (sample)",
        distribution_title=None,
        accrual_periodicity=FREQ_QUARTERLY,
        max_lag_days=120,
        stamp_file="kohtud/KOHTUD_INDEX.json",
        stamp_fields=("fetched", "generated"),
        rationale="A capped sample (#689), not an advertised distribution; "
        "same budget as Riigikohus.",
    ),
    CorpusBudget(
        key="eurlex",
        label="EU legislation (EUR-Lex)",
        distribution_title="Combined EU legislation ontology",
        accrual_periodicity=FREQ_QUARTERLY,
        max_lag_days=120,
        stamp_file="eurlex/EURLEX_INDEX.json",
        stamp_fields=("fetched", "generated"),
        rationale="Estonian translations lag adoption by weeks; used for "
        "harmonisation analysis, not same-day lookup.",
    ),
    CorpusBudget(
        key="curia",
        label="EU court decisions (CURIA)",
        distribution_title="Combined EU court decisions ontology",
        accrual_periodicity=FREQ_QUARTERLY,
        max_lag_days=120,
        stamp_file="curia/CURIA_INDEX.json",
        stamp_fields=("fetched", "generated"),
        rationale="Same CELLAR pipeline and use as EUR-Lex.",
    ),
    CorpusBudget(
        key="changes",
        label="Inter-release change record",
        distribution_title="Inter-release change record 0.11.0",
        accrual_periodicity=FREQ_IRREGULAR,
        max_lag_days=None,
        rationale="Frozen per-release diff; it is never refreshed, a new "
        "release publishes a new one.",
    ),
)


@dataclass(frozen=True)
class CorpusStamp:
    budget: CorpusBudget
    path: Path | None
    stamp: date | None
    field: str | None
    partial: bool = False


def read_corpus_stamp(budget: CorpusBudget, krr_dir: Path = KRR_DIR) -> CorpusStamp:
    """Read the committed snapshot date for a non-law corpus."""
    if budget.stamp_file is None:
        return CorpusStamp(budget, None, None, None)
    path = krr_dir / budget.stamp_file
    if not path.is_file():
        return CorpusStamp(budget, path, None, None)
    with path.open(encoding="utf-8") as handle:
        doc = json.load(handle)
    if isinstance(doc, dict):
        partial = bool(doc.get("partial") or doc.get("partial_years"))
        for field in budget.stamp_fields:
            parsed = parse_iso_date(doc.get(field))
            if parsed is not None:
                return CorpusStamp(budget, path, parsed, field, partial=partial)
    return CorpusStamp(budget, path, None, None)


def check_corpora(
    *,
    as_of: date,
    krr_dir: Path = KRR_DIR,
    budgets: tuple[CorpusBudget, ...] = CORPUS_BUDGETS,
) -> tuple[list[CorpusStamp], list[str]]:
    """Check every index-stamped corpus against its lag budget."""
    stamps: list[CorpusStamp] = []
    failures: list[str] = []
    for budget in budgets:
        if budget.stamp_file is None or budget.max_lag_days is None:
            continue
        stamp = read_corpus_stamp(budget, krr_dir)
        stamps.append(stamp)
        if stamp.partial:
            failures.append(f"{budget.key}: partial snapshot; a complete refresh is required")
        if stamp.path is not None and not stamp.path.is_file():
            failures.append(f"{budget.key}: missing index {stamp.path}")
            continue
        if stamp.stamp is None:
            failures.append(
                f"{budget.key}: {budget.stamp_file} has no date in {list(budget.stamp_fields)}"
            )
            continue
        lag = (as_of - stamp.stamp).days
        if lag > budget.max_lag_days:
            failures.append(
                f"{budget.key}: {stamp.field}={stamp.stamp.isoformat()} is {lag}d "
                f"behind {as_of.isoformat()} (budget {budget.max_lag_days}d)"
            )
    return stamps, failures


def _print_schema_canary_result() -> int:
    result = run_live_schema_canary()
    print(f"RT live schema canary (#691): {result.url}")
    if result.status == CANARY_OK:
        print(f"SCHEMA OK: {result.detail}")
        return 0
    if result.status == CANARY_UNREACHABLE:
        # Annotation for GitHub Actions; harmless on a terminal.
        print(f"::warning title=RT unreachable::{result.detail}")
        print(f"SCHEMA UNKNOWN (RT unreachable, not a format change): {result.detail}")
        return 0
    print(f"::error title=RT format changed::{result.detail}")
    print(f"SCHEMA FAIL (RT answered but the act format changed): {result.detail}")
    print(
        "  Remediation: update riigiteataja_common.build_xml_url / RT_LAW_XML_SCHEMA "
        "and the generators' tag contract before the next refresh."
    )
    return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Operator-run: GET live RT metadata + act XML and compare to the law sample.",
    )
    parser.add_argument(
        "--schema-canary",
        action="store_true",
        help="Only run the live RT schema canary (GET one act's XML); "
        "exit 1 only when RT answered with a changed format.",
    )
    parser.add_argument(
        "--evaluation-date",
        default=None,
        help="Override the as-of date (default: today). Use for reproducible reruns.",
    )
    parser.add_argument(
        "--max-lag-days",
        type=int,
        default=SLA_MAX_LAG_DAYS,
        help=f"Maximum allowed law kehtiv lag (default: {SLA_MAX_LAG_DAYS}).",
    )
    parser.add_argument(
        "--laws-only",
        action="store_true",
        help="Check only the law estleg:kehtiv sample (the #531 behaviour).",
    )
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="Corpus directory (default: krr_outputs/).",
    )
    return parser.parse_args(argv)


REMEDIATION = (
    "Remediation: refresh the stale corpora through their generators "
    "(laws/regulations now fetch via the RT public API, #691: "
    "generate_all_laws / generate_regulations), commit the new snapshot, "
    "and rerun this gate. See docs/RELEASE.md#refresh-sla."
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.schema_canary:
        return _print_schema_canary_result()
    as_of = evaluation_date(args.evaluation_date)
    samples = load_sample(args.krr_dir)
    print(
        f"Corpus freshness gate (#531/#693): evaluation_date={as_of.isoformat()} "
        f"({'explicit' if args.evaluation_date else 'today'}), "
        f"generator_kehtiv_pin={GENERATOR_KEHTIV_PIN}, fetch={args.fetch}"
    )
    print(f"  laws (budget {args.max_lag_days}d, estleg:kehtiv sample):")
    for sample in samples:
        kehtiv = sample.kehtiv.isoformat() if sample.kehtiv else "missing"
        lag = f", {(as_of - sample.kehtiv).days}d" if sample.kehtiv else ""
        print(f"    {sample.abbrev}: kehtiv={kehtiv}{lag} ({sample.path.name})")
    failures = check_samples(
        samples,
        as_of=as_of,
        max_lag_days=args.max_lag_days,
        fetch=args.fetch,
    )
    if not args.laws_only:
        stamps, corpus_failures = check_corpora(as_of=as_of, krr_dir=args.krr_dir)
        for stamp in stamps:
            budget = stamp.budget
            if stamp.stamp is None:
                shown = "missing"
            else:
                shown = (
                    f"{stamp.field}={stamp.stamp.isoformat()}, "
                    f"{(as_of - stamp.stamp).days}d"
                )
            print(f"  {budget.key} (budget {budget.max_lag_days}d): {shown}")
        failures.extend(corpus_failures)
    if failures:
        print("SLA FAIL:")
        for line in failures:
            print(f"  {line}")
        print(REMEDIATION)
        return 1
    print("SLA PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
