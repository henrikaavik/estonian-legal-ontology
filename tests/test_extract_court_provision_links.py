"""Tests for extract_court_provision_links.py — Layer 2c PR #3."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from extract_court_provision_links import expand_two_digit_year
from extract_court_provision_links import PAT_KOV_ACT


# ---------------------------------------------------------------------------
# Group 1a — expand_two_digit_year
# ---------------------------------------------------------------------------

def test_year_99_to_1999() -> None:
    assert expand_two_digit_year("21.06.99") == 1999


def test_year_51_to_1951() -> None:
    assert expand_two_digit_year("01.01.51") == 1951


def test_year_50_to_2050_boundary() -> None:
    # y > 50 means y == 50 falls into the 20xx bucket
    assert expand_two_digit_year("01.01.50") == 2050


def test_year_25_to_2025() -> None:
    assert expand_two_digit_year("13.10.25") == 2025


def test_year_00_to_2000() -> None:
    assert expand_two_digit_year("13.10.00") == 2000


def test_year_four_digit_passthrough() -> None:
    assert expand_two_digit_year("13.10.2009") == 2009


def test_year_long_form_estonian_month() -> None:
    assert expand_two_digit_year("18. juuni 2020") == 2020


# ---------------------------------------------------------------------------
# Group 1b — PAT_KOV_ACT regex
# ---------------------------------------------------------------------------

def _first_match(text: str):
    return PAT_KOV_ACT.search(text)


def test_pat_numeric_date_genitive_body() -> None:
    m = _first_match("Keila Vallavolikogu 21.06.99 määrus nr 55")
    assert m is not None
    assert m.group("municipality").strip() == "Keila"
    assert m.group("body").lower() == "vallavolikogu"
    assert m.group("date") == "21.06.99"
    assert m.group("num") == "55"


def test_pat_long_date_form() -> None:
    m = _first_match("Tallinna Linnavolikogu 18. juuni 2020. a määruse nr 15")
    assert m is not None
    assert m.group("municipality").strip() == "Tallinna"
    assert m.group("body").lower() == "linnavolikogu"
    assert m.group("date") == "18. juuni 2020"
    assert m.group("num") == "15"


def test_pat_instrumental() -> None:
    m = _first_match("Tallinna Linnavalitsuse 14.05.2009 määrusega nr 23")
    assert m is not None
    assert m.group("num") == "23"


def test_pat_nominative_body() -> None:
    m = _first_match("Tallinna Linnavalitsus 14.05.2009 määrus nr 23")
    assert m is not None
    assert m.group("body").lower() == "linnavalitsus"


def test_pat_no_match_vague_plural() -> None:
    assert _first_match("Pärnu Linnavalitsuse määruste peale") is None


def test_pat_no_match_case_name() -> None:
    # "Tallinna Linna määruskaebus" is a case-name pattern, not an act citation.
    assert _first_match("Tallinna Linna määruskaebus Tallinna Ringkonnakohtu otsuse peale") is None


def test_pat_no_overcapture_lowercase_prefix_long() -> None:
    # Pins the case-sensitive municipality match against IGNORECASE-driven regression.
    m = _first_match("Õiguskantsleri taotlus Tallinna Linnavolikogu 19.02.2009 määruse nr 3 muutmise kohta")
    assert m is not None
    assert m.group("municipality").strip() == "Tallinna"


def test_pat_no_overcapture_lowercase_prefix_short() -> None:
    m = _first_match("tunnistada kehtetuks Keila Vallavolikogu 21.06.99 määrus nr 55")
    assert m is not None
    assert m.group("municipality").strip() == "Keila"


from extract_court_provision_links import PAT_RTIV


def test_pat_rtiv_canonical_form() -> None:
    assert PAT_RTIV.search("vt RT IV, 2020-04-15, 7") is not None


def test_pat_rtiv_alternate_form() -> None:
    assert PAT_RTIV.search("avaldatud RT IV 2018, 3, 5") is not None


def test_pat_rtiv_no_match_plain_text() -> None:
    assert PAT_RTIV.search("KarS § 121") is None
