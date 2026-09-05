#!/usr/bin/env python3
"""Ingest first- and second-instance Estonian court decisions (#525).

Riigikohus (Supreme Court) is already ingested from rikos.rik.ee. This
module reads the Riigi Teataja kohtulahendid search API — the same feed
the public SPA uses — and keeps maakohus / halduskohus / ringkonnakohus
rows.

    python3 -m estleg.generate_lower_court_decisions --from-fixture PATH --apply
    python3 -m estleg.generate_lower_court_decisions --fetch --year 2026 --limit 50 --apply

Live fetch is operator-run. Tests inject a fixture JSON page.

Whatever the scope, the output is a *sample*: one search page, capped by
``--limit``, never a complete corpus. Every write is therefore marked as
such (#689) — "(näidis)" in the ontology label, ``estleg:isSampleData``
on the header node, and ``"sample": true`` in the index — so downstream
consumers cannot mistake it for full lower-court coverage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    CONTEXT,
    KRR_DIR,
    assert_allowed_http_url,
    save_json,
)

SEARCH_URL = (
    "https://www.riigiteataja.ee/api/v1/kohtuteave/otsing/kohtulahendid"
)
DECISION_PAGE = "https://www.riigiteataja.ee/kohtulahendid/{oid}"
OUT_DIR = KRR_DIR / "kohtud"
INDEX_NAME = "KOHTUD_INDEX.json"
SAMPLE_PEEP = "kohtud_sample_peep.json"

# #689: this ingest is always a sample — one search page, capped by
# ``--limit`` — so the sample marking is unconditional, not year-dependent.
SAMPLE_LABEL_SUFFIX = "(näidis)"
SAMPLE_COMMENT = (
    "Scoped sample ingest (#525/#689), not a complete corpus: a capped slice "
    "of first- and second-instance decisions (maakohus / halduskohus / "
    "ringkonnakohus) from the Riigi Teataja kohtulahendid search API. Supreme "
    "Court decisions are ingested separately under krr_outputs/riigikohus/."
)
INDEX_NOTE = (
    "Sample ingest (näidis), not a corpus (#525/#689): a capped slice of "
    "maakohus / halduskohus / ringkonnakohus decisions. Riigikohus stays in "
    "krr_outputs/riigikohus/."
)

CASE_TYPE_BY_PREFIX = {
    "1": "Criminal",
    "2": "Civil",
    "3": "Administrative",
    "4": "Misdemeanor",
    "5": "ConstitutionalReview",
}

# Observed ``kohtuasiLiik`` codes on the 2026 search API. Fallback is the
# first digit of the case number when the code is missing or unknown.
CASE_TYPE_BY_LIIK = {
    20009: "Criminal",
    20011: "Civil",
    20012: "Administrative",
}

# Nominative stems; classify_court also accepts the genitive (kohus→kohtu).
SUPREME_STEM = "riigikohus"
APPEAL_STEM = "ringkonnakohus"
ADMIN_STEM = "halduskohus"
COUNTY_STEM = "maakohus"


def _mentions_court(folded: str, nominative: str) -> bool:
    """True if ``folded`` contains the nominative or genitive court name."""
    if nominative in folded:
        return True
    if nominative.endswith("kohus"):
        return nominative[: -len("kohus")] + "kohtu" in folded
    return False


def classify_court(name: str) -> tuple[str, str] | None:
    """Return ``(courtLevel, courtKind)`` or ``None`` for Supreme Court / unknown."""
    folded = (name or "").casefold()
    if _mentions_court(folded, SUPREME_STEM):
        return None
    if _mentions_court(folded, APPEAL_STEM):
        return ("appeal", "circuit")
    if _mentions_court(folded, ADMIN_STEM):
        return ("firstInstance", "administrative")
    if _mentions_court(folded, COUNTY_STEM):
        return ("firstInstance", "county")
    return None


def case_type_id(case_nr: str, liik: object = None) -> str:
    """Case-type token from ``kohtuasiLiik`` or the first case-number digit."""
    if isinstance(liik, str) and liik.isdigit():
        liik = int(liik)
    if isinstance(liik, int) and liik in CASE_TYPE_BY_LIIK:
        return CASE_TYPE_BY_LIIK[liik]
    digit = next((ch for ch in case_nr if ch.isdigit()), "")
    return CASE_TYPE_BY_PREFIX.get(digit, "Other")


def decision_date(hit: dict) -> str | None:
    raw = hit.get("staatusKp") or hit.get("lahendiKuulutamiseAeg") or ""
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def parse_search_hit(hit: dict) -> dict | None:
    """Map one API row to a graph-ready record, or None if out of scope."""
    if not isinstance(hit, dict):
        return None
    court = hit.get("toiminguEsitajaAsutus") or ""
    if not isinstance(court, str):
        return None
    classified = classify_court(court)
    if classified is None:
        return None
    oid = hit.get("objektId")
    case_nr = hit.get("kohtuasjaNumber")
    if oid is None or not isinstance(case_nr, str) or not case_nr.strip():
        return None
    level, kind = classified
    return {
        "objekt_id": int(oid),
        "case_nr": case_nr.strip(),
        "court": court.strip(),
        "court_level": level,
        "court_kind": kind,
        "liik": hit.get("kohtuasiLiik"),
        "date": decision_date(hit),
        "status": hit.get("staatusKlVaartus") if isinstance(hit.get("staatusKlVaartus"), str) else None,
        "file_id": hit.get("avalikustatudFailiId"),
        "file_name": hit.get("avalikustatudFailiNimi")
        if isinstance(hit.get("avalikustatudFailiNimi"), str)
        else None,
        "summary": hit.get("kokkuvote") if isinstance(hit.get("kokkuvote"), str) else None,
    }


def parse_search_page(payload: dict) -> tuple[int, list[dict]]:
    """Return ``(total, parsed hits)`` from a search JSON body."""
    total = payload.get("kokku")
    total_n = int(total) if isinstance(total, int) else 0
    rows = payload.get("tulemused") or []
    hits = []
    if isinstance(rows, list):
        for row in rows:
            parsed = parse_search_hit(row)
            if parsed:
                hits.append(parsed)
    return total_n, hits


def decision_node(hit: dict) -> dict:
    """JSON-LD CourtDecision for one lower-court hit."""
    oid = hit["objekt_id"]
    node: dict = {
        "@id": f"estleg:Kohtuasi_{oid}",
        "@type": ["owl:NamedIndividual", "estleg:CourtDecision"],
        "rdfs:label": {
            "@value": f"{hit['court']} {hit['case_nr']}",
            "@language": "et",
        },
        "estleg:caseNumber": hit["case_nr"],
        "estleg:caseType": {
            "@id": f"estleg:CaseType_{case_type_id(hit['case_nr'], hit.get('liik'))}"
        },
        "estleg:courtName": hit["court"],
        "estleg:courtLevel": hit["court_level"],
        "estleg:courtKind": hit["court_kind"],
        "estleg:decisionLink": {
            "@value": DECISION_PAGE.format(oid=oid),
            "@type": "xsd:anyURI",
        },
        "estleg:rtObjektId": str(oid),
    }
    if hit.get("date"):
        node["estleg:decisionDate"] = {
            "@value": hit["date"],
            "@type": "xsd:date",
        }
    if hit.get("summary"):
        node["estleg:summary"] = {"@value": hit["summary"][:800], "@language": "et"}
    return node


def search_payload(*, page: int, size: int, year: int | None) -> dict:
    precise: dict = {}
    if year is not None:
        precise["lahendiKpAlgus"] = f"{year}-01-01"
        precise["lahendiKpLopp"] = f"{year}-12-31"
    return {
        "general": {"page": page, "size": size, "sort": "new-first"},
        "precise": precise,
    }


def fetch_search_page(body: dict, *, session: object | None = None) -> dict:
    """POST the official RT search API (allow-listed host)."""
    assert_allowed_http_url(SEARCH_URL)
    client = session or requests
    response = client.post(
        SEARCH_URL,
        json=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
        allow_redirects=False,
    )
    response.raise_for_status()
    return response.json()


def collect_hits(
    payload: dict | None,
    *,
    limit: int,
) -> tuple[int, list[dict]]:
    total, hits = parse_search_page(payload or {})
    if limit > 0:
        hits = hits[:limit]
    return total, hits


def write_corpus(
    hits: list[dict],
    *,
    out_dir: Path,
    year: int | None,
    source_total: int,
) -> dict:
    """Write a peep + index. Returns the index document.

    The header node is always marked as sample data (#689): a year-scoped
    run is still one capped search page, so it must not read as complete
    lower-court coverage for that year.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    scope = f" {year}" if year else ""
    graph: list[dict] = [
        {
            "@id": f"estleg:Kohtud_{year or 'sample'}",
            "@type": ["owl:Ontology"],
            "rdfs:label": {
                "@value": (
                    f"Esimese ja teise astme kohtulahendid{scope} "
                    f"{SAMPLE_LABEL_SUFFIX}"
                ),
                "@language": "et",
            },
            "rdfs:comment": {"@value": SAMPLE_COMMENT, "@language": "en"},
            "estleg:isSampleData": True,
            "dc:source": "Riigi Teataja kohtulahendid",
        }
    ]
    by_kind: dict[str, int] = {}
    for hit in hits:
        graph.append(decision_node(hit))
        by_kind[hit["court_kind"]] = by_kind.get(hit["court_kind"], 0) + 1
    peep_name = f"kohtud_{year}_peep.json" if year else SAMPLE_PEEP
    save_json(out_dir / peep_name, {"@context": CONTEXT, "@graph": graph})
    index = {
        "generated": BUILD_EVALUATION_DATE,
        "source": SEARCH_URL,
        "sample": True,
        "note": INDEX_NOTE,
        "api_total": source_total,
        "ingested": len(hits),
        "year": year,
        "by_kind": by_kind,
        "file": peep_name,
    }
    save_json(out_dir / INDEX_NAME, index)
    return index


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-fixture", type=Path, help="Search JSON page (offline).")
    parser.add_argument("--fetch", action="store_true", help="POST the live RT API.")
    parser.add_argument("--year", type=int, default=None, help="Restrict to this calendar year.")
    parser.add_argument("--limit", type=int, default=50, help="Max lower-court rows to keep.")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--apply", action="store_true", help="Write krr_outputs/kohtud/.")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload: dict | None = None
    if args.from_fixture:
        payload = json.loads(args.from_fixture.read_text(encoding="utf-8"))
    elif args.fetch:
        payload = fetch_search_page(
            search_payload(page=0, size=args.page_size, year=args.year)
        )
    else:
        print("specify --from-fixture or --fetch", file=sys.stderr)
        return 2
    total, hits = collect_hits(payload, limit=args.limit)
    print(f"api_total={total} lower_court_hits={len(hits)}")
    if not args.apply:
        return 0
    index = write_corpus(hits, out_dir=args.out_dir, year=args.year, source_total=total)
    print(f"wrote {args.out_dir / index['file']} ingested={index['ingested']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
