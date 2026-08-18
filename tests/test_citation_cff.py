"""Dataset citation surface (#547).

Guards ``CITATION.cff``, the README How to cite examples, and version
parity with ``metadata.jsonld``. A Zenodo DOI is #473 and is not minted here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CFF = REPO / "CITATION.cff"
README = REPO / "README.md"
METADATA = REPO / "metadata.jsonld"

# Top-level ``key: value`` lines only (CFF 1.2 is a small YAML subset).
_TOP_LEVEL = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$", re.MULTILINE)


def _cff_fields(text: str) -> dict[str, str]:
    """YAML-ish extract of top-level CFF keys (no PyYAML dependency)."""
    fields: dict[str, str] = {}
    for match in _TOP_LEVEL.finditer(text):
        fields[match.group(1)] = match.group(2).strip().strip("\"'")
    return fields


def test_citation_cff_exists_and_has_required_keys() -> None:
    assert CFF.is_file(), "CITATION.cff is the #547 cite record"
    text = CFF.read_text(encoding="utf-8")
    fields = _cff_fields(text)
    assert fields.get("cff-version"), "cff-version is required"
    assert fields.get("title"), "title is required"
    assert fields.get("type") == "dataset"
    assert fields.get("version"), "version is required"
    assert "authors" in fields, "authors is required"


def test_readme_how_to_cite_surface() -> None:
    text = README.read_text(encoding="utf-8")
    assert "How to cite" in text
    assert "CITATION.cff" in text
    assert "@misc" in text or "@dataset" in text


def test_metadata_version_matches_citation_cff() -> None:
    cff_version = _cff_fields(CFF.read_text(encoding="utf-8")).get("version")
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    assert meta.get("owl:versionInfo") == cff_version
