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


def test_tsus_osa1_flat_marker_falls_through_to_section_scan() -> None:
    """Regression #269 (mechanism 1): a flat ``<osa>`` marker for Osa 1
    (paragraphs are siblings, not descendants) MUST fall through to the
    §§ 1-7 scan instead of returning an empty artifact.

    Pre-fix: ``find_osa`` returned the non-None marker, the ``if osa is
    None:`` fallback never ran, ``extract_paragrahvid(osa)`` was 0, and
    TsÜS Osa1 shipped with **zero provisions**.
    """
    root = ET.fromstring(
        """
        <akt>
          <sisu>
            <osa>
              <osaNr>1</osaNr>
              <kuvatavNr>1. osa</kuvatavNr>
              <osaPealkiri>ÜLDSÄTTED</osaPealkiri>
            </osa>
            <peatykk>
              <peatykkNr>1</peatykkNr>
              <paragrahv>
                <paragrahvNr>1</paragrahvNr>
                <kuvatavNr>§ 1.</kuvatavNr>
                <paragrahvPealkiri>Seaduse ülesanne</paragrahvPealkiri>
                <loige><loigeNr>1</loigeNr><tavatekst>Esimene.</tavatekst></loige>
              </paragrahv>
              <paragrahv>
                <paragrahvNr>2</paragrahvNr>
                <kuvatavNr>§ 2.</kuvatavNr>
                <paragrahvPealkiri>Hea usu põhimõte</paragrahvPealkiri>
                <loige><loigeNr>1</loigeNr><tavatekst>Teine.</tavatekst></loige>
              </paragrahv>
            </peatykk>
          </sisu>
        </akt>
        """
    )

    doc = generate_missing_parts.generate_tsus_part1(root, "fixture.xml")
    assert doc is not None
    provisions = [
        n
        for n in doc["@graph"]
        if "estleg:LegalProvision_TsUS_osa1" in (n.get("@type") or [])
    ]
    assert len(provisions) == 2, (
        "flat Osa1 marker must fall through to the §§-scan and emit "
        f"provisions; got {len(provisions)}"
    )
    provision_ids = {p["@id"] for p in provisions}
    assert provision_ids == {
        "estleg:TsUS_Osa1_Par_1",
        "estleg:TsUS_Osa1_Par_2",
    }


def test_tsus_osa1_act_id_is_snapshot_stable() -> None:
    """Regression #269c: the TsÜS Osa1 act @id MUST NOT embed the
    volatile §min–§max range (so it does not churn between redactions)."""
    root = ET.fromstring(
        """
        <akt>
          <sisu>
            <osa>
              <osaNr>1</osaNr>
              <osaPealkiri>Üldsätted</osaPealkiri>
              <paragrahv>
                <paragrahvNr>3</paragrahvNr>
                <kuvatavNr>§ 3.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>
              </paragrahv>
            </osa>
          </sisu>
        </akt>
        """
    )
    doc = generate_missing_parts.generate_tsus_part1(root, "fixture.xml")
    assert doc is not None
    assert doc["@graph"][0]["@id"] == "estleg:TsUS_Osa1"
    # The §range still appears in the human-readable label.
    assert "§3" in doc["@graph"][0]["rdfs:label"]


def test_vos_act_id_is_snapshot_stable() -> None:
    """Regression #269c: the VÕS act @id is identified by osa number
    alone, not the paragraph range."""
    root = ET.fromstring(
        """
        <akt>
          <sisu>
            <osa>
              <osaNr>6</osaNr>
              <osaPealkiri>Kindlustuslepingud</osaPealkiri>
              <paragrahv>
                <paragrahvNr>422</paragrahvNr>
                <kuvatavNr>§ 422.</kuvatavNr>
                <loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>
              </paragrahv>
            </osa>
          </sisu>
        </akt>
        """
    )
    doc = generate_missing_parts.generate_vos_part(root, "fixture.xml", "6")
    assert doc is not None
    assert doc["@graph"][0]["@id"] == "estleg:VOS_Osa6"


def test_vos_output_slug_matches_generate_all_laws(tmp_path, monkeypatch) -> None:
    """Regression #269 (mechanism 2): the VÕS per-osa output file MUST be
    written under the canonical ``volaoigusseadus`` slug (õ→o), matching
    ``generate_all_laws.slugify("Võlaõigusseadus")`` — not the
    ``volaigusseadus`` typo that produced duplicate divergent files.
    """
    vos_xml = tmp_path / "vos.xml"
    vos_xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
        <akt>
          <sisu>
            <osa>
              <osaNr>6</osaNr>
              <osaPealkiri>Kindlustuslepingud</osaPealkiri>
              <kuvatavNr>6. osa</kuvatavNr>
              <paragrahv>
                <paragrahvNr>422</paragrahvNr>
                <kuvatavNr>§ 422.</kuvatavNr>
                <paragrahvPealkiri>Kindlustusleping</paragrahvPealkiri>
                <loige><loigeNr>1</loigeNr><tavatekst>Tekst.</tavatekst></loige>
              </paragrahv>
            </osa>
          </sisu>
        </akt>
        """,
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    rc = generate_missing_parts.main(
        [
            "--input-xml-vos",
            str(vos_xml),
            "--vos-osa",
            "6",
            "--skip-tsus",
            "--output-dir",
            str(out_dir),
        ]
    )
    assert rc == 0
    produced = {p.name for p in out_dir.glob("*.json")}
    assert "volaoigusseadus_osa6_peep.json" in produced, produced
    # The typo slug must never be emitted again.
    assert "volaigusseadus_osa6_peep.json" not in produced

    # Canonical slug must equal generate_all_laws.slugify of the title.
    from scripts.generate_all_laws import slugify  # noqa: PLC0415

    assert slugify("Võlaõigusseadus") == "volaoigusseadus"


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
