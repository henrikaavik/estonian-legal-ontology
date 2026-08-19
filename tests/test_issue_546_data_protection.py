"""#546 — court subcorpora carry a GDPR notice and machine-readable flags."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NOTICE = REPO / "docs" / "DATA_PROTECTION.md"
METADATA = REPO / "metadata.jsonld"


def test_data_protection_doc_exists_and_warns_controllers() -> None:
    assert NOTICE.is_file()
    text = NOTICE.read_text(encoding="utf-8")
    assert "GDPR" in text
    assert "independent" in text.casefold() and "controller" in text.casefold()
    assert "krr_outputs/riigikohus/" in text
    assert "krr_outputs/curia/" in text
    assert "krr_outputs/kohtud/" in text
    assert "Article 10" in text or "Art. 10" in text


def test_court_distributions_flag_personal_data() -> None:
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    flagged = []
    for dist in meta.get("dcat:distribution") or []:
        if not isinstance(dist, dict):
            continue
        if dist.get("estleg:containsPersonalData") is True:
            flagged.append(dist.get("dcterms:title"))
            rights = dist.get("dcterms:rights") or ""
            assert "PERSONAL DATA" in rights or "personal data" in rights.casefold()
    assert len(flagged) >= 2, flagged
    blob = " ".join(str(t) for t in flagged)
    assert "Riigikohus" in blob or "Supreme Court" in blob
    assert "EU court" in blob or "CURIA" in blob or "EU Court" in blob
