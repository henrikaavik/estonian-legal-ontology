"""Regression tests for ``scripts/generate_missing_parts.py``."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

import generate_missing_parts


def _node_by_id(doc: dict, node_id: str) -> dict:
    for node in doc["@graph"]:
        if node.get("@id") == node_id:
            return node
    raise AssertionError(f"missing node {node_id}")


def test_vos_part_emits_subsections_with_dedup_guard() -> None:
    root = ET.fromstring(
        """
        <akt>
          <sisu>
            <osa>
              <osaNr>11</osaNr>
              <osaPealkiri>Testosa</osaPealkiri>
              <kuvatavNr>11. osa</kuvatavNr>
              <paragrahv>
                <paragrahvNr>100</paragrahvNr>
                <kuvatavNr>§ 100.</kuvatavNr>
                <paragrahvPealkiri>Testparagrahv</paragrahvPealkiri>
                <loige>
                  <loigeNr>1</loigeNr>
                  <kuvatavNr>(1)</kuvatavNr>
                  <tavatekst>Esimese lõike tekst.</tavatekst>
                </loige>
                <loige>
                  <loigeNr>2</loigeNr>
                  <kuvatavNr>(2)</kuvatavNr>
                  <tavatekst>Teise lõike tekst.</tavatekst>
                </loige>
              </paragrahv>
            </osa>
          </sisu>
        </akt>
        """
    )

    doc = generate_missing_parts.generate_vos_part(root, "fixture.xml", "11")
    assert doc is not None
    provision = _node_by_id(doc, "estleg:VOS_Osa11_Par_100")
    assert provision["estleg:hasSubsection"] == [
        {"@id": "estleg:VOS_Osa11_Par_100_Lg_1"},
        {"@id": "estleg:VOS_Osa11_Par_100_Lg_2"},
    ]

    duplicate_root = ET.fromstring(
        """
        <akt>
          <sisu>
            <osa>
              <osaNr>11</osaNr>
              <osaPealkiri>Testosa</osaPealkiri>
              <paragrahv>
                <paragrahvNr>100</paragrahvNr>
                <kuvatavNr>§ 100.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Esimene tekst.</tavatekst></loige>
              </paragrahv>
              <paragrahv>
                <paragrahvNr>100</paragrahvNr>
                <kuvatavNr>§ 100.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Duplikaat tekst.</tavatekst></loige>
              </paragrahv>
            </osa>
          </sisu>
        </akt>
        """
    )
    with pytest.raises(ValueError, match="Duplicate subsection IRI"):
        generate_missing_parts.generate_vos_part(duplicate_root, "fixture.xml", "11")


def test_tsus_part_subsection_emission_uses_ascii_iri_prefix() -> None:
    root = ET.fromstring(
        """
        <akt>
          <sisu>
            <osa>
              <osaNr>1</osaNr>
              <osaPealkiri>Üldsätted</osaPealkiri>
              <paragrahv>
                <paragrahvNr>1</paragrahvNr>
                <kuvatavNr>§ 1.</kuvatavNr>
                <paragrahvPealkiri>Seaduse ülesanne</paragrahvPealkiri>
                <loige>
                  <loigeNr>1</loigeNr>
                  <kuvatavNr>(1)</kuvatavNr>
                  <tavatekst>Tsiviilõiguse üldpõhimõtted.</tavatekst>
                </loige>
              </paragrahv>
            </osa>
          </sisu>
        </akt>
        """
    )

    doc = generate_missing_parts.generate_tsus_part1(root, "fixture.xml")
    assert doc is not None
    ids = {node["@id"] for node in doc["@graph"]}
    assert "estleg:TsUS_Osa1_Par_1_Lg_1" in ids
    assert all("TsÜS" not in node_id for node_id in ids)


def test_helpers_resolve_from_generate_all_laws() -> None:
    from scripts.generate_missing_parts import (  # noqa: PLC0415
        _iter_loiked,
        _loige_numbers,
        _paragraph_id_suffix,
        build_subsections,
        collect_full_text,
    )

    assert callable(_paragraph_id_suffix)
    assert callable(build_subsections)
    assert callable(_iter_loiked)
    assert callable(_loige_numbers)
    assert callable(collect_full_text)
