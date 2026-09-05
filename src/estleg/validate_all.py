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

# Make sibling scripts importable regardless of cwd so `estleg_common`
# (the single source of truth for operational-state-file exclusion)
# resolves whether this module is run as a script or imported by tests.
from estleg import estleg_common
from estleg.deprecate_legacy_statutes import verify_decisions_applied
from estleg.estleg_common import (
    ISIKUKOOD_RE,
    act_deprecation,
    act_root_node,
    find_personal_codes,
    is_domain_individual,
    is_non_data_file,
    is_valid_isikukood,
    is_xml_sameas_iri,
    iter_krr_jsonld_files,
    jsonld_id_values,
    jsonld_texts,
)
from estleg.generate_amendment_history import collect_versions_by_date
from estleg.generate_provision_versions import riik_version_coverage

# #519: the combined builder forward-chains rdf:type over the subclass
# hierarchy, so a provision's combined @type is its source @type plus the
# entailed supertypes. The parity check below reuses the builder's own
# entailment rule (single source of truth) to tell that intentional rollup
# apart from genuine type drift.
from estleg.fix_all_issues import _materialize_supertypes
from estleg.index_body_coverage import (
    EMPTY_SUBSTANTIVE_BASELINE,
    classify_index_entry,
)
from estleg.multipart_coverage import (
    iter_multipart_index_entries,
    unmarked_multipart_gaps,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
LEGACY_STATUTE_DECISIONS_PATH = REPO_ROOT / "data" / "legacy_statute_decisions.json"
EXPECTED_NS = "https://w3id.org/estleg/"

# Root-level JSON-LD files that are canonical inputs to
# `combined_ontology.jsonld` alongside every `*_peep.json`. Kept in sync
# with `scripts/fix_all_issues.COMBINED_ALLOWED_JSONLD`.
COMBINED_ALLOWED_JSONLD = (
    "controlled_vocabulary.jsonld",
    "unresolved_references.jsonld",  # #433 ABox placeholders moved out of the CV
    "karistusseadustik_eriosa_owl.jsonld",
    "tsus_osa7_138_169_owl.jsonld",
    "act_expressions_combined.jsonld",  # #608 FRBR Expression layer
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
# Kept as a module-level name so existing tests of ``EXCLUDE_SUFFIXES``
# still import something. Classification now lives in
# ``estleg_common.is_non_data_file`` (#452); these filename suffixes
# remain the validate_all surface (reports/mappings/indexes/etc.).
EXCLUDE_SUFFIXES = (
    "_report.json",
    "_mapping.json",
    "_index.json",
    "_classification.json",
    "_coverage.json",
    "_probe.json",
    "_review.json",
)

# Re-export alias of the single source of truth (#240). Kept as a
# module-level name so `tests/test_validate_all.py` and external importers
# of `validate_all.OPERATIONAL_STATE_FILES` keep working. Do NOT replace
# this with a literal copy — the literal would drift from
# `fix_all_issues` and reintroduce the PR #232 count bug.
OPERATIONAL_STATE_FILES = estleg_common.OPERATIONAL_STATE_FILES


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
    "estleg:similarAct",
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


def _is_lfs_pointer(filepath: Path) -> bool:
    """True when ``filepath`` is an un-materialised Git-LFS pointer.

    The pytest CI job runs ``lfs: false``, so the 6 LFS-tracked artifacts
    (combined_ontology.jsonld, eurlex/curia *_combined.jsonld, …) are present
    only as their ~130-byte pointer stub. Reading one as JSON yields a spurious
    "Invalid JSON" error and a 0 node count, so corpus-count gates must detect
    and skip them rather than fail. Mirrors the workflow's own pointer guard.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.readline().rstrip("\n") == "version https://git-lfs.github.com/spec/v1"
    except OSError:
        return False


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


def _estleg_namespace_from_context_dict(ctx: dict) -> str | None:
    """Return the ``estleg`` prefix IRI declared in a context dict, or None.

    Handles both the plain-string form (``"estleg": "https://…#"``) and the
    expanded term-definition form (``"estleg": {"@id": "https://…#"}``). Returns
    ``None`` when the dict does not define an ``estleg`` term at all.
    """
    if "estleg" not in ctx:
        return None
    value = ctx["estleg"]
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        nested = value.get("@id")
        if isinstance(nested, str):
            return nested
    return ""


def validate_context(filepath: Path, doc: dict):
    """Validate the ``estleg`` namespace binding regardless of @context shape.

    JSON-LD permits ``@context`` to be a dict, a remote IRI string, or a list
    mixing remote IRIs and inline dicts. The dict case is checked directly; for
    the list case every dict member is scanned for an ``estleg`` term; a bare
    remote-string context cannot be inspected offline, so it is surfaced as a
    warning rather than silently passing (issue #286).
    """
    ctx = doc.get("@context", {})

    def _check_dict(ctx_dict: dict) -> bool:
        """Validate one context dict; return True if it declared ``estleg``."""
        ns = _estleg_namespace_from_context_dict(ctx_dict)
        if ns is None:
            return False
        if ns != EXPECTED_NS:
            error(f"{filepath.name}: Wrong estleg namespace: {ns} (expected {EXPECTED_NS})")
        return True

    if isinstance(ctx, dict):
        _check_dict(ctx)
        return

    if isinstance(ctx, list):
        for member in ctx:
            if isinstance(member, dict):
                _check_dict(member)
            elif isinstance(member, str):
                warn(
                    f"{filepath.name}: remote @context reference {member!r} not "
                    f"fetched; estleg namespace not verified"
                )
        return

    if isinstance(ctx, str):
        warn(
            f"{filepath.name}: remote @context reference {ctx!r} not fetched; "
            f"estleg namespace not verified"
        )


def validate_bare_namespace_act_ids(filepath: Path, doc: dict):
    """Reject act/law nodes that reuse the ontology namespace IRI as @id."""
    if "@graph" not in doc:
        return
    for i, node in enumerate(doc["@graph"]):
        if not isinstance(node, dict) or node.get("@id") != EXPECTED_NS:
            continue
        types = set(node_types(node))
        if not (types & ACT_TYPES):
            continue
        error(
            f"{filepath.name}: act/law node uses bare estleg namespace @id "
            f"at graph[{i}] (derive a compact act IRI instead)"
        )


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


def validate_act_xml_sameas(filepath: Path, doc: dict):
    """Reject ``owl:sameAs`` to a ``*.xml`` file on act/regulation individuals (#447).

    ``dcterms:source`` may still cite the Riigi Teataja XML. EU CELLAR
    and Wikidata sameAs are not ``*.xml`` and are left alone.
    """
    if "@graph" not in doc:
        return
    for node in doc["@graph"]:
        if not isinstance(node, dict) or not is_domain_individual(node):
            continue
        for iri in jsonld_id_values(node.get("owl:sameAs")):
            if is_xml_sameas_iri(iri):
                error(
                    f"{filepath.name}: owl:sameAs must not target a .xml "
                    f"manifestation on act {node.get('@id', '?')} ({iri})"
                )


def validate_per_document_classes(filepath: Path, doc: dict):
    """Reject mechanically minted per-file provision classes (#434)."""
    if "@graph" not in doc:
        return
    from estleg.remint_per_document_classes import (
        CANONICAL_PROVISION_TYPE,
        is_per_doc_class_id,
        is_per_doc_class_node,
        node_types,
    )

    for node in doc["@graph"]:
        if not isinstance(node, dict):
            continue
        if is_per_doc_class_node(node):
            error(
                f"{filepath.name}: per-document class {node.get('@id')} "
                "must not be minted (#434)"
            )
            continue
        types = node_types(node)
        if any(is_per_doc_class_id(item) for item in types):
            error(
                f"{filepath.name}: {node.get('@id')} still typed with a "
                "per-document class (#434)"
            )
        if "estleg:paragrahv" in node and CANONICAL_PROVISION_TYPE not in types:
            error(
                f"{filepath.name}: provision {node.get('@id')} lacks "
                f"{CANONICAL_PROVISION_TYPE} (#434)"
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


# #683: the only two published literals that carry verbatim scraped court
# prose. Structured fields (case number, ECLI, rikObjectId) are court-assigned
# document identifiers, not personal data, so they are deliberately excluded.
PERSONAL_DATA_TEXT_PROPS: tuple[str, ...] = (
    "estleg:summary",
    "estleg:legalText",
)


def validate_no_personal_codes(files: list[Path]):
    """#683: no Estonian personal ID code may survive in published court text.

    Riigikohus decisions are scraped verbatim from rikos.rik.ee, and the
    court's anonymisation initials party *names* while sometimes leaving an
    ``isikukood`` in the reasoning. A personal identification code is a direct
    identifier under the GDPR. Generation now screens both literals at their
    write sites and `estleg.screen_court_personal_data` backfilled the
    committed corpus, so a surviving code means one of those two paths
    regressed — this gate is the release-blocking backstop.

    Detection calls `estleg_common.find_personal_codes`, the same routine the
    screener rewrites from, so the gate and the screener cannot disagree about
    what counts as a code — including the carve-out for runs introduced by
    `registrikood` / `otsuse nr`, which state the number is not a person. Only
    the masked form is reported: a validator failure must not itself print the
    code.

    Cost control: re-parsing the ~200 MB riigikohus corpus purely for this
    gate is avoidable. `json.dump` never escapes ASCII digits, so a code
    inside a literal is always present verbatim in the file bytes — a raw-text
    scan that finds no checksum-valid run proves the parsed file has none
    either, and the file is skipped without being deserialised. The raw scan
    is deliberately looser than `find_personal_codes` (it ignores labels, which
    JSON escaping can reorder around a line break), so it stays a superset.
    """
    print("\n--- Personal Identification Codes (#683) ---")
    offenders: list[tuple[str, str, str, str]] = []
    for filepath in files:
        try:
            raw = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # validate_json_syntax already reports unreadable / mis-encoded
            # files; do not double-report them here.
            continue
        if not any(
            is_valid_isikukood(match.group(0)) for match in ISIKUKOOD_RE.finditer(raw)
        ):
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue  # already reported by the syntax gate
        if not isinstance(doc, dict):
            continue
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            if "estleg:CourtDecision" not in node_types(node):
                continue
            node_id = node.get("@id", "?")
            for prop in PERSONAL_DATA_TEXT_PROPS:
                for text in jsonld_texts(node.get(prop)):
                    for finding in find_personal_codes(text):
                        offenders.append(
                            (filepath.name, node_id, prop, finding["masked"])
                        )

    if offenders:
        error(
            f"{len(offenders)} Estonian personal ID codes in published court text "
            f"— run scripts/screen_court_personal_data.py (#683)"
        )
        for file_name, node_id, prop, masked in offenders[:20]:
            print(f"    {file_name}: {node_id} {prop} carries {masked}")
        if len(offenders) > 20:
            print(f"    ... and {len(offenders) - 20} more")
    else:
        print("  OK: no personal identification codes in estleg:summary / estleg:legalText")


def collect_internal_refs(filepath: Path, doc: dict) -> list[tuple[str, str, str, str]]:
    """Collect internal object refs using the shared JSON-LD walker (#453)."""
    refs: list[tuple[str, str, str, str]] = []

    for node in doc.get("@graph", []):
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id", "?")
        nid = node_id if isinstance(node_id, str) else "?"
        for prop, value in node.items():
            if prop in {"@id", "@type", "@context"}:
                continue
            for pred, ref_id in estleg_common.walk_object_refs(value, prop):
                if isinstance(ref_id, str) and not ref_id.startswith(
                    EXTERNAL_REF_PREFIXES
                ):
                    refs.append((filepath.name, nid, pred, ref_id))
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
    # Route through the shared enumerator so operational-state-file
    # exclusion can never drift between counters (#240).
    files = list(iter_krr_jsonld_files(krr_dir))
    files = [f for f in files if not is_non_data_file(f)]
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


def validate_registry_index(krr_dir: Path = KRR_DIR, *, allow_missing_index: bool = False):
    index_path = krr_dir / "INDEX.json"
    print("\n--- Registry Drift ---")
    if not index_path.exists():
        # Issue #315: INDEX.json is a committed artifact. Its absence on a
        # normal checkout means the entire file-existence / zero-provision
        # gate and the registry count checks are silently skipped, so a
        # scratch tree would print VALIDATION PASSED. Treat a missing
        # registry as a hard error unless the caller explicitly opts in to
        # a not-yet-generated state via `--allow-missing-index`.
        if allow_missing_index:
            warn(f"{index_path.name}: registry not found (allowed via --allow-missing-index)")
        else:
            error(
                f"{index_path.name}: registry not found — the committed INDEX.json "
                f"is required. Pass --allow-missing-index to permit a not-yet-"
                f"generated tree."
            )
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
            if provision_count == 0 and not exception and not _is_no_structured_body_doc(file_doc):
                error(f"{file_name}: indexed file has no provision nodes and no registry exception")

    if isinstance(doc.get("total_laws"), int) and doc["total_laws"] != len(laws):
        error(f"{index_path.name}: total_laws={doc['total_laws']} but laws has {len(laws)} entries")
    if isinstance(doc.get("total_files"), int) and doc["total_files"] != indexed_file_count:
        error(f"{index_path.name}: total_files={doc['total_files']} but registry lists {indexed_file_count} files")

    print(f"  Checked {len(laws)} indexed laws and {indexed_file_count} indexed files")


def validate_multipart_coverage(krr_dir: Path = KRR_DIR, *, allow_missing_index: bool = False):
    """Issue #556: every min–max osa hole on a multipart INDEX entry must be marked."""
    print("\n--- Multipart coverage ---")
    index_path = krr_dir / "INDEX.json"
    if not index_path.exists():
        if allow_missing_index:
            warn(f"{index_path.name}: registry not found (allowed via --allow-missing-index)")
        else:
            error(
                f"{index_path.name}: registry not found — the committed INDEX.json "
                f"is required. Pass --allow-missing-index to permit a not-yet-"
                f"generated tree."
            )
        return

    doc = validate_json_syntax(index_path)
    if doc is None:
        return

    entries = iter_multipart_index_entries(doc)
    unmarked = 0
    for entry in entries:
        gaps = unmarked_multipart_gaps(entry)
        if not gaps:
            continue
        unmarked += 1
        name = entry.get("name") or "<unnamed>"
        error(
            f"{index_path.name}: multipart law {name!r} has unmarked osa gaps "
            f"{gaps}; set multipart_gaps or intentionally_skipped_osa"
        )
    if unmarked == 0:
        print(f"  Checked {len(entries)} multipart INDEX entries; no unmarked osa gaps")


def validate_index_body_coverage(krr_dir: Path = KRR_DIR, *, allow_missing_index: bool = False):
    """Issue #507: INDEX laws must not regress on empty / summaryOnly bodies."""
    print("\n--- INDEX body coverage ---")
    index_path = krr_dir / "INDEX.json"
    if not index_path.exists():
        if allow_missing_index:
            warn(f"{index_path.name}: registry not found (allowed via --allow-missing-index)")
        else:
            error(
                f"{index_path.name}: registry not found — the committed INDEX.json "
                f"is required. Pass --allow-missing-index to permit a not-yet-"
                f"generated tree."
            )
        return

    doc = validate_json_syntax(index_path)
    if doc is None:
        return
    laws = doc.get("laws")
    if not isinstance(laws, list):
        error(f"{index_path.name}: laws is not an array")
        return

    empty_count = 0
    summary_only = 0
    treaty_count = 0
    unmarked_empty = 0
    for law in laws:
        if not isinstance(law, dict):
            continue
        coverage = classify_index_entry(law, krr_dir)
        name = law.get("name") or "<unnamed>"
        provision_count = coverage["provisionCount"]
        legal_text_count = coverage["legalTextCount"]
        kind = coverage.get("stubKind")
        if provision_count > 0 and legal_text_count == 0:
            summary_only += 1
            error(
                f"{index_path.name}: law {name!r} has provisionCount={provision_count} "
                f"but legalTextCount=0 (summaryOnly is not allowed after backfill)"
            )
        if kind == "empty":
            empty_count += 1
            if law.get("stubKind") != "empty":
                unmarked_empty += 1
                error(f"{index_path.name}: unmarked empty non-treaty law {name!r}")
        elif kind == "treaty":
            treaty_count += 1

    if empty_count > EMPTY_SUBSTANTIVE_BASELINE:
        error(
            f"{index_path.name}: empty non-treaty count {empty_count} exceeds "
            f"EMPTY_SUBSTANTIVE_BASELINE={EMPTY_SUBSTANTIVE_BASELINE}"
        )
    if summary_only == 0 and unmarked_empty == 0 and empty_count <= EMPTY_SUBSTANTIVE_BASELINE:
        print(
            f"  Checked {len(laws)} INDEX laws; {treaty_count} treaty stubs, "
            f"{empty_count} empty (baseline {EMPTY_SUBSTANTIVE_BASELINE})"
        )


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
        # Issue #314: assert every listed regulation file actually exists,
        # analogous to the per-entry existence check `validate_registry_index`
        # applies to the laws INDEX. A count-only check masks a rename that
        # leaves the tally intact but points at a now-missing path. Index
        # `files` entries are stored relative to the index directory.
        if isinstance(index_files, list):
            for file_idx, file_name in enumerate(index_files):
                if not isinstance(file_name, str) or not file_name:
                    error(f"{index_name}: files[{file_idx}] is not a file path string")
                    continue
                rel = Path(file_name)
                if rel.is_absolute() or ".." in rel.parts:
                    error(f"{index_name}: indexed path is not directory-relative: {file_name}")
                    continue
                if not (directory / rel).exists():
                    error(f"{index_name}: indexed file does not exist: {file_name}")
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
    """Return the act-root node in a peep doc's ``@graph``."""
    return act_root_node(doc)


def _is_no_structured_body_doc(doc: dict) -> bool:
    ont = _peep_ontology_node(doc)
    if not isinstance(ont, dict):
        return False
    return ont.get("estleg:contentStatus") == "noStructuredBody"


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

    run_info = manifest.get("run")
    if not isinstance(run_info, dict):
        warn(
            "generation_manifest_laws.json: malformed run block "
            f"(type={type(run_info).__name__}), ignoring sourceRemovedFromSnapshot"
        )
        run_info = {}
    source_removed = run_info.get("sourceRemovedFromSnapshot", [])
    if not isinstance(source_removed, list):
        warn(
            "generation_manifest_laws.json: malformed "
            "run.sourceRemovedFromSnapshot, ignoring"
        )
        source_removed = []
    if any(not isinstance(name, str) for name in source_removed):
        warn(
            "generation_manifest_laws.json: ignoring non-string "
            "run.sourceRemovedFromSnapshot entries"
        )
    source_removed_files = {
        name for name in source_removed if isinstance(name, str) and name.endswith("_peep.json")
    }

    # Orphan peep files: on disk but not referenced by any manifest entry
    # and not explicitly reported as source-removed by the supervised run.
    referenced = set()
    for slug in manifest_slugs:
        referenced.update(slug_files.get(slug, set()))
    orphans = sorted(disk_files - referenced - source_removed_files)

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
    # Route through the shared enumerator so this count and
    # `discover_validation_files` apply the exact same operational-state
    # exclusion (#240 anti-drift mechanism).
    return sum(1 for _ in iter_krr_jsonld_files(krr_dir))


def metadata_stats(krr_dir: Path = KRR_DIR) -> dict[str, int | None]:
    index_path = krr_dir / "INDEX.json"
    index_doc = validate_json_syntax(index_path) if index_path.exists() else {}
    laws = index_doc.get("laws", []) if isinstance(index_doc, dict) else []

    def count_graph_nodes(path: Path, type_name: str) -> int | None:
        # An un-materialised LFS pointer (lfs:false CI) is not corruption — return
        # None so corpus-count gates skip it instead of counting 0 and erroring.
        if _is_lfs_pointer(path):
            return None
        doc = validate_json_syntax(path)
        if not isinstance(doc, dict):
            return 0
        return sum(
            1
            for node in doc.get("@graph", [])
            if isinstance(node, dict) and type_name in (node.get("@type") or [])
        )

    # `courtDecisionCount` aggregates many riigikohus peep files, unlike the
    # 3 sibling counts that each read a single combined file and return None
    # (via count_graph_nodes) when that file is an un-materialised LFS pointer.
    # Mirror that: when EVERY contributing peep is a pointer (count -> None),
    # report None too — an all-unverifiable corpus must stay distinguishable
    # from a genuinely-empty one, not collapse to 0 (#605). A truly absent
    # directory (no peeps at all) still reports 0, as before.
    court_counts = [
        count_graph_nodes(path, "estleg:CourtDecision")
        for path in sorted((krr_dir / "riigikohus").glob("riigikohus_*_peep.json"))
    ]
    court_verifiable = [c for c in court_counts if c is not None]
    court_decision_count = (
        None if court_counts and not court_verifiable else sum(court_verifiable)
    )

    return {
        "estleg:enactedLawCount": len(laws) if isinstance(laws, list) else 0,
        "estleg:enactedLawFileCount": len(list(krr_dir.glob("*_peep.json"))),
        "estleg:domesticRegulationCount": len(list((krr_dir / "regulations" / "riik").glob("*_peep.json"))),
        "estleg:municipalRegulationCount": len(list((krr_dir / "regulations" / "kov").glob("**/*_peep.json"))),
        "estleg:draftLegislationCount": count_graph_nodes(krr_dir / "eelnoud" / "eelnoud_combined.jsonld", "estleg:DraftLegislation"),
        "estleg:courtDecisionCount": court_decision_count,
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
    "Combined Supreme Court decisions ontology (Riigikohus)": {
        "estleg:courtDecisionCount": "estleg:courtDecisionCount",
    },
    "Municipal regulations ontology (KOV)": {
        "estleg:municipalRegulationCount": "estleg:municipalRegulationCount",
    },
}


def validate_metadata_catalog(krr_dir: Path = KRR_DIR, *, allow_missing_index: bool = False):
    print("\n--- Catalog Metadata ---")
    metadata_path = REPO_ROOT / "metadata.jsonld"
    if not metadata_path.exists():
        # Issue #315: metadata.jsonld is a committed artifact. When it is
        # absent the advertised-corpus-statistics block and all distribution
        # count cross-checks are skipped; a fresh checkout missing the file
        # would otherwise pass green. Hard error unless explicitly allowed.
        if allow_missing_index:
            warn("metadata.jsonld: not found (allowed via --allow-missing-index)")
        else:
            error(
                "metadata.jsonld: not found — the committed catalog metadata is "
                "required. Pass --allow-missing-index to permit a not-yet-"
                "generated tree."
            )
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
        if value is None:
            # Count source is an un-materialised LFS pointer (lfs:false CI) —
            # can't verify this advertised statistic; skip rather than error.
            continue
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
            if actual.get(stats_key) is None:
                # LFS-pointer count source (lfs:false CI) — skip, don't error.
                continue
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
    validate_metadata_repro_pins(doc)


def validate_metadata_repro_pins(doc: dict) -> None:
    """#548: catalog GitHub URLs must not pin the moving ``main`` branch.

    Immutable GitHub Release assets are the other #473/#548 pin. This
    gate asserts the committed catalog cites a content SHA
    (``DATASET_CONTENT_SHA``) or a tagged ``/releases/download/v<ver>/``
    URL, and that ``dcterms:modified`` / ``owl:versionInfo`` match the
    build manifest when that file is present.
    """
    from estleg.write_build_manifest import (
        DATASET_CONTENT_SHA,
        is_mutable_main_url,
        is_release_asset_url,
    )

    urls: list[str] = []

    def _walk(value: object) -> None:
        if isinstance(value, dict):
            ident = value.get("@id")
            if isinstance(ident, str):
                urls.append(ident)
            for item in value.values():
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(doc.get("dcat:distribution"))
    _walk(doc.get("schema:distribution"))
    mutable = [url for url in urls if is_mutable_main_url(url)]
    if mutable:
        error(
            "metadata.jsonld: catalog GitHub URL still pins mutable /main "
            f"({mutable[0]})"
        )
    github_urls = [
        url
        for url in urls
        if "github.com/henrikaavik/estonian-legal-ontology" in url
    ]
    unpinned = [
        url
        for url in github_urls
        if DATASET_CONTENT_SHA not in url and not is_release_asset_url(url)
    ]
    if unpinned:
        error(
            "metadata.jsonld: catalog GitHub URL is not pinned to "
            f"DATASET_CONTENT_SHA or /releases/download/v{estleg_common.ONTOLOGY_VERSION}/ "
            f"({unpinned[0]})"
        )
    if github_urls:
        print(
            f"  Catalog GitHub URLs pinned to {DATASET_CONTENT_SHA[:12]} "
            "or a tagged release asset"
        )

    manifest_path = REPO_ROOT / "krr_outputs" / "dataset_build_manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        error("dataset_build_manifest.json: unreadable")
        return
    if not isinstance(manifest, dict):
        error("dataset_build_manifest.json: not an object")
        return
    version = doc.get("owl:versionInfo")
    if version is None:
        return
    if version != manifest.get("datasetVersion"):
        error(
            f"metadata.jsonld: owl:versionInfo={version!r} != "
            f"manifest datasetVersion={manifest.get('datasetVersion')!r}"
        )
    modified = doc.get("dcterms:modified")
    if isinstance(modified, dict):
        modified = modified.get("@value")
    if manifest.get("catalogModified") and modified != manifest.get("catalogModified"):
        error(
            f"metadata.jsonld: dcterms:modified={modified!r} != "
            f"manifest catalogModified={manifest.get('catalogModified')!r}"
        )
    if manifest.get("contentSha") and manifest.get("contentSha") != DATASET_CONTENT_SHA:
        error(
            "dataset_build_manifest.json: contentSha="
            f"{manifest.get('contentSha')!r} != DATASET_CONTENT_SHA"
        )


# Issue #313: each subcorpus INDEX ledger advertises a scalar count
# (`total_decisions` / `total_drafts` / `total_acts`) that, until now, was
# excluded from content validation (the INDEX files only appear in the
# `well_known_indexes` skip set) and never cross-checked against the
# corpus. A stale ledger can therefore drift independently of the nodes
# it summarises. Each entry binds an INDEX file (relative to `KRR_DIR`)
# to the count field it carries and the `metadata_stats()` key that
# counts the matching nodes directly.
#
# `metadata_stats()` already derives the node counts:
#   * courtDecisionCount   <- estleg:CourtDecision   (riigikohus_*_peep)
#   * draftLegislationCount <- estleg:DraftLegislation (eelnoud combined)
#   * euLegislationCount    <- estleg:EULegislation    (eurlex combined)
#   * euCourtDecisionCount  <- estleg:EUCourtDecision  (curia combined)
SUBCORPUS_INDEX_COUNT_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("riigikohus/RIIGIKOHUS_INDEX.json", "total_decisions", "estleg:courtDecisionCount"),
    ("eelnoud/EELNOUD_INDEX.json", "total_drafts", "estleg:draftLegislationCount"),
    ("eurlex/EURLEX_INDEX.json", "total_acts", "estleg:euLegislationCount"),
    ("curia/CURIA_INDEX.json", "total_decisions", "estleg:euCourtDecisionCount"),
)


def validate_subcorpus_index_counts(krr_dir: Path = KRR_DIR, *, allow_missing_index: bool = False):
    """Cross-check each subcorpus INDEX count field against node counts (#313).

    `RIIGIKOHUS/EELNOUD/EURLEX/CURIA_INDEX.json` advertise a `total_*`
    field that downstream consumers trust for corpus sizing. This gate
    asserts each one equals the corresponding `metadata_stats()` node
    count so the ledger cannot drift away from the corpus unnoticed.

    A missing INDEX file is a hard error (committed artifact) unless
    `allow_missing_index` is set, mirroring the registry/metadata gates
    (#315). A present INDEX with a missing/non-int count field is always
    an error — the field is mandatory for a populated subcorpus.
    """
    print("\n--- Subcorpus Index Counts ---")
    actual = metadata_stats(krr_dir)
    checked = 0
    for rel_path, count_field, stats_key in SUBCORPUS_INDEX_COUNT_CHECKS:
        index_path = krr_dir / rel_path
        if not index_path.exists():
            if allow_missing_index:
                warn(f"{rel_path}: index not found (allowed via --allow-missing-index)")
            else:
                error(
                    f"{rel_path}: subcorpus index not found — the committed INDEX "
                    f"is required. Pass --allow-missing-index to permit a not-yet-"
                    f"generated tree."
                )
            continue
        doc = validate_json_syntax(index_path)
        if not isinstance(doc, dict):
            if doc is not None:
                error(f"{rel_path}: not a JSON object")
            continue
        advertised = doc.get(count_field)
        if not isinstance(advertised, int) or isinstance(advertised, bool):
            error(f"{rel_path}: {count_field} is missing or not an integer")
            continue
        expected = actual.get(stats_key)
        if expected is None:
            # The node-count source (an LFS *_combined.jsonld) is an
            # un-materialised pointer (lfs:false CI) — can't cross-check.
            warn(f"{rel_path}: {stats_key} source is an LFS pointer "
                 f"(not materialised); skipping index cross-check")
            continue
        checked += 1
        if advertised != expected:
            error(
                f"{rel_path}: {count_field}={advertised} but corpus has "
                f"{expected} matching nodes ({stats_key})"
            )
    print(f"  Checked {checked} subcorpus index count fields against corpus node counts")


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
    report_path = krr_dir / "reports" / "transposition_mapping.json"
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
        # #631 review: the header counts must agree with the body — a manual
        # surgery on `mappings` that forgot to recompute them otherwise ships
        # silently (total_matched read 300 while the body had 288).
        declared = doc.get("total_matched")
        if isinstance(declared, int) and declared != len(mappings):
            error(
                f"transposition_mapping.json: total_matched={declared} but "
                f"'mappings' has {len(mappings)} entries — stale header after a "
                "manual edit. Re-run scripts/generate_transposition_mapping.py."
            )
            return
        # #631 review: matched_law_name must be the INDEX law name, not a peep
        # FILENAME stem — a retarget that used Path(file).stem leaves a trailing
        # '_peep' that matches no indexed law.
        peep_named = [m.get("matched_law_name") for m in mappings
                      if isinstance(m.get("matched_law_name"), str)
                      and m["matched_law_name"].endswith("_peep")]
        if peep_named:
            error(
                f"transposition_mapping.json: {len(peep_named)} matched_law_name(s) end "
                f"in '_peep' — a peep filename stem, not an INDEX law name "
                f"(e.g. {peep_named[0]})."
            )
            return
        print(f"  OK: {len(mappings)} law↔directive mappings, total_matched consistent")
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


def validate_canonical_tbox(krr_dir: Path = KRR_DIR):
    """#433: the CV is the default-graph T-Box; metadata is DCAT-only."""
    print("\n--- Canonical T-Box (#433) ---")
    from estleg.consolidate_tbox import (
        JUNK_TERMS,
        METADATA_PATH,
        PLACEHOLDER_NEEDLE,
        VOCABULARY_IRI,
        graph_nodes,
        index_by_id,
        is_unresolved_individual,
        load_jsonld,
        placeholder_ids,
        property_coverage,
        schema_ids_missing_from_vocab,
    )

    vocab_path = krr_dir / "controlled_vocabulary.jsonld"
    if not vocab_path.is_file():
        error("controlled_vocabulary.jsonld not found")
        return
    vocab = load_jsonld(vocab_path)
    nodes = graph_nodes(vocab)
    index = index_by_id(nodes)
    header = index.get(VOCABULARY_IRI)
    if header is None or "owl:Ontology" not in (
        header.get("@type")
        if isinstance(header.get("@type"), list)
        else [header.get("@type")]
    ):
        error("controlled_vocabulary.jsonld: missing owl:Ontology header (#433)")
    elif not header.get("owl:versionInfo"):
        error("controlled_vocabulary.jsonld: owl:Ontology header lacks owl:versionInfo")
    else:
        print(f"  OK: ontology header {VOCABULARY_IRI} version {header['owl:versionInfo']}")

    both, total, missing = property_coverage(nodes)
    coverage = (both / total) if total else 0.0
    if coverage < 0.95:
        error(
            f"controlled_vocabulary.jsonld: {both}/{total} properties have "
            f"rdfs:domain and rdfs:range ({coverage:.1%} < 95%)"
        )
        for term in missing[:15]:
            print(f"    missing domain+range: {term}")
    else:
        print(
            f"  OK: {both}/{total} properties have rdfs:domain and rdfs:range "
            f"({coverage:.1%})"
        )

    leftovers = placeholder_ids(nodes)
    if leftovers:
        error(
            f"controlled_vocabulary.jsonld: {len(leftovers)} placeholder "
            f"comments still contain {PLACEHOLDER_NEEDLE!r}"
        )
    else:
        print("  OK: 0 placeholder vocabulary comments")

    junk = sorted(JUNK_TERMS & set(index))
    if junk:
        error(f"controlled_vocabulary.jsonld: junk terms still declared: {junk}")
    else:
        print("  OK: junk terms jsonld/counts/note/scope/title removed")

    parked = [
        nid
        for nid, node in index.items()
        if is_unresolved_individual(node)
    ]
    if parked:
        error(
            f"controlled_vocabulary.jsonld: {len(parked)} unresolved "
            "placeholder individuals still in the T-Box"
        )
    unresolved_path = krr_dir / "unresolved_references.jsonld"
    if not unresolved_path.is_file():
        error("unresolved_references.jsonld not found (#433)")
    else:
        print(
            f"  OK: unresolved placeholders live in {unresolved_path.name} "
            f"({len(graph_nodes(load_jsonld(unresolved_path)))} individuals)"
        )

    if METADATA_PATH.is_file():
        metadata = load_jsonld(METADATA_PATH)
        meta_classes = [
            node.get("@id")
            for node in graph_nodes(metadata)
            if isinstance(node, dict) and "owl:Class" in (
                node.get("@type")
                if isinstance(node.get("@type"), list)
                else [node.get("@type")]
            )
        ]
        if meta_classes:
            error(
                "metadata.jsonld @graph still declares owl:Class nodes "
                f"(default-graph T-Box leak): {meta_classes[:8]}"
            )
        conforms = metadata.get("dcterms:conformsTo")
        conforms_id = (
            conforms.get("@id")
            if isinstance(conforms, dict)
            else conforms
        )
        if conforms_id != VOCABULARY_IRI:
            error(
                "metadata.jsonld: dcterms:conformsTo must point at "
                f"{VOCABULARY_IRI}"
            )
        else:
            print("  OK: metadata.jsonld is DCAT-only and conformsTo the CV")

    missing_schema = schema_ids_missing_from_vocab(index, krr_dir)
    if missing_schema:
        error(
            "subcorpus *_schema.json terms missing from the CV: "
            + ", ".join(
                f"{name}({len(ids)})" for name, ids in missing_schema.items()
            )
        )
    else:
        print("  OK: 4 subcorpus schemas are projections of the CV")


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
            json.dumps(_normalize_parity_value(item), sort_keys=True, ensure_ascii=False)
             for item in value
        )
    if isinstance(value, dict):
        return {k: _normalize_parity_value(v) for k, v in value.items()}
    return value


def _parity_field_drift(source_node: dict, combined_node: dict) -> list[str]:
    drift: list[str] = []
    for f in PROVISION_PARITY_FIELDS:
        if f not in source_node and f not in combined_node:
            continue
        if f == "@type":
            # #519: the builder forward-chains rdf:type over the subclass
            # hierarchy, so the combined node's @type is the source @type plus
            # its entailed supertypes — an intentional superset, not drift.
            # Compare the combined @type against the source @type run through the
            # SAME rollup, so a genuinely divergent type is still caught (combined
            # dropped a source type, or gained a non-entailed one).
            src_types = source_node.get(f)
            expected = (
                _materialize_supertypes(src_types)
                if isinstance(src_types, list)
                else src_types
            )
            if _normalize_parity_value(expected) != _normalize_parity_value(
                combined_node.get(f)
            ):
                drift.append(f)
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
    # #416: compact-IRI prefixes whose combined nodes are exempt from the
    # stale-extra check because their overlay source could not be ingested (an
    # un-materialised LFS pointer). Empty when every overlay is materialised —
    # so a stale overlay node is still flagged in the normal case. Prefixes
    # (not @type) so an overlay's generic owl:Ontology wrapper node is covered.
    extra_exempt_id_prefixes: tuple[str, ...] = ()


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

    # #416: graph-closure stub nodes (estleg:isStubNode) are synthesised by the
    # builder, not drawn from any source file, so they are always legitimate
    # extras.
    stub_ids = {
        nid
        for nid, node in combined_nodes.items()
        if node.get(estleg_common.STUB_NODE_MARKER) is True
    }
    # An overlay whose source was an un-materialised LFS pointer could not be
    # ingested, so its merged nodes (matched by @id prefix, which also covers
    # the overlay's owl:Ontology wrapper node) are exempt from the stale-extra
    # check for THIS run only. When the source IS materialised
    # `extra_exempt_id_prefixes` is empty, so a stale overlay node IS flagged.
    exempt_prefix_ids: set[str] = set()
    if target.extra_exempt_id_prefixes:
        prefixes = target.extra_exempt_id_prefixes
        exempt_prefix_ids = {nid for nid in combined_nodes if nid.startswith(prefixes)}

    structural_drift = False

    missing = source_ids - combined_ids - target.expected_missing_ids
    if missing:
        error(f"{label}: missing {len(missing)} source graph IDs")
        for node_id in sorted(missing)[:20]:
            print(f"    missing from combined: {node_id}")
        structural_drift = True

    extras = (
        combined_ids
        - source_ids
        - target.allowlist_ids
        - target.expected_extra_ids
        - stub_ids
        - exempt_prefix_ids
    )
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
            "estleg:EURlex_Decisions_Map",
            "estleg:EURlex_Directives_Map",
            "estleg:EURlex_Regulations_Map",
        ),
        expected_extras=("estleg:EURlex_Combined_Map",),
    ),
    SubcorpusCombinedSpec(
        name="curia",
        peep_glob="curia/*_peep.json",
        combined_path_rel="curia/curia_combined.jsonld",
        schema_files=("curia/curia_schema.json",),
        expected_missing=(
            "estleg:CURIA_Ag_Opinions_Map",
            "estleg:CURIA_Court_Opinions_Map",
            "estleg:CURIA_Judgments_Map",
            "estleg:CURIA_Orders_Map",
            "estleg:CURIA_Other_Map",
        ),
        expected_extras=("estleg:CURIA_Combined_Map",),
    ),
    SubcorpusCombinedSpec(
        name="eelnoud",
        peep_glob="eelnoud/*_peep.json",
        combined_path_rel="eelnoud/eelnoud_combined.jsonld",
        schema_files=("eelnoud/eelnoud_schema.json",),
        expected_missing=(
            "estleg:Eelnoud_PublicConsultation_Map",
            "estleg:Eelnoud_Submission_Map",
            "estleg:Eelnoud_Review_Map",
        ),
        expected_extras=("estleg:Eelnoud_Combined_Map",),
    ),
)


def validate_combined_ontology(krr_dir: Path = KRR_DIR):
    print("\n--- Combined Ontology Artifact ---")
    combined_path = krr_dir / "combined_ontology.jsonld"
    source_nodes, allowlist_ids, source_files = collect_source_nodes(krr_dir)
    # #416: the enrichment overlays are fully merged into combined, so they are
    # canonical sources too — every overlay node must be present and none may be
    # a stale extra. Pointer-tolerant: an un-materialised LFS overlay
    # (annotations) is skipped with a warning; only THEN are its node @id
    # prefixes exempted from the stale-extra check (via extra_exempt_id_prefixes),
    # so a stale node in a materialised overlay is still flagged.
    extra_exempt_id_prefixes: list[str] = []
    for path in estleg_common.iter_combined_overlay_files(krr_dir):
        if _is_lfs_pointer(path):
            try:
                subdir = path.relative_to(krr_dir).parts[0]
            except ValueError:
                subdir = path.parent.name
            prefixes = estleg_common.COMBINED_OVERLAY_ID_PREFIXES.get(subdir, ())
            warn(
                f"combined_ontology.jsonld: overlay source {path.name} is an "
                f"un-materialised LFS pointer — run `git lfs pull` for full "
                f"parity (exempting {len(prefixes)} @id prefix(es) for this run)"
            )
            extra_exempt_id_prefixes.extend(prefixes)
            continue
        _ingest_graph_into(path, source_nodes)
        source_files.append(path)
    target = CombinedParityTarget(
        label="combined_ontology.jsonld",
        combined_path=combined_path,
        source_files=source_files,
        source_nodes=source_nodes,
        allowlist_ids=allowlist_ids,
        extra_exempt_id_prefixes=tuple(extra_exempt_id_prefixes),
        # #616: the dataset-level owl:Ontology version header is synthesised by
        # the builder (estleg_common.combined_ontology_header), not drawn from any
        # source peep, so it is a legitimate extra — like the graph-closure stubs.
        expected_extra_ids={estleg_common.ONTOLOGY_IRI},
    )
    _check_combined_parity(target)


def validate_combined_graph_closure(krr_dir: Path = KRR_DIR):
    """#416 closure gate: combined must load standalone with zero dangling refs.

    Reads combined alone (no sibling corpora) and asserts that EVERY ``estleg:``
    object reference resolves to a node in the file — no exemptions (#561/#589):
    version forward edges are stripped at build time and the amendment layer is
    merged, so a residual dangling ref is a real defect, not silently "Closed".
    Also checks that every synthesised stub is actually referenced (no stale
    orphan stubs) and carries only the whitelisted shaped-closure edges (#488) —
    each of which must itself resolve in-graph.
    This fails CI on a stale or incomplete combined — the parity guarantee the
    merge + stub build depends on.
    """
    print("\n--- Combined Graph Closure (#416) ---")
    combined_path = krr_dir / "combined_ontology.jsonld"
    if not combined_path.exists():
        error("combined_ontology.jsonld: not found")
        return
    if _is_lfs_pointer(combined_path):
        warn("combined_ontology.jsonld: un-materialised LFS pointer — skipping closure")
        return
    doc = validate_json_syntax(combined_path)
    if not isinstance(doc, dict):
        return

    nodes = [n for n in doc.get("@graph", []) if isinstance(n, dict)]
    # Canonicalise @ids to the compact estleg: form (matching the canonical
    # targets iter_node_estleg_refs yields) so an expanded-IRI node is still
    # recognised as present.
    node_ids: set[str] = set()
    stub_ids: set[str] = set()
    for n in nodes:
        nid = n.get("@id")
        if not isinstance(nid, str):
            continue
        canon = estleg_common.canonical_estleg_ref(nid) or nid
        node_ids.add(canon)
        if n.get(estleg_common.STUB_NODE_MARKER) is True:
            stub_ids.add(canon)
    exempt = estleg_common.COMBINED_CLOSURE_EXEMPT_PREDICATES
    allowed_stub_edges = estleg_common.STUB_SEMANTIC_EDGE_PREDICATES

    dangling: dict[str, set[str]] = defaultdict(set)
    # Every incoming reference keeps a stub alive (#561/#589: no exempt
    # predicates remain — version forward edges are stripped and the amendment
    # layer is merged), so a stub referenced by nothing is stale and must
    # surface as an orphan.
    referenced: set[str] = set()
    leaky_stubs: list[str] = []
    for node in nodes:
        is_stub = node.get(estleg_common.STUB_NODE_MARKER) is True
        node_refs = list(estleg_common.iter_node_estleg_refs(node))
        # #488: a stub may carry the whitelisted shaped-closure edges
        # (estleg:partOfAct / enactedBy / enactedByMunicipality / amendingDraft)
        # — and only those; any other estleg: object ref on a stub is a
        # stripping regression. The targets of the allowed edges must still
        # resolve in-graph, which the dangling check below enforces for all nodes.
        if is_stub and any(pred not in allowed_stub_edges for pred, _ in node_refs):
            leaky_stubs.append(str(node.get("@id")))
        for predicate, target in node_refs:
            if predicate in exempt:
                continue
            referenced.add(target)
            if target not in node_ids:
                dangling[predicate].add(target)

    total_dangling = sum(len(v) for v in dangling.values())
    if total_dangling:
        error(
            f"combined_ontology.jsonld: {total_dangling} dangling estleg: object "
            f"IRIs across {len(dangling)} non-exempt predicate(s) — graph not closed"
        )
        for pred in sorted(dangling, key=lambda p: len(dangling[p]), reverse=True)[:8]:
            targets = dangling[pred]
            print(f"    {pred}: {len(targets)} unresolved, e.g. {sorted(targets)[0]}")

    orphan_stubs = stub_ids - referenced
    if orphan_stubs:
        error(
            f"combined_ontology.jsonld: {len(orphan_stubs)} stub node(s) referenced "
            f"by nothing — stale stubs, rebuild combined"
        )
        for nid in sorted(orphan_stubs)[:10]:
            print(f"    orphan stub: {nid}")

    if leaky_stubs:
        error(
            f"combined_ontology.jsonld: {len(leaky_stubs)} stub node(s) carry "
            f"disallowed estleg: object refs — a stub may carry only the shaped "
            f"closure edges {sorted(allowed_stub_edges)}"
        )
        for nid in sorted(leaky_stubs)[:10]:
            print(f"    leaky stub: {nid}")

    # #561 overlay census: every node a merged overlay subdir
    # (COMBINED_OVERLAY_SUBDIRS) contributes must be PRESENT in combined — not
    # merely non-dangling. A partial-merge regression (an overlay silently stops
    # contributing some nodes) passes the dangling check whenever the dropped
    # nodes are not referenced by a non-exempt edge, so assert that every
    # source-overlay @id resolves to a node in combined.
    overlay_missing: dict[str, int] = defaultdict(int)
    overlay_sample: dict[str, str] = {}
    overlay_source_errors = 0
    for opath in estleg_common.iter_combined_overlay_files(krr_dir):
        try:
            with open(opath, "r", encoding="utf-8") as f:
                odoc = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            # An unreadable/malformed overlay source must NOT let the census
            # silently report "all present" — its nodes can't be checked, so
            # surface it as an error (the validate_all JSON-syntax pass also
            # flags it, but direct closure-gate callers rely on this) AND gate
            # the success line below on it.
            error(f"combined overlay census: cannot read overlay source {opath.name}: {exc}")
            overlay_source_errors += 1
            continue
        if not isinstance(odoc, dict):
            error(f"combined overlay census: overlay source {opath.name} is not a JSON object")
            overlay_source_errors += 1
            continue
        subdir = opath.relative_to(krr_dir).parts[0]
        for onode in odoc.get("@graph", []):
            if not isinstance(onode, dict):
                continue
            oid = onode.get("@id")
            if not isinstance(oid, str):
                continue
            if (estleg_common.canonical_estleg_ref(oid) or oid) not in node_ids:
                overlay_missing[subdir] += 1
                overlay_sample.setdefault(subdir, oid)
    if overlay_missing:
        total_missing = sum(overlay_missing.values())
        error(
            f"combined_ontology.jsonld: {total_missing} merged-overlay node(s) "
            f"absent from combined across {len(overlay_missing)} subdir(s) — the "
            f"overlay layer did not fully merge into combined (rebuild combined)"
        )
        for subdir in sorted(overlay_missing):
            print(f"    {subdir}: {overlay_missing[subdir]} missing, e.g. {overlay_sample[subdir]}")

    if not (total_dangling or orphan_stubs or leaky_stubs or overlay_missing
            or overlay_source_errors):
        print(
            f"  Closed: 0 dangling estleg: refs across {len(nodes)} nodes; "
            f"{len(stub_ids)} stub nodes all referenced; all merged-overlay "
            f"nodes present (census)"
        )


def validate_harmonisation_symmetry(krr_dir: Path = KRR_DIR):
    """#578/#631 gate: every ``harmonisation_by_directive/harm_*.json``
    ``estleg:harmonises`` → act edge must have a backing ``estleg:harmonisedWith``
    on that act in the law peeps.

    Catches the deprecated-rejection asymmetry the #632 review found — a harm
    inverse file pointing at a (canonical) act that never received the forward
    edge, e.g. after a directive was stripped off a deprecated act instead of
    moved onto its ``dcterms:isReplacedBy`` replacement.
    """
    print("\n--- Harmonisation Symmetry (#578/#631) ---")
    harm_dir = krr_dir / "harmonisation" / "harmonisation_by_directive"
    if not harm_dir.is_dir():
        warn("harmonisation_by_directive/: not found — skipping symmetry gate")
        return
    forward: set[tuple[str, str]] = set()
    dep_links = 0
    dep_sample: str | None = None
    # #631 review: harmonisation is DERIVED from transposition_mapping.json, so
    # every harmonisedWith (act→directive) must be backed by a mapping row whose
    # law_files include that act's peep — else the link isn't reproducible and a
    # regen would silently drop it. Build directive CELEX → {peep filename}.
    mapping_path = krr_dir / "reports" / "transposition_mapping.json"
    dir_files: dict[str, set[str]] = {}
    if mapping_path.is_file() and not _is_lfs_pointer(mapping_path):
        try:
            with open(mapping_path, encoding="utf-8") as fh:
                for m in json.load(fh).get("mappings", []):
                    dir_files.setdefault(m.get("directive_celex"), set()).update(
                        Path(lf).name for lf in m.get("law_files", []))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            dir_files = {}
    unbacked = 0
    unbacked_sample: tuple[str, str] | None = None
    for path in krr_dir.glob("*_peep.json"):
        if _is_lfs_pointer(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        is_dep = act_deprecation(doc)[0]
        for n in doc.get("@graph", []):
            if not isinstance(n, dict):
                continue
            # #631 review: a deprecated/replaced act must carry NEITHER directive
            # field — both estleg:harmonisedWith and estleg:transposesDirective
            # belong on its dcterms:isReplacedBy canonical, not split across the pair.
            if is_dep and (n.get("estleg:harmonisedWith") or n.get("estleg:transposesDirective")):
                dep_links += 1
                dep_sample = dep_sample or str(n.get("@id"))
            hw = n.get("estleg:harmonisedWith")
            if not hw:
                continue
            for x in (hw if isinstance(hw, list) else [hw]):
                if isinstance(x, dict) and x.get("@id"):
                    forward.add((n["@id"], x["@id"]))
                    if dir_files and not is_dep:
                        cel = x["@id"].replace("estleg:Harmonisation_", "")
                        if path.name not in dir_files.get(cel, set()):
                            unbacked += 1
                            unbacked_sample = unbacked_sample or (path.name, cel)
    if dep_links:
        error(
            f"harmonisation: {dep_links} deprecated act(s) still carry "
            f"estleg:harmonisedWith/transposesDirective — move them onto the "
            f"dcterms:isReplacedBy canonical. First: {dep_sample}"
        )
    if unbacked:
        error(
            f"harmonisation: {unbacked} harmonisedWith link(s) not backed by a "
            f"transposition_mapping.json row (law not mapped to that directive — "
            f"not reproducible; a regen would drop them). First: {unbacked_sample}"
        )
    asym = total = 0
    sample: tuple[str, str] | None = None
    for path in sorted(harm_dir.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        for n in doc.get("@graph", []):
            cel = n.get("@id", "") if isinstance(n, dict) else ""
            if not cel.startswith("estleg:Harmonisation_"):
                continue
            h = n.get("estleg:harmonises")
            for x in (h if isinstance(h, list) else [h] if h else []):
                if isinstance(x, dict) and x.get("@id"):
                    total += 1
                    if (x["@id"], cel) not in forward:
                        asym += 1
                        sample = sample or (cel, x["@id"])
    if asym:
        error(
            f"harmonisation: {asym}/{total} harmonises→act edge(s) lack a backing "
            f"estleg:harmonisedWith (asymmetric inverse — a directive points at an "
            f"act that never received the forward edge). First: {sample}"
        )
    else:
        print(f"  OK: all {total} harmonises→act edges have a backing harmonisedWith")

    # #631 review: harmonisation_report.json's declared parallel-directive count
    # must match the body — a member-level edit that deletes whole entries
    # (instead of the deprecated member) otherwise drifts silently.
    report = krr_dir / "harmonisation" / "harmonisation_report.json"
    if report.is_file() and not _is_lfs_pointer(report):
        try:
            with open(report, encoding="utf-8") as fh:
                rep = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            rep = None
        if isinstance(rep, dict):
            declared = rep.get("directives_with_parallels")
            entries = rep.get("harmonisation_entries")
            if not isinstance(entries, list):
                entries = []
            if isinstance(declared, int) and declared != len(entries):
                error(
                    f"harmonisation_report.json: directives_with_parallels={declared} but "
                    f"harmonisation_entries has {len(entries)} entries — entries dropped "
                    "wholesale (a deprecated member should be removed, not its directive)."
                )
            # #631 review: each entry's estonian_laws IRIs must equal its
            # harm_*.json harmonises IRIs — otherwise the report body silently
            # disagrees with the per-directive files (e.g. a member removed by
            # name when only its name, not its canonical IRI, was deprecated).
            entry_mismatch = 0
            sample_e: str | None = None
            for e in entries:
                cel = e.get("directive_celex") if isinstance(e, dict) else None
                hf = harm_dir / f"harm_{cel}.json"
                if not cel or not hf.is_file():
                    continue
                try:
                    with open(hf, encoding="utf-8") as fh:
                        hdoc = json.load(fh)
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    continue
                hiris: set[str] = set()
                for n in hdoc.get("@graph", []):
                    if isinstance(n, dict) and n.get("@id", "").startswith("estleg:Harmonisation_"):
                        h = n.get("estleg:harmonises", [])
                        hiris = {x["@id"] for x in (h if isinstance(h, list) else [h])
                                 if isinstance(x, dict) and x.get("@id")}
                riris = {m.get("iri") for m in e.get("estonian_laws", []) if isinstance(m, dict)}
                if hiris and riris != hiris:
                    entry_mismatch += 1
                    sample_e = sample_e or str(cel)
            if entry_mismatch:
                error(
                    f"harmonisation_report.json: {entry_mismatch} entry(ies) whose "
                    f"estonian_laws IRIs differ from their harm_*.json harmonises set "
                    f"(report body disagrees with the per-directive files). First: {sample_e}"
                )


def validate_provision_text_quality(krr_dir: Path = KRR_DIR):
    """#572/#613 release gate for the structured-law peeps (generate_all_laws).

    Blocking:
      * no literal ``<sup>`` markup in any law-peep string — RT's superscript
        section numbers must render as Unicode (``¹²³``), not leak HTML (#572);
      * no ``estleg:summary`` that is exactly ``estleg:legalText`` repeated — the
        regression guard for #613, now that the summary is derived from the
        (non-doubling) full text rather than an independent leaf-walk.

    (No broader/heuristic summary-repetition warning: with the root cause fixed
    the only remaining matches are legitimate cross-subsection phrase repeats,
    e.g. two lõiked both opening "Käesoleva seaduse §-s 2 tähendatud…" — all
    false positives, so the signal was dropped rather than shipped noisy. The
    wider ``<sup>`` leak in the subcorpus generators is tracked separately.)
    """
    print("\n--- Provision Text Quality (#572/#613) ---")
    sup_fields = doubled_exact = scanned = 0
    sample_sup: tuple[str, str] | None = None
    sample_dup: str | None = None
    # The structured-law surface is the top-level peeps plus the two special-part
    # OWL law files merged into combined (KarS eriosa, TsÜS osa7) — all #572 scope.
    law_files = sorted(krr_dir.glob("*_peep.json")) + [
        krr_dir / "karistusseadustik_eriosa_owl.jsonld",
        krr_dir / "tsus_osa7_138_169_owl.jsonld",
    ]
    for path in law_files:
        if not path.exists() or _is_lfs_pointer(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        scanned += 1
        for n in doc.get("@graph", []):
            if not isinstance(n, dict):
                continue
            for k, v in n.items():
                if isinstance(v, str) and "<sup>" in v:
                    sup_fields += 1
                    sample_sup = sample_sup or (str(n.get("@id")), k)
            s, lt = n.get("estleg:summary"), n.get("estleg:legalText")
            if isinstance(s, str) and isinstance(lt, str):
                ss, lts = s.strip(), lt.strip()
                if lts and ss != lts and ss in (f"{lts} {lts}", f"{lts}{lts}"):
                    doubled_exact += 1
                    sample_dup = sample_dup or str(n.get("@id"))
    if sup_fields:
        error(
            f"#572: {sup_fields} law-peep field(s) carry literal <sup> markup "
            f"(should be Unicode superscript ¹²³). First: {sample_sup}"
        )
    if doubled_exact:
        error(
            f"#613: {doubled_exact} provision(s) have estleg:summary == estleg:legalText "
            f"repeated verbatim (the documented summary path shows the text twice). "
            f"First: {sample_dup}"
        )
    if not (sup_fields or doubled_exact):
        print(f"  OK: {scanned} law peeps free of <sup> markup and exact doubled summaries")


def validate_provision_version_monotonicity(krr_dir: Path = KRR_DIR):
    """#574 release gate: every per-provision version timeline must be monotone.

    Chains are walked along the canonical ``estleg:supersededByVersion`` edges
    (NOT a validFrom sort, which ties on same-day redactions and silently drops
    the final closed-to-current transition). For each superseded version ``v``
    with successor ``w``:

    * ``v`` MUST carry a ``versionValidTo`` (it is closed) — a missing end on a
      superseded version is corrupt;
    * ``v.versionValidTo`` MUST be strictly before ``w.versionValidFrom`` (the
      #306 exclusive-end contract). ``validTo == w.validFrom`` is a 1-day overlap
      that makes a point-in-time as-of query return two active versions on the
      transition day; ``validTo > w.validFrom`` is an inversion;
    * ``v.versionValidFrom`` MUST be before ``w.versionValidFrom`` — a zero- or
      negative-duration redaction (``v`` starts on/after its successor) never had
      a distinct active period and must be dropped, not retained.

    The open-ended current version (no successor) legitimately has no validTo.
    """
    print("\n--- Provision Version Monotonicity (#574) ---")
    pv_dir = krr_dir / "provision_versions"
    if not pv_dir.is_dir():
        warn("provision_versions/: not found — skipping monotonicity gate")
        return

    def _val(node: dict, key: str):
        v = node.get(key)
        return v.get("@value") if isinstance(v, dict) else None

    overlap = inverted = missing_to = zero_dur = total = 0
    sample: tuple[str, str] | None = None
    for path in sorted(pv_dir.glob("*.jsonld")):
        if _is_lfs_pointer(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        graph = doc.get("@graph", [])
        by_id = {n["@id"]: n for n in graph if isinstance(n, dict) and n.get("@id")}
        for v in graph:
            if not isinstance(v, dict) or "ProvisionVersion" not in str(v.get("@type", "")):
                continue
            sb = v.get("estleg:supersededByVersion")
            w_id = sb.get("@id") if isinstance(sb, dict) else None
            w = by_id.get(w_id) if w_id else None
            if w is None:
                continue  # current/open-ended version (or successor outside this file)
            total += 1
            v_to, w_from, v_from = _val(v, "estleg:versionValidTo"), _val(w, "estleg:versionValidFrom"), _val(v, "estleg:versionValidFrom")
            vid = str(v.get("@id"))
            if v_to is None:
                missing_to += 1
                sample = sample or (vid, "superseded version missing versionValidTo")
            elif w_from and v_to == w_from:
                overlap += 1
                sample = sample or (vid, "validTo == successor validFrom (overlap)")
            elif w_from and v_to > w_from:
                inverted += 1
                sample = sample or (vid, "validTo > successor validFrom (inverted)")
            if v_from and w_from and v_from >= w_from:
                zero_dur += 1
                sample = sample or (vid, "validFrom >= successor validFrom (zero/negative duration)")
    bad = overlap + inverted + missing_to + zero_dur
    if bad:
        error(
            f"provision_versions: {bad} non-monotone version transition(s) "
            f"({overlap} overlapping, {inverted} inverted, {missing_to} superseded-missing-validTo, "
            f"{zero_dur} zero/negative-duration) across {total} supersededByVersion edges — "
            f"point-in-time as-of queries return duplicate/corrupt active versions; "
            f"regenerate the version layer. First: {sample}"
        )
    else:
        print(f"  OK: all {total} version transitions (via supersededByVersion) monotone")


def validate_provision_version_encoding(krr_dir: Path = KRR_DIR):
    """#355 release gate: ProvisionVersion versionText must not contain U+FFFD.

    Historic pre-2010 RT XML was often windows-1257; a forced UTF-8 decode
    wrote U+FFFD pairs into ``estleg:versionText`` (``või`` → ``v\\ufffd\\ufffdi``).
    After the decoder + committed-file cleanup this must stay at zero.
    """
    print("\n--- Provision Version Encoding (#355) ---")
    pv_dir = krr_dir / "provision_versions"
    if not pv_dir.is_dir():
        warn("provision_versions/: not found — skipping U+FFFD encoding gate")
        return

    fffd = "\ufffd"
    files_hit = nodes_hit = fields_hit = scanned = 0
    sample: tuple[str, str] | None = None
    for path in sorted(pv_dir.glob("*.jsonld")):
        if _is_lfs_pointer(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        scanned += 1
        file_hit = False
        for node in doc.get("@graph", []):
            if not isinstance(node, dict):
                continue
            if "ProvisionVersion" not in str(node.get("@type", "")):
                continue
            text = node.get("estleg:versionText")
            if not isinstance(text, str) or fffd not in text:
                continue
            file_hit = True
            nodes_hit += 1
            fields_hit += text.count(fffd)
            sample = sample or (path.name, str(node.get("@id")))
        if file_hit:
            files_hit += 1
    if nodes_hit:
        error(
            f"#355: {fields_hit} U+FFFD replacement char(s) in "
            f"{nodes_hit} ProvisionVersion versionText value(s) across "
            f"{files_hit} provision_versions file(s) (scanned {scanned}). "
            f"Re-decode RT XML without forcing UTF-8 and rerun "
            f"generate_provision_versions --cleanup-fffd. First: {sample}"
        )
    else:
        print(
            f"  OK: {scanned} provision_versions files free of U+FFFD in versionText"
        )


def _version_date(node: dict, key: str) -> str | None:
    raw = node.get(key)
    value = raw.get("@value") if isinstance(raw, dict) else raw
    return value if isinstance(value, str) and value else None


def version_covers_date(node: dict, as_of: str) -> bool:
    """True when a ProvisionVersion interval contains *as_of* (inclusive ends).

    Absent ``versionValidTo`` means still in force. Matches the MCP
    ``version_in_force_on`` contract so as-of queries and the #532 gate
    agree.
    """
    valid_from = _version_date(node, "estleg:versionValidFrom")
    if not valid_from or not as_of:
        return False
    if valid_from > as_of:
        return False
    valid_to = _version_date(node, "estleg:versionValidTo")
    return not valid_to or as_of <= valid_to


def version_layer_lag(pv_doc: dict, kehtiv: str) -> str | None:
    """Return newest ``versionValidFrom`` when *no* version covers *kehtiv*.

    #532: a current redaction that started before the peep snapshot
    (e.g. validFrom 2026-05-22, open validTo, kehtiv 2026-05-24) still
    answers as-of queries for the snapshot date. That is not a lag.
    A lag is an actual coverage hole: every version ends before *kehtiv*
    or starts after it.
    """
    if not isinstance(pv_doc, dict) or not kehtiv:
        return None
    newest: str | None = None
    covers = False
    for node in pv_doc.get("@graph") or []:
        if not isinstance(node, dict):
            continue
        if "estleg:ProvisionVersion" not in node_types(node):
            continue
        valid_from = _version_date(node, "estleg:versionValidFrom")
        if valid_from and (newest is None or valid_from > newest):
            newest = valid_from
        if version_covers_date(node, kehtiv):
            covers = True
    if covers or newest is None:
        return None
    return newest


def _index_files_by_slug(krr_dir: Path) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    index_path = krr_dir / "INDEX.json"
    try:
        doc = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return mapping
    if not isinstance(doc, dict):
        return mapping
    for law in doc.get("laws") or []:
        if not isinstance(law, dict):
            continue
        name = law.get("name")
        files = law.get("files")
        if isinstance(name, str) and name and isinstance(files, list):
            mapping[name] = [f for f in files if isinstance(f, str) and f]
    return mapping


def _kehtiv_literal(node: dict) -> str | None:
    raw = node.get("estleg:kehtiv")
    value = raw.get("@value") if isinstance(raw, dict) else raw
    if isinstance(value, str) and value:
        return value
    return None


def _kehtiv_for_slug(
    slug: str, krr_dir: Path, index_files: dict[str, list[str]]
) -> str | None:
    """First ``estleg:kehtiv`` found on the law peep(s) for *slug*."""
    seen: set[Path] = set()
    candidates: list[Path] = []

    def _add(path: Path) -> None:
        if path.is_file() and path not in seen:
            seen.add(path)
            candidates.append(path)

    _add(krr_dir / f"{slug}_peep.json")
    for fname in index_files.get(slug) or []:
        _add(krr_dir / fname)
    for extra in sorted(krr_dir.glob(f"{slug}_*_peep.json")):
        _add(extra)
    for path in candidates:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        for node in doc.get("@graph") or []:
            if not isinstance(node, dict):
                continue
            kehtiv = _kehtiv_literal(node)
            if kehtiv:
                return kehtiv
    return None


def validate_regulation_version_coverage(krr_dir: Path = KRR_DIR):
    """#431: ≥90% of state-regulation peeps must have a version sidecar."""
    have, total, ratio = riik_version_coverage(krr_dir)
    if total == 0:
        return
    if ratio < 0.9:
        error(
            f"regulation version coverage {have}/{total} ({ratio:.1%}) "
            f"is below the #431 90% gate"
        )


def validate_last_amendment_matches_versions(krr_dir: Path = KRR_DIR):
    """#429: act lastAmendmentDate must equal max versionValidFrom when both exist."""
    vers_dir = krr_dir / "provision_versions"
    if not vers_dir.is_dir():
        return
    for path in sorted(vers_dir.glob("*.jsonld")):
        try:
            version_doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        by_date = collect_versions_by_date(version_doc)
        if not by_date:
            continue
        latest = max(by_date)
        slug = path.stem
        peeps = [krr_dir / f"{slug}_peep.json", *sorted(krr_dir.glob(f"{slug}_osa*_peep.json"))]
        for peep in peeps:
            if not peep.is_file():
                continue
            try:
                doc = json.loads(peep.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            root = act_root_node(doc)
            if root is None or "estleg:lastAmendmentDate" not in root:
                continue
            raw = root.get("estleg:lastAmendmentDate")
            got = raw.get("@value") if isinstance(raw, dict) else raw
            if got != latest:
                error(
                    f"{peep.name}: lastAmendmentDate {got} != version-layer "
                    f"max versionValidFrom {latest} (#429)"
                )


def validate_version_layer_freshness(krr_dir: Path = KRR_DIR):
    """#532: every sidecar must have a version interval that contains peep kehtiv.

    A current redaction that *started* before the snapshot date is fine
    when ``versionValidTo`` is absent (still in force). Inventing
    2026-05-23/24 redactions is not the fix. A sidecar whose latest
    interval ends before kehtiv is a real hole and is an error.
    """
    print("\n--- Provision Version Freshness (#532) ---")
    pv_dir = krr_dir / "provision_versions"
    if not pv_dir.is_dir():
        warn("provision_versions/: not found — skipping version-layer freshness gate")
        return

    index_files = _index_files_by_slug(krr_dir)
    lagging = 0
    scanned = 0
    sample: tuple[str, str, str] | None = None
    for path in sorted(pv_dir.glob("*.jsonld")):
        if _is_lfs_pointer(path):
            continue
        kehtiv = _kehtiv_for_slug(path.stem, krr_dir, index_files)
        if not kehtiv:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        scanned += 1
        newest = version_layer_lag(doc, kehtiv)
        if newest is None:
            continue
        lagging += 1
        if sample is None:
            sample = (path.name, newest, kehtiv)
    if lagging:
        first = (
            f"{sample[0]} newestFrom={sample[1]} kehtiv={sample[2]}"
            if sample
            else "n/a"
        )
        error(
            f"#532: {lagging} provision_versions sidecar(s) have no version "
            f"covering peep kehtiv across {scanned} compared file(s). "
            f"First: {first}."
        )
        print(f"  lagging sidecars: {lagging}")
    else:
        print(
            f"  OK: {scanned} sidecar(s) have a version interval covering "
            f"peep kehtiv"
        )


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


# ---------------------------------------------------------------------------
# Regen-pending staleness guards (#366, #348, #384).
#
# The underlying corpus for these three checks is currently stale: the
# fixes live in the generators but the heavy/network regen step that
# rewrites the artifacts has not been run on this tree. A hard-fail gate
# would therefore turn CI red on a known-deferred task. These guards are
# deliberately implemented as **warnings** (via `warn()`, never
# `error()`) so the staleness stays visible — and a *new* regression is
# still surfaced — without breaking the green build. Each carries a
# `# TODO: promote to error()` marker to flip once the regen lands.
# ---------------------------------------------------------------------------

# Object-property pairs whose `owl:inverseOf` axiom PR #297 added to
# `controlled_vocabulary.jsonld`. The combined artifact must mirror each
# forward property's inverse so OWL reasoners loading only the combined
# can materialise the pair. Keyed by the forward property whose node
# carries `owl:inverseOf`; the value is the expected inverse IRI (#366).
TBOX_INVERSE_OF_PAIRS: dict[str, str] = {
    "estleg:amendedBy": "estleg:amends",
    "estleg:interpretedBy": "estleg:interpretsLaw",
    "estleg:references": "estleg:referencedBy",
}

# EULegislation provenance/linkage predicates that the current
# `generate_eu_legislation.py` emits. An artifact that predates those
# commits will have none of them, so a sample with 0% presence is the
# stale-artifact signal (#348).
EU_PROVENANCE_FIELDS: tuple[str, ...] = (
    "owl:sameAs",
    "dcterms:source",
    "eli:id_local",
)

# How many EULegislation nodes to spot-sample for `EU_PROVENANCE_FIELDS`
# presence. Sampling (rather than scanning all ~33k nodes) keeps the
# guard cheap; the staleness signal is uniform (all-or-nothing per regen)
# so a small deterministic prefix sample is representative (#348).
EU_PROVENANCE_SAMPLE_SIZE = 50

# Minimum events-to-unique ratio before an amendment chain is flagged as
# materially bloated. A healthy chain has ratio ~1.0 (one event per
# unique `(rtReference, entryIntoForce)` pair); the pre-dedup artifacts
# run 3-50x. 1.5 leaves generous headroom for benign near-duplicates
# while still catching the regression (#384).
AMENDMENT_BLOAT_RATIO_THRESHOLD = 1.5

# Ignore tiny chains when ranking offenders: a 2-event/1-unique chain is
# ratio 2.0 but only 1 duplicate, which is noise. The overall corpus
# ratio is still computed across every chain.
AMENDMENT_BLOAT_MIN_DUPLICATES = 2


def _amendment_pair_key(node: dict) -> tuple:
    """Return the dedup-invariant key for one `AmendmentEvent` node.

    The invariant is the `(estleg:rtReference, estleg:entryIntoForce)`
    pair (#384). `estleg:entryIntoForce` is an xsd:date value object, so
    its `@value` is unwrapped; `estleg:rtReference` is a plain string.
    """
    rt = node.get("estleg:rtReference")
    eif = node.get("estleg:entryIntoForce")
    if isinstance(eif, dict):
        eif = eif.get("@value")
    return (rt, eif)


def validate_tbox_inverse_parity(krr_dir: Path = KRR_DIR):
    """Warn when `combined_ontology.jsonld` omits T-Box inverse axioms (#366).

    PR #297 added `owl:inverseOf` to `estleg:amendedBy`/`interpretedBy`/
    `references` in `controlled_vocabulary.jsonld`, but the parity gate
    only compares provision-data fields (`PROVISION_PARITY_FIELDS`), so a
    combined artifact that predates the rebuild silently lacks those
    axioms. We compare the inverse declared on each forward-property node
    in the vocabulary against the same node in the combined and warn on
    any that did not propagate.

    NOTE: currently a *warning* on purpose — the combined is stale
    pending regen. # TODO: promote to error() after corpus regen (#366).
    """
    print("\n--- T-Box Inverse Parity ---")
    combined_path = krr_dir / "combined_ontology.jsonld"
    vocab_path = krr_dir / "controlled_vocabulary.jsonld"
    if not combined_path.exists() or not vocab_path.exists():
        warn(
            "T-Box inverse parity: combined_ontology.jsonld or "
            "controlled_vocabulary.jsonld not found"
        )
        return
    combined_doc = validate_json_syntax(combined_path)
    vocab_doc = validate_json_syntax(vocab_path)
    if not isinstance(combined_doc, dict) or not isinstance(vocab_doc, dict):
        return

    def _inverse_of(doc: dict, prop_id: str) -> str | None:
        for node in doc.get("@graph", []):
            if not isinstance(node, dict) or node.get("@id") != prop_id:
                continue
            value = node.get("owl:inverseOf")
            if isinstance(value, dict):
                return value.get("@id")
            if isinstance(value, str):
                return value
            return None
        return None

    missing: list[str] = []
    for prop_id, expected_inverse in TBOX_INVERSE_OF_PAIRS.items():
        vocab_inverse = _inverse_of(vocab_doc, prop_id)
        if vocab_inverse != expected_inverse:
            # The vocabulary itself does not declare the expected axiom —
            # not a combined-staleness signal, skip (the vocabulary is
            # the source of truth here).
            continue
        combined_inverse = _inverse_of(combined_doc, prop_id)
        if combined_inverse != expected_inverse:
            missing.append(f"{prop_id} owl:inverseOf {expected_inverse}")

    if missing:
        warn(
            f"combined_ontology.jsonld: missing {len(missing)} owl:inverseOf "
            f"T-Box axiom(s) declared in controlled_vocabulary.jsonld: "
            f"{', '.join(missing)} — rebuild the combined to propagate them"
        )
    else:
        print(
            f"  OK: {len(TBOX_INVERSE_OF_PAIRS)} owl:inverseOf axioms present "
            f"in combined_ontology.jsonld"
        )


def validate_eu_provenance_fields(krr_dir: Path = KRR_DIR):
    """Warn when sampled EULegislation nodes lack provenance fields (#348).

    `generate_eu_legislation.py` now stamps `owl:sameAs`/`dcterms:source`/
    `eli:id_local` on every `EULegislation` node, but the committed
    `eurlex/*_peep.json` artifacts predate those commits, so 100% of
    nodes lack them. No existing gate spot-samples emitted fields, so the
    divergence is silent. We sample a small deterministic prefix of the
    EU nodes and report the per-field missing counts.

    NOTE: currently a *warning* on purpose — the EU artifacts are stale
    pending regen. # TODO: promote to error() after corpus regen (#348).
    """
    print("\n--- EU Provenance Fields ---")
    peep_files = sorted((krr_dir / "eurlex").glob("*_peep.json"))
    if not peep_files:
        warn("EU provenance fields: no eurlex/*_peep.json files found")
        return

    sampled = 0
    missing_counts: dict[str, int] = {f: 0 for f in EU_PROVENANCE_FIELDS}
    for path in peep_files:
        if sampled >= EU_PROVENANCE_SAMPLE_SIZE:
            break
        doc = validate_json_syntax(path)
        if not isinstance(doc, dict):
            continue
        for node in doc.get("@graph", []):
            if sampled >= EU_PROVENANCE_SAMPLE_SIZE:
                break
            if not isinstance(node, dict):
                continue
            if "estleg:EULegislation" not in node_types(node):
                continue
            sampled += 1
            for field_name in EU_PROVENANCE_FIELDS:
                if field_name not in node:
                    missing_counts[field_name] += 1

    if sampled == 0:
        warn("EU provenance fields: no EULegislation nodes found to sample")
        return

    deficient = {f: c for f, c in missing_counts.items() if c}
    if deficient:
        summary = ", ".join(
            f"{f} missing on {c}/{sampled}" for f, c in deficient.items()
        )
        warn(
            f"eurlex/*_peep.json: sampled {sampled} EULegislation node(s) lack "
            f"provenance fields ({summary}) — artifact predates the generator "
            f"that emits owl:sameAs/dcterms:source/eli:id_local; re-run "
            f"scripts/generate_eu_legislation.py"
        )
    else:
        print(
            f"  OK: {sampled} sampled EULegislation nodes carry "
            f"owl:sameAs/dcterms:source/eli:id_local"
        )


def validate_amendment_duplicate_bloat(krr_dir: Path = KRR_DIR):
    """Warn when amendment chains have duplicate `AmendmentEvent` bloat (#384).

    PR #297 added `_dedupe_amendments` to `generate_amendment_history.py`,
    but `krr_outputs/amendments/` was last regenerated before the fix, so
    the pre-fix code minted one `AmendmentEvent` per `<muutmismärge>`
    block — the same amending act repeated once per amended provision.
    The dedup invariant is one event per unique
    `(estleg:rtReference, estleg:entryIntoForce)` pair, so a chain with
    materially more events than unique pairs is bloated. We report the
    top offending chains and the overall corpus duplicate ratio.

    NOTE: currently a *warning* on purpose — the amendment chains are
    stale pending regen. # TODO: promote to error() after corpus regen (#384).
    """
    print("\n--- Amendment Duplicate Bloat ---")
    chain_files = sorted((krr_dir / "amendments").glob("*.json"))
    if not chain_files:
        warn("amendment duplicate bloat: no amendments/*.json files found")
        return

    total_events = 0
    total_unique = 0
    offenders: list[tuple[float, int, int, str]] = []  # (ratio, events, unique, name)
    for path in chain_files:
        doc = validate_json_syntax(path)
        if not isinstance(doc, dict):
            continue
        events = [
            node
            for node in doc.get("@graph", [])
            if isinstance(node, dict)
            and "estleg:AmendmentEvent" in node_types(node)
        ]
        if not events:
            continue
        unique_pairs = {_amendment_pair_key(node) for node in events}
        event_count = len(events)
        unique_count = len(unique_pairs)
        total_events += event_count
        total_unique += unique_count
        duplicates = event_count - unique_count
        ratio = event_count / unique_count if unique_count else float(event_count)
        if (
            ratio >= AMENDMENT_BLOAT_RATIO_THRESHOLD
            and duplicates >= AMENDMENT_BLOAT_MIN_DUPLICATES
        ):
            offenders.append((ratio, event_count, unique_count, path.name))

    if total_events == 0:
        warn("amendment duplicate bloat: no AmendmentEvent nodes found")
        return

    overall_ratio = total_events / total_unique if total_unique else float(total_events)
    if offenders:
        offenders.sort(reverse=True)
        dup_pct = (total_events - total_unique) / total_events * 100
        warn(
            f"krr_outputs/amendments/: {len(offenders)} chain(s) have more "
            f"AmendmentEvent nodes than unique (rtReference, entryIntoForce) "
            f"pairs — {total_events} events vs {total_unique} unique "
            f"({dup_pct:.1f}% duplicates, overall ratio {overall_ratio:.2f}x); "
            f"re-run scripts/generate_amendment_history.py to apply the "
            f"_dedupe_amendments fix"
        )
        for ratio, event_count, unique_count, name in offenders[:10]:
            print(
                f"    {name}: {event_count} events / {unique_count} unique "
                f"({ratio:.1f}x)"
            )
        if len(offenders) > 10:
            print(f"    ... and {len(offenders) - 10} more bloated chains")
    else:
        print(
            f"  OK: {total_events} AmendmentEvent nodes across "
            f"{len(chain_files)} chains, no material duplicate bloat "
            f"(overall ratio {overall_ratio:.2f}x)"
        )


# Issue #454: historical `_DupN` collision-suffix IRIs on root law peeps
# (generator / fix_duplicate_ids remints). Until content-derived IDs land
# (Epic #407 / #448), pin the unique-@id count so a silent remint that
# mints more suffixes fails the default gate. Decreases are allowed.
# Measured 2026-08-18 on krr_outputs/*_peep.json only (not regulations).
DUPN_ID_RE = re.compile(r"_Dup\d+")
DUPN_IRI_BASELINE = 0


def root_law_peep_files(krr_dir: Path = KRR_DIR) -> list[Path]:
    """Return root-level law ``*_peep.json`` files (not regulations).

    Non-recursive on purpose: ``krr_outputs/regulations/**`` is out of
    scope for the #454 ceiling so the walk stays cheap. Operational
    state files are excluded even though none currently match this glob.
    """
    return [
        path
        for path in sorted(krr_dir.glob("*_peep.json"))
        if path.is_file() and path.name not in OPERATIONAL_STATE_FILES
    ]


def count_dupn_iris(files: list[Path]) -> int:
    """Count unique graph-node ``@id`` values matching ``_DupN`` (#454)."""
    found: set[str] = set()
    for path in files:
        doc = validate_json_syntax(path)
        if not isinstance(doc, dict):
            continue
        graph = doc.get("@graph")
        if not isinstance(graph, list):
            continue
        for node in graph:
            if not isinstance(node, dict):
                continue
            nid = node.get("@id")
            if isinstance(nid, str) and DUPN_ID_RE.search(nid):
                found.add(nid)
    return len(found)


def validate_dupn_iri_baseline(
    krr_dir: Path = KRR_DIR,
    *,
    files: list[Path] | None = None,
    baseline: int = DUPN_IRI_BASELINE,
) -> int:
    """Error if the `_DupN` IRI count on root law peeps grows (#454).

    INDEX-style count baseline: growth is an ``error()`` so the default
    gate fails. A remint that shrinks the family stays green.
    """
    print("\n--- _DupN IRI Baseline ---")
    if files is None:
        files = root_law_peep_files(krr_dir)
    count = count_dupn_iris(files)
    if count > baseline:
        error(
            f"_DupN IRI count {count} exceeds pinned baseline {baseline} "
            f"on {len(files)} root law peep files — new collision suffixes "
            f"must not grow"
        )
    else:
        print(
            f"  OK: {count} _DupN IRIs on {len(files)} root law peep files "
            f"(baseline {baseline}, must not grow)"
        )
    return count


def validate_id_uniqueness(all_ids: dict[str, list[str]]):
    print("\n--- @id Uniqueness ---")
    # Shared ontology class definitions are expected to appear in multiple files
    shared_class_ids = {
        "estleg:LegalPart", "estleg:Provision", "estleg:Section",
        "estleg:LegalConcept",
        "estleg:CaseType", "estleg:EUCourtDecisionType", "estleg:EUInstitution",
        # #562: core classes declared canonically in controlled_vocabulary.jsonld
        # (so the self-contained combined graph can type its most-used classes)
        # AND in their subcorpus *_schema.json / *_combined.jsonld with the SAME
        # declaration — a consistent shared TBox class id, not a collision.
        "estleg:CourtDecision", "estleg:DraftLegislation",
        "estleg:EULegislation", "estleg:HarmonisationLink",
        # estleg:interpretsLaw is canonically defined (with rdfs:domain/range) in
        # riigikohus_schema.json AND materialized as a graph-closure stub in
        # controlled_vocabulary.jsonld so the estleg:interpretedBy owl:inverseOf
        # axiom resolves inside combined_ontology.jsonld (#366 / Seadusloome gate).
        "estleg:interpretsLaw",
        # #563: the EU-transposition + cross-border-harmonisation predicates and
        # the five enum vocabularies (LegislativePhase/DraftType/DecisionType/
        # EUDocumentType/EUCourt, with their individuals) were defined ONLY in the
        # per-bucket *_schema.json files, which the combined builder does not merge
        # and the SHACL bucket collectors do not load — so the shipped graph left
        # 376 transposition assertions with no resolvable predicate and the five
        # enum NodeShapes validated zero nodes (false green). They are now folded
        # into controlled_vocabulary.jsonld (loaded into every bucket) with the
        # SAME declaration that still lives in the subcorpus *_schema.json — a
        # consistent shared TBox id, not a collision (mirrors the #562 pattern).
        "estleg:transposesDirective", "estleg:transposedBy", "estleg:transpositionStatus",
        "estleg:harmonisedWith", "estleg:harmonises", "estleg:sharedDirective",
        "estleg:memberStateCode", "estleg:nationalCelex",
        "estleg:LegislativePhase",
        "estleg:Phase_PublicConsultation", "estleg:Phase_Review", "estleg:Phase_Submission",
        "estleg:DraftType",
        "estleg:DraftType_Bill", "estleg:DraftType_AmendmentBill",
        "estleg:DraftType_GovernmentRegulation", "estleg:DraftType_MinisterialRegulation",
        "estleg:DraftType_GovernmentOrder", "estleg:DraftType_EUPosition",
        "estleg:DraftType_DraftIntent", "estleg:DraftType_Regulation",
        "estleg:DraftType_ActionPlan", "estleg:DraftType_Report",
        "estleg:DraftType_CitizenshipDecision", "estleg:DraftType_Other",
        "estleg:DecisionType",
        "estleg:DecisionType_Judgment", "estleg:DecisionType_Ruling",
        "estleg:DecisionType_Resolution", "estleg:DecisionType_OrderRuling",
        "estleg:EUDocumentType",
        "estleg:EUDocType_Regulation", "estleg:EUDocType_Directive", "estleg:EUDocType_Decision",
        "estleg:EUCourt",
        "estleg:EUCourt_CourtOfJustice", "estleg:EUCourt_GeneralCourt",
        "estleg:EUCourt_CivilServiceTribunal", "estleg:EUCourt_NationalCourt",
        "estleg:EUCourt_EFTACourt",
        # #566: structural classes used as @type in the corpus (combined + the law
        # peep that introduces them) but previously absent from the curated TBox;
        # now declared canonically in controlled_vocabulary.jsonld alongside the
        # existing inline declaration — a shared TBox class id, not a collision.
        "estleg:GeneralPartConcept", "estleg:ProcedureStage",
        "https://w3id.org/estleg/",
        "https://w3id.org/estleg/LegalPart",
        "https://w3id.org/estleg/Chapter",
        "https://w3id.org/estleg/Division",
        "https://w3id.org/estleg/Section",
        "https://w3id.org/estleg/LegalConcept",
    }
    # #433: subcorpus *_schema.json files are projections of the CV, so every
    # schema @id is a shared T-Box id rather than a semantic collision.
    from estleg.consolidate_tbox import schema_paths, schema_term_ids

    for schema_path in schema_paths().values():
        if schema_path.is_file():
            shared_class_ids.update(schema_term_ids(schema_path))
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


def parse_args(argv: list[str] | None = None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Comprehensive validation for Estonian Legal Ontology files."
    )
    parser.add_argument(
        "--krr-dir",
        type=Path,
        default=KRR_DIR,
        help="Directory holding the ontology corpus (default: krr_outputs/).",
    )
    parser.add_argument(
        "--allow-missing-index",
        action="store_true",
        help=(
            "Permit missing committed index/catalog artifacts "
            "(INDEX.json, metadata.jsonld, subcorpus *_INDEX.json) instead of "
            "failing. Use only for a deliberately not-yet-generated tree."
        ),
    )
    return parser.parse_args(argv)


def validate_legacy_deprecations(
    krr_dir: Path = KRR_DIR,
    decisions_path: Path | None = None,
):
    """Issue #426: every legacy duplicate listed in the deprecation decisions
    file must carry ``owl:deprecated: true`` + ``dcterms:isReplacedBy`` on its
    act root. ``generate_index()`` excludes those files by decisions-file
    lookup alone, so an unmarked root would drop out of the active index while
    still shipping undeprecated inside ``combined_ontology.jsonld``. Errors
    here mean ``scripts/deprecate_legacy_statutes.py`` must be (re)run.
    """
    print("\n--- Legacy Statute Deprecations (#426) ---")
    path = decisions_path if decisions_path is not None else LEGACY_STATUTE_DECISIONS_PATH
    checked, violations = verify_decisions_applied(path, krr_dir)
    if not checked and not violations:
        print("  OK: no deprecation decisions to enforce")
        return
    for msg in violations:
        error(f"deprecation decision not applied: {msg}")
    if not violations:
        print(
            f"  OK: {checked} deprecated legacy roots carry "
            f"owl:deprecated + dcterms:isReplacedBy"
        )


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    krr_dir: Path = args.krr_dir
    allow_missing_index: bool = args.allow_missing_index

    print("=" * 60)
    print("Estonian Legal Ontology - Validation")
    print("=" * 60)

    files = discover_validation_files(krr_dir)

    # Issue #316: a misconfigured `--krr-dir` or an unpopulated build env
    # yields zero discoverable corpus files. Without this guard the
    # validation loop runs over nothing, `errors` stays empty, and the
    # script exits 0 — a false green that hides a broken setup. Fail hard.
    if not files:
        error(
            f"No validatable corpus files found under {krr_dir} — refusing to "
            f"report success on an empty or misconfigured corpus."
        )
        print("\n" + "=" * 60)
        print("Files validated: 0")
        print(f"Errors: {len(errors)}")
        print(f"Warnings: {len(warnings)}")
        print("=" * 60)
        print("\nVALIDATION FAILED")
        sys.exit(1)

    print(f"\nValidating {len(files)} files...\n")

    all_ids: dict[str, list[str]] = defaultdict(list)
    internal_refs: list[tuple[str, str, str, str]] = []

    for filepath in files:
        doc = validate_json_syntax(filepath)
        if doc is None:
            continue

        validate_context(filepath, doc)
        validate_bare_namespace_act_ids(filepath, doc)
        validate_types(filepath, doc)
        validate_multi_valued(filepath, doc)
        validate_section_numbers(filepath, doc)
        validate_dc_source(filepath, doc)
        validate_source_provenance(filepath, doc)
        validate_act_xml_sameas(filepath, doc)
        validate_per_document_classes(filepath, doc)
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
    validate_canonical_tbox(krr_dir)
    validate_temporal_property_targets(files)
    validate_no_personal_codes(files)
    validate_transposition_mapping(krr_dir)
    validate_registry_index(krr_dir, allow_missing_index=allow_missing_index)
    validate_multipart_coverage(krr_dir, allow_missing_index=allow_missing_index)
    validate_index_body_coverage(krr_dir, allow_missing_index=allow_missing_index)
    validate_regulation_indexes(krr_dir)
    validate_act_coverage_reconciliation(krr_dir)
    validate_legacy_deprecations(krr_dir)
    validate_combined_ontology(krr_dir)
    validate_combined_graph_closure(krr_dir)
    validate_provision_version_monotonicity(krr_dir)
    validate_provision_version_encoding(krr_dir)
    validate_version_layer_freshness(krr_dir)
    validate_last_amendment_matches_versions(krr_dir)
    validate_regulation_version_coverage(krr_dir)
    validate_provision_text_quality(krr_dir)
    validate_harmonisation_symmetry(krr_dir)
    validate_subcorpus_combined_ontologies(krr_dir)
    validate_subcorpus_index_counts(krr_dir, allow_missing_index=allow_missing_index)
    # Regen-pending staleness guards (#366, #348, #384). These emit
    # warnings only (see each function's docstring) so they make stale
    # artifacts visible without failing the green build pre-regen.
    validate_tbox_inverse_parity()
    validate_eu_provenance_fields()
    validate_amendment_duplicate_bloat()
    validate_dupn_iri_baseline(krr_dir)
    validate_metadata_catalog(krr_dir, allow_missing_index=allow_missing_index)
    validate_institution_duplicates(krr_dir)
    validate_institution_registry_consistency(krr_dir)

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
