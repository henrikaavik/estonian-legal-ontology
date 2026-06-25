"""Unit tests for the #580 concept-definition citation strip.

Covers the post-process script (scripts/fix_concept_definition_splices.py) AND
the generator backstop in scripts/extract_legal_concepts.py. Pure in-memory /
tmp_path tests — no corpus, no LFS, no network.
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import extract_legal_concepts as elc  # noqa: E402
import fix_concept_definition_splices as fix  # noqa: E402


# --------------------------------------------------------------------------
# strip_citation_tail (post-process)
# --------------------------------------------------------------------------
def test_strip_tail_after_period():
    src = (
        "tänava- ja numbrisilt. RT IV 2022-05-07 26 407052022026 2022-05-10 "
        "Käesoleva paragrahvi lõikes 1 …"
    )
    assert fix.strip_citation_tail(src) == "tänava- ja numbrisilt."


def test_strip_tail_after_semicolon():
    src = (
        "hauaplatsi ümbritsev maapinnaga kohtkindlalt ühendatud rajatis; "
        "RT IV 2020-10-01 2 401102020002 2020-10-04"
    )
    assert (
        fix.strip_citation_tail(src)
        == "hauaplatsi ümbritsev maapinnaga kohtkindlalt ühendatud rajatis"
    )


def test_strip_tail_removes_next_item_bleed():
    # The citation is followed by text bled in from the NEXT list item; the
    # whole tail (citation + foreign text) is cut at the RT marker.
    src = (
        "teenistuja palga ebaregulaarne osa, mida võib maksta tulemuspalgana; "
        "RT IV 2019-03-07 13 407032019013 2019-03-10 9) töötasu - tasu, mis …"
    )
    assert (
        fix.strip_citation_tail(src)
        == "teenistuja palga ebaregulaarne osa, mida võib maksta tulemuspalgana"
    )


def test_strip_handles_rt_variants():
    for sigil in ("RT I", "RT IV", "RT Õ", "RT ÕV"):
        src = f"päris definitsioon. {sigil} 2020-01-02 3 123 2020-01-05"
        assert fix.strip_citation_tail(src) == "päris definitsioon."


def test_citation_only_definition_becomes_empty():
    # No real text before the citation -> empty (left empty by design).
    assert fix.strip_citation_tail("RT IV 2019-05-02 4 402052019004 2019-05-05") == ""


def test_clean_definition_no_marker_is_noop():
    # A definition that merely mentions a year (no RT marker) is untouched.
    src = "leping, mis sõlmiti aastal 2019 ja kehtib edasi"
    assert fix.strip_citation_tail(src) == src


def test_clean_definition_bare_rt_without_date_untouched():
    # "RT" without a following ISO date must NOT trigger a cut.
    src = "dokument, mis avaldati RT teataja paberväljaandes"
    assert fix.strip_citation_tail(src) == src


def test_idempotent():
    src = "definitsioon. RT IV 2020-10-01 2 401102020002 2020-10-04"
    once = fix.strip_citation_tail(src)
    assert fix.strip_citation_tail(once) == once


# --------------------------------------------------------------------------
# migrate_doc — both LegalConcept (single) and Concept (list) shapes
# --------------------------------------------------------------------------
def test_migrate_doc_single_value_obj():
    doc = {
        "@graph": [
            {
                "@id": "estleg:Concept_x_lc",
                "skos:definition": {
                    "@value": "termin. RT IV 2020-10-01 2 401102020002 2020-10-04",
                    "@language": "et",
                },
            }
        ]
    }
    nodes, values = fix.migrate_doc(doc)
    assert nodes == 1
    assert values == 1
    out = doc["@graph"][0]["skos:definition"]
    assert out["@value"] == "termin."
    assert out["@language"] == "et"  # language tag preserved


def test_migrate_doc_list_valued():
    doc = {
        "@graph": [
            {
                "@id": "estleg:Concept_x",
                "skos:definition": [
                    {"@value": "esimene. RT IV 2020-10-01 2 401 2020-10-04", "@language": "et"},
                    {"@value": "teine puhas definitsioon", "@language": "et"},
                ],
            }
        ]
    }
    nodes, values = fix.migrate_doc(doc)
    assert nodes == 1
    assert values == 1  # only the first value changed
    out = doc["@graph"][0]["skos:definition"]
    assert out[0]["@value"] == "esimene."
    assert out[1]["@value"] == "teine puhas definitsioon"


def test_migrate_doc_clean_corpus_is_noop():
    doc = {
        "@graph": [
            {"@id": "estleg:C1", "skos:definition": {"@value": "puhas", "@language": "et"}},
        ]
    }
    assert fix.migrate_doc(doc) == (0, 0)


def test_process_file_roundtrip(tmp_path: Path):
    p = tmp_path / "concepts_combined.jsonld"
    p.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:C1",
                        "skos:definition": {
                            "@value": "x. RT IV 2020-10-01 2 401 2020-10-04",
                            "@language": "et",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    nodes, values = fix.process_file(p, dry_run=False)
    assert (nodes, values) == (1, 1)
    reloaded = json.loads(p.read_text(encoding="utf-8"))
    assert reloaded["@graph"][0]["skos:definition"]["@value"] == "x."


# --------------------------------------------------------------------------
# Generator backstop (extract_legal_concepts.py)
# --------------------------------------------------------------------------
def test_extract_definitions_strips_citation_tail():
    # The numbered-definition extractor must not leak an RT citation tail.
    text = (
        "1) hauaplats - hauaplatsi ümbritsev rajatis; "
        "RT IV 2020-10-01 2 401102020002 2020-10-04"
    )
    results = elc.extract_definitions_from_text(text)
    assert len(results) == 1
    _num, term, definition = results[0]
    assert term == "hauaplats"
    assert "RT" not in definition
    assert definition == "hauaplatsi ümbritsev rajatis"


def test_itertext_filtered_drops_avaldamismarge_subtree():
    # An <avaldamismarge> publication-mark node nested in a paragraph must be
    # dropped (text + children), but its tail kept.
    xml = (
        "<lipoke>termin tekst"
        "<avaldamismarge>RT IV 2020-10-01 2 401102020002 2020-10-04</avaldamismarge>"
        " jätkuv tekst</lipoke>"
    )
    el = ET.fromstring(xml)
    text = " ".join(elc._itertext_filtered(el))
    assert "RT" not in text
    assert "401102020002" not in text
    assert "termin tekst" in text
    assert "jätkuv tekst" in text


def test_collect_full_text_excludes_citation():
    xml = (
        "<root>päris definitsioon."
        "<avaldamismarge>RT IV 2019-03-07 13 407032019013 2019-03-10</avaldamismarge>"
        "</root>"
    )
    el = ET.fromstring(xml)
    out = elc.collect_full_text(el)
    assert out.startswith("päris definitsioon.")
    assert "RT" not in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
