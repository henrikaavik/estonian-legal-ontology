"""#703 follow-up: a § Riigi Teataja marks "Kehtetu -" is flagged, not counted as missing text."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from estleg import check_text_fidelity as fidelity
from estleg.law_structure import (
    collect_full_text,
    html_container_text,
    is_omitted_paragraph,
    is_repealed_paragraph,
)

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


def test_batch_repeal_plural_placeholder() -> None:
    # RT writes "Kehtetud -" when one note repeals several §§ (ABIPOL § 26,
    # AOSRS § 4); the number carries kehtiv="0" and the note element an id.
    par = _par(
        '<paragrahvNr kehtiv="0">26</paragrahvNr><kuvatavNr>§ 26. </kuvatavNr>'
        '<muutmismarge id="m1"><tavatekst id="t1">Kehtetud - </tavatekst>'
        "<joustumine>2014-07-01</joustumine></muutmismarge>"
    )
    assert is_repealed_paragraph(par)


def test_not_in_force_number_with_a_body_is_not_repealed() -> None:
    # kehtiv="0" alone is not a repeal: 463 such §§ in the cache still have
    # text (not yet in force). The caller only asks when no text was found.
    par = _par(
        '<paragrahvNr kehtiv="0">9</paragrahvNr>'
        "<loige><sisuTekst><tavatekst>Jõustub 2027.</tavatekst></sisuTekst></loige>"
    )
    assert not is_repealed_paragraph(par)


def test_omitted_spent_paragraph_dash_placeholder() -> None:
    # ABIPOL § 17 / AOSRS § 3: kehtiv="0" number, body is a lone dash.
    par = _par(
        '<paragrahvNr kehtiv="0">17</paragrahvNr><kuvatavNr>§ 17. </kuvatavNr>'
        "<sisuTekst><tavatekst> – </tavatekst></sisuTekst>"
    )
    assert is_omitted_paragraph(par)
    assert not is_repealed_paragraph(par)


def test_omitted_explicit_note() -> None:
    # BIOTSI § 7: the note says so, the number carries no kehtiv attribute.
    par = _par(
        "<paragrahvNr>7</paragrahvNr><kuvatavNr>§ 7. </kuvatavNr>"
        "<muutmismarge><tavatekst>Välja jäetud - </tavatekst>"
        "<joustumine>2010-07-09</joustumine></muutmismarge>"
    )
    assert is_omitted_paragraph(par)
    assert not is_repealed_paragraph(par)


def test_dash_without_kehtiv_zero_is_not_omitted() -> None:
    par = _par("<paragrahvNr>17</paragrahvNr><sisuTekst><tavatekst> – </tavatekst></sisuTekst>")
    assert not is_omitted_paragraph(par)


def test_kehtiv_zero_with_text_is_not_omitted() -> None:
    par = _par(
        '<paragrahvNr kehtiv="0">9</paragrahvNr>'
        "<loige><sisuTekst><tavatekst>Jõustub 2027.</tavatekst></sisuTekst></loige>"
    )
    assert not is_omitted_paragraph(par)


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
            {"@id": "estleg:X_Par_4", "@type": ["estleg:LegalProvision"], "estleg:paragrahv": "§ 4.",
             "estleg:provisionOmitted": True},
        ]
    }
    monkeypatch.setattr(fidelity, "iter_root_law_docs", lambda krr=None: iter([(tmp_path / "x_peep.json", doc)]))
    offenders = fidelity.measure_coverage(tmp_path)
    assert offenders["missing_legal_text"] == ["estleg:X_Par_1"]


def test_html_container_body_is_linearised() -> None:
    # Older ratification acts carry the § body as an HTMLKonteiner CDATA blob
    # (140 law §§ in the 2026-10-09 cache had no other text).
    par = _par(
        "<paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>"
        "<loige id=\"lg1\"><sisuTekst><HTMLKonteiner><![CDATA[Ratifitseerida juurdelisatud "
        "<a href=\"./78640\">kriminaalmenetluse &uuml;lev&otilde;tmise Euroopa konventsioon</a>,"
        "<table><tr><td>mis on</td><td>koostatud</td></tr></table> 1972.]]></HTMLKonteiner>"
        "</sisuTekst></loige>"
    )
    text = collect_full_text(par)
    # The lõige carries no loigeNr in these acts, so no "(1)" marker is prefixed.
    assert text.startswith("Ratifitseerida juurdelisatud kriminaalmenetluse ülevõtmise Euroopa konventsioon")
    assert "mis on koostatud 1972." in text
    assert "<" not in text
    assert html_container_text(None) == ""
