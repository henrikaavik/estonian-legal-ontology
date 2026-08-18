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
from pathlib import Path

from functools import partial

from estleg_common import (
    iter_peep_files,
    jsonld_text,
    jsonld_texts,
    save_json,
    sanitize_id as _shared_sanitize_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EELNOUD_DIR = KRR_DIR / "eelnoud"

NS = "https://w3id.org/estleg/"



def load_json(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"  WARN: cannot load {filepath.name}: {exc}")
        return None


sanitize_id = partial(_shared_sanitize_id, max_len=80, replace_dash=True)


# ---------- act-node resolution (issue #289) ----------

ACT_TYPE_MARKERS = {"estleg:Act"}


def _node_types(node: dict) -> set[str]:
    types = node.get("@type", [])
    if isinstance(types, str):
        return {types}
    if isinstance(types, list):
        return {t for t in types if isinstance(t, str)}
    return set()


def find_act_node(graph: list[dict]) -> dict | None:
    """Return the act node a peep file's ``estleg:affectedBy`` triple
    belongs on, mirroring ``extract_temporal_data.find_act_node``.

    Preference order: the ``owl:Ontology`` act-metadata node when it is
    also typed as ``estleg:Act`` (the historical write site), then any
    ``estleg:Act``-typed node. Returns ``None`` when neither exists — the
    caller must then skip enrichment for that file rather than stamping
    the link onto ``graph[0]`` (which could be a ``LegalConcept``; this is
    the corruption #128 fixed in the temporal sibling).
    """
    for node in graph:
        if not isinstance(node, dict):
            continue
        types = _node_types(node)
        if "owl:Ontology" in types and types & ACT_TYPE_MARKERS:
            return node
    for node in graph:
        if isinstance(node, dict) and _node_types(node) & ACT_TYPE_MARKERS:
            return node
    return None


# ---------- change-type classification ----------

CHANGE_TYPE_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"muutmi", re.IGNORECASE), "amends", "muudab"),
    (re.compile(r"kehtetuks\s+tunnistami", re.IGNORECASE), "repeals", "tunnistab kehtetuks"),
    (re.compile(r"täiendami", re.IGNORECASE), "supplements", "täiendab"),
    (re.compile(r"kehtestami", re.IGNORECASE), "enacts", "kehtestab"),
]

# Canonical priority for change types when multiple match. The
# previous "first-pattern wins" rule was order-of-list dependent and
# would classify a draft titled "X seaduse muutmise ja Y kehtetuks
# tunnistamise seadus" as "amends" even though it also repeals — the
# stronger statement should dominate.
_CHANGE_TYPE_PRIORITY: list[str] = [
    "repeals",
    "enacts",
    "amends",
    "supplements",
]


def classify_change_types(title: str) -> list[tuple[str, str]]:
    """Return ALL change-type matches as (change_type, label_et) tuples,
    deduplicated and ordered by canonical priority.
    """
    matches: dict[str, tuple[str, str]] = {}
    for pat, ctype, label in CHANGE_TYPE_PATTERNS:
        if pat.search(title):
            matches[ctype] = (ctype, label)
    return [matches[ct] for ct in _CHANGE_TYPE_PRIORITY if ct in matches]


def classify_change_type(title: str) -> tuple[str, str] | None:
    """Return the highest-priority (change_type, label_et) match, or None.

    Priority order (strongest first): repeals → enacts → amends →
    supplements. This replaces the prior "first-pattern wins" rule.
    """
    matches = classify_change_types(title)
    return matches[0] if matches else None


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


def affected_law_name_values(
    value: object, *, prefer_language: str | None = None
) -> list[str]:
    return jsonld_texts(value, prefer_language=prefer_language)


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
    for fpath in iter_peep_files(include_kov=False):  # KOV does not apply (EU/draft pipeline)
        iri = _read_ontology_iri(fpath)
        if iri:
            iri_map[fpath.name] = iri
    return iri_map


