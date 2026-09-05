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


def test_check_provision_detection_passes_on_the_real_corpus() -> None:
    # #678 boot guard: the committed corpus must satisfy it, so a healthy
    # server never trips the startup abort.
    server.check_provision_detection()


def test_check_provision_detection_aborts_when_provisions_are_dark(
    monkeypatch, capsys
) -> None:
    # A generator change that retypes § nodes loses no file and raises no
    # error, so the server must refuse to boot rather than serve confident
    # empty answers from every provision-backed tool.
    monkeypatch.delenv("ESTLEG_ALLOW_EMPTY_PROVISIONS", raising=False)
    monkeypatch.setattr(
        server.data,
        "provision_detection_check",
        lambda: {
            "law": "karistusseadustik",
            "abbrev": "KarS",
            "provisions": 0,
            "ok": False,
        },
    )
    with pytest.raises(SystemExit) as excinfo:
        server.check_provision_detection()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    # The message must name the symptom, the likely cause, and the opt-out.
    assert "KarS" in err
    assert "get_provision" in err
    assert "ESTLEG_CORPUS" in err
    assert "ESTLEG_ALLOW_EMPTY_PROVISIONS=1" in err


def test_check_provision_detection_opt_out_warns_and_continues(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("ESTLEG_ALLOW_EMPTY_PROVISIONS", "1")
    monkeypatch.setattr(
        server.data,
        "provision_detection_check",
        lambda: {
            "law": "karistusseadustik",
            "abbrev": "KarS",
            "provisions": 0,
            "ok": False,
        },
    )
    server.check_provision_detection()  # must not raise
    assert "starting anyway" in capsys.readouterr().err


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
