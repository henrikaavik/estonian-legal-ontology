#!/usr/bin/env python3
"""
Comparative NIM (national implementing measure) links for Latvia, Lithuania,
Finland, and Sweden — not Estonian article-level transposition.

For each Estonian law → EU directive row in the transposition mapping, queries
EUR-Lex for how Latvia (LV), Lithuania (LT), Finland (FI), and Sweden (SE)
implemented the same directive. The sidecar is a neighbour-state comparative
layer. Estonian transposition itself lives on ``estleg:transposesDirective`` /
``krr_outputs/reports/transposition_mapping.json``, not in these HarmonisationLink
records.

``TARGET_COUNTRIES`` is LVA/LTU/FIN/SWE only. Do not add EST without a fetch
of Estonian NIMs; this script does not model EE article-level transposition.

Requires: krr_outputs/reports/transposition_mapping.json (from generate_transposition_mapping.py)

Generates:
  - krr_outputs/harmonisation/harmonisation_report.json    (full report)
  - krr_outputs/harmonisation/harmonisation_schema.json    (OWL property definitions)
  - krr_outputs/harmonisation/harmonisation_by_directive/   (per-directive files)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import date as _date
from datetime import datetime
from pathlib import Path

from estleg.estleg_common import (
    BUILD_EVALUATION_DATE,
    CONTEXT,
    act_deprecation,
    act_root_node,
    iter_peep_files,
    save_json,
)
from estleg.eurlex_common import (
    SPARQL_ENDPOINT,
    sanitize_celex,
    sparql_query,
)
from estleg.eurlex_common import (
    sparql_query_with_retry as _sparql_query_with_retry,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
KRR_DIR = REPO_ROOT / "krr_outputs"
HARMONISATION_DIR = KRR_DIR / "harmonisation"
BY_DIRECTIVE_DIR = HARMONISATION_DIR / "harmonisation_by_directive"

# Per-CELEX SPARQL response cache. The harmonisation pipeline issues one
# SPARQL query per directive (typically 200-400 directives) with a
# ~1.5s rate-limit sleep between calls — that's 5-15 minutes of wall
# time even when nothing has changed upstream. Caching successful
# responses to disk lets reruns finish in seconds when EUR-Lex hasn't
# moved.
CACHE_DIR = REPO_ROOT / ".cache" / "harmonisation"
CACHE_TTL_DAYS = 30

# Default freshness window for ``transposition_mapping.json``. The
# transposition mapping is generated upstream from the directives index;
# if it's older than this, the harmonisation report risks being
# anchored to stale Estonian-side data. Override with
# ``--allow-stale-mapping`` when you knowingly run against a snapshot.
MAPPING_FRESHNESS_DAYS = 30

NS = "https://w3id.org/estleg/"

RATE_DELAY = 1.5  # seconds between SPARQL requests

# #356 (SPARQL injection): ``celex_dir`` is interpolated verbatim into the
# SPARQL ``FILTER(STR(?celex_dir) = "...")`` string literal and is the seed
# for ``nim_prefix`` in another FILTER. CELEX numbers are an extremely
# narrow alphabet (digits, latin letters, and the ``()``/``/`` that appear
# in corrigenda/consolidated identifiers), so we reject anything outside it
# before building the query. ``sanitize_celex`` only protects IRIs/paths —
# it silently *strips* offending characters, which would mask a tampered
# value rather than refuse it — so it is NOT a substitute for this check.
_CELEX_ALLOWED = re.compile(r"[0-9A-Za-z()/]+")


# Target countries: Baltic/Nordic neighbors (LV/LT/FI/SE). This layer is
# comparative NIM measures for those states, not Estonian article-level
# transposition. EST is intentionally absent — do not add it without a
# fetch of Estonian NIMs. The keys are the ISO 3166-1 alpha-3 codes the
# Publications Office country vocabulary uses in
# ``.../authority/country/<CODE>`` URIs (these match
# ``cdm:measure_national_implementing_implemented_by_country`` exactly).
TARGET_COUNTRIES = {
    "LVA": {"label_en": "Latvia", "label_et": "Läti"},
    "LTU": {"label_en": "Lithuania", "label_et": "Leedu"},
    "FIN": {"label_en": "Finland", "label_et": "Soome"},
    "SWE": {"label_en": "Sweden", "label_et": "Rootsi"},
}

# Country filter for SPARQL — a ``VALUES``-style ``IN(...)`` list of the
# 3-letter codes extracted from each NIM's country authority URI.
COUNTRY_FILTER = ", ".join(f'"{c}"' for c in TARGET_COUNTRIES)


def load_json(filepath: Path) -> dict:
    """Load a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _is_strict_iso_date(value: str) -> bool:
    """Return True iff ``value`` is a strict ``YYYY-MM-DD`` date.

    Mirrors ``validate_all.is_strict_xsd_date`` so any ``xsd:date``
    literal this script emits passes the corpus date validator
    (``date.fromisoformat`` accepts ``"2011-7-8"`` on 3.11+, but the
    validator additionally requires the round-tripped canonical form).
    """
    try:
        parsed = _date.fromisoformat(value)
    except ValueError:
        return False
    return value == parsed.isoformat()


def sparql_query_with_retry(
    query: str, *, retries: int = 3, backoff: float = 2.0
) -> list[dict]:
    # Forwards to the shared helper; resolves ``sparql_query`` from this
    # module so tests that monkeypatch it on this module still take effect.
    return _sparql_query_with_retry(
        query, query_fn=sparql_query, retries=retries, backoff=backoff
    )


