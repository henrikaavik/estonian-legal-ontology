"""Unified ``sanitize_id`` contract (issue #449).

One implementation lives in ``estleg_common``. Callers must import it (or
agree on the same outputs) rather than keep a private copy that mints a
different IRI from the same input.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from estleg_common import sanitize_id

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_DEF_SANITIZE_ID = re.compile(r"^def sanitize_id\b", re.M)
CROSS_GENERATOR_FIXTURES = ("1–94", "194", "ÄS", "3-5", "Karistus")


def _scripts_defining_sanitize_id() -> list[str]:
    offenders: list[str] = []
    for path in sorted(SCRIPTS.glob("*.py")):
        if path.name == "estleg_common.py":
            continue
        text = path.read_text(encoding="utf-8")
        if _DEF_SANITIZE_ID.search(text):
            offenders.append(path.name)
    return offenders


def test_transliterates_estonian_capital_a_umlaut() -> None:
    assert sanitize_id("Äriseadustik") == "Ariseadustik"


def test_endash_numeric_range_does_not_collide_with_plain_number() -> None:
    assert sanitize_id("1–94") == "1_to_94"
    assert sanitize_id("1–94") != sanitize_id("194")


def test_ascii_hyphen_numeric_range_canonicalises() -> None:
    assert sanitize_id("1-94") == "1_to_94"


def test_non_numeric_hyphen_is_still_stripped() -> None:
    assert sanitize_id("a-b") == "ab"


def test_court_style_flags_replace_dashes() -> None:
    assert (
        sanitize_id(
            "3-1-1-14-20",
            replace_dash=True,
            replace_slash=True,
            canonicalize_ranges=False,
            unknown_char="_",
            max_len=80,
        )
        == "3_1_1_14_20"
    )


def test_draft_style_flags_keep_eis_number() -> None:
    assert sanitize_id("JDM/26-0099", replace_dash=True, max_len=80) == "JDM26_0099"


def test_generate_all_laws_shares_or_agrees() -> None:
    # Caller-migration may still be landing. A leftover local body is
    # reported by ``test_sanitize_id_defined_only_in_estleg_common``;
    # skip the identity/agreement check rather than fail this file twice.
    gal_src = (SCRIPTS / "generate_all_laws.py").read_text(encoding="utf-8")
    if _DEF_SANITIZE_ID.search(gal_src):
        pytest.skip(
            "generate_all_laws still has a local sanitize_id body; "
            "caller-migration agent may still be running (#449)"
        )

    from generate_all_laws import sanitize_id as gal_sanitize_id

    if gal_sanitize_id is sanitize_id:
        return
    for value in CROSS_GENERATOR_FIXTURES:
        assert gal_sanitize_id(value) == sanitize_id(value), value


def test_sanitize_id_defined_only_in_estleg_common() -> None:
    """DoD #449: the only ``def sanitize_id`` in scripts/ is estleg_common.

    Thin wrappers that call ``estleg_common.sanitize_id`` are acceptable
    only if they are not themselves named ``sanitize_id``.
    """
    common_src = (SCRIPTS / "estleg_common.py").read_text(encoding="utf-8")
    assert _DEF_SANITIZE_ID.search(common_src), (
        "estleg_common must own the real sanitize_id implementation (#449)"
    )

    offenders = _scripts_defining_sanitize_id()
    assert offenders == [], (
        "sanitize_id must be defined only in estleg_common (#449); "
        f"still defined in: {', '.join(offenders)}"
    )
