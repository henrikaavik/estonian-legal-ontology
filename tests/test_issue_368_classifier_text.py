"""Issue #368 — classifiers must read past the 500-char summary cap."""

from __future__ import annotations

from classify_eurovoc import extract_text_from_law
from estleg_common import classifier_text
from generate_regulations import provision_summary


def test_classifier_text_prefers_legal_text_over_truncated_summary():
    node = {
        "estleg:summary": "kohustatud arvut",
        "estleg:legalText": (
            "kohustatud arvutama tasumisele kuuluva aktsiisisumma"
        ),
    }
    assert classifier_text(node).endswith("aktsiisisumma")
    assert "arvutama" in classifier_text(node)


def test_classifier_text_falls_back_to_summary():
    node = {"estleg:summary": "ainult kokkuvõte"}
    assert classifier_text(node) == "ainult kokkuvõte"


def test_eurovoc_extract_includes_legal_text():
    blob = extract_text_from_law(
        {
            "@graph": [
                {
                    "estleg:summary": "lühike",
                    "estleg:legalText": "unikaalne_eurovoc_marker_xyz",
                }
            ]
        }
    )
    assert "unikaalne_eurovoc_marker_xyz" in blob


def test_regulation_summary_does_not_cut_mid_token():
    body = ("sõna " * 120) + "lõppsõna"
    out = provision_summary("§ 1.", "§ 1.", "Määrus", body)
    assert len(out) <= 500
    assert not out.endswith("sõn")
    assert "sõna" in out
