"""#490 — committed combined closure stubs carry SHACL-required fields."""

from __future__ import annotations

import json
from pathlib import Path

from estleg import estleg_common

REPO = Path(__file__).resolve().parent.parent
COMBINED = REPO / "krr_outputs" / "combined_ontology.jsonld"
LFS_PREFIX = "version https://git-lfs.github.com/spec/v1"

REQUIRED = {
    "estleg:NationalRegulation": ("estleg:documentType", "estleg:terviktekstId"),
    "estleg:MunicipalRegulation": (
        "estleg:documentType",
        "estleg:terviktekstId",
        "estleg:enactedBy",
        "estleg:enactedByMunicipality",
        "estleg:titleNormalized",
    ),
    "estleg:KovProvision": (
        "estleg:enactedBy",
        "estleg:enactedByMunicipality",
        "estleg:partOfAct",
    ),
    "estleg:ProposedAmendment": ("estleg:amendingDraft",),
}


def _iter_graph_objects(path: Path):
    """Yield each top-level ``@graph`` object without loading the 265 MB file."""
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as handle:
        text = handle.read()
    start = text.find('"@graph"')
    if start < 0:
        return
    bracket = text.find("[", start)
    i = bracket + 1
    length = len(text)
    while i < length:
        while i < length and text[i] in " \t\r\n,":
            i += 1
        if i < length and text[i] == "]":
            return
        obj, end = decoder.raw_decode(text, i)
        if isinstance(obj, dict):
            yield obj
        i = end


def test_combined_stub_required_paths_are_present() -> None:
    if not COMBINED.is_file():
        import pytest

        pytest.skip("combined_ontology.jsonld missing")
    first = COMBINED.read_text(encoding="utf-8", errors="replace")[:80]
    if first.startswith(LFS_PREFIX):
        import pytest

        pytest.skip("combined_ontology.jsonld is an LFS pointer")

    stubs = 0
    incomplete: list[str] = []
    for node in _iter_graph_objects(COMBINED):
        if not node.get(estleg_common.STUB_NODE_MARKER):
            continue
        stubs += 1
        types = node.get("@type", [])
        if isinstance(types, str):
            types = [types]
        need: set[str] = set()
        for typ in types:
            need.update(REQUIRED.get(typ, ()))
        missing = [prop for prop in sorted(need) if prop not in node]
        if missing:
            incomplete.append(f"{node.get('@id')} missing {missing}")
    assert stubs > 0
    assert incomplete == []


def test_legacy_deprecation_decisions_match_on_disk_roots() -> None:
    """#490 rebuild is blocked unless #426 marks match current IRIs."""

    from estleg import deprecate_legacy_statutes as dep

    checked, violations = dep.verify_decisions_applied(
        REPO / "data" / "legacy_statute_decisions.json",
        REPO / "krr_outputs",
    )
    assert checked > 0
    assert violations == []


def test_builder_emits_required_closure_props() -> None:
    for typ, props in REQUIRED.items():
        assert tuple(estleg_common.required_closure_props([typ])) == props
