"""Tests for check_numeric_identity_strings.py (#569).

#569's narrowing makes it a no-op for the data: numeric identity/label fields
(``terviktekstId``, ``globalId``, ``actNumber``, ``ehakCode``,
``chapterNumber``, ``sectionNumber``, ``subsectionNumber``, ``annexNumber``)
must stay plain ``xsd:string`` literals, not ``xsd:integer`` value objects.
This guards that decision: a plain-string corpus passes; a numeric-typed
identity value is a violation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from estleg import check_numeric_identity_strings as mod
from estleg.check_numeric_identity_strings import (
    STRING_IDENTITY_PROPERTIES,
    _is_numeric_typed_object,
    find_violations,
    process_file,
)

# ── _is_numeric_typed_object ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ({"@value": "86", "@type": "xsd:integer"}, True),
        ({"@value": "86", "@type": "http://www.w3.org/2001/XMLSchema#integer"}, True),
        ({"@value": "5", "@type": "xsd:decimal"}, True),
        ({"@value": "5", "@type": ["xsd:integer"]}, True),
        # plain strings (the CORRECT representation) are never violations
        ("86", False),
        ("0130", False),  # leading-zero identifier
        ("III", False),  # Roman numeral
        ("1¹", False),  # superscript
        # a string-typed value object is fine
        ({"@value": "86", "@type": "xsd:string"}, False),
        ({"@value": "86", "@language": "et"}, False),
        ({"@value": "86"}, False),  # no @type at all
        ("", False),
        (None, False),
        (86, False),
    ],
)
def test_is_numeric_typed_object(value, expected):
    assert _is_numeric_typed_object(value) is expected


# ── find_violations ────────────────────────────────────────────────────────


def test_find_violations_flags_numeric_typed_identity():
    doc = {
        "@graph": [
            {
                "@id": "estleg:Act_X",
                # the violation: actNumber typed as integer
                "estleg:actNumber": {"@value": "86", "@type": "xsd:integer"},
            }
        ]
    }
    violations = find_violations(doc)
    assert len(violations) == 1
    node_id, prop, _value = violations[0]
    assert node_id == "estleg:Act_X"
    assert prop == "estleg:actNumber"


def test_find_violations_passes_plain_string_identity():
    doc = {
        "@graph": [
            {
                "@id": "estleg:Act_X",
                "estleg:actNumber": "86",
                "estleg:terviktekstId": "1047443",
                "estleg:ehakCode": "0130",  # leading zero preserved as string
                "estleg:chapterNumber": "III",  # Roman numeral
            }
        ]
    }
    assert find_violations(doc) == []


def test_find_violations_checks_list_valued_property():
    # A list-valued guarded property with one numeric-typed element is flagged.
    doc = {
        "@graph": [
            {
                "@id": "x",
                "estleg:chapterNumber": [
                    "1",
                    {"@value": "2", "@type": "xsd:integer"},
                ],
            }
        ]
    }
    violations = find_violations(doc)
    assert len(violations) == 1
    assert violations[0][1] == "estleg:chapterNumber"


def test_find_violations_ignores_unguarded_numeric_properties():
    # Count properties ARE legitimately xsd:integer — they must NOT be flagged.
    doc = {
        "@graph": [
            {
                "@id": "x",
                "estleg:definitionCount": {"@value": "12", "@type": "xsd:integer"},
            }
        ]
    }
    assert find_violations(doc) == []


def test_find_violations_no_graph_is_safe():
    assert find_violations({}) == []
    assert find_violations({"@graph": "not-a-list"}) == []
    assert find_violations({"@graph": [None, 5, "x"]}) == []


def test_guarded_set_matches_documented_decision():
    # The four identifiers + four positional label fields the #569 review
    # decided must stay strings.
    assert STRING_IDENTITY_PROPERTIES == frozenset(
        {
            "estleg:terviktekstId",
            "estleg:globalId",
            "estleg:actNumber",
            "estleg:ehakCode",
            "estleg:chapterNumber",
            "estleg:sectionNumber",
            "estleg:subsectionNumber",
            "estleg:annexNumber",
        }
    )


# ── process_file (LFS guard + IO) ──────────────────────────────────────────


def test_process_file_flags_violation(tmp_path: Path):
    path = tmp_path / "law_peep.json"
    path.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "x",
                        "estleg:globalId": {"@value": "112072019001", "@type": "xsd:integer"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rows = process_file(path)
    assert len(rows) == 1
    assert rows[0][0] == "law_peep.json"
    assert rows[0][2] == "estleg:globalId"


def test_process_file_clean(tmp_path: Path):
    path = tmp_path / "law_peep.json"
    path.write_text(
        json.dumps({"@graph": [{"@id": "x", "estleg:globalId": "112072019001"}]}),
        encoding="utf-8",
    )
    assert process_file(path) == []


def test_process_file_skips_lfs_pointer(tmp_path: Path):
    # An LFS pointer is not real JSON; the guard must skip it, not crash.
    path = tmp_path / "big_peep.json"
    path.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:deadbeef\nsize 123\n",
        encoding="utf-8",
    )
    assert process_file(path) == []


def test_process_file_skips_malformed_json(tmp_path: Path):
    path = tmp_path / "broken_peep.json"
    path.write_text("{ not valid json", encoding="utf-8")
    assert process_file(path) == []


# ── main against real corpus (idempotent, read-only) ───────────────────────


@pytest.mark.corpus
def test_main_passes_against_real_corpus():
    """The real corpus holds the #569 no-op decision: every guarded
    identity/label field is a plain string, so the guard exits 0."""
    assert mod.main([]) == 0