def build_law_lookup(
    index_data: dict,
    on_collision: str = "warn",
) -> dict[str, dict]:
    """
    Build a lookup:  normalized_name → { name, files, slug }
    Also add slug-based keys for fallback matching.

    Collisions occur when two laws produce the same readable/slug key
    (multiple laws with the same name in INDEX.json). The previous
    implementation silently overwrote the earlier entry, collapsing
    them into one. The new behaviour is configurable:

    * ``on_collision='warn'`` (default): drop ambiguous keys with a
      printed warning. Subsequent ``resolve_law_name`` calls will not
      match those keys, forcing the caller to disambiguate.
    * ``on_collision='raise'``: raise ``ValueError`` so the pipeline
      fails loudly.
    """
    if on_collision not in {"warn", "raise"}:
        raise ValueError(
            f"on_collision must be 'warn' or 'raise', got {on_collision!r}"
        )

    lookup: dict[str, dict] = {}
    collisions: dict[str, list[str]] = defaultdict(list)

    for law in index_data.get("laws", []):
        slug = law["name"]
        files = law.get("files", [])
        # Reconstruct a readable name from the slug (imperfect but useful for matching)
        readable = slug.replace("_", " ")
        entry = {"name": slug, "files": files, "slug": slug}

        for key in (readable, slug):
            if key in lookup and lookup[key] is not entry:
                # Track every entry that wanted this key so we can
                # report all participants in the collision.
                if not collisions[key]:
                    collisions[key].append(lookup[key]["slug"])
                collisions[key].append(slug)
            else:
                lookup[key] = entry

    if collisions:
        if on_collision == "raise":
            raise ValueError(
                "Ambiguous law-lookup keys: "
                + ", ".join(
                    f"{k} → {sorted(set(v))}"
                    for k, v in sorted(collisions.items())
                )
            )
        # 'warn' branch: drop ambiguous keys so resolve_law_name
        # cannot accidentally match the wrong entry.
        for key, participants in sorted(collisions.items()):
            print(
                f"  WARN: ambiguous law-lookup key {key!r} matched by "
                f"{sorted(set(participants))}; key dropped from lookup."
            )
            lookup.pop(key, None)

    return lookup


def _contiguous_subsequence(needle: str, haystack: str) -> bool:
    """Return True iff every whitespace-separated token of ``needle``
    appears in ``haystack`` as a whole-token contiguous run.

    This is the word-boundary alignment used by ``resolve_law_name``
    for substring matches: ``"alko seadus"`` matches ``"alko seadus
    plus"`` (the tokens line up and run consecutively) but not
    ``"alkohoolne seadus"`` (no whole-token match for ``alko``).
    """
    needle_tokens = needle.split()
    haystack_tokens = haystack.split()
    if not needle_tokens or len(needle_tokens) > len(haystack_tokens):
        return False
    for offset in range(len(haystack_tokens) - len(needle_tokens) + 1):
        if haystack_tokens[offset : offset + len(needle_tokens)] == needle_tokens:
            return True
    return False


