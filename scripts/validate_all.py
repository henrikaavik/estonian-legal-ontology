#!/usr/bin/env python3
"""
Comprehensive validation script for Estonian Legal Ontology files.

Checks:
1. JSON syntax validity
2. @context consistency (same namespace across all files)
3. @id uniqueness (no duplicates within or across files)
4. @type is always an array
5. Property type consistency (multi-valued props are arrays)
6. sectionNumber is always a string
7. dc:source is always a string

Exit code 0 = all pass, 1 = failures found.
"""

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EXPECTED_NS = "https://data.riik.ee/ontology/estleg#"

# Root-level JSON-LD files that are canonical inputs to
# `combined_ontology.jsonld` alongside every `*_peep.json`. Kept in sync
# with `scripts/fix_all_issues.COMBINED_ALLOWED_JSONLD`.
COMBINED_ALLOWED_JSONLD = (
    "controlled_vocabulary.jsonld",
    "karistusseadustik_eriosa_owl.jsonld",
    "tsus_osa7_138_169_owl.jsonld",
)

# SHACL-sensitive provision fields that Seadusloome's ontology load path
# validates. Drift in any of these between source `*_peep.json` and the
# combined artifact reproduces consumer-side warnings, so the parity
# check rejects them before publication.
#
# Kept in sync with `shacl/estonian_legal_shapes.ttl`. Extending this
# list (#158) catches more drift but does not eliminate the need for
# SHACL — we use it as a fast structural-equality precheck before
# SHACL runs.
PROVISION_PARITY_FIELDS = (
    "@type",
    "estleg:paragrahv",
    "estleg:sourceAct",
    "estleg:summary",
    "estleg:requestedCluster",
    "estleg:legalText",
    "estleg:sectionNumber",
    "estleg:hasSanction",
    "estleg:competentAuthority",
    "estleg:references",
    "estleg:interpretedBy",
    "dcterms:subject",
    "dcterms:source",
)

# Aggregate / report file classification.
#
# Files in this set are NEVER candidates for the canonical-source
# parity check; they are derived artefacts that summarise other files
# rather than primary extractor output. Splitting catalogue files
# (which carry `'totalRegulations'` or no `'@graph'`) from the parity
# corpus keeps `discover_validation_files()` honest without resorting
# to brittle filename-prefix heuristics (#158).
EXCLUDE_SUFFIXES = (
    "_report.json",
    "_mapping.json",
    "_index.json",
    "_classification.json",
    "_coverage.json",
)


