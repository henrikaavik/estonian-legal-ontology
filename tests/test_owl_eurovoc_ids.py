"""Regression for leftover #421 fabricated EuroVoc ids in the two OWL modules.

``karistusseadustik_eriosa_owl.jsonld`` and ``tsus_osa7_138_169_owl.jsonld``
are hand-curated (not rewritten by ``classify_eurovoc.py``), so they still
carried pre-#421 codes after the peep corpus was remapped. This gate keeps
those two files on verified descriptor ids.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import classify_eurovoc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
KRR = REPO_ROOT / "krr_outputs"

OWL_MODULES = (
    "karistusseadustik_eriosa_owl.jsonld",
    "tsus_osa7_138_169_owl.jsonld",
)

# Headline pre-#421 fabrications that must not reappear in the OWL modules.
# 2411 is the generic "Law" domain, which EUROVOC_DOMAINS documents as removed
# (no mapping entry); the rest remap via data/eurovoc_domain_mapping.json.
FABRICATED_OLD_CODES = (
    "2446",
    "2411",
    "2431",
    "5616",
    "5226",
    "1221",
    "2841",
)

EUROVOC_IRI_RE = re.compile(r"^http://eurovoc\.europa\.eu/(.+)$")


def _mapping_new_ids() -> set[str]:
    mapping_path = REPO_ROOT / "data" / "eurovoc_domain_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    return {entry["newId"] for entry in mapping}


def _eurovoc_ids(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    ids: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            raw = node.get("@id")
            if isinstance(raw, str):
                match = EUROVOC_IRI_RE.fullmatch(raw)
                if match:
                    ids.append(match.group(1))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return ids


def test_owl_modules_have_no_fabricated_eurovoc_ids():
    allowed = set(classify_eurovoc.EUROVOC_DOMAINS) | _mapping_new_ids()

    for name in OWL_MODULES:
        path = KRR / name
        found = _eurovoc_ids(path)
        leftover = [code for code in found if code in FABRICATED_OLD_CODES]
        assert leftover == [], (
            f"{name} still has pre-#421 fabricated EuroVoc ids: {leftover}"
        )
        unknown = [code for code in found if code not in allowed]
        assert unknown == [], (
            f"{name} has eurovoc.europa.eu ids not in EUROVOC_DOMAINS "
            f"or the mapping new codes: {unknown}"
        )
