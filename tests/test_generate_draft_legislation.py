import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_draft_legislation import generate_draft_node


def test_generate_draft_node_emits_affected_law_names_as_strings():
    node = generate_draft_node(
        {
            "title": "Riigi Teataja seaduse muutmise seadus",
            "link": "https://eelnoud.valitsus.ee/main/mount/docList/12345678-1234-1234-1234-123456789abc?activity=2",
        },
        phase_id="Review",
        eis_number="JDM/26-0214",
        ministry_code="JDM",
        date_str="17.02.2026",
    )

    assert node["estleg:affectedLawName"]
    assert all(isinstance(value, str) for value in node["estleg:affectedLawName"])