def _countries_cache_key() -> str:
    """Return a short stable digest of the active ``TARGET_COUNTRIES`` set.

    The per-CELEX SPARQL query embeds ``COUNTRY_FILTER`` (built from
    ``TARGET_COUNTRIES``), so a cached response is only valid for the
    country set it was computed against. Folding this digest into the
    cache path invalidates every entry the moment the country set
    changes. Reads the module global at call time so the key tracks any
    reassignment of ``TARGET_COUNTRIES`` (e.g. monkeypatched in tests).
    """
    payload = ",".join(sorted(TARGET_COUNTRIES))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


def _cache_path_for(celex_dir: str) -> Path:
    """Compute the on-disk cache path for a directive CELEX response.

    The path is an *injective* function of both ``celex_dir`` and the
    active country set. ``sanitize_celex`` strips the ``()``/``/``/``-``
    that distinguish corrigenda/consolidated CELEX identifiers (e.g.
    ``32011L0024R(01)`` and ``32011L0024R01`` sanitize identically), so
    the readable ``stem`` alone is NOT collision-free — two distinct
    directives could otherwise share one cache file and poison each
    other. A digest of the *raw* ``celex_dir`` restores injectivity while
    keeping the stem for debuggability; the country digest invalidates
    the cache when ``TARGET_COUNTRIES`` changes.
    """
    stem = sanitize_celex(celex_dir)
    celex_hash = hashlib.sha1(celex_dir.encode("utf-8")).hexdigest()[:8]
    return CACHE_DIR / f"{stem}__{_countries_cache_key()}__{celex_hash}.json"


def _cache_is_fresh(path: Path, ttl_days: int = CACHE_TTL_DAYS) -> bool:
    """Return ``True`` iff ``path`` exists and was written within the TTL."""
    if not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < ttl_days * 86400


def _nim_celex_prefix(celex_dir: str) -> str:
    """Return the prefix every NIM CELEX for ``celex_dir`` shares.

    CELLAR assigns each National-Implementing-Measure work a CELEX of the
    form ``7<directive-CELEX-without-its-sector-digit><COUNTRY3>_<id>`` —
    e.g. directive ``32009L0110`` → ``72009L0110FIN_184955``. A NIM that
    transposes several directives carries one such CELEX *per directive*,
    so restricting ``?celex_nat`` to this prefix collapses the SPARQL
    result to one row per (country, NIM) for *this* directive instead of
    a cartesian product over every directive the NIM happens to
    implement. Empty string for a degenerate CELEX (never happens for the
    ``3xxxxLxxxx`` directives in ``transposition_mapping.json``).
    """
    return ("7" + celex_dir[1:]) if len(celex_dir) > 1 else ""


