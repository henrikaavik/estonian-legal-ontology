#!/usr/bin/env python3
"""
Fetch missing law parts from Riigi Teataja and generate JSON-LD ontology files.

Default missing parts (when run without args):
  - VÕS Osa 2 (Lepingu üldosa / General part of contracts)
  - VÕS Osa 6 (Kindlustuslepingud / Insurance contracts)
  - VÕS Osa 10 (Seltsingulepingud / Partnership)
  - TsÜS Osa 1 (Üldsätted / General provisions)

CLI:
  ``python -m scripts.generate_missing_parts --kehtiv 2026-05-01``

  Override which laws / parts to generate via ``--input-xml-vos``,
  ``--input-xml-tsus``, ``--vos-osa`` (repeatable), ``--skip-tsus``, and
  ``--output-dir``. Pass an explicit XML path to bypass the network and
  test against fixture data.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date as _date_cls
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from riigiteataja_common import (  # noqa: E402
    fetch_acts,
    fetch_xml,
    SourceListFetchError,
)
from generate_all_laws import (  # noqa: E402
    _iter_loiked as _iter_loiked,
    _loige_numbers as _loige_numbers,
    _paragraph_id_suffix,
    build_subsections,
    collect_full_text,
    collect_text as collect_text,
)

KRR_DIR = REPO_ROOT / "krr_outputs"
DATA_DIR = REPO_ROOT / "data" / "riigiteataja"
DATA_DIR.mkdir(parents=True, exist_ok=True)

NS = "https://data.riik.ee/ontology/estleg#"

CONTEXT = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}

DEFAULT_VOS_OSA_NUMBERS = ("2", "6", "10")
DEFAULT_KEHTIV = _date_cls.today().isoformat()


def ln(tag: str) -> str:
    """Strip XML namespace prefix."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def child_text(el: ET.Element, name: str) -> str | None:
    for c in el:
        if ln(c.tag) == name and c.text:
            return c.text.strip()
    return None


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    s = value.replace(" ", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s or "Unknown"


# ``collect_text`` (and its marker-pruning helpers ``_iter_text_nodes`` /
# ``_marker_pruned_text``) are imported from ``generate_all_laws`` above so
# that the summary-text extraction stays in lock-step with the canonical
# pipeline: amendment/publication/entry-into-force marker subtrees
# (``muutmismarge``/``avaldamismarge``/``joustumismarge``, #255) are pruned
# and the ``loige`` container is not double-captured alongside its own
# ``tavatekst``. A previous local copy walked the raw ``el.iter()`` tree and
# leaked those markers into ``estleg:summary``.


# ---------------------------------------------------------------------------
# Riigi Teataja law lookup
# ---------------------------------------------------------------------------

def _select_law_match(matches: list[dict], title: str) -> dict | None:
    """Pick the canonical match from a list of search-API rows.

    Strategy:
      1. Filter to entries whose ``pealkiri`` matches ``title``
         exactly (case-insensitive) or contains it as a substring.
      2. Within the filtered set, return the entry with the largest
         numeric ``globaalID`` (newest redaction wins). When no
         globalIDs are numeric, fall back to lexicographic order on
         the raw string.
    """
    if not matches:
        return None
    needle = title.lower().strip()

    exact: list[dict] = []
    substring: list[dict] = []
    for m in matches:
        haystack = (m.get("pealkiri") or "").lower().strip()
        if haystack == needle:
            exact.append(m)
        elif needle in haystack:
            substring.append(m)

    candidates = exact or substring
    if not candidates:
        return None

    def _gid_key(m: dict) -> tuple[int, int, str]:
        gid = str(m.get("globaalID") or "")
        try:
            return (1, int(gid), gid)
        except (TypeError, ValueError):
            return (0, 0, gid)

    return max(candidates, key=_gid_key)


def fetch_law_xml(
    title: str,
    *,
    kehtiv: str = DEFAULT_KEHTIV,
    cache_subdir: str | None = "seadus",
    explicit_xml: Path | None = None,
) -> tuple[ET.Element, str, dict]:
    """Fetch a law's XML from Riigi Teataja (or load from disk).

    When ``explicit_xml`` is supplied, parse and return that file
    directly — no network. Otherwise:

      * Query the search API with ``dokument=seadus`` and
        ``kehtiv=<date>`` so we never accept a historically-redacted
        match by accident.
      * Filter the rows to exact-or-substring matches and take the
        one with the largest numeric ``globaalID``.
      * Delegate to ``riigiteataja_common.fetch_xml`` so caching, retry,
        and timeout policy stay aligned with other generators.
    """
    if explicit_xml is not None:
        path = Path(explicit_xml)
        if not path.exists():
            raise RuntimeError(
                f"Explicit XML path does not exist: {explicit_xml}"
            )
        print(f"  Loading {title} from explicit XML: {path}")
        try:
            tree = ET.parse(str(path))
        except ET.ParseError as exc:
            raise RuntimeError(
                f"Could not parse {path}: {exc}"
            ) from exc
        root = tree.getroot()
        return root, str(path), {"source": "explicit", "path": str(path)}

    print(f"  Searching for '{title}' (kehtiv={kehtiv})...")
    try:
        rows = list(
            fetch_acts(
                document="seadus",
                kehtiv=kehtiv,
                limiit=50,
                allow_partial=False,
            )
        )
    except SourceListFetchError as exc:
        raise RuntimeError(
            f"Riigi Teataja search failed for '{title}': {exc}"
        ) from exc

    # The search API doesn't accept a free-text title parameter via
    # fetch_acts(), so filter client-side. Substring + globalID rank
    # is enforced by _select_law_match.
    needle = title.lower().strip()
    pre_filtered = [
        r for r in rows
        if needle in (r.get("pealkiri") or "").lower()
    ]
    match = _select_law_match(pre_filtered, title)
    if match is None:
        raise RuntimeError(f"'{title}' not found in Riigi Teataja API (kehtiv={kehtiv})")

    gid = str(match.get("globaalID") or "")
    cache_name = f"law_{gid or sanitize_id(title)}"
    print(f"  Selected globaalID={gid or '(unknown)'}; fetching XML via cache...")

    root = fetch_xml(
        match["url"],
        cache_name=cache_name,
        cache_subdir=cache_subdir,
        refresh=False,
    )
    if root is None:
        raise RuntimeError(
            f"Could not fetch XML for '{title}' (globaalID={gid})"
        )

    xml_url = match.get("url", "")
    return root, xml_url, match


def find_osa(root: ET.Element, osa_nr: str) -> ET.Element | None:
    """Find a specific osa (part) by number."""
    for osa in root.iter():
        if ln(osa.tag) == "osa":
            nr = child_text(osa, "osaNr")
            if nr == osa_nr:
                return osa
    return None


def extract_paragrahvid(parent: ET.Element) -> list[ET.Element]:
    """Recursively collect all paragrahv elements under a parent."""
    result = []
    for el in parent.iter():
        if ln(el.tag) == "paragrahv":
            result.append(el)
    return result


def generate_vos_part(root: ET.Element, xml_url: str, osa_nr: str) -> dict | None:
    """Generate JSON-LD for a VÕS part.

    Issue #175: each part gets its own LegalProvision class IRI
    (``estleg:LegalProvision_VOS_osa<N>``) — the earlier hardcoded
    ``Osa7`` literal was a copy-paste typo. Paragraph IRIs are
    namespaced per part (``estleg:VOS_Osa<N>_Par_<n>``) so two parts
    that happen to renumber paragraphs can never collide on the same
    @id.
    """
    osa = find_osa(root, osa_nr)
    if osa is None:
        print(f"  WARNING: VÕS osa {osa_nr} not found in XML!")
        return None

    osa_title = child_text(osa, "osaPealkiri") or f"Osa {osa_nr}"
    osa_display = child_text(osa, "kuvatavNr") or f"{osa_nr}. osa"
    print(f"  Found: {osa_display} – {osa_title}")

    paragrahvid = extract_paragrahvid(osa)
    print(f"  Paragraphs found: {len(paragrahvid)}")

    if not paragrahvid:
        print("  WARNING: No paragraphs found!")
        return None

    # Determine paragraph range
    par_numbers = []
    for p in paragrahvid:
        nr = child_text(p, "paragrahvNr")
        if nr:
            try:
                par_numbers.append(int(re.sub(r"[^\d]", "", nr)))
            except ValueError:
                pass

    par_min = min(par_numbers) if par_numbers else "?"
    par_max = max(par_numbers) if par_numbers else "?"

    # Build graph
    prefix = "VOS"
    osa_safe = sanitize_id(osa_nr)
    # Snapshot-stable act IRI: the part is identified by its osa number
    # alone, not by the §min–§max range, so the act @id does not churn when
    # a redaction inserts/removes a paragraph (#269c). The range stays in
    # the human-readable label below.
    ontology_id = f"estleg:{prefix}_Osa{osa_safe}"
    class_iri = f"estleg:LegalProvision_VOS_osa{osa_safe}"

    graph: list[dict] = [
        {
            "@id": ontology_id,
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
            "rdfs:label": f"VÕS Osa {osa_nr} ({osa_title}) §{par_min}–{par_max} kaardistus",
            "dc:source": "Võlaõigusseadus",
        },
        {
            "@id": class_iri,
            "@type": ["owl:Class"],
            "rdfs:label": "Õigusnorm (paragrahv)",
        },
    ]

    # Collect topic clusters from peatükk (chapter) structure
    clusters = []
    for ch in osa.iter():
        if ln(ch.tag) == "peatykk":
            ch_nr = child_text(ch, "peatykkNr") or ""
            ch_title = child_text(ch, "peatykkPealkiri") or ""
            if ch_title:
                cluster_id = f"estleg:Cluster_VOS_{osa_safe}_{sanitize_id(ch_nr or ch_title[:20])}"
                ch_pars = extract_paragrahvid(ch)
                ch_par_nrs = []
                for p in ch_pars:
                    nr = child_text(p, "paragrahvNr")
                    if nr:
                        try:
                            ch_par_nrs.append(int(re.sub(r"[^\d]", "", nr)))
                        except ValueError:
                            pass
                par_range = f"§{min(ch_par_nrs)}–{max(ch_par_nrs)}" if ch_par_nrs else ""
                clusters.append({
                    "id": cluster_id,
                    "label": f"{par_range} {ch_title}".strip(),
                    "par_nrs": ch_par_nrs,
                })

    # Add jagu-level clusters too
    for jg in osa.iter():
        if ln(jg.tag) == "jagu":
            jg_nr = child_text(jg, "jaguNr") or ""
            jg_title = child_text(jg, "jaguPealkiri") or ""
            if jg_title:
                cluster_id = (
                    f"estleg:Cluster_VOS_{osa_safe}_jagu_"
                    f"{sanitize_id(jg_nr or jg_title[:20])}"
                )
                jg_pars = extract_paragrahvid(jg)
                jg_par_nrs = []
                for p in jg_pars:
                    nr = child_text(p, "paragrahvNr")
                    if nr:
                        try:
                            jg_par_nrs.append(int(re.sub(r"[^\d]", "", nr)))
                        except ValueError:
                            pass
                par_range = f"§{min(jg_par_nrs)}–{max(jg_par_nrs)}" if jg_par_nrs else ""
                clusters.append({
                    "id": cluster_id,
                    "label": f"{par_range} {jg_title}".strip(),
                    "par_nrs": jg_par_nrs,
                })

    # Add cluster nodes
    for cl in clusters:
        graph.append({
            "@id": cl["id"],
            "@type": ["owl:NamedIndividual", "estleg:TopicCluster"],
            "rdfs:label": cl["label"],
        })

    # Add paragraph nodes — IRIs are namespaced by osa so cross-part
    # collisions on @id are impossible (smoke-test enforced).
    seen_subsection_ids: set[str] = set()
    for p in paragrahvid:
        p_nr = child_text(p, "paragrahvNr") or "?"
        p_title = child_text(p, "paragrahvPealkiri") or ""
        p_display = child_text(p, "kuvatavNr") or f"§ {p_nr}"
        # Preserve this module's historical 800-char summary budget while
        # delegating to the marker-pruned canonical extractor (its own
        # default is 500).
        text = collect_text(p, max_len=800)
        full_text = collect_full_text(p)
        par_suffix = _paragraph_id_suffix(p)

        p_id = f"estleg:VOS_Osa{osa_safe}_Par_{par_suffix}"

        # Find which cluster this paragraph belongs to
        try:
            p_num = int(re.sub(r"[^\d]", "", p_nr))
        except ValueError:
            p_num = 0

        cluster_ref = None
        for cl in clusters:
            if p_num in cl["par_nrs"]:
                cluster_ref = cl["id"]
                break

        node: dict = {
            "@id": p_id,
            "@type": [
                "owl:NamedIndividual",
                class_iri,
            ],
            "estleg:paragrahv": p_display,
            "rdfs:label": f"{p_display} {p_title}".strip() if p_title else p_display,
            "estleg:sourceAct": "Võlaõigusseadus",
            # Issue #415: structural IRI join to this osa's act root.
            "estleg:partOfAct": {"@id": ontology_id},
        }

        if text:
            node["estleg:summary"] = text

        if full_text:
            node["estleg:legalText"] = full_text

        if cluster_ref:
            # IRI reference, not a bare string literal (JSON-LD would treat
            # a plain string as an xsd:string and orphan the cluster).
            node["estleg:requestedCluster"] = {"@id": cluster_ref}

        subsection_nodes = build_subsections(
            p,
            p_id,
            abbrev_prefix=prefix,
            par_suffix=par_suffix,
            paragraph_display=p_display,
            osa_nr=osa_safe,
            seen_ids=seen_subsection_ids,
        )
        if subsection_nodes:
            node["estleg:hasSubsection"] = [
                {"@id": subsection["@id"]} for subsection in subsection_nodes
            ]

        graph.append(node)
        graph.extend(subsection_nodes)

    return {"@context": CONTEXT, "@graph": graph}


def generate_tsus_part1(root: ET.Element, xml_url: str) -> dict | None:
    """Generate JSON-LD for TsÜS Osa 1."""
    osa = find_osa(root, "1")
    osa_paragrahvid = extract_paragrahvid(osa) if osa is not None else []
    # In the real TsÜS XML the Osa 1 ``<osa>`` is a *flat marker*: it holds
    # only ``osaNr``/``kuvatavNr``/``osaPealkiri`` and the paragraphs are
    # siblings further down the tree. The pre-fix ``if osa is None:`` guard
    # therefore never fired (the marker is non-None), and
    # ``extract_paragrahvid(osa)`` returned 0 → an empty artifact (#269).
    # Treat a marker with no paragrahv descendants as unusable and fall
    # through to the §§ 1-7 scan.
    if osa is None or not osa_paragrahvid:
        # TsÜS might not have explicit osa markers for part 1, or the marker
        # is flat. Try to find paragraphs 1-7 directly.
        print("  Osa 1 marker empty/absent, looking for §§ 1-7...")
        paragrahvid = []
        for p in root.iter():
            if ln(p.tag) == "paragrahv":
                nr = child_text(p, "paragrahvNr")
                if nr:
                    try:
                        num = int(re.sub(r"[^\d]", "", nr))
                        if 1 <= num <= 7:
                            paragrahvid.append(p)
                    except ValueError:
                        pass
        if not paragrahvid:
            print("  WARNING: Could not find TsÜS §§ 1-7!")
            return None
    else:
        osa_title = child_text(osa, "osaPealkiri") or "Üldsätted"
        print(f"  Found: 1. osa – {osa_title}")
        paragrahvid = osa_paragrahvid

    print(f"  Paragraphs found: {len(paragrahvid)}")

    par_numbers = []
    for p in paragrahvid:
        nr = child_text(p, "paragrahvNr")
        if nr:
            try:
                par_numbers.append(int(re.sub(r"[^\d]", "", nr)))
            except ValueError:
                pass

    par_min = min(par_numbers) if par_numbers else 1
    par_max = max(par_numbers) if par_numbers else 7

    graph: list[dict] = [
        {
            # Snapshot-stable act IRI (no volatile §min–§max range) — #269c.
            "@id": "estleg:TsUS_Osa1",
            "@type": ["owl:Ontology", "estleg:Act", "estleg:Law"],
            "rdfs:label": f"TsÜS Osa 1 (Üldsätted) §{par_min}–{par_max} kaardistus",
            "dc:source": "Tsiviilseadustiku üldosa seadus",
        },
        {
            "@id": "estleg:LegalProvision_TsUS_osa1",
            "@type": ["owl:Class"],
            "rdfs:label": "Õigusnorm (paragrahv)",
        },
        {
            "@id": "estleg:Cluster_TsUS_Uldsatted",
            "@type": ["owl:NamedIndividual", "estleg:TopicCluster"],
            "rdfs:label": "Üldsätted (tsiviilõiguse aluspõhimõtted)",
        },
    ]

    seen_subsection_ids: set[str] = set()
    for p in paragrahvid:
        p_nr = child_text(p, "paragrahvNr") or "?"
        p_title = child_text(p, "paragrahvPealkiri") or ""
        p_display = child_text(p, "kuvatavNr") or f"§ {p_nr}"
        # Preserve this module's historical 800-char summary budget while
        # delegating to the marker-pruned canonical extractor (its own
        # default is 500).
        text = collect_text(p, max_len=800)
        full_text = collect_full_text(p)
        par_suffix = _paragraph_id_suffix(p)

        p_id = f"estleg:TsUS_Osa1_Par_{par_suffix}"

        node: dict = {
            "@id": p_id,
            "@type": [
                "owl:NamedIndividual",
                "estleg:LegalProvision_TsUS_osa1",
            ],
            "estleg:paragrahv": p_display,
            "rdfs:label": f"{p_display} {p_title}".strip() if p_title else p_display,
            # IRI reference, not a bare string literal (#370 item 2).
            "estleg:requestedCluster": {"@id": "estleg:Cluster_TsUS_Uldsatted"},
            "estleg:sourceAct": "Tsiviilseadustiku üldosa seadus",
            # Issue #415: structural IRI join to the TsÜS part-1 act root.
            "estleg:partOfAct": {"@id": "estleg:TsUS_Osa1"},
        }

        if text:
            node["estleg:summary"] = text

        if full_text:
            node["estleg:legalText"] = full_text

        subsection_nodes = build_subsections(
            p,
            p_id,
            abbrev_prefix="TsUS",
            par_suffix=par_suffix,
            paragraph_display=p_display,
            osa_nr="1",
            seen_ids=seen_subsection_ids,
        )
        if subsection_nodes:
            node["estleg:hasSubsection"] = [
                {"@id": subsection["@id"]} for subsection in subsection_nodes
            ]

        graph.append(node)
        graph.extend(subsection_nodes)

    return {"@context": CONTEXT, "@graph": graph}


def save_json(filepath: Path, doc: dict):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  Saved: {filepath.name}")


# ---------------------------------------------------------------------------
# CLI driver
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-xml-vos",
        type=Path,
        default=None,
        help="Path to a Võlaõigusseadus XML file (skip the search API).",
    )
    parser.add_argument(
        "--input-xml-tsus",
        type=Path,
        default=None,
        help="Path to a Tsiviilseadustiku üldosa seadus XML file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=KRR_DIR,
        help="Directory to write *_peep.json files (default: %(default)s).",
    )
    parser.add_argument(
        "--kehtiv",
        default=DEFAULT_KEHTIV,
        help="Snapshot date YYYY-MM-DD (default: today's date).",
    )
    parser.add_argument(
        "--vos-osa",
        action="append",
        dest="vos_osa",
        default=None,
        metavar="N",
        help=(
            "VÕS osa number to generate; pass multiple times. "
            f"Defaults to {','.join(DEFAULT_VOS_OSA_NUMBERS)}."
        ),
    )
    parser.add_argument(
        "--skip-tsus",
        action="store_true",
        help="Skip Tsiviilseadustiku üldosa seadus generation.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    osa_numbers = args.vos_osa or list(DEFAULT_VOS_OSA_NUMBERS)
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Generating missing law parts from Riigi Teataja")
    print("=" * 60)

    # === VÕS ===
    print("\n--- Fetching Võlaõigusseadus ---")
    try:
        vos_root, vos_url, _vos_match = fetch_law_xml(
            "Võlaõigusseadus",
            kehtiv=args.kehtiv,
            cache_subdir="seadus",
            explicit_xml=args.input_xml_vos,
        )
    except Exception as e:
        print(f"  ERROR fetching VÕS: {e}")
        return 1

    # List all osa elements for debugging
    print("\n  Available parts in VÕS:")
    for el in vos_root.iter():
        if ln(el.tag) == "osa":
            nr = child_text(el, "osaNr")
            title = child_text(el, "osaPealkiri")
            print(f"    Osa {nr}: {title}")

    for osa_nr in osa_numbers:
        print(f"\n--- Generating VÕS Osa {osa_nr} ---")
        doc = generate_vos_part(vos_root, vos_url, osa_nr)
        if doc:
            # Slug MUST match generate_all_laws.slugify("Võlaõigusseadus")
            # == "volaoigusseadus" (õ→o). The old "volaigusseadus" dropped
            # the second 'o', producing duplicate divergent files under a
            # second slug/IRI namespace (#269).
            out_path = out_dir / f"volaoigusseadus_osa{osa_nr}_peep.json"
            save_json(out_path, doc)
            node_count = len(doc["@graph"])
            print(f"  Generated {node_count} nodes")
        else:
            print(f"  FAILED to generate VÕS Osa {osa_nr}")

    # === TsÜS ===
    if args.skip_tsus:
        print("\n--- Skipping TsÜS (--skip-tsus) ---")
    else:
        print("\n--- Fetching Tsiviilseadustiku üldosa seadus ---")
        try:
            tsus_root, tsus_url, _tsus_match = fetch_law_xml(
                "Tsiviilseadustiku üldosa seadus",
                kehtiv=args.kehtiv,
                cache_subdir="seadus",
                explicit_xml=args.input_xml_tsus,
            )
        except Exception as e:
            print(f"  ERROR fetching TsÜS: {e}")
            return 1

        # List all osa elements
        print("\n  Available parts in TsÜS:")
        for el in tsus_root.iter():
            if ln(el.tag) == "osa":
                nr = child_text(el, "osaNr")
                title = child_text(el, "osaPealkiri")
                print(f"    Osa {nr}: {title}")

        print("\n--- Generating TsÜS Osa 1 ---")
        doc = generate_tsus_part1(tsus_root, tsus_url)
        if doc:
            out_path = out_dir / "tsiviilseadustik_osa1_peep.json"
            save_json(out_path, doc)
            node_count = len(doc["@graph"])
            print(f"  Generated {node_count} nodes")
        else:
            print("  FAILED to generate TsÜS Osa 1")

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