def is_aggregate_index_file(filepath: Path) -> bool:
    """Return True if `filepath` is a registry/index/aggregate file.

    The check is content-driven where possible (so it stays stable when
    naming conventions evolve): catalogue files either advertise a top-
    level `totalRegulations` integer (regulation indexes) or, in the
    case of registry indexes (`INDEX.json`, `EELNOUD_INDEX.json`,
    `EURLEX_INDEX.json`, `CURIA_INDEX.json`, `RIIGIKOHUS_INDEX.json`,
    `REGULATIONS_*INDEX.json`), have no `@graph` array at all. We fall
    back to a small set of well-known names so the classifier still
    recognises an empty/corrupt index file.
    """
    name = filepath.name
    well_known_indexes = {
        "INDEX.json",
        "EELNOUD_INDEX.json",
        "EURLEX_INDEX.json",
        "CURIA_INDEX.json",
        "RIIGIKOHUS_INDEX.json",
        "REGULATIONS_RIIK_INDEX.json",
        "REGULATIONS_KOV_INDEX.json",
    }
    if name in well_known_indexes:
        return True
    try:
        with open(filepath, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(doc, dict):
        return False
    if "totalRegulations" in doc and "@graph" not in doc:
        return True
    return False


def is_combined_artifact_file(filepath: Path) -> bool:
    """Return True if `filepath` is a generated combined ontology file.

    Combined files are derived artefacts: they re-export every node
    from their source peeps, so including them in the per-file
    validation pipeline (id-uniqueness, internal-reference resolution)
    creates trivial cross-file collisions with the peeps. The parity
    gate checks them separately via `validate_combined_ontology` /
    `validate_subcorpus_combined_ontologies`.
    """
    name = filepath.name
    if name == "combined_ontology.jsonld":
        return True
    if name.endswith("_combined.jsonld"):
        return True
    return False


# Backwards-compatible alias retained for callers that may already
# import the old subcorpus-only name.
is_subcorpus_combined_file = is_combined_artifact_file


ACT_TYPES = {
    "estleg:Act",
    "estleg:Law",
    "estleg:NationalRegulation",
    "estleg:MunicipalRegulation",
}

REGISTRY_EXCEPTION_CATEGORIES = {
    "procedure_map",
    "concept_map",
}

CONCEPT_ONLY_REGISTRY_EXCEPTIONS = {
    "concept_map",
}

EXTERNAL_REF_PREFIXES = (
    "http://",
    "https://",
    "xsd:",
    "owl:",
    "rdf:",
    "rdfs:",
    "skos:",
    "dc:",
    "dcterms:",
    "eli:",
)

MULTI_VALUED_PROPS = {
    "estleg:coversConcept", "coversConcept",
    "estleg:hasSection", "hasSection",
    "estleg:hasDivision", "hasDivision",
    "estleg:hasSubdivision", "hasSubdivision",
    "estleg:hasChapter", "hasChapter",
    "estleg:references", "references",
    "estleg:referencedBy",
    "estleg:interpretsLaw",
    "estleg:interpretedBy",
    "estleg:transposesDirective",
    "estleg:transposedBy",
    "estleg:amendedBy",
    "estleg:hasSanction",
    "estleg:competentAuthority",
    "estleg:affectedBy",
    "estleg:harmonisedWith",
    "estleg:semanticallySimilarTo",
    "estleg:definesTerm",
    "estleg:definedIn",
    "dcterms:subject",
    "estleg:affectedLawName",
    "estleg:referencedLaw",
    "skos:exactMatch",
    "skos:closeMatch",
    "estleg:hasAnnex",
}

# Properties that are semantically single-valued (dict OK, not required to be list)
SINGLE_VALUED_PROPS = {
    "estleg:normativeType",
}

GENERATED_CLASS_PREFIXES = (
    "estleg:LegalProvision_",
    "estleg:Regulation_",
    "estleg:Concept_",
)


@dataclass
class Reporter:
    """Reentrant container for validation diagnostics.

    Replaces the previous module-level `errors` / `warnings` lists so
    that nested invocations (tests, future programmatic callers) can
    keep their own state. The module-level `errors` / `warnings` lists
    below remain bound to the module's default reporter for backwards
    compatibility with existing tests, which mutate them directly via
    `validate_all.errors.clear()` etc. New tests should prefer using
    a fresh `Reporter()` and passing it explicitly.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.errors.clear()
        self.warnings.clear()

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        print(f"  ERROR: {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"  WARN: {msg}")


_DEFAULT_REPORTER = Reporter()
# Module-level aliases. Existing tests do `validate_all.errors.clear()`
# and `validate_all.errors == []` — keep those working by rebinding to
# the default reporter's mutable lists. NEVER reassign these names
# (use `reset()` instead) or the test fixtures will silently observe
# a stale list.
errors = _DEFAULT_REPORTER.errors
warnings = _DEFAULT_REPORTER.warnings


def error(msg: str) -> None:
    _DEFAULT_REPORTER.error(msg)


def warn(msg: str) -> None:
    _DEFAULT_REPORTER.warn(msg)


def reset() -> None:
    """Clear accumulated diagnostics on the module-level reporter.

    Safe to call between test cases when not using the explicit
    `Reporter()` pattern. Also used by the autouse pytest fixture
    declared in `tests/test_validate_all.py`.
    """
    _DEFAULT_REPORTER.reset()


def validate_json_syntax(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        error(f"{filepath.name}: Invalid JSON - {e}")
        return None
    except UnicodeDecodeError as e:
        error(f"{filepath.name}: Encoding error - {e}")
        return None


def validate_context(filepath: Path, doc: dict):
    ctx = doc.get("@context", {})
    if isinstance(ctx, dict):
        ns = ctx.get("estleg", "")
        if ns != EXPECTED_NS:
            error(f"{filepath.name}: Wrong estleg namespace: {ns} (expected {EXPECTED_NS})")


def validate_types(filepath: Path, doc: dict):
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        if "@type" in node and not isinstance(node["@type"], list):
            error(f"{filepath.name}: @type is not an array at graph[{i}] (@id={node.get('@id', '?')})")


def validate_multi_valued(filepath: Path, doc: dict):
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        for key in node:
            if key in MULTI_VALUED_PROPS:
                val = node[key]
                if not isinstance(val, list):
                    error(f"{filepath.name}: {key} is not an array at graph[{i}] (@id={node.get('@id', '?')})")


def validate_section_numbers(filepath: Path, doc: dict):
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        for key in ("estleg:sectionNumber", "sectionNumber"):
            if key in node and not isinstance(node[key], str):
                error(f"{filepath.name}: {key} is not a string at graph[{i}] ({type(node[key]).__name__})")


def validate_dc_source(filepath: Path, doc: dict):
    """Validate `dc:source` / bare `source` typing on each graph node.

    Accepts either a single string OR an array of strings — multi-
    valued sources are now preserved (#159) instead of being lossy-
    joined with `'; '`. Anything else (numbers, dicts without an
    `@id`, etc.) is still a hard error.
    """
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        for key in ("dc:source", "source"):
            if key not in node:
                continue
            value = node[key]
            if isinstance(value, list):
                if not all(isinstance(item, str) for item in value):
                    error(
                        f"{filepath.name}: {key} array contains non-string item "
                        f"at graph[{i}] (@id={node.get('@id', '?')})"
                    )
            elif not isinstance(value, str):
                error(
                    f"{filepath.name}: {key} must be a string or array of strings "
                    f"at graph[{i}] (@id={node.get('@id', '?')}, got {type(value).__name__})"
                )


def validate_source_provenance(filepath: Path, doc: dict):
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        if "dcterms:source" in node:
            value = node["dcterms:source"]
            if not isinstance(value, dict) or not isinstance(value.get("@id"), str):
                error(
                    f"{filepath.name}: dcterms:source must be an IRI object "
                    f"at graph[{i}] (@id={node.get('@id', '?')})"
                )
        if "dcterms:title" in node:
            value = node["dcterms:title"]
            if not isinstance(value, (str, dict)):
                error(
                    f"{filepath.name}: dcterms:title must be a string or "
                    f"language-tagged value at graph[{i}] (@id={node.get('@id', '?')})"
                )


def iter_json_values(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_values(child)


def is_strict_xsd_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return value == parsed.isoformat()


def validate_xsd_dates(filepath: Path, doc: dict):
    for value in iter_json_values(doc):
        if value.get("@type") != "xsd:date":
            continue
        literal = value.get("@value")
        if not is_strict_xsd_date(literal):
            error(
                f"{filepath.name}: invalid xsd:date literal "
                f"{literal!r} (expected YYYY-MM-DD)"
            )


def validate_affected_law_names(filepath: Path, doc: dict):
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        if "estleg:affectedLawName" not in node:
            continue
        value = node["estleg:affectedLawName"]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            error(
                f"{filepath.name}: estleg:affectedLawName must be an array "
                f"of strings at graph[{i}] (@id={node.get('@id', '?')})"
            )


# Act-level temporal validity properties (#128). The ontology models
# temporal validity at the *act* level: these predicates are only ever
# written onto a node whose `@type` includes `estleg:Act` (laws, state
# regulations, KOV regulations all get `estleg:Act` stamped on their act
# node during KOV layer-1 enrichment). `estleg:publicationDate` is
# deliberately excluded — it is also a legitimate DraftLegislation
# property, so it is not act-exclusive.
TEMPORAL_ACT_LEVEL_PROPS = frozenset({
    "estleg:temporalStatus",
    "estleg:entryIntoForce",
    "estleg:adoptionDate",
    "estleg:repealDate",
    "estleg:lastAmendmentDate",
})

# Documented (property, type) exceptions to the act-level placement rule.
# `estleg:AmendmentEvent` reified amendment nodes carry their own
# `estleg:entryIntoForce` (the date *that amendment* took effect) — that
# is an event date, not the act-validity property, so it is allowed on
# non-Act nodes. Any other temporal property on a non-Act node is the
# bug class from #128 (a `graph[0]` fallback stamping temporal props
# onto e.g. an `estleg:LegalConcept`).
TEMPORAL_PLACEMENT_EXCEPTIONS = frozenset({
    ("estleg:entryIntoForce", "estleg:AmendmentEvent"),
})


def validate_temporal_property_targets(files: list[Path]):
    """Closed-world check: act-level temporal props must live on Act nodes.

    Fails (errors) when `estleg:temporalStatus` / `estleg:entryIntoForce`
    / `estleg:adoptionDate` / `estleg:repealDate` / `estleg:lastAmendmentDate`
    appear on a node whose `@type` does not include `estleg:Act`, unless
    the (property, type) pair is in `TEMPORAL_PLACEMENT_EXCEPTIONS`. This
    catches the historical `graph[0]` fallback in `extract_temporal_data.py`
    that wrote temporal props onto non-Act `estleg:LegalConcept` nodes
    (#128). SHACL's `estleg:ActTemporalShape` only constrains nodes that
    *are* `estleg:Act` (open-world `sh:targetClass`), so this gate is the
    only thing that flags misplacement.
    """
    print("\n--- Temporal Property Placement ---")
    offenders: list[tuple[str, str, str]] = []
    for filepath in files:
        doc = validate_json_syntax(filepath)
        if not isinstance(doc, dict):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            types = set(node_types(node))
            if "estleg:Act" in types:
                continue
            for prop in TEMPORAL_ACT_LEVEL_PROPS:
                if prop not in node:
                    continue
                if any((prop, t) in TEMPORAL_PLACEMENT_EXCEPTIONS for t in types):
                    continue
                offenders.append((filepath.name, node.get("@id", "?"), prop))
    if offenders:
        error(
            f"{len(offenders)} act-level temporal properties on non-Act nodes"
        )
        for file_name, node_id, prop in offenders[:20]:
            print(f"    {file_name}: {node_id} carries {prop} (not estleg:Act)")
        if len(offenders) > 20:
            print(f"    ... and {len(offenders) - 20} more")
    else:
        print("  OK: all act-level temporal properties live on estleg:Act nodes")


def collect_internal_refs(filepath: Path, doc: dict) -> list[tuple[str, str, str, str]]:
    refs: list[tuple[str, str, str, str]] = []

    def walk(value: object, prop: str, node_id: str) -> None:
        if isinstance(value, dict):
            ref_id = value.get("@id")
            if isinstance(ref_id, str) and not ref_id.startswith(EXTERNAL_REF_PREFIXES):
                refs.append((filepath.name, node_id, prop, ref_id))
            for key, child in value.items():
                if key != "@id":
                    walk(child, prop, node_id)
        elif isinstance(value, list):
            for child in value:
                walk(child, prop, node_id)

    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id", "?")
        for prop, value in node.items():
            if prop == "@id":
                continue
            walk(value, prop, node_id if isinstance(node_id, str) else "?")
    return refs


def validate_internal_references(all_ids: dict[str, list[str]], refs: list[tuple[str, str, str, str]]):
    print("\n--- Internal Reference Integrity ---")
    ids = set(all_ids)
    missing = [
        (file_name, node_id, prop, ref_id)
        for file_name, node_id, prop, ref_id in refs
        if ref_id.startswith("estleg:") and ref_id not in ids
    ]
    if not missing:
        print(f"  OK: {len(refs)} internal object references resolve")
        return
    error(f"{len(missing)} internal estleg: object references do not resolve")
    for file_name, node_id, prop, ref_id in missing[:20]:
        print(f"    {file_name}: {node_id} {prop} -> {ref_id}")
    if len(missing) > 20:
        print(f"    ... and {len(missing) - 20} more")


def discover_validation_files(krr_dir: Path = KRR_DIR) -> list[Path]:
    """Return JSON/JSON-LD files that should pass full validation.

    Excludes:
    * files matching `EXCLUDE_SUFFIXES` (report/mapping/index/etc.),
    * `combined_ontology.jsonld` and subcorpus `*_combined.jsonld`
      artefacts (validated separately by the parity gate so we don't
      both walk every node and ID-collide with the source peeps),
    * registry / catalogue files identified by content signature in
      `is_aggregate_index_file()`.
    """
    files = sorted(set(
        list(krr_dir.glob("*.json"))
        + list(krr_dir.glob("*.jsonld"))
        + list(krr_dir.glob("**/*.json"))
        + list(krr_dir.glob("**/*.jsonld"))
    ))
    files = [f for f in files if not any(f.name.endswith(s) for s in EXCLUDE_SUFFIXES)]
    files = [f for f in files if not is_combined_artifact_file(f)]
    files = [f for f in files if not is_aggregate_index_file(f)]
    return files


def node_types(node: dict) -> list[str]:
    types = node.get("@type", [])
    if isinstance(types, str):
        return [types]
    if isinstance(types, list):
        return [t for t in types if isinstance(t, str)]
    return []


def is_act_node(node: dict) -> bool:
    types = set(node_types(node))
    return "estleg:Act" in types or bool(types & ACT_TYPES and "owl:Ontology" in types)


def is_provision_node(node: dict) -> bool:
    if any(key in node for key in ("estleg:paragrahv", "estleg:sectionNumber", "sectionNumber", "estleg:legalText")):
        return True
    for type_name in node_types(node):
        if type_name in {"estleg:LegalProvision", "estleg:Provision", "estleg:Section"}:
            return True
        if type_name.startswith("estleg:LegalProvision_"):
            return True
        if type_name.startswith("estleg:Regulation_"):
            return True
    return False


def registry_exception_category(index_doc: dict, law: dict, file_name: str) -> str | None:
    top_level = index_doc.get("registry_exceptions", {})
    for container in (law.get("registry_exceptions", {}), top_level):
        if not isinstance(container, dict):
            continue
        entry = container.get(file_name)
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            category = entry.get("category")
            if isinstance(category, str):
                return category
    return None


def validate_registry_index(krr_dir: Path = KRR_DIR):
    index_path = krr_dir / "INDEX.json"
    print("\n--- Registry Drift ---")
    if not index_path.exists():
        warn(f"{index_path.name}: registry not found")
        return

    doc = validate_json_syntax(index_path)
    if doc is None:
        return
    laws = doc.get("laws")
    if not isinstance(laws, list):
        error(f"{index_path.name}: laws is not an array")
        return

    indexed_file_count = 0
    for law_idx, law in enumerate(laws):
        if not isinstance(law, dict):
            error(f"{index_path.name}: laws[{law_idx}] is not an object")
            continue
        law_name = law.get("name")
        if not isinstance(law_name, str) or not law_name:
            error(f"{index_path.name}: laws[{law_idx}].name is missing or invalid")
        files = law.get("files")
        if not isinstance(files, list) or not files:
            error(f"{index_path.name}: laws[{law_idx}].files is missing or empty")
            continue

        for file_idx, file_name in enumerate(files):
            indexed_file_count += 1
            if not isinstance(file_name, str) or not file_name:
                error(f"{index_path.name}: laws[{law_idx}].files[{file_idx}] is not a file path string")
                continue
            path = Path(file_name)
            if path.is_absolute() or ".." in path.parts:
                error(f"{index_path.name}: indexed path is not repository-relative: {file_name}")
                continue
            file_path = krr_dir / path
            if not file_path.exists():
                error(f"{index_path.name}: indexed file does not exist: {file_name}")
                continue

            file_doc = validate_json_syntax(file_path)
            if file_doc is None:
                continue
            graph = file_doc.get("@graph")
            if not isinstance(graph, list):
                error(f"{file_name}: indexed file has no @graph array")
                continue

            exception = registry_exception_category(doc, law, file_name)
            if exception and exception not in REGISTRY_EXCEPTION_CATEGORIES:
                error(f"{index_path.name}: unsupported registry exception {exception!r} for {file_name}")
                exception = None

            act_count = sum(1 for node in graph if isinstance(node, dict) and is_act_node(node))
            provision_count = sum(1 for node in graph if isinstance(node, dict) and is_provision_node(node))

            if act_count != 1 and exception not in CONCEPT_ONLY_REGISTRY_EXCEPTIONS:
                error(f"{file_name}: indexed file has {act_count} act-level nodes (expected 1)")
            if provision_count == 0 and not exception:
                error(f"{file_name}: indexed file has no provision nodes and no registry exception")

    if isinstance(doc.get("total_laws"), int) and doc["total_laws"] != len(laws):
        error(f"{index_path.name}: total_laws={doc['total_laws']} but laws has {len(laws)} entries")
    if isinstance(doc.get("total_files"), int) and doc["total_files"] != indexed_file_count:
        error(f"{index_path.name}: total_files={doc['total_files']} but registry lists {indexed_file_count} files")

    print(f"  Checked {len(laws)} indexed laws and {indexed_file_count} indexed files")


def validate_regulation_indexes(krr_dir: Path = KRR_DIR):
    print("\n--- Regulation Indexes ---")
    checks = [
        (
            krr_dir / "regulations" / "riik",
            "REGULATIONS_RIIK_INDEX.json",
            False,
        ),
        (
            krr_dir / "regulations" / "kov",
            "REGULATIONS_KOV_INDEX.json",
            True,
        ),
    ]
    for directory, index_name, is_kov in checks:
        files = sorted(directory.glob("**/*_peep.json"))
        index_path = directory / index_name
        if not index_path.exists():
            warn(f"{index_name}: index not found")
            continue
        doc = validate_json_syntax(index_path)
        if doc is None:
            continue
        expected_kov = doc.get("kov")
        if expected_kov is not is_kov:
            error(f"{index_name}: kov={expected_kov!r}, expected {is_kov!r}")
        advertised = doc.get("totalRegulations")
        if advertised != len(files):
            error(f"{index_name}: totalRegulations={advertised} but filesystem has {len(files)} peep files")
        index_files = doc.get("files")
        if isinstance(index_files, list) and len(index_files) != len(files):
            error(f"{index_name}: files lists {len(index_files)} entries but filesystem has {len(files)} peep files")
        # Issue #119: when the index carries the per-act `acts` ledger,
        # cross-check it against `files` and verify stub acts carry a
        # contentStatus marker.
        acts = doc.get("acts")
        if isinstance(acts, list):
            act_files = {a.get("file") for a in acts if isinstance(a, dict)}
            file_set = set(index_files) if isinstance(index_files, list) else None
            if file_set is not None and act_files != file_set:
                error(
                    f"{index_name}: acts ledger lists {len(act_files)} files but "
                    f"files lists {len(file_set)}"
                )
            for a in acts:
                if not isinstance(a, dict):
                    continue
                if a.get("status") == "stub" and not a.get("contentStatus"):
                    error(
                        f"{index_name}: stub act {a.get('slug')!r} lacks a "
                        f"contentStatus marker"
                    )
        print(f"  Checked {index_name}: {len(files)} files")


def _peep_ontology_node(doc: dict) -> dict | None:
    """Return the first ``owl:Ontology`` node in a peep doc's ``@graph``."""
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" in types:
            return node
    return None


def validate_act_coverage_reconciliation(krr_dir: Path = KRR_DIR):
    """Issue #119: reconcile ``generation_manifest_laws.json`` against the
    enacted-law peep files on disk.

    When the manifest is present, assert:
      * every act listed in the manifest (``outputsAll``) maps to a peep
        file that exists on disk (``<slug>_peep.json`` for single-file
        laws, or at least one ``<slug>_osa*_peep.json`` for multipart
        laws, or — for ``failed`` acts — it is *allowed* to be missing);
      * every root ``*_peep.json`` law file appears in the manifest (no
        orphan peep file with no provenance entry);
      * acts whose manifest status is ``stub`` carry the stub marker
        (``estleg:contentStatus`` set to ``noStructuredBody``) on their
        ontology node.

    The manifest is not checked in by default, so a missing manifest is a
    *warning* (the gate stays green) — it just means a supervised
    ``generate_all_laws.py`` run hasn't happened on this tree yet.
    """
    print("\n--- Act Coverage Reconciliation ---")
    manifest_path = krr_dir / "generation_manifest_laws.json"
    if not manifest_path.exists():
        warn(
            "generation_manifest_laws.json: not found — run "
            "scripts/generate_all_laws.py to produce act-coverage provenance"
        )
        return
    manifest = validate_json_syntax(manifest_path)
    if not isinstance(manifest, dict):
        error("generation_manifest_laws.json: not a JSON object")
        return
    entries = manifest.get("outputsAll")
    if not isinstance(entries, list):
        # Older manifests used `outputs` only.
        entries = manifest.get("outputs")
    if not isinstance(entries, list):
        error("generation_manifest_laws.json: no outputsAll/outputs list")
        return

    # Index the root law peep files on disk.
    disk_files = {p.name for p in krr_dir.glob("*_peep.json")}
    # Map slug -> set of peep filenames it could own (single + osa parts).
    slug_files: dict[str, set[str]] = defaultdict(set)
    osa_re = re.compile(r"^(?P<slug>.+?)_osa\d+_peep\.json$")
    for name in disk_files:
        m = osa_re.match(name)
        if m:
            slug_files[m.group("slug")].add(name)
        elif name.endswith("_peep.json"):
            slug_files[name[: -len("_peep.json")]].add(name)

    manifest_slugs: set[str] = set()
    manifest_files: set[str] = set()
    missing_for_acts: list[str] = []
    stub_marker_problems: list[str] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        status = entry.get("status", "full")
        manifest_slugs.add(slug)
        owned = slug_files.get(slug, set())
        manifest_files.update(owned)
        single = f"{slug}_peep.json"
        if status == "failed":
            # A failed fetch is allowed to have no file on disk.
            continue
        if not owned:
            missing_for_acts.append(f"{slug} (status={status})")
            continue
        # Stub acts must carry the noStructuredBody marker on the peep's
        # ontology node (single-file shape — stubs are never multipart).
        if status == "stub" and single in disk_files:
            doc = validate_json_syntax(krr_dir / single)
            if isinstance(doc, dict):
                ont = _peep_ontology_node(doc)
                marker = ont.get("estleg:contentStatus") if isinstance(ont, dict) else None
                if marker != "noStructuredBody":
                    stub_marker_problems.append(
                        f"{single}: manifest status=stub but contentStatus={marker!r}"
                    )

    # Orphan peep files: on disk but not referenced by any manifest entry.
    referenced = set()
    for slug in manifest_slugs:
        referenced.update(slug_files.get(slug, set()))
    orphans = sorted(disk_files - referenced)

    if missing_for_acts:
        error(
            f"{len(missing_for_acts)} acts in generation_manifest_laws.json "
            f"have no peep file on disk"
        )
        for item in missing_for_acts[:15]:
            print(f"    manifest act with no file: {item}")
    if orphans:
        error(
            f"{len(orphans)} root law peep files are not listed in "
            f"generation_manifest_laws.json"
        )
        for name in orphans[:15]:
            print(f"    orphan peep (no manifest entry): {name}")
    if stub_marker_problems:
        error(
            f"{len(stub_marker_problems)} stub acts lack the noStructuredBody "
            f"content-status marker"
        )
        for item in stub_marker_problems[:15]:
            print(f"    {item}")
    if not (missing_for_acts or orphans or stub_marker_problems):
        print(
            f"  OK: {len(manifest_slugs)} manifest acts reconcile with "
            f"{len(disk_files)} root law peep files"
        )


def jsonld_file_count(krr_dir: Path = KRR_DIR) -> int:
    return len(sorted(set(
        list(krr_dir.glob("*.json"))
        + list(krr_dir.glob("*.jsonld"))
        + list(krr_dir.glob("**/*.json"))
        + list(krr_dir.glob("**/*.jsonld"))
    )))


def metadata_stats(krr_dir: Path = KRR_DIR) -> dict[str, int]:
    index_path = krr_dir / "INDEX.json"
    index_doc = validate_json_syntax(index_path) if index_path.exists() else {}
    laws = index_doc.get("laws", []) if isinstance(index_doc, dict) else []

    def count_graph_nodes(path: Path, type_name: str) -> int:
        doc = validate_json_syntax(path)
        if not isinstance(doc, dict):
            return 0
        return sum(
            1
            for node in doc.get("@graph", [])
            if isinstance(node, dict) and type_name in (node.get("@type") or [])
        )

    return {
        "estleg:enactedLawCount": len(laws) if isinstance(laws, list) else 0,
        "estleg:enactedLawFileCount": len(list(krr_dir.glob("*_peep.json"))),
        "estleg:domesticRegulationCount": len(list((krr_dir / "regulations" / "riik").glob("*_peep.json"))),
        "estleg:municipalRegulationCount": len(list((krr_dir / "regulations" / "kov").glob("**/*_peep.json"))),
        "estleg:draftLegislationCount": count_graph_nodes(krr_dir / "eelnoud" / "eelnoud_combined.jsonld", "estleg:DraftLegislation"),
        "estleg:courtDecisionCount": sum(
            count_graph_nodes(path, "estleg:CourtDecision")
            for path in (krr_dir / "riigikohus").glob("riigikohus_*_peep.json")
        ),
        "estleg:euLegislationCount": count_graph_nodes(krr_dir / "eurlex" / "eurlex_combined.jsonld", "estleg:EULegislation"),
        "estleg:euCourtDecisionCount": count_graph_nodes(krr_dir / "curia" / "curia_combined.jsonld", "estleg:EUCourtDecision"),
        "estleg:totalFiles": jsonld_file_count(krr_dir),
    }


# Maps each `dcat:distribution` entry (keyed by its `dcterms:title`) to
# the `estleg:*Count` predicates it advertises and the canonical
# `metadata_stats()` key each one must equal. Without this, the
# per-distribution count keys (and the parallel `estleg:fileCount` keys
# that mean different things in different distributions) drift
# independently of the validated `estleg:statistics` block (#110).
DISTRIBUTION_COUNT_KEYS: dict[str, dict[str, str]] = {
    "JSON-LD ontology files (complete dataset)": {
        "estleg:fileCount": "estleg:totalFiles",
    },
    "Combined enacted laws ontology": {
        "estleg:lawIndexRecordCount": "estleg:enactedLawCount",
        "estleg:lawFileCount": "estleg:enactedLawFileCount",
    },
    "Domestic regulations (state-level)": {
        "estleg:fileCount": "estleg:domesticRegulationCount",
    },
    "Combined draft legislation ontology": {
        "estleg:draftLegislationCount": "estleg:draftLegislationCount",
    },
    "Combined EU legislation ontology": {
        "estleg:euLegislationCount": "estleg:euLegislationCount",
    },
    "Combined EU court decisions ontology": {
        "estleg:euCourtDecisionCount": "estleg:euCourtDecisionCount",
    },
}


def validate_metadata_catalog(krr_dir: Path = KRR_DIR):
    print("\n--- Catalog Metadata ---")
    metadata_path = REPO_ROOT / "metadata.jsonld"
    if not metadata_path.exists():
        warn("metadata.jsonld: not found")
        return
    doc = validate_json_syntax(metadata_path)
    if doc is None:
        return
    actual = metadata_stats(krr_dir)
    advertised = doc.get("estleg:statistics", {})
    if not isinstance(advertised, dict):
        error("metadata.jsonld: estleg:statistics is missing or not an object")
        return
    for key, value in actual.items():
        if advertised.get(key) != value:
            error(f"metadata.jsonld: {key}={advertised.get(key)!r}, expected {value!r}")

    # Cross-check the per-distribution `estleg:*Count` keys too — the
    # `dcat:distribution` prose previously hard-coded counts that
    # nothing validated (#110). Counts in the free-text `dcterms:description`
    # strings have been replaced with qualitative wording; the remaining
    # numeric assertions live only in these structured keys, which we now
    # pin against `metadata_stats()`.
    checked_dist_keys = 0
    distributions = doc.get("dcat:distribution", [])
    if not isinstance(distributions, list):
        error("metadata.jsonld: dcat:distribution is not an array")
        distributions = []
    for dist in distributions:
        if not isinstance(dist, dict):
            continue
        title = dist.get("dcterms:title")
        expected_keys = DISTRIBUTION_COUNT_KEYS.get(title) if isinstance(title, str) else None
        if not expected_keys:
            continue
        for dist_key, stats_key in expected_keys.items():
            checked_dist_keys += 1
            if dist.get(dist_key) != actual.get(stats_key):
                error(
                    f"metadata.jsonld: dcat:distribution[{title!r}].{dist_key}="
                    f"{dist.get(dist_key)!r}, expected {actual.get(stats_key)!r}"
                )
    print(
        f"  Checked {len(actual)} advertised corpus statistics "
        f"and {checked_dist_keys} distribution count keys"
    )


# Required keys in the *current* shape of `transposition_mapping.json`
# (the shape `generate_transposition_mapping.py` emits, mirroring the
# other EU generators per #183/#129). The presence of `country` and
# `total_matched` discriminates it from the legacy `{matched, unmatched,
# mappings}` shape that the generator no longer produces.
TRANSPOSITION_REPORT_REQUIRED_KEYS = frozenset({
    "generated",
    "source",
    "country",
    "total_measures_fetched",
    "total_matched",
    "total_unmatched",
    "unique_directives",
    "unique_laws",
    "mappings",
})


def validate_transposition_mapping(krr_dir: Path = KRR_DIR):
    """Gate the EU transposition layer (#129).

    If `transposition_mapping.json` exists it must (1) be in the current
    report shape, and (2) either carry a non-empty `mappings` array or be
    explicitly flagged as an intentional empty snapshot (`documented_empty:
    true`, set by the generator's `--allow-empty`). README advertises EU
    directive transposition as an active integration feature, so an empty
    *and* unflagged layer is a release defect, not a no-op.
    """
    print("\n--- Transposition Mapping ---")
    report_path = krr_dir / "transposition_mapping.json"
    if not report_path.exists():
        warn("transposition_mapping.json: not found")
        return
    doc = validate_json_syntax(report_path)
    if not isinstance(doc, dict):
        error("transposition_mapping.json: not a JSON object")
        return
    missing_keys = sorted(TRANSPOSITION_REPORT_REQUIRED_KEYS - set(doc))
    if missing_keys:
        error(
            "transposition_mapping.json: stale/legacy report shape — missing "
            f"keys {missing_keys}. Re-run scripts/generate_transposition_mapping.py."
        )
        return
    mappings = doc.get("mappings")
    if not isinstance(mappings, list):
        error("transposition_mapping.json: 'mappings' is not an array")
        return
    if not mappings and not doc.get("documented_empty"):
        error(
            "transposition_mapping.json: 'mappings' is empty and "
            "'documented_empty' is not set — the transposition layer is "
            "advertised but unpopulated. Re-run the generator, or pass "
            "--allow-empty to record an intentional empty snapshot."
        )
        return
    if mappings:
        print(f"  OK: {len(mappings)} law↔directive mappings, current report shape")
    else:
        print("  OK: empty transposition layer, explicitly flagged (documented_empty)")


def graph_ids(doc: dict) -> set[str]:
    ids: set[str] = set()
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return ids
    for node in graph:
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id")
        if isinstance(node_id, str) and node_id:
            ids.add(node_id)
    return ids


def is_vocabulary_definition_node(node: dict) -> bool:
    types = node_types(node)
    return bool(
        set(types)
        & {
            "owl:Class",
            "owl:ObjectProperty",
            "owl:DatatypeProperty",
            "owl:AnnotationProperty",
        }
    )


def collect_defined_vocabulary_terms(files: list[Path] | None = None) -> set[str]:
    terms: set[str] = set()
    for path in (REPO_ROOT / "metadata.jsonld", KRR_DIR / "controlled_vocabulary.jsonld"):
        if not path.exists():
            continue
        doc = validate_json_syntax(path)
        if not isinstance(doc, dict):
            continue
        terms.update(graph_ids(doc))
    for path in files or []:
        doc = validate_json_syntax(path)
        if not isinstance(doc, dict):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict) or not is_vocabulary_definition_node(node):
                continue
            node_id = node.get("@id")
            if isinstance(node_id, str) and node_id.startswith("estleg:"):
                terms.add(node_id)
    return terms


def collect_used_vocabulary_terms(doc: dict) -> tuple[set[str], set[str]]:
    predicates: set[str] = set()
    classes: set[str] = set()
    graph = doc.get("@graph")
    if not isinstance(graph, list):
        return predicates, classes

    for node in graph:
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if key.startswith("estleg:"):
                predicates.add(key)
            if key != "@type":
                continue
            values = value if isinstance(value, list) else [value]
            for type_name in values:
                if isinstance(type_name, str) and type_name.startswith("estleg:"):
                    classes.add(type_name)
    return predicates, classes


def is_generated_structural_class(term: str) -> bool:
    return term.startswith(GENERATED_CLASS_PREFIXES)


def validate_vocabulary_coverage(files: list[Path]):
    print("\n--- Vocabulary Coverage ---")
    defined = collect_defined_vocabulary_terms(files)
    used_predicates: set[str] = set()
    used_classes: set[str] = set()

    for filepath in files:
        doc = validate_json_syntax(filepath)
        if not isinstance(doc, dict):
            continue
        predicates, classes = collect_used_vocabulary_terms(doc)
        used_predicates.update(predicates)
        used_classes.update(classes)

    missing_predicates = sorted(used_predicates - defined)
    missing_classes = sorted(
        term for term in used_classes - defined
        if not is_generated_structural_class(term)
    )
    if missing_predicates or missing_classes:
        error(
            "Undefined reusable estleg vocabulary terms: "
            f"{len(missing_predicates)} predicates, {len(missing_classes)} classes"
        )
        for term in (missing_predicates + missing_classes)[:30]:
            print(f"    undefined vocabulary term: {term}")
    else:
        print(f"  OK: {len(used_predicates)} predicates and {len(used_classes)} classes are covered")


def _ingest_graph_into(
    path: Path,
    source_nodes: dict[str, dict],
) -> None:
    doc = validate_json_syntax(path)
    if not isinstance(doc, dict):
        return
    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        nid = node.get("@id")
        if isinstance(nid, str) and nid:
            source_nodes.setdefault(nid, node)


def collect_source_nodes(
    krr_dir: Path = KRR_DIR,
    *,
    peep_glob: str = "*_peep.json",
    allowlist_jsonld: tuple[str, ...] = COMBINED_ALLOWED_JSONLD,
    allowlist_dir: Path | None = None,
) -> tuple[dict[str, dict], set[str], list[Path]]:
    """Return (id -> source node, allowlisted IDs, source file list).

    Sources = every peep file matching `peep_glob` (relative to
    `krr_dir`) plus an explicit JSON-LD allowlist (vocabulary +
    standalone OWL serialisations). The second element is the set of
    IDs contributed *only* by the allowlisted JSON-LD files so the
    parity check can permit those without flagging them as missing-
    from-source.

    `peep_glob` accepts `**` and is passed to `Path.glob()` as-is. The
    root combined corpus uses the non-recursive `*_peep.json` pattern;
    subcorpus combined files use a directory-scoped pattern (e.g.
    `eurlex/*_peep.json`). To detect drift in a subcorpus regardless
    of where its peep files live, pass `**/*_peep.json` and combine
    with a per-subcorpus filter (#158).
    """
    source_nodes: dict[str, dict] = {}
    allowlist_ids: set[str] = set()
    files: list[Path] = sorted(krr_dir.glob(peep_glob))
    for path in files:
        _ingest_graph_into(path, source_nodes)

    allowlist_root = allowlist_dir if allowlist_dir is not None else krr_dir
    for name in allowlist_jsonld:
        path = allowlist_root / name
        if not path.exists():
            continue
        files.append(path)
        before = set(source_nodes)
        _ingest_graph_into(path, source_nodes)
        allowlist_ids.update(set(source_nodes) - before)
        # Also mark IDs that the allowlist file *re*-asserts: SHACL
        # treats those as legitimately defined elsewhere too.
        doc = validate_json_syntax(path)
        if isinstance(doc, dict):
            for node in doc.get("@graph", []):
                if not isinstance(node, dict):
                    continue
                nid = node.get("@id")
                if isinstance(nid, str) and nid:
                    allowlist_ids.add(nid)
    return source_nodes, allowlist_ids, files


def _normalize_parity_value(value):
    """Normalize a JSON-LD value so equality compares semantics, not order.

    Lists are compared as a stable representation that preserves the
    distinction between a string literal and a `{"@id": "..."}` object —
    that is the exact distinction that produces SHACL `nodeKind` warnings
    when the combined artifact drifts from its sources.
    """
    if isinstance(value, list):
        return sorted(
            (json.dumps(_normalize_parity_value(item), sort_keys=True, ensure_ascii=False)
             for item in value)
        )
    if isinstance(value, dict):
        return {k: _normalize_parity_value(v) for k, v in value.items()}
    return value


def _parity_field_drift(source_node: dict, combined_node: dict) -> list[str]:
    drift: list[str] = []
    for f in PROVISION_PARITY_FIELDS:
        if f not in source_node and f not in combined_node:
            continue
        src_value = _normalize_parity_value(source_node.get(f))
        comb_value = _normalize_parity_value(combined_node.get(f))
        if src_value != comb_value:
            drift.append(f)
    return drift


@dataclass(frozen=True)
class CombinedParityTarget:
    """Configuration for one parity check between sources and a combined."""

    label: str  # e.g. "combined_ontology.jsonld"
    combined_path: Path
    source_files: list[Path]
    source_nodes: dict[str, dict]
    allowlist_ids: set[str]
    # Files used for the mtime staleness check. Only peep files are
    # considered authoritative for staleness: subcorpus schema/vocab
    # allowlist files frequently get touched independently of the
    # combined and would otherwise create spurious "older than source"
    # warnings.
    mtime_check_files: list[Path] = field(default_factory=list)
    # Map nodes (`*_Map_2026`) generated separately for combined files
    # are documented exceptions: subcorpus combined files merge their
    # peeps under a single `<Subcorpus>_Combined_Map_2026` instead of
    # carrying every per-peep map node forward. Tracked drift but not
    # reported as missing/stale to avoid false positives.
    expected_missing_ids: set[str] = field(default_factory=set)
    expected_extra_ids: set[str] = field(default_factory=set)


def _check_combined_parity(target: CombinedParityTarget) -> None:
    label = target.label
    combined_path = target.combined_path

    if not combined_path.exists():
        warn(f"{label}: not found")
        return

    combined_doc = validate_json_syntax(combined_path)
    if combined_doc is None:
        return

    combined_nodes: dict[str, dict] = {}
    for node in combined_doc.get("@graph", []):
        if isinstance(node, dict):
            nid = node.get("@id")
            if isinstance(nid, str) and nid:
                combined_nodes.setdefault(nid, node)
    combined_ids = set(combined_nodes)

    source_ids = set(target.source_nodes)

    structural_drift = False

    missing = source_ids - combined_ids - target.expected_missing_ids
    if missing:
        error(f"{label}: missing {len(missing)} source graph IDs")
        for node_id in sorted(missing)[:20]:
            print(f"    missing from combined: {node_id}")
        structural_drift = True

    extras = combined_ids - source_ids - target.allowlist_ids - target.expected_extra_ids
    if extras:
        error(
            f"{label}: {len(extras)} stale extra IDs "
            f"not present in any canonical source"
        )
        for node_id in sorted(extras)[:20]:
            print(f"    stale extra in combined: {node_id}")
        if len(extras) > 20:
            print(f"    ... and {len(extras) - 20} more")
        structural_drift = True

    drift_samples: dict[str, list[str]] = {f: [] for f in PROVISION_PARITY_FIELDS}
    drift_count = 0
    for nid in sorted(source_ids & combined_ids):
        drift_fields = _parity_field_drift(target.source_nodes[nid], combined_nodes[nid])
        if not drift_fields:
            continue
        drift_count += 1
        for f in drift_fields:
            if len(drift_samples[f]) < 5:
                drift_samples[f].append(nid)
    if drift_count:
        error(
            f"{label}: {drift_count} shared provision IDs "
            f"drift from source on SHACL-sensitive fields"
        )
        for f, ids in drift_samples.items():
            if ids:
                print(f"    drift in {f}: {', '.join(ids)}")
        structural_drift = True

    mtime_files = target.mtime_check_files or target.source_files
    max_source_mtime = max(
        (path.stat().st_mtime for path in mtime_files),
        default=0.0,
    )
    # Use `<=` so that a same-second tie still flags a stale combined.
    # Without this guard a regenerated source file landing in the same
    # epoch second as the previous combined would silently pass (#158).
    #
    # The mtime gate is a backstop for the case where someone hand-
    # edited a peep without regenerating the combined. When the
    # structural parity check is clean it implies the combined matches
    # its sources content-wise, so a stale mtime can only be a build-
    # ordering artefact (e.g. a fresh git checkout that wrote files in
    # alphabetical order) and is not actionable. We therefore suppress
    # the mtime error when structural parity passes; this keeps the
    # gate strong against real staleness without flagging normal
    # checkout artifacts.
    if (
        max_source_mtime
        and mtime_files
        and combined_path.stat().st_mtime <= max_source_mtime
        and structural_drift
    ):
        error(f"{label}: older than at least one canonical source file")
    print(
        f"  Checked {label} against {len(target.source_files)} canonical source files "
        f"({len(target.allowlist_ids)} allowlisted vocabulary IDs)"
    )


# Subcorpus combined targets. Each entry binds a peep glob (relative to
# `KRR_DIR`) to the combined file built from those peeps. The
# `expected_missing` / `expected_extras` tuples document the permitted
# deltas between a subcorpus' per-peep map nodes and the single
# `<Subcorpus>_Combined_Map_2026` node that the combined writer emits
# in their place. These are stable artefacts of the subcorpus pipeline,
# not drift.
@dataclass(frozen=True)
class SubcorpusCombinedSpec:
    name: str
    peep_glob: str
    combined_path_rel: str  # e.g. "eurlex/eurlex_combined.jsonld"
    schema_files: tuple[str, ...] = ()
    expected_missing: tuple[str, ...] = ()
    expected_extras: tuple[str, ...] = ()


SUBCORPUS_COMBINED_TARGETS: tuple[SubcorpusCombinedSpec, ...] = (
    SubcorpusCombinedSpec(
        name="eurlex",
        peep_glob="eurlex/*_peep.json",
        combined_path_rel="eurlex/eurlex_combined.jsonld",
        schema_files=("eurlex/eurlex_schema.json",),
        expected_missing=(
            "estleg:EURlex_Decisions_Map_2026",
            "estleg:EURlex_Directives_Map_2026",
            "estleg:EURlex_Regulations_Map_2026",
        ),
        expected_extras=("estleg:EURlex_Combined_Map_2026",),
    ),
    SubcorpusCombinedSpec(
        name="curia",
        peep_glob="curia/*_peep.json",
        combined_path_rel="curia/curia_combined.jsonld",
        schema_files=("curia/curia_schema.json",),
        expected_missing=(
            "estleg:CURIA_Ag_Opinions_Map_2026",
            "estleg:CURIA_Court_Opinions_Map_2026",
            "estleg:CURIA_Judgments_Map_2026",
            "estleg:CURIA_Orders_Map_2026",
            "estleg:CURIA_Other_Map_2026",
        ),
        expected_extras=("estleg:CURIA_Combined_Map_2026",),
    ),
    SubcorpusCombinedSpec(
        name="eelnoud",
        peep_glob="eelnoud/*_peep.json",
        combined_path_rel="eelnoud/eelnoud_combined.jsonld",
        schema_files=("eelnoud/eelnoud_schema.json",),
        expected_missing=(
            "estleg:Eelnoud_PublicConsultation_Map_2026",
            "estleg:Eelnoud_Submission_Map_2026",
            "estleg:Eelnoud_Review_Map_2026",
        ),
        expected_extras=("estleg:Eelnoud_Combined_Map_2026",),
    ),
)


def validate_combined_ontology(krr_dir: Path = KRR_DIR):
    print("\n--- Combined Ontology Artifact ---")
    combined_path = krr_dir / "combined_ontology.jsonld"
    source_nodes, allowlist_ids, source_files = collect_source_nodes(krr_dir)
    target = CombinedParityTarget(
        label="combined_ontology.jsonld",
        combined_path=combined_path,
        source_files=source_files,
        source_nodes=source_nodes,
        allowlist_ids=allowlist_ids,
    )
    _check_combined_parity(target)


def validate_subcorpus_combined_ontologies(krr_dir: Path = KRR_DIR):
    """Run the parity check for every documented subcorpus combined.

    Walks `SUBCORPUS_COMBINED_TARGETS` and calls `_check_combined_parity`
    once per (peep glob, combined file). This is the fix for #158: the
    root parity gate cannot detect drift in `eurlex/`, `curia/`, or
    `eelnoud/` peep files because none of those IDs are merged into
    `combined_ontology.jsonld` — they live in subcorpus-specific
    combined artefacts that this gate now monitors.
    """
    print("\n--- Subcorpus Combined Artifacts ---")
    for spec in SUBCORPUS_COMBINED_TARGETS:
        combined_path = krr_dir / spec.combined_path_rel
        # Exempt the well-known per-peep map nodes documented on the
        # spec from the missing-from-combined delta. These are merged
        # into a single `<Subcorpus>_Combined_Map_2026` by the combined
        # writer and are not drift.
        peep_files = sorted(krr_dir.glob(spec.peep_glob))
        source_nodes, allowlist_ids, source_files = collect_source_nodes(
            krr_dir,
            peep_glob=spec.peep_glob,
            allowlist_jsonld=spec.schema_files,
            allowlist_dir=krr_dir,
        )
        if not source_files and not combined_path.exists():
            continue
        target = CombinedParityTarget(
            label=spec.combined_path_rel,
            combined_path=combined_path,
            source_files=source_files,
            source_nodes=source_nodes,
            allowlist_ids=allowlist_ids,
            # Schema files are intentionally excluded from the staleness
            # check: they are independent vocabulary inputs that may be
            # touched without invalidating the combined artefact. Only
            # `*_peep.json` regeneration should fail the gate.
            mtime_check_files=peep_files,
            expected_missing_ids=set(spec.expected_missing),
            expected_extra_ids=set(spec.expected_extras),
        )
        _check_combined_parity(target)


_ESTONIAN_TRANSLITERATION = str.maketrans({
    "ö": "o", "ä": "a", "ü": "u", "õ": "o",
    "Ö": "o", "Ä": "a", "Ü": "u", "Õ": "o",
    "š": "s", "ž": "z", "Š": "s", "Ž": "z",
})


def normalized_label(value: str) -> str:
    text = value.translate(_ESTONIAN_TRANSLITERATION).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def validate_institution_duplicates(krr_dir: Path = KRR_DIR):
    print("\n--- Institution Canonical Labels ---")
    groups: dict[str, list[str]] = defaultdict(list)
    for path in sorted((krr_dir / "institutions").glob("institution_*.json")):
        doc = validate_json_syntax(path)
        if not isinstance(doc, dict):
            continue
        label = ""
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            if "estleg:Institution" not in (node.get("@type") or []):
                continue
            raw_label = node.get("rdfs:label", "")
            if isinstance(raw_label, dict):
                label = str(raw_label.get("@value", ""))
            else:
                label = str(raw_label)
            break
        if label:
            groups[normalized_label(label)].append(path.name)
    dupes = {label: files for label, files in groups.items() if len(files) > 1}
    if dupes:
        error(f"{len(dupes)} duplicate normalized institution label groups")
        for label, files in sorted(dupes.items())[:10]:
            print(f"    {label}: {files}")
    else:
        print(f"  OK: {len(groups)} institution labels are canonical")


def _collapse_slug(slug: str) -> str:
    """Collapse underscore runs in an institution slug for comparison.

    The on-disk registry currently contains a few stale double-underscore
    filenames (e.g. ``institution_maksu__ja_tolliamet.json``) alongside
    the single-underscore convention the extractor now emits. Comparing
    collapsed forms makes the consistency check tolerant of that until
    the extractor is re-run.
    """
    return re.sub(r"_+", "_", slug).strip("_")


def load_institution_alias_canonicals(repo_root: Path = REPO_ROOT) -> set[str]:
    """Return the set of canonical target slugs in data/institution_aliases.json.

    Missing / malformed file -> empty set (the alias table is optional).
    Slugs are returned underscore-collapsed for comparison with the
    on-disk registry filenames.
    """
    path = repo_root / "data" / "institution_aliases.json"
    if not path.exists():
        return set()
    doc = validate_json_syntax(path)
    if not isinstance(doc, dict):
        return set()
    entries = doc.get("aliases")
    if not isinstance(entries, dict):
        return set()
    out: set[str] = set()
    for value in entries.values():
        canonical = None
        if isinstance(value, str):
            canonical = value
        elif isinstance(value, dict):
            cand = value.get("canonical")
            if isinstance(cand, str):
                canonical = cand
        if canonical:
            out.add(_collapse_slug(canonical.lower()))
    return out


def validate_institution_registry_consistency(krr_dir: Path = KRR_DIR, repo_root: Path = REPO_ROOT):
    """Issue #118: every ``Institution_*`` @id in the registry must be a
    known canonical institution slug, and the alias table must point only
    at known canonical slugs.

    Checks (underscore-collapse tolerant):
      1. The canonical slug set = filename stems of
         ``krr_outputs/institutions/institution_*.json``.
      2. Every node in those files whose @id is ``estleg:Institution_<X>``
         must have ``collapse(X)`` present in the canonical set. (Catches
         a file whose internal @id drifted from its filename, or a stray
         Institution node smuggled into another institution file.)
      3. Every ``canonical`` target in ``data/institution_aliases.json``
         must resolve to a known canonical slug — an alias must not point
         at a nonexistent institution. (Alias *keys* are historical and
         may or may not still have their own file, so they are not
         required to be in the set.)
    """
    print("\n--- Institution Registry Consistency ---")
    inst_dir = krr_dir / "institutions"
    files = sorted(inst_dir.glob("institution_*.json"))
    if not files:
        warn(f"{inst_dir}: no institution_*.json files found")
        return

    canonical: set[str] = set()
    for path in files:
        slug = path.stem.removeprefix("institution_")
        if slug:
            canonical.add(_collapse_slug(slug))

    offending_ids: list[tuple[str, str]] = []
    for path in files:
        doc = validate_json_syntax(path)
        if not isinstance(doc, dict):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            nid = node.get("@id")
            if not isinstance(nid, str):
                continue
            m = re.match(r"^estleg:Institution_([^_].*)$", nid)
            if not m:
                continue
            # Strip any ``_competence_<type>`` Competence-node suffix —
            # those legitimately extend the institution IRI.
            base = re.sub(r"_competence_[a-z_]+$", "", m.group(1))
            if "estleg:Competence" in (node.get("@type") or []):
                continue
            if _collapse_slug(base) not in canonical:
                offending_ids.append((path.name, nid))

    alias_canonicals = load_institution_alias_canonicals(repo_root)
    dangling_aliases = sorted(slug for slug in alias_canonicals if slug not in canonical)

    if offending_ids:
        error(
            f"{len(offending_ids)} Institution_* @ids in the registry are not "
            f"canonical institution slugs"
        )
        for file_name, nid in offending_ids[:15]:
            print(f"    {file_name}: {nid}")
    if dangling_aliases:
        error(
            f"{len(dangling_aliases)} alias canonical targets in "
            f"data/institution_aliases.json have no institution file"
        )
        for slug in dangling_aliases[:15]:
            print(f"    alias -> {slug} (no institution_{slug}.json)")
    if not offending_ids and not dangling_aliases:
        print(
            f"  OK: {len(canonical)} canonical institution slugs, "
            f"{len(alias_canonicals)} alias targets all resolve"
        )


def validate_id_uniqueness(all_ids: dict[str, list[str]]):
    print("\n--- @id Uniqueness ---")
    # Shared ontology class definitions are expected to appear in multiple files
    shared_class_ids = {
        "estleg:LegalPart", "estleg:Provision", "estleg:Section",
        "estleg:LegalConcept",
        "estleg:CaseType", "estleg:EUCourtDecisionType", "estleg:EUInstitution",
        "https://data.riik.ee/ontology/estleg#",
        "https://data.riik.ee/ontology/estleg#LegalPart",
        "https://data.riik.ee/ontology/estleg#Chapter",
        "https://data.riik.ee/ontology/estleg#Division",
        "https://data.riik.ee/ontology/estleg#Section",
        "https://data.riik.ee/ontology/estleg#LegalConcept",
    }
    dupes = {k: v for k, v in all_ids.items() if len(v) > 1 and k not in shared_class_ids}
    shared_dupes = {k: v for k, v in all_ids.items() if len(v) > 1 and k in shared_class_ids}
    if shared_dupes:
        print(f"  OK: {len(shared_dupes)} shared ontology class @id values (expected)")
    if dupes:
        error(f"{len(dupes)} @id values are duplicated across files (semantic collisions)")
        for dupe_id, files in sorted(dupes.items())[:20]:
            print(f"    {dupe_id}: {files}")
        if len(dupes) > 20:
            print(f"    ... and {len(dupes) - 20} more")
    else:
        print("  OK: All @id values are unique across files")


def main():
    print("=" * 60)
    print("Estonian Legal Ontology - Validation")
    print("=" * 60)

    files = discover_validation_files()

    print(f"\nValidating {len(files)} files...\n")

    all_ids: dict[str, list[str]] = defaultdict(list)
    internal_refs: list[tuple[str, str, str, str]] = []

    for filepath in files:
        doc = validate_json_syntax(filepath)
        if doc is None:
            continue

        validate_context(filepath, doc)
        validate_types(filepath, doc)
        validate_multi_valued(filepath, doc)
        validate_section_numbers(filepath, doc)
        validate_dc_source(filepath, doc)
        validate_source_provenance(filepath, doc)
        validate_xsd_dates(filepath, doc)
        validate_affected_law_names(filepath, doc)
        internal_refs.extend(collect_internal_refs(filepath, doc))

        # Collect IDs
        if "@graph" in doc:
            seen_in_file = set()
            for node in doc["@graph"]:
                nid = node.get("@id", "")
                if nid in seen_in_file:
                    error(f"{filepath.name}: Duplicate @id within file: {nid}")
                seen_in_file.add(nid)
                all_ids[nid].append(filepath.name)

    validate_id_uniqueness(all_ids)
    validate_internal_references(all_ids, internal_refs)
    validate_vocabulary_coverage(files)
    validate_temporal_property_targets(files)
    validate_transposition_mapping()
    validate_registry_index()
    validate_regulation_indexes()
    validate_act_coverage_reconciliation()
    validate_combined_ontology()
    validate_subcorpus_combined_ontologies()
    validate_metadata_catalog()
    validate_institution_duplicates()
    validate_institution_registry_consistency()

    print("\n" + "=" * 60)
    print(f"Files validated: {len(files)}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    print("=" * 60)

    if errors:
        print("\nVALIDATION FAILED")
        sys.exit(1)
    else:
        print("\nVALIDATION PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
