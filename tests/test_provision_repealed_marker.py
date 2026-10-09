"""#703 follow-up: a § Riigi Teataja marks "Kehtetu -" is flagged, not counted as missing text."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from estleg import check_text_fidelity as fidelity
from estleg.law_structure import is_repealed_paragraph

NS = "tyviseadus_1_10.02.2010"


def _par(inner: str) -> ET.Element:
    return ET.fromstring(f'<paragrahv xmlns="{NS}" id="para12">{inner}</paragrahv>')


def test_repealed_in_amendment_note() -> None:
    par = _par(
        "<paragrahvNr>12</paragrahvNr><kuvatavNr>§ 12. </kuvatavNr>"
        "<muutmismarge><tavatekst>Kehtetu - </tavatekst>"
        "<joustumine>2026-07-01</joustumine></muutmismarge>"
    )
    assert is_repealed_paragraph(par)


def test_repealed_as_paragraph_text() -> None:
    par = _par("<paragrahvNr>5</paragrahvNr><sisuTekst><tavatekst>Kehtetu - </tavatekst></sisuTekst>")
    assert is_repealed_paragraph(par)


def test_paragraph_without_note_is_not_repealed() -> None:
    par = _par("<paragrahvNr>7</paragrahvNr><sisuTekst><tavatekst>Seadus jõustub.</tavatekst></sisuTekst>")
    assert not is_repealed_paragraph(par)


def test_coverage_exempts_marked_paragraphs(tmp_path, monkeypatch) -> None:
    doc = {
        "@graph": [
            {"@id": "estleg:X_Map", "@type": ["estleg:Act", "estleg:Law"],
             "estleg:contentStatus": "structuredBody", "dcterms:source": "https://www.riigiteataja.ee/akt/1",
             "estleg:kehtiv": "2026-10-09"},
            {"@id": "estleg:X_Par_1", "@type": ["estleg:LegalProvision"], "estleg:paragrahv": "§ 1."},
            {"@id": "estleg:X_Par_2", "@type": ["estleg:LegalProvision"], "estleg:paragrahv": "§ 2.",
             "estleg:provisionRepealed": True},
            {"@id": "estleg:X_Par_3", "@type": ["estleg:LegalProvision"], "estleg:paragrahv": "§ 3.",
             "estleg:legalText": "Tekst."},
        ]
    }
    monkeypatch.setattr(fidelity, "iter_root_law_docs", lambda krr=None: iter([(tmp_path / "x_peep.json", doc)]))
    offenders = fidelity.measure_coverage(tmp_path)
    assert offenders["missing_legal_text"] == ["estleg:X_Par_1"]
