"""Console entry point ``estleg-load``.

``estleg-load <name>`` (no sub-command) keeps its pre-1.0 behaviour: load an
enacted law and print triple and provision counts. Sub-commands::

    estleg-load law ABIPOL [--rows provisions|subsections|sanctions|citations]
    estleg-load regulation 1057801 [--kov | --state] [--rows ...]
    estleg-load decision 3-18-1432/93 [--rows decisions|citations]
    estleg-load decisions --year 2020            # rows of every decision
    estleg-load draft JDM/26-0214
    estleg-load drafts [--phase PublicConsultation]
    estleg-load eu-act 31981L0643
    estleg-load eu-acts [--doc-type Directive] [--in-force] [--estonia-relevant]
    estleg-load regulations [--kov | --state] [--issuer "Tallinna Linnavalitsus"]
    estleg-load download [--dest DIR] [--version v1.0.0] [--asset NAME ...]
    estleg-load where                            # print the corpus root

``--format csv|jsonl`` selects the row output (default ``csv``).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TextIO

from rdflib import Graph

from estleg_client._corpus import CorpusNotFoundError, CorpusUnavailableError, corpus_root
from estleg_client.corpora import (
    iter_court_decisions,
    iter_drafts,
    iter_eu_acts,
    iter_regulations,
    load_court_decision,
    load_draft,
    load_eu_act,
    load_regulation,
)
from estleg_client.download import DEFAULT_ASSETS, DEFAULT_VERSION, DownloadError, fetch_corpus
from estleg_client.load import NotFoundError, load_law, provisions_of
from estleg_client.rows import ROW_KINDS, Row, iter_rows

_SUBCOMMANDS = {
    "law",
    "regulation",
    "regulations",
    "decision",
    "decisions",
    "draft",
    "drafts",
    "eu-act",
    "eu-acts",
    "download",
    "where",
}
_ERRORS = (NotFoundError, CorpusUnavailableError, FileNotFoundError, OSError, ValueError)


def write_rows(rows: Iterable[Row], fmt: str, out: TextIO) -> int:
    """Write rows as CSV (header from the first row's class) or JSON lines."""
    count = 0
    writer: csv.DictWriter[str] | None = None
    for row in rows:
        record = row.as_dict()
        if fmt == "jsonl":
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            if writer is None:
                writer = csv.DictWriter(out, fieldnames=type(row).columns(), lineterminator="\n")
                writer.writeheader()
            writer.writerow(record)
        count += 1
    return count


def _add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        default=None,
        help="Corpus root (git checkout or fetch_corpus directory)",
    )


def _add_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("csv", "jsonl"), default="csv")


def _add_rows(parser: argparse.ArgumentParser, default: str | None = None) -> None:
    parser.add_argument(
        "--rows",
        choices=ROW_KINDS,
        default=default,
        help="Print typed rows of this kind instead of a summary",
    )
    _add_format(parser)


