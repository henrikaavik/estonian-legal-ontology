#!/usr/bin/env python3
"""
Analyze draft legislation impact at provision level.

Resolves estleg:affectedLawName strings to actual law ontology IRIs via
fuzzy matching against INDEX.json, classifies the change type from draft
titles, and adds inverse estleg:affectedBy links on enacted-law files.

Outputs:
  - Updated krr_outputs/eelnoud/eelnoud_combined.jsonld  (enriched with IRI links)
  - Updated law *_peep.json files (with estleg:affectedBy)
  - krr_outputs/draft_impact_report.json
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EELNOUD_DIR = KRR_DIR / "eelnoud"

NS = "https://data.riik.ee/ontology/estleg#"

CONTEXT = {
    "estleg": NS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
}


def save_json(filepath: Path, doc: dict) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_json(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"  WARN: cannot load {filepath.name}: {exc}")
        return None


_ESTONIAN_TRANSLITERATION: dict[str, str] = {
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "O", "Ä": "A", "Ü": "U", "Õ": "O",
    "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
}
_TRANSLIT_TABLE = str.maketrans(_ESTONIAN_TRANSLITERATION)


def sanitize_id(value: str) -> str:
    s = value.replace(" ", "_").replace("-", "_")
    # Transliterate Estonian diacritics before stripping non-ASCII
    s = s.translate(_TRANSLIT_TABLE)
    s = re.sub(r"[^0-9A-Za-z_]", "", s)
    return s[:80] or "Unknown"


# ---------- change-type classification ----------

CHANGE_TYPE_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"muutmi", re.IGNORECASE), "amends", "muudab"),
    (re.compile(r"kehtetuks\s+tunnistami", re.IGNORECASE), "repeals", "tunnistab kehtetuks"),
    (re.compile(r"täiendami", re.IGNORECASE), "supplements", "täiendab"),
    (re.compile(r"kehtestami", re.IGNORECASE), "enacts", "kehtestab"),
]


def classify_change_type(title: str) -> tuple[str, str] | None:
    """Return (change_type, label_et) or None."""
    for pat, ctype, label in CHANGE_TYPE_PATTERNS:
        if pat.search(title):
            return ctype, label
    return None


# ---------- fuzzy law-name resolution ----------

def normalize_law_name(name: str) -> str:
    """
    Normalize an Estonian law name for fuzzy matching:
    lowercase, strip common suffixes, collapse whitespace.
    """
    n = name.lower().strip()
    # Strip genitive/partitive forms: "seaduse" → "seadus", "seadustiku" → "seadustik"
    n = re.sub(r"seaduse\b", "seadus", n)
    n = re.sub(r"seadustiku\b", "seadustik", n)
    # Remove trailing punctuation
    n = re.sub(r"[\s,;.]+$", "", n)
    # Collapse whitespace
    n = re.sub(r"\s+", " ", n)
    return n


def slug_from_name(name: str) -> str:
    """Convert a law name to the filename slug form used in INDEX.json."""
    replacements = {
        "ä": "a", "ö": "o", "ü": "u", "õ": "o",
        "Ä": "A", "Ö": "O", "Ü": "U", "Õ": "O",
        "š": "s", "ž": "z", "Š": "S", "Ž": "Z",
    }
    text = name
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:80]


def _read_ontology_iri(filepath: Path) -> str | None:
    """Read the owl:Ontology node @id from a JSON-LD file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            doc = json.load(f)
        for node in doc.get("@graph", []):
            types = node.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "owl:Ontology" in types:
                iri = node.get("@id", "")
                if iri:
                    return iri
    except Exception:
        pass
    return None


def build_ontology_iri_map() -> dict[str, str]:
    """Scan all krr_outputs/*_peep.json files and build filename → ontology IRI map."""
    iri_map: dict[str, str] = {}
    for fpath in sorted(KRR_DIR.glob("*_peep.json")):
        iri = _read_ontology_iri(fpath)
        if iri:
            iri_map[fpath.name] = iri
    return iri_map


def build_law_lookup(index_data: dict) -> dict[str, dict]:
    """
    Build a lookup:  normalized_name → { name, files, slug }
    Also add slug-based keys for fallback matching.
    """
    lookup: dict[str, dict] = {}
    for law in index_data.get("laws", []):
        slug = law["name"]
        files = law.get("files", [])
        # Reconstruct a readable name from the slug (imperfect but useful for matching)
        readable = slug.replace("_", " ")
        entry = {"name": slug, "files": files, "slug": slug}
        lookup[readable] = entry
        lookup[slug] = entry
    return lookup


