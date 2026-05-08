from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

import generate_all_laws


def test_has_existing_output_uses_exact_slug_not_short_prefix():
    existing = {"abcdefghijabcdefghij_other_law"}

    assert not generate_all_laws.has_existing_output(
        existing,
        "Different law",
        "abcdefghijabcdefghij_real_law",
    )


def test_law_stub_preserves_no_body_act():
    generate_all_laws._used_prefixes.clear()
    root = ET.fromstring("<akt><sisu /></akt>")

    doc = generate_all_laws.generate_law_stub_jsonld(
        "Rahvusvahelise lepingu ratifitseerimise seadus",
        "rahvusvahelise_lepingu_ratifitseerimise_seadus",
        root,
        "RLRS",
        "/akt/123.xml",
    )

    ontology = doc["@graph"][0]
    assert ontology["estleg:contentStatus"] == "noStructuredBody"
    assert "estleg:Act" in ontology["@type"]
    assert "estleg:Law" in ontology["@type"]
    assert ontology["dcterms:source"]["@id"].endswith("/akt/123.xml")


class _FailingResponse:
    def raise_for_status(self):
        raise RuntimeError("boom")


def test_get_all_laws_raises_on_source_list_failure(monkeypatch):
    monkeypatch.setattr(generate_all_laws.requests, "get", lambda *args, **kwargs: _FailingResponse())

    with pytest.raises(generate_all_laws.SourceListFetchError):
        generate_all_laws.get_all_laws()
