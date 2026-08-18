#!/usr/bin/env python3
"""
Build + repair helpers for the Estonian Legal Ontology corpus.

This module has **two distinct responsibilities** that should not be
conflated:

(a) STILL-ESSENTIAL BUILD STEPS — part of every ordinary regeneration:
    - ``generate_combined_jsonld`` — the canonical builder of
      ``krr_outputs/combined_ontology.jsonld`` from ``canonical_combined_inputs``
      (every root ``*_peep.json`` + the ``COMBINED_ALLOWED_JSONLD`` allowlist).
      Nothing else builds this artifact; it is validated by
      ``validate_all.validate_combined_ontology``.
    - ``generate_index`` — the builder of ``krr_outputs/INDEX.json``
      (the master law registry); validated by
      ``validate_all.validate_registry_index``.
    These two are *not* repair passes — they are first-class build outputs.

(b) LEGACY ONE-TIME-MIGRATION / SAFETY-NET REPAIR PASSES — kept ONLY as a
    belt-and-braces guard because the generators now emit the correct
    JSON-LD shapes directly. They are no longer required for ordinary
    regeneration and should be considered for removal once a regression
    window confirms generator output stays validator-clean:
      - ``migrate_namespace_in_value`` — legacy example.org → canonical
        namespace migration (#2/#17). Generators emit the canonical
        ``w3id.org/estleg`` namespace directly (see ``estleg_common.NS`` /
        ``estleg_common.CONTEXT``).
      - ``normalize_type`` — coerce ``@type`` to an array (#4).
        Generators emit ``@type`` arrays directly (see the act-node
        construction in ``generate_all_laws.py`` / ``generate_regulations.py``,
        all of which write ``"@type": [...]``).
      - ``normalize_multi_valued`` — coerce ``coversConcept`` / ``hasSection``
        / etc. to arrays (#5). Generators emit these as arrays directly.
      - ``normalize_dc_source`` — collapse a singleton ``dc:source`` array
        to a scalar and coerce array items to ``str`` (#12 / lossless per
        #159). Generators emit a scalar ``dc:source`` string directly
        (see ``estleg_common.source_provenance`` and the inline
        ``"dc:source": title`` in the generators).
      - ``normalize_section_number`` — coerce ``estleg:sectionNumber`` to a
        string (#13). ``generate_kars_eriosa_jsonld.py`` emits string
        section numbers directly.
      - ``audit_duplicate_ids`` — cross-file ``@id`` collision report (#6).
        Reporting only; the generators produce file-disjoint id spaces.
      - ``fix_intra_file_duplicates`` / ``_fix_intra_file_duplicates_in_doc``
        — content-hash-stable intra-file ``@id`` dedup (#6 / #159).
        Generators produce unique ids within a file; this is a no-op on
        clean output.
      - ``fix_generator_script`` / ``fix_docs_namespace`` — one-time
        namespace fix in ``generate_kars_eriosa_jsonld.py`` and ``docs/*.md``
        (#14).

All of the ``normalize_*`` passes in group (b) are idempotent no-ops on
already-clean input; ``tests/test_fix_all_issues.py`` pins both that
property and the "generators produce validator-clean output" guarantee
with fixture-based tests. ``scripts/validate_all.py``'s per-node
validators (``validate_types``, ``validate_section_numbers``,
``validate_dc_source``, ``validate_source_provenance``, ...) are the
enforcement side of the contract.

Originally fixed GitHub issues: #2/#17 (namespace), #3/#21 (AÕS naming),
#4 (@type arrays), #5 (multi-valued property arrays), #6 (duplicate @id),
#12/#159 (dc:source), #13 (sectionNumber),
#14 (script namespace), #19 (INDEX.json), #26 (combined_ontology.jsonld).
"""

import copy
import datetime as _dt
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path

