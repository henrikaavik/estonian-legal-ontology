import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_draft_impact import affected_law_name_values


def test_affected_law_name_values_accepts_canonical_string_array():
    assert affected_law_name_values(["Riigi Teataja seaduse"]) == ["Riigi Teataja seaduse"]


def test_affected_law_name_values_unwraps_legacy_jsonld_values():
    assert affected_law_name_values(
        [{"@value": "Riigi Teataja seaduse", "@language": "et"}]
    ) == ["Riigi Teataja seaduse"]
