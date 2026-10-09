"""Human-readable section numbers retain the identity encoded by the IRI."""
from xml.etree import ElementTree as ET

import pytest

from estleg.law_structure import paragraph_display


@pytest.mark.parametrize("display", ["§ 1<sup>2</sup>. ", "<![CDATA[§ 1<sup>2</sup>. ]]>", "§ 1². "])
def test_section_display_preserves_superscript_and_trims_whitespace(display):
    section = ET.fromstring(f"<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>{display}</kuvatavNr></paragrahv>")
    assert paragraph_display(section) == "§ 1²."
