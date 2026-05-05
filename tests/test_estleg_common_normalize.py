"""Tests for normalize_issuer_name and BODY_CANON helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import BODY_CANON, normalize_issuer_name


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
