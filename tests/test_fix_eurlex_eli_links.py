"""Unit tests for scripts/fix_eurlex_eli_links.py (#607b + #610).

Pure tmp_path / in-memory tests — no corpus, no LFS, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fix_eurlex_eli_links as fix  # noqa: E402

CELEX = "32008R0015"
ELI = "http://data.europa.eu/eli/reg/2008/15/oj"
NATURAL = "reg/2008/15"
CELEX_PURL = f"http://publications.europa.eu/resource/celex/{CELEX}"


def _eu_node(**overrides) -> dict:
    node = {
        "@id": f"estleg:EU_{CELEX}",
        "@type": ["owl:NamedIndividual", "estleg:EULegislation"],
        "estleg:celexNumber": CELEX,
        "estleg:eliIdentifier": {"@value": ELI, "@type": "xsd:anyURI"},
        "eli:id_local": CELEX,  # the bug: holds CELEX
        "owl:sameAs": {"@id": CELEX_PURL},  # only CELEX sameAs
    }
    node.update(overrides)
    return node


def test_fix_node_corrects_id_local_and_adds_eli_sameas():
    node = _eu_node()
    id_fixed, sa_added = fix.fix_node(node)
    assert id_fixed is True
    assert sa_added is True
    # eli:id_local now the natural id; CELEX untouched.
    assert node["eli:id_local"] == NATURAL
    assert node["estleg:celexNumber"] == CELEX
    # owl:sameAs grew to a 2-item list: CELEX PURL preserved + ELI added.
    targets = {s["@id"] for s in node["owl:sameAs"]}
    assert targets == {CELEX_PURL, ELI}


def test_fix_node_idempotent():
    node = _eu_node()
    fix.fix_node(node)
    # Second pass: id_local already natural, ELI sameAs already present.
    id_fixed, sa_added = fix.fix_node(node)
    assert id_fixed is False
    assert sa_added is False


def test_fix_node_without_eli_is_noop():
    # No estleg:eliIdentifier -> nothing to derive, no ELI sameAs to add.
    node = _eu_node()
    del node["estleg:eliIdentifier"]
    id_fixed, sa_added = fix.fix_node(node)
    assert id_fixed is False
    assert sa_added is False
    assert node["eli:id_local"] == CELEX  # left as-is


def test_fix_node_preserves_celex_sameas_when_adding_eli():
    node = _eu_node()
    fix.fix_node(node)
    targets = {s["@id"] for s in node["owl:sameAs"]}
    assert CELEX_PURL in targets  # never dropped


def test_fix_node_handles_missing_sameas():
    node = _eu_node()
    del node["owl:sameAs"]
    _id, sa_added = fix.fix_node(node)
    assert sa_added is True
    # Single value (only ELI) -> a dict, not a list.
    assert node["owl:sameAs"] == {"@id": ELI}


def test_migrate_doc_only_touches_eu_nodes():
    doc = {
        "@graph": [
            _eu_node(),
            {"@id": "estleg:SomethingElse", "eli:id_local": "x"},  # not EU_*
        ]
    }
    id_fixed, sa_added = fix.migrate_doc(doc)
    assert id_fixed == 1
    assert sa_added == 1
    # Non-EU node untouched.
    assert doc["@graph"][1]["eli:id_local"] == "x"


def test_process_file_roundtrip(tmp_path: Path):
    p = tmp_path / "eurlex_regulations_peep.json"
    p.write_text(json.dumps({"@graph": [_eu_node()]}), encoding="utf-8")
    id_fixed, sa_added = fix.process_file(p, dry_run=False)
    assert (id_fixed, sa_added) == (1, 1)
    reloaded = json.loads(p.read_text(encoding="utf-8"))
    assert reloaded["@graph"][0]["eli:id_local"] == NATURAL


def test_dry_run_writes_nothing(tmp_path: Path):
    p = tmp_path / "eurlex_regulations_peep.json"
    original = json.dumps({"@graph": [_eu_node()]})
    p.write_text(original, encoding="utf-8")
    fix.process_file(p, dry_run=True)
    assert p.read_text(encoding="utf-8") == original


def test_lfs_pointer_skipped(tmp_path: Path):
    p = tmp_path / "eurlex_combined.jsonld"
    p.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:x\nsize 1\n",
        encoding="utf-8",
    )
    assert fix._is_lfs_pointer(p) is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