def resolve_law_name(
    affected_name: str,
    lookup: dict[str, dict],
) -> dict | None:
    """Try to resolve an affected-law name to an INDEX entry.

    The lookup is iterated in **sorted-key order** so the result is
    deterministic regardless of dict insertion order. Substring
    matches require word-boundary alignment via
    ``_contiguous_subsequence`` to avoid e.g. ``"riik"`` colliding
    with ``"riiklik"``. Ties on substring matches are broken by
    shortest key length (more specific wins).
    """
    norm = normalize_law_name(affected_name)
    slug = slug_from_name(norm)

    # 1. Direct slug match (still a deterministic exact-equality check)
    if slug in lookup:
        return lookup[slug]

    # 2. Substring match with word-boundary alignment. Sort keys so
    # iteration is deterministic; tie-break by shortest key length
    # (the more specific match wins).
    candidates: list[tuple[int, str, dict]] = []
    for key in sorted(lookup):
        entry = lookup[key]
        if _contiguous_subsequence(slug, key) or _contiguous_subsequence(key, slug):
            candidates.append((len(key), key, entry))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    # 3. Token overlap: at least 2 significant tokens must match.
    #    Sort the iteration to make ties deterministic.
    norm_tokens = set(norm.split()) - {"ja", "ning", "seadus", "seadustik"}
    best_match = None
    best_overlap = 0
    best_key_len = float("inf")
    for key in sorted(lookup):
        entry = lookup[key]
        key_tokens = set(key.split()) - {"ja", "ning", "seadus", "seadustik"}
        overlap = len(norm_tokens & key_tokens)
        if overlap >= 2 and (
            overlap > best_overlap
            or (overlap == best_overlap and len(key) < best_key_len)
        ):
            best_overlap = overlap
            best_match = entry
            best_key_len = len(key)

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
    for fpath in iter_peep_files(include_kov=False):  # KOV does not apply (EU/draft pipeline)
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

    # Clear estleg:amendsLaw and estleg:changeType from all draft eelnoud files.
    # Use explicit globs (NOT ``*.json*``) so backup/temp/lock files
    # ending in suffixes like ``.json.bak``, ``.json~``, ``.json.swp``
    # are not opened. We process ``*.jsonld`` first, then ``*.json``,
    # de-duping by path so a file matched by both globs is touched
    # once.
    seen_paths: set[Path] = set()
    eelnoud_targets: list[Path] = []
    for pattern in ("*.jsonld", "*.json"):
        for fpath in sorted(EELNOUD_DIR.glob(pattern)):
            # Skip backup/temp suffixes (extra defence in depth — the
            # globs above already exclude them, but a future glob change
            # shouldn't silently regress this).
            if fpath.name.endswith((".bak", ".swp", "~")):
                continue
            if fpath in seen_paths:
                continue
            seen_paths.add(fpath)
            eelnoud_targets.append(fpath)

    for fpath in eelnoud_targets:
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
        title = jsonld_text(node.get("rdfs:label", ""), prefer_language="et")

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

        affected_names = affected_law_name_values(
            affected_raw, prefer_language="et"
        )
        if not affected_names:
            continue

        # Dedup resolved IRIs by ``@id`` (issue #266). Several affected-name
        # strings on one draft can resolve to the *same* act IRI (e.g. an
        # English + Estonian label pair, or a stray phantom candidate); the
        # earlier code appended each un-deduped, producing duplicate
        # ``amendsLaw`` IRIs and an inflated ``resolved_count``. Count each
        # distinct IRI once and emit it once.
        seen_iris: set[str] = set()
        amends_iris: list[dict] = []
        for aname in affected_names:
            entry = resolve_law_name(aname, lookup)
            if entry:
                ont_iri = get_ontology_iri(entry, iri_map)
                if ont_iri is None:
                    unresolved.append(aname)
                    continue
                if ont_iri in seen_iris:
                    continue  # already recorded for this draft
                seen_iris.add(ont_iri)
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
        for initiator in jsonld_texts(
            node.get("estleg:initiator", ""), prefer_language="et"
        ):
            ministry_drafts[initiator] += 1

    print(f"  Resolved: {resolved_count}")
    print(f"  Unresolved: {len(unresolved)}")

    # Save enriched drafts
    save_json(combined_path, drafts_doc)
    print(f"  Updated: {combined_path.name}")

    # ---------- inverse linking on law files ----------
    print(f"\n[4/5] Adding estleg:affectedBy to {len(law_affected_by)} law files...")

    inverse_count = 0
    no_act_node_skipped = 0
    for law_file, draft_iris in sorted(law_affected_by.items()):
        filepath = KRR_DIR / law_file
        if not filepath.exists():
            continue

        doc = load_json(filepath)
        if doc is None or "@graph" not in doc:
            continue

        # ``estleg:affectedBy`` is act-level — locate the act node the same
        # way extract_temporal_data does (issue #289). SKIP (with a counter)
        # when there is no act node rather than stamping onto ``graph[0]``,
        # which could be a ``LegalConcept`` (the #128 bug).
        ont_node = find_act_node(doc["@graph"])
        if ont_node is None:
            no_act_node_skipped += 1
            continue

        # Deduplicate draft IRIs
        unique_iris = list(dict.fromkeys(draft_iris))
        refs = [{"@id": d} for d in unique_iris]

        ont_node["estleg:affectedBy"] = refs

        save_json(filepath, doc)
        inverse_count += 1

    print(f"  Law files updated: {inverse_count}")
    if no_act_node_skipped:
        print(f"  Skipped (no act node): {no_act_node_skipped}")

    # ---------- report ----------
    print("\n[5/5] Generating report...")

    # Most-affected laws
    most_affected = sorted(
        law_affected_by.items(),
        key=lambda x: -len(x[1]),
    )[:20]

    # NOTE (issue #295): no wall-clock ``generated`` field. draft_impact_report.json
    # is git-tracked; embedding ``datetime.now()`` made it re-diff on every run
    # regardless of data changes (timestamp-only churn, banned by AGENTS.md).
    # Every value below is fully determined by the corpus, so the report is
    # byte-stable across reruns of the same inputs.
    report = {
        "summary": {
            "total_draft_nodes": len(draft_nodes),
            "affected_law_names_resolved": resolved_count,
            # issue #394: report the DEDUPED count so the summary matches its
            # own companion list ``unresolved_law_names`` (== sorted(set(...))).
            # The prior ``len(unresolved)`` was the raw occurrence count and
            # disagreed with the list length (1414 vs 845 in the live corpus).
            "affected_law_names_unresolved": len(set(unresolved)),
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
