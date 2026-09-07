"""#352 — implausible years must not become typed xsd:date values.

Covers the remaining claim sites: ``parse_act_metadata`` string-compare of
``aktikuupaev``, EUR-Lex ``_is_valid_iso_date``, and the committed
directives peep still carrying CELLAR 1001/1002 sentinel deadlines.
``extract_temporal_data.parse_date`` is already guarded; the assertions
here pin that so a later refactor cannot drop it.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from estleg import generate_eu_legislation as eu_mod
from estleg import validate_all
from estleg.extract_temporal_data import parse_date
from estleg.riigiteataja_common import parse_act_metadata

REPO_ROOT = Path(__file__).resolve().parent.parent
EURLEX_DIRECTIVES_PEEP = REPO_ROOT / "krr_outputs" / "eurlex" / "eurlex_directives_peep.json"
SENTINEL_DEADLINES = frozenset({"1001-01-01", "1002-02-02"})


def test_extract_temporal_parse_date_rejects_millennium_typo() -> None:
    assert parse_date("2918-10-17") is None


def test_extract_temporal_parse_date_rejects_eurlex_sentinel() -> None:
    assert parse_date("1001-01-01") is None


def test_is_valid_iso_date_rejects_eurlex_sentinel() -> None:
    assert eu_mod._is_valid_iso_date("1001-01-01") is False


def test_is_valid_iso_date_accepts_plausible_date() -> None:
    assert eu_mod._is_valid_iso_date("2024-06-15") is True


def test_parse_act_metadata_ignores_implausible_muutmismarge_date() -> None:
    root = ET.fromstring(
        "<akt>"
        "<metaandmed><kehtivus>"
        "<kehtivuseAlgus>2010-01-01+02:00</kehtivuseAlgus>"
        "</kehtivus></metaandmed>"
        "<muutmismarge><aktikuupaev>2918-10-17</aktikuupaev></muutmismarge>"
        "</akt>"
    )
    meta = parse_act_metadata(root)
    assert meta["lastAmendmentDate"] is None
    assert meta["lastAmendmentDate"] != "2918-10-17"


def test_parse_act_metadata_implausible_year_does_not_win_latest() -> None:
    """A 2918 typo used to beat 2018 under lexicographic comparison."""
    root = ET.fromstring(
        "<akt>"
        "<metaandmed><kehtivus>"
        "<kehtivuseAlgus>2918-10-17</kehtivuseAlgus>"
        "<kehtivuseLopp>1001-01-01</kehtivuseLopp>"
        "</kehtivus></metaandmed>"
        "<muutmismarge><aktikuupaev>2018-10-17</aktikuupaev></muutmismarge>"
        "<muutmismarge><aktikuupaev>2918-10-17+03:00</aktikuupaev></muutmismarge>"
        "</akt>"
    )
    meta = parse_act_metadata(root)
    assert meta["entryIntoForce"] is None
    assert meta["repealDate"] is None
    assert meta["lastAmendmentDate"] == "2018-10-17"


def test_eurlex_directives_peep_has_no_sentinel_deadlines() -> None:
    path = EURLEX_DIRECTIVES_PEEP
    if not path.is_file() or validate_all._is_lfs_pointer(path):
        pytest.skip("eurlex_directives_peep.json missing or an un-materialised LFS pointer")

    doc = json.loads(path.read_text(encoding="utf-8"))
    graph = doc.get("@graph", doc if isinstance(doc, list) else [doc])
    leftovers: list[str] = []
    for node in graph:
        if not isinstance(node, dict):
            continue
        raw = node.get("estleg:transpositionDeadline")
        if raw is None:
            continue
        value = raw.get("@value") if isinstance(raw, dict) else raw
        if value in SENTINEL_DEADLINES:
            leftovers.append(f"{node.get('@id')}:{value}")
    assert leftovers == [], (
        "CELLAR 1001/1002 sentinel transpositionDeadline values remain: "
        + ", ".join(leftovers)
    )
