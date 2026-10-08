"""Regression tests for #694: superscripts and sub-points in statutory text."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from estleg.law_structure import (
    _loige_body_text,
    _loige_item_numbers,
    _marker_pruned_text,
    _sup_to_unicode,
    build_subsections,
    collect_full_text,
    collect_text,
)
from estleg.riigiteataja_common import ln

FIXTURE = Path(__file__).parent / "fixtures" / "rt_law_sup_alampunkt.xml"


@pytest.fixture(scope="module")
def paragraphs() -> dict[str, ET.Element]:
    root = ET.parse(FIXTURE).getroot()
    return {
        el.attrib["id"]: el for el in root.iter() if ln(el.tag) == "paragrahv"
    }


def _loige(par: ET.Element, nr: str) -> ET.Element:
    for child in par:
        if ln(child.tag) == "loige" and child.attrib["id"].endswith(f"lg{nr}"):
            return child
    raise AssertionError(f"lõige {nr} not in fixture")


def _subsections(par: ET.Element, par_suffix: str) -> dict[str, dict]:
    nodes = build_subsections(
        par,
        f"estleg:KARIST_Par_{par_suffix}",
        abbrev_prefix="KARIST",
        par_suffix=par_suffix,
        paragraph_display=f"§ {par_suffix}.",
    )
    return {node["@id"]: node for node in nodes}


# (a) real <sup> child inside tavatekst -> Unicode superscript


def test_sup_child_element_becomes_unicode_superscript() -> None:
    el = ET.fromstring("<tavatekst>§-s 217<sup>2</sup>, 298<sup>1</sup> või 400</tavatekst>")
    assert _marker_pruned_text(el) == "§-s 217², 298¹ või 400"


def test_sup_child_in_fixture_survives_lõige_and_provision_text(paragraphs) -> None:
    par = paragraphs["para49b1"]
    body = _loige_body_text(_loige(par, "1"))
    assert "§-s 213, 217², 298, 298¹ või 400" in body
    assert "2172" not in body and "2981" not in body
    full = collect_full_text(par)
    assert full.startswith("(1) Kohus võib")
    assert "217²" in full and "298¹" in full
    assert "217²" in collect_text(par)
    node = _subsections(par, "49_1")["estleg:KARIST_Par_49_1_Lg_1"]
    assert "217², 298, 298¹" in node["estleg:legalText"]


def test_sup_child_in_namespaced_xml() -> None:
    el = ET.fromstring(
        '<tavatekst xmlns="tyviseadus_1_10.02.2010">§ 153<sup>1</sup> lõige 2</tavatekst>'
    )
    assert _marker_pruned_text(el) == "§ 153¹ lõige 2"


def test_sup_child_tail_and_marker_pruning_still_apply() -> None:
    el = ET.fromstring(
        "<lause>LS § 261<sup>6</sup>–261<sup>9</sup>"
        "<muutmismarge><tavatekst>RT I 2004</tavatekst></muutmismarge> kehtivad.</lause>"
    )
    assert _marker_pruned_text(el) == "LS § 261⁶–261⁹ kehtivad."


# (b) escaped <sup> string still converts


def test_escaped_sup_string_still_converts() -> None:
    assert _sup_to_unicode("217<sup>2</sup>") == "217²"
    el = ET.fromstring("<tavatekst>MKS §-s 153&lt;sup&gt;1&lt;/sup&gt; sätestatud</tavatekst>")
    wrapper = ET.Element("loige")
    wrapper.append(el)
    assert _loige_body_text(wrapper) == "MKS §-s 153¹ sätestatud"


def test_already_converted_text_is_idempotent() -> None:
    once = _sup_to_unicode("§ 217² ja 298¹")
    assert _sup_to_unicode(once) == once


# (c) lõige with two alampunkt children keeps markers + itemNumber


def test_alampunkt_markers_kept_in_lõige_body(paragraphs) -> None:
    body = _loige_body_text(_loige(paragraphs["para7"], "1"))
    assert "karistusõigus ning: 1) tegu on toime pandud" in body
    assert "isiku vastu või 2) teo toimepanija" in body
    # The muutmismarge inside alampunkt 2 must not leak.
    assert "RT I" not in body


def test_alampunkt_item_numbers_from_alampunktNr(paragraphs) -> None:
    loige = _loige(paragraphs["para7"], "1")
    assert _loige_item_numbers(loige, _loige_body_text(loige)) == ["1", "2"]
    node = _subsections(paragraphs["para7"], "7")["estleg:KARIST_Par_7_Lg_1"]
    assert node["estleg:itemNumber"] == ["1", "2"]
    assert node["estleg:legalText"].startswith("(1) Eesti karistusseadus")
    assert " 1) tegu " in node["estleg:legalText"]


def test_superscripted_alampunkt_keeps_distinct_number(paragraphs) -> None:
    loige = _loige(paragraphs["para209"], "2")
    body = _loige_body_text(loige)
    assert "kelmuse; 1¹) ametiisiku poolt" in body
    assert _loige_item_numbers(loige, body) == ["1", "1¹"]


def test_provision_text_keeps_alampunkt_markers(paragraphs) -> None:
    full = collect_full_text(paragraphs["para7"])
    assert "ning: 1) tegu" in full and "või 2) teo" in full
    assert "(2) Eesti karistusseadus" in full


def test_alampunkt_marker_falls_back_to_number() -> None:
    loige = ET.fromstring(
        "<loige><loigeNr>1</loigeNr><sisuTekst><tavatekst>Loetelu:</tavatekst></sisuTekst>"
        "<alampunkt><alampunktNr>3</alampunktNr>"
        "<sisuTekst><tavatekst>kolmas punkt</tavatekst></sisuTekst></alampunkt></loige>"
    )
    assert _loige_body_text(loige) == "Loetelu: 3) kolmas punkt"
    assert _loige_item_numbers(loige, "") == ["3"]


# (d) lõige without sub-points gets no itemNumber


def test_lõige_without_sub_points_has_no_item_number(paragraphs) -> None:
    loige = _loige(paragraphs["para7"], "2")
    assert _loige_item_numbers(loige, _loige_body_text(loige)) == []
    node = _subsections(paragraphs["para7"], "7")["estleg:KARIST_Par_7_Lg_2"]
    assert "estleg:itemNumber" not in node
    assert ")" not in node["estleg:legalText"].split(" ", 1)[1]
