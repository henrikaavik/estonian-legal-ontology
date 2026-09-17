"""#545 — MIT is software-only; the dataset is not licensed as MIT."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LICENSE = REPO / "LICENSE"
README = REPO / "README.md"
NOTICE = REPO / "NOTICE"
RIGHTS = REPO / "docs" / "DATA_RIGHTS.md"
METADATA = REPO / "metadata.jsonld"


def test_license_scope_note_excludes_krr_outputs() -> None:
    text = LICENSE.read_text(encoding="utf-8")
    note = text.split("----------------------------------------------------------------------", 1)[0]
    assert "krr_outputs" in note
    assert "does NOT license the DATA" in note or "does not license" in note.lower()


def test_metadata_dataset_is_not_mit() -> None:
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    rights = meta.get("dcterms:rights") or ""
    assert "NOT licensed as a whole under MIT" in rights
    license_id = (meta.get("dcterms:license") or {}).get("@id", "")
    assert "opensource.org/licenses/MIT" not in license_id
    assert "mit-license" not in license_id.lower()
    compilation = meta.get("dcterms:hasPart") or {}
    assert "creativecommons.org/licenses/by/4.0" in (
        (compilation.get("dcterms:license") or {}).get("@id") or ""
    )


def test_readme_and_notice_point_at_data_rights() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "DATA_RIGHTS.md" in readme
    assert "does **not** license the JSON-LD" in readme or "does not license" in readme.lower()
    notice = NOTICE.read_text(encoding="utf-8")
    assert "NOT covered by the" in notice
    assert "MIT License" in notice
    assert RIGHTS.is_file()
    assert "layered rights" in RIGHTS.read_text(encoding="utf-8").casefold()
