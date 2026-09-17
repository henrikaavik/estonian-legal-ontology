"""Safe XML parse helper (issue #356).

``estleg_common.parse_xml`` must reject DTD/entity payloads and oversized
input instead of expanding them via ``xml.etree.ElementTree.fromstring``.
"""
from __future__ import annotations

import pytest

from estleg.estleg_common import parse_xml

BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
]>
<lolz>&lol;</lolz>
"""


def test_parse_xml_returns_root_element() -> None:
    root = parse_xml("<root>ok</root>")
    assert root.tag == "root"
    assert (root.text or "") == "ok"


def test_parse_xml_rejects_dtd_entity_payload() -> None:
    with pytest.raises(ValueError):
        parse_xml(BILLION_LAUGHS)


def test_parse_xml_rejects_oversize_payload() -> None:
    with pytest.raises(ValueError):
        parse_xml("xy", max_bytes=1)