def resolve_law_name(
    affected_name: str,
    lookup: dict[str, dict],
) -> dict | None:
    """Try to resolve an affected-law name to an INDEX entry."""
    norm = normalize_law_name(affected_name)
    slug = slug_from_name(norm)

    # 1. Direct slug match
    if slug in lookup:
        return lookup[slug]

    # 2. Substring match: find lookup keys that contain the slug or vice versa
    for key, entry in lookup.items():
        if slug in key or key in slug:
            return entry

    # 3. Token overlap: at least 2 significant tokens must match
    norm_tokens = set(norm.split()) - {"ja", "ning", "seadus", "seadustik"}
    best_match = None
    best_overlap = 0
    for key, entry in lookup.items():
        key_tokens = set(key.split()) - {"ja", "ning", "seadus", "seadustik"}
        overlap = len(norm_tokens & key_tokens)
        if overlap >= 2 and overlap > best_overlap:
            best_overlap = overlap
            best_match = entry

    return best_match


def get_ontology_iri(law_entry: dict, iri_map: dict[str, str]) -> str | None:
    """Look up the actual ontology @id for a law entry using the pre-built IRI map.

    Returns None if no matching ontology IRI is found, avoiding synthetic
    IRIs that would create dangling references.
    """
    for f in law_entry.get("files", []):
        iri = iri_map.get(f)
        if iri:
            return iri
    return None


