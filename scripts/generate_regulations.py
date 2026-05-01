#!/usr/bin/env python3
"""Fetch Estonian state-level regulations (määrused) from Riigi Teataja and
generate JSON-LD ontology files under ``krr_outputs/regulations/riik/``.

Phase 1 scope:
  * State-level regulations only — Vabariigi Valitsus, ministers, Eesti Pank,
    and other central issuers (`kov=false` in the API).
  * Current snapshot only — generated against an explicit `kehtiv=YYYY-MM-DD`
    date for reproducibility. Historical redactions are out of scope.

KOV regulations and historical redactions are intentionally deferred. Run
this script with ``--kov`` to include municipal regulations once the rest
of the pipeline is ready to consume them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
import xml.etree.ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from riigiteataja_common import (  # noqa: E402
    BASE_URL,
    CONTEXT,
    DATA_DIR,
    KRR_DIR,
    collect_full_text,
    collect_text,
    ct,
    fetch_acts,
    fetch_xml,
    ln,
    parse_act_metadata,
    parse_html_konteiner,
    sanitize_id,
    save_json,
    slugify,
)

DEFAULT_KEHTIV = "2026-05-01"
OUTPUT_RIIK = KRR_DIR / "regulations" / "riik"
OUTPUT_KOV = KRR_DIR / "regulations" / "kov"


# ---------------------------------------------------------------------------
# Issuer → regulation class
# ---------------------------------------------------------------------------

def classify_issuer(issuer: str | None, is_kov: bool) -> list[str]:
    """Return the list of regulation classes to attach to an act node.

    Examples:
      Vabariigi Valitsus            -> [NationalRegulation, GovernmentRegulation]
      Sotsiaalminister              -> [NationalRegulation, MinisterialRegulation]
      Eesti Pank                    -> [NationalRegulation]
      <KOV name>, is_kov=True       -> [MunicipalRegulation]
    """
    if is_kov:
        return ["estleg:MunicipalRegulation"]

    classes = ["estleg:NationalRegulation"]
    if not issuer:
        return classes

    issuer_l = issuer.lower()
    if issuer_l == "vabariigi valitsus":
        classes.append("estleg:GovernmentRegulation")
    elif issuer_l.endswith("minister"):
        classes.append("estleg:MinisterialRegulation")
    return classes


# ---------------------------------------------------------------------------
# Annex extraction
# ---------------------------------------------------------------------------

def extract_annexes(root: ET.Element, prefix: str) -> list[dict]:
    """Collect `<lisa>` / `<lisaViide>` / `<fail>` elements as Annex nodes."""
    annexes: list[dict] = []
    seen: set[str] = set()

    for el in root.iter():
        tag = ln(el.tag)
        if tag not in ("lisa", "lisaViide", "fail"):
            continue

        # The annex number is sometimes a direct attribute, sometimes a child.
        annex_nr = (
            ct(el, "lisaNr")
            or ct(el, "viideNr")
            or el.attrib.get("nr")
            or el.attrib.get("number")
            or str(len(annexes) + 1)
        )
        title = (
            ct(el, "lisaPealkiri")
            or ct(el, "viidePealkiri")
            or ct(el, "kuvatavTekst")
            or ""
        )
        # File annexes carry a source URL via the `viideURI` child or a
        # `kuvatavTekst` link.
        href = None
        for child in el.iter():
            ctag = ln(child.tag)
            if ctag in ("viideURI", "url", "link") and child.text:
                href = child.text.strip()
                break

        annex_key = f"{annex_nr}::{title}"
        if annex_key in seen:
            continue
        seen.add(annex_key)

        annex_id = f"estleg:{prefix}_Annex_{sanitize_id(annex_nr)}"
        node: dict = {
            "@id": annex_id,
            "@type": ["owl:NamedIndividual", "estleg:Annex"],
            "rdfs:label": f"Lisa {annex_nr}{(' – ' + title) if title else ''}",
            "estleg:annexNumber": str(annex_nr),
        }
        if href:
            full = href if href.startswith(("http://", "https://")) else BASE_URL + href.lstrip(".")
            node["dcterms:source"] = {"@id": full}
        annexes.append(node)

    return annexes


# ---------------------------------------------------------------------------
# Preamble extraction (legal basis)
# ---------------------------------------------------------------------------

def extract_preamble(root: ET.Element) -> str:
    """Return the regulation preamble (legal basis text) as plain text.

    Modern XML stores it under `<sisu><preambul>`. Pre-2010 regulations
    embed it as the first paragraph of the `<HTMLKonteiner>` body — the
    HTML fallback path returns it directly so we don't repeat that here.
    """
    for el in root.iter():
        if ln(el.tag) != "preambul":
            continue
        parts: list[str] = []
        for child in el.iter():
            tag = ln(child.tag)
            # Pull both plain text and the rendered text of any cross-references.
            if tag in ("tavatekst", "kuvatavTekst") and child.text:
                parts.append(child.text.strip())
        joined = " ".join(parts)
        return " ".join(joined.split())
    return ""


# ---------------------------------------------------------------------------
# Provision extraction — structured XML path
# ---------------------------------------------------------------------------

def collect_structured_paragraphs(root: ET.Element, prefix: str, title: str, class_id: str) -> list[dict]:
    """Build provision nodes from `<paragrahv>` elements (modern XML)."""
    nodes: list[dict] = []
    seen_ids: set[str] = set()

    for p in [el for el in root.iter() if ln(el.tag) == "paragrahv"]:
        nr = ct(p, "paragrahvNr") or "?"
        ptitle = ct(p, "paragrahvPealkiri") or ""
        display = ct(p, "kuvatavNr") or f"§ {nr}"
        text = collect_text(p)
        full_text = collect_full_text(p)

        p_id = f"estleg:{prefix}_Par_{sanitize_id(nr)}"
        if p_id in seen_ids:
            p_id = f"{p_id}_{len(seen_ids)}"
        seen_ids.add(p_id)

        if ptitle:
            label = f"{display} {ptitle}"
        elif text:
            excerpt = text[:80].rstrip()
            if len(text) > 80:
                excerpt = excerpt + "..."
            label = f"{display} [{excerpt}]"
        else:
            label = display

        node: dict = {
            "@id": p_id,
            "@type": ["owl:NamedIndividual", class_id],
            "estleg:paragrahv": display,
            "rdfs:label": label,
            "estleg:sourceAct": title,
        }
        if text:
            node["estleg:summary"] = text
        if full_text:
            node["estleg:legalText"] = full_text
        nodes.append(node)

    return nodes


# ---------------------------------------------------------------------------
# Provision extraction — legacy HTMLKonteiner fallback
# ---------------------------------------------------------------------------

def collect_html_paragraphs(root: ET.Element, prefix: str, title: str, class_id: str) -> tuple[str, list[dict]]:
    """Build provision nodes from the legacy HTMLKonteiner CDATA body.

    Returns ``(preamble_text, paragraph_nodes)``.
    """
    container = None
    for el in root.iter():
        if ln(el.tag) == "HTMLKonteiner":
            container = el
            break
    if container is None or not (container.text or "").strip():
        return "", []

    preamble, paragraphs = parse_html_konteiner(container.text)

    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for entry in paragraphs:
        nr = entry["nr"]
        display = f"§ {nr}"
        ptitle = entry["title"].strip()
        text_full = entry["text"].strip()
        # Trim summary to the first 500 chars (matches the structured path).
        summary = text_full[:500] if text_full else ""

        p_id = f"estleg:{prefix}_Par_{sanitize_id(nr)}"
        if p_id in seen_ids:
            p_id = f"{p_id}_{len(seen_ids)}"
        seen_ids.add(p_id)

        if ptitle:
            label = f"{display} {ptitle}"
        elif summary:
            excerpt = summary[:80].rstrip()
            if len(summary) > 80:
                excerpt = excerpt + "..."
            label = f"{display} [{excerpt}]"
        else:
            label = display

        node: dict = {
            "@id": p_id,
            "@type": ["owl:NamedIndividual", class_id],
            "estleg:paragrahv": display,
            "rdfs:label": label,
            "estleg:sourceAct": title,
        }
        if summary:
            node["estleg:summary"] = summary
        if text_full:
            node["estleg:legalText"] = text_full
        nodes.append(node)

    return preamble, nodes


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------

def build_regulation_jsonld(
    title: str,
    info: dict,
    root: ET.Element,
    *,
    is_kov: bool,
) -> tuple[dict, dict[str, int]]:
    """Generate the JSON-LD document for one regulation.

    Returns the doc plus a small stats dict used for reporting.
    """
    metadata = parse_act_metadata(root)
    tid = metadata.get("terviktekstId") or info.get("tid") or ""
    gid = metadata.get("globalId") or info.get("gid") or ""
    issuer = metadata.get("issuer") or info.get("valjaandja") or ""
    rt_url = info.get("url", "")

    if not tid:
        # Without a stable ID we can't produce a reliable IRI — bail out.
        raise ValueError(f"Regulation has no terviktekstID: {title}")

    prefix = f"Reg_{tid}"
    class_id = f"estleg:Regulation_{tid}"
    ontology_id = f"estleg:{prefix}_Map_2026"

    rt_source_url = ""
    if rt_url:
        rt_source_url = BASE_URL + rt_url if rt_url.startswith("/") else rt_url

    # Provisions: try structured first, fall back to HTMLKonteiner
    provisions = collect_structured_paragraphs(root, prefix, title, class_id)
    parse_mode = "structured"
    preamble_html = ""
    if not provisions:
        preamble_html, provisions = collect_html_paragraphs(root, prefix, title, class_id)
        parse_mode = "html_fallback" if provisions else "no_paragraphs"

    # Preamble: prefer structured `<preambul>`, fall back to HTML preamble
    preamble = extract_preamble(root) or preamble_html
    annexes = extract_annexes(root, prefix)

    # ---- Build the act ontology node ------------------------------------
    act_classes = ["owl:Ontology", *classify_issuer(issuer, is_kov)]
    ontology_node: dict = {
        "@id": ontology_id,
        "@type": act_classes,
        "rdfs:label": f"{title} (määrus)",
        "dc:source": title,
        "estleg:documentType": "määrus",
        "estleg:isKov": {"@value": "true" if is_kov else "false", "@type": "xsd:boolean"},
    }
    if rt_source_url:
        ontology_node["dcterms:source"] = {"@id": rt_source_url}
        ontology_node["owl:sameAs"] = {"@id": rt_source_url}
    if issuer:
        ontology_node["estleg:issuer"] = issuer
    if metadata.get("actNumber"):
        ontology_node["estleg:actNumber"] = metadata["actNumber"]
    if gid:
        ontology_node["estleg:globalId"] = str(gid)
    ontology_node["estleg:terviktekstId"] = str(tid)
    if metadata.get("entryIntoForce"):
        ontology_node["estleg:entryIntoForce"] = {
            "@value": metadata["entryIntoForce"],
            "@type": "xsd:date",
        }
    if metadata.get("repealDate"):
        ontology_node["estleg:repealDate"] = {
            "@value": metadata["repealDate"],
            "@type": "xsd:date",
        }
    if metadata.get("lastAmendmentDate"):
        ontology_node["estleg:lastAmendmentDate"] = {
            "@value": metadata["lastAmendmentDate"],
            "@type": "xsd:date",
        }
    if preamble:
        ontology_node["estleg:preambleText"] = preamble
    if annexes:
        ontology_node["estleg:hasAnnex"] = [{"@id": a["@id"]} for a in annexes]

    graph: list[dict] = [
        ontology_node,
        {
            "@id": class_id,
            "@type": ["owl:Class"],
            "rdfs:label": "Õigusnorm (paragrahv)",
            "rdfs:subClassOf": {"@id": "estleg:LegalProvision"},
        },
    ]
    graph.extend(annexes)
    graph.extend(provisions)

    stats = {
        "paragraphs": len(provisions),
        "annexes": len(annexes),
        "has_preamble": int(bool(preamble)),
        "html_fallback": int(parse_mode == "html_fallback"),
        "no_paragraphs": int(parse_mode == "no_paragraphs"),
    }

    return {"@context": CONTEXT, "@graph": graph}, stats


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def make_filename(title: str, tid: str, is_kov: bool, issuer: str | None) -> tuple[Path, str]:
    """Return (output_path, slug) for a regulation, namespacing by issuer for KOV."""
    slug_base = slugify(title, max_len=60) or f"regulation_{tid}"
    filename = f"{slug_base}_t{tid}_peep.json"
    if is_kov:
        issuer_slug = slugify(issuer or "kov", max_len=40) or "kov"
        return OUTPUT_KOV / issuer_slug / filename, slug_base
    return OUTPUT_RIIK / filename, slug_base


def gather_regulations(kov: bool, kehtiv: str, limit: int | None) -> dict[str, dict]:
    """Run the search API and return ``{terviktekstID: act_info}``.

    De-duplication: keep the entry with the largest globalID per terviktekstID
    (matches the convention used for laws — newest redaction wins).
    """
    by_tid: dict[str, dict] = {}
    seen = 0
    for act in fetch_acts(document="määrus", kov=kov, kehtiv=kehtiv, limiit=500):
        seen += 1
        tid = str(act.get("terviktekstID") or "")
        gid = str(act.get("globaalID") or "")
        if not tid:
            continue
        prev = by_tid.get(tid)
        if prev is None or gid > str(prev.get("gid", "")):
            by_tid[tid] = {
                "tid": tid,
                "gid": gid,
                "url": act.get("url", ""),
                "pealkiri": (act.get("pealkiri") or "").strip(),
                "valjaandja": act.get("valjaandja") or "",
                "kehtivus": act.get("kehtivus") or {},
            }
        if limit is not None and len(by_tid) >= limit:
            break
    print(f"  Pulled {seen} search rows -> {len(by_tid)} unique regulations")
    return by_tid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kov", action="store_true", help="Generate KOV (municipal) regulations instead of state-level.")
    parser.add_argument("--kehtiv", default=DEFAULT_KEHTIV, help="Snapshot date YYYY-MM-DD (default: %(default)s).")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N regulations (for dry runs).")
    parser.add_argument("--sleep", type=float, default=0.3, help="Seconds to sleep between XML fetches (be polite).")
    args = parser.parse_args()

    is_kov = args.kov
    out_dir = OUTPUT_KOV if is_kov else OUTPUT_RIIK
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_subdir = "maarus_kov" if is_kov else "maarus"

    print("=" * 70)
    print(f"Generate regulations (kov={'true' if is_kov else 'false'}, kehtiv={args.kehtiv})")
    print("=" * 70)

    print("\n[1/3] Querying Riigi Teataja API...")
    regs = gather_regulations(kov=is_kov, kehtiv=args.kehtiv, limit=args.limit)

    print(f"\n[2/3] Generating JSON-LD for {len(regs)} regulations...")
    generated = 0
    failed = 0
    skipped = 0
    totals = {"paragraphs": 0, "annexes": 0, "has_preamble": 0, "html_fallback": 0, "no_paragraphs": 0}
    issuer_counts: dict[str, int] = {}

    for i, (tid, info) in enumerate(sorted(regs.items()), 1):
        title = info["pealkiri"]
        url = info["url"]
        issuer = info.get("valjaandja", "")
        out_path, slug = make_filename(title, tid, is_kov, issuer)

        if out_path.exists():
            skipped += 1
            continue

        print(f"  [{i}/{len(regs)}] {issuer} | {title[:80]}")

        # Cache name: use globalID first (cheap to verify against RT), tid as fallback
        cache_name = f"reg_{info.get('gid') or tid}"
        root = fetch_xml(url, cache_name=cache_name, cache_subdir=cache_subdir)
        if root is None:
            print(f"    SKIP: could not fetch XML")
            failed += 1
            continue

        try:
            doc, stats = build_regulation_jsonld(title, info, root, is_kov=is_kov)
        except Exception as e:
            print(f"    FAIL: {e}")
            failed += 1
            continue

        if stats["paragraphs"] == 0 and stats["annexes"] == 0 and not stats.get("has_preamble"):
            # Pure procedural / amendment-only act with no body — skip.
            print(f"    SKIP: no parseable body")
            skipped += 1
            continue

        save_json(out_path, doc)
        generated += 1
        for k, v in stats.items():
            totals[k] = totals.get(k, 0) + v
        issuer_counts[issuer or "(unknown)"] = issuer_counts.get(issuer or "(unknown)", 0) + 1

        if args.sleep > 0:
            time.sleep(args.sleep)

    # ---------------------------------------------------------------------
    print("\n[3/3] Writing index...")
    index_path = out_dir / ("REGULATIONS_KOV_INDEX.json" if is_kov else "REGULATIONS_RIIK_INDEX.json")
    index_doc = {
        "kehtiv": args.kehtiv,
        "kov": is_kov,
        "totalRegulations": generated,
        "totalParagraphs": totals["paragraphs"],
        "totalAnnexes": totals["annexes"],
        "regulationsWithPreamble": totals["has_preamble"],
        "htmlFallbackCount": totals["html_fallback"],
        "byIssuer": dict(sorted(issuer_counts.items(), key=lambda x: -x[1])),
    }
    save_json(index_path, index_doc)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total regulations from API: {len(regs)}")
    print(f"  Newly generated:            {generated}")
    print(f"  Skipped (existing/empty):   {skipped}")
    print(f"  Failed:                     {failed}")
    print(f"  Total paragraphs written:   {totals['paragraphs']}")
    print(f"  Total annexes:              {totals['annexes']}")
    print(f"  HTML-fallback regulations:  {totals['html_fallback']}")
    print(f"  Output directory:           {out_dir}")
    print(f"  Index file:                 {index_path.name}")


if __name__ == "__main__":
    main()
