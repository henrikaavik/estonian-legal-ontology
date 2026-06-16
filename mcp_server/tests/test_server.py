"""Tool-level smoke tests for the estleg_mcp server against the REAL corpus.

These import the MCP server (and therefore ``mcp``) and call the tool
functions directly -- the ``@mcp.tool()`` decorator leaves them callable. They
assert the behaviour a lawmaker-facing client sees, complementing the
MCP-free unit tests in ``test_data.py``.

If the corpus is absent the whole module skips, like ``test_data.py``.
"""

from __future__ import annotations

import pytest

from estleg_mcp import data, server

try:
    data.corpus_root()
    _CORPUS_AVAILABLE = True
except FileNotFoundError:
    _CORPUS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _CORPUS_AVAILABLE,
    reason="Estonian Legal Ontology corpus (krr_outputs/INDEX.json) not found",
)


def test_search_laws_surfaces_abbreviation_match() -> None:
    # The conventional abbreviation must produce the matching law, ranked first.
    hits = server.search_laws("VÕS")
    assert hits
    assert hits[0]["name"] == "volaoigusseadus"
    assert hits[0]["abbrev"] == "VÕS"


def test_drafts_affecting_law_follows_proposed_amendment() -> None:
    # keskkonnaseadustiku_uldosa_seadus has a draft link only via
    # estleg:hasProposedAmendment (no affectedBy); the tool must follow it
    # through the amendments sidecar to the real draft (regression for the
    # affectedBy-only collection that returned []).
    items = server.drafts_affecting_law("keskkonnaseadustiku_uldosa_seadus")
    assert items
    eis_numbers = {it.get("eis_number") for it in items}
    assert "KLIM/13-0996" in eis_numbers
    matched = next(it for it in items if it.get("eis_number") == "KLIM/13-0996")
    # A resolved draft carries a title and an EIS link, not the not-found note.
    assert matched["title"]
    assert matched["link"].startswith("https://eelnoud.valitsus.ee/")
    assert "note" not in matched
