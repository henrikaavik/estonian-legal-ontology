"""Layer 1 acceptance verification. Exit 0 = all checks pass."""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from enrich_kov_layer1 import is_stampable_law_node
from kov_registry import discover_issuer_slugs

REPO = Path(__file__).resolve().parents[1]
KRR = REPO / "krr_outputs"
KOV_DIR = KRR / "regulations" / "kov"


# Cap how many failure samples we surface in CLI output. Twenty is
# enough to spot patterns without flooding logs.
_FAIL_SAMPLE_CAP = 20


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _act_node(doc, type_iri):
    for n in doc.get("@graph", []):
        types = n.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if type_iri in types:
            return n
    return None


def check_issuer_count():
    """Issuer count must equal the number of issuer-directories under
    krr_outputs/regulations/kov/. Reading the expected count from the
    filesystem (rather than a hardcoded 357) means corpus growth no
    longer requires a code edit, while still flagging mismatches between
    issuers.json and the actual KOV directory tree (Finding #6 in
    issue #182).
    """
    issuers = _load(REPO / "data" / "ehak" / "issuers.json")
    expected = len(discover_issuer_slugs(KOV_DIR))
    return len(issuers) == expected, f"{len(issuers)}/{expected}"


def check_kov_acts():
    fields = ("estleg:enactedBy", "estleg:enactedByMunicipality",
              "estleg:titleNormalized")
    types_required = ("estleg:Act", "estleg:MunicipalRegulation")
    files = [
        f for f in glob.glob(str(KRR / "regulations/kov/**/*_peep.json"),
                              recursive=True)
        if not f.endswith("REGULATIONS_KOV_INDEX.json")
    ]
    ok = 0
    failures: list[str] = []
    for f in files:
        doc = _load(f)
        act = _act_node(doc, "estleg:MunicipalRegulation")
        if act is None:
            failures.append(f"{f}: missing MunicipalRegulation node")
            continue
        types = act.get("@type") or []
        if all(k in act for k in fields) and all(t in types for t in types_required):
            ok += 1
        else:
            missing_fields = [k for k in fields if k not in act]
            missing_types = [t for t in types_required if t not in types]
            failures.append(
                f"{f}: missing {missing_fields + missing_types}"
            )
    detail = f"{ok}/{len(files)}"
    if failures:
        detail += "; samples: " + "; ".join(failures[:_FAIL_SAMPLE_CAP])
    return ok == len(files), detail


