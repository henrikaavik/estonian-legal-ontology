#!/usr/bin/env python3
"""Surgical Round-2 data-quality repairs for issue #588 (offline, idempotent).

Repairs three instance-level defects verified for #588 WITHOUT re-running any
network-backed generator. Each fix is a value-level correction on a single
already-emitted node, mirroring what the (already-fixed) generators would emit:

1. **CURIA order ``EUCJ_62016TT0624``** (``curia/curia_other_peep.json``) —
   malformed CELEX + wrong court/decision-type. The ``rdfs:label``
   ("Üldkohtu … määrus … Kohtuasi T-624/16 TO"), the ECLI
   (``ECLI:EU:T:2019:47`` — ``T`` = General Court) and the case number
   (``T-624/16``) all describe a General Court **order**, yet the record was
   typed ``EUCourt_CourtOfJustice`` / ``EUDecType_Other`` and carries an
   invalid CELEX (``62016TT0624`` — ``TT`` is not a CELEX descriptor; the order
   document type is ``TO``). Repairs:

     * ``estleg:celexNumber``           ``62016TT0624`` -> ``62016TO0624``
     * ``estleg:euCourt``               ``EUCourt_CourtOfJustice`` -> ``EUCourt_GeneralCourt``
     * ``estleg:euCourtDecisionType``   ``EUDecType_Other`` -> ``EUDecType_Order``
     * the SAME bad CELEX token embedded in ``estleg:eurLexLink`` (``@value``;
       legacy ``estleg:curiaLink``), ``owl:sameAs`` (``@id``) and
       ``dcterms:source`` (``@id``) is rewritten
       too, so the node stays internally consistent — otherwise
       ``celexNumber`` would contradict its own ``owl:sameAs`` identity claim
       and the CELLAR/EUR-Lex links would resolve to a non-existent CELEX.

   The node ``@id`` (``estleg:EUCJ_62016TT0624``) is LEFT unchanged: it is an
   opaque identifier, nothing in ``krr_outputs/`` references it except the
   generated ``curia_combined.jsonld`` (rebuilt by the combined builder), and
   renaming it here would diverge from that generated artifact until a regen.

2. **Five EP "Texts Adopted"** (``eurlex/eurlex_regulations_peep.json``) —
   sector-5 type-AP CELEX (``52025AP0047/0045/0108/0102/0101``) mistyped
   ``estleg:euDocumentType = EUDocType_Regulation``. ``generate_eu_legislation``'s
   #394 classifier already routes ``("5", "AP")`` records to
   ``EUDocType_ParliamentPosition`` ("Euroopa Parlamendi seisukoht … ei ole
   jõustunud määrus"); the on-disk data is simply stale. This backfills the
   generator's value by REUSING :func:`classify_eu_doc_type` so the two never
   drift. SHACL's ``EULegislationShape`` requires ``euDocumentType``
   (``sh:minCount 1``), so the property is RE-POINTED, never deleted;
   ``euDocumentType`` is ``graphClosureExempt`` so the target individual need
   not be in-graph.

3. **Eesti Pank issuer case** (``regulations/riik/
   meenemundi_kaibelelaskmine_t1000150_peep.json``) —
   ``estleg:issuer = "Eesti Panga president"`` (lowercase ``president``) vs the
   canonical title-case ``"Eesti Panga President"`` used by the other 60 Eesti
   Pank regulations. Normalised to the canonical form (only that exact value).

The companion ``KNOWN_ABBREVIATIONS`` corrections (``PPVS``, ``RSVS``) are a
source edit in ``estleg_common.py``, not a data rewrite, so they are NOT
performed here (see ``test_fix_data_quality_588.py`` for the assertion).

Scope / safety:
  * Only the three named nodes are touched; every other node is left verbatim.
  * Idempotent: a re-run over already-fixed files reports 0 changes.
  * Atomic writes via :func:`estleg_common.save_json` (tempfile + os.replace).
  * Git-LFS-pointer guard: a file whose first bytes are the LFS spec marker is
    skipped (never rewrite a pointer stub into real content).
  * Dry-run is the DEFAULT; pass ``--apply`` to write.

Run (offline, idempotent)::

    python3 scripts/fix_data_quality_588.py            # dry-run (default)
    python3 scripts/fix_data_quality_588.py --apply    # write
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from estleg_common import KRR_DIR, save_json
from generate_eu_legislation import classify_eu_doc_type

LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"

# --- Item 1: CURIA order EUCJ_62016TT0624 -----------------------------------
CURIA_FILE = KRR_DIR / "curia" / "curia_other_peep.json"
# The curia aggregate is a SEPARATE build artifact (not rebuilt by
# generate_combined_jsonld), so the same node is patched there too or the
# subcorpus-parity gate flags drift (#643 lesson).
CURIA_COMBINED = KRR_DIR / "curia" / "curia_combined.jsonld"
CURIA_NODE_ID = "estleg:EUCJ_62016TT0624"
CURIA_BAD_CELEX = "62016TT0624"
CURIA_GOOD_CELEX = "62016TO0624"
CURIA_COURT_WRONG = "estleg:EUCourt_CourtOfJustice"
CURIA_COURT_RIGHT = "estleg:EUCourt_GeneralCourt"
CURIA_TYPE_WRONG = "estleg:EUDecType_Other"
CURIA_TYPE_RIGHT = "estleg:EUDecType_Order"
# Fields whose @value / @id embed the CELEX token and so must be rewritten in
# lock-step with celexNumber to keep the node internally consistent.
CURIA_CELEX_BEARING_FIELDS = (
    "estleg:eurLexLink",
    "estleg:curiaLink",
    "owl:sameAs",
    "dcterms:source",
)

# --- Item 2: 5 EP "Texts Adopted" -------------------------------------------
EURLEX_FILE = KRR_DIR / "eurlex" / "eurlex_regulations_peep.json"
EURLEX_TARGET_CELEX = (
    "52025AP0047",
    "52025AP0045",
    "52025AP0108",
    "52025AP0102",
    "52025AP0101",
)
# These records arrived through the regulations (``cdm:regulation``) query, so
# their nominal type is ``Regulation`` — the classifier reclassifies from there.
EURLEX_NOMINAL_TYPE = "Regulation"

# --- Item 3: Eesti Pank issuer case -----------------------------------------
EP_FILE = (
    KRR_DIR
    / "regulations"
    / "riik"
    / "meenemundi_kaibelelaskmine_t1000150_peep.json"
)
EP_ISSUER_WRONG = "Eesti Panga president"
EP_ISSUER_RIGHT = "Eesti Panga President"


# ---------------------------------------------------------------------------
# Pure, testable fixers — each mutates a parsed JSON-LD doc in place.
# ---------------------------------------------------------------------------
def _graph(doc: dict) -> list:
    graph = doc.get("@graph")
    return graph if isinstance(graph, list) else []


def _replace_celex_in_ref(ref: object, bad: str, good: str) -> bool:
    """Rewrite a CELEX token inside a ``{"@id"|"@value": …}`` object (or a list
    of them). Returns True if anything changed. Idempotent: once ``bad`` is
    gone there is nothing left to replace."""
    changed = False
    items = ref if isinstance(ref, list) else [ref]
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("@id", "@value"):
            val = item.get(key)
            if isinstance(val, str) and bad in val:
                item[key] = val.replace(bad, good)
                changed = True
    return changed


def fix_curia_doc(doc: dict) -> dict:
    """Repair the ``EUCJ_62016TT0624`` order node in place.

    Returns a result dict with per-field booleans plus ``node_found``. Every
    correction is guarded on the exact known-wrong value, so a re-run over an
    already-fixed (or absent) node reports all-False — idempotent.
    """
    result = {
        "node_found": False,
        "celexNumber": False,
        "euCourt": False,
        "euCourtDecisionType": False,
        "celex_in_links": False,
    }
    for node in _graph(doc):
        if not isinstance(node, dict) or node.get("@id") != CURIA_NODE_ID:
            continue
        result["node_found"] = True

        if node.get("estleg:celexNumber") == CURIA_BAD_CELEX:
            node["estleg:celexNumber"] = CURIA_GOOD_CELEX
            result["celexNumber"] = True

        court = node.get("estleg:euCourt")
        if isinstance(court, dict) and court.get("@id") == CURIA_COURT_WRONG:
            court["@id"] = CURIA_COURT_RIGHT
            result["euCourt"] = True

        dtype = node.get("estleg:euCourtDecisionType")
        if isinstance(dtype, dict) and dtype.get("@id") == CURIA_TYPE_WRONG:
            dtype["@id"] = CURIA_TYPE_RIGHT
            result["euCourtDecisionType"] = True

        links_changed = False
        for field in CURIA_CELEX_BEARING_FIELDS:
            if field in node and _replace_celex_in_ref(
                node[field], CURIA_BAD_CELEX, CURIA_GOOD_CELEX
            ):
                links_changed = True
        result["celex_in_links"] = links_changed
        break
    return result


def fix_eurlex_doc(doc: dict) -> int:
    """Re-point ``euDocumentType`` on the 5 sector-5 type-AP records to the
    generator's #394 classification. Returns the count of nodes changed."""
    targets = set(EURLEX_TARGET_CELEX)
    changed = 0
    for node in _graph(doc):
        if not isinstance(node, dict):
            continue
        celex = node.get("estleg:celexNumber")
        if celex not in targets:
            continue
        effective = classify_eu_doc_type(celex, EURLEX_NOMINAL_TYPE)
        if not effective:
            continue
        target_iri = f"estleg:EUDocType_{effective}"
        ref = node.get("estleg:euDocumentType")
        current = ref.get("@id") if isinstance(ref, dict) else None
        if current != target_iri:
            node["estleg:euDocumentType"] = {"@id": target_iri}
            changed += 1
    return changed


