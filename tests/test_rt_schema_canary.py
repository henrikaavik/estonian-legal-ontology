"""#469: fail CI if the cached Riigi Teataja XML schema silently drifts.

The law generator keys off local-names (``oigusakt``, ``paragrahv``,
``paragrahvNr``, ``peatykk``). An RT format change that dropped those
tags would still parse and emit stubs. This canary loads the committed
KarS cache and asserts the structural contract ``generate_all_laws``
depends on.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from estleg import generate_all_laws as gal

REPO = Path(__file__).resolve().parent.parent

KARS_XML = REPO / "data" / "riigiteataja" / "karistusseadustik.xml"

_REQUIRED_LOCALNAMES = {
    "oigusakt",
    "metaandmed",
    "paragrahv",
    "paragrahvNr",
    "peatykk",
}


def _localnames(root: ET.Element) -> set[str]:
    names = {gal.ln(root.tag)}
    for el in root.iter():
        names.add(gal.ln(el.tag))
    return names


def test_committed_kars_xml_is_a_trustworthy_act_root() -> None:
    assert KARS_XML.is_file(), "KarS RT cache missing — cannot canary the schema"
    root = ET.fromstring(KARS_XML.read_bytes())
    assert gal._is_trustworthy_xml_root(root)
    assert gal.ln(root.tag) == "oigusakt"


def test_committed_kars_xml_still_has_generator_contract_tags() -> None:
    """#469: RT format drift that drops paragraph/chapter tags must fail CI."""
    root = ET.fromstring(KARS_XML.read_bytes())
    names = _localnames(root)
    missing = sorted(_REQUIRED_LOCALNAMES - names)
    assert missing == [], f"RT XML lost tags the law generator requires: {missing}"
    paragrahvid = [el for el in root.iter() if gal.ln(el.tag) == "paragrahv"]
    assert len(paragrahvid) >= 10
    numbered = [p for p in paragrahvid if gal.ct(p, "paragrahvNr")]
    assert numbered, "paragrahv elements no longer carry paragrahvNr"


def test_html_error_root_is_not_trusted() -> None:
    html = ET.fromstring("<html><body>error</body></html>")
    assert gal._is_trustworthy_xml_root(html) is False