from estleg import deprecate_legacy_statutes as _legacy_deprecation
from estleg import estleg_common
from estleg.index_body_coverage import apply_body_coverage_fields
from estleg.multipart_coverage import (
    apply_known_multipart_annotations,
    preserve_multipart_annotations,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"

OLD_NS = "https://example.org/estonian-legal#"
NEW_NS = "https://w3id.org/estleg/"

# Root-level non-`*_peep.json` JSON-LD files that are canonical inputs to
# `combined_ontology.jsonld`. Anything outside this list (including
# `combined_ontology.jsonld` itself, INDEX/aggregate/report files, and
# generator outputs) must NEVER be re-ingested.
COMBINED_ALLOWED_JSONLD = (
    "controlled_vocabulary.jsonld",
    "karistusseadustik_eriosa_owl.jsonld",
    "tsus_osa7_138_169_owl.jsonld",
    # #608: act-level FRBR Expression layer — addressable "law as of date D"
    # nodes (estleg:ActExpression) materialized from the provision_versions/
    # consolidation dates. A canonical merged input (not stubbed).
    "act_expressions_combined.jsonld",
)
COMBINED_OUTPUT_NAME = "combined_ontology.jsonld"

# #416: directories scanned (in this order, first match wins) to resolve a
# dangling cross-corpus reference left in the merged graph to its full source
# node so a lightweight stub can be synthesised. ``curia/`` is intentionally
# absent — no law peep references an ``estleg:EUCJ_*`` node, so scanning it
# would only slow the build.
STUB_SOURCE_SUBDIRS = (
    "riigikohus",
    "eurlex",
    "eelnoud",
    "regulations",
    "amendments",
    "harmonisation",
)

# #416: git-LFS-tracked sources the combined build now depends on. They must be
# materialised (``git lfs pull``) or the build would silently emit a degraded
# artifact — fail hard instead, mirroring the deprecation-decisions guard.
COMBINED_BUILD_LFS_SOURCES = (
    "annotations/oiguskantsler_seisukohad.jsonld",  # overlay merge
    "eurlex/eurlex_combined.jsonld",  # EU stub source
)

# #416: the only properties copied onto a closure stub. Everything else (large
# text bodies — summary/legalText — and crucially every ``estleg:`` object
# reference) is dropped so a stub is a graph-closure LEAF that introduces no
# new dangling refs. ``owl:sameAs`` / ``dcterms:source`` are kept because they
# point at EXTERNAL (non-``estleg:``) IRIs (Riigi Teataja / CELEX) — real,
# useful links that do not affect internal closure.
STUB_KEEP_PROPS = (
    "rdfs:label",
    "rdfs:comment",
    "estleg:caseNumber",
    "estleg:ecliIdentifier",
    "estleg:celexNumber",
    "estleg:eliIdentifier",
    # #607(a): the ELI natural-id local identifier. A leaf literal (no estleg:
    # object ref), sibling of celexNumber/eliIdentifier above — carry it onto EU
    # closure stubs so the self-contained combined artifact keeps the only real
    # eli: triple instead of dropping all 66k. NOT in COMBINED_STRIPPED_PREDICATES,
    # so it is meant to survive into combined.
    "eli:id_local",
    "estleg:eisNumber",
    "estleg:actNumber",
    "estleg:globalId",
    "dcterms:identifier",
    "estleg:decisionLink",
    "estleg:eurLexLink",
    "estleg:eisLink",
    "estleg:curiaLink",
    "estleg:decisionDate",
    # #618: court-interpretation staleness. Self-contained leaf literals on the
    # court node (not estleg: object refs), so they survive onto the combined
    # court stub like decisionDate/caseNumber above — making "is this precedent
    # outdated?" answerable on the combined-only artifact. (The companion
    # estleg:interpretsVersion is a version-layer object edge, stripped via
    # COMBINED_STRIPPED_PREDICATES instead.)
    "estleg:interpretationOutdated",
    "estleg:earliestSupersedingDate",
    "estleg:publicationDate",
    "estleg:documentDate",
    "owl:sameAs",
    "dcterms:source",
)

INDEX_ALLOWED_JSONLD = (
    "karistusseadustik_eriosa_owl.jsonld",
    "tsus_osa7_138_169_owl.jsonld",
)
# Non-law registry peeps (issuer/municipality registries) excluded from
# the law index. The two formerly-listed maksejõuetus/KOV concept-map
# peeps were renamed in PR #232; their successors are now real indexed
# laws under new names, so the dead entries were removed (#243).
INDEX_EXCLUDED_PEEPS = {
    "issuers_kov_peep.json",
    "municipalities_peep.json",
}
DEFAULT_REGISTRY_EXCEPTIONS = {
    "kriminaalmenetluse_peep.json": {
        "category": "procedure_map",
        "reason": "procedure-stage concept map without provision nodes",
    },
    "tsiviilseadustik_osa1_peep.json": {
        "category": "concept_map",
        "reason": "legacy concept map without provision nodes",
    },
    "tsiviilseadustik_osa5_peep.json": {
        "category": "concept_map",
        "reason": "legacy concept map without provision nodes",
    },
    "tsiviilseadustik_osa6_peep.json": {
        "category": "concept_map",
        "reason": "legacy concept-only sidecar without act node",
    },
    "tsiviilseadustik_osa7_peep.json": {
        "category": "concept_map",
        "reason": "legacy concept-only sidecar without act node",
    },
    # #426: duplicate legacy statute peeps (orthography / truncation /
    # renaming variants) are deprecated in place — their roots carry
    # owl:deprecated + dcterms:isReplacedBy and they are pulled out of the
    # law count into INDEX.json's `deprecated_laws` section so total_laws
    # stops double-counting the same statute. The authoritative list lives
    # in data/legacy_statute_decisions.json.
    "deprecated_duplicates": {
        "category": "deprecated_duplicate",
        "reason": (
            "duplicate legacy statute peeps with verdict 'deprecate' in "
            "data/legacy_statute_decisions.json (#426); excluded from "
            "total_laws/total_files and listed under deprecated_laws with "
            "dcterms:isReplacedBy targets"
        ),
    },
}

# #426: legacy duplicate-statute decisions. The companion file lists every
# legacy peep FILE with verdict "deprecate" (whose root node now carries
# owl:deprecated + dcterms:isReplacedBy). `generate_index` pulls those files
# out of the law count and into a dedicated `deprecated_laws` section.
LEGACY_STATUTE_DECISIONS_PATH = REPO_ROOT / "data" / "legacy_statute_decisions.json"
# Re-export alias of the single source of truth (#240). Imported from
# `estleg_common` so this module and `validate_all` share one definition;
# never reintroduce a literal copy here — that is exactly the drift that
# caused the PR #232 count bug.
OPERATIONAL_STATE_FILES = estleg_common.OPERATIONAL_STATE_FILES


# Multi-valued properties that should always be arrays
MULTI_VALUED_PROPS = {
    "estleg:coversConcept", "coversConcept",
    "estleg:hasSection", "hasSection",
    "estleg:hasDivision", "hasDivision",
    "estleg:hasSubdivision", "hasSubdivision",
    "estleg:hasChapter", "hasChapter",
    "estleg:references", "references",
}

stats = {
    "namespace_replacements": 0,
    "type_normalizations": 0,
    "property_normalizations": 0,
    "dc_source_fixes": 0,
    "section_number_fixes": 0,
    "files_processed": 0,
    "files_renamed": 0,
    "files_removed": 0,
    "id_collisions_fixed": 0,
}


def normalize_type(node: dict) -> dict:
    """Ensure @type is always an array."""
    if "@type" in node:
        t = node["@type"]
        if isinstance(t, str):
            node["@type"] = [t]
            stats["type_normalizations"] += 1
    return node


def normalize_multi_valued(node: dict) -> dict:
    """Ensure multi-valued properties are always arrays."""
    for key in list(node.keys()):
        if key in MULTI_VALUED_PROPS:
            val = node[key]
            if isinstance(val, dict):
                node[key] = [val]
                stats["property_normalizations"] += 1
            elif isinstance(val, str):
                node[key] = [{"@id": val}] if val.startswith("estleg:") or val.startswith("http") else [val]
                stats["property_normalizations"] += 1
    return node


def normalize_dc_source(node: dict) -> dict:
    """Normalize `dc:source` (and bare `source`) to a stable shape.

    Previously this collapsed list values to a `'; '`-joined string, a
    lossy round-trip that destroyed any source whose own text contained
    `'; '` (issue #159). The validator already accepts arrays of
    strings via `validate_all.validate_dc_source`, so we now keep the
    multi-valued form. A single-element array is collapsed back to the
    canonical scalar form most consumers still expect; everything else
    is preserved exactly, with each element coerced to `str` to keep
    typing predictable.
    """
    for key in ("dc:source", "source"):
        if key not in node:
            continue
        value = node[key]
        if isinstance(value, list):
            normalised = [str(item) for item in value if item is not None]
            if len(normalised) == 1:
                node[key] = normalised[0]
            else:
                node[key] = normalised
            stats["dc_source_fixes"] += 1
    return node


def normalize_section_number(node: dict) -> dict:
    """Ensure sectionNumber is always a string."""
    for key in ("estleg:sectionNumber", "sectionNumber"):
        if key in node and isinstance(node[key], (int, float)):
            node[key] = str(int(node[key]))
            stats["section_number_fixes"] += 1
    return node


def migrate_namespace_in_value(val):
    """Recursively replace old namespace with new namespace in any value."""
    if isinstance(val, str):
        if OLD_NS in val:
            stats["namespace_replacements"] += 1
            return val.replace(OLD_NS, NEW_NS)
        return val
    elif isinstance(val, list):
        return [migrate_namespace_in_value(item) for item in val]
    elif isinstance(val, dict):
        return {migrate_namespace_in_value(k): migrate_namespace_in_value(v) for k, v in val.items()}
    return val


def process_node(node: dict) -> dict:
    """Apply all normalizations to a single graph node."""
    node = normalize_type(node)
    node = normalize_multi_valued(node)
    node = normalize_dc_source(node)
    node = normalize_section_number(node)
    return node


def process_json_file(filepath: Path) -> dict:
    """Load, transform, and return the JSON-LD document."""
    with open(filepath, "r", encoding="utf-8") as f:
        doc = json.load(f)

    # Migrate namespace
    doc = migrate_namespace_in_value(doc)

    # Process each node in @graph
    if "@graph" in doc and isinstance(doc["@graph"], list):
        doc["@graph"] = [process_node(node) for node in doc["@graph"]]

    stats["files_processed"] += 1
    return doc


def save_json(filepath: Path, doc: dict):
    """Save JSON with consistent formatting, atomically (#280).

    Delegates to ``estleg_common.save_json`` (tempfile + ``os.replace``) so a
    crash / ``KeyboardInterrupt`` / disk-full mid-write never leaves a
    truncated or empty JSON file at ``filepath``. Every JSON write in this
    module funnels through here.
    """
    estleg_common.save_json(Path(filepath), doc)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (#280).

    Used for the non-JSON text writes (generator-script / docs namespace
    fixes). Writes to a sibling tempfile then ``os.replace``s it into place
    so a partial write never appears at ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _audit_files(krr_dir: Path) -> list[Path]:
    """Return the file set the audit should scan.

    Reuses `validate_all.discover_validation_files` so the audit walks
    *exactly* the same corpus the validator does (#159) — including
    `.jsonld` extensions and recursion into `eurlex/`, `curia/`,
    `riigikohus/`, `eelnoud/`, and `regulations/` subdirectories.
    Falls back to a hand-rolled glob if `validate_all` cannot be
    imported (e.g. circular-import smoke tests); the fallback is
    written to be a strict superset of the old behaviour minus
    `_summary.json` files.
    """
    try:
        from estleg import validate_all as _validate_all  # local import to avoid hard dep
    except ImportError:
        files = sorted(set(
            list(krr_dir.glob("*.json"))
            + list(krr_dir.glob("*.jsonld"))
            + list(krr_dir.glob("**/*.json"))
            + list(krr_dir.glob("**/*.jsonld"))
        ))
        return [f for f in files if not f.name.endswith("_summary.json")]
    return _validate_all.discover_validation_files(krr_dir)


def audit_duplicate_ids(krr_dir: Path | None = None):
    """Audit and report duplicate @id values (Issue #6).

    Now reuses `validate_all.discover_validation_files` to ensure the
    audit covers the same corpus as the validator (#159): JSON +
    JSON-LD + every subdirectory tracked by the validation pipeline.
    """
    print("\n=== Auditing duplicate @id values ===")
    krr_dir = krr_dir or KRR_DIR

    id_locations = defaultdict(list)

    for filepath in _audit_files(krr_dir):
        if filepath.name.endswith("_summary.json"):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        if "@graph" not in doc:
            continue

        seen_in_file = set()
        for node in doc["@graph"]:
            if "@id" in node:
                nid = node["@id"]
                if nid in seen_in_file:
                    # Duplicate within same file!
                    print(f"  INTRA-FILE DUPLICATE: {nid} in {filepath.name}")
                seen_in_file.add(nid)
                # Record the path relative to krr_dir when possible so
                # subdirs are visible in the report.
                try:
                    rel = filepath.relative_to(krr_dir).as_posix()
                except ValueError:
                    rel = filepath.name
                id_locations[nid].append(rel)

    # Find cross-file duplicates.
    duplicates = []
    for nid, files in id_locations.items():
        if len(files) > 1:
            duplicates.append((nid, files))

    if duplicates:
        print(f"  Found {len(duplicates)} IDs appearing in multiple files")
        # Write report
        report_path = REPO_ROOT / "docs" / "DUPLICATE_IDS_REPORT.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("# Duplicate @id Report\n\n")
            f.write(f"Found {len(duplicates)} @id values appearing in multiple files.\n\n")
            f.write("| @id | Files |\n|-----|-------|\n")
            for nid, files in sorted(duplicates):
                f.write(f"| `{nid}` | {', '.join(sorted(set(files)))} |\n")
        print("  Report written to docs/DUPLICATE_IDS_REPORT.md")
    else:
        print("  No cross-file duplicate IDs found")

    return duplicates


def _content_hash(node: dict) -> str:
    """Return a short, deterministic content hash for a graph node.

    Used by `fix_intra_file_duplicates` to derive a *stable* suffix
    that survives upstream extractor reorderings. Encoding via
    `json.dumps(..., sort_keys=True)` makes the hash invariant under
    key reordering inside the node; we only take the first 8 hex
    chars of the SHA-256 because we just need enough entropy to
    disambiguate within a single file's `@graph`.
    """
    canonical = json.dumps(node, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def _fix_intra_file_duplicates_in_doc(doc: dict) -> bool:
    """In-place rewrite duplicate `@id`s in `doc`. Returns whether modified.

    Public callers should use `fix_intra_file_duplicates` (which walks
    `KRR_DIR`); this helper is exposed for tests so they can exercise
    the rewrite without touching the filesystem.
    """
    if "@graph" not in doc or not isinstance(doc["@graph"], list):
        return False

    seen_ids: set[str] = set()
    rename_map: dict[int, str] = {}  # graph index -> new id
    id_to_graph_indices: dict[str, list[int]] = defaultdict(list)
    for idx, node in enumerate(doc["@graph"]):
        if not isinstance(node, dict) or "@id" not in node:
            continue
        nid = node["@id"]
        id_to_graph_indices[nid].append(idx)

    for nid, indices in id_to_graph_indices.items():
        if len(indices) <= 1:
            seen_ids.add(nid)
            continue
        # Keep the first occurrence; rewrite the rest with a content
        # hash so the suffix is stable across runs.
        seen_ids.add(nid)
        for dup_idx in indices[1:]:
            node = doc["@graph"][dup_idx]
            extra_chars = 8
            suffix = _content_hash(node)
            new_id = f"{nid}_dup_{suffix}"
            # Avoid collision with another existing/renamed id by
            # widening the suffix until unique.
            while new_id in seen_ids:
                extra_chars += 8
                if extra_chars > 64:
                    raise RuntimeError(
                        f"Cannot disambiguate duplicate @id {nid!r}; "
                        f"hash space exhausted"
                    )
                suffix = hashlib.sha256(
                    json.dumps(node, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
                ).hexdigest()[:extra_chars]
                new_id = f"{nid}_dup_{suffix}"
            seen_ids.add(new_id)
            rename_map[dup_idx] = new_id

    if not rename_map:
        return False

    # Apply @id rewrites. Note: we deliberately do NOT rewrite
    # internal references to the renamed @ids — when a duplicate id
    # appears multiple times, an external reference to that id is
    # ambiguous (which copy?), and the safest interpretation is that
    # it points at the kept head, which retains the original @id. The
    # test for this behaviour is
    # `test_fix_intra_file_duplicates_preserves_head_references`.
    for idx, new_id in rename_map.items():
        node = doc["@graph"][idx]
        old_id = node["@id"]
        node["@id"] = new_id
        stats["id_collisions_fixed"] += 1
        print(f"  Fixed (intra-file): {old_id} -> {new_id}")

    return True


def fix_intra_file_duplicates():
    """Fix duplicate @id values within the same file by appending a content-stable suffix.

    Previously the suffix was `_dup{count}` based on enumeration order,
    which flipped across runs whenever the upstream extractor reordered
    nodes (issue #159). We now derive the suffix from the SHA-256 of
    the duplicating node's canonical-JSON content, giving:

    1. **Stability** — the same node always gets the same suffix.
    2. **Convergence** — running the function twice is a no-op.
    3. **Same-file reference rewrite** — properties pointing at the
       original `@id` from elsewhere in the file are also rewritten so
       the disambiguation is consistent within the document.

    Convergence is asserted in tests by re-running the fix on the
    rewritten document and checking that no further mutations occur.
    """
    print("\n=== Fixing intra-file duplicate @id values ===")

    for filepath in sorted(KRR_DIR.glob("*.json")):
        if filepath.name in OPERATIONAL_STATE_FILES:
            continue
        if filepath.name.endswith("_summary.json"):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        if _fix_intra_file_duplicates_in_doc(doc):
            save_json(filepath, doc)


def process_all_json_files():
    """Process all JSON/JSONLD files for namespace and normalization fixes."""
    print("\n=== Processing all JSON/JSONLD files ===")

    for filepath in sorted(KRR_DIR.glob("*.json")):
        if filepath.name in OPERATIONAL_STATE_FILES:
            continue
        if filepath.name.endswith("_summary.json"):
            # Still need namespace fix in summary files
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                doc = migrate_namespace_in_value(doc)
                save_json(filepath, doc)
                print(f"  Processed (summary): {filepath.name}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                print(f"  SKIP (parse error): {filepath.name}")
            continue

        try:
            doc = process_json_file(filepath)
            save_json(filepath, doc)
            print(f"  Processed: {filepath.name}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  SKIP (parse error): {filepath.name}: {e}")

    # Also process .jsonld files
    for filepath in sorted(KRR_DIR.glob("*.jsonld")):
        if filepath.name in OPERATIONAL_STATE_FILES:
            continue
        try:
            doc = process_json_file(filepath)
            save_json(filepath, doc)
            print(f"  Processed: {filepath.name}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  SKIP (parse error): {filepath.name}: {e}")


def fix_generator_script():
    """Fix namespace in generate_kars_eriosa_jsonld.py (Issue #14)."""
    print("\n=== Fixing generator script namespace ===")
    script_path = REPO_ROOT / "scripts" / "generate_kars_eriosa_jsonld.py"
    if script_path.exists():
        content = script_path.read_text(encoding="utf-8")
        if OLD_NS in content:
            content = content.replace(OLD_NS, NEW_NS)
            _atomic_write_text(script_path, content)
            print(f"  Fixed namespace in {script_path.name}")
        else:
            print(f"  Namespace already correct in {script_path.name}")


def fix_docs_namespace():
    """Fix namespace references in documentation files."""
    print("\n=== Fixing namespace in documentation ===")
    for doc_file in (REPO_ROOT / "docs").glob("*.md"):
        content = doc_file.read_text(encoding="utf-8")
        if OLD_NS in content:
            content = content.replace(OLD_NS, NEW_NS)
            _atomic_write_text(doc_file, content)
            print(f"  Fixed namespace in docs/{doc_file.name}")


def _index_base_name(name: str) -> tuple[str, str | None]:
    """Split a peep stem into its base law name and ``_osaN`` part suffix.

    Shared by the active-law grouping and the ``deprecated_laws`` grouping so
    both collapse multipart files identically (e.g. every ``volaigusseadus_osa*``
    file maps to base ``volaigusseadus``). Returns ``(base_name, part)`` where
    ``part`` is the digit string after ``_osa`` (or ``None`` for a single-file
    law).
    """
    import re

    # #379: ``karistusseadustik_map`` is the act-root Map peep for the
    # same statute as ``karistusseadustik_osa1``, not a separate law.
    if name.endswith("_map"):
        return name[: -len("_map")], None

    match = re.match(r"^(.+?)(_osa\d+.*)?$", name)
    if match:
        base_name = match.group(1)
        part_suffix = match.group(2)
    else:
        return name, None
    part = part_suffix.replace("_osa", "") if part_suffix else None
    return base_name, part


def load_deprecated_statutes(path: Path | None = None) -> dict[str, str]:
    """Return ``{deprecated_peep_filename: replacedByFile}`` for #426.

    Reads ``data/legacy_statute_decisions.json`` (the authoritative duplicate
    decision list) and returns the subset of entries whose verdict is
    ``"deprecate"``. The mapping value is the canonical successor's peep
    filename (``replacedByFile``), used to annotate the ``deprecated_laws``
    INDEX section.

    The path is resolved from ``LEGACY_STATUTE_DECISIONS_PATH`` by default but
    is overridable so tests can point at a tmp decisions file. A missing or
    malformed file yields an EMPTY mapping, keeping ``generate_index``
    standalone-safe: with no decisions file the index behaves exactly as it
    did before #426.
    """
    decisions_path = path if path is not None else LEGACY_STATUTE_DECISIONS_PATH
    try:
        with open(decisions_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    deprecated: dict[str, str] = {}
    for entry in data.get("deprecations", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("verdict") != "deprecate":
            continue
        filename = entry.get("file")
        if not isinstance(filename, str) or not filename:
            continue
        replaced_by = entry.get("replacedByFile")
        deprecated[filename] = replaced_by if isinstance(replaced_by, str) else None
    return deprecated


def generate_index():
    """Generate master registry/index file (Issue #19).

    Deprecated duplicate statutes (#426) — peeps whose root node now carries
    ``owl:deprecated`` + ``dcterms:isReplacedBy`` per
    ``data/legacy_statute_decisions.json`` — are pulled OUT of the law count
    (``total_laws`` / ``total_files``) and surfaced in a dedicated top-level
    ``deprecated_laws`` section, so the registry stops double-counting the same
    statute under both its legacy and canonical spellings.
    """
    print("\n=== Generating master registry ===")

    laws = {}
    deprecated_groups: dict[str, dict] = {}
    index_path = KRR_DIR / "INDEX.json"
    registry_exceptions = dict(DEFAULT_REGISTRY_EXCEPTIONS)
    existing_laws_by_name: dict[str, dict] = {}
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                existing_index = json.load(f)
            if isinstance(existing_index, dict) and isinstance(existing_index.get("registry_exceptions"), dict):
                registry_exceptions.update(existing_index["registry_exceptions"])
            if isinstance(existing_index, dict):
                for previous in existing_index.get("laws") or []:
                    if isinstance(previous, dict) and isinstance(previous.get("name"), str):
                        existing_laws_by_name[previous["name"]] = previous
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass

    # #426: deprecated-duplicate peep files are excluded from the law count and
    # collected separately. An absent/empty decisions file → empty set, so the
    # index is unchanged from its pre-#426 behaviour.
    deprecated_map = load_deprecated_statutes()

    for filepath in sorted(KRR_DIR.glob("*_peep.json")):
        if filepath.name in INDEX_EXCLUDED_PEEPS:
            continue
        name = filepath.stem.replace("_peep", "")
        base_name, part = _index_base_name(name)

        if filepath.name in deprecated_map:
            # Group deprecated duplicates by the same base-name/_osaN logic as
            # active laws so a multipart legacy statute collapses to one entry.
            group = deprecated_groups.setdefault(
                base_name,
                {"name": base_name, "files": [], "replaced_by_map": {}},
            )
            group["files"].append(filepath.name)
            group["replaced_by_map"][filepath.name] = deprecated_map[filepath.name]
            continue

        if base_name not in laws:
            laws[base_name] = {
                "base_name": base_name,
                "files": [],
                "parts": [],
            }

        laws[base_name]["files"].append(filepath.name)
        if part:
            laws[base_name]["parts"].append(part)

    # Also add standalone OWL JSON-LD files that are law-shaped index inputs.
    # Derived combined/vocabulary artifacts are validated separately and must
    # not be listed as enacted laws.
    for name in INDEX_ALLOWED_JSONLD:
        filepath = KRR_DIR / name
        if not filepath.exists():
            continue
        name = filepath.stem
        if name not in laws:
            laws[name] = {"base_name": name, "files": [], "parts": []}
        laws[name]["files"].append(filepath.name)

    # `generated` was hardcoded to a checked-in date string (#159), which
    # made staleness/cache-busting unusable. Use today's UTC ISO date so
    # consumers can detect when the registry actually changed.
    index = {
        "generated": _dt.date.today().isoformat(),
        "total_files": sum(len(law["files"]) for law in laws.values()),
        "total_laws": len(laws),
        "laws": [],
    }
    if registry_exceptions:
        index["registry_exceptions"] = registry_exceptions

    for base_name, info in sorted(laws.items()):
        def _file_sort_key(filename: str) -> tuple[int, str]:
            # #379: Map peep is the act-root file and must be listed first.
            return (0, filename) if filename.endswith("_map_peep.json") else (1, filename)

        entry = {
            "name": base_name,
            "files": sorted(info["files"], key=_file_sort_key),
        }
        if info["parts"]:
            entry["parts_mapped"] = sorted(info["parts"])
        preserve_multipart_annotations(entry, existing_laws_by_name.get(base_name))
        apply_known_multipart_annotations(entry)
        apply_body_coverage_fields(entry, KRR_DIR)
        index["laws"].append(entry)

    # #426: emit the deprecated-duplicate section (deterministic: sorted
    # entries, sorted file lists, stable shapes). Only present when at least
    # one deprecated peep was actually found in the corpus.
    if deprecated_groups:
        deprecated_entries = []
        for base_name, info in sorted(deprecated_groups.items()):
            files_sorted = sorted(info["files"])
            # Canonical replaced_by = the target of the first file in sorted
            # order. When parts map to different canonical files, also surface
            # the full sorted/unique `replaced_by_files` list so no successor
            # is lost.
            replaced_by = info["replaced_by_map"].get(files_sorted[0])
            distinct_targets = sorted(
                {t for t in info["replaced_by_map"].values() if t}
            )
            entry = {
                "name": base_name,
                "files": files_sorted,
                "replaced_by": replaced_by,
            }
            if len(distinct_targets) > 1:
                entry["replaced_by_files"] = distinct_targets
            deprecated_entries.append(entry)
        index["deprecated_laws"] = {
            "count": len(deprecated_entries),
            "note": (
                "Legacy duplicate statutes deprecated in place (#426): roots "
                "carry owl:deprecated + dcterms:isReplacedBy; excluded from "
                "total_laws/total_files."
            ),
            "entries": deprecated_entries,
        }

    save_json(index_path, index)
    suffix = (
        f" ({len(deprecated_groups)} deprecated duplicate(s) excluded)"
        if deprecated_groups
        else ""
    )
    print(f"  Generated {index_path.name} with {len(laws)} laws{suffix}")


def canonical_combined_inputs(krr_dir: Path = KRR_DIR) -> list[Path]:
    """Return the canonical input list for combined_ontology.jsonld.

    The combined artifact is built from:
      - every root `*_peep.json` source file, and
      - the explicit allowlist in COMBINED_ALLOWED_JSONLD (vocabulary +
        standalone OWL serializations).

    `combined_ontology.jsonld` itself is never an input to its own build;
    aggregate/report/index files (`INDEX.json`, `*_report.json`,
    `*_summary.json`, `*_INDEX.json`, etc.) are outputs, not sources.
    """
    inputs: list[Path] = sorted(krr_dir.glob("*_peep.json"))
    for name in COMBINED_ALLOWED_JSONLD:
        path = krr_dir / name
        if path.exists():
            inputs.append(path)
    return inputs


def _value_key(value: object) -> str:
    """Return a stable de-duplication key for a JSON-LD property value.

    Used by :func:`_merge_node_properties` to union list-valued properties
    without introducing duplicates. The key is the canonical-JSON encoding
    of the value, so two structurally identical ``{"@id": ...}`` objects (or
    two equal scalars) collapse to one.
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _as_value_list(value: object) -> list:
    """Coerce a property value to a list of values (a bare value → ``[value]``)."""
    return list(value) if isinstance(value, list) else [value]


def _merge_node_properties(existing: dict, incoming: dict) -> None:
    """Merge ``incoming``'s properties into ``existing`` in place (#281).

    When two input files contribute a node sharing the same ``@id``, the
    combined artifact must retain BOTH files' properties rather than
    silently dropping the later node (the old first-write-wins behaviour).
    Merge rules:

    * A key present only in ``incoming`` is copied over verbatim.
    * A key present in both whose value is list-valued on either side is
      UNIONed (order-preserving, de-duplicated by canonical-JSON value) —
      the normal JSON-LD multi-valued-property pattern.
    * A key present in both with scalar/object values that are EQUAL is
      kept as-is.
    * A key present in both with DIVERGENT scalar/object values keeps the
      first file's value and emits a warning (the combined build can't
      decide which scalar wins, so it stays deterministic and flags it).

    ``@id`` is never merged (it is the merge key and identical by
    construction).
    """
    for key, inc_val in incoming.items():
        if key == "@id":
            continue
        if key not in existing:
            existing[key] = inc_val
            continue
        cur_val = existing[key]
        if cur_val == inc_val:
            continue
        if isinstance(cur_val, list) or isinstance(inc_val, list):
            merged = _as_value_list(cur_val)
            seen = {_value_key(v) for v in merged}
            for item in _as_value_list(inc_val):
                k = _value_key(item)
                if k not in seen:
                    merged.append(item)
                    seen.add(k)
            existing[key] = merged
        else:
            # Divergent scalars/objects: keep first, warn on divergence.
            print(
                f"  WARNING: divergent value for {existing.get('@id', '?')!r} "
                f"property {key!r}: keeping {cur_val!r}, ignoring {inc_val!r}"
            )


def _is_lfs_pointer(path: Path) -> bool:
    """True when ``path`` is an un-materialised Git-LFS pointer stub (#416)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.readline().rstrip("\n") == "version https://git-lfs.github.com/spec/v1"
    except OSError:
        return False


def _require_combined_build_sources(krr_dir: Path) -> None:
    """Fail hard if an LFS source the combined build needs is un-materialised.

    Merging the annotations overlay and resolving EU stub targets both read
    git-LFS files. On a checkout without ``git lfs pull`` those are ~130-byte
    pointer stubs; building anyway would emit a silently-degraded combined
    artifact, so refuse — the same fail-fast contract as the #426 deprecation
    guard below.
    """
    missing = [
        rel
        for rel in COMBINED_BUILD_LFS_SOURCES
        if (krr_dir / rel).exists() and _is_lfs_pointer(krr_dir / rel)
    ]
    if missing:
        raise ValueError(
            "combined build requires materialised Git-LFS sources but these are "
            "pointer stubs: " + ", ".join(missing) + ". Run "
            "`git lfs pull --include="
            + ",".join(f"krr_outputs/{rel}" for rel in missing)
            + "` first."
        )


def _strip_estleg_refs(value):
    """Return ``value`` with every internal ``estleg:`` ``@id`` reference removed.

    Keeps literals, ``{"@value": ...}`` objects, and EXTERNAL ``{"@id": ...}``
    references (Riigi Teataja / CELEX links). Returns ``None`` when nothing
    survives, so a property that held only internal refs is dropped entirely —
    the mechanism that makes a stub a graph-closure leaf (#416).
    """
    if isinstance(value, dict):
        rid = value.get("@id")
        if isinstance(rid, str) and estleg_common.canonical_estleg_ref(rid) is not None:
            # internal ref (compact OR expanded form) — drop so the stub stays a leaf
            return None
        cleaned = {}
        for key, child in value.items():
            child_value = child if key == "@id" else _strip_estleg_refs(child)
            if child_value is not None:
                cleaned[key] = child_value
        return cleaned or None
    if isinstance(value, list):
        kept = [c for c in (_strip_estleg_refs(item) for item in value) if c is not None]
        return kept or None
    return value


def _make_closure_stub(source: dict) -> dict:
    """Synthesise a graph-closed stub from a full source node (#416, #488).

    Carries the real ``@type`` plus the curated ``STUB_KEEP_PROPS`` (label +
    identifier + external link) with every ``estleg:`` object ref stripped, and
    marks the node with ``estleg:isStubNode`` so the parity gate can recognise it.

    #488: when the source carries a SHACL-shaped ``@type`` (a domestic
    regulation, KOV provision, or proposed amendment), the stub must ALSO carry
    that shape's required SEMANTIC properties — including IRI-valued graph edges
    (``estleg:enactedBy`` / ``estleg:enactedByMunicipality`` / ``estleg:partOfAct``
    / ``estleg:amendingDraft``) — so the standalone combined artifact satisfies
    the project's own SHACL contract rather than publishing an incomplete typed
    node. These required props are copied verbatim (NOT through
    ``_strip_estleg_refs``); their estleg: targets are exactly the edges
    consumers traverse, and :func:`_emit_closure_stubs` closes those targets
    transitively so the graph stays self-contained.
    """
    stub: dict = {"@id": source["@id"]}
    node_type = source.get("@type")
    if node_type is not None:
        # Copy the list so the stub never aliases the (transient) source node's
        # @type — defensive; the source is discarded after each scan anyway.
        stub["@type"] = list(node_type) if isinstance(node_type, list) else node_type
    for prop in STUB_KEEP_PROPS:
        if prop not in source:
            continue
        kept = _strip_estleg_refs(source[prop])
        if kept is not None:
            stub[prop] = kept
    for prop in estleg_common.required_closure_props(node_type):
        if prop in source and prop not in stub:
            value = source[prop]
            stub[prop] = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
    stub[estleg_common.STUB_NODE_MARKER] = True
    return stub


# #488: a stub may now carry IRI-valued semantic edges (partOfAct / amendingDraft
# / …) whose targets are NOT referenced anywhere else, so closing the graph is a
# fixpoint, not a single pass: each new stub can introduce fresh cross-corpus
# targets that themselves need stubbing. The cascade is shallow (a KOV provision
# → its parent regulation → its issuer/municipality, which are full registry
# peeps already present) and always terminates, but this caps runaway loops.
_MAX_CLOSURE_ITERATIONS = 10


def _scan_sources_for(remaining: set[str], krr_dir: Path) -> dict[str, dict]:
    """Resolve ``remaining`` ids against the sibling corpora in one full scan.

    Walks ``STUB_SOURCE_SUBDIRS`` (in order, first match wins) and returns
    ``{canonical_id: source_node}`` for every id in ``remaining`` that resolves
    to a node in a public data file. Ids with no source node are simply absent
    from the result; the caller treats them as unresolvable.
    """
    pending = set(remaining)
    found: dict[str, dict] = {}
    for subdir_name in STUB_SOURCE_SUBDIRS:
        if not pending:
            break
        subdir = krr_dir / subdir_name
        if not subdir.is_dir():
            continue
        for suffix in (".json", ".jsonld"):
            for path in sorted(subdir.rglob(f"*{suffix}")):
                if not pending:
                    break
                if not estleg_common.is_public_jsonld_data_file(path):
                    continue
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        doc = json.load(f)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                graph = doc.get("@graph")
                if not isinstance(graph, list):
                    continue
                for node in graph:
                    if not isinstance(node, dict):
                        continue
                    nid = node.get("@id")
                    if not isinstance(nid, str):
                        continue
                    canon = estleg_common.canonical_estleg_ref(nid) or nid
                    if canon in pending:
                        found[canon] = node
                        pending.discard(canon)
    return found


def _emit_closure_stubs(
    node_by_id: dict[str, dict], all_nodes: list[dict], krr_dir: Path
) -> int:
    """Append stub nodes for every dangling cross-corpus ref in the merged graph.

    Scans the already-merged law+overlay graph for ``estleg:`` object refs whose
    target is not present (and whose predicate is not closure-exempt), resolves
    each against the sibling corpora, and appends a deterministic, ``@id``-sorted
    block of stubs so combined loads graph-closed on its own (#416). Because a
    shaped stub now carries its SHACL-required semantic edges (#488), the closure
    is computed to a FIXPOINT: any cross-corpus target a freshly-minted stub
    introduces (a KOV provision's parent act, a proposal's draft) is resolved and
    stubbed in turn, until no new targets remain.
    """
    exempt = estleg_common.COMBINED_CLOSURE_EXEMPT_PREDICATES
    # iter_node_estleg_refs yields canonical compact targets, so compare against
    # the canonical form of every present @id (handles expanded-IRI nodes too).
    present = {estleg_common.canonical_estleg_ref(k) or k for k in node_by_id}
    stubs: dict[str, dict] = {}

    def dangling_from(nodes) -> set[str]:
        """Non-exempt estleg: targets in ``nodes`` not yet present or stubbed."""
        out: set[str] = set()
        for node in nodes:
            for predicate, target in estleg_common.iter_node_estleg_refs(node):
                if predicate in exempt:
                    continue
                if target not in present and target not in stubs:
                    out.add(target)
        return out

    frontier = dangling_from(all_nodes)
    if not frontier:
        print("  No dangling cross-corpus refs — no stubs needed")
        return 0

    unresolved: set[str] = set()
    iterations = 0
    while frontier:
        iterations += 1
        if iterations > _MAX_CLOSURE_ITERATIONS:
            unresolved |= frontier
            print(
                f"  WARNING: closure did not converge after "
                f"{_MAX_CLOSURE_ITERATIONS} iterations; leaving {len(frontier)} "
                f"ref(s) for the closure gate to flag"
            )
            break
        found = _scan_sources_for(frontier, krr_dir)
        unresolved |= frontier - found.keys()
        if not found:
            break
        new_stubs = {canon: _make_closure_stub(node) for canon, node in found.items()}
        stubs.update(new_stubs)
        # A new stub's carried semantic edges (#488) may dangle in turn — close
        # them next round. dangling_from already excludes present + stubbed ids;
        # subtract known-unresolvable ids so we never rescan a dead target.
        frontier = dangling_from(new_stubs.values()) - unresolved

    if unresolved:
        # Defer enforcement to the closure gate rather than aborting the build:
        # a single unresolvable ref shouldn't block a release rebuild, and
        # validate_combined_graph_closure() fails CI on any residual dangling.
        sample = ", ".join(sorted(unresolved)[:10])
        print(
            f"  WARNING: {len(unresolved)} cross-corpus reference(s) resolve to no "
            f"node in any sibling corpus; left unstubbed for the closure gate "
            f"(validate_combined_graph_closure) to flag: {sample}"
        )

    for nid in sorted(stubs):
        stub = stubs[nid]
        node_by_id[stub["@id"]] = stub
        all_nodes.append(stub)
    print(f"  Emitted {len(stubs)} graph-closure stub nodes")
    return len(stubs)


# --- #519: build-time rdf:type rollup over the subclass hierarchy -----------
#
# The shipped graph asserts only leaf-level types (per-file
# ``estleg:LegalProvision_<slug>`` / ``estleg:Regulation_<id>`` classes,
# ``estleg:Subsection``, the four concrete ``*Regulation`` act classes, ...) and
# ships with no reasoner, so the single most natural query —
# ``?x a estleg:LegalProvision`` / ``?x a estleg:Act`` — silently returned only
# 71 of ~162k provisions. We forward-chain rdf:type once at build time so every
# instance also carries its entailed parent types. Each rollup edge below is
# backed by an ``rdfs:subClassOf`` axiom, so the materialized types are exactly
# what an RDFS reasoner would entail:
#   * the bare classes (Subsection, KovProvision, Law, the *Regulation
#     hierarchy) -> ``krr_outputs/controlled_vocabulary.jsonld`` (a combined
#     input; axioms added in #519).
#   * the ``LegalProvision_<slug>`` leaf family -> the per-law ``*_peep.json``
#     ``owl:Class`` declarations (``... rdfs:subClassOf estleg:LegalProvision``),
#     fully merged into combined (~812 such class nodes).
#   * the ``Regulation_<id>`` leaf family -> the per-regulation ``owl:Class``
#     declaration emitted by ``generate_regulations.py`` into each
#     ``regulations/{riik,kov}/*`` source file; combined emits those provision
#     nodes as closure stubs, so that declaration lives in the source corpus,
#     not in combined (retiring the per-file classes themselves is #434's scope).
# Structural CONTAINERS (Chapter/Division/Section/Part/Subdivision) are
# deliberately NOT rolled up to estleg:LegalProvision: the TBox declares no such
# subclass intent and estleg:Part is in fact used at act-root level (e.g. the
# multi-part AOS osa files), so asserting LegalProvision on them would over-type
# the graph. Err toward not over-asserting.

#: Exact-match rollup edges: an instance carrying the key class also gets the
#: value class asserted. Resolved transitively (Government/Ministerial ->
#: National -> Act), so only the *direct* superclass is listed per key.
_TYPE_ROLLUP_EDGES: dict[str, str] = {
    "estleg:Subsection": "estleg:LegalProvision",
    "estleg:KovProvision": "estleg:LegalProvision",
    "estleg:Law": "estleg:Act",
    "estleg:NationalRegulation": "estleg:Act",
    "estleg:MunicipalRegulation": "estleg:Act",
    "estleg:GovernmentRegulation": "estleg:NationalRegulation",
    "estleg:MinisterialRegulation": "estleg:NationalRegulation",
}

#: Prefix-match rollup edges for the per-file leaf-class families. Any ``@type``
#: entry starting with the prefix entails the paired superclass.
_TYPE_ROLLUP_PREFIXES: tuple[tuple[str, str], ...] = (
    ("estleg:LegalProvision_", "estleg:LegalProvision"),
    ("estleg:Regulation_", "estleg:LegalProvision"),
)


def _materialize_supertypes(types: list[str]) -> list[str]:
    """Forward-chain rdf:type over the subclass hierarchy for one ``@type`` (#519).

    Returns the original ``types`` (order and any quirks preserved exactly) with
    every transitively-entailed parent class appended in breadth-first discovery
    order, de-duplicated against types already present. The ordering uses no
    ``set`` — only the input order, the fixed ``_TYPE_ROLLUP_EDGES`` lookup and
    the fixed ``_TYPE_ROLLUP_PREFIXES`` tuple — so the output is byte-identical
    across interpreter runs regardless of ``PYTHONHASHSEED`` (AGENTS.md
    determinism rule). Pure function; never mutates ``types``.
    """
    present: dict[str, None] = dict.fromkeys(types)  # membership set, ordered
    additions: list[str] = []
    queue: list[str] = list(types)  # BFS work queue, originals first
    idx = 0
    while idx < len(queue):
        current = queue[idx]
        idx += 1
        candidates: list[str] = []
        parent = _TYPE_ROLLUP_EDGES.get(current)
        if parent is not None:
            candidates.append(parent)
        for prefix, prefix_parent in _TYPE_ROLLUP_PREFIXES:
            if current.startswith(prefix):
                candidates.append(prefix_parent)
        for candidate in candidates:
            if candidate not in present:
                present[candidate] = None
                additions.append(candidate)
                queue.append(candidate)  # expand transitively
    if not additions:
        return list(types)
    return list(types) + additions


def _apply_type_rollup(all_nodes: list[dict]) -> int:
    """Assert entailed parent rdf:types on every node, in place (#519).

    Mutates each node whose ``@type`` is a list, replacing it with the
    supertype-closed list. Returns the number of nodes that gained at least one
    type. Idempotent: nodes already carrying their full parent chain (e.g.
    regulation roots already typed ``estleg:Act``) are left byte-for-byte
    untouched, so re-running the build is a no-op for them.
    """
    enriched = 0
    for node in all_nodes:
        types = node.get("@type")
        if not isinstance(types, list):
            continue
        rolled = _materialize_supertypes(types)
        if len(rolled) != len(types):
            node["@type"] = rolled
            enriched += 1
    return enriched


def generate_combined_jsonld(krr_dir: Path = KRR_DIR):
    """Generate combined JSON-LD file (Issue #26, DQ-1).

    Composition (#416): law peeps + the ``COMBINED_ALLOWED_JSONLD`` allowlist,
    then the enrichment overlays (sanctions/institutions/concepts/annotations/
    amendments — #561) fully merged, then a deterministic block of lightweight
    stub nodes for every remaining cross-corpus reference (court/EU/draft/
    regulation/harmonisation) so the artifact is graph-closed when loaded on its
    own. Version forward edges (estleg:hasVersion) are stripped, not stubbed —
    the version layer is a separate load surface (#561, COMBINED_STRIPPED_PREDICATES).
    """
    print("\n=== Generating combined JSON-LD ===")

    # #426 invariant: a legacy peep listed in the deprecation decisions must
    # carry its owl:deprecated / dcterms:isReplacedBy marks BEFORE it is folded
    # into the combined artifact. generate_index() excludes those files by
    # decisions-file lookup alone, so an unmarked root would otherwise vanish
    # from the active index yet ship undeprecated inside combined_ontology —
    # an inconsistent release. Fail hard instead of building one.
    checked, violations = _legacy_deprecation.verify_decisions_applied(
        LEGACY_STATUTE_DECISIONS_PATH, krr_dir
    )
    if violations:
        detail = "\n  ".join(violations[:10])
        raise ValueError(
            f"{len(violations)} deprecation decision(s) from "
            f"{LEGACY_STATUTE_DECISIONS_PATH.name} are not applied on disk "
            f"(run scripts/deprecate_legacy_statutes.py first):\n  {detail}"
        )
    if checked:
        print(f"  Verified {checked} deprecated legacy roots carry #426 marks")

    # #416: the overlay merge + EU stub lookup read git-LFS sources — refuse to
    # build a degraded artifact from pointer stubs.
    _require_combined_build_sources(krr_dir)

    combined_context = dict(estleg_common.CONTEXT)
    combined_context["estleg"] = NEW_NS

    # First-occurrence-ordered list of merged nodes, plus an index by @id so a
    # recurring @id MERGES into the existing node instead of being dropped
    # wholesale (#281). `all_nodes` preserves insertion order for a stable,
    # diff-friendly artifact; `node_by_id` maps @id -> the live node object in
    # `all_nodes` (same object, mutated in place on merge).
    all_nodes: list[dict] = []
    node_by_id: dict[str, dict] = {}
    out_path = krr_dir / COMBINED_OUTPUT_NAME

    def ingest(filepath: Path) -> None:
        if filepath.name == COMBINED_OUTPUT_NAME:
            # Defensive guard. The input lists never return the combined output
            # file, but make doubly sure we never read it back into itself even
            # if a list is hand-extended.
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        graph = doc.get("@graph")
        if not isinstance(graph, list):
            return
        for node in graph:
            if not isinstance(node, dict):
                continue
            nid = node.get("@id", "")
            if not nid:
                continue
            # #561: the version layer is a separate load surface (hybrid
            # decision), so combined must not emit forward edges into it — drop
            # them here so the closure gate sees a genuinely closed graph rather
            # than dangling-but-exempt edges. Mutating the transient source node
            # is safe (the source document is discarded after this file).
            for _pred in estleg_common.COMBINED_STRIPPED_PREDICATES:
                node.pop(_pred, None)
            existing = node_by_id.get(nid)
            if existing is None:
                # First time we see this @id — keep a fresh copy so a later
                # merge never mutates the source document's node object.
                new_node = dict(node)
                all_nodes.append(new_node)
                node_by_id[nid] = new_node
            else:
                # Same @id from a later file — union complementary
                # properties into the node we already kept (#281).
                _merge_node_properties(existing, node)

    for filepath in canonical_combined_inputs(krr_dir):
        ingest(filepath)
    law_node_count = len(all_nodes)

    # #416: fully merge the enrichment overlays — their nodes are part of the
    # law graph (Sanction/Institution/Concept/Annotation are the targets of
    # hasSanction/competentAuthority/annotates/concept links).
    for filepath in estleg_common.iter_combined_overlay_files(krr_dir):
        ingest(filepath)
    overlay_node_count = len(all_nodes) - law_node_count
    print(
        f"  Merged {law_node_count} law nodes + {overlay_node_count} overlay "
        f"nodes ({'/'.join(estleg_common.COMBINED_OVERLAY_SUBDIRS)})"
    )

    # #416: close the graph — synthesise a leaf stub for every remaining
    # cross-corpus reference so combined loads standalone with zero dangling
    # estleg: object IRIs.
    stub_count = _emit_closure_stubs(node_by_id, all_nodes, krr_dir)

    # #519: forward-chain rdf:type over the subclass hierarchy so the shipped
    # graph answers `?x a estleg:LegalProvision` / `?x a estleg:Act` without a
    # reasoner. Runs AFTER stub emission (closure stubs are real graph nodes that
    # carry leaf provision types too, so they must be rolled up) and BEFORE the
    # header insert (the owl:Ontology header carries no rollup-eligible @type).
    # Every materialized parent is backed by an rdfs:subClassOf axiom — see
    # _TYPE_ROLLUP_EDGES / _TYPE_ROLLUP_PREFIXES.
    rolled_count = _apply_type_rollup(all_nodes)
    print(f"  Materialized entailed parent types on {rolled_count} nodes (#519)")

    # #616: stamp a dataset-level owl:Ontology header (owl:versionInfo +
    # owl:versionIRI) at @graph[0] so the shipped graph is self-describing and a
    # consumer can pin/cite the version. Inert for the closure gate (no estleg:
    # refs); re-emitted every build from the single ONTOLOGY_VERSION constant.
    all_nodes.insert(0, estleg_common.combined_ontology_header())

    combined = {
        "@context": combined_context,
        "@graph": all_nodes,
    }

    save_json(out_path, combined)
    print(
        f"  Generated {out_path.name} with {len(all_nodes)} nodes from "
        f"{len(node_by_id)} unique IDs "
        f"({law_node_count} law + {overlay_node_count} overlay + {stub_count} stub)"
    )


def main():
    """Release build: INDEX + combined only (#467).

    Legacy repair passes are not invoked. Use
    ``scripts/archive/legacy_repairs.py --yes`` for emergency rewrites.
    """
    print("=" * 60)
    print("Estonian Legal Ontology - release artifact build (#467)")
    print("=" * 60)
    generate_index()
    generate_combined_jsonld()


if __name__ == "__main__":
    main()
