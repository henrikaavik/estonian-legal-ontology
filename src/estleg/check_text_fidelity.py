#!/usr/bin/env python3
"""Legal-text fidelity gate against Riigi Teataja (issue #703).

Two checks protect the authoritative provision text rather than the derived
``estleg:summary``:

``--coverage`` (offline, fast; every push)
    Measures three coverage rules over the root law peeps
    (``krr_outputs/*_peep.json``) and compares them with the committed
    baseline ``data/text_fidelity_baseline.json``. The baseline may only
    shrink: a node that is not already listed fails the gate.

    * ``missing_legal_text``: an ``estleg:LegalProvision`` with an
      ``estleg:paragrahv`` whose file head (:func:`act_root_node`) has
      ``estleg:contentStatus "structuredBody"`` but carries no
      ``estleg:legalText``. The file head is the act root or the
      ``estleg:Part`` of a multi-part law, so it is the node that was
      actually parsed from the RT XML.
    * ``root_missing_source`` / ``root_missing_kehtiv``: a law-peep file
      head without ``dcterms:source`` / ``estleg:kehtiv``. Only root law
      peeps are measured; state and KOV regulations carry no ``kehtiv``
      by design (#703 scope note) and are out of scope.

``--sample N`` (live; weekly)
    Picks N provisions (seeded) that carry ``estleg:legalText`` from
    structured law files whose head has an RT ``/akt/{id}`` source, GETs
    that exact redaction's XML through ``riigiteataja_common.fetch_xml``,
    re-parses it with the generator's own
    :func:`law_structure.emit_hierarchy_and_provisions`, and diffs the text
    after :func:`normalise_text`. The committed redaction is fetched (not the
    one in force today), so a newer consolidation never shows up as a text
    difference; it is reported separately via ``fetch_act_metadata``
    (``kehtivId`` != committed id). When the committed redaction does not
    match but the current one does, the text was regenerated from a newer
    consolidation without updating ``dcterms:source``; that is reported as
    ``stale-source-id`` and does not fail the gate.

Exit codes: 0 all good; 1 coverage regression, text mismatch, missing
provision, unknown act id or RT format change; 2 Riigi Teataja unreachable
(no mismatch found among the samples that could be checked).
"""

from __future__ import annotations

import argparse
import difflib
import json
import random
import re
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import requests

from estleg import riigiteataja_common as rt
from estleg.estleg_common import act_root_node, jsonld_text, node_type_list
from estleg.law_structure import emit_hierarchy_and_provisions
from estleg.riigiteataja_common import ct, ln

REPO = Path(__file__).resolve().parents[2]
KRR = REPO / "krr_outputs"
BASELINE_PATH = REPO / "data" / "text_fidelity_baseline.json"

DEFAULT_SAMPLE = 25
DEFAULT_SEED = 703
DIFF_MAX_LINES = 24

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_UNREACHABLE = 2

COVERAGE_RULES: dict[str, str] = {
    "missing_legal_text": (
        "estleg:LegalProvision (with estleg:paragrahv) in a root law peep whose "
        "file head has contentStatus 'structuredBody' but no estleg:legalText"
    ),
    "root_missing_source": (
        "root law peep file head (act_root_node: estleg:Law, or estleg:Part of a "
        "multi-part law) without dcterms:source"
    ),
    "root_missing_kehtiv": (
        "root law peep file head (act_root_node) without estleg:kehtiv"
    ),
}

# Result statuses of one sampled provision.
MATCH = "match"
STALE_SOURCE_ID = "stale-source-id"
MISMATCH = "mismatch"
NOT_FOUND = "not-found"
FETCH_ERROR = "fetch-error"
FORMAT_ERROR = "format-error"
UNREACHABLE = "unreachable"
FAILING_STATUSES = frozenset({MISMATCH, NOT_FOUND, FETCH_ERROR, FORMAT_ERROR})

