"""#409 — INDEX live Act/Law roots must have a known temporalStatus."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KRR = REPO / "krr_outputs"
INDEX = KRR / "INDEX.json"
KNOWN = {"inForce", "repealed", "notYetEffective"}


def _types(node: dict) -> list[str]:
    raw = node.get("@type") or []
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def _is_work_act(node: dict) -> bool:
    types = _types(node)
    if "estleg:Part" in types and "estleg:Act" not in types:
        return False
    return "estleg:Act" in types or "estleg:Law" in types


def test_index_live_acts_have_known_temporal_status() -> None:
    """Regression for #409 / #428 residual: no unknown/missing on live INDEX acts.

    Treaty ratification shells and sidecar-less live INDEX laws are classified
    ``inForce`` (current RT consolidations). Deprecated leftover peeps are
    excluded — they are not in ``INDEX.laws``.
    """
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    leftovers: list[str] = []
    for law in index["laws"]:
        for fname in law.get("files") or []:
            path = KRR / fname
            if not path.is_file():
                leftovers.append(f"missing-file:{fname}")
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            for node in doc.get("@graph") or []:
                if not isinstance(node, dict) or not _is_work_act(node):
                    continue
                if node.get("owl:deprecated") is True:
                    continue
                status = node.get("estleg:temporalStatus")
                if status not in KNOWN:
                    leftovers.append(f"{fname}:{node.get('@id')}:{status!r}")
    assert leftovers == [], (
        f"{len(leftovers)} live INDEX Act/Law nodes still unknown/missing "
        f"temporalStatus, e.g. {leftovers[:8]}"
    )