def check_kov_provisions():
    """Verify each KOV provision is fully enriched AND its partOfAct
    points at the same file's MunicipalRegulation node — not just any
    IRI. A bug that wrote a stale or unrelated act IRI would be caught
    here, not just at SHACL.

    A KOV file MUST contain exactly one `estleg:MunicipalRegulation`
    node — Layer 1 enforces this in `enrich_kov_act_file`. We assert
    that invariant at the start of the per-file loop here so a
    regression that lets a malformed file slip through fails loudly
    with the path, rather than silently undercounting matched
    provisions (Finding #5).
    """
    fields = ("estleg:enactedBy", "estleg:enactedByMunicipality",
              "estleg:partOfAct")
    files = [
        f for f in glob.glob(str(KRR / "regulations/kov/**/*_peep.json"),
                              recursive=True)
        if not f.endswith("REGULATIONS_KOV_INDEX.json")
    ]
    ok = 0
    total = 0
    failures: list[str] = []
    files_with_zero_acts: list[str] = []
    files_with_multiple_acts: list[str] = []
    files_missing_act_iri: list[str] = []
    for f in files:
        doc = _load(f)
        # Locate the file's MunicipalRegulation @id; assert exactly
        # one. Any other count is a hard fail with the file path.
        act_iris: list[str | None] = []
        for n in doc.get("@graph", []):
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:MunicipalRegulation" in types:
                act_iris.append(n.get("@id"))
        if len(act_iris) == 0:
            files_with_zero_acts.append(f)
            # Skip the provision loop entirely — there's no anchor to
            # join against and the file already counts as a failure.
            continue
        if len(act_iris) > 1:
            files_with_multiple_acts.append(f)
            continue
        act_iri = act_iris[0]
        if act_iri is None:
            # An untyped @id (or missing @id) on the act node is a
            # data-shape bug we want to surface, not silently match.
            files_missing_act_iri.append(f)
            continue
        for n in doc.get("@graph", []):
            if "estleg:paragrahv" not in n:
                continue
            total += 1
            types = n.get("@type") or []
            if "estleg:KovProvision" not in types:
                failures.append(f"{f}: provision missing KovProvision type")
                continue
            if not all(k in n for k in fields):
                missing = [k for k in fields if k not in n]
                failures.append(f"{f}: provision missing {missing}")
                continue
            link = n.get("estleg:partOfAct") or {}
            if link.get("@id") != act_iri:
                failures.append(
                    f"{f}: partOfAct {link.get('@id')!r} != act IRI {act_iri!r}"
                )
                continue
            ok += 1
    detail = f"{ok}/{total}"
    extras: list[str] = []
    if files_with_zero_acts:
        extras.append(
            f"{len(files_with_zero_acts)} files with 0 MunicipalRegulation nodes"
        )
    if files_with_multiple_acts:
        extras.append(
            f"{len(files_with_multiple_acts)} files with >1 MunicipalRegulation nodes"
        )
    if files_missing_act_iri:
        extras.append(
            f"{len(files_missing_act_iri)} files with no @id on the act node"
        )
    if extras:
        detail += "; " + ", ".join(extras)
    if failures:
        detail += "; samples: " + "; ".join(failures[:_FAIL_SAMPLE_CAP])
    if files_missing_act_iri:
        detail += (
            "; missing-@id sample: "
            + "; ".join(files_missing_act_iri[:_FAIL_SAMPLE_CAP])
        )
    passed = (
        ok == total
        and not files_with_zero_acts
        and not files_with_multiple_acts
        and not files_missing_act_iri
    )
    return passed, detail


def check_laws():
    """Both `stamp_law_type` and this check use the SAME
    `is_stampable_law_node` predicate to decide whether a file is
    "applicable". Without that shared definition the two could drift —
    e.g. stamp_law_type would skip files that this check still expects
    to be stamped — and the verifier would fail mysteriously while the
    enricher reports success (Finding #1).
    """
    idx = _load(KRR / "INDEX.json")
    paths = [
        KRR / f
        for entry in idx["laws"] for f in entry.get("files", [])
        if os.path.exists(KRR / f)
    ]

    def _root_node_types(path: Path) -> list[str] | None:
        node = _act_node(_load(path), "owl:Ontology")
        if node is None:
            return None
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        return types

    applicable: list[Path] = []
    for p in paths:
        types = _root_node_types(p)
        if types is None:
            continue
        if not is_stampable_law_node(types):
            continue
        applicable.append(p)

    ok = 0
    for p in applicable:
        types = _root_node_types(p) or []
        if "estleg:Law" in types and "estleg:Act" in types:
            ok += 1
    return ok == len(applicable), f"{ok}/{len(applicable)}"


def check_state_regulations():
    files = sorted(glob.glob(str(KRR / "regulations/riik/*_peep.json")))
    ok = 0
    for f in files:
        doc = _load(f)
        for n in doc.get("@graph", []):
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "owl:Ontology" not in types:
                continue
            if any(t.endswith("Regulation") and t.startswith("estleg:")
                   for t in types):
                if "estleg:Act" in types:
                    ok += 1
                break
    return ok == len(files), f"{ok}/{len(files)}"


CHECKS = [
    ("issuer count = #(KOV dirs)", check_issuer_count),
    ("KOV acts enriched", check_kov_acts),
    ("KOV provisions enriched", check_kov_provisions),
    ("indexed laws stamped (Law + Act)", check_laws),
    ("state regulations stamped (Act)", check_state_regulations),
]


def main():
    fail = 0
    for label, fn in CHECKS:
        ok, detail = fn()
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {label}: {detail}")
        if not ok:
            fail += 1
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
