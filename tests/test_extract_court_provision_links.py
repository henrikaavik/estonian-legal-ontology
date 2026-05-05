"""Tests for extract_court_provision_links.py — Layer 2c PR #3."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from extract_court_provision_links import expand_two_digit_year


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
