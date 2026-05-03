"""KOV integration Layer 1 enrichment orchestrator.

Reads:
  - data/ehak/municipalities.json
  - data/ehak/issuers.json
  - krr_outputs/regulations/kov/<issuer>/*_peep.json
  - krr_outputs/*_peep.json (laws)

Writes:
  - krr_outputs/municipalities_peep.json (new)
  - krr_outputs/issuers_kov_peep.json (new)
  - in-place updates to KOV act + provision files
  - in-place stamp of estleg:Law on existing law nodes

Idempotent: safe to re-run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kov_registry import (
    IssuerEntry,
    Municipality,
    load_municipalities,
    normalize_title,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
KRR_DIR = REPO_ROOT / "krr_outputs"
EHAK_DIR = REPO_ROOT / "data" / "ehak"
KOV_DIR = KRR_DIR / "regulations" / "kov"

MUNICIPALITIES_OUT = KRR_DIR / "municipalities_peep.json"
ISSUERS_OUT = KRR_DIR / "issuers_kov_peep.json"

CONTEXT = {
    "estleg": "https://data.riik.ee/ontology/estleg#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}


def municipality_iri(ehak_code: str) -> str:
    return f"estleg:Municipality_EHAK_{ehak_code}"


def issuer_iri(slug: str) -> str:
    return f"estleg:Issuer_{slug}"


def build_municipality_doc(municipalities: dict[str, Municipality]) -> dict:
    """Build the JSON-LD document containing all Municipality nodes."""
    nodes = [
        {
            "@id": "estleg:Municipalities_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "Estonian Municipalities (current EHAK)",
            "dcterms:source": "https://www.stat.ee/sites/default/files/2020-03/ehak.csv",
        }
    ]
    for code in sorted(municipalities):
        mun = municipalities[code]
        nodes.append({
            "@id": municipality_iri(code),
            "@type": ["owl:NamedIndividual", "estleg:Municipality"],
            "rdfs:label": mun["name"],
            "estleg:ehakCode": code,
            "estleg:county": mun["county"],
        })
    return {"@context": CONTEXT, "@graph": nodes}


def build_issuer_doc(issuers: dict[str, IssuerEntry]) -> dict:
    """Build the JSON-LD document containing all Issuer nodes."""
    nodes: list[dict] = [
        {
            "@id": "estleg:Issuers_Kov_Map_2026",
            "@type": ["owl:Ontology"],
            "rdfs:label": "KOV Issuers (volikogu and valitsus bodies)",
        }
    ]
    for slug in sorted(issuers):
        entry = issuers[slug]
        node: dict = {
            "@id": issuer_iri(slug),
            "@type": ["owl:NamedIndividual", "estleg:Issuer"],
            "rdfs:label": entry["displayName"],
            "estleg:bodyType": entry["bodyType"],
            "estleg:currentMunicipality": {
                "@id": municipality_iri(entry["currentMunicipalityCode"])
            },
            "estleg:mappingSource": entry["mappingSource"],
        }
        if entry["mappingEvidence"]:
            node["estleg:mappingEvidence"] = entry["mappingEvidence"]
        if entry["historicalMunicipalityName"]:
            node["estleg:historicalMunicipalityName"] = entry["historicalMunicipalityName"]
        nodes.append(node)
    return {"@context": CONTEXT, "@graph": nodes}


_REGULATION_TYPES = {
    "estleg:NationalRegulation",
    "estleg:GovernmentRegulation",
    "estleg:MinisterialRegulation",
    "estleg:MunicipalRegulation",
}


def _add_type(node: dict, type_iri: str) -> bool:
    """Add type_iri to node["@type"] if missing. Returns True if changed."""
    types = node.get("@type")
    if isinstance(types, str):
        types = [types]
    elif types is None:
        types = []
    if type_iri in types:
        return False
    types.append(type_iri)
    node["@type"] = types
    return True


def stamp_law_type(path: Path) -> None:
    """Add `estleg:Law` and `estleg:Act` rdf:type to the act node of a
    law peep file.

    Skips files whose act node already has a regulation-specific type
    (NationalRegulation, MunicipalRegulation, etc.) — those go through
    `stamp_act_type` instead.

    Idempotent.
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    changed = False
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" not in types:
            continue
        if _REGULATION_TYPES.intersection(types):
            continue
        if _add_type(node, "estleg:Law"):
            changed = True
        if _add_type(node, "estleg:Act"):
            changed = True
        node["@type"] = sorted(set(node["@type"]))  # stable order

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")