def fetch_other_transpositions(
    celex_dir: str, *, use_cache: bool = True
) -> list[dict]:
    """
    For a given directive CELEX, fetch transposition measures from target countries.
    Returns list of dicts with ``country_code``/``country_en``/``country_et``,
    ``celex_nat`` and (when CELLAR has them) ``title``, ``date``, ``nim_uri``.

    The CDM models a National Implementing Measure (NIM) as a ``work``
    typed ``cdm:measure_national_implementing`` that carries:
      * ``cdm:measure_national_implementing_implements_directive`` → the
        directive ``work`` it transposes;
      * ``cdm:measure_national_implementing_implemented_by_country`` → the
        country authority URI (we filter for LV/LT/FI/SE);
      * ``cdm:resource_legal_id_celex`` → the NIM's CELEX(es);
      * ``cdm:work_title`` → the national-language act title;
      * ``cdm:work_date_document`` → the act's document date.
    The directive is joined via its CELEX *variable* rather than a string
    literal: Virtuoso treats an unqualified ``"X"`` literal as
    ``rdf:langString`` (no lang tag), which never equals the ``xsd:string``
    CELEX value, so ``?directive cdm:resource_legal_id_celex "<celex>"``
    returns zero rows — the bug behind #197 (the GET→POST fix from #129
    made the endpoint respond, but this query body still matched nothing).
    ``FILTER(STR(?celex_dir) = "<celex>")`` matches regardless of the
    literal's datatype, and the exact equality (vs ``STRSTARTS``) keeps
    corrigenda works (``...R(01)``) out of the join. The earlier query
    also used ``cdm:resource_legal_measures_transposition_for`` /
    ``cdm:work_created_by_agent`` — neither holds for NIMs in CELLAR
    today, so it returned nothing on every directive (#197).

    Uses an on-disk per-CELEX cache (``CACHE_DIR``) with ``CACHE_TTL_DAYS``
    TTL. Cache hits skip both the SPARQL call and the rate-limit sleep
    that follows it in the main loop, so reruns within the TTL are
    near-instant. Pass ``use_cache=False`` to force a network round-trip.

    Raises ``ValueError`` if ``celex_dir`` contains any character outside
    the strict CELEX allowlist (#356) — guarding the two SPARQL FILTER
    string literals it feeds against injection from a tampered EUR-Lex
    response or a hand-edited report value.
    """
    # #356: validate BEFORE the cache lookup — sanitize_celex (used by
    # _cache_path_for) strips disallowed characters, so a malicious value
    # could otherwise collide with / poison a legitimate cache entry.
    if not isinstance(celex_dir, str) or not _CELEX_ALLOWED.fullmatch(celex_dir):
        raise ValueError(
            f"Refusing to build SPARQL query: celex_dir={celex_dir!r} "
            r"contains characters outside the CELEX allowlist [0-9A-Za-z()/]."
        )

    cache_path = _cache_path_for(celex_dir)
    if use_cache and _cache_is_fresh(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, list):
                return cached
        except (OSError, json.JSONDecodeError):
            # Corrupt cache entry — fall through to a fresh query and
            # overwrite it.
            pass

    nim_prefix = _nim_celex_prefix(celex_dir)
    query = f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?nim ?country ?celex_nat ?title ?date WHERE {{
  ?nim cdm:measure_national_implementing_implements_directive ?directive .
  ?directive cdm:resource_legal_id_celex ?celex_dir .
  FILTER(STR(?celex_dir) = "{celex_dir}")
  ?nim cdm:measure_national_implementing_implemented_by_country ?country_uri .
  BIND(STRAFTER(STR(?country_uri), "authority/country/") AS ?country)
  FILTER(?country IN ({COUNTRY_FILTER}))
  ?nim cdm:resource_legal_id_celex ?celex_nat .
  FILTER(STRSTARTS(STR(?celex_nat), "{nim_prefix}"))
  OPTIONAL {{ ?nim cdm:work_title ?title }}
  OPTIONAL {{ ?nim cdm:work_date_document ?date }}
}} ORDER BY ?country ?celex_nat
"""
    bindings = sparql_query_with_retry(query)

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for b in bindings:
        country = b.get("country", {}).get("value", "")
        celex_nat = b.get("celex_nat", {}).get("value", "")
        # Defensive: the SPARQL FILTER already restricts to TARGET_COUNTRIES,
        # but never trust a remote endpoint to enforce it.
        if country not in TARGET_COUNTRIES or not celex_nat:
            continue

        key = (country, celex_nat)
        if key in seen:
            continue
        seen.add(key)

        entry: dict = {
            "country_code": country,
            "country_en": TARGET_COUNTRIES[country].get("label_en", country),
            "country_et": TARGET_COUNTRIES[country].get("label_et", country),
            "celex_nat": celex_nat,
        }
        title = b.get("title", {}).get("value", "")
        if title:
            entry["title"] = title
        date = b.get("date", {}).get("value", "")
        if date:
            entry["date"] = date
        nim_uri = b.get("nim", {}).get("value", "")
        if nim_uri:
            entry["nim_uri"] = nim_uri
        results.append(entry)

    if use_cache:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            # Cache write is best-effort: a failed write should not abort
            # the pipeline run.
            print(f"    WARNING: cache write failed for {celex_dir}: {exc}")

    return results


def generate_schema() -> dict:
    """Generate OWL schema definitions for harmonisation properties."""
    schema_nodes: list[dict] = [
        # HarmonisationLink class
        {
            "@id": "estleg:HarmonisationLink",
            "@type": ["owl:Class"],
            "rdfs:label": "Harmoniseerimisseos (Harmonisation link)",
            "rdfs:comment": (
                "A link between an Estonian law and national laws of other EU member states "
                "that transpose the same EU directive."
            ),
        },
        # ObjectProperty: harmonisedWith
        {
            "@id": "estleg:harmonisedWith",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "harmoniseeritud (harmonised with)",
            "rdfs:comment": (
                "Links an Estonian law to a harmonisation record showing "
                "parallel implementations of the same EU directive in other member states."
            ),
            # #335: every instance of estleg:harmonisedWith is emitted on an
            # act-level node (estleg:Act / Law / owl:Ontology), never on a
            # provision — so the domain must be estleg:Act. Under rdfs
            # inference a LegalProvision domain would mis-type every
            # harmonised statute as a single provision.
            "rdfs:domain": {"@id": "estleg:Act"},
            "rdfs:range": {"@id": "estleg:HarmonisationLink"},
            # #425: the harmonisation node↔act edge previously carried
            # estleg:harmonisedWith in BOTH directions — act→link in the law
            # peeps (correct) and link→act on the aggregate node here (inverted).
            # estleg:harmonises is the explicit inverse so each direction has
            # exactly one predicate; declare the axiom on both ends.
            "owl:inverseOf": {"@id": "estleg:harmonises"},
        },
        # ObjectProperty: harmonises (#425) — inverse of harmonisedWith.
        # Emitted ONLY on the aggregate estleg:Harmonisation_<celex> node so
        # the link→act back-edge stops re-using estleg:harmonisedWith. Domain
        # HarmonisationLink / range Act mirror the inverse of harmonisedWith's
        # Act→HarmonisationLink direction.
        {
            "@id": "estleg:harmonises",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "harmoneerib (harmonises)",
            "rdfs:comment": (
                "Links a harmonisation record to the Estonian act(s) that "
                "transpose the shared EU directive. Inverse of "
                "estleg:harmonisedWith."
            ),
            "rdfs:domain": {"@id": "estleg:HarmonisationLink"},
            "rdfs:range": {"@id": "estleg:Act"},
            "owl:inverseOf": {"@id": "estleg:harmonisedWith"},
        },
        # ObjectProperty: sharedDirective
        {
            "@id": "estleg:sharedDirective",
            "@type": ["owl:ObjectProperty"],
            "rdfs:label": "ühine direktiiv (shared directive)",
            "rdfs:comment": "The EU directive that multiple member states have transposed.",
            "rdfs:domain": {"@id": "estleg:HarmonisationLink"},
            "rdfs:range": {"@id": "estleg:EULegislation"},
        },
        # DatatypeProperty: memberStateCode
        {
            "@id": "estleg:memberStateCode",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "liikmesriigi kood (member state code)",
            "rdfs:comment": "ISO 3166-1 alpha-3 country code of the EU member state.",
            "rdfs:domain": {"@id": "estleg:HarmonisationLink"},
            "rdfs:range": {"@id": "xsd:string"},
        },
        # DatatypeProperty: nationalCelex
        {
            "@id": "estleg:nationalCelex",
            "@type": ["owl:DatatypeProperty"],
            "rdfs:label": "riigisisene CELEX (national CELEX number)",
            "rdfs:comment": "CELEX identifier for the national transposition measure.",
            "rdfs:domain": {"@id": "estleg:HarmonisationLink"},
            "rdfs:range": {"@id": "xsd:string"},
        },
    ]

    return {"@context": CONTEXT, "@graph": schema_nodes}


def build_directive_node(
    celex_dir: str,
    directive_iri: str,
    estonian_law_name: str,
    estonian_laws: list[dict],
    other_measures: list[dict],
) -> dict:
    """
    Build a JSON-LD document for harmonisation data around one directive.

    ``estonian_laws`` is the full list of Estonian transposing-law records
    for this directive (each a dict that may carry an ``iri`` key). #334:
    when several Estonian laws transpose one directive, the aggregate
    ``Harmonisation_<celex>`` node must back-link to *all* of them via
    ``estleg:harmonises`` — previously only the first law was listed.

    #425: the back-edge uses ``estleg:harmonises`` (link → act), the inverse
    of the ``estleg:harmonisedWith`` edge the law peeps carry (act → link).
    The aggregate node never re-uses ``harmonisedWith`` so the predicate
    carries a single direction throughout the corpus.
    """
    safe_celex = sanitize_celex(celex_dir)
    # Preserve order, drop laws whose act-level IRI couldn't be resolved,
    # and de-duplicate so a law mapped twice to the same directive yields a
    # single back-link.
    harmonised_refs: list[dict] = []
    seen_iris: set[str] = set()
    for law in estonian_laws:
        iri = law.get("iri")
        if iri and iri not in seen_iris:
            seen_iris.add(iri)
            harmonised_refs.append({"@id": iri})
    graph: list[dict] = [
        {
            "@id": f"estleg:Harmonisation_{safe_celex}",
            "@type": ["owl:NamedIndividual", "estleg:HarmonisationLink"],
            "rdfs:label": f"Harmonisation: {celex_dir}",
            "rdfs:comment": (
                f"Harmonisation record for directive {celex_dir}, "
                f"transposed by Estonia ({estonian_law_name}) and "
                f"{len(other_measures)} other member state measure(s)."
            ),
            "estleg:sharedDirective": {"@id": directive_iri},
            # #425: emit the inverse estleg:harmonises (link → act) here, NOT
            # estleg:harmonisedWith (act → link, which the law peeps carry).
            # Multi-valued — always emit it as an array. #334: list every
            # Estonian law that transposes this directive, not just the first.
            "estleg:harmonises": harmonised_refs,
        },
    ]

    # Add individual country measure nodes
    by_country: dict[str, list[dict]] = {}
    for m in other_measures:
        code = m["country_code"]
        if code not in by_country:
            by_country[code] = []
        by_country[code].append(m)

    for country_code, measures in sorted(by_country.items()):
        country_info = TARGET_COUNTRIES.get(country_code, {})
        for idx, m in enumerate(measures):
            node_id = f"estleg:Harm_{safe_celex}_{country_code}_{idx + 1}"

            title = m.get("title", "")
            label_suffix = title if title else m["celex_nat"]
            node: dict = {
                "@id": node_id,
                # Typed as HarmonisationLink so the SHACL HarmonisationLinkShape
                # (and the eurlex bucket validator) reaches these per-measure
                # nodes too — all of HarmonisationLinkShape's constraints are
                # optional, so the aggregate node above and these measure
                # nodes both validate against it.
                "@type": ["owl:NamedIndividual", "estleg:HarmonisationLink"],
                "rdfs:label": f"{country_info.get('label_en', country_code)}: {label_suffix}",
                "estleg:memberStateCode": country_code,
                "estleg:nationalCelex": m["celex_nat"],
                "estleg:sharedDirective": {"@id": directive_iri},
            }
            if title:
                node["dcterms:title"] = title
            measure_date = m.get("date", "")
            # Emit dcterms:date as xsd:date only when it is a strict
            # YYYY-MM-DD value (validate_all rejects loose xsd:date literals);
            # otherwise keep it as a plain string so no information is lost.
            if measure_date:
                if _is_strict_iso_date(measure_date):
                    node["dcterms:date"] = {"@value": measure_date, "@type": "xsd:date"}
                else:
                    node["dcterms:date"] = measure_date
            graph.append(node)

    return {"@context": CONTEXT, "@graph": graph}


def update_law_file_harmonisation(filepath: Path, harmonisation_ids: list[str]) -> bool:
    """
    Add estleg:harmonisedWith references to a law file's ontology node.
    Returns True if the file was modified.
    """
    try:
        data = load_json(filepath)
    except Exception as e:
        print(f"    ERROR loading {filepath.name}: {e}")
        return False

    # #578 P2 (defense in depth): never write harmonisation onto a deprecated/
    # replaced act, even if a stale or hand-edited mapping routes one here past
    # the main() guard.
    if act_deprecation(data)[0]:
        return False

    graph = data.get("@graph", [])

    target_node = act_root_node({"@graph": graph})
    if target_node is None and graph:
        target_node = graph[0]

    if target_node is None:
        return False

    # Build harmonisation references
    harm_refs = [{"@id": hid} for hid in harmonisation_ids]

    existing = target_node.get("estleg:harmonisedWith", [])
    if isinstance(existing, dict):
        existing = [existing]

    existing_ids = {ref.get("@id") for ref in existing if isinstance(ref, dict)}
    new_refs = [ref for ref in harm_refs if ref["@id"] not in existing_ids]

    if not new_refs:
        return False

    all_refs = existing + new_refs
    target_node["estleg:harmonisedWith"] = all_refs

    save_json(filepath, data)
    return True


# Node @type values that legitimately represent the act-level entity
# we want harmonisation links to point at. Provision-level nodes (e.g.
# ``estleg:Provision``) intentionally do NOT appear here so the fallback
# below never silently picks a section/sub-paragraph node.
_ACT_LEVEL_TYPES = frozenset(
    {
        "estleg:Act",
        "estleg:Map",
    }
)


# #578b/#631: multi-Part Võlaõigusseadus harmonisation must NOT default to
# ``law_files[0]`` (string-sorted, so ``osa10`` = the torts Part wins for every
# consumer/contract directive). For the affected directives, pin the
# subject-correct Part peep (or ``None`` to drop the VÕS edge — other mapped acts
# transpose it). Kept in lockstep with the data set by
# ``scripts/retarget_vos_harmonisation_578b.py``. Consulted only for the VÕS rows.
VOS_DIRECTIVE_PART_OVERRIDES: dict[str, str | None] = {
    "31990L0314": "volaoigusseadus_osa8_peep.json",  # Package Travel -> services
    "31993L0013": "volaoigusseadus_osa1_peep.json",  # Unfair Terms -> general
    "31997L0007": "volaoigusseadus_osa1_peep.json",  # Distance Contracts -> general
    "31999L0044": "volaoigusseadus_osa2_peep.json",  # Consumer Sales -> sales
    "32002L0065": "volaoigusseadus_osa1_peep.json",  # Distance Fin. Marketing -> general
    "32008L0048": "volaoigusseadus_osa1_peep.json",  # Consumer Credit -> general
    "32008L0122": "volaoigusseadus_osa3_peep.json",  # Timeshare -> use contracts
    "32009L0044": None,                              # Financial Collateral -> drop VÕS
    "32009L0052": None,                              # Employer Sanctions -> drop VÕS
}


def pick_harmonisation_law_file(celex: str, law_files: list[str]) -> str | None:
    """Choose which mapped law file anchors a directive's harmonisation link.

    Defaults to the first file, EXCEPT for the #578b Võlaõigusseadus directives
    whose correct Part is pinned in ``VOS_DIRECTIVE_PART_OVERRIDES`` — there the
    override Part is used when the row is a VÕS row, or the VÕS anchor is dropped
    (``None``) when VÕS is not the transposing vehicle. Non-VÕS rows are
    unaffected.
    """
    if not law_files:
        return None
    is_vos = any("volaoigusseadus_osa" in f for f in law_files)
    if is_vos and celex in VOS_DIRECTIVE_PART_OVERRIDES:
        override = VOS_DIRECTIVE_PART_OVERRIDES[celex]
        return override  # may be None -> caller drops the VÕS edge
    return law_files[0]


def get_law_harmonisation_target_iri(law_file: str) -> str | None:
    """Return the real act-level node IRI for a mapped law file.

    The previous implementation fell through to ``graph[0]`` whenever no
    act-root node was found — which silently anchored the
    harmonisation link on whatever sat first in the file (often a
    provision, not the act). We now require the fallback node to carry
    an act-level ``@type`` (see ``_ACT_LEVEL_TYPES``); otherwise return
    ``None`` and let the caller skip the law (and log it in the report).
    """
    try:
        data = load_json(KRR_DIR / law_file)
    except Exception:
        return None

    # #578: a deprecated/replaced act (the retired VOS_*/volaigusseadus VÕS
    # decomposition) must never receive a harmonisation link — the canonical
    # volaoigusseadus_* family carries them. Skip and let the caller log it.
    if act_deprecation(data)[0]:
        return None

    graph = data.get("@graph", [])

    def _node_id_if_act_level(node: dict) -> str | None:
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if not any(t in _ACT_LEVEL_TYPES for t in types):
            return None
        node_id = node.get("@id")
        return node_id if isinstance(node_id, str) and node_id else None

    root = act_root_node({"@graph": graph})
    if root is not None:
        node_id = root.get("@id")
        return node_id if isinstance(node_id, str) and node_id else None

    # Restricted fallback: only accept ``graph[0]`` if it is itself an
    # act-level node — never an arbitrary provision node.
    if graph:
        return _node_id_if_act_level(graph[0])
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write outputs even if one or more EUR-Lex directive lookups fail.",
    )
    parser.add_argument(
        "--allow-stale-mapping",
        action="store_true",
        help=(
            "Skip the freshness gate on transposition_mapping.json. "
            f"By default the run fails if the mapping is older than "
            f"{MAPPING_FRESHNESS_DAYS} days."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help=(
            "Bypass the per-CELEX SPARQL response cache "
            f"({CACHE_DIR.relative_to(REPO_ROOT)}). Useful for forcing a "
            f"fresh sweep before a release."
        ),
    )
    parser.add_argument(
        "--mapping-freshness-days",
        type=int,
        default=MAPPING_FRESHNESS_DAYS,
        help=(
            "Override the staleness threshold (in days) for "
            "transposition_mapping.json."
        ),
    )
    return parser.parse_args()


def _check_mapping_freshness(
    mapping_data: dict, *, threshold_days: int, allow_stale: bool
) -> None:
    """Validate that ``transposition_mapping.json`` is current with the build.

    Reads ``mapping_data['generated']`` (an ISO date == the ``BUILD_EVALUATION_DATE``
    pinned at generation time) and compares it against the *current*
    ``BUILD_EVALUATION_DATE`` — NOT wall-clock now() (see #592: a now()-based age
    grows unbounded against the pinned stamp and hard-fails CI ~threshold days
    after the pin). Either warns or aborts depending on ``allow_stale``. Always
    prints the generated date for traceability.
    """
    generated = mapping_data.get("generated")
    if not generated:
        msg = "transposition_mapping.json missing 'generated' timestamp"
        if allow_stale:
            print(f"  WARNING: {msg} (allowed via --allow-stale-mapping)")
            return
        print(f"ERROR: {msg}")
        sys.exit(1)

    try:
        gen_date = datetime.fromisoformat(generated).date()
    except ValueError as exc:
        msg = f"unparseable 'generated' timestamp {generated!r}: {exc}"
        if allow_stale:
            print(f"  WARNING: {msg} (allowed via --allow-stale-mapping)")
            return
        print(f"ERROR: {msg}")
        sys.exit(1)

    # #592: compare against the current pinned build date, NOT wall-clock now().
    # `generated` is BUILD_EVALUATION_DATE (#295 deterministic stamp), so a
    # now()-based age grows unbounded and hard-fails CI ~threshold days after the
    # pin even with zero changes (a calendar time-bomb). Pin-to-pin keeps the
    # staleness signal — "mapping generated for an older corpus snapshot than the
    # current build" — fully deterministic.
    try:
        reference_date = _date.fromisoformat(BUILD_EVALUATION_DATE)
    except ValueError:
        reference_date = gen_date  # defensive: a malformed pin must not abort the gate
    age_days = (reference_date - gen_date).days
    print(
        f"  transposition_mapping.json generated: {generated} "
        f"({age_days}d behind build snapshot {BUILD_EVALUATION_DATE})"
    )

    if age_days > threshold_days:
        msg = (
            f"transposition_mapping.json is {age_days} days old "
            f"(threshold: {threshold_days})"
        )
        if allow_stale:
            print(f"  WARNING: {msg} (allowed via --allow-stale-mapping)")
            return
        print(
            f"ERROR: {msg}. Re-run generate_transposition_mapping.py or "
            "pass --allow-stale-mapping to override."
        )
        sys.exit(1)


def main():
    args = parse_args()
    print("=" * 60)
    print(
        "Generate comparative NIM measures for LV/LT/FI/SE "
        "(not Estonian article-level transposition)"
    )
    countries = ', '.join(f"{v['label_en']} ({k})" for k, v in TARGET_COUNTRIES.items())
    print(f"Target countries: {countries}")
    print(f"Endpoint: {SPARQL_ENDPOINT}")
    print("=" * 60)

    # --- Step 0: Clear existing harmonisation data ---
    print("\n--- Clearing existing harmonisation data ---")
    cleared = 0
    for peep_file in iter_peep_files(include_kov=False):  # KOV does not apply
        try:
            with open(peep_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
            modified = False
            for node in doc.get("@graph", []):
                if "estleg:harmonisedWith" in node:
                    del node["estleg:harmonisedWith"]
                    modified = True
            if modified:
                save_json(peep_file, doc)
                cleared += 1
        except Exception:
            continue
    print(f"  Cleared harmonisedWith from {cleared} files")

    # Clear old per-directive harmonisation files
    if BY_DIRECTIVE_DIR.exists():
        old_files = list(BY_DIRECTIVE_DIR.glob("harm_*.json"))
        if old_files:
            shutil.rmtree(BY_DIRECTIVE_DIR)
            BY_DIRECTIVE_DIR.mkdir(parents=True, exist_ok=True)
            print(f"  Cleared {len(old_files)} old files from {BY_DIRECTIVE_DIR.name}/")
        else:
            print(f"  {BY_DIRECTIVE_DIR.name}/ already empty")
    else:
        print(f"  {BY_DIRECTIVE_DIR.name}/ does not exist yet")

    # --- Step 1: Load transposition mapping ---
    print("\n--- Loading transposition mapping ---")
    mapping_path = KRR_DIR / "reports" / "transposition_mapping.json"
    if not mapping_path.exists():
        print(f"ERROR: {mapping_path} not found.")
        print("Run generate_transposition_mapping.py first.")
        sys.exit(1)

    mapping_data = load_json(mapping_path)
    _check_mapping_freshness(
        mapping_data,
        threshold_days=args.mapping_freshness_days,
        allow_stale=args.allow_stale_mapping,
    )
    mappings = mapping_data.get("mappings", [])
    print(f"  Transposition mappings loaded: {len(mappings)}")

    if not mappings:
        print("  No transposition mappings found. Nothing to harmonise.")
        return

    # Deduplicate by directive CELEX (we only need to query each directive once)
    # Track laws whose target IRI couldn't be resolved (e.g. file missing,
    # only provision-level nodes present after the graph[0] guard) so we
    # can surface them in the report rather than silently dropping.
    skipped_laws: list[dict] = []
    directives_to_process: dict[str, dict] = {}
    for m in mappings:
        celex = m["directive_celex"]
        if celex not in directives_to_process:
            directives_to_process[celex] = {
                "directive_iri": m["directive_iri"],
                "estonian_laws": [],
            }
        law_entry = {
            "name": m["matched_law_name"],
            "source_act": m.get("matched_source_act", ""),
            "files": m.get("law_files", []),
        }
        # #578b/#631: pick the subject-correct Part (not the string-sorted
        # law_files[0], which lands every VÕS consumer directive on the torts
        # Part). pick_* may return None to deliberately drop a spurious VÕS anchor.
        anchor_file = pick_harmonisation_law_file(celex, law_entry["files"])
        if law_entry["files"] and anchor_file is None:
            # #578b: the VÕS anchor was deliberately dropped for this directive
            # (VÕS is not its transposing vehicle) — skip the row entirely so it
            # contributes no harmonisedWith/harmonises edge.
            continue
        if law_entry["files"]:
            resolved = get_law_harmonisation_target_iri(anchor_file)
            law_entry["iri"] = resolved
            if not resolved:
                skipped_laws.append(
                    {
                        "name": law_entry["name"],
                        "source_act": law_entry["source_act"],
                        "law_file": anchor_file,
                        "directive_celex": celex,
                        "reason": "no act-level @id found (deprecated/unresolved)",
                    }
                )
                # #578 P2: an unresolved law — including a deprecated/replaced act,
                # for which get_law_harmonisation_target_iri returns None — must NOT
                # be recorded for harmonisation. Without this continue the entry is
                # still appended below and update_law_file_harmonisation later writes
                # estleg:harmonisedWith onto the deprecated act anyway.
                continue
        # Avoid duplicate law entries per directive
        existing_names = {
            law["name"] for law in directives_to_process[celex]["estonian_laws"]
        }
        if law_entry["name"] not in existing_names:
            directives_to_process[celex]["estonian_laws"].append(law_entry)

    print(f"  Unique directives to query: {len(directives_to_process)}")
    if skipped_laws:
        print(
            f"  WARNING: {len(skipped_laws)} law(s) skipped — could not "
            f"resolve act-level IRI (see harmonisation_report.json)"
        )

    # --- Step 2: Create output directories ---
    HARMONISATION_DIR.mkdir(parents=True, exist_ok=True)
    BY_DIRECTIVE_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 3: Generate schema ---
    print("\n--- Generating harmonisation schema ---")
    schema_doc = generate_schema()
    schema_path = HARMONISATION_DIR / "harmonisation_schema.json"
    save_json(schema_path, schema_doc)
    print(f"  Saved: {schema_path.name}")

    # --- Step 4: Query EUR-Lex for parallel transpositions ---
    print("\n--- Querying EUR-Lex for neighbour-state NIM measures (LV/LT/FI/SE) ---")

    harmonisation_data: list[dict] = []
    total_parallel_measures = 0
    directives_with_parallels = 0
    country_totals: dict[str, int] = {c: 0 for c in TARGET_COUNTRIES}
    # Track which law files get which harmonisation node IDs
    law_file_harmonisation: dict[str, list[str]] = {}
    errors = 0
    # Persist failed CELEXes so reruns can target them (and the
    # harmonisation report shows what's outstanding).
    failed_directives: list[dict] = []
    cache_hits = 0
    use_cache = not args.no_cache

    directive_list = sorted(directives_to_process.keys())

    for i, celex_dir in enumerate(directive_list):
        info = directives_to_process[celex_dir]
        directive_iri = info["directive_iri"]
        estonian_laws = info["estonian_laws"]

        if (i + 1) % 10 == 0 or i == 0:
            print(f"  Processing directive {i + 1}/{len(directive_list)}: {celex_dir}")

        # Detect cache hits up-front so we can skip the rate-limit sleep
        # below — that sleep is a defence against EUR-Lex throttling, but
        # a cache hit issued zero requests.
        cache_hit = use_cache and _cache_is_fresh(_cache_path_for(celex_dir))
        if cache_hit:
            cache_hits += 1

        # Query for other countries' transpositions. ``sparql_query_with_retry``
        # already retries transient 5xxs with exponential backoff;
        # reaching this except branch means the failure was terminal for
        # this directive. We log it, stash the CELEX for the report, and
        # continue rather than aborting the whole sweep.
        try:
            other_measures = fetch_other_transpositions(
                celex_dir, use_cache=use_cache
            )
        except Exception as e:
            print(f"    ERROR ({celex_dir}): {e}")
            errors += 1
            failed_directives.append(
                {"directive_celex": celex_dir, "error": str(e)}
            )
            if not cache_hit:
                time.sleep(RATE_DELAY)
            continue

        if other_measures:
            directives_with_parallels += 1
            total_parallel_measures += len(other_measures)

            # Count by country
            for m in other_measures:
                cc = m["country_code"]
                if cc in country_totals:
                    country_totals[cc] += 1

            # Generate per-directive harmonisation file
            safe_celex = sanitize_celex(celex_dir)
            primary_law = estonian_laws[0] if estonian_laws else {"name": "unknown", "files": []}
            law_iri = primary_law.get("iri")
            if not law_iri:
                print(f"    ERROR: no law IRI for {primary_law['name']} ({celex_dir})")
                errors += 1
                # Still fall through to the rate-limit sleep below — the
                # SPARQL query already happened, so we owe EUR-Lex a
                # courtesy delay before the next request unless this was
                # a cache hit.
                if not cache_hit:
                    time.sleep(RATE_DELAY)
                continue

            directive_doc = build_directive_node(
                celex_dir=celex_dir,
                directive_iri=directive_iri,
                estonian_law_name=primary_law.get("source_act", primary_law["name"]),
                # #334: pass every transposing law so the aggregate node
                # back-links to all of them (build_directive_node filters
                # out any whose IRI is unresolved).
                estonian_laws=estonian_laws,
                other_measures=other_measures,
            )

            directive_file = BY_DIRECTIVE_DIR / f"harm_{safe_celex}.json"
            save_json(directive_file, directive_doc)

            # Build by-country breakdown for the report
            by_country: dict[str, list[str]] = {}
            for m in other_measures:
                cc = m["country_code"]
                if cc not in by_country:
                    by_country[cc] = []
                by_country[cc].append(m["celex_nat"])

            harmonisation_id = f"estleg:Harmonisation_{safe_celex}"

            harmonisation_entry = {
                "directive_celex": celex_dir,
                "directive_iri": directive_iri,
                "estonian_laws": [
                    {
                        "name": law["name"],
                        "source_act": law.get("source_act", ""),
                        "iri": law.get("iri", ""),
                    }
                    for law in estonian_laws
                ],
                "parallel_measures": len(other_measures),
                "countries": by_country,
                "harmonisation_file": f"harmonisation_by_directive/harm_{safe_celex}.json",
                "harmonisation_id": harmonisation_id,
            }
            harmonisation_data.append(harmonisation_entry)

            # Track which law files need harmonisation links
            for law in estonian_laws:
                for law_file in law.get("files", []):
                    filepath_str = str(KRR_DIR / law_file)
                    if filepath_str not in law_file_harmonisation:
                        law_file_harmonisation[filepath_str] = []
                    if harmonisation_id not in law_file_harmonisation[filepath_str]:
                        law_file_harmonisation[filepath_str].append(harmonisation_id)

        # Rate limiting: only sleep when we actually issued a network
        # request (cache hits read from disk and incur no remote load).
        if not cache_hit:
            time.sleep(RATE_DELAY)

    print(f"\n  Directives queried: {len(directive_list)}")
    print(f"  Directives with parallel measures: {directives_with_parallels}")
    print(f"  Total parallel measures found: {total_parallel_measures}")
    print(f"  Cache hits: {cache_hits}/{len(directive_list)}")
    print(f"  Query errors: {errors}")

    # --- Step 5: Update Estonian law files with harmonisation links ---
    print("\n--- Updating Estonian law files with harmonisation links ---")
    files_updated = 0
    for filepath_str, harm_ids in law_file_harmonisation.items():
        filepath = Path(filepath_str)
        if not filepath.exists():
            continue
        if update_law_file_harmonisation(filepath, harm_ids):
            files_updated += 1

    print(f"  Law files updated: {files_updated}")

    # --- Step 6: Generate report ---
    print("\n--- Generating harmonisation report ---")

    report = {
        "generated": BUILD_EVALUATION_DATE,  # #295: pinned deterministic stamp (no wall-clock churn in tracked artifact)
        "source": SPARQL_ENDPOINT,
        "estonian_country_code": "EST",
        "transposition_mapping_generated": mapping_data.get("generated", ""),
        "target_countries": {
            code: info for code, info in TARGET_COUNTRIES.items()
        },
        "total_directives_queried": len(directive_list),
        "directives_with_parallels": directives_with_parallels,
        "total_parallel_measures": total_parallel_measures,
        "query_errors": errors,
        "cache_hits": cache_hits,
        "failed_directives": sorted(
            failed_directives, key=lambda d: d["directive_celex"]
        ),
        "skipped_laws": sorted(skipped_laws, key=lambda s: s["name"]),
        "law_files_updated": files_updated,
        "measures_by_country": {
            code: {
                "label_en": TARGET_COUNTRIES[code]["label_en"],
                "label_et": TARGET_COUNTRIES[code]["label_et"],
                "total_measures": count,
            }
            for code, count in sorted(country_totals.items(), key=lambda x: -x[1])
        },
        "harmonisation_entries": sorted(
            harmonisation_data, key=lambda h: h["directive_celex"]
        ),
    }

    report_path = HARMONISATION_DIR / "harmonisation_report.json"
    save_json(report_path, report)
    print(f"  Saved: {report_path.name}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("Harmonisation linking complete!")
    print(f"  Directives queried:             {len(directive_list)}")
    print(f"  Directives with parallels:      {directives_with_parallels}")
    print(f"  Total parallel measures:        {total_parallel_measures}")
    print(f"  Query errors:                   {errors}")
    print(f"  Law files updated:              {files_updated}")
    print("\n  Measures by country:")
    for code, count in sorted(country_totals.items(), key=lambda x: -x[1]):
        label = TARGET_COUNTRIES[code]["label_en"]
        print(f"    {label:20s} ({code}): {count:4d}")
    print("\nOutputs:")
    print(f"  {report_path.relative_to(REPO_ROOT)}")
    print(f"  {schema_path.relative_to(REPO_ROOT)}")
    print(f"  {BY_DIRECTIVE_DIR.relative_to(REPO_ROOT)}/ ({len(harmonisation_data)} files)")
    print("=" * 60)
    if errors and not args.allow_partial:
        sys.exit(1)


if __name__ == "__main__":
    main()
