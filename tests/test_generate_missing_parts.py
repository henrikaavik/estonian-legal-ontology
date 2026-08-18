"""Regression tests for ``scripts/generate_missing_parts.py``."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from estleg import generate_missing_parts


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


def _provisions(doc: dict) -> list[dict]:
    return [n for n in doc["@graph"] if "estleg:paragrahv" in n]


def test_vos_provisions_link_to_act_root_via_part_of_act() -> None:
    """Issue #415: every VÕS provision must carry estleg:partOfAct → this
    osa's act root (LegalProvisionShape now requires the IRI join), so fresh
    generator output stays SHACL-conformant and graph-connected."""
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
    act_root = doc["@graph"][0]["@id"]
    provisions = _provisions(doc)
    assert provisions
    for prov in provisions:
        assert prov.get("estleg:partOfAct") == {"@id": act_root}


def test_tsus_provisions_link_to_act_root_via_part_of_act() -> None:
    """Issue #415: every TsÜS part-1 provision must carry estleg:partOfAct →
    estleg:TsUS_Osa1."""
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
    provisions = _provisions(doc)
    assert provisions
    for prov in provisions:
        assert prov.get("estleg:partOfAct") == {"@id": "estleg:TsUS_Osa1"}


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
    from estleg.generate_all_laws import slugify

    assert slugify("Võlaõigusseadus") == "volaoigusseadus"


def test_helpers_resolve_from_generate_all_laws() -> None:
    from estleg import generate_all_laws
    from estleg.generate_missing_parts import (
        _iter_loiked,
        _loige_numbers,
        _paragraph_id_suffix,
        build_subsections,
        collect_full_text,
        collect_text,
    )

    assert callable(_paragraph_id_suffix)
    assert callable(build_subsections)
    assert callable(_iter_loiked)
    assert callable(_loige_numbers)
    assert callable(collect_full_text)
    assert callable(collect_text)
    # The summary extractor must be the canonical, marker-pruned helper
    # from generate_all_laws — not a divergent local copy. (``is`` identity
    # is unreliable here: generate_missing_parts imports the bare
    # ``generate_all_laws`` module while the test imports the package-
    # qualified ``scripts.generate_all_laws``; these are distinct module-
    # cache entries, so compare provenance instead.)
    assert collect_text.__module__ in {
        "generate_all_laws",
        "law_structure",
        "estleg.generate_all_laws",
        "estleg.law_structure",
    }
    assert (
        collect_text.__code__.co_filename
        == generate_all_laws.collect_text.__code__.co_filename
    )


def test_vos_requested_cluster_is_iri_reference_not_bare_string() -> None:
    """#370 item 2: ``estleg:requestedCluster`` on a VÕS provision MUST be an
    IRI reference ``{"@id": ...}``, not a bare string literal (which JSON-LD
    would treat as an ``xsd:string`` and orphan the cluster)."""
    root = ET.fromstring(
        """
        <akt>
          <sisu>
            <osa>
              <osaNr>2</osaNr>
              <osaPealkiri>Lepingu üldosa</osaPealkiri>
              <kuvatavNr>2. osa</kuvatavNr>
              <peatykk>
                <peatykkNr>1</peatykkNr>
                <peatykkPealkiri>Lepingu mõiste</peatykkPealkiri>
                <paragrahv>
                  <paragrahvNr>8</paragrahvNr>
                  <kuvatavNr>§ 8.</kuvatavNr>
                  <paragrahvPealkiri>Leping</paragrahvPealkiri>
                  <loige><loigeNr>1</loigeNr><tavatekst>Leping on tehing.</tavatekst></loige>
                </paragrahv>
              </peatykk>
            </osa>
          </sisu>
        </akt>
        """
    )

    doc = generate_missing_parts.generate_vos_part(root, "fixture.xml", "2")
    assert doc is not None
    provision = _node_by_id(doc, "estleg:VOS_Osa2_Par_8")
    cluster = provision["estleg:requestedCluster"]
    assert isinstance(cluster, dict), (
        f"requestedCluster must be an IRI reference dict, got {cluster!r}"
    )
    assert not isinstance(cluster, str)
    assert set(cluster) == {"@id"}
    assert cluster["@id"].startswith("estleg:Cluster_VOS_2_")
    # The cluster the provision points at must actually exist in the graph.
    _node_by_id(doc, cluster["@id"])


def test_tsus_requested_cluster_is_iri_reference_not_bare_string() -> None:
    """#370 item 2: the hardcoded TsÜS Osa1 ``estleg:requestedCluster`` MUST
    be an IRI reference ``{"@id": ...}``, not a bare string literal."""
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
                <loige><loigeNr>1</loigeNr><tavatekst>Üldpõhimõtted.</tavatekst></loige>
              </paragrahv>
            </osa>
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
    assert provisions, "expected at least one TsÜS Osa1 provision"
    for provision in provisions:
        cluster = provision["estleg:requestedCluster"]
        assert isinstance(cluster, dict), (
            f"requestedCluster must be an IRI reference dict, got {cluster!r}"
        )
        assert not isinstance(cluster, str)
        assert cluster == {"@id": "estleg:Cluster_TsUS_Uldsatted"}
    # The referenced cluster individual must exist in the graph.
    _node_by_id(doc, "estleg:Cluster_TsUS_Uldsatted")


def test_summary_does_not_leak_amendment_marker() -> None:
    """Round-1 finding (near #298) / #255: the local ``collect_text`` used to
    walk the raw tree and leak amendment-marker text into ``estleg:summary``.

    A ``muutmismarge``/``avaldamismarge`` subtree nested inside the paragraph
    (or inside a ``loige``) MUST NOT appear in the emitted summary; only the
    genuine ``loige`` body text should.
    """
    root = ET.fromstring(
        """
        <akt>
          <sisu>
            <osa>
              <osaNr>2</osaNr>
              <osaPealkiri>Lepingu üldosa</osaPealkiri>
              <paragrahv>
                <paragrahvNr>9</paragrahvNr>
                <kuvatavNr>§ 9.</kuvatavNr>
                <paragrahvPealkiri>Leping</paragrahvPealkiri>
                <avaldamismarge>
                  <tavatekst>terviktekst RT paberkandjal</tavatekst>
                </avaldamismarge>
                <loige>
                  <loigeNr>1</loigeNr>
                  <tavatekst>Ehtne sätte sisu kehtib.</tavatekst>
                  <muutmismarge>
                    <tavatekst>Kehtetu -</tavatekst>
                    <tavatekst>(jõustumine muudetud - RT I, 03.03.2019, 7)</tavatekst>
                  </muutmismarge>
                </loige>
                <joustumismarge>
                  <lause>(jõustumine muudetud - RT I, 01.01.2020, 1)</lause>
                </joustumismarge>
              </paragrahv>
            </osa>
          </sisu>
        </akt>
        """
    )

    doc = generate_missing_parts.generate_vos_part(root, "fixture.xml", "2")
    assert doc is not None
    provision = _node_by_id(doc, "estleg:VOS_Osa2_Par_9")
    summary = provision["estleg:summary"]
    assert "Ehtne sätte sisu kehtib." in summary
    for leaked in (
        "terviktekst RT paberkandjal",
        "Kehtetu -",
        "jõustumine muudetud",
    ):
        assert leaked not in summary, (
            f"amendment-marker text leaked into estleg:summary: {leaked!r}"
        )


# ---------------------------------------------------------------------------
# save_json atomicity (#601 residual)
# ---------------------------------------------------------------------------

def _sample_doc() -> dict:
    return {
        "@context": {"estleg": "https://w3id.org/estleg/"},
        "@graph": [
            {"@id": "estleg:VOS_Osa6", "rdfs:label": "Õigus — ä ü test"},
        ],
    }


def test_save_json_writes_correct_content_and_formatting(tmp_path) -> None:
    """``save_json`` must round-trip the document and preserve the canonical
    formatting (``ensure_ascii=False``, ``indent=2``, trailing newline) so the
    atomic-write refactor (#601) leaves output bytes unchanged."""
    target = tmp_path / "out.json"
    doc = _sample_doc()

    generate_missing_parts.save_json(target, doc)

    assert target.exists()
    raw = target.read_text(encoding="utf-8")
    assert json.loads(raw) == doc
    # ensure_ascii=False -> non-ASCII stays literal, never \u-escaped.
    assert "Õigus — ä ü test" in raw
    assert "\\u" not in raw
    # indent=2 + single trailing newline.
    assert raw.endswith("}\n")
    assert not raw.endswith("}\n\n")
    assert '\n  "@graph"' in raw


def test_save_json_failure_leaves_existing_target_intact(tmp_path) -> None:
    """#601 residual: the write must be atomic. A failure mid-serialisation
    must NOT truncate or corrupt a pre-existing peep at the live path, nor
    leave a partial ``.tmp`` dropping behind.

    Regression guard: the previous ``open(target, "w")`` truncated the target
    *before* ``json.dump`` ran, so a mid-dump crash destroyed the live file.
    """
    target = tmp_path / "out.json"
    original = '{"keep": "me"}\n'
    target.write_text(original, encoding="utf-8")

    # A set is not JSON-serialisable: json.dump streams the serialisable
    # prefix, then raises TypeError partway through the write.
    poison = {"@graph": ["ok"], "bad": {1, 2, 3}}

    with pytest.raises(TypeError):
        generate_missing_parts.save_json(target, poison)

    # Original survives byte-for-byte ...
    assert target.read_text(encoding="utf-8") == original
    # ... and no tempfile dropping is left (iterdir sees dotfiles too).
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]


def test_save_json_failure_writes_no_partial_file_at_target(tmp_path) -> None:
    """When the target does not pre-exist, a failed write must leave NO file
    at the target path and no tempfile dropping — the old direct
    ``open(target, "w")`` would have created an empty/truncated file."""
    target = tmp_path / "fresh.json"
    poison = {"@graph": ["ok"], "bad": {1, 2, 3}}

    with pytest.raises(TypeError):
        generate_missing_parts.save_json(target, poison)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_save_json_uses_temp_then_atomic_replace(tmp_path, monkeypatch) -> None:
    """``save_json`` must route through a sibling tempfile + ``os.replace``
    onto the target (never a direct open-write-truncate). Spy on os.replace
    and assert the source is a same-directory tempfile and the destination is
    the target — this is what makes the write crash-atomic (#601)."""
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def _spy(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", _spy)

    target = tmp_path / "out.json"
    generate_missing_parts.save_json(target, _sample_doc())

    assert len(calls) == 1, "expected exactly one atomic os.replace onto target"
    src, dst = calls[0]
    assert dst == str(target)
    assert src != str(target)
    # Same-directory tempfile is what guarantees os.replace is atomic.
    assert Path(src).parent == target.parent
    assert target.exists()