def _kov_flag(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--kov", dest="kov", action="store_const", const=True, default=None)
    group.add_argument("--state", dest="kov", action="store_const", const=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="estleg-load",
        description="Read the Estonian Legal Ontology corpus (laws, regulations, "
        "Riigikohus decisions, EIS drafts, EU acts).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    law = sub.add_parser("law", help="Load an enacted law")
    law.add_argument("name", help="INDEX slug, title substring, or registry abbreviation")
    _add_root(law)
    _add_rows(law)

    reg = sub.add_parser("regulation", help="Load one state or KOV regulation")
    reg.add_argument("key", help="RT terviktekst id, RT global id, Reg_<id>, or title")
    _kov_flag(reg)
    _add_root(reg)
    _add_rows(reg)

    regs = sub.add_parser("regulations", help="Rows for every regulation")
    _kov_flag(regs)
    regs.add_argument("--issuer", default=None)
    _add_root(regs)
    _add_format(regs)

    dec = sub.add_parser("decision", help="Load Riigikohus decision(s)")
    dec.add_argument("key", help="Case number, ECLI, or RK_... IRI")
    _add_root(dec)
    _add_rows(dec)

    decs = sub.add_parser("decisions", help="Rows for Riigikohus decisions")
    decs.add_argument("--year", type=int, default=None)
    _add_root(decs)
    _add_format(decs)

    draft = sub.add_parser("draft", help="Load one EIS draft")
    draft.add_argument("key", help="EIS number, Draft_... IRI, or title")
    _add_root(draft)
    _add_rows(draft)

    drafts = sub.add_parser("drafts", help="Rows for EIS drafts")
    drafts.add_argument("--phase", default=None)
    _add_root(drafts)
    _add_format(drafts)

    eu = sub.add_parser("eu-act", help="Load one EUR-Lex act")
    eu.add_argument("celex", help="CELEX number, e.g. 31981L0643")
    _add_root(eu)
    _add_rows(eu)

    eus = sub.add_parser("eu-acts", help="Rows for EUR-Lex acts")
    eus.add_argument("--doc-type", default=None)
    eus.add_argument("--in-force", action="store_const", const=True, default=None)
    eus.add_argument("--estonia-relevant", action="store_const", const=True, default=None)
    _add_root(eus)
    _add_format(eus)

    dl = sub.add_parser("download", help="Download and verify a corpus release")
    dl.add_argument("--dest", default=None, help="Target directory (default: user cache)")
    dl.add_argument("--version", default=DEFAULT_VERSION)
    dl.add_argument(
        "--asset",
        action="append",
        default=None,
        help=f"Required asset (repeatable; default: {', '.join(DEFAULT_ASSETS)})",
    )
    dl.add_argument("--force", action="store_true", help="Re-download verified files")

    where = sub.add_parser("where", help="Print the corpus root")
    _add_root(where)
    return parser


def _summary(graph: Graph, label: str, kind: str, out: TextIO) -> None:
    print(f"triples: {len(graph)}", file=out)
    count = sum(1 for _ in iter_rows(graph, kind))  # type: ignore[arg-type]
    print(f"{label}: {count}", file=out)


def _single(
    graph_of: Callable[[argparse.Namespace], Graph],
    label: str,
    kind: str,
) -> Callable[[argparse.Namespace, TextIO], None]:
    def run(args: argparse.Namespace, out: TextIO) -> None:
        graph = graph_of(args)
        if args.rows:
            write_rows(iter_rows(graph, args.rows), args.format, out)
        elif kind == "provisions":
            print(f"triples: {len(graph)}", file=out)
            print(f"provisions: {len(provisions_of(graph))}", file=out)
        else:
            _summary(graph, label, kind, out)

    return run


def _rows(
    rows_of: Callable[[argparse.Namespace], Iterable[Row]],
) -> Callable[[argparse.Namespace, TextIO], None]:
    def run(args: argparse.Namespace, out: TextIO) -> None:
        write_rows(rows_of(args), args.format, out)

    return run


def _download(args: argparse.Namespace, out: TextIO) -> None:
    root = fetch_corpus(
        args.dest,
        version=args.version,
        assets=tuple(args.asset) if args.asset else DEFAULT_ASSETS,
        force=args.force,
    )
    print(root, file=out)


_HANDLERS: dict[str, Callable[[argparse.Namespace, TextIO], None]] = {
    "law": _single(lambda a: load_law(a.name, root=a.root), "provisions", "provisions"),
    "regulation": _single(
        lambda a: load_regulation(a.key, kov=a.kov, root=a.root), "provisions", "provisions"
    ),
    "decision": _single(
        lambda a: load_court_decision(a.key, root=a.root), "decisions", "decisions"
    ),
    "draft": _single(lambda a: load_draft(a.key, root=a.root), "drafts", "drafts"),
    "eu-act": _single(lambda a: load_eu_act(a.celex, root=a.root), "eu_acts", "eu_acts"),
    "regulations": _rows(lambda a: iter_regulations(kov=a.kov, issuer=a.issuer, root=a.root)),
    "decisions": _rows(lambda a: iter_court_decisions(year=a.year, root=a.root)),
    "drafts": _rows(lambda a: iter_drafts(phase=a.phase, root=a.root)),
    "eu-acts": _rows(
        lambda a: iter_eu_acts(
            doc_type=a.doc_type,
            in_force=a.in_force,
            estonia_relevant=a.estonia_relevant,
            root=a.root,
        )
    ),
    "download": _download,
    "where": lambda a, out: print(corpus_root(a.root), file=out),
}


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    # Pre-1.0 form: ``estleg-load <name> [--root DIR]`` == ``estleg-load law <name>``.
    if args_list and args_list[0] not in _SUBCOMMANDS and not args_list[0].startswith("-"):
        args_list.insert(0, "law")
    args = _build_parser().parse_args(args_list)
    out: Any = sys.stdout
    try:
        _HANDLERS[args.command](args, out)
    except CorpusNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    except DownloadError as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        return 1
    except _ERRORS as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
