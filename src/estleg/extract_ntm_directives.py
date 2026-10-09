#!/usr/bin/env python3
"""Parse the Riigi Teataja normitehniline märkus into asserted transposition (#711).

An Estonian act that implements EU law closes with a *normitehniline märkus*
(``<normtehnmarkus>``) naming the directives it transposes, e.g.::

    Euroopa Parlamendi ja nõukogu direktiiv 2001/29/EÜ autoriõiguse … (ELT L 167,
    22.06.2001, lk 10–19); Euroopa Parlamendi ja nõukogu direktiiv 2008/99/EÜ …

That is the act's *own* statement of what it transposes, independent of the
CELLAR national-implementing-measure notification that feeds
``estleg:transposesDirective``. This pass records it as
``estleg:transposesDirectiveAsserted`` on the act root so the two can be
diffed (asserted ∖ notified, notified ∖ asserted).

Inputs are cached RT XML files (``data/riigiteataja/`` by default — the
generate_all_laws cache convention, ``<law slug>[__tid<id>].xml``). A file is
attached to its peep(s) by:

* law: the cache-file stem (before ``__tid``) is the ``INDEX.json`` law name;
* state regulation: the XML ``terviktekstiGrupiID`` matches the
  ``*_t<id>_peep.json`` file under ``krr_outputs/regulations/riik/``.

Per-file replace semantics: a processed act's asserted set is rewritten; acts
whose XML was not processed keep what they have. The report
``krr_outputs/reports/ntm_directives.json`` is rebuilt from the peeps after
every run, so it describes corpus state, not the last run.

Usage::

    python3 scripts/extract_ntm_directives.py                 # cached XML only
    python3 scripts/extract_ntm_directives.py --xml data/riigiteataja/karistusseadustik.xml
    python3 scripts/extract_ntm_directives.py --fetch         # operator refresh (network)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    FETCH_HASH_FILENAME,
    KRR_DIR,
    REPO_ROOT,
    act_root_node,
    iter_peep_files,
    jsonld_id_values,
    parse_xml_file,
    save_json,
)
from estleg.law_structure import normtehnmarkus_texts
from estleg.riigiteataja_common import DATA_DIR, parse_act_metadata

PROPERTY = "estleg:transposesDirectiveAsserted"
NOTIFIED_PROPERTY = "estleg:transposesDirective"
RIIK_DIR = KRR_DIR / "regulations" / "riik"
EURLEX_DIRECTIVES = KRR_DIR / "eurlex" / "eurlex_directives_peep.json"
REPORT_PATH = KRR_DIR / "reports" / "ntm_directives.json"

# One EU instrument reference: the instrument word (any case form), an optional
# ``(EL)`` / ``(EÜ)`` / ``(EMÜ)`` / ``(Euratom)`` prefix (post-2015 numbering,
# ``direktiiv (EL) 2015/849``), an optional ``nr``, then ``YYYY/N`` or ``YY/N``.
# Directives are always year/number; regulations of the old series are
# number/year and are not emitted (only directives become CELEX here).
_INSTRUMENT_RE = re.compile(
    r"\b(?P<kind>direktiiv|raamotsus|määrus|maarus|otsus)\w*"
    r"\s+(?:\((?:EL|EÜ|EMÜ|EU|EC|EEC|Euratom|ELT)\)\s+)?(?:nr\.?\s+)?"
    r"(?P<a>\d{2,4})/(?P<b>\d{1,4})\b",
    re.IGNORECASE,
)

# NTM items are separated by ``;``; a bare ``parandus (ELT …)`` tail has no
# instrument and is ignored.
_ITEM_SEPARATOR_RE = re.compile(r";")


def directive_celex(year: str, number: str) -> str | None:
    """``2001``/``29`` → ``32001L0029``; ``89``/``391`` → ``31989L0391``.

    Two-digit years are pre-1999 directives (``89/391/EMÜ``). Returns
    ``None`` for a year outside 1958..2099 or an over-long number.
    """
    if len(year) == 2:
        year = f"19{year}"
    if len(year) != 4 or not year.isdigit() or not number.isdigit():
        return None
    if not 1958 <= int(year) <= 2099 or len(number) > 4:
        return None
    return f"3{year}L{int(number):04d}"


def ntm_directive_celexes(text: str) -> list[str]:
    """CELEX numbers of the directives an NTM text asserts, in text order.

    Each ``;``-separated item names one instrument first; references after it
    (``…, millega muudetakse direktiivi 2005/35/EÜ``, ``…, mis asendab
    raamotsuse …``) describe that instrument and are not separate assertions.
    So only the *first* instrument reference of each item counts, and only
    when it is a directive — a regulation item that repeals a directive
    (``määrus (EL) 2016/679 …, millega tunnistatakse kehtetuks direktiiv
    95/46/EÜ``) asserts nothing about the directive.
    """
    out: list[str] = []
    for item in _ITEM_SEPARATOR_RE.split(text or ""):
        match = _INSTRUMENT_RE.search(item)
        if match is None or not match.group("kind").lower().startswith("direktiiv"):
            continue
        celex = directive_celex(match.group("a"), match.group("b"))
        if celex and celex not in out:
            out.append(celex)
    return out


def parse_ntm_xml(source: Path | ET.Element) -> dict:
    """Parse one RT act XML: ``{global_id, tervikteksti_id, texts, celexes}``."""
    root = parse_xml_file(source) if isinstance(source, Path) else source
    meta = parse_act_metadata(root)
    texts = normtehnmarkus_texts(root)
    celexes: list[str] = []
    for text in texts:
        for celex in ntm_directive_celexes(text):
            if celex not in celexes:
                celexes.append(celex)
    return {
        "global_id": meta.get("globalId") or "",
        "tervikteksti_id": meta.get("terviktekstId") or "",
        "texts": texts,
        "celexes": celexes,
    }


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def law_files_by_name(krr_dir: Path = KRR_DIR) -> dict[str, list[str]]:
    """INDEX.json law name → its existing peep files (relative to ``krr_dir``)."""
    index_path = krr_dir / "INDEX.json"
    if not index_path.exists():
        return {}
    out: dict[str, list[str]] = {}
    for law in _load_json(index_path).get("laws", []):
        name = law.get("name")
        files = [
            f
            for f in law.get("files", [])
            if isinstance(f, str) and f.endswith("_peep.json") and (krr_dir / f).exists()
        ]
        if name and files:
            out[name] = files
    return out


def regulation_files_by_tid(riik_dir: Path = RIIK_DIR) -> dict[str, list[str]]:
    """``terviktekstiGrupiID`` → state-regulation peep files (relative to KRR_DIR)."""
    out: dict[str, list[str]] = {}
    if not riik_dir.exists():
        return out
    for path in sorted(riik_dir.glob("*_t*_peep.json")):
        match = re.search(r"_t(\d+)_peep\.json$", path.name)
        if match:
            rel = path.relative_to(riik_dir.parent.parent).as_posix()
            out.setdefault(match.group(1), []).append(rel)
    return out


def resolve_peep_files(
    xml_path: Path,
    parsed: dict,
    laws: dict[str, list[str]],
    regulations: dict[str, list[str]],
) -> list[str]:
    """Peep files (relative to KRR_DIR) the XML's NTM belongs to, else ``[]``."""
    stem = xml_path.stem.split("__tid", 1)[0]
    if stem in laws:
        return laws[stem]
    return regulations.get(parsed.get("tervikteksti_id") or "", [])