def main() -> None:
    print("=" * 70)
    print("Estonian Legal Ontology - Draft Legislation Impact Analysis")
    print("=" * 70)

    # ---------- load index ----------
    print("\n[1/5] Loading law index...")
    index_path = KRR_DIR / "INDEX.json"
    index_data = load_json(index_path)
    if index_data is None:
        print("  ERROR: INDEX.json not found or invalid. Aborting.")
        return
    print(f"  Laws in index: {index_data.get('total_laws', '?')}")

    lookup = build_law_lookup(index_data)
    print(f"  Lookup entries: {len(lookup)}")

    # Build mapping from filename → actual ontology IRI by scanning all law files
    print("  Building ontology IRI map from law files...")
    iri_map = build_ontology_iri_map()
    print(f"  Ontology IRIs found: {len(iri_map)}")

    # ---------- load drafts ----------
    print("\n[2/5] Loading draft legislation...")
    combined_path = EELNOUD_DIR / "eelnoud_combined.jsonld"
    drafts_doc = load_json(combined_path)
    if drafts_doc is None:
        print("  ERROR: eelnoud_combined.jsonld not found or invalid. Aborting.")
        return

    draft_nodes = [
        n for n in drafts_doc.get("@graph", [])
        if "estleg:DraftLegislation" in (n.get("@type") or [])
    ]
    print(f"  Draft nodes: {len(draft_nodes)}")

    # ---------- clearing pass ----------
    print("\n[2b/5] Clearing stale draft impact links...")

    # Clear estleg:amendsLaw and estleg:changeType from draft nodes
    for node in draft_nodes:
        for key in ("estleg:amendsLaw", "estleg:changeType"):
            if key in node:
                del node[key]

    # Clear estleg:affectedBy from all law peep files
    for fpath in sorted(KRR_DIR.glob("*_peep.json")):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            continue
        modified = False
        for node in doc.get("@graph", []):
            if "estleg:affectedBy" in node:
                del node["estleg:affectedBy"]
                modified = True
        if modified:
            save_json(fpath, doc)

    # Clear estleg:amendsLaw and estleg:changeType from all draft eelnoud files
    for fpath in sorted(EELNOUD_DIR.glob("*.json*")):
        if fpath == combined_path:
            continue  # already handled above via draft_nodes
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            continue
        modified = False
        for node in doc.get("@graph", []):
            for key in ("estleg:amendsLaw", "estleg:changeType"):
                if key in node:
                    del node[key]
                    modified = True
        if modified:
            save_json(fpath, doc)

    print("  Clearing done.")

    # ---------- resolve and enrich ----------
    print("\n[3/5] Resolving affected laws and classifying change types...")

    resolved_count = 0
    unresolved: list[str] = []
    change_type_counts: dict[str, int] = defaultdict(int)
    # law_slug → list of draft IRIs
    law_affected_by: dict[str, list[str]] = defaultdict(list)
    # ministry → list of draft IRIs
    ministry_drafts: dict[str, int] = defaultdict(int)

    for node in draft_nodes:
        draft_iri = node.get("@id", "")
        title = node.get("rdfs:label", "")

        # -- change type --
        ct = classify_change_type(title)
        if ct:
            ctype, clabel = ct
            node["estleg:changeType"] = ctype
            change_type_counts[ctype] += 1

        # -- affected law resolution --
        affected_raw = node.get("estleg:affectedLawName")
        if not affected_raw:
            continue

        # affectedLawName can be a string or a list
        if isinstance(affected_raw, str):
            affected_names = [affected_raw]
        elif isinstance(affected_raw, list):
            affected_names = affected_raw
        else:
            continue

        amends_iris: list[dict] = []
        for aname in affected_names:
            entry = resolve_law_name(aname, lookup)
            if entry:
                ont_iri = get_ontology_iri(entry, iri_map)
                if ont_iri is None:
                    unresolved.append(aname)
                    continue
                amends_iris.append({"@id": ont_iri})
                # Record for inverse linking
                for f in entry.get("files", []):
                    law_affected_by[f].append(draft_iri)
                resolved_count += 1
            else:
                unresolved.append(aname)

        if len(amends_iris) == 1:
            node["estleg:amendsLaw"] = amends_iris[0]
        elif amends_iris:
            node["estleg:amendsLaw"] = amends_iris

        # -- ministry stats --
        initiator = node.get("estleg:initiator", "")
        if initiator:
            ministry_drafts[initiator] += 1

    print(f"  Resolved: {resolved_count}")
    print(f"  Unresolved: {len(unresolved)}")

    # Save enriched drafts
    save_json(combined_path, drafts_doc)
    print(f"  Updated: {combined_path.name}")

    # ---------- inverse linking on law files ----------
    print(f"\n[4/5] Adding estleg:affectedBy to {len(law_affected_by)} law files...")

    inverse_count = 0
    for law_file, draft_iris in sorted(law_affected_by.items()):
        filepath = KRR_DIR / law_file
        if not filepath.exists():
            continue

        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue

        # Add affectedBy to the ontology node (first node in graph)
        ont_node = doc["@graph"][0] if doc["@graph"] else None
        if ont_node is None:
            continue

        # Deduplicate draft IRIs
        unique_iris = list(dict.fromkeys(draft_iris))
        refs = [{"@id": d} for d in unique_iris]

        ont_node["estleg:affectedBy"] = refs

        save_json(filepath, doc)
        inverse_count += 1

    print(f"  Law files updated: {inverse_count}")

    # ---------- report ----------
    print(f"\n[5/5] Generating report...")

    # Most-affected laws
    most_affected = sorted(
        law_affected_by.items(),
        key=lambda x: -len(x[1]),
    )[:20]

    report = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "summary": {
            "total_draft_nodes": len(draft_nodes),
            "affected_law_names_resolved": resolved_count,
            "affected_law_names_unresolved": len(unresolved),
            "law_files_with_inverse_links": inverse_count,
        },
        "by_change_type": dict(sorted(change_type_counts.items(), key=lambda x: -x[1])),
        "most_affected_laws": [
            {"file": f, "pending_drafts": len(d)}
            for f, d in most_affected
        ],
        "pending_changes_by_ministry": dict(
            sorted(ministry_drafts.items(), key=lambda x: -x[1])
        ),
        "unresolved_law_names": sorted(set(unresolved)),
    }

    report_path = KRR_DIR / "draft_impact_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # ---------- summary ----------
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Drafts processed:          {len(draft_nodes)}")
    print(f"  Affected laws resolved:    {resolved_count}")
    print(f"  Unresolved names:          {len(unresolved)}")
    print(f"  Law files with inverse:    {inverse_count}")
    print()
    print("  Change types:")
    for ct, cnt in sorted(change_type_counts.items(), key=lambda x: -x[1]):
        print(f"    {ct:20s}  {cnt}")
    print()
    if most_affected:
        print("  Most-affected laws:")
        for f, drafts in most_affected[:5]:
            print(f"    {f:55s}  {len(drafts)} drafts")
    print("=" * 70)


if __name__ == "__main__":
    main()
