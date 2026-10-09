"""Text omissions exposed by the source XML golden fixtures."""

from xml.etree import ElementTree as ET

from estleg.law_structure import _loige_body_text


def test_reference_display_text_is_preserved_without_link_target():
    root = ET.fromstring('''<loige><sisuTekst>
        <tavatekst>mille kehtestab </tavatekst>
        <viide><kuvatavTekst>Vabariigi Valitsus</kuvatavTekst>
          <viideURID><viideURI>./dyn=123&amp;id=456</viideURI></viideURID>
        </viide><tavatekst>.</tavatekst></sisuTekst></loige>''')
    text = _loige_body_text(root)
    assert "Vabariigi Valitsus" in text
    assert "dyn=" not in text


def test_nested_text_fragments_are_not_duplicated():
    root = ET.fromstring('''<loige><lause>Vt <lauseOsa>seaduse
      <viide><kuvatavTekst>§ 22<sup>1</sup></kuvatavTekst>
        <viideURID><viideURI>./dyn=123</viideURI></viideURID></viide>
      nõudeid</lauseOsa>.</lause></loige>''')
    text = _loige_body_text(root)
    assert text.count("seaduse") == 1
    assert text.count("§ 22¹") == 1
    assert "dyn=" not in text