def directive_iris(path: Path = EURLEX_DIRECTIVES) -> dict[str, str]:
    """CELEX → directive @id from the EUR-Lex directives peep."""
    if not path.exists():
        return {}
    return {
        node["estleg:celexNumber"]: node["@id"]
        for node in _load_json(path).get("@graph", [])
        if node.get("estleg:celexNumber") and node.get("@id")
    }


def write_asserted(path: Path, iris: list[str]) -> bool:
    """Replace the act root's asserted set with ``iris``; True if the file changed."""
    doc = _load_json(path)
    root = act_root_node(doc)
    if root is None:
        return False
    before = root.get(PROPERTY)
    if iris:
        root[PROPERTY] = [{"@id": iri} for iri in sorted(set(iris))]
    else:
        root.pop(PROPERTY, None)
    if root.get(PROPERTY) == before:
        return False
    save_json(path, doc)
    return True


def _root_global_id(path: Path) -> str:
    """The act root's ``estleg:globalId`` (the redaction it was built from)."""
    root = act_root_node(_load_json(path)) or {}
    return str(root.get("estleg:globalId") or "")


def attested_cache_global_ids(krr_dir: Path = KRR_DIR) -> dict[str, str]:
    """Cached XML basename → the ``globalId`` ``fetch_content_hashes.json``
    attests for it. Some RT XML carries a UUID ``globaalID`` while the API
    (and so the peep root) uses the numeric id; the attestation bridges it."""
    path = krr_dir / FETCH_HASH_FILENAME
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for row in _load_json(path).values():
        if isinstance(row, dict) and row.get("cacheFile") and row.get("globalId"):
            out[Path(str(row["cacheFile"])).name] = str(row["globalId"])
    return out