def stamp_act_type(path: Path) -> None:
    """Add `estleg:Act` to act nodes already typed as a regulation
    subclass (NationalRegulation / GovernmentRegulation /
    MinisterialRegulation). For state regulation files under
    `regulations/riik/`. Idempotent.

    KOV act nodes are handled by `enrich_kov_act_file`; this function is
    for the state regulation files that Layer 1 otherwise leaves alone.
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    changed = False
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "owl:Ontology" not in types:
            continue
        # Only stamp Act on regulation acts; KOV acts go through
        # enrich_kov_act_file.
        if "estleg:MunicipalRegulation" in types:
            continue
        if not _REGULATION_TYPES.intersection(types):
            continue
        if _add_type(node, "estleg:Act"):
            changed = True
            node["@type"] = sorted(set(node["@type"]))

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")


def enrich_kov_act_file(path: Path, issuer: IssuerEntry) -> None:
    """Add Layer 1 properties to a single KOV act peep file in place.

    Raises ValueError if the file does not contain exactly one node
    typed as estleg:MunicipalRegulation. A KOV file under the corpus
    must have that node — silently skipping malformed files would let
    Gate A pass while leaving acts undecorated.

    Idempotent — running on an already-enriched file leaves it
    byte-identical.
    """
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)

    issuer_ref = {"@id": issuer_iri(issuer["slug"])}
    municipality_ref = {"@id": municipality_iri(issuer["currentMunicipalityCode"])}

    # Pass 1: collect every MunicipalRegulation node, enforce exactly one,
    # then enrich it.
    act_nodes: list[dict] = []
    for node in doc.get("@graph", []):
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if "estleg:MunicipalRegulation" in types:
            act_nodes.append(node)

    if len(act_nodes) == 0:
        raise ValueError(
            f"{path}: no estleg:MunicipalRegulation node in @graph; "
            "expected a KOV act file"
        )
    if len(act_nodes) > 1:
        raise ValueError(
            f"{path}: found {len(act_nodes)} estleg:MunicipalRegulation "
            "nodes; KOV files must contain exactly one"
        )

    act_node = act_nodes[0]
    act_iri = act_node.get("@id")
    # Prefer dc:source over rdfs:label: the canonical KOV peep shape
    # has rdfs:label with the document-type suffix appended (e.g.
    # "Tallinna jäätmehoolduseeskiri (määrus)") while dc:source carries
    # the clean title ("Tallinna jäätmehoolduseeskiri"). The clean title
    # is what we want to feed normalize_title.
    title = act_node.get("dc:source") or act_node.get("rdfs:label") or ""
    # Stamp estleg:Act directly so SHACL constraints with
    # sh:class estleg:Act on partOfAct don't require RDFS inference
    # at validation time.
    _add_type(act_node, "estleg:Act")
    act_node["estleg:enactedBy"] = issuer_ref
    act_node["estleg:enactedByMunicipality"] = municipality_ref
    act_node["estleg:titleNormalized"] = normalize_title(
        title, issuer["displayName"]
    )

    # Pass 2: enrich provisions.
    for node in doc.get("@graph", []):
        if "estleg:paragrahv" not in node:
            continue
        _add_type(node, "estleg:KovProvision")
        node["estleg:enactedBy"] = issuer_ref
        node["estleg:enactedByMunicipality"] = municipality_ref
        node["estleg:partOfAct"] = {"@id": act_iri}

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _load_issuers(path: Path) -> dict[str, IssuerEntry]:
    with open(path, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    return {row["slug"]: row for row in rows}


def _save_jsonld(path: Path, doc: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _load_law_paths(index_path: Path) -> tuple[list[Path], list[str]]:
    """Resolve law file paths from krr_outputs/INDEX.json.

    INDEX.json's `laws` is a list of {name, files: [...]}. A multi-part
    law (e.g. asjaoigusseadus_osa1, _osa2) has multiple files in one
    entry. Flatten and resolve to absolute paths under KRR_DIR.

    INDEX.json is the canonical source — using KRR_DIR.glob('*_peep.json')
    instead would conflate the 615 laws with other root-level files
    (combined ontology output, generated registry files, etc.) and would
    drift over time.

    Policy on missing entries: INDEX.json is known to drift (typos like
    'ametiuhigute' for 'ametiuhingute', renames not propagated, etc.).
    Rather than abort the whole Layer 1 run on stale INDEX entries —
    which is an unrelated hygiene issue — we log them and continue.
    The orchestrator surfaces the count in its summary so the drift
    is visible. INDEX hygiene is fixed in a separate PR.

    Mixed extensions: INDEX entries end in either `.json` or `.jsonld`.
    Both are accepted as-is; we do NOT silently substitute the
    alternate extension because that could mask real renames.

    Returns: (resolved_paths, missing_filenames)
    """
    with open(index_path, "r", encoding="utf-8") as fh:
        idx = json.load(fh)
    paths: list[Path] = []
    missing: list[str] = []
    for entry in idx.get("laws", []):
        for fname in entry.get("files", []):
            p = KRR_DIR / fname
            if p.exists():
                paths.append(p)
            else:
                missing.append(fname)
    return paths, missing


def main() -> int:
    print("=" * 70)
    print("KOV Integration Layer 1 — Enrichment")
    print("=" * 70)

    municipalities = load_municipalities(EHAK_DIR / "municipalities.json")
    issuers = _load_issuers(EHAK_DIR / "issuers.json")
    print(f"Loaded {len(municipalities)} municipalities, {len(issuers)} issuers")

    # 1. Write Municipality JSON-LD
    _save_jsonld(MUNICIPALITIES_OUT, build_municipality_doc(municipalities))
    print(f"Wrote {MUNICIPALITIES_OUT.name}")

    # 2. Write Issuer JSON-LD
    _save_jsonld(ISSUERS_OUT, build_issuer_doc(issuers))
    print(f"Wrote {ISSUERS_OUT.name}")

    # 3. Enrich KOV act files
    kov_files = sorted(KOV_DIR.glob("**/*_peep.json"))
    # The KOV index file lives at the top of KOV_DIR — exclude it.
    kov_files = [f for f in kov_files if not f.name.startswith("REGULATIONS_KOV_INDEX")]
    print(f"\nEnriching {len(kov_files)} KOV act files...")
    enriched = 0
    missing_issuer = 0
    for f in kov_files:
        slug = f.parent.name
        issuer = issuers.get(slug)
        if issuer is None:
            missing_issuer += 1
            print(f"  ERROR: no issuer entry for {slug}; skipping {f.name}")
            continue
        # Will raise on malformed files (no MunicipalRegulation node).
        enrich_kov_act_file(f, issuer)
        enriched += 1
    print(f"Enriched {enriched} KOV act files (missing-issuer: {missing_issuer})")
    if missing_issuer:
        # Hard-fail rather than let Gate A pass with skipped files.
        print(
            f"FAIL: {missing_issuer} KOV files had no matching issuer entry. "
            "Re-run scripts/build_kov_registry.py to regenerate issuers.json.",
            file=sys.stderr,
        )
        return 2

    # 4. Stamp estleg:Law + estleg:Act on the 615 laws (paths from INDEX.json)
    law_paths, missing_law_files = _load_law_paths(KRR_DIR / "INDEX.json")
    law_count = len({p.stem.replace("_peep", "").rsplit("_osa", 1)[0]
                     for p in law_paths})
    print(f"\nStamping estleg:Law on {len(law_paths)} law files "
          f"({law_count} unique laws)...")
    if missing_law_files:
        print(f"  WARN: INDEX.json references {len(missing_law_files)} "
              "missing law files (stale index — fix in a separate PR):")
        for f in missing_law_files[:10]:
            print(f"    {f}")
        if len(missing_law_files) > 10:
            print(f"    ... and {len(missing_law_files) - 10} more")
    for f in law_paths:
        stamp_law_type(f)

    # 5. Stamp estleg:Act on state regulation acts under regulations/riik/
    riik_dir = KRR_DIR / "regulations" / "riik"
    riik_files = sorted(riik_dir.glob("*_peep.json")) if riik_dir.exists() else []
    print(f"\nStamping estleg:Act on {len(riik_files)} state regulation files...")
    for f in riik_files:
        stamp_act_type(f)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
