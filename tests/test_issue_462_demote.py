"""#462 — formally demote similarity + sanction-severity JSON as non-graph."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "docs" / "SCHEMA_REFERENCE.md"
README = REPO / "README.md"

NAMED_JSON = (
    REPO / "krr_outputs" / "reports" / "similarity_index.json",
    REPO / "krr_outputs" / "reports" / "similarity_report.json",
    REPO / "krr_outputs" / "reports" / "sanctions_report.json",
)


def test_schema_reference_documents_non_graph_indexes() -> None:
    text = SCHEMA.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "non-graph" in lowered
    assert "similarity" in lowered
    assert "sanctions_report" in lowered or "severity" in lowered


def test_readme_mentions_json_indexes() -> None:
    text = README.read_text(encoding="utf-8")
    assert "similarity_index.json" in text or "similarity_report.json" in text
    assert "sanctions_report.json" in text


@pytest.mark.parametrize("path", NAMED_JSON, ids=lambda p: p.name)
def test_named_json_artifacts_exist(path: Path) -> None:
    if not path.is_file():
        pytest.skip(f"{path.relative_to(REPO)} not present")
    assert path.stat().st_size > 0