_SUP_TAG_RE = re.compile(r"</?sup\s*>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
# After NFKC: "(1) Kehtetu -", "(2 1) Kehtetu" (superscript folded).
_REPEALED_LOIGE_RE = re.compile(r"\(\s*\d+(?:\s*\d+)*\s*\)\s*Kehtetu\s*[-\u2013\u2014]?")
# A whitespace-delimited enumerator token: "1)", "12)", "21)" (from "2¹)").
_ITEM_MARKER_RE = re.compile(r"(?<!\S)\d+[a-z]?\)(?=\s|$)")
# A repealed paragraph or list item rendered as "Kehtetu -" / "kehtetu -".
# Only the dash-terminated RT placeholder: "Tehing on kehtetu." is wording.
_REPEALED_PLACEHOLDER_RE = re.compile(r"\bkehtetu\s*[-\u2013\u2014](?=\s|$)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalise_text(text: str) -> str:
    """Comparison form of a provision text.

    Both sides go through this. It removes rendering differences that carry
    no normative wording, so a parser change in ``law_structure`` (the
    ``<sup>`` / alampunkt fix) cannot turn into a false mismatch:

    * ``<sup>`` markup is dropped and NFKC folds superscripts (``§ 7²`` and
      ``§ 72`` compare equal);
    * repealed placeholders are dropped, for a subsection (``(1) Kehtetu -``),
      a whole paragraph (``Kehtetu -``) or a list item (``kehtetu -``):
      older corpus text keeps them, the current parser emits nothing for an
      empty repealed element. Only the dash-terminated placeholder is
      dropped; ``Tehing on kehtetu.`` is wording and still compared;
    * standalone list-item markers (``1)``, ``2¹)``) are dropped: older
      corpus text omits the alampunkt numbers the current parser adds;
    * every whitespace run is removed.
    """
    text = _SUP_TAG_RE.sub("", text or "")
    text = unicodedata.normalize("NFKC", text)
    text = _REPEALED_LOIGE_RE.sub(" ", text)
    text = _ITEM_MARKER_RE.sub(" ", text)
    text = _REPEALED_PLACEHOLDER_RE.sub(" ", text)
    return _WS_RE.sub("", text)


_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SUPERSCRIPT_RUN_RE = re.compile("[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_SUP_OPEN_RE = re.compile(r"<sup\s*>", re.IGNORECASE)


def provision_key(paragrahv: str) -> str:
    """Lookup key for a ``§`` display label (``§ 7².`` → ``7^2``).

    Superscripts stay distinguishable from plain digits here (``§ 22¹`` is
    not ``§ 221``), unlike in :func:`normalise_text`.
    """
    text = _SUP_OPEN_RE.sub("^", paragrahv or "")
    text = _SUP_TAG_RE.sub("", text)
    text = _SUPERSCRIPT_RUN_RE.sub(
        lambda m: "^" + m.group(0).translate(_SUPERSCRIPT_DIGITS), text
    )
    text = _WS_RE.sub("", text.replace("§", ""))
    return text.rstrip(".").lower()


def _text_value(value: object) -> str:
    return jsonld_text(value) or ""


# ---------------------------------------------------------------------------
# Corpus walk
# ---------------------------------------------------------------------------


def iter_root_law_docs(krr: Path = KRR) -> Iterator[tuple[Path, dict]]:
    """Yield ``(path, doc)`` for every root law peep, in path order."""
    for path in sorted(krr.glob("*_peep.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("@graph"), list):
            yield path, doc


def _is_provision(node: dict) -> bool:
    return (
        "estleg:LegalProvision" in node_type_list(node)
        and "estleg:paragrahv" in node
    )


def measure_coverage(krr: Path = KRR) -> dict[str, list[str]]:
    """Current offender ``@id`` sets for each :data:`COVERAGE_RULES` rule."""
    offenders: dict[str, set[str]] = {rule: set() for rule in COVERAGE_RULES}
    for _path, doc in iter_root_law_docs(krr):
        head = act_root_node(doc)
        if head is None:
            continue
        head_id = str(head.get("@id", ""))
        if "dcterms:source" not in head:
            offenders["root_missing_source"].add(head_id)
        if "estleg:kehtiv" not in head:
            offenders["root_missing_kehtiv"].add(head_id)
        if head.get("estleg:contentStatus") != "structuredBody":
            continue
        for node in doc["@graph"]:
            if not (isinstance(node, dict) and _is_provision(node)):
                continue
            if node.get("estleg:provisionRepealed") is True:
                # A § Riigi Teataja marks "Kehtetu -" has no body by design.
                continue
            if not _text_value(node.get("estleg:legalText")):
                offenders["missing_legal_text"].add(str(node.get("@id", "")))
    return {rule: sorted(ids) for rule, ids in offenders.items()}


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data.get("rules", {})
    return {rule: list(rules.get(rule, {}).get("offenders", [])) for rule in COVERAGE_RULES}


def write_baseline(current: dict[str, list[str]], path: Path = BASELINE_PATH) -> None:
    doc = {
        "issue": "#703",
        "description": (
            "Coverage baseline for scripts/check_text_fidelity.py --coverage. "
            "Lists may only shrink; regenerate with --update-baseline after "
            "fixing offenders."
        ),
        "rules": {
            rule: {
                "definition": COVERAGE_RULES[rule],
                "count": len(current[rule]),
                "offenders": current[rule],
            }
            for rule in COVERAGE_RULES
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class CoverageDelta:
    rule: str
    baseline: int
    current: int
    new: list[str]
    fixed: list[str]


def compare_coverage(
    current: dict[str, list[str]], baseline: dict[str, list[str]]
) -> list[CoverageDelta]:
    deltas = []
    for rule in COVERAGE_RULES:
        cur, base = set(current.get(rule, [])), set(baseline.get(rule, []))
        deltas.append(
            CoverageDelta(
                rule=rule,
                baseline=len(base),
                current=len(cur),
                new=sorted(cur - base),
                fixed=sorted(base - cur),
            )
        )
    return deltas


def run_coverage(
    krr: Path = KRR,
    baseline_path: Path = BASELINE_PATH,
    *,
    update: bool = False,
    out=None,
) -> int:
    out = out or sys.stdout
    current = measure_coverage(krr)
    if update:
        write_baseline(current, baseline_path)
        for rule in COVERAGE_RULES:
            print(f"{rule}: {len(current[rule])} offender(s) written", file=out)
        print(f"Baseline written to {baseline_path}", file=out)
        return EXIT_OK
    if not baseline_path.exists():
        print(
            f"::error::text-fidelity baseline {baseline_path} is missing; "
            "run with --coverage --update-baseline",
            file=out,
        )
        return EXIT_FAIL
    status = EXIT_OK
    for delta in compare_coverage(current, load_baseline(baseline_path)):
        print(
            f"{delta.rule}: {delta.current} current / {delta.baseline} baseline",
            file=out,
        )
        if delta.new:
            status = EXIT_FAIL
            shown = ", ".join(delta.new[:20])
            more = f" (+{len(delta.new) - 20} more)" if len(delta.new) > 20 else ""
            print(
                f"::error::{delta.rule}: {len(delta.new)} new offender(s) not in "
                f"the baseline: {shown}{more}",
                file=out,
            )
        if delta.fixed:
            print(
                f"  {len(delta.fixed)} baseline entr(y/ies) fixed; shrink the "
                "baseline with --coverage --update-baseline",
                file=out,
            )
    return status


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvisionRecord:
    iri: str
    paragrahv: str
    legal_text: str
    act_id: str
    file: str


def collect_sample_population(krr: Path = KRR) -> list[ProvisionRecord]:
    """Provisions with ``legalText`` whose file head has an RT act source."""
    records: list[ProvisionRecord] = []
    for path, doc in iter_root_law_docs(krr):
        head = act_root_node(doc)
        if head is None or head.get("estleg:contentStatus") != "structuredBody":
            continue
        source = head.get("dcterms:source")
        source_iri = source.get("@id") if isinstance(source, dict) else source
        if not isinstance(source_iri, str):
            continue
        try:
            act_id = rt.rt_act_id(source_iri)
        except ValueError:
            continue
        for node in doc["@graph"]:
            if not (isinstance(node, dict) and _is_provision(node)):
                continue
            text = _text_value(node.get("estleg:legalText"))
            if not text:
                continue
            records.append(
                ProvisionRecord(
                    iri=str(node.get("@id", "")),
                    paragrahv=_text_value(node.get("estleg:paragrahv")),
                    legal_text=text,
                    act_id=act_id,
                    file=path.name,
                )
            )
    records.sort(key=lambda r: (r.iri, r.file))
    return records


def choose_sample(
    population: list[ProvisionRecord], n: int, seed: int
) -> list[ProvisionRecord]:
    if n >= len(population):
        return list(population)
    return random.Random(seed).sample(population, n)


def parse_rt_provisions(root: ET.Element) -> dict[str, list[str]]:
    """Provision texts keyed by :func:`provision_key`, via the generator.

    Runs :func:`emit_hierarchy_and_provisions` on a scratch graph exactly as
    ``generate_all_laws.generate_law_jsonld`` does, so the comparison uses
    the production parser rather than a reimplementation.
    """
    paragrahvid = [el for el in root.iter() if ln(el.tag) == "paragrahv"]
    par_numbers: list[int] = []
    for par in paragrahvid:
        digits = re.sub(r"[^\d]", "", ct(par, "paragrahvNr") or "")
        if digits:
            par_numbers.append(int(digits))
    graph: list[dict] = []
    emit_hierarchy_and_provisions(
        graph=graph,
        chapter_root=root,
        paragrahvid=paragrahvid,
        prefix="FIDELITY",
        ontology_id="estleg:FIDELITY_Map",
        class_id="estleg:LegalProvision",
        title="fidelity",
        slug="fidelity",
        par_min=min(par_numbers) if par_numbers else "?",
        par_max=max(par_numbers) if par_numbers else "?",
        par_numbers=par_numbers,
        scheme_id="estleg:FIDELITY_TopicScheme",
        descend_osa=True,
        fallback_label="fidelity",
        provision_iri_prefix="estleg:FIDELITY_Par_",
        structural_ns="FIDELITY",
    )
    texts: dict[str, list[str]] = defaultdict(list)
    for node in graph:
        if "estleg:LegalProvision" not in node_type_list(node):
            continue
        key = provision_key(_text_value(node.get("estleg:paragrahv")))
        texts[key].append(_text_value(node.get("estleg:legalText")))
    return dict(texts)


def text_diff(expected: str, actual: str, *, max_lines: int = DIFF_MAX_LINES) -> str:
    """Word-level unified diff snippet (committed corpus vs RT)."""
    lines = list(
        difflib.unified_diff(
            expected.split(),
            actual.split(),
            fromfile="corpus legalText",
            tofile="RT re-parse",
            lineterm="",
            n=3,
        )
    )
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more diff lines)"]
    return "\n".join(lines)


@dataclass
class SampleResult:
    record: ProvisionRecord
    status: str
    detail: str = ""
    diff: str = ""
    current_id: str | None = None


@dataclass
class SampleReport:
    results: list[SampleResult] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def exit_code(self) -> int:
        if any(r.status in FAILING_STATUSES for r in self.results):
            return EXIT_FAIL
        if any(r.status == UNREACHABLE for r in self.results):
            return EXIT_UNREACHABLE
        return EXIT_OK


XmlFetcher = Callable[[str], ET.Element]
MetaFetcher = Callable[[str], dict]


def make_xml_fetcher(cache_dir: Path) -> XmlFetcher:
    """Fresh (``refresh=True``) strict fetch of one act's XML into ``cache_dir``."""

    def _fetch(act_id: str) -> ET.Element:
        root = rt.fetch_xml(
            act_id,
            f"fidelity_{act_id}",
            cache_dir=cache_dir,
            refresh=True,
            strict=True,
        )
        if root is None:  # strict mode raises instead; defensive only
            raise rt.RTFormatError(f"no XML returned for act {act_id}")
        return root

    return _fetch


def _classify_fetch_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, rt.RTFormatError):
        return FORMAT_ERROR, str(exc)
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is not None and not (int(status) == 429 or 500 <= int(status) < 600):
            return FETCH_ERROR, f"HTTP {status}: {exc}"
        return UNREACHABLE, str(exc)
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return UNREACHABLE, str(exc)
    return FORMAT_ERROR, f"{type(exc).__name__}: {exc}"


def _match(record: ProvisionRecord, parsed: dict[str, list[str]]) -> tuple[bool | None, str]:
    """``(True|False, rt_text)``, or ``(None, "")`` when the § is absent."""
    candidates = parsed.get(provision_key(record.paragrahv))
    if not candidates:
        return None, ""
    want = normalise_text(record.legal_text)
    for text in candidates:
        if normalise_text(text) == want:
            return True, text
    return False, candidates[0]


def check_act(
    act_id: str,
    records: Iterable[ProvisionRecord],
    *,
    fetch_xml_root: XmlFetcher,
    fetch_metadata: MetaFetcher,
) -> list[SampleResult]:
    """Check every sampled provision of one act against RT."""
    records = list(records)
    try:
        meta = fetch_metadata(act_id)
        current = meta.get("currentId")
        current_id = str(current) if current else None
        parsed = parse_rt_provisions(fetch_xml_root(act_id))
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed silently
        status, detail = _classify_fetch_error(exc)
        return [SampleResult(r, status, detail) for r in records]

    drift = current_id is not None and current_id != act_id
    parsed_current: dict[str, list[str]] | None = None
    results: list[SampleResult] = []
    for record in records:
        ok, rt_text = _match(record, parsed)
        if ok:
            results.append(SampleResult(record, MATCH, current_id=current_id))
            continue
        if drift:
            if parsed_current is None:
                try:
                    parsed_current = parse_rt_provisions(fetch_xml_root(current_id))
                except Exception:  # noqa: BLE001 - fall back to the committed verdict
                    parsed_current = {}
            ok_current, _ = _match(record, parsed_current)
            if ok_current:
                results.append(
                    SampleResult(
                        record,
                        STALE_SOURCE_ID,
                        f"text matches the current redaction {current_id}, not the "
                        f"committed dcterms:source {act_id}",
                        current_id=current_id,
                    )
                )
                continue
        if ok is None:
            results.append(
                SampleResult(
                    record,
                    NOT_FOUND,
                    f"{record.paragrahv!r} not found in RT act {act_id}",
                    current_id=current_id,
                )
            )
        else:
            results.append(
                SampleResult(
                    record,
                    MISMATCH,
                    f"legalText differs from RT act {act_id}",
                    diff=text_diff(record.legal_text, rt_text),
                    current_id=current_id,
                )
            )
    return results


def run_sample(
    population: list[ProvisionRecord],
    n: int,
    seed: int,
    *,
    fetch_xml_root: XmlFetcher,
    fetch_metadata: MetaFetcher,
) -> SampleReport:
    sample = choose_sample(population, n, seed)
    by_act: dict[str, list[ProvisionRecord]] = defaultdict(list)
    for record in sample:
        by_act[record.act_id].append(record)
    report = SampleReport()
    unreachable_streak = False
    for act_id in sorted(by_act):
        if unreachable_streak:
            # RT is down: do not hammer it once per remaining act.
            report.results.extend(
                SampleResult(r, UNREACHABLE, "skipped after RT was unreachable")
                for r in by_act[act_id]
            )
            continue
        results = check_act(
            act_id,
            by_act[act_id],
            fetch_xml_root=fetch_xml_root,
            fetch_metadata=fetch_metadata,
        )
        unreachable_streak = bool(results) and all(r.status == UNREACHABLE for r in results)
        report.results.extend(results)
    report.results.sort(key=lambda r: (r.record.act_id, r.record.iri))
    return report


def print_report(
    report: SampleReport, *, n: int, seed: int, population: int, out=None
) -> None:
    out = out or sys.stdout
    print(
        f"Text fidelity sample: {len(report.results)} of {population} provisions "
        f"(--sample {n} --seed {seed})",
        file=out,
    )
    drift_acts: dict[str, str] = {}
    for result in report.results:
        rec = result.record
        line = f"  [{result.status}] {rec.iri} {rec.paragrahv} (act {rec.act_id}, {rec.file})"
        if result.detail and result.status != MATCH:
            line += f": {result.detail}"
        print(line, file=out)
        if result.diff:
            for diff_line in result.diff.splitlines():
                print(f"      {diff_line}", file=out)
        if result.current_id and result.current_id != rec.act_id:
            drift_acts[rec.act_id] = result.current_id
    for act_id, current in sorted(drift_acts.items()):
        print(
            f"  note: newer consolidation available for act {act_id}: RT kehtivId "
            f"is {current} (not a fidelity failure; refresh via the RT ingest)",
            file=out,
        )
    counts = ", ".join(
        f"{status} {report.count(status)}"
        for status in (MATCH, STALE_SOURCE_ID, MISMATCH, NOT_FOUND, FETCH_ERROR, FORMAT_ERROR, UNREACHABLE)
        if report.count(status)
    )
    print(f"Summary: {counts or 'no samples'}", file=out)
    if report.exit_code == EXIT_FAIL:
        print("::error::legal-text fidelity check failed against Riigi Teataja", file=out)
    elif report.exit_code == EXIT_UNREACHABLE:
        print(
            "::warning::Riigi Teataja unreachable; text fidelity not verified "
            f"for {report.count(UNREACHABLE)} sampled provision(s)",
            file=out,
        )
    if report.count(STALE_SOURCE_ID):
        print(
            "::warning::some provisions match a newer RT redaction than their "
            "dcterms:source records",
            file=out,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="run the offline coverage-baseline check instead of the live sample",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="with --coverage: rewrite the baseline from the current corpus",
    )
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help="provisions to sample")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="sampling seed")
    parser.add_argument("--krr-dir", type=Path, default=KRR)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="directory for fetched RT XML (default: a temporary directory)",
    )
    args = parser.parse_args(argv)
    if args.update_baseline and not args.coverage:
        parser.error("--update-baseline requires --coverage")
    if args.sample < 1:
        parser.error("--sample must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.coverage:
        return run_coverage(args.krr_dir, args.baseline, update=args.update_baseline)

    population = collect_sample_population(args.krr_dir)
    if not population:
        print("::error::no sampleable provisions found (corpus missing?)")
        return EXIT_FAIL

    def _run(cache_dir: Path) -> int:
        report = run_sample(
            population,
            args.sample,
            args.seed,
            fetch_xml_root=make_xml_fetcher(cache_dir),
            fetch_metadata=rt.fetch_act_metadata,
        )
        print_report(report, n=args.sample, seed=args.seed, population=len(population))
        return report.exit_code

    if args.cache_dir is not None:
        return _run(args.cache_dir)
    with tempfile.TemporaryDirectory(prefix="estleg_fidelity_") as tmp:
        return _run(Path(tmp))


if __name__ == "__main__":
    sys.exit(main())
