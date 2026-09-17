"""Schema-reference wording for instance labels (#382) and kehtiv (#432)."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "docs" / "SCHEMA_REFERENCE.md"


def _schema_text() -> str:
    return SCHEMA.read_text(encoding="utf-8")


def test_schema_reference_instance_labels_follow_current_language_policy() -> None:
    """#437/#509 supersede #382: plain legacy strings and new @et coexist."""
    text = _schema_text()
    match = re.search(r"`rdfs:label`:[^\n]*", text)
    assert match, "SCHEMA_REFERENCE.md must document rdfs:label"
    # Include the literal policy immediately following the property entries.
    window = text[match.start() : match.start() + 700]
    assert "xsd:string" in window and "rdf:langString" in window, window
    assert "new generators use `@et`" in window, window
    assert "English translations are not guaranteed" in window, window
    assert not re.search(
        r"language-tagged\s+(Estonian/?English|et/?en)",
        window,
        re.IGNORECASE,
    ), "instance rdfs:label must not be documented as language-tagged et/en"


def test_schema_reference_documents_kehtiv_as_snapshot_date() -> None:
    """#432: kehtiv is the snapshot/consolidation date, not temporalStatus."""
    text = _schema_text()
    idx = text.find("estleg:kehtiv")
    assert idx != -1, "SCHEMA_REFERENCE.md must document estleg:kehtiv"
    window = text[idx : idx + 500]
    assert re.search(r"snapshot|consolidation", window, re.IGNORECASE), window
    assert "#432" in window
