"""Issue #404 — step-script guards against non-string @id / dates / sourceAct."""

from __future__ import annotations

import json
from pathlib import Path

from estleg.generate_amendment_history import _draft_publication_sort_key
from estleg.generate_similarity_index import extract_provisions_from_file
from estleg.generate_transposition_mapping import jsonld_text, normalize_text


def test_similarity_skips_non_string_node_id(tmp_path: Path):
    path = tmp_path / "act_peep.json"
    path.write_text(
        json.dumps(
            {
                "@graph": [
                    {
                        "@id": "estleg:Reg_1_Map_2026",
                        "@type": ["estleg:MunicipalRegulation", "estleg:Act"],
                    },
                    {"@id": ["not", "a", "string"], "estleg:summary": "x"},
                    {
                        "@id": "estleg:Reg_1_Par_1",
                        "@type": ["estleg:KovProvision"],
                        "estleg:summary": "kohustuslik säte asutusele",
                        "estleg:sourceAct": "Määrus",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    provisions, _act_type, _excluded = extract_provisions_from_file(path)
    assert all(isinstance(p["id"], str) for p in provisions)


def test_transposition_source_act_accepts_value_object():
    raw = {"@value": "Karistusseadustik", "@language": "et"}
    text = jsonld_text(raw)
    assert text == "Karistusseadustik"
    assert normalize_text(text) == "karistusseadustik"


def test_amendment_draft_sort_tolerates_non_string_publication_date():
    drafts = [
        {"publication_date": ["2020-01-01"]},
        {"publication_date": "2019-06-01"},
        {"publication_date": None},
    ]
    ordered = sorted(drafts, key=_draft_publication_sort_key)
    assert ordered[0]["publication_date"] == "2019-06-01"
