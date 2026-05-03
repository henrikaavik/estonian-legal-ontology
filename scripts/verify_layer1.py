"""Layer 1 acceptance verification. Exit 0 = all checks pass."""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KRR = REPO / "krr_outputs"


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
    issuers = _load(REPO / "data" / "ehak" / "issuers.json")
    expected = 357
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
    for f in files:
        doc = _load(f)
        act = _act_node(doc, "estleg:MunicipalRegulation")
        if act is None:
            continue
        types = act.get("@type") or []
        if all(k in act for k in fields) and all(t in types for t in types_required):
            ok += 1
    return ok == len(files), f"{ok}/{len(files)}"


def check_kov_provisions():
    """Verify each KOV provision is fully enriched AND its partOfAct
    points at the same file's MunicipalRegulation node — not just any
    IRI. A bug that wrote a stale or unrelated act IRI would be caught
    here, not just at SHACL.
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
    for f in files:
        doc = _load(f)
        # Locate the file's exactly-one MunicipalRegulation @id
        act_iri = None
        for n in doc.get("@graph", []):
            types = n.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if "estleg:MunicipalRegulation" in types:
                if act_iri is not None:
                    act_iri = "<MULTIPLE>"  # invalidate
                    break
                act_iri = n.get("@id")
        for n in doc.get("@graph", []):
            if "estleg:paragrahv" not in n:
                continue
            total += 1
            types = n.get("@type") or []
            if "estleg:KovProvision" not in types:
                continue
            if not all(k in n for k in fields):
                continue
            link = n.get("estleg:partOfAct") or {}
            if link.get("@id") != act_iri:
                continue
            ok += 1
    return ok == total, f"{ok}/{total}"


def check_laws():
    idx = _load(KRR / "INDEX.json")
    paths = [
        KRR / f
        for entry in idx["laws"] for f in entry.get("files", [])
        if os.path.exists(KRR / f)
    ]
    # Sub-part law files (e.g. *_osa6_peep.json) often have no
    # owl:Ontology node — only the parent file does. stamp_law_type()
    # skips those by design, so the denominator should too.
    applicable = [p for p in paths if _act_node(_load(p), "owl:Ontology") is not None]
    ok = 0
    for p in applicable:
        doc = _load(p)
        n = _act_node(doc, "owl:Ontology")
        types = n.get("@type") or []
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
    ("issuer count = 357", check_issuer_count),
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