def fix_ep_doc(doc: dict) -> int:
    """Title-case-normalise the lone lowercase ``"Eesti Panga president"``
    issuer. Returns the count of issuer values changed. Only that exact value
    is touched (no blanket case-folding of other issuers)."""
    changed = 0
    for node in _graph(doc):
        if not isinstance(node, dict):
            continue
        issuer = node.get("estleg:issuer")
        if issuer == EP_ISSUER_WRONG:
            node["estleg:issuer"] = EP_ISSUER_RIGHT
            changed += 1
        elif isinstance(issuer, list) and EP_ISSUER_WRONG in issuer:
            node["estleg:issuer"] = [
                EP_ISSUER_RIGHT if v == EP_ISSUER_WRONG else v for v in issuer
            ]
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# File I/O wrappers (LFS guard + atomic write).
# ---------------------------------------------------------------------------
def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return fh.read(64).startswith(LFS_POINTER_PREFIX)
    except OSError:
        return True


def _load_doc(path: Path, *, log) -> dict | None:
    """Read + parse a JSON-LD file, or return None (with a logged reason) for a
    missing file, an LFS pointer stub, or unparseable content."""
    if not path.is_file():
        log(f"  (skip, missing) {path}")
        return None
    if _is_lfs_pointer(path):
        log(f"  (skip, git-LFS pointer — run `git lfs pull`) {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"  (skip, unreadable: {exc}) {path}")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the corrections. Default is a dry-run (no writes).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing (this is the default).",
    )
    args = parser.parse_args(argv)
    # Dry-run is the default; --apply opts in to writing. If both are passed the
    # safe (dry-run) interpretation wins.
    dry_run = args.dry_run or not args.apply

    log = print
    log("=" * 70)
    log("Surgical data-quality repairs for issue #588")
    log(f"  MODE: {'dry-run (no writes)' if dry_run else 'APPLY (writing)'}")
    log("=" * 70)

    total_changes = 0

    # --- Item 1: CURIA ------------------------------------------------------
    log("\n[1] CURIA order EUCJ_62016TT0624 (curia/curia_other_peep.json)")
    doc = _load_doc(CURIA_FILE, log=log)
    if doc is not None:
        res = fix_curia_doc(doc)
        if not res["node_found"]:
            log(f"  skipped: node {CURIA_NODE_ID} not present")
        else:
            n = sum(
                1
                for k in ("celexNumber", "euCourt", "euCourtDecisionType", "celex_in_links")
                if res[k]
            )
            total_changes += n
            verb = "would fix" if dry_run else "fixed"
            log(
                f"  {verb}: celexNumber={res['celexNumber']} "
                f"euCourt={res['euCourt']} "
                f"euCourtDecisionType={res['euCourtDecisionType']} "
                f"celex_in_links(eurLexLink/sameAs/source)={res['celex_in_links']}"
            )
            if n and not dry_run:
                save_json(CURIA_FILE, doc)

    # Patch the same node in the curia aggregate (build artifact, see above).
    agg = _load_doc(CURIA_COMBINED, log=log)
    if agg is not None:
        res = fix_curia_doc(agg)
        if res["node_found"]:
            n = sum(
                1
                for k in ("celexNumber", "euCourt", "euCourtDecisionType", "celex_in_links")
                if res[k]
            )
            if n:
                total_changes += n
                verb = "would fix" if dry_run else "fixed"
                log(f"  {verb} (curia_combined.jsonld aggregate): {n} field(s)")
                if not dry_run:
                    save_json(CURIA_COMBINED, agg)

    # --- Item 2: 5 EP "Texts Adopted" --------------------------------------
    log("\n[2] 5 EP sector-5 type-AP records (eurlex/eurlex_regulations_peep.json)")
    doc = _load_doc(EURLEX_FILE, log=log)
    if doc is not None:
        n = fix_eurlex_doc(doc)
        total_changes += n
        verb = "would re-type" if dry_run else "re-typed"
        log(
            f"  {verb} {n}/{len(EURLEX_TARGET_CELEX)} record(s): "
            f"EUDocType_Regulation -> EUDocType_ParliamentPosition"
        )
        if n and not dry_run:
            save_json(EURLEX_FILE, doc)

    # --- Item 3: Eesti Pank issuer -----------------------------------------
    log("\n[3] Eesti Pank issuer case (regulations/riik/…t1000150_peep.json)")
    doc = _load_doc(EP_FILE, log=log)
    if doc is not None:
        n = fix_ep_doc(doc)
        total_changes += n
        verb = "would normalise" if dry_run else "normalised"
        log(
            f"  {verb} {n} issuer value(s): "
            f"{EP_ISSUER_WRONG!r} -> {EP_ISSUER_RIGHT!r}"
        )
        if n and not dry_run:
            save_json(EP_FILE, doc)

    log("\n" + "=" * 70)
    verb = "would change" if dry_run else "changed"
    log(f"  TOTAL field corrections {verb}: {total_changes}")
    if dry_run and total_changes:
        log("  Re-run with --apply to write.")
    log("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
