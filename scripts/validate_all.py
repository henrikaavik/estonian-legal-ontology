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
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EXPECTED_NS = "https://data.riik.ee/ontology/estleg#"

EXCLUDE_PREFIXES = (
    "INDEX",
    "combined_",
    "EELNOUD_INDEX",
    "eelnoud_combined",
    "RIIGIKOHUS_INDEX",
    "EURLEX_INDEX",
    "eurlex_combined",
    "CURIA_INDEX",
    "curia_combined",
    "REGULATIONS_",
)
EXCLUDE_SUFFIXES = (
    "_report.json",
    "_mapping.json",
    "_index.json",
    "_classification.json",
    "_coverage.json",
)

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

errors = []
warnings = []


def error(msg: str):
    errors.append(msg)
    print(f"  ERROR: {msg}")


def warn(msg: str):
    warnings.append(msg)
    print(f"  WARN: {msg}")


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
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        for key in ("dc:source", "source"):
            if key in node and isinstance(node[key], list):
                error(f"{filepath.name}: {key} is an array at graph[{i}] (expected string)")


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
    files = sorted(
        list(krr_dir.glob("*.json"))
        + list(krr_dir.glob("*.jsonld"))
        + list(krr_dir.glob("**/*.json"))
        + list(krr_dir.glob("**/*.jsonld"))
    )
    files = sorted(set(files))
    files = [f for f in files if not any(f.name.startswith(p) for p in EXCLUDE_PREFIXES)]
    files = [f for f in files if not any(f.name.endswith(s) for s in EXCLUDE_SUFFIXES)]
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
        print(f"  Checked {index_name}: {len(files)} files")


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
    print(f"  Checked {len(actual)} advertised corpus statistics")


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


def validate_combined_ontology(krr_dir: Path = KRR_DIR):
    print("\n--- Combined Ontology Artifact ---")
    combined_path = krr_dir / "combined_ontology.jsonld"
    if not combined_path.exists():
        warn("combined_ontology.jsonld: not found")
        return

    combined_doc = validate_json_syntax(combined_path)
    if combined_doc is None:
        return
    combined_ids = graph_ids(combined_doc)

    source_files = sorted(krr_dir.glob("*_peep.json"))
    source_ids: set[str] = set()
    max_source_mtime = 0.0
    for path in source_files:
        max_source_mtime = max(max_source_mtime, path.stat().st_mtime)
        doc = validate_json_syntax(path)
        if isinstance(doc, dict):
            source_ids.update(graph_ids(doc))

    missing = source_ids - combined_ids
    if missing:
        error(f"combined_ontology.jsonld: missing {len(missing)} source graph IDs")
        for node_id in sorted(missing)[:20]:
            print(f"    missing from combined: {node_id}")
    if max_source_mtime and combined_path.stat().st_mtime < max_source_mtime:
        error("combined_ontology.jsonld: older than at least one source *_peep.json file")
    print(f"  Checked combined_ontology.jsonld against {len(source_files)} root source files")


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
    validate_registry_index()
    validate_regulation_indexes()
    validate_combined_ontology()
    validate_metadata_catalog()
    validate_institution_duplicates()

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
