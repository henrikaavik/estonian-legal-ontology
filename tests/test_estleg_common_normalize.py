"""Tests for common normalization and JSON-LD text helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import BODY_CANON, jsonld_text, jsonld_texts, normalize_issuer_name


def test_normalize_issuer_lowercase() -> None:
    assert normalize_issuer_name("Põlva Vallavalitsus") == "polva vallavalitsus"


def test_normalize_issuer_uppercase() -> None:
    assert normalize_issuer_name("PÕLVA VALLAVALITSUS") == "polva vallavalitsus"


def test_normalize_issuer_diacritic_transliteration() -> None:
    # Õ→o, Ä→a, Ö→o, Ü→u, Š→s, Ž→z
    assert normalize_issuer_name("Tõrva Linnavolikogu") == "torva linnavolikogu"
    assert normalize_issuer_name("Häädemeeste Vallavolikogu") == "haademeeste vallavolikogu"


def test_normalize_issuer_collapses_whitespace() -> None:
    assert normalize_issuer_name("  Tartu   Linnavolikogu  ") == "tartu linnavolikogu"


def test_body_canon_genitive_to_nominative() -> None:
    assert BODY_CANON["linnavalitsuse"] == "linnavalitsus"
    assert BODY_CANON["vallavalitsuse"] == "vallavalitsus"
    assert BODY_CANON["linnavalitsus"] == "linnavalitsus"
    assert BODY_CANON["vallavalitsus"] == "vallavalitsus"


def test_body_canon_volikogu_passthrough() -> None:
    # Volikogu forms are identical in nominative and the genitive used in court text.
    assert BODY_CANON["linnavolikogu"] == "linnavolikogu"
    assert BODY_CANON["vallavolikogu"] == "vallavolikogu"


def test_jsonld_text_unwraps_common_value_shapes() -> None:
    assert jsonld_text("plain") == "plain"
    assert jsonld_text({"@value": "keel", "@language": "et"}) == "keel"
    assert jsonld_text([{"@value": "üks"}, {"@value": "kaks"}]) == "üks kaks"
    assert jsonld_text({"@id": "estleg:Thing"}, default="") == ""


def test_jsonld_text_prefers_requested_language() -> None:
    value = [
        {"@value": "one", "@language": "en"},
        {"@value": "üks", "@language": "et"},
    ]

    assert jsonld_text(value, prefer_language="et") == "üks"
    assert jsonld_text(value, prefer_language="de") == "one üks"


def test_jsonld_text_preference_keeps_untagged_values() -> None:
    assert jsonld_text({"@value": "üks"}, prefer_language="et") == "üks"


def test_jsonld_texts_prefers_requested_language() -> None:
    value = [
        {"@value": "one", "@language": "en"},
        {"@value": "keel"},
        {"@value": "üks", "@language": "et"},
        {"@value": "kaks", "@language": "et"},
    ]

    assert jsonld_texts(value, prefer_language="et") == ["keel", "üks", "kaks"]
    assert jsonld_texts(value, prefer_language="de") == ["keel"]
    assert jsonld_texts(value) == ["one", "keel", "üks", "kaks"]
