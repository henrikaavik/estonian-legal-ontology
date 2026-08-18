"""CI schedule trigger for committed-artifact drift (#479)."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VALIDATE_YML = REPO / ".github" / "workflows" / "validate.yml"


def test_validate_yml_declares_weekly_schedule() -> None:
    text = VALIDATE_YML.read_text(encoding="utf-8")
    assert re.search(r"(?m)^  schedule:\s*$", text), (
        "validate.yml must declare on.schedule for the weekly drift check (#479)"
    )
    cron = re.search(r"""cron:\s*['\"]([^'\"]+)['\"]""", text)
    assert cron is not None, "validate.yml schedule must include a cron string"
    fields = cron.group(1).split()
    assert len(fields) == 5, (
        f"GitHub cron must have 5 fields, got {cron.group(1)!r}"
    )
