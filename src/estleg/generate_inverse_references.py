#!/usr/bin/env python3
"""
Generate estleg:referencedBy inverse links from existing estleg:references.

This script:
1. Scans all *_peep.json files for estleg:references properties
2. For each A estleg:references B, records that B estleg:referencedBy A
3. Groups inverse links by target file (which JSON-LD file contains node B)
4. Updates each target file, adding estleg:referencedBy arrays
5. Verifies symmetry: every references A->B has a matching referencedBy B<-A
6. Generates inverse_references_report.json with statistics

Idempotent: clears any existing estleg:referencedBy before regenerating.

Handles IRI alias resolution for mismatched prefixes (e.g.
"Vlaigusseadus_Par_14" -> "VOS_Par_14") so that forward references
authored with an alternate prefix still get their inverse links.
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from estleg.estleg_common import BUILD_EVALUATION_DATE, iter_peep_files, save_json
from estleg.kov_pipeline_coverage import (
    PINNED_RUN_TIMESTAMP,
    CoverageReport,
    measure_runtime,
    resolve_pipeline_version,
    write_coverage_report,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
CROSS_REFERENCES_REPORT = "cross_references_report.json"
# ProvisionVersion sidecars (estleg:versionOf source). These live OUTSIDE
# iter_peep_files() (they are *.jsonld, not *_peep.json), so the version
# inverse pass scans them explicitly. See materialize_has_version().
VERSIONS_DIR = KRR_DIR / "provision_versions"

# #606: estleg:hasVersion (inverse of estleg:versionOf) is materialised onto a
# peep node only when its provision IRI resolves to a peep file. When the
# ProvisionVersion sidecars carry estleg:versionOf @ids that predate the #345
# IRI scheme, almost every target fails to resolve and hasVersion silently
# lands on a tiny fraction of its resolvable provisions (the observed
# 13-of-38,512 symptom). main() compares the resolved fraction against this
# conservative floor and, when it falls below, emits a loud stderr warning
# (a visibility gate, not a hard failure). A healthy run resolves ~100% of
# provisions, so anything below half signals systemic IRI-scheme drift rather
# than incidental dangling sidecars. Re-materialisation is a DEFERRED DATA
# regen: regenerate the sidecars under the #345 IRI scheme, then re-run.
HASVERSION_MIN_RESOLVED_FRACTION = 0.5

NS = "https://w3id.org/estleg/"

# Forward → inverse pairs the combined builder materialises (#520).
# Peep-side passes below still special-case referencedBy / implementedBy /
# hasVersion; this table is the single extension point for new pairs.
INVERSE_PAIRS: tuple[tuple[str, str], ...] = (
    ("estleg:references", "estleg:referencedBy"),
    ("estleg:issuedUnder", "estleg:implementedBy"),
    ("estleg:interpretsLaw", "estleg:interpretedBy"),
    ("estleg:versionOf", "estleg:hasVersion"),
    ("estleg:transposesDirective", "estleg:transposedBy"),
    ("estleg:harmonisedWith", "estleg:harmonises"),
    ("estleg:competentAuthority", "estleg:governs"),
    ("estleg:parentProvision", "estleg:hasSubsection"),
)

# Version nodes live on the separate provision_versions/ surface (#561).
# Combined must not emit either direction — they would dangle.
COMBINED_SKIP_INVERSE_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {("estleg:versionOf", "estleg:hasVersion")}
)


# ---------------------------------------------------------------------------
# IRI prefix aliases
#
# Some forward references were written with an alternative prefix that does
# not match any existing node @id.  Map each known alias to the list of
# canonical prefixes that should be searched (in priority order).
#
# Example: "Vlaigusseadus" (a transliteration of Võlaõigusseadus) is used
# in some references but the actual node IRIs use VOS / VOS3 / VOS_O4 / VOS7.
# ---------------------------------------------------------------------------
IRI_PREFIX_ALIASES: dict[str, list[str]] = {
    "Vlaigusseadus": ["VOS", "VOS3", "VOS_O4", "VOS7"],
}


def _build_prefix_par_index(
    iri_to_file: dict[str, Path],
) -> dict[str, dict[str, str]]:
    """
    Build {prefix: {par_number: full_iri}} from the iri_to_file map.

    Only considers IRIs of the form ``estleg:{Prefix}_Par_{Number}``.
    """
    index: dict[str, dict[str, str]] = defaultdict(dict)
    for iri in iri_to_file:
        if not iri.startswith("estleg:") or "_Par_" not in iri:
            continue
        local = iri[len("estleg:"):]
        prefix, par = local.split("_Par_", 1)
        index[prefix][par] = iri
    return dict(index)


class AliasResolution:
    """Result of an alias-resolution attempt.

    ``canonical`` carries the resolved IRI when alias resolution
    produced exactly one canonical match; otherwise ``None``.

    ``ambiguous_candidates`` is non-empty only when the alias matched
    >1 canonical prefix — i.e. two or more canonical laws contain the
    requested paragraph. In that case we refuse to pick one to avoid
    silently corrupting back-links across acts (#157).
    """

    __slots__ = ("ambiguous_candidates", "canonical")

    def __init__(
        self,
        canonical: str | None,
        ambiguous_candidates: list[str] | None = None,
    ) -> None:
        self.canonical = canonical
        self.ambiguous_candidates = ambiguous_candidates or []


def _resolve_alias(
    target_iri: str,
    iri_to_file: dict[str, Path],
    prefix_par_index: dict[str, dict[str, str]],
) -> AliasResolution:
    """
    Try to resolve *target_iri* via IRI_PREFIX_ALIASES.

    Issue #157: ``IRI_PREFIX_ALIASES[<alias>]`` lists ALL canonical
    prefixes whose acts can plausibly be the alias's referent. If
    more than one of those canonical prefixes contains the requested
    paragraph number, picking the first match silently mis-attributes
    the back-link to a sibling osa. Refuse to resolve in that case
    and let the caller record an unresolved warning instead.

    Returns an :class:`AliasResolution` describing what we found:
      * ``canonical`` set on a single confident match;
      * ``ambiguous_candidates`` populated when multiple prefixes
        carry the same paragraph number (caller must NOT use it);
      * both empty when the alias is unknown or no canonical prefix
        holds the paragraph (caller treats it as unresolved).
    """
    if not target_iri.startswith("estleg:") or "_Par_" not in target_iri:
        return AliasResolution(None)

    local = target_iri[len("estleg:"):]
    alias_prefix, par_num = local.split("_Par_", 1)

    canonical_prefixes = IRI_PREFIX_ALIASES.get(alias_prefix)
    if canonical_prefixes is None:
        return AliasResolution(None)

    matches: list[str] = []
    for canon in canonical_prefixes:
        pars = prefix_par_index.get(canon, {})
        if par_num in pars:
            resolved = pars[par_num]
            if resolved in iri_to_file:
                matches.append(resolved)

    if not matches:
        return AliasResolution(None)
    if len(matches) > 1:
        return AliasResolution(None, ambiguous_candidates=matches)
    return AliasResolution(matches[0])


def collect_all_references() -> tuple[
    dict[str, list[str]],
    dict[str, Path],
    dict[str, dict[str, str]],
]:
    """
    Scan all law JSON-LD files and collect forward references.

    Returns:
      - inverse_map: {target_iri: [source_iri, ...]} for estleg:referencedBy
      - iri_to_file: {iri: filepath} mapping each provision IRI to its file
      - prefix_par_index: {prefix: {par_number: iri}} for alias resolution
    """
    inverse_map: dict[str, list[str]] = defaultdict(list)
    iri_to_file: dict[str, Path] = {}

    # Collect from main krr_outputs directory. KOV inclusion is now the
    # baseline — Layer 2b body-text references in KOV peep files must be
    # collected so they gain referencedBy on their targets (Round-3 fix #7).
    all_files = iter_peep_files()
    # Also include riigikohus files (though they typically don't have references)
    rk_dir = KRR_DIR / "riigikohus"
    if rk_dir.exists():
        all_files.extend(sorted(rk_dir.glob("*_peep.json")))

    for json_file in all_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if not node_id:
                continue

            # Register this IRI's file location. Three node-id shapes
            # carry references in the corpus:
            #   1. <prefix>_Par_<n>      provisions in laws / regulations
            #   2. RK_*                  riigikohus decisions
            #   3. owl:Ontology-typed    act-level IRIs (laws, state regs,
            #                            KOV regs). Layer 2b body-text refs
            #                            resolve to these directly, so they
            #                            must be indexed too — otherwise
            #                            referencedBy on KOV act targets
            #                            silently drops.
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            is_act_level = any(t in {"owl:Ontology", "estleg:Act",
                                       "estleg:Law",
                                       "estleg:NationalRegulation",
                                       "estleg:GovernmentRegulation",
                                       "estleg:MinisterialRegulation",
                                       "estleg:MunicipalRegulation"}
                                for t in types)
            if "_Par_" in node_id or "RK_" in node_id or is_act_level:
                iri_to_file[node_id] = json_file

            # Collect forward references
            refs = node.get("estleg:references")
            if refs is None:
                continue

            # Normalize to list
            if isinstance(refs, dict):
                ref_list = [refs]
            elif isinstance(refs, list):
                ref_list = refs
            else:
                continue

            for ref in ref_list:
                if isinstance(ref, dict) and "@id" in ref:
                    target_iri = ref["@id"]
                    if target_iri != node_id:  # skip self-refs
                        inverse_map[target_iri].append(node_id)

    # Build prefix-based paragraph index for alias resolution
    prefix_par_index = _build_prefix_par_index(iri_to_file)

    return dict(inverse_map), iri_to_file, prefix_par_index


def apply_inverse_references(
    inverse_map: dict[str, list[str]],
    iri_to_file: dict[str, Path],
    prefix_par_index: dict[str, dict[str, str]],
) -> tuple[dict[Path, int], list[str], dict[str, str], dict[str, list[str]]]:
    """
    Apply estleg:referencedBy to target files.

    Uses alias resolution for target IRIs that cannot be found directly.

    Returns:
      - update_counts: {target_filepath: count_of_nodes_updated}
        (Path-keyed so nested-directory targets — e.g. KOV peeps under
        regulations/kov/<municipality>/ — never collide on basename;
        also matches apply_implemented_by's signature so the coverage
        accumulator can resolve target paths losslessly)
      - unresolved_iris: list of target IRIs that could not be resolved
      - alias_resolved: {original_iri: canonical_iri} for IRIs fixed via alias
      - alias_ambiguous: {alias_iri: [candidate_canonical_iri, ...]} for
        IRIs that matched multiple canonical prefixes (#157). These
        are recorded for diagnostics but never written as back-links —
        attributing them to one canonical would risk silent corruption.
    """
    # Group inverse references by target file
    file_updates: dict[Path, dict[str, list[str]]] = defaultdict(dict)

    unresolved_iris: list[str] = []
    alias_resolved: dict[str, str] = {}
    alias_ambiguous: dict[str, list[str]] = {}

    for target_iri, source_iris in inverse_map.items():
        target_file = iri_to_file.get(target_iri)

        # If direct lookup fails, try alias resolution
        if target_file is None:
            res = _resolve_alias(target_iri, iri_to_file, prefix_par_index)
            if res.canonical is not None:
                target_file = iri_to_file.get(res.canonical)
                alias_resolved[target_iri] = res.canonical
                # Re-key: merge sources under the canonical IRI
                target_iri = res.canonical
            elif res.ambiguous_candidates:
                # Refuse to silently pick one canonical (#157).
                alias_ambiguous[target_iri] = sorted(res.ambiguous_candidates)
                unresolved_iris.append(target_iri)
                continue

        if target_file is None:
            unresolved_iris.append(target_iri)
            continue

        # Deduplicate source IRIs
        existing = file_updates[target_file].get(target_iri, [])
        merged = existing + source_iris
        unique_sources = list(dict.fromkeys(merged))
        file_updates[target_file][target_iri] = unique_sources

    if unresolved_iris:
        print(f"  Warning: {len(unresolved_iris)} target IRIs could not be resolved to files")
        for iri in sorted(unresolved_iris)[:10]:
            print(f"    {iri}")
        if len(unresolved_iris) > 10:
            print(f"    ... and {len(unresolved_iris) - 10} more")
    if alias_resolved:
        print(f"  Resolved {len(alias_resolved)} IRIs via prefix aliases")
    if alias_ambiguous:
        print(
            f"  Refused {len(alias_ambiguous)} ambiguous alias resolutions "
            "(multiple canonical prefixes carry the same paragraph)"
        )
        for iri in sorted(alias_ambiguous)[:5]:
            print(f"    {iri} -> {alias_ambiguous[iri]}")

    update_counts: dict[Path, int] = {}

    for target_file, iri_sources in sorted(file_updates.items()):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Error reading {target_file.name}: {e}")
            continue

        modified = False
        nodes_updated = 0

        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if node_id not in iri_sources:
                # Clear any stale referencedBy (idempotent)
                if "estleg:referencedBy" in node:
                    del node["estleg:referencedBy"]
                    modified = True
                continue

            sources = iri_sources[node_id]
            ref_iris = [{"@id": s} for s in sources]

            node["estleg:referencedBy"] = ref_iris

            nodes_updated += 1
            modified = True

        if modified:
            save_json(target_file, doc)
            update_counts[target_file] = nodes_updated

    return update_counts, unresolved_iris, alias_resolved, alias_ambiguous


# ---------------------------------------------------------------------------
# Layer 2b: implementedBy filtered projection
#
# Distinct from estleg:referencedBy (body-text refs). estleg:implementedBy
# is drawn ONLY from preamble citations:
#   1. estleg:issuedUnder         — act node's direct enabling-act link
#   2. estleg:implementsCitation  — reified Citation whose
#                                   estleg:citationTarget gives the target
# Body-text estleg:references are explicitly NOT consulted here.
# ---------------------------------------------------------------------------


def collect_preamble_back_references(
    json_files: list[Path],
) -> dict[str, list[str]]:
    """Collect inverse links derived ONLY from preamble citations.

    Two sources:
      1. estleg:issuedUnder  → enabling_act_iri receives the source act
      2. estleg:implementsCitation → reified Citation node's
                                       estleg:citationTarget receives
                                       the source act

    Body-text references (estleg:references) are explicitly NOT
    consulted. They feed estleg:referencedBy via the existing
    inverse pass.
    """
    inverse: dict[str, list[str]] = defaultdict(list)
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        graph = doc.get("@graph", [])
        # Build a quick map of citation_iri → target_iri inside this file
        citation_target: dict[str, str] = {}
        for n in graph:
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:Citation" in types:
                cid = n.get("@id")
                tgt = n.get("estleg:citationTarget")
                if isinstance(tgt, dict):
                    tgt = tgt.get("@id")
                if cid and tgt:
                    citation_target[cid] = tgt

        for n in graph:
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if not any(t in {"owl:Ontology", "estleg:Act"} for t in types):
                continue
            source_act_iri = n.get("@id")
            if not source_act_iri:
                continue

            # issuedUnder targets
            iu = n.get("estleg:issuedUnder", [])
            if isinstance(iu, dict):
                iu = [iu]
            for ref in iu:
                if isinstance(ref, dict) and "@id" in ref:
                    inverse[ref["@id"]].append(source_act_iri)

            # implementsCitation → resolve to citationTarget
            ic = n.get("estleg:implementsCitation", [])
            if isinstance(ic, dict):
                ic = [ic]
            for ref in ic:
                if isinstance(ref, dict) and "@id" in ref:
                    target = citation_target.get(ref["@id"])
                    if target:
                        inverse[target].append(source_act_iri)
    # Dedupe per target
    return {k: sorted(set(v)) for k, v in inverse.items()}


def clear_stale_implemented_by(json_files: list[Path]) -> int:
    """Idempotency pass: scan every peep file once and remove any
    pre-existing estleg:implementedBy / estleg:implementedByCount
    triples. Returns the number of files modified.

    Required because apply_implemented_by writes ONLY to nodes still
    present in the current preamble_inverse map. Without this clear
    pass, a stale target whose source act lost its issuedUnder triple
    (e.g. preamble parser improvement, or act removal) would retain
    a now-incorrect implementedBy across re-runs.
    """
    n_modified = 0
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        modified = False
        for node in doc.get("@graph", []):
            if "estleg:implementedBy" in node:
                node.pop("estleg:implementedBy")
                modified = True
            if "estleg:implementedByCount" in node:
                node.pop("estleg:implementedByCount")
                modified = True
        if modified:
            save_json(json_file, doc)
            n_modified += 1
    return n_modified


def apply_implemented_by(
    inverse: dict[str, list[str]],
    iri_to_file: dict[str, Path],
) -> dict[Path, int]:
    """Write estleg:implementedBy + estleg:implementedByCount onto
    target nodes. Returns {target_filepath: count_of_nodes_updated}.

    NOTE: callers MUST run clear_stale_implemented_by() over the same
    file set BEFORE this function. Otherwise stale targets that no
    longer appear in `inverse` retain old triples.

    The Path-keyed return matches apply_inverse_references' new
    signature so the coverage accumulator (Task 11) can resolve the
    target file path losslessly even when peeps live in nested
    regulations/kov/<municipality>/ directories with colliding
    basenames.
    """
    file_updates: dict[Path, dict[str, list[str]]] = defaultdict(dict)
    for target_iri, sources in inverse.items():
        target_file = iri_to_file.get(target_iri)
        if target_file is None:
            continue
        file_updates[target_file][target_iri] = sources

    counts: dict[Path, int] = {}
    for target_file, by_iri in sorted(file_updates.items()):
        try:
            with open(target_file, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        nodes_updated = 0
        for node in doc.get("@graph", []):
            nid = node.get("@id")
            if not nid or nid not in by_iri:
                continue
            sources = by_iri[nid]
            node["estleg:implementedBy"] = [{"@id": s} for s in sources]
            # JSON-LD 1.1 expands a plain integer to xsd:integer per
            # spec, with no @context coercion needed. We deliberately
            # use a plain int here (not {"@type": "xsd:integer",
            # "@value": ...}) to match the project's existing literal
            # convention; SPARQL queries can still filter via
            # xsd:integer. If a future SHACL constraint requires the
            # expanded form, refactor to typed-literal at that point.
            node["estleg:implementedByCount"] = len(sources)
            nodes_updated += 1
        if nodes_updated:
            save_json(target_file, doc)
            counts[target_file] = nodes_updated
    return counts


def compute_implemented_by_count(
    inverse: dict[str, list[str]],
) -> dict[str, int]:
    """Aggregate implementedByCount per target IRI."""
    return {target: len(set(sources)) for target, sources in inverse.items()}


# ---------------------------------------------------------------------------
# estleg:hasVersion — inverse of estleg:versionOf  (#345)
#
# Every estleg:ProvisionVersion node carries estleg:versionOf -> <provision>,
# but the documented inverse estleg:hasVersion (T-Box: owl:inverseOf
# estleg:versionOf) was never materialised on the LegalProvision side. The
# corpus materialises every OTHER inverse pair bidirectionally (references/
# referencedBy, hasSubsection/parentProvision, interpretsLaw/interpretedBy,
# transposesDirective/transposedBy); this pass closes the version pair the
# same way the referencedBy pass closes the reference pair.
#
# Source of versionOf edges: the ProvisionVersion sidecars under
# krr_outputs/provision_versions/<slug>.jsonld. These are *.jsonld files,
# NOT *_peep.json, so iter_peep_files() does not return them — we scan
# VERSIONS_DIR explicitly. The target LegalProvision nodes, however, live
# in the peep corpus and are already indexed by collect_all_references()'s
# iri_to_file map, so hasVersion lands on the canonical provision node.
# ---------------------------------------------------------------------------


def collect_version_inverse(
    versions_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Scan ProvisionVersion sidecars and build the version inverse map.

    Reads every ``*.jsonld`` under ``versions_dir`` (default
    :data:`VERSIONS_DIR`) and, for each node carrying
    ``estleg:versionOf -> <provision_iri>``, records that the provision
    ``estleg:hasVersion`` that version node.

    Returns ``{provision_iri: [version_iri, ...]}`` with version IRIs
    deduplicated and in first-seen order. Sidecar @id ordering already
    follows redaction chronology (versions are emitted oldest-first), so
    the resulting hasVersion list mirrors the provision's text history.

    A ProvisionVersion may legitimately appear in only one sidecar, but we
    dedupe defensively so a version IRI is never listed twice on a
    provision even if a sidecar were regenerated/merged with overlap.
    """
    if versions_dir is None:
        versions_dir = VERSIONS_DIR

    inverse: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)

    if not versions_dir.exists():
        return {}

    for sidecar in sorted(versions_dir.glob("*.jsonld")):
        try:
            with open(sidecar, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        for node in doc.get("@graph", []):
            version_iri = node.get("@id")
            if not isinstance(version_iri, str) or not version_iri:
                continue
            vof = node.get("estleg:versionOf")
            # versionOf is authored as a single {"@id": ...} object, but
            # tolerate a list form defensively.
            if isinstance(vof, dict):
                targets = [vof]
            elif isinstance(vof, list):
                targets = vof
            else:
                continue
            for tgt in targets:
                if not isinstance(tgt, dict):
                    continue
                provision_iri = tgt.get("@id")
                if not isinstance(provision_iri, str) or not provision_iri:
                    continue
                if provision_iri == version_iri:  # never self-link
                    continue
                if version_iri in seen[provision_iri]:
                    continue
                seen[provision_iri].add(version_iri)
                inverse[provision_iri].append(version_iri)

    return dict(inverse)


def clear_existing_has_version() -> int:
    """Remove all existing estleg:hasVersion from peep files (idempotent).

    Mirrors :func:`clear_existing_inverse_refs`. hasVersion is materialised
    onto LegalProvision nodes that live in the peep corpus, so the clear
    pass walks the same ``iter_peep_files()`` set (plus riigikohus, for
    parity — those nodes never carry hasVersion, but scanning them keeps
    the clear/apply file sets identical and the pass fully idempotent).

    Returns the count of files cleaned.
    """
    cleaned = 0
    all_files = iter_peep_files()
    rk_dir = KRR_DIR / "riigikohus"
    if rk_dir.exists():
        all_files.extend(sorted(rk_dir.glob("*_peep.json")))

    for json_file in all_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        modified = False
        for node in doc.get("@graph", []):
            if "estleg:hasVersion" in node:
                del node["estleg:hasVersion"]
                modified = True

        if modified:
            save_json(json_file, doc)
            cleaned += 1

    return cleaned


def apply_has_version(
    version_inverse: dict[str, list[str]],
    iri_to_file: dict[str, Path],
) -> tuple[dict[Path, int], list[str]]:
    """Write estleg:hasVersion onto LegalProvision nodes in their peep files.

    Mirrors :func:`apply_inverse_references` (the referencedBy pass):
    groups by target file, writes one ``estleg:hasVersion`` array per
    provision node, and returns Path-keyed update counts so nested KOV
    targets never collide on basename.

    ``iri_to_file`` is the provision-IRI -> peep-file map built by
    :func:`collect_all_references`. Provision IRIs not present in that map
    (the sidecar references a provision with no peep node) are returned as
    ``unresolved`` rather than silently dropped, so the report can surface
    them.

    Returns ``(update_counts, unresolved_provision_iris)``.
    """
    file_updates: dict[Path, dict[str, list[str]]] = defaultdict(dict)
    unresolved: list[str] = []

    for provision_iri, version_iris in version_inverse.items():
        target_file = iri_to_file.get(provision_iri)
        if target_file is None:
            unresolved.append(provision_iri)
            continue
        # Dedupe defensively while preserving order.
        existing = file_updates[target_file].get(provision_iri, [])
        merged = existing + version_iris
        file_updates[target_file][provision_iri] = list(dict.fromkeys(merged))

    if unresolved:
        print(
            f"  Warning: {len(unresolved)} provision IRIs from version "
            "sidecars could not be resolved to peep files"
        )
        for iri in sorted(unresolved)[:10]:
            print(f"    {iri}")
        if len(unresolved) > 10:
            print(f"    ... and {len(unresolved) - 10} more")

    update_counts: dict[Path, int] = {}
    for target_file, prov_versions in sorted(file_updates.items()):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Error reading {target_file.name}: {e}")
            continue

        nodes_updated = 0
        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if node_id not in prov_versions:
                continue
            version_iris = prov_versions[node_id]
            node["estleg:hasVersion"] = [{"@id": v} for v in version_iris]
            nodes_updated += 1

        if nodes_updated:
            save_json(target_file, doc)
            update_counts[target_file] = nodes_updated

    return update_counts, unresolved


def hasversion_resolution_degraded(
    version_inverse: dict[str, list[str]],
    unresolved: list[str],
    *,
    min_resolved_fraction: float = HASVERSION_MIN_RESOLVED_FRACTION,
) -> bool:
    """Return True when estleg:hasVersion materialisation is degraded (#606).

    ``version_inverse`` is the provision-IRI -> [version-IRI] map produced by
    :func:`collect_version_inverse`; ``unresolved`` is the list of provision
    IRIs that :func:`apply_has_version` could not resolve to a peep node.

    The *resolved fraction* is::

        (len(version_inverse) - len(unresolved)) / len(version_inverse)

    Degradation means the map is non-trivially populated yet that fraction
    falls below ``min_resolved_fraction`` — the signature of stale
    ProvisionVersion sidecars whose ``estleg:versionOf`` @ids predate the #345
    IRI scheme, so almost every target lands in ``unresolved`` and hasVersion
    materialises on only a handful of provisions.

    An empty ``version_inverse`` is never degraded: there is no signal to act
    on, and the guard also avoids a division by zero.
    """
    total = len(version_inverse)
    if total == 0:
        return False
    resolved = total - len(unresolved)
    return (resolved / total) < min_resolved_fraction


def clear_existing_inverse_refs() -> int:
    """
    Remove all existing estleg:referencedBy from law files for idempotent re-run.

    Returns count of files cleaned.
    """
    cleaned = 0
    # KOV inclusion is now the baseline — KOV targets that received
    # referencedBy in a previous run must be cleared too so this pass
    # stays idempotent (Layer 2b).
    all_files = iter_peep_files()
    rk_dir = KRR_DIR / "riigikohus"
    if rk_dir.exists():
        all_files.extend(sorted(rk_dir.glob("*_peep.json")))

    for json_file in all_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        modified = False
        for node in doc.get("@graph", []):
            if "estleg:referencedBy" in node:
                del node["estleg:referencedBy"]
                modified = True

        if modified:
            save_json(json_file, doc)
            cleaned += 1

    return cleaned


def verify_symmetry() -> list[dict]:
    """
    Post-verification: for every ``estleg:references`` A->B, verify that
    node B carries ``estleg:referencedBy`` pointing back to A.

    Issue #157: when the forward reference uses an alias prefix (e.g.
    ``Vlaigusseadus_Par_14``), apply the same alias-resolution logic
    used by ``apply_inverse_references`` before declaring a mismatch.
    Otherwise every aliased forward ref shows up as a phantom missing
    back-link even though the canonical IRI carries the inverse.

    Mismatch categories:
      * ``target node does not exist in any file`` — pure dangling.
      * ``unresolved-after-alias`` — alias unknown OR ambiguous.
      * ``genuine-missing-back-link`` — forward ref resolved cleanly to
        a canonical IRI yet the canonical target lacks the back-link.

    Returns a list of mismatch dicts:
      {"source": A, "target": B, "file": filename, "issue": description}
    """
    # Pass 1 – collect all referencedBy + iri-to-file map for quick
    # lookup. We rebuild the prefix index here too so verify_symmetry
    # can resolve aliases the same way apply_inverse_references does.
    referenced_by: dict[str, set[str]] = defaultdict(set)
    iri_to_file: dict[str, Path] = {}

    # KOV inclusion is now the baseline — KOV targets carrying
    # referencedBy must be reachable for symmetry verification (Layer 2b).
    all_files = iter_peep_files()
    rk_dir = KRR_DIR / "riigikohus"
    if rk_dir.exists():
        all_files.extend(sorted(rk_dir.glob("*_peep.json")))

    iri_exists: set[str] = set()

    for json_file in all_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for node in doc.get("@graph", []):
            node_id = node.get("@id", "")
            if node_id:
                iri_exists.add(node_id)
                iri_to_file[node_id] = json_file
            inv = node.get("estleg:referencedBy")
            if inv is None:
                continue
            if isinstance(inv, dict):
                inv = [inv]
            for entry in inv:
                if isinstance(entry, dict) and "@id" in entry:
                    referenced_by[node_id].add(entry["@id"])

    prefix_par_index = _build_prefix_par_index(iri_to_file)

    # Pass 2 – check every forward reference. When the target IRI does
    # not exist directly, run alias resolution before deciding the
    # category; mirrors apply_inverse_references' behaviour so the
    # symmetry view doesn't double-count alias gaps.
    mismatches: list[dict] = []
    for json_file in all_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for node in doc.get("@graph", []):
            source_id = node.get("@id", "")
            refs = node.get("estleg:references")
            if refs is None:
                continue
            if isinstance(refs, dict):
                refs = [refs]
            for ref in refs:
                if not isinstance(ref, dict) or "@id" not in ref:
                    continue
                target_id = ref["@id"]
                if target_id == source_id:
                    continue

                resolved_target: str | None = None
                if target_id in iri_exists:
                    resolved_target = target_id
                else:
                    res = _resolve_alias(target_id, iri_to_file, prefix_par_index)
                    if res.canonical is not None:
                        resolved_target = res.canonical
                    elif res.ambiguous_candidates:
                        mismatches.append({
                            "source": source_id,
                            "target": target_id,
                            "file": json_file.name,
                            "issue": "unresolved-after-alias",
                            "candidates": res.ambiguous_candidates,
                        })
                        continue

                if resolved_target is None:
                    # If the prefix WAS in IRI_PREFIX_ALIASES but no
                    # canonical contained the paragraph, the gap is
                    # alias-shaped; otherwise it's a pure dangling ref.
                    local = (
                        target_id.removeprefix("estleg:")
                    )
                    alias_prefix = (
                        local.split("_Par_", 1)[0]
                        if "_Par_" in local
                        else None
                    )
                    if alias_prefix and alias_prefix in IRI_PREFIX_ALIASES:
                        mismatches.append({
                            "source": source_id,
                            "target": target_id,
                            "file": json_file.name,
                            "issue": "unresolved-after-alias",
                        })
                    else:
                        mismatches.append({
                            "source": source_id,
                            "target": target_id,
                            "file": json_file.name,
                            "issue": "target node does not exist in any file",
                        })
                    continue

                # Resolved. Check for the back-link.
                if source_id not in referenced_by.get(resolved_target, set()):
                    mismatches.append({
                        "source": source_id,
                        "target": target_id,
                        "resolvedTarget": resolved_target,
                        "file": json_file.name,
                        "issue": "genuine-missing-back-link",
                    })

    return mismatches


def require_cross_reference_report(*, krr_dir: Path | None = None) -> None:
    """Fail fast when extract_cross_references has not been run (#470).

    ``generate_inverse_references`` reads ``estleg:references`` off peeps.
    Those triples are written by ``extract_cross_references.py``. A
    standalone run against a corpus that still has INDEX.json (the
    production tree) but no ``cross_references_report.json`` used to
    silently emit an empty inverse graph. Unit tests monkeypatch
    ``KRR_DIR`` to a tmp tree without INDEX — they are not gated.
    """
    krr_dir = krr_dir if krr_dir is not None else KRR_DIR
    report_path = krr_dir / "reports" / CROSS_REFERENCES_REPORT
    if report_path.is_file():
        try:
            doc = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"FATAL: unreadable {report_path.name}: {exc}"
            ) from exc
        found = 0
        if isinstance(doc, dict):
            summary = doc.get("summary") or {}
            if isinstance(summary, dict):
                found = int(summary.get("total_citations_found") or 0)
        if found <= 0:
            raise SystemExit(
                "FATAL: cross_references_report.json has zero citations — "
                "run extract_cross_references.py before "
                "generate_inverse_references.py (#470)"
            )
        return
    if (krr_dir / "INDEX.json").is_file():
        raise SystemExit(
            "FATAL: missing krr_outputs/reports/cross_references_report.json — "
            "run extract_cross_references.py before "
            "generate_inverse_references.py (#470)"
        )


_ACT_ROOT_AGGREGATE_PROPS: tuple[str, ...] = (
    "estleg:references",
    "estleg:referencedBy",
    "estleg:interpretedBy",
    "estleg:competentAuthority",
)
_ACT_ROOT_TYPES = frozenset(
    {
        "owl:Ontology",
        "estleg:Act",
        "estleg:Law",
        "estleg:NationalRegulation",
        "estleg:GovernmentRegulation",
        "estleg:MinisterialRegulation",
        "estleg:MunicipalRegulation",
    }
)


def _iri_values(value: object) -> list[str]:
    """Flatten a JSON-LD IRI slot (string, ``{@id}``, or list) to id strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        iri = value.get("@id")
        return [iri] if isinstance(iri, str) and iri else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_iri_values(item))
        return out
    return []


def materialize_act_root_aggregates(json_files: list[Path]) -> int:
    """Union provision-level citation/authority edges onto each act root (#508).

    Law-level tools otherwise have to scan every ``_Par_`` node (and fragment
    across multipart osa files). After this pass the act-root node carries
    the de-duplicated union of ``references`` / ``referencedBy`` /
    ``interpretedBy`` / ``competentAuthority`` from provisions whose
    ``estleg:partOfAct`` points at it. Existing act-root values are kept
    and merged. Returns the number of files written.
    """
    updated = 0
    for path in json_files:
        try:
            with open(path, encoding="utf-8") as handle:
                doc = json.load(handle)
        except (json.JSONDecodeError, OSError):
            continue
        graph = doc.get("@graph")
        if not isinstance(graph, list):
            continue
        act_nodes: list[dict] = []
        by_act: dict[str, dict[str, list[str]]] = {}
        for node in graph:
            if not isinstance(node, dict):
                continue
            types = node.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if any(t in _ACT_ROOT_TYPES for t in types):
                act_nodes.append(node)
            part = node.get("estleg:partOfAct")
            act_id = None
            if isinstance(part, dict):
                act_id = part.get("@id")
            elif isinstance(part, str):
                act_id = part
            if not isinstance(act_id, str) or not act_id:
                continue
            bucket = by_act.setdefault(act_id, {p: [] for p in _ACT_ROOT_AGGREGATE_PROPS})
            for prop in _ACT_ROOT_AGGREGATE_PROPS:
                bucket[prop].extend(_iri_values(node.get(prop)))
        changed = False
        for act in act_nodes:
            act_id = act.get("@id")
            if not isinstance(act_id, str) or act_id not in by_act:
                continue
            for prop in _ACT_ROOT_AGGREGATE_PROPS:
                merged: list[str] = []
                seen: set[str] = set()
                for iri in _iri_values(act.get(prop)) + by_act[act_id][prop]:
                    if iri not in seen:
                        seen.add(iri)
                        merged.append(iri)
                if not merged:
                    continue
                new_val = [{"@id": iri} for iri in merged]
                if act.get(prop) != new_val:
                    act[prop] = new_val
                    changed = True
        if changed:
            save_json(path, doc)
            updated += 1
    return updated


def main() -> int:
    print("=" * 70)
    print("Estonian Legal Ontology - Generate Inverse References (referencedBy)")
    print("=" * 70)
    require_cross_reference_report()

    # ---------- coverage instrumentation ----------
    _start = time.perf_counter()
    _files_processed: set[Path] = set()
    _files_processed_kov: set[Path] = set()
    _files_with_output: set[Path] = set()
    _files_with_output_kov: set[Path] = set()
    _triples = 0
    _triples_kov = 0
    _failures: list[str] = []

    # Layer 2a parity: every scanned file counts as processed.
    # collect_all_references() opened all of these; mark them all,
    # regardless of whether they ended up emitting an inverse triple.
    for path in iter_peep_files():
        _files_processed.add(path)
        if "regulations/kov/" in str(path):
            _files_processed_kov.add(path)

    # Step 1: Clear existing inverse references for idempotent re-run
    print("\n[1/6] Clearing existing estleg:referencedBy properties...")
    cleaned = clear_existing_inverse_refs()
    print(f"  Cleaned {cleaned} files")

    # Step 2: Collect all forward references
    print("\n[2/6] Collecting estleg:references from all files...")
    inverse_map, iri_to_file, prefix_par_index = collect_all_references()
    total_inverse_links = sum(len(v) for v in inverse_map.values())
    print(f"  Found {len(inverse_map)} target IRIs with {total_inverse_links} inverse links")
    print(f"  IRI index: {len(iri_to_file)} provision/decision IRIs mapped to files")
    print(f"  Prefix index: {len(prefix_par_index)} prefixes")
    print(f"  Alias rules: {len(IRI_PREFIX_ALIASES)} alias prefixes configured")

    # Step 3: Apply inverse references (with alias resolution)
    print("\n[3/6] Applying estleg:referencedBy to target files...")
    update_counts, unresolved_iris, alias_resolved, alias_ambiguous = apply_inverse_references(
        inverse_map, iri_to_file, prefix_par_index,
    )
    total_nodes_updated = sum(update_counts.values())
    print(f"\nUpdated {sum(update_counts.values())} nodes across "
          f"{len(update_counts)} files")

    print("\n[3b] Materialising act-root aggregates (#508)...")
    act_root_files = materialize_act_root_aggregates(list(iter_peep_files()))
    print(f"  Act-root aggregates written to {act_root_files} files")

    # Target-side attribution: each (target_path, count) pair is one
    # peep file that received >=1 referencedBy triple in this pass.
    for target_path, n in update_counts.items():
        _files_with_output.add(target_path)
        if "regulations/kov/" in str(target_path):
            _files_with_output_kov.add(target_path)
        _triples += n
        if "regulations/kov/" in str(target_path):
            _triples_kov += n

    print("\nLayer 2b: collecting preamble back-references...")
    files = list(iter_peep_files())
    preamble_inverse = collect_preamble_back_references(files)

    # Idempotent clear before re-applying. Strips any stale
    # implementedBy / implementedByCount triples from prior runs so
    # corrected preamble parses and removed acts can't leave behind
    # now-incorrect triples.
    cleared = clear_stale_implemented_by(files)
    print(f"  cleared stale implementedBy/Count from {cleared} files")

    impl_counts = apply_implemented_by(preamble_inverse, iri_to_file)
    print(f"  implementedBy emitted to {sum(impl_counts.values())} nodes "
          f"across {len(impl_counts)} files")

    # Layer 2b implementedBy attribution — target side only. The
    # source-side processed-set already covers every scanned input
    # file (the loop at the top of main() runs once at startup) and
    # doesn't need additional bookkeeping here.
    for target_path, n in impl_counts.items():
        _files_with_output.add(target_path)
        if "regulations/kov/" in str(target_path):
            _files_with_output_kov.add(target_path)
        _triples += n
        if "regulations/kov/" in str(target_path):
            _triples_kov += n

    # Step 4: Materialise estleg:hasVersion (inverse of estleg:versionOf) (#345)
    print("\n[4/6] Materialising estleg:hasVersion from ProvisionVersion "
          "sidecars...")
    has_version_cleaned = clear_existing_has_version()
    print(f"  Cleared existing estleg:hasVersion from {has_version_cleaned} "
          "files")
    version_inverse = collect_version_inverse()
    total_version_edges = sum(len(v) for v in version_inverse.values())
    print(f"  Found {len(version_inverse)} provisions with "
          f"{total_version_edges} version edges in sidecars")
    has_version_counts, unresolved_versions = apply_has_version(
        version_inverse, iri_to_file,
    )
    total_has_version_nodes = sum(has_version_counts.values())
    print(f"  hasVersion emitted to {total_has_version_nodes} provision nodes "
          f"across {len(has_version_counts)} files")

    # #606 visibility gate: when ProvisionVersion sidecars carry
    # estleg:versionOf @ids that predate the #345 IRI scheme, nearly every
    # target lands in `unresolved_versions` and hasVersion silently
    # materialises on a tiny fraction of its resolvable provisions (the
    # 13-of-38,512 symptom). Surface it loudly on stderr WITHOUT failing the
    # pass — re-materialisation is a DEFERRED DATA regen (regenerate the
    # sidecars under the #345 IRI scheme, then re-run), out of scope here.
    if hasversion_resolution_degraded(version_inverse, unresolved_versions):
        _hv_total = len(version_inverse)
        _hv_resolved = _hv_total - len(unresolved_versions)
        _bang = "!" * 70
        print(
            f"\n{_bang}\n"
            "WARNING: estleg:hasVersion resolution is DEGRADED (#606)\n"
            f"  resolved {_hv_resolved}/{_hv_total} provisions "
            f"({len(unresolved_versions)} unresolved)\n"
            "  Likely cause: stale ProvisionVersion sidecars whose\n"
            "  estleg:versionOf @ids predate the #345 IRI scheme, so their\n"
            "  targets no longer match any peep node.\n"
            "  Fix: regenerate the ProvisionVersion sidecars under the #345\n"
            "  IRI scheme, then re-run this pass.\n"
            f"{_bang}",
            file=sys.stderr,
        )

    # Target-side attribution: hasVersion lands on provision nodes in the
    # peep corpus, so each updated file counts toward output/triples.
    for target_path, n in has_version_counts.items():
        _files_with_output.add(target_path)
        if "regulations/kov/" in str(target_path):
            _files_with_output_kov.add(target_path)
        _triples += n
        if "regulations/kov/" in str(target_path):
            _triples_kov += n

    # Step 5: Verify symmetry
    print("\n[5/6] Verifying references/referencedBy symmetry...")
    mismatches = verify_symmetry()
    # Categorisation (#157):
    #   * "target node does not exist in any file" — pure dangling
    #   * "unresolved-after-alias"                  — alias prefix gap
    #   * "genuine-missing-back-link"               — canonical target
    #                                                  exists, missing
    #                                                  back-link.
    cat_missing = [m for m in mismatches if "does not exist" in m["issue"]]
    cat_unresolved_alias = [m for m in mismatches if m["issue"] == "unresolved-after-alias"]
    cat_genuine = [m for m in mismatches if m["issue"] == "genuine-missing-back-link"]
    if mismatches:
        print(f"  Mismatches found: {len(mismatches)}")
        if cat_missing:
            print(f"    - target node missing from all files:  {len(cat_missing)}")
        if cat_unresolved_alias:
            print(f"    - unresolved-after-alias:              {len(cat_unresolved_alias)}")
        if cat_genuine:
            print(f"    - genuine-missing-back-link:           {len(cat_genuine)}")
            for m in cat_genuine[:5]:
                print(f"      {m['source']} -> {m['target']} (in {m['file']})")
            if len(cat_genuine) > 5:
                print(f"      ... and {len(cat_genuine) - 5} more")
    else:
        print("  All forward references have matching referencedBy links (or target does not exist)")

    # Step 6: Generate report
    print("\n[6/6] Generating inverse references report...")
    report = {
        "generated": f"{BUILD_EVALUATION_DATE}T00:00:00+00:00",  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "summary": {
            "target_iris_with_inverse_links": len(inverse_map),
            "total_inverse_links": total_inverse_links,
            "files_updated": len(update_counts),
            "total_nodes_updated": total_nodes_updated,
            "iris_indexed": len(iri_to_file),
            "alias_resolved": len(alias_resolved),
            "alias_ambiguous_refused": len(alias_ambiguous),
            "unresolved_target_iris": len(unresolved_iris),
            "symmetry_mismatches": len(mismatches),
            "symmetry_mismatch_categories": {
                "target_missing": len(cat_missing),
                "unresolved_after_alias": len(cat_unresolved_alias),
                "genuine_missing_back_link": len(cat_genuine),
            },
            # estleg:hasVersion (inverse of estleg:versionOf, #345)
            "provisions_with_versions": len(version_inverse),
            "total_version_edges": total_version_edges,
            "has_version_nodes_updated": total_has_version_nodes,
            "has_version_files_updated": len(has_version_counts),
            "has_version_unresolved_provisions": len(unresolved_versions),
        },
        "alias_resolutions": {
            orig: canon
            for orig, canon in sorted(alias_resolved.items())
        },
        "alias_ambiguous": {
            orig: cands
            for orig, cands in sorted(alias_ambiguous.items())
        },
        "unresolved_target_iris": sorted(unresolved_iris),
        # Provision IRIs referenced by a ProvisionVersion sidecar that have
        # no corresponding peep node to carry hasVersion (#345 diagnostics).
        "has_version_unresolved_provisions": sorted(unresolved_versions),
        "symmetry_mismatches": mismatches[:100],
        "top_referenced_provisions": sorted(
            [
                {"iri": iri, "referenced_by_count": len(sources)}
                for iri, sources in inverse_map.items()
            ],
            key=lambda x: x["referenced_by_count"],
            reverse=True,
        )[:50],
        "per_file_updates": {
            # Path keys aren't JSON-serializable. Render each target
            # as its corpus-relative path (falling back to the absolute
            # POSIX form if the file lives outside KRR_DIR — defensive,
            # shouldn't happen in normal runs).
            (
                str(path.relative_to(KRR_DIR))
                if path.is_absolute() and KRR_DIR in path.parents
                else path.as_posix()
            ): count
            for path, count in sorted(update_counts.items(), key=lambda x: -x[1])
        },
    }

    report_path = KRR_DIR / "reports" / "inverse_references_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Target provisions with inverse links: {len(inverse_map)}")
    print(f"  Total inverse link edges:             {total_inverse_links}")
    print(f"  Files updated:                        {len(update_counts)}")
    print(f"  Total nodes with referencedBy:        {total_nodes_updated}")
    print(f"  Alias-resolved IRIs:                  {len(alias_resolved)}")
    print(f"  Alias-ambiguous (refused):            {len(alias_ambiguous)}")
    print(f"  Unresolved target IRIs:               {len(unresolved_iris)}")
    print(f"  Symmetry mismatches:                  {len(mismatches)}")
    print(f"  Provisions with hasVersion:           {total_has_version_nodes}")
    print(f"  Total hasVersion edges:               {total_version_edges}")

    if inverse_map:
        top = max(inverse_map.items(), key=lambda x: len(x[1]))
        print(f"  Most referenced provision:            {top[0]} ({len(top[1])} refs)")

    print(f"  Report: {report_path}")
    print("=" * 70)

    # ---------- coverage report (KOV-aware schema) ----------
    all_input_files = list(iter_peep_files())
    _kov_files = [p for p in all_input_files
                  if "regulations/kov/" in str(p)]

    _wall, _rate, _peak_mb = measure_runtime(_start, len(_files_processed))
    out_path = KRR_DIR / "reports" / "kov" / "generate_inverse_references_coverage.json"
    write_coverage_report(
        CoverageReport(
            pipeline="generate_inverse_references",
            run_timestamp=PINNED_RUN_TIMESTAMP,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
            pipeline_version=resolve_pipeline_version(),
            input_files_total=len(all_input_files),
            input_files_kov=len(_kov_files),
            files_processed=len(_files_processed),
            files_processed_kov=len(_files_processed_kov),
            files_with_output=len(_files_with_output),
            files_with_output_kov=len(_files_with_output_kov),
            triples_emitted=_triples,
            triples_emitted_kov=_triples_kov,
            unresolved_references=len(unresolved_iris),
            wall_time_seconds=round(_wall, 2),
            items_per_second=round(_rate, 2),
            peak_memory_mb=round(_peak_mb, 1),
            error_count=len(_failures),
            failure_samples=_failures,
        ),
        out_path,
    )
    print(f"\nCoverage report: {out_path}")
    print(f"  files_with_output: {len(_files_with_output)} "
          f"(KOV: {len(_files_with_output_kov)})")
    print(f"  triples_emitted: {_triples} (KOV: {_triples_kov})")

    # Gate check — fails the run when the KOV side produced nothing
    # despite the corpus being present.
    if len(_kov_files) >= 11000 and len(_files_with_output_kov) == 0:
        print("\nGATE FAIL: KOV files were processed but none received output.")
        return 1
    print("\nGATE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
