"""Schema-reference wording for instance labels (#382) and kehtiv (#432)."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "docs" / "SCHEMA_REFERENCE.md"


def _schema_text() -> str:
    return SCHEMA.read_text(encoding="utf-8")


def test_schema_reference_instance_labels_are_plain_estonian() -> None:
    """#382: do not document language-tagged et/en instance labels as current."""
    text = _schema_text()
    match = re.search(r"`rdfs:label`:[^\n]*", text)
    assert match, "SCHEMA_REFERENCE.md must document rdfs:label"
    # Include the following bilingual-is-future note when present.
    window = text[match.start() : match.start() + 700]
    assert "no language tag" in window or "Estonian plain string" in window, window
    assert not re.search(
        r"language-tagged\s+(Estonian/?English|et/?en)",
        window,
        re.I,
    ), "instance rdfs:label must not be documented as language-tagged et/en"


def test_schema_reference_documents_kehtiv_as_snapshot_date() -> None:
    """#432: kehtiv is the snapshot/consolidation date, not temporalStatus."""
    text = _schema_text()
    idx = text.find("estleg:kehtiv")
    assert idx != -1, "SCHEMA_REFERENCE.md must document estleg:kehtiv"
    window = text[idx : idx + 500]
    assert re.search(r"snapshot|consolidation", window, re.I), window
    assert "#432" in window