def _iter_xml(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(sorted(p for p in path.rglob("*.xml") if p.is_file()))
        elif path.suffix == ".xml" and path.is_file():
            out.append(path)
    return out


def apply_ntm(
    xml_paths: Iterable[Path],
    *,
    krr_dir: Path = KRR_DIR,
    dry_run: bool = False,
) -> dict:
    """Parse each XML and write its asserted directives onto the matching peeps."""
    laws = law_files_by_name(krr_dir)
    regulations = regulation_files_by_tid(krr_dir / "regulations" / "riik")
    known = directive_iris(krr_dir / "eurlex" / "eurlex_directives_peep.json")
    attested = attested_cache_global_ids(krr_dir)
    stats = {
        "xml_files": 0,
        "xml_with_ntm": 0,
        "xml_unresolved": [],
        "xml_other_redaction": [],
        "xml_snapshot_mismatch": [],
        "files_changed": 0,
        "directives_not_in_eurlex": [],
    }
    for xml_path in _iter_xml(xml_paths):
        stats["xml_files"] += 1
        try:
            parsed = parse_ntm_xml(xml_path)
        except (ET.ParseError, ValueError) as exc:
            print(f"  SKIP {xml_path.name}: unparseable XML ({exc})")
            continue
        if parsed["texts"]:
            stats["xml_with_ntm"] += 1
        files = resolve_peep_files(xml_path, parsed, laws, regulations)
        if not files:
            stats["xml_unresolved"].append(xml_path.name)
            continue
        # A stale cached redaction (e.g. ``<slug>.xml`` beside the current
        # ``<slug>__tid<N>.xml``) resolves to the same peeps by stem; only
        # the XML whose globaalID matches the root's estleg:globalId may
        # write, so the result never depends on file iteration order and
        # every part of a multipart act gets the same redaction's set.
        if parsed["global_id"]:
            accepted = {"", parsed["global_id"], attested.get(xml_path.name, "")}
            files = [rel for rel in files if _root_global_id(krr_dir / rel) in accepted]
            if not files:
                stats["xml_other_redaction"].append(xml_path.name)
                continue
        iris = []
        for celex in parsed["celexes"]:
            if celex in known:
                iris.append(known[celex])
            elif celex not in stats["directives_not_in_eurlex"]:
                stats["directives_not_in_eurlex"].append(celex)
        for rel in files:
            # A cache directory can contain several redactions of one act.
            # Only the redaction named by this peep may replace its assertions.
            source = _source_url(krr_dir / rel)
            match = re.search(r"/akt/(\d+)(?:\.xml)?(?:[?#]|$)", source)
            # The attested id bridges RT XML that carries a UUID globaalID
            # while the peep (and its dcterms:source) uses the numeric API id.
            if match and match.group(1) not in {parsed["global_id"], attested.get(xml_path.name, "")}:
                stats["xml_snapshot_mismatch"].append(f"{xml_path.name}: {rel}")
                continue
            if not dry_run and write_asserted(krr_dir / rel, iris):
                stats["files_changed"] += 1
        print(f"  {xml_path.name}: {len(parsed['celexes'])} directive(s) → {len(files)} file(s)")
    stats["directives_not_in_eurlex"].sort()
    return stats


def build_report(krr_dir: Path = KRR_DIR, *, run_stats: dict | None = None) -> dict:
    """Corpus-state report: every act (law or state regulation) carrying
    asserted directives, diffed against its CELLAR-notified
    ``transposesDirective`` set. A multipart law's files (map + osa peeps)
    are one act; its root IRIs are listed under ``act_iris``."""
    file_to_act: dict[str, str] = {}
    for name, files in law_files_by_name(krr_dir).items():
        for rel in files:
            file_to_act[rel] = name
    acts: dict[str, dict] = {}
    for path in _peeps_under(krr_dir, KRR_DIR):
        doc = _load_json(path)
        root = act_root_node(doc)
        if root is None:
            continue
        asserted = set(jsonld_id_values(root.get(PROPERTY)))
        if not asserted:
            continue
        rel = path.relative_to(krr_dir).as_posix()
        name = file_to_act.get(rel) or path.name.removesuffix("_peep.json")
        act = acts.setdefault(
            name, {"iris": set(), "files": [], "asserted": set(), "notified": set()}
        )
        act["iris"].add(root["@id"])
        act["files"].append(rel)
        act["asserted"] |= asserted
        act["notified"] |= set(jsonld_id_values(root.get(NOTIFIED_PROPERTY)))
    rows = []
    for name in sorted(acts):
        act = acts[name]
        rows.append(
            {
                "act": name,
                "act_iris": sorted(act["iris"]),
                "files": sorted(act["files"]),
                "asserted": sorted(act["asserted"]),
                "asserted_not_notified": sorted(act["asserted"] - act["notified"]),
                "notified_not_asserted": sorted(act["notified"] - act["asserted"]),
            }
        )
    asserted_directives = sorted({d for row in rows for d in row["asserted"]})
    report = {
        "generated": BUILD_EVALUATION_DATE,
        "source": "Riigi Teataja act XML <normtehnmarkus> (normitehniline märkus)",
        "property": PROPERTY,
        "acts_with_assertions": len(rows),
        "asserted_directives": len(asserted_directives),
        "asserted_pairs": sum(len(row["asserted"]) for row in rows),
        "asserted_not_notified_pairs": sum(len(row["asserted_not_notified"]) for row in rows),
        "notified_not_asserted_pairs": sum(len(row["notified_not_asserted"]) for row in rows),
        "acts": rows,
    }
    if run_stats is not None:
        report["last_run"] = {
            key: value for key, value in run_stats.items() if key != "files_changed"
        }
    return report


def _peeps_under(krr_dir: Path, default_dir: Path) -> list[Path]:
    if krr_dir == default_dir:
        return iter_peep_files(include_kov=False)
    files = list(krr_dir.glob("*_peep.json"))
    riik = krr_dir / "regulations" / "riik"
    if riik.exists():
        files.extend(riik.glob("*_peep.json"))
    return sorted(files, key=lambda p: p.as_posix().casefold())


def fetch_corpus_xml(rt_dir: Path, krr_dir: Path = KRR_DIR) -> list[Path]:
    """Operator refresh: download the current XML of every law and state
    regulation peep (network). Laws are cached as ``<slug>.xml`` so the stem
    rule resolves them; regulations as ``reg_t<id>.xml`` and resolve by
    ``terviktekstiGrupiID``."""
    from estleg.riigiteataja_common import fetch_xml

    targets: list[tuple[str, str]] = []
    for name, files in sorted(law_files_by_name(krr_dir).items()):
        url = _source_url(krr_dir / files[-1])
        if url:
            targets.append((url, name))
    for tid, files in sorted(regulation_files_by_tid(krr_dir / "regulations" / "riik").items()):
        url = _source_url(krr_dir / files[0])
        if url:
            targets.append((url, f"reg_t{tid}"))
    fetched: list[Path] = []
    for url, cache_name in targets:
        if fetch_xml(url, cache_name, cache_dir=rt_dir) is not None:
            fetched.append(rt_dir / f"{cache_name}.xml")
    return fetched


def _source_url(path: Path) -> str:
    root = act_root_node(_load_json(path)) or {}
    values = jsonld_id_values(root.get("dcterms:source"))
    return next((v for v in values if "riigiteataja.ee" in v), "")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--xml",
        action="append",
        type=Path,
        default=[],
        help="RT act XML file or directory (repeatable). Default: --rt-dir.",
    )
    parser.add_argument(
        "--rt-dir",
        type=Path,
        default=DATA_DIR,
        help="RT XML cache directory scanned when --xml is not given.",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Download current XML for every law/state regulation first (network).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and report only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sources: list[Path] = list(args.xml)
    if args.fetch:
        sources.extend(fetch_corpus_xml(args.rt_dir))
    if not sources:
        sources = [args.rt_dir]
    stats = apply_ntm(sources, dry_run=args.dry_run)
    print(
        f"  XML parsed: {stats['xml_files']}; with NTM: {stats['xml_with_ntm']}; "
        f"unresolved: {len(stats['xml_unresolved'])}; files changed: {stats['files_changed']}"
    )
    if args.dry_run:
        return 0
    report = build_report(run_stats=stats)
    save_json(REPORT_PATH, report)
    print(
        f"  Saved {REPORT_PATH.relative_to(REPO_ROOT)}: {report['acts_with_assertions']} act(s), "
        f"{report['asserted_pairs']} asserted pair(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
